"""장기 실행 루프들: 수집 스케줄러 / 발행 워커 / 일일 요약 / 유지보수."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.app import DealBot
from dealbot.monitoring.admin import CODE_SHA, heartbeat_due
from dealbot.utils.timeutil import local_now, next_daily_time, utcnow

log = logging.getLogger(__name__)

# 재시작(SIGTERM) 때 하던 발행(텔레그램 → 스레드 글 → 링크 답글)을 마칠 시간. systemd 기본 TimeoutStopSec(90초) 안
SHUTDOWN_GRACE_SECONDS = 60.0

# ---- 자동 업데이트(deploy/lightsail/auto-update.sh)와 주고받는 표식. git 으로 받은 서버에서만 쓴다 (도커 이미지엔 없음)
_REPO_ROOT = Path(__file__).resolve().parents[2]
VAR_DIR: Path | None = _REPO_ROOT / "var" if (_REPO_ROOT / ".git").exists() else None
UPDATE_MARK_MAX_AGE = 600  # 초. 이보다 오래된 var/updating 은 업데이트가 죽고 남은 찌꺼기로 보고 무시


def write_running_version() -> None:
    """지금 돌고 있는 커밋을 var/running_version 에 적는다 — auto-update.sh 가 받은 코드(HEAD)와 비교해서
    아직 재시작이 안 됐으면 다시 시도한다. 이전 실행이 강제 종료로 남긴 발행 중 표식도 지운다."""
    if VAR_DIR is None:
        return
    try:
        (VAR_DIR / "busy").mkdir(parents=True, exist_ok=True)
        (VAR_DIR / "running_version").write_text(CODE_SHA + "\n", encoding="utf-8")
        for old in (VAR_DIR / "busy").glob("publish_*"):
            old.unlink(missing_ok=True)
    except OSError as e:
        log.warning("var/running_version 을 못 썼습니다 (자동 업데이트가 재시작 여부를 모름): %s", e)


@contextlib.contextmanager
def publishing_mark() -> Iterator[None]:
    """발행하는 동안 var/busy/publish_<pid> 를 둔다 — auto-update.sh 는 이게 있으면 재시작을 1분 미룬다."""
    mark: Path | None = None
    if VAR_DIR is not None:
        try:
            (VAR_DIR / "busy").mkdir(parents=True, exist_ok=True)
            mark = VAR_DIR / "busy" / f"publish_{os.getpid()}"
            mark.write_text("publish", encoding="utf-8")
        except OSError:
            mark = None
    try:
        yield
    finally:
        if mark is not None:
            mark.unlink(missing_ok=True)


def update_in_progress(started_at: float) -> bool:
    """auto-update.sh 가 새 코드를 받는 중(var/updating)이면 True — 그동안 옛 프로세스는 새 글을 시작하지 않는다
    (받은 새 템플릿을 옛 코드로 그리다 깨지지 않게). 이 프로세스가 켜지기 전부터 있던 표식은
    재시작 뒤 새 프로세스가 볼 일이 아니므로 무시한다."""
    if VAR_DIR is None:
        return False
    try:
        mtime = (VAR_DIR / "updating").stat().st_mtime
    except OSError:
        return False
    return mtime >= started_at and time.time() - mtime < UPDATE_MARK_MAX_AGE


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.1, seconds))
    except TimeoutError:
        pass


async def collector_loop(bot: DealBot, stop: asyncio.Event) -> None:
    tick = bot.settings.app.scheduler_tick_seconds
    # 시작 직후 순차 실행되도록 next_run 을 지금으로, 약간씩 어긋나게
    for i, c in enumerate(bot.collectors):
        bot.state.collectors[c.name].next_run_at = utcnow() + timedelta(seconds=5 + i * 10)

    while not stop.is_set():
        bot.heartbeat()
        for c in bot.collectors:
            if stop.is_set():
                break
            st = bot.state.collectors[c.name]
            now = utcnow()
            due = st.next_run_at is not None and now >= st.next_run_at
            if st.run_requested:
                due = True
            if not due or (bot.state.paused and not st.run_requested):
                continue
            st.run_requested = False
            if not st.available:
                # 자격 증명 없이 등록된 수집기: 주기적으로 조용히 건너뜀
                st.next_run_at = now + timedelta(minutes=st.interval_minutes)
                continue
            await bot.run_collector(c)
            st.next_run_at = utcnow() + timedelta(minutes=st.interval_minutes)
        await _sleep_or_stop(stop, tick)


async def publisher_loop(bot: DealBot, stop: asyncio.Event) -> None:
    tick = bot.settings.publish.publisher_tick_seconds
    started = time.time()
    while not stop.is_set():
        try:
            # 표식을 먼저 놓고 업데이트 여부를 본다 (auto-update.sh 는 반대 순서) → 둘이 엇갈려 동시에 진행하지 않음
            with publishing_mark():
                if update_in_progress(started):
                    log.info("자동 업데이트가 새 코드를 받는 중 — 새 글 발행은 재시작 뒤에")
                else:
                    await bot.process_queue_once()
        except Exception as e:  # noqa: BLE001
            log.exception("publisher loop error")
            bot.db.log_event("ERROR", "publisher", f"{type(e).__name__}: {e}")
            bot.state.set_error(f"[publisher] {e}")
            await bot.notifier.notify_error("publisher", f"{type(e).__name__}: {e}")
        await _sleep_or_stop(stop, tick)


async def _daily_job_loop(bot: DealBot, stop: asyncio.Event, *, hhmm: str, marker_key: str, label: str, job: Any) -> None:
    """매일 hhmm(app.timezone)에 job 을 한 번 실행한다. 같은 날 두 번 돌지 않도록 날짜 표식을 남긴다."""
    tz = bot.settings.app.timezone
    while not stop.is_set():
        now_local = local_now(tz)
        target = next_daily_time(now_local, hhmm)
        wait = (target - now_local).total_seconds()
        log.info("next %s at %s (%s)", label, target.strftime("%Y-%m-%d %H:%M"), tz)
        await _sleep_or_stop(stop, wait)
        if stop.is_set():
            break
        marker = target.strftime("%Y-%m-%d")
        if bot.db.kv_get(marker_key) == marker:
            continue
        try:
            await job()
            bot.db.kv_set(marker_key, marker)
        except Exception as e:  # noqa: BLE001
            log.exception("%s failed", label)
            bot.db.log_event("ERROR", label, f"{type(e).__name__}: {e}")
            bot.state.set_error(f"[{label}] {e}")
        await _sleep_or_stop(stop, 60)


async def daily_summary_loop(bot: DealBot, stop: asyncio.Event) -> None:
    if not bot.settings.monitoring.daily_summary:
        return
    await _daily_job_loop(
        bot, stop, hhmm=bot.settings.monitoring.daily_summary_time, marker_key="daily_summary_marker", label="summary", job=bot.daily_summary
    )


async def blog_digest_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """매일 밤 하루치 딜을 블로그 글로 묶어 관리자 챗에 보낸다."""
    cfg = bot.settings.blog_digest
    if not cfg.enabled:
        return
    await _daily_job_loop(bot, stop, hhmm=cfg.time, marker_key="blog_digest_marker", label="blog_digest", job=bot.blog_digest)


async def heartbeat_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """관리자 챗이 monitoring.heartbeat_minutes 동안 조용하면 '정상 가동 중' 상태를 보낸다.
    특가 미리보기/발행/링크 요청 같은 알림이 그 사이에 있었으면 그것이 생존 신고를 대신한다."""
    minutes = bot.settings.monitoring.heartbeat_minutes
    if minutes <= 0 or not bot.notifier.enabled:
        return
    while not stop.is_set():
        await _sleep_or_stop(stop, 60)
        if stop.is_set():
            break
        try:
            last = bot.notifier.last_sent_at or bot.state.started_at
            if heartbeat_due(last, utcnow(), minutes):
                await bot.send_heartbeat()
        except Exception as e:  # noqa: BLE001
            log.warning("heartbeat notice failed: %s", e)


async def soldout_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """내 링크를 기다리는 글의 상품 페이지를 N분마다 다시 읽어 품절이면 내린다."""
    so = bot.settings.deal.sold_out
    if not so.enabled or so.recheck_awaiting_minutes <= 0:
        return
    while not stop.is_set():
        await _sleep_or_stop(stop, so.recheck_awaiting_minutes * 60)
        if stop.is_set():
            break
        try:
            n = await bot.recheck_awaiting()
            if n:
                log.info("sold-out recheck dropped %d awaiting item(s)", n)
        except Exception as e:  # noqa: BLE001
            log.warning("sold-out recheck failed: %s", e)


async def maintenance_loop(bot: DealBot, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await _sleep_or_stop(stop, 6 * 3600)
        if stop.is_set():
            break
        try:
            bot.maintenance()
        except Exception as e:  # noqa: BLE001
            log.exception("maintenance failed")
            bot.db.log_event("ERROR", "maintenance", f"{type(e).__name__}: {e}")
            bot.state.set_error(f"[maintenance] {e}")


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - windows
            signal.signal(sig, lambda *_: stop.set())


async def run_forever(bot: DealBot) -> None:
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    write_running_version()

    try:
        await bot.start_telegram(polling=True)
    except Exception as e:  # noqa: BLE001 — 어떤 경우에도 봇 프로세스는 살아 있어야 한다
        log.exception("텔레그램 시작 중 예기치 못한 오류 — 오프라인으로 계속합니다: %s", e)
    await bot.start_web()  # 스레드 OAuth 콜백·/health (실패해도 계속)
    log.info("DealBot started: %r (tz=%s)", bot, ZoneInfo(bot.settings.app.timezone))
    bot.db.log_event("INFO", "lifecycle", "started")
    try:
        checks = await bot.self_check()
        lines = [("✅ " if ok else "⚠️ " if ok is None else "❌ ") + text for ok, text in checks]
        for line in lines:
            log.info("self-check: %s", line)
        if not bot.notifier.enabled:
            log.warning(
                "관리자 알림을 보낼 수 없습니다 — TELEGRAM_BOT_TOKEN=%s, TELEGRAM_ADMIN_CHAT_ID=%s. "
                "두 값을 모두 설정하고 봇에게 /start 를 보낸 뒤 재배포하세요.",
                "설정됨" if bot.settings.secrets.has_telegram else "없음",
                bot.settings.secrets.telegram_admin_chat_id or "없음",
            )
        else:
            sent = await bot.notifier.notify_startup(bot.reporter.status_text(), lines)
            if sent is False:
                log.warning("시작 알림 전송 실패 — 봇에게 /start 를 먼저 보냈는지, 챗 ID 가 맞는지 확인하세요")
    except Exception as e:  # noqa: BLE001
        log.warning("startup notice failed: %s", e)

    try:
        await bot.sweep_unverified_reviews()  # 설정이 '확인 안 되면 제외' 면 묻고 있던 딜은 내린다
    except Exception as e:  # noqa: BLE001
        log.warning("sweep of pending reviews failed: %s", e)

    tasks = [
        asyncio.create_task(collector_loop(bot, stop), name="collector_loop"),
        asyncio.create_task(publisher_loop(bot, stop), name="publisher_loop"),
        asyncio.create_task(daily_summary_loop(bot, stop), name="daily_summary_loop"),
        asyncio.create_task(blog_digest_loop(bot, stop), name="blog_digest_loop"),
        asyncio.create_task(maintenance_loop(bot, stop), name="maintenance_loop"),
        asyncio.create_task(heartbeat_loop(bot, stop), name="heartbeat_loop"),
        asyncio.create_task(soldout_loop(bot, stop), name="soldout_loop"),
    ]
    try:
        await stop.wait()
    finally:
        stop.set()  # 예외로 빠져나와도 루프들이 멈추도록
        # 루프들은 stop 을 보고 스스로 끝난다. 발행 중이던 글은 링크 답글·카드까지 마치게 기다렸다가 (최대 SHUTDOWN_GRACE_SECONDS)
        log.info("shutting down — 하던 작업을 마치는 중 (최대 %d초)...", SHUTDOWN_GRACE_SECONDS)
        _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
        for t in pending:
            log.warning("종료 대기 시간이 지나 중단합니다: %s", t.get_name())
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bot.db.log_event("INFO", "lifecycle", "stopped")
        await bot.close()
