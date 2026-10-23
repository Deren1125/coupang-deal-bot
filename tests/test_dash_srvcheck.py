"""서버 점검 (주인: 터미널 명령 붙여넣기 대신 단추) — 연결 탭 '서버 점검'. 네트워크·진짜 명령 없이.

딜봇 몫(스레드·인스타 연결·최근 실패·서비스·오류 기록·버전)은 서버가 줄로 만들고, Blog-Auto 몫(server_check 일 — 계약
tests/fixtures/dash_contract/action_server_check.json, Blog-Auto 와 같은 파일)과 한 시트로 합친다. 토큰은 어떤 줄에도 없다."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import srvcheck, webapp
from dealbot.dashboard.actions import BLOG_ACTIONS, ActionError, clean_args
from dealbot.publisher import instagram, threads
from dealbot.utils.timeutil import to_iso, utcnow
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

SECRET = "EAAB" + "x9Z" * 20  # 토큰 모양 (60자)


def cp(out: str = "", code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


@pytest.fixture
def cmds(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """systemctl·journalctl·git 을 가짜로 — 부른 인자를 모은다."""
    calls: list[list[str]] = []

    def run(args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
        calls.append(list(args))
        if args[0] == "systemctl":
            return cp("active\n")
        if args[0] == "journalctl":
            return cp("")
        return cp("abc1234 10/23 09:00\n")

    monkeypatch.setattr(srvcheck, "_run", run)
    monkeypatch.setattr(srvcheck, "meminfo", lambda path="/proc/meminfo": {"MemAvailable": 700, "SwapTotal": 0})
    return calls


def link(bot: DealBot, name: str, *, days: int = 30, user: str = "dereninc") -> None:  # noqa: F811
    m = threads if name == "threads" else instagram
    bot.settings.threads.enabled = bot.settings.instagram.enabled = True
    bot.db.kv_set(m.KV_TOKEN, SECRET)
    bot.db.kv_set(m.KV_USER_ID, "1234")
    bot.db.kv_set(m.KV_USERNAME, user)
    bot.db.kv_set(m.KV_TOKEN_EXPIRES, to_iso(utcnow() + timedelta(days=days, hours=1)))
    bot.db.kv_set(m.KV_TOKEN_SAVED_AT, to_iso(utcnow() - timedelta(days=2)))


def test_contract_fixture_and_clean_args() -> None:
    want = fixture("action_server_check.json")
    assert want["action"] == "server_check" and want["args"] == {} and want["blog"] is None and want["confirmed"] is False
    assert "server_check" in BLOG_ACTIONS and "server_check" in fixture("status_blogauto.json")["actions"]
    assert clean_args("blog.server_check", {}) == {}
    assert clean_args("blog.server_check", {"cmd": "rm -rf /", "unit": "x; reboot"}) == {}  # 넣은 값은 어디에도 쓰지 않는다
    with pytest.raises(ActionError):
        clean_args("blog.nope", {})


def test_reason_mapping_for_common_errors() -> None:
    cases = {
        "OAuthException: Error validating access token: Session has expired": "다시 연결 필요",
        "(#10) Application does not have permission for this action": "다시 연결 필요",
        "authorize failed: invalid code": "다시 연결 필요",
        "(#4) Application request limit reached": "잠시 뒤 자동 재시도",
        "HTTP 429 too many requests": "잠시 뒤 자동 재시도",
        "(#24) The requested resource does not exist": "사진",
        "ReadTimeout: timed out": "자동 재시도",
        "12: not posted: no photo": "글 상태",
    }
    for msg, want in cases.items():
        assert want in srvcheck.reason_of(msg), msg
    assert srvcheck.reason_of("전혀 모르는 문제") == ""


def test_sns_connection_has_user_expiry_saved_at_and_never_the_token(bot: DealBot) -> None:  # noqa: F811
    link(bot, "threads", days=30)
    got = srvcheck.sns_connection(bot, "threads", "스레드")
    assert got.startswith("✅") and "@dereninc" in got and "D-30" in got and "연결한 때" in got and SECRET not in got
    link(bot, "instagram", days=2)
    got = srvcheck.sns_connection(bot, "instagram", "인스타그램")
    assert got.startswith("❌") and "다시 연결" in got and SECRET not in got
    link(bot, "instagram", days=8)
    assert srvcheck.sns_connection(bot, "instagram", "인스타그램").startswith("⚠️")
    bot.db.kv_set(instagram.KV_TOKEN, "")
    gone = srvcheck.sns_connection(bot, "instagram", "인스타그램")
    assert gone.startswith("❌") and "토큰이 없어요" in gone
    bot.settings.threads.enabled = False
    assert "꺼져" in srvcheck.sns_connection(bot, "threads", "스레드")


def test_sns_events_latest_five_trimmed_mapped_and_clean(bot: DealBot) -> None:  # noqa: F811
    link(bot, "threads")
    link(bot, "instagram")
    now = utcnow()
    old = now - timedelta(days=5)  # 다시 연결(2일 전)하기 전의 실패는 뺀다
    bot.db.log_event("WARNING", "threads", "연결 전 실패", now=old)
    bot.db.log_event("ERROR", "collector", "상관없는 종류")
    for i in range(6):
        bot.db.log_event("WARNING", "threads", f"{i}: OAuthException Session has expired " + "가" * 200,
                         now=now - timedelta(minutes=60 - i))
    bot.db.log_event("ERROR", "instagram", f"(#4) rate limit access_token={SECRET} and {SECRET}", now=now)
    bot.db.log_event("INFO", "instagram", "INFO 는 보이지 않아요", now=now)
    rows = srvcheck.sns_events(bot)
    assert len(rows) == 5 and rows[0].startswith("❌ 인스타그램") and "잠시 뒤 자동 재시도" in rows[0]
    assert all(r.startswith("⚠️ 스레드") and "다시 연결 필요" in r for r in rows[1:])
    joined = "\n".join(rows)
    assert SECRET not in joined and "access_token=***" in joined and "연결 전" not in joined and "상관없는" not in joined
    assert all(len(r.split(" — ", 1)[1]) <= srvcheck.EVENT_MSG_MAX + 80 for r in rows)  # 원문은 120자 + 이유
    assert max(len(r.rsplit(" · ", 1)[-1]) for r in rows) <= srvcheck.EVENT_MSG_MAX
    bot.db._conn.execute("DELETE FROM events")
    assert srvcheck.sns_events(bot) == ["✅ 스레드·인스타 최근 실패 — 없어요"]


def test_service_and_journal_lines_and_unknown_when_not_permitted() -> None:
    assert srvcheck.service_line(lambda u: "active").startswith("✅")
    down = srvcheck.service_line(lambda u: "failed")
    assert down.startswith("❌") and "restart dealbot" in down
    assert "확인 못 함" in srvcheck.service_line(lambda u: None)
    assert srvcheck.journal_line(lambda u: 0).startswith("✅")
    assert srvcheck.journal_line(lambda u: 3).startswith("⚠️")
    assert srvcheck.journal_line(lambda u: 90).startswith("❌")
    unk = srvcheck.journal_line(lambda u: None)
    assert unk.startswith("⚠️") and "확인 못 함" in unk
    assert srvcheck.version_line("abc1234 10/23 09:00", "abc1234").startswith("✅")
    assert srvcheck.version_line("def5678 10/23 10:00", "abc1234").startswith("⚠️")  # 받았지만 재시작 전
    assert "확인 못 함" in srvcheck.version_line("")


def test_commands_have_fixed_args_no_shell_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[list[str], dict]] = []

    def fake(args: list[str], **kw: object) -> subprocess.CompletedProcess:
        seen.append((args, kw))
        return cp("active\n")

    monkeypatch.setattr(subprocess, "run", fake)
    srvcheck.unit_state("dealbot")
    srvcheck.journal_errors("dealbot")
    srvcheck.code_version()
    assert [a[:2] for a, _ in seen] == [["systemctl", "is-active"], ["journalctl", "-u"], ["git", "-C"]]
    assert all(isinstance(a, list) and kw["shell"] is False and kw["timeout"] <= 10 for a, kw in seen)
    assert seen[0][0] == ["systemctl", "is-active", "dealbot"] and "-p" in seen[1][0] and "err" in seen[1][0]
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))
    assert srvcheck.unit_state("dealbot") is None and srvcheck.journal_errors("dealbot") is None and srvcheck.code_version() is None


def test_local_lines_one_failing_part_does_not_stop_the_rest(bot: DealBot, cmds: list) -> None:  # noqa: F811
    link(bot, "threads")
    link(bot, "instagram")
    rows = srvcheck.local_lines(bot)
    assert [r[:1] for r in rows][0] == "✅" and any("서비스 dealbot" in r for r in rows) and any("코드 버전" in r for r in rows)
    assert not any("메모리" in r for r in rows) and any("메모리" in r for r in srvcheck.local_lines(bot, with_memory=True))
    bot.db.kv_set(threads.KV_TOKEN_EXPIRES, "아무 글자")  # 날짜가 깨져도 다른 줄은 나온다
    again = srvcheck.local_lines(bot)
    assert any("점검 한 칸" in r for r in again) and any("서비스 dealbot" in r for r in again)
    assert SECRET not in "\n".join(rows + again)


def test_cooldown_two_minutes() -> None:
    t = [1000.0]
    c = srvcheck.Cooldown(lambda: t[0])
    assert c.hit() == 0
    t[0] += 30
    left = c.hit()
    assert 85 <= left <= 90 and "초 뒤" in srvcheck.Cooldown.text(left)
    t[0] += 91
    assert c.hit() == 0


def test_blog_part_when_blogauto_did_not_answer() -> None:
    assert srvcheck.blog_part("ok", "done", "✅ a — b\n⚠️ c — d") == ["✅ a — b", "⚠️ c — d"]
    assert "받지 않았어요" in srvcheck.blog_part("expired", "expired", "")[0]
    assert "끊겼어요" in srvcheck.blog_part("unknown", "", "")[0]
    assert srvcheck.blog_part("error", "failed", "실패했어요 (X)")[0].startswith("❌")
    assert srvcheck.blog_part("rejected", "busy", "방금 점검했어요")[0].startswith("⚠️")


async def test_button_press_flow_queue_cooldown_and_one_sheet(bot: DealBot, settings: Settings, tmp_path: Path,  # noqa: F811
                                                              cmds: list) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    link(bot, "threads")
    link(bot, "instagram")
    bot.db.log_event("WARNING", "threads", "OAuthException token expired")
    h = session_headers(bot)
    want = fixture("action_server_check.json")
    status, body = await act(bot, "blog.server_check", {}, h)
    assert status == 202, body  # 확인 창 없음
    r = spool.root(settings.data_dir, spool.DASH_ACTIONS)
    f = spool.read(r / "new" / f"{body['id']}.json")
    assert set(f) == set(want) and f["action"] == "server_check" and f["args"] == {} and f["confirmed"] is False
    # 2분 안에 또 누르면 안내
    status, again = await act(bot, "blog.server_check", {}, session_headers(bot))
    assert status == 429 and again["code"] == "cooldown" and "초 뒤에 다시" in again["text"]
    assert len(spool.names(r, "new")) == 1
    # Blog-Auto 가 결과를 씀 → 읽을 때 딜봇 몫과 합쳐 한 시트
    spool.claim(r, body["id"])
    blog_text = "✅ 네이버 로그인 Pick — 로그인 정상 · 블로그 derenpick 맞아요\n⚠️ 메모리 — 여유 250MB · 스왑 없음 → 부족해지는 중이에요"
    spool.write(r, body["id"], {"v": 1, "id": body["id"], "status": "ok", "code": "done", "text": blog_text, "post": None,
                                "started_at": time.time(), "updated_at": time.time(), "done_at": time.time()}, sub="results")
    res = await bot.web.dispatch("GET", f"/dashboard/api/actions?ids={body['id']}", h)
    got = json.loads(res[1])["actions"][0]
    rows = got["text"].split("\n")
    assert got["status"] == "ok" and rows[0].startswith("✅ 네이버 로그인 Pick") and rows[1].startswith("⚠️ 메모리")
    assert any(x.startswith("✅ 스레드 연결") for x in rows) and any("다시 연결 필요" in x for x in rows)
    assert any("서비스 dealbot" in x for x in rows) and any("코드 버전 딜봇" in x for x in rows)
    assert SECRET not in got["text"] and all(x[:1] in ("✅", "⚠", "❌") for x in rows)
    # 명령은 정해진 목록만
    assert {c[0] for c in cmds} == {"systemctl", "journalctl", "git"}
    assert all(c[:2] == ["systemctl", "is-active"] and c[2] == "dealbot" for c in cmds if c[0] == "systemctl")


async def test_blogauto_failure_still_shows_the_dealbot_half(bot: DealBot, settings: Settings, tmp_path: Path,  # noqa: F811
                                                             cmds: list) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    link(bot, "threads")
    h = session_headers(bot)
    status, body = await act(bot, "blog.server_check", {}, h)
    r = spool.root(settings.data_dir, spool.DASH_ACTIONS)
    spool.claim(r, body["id"])
    spool.write(r, body["id"], {"v": 1, "id": body["id"], "status": "error", "code": "failed", "text": "실패했어요 (RuntimeError)",
                                "post": None, "started_at": time.time(), "updated_at": time.time(), "done_at": time.time()}, sub="results")
    res = await bot.web.dispatch("GET", f"/dashboard/api/actions?ids={body['id']}", h)
    got = json.loads(res[1])["actions"][0]
    assert got["status"] == "ok" and got["text"].startswith("❌ 블로그 프로그램 점검") and "메모리" in got["text"]
    assert "서비스 dealbot" in got["text"]


async def test_blogauto_down_gives_the_dealbot_half_right_away(bot: DealBot, settings: Settings, tmp_path: Path,  # noqa: F811
                                                               cmds: list) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, age=3600)
    link(bot, "threads")
    status, body = await act(bot, "blog.server_check", {})
    assert status == 200 and body["status"] == "ok" and body["sheet"] is True, body
    rows = body["text"].split("\n")
    assert rows[0].startswith("❌ 블로그 프로그램") and "응답이 없어요" in rows[0]
    assert any("메모리" in x for x in rows) and any("스레드 연결" in x for x in rows) and SECRET not in body["text"]
    blogauto_status(settings, actions=["publish"])  # 예전 Blog-Auto: 이 점검을 모름
    srv = srvcheck.Cooldown()
    bot.dashboard.api.desk.srv_cool = srv
    status, body = await act(bot, "blog.server_check", {})
    assert status == 200 and "업데이트" in body["text"].split("\n")[0]


async def test_button_is_in_the_connect_tab(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    conn = (await state(bot))["connect"]
    assert conn["check"]["action"] == "blog.server_check" and conn["check"]["label"] == "서버 점검"
    assert conn["check"]["args"] == {} and conn["check"]["confirm"] is False
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "row('서버 점검'" in js and "exec(c.check" in js and "server_check" in js
