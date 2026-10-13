"""상품 페이지 가격 대조 (주인 결정 10/12, 큐 #22).

#22: 뽐뿌 '[토스] 풀무원 생수 2L 24개 (6,410원/무료)' → 실제 페이지는 24개 14,465원 · 12개 6,390원.
제목 가격만 믿고 쿠팡 24병 31,900원과 비교해 '79.9% 싸다'며 링크 요청까지 갔다. 이제는 페이지 가격·옵션과 맞춰 보고
안 맞으면 보류(held) + 관리자 챗 한 줄, 링크 요청·발행 없음. 페이지를 못 읽으면 지금처럼 진행(확인 못 함).
10/12 서버: 네이버 스토어 일반 요청이 모두 429 라 네이버 딜은 하나도 대조 못 함 → 막히면 사진용 크롬으로 읽기 (맨 아래 묶음).
테스트는 가짜만 쓴다 (네트워크·진짜 크롬 없음).
"""

from __future__ import annotations

import asyncio
import logging
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, PageCheckConfig, Settings
from dealbot.enrich import (
    PageEnricher,
    PageMeta,
    is_naver_off_shop,
    is_naver_product_page,
    page_meta_at,
)
from dealbot.models import Deal, Product
from dealbot.page_price import (
    PageOption,
    PagePrices,
    check_page_price,
    hold_line,
    naver_state_prices,
    quantity_conflict,
)
from dealbot.page_reader import PageReader
from dealbot.pricing.market import MarketQuote

FIX = Path(__file__).parent / "fixtures"
_REAL_FETCH = PageEnricher.fetch  # conftest 가 테스트마다 '못 읽음' 으로 바꿔 끼우기 전의 진짜 읽기 (가짜 HTTP 로만 씀)
TOSS_URL = "https://toss.shopping/t/801712557"
NAVER_URL = "https://smartstore.naver.com/water/products/11223344556"
NAME_22 = "풀무원 샘물 생수 무라벨 2L 24개"
HOLD_22 = "⏸ #{n} 보류: 글 가격 6,410원 ≠ 상품 페이지 14,465원 (2L 24개) — 6,390원짜리는 '2L 12개' 옵션이에요"


def _meta(name: str, url: str) -> PageMeta:
    return page_meta_at((FIX / name).read_text(encoding="utf-8"), url)


def _prices(*opts: tuple[str | None, tuple[int, ...]], fee: int | None = None) -> PagePrices:
    return PagePrices(options=[PageOption(label=lbl, prices=ps, lead=i == 0) for i, (lbl, ps) in enumerate(opts)], shipping_fee=fee)


# ---------------------------------------------------------------- 페이지 읽기
def test_toss_web_page_has_no_price() -> None:
    """실제 토스 상품 페이지(10/12 받음): 사진·별점·후기 수는 있지만 가격은 어디에도 없다 → 확인 못 함 (지금처럼 진행)."""
    meta = _meta("toss_t801712557.html", TOSS_URL)
    assert meta.title and "2L, 24개" in meta.title and meta.rating == 4.7 and meta.review_count == 587
    assert meta.price is None and meta.prices is None
    check = check_page_price(NAME_22, 6410, meta.prices, page_title=meta.title)
    assert check.status == "unverified" and check.reason == "페이지에 가격 없음"


def test_jsonld_offers_are_options() -> None:
    meta = _meta("page_case22_options.html", TOSS_URL)
    assert meta.prices is not None and meta.prices.source == "jsonld"
    assert [(o.label, o.prices, o.lead) for o in meta.prices.options] == [("2L 24개", (14465,), True), ("2L 12개", (6390,), False)]


def test_naver_state_prices_options_and_discounts() -> None:
    meta = _meta("naver_store_state.html", NAVER_URL)
    assert meta.prices is not None and meta.prices.source == "naver_state" and meta.prices.shipping_fee == 0
    opts = meta.prices.options
    # 품절 옵션(36개, 재고 0)은 뺌. 옵션 값 = (할인가·판매가) + 차액. 차액 0 인 옵션이 기본
    assert [(o.label, o.prices, o.lead) for o in opts] == [("2L 12개", (6390, 7100), True), ("2L 24개", (14465, 15175), False)]
    # 같은 페이지의 다른 상품(추천 상품) 가격은 안 씀: 주소 상품번호와 같은 상품만
    assert naver_state_prices("<script>window.__PRELOADED_STATE__={\"x\":1}</script>") is None
    # 주소 상품번호와 같은 상품이 없으면(구조가 다르거나 추천 상품만 보임) 다른 상품 값으로 대조하지 않음 → 확인 못 함
    html = (FIX / "naver_store_state.html").read_text(encoding="utf-8")
    assert naver_state_prices(html, "https://smartstore.naver.com/water/products/123") is None
    assert naver_state_prices(html, "https://shopping.naver.com/window-products/style/123") is not None  # 번호 없는 주소는 전처럼


# ---------------------------------------------------------------- 대조 규칙
def test_case22_holds_and_names_the_12_pack() -> None:
    meta = _meta("page_case22_options.html", TOSS_URL)
    check = check_page_price(NAME_22, 6410, meta.prices, page_title=meta.title)
    assert check.status == "hold" and check.reason == "quantity"
    assert (check.page_price, check.page_label, check.matched_price, check.matched_label) == (14465, "2L 24개", 6390, "2L 12개")
    assert hold_line(22, check) == HOLD_22.format(n=22)


def test_matching_lead_price_proceeds() -> None:
    meta = _meta("page_case22_options.html", TOSS_URL)
    check = check_page_price(NAME_22, 14900, meta.prices)  # 14,465 은 14,900 의 ±10% 안
    assert check.status == "ok" and check.matched_price == 14465 and check.matched_label == "2L 24개"


def test_other_option_same_quantity_is_recorded() -> None:
    """맞는 값이 기본이 아닌 옵션이어도 수량이 제목과 같으면(맛·색 옵션 등) 진행하고 옵션 이름을 남긴다."""
    p = _prices(("오리지널 24개", (21900,)), ("라임 24개", (16900,)))
    check = check_page_price("탄산수 500ml 24개", 16900, p)
    assert check.status == "option" and check.matched_label == "라임 24개" and check.matched_price == 16900


def test_no_price_within_tolerance_holds() -> None:
    check = check_page_price(NAME_22, 6410, _prices(("2L 24개", (14465,)), ("2L 36개", (21000,))))
    assert check.status == "hold" and check.reason == "price" and check.matched_label is None
    assert hold_line(7, check) == "⏸ #7 보류: 글 가격 6,410원 ≠ 상품 페이지 14,465원 (2L 24개) — 맞는 옵션이 없어요"


@pytest.mark.parametrize(
    ("title_price", "prices", "why"),
    [
        (8900, _prices((None, (9900, 8900))), "쿠폰·할인가가 따로 보임 (판매가 9,900 · 할인가 8,900)"),
        (9900, _prices((None, (8900, 9900))), "제목이 할인 전 값"),
        (12900, _prices((None, (9900,)), fee=3000), "제목이 배송비 포함가"),
        (11800, _prices((None, (5900,))), "2개 구매 가격"),
        (9500, _prices((None, (9990,))), "±10% 안의 작은 차이 (회원가 등)"),
    ],
)
def test_tolerant_matches(title_price: int, prices: PagePrices, why: str) -> None:
    assert check_page_price("아무 상품", title_price, prices).status == "ok", why


def test_quantity_conflict_only_when_both_sides_say() -> None:
    assert quantity_conflict("풀무원 생수 2L 24개", "2L 12개")
    assert quantity_conflict("풀무원 생수 2L 24개", "500ml 24개")
    assert not quantity_conflict("풀무원 생수 2L 24개", "12개 x2")  # 12개 두 묶음 = 24개
    assert not quantity_conflict("풀무원 생수 2L 24개", "무라벨")
    assert not quantity_conflict("풀무원 생수", "2L 12개")  # 제목에 수량이 없으면 다르다고 단정하지 않음
    assert not quantity_conflict("풀무원 생수 2L 24개", None)


# ---------------------------------------------------------------- 파이프라인
@register("fake_page")
class FakePageCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return [Product(**_copy(p)) for p in FakePageCollector.products]


def _copy(p: Product) -> dict[str, Any]:
    return {f: (dict(getattr(p, f)) if f == "extra" else getattr(p, f)) for f in Product.__dataclass_fields__}


def _toss(name: str = NAME_22, price: int = 6410, pid: str = "801712557") -> Product:
    return Product(source="ppomppu", product_id=f"toss:{pid}", shop="toss", name=name, price=price, url=f"https://toss.shopping/t/{pid}",
                   shipping="무료", recommend_count=9, extra={"post_url": "https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=1"})


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake_page", type="fake_page", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False  # 링크 요청이 실제로 나가는지 보려고
    FakePageCollector.products = []
    b = DealBot(settings)
    b.sent = []  # type: ignore[attr-defined]
    b.link_requests = []  # type: ignore[attr-defined]

    async def send_with_id(text: str, *, silent: bool = False, kind: str = "", queue_id: int | None = None) -> int:
        b.sent.append((text, silent, kind, queue_id))  # type: ignore[attr-defined]
        return 1

    async def manual_link(item: Any, shop: Any, *, auth_note: str | None = None) -> int:
        b.link_requests.append(item.id)  # type: ignore[attr-defined]
        return 2

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.notify_manual_link = manual_link  # type: ignore[method-assign]
    yield b
    b.db.close()


def _serve(bot: DealBot, pages: dict[str, PageMeta | None]) -> list[str]:
    seen: list[str] = []

    async def fetch(url: str) -> PageMeta | None:
        seen.append(url)
        return pages.get(url)

    bot.enricher.fetch = fetch  # type: ignore[method-assign]
    return seen


async def test_case22_reproduction_is_held_without_link_request(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    _serve(bot, {TOSS_URL: _meta("page_case22_options.html", TOSS_URL)})
    quotes: list[int] = []

    async def market_quote(p: Product) -> MarketQuote:
        quotes.append(p.price)
        return MarketQuote(price=31900, source="coupang", title="풀무원 샘물 2L 24병")

    bot.market = object()  # type: ignore[assignment]  # 시중가 대조가 켜진 환경
    bot._market_quote = market_quote  # type: ignore[method-assign]
    FakePageCollector.products = [_toss()]
    with caplog.at_level(logging.INFO, logger="dealbot.app"):
        await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1}
    assert quotes == []  # 틀린 가격으로 쿠팡 대조(79.9%↓)를 하지 않음
    assert bot.db.price_history_count() == 0  # 틀린 가격을 가격 기록에 남기지 않음
    item = bot.db.items_for_product("toss:801712557", ("held",))[0]
    assert item.last_error == "held: page price (quantity)"
    text, silent, kind, qid = bot.sent[0]  # type: ignore[attr-defined]
    assert silent and kind == "price_hold" and qid == item.id
    assert text.splitlines()[0] == HOLD_22.format(n=item.id)
    assert sum(f"queue #{item.id} held" in r.getMessage() for r in caplog.records) == 1

    # 대기열에 올릴 글이 없음 → 링크 요청 없음
    assert not await bot.process_queue_once()
    assert bot.link_requests == []  # type: ignore[attr-defined]

    # 같은 글을 다시 봐도(추천이 올라 재수집) 메모를 또 보내지 않음
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1} and len(bot.sent) == 1  # type: ignore[attr-defined]


async def test_hold_found_later_holds_the_open_link_request_too(bot: DealBot) -> None:
    """먼저 페이지를 못 읽어(429) 줄 세우고 링크까지 요청한 딜을, 다음 수집에서 읽은 페이지가 안 맞는다고 하면:
    새 보류 글을 따로 만들지 않고 그 글 자체를 보류한다 (링크 요청 메시지도 보류 안내로 고침). 주인이 링크를 보내도 올라가지 않고,
    다시 못 읽은 판에서도 같은 제목 가격이면 또 줄 세우지 않는다. 제목 가격이 바뀌면 새 딜로 본다."""
    edits: list[tuple[int, str]] = []

    async def edit(message_id: int, text: str) -> bool:
        edits.append((message_id, text))
        return True

    bot.notifier.edit = edit  # type: ignore[method-assign]
    _serve(bot, {})  # 1판: 페이지 막힘 → 확인 못 함으로 진행
    FakePageCollector.products = [_toss()]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1} and len(bot.link_requests) == 1  # type: ignore[attr-defined]
    item = bot.db.awaiting_items()[0]

    _serve(bot, {TOSS_URL: _meta("page_case22_options.html", TOSS_URL)})  # 2판: 페이지를 읽었더니 24개가 아니라 12개 값
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1}  # 링크 요청 중이던 그 글이 보류로 (따로 보류 글을 만들지 않음)
    held = bot.db.get_queue_item(item.id)
    assert held is not None and held.status == "held" and held.last_error == "held: page price (quantity, re-collected)"
    assert held.deal.product.extra["page_price"]["status"] == "hold"
    assert edits and edits[0][0] == 2 and edits[0][1].splitlines()[0] == HOLD_22.format(n=item.id)  # 링크 요청 메시지를 고침
    assert "링크를 만들 필요가 없습니다" in edits[0][1]

    reply = await bot.attach_link(item.id, "https://toss.im/_m/MINE")  # 주인이 그사이 링크를 만들어 보내도
    assert "'held' 상태라 링크를 붙일 수 없습니다" in reply
    assert not await bot.process_queue_once() and bot.db.recent_posts() == []

    # 3판: 기억해 둔 페이지(cache_minutes)가 지난 뒤 다시 막힘 — 같은 제목 가격이면 또 줄 세우지 않음
    bot.page_reader._clock = lambda: 10_000_000.0  # type: ignore[assignment]
    _serve(bot, {})
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1}
    assert len(edits) == 1 and len(bot.sent) == 0  # type: ignore[attr-defined]  # 같은 보류를 또 알리지 않음

    FakePageCollector.products = [_toss(price=5900)]  # 제목 가격이 바뀌면 새 딜 (확인 못 함으로 진행)
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1, "pending": 1}


async def test_matching_page_price_proceeds_and_market_uses_page_price(bot: DealBot) -> None:
    page = PageMeta(title="풀무원 샘물 생수, 무라벨, 2L, 24개", final_url=TOSS_URL,
                    prices=_prices((None, (6800,))))
    _serve(bot, {TOSS_URL: page})
    quotes: list[int] = []

    async def market_quote(p: Product) -> MarketQuote:
        quotes.append(p.price)
        return MarketQuote(price=10000, source="coupang", title="풀무원 샘물 2L 24병")

    bot.market = object()  # type: ignore[assignment]
    bot._market_quote = market_quote  # type: ignore[method-assign]
    FakePageCollector.products = [_toss(price=6410)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    assert quotes == [6800]  # 시중가 대조는 페이지 가격으로
    deal = bot.db.pending_items()[0].deal
    assert deal.product.price == 6800 and deal.product.extra["title_price"] == 6410
    assert deal.verdict.below_market_pct == 32.0  # (10,000 - 6,800) / 10,000 — 제목 가격이면 35.9%
    assert deal.product.extra["page_price"]["status"] == "ok"
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1} and len(bot.link_requests) == 1  # type: ignore[attr-defined]


async def test_other_option_match_records_option_for_templates(bot: DealBot) -> None:
    page = PageMeta(title="스파클 탄산수 500ml 24개", final_url=TOSS_URL,
                    prices=_prices(("플레인 500ml 24개", (12900,)), ("레몬 500ml 24개", (9900,))))
    _serve(bot, {TOSS_URL: page})
    FakePageCollector.products = [_toss(name="스파클 탄산수 레몬 500ml 24개", price=9900)]
    await bot.run_collector(bot.collectors[0])
    deal = bot.db.pending_items()[0].deal
    assert deal.product.extra["page_option"] == "레몬 500ml 24개" and deal.product.price == 9900
    deal.affiliate_url = "https://toss.im/_m/MINE"
    assert "구매옵션 : 레몬 500ml 24개" in bot.publisher.render(deal)


async def test_option_match_with_other_quantity_is_held(bot: DealBot) -> None:
    """맞는 값이 다른 수량 옵션이면 이름을 고치지 않고 보류 (커뮤니티 글과 다른 상품을 올리지 않게)."""
    page = PageMeta(title="스파클 생수 500ml", final_url=TOSS_URL,
                    prices=_prices(("500ml 40병", (11800,)), ("500ml 20병", (5900,))))
    _serve(bot, {TOSS_URL: page})
    FakePageCollector.products = [_toss(name="스파클 생수 500ml 40병", price=5900)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1}
    assert "5,900원짜리는 '500ml 20병' 옵션이에요" in bot.sent[0][0]  # type: ignore[attr-defined]


async def test_unreadable_page_proceeds_unverified(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    _serve(bot, {})  # 막힘·시간 초과 → None
    FakePageCollector.products = [_toss()]
    with caplog.at_level(logging.INFO, logger="dealbot.app"):
        await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    deal = bot.db.pending_items()[0].deal
    assert deal.product.price == 6410 and deal.product.extra["page_price"]["status"] == "unverified"
    assert any("page price unverified" in r.getMessage() and "페이지를 못 읽음" in r.getMessage() for r in caplog.records)
    assert bot.sent == []  # type: ignore[attr-defined]


async def test_real_toss_page_proceeds_unverified(bot: DealBot) -> None:
    """지금의 토스 웹 페이지(가격 없음)로는 #22 도 '확인 못 함' — 이 변경만으로는 토스 딜을 못 막는다 (보고서 참고)."""
    _serve(bot, {TOSS_URL: _meta("toss_t801712557.html", TOSS_URL)})
    FakePageCollector.products = [_toss()]
    await bot.run_collector(bot.collectors[0])
    deal = bot.db.pending_items()[0].deal
    assert deal.product.extra["page_price"] == {**deal.product.extra["page_price"], "status": "unverified", "reason": "페이지에 가격 없음"}


async def test_naver_store_page_mismatch_is_held(bot: DealBot) -> None:
    _serve(bot, {NAVER_URL: _meta("naver_store_state.html", NAVER_URL)})
    FakePageCollector.products = [Product(source="ppomppu", product_id="naver:11223344556", shop="naver", name=NAME_22, price=6410,
                                          url=NAVER_URL, recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"held": 1}
    # 네이버는 기본(차액 0) 옵션이 12개: 기본가에 맞아도 수량이 다르니 보류, 대표 가격은 그 기본 옵션
    assert bot.sent[0][0].splitlines()[0].endswith("상품 페이지 6,390원 (2L 12개) — 6,390원짜리는 '2L 12개' 옵션이에요")  # type: ignore[attr-defined]


async def test_naver_store_page_match_uses_page_price(bot: DealBot) -> None:
    _serve(bot, {NAVER_URL: _meta("naver_store_state.html", NAVER_URL)})
    FakePageCollector.products = [Product(source="ppomppu", product_id="naver:11223344556", shop="naver", name=NAME_22, price=14500,
                                          url=NAVER_URL, recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    deal = bot.db.pending_items()[0].deal
    # 24개 옵션의 할인가 14,465 · 판매가 15,175 중 글 가격에 가까운 쪽 (12개 옵션 7,100 x2 = 14,200 묶음보다 낱개 옵션 먼저)
    assert deal.product.price == 14465 and deal.product.extra["page_option"] == "2L 24개"


async def test_naver_store_browser_page_holds_before_publish(bot: DealBot) -> None:
    """수집 때 스토어가 막혀(429) 확인 못 한 딜: 발행 직전 사진 때문에 이미 여는 크롬 페이지로 가격을 보고, 안 맞으면 올리지 않는다.
    크롬을 따로 더 열지는 않는다 (사진이 있으면 크롬도 안 엶)."""
    _serve(bot, {})
    opened: list[str] = []

    async def page_meta(url: str) -> PageMeta:
        opened.append(url)
        return _meta("naver_store_state.html", url)

    bot.store_browser.page_meta = page_meta  # type: ignore[method-assign]
    p = Product(source="ppomppu", product_id="naver:11223344556", shop="naver", name="풀무원 샘물 2L 24개", price=6410, url=NAVER_URL,
                recommend_count=9, affiliate_url=None)
    FakePageCollector.products = [p]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    item = bot.db.pending_items()[0]
    await bot.attach_link(item.id, "https://naver.me/MINE")  # 주인이 링크를 만들어 줌
    assert await bot.process_queue_once()
    assert opened == [NAVER_URL]  # 사진이 없어 원래 여는 그 한 번
    assert bot.db.queue_counts() == {"held": 1} and bot.db.recent_posts() == []
    assert bot.sent[-1][2] == "price_hold"  # type: ignore[attr-defined]


async def test_page_check_off_keeps_old_behaviour(bot: DealBot) -> None:
    bot.settings.deal.page_check.enabled = False
    _serve(bot, {TOSS_URL: _meta("page_case22_options.html", TOSS_URL)})
    FakePageCollector.products = [_toss()]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    assert "page_price" not in bot.db.pending_items()[0].deal.product.extra


def test_held_deal_round_trips(db: Any) -> None:
    deal = Deal.from_dict({"product": _copy(_toss()), "verdict": {"is_deal": True}})
    qid = db.add_held(deal, score=1.0, error="held: page price (price)")
    assert db.get_queue_item(qid).status == "held" and db.queue_counts() == {"held": 1}
    assert db.enqueue(deal, score=1.0)  # 보류는 열린 글이 아니라 나중에 값이 맞으면 다시 들어올 수 있음


def test_card_discount_in_title_is_tolerated_one_way() -> None:
    """페이지에 안 보이는 할인(카드 청구할인·다운로드 쿠폰)을 뺀 글 가격: 제목에 그런 말이 있을 때만, 글이 더 싼 쪽으로만 더 봐 준다."""
    page = _prices((None, (14900,)))
    assert check_page_price("샴푸 1L", 12900, page).status == "hold"  # 13% 차이, 할인 말 없음
    assert check_page_price("샴푸 1L", 12900, page, discount_tolerance_pct=25).status == "ok"
    assert check_page_price("샴푸 1L", 17900, page, discount_tolerance_pct=25).status == "hold"  # 글이 더 비싼 쪽은 그대로 ±10%
    assert check_page_price(NAME_22, 6410, _prices(("2L 24개", (14465,))), discount_tolerance_pct=25).status == "hold"  # #22 는 그대로
    # 25% 는 페이지 가격 기준: 10,000원 페이지에 카드가 7,500원(25%)까지 맞고, 그보다 싸면 보류
    ten = _prices((None, (10000,)))
    assert check_page_price("샴푸 1L", 7700, ten, discount_tolerance_pct=25).status == "ok"
    assert check_page_price("샴푸 1L", 7500, ten, discount_tolerance_pct=25).status == "ok"
    assert check_page_price("샴푸 1L", 7400, ten, discount_tolerance_pct=25).status == "hold"


async def test_card_word_in_post_title_widens_match(bot: DealBot) -> None:
    page = PageMeta(title="샴푸 1L", final_url=TOSS_URL, prices=_prices((None, (14900,))))
    _serve(bot, {TOSS_URL: page})
    p = _toss(name="샴푸 1L", price=12900)
    p.extra["title"] = "[토스] 샴푸 1L (현대카드 12,900원/무료)"
    FakePageCollector.products = [p]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1} and bot.db.pending_items()[0].deal.product.price == 14900


def test_naver_benefit_prices_are_candidates() -> None:
    html = ('<script>window.__PRELOADED_STATE__={"product":{"A":{"productNo":"1","salePrice":20000,"discountedSalePrice":18000,'
            '"benefitsView":{"discountedSalePrice":18000,"couponDiscountedPrice":15500,"discountAmount":2000}}}};</script>')
    got = naver_state_prices(html, "https://smartstore.naver.com/a/products/1")
    assert got is not None and got.options[0].prices == (18000, 15500, 20000)  # 쿠폰가도 후보, 할인 '금액'은 가격 아님
    assert check_page_price("아무 상품", 15500, got).status == "ok"


# ---------------------------------------------------------------- 막힌 네이버 페이지는 크롬으로 (10/12 서버: 일반 요청 모두 429)
STATE_HTML = (FIX / "naver_store_state.html").read_text(encoding="utf-8")


WATERS = {"1": "제주 삼다수 2L 24개", "2": "백산수 2L 24개", "3": "아이시스 8.0 2L 24개", "4": "스파클 생수 2L 24개", "5": "평창수 2L 24개"}


def _naver(no: str = "11223344556", name: str | None = None, price: int = 14500, title: str | None = None) -> Product:
    url = f"https://smartstore.naver.com/water/products/{no}"
    return Product(source="ppomppu", product_id=f"naver:{no}", shop="naver", name=name or WATERS.get(no, NAME_22), price=price, url=url,
                   recommend_count=9, extra={"title": title} if title else {})


def _state_for(url: str) -> str:
    """그 상품번호의 스토어 페이지 (같은 가격·옵션)."""
    return STATE_HTML.replace("11223344556", url.rsplit("/", 1)[-1])


def _chrome(bot: DealBot, clock: list[float], *pages: Any) -> list[str]:
    """가짜 크롬: 차례대로 (코드, 최종 주소, HTML) · 주소를 받아 그걸 주는 함수 · 예외. 크롬 한 번 = 5초.
    크롬·페이지 읽기·기억이 같은 가짜 시계를 쓰고, 잠은 적기만 한다 (가격 대조는 기다리면 안 됨)."""
    sb = bot.store_browser
    opened: list[str] = []

    async def open_page(url: str) -> tuple[int, str, str]:
        opened.append(url)
        clock[0] += 5
        got = pages[min(len(opened), len(pages)) - 1]
        if isinstance(got, BaseException):
            raise got
        return got(url) if callable(got) else got

    async def sleep(s: float) -> None:
        bot.slept.append(s)  # type: ignore[attr-defined]
        clock[0] += s

    bot.slept = []  # type: ignore[attr-defined]
    sb._open_page = open_page  # type: ignore[method-assign]
    sb._sleep = sleep  # type: ignore[assignment]
    sb._clock = lambda: clock[0]  # type: ignore[assignment]
    sb._mem = lambda: 1500  # type: ignore[assignment]
    bot.page_reader._clock = lambda: clock[0]  # type: ignore[assignment]
    return opened


def _ok_page(url: str) -> tuple[int, str, str]:
    return 200, url, _state_for(url)


def _checks(bot: DealBot) -> dict[str, dict[str, Any]]:
    """수집할 때마다 상품별 마지막 가격 대조 결과 (p.extra['page_price'])."""
    out: dict[str, dict[str, Any]] = {}
    check = bot._check_page_price

    def record(p: Product, meta: PageMeta | None, **kw: Any) -> Any:
        got = check(p, meta, **kw)
        out[p.product_id] = dict(p.extra["page_price"])
        return got

    bot._check_page_price = record  # type: ignore[method-assign]
    return out


def test_only_naver_product_pages_go_to_the_browser() -> None:
    assert is_naver_product_page(NAVER_URL)
    assert is_naver_product_page("https://brand.naver.com/daekket/products/13008954661")
    assert is_naver_product_page("https://m.smartstore.naver.com/water/products/1")
    assert is_naver_product_page("https://shopping.naver.com/window-products/style/5512345678")
    for url in ("https://blog.naver.com/someone/223000000000", "https://smartstore.naver.com/water",  # 블로그·스토어 첫 화면
                "https://search.shopping.naver.com/catalog/1234567", "https://cafe.naver.com/x/1",  # 가격비교 카탈로그·카페
                TOSS_URL, "https://naver.me/abc", "", None):
        assert not is_naver_product_page(url), url


def test_naver_pages_that_are_never_read() -> None:
    """쇼핑이 아닌 네이버 주소(블로그·카페·포스트·로그인 등)는 읽지 않는다. 스토어·쇼핑·가격비교·쇼핑라이브와 다른 사이트는 읽음."""
    for url in ("https://blog.naver.com/someone/223000000000", "https://m.blog.naver.com/someone/1", "https://cafe.naver.com/x/1",
                "https://post.naver.com/viewer/1", "https://kin.naver.com/qna/1", "https://nid.naver.com/nidlogin.login",
                "https://www.naver.com/", "https://BLOG.Naver.com:443/a/1"):
        assert is_naver_off_shop(url), url
    for url in (NAVER_URL, "https://m.smartstore.naver.com/water/products/1", "https://brand.naver.com/daekket/products/1",
                "https://shopping.naver.com/window-products/style/1", "https://search.shopping.naver.com/catalog/1",
                "https://view.shoppinglive.naver.com/lives/1", "https://naver.me/abc", TOSS_URL, "https://notnaver.com/blog", "", None):
        assert not is_naver_off_shop(url), url


async def test_store_429_is_verified_with_the_browser(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    """일반 요청이 429 → 같은 페이지를 크롬으로 읽어 가격·옵션 대조 (위장 없는 사진용 크롬 그대로). 사진도 그 페이지에서."""
    asked: list[str] = []

    def store(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(429)

    bot.enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(store)), store_attempts=2)
    bot.enricher.fetch = types.MethodType(_REAL_FETCH, bot.enricher)  # type: ignore[method-assign]
    clock = [1000.0]

    async def sleep(s: float) -> None:  # 재시도 간격도 가짜 시계로 (실제로 안 잠)
        clock[0] += s

    bot.enricher._sleep = sleep  # type: ignore[assignment]
    bot.enricher._clock = lambda: clock[0]  # type: ignore[assignment]
    opened = _chrome(bot, clock, _ok_page)
    FakePageCollector.products = [_naver(price=14500)]
    with caplog.at_level(logging.INFO, logger="dealbot.app"):
        await bot.run_collector(bot.collectors[0])
    assert len(asked) == 2 and opened == [NAVER_URL]  # 일반 요청(재시도 포함)이 먼저, 막혀서 크롬 한 번
    assert bot.db.queue_counts() == {"pending": 1}
    deal = bot.db.pending_items()[0].deal
    assert deal.product.extra["page_price"]["status"] == "option" and deal.product.price == 14465
    assert deal.product.extra["page_option"] == "2L 24개" and deal.product.extra["title_price"] == 14500
    assert deal.product.image_url == "https://shop-phinf.pstatic.net/20261001_1/water_JPEG/1.jpg?type=o1000"
    assert any("page price verified" in r.getMessage() and "크롬으로 읽음" in r.getMessage() for r in caplog.records)
    assert not any("unverified" in r.getMessage() for r in caplog.records)


async def test_browser_page_holds_only_a_true_mismatch(bot: DealBot) -> None:
    """크롬으로 읽은 페이지로도 보류 규칙은 그대로: 어느 가격·옵션에도 안 맞을 때만 (±10%, 카드·쿠폰 제목은 25%까지 싼 것도)."""
    _serve(bot, {})
    _chrome(bot, [1000.0], _ok_page)
    bot.store_browser.min_interval = 0
    bot.page_reader.per_run = 9
    checks = _checks(bot)
    FakePageCollector.products = [
        _naver("1", price=6410),  # #22: 24개라는데 12개 옵션 값 → 보류
        _naver("2", price=8900),  # 맞는 가격·옵션 없음 → 보류
        _naver("3", price=13900),  # 24개 할인가 14,465 의 ±10% → 진행
        _naver("4", price=11200, title="[네이버] 스파클 생수 2L 24개 (현대카드 11,200원/무료)"),  # 카드가: 22.6% 싸도 진행
        _naver("5", name="평창수 2L", price=6390),  # 수량을 안 적은 제목 = 기본 옵션 값 → 진행
    ]
    await bot.run_collector(bot.collectors[0])
    got = {pid.split(":")[1]: c for pid, c in checks.items()}
    assert {n: c["status"] for n, c in got.items()} == {"1": "hold", "2": "hold", "3": "option", "4": "option", "5": "ok"}
    assert (got["1"]["reason"], got["2"]["reason"]) == ("quantity", "price")
    assert bot.db.queue_counts() == {"held": 2, "pending": 3}
    assert [kind for _, _, kind, _ in bot.sent] == ["price_hold", "price_hold"]  # type: ignore[attr-defined]


async def test_unverified_deal_is_never_held(bot: DealBot) -> None:
    """페이지를 읽었으면 보류됐을 딜(#22)도 크롬까지 못 읽으면 지금처럼 진행한다 (싸다는 이유만으로는 절대 보류 안 함)."""
    _serve(bot, {})
    _chrome(bot, [1000.0], (429, NAVER_URL, "<html><body>Too Many Requests</body></html>"))
    FakePageCollector.products = [_naver(price=6410)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1} and bot.sent == []  # type: ignore[attr-defined]
    deal = bot.db.pending_items()[0].deal
    check = deal.product.extra["page_price"]
    assert check["status"] == "unverified" and check["reason"] == "페이지를 못 읽음 · 크롬도 막힘 (HTTP 429)"
    assert deal.product.price == 6410  # 제목 가격 그대로


async def test_blocked_browser_cools_down_and_says_so_once(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    """크롬도 막히면(429·캡차·빈 페이지) 30분 크롬을 안 띄우고(또 막히면 60분), 로그는 쉬기 시작할 때 한 번.
    막힌 상품은 쉬는 시간보다 길게(60분) 기다려, 쉬는 시간이 끝나면 다른 상품부터 연다 (한 상품이 쉬는 시간을 계속 잇지 않게)."""
    clock = [1000.0]
    _serve(bot, {})
    captcha = (200, "https://ncpt.naver.com/captcha?x=1", "<html><title>보안 확인</title></html>")
    opened = _chrome(bot, clock, (429, NAVER_URL, ""), captcha, _ok_page)
    bot.page_reader.per_run = 5
    checks = _checks(bot)
    FakePageCollector.products = [_naver("1"), _naver("2")]
    with caplog.at_level(logging.INFO):
        await bot.run_collector(bot.collectors[0])
        assert len(opened) == 1  # 둘째 상품은 띄우지 않음 (쉬는 중)
        assert checks["naver:1"]["reason"] == "페이지를 못 읽음 · 크롬도 막힘 (HTTP 429)"
        assert checks["naver:2"]["reason"] == "페이지를 못 읽음 · 크롬도 막혀 쉬는 중 (30분 남음)"
        clock[0] += 20 * 60
        await bot.run_collector(bot.collectors[0])  # 20분 뒤: 아직 쉼
        assert len(opened) == 1
        clock[0] += 11 * 60
        await bot.run_collector(bot.collectors[0])  # 30분 지나 한 번 (막혔던 1 이 아니라 2) → 캡차 화면 → 이번엔 60분
        assert len(opened) == 2 and checks["naver:2"]["reason"] == "페이지를 못 읽음 · 크롬도 막힘 (캡차)"
        assert checks["naver:1"]["reason"] == "페이지를 못 읽음 · 크롬으로 못 읽은 지 얼마 안 됨 (29분 뒤 다시)"
        clock[0] += 31 * 60
        await bot.run_collector(bot.collectors[0])
        assert len(opened) == 2  # 30분으로는 아직
        clock[0] += 30 * 60
        await bot.run_collector(bot.collectors[0])
    assert len(opened) == 3 and checks["naver:1"]["status"] == "option"  # 풀린 뒤 읽음
    blocked = [r.getMessage() for r in caplog.records if r.name == "dealbot.store_browser" and r.levelno == logging.WARNING]
    assert len(blocked) == 2 and "HTTP 429" in blocked[0] and "30분" in blocked[0] and "캡차" in blocked[1] and "60분" in blocked[1]
    assert bot.db.queue_counts() == {"pending": 2}  # 막혀 있는 동안에도 보류 없이 진행


async def test_empty_browser_page_counts_as_blocked(bot: DealBot) -> None:
    """응답은 200 인데 상품 가격도 상품 사진도 없는 빈 페이지 = 막힘 (쉼). 사진은 있는데 가격만 없으면 막힌 게 아니라 '가격 없음'."""
    clock = [1000.0]
    _serve(bot, {})
    no_price = '<html><head><meta property="og:image" content="https://shop-phinf.pstatic.net/x/1.jpg"></head></html>'
    opened = _chrome(bot, clock, (200, NAVER_URL, "<html><body><div id='root'></div></body></html>"),
                     lambda url: (200, url, no_price))
    checks = _checks(bot)
    FakePageCollector.products = [_naver()]
    await bot.run_collector(bot.collectors[0])
    assert checks["naver:11223344556"]["reason"] == "페이지를 못 읽음 · 크롬도 막힘 (빈 페이지)" and bot.store_browser.cooling()
    clock[0] += 61 * 60  # 막힌 상품은 쉬는 시간(30분)보다 길게 60분 뒤에 다시
    await bot.run_collector(bot.collectors[0])
    assert len(opened) == 2 and not bot.store_browser.cooling()
    assert checks["naver:11223344556"] == {**checks["naver:11223344556"], "status": "unverified", "reason": "페이지에 가격 없음"}


async def test_cached_page_skips_the_plain_read_and_the_browser(bot: DealBot) -> None:
    clock = [1000.0]
    seen = _serve(bot, {})
    opened = _chrome(bot, clock, _ok_page)
    checks = _checks(bot)
    FakePageCollector.products = [_naver(price=14500)]
    await bot.run_collector(bot.collectors[0])
    assert seen == [NAVER_URL] and opened == [NAVER_URL]
    for _ in range(3):  # 5분마다 다시 수집해도 기억해 둔 가격·옵션으로 (일반 요청도 크롬도 안 함)
        clock[0] += 5 * 60
        checks.clear()
        await bot.run_collector(bot.collectors[0])
        assert checks["naver:11223344556"]["status"] == "option"
    assert seen == [NAVER_URL] and opened == [NAVER_URL]
    clock[0] += bot.settings.deal.page_check.cache_minutes * 60  # 기억 시간(cache_minutes)이 지나면 다시 읽음
    await bot.run_collector(bot.collectors[0])
    assert seen == [NAVER_URL] * 2 and opened == [NAVER_URL] * 2


async def test_unverified_is_logged_once_per_product_per_day(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    clock = [1000.0]
    _serve(bot, {})
    bot.page_reader._clock = lambda: clock[0]  # type: ignore[assignment]
    FakePageCollector.products = [_toss(), _toss(pid="9001", name="스파클 탄산수 500ml 20병", price=5900)]
    with caplog.at_level(logging.INFO, logger="dealbot.app"):
        for _ in range(6):  # 30분 동안 5분마다
            await bot.run_collector(bot.collectors[0])
            clock[0] += 5 * 60
        lines = [r.getMessage() for r in caplog.records if "page price unverified" in r.getMessage()]
        assert len(lines) == 2 and all(line.endswith(": 페이지를 못 읽음") for line in lines)  # 상품마다 한 번, 이유와 함께
        clock[0] += 24 * 3600
        await bot.run_collector(bot.collectors[0])
    assert sum("page price unverified" in r.getMessage() for r in caplog.records) == 4  # 하루 뒤 다시 한 번씩


async def test_browser_reads_are_bounded_per_run_and_per_hour_without_waiting(bot: DealBot) -> None:
    """수집이 오래 멈추지 않게: 한 판에 browser_per_run 번, 한 시간에 browser_per_hour 번. 크롬 열기 간격 안이면 기다리지 않고
    다음 판에. 한도에 걸린 딜은 확인 못 함으로 진행하고 다음 판에 다시 본다."""
    clock = [1000.0]
    _serve(bot, {})
    opened = _chrome(bot, clock, _ok_page)
    checks = _checks(bot)
    bot.page_reader.per_run, bot.page_reader.per_hour = 2, 3
    assert bot.store_browser.min_interval == 60  # config.yaml
    FakePageCollector.products = [_naver("1"), _naver("2"), _naver("3"), _naver("4")]
    await bot.run_collector(bot.collectors[0])
    assert [u.rsplit("/", 1)[-1] for u in opened] == ["1"] and bot.slept == []  # type: ignore[attr-defined]
    assert checks["naver:2"]["reason"] == "페이지를 못 읽음 · 크롬 다른 페이지를 읽는 중이거나 열기 간격 안"
    clock[0] += 5 * 60
    bot.store_browser.min_interval = 0
    await bot.run_collector(bot.collectors[0])  # 1 은 기억, 2·3 을 읽고 4 는 이번 판 한도
    assert [u.rsplit("/", 1)[-1] for u in opened] == ["1", "2", "3"]
    assert checks["naver:4"]["reason"] == "페이지를 못 읽음 · 이번 판 크롬 한도"
    clock[0] += 5 * 60
    await bot.run_collector(bot.collectors[0])  # 한 시간에 3번을 다 씀
    assert len(opened) == 3 and checks["naver:4"]["reason"] == "페이지를 못 읽음 · 한 시간 크롬 한도 (3번)"
    clock[0] += 3600
    await bot.run_collector(bot.collectors[0])
    assert len(opened) == 4 and checks["naver:4"]["status"] == "option"
    assert bot.db.queue_counts() == {"pending": 4} and bot.slept == []  # type: ignore[attr-defined]


async def test_browser_read_is_cut_off_and_that_product_waits(bot: DealBot) -> None:
    """크롬 한 번 읽기에는 시간 제한: 넘으면 끊고(확인 못 함으로 진행), 그 상품은 쉬는 시간 뒤에 다시 (한도를 매번 먹지 않게)."""
    clock = [1000.0]
    _serve(bot, {})
    _chrome(bot, clock, _ok_page)
    calls: list[str] = []

    async def first_hangs(url: str) -> tuple[int, str, str]:
        calls.append(url)
        if len(calls) == 1:
            await asyncio.Event().wait()  # 멈춘 크롬
        return _ok_page(url)

    bot.store_browser._open_page = first_hangs  # type: ignore[method-assign]
    bot.store_browser.min_interval = 0
    bot.page_reader.timeout = 0.05
    checks = _checks(bot)
    FakePageCollector.products = [_naver()]
    await bot.run_collector(bot.collectors[0])
    assert checks["naver:11223344556"]["reason"] == "페이지를 못 읽음 · 크롬 실패 (TimeoutError)"
    assert bot.db.queue_counts() == {"pending": 1} and not bot.store_browser.cooling()  # 막힌 게 아니라 이번만 실패
    clock[0] += 5 * 60
    await bot.run_collector(bot.collectors[0])
    assert len(calls) == 1 and checks["naver:11223344556"]["reason"] == "페이지를 못 읽음 · 크롬으로 못 읽은 지 얼마 안 됨 (25분 뒤 다시)"
    clock[0] += 30 * 60
    await bot.run_collector(bot.collectors[0])
    assert len(calls) == 2 and checks["naver:11223344556"]["status"] == "option"


async def test_gone_or_redirected_pages_are_not_used(bot: DealBot) -> None:
    """일반 요청이 '없는 페이지(404)' 면 크롬을 띄우지 않고, 크롬이 다른 곳(스토어 첫 화면)으로 넘어가면 그 값으로 보류하지 않는다."""
    _serve(bot, {})
    bot.enricher._note_failed("https://smartstore.naver.com/water/products/1", 404)
    opened = _chrome(bot, [1000.0], (200, "https://smartstore.naver.com/water", _state_for(NAVER_URL)))
    bot.page_reader.per_run = 5
    checks = _checks(bot)
    FakePageCollector.products = [_naver("1"), _naver(price=6410)]
    await bot.run_collector(bot.collectors[0])
    assert opened == [NAVER_URL]
    assert checks["naver:1"]["reason"] == "페이지를 못 읽음 (HTTP 404)"
    assert checks["naver:11223344556"]["reason"] == "페이지를 못 읽음 · 크롬 다른 페이지로 넘어감"
    assert bot.db.queue_counts() == {"pending": 2}


async def test_browser_price_check_off_keeps_the_old_way(bot: DealBot) -> None:
    bot.page_reader.browser = False
    _serve(bot, {})
    opened = _chrome(bot, [1000.0], _ok_page)
    FakePageCollector.products = [_naver(price=6410)]
    await bot.run_collector(bot.collectors[0])
    assert opened == [] and bot.db.queue_counts() == {"pending": 1}
    assert bot.db.pending_items()[0].deal.product.extra["page_price"]["reason"] == "페이지를 못 읽음"


def _price_drop(url: str) -> str:
    """가게가 24개 옵션을 9,900원으로 내린 페이지 (12개 6,390 + 차액 3,510)."""
    return _state_for(url).replace('"price":8075', '"price":3510')


async def test_remembered_page_never_holds_a_new_title_price(bot: DealBot) -> None:
    """기억해 둔 페이지(cache_minutes)는 맞음 확인에만: 그사이 가게가 값을 내리고 새 글이 새 가격을 적으면 옛 페이지로 보류하지
    않는다. 새로 읽을 수 없으면 확인 못 함으로 진행하고, 읽을 수 있으면 지금 페이지(일반 요청·크롬이 기억해 둔 것도 버림)로 대조."""
    clock = [1000.0]
    _serve(bot, {})
    page = {"html": _state_for}
    opened = _chrome(bot, clock, lambda url: (200, url, page["html"](url)))
    bot.store_browser.min_interval = 0
    checks = _checks(bot)
    FakePageCollector.products = [_naver(price=14500)]
    await bot.run_collector(bot.collectors[0])  # 24개 14,465 에 맞음 (크롬)
    assert checks["naver:11223344556"]["status"] == "option" and len(opened) == 1

    page["html"] = _price_drop  # 10분 뒤 타임세일로 24개 9,900원, 새 글도 9,900원
    clock[0] += 10 * 60
    FakePageCollector.products = [_naver(price=9900)]
    max_per_run = bot.settings.deal.enrich.max_per_run
    bot.settings.deal.enrich.max_per_run = 0  # 이번 판 페이지 읽기 한도: 기억해 둔 페이지만 볼 수 있음
    await bot.run_collector(bot.collectors[0])
    assert checks["naver:11223344556"] == {**checks["naver:11223344556"], "status": "unverified",
                                           "reason": "이번 판 페이지 읽기 한도 (옛 페이지와 안 맞는 새 가격)"}
    bot.settings.deal.enrich.max_per_run = max_per_run
    clock[0] += 5 * 60
    bot.page_reader.per_run = 0  # 크롬 한도: 새로 못 읽음
    await bot.run_collector(bot.collectors[0])
    assert checks["naver:11223344556"]["reason"] == "페이지를 못 읽음 · 이번 판 크롬 한도 (옛 페이지와 안 맞는 새 가격)"
    assert len(opened) == 1 and bot.store_browser.cached(NAVER_URL) is None  # 크롬이 기억해 둔 옛 페이지도 버림
    assert bot.db.queue_counts() == {"pending": 1} and bot.sent == []  # type: ignore[attr-defined]  # 보류도 보류 메모도 없음

    clock[0] += 5 * 60
    bot.page_reader.per_run = 1
    await bot.run_collector(bot.collectors[0])  # 새로 읽은 페이지로: 9,900 = 24개 옵션
    assert len(opened) == 2 and checks["naver:11223344556"]["status"] == "option"
    assert checks["naver:11223344556"]["matched_price"] == 9900
    assert bot.db.queue_counts() == {"pending": 1} and bot.sent == []  # type: ignore[attr-defined]


async def test_remembered_page_repeats_the_hold_for_the_same_title_price(bot: DealBot) -> None:
    """같은 글(같은 제목 가격)을 5분마다 다시 수집하면 기억해 둔 페이지로 같은 판정 (다시 읽지 않고, 보류 메모도 한 번)."""
    clock = [1000.0]
    seen = _serve(bot, {})
    opened = _chrome(bot, clock, _ok_page)
    checks = _checks(bot)
    FakePageCollector.products = [_naver(price=6410)]  # #22: 24개라는데 12개 값
    for _ in range(3):
        await bot.run_collector(bot.collectors[0])
        assert checks["naver:11223344556"]["status"] == "hold"
        clock[0] += 5 * 60
    assert seen == [NAVER_URL] and opened == [NAVER_URL]
    assert bot.db.queue_counts() == {"held": 1} and [k for _, _, k, _ in bot.sent] == ["price_hold"]  # type: ignore[attr-defined]


async def test_remembered_page_holds_only_the_post_it_held(bot: DealBot) -> None:
    """기억해 둔 페이지는 새로 읽었을 때 보류로 나온 글만 되풀이한다. 같은 제목 가격이라도 그때 맞았던 글(카드 말이 있어 25% 까지)과
    말이 다른 글은 옛 페이지로 보류하지 않고 새로 읽는다 (그사이 가게가 그 값으로 내렸을 수도 있다)."""
    clock = [1000.0]
    _serve(bot, {})
    opened = _chrome(bot, clock, _ok_page)
    bot.store_browser.min_interval = 0
    checks = _checks(bot)
    FakePageCollector.products = [_naver(price=11200, title="[네이버] 풀무원 샘물 2L 24개 (현대카드 11,200원/무료)")]
    await bot.run_collector(bot.collectors[0])  # 24개 14,465 보다 22.6% 싸지만 카드가라 맞음
    assert checks["naver:11223344556"]["status"] == "option" and len(opened) == 1

    clock[0] += 10 * 60
    bot.page_reader.per_run = 0  # 크롬 한도: 새로 못 읽음
    FakePageCollector.products = [_naver(price=11200, title="[네이버] 풀무원 샘물 2L 24개 (11,200원/무료)")]
    await bot.run_collector(bot.collectors[0])
    assert checks["naver:11223344556"] == {**checks["naver:11223344556"], "status": "unverified",
                                           "reason": "페이지를 못 읽음 · 이번 판 크롬 한도 (옛 페이지와 안 맞는 새 가격)"}
    assert len(opened) == 1 and bot.sent == []  # type: ignore[attr-defined]

    clock[0] += 5 * 60
    bot.page_reader.per_run = 1
    await bot.run_collector(bot.collectors[0])  # 지금 페이지를 새로 읽고서야 보류 (카드 말 없는 22.6% 차이)
    assert len(opened) == 2 and checks["naver:11223344556"]["status"] == "hold"
    clock[0] += 5 * 60
    await bot.run_collector(bot.collectors[0])  # 새로 읽어 보류로 나온 글이니 이제는 기억해 둔 페이지로 되풀이
    assert len(opened) == 2 and checks["naver:11223344556"]["status"] == "hold"


async def test_browser_kept_page_only_confirms(bot: DealBot) -> None:
    """크롬이 발행 사진용으로 읽어 둔(30분) 페이지도 같다: 제목 가격이 맞으면 다시 안 열고, 보류가 될 값이면 지금 페이지를 새로 읽음."""
    clock = [1000.0]
    _serve(bot, {})
    page = {"html": _state_for}
    opened = _chrome(bot, clock, lambda url: (200, url, page["html"](url)))
    bot.store_browser.min_interval = 0
    bot.page_reader.per_run = 0  # 맞음 확인은 한도와 상관없이 (크롬을 안 띄움)
    url2 = "https://smartstore.naver.com/water/products/2"
    assert await bot.store_browser.page_meta(NAVER_URL) is not None and await bot.store_browser.page_meta(url2) is not None
    checks = _checks(bot)
    page["html"] = _price_drop
    FakePageCollector.products = [_naver("2", price=14500)]
    await bot.run_collector(bot.collectors[0])
    assert len(opened) == 2 and checks["naver:2"]["status"] == "option"  # 크롬이 기억해 둔 페이지로 맞음
    bot.page_reader.per_run = 1
    FakePageCollector.products = [_naver(price=9900)]
    await bot.run_collector(bot.collectors[0])
    assert len(opened) == 3 and checks["naver:11223344556"]["status"] == "option"  # 옛 페이지면 보류 → 새로 읽어 맞음
    assert bot.db.queue_counts() == {"pending": 2}


async def test_browser_sent_elsewhere_fails_only_that_product(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    """크롬이 로그인(19세 상품)·스토어 첫 화면 같은 다른 곳으로 넘어가면 그 상품만 못 읽은 것: 크롬 전체를 쉬지 않고(사진용도 그대로),
    그 상품은 30분·60분… 뒤에 다시. 그사이 다른 상품은 크롬으로 읽는다."""
    clock = [1000.0]
    _serve(bot, {})
    login = '<html><head><title>네이버 : 로그인</title><meta property="og:image" content="https://static.nid.naver.com/login.png"></head></html>'

    def page(url: str) -> tuple[int, str, str]:
        if url.endswith("/1"):
            return 200, "https://nid.naver.com/nidlogin.login?mode=form&url=" + url, login
        return _ok_page(url)

    opened = _chrome(bot, clock, page)
    bot.store_browser.min_interval = 0
    checks = _checks(bot)
    FakePageCollector.products = [_naver("1"), _naver("2")]
    with caplog.at_level(logging.INFO):
        await bot.run_collector(bot.collectors[0])
        assert checks["naver:1"]["reason"] == "페이지를 못 읽음 · 크롬 다른 페이지로 넘어감" and not bot.store_browser.cooling()
        assert checks["naver:2"]["reason"] == "페이지를 못 읽음 · 이번 판 크롬 한도"
        clock[0] += 5 * 60
        await bot.run_collector(bot.collectors[0])  # 1 은 쉬고 2 를 읽음
        assert checks["naver:2"]["status"] == "option"
        assert checks["naver:1"]["reason"] == "페이지를 못 읽음 · 크롬으로 못 읽은 지 얼마 안 됨 (25분 뒤 다시)"
        clock[0] += 31 * 60
        await bot.run_collector(bot.collectors[0])  # 30분 뒤 1 을 한 번 더 → 또 넘어감 → 이번엔 60분
        clock[0] += 31 * 60
        await bot.run_collector(bot.collectors[0])
        assert checks["naver:1"]["reason"] == "페이지를 못 읽음 · 크롬으로 못 읽은 지 얼마 안 됨 (29분 뒤 다시)"
    assert [u.rsplit("/", 1)[-1] for u in opened] == ["1", "2", "1"] and not bot.store_browser.cooling()
    assert not any(r.name == "dealbot.store_browser" and r.levelno == logging.WARNING for r in caplog.records)  # '크롬도 막힘' 없음
    assert bot.db.queue_counts() == {"pending": 2}


def test_browser_retry_backs_off_per_product_and_stays_bounded() -> None:
    """크롬으로 못 읽은 상품의 다시 읽기: 30분 → 60분 → … 최대 6시간. 막힌 상품은 처음부터 60분. 기억하는 상품 수는 500개까지."""
    reader = PageReader(retry_seconds=1800, retry_max_seconds=6 * 3600)
    reader._clock = lambda: 0.0  # type: ignore[assignment]
    waits = []
    for _ in range(6):
        reader._failed("a", 0.0, blocked=False)
        waits.append(reader._retry["a"][0] / 60)
    assert waits == [30, 60, 120, 240, 360, 360]
    reader._failed("b", 0.0, blocked=True)
    assert reader._retry["b"][0] / 60 == 60
    for i in range(1000):
        reader._failed(f"x{i}", float(i), blocked=False)
    assert len(reader._retry) <= 500 and "x999" in reader._retry


async def test_naver_blog_or_cafe_is_never_read(bot: DealBot) -> None:
    """'[네이버]' 글의 첫 naver.com 링크가 블로그 후기면: 가격 대조·사진·품절 확인 모두 그 글을 읽지 않는다 (일반 요청도 크롬도).
    딜은 확인 못 함으로 지금처럼 진행."""
    asked: list[str] = []

    def anything(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(200, text=_state_for(str(req.url)))

    bot.enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(anything)))
    bot.enricher.fetch = types.MethodType(_REAL_FETCH, bot.enricher)  # type: ignore[method-assign]
    opened = _chrome(bot, [1000.0], _ok_page)
    checks = _checks(bot)
    blog = "https://blog.naver.com/someone/223000000000"
    FakePageCollector.products = [Product(source="ppomppu", product_id="naver:blogpost", shop="naver", name="삼다수 2L 24개",
                                          price=14500, url=blog, recommend_count=9, extra={})]
    await bot.run_collector(bot.collectors[0])
    assert asked == [] and opened == []
    assert checks["naver:blogpost"]["reason"] == "네이버 쇼핑 페이지가 아님 (블로그·카페 등은 안 읽음)"
    for url in (blog, "https://m.blog.naver.com/someone/1", "https://cafe.naver.com/x/1", "https://post.naver.com/viewer/1"):
        assert await bot.enricher.fetch(url) is None and await bot.enricher.check_available(url) is None
        assert (await bot.store_browser.read(url)).why == "네이버 상품 페이지 아님"
    assert asked == [] and opened == []


@pytest.mark.real_fetch
async def test_naver_short_link_stops_before_a_blog() -> None:
    """naver.me 짧은 주소는 한 단계씩 따라가다 블로그·카페로 넘어가면 거기서 멈춘다. 스토어로 넘어가면 그대로 읽음."""
    asked: list[str] = []
    hops = {"https://naver.me/blog": "https://blog.naver.com/someone/223000000000", "https://naver.me/store": NAVER_URL}

    def serve(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        if str(req.url) in hops:
            return httpx.Response(302, headers={"location": hops[str(req.url)]})
        return httpx.Response(200, text=STATE_HTML)

    enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(serve)))
    assert await enricher.fetch("https://naver.me/blog") is None and asked == ["https://naver.me/blog"]
    meta = await enricher.fetch("https://naver.me/store")
    assert meta is not None and meta.final_url == NAVER_URL and meta.prices is not None
    assert asked == ["https://naver.me/blog", "https://naver.me/store", NAVER_URL]


@pytest.mark.parametrize(
    "bad",
    [{"browser_timeout_seconds": 0}, {"browser_per_run": -1}, {"browser_per_hour": -1}, {"browser_cooldown_minutes": -5},
     {"cache_minutes": -1}, {"unverified_log_hours": -1}],
)
def test_page_check_browser_settings_are_bounded(bad: dict[str, float]) -> None:
    """크롬 시간 제한 0(예전엔 몰래 75초가 됨)·음수 한도·음수 시간은 설정을 읽을 때 거절."""
    with pytest.raises(ValidationError):
        PageCheckConfig(**bad)
    assert PageCheckConfig(browser_per_run=0, cache_minutes=0, browser_cooldown_minutes=0).browser_per_run == 0  # 0 = 안 씀은 그대로
