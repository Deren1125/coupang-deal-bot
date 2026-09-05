from __future__ import annotations

import json

import httpx

from dealbot.config import PushConfig, Secrets
from dealbot.monitoring.push import PushNotifier


def _http(calls: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json={"id": "x"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ntfy_json_publish() -> None:
    calls: list[httpx.Request] = []
    n = PushNotifier(PushConfig(), Secrets(ntfy_topic="deal-abc", ntfy_token="tok"), _http(calls))
    assert n.enabled and n.provider == "ntfy" and n.wants("manual_link") and not n.wants("error")
    assert await n.send("링크 필요 #1", "본문", click_url="https://t.me/bot", priority="high", tags=["link"])
    req = calls[0]
    assert str(req.url) == "https://ntfy.sh" and req.headers["Authorization"] == "Bearer tok"
    body = json.loads(req.content)
    assert body == {"topic": "deal-abc", "title": "링크 필요 #1", "message": "본문", "priority": 4, "click": "https://t.me/bot", "tags": ["link"]}


async def test_pushover_form() -> None:
    calls: list[httpx.Request] = []
    n = PushNotifier(PushConfig(events=["manual_link", "error"]), Secrets(pushover_user_key="u", pushover_app_token="t"), _http(calls))
    assert n.provider == "pushover"
    assert await n.send("t", "m", click_url="https://t.me/bot", priority="high")
    req = calls[0]
    assert req.url.host == "api.pushover.net"
    form = dict(x.split("=") for x in req.content.decode().split("&"))
    assert form["token"] == "t" and form["user"] == "u" and form["priority"] == "1" and "t.me" in form["url"]


async def test_provider_resolution_and_disabled() -> None:
    calls: list[httpx.Request] = []
    assert not PushNotifier(PushConfig(), Secrets(), _http(calls)).enabled
    assert not PushNotifier(PushConfig(provider="none"), Secrets(ntfy_topic="x"), _http(calls)).enabled
    both = PushNotifier(PushConfig(), Secrets(ntfy_topic="x", pushover_user_key="u", pushover_app_token="t"), _http(calls))
    assert both.provider == "pushover"
    forced = PushNotifier(PushConfig(provider="ntfy"), Secrets(ntfy_topic="x", pushover_user_key="u", pushover_app_token="t"), _http(calls))
    assert forced.provider == "ntfy"
    assert not await PushNotifier(PushConfig(), Secrets(), _http(calls)).send("a", "b")


async def test_manual_link_triggers_push(settings, tmp_path) -> None:  # type: ignore[no-untyped-def]

    from dealbot.app import DealBot
    from dealbot.collectors import BaseCollector, register
    from dealbot.config import CollectorConfig
    from dealbot.models import Product

    @register("fake_push")
    class FakeCollector(BaseCollector):
        products: list[Product] = []

        async def collect(self) -> list[Product]:
            return list(FakeCollector.products)

    settings.collectors = [CollectorConfig(name="fake", type="fake_push", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False
    settings.secrets.ntfy_topic = "topic1"
    bot = DealBot(settings)
    calls: list[httpx.Request] = []
    bot.push = PushNotifier(settings.monitoring.push, settings.secrets, _http(calls))
    bot.notifier.push = bot.push
    bot.notifier.bot_username = "mydealbot"
    try:
        FakeCollector.products = [Product(source="fake", product_id="toss:1", shop="toss", name="토스 상품", price=9900, url="https://toss.im/_m/1", recommend_count=9)]
        await bot.run_collector(bot.collectors[0])
        await bot.process_queue_once()
        assert bot.db.queue_counts() == {"awaiting_link": 1}
        assert len(calls) == 1
        body = json.loads(calls[0].content)
        assert body["topic"] == "topic1" and "링크가 필요합니다 #" in body["title"] and "[토스쇼핑]" in body["title"]
        assert body["click"] == "https://t.me/mydealbot" and "https://toss.im/_m/1" in body["message"]
        checks = await bot.self_check()
        assert any("휴대폰 푸시: ntfy" in t for _, t in checks)
    finally:
        await bot.close()
