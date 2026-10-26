"""SNS 탭 (주인 10/26 — '스레드·인스타·트위터·룩북 릴스는 별개의 SNS 라 블로그 탭에 있으면 안 돼').

- sns.js: 처음 열 때만 받는 상자 파일 (meta sb-sns) · 바깥 주소·innerHTML 없음 · 문법 (node --check)
- 상자는 칸마다 하나를 두고 다시 쓴다: 서버에서 스스로 읽는 화면(threads·x)은 탭을 열 때 · 새로고침 · (스레드는) 글 칸 값이 바뀔 때만
  다시 붙이고 (20초 새로고침에 X 승인함에서 고치던 댓글이 사라지지 않게), S 로 그리는 화면(xpost·lookbook)은 매번 다시 그린다
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from dealbot.dashboard import api as dash_api
from dealbot.dashboard import shell, webapp

STATIC = webapp.STATIC_DIR


def test_sns_js_is_lazy_local_and_safe() -> None:
    js = (STATIC / "sns.js").read_text(encoding="utf-8")
    assert "'use strict'" in js and "W.SBSns = {view}" in js
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
  constructor(t, a) { this.tag = t; this.a = a || {}; this.kids = []; this.ls = {}; }
  replaceChildren(...k) { this.kids = k; }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  append(...k) { this.kids.push(...k); }
}
const ids = {rf: new E('button')};
global.location = {hash: '#/sns/x'};
global.document = {getElementById: id => ids[id] || null, createElement: t => new E(t), head: {append: s => { ids[s.id] = s; }}};
global.window = {};
require(process.argv[1]);
const N = window.SBSns, log = [];
const mount = (L, b) => log.push(L + ':' + b.a.class);
const h = (nav, S) => ({el: (t, a, ...k) => { const e = new E(t, a); e.kids = k; return e; }, mount, NAV: nav, S,
  L: {threads: 'th', x: ['xb', 'xp'], lookbook: 'lb'}});
const out = {};
const b = new E('div');
N.view(b, 'x', h(1, {}));                       // X 칸 처음: x.js + xpost.js
out.first = log.splice(0);
out.kids = b.kids.map(k => k.tag === 'p' ? k.kids[0] : k.a.class);
N.view(b, 'x', h(1, {}));                       // 20초 새로고침: x.js 는 그대로 · xpost.js 만
out.poll = log.splice(0);
N.view(b, 'x', h(2, {}));                       // 칸을 다시 열면 (NAV) 둘 다
out.nav = log.splice(0);
ids.rf.ls.click.forEach(f => f());              // 위 '새로고침' 단추
out.rf = log.splice(0);
location.hash = '#/sns/threads';
N.view(b, 'threads', h(3, {thpost: {a: 1}}));
out.th = log.splice(0);
N.view(b, 'threads', h(3, {thpost: {a: 1}}));   // 글 칸 값이 같으면 그대로
out.thSame = log.splice(0);
N.view(b, 'threads', h(3, {thpost: {a: 2}}));   // 올린 글·답글로 값이 바뀌면 다시
out.thNew = log.splice(0);
N.view(b, 'lookbook', h(4, {}));
N.view(b, 'lookbook', h(4, {}));
out.lb = log.splice(0);
location.hash = '#/blog/issue';
ids.rf.ls.click.forEach(f => f());              // 다른 탭의 새로고침은 상관없음
out.away = log.splice(0);
out.css = !!ids['sn-css'];
console.log(JSON.stringify(out));
"""


def test_sns_boxes_remount_only_when_needed() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    res = subprocess.run([node, "-e", SHIM, str(STATIC / "sns.js")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["first"] == ["xb:snx", "xp:snp"]
    assert got["kids"] == ["자동 반응 · 승인함", "snx", "직접 올리는 글", "snp"]  # 자동 반응 먼저, 직접 올리는 글 아래
    assert got["poll"] == ["xp:snp"]  # 고치던 X 댓글이 남게
    assert got["nav"] == ["xb:snx", "xp:snp"]
    assert got["rf"] == ["xb:snx", "xp:snp"]
    assert got["th"] == ["th:snb"] and got["thSame"] == [] and got["thNew"] == ["th:snb"]
    assert got["lb"] == ["lb:snl", "lb:snl"]
    assert got["away"] == [] and got["css"] is True
