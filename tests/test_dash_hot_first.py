"""인기 키워드 글은 할 일 맨 위 (주인 10/20 — "할 일 목록에서도 상위로 올리고. 인기 키워드가 포함된 글의 경우에").

Blog-Auto 기록 파일 posts[].hot_rank (선택 칸, 인기 검색어 상위 10 키워드 글만 — hotfirst) → 할 일 줄이 맨 위, 순위 순 ·
작은 칩 '🔥 인기 N위' (cue — app.js chip 이 그대로 보여 줌). 블로그별 할 일 수는 그대로."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import rank, webapp
from dealbot.dashboard.blogauto import parse_v2
from test_dash_state import state, write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _posts(**ranks: int) -> dict:
    posts = fixture("export_v2.json")["posts"]
    for p in posts["issue"]:
        if str(p["pid"]) in ranks:
            p["hot_rank"] = ranks[str(p["pid"])]
    return posts


def test_hot_rank_parse_is_optional_and_bounded() -> None:
    data = fixture("export_v2.json")
    assert all("hot_rank" not in p for p in parse_v2(data)["posts"]["issue"])  # 예전 Blog-Auto · 핫이 아닌 글
    data["posts"] = _posts(**{"46": 3})
    data["posts"]["issue"][1]["hot_rank"] = 11
    data["posts"]["issue"][2]["hot_rank"] = True
    got = {p["pid"]: p.get("hot_rank") for p in parse_v2(data)["posts"]["issue"]}
    assert got == {46: 3, 47: None, 48: None}


def test_rank_puts_hot_posts_on_top_by_rank_with_a_chip() -> None:
    now = 1_800_000_000.0
    items = [{"iid": "sys-dealbot", "sev": "stop", "kind": "publish_broken"},
             {"iid": "post-issue-1", "sev": "todo", "kind": "photos", "src": "blog", "_hot": 90, "_hot_rank": 5},
             {"iid": "deal-1", "sev": "todo", "kind": "link_request", "src": "deal", "_hot": 100, "expires_at": now + 600},
             {"iid": "post-issue-2", "sev": "todo", "kind": "photos", "src": "blog", "_hot_rank": 2},
             {"iid": "post-issue-3", "sev": "todo", "kind": "photos", "src": "blog", "_hot_rank": 0}]
    out = rank.order(items, now)
    assert [x["iid"] for x in out] == ["post-issue-2", "post-issue-1", "sys-dealbot", "deal-1", "post-issue-3"]
    assert [x["cue"] for x in out[:2]] == ["🔥 인기 2위", "🔥 인기 5위"] and out[3]["cue"] == "급해요"
    assert all("_hot_rank" not in x and "_hot" not in x for x in out)


async def test_state_todo_sorts_hot_posts_first_and_keeps_blog_counts(bot: DealBot, settings: Settings,
                                                                     tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    before = await state(bot)
    write_export(tmp_path / "blogauto.json", posts=_posts(**{"48": 2, "46": 7}))
    body = await state(bot)
    order = [x["iid"] for x in body["todo"]]
    assert order[:2] == ["post-issue-48", "post-issue-46"]
    assert [x["cue"] for x in body["todo"][:2]] == ["🔥 인기 2위", "🔥 인기 7위"]
    assert sorted(order) == sorted(x["iid"] for x in before["todo"])  # 줄은 그대로 · 순서만
    assert body["counts"]["todo"] == before["counts"]["todo"]
    for b in ("issue", "finance", "hotdeal"):
        assert len(body["blogs"][b]["waiting"]) == len(before["blogs"][b]["waiting"])
    raw = json.dumps(body, ensure_ascii=False)
    assert "_hot_rank" not in raw and "_hot" not in raw


def test_app_js_shows_the_cue_chip_as_hot() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "if (it.cue) return el('span', {class: 'cp ' + (it.cue === '급해요' ? 'stop' : 'hot')}, it.cue);" in js
