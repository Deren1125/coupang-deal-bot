"""딜 상품 사진 자동 찾기 (주인 11/14 '사진이 필요한 딜은 왜 있는거야. 자동으로 해줘').

원인: 게시판(뽐뿌 등)에서 온 쿠팡 딜은 쿠팡 페이지를 읽지 않고(봇 확인으로 막힘) 게시판 썸네일은 상품 사진이 아니라 찾을 곳이 없었다
(서버 deal_photos 11/11~11/14: 사진 필요 #229·#230·#213 모두 쿠팡 게시판 딜, img 없음).
더한 곳: remembered(같은 상품을 API·페이지에서 본 사진) · coupang_api(파트너스 검색의 같은 상품번호 사진) · 발행 뒤 자동 다시 찾기.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from dealbot.config import Settings
from dealbot.coupang.client import ApiBudget, CoupangRateLimited
from dealbot.growth.photo_sources import PhotoStats, coupang_number
from dealbot.models import Deal, DealVerdict, Product

NOON = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # 12:00 KST
BOARD_THUMB = "https://cdn2.ppomppu.co.kr/zboard/data3/2026/1113/small_1.jpg"
API_IMG = "https://thumbnail7.coupangcdn.com/thumbnails/remote/1000x1000ex/image/retail/images/hp.jpg"


def _jpeg(w: int = 1000, h: int = 1000) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(out, "JPEG")
    return out.getvalue()


def _board_deal(num: str = "9024167552", *, name: str = "[쿠팡] HP 노트북 15 i5 479,000원", image_url: str | None = BOARD_THUMB) -> Deal:
    p = Product(source="ppomppu", product_id=f"coupang:{num}", shop="coupang", name=name, price=479000,
                url=f"https://www.coupang.com/vp/products/{num}", image_url=image_url)
    return Deal(p, DealVerdict(is_deal=True, score=10), affiliate_url="https://link.coupang.com/a/x", detected_at=NOON)


class FakeSearch:
    """쿠팡 파트너스 검색 흉내: 부른 키워드를 남기고, 정한 결과(또는 예외)를 돌려줌."""

    def __init__(self, results: list[dict[str, Any]] | None = None, exc: Exception | None = None) -> None:
        self.results = results or []
        self.exc = exc
        self.calls: list[tuple[str, str]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def search(self, keyword: str, *, limit: int = 10, image_size: str | None = None, kind: str = "search",
                     attempts: int | None = None) -> list[dict[str, Any]]:
        self.calls.append((keyword, kind))
        self.kwargs.append({"image_size": image_size, "attempts": attempts})
        if self.exc is not None:
            raise self.exc
        return list(self.results)


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    settings.instagram.enabled = False
    b = DealBot(settings)
    b.clock = lambda: NOON  # type: ignore[method-assign]
    sent: list[str] = []

    async def send_with_id(text: str, **_kw: Any) -> int:
        sent.append(text)
        return len(sent)

    alerts: list[dict[str, Any]] = []

    async def alert(kind: str, tg: str | None, kakao: str, **kw: Any) -> None:
        alerts.append({"kind": kind, **kw})

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.alert = alert  # type: ignore[method-assign]
    b.notifier.resolve = lambda *keys: None  # type: ignore[method-assign]
    b.sent, b.alerts = sent, alerts  # type: ignore[attr-defined]
    b.fetched = []  # type: ignore[attr-defined]
    images = {API_IMG: _jpeg()}
    b.images = images  # type: ignore[attr-defined]

    async def fetch(url: str, **_kw: object) -> bytes | None:
        b.fetched.append(url)  # type: ignore[attr-defined]
        return images.get(url)

    b._fetch_image = fetch  # type: ignore[method-assign]
    yield b
    b.db.close()


def _publish(bot, deal: Deal) -> int:  # type: ignore[no-untyped-def]
    assert bot.db.enqueue(deal, score=10, now=NOON)
    qid = max(it.id for it in bot.db.pending_items(10))
    bot.db.update_queue_item(qid, status="published", deal=deal, now=NOON)
    return qid


def _rows(settings: Settings) -> list[dict[str, Any]]:
    path = settings.data_dir / "published_deals.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ---------------------------------------------------------------- 쿠팡 상품번호
def test_coupang_number_from_url_or_key() -> None:
    assert coupang_number(_board_deal("123").product) == "123"
    p = Product(source="s", product_id="coupang:55:77", shop="coupang", name="x", price=1, url="https://link.coupang.com/a/x")
    assert coupang_number(p) == "55"
    assert coupang_number(Product(source="s", product_id="gmarket:1", shop="gmarket", name="x", price=1, url="https://x")) is None


# ---------------------------------------------------------------- 1. 전에 본 사진 (remembered)
async def test_board_coupang_deal_reuses_the_api_photo_seen_before(bot) -> None:  # type: ignore[no-untyped-def]
    # 골드박스(API)에서 같은 상품(옵션 붙은 키)을 본 적이 있음 → 뒤에 온 뽐뿌 썸네일이 그 사진을 덮지 않음
    seen = Product(source="goldbox", product_id="coupang:9024167552:111", shop="coupang", name="HP 노트북", price=500000,
                   url="https://www.coupang.com/vp/products/9024167552", image_url=API_IMG)
    bot.db.record_observation(seen, NOON - timedelta(days=1))
    again = Product(source="ppomppu", product_id="coupang:9024167552:111", shop="coupang", name="HP 노트북", price=479000,
                    url="https://www.coupang.com/vp/products/9024167552", image_url=BOARD_THUMB)
    bot.db.record_observation(again, NOON)
    bot.photo_search.client = FakeSearch()  # 부르면 안 됨
    d = _board_deal()
    photo = await bot.deal_photo(d)
    assert photo is not None and d.product.image_url == API_IMG
    assert BOARD_THUMB not in bot.fetched and bot.photo_search.client.calls == []
    assert PhotoStats(bot.db).totals(now=NOON) == {"remembered": 1}


# ---------------------------------------------------------------- 2. 쿠팡 검색 API (같은 상품번호만)
async def test_coupang_search_uses_only_the_same_product_number(bot) -> None:  # type: ignore[no-untyped-def]
    other = {"productId": 1, "productImage": "https://thumbnail7.coupangcdn.com/thumbnails/remote/492x492ex/image/other.jpg"}
    same = {"productId": 9024167552, "productImage": API_IMG.replace("1000x1000ex", "230x230ex")}
    bot.photo_search.client = FakeSearch([other, same])
    d = _board_deal()
    photo = await bot.deal_photo(d)
    assert photo is not None and d.product.image_url == API_IMG  # 작은 주소도 1000px 로
    keyword, kind = bot.photo_search.client.calls[0]
    assert kind == "photo" and "479" not in keyword and "hp" in keyword.lower()
    assert "other.jpg" not in " ".join(bot.fetched)
    assert PhotoStats(bot.db).totals(now=NOON) == {"coupang_api": 1}
    # 찾은 사진은 그 상품에 남아 다음엔 검색 없이 (remembered)
    bot.db.record_observation(d.product, NOON)
    bot.db.remember_product_image(d.product.product_id, API_IMG)
    bot.photo_search.client = FakeSearch()
    assert await bot.deal_photo(_board_deal()) is not None and bot.photo_search.client.calls == []


async def test_no_same_product_means_no_photo_and_no_search_for_a_day(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch([{"productId": 5, "productImage": API_IMG}])  # 이름은 비슷해도 다른 상품
    d = _board_deal()
    assert await bot.deal_photo(d) is None and d.product.image_url is None
    assert len(bot.photo_search.client.calls) == 1
    assert await bot.deal_photo(_board_deal()) is None
    assert len(bot.photo_search.client.calls) == 1  # 하루 동안 다시 안 찾음 (예산 아낌)
    assert bot._photo_retry == {}  # 다시 찾을 까닭 없음 → 주인에게 바로
    assert PhotoStats(bot.db).totals(now=NOON) == {"none": 2}


async def test_deal_photo_comes_first_and_search_is_not_called(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": API_IMG}])
    own = "https://thumbnail9.coupangcdn.com/thumbnails/remote/1000x1000ex/image/own.jpg"
    bot.images[own] = _jpeg()
    d = _board_deal(image_url=own)
    assert await bot.deal_photo(d) is not None and d.product.image_url == own
    assert bot.photo_search.client.calls == []


def test_photo_search_has_its_own_hourly_cap() -> None:
    budget = ApiBudget(10, reserve={"deeplink": 3}, caps={"deeplink": 7, "photo": 2})
    for _ in range(2):
        assert budget.available("photo")
        budget.record("photo")
    assert not budget.available("photo") and budget.available("deeplink") and budget.available("search")


# ---------------------------------------------------------------- 3. 발행 뒤 자동 다시 찾기 → 끝내 없을 때만 주인에게
async def test_budget_exhausted_holds_then_finds_the_photo_without_asking(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch(exc=CoupangRateLimited("budget"))
    d = _board_deal()
    qid = _publish(bot, d)
    photo = await bot.deal_photo(d)
    assert photo is None and bot._photo_retry == {d.product.product_id: "coupang_api:budget"}
    row = bot.export_published(d, None)
    await bot._ask_late_photo(qid, d, None, row)
    assert bot.sent == [] and bot.alerts == []  # 바로 묻지 않음
    assert bot.late_photo.until(qid) is not None  # 인스타 시간표는 그동안 글자 카드로 먼저 올리지 않음
    assert bot.late_photo_waiting(qid)  # 주인이 대시보드로 올려도 받음
    # 10분 전엔 다시 찾지 않음
    assert await bot.late_photo.tick(NOON + timedelta(minutes=5)) == 0 and len(bot.photo_search.client.calls) == 1
    # 다음 시간 몫이 생겨 같은 상품을 찾음 → 딜 사진으로 (photo_kind verified 줄), 주인에게 안 물음
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": API_IMG}])
    assert await bot.late_photo.tick(NOON + timedelta(minutes=11)) == 1
    assert bot.sent == [] and bot.alerts == [] and not bot.late_photo_waiting(qid)
    last = _rows(settings)[-1]
    assert last["photo_kind"] == "verified" and last["photo_src"] == API_IMG and Path(last["photo_file"]).is_file()
    assert last["posted_at"] == row["posted_at"] and last["photo_updated_at"]
    item = bot.db.get_queue_item(qid)
    assert item.deal.product.image_url == API_IMG and "owner_photo" not in item.deal.product.extra
    assert PhotoStats(bot.db).totals(now=NOON)["late_auto"] == 1


async def test_hold_asks_the_owner_when_the_window_ends(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch(exc=CoupangRateLimited("budget"))
    d = _board_deal()
    qid = _publish(bot, d)
    assert await bot.deal_photo(d) is None
    await bot._ask_late_photo(qid, d, None, bot.export_published(d, None))
    assert bot.sent == []
    assert await bot.late_photo.tick(NOON + timedelta(minutes=10)) == 0  # 아직 몫 없음 → 다음에 또
    assert bot.sent == []
    assert await bot.late_photo.tick(NOON + timedelta(minutes=30)) == 1  # 30분이 지나도 없음 → 주인에게
    assert len(bot.sent) == 1 and bot.sent[0].startswith(f"📷 사진 필요 #{qid}") and "3.5시간 안에" in bot.sent[0]
    assert bot.alerts[-1]["key"] == f"photo:{qid}" and bot.late_photo_waiting(qid)
    assert await bot.late_photo.tick(NOON + timedelta(minutes=50)) == 0  # 한 번만 물음
    assert PhotoStats(bot.db).totals(now=NOON).get("asked") == 1


async def test_nothing_left_to_try_asks_right_away(bot) -> None:  # type: ignore[no-untyped-def]
    d = _board_deal()  # 쿠팡 키 없음(검색 못 함)
    qid = _publish(bot, d)
    assert await bot.deal_photo(d) is None
    await bot._ask_late_photo(qid, d, None, bot.export_published(d, None))
    assert len(bot.sent) == 1 and "4시간 안에" in bot.sent[0]


# ---------------------------------------------------------------- 4. 어디서 찾았는지 보이게
def test_photo_stats_summary_and_status_line(bot) -> None:  # type: ignore[no-untyped-def]
    stats = PhotoStats(bot.db)
    for src in ("deal", "deal", "coupang_api", "none"):
        stats.record(src)
    assert stats.summary() == "딜 사진 2 · 쿠팡 검색 1 · 못 찾음 1"
    assert "📷 <b>상품 사진</b> 최근 7일 — 딜 사진 2 · 쿠팡 검색 1 · 못 찾음 1" in bot.reporter.status_text()


def test_photo_stats_keep_two_weeks(bot) -> None:  # type: ignore[no-untyped-def]
    stats = PhotoStats(bot.db)
    for d in range(20):
        stats.record("deal", NOON - timedelta(days=d))
    data = json.loads(bot.db.kv_get("photo_stats"))
    assert len(data) == 14 and stats.totals(7, NOON) == {"deal": 7}


# ---------------------------------------------------------------- 5. 점검 11 — 사진 크기·한 번만 검색·찾은 주소 기억 (주인 11/14)
ADS_IMG = "https://ads-partners.coupang.com/image1/AbCdEf123.jpg"  # '/thumbnails/remote/NxNex/' 가 아닌 파트너스 주소 — 크기를 못 바꿈


async def test_photo_search_asks_for_1000px_photos_and_one_attempt(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": ADS_IMG}])
    bot.images[ADS_IMG] = _jpeg()
    d = _board_deal()
    assert await bot.deal_photo(d) is not None and d.product.image_url == ADS_IMG
    assert bot.photo_search.client.kwargs == [{"image_size": "1000x1000", "attempts": 1}]


async def test_search_client_passes_attempts_to_the_request() -> None:
    from dealbot.coupang.client import CoupangClient

    seen: dict[str, Any] = {}

    class C(CoupangClient):
        def __init__(self) -> None:
            self.sub_id = "s"

        async def _request(self, method: str, path: str, *, params: Any = None, json: Any = None,  # type: ignore[override]
                           kind: str = "other", attempts: int | None = None) -> Any:
            seen.update(params=params, kind=kind, attempts=attempts)
            return {"productData": [{"productId": 1}]}

    assert await C().search("hp", image_size="1000x1000", kind="photo", attempts=1) == [{"productId": 1}]
    assert seen["attempts"] == 1 and seen["params"]["imageSize"] == "1000x1000" and seen["kind"] == "photo"


async def test_failed_download_retries_the_remembered_url_without_searching_again(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": ADS_IMG}])
    d = _board_deal()
    qid = _publish(bot, d)
    assert await bot.deal_photo(d) is None  # 사진 받기 실패 (CDN 일시 오류)
    assert bot._photo_retry == {d.product.product_id: "coupang_api:fetch"}
    await bot._ask_late_photo(qid, d, None, bot.export_published(d, None))
    assert bot.sent == []
    bot.images[ADS_IMG] = _jpeg()  # 이번엔 받힘
    assert await bot.late_photo.tick(NOON + timedelta(minutes=10)) == 1
    assert len(bot.photo_search.client.calls) == 1  # 검색은 처음 한 번뿐 — 기억한 주소를 다시 받음
    assert bot.fetched.count(ADS_IMG) == 2
    # 한 번만 셈: 'none' 은 hold 로 넘어가며 되돌리고 'late_auto' 하나
    assert PhotoStats(bot.db).totals(now=NOON) == {"late_auto": 1}


async def test_too_small_photo_is_remembered_for_a_day(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": ADS_IMG}])
    bot.images[ADS_IMG] = _jpeg(400, 400)  # 짧은 변 600px 미만
    d = _board_deal()
    assert await bot.deal_photo(d) is None
    assert bot._photo_retry == {}  # 다시 받아도 같음 → 다시 찾지 않고 주인에게
    assert bot.db.kv_get("photo_miss:9024167552")
    assert await bot.deal_photo(_board_deal()) is None
    assert len(bot.photo_search.client.calls) == 1 and bot.fetched.count(ADS_IMG) == 1  # 하루 동안 검색도 받기도 안 함
    bot.photo_search.prune()
    assert bot.db.kv_get("photo_miss:9024167552") and not bot.db.kv_get("photo_hit:9024167552")  # 하루 안 된 기록은 남음


async def test_broken_hit_record_is_ignored(bot) -> None:  # type: ignore[no-untyped-def]
    # 마지막 검토 11/14: 깨진 photo_hit 기록이 사진 찾기(→ 발행)를 터뜨리지 않음 — 없는 것으로 보고 다시 검색
    bot.photo_search.client = FakeSearch([{"productId": 9024167552, "productImage": ADS_IMG}])
    bot.images[ADS_IMG] = _jpeg()
    for broken in ('{"url": "' + ADS_IMG + '", "at": "어제"}', '{"url": "' + ADS_IMG + '", "at": [1]}', "[1]", "{"):
        bot.db.kv_set("photo_hit:9024167552", broken)
        assert await bot.photo_search.find(_board_deal().product) == (ADS_IMG, "found"), broken
    assert len(bot.photo_search.client.calls) == 4


# ---------------------------------------------------------------- 6. 점검 15 — 한 딜의 오류가 다른 딜·스레드 차례를 막지 않음
async def test_one_failing_deal_does_not_stop_the_others(bot) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch(exc=CoupangRateLimited("budget"))
    d1, d2 = _board_deal("111"), _board_deal("222")
    q1, q2 = _publish(bot, d1), _publish(bot, d2)
    for q, d in ((q1, d1), (q2, d2)):
        assert await bot.deal_photo(d) is None
        await bot._ask_late_photo(q, d, None, bot.export_published(d, None))
    real = bot.save_auto_photo

    def boom(item, jpeg, src):  # type: ignore[no-untyped-def]
        if item.id == q1:
            raise OSError("disk full")
        return real(item, jpeg, src)

    bot.save_auto_photo = boom  # type: ignore[method-assign]
    bot.photo_search.client = FakeSearch([{"productId": 111, "productImage": API_IMG}, {"productId": 222, "productImage": API_IMG}])
    t = NOON + timedelta(minutes=10)
    assert await bot.late_photo.tick(t) == 1  # #q2 는 사진을 받음
    assert not bot.late_photo_waiting(q2)
    info = bot.late_photo._info(q1)
    assert info["auto_until"] and info["auto_next"] == "2026-11-14T03:20:00+00:00"  # 이 딜만 10분 뒤로
    assert any("오류" in r["message"] and r["level"] == "WARNING" for r in bot.db.recent_events(10) if r["kind"] == "late_photo")
    # 10분 뒤엔 다시 시도 → 이번엔 받음
    bot.save_auto_photo = real  # type: ignore[method-assign]
    assert await bot.late_photo.tick(t) == 0 and await bot.late_photo.tick(t + timedelta(minutes=10)) == 1
    assert not bot.late_photo_waiting(q1)


async def test_failed_ask_keeps_the_automatic_retry(bot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bot.photo_search.client = FakeSearch(exc=CoupangRateLimited("budget"))
    d = _board_deal()
    qid = _publish(bot, d)
    assert await bot.deal_photo(d) is None
    await bot._ask_late_photo(qid, d, None, bot.export_published(d, None))

    async def fail(text: str, **_kw: Any) -> int | None:
        return None  # 텔레그램 오류 (관리자 챗 알림은 켜져 있음)

    bot.notifier.send_with_id = fail  # type: ignore[method-assign]
    monkeypatch.setattr(type(bot.notifier), "tg_quiet", property(lambda _s: False))
    monkeypatch.setattr(type(bot.notifier), "enabled", property(lambda _s: True))
    assert await bot.late_photo.tick(NOON + timedelta(minutes=30)) == 0
    info = bot.late_photo._info(qid)
    assert info["auto_until"] and info["auto_next"]  # 기록이 그대로 — 10분 뒤 다시
    assert bot.alerts == [] and "asked" not in PhotoStats(bot.db).totals(now=NOON)
    sent: list[str] = []

    async def ok(text: str, **_kw: Any) -> int:
        sent.append(text)
        return 7

    bot.notifier.send_with_id = ok  # type: ignore[method-assign]
    assert await bot.late_photo.tick(NOON + timedelta(minutes=41)) == 1
    assert len(sent) == 1 and "auto_until" not in bot.late_photo._info(qid)
    assert PhotoStats(bot.db).totals(now=NOON) == {"asked": 1}


async def test_publisher_loop_runs_threads_even_when_late_photo_fails(bot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    from dealbot import scheduler

    stop = asyncio.Event()
    ran: list[str] = []
    errors: list[str] = []

    async def queue_once() -> None:
        ran.append("queue")

    class Late:
        async def tick(self) -> int:
            raise RuntimeError("late boom")

    class Flow:
        async def tick(self) -> None:
            ran.append("threads")
            stop.set()

    async def notify_error(src: str, msg: str) -> None:
        errors.append(src)

    bot.process_queue_once = queue_once  # type: ignore[method-assign]
    bot.late_photo = Late()  # type: ignore[assignment]
    bot.threads_flow = Flow()  # type: ignore[attr-defined]
    bot.notifier.notify_error = notify_error  # type: ignore[method-assign]
    bot.notifier.error_cleared = lambda *_a: None  # type: ignore[method-assign]
    monkeypatch.setattr(scheduler, "update_in_progress", lambda _s: False)
    monkeypatch.setattr(scheduler, "VAR_DIR", None)
    await asyncio.wait_for(scheduler.publisher_loop(bot, stop), 5)
    assert ran == ["queue", "threads"] and errors == []
