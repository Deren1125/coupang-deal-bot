"""장기 실행 루프들: 수집 스케줄러 / 발행 워커 / 일일 요약 / 유지보수."""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import logging
import os
import re
import signal
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.app import DealBot
from dealbot.monitoring.admin import CODE_SHA, heartbeat_due, kakao_fit
from dealbot.utils.timeutil import local_now, next_daily_time, utcnow

log = logging.getLogger(__name__)

# 재시작(SIGTERM) 때 하던 발행(텔레그램 → 스레드 글 → 답글)을 마칠 시간. systemd TimeoutStopSec(90초) 안.
# 기다리는 건 발행 루프만 — 수집·정리 같은 다른 루프는 바로 끊는다 (재시작이 긁어 오기 때문에 늦어지지 않게)
SHUTDOWN_GRACE_SECONDS = 60.0
# systemd 밖(도커 compose 기본 10초 뒤 강제 종료)에서는 짧게 기다려 close()(DB 닫기·'stopped' 기록)까지 마친다
SHUTDOWN_GRACE_OUTSIDE_SYSTEMD = 8.0


def shutdown_grace() -> float:
    """발행 루프를 기다릴 시간. DEALBOT_SHUTDOWN_GRACE_SECONDS 로 바꿀 수 있고, systemd(INVOCATION_ID 있음)가 아니면 짧게."""
    raw = os.environ.get("DEALBOT_SHUTDOWN_GRACE_SECONDS", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            log.warning("DEALBOT_SHUTDOWN_GRACE_SECONDS 가 숫자가 아님 — 기본값 사용")
    if os.environ.get("INVOCATION_ID"):
        return SHUTDOWN_GRACE_SECONDS
    return min(SHUTDOWN_GRACE_SECONDS, SHUTDOWN_GRACE_OUTSIDE_SYSTEMD)

# ---- 자동 업데이트(deploy/lightsail/auto-update.sh)와 주고받는 표식. git 으로 받은 서버에서만 쓴다 (도커 이미지엔 없음)
_REPO_ROOT = Path(__file__).resolve().parents[2]
VAR_DIR: Path | None = _REPO_ROOT / "var" if (_REPO_ROOT / ".git").exists() else None
UPDATE_MARK_MAX_AGE = 600  # 초. 이보다 오래된 var/updating 은 업데이트가 죽고 남은 찌꺼기로 보고 무시


# ---- 시작 알림: 자동 업데이트로 하루에도 여러 번 재시작하니, 자기 점검 결과가 그대로면 하루 한 번만 보낸다
STARTUP_NOTICE_KV = "startup_notice"
STARTUP_NOTICE_REPEAT = timedelta(hours=24)


def _startup_signature(lines: list[str]) -> str:
    # 매번 바뀌는 숫자(골드박스 n건 등)는 빼고 비교한다
    return "\n".join(re.sub(r"\d+", "#", line) for line in sorted(lines))


def startup_notice_due(db: Any, lines: list[str], now: datetime | None = None) -> bool:
    """점검 결과가 지난 알림과 다르거나(새 문제·해결된 문제) 하루가 지났을 때만 True."""
    try:
        last = json.loads(db.kv_get(STARTUP_NOTICE_KV) or "{}")
        at = datetime.fromisoformat(last["at"])
    except (ValueError, KeyError, TypeError):
        return True
    if last.get("sig") != _startup_signature(lines):
        return True
    return (now or utcnow()) - at >= STARTUP_NOTICE_REPEAT


def mark_startup_notice(db: Any, lines: list[str], now: datetime | None = None) -> None:
    db.kv_set(STARTUP_NOTICE_KV, json.dumps({"sig": _startup_signature(lines), "at": (now or utcnow()).isoformat()}))


SELFCHECK_BAD_KV = "selfcheck_bad"


async def selfcheck_alerts(bot: DealBot, lines: list[str]) -> None:
    """자기 점검에서 새로 ❌ 가 된 항목만 주인 카톡 (지난 시작 때도 ❌ 였던 것은 다시 알리지 않음)."""
    bad = sorted(line[2:].strip() for line in lines if line.startswith("❌"))
    try:
        before = set(json.loads(bot.db.kv_get(SELFCHECK_BAD_KV) or "[]"))
    except ValueError:
        before = set()
    bot.db.kv_set(SELFCHECK_BAD_KV, json.dumps(bad, ensure_ascii=False))
    for item in bad:
        if item in before:
            continue
        name = item.split(":", 1)[0][:30]
        await bot.notifier.alert(
            "selfcheck", f"❌ <b>자기 점검 실패</b>: {html.escape(item[:300])}\n/status 로 확인해 주세요",
            kakao_fit(f"⛔[멈춤] 딜봇 자기 점검 실패: {item[:80]}", "→ 텔레그램 핫딜봇 /status"),
            key=f"selfcheck:{name}", short=f"딜봇 점검 실패 {name}"[:48],
        )


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
def publishing_mark(name: str = "publish") -> Iterator[None]:
    """발행하는 동안 var/busy/<name>_<pid> 를 둔다 — auto-update.sh 는 이게 있으면 재시작을 1분 미룬다.
    채널 발행 루프와 인스타 루프가 따로 표식을 둔다 (한쪽이 끝나며 다른 쪽 표식을 지우지 않게)."""
    mark: Path | None = None
    if VAR_DIR is not None:
        try:
            (VAR_DIR / "busy").mkdir(parents=True, exist_ok=True)
            mark = VAR_DIR / "busy" / f"{name}_{os.getpid()}"
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
                    # 스레드 대기열: 리듬(07~24시·45분 간격·하루 딜 10개)이 허락하면 한 글 (발행 루프라 재시작 때 마칠 시간을 받음)
                    flow = getattr(bot, "threads_flow", None)
                    if flow is not None:
                        await flow.tick()
                    bot.notifier.error_cleared("publisher")
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


async def owner_summary_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """저녁 요약(카톡)에 넣을 딜봇 하루 정리를 Blog-Auto 에 넘긴다 (Blog-Auto 가 21~22시에 블로그 요약과 함께 문장으로 보냄)."""
    if not bot.notifier.silent_policy:
        return
    await _daily_job_loop(
        bot, stop, hhmm=bot.settings.monitoring.owner_summary_time, marker_key="owner_summary_marker", label="owner_summary",
        job=bot.owner_summary,
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


async def sns_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """채널 성장 일 (주인 결정 10/15): 1분마다 스레드 답글 모으기(12:30·20:30대)·투표·일요일 TOP5·SNS 0개 알림·통계 권한 할 일.
    딜 수집·발행 루프와 따로 돈다 (스레드 글 올리기 자체는 발행 루프에서)."""
    tick = getattr(bot, "sns_tick", None)
    if tick is None:
        return
    await _sleep_or_stop(stop, 45)  # 시작 직후의 자기 점검·첫 수집이 먼저
    while not stop.is_set():
        try:
            ran = await tick()
            if ran:
                log.info("sns jobs: %s", ", ".join(ran))
        except Exception as e:  # noqa: BLE001
            log.warning("sns loop failed: %s", e)
        await _sleep_or_stop(stop, 60)


async def dashboard_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """채널 대시보드 숫자 모으기: 1분마다 확인해서 정해진 시각(dashboard.collect_times)이 지났으면 API 카드를 읽고,
    Blog-Auto 기록·봇 DB 카드는 export_interval_minutes 마다. 딜 수집·발행 루프와 따로 돌아 그쪽을 막지 않는다."""
    dash = getattr(bot, "dashboard", None)
    if dash is None or not dash.enabled:
        return
    await _sleep_or_stop(stop, 30)  # 시작 직후의 자기 점검·첫 수집이 먼저
    while not stop.is_set():
        try:
            ran = await dash.tick()
            if ran:
                log.info("dashboard collected: %s", ", ".join(ran))
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard collect failed: %s", e)
        earn = getattr(bot, "earn_coupang", None)
        if earn is not None:  # 제휴 수익: 쿠팡 실적 3시간마다 (읽을 때가 아니면 바로 돌아옴 — dashboard/earn.py)
            try:
                got = await earn.tick()
                if got:
                    log.info("earnings coupang: %s", got)
            except Exception as e:  # noqa: BLE001
                log.warning("earnings coupang failed: %s", type(e).__name__)
        try:  # 새 제휴 수익 → 앱 알림 (주인 10/24 — notify/earn_watch)
            from dealbot.notify import earn_watch

            await earn_watch.check(bot)
        except Exception as e:  # noqa: BLE001
            log.warning("earnings watch failed: %s", type(e).__name__)
        try:  # 핫딜 새 글 → '오픈톡방에 보내기' 앱 알림 (주인 10/24 — notify/openchat_watch)
            from dealbot.notify import openchat_watch

            await openchat_watch.check(bot)
        except Exception as e:  # noqa: BLE001
            log.warning("open chat watch failed: %s", type(e).__name__)
        await _sleep_or_stop(stop, 60)


NOTIFY_TICK_SECONDS = 2.0
HEARTBEAT_SECONDS = 60.0
GC_SECONDS = 3600.0


async def notify_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """주인 알림·대시보드 다리 (주인 10/18), 2초마다: Blog-Auto 알림 받기 → 푸시 보내기(묶기·한도) → 블로그 일 결과 읽기.
    1분마다 생존 신호(dashboard_status.json)·오래된 일 정리·아침/저녁 정리, 1시간마다 받은 파일 정리.
    하나가 실패해도 나머지는 돈다 (딜 수집·발행 루프와 따로)."""
    from dealbot.bridge import status as bridge_status
    from dealbot.notify import ingest

    router = getattr(bot, "router", None)
    outbox = getattr(bot, "actions_out", None)
    if router is None and outbox is None:
        return
    if router is not None:
        router.load_key()  # 알림 키 (처음이면 만든다 — 값은 남기지 않음)
    last_beat = last_gc = 0.0
    while not stop.is_set():
        now = time.monotonic()
        try:
            if router is not None:
                ingest.ingest(bot.settings.data_dir, router)
                await router.tick()
        except Exception as e:  # noqa: BLE001
            log.warning("notify tick failed: %s", e)
        try:
            if outbox is not None:
                outbox.ingest()
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard results ingest failed: %s", e)
        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            try:
                if router is not None:
                    bridge_status.write_dashboard(bot.settings.data_dir, push_devices=len(router.devices()),
                                                  push_ok_at=router.store.last_push_ok())
                    router.watch_blogauto(bridge_status.read_blogauto(bot.settings.data_dir))  # 블로그 프로그램 멈춤 → ⛔
                if outbox is not None:
                    outbox.sweep()
                digests = getattr(bot, "digests", None)
                if digests is not None:
                    await digests.tick()
            except Exception as e:  # noqa: BLE001
                log.warning("notify minute jobs failed: %s", e)
        if now - last_gc >= GC_SECONDS:
            last_gc = now
            api = getattr(getattr(bot, "dashboard", None), "api", None)
            try:
                if api is not None:
                    api.uploads.gc()
            except Exception as e:  # noqa: BLE001
                log.warning("dashboard upload gc failed: %s", e)
        await _sleep_or_stop(stop, NOTIFY_TICK_SECONDS)


INSTAGRAM_TICK_SECONDS = 60


async def instagram_loop(bot: DealBot, stop: asyncio.Event) -> None:
    """인스타 시간표 (주인 결정 10/15): 1분마다 인스타 대기열·점심 릴스·저녁 TOP5·화금 단가표 중 차례가 된 글 하나를 올린다.
    텔레그램 발행 루프와 따로 돌아 (릴스 영상 처리를 몇 분 기다려도) 채널 발행을 막지 않는다. 연습 모드·인스타 꺼짐이면 할 일 없음."""
    ig = getattr(bot, "ig", None)
    if ig is None or not bot.settings.instagram.enabled:
        return
    await _sleep_or_stop(stop, 20)  # 시작 직후의 자기 점검·첫 수집이 먼저
    while not stop.is_set():
        if not bot.state.paused:
            try:
                with publishing_mark("publish_instagram"):
                    done = await bot.instagram_tick()
                if done:
                    log.info("instagram: %s", done)
            except Exception as e:  # noqa: BLE001
                log.exception("instagram loop error")
                bot.db.log_event("ERROR", "instagram", f"{type(e).__name__}: {e}")
        await _sleep_or_stop(stop, INSTAGRAM_TICK_SECONDS)


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
    export_links = getattr(bot, "export_channel_links", None)
    if export_links is not None:
        export_links()  # 기억한 채널 초대 링크를 Blog-Auto 에 (channel_links.json — 핫딜 블로그 서명의 demiyum 링크)
    log.info("DealBot started: %r (tz=%s)", bot, ZoneInfo(bot.settings.app.timezone))
    bot.db.log_event("INFO", "lifecycle", "started")
    try:
        checks = await bot.self_check()
        lines = [("✅ " if ok else "⚠️ " if ok is None else "❌ ") + text for ok, text in checks]
        for line in lines:
            log.info("self-check: %s", line)
        await selfcheck_alerts(bot, lines)
        domain_todo = getattr(bot, "domain_todo", None)
        if domain_todo is not None:
            await domain_todo()  # 내 도메인 DNS 가 이 서버를 안 가리키면 주인 할 일 (https.sh --domain 뒤)
        if not bot.notifier.enabled:
            log.warning(
                "관리자 알림을 보낼 수 없습니다 — TELEGRAM_BOT_TOKEN=%s, TELEGRAM_ADMIN_CHAT_ID=%s. "
                "두 값을 모두 설정하고 봇에게 /start 를 보낸 뒤 재배포하세요.",
                "설정됨" if bot.settings.secrets.has_telegram else "없음",
                bot.settings.secrets.telegram_admin_chat_id or "없음",
            )
        elif bot.notifier.tg_quiet:  # 주인 10/18: 관리자 챗 알림 끔 — 점검 결과는 대시보드 '연결' → 봇 상태, ❌ 는 위에서 할 일로
            log.info("시작 알림은 보내지 않음 (대시보드 '연결' 에서 봄)")
        elif not startup_notice_due(bot.db, lines):
            log.info("시작 알림 생략 — 자기 점검 결과가 지난 알림과 같습니다 (/status 로 확인)")
        else:
            sent = await bot.notifier.notify_startup(bot.reporter.status_text(), lines)
            if sent is False:
                log.warning("시작 알림 전송 실패 — 봇에게 /start 를 먼저 보냈는지, 챗 ID 가 맞는지 확인하세요")
            else:
                mark_startup_notice(bot.db, lines)
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
        asyncio.create_task(owner_summary_loop(bot, stop), name="owner_summary_loop"),
        asyncio.create_task(blog_digest_loop(bot, stop), name="blog_digest_loop"),
        asyncio.create_task(maintenance_loop(bot, stop), name="maintenance_loop"),
        asyncio.create_task(heartbeat_loop(bot, stop), name="heartbeat_loop"),
        asyncio.create_task(soldout_loop(bot, stop), name="soldout_loop"),
        asyncio.create_task(dashboard_loop(bot, stop), name="dashboard_loop"),
        asyncio.create_task(instagram_loop(bot, stop), name="instagram_loop"),
        asyncio.create_task(sns_loop(bot, stop), name="sns_loop"),
        asyncio.create_task(notify_loop(bot, stop), name="notify_loop"),
    ]
    try:
        await stop.wait()
    finally:
        stop.set()  # 예외로 빠져나와도 루프들이 멈추도록
        # 발행 중이던 글은 스레드 글·답글까지(인스타는 하던 글까지) 마치게 발행 루프만 기다리고 (최대 shutdown_grace()), 나머지 루프는 바로 끊는다
        publishers = [t for t in tasks if t.get_name() in ("publisher_loop", "instagram_loop")]
        for t in tasks:
            if t not in publishers:
                t.cancel()
        grace = shutdown_grace()
        log.info("shutting down — 하던 발행을 마치는 중 (최대 %g초)...", grace)
        _, pending = await asyncio.wait(publishers, timeout=grace)
        for t in pending:
            log.warning("종료 대기 시간이 지나 중단합니다: %s", t.get_name())
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        bot.db.log_event("INFO", "lifecycle", "stopped")
        await bot.close()
