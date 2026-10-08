"""시중가 대조: 다른 수량·묶음 상품을 '쿠팡 최저가'로 잡지 않는지 (쿠팡보다 N%↓ 부풀림 방지)."""

from __future__ import annotations

import pytest

from dealbot.config import DealConfig, InterestConfig, MarketCheckConfig, Settings
from dealbot.models import Deal, PriceStats, Product, PublishResult
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import (
    CoupangMarketReference,
    build_keyword,
    match_ratio,
    normalize_qty,
    parse_quantity,
)

WATER = "스파클 생수 무라벨, 500ml, 40병"
HOLLYS = "할리스 바닐라 딜라이트 로우슈거, 24개"


@pytest.mark.parametrize(
    ("deal", "candidate"),
    [
        (WATER, "스파클 무라벨 생수 500ml 40병 x 2팩"),  # 80병
        (HOLLYS, "할리스 바닐라 딜라이트 로우슈거, 285ml, 24개, 2박스"),  # 48개
        ("곰곰 무항생제 닭가슴살 1kg", "곰곰 무항생제 닭가슴살 1kg, 2개"),
        ("다우니 섬유유연제 실내건조 4L", "다우니 섬유유연제 실내건조 4L 1+1"),
        ("펩시 제로 500ml 12병", "펩시 제로 1,500ml 12병"),
        ("코카콜라 제로 1,000ml 12병", "코카콜라 제로 2,000ml 12병"),
        (WATER, "스파클 생수 무라벨 500ml 40병 + 2L 12병"),  # 다른 상품을 끼운 묶음
        ("삼다수 2L 6개", "삼다수 2L 6개 x 2"),
        ("삼다수 2L 6개", "삼다수 2L 6개입 x 6개"),  # 36병 (같은 숫자여도 x 뒤는 곱수)
        ("제주 삼다수 2L 6개", "제주 삼다수 2L, 6개입, 6개"),  # 쿠팡식 '6개입 묶음 6개' = 36병
        ("다우니 섬유유연제 실내건조 4L 1+1 (총 2개)", "다우니 섬유유연제 실내건조 4L, 4개"),  # 총 2개에 1+1 을 또 곱하지 않음
        ("곰곰 무항생제 닭가슴살 1kg", "곰곰 무항생제 닭가슴살 1kg*2"),
        ("곰곰 무항생제 닭가슴살 1kg", "곰곰 무항생제 닭가슴살 1kg×2"),
        ("곰곰 무항생제 닭가슴살 1kg", "곰곰 무항생제 닭가슴살 1kgx2"),
        ("펩시 제로 355ml 24캔+24캔", "펩시 제로 355ml, 24개"),  # 48캔
        ("삼다수 2L 12병", "삼다수 2L 12병 + 24캔"),  # 단위가 다른 '+' 는 다른 상품을 끼운 묶음
    ],
)
def test_bundles_and_other_sizes_are_not_the_same_product(deal: str, candidate: str) -> None:
    assert match_ratio(deal, candidate) == 0.0


@pytest.mark.parametrize(
    ("deal", "candidate"),
    [
        (WATER, "스파클 생수 무라벨, 500ml, 40개"),  # 병 = 개 (쿠팡 표준 이름)
        (HOLLYS, "할리스 바닐라 딜라이트 로우슈거, 285ml, 24개"),  # 딜에 없는 용량은 덧붙어도 같은 상품
        ("곰곰 무항생제 닭가슴살 1kg", "곰곰 무항생제 닭가슴살 1kg, 1개"),
        ("다우니 섬유유연제 실내건조 4L 1+1", "다우니 섬유유연제 실내건조 4L, 2개"),
        ("코카콜라 제로 1,000ml 12병", "코카콜라 제로 1L, 12개"),
        ("제주 삼다수 2Lx6", "제주 삼다수 2L, 6개"),
        ("곰곰 물티슈 100매 10팩", "곰곰 물티슈 100매, 10개"),
        ("애슐리 크리스피 핫도그 8개입 2세트", "애슐리 크리스피 핫도그, 80g, 8개입, 2세트"),
        ("다우니 섬유유연제 실내건조 4L 1+1 (총 2개)", "다우니 섬유유연제 실내건조 4L, 2개"),
        ("다우니 섬유유연제 실내건조 4L 1+1 (2개)", "다우니 섬유유연제 실내건조 4L, 2개"),  # (2개)는 1+1 을 풀어 쓴 것
        ("곰곰 무항생제 닭가슴살 1kg*2", "곰곰 무항생제 닭가슴살 1kg, 2개"),
        ("곰곰 무항생제 닭가슴살 1kg×2", "곰곰 무항생제 닭가슴살 1kg, 2개"),
        ("곰곰 무항생제 닭가슴살 1kgx2", "곰곰 무항생제 닭가슴살 1kg, 2개"),
        ("제주 삼다수 2L 6개입 6개", "제주 삼다수 2L, 6개입, 6개"),
        # 괄호 안에 풀어 쓴 수량·총용량은 한 번만: 12병 (6병x2) = 12병, (총 20L) 은 한 병 용량이 아님
        ("제주 삼다수 2L 12병 (6병x2)", "제주 삼다수 2L, 12개"),
        ("스파클 생수 500ml, 40개 (총 20L)", "스파클 생수 무라벨, 500ml, 40개"),
        ("코카콜라 제로 500ml 24개입 (24개)", "코카콜라 제로 500ml, 24개입, 1개"),
        # 같은 단위끼리 더한 수량, 이어 쓴 1+1+1
        ("펩시 제로 355ml 24캔+24캔", "펩시 제로 355ml, 48개"),
        ("햇반 210g 12+12", "햇반 210g, 24개"),
        ("비비고 왕교자 1.05kg 1+1+1", "비비고 왕교자 1.05kg, 3개"),
    ],
)
def test_same_quantity_written_differently_still_matches(deal: str, candidate: str) -> None:
    assert match_ratio(deal, candidate) == 1.0


def test_thousands_separator_is_read_as_one_number() -> None:
    assert normalize_qty("펩시 제로 1,500ml 12병") == {"1500ml", "12병"}
    assert build_keyword("코카콜라 제로 1,000ml 12병") == "코카콜라 제로 1000ml"
    q = parse_quantity("코카콜라 제로 1,000ml 12병")
    assert q.sizes == {"ml": frozenset({1000.0})} and q.count == 12
    # 단위가 아닌 숫자는 개수로 안 읽음
    assert parse_quantity("비타민C 3개월분").count == 1
    assert parse_quantity("삼성 USB 128GB").sizes == {}
    # 카드 할인 '10+5%' 는 묶음도 1+1 도 아님. 가로x세로 치수도 곱수가 아님
    q = parse_quantity("[카드 10+5%] 삼다수 2L 6개")
    assert q.count == 6 and not q.bundle
    assert parse_quantity("러그 120cmx180cm").count == 1
    assert parse_quantity("1리터x2").count == 2 and parse_quantity("40병 (40개)").count == 40


def _cand(pid: int, name: str, price: int, category: str | None = None) -> Product:
    return Product(source="market", product_id=f"coupang:{pid}", shop="coupang", name=name, price=price, url=f"https://c/{pid}",
                   category=category)


def _deal(name: str, price: int) -> Product:
    return Product(source="ppomppu", product_id="toss:1", shop="toss", name=name, price=price, url="u", shipping="무료")


def test_pick_best_reads_totals_and_kg_multipliers() -> None:
    ref = CoupangMarketReference(None, MarketCheckConfig())  # type: ignore[arg-type]
    ev = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False)))
    # '1+1 (총 2개)' 는 2병 → 2병 상품(19,800)과 비교해 19.7% → 기준 미달 (예전엔 4병 상품과 비교해 59.8%↓ 로 통과)
    downy = "다우니 섬유유연제 실내건조 4L 1+1 (총 2개)"
    deal = _deal(downy, 15900)
    q = ref.pick_best(deal, [_cand(1, "다우니 섬유유연제 실내건조 4L, 2개", 19800), _cand(2, "다우니 섬유유연제 실내건조 4L, 4개", 39600)])
    assert q is not None and q.price == 19800
    v = ev.evaluate(deal, PriceStats(), q, market_available=True)
    assert not v.is_deal and v.below_market_pct == 19.7 and v.reasons == ["below_coupang_price<20%"]
    # 1kg 딜을 '1kg*2' 묶음과 비교하지 않음
    chicken = [_cand(1, "곰곰 무항생제 닭가슴살 1kg*2", 19800)]
    assert ref.pick_best(_deal("곰곰 무항생제 닭가슴살 1kg", 9900), chicken) is None
    # '1kg*2' 딜은 '1kg, 2개'와 비교 (1kg 1개짜리 9,900원과 비교해 '쿠팡보다 비쌈'으로 떨어뜨리지 않음)
    q = ref.pick_best(_deal("곰곰 무항생제 닭가슴살 1kg*2", 15900),
                      [_cand(1, "곰곰 무항생제 닭가슴살 1kg, 2개", 19800), _cand(2, "곰곰 무항생제 닭가슴살 1kg, 1개", 9900)])
    assert q is not None and q.price == 19800
    # '6개입, 6개'(36병)는 6병 딜과 다른 상품
    assert ref.pick_best(_deal("제주 삼다수 2L 6개", 4900), [_cand(1, "제주 삼다수 2L, 6개입, 6개", 29000)]) is None


def test_pick_best_keeps_the_coupang_category() -> None:
    ref = CoupangMarketReference(None, MarketCheckConfig())  # type: ignore[arg-type]
    q = ref.pick_best(_deal("스파클 생수 무라벨, 500ml, 40병", 5900), [_cand(2, "스파클 생수 무라벨, 500ml, 40개", 10900, "식품")])
    assert q is not None and q.category == "식품"


def test_pick_best_uses_same_size_listing_not_the_bundle() -> None:
    ref = CoupangMarketReference(None, MarketCheckConfig())  # type: ignore[arg-type]
    deal = Product(source="ppomppu", product_id="toss:1", shop="toss", name=WATER, price=5900, url="u")
    candidates = [
        _cand(1, "스파클 무라벨 생수 500ml 40병 x 2팩", 16900),
        _cand(2, "스파클 생수 무라벨, 500ml, 40개", 10900),
        _cand(3, "스파클 생수 무라벨 500ml 20개", 6000),
    ]
    q = ref.pick_best(deal, candidates)
    assert q is not None and q.price == 10900 and q.title == "스파클 생수 무라벨, 500ml, 40개"
    # 묶음 상품만 있으면 대조 결과 없음 (부풀린 % 를 만들지 않음)
    assert ref.pick_best(deal, [candidates[0]]) is None

    v = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False))).evaluate(deal, PriceStats(), q, market_available=True)
    assert v.is_deal and v.below_market_pct == 45.9  # 65%↓ 가 아니라 같은 40병 대비 46%
    assert v.market_title == "스파클 생수 무라벨, 500ml, 40개" and v.market_price == 10900


async def test_admin_preview_shows_the_compared_coupang_listing(settings: Settings) -> None:
    from dealbot.monitoring.admin import AdminNotifier
    from dealbot.publisher.templates import TemplateRenderer

    sent: list[str] = []

    class FakeBot:
        async def send_message(self, **kw):  # type: ignore[no-untyped-def]
            sent.append(kw["text"])

    renderer = TemplateRenderer(settings.templates_dir, settings.app.timezone)
    n = AdminNotifier(FakeBot(), 42, settings.monitoring, renderer, settings.app.timezone)  # type: ignore[arg-type]
    deal = Product(source="ppomppu", product_id="toss:1", shop="toss", name=WATER, price=5900, url="u")
    ref = CoupangMarketReference(None, MarketCheckConfig())  # type: ignore[arg-type]
    q = ref.pick_best(deal, [_cand(2, "스파클 생수 무라벨, 500ml, 40개 <로켓>", 10900)])
    v = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False))).evaluate(deal, PriceStats(), q, market_available=True)
    await n.notify_published(Deal(product=deal, verdict=v), PublishResult(ok=True, dry_run=True), preview="글")
    assert "비교한 쿠팡 상품: 스파클 생수 무라벨, 500ml, 40개 &lt;로켓&gt; 10,900원" in sent[-1]
    # 대조를 안 한 딜은 그 줄이 없음
    v2 = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False))).evaluate(
        Product(source="s", product_id="x:1", shop="toss", name="n", price=5000, url="u", discount_rate=60), PriceStats()
    )
    await n.notify_published(Deal(product=deal, verdict=v2), PublishResult(ok=True, dry_run=True), preview="글")
    assert "비교한 쿠팡 상품" not in sent[-1]
