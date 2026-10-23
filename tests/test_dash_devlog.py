"""할 일 페이지 맨 아래 '오늘 바뀐 것' 칸 (주인 10/23 — Blog-Auto 기록 파일 devlog 칸 → state['devlog']).

글은 서버에서 만든다 (app.js 는 title·rows 를 기존 접는 줄 + 목록 시트로 그대로 그림)."""

from __future__ import annotations

from pathlib import Path

from dash_helpers import blogauto_status, fixture
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.blogauto import _devlog
from dealbot.dashboard.webapp import STATIC_DIR
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export


def test_reader_keeps_contract_rows_and_drops_bad_ones() -> None:
    fx = fixture("export_v2.json")["devlog"]
    got = _devlog(fx)
    assert got is not None and got["n"] == 2 and [x["repo"] for x in got["items"]] == ["딜봇", "블로그"]
    assert got["items"][1]["count"] == 2 and got["items"][0]["count"] == 1
    assert "sha" not in got["items"][0]  # 해시는 화면까지 가지 않음
    bad = dict(fx, items=fx["items"] + [{"at": "9:5", "repo": "딜봇", "text": "x"}, {"at": "10:00", "repo": "남", "text": "x"},
                                        {"at": "10:00", "repo": "딜봇", "text": ""}, "x"])
    assert len(_devlog(bad)["items"]) == 2
    assert _devlog(None) is None and _devlog({"day": "x"}) is None


async def test_state_devlog_card_text(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    d = (await state(bot))["devlog"]
    assert d["title"] == "오늘 바뀐 것 · 2개"
    assert d["rows"][0] == {"at": "09:05", "repo": "딜봇", "text": "할 일 페이지에 '오늘 바뀐 것' 칸을 넣었어요"}
    assert d["rows"][1]["text"] == "대시보드 손봤어요 (2번)"
    write_export(tmp_path / "blogauto.json", devlog={"day": "2026-10-18", "items": [], "n": 0})
    assert (await state(bot))["devlog"] is None
    write_export(tmp_path / "blogauto.json", devlog=None)
    assert (await state(bot))["devlog"] is None


def test_app_js_renders_the_card_without_a_new_file() -> None:
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "devRow(S.devlog)" in js and "오늘 바뀐 것" in js
