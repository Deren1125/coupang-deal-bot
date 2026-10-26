"""대시보드 룩북 릴스 화면 (주인 10/25 — 실제 지그재그 상품 → 얼굴 없는 AI 모델 영상 → 주인이 새 인스타 계정에 유행 음악을 붙여 올림). 네트워크 없이.

1) 기록 파일 lookbook 칸 (계약 export_v2.json — Blog-Auto 와 같은 파일): 칸이 없거나 이상한 줄은 버림 · 링크는 지그재그·에이블리 https 만 ·
   파일 이름은 번호에서 정해진 것만.
2) 화면 값 state.lookbook: 쉬운 말 상태(서버 준비 중 / 사진 만드는 중 / 완성 / 상품과 달라 다시 / 실패) · 영상·표지 주소 · 룩 목록 ·
   올리기 전 확인 · 다리 일 blog.lookbook_posted {id, url} · lookbook_drop · lookbook_redo (계약 action_lookbook_posted.json · 확인 창 없음).
3) 할 일 '룩북 릴스 올리기' (완성됐는데 안 올린 편이 있을 때만).
4) 영상·표지 /dashboard/lookbook/<id>/video.mp4 · cover.jpg: 로그인 아래 · blog_view 사본 먼저, 없으면 Blog-Auto var/lookbook/<id>/ ·
   바로가기·밖으로 나가는 이름 안 됨 · Range → 206 (아이폰 사파리 <video>).
5) lookbook.js: 처음 열 때만 (meta sb-lookbook) · 바깥 주소 없음 · 한국어 단추 · SNS 탭 '룩북 릴스' 칸 (10/26 — 예전 '#/blog/lookbook' 은 새 자리로).
6) 사진 시험 (주인 10/26): 시험 줄은 릴스 목록·할 일에 안 들어가고 첫 상태 값엔 수(trial_n)·지금 양식만 ·
   사진은 GET /dashboard/api/lbtrial (바탕 릴스마다 묶음) · 다리 일 blog.lookbook_trial {base?} · blog.lookbook_style {style}
   (계약 action_lookbook_trial.json · action_lookbook_style.json).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import api as dash_api
from dealbot.dashboard import lbview, shell, webapp
from dealbot.dashboard.actions import (
    BLOG_ACTIONS,
    ActionError,
    clean_args,
    needs_confirm,
    reel_url,
    target_of,
)
from dealbot.dashboard.blogauto import _lookbook
from dealbot.dashboard.state import act as state_act
from dealbot.dashboard.state import goto as state_goto
from test_dash_api_actions import act
from test_dash_state import state, write_export

STATIC = webapp.STATIC_DIR
TZ = ZoneInfo("Asia/Seoul")
JID = "lb-20261026-0712"
LB_ACTS = ("lookbook_posted", "lookbook_drop", "lookbook_redo")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "var" / "metrics" / "blogauto.json")
    (tmp_path / "var" / "metrics").mkdir(parents=True)
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def export_path(tmp_path: Path) -> Path:
    return tmp_path / "var" / "metrics" / "blogauto.json"


def old_actions() -> list[str]:
    return [a for a in fixture("status_blogauto.json")["actions"] if not a.startswith("lookbook_")]


# ---------------------------------------------------------------- 다리 일 (Blog-Auto 와 같은 계약)
def test_actions_match_the_contract_fixture() -> None:
    want = fixture("action_lookbook_posted.json")
    assert want["action"] == "lookbook_posted" and want["confirmed"] is False and want["blog"] is None
    for name in LB_ACTS:
        assert name in BLOG_ACTIONS and name in fixture("status_blogauto.json")["actions"]
    out = clean_args("blog.lookbook_posted", {**want["args"], "blog": "issue", "extra": 1})
    assert out == want["args"] and not needs_confirm("blog.lookbook_posted", out)
    assert target_of("blog.lookbook_posted", out) == want["target"]
    for name in ("lookbook_drop", "lookbook_redo"):
        out = clean_args(f"blog.{name}", {"id": JID, "url": "x"})
        assert out == {"id": JID} and not needs_confirm(f"blog.{name}", out) and target_of(f"blog.{name}", out) == want["target"]
    for bad in ({}, {"id": 3}, {"id": "lb-2026102-0712"}, {"id": "lb-20261026-0712 "}, {"id": "../lb-20261026-0712"},
                {"id": "lb-٢٠٢٦١٠٢٦-0712"}):
        for name in LB_ACTS:
            with pytest.raises(ActionError):
                clean_args(f"blog.{name}", {**bad, "url": want["args"]["url"]})
    with pytest.raises(ActionError) as e:
        clean_args("blog.lookbook_posted", {"id": JID})
    assert "릴스 주소" in e.value.text


def test_posted_needs_the_music_and_ai_label_checks() -> None:
    """'올림 완료' 필수 체크 (주인 10/26 — 쇼츠 총괄 제작 기준 v1 P0-8 · M3·B7): 앱에서 음악 붙임 · AI 레이블 켬 — 체크 없이는 저장 안 됨.
    고정 댓글(pin, D3)은 선택 — 체크하면 같이 실림. 화면 체크리스트도 같은 키 (need 둘 · 선택 칸)."""
    url = fixture("action_lookbook_posted.json")["args"]["url"]
    for args in ({}, {"music": True}, {"ai_label": True}, {"music": "true", "ai_label": 1}, {"music": True, "ai_label": False}):
        with pytest.raises(ActionError) as e:
            clean_args("blog.lookbook_posted", {"id": JID, "url": url, **args})
        assert "음악" in e.value.text and "AI 정보" in e.value.text
    ok = clean_args("blog.lookbook_posted", {"id": JID, "url": url, "music": True, "ai_label": True, "pin": True})
    assert ok == {"id": JID, "url": url, "music": True, "ai_label": True, "pin": True}
    assert "pin" not in clean_args("blog.lookbook_posted", {"id": JID, "url": url, "music": True, "ai_label": True, "pin": "yes"})
    from dealbot.dashboard import lbview

    need = [c["k"] for c in lbview.CHECKLIST if c["need"]]
    assert need == ["music", "ai_label"] and any(c["k"] == "pin" and not c["need"] for c in lbview.CHECKLIST)


def test_reel_url_is_checked_by_its_letters_only_and_normalized() -> None:
    good = {
        "https://www.instagram.com/reel/DAbCdEfGhIj/": "https://www.instagram.com/reel/DAbCdEfGhIj/",
        "https://www.instagram.com/reel/DAbCdEfGhIj/?igsh=MWQ1ZGUxMzBkMA==": "https://www.instagram.com/reel/DAbCdEfGhIj/",
        "https://instagram.com/reel/DAbCdEfGhIj": "https://www.instagram.com/reel/DAbCdEfGhIj/",
        "https://m.instagram.com/reels/DAbCdEfGhIj/": "https://www.instagram.com/reels/DAbCdEfGhIj/",
        "  https://www.instagram.com/derenpick.look/reel/DAbC_d-EfG/  ": "https://www.instagram.com/derenpick.look/reel/DAbC_d-EfG/",
        "https://www.instagram.com/p/DAbCdEfGhIj/#x": "https://www.instagram.com/p/DAbCdEfGhIj/",
    }
    for raw, want in good.items():
        assert reel_url(raw) == want, raw
    for bad in (None, 3, "", "instagram.com/reel/DAbCdEfGhIj/", "http://www.instagram.com/reel/DAbCdEfGhIj/",
                "https://evil.example/reel/DAbCdEfGhIj/", "https://www.instagram.com.evil.example/reel/DAbCdEfGhIj/",
                "https://www.instagram.com/derenpick.look/", "https://www.instagram.com/stories/derenpick/123456/",
                "https://www.instagram.com/reel/abc/", "https://www.instagram.com/reel/DAbC dEfGhIj/",
                "https://user@www.instagram.com/reel/DAbCdEfGhIj/", "https://www.instagram.com:444/reel/DAbCdEfGhIj/",
                "https://www.instagram.com:x/reel/DAbCdEfGhIj/", "https://www.instagram.com/reel/" + "a" * 300 + "/",
                "https://www.instagram.com/reel/DAbCdEfGhIj/\x00", "https://www.instagram.com/reel/DAbCdEfGhIj/extra/"):
        with pytest.raises(ActionError):
            reel_url(bad)


async def test_posted_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(export_path(tmp_path))
    blogauto_status(settings)
    want = fixture("action_lookbook_posted.json")
    status, body = await act(bot, "blog.lookbook_posted", {"id": JID, "url": want["args"]["url"] + "?igsh=abc", "music": True},
                             session_headers(bot))
    assert status == 400 and "AI 정보" in body["text"]  # 필수 체크 하나라도 없으면 저장 안 됨
    status, body = await act(bot, "blog.lookbook_posted", {"id": JID, "url": want["args"]["url"] + "?igsh=abc", "music": True,
                                                           "ai_label": True}, session_headers(bot))
    assert status == 202, body  # 확인 창 없음 (서버에서 밖으로 나가는 것 없음 — 올리기는 주인 손)
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed", "args")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed", "args")}
    status, body = await act(bot, "blog.lookbook_posted", {"id": JID, "url": "https://www.instagram.com/derenpick.look/"})
    assert status == 400 and "릴스 주소" in body["text"]
    # 예전 Blog-Auto (lookbook_* 를 모름) → 맡기지 않음
    blogauto_status(settings, actions=old_actions())
    status, body = await act(bot, "blog.lookbook_drop", {"id": JID})
    assert status == 503 and body["code"] == "unsupported"


# ---------------------------------------------------------------- 기록 파일 lookbook 칸
def test_export_parsing_reads_the_contract_fixture() -> None:
    fx = fixture("export_v2.json")
    assert list(fx)[-2:] == ["post_counts", "lookbook"]  # Blog-Auto 가 post_counts 뒤에 붙임
    got = _lookbook(fx["lookbook"])
    assert got["enabled"] is True and got["per_day"] == 1 and got["server"] == "ok" and got["instagram"] == "derenpick.look"
    assert got["style"] == "mirror" and [x["trial"] for x in got["items"]] == [True, True, False, False]  # 사진 시험 둘이 먼저 (새 것부터)
    a, b = [x for x in got["items"] if not x["trial"]]
    assert (a["id"], a["status"], b["id"], b["status"]) == (JID, "ready", "lb-20261025-0648", "posted")
    assert (a["trial_of"], a["style"], b["style"]) == (None, "mirror", "studio")
    assert a["video"] == f"lookbook/{JID}/video.mp4" and a["view_video"] == f"{JID}.mp4" and a["view_cover"] == f"{JID}-c.jpg"
    assert a["has_video"] and a["has_cover"] and a["caption"].startswith("#광고") and "AI 가상인물" in a["caption"].split("\n")[0]
    assert [lk["n"] for lk in a["looks"]] == [1, 2, 3, 4, 5] and a["looks"][0] == {
        "n": 1, "name": "브라운 타이 블라우스", "link": "https://s.zigzag.kr/AbC12dE", "price": 39800, "mall": "zigzag", "score": 9,
        "dropped": False, "thumb": f"{JID}-1.jpg", "product_thumb": None}
    assert b["posted_url"] == "https://www.instagram.com/reel/DAbCdEfGhIj/" and b["ably"] == "no_link"
    assert b["looks"][1]["dropped"] is True and b["looks"][1]["thumb"] is None


def test_export_parsing_tolerates_missing_and_bad_rows() -> None:
    assert _lookbook(None) is None and _lookbook({}) is None and _lookbook({"items": "x"}) is None and _lookbook([]) is None
    fx = copy.deepcopy(fixture("export_v2.json")["lookbook"])
    good = next(x for x in fx["items"] if x["id"] == JID)  # 보통 릴스 줄 (사진 시험 줄은 아래 따로)
    evil = dict(copy.deepcopy(good), id="lb-20261024-0101", status="failed", reason="server: Traceback (most recent call last) …",
                warn="weird", caption="첫 줄\x00\x07\n둘째 줄" + "가" * 3000, video="../../etc/passwd", cover="lookbook/lb-20261026-0712/cover.jpg",
                view_video="lb-20261026-0712.mp4", view_cover="other-c.jpg", has_video=True, has_cover=True,
                posted_url="https://evil.example/reel/x/", ably="hacked",
                looks=[{"n": 1, "name": "A", "link": "https://evil.example/x", "price": -5, "mall": "coupang", "score": 11,
                        "thumb": "lb-20261026-0712-1.jpg"},
                       {"n": 1, "name": "중복", "link": "https://s.zigzag.kr/dup"}, "junk", {"n": 0}, {"n": 99},
                       {"n": 2, "name": "에이블리", "link": "https://applink.a-bly.com/abc", "price": 1000, "mall": "ably", "score": 7,
                        "thumb": "lb-20261024-0101-2.jpg"},
                       {"n": 3, "name": "가짜 호스트", "link": "https://zigzag.kr.evil.example/x"},
                       {"n": 4, "name": "평문", "link": "http://s.zigzag.kr/abc"}])
    fx["items"] = [good, {"id": "lb-1", "status": "ready"}, {"id": JID, "status": "ready"}, {"id": "lb-20261023-0101", "status": "weird"},
                   "junk", None, evil]
    fx["instagram"] = "@derenpick.look"
    got = _lookbook(fx)
    assert [x["id"] for x in got["items"]] == [JID, "lb-20261024-0101"]  # 같은 번호는 처음 것만
    assert got["instagram"] == "derenpick.look"
    bad = got["items"][1]
    assert bad["reason"] == "server" and bad["warn"] is None and bad["ably"] is None and bad["posted_url"] is None
    assert "\x00" not in bad["caption"] and "\x07" not in bad["caption"] and len(bad["caption"]) == 2200 and "\n" in bad["caption"]
    assert bad["video"] is None and bad["view_video"] is None and bad["cover"] is None and bad["view_cover"] is None  # 다른 번호의 파일은 안 됨
    assert bad["has_video"] is False and bad["has_cover"] is False
    lk = {x["n"]: x for x in bad["looks"]}
    assert sorted(lk) == [1, 2, 3, 4]
    assert lk[1]["link"] == "" and lk[1]["price"] == 0 and lk[1]["mall"] == "" and lk[1]["score"] is None and lk[1]["thumb"] is None
    assert lk[2] == {"n": 2, "name": "에이블리", "link": "https://applink.a-bly.com/abc", "price": 1000, "mall": "ably", "score": 7,
                     "dropped": False, "thumb": "lb-20261024-0101-2.jpg", "product_thumb": None}
    assert lk[3]["link"] == "" and lk[4]["link"] == ""
    assert _lookbook({"items": [], "instagram": "a b", "server": "weird", "per_day": "x", "style": "weird"}) == {
        "enabled": False, "per_day": 0, "server": "", "instagram": None, "style": None, "items": []}


# ---------------------------------------------------------------- 화면 값 (state.lookbook)
async def test_state_has_cards_media_looks_checklist_and_actions(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(export_path(tmp_path))
    blogauto_status(settings)
    lb = (await state(bot))["lookbook"]
    assert lb["ready"] and lb["bridge_ok"] and lb["enabled"] and lb["server"] == "" and lb["instagram"] == "@derenpick.look"
    assert lb["entry"] == "룩북 릴스 · 완성 1편 →" and lb["ready_n"] == 1
    assert [(c["k"], c["need"]) for c in lb["checklist"]] == [("music", True), ("ai_label", True), ("", False), ("pin", False)]
    assert "음악" in lb["checklist"][0]["t"] and "AI 정보" in lb["checklist"][1]["t"] and "선택" in lb["checklist"][3]["t"]
    assert "AI 정보" in lb["rules"] and "#광고" in lb["rules"]
    [it] = lb["items"]
    assert (it["id"], it["status"], it["word"], it["tone"]) == (JID, "ready", "완성", "ok")
    assert it["hook"] == "결혼식 뭐 입지?\n고민이라면" and it["theme"] == "하객룩" and it["created"] == "10/26 07:12"
    assert it["video"] == f"/dashboard/lookbook/{JID}/video.mp4" and it["cover"] == f"/dashboard/lookbook/{JID}/cover.jpg"
    assert it["save_name"] == f"{JID}.mp4" and it["caption"].startswith("#광고")
    assert it["looks"][0] == {"n": 1, "name": "브라운 타이 블라우스", "price": "39,800원", "link": "https://s.zigzag.kr/AbC12dE",
                              "mall": "지그재그", "thumb": f"/dashboard/thumb/{JID}-1.jpg", "dropped": False}
    pa = it["posted_act"]
    assert (pa["action"], pa["args"], pa["input"], pa["confirm"]) == ("blog.lookbook_posted", {"id": JID}, "url", False)
    assert it["drop"]["action"] == "blog.lookbook_drop" and it["redo"]["action"] == "blog.lookbook_redo"
    assert not it["drop"]["confirm"] and "disabled" not in it["drop"]
    [done] = lb["posted"]
    assert done["id"] == "lb-20261025-0648" and done["word"] == "올렸어요" and done["posted_url"].endswith("/reel/DAbCdEfGhIj/")
    assert done["ably"] == "에이블리 링크가 없어 챌린지엔 안 내요" and done["created"] == "10/25 06:48"
    assert set(done) == {"id", "theme", "status", "word", "created", "posted_url", "ably"}  # 올린 편은 한 줄만 (화면 첫 값을 작게)
    # 예전 Blog-Auto: 다리 일을 모르면 단추는 꺼둠 · 기록 파일에 lookbook 칸이 없으면 일감 없음
    blogauto_status(settings, actions=old_actions())
    lb = (await state(bot))["lookbook"]
    assert lb["bridge_ok"] is False and lb["items"][0]["posted_act"]["disabled"] is True and lb["items"][0]["drop"]["disabled"] is True
    write_export(export_path(tmp_path), lookbook=None)
    lb = (await state(bot))["lookbook"]
    assert lb["ready"] is False and lb["items"] == [] and lb["posted"] == [] and "업데이트" in lb["empty"] and lb["entry"] == "룩북 릴스 →"
    data = json.loads(export_path(tmp_path).read_text(encoding="utf-8"))
    del data["lookbook"]
    export_path(tmp_path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert (await state(bot))["lookbook"]["items"] == []


def _job(status: str, **kw: object) -> dict:
    base = {"id": "lb-20261026-0712", "theme": "하객룩", "hook": "훅", "status": status, "reason": "", "warn": None, "caption": "#광고 캡션",
            "looks": [], "has_video": False, "has_cover": False, "video": None, "cover": None, "view_video": None, "view_cover": None,
            "created_at": "2026-10-26T07:12:40", "posted_url": None, "ably": None, "trial": False, "trial_of": None, "style": "mirror"}
    return {**base, **kw}


def test_status_words_notes_and_buttons_per_status() -> None:
    words = {"new": "서버 준비 중", "waiting_server": "서버 준비 중", "submitted": "사진 만드는 중", "rendering": "사진 만드는 중",
             "ready": "완성", "failed_match": "상품과 달라 다시", "failed": "실패", "expired": "실패"}
    lv = {"enabled": True, "per_day": 1, "server": "no_token", "instagram": None,
          "items": [_job(s, id=f"lb-2026102{i}-0000") for i, s in enumerate(words)] + [_job("dropped", id="lb-20261019-0000")]}
    out = lbview.build(lv, TZ, 0.0, state_act)
    assert [it["word"] for it in out["items"]] == list(words.values())  # 버린 편은 안 보임
    assert "토큰" in out["server"] and out["instagram"] == ""
    by = {it["status"]: it for it in out["items"]}
    assert by["waiting_server"]["note"] == "쇼츠 서버를 기다리는 중이에요" and by["failed"]["tone"] == "bad" and by["new"]["tone"] == "wait"
    assert "posted_act" in by["ready"] and all("posted_act" not in it for s, it in by.items() if s != "ready")
    assert {s for s, it in by.items() if "redo" in it} == {"ready", "failed", "failed_match"}
    assert {s for s, it in by.items() if "drop" in it} == set(words)
    notes = {r: lbview.build({**lv, "items": [_job("failed", reason=r)]}, TZ, 0.0, state_act)["items"][0]["note"]
             for r in ("no_token", "http_503", "looks_left", "server", "zzz")}
    assert "토큰" in notes["no_token"] and "잠시" in notes["http_503"] and "4벌" in notes["looks_left"] and notes["server"] and notes["zzz"] == ""
    warn = lbview.build({**lv, "items": [_job("ready", warn="video_has_dropped_look")]}, TZ, 0.0, state_act)["items"][0]
    assert "다시 만들기" in warn["warn"] and warn["note"] == ""
    assert "꺼져" in lbview.build({**lv, "enabled": False, "items": []}, TZ, 0.0, state_act)["empty"]
    assert "6~8시" in lbview.build({**lv, "items": []}, TZ, 0.0, state_act)["empty"]


# ---------------------------------------------------------------- 할 일 '룩북 릴스 올리기'
async def test_todo_line_only_while_a_finished_reel_waits(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(export_path(tmp_path))
    blogauto_status(settings)
    todo = {it["iid"]: it for it in (await state(bot))["todo"]}
    row = todo[f"lookbook-{JID}"]
    assert row["title"] == "룩북 릴스 올리기" and row["primary"]["goto"] == "#/blog/lookbook" and "유행 음악" in row["status"]
    assert any(a["action"] == "todo.hide" for a in row["more"]) and row["since"]
    lb = fixture("export_v2.json")["lookbook"]
    next(x for x in lb["items"] if x["id"] == JID)["status"] = "posted"
    write_export(export_path(tmp_path), lookbook=lb)
    assert not any(it["iid"].startswith("lookbook-") for it in (await state(bot))["todo"])  # 완성된 사진 시험은 할 일이 아님
    two = fixture("export_v2.json")["lookbook"]
    next(x for x in two["items"] if x["id"] == "lb-20261025-0648")["status"] = "ready"
    assert lbview.todo_item(_lookbook(two), TZ, state_goto)["title"] == "룩북 릴스 올리기 · 2편"
    assert lbview.todo_item(None, TZ, state_goto) is None


# ---------------------------------------------------------------- 영상·표지 내보내기
def _view_file(settings: Settings, sub: str, name: str, data: bytes) -> Path:
    d = settings.data_dir / "blog_view" / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return d / name


async def test_video_and_cover_are_served_behind_login_with_ranges(bot: DealBot, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    data = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 4
    _view_file(settings, "reels", f"{JID}.mp4", data)
    _view_file(settings, "thumbs", f"{JID}-c.jpg", b"\xff\xd8\xff\xe0cover")
    path = f"/dashboard/lookbook/{JID}/video.mp4"
    assert (await bot.web.dispatch("GET", path, {}))[0] == 401
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", path, h)
    assert res[0] == 200 and res[1] == data and res[2] == "video/mp4"
    assert res[3]["Accept-Ranges"] == "bytes" and res[3]["Cache-Control"].startswith("private") and res[3]["X-Content-Type-Options"] == "nosniff"
    n = len(data)
    for rng, status, body, cr in (("bytes=0-1", 206, data[:2], f"bytes 0-1/{n}"), ("bytes=10-", 206, data[10:], f"bytes 10-{n - 1}/{n}"),
                                  ("bytes=-3", 206, data[-3:], f"bytes {n - 3}-{n - 1}/{n}"),
                                  (f"bytes=5-{n + 100}", 206, data[5:], f"bytes 5-{n - 1}/{n}"),
                                  (f"bytes=-{n + 5}", 206, data, f"bytes 0-{n - 1}/{n}")):
        res = await bot.web.dispatch("GET", path, {**h, "range": rng})
        assert (res[0], res[1], res[3]["Content-Range"]) == (status, body, cr), rng
    for rng in (f"bytes={n}-", "bytes=9-3", "bytes=-0"):
        res = await bot.web.dispatch("GET", path, {**h, "range": rng})
        assert res[0] == 416 and res[3]["Content-Range"] == f"bytes */{n}", rng
    for rng in ("bytes=0-1,5-9", "items=0-1", "bytes=-", "bytes=x-1", "bytes=٠-١"):  # 모르는 모양 → 전체
        res = await bot.web.dispatch("GET", path, {**h, "range": rng})
        assert res[0] == 200 and res[1] == data, rng
    res = await bot.web.dispatch("GET", f"/dashboard/lookbook/{JID}/cover.jpg", h)
    assert res[0] == 200 and res[1] == b"\xff\xd8\xff\xe0cover" and res[2] == "image/jpeg"
    monkeypatch.setattr(dash_api, "RANGE_CHUNK", 100)  # 끝을 안 정한 조각은 조금씩 (브라우저가 이어서 부름)
    res = await bot.web.dispatch("GET", path, {**h, "range": "bytes=10-"})
    assert (res[0], res[1], res[3]["Content-Range"]) == (206, data[10:110], f"bytes 10-109/{n}")
    res = await bot.web.dispatch("GET", path, {**h, "range": "bytes=10-500"})
    assert res[0] == 206 and res[1] == data[10:501]  # 끝을 정한 조각은 그대로


async def test_falls_back_to_blogauto_var_and_refuses_odd_names(bot: DealBot, settings: Settings, tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    var = tmp_path / "var" / "lookbook" / JID
    var.mkdir(parents=True)
    (var / "video.mp4").write_bytes(b"var-video")
    (var / "cover.jpg").write_bytes(b"var-cover")
    (var / "secret.txt").write_bytes(b"no")
    h = session_headers(bot)
    assert (await bot.web.dispatch("GET", f"/dashboard/lookbook/{JID}/video.mp4", h))[1] == b"var-video"
    assert (await bot.web.dispatch("GET", f"/dashboard/lookbook/{JID}/cover.jpg", h))[1] == b"var-cover"
    _view_file(settings, "reels", f"{JID}.mp4", b"view-copy")  # 대시보드용 사본이 있으면 그것 먼저
    assert (await bot.web.dispatch("GET", f"/dashboard/lookbook/{JID}/video.mp4", h))[1] == b"view-copy"
    # 바로가기 (var 안 파일·폴더, blog_view 사본) · 다른 이름 · 밖으로 나가는 이름은 안 됨
    other = "lb-20261020-0101"
    (tmp_path / "var" / "lookbook" / other).mkdir()
    os.symlink(tmp_path / "var" / "lookbook" / JID / "secret.txt", tmp_path / "var" / "lookbook" / other / "video.mp4")
    os.symlink(tmp_path / "var" / "lookbook" / JID, tmp_path / "var" / "lookbook" / "lb-20261021-0101")
    os.symlink(var / "secret.txt", settings.data_dir / "blog_view" / "reels" / "lb-20261022-0101.mp4")
    for bad in (f"{other}/video.mp4", "lb-20261021-0101/video.mp4", "lb-20261022-0101/video.mp4", f"{JID}/secret.txt",
                f"{JID}/../{JID}/video.mp4", f"../lookbook/{JID}/video.mp4", f"{JID}/video.mp4/", f"{JID}/Video.mp4", f"{JID}",
                "lb-2026102-0712/video.mp4", f"{JID}%2Fvideo.mp4", "", "lb-٢٠٢٦١٠٢٦-0712/video.mp4"):
        assert (await bot.web.dispatch("GET", f"/dashboard/lookbook/{bad}", h))[0] == 404, bad
    monkeypatch.setattr(dash_api, "REEL_MAX_BYTES", 4)  # 너무 큰 영상은 내보내지 않음 (메모리)
    assert (await bot.web.dispatch("GET", f"/dashboard/lookbook/{JID}/video.mp4", h))[0] == 404


# ---------------------------------------------------------------- lookbook.js
def test_lookbook_js_lazy_load_budget_and_safety() -> None:
    js = (STATIC / "lookbook.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "eval(" not in js and "new Function" not in js and "innerHTML" not in js
    for word in ("영상 저장", "표지 저장", "캡션 복사", "링크 복사", "올렸어요", "버리기", "다시 만들기", "올리기 전 확인", "playsinline: true",
                 "controls: true", "rel: 'noopener noreferrer'", "navigator.share", "W.SBLB", "L.checklist", "it.posted_act", "{url: u}", "k.need && !c[i]", "x[k.k] = true", "' · 필수'",
                 "lk.link && !lk.dropped",  # 링크 복사는 뺀 룩(상품과 다름)을 빼고
                 # 사진 시험 (10/26): 열 때만 받기 · 상품 사진 | 거울 셀카 | 예전 양식 · 점수 · 단추 둘 · 비용 안내
                 "'/dashboard/api/lbtrial'", "if (!TOPEN", "'사진 시험'", "'상품 사진'", "거울 셀카", "예전 양식", "점수", "'시험 새로 만들기'",
                 "'이 양식으로 정하기'", "'blog.lookbook_trial'", "'blog.lookbook_style'", "args: {style: st}", "사진값 약 1,000원 · 쇼핑몰 접속 없음",
                 "지금 양식", "L.trial_ok"):
        assert word in js, word
    for label in re.findall(r"btn\('[^']*', '([^']*)'", js):
        assert re.search(r"[가-힣]", label), label
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "lazy('meta[name=sb-lookbook]', 'lookbook', 'SBLB')" in app and "'#/blog/lookbook': '#/sns/lookbook'" in app
    assert "lookbook: lbLoad" in app and "['lookbook', '룩북 릴스']" in app and "/^(pics|search|trial|ptrial|sched)$/" in app
    html = shell.page({"me": {}})
    assert re.search(r'<meta name="sb-lookbook" content="/dashboard/static/lookbook\.js\?v=[0-9a-f]{10}">', html)
    assert html.count("<script src=") == 1  # lookbook.js 는 처음 열 때만
    assert "lookbook.js" in dash_api.APP_FILES


def test_lookbook_js_syntax_and_reel_check() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    f = STATIC / "lookbook.js"
    assert subprocess.run([node, "--check", str(f)], capture_output=True).returncode == 0
    # 화면의 주소 검사는 서버(reel_url)와 같은 것을 받는다 (물음표 꼬리 포함 — 서버가 지움)
    src = f.read_text(encoding="utf-8")
    rx = re.search(r"const REEL = (/.+/);", src).group(1)
    urls = ["https://www.instagram.com/reel/DAbCdEfGhIj/?igsh=x", "https://instagram.com/reels/DAbCdEfGhIj",
            "https://www.instagram.com/derenpick.look/", "http://www.instagram.com/reel/DAbCdEfGhIj/", "https://evil.example/reel/DAbCdEfGhIj/"]
    out = subprocess.run([node, "-e", f"const R={rx};console.log(JSON.stringify(process.argv.slice(1).map(u=>R.test(u))))", *urls],
                         capture_output=True, text=True)
    assert json.loads(out.stdout) == [True, True, False, False, False]


# ---------------------------------------------------------------- 사진 시험 (주인 10/26)
TRIAL_M, TRIAL_S = "lb-20261026-0911", "lb-20261026-0910"


def test_trial_actions_match_the_contract_fixtures() -> None:
    for name, args, target in (("lookbook_trial", {}, "lookbook-trial"), ("lookbook_style", {"style": "mirror"}, "lookbook-style")):
        want = fixture(f"action_{name}.json")
        assert (want["action"], want["args"], want["target"], want["confirmed"], want["blog"]) == (name, args, target, False, None)
        assert name in BLOG_ACTIONS and name in fixture("status_blogauto.json")["actions"]
        out = clean_args(f"blog.{name}", {**want["args"], "blog": "issue", "extra": 1})
        assert out == want["args"] and not needs_confirm(f"blog.{name}", out) and target_of(f"blog.{name}", out) == target
    assert clean_args("blog.lookbook_trial", {"base": JID}) == {"base": JID} and clean_args("blog.lookbook_trial", {"base": ""}) == {}
    assert clean_args("blog.lookbook_style", {"style": "studio"}) == {"style": "studio"}
    for bad in ({"base": "lb-1"}, {"base": 3}, {"base": JID + "\n"}, {"base": "../" + JID}, {"base": [JID]}):
        with pytest.raises(ActionError):
            clean_args("blog.lookbook_trial", bad)
    for bad in ({}, {"style": "weird"}, {"style": ["mirror"]}, {"style": "Mirror"}):
        with pytest.raises(ActionError) as e:
            clean_args("blog.lookbook_style", bad)
        assert "양식" in e.value.text


async def test_trial_and_style_reach_the_spool_like_the_contract(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(export_path(tmp_path))
    blogauto_status(settings)
    for name, args in (("lookbook_style", {"style": "mirror"}), ("lookbook_trial", {})):
        want = fixture(f"action_{name}.json")
        status, body = await act(bot, f"blog.{name}", args, session_headers(bot))
        assert status == 202, body  # 확인 창 없음 (사진값 안내는 화면에)
        f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
        assert set(f) == set(want)
        assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed", "args")} == \
            {k: want[k] for k in ("v", "action", "blog", "target", "confirmed", "args")}
    blogauto_status(settings, actions=old_actions())  # 예전 Blog-Auto (사진 시험을 모름) → 맡기지 않음
    status, body = await act(bot, "blog.lookbook_style", {"style": "studio"})
    assert status == 503 and body["code"] == "unsupported"


def test_trial_rows_are_parsed_strictly() -> None:
    fx = copy.deepcopy(fixture("export_v2.json")["lookbook"])
    got = _lookbook(fx)
    m, s = got["items"][:2]
    assert (m["id"], m["trial"], m["trial_of"], m["style"], m["status"]) == (TRIAL_M, True, JID, "mirror", "ready")
    assert (s["id"], s["trial_of"], s["style"]) == (TRIAL_S, JID, "studio") and m["caption"] == "" and m["has_video"] is False
    assert m["looks"][0] == {"n": 1, "name": "브라운 타이 블라우스", "link": "https://s.zigzag.kr/AbC12dE", "price": 39800, "mall": "zigzag",
                             "score": 9, "dropped": False, "thumb": f"{TRIAL_M}-1.jpg", "product_thumb": f"{TRIAL_M}-p1.jpg"}
    assert s["looks"][4]["product_thumb"] is None and s["looks"][1]["score"] == 3  # 낮은 점수도 그대로 (다시 만들기·빼기 없음)
    one = fx["items"][0]
    evil = [dict(copy.deepcopy(one), id="lb-20261020-0101", trial_of=None),  # 바탕 번호 없음 → 버림 (보통 릴스로 보이면 안 됨)
            dict(copy.deepcopy(one), id="lb-20261020-0102", trial_of="lb-20261020-0102"),  # 자기 자신
            dict(copy.deepcopy(one), id="lb-20261020-0103", trial_of="../" + JID),
            dict(copy.deepcopy(one), id="lb-20261020-0104", trial="yes", trial_of=JID, style="weird",  # 참·거짓만 → 보통 줄
                 looks=[{"n": 1, "name": "A", "product_thumb": "lb-20261026-0911-p1.jpg"},  # 다른 번호의 사본
                        {"n": 2, "name": "B", "product_thumb": "lb-20261020-0104-p3.jpg"},  # 다른 룩 번호
                        {"n": 3, "name": "C", "product_thumb": "../lb-20261020-0104-p3.jpg"},
                        {"n": 4, "name": "D", "product_thumb": "lb-20261020-0104-p4.jpg"}])]
    got = _lookbook({**fx, "items": evil, "style": ["mirror"]})
    assert [x["id"] for x in got["items"]] == ["lb-20261020-0104"] and got["style"] is None
    plain = got["items"][0]
    assert plain["trial"] is False and plain["trial_of"] is None and plain["style"] is None
    assert [lk["product_thumb"] for lk in plain["looks"]] == [None, None, None, "lb-20261020-0104-p4.jpg"]


async def test_trial_sets_stay_out_of_the_reel_list_and_first_state(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(export_path(tmp_path))
    blogauto_status(settings)
    body = await state(bot)
    lb = body["lookbook"]
    assert [it["id"] for it in lb["items"]] == [JID] and [it["id"] for it in lb["posted"]] == ["lb-20261025-0648"]
    assert (lb["trial_n"], lb["trial_ready"], lb["style"], lb["style_word"], lb["trial_ok"]) == (2, 2, "mirror", "거울 셀카", True)
    assert lb["entry"] == "룩북 릴스 · 완성 1편 →"  # 완성된 사진 시험은 '올릴 릴스'로 세지 않음
    text = json.dumps(body, ensure_ascii=False)
    assert TRIAL_M not in text and TRIAL_S not in text  # 시험 사진 목록은 첫 상태 값에 없음 (104 KB 예산 — 열 때만 받음)
    assert [it["iid"] for it in body["todo"] if it["iid"].startswith("lookbook-")] == [f"lookbook-{JID}"]
    blogauto_status(settings, actions=old_actions())
    assert (await state(bot))["lookbook"]["trial_ok"] is False  # 예전 Blog-Auto 면 단추를 꺼 둠
    write_export(export_path(tmp_path), lookbook=None)
    lb = (await state(bot))["lookbook"]
    assert (lb["trial_n"], lb["style"], lb["trial_ok"]) == (0, "", False)


async def test_trial_endpoint_groups_sets_by_base_behind_login(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    from dash_helpers import get_json

    write_export(export_path(tmp_path))
    blogauto_status(settings)
    status, _p, _ = await get_json(bot, "/dashboard/api/lbtrial", {})
    assert status in (401, 302, 303)  # 로그인 아래
    status, data, headers = await get_json(bot, "/dashboard/api/lbtrial", session_headers(bot))
    assert status == 200 and headers.get("Cache-Control") == "no-store"
    [g] = data
    assert (g["base_id"], g["theme"], g["created_at"], g["created"]) == (JID, "하객룩", "2026-10-26T09:12:05", "10/26 09:12")
    assert [(st["id"], st["style"], st["status"], st["word"]) for st in g["sets"]] == [(TRIAL_M, "mirror", "ready", "완성"),
                                                                                       (TRIAL_S, "studio", "ready", "완성")]
    assert g["sets"][0]["looks"][0] == {"n": 1, "name": "브라운 타이 블라우스", "score": 9, "thumb": f"/dashboard/thumb/{TRIAL_M}-1.jpg",
                                        "product_thumb": f"/dashboard/thumb/{TRIAL_M}-p1.jpg"}
    assert g["sets"][1]["looks"][4]["product_thumb"] == "" and "link" not in g["sets"][0]["looks"][0]
    lb = fixture("export_v2.json")["lookbook"]
    lb["items"] = [x for x in lb["items"] if not x["trial"]]
    write_export(export_path(tmp_path), lookbook=lb)
    status, data, _ = await get_json(bot, "/dashboard/api/lbtrial", session_headers(bot))
    assert status == 200 and data == []
    write_export(export_path(tmp_path), lookbook=None)
    assert (await get_json(bot, "/dashboard/api/lbtrial", session_headers(bot)))[1] == []


def test_trials_put_mirror_first_and_tolerate_one_set() -> None:
    lv = {"enabled": True, "per_day": 1, "server": "ok", "instagram": None, "style": "studio",
          "items": [_job("rendering", id="lb-20261027-0101", trial=True, trial_of=JID, style="studio",
                         looks=[{"n": 1, "name": "A", "score": None, "thumb": None, "product_thumb": None, "link": "", "price": 0, "mall": "",
                                 "dropped": False}]),
                    _job("ready", id="lb-20261027-0100", trial=True, trial_of=JID, style="mirror", looks=[]),
                    _job("ready", id="lb-20261026-0900", trial=True, trial_of="lb-20261025-0648", style="studio", looks=[]),
                    _job("ready")]}
    out = lbview.trials(lv, TZ)
    assert [g["base_id"] for g in out] == [JID, "lb-20261025-0648"]
    assert [st["style"] for st in out[0]["sets"]] == ["mirror", "studio"] and out[0]["sets"][1]["word"] == "사진 만드는 중"
    assert out[0]["sets"][1]["looks"] == [{"n": 1, "name": "A", "score": None, "thumb": "", "product_thumb": ""}]
    view = lbview.build(lv, TZ, 0.0, state_act)
    assert [it["id"] for it in view["items"]] == [JID] and view["trial_n"] == 3 and view["trial_ready"] == 2
    assert view["style_word"] == "예전 양식" and lbview.todo_item(lv, TZ, state_goto)["iid"] == f"lookbook-{JID}"
    assert lbview.trials(None, TZ) == []


def test_second_trial_on_the_same_base_shows_its_own_photos_newest_first() -> None:
    """검토 10/26: 바탕 릴스가 같으면 예전엔 한 묶음으로 합쳐 가장 옛 세트만 보였다 (새로 낸 사진값 1,000원어치가 안 보임).
    한 번 누를 때 만든 두 세트는 created_at 이 같으니 누른 번마다 따로 · 새 것부터 · 번호가 섞여도(앞 시험 실패 뒤 1000/0957) 제 세트끼리."""
    def t(jid: str, style: str, at: str, status: str = "ready") -> dict:
        return _job(status, id=jid, trial=True, trial_of=JID, style=style, created_at=at, looks=[])
    lv = {"enabled": True, "per_day": 1, "server": "ok", "instagram": None, "style": "mirror",
          "items": [t("lb-20261026-1059", "mirror", "2026-10-26T11:00:30"), t("lb-20261026-1058", "studio", "2026-10-26T11:00:30"),
                    t("lb-20261026-1000", "mirror", "2026-10-26T10:01:30", "rendering"),
                    t("lb-20261026-0959", "mirror", "2026-10-26T10:00:30", "failed"), t("lb-20261026-0958", "studio", "2026-10-26T10:00:30"),
                    t("lb-20261026-0957", "studio", "2026-10-26T10:01:30", "rendering"), _job("ready")]}
    out = lbview.trials(lv, TZ)
    assert [g["created_at"] for g in out] == ["2026-10-26T11:00:30", "2026-10-26T10:01:30", "2026-10-26T10:00:30"]
    assert [[st["id"] for st in g["sets"]] for g in out] == [["lb-20261026-1059", "lb-20261026-1058"],
                                                             ["lb-20261026-1000", "lb-20261026-0957"],
                                                             ["lb-20261026-0959", "lb-20261026-0958"]]
    view = lbview.build(lv, TZ, 0.0, state_act)
    assert view["trial_busy"] == 2 and view["trial_base"] is False  # 만드는 중 2세트 · _job("ready") 는 룩이 없어 바탕이 못 됨
    sig = view["trial_sig"]
    assert re.fullmatch(r"[0-9a-f]{10}", sig) and "lb-" not in sig
    lv["items"][2]["status"] = "ready"  # 세트 하나 상태만 바뀌어도 화면이 목록을 다시 받게
    assert lbview.build(lv, TZ, 0.0, state_act)["trial_sig"] != sig
    lv["items"][-1]["looks"] = [{"n": k, "name": f"옷 {k}", "price": 0, "link": "", "mall": "", "score": None, "thumb": None,
                                 "product_thumb": None, "dropped": False} for k in range(1, 5)]
    assert lbview.build(lv, TZ, 0.0, state_act)["trial_base"] is True
    assert lbview.build({**lv, "items": []}, TZ, 0.0, state_act)["trial_sig"] == ""


def test_trial_buttons_wait_for_the_real_result_and_the_list_refreshes() -> None:
    """검토 10/26: 시험·양식 단추는 접수(202)만 보고 '됐어요' 하지 않고 app.js exec 가 결과까지 · 만드는 중엔 시험 단추를 끔 ·
    목록은 세트 표시가 바뀌거나 칸을 다시 열 때 새로 받음 · 화면에만 기억한 양식(STY)은 없앰 (서버 값만)."""
    js = (STATIC / "lookbook.js").read_text(encoding="utf-8")
    assert "H.exec(a, {}, {btn: b})" in js and "run({action: 'blog.lookbook_trial'" in js and "run({action: 'blog.lookbook_style'" in js
    assert "send({action: 'blog.lookbook_" not in js and "STY" not in js
    assert "L.trial_busy ||" in js and "!L.trial_base" in js and "L.trial_sig ||" in js and "if (TOPEN) TR = null" in js
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "T.view(b, {el, btn, api, toast, S, exec, uploadAll})" in app  # exec 를 넘겨줌
