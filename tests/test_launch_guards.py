"""실제 발행 전 안전장치: 야간 무음, 링크 요청 폭주 방지, 데이터 볼륨 표시."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from dealbot.app import DealBot
from dealbot.cli import sample_deal
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.models import Product
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer
from dealbot.utils.timeutil import in_time_window


def test_in_time_window() -> None:
    t = lambda h, m=0: datetime(2026, 9, 7, h, m)  # noqa: E731
    assert not in_time_window(t(3), None) and not in_time_window(t(3), "")
    assert in_time_window(t(3), "00:00-07:00") and not in_time_window(t(7), "00:00-07:00")
    assert in_time_window(t(23, 30), "23:00-07:00") and in_time_window(t(6, 59), "23:00-07:00")
    assert not in_time_window(t(12), "23:00-07:00")
    assert not in_time_window(t(5), "05:00-05:00")
    with pytest.raises(ValueError, match="형식"):
        in_time_window(t(5), "night")


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_photo(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(kw)
        return SimpleNamespace(message_id=1)

    async def send_message(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(kw)
        return SimpleNamespace(message_id=2)


async def test_publisher_passes_silent_flag(repo_root: Path) -> None:
    fake = _FakeBot()
    pub = TelegramPublisher(fake, "-100123", TemplateRenderer(repo_root / "templates"))  # type: ignore[arg-type]
    deal = sample_deal()
    assert (await pub.publish(deal, silent=True)).ok and fake.calls[-1]["disable_notification"] is True
    assert (await pub.publish(deal)).ok and fake.calls[-1]["disable_notification"] is False
    deal.product.image_url = None
    assert (await pub.publish(deal, silent=True)).ok and fake.calls[-1]["disable_notification"] is True


@register("fake_guard")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_guard", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 10
    settings.publish.dry_run = False
    settings.publish.max_awaiting_links = 2
    FakeCollector.products = []
    b = DealBot(settings)
    yield b
    b.db.close()


async def test_manual_link_requests_are_capped(bot: DealBot) -> None:
    FakeCollector.products = [
        Product(source="fake", product_id=f"toss:{i}", shop="toss", name=f"토스 상품 {i}", price=10000 + i, url=f"https://toss.im/_m/{i}", recommend_count=9)
        for i in range(5)
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 5}
    for _ in range(5):
        assert await bot.process_queue_once()
    counts = bot.db.queue_counts()
    assert counts.get("awaiting_link") == 2 and counts.get("skipped") == 3, counts
    assert not await bot.process_queue_once()  # 남은 게 없음
    # 무제한(0)으로 두면 전부 요청한다
    bot.settings.publish.max_awaiting_links = 0
    FakeCollector.products = [Product(source="fake", product_id="toss:x", shop="toss", name="토스 상품 x", price=9000, url="https://toss.im/_m/x", recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once() and bot.db.queue_counts().get("awaiting_link") == 3


def test_status_reports_data_persistence(bot: DealBot) -> None:
    ctx = bot.reporter.status_context()
    assert ctx["data_persistent"] is True  # 테스트는 임시 폴더라 볼륨 여부와 무관
    assert ctx["db_since"] and len(ctx["db_since"]) == 10
    text = bot.reporter.status_text()
    assert "기록 시작" in text and "볼륨이 연결되지" not in text


def test_db_created_at_uses_oldest_record(tmp_path: Path) -> None:
    from datetime import timedelta

    from dealbot.storage.db import Database
    from dealbot.utils.timeutil import utcnow

    d = Database(tmp_path / "old.db")
    old = utcnow() - timedelta(days=40)
    d.record_observation(Product(source="s", product_id="coupang:1", shop="coupang", name="예전 상품", price=1000, url="u"), now=old)
    d._conn.execute("DELETE FROM kv WHERE key = 'db_created_at'")  # 예전 버전 DB 를 흉내
    d.close()
    d2 = Database(tmp_path / "old.db")
    assert d2.kv_get("db_created_at", "")[:10] == old.isoformat()[:10]
    d2.close()


class _FailingLinkPrice:
    def __init__(self) -> None:
        self.calls = 0

    async def convert(self, url: str) -> str:
        self.calls += 1
        from dealbot.links import LinkConversionError

        raise LinkConversionError("linkprice api error: {'result': 'E', 'msg': 'merchant not approved'}")


async def test_linkprice_failure_puts_shop_on_cooldown(bot: DealBot) -> None:
    sent: list[str] = []

    async def capture(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    bot.notifier.send = capture  # type: ignore[method-assign]
    bot.state.dry_run = False  # 테스트 환경은 텔레그램이 없어 연습 모드로 뜨므로 실제 모드로 강제
    bot.settings.deal.authenticity.enabled = False  # 링크 변환만 보는 테스트 (정품 확인은 test_authenticity)
    prov = _FailingLinkPrice()
    bot.links.providers["linkprice"] = prov
    lotteon = bot.registry.get("lotteon")
    assert lotteon is not None
    lotteon.enabled, lotteon.disabled_reason = True, None
    FakeCollector.products = [
        Product(source="fake", product_id=f"lotteon:{i}", shop="lotteon", name=f"롯데온 상품 {i}", price=20000 + i, url=f"https://www.lotteon.com/p/product/LO{i}", recommend_count=9)
        for i in range(3)
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 3}
    for _ in range(3):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 3}
    assert prov.calls == 1, "한 번 실패하면 같은 몰은 API 를 다시 부르지 않고 건너뛴다"
    assert len(sent) == 1 and "롯데온" in sent[0] and "건너뜁니다" in sent[0]
    assert bot.db.kv_get("provider_cooldown:linkprice:lotteon")

    # 냉각 시간이 지나면 다시 시도한다
    bot.db.kv_set("provider_cooldown:linkprice:lotteon", "2000-01-01T00:00:00+00:00")
    FakeCollector.products = [Product(source="fake", product_id="lotteon:9", shop="lotteon", name="롯데온 상품 9", price=1000, url="https://www.lotteon.com/p/product/LO9", recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once() and prov.calls == 2


async def test_board_only_urls_are_not_queued(bot: DealBot) -> None:
    bot.settings.info_posts.enabled = False  # 정보 글 기능은 별도 테스트
    FakeCollector.products = [
        Product(source="fake", product_id="toss:board", shop="toss", name="토스 이벤트 글", price=4800, url="https://bbs.ruliweb.com/market/board/1020/read/107150", recommend_count=9),
        Product(source="fake", product_id="toss:real", shop="toss", name="토스 상품", price=12900, url="https://toss.im/_m/ABC", recommend_count=9),
        Product(source="fake", product_id="coupang:short", shop="coupang", name="쿠팡 상품", price=9900, url="https://coupa.ng/cfXyz", recommend_count=9),
    ]
    await bot.run_collector(bot.collectors[0])
    queued = {it.deal.product.product_id for it in bot.db.pending_items()} if hasattr(bot.db, "pending_items") else None
    counts = bot.db.queue_counts()
    assert counts.get("pending") == 2, counts
    if queued is not None:
        assert "toss:board" not in queued
    # 끄면 예전처럼 들어간다
    bot.settings.deal.require_shop_url = False
    FakeCollector.products = [Product(source="fake", product_id="toss:board2", shop="toss", name="토스 이벤트 글 2", price=4800, url="https://bbs.ruliweb.com/market/board/1020/read/1", recommend_count=9)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts().get("pending") == 3


async def test_coupang_deals_wait_when_deeplink_budget_is_out(bot: DealBot) -> None:
    """딥링크 예산이 찬 시간에는 쿠팡 딜을 실패로 세지 않고 기다리게 두고, 다른 몰의 글을 먼저 올린다."""
    from dealbot.coupang.client import ApiBudget, CoupangRateLimited

    bot.settings.deal.authenticity.enabled = False
    bot.budget = ApiBudget(10, reserve={"deeplink": 3}, caps={"deeplink": 2})
    for _ in range(2):
        bot.budget.record("deeplink")
    assert not bot.budget.available("deeplink")
    calls: list[str] = []

    class _Deeplink:
        async def convert(self, url: str) -> str:
            calls.append(url)
            raise CoupangRateLimited("budget")

    bot.links.providers["coupang"] = _Deeplink()  # type: ignore[assignment]
    for key in ("coupang", "daiso"):
        shop = bot.registry.get(key)
        assert shop is not None
        shop.enabled, shop.disabled_reason = True, None
    FakeCollector.products = [
        Product(source="fake", product_id="coupang:1", shop="coupang", name="쿠팡 상품", price=10000, url="https://www.coupang.com/vp/products/1", recommend_count=9, discount_rate=50),
        Product(source="fake", product_id="daiso:2", shop="daiso", name="다이소 상품", price=3000, url="https://www.daisomall.co.kr/p/2", recommend_count=9, discount_rate=50),
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 2}
    assert await bot.process_queue_once()  # 쿠팡은 건너뛰고 다이소를 올린다
    counts = bot.db.queue_counts()
    assert counts == {"pending": 1, "published": 1} and bot.db.next_pending().product_id == "coupang:1"  # type: ignore[union-attr]
    assert calls == [], "예산이 없으면 딥링크 API 를 부르지 않는다"
    assert await bot.process_queue_once() is False  # 남은 건 쿠팡뿐 → 예산이 풀릴 때까지 대기 (실패 아님)
    assert bot.db.next_pending().attempts == 0  # type: ignore[union-attr]


async def test_coupang_rate_limit_in_collector_is_not_an_error(bot: DealBot) -> None:
    from dealbot.coupang.client import CoupangRateLimited

    class _Limited(BaseCollector):
        name = "fake"

        async def collect(self) -> list[Product]:
            raise CoupangRateLimited("coupang api hourly budget exhausted (kind=goldbox)")

    errors: list[str] = []

    async def capture_error(kind: str, message: str) -> None:
        errors.append(message)

    bot.notifier.notify_error = capture_error  # type: ignore[method-assign]
    result = await bot.run_collector(_Limited("fake", {}, bot.collectors[0].ctx))
    assert result["status"] == "skipped" and errors == [] and bot.state.last_error is None
