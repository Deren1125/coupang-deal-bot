"""Pick 사진 올리기의 인스타 링크 칸 (주인 10/18 story — Blog-Auto bridge.x_photos 의 links) · 핫딜 오픈채팅 문구 복사 (recent[].share).
주소 글자만 본다 — 인스타그램에 접속하지 않는다. 네트워크 없이."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args, confirm_text
from dealbot.dashboard.blogauto import SHARE_MAX, parse_v2
from test_dash_api_actions import act, bot, confirmed, photo_upload  # noqa: F401 — bot: 픽스처
from test_dash_state import state, write_export

POST = "https://www.instagram.com/p/DPq7x2aE1bc/"
REEL = "https://www.instagram.com/reel/DPq9z3bF2cd/"


def photos(**kw: object) -> dict:
    return clean_args("blog.photos", {"blog": "issue", "pid": 46, **kw})


@pytest.mark.parametrize("url", [POST, REEL, "https://instagram.com/p/DPq7x2aE1bc", "http://m.instagram.com/reels/DPq9z3bF2cd/?igsh=x",
                                 "https://www.instagram.com/harin.official/p/DPq7x2aE1bc/", "https://instagr.am/p/DPq7x2aE1bc/"])
def test_post_and_reel_links_are_accepted(url: str) -> None:
    assert photos(links=[{"slot": 3, "url": url}]) == {"blog": "issue", "pid": 46, "links": [{"slot": 3, "url": url}]}


@pytest.mark.parametrize("url", ["https://www.instagram.com/harin.official/", "https://www.instagram.com/stories/harin/3712345678/",
                                 "https://www.instagram.com/explore/tags/공항패션/", "https://www.instagram.com/reels/audio/1234567890/",
                                 "https://example.com/p/DPq7x2aE1bc/", "https://instagram.com.evil.io/p/DPq7x2aE1bc/",
                                 "javascript:alert(1)//instagram.com/p/DPq7x2aE1bc/", "https://www.instagram.com/p/ab/", "",
                                 "https://www.instagram.com/p/" + "a" * 300])
def test_profile_story_and_other_links_are_rejected(url: str) -> None:
    with pytest.raises(ActionError):
        photos(links=[{"slot": 3, "url": url}])


def test_slot_range_count_and_shape() -> None:
    assert photos(links=[{"url": POST}])["links"] == [{"slot": 0, "url": POST}]  # 자리 없음 = 빈 자리에 (Blog-Auto 와 같게)
    assert photos(links=[{"slot": 40, "url": POST}])["links"][0]["slot"] == 40
    for bad in ([{"slot": 41, "url": POST}], [{"slot": -1, "url": POST}], [{"slot": True, "url": POST}], [{"slot": "x", "url": POST}],
                [POST], {"slot": 1, "url": POST}, [{"slot": 1, "url": POST}] * 13):
        with pytest.raises(ActionError):
            photos(links=bad)
    with pytest.raises(ActionError):  # Pick 글에만
        clean_args("blog.photos", {"blog": "finance", "pid": 1, "links": [{"slot": 1, "url": POST}]})
    with pytest.raises(ActionError):  # 사진도 링크도 없으면 전처럼 거절
        photos()


def test_photos_only_args_stay_as_before_and_both_add_links() -> None:
    up = "9f0e1d2c3b4a59687766554433221100"
    only = photos(uploads=[up], slot=3)
    assert only == {"blog": "issue", "pid": 46, "uploads": [up], "mode": "slot", "slot": 3, "in_order": False, "crop": False, "credit": ""}
    both = photos(uploads=[up], links=[{"slot": 2, "url": POST}])
    assert both["mode"] == "add" and both["links"] == [{"slot": 2, "url": POST}]
    assert "인스타 게시물" in confirm_text("blog.photos", {"blog": "issue", "pid": 46, "links": [{"slot": 2, "url": POST}]}, {})["title"]
    assert "사진을" in confirm_text("blog.photos", both, {})["title"]


async def test_links_reach_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings)
    want = fixture("action_photos_links.json")
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    status, body = await confirmed(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": [uid], "links": want["args"]["links"]}, h)
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want) and f["action"] == want["action"] and f["blog"] == want["blog"] and f["target"] == want["target"]
    got = dict(f["args"])
    assert Path(got.pop("uploads")[0]).name == f"{uid}.jpg"
    assert got == {k: v for k, v in want["args"].items() if k != "uploads"}
    # 링크만 (사진 없이)
    status, body = await confirmed(bot, "blog.photos", {"blog": "issue", "pid": 47, "links": want["args"]["links"][:1]}, h)
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert f["args"] == {"blog": "issue", "pid": 47, "links": want["args"]["links"][:1]}
    status, body = await act(bot, "blog.photos", {"blog": "issue", "pid": 47, "links": [{"slot": 1, "url": "https://www.instagram.com/harin/"}]}, h)
    assert status == 400 and body["code"] == "bad_args"


def test_app_js_has_the_link_control_for_pick_only_and_never_loads_instagram() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "인스타 링크 넣기" in js and "(a.args || {}).blog !== 'issue'" in js and "extra.links = links" in js
    assert "fetch('https://www.instagram" not in js and "src: 'https://www.instagram" not in js
    assert int(re.search(r"sb-v(\d+)", (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")).group(1)) >= 8  # type: ignore[union-attr]


# ---------------------------------------------------------------- 핫딜 오픈채팅 문구 (recent[].share)
def test_share_text_is_read_for_hotdeal_recent_only() -> None:
    row = {"pid": 9, "title": "에어팟 핫딜", "url": "https://m.blog.naver.com/derenhot/224000000009", "at": "2026-10-18T20:00:00",
           "share": "🔥 에어팟 4 최저가\n\n\n\n129,000원  (쿠폰)\r\nhttps://m.blog.naver.com/derenhot/224000000009\n" + "가" * 2000}
    out = parse_v2({"recent": {"hotdeal": [row], "issue": [dict(row, share="x")]}})
    sh = out["recent"]["hotdeal"][0]["share"]
    assert sh.startswith("🔥 에어팟 4 최저가\n\n129,000원 (쿠폰)\nhttps://") and len(sh) <= SHARE_MAX
    assert "share" not in out["recent"]["issue"][0]
    assert "share" not in parse_v2({"recent": {"hotdeal": [dict(row, share=None)]}})["recent"]["hotdeal"][0]
    assert "share" not in parse_v2({"recent": {"hotdeal": [dict(row, share="  \n ")]}})["recent"]["hotdeal"][0]


@pytest.fixture
def ebot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_share_text_reaches_the_blog_tab(ebot: DealBot, settings: Settings, tmp_path: Path) -> None:
    recent = fixture("export_v2.json")["recent"]
    recent["hotdeal"] = [{"pid": 9, "title": "에어팟 핫딜", "url": "https://m.blog.naver.com/derenhot/224000000009",
                          "at": "2026-10-18T20:00:00", "share": "🔥 에어팟\n129,000원"}]
    write_export(tmp_path / "blogauto.json", recent=recent)
    blogauto_status(settings)
    body = await state(ebot)
    assert body["blogs"]["hotdeal"]["recent"][0]["share"] == "🔥 에어팟\n129,000원"
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "오픈채팅 문구 복사" in js and "복사했어요 — 오픈채팅에 붙여넣기 하세요" in js
