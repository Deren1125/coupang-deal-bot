from __future__ import annotations

from dealbot.config import DealConfig
from dealbot.models import PriceStats, Product
from dealbot.pricing.evaluator import DealEvaluator


def _p(price: int, **kw) -> Product:  # type: ignore[no-untyped-def]
    return Product(source="s", product_id="1", name=kw.pop("name", "상품"), price=price, url="u", **kw)


def test_rule_a_displayed_discount() -> None:
    ev = DealEvaluator(DealConfig())
    v = ev.evaluate(_p(7000, discount_rate=35), PriceStats())
    assert v.is_deal and v.reasons == ["discount_rate>=30%"] and v.score == 35


def test_rule_a_computed_from_original_price() -> None:
    ev = DealEvaluator(DealConfig())
    v = ev.evaluate(_p(6000, original_price=10000), PriceStats())
    assert v.is_deal and v.discount_rate == 40.0
    assert not ev.evaluate(_p(8000, original_price=10000), PriceStats()).is_deal


def test_rule_b_below_average_requires_samples() -> None:
    ev = DealEvaluator(DealConfig())
    stats = PriceStats(count=2, avg=10000, min=9500, max=10500)
    assert not ev.evaluate(_p(8000), stats).is_deal  # 샘플 부족
    stats.count = 3
    v = ev.evaluate(_p(8000), stats)
    assert v.is_deal and v.below_avg_pct == 20.0 and v.reasons == ["below_30d_avg>=15%"]
    assert not ev.evaluate(_p(9000), stats).is_deal  # 10% 만 저렴


def test_thresholds_configurable() -> None:
    ev = DealEvaluator(DealConfig(min_discount_rate=50, min_below_average_pct=5, min_history_samples=1))
    assert not ev.evaluate(_p(7000, discount_rate=40), PriceStats()).is_deal
    assert ev.evaluate(_p(9400, discount_rate=None), PriceStats(count=1, avg=10000)).is_deal


def test_min_price_and_exclude_keywords() -> None:
    ev = DealEvaluator(DealConfig(min_price=5000, exclude_keywords=["리퍼"]))
    assert ev.evaluate(_p(3000, discount_rate=90), PriceStats()).reasons == ["below_min_price"]
    v = ev.evaluate(_p(9000, discount_rate=90, name="리퍼 노트북"), PriceStats())
    assert not v.is_deal and v.reasons == ["excluded:리퍼"]


def test_both_rules_score_is_max() -> None:
    ev = DealEvaluator(DealConfig())
    v = ev.evaluate(_p(5000, discount_rate=31), PriceStats(count=5, avg=10000))
    assert v.is_deal and len(v.reasons) == 2 and v.score == 50.0
