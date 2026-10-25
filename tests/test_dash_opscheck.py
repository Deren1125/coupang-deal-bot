"""할 일 페이지 맨 아래 '직전 AI 점검' 칸 (주인 10/25 — Blog-Auto 기록 파일 opscheck 칸 → state['opscheck']).

매시간 오류 점검 AI 의 바로 전 차례 기록만. 글은 서버에서 만든다 (app.js 는 title·rows 를 기존 접는 줄 devRow + 목록 시트로 그대로 그림)."""

from __future__ import annotations

from pathlib import Path

from dash_helpers import blogauto_status, fixture
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.blogauto import _opscheck
from dealbot.dashboard.webapp import STATIC_DIR
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export


def test_reader_keeps_contract_and_drops_bad_shapes() -> None:
    fx = fixture("export_v2.json")["opscheck"]
    got = _opscheck(fx)
    assert got == fx
    assert _opscheck(dict(fx, lines=fx["lines"] + ["", 3, "  "]))["lines"] == fx["lines"]
    assert _opscheck(dict(fx, lines="x"))["lines"] == []
    for bad in (None, "x", dict(fx, result="weird"), dict(fx, at="어제"), dict(fx, summary=" ")):
        assert _opscheck(bad) is None


async def test_state_opscheck_card_text(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    d = (await state(bot))["opscheck"]
    assert d["title"] == "직전 AI 점검 · 10/25 12:04 · 1개 고쳤어요"
    assert d["rows"][0] == {"text": "🔧 1개 고쳤어요", "bad": False}
    assert d["rows"][1:] == [{"text": "메모리 경고 — 계속되는지 다음 차례에 봐요"}, {"text": "딜봇 자동 링크 시간 초과를 한 번 더 시도하게 고쳤어요"}]
    write_export(tmp_path / "blogauto.json", opscheck={"at": "10/25 13:04", "result": "blocked", "summary": "테스트 실패로 못 올렸어요", "lines": []})
    d = (await state(bot))["opscheck"]
    assert d["rows"] == [{"text": "⛔ 테스트 실패로 못 올렸어요", "bad": True}]
    write_export(tmp_path / "blogauto.json", opscheck=None)
    assert (await state(bot))["opscheck"] is None


def test_app_js_renders_the_card_without_a_new_file() -> None:
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "devRow(S.opscheck, '직전 AI 점검')" in js and "직전 AI 점검" in js
