"""인스타그램 대기열·시간표 (주인 결정 10/15).

- 텔레그램에 올린 딜은 인스타 대기열(DB — 재시작해도 남음)에 줄 서고, 시간표가 하루 3개까지·07~23시·2시간 간격·점수 순으로 올린다
- 올리기 직전에 품절·가격 오름을 다시 보고 건너뛴다 (사유 기록), 12시간 넘게 기다린 딜은 버린다
- 저녁 TOP5 캐러셀(공식 흐름: is_carousel_item 컨테이너 → CAROUSEL → 게시), 점심 릴스(가짜 ffmpeg, REELS → 처리 끝 → 게시),
  화·금 생필품 1개당 가격 캐러셀
- 올리기 전 검사: 본문 첫 줄 고지, 모든 장의 고지, 근거 없는 '최저가'·'역대'
진짜 인스타그램·텔레그램·상품 페이지에는 요청하지 않는다 (가짜 HTTP·가짜 ffmpeg).
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import random
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from PIL import Image

from dealbot.config import Settings
from dealbot.enrich import PageMeta
from dealbot.media import reel
from dealbot.media.card_slides import SlideSpec
from dealbot.models import Deal, DealVerdict, Product, PublishResult
from dealbot.page_price import PageOption, PagePrices
from dealbot.publisher import ig_scheduler, insta_style
from dealbot.publisher import instagram as ig_mod
from dealbot.publisher.instagram import InstagramClient, InstagramToken

ROOT = Path(__file__).resolve().parent.parent
KST = ZoneInfo("Asia/Seoul")
DAY = datetime(2026, 10, 13, tzinfo=KST)  # 화요일 (단가표 요일)


def at(h: int, m: int = 0, *, day: datetime = DAY) -> datetime:
    return day.replace(hour=h, minute=m).astimezone(UTC)


def _jpeg(color: tuple[int, int, int] = (90, 140, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (900, 900), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    """봇의 '지금' (핫딜 페이지 번호·대기열 넣은 시각)을 정해 둔다 — 테스트가 도는 실제 시각과 상관없이."""
    state = {"now": at(9)}

    def now() -> datetime:
        return state["now"]

    monkeypatch.setattr("dealbot.app.utcnow", now)
    monkeypatch.setattr("dealbot.publisher.ig_scheduler.utcnow", now)
    return state


@pytest.fixture
def bot(settings: Settings, clock: dict[str, datetime]):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    settings.secrets.public_domain = "bot.example.com"
    settings.threads.enabled = False
    settings.copy_cfg.enabled = False
    b = DealBot(settings)
    b.ig.rng = random.Random(7)
    assert b.instagram.dry_run  # 연습 모드: 인스타 게시기는 기록만
    yield b
    b.db.close()


def _plan(bot: Any, plan: dict[str, str], day: datetime = DAY) -> None:
    bot.db.kv_set(f"ig_plan:{day.date().isoformat()}", json.dumps(plan))


async def tick(bot: Any, clock: dict[str, datetime], when: datetime) -> str | None:
    clock["now"] = when
    return await bot.ig.tick(now=when)


def published(bot: Any, pid: str, name: str, price: int, *, when: datetime, score: float = 50, shop: str = "coupang",
              market: int | None = None, pct: float | None = None, avg: float | None = None, avg_pct: float | None = None,
              low: int | None = None, samples: int = 12, category: str = "") -> tuple[int, Deal]:
    """채널(텔레그램)에 올라간 딜 하나 (대기열 published + 발행 기록)."""
    deal = Deal(
        Product(source="goldbox" if shop == "coupang" else "ppomppu", product_id=pid, shop=shop, name=name, price=price,
                url=f"https://shop.example.com/{pid}", category=category, is_rocket=shop == "coupang", is_free_shipping=True),
        DealVerdict(is_deal=True, score=score, market_price=market, below_market_pct=pct, avg_price=avg, below_avg_pct=avg_pct,
                    low_price=low, history_days=30 if low else None, sample_count=samples),
        affiliate_url=f"https://link.example.com/{pid}", detected_at=when,
    )
    assert bot.db.enqueue(deal, score=score, now=when)
    qid = bot.db.items_for_product(pid, ("pending",))[0].id
    bot.db.update_queue_item(qid, status="published", deal=deal, now=when)
    bot.db.record_post(deal, channel_id="-100", message_id=qid, dry_run=bot.state.dry_run, now=when)
    return qid, deal


async def to_instagram(bot: Any, clock: dict[str, datetime], qid: int, deal: Deal, *, photo: bytes | None = None) -> None:
    clock["now"] = bot.db.get_queue_item(qid).created_at
    await bot._publish_side_channels(deal, photo=photo, deal_no=qid)


def singles(bot: Any) -> list[str]:
    return [it["product_id"] for p in bot.db.ig_posts_since(at(0) - timedelta(days=3), kind="single") for it in p["items"]]


def live(bot: Any, handler: Any, monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """연습 모드를 끄고 가짜 인스타 API 로 (요청은 기록)."""
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    monkeypatch.setattr(ig_mod, "CONTAINER_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ig_mod, "REEL_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ig_mod, "PUBLISH_ATTEMPT_DELAYS", (0.0,))
    bot.state.dry_run = False
    bot.instagram.dry_run = False
    bot.instagram.save_token(InstagramToken("T", "UID", expires_at=datetime(2099, 1, 1, tzinfo=UTC)))
    bot.instagram.client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(record)), retry_backoff=0.01)
    return seen


# ---------------------------------------------------------------- 대기열
async def test_published_deal_waits_in_the_instagram_queue_and_survives_a_restart(bot, clock, settings) -> None:  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    qid, deal = published(bot, "coupang:1", "도브 샴푸 680ml 2개", 15900, when=at(9))
    photo = _jpeg()
    await to_instagram(bot, clock, qid, deal, photo=photo)
    rows = bot.db.ig_queued()
    assert [(r["product_id"], r["queue_id"], r["status"]) for r in rows] == [("coupang:1", qid, "queued")]
    assert Path(rows[0]["photo_file"]).read_bytes() == photo  # 텔레그램 글에 쓴 그 사진의 사본
    assert bot.db.ig_last_post_at() is None  # 바로 올리지 않음
    await to_instagram(bot, clock, qid, deal, photo=photo)
    assert len(bot.db.ig_queued()) == 1  # 같은 상품은 한 번만 줄 섬
    again = DealBot(settings)  # 재시작
    try:
        assert [r["product_id"] for r in again.db.ig_queued()] == ["coupang:1"]
    finally:
        again.db.close()


async def test_singles_follow_hours_gap_daily_cap_and_score(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    for i, score in enumerate([10, 50, 30, 40, 20], 1):
        qid, deal = published(bot, f"coupang:{i}", f"상품 {i}호 500ml 2개", 9900, when=at(6, i), score=score)
        await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(6, 50)) is None  # 07시 전
    assert await tick(bot, clock, at(7, 0)) == "single: coupang:2"  # 점수 가장 높은 것
    assert await tick(bot, clock, at(8, 30)) is None  # 2시간 안
    assert await tick(bot, clock, at(9, 0)) == "single: coupang:4"
    assert await tick(bot, clock, at(11, 5)) == "single: coupang:3"
    assert await tick(bot, clock, at(13, 30)) is None  # 하루 3개
    assert singles(bot) == ["coupang:2", "coupang:4", "coupang:3"]
    assert await tick(bot, clock, at(23, 10)) is None  # 23시 뒤


async def test_singles_keep_clear_of_the_evening_carousel(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {"top5": "20:30"})
    for i in (1, 2):
        qid, deal = published(bot, f"coupang:{i}", f"상품 {i}호", 9900, when=at(17, i))
        await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(18, 40)) is None  # 캐러셀 2시간 앞
    assert await tick(bot, clock, at(18, 20)) == "single: coupang:1"
    # 캐러셀이 20:30 에 올라감 (딜이 모자라 건너뛴 게 아니라) → 그 뒤 2시간도 비움
    bot.db.kv_set(f"ig_done:top5:{DAY.date().isoformat()}", "posted 1 (3 deals)")
    bot.db.ig_record_post("top5", media_id="1", items=[], now=at(20, 30))
    assert await tick(bot, clock, at(22, 0)) is None
    assert await tick(bot, clock, at(22, 31)) == "single: coupang:2"


async def test_skipped_carousel_frees_the_evening_for_singles(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {"top5": "20:10"})
    qid, deal = published(bot, "coupang:1", "상품", 9900, when=at(19))  # 비교 근거 없음 → TOP5 후보 아님
    await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(19, 0)) is None  # 캐러셀 앞 2시간
    assert (await tick(bot, clock, at(20, 11)) or "").startswith("top5: skipped: 0 deal(s)")
    assert bot.db.kv_get(f"ig_done:top5:{DAY.date().isoformat()}").startswith("skipped")
    assert await tick(bot, clock, at(20, 12)) == "single: coupang:1"


async def test_queue_expires_after_twelve_hours_and_a_cut_off_post_is_not_retried(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "오래된 딜", 9900, when=at(6))
    await to_instagram(bot, clock, qid, deal)
    qid2, deal2 = published(bot, "coupang:2", "올리다 끊긴 딜", 9900, when=at(17))
    await to_instagram(bot, clock, qid2, deal2)
    item2 = bot.db.ig_queued()[-1]
    bot.db.ig_update(item2["id"], status="posting")  # 올리는 중에 재시작
    assert await tick(bot, clock, at(18, 30)) is None
    rows = {r["product_id"]: r for r in (bot.db.ig_item(i) for i in (1, 2))}
    assert (rows["coupang:1"]["status"], rows["coupang:1"]["reason"]) == ("expired", "waited too long")
    assert rows["coupang:2"]["status"] == "failed" and "maybe posted" in rows["coupang:2"]["reason"]
    assert singles(bot) == []


async def test_recheck_right_before_posting_skips_sold_out_and_price_rises(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    made: dict[str, int] = {}
    for pid, score, shop in (("coupang:A", 90, "coupang"), ("coupang:B", 80, "coupang"), ("gmarket:C", 70, "gmarket"),
                             ("gmarket:D", 60, "gmarket"), ("gmarket:E", 10, "gmarket")):
        qid, deal = published(bot, pid, f"상품 {pid}", 15900, when=at(8), score=score, shop=shop)
        await to_instagram(bot, clock, qid, deal)
        made[pid] = qid
    bot.db.kv_set("soldout_marked:coupang:A", (at(8, 30)).isoformat())  # 채널 글에 품절 표시가 붙음
    bot.db.record_observation(Product(source="goldbox", product_id="coupang:B", name="상품", price=19900,
                                      url="https://shop.example.com/coupang:B"), now=at(8, 40))  # 수집기가 오른 값을 봄
    pages = {
        "https://shop.example.com/gmarket:C": PageMeta(available=False),
        "https://shop.example.com/gmarket:D": PageMeta(available=True, prices=PagePrices(options=[PageOption(label=None, prices=[19900], lead=True)])),
        "https://shop.example.com/gmarket:E": PageMeta(available=True, prices=PagePrices(options=[PageOption(label=None, prices=[15900], lead=True)])),
    }
    read: list[str] = []
    sold: list[str] = []

    async def fetch(url: str) -> PageMeta | None:
        read.append(url)
        return pages.get(url)

    async def on_sold_out(product: Product, via: str) -> dict[str, Any]:
        sold.append(product.product_id)
        return {}

    bot.enricher.fetch = fetch
    bot._on_sold_out = on_sold_out
    assert await tick(bot, clock, at(9)) is None  # 한 번에 3개까지 확인: A·B·C 모두 건너뜀
    assert await tick(bot, clock, at(9, 1)) == "single: gmarket:E"  # D 도 건너뛰고 E
    reasons = {r["product_id"]: r["reason"] for r in (bot.db.ig_item(i) for i in range(1, 6))}
    assert reasons["coupang:A"] == "sold out (channel record)"
    assert reasons["coupang:B"] == "price up 15,900→19,900 (collected)"
    assert reasons["gmarket:C"] == "sold out (page)" and sold == ["gmarket:C"]  # 기존 품절 처리(채널 글 표시)도
    assert reasons["gmarket:D"] == "price up 15,900→19,900 (page)"
    assert "coupang" not in " ".join(read)  # 쿠팡 페이지는 안 읽는 몰 (설정 그대로)
    assert any("gmarket:D not posted: price up" in e["message"] for e in bot.db.recent_events(30))


async def test_failure_alerts_once_and_backs_off(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    for i in (1, 2):
        qid, deal = published(bot, f"coupang:{i}", f"상품 {i}", 9900, when=at(8, i))
        await to_instagram(bot, clock, qid, deal)
    alerts: list[str] = []

    async def failed(sns: str, cmd: str, tg_text: str, error: str) -> None:
        alerts.append(error)

    async def broken(deal: Deal, image_url: str | None, *, deal_no: int | None = None) -> PublishResult:
        return PublishResult(ok=False, error="instagram api 400 (publish): boom")

    bot._sns_failed = failed
    bot.instagram.publish = broken
    assert await tick(bot, clock, at(9)) == "single failed: coupang:1"
    assert await tick(bot, clock, at(9, 10)) is None  # 30분 쉼
    assert alerts == ["instagram api 400 (publish): boom"]
    assert bot.db.ig_item(1)["status"] == "failed" and bot.db.ig_item(2)["status"] == "queued"


async def test_live_single_post_sends_the_card_alt_text_and_first_line_disclosure(bot, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = live(bot, _carousel_handler("5150"), monkeypatch)
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "크리넥스 3겹 30m 30롤", 15900, when=at(8), market=22900, pct=30.6)
    await to_instagram(bot, clock, qid, deal, photo=_jpeg())
    assert await tick(bot, clock, at(9)) == "single: coupang:1"
    create = next(r for r in seen if r.url.path == "/v25.0/UID/media")
    p = create.url.params
    assert p["image_url"].startswith("https://bot.example.com/media/") and p["image_url"].endswith(".jpg")
    assert p["alt_text"].startswith("크리넥스 3겹 30m 30롤 가격 정보 카드 — 가격 15,900원, 1롤당 530원")
    lines = p["caption"].splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다"
    assert lines[1] == "크리넥스 3겹 30m 30롤 15,900원 · 쿠팡 최저가 22,900원"
    assert "1롤당 가격은 카드 표 둘째 줄에 있어요" in lines and "실시간 전체 딜은 텔레그램에 먼저 올라와요 (프로필 링크)" in lines
    assert "저장해" not in p["caption"] and "보내 주세요" not in p["caption"] and "http" not in p["caption"]
    assert bot.db.ig_item(1)["status"] == "posted" and bot.db.ig_item(1)["media_id"] == "5150"


async def test_no_public_domain_skips_quietly(bot, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    live(bot, _carousel_handler(), monkeypatch)
    bot.settings.secrets.public_domain = None
    alerts: list[str] = []

    async def failed(*a: Any) -> None:
        alerts.append(a[-1])

    bot._sns_failed = failed
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "상품", 9900, when=at(8))
    await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(9)) == "single skipped: coupang:1"
    assert alerts == [] and "공개 도메인" in bot.db.ig_item(1)["reason"]


# ---------------------------------------------------------------- 하루 시각표
def test_plan_picks_random_minutes_inside_the_windows_and_keeps_them(bot) -> None:  # type: ignore[no-untyped-def]
    for d in range(14):
        local = (DAY + timedelta(days=d)).replace(hour=8)
        plan = bot.ig.plan(local)
        assert "12:00" <= plan["reel"] < "13:00" and "20:00" <= plan["top5"] < "21:00"
        if local.weekday() in (1, 4):  # 화·금만 단가표
            assert "19:00" <= plan["unit"] < "20:00"
            m = lambda s: int(s[:2]) * 60 + int(s[3:])  # noqa: E731
            assert m(plan["top5"]) - m(plan["unit"]) >= 60  # TOP5 와 같은 시간대에 안 겹침
        else:
            assert "unit" not in plan
        assert bot.ig.plan(local.replace(hour=15)) == plan  # 재시작해도 같은 시각
    assert len({bot.ig.plan((DAY + timedelta(days=d)).replace(hour=8))["top5"] for d in range(14)}) > 3  # 날마다 다름


# ---------------------------------------------------------------- 저녁 TOP5 캐러셀
def _carousel_handler(published_id: str = "9001") -> Any:
    count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v25.0/UID/media":
            count["n"] += 1
            return httpx.Response(200, json={"id": f"C{count['n']}"})
        if req.url.path == "/v25.0/UID/media_publish":
            return httpx.Response(200, json={"id": published_id})
        return httpx.Response(200, json={"status_code": "FINISHED"})

    return handler


async def test_evening_top5_uses_the_official_carousel_flow(bot, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = live(bot, _carousel_handler(), monkeypatch)
    _plan(bot, {"top5": "20:15"})
    q1, _ = published(bot, "coupang:1", "크리넥스 3겹 30m 30롤", 15900, when=at(9), market=22900, pct=30.6)
    q2, _ = published(bot, "naver:2", "삼다수 2L 12병", 9980, when=at(10), shop="naver", avg=12800, avg_pct=22, low=10500)
    q3, _ = published(bot, "coupang:3", "도브 샴푸 680ml 2개", 15900, when=at(11), market=19900, pct=20.1)
    published(bot, "coupang:4", "비교 근거 없는 딜", 9900, when=at(12))  # 빠짐
    published(bot, "coupang:5", "품절된 딜", 9900, when=at(13), market=19900, pct=50)
    bot.db.kv_set("soldout_marked:coupang:5", at(14).isoformat())  # 빠짐
    published(bot, "coupang:6", "어제 딜", 9900, when=at(22, day=DAY - timedelta(days=1)), market=19900, pct=50)  # 어제 → 빠짐
    assert await tick(bot, clock, at(20, 14)) is None  # 아직
    done = await tick(bot, clock, at(20, 16))
    assert done == "top5: posted 9001 (3 deals)"
    creates = [r for r in seen if r.url.path == "/v25.0/UID/media"]
    items, parent = creates[:-1], creates[-1]
    assert len(items) == 5  # 표지 + 딜 3 + 마지막 장
    for r in items:
        p = r.url.params
        assert p["is_carousel_item"] == "true" and p["alt_text"] and p["image_url"].startswith("https://bot.example.com/media/ig_top5_")
        assert "caption" not in p
    assert parent.url.params["media_type"] == "CAROUSEL" and parent.url.params["children"] == "C1,C2,C3,C4,C5"
    caption = parent.url.params["caption"]
    lines = caption.splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스·네이버 쇼핑 커넥트 활동으로 수수료를 받습니다"
    assert f"#{q1} 크리넥스 3겹 30m 30롤 15,900원" in lines and f"#{q2} 삼다수 2L 12병 9,980원" in lines
    assert lines.index(f"#{q1} 크리넥스 3겹 30m 30롤 15,900원") < lines.index(f"#{q3} 도브 샴푸 680ml 2개 15,900원")  # 내려간 폭 순
    assert "품절된 딜" not in caption and "비교 근거 없는 딜" not in caption and "어제 딜" not in caption
    assert "이 포스팅은 쿠팡 파트너스 활동의 일환으로" in caption and "네이버 쇼핑 커넥트 활동의 일환으로" in caption
    publish = [r for r in seen if r.url.path.endswith("/media_publish")]
    assert [r.url.params["creation_id"] for r in publish] == ["C6"]
    assert [p["kind"] for p in bot.db.ig_posts_since(at(0))] == ["top5"]
    # 장 그림은 공개 폴더에서 서빙 (메타가 받아 감)
    name = items[0].url.params["image_url"].rsplit("/", 1)[-1]
    served = await bot.web.dispatch("GET", f"/media/{name}")
    assert served[0] == 200 and served[2] == "image/jpeg"  # type: ignore[misc]
    with Image.open(bot.settings.data_dir / "media" / name) as im:
        assert im.size == (1080, 1350)
    assert await tick(bot, clock, at(20, 40)) is None  # 하루 한 번


async def test_top5_slides_carry_the_disclosure_and_data_only(bot) -> None:  # type: ignore[no-untyped-def]
    picks = []
    for no, (pid, name, price, market) in enumerate((("coupang:1", "휴지 30롤", 15900, 22900), ("coupang:2", "샴푸 2개", 9900, 14900)), 12):
        d = Deal(Product(source="goldbox", product_id=pid, shop="coupang", name=name, price=price, url="https://x"),
                 DealVerdict(is_deal=True, market_price=market, below_market_pct=round((1 - price / market) * 100, 1)), detected_at=at(9))
        info = insta_style.card_info(d, bot.renderer, shop=bot.registry.get("coupang"), deal_no=no)
        picks.append(insta_style.Pick(deal=d, info=info, deal_no=no))
    specs = bot.ig.top5_specs(picks, DAY.replace(hour=20))
    assert [s.kind for s in specs] == ["cover", "deal", "deal", "end"]
    assert specs[0].ad == "광고 · 제휴 링크로 수수료를 받아요" and specs[-1].title == "실시간 전체 딜은\n텔레그램에 먼저"
    assert specs[-1].big == "프로필 링크"
    assert all(s.ad.startswith("광고 · ") for s in specs)
    assert specs[1].rows[0] == ("가격", "15,900원") and ("비교", "쿠팡 최저가 22,900원 (31%↓)") in specs[1].rows
    caption = insta_style.top5_caption(picks, bot.registry, DAY)
    assert caption.splitlines()[:2] == ["광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다", "10월 13일 (화) 가격 내려간 딜 TOP2"]
    assert not insta_style.validate(caption, slides=specs, allowed=["쿠팡 최저가"])
    assert insta_style.validate(caption, slides=specs)  # '쿠팡 최저가' 를 데이터 근거로 넘기지 않으면 실패


# ---------------------------------------------------------------- 점심 릴스 (가짜 ffmpeg)
def _fake_ffmpeg(tmp: Path) -> Path:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "ffmpeg"
    tool.write_text('#!/usr/bin/env bash\necho "$@" > "$(dirname "$0")/args.txt"\nfor last; do :; done\nprintf fakevideo > "$last"\n',
                    encoding="utf-8")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    return bin_dir


async def test_lunch_reel_builds_a_video_and_uses_the_reels_flow(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    bin_dir = _fake_ffmpeg(tmp_path)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    status = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v25.0/UID/media":
            return httpx.Response(200, json={"id": "R1"})
        if req.url.path == "/v25.0/R1":
            status["n"] += 1
            return httpx.Response(200, json={"status_code": "IN_PROGRESS" if status["n"] < 3 else "FINISHED"})
        if req.url.path == "/v25.0/UID/media_publish":
            return httpx.Response(200, json={"id": "777"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    seen = live(bot, handler, monkeypatch)
    _plan(bot, {"reel": "12:20"})
    q1, _ = published(bot, "coupang:1", "크리넥스 3겹 30m 30롤", 15900, when=at(8), score=90, market=22900, pct=30.6)
    published(bot, "coupang:2", "도브 샴푸 680ml 2개", 15900, when=at(9), score=80)
    published(bot, "naver:3", "삼다수 2L 12병", 9980, when=at(10), score=70, shop="naver")
    published(bot, "coupang:4", "점수 낮은 딜", 9900, when=at(11), score=5)
    published(bot, "coupang:5", "점심 뒤 딜", 9900, when=at(12, 30), score=99)  # 릴스 시각 뒤 → 빠짐
    done = await tick(bot, clock, at(12, 21))
    assert done == "reel: posted 777 (3 deals)"
    create = next(r for r in seen if r.url.path == "/v25.0/UID/media")
    p = create.url.params
    assert p["media_type"] == "REELS" and p["share_to_feed"] == "true"
    assert p["video_url"].startswith("https://bot.example.com/media/reel_") and p["video_url"].endswith(".mp4")
    assert p["cover_url"].endswith("_cover.jpg")
    lines = p["caption"].splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스·네이버 쇼핑 커넥트 활동으로 수수료를 받습니다" and lines[1].startswith("점심 TOP3")
    assert f"#{q1} 크리넥스 3겹 30m 30롤 15,900원" in lines and "점수 낮은 딜" not in p["caption"] and "점심 뒤 딜" not in p["caption"]
    assert status["n"] >= 3 and [r.url.params["creation_id"] for r in seen if r.url.path.endswith("/media_publish")] == ["R1"]
    args = (bin_dir / "args.txt").read_text(encoding="utf-8")
    assert args.count("-loop 1") == 5  # 첫 장(가격이 내려간 폭) + 딜 3 + 마지막 장
    for part in ("libx264", "yuv420p", "scale=1080:1920", "anullsrc", "aac", "+faststart"):
        assert part in args
    video = p["video_url"].rsplit("/", 1)[-1]
    served = await bot.web.dispatch("GET", f"/media/{video}")
    assert served[0] == 200 and served[2] == "video/mp4"  # type: ignore[misc]
    assert [x["kind"] for x in bot.db.ig_posts_since(at(0))] == ["reel"]


async def test_reel_processing_timeout_gives_up_quietly(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATH", f"{_fake_ffmpeg(tmp_path)}{os.pathsep}{os.environ.get('PATH', '')}")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v25.0/UID/media":
            return httpx.Response(200, json={"id": "R1"})
        return httpx.Response(200, json={"status_code": "IN_PROGRESS"})

    seen = live(bot, handler, monkeypatch)
    bot.settings.instagram.reels.processing_timeout_seconds = 0.05
    alerts: list[str] = []

    async def failed(*a: Any) -> None:
        alerts.append(a[-1])

    bot._sns_failed = failed
    _plan(bot, {"reel": "12:20"})
    for i in (1, 2):
        published(bot, f"coupang:{i}", f"상품 {i} 500ml 2개", 9900, when=at(9, i))
    done = await tick(bot, clock, at(12, 25))
    assert done is not None and done.startswith("reel: failed: timeout")
    assert not any(r.url.path.endswith("/media_publish") for r in seen) and alerts == []  # 게시 안 함, 알림 없음
    assert bot.db.ig_last_post_at() is None


async def test_reel_without_ffmpeg_skips_and_asks_the_owner_once(bot, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(reel, "find_ffmpeg", lambda: None)
    todo: list[tuple[str, str]] = []

    async def alert(kind: str, tg_text: str | None, kakao_text: str, **kw: Any) -> int | None:
        todo.append((kind, kakao_text))
        return None

    bot.notifier.alert = alert
    for d in (0, 1):
        day = DAY + timedelta(days=d)
        _plan(bot, {"reel": "12:10"}, day)
        for i in (1, 2):
            published(bot, f"coupang:{d}{i}", f"상품 {d}{i}", 9900, when=at(9, i, day=day))
        assert await tick(bot, clock, at(12, 11, day=day)) == "reel: skipped: ffmpeg not installed"
    assert len(todo) == 1 and "ffmpeg" in todo[0][1] and "apt-get install -y ffmpeg" in todo[0][1]
    # 시작 자기 점검에도 한 줄 (인스타가 연결돼 있을 때)
    bot.settings.secrets.instagram_app_id, bot.settings.secrets.instagram_app_secret = "IGAPP", "IGSECRET"
    bot.instagram.save_token(InstagramToken("T", "UID"))
    lines = [t for _ok, t in await bot.self_check()]
    assert any(t.startswith("인스타 점심 릴스: 서버에 ffmpeg 없음") for t in lines)


def test_reel_timing_and_command() -> None:
    for n in (2, 3, 4):
        lengths = reel.durations(n)
        assert 10.0 <= sum(lengths) <= 15.0 and lengths[0] <= 1.5 and len(lengths) == n + 2
    cmd = reel.command("ffmpeg", [(Path("a.png"), 1.5), (Path("b.png"), 3.8)], Path("out.mp4"))
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4" and "-c:v libx264" in joined and "-pix_fmt yuv420p" in joined
    assert "concat=n=2:v=1:a=0" in joined and "-c:a aac" in joined and "-r 30" in joined


def test_setup_script_installs_ffmpeg() -> None:
    setup = (ROOT / "deploy" / "lightsail" / "setup.sh").read_text(encoding="utf-8")
    line = next(ln for ln in setup.splitlines() if "apt-get install -y" in ln and "fonts-nanum" in ln)
    assert line.count("ffmpeg") == 2  # 두 갈래 모두


# ---------------------------------------------------------------- 화·금 생필품 1개당 가격
async def test_weekly_unit_price_carousel_uses_only_our_recorded_prices(bot, clock, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen = live(bot, _carousel_handler("4242"), monkeypatch)
    _plan(bot, {"unit": "19:05", "top5": "20:40"})

    def history(pid: str, name: str, price: int, n: int, when: datetime) -> None:
        for k in range(n):
            bot.db.record_observation(Product(source="goldbox", product_id=pid, name=name, price=price + 100 * k,
                                              url="https://x"), now=when - timedelta(days=k + 1))

    week = [
        ("coupang:t1", "크리넥스 3겹 30m 30롤", 15900, 3),  # 휴지 1롤당 530
        ("coupang:w1", "베베숲 물티슈 100매 10팩", 13900, 3),  # 물티슈 1매당 14
        ("coupang:s1", "삼다수 2L 12병", 9980, 4),  # 생수 100mL당 42
        ("coupang:s2", "아이시스 2L 12병", 8980, 1),  # 기록 1번 → 빠짐
        ("coupang:x1", "도브 샴푸 680ml 2개", 15900, 5),  # 생필품 아님 → 빠짐
    ]
    for i, (pid, name, price, n) in enumerate(week):
        when = at(9, i, day=DAY - timedelta(days=1))
        history(pid, name, price, n, when)
        published(bot, pid, name, price, when=when)
    old = at(9, day=DAY - timedelta(days=12))  # 지난달 딜: 기준선에만
    history("coupang:t0", "깨끗한나라 화장지 27m 30롤", 12900, 3, old)
    published(bot, "coupang:t0", "깨끗한나라 화장지 27m 30롤", 12900, when=old)
    done = await tick(bot, clock, at(19, 6))
    assert done == "unit: posted 4242 (3 deals)"
    creates = [r for r in seen if r.url.path == "/v25.0/UID/media"]
    assert len(creates) == 5  # 표지 + 휴지·물티슈 + 생수 + 기준선 (+ 캐러셀)
    caption = creates[-1].url.params["caption"]
    lines = caption.splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다" and lines[1].startswith("이번 주 생필품 1개당 가격")
    assert any(ln.endswith("크리넥스 3겹 30m 30롤 15,900원 → 휴지 1롤당 530원") for ln in lines)
    assert any(ln.endswith("삼다수 2L 12병 9,980원 → 생수 100mL당 42원") for ln in lines)
    assert "아이시스" not in caption and "샴푸" not in caption and "깨끗한나라" not in caption  # 이번 주 목록엔 없음
    assert "· 휴지 1롤당 480원 (딜 2건)" in lines  # 기준선: 530·430 의 가운데 값 (우리가 기록한 딜만)
    assert "최저가" not in caption and "역대" not in caption
    assert bot.db.kv_get(f"ig_done:unit:{DAY.date().isoformat()}").startswith("posted")


async def test_unit_carousel_skips_without_enough_categories(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {"unit": "19:05"})
    for k in range(3):
        bot.db.record_observation(Product(source="goldbox", product_id="coupang:t1", name="휴지", price=15900, url="https://x"),
                                  now=at(8) - timedelta(days=k + 1))
    published(bot, "coupang:t1", "크리넥스 3겹 30m 30롤", 15900, when=at(8))
    assert await tick(bot, clock, at(19, 10)) == "unit: skipped: 1 category slide(s) with our price data (need 2)"


def test_unit_kinds_read_units_from_names() -> None:
    from dealbot.utils.text import unit_price

    def kind(name: str, price: int) -> tuple[str, str, int] | None:
        got = insta_style.unit_kind(name, unit_price(name, price))
        return (got[0], got[1].label, got[2]) if got else None

    assert kind("크리넥스 3겹 30m 30롤", 15900) == ("휴지·물티슈", "1롤당", 530)
    assert kind("베베숲 물티슈 100매 10팩", 13900) == ("휴지·물티슈", "1매당", 14)
    assert kind("삼다수 2L 12병", 9980) == ("생수", "100mL당", 42)
    assert kind("퍼실 딥클린 액체세제 2.7L 2개", 19800) == ("세제", "100mL당", 367)
    assert kind("피지 캡슐세제 30개입", 12900) == ("세제", "1회분(1개)당", 430)
    assert kind("하기스 네이처메이드 기저귀 4단계 62매", 25900) == ("기저귀", "1매당", 418)
    assert kind("도브 샴푸 680ml 2개", 15900) is None
    assert kind("코디 키친타월 3겹 150매 6롤", 9900) is None  # 키친타월은 휴지가 아님
    assert insta_style.baseline([530]) is None and insta_style.baseline([530, 430, 610]) == 530


# ---------------------------------------------------------------- 올리기 전 검사
def test_validator_rules() -> None:
    ok = "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다\n휴지 15,900원 · 쿠팡 최저가 22,900원\n#핫딜"
    assert insta_style.validate(ok, allowed=["쿠팡 최저가"]) == []
    late = "휴지 15,900원\n\n이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
    assert any("첫 줄" in p for p in insta_style.validate(late))  # 끝에만 있는 고지는 인정 안 됨
    assert any("최저가" in p for p in insta_style.validate(ok))  # 데이터 근거 없이 '최저가'
    assert any("역대" in p for p in insta_style.validate(ok.replace("휴지", "역대 휴지"), allowed=["쿠팡 최저가"]))
    assert any("링크 주소" in p for p in insta_style.validate(ok + "\nhttps://t.me/x", allowed=["쿠팡 최저가"]))
    good = SlideSpec(kind="deal", ad="광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다", title="휴지")
    bare = SlideSpec(kind="end", ad="", title="실시간 전체 딜은 텔레그램")
    problems = insta_style.validate(ok, slides=[good, bare], allowed=["쿠팡 최저가"])
    assert problems == ["2번째 장에 광고·수수료 고지가 없음"]
    hype = SlideSpec(kind="deal", ad="광고 · 제휴 링크로 수수료를 받아요", rows=[("기록", "30일 최저가")])
    assert insta_style.validate(ok, slides=[hype], allowed=["쿠팡 최저가"]) == ["1번째 장: '최저가' — 데이터로 확인된 비교가 아님"]


def test_ad_line_and_low_text(bot) -> None:  # type: ignore[no-untyped-def]
    reg = bot.registry
    assert insta_style.ad_line([reg.get("coupang").disclosure]) == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다"
    both = insta_style.ad_line([reg.get("coupang").disclosure, reg.get("naver").disclosure, reg.get("coupang").disclosure])
    assert both == "광고 · 쿠팡 파트너스·네이버 쇼핑 커넥트 활동으로 수수료를 받습니다"
    assert insta_style.ad_line([None]) == "광고 · 제휴 링크로 수수료를 받아요"  # 몰 고지가 없는 딜도 인스타엔 고지
    d = Deal(Product(source="goldbox", product_id="coupang:1", shop="coupang", name="휴지", price=9000, url="https://x"),
             DealVerdict(is_deal=True, low_price=10000, avg_price=11000, history_days=30, sample_count=10),
             detected_at=datetime(2026, 10, 15, 0, 5, tzinfo=UTC))
    assert insta_style.low_text(d, bot.renderer, 30) == "우리가 본 최근 30일 중 가장 낮음 (10/15 09시 기준)"
    d.detected_at = None
    assert insta_style.low_text(d, bot.renderer, 30) is None  # 기준 시각을 모르면 안 씀
    assert insta_style.ig_name("[역대최저가] 휴지 30롤") == "휴지 30롤"


# ---------------------------------------------------------------- 정리·루프
def test_prune_drops_old_instagram_rows(bot) -> None:  # type: ignore[no-untyped-def]
    deal = Deal(Product(source="goldbox", product_id="coupang:1", shop="coupang", name="x", price=1000, url="https://x"),
                DealVerdict(is_deal=True))
    old = datetime.now(UTC) - timedelta(days=40)
    item = bot.db.ig_enqueue(deal, queue_id=1, score=1, now=old)
    bot.db.ig_update(item, status="posted", now=old)
    bot.db.ig_record_post("single", media_id="1", items=[], now=old)
    res = bot.db.prune(price_history_days=180, events_days=30)
    assert res["ig_queue"] == 1 and res["ig_posts"] == 1


async def test_instagram_loop_is_a_noop_without_a_scheduler() -> None:
    from types import SimpleNamespace

    from dealbot import scheduler as sched

    stop = asyncio.Event()
    await asyncio.wait_for(sched.instagram_loop(SimpleNamespace(), stop), 1)  # type: ignore[arg-type]
    off = SimpleNamespace(ig=object(), settings=SimpleNamespace(instagram=SimpleNamespace(enabled=False)))
    await asyncio.wait_for(sched.instagram_loop(off, stop), 1)  # type: ignore[arg-type]


async def test_tick_does_nothing_when_instagram_is_not_connected(bot, clock) -> None:  # type: ignore[no-untyped-def]
    bot.instagram.dry_run = False  # 실제 모드인데 연결 안 됨
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "상품", 9900, when=at(8))
    await to_instagram(bot, clock, qid, deal)
    assert bot.db.ig_queued() == []  # 연결이 안 됐으면 줄도 안 섬
    assert await tick(bot, clock, at(9)) is None


def test_scheduler_module_constants() -> None:
    assert ig_scheduler.RECHECK_TRIES >= 1 and ig_scheduler.SCHEDULED_GAP_MINUTES == 60
