"""링크에서 사진 고르기 (주인 10/18 — 손이 아파요: 한 번에, Blog-Auto pickphotos 계약 scratchpad pics/CONTRACT.md):
blog.pics_fetch · blog.pics_clear · blog.photos 의 pics · 후보 사진 원본 /dashboard/pic/<이름> (로그인·이름 규칙) ·
기록 파일 posts[].pics·pics_jobs → 화면 ('이대로 넣기' 가 큰 버튼 · 사진 찾는 중 · 다시 찾기 · 링크에서 사진 뽑기).
주소 글자만 본다 — 여기서 아무 곳에도 접속하지 않는다 (인스타그램은 더더욱)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import (
    TEXTS,
    ActionError,
    clean_args,
    confirm_text,
    fetchable,
    needs_confirm,
)
from dealbot.dashboard.blogauto import parse_v2
from test_dash_api_actions import act, confirmed
from test_dash_state import state, write_export

ACTS = fixture("status_blogauto.json")["actions"]
OLD = [a for a in ACTS if not a.startswith("pics_")]  # 예전 Blog-Auto (사진 고르기를 모름)
URL = "https://www.musinsa.com/products/4123456"


def item(n: int, slot: int | None = None, **kw: object) -> dict:
    return {"id": f"pk-{n:08x}", "thumb": f"pick-{n:04d}.jpg", "full": f"pick-{n:04d}-full.jpg", "why": f"사진 {n}", "slot": slot,
            "host": "www.juunj.com", **kw}


def pics(state_: str = "ready", items: list | None = None, text: str = "") -> dict:
    return {"state": state_, "text": text, "at": "2026-10-18T20:00:00+09:00", "items": items or []}


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


# ---------------------------------------------------------------- 받는 값 (args)
@pytest.mark.parametrize("url", ["https://www.instagram.com/p/DPq7x2aE1bc/", "https://m.facebook.com/x/posts/1", "https://fb.me/abc",
                                 "https://www.threads.net/@x/post/1", "https://threads.com/@x", "https://blog.naver.com/someone/2230000001",
                                 "https://m.blog.naver.com/someone/2230000001", "https://cafe.naver.com/x/1", "https://x.blog.me/1",
                                 "https://scontent.cdninstagram.com/a.jpg", "HTTPS://WWW.INSTAGRAM.COM./p/x"])
def test_no_fetch_hosts_are_refused_with_a_friendly_text(url: str) -> None:
    assert not fetchable(url)
    with pytest.raises(ActionError) as e:
        clean_args("blog.pics_fetch", {"url": url})
    assert e.value.code == "bad_args" and e.value.text == TEXTS["no_fetch"] and "인스타" in e.value.text


@pytest.mark.parametrize("url", ["ftp://x.com/a", "javascript:alert(1)", "www.musinsa.com/x", "https://", "https://a b.com/x",
                                 "https://x.com/" + "a" * 500, 123, None, ""])
def test_bad_urls_are_refused(url: object) -> None:
    with pytest.raises(ActionError):
        clean_args("blog.pics_fetch", {"url": url})


def test_pics_fetch_args_for_a_post_and_for_a_pasted_link() -> None:
    assert clean_args("blog.pics_fetch", {"blog": "issue", "pid": "46"}) == {"blog": "issue", "pid": 46}  # 주소·원하는 사진은 Blog-Auto 가 정함
    assert clean_args("blog.pics_fetch", {"blog": "issue", "pid": 46, "url": URL, "want": "  신발   클로즈업 "}) == {
        "blog": "issue", "pid": 46, "url": URL, "want": "신발 클로즈업"}
    assert clean_args("blog.pics_fetch", {"url": URL, "want": "", "x": 1}) == {"url": URL}
    assert clean_args("blog.pics_fetch", {"url": "http://news.example.com/a?b=1"})["url"] == "http://news.example.com/a?b=1"
    assert fetchable("https://blog.naver.com.example.org/x") and fetchable("https://shopping.naver.com/x")
    for bad in ({}, {"pid": 46}, {"blog": "nope", "pid": 46}, {"url": URL, "want": "가" * 201}, {"url": URL, "want": 3}):
        with pytest.raises(ActionError):
            clean_args("blog.pics_fetch", bad)
    assert not needs_confirm("blog.pics_fetch", {"url": URL}) and not needs_confirm("blog.pics_clear", {"job": "pj-0000aa01"})


def test_pics_clear_args() -> None:
    assert clean_args("blog.pics_clear", {"blog": "issue", "pid": 46}) == {"blog": "issue", "pid": 46}
    assert clean_args("blog.pics_clear", {"job": "pj-0000aa01"}) == {"job": "pj-0000aa01"}
    for bad in ({}, {"job": "pj-xyz"}, {"job": "../pj-0000aa01"}, {"job": 1}, {"blog": "issue"}):
        with pytest.raises(ActionError):
            clean_args("blog.pics_clear", bad)


def test_photos_take_chosen_candidates_alone_or_with_uploads_and_links() -> None:
    a = {"blog": "issue", "pid": 46}
    opts = {"uploads": [], "mode": "add", "slot": 0, "in_order": False, "crop": False, "credit": ""}  # 올린 사진과 같은 길
    got = clean_args("blog.photos", {**a, "pics": [{"id": "pk-0000000a", "slot": 2}, {"id": "pk-0000000b"}, {"id": "pk-0000000a", "slot": 4}]})
    assert got == {**a, **opts, "pics": [{"id": "pk-0000000a", "slot": 2}, {"id": "pk-0000000b", "slot": 0}]}  # 같은 사진은 한 번 (처음 것)
    up = "9f0e1d2c3b4a59687766554433221100"
    both = clean_args("blog.photos", {**a, "uploads": [up], "pics": [{"id": "pk-0000000a", "slot": 1}]})
    assert both["uploads"] == [up] and both["pics"] == [{"id": "pk-0000000a", "slot": 1}] and both["mode"] == "add"
    link = {"slot": 3, "url": "https://www.instagram.com/p/DPq7x2aE1bc/"}
    assert clean_args("blog.photos", {**a, "links": [link], "pics": [{"id": "pk-0000000a", "slot": 1}]}) == {
        **a, **opts, "links": [link], "pics": [{"id": "pk-0000000a", "slot": 1}]}
    assert clean_args("blog.photos", {**a, "links": [link]}) == {**a, "links": [link]}  # 링크만은 전과 같다
    assert clean_args("blog.photos", {"blog": "hotdeal", "pid": 8, "pics": [{"id": "pk-0000000a", "slot": 0}]})["pics"]  # 모든 블로그
    for bad in ([{"id": "pk-0000000g", "slot": 1}], [{"id": "pk-000000a", "slot": 1}], [{"id": "pk-0000000a", "slot": 41}],
                [{"id": "pk-0000000a", "slot": -1}], [{"id": "pk-0000000a", "slot": True}], ["pk-0000000a"], {"id": "pk-0000000a"},
                [{"id": f"pk-{i:08x}", "slot": 0} for i in range(25)], []):
        with pytest.raises(ActionError):
            clean_args("blog.photos", {**a, "pics": bad})
    assert len(clean_args("blog.photos", {**a, "pics": [{"id": f"pk-{i:08x}", "slot": 0} for i in range(24)]})["pics"]) == 24
    assert "사진을" in confirm_text("blog.photos", {**a, "pics": [{"id": "pk-0000000a", "slot": 1}]}, {})["title"]


# ---------------------------------------------------------------- 누르기 → Blog-Auto 스풀
async def test_pics_actions_reach_the_spool_without_confirm(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings, actions=ACTS)
    h = session_headers(bot)
    root = spool.root(settings.data_dir, spool.DASH_ACTIONS)
    status, body = await act(bot, "blog.pics_fetch", {"url": URL, "want": "제품 정면"}, h)
    assert status == 202, body
    f = spool.read(root / "new" / f"{body['id']}.json")
    assert f["action"] == "pics_fetch" and f["args"] == {"url": URL, "want": "제품 정면"} and f["confirmed"] is False and f["target"] == ""
    status, body = await act(bot, "blog.pics_fetch", {"blog": "issue", "pid": 46}, h)
    assert status == 202 and spool.read(root / "new" / f"{body['id']}.json")["target"] == "post-issue-46"
    status, body = await act(bot, "blog.pics_clear", {"job": "pj-0000aa01"}, h)
    assert status == 202
    status, body = await act(bot, "blog.pics_fetch", {"url": "https://www.instagram.com/p/DPq7x2aE1bc/"}, h)
    assert status == 400 and body["code"] == "bad_args" and body["text"] == TEXTS["no_fetch"]
    # 고른 후보 넣기: 임시저장 블로그(Pick)는 확인 없이, 자동 발행 블로그는 확인 창(넣고 발행)
    status, body = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "pics": [{"id": "pk-0000000a", "slot": 2}]}, h)
    assert status == 409 and body["ok"] == "넣고 발행"  # 기록 파일이 없으면 모드를 모름 → 확인 한 번 (더 안전)
    status, body = await confirmed(bot, "blog.photos", {"blog": "issue", "pid": 46, "pics": [{"id": "pk-0000000a", "slot": 2}]}, h)
    assert status == 202
    f = spool.read(root / "new" / f"{body['id']}.json")
    assert f["action"] == "photos" and f["args"]["pics"] == [{"id": "pk-0000000a", "slot": 2}] and f["args"]["uploads"] == []


async def test_spool_files_match_the_contract_fixtures(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")  # Pick = 임시저장 · 간격 두고만 → 확인 없음 (계약 confirmed false)
    blogauto_status(settings, actions=ACTS)
    h = session_headers(bot)
    root = spool.root(settings.data_dir, spool.DASH_ACTIONS)
    for name, action in (("action_pics_fetch.json", "blog.pics_fetch"), ("action_photos_pics.json", "blog.photos")):
        want = fixture(name)
        status, body = await act(bot, action, {"blog": want["args"]["blog"], "pid": want["args"]["pid"],
                                               **({"pics": want["args"]["pics"]} if "pics" in want["args"] else {})}, h)
        assert status == 202, body
        f = spool.read(root / "new" / f"{body['id']}.json")
        assert set(f) == set(want)
        assert {k: f[k] for k in ("v", "action", "blog", "args", "target", "confirmed")} == {
            k: want[k] for k in ("v", "action", "blog", "args", "target", "confirmed")}, name


async def test_export_fixture_candidates_reach_the_screen(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=ACTS)
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-48")
    assert it["primary"]["label"] == "이대로 넣기 · 2장" and it["primary"]["args"]["pics"] == [
        {"id": "pk-3fa9c2d1", "slot": 1}, {"id": "pk-8b1f2f0a", "slot": 2}]
    full = [x["full"] for x in it["detail"]["pics"]["items"]]
    assert full[2] == "/dashboard/pic/pk-c0ffee12.png" and len(full) == 4
    assert body["pics"]["jobs"][0]["host"] == "news1.kr" and len(body["pics"]["jobs"][0]["items"]) == 2


async def test_old_blogauto_answers_unsupported(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings, actions=OLD)
    status, body = await act(bot, "blog.pics_fetch", {"url": URL})
    assert status == 503 and body["code"] == "unsupported"


# ---------------------------------------------------------------- 후보 사진 원본 (로그인 아래)
def _pic(settings: Settings, name: str = "pick-0001-full.jpg", data: bytes = b"\xff\xd8\xff\xe0jpeg") -> Path:
    d = settings.data_dir / "blog_view" / "pics"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return d / name


async def test_full_size_pic_is_served_behind_login_with_private_cache(bot: DealBot, settings: Settings) -> None:
    _pic(settings)
    _pic(settings, "pick-0002.png", b"\x89PNG....")
    assert (await bot.web.dispatch("GET", "/dashboard/pic/pick-0001-full.jpg", {}))[0] == 401
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/pic/pick-0001-full.jpg", h)
    assert res[0] == 200 and res[1] == b"\xff\xd8\xff\xe0jpeg" and res[2] == "image/jpeg"
    assert res[3]["Cache-Control"].startswith("private") and res[3]["X-Content-Type-Options"] == "nosniff"
    res = await bot.web.dispatch("GET", "/dashboard/pic/pick-0002.png", h)
    assert res[0] == 200 and res[2] == "image/png"


async def test_pic_names_are_strict_and_traversal_and_links_fail(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    _pic(settings)
    (settings.data_dir / "secret.jpg").write_bytes(b"x")
    os.symlink(settings.data_dir / "secret.jpg", settings.data_dir / "blog_view" / "pics" / "link.jpg")
    (settings.data_dir / "blog_view" / "pics" / "dir.jpg").mkdir()
    h = session_headers(bot)
    for bad in ("..%2Fsecret.jpg", "../secret.jpg", "%2e%2e/secret.jpg", "link.jpg", "dir.jpg", "Pick-0001-full.jpg", "pick-0001-full.gif",
                "pick-0001-full.jpg/", "pick 1.jpg", ".jpg", "a" * 81 + ".jpg", "pick-0001-full.jpg%00.png", "nope.jpg", ""):
        assert (await bot.web.dispatch("GET", "/dashboard/pic/" + bad, h))[0] == 404, bad
    big = _pic(settings, "big.jpg", b"0")
    with open(big, "wb") as f:  # 8MB 넘는 파일은 주지 않는다
        f.truncate(8_000_001)
    assert (await bot.web.dispatch("GET", "/dashboard/pic/big.jpg", h))[0] == 404


# ---------------------------------------------------------------- 기록 파일 → 화면
def test_export_pics_are_filtered() -> None:
    good = item(1, 2)
    rows = [good, item(1, 3), dict(item(2), id="pk-zz"), dict(item(3), thumb="../x.jpg"), dict(item(4), full="a/b.jpg"),
            dict(item(5), full="x.gif"), dict(item(6), slot=41), dict(item(7), slot=True), dict(item(8), why="가" * 50)]
    post = {"pid": 46, "title": "글", "pics": pics(items=rows + [item(100 + i) for i in range(30)])}
    got = parse_v2({"posts": {"issue": [post, {"pid": 47, "title": "글", "pics": {"state": "done"}}]}})["posts"]["issue"]
    p = got[0]["pics"]
    assert p["state"] == "ready" and len(p["items"]) <= 24 and p["items"][0] == good
    ids = [x["id"] for x in p["items"]]
    assert ids.count(good["id"]) == 1 and "pk-zz" not in ids and f"pk-{3:08x}" not in ids and f"pk-{4:08x}" not in ids
    assert next(x for x in p["items"] if x["id"] == f"pk-{6:08x}")["slot"] is None
    assert next(x for x in p["items"] if x["id"] == f"pk-{7:08x}")["slot"] is None
    assert len(next(x for x in p["items"] if x["id"] == f"pk-{8:08x}")["why"]) <= 20
    assert got[1]["pics"] is None
    jobs = parse_v2({"pics_jobs": [{"id": f"pj-{i:08x}", "url": URL, "want": "정면", **pics()} for i in range(8)]
                     + [{"id": "bad", **pics()}]})["pics_jobs"]
    assert len(jobs) == 5 and jobs[0]["url"] == URL
    assert parse_v2({"pics_jobs": [{"id": "pj-00000001", "url": "javascript:x", **pics()}]})["pics_jobs"][0]["url"] == ""
    assert parse_v2({})["pics_jobs"] is None


def _posts(p46: dict | None, p48: dict | None = None) -> dict:
    posts = fixture("export_v2.json")["posts"]
    for p in posts["issue"]:
        if p["pid"] == 46 and p46 is not None:
            p["pics"] = p46
        if p["pid"] == 48 and p48 is not None:
            p["pics"] = p48
    return posts


async def test_ready_candidates_make_put_the_primary_in_slot_order(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    rows = [item(1), item(2, 4), item(3, 0), item(4, 2), item(5)]
    write_export(tmp_path / "blogauto.json", posts=_posts(pics(items=rows), pics("working")),
                 pics_jobs=[{"id": "pj-0000aa01", "url": URL, "want": "정면", **pics(items=[item(9)])}])
    blogauto_status(settings, actions=ACTS)
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-46")
    a = it["primary"]
    assert a["action"] == "blog.photos" and a["label"].startswith("이대로 넣기") and a["input"] == "pics" and a["label"].endswith("3장")
    assert a["args"] == {"blog": "issue", "pid": 46, "pics": [{"id": "pk-00000004", "slot": 2}, {"id": "pk-00000002", "slot": 4},
                                                              {"id": "pk-00000003", "slot": 0}]}  # 자리 순서 · 빈 자리(0)는 끝
    assert it["more"][0]["action"] == "blog.photos" and it["more"][0]["input"] == "photos"  # 직접 올리기는 더보기 맨 앞
    assert "추천 사진 5장" in it["status"]
    dp = it["detail"]["pics"]
    assert dp["items"][0]["thumb"] == "/dashboard/thumb/pick-0001.jpg" and dp["items"][0]["full"] == "/dashboard/pic/pick-0001-full.jpg"
    assert dp["fetch"]["action"] == "blog.pics_fetch" and dp["fetch"]["label"] == "다시 찾기" and dp["fetch"]["args"] == {"blog": "issue", "pid": 46}
    assert dp["clear"]["action"] == "blog.pics_clear" and dp["put"]["args"] == a["args"]
    working = next(i for i in body["todo"] if i["iid"] == "post-issue-48")
    assert working["primary"]["input"] == "photos" and working["detail"]["pics"]["state"] == "working" and working["detail"]["pics"]["put"] is None
    assert working["status"] == "예약 발행 멈춤 · 사진이 아직 없어요"  # 멈춘 예약은 그 이유 그대로
    tool = body["pics"]
    assert tool["fetch"]["action"] == "blog.pics_fetch" and tool["fetch"]["input"] == "url"
    j = tool["jobs"][0]
    assert j["host"] == "musinsa.com" and j["want"] == "정면" and j["items"][0]["full"] == "/dashboard/pic/pick-0009-full.jpg"
    assert j["clear"]["args"] == {"job": "pj-0000aa01"}


async def test_failed_none_and_no_suggestion_states(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", posts=_posts(pics(items=[item(1), item(2)]), pics("failed", text="사진이 없었어요")))
    blogauto_status(settings, actions=ACTS)
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-46")
    assert it["primary"]["input"] == "photos"  # AI 가 고른 게 없으면 큰 버튼은 그대로 (시트에서 골라 넣기)
    assert it["detail"]["pics"]["put"]["args"]["pics"] == []
    failed = next(i for i in body["todo"] if i["iid"] == "post-issue-48")["detail"]["pics"]
    assert failed["state"] == "failed" and failed["text"] == "사진이 없었어요" and failed["fetch"]["label"] == "다시 찾기"
    write_export(tmp_path / "blogauto.json", posts=_posts(None))
    body = await state(bot)
    none = next(i for i in body["todo"] if i["iid"] == "post-issue-46")["detail"]["pics"]
    assert none["state"] == "none" and none["fetch"]["label"] == "링크에서 사진 찾기" and none["put"] is None
    assert body["pics"]["jobs"][0]["id"] == "pj-5e6f7a8b"  # 계약 픽스처의 링크 사진 뽑기
    write_export(tmp_path / "blogauto.json", posts=_posts(pics("working")), pics_jobs=[])
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-46")
    assert it["status"].endswith("사진 찾는 중…") and it["primary"]["input"] == "photos" and body["pics"]["jobs"] == []


async def test_old_blogauto_hides_fetch_and_the_tool(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", posts=_posts(None))
    blogauto_status(settings, actions=OLD)
    body = await state(bot)
    it = next(i for i in body["todo"] if i["iid"] == "post-issue-46")
    assert it["detail"]["pics"]["fetch"] is None and body["pics"] is None


# ---------------------------------------------------------------- 화면 글자 · 서비스 워커
def test_app_js_has_the_picker_strings_and_long_press() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for word in ("추천 사진", "이대로 넣기", "사진 찾는 중…", "꾹 눌러 저장", "링크에서 사진 뽑기", "누르면 넣기·빼기", "#/blog/pics",
                 "원하는 사진 (선택)", "사진 뽑기"):
        assert word in js, word
    assert "contextmenu" in js and "pointerdown" in js  # 꾹 누르기 = 크게 (저장·복사)
    assert "instagram.com/p/'" not in js and "fetch('https://" not in js
    css = (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert re.search(r"\.pt\{[^}]*-webkit-touch-callout:none", css) and re.search(r"\.pk\+\.pb\{[^}]*position:sticky", css)
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert int(re.search(r"sb-v(\d+)", sw).group(1)) >= 9  # type: ignore[union-attr]
    assert "/dashboard/pic/" not in sw  # 후보 사진은 저장해 두지 않는다 (로그아웃 뒤에도 남지 않게)


def test_contract_notes() -> None:
    """계약의 이름 규칙: 후보 id pk-<8hex> · 일 id pj-<8hex> · 썸네일은 blog_view/thumbs 규칙 (jpg·webp)."""
    v = parse_v2({"posts": {"issue": [{"pid": 1, "title": "t", "pics": pics(items=[item(1, 1, thumb="pick-1.png")])}]}})
    assert v["posts"]["issue"][0]["pics"]["items"] == []
