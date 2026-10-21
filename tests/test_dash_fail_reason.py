"""업로드 실패 이유 (주인 10/21 — Blog-Auto failwhy, posts[].fail 선택 칸) → 할 일 줄 '업로드 실패 · 이유 — 자동으로 다시'.
글자는 state.py 에서 만든다 (app.js·app.css 는 그대로). 다시 올리기 단추는 그대로."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.blogauto import parse_v2
from test_dash_state import state, write_export

WHY = "서버 메모리가 모자라 크롬이 제때 안 떠서 (잠시 뒤 다시 하면 대부분 돼요)"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _posts(**fail: object) -> dict:
    posts = fixture("export_v2.json")["posts"]
    posts["hotdeal"][0]["fail"] = {"why": WHY, "kind": "memory", "at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
                                   "auto_retry": "pending", **fail}
    return posts


def test_parse_fail_is_optional_and_bounded() -> None:
    assert parse_v2(fixture("export_v2.json"))["posts"]["hotdeal"][0]["fail"]["auto_retry"] == "pending"
    data = fixture("export_v2.json")
    del data["posts"]["hotdeal"][0]["fail"]
    assert "fail" not in parse_v2(data)["posts"]["hotdeal"][0]  # 예전 Blog-Auto
    data["posts"]["hotdeal"][0]["fail"] = {"why": "x", "kind": "zzz", "auto_retry": "later"}
    assert parse_v2(data)["posts"]["hotdeal"][0]["fail"] == {"why": "x", "kind": "other", "at": "", "auto_retry": "no"}
    data["posts"]["hotdeal"][0]["fail"] = {"why": "  ", "kind": "memory"}
    assert "fail" not in parse_v2(data)["posts"]["hotdeal"][0]


@pytest.mark.parametrize("auto,tail", [("pending", "분 뒤 한 번 자동으로 다시 해요"), ("done", "자동으로 한 번 다시 해 봤어요"), ("no", "")])
async def test_todo_row_shows_reason_and_keeps_retry_button(bot: DealBot, settings: Settings, tmp_path: Path, auto: str, tail: str) -> None:
    write_export(tmp_path / "blogauto.json", posts=_posts(auto_retry=auto))
    blogauto_status(settings)
    body = await state(bot)
    row = next(x for x in body["todo"] if x["iid"] == "post-hotdeal-100")
    assert row["status"].startswith(f"업로드 실패 · {WHY}")
    assert row["status"].endswith(tail) and (auto != "pending" or "20분 뒤" in row["status"] or "19분 뒤" in row["status"])
    assert row["primary"]["action"] == "blog.retry" and row["primary"]["label"] == "다시 올리기"


async def test_row_without_reason_is_unchanged(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    data = fixture("export_v2.json")
    del data["posts"]["hotdeal"][0]["fail"]
    write_export(tmp_path / "blogauto.json", posts=data["posts"])
    blogauto_status(settings)
    row = next(x for x in (await state(bot))["todo"] if x["iid"] == "post-hotdeal-100")
    assert row["status"] == "업로드 실패"
