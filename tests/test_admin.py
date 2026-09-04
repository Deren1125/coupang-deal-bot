"""관리자 챗 알림/명령 (DRY-RUN 미리보기, /pushtest, /status 마지막 에러)."""

from __future__ import annotations

import json

import httpx
import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.models import Product, PublishResult
from dealbot.monitoring.push import PushNotifier


@register("fake_admin")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


def _product(pid: str = "1") -> Product:
    return Product(
        source="fake", product_id=f"coupang:{pid}", shop="coupang", name=f"에어프라이어 {pid}", price=39900, discount_rate=60, rank=1,
        url=f"https://www.coupang.com/vp/products/{pid}",
        affiliate_url=f"https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey={pid}", image_url="https://img.example/1.jpg",
    )


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_admin", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    FakeCollector.products = []
    b = DealBot(settings)
    sent: list[str] = []

    async def capture(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    b.notifier.send = capture  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    yield b
    b.db.close()


async def test_dry_run_sends_full_preview_and_skips_side_channels(bot: DealBot) -> None:
    assert bot.state.dry_run
    FakeCollector.products = [_product()]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    assert len(sent) == 1, sent  # 미리보기 1건만 — 카카오/블로그 복붙 문구는 실제 발행 때만
    text = sent[0]
    assert text.startswith("🧪 <b>DRY-RUN 미리보기</b>")
    assert "[fake/coupang]" in text and "🖼 사진 포함" in text
    assert bot.publisher.render(bot.db.last_published_item().deal) in text  # 채널 글 전체가 그대로 들어감
    assert "에어프라이어 1" in text and "39,900원" in text and "link.coupang.com" in text


async def test_real_publish_sends_summary_and_copy_blocks(bot: DealBot) -> None:
    async def fake_publish(deal):  # type: ignore[no-untyped-def]
        return PublishResult(ok=True, message_id=7)

    bot.publisher.publish = fake_publish  # type: ignore[method-assign]
    FakeCollector.products = [_product("2")]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    assert sent[0].startswith("✅ <b>발행</b>") and "DRY-RUN" not in sent[0]
    names = [s.splitlines()[0] for s in sent[1:]]
    assert names == ["📋 <b>카카오 오픈채팅</b> 복사용", "📋 <b>네이버 블로그</b> 복사용"]


def test_status_last_error_is_in_memory_only(bot: DealBot) -> None:
    bot.db.log_event("ERROR", "collector:ppomppu", "403 Forbidden (지난 실행 기록)")
    ctx = bot.reporter.status_context()
    assert ctx["last_error"] is None and ctx["last_error_at"] is None
    assert "마지막 에러" not in bot.reporter.status_text()
    bot.state.set_error("[ppomppu] boom\nstack...")
    ctx = bot.reporter.status_context()
    assert ctx["last_error"] == "[ppomppu] boom" and ctx["last_error_at"]
    assert "❗ 마지막 에러" in bot.reporter.status_text()
    # 이력은 /errors 로 계속 볼 수 있음
    assert "403 Forbidden" in bot.reporter.errors_text()


async def test_push_test_without_provider(bot: DealBot) -> None:
    assert not bot.push.enabled
    msg = await bot.push_test()
    assert msg.startswith("📵") and "NTFY_TOPIC" in msg


async def test_push_test_sends_ntfy(bot: DealBot) -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"id": "x"})

    bot.settings.secrets.ntfy_topic = "dealbot-abc"
    bot.push = PushNotifier(bot.settings.monitoring.push, bot.settings.secrets, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    bot.notifier.push = bot.push
    bot.notifier.bot_username = "hotdeal_info_bot"
    msg = await bot.push_test()
    assert msg.startswith("📲") and "dealbot-abc" in msg and "manual_link" in msg
    body = json.loads(calls[0].content)
    assert body["topic"] == "dealbot-abc" and body["title"] == "DealBot 푸시 테스트" and body["click"] == "https://t.me/hotdeal_info_bot"

    def failing(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    bot.push.http = httpx.AsyncClient(transport=httpx.MockTransport(failing))
    assert (await bot.push_test()).startswith("❌")
