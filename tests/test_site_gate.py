"""사이트·계정마다 접속 상한 (주인 10/26 — '로그인이 자주 풀려 … 1분·1시간·하루 상한, 강력하게'). 블로그 프로그램과 같은 기록·같은 상한 표."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dealbot import site_gate as sg

FIX = Path(__file__).parent / "fixtures" / "dash_contract" / "site_gate_caps.json"
T = 1_800_000_000.0
URL = "https://blog.naver.com/x"
LOGIN = "https://nid.naver.com/nidlogin.login?mode=form"


@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    sg.configure(tmp_path / "site_gate.db")
    monkeypatch.setattr(sg, "_owner_until", {})
    yield tmp_path / "site_gate.db"
    sg.configure(None)


def test_caps_match_the_blog_program_contract() -> None:
    fx = json.loads(FIX.read_text(encoding="utf-8"))
    assert fx["caps"] == {k: list(v) for k, v in sg.CAPS.items()} and fx["all"] == {k: list(v) for k, v in sg.ALL_CAPS.items()}
    assert fx["login"] == list(sg.LOGIN_CAPS) and fx["lock_h"] == sg.LOCK_H and fx["settle_s"] == sg.SETTLE_S
    assert fx["windows_s"] == [w[1] for w in sg.WINDOWS] and fx["login_re"] == sg.LOGIN_RE.pattern


def test_minute_hour_day_caps_per_account_and_all_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "CAPS", {"naver.com": (2, 3, 4)})
    monkeypatch.setattr(sg, "ALL_CAPS", {"naver.com": (3, 10, 10)})
    assert [sg.gate(URL, "a", T + k) for k in range(2)] == ["", ""]
    assert "1분 2번" in sg.gate(URL, "a", T + 5)  # 1분 안 세 번째
    assert sg.gate(URL, "b", T + 6) == ""  # 다른 계정은 따로
    assert "모든 계정 합" in sg.gate(URL, "c", T + 7)  # 모든 계정 합 1분 3번
    assert sg.gate(URL, "a", T + 70) == ""  # 1분 지나면 됨
    assert "1시간 3번" in sg.gate(URL, "a", T + 140)
    assert sg.gate("https://example.com/", "a", T) == ""  # 표에 없는 사이트는 그냥 됨


def test_login_page_left_open_locks_the_account_until_owner_logs_in() -> None:
    assert sg.gate(URL, "a", T) == ""
    assert sg.saw(LOGIN, "a", T + 1) is True  # 로그인 화면으로 넘겨짐 (풀림)
    why = sg.gate(URL, "a", T + 1 + sg.SETTLE_S)
    assert "로그인이 풀려 멈춤" in why
    assert "로그인이 풀려 멈춤" in sg.gate(URL, "a", T + 3600)  # 계속 멈춤
    assert sg.gate(URL, "b", T + 3600) == ""  # 다른 계정은 그대로
    assert "로그인이 풀려 멈춤" in sg.gate(URL, "a", T + sg.LOCK_H * 3600 + 10)  # 10/27: 시간이 지나도 다시 확인 안 함 (UNTIL_OWNER)
    sg.UNTIL_OWNER = False
    try:
        assert sg.gate(URL, "a", T + sg.LOCK_H * 3600 + 10) == ""  # 끄면 예전처럼 LOCK_H 뒤 한 번 다시 확인
        sg.saw(LOGIN, "a", T + 30000)
        assert sg.gate(URL, "a", T + 30010)
    finally:
        sg.UNTIL_OWNER = True
    sg.owner_window("a")  # 주인이 /naverlogin — 풀고, 그동안 세지도 막지도 않음
    assert sg.gate(LOGIN, "a") == "" and sg.saw(LOGIN, "a") is False


def test_login_page_that_redirects_when_logged_in_does_not_lock() -> None:
    sg.saw(LOGIN, "a", T)
    sg.saw("https://blog.naver.com/me", "a", T + 1)  # 로그인돼 있어 바로 넘겨 줌
    assert sg.gate(URL, "a", T + 60) == ""


def test_login_page_itself_is_capped() -> None:
    assert sg.gate(LOGIN, "a", T) == ""
    sg.saw(LOGIN, "a", T)
    sg.saw(URL, "a", T + 1)
    assert "로그인 화면 1분 1번" in sg.gate(LOGIN, "a", T + 2)


async def test_route_aborts_blocked_top_navigations_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sg, "CAPS", {"naver.com": (1, 5, 5)})
    calls: list[str] = []

    def route(url: str, nav: bool = True, top: bool = True) -> SimpleNamespace:
        frame = SimpleNamespace(parent_frame=None if top else object())
        req = SimpleNamespace(url=url, is_navigation_request=lambda: nav, frame=frame)

        async def abort(why: str) -> None:
            calls.append("abort:" + why)
        return SimpleNamespace(request=req, abort=abort)

    assert await sg.check_route(route(URL), "a") is False
    assert await sg.check_route(route(URL, nav=False), "a") is False  # 사진·스크립트는 세지 않음
    assert await sg.check_route(route(URL, top=False), "a") is False  # 안쪽 창도
    assert await sg.check_route(route(URL), "a") is True and calls == ["abort:blockedbyclient"]


def test_session_and_qr_login_are_wired() -> None:
    src = (Path(sg.__file__).parent / "browser" / "session.py").read_text(encoding="utf-8")
    assert "site_gate.watch(self._context, self.gate_acct)" in src
    nc = (Path(sg.__file__).parent / "browser" / "naver_connect.py").read_text(encoding="utf-8")
    assert "site_gate.owner_window(self.session.gate_acct)" in nc
    app = (Path(sg.__file__).parent / "app.py").read_text(encoding="utf-8")
    assert 'site_gate.configure((ba_var if ba_var.is_dir() else settings.data_dir) / "site_gate.db")' in app
