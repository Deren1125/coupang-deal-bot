"""할 일 '붙여 넣기' (주인 10/27 — 서버가 네이버에 로그인하지 않고, 주인이 아이패드 사파리에서 대시보드 글을 네이버 글쓰기에 붙여 넣음).

1) status.routine 발행·예약 줄의 paste 를 엄격히 거름 (blogauto._paste): media:<파일> → <공개 주소>/media/<파일> · https 가 아닌 사진은 뺌 ·
   믿지 않는 HTML (on*·스크립트·iframe·javascript: 링크 뺌) · 제목 한 줄 120자 · 태그 30개 40자 · 공개 주소가 없으면 None + 안내.
2) BlogAutoReader(media_base) → routine.view 까지 그대로.
3) routine.js: 제목·본문·태그 복사 큰 단추 · 본문은 text/html + text/plain 한 번에 · 안 되면 화면 밖 칸(DOM) 복사 · 링크 카드 안내 한 줄.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dash_helpers import fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import routine
from dealbot.dashboard.blogauto import PASTE_NO_BASE, BlogAutoReader, _paste, _routine, parse_export
from test_dash_routine import SLOTS, at, item, tz_now, with_routine

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
BASE = "https://bot.example.com"
HTML = ('<p style="text-align:center" onclick="steal()"><b>오늘의 적금</b> 정리</p>'
        '<img src="media:fin_31-1.jpg" onerror="alert(1)" alt="표">'
        '<img src="media:../../etc/passwd"><img src="http://cdn.example/x.jpg"><img src="https://cdn.example/y.png"><img src="data:image/png;base64,AAAA">'
        '<blockquote class="se-quote">인용 &amp; <i>한 줄</i></blockquote>'
        '<p>https://link.example/a</p>'
        '<a href="javascript:alert(1)">나쁜 링크</a> <a href="https://shop.example/p?a=1&b=2">좋은 링크</a>'
        '<script>alert(1)</script><iframe src="https://evil.example"></iframe><style>p{color:red}</style>'
        '<span style="background:url(https://evil.example/t)">배경</span>')


def paste(**kw: object) -> dict:
    return {"title": "적금 비교 정리", "html": HTML, "tags": ["적금", "#재테크", "적금 통장"], "cards": 1, "photos": 2, **kw}


# ---------------------------------------------------------------- 1) 거름
def test_paste_rewrites_media_and_drops_unsafe_parts() -> None:
    p = _paste(paste(), BASE)
    assert p is not None and p["title"] == "적금 비교 정리" and p["cards"] == 1 and p["photos"] == 2
    h = p["html"]
    assert f'<img src="{BASE}/media/fin_31-1.jpg" alt="표">' in h  # media: → 공개 주소 (onerror 는 뺌)
    assert '<img src="https://cdn.example/y.png">' in h and h.count("<img") == 2  # ../ · http · data: 사진은 통째로 뺌
    assert '<p style="text-align:center"><b>오늘의 적금</b> 정리</p>' in h  # 서식은 그대로
    assert '<blockquote class="se-quote">인용 &amp; <i>한 줄</i></blockquote>' in h
    assert "<a>나쁜 링크</a>" in h and '<a href="https://shop.example/p?a=1&amp;b=2">좋은 링크</a>' in h
    for bad in ("onclick", "onerror", "<script", "alert(", "<iframe", "evil.example", "<style", "javascript:", "passwd", "http://"):
        assert bad not in h, bad
    assert "<span>배경</span>" in h  # url( 이 든 style 은 그 속성만 뺌
    assert p["text"].splitlines()[0] == "오늘의 적금 정리" and "https://link.example/a" in p["text"].splitlines()
    assert "인용 & 한 줄" in p["text"] and "alert" not in p["text"] and "<" not in p["text"]
    assert p["tags"] == ["적금", "재테크", "적금통장"] and p["tag_text"] == "#적금 #재테크 #적금통장"


def test_paste_limits_and_bad_shapes() -> None:
    long_title = "가" * 300
    p = _paste(paste(title=long_title + "\n둘째 줄", tags=[f"t{i}" for i in range(50)] + ["x" * 41], cards=-1, photos="3"), BASE + "/")
    assert p is not None and len(p["title"]) == 120 and "\n" not in p["title"]
    assert len(p["tags"]) == 30 and p["tags"][0] == "t0" and p["cards"] == 0 and p["photos"] == 0
    assert _paste(paste(tags=["a" * 40, "b" * 41, 7, None, "  ", "#", "중복", "중복"]), BASE)["tags"] == ["a" * 40, "중복"]
    assert _paste(paste(tags="적금,재테크"), BASE)["tags"] == []
    assert _paste(paste(html="<p>" + "가" * 199_993 + "</p>"), BASE) is not None  # 200000자까지
    assert _paste(paste(html="<p>" + "가" * 199_994 + "</p>"), BASE) is None  # 넘으면 자르지 않고 버림 (HTML 이 깨지지 않게)
    for bad in (None, "글", [], paste(html=None), paste(html=3), paste(title="  "), paste(title=None),
                paste(html="<script>x</script>"), paste(html="")):
        assert _paste(bad, BASE) is None, bad
    assert _paste(paste(), "http://bot.example.com") is None  # 공개 주소가 https 가 아니면 사진 주소를 만들지 않음
    assert _paste(paste(html='<img src="media:a.jpg">'), BASE)["html"] == f'<img src="{BASE}/media/a.jpg">'  # 사진만 있는 글도
    assert _paste(paste(html="<div><p>열린<b>채"), BASE)["html"] == "<div><p>열린<b>채</b></p></div>"  # 닫히지 않은 태그는 닫음


def test_routine_item_keeps_paste_only_for_publish_rows_and_notes_missing_base() -> None:
    rt = {"day": "2026-10-27", "slots": SLOTS, "items": [
        item("pub:finance:31", "s1", group="발행", note="아카이브 31번", paste=paste()),
        item("sch:issue:7", "s2", group="예약", paste=paste(title="예약 글")),
        item("nb:issue:a", "s1", paste=paste()),  # 이웃 줄엔 붙여 넣기 없음
        item("pub:hotdeal:2", "s3", group="발행", paste={"title": "t"}),  # 틀린 모양 → None (안내 없음)
        item("pub:hotdeal:3", "s3", group="발행")]}
    got = {i["id"]: i for i in _routine(rt, BASE)["items"]}
    assert got["pub:finance:31"]["paste"]["html"].count(f"{BASE}/media/") == 1 and got["sch:issue:7"]["paste"]["title"] == "예약 글"
    assert got["nb:issue:a"]["paste"] is None and got["pub:hotdeal:2"]["paste"] is None and got["pub:hotdeal:3"]["paste"] is None
    assert got["pub:hotdeal:2"]["note"] == "" and got["pub:finance:31"]["note"] == "아카이브 31번"
    # 공개 주소가 없으면: 붙여 넣기는 None, 안내 한 줄
    got = {i["id"]: i for i in _routine(rt)["items"]}
    assert got["pub:finance:31"]["paste"] is None and got["pub:finance:31"]["note"] == f"아카이브 31번 · {PASTE_NO_BASE}"
    assert got["sch:issue:7"]["note"] == PASTE_NO_BASE and got["pub:hotdeal:3"]["note"] == ""
    data = fixture("export_v2.json")
    data["status"]["routine"] = rt
    assert parse_export(data, "Asia/Seoul", BASE).v2["status"]["routine"]["items"][0]["paste"]["tags"] == ["적금", "재테크", "적금통장"]
    assert parse_export(data, "Asia/Seoul").v2["status"]["routine"]["items"][0]["paste"] is None


# ---------------------------------------------------------------- 2) 읽기 → 할 일 칸
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.public_domain = "bot.example.com"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def sample(day: str) -> dict:
    return {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("pub:finance:31", "s1", group="발행", title="아카이브 31번 발행", min=8, paste=paste(cards=2)),
        item("pub:issue:9", "s1", group="발행", blog="issue", title="Pick 9번 발행", paste=paste(tags=[], cards=0)),
        item("nb:issue:a", "s1", blog="issue", copy=[{"label": "댓글 복사", "text": "잘 봤어요"}])]}


def test_reader_gets_the_public_base_from_settings(bot: DealBot, tmp_path: Path) -> None:
    assert bot.settings.secrets.public_base_url == BASE
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    v = routine.view(bot, now=at(bot, day, "10:00"))
    its = v["slots"][0]["items"]
    assert its[0]["paste"]["html"].count(f'src="{BASE}/media/fin_31-1.jpg"') == 1 and its[0]["paste"]["cards"] == 2
    assert its[1]["paste"]["tag_text"] == "" and its[2]["paste"] is None
    r = BlogAutoReader(tmp_path / "blogauto.json", tz="Asia/Seoul", stale_hours=10**6, now=lambda: tz_now(bot))
    assert r.read().v2["status"]["routine"]["items"][0]["paste"] is None  # 공개 주소를 받지 않은 읽기 → None


# ---------------------------------------------------------------- 3) routine.js 복사 단추
SHIM = r"""
const out = {clip: [], rich: [], exec: [], toasts: []};
let observer = null, mode = process.argv[3];
class T { constructor(s) { this.data = s; this.parentNode = null; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.ls = {}; this.parentNode = null; this.className = ''; this._t = ''; this.hidden = false; this.style = {}; this.attributes = []; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  get childNodes() { return this.children; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; }
  removeAttribute(k) { delete this.attrs[k]; this.attributes = this.attributes.filter(a => a.name !== k); }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  _add(c, front) { if (!c.nodeType) c = new T(String(c)); if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; front ? this.children.unshift(c) : this.children.push(c); }
  append(...cs) { cs.forEach(c => this._add(c, false)); this._n(); }
  prepend(...cs) { cs.reverse().forEach(c => this._add(c, true)); this._n(); }
  replaceChildren(...cs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; cs.forEach(c => this._add(c, false)); this._n(); }
  remove() { if (this.parentNode) { this.parentNode.children = this.parentNode.children.filter(x => x !== this); this.parentNode = null; } }
  _n() { if (observer && observer.t === this) queueMicrotask(observer.cb); }
  all(f, acc = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) acc.push(c); c.all(f, acc); } return acc; }
  click() { (this.ls.click || []).forEach(f => f({currentTarget: this})); }
}
const ids = {}, mk = (t, id) => { const e = new E(t); ids[id] = e; return e; };
const main = mk('main', 'main'), boot = mk('script', 'boot'), toast = mk('div', 'toast'), body = new E('body');
boot.textContent = JSON.stringify({me: {csrf: 'tok123'}});
main.append(new E('p'));
let sel = null;
global.location = {hash: '#/todo'};
const clipboard = {writeText: async t => { out.clip.push(t); }};
if (mode === 'rich') clipboard.write = async items => { for (const it of items) for (const [k, b] of Object.entries(it.parts)) out.rich.push([k, b.type, await b.text()]); };
global.window = {addEventListener() {}, navigator: {clipboard}, getSelection: () => ({removeAllRanges() { sel = null; }, addRange(r) { sel = r; }})};
if (mode === 'rich') window.ClipboardItem = class { constructor(parts) { this.parts = parts; } };
// 가짜 DOMParser: 스크립트 하나 + onclick 이 붙은 문단 하나로 읽힌 것처럼
global.DOMParser = class { parseFromString(h, type) {
  out.parsed = [h.length, type];
  const p = new E('p'), s = new E('script');
  p.attributes = [{name: 'onclick'}, {name: 'style'}]; p.attrs = {onclick: 'x()', style: 'color:red'}; p.append('붙여 넣을 글');
  const b = new E('body'); b.append(s, p);
  return {body: b, querySelectorAll: q => q.startsWith('script') ? [s] : q === '*' ? [p] : []};
} };
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.t = t; observer = this; } };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), body, getElementById: i => ids[i] || null,
  createElement: t => new E(t), addEventListener() {}, importNode: n => n,
  createRange: () => ({selectNodeContents(n) { this.n = n; }}),
  execCommand: c => { const n = sel && sel.n; out.exec.push([c, n && n.attrs.contenteditable, n && n.attrs.style, n && n.parentNode === body,
    n && n.textContent, n && Object.keys(n.children[0].attrs), n && n.children.some(c => c.tagName === 'SCRIPT')]); return true; }};
const VIEW = JSON.parse(process.argv[2]);
global.fetch = async url => ({status: 200, json: async () => url === '/dashboard/api/routine' ? VIEW : {status: 'ok', code: 'done'}});
const tick = () => new Promise(r => setTimeout(r, 5));
(async () => {
  require(process.argv[1]);
  await tick(); await tick();
  const box = main.children.find(c => c.attrs.id === 'rtp');
  const its = box.all(c => c.className.startsWith('it'));
  out.big = its.map(i => i.all(c => c.tagName === 'BUTTON' && c.className === 'sm' && c.parentNode.className === 'bt pb').map(b => b.textContent));
  out.hint = its.map(i => i.all(c => c.className === 's').map(c => c.textContent).filter(t => t.startsWith('링크 카드')));
  const press = async label => { its[0].all(c => c.tagName === 'BUTTON' && c.textContent === label)[0].click(); await tick(); await tick(); out.toasts.push(toast.textContent); };
  await press('제목 복사'); await press('본문 복사'); await press('태그 복사');
  out.bodyLeft = body.children.length;  // 화면 밖 칸은 복사 뒤 지움
  out.inCard = box.all(c => c.attrs.contenteditable).length;  // 살아 있는 칸엔 아무것도 넣지 않음
  its[1].all(c => c.className === 'cb')[0].click();
  await tick(); await tick();
  out.doneBig = main.children.find(c => c.attrs.id === 'rtp').all(c => c.className === 'bt pb').length;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def _run(node: str, view: dict, mode: str) -> dict:
    out = subprocess.run([node, "-e", SHIM, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False), mode],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_routine_js_copy_buttons(bot: DealBot, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    p = view["slots"][0]["items"][0]["paste"]
    got = _run(node, view, "rich")
    assert got["big"] == [["제목 복사", "본문 복사", "태그 복사"], ["제목 복사", "본문 복사"], []]  # 태그가 없으면 태그 단추 없음
    assert got["hint"] == [["링크 카드 2개 — 붙여 넣은 뒤 책갈피 '꾸미기'를 눌러요"], [], []]
    assert got["clip"] == ["적금 비교 정리", "#적금 #재테크 #적금통장"] and got["toasts"] == ["복사했어요"] * 3
    assert got["rich"] == [["text/html", "text/html", p["html"]], ["text/plain", "text/plain", p["text"]]]  # 한 번에 둘 다
    assert got["exec"] == [] and got["bodyLeft"] == 0 and got["inCard"] == 0
    assert got["doneBig"] == 1  # 체크한 줄은 단추를 숨김
    # ClipboardItem 이 없는 사파리: 화면 밖 칸에 DOM 으로 옮겨 담아 선택·복사 (스크립트·on* 은 뺌), 끝나면 칸을 지움
    got = _run(node, view, "off")
    assert got["rich"] == [] and got["parsed"] == [len(p["html"]), "text/html"] and got["toasts"] == ["복사했어요"] * 3
    (cmd, editable, style, attached, text, attrs, script), = got["exec"]
    assert cmd == "copy" and editable == "true" and "left:-9999px" in style and attached is True and text == "붙여 넣을 글"
    assert attrs == ["style"] and script is False and got["bodyLeft"] == 0 and got["inCard"] == 0


def test_routine_js_stays_local_and_never_injects_html() -> None:
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "outerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js
    assert "ClipboardItem" in js and "'text/html'" in js and "'text/plain'" in js and "execCommand('copy')" in js and "DOMParser" in js
    for word in ("제목 복사", "본문 복사", "태그 복사", "링크 카드", "꾸미기"):
        assert word in js, word
