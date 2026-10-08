"""/status·시작 알림에 지금 돌고 있는 커밋이 보이는지 (서버가 옛 코드인지 텔레그램에서 바로 확인)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import dealbot.monitoring.admin as admin
from dealbot.app import DealBot
from dealbot.config import Settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not ((ROOT / ".git").exists() and shutil.which("git")), reason="git 체크아웃 필요")
def test_code_version_reads_the_checkout_commit() -> None:
    head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    sha, label = admin._code_version(ROOT)
    assert head.startswith(sha) and len(sha) >= 7
    assert label.startswith(sha + " ") and len(label.split()) == 3  # '4ccfbc1 10/07 11:05'


def test_code_version_without_git_uses_deploy_env_or_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "0123456789abcdef")
    assert admin._code_version(tmp_path) == ("0123456", "0123456")
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA")
    assert admin._code_version(tmp_path) == ("?", "")
    (tmp_path / ".git").mkdir()  # 깨진 체크아웃: git 이 실패해도 죽지 않음
    assert admin._code_version(tmp_path) == ("?", "")


def test_version_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "CODE_VERSION", "4ccfbc1 10/07 11:05")
    assert admin.version_label() == f"{admin.__version__} · 4ccfbc1 10/07 11:05"
    monkeypatch.setattr(admin, "CODE_VERSION", "")
    assert admin.version_label() == admin.__version__


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    b = DealBot(settings)
    sent: list[str] = []

    async def capture(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    b.notifier.send = capture  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    yield b
    b.db.close()


async def test_startup_message_and_status_show_the_running_commit(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "CODE_VERSION", "4ccfbc1 10/07 11:05")
    status = bot.reporter.status_text()
    assert f"DealBot v{admin.__version__} · 4ccfbc1 10/07 11:05" in status
    assert bot.reporter.status_context()["version"] == f"{admin.__version__} · 4ccfbc1 10/07 11:05"
    await bot.notifier.notify_startup(status, ["텔레그램 연결"])
    first = bot.sent[0].splitlines()[0]  # type: ignore[attr-defined]
    assert first == f"🟢 <b>봇이 켜졌습니다</b> (v{admin.__version__} · 4ccfbc1 10/07 11:05)"
