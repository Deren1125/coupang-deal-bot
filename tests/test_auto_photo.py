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

    async def search(self, keyword: str, *, limit: int = 10, image_size: str | None = None, kind: str = "search") -> list[dict[str, Any]]:
        self.calls.append((keyword, kind))
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
