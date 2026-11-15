"""'이웃 관리' 책갈피 자동 (주인 11/15 09:1x — '한 번 켜 놓으면 이웃 가서 서이추·공감·댓글까지, 대신 시간 간격은 있게') + 공감 고치기 (주인 11/15 09:10 화면).

1) 받기 (seoi.act): a=queue 차례 (그 블로그 줄만 · 체크된 줄·해 본 것 빼고 · 번갈아) · 자동의 간격·한 시간 상한은 서이추·들르기 합쳐서 ·
   a=done (줄 체크 + nb_requested 맡기기 · skip 은 센 것을 되돌림) · a=stop w=all · 책갈피 판 번호 · 한글 줄이기(pack ↔ 책갈피의 Z).
2) 가짜 네이버 화면 (진짜 크롬 · playwright 가 있을 때만 · 가짜 시계 · 대시보드는 진짜 api 길 bot.web.dispatch):
   모바일 이웃추가 화면·글 화면은 11/15 네이버 공개 스크립트(NgTemplate BuddyAddForm.html · reaction.basic.js)의 모양대로 —
   서로이웃 30명 + 들르기 · 중간에 한도 안내 → 바로 멈춤 + 그날 쉼 · 로그인 화면 → 멈춤 · 이미 이웃 → 건너뜀(세지 않음) · 간격·한 시간 15 지킴 · 멈춤/이어하기.
3) 공감 (한 단계 책갈피도 같은 코드): 얼굴 단추는 스크립트로 누르면 반응 고르는 칸만 열림 → 그 안의 '공감'을 누름 · 이미 공감이면 절대 누르지 않음 ·
   표시가 늦게 바뀌어도 기다림 · 댓글 창이 열려 있어도.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
import time as real_time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import api as api_mod
from dealbot.dashboard import routine, seoi
from dealbot.dashboard.actions import RateWindow
from test_dash_kkumi import _playwright_env
from test_dash_nbvisit import list_for, visit_row
from test_dash_routine import tz_now
from test_dash_seoi import (
    DRAFT,
    NODE,
    NOW,
    TZ,
    FakeDrafter,
    Rng,
    add_form,
    nb_row,
    needs_node,
    refused,
    visit_form,
)
from test_dash_statscap import FakeDb

DAY = datetime.fromtimestamp(NOW, TZ).strftime("%Y-%m-%d")
POST_NO = 223456789012


def q(db: FakeDb, blog: str = "issue", **kw: str) -> dict[str, str]:
    return {"k": seoi.key(db), "a": "queue", "b": blog, "v": str(seoi.version()), **kw}


def done_form(db: FakeDb, blog: str, target: str, r: str, w: str = "add", n: str = "") -> dict[str, str]:
    return {"k": seoi.key(db), "a": "done", "b": blog, "w": w, "t": target, "n": n, "r": r, "au": "1"}


def rows3() -> list[dict[str, Any]]:
    return [nb_row("issue", "nb1", "멘트 하나"), nb_row("issue", "nb2", "멘트 둘"), nb_row("finance", "nb9", "드라마 멘트"), nb_row("issue", "derenlog", "우리 블로그"),
            visit_row("issue", "vv1", POST_NO), visit_row("finance", "vv9", POST_NO + 1), {"id": "pub:issue:1"}, "junk"]


# ---------------------------------------------------------------- 1) 받기
async def test_queue_gives_this_blogs_rows_in_turn_and_skips_done_and_tried() -> None:
    db = FakeDb()
    got = await seoi.act(db, q(db), tz=TZ, now=NOW, rows=rows3(), done=set(), day=DAY)
    assert got["ok"] is True and got["next"] == {"id": "nb1", "msg": "멘트 하나"} and (got["add"], got["visit"], got["wait"]) == (2, 1, 0)
    assert got["text"] == f"오늘 이웃 자동 — 데런픽 · 서이추 2명 · 들르기 1편 · 약 {seoi._minutes(3)}분 · 화면을 켜 둔 채 이 탭에 두세요" and got["min"] == seoi._minutes(3)
    # 체크된 줄은 빼고 · 다른 블로그 줄·우리 블로그는 처음부터 없음
    got = await seoi.act(db, q(db), tz=TZ, now=NOW, rows=rows3(), done={"nb-add:issue:nb1"}, day=DAY)
    assert got["next"] == {"id": "nb2", "msg": "멘트 둘"} and got["add"] == 1
    got = await seoi.act(db, q(db, "finance"), tz=TZ, now=NOW, rows=rows3(), done=set(), day=DAY)
    assert got["next"] == {"id": "nb9", "msg": "드라마 멘트"} and (got["add"], got["visit"]) == (1, 1) and "드라마" in got["text"]
    # 서이추를 하나 하면 다음은 들르기 (번갈아) · 기다릴 시간은 대시보드가 정함
    rng = Rng("hi")
    await seoi.act(db, {**add_form(db, "issue", "nb1"), "au": "1"}, tz=TZ, now=NOW, rows=rows3(), rng=rng)
    assert rng.seen == [seoi.GAP["add"]]
    await seoi.act(db, done_form(db, "issue", "nb1", "ok"), tz=TZ, now=NOW + 9, rows=rows3(), day=DAY)
    got = await seoi.act(db, q(db), tz=TZ, now=NOW + 10, rows=rows3(), done={"nb-add:issue:nb1"}, day=DAY)
    assert got["next"] == {"id": "vv1", "no": str(POST_NO)} and got["wait"] == seoi.GAP["add"][1] - 10
    # 해 봤는데 안 된 것(줄은 그대로)도 오늘은 다시 주지 않음
    await seoi.act(db, done_form(db, "issue", "vv1", "fail", "visit", str(POST_NO)), tz=TZ, now=NOW + 200, rows=rows3(), day=DAY)
    got = await seoi.act(db, q(db), tz=TZ, now=NOW + 300, rows=rows3(), done={"nb-add:issue:nb1"}, day=DAY)
    assert got["next"] == {"id": "nb2", "msg": "멘트 둘"} and got["visit"] == 0 and got["wait"] == 0
    # 목록이 오늘 것이 아니면 차례가 없음 · 다 했으면 다 했다고
    got = await seoi.act(db, q(db), tz=TZ, now=NOW, rows=rows3(), done=set(), day=None)
    assert got["next"] is None and got["text"].startswith("오늘 할 일 목록이 아직 없어요")
    got = await seoi.act(db, q(db), tz=TZ, now=NOW, rows=[], done=set(), day=DAY)
    assert got["next"] is None and got["text"] == "오늘 데런픽 자동으로 할 것은 다 했어요 (서이추 1/30 · 들르기 0)"
    e = await refused(db, {**q(db), "k": "wrong"}, NOW)
    assert e.code == "key"
    assert (await refused(db, {**q(db), "b": "nope"}, NOW)).code == "who"


async def test_auto_pace_counts_add_and_visit_together() -> None:
    db, d = FakeDb(), FakeDrafter()
    au = {"au": "1"}
    hi = seoi.GAP["add"][1]
    assert hi > 30
    await seoi.act(db, {**add_form(db, "issue", "nb1"), **au}, tz=TZ, now=NOW, rng=Rng("hi"))  # 서이추 → 다음 허용 GAP['add'] 끝 뒤
    e = await refused(db, {**visit_form(db, "issue"), **au}, NOW + 30, drafter=d)  # 자동: 들르기도 그 간격 안에는 안 됨
    assert (e.status, e.code) == (429, "gap") and e.text.startswith(f"조금 쉬었다가 — {hi - 30}초 뒤에") and d.calls == []
    assert seoi.hold(db, NOW + 30) == hi - 30
    got = await seoi.act(db, visit_form(db, "issue"), tz=TZ, now=NOW + 30, drafter=d)  # 손으로 누른 들르기(한 단계)는 예전대로 종류마다
    assert got["comment"] == DRAFT
    # 한 시간 상한: 서이추 + 들르기 합쳐 HOUR_CAP → 자동은 그 한 시간이 지날 때까지 (어느 한 종류만으로는 상한에 안 닿음)
    db, cap = FakeDb(), seoi.HOUR_CAP
    step = max(hi, seoi.GAP["visit"][1]) + 5
    adds = (cap + 1) // 2
    assert cap * step < 3600 and adds < cap and adds <= seoi.ADD_DAY_CAP and cap - adds <= seoi.VISIT_DAY_CAP
    for i in range(cap):
        f = add_form(db, "issue", f"nb{i}") if i % 2 == 0 else visit_form(db, "issue", POST_NO + i)
        await seoi.act(db, {**f, **au}, tz=TZ, now=NOW + i * step, drafter=d, rng=Rng())
    t = NOW + cap * step
    assert seoi.hold(db, t) == 3600 - cap * step
    assert (await refused(db, {**add_form(db, "issue", "late"), **au}, t)).code == "gap"
    got = await seoi.act(db, q(db), tz=TZ, now=t, rows=[nb_row("issue", "late", "m")], done=set(), day=DAY)
    assert got["wait"] == 3600 - cap * step and got["next"]["id"] == "late"
    assert (await seoi.act(db, {**add_form(db, "issue", "late"), **au}, tz=TZ, now=NOW + 3601))["n"] == adds + 1
    # 하루 상한을 넘는 차례는 주지 않음 (서이추 블로그마다 30)
    db = FakeDb()
    db.kv_set(f"seoi:day:{DAY}", json.dumps({"add": {"issue": 29}}))
    rows = [nb_row("issue", f"nb{i}", "m") for i in range(5)]
    got = await seoi.act(db, q(db), tz=TZ, now=NOW, rows=rows, done=set(), day=DAY)
    assert got["add"] == 1 and seoi.today(db, NOW, TZ, rows=rows, done=set())["by"]["issue"]["auto"] == {"add": 1, "visit": 0, "min": seoi._minutes(1)}


async def test_done_ticks_the_row_hands_nb_requested_and_skip_gives_the_count_back() -> None:
    db = FakeDb()
    rows = rows3()
    await seoi.act(db, {**add_form(db, "issue", "nb1"), "au": "1"}, tz=TZ, now=NOW, rows=rows, rng=Rng("hi"))
    got = await seoi.act(db, done_form(db, "issue", "nb1", "ok"), tz=TZ, now=NOW + 8, rows=rows, day=DAY)
    assert got == {"ok": True, "row": True, "text": "적었어요"}
    assert routine.done_ids(db, DAY) == {"nb-add:issue:nb1"}  # 할 일 줄이 저절로 체크
    (r,) = routine.relay_rows(db)  # 블로그 프로그램에 '신청했어요' — 손으로 체크할 때와 같은 길 (relay_tick 이 맡김)
    assert (r["name"], r["args"], r["rid"]) == ("nb_requested", {"blog": "issue", "target": "nb1", "done": True}, "nb-add:issue:nb1") and "aid" not in r
    assert seoi.today(db, NOW + 8, TZ)["by"]["issue"]["add"] == 1
    # '확인'을 누른 뒤 네이버가 신청 중·상대가 제한이라고 함: 줄은 체크하되 하루 센 것만 되돌림 — 실제로 눌렀으니 간격(GAP['add'])·한 시간 수는 그대로
    await seoi.act(db, {**add_form(db, "issue", "nb2"), "au": "1"}, tz=TZ, now=NOW + 200, rows=rows, rng=Rng("hi"))
    assert seoi.today(db, NOW + 200, TZ)["by"]["issue"]["add"] == 2
    rng, hi = Rng("lo"), seoi.GAP["add"][1]
    assert 210 + seoi.SKIP_GAP[0] < 200 + hi  # 건너뜀 쉼이 간격보다 짧아도 간격이 그대로 남는지 보이게
    await seoi.act(db, done_form(db, "issue", "nb2", "skip"), tz=TZ, now=NOW + 210, rows=rows, day=DAY, rng=rng)
    assert seoi.today(db, NOW + 210, TZ)["by"]["issue"]["add"] == 1 and rng.seen == [seoi.SKIP_GAP] and seoi.hold(db, NOW + 210) == hi - 10
    assert len(seoi._pace(db, "add", NOW + 210)["recent"]) == 2 and routine.done_ids(db, DAY) == {"nb-add:issue:nb1", "nb-add:issue:nb2"}
    await seoi.act(db, done_form(db, "issue", "nb2", "skip"), tz=TZ, now=NOW + 215, rows=rows, day=DAY)  # 두 번 보내도 한 번만 되돌림
    assert seoi.today(db, NOW + 215, TZ)["by"]["issue"]["add"] == 1
    # 세기 전에 건너뛴 사람(표 없음)은 되돌릴 것이 없음 · 줄에 없는 사람·다른 블로그 줄·어제 목록은 체크하지 않음
    before = dict(db.kv)
    for f, kw in ((done_form(db, "issue", "stranger", "ok"), {"day": DAY}), (done_form(db, "issue", "nb9", "ok"), {"day": DAY}),
                  (done_form(db, "finance", "nb9", "ok"), {"day": None})):
        assert (await seoi.act(db, f, tz=TZ, now=NOW + 300, rows=rows, **kw))["row"] is False
    assert routine.done_ids(db, DAY) == {"nb-add:issue:nb1", "nb-add:issue:nb2"} and db.kv["routine_relay_pending"] == before["routine_relay_pending"]
    # 안 된 것(fail·unsure·login·stop)은 적기만 — 줄은 그대로
    assert (await seoi.act(db, done_form(db, "issue", "nb1", "unsure"), tz=TZ, now=NOW + 400, rows=rows, day=DAY))["row"] is False
    for bad in ({"r": "great"}, {"w": "like"}, {"t": "a b"}, {"w": "visit", "n": "12"}):
        assert (await refused(db, {**done_form(db, "issue", "nb1", "ok"), **bad}, NOW + 500)).code == "bad"


async def test_auto_marks_tried_when_allowed_and_refuses_the_same_target_twice() -> None:
    """탭이 닫히거나 새로고침돼 a=done 이 안 와도 오늘은 그 사람·그 글을 다시 주지 않음 · 탭 둘이 같은 차례를 받아도 한 번만 (같은 글에 댓글 두 번 막기)."""
    db, d, au = FakeDb(), FakeDrafter(), {"au": "1"}
    rows = [visit_row("issue", "neighbor1", POST_NO), visit_row("issue", "neighbor1", POST_NO + 1), nb_row("issue", "nb1", "멘트")]
    assert (await seoi.act(db, q(db), tz=TZ, now=NOW, rows=rows, done=set(), day=DAY))["next"] == {"id": "nb1", "msg": "멘트"}
    got = await seoi.act(db, {**visit_form(db, "issue"), **au}, tz=TZ, now=NOW, drafter=d, rng=Rng())
    assert got["comment"] == DRAFT and got["again"] is False
    assert seoi._counts(db, DAY)["tried"] == [f"issue:neighbor1/{POST_NO}"]  # a=done 없이도 적힘
    # 다른 탭(또는 다시 시작한 책갈피)이 그 글을 또 물음: 초안을 다시 주지 않음 — 세지도 쓰지도 않음
    for t in (NOW + 5, NOW + 300):
        e = await refused(db, {**visit_form(db, "issue"), **au}, t, drafter=d)
        assert (e.status, e.code) == (409, "dup")
    assert len(d.calls) == 1 and seoi.today(db, NOW + 300, TZ)["visit"] == 1
    got = await seoi.act(db, q(db), tz=TZ, now=NOW + 700, rows=rows, done=set(), day=DAY)  # 이어 하기 표(10분)가 지나도 오늘은 그 글을 안 줌
    assert got["visit"] == 1 and all(n["id"] != "neighbor1" or n.get("no") != str(POST_NO) for n in [got["next"]])
    # 손으로 누른 한 단계는 예전대로 이어 하기 (같은 초안 · 다시 세지 않음)
    again = await seoi.act(db, visit_form(db, "issue"), tz=TZ, now=NOW + 10, drafter=d)
    assert again["again"] is True and again["comment"] == DRAFT and len(d.calls) == 1
    # 손으로 방금 한 대상을 자동이 물으면 건너뜀 + 오늘은 다시 안 줌 (같은 화면만 되풀이해 열지 않게)
    db = FakeDb()
    await seoi.act(db, visit_form(db, "issue"), tz=TZ, now=NOW, drafter=d)
    assert seoi._counts(db, DAY)["tried"] == []
    assert (await refused(db, {**visit_form(db, "issue"), **au}, NOW + 200, drafter=d)).code == "dup"
    assert seoi._counts(db, DAY)["tried"] == [f"issue:neighbor1/{POST_NO}"]
    got = await seoi.act(db, q(db), tz=TZ, now=NOW + 201, rows=rows[:1], done=set(), day=DAY)
    assert got["next"] is None
    # 서이추도 같음: 허락한 그때 적고, 또 물으면 거절 (세지 않음)
    db = FakeDb()
    await seoi.act(db, {**add_form(db, "issue", "nb1"), **au}, tz=TZ, now=NOW, rows=rows)
    assert seoi._counts(db, DAY)["tried"] == ["issue:nb1"]
    assert (await refused(db, {**add_form(db, "issue", "nb1"), **au}, NOW + 3, rows=rows)).code == "dup"
    assert seoi.today(db, NOW + 3, TZ)["by"]["issue"]["add"] == 1
    assert (await seoi.act(db, q(db), tz=TZ, now=NOW + 4, rows=rows[2:], done=set(), day=DAY))["next"] is None
    # 간격에 걸려 거절된 것은 적지 않음 (다음에 다시 차례)
    assert (await refused(db, {**add_form(db, "issue", "nb2"), **au}, NOW + 5, rows=rows)).code == "gap"
    assert seoi._counts(db, DAY)["tried"] == ["issue:nb1"]


async def test_stop_all_rests_both_kinds_and_queue_says_so() -> None:
    db = FakeDb()
    await seoi.act(db, {"k": seoi.key(db), "a": "stop", "b": "issue", "w": "all"}, tz=TZ, now=NOW)
    e = await refused(db, q(db), NOW + 1, rows=rows3(), done=set(), day=DAY)
    assert (e.status, e.code) == (429, "stopped") and e.text == "오늘은 네이버가 제한 안내를 보여서 데런픽 서이추·들르기를 쉬어요 — 내일 다시"
    assert (await refused(db, {**add_form(db, "issue"), "au": "1"}, NOW + 2)).code == "stopped"
    assert (await seoi.act(db, q(db, "finance"), tz=TZ, now=NOW + 3, rows=rows3(), done=set(), day=DAY))["next"]["id"] == "nb9"  # 다른 블로그는 그대로
    assert "네이버 제한 안내로 오늘 서이추·들르기 쉼" in seoi.today(db, NOW + 3, TZ)["by"]["issue"]["line"]
    # 서이추만 쉬는 날이면 들르기 차례만
    db = FakeDb()
    await seoi.act(db, {"k": seoi.key(db), "a": "stop", "b": "issue", "w": "add"}, tz=TZ, now=NOW)
    got = await seoi.act(db, q(db), tz=TZ, now=NOW + 1, rows=rows3(), done=set(), day=DAY)
    assert got["next"] == {"id": "vv1", "no": str(POST_NO)} and (got["add"], got["visit"]) == (0, 1)
    assert (await refused(db, {"k": seoi.key(db), "a": "stop", "b": "issue", "w": "everything"}, NOW)).code == "bad"


async def test_new_version_note_goes_away_once_the_new_bookmarklet_asks() -> None:
    db = FakeDb()
    assert seoi.is_new(db) and seoi.view(db, "https://dash.example", tz=TZ)["note"].startswith("'이웃 관리' 책갈피가 바뀌었어요 (v2")
    await seoi.act(db, add_form(db, "issue", "nb1"), tz=TZ, now=NOW)  # 예전 책갈피 (v 없음) — 되긴 하지만 안내는 그대로
    assert seoi.is_new(db) and seoi.today(db, NOW, TZ)["how"].startswith("'이웃 관리' 책갈피가 바뀌었어요")
    await seoi.act(db, q(db), tz=TZ, now=NOW, rows=[], done=set(), day=DAY)
    v = seoi.view(db, "https://dash.example", tz=TZ)
    assert not seoi.is_new(db) and v["new"] is False and v["note"].startswith("오늘 이웃 들르기")
    assert seoi.today(db, NOW, TZ)["how"] == seoi.HOW + " · " + seoi.HOW_AUTO


def test_todo_line_says_how_long_the_auto_run_takes() -> None:
    db = FakeDb()
    rows = [nb_row("hotdeal", f"nb{i}", "m") for i in range(30)] + [visit_row("hotdeal", f"vv{i}", POST_NO + i) for i in range(5)]
    by = seoi.today(db, NOW, TZ, rows=rows, done={"nb-add:hotdeal:nb0"})["by"]
    m = seoi._minutes(34)
    assert by["hotdeal"]["auto"] == {"add": 29, "visit": 5, "min": m}  # 34번
    assert by["hotdeal"]["line"] == f"오늘 이웃 들르기 0 (세 블로그 합쳐 0/40) · 서이추 0/30 · 서이추 자동: 내 블로그에서 책갈피 → 시작 · 탭 켜 두기 · 약 {m}분"
    assert by["issue"]["line"] == "오늘 이웃 들르기 0 (세 블로그 합쳐 0/40) · 서이추 0/30" and by["issue"]["auto"]["min"] == 0
    cap, one = seoi.HOUR_CAP, math.ceil(seoi.AUTO_AVG_S / 60)
    assert [seoi._minutes(n) for n in (0, 1, cap, cap + 1)] == [0, one, math.ceil(cap * seoi.AUTO_AVG_S / 60), 60 + one]  # 한 시간 상한을 넘으면 한 시간 쉼
    assert 20 <= seoi._minutes(28 + 3) <= 35  # 주인 11/15 — 서이추 28 + 들르기 3 이 30분쯤 (예전 122분)


@needs_node
def test_packed_korean_text_reads_back_in_the_bookmarklet() -> None:
    """pack(): 한글이 든 글만 Z('…') 로 — 책갈피의 Z 가 그대로 되돌림 · 정규식·주소·열쇠·블로그 표는 그대로."""
    texts = ["이웃 관리: ", " — 마지막 단추는 직접 눌러 주세요", "댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접", "오류 100% (A·B) _.~ \"따옴표\" X-Z 힣가",
             "서로이웃을 눌렀어요 (골라졌는지 화면에서 봐 주세요)"]
    packed = [seoi.pack_text(t) for t in texts]
    assert all(p and all(c.isalnum() or c in "-_.!~*()" for c in p) for p in packed)  # 주소에서 안 늘어나는 글자만
    assert seoi.pack_text("이모지 😀") is None
    src = (Path(seoi.SRC)).read_text(encoding="utf-8")
    zline = next(ln for ln in src.splitlines() if ln.startswith("const Z = "))
    out = subprocess.run([NODE, "-e", zline + "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(Z)))", json.dumps(packed)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == texts
    js = seoi.pack("const A='한글 글',B=\"둘 'x'\",R=/확인|등록/,C='plain',D='역\\\\n슬래시',E=`틱 ${A}`;")
    assert js == "const A=Z('I64Ae8_Ae8'),B=Z('" + seoi.pack_text("둘 'x'") + "'),R=/확인|등록/,C='plain',D='역\\\\n슬래시',E=`틱 ${A}`;"
    code = unquote(seoi.view(FakeDb(), "https://dash.example")["code"])
    assert "SB=Z('" in code and "이웃 관리" not in code and "const NO=/확인|등록|신청" in code and '["derenpick","issue","데런픽"]' in code
    # 글 안에 역슬래시가 없어야 다 줄어듦 (seoi.js 규칙)
    body = src.split("*/", 1)[1]
    for lit in re.findall(r"'([^'\n]*[가-힣][^'\n]*)'", body):
        assert "\\" not in lit, lit


# ---------------------------------------------------------------- 2) 가짜 네이버 화면 (진짜 크롬)
# 모바일 이웃추가 화면 — 네이버 NgTemplate BuddyAddForm.html·BothBuddyInviteComplete.html·alertLayer(11/15 공개 스크립트) 모양 그대로:
# 확인 = 머리의 a.btn_ok · 서로이웃 = #bothBuddyRadio · 멘트 칸은 서로이웃을 골라야 보임 · 안내는 #_alertLayer(.lyr_popup · 그 안에도 '확인' 단추)
BUDDY = r"""<!doctype html><meta charset="utf-8"><body>
<div class="head type1"><h2 class="title">이웃추가</h2><a href="#" class="btn_close">취소</a><a href="#" class="btn_ok">확인</a></div>
<div id="ct" role="main"><form name="buddyAddForm" method="post" id="buddyAddForm"><fieldset><legend>이웃추가</legend>
<div class="set_radio_t1"><span><input type="radio" id="buddyRadio" name="relation" value="2" class="radio_img1 sim" checked><label for="buddyRadio">이웃으로 추가합니다.</label></span>
<span><input type="radio" id="bothBuddyRadio" name="relation" value="1" class="radio_img1 sim"><label for="bothBuddyRadio">서로이웃을 신청합니다.</label></span></div>
<div class="set_detail_t1"><label class="label_t1" for="buddyGroupSelect">그룹을 선택해주세요.</label><select class="select_t1" id="buddyGroupSelect"><option value="1">새 그룹</option></select>
<div class="add_msg" style="display:none"><textarea rows="4" cols="35" class="textarea_t1">우리 서로이웃해요~</textarea></div></div>
</fieldset></form></div>
<script>
const KIND = '__KIND__', ID = new URLSearchParams(location.search).get('blogId'), $ = s => document.querySelector(s);
const ev = o => fetch('/__ev?' + new URLSearchParams(Object.assign({id: ID, t: Date.now()}, o)));
function alertLayer(w, cb) {
  const m = document.createElement('div'); m.id = '_alertLayer';
  m.innerHTML = '<div class="g_dimmed _g_dimmed" style="display: block;"></div><div class="lyr_popup" id="lyr6" style="display: block;"><div class="lyr_cont lyr_alert"><div class="txt_area"><p class="dsc">' + w + '</p></div><div class="btn_area"><a href="#" class="btn_100 green" id="_alertLayerClose">확인</a></div></div></div>';
  document.body.appendChild(m);
  $('#_alertLayerClose').addEventListener('click', e => { e.preventDefault(); ev({k: 'layer_ok'}); m.remove(); cb && cb(); });
}
const both = $('#bothBuddyRadio'), ta = $('textarea');
document.querySelectorAll('input[name=relation]').forEach(r => r.addEventListener('click', () => { $('.add_msg').style.display = both.checked ? '' : 'none'; }));
if (KIND === 'login') location.replace('https://nid.naver.com/nidlogin.login');  // 네이버 화면 스크립트의 doLogin 처럼 로그인 화면으로 넘어감
if (KIND === 'closed') both.disabled = true;
if (KIND === 'already') { $('#ct').style.display = 'none'; $('.head').style.display = 'none'; alertLayer('이미 추가한 이웃입니다.'); }
if (KIND === 'weird') { $('#ct').remove(); }
$('.btn_ok').addEventListener('click', e => {
  e.preventDefault();
  const rel = both.checked ? '1' : '2';
  if (KIND === 'limit') { ev({k: 'tried', rel: rel}); alertLayer('하루에 신청 가능한 서로이웃 수를 초과하였습니다.<br>내일 다시 시도해 주세요.'); return; }
  if (KIND === 'later') { alertLayer('잠시 후 다시 시도해 주세요'); return; }
  if (KIND === 'pending') { alertLayer('서로이웃 신청 진행중입니다.'); return; }
  if (KIND === 'blocked') { alertLayer('상대방이 이웃 관계를 제한하여,<br>이웃/서로이웃을 신청하실 수 없습니다.'); return; }
  if (KIND === 'silent') return;
  if (KIND === 'donelayer') { ev({k: 'submit', rel: rel, msg: ta.value}); alertLayer('서로이웃을 신청하였습니다.'); return; }
  if (KIND === 'donealert') { ev({k: 'submit', rel: rel, msg: ta.value}); alert('서로이웃 신청이 완료되었습니다.'); return; }
  if (KIND === 'crowded') { alertLayer('상대방의 이웃 수가 초과되어 더 이상 이웃을 추가할 수 없습니다.'); return; }
  if (KIND === 'flood') { alertLayer('연속해서 신청할 수 없습니다.'); return; }
  ev({k: 'submit', rel: rel, msg: ta.value});
  setTimeout(() => { document.body.innerHTML = rel === '1' ? '<div class="head type1"><h2 class="title">서로이웃 신청 완료</h2><a href="#" class="btn_close">닫기</a></div><div id="ct"><p class="txt"><strong><em>' + ID + '</em></strong>님에게<br>서로이웃을 신청하였습니다.</p></div>'
    : '<div class="head type1"><h2 class="title">이웃추가 완료</h2></div><div id="ct"><p class="txt">이웃으로 추가하였습니다.</p></div>'; }, 300);
});
</script></body>"""

# 글 화면 (옛 모양 · PC 와 같은 한 묶음) — 공감 묶음은 네이버 reaction.basic.js 대로: 얼굴 단추(a._face)를 스크립트로 누르면(evt.target === 단추) 고르는 칸(._faceLayer)만
# 열리고 닫히고, 반응은 칸 안의 a._button 을 눌러야 바뀜 (on/off · aria-selected · 얼굴 단추는 on/off · aria-pressed). 댓글은 cbox (등록 = .u_cbox_btn_upload).
# 실제 모바일 화면(하트 두 개)은 아래 MPOST.
POST = r"""<!doctype html><meta charset="utf-8"><body style="margin:0">
<script>var userId = '__ME__', bLoginUser = __LOGGED__;</script>
<div class="se-title-text">수원 직관 후기</div>
<div class="se-main-container" style="min-height:2400px">__TEXT__</div>
<div class="area_sympathy"><div class="u_likeit_list_module _reactionModule" data-sid="BLOG" data-loaded="1">
<a href="#" role="button" class="u_likeit_button _face off" aria-pressed="false"><span class="u_likeit_icons _icons"><span class="u_likeit_icon"></span></span><span class="u_likeit_text _count">3</span></a>
<ul class="u_likeit_layer _faceLayer" style="display:none">
<li class="u_likeit_list like"><a href="#" class="u_likeit_list_button _button off" data-type="like" aria-selected="false"><span class="u_likeit_list_name _label">공감</span></a></li>
<li class="u_likeit_list impressive"><a href="#" class="u_likeit_list_button _button off" data-type="impressive" aria-selected="false"><span class="u_likeit_list_name _label">감동</span></a></li>
</ul></div></div>
<button type="button" class="comment_btn__Wbuoq" data-click-area="pst.re"><i aria-label="댓글"></i><span>4</span></button>
<div id="sheet" style="display:none;position:fixed;left:0;right:0;bottom:0;height:60%;background:#fff;overflow:auto;border-top:1px solid #999">
<div class="u_cbox"><ul class="u_cbox_list"><li class="u_cbox_comment" data-info="commentNo:1,mine:false"><span class="u_cbox_contents">잘 봤습니다 하루에 한 번은 꼭 와요</span></li></ul>
<div class="u_cbox_write_wrap"><div class="u_cbox_write_box"><div class="u_cbox_text" id="naverComment__write_textarea" contenteditable="true"></div>
<button type="button" class="u_cbox_btn_upload"><span class="u_cbox_txt_upload">등록</span></button></div></div></div></div>
<script>
const KIND = '__KIND__', ID = location.pathname.slice(1), $ = s => document.querySelector(s), $$ = s => Array.from(document.querySelectorAll(s));
const ev = o => fetch('/__ev?' + new URLSearchParams(Object.assign({id: ID, t: Date.now()}, o)));
const face = $('a._face'), layer = $('._faceLayer'), sheet = $('#sheet');
const set = (b, on, attr) => { b.classList.toggle('on', on); b.classList.toggle('off', !on); b.setAttribute(attr, String(on)); };
const paint = () => set(face, $$('._faceLayer a._button').some(b => b.classList.contains('on')), 'aria-pressed');
if (/liked/.test(KIND)) { set($('a._button[data-type=like]'), true, 'aria-selected'); paint(); }
if (/moved/.test(KIND)) { set($('a._button[data-type=impressive]'), true, 'aria-selected'); paint(); }
face.addEventListener('click', e => { e.preventDefault(); ev({k: 'face'}); if (e.target !== face) return; layer.style.display = layer.style.display === 'none' ? '' : 'none'; });
$$('._faceLayer a._button').forEach(b => b.addEventListener('click', e => {
  e.preventDefault();
  const on = !b.classList.contains('on');
  ev({k: 'react', type: b.getAttribute('data-type'), on: on ? 1 : 0});
  layer.style.display = 'none';
  if (/captcha/.test(KIND)) { const c = document.createElement('div'); c.className = 'u_likeit_captcha'; c.textContent = '자동입력 방지 문자를 입력해 주세요'; document.body.appendChild(c); return; }
  setTimeout(() => { $$('._faceLayer a._button').forEach(x => set(x, x === b && on, 'aria-selected')); paint(); }, /slow/.test(KIND) ? 2600 : 150);
}));
const own = () => { const li = document.createElement('li'); li.className = 'u_cbox_comment'; li.setAttribute('data-info', 'commentNo:2,mine:true'); li.textContent = '전에 남긴 내 댓글'; $('.u_cbox_list').appendChild(li); };
$('.comment_btn__Wbuoq').addEventListener('click', () => { sheet.style.display = ''; if (/lateown/.test(KIND)) setTimeout(own, 1800); });
if (/cboxcap/.test(KIND)) $('.u_cbox_btn_upload').addEventListener('click', () => { const c = document.createElement('div'); c.className = 'u_cbox_captcha_wrap'; c.style.height = '80px'; c.textContent = '아래 문자를 입력해 주세요'; sheet.appendChild(c); }, true);
if (/sheet/.test(KIND)) sheet.style.display = '';
if (/mine/.test(KIND)) { const li = document.createElement('li'); li.className = 'u_cbox_comment'; li.setAttribute('data-info', 'commentNo:2,mine:true'); li.textContent = '전에 남긴 내 댓글'; $('.u_cbox_list').appendChild(li); }
$('.u_cbox_btn_upload').addEventListener('click', () => {
  const box = $('#naverComment__write_textarea'), t = box.textContent.trim();
  if (!t) return;
  if (/spam/.test(KIND)) { ev({k: 'comment_refused'}); alert('스팸성 댓글로 판단되어 등록이 제한되었습니다. 잠시 후 다시 시도해 주세요.'); return; }
  if (/lost|cboxcap/.test(KIND)) { box.textContent = ''; return; }
  ev({k: 'comment', text: t});
  setTimeout(() => { const li = document.createElement('li'); li.className = 'u_cbox_comment'; li.setAttribute('data-info', 'commentNo:9,mine:true');
    const s = document.createElement('span'); s.className = 'u_cbox_contents'; s.textContent = t; li.appendChild(s); $('.u_cbox_list').appendChild(li); box.textContent = ''; }, 400);
});
</script></body>"""

HOME = """<!doctype html><meta charset="utf-8"><body><script>var userId = '__ME__', bLoginUser = __LOGGED__;</script><h1>이웃새글</h1><p>내 이웃의 새 글이 여기 보여요</p></body>"""
LOGIN = """<!doctype html><meta charset="utf-8"><body><h1>NAVER 로그인</h1><form><input name="id"><input type="password" name="pw"><button>로그인</button></form></body>"""
POST_TEXT_LONG = "지난 주말 수원 경기 직관 후기입니다. 후반 추가 시간에 터진 역전 골 장면을 골대 뒤에서 봤는데 아직도 소름이 돋아요. " * 12

RUN = r"""
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
  await c.clock.pauseAt(S.base + 1000);  // 가짜 시계는 tick() 으로만 흐름 — T = 지금 화면의 시각
  const p = await c.newPage(), out = {ev: [], alerts: [], docs: [], panel: []};
  let pending = 0, T = S.base + 1000;
  p.on('dialog', async d => { out.alerts.push(d.message()); await d.accept(); });
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
      if (u.hostname !== 'm.blog.naver.com') return await route.fulfill({status: 404, body: 'x'});
      if (u.pathname === '/__ev') { out.ev.push(Object.fromEntries(u.searchParams)); return await route.fulfill({status: 204, body: ''}); }
      if (route.request().resourceType() === 'document') out.docs.push({u: u.pathname + u.search, t: T});
      if (u.pathname === '/BuddyAddForm.naver') {
        const kind = (S.buddies || {})[u.searchParams.get('blogId')] || 'ok';
        return await route.fulfill({contentType: 'text/html', body: html(S.buddy, kind)});
      }
      const m = /^\/([a-z0-9_-]+)\/(\d+)$/.exec(u.pathname);
      if (m) return await route.fulfill({contentType: 'text/html', body: html(S.post, (S.posts || {})[m[1] + '/' + m[2]] || 'ok')});
      return await route.fulfill({contentType: 'text/html', body: html(S.home, '')});
    } finally { pending--; }
  });
  const tick = async ms => { for (let i = 0; i < 400 && pending; i++) await real(3); await c.clock.runFor(ms); T += ms; await real(2); };
  const panel = () => p.evaluate(() => { const o = Array.from(document.body.children).find(n => n.style && n.style.position === 'fixed' && n.querySelector('iframe'));
    return o ? {title: o.firstChild.textContent, line: o.children[0].textContent, buttons: Array.from(o.querySelectorAll('button')).map(x => x.textContent)} : null; });
  const tap = t => p.evaluate(t => { const x = Array.from(document.querySelectorAll('button')).find(n => n.textContent === t); if (x) x.click(); return !!x; }, t);
  await p.goto('https://m.blog.naver.com' + S.start);
  if (S.open_ms) await tick(S.open_ms);
  if (S.pre) await p.evaluate(S.pre);
  p.evaluate(code).catch(e => { out.error = String(e); });
  if (S.mode === 'step') {
    for (let i = 0; i < 400 && !out.alerts.length; i++) await tick(100);
  } else {
    for (let i = 0; i < 100 && !(await panel()) && !out.alerts.length; i++) await tick(100);
    out.first = await panel();
    if (out.first) {
      await tap('시작');
      let last = '', sec = 0, paused = 0;
      for (; sec < S.max_s; sec++) {
        await tick(1000);
        const v = await panel();
        if (!v) break;
        if (v.line !== last && !/초 뒤|읽는 중/.test(v.line)) out.panel.push(v.line);
        last = v.line;
        if (S.pause_at && !paused && out.ev.filter(e => e.k === 'submit').length >= S.pause_at) { paused = 1; out.paused = await tap('멈춤'); out.pause_t = T; }
        else if (paused === 1 && /이어하기를 누르면/.test(v.line) && ++paused) { for (let i = 0; i < 600; i++) await tick(1000); out.idle_docs = out.docs.length; out.resumed = await tap('이어하기'); out.resume_t = T; }
        else if (/^멈췄어요 — (?!이어하기)|다 했어요|닿지 않아/.test(v.line)) break;
      }
      out.sec = sec;
      out.last = await panel();
      out.frame = await p.evaluate(() => { const f = document.querySelector('iframe'); try { return f.contentDocument.body.innerText.slice(0, 200); } catch (e) { return 'unreadable'; } });
      await tap('멈춤');
      await tick(200);
      out.closed = !(await panel());
      out.busy = await p.evaluate(() => window.__sbnb);
    }
  }
  if (S.mode === 'step') out.state = await p.evaluate(() => { const q = s => document.querySelector(s); return {face: q('a._face').className, like: q('a._button[data-type=like]').className,
    pressed: q('a._face').getAttribute('aria-pressed'), layer: q('._faceLayer').style.display, box: q('#naverComment__write_textarea').textContent, mine: document.querySelectorAll('li.u_cbox_comment[data-info*="mine:true"]').length}; });
  process.stdout.write('OUT ' + JSON.stringify(out) + '\n');
  await b.close();
  process.exit(0);
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(2); });
"""


DRAFTS = ["골대 뒤에서 본 역전 골이라니 글만 읽어도 그 순간이 그려져요. 추가 시간에 터져서 더 짜릿했겠어요!",
          "직관 사진 속 응원석 분위기가 고스란히 전해지네요. 비 오는 날인데도 끝까지 자리를 지키신 게 대단해요.",
          "후반전 교체 카드 이야기가 특히 재미있었어요. 감독 판단이 그대로 맞아떨어진 날이었군요.",
          "경기장 가는 길 먹거리 소개까지 있어서 다음 원정 때 참고하려고 저장해 뒀습니다.",
          "선수 입장 장면 묘사를 읽으니 현장 함성이 들리는 듯합니다. 다음 홈 경기 후기도 기다릴게요."]


class SeqDrafter(FakeDrafter):
    """글마다 다른 초안 (같은 초안을 되풀이하면 대시보드가 '반복'으로 빼므로)."""

    async def draft(self, blog: str, title: str, text: str, recent: list[str]) -> dict[str, Any]:
        self.calls.append((blog, title, text, list(recent)))
        return {"skip": False, "comment": DRAFTS[(len(self.calls) - 1) % len(DRAFTS)]}


class Clock:
    """대시보드 쪽 가짜 시계 (책갈피가 도는 화면의 가짜 시계와 같은 시각 — 요청마다 맞춤). time 모듈의 나머지는 그대로."""

    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def __getattr__(self, name: str) -> Any:
        return getattr(real_time, name)


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
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


async def test_api_queue_and_done_read_todays_list_and_tick_the_row(bot: DealBot, tmp_path: Path) -> None:
    """진짜 api 길: a=queue 는 오늘 목록일 때만 차례를 주고 체크된 줄은 뺌 · a=done 은 그 줄을 체크하고 블로그 프로그램에 맡길 것을 적음."""
    from test_dash_seoi import _post

    day = tz_now(bot).date().isoformat()
    list_for(tmp_path, day, [nb_row("hotdeal", "nb1", "직관 글 잘 봤어요"), nb_row("hotdeal", "nb2", "둘째 멘트"), visit_row("hotdeal", "vv1", POST_NO)])
    res = await _post(bot, q(bot.db, "hotdeal"), origin="https://m.blog.naver.com")
    got = json.loads(res[1])
    assert res[0] == 200 and got["next"] == {"id": "nb1", "msg": "직관 글 잘 봤어요"} and (got["add"], got["visit"]) == (2, 1)
    assert res[3]["Access-Control-Allow-Origin"] == "https://m.blog.naver.com"
    assert (await _post(bot, {**add_form(bot.db, "hotdeal", "nb1"), "au": "1"}, origin="https://m.blog.naver.com"))[0] == 200
    res = await _post(bot, done_form(bot.db, "hotdeal", "nb1", "ok"), origin="https://m.blog.naver.com")
    assert json.loads(res[1])["row"] is True and routine.done_ids(bot.db, day) == {"nb-add:hotdeal:nb1"}
    assert [r["rid"] for r in routine.relay_rows(bot.db)] == ["nb-add:hotdeal:nb1"]
    v = routine.view(bot)
    row = next(i for s in v["slots"] for i in s["items"] if i["id"] == "nb-add:hotdeal:nb1")
    assert row["done"] is True and v["seoi"]["by"]["hotdeal"]["auto"] == {"add": 1, "visit": 1, "min": seoi._minutes(2)}  # 할 일 탭에서 그 줄이 체크로 보임
    assert v["seoi"]["by"]["hotdeal"]["line"].endswith(f"서이추 자동: 내 블로그에서 책갈피 → 시작 · 탭 켜 두기 · 약 {seoi._minutes(2)}분")
    got = json.loads((await _post(bot, q(bot.db, "hotdeal"), origin="https://m.blog.naver.com"))[1])
    assert got["next"] == {"id": "vv1", "no": str(POST_NO)} and got["add"] == 1 and got["wait"] > 0  # 체크된 줄은 빼고 · 방금 서이추를 했으니 들르기 먼저
    # 어제 목록이면 차례를 주지 않고 줄도 체크하지 않음
    list_for(tmp_path, "2020-01-01", [nb_row("issue", "old1", "어제 멘트")])
    got = json.loads((await _post(bot, q(bot.db, "issue"), origin="https://m.blog.naver.com"))[1])
    assert got["next"] is None and got["text"].startswith("오늘 할 일 목록이 아직 없어요")
    assert json.loads((await _post(bot, done_form(bot.db, "issue", "old1", "ok"), origin="https://m.blog.naver.com"))[1])["row"] is False
    assert (await _post(bot, {**q(bot.db), "k": "wrong"}, origin="https://m.blog.naver.com"))[0] == 403


async def drive(bot: DealBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[dict[str, Any]] | None, scn: dict[str, Any],
                drafter: Any = None) -> dict[str, Any]:
    """가짜 네이버 화면에서 책갈피를 돌린다 — 책갈피가 대시보드에 묻는 것은 진짜 api 길(bot.web.dispatch)로, 시계는 화면의 가짜 시계에 맞춰.
    돌려줌: 화면 쪽 기록 + reqs [{t, form, status, reply}]."""
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    day = tz_now(bot).date().isoformat()
    base = datetime.fromisoformat(f"{day}T09:00:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()
    clock = Clock(base)
    monkeypatch.setattr(seoi, "time", clock)
    monkeypatch.setattr(api_mod, "time", clock)
    d = drafter or SeqDrafter()
    monkeypatch.setattr(seoi, "get_drafter", lambda: d)
    bot.dashboard.api.seoi_rate = RateWindow(10**6, 600.0)  # 가짜 시계로 두 시간을 1분에 돌리므로 — 진짜 10분 상한은 아래에서 가짜 시각으로 셈
    if rows is not None:
        list_for(tmp_path, day, rows)
    code = unquote(seoi.view(bot.db, bot.settings.dashboard.public_url)["code"][len("javascript:"):])
    full = {"base": int(base * 1000), "me": "derenpick", "start": "/FeedList.naver", "max_s": 12000, "buddy": BUDDY, "post": POST, "home": HOME, "login": LOGIN,
            "text": POST_TEXT_LONG, **scn}
    reqs: list[dict[str, Any]] = []
    out: dict[str, Any] | None = None
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen([NODE, "-e", RUN, code, json.dumps(full, ensure_ascii=False)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True, env=env)
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


def acts(got: dict[str, Any], a: str) -> list[dict[str, Any]]:
    return [r for r in got["reqs"] if r["form"].get("a") == a]


def max_per_window(times: list[float], span: float) -> int:
    return max((sum(1 for u in times if t <= u < t + span) for t in times), default=0)


async def test_auto_sends_thirty_mutual_requests_slowly_and_ticks_every_row(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 30명: 한 명씩 서로이웃 + 그 사람 멘트 + 확인 → '신청 완료' 확인 → 줄 체크. 사이는 GAP['add'](대시보드가 정함), 한 시간 HOUR_CAP 명."""
    rows = [nb_row("issue", f"nb{i:02d}", f"안녕하세요 nb{i:02d} 님, 글 잘 봤어요. 서로이웃 해요!") for i in range(30)]
    rows += [nb_row("finance", "other1", "드라마 블로그 멘트"), nb_row("issue", "derenlog", "우리 블로그는 빼요")]
    got = await drive(bot, monkeypatch, tmp_path, rows, {})
    assert got["first"]["title"] == f"오늘 이웃 자동 — 데런픽 · 서이추 30명 · 들르기 0편 · 약 {seoi._minutes(30)}분 · 화면을 켜 둔 채 이 탭에 두세요"
    assert got["first"]["buttons"] == ["시작", "멈춤"] and got["first"]["line"].startswith("시작을 누르면")
    subs = ev(got, "submit")
    assert [s["id"] for s in subs] == [f"nb{i:02d}" for i in range(30)]  # 그 블로그 줄만 · 차례대로 · 한 번씩
    assert all(s["rel"] == "1" for s in subs) and [s["msg"] for s in subs] == [f"안녕하세요 nb{i:02d} 님, 글 잘 봤어요. 서로이웃 해요!" for i in range(30)]
    ts = [int(s["t"]) / 1000 for s in subs]
    gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
    lo, hi = seoi.GAP["add"]
    assert min(gaps) >= lo and sorted(gaps)[len(gaps) // 2] <= hi + 15  # 간격은 진짜로 GAP 아래로 안 감 (보통 GAP + 화면 여는 시간)
    assert max_per_window(ts, 3600) <= seoi.HOUR_CAP
    if len(ts) > seoi.HOUR_CAP:
        assert ts[seoi.HOUR_CAP] - ts[0] >= 3600 and max(gaps) > 1200  # 한 시간 상한 → 다음은 한 시간 뒤
    else:
        assert max(gaps) <= hi + 30 and ts[-1] - ts[0] <= seoi._minutes(len(ts)) * 60  # 상한 안이면 쉼 없이 · '약 n분' 안에 끝남
    assert ev(got, "layer_ok") == [] and got["alerts"] == []  # 네이버 안내 창의 '확인'은 누른 적 없음 · 알림 창 없음
    # 대시보드: 30번 세고, 줄 30개가 저절로 체크, 블로그 프로그램에 맡길 '신청했어요' 30개
    assert seoi.today(bot.db, got["base"] + 9000, TZ)["by"]["issue"]["add"] == 30
    assert routine.done_ids(bot.db, got["day"]) == {f"nb-add:issue:nb{i:02d}" for i in range(30)}
    assert sorted(r["rid"] for r in routine.relay_rows(bot.db)) == sorted(f"nb-add:issue:nb{i:02d}" for i in range(30))
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok"] * 30 and acts(got, "stop") == []
    assert all(r["form"].get("au") == "1" and r["form"]["v"] == "2" for r in acts(got, "add")) and len(acts(got, "add")) == 30
    assert max_per_window([r["t"] for r in got["reqs"]], 600) <= api_mod.SEOI_RATE_N  # 대시보드의 손님마다 10분 SEOI_RATE_N 번 안
    assert got["last"]["line"] == "오늘 할 것을 다 했어요 ✓" and got["last"]["title"] == "오늘 데런픽 자동으로 할 것은 다 했어요 (서이추 30/30 · 들르기 0)"
    assert "서로이웃을 신청하였습니다" in got["frame"] and got["closed"] is True and got["busy"] == 0
    assert not seoi.is_new(bot.db)  # 새 판 책갈피가 물어 왔으니 '다시 깔기' 안내는 사라짐


async def test_auto_stops_at_once_when_naver_shows_a_limit_notice(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(8)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb3": "limit"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb1", "nb2"] and [e["id"] for e in ev(got, "tried")] == ["nb3"]
    assert [d["u"] for d in got["docs"] if "Buddy" in d["u"]] == [f"/BuddyAddForm.naver?blogId=nb{i}" for i in range(4)]  # 그 뒤로는 아무 화면도 열지 않음
    assert got["last"]["line"].startswith("멈췄어요 — 네이버가 한도·제한 안내를 보여요 (하루에 신청 가능한 서로이웃 수를 초과하였습니다.")
    assert got["last"]["line"].endswith("· 오늘은 쉬어요") and got["last"]["buttons"] == ["멈춤"]  # 이어하기 단추는 없앰
    assert [r["form"]["w"] for r in acts(got, "stop")] == ["all"] and ev(got, "layer_ok") == []
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok", "ok", "ok", "stop"]
    t = seoi.today(bot.db, got["base"] + 3000, TZ)["by"]["issue"]
    assert "네이버 제한 안내로 오늘 서이추·들르기 쉼" in t["line"]
    # 그날은 책갈피를 다시 눌러도 시작하지 않음 (칸도 안 뜨고 알림만)
    again = await drive(bot, monkeypatch, tmp_path, None, {})
    assert again["alerts"] == ["이웃 관리: 오늘은 네이버가 제한 안내를 보여서 데런픽 서이추·들르기를 쉬어요 — 내일 다시"] and again["first"] is None and again["docs"][1:] == []


async def test_auto_stops_on_a_later_notice_and_on_two_failures_in_a_row(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(6)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb1": "later"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0"] and "잠시 후 다시 시도해 주세요" in got["last"]["line"] and len(acts(got, "stop")) == 1


async def test_auto_stops_after_two_failures_in_a_row_but_not_after_one(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(8)]
    # 한 번 안 됨(화면이 예상과 다름) → 다음 사람은 됨 → 확인을 눌러도 아무 글이 없음 + 또 화면이 다름 = 두 번 잇달아 → 멈춤 + 그날 쉼
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb1": "weird", "nb3": "silent", "nb4": "weird"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb2"]
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok", "fail", "ok", "unsure", "fail"] and len(acts(got, "stop")) == 1
    assert got["last"]["line"].startswith("멈췄어요 — ") and "오늘은 쉬어요" in got["last"]["line"]
    assert routine.done_ids(bot.db, got["day"]) == {"nb-add:issue:nb0", "nb-add:issue:nb2"}  # 안 된 사람의 줄은 그대로 (주인이 볼 수 있게)
    assert len([d for d in got["docs"] if "Buddy" in d["u"]]) == 5


async def test_auto_stops_on_a_login_screen(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(5)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb2": "login"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb1"] and got["frame"] == "unreadable"
    assert got["last"]["line"] == "멈췄어요 — 로그인 화면이 나왔거나 화면을 읽지 못했어요 — 네이버에 로그인한 뒤 책갈피를 다시 눌러 주세요 · 오늘은 쉬어요"
    assert [r["form"]["w"] for r in acts(got, "stop")] == ["all"] and [r["form"]["r"] for r in acts(got, "done")] == ["ok", "ok", "login"]
    assert len([d for d in got["docs"] if "Buddy" in d["u"]]) == 3 and seoi.today(bot.db, got["base"] + 900, TZ)["by"]["issue"]["add"] == 2  # 로그인 화면은 세지 않음


async def test_login_screen_on_the_very_first_one_stops_without_resting_the_day(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(3)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb0": "login"}})
    assert ev(got, "submit") == [] and acts(got, "stop") == [] and "오늘은 쉬어요" not in got["last"]["line"] and got["last"]["line"].startswith("멈췄어요 — 로그인 화면이")
    assert seoi.today(bot.db, got["base"] + 900, TZ)["by"]["issue"]["add"] == 0
    # 로그인 아이디를 못 읽은 화면(로그인 안 됨)에서 글 들르기: 책갈피가 먼저 알아봄
    got = await drive(bot, monkeypatch, tmp_path, [visit_row("issue", "vv1", POST_NO)], {"me": "", "pre": "sessionStorage.setItem('sbnb_b', 'issue')"})
    assert ev(got, "react") == [] and ev(got, "comment") == [] and acts(got, "visit") == [] and got["last"]["line"].startswith("멈췄어요 — 네이버에 로그인돼 있지 않아요")


async def test_auto_skips_people_it_cannot_ask_without_counting_them(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """이미 이웃(안내 창) · 서로이웃을 받지 않음(고르는 칸이 잠김) · 신청 중·상대가 제한(확인 뒤 안내 창): 건너뛰고 세지 않음 — 멈추지 않음."""
    rows = [nb_row("issue", f"nb{i}", f"멘트 {i}") for i in range(7)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb1": "already", "nb2": "closed", "nb4": "pending", "nb5": "blocked"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb3", "nb6"] and ev(got, "layer_ok") == []
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok", "skip", "skip", "ok", "skip", "skip", "ok"] and acts(got, "stop") == []
    assert [r["form"]["t"] for r in acts(got, "add")] == ["nb0", "nb3", "nb4", "nb5", "nb6"]  # 이미 이웃·받지 않음은 묻기(세기) 전에 건너뜀
    assert seoi.today(bot.db, got["base"] + 3000, TZ)["by"]["issue"]["add"] == 3  # 확인 뒤에 건너뛴 둘은 센 것을 되돌림
    assert routine.done_ids(bot.db, got["day"]) == {f"nb-add:issue:nb{i}" for i in range(7)}  # 줄은 모두 체크 (주인이 할 일이 없음)
    assert got["last"]["line"] == "오늘 할 것을 다 했어요 ✓"
    ts = [d["t"] / 1000 for d in got["docs"] if "Buddy" in d["u"]]
    assert min(b - a for a, b in zip(ts, ts[1:], strict=False)) >= 20  # 건너뛴 뒤에도 화면은 천천히 (20초 넘게)


async def test_auto_visits_posts_reads_likes_comments_and_registers(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """서이추와 들르기를 번갈아: 글을 열어 읽고(20~60초 · 끝까지 내림) → 공감(고르는 칸의 '공감') → 댓글 초안 → 등록 → 내 댓글 확인 → 줄 체크."""
    d = SeqDrafter()
    rows = [nb_row("issue", "nb0", "멘트 0"), nb_row("issue", "nb1", "멘트 1"), visit_row("issue", "vv1", POST_NO), visit_row("issue", "vv2", POST_NO + 1),
            visit_row("issue", "vv3", POST_NO + 2), visit_row("issue", "vv4", POST_NO + 3), visit_row("finance", "vv9", POST_NO + 9)]
    posts = {f"vv2/{POST_NO + 1}": "liked", f"vv3/{POST_NO + 2}": "mine slow", f"vv4/{POST_NO + 3}": "moved"}
    got = await drive(bot, monkeypatch, tmp_path, rows, {"posts": posts}, drafter=d)
    assert got["first"]["title"].startswith(f"오늘 이웃 자동 — 데런픽 · 서이추 2명 · 들르기 4편 · 약 {seoi._minutes(6)}분")
    order = [(r["form"]["a"], r["form"].get("t") if r["form"]["a"] == "add" else r["form"]["u"].split("/")[-2]) for r in got["reqs"] if r["form"]["a"] in ("add", "visit")]
    assert order == [("add", "nb0"), ("visit", "vv1"), ("add", "nb1"), ("visit", "vv2"), ("visit", "vv3"), ("visit", "vv4")]  # 번갈아 · 그 블로그 것만
    reacts = ev(got, "react")
    assert [(e["id"], e["type"], e["on"]) for e in reacts] == [(f"vv1/{POST_NO}", "like", "1"), (f"vv3/{POST_NO + 2}", "like", "1")]  # 이미 공감·다른 반응이 눌린 글은 절대 안 누름
    assert [e["id"] for e in ev(got, "comment")] == [f"vv1/{POST_NO}", f"vv2/{POST_NO + 1}", f"vv4/{POST_NO + 3}"]  # 내 댓글이 이미 있는 글(vv3)은 공감만
    assert [e["text"] for e in ev(got, "comment")] == [DRAFTS[0], DRAFTS[1], DRAFTS[3]] and len(d.calls) == 4 and d.calls[0][0] == "issue"
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok"] * 6 and acts(got, "stop") == [] and got["alerts"] == []
    # 읽는 시간: 글을 연 뒤 대시보드에 묻기까지 20초 넘게 (본문 길이에 따라 60초까지) — 대시보드에도 그 시간으로
    opened = {d0["u"]: d0["t"] / 1000 for d0 in got["docs"]}
    for r in acts(got, "visit"):
        path = "/" + "/".join(r["form"]["u"].split("/")[-2:])
        assert 20 <= int(r["form"]["rd"]) <= 75 and r["form"]["sc"] == "1" and got["base"] + r["t"] - opened[path] >= 20, r["form"]
    ts = sorted(got["base"] + r["t"] for r in got["reqs"] if r["form"]["a"] in ("add", "visit"))
    assert min(b - a for a, b in zip(ts, ts[1:], strict=False)) >= min(seoi.GAP["add"][0], seoi.GAP["visit"][0])  # 서이추·들르기를 섞어도 사이는 GAP 아래로 안 감
    t = seoi.today(bot.db, got["base"] + 3000, TZ)
    assert t["by"]["issue"]["add"] == 2 and t["by"]["issue"]["visit"] == 4
    assert routine.done_ids(bot.db, got["day"]) == {"nb-add:issue:nb0", "nb-add:issue:nb1", f"nb-visit:issue:vv1:{POST_NO}", f"nb-visit:issue:vv2:{POST_NO + 1}",
                                                    f"nb-visit:issue:vv3:{POST_NO + 2}", f"nb-visit:issue:vv4:{POST_NO + 3}"}
    assert got["last"]["line"] == "오늘 할 것을 다 했어요 ✓"


async def test_auto_stops_when_a_comment_is_refused_as_spam_or_a_captcha_shows(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [visit_row("issue", f"vv{i}", POST_NO + i) for i in range(4)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"posts": {f"vv1/{POST_NO + 1}": "spam"}})
    assert [e["id"] for e in ev(got, "comment")] == [f"vv0/{POST_NO}"] and len(ev(got, "comment_refused")) == 1
    assert got["alerts"] == []  # 네이버 알림 창은 작은 창 안에서 글만 받아 적음 (주인 화면에 안 뜸)
    assert "네이버 알림: 스팸성 댓글로 판단되어 등록이 제한되었습니다" in got["last"]["line"] and got["last"]["line"].endswith("· 오늘은 쉬어요")
    assert [r["form"]["w"] for r in acts(got, "stop")] == ["all"] and len([d for d in got["docs"] if d["u"].startswith("/vv")]) == 2
    assert seoi.today(bot.db, got["base"] + 2000, TZ)["by"]["issue"]["line"].endswith("네이버 제한 안내로 오늘 서이추·들르기 쉼")


async def test_auto_stops_on_a_like_captcha_and_on_two_lost_comments(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [visit_row("issue", f"vv{i}", POST_NO + i) for i in range(4)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"posts": {f"vv0/{POST_NO}": "captcha"}})
    assert len(ev(got, "react")) == 1 and "자동입력 방지" in got["last"]["line"] and len(acts(got, "stop")) == 1 and len([d for d in got["docs"] if d["u"].startswith("/vv")]) == 1
    # 등록을 눌렀는데 내 댓글이 안 생김: 한 번은 넘어가고 두 번 잇달아면 멈춤
    rows = [visit_row("hotdeal", f"ww{i}", POST_NO + i) for i in range(4)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"me": "demiyum", "posts": {f"ww0/{POST_NO}": "lost", f"ww1/{POST_NO + 1}": "lost"}})
    assert ev(got, "comment") == [] and [r["form"]["r"] for r in acts(got, "done")] == ["fail", "fail"] and len(acts(got, "stop")) == 1
    assert "등록을 눌렀는데 내 댓글이 안 보여요" in got["last"]["line"] and acts(got, "visit")[0]["form"]["b"] == "hotdeal"


async def test_auto_reads_done_notices_and_other_sides_limit_and_stops_on_flood(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """신청 완료가 안내 창·알림으로 와도 된 것으로 (예전엔 '네이버 알림'으로 실패 → 두 번이면 그날 쉼) · 상대방 이웃 수 초과는 내 한도가 아님(건너뜀) ·
    '연속해서 …' 안내는 바로 멈춤."""
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(6)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"buddies": {"nb0": "donelayer", "nb1": "donealert", "nb2": "crowded", "nb4": "flood"}})
    assert [s["id"] for s in ev(got, "submit")] == ["nb0", "nb1", "nb3"] and got["alerts"] == [] and ev(got, "layer_ok") == []
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok", "ok", "skip", "ok", "stop"] and [r["form"]["w"] for r in acts(got, "stop")] == ["all"]
    assert "연속해서 신청할 수 없습니다" in got["last"]["line"] and got["last"]["line"].endswith("· 오늘은 쉬어요")
    assert len([d for d in got["docs"] if "Buddy" in d["u"]]) == 5  # 멈춘 뒤로는 아무 화면도 열지 않음
    ts = [d["t"] / 1000 for d in got["docs"] if "Buddy" in d["u"]]
    assert ts[3] - ts[2] >= seoi.GAP["add"][0]  # '확인'을 누른 뒤 건너뛴 사람 다음에도 간격은 그대로 (GAP 넘게)


async def test_auto_never_comments_twice_on_one_post(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 글에 댓글 두 번 없음: 방금 초안을 받아 간 글(다른 탭·다시 시작)은 건너뜀 · 내 댓글이 늦게 뜨는 목록도 기다려서 봄 ·
    등록 뒤 자동입력 방지 칸이 뜨면 바로 멈춤."""
    day = tz_now(bot).date().isoformat()
    base = datetime.fromisoformat(f"{day}T09:00:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()
    rows = [visit_row("issue", f"vv{i}", POST_NO + i) for i in range(5)]
    seed = {"k": seoi.key(bot.db), "a": "visit", "b": "issue", "u": f"https://blog.naver.com/vv0/{POST_NO}", "t": "수원 직관 후기", "x": POST_TEXT_LONG, "rd": "31", "sc": "0"}
    await seoi.act(bot.db, seed, tz=ZoneInfo(bot.settings.app.timezone), now=base + 1, drafter=FakeDrafter(comment=DRAFTS[4]))  # 다른 탭이 방금 받아 감
    got = await drive(bot, monkeypatch, tmp_path, rows, {"posts": {f"vv1/{POST_NO + 1}": "lateown", f"vv3/{POST_NO + 3}": "cboxcap"}})
    visits = acts(got, "visit")
    assert [(r["form"]["u"].split("/")[-2], r["status"], r["reply"].get("code")) for r in visits][:2] == [("vv0", 409, "dup"), ("vv1", 200, None)]
    assert [e["id"] for e in ev(got, "comment")] == [f"vv2/{POST_NO + 2}"]  # vv0 = 건너뜀 · vv1 = 내 댓글이 있음(늦게 뜸) · vv3 = 등록이 막힘
    assert all(e["id"] != f"vv0/{POST_NO}" for e in got["ev"])  # 건너뛴 글에서는 공감도 누르지 않음
    assert [d["u"] for d in got["docs"] if d["u"].startswith("/vv")] == [f"/vv{i}/{POST_NO + i}" for i in range(4)]  # 건너뛴 글을 되풀이해 열지 않음 · 멈춘 뒤 안 엶
    assert [r["form"]["r"] for r in acts(got, "done")] == ["ok", "ok", "stop"] and [r["form"]["w"] for r in acts(got, "stop")] == ["all"]
    assert "등록을 눌렀는데 내 댓글이 안 보여요 · 네이버 알림: 아래 문자를 입력해 주세요" in got["last"]["line"] and got["last"]["line"].endswith("· 오늘은 쉬어요")


async def test_pause_waits_and_resume_goes_on_from_the_next_one(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [nb_row("issue", f"nb{i}", "멘트") for i in range(5)]
    got = await drive(bot, monkeypatch, tmp_path, rows, {"pause_at": 2, "max_s": 1500})
    assert got["paused"] is True and got["resumed"] is True and any(ln.endswith("멈췄어요 — 이어하기를 누르면 이어서 해요") for ln in got["panel"])
    subs = ev(got, "submit")
    assert [s["id"] for s in subs] == [f"nb{i}" for i in range(5)]  # 멈춘 동안 빠뜨리거나 두 번 하지 않음
    idle = [d for d in got["docs"] if got["pause_t"] + 30000 < d["t"] < got["resume_t"]]
    assert idle == [] and got["resume_t"] - got["pause_t"] >= 600000  # 멈춘 10분 동안 아무 화면도 열지 않음
    assert got["last"]["line"] == "오늘 할 것을 다 했어요 ✓" and len(acts(got, "done")) == 5


async def test_auto_only_starts_on_the_mobile_blog_and_says_when_there_is_nothing(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    got = await drive(bot, monkeypatch, tmp_path, [nb_row("finance", "nb1", "드라마 것만")], {})
    assert got["first"]["title"] == "오늘 데런픽 자동으로 할 것은 다 했어요 (서이추 0/30 · 들르기 0)" and got["last"]["line"] == "오늘 할 것을 다 했어요 ✓"
    assert ev(got, "submit") == [] and len(got["docs"]) == 1 and got["closed"] is True
    # 내 블로그 화면에서 눌러도 됨 (로그인 아이디 → 그 블로그) · 드라마 계정이면 드라마 줄
    got = await drive(bot, monkeypatch, tmp_path, None, {"me": "derenlog", "start": "/derenlog"})
    assert got["first"]["title"].startswith("오늘 이웃 자동 — 드라마 · 서이추 1명") and [s["id"] for s in ev(got, "submit")] == ["nb1"]
    assert acts(got, "add")[0]["form"]["b"] == "finance"


# ---------------------------------------------------------------- 3) 공감 (한 단계 책갈피 — 주인 11/15 09:10 화면)
async def step(bot: DealBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str, **scn: Any) -> dict[str, Any]:
    return await drive(bot, monkeypatch, tmp_path, None, {"mode": "step", "start": f"/neighbor1/{POST_NO}", "posts": {f"neighbor1/{POST_NO}": kind}, "open_ms": 31000, **scn})


async def test_like_presses_like_inside_the_reaction_layer(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """네이버 공감 단추(a._face)는 스크립트로 누르면 반응 고르는 칸만 열림 (11/15 '눌렀는데 표시가 안 바뀌었어요') → 칸 안의 '공감'을 바로 누름 (얼굴 단추는 안 누름)."""
    got = await step(bot, monkeypatch, tmp_path, "ok")
    assert [(e["k"], e.get("type"), e.get("on")) for e in got["ev"]] == [("react", "like", "1")]
    assert "_face on" in got["state"]["face"] and got["state"]["pressed"] == "true" and "on" in got["state"]["like"].split() and got["state"]["layer"] == "none"
    (a,) = got["alerts"]
    assert a.startswith("이웃 관리: 데런픽 · 공감 ✓ · 댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접") and got["state"]["box"] == DRAFT
    assert ev(got, "comment") == []  # 한 단계 책갈피는 등록을 누르지 않음 (자동만)


@pytest.mark.parametrize("kind", ["liked", "liked sheet", "liked sheet mine", "moved"])
async def test_already_liked_is_never_pressed_even_with_the_comment_sheet_open(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    got = await step(bot, monkeypatch, tmp_path, kind)
    assert [e for e in got["ev"] if e["k"] in ("face", "react")] == []  # 얼굴 단추도 고르는 칸도 누르지 않음 (누르면 공감이 풀림)
    assert "_face on" in got["state"]["face"] and "공감은 이미 눌러져 있어요" in got["alerts"][0]
    if "mine" in kind:
        assert "이미 내 댓글이 있는 글이에요 — 초안은 넣지 않았어요" in got["alerts"][0] and got["state"]["box"] == ""
    else:
        assert got["state"]["box"] == DRAFT


@pytest.mark.parametrize("kind", ["slow", "sheet", "slow sheet"])
async def test_like_waits_for_a_slow_mark_and_works_with_the_comment_sheet_open(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    got = await step(bot, monkeypatch, tmp_path, kind)
    assert [(e["type"], e["on"]) for e in ev(got, "react")] == [("like", "1")] and ev(got, "face") == []  # 한 번만 · 다시 눌러 풀지 않음
    assert got["state"]["pressed"] == "true" and "공감 ✓" in got["alerts"][0] and "표시가 안 바뀌었어요" not in got["alerts"][0]
    assert got["state"]["box"] == DRAFT


# ---------------------------------------------------------------- 4) 실제 모바일 공감 모양 (주인 11/15 12:13 아이패드 — 빈 하트 ♡ + 분홍 하트 ❤ 4 + 댓글 2 + 공유)
# 11/15 공개 m.blog 글(로그인 없이)에서 뽑은 모양 그대로 줄임: Interact__interact_section > Interact__like_area > .blog_mobile_like_area 안에
#   .my_reaction  > ._reactionModule(data-sid·data-cid·data-loaded=1) > a.u_likeit_button._face(off · aria-pressed · ._icons._longpress · __reaction__zeroface = 빈 하트) + ul._faceLayer
#   a.other_reaction > ._reactionModule > span.u_likeit_button._face(남들 반응 아이콘 + ._count 숫자) + ul._faceLayer  ← 누르면 반응 목록 창 (누르면 안 됨)
# 고르는 칸은 CSS(.blog_mobile_like_area .u_likeit_layer{display:none}) 로 늘 숨김 · 같은 구역이 떠 있는 줄(Interact__is_floating--…)에 한 벌 더.
# 네이버(reaction.basic.js · 모바일 isAllowLongPress): 하트를 톡 = touchend → _runFaceDirectReaction → 칸의 첫 단추(공감)로 _onButtonHandler
# (document 에 붙은 '._reactionModule_BLOG a._button' click 처리기) · 스크립트로 하트 click = 칸 열기만 — 주인 화면에선 칸이 안 보였고 공감도 안 됨 → 여기서도 그대로.
MOD = """<div class="u_likeit_list_module _reactionModule _reactionModule_BLOG" data-sid="BLOG" data-cid="__CID__" __LOADED__ data-facetype="1">__FACE__
<ul class="u_likeit_layer _faceLayer" role="menu" aria-hidden="false">
<li class="u_likeit_list like"><a class="u_likeit_list_button _button off" data-type="like" href="#ratingbutton-like" role="menuitem" aria-selected="false" aria-pressed="false"><span class="u_ico _icon"></span><span class="u_likeit_list_name _label">공감</span><span class="u_likeit_list_count _count">4</span></a></li>
<li class="u_likeit_list impressive"><a class="u_likeit_list_button _button off" data-type="impressive" href="#ratingbutton-impressive" role="menuitem" aria-selected="false" aria-pressed="false"><span class="u_ico _icon"></span><span class="u_likeit_list_name _label">칭찬</span><span class="u_likeit_list_count _count">0</span></a></li>
</ul></div>"""
MY = ('<a class="u_likeit_button _face off" href="javascript:;" onclick="return false;" role="button" aria-haspopup="true" aria-pressed="false">'
      '<span class="u_likeit_icons _icons _longpress"><span class="u_likeit_icon __reaction__zeroface"></span></span></a>')
OTHER = ('<span class="u_likeit_button _face off" role="button" aria-haspopup="true" aria-pressed="false"><span class="u_likeit_icons _icons">'
         '<span class="u_likeit_icon __reaction__like"></span></span><span class="u_likeit_text _count num">4</span></span>')
SECT = ('<div class="Interact__interact_section--vWRCb __FL__"><div class="Interact__like_area--b7PEN"><div class="blog_mobile_like_area" style="user-select:none">'
        '<div class="my_reaction">' + MOD.replace("__FACE__", MY) + '</div><div class="oth">' + MOD.replace("__FACE__", OTHER) + '</div></div></div>'
        '<button type="button" class="Interact__comment_btn--Wbuoq" data-click-area="pst.re"><i aria-label="댓글"></i><span>2</span></button>'
        '<a class="naver-splugin Interact__splugin_btn--ajBSC" href="#">공유</a></div>')
MPOST = (POST.split('<div class="area_sympathy">')[0]
         + "<style>.u_likeit_layer{display:none}.blog_mobile_like_area .u_likeit_layer{display:none}"
         + ".Interact__is_floating--LzUQ5{position:fixed;left:0;right:0;bottom:0;background:#fff;pointer-events:none}</style>"
         + SECT.replace("__FL__", "") + SECT.replace("__FL__", "Interact__is_floating--LzUQ5 Interact__is_not_floating--RmKij")
         + '<div id="sheet"' + POST.split('<div id="sheet"')[1].split("<script>")[0] + r"""<script>
const KIND = '__KIND__', ID = location.pathname.slice(1), $ = s => document.querySelector(s), $$ = s => Array.from(document.querySelectorAll(s));
const ev = o => fetch('/__ev?' + new URLSearchParams(Object.assign({id: ID, t: Date.now()}, o)));
// a.other_reaction 은 네이버가 스크립트로 만듦 (a 안에 a — HTML 로 쓰면 브라우저가 쪼갬)
$$('.oth').forEach(d => { const a = document.createElement('a'); a.className = 'other_reaction'; while (d.firstChild) a.appendChild(d.firstChild); d.replaceWith(a); });
const sheet = $('#sheet'), set = (b, on, attr) => { b.classList.toggle('on', on); b.classList.toggle('off', !on); b.setAttribute(attr, String(on)); };
// 네이버가 내 반응을 그림: 모든 묶음의 칸 단추 · 내 하트(aria-pressed · 아이콘)
const paint = type => { $$('._faceLayer a._button').forEach(x => set(x, x.getAttribute('data-type') === type, 'aria-selected'));
  $$('.my_reaction a._face').forEach(f => { set(f, !!type, 'aria-pressed'); f.querySelector('.u_likeit_icon').className = 'u_likeit_icon __reaction__' + (type || 'zeroface'); }); };
if (/liked/.test(KIND)) paint('like');
if (/otheron/.test(KIND)) $$('.other_reaction a._button[data-type=like]').forEach(x => set(x, true, 'aria-selected'));  // 남들 묶음에만 내 표시가 먼저 뜬 화면
$$('._reactionModule').forEach(m => m.setAttribute('data-cid', (/alias/.test(KIND) ? 'realid/' + ID.split('/')[1] : ID).replace('/', '_')));  // 네이버: 블로그아이디_글번호 (주소가 별칭이면 진짜 아이디)
if (/feed/.test(KIND)) { const x = $('.my_reaction ._reactionModule').cloneNode(true); x.setAttribute('data-cid', 'other_1'); x.querySelector('a._button').className = 'u_likeit_list_button _button on'; x.style.display = 'none'; document.body.appendChild(x); }  // 아래 다른 글 묶음 (내가 공감한 글)
if (!/unloaded/.test(KIND)) $$('._reactionModule').forEach(m => m.setAttribute('data-loaded', '1'));
document.addEventListener('click', e => {
  const t = e.target, f = t.closest('._reactionModule_BLOG a._face'), b = t.closest('._reactionModule_BLOG a._button');
  if (t.closest('a.other_reaction')) { e.preventDefault(); ev({k: 'other'}); return; }
  if (f) { e.preventDefault(); ev({k: 'face'}); return; }  // 스크립트로 하트 = 칸 열기 시도뿐 (주인 화면: 칸도 안 보이고 공감도 안 됨)
  if (!b) return;
  e.preventDefault();
  const on = !b.classList.contains('on');
  ev({k: 'react', type: b.getAttribute('data-type'), on: on ? 1 : 0});
  paint(on ? b.getAttribute('data-type') : '');  // 화면에 바로 (네이버도 서버 답 전에 바꿈)
});
$('.Interact__comment_btn--Wbuoq').addEventListener('click', () => { sheet.style.display = ''; });
$('.u_cbox_btn_upload').addEventListener('click', () => { const box = $('#naverComment__write_textarea'), t = box.textContent.trim(), li = document.createElement('li');
  ev({k: 'comment', text: t}); li.className = 'u_cbox_comment'; li.setAttribute('data-info', 'commentNo:9,mine:true'); li.textContent = t; $('.u_cbox_list').appendChild(li); box.textContent = ''; });
</script></body>""").replace("__LOADED__", "")


@pytest.mark.parametrize("auto", [False, True])
async def test_mobile_heart_presses_like_in_the_hidden_layer_and_sees_it_filled(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, auto: bool) -> None:
    """빈 하트 ♡ → 그 묶음 칸 안 '공감'을 바로 눌러 하트가 채워짐 (예전: 하트를 눌러 칸이 뜨기를 기다리다 '표시가 안 바뀌었어요') ·
    남들 반응(분홍 하트·숫자)·떠 있는 줄은 안 누름. 한 편 모드·자동 둘 다."""
    if auto:
        got = await drive(bot, monkeypatch, tmp_path, [visit_row("issue", "neighbor1", POST_NO)], {"post": MPOST})
        assert [r["form"]["r"] for r in acts(got, "done")] == ["ok"] and [e["text"] for e in ev(got, "comment")] == [DRAFTS[0]]
    else:
        got = await step(bot, monkeypatch, tmp_path, "feed", post=MPOST)  # 아래 다른 글(내가 공감한 글) 묶음은 이 글 판단에 안 섞임
        assert got["alerts"][0].startswith("이웃 관리: 데런픽 · 공감 ✓ · 댓글 초안 넣음 ✓") and got["state"]["box"] == DRAFT
        assert got["state"]["pressed"] == "true" and "on" in got["state"]["face"].split() and "on" in got["state"]["like"].split()
    assert [(e["k"], e.get("type"), e.get("on")) for e in got["ev"] if e["k"] != "comment"] == [("react", "like", "1")]  # 하트·남들 묶음은 안 누름 · 한 번만


@pytest.mark.parametrize("kind", ["alias", "alias liked"])
async def test_mobile_heart_blog_id_alias_matches_by_post_number(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """주소의 블로그 아이디가 별칭(묶음 data-cid 는 진짜 아이디_글번호)이어도 글번호로 이 글 묶음을 알아봄 — 빈 하트면 한 번 누르고, 이미 눌렸으면 안 누름."""
    got = await step(bot, monkeypatch, tmp_path, kind, post=MPOST)
    want = [] if "liked" in kind else [("react", "like", "1")]
    assert [(e["k"], e.get("type"), e.get("on")) for e in got["ev"] if e["k"] in ("face", "react", "other")] == want
    assert ("공감은 이미 눌러져 있어요" if "liked" in kind else "공감 ✓") in got["alerts"][0]


@pytest.mark.parametrize("kind", ["liked", "otheron", "unloaded"])
async def test_mobile_heart_already_filled_or_not_drawn_is_never_pressed(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """이미 채워진 하트(어느 묶음이든 눌린 표시) → 절대 안 누름 (누르면 공감 취소) · 네이버가 내 반응을 아직 안 그린 화면도 안 누름."""
    got = await step(bot, monkeypatch, tmp_path, kind, post=MPOST)
    assert [e for e in got["ev"] if e["k"] in ("face", "react", "other")] == []
    (a,) = got["alerts"]
    assert ("공감이 눌렸는지 알 수 없어 누르지 않았어요" if kind == "unloaded" else "공감은 이미 눌러져 있어요") in a and got["state"]["box"] == DRAFT
