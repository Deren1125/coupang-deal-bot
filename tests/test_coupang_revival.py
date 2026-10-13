"""쿠팡 딜 살리기 (주인 결정 10/13 '전부 진행', 목표: 너무 싼 딜을 찾기 — 싼 딜을 보류하면 안 됨).

1. 골드박스·카테고리 베스트: 표시 할인율 칸이 없어 통과할 수 없던 것을, 확실히 같은 상품의 네이버 최저가(공식 쇼핑 검색 API)와
   가격 이력으로 판정. 네이버보다 비싸면 탈락(veto), 헷갈리면 근거 없음. 키 없음·401 이면 비교만 꺼짐 (로그 한 번)
2. 카테고리 베스트: 더 많은 카테고리를 돌아가며 한 판에 조금씩 (쿠팡 API 예산·딥링크 몫 지킴)
3. 특가인데 안 올린 이유: 수집 한 판에 INFO 한 줄 + 저녁 요약 한 줄. 골드박스 응답 칸 이름은 프로세스마다 한 번 로그
테스트는 가짜 HTTP·가짜 쿠팡만 쓴다 (네트워크 없음).
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dealbot.app import NAVER_QUOTE_KEY, SKIP_KV, DealBot
from dealbot.collectors import BaseCollector, CollectorContext, build_collector, register
from dealbot.collectors.coupang_category_best import CURSOR_KV
from dealbot.collectors.coupang_goldbox import CoupangGoldboxCollector
from dealbot.commentary import THREAD_BANNED, check_comment
from dealbot.config import (
    CollectorConfig,
    DealConfig,
    InterestConfig,
    NaverReferenceConfig,
    Settings,
    load_settings,
)
from dealbot.coupang.client import ApiBudget, CoupangRateLimited
from dealbot.models import Deal, DealVerdict, PriceStats, Product
from dealbot.naver_shop import (
    LookupRun,
    NaverPriceReference,
    NaverShopSearch,
    item_title,
    naver_lowest,
    same_item,
)
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import NAVER_SOURCE, MarketQuote
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry
from dealbot.utils.timeutil import to_iso, utcnow

ROOT = Path(__file__).resolve().parent.parent
CLIENT_ID, CLIENT_SECRET = "test-naver-id", "test-naver-secret-value"
WATER = "탐사 샘물 2L 12개"
BUDS = "삼성 갤럭시 버즈3 프로"


# ---------------------------------------------------------------- 가짜들
def _nitem(title: str, lprice: int, *, ptype: str = "1", mall: str = "네이버", n: int = 1) -> dict[str, Any]:
    """네이버 쇼핑 검색 결과 한 칸 (productType 1 = 가격비교 카탈로그, 2·3 = 일반 판매글)."""
    return {"title": title, "link": f"https://search.shopping.naver.com/catalog/{n}", "image": "https://img/x.jpg",
            "lprice": str(lprice), "hprice": "", "mallName": mall, "productId": str(n), "productType": ptype,
            "category1": "식품", "category2": "생수/음료"}


class NaverApi:
    """openapi.naver.com/v1/search/shop.json 가짜: 검색어 → 결과 (기본 결과는 default). status 로 401·429 흉내."""

    def __init__(self, default: list[dict[str, Any]] | None = None, *, status: int = 200,
                 asc: list[dict[str, Any]] | None = None) -> None:
        self.default = default or []
        self.by_query: dict[str, list[dict[str, Any]]] = {}
        self.asc = asc  # 낮은 가격순(sort=asc) 검색 결과 (None 이면 관련도순과 같게)
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.url.host == "openapi.naver.com" and req.url.path == "/v1/search/shop.json"
        self.requests.append(req)
        if self.status != 200:
            return httpx.Response(self.status, json={"errorMessage": "x"})
        items = self.by_query.get(req.url.params["query"], self.default)
        if req.url.params.get("sort") == "asc" and self.asc is not None:
            items = self.asc
        return httpx.Response(200, json={"total": len(items), "items": items})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _coupang(pid: str, name: str, price: int, *, source: str = "goldbox", **kw: Any) -> Product:
    return Product(source=source, product_id=f"coupang:{pid}:{pid}1", shop="coupang", name=name, price=price,
                   url=f"https://www.coupang.com/vp/products/{pid}", affiliate_url=f"https://link.coupang.com/re/{pid}",
                   rank=kw.pop("rank", 3), is_rocket=True, is_free_shipping=True, **kw)


_RUNS: list[list[Product]] = []


@register("fake_coupang_revival")
class _ListCollector(BaseCollector):
    """판마다 새 Product 목록을 돌려준다 (골드박스처럼)."""

    async def collect(self) -> list[Product]:
        return _RUNS.pop(0) if _RUNS else []


def _nref_cfg(**kw: Any) -> NaverReferenceConfig:
    base: dict[str, Any] = {"min_interval_seconds": 0}
    base.update(kw)
    return NaverReferenceConfig(**base)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="goldbox", type="fake_coupang_revival", interval_minutes=1)]
    # 네이버 최저가 비교 경로를 시험한다 (운영 config.yaml 은 10/13 주인 결정으로 enabled: false — 딜봇 키로 쇼핑 검색이 안 됨)
    settings.deal.naver_reference.enabled = True
    settings.deal.naver_reference.min_interval_seconds = 0
    _RUNS.clear()
    b = DealBot(settings)
    yield b
    b.db.close()


def _attach_naver(bot: DealBot, api: NaverApi, **cfg: Any) -> None:
    for k, v in cfg.items():
        setattr(bot.settings.deal.naver_reference, k, v)
    bot.naver_shop = NaverShopSearch(api.client(), CLIENT_ID, CLIENT_SECRET)
    bot.naver_ref = NaverPriceReference(bot.naver_shop, bot.settings.deal.naver_reference)


def _history(bot: DealBot, p: Product, prices: list[int], *, days_ago: float = 5) -> None:
    """같은 상품의 지난 가격 기록 (반나절 간격)."""
    for i, price in enumerate(prices):
        q = Product(source=p.source, product_id=p.product_id, shop=p.shop, name=p.name, price=price, url=p.url)
        bot.db.record_observation(q, utcnow() - timedelta(days=days_ago - i * 0.5))


def _queued(bot: DealBot) -> list[Deal]:
    return [it.deal for it in bot.db.pending_items(50)]


# ---------------------------------------------------------------- 1. 네이버 최저가로 판정 (파이프라인)
async def test_goldbox_item_cheaper_than_a_confident_naver_lowest_is_a_deal(bot: DealBot) -> None:
    api = NaverApi([_nitem("<b>탐사</b> 샘물 2L 12개", 9900), _nitem("탐사 샘물 2L 12개 무라벨", 10500, ptype="2", mall="가게A", n=2)])
    _attach_naver(bot, api)
    _RUNS.append([_coupang("1", WATER, 7900)])
    res = await bot.run_collector(bot.collectors[0])
    assert res["status"] == "ok" and res["queued"] == 1
    deal = _queued(bot)[0]
    v = deal.verdict
    assert v.is_deal and v.market_source == NAVER_SOURCE and v.market_price == 9900 and v.below_market_pct == 20.2
    assert "below_naver_price>=12%" in v.reasons
    req = api.requests[0]
    assert req.url.params["exclude"] == "used:rental:cbshop" and req.url.params["query"] == WATER  # 중고·렌탈·해외직구는 검색에서 뺌
    # 글: 근거가 확실할 때만 '네이버' 로 (쿠팡 딜에 '쿠팡 최저가' 라고 쓰지 않음)
    text = bot.publisher.render(deal)
    assert text.splitlines()[0].endswith("네이버보다 20%↓") and "가격비교 : 네이버 최저가 9,900원" in text and "쿠팡 최저가" not in text
    facts = bot.renderer.deal_facts(deal)
    assert facts["evidence"] == "네이버 최저가 9,900원보다 20% 싸요." and facts["ref_label"] == "네이버 최저가"
    # 기억해 둔 근거 (6시간 안에는 다시 안 찾음)
    assert bot.db.get_market_quote(NAVER_QUOTE_KEY + "coupang:1:11", 24)["price"] == 9900


async def test_naver_cheaper_than_coupang_vetoes_even_with_history_evidence(bot: DealBot) -> None:
    p = _coupang("2", WATER, 7900)
    _history(bot, p, [10000, 10000, 10000])  # 평균보다 21% 쌈 → 이력만으로는 특가
    stats = bot.db.price_stats(p.product_id, 30)
    assert bot.evaluator.evaluate(p, stats).is_deal
    api = NaverApi([_nitem(WATER, 7500)])
    _attach_naver(bot, api)
    _RUNS.append([p])
    res = await bot.run_collector(bot.collectors[0])
    assert len(api.requests) == 1 and res["deals"] == 0 and res["queued"] == 0 and _queued(bot) == []
    v = bot.evaluator.evaluate(p, stats, MarketQuote(7500, NAVER_SOURCE, WATER))
    assert not v.is_deal and "above_market_price" in v.reasons


async def test_naver_slightly_higher_falls_back_to_history(bot: DealBot) -> None:
    """네이버보다 0~12% 싼 것은 탈락이 아니라 가격 이력으로 (strict 아님). 10% 미만 차이는 글에 내세우지 않음."""
    p = _coupang("3", WATER, 7900)
    _history(bot, p, [10000, 10000, 10000])
    _attach_naver(bot, NaverApi([_nitem(WATER, 8300)]))
    _RUNS.append([p])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1
    deal = _queued(bot)[0]
    assert deal.verdict.below_market_pct == 4.8 and "naver_diff=4.8%" in deal.verdict.reasons
    assert any(r.startswith("below_30d_avg") for r in deal.verdict.reasons)
    assert "네이버" not in bot.publisher.render(deal)


async def test_ambiguous_naver_results_give_no_evidence_and_are_remembered(bot: DealBot) -> None:
    """같은 상품이 확실하지 않으면(수량 다름·부속품·중고) 근거 없음: 이력 판정 그대로, 24시간 다시 안 찾음."""
    api = NaverApi([_nitem("탐사 샘물 2L 6개", 5000), _nitem("탐사 샘물 2L 12개 중고", 9000), _nitem("탐사 샘물 2L 12개 케이스", 9900)])
    _attach_naver(bot, api)
    p = _coupang("4", WATER, 7900)
    _RUNS.extend([[p], [_coupang("4", WATER, 7900)]])
    for _ in range(2):
        res = await bot.run_collector(bot.collectors[0])
        assert res["status"] == "ok" and res["deals"] == 0  # 이력도 없음 → 특가 아님 (할인을 지어내지 않음)
    assert len(api.requests) == 1  # 첫 판에 한 번 (결과가 있으니 짧은 검색어로 다시 안 찾음), 두 번째 판은 기억으로 안 찾음
    row = bot.db.get_market_quote(NAVER_QUOTE_KEY + p.product_id, 24)
    assert row is not None and row["price"] == 0


# ---------------------------------------------------------------- 2. 같은 상품 판정 (보수적으로)
CFG = NaverReferenceConfig()


@pytest.mark.parametrize(
    ("title", "ok"),
    [
        (WATER, True),
        ("탐사 샘물 2L x 12병", True),  # 12병 = 12개
        ("탐사샘물 2L 12개입 무라벨", True),  # 띄어쓰기 달라도
        ("탐사 샘물 2L 6개", False),  # 수량 다름
        ("탐사 샘물 500ml 12개", False),  # 용량 다름
        ("탐사 샘물 2L 12개 + 6개 증정", False),  # 다른 묶음
        ("탐사 샘물 2L 12개 중고", False),
        ("탐사 샘물 2L 12개 해외직구", False),
        ("탐사 샘물 2L 12개 렌탈", False),
        ("탐사 생수 2L 12개 / 24개 택1", False),  # 옵션 목록
        ("제주 삼다수 2L 12개", False),  # 브랜드 다름
    ],
)
def test_same_item_rules_for_water(title: str, ok: bool) -> None:
    assert same_item(WATER, title, CFG) is ok


@pytest.mark.parametrize(
    ("title", "ok"),
    [
        ("삼성전자 갤럭시 버즈3 프로 SM-R630", True),
        ("삼성 갤럭시 버즈3 프로 케이스", False),  # 부속품
        ("삼성 갤럭시 버즈3 프로 호환 이어팁", False),
        ("삼성 갤럭시 버즈2 프로", False),  # 모델(숫자가 섞인 낱말) 다름
        ("삼성 갤럭시 버즈3 프로 리퍼", False),
    ],
)
def test_same_item_rules_for_models_and_accessories(title: str, ok: bool) -> None:
    assert same_item(BUDS, title, CFG) is ok


def test_naver_lowest_needs_a_catalog_or_several_sellers() -> None:
    """'네이버 최저가보다 N% 싸요' 근거는 최저가가 확인될 때만: 가격비교(카탈로그) 상품, 또는 판매처 여럿 + 낮은 가격순 확인
    (리뷰 F4: 관련도순 40개의 일반 판매글 최솟값은 최저가가 아닐 수 있음)."""
    one = [_nitem(WATER, 9900, ptype="2", mall="가게A")]
    assert naver_lowest(WATER, 7900, one, CFG) == (None, "가격비교 상품 없이 판매글 1곳 — 최저가로 단정 안 함")
    same_seller = one + [_nitem(WATER, 9500, ptype="3", mall="가게A", n=2)]
    assert naver_lowest(WATER, 7900, same_seller, CFG)[0] is None  # 같은 판매처 두 글은 한 곳
    two = one + [_nitem(WATER, 9500, ptype="3", mall="가게B", n=2)]
    q, why = naver_lowest(WATER, 7900, two, CFG)
    assert q is None and why == "가격비교 상품 없이 판매글 2곳 — 최저가로 단정 안 함"  # 관련도순만으로는 최저가라 단정 안 함
    # 낮은 가격순 검색에도 같은 상품이 나오면 그 값까지 넣은 최솟값이 최저가
    q, why = naver_lowest(WATER, 7900, two, CFG, asc_items=[_nitem(WATER, 9300, ptype="2", mall="가게C", n=3)])
    assert q is not None and q.price == 9300 and q.source == NAVER_SOURCE and why == "낮은 가격순 확인"
    # 낮은 가격순에 같은 상품이 없으면(싼 부속품뿐) 확인 못 함
    assert naver_lowest(WATER, 7900, two, CFG, asc_items=[_nitem("탐사 샘물 2L 12개 케이스", 900, ptype="2", mall="X")])[0] is None
    # 카탈로그가 있으면 하나로 충분, 최저가는 맞은 것 전부 중 가장 싼 값 (가장 보수적인 근거)
    q, why = naver_lowest(WATER, 7900, [_nitem(WATER, 9900), _nitem(WATER, 9000, ptype="2", mall="가게C", n=3)], CFG)
    assert q is not None and q.price == 9000 and why == "가격비교 상품"
    # 중고·단종 결과(productType 4~) 는 안 씀
    assert naver_lowest(WATER, 7900, [_nitem(WATER, 9900, ptype="4")], CFG)[0] is None
    # 쿠팡 값의 절반도 안 되는 '같은 상품' 이 있으면 낱개·옵션·다른 상품일 수 있어 근거도 탈락도 없음 (리뷰 D3: 가격비교 상품이어도)
    assert naver_lowest(WATER, 7900, [_nitem(WATER, 3000)], CFG)[0] is None
    q, why = naver_lowest("필립스 소닉케어 전동칫솔 HX3671", 20000,
                          [_nitem("필립스 소닉케어 전동칫솔 HX3671", 9000),
                           _nitem("필립스 소닉케어 전동칫솔 HX3671", 23000, ptype="2", mall="가게A", n=2),
                           _nitem("필립스 소닉케어 전동칫솔 HX3671", 23500, ptype="2", mall="가게B", n=3)], CFG)
    assert q is None and "확실하지 않음" in why  # 9,000원 카탈로그를 빼고 23,000원을 '최저가'로 내세우지 않음
    # 너무 큰 차이(70%↑)는 근거로 안 씀
    q, why = naver_lowest(WATER, 2000, [_nitem(WATER, 9900)], CFG.model_copy(update={"min_price_ratio": 0}))
    assert q is None and "다른 상품" in why
    assert item_title({"title": "<b>탐사</b> 샘물 &amp; 컵"}) == "탐사 샘물 & 컵"


# ---------------------------------------------------------------- 3. 키 없음·401·429·한도·캐시
async def test_missing_keys_turn_the_comparison_off_and_log_once(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    assert not bot.naver_shop.configured and bot.naver_ref is not None  # 테스트 환경엔 키가 없다
    p = _coupang("5", WATER, 7900)
    _history(bot, p, [10000, 10000, 10000])
    _RUNS.extend([[p], [_coupang("6", WATER, 7900)]])
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            assert (await bot.run_collector(bot.collectors[0]))["status"] == "ok"
    assert caplog.text.count("네이버 최저가 비교는 꺼짐") == 1
    assert _queued(bot)[0].verdict.market_source is None  # 이력으로만 판정
    checks = dict((text, ok) for ok, text in await _self_check_lines(bot))
    assert any("네이버 최저가 비교: 꺼짐 (NAVER_CLIENT_ID" in t for t in checks)


async def _self_check_lines(bot: DealBot) -> list[tuple[bool | None, str]]:
    return await bot.self_check()


async def test_401_turns_it_off_once_and_the_run_keeps_going(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    api = NaverApi(status=401)
    _attach_naver(bot, api)
    _RUNS.append([_coupang("7", WATER, 7900), _coupang("8", "곰곰 무농약 대추방울토마토 750g", 6900)])
    with caplog.at_level(logging.INFO):
        res = await bot.run_collector(bot.collectors[0])
    assert res["status"] == "ok" and len(api.requests) == 1 and bot.naver_shop.disabled_reason == "HTTP 401"
    assert caplog.text.count("HTTP 401") == 1 and "네이버 최저가 비교 모두" in caplog.text
    assert CLIENT_SECRET not in caplog.text and CLIENT_ID not in caplog.text
    assert bot.db.get_market_quote(NAVER_QUOTE_KEY + "coupang:7:71", 24) is None  # 꺼진 것은 '못 찾음'으로 기억하지 않음


async def test_429_pauses_and_is_not_remembered() -> None:
    api = NaverApi(status=429)
    clock = [1000.0]
    ref = NaverPriceReference(NaverShopSearch(api.client(), CLIENT_ID, CLIENT_SECRET), _nref_cfg(), clock=lambda: clock[0])
    run = ref.new_run()
    assert await ref.lookup(_coupang("9", WATER, 7900), run) is None and not ref.last_no_match
    assert await ref.lookup(_coupang("10", WATER, 7900), run) is None  # 쉬는 중: 부르지 않음
    assert len(api.requests) == 1 and run.failed == 1 and run.skipped == 1
    clock[0] += 601
    api.status = 200
    assert await ref.lookup(_coupang("10", WATER, 7900), ref.new_run()) is None and len(api.requests) == 2  # 쉬고 나서 다시
    assert ref.last_no_match  # 이번엔 실제로 찾았는데 없었음 → 부르는 쪽이 기억


async def test_hourly_and_per_run_bounds() -> None:
    api = NaverApi([_nitem(WATER, 9900)])
    clock = [0.0]
    ref = NaverPriceReference(NaverShopSearch(api.client(), CLIENT_ID, CLIENT_SECRET),
                              _nref_cfg(max_lookups_per_run=2, max_lookups_per_hour=3), clock=lambda: clock[0])
    run = ref.new_run()
    got = [await ref.lookup(_coupang(str(i), WATER, 7900), run) for i in range(3)]
    assert [q is not None for q in got] == [True, True, False] and run.calls == 2 and run.skipped == 1
    run2 = ref.new_run()
    assert await ref.lookup(_coupang("4", WATER, 7900), run2) is not None
    assert await ref.lookup(_coupang("5", WATER, 7900), run2) is None and len(api.requests) == 3  # 한 시간 3번
    clock[0] += 3601
    assert await ref.lookup(_coupang("5", WATER, 7900), ref.new_run()) is not None
    # 한 판의 시간 상한: 넘으면 다음 판에 (5분 수집 루프를 막지 않게)
    slow = LookupRun(10, 45, clock=lambda: clock[0])
    clock[0] += 46
    assert not slow.allows()


async def test_interval_between_calls_is_kept_within_the_run_time() -> None:
    api = NaverApi([_nitem(WATER, 9900)])
    clock, slept = [0.0], []

    async def sleep(s: float) -> None:
        slept.append(s)
        clock[0] += s

    ref = NaverPriceReference(NaverShopSearch(api.client(), CLIENT_ID, CLIENT_SECRET),
                              _nref_cfg(min_interval_seconds=0.5), clock=lambda: clock[0], sleep=sleep)
    run = ref.new_run()
    for i in range(3):
        assert await ref.lookup(_coupang(str(i), WATER, 7900), run) is not None
    assert slept == [0.5, 0.5]


async def test_naver_quotes_are_cached_per_product(bot: DealBot) -> None:
    api = NaverApi([_nitem(WATER, 9900)])
    _attach_naver(bot, api)
    p = _coupang("11", WATER, 7900)
    run = bot.naver_ref.new_run()
    assert (await bot._naver_quote(p, run)).price == 9900
    assert (await bot._naver_quote(p, run)).price == 9900 and len(api.requests) == 1  # 6시간 안: 기억
    # 7시간 지난 값은 글에 쓰지 않고 다시 찾음
    key = NAVER_QUOTE_KEY + p.product_id
    bot.db.set_market_quote(key, price=9900, source=NAVER_SOURCE, title=WATER, url=None, now=utcnow() - timedelta(hours=7))
    api.default = [_nitem(WATER, 8800)]
    assert (await bot._naver_quote(p, run)).price == 8800 and len(api.requests) == 2
    # 못 찾음은 24시간 기억
    api.default = []
    other = _coupang("12", "아무개 상품 1개", 7900)
    assert await bot._naver_quote(other, run) is None and await bot._naver_quote(other, run) is None
    assert len(api.requests) == 3  # 못 찾은 상품은 한 번만 찾고 기억


# ---------------------------------------------------------------- 4. 판정 규칙 (평가기)
def test_evaluator_naver_rule_thresholds() -> None:
    ev = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False), min_history_samples=3, history_days=30))
    p = _coupang("1", WATER, 8000)
    q = lambda price: MarketQuote(price=price, source=NAVER_SOURCE, title=WATER)  # noqa: E731
    v = ev.evaluate(p, PriceStats(), q(10000))  # 20% 쌈
    assert v.is_deal and v.reasons == ["below_naver_price>=12%"] and v.market_source == "naver"
    v = ev.evaluate(p, PriceStats(), q(8500))  # 5.9%: 근거 부족 → 이력도 없으니 특가 아님 (탈락 사유는 남김)
    assert not v.is_deal and "naver_diff=5.9%" in v.reasons
    v = ev.evaluate(p, PriceStats(), q(7900))
    assert not v.is_deal and v.reasons == ["above_market_price"]
    strict = DealEvaluator(DealConfig(interest=InterestConfig(enabled=False), naver_reference=NaverReferenceConfig(strict=True)))
    assert strict.evaluate(p, PriceStats(), q(8500)).reasons == ["below_naver_price<12%"]
    # 쿠팡 시중가(다른 몰 딜)는 예전 기준 그대로 (20%, strict)
    toss = Product(source="ppomppu", product_id="toss:1", shop="toss", name=WATER, price=8000, url="u")
    assert ev.evaluate(toss, PriceStats(), MarketQuote(9500, "coupang", WATER)).reasons == ["below_coupang_price<20%"]


def test_evaluator_new_low_rule() -> None:
    cfg = DealConfig(interest=InterestConfig(enabled=False), min_history_samples=3, min_below_average_pct=15, new_low_below_avg_pct=10)
    ev = DealEvaluator(cfg)
    stats = PriceStats(count=6, avg=10000, min=9000, max=11000, first_seen_at=utcnow() - timedelta(days=8))
    v = ev.evaluate(_coupang("1", WATER, 8900), stats)  # 평균 대비 11%↓ + 기록 최저(9,000)보다 쌈
    assert v.is_deal and v.reasons == ["30d_low&below_avg>=10%"] and v.score == 11.0
    assert not ev.evaluate(_coupang("1", WATER, 9100), stats).is_deal  # 기록 최저보다 비쌈
    assert not DealEvaluator(cfg.model_copy(update={"new_low_below_avg_pct": None})).evaluate(_coupang("1", WATER, 8900), stats).is_deal
    # 기록이 적으면 안 씀
    assert not ev.evaluate(_coupang("1", WATER, 8900), PriceStats(count=2, avg=10000, min=9000)).is_deal


def test_goldbox_discount_alone_still_works_if_a_discount_field_appears() -> None:
    ev = DealEvaluator(load_settings(ROOT / "config.yaml", load_env=False).deal)
    gold = _coupang("1", "스파클 생수 2L 24개", 7400, discount_rate=55)
    assert ev.evaluate(gold, PriceStats()).is_deal
    # 그래도 네이버 최저가보다 비싸면 탈락
    assert not ev.evaluate(gold, PriceStats(), MarketQuote(7000, NAVER_SOURCE, "스파클 생수 2L 24개")).is_deal


# ---------------------------------------------------------------- 5. 글·한줄평: '네이버' 는 확실한 근거가 있을 때만
def _renderer() -> TemplateRenderer:
    return TemplateRenderer(ROOT / "templates", "Asia/Seoul")


def test_templates_name_the_compared_market() -> None:
    r = _renderer()
    shop = ShopRegistry().get("coupang")
    naver = Deal(_coupang("1", WATER, 7900), DealVerdict(is_deal=True, market_price=9900, market_source="naver", below_market_pct=20.2))
    th = r.render_deal(naver, "https://link.coupang.com/a/x", shop=shop, template="deal_threads.j2", autoescape=False)
    assert "네이버 최저가는 9,900원" in th and "쿠팡 최저가" not in th
    blog = r.render_deal(naver, "https://link.coupang.com/a/x", shop=shop, template="deal_blog.j2", autoescape=False)
    assert "네이버 최저가 9,900원 대비 20% 저렴합니다." in blog
    kakao = r.render_deal(naver, "https://link.coupang.com/a/x", shop=shop, template="deal_kakao.j2", autoescape=False)
    assert "(네이버 최저가 9,900원보다 20%↓)" in kakao
    # 쿠팡 시중가(다른 몰 딜)·예전 기록(source 없음)은 그대로 '쿠팡'
    toss = Deal(Product(source="ppomppu", product_id="toss:1", shop="toss", name=WATER, price=7900, url="u"),
                DealVerdict(is_deal=True, market_price=9900, below_market_pct=20.2))
    assert r.deal_facts(toss)["compare"] == "쿠팡 최저가 9,900원"
    # 근거가 없으면 비교 말이 없음
    plain = Deal(_coupang("1", WATER, 7900), DealVerdict(is_deal=True))
    assert r.deal_facts(plain)["compare"] is None and "네이버" not in r.render_deal(plain, "https://link.coupang.com/a/x", shop=shop)


def test_commentary_cannot_invent_a_naver_comparison() -> None:
    assert check_comment("네이버보다 싸서 물 많이 마시는 집에 좋아요.", "병당 658원 꼴 / 무료 로켓배송", WATER) is None
    assert check_comment("다른 몰보다 싸서 좋아요.", "병당 658원 꼴", WATER) is None
    assert check_comment("최저가라 지금 담아 두기 좋아요.", "병당 658원 꼴", WATER) is None
    facts = "네이버 최저가 9,900원보다 20% 싸요. / 병당 658원 꼴"
    assert check_comment("네이버보다 싸서 물 많이 마시는 집에 좋아요.", facts, WATER)
    assert THREAD_BANNED.search("네이버보다 20% 쌈")


# ---------------------------------------------------------------- 6. 카테고리 돌아가며 + 예산
class FakeCoupang:
    def __init__(self, budget: ApiBudget | None = None) -> None:
        self.budget = budget
        self.calls: list[int] = []

    async def best_category(self, cid: int, *, limit: int = 50, image_size: str | None = None) -> list[dict[str, Any]]:
        if self.budget is not None:
            if not self.budget.available("bestcategory"):
                raise CoupangRateLimited("budget")
            self.budget.record("bestcategory")
        self.calls.append(cid)
        return [{"productId": cid, "productName": f"상품 {cid}", "productPrice": 9900, "productUrl": f"https://link.coupang.com/re/{cid}"}]


def _category_collector(settings: Settings, coupang: FakeCoupang, db: Any, **opts: Any):  # type: ignore[no-untyped-def]
    options = {"category_ids": [1016, 1012, 1014, 1013, 1010, 1024, 1029, 1011, 1015], "categories_per_run": 3,
               "keep_free_calls": 1, "delay_between_categories_seconds": 0, "limit": 30}
    options.update(opts)
    ctx = CollectorContext(settings=settings, http=httpx.AsyncClient(), db=db, coupang=coupang)  # type: ignore[arg-type]
    return build_collector(CollectorConfig(name="category_best", type="coupang_category_best", options=options), ctx)


async def test_categories_rotate_a_slice_per_run_and_survive_a_restart(settings: Settings, db: Any) -> None:
    fake = FakeCoupang()
    c = _category_collector(settings, fake, db)
    for _ in range(2):
        await c.collect()
    assert fake.calls == [1016, 1012, 1014, 1013, 1010, 1024]
    c2 = _category_collector(settings, fake, db)  # 재시작해도 이어서
    await c2.collect()
    await c2.collect()
    assert fake.calls[6:] == [1029, 1011, 1015, 1016, 1012, 1014]
    assert db.kv_get(CURSOR_KV + "category_best") == "3"
    # categories_per_run 이 없으면 예전처럼 전부
    fake2 = FakeCoupang()
    await _category_collector(settings, fake2, db, category_ids=[1016, 1012], categories_per_run=0).collect()
    assert sorted(fake2.calls) == [1012, 1016]


async def test_categories_leave_budget_for_goldbox_market_and_deeplink(settings: Settings, db: Any) -> None:
    budget = ApiBudget(10, reserve={"deeplink": 3}, caps={"deeplink": 7})
    for kind in ("goldbox", "search", "search", "search"):  # 골드박스 1 + 시중가 조회 3 → 공용 몫 7 중 3 남음
        budget.record(kind)
    fake = FakeCoupang(budget)
    c = _category_collector(settings, fake, db)
    got = await c.collect()
    assert fake.calls == [1016, 1012] and len(got) == 2  # 1회는 남겨 둠 (keep_free_calls)
    assert budget.remaining("general") == 1 and budget.available("deeplink") and budget.remaining("deeplink") == 4
    # 공용 몫이 다 차면: 하나도 못 보고 '건너뜀'(고장 아님), 다음 판은 못 본 카테고리부터
    budget.record("search")
    with pytest.raises(CoupangRateLimited):
        await c.collect()
    budget._calls.clear()
    await c.collect()
    assert fake.calls[2:] == [1014, 1013, 1010]


def test_config_keeps_coupang_calls_under_the_hourly_budget() -> None:
    s = load_settings(ROOT / "config.yaml", load_env=False)
    cat = next(c for c in s.collectors if c.name == "category_best")
    gold = next(c for c in s.collectors if c.name == "goldbox")
    per_run = int(cat.options["categories_per_run"])
    assert 1 <= per_run <= 3 and len(cat.options["category_ids"]) >= 9
    assert set(cat.options["category_ids"]) <= {1001, 1002, 1010, 1011, 1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019, 1020,
                                                1021, 1024, 1029, 1030}
    pool = s.coupang.max_calls_per_hour - s.coupang.deeplink_reserve
    # 한 시간 최악: 골드박스·카테고리 한 판씩 + 시중가 조회 한도 ≤ 공용 몫 (딥링크 예약분은 그대로)
    worst = -(-60 // gold.interval_minutes) + per_run * -(-60 // cat.interval_minutes) + s.deal.market.max_checks_per_hour
    assert worst <= pool
    avg = 60 / gold.interval_minutes + per_run * 60 / cat.interval_minutes + s.deal.market.max_checks_per_hour
    assert avg <= s.coupang.max_calls_per_hour / 2  # 평균은 한도의 절반 이하
    nr = s.deal.naver_reference
    assert not nr.enabled  # 10/13 주인 결정: 네이버 쇼핑 검색이 될 때까지 끔
    assert set(nr.sources) == {"goldbox", "category_best"} and 10 <= nr.min_below_pct <= 15  # 켜면 이 값으로


def test_goldbox_items_without_a_rank_still_get_judged_on_price() -> None:
    """골드박스 응답에 순위 칸이 없어도 관심도 게이트에서 떨어지지 않는다 (가격 근거로만 판정)."""
    ev = DealEvaluator(load_settings(ROOT / "config.yaml", load_env=False).deal)
    p = _coupang("1", WATER, 7900, rank=None)
    assert ev.interest_signal(p) == "always"
    v = ev.evaluate(p, PriceStats(), MarketQuote(9900, NAVER_SOURCE, WATER))
    assert v.is_deal and v.reasons == ["below_naver_price>=12%"]
    assert not ev.evaluate(p, PriceStats()).is_deal  # 근거가 없으면 특가 아님
    # 카테고리 베스트는 순위(30위 안)로 그대로
    assert ev.interest_signal(_coupang("2", WATER, 7900, source="category_best", rank=5)) == "rank<=30"


# ---------------------------------------------------------------- 7. 안 올린 이유 보이기 · 같은 상품 다시 안 올림
async def test_dedup_still_blocks_reposting_within_dedup_days(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    api = NaverApi([_nitem(WATER, 9900)])
    _attach_naver(bot, api)
    p = _coupang("20", WATER, 7900)
    bot.db.record_post(Deal(p, DealVerdict(is_deal=True)), channel_id="c", message_id=1, dry_run=True,
                       now=utcnow() - timedelta(days=2))
    _RUNS.append([_coupang("20", WATER, 7900)])
    with caplog.at_level(logging.INFO):
        res = await bot.run_collector(bot.collectors[0])
    assert res["queued"] == 0 and api.requests == []  # 이미 올린 상품은 네이버 호출도 안 씀
    # 이력이 있어 특가로 판정되면 이유가 한 줄로 보임
    _history(bot, p, [10000, 10000, 10000])
    _RUNS.append([_coupang("20", WATER, 7900)])
    with caplog.at_level(logging.INFO):
        res = await bot.run_collector(bot.collectors[0])
    assert res["deals"] == 1 and res["queued"] == 0
    assert "collector 'goldbox' deals not queued: already_posted=1" in caplog.text
    # dedup_days(7일)가 지나면 다시 올릴 수 있음
    bot.db._conn.execute("UPDATE posts SET posted_at = ?", (to_iso(utcnow() - timedelta(days=8)),))
    _RUNS.append([_coupang("20", WATER, 7900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1


async def test_skip_reasons_reach_the_evening_summary_quietly(bot: DealBot) -> None:
    posted = _coupang("30", WATER, 7900)
    _history(bot, posted, [10000, 10000, 10000])
    bot.db.record_post(Deal(posted, DealVerdict(is_deal=True)), channel_id="c", message_id=1, dry_run=True,
                       now=utcnow() - timedelta(days=2))  # 요약 기간(하루)보다 앞서 올림 → 그 뒤로는 '이미 올린 상품'
    fresh = _coupang("31", "곰곰 무농약 대추방울토마토 750g", 6900)
    _history(bot, fresh, [9000, 9000, 9000])
    # 같은 글을 여러 번 다시 봐도 상품 하나로 셈
    for _ in range(3):
        _RUNS.append([_coupang("30", WATER, 7900), _coupang("31", "곰곰 무농약 대추방울토마토 750g", 6900)])
        await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}  # 대추방울토마토 (두 번째 판부터는 이미 대기 중 — 요약엔 안 셈)
    # 줄 섰다가 발행 전에 품절로 빠진 것도 같이
    item = bot.db.pending_items(5)[0]
    bot.db.update_queue_item(item.id, status="skipped", error="sold out (page, before publish)")
    counts = bot.skipped_deal_counts(utcnow() - timedelta(days=1))
    assert counts == {"이미 올린 상품": 1, "품절": 1}
    sent: list[tuple[str, str]] = []
    bot.notifier.summary = lambda cat, text, replace=False: sent.append((cat, text))  # type: ignore[method-assign]
    pings: list[Any] = []
    bot.notifier.send = lambda *a, **k: pings.append(a)  # type: ignore[method-assign]
    text = await bot.owner_summary()
    assert text.split(" 핫딜봇")[0].endswith(" 특가인데 안 올린 딜은 이미 올린 상품 1개, 품절 1개예요.")
    assert sent[0][0] == "day" and "특가인데 안 올린 딜" in sent[0][1] and pings == []  # 저녁 요약 줄뿐, 따로 알림 없음
    # 오래된 기록은 하루 남짓 뒤 지워짐
    bot._remember_skips({}, utcnow() + timedelta(days=2))
    assert bot.db.kv_get(SKIP_KV) == "{}"


async def test_goldbox_logs_response_field_names_once(settings: Settings, db: Any, monkeypatch: pytest.MonkeyPatch,
                                                      caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(CoupangGoldboxCollector, "_keys_logged", False)

    class Gold:
        budget = None

        async def goldbox(self, **_kw: Any) -> list[dict[str, Any]]:
            return [{"productId": 1, "productName": "비밀 아닌 상품명", "productPrice": 7900,
                     "productUrl": "https://link.coupang.com/re/1", "rank": 1, "isRocket": True}]

    ctx = CollectorContext(settings=settings, http=httpx.AsyncClient(), db=db, coupang=Gold())  # type: ignore[arg-type]
    c = build_collector(CollectorConfig(name="goldbox", type="coupang_goldbox"), ctx)
    with caplog.at_level(logging.INFO):
        await c.collect()
        await c.collect()
    lines = [r.getMessage() for r in caplog.records if "goldbox response fields" in r.getMessage()]
    assert lines == ["goldbox response fields (first item): isRocket, productId, productName, productPrice, productUrl, rank"]
    assert "비밀 아닌 상품명" not in lines[0]  # 값은 안 남김


def test_admin_reasons_name_the_naver_evidence() -> None:
    from dealbot.monitoring.admin import humanize_reasons

    assert humanize_reasons(["interest:rank<=30", "below_naver_price>=12%"]) == "관심도 통과(순위 30위 안) · 네이버 최저가보다 12% 이상 쌈"
    assert humanize_reasons(["naver_diff=4.8%", "below_30d_avg>=15%"]) == "네이버 최저가보다 4.8% 쌈(기준 미만) · 최근 30일 평균가보다 15% 이상 쌈"
    assert humanize_reasons(["30d_low&below_avg>=10%"]) == "최근 30일 중 제일 싸고 평균가보다 10% 이상 쌈"


# ---------------------------------------------------------------- 8. 리뷰 10/13: 같은 상품을 양쪽에서 (더 비싼 갈래를 '네이버 최저가'로 잡지 않음)
@pytest.mark.parametrize(
    ("name", "title", "ok"),
    [
        # 리뷰 F1: 쿠팡 낱말이 다 들어 있는 윗급·다른 갈래는 다른 상품
        ("삼성전자 갤럭시 버즈3, 실버", "삼성전자 갤럭시 버즈3 프로 실버", False),
        ("삼성전자 갤럭시 버즈3, 실버", "삼성전자 갤럭시 버즈3프로 실버", False),  # 모델 낱말은 통째로 ('버즈3' ≠ '버즈3프로')
        ("삼성전자 갤럭시 버즈3, 실버", "삼성전자 갤럭시 버즈3 실버 SM-R530", True),
        ("삼성전자 갤럭시 버즈3, 실버", "삼성전자 갤럭시 버즈 3 실버", True),  # 띄어 쓴 번호는 같은 것
        ("삼성전자 갤럭시 버즈3 프로, 실버", "삼성전자 갤럭시 버즈3 실버", False),  # 거꾸로도
        ("Apple 아이폰15 128GB 블랙", "Apple 아이폰15 프로 128GB 블랙", False),
        ("Apple 아이폰15 128GB 블랙", "Apple 아이폰15 128GB 블랙 자급제", True),
        ("농심 신라면, 120g, 40개", "농심 신라면 블랙 120g 40개", False),
        ("농심 신라면, 120g, 40개", "농심 신라면블랙 120g 40개", False),
        ("빙그레 바나나맛우유, 240ml, 8개", "빙그레 바나나맛우유 라이트 240ml 8개", False),
        ("빙그레 바나나맛우유, 240ml, 8개", "빙그레 바나나맛우유 240ml 8개", True),
        # 수량 규칙이 못 읽는 묶음 단위·괄호 안 개수
        ("센트룸 멀티비타민 우먼, 60정, 1개", "센트룸 멀티비타민 우먼 60정 3통", False),
        ("센트룸 멀티비타민 우먼, 60정, 1개", "센트룸 멀티비타민 우먼 60정 1통", True),
        ("깨끗한나라 순수 3겹 롤화장지 30m 30 롤", "깨끗한나라 순수 3겹 롤화장지 30m 24 롤", False),
        ("CJ 햇반, 210g, 24개", "CJ 햇반 210g 24개 (스팸 200g 1개 증정)", False),
        ("CJ 햇반, 210g, 24개", "CJ 햇반 210g 24개 스팸증정", False),
        ("CJ 햇반, 210g, 24개", "CJ 햇반 210g 24개 (12개x2)", True),  # 풀어 쓴 개수는 같은 것
        ("코카콜라 제로, 355ml, 24개", "코카콜라 제로 355ml 24캔 (2박스)", False),
        ("코카콜라 제로, 355ml, 24개", "코카콜라 제로 355ml 24캔", True),
        ("곰곰 국산 감자, 1kg(5~7입), 1개", "곰곰 국산 감자 1kg(5~7입) 1개", True),  # 쿠팡 이름에도 있는 괄호는 같은 설명
        ("삼성전자 갤럭시 버즈3, 실버", "삼성전자 갤럭시 버즈3,실버", True),  # 쉼표로 붙은 낱말도 낱말 경계
        ("설화수 자음생크림 소프트, 50ml, 1개", "설화수 자음생크림 소프트 50ml 기획세트", False),
        # 띄어 쓴 세대·모델 번호 (리뷰 F3: '에어팟 4' 가 '에어팟 2세대' 에 맞음)
        ("애플 에어팟 4", "애플 에어팟 2세대", False),
        ("애플 에어팟 4", "애플 에어팟 4 액티브 노이즈 캔슬링", True),
        ("애플 에어팟 4", "애플 에어팟4", True),
        # 부속품·소모품·개봉품·해외판·정기배송 (리뷰 F3)
        ("샤오미 미에어 공기청정기 4 프로", "샤오미 미에어 공기청정기 4 프로 정품 필터", False),
        ("다이슨 V12 디텍트 슬림 무선청소기", "다이슨 V12 디텍트 슬림 무선청소기 배터리", False),
        ("다이슨 V12 디텍트 슬림 무선청소기", "다이슨 V12 디텍트 슬림 무선청소기 충전기", False),
        ("필립스 소닉케어 전동칫솔 HX6859", "필립스 소닉케어 전동칫솔 HX6859 전용 칫솔모", False),
        ("로지텍 MX Master 3S 무선 마우스, 그래파이트", "로지텍 MX Master 3S 무선 마우스 그래파이트 단순개봉", False),
        ("로지텍 MX Master 3S 무선 마우스, 그래파이트", "로지텍 MX Master 3S 무선 마우스 그래파이트 미개봉 새상품", True),
        ("샤오미 미지아 무선 진공청소기 G10", "샤오미 미지아 무선 진공청소기 G10 글로벌버전", False),
        ("브리타 정수기 마렐라 XL 3.5L", "브리타 정수기 마렐라 XL 3.5L 정기배송", False),
        ("소니 WH-1000XM5 블루투스 헤드폰", "소니 WH-1000XM5 무선 헤드폰 블랙", False),  # 색(블랙)이 한쪽에만
        ("소니 WH-1000XM5 블루투스 헤드폰, 블랙", "소니 WH-1000XM5 무선 블루투스 헤드폰 블랙", True),  # '헤드폰' 은 부속 '헤드' 가 아님
    ],
)
def test_same_item_is_checked_both_ways(name: str, title: str, ok: bool) -> None:
    assert same_item(name, title, CFG) is ok


async def test_buds3_is_not_posted_against_a_buds3_pro_price(bot: DealBot) -> None:
    """리뷰 F1 재현: 진짜 버즈3 판매글은 빠지고(브랜드 표기·케이스 증정) 버즈3 프로만 맞던 것 → 근거 없음 (거짓 '33% 싸요' 없음)."""
    def n(title: str, price: int, ptype: str, mall: str, i: int) -> dict[str, Any]:
        return {**_nitem(title, price, ptype=ptype, mall=mall, n=i), "category1": "디지털/가전"}

    api = NaverApi([n("삼성 갤럭시 버즈3 실버 SM-R530", 165000, "1", "네이버", 1),
                    n("삼성전자 갤럭시 버즈3 실버 케이스 증정", 169000, "2", "A", 2),
                    n("삼성전자 갤럭시 버즈3 프로 실버", 239000, "1", "네이버", 3),
                    n("삼성전자 갤럭시 버즈3 프로 실버 SM-R630", 245000, "3", "B", 4)])
    _attach_naver(bot, api)
    _RUNS.append([_coupang("9", "삼성전자 갤럭시 버즈3, 실버", 159000)])
    res = await bot.run_collector(bot.collectors[0])
    assert res["status"] == "ok" and res["deals"] == 0 and _queued(bot) == []
    assert bot.db.get_market_quote(NAVER_QUOTE_KEY + "coupang:9:91", 24)["price"] == 0  # 확실한 같은 상품 없음으로 기억


# ---------------------------------------------------------------- 9. 리뷰 F2: 기억한 네이버 값으로도 식품 기준이 같게
@pytest.mark.food_rule
async def test_food_rule_holds_on_the_second_run_from_the_cached_naver_quote(bot: DealBot) -> None:
    api = NaverApi([_nitem("제주 삼다수 2L 6개", 7900)])  # category1 '식품'
    _attach_naver(bot, api)
    for _ in range(2):  # 골드박스 2시간마다 — 두 번째 판은 기억한 값(6시간 안)으로 판정
        _RUNS.append([_coupang("77", "제주 삼다수, 2L, 6개", 5490)])
        res = await bot.run_collector(bot.collectors[0])
        assert res["deals"] == 0 and _queued(bot) == []  # 30.5% 쌈: 식품은 50% 기준
    assert len(api.requests) == 1  # 두 번째 판은 기억한 값으로
    row = bot.db.get_market_quote(NAVER_QUOTE_KEY + "coupang:77:771", 24)
    assert row["meta"] and "식품" in row["meta"]
    q = await bot._naver_quote(_coupang("77", "제주 삼다수, 2L, 6개", 5490), bot.naver_ref.new_run())
    assert q is not None and q.category and q.category.startswith("식품")
    v = bot.evaluator.evaluate(_coupang("77", "제주 삼다수, 2L, 6개", 5490), PriceStats(), q)
    assert not v.is_deal and "food_below_ref<50%" in v.reasons


async def test_cached_coupang_market_quote_keeps_the_category(bot: DealBot) -> None:
    """다른 몰 딜의 쿠팡 시중가도 기억한 값에 분류를 같이 (식품 판정이 처음 찾은 값과 같게)."""
    calls: list[str] = []

    class Market:
        last_no_match = False

        async def lookup(self, p: Product) -> MarketQuote:
            calls.append(p.product_id)
            return MarketQuote(price=9900, source="coupang", title="제주 삼다수 2L 6개", url="u", category="식품>생수/음료")

    bot.market = Market()  # type: ignore[assignment]
    p = Product(source="ppomppu", product_id="toss:5", shop="toss", name="제주 삼다수 2L 6개", price=5490, url="u")
    fresh = await bot._market_quote(p)
    cached = await bot._market_quote(p)
    assert calls == ["toss:5"] and fresh.category == cached.category == "식품>생수/음료"


# ---------------------------------------------------------------- 10. 리뷰 F3: 탈락은 확실할 때만 (판매글 한 곳의 싼 값으로 이력 특가를 막지 않음)
DOWNY = "다우니 엑스퍼트 실내건조 섬유유연제 프레시클린, 2.6L, 4개"
DOWNY_N = "다우니 엑스퍼트 실내건조 섬유유연제 프레시클린 2.6L 4개"


async def test_a_history_deal_survives_one_cheap_outlier_listing(bot: DealBot) -> None:
    api = NaverApi([_nitem(DOWNY_N, 52900), _nitem(DOWNY_N, 28900, ptype="2", mall="옵션가게", n=2)])
    _attach_naver(bot, api)
    p = _coupang("88", DOWNY, 39900)
    _history(bot, p, [49900, 49900, 52900, 49900, 49900, 44900])
    _RUNS.append([_coupang("88", DOWNY, 39900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1
    v = _queued(bot)[0].verdict
    assert any(r.startswith("below_30d_avg") for r in v.reasons) and v.market_source is None  # 네이버 비교 말 없음


async def test_two_cheaper_sellers_or_a_catalog_still_veto(bot: DealBot) -> None:
    api = NaverApi([_nitem(DOWNY_N, 52900), _nitem(DOWNY_N, 28900, ptype="2", mall="가게A", n=2),
                    _nitem(DOWNY_N, 29500, ptype="2", mall="가게B", n=3)])  # 두 곳 모두 배송비 3,000원을 더해도 쌈
    _attach_naver(bot, api)
    p = _coupang("89", DOWNY, 39900)
    _history(bot, p, [49900, 49900, 52900, 49900, 49900, 44900])
    _RUNS.append([_coupang("89", DOWNY, 39900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 0
    q, why = naver_lowest(DOWNY, 39900, api.default, CFG)
    assert q is not None and q.price == 29500 + 3000 and why.startswith("네이버가 더 쌈")
    # 가격비교 상품 최저가가 쿠팡 값 이하면 하나로 탈락
    q, _ = naver_lowest(DOWNY, 39900, [_nitem(DOWNY_N, 38900)], CFG)
    assert q is not None and q.price == 38900


def test_shipping_allowance_and_coupang_listings_on_naver() -> None:
    ramen, ramen_n = "오뚜기 진라면 매운맛, 120g, 20개", "오뚜기 진라면 매운맛 120g 20개"
    # 일반 판매글 11,500·11,800원(배송비 따로)은 쿠팡 11,900원 로켓배송보다 싸다고 보지 않음 → 근거 없음(이력으로)
    two = [_nitem(ramen_n, 11500, ptype="2", mall="라면나라"), _nitem(ramen_n, 11800, ptype="2", mall="식품창고", n=2)]
    assert naver_lowest(ramen, 11900, two, CFG)[0] is None
    # 쿠팡이 네이버에 낸 판매글은 '네이버 값'이 아님
    with_coupang = [_nitem(WATER, 7500, ptype="3", mall="쿠팡"), _nitem(WATER, 9900)]
    q, _ = naver_lowest(WATER, 7900, with_coupang, CFG)
    assert q is not None and q.price == 9900


async def test_airpods_4_is_not_vetoed_by_an_older_generation(bot: DealBot) -> None:
    api = NaverApi([_nitem("애플 에어팟 2세대", 129000, n=2), _nitem("애플 에어팟 4", 199000, n=1)])
    _attach_naver(bot, api)
    _RUNS.append([_coupang("10", "애플 에어팟 4", 159000)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1
    v = _queued(bot)[0].verdict
    assert v.market_price == 199000 and "below_naver_price>=12%" in v.reasons


# ---------------------------------------------------------------- 11. 리뷰 F4: 판매글만 맞으면 낮은 가격순으로 최저가 확인
PAN = "테팔 매직핸즈 티타늄 프라이팬 28cm"


async def test_general_listings_need_a_lowest_price_check_before_a_claim(bot: DealBot) -> None:
    sim = [_nitem(PAN, 39900, ptype="2", mall="가게A"), _nitem(f"{PAN} 정품", 41900, ptype="2", mall="가게B", n=2)]
    # 낮은 가격순 1쪽에 더 싼 같은 상품(28,900원)이 있음 → 쿠팡 29,900원은 네이버 최저가보다 싸지 않음: 근거 없음
    api = NaverApi(sim, asc=[_nitem("매직핸즈 손잡이", 5900, ptype="2", mall="X", n=9), _nitem(PAN, 28900, ptype="2", mall="가게C", n=3)])
    _attach_naver(bot, api)
    _RUNS.append([_coupang("40", PAN, 29900)])
    assert (await bot.run_collector(bot.collectors[0]))["deals"] == 0
    assert [r.url.params["sort"] for r in api.requests] == ["sim", "asc"]
    # 낮은 가격순에서도 같은 상품의 제일 싼 값이 39,900원 → 그게 최저가: '네이버 최저가 39,900원보다 25% 싸요'
    api2 = NaverApi(sim, asc=[_nitem("매직핸즈 손잡이", 5900, ptype="2", mall="X", n=9), _nitem(PAN, 39900, ptype="2", mall="가게A")])
    _attach_naver(bot, api2)
    _RUNS.append([_coupang("41", PAN, 29900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1
    deal = _queued(bot)[0]
    assert deal.verdict.market_price == 39900 and bot.renderer.deal_facts(deal)["evidence"] == "네이버 최저가 39,900원보다 25% 싸요."
    # 확인 검색을 못 하면(한 판 한도) 판매글만으로는 근거 없음, 기억도 안 함 (다음 판에 다시)
    api3 = NaverApi(sim, asc=[_nitem(PAN, 39900, ptype="2", mall="가게A")])
    _attach_naver(bot, api3, max_lookups_per_run=1)
    _RUNS.append([_coupang("42", PAN, 29900)])
    assert (await bot.run_collector(bot.collectors[0]))["deals"] == 0 and len(api3.requests) == 1
    assert bot.db.get_market_quote(NAVER_QUOTE_KEY + "coupang:42:421", 24) is None


# ---------------------------------------------------------------- 12. 리뷰: 다시 못 찾아도 아는 탈락은 지킴 · 차례
async def test_a_known_naver_veto_is_kept_when_the_refresh_fails(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    p = _coupang("2", WATER, 7900)
    _history(bot, p, [10000, 10000, 10000])  # 이력만으로는 특가
    api = NaverApi([_nitem(WATER, 7500)])
    _attach_naver(bot, api)
    _RUNS.append([_coupang("2", WATER, 7900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 0  # 네이버가 더 쌈
    key = NAVER_QUOTE_KEY + p.product_id
    row = bot.db.get_market_quote(key, 24)
    # 7시간 뒤: 근거로 쓰는 6시간은 지났지만 하루 안 — 다시 찾다가 5xx
    bot.db.set_market_quote(key, price=row["price"], source=NAVER_SOURCE, title=row["title"], url=row["url"],
                            meta=row["meta"], now=utcnow() - timedelta(hours=7))
    api.status = 503
    _RUNS.append([_coupang("2", WATER, 7900)])
    with caplog.at_level(logging.INFO):
        res = await bot.run_collector(bot.collectors[0])
    assert res["queued"] == 0 and len(api.requests) == 2 and "kept_veto=1" in caplog.text
    # 한 판 한도를 앞의 특가 후보가 다 쓴 경우도 같음
    api.status = 200
    other = _coupang("3", "곰곰 무농약 대추방울토마토 750g", 6900)
    _history(bot, other, [9000, 9000, 9000])
    bot.settings.deal.naver_reference.max_lookups_per_run = 1
    _RUNS.append([_coupang("3", "곰곰 무농약 대추방울토마토 750g", 6900), _coupang("2", WATER, 7900)])
    await bot.run_collector(bot.collectors[0])
    assert [d.product.product_id for d in _queued(bot)] == ["coupang:3:31"]


async def test_a_stale_naver_price_is_never_used_as_a_claim(bot: DealBot) -> None:
    api = NaverApi([_nitem(WATER, 9900)], status=503)
    _attach_naver(bot, api)
    key = NAVER_QUOTE_KEY + "coupang:5:51"
    bot.db.set_market_quote(key, price=9900, source=NAVER_SOURCE, title=WATER, url=None, now=utcnow() - timedelta(hours=7),
                            meta='{"floor": 9900, "confirmed": true, "catalog": true, "sellers": 0}')
    _RUNS.append([_coupang("5", WATER, 7900)])
    assert (await bot.run_collector(bot.collectors[0]))["deals"] == 0  # 지난 9,900원으로 '20% 싸요' 를 쓰지 않음
    # 6시간 안의 값이면 근거로 씀
    bot.db.set_market_quote(key, price=9900, source=NAVER_SOURCE, title=WATER, url=None, now=utcnow() - timedelta(hours=5),
                            meta='{"floor": 9900, "confirmed": true, "catalog": true, "sellers": 0}')
    _RUNS.append([_coupang("5", WATER, 7900)])
    assert (await bot.run_collector(bot.collectors[0]))["queued"] == 1


async def test_cached_naver_prices_are_judged_against_the_current_coupang_price(bot: DealBot) -> None:
    """리뷰 F6: 기억한 값도 판마다 지금 쿠팡 값으로 다시 거름 (값이 급히 내려도 70% 넘는 차이는 근거 아님)."""
    api = NaverApi([_nitem(WATER, 40000)])
    _attach_naver(bot, api)
    run = bot.naver_ref.new_run()
    assert (await bot._naver_quote(_coupang("6", WATER, 30000), run)).price == 40000
    assert await bot._naver_quote(_coupang("6", WATER, 9000), run) is None and len(api.requests) == 1


async def test_naver_lookups_go_to_history_deals_first_then_new_products(bot: DealBot) -> None:
    api = NaverApi([])  # 같은 상품 없음
    _attach_naver(bot, api, max_lookups_per_run=2)
    plain = [_coupang(str(100 + i), f"아무개{i} 상품 {i}호", 9000 + i) for i in range(5)]
    seen_before = _coupang("200", "이미본 상품 하나", 9900)
    bot.db.set_market_quote(NAVER_QUOTE_KEY + seen_before.product_id, price=12000, source=NAVER_SOURCE, title="이미본 상품 하나",
                            url=None, now=utcnow() - timedelta(hours=7), meta='{"floor": null, "confirmed": true}')
    hist = _coupang("300", "곰곰 무농약 대추방울토마토 750g", 6900)
    _history(bot, hist, [9000, 9000, 9000])
    _RUNS.append([seen_before, *plain, hist])
    await bot.run_collector(bot.collectors[0])
    asked = [r.url.params["query"] for r in api.requests]
    assert asked[0] == "곰곰 무농약 대추방울토마토 750g" and asked[1].startswith("아무개0")  # 특가 후보 → 처음 보는 상품 → 지난 값
    assert all("이미본" not in q for q in asked)


async def test_category_best_lookups_are_spread_across_categories(bot: DealBot) -> None:
    """리뷰 F5: 카테고리 3개 x 30개 한 판에서 첫 카테고리가 한도를 다 쓰지 않게 (순위대로 번갈아)."""
    api = NaverApi([_nitem("전혀 다른 상품", 1000)])
    _attach_naver(bot, api, max_lookups_per_run=15)
    prods = []
    for cid in (1016, 1012, 1014):
        for r in range(30):
            p = _coupang(f"{cid}{r:02d}", f"브랜드{cid} 상품 모델{r}", 19900, rank=r + 1)
            p.extra["category_id"] = cid
            prods.append(p)
    _RUNS.append(prods)
    await bot.run_collector(bot.collectors[0])
    asked = [r.url.params["query"] for r in api.requests]
    assert len(asked) == 15 and {cid: sum(f"브랜드{cid}" in q for q in asked) for cid in (1016, 1012, 1014)} == {1016: 5, 1012: 5, 1014: 5}
    assert asked[:3] == ["브랜드1016 상품 모델0", "브랜드1012 상품 모델0", "브랜드1014 상품 모델0"]  # 순위 1위부터


def test_config_bounds_naver_calls_per_hour_and_day() -> None:
    s = load_settings(ROOT / "config.yaml", load_env=False)
    nr = s.deal.naver_reference
    gold = next(c for c in s.collectors if c.name == "goldbox")
    cat = next(c for c in s.collectors if c.name == "category_best")
    # 카테고리 베스트 한 판(3 x 30 = 90개) 중 절반은 보게, 최악의 한 시간(골드박스·카테고리 한 판씩)도 시간 한도 안
    assert nr.max_lookups_per_run >= 45 and 2 * nr.max_lookups_per_run <= nr.max_lookups_per_hour
    per_day = (24 * 60 / gold.interval_minutes + 24 * 60 / cat.interval_minutes) * nr.max_lookups_per_run
    assert per_day <= 25000 / 10  # 네이버 허용(하루 25,000회)의 1/10 아래
    assert nr.run_seconds <= 60 and nr.general_shipping_allowance > 0 and nr.confirm_lowest


# ---------------------------------------------------------------- 13. 리뷰 D1·D2·D4: 저녁 요약 줄 길이·이유, 자기 점검 표시
async def test_evening_line_fits_kakao_and_keeps_errors_and_resume(bot: DealBot) -> None:
    now = utcnow()
    for i in range(12):  # 리뷰 D1 의 최악: 12개 발행(이름 두 개 인용) + 안 올린 이유 셋 + 오류 12번 + 멈춤
        p = Product(source="ppomppu", product_id=f"toss:{i}", shop="toss", name=f"{i}번 아주 긴 이름의 상품 이름 대용량 묶음", price=9900, url="u")
        bot.db.enqueue(Deal(p, DealVerdict(is_deal=True)), score=1, now=now - timedelta(hours=3))
        item = next(it for it in bot.db.pending_items(50) if it.deal.product.product_id == p.product_id)
        bot.db.update_queue_item(item.id, status="published")
        bot.db.record_post(Deal(p, DealVerdict(is_deal=True)), channel_id="c", message_id=i, dry_run=True, now=now - timedelta(hours=2))
    bot.db.kv_set(SKIP_KV, json.dumps({"already_posted": {f"coupang:{i}:1": to_iso(now) for i in range(9)},
                                       "duplicate": {"coupang:a:1": to_iso(now), "coupang:b:1": to_iso(now)},
                                       "declined": {"coupang:c:1": to_iso(now)}}))
    for _ in range(12):
        run_id = bot.db.start_run("ppomppu")
        bot.db.finish_run(run_id, status="error", error="x")
    bot.state.paused = True
    bot.notifier.summary = lambda *a, **k: None  # type: ignore[method-assign]
    day = (await bot.owner_summary()).split(" 핫딜봇")[0]
    assert "「" in day and "특가인데 안 올린 딜은 이미 올린 상품 9개" in day  # 이름도 이유도 들어감 (자리가 있으면)
    assert len("🌙 딜봇 10/14") + 1 + len(day) <= 200
    assert day.endswith("이에요.") or day.endswith("예요.") or day.endswith("(/resume).")
    assert "(/resume)" in day and "오류가 12번" in day


async def test_today_posted_deal_is_not_counted_as_not_posted(bot: DealBot) -> None:
    p = _coupang("50", WATER, 7900)
    _history(bot, p, [10000, 10000, 10000])
    _RUNS.append([_coupang("50", WATER, 7900)])
    await bot.run_collector(bot.collectors[0])
    item = bot.db.pending_items(5)[0]
    bot.db.update_queue_item(item.id, status="published")
    bot.db.record_post(item.deal, channel_id="c", message_id=1, dry_run=True)
    _RUNS.append([_coupang("50", WATER, 7900)])  # 골드박스가 같은 상품을 다시 가져옴 → 수집 로그엔 already_posted
    await bot.run_collector(bot.collectors[0])
    assert "already_posted" in (bot.db.kv_get(SKIP_KV) or "")
    assert bot.skipped_deal_counts(utcnow() - timedelta(days=1)) == {}
    bot.notifier.summary = lambda *a, **k: None  # type: ignore[method-assign]
    assert "안 올린 딜" not in await bot.owner_summary()
    # 보류는 Blog-Auto 가 따로 알리니 이 문장에 안 넣음
    bot._remember_skips({"held": {"coupang:x:1"}, "held_price": {"coupang:y:1"}}, utcnow())
    assert bot.skipped_deal_counts(utcnow() - timedelta(days=1)) == {}


async def test_self_check_does_not_claim_the_naver_comparison_works_before_a_search(bot: DealBot) -> None:
    api = NaverApi([_nitem(WATER, 9900)])
    _attach_naver(bot, api)
    line = next(x for x in await bot.self_check() if "네이버 최저가 비교" in x[1])
    assert line[0] is None and "첫 조회 때 확인" in line[1]
    await bot.naver_ref.lookup(_coupang("60", WATER, 7900), bot.naver_ref.new_run())
    line = next(x for x in await bot.self_check() if "네이버 최저가 비교" in x[1])
    assert line[0] is True and "시간당" in line[1]
    bad = NaverApi(status=401)
    _attach_naver(bot, bad)
    await bot.naver_ref.lookup(_coupang("61", WATER, 7900), bot.naver_ref.new_run())
    line = next(x for x in await bot.self_check() if "네이버 최저가 비교" in x[1])
    assert line[0] is None and "꺼짐 (HTTP 401" in line[1]
