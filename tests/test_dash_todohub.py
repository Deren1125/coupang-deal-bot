"""할 일 종류 목록 (주인 11/14 — '할 일 첫 화면은 종류별 목록만, 누르면 그 종류 할 일만'). static/todohub.js.

- #/todo = 종류 목록: 폰은 세로 줄(할 일 있는 종류 먼저 · 없는 종류는 흐리게 맨 아래), 600px 넘는 화면은 블로그 탭 같은 가로 단추
- #/todo/<종류> = 그 종류 칸만 (routine.js part() 의 data-k) + '‹ 할 일' · 이벤트는 #/todo/ev (ssalmuk.js 화면)
- 뒤로 가기·밀어서 뒤로·아래 '할 일' 탭은 목록으로 · 새로고침해도 그 종류 · 모르는 종류는 목록으로
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import routine, webapp
from test_dash_routine import item, tz_now, with_routine, SLOTS
from test_dash_ssalmuk import sample as ev_sample

STATIC = webapp.STATIC_DIR
NODE = shutil.which("node")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버에서 돌려도 실제 threads-auto(127.0.0.1:8710) 목록이 섞이지 않게"""
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)


def test_todohub_js_local_safe_and_ordered() -> None:
    js = (STATIC / "todohub.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js and "new Function" not in js
    assert "fetch(" not in js  # 서버를 부르지 않음 — 다른 칸이 받아 둔 수만 읽음
    # 블로그 셋 → 딜 → SNS → 숏폼 → 이벤트 → 기타 (주인 11/14)
    order = [js.index(f"['{k}', '{n}'") for k, n in (("issue", "데런픽"), ("finance", "드라마"), ("hotdeal", "라커룸"), ("deals", "딜"),
                                                    ("sns", "SNS"), ("shorts", "숏폼"), ("ev", "이벤트"), ("etc", "기타"))]
    assert order == sorted(order)
    assert "try { return W.localStorage.getItem('todo-cat')" in js and "try { W.localStorage.setItem('todo-cat', v); } catch" in js
    assert "@media (min-width:600px)" in js and "'‹ 할 일'" in js and "W.history.back()" in js
    assert "W.history.replaceState(null, '', '#/todo')" in js  # 모르는 종류 → 목록
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    ev = (STATIC / "ssalmuk.js").read_text(encoding="utf-8")
    assert "m.firstChild.id === 'tdh'" in rt and "m.firstChild.id === 'tdh'" in ev  # 종류 목록 다음에 붙음 (서로 맨 위를 다투지 않게)
    assert "main[data-ev]>:not(#evs):not(#tdh)" in ev and "W.SBEvent = {load, mount, n:" in ev
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "todohub.js")], capture_output=True).returncode == 0


BROWSER = r"""
const {chromium} = require('playwright');
const fs = require('fs'), path = require('path');
(async () => {
  const [html, boot, rt, ev, dir, shots] = process.argv.slice(1);
  const b = await chromium.launch();
  const out = {}, errors = [];
  async function open(width, hash) {
    const ctx = await b.newContext({viewport: {width, height: 900}, serviceWorkers: 'block', hasTouch: width < 600});
    const p = await ctx.newPage();
    p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    p.on('pageerror', e => errors.push(String(e)));
    await p.route('http://dash.test/**', r => {
      const u = new URL(r.request().url()), j = d => r.fulfill({contentType: 'application/json', body: d});
      if (u.pathname === '/dashboard/') return r.fulfill({contentType: 'text/html', body: html});
      if (u.pathname.startsWith('/dashboard/static/')) {
        const f = path.join(dir, path.basename(u.pathname));
        return fs.existsSync(f) ? r.fulfill({contentType: f.endsWith('.css') ? 'text/css' : f.endsWith('.js') ? 'text/javascript' : 'image/png', body: fs.readFileSync(f)})
          : r.fulfill({status: 404, body: ''});
      }
      if (u.pathname === '/dashboard/api/routine') return j(rt);
      if (u.pathname === '/dashboard/api/uploads') return j('{"groups": []}');
      if (u.pathname === '/dashboard/api/ssalmuk/state') return j(ev);
      if (u.pathname === '/dashboard/api/state') return j(boot);
      return j('{}');
    });
    await p.goto('http://dash.test/dashboard/' + hash);
    await p.waitForFunction(() => document.querySelector('#tdh .sg2 button') && window.SBRoutine && Object.keys(window.SBRoutine.cnt).length > 3
      && window.SBEvent && window.SBEvent.n());  // 칸·수가 다 온 뒤
    await p.waitForTimeout(500);
    return p;
  }
  // 보이는 #main 자식 (id · data-k · class)
  const vis = p => p.$$eval('#main > *', es => es.filter(e => e.offsetParent !== null || getComputedStyle(e).display !== 'none' && e.getClientRects().length)
    .map(e => e.id || (e.hasAttribute('data-k') ? 'k:' + e.getAttribute('data-k') : e.className)));
  const hash = p => p.evaluate(() => location.hash);
  // 폰 (390)
  let p = await open(390, '#/todo');
  out.rows = await p.$$eval('#tdh .row2', es => es.map(e => [e.dataset.cat, e.classList.contains('z'), e.innerText.replace(/\s+/g, ' ').trim()]));
  out.hubVis = await vis(p); out.segShown = await p.$eval('#tdh .sg2', e => getComputedStyle(e).display);
  out.wide390 = await p.evaluate(() => document.documentElement.scrollWidth);
  if (shots) await p.screenshot({path: shots + '-390-hub.png'});
  await p.click('#tdh .row2[data-cat=issue]');
  await p.waitForTimeout(500);
  out.issueHash = await hash(p); out.issueVis = await vis(p);
  out.issueText = await p.$eval('#main', e => e.innerText);
  if (shots) await p.screenshot({path: shots + '-390-issue.png', fullPage: true});
  await p.click('#tdh .bk2 .lnk');  // ‹ 할 일 (목록에서 왔으니 뒤로)
  await p.waitForTimeout(500);
  out.backHash = await hash(p); out.backVis = await vis(p);
  await p.click('#tdh .row2[data-cat=finance]'); await p.waitForTimeout(500);
  await p.goBack(); await p.waitForTimeout(500);  // 밀어서 뒤로
  out.swipeHash = await hash(p); out.swipeRows = await p.$$eval('#tdh .row2', es => es.length);
  await p.click('#tdh .row2[data-cat=hotdeal]'); await p.waitForTimeout(500);
  await p.click('.tab[data-tab=todo]'); await p.waitForTimeout(500);  // 아래 '할 일' 탭
  out.tabHash = await hash(p); out.tabRows = await p.$$eval('#tdh .row2', es => es.length);
  await p.click('#tdh .row2[data-cat=ev]'); await p.waitForTimeout(400);
  out.evHash = await hash(p); out.evVis = await vis(p);
  await p.click('#tdh .bk2 .lnk'); await p.waitForTimeout(500);
  out.evBack = await hash(p);
  await p.click('#tdh .row2[data-cat=etc]'); await p.waitForTimeout(500);
  out.etcVis = await vis(p);
  out.last = await p.$eval('#tdh .bk2 b', e => e.textContent);
  await p.goBack(); await p.waitForTimeout(500);
  out.recent = await p.$eval('#tdh .row2 .rc', e => e.closest('.row2').dataset.cat);
  out.hubCount = await p.$$eval('#tdh', es => es.length);
  await p.context().close();
  // 새로고침 · 주소로 바로 열기
  p = await open(390, '#/todo/finance');
  out.deepVis = await vis(p); out.deepTitle = await p.$eval('#tdh .bk2 b', e => e.textContent);
  await p.click('#tdh .bk2 .lnk'); await p.waitForTimeout(500);
  out.deepBack = await hash(p);
  await p.context().close();
  p = await open(390, '#/todo/zzz');
  out.unknownHash = await hash(p); out.unknownVis = await vis(p);
  await p.context().close();
  // 아이패드 (1024): 가로 단추
  p = await open(1024, '#/todo');
  out.wideList = await p.evaluate(() => getComputedStyle(document.querySelector('#tdh .tl')).display);
  out.seg = await p.$$eval('#tdh .sg2 button', es => es.map(e => [e.innerText.replace(/\s+/g, ' ').trim(), e.classList.contains('z')]));
  if (shots) await p.screenshot({path: shots + '-1024-hub.png'});
  await p.click('#tdh .sg2 button:nth-child(1)'); await p.waitForTimeout(500);
  out.wideHash = await hash(p); out.widePressed = await p.$eval('#tdh .sg2 [aria-pressed=true]', e => e.innerText.split('\n')[0]);
  await p.click('#tdh .sg2 button:nth-child(2)'); await p.waitForTimeout(500);  // 종류끼리는 바꿔 끼움
  out.wideHash2 = await hash(p);
  if (shots) await p.screenshot({path: shots + '-1024-finance.png'});
  await p.goBack(); await p.waitForTimeout(500);
  out.wideBack = await hash(p);
  await p.context().close();
  out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


async def test_real_browser_hub_and_routes(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "items": [
        item("pub:issue:1", "s1", group="발행", blog="issue", title="데런픽 글 발행", min=5,
             copy=[{"label": "제목 복사", "text": "제목"}], links=[{"label": "글쓰기", "url": "https://blog.naver.com/x"}]),
        item("nb-add:issue:a", "s1", blog="issue", title="서로이웃 신청 · 이웃A", note="같은 주제", min=2,
             copy=[{"label": "멘트", "text": "반가워요"}], links=[{"label": "블로그", "url": "https://blog.naver.com/a"}]),
        item("pub:finance:2", "s2", group="발행", blog="finance", title="드라마 글 발행", min=5),
        item("sns:x:1", "s3", group="SNS", blog="", title="X 답글 달기", min=2)]})
    blogauto_status(settings)
    queued(bot, name="무선청소기 특가")
    h = session_headers(bot)
    html = (await bot.web.dispatch("GET", "/dashboard/", h))[1]
    boot = html.split('<script type="application/json" id="boot">', 1)[1].split("</script>", 1)[0]
    rt = routine.view(bot)
    ev = {"state": ev_sample(), "receivedAt": datetime.now(timezone.utc).isoformat(), "pending": 0, "stale": False, "results": []}
    shots = os.environ.get("TODOHUB_SHOT", "")
    out = subprocess.run([NODE or "node", "-e", BROWSER, html, boot, json.dumps(rt, ensure_ascii=False), json.dumps(ev, ensure_ascii=False),
                          str(STATIC), shots], capture_output=True, text=True, timeout=180, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["errors"] == [], got["errors"]
    cats = [r[0] for r in got["rows"]]
    assert sorted(cats) == sorted(["issue", "finance", "hotdeal", "deals", "sns", "shorts", "ev", "etc"])
    busy = [r[0] for r in got["rows"] if not r[1]]
    assert busy[:2] == ["issue", "finance"] and "deals" in busy and "sns" in busy and "ev" in busy  # 할 일 있는 종류 먼저, 정한 순서대로
    assert cats.index("deals") < cats.index("sns") < cats.index("ev")
    assert all(r[1] for r in got["rows"][len(busy):]) and all("할 일 없음" in r[2] for r in got["rows"][len(busy):])
    assert got["hubVis"] == ["tdh"] and got["segShown"] == "none" and got["wide390"] <= 390  # 폰: 목록만, 가로로 안 넘침
    # 종류 화면: 그 칸만 · 있던 단추 그대로 (복사·체크·서로이웃 멘트)
    assert got["issueHash"] == "#/todo/issue" and got["issueVis"] == ["tdh", "k:issue"]
    for word in ("‹ 할 일", "데런픽 글 발행", "제목 복사", "서로이웃 신청", "이웃A", "멘트 · 반가워요"):
        assert word in got["issueText"], word
    for bad in ("[object", "undefined", "NaN", "null"):
        assert bad not in got["issueText"], bad
    assert got["backHash"] == "#/todo" and got["backVis"] == ["tdh"]
    assert got["swipeHash"] == "#/todo" and got["swipeRows"] == 8
    assert got["tabHash"] == "#/todo" and got["tabRows"] == 8
    assert got["evHash"] == "#/todo/ev" and got["evVis"][:2] == ["tdh", "evs"] and len(got["evVis"]) == 2
    assert got["evBack"] == "#/todo"
    assert "rtp" in got["etcVis"] and not [x for x in got["etcVis"] if x.startswith("k:") and x != "k:"] and got["last"] == "기타"
    assert got["recent"] == "etc" and got["hubCount"] == 1
    assert got["deepVis"] == ["tdh", "k:finance"] and got["deepTitle"] == "드라마" and got["deepBack"] == "#/todo"
    assert got["unknownHash"] == "#/todo" and got["unknownVis"] == ["tdh"]
    # 넓은 화면: 블로그 탭 같은 가로 단추 (0 인 종류도 흐리게)
    assert got["wideList"] == "none" and [s[0].rstrip("0123456789") for s in got["seg"]] == ["데런픽", "드라마", "라커룸", "딜", "SNS", "숏폼", "이벤트", "기타"]
    assert dict((s[0].rstrip("0123456789"), s[1]) for s in got["seg"])["기타"] is True
    assert got["wideHash"] == "#/todo/issue" and got["widePressed"].startswith("데런픽") and got["wideHash2"] == "#/todo/finance"
    assert got["wideBack"] == "#/todo"

