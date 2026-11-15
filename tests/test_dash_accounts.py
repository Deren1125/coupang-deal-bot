"""연결 탭 '계정 정리 (구글·인스타)' (주인 11/15 — '구글 계정이 여러 개 필요할 듯한데, 계정별 사용처를 정리해서 대시보드에 간단한 페이지로').

1) 표 상수 (accounts_map.py): 여섯 줄 · 규칙 셋 · 근거 한 줄 — 쓰임만, 이메일·웹 주소·토큰·비밀번호 꼴 없음 (섞인 줄은 view 가 뺌).
2) GET /dashboard/api/accounts: 로그인 아래 · 상수 그대로.
3) 화면 (static/accounts.js — 가짜 DOM): 접힌 한 줄 → 펼치면 표 + 규칙 + 근거 · #/connect/accounts 는 펼치고 그 칸으로 · 글자는 텍스트 노드.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import accounts_map, shell

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


# ---------------------------------------------------------------- 1) 표
def test_table_has_the_six_accounts_three_rules_and_no_secret_shapes() -> None:
    v = accounts_map.view()
    assert v["title"] == "계정 정리 (구글·인스타)" and v["cols"] == ["계정", "쓰는 곳", "누가·어디서 쓰나", "상태"]
    assert [r["name"][:1] for r in v["rows"]] == ["①", "②", "③", "④", "⑤", "인"] and v["hidden"] == 0
    assert all(set(r) == {"name", "use", "who", "status", "state"} and all(isinstance(x, str) and x for x in r.values()) for r in v["rows"])
    assert {r["state"] for r in v["rows"]} <= {"on", "todo", "wait"}
    assert len(v["rules"]) == 3 and "한 계정 = 한 층" in v["rules"][0] and "버림 계정은 업로드에 절대 안 씀" == v["rules"][2]
    assert v["source"].startswith("근거: 쇼츠 서버 config/upload_channels.yaml layers")
    by = {r["name"][:1]: r for r in v["rows"]}
    assert "업로드·채널 0" in by["④"]["use"] and "쌀먹" in by["⑤"]["use"] and by["①"]["status"] == "사용 중"
    # 화면에 나가는 글 어디에도 이메일·웹 주소·긴 영숫자(토큰)·비밀번호 꼴이 없음
    whole = json.dumps(v, ensure_ascii=False)
    for text in [whole, *(x for r in accounts_map.ACCOUNTS for x in r.values()), *accounts_map.RULES, accounts_map.SOURCE]:
        assert not accounts_map.leaks(text), text
    assert "gmail" not in whole.lower() and "://" not in whole and ".com" not in whole.lower()


@pytest.mark.parametrize("bad", ["owner.name@gmail.com", "https://accounts.google.com/x", "www.youtube.com/@x",
                                 "ya29.A0AfH6SMBxxxxxxxxxxxxxxxxxxxxxxx", "비밀번호: hunter2", "token=abc"])
def test_leak_shapes_are_caught_and_such_rows_never_reach_the_screen(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert accounts_map.leaks(bad)
    for ok in ("@derenpick.look 룩북", "쇼츠 서버 업로드 토큰(OAuth) · 블로그 서버", "config/upload_channels.yaml layers (11/15)", "11/22~23 전"):
        assert not accounts_map.leaks(ok), ok
    row = {"name": "⑥ 잘못 적은 줄", "use": bad, "who": "어딘가", "status": "사용 중", "state": "on"}
    monkeypatch.setattr(accounts_map, "ACCOUNTS", (*accounts_map.ACCOUNTS, row))
    monkeypatch.setattr(accounts_map, "RULES", (*accounts_map.RULES, bad))
    v = accounts_map.view()
    assert len(v["rows"]) == 6 and v["hidden"] == 1 and len(v["rules"]) == 3 and bad not in json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------- 2) API
async def test_api_serves_the_table_behind_login(bot: DealBot) -> None:
    status, body, headers = await get_json(bot, "/dashboard/api/accounts", session_headers(bot))
    assert status == 200 and body == accounts_map.view() and headers["Cache-Control"] == "no-cache"
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/accounts", anon))[0] == 401


# ---------------------------------------------------------------- 3) 화면
def test_screen_file_is_wired_and_safe() -> None:
    js = (STATIC / "accounts.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "XMLHttpRequest", "localStorage", "http://", "https://", "href"):
        assert bad not in js, bad
    assert js.count("fetch(") == 1 and "fetch('/dashboard/api/accounts'" in js and "method:" not in js  # 읽기만
    assert "/^#\\/connect\\/accounts(\\/|$)/" in js and "MutationObserver" in js
    assert shell.ACCOUNTS_JS == "accounts.js" and f'<script defer src="{shell.asset_url("accounts.js")}"></script>' in shell.page({})
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "accounts.js")], capture_output=True).returncode == 0


SHIM = r"""
const fs = require('fs');
class N {
  constructor(t) { this.tag = t; this.kids = []; this.attrs = {}; this.ev = {}; this.className = ''; this.nodeType = 1; this.parentNode = null; this.scrolled = 0; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, f) { this.ev[k] = f; }
  _in(x) { if (typeof x === 'string') return {nodeType: 3, text: x}; if (x.parentNode) x.parentNode.kids = x.parentNode.kids.filter(k => k !== x); x.parentNode = this; return x; }
  append(...c) { for (const x of c) this.kids.push(this._in(x)); }
  prepend(...c) { this.kids.unshift(...c.map(x => this._in(x))); }
  after(x) { const p = this.parentNode; x = p._in(x); p.kids.splice(p.kids.indexOf(this) + 1, 0, x); }
  replaceChildren(...c) { for (const k of this.kids) if (k.nodeType === 1) k.parentNode = null; this.kids = []; this.append(...c); }
  scrollIntoView() { this.scrolled++; }
}
const text = n => (n.nodeType === 3 ? n.text : n.kids.map(text).join(''));
const all = (n, f, o = []) => { if (n && n.nodeType === 1) { if (f(n)) o.push(n); n.kids.forEach(k => all(k, f, o)); } return o; };
const head = new N('head'), main = new N('main'), calls = [], wev = {};
main.attrs.id = 'main';
let observer = null;
global.MutationObserver = class { constructor(f) { observer = f; } observe() {} };
global.document = {createElement: t => new N(t), head, readyState: 'complete', addEventListener() {},
  getElementById: id => all(head, n => n.attrs.id === id)[0] || all(main, n => n.attrs.id === id)[0] || null};
global.location = {hash: '#/todo'};
global.window = {addEventListener: (k, f) => { wev[k] = f; }};
const DATA = JSON.parse(process.argv[2]);
let fail = false;
global.fetch = async (url, opt) => { calls.push([url, (opt || {}).method || 'GET']); return fail ? {status: 500, json: async () => ({})} : {status: 200, json: async () => DATA}; };
const timers = [];
global.setTimeout = f => { timers.push(f); return timers.length; };
const tick = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); while (timers.length) timers.shift()(); for (let i = 0; i < 5; i++) await Promise.resolve(); };
const go = async h => { location.hash = h; main.replaceChildren(new N('p')); observer(); wev.hashchange(); await tick(); };
const box = () => all(main, n => n.attrs.id === 'acm')[0] || null;
const snap = () => {
  const b = box();
  if (!b) return null;
  const btn = all(b, x => x.tag === 'button')[0];
  return {idx: main.kids.indexOf(b), head: text(btn), open: btn.attrs['aria-expanded'], th: all(b, x => x.tag === 'th').map(text),
    rows: all(b, x => x.tag === 'tr').slice(1).map(tr => [tr.className, ...tr.kids.map(text)]), rules: all(b, x => x.tag === 'li').map(text),
    notes: all(b, x => x.tag === 'p').map(text), tags: [...new Set(all(b, () => true).map(x => x.tag))].sort(), scrolled: b.scrolled, click: () => btn.ev.click()};
};
(async () => {
  const out = {};
  (0, eval)(fs.readFileSync(process.argv[1], 'utf8'));
  await tick();
  out.todo = snap();                       // 연결 탭이 아니면 칸 없음
  await go('#/connect');
  out.closed = snap(); out.calls0 = calls.length;
  // 로그인 관리 칸(#lgp)이 있으면 그 바로 아래
  const lg = new N('section'); lg.attrs.id = 'lgp';
  location.hash = '#/connect'; main.replaceChildren(lg, new N('p')); observer(); await tick();
  out.afterLg = snap().idx;
  fail = true;
  snap().click(); await tick();
  out.failed = snap();
  snap().click(); await tick();            // 접고
  fail = false;
  snap().click(); await tick();            // 다시 펼치면 다시 받음
  out.opened = snap(); out.calls1 = calls.slice();
  snap().click(); await tick();
  out.reclosed = snap().open;
  snap().click(); await tick();
  out.calls2 = calls.length;               // 한 번 받은 표는 다시 받지 않음
  snap().click(); await tick();
  await go('#/todo');
  out.gone = snap();
  await go('#/connect/accounts');          // 바로 가기: 펼치고 그 칸으로
  out.direct = snap();
  snap().click(); await tick();
  observer(); await tick();                // 주인이 접으면 같은 주소에서는 다시 펼치지 않음
  out.stay = [snap().open, snap().scrolled];
  out.css = all(head, n => n.tag === 'style').length;
  console.log(JSON.stringify(out));
})();
"""


@needs_node
def test_fold_opens_the_table_and_the_shortcut_jumps_to_it() -> None:
    evil = '<img src=x onerror="alert(1)">'
    data = accounts_map.view()
    data["rows"] = [*data["rows"], {"name": evil, "use": evil, "who": 7, "status": None, "state": "x y"}, None, "junk"]
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "accounts.js"), json.dumps(data, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["todo"] is None and got["gone"] is None
    c = got["closed"]
    assert c["head"] == "계정 정리 (구글·인스타)" and c["open"] == "false" and c["rows"] == [] and c["idx"] == 0 and got["calls0"] == 0  # 접힌 채 — 서버도 안 부름
    assert got["afterLg"] == 1
    assert got["failed"]["open"] == "true" and got["failed"]["notes"] == ["불러오지 못했어요 — 접었다 다시 펼쳐 주세요"]
    o = got["opened"]
    assert got["calls1"] == [["/dashboard/api/accounts", "GET"]] * 2 and got["calls2"] == 2 and got["reclosed"] == "false"
    assert o["th"] == ["계정", "쓰는 곳", "누가·어디서 쓰나", "상태"] and len(o["rows"]) == 7
    want = accounts_map.view()["rows"]
    assert o["rows"][:6] == [[r["state"], r["name"], r["use"], r["who"], r["status"]] for r in want]
    assert o["rows"][6] == ["", evil, evil, "", ""]  # 이상한 값은 글자 그대로 (태그로 안 바뀜) · 모르는 상태는 색 없음
    assert o["rules"] == list(accounts_map.RULES) and o["notes"] == [accounts_map.SOURCE]
    assert set(o["tags"]) <= {"section", "div", "button", "table", "thead", "tbody", "tr", "th", "td", "ol", "li", "p"}  # 링크·그림·입력 칸 없음
    d = got["direct"]
    assert d["open"] == "true" and len(d["rows"]) == 7 and d["scrolled"] == 1
    assert got["stay"] == ["false", 1] and got["css"] == 1
