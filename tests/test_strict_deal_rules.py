"""운영 설정(config.yaml)의 엄격한 특가 기준: 실제로 채널에 잘못 올라갔던 글 유형을 막는지."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from dealbot.config import load_settings
from dealbot.models import PriceStats, Product
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import CoupangMarketReference
from dealbot.utils.timeutil import utcnow

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def ev() -> DealEvaluator:
    return DealEvaluator(load_settings(ROOT / "config.yaml", load_env=False).deal)


def _p(name: str, price: int, source: str = "ppomppu", shop: str = "coupang", **kw) -> Product:
    return Product(source=source, product_id=f"{shop}:1", shop=shop, name=name, price=price,
                   url="https://www.coupang.com/vp/products/1", **kw)


def _hist(avg: float, low: int, days: float = 10, count: int = 8) -> PriceStats:
    return PriceStats(count=count, avg=avg, min=low, max=int(avg * 1.2), first_seen_at=utcnow() - timedelta(days=days))


def test_recommends_alone_do_not_make_a_deal(ev):
    """PS5 타이틀·폴드 같은 정가 상품이 추천 5~10개로 올라가던 문제."""
    assert not ev.evaluate(_p("아스트로 봇 패키지", 38450, recommend_count=12), PriceStats()).is_deal
    v = ev.evaluate(_p("로지텍 G102 마우스", 19900, recommend_count=35), PriceStats())  # 추천 30↑ = 커뮤니티 검증
    assert v.is_deal


def test_displayed_discount_alone_is_not_trusted_except_goldbox(ev):
    assert not ev.evaluate(_p("네파 구스 패딩", 89000, discount_rate=60, recommend_count=1), PriceStats()).is_deal
    gold = _p("스파클 생수 2L 24개", 7400, source="goldbox", discount_rate=55, rank=3)
    assert ev.evaluate(gold, PriceStats()).is_deal


def test_history_needs_days_and_near_low(ev):
    item = _p("사조 살코기 참치 150g 10개", 15740, source="goldbox", rank=2)
    assert not ev.evaluate(item, _hist(avg=20000, low=15000, days=1)).is_deal  # 하루치 이력으로 단정 안 함
    assert not ev.evaluate(item, _hist(avg=20000, low=13000)).is_deal  # 최근 최저(13,000)보다 비쌈
    assert ev.evaluate(item, _hist(avg=20000, low=15500)).is_deal  # 평균 대비 21%↓ + 최저가 수준


def test_blocked_categories_and_cheap_items(ev):
    assert not ev.evaluate(_p("★스탠다드★ 오션뷰 객실 1박", 80000, recommend_count=40), PriceStats()).is_deal
    assert not ev.evaluate(_p("갤럭시 폴드8 자급제", 1898680, recommend_count=40), PriceStats()).is_deal
    v = ev.evaluate(_p("니트릴 위생장갑 100매", 2980, recommend_count=40), PriceStats())
    assert not v.is_deal and "below_min_price" in v.reasons


def test_market_match_needs_same_brand():
    s = load_settings(ROOT / "config.yaml", load_env=False)
    ref = CoupangMarketReference(client=None, cfg=s.deal.market)  # type: ignore[arg-type]
    deal = _p("폴햄 남성 기모 맨투맨 3종", 19900, shop="gmarket")
    other = Product(source="market", product_id="coupang:9", shop="coupang", name="기모 맨투맨 3종 남성 세트",
                    price=39800, url="https://www.coupang.com/vp/products/9")
    assert ref.pick_best(deal, [other]) is None  # 브랜드(폴햄)가 없는 비싼 상품을 '시중가'로 잡지 않음
