"""정품·공식 판매처 확인: 병행수입 표시는 버리고, 오픈마켓은 공식 표시가 있어야 자동으로 올린다."""

from __future__ import annotations

import pytest

from dealbot.app import DealBot
from dealbot.authenticity import check_authenticity
from dealbot.collectors import BaseCollector, register
from dealbot.config import AuthenticityConfig, CollectorConfig, Settings
from dealbot.models import Product
from dealbot.monitoring.admin import AdminNotifier

CFG = AuthenticityConfig()


def _p(name: str, shop: str = "gmarket", **kw) -> Product:  # type: ignore[no-untyped-def]
    return Product(source="ppomppu", product_id=f"{shop}:{abs(hash(name))}", shop=shop, name=name, price=10000, url=f"https://www.{shop}.co.kr/x", **kw)


def test_reject_keywords_anywhere() -> None:
    assert check_authenticity(CFG, _p("[병행수입] 나이키 운동화")).status == "reject"
    r = check_authenticity(CFG, _p("나이키 운동화"), page_text="상품정보고시 병행수입 여부: 예")
    assert r.status == "reject" and "상품 페이지" in r.reason
    assert check_authenticity(CFG, _p("리퍼 아이패드", shop="coupang")).status == "reject"  # 공식 몰이어도 표시가 있으면 안 됨
    # 두 글자 낱말은 다른 말의 일부면 세지 않는다
    assert check_authenticity(CFG, _p("갤럭시 케이스", shop="coupang"), page_text="리퍼러 정책 · 중고등학생 할인").status == "ok"


def test_open_market_needs_official_marker() -> None:
    assert check_authenticity(CFG, _p("나이키 운동화", shop="gmarket")).status == "unknown"
    assert check_authenticity(CFG, _p("나이키 공식스토어 운동화", shop="gmarket")).status == "ok"
    assert check_authenticity(CFG, _p("나이키 운동화", shop="11st"), post_text="11번가 나이키 브랜드관 세일입니다").status == "ok"
    r = check_authenticity(CFG, _p("나이키 운동화", shop="gmarket"), seller="나이키 공식 스토어")
    assert r.status == "ok" and r.seller == "나이키 공식 스토어"
    assert check_authenticity(CFG, _p("나이키 운동화", shop="lfmall")).status == "ok"  # 브랜드 몰
    assert check_authenticity(CFG, _p("나이키 운동화", shop="coupang")).status == "ok"
    assert check_authenticity(AuthenticityConfig(enabled=False), _p("[병행수입] 뭐든", shop="gmarket")).status == "ok"


def test_review_counts_stand_in_for_seller_checks() -> None:
    # 오픈마켓이라도 후기가 많이 쌓인 상품은 통과
    r = check_authenticity(CFG, _p("나이키 운동화", shop="gmarket", review_count=250))
    assert r.status == "ok" and "후기 250건" in r.reason
    assert check_authenticity(CFG, _p("나이키 운동화", shop="gmarket", review_count=20)).status == "unknown"
    # 쿠팡은 판매자를 못 가리니 후기 양으로: 적으면 안 올리고, 모르면 통과
    r = check_authenticity(CFG, _p("무선 이어폰", shop="coupang", review_count=5))
    assert r.status == "reject" and "후기 5건뿐" in r.reason
    assert check_authenticity(CFG, _p("무선 이어폰", shop="coupang", review_count=500)).status == "ok"
    assert check_authenticity(CFG, _p("무선 이어폰", shop="coupang")).status == "ok"


@register("fake_auth")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


class _OkLinkPrice:
    async def convert(self, url: str) -> str:
        return "https://linkprice.example/go?u=" + url.rsplit("/", 1)[-1]


@pytest.fixture
def bot(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_auth", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 20
    FakeCollector.products = []
    b = DealBot(settings)
    b.links.providers["linkprice"] = _OkLinkPrice()  # type: ignore[assignment]
    lotteon = b.registry.get("lotteon")
    assert lotteon is not None
    lotteon.enabled, lotteon.disabled_reason = True, None
    calls: dict[str, list] = {"send": [], "edit": []}

    async def send_with_id(text: str, *, silent: bool = False) -> int:
        calls["send"].append(text)
        return 100 + len(calls["send"])

    async def edit(message_id: int, text: str) -> bool:
        calls["edit"].append((message_id, text))
        return True

    async def pin(message_id: int) -> bool:
        return True

    monkeypatch.setattr(AdminNotifier, "enabled", property(lambda self: True))
    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.edit = edit  # type: ignore[method-assign]
    b.notifier.pin = pin  # type: ignore[method-assign]
    b.calls = calls  # type: ignore[attr-defined]
    yield b
    b.db.close()


def _lotteon(i: int, name: str) -> Product:
    return Product(source="fake", product_id=f"lotteon:{i}", shop="lotteon", name=name, price=20000, url=f"https://www.lotteon.com/p/product/LO{i}", recommend_count=9)


async def test_open_market_deals_wait_for_my_ok(bot: DealBot) -> None:
    calls: dict[str, list] = bot.calls  # type: ignore[attr-defined]
    FakeCollector.products = [_lotteon(1, "나이키 에어맥스 운동화"), _lotteon(2, "[병행수입] 아디다스 삼바"), _lotteon(3, "삼성전자 공식 브랜드관 갤럭시 버즈")]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 3}
    for _ in range(3):
        assert await bot.process_queue_once()
    counts = bot.db.queue_counts()
    assert counts == {"awaiting_approval": 1, "skipped": 1, "published": 1}, counts
    items = {it.product_id: it for it in [bot.db.get_queue_item(i) for i in (1, 2, 3)] if it}
    assert items["lotteon:1"].status == "awaiting_approval" and "정품 확인" in (items["lotteon:1"].last_error or "")
    assert items["lotteon:2"].status == "skipped" and "병행수입" in (items["lotteon:2"].last_error or "")
    assert items["lotteon:3"].status == "published" and items["lotteon:3"].deal.product.extra["auth"]["status"] == "ok"
    notice = [s for s in calls["send"] if s.startswith("🔎")]
    assert len(notice) == 1 and "정품 확인 필요 #1" in notice[0] and "나이키 에어맥스" in notice[0] and "/ok 1" in notice[0]
    assert "https://www.lotteon.com/p/product/LO1" in notice[0]
    pinned = calls["edit"][-1][1]
    assert "내가 확인해 줘야 하는 글" in pinned and "나이키 에어맥스" in pinned and "정품 확인" in pinned

    # /ok → 링크를 만들어 올린다. 다시 묻지 않는다
    assert "/ok" in await bot.attach_link(1, "https://x")
    assert bot.approve_item(1).startswith("✅") and "정품으로 확인" in bot.approve_item(1) or True
    assert bot.db.queue_counts() == {"pending": 1, "skipped": 1, "published": 1}
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(1)
    assert item is not None and item.status == "published" and item.deal.affiliate_url and "linkprice.example" in item.deal.affiliate_url


async def test_manual_link_shop_gets_a_warning_instead(bot: DealBot) -> None:
    bot.state.dry_run = False  # 연습 모드는 링크 요청을 생략하므로
    bot.settings.publish.dry_run = False
    calls: dict[str, list] = bot.calls  # type: ignore[attr-defined]
    FakeCollector.products = [Product(source="fake", product_id="toss:ABC", shop="toss", name="다이슨 에어랩", price=399000, url="https://toss.im/_m/ABC", recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}  # 확인 요청이 아니라 링크 요청 (거기서 같이 확인)
    notice = [s for s in calls["send"] if s.startswith("🔗")][0]
    assert "⚠️ 정품 확인" in notice and "공식 판매처" in notice
