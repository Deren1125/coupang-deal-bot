"""'오늘 자동 결과' 표 (주인 11/15 14:1x — '책갈피 자동 한 바퀴가 끝난 뒤 좋아요가 잘 눌렸는지·댓글이 잘 달렸는지 대시보드에서 검수').

1) 적기 (seoi.act a=done → seoi.record): 책갈피가 실어 보낸 공감·댓글 낱말(l·c) 그대로 · 옛 책갈피(칸 없음)는 서버가 아는 것(그 글의 표)으로 ·
   아이디·글 번호·결과 낱말·시각만 (글 본문·제목·댓글 글은 적지 않음) · 같은 사람·글은 마지막 것만 · 30일.
2) 보이기 (seoi.results → today()["by"][블로그]["res"] · routine.view · 연결 탭 seoi.view()["go"]): 문제 줄이 위 · 블로그마다 따로.
3) 화면 (static/seoires.js — 가짜 DOM): 접는 칸 '오늘 자동 결과 n' · '문제만 보기' · 글 열기는 네이버 블로그 주소만 (홈 화면 앱이면 사파리) · 글자는 텍스트 노드.
4) 진짜 크롬의 가짜 네이버 화면에서 책갈피 자동 한 바퀴 → 표 (playwright 가 있을 때만).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dealbot.app import DealBot
from dealbot.dashboard import routine, seoi
from test_dash_nbvisit import list_for, visit_row
from test_dash_routine import tz_now
from test_dash_seoi import (
    DRAFT,
    NODE,
    NOW,
    POST_TEXT,
    STATIC,
    TZ,
    FakeDrafter,
    Rng,
    _post,
    add_form,
    nb_row,
    needs_node,
    visit_form,
)
from test_dash_seoi_auto import (  # noqa: F401 — bot·_no_live_threads 는 fixture
    DAY,
    POST_NO,
    SeqDrafter,
    _no_live_threads,
    acts,
    bot,
    done_form,
    drive,
)
from test_dash_statscap import FakeDb

NO = 223456789012


async def add(db: FakeDb, target: str, r: str, at: float, blog: str = "issue", **kw: str) -> None:
    await seoi.act(db, {**done_form(db, blog, target, r), **kw}, tz=TZ, now=at, rows=[], day=DAY, rng=Rng())


async def visit(db: FakeDb, no: int, r: str, at: float, *, ask: bool = True, drafter: Any = None, blog: str = "issue", title: str = "수원 직관 후기", **kw: str) -> None:
    """들르기 한 편: (ask 면) 자동이 허락을 받고(표가 생김) → a=done. kw = 책갈피가 실어 보내는 l·c (없으면 옛 책갈피)."""
    if ask:
        got = await seoi.act(db, {**visit_form(db, blog, no, t=title), "au": "1"}, tz=TZ, now=at, drafter=drafter or FakeDrafter(), rng=Rng())
        assert got["ok"] is True
    await seoi.act(db, {**done_form(db, blog, "neighbor1", r, "visit", str(no)), **kw}, tz=TZ, now=at + 5, rows=[], day=DAY, rng=Rng())


def table(db: FakeDb, blog: str = "issue", at: float = NOW + 9000) -> dict[str, Any]:
    return seoi.today(db, at, TZ)["by"][blog]["res"]


def cells(r: dict[str, Any]) -> tuple[str, str, str, str, bool]:
    return r["who"], r["what"], r["like"], r["cmt"], r["bad"]


# ---------------------------------------------------------------- 1) 적기 · 2) 보이기
async def test_done_with_like_and_comment_words_becomes_a_row_and_trouble_comes_first() -> None:
    db = FakeDb()
    await add(db, "nb1", "ok", NOW, l="-", c="-")
    await add(db, "nb2", "unsure", NOW + 10, l="-", c="-")
    await add(db, "nb3", "skip", NOW + 20, l="-", c="-")
    await visit(db, NO, "ok", NOW + 200, l="ok", c="ok")
    await visit(db, NO + 1, "fail", NOW + 400, l="fail", c="none")
    await visit(db, NO + 2, "fail", NOW + 600, l="had", c="fail")
    await visit(db, NO + 3, "ok", NOW + 800, l="no", c="none")
    await visit(db, NO + 4, "ok", NOW + 1000, l="had", c="kept")
    t = table(db)
    assert (t["n"], t["bad"]) == (8, 3)
    rows = t["rows"]
    # 문제 줄이 위 (그 안에서는 새것이 위) → 그 아래 나머지
    assert [cells(r) for r in rows[:3]] == [("neighbor1", "들르기", "✅", "❌", True), ("neighbor1", "들르기", "❌", "건너뜀", True), ("nb2", "서이추", "–", "–", True)]
    assert rows[0]["res"] == "실패 — 등록을 눌렀는데 내 댓글이 안 보임" and rows[1]["res"] == "실패 — 공감을 눌렀는데 표시가 안 바뀜"
    assert rows[2]["res"].startswith("확인 필요 — '확인'을 눌렀는데")
    assert [cells(r) for r in rows[3:]] == [("neighbor1", "들르기", "✅", "건너뜀", False), ("neighbor1", "들르기", "–", "건너뜀", False),
                                           ("neighbor1", "들르기", "✅", "✅", False), ("nb3", "서이추", "–", "–", False), ("nb1", "서이추", "–", "–", False)]
    assert [r["res"] for r in rows[3:]] == ["들르기 완료 — 댓글 칸에 쓰던 글이 있었음", "들르기 완료 — 공감은 누르지 않는 글", "들르기 완료", "이미 이웃·신청 중 (건너뜀)", "신청 완료"]
    # 글 열기 주소 = 모바일 블로그 (서이추는 블로그 · 들르기는 그 글) · 시각은 한국 시간 시:분
    assert rows[-1]["url"] == "https://m.blog.naver.com/nb1" and rows[0]["url"] == f"https://m.blog.naver.com/neighbor1/{NO + 2}"
    assert all(len(r["hm"]) == 5 and r["hm"][2] == ":" for r in rows) and set(rows[0]) == {"who", "what", "like", "cmt", "res", "bad", "url", "hm"}
    # 다른 블로그 표에는 안 섞임
    assert table(db, "finance") == {"n": 0, "bad": 0, "rows": []} and table(db, "hotdeal")["n"] == 0


async def test_only_ids_result_words_and_time_are_stored() -> None:
    """남의 글 본문·제목·내 댓글 글은 kv 에 남지 않는다 (아이디·글 번호·결과 낱말·시각만)."""
    db = FakeDb()
    await visit(db, NO, "ok", NOW, l="ok", c="ok")
    await add(db, "nb1", "ok", NOW + 100, l="<script>", c="x" * 500)
    raw = db.kv[f"seoi:res:{DAY}"]
    items = json.loads(raw)["items"]
    assert [set(x) for x in items] == [{"b", "k", "t", "n", "r", "l", "c", "at"}] * 2
    assert items[0] == {"b": "issue", "k": "visit", "t": "neighbor1", "n": str(NO), "r": "ok", "l": "ok", "c": "ok", "at": int(NOW + 5)}
    assert items[1]["l"] == "-" and items[1]["c"] == "-" and items[1]["n"] == ""  # 서이추에는 공감·댓글이 없음 — 온 값은 버림
    assert DRAFT[:10] not in raw and POST_TEXT[:10] not in raw and "수원" not in raw and "script" not in raw
    # 모르는 낱말은 '모름'(❓ · 문제 줄)으로 — 온 글자를 그대로 두지 않음
    await visit(db, NO + 1, "ok", NOW + 300, l="<b>", c="ok")
    assert "<b>" not in db.kv[f"seoi:res:{DAY}"] and cells(table(db)["rows"][0]) == ("neighbor1", "들르기", "❓", "✅", True)


async def test_old_bookmarklet_without_the_words_is_filled_from_what_the_server_knows() -> None:
    """옛 책갈피는 done 에 공감·댓글 칸이 없다 → r=ok 면 그 글에 허락한 표(공감 여부·초안 유무) 그대로 · 실패면 '모름' · 허락 전에 끝났으면 '–'."""
    db = FakeDb()
    await visit(db, NO, "ok", NOW)  # 초안이 있었고 끝까지 잘 됨
    await visit(db, NO + 1, "ok", NOW + 200, drafter=FakeDrafter(skip=True))  # 댓글 달 글이 아님 — 공감만
    await visit(db, NO + 2, "ok", NOW + 400, title="부고를 전합니다")  # 슬픈 소식 — 공감·댓글 안 함
    await visit(db, NO + 3, "fail", NOW + 600)  # 어느 쪽이 안 됐는지 옛 책갈피는 알려 주지 않음
    await visit(db, NO + 4, "fail", NOW + 800, ask=False)  # 글을 못 읽어 허락도 안 받음
    await visit(db, NO + 5, "login", NOW + 900, ask=False)
    await add(db, "nb1", "ok", NOW + 1000)
    got = {r["url"].rsplit("/", 1)[-1]: r for r in table(db)["rows"]}
    assert cells(got[str(NO)])[2:] == ("✅", "✅", False) and cells(got[str(NO + 1)])[2:] == ("✅", "건너뜀", False)
    assert cells(got[str(NO + 2)])[2:] == ("–", "건너뜀", False)
    assert cells(got[str(NO + 3)])[2:] == ("❓", "❓", True) and got[str(NO + 3)]["res"] == "실패 — 공감·댓글 가운데 안 된 것이 있어요 — 글을 열어 확인"
    assert cells(got[str(NO + 4)])[2:] == ("–", "–", True) and got[str(NO + 4)]["res"].startswith("실패 — 글 화면이 예상과 달랐거나")
    assert cells(got[str(NO + 5)])[2:] == ("–", "–", True) and got[str(NO + 5)]["res"] == "로그인 화면이 나와 멈춤"
    assert cells(got["nb1"]) == ("nb1", "서이추", "–", "–", False)
    assert [r["bad"] for r in table(db)["rows"]] == [True] * 3 + [False] * 4
    # 다른 블로그가 받은 표는 이 블로그의 줄을 채우지 않음 (그 블로그는 그 글에서 아무것도 안 했음) · 망가진 줄은 버림
    await seoi.act(db, done_form(db, "hotdeal", "neighbor1", "ok", "visit", str(NO)), tz=TZ, now=NOW + 20, rows=[], day=DAY, rng=Rng())
    assert cells(table(db, "hotdeal")["rows"][0])[2:] == ("–", "–", False)
    v = json.loads(db.kv_get(f"seoi:res:{DAY}"))
    db.kv_set(f"seoi:res:{DAY}", json.dumps({"items": [*v["items"], {"b": ["issue"], "k": "add", "t": "nb2", "r": "ok"}, "x"]}))
    assert table(db)["n"] == 7 and table(db, "hotdeal")["n"] == 1


async def test_same_target_keeps_the_last_row_and_old_days_go_after_thirty() -> None:
    db = FakeDb()
    db.kv_set("seoi:res:2026-10-01", json.dumps({"items": [{"b": "issue", "k": "add", "t": "old1", "n": "", "r": "ok", "l": "-", "c": "-", "at": 1}]}))
    db.kv_set("seoi:res:2026-11-13", json.dumps({"items": [{"b": "issue", "k": "add", "t": "yest1", "n": "", "r": "fail", "l": "-", "c": "-", "at": int(NOW - 86400)}]}))
    await add(db, "nb1", "fail", NOW)
    await add(db, "nb1", "ok", NOW + 50)  # 같은 사람 — 마지막 것만
    await add(db, "nb1", "ok", NOW + 60, blog="finance")  # 다른 블로그의 같은 아이디는 따로
    assert "seoi:res:2026-10-01" not in db.kv and "seoi:res:2026-11-13" in db.kv  # 30일
    t = table(db)
    assert (t["n"], t["bad"]) == (1, 0) and t["rows"][0]["res"] == "신청 완료" and table(db, "finance")["n"] == 1  # 어제 것은 오늘 표에 없음
    # 하루 줄 수 상한 · 깨진 값은 빈 표
    db.kv_set(f"seoi:res:{DAY}", json.dumps({"items": [{"b": "issue", "k": "add", "t": f"nb{i:04d}", "n": "", "r": "ok", "l": "-", "c": "-", "at": int(NOW)} for i in range(seoi.RES_MAX + 20)]}))
    await add(db, "last1", "ok", NOW + 70)
    items = json.loads(db.kv[f"seoi:res:{DAY}"])["items"]
    assert len(items) == seoi.RES_MAX and items[-1]["t"] == "last1"
    db.kv_set(f"seoi:res:{DAY}", "{broken")
    assert table(db) == {"n": 0, "bad": 0, "rows": []}
    db.kv_set(f"seoi:res:{DAY}", json.dumps({"items": ["x", {"b": "issue", "k": "visit", "t": "a<b", "n": "1", "r": "ok"}, {"b": "nope", "k": "add", "t": "nb1", "r": "ok"},
                                                      {"b": "issue", "k": "add", "t": "nb1", "r": "weird"}]}))
    assert table(db)["n"] == 0  # 모양이 다른 줄은 버림 (주소에 넣을 아이디·글 번호는 다시 확인)


async def test_bad_done_is_still_refused_and_a_failed_record_does_not_break_done(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDb()
    with pytest.raises(seoi.CaptureError):
        await seoi.act(db, {**done_form(db, "issue", "nb1", "weird"), "l": "ok", "c": "ok"}, tz=TZ, now=NOW, rows=[], day=DAY)
    assert f"seoi:res:{DAY}" not in db.kv

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("kv down")
    monkeypatch.setattr(seoi, "record", boom)
    got = await seoi.act(db, done_form(db, "issue", "nb1", "ok"), tz=TZ, now=NOW, rows=[nb_row("issue", "nb1", "멘트")], day=DAY, rng=Rng())
    assert got == {"ok": True, "row": True, "text": "적었어요"} and "issue:nb1" in seoi._counts(db, DAY)["tried"]


async def test_connect_tab_gets_a_link_to_the_blog_with_most_trouble() -> None:
    db = FakeDb()
    assert "go" not in seoi.view(db, "https://dash.example", now=NOW, tz=TZ)  # 오늘 결과가 없으면 링크도 없음
    await add(db, "nb1", "ok", NOW)
    assert seoi.view(db, "https://dash.example", now=NOW + 100, tz=TZ)["go"] == {"t": "오늘 자동 결과 1 — 할 일 탭에서 보기", "h": "#/todo/issue"}
    await add(db, "nb2", "fail", NOW + 10, blog="hotdeal")
    await add(db, "nb3", "ok", NOW + 20)
    assert seoi.view(db, "https://dash.example", now=NOW + 100, tz=TZ)["go"] == {"t": "오늘 자동 결과 3 · 문제 1 — 할 일 탭에서 보기", "h": "#/todo/hotdeal"}
    lg = (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "b.d.go ? el('a', {class: 'sm', href: b.d.go.h}, b.d.go.t) : ''" in lg


async def test_api_done_shows_in_the_todo_view(bot: DealBot, tmp_path: Path) -> None:  # noqa: F811
    """진짜 api 길: 책갈피의 a=done (l·c 실림) → 할 일 탭 값 routine.view()["seoi"]["by"][블로그]["res"]."""
    day = tz_now(bot).date().isoformat()
    list_for(tmp_path, day, [nb_row("hotdeal", "nb1", "직관 글 잘 봤어요"), visit_row("hotdeal", "vv1", POST_NO)])
    o = "https://m.blog.naver.com"
    assert (await _post(bot, {**add_form(bot.db, "hotdeal", "nb1"), "au": "1"}, origin=o))[0] == 200
    assert (await _post(bot, {**done_form(bot.db, "hotdeal", "nb1", "ok"), "l": "-", "c": "-"}, origin=o))[0] == 200
    assert (await _post(bot, {**done_form(bot.db, "hotdeal", "vv1", "fail", "visit", str(POST_NO)), "l": "ok", "c": "nobox"}, origin=o))[0] == 200
    res = routine.view(bot)["seoi"]["by"]["hotdeal"]["res"]
    assert (res["n"], res["bad"]) == (2, 1)
    assert cells(res["rows"][0]) == ("vv1", "들르기", "✅", "❌", True) and res["rows"][0]["res"] == "실패 — 댓글 칸을 못 찾음"
    assert res["rows"][0]["url"] == f"https://m.blog.naver.com/vv1/{POST_NO}" and cells(res["rows"][1]) == ("nb1", "서이추", "–", "–", False)
    assert routine.view(bot)["seoi"]["by"]["issue"]["res"] == {"n": 0, "bad": 0, "rows": []}


# ---------------------------------------------------------------- 3) 화면
def test_screen_files_are_wired_and_safe() -> None:
    sr = (STATIC / "seoires.js").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "fetch(", "XMLHttpRequest", "localStorage"):
        assert bad not in sr, bad
    assert "W.SBSeoiRes = {fold};" in sr and "/^https:\\/\\/m\\.blog\\.naver\\.com\\/" in sr and "href: sf ? sf(u) : u, target: '_blank', rel: 'noopener noreferrer'" in sr
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "seoiRow(k, sv, data.seoi), W.SBSeoiRes && W.SBSeoiRes.fold(k, sv.res, sf));" in rt  # 이웃 들르기 줄 바로 아래 · 사파리로 여는 sf 를 넘김
    bm = (STATIC / "seoi.js").read_text(encoding="utf-8")
    assert "await ask({a: 'done', b: fx, w: cur.no ? 'visit' : 'add', t: cur.id, n: cur.no || '', r: res, l: lr, c: cr});" in bm
    assert "        res = 'fail';\n        lr = cr = '-';\n" in bm  # 한 명·한 편마다 처음부터
    # 책갈피가 보내는 낱말은 모두 서버 표에 있음
    import re

    assert set(re.findall(r"\blr = '([a-z-]+)'", bm)) <= set(seoi.LIKES) and set(re.findall(r"\bcr = '([a-z-]+)'", bm)) <= set(seoi.CMTS)
    assert {"ok", "had", "no", "miss", "fail"} <= set(re.findall(r"\blr = '([a-z-]+)'", bm))
    assert {"ok", "had", "none", "kept", "login", "nobox", "nobtn", "fail"} <= set(re.findall(r"\bcr = '([a-z-]+)'", bm))
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "seoires.js")], capture_output=True).returncode == 0


SHIM = r"""
const fs = require('fs');
class N {
  constructor(t) { this.tag = t; this.kids = []; this.attrs = {}; this.ev = {}; this.className = ''; this.nodeType = 1; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, f) { this.ev[k] = f; }
  append(...c) { for (const x of c) this.kids.push(typeof x === 'string' ? {nodeType: 3, text: x} : x); }
}
const text = n => (n.nodeType === 3 ? n.text : n.kids.map(text).join(''));
const all = (n, f, o = []) => { if (n && n.nodeType === 1) { if (f(n)) o.push(n); n.kids.forEach(k => all(k, f, o)); } return o; };
const head = new N('head'), events = [];
global.document = {createElement: t => new N(t), head, getElementById: id => all(head, n => n.attrs.id === id)[0] || null};
global.window = {dispatchEvent: e => { events.push(e.type); }};
global.Event = class { constructor(t) { this.type = t; } };
(0, eval)(fs.readFileSync(process.argv[1], 'utf8'));
const res = JSON.parse(process.argv[2]), sf = u => 'x-safari-' + u, S = window.SBSeoiRes;
const snap = (k, r, f) => {
  const n = S.fold(k, r, f);
  if (!n) return null;
  const btn = all(n, x => x.tag === 'button');
  return {head: text(btn[0]), open: btn[0].attrs['aria-expanded'], only: btn[1] ? [text(btn[1]), btn[1].attrs['aria-pressed']] : null, k: n.attrs['data-k'],
    th: all(n, x => x.tag === 'th').map(text), rows: all(n, x => x.tag === 'tr').slice(1).map(tr => ({bad: tr.className === 'bd', td: tr.kids.map(text),
      a: all(tr, x => x.tag === 'a').map(a => [a.attrs.href, a.attrs.target, a.attrs.rel, text(a)])})),
    tags: [...new Set(all(n, () => true).map(x => x.tag))].sort(), click: i => btn[i].ev.click()};
};
const out = {};
out.none = [S.fold('issue', null, sf), S.fold('issue', {n: 0, bad: 0, rows: []}, sf), S.fold('issue', {rows: 'x'}, sf)];
let s = snap('issue', res, sf);
out.closed = {...s};
s.click(0);
s = snap('issue', res, sf);
out.opened = {...s};
out.other = snap('finance', res, sf).open;
s.click(1);
s = snap('issue', res, sf);
out.only = {...s};
s.click(1);
out.back = snap('issue', res, sf).rows.length;
out.plain = snap('hotdeal', {rows: [res.rows[2]]}, null);
out.plain.click(0);
out.plain = snap('hotdeal', {rows: [res.rows[2]]}, null);
out.events = events;
out.css = all(head, n => n.tag === 'style').length;
console.log(JSON.stringify(out));
"""


@needs_node
def test_fold_draws_the_table_filters_trouble_and_opens_posts_in_safari() -> None:
    evil = '<img src=x onerror="alert(1)">'
    res = {"n": 5, "bad": 2, "rows": [
        {"who": "vv2", "what": "들르기", "like": "✅", "cmt": "❌", "res": "실패 — 등록을 눌렀는데 내 댓글이 안 보임", "bad": True, "url": f"https://m.blog.naver.com/vv2/{NO}", "hm": "14:05"},
        {"who": evil, "what": "서이추", "like": "–", "cmt": "–", "res": evil, "bad": True, "url": "javascript:alert(1)", "hm": "14:01"},
        {"who": "vv1", "what": "들르기", "like": "✅", "cmt": "✅", "res": "들르기 완료", "bad": False, "url": f"https://m.blog.naver.com/vv1/{NO}", "hm": "13:59"},
        {"who": "nb1", "what": "서이추", "like": "–", "cmt": "–", "res": "신청 완료", "bad": False, "url": "https://m.blog.naver.com/nb1", "hm": "13:55"},
        {"who": "nb9", "what": "서이추", "like": "–", "cmt": "–", "res": "신청 완료", "bad": False, "url": "https://evil.example/m.blog.naver.com/nb9", "hm": ""},
        None, "junk"]}
    out = subprocess.run([NODE, "-e", SHIM, str(STATIC / "seoires.js"), json.dumps(res, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["none"] == [None, None, None]  # 오늘 결과가 없으면 칸도 없음
    c = got["closed"]
    assert c["head"] == "오늘 자동 결과 5 · 문제 2열기" and c["open"] == "false" and c["rows"] == [] and c["k"] == "issue"  # 접힌 채로 — 수와 문제 수만
    o = got["opened"]
    assert o["open"] == "true" and o["th"] == ["이웃", "할 일", "공감", "댓글", "결과", "열기"] and o["only"] == ["문제만 보기 (2)", "false"]
    assert [r["bad"] for r in o["rows"]] == [True, True, False, False, False]  # 서버가 준 차례 그대로 (문제 줄이 위)
    assert o["rows"][0]["td"] == ["vv214:05", "들르기", "✅", "❌", "실패 — 등록을 눌렀는데 내 댓글이 안 보임", "글 열기 ↗"]
    assert o["rows"][0]["a"] == [[f"x-safari-https://m.blog.naver.com/vv2/{NO}", "_blank", "noopener noreferrer", "글 열기 ↗"]]  # 홈 화면 앱이면 사파리로 (routine.js sf)
    assert o["rows"][3]["a"] == [["x-safari-https://m.blog.naver.com/nb1", "_blank", "noopener noreferrer", "블로그 ↗"]]
    # 이상한 값: 글자는 그대로 글자로 (태그가 생기지 않음) · 네이버 블로그 주소가 아니면 링크 없음
    assert o["rows"][1]["td"] == [evil + "14:01", "서이추", "–", "–", evil, "–"] and o["rows"][1]["a"] == [] and o["rows"][4]["a"] == []
    assert "img" not in o["tags"] and "script" not in o["tags"] and got["other"] == "false"  # 블로그마다 따로 접고 펼침
    assert got["only"]["only"] == ["문제만 보는 중 (2) — 모두 보기", "true"] and [r["td"][0] for r in got["only"]["rows"]] == ["vv214:05", evil + "14:01"]
    assert got["back"] == 5 and got["events"] == ["sb-routine"] * 4 and got["css"] == 1  # 누를 때마다 할 일 칸을 다시 그림
    p = got["plain"]
    assert p["head"] == "오늘 자동 결과 1 · 모두 정상접기" and p["only"] is None  # 문제가 없으면 '문제만 보기' 단추도 없음
    assert p["rows"][0]["a"][0][0] == f"https://m.blog.naver.com/vv1/{NO}"


# ---------------------------------------------------------------- 4) 진짜 크롬 — 책갈피 자동 한 바퀴 → 표
async def test_auto_run_fills_the_table_with_like_and_comment_results(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    rows = [nb_row("issue", "nb0", "멘트 0"), visit_row("issue", "vv1", POST_NO), visit_row("issue", "vv2", POST_NO + 1), visit_row("issue", "vv3", POST_NO + 2),
            visit_row("issue", "vv4", POST_NO + 3)]
    posts = {f"vv2/{POST_NO + 1}": "liked", f"vv3/{POST_NO + 2}": "mine slow", f"vv4/{POST_NO + 3}": "lost"}
    got = await drive(bot, monkeypatch, tmp_path, rows, {"posts": posts}, drafter=SeqDrafter())
    sent = {(r["form"]["t"], r["form"]["r"], r["form"]["l"], r["form"]["c"]) for r in acts(got, "done")}
    assert sent == {("nb0", "ok", "-", "-"), ("vv1", "ok", "ok", "ok"), ("vv2", "ok", "had", "ok"), ("vv3", "ok", "ok", "had"), ("vv4", "fail", "ok", "fail")}
    res = seoi.today(bot.db, got["base"] + 3000, TZ)["by"]["issue"]["res"]
    assert (res["n"], res["bad"]) == (5, 1)
    assert cells(res["rows"][0]) == ("vv4", "들르기", "✅", "❌", True) and res["rows"][0]["res"] == "실패 — 등록을 눌렀는데 내 댓글이 안 보임"
    rest = {r["who"]: cells(r)[1:] for r in res["rows"][1:]}
    assert rest == {"nb0": ("서이추", "–", "–", False), "vv1": ("들르기", "✅", "✅", False), "vv2": ("들르기", "✅", "✅", False), "vv3": ("들르기", "✅", "✅", False)}
    assert routine.view(bot)["seoi"]["by"]["issue"]["res"]["n"] == 5
