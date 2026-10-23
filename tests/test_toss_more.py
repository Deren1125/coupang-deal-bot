"""토스 딜 늘리기 (주인 10/23: 토스 쉐어링크가 잘 팔림 → '괜찮은 제품만' 그대로 더 많이).

- 품질 기준(할인율·후기·가격 비교·관심도)은 그대로 — 토스만 느슨하게 하지 않는다
- 링크 요청 자리(15칸)에서 토스 딜을 먼저: 자리의 절반까지는 점수와 상관없이 다른 몰 글을 밀어내고, 그 몫은 다른 몰 새 딜이 못 밀어냄
- 알구몬·뽐뿌: 몰 머리말이 없어도 제목·주소에 토스 단서가 있으면 같은 기준으로 판정
- 딜 탭 한 줄 '토스 후보 N · 링크 대기 N · 오늘 올림 N' · 링크 넣기 칸의 '토스에서 열기'·붙여넣기 모양
네트워크 없이."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dash_helpers import blogauto_status, push_settings, queued
from dealbot.app import DealBot
from dealbot.collectors import CollectorContext, build_collector
from dealbot.config import CollectorConfig, Settings, load_settings
from dealbot.dashboard import webapp
from dealbot.models import Deal, DealVerdict, Product
from dealbot.shops import ShopRegistry
from dealbot.storage.db import REPLACED, Database
from dealbot.utils.timeutil import utcnow
from test_dash_state import state

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ 품질 기준은 그대로
def test_quality_gates_are_unchanged() -> None:
    d = load_settings(ROOT / "config.yaml", load_env=False).deal
    assert d.min_discount_rate == 50 and d.discount_alone is False
    assert d.quality.enabled and d.quality.min_review_count == 10 and d.quality.shops == []  # 후기 기준: 모든 몰 같은 값
    m = d.market
    assert m.enabled and m.min_below_market_pct == 20 and m.strict and m.veto_if_not_cheaper and m.require_for_discount_rule
    assert (d.interest.min_recommend, d.interest.min_comments, d.interest.min_views) == (1, 3, 500)
    assert "toss" not in d.interest.always_pass_sources  # 토스라고 관심도 게이트를 건너뛰지 않음
    assert d.community_min_recommend == 10 and d.recommend_needs_support and d.min_price == 5000
    assert d.food.min_below_reference_pct == 50
    assert d.page_check.tolerance_pct == 10 and "toss" in d.page_check.shops
    assert not any("toss" in k for k in d.per_source)  # 토스만의 기준 재정의 없음


def test_toss_page_without_price_is_unverified_not_held() -> None:
    """토스 웹 페이지는 가격을 숨긴다 → 다른 '확인 못 함' 몰처럼 지금대로 진행 (보류하지 않음)."""
    from dealbot.page_price import check_page_price

    check = check_page_price("애슐리 핫도그 8개입", 14890, [], page_title=None)
    assert check.status == "unverified"


def test_priority_config_defaults() -> None:
    p = load_settings(ROOT / "config.yaml", load_env=False).publish
    assert p.link_priority_shops == ["toss"] and p.link_priority_share == 0.5
    assert p.max_awaiting_links == 15 and p.swap_min_score_gain == 5


# ------------------------------------------------------------ 링크 요청 자리: 토스 먼저
@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    settings.publish.dry_run = False
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 100
    b = DealBot(settings)
    yield b
    b.db.close()


def _prod(shop: str, key: str, price: int = 10000) -> Product:
    url = f"https://toss.im/_m/{key}" if shop == "toss" else f"https://smartstore.naver.com/x/products/{key}"
    return Product(source="fake", product_id=f"{shop}:{key}", shop=shop, name=f"{shop} 상품 {key}", price=price, url=url)


def _waiting(bot: DealBot, shop: str, key: str, score: float, *, age_h: float = 1.0) -> int:
    at = utcnow() - timedelta(hours=age_h)
    assert bot.db.enqueue(Deal(_prod(shop, key), DealVerdict(is_deal=True, score=score)), score=score, now=at)
    item = bot.db.items_for_product(f"{shop}:{key}", ("pending",))[0]
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual link required", now=at)
    return item.id


async def _offer(bot: DealBot, shop: str, key: str, score: float) -> int:
    assert bot.db.enqueue(Deal(_prod(shop, key), DealVerdict(is_deal=True, score=score)), score=score)
    item = bot.db.items_for_product(f"{shop}:{key}", ("pending",))[0]
    assert await bot.process_queue_once()
    return item.id


def _st(bot: DealBot, qid: int) -> str:
    it = bot.db.get_queue_item(qid)
    assert it is not None
    return it.status


def _toss_waiting(bot: DealBot) -> int:
    return bot.db.awaiting_shop_count(("toss",))


async def test_toss_takes_slots_from_other_shops_regardless_of_score(bot: DealBot) -> None:
    others = [_waiting(bot, "naver", f"o{i}", 50 + i, age_h=10 - i * 0.1) for i in range(15)]
    new = await _offer(bot, "toss", "t0", 1)  # 점수 1 < 50 이어도 토스 몫이 비어 있어 가장 낮은 다른 몰 글을 내림
    assert _st(bot, new) == "awaiting_link"
    assert bot.db.get_queue_item(others[0]).last_error.startswith(REPLACED)  # type: ignore[union-attr]
    assert bot.db.queue_counts()["awaiting_link"] == 15 and _toss_waiting(bot) == 1


async def test_toss_share_is_half_of_the_slots_then_normal_rules(bot: DealBot) -> None:
    for i in range(15):
        _waiting(bot, "naver", f"o{i}", 50, age_h=10 - i * 0.1)
    for i in range(8):
        await _offer(bot, "toss", f"t{i}", 1)
    assert _toss_waiting(bot) == 8 and bot.db.queue_counts()["awaiting_link"] == 15
    nine = await _offer(bot, "toss", "t8", 1)  # 몫(8)을 채웠으니 보통 규칙: 점수 1 로는 다른 몰 글(50)을 못 밀어냄
    assert _st(bot, nine) == "skipped" and _toss_waiting(bot) == 8
    strong = await _offer(bot, "toss", "t9", 60)  # 보통 규칙(5 이상 높음)이면 밀어냄
    assert _st(bot, strong) == "awaiting_link" and bot.db.queue_counts()["awaiting_link"] == 15


async def test_other_shops_cannot_push_out_the_toss_share(bot: DealBot) -> None:
    toss_ids = [_waiting(bot, "toss", f"t{i}", 1, age_h=10 - i * 0.1) for i in range(8)]
    others = [_waiting(bot, "naver", f"o{i}", 20, age_h=9 - i * 0.1) for i in range(7)]
    new = await _offer(bot, "naver", "big", 90)  # 가장 낮은 건 점수 1 인 토스 글이지만 토스 몫(8)이라 못 밂 → 다른 몰 글을 내림
    assert _st(bot, new) == "awaiting_link"
    assert all(_st(bot, i) == "awaiting_link" for i in toss_ids) and _toss_waiting(bot) == 8
    assert _st(bot, others[0]) == "skipped"
    # 토스 글이 몫보다 많으면 그 위는 보통 규칙으로 밀릴 수 있음
    extra = _waiting(bot, "toss", "t-extra", 1, age_h=0.1)
    bot.db.update_queue_item(others[1], status="expired")
    assert _toss_waiting(bot) == 9
    new2 = await _offer(bot, "naver", "big2", 90)
    assert _st(bot, new2) == "awaiting_link" and _st(bot, toss_ids[0]) == "skipped" and _st(bot, extra) == "awaiting_link"


async def test_slots_are_not_held_empty_when_there_is_no_toss(bot: DealBot) -> None:
    for i in range(15):
        await _offer(bot, "naver", f"o{i}", 10)  # 토스가 없으면 15칸 모두 다른 몰이 씀
    assert bot.db.queue_counts()["awaiting_link"] == 15
    assert _toss_waiting(bot) == 0
    first = await _offer(bot, "naver", "late", 14.9)  # 다른 몰끼리는 예전 규칙 (5 이상 높아야 교체)
    assert _st(bot, first) == "skipped"


async def test_priority_can_be_turned_off(bot: DealBot) -> None:
    bot.settings.publish.link_priority_share = 0
    for i in range(15):
        _waiting(bot, "naver", f"o{i}", 50, age_h=10 - i * 0.1)
    new = await _offer(bot, "toss", "t0", 1)
    assert _st(bot, new) == "skipped"  # 예전과 같음: 점수가 낮으면 자리를 못 얻음


async def test_toss_counts_line(bot: DealBot) -> None:
    _waiting(bot, "toss", "w1", 5)
    _waiting(bot, "toss", "w2", 5)
    _waiting(bot, "naver", "o1", 5)
    assert bot.db.enqueue(Deal(_prod("toss", "p1"), DealVerdict(is_deal=True, score=3)), score=3)  # 올릴 차례를 기다리는 토스 딜
    day = utcnow() - timedelta(hours=1)
    assert bot.toss_counts(day) == {"candidates": 3, "waiting": 2, "today": 0}


# ------------------------------------------------------------ 토스 주소·단서 알아보기
def test_toss_urls_are_recognised_with_either_domain() -> None:
    reg = ShopRegistry()
    toss = reg.get("toss")
    assert toss is not None
    for u in ("https://toss.im/_m/P4Qr1ope", "https://toss.shopping/t/40594131", "https://www.toss.im/_m/x", "https://service.toss.im/shopping-share?p=1"):
        assert toss.matches_url(u) and reg.by_url(u).key == "toss"  # type: ignore[union-attr]
    assert not toss.matches_url("https://nottoss.im/_m/x") and not toss.matches_url("https://toss.im.evil.example/_m/x")
    assert ShopRegistry.product_key("toss", "https://toss.im/_m/ABC") == ShopRegistry.product_key("toss", "https://toss.shopping/t/ABC")


def test_title_hint_only_for_real_toss_words() -> None:
    reg = ShopRegistry()
    for t in ("토스쇼핑 애슐리 핫도그", "[기타] 토스 쇼핑 첫구매 3천원", "토스 쉐어링크 특가", "쉐어링크 핫딜 모음", "TOSS 쇼핑 딜"):
        assert reg.by_title_hint(t) is not None and reg.by_title_hint(t).key == "toss", t  # type: ignore[union-attr]
    for t in ("토스트기 특가", "토스터 스테인리스", "네이버 스마트스토어 딜", "", "tossed salad"):
        assert reg.by_title_hint(t) is None, t


ALGUMON_LIST = """
<html><body><ul>
<li class="post-li"><a class="product-link" href="/n/deal/1">[기타] 토스쇼핑 애슐리 핫도그 8개입 (14,890원/무료)</a>
  <span class="product-like">5</span>
  <a class="origin" href="https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=700001">원문</a></li>
<li class="post-li"><a class="product-link" href="/n/deal/2">[모름몰] 어떤 상품 (9,900원/무료)</a>
  <span class="product-like">9</span>
  <a class="origin" href="https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=700002">원문</a></li>
<li class="post-li"><a class="product-link" href="/n/deal/3">[토스] 샴푸 2개 (12,000원/무료)</a>
  <span class="product-like">5</span>
  <a class="origin" href="https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=700003">원문</a></li>
</ul></body></html>
"""
ORIGIN = '<html><body><div class="cont"><a href="https://toss.im/_m/HOT{n}">상품 링크</a></div></body></html>'


@pytest.mark.real_fetch
async def test_algumon_finds_toss_by_title_hint_and_toss_urls(settings: Settings, db: Database) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "www.algumon.com":
            return httpx.Response(200, content=ALGUMON_LIST.encode(), headers={"content-type": "text/html; charset=utf-8"})
        if req.url.host == "www.ppomppu.co.kr":
            n = req.url.params["no"][-1]
            return httpx.Response(200, content=ORIGIN.format(n=n).encode(), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="algumon", type="algumon", options={"request_delay_seconds": 0, "unknown_shop": "skip"})
    products = await build_collector(cfg, ctx).collect()
    by = {p.name.split()[0]: p for p in products}
    assert len(products) == 2  # '[모름몰]' 글은 예전처럼 건너뜀
    hint = next(p for p in products if "핫도그" in p.name)
    assert hint.shop == "toss" and hint.product_id == "toss:HOT1" and hint.url == "https://toss.im/_m/HOT1" and hint.price == 14890
    tagged = next(p for p in products if "샴푸" in p.name)
    assert tagged.shop == "toss" and tagged.product_id == "toss:HOT3" and by


@pytest.mark.real_fetch
async def test_ppomppu_toss_filter_keeps_toss_and_rescues_untagged(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    """shops: ["toss"] 는 토스만 남기는 필터 — 토스 글을 빼지 않고, 머리말이 모르는 말이어도 제목에 토스 단서가 있으면 토스로 본다."""
    listing = (fixtures_dir / "ppomppu_list.html").read_text(encoding="utf-8")
    assert "[토스쇼핑] 애슐리" in listing
    untagged = listing.replace("[토스쇼핑] 애슐리", "[기타] 토스쇼핑 애슐리")  # 머리말은 모르는 말, 제목엔 '토스쇼핑'

    def handler(req: httpx.Request) -> httpx.Response:
        h = {"content-type": "text/html; charset=utf-8"}
        if req.url.path == "/zboard/zboard.php":
            return httpx.Response(200, content=untagged.encode(), headers=h)
        if req.url.path == "/zboard/view.php":
            f = fixtures_dir / f"ppomppu_view_{req.url.params['no']}.html"
            return httpx.Response(200, content=f.read_bytes(), headers=h) if f.exists() else httpx.Response(404)
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp", type="ppomppu", options={"request_delay_seconds": 0, "shops": ["toss"]})
    products = await build_collector(cfg, ctx).collect()
    assert [(p.shop, p.product_id) for p in products] == [("toss", "toss:ABC123")]
    base = load_settings(ROOT / "config.yaml", load_env=False)
    pp = next(c for c in base.collectors if c.name == "ppomppu")
    assert pp.options["shops"] == []  # 뽐뿌 수집기는 토스를 거르지 않음 (등록된 모든 몰)


# ------------------------------------------------------------ 대시보드: 토스에서 열기 · 붙여넣기 · 한 줄
@pytest.fixture
def dbot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_dashboard_toss_link_request_fields(dbot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    t = queued(dbot, product_id="toss:HOT1", url="https://toss.im/_m/HOT1")
    t2 = queued(dbot, product_id="toss:t/2", url="https://toss.shopping/t/2222")
    o = queued(dbot, product_id="naver:1", shop="naver", url="https://smartstore.naver.com/x/products/1")
    body = await state(dbot)
    by = {it["iid"]: it for it in body["todo"]}
    p = by[f"deal-{t.id}"]["primary"]
    assert p["action"] == "deal.link" and p["url"] == "https://toss.im/_m/HOT1" and p["ol"] == "토스에서 열기"
    assert re.search(p["pm"], "복사한 글 https://toss.im/_m/HOT1 입니다") and re.search(p["pm"], "https://toss.shopping/t/3")
    assert not re.search(p["pm"], "https://www.coupang.com/vp/products/1") and not re.search(p["pm"], "https://toss.im/other")
    assert by[f"deal-{t.id}"]["detail"]["open"] == [{"label": "토스에서 열기", "url": "https://toss.im/_m/HOT1"}]
    assert by[f"deal-{t2.id}"]["primary"]["ol"] == "토스에서 열기"
    other = by[f"deal-{o.id}"]["primary"]
    assert "ol" not in other and "pm" not in other  # 토스가 아닌 몰은 그대로
    assert by[f"deal-{o.id}"]["detail"]["open"][0]["label"] == "상품 열기"


async def test_dashboard_deal_tab_has_toss_counter(dbot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    queued(dbot, product_id="toss:HOT1", url="https://toss.im/_m/HOT1")
    queued(dbot, product_id="toss:HOT2", url="https://toss.im/_m/HOT2")
    queued(dbot, status="pending", product_id="toss:HOT3", url="https://toss.im/_m/HOT3")
    queued(dbot, product_id="naver:1", shop="naver", url="https://smartstore.naver.com/x/products/1")
    body = await state(dbot)
    assert body["deal"]["status"]["toss"] == "토스 후보 3 · 링크 대기 2 · 오늘 올림 0"


def test_app_js_uses_the_server_fields() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "a.ol || '상품 열기'" in js and "RegExp(a.pm)" in js and "st.toss" in js
    # 클립보드는 사용자가 누를 때만: 붙여넣기 단추 · 링크 넣기를 누른 순간 (페이지가 다시 보일 때 읽지 않음)
    vis = js.split("visibilitychange", 1)[1].split("});", 1)[0]
    assert "readText" not in vis and "붙여넣기" in vis
    assert "readText" in js.split("function paste(", 1)[1].split("\n}\n", 1)[0]  # 붙여넣기 단추를 누른 때
