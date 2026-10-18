"""Pick 사진 칸의 '넣으면 좋을 인스타 게시물' (주인 10/18 — Blog-Auto ig_suggest, posts[].ig_suggest).
기사에서 찾은 게시물·계정·소식 줄 · '인스타 열기 ↗' (주인 브라우저로) · '넣기' = 인스타 링크 칸과 같은 blog.photos links.
주소 글자만 본다 — 인스타그램에 접속하지 않는다. 네트워크 없이."""

from __future__ import annotations

from pathlib import Path

from dash_helpers import blogauto_status, fixture
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import clean_args
from dealbot.dashboard.blogauto import IG_SUGGEST_MAX, parse_v2
from test_dash_state import bot, state, write_export  # noqa: F401 — bot: 픽스처

ART = "https://www.news1.kr/entertain/broadcast-tv/5912345"
POST = "https://www.instagram.com/p/DPq3xYzAbCd/"


def _parse(rows: list) -> list[dict]:
    return parse_v2({"posts": {"issue": [{"pid": 48, "title": "글", "ig_suggest": rows}]}})["posts"]["issue"][0]["ig_suggest"]


def test_fixture_rows_are_read() -> None:
    p48 = next(p for p in parse_v2(fixture("export_v2.json"))["posts"]["issue"] if p["pid"] == 48)
    ig = p48["ig_suggest"]
    assert [r["url"] for r in ig] == [POST, "https://www.instagram.com/reel/DPr7KlmNoPq/", None]
    assert ig[0]["handle"] == "hyoseop.ahn" and ig[0]["when"] == "18일" and ig[0]["source"] == ART and ig[0]["host"] == "news1.kr"
    assert ig[1]["inserted"] is True and ig[2]["handle"] == "juunj_official" and ig[2]["when"] == ""
    assert next(p for p in parse_v2(fixture("export_v2.json"))["posts"]["issue"] if p["pid"] == 46)["ig_suggest"] == []


def test_only_instagram_post_urls_and_safe_text() -> None:
    bad_urls = ["https://instagram.com/p/DPq3xYzAbCd/", "http://www.instagram.com/p/DPq3xYzAbCd/", "https://www.instagram.com/hyoseop/",
                "https://www.instagram.com/p/DPq3xYzAbCd/?utm=1", "https://www.instagram.com.evil.io/p/DPq3xYzAbCd/",
                "javascript:alert(1)//www.instagram.com/p/DPq3xYzAbCd/", "https://www.instagram.com/stories/a/1/", 5]
    got = _parse([{"url": u, "what": "소식", "source": ART} for u in bad_urls])
    assert got and all(r["url"] is None for r in got)  # 기사 링크는 남음 · 게시물 주소로는 안 씀 ('넣기' 없음)
    got = _parse([{"url": POST, "what": "가" * 300, "when": "1" * 50, "handle": "bad handle!", "source": "http://x.kr/a"},
                  {"url": POST, "what": "같은 게시물", "source": ART},  # 같은 게시물은 한 번
                  {"what": "", "source": ART}, {"what": "열 곳 없음", "source": "javascript:1"}, "x",
                  {"what": "계정만", "handle": "juunj_official"}])
    assert len(got) == 2 and len(got[0]["what"]) <= 80 and len(got[0]["when"]) <= 20
    assert got[0]["handle"] is None and got[0]["source"] is None and got[1]["handle"] == "juunj_official"
    many = _parse([{"url": f"https://www.instagram.com/p/DPq3xYzAb{i:02d}/", "what": f"소식 {i}", "source": ART} for i in range(10)])
    assert len(many) == IG_SUGGEST_MAX
    assert _parse("nope") == [] and _parse(None) == []


async def test_photo_panel_rows_open_and_one_tap_insert(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-48")
    rows = it["detail"]["ig"]
    assert [r["open"] for r in rows] == [POST, "https://www.instagram.com/reel/DPr7KlmNoPq/", "https://www.instagram.com/juunj_official/"]
    assert rows[0]["text"].endswith(" · 18일") and rows[0]["src"] == ART
    put = rows[0]["put"]
    assert put["action"] == "blog.photos" and put["label"] == "넣기" and put["args"] == {"blog": "issue", "pid": 48, "links": [{"slot": 0, "url": POST}]}
    assert clean_args("blog.photos", put["args"]) == put["args"]  # 인스타 링크 칸과 같은 길 그대로 통과
    assert rows[1]["put"] is None and rows[1]["text"].endswith("넣음")  # 이미 넣은 게시물
    assert rows[2]["put"] is None  # 계정만 — 열어서 찾기만
    other = next(i for i in body["todo"] if i["iid"] == "post-issue-46")
    assert other["detail"]["ig"] == []


def test_app_js_shows_the_rows_and_never_loads_instagram() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "넣으면 좋을 인스타 게시물" in js and "'인스타 열기'" in js and "exec(g.put, {}" in js
    assert "fetch('https://www.instagram" not in js and "src: 'https://www.instagram" not in js and "src: g.open" not in js
