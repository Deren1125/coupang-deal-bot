"""상품 페이지 가격 대조 (주인 결정 10/12, 큐 #22).

#22: 뽐뿌 '[토스] 풀무원 생수 2L 24개 (6,410원/무료)' → 실제 페이지는 24개 14,465원 · 12개 6,390원.
제목 가격만 믿고 쿠팡 24병 31,900원과 비교해 '79.9% 싸다'며 링크 요청까지 갔다. 이제는 페이지 가격·옵션과 맞춰 보고
안 맞으면 보류(held) + 관리자 챗 한 줄, 링크 요청·발행 없음. 페이지를 못 읽으면 지금처럼 진행(확인 못 함).
테스트는 가짜만 쓴다 (네트워크 없음).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.enrich import PageMeta, page_meta_at
from dealbot.models import Deal, Product
from dealbot.page_price import (
    PageOption,
    PagePrices,
    check_page_price,
    hold_line,
    naver_state_prices,
    quantity_conflict,
)
from dealbot.pricing.market import MarketQuote

FIX = Path(__file__).parent / "fixtures"
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

    _serve(bot, {})  # 3판: 다시 막힘 — 같은 제목 가격이면 또 줄 세우지 않음
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
