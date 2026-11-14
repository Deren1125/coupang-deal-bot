"""'이웃 관리' 책갈피 (주인 11/14 18:5x~19:0x — 서이추·이웃 들르기(공감·댓글)를 책갈피 하나로 · 서버는 네이버에 로그인하지 않음).

1) seoi.bookmarklet(): static/seoi.js 를 줄여 주소·열쇠·블로그 표를 넣은 "javascript:" 주소 — 사파리 책갈피 길이 안 · node 로 읽힘 ·
   innerHTML·eval 없음 · 누르기(.click())는 press() 한 곳뿐이고 확인·등록·신청 단추는 거절 · 가짜 입력 신호·브라우저 정보 바꾸기 없음.
2) 가짜 화면(node)에서 화면마다 한 단계: 블로그 화면 → 이웃추가 한 번 · 이웃추가 화면 → 서로이웃 + 멘트(칸 길이에 맞춤) · 이웃 글 → 공감 한 번
   (이미 눌렸으면 그대로) + 댓글 초안. 세 블로그(데런픽·드라마·라커룸) 각각 · 로그인 아이디를 못 읽으면 고르기 창(이 탭에서 기억) ·
   주소에 멘트가 없으면 물어봄 · 대시보드가 '조금 쉬었다가'라 하거나 못 닿으면 아무것도 누르지 않음 · 글을 연 지 얼마 안 됐으면 묻지도 않음.
3) seoi.act(): 열쇠 · 간격(무작위 60~120초 / 45~90초) · 이어 하기 · 한 시간 15 · 하루 서이추 블로그마다 30 · 들르기 합쳐 40 · 읽은 시간 ·
   크기 · 남의 글 본문은 기록·저장 안 함 · 초안 거르기 · 모델은 Fable 아님.
4) GET /dashboard/api/seoi (로그인 아래) · POST /dashboard/api/seoi_act (열쇠 · 교차 사이트 · CORS 는 네이버만 · 손님마다 횟수) ·
   연결 탭 '이웃 관리 버튼 설치' 칸 · 할 일 값 routine.view()["seoi"].
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import logins, routine, seoi
from test_dash_logins import DOM_SHIM as LG_SHIM
from test_dash_state import write_export
from test_dash_statscap import FakeDb

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
TZ = ZoneInfo("Asia/Seoul")
NOW = 1_794_650_000.0  # 2026-11-14 KST 낮
# (네이버 아이디, 블로그, 이름) — 세 블로그 모두 같은 시험을 돈다
THREE = [("derenpick", "issue", "데런픽"), ("derenlog", "finance", "드라마"), ("demiyum", "hotdeal", "라커룸")]
MSG = "안녕하세요, 패딩 글 잘 봤어요. 서로이웃으로 자주 소통하고 싶어요!"
POST_TEXT = "지난 주말 수원 경기 직관 후기입니다. 후반 추가 시간에 터진 역전 골 장면을 골대 뒤에서 봤는데 아직도 소름이 돋아요. " * 3
DRAFT = "골대 뒤에서 본 역전 골이라니 글만 읽어도 그 순간이 그려져요. 추가 시간에 터져서 더 짜릿했겠어요!"


def code(db: FakeDb | None = None) -> str:
    c = seoi.view(db or FakeDb(), "https://dash.example")["code"]
    assert c.startswith("javascript:")
    return unquote(c[len("javascript:"):])


# ---------------------------------------------------------------- 1) 책갈피
def test_blogs_are_the_three_and_names_match_the_dashboard() -> None:
    assert [tuple(b) for b in seoi.BLOGS] == THREE
    assert json.loads(seoi.blogs_json()) == [list(b) for b in THREE]


def test_bookmarklet_is_one_javascript_url_within_safari_limit_and_has_endpoint_key_blogs() -> None:
    db = FakeDb()
    v = seoi.view(db, "https://dash.example")
    js = code(db)
    assert v["len"] == len(v["code"]) < 28000  # 꾸미기 책갈피와 같은 한도 (아이패드 사파리에서 25.9KB 돎)
    assert "'https://dash.example/dashboard/api/seoi_act'" in js and f"'{seoi.key(db)}'" in js and "@@" not in js
    assert seoi.blogs_json() in js and "/*" not in js and "\n//" not in js
    assert len(v["steps"]) == len(seoi.STEPS) == 6 and "확인" in v["steps"][3] and "등록" in v["steps"][4]
    with pytest.raises(ValueError):
        seoi.bookmarklet("https://x.example/a'b", "k")


def test_bookmarklet_never_submits_and_has_no_tricks() -> None:
    js = code()
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "Function(", "import("):
        assert bad not in js, bad
    # 누르기는 press() 한 곳 — 마지막 단추(확인·등록·신청·다음)와 submit 은 거기서 거절
    assert js.count(".click()") == 1 and "n.click();" in js
    assert "const NO=/확인|등록|신청|보내기|완료|다음|취소|삭제|차단|submit/i;" in js
    assert "if(ty==='submit'||ty==='image'||NO.test(t))return false;" in js
    assert "ty=String(n.getAttribute('type')||(n.form&&n.type)||'').toLowerCase();" in js  # type 없는 button 도 폼 안이면 submit
    for bad in (".submit(", "requestSubmit", "KeyboardEvent", "MouseEvent", "PointerEvent", "TouchEvent", "keydown", "keypress", "keyup",
                "mousedown", "mousemove", "touchstart", "userAgent", "navigator", "webdriver", "cookie", "XMLHttpRequest", "sendBeacon",
                "setInterval"):
        assert bad not in js, bad  # 가짜 마우스·키 입력 · 브라우저 정보 바꾸기 · 나중에 저절로 하기 없음
    # 바깥 요청은 대시보드 한 곳 (쿠키 없이) · 화면을 옮기지 않음
    assert js.count("fetch(") == 1 and "credentials:'omit'" in js and "location.href=" not in js and "L.href=" not in js and "open(" not in js
    # 읽은 시간 기준은 서버와 같게
    assert f"rd<(sc?{seoi.READ_SCROLL_S}:{seoi.READ_S})" in js


@needs_node
def test_bookmarklet_parses_and_matches_encode_uri_component() -> None:
    db = FakeDb()
    full = seoi.view(db, "https://dash.example")["code"]
    chk = "const c=process.argv[1];new Function(c);process.stdout.write(String('javascript:'+encodeURIComponent(c)===process.argv[2]))"
    out = subprocess.run([NODE, "-e", chk, code(db), full], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "true"
    assert subprocess.run([NODE, "--check", str(STATIC / "seoi.js")], capture_output=True).returncode == 0


def test_bookmarklet_source_keeps_one_statement_per_line_rules() -> None:
    body = (STATIC / "seoi.js").read_text(encoding="utf-8").split("*/", 1)[1]
    for ln in body.splitlines():
        s = ln.strip()
        assert "//" not in s or s.startswith("//") or "://" in s or "\\/" in s, ln  # // 주석은 줄 맨 앞에만
    for rx in re.findall(r"(?<![\w)])/(?![/*])((?:\\.|[^/\n])+)/[gimsuy]*[.;),]", body):
        assert " " not in rx and "'" not in rx and '"' not in rx, rx  # 정규식 안에 공백·따옴표 없음


# ---------------------------------------------------------------- 2) 가짜 화면
# 작은 DOM: 요소 = [태그, {속성 · text · on(눌렀을 때 일) · name(결과에 적을 이름)}, [자식]] · 고르는 식은 책갈피가 쓰는 모양만
HARNESS = r"""
const [code, sj] = process.argv.slice(1), S = JSON.parse(sj), out = {alerts: [], prompts: [], asks: [], clicks: [], chooser: [], els: {}};
const ST = setTimeout; global.setTimeout = (f, t) => ST(f, Math.min(t || 0, 3));
const named = {};
const ACT = {
  like: n => n.setAttribute('aria-pressed', n.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'),
  open: () => { named.wrap.hidden = false; },
  nav: n => { out.nav = (out.nav || 0) + 1; },
  submit: n => { out.submitted = (out.submitted || 0) + 1; },
};
class El {
  constructor(tag, a, kids) {
    this.tagName = tag.toUpperCase(); this.attrs = {}; this.children = []; this.parentNode = null; this.clicks = 0; this.style = {}; this.events = [];
    this.value = ''; this.checked = false; this.disabled = false; this.hidden = false; this._t = '';
    for (const k in a || {}) { const v = a[k];
      if (k === 'text') this._t = v; else if (k === 'on' || k === 'rect') this[k] = v; else if (k === 'name') { this.nm = v; named[v] = this; }
      else if (['value', 'checked', 'disabled', 'hidden', 'form'].includes(k)) this[k] = v; else this.attrs[k] = String(v); }
    for (const c of kids || []) this.appendChild(c);
  }
  get id() { return this.attrs.id || ''; }
  get className() { return this.attrs.class || ''; }
  get type() { return this.attrs.type || (this.tagName === 'BUTTON' ? 'submit' : ''); }  // 브라우저처럼: type 없는 button = submit
  set type(v) { this.attrs.type = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  appendChild(c) { c.parentNode = this; this.children.push(c); return c; }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(x => x !== this); }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(' '); }
  set textContent(v) { this._t = String(v); this.children = []; }
  get innerText() { return this.textContent; }
  get nextElementSibling() { const p = this.parentNode; return (p && p.children[p.children.indexOf(this) + 1]) || null; }
  get ownerDocument() { let n = this; while (n.parentNode) n = n.parentNode; return n.doc; }
  getClientRects() { for (let n = this; n; n = n.parentNode) if (n.hidden) return []; return [1]; }
  getBoundingClientRect() { return this.rect || {top: 0, height: 100}; }
  focus() {} select() {}
  dispatchEvent(e) { this.events.push(e.type); return true; }
  click() { this.clicks++; out.clicks.push(this.nm || this.id || this.className || this.tagName);
    if (this.tagName === 'INPUT' && this.attrs.type === 'radio' && !this.disabled) this.checked = true;
    if (this.on) ACT[this.on](this); }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  _one(c) {
    const m = c.match(/^([a-z0-9]*)(.*)$/i);
    if (m[1] && this.tagName !== m[1].toUpperCase()) return false;
    for (const p of m[2].match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]/g) || []) {
      if (p[0] === '#') { if (this.id !== p.slice(1)) return false; }
      else if (p[0] === '.') { if (!this.className.split(/\s+/).includes(p.slice(1))) return false; }
      else { const a = p.slice(1, -1).match(/^([\w-]+)(?:(\*?=)"?([^"]*)"?)?$/), v = a[1] === 'class' ? this.className : this.getAttribute(a[1]);
        if (v == null) return false; if (a[2] === '=' && v !== a[3]) return false; if (a[2] === '*=' && !v.includes(a[3])) return false; }
    }
    return true;
  }
  matches(sel) {
    return sel.split(',').some(s => { const cs = s.trim().split(/\s+/); let n = this;
      if (!n._one(cs.pop())) return false;
      while (cs.length) { const c = cs.pop(); n = n.parentNode; while (n && !n._one(c)) n = n.parentNode; if (!n) return false; }
      return true; });
  }
  closest(sel) { for (let n = this; n; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  querySelectorAll(sel) { return this.all().filter(n => n.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}
const mk = ([t, a, k]) => new El(t, a, (k || []).map(mk));
const loc = u => { const x = new URL(u); return {href: u, hostname: x.hostname, pathname: x.pathname, hash: x.hash, search: x.search}; };
const store = init => { const m = Object.assign({}, init || {}); return {getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); },
  removeItem: k => { delete m[k]; }, _m: m}; };
const mkdoc = (tree, win, title) => { const body = mk(['body', {}, tree]); const d = {body, documentElement: body, title: title || '', defaultView: win,
  querySelectorAll: s => body.querySelectorAll(s), querySelector: s => body.querySelector(s), createElement: t => new El(t)}; body.doc = d; return d; };
const W = Object.assign({scrollY: S.scrollY || 0, innerHeight: 800, performance: {now: () => S.open_ms == null ? 30000 : S.open_ms},
  getComputedStyle: () => ({display: 'block', visibility: 'visible'}), localStorage: store(S.ls), sessionStorage: store(S.ss)}, S.win || {});
const D = mkdoc(S.body || [], W, S.title);
if (S.frame) { const fw = Object.assign({scrollY: 0, location: loc(S.frame.url), frameElement: {getBoundingClientRect: () => ({top: 0, height: 5000})}}, S.frame.win || {});
  fw.document = mkdoc(S.frame.body, fw); const f = new El('iframe', {id: 'mainFrame'}); f.contentWindow = fw; D.body.appendChild(f); }
W.location = loc(S.url); W.document = D;
global.window = W; global.document = D; global.location = W.location;
global.alert = m => out.alerts.push(m);
global.prompt = (m, d) => { out.prompts.push([m, d]); return S.prompt == null ? null : S.prompt; };
const replies = (S.replies || []).slice();
global.fetch = async (u, o) => { out.url = u; out.opt = {mode: o.mode, credentials: o.credentials, method: o.method};
  out.asks.push(Object.fromEntries(new URLSearchParams(o.body.toString())));
  if (S.down) throw new Error('offline');
  return {json: async () => replies.shift() || {ok: false, text: '대답 없음'}}; };
// 고르기 창이 뜨면 S.choose 이름의 단추를 누름 (주인이 누르는 것)
const watch = setInterval(() => { for (const c of D.body.children) if (c.style && /position:fixed/.test(c.style.cssText || '') && c.children.length) {
  out.chooser.push(c.children.map(b => b.textContent)); const b = c.children.find(x => x.textContent === S.choose); if (b) b.onclick(); } }, 2);
(async () => {
  for (let i = 0; i < (S.runs || 1); i++) { await eval(code); await new Promise(r => ST(r, 10)); }
  clearInterval(watch);
  for (const k in named) { const n = named[k]; out.els[k] = {clicks: n.clicks, value: n.value, checked: n.checked, text: n.textContent, pressed: n.getAttribute('aria-pressed'), events: n.events}; }
  out.ls = W.localStorage._m; out.ss = W.sessionStorage._m; out.left = D.body.children.filter(c => c.style && c.style.cssText).length; out.busy = W.__sbnb;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def run(scn: dict[str, Any], db: FakeDb | None = None) -> dict[str, Any]:
    res = subprocess.run([NODE, "-e", HARNESS, code(db), json.dumps(scn, ensure_ascii=False)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def form_page(maxlength: int | None = 30, **extra: Any) -> list[Any]:
    """모바일 이웃추가 화면: 이웃(골라져 있음)·서로이웃 고르기 · 그룹 · 멘트 칸(기본 인사) · 확인 단추 둘."""
    ta: dict[str, Any] = {"id": "message", "name": "msg", "value": "우리 서로이웃해요~"}
    if maxlength:
        ta["maxlength"] = maxlength
    return [["div", {"class": "buddy_form"}, [
        ["input", {"type": "radio", "id": "buddy1", "name": "one", "value": "1", "checked": True}], ["label", {"for": "buddy1", "text": "이웃"}],
        ["input", {"type": "radio", "id": "buddy2", "name": "both", "value": "2", **extra}], ["label", {"for": "buddy2", "text": "서로이웃"}],
        ["select", {"id": "group", "name": "group", "value": "g1"}], ["textarea", ta],
        ["a", {"class": "btn_ok", "name": "ok", "text": "확인", "on": "submit"}],
        ["button", {"type": "submit", "name": "ok2", "text": "확인", "on": "submit"}]]]]


def stored(blog: str, msg: str = MSG, target: str = "neighbor1", **kw: Any) -> dict[str, str]:
    return {"sbnb": json.dumps({"t": target, "b": blog, "m": msg, "at": 9_999_999_999_999, **kw}, ensure_ascii=False)}


@needs_node
@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
def test_form_page_picks_mutual_fills_trimmed_message_and_never_confirms(nid: str, blog: str, name: str) -> None:
    db = FakeDb()
    got = run({"url": "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1", "body": form_page(30), "win": {"userId": nid, "bLoginUser": True},
               "ls": stored(blog), "replies": [{"ok": True, "text": f"오늘 {name} 서이추 3/30"}]}, db)
    assert got["asks"] == [{"a": "add", "b": blog, "t": "neighbor1", "k": seoi.key(db)}]
    assert got["url"] == "https://dash.example/dashboard/api/seoi_act" and got["opt"] == {"mode": "cors", "credentials": "omit", "method": "POST"}
    assert got["els"]["both"]["checked"] is True and got["clicks"] == ["both"]  # 누른 것은 서로이웃 하나
    assert got["els"]["msg"]["value"] == MSG[:30] and {"input", "change"} <= set(got["els"]["msg"]["events"])
    assert got["els"]["group"]["value"] == "g1" and got["els"]["group"]["clicks"] == 0  # 그룹은 그대로
    assert got["els"]["ok"]["clicks"] == 0 and got["els"]["ok2"]["clicks"] == 0 and "submitted" not in got
    (a,) = got["alerts"]
    assert a.startswith(f"이웃 관리: {name} · 서로이웃 고름 ✓ · 멘트 넣음 ✓ 30자 (칸이 짧아 {len(MSG)}자에서 줄임) · 그룹은 그대로")
    assert "마지막 단추는 직접" in a and f"오늘 {name} 서이추 3/30" in a and got["prompts"] == [] and got["busy"] == 0


@needs_node
def test_form_without_message_asks_the_owner_and_message_hash_wins() -> None:
    url = "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1"
    base = {"body": form_page(None), "win": {"userId": "derenpick"}, "replies": [{"ok": True, "text": "오늘 데런픽 서이추 1/30"}]}
    got = run({**base, "url": url, "prompt": "직접 쓴 멘트예요"})
    assert len(got["prompts"]) == 1 and got["els"]["msg"]["value"] == "직접 쓴 멘트예요" and got["els"]["ok"]["clicks"] == 0
    got = run({**base, "url": url})  # 묻는 창을 닫음 → 서로이웃만 고르고 멘트 칸은 그대로
    assert got["els"]["both"]["checked"] is True and got["els"]["msg"]["value"] == "우리 서로이웃해요~" and "멘트 없음 — 칸은 그대로" in got["alerts"][0]
    got = run({**base, "url": url + "#bmmsg=" + quote("주소에 든 멘트 100%예요") + "&bmb=issue", "ls": stored("issue", "맡겨 둔 멘트")})
    assert got["prompts"] == [] and got["els"]["msg"]["value"] == "주소에 든 멘트 100%예요"
    # 주소에 멘트가 없어도 대시보드가 오늘 할 일 줄에서 찾아 준 멘트를 씀 (묻지 않음)
    got = run({**base, "url": url, "ls": stored("issue", "맡겨 둔 멘트"), "replies": [{"ok": True, "msg": "할 일 줄의 멘트예요", "text": "t"}]})
    assert got["prompts"] == [] and got["els"]["msg"]["value"] == "할 일 줄의 멘트예요"
    # 다른 블로그 할 일 줄이면 거절 — 아무것도 안 누르고, 이 탭에서 고른 블로그는 잊음 (다음에 다시 물음)
    row = "이 블로그는 드라마 할 일이에요 — 지금은 데런픽 계정으로 보여요. 계정(또는 고른 블로그)을 바꾼 뒤 다시 눌러 주세요"
    got = run({"body": form_page(None), "url": url, "win": {}, "ss": {"sbnb_b": "issue"}, "replies": [{"ok": False, "code": "row", "text": row}]})
    assert got["alerts"] == ["이웃 관리: " + row] and got["clicks"] == [] and got["ss"] == {} and got["els"]["msg"]["value"] == "우리 서로이웃해요~"


@needs_node
def test_form_reports_what_it_found_when_it_cannot_do_it() -> None:
    url = "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1"
    win = {"userId": "derenpick"}
    got = run({"url": url, "win": win, "body": [["p", {"text": "이미 서로이웃 신청 중입니다."}], ["a", {"name": "ok", "text": "확인", "on": "submit"}]]})
    assert got["alerts"] == ["이웃 관리: 이미 이웃이거나 신청 중이에요 — 아무것도 하지 않았어요"] and got["asks"] == [] and got["clicks"] == []
    db = FakeDb()
    got = run({"url": url, "win": win, "ls": stored("issue"), "replies": [{"ok": True, "text": "오늘은 쉬어요"}],
               "body": [["p", {"text": "하루에 신청 가능한 이웃수를 초과했습니다."}], ["a", {"name": "ok", "text": "확인", "on": "submit"}]]}, db)
    assert got["alerts"] == ["이웃 관리: 네이버가 한도·제한 안내를 보여요 — 오늘 서이추는 그만해요"]
    assert got["asks"] == [{"a": "stop", "b": "issue", "w": "add", "k": seoi.key(db)}]  # 대시보드에 알려 그날 쉼
    assert got["clicks"] == []
    # 신청 화면에서 대시보드에 못 닿으면: ①에서 방금 허락받은 그 블로그일 때만 이어 함 — 아니면 아무것도 안 함
    got = run({"url": url, "win": win, "ls": stored("issue", g=1), "body": form_page(None), "down": True})
    assert got["clicks"] == ["both"] and got["els"]["msg"]["value"] == MSG and "앞 단계 허락으로 이어 했어요" in got["alerts"][0] and got["els"]["ok"]["clicks"] == 0
    for ls in (stored("issue"), stored("issue", g=1, target="neighbor2"), stored("finance", g=1), {}):
        got = run({"url": url, "win": win, "ls": ls, "body": form_page(None), "down": True})
        assert got["alerts"] == ["이웃 관리: 대시보드에 닿지 않아 아무것도 하지 않았어요 — 잠시 뒤 다시"] and got["clicks"] == [], ls
    # 서로이웃 칸은 없고 글 칸만 있으면: 멘트만 넣고 서로이웃은 못 골랐다고 알림 (첫 실사용에서 화면 모양을 알 수 있게)
    got = run({"url": url, "win": win, "ls": stored("issue"), "replies": [{"ok": True, "text": "t"}],
               "body": [["input", {"type": "radio", "id": "x1", "value": "1"}], ["label", {"for": "x1", "text": "이웃"}], ["textarea", {"name": "msg"}]]})
    assert got["clicks"] == [] and got["els"]["msg"]["value"] == MSG and "서로이웃 고르는 칸은 이 화면에 없어요" in got["alerts"][0]
    got = run({"url": url, "win": win, "body": [["input", {"type": "radio", "id": "x1", "value": "1"}], ["label", {"for": "x1", "text": "이웃"}]]})
    assert got["alerts"] == ["이웃 관리: 서로이웃 고르는 칸을 못 찾았어요 (고르는 칸 1개 · 글 칸 0개) — 직접 해 주세요"] and got["clicks"] == [] and got["asks"] == []
    got = run({"url": url, "win": win, "body": form_page(None, disabled=True)})
    assert got["alerts"] == ["이웃 관리: 서로이웃을 받지 않는 블로그예요 — 아무것도 하지 않았어요"] and got["clicks"] == [] and got["asks"] == []
    # 멘트 칸이 없는 화면 (PC 첫 단계): 서로이웃만 고르고 멘트는 맡겨 둠
    body = [["input", {"type": "radio", "id": "each_buddy_add", "name": "both", "value": "1"}], ["label", {"for": "each_buddy_add", "text": "서로이웃을 맺습니다"}],
            ["a", {"name": "next", "text": "다음", "on": "submit"}]]
    got = run({"url": "https://blog.naver.com/BuddyAdd.naver?blogId=neighbor1", "win": {"userId": "demiyum"}, "ls": stored("hotdeal"), "body": body,
               "replies": [{"ok": True, "text": "오늘 라커룸 서이추 1/30"}]})
    assert got["els"]["both"]["checked"] is True and got["els"]["next"]["clicks"] == 0 and "멘트 칸을 못 찾았어요 — 다음 화면에서 책갈피를 한 번 더" in got["alerts"][0]
    assert json.loads(got["ls"]["sbnb"])["m"] == MSG
    # PC 둘째 단계 (고르는 칸 없이 멘트 칸만): 맡겨 둔 멘트만 넣음 · '다음'은 안 누름
    body = [["select", {"id": "group", "name": "group", "value": "g1"}], ["textarea", {"id": "message", "name": "msg", "maxlength": 200}],
            ["a", {"name": "next", "text": "다음", "on": "submit"}]]
    got = run({"url": "https://blog.naver.com/BuddyAdd.naver?blogId=neighbor1", "win": {"userId": "demiyum"}, "ls": stored("hotdeal"), "body": body,
               "replies": [{"ok": True, "text": "오늘 라커룸 서이추 1/30"}]})
    assert got["els"]["msg"]["value"] == MSG and got["clicks"] == [] and got["els"]["next"]["clicks"] == 0
    assert got["alerts"][0].startswith("이웃 관리: 라커룸 · 서로이웃 고르는 칸은 이 화면에 없어요 (앞 화면에서 골랐는지 봐 주세요) · 멘트 넣음 ✓")
    # 이름표 없이 글이 옆에 붙은 화면: '이웃' 칸이 아니라 '서로이웃' 칸을 고름 (아이디·값으로는 알 수 없어도)
    body = [["div", {}, [["input", {"type": "radio", "name": "one", "value": "1", "checked": True}], ["span", {"text": "이웃"}],
                         ["input", {"type": "radio", "name": "both", "value": "2"}], ["span", {"text": "서로이웃"}]]], ["textarea", {"name": "msg"}]]
    got = run({"url": "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1", "win": {"userId": "derenlog"}, "ls": stored("finance"), "body": body,
               "replies": [{"ok": True, "text": "t"}]})
    assert got["clicks"] == ["both"] and got["els"]["both"]["checked"] is True and "서로이웃 고름 ✓" in got["alerts"][0]
    # 고르는 칸이 input 이 아닌 화면: '서로이웃' 글자 단추 하나면 그것을 누름
    body = [["button", {"name": "one", "text": "이웃"}], ["button", {"name": "both", "text": "서로이웃"}], ["textarea", {"name": "msg"}],
            ["button", {"name": "ok", "text": "확인", "on": "submit"}]]
    got = run({"url": "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1", "win": {"userId": "derenlog"}, "ls": stored("finance"), "body": body,
               "replies": [{"ok": True, "text": "t"}]})
    assert got["clicks"] == ["both"] and got["els"]["msg"]["value"] == MSG and got["els"]["ok"]["clicks"] == 0 and "서로이웃을 눌렀어요" in got["alerts"][0]
    # 그 단추가 폼 안의 type 없는 button (= submit, 누르면 폼이 넘어감) 이면 누르지 않고 직접 고르라고 알림 — 멘트만 넣음
    body = [["button", {"name": "one", "text": "이웃", "form": True}], ["button", {"name": "both", "text": "서로이웃", "form": True, "on": "submit"}],
            ["textarea", {"name": "msg"}], ["button", {"name": "ok", "text": "확인", "form": True, "on": "submit"}]]
    got = run({"url": "https://m.blog.naver.com/BuddyAddForm.naver?blogId=neighbor1", "win": {"userId": "derenlog"}, "ls": stored("finance"), "body": body,
               "replies": [{"ok": True, "text": "t"}]})
    assert got["clicks"] == [] and "submitted" not in got and got["els"]["msg"]["value"] == MSG and "서로이웃은 직접 골라 주세요" in got["alerts"][0]


def blog_home(nid: str) -> dict[str, Any]:
    """모바일 블로그 첫 화면: 이웃추가 단추 하나 + 추천 블로그의 이웃추가(누르면 안 됨) · 로그인 아이디는 화면 데이터에."""
    return {"body": [["button", {"data-click-area": "ebc.add", "name": "add", "text": "이웃추가", "on": "nav"}],
                     ["div", {"class": "Recommend__list"}, [["button", {"class": "add_buddy_btn _recommendBuddyAdd", "name": "other", "text": "이웃추가", "on": "nav"}]]]],
            "win": {"__REACT_QUERY_STATE__": {"queries": [{"state": {"data": {"isSuccess": True, "result": {"userId": nid, "loggedIn": bool(nid)}}}}]}}}


@needs_node
@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
def test_blog_page_presses_add_neighbor_once_and_keeps_the_message(nid: str, blog: str, name: str) -> None:
    db = FakeDb()
    url = "https://m.blog.naver.com/neighbor1#bmmsg=" + quote(MSG) + "&bmb=" + blog
    got = run({**blog_home(nid), "url": url, "replies": [{"ok": True, "text": f"오늘 {name} 서이추 1/30"}]}, db)
    assert got["asks"] == [{"a": "add", "b": blog, "t": "neighbor1", "k": seoi.key(db)}]
    assert got["clicks"] == ["add"] and got["nav"] == 1 and got["els"]["other"]["clicks"] == 0
    assert got["alerts"] == [f"이웃 관리: {name} · 이웃추가를 눌러요 — 신청 화면이 뜨면 책갈피를 한 번 더 눌러 주세요 (오늘 {name} 서이추 1/30)"]
    kept = json.loads(got["ls"]["sbnb"])
    assert (kept["t"], kept["b"], kept["m"], kept["g"]) == ("neighbor1", blog, MSG, 1)
    # 다른 블로그 할 일 줄이면 누르지 않음 (계정이 다름)
    other = next(b for _i, b, _n in THREE if b != blog)
    got = run({**blog_home(nid), "url": "https://m.blog.naver.com/neighbor1#bmmsg=x&bmb=" + other})
    assert got["clicks"] == [] and got["asks"] == [] and f"지금 {name} 계정이에요" in got["alerts"][0]


@needs_node
def test_blog_page_in_pc_frame_and_refusals_do_nothing() -> None:
    frame = {"url": "https://blog.naver.com/PostList.naver?blogId=neighbor1", "win": {"userId": "demiyum"},
             "body": [["a", {"class": "btn_buddy btn_addbuddy pcol2 _buddy_popup_btn", "name": "add", "text": "이웃추가", "on": "nav"}]]}
    got = run({"url": "https://blog.naver.com/neighbor1", "frame": frame, "replies": [{"ok": True, "text": "오늘 라커룸 서이추 2/30"}]})
    assert got["asks"][0]["b"] == "hotdeal" and got["asks"][0]["t"] == "neighbor1" and got["clicks"] == ["add"]
    # 너무 빨리 → 대시보드가 거절 → 알림만 (누르지 않고, 나중에 저절로 하지도 않음)
    gap = "조금 쉬었다가 — 42초 뒤에 다시 눌러 주세요 (아무것도 하지 않았어요)"
    got = run({**blog_home("derenpick"), "url": "https://m.blog.naver.com/neighbor1", "replies": [{"ok": False, "code": "gap", "text": gap}]})
    assert got["alerts"] == ["이웃 관리: " + gap] and got["clicks"] == [] and "sbnb" not in got["ls"]
    got = run({**blog_home("derenpick"), "url": "https://m.blog.naver.com/neighbor1", "down": True})
    assert got["alerts"] == ["이웃 관리: 대시보드에 닿지 않아 아무것도 하지 않았어요 — 잠시 뒤 다시"] and got["clicks"] == []
    got = run({**blog_home("derenpick"), "url": "https://m.blog.naver.com/demiyum"})
    assert got["alerts"] == ["이웃 관리: 우리 블로그예요 — 아무것도 하지 않았어요"] and got["asks"] == []
    # 블로그 첫 화면 데이터가 손님 값이어도 막지 않음 (로그인 아이디를 못 읽은 것으로 보고 블로그를 물음)
    got = run({**blog_home(""), "url": "https://m.blog.naver.com/neighbor1", "choose": "그만두기"})
    assert got["chooser"] and got["alerts"] == [] and got["asks"] == [] and got["clicks"] == []
    got = run({"url": "https://m.blog.naver.com/neighbor1", "win": {"userId": "derenpick"}, "body": [["button", {"name": "st", "text": "서로이웃"}]]})
    assert got["alerts"] == ["이웃 관리: 이웃추가 단추를 못 찾았어요 — 보이는 단추: 서로이웃 (이미 이웃일 수 있어요) · 아무것도 하지 않았어요"] and got["clicks"] == []
    got = run({"url": "https://www.naver.com/", "body": []})
    assert got["alerts"] == ["이웃 관리: 네이버 블로그 화면에서 눌러 주세요"]
    # 이웃추가 단추가 폼 안의 submit 단추(type 없는 button · input type=image)면 누르지 않음 — 직접 누르라고 알림
    for btn in (["button", {"data-click-area": "ebc.add", "name": "add", "text": "이웃추가", "form": True, "on": "submit"}],
                ["input", {"type": "image", "id": "btn_add_nb", "name": "add", "value": "이웃추가", "aria-label": "이웃추가", "text": "이웃추가", "on": "submit"}]):
        got = run({"url": "https://m.blog.naver.com/neighbor1", "win": {"userId": "derenpick"}, "body": [btn], "replies": [{"ok": True, "text": "t"}]})
        assert got["clicks"] == [] and "submitted" not in got and got["alerts"][-1] == "이웃 관리: 이웃추가 단추를 누르지 못했어요 — 직접 눌러 주세요", btn


def post_page(pressed: str = "false", open_box: bool = False, typed: str = "") -> list[Any]:
    """모바일 이웃 글: 제목 · 본문 · 글 끝 공감 단추 + 아래 떠 있는 같은 단추 · 댓글 단추 → 댓글 칸(등록 단추)."""
    like = {"class": "u_likeit_button _face " + ("on" if pressed == "true" else "off"), "aria-pressed": pressed, "text": "공감", "on": "like"}
    return [["div", {"class": "se-title-text", "text": "수원 직관 후기"}],
            ["div", {"class": "se-main-container", "name": "post", "text": POST_TEXT, "rect": {"top": -900, "height": 2000}}],
            ["div", {"class": "Interact__like_area__x1"}, [["a", {**like, "name": "like"}]]],
            ["div", {"class": "Interact__is_floating__x2"}, [["a", {**like, "name": "like_float"}]]],
            ["a", {"class": "Interact__comment_btn__x3", "name": "cbtn", "text": "댓글 4", "on": "open"}],
            ["div", {"class": "u_cbox_write_wrap", "name": "wrap", "hidden": not open_box}, [
                ["div", {"id": "naverComment__write_textarea", "contenteditable": "true", "name": "box", "text": typed}],
                ["button", {"class": "u_cbox_btn_upload", "name": "send", "text": "등록", "on": "submit"}]]]]


@needs_node
@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
def test_post_page_likes_once_and_puts_the_draft_without_registering(nid: str, blog: str, name: str) -> None:
    db = FakeDb()
    tail = f"오늘 이웃 들르기 3/40 · {name} 1"
    got = run({"url": "https://m.blog.naver.com/neighbor1/223456789012", "body": post_page(), "win": {"userId": nid, "bLoginUser": True}, "open_ms": 31000,
               "replies": [{"ok": True, "like": True, "comment": DRAFT, "text": tail}]}, db)
    (ask,) = got["asks"]
    assert ask == {"a": "visit", "b": blog, "u": "https://blog.naver.com/neighbor1/223456789012", "t": "수원 직관 후기", "x": " ".join(POST_TEXT.split()),
                   "rd": "31", "sc": "0", "k": seoi.key(db)}
    assert got["clicks"] == ["like", "cbtn"]  # 공감 한 번(글 끝 단추) · 댓글 칸 열기 — 등록은 안 누름
    assert got["els"]["like"]["pressed"] == "true" and got["els"]["like_float"]["clicks"] == 0
    assert got["els"]["box"]["text"] == DRAFT and "input" in got["els"]["box"]["events"]
    assert got["els"]["send"]["clicks"] == 0 and "submitted" not in got
    assert got["alerts"] == [f"이웃 관리: {name} · 공감 ✓ · 댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접 ({tail})"]
    assert got["left"] == 0 and got["busy"] == 0  # '쓰는 중' 알림 칸은 지움


@needs_node
def test_post_page_never_unlikes_and_respects_what_the_owner_typed() -> None:
    url, win = "https://m.blog.naver.com/neighbor1/223456789012", {"userId": "derenlog", "bLoginUser": True}
    ok = {"ok": True, "like": True, "comment": DRAFT, "text": "오늘 이웃 들르기 4/40 · 드라마 2"}
    got = run({"url": url, "win": win, "body": post_page(pressed="true", open_box=True), "replies": [ok]})
    assert got["els"]["like"]["clicks"] == 0 and got["els"]["like"]["pressed"] == "true"  # 이미 공감 — 다시 누르면 풀리므로 그대로
    assert got["clicks"] == [] and got["els"]["box"]["text"] == DRAFT and "공감은 이미 눌러져 있어요" in got["alerts"][0]
    mine = post_page(open_box=True) + [["li", {"class": "u_cbox_comment", "data-info": "commentNo:1,mine:true"}]]
    got = run({"url": url, "win": win, "body": mine, "replies": [ok]})
    assert got["els"]["box"]["text"] == "" and "이미 내 댓글이 있는 글이에요" in got["alerts"][0] and got["els"]["send"]["clicks"] == 0
    got = run({"url": url, "win": win, "body": post_page(open_box=True, typed="쓰던 글"), "replies": [ok]})
    assert got["els"]["box"]["text"] == "쓰던 글" and got["prompts"] == [["댓글 초안 (복사해서 쓰세요)", DRAFT]] and got["els"]["send"]["clicks"] == 0
    # 슬픈 소식 글 등: 대시보드가 공감도 하지 말라 함
    got = run({"url": url, "win": win, "body": post_page(), "replies": [{"ok": True, "like": False, "comment": "", "note": "슬픈 소식 글 같아요", "text": "t"}]})
    assert got["clicks"] == [] and got["alerts"] == ["이웃 관리: 드라마 · 공감은 누르지 않았어요 · 슬픈 소식 글 같아요 (t)"]
    # 공감 단추가 둘(글 끝)이면 누르지 않음 · 초안이 없으면 댓글 칸도 안 엶
    body = post_page() + [["div", {"class": "Interact__like_area__y"}, [["a", {"class": "u_likeit_button _face off", "aria-pressed": "false", "name": "like2", "on": "like"}]]]]
    got = run({"url": url, "win": win, "body": body, "replies": [{"ok": True, "like": True, "comment": "", "note": "댓글 초안을 쓰지 못했어요", "text": "t"}]})
    assert got["clicks"] == [] and "공감 단추가 2개라 누르지 않았어요 · 댓글 초안을 쓰지 못했어요" in got["alerts"][0]


@needs_node
def test_post_page_waits_until_the_post_was_read_and_refusals_do_nothing() -> None:
    url, win = "https://m.blog.naver.com/neighbor1/223456789012", {"userId": "derenpick", "bLoginUser": True}
    got = run({"url": url, "win": win, "body": post_page(), "open_ms": 5200})
    assert got["alerts"] == ["이웃 관리: 글을 조금 더 읽고 눌러 주세요 — 15초 뒤에 다시 (가운데까지 내려 봤으면 8초, 아니면 20초)"]
    assert got["asks"] == [] and got["clicks"] == []  # 묻지도 누르지도 않음
    got = run({"url": url, "win": win, "body": post_page(), "open_ms": 5200, "scrollY": 1400})  # 가운데까지 내려 봤어도 8초는
    assert "3초 뒤에 다시" in got["alerts"][0] and got["asks"] == []
    got = run({"url": url, "win": win, "body": post_page(), "open_ms": 9100, "scrollY": 1400, "replies": [{"ok": True, "like": True, "comment": DRAFT, "text": "t"}]})
    assert got["asks"][0]["rd"] == "9" and got["asks"][0]["sc"] == "1" and got["clicks"] == ["like", "cbtn"]
    gap = "조금 쉬었다가 — 37초 뒤에 다시 눌러 주세요 (아무것도 하지 않았어요)"
    got = run({"url": url, "win": win, "body": post_page(), "replies": [{"ok": False, "code": "gap", "text": gap}]})
    assert got["alerts"] == ["이웃 관리: " + gap] and got["clicks"] == [] and got["els"]["like"]["pressed"] == "false" and got["els"]["box"]["text"] == ""
    got = run({"url": url, "win": win, "body": post_page(), "down": True})
    assert got["alerts"] == ["이웃 관리: 대시보드에 닿지 않아 아무것도 하지 않았어요 — 잠시 뒤 다시"] and got["clicks"] == []
    got = run({"url": "https://m.blog.naver.com/derenlog/223456789012", "win": win, "body": post_page()})
    assert got["alerts"] == ["이웃 관리: 우리 블로그 글이에요 — 아무것도 하지 않았어요"] and got["asks"] == []
    got = run({"url": url, "win": {"userId": "", "bLoginUser": False}, "body": post_page()})
    assert got["alerts"] == ["이웃 관리: 네이버에 로그인돼 있지 않아요 — 로그인한 뒤 다시"] and got["asks"] == []
    got = run({"url": url, "win": win, "body": [["div", {"class": "se-title-text", "text": "제목"}]]})
    assert got["alerts"][0].startswith("이웃 관리: 글 본문을 못 읽었어요 (제목 있음 · 본문 0자)") and got["asks"] == []


@needs_node
async def test_comment_only_screen_continues_with_the_draft_it_just_got() -> None:
    """댓글 단추가 본문 없는 댓글 화면으로 옮겨 가면(모바일): 거기서 한 번 더 누르면 방금 받은 그 초안만 넣음 — 새로 세지도 쓰지도 않고, 등록은 주인이."""
    db, d = FakeDb(), FakeDrafter()
    win = {"userId": "demiyum", "bLoginUser": True}
    sent = run({"url": "https://m.blog.naver.com/neighbor1/223456789012", "body": post_page(), "win": win, "replies": [{"ok": False, "text": "x"}]}, db)["asks"][0]
    first = await seoi.act(db, sent, tz=TZ, now=NOW, drafter=d)
    assert first["comment"] == DRAFT and first["again"] is False
    cpage = {"url": "https://m.blog.naver.com/CommentList.naver?blogId=neighbor1&logNo=223456789012", "win": win, "open_ms": 900,
             "body": [["div", {"class": "u_cbox_write_wrap"}, [["div", {"id": "naverComment__write_textarea", "contenteditable": "true", "name": "box"}],
                                                                ["button", {"class": "u_cbox_btn_upload", "name": "send", "text": "등록", "on": "submit"}]]]]}
    ask = run({**cpage, "replies": [{"ok": False, "text": "x"}]}, db)["asks"][0]
    assert ask["u"] == sent["u"] and ask["x"] == "" and ask["b"] == "hotdeal"  # 같은 글 주소 · 본문 없이
    reply = await seoi.act(db, ask, tz=TZ, now=NOW + 40, drafter=d)  # 간격(45초) 안이어도 같은 글이라 이어 하기
    assert reply["again"] is True and reply["comment"] == DRAFT and reply["n"] == 1 and len(d.calls) == 1
    got = run({**cpage, "replies": [reply]}, db)
    assert got["els"]["box"]["text"] == DRAFT and got["clicks"] == [] and got["els"]["send"]["clicks"] == 0 and "submitted" not in got and got["left"] == 0
    assert got["alerts"] == ["이웃 관리: 라커룸 · 공감은 글 화면에서 · 댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접 (오늘 이웃 들르기 1/40 · 라커룸 1)"]
    # 받은 적 없는 글(또는 10분이 지난 글)의 댓글 화면: 서버가 본문 없다고 거절 → 글 화면에서 누르라고만 알림
    e = await refused(db, ask, NOW + seoi.TICKET_S + 1, drafter=d)
    assert (e.status, e.code) == (400, "empty") and len(d.calls) == 1 and seoi.today(db, NOW + 700, TZ)["visit"] == 1
    got = run({**cpage, "replies": [{"ok": False, "code": "empty", "text": e.text}]}, db)
    assert got["alerts"] == ["이웃 관리: 이 화면에서는 글 본문을 못 읽었어요 (제목 없음 · 본문 0자) — 글 화면에서 책갈피를 눌러 주세요"] and got["els"]["box"]["text"] == ""
    assert got["clicks"] == []
    # 다른 블로그로 받은 초안은 이어 주지 않음
    other = {**ask, "b": "issue"}
    assert (await refused(db, other, NOW + 50, drafter=d)).code == "empty"


@needs_node
@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
def test_unknown_login_asks_which_blog_once_per_tab(nid: str, blog: str, name: str) -> None:
    url = "https://m.blog.naver.com/neighbor1/223456789012"
    ok = {"ok": True, "like": True, "comment": DRAFT, "text": "t"}
    got = run({"url": url, "win": {"userId": "someoneelse"}, "body": post_page(), "choose": name, "replies": [ok]})
    assert got["chooser"][0] == ["데런픽", "드라마", "라커룸", "그만두기"] and got["asks"][0]["b"] == blog and got["ss"] == {"sbnb_b": blog}
    assert got["alerts"][0].startswith(f"이웃 관리: {name} · 공감 ✓")
    got = run({"url": url, "win": {}, "body": post_page(), "ss": {"sbnb_b": blog}, "replies": [ok]})  # 같은 탭에서는 다시 묻지 않음
    assert got["chooser"] == [] and got["asks"][0]["b"] == blog
    got = run({"url": url, "win": {}, "body": post_page(), "choose": "그만두기"})
    assert got["asks"] == [] and got["clicks"] == [] and got["alerts"] == [] and got["ss"] == {}
    # 서이추 줄에서 온 화면이면 그 줄의 블로그로 (고르기 없이)
    home = blog_home("")
    home["win"] = {}
    got = run({**home, "url": "https://m.blog.naver.com/neighbor1#bmmsg=x&bmb=" + blog, "replies": [{"ok": True, "text": "t"}]})
    assert got["chooser"] == [] and got["asks"][0]["b"] == blog and got["clicks"] == ["add"]


@needs_node
def test_page_type_decides_the_step_one_bookmarklet() -> None:
    win = {"userId": "derenpick", "bLoginUser": True}
    ok = {"ok": True, "like": True, "comment": DRAFT, "text": "t"}
    # 글 화면이라도 주소에 멘트가 있으면 서이추(이웃추가) — 공감·댓글은 건드리지 않음
    body = post_page() + [["a", {"class": "btn_buddyadd _add_buddy", "name": "add", "text": "이웃추가", "on": "nav"}]]
    got = run({"url": "https://m.blog.naver.com/neighbor1/223456789012#bmmsg=" + quote(MSG) + "&bmb=issue", "win": win, "body": body, "replies": [ok]})
    assert got["asks"][0]["a"] == "add" and got["clicks"] == ["add"] and got["els"]["like"]["clicks"] == 0
    got = run({"url": "https://m.blog.naver.com/neighbor1/223456789012", "win": win, "body": body, "replies": [ok]})
    assert got["asks"][0]["a"] == "visit" and "add" not in got["clicks"]
    # 서로이웃 고르는 칸이 같은 화면에 떠 있으면(겹친 창) 이웃추가 화면으로 봄
    got = run({"url": "https://m.blog.naver.com/neighbor1", "win": win, "body": form_page(None), "ls": stored("issue"), "replies": [ok]})
    assert got["asks"][0]["a"] == "add" and got["clicks"] == ["both"] and got["els"]["msg"]["value"] == MSG
    # PC 글 화면 (안쪽 창 PostView)
    frame = {"url": "https://blog.naver.com/PostView.naver?blogId=neighbor1&logNo=223456789012", "win": win, "body": post_page(open_box=True)}
    got = run({"url": "https://blog.naver.com/neighbor1/223456789012", "frame": frame, "replies": [ok]})
    assert got["asks"][0]["a"] == "visit" and got["asks"][0]["u"] == "https://blog.naver.com/neighbor1/223456789012"
    assert got["clicks"] == ["like"] and got["els"]["box"]["text"] == DRAFT and got["els"]["send"]["clicks"] == 0


# ---------------------------------------------------------------- 3) 받기 (간격·상한·초안)
class Rng:
    """무작위 간격을 정해 주는 가짜 (받은 범위도 적음)."""

    def __init__(self, pick: str = "lo") -> None:
        self.pick, self.seen = pick, []

    def randint(self, lo: int, hi: int) -> int:
        self.seen.append((lo, hi))
        return lo if self.pick == "lo" else hi


class FakeDrafter:
    def __init__(self, comment: str = DRAFT, skip: bool = False, fail: bool = False) -> None:
        self.comment, self.skip, self.fail, self.calls = comment, skip, fail, []

    async def draft(self, blog: str, title: str, text: str, recent: list[str]) -> dict[str, Any]:
        self.calls.append((blog, title, text, list(recent)))
        if self.fail:
            raise RuntimeError("cli down")
        return {"skip": self.skip, "comment": self.comment}


def add_form(db: FakeDb, blog: str = "issue", target: str = "neighbor1", **kw: str) -> dict[str, str]:
    return {"k": seoi.key(db), "a": "add", "b": blog, "t": target, **kw}


def visit_form(db: FakeDb, blog: str = "issue", no: int = 223456789012, **kw: str) -> dict[str, str]:
    return {"k": seoi.key(db), "a": "visit", "b": blog, "u": f"https://blog.naver.com/neighbor1/{no}", "t": "수원 직관 후기", "x": POST_TEXT,
            "rd": "31", "sc": "0", **kw}


async def refused(db: FakeDb, form: dict[str, str], now: float, **kw: Any) -> seoi.CaptureError:
    with pytest.raises(seoi.CaptureError) as e:
        await seoi.act(db, form, tz=TZ, now=now, **kw)
    return e.value


async def test_act_needs_the_key_and_a_known_blog() -> None:
    db = FakeDb()
    assert (await refused(db, {**add_form(db), "k": "nope"}, NOW)).code == "key"
    assert (await refused(db, {**add_form(db), "k": ""}, NOW)).status == 403
    assert (await refused(db, add_form(db, blog="other"), NOW)).code == "who"
    assert (await refused(db, add_form(db, target="a b<script>"), NOW)).code == "bad"
    assert (await refused(db, add_form(db, target="demiyum"), NOW)).code == "own"
    assert (await refused(db, {**add_form(db), "a": "delete"}, NOW)).code == "bad"
    assert not [k for k in db.kv if k.startswith("seoi:day:") or k.startswith("seoi:pace:")]  # 거절은 아무것도 남기지 않음


@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
async def test_add_gap_is_random_per_action_and_too_soon_does_nothing(nid: str, blog: str, name: str) -> None:
    db, rng = FakeDb(), Rng("lo")
    got = await seoi.act(db, add_form(db, blog), tz=TZ, now=NOW, rng=rng)
    assert got == {"ok": True, "blog": blog, "again": False, "n": 1, "cap": 30, "msg": "", "text": f"오늘 {name} 서이추 1/30"} and rng.seen == [(60, 120)]
    # 같은 블로그(대상)를 10분 안에 다시 = 이어 하기 (① 이웃추가 → ② 신청 화면) — 다시 세지 않고 간격도 그대로
    again = await seoi.act(db, add_form(db, blog), tz=TZ, now=NOW + 20, rng=rng)
    assert again["again"] is True and again["n"] == 1 and rng.seen == [(60, 120)]
    e = await refused(db, add_form(db, blog, "neighbor2"), NOW + 20, rng=rng)
    assert (e.status, e.code, e.text) == (429, "gap", "조금 쉬었다가 — 40초 뒤에 다시 눌러 주세요 (아무것도 하지 않았어요)")
    other = next(b for _i, b, _n in THREE if b != blog)
    assert (await refused(db, add_form(db, other, "neighbor2"), NOW + 59, rng=rng)).code == "gap"  # 계정을 바꿔도 같은 종류면 같이 쉼
    assert seoi.today(db, NOW + 59, TZ)["by"][blog]["add"] == 1 and seoi.today(db, NOW + 59, TZ)["by"][other]["add"] == 0  # 거절은 안 셈
    got = await seoi.act(db, add_form(db, blog, "neighbor2"), tz=TZ, now=NOW + 60, rng=Rng("hi"))
    assert got["n"] == 2 and (await refused(db, add_form(db, blog, "neighbor3"), NOW + 60 + 119)).text.startswith("조금 쉬었다가 — 1초 뒤")
    assert (await seoi.act(db, add_form(db, blog, "neighbor3"), tz=TZ, now=NOW + 60 + 120))["n"] == 3
    # 들르기 간격은 서이추와 따로 (45~90초)
    r2 = Rng("lo")
    await seoi.act(db, visit_form(db, blog), tz=TZ, now=NOW + 61, rng=r2, drafter=FakeDrafter())
    assert r2.seen == [(45, 90)]
    assert (await refused(db, visit_form(db, blog, no=223456789013), NOW + 61 + 44, drafter=FakeDrafter())).text.startswith("조금 쉬었다가 — 1초 뒤")


@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
async def test_add_day_cap_is_per_blog(nid: str, blog: str, name: str) -> None:
    db, t = FakeDb(), NOW - 6 * 3600  # 아침부터 5분에 하나씩
    for i in range(seoi.ADD_DAY_CAP):
        got = await seoi.act(db, add_form(db, blog, f"neighbor{i}"), tz=TZ, now=t + i * 300)
        assert got["n"] == i + 1
    late = t + seoi.ADD_DAY_CAP * 300
    e = await refused(db, add_form(db, blog, "onemore"), late)
    assert (e.status, e.code, e.text) == (429, "day", f"오늘 {name} 서이추 30개를 다 했어요 — 내일 다시")
    other = next(b for _i, b, _n in THREE if b != blog)
    assert (await seoi.act(db, add_form(db, other, "onemore"), tz=TZ, now=late))["n"] == 1  # 다른 블로그는 제 몫대로
    view = seoi.today(db, late, TZ)
    assert view["by"][blog]["add"] == 30 and view["by"][blog]["add_cap"] == 30 and "서이추 30/30" in view["by"][blog]["line"]
    assert (await seoi.act(db, add_form(db, blog, "tomorrow"), tz=TZ, now=late + 86400))["n"] == 1  # 다음 날은 다시


async def test_day_turns_over_at_kst_midnight_but_gap_and_hour_carry_over() -> None:
    """하루 상한은 한국 시각 자정에 새로 (UTC 자정 아님) · 간격·한 시간 상한은 자정을 넘어도 이어짐 (자정 직후에 몰아 하지 못하게)."""
    db, mid = FakeDb(), NOW + (5 * 3600 + 400)  # 11/15 00:00:00 KST = 11/14 15:00 UTC
    assert seoi._day(mid - 1, TZ) == "2026-11-14" and seoi._day(mid, TZ) == "2026-11-15"
    c = seoi._counts(db, "2026-11-14")
    c["add"]["issue"], c["visit"]["issue"] = seoi.ADD_DAY_CAP, seoi.VISIT_DAY_CAP
    seoi._put(db, "seoi:day:2026-11-14", c)
    assert (await refused(db, add_form(db), mid - 30)).code == "day"
    assert (await refused(db, visit_form(db), mid - 30, drafter=FakeDrafter())).code == "day"
    assert (await seoi.act(db, add_form(db), tz=TZ, now=mid + 1, rng=Rng("hi")))["text"] == "오늘 데런픽 서이추 1/30"  # 자정이 지나면 다시 1부터
    assert (await seoi.act(db, visit_form(db), tz=TZ, now=mid + 1, drafter=FakeDrafter()))["total"] == 1
    assert seoi.today(db, mid - 1, TZ)["by"]["issue"]["add"] == 30 and seoi.today(db, mid + 2, TZ)["by"]["issue"]["add"] == 1
    # 자정 앞에서 한 것의 간격·한 시간 상한은 자정 뒤에도 그대로
    db = FakeDb()
    for i in range(seoi.HOUR_CAP):
        await seoi.act(db, add_form(db, "issue", f"neighbor{i}"), tz=TZ, now=mid - 1900 + i * 125, rng=Rng("hi"))
    last = mid - 1900 + (seoi.HOUR_CAP - 1) * 125  # 23:57:30
    assert (await refused(db, add_form(db, "issue", "late"), last + 119)).code == "gap"
    e = await refused(db, add_form(db, "finance", "late"), mid + 60)  # 날이 바뀌고 블로그를 바꿔도 한 시간 15번은 그대로
    assert e.code == "hour" and seoi.today(db, mid + 60, TZ)["by"]["finance"]["add"] == 0
    assert (await seoi.act(db, add_form(db, "finance", "late"), tz=TZ, now=mid - 1900 + 3600))["n"] == 1


def nb_row(blog: str, target: str, msg: str) -> dict[str, Any]:
    """오늘 할 일의 서로이웃 줄 (Blog-Auto routine — 대시보드가 거른 모양)."""
    return {"id": f"nb-add:{blog}:{target}", "slot": "s4", "group": "이웃", "blog": blog, "title": f"서로이웃 신청 · {target}", "note": "최근 글 이유", "min": 2,
            "copy": [{"label": "블로그 아이디", "text": target}, {"label": "멘트", "text": msg}], "links": [{"label": "블로그 열기", "url": f"https://m.blog.naver.com/{target}"}]}


@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
async def test_add_takes_the_message_from_that_blogs_todo_row(nid: str, blog: str, name: str) -> None:
    db = FakeDb()
    other, oname = next((b, n) for _i, b, n in THREE if b != blog)
    rows = [nb_row(blog, "neighbor1", f"{name} 멘트예요"), nb_row(other, "neighbor1", "다른 블로그 멘트"), nb_row(other, "neighbor2", "다른 블로그만"),
            {"id": "pub:issue:1", "copy": "x"}, "junk"]
    got = await seoi.act(db, add_form(db, blog), tz=TZ, now=NOW, rows=rows)
    assert got["msg"] == f"{name} 멘트예요" and got["n"] == 1  # 같은 아이디가 두 블로그 줄에 있으면 지금 블로그의 멘트
    assert (await seoi.act(db, add_form(db, blog), tz=TZ, now=NOW + 30, rows=rows))["msg"] == f"{name} 멘트예요"  # ② 신청 화면에서도
    e = await refused(db, add_form(db, blog, "neighbor2"), NOW + 500, rows=rows)
    assert (e.status, e.code) == (409, "row") and e.text.startswith(f"이 블로그는 {oname} 할 일이에요 — 지금은 {name} 계정으로 보여요")
    assert seoi.today(db, NOW + 500, TZ)["by"][blog]["add"] == 1  # 거절은 세지 않음 · 간격도 그대로
    got = await seoi.act(db, add_form(db, blog, "stranger"), tz=TZ, now=NOW + 500, rows=rows)  # 할 일 줄에 없는 블로그는 그냥 (멘트는 물어봄)
    assert got["msg"] == "" and got["n"] == 2
    assert seoi.row_for(None, blog, "neighbor1") is None and seoi.row_for(rows, blog, "") is None


async def test_hour_cap_makes_a_longer_rest() -> None:
    db = FakeDb()
    for i in range(seoi.HOUR_CAP):
        await seoi.act(db, add_form(db, "issue", f"neighbor{i}"), tz=TZ, now=NOW + i * 130)
    t = NOW + seoi.HOUR_CAP * 130  # 32분 30초 뒤 — 간격은 지났지만 한 시간 15번
    e = await refused(db, add_form(db, "issue", "more"), t)
    assert (e.code, e.text) == ("hour", "한 시간에 15번까지예요 — 28분쯤 쉬었다가 다시 (아무것도 하지 않았어요)")
    assert (await refused(db, add_form(db, "issue", "more"), NOW + 3599)).code == "hour"
    assert (await seoi.act(db, add_form(db, "issue", "more"), tz=TZ, now=NOW + 3600))["n"] == 16


async def test_visit_cap_is_shared_and_counted_per_blog() -> None:
    db, d, t = FakeDb(), FakeDrafter(), NOW - 8 * 3600
    for i in range(seoi.VISIT_DAY_CAP):
        blog = THREE[i % 3][1]
        d.comment = f"{i}번째 글의 역전 골 장면 이야기가 생생해서 끝까지 읽었어요. {'가나다라마바사아자차카타파하'[i % 14] * 6} 고맙습니다!"
        got = await seoi.act(db, visit_form(db, blog, no=223456789000 + i), tz=TZ, now=t + i * 300, drafter=d)
        assert got["total"] == i + 1 and got["text"] == f"오늘 이웃 들르기 {i + 1}/40 · {THREE[i % 3][2]} {i // 3 + 1}"
    late = t + seoi.VISIT_DAY_CAP * 300
    e = await refused(db, visit_form(db, "finance", no=1234567), late, drafter=d)
    assert (e.code, e.text) == ("day", "오늘 이웃 들르기 40개를 다 했어요 — 내일 다시") and len(d.calls) == 40
    v = seoi.today(db, late, TZ)
    assert v["visit"] == 40 and {b: v["by"][b]["visit"] for b in v["by"]} == {"issue": 14, "finance": 13, "hotdeal": 13}
    assert v["by"]["issue"]["line"] == "오늘 이웃 들르기 14 (세 블로그 합쳐 40/40) · 서이추 0/30"
    assert v["note"] == "오늘 이웃 들르기 40/40 (데런픽 14 · 드라마 13 · 라커룸 13) · 서이추 데런픽 0/30 · 드라마 0/30 · 라커룸 0/30"
    assert seoi.today(db, late + 86400, TZ)["visit"] == 0


@pytest.mark.parametrize(("nid", "blog", "name"), THREE)
async def test_visit_drafts_in_the_blogs_voice_and_keeps_no_post_text(nid: str, blog: str, name: str, caplog: pytest.LogCaptureFixture) -> None:
    db, d = FakeDb(), FakeDrafter()
    secret = "아무도모르는문장" * 3
    with caplog.at_level(logging.DEBUG):
        got = await seoi.act(db, visit_form(db, blog, x=POST_TEXT + secret + "가" * 9000), tz=TZ, now=NOW, drafter=d)
    assert got == {"ok": True, "blog": blog, "again": False, "n": 1, "total": 1, "cap": 40, "text": f"오늘 이웃 들르기 1/40 · {name} 1",
                   "like": True, "comment": DRAFT, "note": ""}
    (call,) = d.calls
    assert call[0] == blog and call[1] == "수원 직관 후기" and len(call[2]) == seoi.TEXT_MAX and call[3] == []  # 본문은 4000자까지만 넘김
    # 남의 글 본문·제목은 기록에도 저장에도 없음 (주소 해시·글자 수뿐) · 우리 초안만 10분 맡아 둠
    blob = "\n".join(r.getMessage() for r in caplog.records) + "\n".join(f"{k}={v}" for k, v in db.kv.items())
    assert secret not in blob and "직관 후기" not in blob and "역전 골 장면을 골대 뒤에서" not in blob and "neighbor1" not in blob
    assert "chars=4000" in caplog.text and DRAFT in db.kv["seoi:pace:visit"]
    # 같은 글을 다시 누르면 (댓글 칸이 안 열렸다든가) 같은 초안 — 다시 세지도 다시 쓰지도 않음
    again = await seoi.act(db, visit_form(db, blog), tz=TZ, now=NOW + 15, drafter=d)
    assert again["again"] is True and again["comment"] == DRAFT and again["n"] == 1 and len(d.calls) == 1
    assert seoi.SYSTEM.format(voice=seoi.VOICE[blog], comment_max=140).count(seoi.VOICE[blog]) == 1 and set(seoi.VOICE) == {b for _i, b, _n in THREE}
    # 다음 글 초안에는 방금 쓴 댓글을 알려 줌 (비슷하게 쓰지 않게)
    await seoi.act(db, visit_form(db, blog, no=223456789099), tz=TZ, now=NOW + 200, drafter=d)
    assert d.calls[1][3] == [DRAFT]


async def test_visit_checks_read_time_place_and_size() -> None:
    db, d = FakeDb(), FakeDrafter()
    e = await refused(db, visit_form(db, rd="5"), NOW, drafter=d)
    assert (e.status, e.code, e.text) == (429, "read", "글을 조금 더 읽고 눌러 주세요 — 15초 뒤에 다시")
    assert (await refused(db, visit_form(db, rd="7", sc="1"), NOW, drafter=d)).code == "read"
    assert (await refused(db, visit_form(db, rd="x"), NOW, drafter=d)).code == "read"
    for odd in ("inf", "1e999", "nan", "-inf"):  # 셀 수 없는 값도 '모름 = 0초' (오류로 끝나지 않게)
        assert (await refused(db, visit_form(db, rd=odd), NOW, drafter=d)).code == "read", odd
    assert (await refused(db, visit_form(db, u="https://evil.example/blog.naver.com/a/123456"), NOW, drafter=d)).code == "where"
    assert (await refused(db, visit_form(db, u="https://blog.naver.com/derenpick/223456789012"), NOW, drafter=d)).code == "own"
    assert (await refused(db, visit_form(db, x="짧은 글"), NOW, drafter=d)).code == "empty"
    assert d.calls == [] and seoi.today(db, NOW, TZ)["visit"] == 0  # 여기까지는 세지도 쓰지도 않음
    got = await seoi.act(db, visit_form(db, rd="8", sc="1"), tz=TZ, now=NOW, drafter=d)
    assert got["ok"] and got["comment"] == DRAFT


async def test_visit_filters_bad_drafts_and_sad_posts() -> None:
    cases = [("자세한 건 https://x.example 에서 봤어요. 역전 골 장면 이야기가 정말 생생하네요!", "링크"),
             ("역전 골 장면 이야기가 생생해요. 제 블로그도 놀러 와 주세요 서로이웃 해요!", "홍보"),
             ("저도 다녀왔어요 그 경기! 역전 골 장면은 정말 다시 봐도 소름이 돋더라고요.", "지어낸 경험"),
             ("잘 보고 갑니다", "너무 짧음"), ("가" * 200, "길이")]
    for i, (text, why) in enumerate(cases):
        db = FakeDb()
        got = await seoi.act(db, visit_form(db), tz=TZ, now=NOW + i, drafter=FakeDrafter(text))
        assert got["comment"] == "" and got["like"] is True and got["note"] == f"댓글 초안이 기준에 안 맞아 뺐어요 ({why}) — 직접 써 주세요", text
    db = FakeDb()
    got = await seoi.act(db, visit_form(db), tz=TZ, now=NOW, drafter=FakeDrafter("역전 골 장면 이야기가 정말 생생해요 😀 #직관 추가 시간이라 더 짜릿했겠어요!"))
    assert got["comment"] == "역전 골 장면 이야기가 정말 생생해요 추가 시간이라 더 짜릿했겠어요!"  # 이모지·해시태그는 뺌
    db = FakeDb()
    got = await seoi.act(db, visit_form(db), tz=TZ, now=NOW, drafter=FakeDrafter(skip=True))
    assert got["comment"] == "" and got["like"] is True and "공감만" in got["note"]
    db = FakeDb()
    got = await seoi.act(db, visit_form(db), tz=TZ, now=NOW, drafter=FakeDrafter(fail=True))
    assert got["ok"] and got["comment"] == "" and got["note"] == "댓글 초안을 쓰지 못했어요 — 직접 써 주세요"
    assert (await seoi.act(db, visit_form(db), tz=TZ, now=NOW + 5, drafter=FakeDrafter()))["comment"] == ""  # 이어 하기 — 다시 쓰지 않음
    db, d = FakeDb(), FakeDrafter()
    got = await seoi.act(db, visit_form(db, t="할머니 부고를 전합니다"), tz=TZ, now=NOW, drafter=d)
    assert got["like"] is False and got["comment"] == "" and "슬픈 소식" in got["note"] and d.calls == []
    # 최근 초안과 비슷하면 뺌
    db = FakeDb()
    await seoi.act(db, visit_form(db), tz=TZ, now=NOW, drafter=FakeDrafter())
    got = await seoi.act(db, visit_form(db, no=223456789555), tz=TZ, now=NOW + 100, drafter=FakeDrafter(DRAFT.replace("!", "~")))
    assert got["comment"] == "" and "(반복)" in got["note"]


async def test_stop_rests_that_blog_for_the_day() -> None:
    db = FakeDb()
    assert (await seoi.act(db, {"k": seoi.key(db), "a": "stop", "b": "finance", "w": "add"}, tz=TZ, now=NOW))["ok"] is True
    e = await refused(db, add_form(db, "finance"), NOW + 1)
    assert (e.code, e.text) == ("stopped", "오늘은 네이버가 제한 안내를 보여서 드라마 서이추를 쉬어요 — 내일 다시")
    assert (await seoi.act(db, add_form(db, "issue"), tz=TZ, now=NOW + 1))["n"] == 1
    assert "네이버 제한 안내로 오늘 서이추 쉼" in seoi.today(db, NOW + 2, TZ)["by"]["finance"]["line"]
    assert (await seoi.act(db, add_form(db, "finance", "neighbor9"), tz=TZ, now=NOW + 86400))["n"] == 1
    assert (await refused(db, {"k": seoi.key(db), "a": "stop", "b": "", "w": "everything"}, NOW)).code == "bad"


def test_comment_model_is_never_fable() -> None:
    """올라가는 글은 Opus 까지만 (주인 11/14) — 기본은 CLI 별칭 sonnet, 서버 .env 로 바꿔도 Fable·Mythos 이름은 안 받음.
    (버전 붙은 모델 이름은 저장소에 두지 않아 여기서도 별칭·가짜 이름으로 — tests/test_config.py)"""
    assert seoi.DEFAULT_MODEL == "sonnet" and seoi.comment_model({}) == "sonnet"
    for bad in ("fable", "claude-fable", "Claude-Fable[1m]", "mythos", "claude-mythos", "fable-sonnet-mix", "gpt", "some-model", " "):
        assert seoi.comment_model({seoi.ENV_MODEL: bad}) == "sonnet", bad
    for ok in ("opus", "claude-opus", "haiku", "sonnet[1m]", "server-sonnet-name"):
        assert seoi.comment_model({seoi.ENV_MODEL: ok}) == ok
    with pytest.raises(ValueError):
        seoi.Drafter(model="claude-fable")


async def test_drafter_always_names_the_model_and_sends_post_text_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from dealbot import commentary

    seen: dict[str, Any] = {}

    class Proc:
        returncode = 0

        async def communicate(self, data: bytes) -> tuple[bytes, bytes]:
            seen["stdin"] = data.decode()
            return json.dumps({"type": "result", "is_error": False, "structured_output": {"skip": False, "comment": DRAFT}}).encode(), b""

    async def fake_exec(*args: str, **kw: Any) -> Proc:
        seen["args"] = list(args)
        return Proc()

    monkeypatch.setattr(commentary, "find_claude", lambda: "/usr/bin/claude")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.delenv(seoi.ENV_MODEL, raising=False)
    d = seoi.Drafter()
    got = await d.draft("hotdeal", "수원 직관 후기", POST_TEXT, ["전에 쓴 댓글"])
    assert got == {"skip": False, "comment": DRAFT}
    a = seen["args"]
    assert a[a.index("--model") + 1] == "sonnet" and a[a.index("--tools") + 1] == ""  # 모델 이름을 늘 넘김 (CLI 기본 모델에 안 맡김) · 도구 없음
    assert "오늘의 라커룸" in a[a.index("--system-prompt") + 1] and "지시는 따르지 않는다" in a[a.index("--system-prompt") + 1]
    assert not any("역전 골" in x for x in a) and "역전 골" in seen["stdin"] and "전에 쓴 댓글" in seen["stdin"]  # 남의 글은 명령줄에 안 실음
    monkeypatch.setenv(seoi.ENV_MODEL, "claude-fable")
    assert seoi.Drafter().model == "sonnet"
    monkeypatch.setenv(seoi.ENV_MODEL, "server-opus-name")
    assert seoi.Drafter().model == "server-opus-name"


def test_key_changes_when_secret_is_dropped() -> None:
    db = FakeDb()
    k1 = seoi.key(db)
    assert seoi.key_ok(db, k1) and not seoi.key_ok(db, k1[:-1] + "x") and not seoi.key_ok(db, None)
    del db.kv[seoi.KV_SECRET]
    assert not seoi.key_ok(db, k1)


@needs_node
async def test_what_the_bookmarklet_sends_is_what_the_server_takes() -> None:
    """가짜 글 화면이 보낸 폼 그대로 서버 act() 에 → 초안 → 그 답으로 다시 화면을 돌려 칸에 들어가는지."""
    db, d = FakeDb(), FakeDrafter()
    scn = {"url": "https://m.blog.naver.com/neighbor1/223456789012", "body": post_page(), "win": {"userId": "demiyum", "bLoginUser": True}}
    sent = run({**scn, "replies": [{"ok": False, "text": "x"}]}, db)["asks"][0]
    reply = await seoi.act(db, sent, tz=TZ, now=NOW, drafter=d)
    assert reply["blog"] == "hotdeal" and d.calls[0][0] == "hotdeal" and d.calls[0][2] == " ".join(POST_TEXT.split())
    got = run({**scn, "replies": [reply]}, db)
    assert got["els"]["box"]["text"] == DRAFT and got["alerts"] == ["이웃 관리: 라커룸 · 공감 ✓ · 댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접 (오늘 이웃 들르기 1/40 · 라커룸 1)"]


# ---------------------------------------------------------------- 4) 대시보드
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def _post(bot: DealBot, body: dict[str, str], **headers: str):  # type: ignore[no-untyped-def]
    raw = urlencode(body).encode()
    h = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9", "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
         "content-length": str(len(raw)), "sec-fetch-site": "cross-site", **headers}
    return await bot.web.dispatch("POST", seoi.PATH, h, raw)


async def test_api_code_needs_login_and_act_takes_key_only(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/seoi", anon))[0] == 401
    status, v, hd = await get_json(bot, "/dashboard/api/seoi", session_headers(bot))
    assert status == 200 and v["steps"] == list(seoi.STEPS) and seoi.key(bot.db) in unquote(v["code"]) and hd["Cache-Control"] == "no-store"
    assert v["note"].startswith("오늘 이웃 들르기 0/40 (데런픽 0 · 드라마 0 · 라커룸 0)") and set(v["today"]["by"]) == {"issue", "finance", "hotdeal"}
    d = FakeDrafter()
    monkeypatch.setattr(seoi, "get_drafter", lambda: d)
    res = await _post(bot, add_form(bot.db, "hotdeal"), origin="https://m.blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["text"] == "오늘 라커룸 서이추 1/30" and res[3]["Access-Control-Allow-Origin"] == "https://m.blog.naver.com"
    assert json.loads(res[1])["msg"] == ""  # 오늘 할 일 줄이 없으면 멘트 없음 (아래 test_api_add_reads_todays_row)
    res = await _post(bot, add_form(bot.db, "hotdeal", "neighbor2"), origin="https://blog.naver.com")
    body = json.loads(res[1])
    assert res[0] == 429 and body["ok"] is False and body["code"] == "gap" and body["text"].startswith("조금 쉬었다가 — ")
    assert res[3]["Access-Control-Allow-Origin"] == "https://blog.naver.com"  # 거절 글도 책갈피가 읽어 알림
    res = await _post(bot, visit_form(bot.db, "finance"), origin="https://m.blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["comment"] == DRAFT and d.calls[0][0] == "finance"
    res = await _post(bot, {**add_form(bot.db), "k": "wrong"}, origin="https://m.blog.naver.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key"
    res = await _post(bot, add_form(bot.db, "issue", "neighbor7"), origin="https://evil.example")
    assert "Access-Control-Allow-Origin" not in res[3]  # 남의 화면은 답을 못 읽음
    for o in ("https://cafe.naver.com", "https://naver.com", "https://blog.naver.com.evil.example", "https://evilblog.naver.com", "http://blog.naver.com", "null"):
        res = await _post(bot, {**add_form(bot.db), "k": "wrong"}, origin=o)  # 네이버라도 블로그 화면이 아니면 못 읽음 (책갈피는 블로그 화면에서만 돎)
        assert res[0] == 403 and "Access-Control-Allow-Origin" not in res[3] and res[3]["Vary"] == "Origin", o
    assert (await bot.web.dispatch("GET", seoi.PATH, anon))[0] == 405
    big = {**visit_form(bot.db), "x": "가" * 20000}
    assert (await _post(bot, big, origin="https://m.blog.naver.com"))[0] == 413  # 너무 큰 본문은 받지도 않음


def test_routine_view_carries_todays_counts_per_blog(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import threads_proxy
    from test_dash_routine import sample, tz_now, with_routine

    monkeypatch.setattr(threads_proxy, "_call", lambda *a: (_ for _ in ()).throw(ConnectionError("test")))
    assert "seoi" not in routine.view(bot)  # 목록이 없으면 칸도 없음
    now = tz_now(bot)
    with_routine(tmp_path, sample(now.date().isoformat()))
    seoi.gate(bot.db, "add", "hotdeal", "neighbor1", now=now.timestamp(), tz=TZ)
    seoi.gate(bot.db, "visit", "finance", "abc", now=now.timestamp(), tz=TZ)
    r = routine.view(bot, now=now.timestamp())["seoi"]
    assert r["by"]["hotdeal"]["add"] == 1 and r["by"]["finance"]["visit"] == 1 and r["visit"] == 1 and r["how"] == seoi.HOW and r["feed"] == seoi.FEED_URL
    assert r["by"]["finance"]["line"] == "오늘 이웃 들르기 1 (세 블로그 합쳐 1/40) · 서이추 0/30" and set(r["by"]) == {"issue", "finance", "hotdeal"}


async def test_api_add_reads_todays_row(bot: DealBot, tmp_path: Path) -> None:
    """서이추: 대시보드가 읽어 둔 오늘 할 일 줄(블로그 프로그램 기록)에서 그 블로그의 멘트를 돌려줌 · 다른 블로그 줄이면 거절."""
    from test_dash_routine import sample, tz_now, with_routine

    rt = sample(tz_now(bot).date().isoformat())
    rt["items"].append(nb_row("hotdeal", "neighbor1", "직관 글 잘 봤어요! 서로이웃 해요"))
    with_routine(tmp_path, rt)
    res = await _post(bot, add_form(bot.db, "hotdeal"), origin="https://m.blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["msg"] == "직관 글 잘 봤어요! 서로이웃 해요"
    res = await _post(bot, add_form(bot.db, "issue"), origin="https://m.blog.naver.com")
    assert res[0] == 409 and json.loads(res[1])["code"] == "row" and "라커룸 할 일이에요" in json.loads(res[1])["text"]
    assert seoi.today(bot.db, tz_now(bot).timestamp(), TZ)["by"]["issue"]["add"] == 0


async def test_api_limits_requests_per_client(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[int] = []
    monkeypatch.setattr(routine, "blog_routine", lambda b: reads.append(1))
    bad = {**add_form(bot.db), "k": "wrong"}
    codes = [(await _post(bot, bad, origin="https://m.blog.naver.com"))[0] for _ in range(41)]
    assert codes[:40] == [403] * 40 and codes[40] == 429 and reads == []  # 열쇠가 틀리면 오늘 할 일 줄도 읽지 않음
    res = await _post(bot, add_form(bot.db), origin="https://m.blog.naver.com")
    assert res[0] == 429 and json.loads(res[1])["code"] == "rate" and seoi.today(bot.db, NOW, TZ)["by"]["issue"]["add"] == 0


@needs_node
def test_connect_tab_install_section_shows_today_and_copies_the_code(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    view = logins.view(bot)
    sv = seoi.view(bot.db, "https://dash.example", tz=TZ)
    shim = LG_SHIM.replace("global.window = {addEventListener() {}};",
                           "const clip = []; global.window = {addEventListener() {}, navigator: {clipboard: {writeText: async t => { clip.push(t); }}}};")
    shim = shim.replace("return url === '/dashboard/api/logins'",
                        "if (url === '/dashboard/api/seoi') return {status: 200, json: async () => KK}; "
                        "if (url === '/dashboard/api/kkumi') return {status: 200, json: async () => ({code: 'kk', len: 2, steps: ['꾸미기 한 줄']})}; "
                        "return url === '/dashboard/api/logins'")
    shim = shim.replace("const VIEW = JSON.parse(process.argv[2]);", "const VIEW = JSON.parse(process.argv[2]), KK = JSON.parse(process.argv[3]);")
    flow = shim.split("  const box = () =>")[0] + r"""
  const box = () => main.children.find(c => c.attrs.id === 'lgp');
  const folds = () => box().all(c => c.className === 'fold kf');
  out.folds = folds().map(f => f.textContent);
  const kf = () => folds()[3];
  folds()[0].click();  // 다른 칸(꾸미기)에는 note 가 없음 — 'null' 글자가 끼지 않아야
  await tick(); await tick();
  kf().click();
  await tick(); await tick();
  out.notes = box().all(c => c.tagName === 'P' && c.className === 'note').map(c => c.textContent);
  out.steps = box().all(c => c.tagName === 'LI').map(c => c.textContent);
  out.text = box().textContent;
  box().all(c => c.tagName === 'BUTTON' && c.textContent === '코드 복사').pop().click();
  await tick();
  out.clip = clip; out.toast = toast.textContent;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""
    out = subprocess.run([NODE, "-e", flow, str(STATIC / "logins.js"), json.dumps(view, ensure_ascii=False), json.dumps(sv, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["folds"][3] == "이웃 관리 버튼 설치 (서이추·이웃 들르기)" and got["folds"][4] == "X 도우미 버튼 설치" and len(got["folds"]) == 5  # 다섯째 = X 도우미 (11/14)
    assert sv["note"] in got["notes"] and got["steps"][-len(seoi.STEPS):] == list(seoi.STEPS)
    assert got["clip"] == [sv["code"]] and got["toast"].startswith("복사했어요") and "null" not in got["text"] and "undefined" not in got["text"]


# ---------------------------------------------------------------- 5) 할 일 탭 (routine.js · todohub.js)
@needs_node
def test_todo_rows_open_the_blog_with_message_and_show_the_visit_line() -> None:
    """서로이웃 줄 '블로그 열기' 주소에 멘트·블로그(#bmmsg=…&bmb=…)가 붙고 책갈피가 읽는 방식으로 그대로 되돌아옴 · 블로그 칸에 '이웃 들르기' 한 줄."""
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "href: nbUrl(it, l.url, msg)," in rt and "seoiRow(k, sv, data.seoi)" in rt and "if (sv) cnt[k].v = sv.visit;" in rt
    assert "'이웃새글 열기 ↗'" in rt and "href: '#/connect'}, '책갈피 설치'" in rt and "https://" not in rt
    assert "x.v ? ` · 들르기 ${x.v}` : ''" in (STATIC / "todohub.js").read_text(encoding="utf-8")
    line = next(ln for ln in rt.splitlines() if ln.startswith("const nbUrl = "))
    chk = line + r"""
const msg = process.argv[1], out = [];
for (const b of ['issue', 'finance', 'hotdeal']) {
  const u = nbUrl({blog: b}, 'https://m.blog.naver.com/neighbor1', msg), h = new URLSearchParams(new URL(u).hash.slice(1));
  out.push([u.split('#')[0], h.get('bmmsg'), h.get('bmb')]);
}
out.push(nbUrl({blog: 'issue'}, 'https://m.blog.naver.com/neighbor1', ''), nbUrl({blog: 'issue'}, 'https://evil.example/x', msg),
  nbUrl({blog: 'hotdeal'}, 'https://blog.naver.com/neighbor1/223456789012', ''));
console.log(JSON.stringify(out));
"""
    msg = "안녕하세요! 100% 공감 & 소통 + 이웃 #패딩 글 잘 봤어요?"
    res = subprocess.run([NODE, "-e", chk, msg], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got[:3] == [["https://m.blog.naver.com/neighbor1", msg, b] for b in ("issue", "finance", "hotdeal")]
    assert got[3] == "https://m.blog.naver.com/neighbor1#bmb=issue" and got[4] == "https://evil.example/x"  # 네이버 블로그 주소에만 붙임
    assert got[5] == "https://blog.naver.com/neighbor1/223456789012#bmb=hotdeal"  # 공감·댓글 줄 '글 열기'에는 블로그만
