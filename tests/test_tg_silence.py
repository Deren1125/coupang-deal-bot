"""텔레그램 관리자 챗 조용히 (주인 10/18 — monitoring.telegram_owner_notices: false + notify.router: push).
알림은 하나도 안 보내고(가짜 봇이 받은 것 0), 각 알림은 정한 곳(대시보드 할 일·정리·연결)에 간다. 명령 답장은 그대로."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from dash_helpers import push_settings, queued
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, PublishResult
from dealbot.page_price import PageCheck
from dealbot.storage.db import MAYBE_POSTED
from dealbot.utils.timeutil import utcnow


class FakeTg:
    """가짜 텔레그램 봇: 관리자 챗으로 간 것을 모두 기록."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def send_message(self, **kw: Any) -> SimpleNamespace:
        self.calls.append(("send", kw.get("text")))
        return SimpleNamespace(message_id=len(self.calls))

    async def edit_message_text(self, **kw: Any) -> None:
        self.calls.append(("edit", kw.get("text")))

    async def pin_chat_message(self, **kw: Any) -> None:
        self.calls.append(("pin", kw.get("message_id")))


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    b = DealBot(push_settings(settings))
    b.notifier.bot = FakeTg()  # type: ignore[assignment]
    b.notifier.chat_id = 42
    yield b
    b.db.close()


def open_keys(bot: DealBot) -> set[str]:
    import time

    return {n.key for n in bot.router.store.open_rows(time.time())}  # type: ignore[union-attr]


async def test_every_owner_notice_path_is_silent_and_lands_on_the_dashboard(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    tg: FakeTg = bot.notifier.bot  # type: ignore[assignment]
    n = bot.notifier
    link = queued(bot)
    await n.notify_manual_link(link, bot.registry.get("toss"))
    review = queued(bot, status="awaiting_approval", product_id="toss:2")
    await n.notify_deal_review(review, "토스", "공식 판매처 확인 필요")
    info = queued(bot, status="awaiting_approval", product_id="toss:3")
    await n.notify_info_review(info, "본문 미리보기", reason="요약 실패")
    unsure = queued(bot, status="failed", error=f"{MAYBE_POSTED}: t", product_id="toss:4")
    await n.notify_publish_unsure(unsure.id, unsure.deal, "timeout")
    for _ in range(3):
        await n.publish_failed_final("telegram 500")
    await n.notify_publish_failed(link.deal, "telegram 500", final=True)
    await n.notify_sold_out_cancel(link, "게시판", 7)
    await n.notify_link_replaced(link, 99, 7, until="14:20")
    await n.notify_price_hold(link.id, link.deal.product, PageCheck(status="hold", title_price=10000, page_price=20000, reason="price"))
    await n.notify_error("collector:ppomppu", "boom")
    await n.notify_startup("STATUS", ["✅ ok"])
    deal = Deal(link.deal.product, DealVerdict(is_deal=True))
    deal.product.extra["owner_link"] = True
    await n.notify_published(deal, PublishResult(ok=True, message_id=5, dry_run=False))
    await bot.late_photo.ask(link.id, link.deal, utcnow())
    await bot._sns_failed("스레드", "threadsauth", "⚠️ 스레드 실패", "server 500")
    await bot._sns_failed("스레드", "threadsauth", "⚠️ 스레드 실패", "token expired")
    await bot._notify_quiet("✅ 스레드 연결 완료")
    await bot._replaced_for_link(None, 1)
    await bot.refresh_pending_notice()
    n._err_since["collector:ppomppu"] = utcnow() - timedelta(hours=3)
    await n.notify_error("collector:ppomppu", "boom again")
    assert tg.calls == []  # 관리자 챗으로는 아무것도
    keys = open_keys(bot)
    for want in (f"link:{link.id}", f"review:{review.id}", f"review:{info.id}", f"unsure:{unsure.id}", "publish_broken",
                 f"photo:{link.id}", "sns:threadsauth", "collector:ppomppu"):
        assert want in keys or want.startswith("link:"), want  # link:N 은 품절로 해결됨
    assert f"link:{link.id}" not in keys  # 품절 → 해결
    import json
    import time
    from zoneinfo import ZoneInfo

    day = __import__("datetime").datetime.fromtimestamp(time.time(), ZoneInfo(bot.settings.app.timezone)).date().isoformat()
    cats = {e["cat"] for e in bot.router.summaries(day)}  # type: ignore[union-attr]
    assert {"soldout", "held", "published"} <= cats
    assert json.loads(bot.db.kv_get("dash:oauth_note") or "{}")["text"] == "스레드 연결됐어요"
    rows = {nn.key: nn for nn in bot.router.store.open_rows(time.time())}  # type: ignore[union-attr]
    assert rows["publish_broken"].urgent and rows["publish_broken"].sev == "stop" and rows["publish_broken"].iid == "sys-dealbot"
    assert not rows["collector:ppomppu"].urgent  # 정리에만
    assert any(k.startswith("publish_failed:") for k in rows) and any(k.startswith("sns_fail:") for k in rows)
    for row in rows.values():  # 대시보드 말 — 텔레그램을 가리키지 않음
        assert "텔레그램" not in row.title + row.body and "/link" not in row.body and "/ok" not in row.body, row.key


async def test_threads_replies_go_to_the_table_not_the_chat(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    tg: FakeTg = bot.notifier.bot  # type: ignore[assignment]
    flow = bot.threads_flow
    from dealbot.storage.sns_store import ThreadsJob

    job = ThreadsJob(id=1, kind="info", product_id="p", queue_id=None, payload={"name": "정보 글"}, score=0, status="posted",
                     reason=None, post_id="T1", dry_run=False, created_at=utcnow(), posted_at=utcnow(), soldout_reply_at=None)
    monkeypatch.setattr(flow, "active", lambda: True)
    bot.threads.dry_run = False
    monkeypatch.setattr(bot.threads.scopes, "usable", lambda _s: True)
    monkeypatch.setattr(bot.db, "threads_posted_since", lambda _since: [job])

    async def replies(_pid: str) -> list[dict[str, Any]]:
        return [{"id": "R1", "text": "이거 아직 팔아요?", "username": "someone", "permalink": "https://www.threads.com/@x/post/1"}]

    monkeypatch.setattr(bot.threads, "read_replies", replies)
    flow.cfg.replies.enabled = True
    assert await flow.collect_replies(utcnow()) == 1
    assert tg.calls == []
    import time

    [r] = bot.router.store.open_replies(time.time())  # type: ignore[union-attr]
    assert r["reply_id"] == "R1" and r["reply_text"] == "이거 아직 팔아요?" and len(r["drafts"]) == 2
    assert any(k.startswith("threads_replies:") for k in open_keys(bot))
    item = bot.dash_state.item("sns-replies")
    assert item["detail"]["replies"][0]["drafts"] and item["detail"]["replies"][0]["open"].startswith("https://")
    assert await flow.collect_replies(utcnow()) == 0  # 같은 답글은 한 번


async def test_owner_commands_still_reply_and_pending_is_the_batch_backup(bot: DealBot) -> None:
    from dealbot.monitoring.admin import register_admin_handlers

    class App:
        def __init__(self) -> None:
            self.handlers: list[Any] = []

        def add_handler(self, h: Any, group: int = 0) -> None:
            self.handlers.append(h)

        def add_error_handler(self, h: Any) -> None:
            pass

        def cb(self, name: str) -> Any:
            return next(h.callback for h in self.handlers if getattr(h.callback, "__name__", "") == name)

    app = App()
    register_admin_handlers(app, admin_chat_id=42, reporter=bot.reporter, controller=bot, nl=bot.nl)  # type: ignore[arg-type]
    out: list[str] = []

    class Message:
        message_id = 500
        date = utcnow()
        chat = SimpleNamespace(id=42, username=None)
        text = "/pending"

        async def reply_text(self, text: str, **_kw: Any) -> SimpleNamespace:
            out.append(text)
            return SimpleNamespace(message_id=777)

    update = SimpleNamespace(effective_message=Message(), effective_chat=Message.chat, callback_query=None)
    await app.cb("drop_stale")(update, None)
    await app.cb("cmd_pending")(update, None)
    assert out and "링크" in out[0]
    assert bot.db.admin_msg(777)["kind"] == "pending_notice"  # type: ignore[index]
    # 주인 명령을 처리하는 동안에는 그 결과 메시지가 관리자 챗으로 간다 (텔레그램 비상용 길)
    await bot.notifier.send("명령 결과")
    assert bot.notifier.bot.calls == [("send", "명령 결과")]  # type: ignore[union-attr]
    # 관리자 챗이 아닌 곳의 업데이트는 다시 조용히
    other = SimpleNamespace(effective_message=None, effective_chat=SimpleNamespace(id=1, username=None), callback_query=None)
    await app.cb("drop_stale")(other, None)
    await bot.notifier.send("알림")
    assert len(bot.notifier.bot.calls) == 1  # type: ignore[union-attr]


async def test_flag_on_brings_telegram_back_and_legacy_router_is_unchanged(settings: Settings) -> None:
    settings = push_settings(settings)
    settings.monitoring.telegram_owner_notices = True
    b = DealBot(settings)
    b.notifier.bot, b.notifier.chat_id = FakeTg(), 42  # type: ignore[assignment]
    try:
        await b.notifier.send("x")
        assert b.notifier.bot.calls == [("send", "x")]  # type: ignore[union-attr]
    finally:
        b.db.close()
    settings.notify.router = "legacy"
    b = DealBot(settings)
    try:
        assert b.router is None and b.notifier.router is None and not b.notifier.tg_quiet
    finally:
        b.db.close()


async def test_last_resort_is_loud_only_when_push_and_kakao_are_both_down(settings: Settings) -> None:
    settings = push_settings(settings)
    settings.monitoring.telegram_last_resort = True
    b = DealBot(settings)
    b.notifier.bot, b.notifier.chat_id = FakeTg(), 42  # type: ignore[assignment]
    try:
        item = queued(b)
        await b.notifier.notify_manual_link(item, b.registry.get("toss"))
        calls = b.notifier.bot.calls  # type: ignore[union-attr]
        assert len(calls) == 1 and "카톡 알림이 안 돼서" in calls[0][1]  # 기기 없음 + Blog-Auto 신호 없음(카톡 못 믿음)
    finally:
        b.db.close()


def test_channel_publisher_is_untouched(bot: DealBot) -> None:
    from dealbot.publisher.telegram import TelegramPublisher

    assert isinstance(bot.publisher, TelegramPublisher) and bot.publisher.bot is bot.bot
