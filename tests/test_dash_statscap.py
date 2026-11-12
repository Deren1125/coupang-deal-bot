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
    # 11/12 '한 번에 모두' 로 커짐 — 같은 방식(전부 %인코딩) 꾸미기 책갈피가 아이패드 사파리에서 25.9KB 돎
    assert "/*" not in js and "\n//" not in js and len(statscap.view(db, "https://dash.example")["code"]) < 20000
    # 대시보드엔 쿠키 없이 · 주인 로그인(include)은 화면이 읽은 같은 출처 JSON 다시 받기에만
    assert js.count("fetch(") == 2 and js.count("credentials:'omit'") == 1 and js.count("credentials:'include'") == 1
    assert "L.origin+'/')===0" in js and "XMLHttpRequest" not in js and "cookie" not in js
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
global.window = {}; global.document = doc; global.location = {hostname: 'm.blog.naver.com', href: doc.location.href};
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
    assert got["alerts"] == ["통계 보냄 ✓ (데런픽 · 유입 분석 · 11/10)", "통계 보내기: 네이버 내 통계에서 눌러 주세요"]
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
    real = "https://blog.stat.naver.com/blog/user/referer/total?blogId=derenpick&proxyReferer=https%3A%2F%2Fadmin.blog.naver.com%2Fderenpick%2Fstat%2Ftoday&isIE8=false"
    assert statscap.guess_blog(real, "", "", "") == ("issue", "derenpick")  # 11/12 통계 앱 주소 그대로
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
# 11/12 갤럭시 크롬: 왼쪽 메뉴 글만 옴 — 통계는 다른 출처 iframe 안 (여기선 다른 포트로 흉내) · 메뉴가 없는 화면은 그 iframe 하나만 옮겨서
MENU_HTML = ("<div class='skip'><a href='#lnb'>메뉴 바로가기</a><a href='#c'>본문 바로가기</a></div><div id='lnb'><ul>"
             + "".join(f"<li><a href='#'>{m}</a></li>" for m in ("오늘", "일간 현황", "방문 분석", "조회수", "순방문자수", "방문 횟수",
                                                               "유입 분석", "조회수 순위", "블로그 스마트봇"))
             + "</ul></div>")
TOP = ("<!doctype html><html><head><title>블로그 통계</title></head><body>" + MENU_HTML
       + "<div id='c'><iframe src='https://admin.blog.naver.com:8443/stat/today?blogId=derenpick&isIE8=false' style='width:600px;height:400px'></iframe>"
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
    if (u.startsWith('https://m.blog.naver.com/stat/visit')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: chart});
    if (u.startsWith('https://m.blog.naver.com/')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: top});
    return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: '<p>광고</p>'});
  });
  p.on('dialog', async d => { out.dialogs.push(d.type() + ':' + d.message()); await d.accept(); });
  const run = async () => { const n = out.dialogs.length; p.evaluate(code).catch(() => {}); for (let i = 0; i < 400 && out.dialogs.length === n; i++) await p.waitForTimeout(100); };
  await p.goto('https://m.blog.naver.com/derenpick/stats/today');
  await run();
  await p.waitForURL('https://admin.blog.naver.com:8443/**');
  out.urls.push(p.url());
  await run();
  await p.goto('https://m.blog.naver.com/stat/visit?blogId=derenpick');
  await run();
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _hash(url: str) -> dict:  # type: ignore[type-arg]
    import base64

    return json.loads(base64.b64decode(url.split("#sc=", 1)[1]).decode())


def test_bookmarklet_in_chrome_moves_to_inner_stats_frame_and_reads_charts() -> None:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    res = subprocess.run([NODE, "-e", PW, code(db), TOP, INNER_HTML, CHART], capture_output=True, text=True, timeout=120, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    d = got["dialogs"]
    # 메뉴가 '#' 링크뿐 · 숫자는 다른 출처 iframe 안 → 그 화면 하나를 목록으로 옮길지 물음 → 옮긴 화면에서 한 번 더 → 숨은 iframe 으로 읽음
    assert len(d) == 3 and d[0] == "confirm:1개 찾음 ✓\n통계 화면으로 옮겨서 모을까요? 열린 화면에서 책갈피를 한 번 더 눌러 주세요", d
    assert d[1].startswith("alert:통계 보냄 ✓ 1개 화면\n다음엔 이 화면을 열고 한 번") and d[2] == "alert:통계 보냄 ✓ (데런픽 · 일간 현황 · 11/12)"
    url = got["urls"][0]
    assert url.startswith("https://admin.blog.naver.com:8443/stat/today?blogId=derenpick&isIE8=false#sc=")
    assert _hash(url) == {"b": "derenpick", "l": [["블로그 통계", "/stat/today"]]}
    bodies = [dict(parse_qsl(x, keep_blank_values=True)) for x in got["bodies"]]
    assert len(bodies) == 4 and [b.get("n") for b in bodies] == [None, "주소 목록", "블로그 통계", None]
    first, lst, second, third = bodies
    # 1) 메뉴뿐인 화면: 메뉴 줄은 빠지고 iframe 주소(다른 출처 · 광고도)는 f 에
    for m in ("메뉴 바로가기", "본문 바로가기", "오늘", "일간 현황", "조회수", "순방문자수", "블로그 스마트봇"):
        assert m not in first["x"].splitlines(), first["x"]
    assert first["f"].splitlines() == ["https://admin.blog.naver.com:8443/stat/today?blogId=derenpick&isIE8=false", "https://ad.example/banner"]
    out = statscap.save(db, first, now=NOW, tz=TZ)
    assert out["blog"] == "issue" and out["kind"] == "일간 현황"
    assert lst["x"] == "블로그 통계 https://admin.blog.naver.com:8443/stat/today" and statscap.save(db, lst, now=NOW + 3, tz=TZ)["kind"] == "주소 목록"
    # 2) 옮긴 안쪽 화면: 같은 출처라 표가 읽힘
    assert json.loads(second["r"]) == [[["지표", "값"], ["조회수", "1,234"], ["순방문자수", "567"]]]
    assert second["u"] == "https://admin.blog.naver.com:8443/stat/today?blogId=derenpick"
    assert statscap.save(db, second, now=NOW + 1, tz=TZ)["day"] == "2026-11-12"
    # 3) 차트 화면: svg 글 · aria-label · shadow root 글 (iframe 이 없으니 묻지 않음)
    x = third["x"].splitlines()
    assert any(ln.startswith("차트: ") and "11.12 152" in ln and "11.11 98" in ln for ln in x), x
    assert "순방문자수 11.12 87명" in x and "평균 사용 시간 2분 31초" in x and "블로그 스마트봇" not in x and "방문 분석" not in x
    assert statscap.save(db, third, now=NOW + 2, tz=TZ)["ok"]  # 메뉴 링크가 없으니 이 화면 하나만


def test_save_takes_tab_name_as_kind_and_keeps_json() -> None:
    db = FakeDb()
    out = statscap.save(db, form(db, n="시간대 분석", j='https://admin.blog.naver.com/a.json\n{"h":[1,2]}'), now=NOW, tz=TZ)
    assert out["kind"] == "시간대 분석" and out["label"] == "데런픽 · 시간대 분석 · 11/10"
    rec = json.loads(next(v for k, v in db.kv.items() if k.startswith(statscap.PREFIX)))
    assert rec["kind"] == "시간대 분석" and rec["json"].endswith('{"h":[1,2]}')
    assert statscap.save(db, form(db, n="공감수 순위 " + "가" * 80), now=NOW + 1, tz=TZ)["kind"] == ("공감수 순위 " + "가" * 80)[:statscap.KIND_MAX]
    assert statscap.save(db, form(db), now=NOW + 2, tz=TZ)["kind"] == "유입 분석"  # n 없으면 예전처럼 짐작


def test_save_keeps_menu_only_capture_with_frames() -> None:
    db = FakeDb()
    out = statscap.save(db, form(db, x="", r="[]", f="https://admin.blog.naver.com:8443/stat/today"), now=NOW, tz=TZ)
    assert out["ok"]


# 11/12 15:24 주인 아이패드: 관리 화면(admin.blog.naver.com) 18개 메뉴 화면이 모두 겉 글('메뉴 바로가기본문 바로가기')뿐 —
# 숫자는 다른 출처 iframe(blog.stat.naver.com/blog/user/…) 안. 1단계 관리 화면에서 iframe src 목록 → 2단계 통계 앱에서 모음 → 다음부턴 한 번
A = "https://admin.blog.naver.com/derenpick/stat/"
S = "https://blog.stat.naver.com/blog/user/"
PR = "&proxyReferer=https%3A%2F%2Fadmin.blog.naver.com%2Fderenpick%2Fstat%2Ftoday&isIE8=false"
ADMIN_MENU = ("<div id='lnb'><ul>" + "".join(
    f"<li><a href='/derenpick/stat/{h}'>{m}</a></li>" for h, m in (
        ("today", "일간 현황"), ("referer", "유입분석"), ("rank_pv", "조회수 순위"), ("hour_cv", "시간대 분석"),
        ("country", "국가별 분포"), ("video", "재생수분석")))
    + "<li><a href='#'>지표 다운로드</a></li></ul></div>")
SHELL = ("<!doctype html><html><head><meta charset='utf-8'><title>블로그 통계</title></head><body><a href='#lnb'>메뉴 바로가기</a><a href='#c'>본문 바로가기</a>"
         + ADMIN_MENU + "<div id='c'>{}</div><iframe style='display:none' title='ie6에서 사용되는 팝업시 비활성화 프레임'></iframe></body></html>")
FR = "<iframe src='{}' style='width:900px;height:700px'></iframe>"
ADMIN = {
    # 일간 현황: iframe 을 0.5초 뒤 스크립트로 넣음 · 시간대 분석: 같은 출처 iframe 안에 한 겹 더
    A + "today": SHELL.format("<script>setTimeout(()=>{document.getElementById('c').innerHTML=\"" + FR.format(S + "today?blogId=derenpick" + PR).replace("'", "\\'") + "\";},500);</script>"),
    A + "referer": SHELL.format(FR.format(S + "referer/total?blogId=derenpick" + PR)),
    A + "rank_pv": SHELL.format(FR.format(S + "rank/pv?blogId=derenpick" + PR)),
    A + "hour_cv": SHELL.format(FR.format("/derenpick/stat/hour_wrap")),
    A + "hour_wrap": "<!doctype html><html><body>" + FR.format(S + "hour?blogId=derenpick" + PR) + "</body></html>",
}
STAT_MENU = ("<div id='lnb'><ul><li><a href='/blog/user/today?blogId=derenpick'>일간 현황</a></li>"
             "<li><a href='/blog/user/referer/total?blogId=derenpick'>유입분석</a></li><li><a href='/blog/user/video'>동영상</a></li></ul></div>")
SPAGE = "<!doctype html><html><head><meta charset='utf-8'><title>통계</title></head><body>" + STAT_MENU + "<div id='c'>{}</div>{}</body></html>"
DAYS = "".join(f"<text x='{i}' y='90'>day-{i}</text>" for i in range(1, 31))
# 일간 현황: 1초 뒤 숫자 · 안쪽 탭 · '이전' · billboard 값 · 값 없는 day-N 차트 글 · 창 전역에 날짜별 숫자 30개
TODAY = SPAGE.format(
    "<p id='d'>2026.11.12.</p><button class='btn_prev' aria-label='이전 날짜'>‹</button>"
    "<ul class='tab'><li><a href='#' role='tab' data-t='pv'>조회수</a></li><li><a href='#' data-t='cnt'>방문횟수</a></li>"
    "<li><a href='#' data-t='age'>성별,연령별 분포</a></li></ul><div id='box'>저장 중</div>"
    "<svg width='300' height='100'>" + DAYS + "</svg><div>" + "".join(f"<p>day-{i}</p>" for i in range(1, 31)) + "</div>",
    "<script>let day='11.12',t='pv';const draw=()=>{document.getElementById('d').textContent='2026.'+day+'.';"
    "document.getElementById('box').innerHTML=t==='pv'?'<table><tr><th>조회수</th><td>'+(day==='11.12'?22:180)+'</td></tr>"
    "<tr><th>공감수</th><td>5</td></tr></table>':t==='cnt'?'<p>방문횟수 '+day+' 31</p><p>평균 1.2</p><p>재방문 4</p>':'<p>여성 30대 41%</p><p>남성 40대 12%</p><p>여성 20대 9%</p>';};"
    "setTimeout(draw,1000);document.querySelectorAll('[data-t]').forEach(a=>a.onclick=e=>{e.preventDefault();t=a.dataset.t;draw();});"
    "document.querySelector('.btn_prev').onclick=()=>{day='11.11';draw();};"
    "window.bb={instance:[{data:()=>[{id:'조회수',values:[{x:new Date(2026,10,11),value:178},{x:new Date(2026,10,12),value:22}]}]}]};"
    "window.__stat={opt:{w:3},list:Array.from({length:30},(_,i)=>({date:'2026-10-'+(i+14),pv:i*3}))};"
    "window.hourly={'2026-11-01':5,'2026-11-02':6,'2026-11-03':7,'2026-11-04':8,'2026-11-05':9,'2026-11-06':1,'2026-11-07':2};</script>")
# 유입분석: 표가 안쪽 같은 출처 iframe 안 · 1초 뒤 뜸 · '더보기' 두 번 · '이전'
REFERER = SPAGE.format("<iframe src='/blog/user/referer/in' style='width:800px;height:600px'></iframe>", "")
REFERER_IN = (
    "<!doctype html><html><head><meta charset='utf-8'></head><body><p id='d'>2026.11.12.</p><button class='prev' title='이전'>‹</button>"
    "<table id='t'></table><button id='more'>더보기</button><script>let n=3,day='11.12';"
    "const R=[['네이버 통합검색_모바일',82.1],['블로그 홈',25],['검색',20],['통합검색 겨울 패딩',9],['통합검색 에이티즈 대상',7],['구글',3],['다음',2]];"
    "const draw=()=>{document.getElementById('d').textContent='2026.'+day+'.';document.getElementById('t').innerHTML='<tr><th>유입경로</th><th>비율</th></tr>'"
    "+R.slice(0,n).map(r=>'<tr><td>'+r[0]+'</td><td>'+(day==='11.12'?r[1]:r[1]+1)+'%</td></tr>').join('');};setTimeout(draw,1000);"
    "document.getElementById('more').onclick=()=>{n+=2;draw();if(n>=7)document.getElementById('more').remove();};"
    "document.querySelector('.prev').onclick=()=>{day='11.11';draw();};</script></body></html>")
# 조회수 순위: 화면이 같은 출처 JSON 을 읽어 온 뒤 div 표
RANK = SPAGE.format(
    "<div id='g'></div>",
    "<script>fetch('/blog/api/rank.json').then(r=>r.json()).then(()=>{document.getElementById('g').innerHTML=\"<div role='grid'><div role='row'>"
    "<span role='columnheader'>순위</span><span role='columnheader'>제목</span><span role='columnheader'>조회수</span></div>"
    "<div role='row'><span role='cell'>1</span><span role='cell'>에이티즈, 3년 연속 대상</span><span role='cell'>10</span></div></div>\";});</script>")
HOUR = SPAGE.format("<p>0시 12</p><p>1시 3</p><p>2시 1</p>", "")
STATS = {S + "today": TODAY, S + "referer/total": REFERER, S + "referer/in": REFERER_IN, S + "rank/pv": RANK, S + "hour": HOUR}
PW_ALL = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, pages] = process.argv.slice(1), P = JSON.parse(pages), out = {dialogs: [], bodies: [], urls: [], marks: [], took: []};
  const b = await chromium.launch();
  let p = null;
  const route = async r => {
    const u = r.request().url(), k = u.split('?')[0].split('#')[0];
    if (u.startsWith('https://dash.example/')) { out.bodies.push(r.request().postData() || '');
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': '*'}, body: JSON.stringify({ok: true, label: 'x'})}); }
    if (k.endsWith('/api/rank.json')) return r.fulfill({status: 200, contentType: 'application/json', body: '{"rank":[{"title":"에이티즈","pv":10}]}'});
    if (P[k]) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: P[k]});
    return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: '<p>다른 화면</p>'});
  };
  const fresh = async () => { const c = await b.newContext(); p = await c.newPage(); await p.route('**/*', route);
    p.on('dialog', async d => { out.dialogs.push(d.type() + ':' + d.message()); await d.accept(); }); };
  const run = async () => { out.marks.push(out.bodies.length); const n = out.dialogs.length, t = Date.now(); p.evaluate(code).catch(() => {});
    for (let i = 0; i < 1500 && out.dialogs.length === n; i++) await p.waitForTimeout(100); out.took.push(Date.now() - t);
    out.left = await p.evaluate(() => document.querySelectorAll('body > iframe').length + [...document.body.children].filter(e => e.style && e.style.zIndex === '2147483647').length); };
  await fresh();
  await p.goto('https://admin.blog.naver.com/derenpick/stat/today');
  await run();
  await p.waitForURL('https://blog.stat.naver.com/**');
  out.urls.push(p.url());
  await run();
  out.ls = await p.evaluate(() => localStorage.getItem('stcap'));
  await p.goto('https://blog.stat.naver.com/blog/user/hour?blogId=derenpick');
  await run();
  await fresh();
  await p.goto('https://blog.stat.naver.com/blog/user/hour?blogId=derenpick');
  await run();
  out.marks.push(out.bodies.length);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def test_bookmarklet_two_steps_admin_frame_srcs_then_stat_app_then_one_tap() -> None:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    db = FakeDb()
    res = subprocess.run([NODE, "-e", PW_ALL, code(db), json.dumps({**ADMIN, **STATS})], capture_output=True, text=True, timeout=400, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    d, m = got["dialogs"], got["marks"]
    sent = [dict(parse_qsl(x, keep_blank_values=True)) for x in got["bodies"]]
    step = [sent[m[i]:m[i + 1]] for i in range(4)]
    # 1단계: 관리 화면 메뉴 4개(국가별·재생수·다운로드 빼고 · 유입분석 먼저)의 안쪽 iframe src → 목록 하나 보내고 옮길지 물음
    assert d[0] == "confirm:4개 찾음 ✓\n통계 화면으로 옮겨서 모을까요? 열린 화면에서 책갈피를 한 번 더 눌러 주세요", d
    assert len(step[0]) == 1 and step[0][0]["n"] == "주소 목록" and step[0][0]["u"] == A + "today"
    assert step[0][0]["x"].splitlines() == ["유입분석 " + S + "referer/total", "일간 현황 " + S + "today", "시간대 분석 " + S + "hour", "조회수 순위 " + S + "rank/pv"]
    assert "proxyReferer" in step[0][0]["f"] and statscap.save(db, step[0][0], now=NOW, tz=TZ)["blog"] == "issue"
    url = got["urls"][0]
    assert url.startswith(S + "referer/total?blogId=derenpick" + PR + "#sc=")  # 처음 옮길 때는 iframe 이 쓰던 주소 그대로
    want = [["유입분석", "/blog/user/referer/total"], ["일간 현황", "/blog/user/today"], ["시간대 분석", "/blog/user/hour"], ["조회수 순위", "/blog/user/rank/pv"]]
    assert _hash(url) == {"b": "derenpick", "l": want} and len(url.split("#")[1]) < 400
    # 2단계: 통계 앱에서 목록대로 숨은 iframe · 안쪽 탭 · 전날 · 처음엔 '다음엔 즐겨찾기로' · 목록은 이 출처 localStorage 에
    names = [s["n"] for s in step[1]]
    assert names == ["유입분석", "유입분석 · 전날", "일간 현황", "일간 현황 · 방문횟수", "일간 현황 · 성별,연령별 분포", "일간 현황 · 전날",
                     "시간대 분석", "조회수 순위"], names
    assert d[1] == "alert:통계 보냄 ✓ 8개 화면\n다음엔 이 화면을 열고 한 번" and got["left"] == 0 and got["took"][1] < 90000
    assert json.loads(got["ls"]) == {"b": "derenpick", "l": want}
    by = dict(zip(names, step[1]))
    rows = json.loads(by["유입분석"]["r"])[0]
    assert rows[0] == ["유입경로", "비율"] and ["네이버 통합검색_모바일", "82.1%"] in rows and ["통합검색 에이티즈 대상", "7%"] in rows and len(rows) == 8
    assert "2026.11.11." in by["유입분석 · 전날"]["x"] and ["블로그 홈", "26%"] in json.loads(by["유입분석 · 전날"]["r"])[0]
    assert json.loads(by["일간 현황"]["r"]) == [[["조회수", "22"], ["공감수", "5"]]]
    tx = by["일간 현황"]["x"]
    assert "차트값 조회수: 11.11=178 11.12=22" in tx.splitlines() and "day-" not in tx  # 값 없는 day-N 글은 뺌
    j = by["일간 현황"]["j"]
    assert '__stat.list\n[{"date":"2026-10-14","pv":0}' in j and 'hourly\n{"2026-11-01":5' in j and "opt" not in j
    assert "방문횟수 11.12 31" in by["일간 현황 · 방문횟수"]["x"] and "2026.11.11." in by["일간 현황 · 전날"]["x"]
    assert json.loads(by["조회수 순위"]["r"])[0][1] == ["1", "에이티즈, 3년 연속 대상", "10"]
    assert by["조회수 순위"]["j"] == 'https://blog.stat.naver.com/blog/api/rank.json\n{"rank":[{"title":"에이티즈","pv":10}]}'
    assert "0시 12" in by["시간대 분석"]["x"].splitlines()
    for s in step[1]:
        assert s["k"] == statscap.key(db) and s["u"].startswith(S) and "blogId=derenpick" in s["u"] and "동영상" not in s["x"]
    for i, s in enumerate(step[1]):
        out = statscap.save(db, s, now=NOW + 1 + i, tz=TZ)
        assert out["kind"] == s["n"] and out["blog"] == "issue"
    recs = [json.loads(v) for k, v in sorted(db.kv.items()) if k.startswith(statscap.PREFIX)]
    assert len(recs) == 9 and recs[1]["blog_id"] == "derenpick" and any(r.get("json") for r in recs)
    # 다음 날: 통계 화면을 바로 열고 한 번 → 저장한 목록 그대로 (알림에 '다음엔' 없음)
    assert [s["n"] for s in step[2]] == names and d[2] == "alert:통계 보냄 ✓ 8개 화면"
    # 목록이 없는 브라우저: 통계 앱 메뉴(동영상 빼고)로
    assert [s["n"] for s in step[3]][:2] == ["일간 현황", "일간 현황 · 방문횟수"] and "유입분석" in [s["n"] for s in step[3]]
    assert len(d) == 4
