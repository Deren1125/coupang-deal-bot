"""주인 요청 (10/10): 내 링크가 필요한 딜은 카카오톡 '나에게 보내기'로도, 링크를 붙이면 '상품 링크 완료', 올라가면 한 번 더.

카톡 토큰은 Blog-Auto 가 가지고 있어서, 딜봇은 Blog-Auto 받은편지함(blogauto_inbox.jsonl)에 {"owner_notice": …} 한 줄을 남기고
Blog-Auto 데몬이 카톡으로 보낸다.
"""

from __future__ import annotations

import json

import pytest

from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _inbox(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _product(**kw: object) -> Product:
    base: dict = dict(source="ppomppu", product_id="naver:13008954661", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm",
                      price=60020, url="https://brand.naver.com/daekket/products/13008954661")
    base.update(kw)
    return Product(**base)


async def test_link_request_and_link_done_go_to_kakao_inbox(bot: DealBot, settings: Settings) -> None:
    sent: list[str] = []

    async def fake_send(text: str, *, silent: bool = False) -> int:
        sent.append(text)
        return 1

    bot.notifier.send_with_id = fake_send  # type: ignore[method-assign]
    deal = Deal(_product(), DealVerdict(is_deal=True))
    assert bot.db.enqueue(deal, score=1)
    item = bot.db.pending_items()[0]
    shop = bot.registry.get("naver")
    await bot.notifier.notify_manual_link(item, shop)
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u]
    assert notes and notes[-1]["kind"] == "link_request"
    assert f"#{item.id}" in notes[-1]["text"] and "brand.naver.com/daekket" in notes[-1]["text"]
    assert len(notes[-1]["text"]) <= 200  # 카톡 텍스트 한 통
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual")
    msg = await bot.attach_link(item.id, "https://naver.me/abc")
    assert "상품 링크 완료" in msg and "데켓 주물" in msg
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u]
    assert notes[-1]["kind"] == "link_done" and "상품 링크 완료" in notes[-1]["text"]
    assert bot.db.get_queue_item(item.id).deal.product.extra.get("owner_link") is True


async def test_relay_can_be_turned_off(bot: DealBot, settings: Settings) -> None:
    bot.notifier.cfg.kakao_relay = False
    bot.notifier._relay("link_request", "x")
    assert not [u for u in _inbox(settings) if "owner_notice" in u]


async def test_owner_linked_deal_gets_a_published_notice_even_when_notices_are_off(bot: DealBot) -> None:
    sent: list[str] = []

    async def fake_send(text: str, *, silent: bool = False) -> int:
        sent.append(text)
        return 1

    bot.notifier.send_with_id = fake_send  # type: ignore[method-assign]
    bot.notifier.cfg.notify_on_publish = False
    plain = Deal(_product(), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(plain, PublishResult(ok=True, message_id=1, dry_run=False))
    assert sent == []  # 알림 끔 — 보통 딜은 조용히
    mine = Deal(_product(extra={"owner_link": True}), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(mine, PublishResult(ok=True, message_id=2, dry_run=False))
    assert sent and "링크를 붙여 주신 상품을 채널에 올렸어요" in sent[-1]


def test_long_store_url_keeps_kakao_text_in_one_message() -> None:
    from dealbot.monitoring.admin import _short_url

    url = "https://brand.naver.com/daekket/products/13008954661?" + "nl-query=%EB%8D%B0%EC%BC%93&" * 6
    assert _short_url(url) == "https://brand.naver.com/daekket/products/13008954661"
    assert _short_url("https://link.coupang.com/a/abc") == "https://link.coupang.com/a/abc"


async def test_owner_linked_deal_relays_a_published_notice_to_kakao(bot: DealBot, settings: Settings) -> None:
    async def fake_send(text: str, *, silent: bool = False) -> int:
        return 1

    bot.notifier.send_with_id = fake_send  # type: ignore[method-assign]
    bot.notifier.channel_post_base = "https://t.me/oneul_hotdeal"
    plain = Deal(_product(), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(plain, PublishResult(ok=True, message_id=1, dry_run=False))
    practice = Deal(_product(extra={"owner_link": True}), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(practice, PublishResult(ok=True, message_id=2, dry_run=True))
    assert not [u for u in _inbox(settings) if "owner_notice" in u]  # 보통 딜·연습 발행은 카톡 안 보냄
    mine = Deal(_product(name="데켓 주물 IH 오발쿡플레이트 31cm " + "아주 긴 상품명 " * 20, extra={"owner_link": True}),
                DealVerdict(is_deal=True))
    await bot.notifier.notify_published(mine, PublishResult(ok=True, message_id=77, dry_run=False))
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u]
    assert len(notes) == 1 and notes[0]["kind"] == "published"
    text = notes[0]["text"]
    assert "채널에 올렸어요" in text and "데켓 주물" in text and "https://t.me/oneul_hotdeal/77" in text
    assert "<" not in text and len(text) <= 200  # HTML 없이 카톡 한 통


def test_channel_post_base_only_for_public_channels() -> None:
    from dealbot.monitoring.admin import channel_post_base

    assert channel_post_base("@oneul_hotdeal") == "https://t.me/oneul_hotdeal"
    assert channel_post_base("-1001234567890", "https://t.me/oneul_hotdeal") == "https://t.me/oneul_hotdeal"
    assert channel_post_base("-1001234567890", "https://t.me/+AbCdEf") is None
    assert channel_post_base(None) is None
