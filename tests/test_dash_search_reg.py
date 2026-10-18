"""구글 검색 등록 체크리스트 (주인 10/18 22:20): 블로그마다 새로 발행한 글 주소를 날마다 모아서 키자드에 붙여넣고 '등록함'.
글은 Blog-Auto 기록 파일 recent (딜봇 kv 에 14일 모음) · 표시는 딜봇에만 (search.check — 확인 창 없음, 되돌림) ·
할 일 한 줄 '구글 검색 등록 N개' (참고 — 아래 쪽) · 정리에 '구글 검색 등록할 글 N개' 한 줄."""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import rank
from dealbot.dashboard.actions import ActionError, clean_args
from dealbot.dashboard.blogauto import parse_v2
from dealbot.dashboard.searchreg import KV, SearchReg, day_label
from dealbot.notify.digest import Digests, waiting_sentences
from test_dash_visits import write_export
from test_notify_router import Env

TZ = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 18, 22, 30, tzinfo=TZ).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def row(pid: int, ts: float, blog: str = "issue", **kw: object) -> dict:
    ids = {"finance": "derenarchive", "issue": "derenpick", "hotdeal": "demiyum"}
    return {"pid": pid, "title": f"글 {pid}", "url": f"https://m.blog.naver.com/{ids[blog]}/2240000000{pid:02d}", "at": iso(ts), **kw}


class FakeDB:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v


# ---------------------------------------------------------------- 기록 파일 recent
def test_parse_reads_published_at_and_google_done_only_when_given() -> None:
    got = parse_v2({"recent": {"issue": [row(1, NOW, google_done=True, published_at="2026-10-18T21:00:00+09:00"),
                                         row(2, NOW, google="done"), row(3, NOW, google_done="yes")]}})["recent"]["issue"]
    assert got[0]["google_done"] is True and got[0]["published_at"] == "2026-10-18T21:00:00+09:00"
    assert got[1]["google_done"] is True and "google_done" not in got[2] and "published_at" not in got[2]


# ---------------------------------------------------------------- 모으기 · 날짜 묶음 · 14일
def test_groups_by_day_newest_first_keeps_14_days_and_collects_beyond_recent() -> None:
    db, clock = FakeDB(), [NOW]
    reg = SearchReg(db, tz="Asia/Seoul", now=lambda: clock[0])
    old = NOW - 14 * 86400  # 15일째 — 버림
    v2 = {"recent": {"issue": [row(3, NOW - 600), row(2, NOW - 86400)], "finance": [row(1, NOW - 3 * 86400, "finance")],
                     "hotdeal": [row(9, old, "hotdeal")]}}
    view = reg.view(v2)
    assert [g["label"] for g in view["days"]] == ["오늘", "어제", "10/15 (목)"] and view["left"] == 3
    assert view["keyzard"] == "https://keyzard.cc/nb"
    x = view["days"][0]["items"][0]
    assert x["url"] == "https://blog.naver.com/derenpick/224000000003"  # 키자드에 넣는 PC 주소
    assert x["chip"] == "Pick" and x["title"] == "글 3" and x["done"] is False and x["auto"] is False
    # 다음 읽기 때 recent 에서 빠진 글(10편 넘게 발행)도 남아 있다
    view = reg.view({"recent": {"issue": [row(4, NOW - 60)]}})
    assert [x["title"] for x in view["days"][0]["items"]] == ["글 4", "글 3"] and view["left"] == 4
    # 10/15 글은 10/28 까지 (오늘 포함 14일), 10/29 에는 빠진다
    clock[0] = NOW + 10 * 86400
    assert "10/15 (목)" in [g["label"] for g in reg.view({})["days"]]
    clock[0] = NOW + 11 * 86400
    assert all(g["label"] != "10/15 (목)" for g in reg.view({})["days"]) and reg.view({})["left"] == 3


def test_day_labels() -> None:
    d = datetime(2026, 10, 18, tzinfo=TZ).date()
    assert day_label(d, d) == "오늘" and day_label(d - timedelta(days=1), d) == "어제"
    assert day_label(d - timedelta(days=2), d) == "10/16 (금)"


def test_check_persists_and_undoes_and_auto_submit_shows_checked() -> None:
    db = FakeDB()
    reg = SearchReg(db, tz="Asia/Seoul", now=lambda: NOW)
    v2 = {"recent": {"issue": [row(1, NOW - 60), row(2, NOW - 120, google_done=True)]}}
    assert reg.view(v2)["left"] == 1  # 자동 등록된 글은 처음부터 '등록함'
    assert reg.check(["https://m.blog.naver.com/derenpick/224000000001"], True) == 1  # 모바일 주소로 와도 같은 글
    again = SearchReg(db, tz="Asia/Seoul", now=lambda: NOW)  # 다시 켜도 그대로 (딜봇 kv)
    items = {x["title"]: x for x in again.view(v2)["days"][0]["items"]}
    assert items["글 1"]["done"] is True and items["글 2"] == dict(items["글 2"], done=True, auto=True)
    assert again.view(v2)["left"] == 0 and KV in db.kv
    assert again.check(["https://blog.naver.com/derenpick/224000000001"], False) == 1 and again.view(v2)["left"] == 1
    assert again.check(["https://blog.naver.com/someone/1"], True) == 0  # 모르는 글


def test_clean_args_takes_naver_post_urls_and_a_bool() -> None:
    u = "https://blog.naver.com/derenpick/224000000001"
    assert clean_args("search.check", {"url": u, "done": True}) == {"urls": [u], "done": True}
    assert clean_args("search.check", {"urls": [u, u], "done": False}) == {"urls": [u], "done": False}
    for bad in ({"url": u}, {"url": u, "done": "true"}, {"url": "https://evil.example/x", "done": True},
                {"url": "http://blog.naver.com/a/1", "done": True}, {"urls": [], "done": True},
                {"urls": [u] * 61, "done": True}, {"url": "https://blog.naver.com/", "done": True}):
        with pytest.raises(ActionError):
            clean_args("search.check", bad)


# ---------------------------------------------------------------- 화면 · 누르기 (딜봇 안에서)
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def recent_now() -> dict:
    now = time.time()
    midnight = datetime.fromtimestamp(now, TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return {"issue": [row(40, midnight + (now - midnight) / 3), row(39, midnight - 3600)], "finance": [],
            "hotdeal": [row(41, midnight + (now - midnight) / 2, "hotdeal")]}


async def test_todo_row_blog_view_check_and_undo(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", recent=recent_now())
    blogauto_status(settings)
    bot.router.open({"src": "blogauto", "key": "draft:issue:31", "kind": "draft_ready", "iid": "post-issue-31",  # type: ignore[union-attr]
                     "title": "Pick #31 발행 대기", "at": time.time()})
    h = session_headers(bot)
    _, body, _ = await get_json(bot, "/dashboard/api/state", h)
    todo = body["todo"]
    by = {it["iid"]: it for it in todo}
    s = by["search"]
    assert s["title"] == "구글 검색 등록 3개" and s["sev"] == "info" and s["kind"] == "search_reg" and s["urgent"] is False
    assert s["primary"]["input"] == "goto" and s["primary"]["goto"] == "#/blog/search"
    assert rank.rank(dict(s), time.time()) == 50.0 and todo.index(s) > todo.index(by["post-issue-31"])  # 주인을 막는 일보다 아래
    sr = body["search"]
    assert sr["left"] == 3 and [g["label"] for g in sr["days"]] == ["오늘", "어제"]
    assert [x["chip"] for x in sr["days"][0]["items"]] == ["핫딜", "Pick"]
    # 확인 창 없이 바로 · 딜봇 kv 에 · Blog-Auto 로 보내지 않음
    urls = [x["url"] for x in sr["days"][0]["items"]]
    status, r, _ = await post_json(bot, "/dashboard/api/action", {"action": "search.check", "args": {"urls": urls, "done": True},
                                                                  "cid": "c_srch01"}, h)
    assert status == 200 and r["status"] == "ok" and "등록함" in r["text"]
    assert all(json.loads(bot.db.kv_get(KV) or "{}")[u]["done"] for u in urls)
    _, body, _ = await get_json(bot, "/dashboard/api/state", h)
    assert {it["iid"]: it for it in body["todo"]}["search"]["title"] == "구글 검색 등록 1개" and body["search"]["days"][0]["left"] == 0
    # 되돌리기
    status, r, _ = await post_json(bot, "/dashboard/api/action", {"action": "search.check", "args": {"url": urls[0], "done": False}}, h)
    assert status == 200 and r["status"] == "ok"
    _, body, _ = await get_json(bot, "/dashboard/api/state", h)
    assert body["search"]["left"] == 2
    # 다 하면 할 일 줄이 빠진다
    left = [x["url"] for g in body["search"]["days"] for x in g["items"] if not x["done"]]
    await post_json(bot, "/dashboard/api/action", {"action": "search.check", "args": {"urls": left, "done": True}}, h)
    _, body, _ = await get_json(bot, "/dashboard/api/state", h)
    assert "search" not in {it["iid"] for it in body["todo"]} and body["search"]["left"] == 0
    # 모르는 글 · 이상한 값
    status, r, _ = await post_json(bot, "/dashboard/api/action", {"action": "search.check",
                                                                  "args": {"url": "https://blog.naver.com/x/1", "done": True}}, h)
    assert status == 200 and r["code"] == "not_found"
    status, r, _ = await post_json(bot, "/dashboard/api/action", {"action": "search.check", "args": {"url": "https://x.example/1"}}, h)
    assert status == 400 and r["code"] == "bad_args"


async def test_digest_gets_one_search_line(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", recent=recent_now())
    blogauto_status(settings)
    brief = bot._todo_brief()
    assert {"kind": "search_reg", "title": "구글 검색 등록 3개", "iid": "search", "n": 3} in brief
    lines = waiting_sentences(brief)
    assert lines[-1] == "구글 검색 등록할 글 3개가 있어요." and not any("그 밖의" in x and "구글" in x for x in lines)
    assert waiting_sentences([{"kind": "search_reg", "title": "구글 검색 등록 0개", "n": 0}]) == []


def test_digest_build_has_the_search_line(tmp_path: Path) -> None:
    env = Env(tmp_path)
    try:
        todo = [{"kind": "link_request", "title": "무선청소기"}, {"kind": "search_reg", "title": "구글 검색 등록 2개", "n": 2}]
        dg = Digests(env.db, env.router, tz="Asia/Seoul", rng=random.Random(5), todo=lambda: todo)
        d = dg.build(datetime(2026, 10, 18).date(), "pm", env.clock())
        wait = d["sections"][0]
        assert wait["label"] == "기다리는 일" and wait["sentences"] == ["링크를 기다리는 딜 1개(무선청소기)가 있어요.",
                                                                       "구글 검색 등록할 글 2개가 있어요."]
    finally:
        env.db.close()
