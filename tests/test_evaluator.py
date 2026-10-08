from __future__ import annotations

import pytest

from dealbot.config import DealConfig, InterestConfig
from dealbot.models import PriceStats, Product
from dealbot.pricing.evaluator import DealEvaluator

NO_GATE = InterestConfig(enabled=False)


def _p(price: int, **kw) -> Product:  # type: ignore[no-untyped-def]
    return Product(source="s", product_id="coupang:1", shop="coupang", name=kw.pop("name", "상품"), price=price, url="u", **kw)


def test_interest_gate() -> None:
    ev = DealEvaluator(DealConfig())
    assert ev.evaluate(_p(7000, discount_rate=90), PriceStats()).reasons == ["low_interest"]
    assert ev.interest_signal(_p(1, recommend_count=1)) == "recommend>=1"
    assert ev.interest_signal(_p(1, comment_count=3)) == "comments>=3"
    assert ev.interest_signal(_p(1, view_count=500)) == "views>=500"
    assert ev.interest_signal(_p(1, rank=30)) == "rank<=30"
    assert ev.interest_signal(_p(1, rank=31, view_count=10)) is None
    assert ev.interest_signal(Product(source="manual", product_id="x", name="n", price=1, url="u")) == "always"
    v = ev.evaluate(_p(7000, discount_rate=90, rank=3), PriceStats())
    assert v.is_deal and v.reasons[0] == "interest:rank<=30"


def test_rule_a_displayed_discount() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    assert not ev.evaluate(_p(7000, discount_rate=35), PriceStats()).is_deal  # 기본 임계값 50
    v = ev.evaluate(_p(7000, discount_rate=55), PriceStats())
    assert v.is_deal and v.reasons == ["discount_rate>=50%"] and v.score == 27.5


def test_rule_a_computed_from_original_price() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    v = ev.evaluate(_p(4000, original_price=10000), PriceStats())
    assert v.is_deal and v.discount_rate == 60.0
    assert not ev.evaluate(_p(6000, original_price=10000), PriceStats()).is_deal


def test_rule_b_below_average_requires_samples() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    stats = PriceStats(count=2, avg=10000, min=9500, max=10500)
    assert not ev.evaluate(_p(8000), stats).is_deal
    stats.count = 3
    v = ev.evaluate(_p(8000), stats)
    assert v.is_deal and v.below_avg_pct == 20.0 and v.reasons == ["below_30d_avg>=15%"]
    assert not ev.evaluate(_p(9000), stats).is_deal


def test_rule_c_community_recommend() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE, community_min_recommend=5))
    assert not ev.evaluate(_p(9000, recommend_count=4), PriceStats()).is_deal
    v = ev.evaluate(_p(9000, recommend_count=7), PriceStats())
    assert v.is_deal and v.reasons == ["recommend>=5"] and v.score == 7
    assert not DealEvaluator(DealConfig(interest=NO_GATE, community_min_recommend=0)).evaluate(_p(9000, recommend_count=99), PriceStats()).is_deal


def test_coupons_and_events_only_by_recommend() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE, community_min_recommend=3, coupon_min_recommend=3))
    coupon = _p(0, deal_kind="coupon", name="토스 25% 쿠폰", recommend_count=2)
    assert ev.evaluate(coupon, PriceStats()).reasons == ["no_price_low_recommend"]
    coupon.recommend_count = 3
    v = ev.evaluate(coupon, PriceStats())
    assert v.is_deal and v.reasons == ["recommend>=3"]
    ev2 = DealEvaluator(DealConfig(interest=NO_GATE, accept_coupons_and_events=False))
    assert ev2.evaluate(coupon, PriceStats()).reasons == ["no_price"]
    assert ev2.evaluate(_p(0, deal_kind="event", name="라방 랜덤 5원", recommend_count=50), PriceStats()).reasons == ["no_price"]
    # 가격이 적힌 딜은 제목에 '이벤트/증정' 이 있어 event 로 분류됐어도 가격 기준으로 판정한다
    priced_event = _p(9900, deal_kind="event", name="1+1 증정 이벤트", discount_rate=60)
    v = ev2.evaluate(priced_event, PriceStats())
    assert v.is_deal and "no_price" not in v.reasons and v.discount_rate == 60
    assert not ev2.evaluate(_p(9900, deal_kind="event", name="증정 이벤트", discount_rate=10), PriceStats()).is_deal


def test_thresholds_configurable() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE, min_discount_rate=50, min_below_average_pct=5, min_history_samples=1))
    assert not ev.evaluate(_p(7000, discount_rate=40), PriceStats()).is_deal
    assert ev.evaluate(_p(9400, discount_rate=None), PriceStats(count=1, avg=10000)).is_deal


def test_min_price_and_exclude_keywords() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE, min_price=5000, exclude_keywords=["리퍼"]))
    assert ev.evaluate(_p(3000, discount_rate=90), PriceStats()).reasons == ["below_min_price"]
    v = ev.evaluate(_p(9000, discount_rate=90, name="리퍼 노트북"), PriceStats())
    assert not v.is_deal and v.reasons == ["excluded:리퍼"]


def test_both_rules_score_is_max() -> None:
    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    v = ev.evaluate(_p(5000, discount_rate=51), PriceStats(count=5, avg=10000))
    assert v.is_deal and len(v.reasons) == 2 and v.score == 50.0


def test_priced_deals_and_events_have_separate_bars() -> None:
    """가격이 적힌 딜은 낮은 기준, 가격 없는 이벤트/공지는 높은 기준."""
    from dealbot.config import SourceRule

    cfg = DealConfig(
        community_min_recommend=5,
        coupon_min_recommend=20,
        per_source={"ruliweb_user": SourceRule(coupon_min_recommend=30), "quiet": SourceRule(community_min_recommend=0)},
    )
    ev = DealEvaluator(cfg)

    def priced(source: str, rec: int) -> Product:
        return Product(source=source, product_id="x:1", shop="coupang", name="오뚜기 소스 2개", price=3480, url="u", recommend_count=rec)

    def event(source: str, rec: int) -> Product:
        return Product(source=source, product_id="x:2", shop="naver", name="라방 랜덤 5원", price=0, url="u",
                       deal_kind="event", recommend_count=rec)

    # 가격 있는 딜: 추천 5 면 통과 (진짜 특가를 놓치지 않도록)
    assert ev.evaluate(priced("ruliweb_user", 8), PriceStats()).is_deal
    assert ev.evaluate(priced("ppomppu", 6), PriceStats()).is_deal
    # 가격 없는 이벤트: 같은 추천 수여도 탈락
    assert not ev.evaluate(event("ppomppu", 8), PriceStats()).is_deal
    assert ev.evaluate(event("ppomppu", 25), PriceStats()).is_deal
    # 루리웹 이벤트는 30 이상이어야
    assert not ev.evaluate(event("ruliweb_user", 25), PriceStats()).is_deal
    assert ev.evaluate(event("ruliweb_user", 37), PriceStats()).is_deal
    assert ev.min_recommend_for(event("ruliweb_user", 1)) == 30
    assert ev.min_recommend_for(priced("ruliweb_user", 1)) == 5
    assert not ev.evaluate(priced("quiet", 99), PriceStats()).is_deal


def test_quality_gate_by_review_count() -> None:
    from dealbot.config import QualityConfig

    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    assert ev.evaluate(_p(7000, discount_rate=60, review_count=3), PriceStats()).reasons[-1] == "few_reviews<10"
    assert ev.evaluate(_p(7000, discount_rate=60, review_count=10), PriceStats()).is_deal
    assert ev.evaluate(_p(7000, discount_rate=60), PriceStats()).is_deal  # 후기 수를 모르면 통과
    only_naver = DealEvaluator(DealConfig(interest=NO_GATE, quality=QualityConfig(min_review_count=10, shops=["naver"])))
    assert only_naver.evaluate(_p(7000, discount_rate=60, review_count=3), PriceStats()).is_deal  # 쿠팡은 대상 아님
    off = DealEvaluator(DealConfig(interest=NO_GATE, quality=QualityConfig(enabled=False)))
    assert off.evaluate(_p(7000, discount_rate=60, review_count=0), PriceStats()).is_deal


@pytest.mark.food_rule
def test_food_needs_half_price_against_reference() -> None:
    from dealbot.config import DealConfig
    from dealbot.models import PriceStats
    from dealbot.pricing.evaluator import DealEvaluator
    from dealbot.pricing.market import MarketQuote

    cfg = DealConfig()
    cfg.interest.enabled = False
    ev = DealEvaluator(cfg)
    stats = PriceStats()
    food = Product(source="s", product_id="coupang:1", shop="coupang", name="갈아만든배 340ml 24개", price=12360, url="u", discount_rate=60, recommend_count=50)
    assert ev.is_food(food)
    v = ev.evaluate(food, stats)  # 표시 할인율·추천 수만으로는 식품 특가가 아니다
    assert not v.is_deal and any(r.startswith("food_below_ref") for r in v.reasons)
    assert ev.evaluate(food, stats, MarketQuote(price=30000, source="coupang", title="t", url=None), market_available=True).is_deal  # 59% 싸면 특가
    assert not ev.evaluate(food, stats, MarketQuote(price=20000, source="coupang", title="t", url=None), market_available=True).is_deal  # 38% 는 부족
    shoes = Product(source="s", product_id="coupang:2", shop="coupang", name="휠라 타르가 운동화", price=38810, url="u", discount_rate=60, recommend_count=50)
    assert not ev.is_food(shoes) and ev.evaluate(shoes, stats).is_deal  # 식품이 아니면 기존 기준
    by_category = Product(source="s", product_id="coupang:3", shop="coupang", name="프리미엄 선물세트", price=9900, url="u", category="식품>선물세트", discount_rate=60)
    assert ev.is_food(by_category)
    by_units = Product(source="s", product_id="coupang:4", shop="coupang", name="제주 삼다수 2L 6개", price=5900, url="u")
    assert ev.is_food(by_units)  # 음료 묶음 규격
    shampoo = Product(source="s", product_id="coupang:5", shop="coupang", name="케라시스 샴푸 500ml 2개", price=9900, url="u")
    assert not ev.is_food(shampoo)  # 규격이 비슷해도 생활용품은 제외


# ---- 배송비: 쿠팡과 비교할 때 딜의 배송비까지 더한다


def _toss(name: str, price: int, shipping: str | None, **kw) -> Product:  # type: ignore[no-untyped-def]
    return Product(source="ppomppu", product_id="toss:1", shop="toss", name=name, price=price, url="u", shipping=shipping, **kw)


@pytest.mark.parametrize(
    ("shipping", "price", "fee"),
    [
        (None, 6900, 0), ("무료", 6900, 0), ("무배", 6900, 0), ("0원", 6900, 0), ("무료/카드할인", 6900, 0),
        ("무료(제주 3,000원)", 6900, 0), ("3,500원", 6900, 3500), ("배송비 3,000원", 6900, 3000), ("3천원", 6900, 3000),
        ("2,500(3만↑무료)", 6900, 2500), ("2,500(3만↑무료)", 35000, 0), ("3만원이상무료", 40000, 0),
        ("3만원이상무료", 20000, None), ("조건부무료", 6900, None), ("착불", 6900, None), ("택배비 별도", 6900, None),
        ("로켓배송", 6900, 0), ("카드할인", 6900, 0),  # 배송비 얘기가 아닌 글은 예전처럼 상품가로 비교
    ],
)
def test_shipping_fee_parsing(shipping: str | None, price: int, fee: int | None) -> None:
    from dealbot.pricing.evaluator import shipping_fee

    assert shipping_fee(_toss("n", price, shipping)) == fee


def test_market_comparison_includes_deal_shipping_fee() -> None:
    from dealbot.pricing.market import MarketQuote

    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    quote = MarketQuote(price=9900, source="coupang", title="3M 스카치브라이트 수세미 20개입")
    # 6,900원 + 배송비 3,500원 = 10,400원 → 쿠팡 무료배송 9,900원보다 비쌈 (예전엔 '쿠팡보다 30%↓' 로 통과)
    v = ev.evaluate(_toss("3M 스카치브라이트 수세미 20개입", 6900, "3,500원"), PriceStats(), quote, market_available=True)
    assert not v.is_deal and "above_market_price" in v.reasons and v.below_market_pct == -5.1
    # 무료배송이면 그대로 30% 싸서 통과
    v = ev.evaluate(_toss("3M 스카치브라이트 수세미 20개입", 6900, "무료"), PriceStats(), quote, market_available=True)
    assert v.is_deal and v.reasons == ["below_coupang_price>=20%"] and v.below_market_pct == 30.3
    assert v.market_title == "3M 스카치브라이트 수세미 20개입"
    # 배송비를 더해도 20% 넘게 싸면 통과하되, % 는 배송비 포함 값
    v = ev.evaluate(_toss("n", 5000, "2,500원"), PriceStats(), MarketQuote(price=10000, source="coupang", title="n"), market_available=True)
    assert v.is_deal and v.below_market_pct == 25.0


def test_unknown_shipping_fee_never_makes_a_market_claim() -> None:
    from dealbot.config import MarketCheckConfig
    from dealbot.pricing.market import MarketQuote

    quote = MarketQuote(price=9900, source="coupang", title="n")
    p = _toss("n", 6900, "착불", recommend_count=9)
    strict = DealEvaluator(DealConfig(interest=NO_GATE, community_min_recommend=5))
    v = strict.evaluate(p, PriceStats(), quote, market_available=True)
    assert not v.is_deal and v.reasons == ["market_shipping_unknown"] and v.below_market_pct is None
    # strict 를 끄면 보조 규칙(추천)으로 판정하되, 글에 '쿠팡보다 N%↓' 를 쓸 근거(below_market_pct)는 남기지 않음
    loose = DealEvaluator(DealConfig(interest=NO_GATE, community_min_recommend=5, recommend_needs_support=False,
                                     market=MarketCheckConfig(strict=False)))
    v = loose.evaluate(p, PriceStats(), quote, market_available=True)
    assert v.is_deal and "market_shipping_unknown" in v.reasons and v.below_market_pct is None and v.market_price == 9900
    v = loose.evaluate(_toss("n", 9000, "착불", recommend_count=9), PriceStats(), quote, market_available=True)
    assert v.is_deal and "market_diff=9.1%" in v.reasons and v.below_market_pct is None
    # 상품가만으로도 쿠팡보다 비싸면 배송비를 몰라도 탈락
    v = strict.evaluate(_toss("n", 9900, "착불"), PriceStats(), quote, market_available=True)
    assert not v.is_deal and "above_market_price" in v.reasons


# ---- 식품 판정: 짧은 낱말이 다른 낱말 속에 들어 있어 생기는 오판


@pytest.mark.food_rule
@pytest.mark.parametrize(
    "name",
    [
        "할리스 바닐라 딜라이트 로우슈거, 24개", "스파클 생수 무라벨, 500ml, 40병", "펩시 제로 라임 24캔", "팔도 비빔면 5개",
        "유니짜장면 4인분", "칼국수면 10인분", "본죽 전복죽 6팩", "양배추즙 30포", "곰곰 냉동 만두 1kg", "닭갈비 1kg",
        "멸치 다시팩 30개", "다시마 500g", "맥심 모카골드 180T",
    ],
)
def test_is_food_catches_drinks_and_compound_food_words(name: str) -> None:
    assert DealEvaluator(DealConfig()).is_food(Product(source="s", product_id="x", name=name, price=1, url="u"))


@pytest.mark.food_rule
@pytest.mark.parametrize(
    "name",
    [
        "브라운 면도기 시리즈5", "존스미스 순면 화장솜 면봉 500개", "면 100% 티셔츠", "가죽 지갑 남성 반지갑", "인조가죽 소파",
        "삼성 비스포크 냉동고 200L", "휴롬 착즙기 H300", "드롱기 커피머신 마그니피카", "LG 27인치 모니터 IPS 화면",
        "삼성 대화면 TV", "양면 테이프 10개", "수면 양말 5켤레", "다시 입고된 운동화", "스타벅스 텀블러",
    ],
)
def test_is_food_ignores_goods_that_contain_food_syllables(name: str) -> None:
    assert not DealEvaluator(DealConfig()).is_food(Product(source="s", product_id="x", name=name, price=1, url="u"))


@pytest.mark.food_rule
def test_food_rule_also_reads_the_matched_coupang_title() -> None:
    from dealbot.pricing.market import MarketQuote

    ev = DealEvaluator(DealConfig(interest=NO_GATE))
    # 딜 이름만으로는 식품인지 모름 → 쿠팡 상품 이름의 '285ml, 24개' 규격으로 음료임을 알아냄 → 식품 기준(50%) 적용
    p = _toss("브랜드엑스 바닐라 딜라이트, 24개", 24900, "무료")
    assert not ev.is_food(p)
    v = ev.evaluate(p, PriceStats(), MarketQuote(price=35000, source="coupang", title="브랜드엑스 바닐라 딜라이트, 285ml, 24개"),
                    market_available=True)
    assert not v.is_deal and v.below_market_pct == 28.9 and v.reasons[-1] == "food_below_ref<50%"
