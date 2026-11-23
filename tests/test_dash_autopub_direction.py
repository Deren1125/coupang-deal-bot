"""주인 11/23 안전 후속: Blog-Auto 가 새 한 분야 방향과 안 맞는다고 적은 대기 글(direction.fit 거짓)은 '하루치 한 번에'가 주지 않는다.

라커룸 비야구(#360·#365·#369) · 드라마 회차 정리(#364·#368) · 데런픽 명단 밖 남자(#363) 같은 옛 대기 글 — 묶음·점검은 통과여도.
할 일 탭 줄·블로그 카드 한 줄에 '새 방향과 안 맞음 — 올리지 않음 (까닭)'."""

from __future__ import annotations

from pathlib import Path

import pytest

from dealbot.app import DealBot
from dealbot.dashboard import autopub, blogauto, routine
from test_dash_autopub import DAY, OK, TZ, Rng, _paste, bot, _no_live_threads  # noqa: F401 — 고정 장치
from test_dash_routine import SLOTS, at, item, with_routine

MEDIA = "https://bot.example.com"


def _rows() -> dict:
    return {"day": DAY, "slots": SLOTS, "overflow": 0, "items": [
        item("rsv:hotdeal:360", "s1", group="예약", blog="hotdeal", title="라커룸 · 손흥민 이적", paste=_paste("손흥민 이적"), gate=OK,
             note="새 방향과 안 맞음 — 올리지 않음 (야구 아님 — 야구 한 분야만) · 붙여넣고 예약",
             direction={"fit": False, "why": "야구 아님 — 야구 한 분야만"}),
        item("rsv:hotdeal:365", "s1", group="예약", blog="hotdeal", title="라커룸 · 배구", paste=_paste("배구"), gate=OK,
             direction={"fit": False, "why": "다른 종목 — 야구 한 분야만"}),  # 옛 Blog-Auto 처럼 note 에 말이 없어도
        item("rsv:hotdeal:474", "s1", group="예약", blog="hotdeal", title="라커룸 · 한화 숄텐스", paste=_paste("한화 숄텐스"), gate=OK,
             direction={"fit": True, "why": ""}),
        item("rsv:hotdeal:475", "s2", group="예약", blog="hotdeal", title="라커룸 · 레이예스", paste=_paste("레이예스"), gate=OK),  # 칸 없음 = 예전처럼
        item("rsv:finance:364", "s1", group="예약", blog="finance", title="드라마 · 회차 정리", paste=_paste("5회 정리"), gate=OK,
             direction={"fit": False, "why": "'회차·결말 정리' 갈래는 꺼짐 — 뒷이야기 한 분야만"}),
    ]}


def test_routine_item_parses_direction_and_puts_reason_in_note() -> None:
    x = _rows()["items"]
    got = blogauto._routine_item(x[1], media_base=MEDIA)
    assert got is not None and got["direction"] == {"fit": False, "why": "다른 종목 — 야구 한 분야만"}
    assert got["note"].startswith("새 방향과 안 맞음 — 올리지 않음 (다른 종목") and got["paste"]["html"]  # 묶음은 그대로 (주인은 직접 올릴 수 있음)
    got = blogauto._routine_item(x[0], media_base=MEDIA)
    assert got["note"].count("새 방향과 안 맞음") == 1  # 이미 적힌 말은 두 번 붙이지 않음
    assert blogauto._routine_item(x[2], media_base=MEDIA)["direction"] == {"fit": True, "why": ""}
    assert "direction" not in blogauto._routine_item(x[3], media_base=MEDIA)
    for bad in ({"fit": "no"}, "x", {"why": "a"}):
        assert "direction" not in blogauto._routine_item({**x[0], "direction": bad}, media_base=MEDIA)
    odd = blogauto._routine_item({**x[0], "direction": {"fit": False, "why": "KeyError"}}, media_base=MEDIA)
    assert odd["direction"]["why"] == "까닭 모름"
    nb = blogauto._routine_item(item("nb:issue:a", "s1", blog="issue", direction={"fit": False, "why": "x"}))
    assert "direction" not in nb  # 발행·예약 줄만


def test_autopub_never_serves_misfits_and_says_why(bot: DealBot, tmp_path: Path) -> None:  # noqa: F811
    with_routine(tmp_path, _rows())
    t = at(bot, DAY, "10:00")
    rv = routine.view(bot, now=t)
    assert [i["id"] for i in autopub.ready(rv, "hotdeal")] == ["rsv:hotdeal:474", "rsv:hotdeal:475"]
    assert autopub.ready(rv, "finance") == []
    rows = {i["id"]: i for s in rv["slots"] for i in s["items"]}
    assert autopub.unfit(rows["rsv:hotdeal:365"]) == "새 방향과 안 맞음 — 올리지 않음 (다른 종목 — 야구 한 분야만)"
    seen = []
    for k in range(4):
        q = autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "next", "b": "hotdeal"}, tz=TZ, now=t + k * 200, rv=routine.view(bot, now=t + k * 200), rng=Rng())
        if not q["next"]:
            break
        seen.append(q["next"]["id"])
        autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "done", "b": "hotdeal", "id": q["next"]["id"], "r": "hold"}, tz=TZ, now=t + k * 200 + 1,
                    rv=routine.view(bot, now=t + k * 200 + 1), rng=Rng())
    assert seen == ["rsv:hotdeal:474", "rsv:hotdeal:475"]
    q = autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "next", "b": "finance"}, tz=TZ, now=t, rv=rv, rng=Rng())
    assert q["next"] is None
    # 블로그 카드 한 줄: 왜 안 올리는지
    by = autopub.today(bot.db, t, TZ, rv)["by"]
    assert by["hotdeal"]["misfit"] == 2 and "새 방향과 안 맞아 올리지 않는 글 2편 (야구 아님 — 야구 한 분야만 외 1)" in by["hotdeal"]["line"]
    assert by["finance"]["misfit"] == 1 and "새 방향과 안 맞아 올리지 않는 글 1편 ('회차·결말 정리' 갈래는 꺼짐" in by["finance"]["line"]
    assert by["issue"]["misfit"] == 0
    # 열쇠가 있어도 안 맞는 줄은 '했어요'로 적지 못함
    with pytest.raises(autopub.CaptureError):
        autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "done", "b": "hotdeal", "id": "rsv:hotdeal:360", "r": "ok"}, tz=TZ, now=t, rv=rv, rng=Rng())


def test_todo_row_shows_reason_through_note(bot: DealBot, tmp_path: Path) -> None:  # noqa: F811
    # routine.js 는 예산 끝 — 할 일 줄 작은 글(it.note)에 까닭이 그대로 보임 (따로 칩 없음)
    with_routine(tmp_path, _rows())
    rows = {i["id"]: i for s in routine.view(bot, now=at(bot, DAY, "10:00"))["slots"] for i in s["items"]}
    assert rows["rsv:hotdeal:365"]["note"].startswith("새 방향과 안 맞음 — 올리지 않음 (다른 종목")
    assert rows["rsv:finance:364"]["note"].startswith("새 방향과 안 맞음 — 올리지 않음 ('회차·결말 정리'")
    assert "새 방향" not in rows["rsv:hotdeal:474"]["note"]
