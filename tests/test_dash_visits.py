"""서로이웃 (주인 10/18 — Blog-Auto neighbors-up): 보호조치 문구 알림(neighbors_protect)은 바로 푸시 · 할 일 블로그 줄 ·
'오늘 들를 이웃 글'(visits) 할 일 한 줄(참고, 아래 쪽) + 블로그 탭 카드 · 했어요(visit_done) 는 Blog-Auto 에 그대로 · 서로이웃 한 줄."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import (
    ORIGIN,
    Clock,
    PushServer,
    blogauto_status,
    decrypt,
    fixture,
    get_json,
    post_json,
    push_settings,
    session_headers,
    subscription,
)
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.bridge.kakao_out import KakaoOut
from dealbot.config import Settings
from dealbot.dashboard.blogauto import parse_v2
from dealbot.dashboard.state import neighbors_line
from dealbot.notify import iid as iidmod
from dealbot.notify import router as routermod
from dealbot.notify.ingest import clean_open
from dealbot.notify.router import NotifyRouter
from dealbot.storage.db import Database

VISIT = fixture("export_v2.json")["visits"][0]


# ---------------------------------------------------------------- 보호조치 알림
def test_neighbors_protect_is_urgent_and_lands_on_the_blog_buddy_row() -> None:
    assert routermod.urgency("neighbors_protect") == "URGENT"
    assert fixture("urgency.json")["neighbors_protect"] == "URGENT"
    assert iidmod.iid_for("neighbors:protect:issue:2026-10-18", "issue", src="blogauto") == "blog-issue-buddy"
    assert iidmod.iid_for("neighbors:config", "finance", src="blogauto") == "sys-blogauto"  # 설정 문제는 그대로 시스템 줄
    # 예전 Blog-Auto iid 표는 이 키를 sys-blogauto 로 보낸다 → 딜봇이 아는 줄로 (블로그 줄)
    raw = dict(fixture("notice_open.json"), key="neighbors:protect:issue:2026-10-18", kind="neighbors_protect", iid="sys-blogauto",
               title="Pick 서로이웃 보호조치 문구")
    assert clean_open(raw)["iid"] == "blog-issue-buddy"  # type: ignore[index]
    assert clean_open(dict(raw, key="err:main:x"))["iid"] == "sys-blogauto"  # type: ignore[index]


async def test_neighbors_protect_is_pushed_without_waiting_for_the_digest(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    clock = Clock()
    server = PushServer(201)
    r = NotifyRouter(db, data_dir=tmp_path, http=server.client(), public_url=ORIGIN, tz="Asia/Seoul",
                     kakao=KakaoOut(tmp_path, bridge_ok=lambda: True, clock=clock), clock=clock)
    body, ua = subscription()
    from dealbot.notify import webpush

    r.store.add_sub(endpoint=body["endpoint"], endpoint_hash=webpush.endpoint_hash(body["endpoint"]), p256dh=body["keys"]["p256dh"],
                    auth=body["keys"]["auth"], label="Galaxy", session="s1", now=clock())
    r.open({"src": "blogauto", "key": "neighbors:protect:issue:2026-10-18", "kind": "neighbors_protect", "blog": "issue",
            "iid": "blog-issue-buddy", "title": "Pick 서로이웃 보호조치 문구", "body": "네이버 앱으로 확인해 주세요", "at": clock()})
    clock.tick(21)
    await r.tick()
    [p] = [decrypt(x.content, ua) for x in server.requests]
    assert p["tag"] == "blog-issue-buddy" and p["urgent"] is True
    db.close()


# ---------------------------------------------------------------- 기록 파일 visits · neighbors
def test_parse_keeps_naver_posts_only_and_no_key_means_none() -> None:
    assert parse_v2({})["visits"] is None  # 예전 Blog-Auto — 화면이 숨김
    bad = [dict(VISIT, id="visit-issue-x"), dict(VISIT, url="https://evil.example/p"), dict(VISIT, blog="finance"), "x",
           dict(VISIT, id="visit-finance-3", blog="finance", status="done", comment="token=abcdef0123456789abcdef")]
    got = parse_v2({"visits": [VISIT, *bad]})["visits"]
    assert [v["id"] for v in got] == ["visit-issue-12", "visit-finance-3"]
    assert got[0]["url"].startswith("https://m.blog.naver.com/") and got[0]["blog_name"] == "미니의 스타일 노트"
    assert got[1]["status"] == "done" and "abcdef0123456789" not in got[1]["comment"]
    st = parse_v2(fixture("export_v2.json"))["status"]["blogs"]["issue"]["neighbors"]
    assert st == {"cap_today": 18, "cap_range": [15, 25], "reduced": False, "accepted_today": 2}


def test_neighbors_line_is_one_quiet_line() -> None:
    assert neighbors_line({"cap_today": 18, "cap_range": [15, 25], "reduced": False, "accepted_today": 3}) == \
        "이웃 신청 오늘 18건까지 · 받은 신청 수락 3건"
    assert neighbors_line({"cap_today": None, "cap_range": [8, 12], "reduced": True, "accepted_today": None}) == \
        "이웃 신청 오늘 8~12건 · 줄인 기간"
    assert neighbors_line(None) is None


# ---------------------------------------------------------------- 화면 (할 일 · 블로그 탭)
def write_export(path: Path, **changes: object) -> None:
    data = fixture("export_v2.json")
    data["written_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    data["todo"]["generated_at"] = data["written_at"]
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def state(bot: DealBot) -> dict:
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    return body


async def test_visits_stay_on_the_blog_tab_not_in_todo(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    more = [dict(VISIT, id=f"visit-issue-{n}", title=f"이웃 글 {n}") for n in (13, 14)] + \
        [dict(VISIT, id="visit-finance-5", blog="finance", blog_name="절약 일기"), dict(VISIT, id="visit-issue-9", status="done")]
    write_export(tmp_path / "blogauto.json", visits=[VISIT, *more])
    blogauto_status(settings)
    bot.router.open({"src": "blogauto", "key": "draft:issue:31", "kind": "draft_ready", "iid": "post-issue-31",  # type: ignore[union-attr]
                     "title": "Pick #31 발행 대기", "at": time.time()})
    body = await state(bot)
    todo = body["todo"]
    assert "visits" not in {it["iid"] for it in todo}  # 주인 10/23: 할 일 알림에는 넣지 않음 — 블로그 화면 '이웃' 칸에서만
    # 블로그 탭: 그 블로그의 들를 글 · 서로이웃 한 줄
    issue, fin = body["blogs"]["issue"], body["blogs"]["finance"]
    assert [x["id"] for x in issue["visits"]] == ["visit-issue-12", "visit-issue-13", "visit-issue-14"]
    assert [x["id"] for x in fin["visits"]] == ["visit-finance-5"] and body["blogs"]["hotdeal"]["visits"] == []
    assert issue["neighbors"] == "이웃 신청 오늘 18건까지 · 받은 신청 수락 2건" and fin["neighbors"] is None


async def test_no_visits_key_or_nothing_left_shows_nothing(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    blogauto_status(settings)
    write_export(tmp_path / "blogauto.json", visits=None)  # 예전 Blog-Auto
    body = await state(bot)
    assert "visits" not in {it["iid"] for it in body["todo"]} and body["blogs"]["issue"]["visits"] is None
    write_export(tmp_path / "blogauto.json", visits=[dict(VISIT, status="done")])
    body = await state(bot)
    assert "visits" not in {it["iid"] for it in body["todo"]} and body["blogs"]["issue"]["visits"] == []


# ---------------------------------------------------------------- 했어요 (visit_done)
async def test_visit_done_goes_to_blogauto_as_is_without_a_confirm(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    h = session_headers(bot)
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "blog.visit_done", "args": {"id": "visit-issue-12"},
                                                                     "cid": "c_9a2e4f"}, h)
    assert status == 202 and body["status"] == "queued"  # 확인 창 없음
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    want = fixture("action_visit_done.json")
    assert set(f) == set(want)  # type: ignore[arg-type]
    assert {k: f[k] for k in ("v", "action", "blog", "args", "target", "confirmed")} == {  # type: ignore[index]
        k: want[k] for k in ("v", "action", "blog", "args", "target", "confirmed")}
    for bad in ({"id": "visit-issue-x"}, {"id": "visit-shop-1"}, {}, {"id": ["visit-issue-12"]}):
        status, b, _ = await post_json(bot, "/dashboard/api/action", {"action": "blog.visit_done", "args": bad}, h)
        assert status == 400 and b["code"] == "bad_args", bad
    # 예전 Blog-Auto (visit_done 을 모름) → 기다리는 중
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "visit_done"])
    status, b, _ = await post_json(bot, "/dashboard/api/action", {"action": "blog.visit_done", "args": {"id": "visit-issue-13"}}, h)
    assert status == 503 and b["code"] == "unsupported"
