"""장기 실행 루프들: 수집 스케줄러 / 발행 워커 / 일일 요약 / 유지보수."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta
from zoneinfo import ZoneInfo

from dealbot.app import DealBot
from dealbot.utils.timeutil import local_now, next_daily_time, utcnow

log = logging.getLogger(__name__)


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
    while not stop.is_set():
        try:
            await bot.process_queue_once()
        except Exception as e:  # noqa: BLE001
            log.exception("publisher loop error")
            bot.db.log_event("ERROR", "publisher", f"{type(e).__name__}: {e}")
            bot.state.set_error(f"[publisher] {e}")
            await bot.notifier.notify_error("publisher", f"{type(e).__name__}: {e}")
        await _sleep_or_stop(stop, tick)


async def daily_summary_loop(bot: DealBot, stop: asyncio.Event) -> None:
    tz = bot.settings.app.timezone
    hhmm = bot.settings.monitoring.daily_summary_time
    while not stop.is_set():
        now_local = local_now(tz)
        target = next_daily_time(now_local, hhmm)
        wait = (target - now_local).total_seconds()
        log.info("next daily summary at %s (%s)", target.strftime("%Y-%m-%d %H:%M"), tz)
        await _sleep_or_stop(stop, wait)
        if stop.is_set():
            break
        # 같은 날 두 번 보내지 않도록 가드
        marker = target.strftime("%Y-%m-%d")
        if bot.db.kv_get("daily_summary_marker") == marker:
            continue
        try:
            await bot.daily_summary()
            bot.db.kv_set("daily_summary_marker", marker)
        except Exception as e:  # noqa: BLE001
            log.exception("daily summary failed")
            bot.db.log_event("ERROR", "summary", f"{type(e).__name__}: {e}")
        await _sleep_or_stop(stop, 60)


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

    await bot.start_telegram(polling=True)
    log.info("DealBot started: %r (tz=%s)", bot, ZoneInfo(bot.settings.app.timezone))
    bot.db.log_event("INFO", "lifecycle", "started")
    try:
        await bot.notifier.notify_startup(bot.reporter.status_text())
    except Exception as e:  # noqa: BLE001
        log.warning("startup notice failed: %s", e)

    tasks = [
        asyncio.create_task(collector_loop(bot, stop), name="collector_loop"),
        asyncio.create_task(publisher_loop(bot, stop), name="publisher_loop"),
        asyncio.create_task(daily_summary_loop(bot, stop), name="daily_summary_loop"),
        asyncio.create_task(maintenance_loop(bot, stop), name="maintenance_loop"),
    ]
    try:
        await stop.wait()
    finally:
        log.info("shutting down...")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bot.db.log_event("INFO", "lifecycle", "stopped")
        await bot.close()
