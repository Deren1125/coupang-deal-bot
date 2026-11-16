"""'글쓰기 한 번에' 책갈피 (주인 11/16 — 글쓰기에서 한 번 누르면 오늘 붙여넣기 대기 글을 차례로 채우고 글마다 '발행'만 주인이).

1) writebm.bookmarklet(): 짧은 받는 코드 (열쇠·주소·블로그 표) — 알림·확인 창 없음 · 진행기(runner) = write.js + kkumi.js 통째 (PATCHES 네 줄) · node 로 읽힘 ·
   발행을 끝내는 단추를 누르는 코드가 없음.
2) writebm.act(): 열쇠 · 그 블로그의 오늘 대기 글만 차례로 · 글 사이 30~60초 · 발행 확인이면 할 일 줄 체크 + 블로그 프로그램에 pasted · 확인 못 한 글·폰에서 직접은
   체크하지 않고 오늘은 다시 주지 않음 · 못 넣은 글은 다음에 다시.
3) GET /dashboard/api/writebm (로그인 아래) · POST /dashboard/api/write_act (열쇠 · 교차 사이트 · CORS 는 네이버 블로그 화면만).
4) 가짜 글쓰기 화면 (진짜 크롬, playwright 가 있을 때만): 바깥이 frameset 인 글쓰기 화면에서 한 번 → 글 2편을 제목·본문·태그·예약까지 채우고 멈춤 →
   발행을 (시험이 주인 대신) 누르면 다음 글 → 끝 요약. 네이버 화면이 맨 위 창을 옮기려 해도 진행기가 살아 있음 · 자동입력 방지가 뜨면 '폰에서 직접'.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import actions, kkumi, routine, writebm
from test_dash_kkumi import INNER, _playwright_env, _tags_line
from test_dash_kkumi_when import RSV
from test_dash_routine import SLOTS, item, tz_now, with_routine
from test_dash_statscap import FakeDb

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
TZ = ZoneInfo("Asia/Seoul")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.public_domain = "bot.example.com"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)
    routine._TH_CACHE.clear()


def _paste(title: str, body: str = "본문이에요.") -> dict:
    return {"title": title, "html": f"<p>{body}</p>" + _tags_line(["태그"], "셀럽 소식"), "tags": ["태그"], "cards": 0, "photos": 0}


def _rows(day: str) -> dict:
    return {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("pub:issue:11", "s1", group="발행", blog="issue", title="데런픽 발행 · 하나", paste=_paste("글 하나")),
        item("rsv:issue:12", "s2", group="예약", blog="issue", title="데런픽 예약 · 둘", paste=_paste("글 둘")),
        item("rsv:issue:13", "s2", group="예약", blog="issue", title="데런픽 예약 · 만드는 중", paste={"title": "글 셋", "stale": True, "why": "building"}),
        item("pub:finance:14", "s1", group="발행", blog="finance", title="드라마 발행", paste=_paste("글 넷")),
        item("nb:issue:a", "s1", blog="issue"),
    ]}


# ---------------------------------------------------------------- 1) 책갈피·진행기
def test_bookmarklet_is_a_short_loader_with_no_dialogs() -> None:
    db = FakeDb()
    v = writebm.view(db, "https://dash.example")
    js = unquote(v["code"][len("javascript:"):])
    assert v["code"].startswith("javascript:") and v["len"] < 2500 and v["steps"] == list(writebm.STEPS) and v["version"] == 1
    assert "'https://dash.example/dashboard/api/write_act'" in js and f"'{writebm.key(db)}'" in js and "@@" not in js
    assert '["derenpick","issue","데런픽"]' in js and "credentials:'omit'" in js
    for word in ("alert(", "confirm(", "prompt(", ".click(", "cookie"):
        assert word not in js, word
    with pytest.raises(ValueError):
        writebm.bookmarklet("http://dash.example/x", "k")
    assert writebm.key(db) == writebm.key(db) and writebm.key(FakeDb()) != writebm.key(db) and not writebm.key_ok(db, "") and writebm.key_ok(db, writebm.key(db))


def test_runner_embeds_kkumi_whole_and_never_presses_the_final_button() -> None:
    r = writebm.runner()
    shell = (STATIC / "write.js").read_text(encoding="utf-8")
    assert r.startswith("async CFG => {") and "@@KKUMI@@" not in r and shell.count(writebm.SLOT) == 1
    assert "const CORE = async (document, alert, confirm, GOT, PRE) => {" in r and "const got = GOT;" in r and "await ask();" not in r
    # 꾸미기의 막이(tap)가 그대로 들어 있고, 껍데기에는 마지막 발행 단추를 누르는 길이 없다 (지켜보는 선택자만 · 누르기는 k.press·K.tap 뿐)
    assert "if (/confirm|publish|submit/i.test(b.className + b.getAttribute('data-testid') + b.type) || /발행|확인|등록/.test(b.innerText)) throw 0;" in r
    for word in (".click(", "requestSubmit", ".submit(", "alert(", "confirm(", "prompt(", "eval(", "document.cookie", "localStorage"):
        assert word not in shell, word
    presses = [ln.strip() for ln in shell.splitlines() if "press(" in ln or ".tap(" in ln]
    assert len(presses) == 7 and all(not any(w in p for w in ("confirm_btn", "seOnePublishBtn")) for p in presses), presses
    assert "isTrusted" in shell and "sandbox" in shell and "credentials: 'omit'" in shell
    # kkumi.js 에서 바꿀 줄이 사라지면 바로 알림
    for old, _new in writebm.PATCHES:
        assert kkumi.SRC.read_text(encoding="utf-8").count(old) == 1, old
    assert writebm.ROW_RE.pattern == actions.PASTE_ROW_RE.pattern


@needs_node
def test_runner_and_shell_parse(tmp_path: Path) -> None:
    f = tmp_path / "r.js"
    f.write_text("(" + writebm.runner() + ")", encoding="utf-8")
    for p in (f, STATIC / "write.js"):
        out = subprocess.run([NODE, "--check", str(p)], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
    g = tmp_path / "l.js"
    g.write_text(unquote(writebm.view(FakeDb(), "https://dash.example")["code"][len("javascript:"):]), encoding="utf-8")
    assert subprocess.run([NODE, "--check", str(g)], capture_output=True, timeout=60).returncode == 0


# ---------------------------------------------------------------- 2) act
class Rng:
    def randint(self, a: int, b: int) -> int:
        return 45


def test_act_serves_the_blogs_pending_posts_in_order_and_checks_published(bot: DealBot, tmp_path: Path) -> None:
    now = tz_now(bot)
    day, t = now.date().isoformat(), now.timestamp()
    with_routine(tmp_path, _rows(day))
    k = writebm.key(bot.db)

    def go(now_: float = t, **form: str) -> dict[str, Any]:
        return writebm.act(bot.db, {"k": k, **form}, tz=TZ, now=now_, rv=routine.view(bot, now=now_), rng=Rng())

    with pytest.raises(writebm.CaptureError) as e:
        writebm.act(bot.db, {"k": "wrong", "a": "code"}, tz=TZ)
    assert e.value.status == 403 and e.value.code == "key"
    assert go(a="code")["code"] == writebm.runner()
    with pytest.raises(writebm.CaptureError) as e:
        go(a="next", b="nope")
    assert e.value.code == "who"
    q = go(a="next", b="issue")
    assert q["left"] == 2 and q["wait"] == 0 and q["next"]["id"] == "pub:issue:11" and q["next"]["title"] == "글 하나" and q["next"]["group"] == "발행"
    assert "본문이에요." in q["next"]["html"] and q["next"]["when"] == ""  # 만드는 중인 묶음·다른 블로그·이웃 줄은 없음
    assert go(a="next", b="hotdeal")["next"] is None and go(a="next", b="finance")["next"]["id"] == "pub:finance:14"
    # 다른 블로그 줄·모르는 결과는 거절 · 발행 확인이면 줄 체크 + 블로그 프로그램에 전할 것으로 적음
    for bad in ({"id": "pub:finance:14", "r": "ok"}, {"id": "pub:issue:11", "r": "yes"}, {"id": "nb:issue:a", "r": "ok"}):
        with pytest.raises(writebm.CaptureError):
            go(a="done", b="issue", **bad)
    d = go(a="done", b="issue", id="pub:issue:11", r="ok", p="1", n="사진은  직접\n넣어 주세요")
    assert d == {"ok": True, "checked": True, "text": "할 일 줄 체크 ✓"} and "pub:issue:11" in routine.done_ids(bot.db, day)
    relay = routine.relay_rows(bot.db)
    assert [(r["name"], r["args"], r["rid"]) for r in relay] == [("pasted", {"blog": "issue", "pid": 11, "done": True}, "pub:issue:11")]
    # 다음 글: 예약 줄 (꾸미기가 채울 예약 시각이 글에 실려 있음) · 앞 글을 끝낸 뒤 45초가 될 때까지 쉼
    q = go(t + 10, a="next", b="issue")
    assert q["next"]["id"] == "rsv:issue:12" and q["left"] == 1 and q["wait"] == 35 and q["next"]["when"]
    assert kkumi.stamped(q["next"]["html"]) and go(t + 50, a="next", b="issue")["wait"] == 0
    # 확인 못 한 글: 체크하지 않고 오늘은 다시 주지 않음 (두 번 발행 막기)
    d = go(t + 60, a="done", b="issue", id="rsv:issue:12", r="unsure")
    assert d["checked"] is False and "rsv:issue:12" not in routine.done_ids(bot.db, day) and len(routine.relay_rows(bot.db)) == 1
    q = go(t + 120, a="next", b="issue")
    assert q["next"] is None and q["text"] == "데런픽: 오늘 붙여 넣을 글이 더 없어요"
    # 못 넣은 글은 다음에 다시 · 폰에서 직접은 오늘 다시 주지 않음
    go(t + 130, a="done", b="finance", id="pub:finance:14", r="fail", n="제목을 넣지 못했어요")
    assert go(t + 200, a="next", b="finance")["next"]["id"] == "pub:finance:14"
    go(t + 210, a="done", b="finance", id="pub:finance:14", r="phone")
    assert go(t + 300, a="next", b="finance")["next"] is None
    td = writebm.today(bot.db, t + 300, TZ)
    assert td["note"] == "오늘 글쓰기 한 번에: 발행·예약 1 · 확인 필요 1 · 폰에서 직접 1 · 사진은 직접 1" and td["fail"] == 0  # 같은 글은 마지막 결과만
    assert writebm.view(bot.db, "https://dash.example", now=t + 300, tz=TZ)["note"] == td["note"]
    # 이미 체크된 줄에 발행 확인이 또 와도 다시 전하지 않음
    assert go(t + 310, a="done", b="issue", id="pub:issue:11", r="ok")["checked"] is False and len(routine.relay_rows(bot.db)) == 1


def test_act_gives_nothing_when_the_list_is_not_todays(bot: DealBot, tmp_path: Path) -> None:
    now = tz_now(bot)
    with_routine(tmp_path, _rows((now - timedelta(days=1)).date().isoformat()))
    k = writebm.key(bot.db)
    q = writebm.act(bot.db, {"k": k, "a": "next", "b": "issue"}, tz=TZ, now=now.timestamp(), rv=routine.view(bot, now=now.timestamp()))
    assert q["next"] is None and q["text"].startswith("오늘 할 일 목록이 아직 없어요")
    d = writebm.act(bot.db, {"k": k, "a": "done", "b": "issue", "id": "pub:issue:11", "r": "ok"}, tz=TZ, now=now.timestamp(), rv=routine.view(bot, now=now.timestamp()))
    assert d["checked"] is False and routine.relay_rows(bot.db) == []
    assert writebm.act(bot.db, {"k": k, "a": "next", "b": "issue"}, tz=TZ, rv=None)["next"] is None


# ---------------------------------------------------------------- 3) 대시보드
async def _post(bot: DealBot, body: dict[str, str], **headers: str):  # type: ignore[no-untyped-def]
    raw = urlencode(body).encode()
    h = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9", "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
         "content-length": str(len(raw)), "sec-fetch-site": "cross-site", **headers}
    return await bot.web.dispatch("POST", writebm.PATH, h, raw)


async def test_api_code_needs_login_and_act_takes_key_only(bot: DealBot, tmp_path: Path) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/writebm", anon))[0] == 401
    status, v, hd = await get_json(bot, "/dashboard/api/writebm", session_headers(bot))
    k = writebm.key(bot.db)
    assert status == 200 and k in unquote(v["code"]) and hd["Cache-Control"] == "no-store" and "note" not in v
    with_routine(tmp_path, _rows(tz_now(bot).date().isoformat()))
    res = await _post(bot, {"k": k, "a": "code"}, origin="https://blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["code"] == writebm.runner() and res[3]["Access-Control-Allow-Origin"] == "https://blog.naver.com"
    assert res[3]["Cache-Control"] == "no-store"
    res = await _post(bot, {"k": k, "a": "next", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["next"]["id"] == "pub:issue:11"
    res = await _post(bot, {"k": k, "a": "done", "b": "issue", "id": "pub:issue:11", "r": "ok"}, origin="https://blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["checked"] is True
    status, rt, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert status == 200 and next(i for s in rt["slots"] for i in s["items"] if i["id"] == "pub:issue:11")["done"] is True
    res = await _post(bot, {"k": "wrong", "a": "next", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key" and "html" not in res[1].decode() if isinstance(res[1], bytes) else True
    for o in ("https://evil.example", "https://cafe.naver.com", "https://blog.naver.com.evil.example", "http://blog.naver.com", "null"):
        res = await _post(bot, {"k": k, "a": "code"}, origin=o)  # 네이버 블로그 화면이 아니면 답을 못 읽음
        assert "Access-Control-Allow-Origin" not in res[3] and res[3]["Vary"] == "Origin", o
    assert (await bot.web.dispatch("GET", writebm.PATH, anon))[0] == 405
    assert (await _post(bot, {"k": k, "a": "code", "x": "가" * 3000}, origin="https://blog.naver.com"))[0] == 413
    # 다른 길에는 네이버 출처 CORS 가 붙지 않음 (이 길만)
    res = await bot.web.dispatch("GET", "/dashboard/api/routine", {**session_headers(bot), "origin": "https://blog.naver.com"})
    assert "Access-Control-Allow-Origin" not in res[3]


def test_connect_tab_lists_the_install_box() -> None:
    js = (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "BM.push({api: 'writebm', t: '글쓰기 한 번에 버튼 설치 (글마다 발행만 누름)'});" in js


# ---------------------------------------------------------------- 4) 가짜 글쓰기 화면
# 네이버 글쓰기처럼: 바깥(/derenpick/postwrite)은 frameset, 안쪽(/PostWriteForm.naver)이 에디터. 처음 커서는 제목 — 스크립트로 넣은 것은 커서 자리에,
# 커서는 손으로 누르거나 '본문 추가'를 눌러야 본문으로 (10/29 아이패드). 마지막 발행 단추를 누르면 MODE 대로:
#   xhr = 저장 요청(/RabbitWrite.naver) 뒤 맨 위 창을 글 주소로 옮기려 함 · self = 요청 없이 안쪽 창만 글 주소로 · wall = 자동입력 방지 창이 뜸
EXTRA = r"""<script>
let wbFocus = 'title';
const TT = document.querySelector('.se-documentTitle .se-text-paragraph');
TT.innerHTML = '<span class="se-placeholder">제목</span>';
document.addEventListener('paste', e => { if (wbFocus !== 'title') return; e.stopImmediatePropagation(); e.preventDefault(); TT.textContent = e.clipboardData.getData('text/plain'); }, true);
document.querySelector('.se-canvas-bottom-button').addEventListener('click', () => { wbFocus = 'body'; });
document.addEventListener('click', e => { if (e.isTrusted && e.target.closest('.se-content')) wbFocus = 'body'; }, true);
const sv = document.createElement('button'); sv.type = 'button'; sv.className = 'save_btn__x'; sv.textContent = '저장'; sv.onclick = () => { fetch('/saved?t=' + encodeURIComponent(TT.textContent)); };
document.getElementById('tb').append(sv);
document.addEventListener('click', e => {
  if (!e.target.closest || !e.target.closest('.confirm_btn__W')) return;
  const MODE = (location.search.match(/mode=(\w+)/) || [])[1] || 'xhr', st = window.rsvState ? window.rsvState() : {};
  const body = JSON.stringify({title: TT.textContent, text: document.querySelector('.se-content').innerText, tags: (document.querySelector('.chips') || {}).textContent,
    cat: (document.querySelector('.option_category button') || {}).textContent, book: st.book, date: st.date, h: st.h, m: st.m, imgs: document.querySelectorAll('.se-image').length, trusted: e.isTrusted});
  if (MODE === 'wall') { fetch('/pressed', {method: 'POST', body}); const w = document.createElement('div'); w.textContent = '자동입력 방지 문자를 입력해 주세요'; document.body.append(w); return; }
  if (MODE === 'self') { fetch('/pressed', {method: 'POST', body}).then(() => { location.href = '/derenpick/223000000002'; }); return; }
  const x = new XMLHttpRequest();
  x.open('POST', '/RabbitWrite.naver');
  x.onload = () => { top.location.href = JSON.parse(x.responseText).result.redirectUrl; };
  x.send(body);
});
</script></body></html>
"""
FLOW = r"""
const {chromium} = require('playwright');
(async () => {
  const [editor, loader, runner, posts, mode] = process.argv.slice(1);
  const b = await chromium.launch();
  const ctx = await b.newContext({viewport: {width: 1100, height: 900}});
  const p = await ctx.newPage();
  const out = {dialogs: [], done: [], pub: [], saved: [], next: 0, errors: []};
  const queue = JSON.parse(posts);
  p.on('dialog', async d => { out.dialogs.push(d.message()); await d.dismiss(); });
  p.on('pageerror', e => out.errors.push(String(e)));
  const cors = {'Access-Control-Allow-Origin': 'https://blog.naver.com'};
  await ctx.route('**/*', async r => {
    const q = r.request(), u = new URL(q.url());
    if (u.host === 'dash.test' && u.pathname === '/dashboard/api/write_act') {
      const f = new URLSearchParams(q.postData() || '');
      if (f.get('k') !== 'KEY') return r.fulfill({status: 403, headers: cors, contentType: 'application/json', body: '{"ok":false,"text":"열쇠"}'});
      if (f.get('a') === 'code') return r.fulfill({headers: cors, contentType: 'application/json', body: JSON.stringify({ok: true, code: runner})});
      if (f.get('a') === 'next') { out.next++; const left = queue.filter(x => !out.done.some(d => d.id === x.id));
        return r.fulfill({headers: cors, contentType: 'application/json', body: JSON.stringify({ok: true, next: left[0] || null, left: left.length, wait: out.done.length ? 1 : 0, text: '데런픽: 오늘 붙여 넣을 글이 더 없어요'})}); }
      if (f.get('a') === 'done') { out.done.push({id: f.get('id'), r: f.get('r'), p: f.get('p'), n: f.get('n'), b: f.get('b')});
        return r.fulfill({headers: cors, contentType: 'application/json', body: JSON.stringify({ok: true, checked: f.get('r') === 'ok'})}); }
    }
    if (u.host === 'dash.test' && u.pathname.startsWith('/media/')) return r.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, contentType: 'image/jpeg', body: Buffer.from([255, 216, 255, 217])});
    if (u.host !== 'blog.naver.com') return r.fulfill({status: 404, body: ''});
    if (u.pathname === '/derenpick/postwrite') return r.fulfill({contentType: 'text/html', body: '<html><head><title>w</title></head><frameset rows="*"><frame id="mainFrame" src="/PostWriteForm.naver?blogId=derenpick&mode=' + mode + '"></frameset></html>'});
    if (u.pathname === '/PostWriteForm.naver') return r.fulfill({contentType: 'text/html; charset=utf-8', body: editor});
    if (u.pathname === '/RabbitWrite.naver') { out.pub.push(JSON.parse(q.postData())); return r.fulfill({contentType: 'application/json', body: JSON.stringify({isSuccess: true, result: {redirectUrl: 'https://blog.naver.com/derenpick/22300000000' + out.pub.length}})}); }
    if (u.pathname === '/pressed') { out.pub.push(JSON.parse(q.postData())); return r.fulfill({body: 'ok'}); }
    if (u.pathname === '/saved') { out.saved.push(u.searchParams.get('t')); return r.fulfill({body: 'ok'}); }
    return r.fulfill({contentType: 'text/html; charset=utf-8', body: '<html><body><p>글 보기 화면</p></body></html>'});
  });
  await p.goto('https://blog.naver.com/derenpick/postwrite');
  await p.frames()[1].waitForSelector('.se-content');
  p.evaluate(loader);
  const bar = () => p.evaluate(() => { const e = document.querySelector('body > div > div'); return e ? e.textContent : ''; });
  out.bars = [];
  for (let n = 0; n < queue.length; n++) {
    let t = '';
    for (let i = 0; i < 600; i++) { t = await bar(); if (t.includes((n + 1) + '/') && /준비 끝|멈췄어요|끝났어요/.test(t)) break; await p.waitForTimeout(200); }
    out.bars.push(t);
    if (!t.includes('준비 끝')) break;
    const fr = p.frames().filter(f => f.url().includes('PostWriteForm') && !f.isDetached()).pop();
    out.sandbox = await p.evaluate(() => document.querySelector('iframe').getAttribute('sandbox'));
    await fr.click('.confirm_btn__W');
  }
  let t = '';
  for (let i = 0; i < 300; i++) { t = await bar(); if (/끝났어요|멈췄어요/.test(t)) break; await p.waitForTimeout(200); }
  out.end = t;
  out.lines = await p.evaluate(() => [...document.querySelectorAll('body > div > div:nth-child(2) p')].map(x => x.textContent));
  out.url = p.url();
  out.frames = await p.evaluate(() => document.querySelectorAll('iframe,frame,frameset').length);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _editor() -> str:
    return INNER.replace("</body></html>", RSV.replace("__MODE__", "full")).replace("</body></html>", EXTRA)


def _flow(posts: list[dict], mode: str) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    loader = unquote(writebm.bookmarklet("https://dash.test/dashboard/api/write_act", "KEY")[len("javascript:"):])
    out = subprocess.run([NODE, "-e", FLOW, _editor(), loader, writebm.runner(), json.dumps(posts, ensure_ascii=False), mode],
                         capture_output=True, text=True, timeout=240, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _posts(when: datetime) -> list[dict]:
    line = _tags_line(["아일릿", "키링"], "셀럽 소식")
    return [
        {"id": "pub:issue:11", "title": "첫 글 제목이에요", "group": "발행", "when": "", "photos": 1, "text": "",
         "html": '<p>첫 글 첫 문단.</p><p><img src="https://dash.test/media/blog_issue_11_1_abcdef12.jpg"></p><p>첫 글 끝 문단.</p>' + line},
        {"id": "rsv:issue:12", "title": "둘째 글 제목", "group": "예약", "when": "내일 19:00", "photos": 0, "text": "",
         "html": "<p>둘째 글 첫 문단.</p><p>둘째 글 끝 문단.</p>" + kkumi.stamp(line, when.strftime("%Y-%m-%dT%H:%M"))},
    ]


@needs_node
def test_one_press_fills_two_posts_and_waits_for_publish_each_time() -> None:
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    got = _flow(_posts(when), "xhr")
    assert got["dialogs"] == [], got  # 알림·확인 창 0개
    # 가짜 네이버 화면이 맨 위 창을 옮기려다 막힌 것뿐 (글마다 한 번) — 진행기 오류는 없음
    assert len(got["errors"]) == 2 and all(e.startswith("SecurityError") and "navigate" in e for e in got["errors"]), got["errors"]
    assert [(d["id"], d["r"], d["b"]) for d in got["done"]] == [("pub:issue:11", "ok", "issue"), ("rsv:issue:12", "ok", "issue")], got
    a, b = got["pub"]
    assert a["trusted"] and b["trusted"]  # 발행은 손으로 누른 것만 (시험이 주인 대신)
    assert a["title"] == "첫 글 제목이에요" and "첫 글 첫 문단." in a["text"] and "첫 글 끝 문단." in a["text"] and "첫 글 제목" not in a["text"]
    assert a["imgs"] == 1 and a["tags"].split() == ["#아일릿", "#키링"] and a["cat"] == "셀럽 소식" and a["book"] is False
    assert b["title"] == "둘째 글 제목" and "둘째 글 끝 문단." in b["text"] and "첫 글" not in b["text"]  # 글마다 새 글쓰기 화면
    assert b["book"] is True and b["h"] == "19" and b["date"] == f"{when.year}. {when.month:02d}. {when.day:02d}"
    assert "1/2 「첫 글 제목이에요」 준비 끝 — '발행'만 눌러 주세요" in got["bars"][0]
    assert "2/2 「둘째 글 제목」 준비 끝 — '발행'만 눌러 주세요 (예약) · 예약 " in got["bars"][1]
    assert got["end"].startswith("글쓰기 한 번에 · 끝났어요 · 발행·예약 2 · 할 일 체크 2"), got["end"]
    assert len(got["lines"]) == 2 and all("할 일 줄 체크 ✓" in x for x in got["lines"])
    # 네이버 화면이 맨 위 창을 글 주소로 옮기려 했지만 진행기는 누른 화면에 그대로 (sandbox) · 끝나면 글쓰기 창을 치움
    assert got["url"] == "https://blog.naver.com/derenpick/postwrite" and got["sandbox"] and "allow-top-navigation" not in got["sandbox"]
    assert got["frames"] == 0 and got["next"] == 3


@needs_node
def test_publish_is_seen_when_the_editor_frame_moves_to_the_post() -> None:
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    got = _flow(_posts(when)[:1], "self")
    assert got["dialogs"] == [] and [(d["id"], d["r"]) for d in got["done"]] == [("pub:issue:11", "ok")], got
    assert got["end"].startswith("글쓰기 한 번에 · 끝났어요 · 발행·예약 1")


@needs_node
def test_captcha_is_not_solved_the_post_is_saved_for_the_phone_and_the_next_one_opens() -> None:
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    got = _flow(_posts(when), "wall")
    assert got["dialogs"] == [] and [(d["id"], d["r"]) for d in got["done"]] == [("pub:issue:11", "phone"), ("rsv:issue:12", "phone")], got
    assert got["saved"] == ["첫 글 제목이에요", "둘째 글 제목"] and all("임시저장함" in d["n"] for d in got["done"])
    assert "폰에서 직접 2" in got["end"] and "할 일 체크 0" in got["end"] and all(x.startswith("폰에서 직접") for x in got["lines"])
