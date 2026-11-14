"""올리기 할 일 (주인 11/08 — 롱폼·쇼츠·룩북 릴스·설명글을 합친 대시보드에, to do list 처럼).

1) clean: 쇼츠 서버 upload_tasks.json 을 엄격히 거름 (http 파일 주소·틀린 id·겹친 id·모르는 필드 버림 · 태그 정리 · 목록/items/tasks 모양).
2) tick: 10분마다만 읽음 · 404 = 아직 없음(빈 목록) · 응답 없음 = 지난 목록 그대로 + 한 줄.
3) view: 오늘·내일·이번 주·나중·지난 것 묶음 · 룩북 칸과 같은 편은 lb · 할 일 '쇼츠 올리기 N' (오늘·밀린 것, 체크·룩북 빼고).
4) uploads.done {id, done}: 봇 DB 만 · 틀린 args 는 bad_args · GET /dashboard/api/uploads (로그인 아래 · no-store).
5) shorts.js: 바깥 주소·innerHTML 없음 · 한국어 단추 · sns.js 가 meta sb-shorts 로 받음 · app.js SNS 칸 '올리기'.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import api as dash_api
from dealbot.dashboard import shell, uploadplan

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
TZ = ZoneInfo("Asia/Seoul")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def iso(d: datetime) -> str:
    return d.isoformat()


def put(bot: DealBot, items: list[dict], state: str = "ok", tried: float = 0.0) -> None:
    tz = ZoneInfo(bot.settings.app.timezone)
    bot.db.kv_set(uploadplan.CACHE_KEY, json.dumps({"state": state, "items": uploadplan.clean(items, tz), "tried": tried, "at": tried}))


# ---------------------------------------------------------------- 1) clean
def test_clean_filters_strictly() -> None:
    raw = {"items": [
        {"id": "a-1", "when": "2031-03-05T19:00:00+09:00", "platform": "youtube", "what": "쇼츠 A", "file_url": "https://x.example/a.mp4",
         "title": "제목", "description": "설명", "tags": ["#코디", "데일리룩", "코디"], "order": 2, "note": "메모", "secret": "버림"},
        {"id": "a-1", "when": "2031-03-05T20:00:00+09:00", "title": "같은 id"},
        {"id": "bad id!", "when": "2031-03-05T21:00:00", "platform": "naver_clip", "file_url": "http://x.example/b.mp4", "tags": "가 나,다"},
        "줄 아님", {"when": "날짜 아님", "platform": "틱톡?"}]}
    got = uploadplan.clean(raw, TZ)
    assert [x["id"] for x in got][0] == "a-1" and len(got) == 3
    a = got[0]
    assert a["plat"] == "유튜브" and a["tags"] == ["코디", "데일리룩"] and a["file_url"].startswith("https://") and "secret" not in a
    b = got[1]
    assert re.match(r"^h[0-9a-f]{12}$", b["id"]) and b["file_url"] == "" and b["plat"] == "네이버 클립" and b["tags"] == ["가", "나", "다"]
    assert b["when"] == datetime(2031, 3, 5, 21, tzinfo=TZ).timestamp()  # 시간대 없는 시각은 대시보드 시간대
    assert got[2]["when"] is None
    assert uploadplan.clean([{"id": "x"}], TZ)[0]["id"] == "x" and uploadplan.clean({"tasks": [{"id": "y"}]}, TZ)[0]["id"] == "y"
    with pytest.raises(ValueError):
        uploadplan.clean({"nope": 1}, TZ)


# ---------------------------------------------------------------- 2) tick
async def test_tick_every_ten_minutes_and_keeps_old_list(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    replies = [("ok", uploadplan.clean([{"id": "a", "when": "2031-03-05T19:00:00+09:00"}], TZ)), ("down", None), ("none", None)]

    async def fake(tz: ZoneInfo):  # type: ignore[no-untyped-def]
        calls.append(1)
        return replies[len(calls) - 1]

    monkeypatch.setattr(uploadplan, "fetch", fake)
    assert await uploadplan.tick(bot, now=1000.0) == "ok 1"
    assert await uploadplan.tick(bot, now=1000.0 + 599) is None and len(calls) == 1
    assert await uploadplan.tick(bot, now=1000.0 + 600) == "down 1"  # 지난 목록 그대로
    v = uploadplan.view(bot, now=1000.0 + 600)
    assert "쇼츠 서버 응답 없음" in v["err"] and not v["ok"]
    assert await uploadplan.tick(bot, now=1000.0 + 1200) == "none 0"
    v = uploadplan.view(bot, now=1000.0 + 1200)
    assert v["err"] is None and v["empty"] and v["groups"] == []


def test_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(uploadplan.URL_ENV, raising=False)
    assert uploadplan.url() == uploadplan.DEFAULT_URL
    monkeypatch.setenv(uploadplan.URL_ENV, "http://plain.example/x.json")
    assert uploadplan.url() == uploadplan.DEFAULT_URL  # https 만
    monkeypatch.setenv(uploadplan.URL_ENV, "https://other.example/x.json")
    assert uploadplan.url() == "https://other.example/x.json"


# ---------------------------------------------------------------- 3) view · 할 일
def test_view_groups_lookbook_and_todo(bot: DealBot) -> None:
    tz = ZoneInfo(bot.settings.app.timezone)
    now_dt = datetime(2031, 3, 5, 14, 0, tzinfo=tz)
    now = now_dt.timestamp()
    put(bot, [
        {"id": "late", "when": iso(now_dt - timedelta(days=1)), "what": "쇼츠 B", "title": "어제 것"},
        {"id": "old", "when": iso(now_dt - timedelta(days=30)), "title": "너무 옛날"},
        {"id": "t2", "when": iso(now_dt.replace(hour=21)), "what": "롱폼", "order": 1},
        {"id": "t1", "when": iso(now_dt.replace(hour=19)), "what": "쇼츠 A", "platform": "instagram"},
        {"id": "lb-1", "when": iso(now_dt.replace(hour=20)), "what": "룩북 릴스"},
        {"id": "tm", "when": iso(now_dt + timedelta(days=1)), "what": "쇼츠 C"},
        {"id": "wk", "when": iso(now_dt + timedelta(days=4)), "what": "쇼츠 D"},
        {"id": "far", "when": iso(now_dt + timedelta(days=20))},
        {"id": "nodate"}])
    lb = {"items": [{"id": "lb-1"}]}
    v = uploadplan.view(bot, now=now, lookbook=lb)
    g = {x["key"]: [i["id"] for i in x["items"]] for x in v["groups"]}
    assert list(g) == ["today", "tomorrow", "week", "later", "past"]
    assert g == {"past": ["late"], "today": ["t1", "lb-1", "t2"], "tomorrow": ["tm"], "week": ["wk"], "later": ["far", "nodate"]}
    today = v["groups"][0]["items"]
    assert today[0]["hm"] == "19:00" and today[0]["when_text"] == "3/5(수) 19:00" and today[0]["plat"] == "인스타" and today[1]["lb"]
    assert v["todo"] == 3  # late·t1·t2 (룩북 같은 편 빼고)
    put(bot, [{"id": "t1", "when": iso(now_dt.replace(hour=19)), "gate_ok": False, "platform": "youtube_shorts", "platform_label": "유튜브 쇼츠"},
              {"id": "t2", "when": iso(now_dt.replace(hour=20)), "platform": "youtube_long"}])
    v = uploadplan.view(bot, now=now)
    a, b = v["groups"][0]["items"]
    assert (a["gate"], a["plat"], b["gate"], b["plat"], v["todo"]) == (False, "유튜브 쇼츠", True, "유튜브 (롱폼)", 1)  # 검수 대기는 안 셈
    put(bot, [
        {"id": "late", "when": iso(now_dt - timedelta(days=1)), "what": "쇼츠 B", "title": "어제 것"},
        {"id": "t2", "when": iso(now_dt.replace(hour=21)), "what": "롱폼", "order": 1},
        {"id": "t1", "when": iso(now_dt.replace(hour=19)), "what": "쇼츠 A", "platform": "instagram"},
        {"id": "lb-1", "when": iso(now_dt.replace(hour=20)), "what": "룩북 릴스"}])
    uploadplan.set_done(bot.db, "t1", True)
    assert uploadplan.count(bot, now, lb) == 2 and uploadplan.view(bot, now=now, lookbook=lb)["groups"][0]["items"][0]["done"]

    def goto(label: str, route: str) -> dict:
        return {"label": label, "goto": route}

    row = uploadplan.todo_item(bot, now, goto, lb)
    assert row["title"] == "숏폼·롱폼 올리기 2" and row["primary"]["goto"] == "#/sns/shorts" and row["iid"] == "uploads"
    uploadplan.set_done(bot.db, "t2", True)
    uploadplan.set_done(bot.db, "late", True)
    assert uploadplan.todo_item(bot, now, goto, lb) is None
    uploadplan.set_done(bot.db, "t2", False)
    assert uploadplan.done_ids(bot.db) == {"t1", "late"}


def test_state_todo_has_uploads_line(bot: DealBot) -> None:
    now = datetime.now(ZoneInfo(bot.settings.app.timezone))
    put(bot, [{"id": "n1", "when": iso(now - timedelta(minutes=5)), "what": "쇼츠 A"}])
    rows = [i for i in bot.dash_state.items() if i["iid"] == "uploads"]
    assert rows and rows[0]["title"] == "숏폼·롱폼 올리기 1"


# ---------------------------------------------------------------- 4) 체크 · API
async def test_uploads_done_action_and_api(bot: DealBot) -> None:
    now = datetime.now(ZoneInfo(bot.settings.app.timezone))
    put(bot, [{"id": "s-1", "when": iso(now), "what": "쇼츠 A", "title": "t", "description": "d", "tags": ["a"]}])
    h = session_headers(bot)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "uploads.done", "args": {"id": "s-1", "done": True}}, h)
    assert status == 200 and got["status"] == "ok" and got["text"] == "올렸어요로 표시했어요"
    assert json.loads(bot.db.kv_get(uploadplan.DONE_KEY)) == ["s-1"]
    status, v, headers = await get_json(bot, "/dashboard/api/uploads", h)
    assert status == 200 and headers["Cache-Control"] == "no-store" and v["todo"] == 0
    assert any(i["id"] == "s-1" and i["done"] for g in v["groups"] for i in g["items"])
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "uploads.done", "args": {"id": "s-1", "done": False}}, h)
    assert status == 200 and got["text"] == "체크를 풀었어요" and json.loads(bot.db.kv_get(uploadplan.DONE_KEY)) == []
    for bad in ({"id": "BAD ID", "done": True}, {"id": "a", "done": "yes"}, {"id": 3, "done": True}, {"done": True}, {"id": "x" * 65, "done": True}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "uploads.done", "args": bad}, h)
        assert status == 400 and got["code"] == "bad_args", bad
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/uploads", anon))[0] == 401


# ---------------------------------------------------------------- 5) shorts.js
def test_shorts_js_local_safe_korean_and_wired() -> None:
    js = (STATIC / "shorts.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js and "new Function" not in js
    for word in ("/dashboard/api/uploads", "uploads.done", "W.SBShorts", "'파일 받기'", "'제목 복사'", "'설명글+태그 복사'", "지난 것",
                 "rel: 'noopener noreferrer'", "clipboard"):
        assert word in js, word
    for label in re.findall(r"btn\('[^']*', '([^']*)'", js):
        assert re.search(r"[가-힣]", label), label
    sns = (STATIC / "sns.js").read_text(encoding="utf-8")
    assert "meta[name=sb-shorts]" in sns and "W.SBShorts" in sns and "k === 'shorts'" in sns
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "['shorts', '숏폼/롱폼']" in app and "'lookbook', 'shorts']" in app
    html = shell.page({"me": {}})
    assert re.search(r'<meta name="sb-shorts" content="/dashboard/static/shorts\.js\?v=[0-9a-f]{10}">', html)
    assert html.count("<script src=") == 1
    assert "shorts.js" in dash_api.APP_FILES
    node = shutil.which("node")
    if node:
        for f in ("shorts.js", "sns.js"):
            assert subprocess.run([node, "--check", str(STATIC / f)], capture_output=True).returncode == 0, f


# 진짜 크롬 (ops 원칙 24 — '[object'·'undefined'·'NaN' 없음 · 콘솔 오류 없음 · 폰 너비에서 가로로 안 넘침 · 단추 44px)
BROWSER = r"""
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [js, css, view, shot] = [fs.readFileSync(process.argv[1], 'utf8'), fs.readFileSync(process.argv[2], 'utf8'), process.argv[3], process.argv[4]];
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 360, height: 900}});
  const errors = [], posts = [];
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push(String(e)));
  const html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<link rel="stylesheet" href="/dashboard/static/app.css"></head><body><main id="main"><div id="b"></div></main></body></html>';
  await p.route('http://dash.test/**', async r => {
    const u = new URL(r.request().url());
    const json = (status, d) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(d)});
    if (u.pathname === '/dashboard/') return r.fulfill({contentType: 'text/html', body: html});
    if (u.pathname.endsWith('app.css')) return r.fulfill({contentType: 'text/css', body: css});
    if (u.pathname.endsWith('shorts.js')) return r.fulfill({contentType: 'text/javascript', body: js});
    if (u.pathname === '/dashboard/api/uploads') return json(200, JSON.parse(view));
    if (u.pathname === '/dashboard/api/action') { posts.push(JSON.parse(r.request().postData())); return json(200, {status: 'ok', text: ''}); }
    return r.fulfill({status: 404, body: ''});
  });
  await p.goto('http://dash.test/dashboard/');
  await p.addScriptTag({url: '/dashboard/static/shorts.js'});
  await p.evaluate(() => {
    const D = document;
    const el = (t, a, ...kids) => { const e = D.createElement(t); for (const n in a || {}) { const v = a[n]; if (v == null || v === false) continue;
      if (n === 'class') e.className = v; else if (n.startsWith('on')) e.addEventListener(n.slice(2), v); else e.setAttribute(n, v === true ? '' : v); }
      for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c)); return e; };
    const btn = (cls, label, on, x) => el('button', Object.assign({class: cls, type: 'button', onclick: on}, x), label);
    const api = async (path, opt = {}) => { const r = await fetch(path, {method: opt.method || 'GET', headers: {'Content-Type': 'application/json'},
      body: opt.body ? JSON.stringify(opt.body) : undefined}); return {status: r.status, data: await r.json()}; };
    window.SBShorts.view(D.getElementById('b'), {el, btn, api, toast: () => {}, S: {}});
  });
  await p.waitForSelector('.upr');
  const out = {};
  out.text = await p.$eval('#b', e => e.innerText);
  out.wide = await p.evaluate(() => document.documentElement.scrollWidth);
  out.btn = await p.$$eval('.upb .pb', es => Math.min(...es.map(e => Math.round(e.getBoundingClientRect().height))));
  await p.click('.upk input');
  await p.waitForFunction(() => document.querySelector('.upr.d'));
  if (shot) await p.screenshot({path: shot, fullPage: true});
  out.posts = posts; out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def test_real_browser_renders_clean(bot: DealBot, tmp_path: Path) -> None:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    tz = ZoneInfo(bot.settings.app.timezone)
    now_dt = datetime(2031, 3, 5, 14, 0, tzinfo=tz)
    put(bot, [
        {"id": "t1", "when": iso(now_dt.replace(hour=19)), "platform": "youtube", "what": "쇼츠 A", "file_url": "https://x.example/a.mp4",
         "title": "겨울 니트 코디 3가지 — 아주 긴 제목이 폰에서 어떻게 꺾이는지 보는 줄", "description": "설명", "tags": ["코디", "데일리룩"],
         "note": "음악은 앱에서"},
        {"id": "t2", "when": iso(now_dt.replace(hour=21)), "platform": "naver_clip", "what": "롱폼", "title": "롱폼 제목"},
        {"id": "t3", "when": iso(now_dt.replace(hour=22)), "platform": "youtube_long", "platform_label": "유튜브 (롱폼)", "what": "롱폼",
         "title": "검수 전", "gate_ok": False, "file_url": "https://x.example/c.mp4", "note": "통과 전엔 올리지 마세요\n두 번째 줄"},
        {"id": "lb-1", "when": iso(now_dt.replace(hour=20)), "platform": "instagram", "what": "룩북 릴스"},
        {"id": "old", "when": iso(now_dt - timedelta(days=1)), "what": "쇼츠 B", "title": "어제 것"}])
    view = uploadplan.view(bot, now=now_dt.timestamp(), lookbook={"items": [{"id": "lb-1"}]})
    out = subprocess.run([shutil.which("node") or "node", "-e", BROWSER, str(STATIC / "shorts.js"), str(STATIC / "app.css"),
                          json.dumps(view, ensure_ascii=False), os.environ.get("UPLOADS_SHOT", "")],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["errors"] == []
    for bad in ("[object", "undefined", "NaN", "null"):
        assert bad not in got["text"], (bad, got["text"])
    for word in ("오늘 · 남은 2", "19:00", "유튜브", "쇼츠 A", "파일 받기", "제목 복사", "설명글+태그 복사", "룩북 릴스 칸에서 올리기", "지난 것 1개", "검수 대기 · 아직 올리지 마세요", "유튜브 (롱폼)", "영상 보기"):
        assert word in got["text"], word
    assert got["wide"] <= 360 and got["btn"] >= 44
    (sent,) = got["posts"]
    assert sent["action"] == "uploads.done" and sent["args"] == {"id": "t1", "done": True}


# ---------------------------------------------------------------- 6) 유튜브 받기 계정 카드 (11/14 쇼츠 총괄)
YT_SHIM = r"""
const out = {};
class T { constructor(s) { this.data = s; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.className = ''; this._t = ''; this.style = {}; this.hidden = false; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener() {}
  append(...cs) { cs.forEach(c => this.children.push(c.nodeType ? c : new T(String(c)))); }
  prepend() {} replaceChildren(...cs) { this.children = []; this.append(...cs); }
  all(f, a = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) a.push(c); c.all(f, a); } return a; }
}
const ids = {main: new E('main'), boot: new E('script'), toast: new E('div')};
ids.boot.textContent = JSON.stringify({me: {csrf: 't'}});
ids.main.append(new E('p'));
global.location = {hash: '#/todo'};
global.window = {SBHub: {}, addEventListener() {}, dispatchEvent() {}, localStorage: {getItem: () => 'blog'}, navigator: {}};
global.MutationObserver = class { observe() {} };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), getElementById: i => ids[i] || null, createElement: t => new E(t), addEventListener() {}};
const UP = JSON.parse(process.argv[2]), fail = process.argv[3] === 'fail';
global.fetch = async url => {
  if (fail && url === '/dashboard/api/uploads') throw new Error('net');
  return url === '/dashboard/api/routine' ? {status: 200, json: async () => ({day: 'd', today: true, slots: [], total: 0, done: 0})} : {status: 200, json: async () => UP};
};
(async () => {
  require(process.argv[1]);
  await new Promise(r => setTimeout(r, 20));
  const R = window.SBRoutine, p = R.part('shorts', '숏폼', [], []);
  out.n = R.cnt.shorts.n; out.card = p.length ? p[0].all(c => c.attrs['data-id'] === 'yt-alive').map(c => c.textContent) : [];
  out.links = p.length ? p[0].all(c => c.tagName === 'A').map(a => [a.textContent, a.attrs.href, a.attrs.target, a.attrs.rel]) : [];
  out.hot = p.length ? p[0].all(c => c.className === 'hu').map(c => c.textContent) : [];
  console.log(JSON.stringify(out)); process.exit(0);
})();
"""


def _yt_view(bot: DealBot, raw: dict | None) -> dict:
    db_c = uploadplan.cache(bot.db)
    if raw is not None:
        db_c["yt"] = uploadplan.yt_clean(raw, TZ)
    bot.db.kv_set(uploadplan.CACHE_KEY, json.dumps(db_c))
    return uploadplan.view(bot)


def test_yt_clean_kinds_and_time() -> None:
    at = datetime(2031, 3, 5, 14, 7, tzinfo=TZ).isoformat()
    assert uploadplan.yt_clean({"ok": True, "kind": "ok", "why": "", "at": at}, TZ) == {"kind": "ok", "why": "", "ts": datetime(2031, 3, 5, 14, 7, tzinfo=TZ).timestamp(), "page": "/ytlogin.html"}
    assert uploadplan.yt_clean({"ok": False, "kind": "login", "why": "쿠키 만료", "at": at}, TZ)["kind"] == "login"
    assert uploadplan.yt_clean({"ok": False, "kind": "weird", "at": 1900000000}, TZ)["kind"] == "other"
    assert uploadplan.yt_clean({"ok": True}, TZ)["ts"] is None and uploadplan.yt_clean({"ok": True}, TZ)["kind"] == "ok"
    assert uploadplan.yt_clean({"ok": True, "login_page": "/ytlogin2.html"}, TZ)["page"] == "/ytlogin2.html"
    for bad in ("https://evil.example/x", "//evil.example/x", "/../x", "x", 5):
        assert uploadplan.yt_clean({"ok": True, "login_page": bad}, TZ)["page"] == "/ytlogin.html"
    now = datetime(2031, 3, 5, 10, 0, tzinfo=TZ).timestamp()
    assert [uploadplan.yt_when(datetime(2031, 3, d, 8, 50, tzinfo=TZ).timestamp(), now, TZ) for d in (5, 4, 2)] == ["오늘 08:50", "어제 08:50", "3/2 08:50"]
    assert uploadplan.yt_when(None, now, TZ) == ""
    with pytest.raises(ValueError):
        uploadplan.yt_clean([1], TZ)


async def test_tick_yt_absent_failure_keeps_last(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [("ok", {"kind": "login", "why": "x", "ts": 1.0, "page": "/ytlogin.html"}), ("down", None), ("none", None)]
    n = []

    async def fake(tz: ZoneInfo):  # type: ignore[no-untyped-def]
        n.append(1)
        return replies[len(n) - 1]

    monkeypatch.setattr(uploadplan, "fetch_yt", fake)
    assert uploadplan.view(bot)["yt"] is None  # 아직 없음 = 조용히 아무것도 없음
    assert await uploadplan.tick_yt(bot, now=1000.0) == "ok"
    assert await uploadplan.tick_yt(bot, now=1100.0) is None and len(n) == 1  # 5분 안엔 다시 안 읽음
    assert uploadplan.view(bot)["yt"]["kind"] == "login" and uploadplan.view(bot)["yt"]["login_url"].endswith("/ytlogin.html")
    assert await uploadplan.tick_yt(bot, now=1300.0) == "down" and uploadplan.view(bot)["yt"]["kind"] == "login"  # 실패 = 지난 값
    assert await uploadplan.tick_yt(bot, now=1700.0) == "none" and uploadplan.view(bot)["yt"] is None


async def test_fetch_yt_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    real = httpx.AsyncClient

    def mk(handler):  # type: ignore[no-untyped-def]
        return lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"})

    for code, body, want in ((200, '{"ok": true, "kind": "ok", "at": "2031-03-05T14:07:00+09:00"}', "ok"), (404, "", "none"), (500, "", "down"), (200, "not json", "down")):
        monkeypatch.setattr(httpx, "AsyncClient", mk(lambda r, c=code, b=body: httpx.Response(c, content=b)))
        assert (await uploadplan.fetch_yt(TZ))[0] == want

    def boom(r):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("x")

    monkeypatch.setattr(httpx, "AsyncClient", mk(boom))
    assert await uploadplan.fetch_yt(TZ) == ("down", None)


@pytest.mark.parametrize("raw,card,n,hot", [
    ({"ok": True, "kind": "ok", "why": "", "at": "2031-03-05T14:07:00+09:00"}, "유튜브 받기 계정✅ 정상 · 3/5 14:07", 0, []),
    ({"ok": False, "kind": "login", "why": "로그인 풀림", "at": "2031-03-05T14:07:00+09:00"}, "유튜브 받기 계정❗다시 로그인 필요 · 3/5 14:07다시 로그인", 1, ["급함 1"]),
    ({"ok": False, "kind": "runtime", "why": "크롬 죽음", "at": "2031-03-05T14:07:00+09:00"}, "유튜브 받기 계정⚠ 서버 문제 — 총괄이 보고 있어요 (크롬 죽음) · 3/5 14:07", 0, []),
    ({"ok": False, "kind": "other", "why": "", "at": "2031-03-05T14:07:00+09:00"}, "유튜브 받기 계정⚠ 서버 문제 — 총괄이 보고 있어요 · 3/5 14:07", 0, []),
    (None, None, 0, []),
])
def test_yt_card_in_shorts_category(bot: DealBot, raw: dict | None, card: str | None, n: int, hot: list[str]) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    view = _yt_view(bot, raw)
    out = subprocess.run([node, "-e", YT_SHIM, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["card"] == ([card] if card else []) and got["n"] == n and got["hot"] == hot
    if raw and raw["kind"] == "login":  # 주인이 누를 때만 새 탭 (자동으로 열지 않음)
        assert got["links"] == [["다시 로그인", "https://3-35-144-76.sslip.io/ytlogin.html", "_blank", "noopener noreferrer"]]
    else:
        assert got["links"] == []
    assert "window.open" not in (STATIC / "routine.js").read_text(encoding="utf-8")


def test_yt_card_fetch_failure_does_not_break(bot: DealBot) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    out = subprocess.run([node, "-e", YT_SHIM, str(STATIC / "routine.js"), "{}", "fail"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["card"] == []
