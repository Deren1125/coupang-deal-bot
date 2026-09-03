from __future__ import annotations

import os
from pathlib import Path

import pytest

from dealbot.config import Settings, load_settings
from dealbot.storage.db import Database

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "COUPANG_ACCESS_KEY",
        "COUPANG_SECRET_KEY",
        "COUPANG_SUB_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHANNEL_ID",
        "TELEGRAM_ADMIN_CHAT_ID",
        "DEALBOT_CONFIG",
        "DEALBOT_DATA_DIR",
        "DEALBOT_DRY_RUN",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path / "data"))
    s = load_settings(ROOT / "config.yaml", load_env=False)
    s.publish.templates_dir = ROOT / "templates"
    s.publish.dry_run = True
    return s


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def repo_root() -> Path:
    return ROOT


def env_true(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true"}
