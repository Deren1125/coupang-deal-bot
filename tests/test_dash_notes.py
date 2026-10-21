"""대시보드 주인 10/20 (2) — 네트워크 없이.

1) 내 후기 쓰기: 핫딜 글(임시저장·예약)에 '내 후기 쓰기' (메모 + 사진 선택) → Blog-Auto 다리 일 post_notes {blog, pid, notes, photos}
   (계약 tests/fixtures/dash_contract/action_post_notes.json — Blog-Auto 와 같은 파일) · 블로그 탭 '후기 쓸 제품 고르기' {product, notes}
2) 인기 검색어를 누르면 확인 창 없이 바로 쓰기 · 쓰는 동안 그 줄은 못 누름
3) 사진을 누르면 올릴 원본 보기: slots[].final (선택 칸 — 없으면 full) · 체크무늬 · 픽셀 크기 · 편집/교체/비우기 · sw sb-v23"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args
from dealbot.dashboard.blogauto import parse_v2
from test_dash_api_actions import act, photo_upload
from test_dash_state import state, write_export

U1 = "2c3d4e5f60718293a4b5c6d7e8f90a1b"
NOTES = "원룸에서 2주 썼어요. 생각보다 가볍고 먼지통 비우기가 편했어요. 소리는 조금 커요."


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def hotdeal_posts() -> dict:
    """계약 export 에 핫딜 글 둘 (임시저장 31 · 예약 32)."""
    base = fixture("export_v2.json")["posts"]["issue"][1]
    saved = dict(copy.deepcopy(base), pid=31, title="샤오미 무선청소기 G10 소개", schedule=None, pics=None)
    sched = dict(copy.deepcopy(base), pid=32, title="무선청소기 고르는 법", pics=None)
    return dict(fixture("export_v2.json")["posts"], hotdeal=[saved, sched])


def test_clean_args_match_the_contract_fixture() -> None:
    want = fixture("action_post_notes.json")
    assert want["action"] == "post_notes" and want["target"] == "post-hotdeal-31" and want["confirmed"] is False
    a = want["args"]
    out = clean_args("blog.post_notes", {"blog": "hotdeal", "pid": a["pid"], "text": f"  {a['notes']} ", "uploads": [U1]})
    assert out == {"blog": "hotdeal", "pid": 31, "notes": a["notes"], "photos": [U1]} and list(out) == list(a)
    assert clean_args("blog.post_notes", {"pid": 31, "notes": "메모"}) == {"blog": "hotdeal", "pid": 31, "notes": "메모", "photos": []}
    pick = clean_args("blog.post_notes", {"blog": "hotdeal", "product": " 샤오미   무선청소기 ", "text": "메모"})
    assert pick == {"blog": "hotdeal", "product": "샤오미 무선청소기", "notes": "메모", "photos": []}
    for bad in ({"blog": "issue", "pid": 31, "notes": "x"}, {"pid": 31, "notes": ""}, {"pid": 31, "notes": "가" * 2001},
                {"notes": "x"}, {"product": "가" * 61, "notes": "x"}, {"pid": 31, "notes": "x", "photos": ["../x"]},
                {"pid": 31, "notes": "x", "photos": [f"{i:032x}" for i in range(11)]}, {"pid": True, "notes": "x"}):
        with pytest.raises(ActionError):
            clean_args("blog.post_notes", bad)


async def test_notes_reach_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    want = fixture("action_post_notes.json")
    h = session_headers(bot)
    up = photo_upload(bot, h)
    status, body = await act(bot, "blog.post_notes", {"blog": "hotdeal", "pid": 31, "text": want["args"]["notes"], "uploads": [up]}, h)
    assert status == 202, body  # 확인 창 없음 (임시저장만 다시 — 예약·발행은 그대로)
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    got = dict(f["args"])
    assert [Path(p).name for p in got.pop("photos")] == [f"{up}.jpg"]
    assert got == {k: v for k, v in want["args"].items() if k != "photos"} and list(f["args"]) == list(want["args"])
    used = {r["used_by"] for r in bot.db._q("SELECT used_by FROM dash_uploads WHERE id = ?", (up,))}
    assert used == {body["id"]}
    status, body = await act(bot, "blog.post_notes", {"pid": 31, "text": NOTES, "uploads": [U1]}, h)
    assert status == 400 and body["code"] == "bad_upload"


async def test_review_buttons_on_hotdeal_posts_and_blog_tab(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", posts=hotdeal_posts())
    blogauto_status(settings)
    body = await state(bot)
    hd = body["blogs"]["hotdeal"]
    rows = {x["iid"]: x for x in hd["waiting"] + ((hd.get("schedule") or {}).get("items") or [])}
    for pid in (31, 32):  # 임시저장 · 예약 모두
        notes = [a for a in rows[f"post-hotdeal-{pid}"]["more"] if a["action"] == "blog.post_notes"]
        assert len(notes) == 1 and notes[0]["label"] == "내 후기 쓰기" and notes[0]["args"] == {"blog": "hotdeal", "pid": pid}
        assert notes[0]["input"] == "text" and notes[0]["photos_optional"] is True and not notes[0]["confirm"]
    for blog in ("issue", "finance"):  # 핫딜 글에만
        assert not any(a["action"] == "blog.post_notes" for x in body["blogs"][blog]["waiting"] for a in x["more"])
    pick = next(a for a in hd["actions"] if a["action"] == "blog.post_notes")
    assert pick["label"] == "후기 쓸 제품 고르기" and pick["product"] is True and pick["args"] == {"blog": "hotdeal"}
    # 예전 Blog-Auto (post_notes 를 모름) — 단추 없음
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "post_notes"])
    body = await state(bot)
    hd = body["blogs"]["hotdeal"]
    assert not any(a["action"] == "blog.post_notes" for a in hd["actions"])
    assert not any(a["action"] == "blog.post_notes" for x in hd["waiting"] for a in x["more"])


def test_slot_final_is_optional_and_checked() -> None:
    data = fixture("export_v2.json")
    s1 = parse_v2(data)["posts"]["issue"][0]["slots"][0]
    assert "final" not in s1 or s1["final"].endswith(".jpg")  # 다른 작업이 계약에 더하기 전에도 그대로 된다
    data["posts"]["issue"][0]["slots"][0]["final"] = "post-issue-46-1-ab12cd34-u.jpg"
    assert parse_v2(data)["posts"]["issue"][0]["slots"][0]["final"] == "post-issue-46-1-ab12cd34-u.jpg"
    for bad in ("../../etc/passwd", 7, "x.png"):
        data["posts"]["issue"][0]["slots"][0]["final"] = bad
        assert "final" not in parse_v2(data)["posts"]["issue"][0]["slots"][0]


async def test_slot_final_reaches_the_screen(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    posts = fixture("export_v2.json")["posts"]
    posts["issue"][0]["slots"][0]["final"] = "post-issue-46-1-ab12cd34-u.jpg"
    write_export(tmp_path / "blogauto.json", posts=posts)
    blogauto_status(settings)
    body = await state(bot)
    it = next(x for x in body["todo"] + body["blogs"]["issue"]["waiting"] if x["iid"] == "post-issue-46")
    assert it["detail"]["slots"][0]["final"] == "/dashboard/thumb/post-issue-46-1-ab12cd34-u.jpg"


def test_app_js_viewer_review_and_trend_tap() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    css = (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    # 사진을 누르면 원본 보기 먼저 (final → full → thumb) · 픽셀 크기 · 편집/교체/비우기
    for word in ("s.thumb ? slotView(it, s) : slotSheet(it, s)", "s.final || s.full || s.thumb", "naturalWidth", "'편집'", "'교체'",
                 "'비우기'", "class: 'ck'"):
        assert word in js, word
    assert ".ck{background:repeating-conic-gradient(" in css
    # 인기 검색어: 확인 창 없이 · 토스트 · 쓰는 동안 못 누름
    start = js.index("async function trendGo")
    go = js[start: js.index("\n}", start)]
    assert "confirmSheet" not in go and "글 쓰기 시작했어요" in go and "{keyword: x.keyword}" in go
    assert "p.args.keyword === x.keyword" in js
    # 내 후기 쓰기: 글 칸 + 사진(답하기 사진과 같은 uploadAll) · 제품 고르기는 제품 이름 칸
    assert "a.product" in js and "extra.product" in js and "'제품 이름을 적어 주세요'" in js
    assert "blog\\.(write|post_notes)" in js
    assert "const V = 'sb-v25'" in sw  # 10/21 사진 테스트 (sb-v25)
