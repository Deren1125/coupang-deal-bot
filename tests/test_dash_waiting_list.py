"""'Pick 발행 대기 N편 · 인기 M' 할 일 줄 → 누르면 그 글 목록 (주인 11/04 '인기 2가 뭔지, 대기가 뭔지 볼 수 있으면').

Blog-Auto 기록 파일의 todo 줄(kind drafts_waiting)에 딜봇이 detail.lines 를 붙인다: 인기 예상 점수 높은 순 · 인기 예상은 🔥 ·
대기 기준은 Blog-Auto metrics.waiting_posts 와 같음 (임시저장 · 주인이 고친 글 · 붙여넣기 기다림, 발행된 글 뺌).
"""

from __future__ import annotations

from pathlib import Path

from dash_helpers import blogauto_status
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.state import waiting_lines
from test_dash_state import bot, state, write_export  # noqa: F401 — bot 픽스처


def test_waiting_lines_order_and_marks() -> None:
    rows = {
        1: {"pid": 1, "title": "낮은 글", "status": "saved", "score": 40, "popular": False, "saved_at": "2026-11-04T09:00:00+09:00"},
        2: {"pid": 2, "title": "인기 글", "status": "saved", "score": 88, "popular": True},
        3: {"pid": 3, "title": "발행됨", "status": "published", "score": 99, "popular": True},
        4: {"pid": 4, "title": "붙여넣기 기다림", "status": "failed", "paste": True, "score": 72, "popular": True},
        5: {"pid": 5, "title": "그냥 실패", "status": "failed", "score": 90},
        6: {"pid": 6, "title": "점수 모름", "status": "saved", "score": None},
        7: {"pid": 7, "title": "주인이 고침", "status": "needs_input", "owner_edited": True, "score": 50},
    }
    lines = waiting_lines(rows)
    assert lines == ["🔥 인기 예상 · 88점 · #2 인기 글", "🔥 인기 예상 · 72점 · #4 붙여넣기 기다림",
                     "50점 · #7 주인이 고침", "40점 · #1 낮은 글", "점수 없음 · #6 점수 모름"]


def test_waiting_lines_caps_long_list() -> None:
    rows = {i: {"pid": i, "title": f"글{i}", "status": "saved", "score": i} for i in range(20)}
    lines = waiting_lines(rows)
    assert len(lines) == 16 and lines[-1] == "… 그 밖 5편은 블로그 보기에서" and lines[0].startswith("19점")


async def test_drafts_waiting_todo_row_shows_posts(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    path = tmp_path / "blogauto.json"
    write_export(path)
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    data["todo"]["items"].append({"key": "waiting:issue", "marker": "🔔[할 일]", "short": "Pick 발행 대기 3편 · 인기 2",
                                  "text": "🔔[할 일] Deren's Pick 발행 대기 글 3편 (인기 예상 2편)\n→ 대시보드 블로그 › 기다리는 글에서 발행 / 버리기",
                                  "blog": "issue", "kind": "drafts_waiting", "since": data["written_at"], "urgent": False,
                                  "link": "", "group": "drafts", "count": 3})
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    blogauto_status(settings)
    body = await state(bot)
    row = next(x for x in body["todo"] if x.get("kind") == "drafts_waiting")
    lines = row["detail"]["lines"]
    assert lines[0] == "🔥 인기 예상 · 82점 · #46 " + next(p["title"] for p in data["posts"]["issue"] if p["pid"] == 46)[:60]
    assert len(lines) == 3 and "🔥" in lines[1] and "🔥" not in lines[2]


async def test_blog_waiting_rows_carry_score_and_popular(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    """블로그 화면 기다리는 글 줄 옆 '🔥 82점'·'60점' (주인 11/04 '기다리는 글 옆에도 뜨게') — 줄에 score·popular."""
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    rows = [x for b in (body.get("blogs") or {}).values() for x in (b.get("waiting") or [])] + list(body["todo"])
    got = {x["iid"]: x for x in rows if x.get("src") == "blog" and "score" in x}
    p46 = next(x for x in got.values() if x["iid"].endswith("46"))
    assert p46["score"] == 82 and p46["popular"] is True and p46["sc"] == "🔥 82점"
    p48 = next(x for x in got.values() if x["iid"].endswith("48"))
    assert p48["sc"] == "60점"


async def test_blog_tab_status_line_ends_with_score_but_todo_row_unchanged(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    blog_rows = {x["iid"]: x for x in body["blogs"]["issue"]["waiting"]}
    todo_rows = {x["iid"]: x for x in body["todo"]}
    iid = next(i for i in blog_rows if i.endswith("46"))
    assert blog_rows[iid]["status"].endswith("· 🔥 82점")
    if iid in todo_rows:
        assert "82점" not in todo_rows[iid]["status"]  # 할 일 탭 줄은 그대로
