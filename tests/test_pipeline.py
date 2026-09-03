"""파이프라인 통합 테스트 (외부 네트워크/텔레그램 없음, dry-run)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, CollectorContext, register
from dealbot.config import CollectorConfig, Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.utils.timeutil import utcnow


@register("fake")
class FakeCollector(BaseCollector):
    products: list[Product] = []
    fail = False

    async def collect(self) -> list[Product]:
        if FakeCollector.fail:
            raise RuntimeError("boom")
        return list(FakeCollector.products)


def _p(pid: str, price: int, **kw) -> Product:  # type: ignore[no-untyped-def]
    return Product(source="fake", product_id=pid, name=f"상품 {pid}", price=price,
                   url=f"https://www.coupang.com/vp/products/{pid}",
                   affiliate_url=f"https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey={pid}", **kw)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 2
    FakeCollector.products = []
    FakeCollector.fail = False
    b = DealBot(settings)
    yield b
    b.db.close()


async def test_collect_evaluate_queue_publish(bot: DealBot) -> None:
    FakeCollector.products = [
        _p("1", 7000, discount_rate=40),   # 규칙 (a)
        _p("2", 9000, discount_rate=10),   # 특가 아님
        _p("3", 500, discount_rate=90),    # 최소가 미만
    ]
    results = await bot.run_once()
    assert results[0]["collected"] == 3 and results[0]["deals"] == 1 and results[0]["queued"] == 1
    assert bot.db.price_history_count() == 3
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.posted_within("1", 7)
    assert bot.db.recent_posts()[0]["product_id"] == "1"

    # 같은 상품 재수집 → 중복 발행 없음
    results = await bot.run_once()
    assert results[0]["deals"] == 1 and results[0]["queued"] == 0
    assert bot.db.count_posts_since(utcnow() - timedelta(hours=1)) == 1


async def test_rule_b_uses_history(bot: DealBot) -> None:
    now = utcnow()
    for i in range(3):
        bot.db.record_observation(_p("10", 10000), now - timedelta(days=i + 1))
    FakeCollector.products = [_p("10", 8000)]  # 20% 저렴, 할인율 정보 없음
    results = await bot.run_once()
    assert results[0]["deals"] == 1
    posted = bot.db.recent_posts()[0]
    assert posted["product_id"] == "10" and posted["price"] == 8000


async def test_rate_limit_leaves_items_pending(bot: DealBot) -> None:
    FakeCollector.products = [_p(str(i), 5000, discount_rate=50) for i in range(4)]
    await bot.run_once()
    counts = bot.db.queue_counts()
    assert counts["published"] == 2 and counts["pending"] == 2
    assert bot.reporter.status_text()  # 렌더 확인


async def test_collector_error_is_recorded(bot: DealBot) -> None:
    FakeCollector.fail = True
    results = await bot.run_once()
    assert results[0]["status"] == "error" and "boom" in results[0]["error"]
    assert bot.state.last_error and "boom" in bot.state.last_error
    assert bot.db.last_error()["kind"] == "collector:fake"  # type: ignore[index]
    assert bot.db.last_run("fake").status == "error"  # type: ignore[union-attr]


async def test_pause_and_run_request(bot: DealBot) -> None:
    bot.pause()
    assert bot.state.paused
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    assert not await bot.process_queue_once()  # 일시정지 중엔 발행 안 함
    bot.resume()
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert "fake" in bot.request_run(None) and "알 수 없는" in bot.request_run("nope")
    assert bot.state.collectors["fake"].run_requested


async def test_queue_expiry_and_failure_attempts(bot: DealBot, settings: Settings) -> None:
    from dealbot.models import PublishResult

    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])

    async def failing_publish(deal):  # type: ignore[no-untyped-def]
        return PublishResult(ok=False, error="telegram down")

    bot.publisher.publish = failing_publish  # type: ignore[method-assign]
    for _ in range(settings.publish.max_publish_attempts):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"failed": 1}
    assert not await bot.process_queue_once()

    # 만료: TTL 0 이면 1분 전 등록된 항목은 발행 전에 expired 처리
    settings.publish.queue_ttl_hours = 0
    stale = Deal(product=_p("2", 1000, discount_rate=50), verdict=DealVerdict(is_deal=True, score=1))
    assert bot.db.enqueue(stale, score=1, now=utcnow() - timedelta(minutes=1))
    await bot.process_queue_once()
    assert bot.db.queue_counts().get("expired") == 1


async def test_dry_run_without_coupang_uses_raw_url(bot: DealBot) -> None:
    p = Product(source="fake", product_id="77", name="뽐뿌 상품", price=5000, url="https://www.coupang.com/vp/products/77", discount_rate=50)
    FakeCollector.products = [p]
    await bot.run_once()
    post = bot.db.recent_posts()[0]
    assert post["affiliate_url"] == "https://www.coupang.com/vp/products/77"


def test_context_type() -> None:
    assert CollectorContext.__dataclass_fields__.keys() >= {"settings", "http", "db", "coupang"}
