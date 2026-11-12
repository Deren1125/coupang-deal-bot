"""'통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음 · 주인 아이패드 사파리 '내 통계'에서 누름).

1) statscap.bookmarklet(): static/statscap.js 를 줄여 주소·열쇠를 넣은 "javascript:" 주소 — node 로 읽힘 · 가짜 통계 화면에서 보내는 폼
   (보이는 글 · 메뉴 줄 뺌 · 표 줄 · 고른 탭 · blogId) → 서버 save 까지 이어서.
2) save(): 열쇠 · 네이버 주소만 · 태그·스크립트 빼기 · 이메일·전화 가리기 · 크기 제한 · 블로그·화면 종류·날짜 짐작 · 30일 지우기 · 하루 개수.
3) GET /dashboard/api/statscap (로그인 아래) · POST /dashboard/api/stats_capture (로그인 없이 · 교차 사이트 · CORS 는 네이버만 · 폼이면 HTML) ·
   할 일 탭 '오늘 통계 보내기' (오늘 보낸 것이 있으면 사라짐).
4) 11/11 밤 실사용에서 '저장 중 잠시만 기다려 주세요.' 덮개 글만 옴 → 덮개 사라질 때까지 기다림 · 덮개·안 보이는 요소 뺌 ·
   ROOT 가 덮개면 body 전체(메뉴 뺌) · div 표([role=row]) 도 읽음.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import routine, statscap

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
TZ = ZoneInfo("Asia/Seoul")
NOW = 1_794_400_000.0  # 2026-11-11 KST 낮


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


def form(db: FakeDb, **kw: str) -> dict[str, str]:
    base = {"k": statscap.key(db), "u": "https://blog.stat.naver.com/blog/inflow?blogId=derenpick", "t": "블로그 통계",
            "s": "유입분석", "x": "2026.11.10. 유입 경로\n네이버 검색 120\n구글 8", "r": "[]", "b": "", "f": ""}
    return {**base, **kw}


# ---------------------------------------------------------------- 1) 책갈피
def code(db: FakeDb) -> str:
    c = statscap.view(db, "https://dash.example")["code"]
    assert c.startswith("javascript:")
    return unquote(c[len("javascript:"):])


def test_bookmarklet_has_endpoint_and_key_and_fits_safari() -> None:
    db = FakeDb()
    js = code(db)
    assert "'https://dash.example/dashboard/api/stats_capture'" in js and f"'{statscap.key(db)}'" in js and "@@" not in js
    assert "/*" not in js and "\n//" not in js and len(statscap.view(db, "https://dash.example")["code"]) < 12000
    assert js.count("fetch(") == 1 and "credentials:'omit'" in js and "XMLHttpRequest" not in js and "cookie" not in js
    assert len(statscap.SENDING_STEPS) == 4
    with pytest.raises(ValueError):
        statscap.bookmarklet("https://x.example/a'b", "k")


FAKE = r"""
const [code] = process.argv.slice(1), out = {alerts: []};
const el = (t, kids) => ({innerText: t, contains: x => (kids || []).includes(x)});
const nav = el('블로그 홈\n내 통계\n유입분석'), root = el('2026.11.10. 유입 경로\n유입분석\n네이버 검색  120\n네이버 검색  120\n문의 a.b@ex.com 010-1234-5678'), tab = el('유입분석');
const cell = t => ({innerText: t});
const table = {rows: [{cells: [cell('유입 경로'), cell('조회수')]}, {cells: [cell('네이버 검색'), cell('120')]}, {cells: [cell(''), cell('')]}]};
const doc = {title: '블로그 통계', location: {href: 'https://blog.stat.naver.com/blog/inflow?blogId=derenpick'},
  body: {querySelectorAll: s => s.startsWith('header') ? [nav] : []},
  querySelectorAll: s => s === 'iframe,frame' ? [] : s.startsWith('main') ? [root] : s === 'table' ? [table] : s.startsWith('[aria-selected') ? [tab] :
    s.startsWith('a[href') ? [{href: 'https://blog.naver.com/derenpick'}] : []};
global.window = {}; global.document = doc; global.location = {hostname: 'blog.stat.naver.com', href: doc.location.href};
global.alert = m => out.alerts.push(m); global.confirm = () => false;
global.fetch = async (u, o) => { out.url = u; out.mode = o.mode; out.cred = o.credentials; out.body = o.body.toString();
  return {json: async () => ({ok: true, label: '데런픽 · 유입 분석 · 11/10'})}; };
(async () => { await eval(code); await new Promise(r => setTimeout(r, 20));
  global.location.hostname = 'example.com'; await eval(code); await new Promise(r => setTimeout(r, 20));
  console.log(JSON.stringify(out)); })();
"""


@needs_node
def test_bookmarklet_on_fake_stats_page_sends_text_rows_and_server_saves() -> None:
    db = FakeDb()
    js = code(db)
    assert subprocess.run([NODE, "--check", str(STATIC / "statscap.js")], capture_output=True).returncode == 0
    res = subprocess.run([NODE, "-e", FAKE, js], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["url"] == "https://dash.example/dashboard/api/stats_capture" and got["mode"] == "cors" and got["cred"] == "omit"
    assert got["alerts"] == ["통계 보냄 ✓ (데런픽 · 유입 분석 · 11/10)", "통계 보내기: 네이버 블로그 내 통계 화면에서 눌러 주세요"]
    sent = dict(parse_qsl(got["body"], keep_blank_values=True))
    assert sent["k"] == statscap.key(db) and sent["s"] == "유입분석" and "derenpick" in sent["b"]
    assert sent["x"].splitlines()[:2] == ["2026.11.10. 유입 경로", "네이버 검색 120"]  # 메뉴 줄('유입분석') · 같은 줄 반복 뺌
    assert json.loads(sent["r"]) == [[["유입 경로", "조회수"], ["네이버 검색", "120"]]]
    out = statscap.save(db, sent, now=NOW, tz=TZ)
    assert out == {"ok": True, "label": "데런픽 · 유입 분석 · 11/10", "kind": "유입 분석", "blog": "issue", "day": "2026-11-10"}
    rec = json.loads(next(v for k, v in db.kv.items() if k.startswith(statscap.PREFIX)))
    assert "a.b@ex.com" not in rec["text"] and "010-1234-5678" not in rec["text"] and "[이메일]" in rec["text"] and "[전화]" in rec["text"]


LOADING = r"""
const [code] = process.argv.slice(1), out = {alerts: [], waited: 0};
let polls = 0;
const LOAD = '저장 중\n잠시만 기다려 주세요.';
const el = (t, kids, o) => ({innerText: t, contains: x => (kids || []).includes(x), ...(o || {})});
const hidden = {getClientRects: () => []};
const nav = el('블로그 홈\n내 통계\n조회수 순위');
// ROOT 로 고른 #container 가 덮개(글이 덮개뿐) · body 는 처음 두 번 덮개만, 그 뒤 통계 · 덮개 든 표 줄 · 숨은 표·줄
const layer = el(LOAD);
const real = '2026.11.10. 조회수 순위\n' + Array.from({length: 12}, (_, i) => (i + 1) + ' 겨울 패딩 고르는 법 ' + (i + 1) + '편 조회수 ' + (300 - i * 7)).join('\n');
const body = {querySelectorAll: s => s.startsWith('header') ? [nav] : [],
  get innerText() { polls++; return polls < 3 ? '블로그 홈\n내 통계\n조회수 순위\n' + LOAD : '블로그 홈\n내 통계\n조회수 순위\n' + real; }};
const cell = (t, o) => ({innerText: t, ...(o || {})});
const loadingTable = {rows: [{cells: [cell('-'), cell('저장 중잠시만 기다려 주세요.'), cell('-')]}]};
const hiddenTable = {rows: [{cells: [cell('숨은'), cell('999')]}], ...hidden};
const row = cs => ({closest: s => s === 'table' ? null : grid, querySelectorAll: () => cs.map(t => cell(t))});
const grid = {};
const gridRows = [row(['순위', '제목', '조회수']), row(['1', '겨울 패딩 고르는 법 1편', '300']), row(['2', '저장 중', '-']),
  {...row(['9', '숨은 줄', '1']), ...hidden}];
const doc = {title: '블로그 통계', location: {href: 'https://admin.blog.naver.com/derenpick/stat/rank_pv'}, body,
  querySelectorAll: s => s === 'iframe,frame' ? [] : s.startsWith('main') ? [el('', [], hidden), layer] : s === 'table' ? [loadingTable, hiddenTable] :
    s === '[role=row]' ? gridRows : []};
global.window = {}; global.document = doc; global.location = {hostname: 'admin.blog.naver.com', href: doc.location.href};
global.alert = m => out.alerts.push(m); global.confirm = () => false;
global.fetch = async (u, o) => { out.body = o.body.toString(); return {json: async () => ({ok: true, label: '데런픽 · 조회수 순위 · 11/10'})}; };
(async () => { const t = Date.now(); await eval(code); out.waited = Date.now() - t; out.polls = polls; console.log(JSON.stringify(out)); })();
"""


@needs_node
def test_bookmarklet_waits_out_loading_overlay_and_reads_body_and_div_grid() -> None:
    db = FakeDb()
    res = subprocess.run([NODE, "-e", LOADING, code(db)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["alerts"] == ["통계 보냄 ✓ (데런픽 · 조회수 순위 · 11/10)"]
    assert 900 <= got["waited"] < 5000  # 덮개가 보이는 동안 0.5초씩 기다림 (끝없이는 아님)
    sent = dict(parse_qsl(got["body"], keep_blank_values=True))
    lines = sent["x"].splitlines()
    assert "저장 중" not in sent["x"] and "잠시만 기다려" not in sent["x"] and "블로그 홈" not in lines  # 덮개 · 메뉴 뺌
    assert lines[0] == "2026.11.10. 조회수 순위" and "1 겨울 패딩 고르는 법 1편 조회수 300" in lines and len(lines) == 13
    assert json.loads(sent["r"]) == [[["순위", "제목", "조회수"], ["1", "겨울 패딩 고르는 법 1편", "300"]]]  # 덮개 줄 · 숨은 표·줄 뺌
    out = statscap.save(db, sent, now=NOW, tz=TZ)
    assert out["kind"] == "조회수 순위" and out["blog"] == "issue" and out["day"] == "2026-11-10"


def test_bookmarklet_source_keeps_one_statement_per_line_rules() -> None:
    src = (STATIC / "statscap.js").read_text(encoding="utf-8")
    body = src.split("*/", 1)[1]
    for ln in body.splitlines():
        s = ln.strip()
        assert "//" not in s or s.startswith("//") or "://" in s or "\\/" in s, ln  # // 주석은 줄 맨 앞에만
    for rx in __import__("re").findall(r"(?<![\w)])/(?![/*])((?:\\.|[^/\n])+)/[gimsuy]*[.;),]", body):
        assert " " not in rx and "'" not in rx and '"' not in rx, rx  # 정규식 안에 공백·따옴표 없음


# ---------------------------------------------------------------- 2) 받기
def test_save_checks_key_where_and_cleans() -> None:
    db = FakeDb()
    for bad, code_ in ((form(db, k="nope"), "key"), (form(db, k=""), "key"), (form(db, u="https://evil.example/naver.com"), "where"),
                       (form(db, x="", r="[]"), "empty")):
        with pytest.raises(statscap.CaptureError) as e:
            statscap.save(db, bad, now=NOW, tz=TZ)
        assert e.value.code == code_
    assert not [k for k in db.kv if k.startswith(statscap.PREFIX)]
    x = "<script>alert(1)</script><b onclick=x()>굵게</b> javascript:void(0) 02-123-4567 +82 10 1234 5678 숫자 1,234 2026.11.10\n" + "가" * 40000
    statscap.save(db, form(db, x=x, r=json.dumps([[["<i>a</i>"] * 30] * 500])), now=NOW, tz=TZ)
    rec = json.loads(next(v for k, v in db.kv.items() if k.startswith(statscap.PREFIX)))
    t = rec["text"]
    assert "<" not in t and "script>" not in t and "onclick" not in t and "javascript:" not in t
    assert "02-123-4567" not in t and "1234 5678" not in t and "1,234" in t and "2026.11.10" in t
    assert len(t.encode()) <= statscap.TEXT_MAX
    assert sum(len(tb) for tb in rec["rows"]) == statscap.ROWS_MAX and len(rec["rows"][0][0]) == statscap.CELLS_MAX and rec["rows"][0][0][0] == "a"


def test_guesses_blog_kind_and_date() -> None:
    assert statscap.guess_blog("https://m.blog.naver.com/stat/today?blogId=Demiyum", "", "", "") == ("hotdeal", "demiyum")
    assert statscap.guess_blog("https://blog.stat.naver.com/x", "", "someone,derenlog", "") == ("finance", "derenlog")
    assert statscap.guess_blog("https://blog.stat.naver.com/x", "", "", "blog.naver.com/derenpick 통계") == ("issue", "derenpick")
    assert statscap.guess_blog("https://blog.stat.naver.com/x?blogId=other", "", "", "") == (None, "other")
    assert statscap.guess_kind("https://blog.stat.naver.com/m/rank/pv", "", "", "") == "조회수 순위"
    assert statscap.guess_kind("https://blog.stat.naver.com/a", "검색어", "", "") == "검색어"
    assert statscap.guess_kind("https://blog.stat.naver.com/a", "", "", "평균 사용 시간 3분") == "평균 사용 시간"
    assert statscap.guess_kind("https://blog.stat.naver.com/a", "", "", "x") == "통계"
    assert statscap.guess_date("", "", "기간 2026.11.09. ~", "2026-11-11") == "2026-11-09"
    assert statscap.guess_date("https://x/?date=20261108", "", "", "2026-11-11") == "2026-11-08"
    assert statscap.guess_date("", "", "1999.01.01 그리고 2026년 13월 40일", "2026-11-11") == "2026-11-11"


def test_keeps_30_days_and_caps_a_day_and_nudges() -> None:
    db = FakeDb()
    db.kv[statscap.PREFIX + "2026-10-01:120000-aaaa"] = "{}"
    db.kv[statscap.PREFIX + "2026-10-20:120000-aaaa"] = "{}"
    assert statscap.nudge(db, NOW, TZ) and statscap.nudge(db, NOW, TZ).startswith("오늘 통계 보내기")
    statscap.save(db, form(db), now=NOW, tz=TZ)
    assert statscap.PREFIX + "2026-10-01:120000-aaaa" not in db.kv and statscap.PREFIX + "2026-10-20:120000-aaaa" in db.kv
    assert statscap.nudge(db, NOW, TZ) is None and statscap.nudge(db, NOW + 86400, TZ) is not None
    for i in range(statscap.DAY_MAX - 1):
        statscap.save(db, form(db), now=NOW + i, tz=TZ)
    with pytest.raises(statscap.CaptureError) as e:
        statscap.save(db, form(db), now=NOW + 100, tz=TZ)
    assert e.value.status == 429


def test_key_changes_when_secret_is_dropped() -> None:
    db = FakeDb()
    k1 = statscap.key(db)
    assert statscap.key_ok(db, k1) and not statscap.key_ok(db, k1[:-1] + "x")
    del db.kv[statscap.KV_SECRET]
    assert not statscap.key_ok(db, k1)


# ---------------------------------------------------------------- 3) 대시보드
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
    return await bot.web.dispatch("POST", statscap.PATH, h, raw)


async def test_api_code_needs_login_and_capture_takes_key_only(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/statscap", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/statscap", session_headers(bot))
    assert status == 200 and v["steps"] == list(statscap.SENDING_STEPS) and statscap.key(bot.db) in unquote(v["code"])
    good = {"k": statscap.key(bot.db), "u": "https://blog.stat.naver.com/a?blogId=derenpick", "t": "통계", "s": "조회수 순위",
            "x": "조회수 순위\n1 글 30", "r": "[]"}
    res = await _post(bot, good, origin="https://blog.stat.naver.com")
    assert res[0] == 200 and json.loads(res[1])["label"].startswith("데런픽 · 조회수 순위 · ")
    assert res[3]["Access-Control-Allow-Origin"] == "https://blog.stat.naver.com"
    res = await _post(bot, {**good, "k": "wrong"}, origin="https://m.blog.naver.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key" and res[3]["Access-Control-Allow-Origin"] == "https://m.blog.naver.com"
    res = await _post(bot, good, origin="https://evil.example")
    assert res[0] == 200 and "Access-Control-Allow-Origin" not in res[3]  # 저장은 열쇠로 · 남의 화면은 답을 못 읽음
    res = await _post(bot, {**good, "via": "form"}, origin="null")
    assert res[0] == 200 and res[2].startswith("text/html") and "통계 보냄 ✓ (데런픽 · 조회수 순위" in res[1]
    assert (await bot.web.dispatch("GET", statscap.PATH, anon))[0] == 405
    assert routine.view(bot)["stats"] is None  # 오늘 보냈으니 할 일 줄 없음


async def test_routine_has_stats_line_until_today_capture(bot: DealBot) -> None:
    assert routine.view(bot)["stats"].startswith("오늘 통계 보내기")


# ---------------------------------------------------------------- 4) 진짜 크롬 (playwright 가 있을 때만)
# 11/12 갤럭시 크롬 today·referer·rank_pv: 왼쪽 메뉴 글만 옴 — 통계는 다른 출처 iframe 안 (여기선 다른 포트로 흉내)
MENU_HTML = ("<div class='skip'><a href='#lnb'>메뉴 바로가기</a><a href='#c'>본문 바로가기</a></div><div id='lnb'><ul>"
             + "".join(f"<li><a href='#'>{m}</a></li>" for m in ("오늘", "일간 현황", "방문 분석", "조회수", "순방문자수", "방문 횟수",
                                                               "유입 분석", "조회수 순위", "블로그 스마트봇"))
             + "</ul></div>")
TOP = ("<!doctype html><html><head><title>블로그 통계</title></head><body>" + MENU_HTML
       + "<div id='c'><iframe src='https://admin.blog.naver.com:8443/stat/today?blogId=derenpick' style='width:600px;height:400px'></iframe>"
       "<iframe src='https://ad.example/banner' style='width:10px;height:10px'></iframe></div></body></html>")
INNER_HTML = ("<!doctype html><html><body><h2>2026.11.12. 일간 현황</h2><table><tr><th>지표</th><th>값</th></tr>"
              "<tr><td>조회수</td><td>1,234</td></tr><tr><td>순방문자수</td><td>567</td></tr></table></body></html>")
CHART = ("<!doctype html><html><head><title>블로그 통계</title></head><body>" + MENU_HTML
         + "<div id='c'><svg width='300' height='100'><title>11.12 조회수 그래프</title><text x='5' y='20'>11.11 98</text>"
         "<text x='5' y='40'>11.12 152</text></svg><div role='img' aria-label='순방문자수 11.12 87명' style='width:50px;height:50px'></div>"
         "<span id='sh'></span></div><script>document.getElementById('sh').attachShadow({mode:'open'}).innerHTML='<p>평균 사용 시간 2분 31초</p>';"
         "</script></body></html>")
PW = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, top, inner, chart] = process.argv.slice(1), out = {dialogs: [], bodies: [], urls: []};
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.route('**/*', r => {
    const u = r.request().url();
    if (u.startsWith('https://dash.example/')) { out.bodies.push(r.request().postData() || '');
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': '*'}, body: JSON.stringify({ok: true, label: '데런픽 · 일간 현황 · 11/12'})}); }
    if (u.startsWith('https://admin.blog.naver.com:8443/')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: inner});
    if (u.startsWith('https://admin.blog.naver.com/derenpick/stat/visit')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: chart});
    if (u.startsWith('https://admin.blog.naver.com/')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: top});
    return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: '<p>광고</p>'});
  });
  p.on('dialog', async d => { out.dialogs.push(d.type() + ':' + d.message()); await d.accept(); });
  const run = async () => { const n = out.dialogs.length; p.evaluate(code).catch(() => {}); for (let i = 0; i < 100 && out.dialogs.length === n; i++) await p.waitForTimeout(100); };
  await p.goto('https://admin.blog.naver.com/derenpick/stat/today');
  await run();
  await p.waitForURL('https://admin.blog.naver.com:8443/**');
  out.urls.push(p.url());
  await run();
  await p.goto('https://admin.blog.naver.com/derenpick/stat/visit');
  await run();
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def test_bookmarklet_in_chrome_sends_iframe_src_opens_it_and_reads_charts() -> None:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    res = subprocess.run([NODE, "-e", PW, code(db), TOP, INNER_HTML, CHART], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    d = got["dialogs"]
    assert len(d) == 3 and d[0].startswith("confirm:통계 보냄 ✓") and "통계 화면만 따로 열까요? 열린 화면에서 책갈피를 한 번 더 눌러 주세요" in d[0]
    assert d[1] == "alert:통계 보냄 ✓ (데런픽 · 일간 현황 · 11/12)" and d[2].startswith("alert:통계 보냄 ✓")
    assert got["urls"] == ["https://admin.blog.naver.com:8443/stat/today?blogId=derenpick"]  # OK → 그 화면만 열림
    first, second, third = (dict(parse_qsl(x, keep_blank_values=True)) for x in got["bodies"])
    # 1) 메뉴뿐인 바깥 화면: 메뉴 줄은 빠지고 iframe 주소(다른 출처 · 광고도)는 f 에
    for m in ("메뉴 바로가기", "본문 바로가기", "오늘", "일간 현황", "조회수", "순방문자수", "블로그 스마트봇"):
        assert m not in first["x"].splitlines(), first["x"]
    assert first["f"].splitlines() == ["https://admin.blog.naver.com:8443/stat/today?blogId=derenpick", "https://ad.example/banner"]
    out = statscap.save(db, first, now=NOW, tz=TZ)
    assert out["blog"] == "issue" and out["kind"] == "일간 현황"
    rec = json.loads(next(v for k, v in db.kv.items() if k.startswith(statscap.PREFIX)))
    assert "admin.blog.naver.com:8443/stat/today" in rec["frames"]
    # 2) 따로 연 안쪽 화면: 같은 출처라 표가 읽힘
    assert json.loads(second["r"]) == [[["지표", "값"], ["조회수", "1,234"], ["순방문자수", "567"]]]
    assert statscap.save(db, second, now=NOW + 1, tz=TZ)["day"] == "2026-11-12"
    # 3) 차트 화면: svg 글 · aria-label · shadow root 글 (iframe 이 없으니 묻지 않음)
    x = third["x"].splitlines()
    assert any(ln.startswith("차트: ") and "11.12 152" in ln and "11.11 98" in ln for ln in x), x
    assert "순방문자수 11.12 87명" in x and "평균 사용 시간 2분 31초" in x and "블로그 스마트봇" not in x and "방문 분석" not in x
    assert statscap.save(db, third, now=NOW + 2, tz=TZ)["ok"]


def test_save_keeps_menu_only_capture_with_frames() -> None:
    db = FakeDb()
    out = statscap.save(db, form(db, x="", r="[]", f="https://admin.blog.naver.com:8443/stat/today"), now=NOW, tz=TZ)
    assert out["ok"]
