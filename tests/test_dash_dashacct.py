"""대시보드 로그인 보기·바꾸기 (주인 11/16 — '로그인한 다음에 아이디·비번 볼 수 있는 기능, 거기서 수정도').

1) API (dashacct.py): 로그인 없으면 401 · 다른 출처·토큰 없음 403 · 기본 인증 403 · 1분 5번 · no-store · 로그·접속 기록에 값 없음.
2) 바꾸기: 규칙 위반 거절 · 저장 뒤 새 비밀번호로 로그인, 옛 비밀번호 거절 · 지금 세션 유지, 다른 세션 풀림 · 파일 600.
3) 복구 길: 파일을 지우면 .env 값 · 서버에서 .env 를 새로 정하면(setenv.sh) 그 값 · 깨진 파일은 무시.
4) 화면 (static/dashacct.js): 셸 연결 · 위험한 API 없음 · 10초 뒤 가림 · 확인 화면을 거쳐야 저장.
모든 값은 가짜.
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from dash_helpers import ORIGIN, PASSWORD, USER, get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import dashacct, shell
from dealbot.dashboard.dashacct import LoginFile

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
NEW = "maple-harbor-lantern-58"  # 가짜 새 비밀번호
REVEAL, SAVE = dashacct.REVEAL_PATH, dashacct.SAVE_PATH


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def login_file(bot: DealBot) -> Path:
    return bot.settings.data_dir / dashacct.FILE_NAME


def basic(user: str, password: str) -> dict[str, str]:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"authorization": f"Basic {raw}", ":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.50"}


async def opens(bot: DealBot, headers: dict[str, str]) -> bool:
    return (await bot.web.dispatch("GET", "/dashboard/data.json", headers))[0] == 200


def audit_rows(bot: DealBot) -> list[tuple[str, str, str]]:
    rows = bot.db._q("SELECT action, args_redacted, result_code FROM dash_audit WHERE action LIKE 'dashacct.%' ORDER BY rowid")
    return [(r["action"], r["args_redacted"], r["result_code"]) for r in rows]


# ---------------------------------------------------------------- 1) API
async def test_needs_login_same_origin_token_and_a_session(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7", "content-type": "application/json"}
    assert (await bot.web.dispatch("GET", dashacct.API_PATH, anon))[0] == 401
    for path in (REVEAL, SAVE):
        status, body, _ = await post_json(bot, path, {}, anon)
        assert status == 401 and PASSWORD not in json.dumps(body)
    h = session_headers(bot)
    status, body, headers = await get_json(bot, dashacct.API_PATH, h)
    assert status == 200 and body == {"user": USER, "custom": False} and headers["Cache-Control"] == "no-store"
    for path in (REVEAL, SAVE):  # 다른 출처 · 다른 사이트 · 토큰 없음 · 틀린 토큰
        for bad in (dict(h, origin="https://evil.example"), dict(h, **{"sec-fetch-site": "cross-site"}),
                    session_headers(bot, csrf=False), dict(h, **{"x-dash-token": "x" * 43})):
            status, body, headers = await post_json(bot, path, {"password": NEW, "password2": NEW}, bad)
            assert status == 403 and PASSWORD not in json.dumps(body, ensure_ascii=False)  # 다른 사이트는 웹 서버가 먼저 막음
    # 기본 인증(브라우저 창)은 보기·바꾸기를 못 함 — 로그인 세션만
    b = dict(basic(USER, PASSWORD), origin=ORIGIN, **{"sec-fetch-site": "same-origin", "content-type": "application/json"})
    b["x-dash-token"] = bot.dashboard.api.csrf_token(b)
    for path in (REVEAL, SAVE):
        status, body, _ = await post_json(bot, path, {"password": NEW, "password2": NEW}, b)
        assert status == 403 and body["code"] == "session" and PASSWORD not in json.dumps(body)
    assert not login_file(bot).exists()


async def test_reveal_shows_once_per_press_five_a_minute_and_logs_no_values(bot: DealBot, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    h = session_headers(bot)
    status, body, headers = await post_json(bot, REVEAL, {}, h)
    assert status == 200 and body == {"user": USER, "password": PASSWORD, "seconds": 10}
    assert headers["Cache-Control"] == "no-store"
    for _ in range(4):
        assert (await post_json(bot, REVEAL, {}, h))[0] == 200
    status, body, headers = await post_json(bot, REVEAL, {}, h)  # 여섯 번째
    assert status == 429 and body["code"] == "rate" and "password" not in body and headers["Cache-Control"] == "no-store"
    assert (await post_json(bot, REVEAL, {}, session_headers(bot)))[0] == 429  # 다른 세션으로도 (모두 합쳐 1분 5번)
    rows = audit_rows(bot)
    assert [r[0] for r in rows] == ["dashacct.reveal"] * 7 and [r[2] for r in rows] == ["done"] * 5 + ["rate"] * 2
    assert all(r[1] == "{}" for r in rows)
    assert PASSWORD not in caplog.text


# ---------------------------------------------------------------- 2) 바꾸기
@pytest.mark.parametrize(("body", "why"), [
    ({"password": "short-pw-1", "password2": "short-pw-1"}, "12자 이상"),
    ({"password": "has space in it 12", "password2": "has space in it 12"}, "영문·숫자"),
    ({"password": "quote'dollar$slash", "password2": "quote'dollar$slash"}, "영문·숫자"),
    ({"password": "한글이-들어간-비밀번호-12", "password2": "한글이-들어간-비밀번호-12"}, "영문·숫자"),
    ({"password": "a" * 129, "password2": "a" * 129}, "128자까지"),
    ({"password": NEW, "password2": NEW + "x"}, "서로 달라요"),
    ({"password": NEW}, "두 번"),
    ({"password": NEW, "password2": NEW, "user": "bad:name"}, "아이디"),
    ({"password": NEW, "password2": NEW, "user": "has space"}, "아이디"),
    ({"password": NEW, "password2": NEW, "user": "x" * 65}, "아이디"),
    ({"password": ["x"], "password2": NEW}, "두 번"),
])
async def test_save_refuses_values_that_break_the_rules(bot: DealBot, body: dict, why: str) -> None:
    status, out, headers = await post_json(bot, SAVE, body, session_headers(bot))
    assert status == 400 and why in out["text"] and headers["Cache-Control"] == "no-store"
    assert not login_file(bot).exists()
    assert await opens(bot, basic(USER, PASSWORD))


async def test_save_applies_at_once_keeps_this_session_and_drops_the_others(bot: DealBot, tmp_path: Path,
                                                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    me, other = session_headers(bot), session_headers(bot)
    assert await opens(bot, me) and await opens(bot, other)
    status, out, headers = await post_json(bot, SAVE, {"user": "", "password": NEW, "password2": NEW}, me)
    assert status == 200 and out["status"] == "ok" and out["user"] == USER and out["kept"] is True
    assert headers["Cache-Control"] == "no-store" and NEW not in json.dumps(out, ensure_ascii=False)
    # 다시 켜지 않아도 바로: 새 비밀번호로 들어가고 옛 비밀번호는 거절
    assert await opens(bot, basic(USER, NEW)) and not await opens(bot, basic(USER, PASSWORD))
    assert await opens(bot, me) and not await opens(bot, other)  # 이 기기는 그대로 · 다른 기기는 풀림
    assert (await post_json(bot, REVEAL, {}, me))[1]["password"] == NEW  # 토큰도 그대로 됨
    assert (await get_json(bot, dashacct.API_PATH, me))[1] == {"user": USER, "custom": True}
    # 파일: 저장소 밖 데이터 폴더 · 권한 600 · 임시 파일 안 남음
    path = login_file(bot)
    assert path.is_file() and tmp_path in path.parents and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")] == []
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["user"] == USER and saved["password"] == NEW and PASSWORD not in path.read_text(encoding="utf-8")
    # 로그·접속 기록·kv 에 값 없음
    assert NEW not in caplog.text and PASSWORD not in caplog.text and "화면에서 바꿈" in caplog.text
    assert audit_rows(bot)[0] == ("dashacct.save", "{}", "done")
    kv = "\n".join(f"{r['key']}={r['value']}" for r in bot.db._q("SELECT key, value FROM kv"))
    assert NEW not in kv and PASSWORD not in kv
    # 아이디도 바꿀 수 있음
    status, out, _ = await post_json(bot, SAVE, {"user": "owner.two", "password": NEW, "password2": NEW}, me)
    assert status == 200 and out["user"] == "owner.two"
    assert await opens(bot, basic("owner.two", NEW)) and not await opens(bot, basic(USER, NEW)) and await opens(bot, me)
    # 새로 켜도(같은 .env) 파일 값이 먼저
    again = DealBot(bot.settings)
    try:
        assert await opens(again, basic("owner.two", NEW)) and not await opens(again, basic(USER, PASSWORD))
        assert await opens(again, me)
    finally:
        again.db.close()


async def test_login_form_takes_the_new_password(bot: DealBot) -> None:
    from dealbot.dashboard.auth import check_credentials

    assert (await post_json(bot, SAVE, {"password": NEW, "password2": NEW}, session_headers(bot)))[0] == 200
    d = bot.dashboard
    assert check_credentials(USER.encode(), NEW.encode(), d._user, d._password)
    assert not check_credentials(USER.encode(), PASSWORD.encode(), d._user, d._password)


# ---------------------------------------------------------------- 3) 복구 길
async def test_deleting_the_file_goes_back_to_the_server_value(bot: DealBot) -> None:
    me = session_headers(bot)
    assert (await post_json(bot, SAVE, {"password": NEW, "password2": NEW}, me))[0] == 200
    login_file(bot).unlink()
    assert await opens(bot, basic(USER, PASSWORD)) and not await opens(bot, basic(USER, NEW))
    assert (await get_json(bot, dashacct.API_PATH, session_headers(bot)))[1] == {"user": USER, "custom": False}


def test_a_newer_server_value_wins_and_broken_files_are_ignored(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "data" / dashacct.FILE_NAME
    f = LoginFile(path, USER, PASSWORD)
    assert f.current() == (USER, PASSWORD) and not f.custom
    f.save(USER, NEW)
    assert f.current() == (USER, NEW) and f.custom and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert LoginFile(path, USER, PASSWORD).current() == (USER, NEW)  # 다시 켜도 그대로
    # 서버에서 setenv.sh 로 새로 정하고 다시 켬 → .env 가 더 새것
    fresh = LoginFile(path, USER, "reset-on-server-0099")
    assert fresh.current() == (USER, "reset-on-server-0099") and not fresh.custom
    for junk in ("", "{", "[1]", json.dumps({"password": "short"}), json.dumps({"user": "a:b", "password": NEW}),
                 json.dumps({"password": 7}), "x" * 5000):
        path.write_text(junk, encoding="utf-8")
        g = LoginFile(path, USER, PASSWORD)
        assert g.current() == (USER, PASSWORD) and not g.custom, junk[:20]
    path.write_text(json.dumps({"password": NEW}), encoding="utf-8")  # 손으로 만든 파일 (지문 없음) → 그대로 씀, 아이디는 .env
    assert LoginFile(path, USER, PASSWORD).current() == (USER, NEW)
    with pytest.raises(ValueError):
        f.save(USER, "short")
    with pytest.raises(ValueError):
        f.save("bad name", NEW)
    assert NEW not in caplog.text and PASSWORD not in caplog.text
    assert LoginFile(None, USER, PASSWORD).current() == (USER, PASSWORD)


def test_login_file_is_outside_git() -> None:
    root = Path(__file__).resolve().parents[1]
    assert "data/" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    if shutil.which("git") and (root / ".git").exists():
        out = subprocess.run(["git", "check-ignore", "-q", f"data/{dashacct.FILE_NAME}"], cwd=root)
        assert out.returncode == 0


# ---------------------------------------------------------------- 4) 화면
def test_screen_file_is_wired_and_safe() -> None:
    js = (STATIC / "dashacct.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "XMLHttpRequest", "localStorage",
                "sessionStorage", "http://", "https://", "href", "console.", "clipboard"):
        assert bad not in js, bad
    assert js.count("fetch(") == 2 and "'X-Dash-Token': csrf()" in js and "cache: 'no-store'" in js
    assert "/^#\\/connect\\/login(\\/|$)/" in js and "MutationObserver" in js
    assert "bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD" in js
    assert shell.DASHACCT_JS == "dashacct.js" and f'<script defer src="{shell.asset_url("dashacct.js")}"></script>' in shell.page({})
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "dashacct.js")], capture_output=True).returncode == 0


SHIM = r"""
const fs = require('fs');
class N {
  constructor(t) { this.tag = t; this.kids = []; this.attrs = {}; this.ev = {}; this.className = ''; this.nodeType = 1; this.parentNode = null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, f) { this.ev[k] = f; }
  _in(x) { if (typeof x === 'string') return {nodeType: 3, text: x}; if (x.parentNode) x.parentNode.kids = x.parentNode.kids.filter(k => k !== x); x.parentNode = this; return x; }
  append(...c) { for (const x of c) this.kids.push(this._in(x)); }
  prepend(...c) { this.kids.unshift(...c.map(x => this._in(x))); }
  after(x) { const p = this.parentNode; x = p._in(x); p.kids.splice(p.kids.indexOf(this) + 1, 0, x); }
  replaceChildren(...c) { for (const k of this.kids) if (k.nodeType === 1) k.parentNode = null; this.kids = []; this.append(...c); }
  scrollIntoView() {}
}
const text = n => (n.nodeType === 3 ? n.text : n.kids.map(text).join(''));
const all = (n, f, o = []) => { if (n && n.nodeType === 1) { if (f(n)) o.push(n); n.kids.forEach(k => all(k, f, o)); } return o; };
const head = new N('head'), main = new N('main'), calls = [], wev = {}, dev = {};
main.attrs.id = 'main';
const boot = new N('script'); boot.attrs.id = 'boot'; boot.textContent = JSON.stringify({me: {csrf: 'tok-1'}});
let observer = null, ticker = null;
global.MutationObserver = class { constructor(f) { observer = f; } observe() {} };
global.document = {createElement: t => new N(t), head, readyState: 'complete', visibilityState: 'visible', addEventListener: (k, f) => { dev[k] = f; },
  getElementById: id => (id === 'boot' ? boot : all(head, n => n.attrs.id === id)[0] || all(main, n => n.attrs.id === id)[0] || null)};
global.location = {hash: '#/connect'};
global.window = {addEventListener: (k, f) => { wev[k] = f; }};
global.setTimeout = () => 0;
global.setInterval = f => { ticker = f; return 1; };
global.clearInterval = () => { ticker = null; };
const PW = process.argv[2], NEW = process.argv[3];
global.fetch = async (url, opt = {}) => {
  calls.push([url, opt.method || 'GET', opt.body || '', (opt.headers || {})['X-Dash-Token'] || '']);
  if (url.endsWith('/reveal')) return {status: 200, json: async () => ({user: 'owner', password: PW, seconds: 10})};
  if (url.endsWith('/save')) return {status: 200, json: async () => ({status: 'ok', user: 'owner', custom: true, text: '바꿨어요'})};
  return {status: 200, json: async () => ({user: 'owner', custom: false})};
};
const wait = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
const box = () => all(main, n => n.attrs.id === 'dac')[0];
const press = async label => { const b = all(box(), x => x.tag === 'button' && text(x) === label)[0]; if (!b) throw new Error('단추 없음: ' + label); b.ev.click(); await wait(); };
const type = (i, v) => { const x = all(box(), n => n.tag === 'input')[i]; x.ev.input({currentTarget: {value: v}}); };
(async () => {
  const out = {};
  (0, eval)(fs.readFileSync(process.argv[1], 'utf8'));
  await wait();
  out.closed = [text(box()), calls.length];
  await press('대시보드 로그인 (아이디·비밀번호)');
  out.opened = text(box()); out.calls0 = calls.slice();
  await press('보기');
  out.shown = text(box());
  for (let i = 0; i < 9; i++) ticker();
  out.at9 = text(box()).includes(PW);
  ticker();
  out.at10 = text(box()); out.ticking = ticker !== null;
  await press('보기');
  document.visibilityState = 'hidden'; dev.visibilitychange();
  out.away = text(box()).includes(PW);
  document.visibilityState = 'visible';
  await press('바꾸기');
  out.inputs = all(box(), n => n.tag === 'input').map(n => [n.attrs.type, n.attrs.autocomplete]);
  type(1, 'short'); type(2, 'short');
  await press('다음 — 새 값 확인');
  out.short = text(box());
  type(1, NEW); type(2, NEW + 'x');
  await press('다음 — 새 값 확인');
  out.differ = text(box());
  type(2, NEW);
  const before = calls.length;
  await press('다음 — 새 값 확인');
  out.confirm = text(box()); out.noPostYet = calls.length === before;
  await press('이대로 저장');
  out.saved = text(box()); out.last = calls[calls.length - 1];
  out.tags = [...new Set(all(box(), () => true).map(x => x.tag))].sort();
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_password_hides_after_ten_seconds_and_save_needs_the_confirm_step() -> None:
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "dashacct.js"), PASSWORD, NEW], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["closed"] == ["대시보드 로그인 (아이디·비밀번호)", 0]  # 접힌 채 — 서버도 안 부름
    assert "owner" in got["opened"] and "●●●●●●●●" in got["opened"] and PASSWORD not in got["opened"]
    assert "setenv.sh DASHBOARD_PASSWORD" in got["opened"]
    assert got["calls0"] == [["/dashboard/api/dashacct", "GET", "", ""]]
    assert PASSWORD in got["shown"] and "10초 뒤 다시 가려요" in got["shown"]
    assert got["at9"] is True and PASSWORD not in got["at10"] and "●●●●●●●●" in got["at10"] and got["ticking"] is False
    assert got["away"] is False  # 화면을 떠나면 바로 가림
    assert got["inputs"] == [["text", "username"], ["password", "new-password"], ["password", "new-password"]]
    assert "12자 이상으로" in got["short"] and "서로 달라요" in got["differ"]
    assert got["noPostYet"] is True and f"비밀번호: {NEW}" in got["confirm"] and "아이디: owner" in got["confirm"]
    assert got["last"] == ["/dashboard/api/dashacct/save", "POST", json.dumps({"user": "", "password": NEW, "password2": NEW}, separators=(",", ":")), "tok-1"]
    assert "바꿨어요" in got["saved"] and NEW not in got["saved"] and "●●●●●●●●" in got["saved"]
    assert set(got["tags"]) <= {"section", "div", "button", "span", "p", "label", "input"}
