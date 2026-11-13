"""대시보드 안 키자드 칸 (주인 11/12 '키자드 도구를 대시보드 화면으로') — static/kz.js.

1) 파일: 바깥 주소·innerHTML 없음 · 부르는 곳은 /dashboard/keyzard/api/… 뿐 · 스스로 POST 하지 않음(단추 act() 안에서만) · 크기 예산 · 셸·앱 파일 등록.
2) 진짜 크롬 (node playwright 가 있을 때만): 가짜 키자드 API 로 블로그 탭(데런픽 = b2 — 도구의 blog_home 으로 맞춤)·연결 탭·SNS 룩북을
   그린다 — 콘솔 오류 없음 · '[object'·'undefined'·'NaN' 없음 · 폰 너비 안 넘침 · 단추를 누를 때만 POST · 오류는 짧은 한국어 · app.js 처럼
   #main 을 다시 그려도 칸이 다시 붙음 · 키자드 도구가 꺼져도(502) 탭이 깨지지 않음.
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dealbot.dashboard import api as dash_api
from dealbot.dashboard import shell

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
# 11/12: 블로그 '키자드' 칸 + 연결 탭 '키자드 로그인' + 룩북 수집·코디 레시피·코디 트렌드 (값은 열 때만 키자드 도구에서)
KZ_JS_MAX = 20 * 1024
KZ_JS_GZIP_MAX = 7 * 1024 + 512


def test_kz_js_local_safe_and_wired() -> None:
    raw = (STATIC / "kz.js").read_bytes()
    js = raw.decode("utf-8")
    assert len(raw) <= KZ_JS_MAX and len(gzip.compress(raw, 9)) <= KZ_JS_GZIP_MAX
    for bad in ("http://", "https://", "innerHTML", "eval(", "new Function", "XMLHttpRequest", "sendBeacon", "setInterval"):
        assert bad not in js, bad
    assert js.count("fetch(") == 1 and "fetch(P + 'api/' + path" in js and "const D = document, W = window, P = '/dashboard/keyzard/'" in js
    # POST 는 kz(path, body) — body 를 주는 곳은 act() 하나뿐, act 는 단추 onclick 에서만
    assert len(re.findall(r"kz\('[^']*', ", js)) == 0 and js.count("await kz(path, body || {})") == 1
    calls = [m.start() for m in re.finditer(r"\bact\(", js) if not js[:m.start()].endswith("function ")]
    assert calls and all(js[i - 6:i] == "() => " for i in calls)
    for word in ("'키자드에 등록'", "오늘 대기 글 모두 등록", "RSS 등록", "'예전 화면'", "'키자드 로그인'", "'원격 로그인'", "'룩북 수집'", "'코디 레시피'",
                 "'코디 트렌드'", "'지금 수집'", "'지금 모으기'", "derenpick: 'issue'", "blog_home", "W.SBKz"):
        assert word in js, word
    html = shell.page({"me": {}})
    assert re.search(r'<script defer src="/dashboard/static/kz\.js\?v=[0-9a-f]{10}"></script>', html)
    assert "kz.js" in dash_api.APP_FILES
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "kz.js")], capture_output=True).returncode == 0


STATUS = {
    "ready": True, "login_blog": "", "login_name": "", "task": None, "rss_task": "__rss__", "settings": {},
    "blogs": [
        {"id": "b1", "name": "아카이브", "active": True, "need_login": False, "message": "", "last_run": "11-12 09:30", "running": False,
         "used_today": 0, "remaining_today": 5, "daily_limit": 5, "logged_in_before": True, "recent": [], "rss_used_this_week": True,
         "waiting": [], "blog_home": "https://blog.naver.com/derenlog", "log": [], "settings": {}},
        {"id": "b2", "name": "데런픽", "active": True, "need_login": False, "message": "", "last_run": "11-12 09:31", "running": False,
         "used_today": 2, "remaining_today": 3, "daily_limit": 5, "logged_in_before": True, "recent": [], "rss_used_this_week": False,
         "waiting": [{"link": "https://blog.naver.com/derenpick/224000000001", "title": "겨울 니트 코디 다섯 가지 — 아주 긴 제목이 폰에서 어떻게 보이는지", "status": "pending"},
                     {"link": "https://blog.naver.com/derenpick/224000000002", "title": "", "status": "failed"}],
         "blog_home": "https://blog.naver.com/derenpick", "log": [], "settings": {}},
        {"id": "b3", "name": "오늘의 핫딜", "active": True, "need_login": True, "message": "로그인이 풀렸습니다", "last_run": "", "running": False,
         "used_today": 0, "remaining_today": 5, "daily_limit": 5, "logged_in_before": False, "recent": [], "rss_used_this_week": False,
         "waiting": [], "blog_home": "https://blog.naver.com/demiyum", "log": [], "settings": {}},
    ],
}
LOOK = {"sources": [{"id": "s1", "name": "29CM 룩북", "url": "https://www.29cm.co.kr/x", "every_hours": 24, "looks": 10, "products": 0,
                     "collected_at": "2026-11-12T02:24:00", "last_note": ""},
                    {"id": "s2", "name": "베스트 랭킹", "url": "https://www.29cm.co.kr/y", "every_hours": 6, "looks": 0, "products": 40,
                     "collected_at": "", "last_run": "2026-11-12T03:00:00", "paused": "봇 확인 화면 (11-12 03:00)"}],
        "status": {"need_login": False, "message": "", "last_run": "", "running": False}, "remote_open": False}
RECIPES = {"recipes": [{"kind": "lookbook", "label": "브랜드 룩북 조합", "title": "데이트 룩 1", "popularity": 1234,
                        "items": [{"role": "상의", "color": "아이보리", "name": "울 니트"}, {"role": "하의", "color": "블랙", "name": "롱스커트"}], "outfit_text": "x"},
                       {"kind": "popular_mix", "label": "인기 조합", "title": "", "popularity": 0, "items": [], "outfit_text": ""}]}
NAVER = {"has_keys": True, "every_hours": 12, "state": {"running": False, "last_error": "", "last_try": ""},
         "result": {"collected_at": "2026-11-12T08:00:00", "post_count": 120, "items": [{"word": "니트", "posts": 30, "change": 2}, {"word": "코트", "posts": 20, "change": "new"}],
                    "styles": [{"word": "꾸안꾸", "posts": 12, "change": -1}], "images": [], "posts": []}}

BROWSER = r"""
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [js, css, data, down] = [fs.readFileSync(process.argv[1], 'utf8'), fs.readFileSync(process.argv[2], 'utf8'), JSON.parse(process.argv[3]), process.argv[4] === '1'];
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 360, height: 900}});
  const errors = [], posts = [], out = {};
  p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  p.on('pageerror', e => errors.push(String(e)));
  p.on('dialog', d => d.accept());
  // app.js 흉내: #main 을 통째로 다시 그림 (블로그 탭은 맨 위 .seg)
  const html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<link rel="stylesheet" href="/dashboard/static/app.css"></head><body><main id="main"></main><div class="toast" id="toast" hidden></div>' +
    '<script>window.fakeRender = () => { const m = document.getElementById("main"), t = location.hash.split("/")[1];' +
    'const a = document.createElement("div"); a.className = t === "blog" ? "seg" : "top"; a.textContent = "앱 " + t;' +
    'const c = document.createElement("p"); c.className = "empty"; c.textContent = "앱 칸"; m.replaceChildren(a, c); };' +
    'addEventListener("hashchange", fakeRender);</script></body></html>';
  await p.route('http://dash.test/**', async r => {
    const u = new URL(r.request().url()), m = r.request().method();
    const json = (status, d) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(d)});
    if (u.pathname === '/dashboard/') return r.fulfill({contentType: 'text/html', body: html});
    if (u.pathname.endsWith('app.css')) return r.fulfill({contentType: 'text/css', body: css});
    if (u.pathname.endsWith('kz.js')) return r.fulfill({contentType: 'text/javascript', body: js});
    if (u.pathname.startsWith('/dashboard/keyzard/api/')) {
      const a = u.pathname.slice('/dashboard/keyzard/api/'.length);
      if (m === 'POST') posts.push([a, JSON.parse(r.request().postData() || '{}')]);
      if (down) return r.fulfill({status: 502, contentType: 'text/html', body: '<p>닫혔어요</p>'});
      if (a === 'status') return json(200, data.status);
      if (a === 'look') return json(200, data.look);
      if (a === 'look/recipes') return json(200, data.recipes);
      if (a === 'naver/result') return json(200, data.naver);
      if (a === 'kz/prepare/b2') return json(200, {ok: true, link: 'x', result: {status: 'ok', message: '등록되었습니다'}});
      if (a === 'kz/register_all/b2') return json(200, {results: [{status: 'ok'}, {status: 'limit', message: '5회 초과'}], done: 1, planned: 2});
      if (a === 'login/start/b3') return json(409, {detail: '지금 자동 작업 중입니다. 잠시 뒤에 다시 눌러 주세요'});
      if (a === 'look/run/s1') return json(200, {ok: '룩북 10개 · 상품 0개'});
      return json(404, {detail: '없음'});
    }
    return r.fulfill({status: 404, body: ''});
  });
  await p.goto('http://dash.test/dashboard/#/blog/issue');
  await p.evaluate(() => fakeRender());
  await p.addScriptTag({url: '/dashboard/static/kz.js'});
  const settle = () => p.waitForTimeout(400);
  await settle();
  const text = () => p.$eval('#main', e => e.innerText);
  out.blog = await text();
  out.blogOrder = await p.$$eval('#main > *', es => es.map(e => e.id || e.className));
  out.wide = [await p.evaluate(() => document.documentElement.scrollWidth)];
  out.postsBefore = posts.length;
  if (!down) {
    await p.click('#kzb .row .sm.ac');  // 첫 대기 글 '키자드에 등록'
    await p.waitForFunction(() => /등록됐어요/.test(document.querySelector('#kzb').innerText));
    await p.evaluate(() => fakeRender());  // 앱이 다시 그려도 칸·결과 줄이 그대로
    await settle();
    out.afterRender = await text();
    await p.click('text=오늘 대기 글 모두 등록');
    await p.waitForFunction(() => /멈춘 까닭/.test(document.querySelector('#kzb').innerText));
    out.all = await p.$eval('#kzb', e => e.innerText);
  }
  await p.evaluate(() => { location.hash = '#/connect'; });
  await settle();
  out.connect = await text();
  if (!down) {
    await p.click('#kzl .row:nth-child(3) .sm');  // 핫딜 '원격 로그인' → 409
    await p.waitForFunction(() => /자동 작업 중/.test(document.querySelector('#kzl').innerText));
    out.connect2 = await p.$eval('#kzl', e => e.innerText);
  }
  out.wide.push(await p.evaluate(() => document.documentElement.scrollWidth));
  await p.evaluate(() => { location.hash = '#/sns/lookbook'; });
  await settle();
  out.look = await text();
  out.lookLast = await p.$eval('#main', e => e.lastElementChild.id);
  if (!down) {
    await p.click('#kzk .row .sm');  // '지금 수집'
    await p.waitForFunction(() => /수집했어요/.test(document.querySelector('#kzk').innerText));
    out.look2 = await p.$eval('#kzk', e => e.innerText);
  }
  out.wide.push(await p.evaluate(() => document.documentElement.scrollWidth));
  out.btn = await p.$$eval('.kz button, .kz a.sm', es => Math.min(...es.filter(e => e.offsetParent).map(e => Math.round(e.getBoundingClientRect().height))));
  out.posts = posts; out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _run(down: bool) -> dict:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    data = {"status": STATUS, "look": LOOK, "recipes": RECIPES, "naver": NAVER}
    out = subprocess.run([shutil.which("node") or "node", "-e", BROWSER, str(STATIC / "kz.js"), str(STATIC / "app.css"),
                          json.dumps(data, ensure_ascii=False), "1" if down else "0"], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _clean(got: dict) -> None:
    assert got["errors"] == []
    for k, v in got.items():
        if isinstance(v, str):
            for bad in ("[object", "undefined", "NaN", "null"):
                assert bad not in v, (k, bad, v)
    assert max(got["wide"]) <= 360


def test_real_browser_three_places_and_buttons_only_on_tap() -> None:
    got = _run(down=False)
    _clean(got)
    # 블로그 탭: 데런픽(issue) = 도구 b2 (blog_home derenpick) · 고르기 칸 바로 아래
    assert got["blogOrder"][:2] == ["seg", "kzb"] and got["postsBefore"] == 0
    for word in ("키자드", "등록 대기 2개 · 오늘 남은 한도 3/5", "오늘 대기 글 모두 등록 (2개)", "RSS 등록 (주 1번", "겨울 니트 코디", "지난번 실패", "키자드에 등록", "예전 화면"):
        assert word in got["blog"], word
    assert "룩북" not in got["blog"]  # 블로그 화면에 '룩북' 글자 없음 (test_dash_sns 와 같은 원칙)
    assert "✅ 등록됐어요" in got["afterRender"] and "앱 칸" in got["afterRender"]
    assert "1/2개 등록됐어요" in got["all"] and "오늘 한도에 걸렸어요" in got["all"] and "5회 초과" in got["all"]
    for word in ("키자드 로그인", "드라마 키자드", "데런픽 키자드", "라커룸 키자드", "로그인 필요 — 로그인이 풀렸습니다", "원격 로그인"):
        assert word in got["connect"], word
    assert "지금 자동 작업 중입니다" in got["connect2"]
    for word in ("룩북 수집", "29CM 룩북", "11-12 02:24 · 룩북 10 · 상품 0 · 24시간마다", "멈춤: 봇 확인 화면", "지금 수집", "코디 레시피", "데이트 룩 1",
                 "상의 아이보리 울 니트 · 하의 블랙 롱스커트", "♥ 1,234", "인기 조합", "코디 트렌드", "니트 · 글 30개 ▲2", "코트 · 글 20개 NEW", "꾸안꾸 · 글 12개 ▼1",
                 "지금 모으기"):
        assert word in got["look"], word
    assert got["lookLast"] == "kzk" and "수집했어요: 룩북 10개" in got["look2"]
    assert got["btn"] >= 36
    assert [p[0] for p in got["posts"]] == ["kz/prepare/b2", "kz/register_all/b2", "login/start/b3", "look/run/s1"]
    assert got["posts"][0][1] == {"link": "https://blog.naver.com/derenpick/224000000001", "submit": True}


def test_real_browser_tool_down_shows_short_korean_and_keeps_tabs() -> None:
    got = _run(down=True)
    _clean(got)
    assert "키자드 도구가 응답하지 않아요" in got["blog"] and "앱 칸" in got["blog"] and "예전 화면" in got["blog"]
    assert "키자드 도구가 응답하지 않아요" in got["connect"] and "키자드 도구가 응답하지 않아요" in got["look"]
    assert got["posts"] == []
