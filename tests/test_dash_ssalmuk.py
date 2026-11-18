"""이벤트 참여 (쌀먹 — 돈좀요 이벤트 자동 응모) 연동 (주인 11/14 "쌀먹을 Sns-board 에 합치자"). 명세: ssalmuk docs/SNSBOARD.md.

1) 쌀먹 서버가 부르는 셋(push·commands·ack): 로그인 없이 · X-Ssalmuk-Key 만 (없음·틀림 401 bad_key · 상수 시간 비교) · 512KB · v/app 확인.
2) 화면 넷(state·act·key·rotate): 로그인 아래 · POST 는 같은 사이트 + X-Dash-Token.
3) 왕복: 화면 act → commands → (쌀먹 처리) ack → 결과 · done 은 받아 둔 상태에 먼저 · run 겹치면 하나만.
4) '연결 끊김': 받은 적 없음 또는 20분 넘게 push 없음.
5) 쌀먹 sync.js 흐름 그대로 (test/e2e-sync.js 를 파이썬으로): push → run → 실행 중 → 끝 → done → profile → ack 결과 셋.
6) ssalmuk.js: 할 일 탭 카드 · 화면 · 바깥 주소·innerHTML 없음 · 보이는 이름은 '이벤트 참여'.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import api as api_mod
from dealbot.dashboard import ssalmuk

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
B = ssalmuk.BASE


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "no-such.env")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def machine_h(key: str | None, peer: str = "198.51.100.7") -> dict[str, str]:
    """쌀먹 서버 (Node fetch): 쿠키·Origin·Sec-Fetch 없음."""
    h = {":peer": peer, "content-type": "application/json", "user-agent": "ssalmuk-sync/1"}
    if key is not None:
        h["x-ssalmuk-key"] = key
    return h


def sample(**kw: Any) -> dict[str, Any]:
    todo = [
        {"id": "a1b2c3d4e5f6", "url": "https://donzomyo.kr/events/event-1", "title": "오늘 마감 퀴즈", "prize": "커피", "deadline": "2026년 11월 14일",
         "today": True, "group": "today", "mode": "manual", "status": "planned", "chip": {"text": "직접 참여", "tone": "info"},
         "actions": ["댓글"], "steps": ["게시물에 댓글"], "detail": "", "comment": "기대돼요!", "links": [{"label": "인스타그램", "href": "https://instagram.com/p/x"}],
         "done": False},
        {"id": "b1b2c3d4e5f6", "url": "https://donzomyo.kr/events/event-2", "title": "폼 + 팔로우", "prize": "", "deadline": "2026년 11월 20일",
         "today": False, "group": "assist", "mode": "assist", "status": "planned", "chip": {"text": "폼 + SNS", "tone": "hot"},
         "actions": ["팔로우"], "steps": [], "detail": "", "comment": "", "links": [], "done": False},
    ]
    st = {"v": 1, "app": "ssalmuk", "sentAt": "2026-11-14T00:10:00Z", "running": None, "nextRun": "내일 07:23", "runTimes": ["07:23", "18:23"],
          "lastRun": {"label": "07:23 정기", "started": "2026-11-13T22:23:00Z", "finished": "2026-11-13T22:30:00Z", "ok": True, "log": "run-1.log"},
          "checkedAt": "2026-11-13T22:30:00Z", "counts": {"auto": 2, "left": 2, "today": 1, "applied": 1}, "todo": todo,
          "applied": [{"url": "https://donzomyo.kr/events/event-0", "title": "자동 응모 된 것", "at": "2026-11-13T22:29:00Z", "status": "success", "detail": "제출 완료"}],
          "profile": {"name": "홍길동", "phone": "010-1234-5678", "email": "a@example.com", "demographics.age": "30대"}, "profileSet": True}
    st.update(kw)
    return st


async def mpost(bot: DealBot, path: str, body: Any, key: str | None, **extra: str) -> tuple[int, Any]:
    raw = json.dumps(body).encode()
    h = dict(machine_h(key), **{"content-length": str(len(raw))}, **extra)
    res = await bot.web.dispatch("POST", path, h, raw)
    return res[0], json.loads(res[1]) if isinstance(res[1], str) and res[1].startswith("{") else res[1]


async def mget(bot: DealBot, path: str, key: str | None) -> tuple[int, Any]:
    res = await bot.web.dispatch("GET", path, machine_h(key))
    return res[0], json.loads(res[1]) if isinstance(res[1], str) and res[1].startswith("{") else res[1]


def desk(bot: DealBot) -> ssalmuk.Ssalmuk:
    return bot.dashboard.api.ssalmuk  # type: ignore[union-attr]


# ---------------------------------------------------------------- 1) 쌀먹 서버 쪽 — 키만
async def test_machine_endpoints_need_key_not_login(bot: DealBot) -> None:
    # 키를 아직 안 만들었으면 무엇이 와도 거절 (빈 키 == 빈 키 로 열리지 않게)
    assert await mpost(bot, ssalmuk.PUSH_PATH, sample(), None) == (401, {"code": "bad_key", "text": "연결 키가 맞지 않아요"})
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), ""))[0] == 401
    key = desk(bot).key()
    assert re.fullmatch(r"[0-9a-f]{64}", key)  # 32바이트 무작위
    for k in (None, "", "wrong", key[:-1], key + "0", key.upper()):
        status, body = await mpost(bot, ssalmuk.PUSH_PATH, sample(), k)
        assert status == 401 and body["code"] == "bad_key", k
        assert (await mget(bot, ssalmuk.COMMANDS_PATH, k))[0] == 401
        assert (await mpost(bot, ssalmuk.ACK_PATH, {"ids": []}, k))[0] == 401
    assert desk(bot).view()["state"] is None  # 틀린 키로는 아무것도 저장되지 않음
    # 맞는 키: 로그인·CSRF·Origin 없이 셋 다
    assert await mpost(bot, ssalmuk.PUSH_PATH, sample(), key) == (200, {"ok": True})
    assert await mget(bot, ssalmuk.COMMANDS_PATH, key) == (200, {"commands": []})
    assert await mpost(bot, ssalmuk.ACK_PATH, {"ids": [], "results": {}}, key) == (200, {"ok": True, "removed": 0})
    # 모양이 틀린 상태는 400
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(v=2), key))[0] == 400
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(app="other"), key))[1]["code"] == "bad_body"
    assert (await mpost(bot, ssalmuk.ACK_PATH, {"ids": "x"}, key))[0] == 400


async def test_key_compare_is_constant_time(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = ssalmuk.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(ssalmuk.hmac, "compare_digest", spy)
    key = desk(bot).key()
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), "short"))[0] == 401
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), key))[0] == 200
    # 해시(32바이트)끼리 — 길이가 달라도 같은 비교
    assert len(calls) == 2 and all(len(a) == len(b) == 32 for a, b in calls)
    assert not ssalmuk.key_ok(None, key) and not ssalmuk.key_ok(key, None) and not ssalmuk.key_ok("", "") and ssalmuk.key_ok(key, key)


async def test_body_cap_512kb(bot: DealBot) -> None:
    key = desk(bot).key()
    big = sample(pad="x" * (ssalmuk.BODY_MAX + 10))
    status, body = await mpost(bot, ssalmuk.PUSH_PATH, big, key)
    assert status == 413 and body["code"] == "too_big"
    ok = sample(pad="x" * (ssalmuk.BODY_MAX - 6000))
    assert (await mpost(bot, ssalmuk.PUSH_PATH, ok, key))[0] == 200
    # 길이를 안 밝힌 POST 는 읽지 않음
    res = await bot.web.dispatch("POST", ssalmuk.PUSH_PATH, machine_h(key), None)
    assert res[0] == 411


async def test_only_five_paths_open_without_login(bot: DealBot) -> None:
    pub = set(api_mod.DashApi.public_paths())
    assert {p for p in pub if p.startswith(B)} == {ssalmuk.PUSH_PATH, ssalmuk.COMMANDS_PATH, ssalmuk.ACK_PATH, ssalmuk.SEARCH_PATH, ssalmuk.LLM_PATH}
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    for path in (ssalmuk.STATE_PATH, ssalmuk.KEY_PATH):
        res = await bot.web.dispatch("GET", path, anon)
        assert res[0] == 401 and json.loads(res[1])["code"] == "login", path
    key = desk(bot).key()
    for path in (ssalmuk.ACT_PATH, ssalmuk.ROTATE_PATH):
        # 연결 키를 넣어도 화면 쪽은 로그인 필요
        status, body = await mpost(bot, path, {"op": "run", "args": {}}, key)
        assert status == 401 and body["code"] == "login", path
    assert desk(bot).view()["pending"] == 0 and desk(bot).key() == key


async def test_machine_rate_limit(bot: DealBot) -> None:
    key = desk(bot).key()
    codes = [(await mget(bot, ssalmuk.COMMANDS_PATH, key))[0] for _ in range(121)]
    assert codes[:120] == [200] * 120 and codes[120] == 429


# ---------------------------------------------------------------- 2) 화면 — 로그인 + 같은 사이트 + X-Dash-Token
async def test_screen_needs_csrf_and_same_site(bot: DealBot) -> None:
    h = session_headers(bot)
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {}}, session_headers(bot, csrf=False)))[0] == 403
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {}}, dict(h, origin="https://evil.example")))[0] == 403
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {}}, dict(h, **{"sec-fetch-site": "cross-site"})))[0] == 403
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "nuke", "args": {}}, h))[:2] == (400, {"status": "error", "code": "bad_op", "text": "모르는 명령이에요"})
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "done", "args": {"url": "javascript:alert(1)"}}, h))[0] == 400
    assert (await post_json(bot, ssalmuk.ACT_PATH, {"op": "profile", "args": {"profile": {"evil": 1}}}, h))[0] == 400
    status, v, headers = await get_json(bot, ssalmuk.STATE_PATH, h)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert v == {"state": None, "receivedAt": None, "pending": 0, "stale": True, "results": [],
                 "search_proxy": {"keysrc": None, "today_count": 0, "keys": {}}, "alerts": [],
                 "llm_proxy": {"gemini": {"today": 0, "cap": 40}, "claude": {"today": 0, "cap": 150, "sonnet": 0, "sonnet_cap": 50}}}  # alerts: 주인 11/14 오류 검수 (없으면 [])


async def test_key_shown_only_logged_in_and_rotate(bot: DealBot) -> None:
    h = session_headers(bot)
    status, k1, headers = await get_json(bot, ssalmuk.KEY_PATH, h)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert re.fullmatch(r"[0-9a-f]{64}", k1["key"]) and k1["url"] == "https://52-79-136-64.sslip.io" and k1["at"]
    assert (await get_json(bot, ssalmuk.KEY_PATH, h))[1]["key"] == k1["key"]  # 열 때마다 바뀌지 않음
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), k1["key"]))[0] == 200
    assert (await post_json(bot, ssalmuk.ROTATE_PATH, {}, session_headers(bot, csrf=False)))[0] == 403
    status, k2, _ = await post_json(bot, ssalmuk.ROTATE_PATH, {}, h)
    assert status == 200 and k2["key"] != k1["key"]
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), k1["key"]))[0] == 401  # 예전 키는 바로 안 받음
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), k2["key"]))[0] == 200
    # 기록에 키·내 정보는 남지 않음
    rows = bot.db._conn.execute("SELECT action, args_redacted FROM dash_audit WHERE action LIKE 'ssalmuk.%'").fetchall()
    assert [r[0] for r in rows] == ["ssalmuk.rotate"] and all(k1["key"] not in r[1] and k2["key"] not in r[1] for r in rows)


# ---------------------------------------------------------------- 3) 왕복
async def test_push_state_act_commands_ack_round_trip(bot: DealBot) -> None:
    key = desk(bot).key()
    h = session_headers(bot)
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(), key))[0] == 200
    _s, v, _h = await get_json(bot, ssalmuk.STATE_PATH, h)
    assert v["state"]["profile"]["name"] == "홍길동" and v["receivedAt"].endswith("Z") and v["stale"] is False and v["pending"] == 0

    r1 = (await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {"x": 1}}, h))[1]
    r2 = (await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {}}, h))[1]
    assert r1["ok"] and r2 == {"status": "ok", "ok": True, "id": r1["id"], "dup": True}  # 겹치면 하나만
    url = "https://donzomyo.kr/events/event-1"
    await post_json(bot, ssalmuk.ACT_PATH, {"op": "done", "args": {"url": url, "done": False}}, h)
    d = (await post_json(bot, ssalmuk.ACT_PATH, {"op": "done", "args": {"url": url, "done": True}}, h))[1]
    p = (await post_json(bot, ssalmuk.ACT_PATH, {"op": "profile", "args": {"profile": {
        "name": "시험이름", "demographics.age": "40대", "commentTemplates": "한 줄\n두 줄", "quizAnswers": {"가격": "1,000원"}, "agreeOptional": 1, "x": "버림"}}}, h))[1]
    # done 은 받아 둔 상태에 먼저 (남은 할 일·오늘 마감 숫자도)
    _s, v, _h = await get_json(bot, ssalmuk.STATE_PATH, h)
    t = next(x for x in v["state"]["todo"] if x["url"] == url)
    assert t["done"] is True and v["state"]["counts"]["left"] == 1 and v["state"]["counts"]["today"] == 0 and v["pending"] == 3

    status, cmds = await mget(bot, ssalmuk.COMMANDS_PATH, key)
    assert status == 200
    got = cmds["commands"]
    assert [c["op"] for c in got] == ["run", "done", "profile"]  # 오래된 순 · 같은 이벤트 done 은 마지막 것만
    assert got[0]["args"] == {} and got[1]["args"] == {"url": url, "done": True} and got[1]["id"] == d["id"]
    assert got[2]["id"] == p["id"] and got[2]["args"] == {"profile": {"name": "시험이름", "demographics.age": "40대",
                                                                     "commentTemplates": "한 줄\n두 줄", "quizAnswers": {"가격": "1,000원"}, "agreeOptional": True}}
    assert all(set(c) == {"id", "op", "args", "at"} and c["at"].endswith("Z") for c in got)

    results = {got[0]["id"]: "ok", got[1]["id"]: "ok", got[2]["id"]: "이미 실행 중이에요"}
    assert await mpost(bot, ssalmuk.ACK_PATH, {"ids": [c["id"] for c in got], "results": results}, key) == (200, {"ok": True, "removed": 3})
    assert await mget(bot, ssalmuk.COMMANDS_PATH, key) == (200, {"commands": []})
    _s, v, _h = await get_json(bot, ssalmuk.STATE_PATH, h)
    assert v["pending"] == 0 and [(r["op"], r["result"]) for r in v["results"]] == [("run", "ok"), ("done", "ok"), ("profile", "이미 실행 중이에요")]
    # 쌀먹이 다시 보내면 그것으로 덮어씀
    assert (await mpost(bot, ssalmuk.PUSH_PATH, sample(running={"label": "Sns-board", "started": "2026-11-14T00:11:00Z"}), key))[0] == 200
    _s, v, _h = await get_json(bot, ssalmuk.STATE_PATH, h)
    assert v["state"]["running"]["label"] == "Sns-board" and v["state"]["todo"][0]["done"] is False
    # 기록엔 명령 이름만 (내 정보 값은 없음)
    rows = bot.db._conn.execute("SELECT action, args_redacted FROM dash_audit WHERE action LIKE 'ssalmuk.%'").fetchall()
    assert [r[0] for r in rows] == ["ssalmuk.run", "ssalmuk.run", "ssalmuk.done", "ssalmuk.done", "ssalmuk.profile"]
    assert not any("시험이름" in r[1] or "donzomyo" in r[1] for r in rows)


class FakeDb:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v


def test_commands_keep_a_day_and_cap() -> None:
    now = [1_794_500_000.0]
    s = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    for i in range(ssalmuk.CMD_MAX):
        assert s.act({"op": "done", "args": {"url": f"https://donzomyo.kr/e/{i}", "done": True}})[0] == 200
    assert s.act({"op": "run"})[0] == 429  # 쌀먹이 안 가져가면 더 쌓지 않음
    assert len(s.commands()["commands"]) == ssalmuk.CMD_LIST
    now[0] += ssalmuk.CMD_KEEP + 1
    assert s.commands() == {"commands": []} and s.view()["pending"] == 0  # 하루 지나면 버림


# ---------------------------------------------------------------- 4) 연결 끊김
def test_stale_after_twenty_minutes() -> None:
    now = [1_794_500_000.0]
    s = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    assert s.view()["stale"] is True  # 받은 적 없음
    s.push(sample())
    assert s.view()["stale"] is False
    now[0] += 19 * 60
    assert s.view()["stale"] is False
    now[0] += 2 * 60
    assert s.view()["stale"] is True
    s.push(sample())
    assert s.view()["stale"] is False
    assert ssalmuk.stale(None, 0) and not ssalmuk.stale(100.0, 100.0 + 1200) and ssalmuk.stale(100.0, 100.0 + 1201)


# ---------------------------------------------------------------- 5) 쌀먹 sync.js 흐름 (test/e2e-sync.js 를 파이썬으로)
async def test_sync_js_flow_mirror(bot: DealBot) -> None:
    """쌀먹 서버가 하는 일: 연결되면 push → 1분마다 commands → 명령 처리 → ack → 바로 push(force)."""
    key = desk(bot).key()
    h = session_headers(bot)
    prof = {"name": "홍길동", "phone": "010-1234-5678", "demographics.age": "30대", "commentTemplates": []}
    done: dict[str, bool] = {}
    running: dict[str, Any] = {"now": None, "auto": 0}

    def build() -> dict[str, Any]:
        todo = [dict(t, done=done.get(t["url"], False)) for t in sample()["todo"]]
        left = [t for t in todo if not t["done"]]
        return sample(running=running["now"], todo=todo, profile=dict(prof),
                      counts={"auto": running["auto"], "left": len(left), "today": sum(t["today"] for t in left), "applied": running["auto"]})

    def apply(c: dict[str, Any]) -> str:
        if c["op"] == "run":
            running["now"] = {"label": "Sns-board", "started": c["at"]}
            return "ok"
        if c["op"] == "done":
            done[c["args"]["url"]] = c["args"]["done"]
            return "ok"
        for k, v in c["args"]["profile"].items():
            prof[k] = v.split("\n") if k == "commentTemplates" else v
        return "ok"

    async def poll() -> None:
        cmds = (await mget(bot, ssalmuk.COMMANDS_PATH, key))[1]["commands"]
        if cmds:
            results = {c["id"]: apply(c) for c in cmds}
            assert (await mpost(bot, ssalmuk.ACK_PATH, {"ids": [c["id"] for c in cmds], "results": results}, key))[0] == 200
        assert (await mpost(bot, ssalmuk.PUSH_PATH, build(), key))[0] == 200

    async def screen() -> dict[str, Any]:
        return (await get_json(bot, ssalmuk.STATE_PATH, h))[1]

    checks: dict[str, bool] = {}
    await poll()
    checks["연결되면 바로 상태를 보냄"] = (await screen())["state"]["profileSet"] is True
    checks["키가 틀리면 거절 (401)"] = (await mpost(bot, ssalmuk.PUSH_PATH, {}, "wrong"))[0] == 401
    await post_json(bot, ssalmuk.ACT_PATH, {"op": "run", "args": {}}, h)
    await poll()
    checks["지금 확인하기 → 실행 중 표시"] = bool((await screen())["state"]["running"])
    running.update(now=None, auto=2)
    await poll()
    st = (await screen())["state"]
    checks["실행 끝나면 결과가 옴"] = st["running"] is None and st["counts"]["auto"] == 2
    first = st["todo"][0]
    await post_json(bot, ssalmuk.ACT_PATH, {"op": "done", "args": {"url": first["url"], "done": True}}, h)
    await poll()
    st = (await screen())["state"]
    checks["완료 체크 → 쌀먹에 저장"] = done[first["url"]] is True and st["counts"]["left"] == len(st["todo"]) - 1
    await post_json(bot, ssalmuk.ACT_PATH, {"op": "profile", "args": {"profile": {
        "name": "시험이름", "demographics.age": "40대", "commentTemplates": "한 줄\n두 줄", "quizAnswers": {"가격": "1,000원"}}}}, h)
    await poll()
    v = await screen()
    checks["내 정보 수정 → 반영"] = v["state"]["profile"]["name"] == "시험이름" and prof["commentTemplates"] == ["한 줄", "두 줄"] and prof["phone"]
    checks["처리 결과 알림(ack)"] = len(v["results"]) == 3 and all(r["result"] == "ok" for r in v["results"]) and v["pending"] == 0
    assert all(checks.values()), checks


# ---------------------------------------------------------------- 6) ssalmuk.js
def test_ssalmuk_js_is_local_safe_and_named_for_owner() -> None:
    js = (STATIC / "ssalmuk.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "ssalmuk.js")], capture_output=True).returncode == 0
    code = re.sub(r"/\*.*?\*/|  //[^\n]*", "", js, flags=re.S)
    assert "innerHTML" not in code and "eval(" not in code
    assert set(re.findall(r"https?://[^\s'\"`)]+", code)) <= {"https://"}  # 바깥 주소 없음 (https 확인 정규식만)
    assert "/dashboard/api/ssalmuk/" in code and "X-Dash-Token" in code
    # 보이는 이름은 '이벤트 참여' (주인 11/14) — 화면 글에 '쌀먹' 없음 (주석만)
    assert "쌀먹" not in code and "이벤트 참여" in code and "연결 끊김" in code and "새 키 만들기" in code
    for label in ("자동 응모", "남은 할 일", "오늘 마감", "지금 확인하기", "이름 복사", "숫자만", "자동 응모 내역", "내 정보", "연결 키"):
        assert label in code or label.replace(" 복사", "") in code, label


SHIM = r"""
const [src, view] = process.argv.slice(1);
const fs = require('fs');
function mk(tag) {
  const n = {nodeType: 1, tagName: tag.toUpperCase(), children: [], attrs: {}, style: {}, listeners: {}, parentNode: null, hidden: false, className: '', _text: '',
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; },
    removeAttribute(k) { delete this.attrs[k]; }, addEventListener(t, f) { this.listeners[t] = f; },
    append(...xs) { for (const x of xs) { const c = typeof x === 'string' ? Object.assign(mk('#text'), {_text: x}) : x; if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); } },
    prepend(x) { if (x.parentNode) x.remove(); x.parentNode = this; this.children.unshift(x); },
    after(x) { if (x.parentNode) x.remove(); const p = this.parentNode; x.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, x); },
    remove() { const p = this.parentNode; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; } },
    replaceChildren(...xs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this.append(...xs); },
    contains(x) { while (x) { if (x === this) return true; x = x.parentNode; } return false; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; },
    set textContent(v) { this._text = String(v); this.children = []; }, get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }};
  return n;
}
const main = mk('main'), ttl = mk('h1'), toast = mk('div'), head = mk('head');
main.id = 'main'; ttl.textContent = '할 일 3개';
const ids = {main, ttl, toast, boot: {textContent: JSON.stringify({me: {csrf: 'tok'}, ssalmuk: {show: !process.env.EV_HIDE}})}};
const urgent = mk('div'); urgent.className = 'bn bad'; main.append(urgent);
const rtp = mk('section'); rtp.id = 'rtp'; main.append(rtp);
const other = mk('article'); other.className = 'itm'; main.append(other);
ids.rtp = rtp;
global.document = {readyState: 'complete', head, visibilityState: 'visible', activeElement: null, createElement: mk,
  getElementById: id => ids[id] || (head.children.find(c => c.attrs.id === id || c.id === id) || null), addEventListener() {}};
global.location = {hash: '#/todo'};
global.window = global; global.addEventListener = () => {}; global.scrollTo = () => {}; global.MutationObserver = class { observe() {} };
const posts = [];
global.fetch = async (u, o) => { if (o && o.method === 'POST') posts.push([u, o.headers['X-Dash-Token'], JSON.parse(o.body)]);
  return {status: 200, json: async () => (o && o.method === 'POST' ? {ok: true, id: 'c1'} : JSON.parse(view))}; };
global.setTimeout = () => 0; global.clearTimeout = () => {};
eval(fs.readFileSync(src, 'utf8'));
const text = n => n.textContent;
(async () => {
  await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r));
  const out = {order: main.children.map(c => c.attrs.id || c.id || c.className), card: text(main.children[3])};
  location.hash = '#/todo/ev'; window.SBEvent.mount();
  await new Promise(r => setImmediate(r));
  const scr = main.children[0];
  out.scr = text(scr); const cz = n => (n.className === 'z' ? 1 : 0) + n.children.reduce((a, c) => a + cz(c), 0); out.zero = cz(scr); out.ev = 'data-ev' in main.attrs; out.title = ttl.textContent;
  const find = (n, f) => f(n) ? n : n.children.map(c => find(c, f)).find(Boolean);
  const runb = find(scr, n => n.tagName === 'BUTTON' && text(n) === '지금 확인하기');
  await runb.listeners.click({currentTarget: runb});
  const doneb = find(scr, n => n.tagName === 'BUTTON' && text(n) === '완료');
  await doneb.listeners.click({currentTarget: doneb});
  const srcb = find(main.children[0], n => n.tagName === 'BUTTON' && text(n).startsWith('수집 출처'));
  if (srcb) { srcb.listeners.click({currentTarget: srcb}); out.src = text(main.children[0]); }
  const skb = find(main.children[0], n => n.tagName === 'BUTTON' && text(n).startsWith('자동 안 되는'));
  if (skb) { out.skipBtn = text(skb); skb.listeners.click({currentTarget: skb}); out.skipOpen = text(main.children[0]); }
  out.posts = posts;
  const sg = find(main.children[0], n => n.tagName === 'DIV' && n.attrs.role === 'group'); out.after = sg ? text(sg) : null;
  const pf = find(main.children[0], n => n.tagName === 'BUTTON' && text(n) === '내 정보');
  pf.listeners.click({currentTarget: pf}); out.prof = text(main.children[0]);
  console.log(JSON.stringify(out));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_card_and_screen(tmp_path: Path) -> None:
    db = FakeDb()
    s = ssalmuk.Ssalmuk(db)
    s.push(sample())
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(s.view(), ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # 급한 알림 → 오늘 할 일 칸 → 할 일 카드 → 이벤트 참여 카드 (맨 아래 — 주인 11/14)
    assert got["order"] == ["bn bad", "rtp", "itm", "evc"]
    assert got["card"].startswith("이벤트 참여열기 ›") and "남은 할 일 2" in got["card"] and "오늘 마감 1" in got["card"]
    assert "오늘 마감 · 오늘 마감 퀴즈돈좀요에서 열기" in got["card"] and "연결 끊김" not in got["card"]
    # 링크가 빈 할 일(RSS 만 읽음 — 11/14)도 '돈좀요에서 열기' · 자동 응모가 0 이면 흐리게
    assert got["scr"].count("돈좀요에서 열기") == 2 and got["zero"] == 0
    assert got["ev"] and got["title"] == "이벤트 참여"
    for t in ("자동 응모2", "남은 할 일2", "오늘 마감1", "지금 확인하기", "이름 복사", "전화번호 복사", "숫자만 복사", "이메일 복사",
              "오늘 마감 1", "폼 + SNS 1", "기대돼요!", "돈좀요", "자동 응모 내역 1", "내 정보", "연결", "매일 07:23, 18:23 자동"):
        assert t in got["scr"], t
    assert got["posts"] == [["/dashboard/api/ssalmuk/act", "tok", {"op": "run", "args": {}}],
                            ["/dashboard/api/ssalmuk/act", "tok", {"op": "done", "args": {"url": "https://donzomyo.kr/events/event-1", "done": True}}]]
    assert got["after"] == "남은 할 일 1완료 1"  # 완료는 누르자마자 화면에


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_card_says_disconnected(tmp_path: Path) -> None:
    now = [1_794_500_000.0]
    s = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    s.push(sample())
    now[0] += 21 * 60
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(s.view(), ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "연결 끊김" in got["card"] and "이벤트 참여 서버 연결 끊김" in got["scr"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_rss_only_state_empty_links_zero_auto(tmp_path: Path) -> None:
    """11/14 돈좀요가 긁기를 막아 RSS 만 — links 가 비고 자동 응모 0 이 보통 (흐린 칸 · 돈좀요에서 열기는 그대로)."""
    s = ssalmuk.Ssalmuk(FakeDb())
    st = sample(counts={"auto": 0, "left": 2, "today": 1, "applied": 0}, applied=[])
    for t in st["todo"]:
        t["links"] = []
    del st["todo"][1]["links"]
    # 11/14 (2): 출처 · 수집 출처 칸 · 네이버폼은 폰에서 (칩은 tone 대로)
    st["todo"][0]["source"] = "돈좀요"
    st["todo"][1]["chip"] = {"text": "네이버폼 — 폰에서", "tone": "hot"}
    st["sources"] = [{"id": "donzomyo", "name": "돈좀요", "status": "ok", "items": 12, "at": "2026-11-13T22:30:00Z"},
                     {"id": "x", "name": "다른 곳", "status": "blocked", "items": 0, "why": "403", "until": "2026-11-14T03:00:00Z"}]
    s.push(st)
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(s.view(), ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["zero"] == 1 and got["scr"].count("돈좀요에서 열기") == 2 and "인스타그램" not in got["scr"]
    assert "아직 자동 응모한 이벤트가 없어요" not in got["scr"]  # 접힌 칸은 열 때만
    assert "커피 · 마감 11/14 · 돈좀요" in got["scr"] and "네이버폼 — 폰에서" in got["scr"] and "수집 출처 2" in got["scr"]
    assert "돈좀요12개 · 확인" in got["src"] and "정상" in got["src"] and "다른 곳0개 · 403 · " in got["src"] and "막힘" in got["src"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_shows_naver_search_line() -> None:
    """11/14 수집 출처 칸 맨 위 회색 한 줄: 네이버 검색 횟수·성공·어느 키·마지막 — 둘 다 없으면 줄 없음."""
    def run(search, proxy) -> str:
        s = ssalmuk.Ssalmuk(FakeDb())
        st = sample()
        st["sources"] = [{"id": "donzomyo", "name": "돈좀요", "status": "ok", "items": 12}]
        if search is not None:
            st["search"] = search
        s.push(st)
        v = s.view()
        v["search_proxy"] = proxy
        out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(v, ensure_ascii=False)],
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)["src"]
    q = {"date": "2026-11-14", "mode": "proxy", "count": 12, "ok": 11, "fail": 1, "lastStatus": 200, "lastAt": "2026-11-14T01:24:00Z"}
    got = run(q, {"keysrc": "blog", "today_count": 12})
    assert "네이버 검색 · 오늘 12회 (성공 11 · 실패 1) · 키: 블로그 서버 · 마지막 200 10:24" in got and "오류" not in got
    assert "돈좀요12개" in got
    assert "· 오류: 네이버 401" in run({**q, "lastError": "네이버 401"}, {"keysrc": "dealbot", "today_count": 12})
    assert "네이버 검색" not in run(None, {"keysrc": None, "today_count": 0})
    assert "네이버 검색 · 오늘 3회 · 키: 딜봇" in run(None, {"keysrc": "dealbot", "today_count": 3})
    # 11/15 네이버 API HUB 로 통한 키
    assert "네이버 검색 · 오늘 3회 · 키: 블로그 서버 (네이버 HUB)" in run(None, {"keysrc": "blog_hub", "today_count": 3})
    assert "키: 딜봇 (네이버 HUB) · 딜봇 키 HUB 200" in run(None, {"keysrc": "dealbot_hub", "today_count": 3, "keys": {"dealbot": {"s": "HUB 200", "at": None}}})


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_auto_apply_first_layout() -> None:
    """11/14 '자동화 되는 애들만': todayApplied·weekApplied·notAutomated 가 오면 카드·화면이 자동 응모 중심 (없으면 예전 화면 — 위 시험들)."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    st = sample(counts={"auto": 2, "left": 1, "today": 0, "applied": 3, "todayApplied": 2, "weekApplied": 9}, notAutomated=41,
                applied=[{"url": "https://suto.example/e/1", "title": "커피 쿠폰 응모", "at": iso(now), "status": "success", "detail": "제출 완료", "source": "슈퍼투데이"},
                         {"url": "https://suto.example/e/2", "title": "샴푸 체험", "at": iso(now - timedelta(minutes=5)), "status": "success", "detail": "", "source": "슈퍼투데이"},
                         {"url": "https://suto.example/e/0", "title": "지난주 응모", "at": iso(now - timedelta(days=3)), "status": "success", "detail": "", "source": "다른 곳"}])
    st["todo"] = [dict(st["todo"][1], title="퀴즈 정답 못 찾음", url="https://suto.example/e/9", status="fail", group="check",
                       chip={"text": "자동 실패", "tone": "stop"}, detail="정답 칸을 못 찾았어요")]
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(st)
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(s.view(), ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    card = got["card"]
    assert "오늘 자동 응모 2 · 이번 주 9 · 확인 필요 1" in card and "커피 쿠폰 응모" in card and "샴푸 체험" in card
    assert "지난주 응모" not in card and "확인 필요 · 퀴즈 정답 못 찾음" in card and "이벤트 열기" in card and "남은 할 일" not in card
    scr = got["scr"]
    for t in ("오늘 자동 응모2", "이번 주9", "확인 필요1", "자동 안 되는 이벤트 41건은 건너뜀", "오늘 응모한 이벤트 2", "슈퍼투데이",
              "확인 필요 1", "정답 칸을 못 찾았어요", "자동 응모 내역 3", "내 정보", "연결"):
        assert t in scr, t
    assert "지난주 응모" not in scr and "이름 복사" not in scr and "남은 할 일" not in scr and got["after"] is None
    assert "이름 복사" in got["prof"] and "숫자만 복사" in got["prof"]  # 복사 단추는 내 정보 안으로
    assert "skipBtn" not in got  # skipReasons 없으면 단추 없이 한 줄


# 11/15 '오늘 응모한 이벤트' — 홈 화면 앱(navigator.standalone) 흉내 + 오늘 목록의 줄·단추를 꺼냄
SHIM_TODAY = SHIM.replace("eval(fs.readFileSync", "Object.defineProperty(global, 'navigator', {value: {standalone: process.env.SA === '1'}, configurable: true});\neval(fs.readFileSync").replace(
    "  out.posts = posts;", """  const all = (n, f, a = []) => { if (f(n)) a.push(n); n.children.forEach(c => all(c, f, a)); return a; };
  const box = find(main.children[0], n => /\\bevt\\b/.test(n.className || ''));
  out.rows = box ? box.children.map(r => ({text: text(r), a: all(r, n => n.tagName === 'A').map(n => [text(n), n.attrs.href, n.attrs.rel])})) : null;
  out.tags = all(main, n => !/^(MAIN|H1|H3|DIV|SECTION|ARTICLE|P|SPAN|A|BUTTON|B|UL|OL|LI|#TEXT|INPUT|SELECT|OPTION|TEXTAREA|LABEL|STYLE)$/.test(n.tagName)).map(n => n.tagName);
  out.posts = posts;""")


def _today_view(applied: list[dict[str, Any]], **counts: Any) -> dict[str, Any]:
    c = {"auto": 2, "left": 0, "today": 0, "applied": len(applied), "todayApplied": len(applied), "weekApplied": 9}
    c.update(counts)
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(sample(counts=c, notAutomated=15, applied=applied, todo=[sample()["todo"][0]]))
    return s.view()


def _run_today(view: dict[str, Any], standalone: bool = False) -> dict[str, Any]:
    import os

    out = subprocess.run([NODE, "-e", SHIM_TODAY, str(STATIC / "ssalmuk.js"), json.dumps(view, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30, env=dict(os.environ, SA="1" if standalone else "0"))
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_today_applied_list_prize_winners_deadline() -> None:
    """11/15 계약(applied[] prize·winners·deadline·kind): 오늘 응모한 이벤트 = 이름·경품·당첨 인원·마감(가까운 순)·폼 단추 · 없는 칸은 숨김 · 글자로만 · https 만 링크."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    iso = lambda m: (now - timedelta(seconds=m)).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    applied = [
        {"url": "https://docs.google.com/forms/d/e/abc/viewform", "title": "늦은 마감 설문", "at": iso(1), "status": "success", "source": "콘테스트코리아", "detail": "제출 완료",
         "prize": "모바일 상품권 1만원", "winners": "추첨 100명", "deadline": "2026년 12월 30일", "kind": "설문"},
        {"url": "https://event.example.go.kr/apply", "title": "가까운 마감 <img src=x onerror=alert(1)>", "at": iso(2), "status": "success", "source": "", "detail": "",
         "prize": "<b>커피</b>", "winners": "전원", "deadline": "2026-12-01", "kind": "SNS 조건 못 함"},
        {"url": "https://suto.example/e/3", "title": "옛 꼴 (새 칸 없음)", "at": iso(3), "status": "duplicate", "source": "슈퍼투데이", "detail": ""},
        {"url": "javascript:alert(1)", "title": "주소가 이상함", "at": iso(4), "status": "success", "source": "", "detail": "", "prize": "치킨", "deadline": "2026. 12. 15"},
        {"url": "http://plain.example/x", "title": "http 주소", "at": iso(5), "status": "success", "source": "", "detail": "", "deadline": "상시", "winners": 7, "prize": {"x": 1}},
    ]
    got = _run_today(_today_view(applied, skipReasons={"SNS 조건": 12, "네이버폼(폰에서)": 3}))
    rows = got["rows"]
    assert "오늘 응모한 이벤트 5" in got["scr"] and len(rows) == 5 and got["tags"] == []  # 태그 문자열은 글자 그대로 (IMG·B 요소 없음)
    # 마감 가까운 순: 12/1 → 12/15 → 12/30 → 마감 못 읽는 것(받은 순)
    assert [r["text"].split(" ")[0] for r in rows] == ["가까운", "주소가", "늦은", "옛", "http"]
    assert rows[0]["text"].startswith("가까운 마감 <img src=x onerror=alert(1)> (SNS 단계는 직접)<b>커피</b> · 전원 · 마감 12/1")
    assert "SNS 조건 못 함" not in rows[0]["text"] and rows[0]["a"] == [["열기 ↗", "https://event.example.go.kr/apply", "noopener noreferrer"]]
    assert rows[2]["text"].startswith("늦은 마감 설문모바일 상품권 1만원 · 추첨 100명 · 마감 12/30 · 설문 · ") and "콘테스트코리아" in rows[2]["text"]
    assert "SNS 단계" not in rows[2]["text"] and rows[2]["a"] == [["폼 열기 ↗", "https://docs.google.com/forms/d/e/abc/viewform", "noopener noreferrer"]]
    # 옛 데이터: 새 칸이 없으면 그 칸만 숨김 (빈 '·'·'마감 -' 없음)
    assert rows[3]["text"].startswith("옛 꼴 (새 칸 없음)이미 응모 · ") and "마감" not in rows[3]["text"] and " ·  · " not in rows[3]["text"]
    assert rows[1]["a"] == [] and "치킨 · 마감 12/15" in rows[1]["text"]  # https 가 아니면 단추 없음
    assert rows[4]["a"] == [] and "마감 상시" in rows[4]["text"] and "[object" not in rows[4]["text"] and "7 ·" not in rows[4]["text"]  # 글자가 아닌 칸은 버림
    # 빠진 것 한 줄 (할 일 탭 카드)
    assert "빠진 것 15 · SNS 조건 12 · 네이버폼(폰에서) 3" in got["card"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_today_applied_opens_safari_in_home_screen_app() -> None:
    """홈 화면 앱(navigator.standalone)에서 폼 단추는 사파리로 (routine.js sf 틀) · skipReasons 가 없으면 카드에 '빠진 것' 줄 없음."""
    from datetime import UTC, datetime

    applied = [{"url": "https://forms.gle/abc", "title": "폼", "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "success", "source": "", "detail": ""}]
    got = _run_today(_today_view(applied), standalone=True)
    assert got["rows"][0]["a"] == [["폼 열기 ↗", "x-safari-https://forms.gle/abc", "noopener noreferrer"]]
    assert got["rows"][0]["text"].startswith("폼") and "빠진 것" not in got["card"]
    assert _run_today(_today_view(applied))["rows"][0]["a"][0][1] == "https://forms.gle/abc"


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_skip_reasons_breakdown() -> None:
    """counts.skipReasons: 접힌 줄에 위 2개, 누르면 많은 순 목록 · 라벨은 글자로만 (태그 문자열 그대로)."""
    why = {"로그인 필요": 40, "SNS 참여 필요": 60, "마감 지남": 3, "<b>x</b>": 5, "빈것": 0}
    st = sample(counts={"auto": 2, "left": 1, "today": 0, "applied": 3, "todayApplied": 2, "weekApplied": 9, "skipReasons": why}, notAutomated=146,
                skipExamples={"SNS 참여 필요": ["예시 하나", "<i>둘</i>", "셋", "넷은 안 보임"]})
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(st)
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(s.view(), ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["skipBtn"].startswith("자동 안 되는 이벤트 146건은 건너뜀 · SNS 참여 필요 60 · 로그인 필요 40")
    assert "마감 지남" not in got["skipBtn"]
    o = got["skipOpen"]
    assert "SNS 참여 필요 · 60건" in o and "<b>x</b> · 5건" in o and "마감 지남 · 3건" in o and "빈것" not in o
    assert "예시 하나 · <i>둘</i> · 셋" in o and "넷은" not in o
    assert o.index("SNS 참여 필요 · 60건") < o.index("로그인 필요 · 40건") < o.index("<b>x</b> · 5건") < o.index("마감 지남 · 3건")


# ---------------------------------------------------------------- 7) 네이버 검색 대신 해 주기 (딜봇 키 — 쌀먹 서버엔 키를 옮기지 않음)
SEARCH = ssalmuk.SEARCH_PATH
NAVER_ID, NAVER_SECRET = "test-naver-id-xyz", "test-naver-secret-abc"


class Seen(list):  # type: ignore[type-arg]
    """예전 주소·카카오로 간 요청 (seen) + 네이버 API HUB 로 간 요청 (seen.hub)."""

    hub: list[Any]


def hub_401(req: Any) -> Any:
    import httpx

    return httpx.Response(401, json={"errorMessage": "Authentication Failed"})


def naver(bot: DealBot, handler: Any, *, keyed: bool = True, kakao: str | None = None, hub: Any = hub_401) -> Seen:
    """hub = HUB 주소(naverapihub.apigw.ntruss.com)로 온 요청의 답 — 기본은 401 (예전 개발자센터 키: 예전 주소로 넘어감).
    hub=None 이면 HUB 요청도 handler 로 (seen 에도 들어감)."""
    import httpx

    seen = Seen()
    seen.hub = []

    def h(req: Any) -> Any:
        if hub is not None and req.url.host == "naverapihub.apigw.ntruss.com":
            seen.hub.append(req)
            return hub(req)
        seen.append(req)
        return handler(req)

    bot.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    s = bot.settings.secrets
    s.naver_client_id, s.naver_client_secret = (NAVER_ID, NAVER_SECRET) if keyed else (None, None)
    s.kakao_rest_api_key = kakao  # 서버 환경에 카카오 키가 있어도 시험은 정해 둔 대로
    return seen


def ok_blog(req: Any) -> Any:
    import httpx

    return httpx.Response(200, json={"total": 42, "items": [
        {"title": "<b>돈좀요</b> 이벤트 &quot;공지&quot;", "link": "https://blog.example/1", "description": "응모 <b>방법</b> &amp; 경품",
         "bloggername": "주최<b>자</b>", "bloggerlink": "https://blog.example", "postdate": "20261110"},
        "junk",
    ]})


async def test_search_needs_key(bot: DealBot) -> None:
    seen = naver(bot, ok_blog)
    key = desk(bot).key()
    for k in (None, "", "wrong", key[:-1]):
        status, body = await mget(bot, f"{SEARCH}?q=abc", k)
        assert status == 401 and body["code"] == "bad_key", k
    assert seen == []  # 틀린 키로는 네이버를 부르지 않음
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", f"{SEARCH}?q=abc", anon))[0] == 401


async def test_search_ok_strips_tags_and_sends_deal_bot_key(bot: DealBot) -> None:
    seen = naver(bot, ok_blog)
    key = desk(bot).key()
    status, body = await mget(bot, f"{SEARCH}?q=돈좀요&display=99&sort=date", key)
    assert status == 200 and body["total"] == 42
    assert body["items"] == [{"title": '돈좀요 이벤트 "공지"', "link": "https://blog.example/1", "description": "응모 방법 & 경품",
                              "postdate": "20261110", "bloggername": "주최자"}]
    assert NAVER_ID not in json.dumps(body) and NAVER_SECRET not in json.dumps(body)
    req = seen[0]
    assert req.url.path == "/v1/search/blog.json" and req.headers["x-naver-client-id"] == NAVER_ID
    assert req.headers["x-naver-client-secret"] == NAVER_SECRET
    assert req.url.params["display"] == "20" and req.url.params["sort"] == "date" and req.url.params["query"] == "돈좀요"
    # 기본값: blog · sim · 10 / news 는 pubDate
    await mget(bot, f"{SEARCH}?q=x&display=0", key)
    assert seen[1].url.params["display"] == "1" and seen[1].url.params["sort"] == "sim"
    await mget(bot, f"{SEARCH}?q=x", key)
    assert seen[2].url.params["display"] == "10"
    import httpx
    naver(bot, lambda r: httpx.Response(200, json={"total": 1, "items": [{"title": "<b>a</b>", "link": "l", "description": "d", "pubDate": "Sat, 14 Nov 2026"}]}))
    status, body = await mget(bot, f"{SEARCH}?type=news&q=x", key)
    assert body["items"][0] == {"title": "a", "link": "l", "description": "d", "pubDate": "Sat, 14 Nov 2026"}


async def test_search_validation(bot: DealBot) -> None:
    seen = naver(bot, ok_blog)
    key = desk(bot).key()
    for qs in ("q=x&type=shop", "q=", "type=blog", f"q={'가' * 101}", "q=x&sort=bad"):
        status, body = await mget(bot, f"{SEARCH}?{qs}", key)
        assert status == 400 and body["code"] == "bad_query", qs
    for t in ("blog", "news", "webkr"):
        assert (await mget(bot, f"{SEARCH}?type={t}&q={'가' * 100}", key))[0] == 200
    assert [r.url.path for r in seen] == [f"/v1/search/{t}.json" for t in ("blog", "news", "webkr")]
    assert ["sort" in r.url.params for r in seen] == [True, True, False]  # 웹문서 검색에는 sort 가 없음 (11/14)
    assert (await mget(bot, f"{SEARCH}?q=x&display=abc", key))[0] == 200  # 숫자가 아니면 기본값


async def test_search_no_key_configured(bot: DealBot) -> None:
    seen = naver(bot, ok_blog, keyed=False)
    status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
    assert (status, body["code"]) == (503, "no_key") and seen == []


async def test_search_limits(bot: DealBot) -> None:
    from datetime import datetime

    naver(bot, ok_blog)
    d = desk(bot)
    key = d.key()
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    # 하루 300 (KST 날짜 기준) — 299 까지 쓴 날
    day = datetime.fromtimestamp(now[0], ssalmuk.KST).strftime("%Y-%m-%d")
    bot.db.kv_set(ssalmuk.KV_SEARCH_DAY, json.dumps({"d": day, "n": 299}))
    assert (await mget(bot, f"{SEARCH}?q=x", key))[0] == 200
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert status == 429 and body["code"] == "limit"
    # 다음 날(KST)엔 다시 0부터
    now[0] += 86400
    assert (await mget(bot, f"{SEARCH}?q=x", key))[0] == 200
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_DAY))["n"] == 1


async def test_search_per_minute_limit(bot: DealBot) -> None:
    naver(bot, ok_blog)
    key = desk(bot).key()
    codes = [(await mget(bot, f"{SEARCH}?q=x", key))[0] for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    assert (await mget(bot, f"{SEARCH}?q=x", key))[1]["code"] == "limit"


async def test_search_naver_errors_map_to_502(bot: DealBot) -> None:
    import httpx

    key = desk(bot).key()
    for st in (401, 429, 500, 503):
        naver(bot, lambda r, st=st: httpx.Response(st, json={"errorMessage": NAVER_SECRET}))
        status, body = await mget(bot, f"{SEARCH}?q=x", key)
        assert status == 502 and body["code"] == "naver_error" and body["status"] == st
        assert NAVER_SECRET not in json.dumps(body) and NAVER_ID not in json.dumps(body)

    def boom(req: Any) -> Any:
        raise httpx.ConnectTimeout("t", request=req)

    naver(bot, boom)
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert (status, body["code"], body["status"]) == (502, "naver_error", 0)


BLOG_ID, BLOG_SECRET = "blog-naver-id-qqq", "blog-naver-secret-zzz"


def blog_env(bot: DealBot, tmp_path: Path, text: str | None = None) -> Path:
    p = tmp_path / "blog.env"
    p.write_text(text if text is not None else f"OTHER=1\nNAVER_CLIENT_ID={BLOG_ID}\nexport NAVER_CLIENT_SECRET=\"{BLOG_SECRET}\"\nTG_TOKEN=do-not-read\n")
    bot.settings.dashboard.ssalmuk_search_env_file = str(p)
    return p


def by_key(req: Any) -> Any:
    import httpx

    if req.headers["x-naver-client-id"] == BLOG_ID:
        return ok_blog(req)
    return httpx.Response(401, json={"errorMessage": NAVER_SECRET})


async def test_search_falls_back_to_blog_key_and_remembers(bot: DealBot, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    blog_env(bot, tmp_path)
    seen = naver(bot, by_key)
    d = desk(bot)
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    key = d.key()
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert status == 200 and body["total"] == 42
    assert [r.headers["x-naver-client-id"] for r in seen] == [NAVER_ID, BLOG_ID]
    assert bot.db.kv_get(ssalmuk.KV_SEARCH_KEYSRC) == "blog"
    # 다음 호출은 곧장 블로그 키로
    await mget(bot, f"{SEARCH}?q=x", key)
    assert [r.headers["x-naver-client-id"] for r in seen][2:] == [BLOG_ID]
    # 화면용 state: 값 없이 keysrc·오늘 횟수만
    _s, st, _h = await get_json(bot, f"{B}/state", session_headers(bot))
    keys = st["search_proxy"].pop("keys")
    assert st["search_proxy"] == {"keysrc": "blog", "today_count": 2}
    # 둘째 호출은 블로그 키만 — 그래도 딜봇 키의 지난 답(401)과 그때 시각이 남는다 (주인 11/14: 예전엔 '-' 로 덮여 원인이 사라짐)
    assert keys["blog"] == {"s": 200, "at": 1_800_000_000} and keys["dealbot"] == {"s": "401 키틀림", "at": 1_800_000_000} and isinstance(keys["at"], int)
    now[0] += 120
    await mget(bot, f"{SEARCH}?q=x", key)
    keys = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert keys["blog"] == {"s": 200, "at": 1_800_000_120} and keys["dealbot"] == {"s": "401 키틀림", "at": 1_800_000_000}
    _s, st, _h = await get_json(bot, f"{B}/state", session_headers(bot))
    assert st["search_proxy"]["today_count"] == 3
    # 하루 뒤엔 딜봇 키를 다시 봄 — 살아났으면 되돌아감
    now[0] += 86401
    naver(bot, ok_blog)
    blog_env(bot, tmp_path)
    assert (await mget(bot, f"{SEARCH}?q=x", key))[0] == 200
    assert bot.db.kv_get(ssalmuk.KV_SEARCH_KEYSRC) == "dealbot"
    for secret in (NAVER_ID, NAVER_SECRET, BLOG_ID, BLOG_SECRET, "do-not-read"):
        assert secret not in caplog.text and secret not in json.dumps(st)
        assert secret not in json.dumps(body)


async def test_search_blog_key_only_when_dealbot_key_missing(bot: DealBot, tmp_path: Path) -> None:
    blog_env(bot, tmp_path)
    seen = naver(bot, by_key, keyed=False)
    assert (await mget(bot, f"{SEARCH}?q=x", desk(bot).key()))[0] == 200
    assert [r.headers["x-naver-client-id"] for r in seen] == [BLOG_ID]
    assert bot.db.kv_get(ssalmuk.KV_SEARCH_KEYSRC) == "blog"


async def test_search_both_keys_dead_is_502(bot: DealBot, tmp_path: Path) -> None:
    import httpx

    blog_env(bot, tmp_path)
    for st in (401, 403):
        seen = naver(bot, lambda r, st=st: httpx.Response(st, json={"errorMessage": BLOG_SECRET}))
        status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
        assert (status, body["code"], body["status"]) == (502, "naver_error", st) and len(seen) == 2
        assert BLOG_SECRET not in json.dumps(body) and BLOG_ID not in json.dumps(body)
    assert bot.db.kv_get(ssalmuk.KV_SEARCH_KEYSRC) is None
    # 429 같은 다른 오류는 키를 바꿔 보지 않음
    seen = naver(bot, lambda r: httpx.Response(429))
    assert (await mget(bot, f"{SEARCH}?q=x", desk(bot).key()))[1]["status"] == 429 and len(seen) == 1


async def test_search_blog_key_file_mtime_reload_and_missing(bot: DealBot, tmp_path: Path) -> None:
    import os

    p = blog_env(bot, tmp_path)
    seen = naver(bot, by_key, keyed=False)
    key = desk(bot).key()
    assert (await mget(bot, f"{SEARCH}?q=x", key))[0] == 200
    # 파일이 같으면 다시 읽지 않음 (메모리)
    reads: list[Any] = []
    orig = ssalmuk.BlogKeyFile._parse
    ssalmuk.BlogKeyFile._parse = staticmethod(lambda q: reads.append(q) or orig(q))  # type: ignore[assignment]
    try:
        await mget(bot, f"{SEARCH}?q=x", key)
        assert reads == []
        # 키가 바뀌고 수정 시각이 달라지면 다시 읽음
        p.write_text("NAVER_CLIENT_ID=other-id\nNAVER_CLIENT_SECRET=other-secret\n")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        status, body = await mget(bot, f"{SEARCH}?q=x", key)
        assert len(reads) == 1 and (status, body["status"]) == (502, 401)
        assert seen[-1].headers["x-naver-client-id"] == "other-id"
    finally:
        ssalmuk.BlogKeyFile._parse = staticmethod(orig)  # type: ignore[assignment]
    # 파일이 없어지면 키 없음 → 503, 두 줄이 다 없는 파일도 마찬가지
    p.unlink()
    assert (await mget(bot, f"{SEARCH}?q=x", key))[1]["code"] == "no_key"
    blog_env(bot, tmp_path, "NAVER_CLIENT_ID=only-id\n")
    assert (await mget(bot, f"{SEARCH}?q=x", key))[1]["code"] == "no_key"


async def test_search_records_each_key_status(bot: DealBot, tmp_path: Path) -> None:
    """주인 11/14 '딜봇 502 · 네이버 401' — 어느 키가 막혔는지(또는 블로그 키 파일을 못 읽었는지) 화면에 값만 남긴다."""
    import httpx

    naver(bot, lambda req: httpx.Response(401, json={"errorCode": "024"}))
    d = desk(bot)
    status, _b = await mget(bot, f"{SEARCH}?q=x", d.key())
    assert status == 502
    keys = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert keys["dealbot"]["s"] == "401 키틀림" and keys["blog"]["s"] == "no_file" and keys["dealbot"]["at"] == keys["at"]
    naver(bot, lambda req: httpx.Response(401, json={"errorMessage": "Scope Status Invalid : Authentication failed. (인증에 실패했습니다.)", "errorCode": "024"}))
    await mget(bot, f"{SEARCH}?q=x", d.key())
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["dealbot"]["s"] == "401 검색미등록"
    # 네이버가 답하지 않아 502 로 일찍 돌아갈 때도 써 본 키의 답을 남김
    def boom(req: Any) -> Any:
        raise httpx.ConnectTimeout("t")
    naver(bot, boom)
    status, body = await mget(bot, f"{SEARCH}?q=x", d.key())
    assert status == 502 and body["status"] == 0
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["dealbot"]["s"] == "no_answer"
    # 예전 모양(값만)으로 저장돼 있던 것도 읽어 이어 씀
    bot.db.kv_set(ssalmuk.KV_SEARCH_KEYS, json.dumps({"dealbot": "401 키틀림", "blog": "-", "at": 111}))
    d.note_keys({"dealbot": "-", "blog": 200})
    got = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert got["dealbot"] == {"s": "401 키틀림", "at": 111} and got["blog"]["s"] == 200
    js = (Path(ssalmuk.__file__).parent / "static" / "ssalmuk.js").read_text(encoding="utf-8")
    assert "키 파일 못 읽음" in js and "keyDiag(p && p.keys)" in js and "x.at ? hm(x.at * 1000)" in js and "키 응답 없음" in js
    assert "search_retry: '검색 다시 켜기'" in js and "trust_host: '사이트 허용'" in js  # 명령 결과 알림에 이름이 뜨게
    assert ssalmuk.OP_LABEL["search_retry"] == "검색 다시 켜기" and ssalmuk.OP_LABEL["trust_host"] == "사이트 허용"


# ---------------------------------------------------------------- 7b) 카카오(다음) 검색으로 이어서 (11/15 — 네이버 검색 앱이 지워져 두 키 다 401)
KAKAO_KEY = "test-kakao-rest-key-777"


def kakao_docs(req: Any) -> Any:
    import httpx

    return httpx.Response(200, json={"meta": {"total_count": 1234, "pageable_count": 800, "is_end": False}, "documents": [
        {"title": "<b>구글폼</b> 응모 &quot;이벤트&quot;", "contents": "경품 <b>추첨</b> &amp; 발표", "url": "https://blog.example/k1",
         "datetime": "2026-11-14T10:24:00.000+09:00", "blogname": "주최<b>측</b>", "thumbnail": "https://img.example/t.jpg"},
        {"title": "자정 넘긴 글", "contents": "", "url": "https://news.example/a", "datetime": "2026-11-13T15:30:00.000Z"},
        "junk",
    ]})


def kakao_or_401(req: Any) -> Any:
    import httpx

    if req.url.host == "dapi.kakao.com":
        return kakao_docs(req)
    return httpx.Response(401, json={"errorMessage": "Not Exist Client ID : Authentication failed.", "errorCode": "024"})


def test_kakao_items_shape() -> None:
    """카카오 documents → 네이버 items 꼴 (쌀먹 계약 그대로): <b>·글자 표현 풀기 · blog 는 postdate·bloggername · news 는 pubDate · webkr 날짜 없음."""
    data = kakao_docs(None).json()
    items, total = ssalmuk.kakao_items(data, "blog")
    assert total == 1234
    assert items == [{"title": '구글폼 응모 "이벤트"', "link": "https://blog.example/k1", "description": "경품 추첨 & 발표",
                      "postdate": "20261114", "bloggername": "주최측"},
                     {"title": "자정 넘긴 글", "link": "https://news.example/a", "description": "", "postdate": "20261114"}]  # UTC 15:30 = KST 다음 날
    items, _t = ssalmuk.kakao_items(data, "news")
    assert items[0]["pubDate"] == "Sat, 14 Nov 2026 10:24:00 +0900" and items[1]["pubDate"] == "Sat, 14 Nov 2026 00:30:00 +0900"
    assert "bloggername" not in items[0] and "postdate" not in items[0]
    items, _t = ssalmuk.kakao_items(data, "webkr")
    assert set(items[0]) == {"title", "link", "description"}
    # 이상한 답: 빈 목록 · total 은 항목 수 · 날짜가 이상하면 날짜 칸만 뺌
    assert ssalmuk.kakao_items(None, "blog") == ([], 0)
    assert ssalmuk.kakao_items({"meta": {"total_count": "9"}, "documents": [{"title": "a", "datetime": "어제"}]}, "blog") == (
        [{"title": "a", "link": "", "description": ""}], 1)


async def test_search_naver_401_falls_back_to_kakao(bot: DealBot, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    blog_env(bot, tmp_path)
    seen = naver(bot, kakao_or_401, kakao=KAKAO_KEY)
    d = desk(bot)
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    key = d.key()
    status, body = await mget(bot, f"{SEARCH}?q=구글폼&display=99&sort=date", key)
    assert status == 200 and body["total"] == 1234 and body["source"] == "kakao"
    assert body["items"][0] == {"title": '구글폼 응모 "이벤트"', "link": "https://blog.example/k1", "description": "경품 추첨 & 발표",
                                "postdate": "20261114", "bloggername": "주최측"}
    assert [r.url.host for r in seen] == ["openapi.naver.com", "openapi.naver.com", "dapi.kakao.com"]
    req = seen[-1]
    assert req.url.path == "/v2/search/blog" and req.headers["authorization"] == f"KakaoAK {KAKAO_KEY}"
    assert dict(req.url.params) == {"query": "구글폼", "sort": "recency", "size": "20", "page": "1"}
    assert "x-naver-client-id" not in req.headers  # 네이버 키는 카카오에 안 보냄
    assert d.search_keysrc() == "kakao" and json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_DAY))["n"] == 1  # 키를 셋 써도 하루 한 번
    keys = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert keys["dealbot"]["s"] == "401 키틀림" and keys["blog"]["s"] == "401 키틀림" and keys["kakao"]["s"] == 200
    # 다음부터 카카오 먼저 (네이버는 안 부름) · 매핑: webkr→web · news→web 최신순 · sim→accuracy
    for qs, path, srt in (("type=webkr&q=x", "/v2/search/web", "accuracy"), ("type=news&q=x", "/v2/search/web", "recency"),
                          ("type=news&q=x&sort=sim", "/v2/search/web", "recency"), ("q=x", "/v2/search/blog", "accuracy")):
        n = len(seen)
        status, body = await mget(bot, f"{SEARCH}?{qs}", key)
        assert status == 200 and body["source"] == "kakao", qs
        assert [r.url.host for r in seen[n:]] == ["dapi.kakao.com"], qs
        assert (seen[-1].url.path, seen[-1].url.params["sort"], seen[-1].url.params["size"]) == (path, srt, "10"), qs
    _s, st, _h = await get_json(bot, f"{B}/state", session_headers(bot))
    assert st["search_proxy"]["keysrc"] == "kakao" and st["search_proxy"]["keys"]["kakao"]["s"] == 200
    # 하루 뒤엔 네이버 키를 다시 봄 — 살아났으면 되돌아감
    now[0] += 86401
    naver(bot, ok_blog, kakao=KAKAO_KEY)
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert status == 200 and "source" not in body and d.search_keysrc() == "dealbot"
    for secret in (KAKAO_KEY, NAVER_ID, NAVER_SECRET, BLOG_ID, BLOG_SECRET):
        assert secret not in caplog.text and secret not in json.dumps(st) and secret not in json.dumps(body)
        assert secret not in (bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS) or "")


async def test_search_kakao_key_from_blog_env_file(bot: DealBot, tmp_path: Path) -> None:
    """딜봇 환경에 카카오 키가 없으면 Blog-Auto .env 의 KAKAO_REST_API_KEY 한 줄 (세 줄만 읽음 — 다른 줄은 안 봄)."""
    blog_env(bot, tmp_path, f"NAVER_CLIENT_ID={BLOG_ID}\nNAVER_CLIENT_SECRET={BLOG_SECRET}\nexport KAKAO_REST_API_KEY='{KAKAO_KEY}'\nTG_TOKEN=do-not-read\n")
    seen = naver(bot, kakao_or_401, keyed=False)
    status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
    assert status == 200 and body["source"] == "kakao"
    assert [r.url.host for r in seen] == ["openapi.naver.com", "dapi.kakao.com"] and seen[-1].headers["authorization"] == f"KakaoAK {KAKAO_KEY}"
    bk = ssalmuk.BlogKeyFile(lambda: str(tmp_path / "blog.env"))
    assert bk.kakao() == KAKAO_KEY and bk.get() == (BLOG_ID, BLOG_SECRET)
    assert "do-not-read" not in json.dumps(bk._vals) and set(bk._vals) == set(ssalmuk.BLOG_ENV_NAMES) - {"GEMINI_API_KEY"}
    # 딜봇 환경 키가 있으면 그것 먼저
    seen = naver(bot, kakao_or_401, keyed=False, kakao="dealbot-kakao-key")
    await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
    assert seen[-1].headers["authorization"] == "KakaoAK dealbot-kakao-key"
    # 카카오 줄만 있는 파일 (네이버 키 없음) → 곧장 카카오
    blog_env(bot, tmp_path, f"KAKAO_REST_API_KEY={KAKAO_KEY}\n")
    seen = naver(bot, kakao_or_401, keyed=False)
    assert (await mget(bot, f"{SEARCH}?q=x", desk(bot).key()))[0] == 200 and [r.url.host for r in seen] == ["dapi.kakao.com"]


async def test_search_kakao_missing_key_keeps_naver_behavior(bot: DealBot, tmp_path: Path) -> None:
    """카카오 키가 어디에도 없으면 예전 그대로 (네이버 401 → 502 · 키가 하나도 없으면 503) · 화면엔 '카카오 키 없음'."""
    import httpx

    blog_env(bot, tmp_path)
    seen = naver(bot, kakao_or_401)
    status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
    assert (status, body["code"], body["status"]) == (502, "naver_error", 401) and "source" not in body
    assert all(r.url.host == "openapi.naver.com" for r in seen)
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["kakao"]["s"] == "none"
    bot.settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "gone.env")
    seen = naver(bot, lambda r: httpx.Response(200), keyed=False)
    status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
    assert (status, body["code"]) == (503, "no_key") and seen == []
    # 카카오 키만 있으면 그것만으로 검색
    seen = naver(bot, kakao_or_401, keyed=False, kakao=KAKAO_KEY)
    assert (await mget(bot, f"{SEARCH}?q=x", desk(bot).key()))[0] == 200 and [r.url.host for r in seen] == ["dapi.kakao.com"]


async def test_search_kakao_errors(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    """카카오 401/403/429/5xx → (남은 네이버 키가 없으면) 502 status 그대로 · 시간 초과 → 502 status 0 — 모두 source kakao · 키는 안 남김."""
    import logging

    import httpx

    caplog.set_level(logging.DEBUG)
    key = desk(bot).key()
    for st in (401, 403, 429, 500):
        def h(r: Any, st: int = st) -> Any:
            if r.url.host == "dapi.kakao.com":
                return httpx.Response(st, json={"errorType": "AccessDeniedError", "message": KAKAO_KEY})
            return httpx.Response(401, json={})
        seen = naver(bot, h, kakao=KAKAO_KEY)
        status, body = await mget(bot, f"{SEARCH}?q=x", key)
        assert (status, body["code"], body["status"], body["source"]) == (502, "naver_error", st, "kakao"), st
        assert KAKAO_KEY not in json.dumps(body) and len(seen) == 2
        assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["kakao"]["s"] == st
    assert desk(bot).search_keysrc() is None

    def boom(req: Any) -> Any:
        if req.url.host == "dapi.kakao.com":
            raise httpx.ReadTimeout("t", request=req)
        return httpx.Response(403)
    naver(bot, boom, kakao=KAKAO_KEY)
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert (status, body["status"], body["source"]) == (502, 0, "kakao")
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["kakao"]["s"] == "no_answer"
    assert KAKAO_KEY not in caplog.text


async def test_search_kakao_first_then_naver_when_kakao_dies(bot: DealBot) -> None:
    """keysrc 가 kakao 이고 하루가 안 지났으면 카카오 먼저 · 카카오가 401 이면 네이버 키로 이어서 (살아 있으면 되돌아감)."""
    import httpx

    d = desk(bot)
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    key = d.key()
    naver(bot, kakao_or_401, kakao=KAKAO_KEY)
    await mget(bot, f"{SEARCH}?q=x", key)
    assert d.search_keysrc() == "kakao"
    now[0] += 60
    seen = naver(bot, lambda r: httpx.Response(401) if r.url.host == "dapi.kakao.com" else ok_blog(r), kakao=KAKAO_KEY)
    status, body = await mget(bot, f"{SEARCH}?q=x", key)
    assert status == 200 and [r.url.host for r in seen] == ["dapi.kakao.com", "openapi.naver.com"]
    assert d.search_keysrc() == "dealbot" and "source" not in body


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_shows_kakao_key() -> None:
    """화면 한 줄: 키가 카카오면 '검색 · … · 키: 카카오(다음)' · keyDiag 에 '카카오 키 …'."""
    s = ssalmuk.Ssalmuk(FakeDb())
    st = sample()
    st["sources"] = [{"id": "donzomyo", "name": "돈좀요", "status": "ok", "items": 12}]
    s.push(st)
    v = s.view()

    def src(proxy: dict[str, Any]) -> str:
        v["search_proxy"] = proxy
        out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "ssalmuk.js"), json.dumps(v, ensure_ascii=False)],
                             capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)["src"]
    keys = {"dealbot": {"s": "401 키틀림", "at": 1_800_000_000}, "blog": {"s": "401 키틀림", "at": None}, "kakao": {"s": 200, "at": None}, "at": 1}
    got = src({"keysrc": "kakao", "today_count": 4, "keys": keys})
    assert "검색 · 오늘 4회 · 키: 카카오(다음)" in got and "네이버 검색" not in got
    assert "딜봇 키 401 키틀림" in got and "블로그 키 401 키틀림" in got and "카카오 키 200" in got
    got = src({"keysrc": "dealbot", "today_count": 4, "keys": dict(keys, kakao={"s": "none", "at": None})})
    assert "네이버 검색 · 오늘 4회 · 키: 딜봇" in got and "카카오 키 없음" in got


def test_act_search_retry_and_trust_host(bot: DealBot) -> None:
    """쌀먹 5469c7b (11/14): '검색 다시 시도' · '이 사이트 허용' 명령 — 모양만 확인 (쌀먹이 http·IP·포트·위험 문구를 다시 거름)."""
    d = desk(bot)
    assert d.act({"op": "search_retry", "args": {"x": 1}})[0] == 200
    s2, b2 = d.act({"op": "search_retry"})
    assert s2 == 200 and b2.get("dup")
    for bad in ("", "1.2.3.4", "m.sum37.com:8080", "http://m.sum37.com", "localhost", "a b.com", "x" * 300 + ".com"):
        assert d.act({"op": "trust_host", "args": {"host": bad}})[0] == 400, bad
    assert d.act({"op": "trust_host", "args": {"host": "M.Sum37.com."}})[0] == 200
    assert d.act({"op": "trust_host", "args": {"host": "m.sum37.com"}})[0] == 200
    cmds = [c for c in json.loads(bot.db.kv_get(ssalmuk.KV_CMDS)) if c["op"] == "trust_host"]
    assert [c["args"] for c in cmds] == [{"host": "m.sum37.com"}]
    js = (Path(ssalmuk.__file__).parent / "static" / "ssalmuk.js").read_text(encoding="utf-8")
    assert "'이 사이트 허용'" in js and "'검색 다시 시도'" in js and "blockedToday" in js


# ---------------------------------------------------------------- 오류 검수 경고 한 줄 (주인 11/14 — 쌀먹 alerts[], SNSBOARD.md '오류 검수 칸')
VIEW_KEYS = {"state", "receivedAt", "pending", "stale", "results", "search_proxy", "llm_proxy"}


def test_alerts_absent_is_empty_and_view_keys_unchanged() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    assert s.view()["alerts"] == [] and set(s.view()) == VIEW_KEYS | {"alerts"}  # 받은 적 없음
    s.push(sample())
    v = s.view()
    assert v["alerts"] == [] and set(v) == VIEW_KEYS | {"alerts"} and "alerts" not in v["state"]
    for bad in ("경고", {"id": "x"}, None, 3):
        s.push(sample(alerts=bad))
        assert s.view()["alerts"] == []


def test_alerts_malformed_entries_dropped_and_text_only() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(sample(alerts=[
        "글 하나", None, ["x"], {"level": "ok", "msg": "정상"}, {"level": "info", "msg": "?"}, {"level": "bad"}, {"level": "bad", "msg": 3},
        {"id": "search", "level": "warn", "msg": "<b>네이버 검색</b> &quot;쉼&quot;\n오늘", "todo": "블로그 메인 6 에 부탁", "since": "2026-11-14T01:00:00Z", "new": True,
         "extra": "버림"},
        {"id": "x" * 200, "level": "bad", "msg": "가" * 500, "todo": ["목록"], "new": "yes"},
    ]))
    a = s.view()["alerts"]
    assert len(a) == 2
    assert a[0] == {"id": "x" * 64, "level": "bad", "msg": "가" * 200, "since": "", "todo": "", "new": False}
    assert a[1] == {"id": "search", "level": "warn", "msg": '네이버 검색 "쉼" 오늘', "since": "2026-11-14T01:00:00Z", "todo": "블로그 메인 6 에 부탁", "new": True}


def test_alerts_bad_first_and_at_most_ten() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    raw = [{"id": f"w{i}", "level": "warn", "msg": f"경고 {i}"} for i in range(8)] + [{"id": f"b{i}", "level": "bad", "msg": f"오류 {i}"} for i in range(5)]
    s.push(sample(alerts=raw))
    a = s.view()["alerts"]
    assert [x["id"] for x in a] == ["b0", "b1", "b2", "b3", "b4", "w0", "w1", "w2", "w3", "w4"]


SHIM_KIDS = SHIM.replace("out.scr = text(scr);", "out.scr = text(scr); out.kids = scr.children.map(c => c.className);")


def _render(view: dict[str, Any]) -> dict[str, Any]:
    out = subprocess.run([NODE, "-e", SHIM_KIDS, str(STATIC / "ssalmuk.js"), json.dumps(view, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_alert_line_on_top_text_only() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(sample())
    plain = s.view()
    base = _render(plain)
    old = dict(plain)
    old.pop("alerts")
    assert _render(old) == base  # 칸이 없으면 화면은 그대로
    assert "bn warn" not in base["kids"] and "bn bad" not in base["kids"]
    s.push(sample(alerts=[{"id": "w", "level": "warn", "msg": "메모리 최고 650MB", "todo": ""},
                          {"id": "s", "level": "bad", "msg": "네이버 검색 오늘 쉼", "todo": "블로그 메인 6 에 부탁", "new": True},
                          {"id": "d", "level": "warn", "msg": "디스크 400MB"}]))
    got = _render(s.view())
    assert got["kids"][:3] == ["top", "bn bad", "box"]  # 맨 위(KPI 칸 위) 한 줄
    assert "네이버 검색 오늘 쉼 · 할 일: 블로그 메인 6 에 부탁 외 2건" in got["scr"]
    assert got["scr"].replace("네이버 검색 오늘 쉼 · 할 일: 블로그 메인 6 에 부탁 외 2건", "") == base["scr"]
    s.push(sample(alerts=[{"id": "w", "level": "warn", "msg": "<a href='x'>메모리</a> 650MB"}]))
    got = _render(s.view())
    assert got["kids"][1] == "bn warn" and "메모리 650MB" in got["scr"] and "할 일:" not in got["scr"] and " 외 " not in got["scr"]
    code = (STATIC / "ssalmuk.js").read_text(encoding="utf-8")
    fn = code[code.index("function alertLine()"):code.index("function drawScr()")]
    assert "innerHTML" not in fn and "'a'" not in fn and "href" not in fn  # 글 노드만


# ---------------------------------------------------------------- 퀴즈 카드 · 마지막 실행 점검 줄 (11/14 — 쌀먹 6663b6c·35aafbb, docs/SNSBOARD.md 'quiz_answer'·'오류 검수 칸')
Q1 = {"q": "이번 행사의 주최 기관은 어디일까요?", "options": ["한국전력", "한국수력원자력", "<b>가스공사</b>"], "suggest": "한국수력원자력", "confidence": 0.6,
      "evidence": "본문: 한국수력원자력이 주최", "why": "글자 비교와 Gemini 답이 다름", "key": "q2:form-abc:1f2e"}
Q2 = {"q": "정답은 무엇일까요?", "options": ["O", "X"], "suggest": None, "confidence": None, "evidence": "", "why": "", "key": None}


def quiz_sample(**kw: Any) -> dict[str, Any]:
    st = sample(counts={"auto": 0, "left": 2, "today": 0, "applied": 1, "todayApplied": 0, "weekApplied": 1})
    st["todo"][0].update(group="check", status="manual", today=False, detail="확인 필요: 정답 불확실", quiz=[Q1])
    st["todo"][1].update(group="check", status="manual", detail="확인 필요: 정답 불확실", quiz=[Q2])
    st.update(kw)
    return st


def test_act_quiz_answer_shape_key_and_null_key() -> None:
    """op quiz_answer 는 {question, answer, key} 그대로 쌓임 — key 는 카드 값(글) 또는 null · 질문·답은 다듬지 않음 · 같은 퀴즈는 마지막 답만."""
    s = ssalmuk.Ssalmuk(FakeDb())
    assert "quiz_answer" in ssalmuk.OPS
    for bad in ({}, {"question": "", "answer": "O"}, {"question": "질문", "answer": ""}, {"question": 3, "answer": "O"}, {"question": "질문", "answer": ["O"]},
                {"question": "가" * 1001, "answer": "O"}, {"question": "질문", "answer": "가" * 301}, {"question": "질문", "answer": "O", "key": 7},
                {"question": "질문", "answer": "O", "key": "k" * 1001}):
        assert s.act({"op": "quiz_answer", "args": bad})[0] == 400, bad
    assert s.commands() == {"commands": []}
    long_q = "가나다 " * 50  # 카드 질문 200자 — 자르지 않고 그대로 (쌀먹 질문 은행 키)
    assert s.act({"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"], "extra": "버림"}})[0] == 200
    assert s.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "O", "key": None}})[0] == 200
    assert s.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "X"}})[0] == 200  # key 를 안 보내도 null 로 · 같은 퀴즈는 마지막 답만
    assert s.act({"op": "quiz_answer", "args": {"question": long_q, "answer": " 띄어 쓴 보기 ", "key": ""}})[0] == 200
    cmds = s.commands()["commands"]
    assert [c["op"] for c in cmds] == ["quiz_answer"] * 3 and all(set(c) == {"id", "op", "args", "at"} for c in cmds)
    assert [c["args"] for c in cmds] == [{"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]},
                                         {"question": Q2["q"], "answer": "X", "key": None},
                                         {"question": long_q, "answer": " 띄어 쓴 보기 ", "key": None}]
    # 같은 질문이라도 key 가 다르면 다른 퀴즈 (다른 이벤트)
    assert s.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "O", "key": "q2:other"}})[0] == 200
    assert len(s.commands()["commands"]) == 4


def test_quiz_pending_and_ack_reason_reach_the_view() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(quiz_sample())
    assert "quiz_pending" not in s.view()  # 없으면 칸도 없음 (화면 그대로)
    a = s.act({"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}})[1]["id"]
    b = s.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "O", "key": None}})[1]["id"]
    v = s.view()
    assert [{k: x[k] for k in ("id", "question", "answer", "key")} for x in v["quiz_pending"]] == [
        {"id": a, "question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}, {"id": b, "question": Q2["q"], "answer": "O", "key": None}]
    assert all(set(x) == {"id", "at", "question", "answer", "key"} and x["at"].endswith("Z") for x in v["quiz_pending"])
    why = "같은 질문 카드가 여럿이라 저장 안 함"
    assert s.ack({"ids": [a, b], "results": {a: "ok", b: why}})[0] == 200
    v = s.view()
    assert "quiz_pending" not in v and v["pending"] == 0
    assert [(r["op"], r["result"], r["ref"]) for r in v["results"]] == [
        ("quiz_answer", "ok", {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}),
        ("quiz_answer", why, {"question": Q2["q"], "answer": "O", "key": None})]
    s.act({"op": "run"})
    s.ack({"ids": [s.commands()["commands"][0]["id"]], "results": {}})
    assert "ref" not in s.view()["results"][-1]  # 다른 명령 결과는 예전 그대로


@pytest.mark.asyncio
async def test_quiz_answer_through_screen_route_and_audit(bot: DealBot) -> None:
    d = desk(bot)
    key = d.key()
    assert (await mpost(bot, ssalmuk.PUSH_PATH, quiz_sample(), key))[0] == 200
    h = session_headers(bot)
    status, body = (await post_json(bot, ssalmuk.ACT_PATH, {"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}}, h))[:2]
    assert status == 200 and body["ok"]
    status, got = await mget(bot, ssalmuk.COMMANDS_PATH, key)
    assert status == 200 and [(c["op"], c["args"]) for c in got["commands"]] == [("quiz_answer", {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]})]
    rows = bot.db._conn.execute("SELECT action, args_redacted FROM dash_audit WHERE action LIKE 'ssalmuk.%'").fetchall()
    assert [r[0] for r in rows] == ["ssalmuk.quiz_answer"] and "한국전력" not in rows[0][1]


QUIZ_SHIM = SHIM.replace("const runb = find(scr,", r"""
  const all = (n, f, a = []) => { if (f(n)) a.push(n); n.children.forEach(c => all(c, f, a)); return a; };
  const qz = () => all(main.children[0], n => n.className === 'qz');
  const btns = n => all(n, x => x.tagName === 'BUTTON').map(b => [text(b), b.attrs['aria-pressed'], 'disabled' in b.attrs]);
  out.quiz = qz().map(n => ({text: text(n), btns: btns(n), tags: all(n, x => x.tagName === 'A' || x.tagName === 'INPUT' || x.tagName === 'TEXTAREA').length,
    chips: all(n, x => /^cp /.test(x.className)).map(x => x.className + ':' + text(x)), warn: all(n, x => x.className === 'st warn').map(text)}));
  out.health = all(scr, n => n.className === 'st hl').map(n => [n.children[0].className, text(n)]);
  out.note = all(scr, n => n.className === 'note').map(text)[0];
  const pick = process.env.QUIZ_PICK ? JSON.parse(process.env.QUIZ_PICK) : null;
  if (pick && process.env.QUIZ_RACE) {  // 답을 보내는 중(POST 가 아직 안 끝남)에 화면이 상태를 다시 읽어도 '보냄 · 대기'가 풀리지 않아야 함
    global.confirm = () => true;
    let go; const gate = new Promise(r => { go = r; }), f0 = global.fetch;
    global.fetch = async (u, o) => { if (o && o.method === 'POST') await gate; return f0(u, o); };
    const b = all(qz()[pick[0]], x => x.tagName === 'BUTTON')[pick[1]];
    const p = b.listeners.click({currentTarget: b});
    await window.SBEvent.load();
    out.during = qz().map(n => ({text: text(n), btns: btns(n)}));
    go(); await p;
    out.sent = qz().map(n => ({text: text(n), btns: btns(n)}));
    await window.SBEvent.load();  // 서버가 받은 뒤의 읽기 → 이제 서버 값대로 (이 시험의 응답에는 대기가 없음)
    out.after = qz().map(n => ({text: text(n), btns: btns(n)})); out.posts = posts;
    console.log(JSON.stringify(out)); return;
  }
  if (pick) {
    const asked = []; global.confirm = m => { asked.push(m); return process.env.QUIZ_NO ? false : true; };
    const b = all(qz()[pick[0]], x => x.tagName === 'BUTTON')[pick[1]];
    await b.listeners.click({currentTarget: b});
    out.asked = asked; out.picked = qz().map(n => ({text: text(n), btns: btns(n)})); out.posts = posts;
  }
  console.log(JSON.stringify(out)); return;
  const runb = find(scr,""")


def _quiz_render(view: dict[str, Any], pick: list[int] | None = None, no: bool = False, race: bool = False) -> dict[str, Any]:
    import os

    env = dict(os.environ)
    env.pop("QUIZ_PICK", None)
    env.pop("QUIZ_NO", None)
    env.pop("QUIZ_RACE", None)
    if race:
        env["QUIZ_RACE"] = "1"
    if pick is not None:
        env["QUIZ_PICK"] = json.dumps(pick)
    if no:
        env["QUIZ_NO"] = "1"
    out = subprocess.run([NODE, "-e", QUIZ_SHIM, str(STATIC / "ssalmuk.js"), json.dumps(view, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_card_buttons_and_op_shape() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(quiz_sample())
    got = _quiz_render(s.view())
    q1, q2 = got["quiz"]
    # 질문 · 보기마다 단추 (추천 보기에 '추천 0.6') · 근거·까닭은 작은 글 · 링크·입력 칸 없음 (보기 중 하나만) · 태그는 글자로
    assert q1["text"].startswith("퀴즈 · " + Q1["q"]) and q1["tags"] == 0 and q2["tags"] == 0
    assert q1["btns"] == [["한국전력", "false", False], ["한국수력원자력 · 추천 0.6", "false", False], ["<b>가스공사</b>", "false", False]]
    assert "근거: 본문: 한국수력원자력이 주최" in q1["text"] and "글자 비교와 Gemini 답이 다름" in q1["text"]
    assert q2["btns"] == [["O", "false", False], ["X", "false", False]] and "추천" not in q2["text"] and "근거" not in q2["text"]
    assert q1["chips"] == [] and q1["warn"] == [] and "되돌리기" not in got["scr"]
    # 누름 → 확인 창 한 번 → op quiz_answer {question, answer, key: 카드 key} · 카드는 '보냄 · 대기' (단추 잠김, 고른 보기 표시)
    got = _quiz_render(s.view(), pick=[0, 0])
    assert len(got["asked"]) == 1 and Q1["q"] in got["asked"][0] and "한국전력" in got["asked"][0]
    assert got["posts"] == [["/dashboard/api/ssalmuk/act", "tok", {"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}}]]
    assert "보냄 · 대기" in got["picked"][0]["text"] and got["picked"][0]["btns"][0] == ["한국전력", "true", True]
    assert all(b[2] for b in got["picked"][0]["btns"]) and not any(b[2] for b in got["picked"][1]["btns"])
    # key 없는 예전 카드: key 는 null 그대로 · 추천 글이 붙은 단추를 눌러도 답은 보기 글만
    got = _quiz_render(s.view(), pick=[1, 1])
    assert got["posts"] == [["/dashboard/api/ssalmuk/act", "tok", {"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "X", "key": None}}]]
    got = _quiz_render(s.view(), pick=[0, 1])
    assert got["posts"][0][2]["args"] == {"question": Q1["q"], "answer": "한국수력원자력", "key": Q1["key"]}
    # 확인 창에서 취소하면 아무것도 안 보냄
    got = _quiz_render(s.view(), pick=[0, 0], no=True)
    assert got["posts"] == [] and "보냄 · 대기" not in got["picked"][0]["text"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_card_pending_applied_and_reason() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    twin = quiz_sample()
    twin["todo"][0]["quiz"] = [Q1, Q2]  # key 없는 같은 질문이 카드 둘에
    s.push(twin)
    a = s.act({"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}})[1]["id"]
    b = s.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "O", "key": None}})[1]["id"]
    got = _quiz_render(s.view())  # 다시 열어도 '보냄 · 대기' (서버가 기억)
    assert [("보냄 · 대기" in q["text"], q["chips"]) for q in got["quiz"]] == [(True, ["cp info:보냄 · 대기"])] * 3
    assert got["quiz"][0]["btns"][0] == ["한국전력", "true", True]
    why = "같은 질문 카드가 여럿이라 저장 안 함"
    s.ack({"ids": [a, b], "results": {a: "ok", b: why}})
    got = _quiz_render(s.view())
    q1, q2a, q2b = got["quiz"]
    assert q1["chips"] == ["cp ok:반영됨"] and "'한국전력' — 다음 확인부터 자동으로 답해요" in q1["text"] and q1["warn"] == []
    assert q1["btns"][0] == ["한국전력", "true", False]  # 다시 고를 수 있음 (되돌리기 단추는 없음)
    for q in (q2a, q2b):  # 안 된 이유를 쉬운 말 + 받은 글 그대로
        assert q["chips"] == [] and len(q["warn"]) == 1
        assert q["warn"][0].startswith("저장 안 됨 — 같은 질문이 다른 이벤트 카드에도 있어서") and q["warn"][0].endswith(f"({why})")
    # 다른 이유는 받은 글 그대로 · 예전 판 서버('모르는 명령')는 풀어서
    s2 = ssalmuk.Ssalmuk(FakeDb())
    s2.push(quiz_sample())
    for res, want in (("질문이 너무 짧아 저장 안 함 (8자 미만)", "저장 안 됨 — 질문이 너무 짧아 저장 안 함 (8자 미만)"),
                      ("모르는 명령: quiz_answer", "저장 안 됨 — 이벤트 참여 서버가 아직 퀴즈 답 받기를 몰라요 (예전 판) — 서버가 새 판이 된 뒤 다시 눌러 주세요 (모르는 명령: quiz_answer)")):
        c = s2.act({"op": "quiz_answer", "args": {"question": Q2["q"], "answer": "O", "key": None}})[1]["id"]
        s2.ack({"ids": [c], "results": {c: res}})
        assert _quiz_render(s2.view())["quiz"][1]["warn"] == [want]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_unknown_shapes_ignored() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    base_state = quiz_sample()
    for t in base_state["todo"]:
        t.pop("quiz")
    s.push(base_state)
    base = _quiz_render(s.view())
    assert base["quiz"] == []
    for bad in ("퀴즈", {"q": "x"}, [None, "글", 3, {"q": ""}, {"q": 5, "options": ["a"]}, {"options": ["a"]}]):
        st = quiz_sample()
        st["todo"][0]["quiz"] = bad
        st["todo"][1].pop("quiz")
        s.push(st)
        got = _quiz_render(s.view())
        assert got["quiz"] == [] and got["scr"] == base["scr"], bad
    st = quiz_sample()
    st["todo"][1].pop("quiz")
    st["todo"][0]["quiz"] = [{"q": "보기 꼴이 이상한 질문입니다", "options": ["가", 3, None, "", {"x": 1}, "나"], "suggest": 7, "confidence": 4, "evidence": ["x"], "why": None, "key": 9},
                             {"q": "보기가 아예 없는 질문입니다", "options": "없음", "key": "k1"}, Q1, Q2]
    s.push(st)
    got = _quiz_render(s.view(), pick=[0, 1])
    assert len(got["quiz"]) == 3  # 3개까지
    assert got["quiz"][0]["btns"] == [["가", "false", False], ["나", "false", False]] and "추천" not in got["quiz"][0]["text"]
    assert got["quiz"][1]["btns"] == [] and "보기가 없는 질문이에요" in got["quiz"][1]["text"]
    assert got["posts"][0][2]["args"] == {"question": "보기 꼴이 이상한 질문입니다", "answer": "나", "key": None}  # 글이 아닌 key 는 null


def test_quiz_pending_is_capped_dated_and_gone_after_a_day() -> None:
    now = [1_800_000_000.0]
    s = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    s.push(quiz_sample())
    for i in range(ssalmuk.QUIZ_PENDING_MAX + 5):
        now[0] += 1
        assert s.act({"op": "quiz_answer", "args": {"question": f"서로 다른 퀴즈 질문 {i}번입니다", "answer": "O", "key": f"q2:k{i}"}})[0] == 200
    qp = s.view()["quiz_pending"]
    assert len(qp) == ssalmuk.QUIZ_PENDING_MAX and qp[-1]["key"] == f"q2:k{ssalmuk.QUIZ_PENDING_MAX + 4}"  # 새것부터 남김
    assert qp[-1]["at"] == ssalmuk._iso(now[0])
    assert len(s.commands()["commands"]) == ssalmuk.QUIZ_PENDING_MAX + 5  # 명령 자체는 그대로 (화면 응답만 줄임)
    now[0] += ssalmuk.CMD_KEEP + 1  # 하루 넘게 안 가져가면 명령이 사라지고 카드도 대기에서 풀림
    v = s.view()
    assert "quiz_pending" not in v and v["pending"] == 0


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_old_pending_can_be_picked_again() -> None:
    now = [time.time() - 400]  # 6분 넘게 전에 누른 답 (쌀먹이 안 가져감)
    s = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    s.push(quiz_sample())
    s.act({"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}})
    now[0] = time.time()
    got = _quiz_render(s.view(), pick=[0, 2])
    q1 = got["quiz"][0]
    assert q1["chips"] == ["cp hot:아직 대기"] and "아직 안 가져갔어요" in q1["text"] and "1분 안에" not in q1["text"]
    assert q1["btns"][0] == ["한국전력", "true", False] and not any(b[2] for b in q1["btns"])  # 잠기지 않음 — 다시 고를 수 있음
    assert got["posts"][0][2]["args"] == {"question": Q1["q"], "answer": "<b>가스공사</b>", "key": Q1["key"]}
    assert "보냄 · 대기" in got["picked"][0]["text"] and all(b[2] for b in got["picked"][0]["btns"])  # 다시 보낸 답은 방금 것으로
    # 방금 보낸 답이라도 연결이 끊겼으면(20분 넘게 push 없음) '1분 안에'라고 하지 않음
    s2 = ssalmuk.Ssalmuk(FakeDb(), clock=lambda: now[0])
    s2.push(quiz_sample())
    now[0] += ssalmuk.STALE_AFTER + 5
    s2.act({"op": "quiz_answer", "args": {"question": Q1["q"], "answer": "한국전력", "key": Q1["key"]}})
    v = s2.view()
    assert v["stale"] is True
    assert _quiz_render(v)["quiz"][0]["chips"] == ["cp hot:아직 대기"]


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_sending_survives_a_reload_in_between() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(quiz_sample())
    got = _quiz_render(s.view(), pick=[0, 0], race=True)
    # 보내는 중에 끼어든 읽기(그 응답에는 아직 대기가 없음)가 '보냄 · 대기'를 지우면 단추가 다시 열려 두 번 누르게 됨
    assert "보냄 · 대기" in got["during"][0]["text"] and all(b[2] for b in got["during"][0]["btns"])
    assert "보냄 · 대기" in got["sent"][0]["text"] and len(got["posts"]) == 1
    assert "보냄 · 대기" not in got["after"][0]["text"] and not any(b[2] for b in got["after"][0]["btns"])  # 서버가 받은 뒤엔 서버 값대로


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_quiz_too_long_outside_text_not_drawn() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    st = quiz_sample()
    st["todo"][1].pop("quiz")
    st["todo"][0]["quiz"] = [{"q": "가" * (ssalmuk.QUIZ_Q_MAX + 1), "options": ["O", "X"], "key": "k1"},
                             {"q": "key 가 너무 긴 질문입니다", "options": ["O"], "key": "k" * (ssalmuk.QUIZ_KEY_MAX + 1)},
                             {"q": "보기 하나가 너무 긴 질문입니다", "options": ["가", "나" * (ssalmuk.QUIZ_A_MAX + 1)], "evidence": "근" * 900, "why": "까" * 900, "key": "k3"}]
    s.push(st)
    got = _quiz_render(s.view())
    assert len(got["quiz"]) == 1 and got["quiz"][0]["btns"] == [["가", "false", False]]  # 서버가 안 받을 길이는 그리지 않음
    assert len(got["quiz"][0]["text"]) < 600  # 근거·까닭은 200자까지
    # 안 된 이유(ack 결과 — 바깥 글)도 200자까지 · 태그는 글자로
    c = s.act({"op": "quiz_answer", "args": {"question": "보기 하나가 너무 긴 질문입니다", "answer": "가", "key": "k3"}})[1]["id"]
    s.ack({"ids": [c], "results": {c: "<img src=x onerror=alert(1)>" + "긴" * 900}})
    got = _quiz_render(s.view())
    assert len(got["quiz"][0]["warn"]) == 1 and got["quiz"][0]["warn"][0].startswith("저장 안 됨 — <img src=x onerror=alert(1)>")
    assert len(got["quiz"][0]["warn"][0]) < 230 and got["quiz"][0]["tags"] == 0


HEALTH = {"level": "warn", "at": "2026-11-13T22:30:00Z", "runId": "r1", "label": "07:23 정기", "status": "aborted",
          "checks": [{"id": "run", "level": "warn", "msg": "중단(서버 종료 신호) — 서버가 다시 시작·업데이트되며 실행을 멈춤 (지금까지 결과는 저장)", "since": "…",
                      "todo": "할 일 없음 — 다음 정기 실행에 저절로 다시 함"}],
          "funnel": None, "durationSec": 61, "version": "unknown", "codeHash": "d9dece8b9d"}


def test_clean_health_contract_fields_only() -> None:
    assert ssalmuk.clean_health(None) is None and ssalmuk.clean_health({}) is None
    for bad in ("정상", [], 3, {"level": "purple", "status": "weird"}, {"checks": []}):
        assert ssalmuk.clean_health({"health": bad}) is None, bad
    h = ssalmuk.clean_health({"health": HEALTH})
    assert h == {"level": "warn", "status": "aborted", "label": "07:23 정기", "at": "2026-11-13T22:30:00Z", "stopped": True, "llm": []}
    # 강제 종료(aborted + run bad)·run 줄 없음·다른 status 는 '중단' 아님
    forced = dict(HEALTH, level="bad", checks=[{"id": "run", "level": "bad", "msg": "강제 종료됨 (종료 코드 143) — 50분 넘음 또는 중단 신호"}])
    assert ssalmuk.clean_health({"health": forced})["stopped"] is False  # type: ignore[index]
    assert ssalmuk.clean_health({"health": dict(HEALTH, checks="x")})["stopped"] is False  # type: ignore[index]
    assert ssalmuk.clean_health({"health": dict(HEALTH, status="failed")})["stopped"] is False  # type: ignore[index]
    # 제미나이 줄 = checks 의 llm·llm-accuracy 만 (급한 것 먼저, 글로만) · health.llm 같은 계약에 없는 칸은 읽지 않음 · 모르는 status 는 None
    chk = [{"id": "llm-accuracy", "level": "warn", "msg": "퀴즈 정확도 60% (최근 7일 5개 폼, 6/10)", "todo": "확인 필요 카드에서 답 고르기"},
           {"id": "search", "level": "bad", "msg": "네이버 검색 오늘 쉼"}, "글", {"id": "llm", "level": "purple", "msg": "?"}, {"id": "llm", "level": "bad", "msg": 3},
           {"id": "llm", "level": "bad", "msg": "<b>Gemini</b> 오늘 쉼 — 키 문제 (401)", "todo": ["x"]}]
    h = ssalmuk.clean_health({"health": {"level": "bad", "status": "running?", "label": 7, "checks": chk, "llm": {"mode": "direct", "today": {"ok": 3}}}})
    assert h == {"level": "bad", "status": None, "label": "", "at": "", "stopped": False,
                 "llm": [{"id": "llm", "level": "bad", "msg": "Gemini 오늘 쉼 — 키 문제 (401)", "todo": ""},
                         {"id": "llm-accuracy", "level": "warn", "msg": "퀴즈 정확도 60% (최근 7일 5개 폼, 6/10)", "todo": "확인 필요 카드에서 답 고르기"}]}
    s = ssalmuk.Ssalmuk(FakeDb())
    s.push(sample())
    assert "health" not in s.view()  # 칸이 없으면 응답에도 없음
    s.push(sample(health=HEALTH))
    assert s.view()["health"]["stopped"] is True


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_health_line_aborted_is_yellow_and_llm_line() -> None:
    s = ssalmuk.Ssalmuk(FakeDb())
    failed_run = {"label": "07:23 정기", "started": "2026-11-13T22:23:00Z", "finished": "2026-11-13T22:30:20Z", "ok": False, "log": "run-1.log"}
    s.push(sample(lastRun=failed_run))
    base = _quiz_render(s.view())
    assert base["health"] == [] and " · 실패 · " in base["note"]
    # 서버 종료 신호로 멈춤 → 빨강(stop)이 아니라 노랑(hot) '중단(서버 종료 신호)' · 마지막 확인 줄도 '실패' 대신 '중단'
    s.push(sample(lastRun=failed_run, health=HEALTH))
    got = _quiz_render(s.view())
    assert len(got["health"]) == 1 and got["health"][0][0] == "cp hot"
    assert got["health"][0][1].startswith("중단(서버 종료 신호)마지막 실행 점검 · 07:23 정기 · ") and "다음 정기 확인 때 저절로 다시 해요" in got["health"][0][1]
    assert " · 중단(서버 종료 신호) · " in got["note"] and "실패" not in got["note"] and "cp stop" not in json.dumps(got["health"])
    # 50분 넘어 강제 종료된 aborted 는 그대로 빨강 · '실패'도 그대로
    s.push(sample(lastRun=failed_run, health=dict(HEALTH, level="bad", checks=[{"id": "run", "level": "bad", "msg": "강제 종료됨 (종료 코드 143)"}])))
    got = _quiz_render(s.view())
    assert [h[0] for h in got["health"]] == ["cp stop"] and got["health"][0][1].startswith("강제 종료마지막 실행 점검") and " · 실패 · " in got["note"]
    # 다른 실행의 '실패'는 '중단'으로 바꾸지 않음 (끝난 시각이 다름)
    s.push(sample(lastRun=dict(failed_run, finished="2026-11-13T19:30:00Z"), health=HEALTH))
    assert " · 실패 · " in _quiz_render(s.view())["note"]
    # 제미나이 답 한 줄 (checks 의 llm 줄이 있을 때만) · 정상 실행은 초록
    s.push(sample(health=dict(HEALTH, status="done", level="warn", checks=[
        {"id": "llm", "level": "warn", "msg": "Gemini 오늘 상한(40회)에 닿음 — 남은 퀴즈는 확인 필요로", "todo": ""},
        {"id": "llm-accuracy", "level": "warn", "msg": "퀴즈 정확도 60% (최근 7일 5개 폼, 6/10)", "todo": "확인 필요 카드에서 답 고르기"}])))
    got = _quiz_render(s.view())
    assert [h[0] for h in got["health"]] == ["cp hot", "cp hot"] and got["health"][0][1].startswith("주의마지막 실행 점검")
    assert got["health"][1][1] == "주의제미나이 답 · Gemini 오늘 상한(40회)에 닿음 — 남은 퀴즈는 확인 필요로 / 퀴즈 정확도 60% (최근 7일 5개 폼, 6/10) (할 일: 확인 필요 카드에서 답 고르기)"
    s.push(sample(health=dict(HEALTH, status="done", level="ok", checks=[])))
    got = _quiz_render(s.view())
    assert [h[0] for h in got["health"]] == ["cp ok"] and "제미나이" not in got["scr"]
    # 모르는 값은 무시 (줄 없음 · 화면 그대로)
    s.push(sample(lastRun=failed_run))
    v = s.view()
    v["health"] = {"level": "purple", "status": "weird", "llm": "x", "stopped": False}
    assert _quiz_render(v)["scr"] == base["scr"]


# ---------------------------------------------------------------- 7c) 네이버 API HUB 먼저 (11/15 — 주인 네이버 키는 개발자센터 키가 아니라 HUB 키)
HUB_HOST = "naverapihub.apigw.ntruss.com"


def never(req: Any) -> Any:
    raise AssertionError(f"예전 주소를 부르면 안 됨: {req.url}")


async def test_search_hub_first_with_ncp_headers_and_source(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    seen = naver(bot, never, hub=ok_blog)
    d = desk(bot)
    key = d.key()
    status, body = await mget(bot, f"{SEARCH}?q=돈좀요&display=5&sort=date", key)
    assert status == 200 and body["total"] == 42 and body["source"] == "naver_hub"
    assert body["items"][0] == {"title": '돈좀요 이벤트 "공지"', "link": "https://blog.example/1", "description": "응모 방법 & 경품",
                                "postdate": "20261110", "bloggername": "주최자"}  # 답 모양은 예전과 같음 (필드 source 만 더함)
    [req] = seen.hub
    assert seen == [] and (req.url.host, req.url.path) == (HUB_HOST, "/search/v1/blog")
    assert req.headers["x-ncp-apigw-api-key-id"] == NAVER_ID and req.headers["x-ncp-apigw-api-key"] == NAVER_SECRET
    assert "x-naver-client-id" not in req.headers and "x-naver-client-secret" not in req.headers
    assert dict(req.url.params) == {"query": "돈좀요", "display": "5", "sort": "date"}
    assert d.search_keysrc() == "dealbot_hub"
    assert json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))["dealbot"]["s"] == "HUB 200"
    await mget(bot, f"{SEARCH}?type=news&q=x", key)
    assert seen.hub[-1].url.path == "/search/v1/news" and seen == []
    _s, st, _h = await get_json(bot, f"{B}/state", session_headers(bot))
    assert st["search_proxy"]["keysrc"] == "dealbot_hub"
    for secret in (NAVER_ID, NAVER_SECRET):
        assert secret not in caplog.text and secret not in json.dumps(st) and secret not in json.dumps(body)
        assert secret not in (bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS) or "")


async def test_search_hub_refused_falls_back_to_old_address_then_next_key(bot: DealBot, tmp_path: Path) -> None:
    import httpx

    # HUB 401/403 → 같은 키로 예전 주소 한 번 (X-Naver 헤더) → 통하면 source 없음 (예전 그대로)
    for st in (401, 403):
        seen = naver(bot, ok_blog, hub=lambda r, st=st: httpx.Response(st, json={}))
        status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
        assert status == 200 and "source" not in body and len(seen.hub) == 1 and len(seen) == 1
        assert seen[0].url.host == "openapi.naver.com" and seen[0].headers["x-naver-client-id"] == NAVER_ID
        assert desk(bot).search_keysrc() == "dealbot"
    # 딜봇 키는 HUB·예전 둘 다 401 → 블로그 키로 HUB → 통함 (keysrc blog_hub · 다음엔 블로그 키 먼저)
    blog_env(bot, tmp_path)

    def hub(r: Any) -> Any:
        return ok_blog(r) if r.headers["x-ncp-apigw-api-key-id"] == BLOG_ID else httpx.Response(401, json={})
    seen = naver(bot, lambda r: httpx.Response(401, json={"errorMessage": "Not Exist Client ID"}), hub=hub)
    d = desk(bot)
    status, body = await mget(bot, f"{SEARCH}?q=x", d.key())
    assert status == 200 and body["source"] == "naver_hub" and d.search_keysrc() == "blog_hub"
    assert [r.headers["x-ncp-apigw-api-key-id"] for r in seen.hub] == [NAVER_ID, BLOG_ID] and len(seen) == 1
    keys = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert keys["dealbot"]["s"] == "401 키틀림" and keys["blog"]["s"] == "HUB 200"
    n = len(seen.hub)
    await mget(bot, f"{SEARCH}?q=x", d.key())
    assert [r.headers["x-ncp-apigw-api-key-id"] for r in seen.hub[n:]] == [BLOG_ID]  # 블로그 키 먼저 (_hub 떼고 같은 순서)
    # HUB 429·500·blog 404 같은 다른 오류는 예전 주소로 넘어가지 않고 502 그대로
    for st in (429, 500, 404):
        seen = naver(bot, never, keyed=True, hub=lambda r, st=st: httpx.Response(st, json={}))
        bot.settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "gone.env")
        status, body = await mget(bot, f"{SEARCH}?q=x", desk(bot).key())
        assert (status, body["code"], body["status"]) == (502, "naver_error", st) and seen == [] and len(seen.hub) == 1


async def test_search_webkr_hub_404_goes_to_kakao_for_a_day(bot: DealBot, tmp_path: Path) -> None:
    import httpx

    blog_env(bot, tmp_path)
    d = desk(bot)
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    key = d.key()

    def hub(r: Any) -> Any:
        return httpx.Response(404, json={}) if r.url.path.endswith("/webkr") else ok_blog(r)
    seen = naver(bot, kakao_or_401, kakao=KAKAO_KEY, hub=hub)
    # 먼저 블로그 검색이 HUB 로 통해 둠
    assert (await mget(bot, f"{SEARCH}?q=x", key))[1]["source"] == "naver_hub" and d.search_keysrc() == "dealbot_hub"
    # webkr: 딜봇 키 HUB 404 → 예전 401 → 블로그 키는 HUB 건너뛰고 예전 401 → 카카오 web
    status, body = await mget(bot, f"{SEARCH}?type=webkr&q=x", key)
    assert status == 200 and body["source"] == "kakao"
    assert [r.url.path for r in seen.hub] == ["/search/v1/blog", "/search/v1/webkr"]
    assert [r.url.host for r in seen] == ["openapi.naver.com", "openapi.naver.com", "dapi.kakao.com"] and seen[-1].url.path == "/v2/search/web"
    assert float(bot.db.kv_get(ssalmuk.KV_WEBKR_HUB404)) == now[0] + 86400
    assert d.search_keysrc() == "dealbot_hub"  # 웹문서만 카카오로 간 건 블로그·뉴스 순서를 안 바꿈
    keys = json.loads(bot.db.kv_get(ssalmuk.KV_SEARCH_KEYS))
    assert keys["dealbot"]["s"] == "HUB 200" and keys["kakao"]["s"] == 200  # 웹문서 401 이 블로그·뉴스에서 본 답을 덮지 않음
    # 하루 동안 webkr 은 곧장 카카오 (네이버 HUB·예전 주소 안 부름) · 블로그·뉴스는 그대로 HUB
    nh, no = len(seen.hub), len(seen)
    now[0] += 3600
    assert (await mget(bot, f"{SEARCH}?type=webkr&q=y", key))[1]["source"] == "kakao"
    assert len(seen.hub) == nh and [r.url.host for r in seen[no:]] == ["dapi.kakao.com"]
    assert (await mget(bot, f"{SEARCH}?type=news&q=y", key))[1]["source"] == "naver_hub" and seen.hub[-1].url.path == "/search/v1/news"
    # 카카오 키가 없으면 webkr 은 예전 주소만 (HUB 를 다시 두드리지 않음)
    seen = naver(bot, ok_blog, kakao=None, hub=hub)
    bot.settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "gone.env")
    status, body = await mget(bot, f"{SEARCH}?type=webkr&q=y", key)
    assert status == 200 and "source" not in body and seen.hub == [] and seen[0].url.path == "/v1/search/webkr.json"
    # 하루 지나면 webkr 도 HUB 를 다시 봄
    now[0] += 86401
    seen = naver(bot, ok_blog, kakao=None, hub=lambda r: ok_blog(r))
    assert (await mget(bot, f"{SEARCH}?type=webkr&q=y", key))[1]["source"] == "naver_hub"
    assert [r.url.path for r in seen.hub] == ["/search/v1/webkr"] and seen == []


def test_naverhub_routes_match_blog_auto() -> None:
    from dealbot.dashboard import naverhub

    assert naverhub.HUB == "https://naverapihub.apigw.ntruss.com" and naverhub.OLD == "https://openapi.naver.com"
    assert naverhub.url("blog", True) == naverhub.HUB + "/search/v1/blog" and naverhub.url("webkr", False) == naverhub.OLD + "/v1/search/webkr.json"
    assert naverhub.headers("i", "s", True) == {"X-NCP-APIGW-API-KEY-ID": "i", "X-NCP-APIGW-API-KEY": "s"}
    assert naverhub.headers("i", "s", False) == {"X-Naver-Client-Id": "i", "X-Naver-Client-Secret": "s"}
    assert [naverhub.hub_refused(t, c) for t, c in (("blog", 401), ("news", 403), ("webkr", 404), ("blog", 404), ("blog", 429))] == [
        True, True, True, False, False]


# ---------------------------------------------------------------- 11/18 숨김 스위치 (주인 '피클 주력 — 이벤트 참여 탭 필요 없음')
async def test_hidden_by_config_but_machine_api_still_works(bot: DealBot) -> None:
    """config.yaml dashboard.ssalmuk.show: false → boot ssalmuk.show 거짓 · push·commands·ack·화면 API 는 그대로."""
    assert bot.settings.dashboard.ssalmuk.show is False
    h = session_headers(bot)
    status, body, _ = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200 and body["ssalmuk"] == {"show": False}
    key = desk(bot).key()
    assert await mpost(bot, ssalmuk.PUSH_PATH, sample(), key) == (200, {"ok": True})
    assert (await mget(bot, ssalmuk.COMMANDS_PATH, key))[0] == 200
    assert (await get_json(bot, ssalmuk.STATE_PATH, h))[0] == 200
    bot.settings.dashboard.ssalmuk.show = True  # 되살리기
    assert (await get_json(bot, "/dashboard/api/state", h))[1]["ssalmuk"] == {"show": True}


HIDE_SHIM = SHIM.split("eval(fs.readFileSync(src, 'utf8'));", 1)[0] + r"""
let calls = 0; const f0 = global.fetch; global.fetch = async (u, o) => { calls++; return f0(u, o); };
eval(fs.readFileSync(src, 'utf8'));
(async () => {
  await new Promise(r => setImmediate(r));
  const before = main.children.length;
  location.hash = '#/todo/ev';
  await new Promise(r => setImmediate(r));
  console.log(JSON.stringify({ev: window.SBEvent, kids: main.children.map(c => c.attrs.id || c.id || c.className), before, calls, css: !!document.getElementById('ev-css')}));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_ssalmuk_js_hidden_attaches_nothing() -> None:
    v = json.dumps(ssalmuk.Ssalmuk(FakeDb()).view(), ensure_ascii=False)
    out = subprocess.run([NODE, "-e", HIDE_SHIM, str(STATIC / "ssalmuk.js"), v], capture_output=True, text=True, timeout=30,
                         env={**os.environ, "EV_HIDE": "1"})
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["ev"] is None and r["calls"] == 0 and not r["css"]  # 카드·화면·상태 읽기 없음 (할 일 '이벤트' 종류는 SBEvent 가 없어도 안 깨짐)
    assert r["kids"] == ["bn bad", "rtp", "itm"] and r["before"] == 3


@pytest.mark.skipif(NODE is None, reason="node 없음")
@pytest.mark.parametrize("show", [False, True])
def test_todohub_event_kind_follows_switch(show: bool) -> None:
    js = r"""
const [src, show] = process.argv.slice(1);
const boot = {textContent: JSON.stringify({me: {csrf: 't'}, ssalmuk: {show: show === '1'}})};
global.document = {readyState: 'loading', getElementById: id => (id === 'boot' ? boot : null), addEventListener() {}};
global.window = global; global.location = {hash: '#/todo'};
eval(require('fs').readFileSync(src, 'utf8'));
console.log(JSON.stringify(window.SBHub.CATS.map(c => c[0])));
"""
    out = subprocess.run([NODE, "-e", js, str(STATIC / "todohub.js"), "1" if show else "0"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    cats = json.loads(out.stdout)
    assert ("ev" in cats) is show and cats[:7] == ["issue", "finance", "hotdeal", "deals", "sns", "shorts", "etc"]
