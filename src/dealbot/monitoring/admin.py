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


def heartbeat_due(last_activity: datetime, now: datetime, minutes: int) -> bool:
    """관리자 챗이 minutes 동안 조용했으면 True (특가/링크/에러 알림이 있었으면 그것으로 생존 신고를 대신한다)."""
    return minutes > 0 and now - last_activity >= timedelta(minutes=minutes)


# 텔레그램 "/" 메뉴에 등록할 명령 (이름은 영문 소문자·숫자·밑줄만 가능 — 텔레그램 규칙)
BOT_COMMANDS: list[tuple[str, str]] = [
    ("status", "지금 상태 (수집기·발행·대기열)"),
    ("queue", "발행 대기열"),
    ("pending", "내 링크가 필요한 항목"),
    ("recent", "최근 발행 목록"),
    ("hot", "최근 24시간 추천 많은 글 (/hot 5)"),
    ("find", "수집된 글 검색 (/find 키워드)"),
    ("errors", "최근 에러"),
    ("run", "지금 바로 수집 (/run 수집기이름)"),
    ("pause", "발행 일시정지"),
    ("resume", "발행 재개"),
    ("post", "직접 딜 올리기"),
    ("link", "만든 제휴 링크 붙이기 (/link 번호 링크)"),
    ("skip", "항목 건너뛰기 (/skip 번호)"),
    ("copy", "카카오·블로그 복붙 문구 (/copy 번호)"),
    ("test", "샘플 발행 양식 보기"),
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
    ) -> None:
        self.bot = bot
        self.chat_id = normalize_chat_id(admin_chat_id)
        self.cfg = cfg
        self.renderer = renderer
        self.tz = tz
        self.push = push
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
        if not self.enabled:
            log.warning(
                "관리자 알림을 보낼 수 없어 건너뜁니다 (봇 토큰/관리자 챗 ID 확인 필요): %s",
                text.replace("\n", " | ")[:150],
            )
            return False
        assert self.bot is not None
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text[:4096],
                parse_mode=ParseMode.HTML,
                # silent 는 "일상 알림" 표시일 뿐, 실제 무음 여부는 monitoring.quiet_notices 가 결정
                disable_notification=silent and self.cfg.quiet_notices,
                link_preview_options=None,
            )
            self.last_sent_at = utcnow()
            return True
        except TelegramError as e:
            log.error("admin notify failed: %s", e)
            return False

    async def notify_startup(self, status_text: str, check_lines: list[str] | None = None) -> bool:
        text = f"🟢 <b>DealBot v{__version__} 시작</b>\n"
        if check_lines:
            text += "\n<b>자기 점검</b>\n" + "\n".join(html.escape(line) for line in check_lines) + "\n"
        text += f"\n{status_text}"
        sent = await self.send(text, silent=True)
        await self._push("startup", "DealBot 시작", "\n".join(check_lines or [])[:500] or "봇이 시작되었습니다.")
        return sent

    async def notify_published(self, deal: Deal, result: PublishResult, preview: str | None = None) -> None:
        """발행 알림. DRY-RUN 이면 채널에 올라갔을 글 전체(preview)를 그대로 보여준다."""
        if not self.cfg.notify_on_publish:
            return
        p = deal.product
        reasons = ", ".join(deal.verdict.reasons) or "-"
        meta = f"[{html.escape(p.source)}/{html.escape(p.shop)}] · 점수 {deal.verdict.score:g} · {html.escape(reasons)}"
        if result.dry_run and preview:
            photo = " · 🖼 사진 포함" if p.image_url else ""
            text = (
                f"🧪 <b>DRY-RUN 미리보기</b> — 실제 모드였다면 채널에 이렇게 올라갔을 글\n"
                f"{meta}{photo}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{preview}"
            )
            await self.send(text, silent=True)
            return
        tag = "DRY-RUN " if result.dry_run else ""
        price = f"{p.price:,}원" if p.has_price else "가격 없음"
        text = (
            f"✅ <b>{tag}발행</b> [{html.escape(p.shop)}/{html.escape(p.source)}]\n"
            f"{html.escape(truncate(p.name, 80))}\n"
            f"{price} · 점수 {deal.verdict.score:g} · {html.escape(reasons)}"
        )
        if deal.affiliate_url:
            text += f"\n{html.escape(deal.affiliate_url)}"
        await self.send(text, silent=True)

    async def notify_publish_failed(self, deal: Deal, error: str, *, final: bool) -> None:
        if not self.cfg.notify_on_failure:
            return
        p = deal.product
        head = "❌ <b>발행 실패 (포기)</b>" if final else "⚠️ <b>발행 실패 (재시도 예정)</b>"
        await self.send(f"{head} [{html.escape(p.shop)}]\n{html.escape(truncate(p.name, 80))}\n<code>{html.escape(error[:500])}</code>")
        if final:
            await self._push("publish_failed", f"발행 실패 [{p.shop}]", f"{truncate(p.name, 60)}\n{error[:200]}")

    async def notify_manual_link(self, item: QueueItem, shop: Shop) -> None:
        """자동 변환이 안 되는 쇼핑몰: 관리자에게 링크 생성을 요청."""
        if not self.cfg.notify_on_manual_link:
            return
        p = item.deal.product
        price = f" — {p.price:,}원" if p.has_price else ""
        hint = shop.manual_hint or "앱/사이트에서 내 제휴 링크를 만들어 보내주세요"
        text = (
            f"🔗 <b>링크 필요 #{item.id}</b> [{html.escape(shop.name)}]\n"
            f"{html.escape(truncate(p.name, 80))}{price}\n"
            f"원본: {html.escape(p.url)}\n"
            + (f"글: {html.escape(str(p.extra.get('post_url')))}\n" if p.extra.get("post_url") else "")
            + f"\n👉 {html.escape(hint)}\n"
            f"만든 링크를 <b>이 메시지에 답장</b>하거나  <code>/link {item.id} https://...</code>\n"
            f"건너뛰기: <code>/skip {item.id}</code>"
        )
        await self.send(text)
        await self._push(
            "manual_link",
            f"링크 필요 #{item.id} [{shop.name}]",
            f"{truncate(p.name, 70)}{price}\n{p.url}\n\n{hint}",
            priority="high",
            tags=["link"],
        )

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
        await self._push("error", f"에러 {kind}", message[:300], tags=["warning"])

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

    def _item_line(self, it: QueueItem) -> str:
        p = it.deal.product
        price = f" — {p.price:,}원" if p.has_price else ""
        return f"• #{it.id} [{html.escape(p.shop)}] {html.escape(truncate(p.name, 50))}{price} (점수 {it.score:g}, 시도 {it.attempts})"

    def queue_text(self, limit: int = 10) -> str:
        items = self.db.pending_items(limit)
        counts = self.db.queue_counts()
        lines = [
            f"🗂 <b>대기열</b> pending {counts.get('pending', 0)} · 링크대기 {counts.get('awaiting_link', 0)} · failed {counts.get('failed', 0)} · expired {counts.get('expired', 0)}"
        ]
        lines += [self._item_line(it) for it in items]
        if not items:
            lines.append("(대기 중인 특가 없음)")
        return "\n".join(lines)

    def pending_text(self, limit: int = 15) -> str:
        items = self.db.awaiting_items(limit)
        lines = ["🔗 <b>내 링크가 필요한 항목</b>"]
        for it in items:
            p = it.deal.product
            lines.append(self._item_line(it))
            lines.append(f"   원본: {html.escape(p.url)}")
        if not items:
            lines.append("(없음)")
        else:
            lines.append("\n답장 또는 <code>/link 번호 링크</code> · 건너뛰기 <code>/skip 번호</code>")
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
            lines.append(f"• {when} <code>{html.escape(e['kind'])}</code> {html.escape(truncate(e['message'].splitlines()[0], 160))}")
        if not events:
            lines.append("(없음)")
        return "\n".join(lines)

    def community_stats_text(self) -> str:
        lines = ["📈 <b>커뮤니티 글 통계</b> (추천 수 분포 → (c) 임계값 조정용)"]
        for label, hours in (("최근 24시간", 24), ("최근 7일", 24 * 7)):
            stats = self.db.community_stats(utcnow() - timedelta(hours=hours))
            lines.append(f"\n<b>{label}</b>")
            if not stats:
                lines.append("(데이터 없음)")
            for src, st in stats.items():
                ge = st["rec_ge"]
                lines.append(
                    f"• {src}: 글 {st['posts']}개 — 추천≥1 {ge[1]} · ≥3 {ge[3]} · ≥5 {ge[5]} · ≥10 {ge[10]} · ≥20 {ge[20]}"
                    f" · 조회≥500 {st.get('views_ge_500', 0)} · 댓글≥3 {st.get('comments_ge_3', 0)}"
                )
        ic = self.settings.deal.interest
        lines.append(
            f"\n관심도 게이트: 추천≥{ic.min_recommend} 또는 댓글≥{ic.min_comments} 또는 조회≥{ic.min_views} 또는 순위≤{ic.max_rank}"
            f"\n(c) community_min_recommend = {self.settings.deal.community_min_recommend}"
        )
        return "\n".join(lines)

    def hot_text(self, min_recommend: int = 5, hours: int = 24) -> str:
        tz = self.settings.app.timezone
        items = self.db.hot_items(utcnow() - timedelta(hours=hours), min_recommend=min_recommend)
        lines = [f"🔥 <b>최근 {hours}시간 추천 {min_recommend}개 이상 글</b> ({len(items)}건)"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz)
            lines.append(f"• [{it['source']}] {when} 추천 {it['recommend']} · 조회 {it['views'] or '-'} · 댓글 {it['comments'] or '-'} — {html.escape(truncate(it['title'] or '', 60))}")
        if not items:
            lines.append("(없음 — 수집이 아직 안 돌았거나 기준 미달)")
        return "\n".join(lines)

    def find_text(self, keyword: str) -> str:
        tz = self.settings.app.timezone
        items = self.db.find_items(keyword)
        lines = [f"🔎 <b>'{html.escape(keyword)}' 검색</b> ({len(items)}건)"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz, "%m/%d %H:%M")
            lines.append(f"• [{it['source']}] 처음 본 시각 {when} · 추천 {it['recommend'] if it['recommend'] is not None else '-'} — {html.escape(truncate(it['title'] or '', 60))}")
        if not items:
            lines.append("(수집된 글 중 없음)")
        return "\n".join(lines)

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
        lines = [
            f"🫀 <b>정상 가동 중</b> · 가동 {humanize_delta(now - self.state.started_at)}{mode}{paused}",
            f"최근 {span}: 수집 {s.runs}회{f' (실패 {s.run_errors})' if s.run_errors else ''} · 글 {s.collected}건 · 특가 {s.deals_found}건 · 발행 {s.published}건",
            f"대기열 {counts.get('pending', 0)} 대기 · {counts.get('awaiting_link', 0)} 링크대기",
        ]
        nxt = [
            f"{st.name} {humanize_delta(st.next_run_at - now) if st.next_run_at and st.next_run_at > now else '곧'}"
            for st in self.state.collectors.values()
            if st.enabled and st.available
        ]
        if nxt:
            lines.append("다음 수집: " + " · ".join(nxt))
        if s.errors:
            lines.append(f"🚨 에러 {s.errors}건 — /errors")
        if s.deals_found == 0 and s.published == 0:
            lines.append("특가가 없었던 건 정상입니다. 기준을 넘는 글이 없으면 조용합니다.")
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
    "🤖 <b>DealBot 명령어</b>\n"
    "/status — 현재 상태\n"
    "/queue — 발행 대기열\n"
    "/pending — 내가 링크를 만들어 줘야 하는 항목\n"
    "/link 번호 링크 — 만든 제휴 링크 붙이기 (또는 요청 메시지에 답장)\n"
    "/skip 번호 — 항목 건너뛰기\n"
    "/post — 직접 딜 올리기. 예)\n"
    "<code>/post\n[토스쇼핑 첫 구매 시 3,000원 추가 할인]\n상품: 애슐리 크리스피 핫도그 4종\n가격: 14,890원\nhttps://toss.im/_m/xxxx</code>\n"
    "/test — 샘플 딜을 이 챗에 보내 양식 확인\n"
    "/pushtest — 휴대폰 푸시(ntfy/Pushover) 연결 확인\n"
    "/threadsauth — 스레드 연결 (최초 1회)\n"
    "/threadscode 코드 — 스레드 인증 코드 입력\n"
    "/copy [번호] — 카카오·블로그 복붙 문구 다시 받기 (번호 없으면 최근 발행)\n"
    "/ppstats — 커뮤니티 글 추천·조회·댓글 분포 (필터 기준 조정용)\n"
    "/hot [추천수] — 최근 24시간 추천 N개 이상 글 목록 (기본 5)\n"
    "/find 키워드 — 수집된 글 제목 검색 (어느 소스에 언제 올라왔나)\n"
    "/naverlogin — 네이버 QR 로그인 (브라우저 자동화 켰을 때)\n"
    "/naverlink 상품URL — 쇼핑커넥트 링크 자동 생성 테스트\n"
    "/shot URL — 서버 브라우저 스크린샷 (셀렉터 조정용)\n"
    "/html URL — 페이지 원문(HTML)을 파일로 받기 (알구몬/뽐뿌 구조 확인용)\n"
    "/recent — 최근 발행 목록\n"
    "/errors — 최근 에러\n"
    "/run [수집기이름] — 지금 바로 수집 실행\n"
    "/pause · /resume — 일시정지/재개\n"
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
        await reply(update, "⏸ 일시정지됨. /resume 으로 재개")

    async def cmd_resume(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.resume()
        await reply(update, "▶️ 재개됨")

    async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        name = ctx.args[0] if ctx.args else None
        await reply(update, controller.request_run(name))

    async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if len(args) < 2 or not args[0].lstrip("#").isdigit():
            await reply(update, "사용법: <code>/link 번호 https://...</code>")
            return
        await reply(update, await controller.attach_link(int(args[0].lstrip("#")), args[1]))

    async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].lstrip("#").isdigit():
            await reply(update, "사용법: <code>/skip 번호</code>")
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
            await reply(update, "사용법: <code>/threadscode 코드값</code>")
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
            await reply(update, "사용법: <code>/find 펩시제로</code>")
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
        await reply(update, "링크가 포함된 메시지나 명령어를 보내주세요. /help")

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
