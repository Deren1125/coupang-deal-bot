"""'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 코디 랭킹 캡처 올리기 대신 · 서버는 에이블리를 열지 않음, 10/28).

1) ablyrank.bookmarklet(): static/ablyrank.js → "javascript:" 주소 · 파일 규칙(statscap.js 와 같음).
2) 가짜 에이블리 랭킹 화면 (진짜 크롬 · node playwright — 모든 요청을 가로채 바깥으로 나가지 않음): 순위·상품명·쇼핑몰·가격·할인·리뷰·별점·
   상품·사진 주소 · 코디 카드(제목·좋아요·든 상품) · 고른 탭 · 늦게 뜨는 카드 기다림 → 서버 save 까지. 에이블리 밖에서는 알림만.
3) save(): 열쇠 · 에이블리 주소만 · 칸 모양 · 개수·크기 제한 · 30일 · 하루 개수.
4) GET /dashboard/api/ablyrank (로그인 아래) · POST /dashboard/api/ably_rank_capture (열쇠로만 · CORS 는 a-bly.com 만 · 폼이면 HTML) ·
   Blog-Auto 가 'ably_rank_capture' 를 알리면 일로 넘김 (계약 action_ably_rank_capture.json).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import fixture, get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import ablyrank, statscap

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
TZ = ZoneInfo("Asia/Seoul")
NOW = 1_794_480_000.0  # 2026-11-12 KST


class FakeDb:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v

    def kv_items(self, prefix: str) -> list[tuple[str, str | None]]:
        return sorted((k, v) for k, v in self.kv.items() if k.startswith(prefix))

    def kv_delete_prefix(self, prefix: str, *, keep=None) -> int:  # type: ignore[no-untyped-def]
        gone = [k for k, v in self.kv.items() if k.startswith(prefix) and not (keep and keep(k, v))]
        for k in gone:
            del self.kv[k]
        return len(gone)


def code(db: FakeDb) -> str:
    c = ablyrank.view(db, "https://dash.example")["code"]
    assert c.startswith("javascript:")
    return unquote(c[len("javascript:"):])


def recs(db: FakeDb) -> list[dict]:
    return [json.loads(v) for k, v in sorted(db.kv.items()) if k.startswith(ablyrank.PREFIX)]


# ---------------------------------------------------------------- 1) 책갈피
def test_bookmarklet_has_endpoint_key_and_keeps_file_rules() -> None:
    db = FakeDb()
    js = code(db)
    assert "'https://dash.example/dashboard/api/ably_rank_capture'" in js and f"'{ablyrank.key(db)}'" in js and "@@" not in js
    # 사파리 책갈피 주소 칸: 꾸미기 책갈피 28KB 가 아이패드에서 돎 (test_dash_kkumi) · 11/12 코디 랭킹 넣으며 16→22KB · 진행 띠 넣으며 24KB
    assert "/*" not in js and "\n//" not in js and len(ablyrank.view(db, "https://dash.example")["code"]) < 24000
    # 대시보드로 쿠키 없이 한 번만 · 코디 화면은 같은 출처만 숨은 iframe 으로 (받아 오기 없음)
    assert js.count("fetch(") == 1 and "fetch(U,{method:'POST',mode:'cors',credentials:'omit'" in js and "XMLHttpRequest" not in js and "cookie" not in js
    assert "credentials:'include'" not in js and "origin===location.origin" in js and "allow-top-navigation" not in js
    assert ablyrank.key(db) != statscap.key(db) and len(ablyrank.INSTALL_STEPS) == 4
    with pytest.raises(ValueError):
        ablyrank.bookmarklet("https://x.example/a'b", "k")
    src = (STATIC / "ablyrank.js").read_text(encoding="utf-8")
    body = src.split("*/", 1)[1]
    for ln in body.splitlines():
        s = ln.strip()
        assert "//" not in s or s.startswith("//") or "://" in s or "\\/" in s, ln  # // 주석은 줄 맨 앞에만
    for rx in re.findall(r"(?<![\w)])/(?![/*])((?:\\.|[^/\n])+)/[gimsuy]*[.;),]", body):
        assert " " not in rx and "'" not in rx and '"' not in rx, rx  # 정규식 안에 공백·따옴표 없음
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "ablyrank.js")], capture_output=True).returncode == 0
        assert subprocess.run([NODE, "-e", "new Function(process.argv[1])", js], capture_output=True).returncode == 0  # 줄인 코드도 읽힘


# ---------------------------------------------------------------- 2) 가짜 에이블리 화면 (진짜 크롬)
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>코디 랭킹 | 에이블리</title></head><body>
<header><nav><a href="/">홈</a> <a href="/ranking">랭킹</a></nav></header>
<h1>랭킹</h1>
<div role="tablist"><button role="tab" aria-selected="false">상품</button><button role="tab" aria-selected="true">코디</button>
<button role="tab" aria-selected="false">마켓</button></div>
<ul id="looks">
 <li><a href="/coordi/7001"><span>1</span><img src="https://img.cdn.example/look/7001.jpg" width="200"><p>데이트룩 니트 코디</p><span>좋아요 1.2만</span></a>
  <div><a href="/goods/111"><img src="https://img.cdn.example/g/111.jpg" width="80"><span class="market_name">러브마켓</span><span class="goods_name">앙고라 브이넥 니트</span><span>20%</span><b>31,200원</b></a></div>
  <div><a href="/goods/112"><img src="https://img.cdn.example/g/112.jpg" width="80"><p>러브마켓</p><p>A라인 미니 스커트</p><p>25,900원</p></a></div>
 </li>
</ul>
<ol id="goods"></ol>
<p id="ld">불러오는 중</p>
<script>
setTimeout(() => {
  const ol = document.getElementById('goods');
  ol.innerHTML = [
    '<li><div><span>1</span></div><a href="https://m.a-bly.com/goods/201"><img data-src="https://img.cdn.example/g/201.jpg" width="120"></a>'
      + '<div><p>무드온</p><a href="https://m.a-bly.com/goods/201">울 블렌드 롱 코트</a><p><em>35%</em> <del>99,000</del> <strong>64,350원</strong></p>'
      + '<p>★4.8 리뷰 1,234</p><p>무료배송</p></div></li>',
    '<li><div><span>2위</span></div><a href="/goods/202"><img src="https://img.cdn.example/g/202.jpg" width="120"></a>'
      + '<div><p>데일리룸</p><a href="/goods/202">케이블 가디건 2026 신상</a><p>29,900원</p><p>찜 532</p></div></li>',
    '<li style="display:none"><a href="/goods/299">숨은 상품</a><p>9,900원</p></li>'
  ].join('');
  document.getElementById('ld').remove();
}, 1200);
</script></body></html>"""

RUN = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, page] = process.argv.slice(1), out = {alerts: [], posts: [], outside: []};
  const b = await chromium.launch();
  const p = await b.newPage();
  p.on('dialog', async d => { out.alerts.push(d.message()); await d.accept(); });
  await p.route('**/*', async r => {
    const u = r.request().url();
    if (u.startsWith('https://dash.example/')) {
      out.posts.push({url: u, body: r.request().postData() || '', cookie: r.request().headers()['cookie'] || ''});
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': 'https://m.a-bly.com'},
        body: JSON.stringify({ok: true, label: '상품 2 · 코디 1 · 코디'})});
    }
    if (u === 'https://m.a-bly.com/ranking') return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: page});
    if (u === 'https://example.com/') return r.fulfill({status: 200, contentType: 'text/html', body: '<p>다른 곳</p>'});
    out.outside.push(u);
    return r.fulfill({status: 204, body: ''});
  });
  await p.goto('https://m.a-bly.com/ranking');
  const t = Date.now();
  await p.evaluate(code);
  out.waited = Date.now() - t;
  await p.goto('https://example.com/');
  await p.evaluate(code);
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


def test_bookmarklet_on_fake_ably_ranking_collects_cards_and_server_saves() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    res = subprocess.run([NODE, "-e", RUN, code(db), PAGE], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["alerts"] == ["Ably 랭킹 보냄 ✓ (코디 1 · 상품 4)", "Ably 랭킹 보내기: 에이블리 랭킹 화면(a-bly.com)에서 눌러 주세요"]
    assert all(u.startswith("https://img.cdn.example/") for u in got["outside"])  # 사진은 화면(브라우저)이 받음 — 책갈피는 대시보드로만 보냄
    assert 900 <= got["waited"] < 6000  # 카드가 늦게 떠서 0.5초씩 기다림
    assert len(got["posts"]) == 1 and got["posts"][0]["url"] == "https://dash.example/dashboard/api/ably_rank_capture"
    assert got["posts"][0]["cookie"] == ""
    sent = dict(parse_qsl(got["posts"][0]["body"], keep_blank_values=True))
    assert sent["k"] == ablyrank.key(db) and sent["u"] == "https://m.a-bly.com/ranking" and sent["t"] == "코디 랭킹 | 에이블리"
    assert sent["s"].splitlines()[0] == "코디" and "랭킹" in sent["h"].splitlines()
    cards = json.loads(sent["j"])
    it = {i["url"].rsplit("/", 1)[1]: i for i in cards["items"]}
    assert set(it) == {"201", "202"}  # 숨은 상품 뺌 · 코디 안 상품은 코디 쪽에
    a, b = it["201"], it["202"]
    assert (a["rank"], a["shop"], a["name"], a["price"], a["orig"], a["disc"], a["star"], a["rev"]) == \
        (1, "무드온", "울 블렌드 롱 코트", 64350, 99000, 35, 4.8, "1,234")
    assert a["img"] == "https://img.cdn.example/g/201.jpg" and a["url"] == "https://m.a-bly.com/goods/201"
    assert (b["rank"], b["shop"], b["name"], b["price"], b["like"]) == (2, "데일리룸", "케이블 가디건 2026 신상", 29900, "532")
    assert "orig" not in b and "disc" not in b
    (lk,) = cards["looks"]
    assert (lk["rank"], lk["title"], lk["like"], lk["url"], lk["img"]) == \
        (1, "데이트룩 니트 코디", "1.2만", "https://m.a-bly.com/coordi/7001", "https://img.cdn.example/look/7001.jpg")
    assert [(x["shop"], x["name"], x["price"]) for x in lk["items"]] == [("러브마켓", "앙고라 브이넥 니트", 31200), ("러브마켓", "A라인 미니 스커트", 25900)]
    assert lk["items"][0]["disc"] == 20
    out = ablyrank.save(db, sent, now=NOW, tz=TZ)
    assert out["ok"] and out["label"] == "상품 2 · 코디 1 · 코디" and out["key"].startswith("ably_rank_capture:2026-11-12:")
    (rec,) = recs(db)
    assert rec["tab"] == "코디" and rec["items"][0]["pos"] == 1 and rec["looks"][0]["items"][1]["name"] == "A라인 미니 스커트"
    assert "울 블렌드 롱 코트" in rec["text"]


# 11/12 아이패드 사파리 mobile.a-bly.com/content/style/home '랭킹' 탭 — 0개로 왔던 화면 흉내:
# 링크·가격 없는 사진 카드 2줄 · 사진 왼쪽 위 순위 딱지('1 –'·'3 9↑'·'5 NEW') · 닉네임·해시태그·바로입기 · 그림자 DOM 안 스크롤 칸 ·
# 1초 뒤에 뜨고 내리면 12개씩 더 (36개까지) · 1~3위만 같은 출처 코디 화면 링크 (상품은 그 화면 __NEXT_DATA__ 에)
COORDI = """<!doctype html><html><head><meta charset="utf-8"><title>에이블리</title>
<style>body{margin:0;width:390px}.tabs{display:flex;gap:12px}.tabs span{color:#999;font-weight:400}.tabs span.x{color:#000;font-weight:700;border-bottom:2px solid #000}</style></head><body>
<div class="top">앱에서 더 많은 상품을 볼 수 있어요! <button>앱에서 보기</button></div>
<div>코디</div>
<div class="tabs"><span>PICK</span><span>팔로잉</span><span>추천</span><span class="x">랭킹</span><span>데일리</span><span>찜한 상품</span><span>오늘 날씨</span></div>
<div id="host"></div>
<script>
const NICK = ['llilililii', '읍스읍스', 'mori_girl', '겨울곰'];
const CHG = n => n % 5 === 0 ? '<span>NEW</span>' : n % 3 === 0 ? '<svg class="ic_up" width="8" height="8"></svg><span style="color:rgb(255,40,40)">9</span>'
  : n % 7 === 0 ? '<svg class="ic_down" width="8" height="8"></svg><span>2</span>' : '<span>–</span>';
const card = n => {
  const pic = n % 4 === 2 ? '<div class="ph" style="width:180px;height:240px;background-image:url(https://img.cdn.example/c/' + n + '_bg.jpg)"></div>'
    : '<img style="width:180px;height:240px;display:block" src="https://img.cdn.example/c/' + n + '_s.jpg" srcset="https://img.cdn.example/c/' + n + '_320.jpg 320w, https://img.cdn.example/c/' + n + '_640.jpg 640w">';
  const photo = '<div style="position:relative">' + (n <= 3 ? '<a href="/coordi/' + (9000 + n) + '">' + pic + '</a>' : pic)
    + '<div class="rk" style="position:absolute;top:6px;left:6px;display:flex;gap:2px"><span>' + n + '</span>' + CHG(n) + '</div>'
    + (n % 2 ? '<span class="chip" style="position:absolute;bottom:6px;left:6px">바로입기</span>' : '') + '</div>';
  return '<div class="card" onclick="void 0">' + photo + '<div class="prof"><img width="20" height="20" src="https://img.cdn.example/av/' + n + '.jpg">'
    + '<span>' + NICK[n % 4] + '</span></div><p>#겨울코디 #빈티지 #모리걸</p></div>';
};
setTimeout(() => {
  let root = null;
  if ('%MODE%' === 'iframe') {
    const f = document.createElement('iframe');
    f.style.cssText = 'width:390px;height:760px;border:0';
    document.getElementById('host').appendChild(f);
    root = f.contentDocument;
    root.body.style.margin = '0';
    root = root.body.appendChild(root.createElement('div'));
    root.getElementById = id => root.querySelector('#' + id);
  } else root = document.getElementById('host').attachShadow({mode: 'open'});
  root.innerHTML = '<div><b>랭킹순위 ?</b><p>12:00 PM 업데이트</p></div><div id="sc" style="height:700px;overflow-y:auto">'
    + '<div id="grid" style="display:grid;grid-template-columns:180px 180px;gap:8px"></div></div>';
  const g = root.getElementById('grid'), sc = root.getElementById('sc');
  let n = 0;
  const more = () => { let h = ''; for (let i = 0; i < 12 && n < 36; i++) h += card(++n); g.insertAdjacentHTML('beforeend', h); };
  more();
  sc.addEventListener('scroll', () => { if (sc.scrollTop + sc.clientHeight > sc.scrollHeight - 400) setTimeout(more, 150); });
}, 1000);
</script></body></html>"""

# 코디 화면(요즘코디): 앱 화면이라 HTML 에는 상품이 없고 0.6초 뒤 그림 — 큰 사진 · 하트·댓글 · 가로 상품 줄(할인·가격·작은 이름) · 해시태그
DETAIL = """<!doctype html><html><head><meta charset="utf-8"><title>요즘코디</title></head><body style="margin:0"><div id="app"></div>
<script>
const P = [['빈티지 니트', 63, '6,900'], ['코듀로이 팬츠', 0, '14,800'], ['울 머플러', 54, '17,800']];
setTimeout(() => {
  document.getElementById('app').innerHTML = '<h1>요즘코디</h1><img style="width:390px;height:480px;display:block" src="https://img.cdn.example/d/%d.jpg">'
    + '<div><img width="16" height="16" src="https://img.cdn.example/i/heart.png"> 120 · 댓글 3</div><div style="display:flex;overflow-x:auto;gap:8px">'
    + P.map((p, i) => '<a href="/goods/%d' + i + '" style="flex:0 0 100px;display:block"><img style="width:100px;height:120px;display:block" src="https://img.cdn.example/g/%d' + i + '.jpg">'
      + '<p>' + p[0] + '</p><p>' + (p[1] ? '<b>' + p[1] + '%</b> ' : '') + p[2] + '</p></a>').join('') + '</div><p>#빈티지룩 #겨울코디</p><ul><li>예뻐요</li></ul>';
}, 600);
</script></body></html>"""

RUN_CO = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, page, detail] = process.argv.slice(1), out = {alerts: [], posts: [], details: [], outside: []};
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 390, height: 844}});
  p.on('dialog', async d => { out.alerts.push(d.message()); await d.accept(); });
  await p.route('**/*', async r => {
    const u = r.request().url();
    if (u.startsWith('https://dash.example/')) {
      out.posts.push({body: r.request().postData() || '', cookie: r.request().headers()['cookie'] || ''});
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': 'https://mobile.a-bly.com'},
        body: JSON.stringify({ok: true, label: '코디 36 · 랭킹'})});
    }
    if (u === 'https://mobile.a-bly.com/content/style/home') return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: page});
    const m = /^https:\/\/mobile\.a-bly\.com\/coordi\/(\d+)$/.exec(u);
    if (m) { out.details.push(u); return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: detail.split('%d').join(m[1])}); }
    out.outside.push(u);
    return r.fulfill({status: 204, body: ''});
  });
  await p.addInitScript(() => { window.__ov = []; setInterval(() => { const h = document.querySelector('[data-abcap]:not(iframe)'); const m = h && h.shadowRoot && h.shadowRoot.textContent;
    if (m && window.__ov[window.__ov.length - 1] !== m) window.__ov.push(m); }, 50); });
  await p.goto('https://mobile.a-bly.com/content/style/home');
  const t = Date.now();
  await p.evaluate(code);
  out.waited = Date.now() - t;
  out.ov = await p.evaluate(() => window.__ov);
  out.left = await p.evaluate(() => document.querySelectorAll('[data-abcap]').length);
  await p.evaluate(() => { window.__abcap = 1; });
  await p.evaluate(code);
  out.busy = out.alerts.pop();
  out.scroll = await p.evaluate(() => PLACE_SCROLL.scrollTop);
  out.frames = await p.evaluate(() => document.querySelectorAll('iframe').length);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


@pytest.mark.parametrize("mode", ["shadow", "iframe"])  # 카드가 그림자 DOM 안이든 같은 출처 iframe 안이든
def test_bookmarklet_on_fake_coordi_ranking_tab_collects_looks_without_items(mode: str) -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    run = RUN_CO.replace("PLACE_SCROLL", "document.getElementById('host').querySelector('iframe').contentDocument.querySelector('#sc')" if mode == "iframe"
                         else "document.getElementById('host').shadowRoot.getElementById('sc')")
    res = subprocess.run([NODE, "-e", run, code(db), COORDI.replace("%MODE%", mode), DETAIL], capture_output=True, text=True, timeout=180, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["alerts"] == ["Ably 랭킹 보냄 ✓ (코디 36 · 상품 9)"]
    # 진행 띠 (주인 11/12): 누르자마자 · 카드 찾기 → 내리기 → 상세 → 보내기 · 끝나면 치움
    ov = got["ov"]
    assert ov[0].startswith("에이블리 모으는 중… 카드 찾는 중 (") and any(re.fullmatch(r"카드 \d+개 · 아래로 내리는 중 1/6", m) for m in ov)
    assert "상세 읽는 중 0/3" in ov and got["left"] == 0  # 상세 1~3/3·보내는 중은 순간이라 50ms 보기에 안 잡힐 때도
    assert got["busy"] == "Ably 랭킹 보내기: 이미 모으는 중이에요"
    assert sorted(got["details"]) == [f"https://mobile.a-bly.com/coordi/{9000 + n}" for n in (1, 2, 3)]  # 같은 출처 코디 화면만 · 숨은 iframe (주인 브라우저)
    assert all(u.startswith("https://img.cdn.example/") for u in got["outside"]) and got["scroll"] == 0 and got["frames"] == (mode == "iframe")  # 내린 뒤 제자리 · 숨은 iframe 치움
    assert 900 <= got["waited"] < 25000
    (post,) = got["posts"]
    assert post["cookie"] == ""
    sent = dict(parse_qsl(post["body"], keep_blank_values=True))
    assert sent["s"].splitlines()[0] == "랭킹"
    cards = json.loads(sent["j"])
    assert cards["items"] == [] and cards["upd"] == "12:00 PM 업데이트" and "roots 2" in cards["dbg"] and "cards 36 urls 3" in cards["dbg"]
    assert "ready complete" in cards["dbg"] and ("shadow 1" if mode == "shadow" else "iframe 1") in cards["dbg"] and len(cards["dbg"]) < 2000
    lk = cards["looks"]
    assert [x["rank"] for x in lk] == list(range(1, 37))  # 내리며 더 뜬 카드까지
    by = {x["rank"]: x for x in lk}
    assert (by[1]["chg"], by[3]["chg"], by[5]["chg"], by[7]["chg"], by[6]["chg"]) == ("–", "9↑", "NEW", "2↓", "9↑")
    assert by[1]["img"] == "https://img.cdn.example/c/1_640.jpg" and by[2]["img"] == "https://img.cdn.example/c/2_bg.jpg"
    assert (by[1]["nick"], by[2]["nick"]) == ("읍스읍스", "mori_girl") and by[4]["nick"] == "llilililii"
    assert by[1]["tags"] == ["#겨울코디", "#빈티지", "#모리걸"] and by[1]["quick"] is True and by[2]["quick"] is False
    assert by[1]["url"] == "https://mobile.a-bly.com/coordi/9001" and by[4]["url"] == ""
    assert [(i["name"], i.get("disc"), i["price"], i["url"], i["img"]) for i in by[1]["items"]] == [
        ("빈티지 니트", 63, 6900, "https://mobile.a-bly.com/goods/90010", "https://img.cdn.example/g/90010.jpg"),
        ("코듀로이 팬츠", None, 14800, "https://mobile.a-bly.com/goods/90011", "https://img.cdn.example/g/90011.jpg"),
        ("울 머플러", 54, 17800, "https://mobile.a-bly.com/goods/90012", "https://img.cdn.example/g/90012.jpg")]
    assert "읍스읍스" in sent["x"] and "#겨울코디" in sent["x"]  # 그림자 DOM 안 글도
    out = ablyrank.save(db, sent, now=NOW, tz=TZ)
    assert out["label"] == "코디 36 · 랭킹" and out["items"] == 0
    (rec,) = recs(db)
    r1 = rec["looks"][0]
    assert (rec["tab"], rec["upd"]) == ("랭킹", "12:00 PM 업데이트")
    assert (r1["rank"], r1["chg"], r1["nick"], r1["quick"], r1["tags"][0], r1["items"][0]["price"]) == (1, "–", "읍스읍스", True, "#겨울코디", 6900)
    assert "cards 36" in rec["dbg"]


def test_bookmarklet_on_empty_looking_page_sends_dbg_and_says_so() -> None:
    """11/12 실제: 코디 › 랭킹에서 눌렀는데 글은 위 띠만 — 그래도 화면 모양(dbg)을 보내고 알림으로 알려 줌."""
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    page = '<!doctype html><html><head><meta charset="utf-8"><title>스타일 - 에이블리 스토어</title></head><body><div class="Banner_top__a1">앱에서 더 많은 상품을 볼 수 있어요! 앱에서 보기</div><app-root></app-root></body></html>'
    run = RUN_CO.replace("PLACE_SCROLL", "document.body")
    res = subprocess.run([NODE, "-e", run, code(FakeDb()), page, DETAIL], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["alerts"] == ["Ably 랭킹 보냄 ✓ (코디 0 · 상품 0) · dbg 보냄 — 메인에게 알려 주세요"] and 7500 <= got["waited"] < 15000
    sent = dict(parse_qsl(got["posts"][0]["body"], keep_blank_values=True))
    dbg = json.loads(sent["j"])["dbg"]
    assert "ready complete" in dbg and "custom 1" in dbg and "cls Banner_top" in dbg and "text 28 앱에서 더 많은" in dbg


def test_save_takes_coordi_cards_alone_and_checks_their_fields() -> None:
    db = FakeDb()
    looks = [{"rank": 1, "chg": "9↑", "img": "https://img.cdn.example/c/1.jpg", "nick": "읍스읍스", "tags": ["#겨울코디", "#<b>", "nope", "#" + "가" * 40],
              "quick": True, "url": "", "items": []},
             {"rank": 2, "chg": "<script>", "nick": "llilililii", "quick": "yes"}, {"rank": 3}]
    out = ablyrank.save(db, form(db, j=json.dumps({"items": [], "looks": looks, "upd": "12:00 PM 업데이트", "dbg": "roots 2"}), s="랭킹"), now=NOW, tz=TZ)
    assert out["label"] == "코디 2 · 랭킹"
    (rec,) = recs(db)
    a, b = rec["looks"]
    assert (a["chg"], a["nick"], a["tags"], a["quick"]) == ("9↑", "읍스읍스", ["#겨울코디", "#" + "가" * 30], True)  # 긴 태그는 30자로
    assert "chg" not in b and "quick" not in b and b["nick"] == "llilililii"
    assert (rec["upd"], rec["dbg"]) == ("12:00 PM 업데이트", "roots 2")


# ---------------------------------------------------------------- 3) 받기
def form(db: FakeDb, **kw: str) -> dict[str, str]:
    cards = {"items": [{"rank": 1, "name": "코트", "shop": "무드온", "price": 64350, "url": "https://m.a-bly.com/goods/1",
                        "img": "https://img.cdn.example/1.jpg"}], "looks": []}
    base = {"k": ablyrank.key(db), "u": "https://m.a-bly.com/ranking", "t": "랭킹", "s": "상품", "h": "", "j": json.dumps(cards), "x": "1\n코트"}
    return {**base, **kw}


def test_save_checks_key_where_and_shapes() -> None:
    db = FakeDb()
    for bad, code_ in ((form(db, k="nope"), "key"), (form(db, k=""), "key"), (form(db, u="https://evil.example/a-bly.com"), "where"),
                       (form(db, u="https://a-bly.com.evil.example/"), "where"), (form(db, u="http://m.a-bly.com/"), "where"),
                       (form(db, j="", x=""), "empty")):
        with pytest.raises(statscap.CaptureError) as e:
            ablyrank.save(db, bad, now=NOW, tz=TZ)
        assert e.value.code == code_
    assert not recs(db)
    weird = {"items": [{"name": "<script>alert(1)</script>니트 010-1234-5678", "url": "https://evil.example/goods/1", "img": "javascript:alert(1)",
                        "price": "1000", "rank": 5000, "star": 9, "rev": "<b>", "orig": 500},
                       {"name": "가디건", "url": "https://a-bly.com/goods/2", "price": 29900, "orig": 39900, "disc": 25, "star": 4.66}, "x", None],
             "looks": [{"title": "코디", "items": [{"name": f"상품{i}", "price": 1000} for i in range(30)]}]}
    ablyrank.save(db, form(db, j=json.dumps(weird)), now=NOW, tz=TZ)
    (rec,) = recs(db)
    w, g = rec["items"]
    assert "<" not in w["name"] and "010-1234-5678" not in w["name"] and "url" not in w and "img" not in w
    assert "price" not in w and "rank" not in w and "star" not in w and "rev" not in w and "orig" not in w
    assert (g["url"], g["price"], g["orig"], g["disc"], g["star"]) == ("https://a-bly.com/goods/2", 29900, 39900, 25, 4.7)
    assert len(rec["looks"][0]["items"]) == ablyrank.LOOK_ITEMS_MAX


def test_save_caps_items_and_record_size() -> None:
    db = FakeDb()
    many = {"items": [{"name": "가" * 200, "shop": "나" * 80, "price": 10000 + i, "url": f"https://m.a-bly.com/goods/{i}",
                       "img": f"https://img.cdn.example/{'x' * 300}/{i}.jpg"} for i in range(150)], "looks": []}
    out = ablyrank.save(db, form(db, j=json.dumps(many, ensure_ascii=False), x="다" * 20000), now=NOW, tz=TZ)
    raw = next(v for k, v in db.kv.items() if k.startswith(ablyrank.PREFIX))
    assert len(raw.encode()) <= ablyrank.REC_MAX and 0 < out["items"] < ablyrank.ITEMS_MAX
    assert out["items"] == len(json.loads(raw)["items"])


def test_keeps_30_days_and_caps_a_day_and_new_secret_drops_old_key() -> None:
    db = FakeDb()
    db.kv[ablyrank.PREFIX + "2026-10-01:120000-aaaa"] = "{}"
    db.kv[ablyrank.PREFIX + "2026-10-20:120000-aaaa"] = "{}"
    ablyrank.save(db, form(db), now=NOW, tz=TZ)
    assert ablyrank.PREFIX + "2026-10-01:120000-aaaa" not in db.kv and ablyrank.PREFIX + "2026-10-20:120000-aaaa" in db.kv
    assert ablyrank.today_count(db, NOW, TZ) == 1
    for i in range(ablyrank.DAY_MAX - 1):
        ablyrank.save(db, form(db), now=NOW + i, tz=TZ)
    with pytest.raises(statscap.CaptureError) as e:
        ablyrank.save(db, form(db), now=NOW + 100, tz=TZ)
    assert e.value.status == 429
    k1 = ablyrank.key(db)
    del db.kv[ablyrank.KV_SECRET]
    assert not ablyrank.key_ok(db, k1)


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
    return await bot.web.dispatch("POST", ablyrank.PATH, h, raw)


def _good(bot: DealBot) -> dict[str, str]:
    return {"k": ablyrank.key(bot.db), "u": "https://m.a-bly.com/ranking", "t": "랭킹", "s": "코디",
            "j": json.dumps({"items": [{"rank": 1, "name": "코트", "price": 64350, "url": "https://m.a-bly.com/goods/1"}], "looks": []}), "x": ""}


async def test_api_code_needs_login_and_capture_takes_key_only(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/ablyrank", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/ablyrank", session_headers(bot))
    assert status == 200 and v["steps"] == list(ablyrank.INSTALL_STEPS) and ablyrank.key(bot.db) in unquote(v["code"])
    good = _good(bot)
    res = await _post(bot, good, origin="https://m.a-bly.com")
    assert res[0] == 200 and json.loads(res[1]) == {"ok": True, "label": "상품 1 · 코디", "handed": False}  # 예전 Blog-Auto — kv 에만
    assert res[3]["Access-Control-Allow-Origin"] == "https://m.a-bly.com"
    res = await _post(bot, {**good, "k": statscap.key(bot.db)}, origin="https://a-bly.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key" and res[3]["Access-Control-Allow-Origin"] == "https://a-bly.com"
    res = await _post(bot, good, origin="https://evil.example")
    assert res[0] == 200 and "Access-Control-Allow-Origin" not in res[3]
    res = await _post(bot, {**good, "via": "form"}, origin="null")
    assert res[0] == 200 and res[2].startswith("text/html") and "Ably 랭킹 보냄 ✓ (상품 1 · 코디)" in res[1]
    assert (await bot.web.dispatch("GET", ablyrank.PATH, anon))[0] == 405
    assert len(bot.db.kv_items(ablyrank.PREFIX)) == 3
    assert bot.actions_out.store.open_rows() == []


async def test_hands_off_when_blogauto_lists_the_action(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.bridge.status import BlogAutoStatus

    monkeypatch.setattr(bot.actions_out, "status", lambda: BlogAutoStatus(actions=["ably_rank", ablyrank.ACTION]))
    res = await _post(bot, _good(bot), origin="https://m.a-bly.com")
    assert res[0] == 200 and json.loads(res[1])["handed"] is True
    (row,) = bot.actions_out.store.open_rows()
    (k, _v), = bot.db.kv_items(ablyrank.PREFIX)
    spooled = [json.loads(p.read_text(encoding="utf-8")) for p in (bot.actions_out.root() / "new").glob("*.json")]
    assert [(s["action"], s["blog"], s["args"], s["target"]) for s in spooled] == \
        [(ablyrank.ACTION, None, {"captures": [k]}, "ably-rank:" + k[len(ablyrank.PREFIX):])]
    fx = fixture("action_ably_rank_capture.json")
    assert fx["action"] == spooled[0]["action"] and set(fx["args"]) == set(spooled[0]["args"]) and fx["origin"]["session"] == "bookmarklet"
    assert fx["args"]["captures"][0].startswith(ablyrank.PREFIX) and row["action"] == "blog." + ablyrank.ACTION
