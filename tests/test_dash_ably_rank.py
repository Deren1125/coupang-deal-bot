"""에이블리 코디 랭킹 캡처 (LB-15 — 주인 11/07 '대시보드 오늘 할 일에 에이블리 코디 랭킹 캡쳐하기, 텔레그램 봇보다 쉬워서').
11/14: 할 일 줄은 뺌 (책갈피로 보냄 · 안 보낸 날만 Blog-Auto 가 10시 알림). 기록 파일 칸 파싱·계약만 남김."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.actions import BLOG_ACTIONS
from dealbot.dashboard.blogauto import _ably_rank
from test_dash_visits import write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def test_parse_only():
    v = _ably_rank({"day": "2031-03-07", "done": False, "n": 0, "at": "", "steps": ["에이블리 앱 › 코디 › 랭킹", "1위부터 3~9장", "올리기"]})
    assert v["done"] is False and len(v["steps"]) == 3
    assert _ably_rank(None) is None and _ably_rank({"day": "x"}) is None


async def test_no_todo_rows_for_ably_rank_and_keyzard(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """주인 11/14: 에이블리 랭킹 캡처·키자드 등록은 할 일 줄이 없음 — 알림(ably_rank:·kz_rss:)과 키자드 로그인 알림도 줄을 만들지 않음."""
    write_export(tmp_path / "blogauto.json", ably_rank={"day": "2026-11-14", "done": False, "n": 0, "at": "", "steps": ["a", "b"]})
    blogauto_status(settings)
    now = time.time()
    for key, kind, iid in (("ably_rank:2026-11-14", "ably_rank", "ably-rank"), ("kz_rss:2026-11-16", "kz_rss", "kz-rss"),
                           ("kz:issue", "keyzard_login", "conn-naver-issue")):
        bot.router.open({"src": "blogauto", "key": key, "kind": kind, "iid": iid, "title": "x", "at": now})  # type: ignore[union-attr]
    _, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    iids = {it["iid"] for it in body["todo"]}
    assert not iids & {"ably-rank", "kz-rss", "conn-naver-issue", "search"} and not any(it["kind"] in ("ably_rank", "search_reg") for it in body["todo"])


def test_contract():
    fx = fixture("action_ably_rank.json")
    assert fx["action"] == "ably_rank" and "ably_rank" in BLOG_ACTIONS and "ably_rank" in fixture("status_blogauto.json")["actions"]
    assert len(fx["args"]["uploads"]) == 2
