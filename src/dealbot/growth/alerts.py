"""SNS·도메인 관련 주인 할 일 (주인 결정 10/15).

- 인스타·스레드 24시간 0개: 텔레그램엔 딜을 올렸는데 인스타나 스레드가 하루 동안 하나도 못 올렸으면 이유 요약과 함께 한 통 (하루 한 번까지).
- 통계 권한: 인사이트·답글 읽기 권한이 없는 토큰이면 '인스타·스레드 통계 권한 다시 연결 (/instaauth, /threadsauth)' 를
  권한이 생길 때까지 하루 한 번 할 일로. 권한을 모르면(아직 안 불러 봄) 하루 한 번 가볍게 불러 본다 (공식 API 만).
- 내 도메인: PUBLIC_DOMAIN 이 내 도메인인데 DNS A 레코드가 이 서버 IP 를 가리키지 않으면 할 일 한 통.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import socket
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from dealbot.monitoring.admin import kakao_fit
from dealbot.publisher.scopes import IG_INSIGHTS, THREADS_INSIGHTS, THREADS_READ_REPLIES
from dealbot.utils.timeutil import from_iso, to_iso

log = logging.getLogger(__name__)

ZERO_KEY = "sns_zero"
SCOPE_KEY = "sns_scope"
SCOPE_TODO = "인스타·스레드 통계 권한 다시 연결 (/instaauth, /threadsauth)"
DOMAIN_KEY = "domain_dns"
SNS_LOG_SINCE = "sns_log_since"  # 기록을 언제부터 모았나 (그 전 24시간을 0개로 오해하지 않게)
CHANNEL_NAMES = {"instagram": "인스타", "threads": "스레드"}
# 기록에 남는 이유 → 짧은 말
_REASON_WORDS = (
    (re.compile(r"확인된 상품 사진 없음"), "확인된 사진 없음"),
    (re.compile(r"sold out", re.I), "품절"),
    (re.compile(r"waited too long", re.I), "너무 오래 기다림"),
    (re.compile(r"not authorized|token|oauth|permission|권한|인증|만료", re.I), "연결·권한 문제"),
    (re.compile(r"규칙 위반"), "글 규칙 검사"),
    (re.compile(r"price up", re.I), "값 오름"),  # 인스타 대기열: 올리기 직전 다시 본 값이 오름
    (re.compile(r"stopped while posting", re.I), "올리다 끊김 (재시작)"),
    (re.compile(r"카드 이미지를 못 만듦"), "카드 못 만듦 (공개 도메인)"),
)


def short_reason(reason: str | None) -> str:
    r = reason or ""
    for pat, word in _REASON_WORDS:
        if pat.search(r):
            return word
    return (r[:30] + "…") if len(r) > 30 else (r or "기타")


def _wants(bot: Any, channel: str) -> tuple[bool, str | None]:
    """(켜져 있나, 연결 안 됨 이유)."""
    if channel == "threads":
        on = bot.settings.threads.enabled
        ready = bot.threads.configured or bot.threads.dry_run
        return on, None if ready else "연결 안 됨 (/threadsauth)"
    on = bot.settings.instagram.enabled
    ready = bot.instagram.configured or bot.instagram.dry_run
    return on, None if ready else "연결 안 됨 (/instaauth)"


async def sns_zero_check(bot: Any, now: datetime | None = None) -> bool:
    """텔레그램엔 딜을 올렸는데 인스타·스레드가 24시간 0개 → 주인 할 일 한 통 (이유 요약). 보냈으면 True."""
    if not bot.settings.monitoring.sns_zero_alert:
        return False
    now = now or bot.clock()
    since_raw = bot.db.kv_get(SNS_LOG_SINCE)
    if not since_raw:
        bot.db.kv_set(SNS_LOG_SINCE, to_iso(now))
        return False
    started = from_iso(since_raw)
    if started is None or now - started < timedelta(hours=24):
        return False  # 기록을 모은 지 하루가 안 됨
    since = now - timedelta(hours=24)
    tg = len(bot.db.published_deal_times(since, include_dry_run=False))
    if tg == 0:
        return False
    parts: list[str] = []
    for ch in ("instagram", "threads"):
        on, not_ready = _wants(bot, ch)
        if not on:
            continue
        rows = bot.db.sns_log_since(ch, since)
        if any(r.get("outcome") == "posted" for r in rows):
            continue
        reasons: Counter[str] = Counter(short_reason(r.get("reason") or r.get("outcome")) for r in rows)
        if ch == "threads":
            waiting = len(bot.db.threads_waiting(100))
            if waiting:
                reasons["대기 중 (07~24시·45분 간격)"] += waiting
        else:
            # 인스타는 자기 대기열·리듬 (하루 단일 글 daily_singles 개·hours·min_gap_minutes 간격 — publisher/ig_scheduler.py)
            waiting = len(bot.db.ig_queued(100))
            if waiting:
                ig = bot.settings.instagram
                reasons[f"대기 중 (하루 {ig.daily_singles}개·{ig.hours}·{ig.min_gap_minutes}분 간격)"] += waiting
        if not_ready:
            reasons[not_ready] += 1
        why = ", ".join(f"{k} {v}" for k, v in reasons.most_common(3)) or "이유 기록 없음"
        parts.append(f"{CHANNEL_NAMES[ch]} 0개 ({why})")
    if not parts:
        bot.notifier.resolve(ZERO_KEY)
        return False
    last = from_iso(bot.db.kv_get("sns_zero_alerted_at"))
    if last is not None and now - last < timedelta(hours=24):
        return False
    bot.db.kv_set("sns_zero_alerted_at", to_iso(now))
    detail = " · ".join(parts)
    await bot.notifier.alert(
        "sns_zero",
        f"⚠️ <b>텔레그램엔 하루 동안 딜 {tg}개를 올렸는데</b>\n{html.escape(detail)}\n/status 로 연결을 확인해 주세요",
        kakao_fit(f"⚠️[할 일] 텔레그램 딜 {tg}개인데 {detail}", "→ 텔레그램 핫딜봇 /status (인스타·스레드 연결 확인)"),
        key=ZERO_KEY, short="인스타·스레드 0개", ttl_h=24,
    )
    return True


async def _probe(bot: Any) -> None:
    """권한을 모르는 것만 하루 한 번 가볍게 불러 본다 (공식 API, 결과는 scopes 가 기억)."""
    from dealbot.dashboard.sources._meta import is_permission, is_token_problem

    async def check(scopes: Any, scope: str, call: Callable[[], Awaitable[Any]]) -> None:
        if scopes.status(scope) != "unknown":
            return
        try:
            await call()
        except Exception as e:  # noqa: BLE001
            if is_permission(e) and not is_token_problem(e):
                scopes.mark(scope, False)
            return
        scopes.mark(scope, True)

    th = bot.threads
    if bot.settings.threads.enabled and th.stored_token() is not None and not th.dry_run:
        token = await th.ensure_fresh()
        if token is not None:
            await check(th.scopes, THREADS_INSIGHTS, lambda: th.client.user_insights(token, "followers_count"))
            post = next(iter(bot.db.threads_posted_since(bot.clock() - timedelta(days=30))), None)
            if post is not None:
                await check(th.scopes, THREADS_READ_REPLIES, lambda: th.client.replies(token, str(post.post_id)))
    ig = bot.instagram
    if bot.settings.instagram.enabled and ig.stored_token() is not None and not ig.dry_run:
        token = await ig.ensure_fresh()
        if token is not None:
            now = bot.clock()
            await check(ig.scopes, IG_INSIGHTS, lambda: ig.client.account_insights(
                token, "reach", since=int((now - timedelta(days=2)).timestamp()), until=int(now.timestamp())))


async def scope_todo(bot: Any, now: datetime | None = None, *, probe: bool = True) -> list[str]:
    """권한이 없다고 안 것이 있으면 하루 한 번 주인 할 일 (생기면 해제). 없는 권한 목록을 돌려준다."""
    now = now or bot.clock()
    if probe:
        try:
            await _probe(bot)
        except Exception as e:  # noqa: BLE001
            log.info("scope probe failed: %s", e)
    missing: list[str] = []
    if bot.settings.threads.enabled and bot.threads.stored_token() is not None:
        missing += [f"스레드 {s}" for s in bot.threads.scopes.missing([THREADS_INSIGHTS, THREADS_READ_REPLIES])]
    if bot.settings.instagram.enabled and bot.instagram.stored_token() is not None:
        missing += [f"인스타 {s}" for s in bot.instagram.scopes.missing([IG_INSIGHTS])]
    if not missing:
        if bot.db.kv_get("sns_scope_alerted_at"):
            bot.db.kv_set("sns_scope_alerted_at", "")
            bot.notifier.resolve(SCOPE_KEY)
        return []
    last = from_iso(bot.db.kv_get("sns_scope_alerted_at") or None)
    if last is None or now - last >= timedelta(hours=24):
        bot.db.kv_set("sns_scope_alerted_at", to_iso(now))
        await bot.notifier.alert(
            "sns_scope",
            "🔑 <b>인스타·스레드 통계 권한이 없는 토큰이에요</b> (발행은 그대로 돼요)\n"
            f"없는 권한: <code>{html.escape(', '.join(missing))}</code>\n"
            "메타 앱에 권한을 더한 뒤 /instaauth · /threadsauth 로 다시 연결해 주세요 (docs/SETUP_KEYS.md '통계·답글 권한')",
            kakao_fit(f"🔔[할 일] {SCOPE_TODO}", "→ 메타 앱에 권한 추가 후 텔레그램 핫딜봇에서 다시 연결"),
            key=SCOPE_KEY, short="인스타·스레드 통계 권한", ttl_h=24,
        )
    return missing


# ---------------------------------------------------------------- 내 도메인 DNS
_SSLIP = re.compile(r"^(\d{1,3})-(\d{1,3})-(\d{1,3})-(\d{1,3})\.sslip\.io$")
_PLATFORM_HOSTS = (".sslip.io", ".nip.io", ".up.railway.app", ".railway.app")


def server_ip_from_aliases(aliases: list[str]) -> str | None:
    """sslip.io 별칭(3-35-1-2.sslip.io)에 든 서버 IP. 없으면 None."""
    for a in aliases:
        m = _SSLIP.match(a.strip().lower())
        if m:
            return ".".join(m.groups())
    return None


async def _resolve_a(host: str) -> list[str]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, 443, socket.AF_INET, socket.SOCK_STREAM)
    return sorted({str(i[4][0]) for i in infos})


async def domain_check(bot: Any, resolver: Callable[[str], Awaitable[list[str]]] | None = None, *,
                       alert: bool = True) -> tuple[bool | None, str] | None:
    """내 도메인이 이 서버를 가리키는지 (자기 점검 한 줄). 내 도메인이 아니거나 서버 IP 를 모르면 None.
    alert: 안 가리키면 주인 할 일 한 통 (봇 시작 때). `dealbot check` 같은 점검 명령은 줄만."""
    sec = bot.settings.secrets
    dom = (sec.public_domain or "").strip().strip("/").lower()
    if not dom or dom.endswith(_PLATFORM_HOSTS):
        return None
    ip = server_ip_from_aliases(sec.public_domain_aliases)
    if ip is None:
        return None
    try:
        ips = await (resolver or _resolve_a)(dom)
    except Exception as e:  # noqa: BLE001 — DNS 오류도 '안 가리킴'으로
        log.info("domain check: %s not resolved (%s)", dom, type(e).__name__)
        ips = []
    if ip in ips:
        if alert:
            bot.notifier.resolve(DOMAIN_KEY)
        return True, f"내 도메인 {dom} → 이 서버 ({ip})"
    now = ", ".join(ips) or "없음"
    if not alert:
        return None, f"내 도메인 {dom} 이 이 서버({ip})를 가리키지 않음 (A 레코드: {now})"
    await bot.notifier.alert(
        "domain_dns",
        f"🌐 <b>내 도메인 {html.escape(dom)} 이 이 서버({ip})를 가리키지 않아요</b> (지금 A 레코드: {html.escape(now)})\n"
        "도메인 관리 화면에서 A 레코드를 이 서버 IP 로 고쳐 주세요 (docs/SETUP_KEYS.md '내 도메인 연결'). "
        "그동안 카드·핫딜 페이지 주소가 열리지 않을 수 있어요.",
        kakao_fit(f"⚠️[할 일] 내 도메인 {dom} 이 서버 IP({ip})를 안 가리켜요", "→ 도메인 관리에서 A 레코드 확인 (SETUP_KEYS 내 도메인 연결)"),
        key=DOMAIN_KEY, short="내 도메인 DNS 확인", ttl_h=24,
    )
    return None, f"내 도메인 {dom} 이 이 서버({ip})를 가리키지 않음 (A 레코드: {now})"
