"""관리자 개인 챗: 알림(발행/실패/에러/수동 링크 요청/일일 요약) + 명령어(/status /link /post ...)."""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Protocol

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from dealbot import __version__
from dealbot.config import MonitoringConfig, Settings
from dealbot.links import LinkRouter
from dealbot.models import Deal, PublishResult
from dealbot.monitoring.push import PushNotifier
from dealbot.monitoring.state import BotState
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import normalize_chat_id
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import Shop, ShopRegistry, find_urls
from dealbot.storage.db import Database, PeriodSummary, QueueItem
from dealbot.utils.text import truncate
from dealbot.utils.timeutil import fmt_local, humanize_delta, utcnow

log = logging.getLogger(__name__)
_QUEUE_REF_RE = re.compile(r"#(\d+)")
STALE_COMMAND_MAX_AGE = timedelta(minutes=15)  # 봇이 꺼져 있던 동안 쌓인 명령 중 이보다 오래된 것은 무시


# 수집기(출처) 코드명 → 관리자 챗 표시명. config.yaml 의 collectors[].label 이 있으면 그쪽이 우선
COLLECTOR_LABELS = {
    "ppomppu": "뽐뿌",
    "ruliweb_user": "루리웹 유저 핫딜",
    "ruliweb_biz": "루리웹 업체 핫딜",
    "goldbox": "쿠팡 골드박스",
    "category_best": "쿠팡 카테고리 베스트",
    "algumon": "알구몬",
    "adpick": "애드픽",
    "manual": "내가 직접 올림",
}

_REASON_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^interest:recommend>=(\d+)$"), "관심도 통과(추천 {0}개 이상)"),
    (re.compile(r"^interest:comments>=(\d+)$"), "관심도 통과(댓글 {0}개 이상)"),
    (re.compile(r"^interest:views>=(\d+)$"), "관심도 통과(조회 {0}회 이상)"),
    (re.compile(r"^interest:rank<=(\d+)$"), "관심도 통과(순위 {0}위 안)"),
    (re.compile(r"^below_coupang_price>=([\d.]+)%$"), "쿠팡 최저가보다 {0}% 이상 쌈"),
    (re.compile(r"^below_(\w+)_price>=([\d.]+)%$"), "{0} 최저가보다 {1}% 이상 쌈"),
    (re.compile(r"^below_(\d+)d_avg>=([\d.]+)%$"), "최근 {0}일 평균가보다 {1}% 이상 쌈"),
    (re.compile(r"^recommend>=(\d+)$"), "커뮤니티 추천 {0}개 이상"),
    (re.compile(r"^discount_rate>=([\d.]+)%$"), "표시 할인율 {0}% 이상"),
    (re.compile(r"^market_diff=([-\d.]+)%$"), "쿠팡보다 {0}% 쌈(기준 미만)"),
    (re.compile(r"^manual$"), "내가 직접 올림"),
    (re.compile(r"^discount_unconfirmed$"), "할인율만 있고 쿠팡 대조로 확인 안 됨"),
]


_KIND_LABELS = {
    "publish": "채널에 올리기",
    "publisher": "발행 작업",
    "summary": "일일 요약",
    "maintenance": "정리 작업",
    "threads": "스레드 게시",
    "control": "관리자 조작",
    "lifecycle": "시작/종료",
}


def describe_kind(kind: str, labels: dict[str, str] | None = None) -> str:
    """에러 종류 코드 → 사람 말 ("collector:ppomppu" → "뽐뿌 핫딜 확인 중")."""
    if kind.startswith("collector:"):
        name = kind.split(":", 1)[1]
        label = (labels or {}).get(name) or COLLECTOR_LABELS.get(name, name)
        return f"{label} 확인 중"
    return _KIND_LABELS.get(kind, kind)


def humanize_reason(code: str) -> str:
    for rx, fmt in _REASON_RULES:
        m = rx.match(code)
        if m:
            return fmt.format(*m.groups())
    return code


def humanize_reasons(reasons: list[str]) -> str:
    return " · ".join(humanize_reason(r) for r in reasons) or "-"


def heartbeat_due(last_activity: datetime, now: datetime, minutes: int) -> bool:
    """관리자 챗이 minutes 동안 조용했으면 True (특가/링크/에러 알림이 있었으면 그것으로 생존 신고를 대신한다)."""
    return minutes > 0 and now - last_activity >= timedelta(minutes=minutes)


# 텔레그램 "/" 메뉴에 등록할 명령 (이름은 영문 소문자·숫자·밑줄만 가능 — 텔레그램 규칙)
BOT_COMMANDS: list[tuple[str, str]] = [
    ("status", "지금 상태 (수집기·발행·대기열)"),
    ("queue", "발행 대기열"),
    ("pending", "내 링크가 필요한 항목"),
    ("recent", "최근 발행 목록"),
    ("hot", "추천 많이 받은 게시판 글과 원문 링크 (/hot 5)"),
    ("find", "수집된 글 검색 (/find 키워드)"),
    ("errors", "최근 에러"),
    ("run", "지금 바로 수집 (/run 수집기이름)"),
    ("pause", "발행 일시정지"),
    ("resume", "발행 재개"),
    ("post", "직접 딜 올리기"),
    ("link", "링크 요청에 답: 만든 제휴 링크 붙이기 (/link 번호 링크)"),
    ("skip", "그 글은 올리지 않기 (/skip 번호)"),
    ("copy", "올린 글의 카카오·블로그 복붙 문구 (/copy 번호)"),
    ("test", "채널에 올라갈 글 양식 미리 보기 (샘플)"),
    ("pushtest", "휴대폰 푸시(ntfy) 연결 확인"),
    ("ppstats", "커뮤니티 글 추천 분포"),
    ("threadsauth", "스레드 연결 (최초 1회)"),
    ("threadscode", "스레드 인증 코드 입력"),
    ("help", "도움말"),
]


def is_stale_message(sent_at: datetime | None, now: datetime | None = None, max_age: timedelta = STALE_COMMAND_MAX_AGE) -> bool:
    if sent_at is None:
        return False
    now = now or utcnow()
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=now.tzinfo)
    return now - sent_at > max_age


class AdminNotifier:
    def __init__(
        self,
        bot: Bot | None,
        admin_chat_id: int | str | None,
        cfg: MonitoringConfig,
        renderer: TemplateRenderer,
        tz: str,
        push: PushNotifier | None = None,
        registry: ShopRegistry | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = normalize_chat_id(admin_chat_id)
        self.cfg = cfg
        self.renderer = renderer
        self.tz = tz
        self.push = push
        self.registry = registry
        self.labels = labels or {}
        self.bot_username: str | None = None
        self.last_sent_at: datetime | None = None  # 마지막으로 관리자 챗에 무언가 보낸 시각 (하트비트 판단용)
        self._last_alert: dict[str, datetime] = {}

    @property
    def enabled(self) -> bool:
        return self.bot is not None and self.chat_id is not None

    @property
    def telegram_link(self) -> str | None:
        return f"https://t.me/{self.bot_username}" if self.bot_username else None

    async def _push(self, event: str, title: str, message: str, *, priority: str = "default", tags: list[str] | None = None) -> None:
        if self.push is None or not self.push.wants(event):
            return
        await self.push.send(title, message, click_url=self.telegram_link, priority=priority, tags=tags)

    async def send(self, text: str, *, silent: bool = False) -> bool:
        return (await self.send_with_id(text, silent=silent)) is not None

    async def send_with_id(self, text: str, *, silent: bool = False) -> int | None:
        """보내고 텔레그램 message_id 를 돌려준다 (나중에 고치거나 지우려고)."""
        if not self.enabled:
            log.warning(
                "관리자 알림을 보낼 수 없어 건너뜁니다 (봇 토큰/관리자 챗 ID 확인 필요): %s",
                text.replace("\n", " | ")[:150],
            )
            return None
        assert self.bot is not None
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id,
                text=text[:4096],
                parse_mode=ParseMode.HTML,
                # silent 는 "일상 알림" 표시일 뿐, 실제 무음 여부는 monitoring.quiet_notices 가 결정
                disable_notification=silent and self.cfg.quiet_notices,
                link_preview_options=None,
            )
            self.last_sent_at = utcnow()
            return int(getattr(msg, "message_id", 0) or 0)  # id 를 모르면 0 (성공은 성공)
        except TelegramError as e:
            log.error("admin notify failed: %s", e)
            return None

    async def edit(self, message_id: int, text: str) -> bool:
        """내가 보낸 관리자 챗 메시지를 고친다 (예: 품절된 링크 요청)."""
        if not self.enabled:
            return False
        assert self.bot is not None
        try:
            await self.bot.edit_message_text(chat_id=self.chat_id, message_id=message_id, text=text[:4096], parse_mode=ParseMode.HTML)
            return True
        except TelegramError as e:
            log.warning("admin edit failed (message %s): %s", message_id, e)
            return False

    def source_label(self, source: str) -> str:
        return self.labels.get(source) or COLLECTOR_LABELS.get(source, source)

    def shop_label(self, key: str) -> str:
        shop = self.registry.get(key) if self.registry else None
        return shop.name if shop else key

    async def notify_startup(self, status_text: str, check_lines: list[str] | None = None) -> bool:
        text = f"🟢 <b>봇이 켜졌습니다</b> (v{__version__})\n"
        if check_lines:
            text += "\n<b>자기 점검</b> — ✅ 정상 · ⚠️ 아직 설정 안 함(선택) · ❌ 문제\n" + "\n".join(html.escape(line) for line in check_lines) + "\n"
        text += f"\n{status_text}"
        sent = await self.send(text, silent=True)
        await self._push("startup", "DealBot 시작", "\n".join(check_lines or [])[:500] or "봇이 시작되었습니다.")
        return sent

    async def notify_published(self, deal: Deal, result: PublishResult, preview: str | None = None) -> None:
        """발행 알림. DRY-RUN 이면 채널에 올라갔을 글 전체(preview)를 그대로 보여준다."""
        if not self.cfg.notify_on_publish:
            return
        p = deal.product
        where = f"어디서: {html.escape(self.source_label(p.source))} → {html.escape(self.shop_label(p.shop))}"
        why = f"고른 이유: {html.escape(humanize_reasons(deal.verdict.reasons))} · 점수 {deal.verdict.score:g}"
        if result.dry_run and preview:
            photo = " · 🖼 사진 있음" if p.image_url else ""
            text = (
                f"🧪 <b>미리보기</b> — 연습 모드라 채널에는 올리지 않았습니다\n"
                f"{where}{photo}\n"
                f"{why}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{preview}"
            )
            await self.send(text, silent=True)
            return
        head = "🧪 <b>연습 발행</b>" if result.dry_run else "✅ <b>채널에 올렸습니다</b>"
        price = f"{p.price:,}원" if p.has_price else "가격 없음"
        text = (
            f"{head}\n"
            f"{html.escape(truncate(p.name, 80))} · {price}\n"
            f"{where}\n{why}"
        )
        if deal.affiliate_url:
            text += f"\n{html.escape(deal.affiliate_url)}"
        await self.send(text, silent=True)

    async def notify_publish_failed(self, deal: Deal, error: str, *, final: bool) -> None:
        if not self.cfg.notify_on_failure:
            return
        p = deal.product
        head = "❌ <b>채널에 올리지 못했습니다</b> (여러 번 실패해서 포기)" if final else "⚠️ <b>채널에 올리지 못했습니다</b> (잠시 뒤 다시 시도합니다)"
        await self.send(
            f"{head} [{html.escape(self.shop_label(p.shop))}]\n{html.escape(truncate(p.name, 80))}\n이유: <code>{html.escape(error[:500])}</code>"
        )
        if final:
            await self._push("publish_failed", f"채널에 올리지 못함 [{self.shop_label(p.shop)}]", f"{truncate(p.name, 60)}\n{error[:200]}")

    async def notify_manual_link(self, item: QueueItem, shop: Shop) -> int | None:
        """자동 변환이 안 되는 쇼핑몰: 관리자에게 링크 생성을 요청. 보낸 메시지 id 를 돌려준다."""
        if not self.cfg.notify_on_manual_link:
            return None
        p = item.deal.product
        price = f" — {p.price:,}원" if p.has_price else ""
        hint = shop.manual_hint or "앱/사이트에서 내 제휴 링크를 만들어 보내주세요"
        text = (
            f"🔗 <b>내 링크가 필요합니다 #{item.id}</b> [{html.escape(shop.name)}]\n"
            f"{html.escape(truncate(p.name, 80))}{price}\n"
            f"원본 주소: {html.escape(p.url)}\n"
            + (f"글: {html.escape(str(p.extra.get('post_url')))}\n" if p.extra.get("post_url") else "")
            + f"\n👉 {html.escape(hint)}\n"
            f"만든 링크를 <b>이 메시지에 답장</b>으로 보내면 바로 올라갑니다. 또는 <code>/link {item.id} https://...</code>\n"
            f"안 올리려면 <code>/skip {item.id}</code>"
        )
        message_id = await self.send_with_id(text)
        await self._push(
            "manual_link",
            f"내 링크가 필요합니다 #{item.id} [{shop.name}]",
            f"{truncate(p.name, 70)}{price}\n{p.url}\n\n{hint}",
            priority="high",
            tags=["link"],
        )
        return message_id

    async def notify_sold_out_cancel(self, item: QueueItem, via: str, notice_message_id: int | None) -> None:
        """내 링크를 기다리던 글이 품절됨: 보냈던 링크 요청 메시지를 고쳐서 헛수고를 막는다."""
        p = item.deal.product
        text = (
            f"⛔ <b>품절되어 취소했습니다 #{item.id}</b> [{html.escape(self.shop_label(p.shop))}]\n"
            f"{html.escape(truncate(p.name, 80))}\n"
            f"확인 경로: {html.escape(via)}. 링크를 만들 필요가 없습니다."
        )
        if notice_message_id and await self.edit(notice_message_id, text):
            return
        await self.send(text, silent=True)

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
        where = describe_kind(kind, self.labels)
        await self.send(
            f"🚨 <b>에러가 났습니다</b> — {html.escape(where)}\n<code>{html.escape(message[:1500])}</code>\n"
            f"(같은 종류는 {self.cfg.error_alert_cooldown_minutes}분에 한 번만 알립니다. 봇은 계속 돌아갑니다)"
        )
        await self._push("error", f"에러: {where}", message[:300], tags=["warning"])

    async def notify_daily_summary(self, text: str) -> None:
        await self.send(text)
        plain = re.sub(r"<[^>]+>", "", text)
        await self._push("daily_summary", "DealBot 일일 요약", plain[:800])


class StatusReporter:
    """/status, /queue, /pending, 일일 요약 텍스트 생성."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        state: BotState,
        rate_limiter: RateLimiter,
        renderer: TemplateRenderer,
        registry: ShopRegistry | None = None,
        links: LinkRouter | None = None,
        budget: Any | None = None,
    ) -> None:
        self.settings = settings
        self.db = db
        self.state = state
        self.rate = rate_limiter
        self.renderer = renderer
        self.registry = registry or settings.shop_registry()
        self.links = links
        self.budget = budget
        self.labels = {c.name: c.label or COLLECTOR_LABELS.get(c.name, c.name) for c in settings.collectors}

    def collector_label(self, name: str) -> str:
        return self.labels.get(name) or COLLECTOR_LABELS.get(name, name)

    def _collector_rows(self, now: datetime) -> list[dict[str, Any]]:
        tz = self.settings.app.timezone
        rows: list[dict[str, Any]] = []
        for st in self.state.collectors.values():
            last = self.db.last_run(st.name)
            rows.append(
                {
                    "name": st.name,
                    "label": self.collector_label(st.name),
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

    def _shop_rows(self) -> list[dict[str, Any]]:
        rows = []
        for s in self.registry.all():
            mode = self.links.describe(s) if self.links else s.link_mode
            rows.append({"key": s.key, "name": s.name, "enabled": s.enabled, "mode": mode, "reason": s.disabled_reason})
        return rows

    def status_context(self) -> dict[str, Any]:
        now = utcnow()
        tz = self.settings.app.timezone
        # 이번 실행(프로세스) 중에 난 에러만. 재시작 전 기록은 /errors 로 본다
        last_err = self.state.last_error
        last_err_at = self.state.last_error_at
        return {
            "version": __version__,
            "uptime": humanize_delta(now - self.state.started_at),
            "paused": self.state.paused,
            "dry_run": self.state.dry_run,
            "publish_enabled": self.settings.publish.enabled,
            "has_coupang": self.settings.secrets.has_coupang,
            "has_channel": self.settings.secrets.has_channel,
            "collectors": self._collector_rows(now),
            "shops": self._shop_rows(),
            # 꺼진 몰도 이유와 함께 보여준다: 제외한 몰 / 링크 변환기(ID)만 넣으면 자동으로 켜질 몰
            "shops_off_excluded": [s.name for s in self.registry.all() if not s.enabled and not s.disabled_reason],
            "shops_off_pending": {
                reason: [s.name for s in self.registry.all() if not s.enabled and s.disabled_reason == reason]
                for reason in dict.fromkeys(s.disabled_reason for s in self.registry.all() if not s.enabled and s.disabled_reason)
            },
            "rate": self.rate.snapshot(now),
            "queue": self.db.queue_counts(),
            "products": self.db.product_count(),
            "price_points": self.db.price_history_count(),
            "db_mb": round(self.db.db_size_bytes() / 1024 / 1024, 1),
            "api_budget": (
                {
                    "used": self.budget.used(),
                    "max": self.budget.max_per_hour,
                    "by_kind": ", ".join(f"{k} {v}" for k, v in sorted(self.budget.usage().items())),
                }
                if self.budget is not None and self.settings.secrets.has_coupang
                else None
            ),
            "last_error": truncate(last_err.splitlines()[0], 300) if last_err else None,
            "last_error_at": fmt_local(last_err_at, tz) if last_err_at else None,
            "tz": tz,
        }

    def status_text(self) -> str:
        return self.renderer.render("status.j2", **self.status_context())

    def _shop_name(self, key: str) -> str:
        shop = self.registry.get(key)
        return shop.name if shop else key

    def _item_line(self, it: QueueItem) -> str:
        p = it.deal.product
        price = f" · {p.price:,}원" if p.has_price else ""
        tries = f" · 실패 {it.attempts}번" if it.attempts else ""
        return f"• #{it.id} {html.escape(self._shop_name(p.shop))} · {html.escape(truncate(p.name, 50))}{price} · 점수 {it.score:g}{tries}"

    def queue_text(self, limit: int = 10) -> str:
        items = self.db.pending_items(limit)
        counts = self.db.queue_counts()
        lines = [
            "🗂 <b>올릴 차례를 기다리는 글</b>",
            f"차례 대기 {counts.get('pending', 0)}건 · 내 링크 대기 {counts.get('awaiting_link', 0)}건 · 실패 {counts.get('failed', 0)}건 · 시간 지나 버림 {counts.get('expired', 0)}건",
        ]
        lines += [self._item_line(it) for it in items]
        if not items:
            lines.append("(지금 기다리는 글 없음 — 특가가 잡히면 여기 쌓였다가 순서대로 올라갑니다)")
        else:
            lines.append("\n점수가 높은 글부터 올라갑니다. 빼려면 <code>/skip 번호</code>")
        return "\n".join(lines)

    def pending_text(self, limit: int = 15) -> str:
        items = self.db.awaiting_items(limit)
        lines = ["🔗 <b>내가 링크를 만들어 줘야 하는 글</b>"]
        for it in items:
            p = it.deal.product
            lines.append(self._item_line(it))
            lines.append(f"   원본 주소: {html.escape(p.url)}")
        if not items:
            lines.append("(없음 — 토스·네이버처럼 링크를 직접 만들어야 하는 글이 생기면 여기 뜹니다)")
        else:
            lines.append("\n링크를 만들었으면 그 요청 메시지에 답장하거나 <code>/link 번호 링크</code> · 안 올리려면 <code>/skip 번호</code>")
        return "\n".join(lines)

    def recent_text(self, limit: int = 10) -> str:
        posts = self.db.recent_posts(limit)
        tz = self.settings.app.timezone
        lines = ["📤 <b>최근에 올린 글</b>" + (" (연습 모드라 미리보기만 보낸 것)" if self.state.dry_run else "")]
        for r in posts:
            when = fmt_local(datetime.fromisoformat(r["posted_at"]), tz)
            lines.append(f"• {when} · {html.escape(self.collector_label(r['source']))} · {html.escape(truncate(r.get('name') or r['product_id'], 50))} · {r['price']:,}원")
        if not posts:
            lines.append("(아직 없음)")
        return "\n".join(lines)

    def errors_text(self, limit: int = 8) -> str:
        events = self.db.recent_events(limit, level="ERROR")
        tz = self.settings.app.timezone
        lines = ["🚨 <b>최근 에러</b> (재시작 전 것도 포함)"]
        for e in events:
            when = fmt_local(datetime.fromisoformat(e["ts"]), tz)
            lines.append(f"• {when} · {html.escape(describe_kind(e['kind'], self.labels))}\n   <code>{html.escape(truncate(e['message'].splitlines()[0], 160))}</code>")
        if not events:
            lines.append("(없음 — 깨끗합니다)")
        return "\n".join(lines)

    def community_stats_text(self) -> str:
        lines = ["📈 <b>게시판별 글 통계</b> — 글이 추천을 얼마나 받는지 보고, 기준이 너무 높거나 낮은지 판단하는 용도"]
        for label, hours in (("최근 24시간", 24), ("최근 7일", 24 * 7)):
            stats = self.db.community_stats(utcnow() - timedelta(hours=hours))
            lines.append(f"\n<b>{label}</b>")
            if not stats:
                lines.append("(아직 데이터 없음)")
            for src, st in stats.items():
                ge = st["rec_ge"]
                lines.append(
                    f"• {html.escape(self.collector_label(src))}: 글 {st['posts']}개 중 추천 1개↑ {ge[1]} · 3개↑ {ge[3]} · 5개↑ {ge[5]} · 10개↑ {ge[10]} · 20개↑ {ge[20]}"
                    f" · 조회 500↑ {st.get('views_ge_500', 0)} · 댓글 3개↑ {st.get('comments_ge_3', 0)}"
                )
        ic = self.settings.deal.interest
        lines.append(
            f"\n지금 기준: 추천 {ic.min_recommend}개 이상, 댓글 {ic.min_comments}개 이상, 조회 {ic.min_views} 이상, 순위 {ic.max_rank}위 안 중 하나면 판정 대상이 되고,"
            f" 가격이 적힌 글은 커뮤니티 추천 {self.settings.deal.community_min_recommend}개 이상이면 특가로 봅니다 (쿠팡 최저가 비교가 켜지면 그쪽이 우선)"
        )
        return "\n".join(lines)

    def hot_text(self, min_recommend: int = 5, hours: int = 24) -> str:
        tz = self.settings.app.timezone
        items = self.db.hot_items(utcnow() - timedelta(hours=hours), min_recommend=min_recommend)
        lines = [f"🔥 <b>최근 {hours}시간에 추천 {min_recommend}개 이상 받은 글</b> ({len(items)}건)\n"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz)
            block = (
                f"<b>{html.escape(truncate(it['title'] or '', 70))}</b>\n"
                f"{html.escape(self.collector_label(it['source']))} · {when} · 추천 {it['recommend']} · 조회 {it['views'] or '-'} · 댓글 {it['comments'] or '-'}"
            )
            if it.get("url"):
                block += f"\n원문: {html.escape(str(it['url']))}"
            lines.append(block + "\n")
        if not items:
            lines.append("(없음 — 아직 게시판을 안 봤거나, 그만큼 추천받은 글이 없습니다. 숫자를 낮춰 보세요: /hot 2)")
        return "\n".join(lines).rstrip()

    def find_text(self, keyword: str) -> str:
        tz = self.settings.app.timezone
        items = self.db.find_items(keyword)
        lines = [f"🔎 <b>'{html.escape(keyword)}' 가 들어간 글</b> ({len(items)}건)\n"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz, "%m/%d %H:%M")
            rec = it["recommend"] if it["recommend"] is not None else "-"
            block = (
                f"<b>{html.escape(truncate(it['title'] or '', 70))}</b>\n"
                f"{html.escape(self.collector_label(it['source']))} · 처음 본 시각 {when} · 추천 {rec}"
            )
            if it.get("url"):
                block += f"\n원문: {html.escape(str(it['url']))}"
            lines.append(block + "\n")
        if not items:
            lines.append("(수집한 글 중에는 없습니다)")
        return "\n".join(lines).rstrip()

    def summary_text(self, summary: PeriodSummary) -> str:
        return self.renderer.render("daily_summary.j2", s=summary, tz=self.settings.app.timezone)

    def heartbeat_text(self, minutes: int) -> str:
        """조용할 때 보내는 짧은 생존 신고 (최근 N분 동안 한 일)."""
        now = utcnow()
        s = self.db.summary(now - timedelta(minutes=minutes), now)
        counts = self.db.queue_counts()
        mode = " · DRY-RUN" if self.state.dry_run else ""
        paused = " · ⏸ 일시정지" if self.state.paused else ""
        span = f"{minutes // 60}시간" if minutes % 60 == 0 else f"{minutes}분"
        mode = " · 🧪 연습 모드" if self.state.dry_run else ""
        paused = " · ⏸ 일시정지 중" if self.state.paused else ""
        dup = max(s.deals_found - s.queued, 0)
        dup_note = f" (기준은 넘었지만 이미 올린 것과 겹친 {dup}건은 건너뜀)" if dup else ""
        fails = f" (확인 실패 {s.run_errors}번)" if s.run_errors else ""
        pending = counts.get("pending", 0)
        awaiting = counts.get("awaiting_link", 0)
        if pending or awaiting:
            waiting = f"{pending}건 발행 차례 기다리는 중" + (f" · {awaiting}건 내 링크 기다리는 중 (/pending)" if awaiting else "")
        else:
            waiting = "없음"
        posted_label = "미리보기로 보낸 글" if self.state.dry_run else "채널에 올린 글"
        lines = [
            f"🐥 <b>봇이 잘 돌고 있습니다</b> · 켜진 지 {humanize_delta(now - self.state.started_at)}{mode}{paused}",
            f"지난 {span} 동안 게시판을 {s.runs}번 확인해서{fails} 글 {s.collected}개를 봤습니다.",
            f"새로 잡은 특가: {s.queued}건{dup_note}",
            f"{posted_label}: {s.published}건",
            f"지금 기다리는 글: {waiting}",
        ]
        nxt = [
            f"{self.collector_label(st.name)} {humanize_delta(st.next_run_at - now) + ' 뒤' if st.next_run_at and st.next_run_at > now else '곧'}"
            for st in self.state.collectors.values()
            if st.enabled and st.available
        ]
        if nxt:
            lines.append("다음 확인: " + " · ".join(nxt))
        if s.errors:
            lines.append(f"🚨 에러 {s.errors}건 — /errors 로 확인")
        if s.queued == 0 and s.published == 0:
            lines.append(f"특가가 없으면 조용한 것이 정상입니다. {span} 동안 아무 소식이 없을 때만 이 메시지를 보냅니다.")
        return "\n".join(lines)


class BotController(Protocol):
    """관리자 명령이 조작하는 인터페이스 (app.DealBot 이 구현)."""

    state: BotState

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def request_run(self, collector: str | None) -> str: ...

    def collector_names(self) -> list[str]: ...

    async def attach_link(self, queue_id: int, url: str) -> str: ...

    def skip_item(self, queue_id: int) -> str: ...

    async def submit_manual(self, text: str) -> str: ...

    async def test_post(self) -> str: ...

    async def push_test(self) -> str: ...

    async def naver_login(self) -> tuple[bytes | None, str]: ...

    async def naver_login_wait(self) -> str: ...

    async def screenshot(self, url: str) -> tuple[bytes | None, str]: ...

    async def fetch_html(self, url: str) -> tuple[bytes | None, str]: ...

    async def threads_auth_url(self) -> str: ...

    async def threads_submit_code(self, code: str) -> str: ...

    async def send_copy_blocks(self, queue_id: int | None = None) -> str: ...

    async def naver_link_test(self, url: str) -> str: ...


HELP_TEXT = (
    "🤖 <b>명령어 안내</b>\n"
    "\n<b>상태 보기</b>\n"
    "/status — 지금 상태 한눈에. 게시판 확인 현황, 올린 글 수, 기다리는 글, 쇼핑몰별 링크 처리 방식.\n"
    "/queue — 올릴 차례를 기다리는 글. 글마다 #번호가 붙어 있고, /skip 에 이 번호를 씁니다.\n"
    "/pending — 내가 링크를 만들어 줘야 하는 글. 토스·네이버처럼 자동 링크가 안 되는 몰의 딜이 여기 쌓입니다.\n"
    "/recent — 최근에 올린 글.\n"
    "/hot 5 — 최근 24시간에 추천 5개 이상 받은 게시판 글과 원문 링크. 숫자를 바꾸면 기준이 바뀝니다 (/hot 2).\n"
    "/find 키워드 — 수집한 글 제목에서 찾기. 어느 게시판에 언제 올라왔는지와 원문 링크.\n"
    "/errors — 최근 에러.\n"
    "/ppstats — 게시판별 추천 수 분포. 판정 기준이 너무 높거나 낮은지 볼 때.\n"
    "\n<b>글 올리기·다루기</b>\n"
    "/link 번호 링크 — 봇이 '내 링크가 필요합니다 #번호' 를 보내면, 그 몰 앱에서 제휴 링크를 만들어 이 명령으로 붙입니다. 그 메시지에 답장으로 링크만 보내도 됩니다. 붙이는 순간 채널에 올라갑니다.\n"
    "/skip 번호 — 그 글은 올리지 않고 건너뜁니다. 품절이거나 별로일 때. 번호는 링크 요청 메시지나 /queue 에 있습니다.\n"
    "/post — 내가 찾은 딜을 직접 올립니다. 아래처럼 보내면 맨 앞 차례로 채널에 올라갑니다 (연습 모드에서는 미리보기만).\n"
    "<code>/post\n[머리글, 없으면 생략]\n상품: 상품명\n가격: 14,890원\nhttps://내가-만든-제휴-링크</code>\n"
    "/copy 번호 — 올린 글의 카카오 오픈채팅용·네이버 블로그용 복붙 문구를 다시 받습니다. 번호 없으면 마지막 글. 실제 모드에서는 올릴 때마다 자동으로 옵니다.\n"
    "/run — 지금 바로 게시판을 확인합니다. /run ppomppu 처럼 하나만도 됩니다.\n"
    "/pause — 잠시 멈춤 (게시판 확인과 올리기 모두). /resume — 다시 시작.\n"
    "\n<b>확인·연결</b>\n"
    "/test — 샘플 딜로 채널에 올라갈 글 양식을 이 챗에 보여줍니다. 양식을 바꿨을 때 확인용이고 채널에는 안 올라갑니다.\n"
    "/pushtest — 휴대폰 푸시(ntfy) 연결 확인.\n"
    "/threadsauth — 스레드 자동 게시 연결. Meta 앱 ID·시크릿을 변수에 넣은 뒤 1회. /threadscode 코드 — 그때 받은 인증 코드 입력.\n"
    "/naverlogin — 네이버 쇼핑커넥트 링크를 봇이 대신 만들도록 서버 브라우저에 QR 로그인. 블로그 글쓰기가 아니라 링크 생성 자동화이고, 브라우저 자동화를 켰을 때만 됩니다.\n"
    "/naverlink 상품URL — 위 자동화로 링크 하나 만들어 보기.\n"
    "/shot URL — 서버 브라우저로 그 페이지 화면을 찍어 보냅니다. 봇이 게시판을 잘못 읽는 것 같을 때 확인용.\n"
    "/html URL — 그 페이지 원문을 파일로 받습니다. 게시판 구조가 바뀌어 봇이 못 읽을 때 저에게 보내주는 용도.\n"
    "/help — 이 안내"
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

    async def drop_stale(update: object, _: ContextTypes.DEFAULT_TYPE) -> None:
        # 봇이 꺼져 있던 동안 쌓인 오래된 명령은 실행하지 않는다 (재배포 직후 옛 /run, /post 가 다시 도는 것 방지)
        msg = getattr(update, "effective_message", None)
        if msg is not None and is_stale_message(getattr(msg, "date", None)):
            log.info("ignoring stale command from %s: %s", getattr(msg, "date", None), (getattr(msg, "text", "") or "")[:40])
            raise ApplicationHandlerStop

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.status_text())

    async def cmd_queue(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.queue_text())

    async def cmd_pending(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.pending_text())

    async def cmd_recent(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.recent_text())

    async def cmd_errors(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.errors_text())

    async def cmd_pause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.pause()
        await reply(update, "⏸ 잠시 멈췄습니다. 게시판 확인과 채널에 올리기를 모두 멈춥니다. /resume 으로 다시 시작")

    async def cmd_resume(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.resume()
        await reply(update, "▶️ 다시 시작했습니다")

    async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        name = ctx.args[0] if ctx.args else None
        await reply(update, controller.request_run(name))

    async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if len(args) < 2 or not args[0].lstrip("#").isdigit():
            await reply(update, "이렇게 보내주세요: <code>/link 12 https://...</code> (번호는 링크 요청 메시지의 #번호)")
            return
        await reply(update, await controller.attach_link(int(args[0].lstrip("#")), args[1]))

    async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].lstrip("#").isdigit():
            await reply(update, "이렇게 보내주세요: <code>/skip 12</code> (번호는 /queue 나 링크 요청 메시지의 #번호)")
            return
        await reply(update, controller.skip_item(int(args[0].lstrip("#"))))

    async def cmd_post(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.effective_message.text if update.effective_message else ""
        await reply(update, await controller.submit_manual(text or ""))

    async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, HELP_TEXT)

    async def cmd_test(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.test_post())

    async def cmd_pushtest(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.push_test())

    async def cmd_threadsauth(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.threads_auth_url())

    async def cmd_threadscode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await reply(update, "이렇게 보내주세요: <code>/threadscode 코드값</code>")
            return
        await reply(update, await controller.threads_submit_code(args[0]))

    async def cmd_copy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        qid = int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdigit() else None
        await reply(update, await controller.send_copy_blocks(qid))

    async def cmd_ppstats(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.community_stats_text())

    async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        n = int(args[0]) if args and args[0].isdigit() else 5
        await reply(update, reporter.hot_text(min_recommend=n))

    async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        kw = " ".join(ctx.args or []).strip()
        if not kw:
            await reply(update, "이렇게 보내주세요: <code>/find 펩시제로</code>")
            return
        await reply(update, reporter.find_text(kw))

    async def _send_photo(update: Update, data: bytes | None, caption: str) -> None:
        msg = update.effective_message
        if msg is None:
            return
        if data:
            await msg.reply_photo(photo=data, caption=caption[:1000])
        else:
            await msg.reply_text(caption[:4096])

    async def cmd_naverlogin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        data, text = await controller.naver_login()
        await _send_photo(update, data, text)
        if data:
            async def _wait() -> None:
                result = await controller.naver_login_wait()
                if update.effective_message:
                    await update.effective_message.reply_text(result)

            ctx.application.create_task(_wait())

    async def cmd_shot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/shot https://...</code>")
            return
        data, text = await controller.screenshot(args[0])
        await _send_photo(update, data, text)

    async def cmd_html(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/html https://www.algumon.com/n/deal</code>")
            return
        data, text = await controller.fetch_html(args[0])
        msg = update.effective_message
        if msg is None:
            return
        if data:
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", args[0].split("//", 1)[-1])[:60] + ".html"
            await msg.reply_document(document=data, filename=name, caption=f"{text}\n이 파일을 Claude 에게 올려주면 셀렉터를 맞춥니다.")
        else:
            await msg.reply_text(text)

    async def cmd_naverlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/naverlink https://smartstore.naver.com/...</code>")
            return
        await reply(update, await controller.naver_link_test(args[0]))

    async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """관리자의 일반 메시지: (1) 링크 요청에 답장 → 링크 붙이기, (2) URL 포함 메시지 → 직접 발행."""
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        urls = find_urls(msg.text)
        replied = msg.reply_to_message
        if replied is not None and replied.text and urls:
            m = _QUEUE_REF_RE.search(replied.text)
            if m:
                await reply(update, await controller.attach_link(int(m.group(1)), urls[0]))
                return
        if urls:
            await reply(update, await controller.submit_manual(msg.text))
            return
        await reply(update, "무엇을 할지 모르겠습니다. 링크 요청 메시지에 답장으로 링크를 보내거나, /help 로 명령어를 확인하세요.")

    async def cmd_unknown_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and update.effective_message:
            await update.effective_message.reply_text(
                f"이 봇은 개인 관리용입니다. (chat id: {update.effective_chat.id})"
            )

    for cmd, fn in (
        ("status", cmd_status),
        ("queue", cmd_queue),
        ("pending", cmd_pending),
        ("recent", cmd_recent),
        ("errors", cmd_errors),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("run", cmd_run),
        ("link", cmd_link),
        ("skip", cmd_skip),
        ("post", cmd_post),
        ("test", cmd_test),
        ("pushtest", cmd_pushtest),
        ("threadsauth", cmd_threadsauth),
        ("threadscode", cmd_threadscode),
        ("copy", cmd_copy),
        ("ppstats", cmd_ppstats),
        ("hot", cmd_hot),
        ("find", cmd_find),
        ("naverlogin", cmd_naverlogin),
        ("naverlink", cmd_naverlink),
        ("shot", cmd_shot),
        ("html", cmd_html),
        ("help", cmd_help),
        ("start", cmd_help),
    ):
        app.add_handler(CommandHandler(cmd, fn, filters=only_admin))
    app.add_handler(TypeHandler(Update, drop_stale), group=-1)
    app.add_handler(MessageHandler(only_admin & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CommandHandler(["start", "status", "help"], cmd_unknown_chat, filters=~only_admin))
