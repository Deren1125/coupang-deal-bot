"""SNS 탭 (주인 10/26 — '스레드·인스타·트위터·룩북 릴스는 별개의 SNS 라 블로그 탭에 있으면 안 돼').

- sns.js: 처음 열 때만 받는 상자 파일 (meta sb-sns) · 바깥 주소·innerHTML 없음 · 문법 (node --check)
- 상자는 칸마다 하나를 두고 다시 쓴다: 서버에서 스스로 읽는 화면(threads·x)은 탭을 열 때 · 새로고침 · (스레드는) 글 칸 값이 바뀔 때만
  다시 붙이고 (20초 새로고침에 X 승인함에서 고치던 댓글이 사라지지 않게), S 로 그리는 화면(xpost·lookbook)은 매번 다시 그린다
- app.js: 아래 탭 SNS (마지막에 본 칸 기억) · 예전 '#/blog/threads|x|lookbook' 주소는 '#/sns/…' 로 · 블로그 탭엔 블로그 셋과 '도구' 한 칸
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from dealbot.dashboard import api as dash_api
from dealbot.dashboard import shell, webapp

STATIC = webapp.STATIC_DIR


def test_sns_js_is_lazy_local_and_safe() -> None:
    js = (STATIC / "sns.js").read_text(encoding="utf-8")
    assert "'use strict'" in js and "W.SBSns = {view, again}" in js
    assert "innerHTML" not in js and "eval(" not in js and "http://" not in js and "https://" not in js
    for word in ("자동 반응 · 승인함", "직접 올리는 글", ".snb>.top:first-child", ".snx>.top:first-child", ".snb label.tg", ".snb .tc>.pb"):
        assert word in js, word
    html = shell.page({"me": {}})
    assert f'<meta name="sb-sns" content="{shell.asset_url("sns.js")}">' in html
    assert html.count("<script src=") == 1  # sns.js 는 처음 열 때만
    assert "sns.js" in dash_api.APP_FILES
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "sns.js")], capture_output=True).returncode == 0


# 작은 가짜 DOM: 상자를 언제 다시 붙이는지만 본다
SHIM = r"""
class E {
  constructor(t, a) { this.tag = t; this.a = a || {}; this.kids = []; }
  replaceChildren(...k) { this.kids = k; }
  append(...k) { this.kids.push(...k); }
}
const ids = {};
global.document = {getElementById: id => ids[id] || null, createElement: t => new E(t), head: {append: s => { ids[s.id] = s; }}};
global.window = {};
require(process.argv[1]);
const N = window.SBSns, log = [], FAIL = new Set();
// 불러오기 실패 흉내: app.js mount 처럼 false 를 돌려줌 (그 화면 파일을 못 받았을 때)
const mount = (L, b) => { log.push(L + ':' + b.a.class); if (FAIL.has(L)) { FAIL.delete(L); return Promise.resolve(false); } return Promise.resolve(); };
const h = (nav, S, j) => ({el: (t, a, ...k) => { const e = new E(t, a); e.kids = k; return e; }, mount, NAV: nav, S, j,
  L: {threads: 'th', x: ['xb', 'xp'], lookbook: 'lb'}});
const tick = () => new Promise(r => setTimeout(r, 0));
const names = b => b.kids.map(k => k.tag === 'p' ? k.kids[0] + (/sng/.test(k.a.class) ? '^' : '') : k.a.class);
(async () => {
  const out = {};
  const b = new E('div');
  N.view(b, 'x', h(1, {}));                       // X 칸 처음: x.js + xpost.js
  out.first = log.splice(0);
  out.kids = names(b);
  N.view(b, 'x', h(1, {}));                       // 20초 새로고침: x.js 는 그대로 · xpost.js 만
  out.poll = log.splice(0);
  N.view(b, 'x', h(2, {}));                       // 칸을 다시 열면 (NAV) 둘 다
  out.nav = log.splice(0);
  N.again();                                      // 새로고침 (위 단추 · 끌어서 · 돌아올 때 — app.js refresh)
  out.rf = log.splice(0);
  N.view(b, 'x', h(2, {}));                       // 그 뒤 새 상태로 그릴 때는 다시 안 붙임
  out.rfAfter = log.splice(0);
  N.view(b, 'threads', h(3, {thpost: {a: 1}}));
  out.th = log.splice(0);
  N.view(b, 'threads', h(3, {thpost: {a: 1}}));   // 글 칸 값이 같으면 그대로
  out.thSame = log.splice(0);
  N.view(b, 'threads', h(3, {thpost: {a: 2}}));   // 올린 글·답글로 값이 바뀌면 다시
  out.thNew = log.splice(0);
  N.again();                                      // 스레드 칸의 새로고침
  out.thRf = log.splice(0);
  N.view(b, 'lookbook', h(4, {}));
  N.view(b, 'lookbook', h(4, {}));
  out.lb = log.splice(0);
  FAIL.add('xb');                                 // 약한 인터넷: x.js 를 한 번 못 받음
  N.view(b, 'x', h(5, {})); await tick();
  N.view(b, 'x', h(5, {})); await tick();         // 다음 그릴 때 다시 받음
  N.view(b, 'x', h(5, {})); await tick();         // 받은 뒤엔 그대로
  out.fail = log.splice(0);
  N.view(b, 'x', h(6, {}, true));                 // 할 일 'X 숫자 넣기'(#/blog/x)로 옴
  out.jumpKids = names(b);
  log.splice(0);
  out.css = !!ids['sn-css'] && /\.snx \.tq\{/.test(ids['sn-css'].textContent) && /\.snx \.tb\{/.test(ids['sn-css'].textContent);
  console.log(JSON.stringify(out));
})();
"""


def test_sns_boxes_remount_only_when_needed() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    res = subprocess.run([node, "-e", SHIM, str(STATIC / "sns.js")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["first"] == ["xb:snx", "xp:snp"]
    assert got["kids"] == ["자동 반응 · 승인함^", "snx", "직접 올리는 글", "snp"]  # 자동 반응 먼저, 직접 올리는 글 아래
    assert got["poll"] == ["xp:snp"]  # 고치던 X 댓글이 남게
    assert got["nav"] == ["xb:snx", "xp:snp"]
    assert got["rf"] == ["xb:snx", "xp:snp"] and got["rfAfter"] == ["xp:snp"]
    assert got["th"] == ["th:snb"] and got["thSame"] == [] and got["thNew"] == ["th:snb"] and got["thRf"] == ["th:snb"]
    assert got["lb"] == ["lb:snl", "lb:snl"]
    # 못 받은 x.js 는 다음 그릴 때 다시 (예전엔 칸이 빈 채로 남음) · 받은 뒤엔 20초 새로고침에 그대로
    assert got["fail"] == ["xb:snx", "xp:snp", "xb:snx", "xp:snp", "xp:snp"]
    assert got["jumpKids"] == ["직접 올리는 글^", "snp", "자동 반응 · 승인함", "snx"]  # 숫자 넣는 칸이 위
    assert got["css"] is True  # X 를 먼저 열어도 숫자 칸·알약 단추 모양 (threads.js 없이)


def test_refresh_paths_all_reload_the_sns_box() -> None:
    """위 '새로고침' 단추 · 끌어서 새로고침 · 앱으로 돌아올 때 모두 같은 길 (SBSns.again) — sns.js 가 단추에 따로 붙지 않는다."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sn = (STATIC / "sns.js").read_text(encoding="utf-8")
    assert "getElementById('rf')" not in sn and "addEventListener" not in sn
    assert "const snAgain = () => R.tab === 'sns' && W.SBSns && W.SBSns.again();" in js
    assert "function refresh() { if (R.tab === 'stats') loadStats(); snAgain(); return load(); }" in js
    assert "$('rf').addEventListener('click', refresh)" in js and "refresh().then(" in _fn(js, "function ptr()")
    assert "if (Date.now() - PICK > 120000) { snAgain(); load(); }" in js
    # 화면 파일을 못 받으면 mount 가 false → sns.js 가 다음에 다시
    assert "() => (toast(MSG.net), false));" in js
    # 할 일 'X 숫자 넣기'(#/blog/x) 표시 · 칸 고르기 숫자는 모서리 동그라미 (#snt)
    route = _fn(js, "function route()")
    assert "const j = location.hash === '#/blog/x';" in route and "sub: p[1] || '', j}" in route
    sns = _fn(js, "function vSns()")
    assert "j: R.j" in sns and "{id: 'snt'}" in sns
    assert "#snt button{position:relative;white-space:nowrap" in sn and "#snt .cp{position:absolute" in sn


def _fn(js: str, head: str) -> str:
    """app.js 에서 'function 이름(' 부터 맨 앞 칸의 '}' 까지."""
    start = js.index(head)
    return js[start:js.index("\n}\n", start)]


def test_sns_tab_routes_and_old_addresses_move() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "sns: 'SNS'" in js and "SUBS = {sns: ['threads', 'x', 'lookbook']}" in js and "sns: vSns" in js
    # 예전 주소(Blog-Auto 알림 '/dashboard/#/blog/threads' · 할 일 goto · 홈 화면)는 새 자리로 — 주소를 바꿔 쓰고(뒤로 가기에 안 남게) 다시 그림
    mv = re.search(r"const MV = \{([^}]*)\};", js)
    assert mv
    pairs = dict(re.findall(r"'(#/[a-z/]+)': '(#/[a-z/]+)'", mv.group(1)))
    assert pairs == {"#/blog/threads": "#/sns/threads", "#/blog/x": "#/sns/x", "#/blog/lookbook": "#/sns/lookbook"}
    route = _fn(js, "function route()")
    assert route.index("if (MV[location.hash]) history.replaceState(null, '', MV[location.hash]);") < route.index("NAV++")
    assert "LS.s(R.tab, R.sub)" in route  # 마지막에 본 칸을 기억
    # 블로그 탭에는 블로그 셋만 — SNS 화면으로 가는 입구·불러오기가 없음
    blog = _fn(js, "function vBlog()")
    for gone in ("#/blog/threads", "#/blog/x", "#/blog/lookbook", "thLoad", "xLoad", "lbLoad", "스레드·인스타", "X(트위터)", "룩북"):
        assert gone not in blog, gone
    assert "sched|threads|x|lookbook)$/" not in js and "/^(pics|search|trial|ptrial|sched)$/" in route
    # 도구 한 칸 (예약 일정 · 사진 뽑기 · 사진/제품 테스트 · 검색 등록) 은 예약된 글 아래 · 기다리는 글과 같은 할 일은 한 번만
    assert blog.index("'기다리는 글'") < blog.index("'도구'") and blog.index("예약된 글") < blog.index("'도구'")
    assert "!b.waiting.some(w => w.iid === i.iid)" in blog
    # SNS 칸 옆 숫자 = 그 칸으로 가는 할 일 (예전 '#/blog/…' goto 도 같은 칸으로 셈)
    sns = _fn(js, "function vSns()")
    assert ".replace('#/blog/', '#/sns/')" in sns and "S.todo.filter(i => go(i) === '#/sns/' + k)" in sns
    assert "['threads', '스레드·인스타'], ['x', 'X(트위터)'], ['lookbook', '룩북 릴스']" in sns
    # 스레드·X 화면 파일은 그대로 (불러오기 이름·예전 주소 글자도 — X 봇·스레드 세션 시험이 본다)
    assert "lazy('meta[name=sb-threads]', 'threads', 'SBThreads')" in js and "lazy('meta[name=sb-x]', 'x', 'SBX')" in js
    assert "T.view(b, {el, btn, api, toast, S, exec, uploadAll})" in js


def test_x_and_lookbook_screens_fit_the_sns_tab() -> None:
    """SNS 탭 안에서는 머리 줄 '… · 블로그로' 가 없고, X 는 데런픽 초안함이 진행률·계획보다 위 (주인이 매일 쓰는 칸이 먼저)."""
    xp = (STATIC / "xpost.js").read_text(encoding="utf-8")
    lb = (STATIC / "lookbook.js").read_text(encoding="utf-8")
    for text in (xp, lb):
        assert "'블로그로'" not in text and "#/blog/issue" not in text
    draw = xp[xp.index("B.replaceChildren(el('div', {class: 'xpw'}"):]
    assert draw.index("card(`데런픽 초안함") < draw.index("progress(x), plan(x)") < draw.index("card('수익 계정 글감 카드'")
    # 할 일 'X 숫자 넣기'가 있는 동안은 숫자 넣는 진행률 칸이 맨 위 (넣으면 할 일이 빠지고 제자리)
    assert "const st = (H.S.todo || []).some(i => i.iid === 'x-stats');" in xp
    assert draw.index("st ? progress(x) : null") < draw.index("card(`데런픽 초안함") and "st ? null : progress(x), plan(x)" in draw
