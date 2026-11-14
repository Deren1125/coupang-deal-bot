"""'통계 보내기' 책갈피 알림 (주인 11/14): 21:30 뒤 안 보낸 블로그가 있으면 하루 한 번 앱 알림 · 할 일 줄 없음."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from dealbot.dashboard import statscap
from dealbot.notify import stats_remind
from test_dash_statscap import FakeDb

TZ = ZoneInfo("Asia/Seoul")


def at(h: int, m: int, d: int = 14) -> float:
    return datetime(2026, 11, d, h, m, tzinfo=TZ).timestamp()


class Router:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    async def push_event(self, title: str, body: str, *, url: str, tag: str) -> int:
        self.sent.append((title, body, tag))
        return 1


def bot(db: FakeDb) -> SimpleNamespace:
    return SimpleNamespace(db=db, router=Router(), settings=SimpleNamespace(app=SimpleNamespace(timezone="Asia/Seoul")))


def got(db: FakeDb, blog: str | None, day: str = "2026-11-14") -> None:
    db.kv[f"{statscap.PREFIX}{day}:100000-{blog}"] = json.dumps({"blog": blog})


async def test_none_sent_names_all_three_once_after_2130() -> None:
    db = FakeDb()
    b = bot(db)
    assert await stats_remind.check(b, at(21, 29)) is None and not b.router.sent  # 21:30 전
    assert await stats_remind.check(b, at(21, 31)) and await stats_remind.check(b, at(22, 40)) is None  # 하루 한 번
    assert b.router.sent == [("오늘 통계 보내기", "오늘 통계 보내기 — 아이패드 사파리 네이버 내 통계에서 책갈피 (세 블로그)", "stats-remind")]
    assert await stats_remind.check(b, at(21, 30, 15))  # 다음 날 또
    assert len(b.router.sent) == 2


async def test_lists_only_blogs_still_missing_and_skips_when_all_sent() -> None:
    db = FakeDb()
    b = bot(db)
    got(db, "issue")
    got(db, "finance", "2026-11-13")  # 어제 것은 안 셈
    await stats_remind.check(b, at(21, 45))
    assert b.router.sent[0][1].endswith("(아직: 라커룸·드라마)")
    db2 = FakeDb()
    for x in ("issue", "finance", "hotdeal"):
        got(db2, x)
    b2 = bot(db2)
    assert await stats_remind.check(b2, at(21, 45)) is None and not b2.router.sent


def test_no_nudge_line_left_in_statscap() -> None:
    assert not hasattr(statscap, "nudge") and statscap.PREFIX == "stats_capture:"  # 할 일 줄은 뺌 · 책갈피 받기는 그대로
