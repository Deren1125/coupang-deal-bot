from __future__ import annotations

from pathlib import Path

import pytest

from dealbot.config import Settings, load_settings


def test_load_repo_config(repo_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COUPANG_ACCESS_KEY", "ak")
    monkeypatch.setenv("COUPANG_SECRET_KEY", "sk")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHANNEL_ID", "@mychannel")
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    monkeypatch.setenv("DEALBOT_DRY_RUN", "true")

    s = load_settings(repo_root / "config.yaml", load_env=False)
    assert [c.name for c in s.collectors] == ["goldbox", "category_best", "ppomppu", "ruliweb_biz", "ruliweb_user", "algumon", "adpick"]
    assert {sh.key for sh in s.shops} >= {"coupang", "toss", "naver"}
    assert s.deal.community_min_recommend == 5
    assert s.deal.accept_coupons_and_events is False  # 이벤트/쿠폰(가격 없는 글)은 올리지 않기로 함
    assert s.deal.min_discount_rate == 50
    assert s.deal.min_below_average_pct == 15
    assert s.coupang.max_calls_per_hour == 10 and s.deal.market.enabled and not s.browser.enabled
    assert s.secrets.has_coupang and s.secrets.has_channel and s.secrets.has_admin
    assert s.secrets.telegram_admin_chat_id == 42
    assert s.publish.dry_run is True
    assert s.db_path == tmp_path / "dealbot.db"
    assert s.templates_dir == (repo_root / "templates").resolve()


def test_defaults_without_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    s = load_settings(None, load_env=False)
    assert isinstance(s, Settings)
    assert s.collectors == []
    assert not s.secrets.has_coupang


def test_missing_explicit_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.yaml", load_env=False)


def test_duplicate_collector_names(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "collectors:\n  - {name: a, type: ppomppu}\n  - {name: a, type: ppomppu}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_settings(cfg, load_env=False)


def test_bad_summary_time(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("monitoring:\n  daily_summary_time: '25:00'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg, load_env=False)
