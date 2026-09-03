"""관리자 개인 챗: 알림(발행/실패/에러/일일 요약) + 명령어(/status 등)."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from dealbot import __version__
from dealbot.config import MonitoringConfig, Settings
from dealbot.models import Deal, PublishResult
from dealbot.monitoring.state import BotState
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import normalize_chat_id
from dealbot.publisher.templates import TemplateRenderer
from dealbot.storage.db import Database, PeriodSummary
from dealbot.utils.text import truncate
from dealbot.utils.timeutil import fmt_local, humanize_delta, utcnow

log = logging.getLogger(__name__)


class AdminNotifier:
    def __init__(
        self,
        bot: Bot | None,
        admin_chat_id: int | str | None,
        cfg: MonitoringConfig,
        renderer: TemplateRenderer,
        tz: str,
    ) -> None:
        self.bot = bot
        self.chat_id = normalize_chat_id(admin_chat_id)
        self.cfg = cfg
        self.renderer = renderer
        self.tz = tz
        self._last_alert: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return self.bot is not None and self.chat_id is not None

    async def send(self, text: str, *, silent: bool = False) -> bool:
        if not self.enabled:
            log.debug("[admin notice suppressed — no admin chat] %s", text.replace("\n", " | ")[:200])
            return False
        assert self.bot is not None
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text[:4096],
                parse_mode=ParseMode.HTML,
                disable_notification=silent,
                link_preview_options=None,
            )
            return True
        except TelegramError as e:
            log.error("admin notify failed: %s", e)
            return False

    async def notify_startup(self, status_text: str) -> None:
        await self.send(f"🟢 <b>DealBot v{__version__} 시작</b>\n\n{status_text}", silent=True)

    async def notify_published(self, deal: Deal, result: PublishResult) -> None:
        if not self.cfg.notify_on_publish:
            return
        p = deal.product
        tag = "DRY-RUN " if result.dry_run else ""
        reasons = ", ".join(deal.verdict.reasons) or "-"
        text = (
            f"✅ <b>{tag}발행</b> [{html.escape(p.source)}]\n"
            f"{html.escape(truncate(p.name, 80))}\n"
            f"{p.price:,}원 · 점수 {deal.verdict.score:g} · {html.escape(reasons)}"
        )
        if deal.affiliate_url:
            text += f"\n{html.escape(deal.affiliate_url)}"
        await self.send(text, silent=True)

    async def notify_publish_failed(self, deal: Deal, error: str, *, final: bool) -> None:
        if not self.cfg.notify_on_failure:
            return
        p = deal.product
        head = "❌ <b>발행 실패 (포기)</b>" if final else "⚠️ <b>발행 실패 (재시도 예정)</b>"
        await self.send(f"{head} [{html.escape(p.source)}]\n{html.escape(truncate(p.name, 80))}\n<code>{html.escape(error[:500])}</code>")

    async def notify_error(self, kind: str, message: str) -> None:
        if not self.cfg.notify_on_error:
            return
        now = utcnow()
        last = self._last_alert.get(kind)
        cooldown = timedelta(minutes=self.cfg.error_alert_cooldown_minutes)
        if last is not None and now - last < cooldown:
            log.debug("error alert for %s suppressed (cooldown)", kind)
            return
        self._last_alert[kind] = now
        await self.send(f"🚨 <b>에러</b> <code>{html.escape(kind)}</code>\n<code>{html.escape(message[:1500])}</code>")

    async def notify_daily_summary(self, text: str) -> None:
        await self.send(text)


class StatusReporter:
    """/status, /queue, 일일 요약 텍스트 생성."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        state: BotState,
        rate_limiter: RateLimiter,
        renderer: TemplateRenderer,
    ) -> None:
        self.settings = settings
        self.db = db
        self.state = state
        self.rate = rate_limiter
        self.renderer = renderer

    def _collector_rows(self, now: datetime) -> list[dict[str, Any]]:
        tz = self.settings.app.timezone
        rows: list[dict[str, Any]] = []
        for st in self.state.collectors.values():
            last = self.db.last_run(st.name)
            rows.append(
                {
                    "name": st.name,
                    "type": st.type,
                    "enabled": st.enabled,
                    "available": st.available,
                    "unavailable_reason": st.unavailable_reason,
                    "running": st.running,
                    "interval_minutes": st.interval_minutes,
                    "last_status": last.status if last else "-",
                    "last_run_ago": humanize_delta(now - last.started_at) + " 전" if last else "-",
                    "last_run_at": fmt_local(last.started_at, tz) if last else "-",
                    "collected": last.collected if last else 0,
                    "deals": last.deals if last else 0,
                    "queued": last.queued if last else 0,
                    "error": truncate(last.error, 120) if last and last.error else None,
                    "next_in": humanize_delta(st.next_run_at - now) if st.next_run_at and st.next_run_at > now else "곧",
                }
            )
        return rows

    def status_context(self) -> dict[str, Any]:
        now = utcnow()
        tz = self.settings.app.timezone
        last_err = self.db.last_error()
        return {
            "version": __version__,
            "uptime": humanize_delta(now - self.state.started_at),
            "paused": self.state.paused,
            "dry_run": self.state.dry_run,
            "publish_enabled": self.settings.publish.enabled,
            "has_coupang": self.settings.secrets.has_coupang,
            "has_channel": self.settings.secrets.has_channel,
            "collectors": self._collector_rows(now),
            "rate": self.rate.snapshot(now),
            "queue": self.db.queue_counts(),
            "products": self.db.product_count(),
            "price_points": self.db.price_history_count(),
            "db_mb": round(self.db.db_size_bytes() / 1024 / 1024, 1),
            "last_error": truncate(last_err["message"].splitlines()[0], 300) if last_err else None,
            "last_error_at": fmt_local(datetime.fromisoformat(last_err["ts"]), tz) if last_err else None,
            "tz": tz,
        }

    def status_text(self) -> str:
        return self.renderer.render("status.j2", **self.status_context())

    def queue_text(self, limit: int = 10) -> str:
        items = self.db.pending_items(limit)
        counts = self.db.queue_counts()
        lines = [f"🗂 <b>대기열</b> pending {counts.get('pending', 0)} · failed {counts.get('failed', 0)} · expired {counts.get('expired', 0)}"]
        for it in items:
            p = it.deal.product
            lines.append(f"• [{p.source}] {html.escape(truncate(p.name, 50))} — {p.price:,}원 (점수 {it.score:g}, 시도 {it.attempts})")
        if not items:
            lines.append("(대기 중인 특가 없음)")
        return "\n".join(lines)

    def recent_text(self, limit: int = 10) -> str:
        posts = self.db.recent_posts(limit)
        tz = self.settings.app.timezone
        lines = ["📤 <b>최근 발행</b>"]
        for r in posts:
            when = fmt_local(datetime.fromisoformat(r["posted_at"]), tz)
            lines.append(f"• {when} [{r['source']}] {html.escape(truncate(r.get('name') or r['product_id'], 50))} — {r['price']:,}원")
        if not posts:
            lines.append("(없음)")
        return "\n".join(lines)

    def errors_text(self, limit: int = 8) -> str:
        events = self.db.recent_events(limit, level="ERROR")
        tz = self.settings.app.timezone
        lines = ["🚨 <b>최근 에러</b>"]
        for e in events:
            when = fmt_local(datetime.fromisoformat(e["ts"]), tz)
            lines.append(f"• {when} <code>{html.escape(e['kind'])}</code> {html.escape(truncate(e['message'], 160))}")
        if not events:
            lines.append("(없음)")
        return "\n".join(lines)

    def summary_text(self, summary: PeriodSummary) -> str:
        return self.renderer.render("daily_summary.j2", s=summary, tz=self.settings.app.timezone)


class BotController(Protocol):
    """관리자 명령이 조작하는 인터페이스 (app.DealBot 이 구현)."""

    state: BotState

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def request_run(self, collector: str | None) -> str: ...

    def collector_names(self) -> list[str]: ...


HELP_TEXT = (
    "🤖 <b>DealBot 명령어</b>\n"
    "/status — 현재 상태\n"
    "/queue — 발행 대기열\n"
    "/recent — 최근 발행 목록\n"
    "/errors — 최근 에러\n"
    "/run [수집기이름] — 지금 바로 수집 실행\n"
    "/pause — 수집/발행 일시정지\n"
    "/resume — 재개\n"
    "/help — 도움말"
)


def register_admin_handlers(
    app: Application,  # type: ignore[type-arg]
    *,
    admin_chat_id: int | str,
    reporter: StatusReporter,
    controller: BotController,
) -> None:
    chat_id = normalize_chat_id(admin_chat_id)
    only_admin = filters.Chat(chat_id=chat_id) if isinstance(chat_id, int) else filters.Chat(username=str(chat_id).lstrip("@"))

    async def reply(update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text[:4096], parse_mode=ParseMode.HTML)

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.status_text())

    async def cmd_queue(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.queue_text())

    async def cmd_recent(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.recent_text())

    async def cmd_errors(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.errors_text())

    async def cmd_pause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.pause()
        await reply(update, "⏸ 일시정지됨. /resume 으로 재개")

    async def cmd_resume(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.resume()
        await reply(update, "▶️ 재개됨")

    async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        name = ctx.args[0] if ctx.args else None
        await reply(update, controller.request_run(name))

    async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, HELP_TEXT)

    async def cmd_unknown_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        # 관리자 외 사용자의 명령: 채팅 ID 만 알려주고 무시 (chat-id 확인용)
        if update.effective_chat and update.effective_message:
            await update.effective_message.reply_text(
                f"이 봇은 개인 관리용입니다. (chat id: {update.effective_chat.id})"
            )

    for cmd, fn in (
        ("status", cmd_status),
        ("queue", cmd_queue),
        ("recent", cmd_recent),
        ("errors", cmd_errors),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("run", cmd_run),
        ("help", cmd_help),
        ("start", cmd_help),
    ):
        app.add_handler(CommandHandler(cmd, fn, filters=only_admin))
    app.add_handler(CommandHandler(["start", "status", "help"], cmd_unknown_chat, filters=~only_admin))
