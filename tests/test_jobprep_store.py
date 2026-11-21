"""취준 저장소 — 표 만들기 · data_dir 밖 경로 거절 · 폴더 700 · 파일 600 · 처음 쓸 때만 디스크."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import store as st


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_tables_and_permissions(tmp_path: Path) -> None:
    s = st.Store(tmp_path / "jp")
    assert not (tmp_path / "jp").exists()  # 열기 전엔 디스크를 안 건드림
    names = {r["name"] for r in s.q("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("kv", "llm_cache", "usage", "chat", "jobs", "companies", "job_events", "watch_pages", "exclusions", "cases", "resumes",
              "ctci_progress", "attempts", "reviews", "library", "speech_cards", "speech_takes", "human_topics", "human_takes", "eng_week",
              "eng_sessions", "eng_fix", "eng_expr", "pron_sents", "pron_takes", "acad_docs", "acad_courses", "acad_req", "acad_plan", "profile"):
        assert t in names, t
    assert mode(tmp_path / "jp") == 0o700 and mode(tmp_path / "jp" / "jobprep.db") == 0o600
    p = s.write("acad", "20261122_ab12.jpg", b"x")
    assert mode(p) == 0o600 and mode(p.parent) == 0o700
    s.kv_set("a", {"b": 1})
    assert s.kv_get("a") == {"b": 1} and s.kv_get("none", 5) == 5


@pytest.mark.parametrize("kind,name", [("acad", "../x"), ("acad", "a/b"), ("etc", "a"), ("acad", ".hidden"), ("acad", "..")])
def test_paths_outside_data_dir_are_refused(tmp_path: Path, kind: str, name: str) -> None:
    s = st.Store(tmp_path / "jp")
    with pytest.raises(st.PathError):
        s.path(kind, name)
    assert s.read(kind, name) is None


def test_symlink_escape_refused(tmp_path: Path) -> None:
    s = st.Store(tmp_path / "jp")
    s.ensure_dir("acad")
    (tmp_path / "secret").write_text("k")
    (tmp_path / "jp" / "acad" / "link.jpg").symlink_to(tmp_path / "secret")
    with pytest.raises(st.PathError):
        s.path("acad", "link.jpg")


def test_mem_free_reads_meminfo() -> None:
    assert st.mem_free_mb("MemTotal: 3924000 kB\nMemAvailable:    1024000 kB\n") == 1000
    assert st.mem_free_mb("garbage") == 99999
