"""재시작(SIGTERM) 때 하던 발행을 마치고 끄는지, 자동 업데이트와 주고받는 표식(var/)을 제대로 쓰는지."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import dealbot.scheduler as sched


async def _until(cond: Any, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


class FakeBot:
    """run_forever 가 부르는 것만 흉내 낸다. process_queue_once 는 스레드 글 → 링크 답글 순서처럼 시간이 걸림."""

    def __init__(self, publish_seconds: float = 0.3) -> None:
        self.calls: list[str] = []
        self.publishing = asyncio.Event()
        self.publish_seconds = publish_seconds
        self.marks_seen: list[list[str]] = []
        self.settings = SimpleNamespace(
            app=SimpleNamespace(timezone="Asia/Seoul", scheduler_tick_seconds=60),
            publish=SimpleNamespace(publisher_tick_seconds=0.05),
            secrets=SimpleNamespace(has_telegram=False, telegram_admin_chat_id=None),
        )
        self.notifier = SimpleNamespace(enabled=False)
        self.state = SimpleNamespace(set_error=lambda *_: None)
        self.db = SimpleNamespace(log_event=lambda *a: self.calls.append(f"log:{a[-1]}"))
        self.queue = 1

    async def start_telegram(self, polling: bool = True) -> None:
        return None

    async def start_web(self) -> None:
        return None

    async def self_check(self) -> list[Any]:
        return []

    async def sweep_unverified_reviews(self) -> None:
        return None

    async def process_queue_once(self) -> bool:
        if sched.VAR_DIR is not None:
            self.marks_seen.append(sorted(p.name for p in (sched.VAR_DIR / "busy").glob("*")))
        if not self.queue:
            return False
        self.queue -= 1
        self.calls.append("threads_post")
        self.publishing.set()
        await asyncio.sleep(self.publish_seconds)  # 컨테이너 대기·답글 재시도
        self.calls.append("threads_reply_with_link")
        return True

    async def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def var_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "var"
    monkeypatch.setattr(sched, "VAR_DIR", d)
    monkeypatch.setattr(sched, "CODE_SHA", "abc1234")

    async def idle(bot: Any, stop: asyncio.Event) -> None:
        await stop.wait()

    for name in ("collector_loop", "daily_summary_loop", "blog_digest_loop", "maintenance_loop", "heartbeat_loop", "soldout_loop"):
        monkeypatch.setattr(sched, name, idle)
    return d


async def test_sigterm_waits_for_the_publish_in_progress(var_dir: Path) -> None:
    """systemctl restart(SIGTERM)가 스레드 글과 링크 답글 사이에 와도 답글까지 올리고 끈다."""
    bot = FakeBot(publish_seconds=0.3)
    task = asyncio.create_task(sched.run_forever(bot))  # type: ignore[arg-type]
    await asyncio.wait_for(bot.publishing.wait(), 5)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 10)
    assert bot.calls.index("threads_reply_with_link") == bot.calls.index("threads_post") + 1
    assert bot.calls[-1] == "close"
    assert not list((var_dir / "busy").glob("*"))  # 발행 중 표식은 끝나면 지움


async def test_shutdown_grace_is_bounded(var_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """끝나지 않는 작업은 기다리는 시간이 지나면 끊고 종료한다 (systemd 가 강제 종료하기 전에)."""
    monkeypatch.setattr(sched, "SHUTDOWN_GRACE_SECONDS", 0.2)
    bot = FakeBot(publish_seconds=3600)
    task = asyncio.create_task(sched.run_forever(bot))  # type: ignore[arg-type]
    await asyncio.wait_for(bot.publishing.wait(), 5)
    started = time.monotonic()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)
    assert time.monotonic() - started < 3
    assert "threads_reply_with_link" not in bot.calls and bot.calls[-1] == "close"
    assert sched.SHUTDOWN_GRACE_SECONDS < 90  # systemd 기본 TimeoutStopSec 안


async def test_running_version_is_written_at_startup(var_dir: Path) -> None:
    (var_dir / "busy").mkdir(parents=True)
    (var_dir / "busy" / "publish_99999").write_text("publish", encoding="utf-8")  # 강제 종료로 남은 표식
    bot = FakeBot()
    bot.queue = 0
    task = asyncio.create_task(sched.run_forever(bot))  # type: ignore[arg-type]
    for _ in range(100):
        if (var_dir / "running_version").exists():
            break
        await asyncio.sleep(0.02)
    assert (var_dir / "running_version").read_text(encoding="utf-8").strip() == "abc1234"
    assert not (var_dir / "busy" / "publish_99999").exists()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 5)


async def test_publisher_marks_busy_while_publishing(var_dir: Path) -> None:
    bot = FakeBot(publish_seconds=0)
    stop = asyncio.Event()
    task = asyncio.create_task(sched.publisher_loop(bot, stop))  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert bot.marks_seen and all(m == [f"publish_{os.getpid()}"] for m in bot.marks_seen)
    assert not list((var_dir / "busy").glob("*"))


async def test_publisher_pauses_while_auto_update_is_swapping_code(var_dir: Path) -> None:
    """auto-update.sh 가 새 코드를 받는 동안(var/updating) 옛 프로세스는 새 글을 시작하지 않는다 —
    받은 새 템플릿을 옛 코드로 그리다 깨지지 않게. 재시작으로 새로 켜진 프로세스는 그 전 표식을 무시한다."""
    bot = FakeBot(publish_seconds=0)
    stop = asyncio.Event()
    task = asyncio.create_task(sched.publisher_loop(bot, stop))  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    var_dir.mkdir(parents=True, exist_ok=True)
    bot.queue = 5
    before = len(bot.marks_seen)
    (var_dir / "updating").touch()  # 업데이트 시작 (이 프로세스보다 나중에 생긴 표식)
    await asyncio.sleep(0.3)
    assert bot.queue == 5 and len(bot.marks_seen) == before  # 발행을 시작하지 않음
    (var_dir / "updating").unlink()
    await _until(lambda: bot.queue == 0)
    stop.set()
    await asyncio.wait_for(task, 5)

    # 새로 켜진 프로세스: 시작 전부터 있던 표식은 무시
    (var_dir / "updating").touch()
    old = time.time() - 5
    os.utime(var_dir / "updating", (old, old))
    bot2 = FakeBot(publish_seconds=0)
    stop2 = asyncio.Event()
    task2 = asyncio.create_task(sched.publisher_loop(bot2, stop2))  # type: ignore[arg-type]
    await _until(lambda: bot2.queue == 0)
    stop2.set()
    await asyncio.wait_for(task2, 5)


def test_stale_update_marker_is_ignored(var_dir: Path) -> None:
    var_dir.mkdir(parents=True)
    (var_dir / "updating").touch()
    t = time.time() - sched.UPDATE_MARK_MAX_AGE - 5
    os.utime(var_dir / "updating", (t, t))
    assert sched.update_in_progress(started_at=t - 100) is False  # 업데이트가 죽고 남은 표식
    (var_dir / "updating").touch()
    assert sched.update_in_progress(started_at=time.time() - 100) is True


def test_no_var_dir_outside_a_git_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """도커 이미지처럼 git 체크아웃이 아니면 표식을 쓰지 않는다."""
    monkeypatch.setattr(sched, "VAR_DIR", None)
    sched.write_running_version()
    with sched.publishing_mark():
        pass
    assert sched.update_in_progress(started_at=0) is False


def test_var_dir_points_at_the_repo_checkout() -> None:
    root = Path(sched.__file__).resolve().parents[2]
    if (root / ".git").exists():
        assert sched.VAR_DIR == root / "var"
