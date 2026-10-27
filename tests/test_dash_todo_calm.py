"""할 일 탭 가볍게 (주인 10/27 — "대시보드도 그거 맞춰서 최대한 내가 부담 안느끼게 깔끔하게 … 보여야하는 기능들은 그대로").

1) app.js vTodo 순서: 급한 알림(있을 때만) → (routine.js '오늘 할 일' 칸) → 할 일 → 자동 작업 한 줄 → 알림 켜기 안내 → '더 보기'
   (오늘 한 일 · 오늘 정리 · 오늘 바뀐 것 · 직전 AI 점검은 '더 보기' 를 눌러야 보임 — 없어지지 않음).
2) 자동 작업: 모두 정상이면 한 줄 '자동 작업 정상 · …' (누르면 펼침) · 문제는 따로 줄로 늘 보임.
3) 오늘 할 일 칸에 같은 글(같은 블로그·글 번호: pub:<blog>:<pid> · rsv:<blog>:<pid>)이 있는 '발행 대기' 할 일은 그 칸에서만.
   사진·질문처럼 다른 일이 남은 글, 칸에 없는 글은 그대로.
4) routine.js: 급한 알림이 맨 위에 있으면 그 바로 아래에 붙음 · 할 일 id 목록(ids)과 'sb-routine' 알림 (app.js 가 다시 그림).
5) logins.js: 주인 몫이 없으면 '로그인 모두 정상' 한 줄 (누르면 펼침) · 필요한 로그인이 있으면 늘 펼침.
6) 할 일 탭 여유 (10/27 '답답한 감'): app.css 규칙은 [data-tab=todo] 아래만 · 체크 칸 44×44 · 칸 머리는 시트 .sh 와 겹치지 않는 .sl.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import logins, routine
from test_dash_logins import DOM_SHIM as LG_SHIM
from test_dash_routine import DOM_SHIM as RT_SHIM
from test_dash_routine import at, sample, tz_now, with_routine
from test_dash_state import state, write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def node(script: str, *args: str) -> dict:
    out = subprocess.run([NODE, "-e", script, *args], capture_output=True, text=True, timeout=30)  # type: ignore[list-item]
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ---------------------------------------------------------------- 1)~3) app.js vTodo (함수만 떼어 가짜 el 로)
TODO_STUB = r"""
const mk = (t, a, k) => ({t, a: a || {}, k: k.flat(9).filter(x => x != null && x !== false),
  classList: {contains: c => ((a || {}).class || '').split(' ').includes(c)}});
const el = (t, a, ...k) => mk(t, a, k);
const btn = (cls, label, on, x) => el('button', Object.assign({class: cls, onclick: on}, x), label);
const row = (t, s, r, bad) => el('div', {class: 'row' + (bad ? ' bad' : '')}, t, ' | ', s);
const item = it => el('article', {class: 'itm', iid: it.iid});
const vis = xs => xs || [];
const ago = () => '방금';
const doneRow = () => el('div', {class: 'done'}, '오늘 한 일');
const devRow = (d, h) => d && el('div', {class: 'dev'}, d.title);
const digestSheet = () => 0;
let BANNER = null, renders = 0;
const banner = () => BANNER, render = () => { renders++; };
const OPEN = {}, R = {tab: 'todo', sub: ''}, W = {};
let ALL = false, S = null;
const txt = n => n == null ? '' : typeof n === 'string' ? n : n.k.map(txt).join('');
const name = n => n.a.iid || (n.a.class === 'bn bad' ? 'urgent' : n.a.class === 'bn' ? 'banner' : n.a.class === 'foot' ? 'foot:' + txt(n)
  : n.a.class === 'box' ? 'auto' : n.a.class === 'empty' ? 'empty' : n.a.class || n.t);
const find = (m, label) => m.t === 'button' && txt(m) === label ? m : m.k.filter(x => typeof x !== 'string').map(x => find(x, label)).find(Boolean);
const click = (n, label) => find(n, label).a.onclick();
"""


def todo_script(js: str) -> str:
    parts = [re.search(r"^const rtIds = .*$", js, re.M), re.search(r"^function autoBox\(\) \{.*?^\}$", js, re.M | re.S),
             re.search(r"^function vTodo\(\) \{.*?^\}$", js, re.M | re.S)]
    assert all(parts), "vTodo·autoBox·rtIds 를 app.js 에서 못 찾음"
    return TODO_STUB + "\n".join(m.group(0) for m in parts if m)


def todo_items() -> list[dict]:
    def it(iid: str, kind: str, sev: str = "todo", group: str = "오늘 안에") -> dict:
        return {"iid": iid, "kind": kind, "sev": sev, "group": group, "src": "blog", "title": iid, "detail": {}}

    return [it("sys-blogauto", "blogauto_down", "stop", "지금 꼭"), it("post-issue-12", "publish"), it("post-issue-13", "photos"),
            it("post-finance-3", "publish"), it("post-hotdeal-12", "publish"), it("blog-issue-stall", "stall", "info", "여유 있을 때")]


@needs_node
async def test_todo_order_auto_line_and_more_toggle(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    st = await state(bot)
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    st["todo"] = todo_items()
    st["digest"] = {"latest": "digest-20261027-am"}
    st["devlog"] = {"title": "오늘 바뀐 것 · 2개", "rows": []}
    st["opscheck"] = {"title": "직전 AI 점검 · 10/27 09:04 · 이상 없음", "rows": []}
    con = st["connect"]
    con["bots"]["dealbot"]["paused"] = False
    con["bots"]["blogauto"]["ok"] = True
    con["sc"] = {"text": "정상 · 오늘 링크 3/40", "bad": False}
    con["bots"]["dealbot"]["collectors"] = [{"label": "뽐뿌", "enabled": True, "ok": True, "text": "방금"},
                                            {"label": "퀘이사존", "enabled": True, "ok": True, "text": "3분 전"},
                                            {"label": "꺼둔 곳", "enabled": False, "ok": False, "text": "아직 안 봄"}]
    script = todo_script(js) + r"""
S = JSON.parse(process.argv[1]);
const out = {};
W.SBRoutine = {ids: () => ['pub:issue:12', 'rsv:hotdeal:12', 'nb:issue:a', 'pub:finance:x', 'post-finance-3']};
let v = vTodo();
out.plain = v.map(name);
const auto = v.find(n => n.a.class === 'box');
out.autoLine = auto.k.map(txt); out.autoOpen = auto.k[0].a['aria-expanded'];
click(auto, out.autoLine[0]);
out.autoRows = vTodo().find(n => n.a.class === 'box').k.map(txt);
click(v.find(n => n.a.class === 'foot'), '더 보기');
v = vTodo();
out.more = v.map(name).slice(-6); out.moreText = v.slice(-4).map(txt);
BANNER = el('div', {class: 'bn bad'}, '알림이 어디로도 가지 않아요');
out.urgent = vTodo().map(name).slice(0, 2);
BANNER = el('div', {class: 'bn'}, '알림을 켜면 할 일이 바로 와요');
out.nag = vTodo().map(name);
W.SBRoutine = undefined;  // routine.js 가 아직 안 떴으면 겹침 빼기 없음
out.noRoutine = vTodo().filter(n => n.a.iid).map(n => n.a.iid);
S.connect.bots.blogauto.ok = false; S.connect.bots.dealbot.collectors[1].ok = false; OPEN.auto = false;
out.bad = vTodo().find(n => n.a.class === 'box').k.map(n => [n.a.class, txt(n)]);
S.connect.bots.dealbot.paused = true; S.connect.bots.dealbot.collectors.forEach(c => { c.ok = false; }); S.connect.sc.bad = true;
out.allBad = vTodo().find(n => n.a.class === 'box').k.map(n => n.a.class);
S.todo = []; OPEN.more = false; BANNER = null;
out.empty = vTodo().map(name);
R.sub = 'deal';
out.deal = vTodo().map(name);
out.renders = renders;
console.log(JSON.stringify(out));
"""
    got = node(script, json.dumps(st, ensure_ascii=False))
    # 할 일 → 자동 작업 한 줄 → '더 보기' (오늘 한 일·정리·바뀐 것·점검은 접힘). Pick #12 '발행 대기'는 오늘 할 일 칸에 → 뺌
    # (같은 번호의 핫딜 #12 는 칸에 rsv:hotdeal:12 로 있어서 뺌 · 사진이 남은 #13·칸에 없는 아카이브 #3 은 그대로)
    assert got["plain"] == ["grp", "sys-blogauto", "grp", "post-issue-13", "post-finance-3", "grp", "blog-issue-stall", "auto", "foot:더 보기"]
    assert got["autoLine"] == ["자동 작업 정상 · 딜봇·블로그 프로그램·쇼핑커넥트·수집기 2"] and got["autoOpen"] == "false"
    assert got["autoRows"] == ["자동 작업 정상 · 딜봇·블로그 프로그램·쇼핑커넥트·수집기 2", "딜봇 | 정상", "블로그 프로그램 | 정상 · 방금 신호",
                               "쇼핑커넥트 | 정상 · 오늘 링크 3/40", "뽐뿌 | 정상 · 방금", "퀘이사존 | 정상 · 3분 전"]  # 꺼 둔 수집기는 안 셈
    assert got["more"] == ["auto", "foot:접기", "done", "foot:오늘 정리 보기", "dev", "dev"]
    assert got["moreText"] == ["오늘 한 일", "오늘 정리 보기", "오늘 바뀐 것 · 2개", "직전 AI 점검 · 10/27 09:04 · 이상 없음"]
    assert got["urgent"] == ["urgent", "grp"]  # 급한 알림만 맨 위 (오늘 할 일 칸은 routine.js 가 그 바로 아래에)
    assert got["nag"].index("banner") == got["nag"].index("auto") + 1 and got["nag"][0] == "grp"  # 알림 켜기 안내는 아래로
    assert got["noRoutine"] == ["sys-blogauto", "post-issue-12", "post-issue-13", "post-finance-3", "post-hotdeal-12", "blog-issue-stall"]
    assert got["bad"] == [["row bad", "블로그 프로그램 | 응답 없음"], ["row bad", "퀘이사존 | 오류 · 3분 전"],
                          ["fold", "자동 작업 정상 · 딜봇·쇼핑커넥트·수집기 1"]]  # 문제는 따로 줄 · 정상은 한 줄
    assert got["allBad"] == ["row bad"] * 5  # 모두 문제면 한 줄은 없음
    assert got["empty"] == ["empty", "auto", "foot:더 보기"]
    assert got["deal"][0] == "top" and "auto" not in got["deal"] and not any(x.startswith("foot") for x in got["deal"])
    assert got["renders"] == 2  # 자동 작업 펼침 · 더 보기


def test_app_js_keeps_every_todo_footer_and_redraws_when_routine_arrives() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    todo = re.search(r"^function vTodo\(\) \{.*?^\}$", js, re.M | re.S).group(0)  # type: ignore[union-attr]
    for word in ("doneRow()", "오늘 정리 보기", "devRow(S.devlog)", "devRow(S.opscheck, '직전 AI 점검')", "autoBox()", "'더 보기'"):
        assert word in todo, word
    assert "W.addEventListener('sb-routine'" in js and "R.tab === 'todo' && render()" in js
    # null·false 는 화면에 'null' 글자로 그리지 않음 (빈 칸은 그냥 빠짐)
    assert "m.replaceChildren(...v.flat(9).filter(x => x != null && x !== false))" in js


# ---------------------------------------------------------------- 4) routine.js: 급한 알림 아래 · ids · sb-routine
RT_MOUNT = r"""
  require(process.argv[1]);
  await tick(); await tick();
  const order = () => main.children.map(c => c.attrs.id || c.className || c.tagName);
  out.first = order();
  out.ids = window.SBRoutine.ids(); out.ev = events.slice();
  const b2 = new E('div'); b2.className = 'bn bad';
  main.replaceChildren(b2, new E('div'));  // app.js 가 다시 그림 (급한 알림 그대로)
  await tick();
  out.again = order(); out.ev2 = events.length;
  main.replaceChildren(new E('article'), new E('div'));  // 급한 알림이 사라짐
  await tick();
  out.noBanner = order();
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@needs_node
def test_routine_card_sits_right_under_an_urgent_banner_and_reports_ids(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    shim = RT_SHIM.split("(async () => {")[0].replace(
        "  click() {",
        "  get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; }\n"
        "  after(c) { const p = this.parentNode; if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c);"
        " c.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, c); p._n(); }\n  click() {", 1).replace(
        "global.window = {addEventListener() {},", "const events = [];\nglobal.window = {addEventListener() {}, dispatchEvent: e => events.push(e.type),", 1).replace(
        "main.append(new E('p'));", "const bn = new E('div'); bn.className = 'bn bad'; main.append(bn, new E('p'));", 1)
    assert "dispatchEvent" in shim and "get nextSibling" in shim
    got = node(shim + "(async () => {" + RT_MOUNT, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False))
    assert got["first"] == ["bn bad", "rtp", "P"]  # 급한 알림 바로 아래
    assert got["ids"] == ["pub:finance:1", "nb:issue:a", "sns:x:1"] and got["ev"] == ["sb-routine"]
    assert got["again"] == ["bn bad", "rtp", "DIV"] and got["ev2"] == 1  # 다시 그려도 같은 자리 · 목록이 같으면 다시 알리지 않음
    assert got["noBanner"] == ["rtp", "ARTICLE", "DIV"]


# ---------------------------------------------------------------- 5) logins.js: 모두 정상이면 한 줄
LG_FLOW = r"""
  require(process.argv[1]);
  await tick(); await tick();
  const box = () => main.children.find(c => c.attrs.id === 'lgp');
  const folds = () => box().all(c => c.className === 'fold');
  out.rows = box().all(c => c.className.startsWith('row')).length;
  out.h2 = box().all(c => c.tagName === 'H2').map(c => c.textContent);
  out.fold = folds().map(f => [f.textContent, f.attrs['aria-expanded']]);
  if (folds().length) {
    folds()[0].click();
    out.open = folds().map(f => [f.textContent, f.attrs['aria-expanded']]);
    out.openRows = box().all(c => c.className.startsWith('row') && c.attrs['data-id']).length;
    main.replaceChildren(new E('div'));  // app.js 가 다시 그려도 펼친 채
    await tick();
    out.keep = folds()[0].attrs['aria-expanded'];
    folds()[0].click();
    out.closed = box().all(c => c.className.startsWith('row')).length;
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


@needs_node
def test_logins_collapse_to_one_line_when_nothing_needs_the_owner(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    base = logins.view(bot)
    calm = dict(base, need=0, qr=None, vnc=None, rows=[dict(r, bad=False, state="ok", text="로그인됨") for r in base["rows"]])
    shim = LG_SHIM.split("(async () => {")[0]
    got = node(shim + "(async () => {" + LG_FLOW, str(STATIC / "logins.js"), json.dumps(calm, ensure_ascii=False))
    assert got["rows"] == 0 and got["h2"] == [] and got["fold"] == [["로그인 모두 정상", "false"]]  # 한 줄만
    assert got["open"] == [["로그인 모두 정상", "true"]] and got["openRows"] == len(calm["rows"]) and got["keep"] == "true"
    assert got["closed"] == 0
    # 쉬는 중인 로그인이 있으면 '모두 정상' 이라고 하지 않음 (그래도 주인 몫은 없어서 접음)
    rest = dict(calm, rows=[dict(calm["rows"][0], state="resting", text="쉬는 중")] + calm["rows"][1:])
    got = node(shim + "(async () => {" + LG_FLOW, str(STATIC / "logins.js"), json.dumps(rest, ensure_ascii=False))
    assert got["fold"] == [["로그인 · 지금 할 것 없음", "false"]] and got["rows"] == 0
    # 주인이 로그인해야 하는 줄이 있으면 늘 펼침 (예전 그대로)
    need = dict(calm, need=1, rows=[dict(calm["rows"][0], bad=True, state="needed", text="로그인 필요")] + calm["rows"][1:])
    got = node(shim + "(async () => {" + LG_FLOW, str(STATIC / "logins.js"), json.dumps(need, ensure_ascii=False))
    assert got["fold"] == [] and got["h2"][0] == "로그인 관리 · 필요 1" and got["rows"] == len(calm["rows"])


# ---------------------------------------------------------------- 6) 할 일 탭 여유 (주인 10/27 '답답한 감 — 간격을 넓혀 가독성')
def test_todo_tab_spacing_is_scoped_and_tap_targets_are_44px() -> None:
    js, css, rt = ((STATIC / f).read_text(encoding="utf-8") for f in ("app.js", "app.css", "routine.js"))
    assert "m.dataset.tab = R.tab;" in js  # 탭 이름 → app.css 가 할 일 탭만 넓힘 (다른 탭 모양은 그대로)
    tail = css.split("할 일 탭 여유", 1)[1].split("*/", 1)[1]
    sels = [s.strip() for rule in re.findall(r"([^{}]+)\{", tail) for s in rule.split(",")]
    assert sels and all(s.startswith("[data-tab=todo] ") for s in sels), sels
    m = re.search(r"#rtp \.cb\{([^}]*)\}", rt)
    assert m and "width:44px" in m.group(1) and "height:44px" in m.group(1)  # 체크 칸
    assert re.search(r"#rtp \.sl\{[^}]*min-height:56px", rt) and "class: 'sl'" in rt and "class: 'cb'" in rt
    assert "'sh'" not in rt and "'ck'" not in rt  # 시트(.sh)·사진 판(.ck) app.css 규칙과 겹치지 않는 이름
