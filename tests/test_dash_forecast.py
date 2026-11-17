"""예상 조회 한 줄 (주인 11/17 23:5x — Blog-Auto trend_forecast · posts[].forecast 선택 칸) → 글 자세히 보기 note."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.blogauto import parse_v2
from test_dash_state import state, write_export

LINE = "예상 조회 ↑ 이유: 뉴스 6시간 3건/24시간 4건 · 커뮤 화제 (예상 +5)"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def test_forecast_parse_is_optional_and_one_line() -> None:
    data = fixture("export_v2.json")
    assert all("forecast" not in p for p in parse_v2(data)["posts"]["issue"])  # 예전 Blog-Auto
    data["posts"]["issue"][0]["forecast"] = LINE
    data["posts"]["issue"][1]["forecast"] = "가\n나" + "x" * 400
    data["posts"]["issue"][2]["forecast"] = 3
    got = [p.get("forecast") for p in parse_v2(data)["posts"]["issue"]]
    assert got[0] == LINE and got[1] is not None and "\n" not in got[1] and len(got[1]) <= 221 and got[2] is None


async def test_state_shows_forecast_as_post_note(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    posts = fixture("export_v2.json")["posts"]
    posts["issue"][0]["forecast"] = LINE
    write_export(tmp_path / "blogauto.json", posts=posts)
    blogauto_status(settings)
    body = await state(bot)
    raw = json.dumps(body, ensure_ascii=False)
    assert LINE in raw
    pid = posts["issue"][0]["pid"]
    items = [x for x in body["todo"] if x["iid"] == f"post-issue-{pid}"] + [
        x for b in body["blogs"].values() for x in b.get("waiting") or [] if x.get("iid") == f"post-issue-{pid}"]
    assert items and all(x["detail"].get("note") == LINE for x in items)
