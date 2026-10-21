"""대시보드 주인 10/21 — 발행 글 고치기 ("발행된 글을 봇이 고치는 기능도 넣자"). 네트워크 없이.

블로그 탭 최근 글(발행된 글)마다 '고치기' (Blog-Auto 가 post_edit 를 켠 블로그 — 기록 파일 recent[].photos 칸이 있을 때만) →
edit.js 시트: 글의 사진마다 빼기·교체 + '이 문장 빼기' 글 칸 → Blog-Auto 다리 일 post_edit {blog, pid, ops}
(계약 tests/fixtures/dash_contract/action_post_edit.json — Blog-Auto 와 같은 파일). 공개된 글을 바로 바꾸니 ★ 확인 · sw sb-v27."""

from __future__ import annotations

import copy
import gzip
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import (
    BLOG_ACTIONS,
    CONFIRM,
    ActionError,
    clean_args,
    confirm_text,
    target_of,
)
from dealbot.dashboard.blogauto import parse_v2
from test_dash_api_actions import act, photo_upload
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

U1 = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
PHOTOS = [{"n": 1, "label": "사진 1 · 자리 1", "thumb": "post-issue-40-1-1a2b3c4d-r.jpg"},
          {"n": 2, "label": "사진 2 · 자리 2", "thumb": None}, {"n": 3, "label": "사진 3", "thumb": "../evil.jpg"}]


def recent_with_photos(photos=PHOTOS) -> dict:
    rec = copy.deepcopy(fixture("export_v2.json")["recent"])
    rec["issue"][0]["photos"] = photos
    return rec


def test_clean_args_match_the_contract_fixture() -> None:
    want = fixture("action_post_edit.json")
    assert want["action"] == "post_edit" and want["target"] == "post-issue-57" and want["confirmed"] is True
    assert "post_edit" in BLOG_ACTIONS and "post_edit" in fixture("status_blogauto.json")["actions"]
    assert "blog.post_edit" in CONFIRM
    ops = copy.deepcopy(want["args"]["ops"])
    ops[1]["file"] = U1  # 화면은 올린 사진 id 로 보낸다 (경로로 바꾸는 건 서버)
    out = clean_args("blog.post_edit", {"blog": "issue", "pid": 57, "ops": [dict(o, junk=1) for o in ops]})
    assert out == {"blog": "issue", "pid": 57, "ops": ops} and list(out) == list(want["args"])
    assert target_of("blog.post_edit", out) == "post-issue-57"
    assert clean_args("blog.post_edit", {"blog": "finance", "pid": 3, "ops": [{"op": "rebuild"}]})["ops"] == [{"op": "rebuild"}]
    assert clean_args("blog.post_edit", {"blog": "hotdeal", "pid": 3, "ops": [{"op": "remove_text", "text": " 두  칸 "}]})["ops"] == \
        [{"op": "remove_text", "text": "두 칸"}]
    c = confirm_text("blog.post_edit", out, {})
    assert "발행 글" in c["title"] and c["ok"] == "고치기" and "그대로" in c["body"]


@pytest.mark.parametrize("ops", [
    [], None, [{"op": "wipe"}], [{"op": "remove_photo"}], [{"op": "remove_photo", "index": 0}], [{"op": "remove_photo", "index": 41}],
    [{"op": "replace_photo", "index": 1}], [{"op": "replace_photo", "index": 1, "file": "../x.jpg"}],
    [{"op": "remove_text", "text": ""}], [{"op": "remove_text", "text": "가" * 501}], [{"op": "replace_text", "text": "a"}],
    [{"op": "remove_text", "text": "줄\n바꿈"}], [{"op": "rebuild"}] * 21,
])
def test_bad_ops_are_refused(ops: object) -> None:
    with pytest.raises(ActionError):
        clean_args("blog.post_edit", {"blog": "issue", "pid": 57, "ops": ops})


def test_bad_blog_is_refused() -> None:
    with pytest.raises(ActionError):
        clean_args("blog.post_edit", {"blog": "nope", "pid": 57, "ops": [{"op": "rebuild"}]})


def test_recent_photos_are_parsed_and_optional() -> None:
    v2 = parse_v2({"recent": recent_with_photos()})
    row = v2["recent"]["issue"][0]
    assert row["photos"] == [{"n": 1, "label": "사진 1 · 자리 1", "thumb": "post-issue-40-1-1a2b3c4d-r.jpg"},
                             {"n": 2, "label": "사진 2 · 자리 2", "thumb": None}, {"n": 3, "label": "사진 3", "thumb": None}]
    assert "photos" not in parse_v2({"recent": fixture("export_v2.json")["recent"]})["recent"]["issue"][0]  # 칸 없음 = 고치기 없음
    assert parse_v2({"recent": recent_with_photos([])})["recent"]["issue"][0]["photos"] == []  # 사진 없는 글도 문장은 고침


async def test_fix_button_only_when_blogauto_offers_it(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json", recent=recent_with_photos())
    blogauto_status(settings)
    body = await state(bot)
    r = body["blogs"]["issue"]["recent"][0]
    assert r["edit"]["act"]["action"] == "blog.post_edit" and r["edit"]["act"]["args"] == {"blog": "issue", "pid": 40}
    assert r["edit"]["act"]["confirm"] is True and r["edit"]["act"]["label"] == "고치기"
    assert [p["thumb"] for p in r["edit"]["photos"]] == ["/dashboard/thumb/post-issue-40-1-1a2b3c4d-r.jpg", None, None]
    assert "photos" not in r
    assert "edit" not in body["blogs"]["hotdeal"]["recent"][0]  # 칸이 없는 글 (예전 Blog-Auto · 꺼진 블로그)
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "post_edit"])
    body = await state(bot)
    assert "edit" not in body["blogs"]["issue"]["recent"][0]


async def test_post_edit_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json", recent=recent_with_photos())
    blogauto_status(settings)
    want = fixture("action_post_edit.json")
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    ops = copy.deepcopy(want["args"]["ops"])
    ops[1]["file"] = uid
    args = {"blog": "issue", "pid": 57, "ops": ops}
    status, body = await act(bot, "blog.post_edit", args, h)
    assert status == 409 and body["status"] == "confirm" and "발행 글" in body["title"]  # ★ 확인
    status, body = await act(bot, "blog.post_edit", args, h, ctoken=body["ctoken"])
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    got = f["args"]
    assert Path(got["ops"][1]["file"]).name == f"{uid}.jpg"  # 올린 사진 id → 받은 파일 경로 (Blog-Auto 는 id 를 모른다)
    got["ops"][1]["file"] = want["args"]["ops"][1]["file"]
    assert got == want["args"] and list(got) == list(want["args"])
    used = {r["used_by"] for r in bot.db._q("SELECT used_by FROM dash_uploads WHERE id = ?", (uid,))}
    assert used == {body["id"]}


async def test_unknown_upload_and_old_blogauto_are_refused(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    args = {"blog": "issue", "pid": 57, "ops": [{"op": "replace_photo", "index": 1, "file": U1}]}
    status, body = await act(bot, "blog.post_edit", args, h)
    status, body = await act(bot, "blog.post_edit", args, h, ctoken=body.get("ctoken"))
    assert status == 400 and body["code"] == "bad_upload"
    assert not list((spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new").glob("*.json"))
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "post_edit"])
    status, body = await act(bot, "blog.post_edit", {"blog": "issue", "pid": 57, "ops": [{"op": "rebuild"}]}, h)
    assert status == 503 and body["code"] == "unsupported"


def test_sheet_lives_in_edit_js_and_app_js_only_opens_it() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    ed = (webapp.STATIC_DIR / "edit.js").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    # app.js: 최근 글 줄에 '고치기' 하나 → edit.js 를 처음 누를 때만 받아 fix 시트
    assert "r.edit ? btn('sm', '고치기', () => edFix(key, r))" in js and "E.fix(h, r)" in js
    assert "post_edit" not in js and "remove_photo" not in js  # 시트·ops 는 edit.js 에만
    for word in ("function fix(h, r)", "'발행 글 고치기'", "'이 문장 빼기'", "'빼기'", "'교체'", "'되돌리기'", "'고치기'", "'취소'",
                 "op: 'replace_photo', index: x.p.n", "op: 'remove_photo', index: x.p.n", "op: 'remove_text'", "h.uploadAll(",
                 "h.exec(r.edit.act, {ops}", "카테고리·태그는 그대로", "W.SBEdit = {batch, editor, photo, fix}"):
        assert word in ed, word
    assert "const V = 'sb-v27'" in sw
    assert len(gzip.compress(ed.encode(), 9)) <= 6 * 1024 + 256
