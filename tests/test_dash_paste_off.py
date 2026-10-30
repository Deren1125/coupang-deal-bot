"""대시보드 전면 손보기 (주인 10/30 '대시보드도 지금 바뀐 기능 맞춰서 전면 수정해'):
붙여넣기 방식(10/29)·네이버 접속 끔(10/30)·토스 점검(~11/4)이면 서버가 할 수 없는 단추(발행·예약·다시 올리기·고치기·로그인)를 숨기고
붙여넣기 글은 '오늘 할 일'로 · 쉬는 것 때문에 생긴 지난 할 일은 숨김 · 할 일 칸에 '⚡ 막 뜨는 중'·'쉬는 중'."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import logins, routine
from test_dash_state import state, write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _export(tmp_path: Path, *, paste: bool = False, off: bool = False) -> dict:
    data = fixture("export_v2.json")
    data["status"]["blogs"]["issue"]["paste"] = paste
    data["status"]["naver_off"] = off
    data["posts"]["issue"].append({**data["posts"]["issue"][1], "pid": 60, "status": "failed", "needs": [], "paste": True,
                                   "schedule": None, "title": "붙여넣기 기다리는 글"})
    write_export(tmp_path / "blogauto.json", status=data["status"], posts=data["posts"])
    return data


def _acts(it: dict) -> list[str]:
    return [a["action"] for a in (it["primary"], *it["more"]) if a]


async def test_paste_mode_sends_posts_to_today_and_hides_server_publish(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    _export(tmp_path, paste=True)
    blogauto_status(settings)
    bot.router.open({"src": "blogauto", "key": "draft:issue:60", "kind": "draft_ready", "iid": "post-issue-60",  # type: ignore[union-attr]
                     "title": "Pick #60 붙여넣기 준비", "at": time.time()})
    body = await state(bot)
    by = {it["iid"]: it for it in body["todo"]}
    it = by["post-issue-60"]
    assert it["kind"] == "paste" and it["status"].startswith("붙여넣기 대기")
    assert it["primary"]["goto"] == "#/todo" and not {"blog.publish", "blog.schedule", "blog.retry"} & set(_acts(it))
    assert "원고 고치기" in [a["label"] for a in it["more"]]  # 붙여넣기면 원고만 고침
    assert "post-issue-47" in {w["iid"] for w in body["blogs"]["issue"]["waiting"]}  # 예약 줄은 서버가 안 올리니 기다리는 글로
    issue = body["blogs"]["issue"]
    assert issue["schedule"] is None and issue["head"].startswith("붙여넣기 대기 ") and issue["neighbors"] is None
    acts = [a["action"] for a in issue["actions"]]
    assert "blog.mode" not in acts and "blog.drafts_cleanup" not in acts and {"what": "creator"} not in [a["args"] for a in issue["actions"]]
    assert all("edit" not in r for r in issue["recent"])  # 발행 글 고치기(네이버를 엶) 없음
    fin = body["blogs"]["finance"]  # 붙여넣기가 아닌 블로그는 그대로
    assert fin["head"] is None and "blog.mode" in [a["action"] for a in fin["actions"]]


async def test_naver_off_hides_login_buttons_and_old_login_rows(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    _export(tmp_path, off=True)
    blogauto_status(settings)
    now = time.time()
    for key, iid, kind in (("login_down:issue", "conn-naver-issue", "login"), ("autolink:login", "sys-autolink", "autolink_login"),
                           ("draft:finance:12", "post-finance-12", "draft_ready")):
        bot.router.open({"src": "blogauto" if "draft" in key else "dealbot", "key": key, "kind": kind, "iid": iid,  # type: ignore[union-attr]
                         "title": key, "at": now})
    body = await state(bot)
    iids = {it["iid"] for it in body["todo"]}
    assert "conn-naver-issue" not in iids and "sys-autolink" not in iids  # 네이버 로그인·쇼핑커넥트 로그인 줄은 쉬는 동안 숨김
    for it in body["todo"]:
        assert "blog.login_check" not in _acts(it)
        assert not any(a and a["action"] == "login.remote" and a["args"].get("target") in ("finance", "issue", "hotdeal")
                       for a in (it["primary"], *it["more"])), it["iid"]
    fin = next(it for it in body["todo"] if it["iid"] == "post-hotdeal-100")  # 실패 글: 서버 다시 올리기 없음
    assert "blog.retry" not in _acts(fin)
    c = body["connect"]
    assert all(v["action"] is None and v["login"] == "off" for v in c["naver"].values())
    assert body["blogs"]["issue"]["login"] == "off" and body["blogs"]["issue"]["head"].startswith("붙여넣기 대기")
    assert bot.dash_state._sc_row({"state": "off"}, now)["text"].startswith("꺼 둠 (10/30")


async def test_toss_pause_line_login_row_and_routine_extras(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    data = _export(tmp_path, off=True)
    data["trends"]["items"][0]["badge"] = "⚡ 막 뜨는 중"
    data["trends"]["items"][0]["written"] = False
    write_export(tmp_path / "blogauto.json", status=data["status"], posts=data["posts"], trends=data["trends"])
    blogauto_status(settings)
    bot.settings.autolink.toss.no_link_until = "2999-01-01T00:00:00+09:00"
    body = await state(bot)
    assert body["deal"]["status"]["toss"].startswith("토스 점검 중 — 11/4까지")
    toss = next(r for r in logins.rows(bot) if r["id"] == "toss")
    assert toss["state"] == "off" and toss["button"] is None and toss["text"].startswith("11/4까지 필요 없음")
    ex = routine.extras(bot)
    assert ex["rising"] == [data["trends"]["items"][0]["keyword"]]
    assert [r[:6] for r in ex["rests"]] == ["네이버 계정", "토스 쉐어링"]
    assert routine.view(bot)["rests"] == ex["rests"]
    bot.settings.autolink.toss.no_link_until = None
    write_export(tmp_path / "blogauto.json")
    assert routine.extras(bot)["rests"] == [] and next(r for r in logins.rows(bot) if r["id"] == "toss")["state"] != "off"
