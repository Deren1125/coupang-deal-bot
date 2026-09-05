"""파이프라인 통합 테스트 (외부 네트워크/텔레그램 없음, dry-run)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, CollectorContext, register
from dealbot.config import CollectorConfig, Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult
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
    kw.setdefault("rank", 1)
    return Product(
        source="fake", product_id=f"coupang:{pid}", shop="coupang", name=f"상품 {pid}", price=price,
        url=f"https://www.coupang.com/vp/products/{pid}",
        affiliate_url=f"https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey={pid}", **kw,
    )


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
        _p("1", 7000, discount_rate=60),
        _p("2", 9000, discount_rate=10),
        _p("3", 500, discount_rate=90),
    ]
    results = await bot.run_once()
    assert results[0]["collected"] == 3 and results[0]["deals"] == 1 and results[0]["queued"] == 1
    assert bot.db.price_history_count() == 3
    await bot.run_once()
    assert bot.db.price_history_count() == 3  # 6시간 안에는 같은 상품 가격을 다시 기록하지 않음
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.posted_within("coupang:1", 7)
    results = await bot.run_once()
    assert results[0]["deals"] == 1 and results[0]["queued"] == 0
    assert bot.db.count_posts_since(utcnow() - timedelta(hours=1)) == 1


async def test_rule_b_uses_history(bot: DealBot) -> None:
    now = utcnow()
    for i in range(3):
        bot.db.record_observation(_p("10", 10000), now - timedelta(days=i + 1))
    FakeCollector.products = [_p("10", 8000)]
    results = await bot.run_once()
    assert results[0]["deals"] == 1
    assert bot.db.recent_posts()[0]["product_id"] == "coupang:10"


async def test_rate_limit_leaves_items_pending(bot: DealBot) -> None:
    FakeCollector.products = [_p(str(i), 5000, discount_rate=50) for i in range(4)]
    await bot.run_once()
    counts = bot.db.queue_counts()
    assert counts["published"] == 2 and counts["pending"] == 2
    assert "쇼핑몰별 링크 처리" in bot.reporter.status_text()


async def test_collector_error_is_recorded(bot: DealBot) -> None:
    FakeCollector.fail = True
    results = await bot.run_once()
    assert results[0]["status"] == "error" and "boom" in results[0]["error"]
    assert bot.state.last_error and "boom" in bot.state.last_error
    assert bot.db.last_error()["kind"] == "collector:fake"  # type: ignore[index]


async def test_pause_and_run_request(bot: DealBot) -> None:
    bot.pause()
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    assert not await bot.process_queue_once()
    bot.resume()
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert "fake" in bot.request_run(None) and "그런 게시판은 없어요" in bot.request_run("nope")


async def test_failure_attempts_and_expiry(bot: DealBot, settings: Settings) -> None:
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])

    async def failing_publish(deal):  # type: ignore[no-untyped-def]
        return PublishResult(ok=False, error="telegram down")

    bot.publisher.publish = failing_publish  # type: ignore[method-assign]
    for _ in range(settings.publish.max_publish_attempts):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"failed": 1}

    settings.publish.queue_ttl_hours = 0
    stale = Deal(product=_p("2", 1000, discount_rate=50), verdict=DealVerdict(is_deal=True, score=1))
    assert bot.db.enqueue(stale, score=1, now=utcnow() - timedelta(minutes=1))
    await bot.process_queue_once()
    assert bot.db.queue_counts().get("expired") == 1


async def test_manual_link_flow_for_toss(bot: DealBot) -> None:
    bot.settings.publish.dry_run = False  # 연습 모드에서는 링크 요청을 생략하므로 실제 모드로
    toss = Product(source="fake", product_id="toss:ABC", shop="toss", name="토스 핫도그", price=14890,
                   url="https://toss.im/_m/ABC", recommend_count=9)
    FakeCollector.products = [toss]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}

    # 자동 변환 불가 → 관리자 링크 대기
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}
    assert not await bot.process_queue_once()  # 대기 중엔 발행 없음
    item = bot.db.awaiting_items()[0]
    assert "링크" in bot.reporter.pending_text() and f"#{item.id}" in bot.reporter.pending_text()

    # 같은 상품 재수집 → 중복 등록 안 됨
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"awaiting_link": 1}

    assert "http" in await bot.attach_link(item.id, "not a url")
    msg = await bot.attach_link(item.id, "https://toss.im/_m/MYLINK")
    assert "붙였습니다" in msg and bot.db.queue_counts() == {"pending": 1}
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/MYLINK"


async def test_skip_and_manual_link_expiry(bot: DealBot, settings: Settings) -> None:
    settings.publish.dry_run = False
    toss = Product(source="fake", product_id="toss:X", shop="toss", name="x", price=1000, url="https://toss.im/_m/X", recommend_count=9)
    FakeCollector.products = [toss]
    await bot.run_collector(bot.collectors[0])
    await bot.process_queue_once()
    item = bot.db.awaiting_items()[0]
    assert "건너" in bot.skip_item(item.id) and bot.db.queue_counts() == {"skipped": 1}
    assert "없습니다" in bot.skip_item(9999)

    settings.publish.manual_link_ttl_hours = 0
    stale = Deal(product=Product(source="f", product_id="toss:Y", shop="toss", name="y", price=1000, url="https://toss.im/_m/Y"), verdict=DealVerdict(is_deal=True))
    bot.db.enqueue(stale, score=1, now=utcnow() - timedelta(minutes=1))
    await bot.process_queue_once()  # → awaiting_link
    assert bot.db.queue_counts().get("awaiting_link") == 1
    await bot.process_queue_once()  # TTL 0 → 만료
    assert bot.db.queue_counts().get("expired") == 1


async def test_submit_manual_post(bot: DealBot) -> None:
    text = "/post\n[토스쇼핑 첫 구매 시 3,000원 추가 할인]\n상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트\n가격: 14,890원\nhttps://toss.im/_m/P4Qr1ope"
    msg = await bot.submit_manual(text)
    assert "맨 앞에 넣었습니다" in msg and bot.db.queue_counts() == {"pending": 1}
    assert await bot.process_queue_once()
    post = bot.db.recent_posts()[0]
    assert post["product_id"] == "toss:P4Qr1ope" and post["affiliate_url"] == "https://toss.im/_m/P4Qr1ope" and post["price"] == 14890
    rendered = bot.publisher.render(Deal.from_dict(bot.db.get_queue_item(1).deal.to_dict()))  # type: ignore[union-attr]
    assert rendered.startswith("[토스쇼핑 첫 구매 시 3,000원 추가 할인]")
    assert "상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트" in rendered
    assert "가격: 14,890원" in rendered and "https://toss.im/_m/P4Qr1ope" in rendered
    assert rendered.endswith("이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.")
    # 중복
    assert "이미" in await bot.submit_manual(text)
    assert "⚠️" in await bot.submit_manual("/post 링크 없음")


async def test_dry_run_without_coupang_uses_raw_url(bot: DealBot) -> None:
    p = Product(source="fake", product_id="coupang:77", shop="coupang", name="뽐뿌 상품", price=5000, url="https://www.coupang.com/vp/products/77", discount_rate=50, recommend_count=2)
    FakeCollector.products = [p]
    await bot.run_once()
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://www.coupang.com/vp/products/77"


async def test_disabled_shop_products_are_ignored(settings: Settings) -> None:
    from dealbot.config import ShopConfig

    settings.collectors = [CollectorConfig(name="fake", type="fake", interval_minutes=1)]
    settings.shops = [ShopConfig(key="temu", enabled=False)]
    b = DealBot(settings)
    try:
        FakeCollector.products = [Product(source="fake", product_id="temu:1", shop="temu", name="t", price=1000, url="https://temu.com/1", recommend_count=99)]
        r = await b.run_once()
        assert r[0]["collected"] == 1 and r[0]["deals"] == 0
    finally:
        b.db.close()


def test_context_type() -> None:
    assert CollectorContext.__dataclass_fields__.keys() >= {"settings", "http", "db", "coupang", "shops"}


async def test_dry_run_skips_manual_link_requests(bot: DealBot) -> None:
    assert bot.settings.publish.dry_run
    FakeCollector.products = [Product(source="fake", product_id="toss:D", shop="toss", name="연습", price=5000, url="https://toss.im/_m/D", recommend_count=9)]
    await bot.run_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/D"
