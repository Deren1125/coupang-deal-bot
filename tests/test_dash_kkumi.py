"""'꾸미기' 책갈피 (주인 10/27 — 붙여 넣은 글의 링크 카드를 주인 아이패드 사파리에서 만듦, 서버는 네이버에 로그인하지 않음).

1) kkumi.bookmarklet(): static/kkumi.js 를 줄여 "javascript:" + encodeURIComponent — node 의 encodeURIComponent 와 같고 new Function 으로 읽힘 ·
   바깥 요청 없음.
2) 가짜 글쓰기 화면(iframe 안 · 진짜 크롬, playwright 가 있을 때만): 주소만 있는 줄 둘 → 카드 둘 (주소 줄 바로 아래) · 이미 카드가 있는 줄·
   글 속 주소는 그대로 · 긴 구분선 → 짧은 선·가운데, 기본 인용구 → 포스트잇 (이미 맞는 것은 누르지 않음) · 다시 누르면 '고칠 것이 없어요' ·
   단추가 없으면 종류마다 이유와 함께 0/m.
3) GET /dashboard/api/kkumi (로그인 아래) · static/kkumi.js (공개 파일) · 연결 탭 '꾸미기 버튼 설치' 칸 (logins.js — 펼치면 4단계 + 코드 복사).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import kkumi, logins
from test_dash_logins import DOM_SHIM as LG_SHIM
from test_dash_state import write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")


def code() -> str:
    c = kkumi.bookmarklet()
    assert c.startswith("javascript:")
    return unquote(c[len("javascript:"):])


# ---------------------------------------------------------------- 1) 만들기
def test_bookmarklet_is_minified_encoded_and_has_no_network() -> None:
    src = kkumi.SRC.read_text(encoding="utf-8")
    js = code()
    assert js == kkumi.minify(src) and len(js) < len(src) * 0.85 and "/*" not in js and "\n//" not in js
    assert len(kkumi.bookmarklet()) < 12000  # 사파리 책갈피 주소 칸에 붙여 넣는 길이 (시험한 책갈피는 5KB · 구분선·인용구까지 11KB 쯤)
    for net in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "import(", ".src=", "location", "open(", "http://"):
        assert net not in js, net
    for word in ("링크 카드 ", "개 만들었어요", "se-text-paragraph", "oglink", "se-popup-button-confirm", "15000", "8000", "pointerdown",
                 "se-horizontalLine", "se-l-default", "align-drop-down-with-justify", "quotation_postit", "짧게·가운데", "포스트잇"):
        assert word in js, word
    assert kkumi.view() == {"code": kkumi.bookmarklet(), "len": len(kkumi.bookmarklet()), "steps": list(kkumi.STEPS)} and len(kkumi.STEPS) == 4


def test_minify_keeps_strings_and_word_gaps() -> None:
    assert kkumi.minify("/* 머리 */\n  // 주석\nconst a = 'x  y' + \"p q\";\n\n  if (a) return b;\nawait  S(1);") == \
        "const a='x  y'+\"p q\";\nif(a)return b;\nawait S(1);"
    assert kkumi.minify("const s = '\\' a';") == "const s='\\' a';"  # 따옴표 안 역슬래시


@needs_node
def test_bookmarklet_parses_and_matches_encode_uri_component() -> None:
    js = code()
    chk = "const c=process.argv[1];new Function(c);" \
          "process.stdout.write(String('javascript:'+encodeURIComponent(c)===process.argv[2]))"
    out = subprocess.run([NODE, "-e", chk, js, kkumi.bookmarklet()], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "true"
    assert subprocess.run([NODE, "--check", str(STATIC / "kkumi.js")], capture_output=True).returncode == 0


# ---------------------------------------------------------------- 2) 가짜 글쓰기 화면
# 네이버 SmartEditor 처럼: 툴바 링크(oglink) 단추 → 팝업(주소 칸·검색·미리보기·확인). 확인 → 커서가 있던 문단 바로 아래에 카드.
# 검색 뒤 0.4초 동안 '불러오는 중' 표시, 그 뒤 미리보기 — 꾸미기는 이것을 기다려야 한다.
INNER = r"""<html><head><meta charset="utf-8"></head><body>
<div class="se-toolbar"><div class="se-popup-oglink" style="display:none"><input placeholder="URL을 입력하세요">
<button class="se-popup-oglink-button">검색</button><div class="se-popup-loading" style="display:none">…</div><div class="pv"></div>
<button class="se-popup-button-confirm">확인</button></div></div>
<div class="se-content" contenteditable="true">
<div class="se-component se-text"><p class="se-text-paragraph">소개 글이에요.</p>
<p class="se-text-paragraph"><span>https://one.example/a?x=1</span></p>
<p class="se-text-paragraph">글 속 주소 https://mid.example 는 그대로</p>
<p class="se-text-paragraph">https://two.example/b</p>
<p class="se-text-paragraph"><span>&#8203;</span></p></div>
<div class="se-component se-oglink" data-url="https://two.example/b">이미 있는 카드</div>
<div class="se-component se-text"><p class="se-text-paragraph"><b>https://three.example/c</b></p></div>
<div class="se-component se-horizontalLine se-l-line1" data-k="A"><div class="se-section se-section-horizontalLine"><hr></div></div>
<div class="se-component se-horizontalLine se-l-default" data-k="B"><div class="se-section se-section-horizontalLine se-section-align-center"><hr></div></div>
<div class="se-component se-horizontalLine se-l-default" data-k="C"><div class="se-section se-section-horizontalLine"><hr></div></div>
<div class="se-component se-quotation se-l-default" data-k="Q1"><blockquote><p class="se-text-paragraph">인용 하나</p></blockquote></div>
<div class="se-component se-quotation se-l-quotation_postit" data-k="Q2"><blockquote><p class="se-text-paragraph">이미 포스트잇</p></blockquote></div>
</div>
<script>
const pop = document.querySelector('.se-popup-oglink'), inp = pop.querySelector('input'), pv = pop.querySelector('.pv'),
  ld = pop.querySelector('.se-popup-loading');
let at = null, url = '';
window.touched = [];
if (TOOL) {
  // 구분선·인용구 (서버 봇 editor.py 와 Blog-Auto test_editor_mock 모양): 선을 누르면 모양 단추가 뜸 · 정렬 메뉴 · 인용구 모양 메뉴
  document.querySelector('.se-toolbar').insertAdjacentHTML('beforeend', `<button data-name="horizontal-line">ㅡ</button>
<button data-name="horizontal-line-select">v</button><div id="lstyles" style="display:none"><button data-name="horizontal-line-style" data-value="default">짧은 선</button>
<button data-value="line1">긴 선</button></div><button data-name="align-drop-down-with-justify">정렬</button><span id="aligns" style="display:none">
<button data-value="left">왼쪽</button><button data-value="center">가운데</button></span><button data-name="quotation-select">인용 v</button>
<div id="qstyles" style="display:none"><button data-value="quotation_postit">포스트잇</button></div>`);
  const $ = q => document.querySelector(q), lst = $('#lstyles');
  let sel = null;
  document.addEventListener('click', e => {
    if (e.target.closest('.se-toolbar')) return;
    sel = e.target.closest('.se-horizontalLine,.se-quotation');
    if (sel) window.touched.push(sel.dataset.k);
    lst.style.display = sel && sel.matches('.se-horizontalLine') ? 'block' : 'none';
  });
  $('#lstyles [data-value=default]').onclick = () => { if (sel && sel.matches('.se-horizontalLine')) sel.className = sel.className.replace(/se-l-\S+/, 'se-l-default'); lst.style.display = 'none'; };
  $('[data-name=align-drop-down-with-justify]').onclick = () => { $('#aligns').style.display = 'inline'; };
  $('#aligns [data-value=center]').onclick = () => { $('#aligns').style.display = 'none';
    const x = sel && sel.matches('.se-horizontalLine') && sel.querySelector('.se-section');
    if (x) x.className = x.className.replace(/ ?se-section-align-\S+/g, '') + ' se-section-align-center'; };
  $('[data-name=quotation-select]').onclick = () => { $('#qstyles').style.display = 'block'; };
  $('[data-value=quotation_postit]').onclick = () => { $('#qstyles').style.display = 'none';
    if (sel && sel.matches('.se-quotation')) sel.className = sel.className.replace(/se-l-\S+/, 'se-l-quotation_postit'); };
  const b = document.createElement('button'); b.setAttribute('data-name', 'oglink'); b.textContent = 'L';
  document.querySelector('.se-toolbar').prepend(b);
  b.onclick = () => { const s = getSelection(); at = s.rangeCount ? s.getRangeAt(0).startContainer : null;
    at = at && (at.nodeType === 1 ? at : at.parentElement).closest('.se-text-paragraph'); pv.replaceChildren(); inp.value = ''; pop.style.display = 'block'; };
}
pop.querySelector('.se-popup-oglink-button').onclick = () => { url = inp.value; ld.style.display = 'block';
  setTimeout(() => { ld.style.display = 'none'; const i = document.createElement('img'); i.className = 'se-oglink-thumbnail'; pv.append(i); }, 400); };
pop.querySelector('.se-popup-button-confirm').onclick = () => {
  if (!at || !url || !pv.firstChild) return;
  const c = document.createElement('div'); c.className = 'se-component se-oglink'; c.dataset.url = url; c.textContent = '카드 ' + url;
  at.after(c); pop.style.display = 'none';
};
</script></body></html>"""

RUN = r"""
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  const alerts = [];
  p.on('dialog', async d => { alerts.push(d.message()); await d.accept(); });
  const inner = process.argv[1], code = process.argv[2], out = {};
  if (process.argv[3] === 'fix') await p.addInitScript(() => { window.__kkumiFix = 1; });
  const frame = async () => (await p.waitForSelector('iframe')).contentFrame();
  const shape = async () => (await frame()).evaluate(() => [...document.querySelectorAll('.se-text-paragraph,.se-oglink')]
    .map(e => e.matches('.se-oglink') ? 'CARD ' + e.dataset.url : e.textContent.replace(/​/g, '').trim()).filter(Boolean));
  const parts = async () => (await frame()).evaluate(() => ({touched: window.touched.slice(),
    comps: [...document.querySelectorAll('.se-horizontalLine,.se-quotation')].map(e => [e.dataset.k, (e.className.match(/se-l-\S+/) || [''])[0],
      /se-section-align-center/.test((e.querySelector('.se-section') || e).className)])}));
  const press = async () => { await p.evaluate(code); for (let i = 0; i < 100 && alerts.length === out.n; i++) await p.waitForTimeout(100); };
  await p.setContent('<iframe id="mainFrame" style="width:900px;height:600px"></iframe>');
  await p.$eval('iframe', (f, h) => { f.srcdoc = h; }, inner.replaceAll('TOOL', 'true'));
  await (await frame()).waitForSelector('.se-content');
  out.n = 0; await press();
  out.first = alerts.slice(); out.shape = await shape(); out.parts = await parts();
  out.n = alerts.length; await press();
  out.again = alerts.slice(1); out.touched2 = await (await frame()).evaluate(() => window.touched);
  // 링크 단추가 없는 화면: 이유와 함께 0/1
  await p.$eval('iframe', (f, h) => { f.srcdoc = h; }, inner.replaceAll('TOOL', 'false').replace('https://two.example/b</p>', 'x</p>')
    .replace('https://three.example/c', 'z'));
  await p.waitForTimeout(300);
  await (await frame()).waitForSelector('.se-content');
  out.n = alerts.length; await press();
  out.nobutton = alerts.slice(2);
  // 글쓰기 화면이 아닌 곳
  await p.setContent('<p>다른 화면</p>');
  out.n = alerts.length; await press();
  out.elsewhere = alerts.slice(3);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _playwright_env() -> dict[str, str] | None:
    if NODE is None:
        return None
    env = dict(os.environ)
    roots = [str(Path(NODE).resolve().parents[1] / "lib" / "node_modules")]
    if npm := shutil.which("npm"):
        try:
            roots.append(subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    env["NODE_PATH"] = os.pathsep.join(r for r in [env.get("NODE_PATH", ""), *roots] if r)
    ok = subprocess.run([NODE, "-e", "const {chromium}=require('playwright');chromium.launch().then(b=>b.close()).then(()=>process.exit(0),()=>process.exit(3))"],
                        capture_output=True, env=env, timeout=60)
    return env if ok.returncode == 0 else None


def test_bookmarklet_makes_cards_on_a_mock_editor() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RUN, INNER, code(), "fix"], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == ["링크 카드 2/2개 만들었어요\n구분선 2/2개 짧게·가운데\n인용구 1/1개 포스트잇"]
    assert got["shape"] == ["소개 글이에요.", "https://one.example/a?x=1", "CARD https://one.example/a?x=1",
                            "글 속 주소 https://mid.example 는 그대로", "https://two.example/b", "CARD https://two.example/b",
                            "https://three.example/c", "CARD https://three.example/c",  # 주소 줄은 남기고 바로 아래 카드 · 있던 카드는 그대로
                            "인용 하나", "이미 포스트잇"]
    # 긴 선 A → 짧은 선 + 가운데 · 가운데가 아닌 짧은 선 C → 가운데 · 기본 인용구 Q1 → 포스트잇. 이미 맞는 B·Q2 는 누르지도 않음
    assert got["parts"]["comps"] == [["A", "se-l-default", True], ["B", "se-l-default", True], ["C", "se-l-default", True],
                                     ["Q1", "se-l-quotation_postit", False], ["Q2", "se-l-quotation_postit", False]]
    assert got["parts"]["touched"] == ["A", "A", "C", "Q1"]
    assert got["again"] == ["고칠 것이 없어요 (링크 카드가 모두 있어요)"] and got["touched2"] == got["parts"]["touched"]
    assert got["nobutton"] == ["링크 카드 0/1개 만들었어요\n구분선 0/2개 짧게·가운데\n인용구 0/1개 포스트잇\n못 한 것:\n"
                               "https://one.example/a?x=1 — 링크 단추를 못 찾음\n구분선 1번째 — 짧은 선 단추를 못 찾음\n"
                               "구분선 3번째 — 가운데 단추를 못 찾음\n인용구 1번째 — 포스트잇 단추를 못 찾음"]
    assert got["elsewhere"] == ["글쓰기 화면을 못 찾았어요 — 네이버 블로그 글쓰기 화면에서 눌러 주세요"]


def test_bookmarklet_leaves_dividers_and_quotes_alone_by_default() -> None:
    """10/28 주인 아이패드: 구분선·인용구 고치기가 기존 것을 못 고르고 새 짧은 선·빈 포스트잇을 더 넣음 → 기본은 링크 카드만."""
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RUN, INNER, code()], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == ["링크 카드 2/2개 만들었어요"] and got["parts"]["touched"] == []


# ---------------------------------------------------------------- 3) 대시보드
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_kkumi_api_needs_login_and_static_file_is_public(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/kkumi", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/kkumi", session_headers(bot))
    assert status == 200 and v == kkumi.view()
    res = await bot.web.dispatch("GET", "/dashboard/static/kkumi.js", {})
    assert res[0] == 200 and res[2].startswith("text/javascript") and res[3]["X-Content-Type-Options"] == "nosniff"


@needs_node
def test_connect_tab_install_section_copies_the_code(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    view = logins.view(bot)
    shim = LG_SHIM.replace("global.window = {addEventListener() {}};",
                           "const clip = []; global.window = {addEventListener() {}, navigator: {clipboard: {writeText: async t => { clip.push(t); }}}};")
    shim = shim.replace("return url === '/dashboard/api/logins'",
                        "if (url === '/dashboard/api/kkumi') return {status: 200, json: async () => KK}; return url === '/dashboard/api/logins'")
    shim = shim.replace("const VIEW = JSON.parse(process.argv[2]);", "const VIEW = JSON.parse(process.argv[2]), KK = JSON.parse(process.argv[3]);")
    flow = shim.split("  const box = () =>")[0] + r"""
  const box = () => main.children.find(c => c.attrs.id === 'lgp');
  const kf = () => box().all(c => c.className === 'fold kf')[0];
  out.fold = [kf().textContent, kf().attrs['aria-expanded']];
  out.before = calls.filter(c => c.url === '/dashboard/api/kkumi').length;
  kf().click();
  await tick(); await tick();
  out.steps = box().all(c => c.tagName === 'LI').map(c => c.textContent);
  box().all(c => c.tagName === 'BUTTON' && c.textContent === '코드 복사')[0].click();
  await tick();
  out.clip = clip; out.toast = toast.textContent;
  main.replaceChildren(new E('div'));  // app.js 가 연결 탭을 다시 그려도 펼친 채
  await tick();
  out.keep = kf().attrs['aria-expanded'];
  out.calls = calls.filter(c => c.url === '/dashboard/api/kkumi').length;
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""
    out = subprocess.run([NODE, "-e", flow, str(STATIC / "logins.js"), json.dumps(view, ensure_ascii=False), json.dumps(kkumi.view())],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["fold"] == ["꾸미기 버튼 설치 (링크 카드)", "false"] and got["before"] == 0  # 접혀 있고 펼칠 때만 받음
    assert got["steps"] == list(kkumi.STEPS) and got["clip"] == [kkumi.bookmarklet()] and got["toast"].startswith("복사했어요")
    assert got["keep"] == "true" and got["calls"] == 1
