"""인스타그램 대기열·시간표 (주인 결정 10/15).

- 텔레그램에 올린 딜은 인스타 대기열(DB — 재시작해도 남음)에 줄 서고, 시간표가 하루 10개까지·07~23시·30~50분 간격·점수 순으로 올린다 (주인 10/18 — 할인 괜찮은 딜만)
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
from dealbot.growth import blog_share
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
    """봇의 '지금' (링크 페이지 번호·대기열 넣은 시각)을 정해 둔다 — 테스트가 도는 실제 시각과 상관없이."""
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
    # 흐름 테스트: 모든 딜을 '할인 괜찮은 딜'로 (주인 10/18 — 고르는 기준·보통 딜은 test_ig_good_deals.py 가 30% 로 본다)
    settings.publish.good_deal_min_discount_pct = 0
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
    """주인 10/18: 딜 하나짜리 글 하루 10개(안전 상한)·글 사이 30~50분 무작위·07~23시·점수 순."""
    _plan(bot, {})
    scores = [10, 50, 30, 40, 20, 15, 25, 35, 45, 55, 5, 60]
    for i, score in enumerate(scores, 1):
        qid, deal = published(bot, f"coupang:{i}", f"상품 {i}호 500ml 2개", 9900, when=at(6, i), score=score)
        await to_instagram(bot, clock, qid, deal)
    order = [f"coupang:{i}" for _, i in sorted(((sc, i) for i, sc in enumerate(scores, 1)), reverse=True)]
    assert await tick(bot, clock, at(6, 50)) is None  # 07시 전
    assert await tick(bot, clock, at(7, 0)) == f"single: {order[0]}"  # 점수 가장 높은 것
    assert await tick(bot, clock, at(7, 29)) is None  # 30분 안
    when = at(7, 0)
    for k in range(1, 10):
        when += timedelta(minutes=50)  # 30~50분 무작위 간격의 끝 — 늘 지남
        assert await tick(bot, clock, when) == f"single: {order[k]}", k
    assert await tick(bot, clock, when + timedelta(minutes=50)) is None  # 하루 10개 (안전 상한)
    assert singles(bot) == order[:10]
    assert await tick(bot, clock, at(23, 10)) is None  # 23시 뒤


def test_single_gap_is_a_random_30_to_50_minutes_kept_per_post(bot) -> None:  # type: ignore[no-untyped-def]
    gaps = {bot.ig.gap_after(at(9) + timedelta(minutes=i)) for i in range(200)}
    assert min(gaps) >= timedelta(minutes=30) and max(gaps) <= timedelta(minutes=50) and len(gaps) > 5
    assert bot.ig.gap_after(at(9)) == bot.ig.gap_after(at(9))  # 1분마다 볼 때(재시작해도) 같은 간격


async def test_singles_keep_clear_of_the_evening_carousel(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """주인 10/18: 릴스·TOP5·단가표 시각 앞 30분만 비워 둔다 (예전 2시간)."""
    _plan(bot, {"top5": "20:30"})
    for i in (1, 2):
        qid, deal = published(bot, f"coupang:{i}", f"상품 {i}호", 9900, when=at(17, i))
        await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(20, 1)) is None  # 캐러셀 30분 앞
    assert await tick(bot, clock, at(19, 59)) == "single: coupang:1"
    # 캐러셀이 20:30 에 올라감 (딜이 모자라 건너뛴 게 아니라) → 그 뒤도 글 사이 간격(30~50분)
    bot.db.kv_set(f"ig_done:top5:{DAY.date().isoformat()}", "posted 1 (3 deals)")
    bot.db.ig_record_post("top5", media_id="1", items=[], now=at(20, 30))
    assert await tick(bot, clock, at(20, 59)) is None
    assert await tick(bot, clock, at(21, 21)) == "single: coupang:2"


async def test_skipped_carousel_frees_the_evening_for_singles(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {"top5": "20:10"})
    qid, deal = published(bot, "coupang:1", "상품", 9900, when=at(19))  # 비교 근거 없음 → TOP5 후보 아님
    await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(19, 45)) is None  # 캐러셀 앞 30분
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
    # 바뀜 (IG-5): 올리다 끊긴 글은 인스타에 올라갔을 수도 있어 하루 개수·간격에 센다 ('maybe' 기록 — 예전엔 아무것도 안 남겼음)
    assert singles(bot) == ["coupang:2"]
    assert bot.db.ig_posts_since(at(0), kind="single")[0]["items"][0]["maybe"] is True


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
    # 종이 카드(P, 주인 결정 10/16)엔 표가 없음 — 예전 B 의 '카드 표 둘째 줄에 있어요' 대신
    assert "1롤당 가격도 카드에 적어 두었어요" in lines and "실시간 전체 딜은 텔레그램에 먼저 올라와요 (프로필 링크)" in lines
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


def _reel_pick(bot: Any, pid: str, name: str, price: int, *, market: int | None = None, no: int = 1) -> insta_style.Pick:
    """점심 릴스 후보 하나 (쿠팡 딜, 네이버 최저가 대조). own_before 로 우리 가격 기록의 바로 앞 가격을 붙인다."""
    d = Deal(Product(source="goldbox", product_id=pid, shop="coupang", name=name, price=price, url="https://x"),
             DealVerdict(is_deal=True, market_price=market, market_source="naver" if market else None,
                         below_market_pct=round((1 - price / market) * 100, 1) if market else None), detected_at=at(9))
    info = bot.ig.own_before(d, insta_style.card_info(d, bot.renderer, shop=bot.registry.get("coupang"), deal_no=no))
    return insta_style.Pick(deal=d, info=info, deal_no=no)


def _observe(bot: Any, pid: str, price: int, when: datetime) -> None:
    bot.db.record_observation(Product(source="goldbox", product_id=pid, name="x", price=price, url="https://x"), now=when)


def test_reel_hook_strikes_only_our_own_earlier_price(bot) -> None:  # type: ignore[no-untyped-def]
    """표시광고법: 첫 장 '19,900원 → 15,900원' 취소선은 19,900원이 이 상품의 바로 앞 가격(우리 가격 기록)일 때만, 근거 '우리가 본 10/08 가격'
    과 함께. 네이버 최저가(다른 판매처일 수 있음)만 있으면 지금 가격을 크게 + 따로 '네이버 최저가 19,900원보다 4,000원 ↓' (취소선 없음)."""
    local = DAY.replace(hour=12, minute=20)
    market_only = _reel_pick(bot, "coupang:m", "크리넥스 3겹 30m 30롤", 15900, market=19900)
    hook = bot.ig.reel_specs([market_only], local)[0]
    assert hook.kind == "reel_hook" and hook.big == "15,900원"
    assert hook.was == "" and hook.was_note == ""  # 시중가는 취소선으로 안 그림
    assert hook.big_note == "네이버 최저가 19,900원보다 4,000원 ↓"
    assert not insta_style.validate("광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다", slides=[hook], allowed=["네이버 최저가"])

    # 이 상품을 10/08 에 19,900원으로 봤고(우리 기록), 그 뒤 딜을 본 때(09시) 15,900원 — 그 관측은 '바로 앞 가격'이 아님
    _observe(bot, "coupang:o", 21900, at(9, day=DAY - timedelta(days=9)))
    _observe(bot, "coupang:o", 19900, at(9, day=DAY - timedelta(days=5)))
    _observe(bot, "coupang:o", 15900, at(9))
    own = _reel_pick(bot, "coupang:o", "도브 샴푸 680ml 2개", 15900, no=2)
    assert (own.info.own_was, own.info.own_was_label) == (19900, "우리가 본 10/08 가격")
    hook = bot.ig.reel_specs([market_only, own], local)[0]  # 우리 기록이 있는 딜이 첫 장으로
    assert hook.title == "도브 샴푸 680ml 2개" and hook.was == "19,900원" and hook.was_note == "우리가 본 10/08 가격"
    assert hook.big == "15,900원" and hook.big_note == "4,000원 내려갔어요 (20%↓)"

    # 바로 앞 가격이 이미 지금 값이면(어제부터 15,900원) 취소선 없음 — 그보다 앞의 비쌌던 때를 골라 쓰지 않는다
    _observe(bot, "coupang:s", 19900, at(9, day=DAY - timedelta(days=5)))
    _observe(bot, "coupang:s", 15900, at(9, day=DAY - timedelta(days=1)))
    same = _reel_pick(bot, "coupang:s", "삼다수 2L 12병", 15900, market=19900, no=3)
    assert same.info.own_was is None
    hook = bot.ig.reel_specs([same], local)[0]
    assert hook.was == "" and hook.big_note == "네이버 최저가 19,900원보다 4,000원 ↓"
    # 10% 미만으로 내린 값·30일보다 오래된 기록도 취소선 근거로 안 씀
    _observe(bot, "coupang:n", 16900, at(9, day=DAY - timedelta(days=2)))
    assert _reel_pick(bot, "coupang:n", "상품", 15900, no=4).info.own_was is None
    _observe(bot, "coupang:q", 19900, at(9, day=DAY - timedelta(days=40)))
    assert _reel_pick(bot, "coupang:q", "상품", 15900, no=5).info.own_was is None


def _bg(img: Image.Image, box: tuple[int, int, int, int]) -> int:
    """box 안의 색 수 (1 이면 배경만)."""
    return len(img.crop(box).getcolors(1_000_000) or [])


def test_reel_frames_keep_text_inside_the_instagram_safe_zones(bot) -> None:  # type: ignore[no-untyped-def]
    """1080×1920 릴스: 위 250px·아래 420px 는 인스타 단추·본문이 덮는다 — 고지 띠와 글이 모두 그 사이에 있고, 고지는 모든 장에서
    위 안전 영역 바로 아래에 보인다."""
    from dealbot.media import card_slides

    local = DAY.replace(hour=12, minute=20)
    _observe(bot, "coupang:o", 19900, at(9, day=DAY - timedelta(days=5)))
    long = "[1+1] 크리넥스 3겹 데코앤소프트 화이트 천연펄프 두루마리 화장지 30m 30롤 x 2팩 (총 60롤) 대용량 생활필수품 특가"
    picks = [_reel_pick(bot, "coupang:o", long, 15900, market=22900, no=12),
             _reel_pick(bot, "coupang:m", long, 1299000, market=1590000, no=13),
             _reel_pick(bot, "coupang:p", "삼다수 2L 12병", 9980, no=14)]
    picks[0].photo = _jpeg()
    plain = bot.ig.reel_specs(picks[1:], local)  # 첫 장: 시중가 비교만 (취소선 없음)
    struck = bot.ig.reel_specs(picks, local)  # 첫 장: 우리 기록의 바로 앞 가격 (취소선)
    assert struck[0].was and not plain[0].was
    for spec in [*struck, plain[0]]:
        img = card_slides.render(bot.card, spec)
        assert img.size == (1080, 1920)
        assert _bg(img, (0, 0, 1080, 250)) == 1, spec.kind  # 위 안전 영역: 아무것도 안 그림
        assert _bg(img, (0, 1500, 1080, 1920)) == 1, spec.kind  # 아래 안전 영역
        assert _bg(img, (0, 250, 1080, 340)) > 1, spec.kind  # 고지 띠: 위 안전 영역 바로 아래
        assert spec.ad.startswith("광고 · ")


def test_unit_lists_keep_one_unit_per_list() -> None:
    """휴지 1롤당과 물티슈 1매당(또는 액체 세제 100mL당과 가루 세제 100g당)을 한 목록에 세우지 않는다 — 한 장엔 한 단위."""
    from dealbot.publisher.insta_style import UnitRow

    def row(kind: str, label: str, value: int, title: str = "휴지·물티슈") -> UnitRow:
        return UnitRow(title=title, kind=kind, label=label, value=value, name=f"{kind} {value}", price=value * 30, deal_no=None)

    rows = [row("휴지", "1롤당", 530), row("물티슈", "1매당", 14), row("휴지", "1롤당", 430), row("물티슈", "1매당", 12),
            row("액체 세제", "100mL당", 367, "세제"), row("가루 세제", "100g당", 210, "세제"), row("기저귀", "1매당", 418, "기저귀")]
    lists = insta_style.unit_lists(rows, 4)
    assert list(lists) == ["물티슈 1매당", "휴지 1롤당", "액체 세제 100mL당", "가루 세제 100g당", "기저귀 1매당"]
    for name, got in lists.items():
        assert {insta_style.unit_list_name(r.kind, r.label) for r in got} == {name}
    assert [r.value for r in lists["휴지 1롤당"]] == [430, 530]  # 낮은 순
    assert len(insta_style.unit_lists(rows * 3, 4)["휴지 1롤당"]) == 4


def test_unit_slides_show_a_baseline_only_with_three_deals_of_that_unit(bot) -> None:  # type: ignore[no-untyped-def]
    from dealbot.media import card_slides
    from dealbot.publisher.insta_style import UnitRow

    def row(kind: str, label: str, value: int, no: int) -> UnitRow:
        return UnitRow(title="휴지·물티슈", kind=kind, label=label, value=value, name=f"상품 {no}", price=15900, deal_no=no,
                       posted=at(9), product_id=f"coupang:{no}")

    local = DAY.replace(hour=19)
    week = insta_style.unit_lists([row("휴지", "1롤당", 530, 1), row("물티슈", "1매당", 14, 2)], 4)
    specs = bot.ig.unit_specs(week, [("휴지", "1롤당", 480, 3)], [], local, local - timedelta(days=7))
    assert [s.kind for s in specs] == ["cover", "list", "list"]  # 기준선만 모은 섞인 장은 없음
    wipes, tissue = specs[1], specs[2]
    assert wipes.title == "물티슈 1매당 가격" and wipes.rows == []  # 물티슈 기준선 없음 (3건 미만 → 줄 자체를 뺌)
    assert tissue.title == "휴지 1롤당 가격" and tissue.rows[0] == ("사도 되는 기준선", "1롤당 480원")
    assert "3건" in tissue.rows[1][1]
    assert all(s.ad.startswith("광고 · ") for s in specs)
    for s in specs:
        assert card_slides.render(bot.card, s).size == (1080, 1350)


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
    for pid, name, price, days in (("coupang:t0", "깨끗한나라 화장지 27m 30롤", 12900, 12),  # 지난 딜: 기준선에만
                                   ("coupang:t9", "잘풀리는집 화장지 30m 30롤", 14400, 20)):
        old = at(9, day=DAY - timedelta(days=days))
        history(pid, name, price, 3, old)
        published(bot, pid, name, price, when=old)
    done = await tick(bot, clock, at(19, 6))
    assert done == "unit: posted 4242 (3 deals)"
    creates = [r for r in seen if r.url.path == "/v25.0/UID/media"]
    assert len(creates) == 5  # 표지 + 물티슈 1매당 + 휴지 1롤당 + 생수 100mL당 (+ 캐러셀) — 한 장엔 한 단위만
    alts = [r.url.params["alt_text"] for r in creates[1:-1]]
    assert alts[0].startswith("물티슈 1매당 가격 — ") and "롤" not in alts[0] and "기준선" not in alts[0]  # 물티슈 딜 1건: 기준선 없음
    assert alts[1].startswith("휴지 1롤당 가격 — ") and "물티슈" not in alts[1] and alts[1].endswith("사도 되는 기준선 1롤당 480원")
    assert alts[2].startswith("생수 100mL당 가격 — ") and "기준선" not in alts[2]
    caption = creates[-1].url.params["caption"]
    lines = caption.splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다" and lines[1].startswith("이번 주 생필품 1개당 가격")
    assert any(ln.endswith("크리넥스 3겹 30m 30롤 15,900원 → 휴지 1롤당 530원") for ln in lines)
    assert any(ln.endswith("삼다수 2L 12병 9,980원 → 생수 100mL당 42원") for ln in lines)
    assert "아이시스" not in caption and "샴푸" not in caption and "깨끗한나라" not in caption  # 이번 주 목록엔 없음
    assert "[휴지 1롤당]" in lines and "[물티슈 1매당]" in lines and "[휴지·물티슈]" not in lines  # 단위마다 따로
    # 기준선: 휴지 530·430·480 의 가운데 값 (우리가 기록한 딜 3건). 물티슈·생수는 1건뿐이라 기준선 줄이 없다
    assert "사도 되는 기준선: 휴지 1롤당 480원 (최근 우리가 올린 딜 3건의 가운데 값)" in lines
    assert sum("기준선" in ln for ln in lines) == 1
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
    assert insta_style.baseline([530, 430]) is None  # 2건의 가운데 값은 기준선으로 안 씀 (3건 이상)


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


# ---------------------------------------------------------------- 스레드 쪽(sns-th)과 합친 뒤: 대기열은 따로, 기록·사진은 같이
async def test_threads_and_instagram_keep_their_own_queues(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """채널 글 하나 → 스레드 대기열(threads_jobs, 07~24시·45분)과 인스타 대기열(ig_queue, 하루 10개·30~50분 — 주인 10/18)에 따로 줄 선다."""
    bot.settings.threads.enabled = True
    bot.clock = lambda: at(6)  # 스레드 리듬 밖 (07시 전) — 스레드도 기다림
    qid, deal = published(bot, "coupang:1", "도브 샴푸 680ml 2개", 15900, when=at(6))
    await to_instagram(bot, clock, qid, deal, photo=_jpeg())
    assert [j.queue_id for j in bot.db.threads_waiting()] == [qid]
    assert [(r["product_id"], r["queue_id"]) for r in bot.db.ig_queued()] == [("coupang:1", qid)]
    assert bot.db.ig_last_post_at() is None  # 인스타는 스레드 리듬과 상관없이 자기 시간표대로


async def test_instagram_queue_outcomes_go_to_the_sns_log(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """하루 0개 알림(growth/alerts)이 이유를 셀 수 있게 인스타 대기열의 올림·건너뜀·버림을 sns_log 에 남긴다."""
    _plan(bot, {})
    old_q, old = published(bot, "coupang:1", "오래된 딜", 9900, when=at(6))
    await to_instagram(bot, clock, old_q, old)
    sold_q, sold = published(bot, "coupang:2", "품절 딜", 9900, when=at(17), score=90)
    await to_instagram(bot, clock, sold_q, sold)
    ok_q, ok = published(bot, "coupang:3", "올라갈 딜", 9900, when=at(17), score=50)
    await to_instagram(bot, clock, ok_q, ok)
    bot.db.kv_set("soldout_marked:coupang:2", at(17, 30).isoformat())
    assert await tick(bot, clock, at(18, 30)) == "single: coupang:3"
    log = {(r["product_id"], r["outcome"]): r["reason"] for r in bot.db.sns_log_since("instagram", at(0))}
    assert log[("coupang:1", "expired")] == "waited too long"
    assert log[("coupang:2", "skipped")] == "sold out (channel record)"
    assert ("coupang:3", "posted") in log


async def test_sns_zero_alert_counts_the_instagram_queue_as_waiting(bot, clock) -> None:  # type: ignore[no-untyped-def]
    from dealbot.growth import alerts

    sent: list[str] = []

    async def alert(kind: str, text: str, kakao: str, **kw: Any) -> None:
        sent.append(text)

    bot.notifier.alert = alert
    bot.settings.threads.enabled = False
    bot.db.kv_set(alerts.SNS_LOG_SINCE, (at(9) - timedelta(days=2)).isoformat())
    qid, deal = published(bot, "coupang:1", "기다리는 딜", 9900, when=at(8))
    bot.db.record_post(deal, channel_id="-100", message_id=qid + 100, dry_run=False, now=at(8))  # 실제로 채널에 올라간 글
    await to_instagram(bot, clock, qid, deal)
    assert await alerts.sns_zero_check(bot, at(9))
    assert "인스타 0개 (대기 중 (하루 10개·07:00-23:00·30~50분 간격) 1)" in sent[0]


async def test_late_owner_photo_reaches_the_waiting_instagram_item(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """사진 없이 줄 선 딜에 나중에 주인 사진이 오면 (growth/late_photo) 인스타 대기열 사본에도 붙어 글자 카드 대신 그 사진."""
    qid, deal = published(bot, "coupang:1", "사진 없는 딜", 9900, when=at(9))
    await to_instagram(bot, clock, qid, deal)
    assert bot.db.ig_queued()[0]["photo_file"] is None
    photo = _jpeg((200, 80, 60))
    assert bot.save_late_photo(bot.db.get_queue_item(qid), photo)
    row = bot.db.ig_queued()[0]
    assert Path(row["photo_file"]).read_bytes() == photo
    assert bot.ig.attach_photo(qid, _jpeg()) == 0  # 이미 사진이 있으면 덮지 않음


# ---------------------------------------------------------------- 검토 뒤 고침 (IG-1~7)
def _owner_photo(color: tuple[int, int, int] = (200, 80, 60)) -> Any:
    async def load() -> tuple[bytes | None, str]:
        out = io.BytesIO()
        Image.new("RGB", (1000, 800), color).save(out, "JPEG")
        return out.getvalue(), ""

    return load


async def test_photoless_single_waits_for_the_late_owner_photo_while_photo_deals_go_first(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """IG-1: 사진 없이 올라간 딜은 주인 사진을 기다리는 4시간 동안 인스타 딜 글을 미룬다 (예전엔 몇 분 만에 글자 카드로 올라가
    주인에게 '인스타에 이 사진을 써요' 라고 한 뒤에도 그 사진이 안 들어갔다). 사진 있는 딜은 그 사이 먼저."""
    _plan(bot, {})
    q_a, a = published(bot, "coupang:A", "사진 없는 딜", 9900, when=at(9), score=90)
    q_b, b = published(bot, "coupang:B", "사진 있는 딜", 9900, when=at(9), score=10)
    clock["now"] = at(9)
    await bot._ask_late_photo(q_a, a, None, bot.export_published(a, None))
    await to_instagram(bot, clock, q_a, a)
    await to_instagram(bot, clock, q_b, b, photo=_jpeg())
    assert await tick(bot, clock, at(9, 1)) == "single: coupang:B"  # 점수는 A 가 높아도 사진을 기다리는 중
    assert await tick(bot, clock, at(11, 5)) is None  # 2시간 지났어도 A 는 아직 기다림 (13:00 까지)
    clock["now"] = at(11, 10)
    reply = await bot.attach_late_photo(q_a, _owner_photo())
    assert "블로그·인스타에 이 사진을 써요" in reply
    made: list[bytes | None] = []
    make_card = bot.make_card

    async def spy(deal: Deal, photo: bytes | None, **kw: Any) -> str | None:
        made.append(photo)
        return await make_card(deal, photo, **kw)

    bot.make_card = spy
    assert await tick(bot, clock, at(11, 11)) == "single: coupang:A"
    assert made and made[0] is not None  # 주인 사진으로 (글자 카드가 아니라)


async def test_photoless_single_posts_a_text_card_after_the_wait(bot, clock) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "사진 없는 딜", 9900, when=at(9))
    clock["now"] = at(9)
    await bot._ask_late_photo(qid, deal, None, bot.export_published(deal, None))
    await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(12, 59)) is None
    assert await tick(bot, clock, at(13, 1)) == "single: coupang:1"  # 4시간 뒤 사진이 안 왔으면 글자 카드로
    assert bot.db.ig_item(1)["photo_file"] is None


async def test_photo_wait_never_outlives_the_instagram_queue(bot, clock) -> None:  # type: ignore[no-untyped-def]
    bot.settings.publish.late_photo_hours = 12  # 대기열 시간(12시간)과 같으면 기다리다 버려지지 않게 그 앞에서 멈춤
    bot.settings.instagram.hours = "00:00-24:00"
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "사진 없는 딜", 9900, when=at(8))
    clock["now"] = at(8)
    await bot._ask_late_photo(qid, deal, None, bot.export_published(deal, None))
    await to_instagram(bot, clock, qid, deal)
    assert await tick(bot, clock, at(19, 20)) is None
    assert await tick(bot, clock, at(19, 31)) == "single: coupang:1"


async def test_late_photo_reply_says_when_the_instagram_single_already_went_up(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """IG-1: 이미 글자 카드로 올라간 인스타 딜 글은 사진을 바꿀 수 없다 — 답장도 그렇게 말한다."""
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "사진 없는 딜", 9900, when=at(9))
    await to_instagram(bot, clock, qid, deal)  # 사진 부탁 전에 줄 섬 (예전 순서)
    assert await tick(bot, clock, at(9, 1)) == "single: coupang:1"
    clock["now"] = at(9, 2)
    await bot.late_photo.ask(qid, deal, at(9))
    reply = await bot.attach_late_photo(qid, _owner_photo())
    assert reply.startswith(f"✅ #{qid} 사진을 받았어요") and "인스타 딜 글은 이미 글자 카드로 올라갔어요" in reply
    assert "블로그·인스타에" not in reply


async def test_publish_records_the_photo_wait_before_queueing_for_instagram(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """발행 흐름: 사진 기다림 기록(📷 사진 필요)이 인스타 대기열에 넣기 전에 — 그 사이 시간표가 돌아도 글자 카드로 먼저 안 올라감."""
    order: list[str] = []
    ask, side = bot._ask_late_photo, bot._publish_side_channels

    async def ask_spy(*a: Any, **kw: Any) -> None:
        order.append("ask")
        await ask(*a, **kw)

    async def side_spy(*a: Any, **kw: Any) -> None:
        order.append("side")
        await side(*a, **kw)

    bot._ask_late_photo, bot._publish_side_channels = ask_spy, side_spy
    bot.state.dry_run = False
    bot.publisher.dry_run = False
    bot.settings.publish.min_interval_seconds = 0

    async def fake_publish(deal: Deal, **kw: Any) -> PublishResult:
        return PublishResult(ok=True, message_id=1)

    bot.publisher.publish = fake_publish
    d = Deal(Product(source="goldbox", product_id="coupang:o1", shop="coupang", name="무선 청소기", price=99000,
                     url="https://shop.example.com/o1"), DealVerdict(is_deal=True, score=40))
    assert bot.db.enqueue(d, score=40, now=at(9))
    assert await bot.process_queue_once()
    assert order == ["ask", "side"]


def test_unit_carousel_slot_is_picked_apart_from_top5(bot) -> None:  # type: ignore[no-untyped-def]
    """IG-2: 단가표 분은 TOP5 와 떨어진 분 중에서 바로 고른다 (예전엔 60번 다시 뽑고도 안 되면 한 시간 안이어도 그대로 썼다).
    구간이 붙어 있어(19~20시·20~21시) 2시간은 안 되지만 1시간은 늘 넘고, 구간이 허락하면 인스타 글 간격(2시간)도 지킨다."""
    m = lambda s: int(s[:2]) * 60 + int(s[3:])  # noqa: E731
    tue = DAY.replace(hour=8)
    for seed in range(400):
        bot.ig.rng = random.Random(seed)
        bot.db.kv_set(f"ig_plan:{tue.date().isoformat()}", "")
        plan = bot.ig.plan(tue)
        assert "19:00" <= plan["unit"] < "20:00" and abs(m(plan["top5"]) - m(plan["unit"])) >= 60, (seed, plan)
    bot.settings.instagram.unit_carousel.window = "17:00-19:00"
    for seed in range(200):
        bot.ig.rng = random.Random(seed)
        bot.db.kv_set(f"ig_plan:{tue.date().isoformat()}", "")
        plan = bot.ig.plan(tue)
        assert abs(m(plan["top5"]) - m(plan["unit"])) >= bot.settings.instagram.min_gap_minutes, (seed, plan)


async def test_cheaper_repost_replaces_the_waiting_instagram_item(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """IG-3: 같은 상품이 더 싸게 다시 채널에 올라오면 기다리던 인스타 줄을 새 딜(가격·번호)로 바꾼다 — 예전 값·번호로 올라가지 않게."""
    _plan(bot, {})
    old_q, old = published(bot, "coupang:1", "도브 샴푸 680ml 2개", 15900, when=at(8))
    await to_instagram(bot, clock, old_q, old, photo=_jpeg())
    new_q, new = published(bot, "coupang:1", "도브 샴푸 680ml 2개", 13900, when=at(8, 30))
    await to_instagram(bot, clock, new_q, new)
    rows = bot.db.ig_queued()
    assert len(rows) == 1 and rows[0]["queue_id"] == new_q and rows[0]["deal"].product.price == 13900
    assert rows[0]["photo_file"]  # 새 글에 사진이 없으면 같은 상품의 원래 사진 그대로
    assert await tick(bot, clock, at(9)) == "single: coupang:1"
    assert bot.db.ig_posts_since(at(0), kind="single")[0]["items"] == [{"product_id": "coupang:1", "queue_id": new_q}]


def test_single_post_checks_the_text_drawn_on_the_card(bot) -> None:  # type: ignore[no-untyped-def]
    """IG-4: 딜 하나짜리 글도 카드에 그리는 글을 검사한다. B 는 위 고지 띠가 첫 부분 → 통과,
    current·A·C 는 고지가 바닥 작은 줄뿐이거나 없음 → 막힘 (예전엔 본문만 검사)."""
    deal = Deal(Product(source="goldbox", product_id="coupang:1", shop="coupang", name="크리넥스 3겹 30m 30롤", price=15900,
                        url="https://shop.example.com/1"), DealVerdict(is_deal=True))
    for layout, ok in (("B", True), ("A", False), ("C", False), ("current", False)):
        bot.card.layout = layout
        slide = bot.ig.single_slide(deal, None, deal_no=3)
        problems = bot.instagram.check(deal, deal_no=3, slides=[slide])
        assert (not problems) is ok, (layout, problems)
        if not ok:
            assert any("1번째 장에 광고·수수료 고지가 없음" in p for p in problems)
    bot.card.layout = "B"
    assert bot.ig.single_slide(deal, None, deal_no=3).texts()[0].startswith("광고 · ")


async def test_cut_off_single_counts_toward_the_gap_and_the_daily_cap(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """IG-5: 올리다 끊긴 글(재시작)은 인스타에 올라갔을 수 있다 — 간격·하루 개수에 세어 바로 다음 글이 붙어 나가지 않게."""
    _plan(bot, {})
    q1, d1 = published(bot, "coupang:1", "끊긴 딜", 9900, when=at(8))
    await to_instagram(bot, clock, q1, d1)
    q2, d2 = published(bot, "coupang:2", "다음 딜", 9900, when=at(8))
    await to_instagram(bot, clock, q2, d2)
    bot.db.ig_update(1, status="posting", now=at(9))  # 09:00 에 올리기 시작했다가 재시작
    assert await tick(bot, clock, at(9, 5)) is None  # 끊긴 글을 정리하고, 그 글에서 2시간 안이라 안 올림
    assert bot.db.ig_last_post_at() == at(9)
    assert await tick(bot, clock, at(11, 1)) == "single: coupang:2"


def test_unit_carousel_marks_past_deals_without_a_page_number(bot) -> None:  # type: ignore[no-untyped-def]
    """IG-6: 단가표는 지난 7일 딜이라 링크 페이지(48시간)에서 내려간 딜도 있다 — 번호 없는 줄은 '지난 딜' 로, 찾는 안내도 그에 맞게."""
    from dealbot.publisher.insta_style import UnitRow

    def row(no: int | None, pid: str) -> UnitRow:
        return UnitRow(title="휴지·물티슈", kind="휴지", label="1롤당", value=530, name=f"상품 {pid}", price=15900, deal_no=no,
                       posted=at(9), product_id=pid)

    local = DAY.replace(hour=19)
    since = local - timedelta(days=7)
    week = {"휴지·물티슈": [row(12, "coupang:1"), row(None, "coupang:2")]}
    specs = bot.ig.unit_specs(week, [], [], local, since)
    items = [a for a, _b, _c in specs[1].items]
    assert items == ["#12 상품 coupang:1", "상품 coupang:2 (지난 딜)"]
    assert specs[0].footer == specs[1].footer == insta_style.UNIT_FIND_OLD
    caption = insta_style.unit_caption(week, [], [], bot.registry, since=since, until=local)
    assert "(지난 딜)" in caption and "번호 없는 지난 딜은 링크 페이지에서 내려갔어요" in caption
    only_new = {"휴지·물티슈": [row(12, "coupang:1")]}
    assert bot.ig.unit_specs(only_new, [], [], local, since)[0].footer == insta_style.UNIT_FIND
    assert "지난 딜" not in insta_style.unit_caption(only_new, [], [], bot.registry, since=since, until=local)


async def test_scheduled_post_crash_is_logged_and_reported(bot, clock) -> None:  # type: ignore[no-untyped-def]
    """IG-7: 묶음 글이 발행 결과 밖에서 실패(영상 도구 오류 등)하면 sns_log(0개 알림 이유)와 주인 알림에 남는다 (예전엔 로그만)."""
    _plan(bot, {"reel": "12:10"})
    told: list[str] = []

    async def failed(sns: str, cmd: str, tg_text: str, error: str) -> None:
        told.append(tg_text)

    async def broken(now: datetime, local: datetime) -> str:
        raise RuntimeError("ffmpeg exited 1")

    bot._sns_failed = failed
    bot.ig._post_reel = broken
    assert (await tick(bot, clock, at(12, 11)) or "").startswith("reel: failed: RuntimeError")
    logged = bot.db.sns_log_since("instagram", at(0))
    assert [(r["outcome"], r["reason"]) for r in logged] == [("failed", "reel: RuntimeError: ffmpeg exited 1")]
    assert len(told) == 1 and "점심 릴스" in told[0] and "ffmpeg exited 1" in told[0]
    assert await tick(bot, clock, at(12, 20)) is None and len(told) == 1  # 같은 날 다시 안 함


async def test_unit_post_records_page_numbers_as_deal_no(bot, clock) -> None:  # type: ignore[no-untyped-def]
    from dealbot.publisher.insta_style import UnitRow

    rows = [UnitRow(title="휴지·물티슈", kind="휴지", label="1롤당", value=530, name="상품", price=15900, deal_no=7, posted=at(9),
                    product_id="coupang:1")]
    await bot.ig._finish("unit", PublishResult(ok=True, dry_run=True), rows, at(19))
    assert bot.db.ig_posts_since(at(0), kind="unit")[0]["items"] == [{"product_id": "coupang:1", "deal_no": 7}]


# ---------------------------------------------------------------- 데런픽 글 → 인스타 (주인 10/23 — growth/blog_share)
# Blog-Auto 가 쓴 var/sns_share/<글번호>.json 을 차례(post_after)가 되면 블로그 사진으로 캐러셀 · 취소된 글은 안 올림 · 같은 글은 한 번

CAPTION = "첫 방송부터 반응이 뜨거워요\n\n전체 이야기는 네이버 블로그 '데런픽'에서 볼 수 있어요\n사진: 뉴스1\n\n#원빈 #데런픽"


def _share(bot: Any, tmp: Path, pid: int = 7, *, photos: int = 2, after=None, cancelled: bool = False,
           size: tuple[int, int] = (600, 1200)) -> Path:
    var = tmp / "Blog-Auto" / "var"
    (var / "metrics").mkdir(parents=True, exist_ok=True)
    bot.settings.dashboard.blogauto_metrics_path = str(var / "metrics" / "blogauto.json")
    d = var / "sns_share" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(1, photos + 1):
        p = d / f"{i:02d}.jpg"
        Image.new("RGB", size, (200, 30 * i, 30)).save(p, "JPEG")
        files.append(str(p))
    rec = {"v": 1, "pid": pid, "title": "원빈 새 드라마 첫 방송", "url": "https://blog.naver.com/derenpick/1",
           "post_after": (after or at(10)).isoformat(), "ig_caption": CAPTION, "photos": files, "cancelled": cancelled}
    path = var / "sns_share" / f"{pid}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return path


async def test_pick_share_posts_a_carousel_of_the_blog_photos_when_due(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    seen = live(bot, _carousel_handler("9100"), monkeypatch)
    _plan(bot, {})
    _share(bot, tmp_path)
    assert await tick(bot, clock, at(9, 30)) is None  # 아직 차례 전
    assert await tick(bot, clock, at(10, 1)) == "pick #7: posted 9100"
    creates = [r for r in seen if r.url.path == "/v25.0/UID/media"]
    items, parent = creates[:-1], creates[-1]
    assert len(items) == 2 and all(r.url.params["is_carousel_item"] == "true" for r in items)
    assert all(r.url.params["image_url"].startswith("https://bot.example.com/media/pick_") for r in items)
    assert parent.url.params["media_type"] == "CAROUSEL" and parent.url.params["caption"] == CAPTION
    assert not list((bot.settings.data_dir / "media").glob("pick_*"))  # 올린 뒤 공개 사본은 지움
    assert [p["kind"] for p in bot.db.ig_posts_since(at(0))] == ["pick"]
    assert await tick(bot, clock, at(12)) is None  # 같은 글은 한 번


async def test_redone_share_posts_once_more_under_its_own_record(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """주인 10/25: 이상하다고 한 글을 새 검수로 다시 쓰면 (redo) 같은 글번호라도 한 번 더 올린다 — 다시 쓴 글도 한 번만."""
    live(bot, _carousel_handler("9100"), monkeypatch)
    _plan(bot, {})
    path = _share(bot, tmp_path)
    assert await tick(bot, clock, at(10, 1)) == "pick #7: posted 9100"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec.update(redo="owner_1025", post_after=at(11).isoformat(), ig_caption="다시 쓴 캡션이에요")
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    assert await tick(bot, clock, at(11, 1)) == "pick #7: posted 9100"
    assert bot.db.kv_get("blog_share:ig:7:owner_1025").startswith("posted")
    assert await tick(bot, clock, at(13)) is None


async def test_cancelled_or_photo_less_share_is_not_posted(bot, clock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    _share(bot, tmp_path, 7, cancelled=True)
    _share(bot, tmp_path, 8, photos=0)
    assert await tick(bot, clock, at(10, 5)) == "pick #8: skipped (no photo)"
    assert await tick(bot, clock, at(11)) is None
    assert bot.db.ig_posts_since(at(0)) == []


async def test_pick_share_keeps_the_instagram_gap_and_one_photo_posts_as_a_single_image(bot, clock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    bot.db.ig_record_post("single", media_id="1", items=[], now=at(9, 50))
    _share(bot, tmp_path, photos=1)
    assert await tick(bot, clock, at(10, 1)) is None  # 직전 인스타 글과 30분이 안 됨
    assert await tick(bot, clock, at(10, 50)) == "pick #7: posted dry-run"


async def test_stale_share_is_skipped(bot, clock, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    _share(bot, tmp_path, after=at(10) - timedelta(hours=31))
    assert await tick(bot, clock, at(10)) == "pick #7: skipped (too late)"


def test_photos_are_fitted_to_instagram_ratios(tmp_path) -> None:
    tall, wide = tmp_path / "tall.jpg", tmp_path / "wide.jpg"
    Image.new("RGB", (600, 1500), "white").save(tall, "JPEG")
    Image.new("RGB", (2000, 500), "white").save(wide, "JPEG")
    for p in (tall, wide):
        out = tmp_path / f"out_{p.name}"
        out.write_bytes(blog_share.fit_jpeg(p))
        with Image.open(out) as im:
            assert 0.8 - 0.01 <= im.width / im.height <= 1.91 + 0.01


async def test_profile_page_lists_pick_posts_shared_to_instagram(bot, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """주인 10/25 '인스타에 잘 올렸는데 내 블로그 링크도 넣어 줘야지': 인스타 캡션 주소는 안 눌려서 프로필 링크 페이지('/') 맨 위에
    인스타에 올린 데런픽 글 링크 (올린 글만)."""
    import json as _json

    from dealbot.growth import blog_share

    folder = blog_share.share_dir(bot.settings.dashboard.blogauto_metrics_path)
    folder.mkdir(parents=True, exist_ok=True)
    for pid, posted in ((7, True), (8, False)):
        (folder / f"{pid}.json").write_text(_json.dumps({"pid": pid, "title": f"글 {pid}", "url": f"https://blog.naver.com/derenpick/22400000000{pid}"}),
                                            encoding="utf-8")
        if posted:
            bot.db.kv_set(f"{blog_share.KV_DONE}{pid}", "posted 123 2026-10-25T00:00:00")
    assert bot._pick_links() == [{"title": "글 7", "url": "https://blog.naver.com/derenpick/224000000007"}]
    status, html = await bot._deals_page({})
    assert status == 200 and "데런픽 블로그 새 글" in html and "224000000007" in html and "224000000008" not in html
