"""'한 번에' 책갈피 (주인 11/16 — '책갈피 버튼 합치자 · 켜놓고 놔두면 한 번에' · '내가 누를 버튼이 없어야 한다 · 한 번 누르면 순차적으로').

1) 만들기 (one.py): 통계 보내기·이웃 관리 코드를 껍데기(one.js)에 통째로 — 주소·열쇠는 저마다 · node 로 읽힘 · 옛 두 책갈피는 그대로 · seoi.js 의 고칠 줄이 사라지면 알림.
2) 대시보드: GET /dashboard/api/one (로그인 아래) · a=queue 답의 stats 칸 · 연결 탭 설치 칸 맨 위 + 옛 둘은 '따로 쓰고 싶을 때' 아래.
3) 가짜 네이버 화면 (진짜 크롬 · playwright 가 있을 때만 · test_dash_seoi_auto 의 화면·가짜 시계 그대로): 한 번 누르고 아무것도 누르지 않음 —
   서이추 → 들르기 → 줄 체크 → 끝 요약 · 알림·묻기 창 0 · 보이는 단추 0 · 간격은 seoi.GAP 그대로 · 한도 안내는 바로 멈추고 '폰에서 직접' · 다시 누르면 이어서.
4) 가짜 통계 앱 (11/18 v3): 한 번 누르면 묻지 않고 조회수 순위·유입분석·검색 유입을 블로그·날짜와 함께 보내고 스스로 이웃새글로 · 우리 블로그가 아니면 안 보냄 · 관리 화면은 통계 앱으로.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, session_headers
from dealbot.app import DealBot
from dealbot.dashboard import api as api_mod
from dealbot.dashboard import kkumi, one, routine, seoi, statscap
from dealbot.dashboard.actions import RateWindow
from test_dash_kkumi import _playwright_env
from test_dash_nbvisit import list_for, visit_row
from test_dash_routine import tz_now
from test_dash_seoi import NODE, nb_row, needs_node
from test_dash_seoi_auto import BUDDY, HOME, LOGIN, POST, POST_NO, POST_TEXT_LONG, Clock, SeqDrafter, _no_live_threads, bot, q  # noqa: F401
from test_dash_statscap import FakeDb

STATIC = Path(one.SRC).parent
DASH = "https://dash.example"


def built(db: Any, base: str = DASH) -> str:
    return unquote(one.view(db, base)["code"][len("javascript:"):])


# ---------------------------------------------------------------- 1) 만들기
def test_one_holds_both_bookmarklets_with_their_own_endpoints_and_keys() -> None:
    db = FakeDb()
    v = one.view(db, DASH)
    assert v["code"].startswith("javascript:") and v["len"] == len(v["code"]) <= one.LEN_MAX and v["steps"] == seoi.steps(one.STEPS) and v["version"] == 4
    js = built(db)
    assert f"U='{DASH}{seoi.PATH}',K='{seoi.key(db)}'" in js and f"U='{DASH}{statscap.PATH}',K='{statscap.key(db)}'" in js
    assert seoi.key(db) != statscap.key(db) and "@@" not in js and '["derenpick","issue","데런픽"]' in js
    assert "/*" not in js and "\n//" not in js and "#" not in v["code"] and "\n" not in v["code"]
    # 줄이기 전 코드와 같은 코드로 풀림 (encode_short ↔ 브라우저의 % 풀기)
    assert unquote(kkumi.encode(js)) == js
    # 옛 두 책갈피는 한 글자도 안 바뀜 (껍데기의 갈고리가 섞이지 않음)
    for old in (unquote(seoi.view(db, DASH)["code"]), unquote(statscap.view(db, DASH)["code"])):
        assert "ONE." not in old and "__sb1" not in old
    assert "await choose()" in unquote(seoi.view(db, DASH)["code"]) and "await choose()" not in js
    with pytest.raises(ValueError):
        one.bookmarklet("http://dash.example/x", "k", DASH + "/y", "k")
    with pytest.raises(ValueError):
        one.bookmarklet(DASH + "/x", "k'", DASH + "/y", "k")


@needs_node
def test_one_parses_and_never_opens_a_dialog_or_adds_a_number_of_its_own(tmp_path: Path) -> None:
    f = tmp_path / "one.js"
    f.write_text(built(FakeDb()), encoding="utf-8")
    assert subprocess.run([NODE, "--check", str(f)], capture_output=True).returncode == 0
    assert subprocess.run([NODE, "--check", str(one.SRC)], capture_output=True).returncode == 0
    src = one.SRC.read_text(encoding="utf-8").split("*/", 1)[1]
    # 껍데기: 창을 띄우는 것·누르는 것·바깥 요청이 없음 (알림·묻기·적기는 같은 이름 함수로 받아 칸에 적음)
    assert not re.search(r"\b(alert|confirm|prompt)\(", src) and "const alert = note;" in src and "const confirm = t =>" in src and "const prompt = t =>" in src
    for bad in (".click(", "fetch(", "XMLHttpRequest", "dispatchEvent", "createElement('button')", "createElement('a')", "document.cookie", "innerHTML", "eval(", "Function("):
        assert bad not in src, bad
    # 간격·상한 숫자는 껍데기에 없음 — 기다리는 것은 화면 옮기기 전 잠깐뿐
    assert sorted(set(re.findall(r"sl\(([^)]*)\)", src))) == ["1500", "5000", "nv ? 1500 : 6000"]
    # 한 줄 규칙 (kkumi.minify · seoi.pack): 주석은 줄 맨 앞에만 · 한글 글 안에 역슬래시 없음
    for ln in src.splitlines():
        assert "//" not in ln.replace("https://", "") or ln.lstrip().startswith("//"), ln
    for lit in re.findall(r"'([^'\n]*[가-힣][^'\n]*)'", src):
        assert "\\" not in lit, lit


def test_patches_fail_loudly_when_seoi_js_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = seoi.SRC.read_text(encoding="utf-8")
    for old, _new in one.PATCHES:
        assert src.count(old) == 1, old
    gone = tmp_path / "seoi.js"
    gone.write_text(src.replace("b = await choose();", "b = await pick();"), encoding="utf-8")
    monkeypatch.setattr(seoi, "SRC", gone)
    with pytest.raises(ValueError, match="seoi.js 에 없는 줄"):
        one._patched_seoi()


# ---------------------------------------------------------------- 2) 대시보드
async def test_api_gives_the_code_under_login_only_and_queue_says_whether_stats_came(bot: DealBot, tmp_path: Path) -> None:
    from test_dash_seoi import _post

    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/one", anon))[0] == 401
    status, v, h = await get_json(bot, "/dashboard/api/one", session_headers(bot))
    assert status == 200 and v["steps"] == seoi.steps(one.STEPS) and h.get("Cache-Control") == "no-store"
    js = unquote(v["code"])
    assert seoi.key(bot.db) in js and statscap.key(bot.db) in js and seoi.PATH in js and statscap.PATH in js
    assert "note" in v  # 이웃 관리 칸과 같은 오늘 수 한 줄
    # 새 POST 길은 없음 — 책갈피 이름으로는 받지 않음
    assert (await bot.web.dispatch("POST", "/dashboard/api/one", {**anon, "content-length": "0"}, b""))[0] in (403, 404, 405)
    day = tz_now(bot).date().isoformat()
    list_for(tmp_path, day, [nb_row("issue", "nb1", "멘트")])
    got = json.loads((await _post(bot, q(bot.db), origin="https://m.blog.naver.com"))[1])
    assert got["ok"] is True and got["stats"] is False
    statscap.save(bot.db, {"k": statscap.key(bot.db), "u": "https://blog.stat.naver.com/a?blogId=derenpick", "t": "통계", "s": "조회수 순위", "x": "조회수 순위\n1 글 30", "r": "[]"},
                  tz=ZoneInfo(bot.settings.app.timezone))
    got = json.loads((await _post(bot, q(bot.db), origin="https://m.blog.naver.com"))[1])
    assert got["stats"] is True and got["next"] == {"id": "nb1", "msg": "멘트"}
    assert json.loads((await _post(bot, q(bot.db, "finance"), origin="https://m.blog.naver.com"))[1])["stats"] is False  # 블로그마다


def test_install_box_puts_one_first_and_folds_the_two_old_buttons() -> None:
    lg = (STATIC / "logins.js").read_text(encoding="utf-8")
    bm = lg.split("const BM = [", 1)[1].split("];", 1)[0]
    apis = re.findall(r"api: '(\w+)'", bm)
    assert apis == ["autopub", "one", "seoi", "statscap", "ablyrank", "xhelper", "writebm"]  # 하루치 한 번에(발행까지 · 11/23)가 접기 첫째 · 꾸미기는 BM 밖 맨 위 카드 (KK — 주인 11/16 14시)
    assert re.findall(r"api: '(\w+)', old: 1", bm) == ["seoi", "statscap", "ablyrank", "xhelper", "writebm"] and bm.index("MORE") < bm.index("seoi")
    assert "const KK = {api: 'kkumi'}, MORE = {t: '따로 쓰고 싶을 때 (옛 이웃 관리 · 통계 보내기 · Ably 랭킹)'}" in lg
    assert "const shown = b => (!b.old || MORE.o) && (!b.x || (data && data.xhelper)) && (!b.w || (data && data.writebm)) && (!b.a || !data || data.autopub !== false);" in lg
    assert "이웃 한 번에 버튼 설치 (이웃 서이추·들르기 + 통계 · 누를 단추 없음)" in bm


# ---------------------------------------------------------------- 3) 가짜 네이버 화면 — 한 번 누르고 손을 뗌
RUN1 = r"""
const {chromium} = require('playwright');
const [code, sj] = process.argv.slice(1), S = JSON.parse(sj);
const rl = require('readline').createInterface({input: process.stdin}), waiters = [], lines = [];
rl.on('line', l => { const w = waiters.shift(); if (w) w(l); else lines.push(l); });
const rpc = o => new Promise(r => { process.stdout.write('REQ ' + JSON.stringify(o) + '\n'); if (lines.length) r(lines.shift()); else waiters.push(r); }).then(JSON.parse);
const real = ms => new Promise(r => setTimeout(r, ms));
setTimeout(() => { console.error('harness timeout'); process.exit(4); }, S.real_ms || 240000);
(async () => {
  const b = await chromium.launch();
  const c = await b.newContext({viewport: {width: 820, height: 1180}});
  await c.clock.install({time: S.base});
  await c.clock.pauseAt(S.base + 1000);
  const p = await c.newPage(), out = {ev: [], dialogs: [], docs: [], card: [], runs: []};
  let pending = 0, T = S.base + 1000, round = 0;
  p.on('dialog', async d => { out.dialogs.push(d.type() + ':' + d.message()); await d.accept(); });
  const html = (t, kind) => t.split('__KIND__').join(kind).split('__ME__').join(S.me || '').split('__LOGGED__').join(S.me ? 'true' : 'false').split('__TEXT__').join(S.text);
  await c.route('**/*', async route => {
    pending++;
    try {
      const u = new URL(route.request().url());
      if (u.pathname === '/dashboard/api/seoi_act') {
        const r = await rpc({now: T, body: route.request().postData() || '', origin: route.request().headers()['origin'] || ''});
        return await route.fulfill({status: r.status, headers: r.headers, body: r.body});
      }
      if (u.hostname === 'nid.naver.com') return await route.fulfill({contentType: 'text/html', body: S.login});
      if (u.hostname !== 'm.blog.naver.com') return await route.fulfill({contentType: 'text/html', body: html(S.home, '')});
      if (u.pathname === '/__ev') { out.ev.push(Object.fromEntries(u.searchParams)); return await route.fulfill({status: 204, body: ''}); }
      if (route.request().resourceType() === 'document') out.docs.push({u: u.pathname + u.search, t: T});
      if (u.pathname === '/BuddyAddForm.naver') {
        const kinds = (S.rounds && S.rounds[round]) || S.buddies || {};
        return await route.fulfill({contentType: 'text/html', body: html(S.buddy, kinds[u.searchParams.get('blogId')] || 'ok')});
      }
      const m = /^\/([a-z0-9_-]+)\/(\d+)$/.exec(u.pathname);
      if (m) return await route.fulfill({contentType: 'text/html', body: html(S.post, (S.posts || {})[m[1] + '/' + m[2]] || 'ok')});
      return await route.fulfill({contentType: 'text/html', body: html(S.home, '')});
    } finally { pending--; }
  });
  const tick = async ms => { for (let i = 0; i < 400 && pending; i++) await real(3); await c.clock.runFor(ms); T += ms; await real(2); };
  // 화면에 보이는 것: 아래 칸(요약) · 사람이 누를 수 있는 단추·링크·입력 칸 (이 화면에 책갈피가 만든 것 — 작은 창 안의 네이버 화면은 빼고)
  const look = () => p.evaluate(() => {
    const cd = document.getElementById('sb1card'), vis = n => { const s = getComputedStyle(n); return s.display !== 'none' && s.visibility !== 'hidden' && n.getClientRects().length > 0; };
    return {card: cd ? cd.textContent : null, busy: window.__sb1 || 0, ask: Array.from(document.querySelectorAll('button,a[href],input,select,textarea')).filter(vis).map(n => n.textContent || n.type),
      frames: document.querySelectorAll('iframe').length, url: location.href};
  });
  await p.goto(S.start);
  for (; round < (S.presses || 1); round++) {
    const seen = [];
    p.evaluate(code).catch(e => { out.error = String(e); });   // 책갈피를 한 번 누름 — 그 뒤로는 아무것도 누르지 않음
    let v = null, sec = 0, last = '';
    for (; sec < S.max_s; sec++) {
      await tick(1000);
      const w = await look().catch(() => null);
      if (!w) continue;   // 화면을 옮기는 중
      v = w;
      if (v.ask.length) seen.push(v.ask.join('|'));
      if (v.card && v.card !== last && !/하는 중/.test(v.card)) out.card.push(v.card);
      last = v.card || last;
      if (!v.busy && sec > 2) break;
    }
    out.runs.push({sec, last: v, asked: Array.from(new Set(seen)), ls: await p.evaluate(() => localStorage.getItem('sb1')).catch(() => null)});
    if (S.reload && round + 1 < (S.presses || 1)) { await p.goto(S.start); await tick(60000); }
  }
  process.stdout.write('OUT ' + JSON.stringify(out) + '\n');
  await b.close();
  process.exit(0);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(2); });
"""


async def drive1(bot: DealBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]], scn: dict[str, Any]) -> dict[str, Any]:
    """가짜 네이버 화면에서 '한 번에' 책갈피를 누른다 — 대시보드는 진짜 api 길(bot.web.dispatch), 시계는 화면의 가짜 시계 (test_dash_seoi_auto.drive 와 같게)."""
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    day = tz_now(bot).date().isoformat()
    base = datetime.fromisoformat(f"{day}T09:00:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()
    clock = Clock(base)
    monkeypatch.setattr(seoi, "time", clock)
    monkeypatch.setattr(api_mod, "time", clock)
    d = SeqDrafter()
    monkeypatch.setattr(seoi, "get_drafter", lambda: d)
    bot.dashboard.api.seoi_rate = RateWindow(10**6, 600.0)
    list_for(tmp_path, day, rows)
    code = built(bot.db, bot.settings.dashboard.public_url)
    full = {"base": int(base * 1000), "me": "derenpick", "start": "https://m.blog.naver.com/FeedList.naver", "max_s": 6000, "buddy": BUDDY, "post": POST, "home": HOME,
            "login": LOGIN, "text": POST_TEXT_LONG, **scn}
    reqs: list[dict[str, Any]] = []
    out: dict[str, Any] | None = None
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen([NODE, "-e", RUN1, code, json.dumps(full, ensure_ascii=False)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True, env=env)
        try:
            assert proc.stdout is not None and proc.stdin is not None
            for line in proc.stdout:
                if line.startswith("REQ "):
                    r = json.loads(line[4:])
                    clock.t = r["now"] / 1000
                    raw = r["body"].encode()
                    h = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9", "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                         "content-length": str(len(raw)), "sec-fetch-site": "cross-site", "origin": r["origin"]}
                    res = await bot.web.dispatch("POST", seoi.PATH, h, raw)
                    body = res[1].decode() if isinstance(res[1], bytes) else res[1]
                    reqs.append({"t": clock.t - base, "form": dict(parse_qsl(r["body"], keep_blank_values=True)), "status": res[0], "reply": json.loads(body)})
                    proc.stdin.write(json.dumps({"status": res[0], "body": body, "headers": {**res[3], "content-type": "application/json"}}) + "\n")
                    proc.stdin.flush()
                elif line.startswith("OUT "):
                    out = json.loads(line[4:])
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
        err.seek(0)
        assert proc.returncode == 0 and out is not None, err.read()[-3000:]
    assert "error" not in out, out.get("error")
    return {**out, "reqs": reqs, "base": base, "day": day}


def ev(got: dict[str, Any], k: str) -> list[dict[str, str]]:
    return [e for e in got["ev"] if e["k"] == k]


async def test_one_press_does_requests_then_visits_ticks_rows_and_ends_with_a_summary(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """이웃새글에서 한 번 → 서이추 3명(멘트·확인까지) + 들르기 1편(읽기·공감·댓글 등록) → 줄 4개 체크 → 끝 요약. 그동안 누를 것·뜨는 창 0."""
    rows = [nb_row("issue", f"nb{i}", f"안녕하세요 nb{i} 님, 서로이웃 해요!") for i in range(3)] + [visit_row("issue", "vv1", POST_NO), nb_row("finance", "other", "드라마 것")]
    got = await drive1(bot, monkeypatch, tmp_path, rows, {"start": "https://m.blog.naver.com/FeedList.naver#sb1=" + "%ED%86%B5%EA%B3%84%20%EB%B3%B4%EB%83%84%20%E2%9C%93%208%EA%B0%9C%20%ED%99%94%EB%A9%B4"})
    run = got["runs"][0]
    assert got["dialogs"] == [] and run["asked"] == []  # 알림·묻기·적기 창 0 · 화면에 보이는 단추·링크·입력 칸 0 (시작·멈춤도 숨김)
    subs = ev(got, "submit")
    assert sorted(s["id"] for s in subs) == ["nb0", "nb1", "nb2"] and all(s["rel"] == "1" and s["msg"].startswith("안녕하세요 nb") for s in subs)
    assert len(ev(got, "comment")) == 1 and [e["on"] for e in ev(got, "react")] == ["1"]
    # 간격은 대시보드 값 그대로 (책갈피가 줄이지 않음)
    ts = sorted(int(e["t"]) / 1000 for e in subs + ev(got, "comment"))
    assert min(b - a for a, b in zip(ts, ts[1:], strict=False)) >= min(seoi.GAP["add"][0], seoi.GAP["visit"][0])
    # 줄 체크 · 결과 기록 (대시보드 '오늘 자동 결과')
    assert routine.done_ids(bot.db, got["day"]) == {"nb-add:issue:nb0", "nb-add:issue:nb1", "nb-add:issue:nb2", f"nb-visit:issue:vv1:{POST_NO}"}
    t = seoi.today(bot.db, got["base"] + 3000, ZoneInfo(bot.settings.app.timezone))["by"]["issue"]
    assert (t["add"], t["visit"], t["res"]["n"], t["res"]["bad"]) == (3, 1, 4, 0)
    done = [r["form"] for r in got["reqs"] if r["form"].get("a") == "done"]
    assert [d["r"] for d in done] == ["ok"] * 4 and all(r["form"].get("au") == "1" and r["form"]["v"] == str(seoi.version()) for r in got["reqs"] if r["form"].get("a") in ("add", "visit"))
    assert not seoi.is_new(bot.db)  # 이웃 관리 '다시 깔기' 안내도 같이 꺼짐
    card = run["last"]["card"].split("\n")
    assert card[0] == "한 번에 끝 ✓ · 오늘 여기서 서이추 3 · 들르기 1 · 건너뜀 0 · 안 된 것 0"
    assert card[1] == f"오늘 데런픽 자동으로 할 것은 다 했어요 (서이추 3/{seoi.ADD_DAY_CAP} · 들르기 1)" and card[2] == "통계: 최근 7일 받은 통계 0개 — 네이버 내 통계 화면(blog.stat.naver.com)에서 책갈피 · 이번: 통계 보냄 ✓ 8개 화면"  # 서버가 받은 수가 먼저 (11/17)
    assert card[-1] == "결과표는 대시보드 할 일 탭 · 이 칸은 누르면 닫혀요" and not any(ln.startswith(("폰에서 직접", "안 된 것:")) for ln in card)
    assert run["last"]["busy"] == 0 and run["last"]["frames"] == 0  # 끝나면 작은 창도 치움 · 다시 누를 수 있음
    assert got["card"][0] == "한 번에: 서이추·들르기를 차례로 해요 — 화면을 켜 둔 채 이 탭에 두세요"
    assert json.loads(run["ls"])["add"] == 3


async def test_limit_notice_stops_at_once_and_goes_to_by_hand_and_a_second_press_goes_on_where_it_can(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """둘째 사람에서 네이버 한도 안내 → 옛 코드 그대로 바로 멈추고 그날 쉼 (돌아가지 않음) · 요약에 '폰에서 직접'. 새로 고침 뒤 다시 눌러도 쉬는 날이면 아무것도 열지 않음."""
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(4)]
    got = await drive1(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb1": "limit"}, "presses": 2, "reload": 1})
    a, b = got["runs"]
    assert got["dialogs"] == [] and a["asked"] == [] and b["asked"] == []
    assert [s["id"] for s in ev(got, "submit")] == ["nb0"] and [e["id"] for e in ev(got, "tried")] == ["nb1"]  # 한도 안내 뒤로는 한 명도 더 안 함
    assert [r["form"]["w"] for r in got["reqs"] if r["form"].get("a") == "stop"] != []
    card = a["last"]["card"].split("\n")
    assert card[0] == "한 번에 끝 — 다 하지는 못했어요 · 오늘 여기서 서이추 1 · 들르기 0 · 건너뜀 0 · 안 된 것 0"
    assert any(ln.startswith("멈췄어요 — ") and ln.endswith("오늘은 이웃 신청 한도 — 내일 다시 (데런픽)") for ln in card)  # 11/18 — 24시간
    hand = next(ln for ln in card if ln.startswith("폰에서 직접: "))
    assert hand.startswith("폰에서 직접: nb1 — ") and hand.endswith("남은 줄은 대시보드 할 일에서")
    # 다시 누름 (새로 고침 뒤): 대시보드가 '오늘은 이웃 신청 한도 — 내일 다시' → 이웃추가 화면을 하나도 더 열지 않고 요약만 · 오늘 한 수는 이어서 보임
    opened = [d["u"] for d in got["docs"] if d["u"].startswith("/BuddyAddForm")]
    assert opened == ["/BuddyAddForm.naver?blogId=nb0", "/BuddyAddForm.naver?blogId=nb1"]
    card2 = b["last"]["card"].split("\n")
    assert card2[0].startswith("한 번에 끝 — 다 하지는 못했어요 · 오늘 여기서 서이추 1 ·") and any("오늘은 이웃 신청 한도 — 내일 다시 (데런픽)" in ln for ln in card2)


async def test_second_press_after_a_reload_goes_on_with_the_rest_and_one_bad_page_is_skipped(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """한 명이 안 돼도(모르는 화면) 다음으로 넘어가고 끝 요약 '안 된 것'에 남음 · 이미 이웃은 건너뜀. 새로 고침 뒤 다시 누르면 한 것은 다시 하지 않음."""
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(4)]
    got = await drive1(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb1": "weird", "nb2": "already"}, "presses": 2, "reload": 1})
    a, b = got["runs"]
    assert got["dialogs"] == [] and a["asked"] == [] and b["asked"] == []
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb3"]  # 두 번 누르는 동안 같은 사람에게 두 번 신청하지 않음
    card = a["last"]["card"].split("\n")
    assert card[0] == "한 번에 끝 ✓ · 오늘 여기서 서이추 2 · 들르기 0 · 건너뜀 1 · 안 된 것 1"
    assert any(ln.startswith("안 된 것: nb1 — ") for ln in card) and not any(ln.startswith("폰에서 직접") for ln in card)
    card2 = b["last"]["card"].split("\n")
    assert card2[0] == card[0] and len([d for d in got["docs"] if d["u"].startswith("/BuddyAddForm")]) == 4  # 둘째 누름은 화면을 하나도 더 열지 않음


async def test_pc_blog_page_moves_itself_to_the_mobile_feed_without_asking(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got = await drive1(bot, monkeypatch, tmp_path, [nb_row("issue", "nb0", "멘트")], {"start": "https://blog.naver.com/derenpick", "max_s": 20})
    run = got["runs"][0]
    assert got["dialogs"] == [] and run["asked"] == [] and got["reqs"] == []
    assert got["card"][0] == "한 번에: 모바일 이웃새글 화면으로 옮겨요 — 열리면 이 책갈피를 한 번 더" and run["last"]["url"] == seoi.FEED_URL


async def test_unknown_account_ends_with_a_line_instead_of_a_chooser(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got = await drive1(bot, monkeypatch, tmp_path, [nb_row("issue", "nb0", "멘트")], {"me": "someoneelse", "max_s": 30})
    run = got["runs"][0]
    assert got["dialogs"] == [] and run["asked"] == [] and got["reqs"] == [] and ev(got, "submit") == []
    card = run["last"]["card"].split("\n")
    assert card[0].startswith("한 번에 끝 — 다 하지는 못했어요") and "어느 블로그 계정인지 못 알아봤어요" in card[1]


# ---------------------------------------------------------------- 4) 가짜 통계 화면
# 11/18 v3 (홈판 분석 세션: 받은 통계가 시간대·기기·이웃뿐 · 모두 '블로그 모름') — 통계 앱(blog.stat.naver.com/m/…)에서 한 번:
# user-info 의 blogId 로 블로그를 알아보고 조회수 순위·유입분석(일간 · 어제·오늘)은 화면이 부르는 JSON 주소로,
# 검색 유입은 유입분석 화면의 탭을 한 번 눌러 배운 주소로 → 날짜마다 하나씩 보냄 → 스스로 이웃새글로. 못 알아보면 하나도 안 보냄.
ST = "https://blog.stat.naver.com"
SD = lambda did, cols, rows: json.dumps({"statusCode": 200, "message": "", "result": {"statDataList": [{"dataId": did, "data": {"columnInfo": cols, "rows": rows}}]}}, ensure_ascii=False)  # noqa: E731
STAT_APP = ("<!doctype html><html><head><meta charset='utf-8'><title>블로그 통계</title></head><body><div id='c'>불러오는 중</div>__TAB__"
            "<script>const d=new Date(Date.now()-864e5),p=n=>String(n).padStart(2,'0'),day=d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate());"
            "fetch('/api/blog/user-info').then(()=>fetch('/api/blog/user/referer/total?timeDimension=DATE&startDate='+day+'&exclude=&_=1'))"
            ".then(r=>r.json()).then(()=>{document.getElementById('c').textContent='네이버 통합검색_모바일 82%';});"
            "const t=document.getElementById('st');if(t)t.onclick=()=>fetch('/api/blog/user/referer/search?timeDimension=WEEK&startDate=2026-11-09&exclude=&_=2');</script></body></html>")
PW3 = r"""
const {chromium} = require('playwright');
(async () => {
  const [code, sj] = process.argv.slice(1), S = JSON.parse(sj), out = {dialogs: [], bodies: [], cards: [], api: []};
  const b = await chromium.launch(), c = await b.newContext(), p = await c.newPage();
  await p.route('**/*', async r => {
    const u = new URL(r.request().url());
    if (u.hostname === 'dash.example') { out.bodies.push(r.request().postData() || '');
      return r.fulfill({status: 200, contentType: 'application/json', headers: {'Access-Control-Allow-Origin': '*'}, body: JSON.stringify({ok: true, label: 'x'})}); }
    if (u.pathname.startsWith('/api/')) { out.api.push(u.pathname + u.search);
      const j = (S.bad || []).some(x => (u.pathname + u.search).indexOf(x) >= 0) ? '' : S.api[u.pathname];
      return j ? r.fulfill({status: 200, contentType: 'application/json', body: j}) : r.fulfill({status: 404, contentType: 'text/html', body: 'no'}); }
    if (u.hostname === 'blog.stat.naver.com' && u.pathname.endsWith('/blog/user/referer/total')) return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: S.page});
    return r.fulfill({status: 200, contentType: 'text/html; charset=utf-8', body: '<p>다른 화면</p>'});
  });
  p.on('dialog', async d => { out.dialogs.push(d.type() + ':' + d.message()); await d.accept(); });
  await p.goto(S.start);
  await p.waitForTimeout(500);
  p.evaluate(code).catch(() => {});
  let last = '';
  for (let i = 0; i < 1800 && new URL(p.url()).hostname === new URL(S.start).hostname; i++) {
    const v = await p.evaluate(() => { const cd = document.getElementById('sb1card'); return cd ? cd.textContent : ''; }).catch(() => '');
    if (v && v !== last) out.cards.push(last = v);
    await p.waitForTimeout(100);
  }
  out.end = p.url();
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _pw3(db: Any, blog_id: str, *, tab: bool = True, start: str = ST + "/m/blog/user/referer/total", bad: tuple[str, ...] = ()) -> dict[str, Any]:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    api = {"/api/blog/user-info": json.dumps({"baUserKey": "x", "blogId": blog_id}),
           "/api/blog/rank/cvContentPc": SD("rankCv", ["date", "uri", "cv", "rank", "title"],
                                           {"date": ["2026-11-17"], "uri": ["https://blog.naver.com/derenpick/224"], "cv": [30], "rank": [1], "title": ["에이티즈 3년 연속 대상 받은 이유"]}),
           "/api/blog/user/referer/total": SD("refererTotal", ["date", "referrerDomain", "cv_p"], {"date": ["2026-11-17"], "referrerDomain": ["네이버 통합검색_모바일"], "cv_p": [82.1]}),
           "/api/blog/user/referer/search": SD("refererSearch", ["date", "keyword", "cv"], {"date": ["2026-11-17"], "keyword": ["에이티즈 대상"], "cv": [7]})}
    page = STAT_APP.replace("__TAB__", "<ul><li><a href='#' id='st' role='tab'>검색 유입</a></li></ul>" if tab else "")
    res = subprocess.run([NODE, "-e", PW3, built(db), json.dumps({"api": api, "page": page, "start": start, "bad": list(bad)})], capture_output=True, text=True, timeout=300, env=env)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    got["sent"] = [dict(parse_qsl(x, keep_blank_values=True)) for x in got["bodies"]]
    return got


@needs_node
def test_stats_app_sends_rank_referer_and_search_by_day_with_the_blog_and_moves_to_the_feed() -> None:
    """11/25 v4: 지난 7일 + 오늘 (8일) · 조회수 순위는 주간(그 주 월요일 · 지난주·이번 주)·월간(그달 1일 · 지난달·이번 달)도."""
    db = FakeDb()
    got = _pw3(db, "derenpick")
    assert got["dialogs"] == []
    sent = got["sent"]
    assert [f["n"] for f in sent] == (["조회수 순위"] * 8 + ["유입분석"] * 8 + ["조회수 순위 · 주간"] * 2 + ["조회수 순위 · 월간"] * 2
                                      + ["검색 유입"] * 8), [f["n"] for f in sent]
    days = [f["d"] for f in sent[:8]]
    assert days == sorted(days) and len(set(days)) == 8 and [f["d"] for f in sent[8:16]] == days and [f["d"] for f in sent[20:]] == days
    d0, d7 = (datetime.strptime(d, "%Y-%m-%d") for d in (days[0], days[-1]))
    assert (d7 - d0).days == 7  # 오늘과 지난 7일
    wk, mo = [f["d"] for f in sent[16:18]], [f["d"] for f in sent[18:20]]
    wd = [datetime.strptime(d, "%Y-%m-%d") for d in wk]
    assert all(w.weekday() == 0 for w in wd) and (wd[1] - wd[0]).days == 7 and wd[1] <= d7 and (d7 - wd[1]).days < 7  # 지난주·이번 주 월요일
    assert all(d.endswith("-01") for d in mo) and mo[1][:7] == days[-1][:7] and mo[0] < mo[1]  # 지난달·이번 달 1일
    assert all(f["k"] == statscap.key(db) and f["b"] == "derenpick" for f in sent)
    # 화면이 부르는 같은 출처 JSON 주소 그대로 · 일간 · 그날 · 검색 유입은 탭을 눌러 배운 주소 (주간 → 일간)
    assert sent[0]["u"] == f"{ST}/api/blog/rank/cvContentPc?timeDimension=DATE&startDate={days[0]}&exclude="
    assert sent[16]["u"] == f"{ST}/api/blog/rank/cvContentPc?timeDimension=WEEK&startDate={wk[0]}&exclude="
    assert sent[19]["u"] == f"{ST}/api/blog/rank/cvContentPc?timeDimension=MONTH&startDate={mo[1]}&exclude="
    assert sent[20]["u"] == f"{ST}/api/blog/user/referer/search?timeDimension=DATE&startDate={days[0]}&exclude="
    assert sum(a.startswith("/api/blog/user/referer/search") for a in got["api"]) == 9  # 탭 한 번 + 8일
    now = datetime.strptime(days[-1] + " 21:00", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul")).timestamp()
    recs = [statscap.save(db, f, now=now, tz=ZoneInfo("Asia/Seoul")) for f in sent]
    assert {r["blog"] for r in recs} == {"issue"} and [r["day"] for r in recs] == [f["d"] for f in sent]
    assert [r["kind"] for r in recs[16:20]] == ["조회수 순위 · 주간"] * 2 + ["조회수 순위 · 월간"] * 2
    rank = json.loads(next(v for k, v in sorted(db.kv.items()) if k.startswith(statscap.PREFIX) and "조회수 순위" in v))
    assert rank["rows"][0][0] == ["date", "uri", "cv", "rank", "title"] and rank["rows"][0][1][4] == "에이티즈 3년 연속 대상 받은 이유"
    md = lambda d: f"{int(d[5:7])}/{int(d[8:10])}"  # noqa: E731
    mn = lambda d: f"{int(d[5:7])}월"  # noqa: E731
    assert got["end"].startswith(seoi.FEED_URL + "#sb1=")
    end = unquote(got["end"].split("#sb1=")[1])
    span = f"{md(days[0])}~{md(days[-1])} 8개"
    assert end == (f"통계 보냄 ✓ 데런픽 28개 — 조회수 순위 {span}, 유입분석 {span}, 검색 유입 {span}, "
                   f"주간 순위 {md(wk[0])}·{md(wk[1])}, 월간 순위 {mn(mo[0])}·{mn(mo[1])}"), end
    assert got["cards"][0] == "한 번에: 통계를 모아 보내요 — 이 탭을 켜 두세요"
    line = statscap.received(db, now, ZoneInfo("Asia/Seoul"))["line"]
    assert f"세 화면: 데런픽 {md(days[-2])} 조회수 순위·유입분석·검색 유입 ✓ / 데런픽 {md(days[-1])} 조회수 순위·유입분석·검색 유입 ✓" in line, line
    assert f"순위 주간·월간: 데런픽 주간 {md(wk[0])}~·{md(wk[1])}~ · 월간 {mn(mo[0])}·{mn(mo[1])}" in line, line


@needs_node
def test_stats_app_weekly_monthly_rank_that_the_app_does_not_answer_is_only_noted() -> None:
    """주간·월간 순위 주소(WEEK·MONTH)는 다른 통계 화면에서만 본 꼴 — 통계 앱이 통계 꼴로 답하지 않으면 '못 읽음'으로만 (다른 주소로 안 돌아감)."""
    got = _pw3(FakeDb(), "demiyum", bad=("cvContentPc?timeDimension=WEEK", "cvContentPc?timeDimension=MONTH"))
    assert [f["n"] for f in got["sent"]] == ["조회수 순위"] * 8 + ["유입분석"] * 8 + ["검색 유입"] * 8
    assert sum("timeDimension=WEEK" in a and "rank" in a for a in got["api"]) == 2 and sum("timeDimension=MONTH" in a for a in got["api"]) == 2
    end = unquote(got["end"].split("#sb1=")[1])
    assert end.startswith("통계 보냄 ✓ 라커룸 24개 — ") and " · 못 읽음: 주간 순위 " in end and ", 월간 순위 " in end, end


@needs_node
def test_stats_app_sends_nothing_when_the_blog_is_not_ours_and_says_which_view_failed() -> None:
    db = FakeDb()
    got = _pw3(db, "someoneelse")
    assert got["sent"] == [] and not any(a.startswith("/api/blog/rank") for a in got["api"])
    end = unquote(got["end"].split("#sb1=")[1])
    assert end.startswith("어느 블로그인지 못 알아봐서 안 보냈어요 (someoneelse) — 데런픽·드라마·라커룸 계정으로"), end
    assert any(c.startswith("한 번에: 통계 못 보냄: 어느 블로그인지 못 알아봐서") for c in got["cards"]), got["cards"]
    # 검색 유입 탭이 없으면 그것만 '못 읽음'으로 (나머지는 보냄)
    got = _pw3(FakeDb(), "demiyum", tab=False)
    assert [f["n"] for f in got["sent"]] == ["조회수 순위"] * 8 + ["유입분석"] * 8 + ["조회수 순위 · 주간"] * 2 + ["조회수 순위 · 월간"] * 2 and all(f["b"] == "demiyum" for f in got["sent"])
    end = unquote(got["end"].split("#sb1=")[1])
    assert end.startswith("통계 보냄 ✓ 라커룸 20개 — ") and end.endswith(" · 못 읽음: 검색 유입 새 주소 없음"), end  # 탭이 없으면 '통합검색' 줄을 눌러 봄 — 새 주소가 없으면 그렇게


@needs_node
def test_admin_stats_screen_moves_itself_to_the_stats_app() -> None:
    got = _pw3(FakeDb(), "derenpick", start="https://admin.blog.naver.com/derenpick/stat/today")
    assert got["dialogs"] == [] and got["sent"] == [] and got["end"] == ST + "/m/blog/user/referer/total"


@needs_node
def test_stats_screen_on_a_blog_address_goes_to_stats_not_neighbours() -> None:
    """11/17 점검 (11/16 통계가 서버에 하나도 없음): 블로그 주소 안의 통계 화면(모바일 '내 통계')도 통계로 — 이웃새글·블로그 글은 그대로 이웃 관리."""
    src = one.SRC.read_text(encoding="utf-8")
    cond = re.search(r"^if \((/\(\^\|\\\.\)stat.*)\) \{$", src, re.M)
    assert cond, "통계 화면 조건 줄"
    js = ("const f=(h,p,t)=>{const L={hostname:h,pathname:p},D={title:t};return !!(" + cond.group(1) + ")};"
          "console.log(JSON.stringify([f('blog.stat.naver.com','/blog/user/referer/total',''),f('admin.blog.naver.com','/derenpick/stat/today',''),"
          "f('m.blog.naver.com','/derenpick/stat',''),f('m.blog.naver.com','/Statistics.naver',''),f('m.blog.naver.com','/x','통계 : 네이버 블로그'),"
          "f('m.blog.naver.com','/FeedList.naver',''),f('m.blog.naver.com','/statlover/2240',''),f('m.blog.naver.com','/derenpick/224','통계로 본 겨울 : 네이버 블로그'),"
          "f('blog.naver.com','/derenpick','')]))")
    res = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert json.loads(res.stdout) == [True, True, True, True, True, False, False, False, False], res.stderr
