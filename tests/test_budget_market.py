from __future__ import annotations

import httpx
import pytest

from dealbot.config import DealConfig, InterestConfig, MarketCheckConfig
from dealbot.coupang.client import ApiBudget, CoupangClient, CoupangRateLimited
from dealbot.models import PriceStats, Product
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import (
    CoupangMarketReference,
    MarketQuote,
    build_keyword,
    match_ratio,
    normalize_qty,
    tokens,
)


def test_api_budget_reserve() -> None:
    b = ApiBudget(5, reserve={"deeplink": 2})
    # 일반 호출은 3번까지
    for _ in range(3):
        assert b.available("goldbox")
        b.record("goldbox")
    assert not b.available("goldbox")
    # 딥링크는 예약 몫 2번 더
    assert b.available("deeplink")
    b.record("deeplink")
    assert b.available("deeplink")
    b.record("deeplink")
    assert not b.available("deeplink")
    assert b.used() == 5 and b.usage() == {"goldbox": 3, "deeplink": 2}
    # 시간이 지나면 풀림
    assert b.available("goldbox", now=b._calls[0][0] + 3601)
    assert ApiBudget(0).available("anything")


async def test_client_raises_when_budget_exhausted() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"rCode": "0", "data": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c = CoupangClient("a", "s", http=http, budget=ApiBudget(1, reserve={"deeplink": 0}), retry_backoff=0.01)
    await c.goldbox()
    with pytest.raises(CoupangRateLimited):
        await c.goldbox()
    assert calls["n"] == 1


def test_tokenizer_and_matching() -> None:
    name = "[토스쇼핑] 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트 (14,890원/무료)"
    assert normalize_qty(name) == {"4종", "80g", "8개", "2세트"} or "80g" in normalize_qty(name)
    toks = tokens(name)
    assert "애슐리" in toks and "핫도그" in toks and "무료" not in toks and "토스쇼핑" not in toks
    kw = build_keyword("애슐리 크리스피 핫도그 4종 80g 8개입 2세트")
    assert kw.startswith("애슐리 크리스피 핫도그")
    assert match_ratio("애슐리 크리스피 핫도그 8개입 2세트", "애슐리 크리스피 핫도그, 80g, 8개입, 2세트") == 1.0
    assert match_ratio("애슐리 크리스피 핫도그 8개입 2세트", "애슐리 크리스피 핫도그 8개입 1세트") == 0.0  # 수량 불일치
    assert 0 < match_ratio("애슐리 크리스피 핫도그", "애슐리 치즈볼") < 0.6


async def test_market_reference_lookup_and_budget() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert "keyword" in req.url.params
        return httpx.Response(
            200,
            json={
                "rCode": "0",
                "data": {
                    "productData": [
                        {"productId": 1, "productName": "애슐리 크리스피 핫도그 80g 8개입 2세트", "productPrice": 17900, "productUrl": "https://link.coupang.com/re/1"},
                        {"productId": 2, "productName": "애슐리 크리스피 핫도그 80g 8개입 1세트", "productPrice": 8000, "productUrl": "https://link.coupang.com/re/2"},
                        {"productId": 3, "productName": "전혀 다른 상품", "productPrice": 1000, "productUrl": "https://link.coupang.com/re/3"},
                    ]
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = CoupangClient("a", "s", http=http, retry_backoff=0.01)
    ref = CoupangMarketReference(client, MarketCheckConfig(max_checks_per_hour=1))
    p = Product(source="s", product_id="toss:1", shop="toss", name="애슐리 크리스피 핫도그 8개입 2세트", price=14890, url="https://toss.im/_m/1")
    q = await ref.lookup(p)
    assert q is not None and q.price == 17900 and q.source == "coupang"
    assert await ref.lookup(p) is None  # 시간당 예산 소진
    assert await ref.lookup(Product(source="s", product_id="coupang:1", shop="coupang", name="x", price=1, url="u")) is None


def test_rule_d_and_veto() -> None:
    ev = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False), min_discount_rate=50, community_min_recommend=5))
    p = Product(source="s", product_id="toss:1", shop="toss", name="핫도그", price=14890, url="u", recommend_count=9)
    # 대조 없이: (c) 로 통과
    assert ev.evaluate(p, PriceStats()).is_deal
    # 쿠팡이 더 쌈 → veto
    v = ev.evaluate(p, PriceStats(), MarketQuote(price=12000, source="coupang", title="핫도그"), market_available=True)
    assert not v.is_deal and "above_market_price" in v.reasons and v.market_price == 12000
    # 쿠팡보다 16.8% 쌈 → 기준(20%) 미만이라 strict 모드에서는 탈락 (추천 9개가 있어도 보조 규칙으로 안 넘어감)
    p2 = Product(source="s", product_id="toss:2", shop="toss", name="핫도그", price=14890, url="u", recommend_count=9)
    v2 = ev.evaluate(p2, PriceStats(), MarketQuote(price=17900, source="coupang", title="핫도그"), market_available=True)
    assert not v2.is_deal and v2.reasons == ["below_coupang_price<20%"] and v2.below_market_pct == 16.8 and v2.market_price == 17900
    # 쿠팡보다 22.3% 쌈 → (d) 통과
    v2b = ev.evaluate(Product(source="s", product_id="toss:3", shop="toss", name="핫도그", price=13900, url="u"), PriceStats(),
                      MarketQuote(price=17900, source="coupang", title="핫도그"), market_available=True)
    assert v2b.is_deal and v2b.reasons == ["below_coupang_price>=20%"] and v2b.below_market_pct == 22.3 and v2b.score == 22.3
    # strict 를 끄면 0~20% 사이는 보조 규칙 (c) 로 판정
    loose = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False), community_min_recommend=5, market=MarketCheckConfig(strict=False)))
    v2c = loose.evaluate(p2, PriceStats(), MarketQuote(price=17900, source="coupang", title="핫도그"), market_available=True)
    assert v2c.is_deal and "market_diff=16.8%" in v2c.reasons and "recommend>=5" in v2c.reasons
    # 표시 할인율만 있고 대조 가능 환경에서 확인 안 됨 → 탈락
    v3 = ev.evaluate(Product(source="s", product_id="x:1", shop="11st", name="n", price=5000, url="u", discount_rate=60), PriceStats(), None, market_available=True)
    assert not v3.is_deal and v3.reasons == ["discount_unconfirmed"]
    # 대조 불가 환경이면 표시 할인율 50% 로 통과
    v4 = ev.evaluate(Product(source="s", product_id="x:1", shop="11st", name="n", price=5000, url="u", discount_rate=60), PriceStats())
    assert v4.is_deal and v4.reasons == ["discount_rate>=50%"]
