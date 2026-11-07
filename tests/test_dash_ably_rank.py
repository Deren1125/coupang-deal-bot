"""에이블리 코디 랭킹 캡처 (LB-15 — 주인 11/07 '대시보드 오늘 할 일에 에이블리 코디 랭킹 캡쳐하기, 텔레그램 봇보다 쉬워서').
기록 파일 v2 ably_rank → 할 일 한 줄(올리면 빠짐) · 큰 단추 = 사진 여러 장 올리기 blog.ably_rank {uploads} · 저녁 알림은 줄을 늘리지 않음."""
from __future__ import annotations

from dash_helpers import fixture
from dealbot.dashboard.actions import BLOG_ACTIONS
from dealbot.dashboard.blogauto import _ably_rank
from dealbot.dashboard.state import ably_rank_item


def test_parse_and_row():
    v = _ably_rank({"day": "2031-03-07", "done": False, "n": 0, "at": "", "steps": ["에이블리 앱 › 코디 › 랭킹", "1위부터 3~9장", "올리기"]})
    row = ably_rank_item(v)
    assert row["iid"] == "ably-rank" and row["title"] == "에이블리 코디 랭킹 캡처" and "랭킹" in row["status"]
    a = row["primary"]
    assert (a["action"], a["input"], a["multiple"]) == ("blog.ably_rank", "photos", True) and a["args"] == {}
    assert ably_rank_item(_ably_rank({"day": "2031-03-07", "done": True, "n": 4})) is None  # 올린 날은 없음
    assert _ably_rank(None) is None and _ably_rank({"day": "x"}) is None and ably_rank_item(None) is None


def test_contract():
    fx = fixture("action_ably_rank.json")
    assert fx["action"] == "ably_rank" and "ably_rank" in BLOG_ACTIONS and "ably_rank" in fixture("status_blogauto.json")["actions"]
    assert len(fx["args"]["uploads"]) == 2
