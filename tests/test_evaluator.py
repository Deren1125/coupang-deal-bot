from __future__ import annotations

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
