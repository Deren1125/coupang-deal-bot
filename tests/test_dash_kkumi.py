"""'꾸미기' 책갈피 (주인 10/27 — 서버는 네이버에 로그인하지 않고 주인이 아이패드 사파리에서 붙여 넣음 · 10/29 v4: 위에서부터 차례로 쌓기).

1) kkumi.bookmarklet(): static/kkumi.js 를 줄여 "javascript:" + encodeURIComponent — node 의 encodeURIComponent 와 같고 new Function 으로 읽힘 ·
   바깥 요청 없음.
2) 가짜 글쓰기 화면(iframe 안 · 진짜 크롬, playwright 가 있을 때만) — 10/29 아이패드에서 본 대로: 스크립트로 넣은 것은 늘 맨 끝 · 붙여넣기
   신호는 맨 끝에 붙음 · 위 정렬 단추는 커서가 있는 글 덩어리에 · '본문 추가'로 맨 끝 새 줄. 칸에 붙여 넣은 글(표시 줄·인용구·주소 줄·태그 줄)이
   글 → 짧은 선 → 글 → 포스트잇(안에 글) → 글 → 카드 … 차례로 쌓이고 태그는 발행 설정창에 · 글쓰기 화면이 아니면 알림 하나.
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
    assert len(kkumi.bookmarklet()) < 26000  # 사파리 책갈피 주소 칸 (10/29 아이패드: v4 23KB 그대로 들어가고 돎)
    for net in ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "import(", ".src=", "location", "open(", "http://"):
        assert net not in js, net
    for word in ("ClipboardEvent('paste'", "DataTransfer", "se-canvas-bottom-button", "oglink", "se-popup-button-confirm", "quotation_postit",
                 "se-horizontalLine", "se-l-default", "publish_btn", "tag_input", "여기에 붙여넣기", "모두 됐어요", "placeholder"):
        assert word in js, word
    assert "data-value='left'" not in js and "execCommand" not in js  # 왼쪽으로 바꾸지 않음 · 글자 고치기 없음
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
# 10/29 아이패드에서 본 네이버 에디터처럼: 붙여넣기 신호·툴바로 넣은 것은 맨 끝에 (포스트잇 안에 커서가 있으면 그 안에) · 위 정렬 단추는
# 지금 정렬과 마지막 글 덩어리를 바꿈 · 넣은 선은 지금 정렬을 따름 · 맨 아래 '본문 추가'는 맨 끝 새 줄(포스트잇에서 나옴) · 링크 팝업 · 발행 설정창 태그 칸.
INNER = r"""<html><body>
<div class="se-documentTitle"><p class="se-text-paragraph">제목</p></div>
<div class="se-content" contenteditable="true"><div class="se-component se-text"><p class="se-text-paragraph"><span class="se-placeholder __se_placeholder">글감과 함께 나의 일상을 기록해보세요!</span></p></div></div>
<div id="tb">
<button data-name="horizontal-line">선</button><button data-name="horizontal-line-select">v</button>
<button data-name="quotation">인용</button><button data-name="quotation-select">v</button>
<button data-name="oglink">링크</button>
<button class="publish_btn">발행</button>
<button class="se-canvas-bottom-button __edge-area">본문 추가</button>
<button data-value="left">왼</button><button data-value="center">가</button>
</div>
<script>
const C = document.querySelector('.se-content');
let inPostit = null, selected = null, align = 'left';
document.querySelector("[data-value='left']").onclick = () => { align = 'left'; const t = [...C.querySelectorAll('.se-component.se-text')].pop(); if (t && t.querySelector('.se-section')) t.querySelector('.se-section').className = 'se-section se-section-align-left'; };
document.querySelector("[data-value='center']").onclick = () => { align = 'center'; const t = [...C.querySelectorAll('.se-component.se-text')].pop(); if (t && t.querySelector('.se-section')) t.querySelector('.se-section').className = 'se-section se-section-align-center'; };
const add = el => { C.append(el); return el; };
const comp = (cls, html) => { const d = document.createElement('div'); d.className = 'se-component ' + cls; d.innerHTML = html; return d; };
C.addEventListener('paste', e => {
  e.preventDefault();
  const h = e.clipboardData.getData('text/html');
  const doc = new DOMParser().parseFromString(h, 'text/html');
  const ps = [...doc.body.querySelectorAll('p')];
  // 빈 첫 줄 정리
  const first = C.querySelector('.se-component.se-text');
  if (first && C.children.length === 1 && first.querySelector('.se-placeholder')) first.remove();
  for (const p of ps) {
    const t = p.textContent;
    if (inPostit) { const q = document.createElement('p'); q.className = 'se-text-paragraph'; q.textContent = t; inPostit.querySelector('.qbox').append(q); }
    else { const lastc = C.lastElementChild; if (lastc && /se-text/.test(lastc.className) && !lastc.textContent) { lastc.querySelector('p').textContent = t; continue; } const c = add(comp('se-text', '<div class="se-section se-section-align-' + align + '"><p class="se-text-paragraph"></p></div>')); c.querySelector('p').textContent = t; }
  }
});
const menu = (id, attrs, label, fn) => { if (document.getElementById(id)) return; const b = document.createElement('button'); b.id = id; for (const k in attrs) b.setAttribute(k, attrs[k]); b.textContent = label; b.onclick = () => { fn(); b.remove(); }; document.getElementById('tb').append(b); };
const selectComp = c => { selected = c; document.querySelectorAll('.fbar').forEach(x => x.remove());
  const bar = document.createElement('div'); bar.className = 'fbar'; bar.style.cssText = 'position:relative';
  const al = document.createElement('button'); al.className = 'zz-al'; al.textContent = '≡';
  al.onclick = () => { const o = document.createElement('button'); o.className = 'zz-opt'; o.setAttribute('data-value', 'center'); o.textContent = '가'; o.onclick = () => { selected.querySelector('.se-section').className = 'se-section se-section-align-center'; o.remove(); }; bar.append(o); };
  const st = document.createElement('button'); st.className = 'zz-st'; st.textContent = '—';
  const tr = document.createElement('button'); tr.className = 'zz-tr'; tr.textContent = '🗑'; tr.onclick = () => { if (selected) { selected.remove(); selected = null; } bar.remove(); };
  bar.append(al, st, tr); c.before(bar); };
document.querySelector("[data-name='horizontal-line-select']").onclick = () => menu('ld', {'data-value': 'default', 'data-name': 'horizontal-default'}, '기본', () => {
  const c = comp('se-horizontalLine se-l-default', '<div class="se-section se-section-align-' + align + '"><hr></div>');
  if (inPostit) { inPostit.after(c); inPostit = null; } else add(c);
  selectComp(c);
  
});
document.addEventListener('click', e => { if (e.target.getAttribute && e.target.getAttribute('data-name') === 'align') menu('ac', {'data-value': 'center'}, '가운데', () => { if (selected) selected.querySelector('.se-section').classList.add('se-section-align-center'); }); }, true);
document.querySelector("[data-name='quotation-select']").onclick = () => menu('qp', {'data-value': 'quotation_postit'}, '포스트잇', () => {
  const c = add(comp('se-quotation se-l-quotation_postit', '<div class="qbox"></div>')); inPostit = c;
});
document.querySelector("[data-name='oglink']").onclick = () => {
  const pop = document.createElement('div'); pop.className = 'se-popup-oglink';
  pop.innerHTML = '<input placeholder="URL"><button class="se-popup-oglink-button">검색</button><button class="se-popup-button-confirm">확인</button>';
  document.body.append(pop);
  pop.querySelector('.se-popup-oglink-button').onclick = () => { const i = document.createElement('img'); pop.append(i); };
  pop.querySelector('.se-popup-button-confirm').onclick = () => { const u = pop.querySelector('input').value; const c = comp('se-oglink', '<a>' + u + '</a>'); if (inPostit) { inPostit.after(c); inPostit = null; } else add(c); pop.remove(); };
};
document.querySelector('.se-canvas-bottom-button').onclick = () => { inPostit = null; add(comp('se-text', '<div class="se-section se-section-align-' + align + '"><p class="se-text-paragraph"></p></div>')); };
document.querySelector('.publish_btn').onclick = () => { if (document.getElementById('tg')) return; const d = document.createElement('div'); d.id='tg'; d.innerHTML='<span class="chips"></span><input class="tag_input">'; document.body.append(d);
  d.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter') { d.querySelector('.chips').textContent += ' #' + e.target.value; e.target.value=''; } }); };
</script></body></html>
"""

# 대시보드 '본문 복사'와 같은 모양 (Blog-Auto paste 표시 줄 모드): 가운데 글 · \u2063 줄 = 구분선 자리 · 인용구 · 주소 줄 · 끝에 \u2064 태그 줄
PASTED = "<p style=\"text-align:center\">[\uc2dc\ud5d8 2] \uac00\uc6b4\ub370 \uc815\ub82c \uae00 \u2014 \uc800\uc7a5\ud558\uc9c0 \uc54a\ub294 \uae00</p><p style=\"text-align:center\"><br></p><p style=\"text-align:center\">\uac00\uc6b4\ub370 \uc815\ub82c \uccab \ubb38\ub2e8\uc774\uc5d0\uc694.</p><p style=\"text-align:center\">\ub458\uc9f8 \uc904\ub3c4 \uac00\uc6b4\ub370\uc608\uc694.</p><p>\u2063</p><p style=\"text-align:center\">\uad6c\ubd84\uc120 \uc544\ub798\ub3c4 \uac00\uc6b4\ub370\uc608\uc694.</p><blockquote><p style=\"text-align:center\">\ud3ec\uc2a4\ud2b8\uc787 \uae00\uc774\uc5d0\uc694.</p><p style=\"text-align:center\">\ub458\uc9f8 \uc904\uc774\uc5d0\uc694.</p></blockquote><p style=\"text-align:center\">\ud3ec\uc2a4\ud2b8\uc787 \uc544\ub798 \uac00\uc6b4\ub370 \ubb38\ub2e8\uc774\uc5d0\uc694.</p><p style=\"text-align:center\">https://t.me/Derens_tip</p><p style=\"text-align:center\">\uce74\ub4dc \uc544\ub798 \uac00\uc6b4\ub370 \ubb38\ub2e8\uc774\uc5d0\uc694.</p><p>\u2063</p><p style=\"text-align:center\">\ub9c8\uc9c0\ub9c9 \uac00\uc6b4\ub370 \ubb38\ub2e8\uc774\uc5d0\uc694.</p><p>\u2064\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200c\u200c\u200c\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200d\u200c\u200c\u200d\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200d\u200c\u200d\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200d\u200d\u200c\u200c\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200d\u200c\u200c\u200c\u200d\u200c\u200d\u200d\u200d\u200c\u200c\u200d\u200d\u200d\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200d\u200c\u200d\u200d\u200d\u200d\u200c\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200c\u200d\u200c\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200d\u200d\u200c\u200c\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200d\u200d\u200c\u200c\u200d\u200d\u200d\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200c\u200c\u200c\u200d\u200d\u200c\u200c\u200d\u200d\u200d\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200d\u200c\u200d\u200d\u200d\u200d\u200c\u200d\u200d\u200d\u200c\u200c\u200c\u200c\u200c\u200d\u200c\u200c\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200c\u200d\u200d\u200d\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200c\u200d\u200d\u200c\u200c\u200c\u200c\u200d\u200c\u200c\u200c\u200d\u200c\u200c\u200c\u200d\u200d\u200d\u200c\u200d\u200d\u200c\u200d\u200d\u200c\u200c\u200d\u200c\u200d\u200c\u200c\u200d\u200c\u200d\u200d\u200d\u200d\u200c\u200c</p>"

RUN = r"""
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  const alerts = [];
  p.on('dialog', async d => { alerts.push(d.message()); await d.accept(); });
  const [inner, code, pasted] = process.argv.slice(1), out = {};
  await p.setContent('<iframe id="mainFrame" style="width:900px;height:700px"></iframe>');
  await p.$eval('iframe', (f, h) => { f.srcdoc = h; }, inner);
  const frame = await (await p.waitForSelector('iframe')).contentFrame();
  await frame.waitForSelector('.se-content');
  p.evaluate(code);
  await frame.waitForSelector('[aria-label="여기에 붙여넣기"]');
  await frame.evaluate(h => { const box = document.querySelector('[aria-label="여기에 붙여넣기"]'), dt = new DataTransfer();
    dt.setData('text/html', h); dt.setData('text/plain', 'x');
    box.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt})); }, pasted);
  for (let i = 0; i < 400 && !alerts.length; i++) await p.waitForTimeout(100);
  out.alert = alerts[0] || '';
  out.comps = await frame.evaluate(() => [...document.querySelectorAll('.se-content > .se-component')].map(c => {
    const k = /horizontalLine/.test(c.className) ? 'LINE' : /quotation/.test(c.className) ? 'POSTIT' : /oglink/.test(c.className) ? 'CARD' : 'TEXT';
    const al = /align-center/.test((c.querySelector('.se-section') || {}).className || '') ? ' C' : '';
    return k + al + (k === 'LINE' ? '' : ' ' + c.textContent.trim().slice(0, 14));
  }).filter(x => x !== 'TEXT  ' && x !== 'TEXT C ' && x.trim() !== 'TEXT'));
  out.tags = await frame.evaluate(() => (document.querySelector('.chips') || {}).textContent || '');
  await p.setContent('<p>다른 화면</p>');
  const n = alerts.length;
  await p.evaluate(code);
  for (let i = 0; i < 50 && alerts.length === n; i++) await p.waitForTimeout(100);
  out.elsewhere = alerts.slice(n);
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


def test_bookmarklet_builds_the_post_top_down_on_a_mock_editor() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RUN, INNER, code(), PASTED], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["alert"].startswith("모두 됐어요") and "링크 카드 1/1 · 구분선 2/2 · 포스트잇 1/1 · 태그 3/3" in got["alert"], got["alert"]
    assert "포스트잇: 글 안 → 나옴" in got["alert"]
    kinds = [c.split(" ")[0] for c in got["comps"]]
    assert [k for i, k in enumerate(kinds) if not (k == "TEXT" and i and kinds[i - 1] == "TEXT")] == \
        ["TEXT", "LINE", "TEXT", "POSTIT", "TEXT", "CARD", "TEXT", "LINE", "TEXT"], got["comps"]  # 맨 끝에 차례로 쌓아 원래 차례 그대로
    assert all(c.startswith("LINE C") for c in got["comps"] if c.startswith("LINE"))  # 가운데 글 → 가운데 선
    assert any("포스트잇 글이에요" in c for c in got["comps"] if c.startswith("POSTIT"))  # 인용구 글은 포스트잇 안에
    assert sum(k == "CARD" for k in kinds) == 1 and got["comps"][kinds.index("CARD") - 1].endswith("https://t.me/D")  # 주소 줄 바로 아래 카드
    assert got["tags"].split() == ["#가운데시험", "#데런로그", "#책갈피"]
    assert got["elsewhere"] == ["꾸미기: 글쓰기 화면이 아니에요"]


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
