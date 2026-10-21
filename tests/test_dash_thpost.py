"""스레드 글·통계 (주인 10/21 — Blog-Auto threads_api · 공식 Threads API · 주인이 쓴 글만). 네트워크 없이.

1) 기록 파일 threads 칸 → blogauto._threads (줄마다 걸러 이상한 줄만 빠짐 · 주소는 https 만)
2) state['thpost'] (thpost.py — 화면 글은 서버에서: 연결 · 오늘 셈/한도 · 쓰기 칸 · 예약 줄(취소) · 최근 글 · 새 답글(답글 달기) · 통계)
3) 다리 일 blog.threads_post (★) · blog.threads_cancel · blog.threads_reply (★) — 계약 action_threads_*.json 과 같은 모양
AI 가 쓰는 글·답글 초안·저절로 올리기는 없다. 화면 파일은 다른 세션의 스레드 화면이 그린다 (여기서 만들지 않음)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import thpost
from dealbot.dashboard.actions import (
    BLOG_ACTIONS,
    CONFIRM,
    ActionError,
    clean_args,
    needs_confirm,
    target_of,
)
from dealbot.dashboard.blogauto import _threads
from dealbot.dashboard.state import act as state_act
from test_dash_api_actions import act, confirmed, photo_upload
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 18, 16, 20, tzinfo=KST).timestamp()


def th_fx() -> dict:
    return fixture("export_v2.json")["threads"]


# ---------------------------------------------------------------- 1) 기록 파일 칸


def test_reader_keeps_contract_rows_and_drops_bad_ones() -> None:
    got = _threads(th_fx())
    assert got is not None and got["connected"] is True and got["token_days"] == 41 and (got["today"], got["cap"]) == (2, 5)
    assert [q["status"] for q in got["queue"]] == ["scheduled", "posted", "failed"]
    assert "\n\n오늘 올린 핫딜" in got["queue"][0]["text"]  # 여러 줄 그대로 (제휴 고지가 맨 위)
    assert got["posts"][0]["permalink"].startswith("https://www.threads.com/")
    assert got["replies"]["new"] == 1 and got["replies"]["items"][0]["id"] == "18012345678901234"
    assert got["stats"]["totals"]["views"] == 1830 and got["stats"]["followers"] == 212 and len(got["stats"]["days"]) == 7
    bad = th_fx()
    bad["queue"].append({"id": "../evil", "status": "scheduled", "text": "x"})
    bad["queue"].append({"id": "th-00000000", "status": "weird", "text": "x"})
    bad["posts"].append({"id": "1", "text": "x", "permalink": "javascript:alert(1)"})
    bad["replies"]["items"].append({"id": "abc", "text": "x"})
    bad["stats"]["days"].append({"day": "<b>", "views": 1})
    got = _threads(bad)
    assert len(got["queue"]) == 3 and got["posts"][-1]["permalink"] == "" and got["replies"]["new"] == 1
    assert len(got["stats"]["days"]) == 7
    assert _threads(None) is None and _threads({"queue": []}) is None  # 예전 Blog-Auto


# ---------------------------------------------------------------- 2) state['thpost']


def test_state_builder_texts_and_actions() -> None:
    s = thpost.build(_threads(th_fx()), KST, NOW, state_act)
    assert s["connected"] and s["status"] == "연결됨 · 토큰 41일 남음" and s["cap_text"] == "오늘 2/5편" and s["limit"] == 500
    c = s["compose"]
    assert c["action"] == "blog.threads_post" and c["input"] == "text" and c["confirm"] is True and c["args"] == {}
    q = {x["id"]: x for x in s["queue"]}
    assert q["th-1a2b3c4d"]["when"] == "오늘 21:30" and q["th-1a2b3c4d"]["status_text"] == "예약"
    assert q["th-1a2b3c4d"]["cancel"] == state_act("blog.threads_cancel", "취소", {"id": "th-1a2b3c4d"})
    assert q["th-1a2b3c4d"]["cancel"]["confirm"] is False
    assert q["th-5e6f7a8b"]["cancel"] is None and q["th-9c0d1e2f"]["status_text"].startswith("실패 · ")
    assert s["queue"][0]["status"] == "scheduled"  # 예약이 먼저
    p = s["posts"]
    assert p[0]["when"] == "오늘 09:02" and p[1]["when"] == "어제 20:15" and p[0]["stats"]["views"] == 420
    r = s["replies"]["items"][0]
    assert s["badge"] == 1 and r["username"] == "yoona_home" and r["text"] == "아직 이 가격인가요?"
    assert r["reply"]["action"] == "blog.threads_reply" and r["reply"]["args"] == {"media_id": "18012345678901234"}
    assert r["reply"]["confirm"] is True and r["reply"]["input"] == "text"
    st = s["stats"]
    assert [t["label"] for t in st["totals"]] == ["조회", "좋아요", "답글", "리포스트", "인용"] and st["totals"][0]["n"] == 1830
    assert st["days"][0] == {"day": "2026-10-12", "label": "10/12 (월)", "views": 180} and st["range"] == "최근 7일"


def test_not_connected_and_cap_reached_and_old_blogauto() -> None:
    off = thpost.build(_threads({"connected": False, "how": "", "queue": []}), KST, NOW, state_act)
    assert off["status"] == "스레드 연결 필요" and "THREADS_ACCESS_TOKEN" in off["how"] and off["compose"] is None
    full = dict(th_fx(), today=5)
    s = thpost.build(_threads(full), KST, NOW, state_act)
    assert "한도에 닿았어요" in s["cap_text"]
    soon = dict(th_fx(), token_days=3)
    assert "곧 만료" in thpost.build(_threads(soon), KST, NOW, state_act)["status"]
    assert thpost.build(None, KST, NOW, state_act) is None
    assert thpost.build(_threads(th_fx()), KST, NOW, state_act, bridge_ok=False)["compose"]["disabled"] is True


async def test_state_has_thpost_from_the_export(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    s = (await state(bot))["thpost"]
    assert s["connected"] and s["badge"] == 1 and s["compose"]["action"] == "blog.threads_post"
    assert "THtoken" not in json.dumps(s)
    data = fixture("export_v2.json")
    del data["threads"]
    write_export(tmp_path / "blogauto.json", threads=None)
    assert (await state(bot))["thpost"] is None


# ---------------------------------------------------------------- 3) 다리 일


def test_clean_args_match_the_contract_fixtures() -> None:
    for name in ("threads_post", "threads_cancel", "threads_reply"):
        assert name in BLOG_ACTIONS and name in fixture("status_blogauto.json")["actions"]
    assert {"blog.threads_post", "blog.threads_reply"} <= CONFIRM and "blog.threads_cancel" not in CONFIRM
    want = fixture("action_threads_post.json")["args"]
    out = clean_args("blog.threads_post", {"text": want["text"], "image": "9f0e1d2c3b4a59687766554433221100", "at": "21:30",
                                           "day": "today"})
    assert list(out) == list(want) and {k: v for k, v in out.items() if k != "image"} == {k: v for k, v in want.items() if k != "image"}
    assert clean_args("blog.threads_post", {"text": "지금"}) == {"text": "지금"}
    assert clean_args("blog.threads_post", {"text": "x", "at": 1760780000})["at"] == 1760780000.0
    for bad in ({"text": ""}, {"text": "가" * 501}, {"text": "x", "at": "25:00", "day": "today"}, {"text": "x", "at": "10:00"},
                {"text": "x", "at": 5}, {"text": "x", "image": "../../etc/passwd"}, {"text": "x", "at": True}):
        with pytest.raises(ActionError):
            clean_args("blog.threads_post", bad)
    assert clean_args("blog.threads_cancel", {"id": "th-1a2b3c4d"}) == fixture("action_threads_cancel.json")["args"]
    r = fixture("action_threads_reply.json")["args"]
    assert clean_args("blog.threads_reply", dict(r)) == r
    for name, bad in (("blog.threads_cancel", {"id": "1a2b"}), ("blog.threads_reply", {"media_id": "abc", "text": "x"}),
                      ("blog.threads_reply", {"media_id": "1", "text": ""})):
        with pytest.raises(ActionError):
            clean_args(name, bad)
    assert target_of("blog.threads_post", out).startswith("thpost-p") and target_of("blog.threads_post", out) == target_of("blog.threads_post", dict(out))
    assert target_of("blog.threads_post", {"text": "다른 글"}) != target_of("blog.threads_post", out)
    assert target_of("blog.threads_cancel", {"id": "th-1a2b3c4d"}) == "thpost-th-1a2b3c4d"
    assert needs_confirm("blog.threads_post", out) and needs_confirm("blog.threads_reply", r)
    assert not needs_confirm("blog.threads_cancel", {"id": "th-1a2b3c4d"})


async def test_post_with_photo_needs_confirm_and_reaches_the_spool(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings)
    want = fixture("action_threads_post.json")
    h = session_headers(bot)
    up = photo_upload(bot, h)
    args = {"text": want["args"]["text"], "image": up, "at": "21:30", "day": "today"}
    status, body = await act(bot, "blog.threads_post", args, h)
    assert status == 409 and body["title"] == "스레드 예약할까요?"
    status, body = await confirmed(bot, "blog.threads_post", args, h)
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "confirmed")} == {k: want[k] for k in ("v", "action", "blog", "confirmed")}
    assert f["target"].startswith("thpost")
    got = dict(f["args"])
    assert Path(got.pop("image")).name == f"{up}.jpg"
    assert got == {k: v for k, v in want["args"].items() if k != "image"} and list(f["args"]) == list(want["args"])
    status, body = await confirmed(bot, "blog.threads_post", {"text": "바로 올릴 글"}, h)
    assert status == 202
    status, body = await act(bot, "blog.threads_post", {"text": "x", "image": "0" * 32}, h, ctoken=None)
    assert status in (400, 409)


async def test_cancel_and_reply_reach_the_spool(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings)
    h = session_headers(bot)
    status, body = await act(bot, "blog.threads_cancel", {"id": "th-1a2b3c4d"}, h)
    assert status == 202, body  # 확인 창 없음
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert f["action"] == "threads_cancel" and f["args"] == {"id": "th-1a2b3c4d"} and f["confirmed"] is False
    want = fixture("action_threads_reply.json")
    status, body = await confirmed(bot, "blog.threads_reply", dict(want["args"]), h)
    assert status == 202
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert f["args"] == want["args"] and f["confirmed"] is True and f["action"] == "threads_reply"


async def test_old_blogauto_without_threads_actions_is_unsupported(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if not a.startswith("threads_")])
    status, body = await act(bot, "blog.threads_cancel", {"id": "th-1a2b3c4d"})
    assert status == 503 and body["code"] == "unsupported"
