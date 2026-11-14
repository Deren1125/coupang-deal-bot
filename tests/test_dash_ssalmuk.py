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
import re
import shutil
import subprocess
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


async def test_only_four_paths_open_without_login(bot: DealBot) -> None:
    pub = set(api_mod.DashApi.public_paths())
    assert {p for p in pub if p.startswith(B)} == {ssalmuk.PUSH_PATH, ssalmuk.COMMANDS_PATH, ssalmuk.ACK_PATH, ssalmuk.SEARCH_PATH}
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
                 "search_proxy": {"keysrc": None, "today_count": 0, "keys": {}}}


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
const ids = {main, ttl, toast, boot: {textContent: JSON.stringify({me: {csrf: 'tok'}})}};
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


def naver(bot: DealBot, handler: Any, *, keyed: bool = True) -> list[Any]:
    import httpx

    seen: list[Any] = []

    def h(req: Any) -> Any:
        seen.append(req)
        return handler(req)

    bot.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    s = bot.settings.secrets
    s.naver_client_id, s.naver_client_secret = (NAVER_ID, NAVER_SECRET) if keyed else (None, None)
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
    assert keys["blog"] == 200 and keys["dealbot"] == "-" and isinstance(keys["at"], int)  # 둘째 호출은 블로그 키만
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
    assert keys["dealbot"] == 401 and keys["blog"] == "no_file"
    js = (Path(ssalmuk.__file__).parent / "static" / "ssalmuk.js").read_text(encoding="utf-8")
    assert "키 파일 못 읽음" in js and "keyDiag(p && p.keys)" in js
