"""블로그 이름 바뀜 (주인 11/13): hotdeal 블로그 = 오늘의 라커룸(스포츠) · finance 블로그 = 다시 보는 드라마.
이름은 Blog-Auto 가 status.blogs.<블로그>.name·short 로 알려 주면 그 이름, 없으면 딜봇 기본 이름. 새 채널이 켜진 블로그는
핫딜 글 단추(지금 한 편·내 후기)와 아카이브 스캔 단추(다시 찾기·새 글·정리본·소재)를 숨긴다 — 딜봇 채널(텔레그램·인스타 등)은 그대로."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import actions, searchreg, state as dstate, statscap, todo
from test_dash_state import state, write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _export(tmp_path: Path, **extra: dict) -> None:
    data = fixture("export_v2.json")
    base = {k: v for k, v in data["status"]["blogs"]["issue"].items() if k != "name"}
    data["status"]["blogs"]["hotdeal"] = {**base, **extra.get("hotdeal", {})}
    data["status"]["blogs"]["finance"] = {**base, **extra.get("finance", {})}
    write_export(tmp_path / "blogauto.json", status=data["status"])


def _acts(blog: dict) -> set[str]:
    return {a["action"] for a in blog["actions"]}


def test_default_names_are_the_new_channel_names() -> None:
    assert dstate.BLOG_LABELS["hotdeal"] == "오늘의 라커룸" and dstate.BLOG_LABELS["finance"] == "다시 보는 드라마"
    assert dstate.CHIPS["hotdeal"] == "라커룸" and dstate.CHIPS["finance"] == "드라마" and dstate.CHIPS["issue"] == "Pick"
    for names in (searchreg.CHIPS, actions.BLOG_NAMES, statscap.BLOG_NAMES):
        assert names["hotdeal"] == "라커룸" and names["finance"] == "드라마"
    assert todo.BLOG_LABELS["hotdeal"] == "오늘의 라커룸" and todo.BLOG_LABELS["dealbot"] == "핫딜 봇"  # 딜봇 이름은 그대로


async def test_names_follow_blog_auto_status_and_old_exports_use_defaults(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    blogauto_status(settings)
    _export(tmp_path)
    blogs = (await state(bot))["blogs"]
    assert blogs["hotdeal"]["label"] == "오늘의 라커룸" and blogs["hotdeal"]["chip"] == "라커룸"
    assert blogs["finance"]["label"] == "다시 보는 드라마" and blogs["finance"]["chip"] == "드라마"
    _export(tmp_path, hotdeal={"name": "오늘의 라커룸 2", "short": "락커", "channel": "sports", "blog_off": True})
    blogs = (await state(bot))["blogs"]
    assert blogs["hotdeal"]["label"] == "오늘의 라커룸 2" and blogs["hotdeal"]["chip"] == "락커"


async def test_channel_blogs_hide_deal_blog_and_archive_panels(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    blogauto_status(settings)
    _export(tmp_path)
    blogs = (await state(bot))["blogs"]
    assert "blog.hotdeal_now" in _acts(blogs["hotdeal"]) and {"blog.scan", "blog.write", "blog.topic_seed"} <= _acts(blogs["finance"])
    _export(tmp_path, hotdeal={"name": "오늘의 라커룸", "short": "라커룸", "channel": "sports", "blog_off": True},
            finance={"name": "다시 보는 드라마", "short": "드라마", "channel": "drama"})
    blogs = (await state(bot))["blogs"]
    assert not {"blog.hotdeal_now", "blog.post_notes", "blog.topic_seed"} & _acts(blogs["hotdeal"])
    assert not {"blog.scan", "blog.write", "blog.ledger", "blog.topic_seed"} & _acts(blogs["finance"])
    assert blogs["finance"]["scan"] is None
    assert {"blog.pause", "blog.mode"} & _acts(blogs["hotdeal"])  # 멈추기 같은 공통 단추는 그대로
    assert "blog.issue" in _acts(blogs["issue"])  # Pick 은 그대로
