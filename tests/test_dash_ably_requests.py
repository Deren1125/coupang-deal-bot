"""대시보드 '오늘 할 일' — 에이블리 링크 부탁 (주인 10/28: 서버는 에이블리를 열지 않는다. 주인이 폰 에이블리 앱에서 링크를 만들어 붙여 넣음).

1) 기록 파일 칸 ably_requests {n, rows: [{rid, pid, title, piece, name, price, shop, url, due}], note} 를 엄격히 거름
   (blogauto._ably_requests): rid 모양·글 번호 맞춤 · 상품 주소는 https 에이블리만 · 틀린 줄만 빼고 30줄까지 · 칸이 없으면 None.
2) 대시보드 일 blog.similar_links 에 rid (선택 — '<글 번호>-<상품 번호>', 링크 하나) · 앱 '공유' 문구째 붙여 넣으면 첫 에이블리 주소만.
   rid 가 없으면 계약(action_similar_links.json) 그대로 — test_dash_similar.
3) routine.view 의 ably 칸 (발행 시각 글 due_text) · routine.js 가 그리는 줄 (가짜 DOM) · 진짜 크롬에서 한 번 (playwright 가 있을 때만).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import routine
from dealbot.dashboard.actions import ActionError, ably_link_in, clean_args
from dealbot.dashboard.blogauto import _ably_requests, parse_export
from test_dash_api_actions import act
from test_dash_kkumi import _playwright_env
from test_dash_routine import SLOTS, at, item, tz_now
from test_dash_state import write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
NOTE = "에이블리 앱 > 크리에이터에서 링크를 만들어 붙여 넣어 주세요 (서버는 에이블리를 열지 않아요)"
BLURB = "[에이블리] 크림 플리츠 스커트 12,900원\n친구에게 공유해요!https://applink.a-bly.com/aBcD1234에서 확인하세요 (앱)"


def row(pid: int = 41, sno: str = "12345678", **kw: object) -> dict:
    return {"rid": f"{pid}-{sno}", "pid": pid, "title": "가을 데이트룩 정리", "piece": "스커트", "name": "크림 플리츠 스커트",
            "price": 12900, "shop": "에이블리 · 모니크샵", "url": f"https://m.a-bly.com/goods/{sno}", "due": None, **kw}


def export(*rows: dict, n: int | None = None) -> dict:
    return {"n": len(rows) if n is None else n, "rows": list(rows), "note": NOTE}


# ---------------------------------------------------------------- 1) 거름
def test_parser_keeps_good_rows_and_drops_bad_ones() -> None:
    assert _ably_requests(None) is None and _ably_requests({"rows": []}) is None  # 예전 Blog-Auto · n 없음
    assert _ably_requests({"n": -1, "rows": []}) is None and _ably_requests({"n": 1, "rows": "x"}) is None
    assert _ably_requests({"n": True, "rows": []}) is None
    long = "가" * 300
    got = _ably_requests(export(
        row(due=1_800_000_000.5, title=long, name=long + "​", shop=None),
        row(sno="7", url="https://a-bly.com/goods/7", price=None, piece=""),
        row(rid="41-12345678"),  # 같은 rid 두 번째
        row(sno="8", url="http://m.a-bly.com/goods/8"), row(sno="9", url="https://m.a-bly.com.evil.example/goods/9"),
        row(sno="10", url="https://zigzag.kr/p/10"), row(sno="11", url="javascript:alert(1)"), row(sno="12", url='https://m.a-bly.com/"x'),
        row(sno="13", rid="42-13"), row(sno="14", rid="41 14"), row(sno="15", pid=True), row(sno="16", pid="41"),
        row(sno="17", price=-1), row(sno="18", price="12,900"), row(sno="19", price=True), row(sno="20", due="곧"), row(sno="21", due=5),
        row(sno="22", due=float("nan")), row(sno="23", name="", piece=""), "줄 아님", None, n=23))
    assert got is not None and got["note"] == NOTE and got["n"] == 2 and [r["rid"] for r in got["rows"]] == ["41-12345678", "41-7"]
    one, two = got["rows"]
    assert len(one["title"]) == 60 and one["title"].endswith("…") and len(one["name"]) == 80 and "​" not in one["name"]
    assert one["shop"] == "에이블리" and one["due"] == 1_800_000_000.5 and one["price"] == 12900 and one["piece"] == "스커트"
    assert two["price"] == 0 and two["piece"] == "" and two["due"] is None and two["url"] == "https://a-bly.com/goods/7"
    assert set(one) == {"rid", "pid", "title", "piece", "name", "price", "shop", "url", "due"}
    # 이름이 없으면 옷 이름으로 · 줄은 30개까지 · 쪽지가 없으면 ''
    assert _ably_requests(export(row(name=None)))["rows"][0]["name"] == "스커트"
    many = _ably_requests({"n": 40, "rows": [row(sno=str(i)) for i in range(40)]})
    assert many["n"] == 30 and len(many["rows"]) == 30 and many["note"] == ""


def test_parse_export_reads_the_part() -> None:
    data = fixture("export_v2.json")
    assert parse_export(data, "Asia/Seoul").v2["ably_requests"] is None
    data["ably_requests"] = export(row())
    assert parse_export(data, "Asia/Seoul").v2["ably_requests"]["rows"][0]["rid"] == "41-12345678"


# ---------------------------------------------------------------- 2) 일 (blog.similar_links + rid)
def test_rid_is_optional_and_checked() -> None:
    base = {"blog": "issue", "pid": 41, "links": ["https://applink.a-bly.com/aBcD1234"]}
    assert clean_args("blog.similar_links", dict(base)) == base  # rid 없음 → 그대로
    out = clean_args("blog.similar_links", dict(base, rid="41-12345678"))
    assert out == {**base, "rid": "41-12345678"} and list(out)[-1] == "rid"
    for bad in ("42-12345678", "41-", "-1", "41-a b", "41-" + "x" * 41, "1234567890-1", 41, "", ["41-1"]):
        with pytest.raises(ActionError):
            clean_args("blog.similar_links", dict(base, rid=bad))
    with pytest.raises(ActionError):  # 부탁 하나엔 링크 하나
        clean_args("blog.similar_links", dict(base, links=["https://m.a-bly.com/goods/1", "https://m.a-bly.com/goods/2"], rid="41-1"))
    with pytest.raises(ActionError):  # 글 칸(text)으로 보내도 링크 하나
        clean_args("blog.similar_links", {"pid": 41, "text": "https://m.a-bly.com/goods/1\nhttps://m.a-bly.com/goods/2", "rid": "41-1"})


def test_share_blurb_gives_the_first_ably_link() -> None:
    got = clean_args("blog.similar_links", {"blog": "issue", "pid": 41, "links": [BLURB], "rid": "41-12345678"})
    assert got == {"blog": "issue", "pid": 41, "links": ["https://applink.a-bly.com/aBcD1234"], "rid": "41-12345678"}
    assert ably_link_in("보기 https://zigzag.kr/x 와 (https://m.a-bly.com/goods/5).") == "https://m.a-bly.com/goods/5"
    assert ably_link_in("  https://a-bly.com/app/goods/6?ref=share  ") == "https://a-bly.com/app/goods/6?ref=share"
    assert ably_link_in("에이블리에서 보기 https://ably.onelink.me/AbCd/x1") == "https://ably.onelink.me/AbCd/x1"  # 앱 공유 짧은 주소
    with pytest.raises(ActionError):
        ably_link_in("https://other.onelink.me/AbCd/x1")
    for bad in ("링크 없음", "http://m.a-bly.com/goods/1 보기", "https://a-bly.com.evil.example/x 보기", "https://zigzag.kr/x 보기",
                "가" * 4001):
        with pytest.raises(ActionError) as e:
            clean_args("blog.similar_links", {"pid": 41, "links": [bad], "rid": "41-1"})
        assert e.value.code == "bad_args"
    with pytest.raises(ActionError) as e:
        ably_link_in("공유 https://zigzag.kr/x")
    assert e.value.text == "붙여 넣은 글에서 에이블리 링크를 찾지 못했어요"
    # 여러 링크면 공유 문구로 보지 않음 (하나씩 https 주소여야)
    with pytest.raises(ActionError):
        clean_args("blog.similar_links", {"pid": 41, "links": [BLURB, "https://m.a-bly.com/goods/1"]})


async def test_rid_reaches_the_spool(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    status, body = await act(bot, "blog.similar_links", {"blog": "issue", "pid": 41, "links": [BLURB], "rid": "41-12345678"},
                             session_headers(bot))
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert f["action"] == "similar_links" and f["target"] == "post-issue-41"
    assert f["args"] == {"blog": "issue", "pid": 41, "links": ["https://applink.a-bly.com/aBcD1234"], "rid": "41-12345678"}


# ---------------------------------------------------------------- 3) 보기
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)


def due_at(bot: DealBot, day: str, hm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()


def two_rows(bot: DealBot, day: str) -> dict:
    return export(row(due=due_at(bot, day, "14:30")), row(sno="777", piece="니트", name="루즈핏 니트", price=0, title="", shop="에이블리"))


def test_view_adds_due_text_and_hides_when_empty(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    write_export(tmp_path / "blogauto.json", ably_requests=two_rows(bot, day))
    v = routine.view(bot, now=at(bot, day, "10:00"))
    assert v["total"] == 0 and v["ably"]["n"] == 2 and v["ably"]["note"] == NOTE  # 할 일 목록이 없어도 부탁 칸은 있음
    assert [r["due_text"] for r in v["ably"]["rows"]] == ["14:30", ""]
    assert routine.view(bot, now=at(bot, day, "15:00"))["ably"]["rows"][0]["due_text"] == ""  # 지난 시각은 안 씀
    prev = (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat()
    assert routine.view(bot, now=at(bot, prev, "22:00"))["ably"]["rows"][0]["due_text"].endswith(" 14:30")  # 다른 날이면 'M/D HH:MM'
    write_export(tmp_path / "blogauto.json", ably_requests=export(n=0))
    assert routine.view(bot)["ably"] is None
    write_export(tmp_path / "blogauto.json")
    assert routine.view(bot)["ably"] is None


# 작은 가짜 DOM (jsdom 없이) — test_dash_routine 의 것에 입력 칸 값·contains·결과 읽기(/dashboard/api/actions)를 더함
SHIM = r"""
const calls = [], out = {};
let observer = null;
class T { constructor(s) { this.data = s; this.parentNode = null; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.ls = {}; this.parentNode = null; this.className = ''; this._t = ''; this.hidden = false; this.style = {}; this.value = ''; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  contains(x) { for (; x; x = x.parentNode) if (x === this) return true; return false; }
  _add(c, front) { if (!c.nodeType) c = new T(String(c)); if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; front ? this.children.unshift(c) : this.children.push(c); }
  append(...cs) { cs.forEach(c => this._add(c, false)); this._n(); }
  prepend(...cs) { cs.reverse().forEach(c => this._add(c, true)); this._n(); }
  replaceChildren(...cs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; cs.forEach(c => this._add(c, false)); this._n(); }
  _n() { if (observer && observer.t === this) queueMicrotask(observer.cb); }
  all(f, acc = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) acc.push(c); c.all(f, acc); } return acc; }
  click() { (this.ls.click || []).forEach(f => f({currentTarget: this})); }
}
const ids = {}, mk = (t, id) => { const e = new E(t); ids[id] = e; return e; };
const main = mk('main', 'main'), boot = mk('script', 'boot'), toast = mk('div', 'toast');
boot.textContent = JSON.stringify({me: {csrf: 'tok123'}});
global.location = {hash: '#/todo'};
global.window = {addEventListener() {}, navigator: {}};
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.t = t; observer = this; } };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), getElementById: i => ids[i] || null,
  createElement: t => new E(t), addEventListener() {}, activeElement: null};
const VIEW = JSON.parse(process.argv[2]), MODE = process.argv[3];
const res = (status, d) => ({status, json: async () => d});
global.fetch = async (url, opt) => {
  calls.push({url, opt: opt || {}});
  if (url === '/dashboard/api/routine') return res(200, VIEW);
  if (url.startsWith('/dashboard/api/actions?ids=')) return res(200, {actions: [MODE === 'fail'
    ? {id: 'a_1', status: 'rejected', code: 'wrong_state', text: '#41 에 에이블리 링크를 넣지 못했어요 (save_failed)'} : {id: 'a_1', status: 'ok', code: 'done', text: ''}]});
  if (MODE === 'bad') return res(400, {status: 'error', code: 'bad_args', text: '붙여 넣은 글에서 에이블리 링크를 찾지 못했어요'});
  return res(202, {status: 'queued', id: 'a_1'});
};
const tick = () => new Promise(r => setTimeout(r, 5));
const box = () => main.children.find(c => c.attrs.id === 'rtp');
const rows = () => box().all(c => c.className === 'it ab').map(r => ({rid: r.attrs['data-rid'],
  texts: r.all(c => c.tagName === 'SPAN').map(c => c.textContent),
  a: r.all(c => c.tagName === 'A').map(a => [a.textContent, a.attrs.href, a.attrs.target, a.attrs.rel]),
  input: r.all(c => c.tagName === 'INPUT').map(i => [i.attrs.placeholder, i.attrs.type]),
  btn: r.all(c => c.tagName === 'BUTTON').map(b => [b.textContent, b.className])}));
(async () => {
  require(process.argv[1]);
  await tick(); await tick();
  out.hidden = box().hidden;
  if (out.hidden) { console.log(JSON.stringify(out)); process.exit(0); }
  out.h2 = box().all(c => c.tagName === 'H2').map(c => c.textContent);
  out.notes = box().all(c => c.tagName === 'P').map(c => c.textContent);
  out.rows = rows();
  const r0 = () => box().all(c => c.className === 'it ab')[0];
  r0().all(c => c.tagName === 'BUTTON')[0].click();  // 빈 칸으로 넣기
  await tick();
  out.empty = r0().all(c => c.attrs.role === 'status')[0].textContent;
  out.posts0 = calls.filter(c => c.url === '/dashboard/api/action').length;
  const f = r0().all(c => c.tagName === 'INPUT')[0];
  f.value = process.argv[4];
  (f.ls.input || []).forEach(h => h({currentTarget: f}));
  r0().all(c => c.tagName === 'BUTTON')[0].click();
  await tick(); await tick(); await tick();
  const post = calls.find(c => c.url === '/dashboard/api/action');
  out.post = {token: post.opt.headers['X-Dash-Token'], body: JSON.parse(post.opt.body)};
  out.toast = toast.textContent;
  out.after = rows().map(r => r.rid);
  out.h2after = box().all(c => c.tagName === 'H2').map(c => c.textContent);
  out.status = box().all(c => c.attrs.role === 'status').map(c => [c.textContent, c.className]);
  out.polled = calls.filter(c => c.url.startsWith('/dashboard/api/actions?ids=')).map(c => c.url);
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def run_shim(view: dict, mode: str = "ok", text: str = BLURB) -> dict:
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False), mode, text],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_routine_js_draws_requests_and_sends_the_pasted_link(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    write_export(tmp_path / "blogauto.json", ably_requests=two_rows(bot, day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    got = run_shim(view)
    assert got["hidden"] is False and got["h2"] == ["에이블리 링크 부탁 (2)"] and got["notes"] == [NOTE]  # 할 일 목록이 없으면 부탁 칸만
    one, two = got["rows"]
    assert one["rid"] == "41-12345678" and one["texts"][:3] == ["크림 플리츠 스커트", "스커트 · 12,900원 · 에이블리 · 모니크샵",
                                                                 "글: 가을 데이트룩 정리 · 발행 14:30 전"]
    assert one["a"] == [["에이블리에서 열기 ↗", "https://m.a-bly.com/goods/12345678", "_blank", "noopener noreferrer"]]
    assert one["input"] == [["여기에 링크 붙여넣기", "text"]] and one["btn"] == [["넣기", "sm ac"]]
    assert two["texts"][:3] == ["루즈핏 니트", "니트 · 에이블리", ""]  # 가격 0·글 제목·발행 시각이 없으면 빼고 씀
    assert got["empty"] == "에이블리 앱에서 만든 링크를 먼저 붙여 넣어 주세요" and got["posts0"] == 0
    assert got["post"] == {"token": "tok123", "body": {"action": "blog.similar_links", "cid": got["post"]["body"]["cid"], "ctoken": None,
                                                       "args": {"blog": "issue", "pid": 41, "links": [BLURB.strip()], "rid": "41-12345678"}}}
    assert got["toast"] == "넣었어요" and got["after"] == ["41-777"] and got["h2after"] == ["에이블리 링크 부탁 (1)"]
    assert got["polled"] == ["/dashboard/api/actions?ids=a_1"]
    # 블로그 프로그램이 받지 못하면 그 줄이 이유와 함께 다시
    got = run_shim(view, "fail")
    assert got["after"] == ["41-12345678", "41-777"] and got["toast"] == "#41 에 에이블리 링크를 넣지 못했어요 (save_failed)"
    assert got["status"][0] == ["#41 에 에이블리 링크를 넣지 못했어요 (save_failed)", "s er"]
    # 대시보드가 거절하면 (공유 문구에 에이블리 링크 없음) 줄은 그대로 · 이유를 그 줄에
    got = run_shim(view, "bad", "공유 https://zigzag.kr/x")
    assert got["after"] == ["41-12345678", "41-777"] and got["status"][0][0] == "붙여 넣은 글에서 에이블리 링크를 찾지 못했어요"
    # 부탁이 없거나 0개면 칸 전체를 숨김 (할 일 목록도 없을 때)
    assert run_shim(view | {"ably": None}) == {"hidden": True}


@pytest.mark.skipif(NODE is None, reason="node 없음")
def test_requests_sit_below_the_checklist(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    st = fixture("export_v2.json")["status"]
    rt = {"day": day, "slots": SLOTS, "overflow": 0, "items": [item("nb:issue:a", "s1")]}
    write_export(tmp_path / "blogauto.json", status={**st, "routine": rt}, ably_requests=two_rows(bot, day))
    got = run_shim(routine.view(bot, now=at(bot, day, "10:00")))
    assert got["h2"] == ["오늘 할 일 · 0/1", "에이블리 링크 부탁 (2)"]


# 진짜 크롬 (ops 원칙 24 — 그려진 화면에 '[object'·'undefined'·'NaN' 없음 · 콘솔 오류 없음 · 폰 너비에서 가로로 안 넘침)
BROWSER = r"""
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [js, css, view] = [fs.readFileSync(process.argv[1], 'utf8'), fs.readFileSync(process.argv[2], 'utf8'), process.argv[3]];
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 375, height: 800}});
  const errors = [], posts = [];
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push(String(e)));
  const html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<link rel="stylesheet" href="/dashboard/static/app.css"></head><body><main id="main"><p>할 일</p></main><div id="toast" hidden></div>' +
    '<script id="boot" type="application/json">{"me":{"csrf":"tok"}}</script><script src="/dashboard/static/routine.js"></script></body></html>';
  await p.route('http://dash.test/**', async r => {
    const u = new URL(r.request().url());
    const json = (status, d) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(d)});
    if (u.pathname === '/dashboard/') return r.fulfill({contentType: 'text/html', body: html});
    if (u.pathname.endsWith('app.css')) return r.fulfill({contentType: 'text/css', body: css});
    if (u.pathname.endsWith('routine.js')) return r.fulfill({contentType: 'text/javascript', body: js});
    if (u.pathname === '/dashboard/api/routine') return json(200, JSON.parse(view));
    if (u.pathname === '/dashboard/api/action') { posts.push(JSON.parse(r.request().postData())); return json(202, {status: 'queued', id: 'a_1'}); }
    if (u.pathname === '/dashboard/api/actions') return json(200, {actions: [{id: 'a_1', status: 'ok', code: 'done', text: ''}]});
    return r.fulfill({status: 404, body: ''});
  });
  await p.goto('http://dash.test/dashboard/#/todo');
  await p.waitForSelector('#rtp .ab');
  const out = {};
  out.text = await p.$eval('#rtp', e => e.innerText);
  out.wide = await p.evaluate(() => document.documentElement.scrollWidth);
  out.btn = await p.$eval('#rtp .ab .pf .sm', e => Math.round(e.getBoundingClientRect().height));
  await p.fill('#rtp .ab input', process.argv[4]);
  await p.click('#rtp .ab .pf .sm');
  await p.waitForFunction(() => document.querySelectorAll('#rtp .ab').length === 1);
  out.toast = await p.$eval('#toast', e => e.innerText);
  out.after = await p.$eval('#rtp', e => e.innerText);
  out.posts = posts; out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def test_real_browser_renders_clean(bot: DealBot, tmp_path: Path) -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    day = tz_now(bot).date().isoformat()
    st = fixture("export_v2.json")["status"]
    rt = {"day": day, "slots": SLOTS, "overflow": 0, "items": [item("nb:issue:a", "s1")]}
    write_export(tmp_path / "blogauto.json", status={**st, "routine": rt}, ably_requests=two_rows(bot, day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    out = subprocess.run([NODE, "-e", BROWSER, str(STATIC / "routine.js"), str(STATIC / "app.css"), json.dumps(view, ensure_ascii=False),
                          BLURB], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["errors"] == []
    for text in (got["text"], got["after"], got["toast"]):
        for bad in ("[object", "undefined", "NaN", "null"):
            assert bad not in text, (bad, text)
    assert "에이블리 링크 부탁 (2)" in got["text"] and "12,900원" in got["text"] and "발행 14:30 전" in got["text"]
    assert "에이블리에서 열기 ↗" in got["text"] and got["text"].count(NOTE) == 1
    assert got["wide"] <= 375 and got["btn"] >= 44  # 폰 너비에서 가로로 안 넘침 · 누르기 쉬운 단추
    assert got["toast"] == "넣었어요" and "에이블리 링크 부탁 (1)" in got["after"] and "크림 플리츠 스커트" not in got["after"]
    # 한 줄 칸이라 붙여 넣은 공유 문구의 줄바꿈은 브라우저가 바꿔 둠 — 서버(ably_link_in)가 그 안의 첫 에이블리 주소를 꺼냄
    (sent,) = got["posts"]
    assert sent["action"] == "blog.similar_links" and {k: v for k, v in sent["args"].items() if k != "links"} == \
        {"blog": "issue", "pid": 41, "rid": "41-12345678"} and len(sent["args"]["links"]) == 1
    assert ably_link_in(sent["args"]["links"][0]) == "https://applink.a-bly.com/aBcD1234"
