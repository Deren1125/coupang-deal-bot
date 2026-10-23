from __future__ import annotations

import re
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
    assert [c.name for c in s.collectors] == ["goldbox", "category_best", "ppomppu", "ruliweb_biz", "ruliweb_user", "algumon", "clien_jirum", "adpick"]
    assert {sh.key for sh in s.shops} >= {"coupang", "toss", "naver"}
    assert s.deal.community_min_recommend == 10 and s.deal.recommend_needs_support and not s.deal.discount_alone
    assert s.deal.accept_coupons_and_events is False  # 이벤트/쿠폰(가격 없는 글)은 올리지 않기로 함
    assert s.deal.market.min_below_market_pct == 20 and s.deal.market.strict  # 쿠팡 최저가보다 20% 이상 싸야 특가
    assert s.monitoring.heartbeat_minutes == 0 and s.monitoring.notify_on_publish is False  # 관리자 챗은 에러·요청 위주로 조용히
    assert s.publish.allow_raw_links is False  # 제휴 링크를 못 만드는 몰은 건너뜀
    assert s.deal.min_discount_rate == 50
    assert s.deal.min_below_average_pct == 15
    assert s.coupang.max_calls_per_hour == 10 and s.deal.market.enabled and not s.browser.enabled
    assert s.secrets.has_coupang and s.secrets.has_channel and s.secrets.has_admin
    assert s.secrets.telegram_admin_chat_id == 42
    assert s.publish.dry_run is True
    assert s.db_path == tmp_path / "dealbot.db"
    assert s.templates_dir == (repo_root / "templates").resolve()


def test_model_names_come_only_from_the_server_env(repo_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """모델 이름은 저장소(config.yaml·코드)에 두지 않는다: 서버 .env 의 DEALBOT_MODEL_* 에서만. 없으면 None (CLI 기본 모델·API 건너뜀)."""
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path))
    s = load_settings(repo_root / "config.yaml", load_env=False)
    assert s.publish.commentary.model is None and s.info_posts.summarizer.model is None
    assert s.monitoring.nl_router.model is None and s.monitoring.nl_router.cli_model is None
    monkeypatch.setenv("DEALBOT_MODEL_COMMENTARY", "test-cli-model")
    monkeypatch.setenv("DEALBOT_MODEL_SUMMARIZER", "'test-model'")  # 따옴표째 붙여넣어도 됨
    s = load_settings(repo_root / "config.yaml", load_env=False)
    assert s.publish.commentary.model == "test-cli-model" and s.info_posts.summarizer.model == "test-model"


def test_no_model_ids_in_the_repo(repo_root: Path) -> None:
    """버전이 붙은 모델 ID 가 코드·설정·문서·테스트 어디에도 없다 (모델 이름은 서버 .env 에서만)."""
    pattern = re.compile(r"claude-(?:\d|[a-z]+-\d)", re.IGNORECASE)
    files = [repo_root / n for n in ("config.yaml", "README.md", "Dockerfile", "docker-compose.yml", "pyproject.toml", "railway.json", "Makefile")]
    for d in ("src", "tests", "docs", "deploy", "templates"):
        files += [f for f in (repo_root / d).rglob("*") if f.is_file() and f.suffix in {".py", ".yaml", ".yml", ".md", ".sh", ".j2", ".toml", ".json", ".txt", ".service", ".conf"}]
    found = []
    for f in files:
        if not f.is_file() or f.name.startswith(".env") or "fixtures" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        found += [f"{f.relative_to(repo_root)}: {m.group(0)}" for m in pattern.finditer(text)]
    assert found == []
    assert "model:" not in (repo_root / "config.yaml").read_text(encoding="utf-8")


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
