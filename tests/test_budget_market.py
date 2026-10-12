from __future__ import annotations

import httpx
import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, DealConfig, InterestConfig, MarketCheckConfig, Settings
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


def test_budget_caps_a_kind_so_it_cannot_starve_the_rest() -> None:
    b = ApiBudget(10, reserve={"deeplink": 3}, caps={"deeplink": 7})
    for _ in range(7):
        assert b.available("deeplink")
        b.record("deeplink")
    assert not b.available("deeplink")  # 딥링크는 7회까지
    assert b.available("goldbox") and b.available("search")  # 나머지 3회는 다른 호출 몫
    for _ in range(3):
        b.record("goldbox")
    assert not b.available("goldbox") and not b.available("deeplink")


# ---------------------------------------------------------------- 게시판이 같은 글을 5분마다 다시 돌려줄 때 (#25)
_RUNS: list[list[Product]] = []


@register("fake_market_rerun")
class _RerunCollector(BaseCollector):
    """판마다 새 Product 를 돌려준다 (실제 수집기처럼 — 앞 판에서 고친 값이 남지 않게)."""

    async def collect(self) -> list[Product]:
        return _RUNS.pop(0) if _RUNS else []


def _toss_deal(pid: str, name: str, price: int) -> Product:
    return Product(source="fake", product_id=f"toss:{pid}", shop="toss", name=name, price=price,
                   url=f"https://toss.im/_m/{pid}", recommend_count=9, discount_rate=50)


@pytest.fixture
def rerun_bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_market_rerun", interval_minutes=1)]
    _RUNS.clear()
    b = DealBot(settings)
    yield b
    b.db.close()


async def test_market_no_match_is_remembered_but_a_skipped_lookup_is_not(rerun_bot: DealBot) -> None:
    searches: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        searches.append(req.url.params["keyword"])
        return httpx.Response(200, json={"rCode": "0", "data": {"productData": [
            {"productId": 3, "productName": "전혀 다른 상품", "productPrice": 1000, "productUrl": "https://link.coupang.com/re/3"},
        ]}})

    client = CoupangClient("a", "s", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0.01)
    ref = CoupangMarketReference(client, MarketCheckConfig(max_checks_per_hour=5))
    rerun_bot.market = ref
    hotdog = "애슐리 크리스피 핫도그 8개입 2세트"
    _RUNS.extend([[_toss_deal("1", hotdog, 14890)], [_toss_deal("1", hotdog, 14890)],
                  [_toss_deal("2", "풀무원 샘물 2L 24개", 9900)]])
    for _ in range(2):
        await rerun_bot.run_collector(rerun_bot.collectors[0])
    assert len(searches) == 1  # 쿠팡에 없던 상품을 5분마다 다시 찾지 않음 (시간당 예산을 같은 글이 다 쓰지 않게)
    assert rerun_bot.db.get_market_quote("toss:1", 24)["price"] == 0
    ref.cfg.max_checks_per_hour = 1
    await rerun_bot.run_collector(rerun_bot.collectors[0])  # 예산이 다 차서 검색을 못 한 것은 '없음'으로 기억하지 않음
    assert len(searches) == 1 and rerun_bot.db.get_market_quote("toss:2", 24) is None


async def test_no_market_lookup_when_the_quote_cannot_change_anything(rerun_bot: DealBot) -> None:
    looked: list[str] = []

    async def market_quote(p: Product) -> MarketQuote | None:
        looked.append(p.product_id)
        return None

    rerun_bot.market = object()  # type: ignore[assignment]  # 시중가 대조가 켜진 환경
    rerun_bot._market_quote = market_quote  # type: ignore[method-assign]
    hotdog = "애슐리 크리스피 핫도그 8개입 2세트"
    _RUNS.extend([[_toss_deal("1", hotdog, 14890), _toss_deal("9", "위생장갑 100매", 900)],
                  [_toss_deal("1", hotdog, 14890), _toss_deal("9", "위생장갑 100매", 900)]])
    for _ in range(2):
        await rerun_bot.run_collector(rerun_bot.collectors[0])
    assert rerun_bot.db.queue_counts() == {"pending": 1}
    # 대기열에 이미 있는 글은 다시 대조하지 않고, 최저가 미만(대조 전에 탈락)은 처음부터 대조하지 않음
    assert looked == ["toss:1"]
