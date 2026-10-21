"""대시보드 세 기능 (주인 10/20) — 네트워크 없이.

1) Pick '인기 검색어' 카드: 계약 export_v2.json trends → 블로그 탭 Pick 화면 (순위·출처·▲▼·이미 쓴 글·패션/연예) ·
   누르면 /issue 키워드와 같은 blog.issue {keyword} (Blog-Auto 다리 일 issue)
2) 블로그 이름 옆 할 일 수 (app.js — '할 일' 줄을 그 블로그만, 0 이면 숨김)
3) 사진을 누르면 편집기: slots[].full 큰 사본 · photos_batch put {edited, focus, retouch} · sw sb-v20"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args
from dealbot.dashboard.blogauto import _trends, parse_v2
from test_dash_api_actions import act
from test_dash_state import state, write_export

U1 = "9f0e1d2c3b4a59687766554433221100"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def test_trends_parse_keeps_the_contract_and_drops_bad_rows() -> None:
    fx = fixture("export_v2.json")["trends"]
    got = parse_v2(fixture("export_v2.json"))["trends"]
    assert [x["keyword"] for x in got["items"]] == [x["keyword"] for x in fx["items"]]
    assert got["items"][0] == {k: fx["items"][0][k] for k in ("keyword", "rank", "sources", "change", "new", "written", "tag")}
    bad = _trends({"at": "x", "items": [
        {"keyword": "a" * 80, "rank": 2, "sources": ["google", "instagram"], "change": True, "tag": "politics"},
        {"keyword": "", "rank": 1}, "junk", {"keyword": "<b>1</b>", "rank": 1, "change": -3, "written": "yes"},
        {"keyword": "<b>1</b>", "rank": 3}]})
    assert [x["rank"] for x in bad["items"]] == [1, 2] and len(bad["items"][1]["keyword"]) <= 40
    assert bad["items"][1]["sources"] == ["google"] and bad["items"][1]["change"] is None and bad["items"][1]["tag"] is None
    assert bad["items"][0]["change"] == -3 and bad["items"][0]["written"] is False
    assert _trends(None) is None and parse_v2({})["trends"] is None


async def test_pick_view_has_the_board_and_tap_writes_with_that_keyword(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    tr = body["blogs"]["issue"]["trends"]
    assert tr["at"] and len(tr["items"]) == 4 and tr["items"][0]["written"] is True
    assert tr["write"]["action"] == "blog.issue" and tr["write"]["label"] == "이 키워드로 Pick 쓰기"
    assert "trends" not in body["blogs"]["finance"] and "trends" not in body["blogs"]["hotdeal"]
    status, res = await act(bot, tr["write"]["action"], dict(tr["write"]["args"], keyword=tr["items"][1]["keyword"]))
    assert status == 202, res
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{res['id']}.json")
    assert f["action"] == "issue" and f["args"] == {"keyword": "폭싹 속았수다"}  # /issue 키워드와 같은 다리 일


async def test_board_is_hidden_for_old_blogauto_and_tap_off_without_issue_action(bot: DealBot, settings: Settings,
                                                                                tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", trends=None)
    blogauto_status(settings)
    assert (await state(bot))["blogs"]["issue"]["trends"] is None
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=["publish", "dismiss"])
    tr = (await state(bot))["blogs"]["issue"]["trends"]
    assert tr["items"] and tr["write"] is None  # 보기만 (누를 일이 없음)


async def test_slot_full_copy_reaches_the_item(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    it = next(x for x in body["todo"] + body["blogs"]["issue"]["waiting"] if x["iid"] == "post-issue-46")
    s1 = it["detail"]["slots"][0]
    assert s1["thumb"] == "/dashboard/thumb/post-issue-46-1-ab12cd34.jpg" and s1["full"] == "/dashboard/thumb/post-issue-46-1-ab12cd34-f.jpg"
    assert all("full" not in s for s in it["detail"]["slots"][1:] if not s["thumb"])
    data = fixture("export_v2.json")
    data["posts"]["issue"][0]["slots"][0]["full"] = "../../etc/passwd"
    assert "full" not in parse_v2(data)["posts"]["issue"][0]["slots"][0]


def test_focus_and_retouch_ops() -> None:
    out = clean_args("blog.photos_batch", {"blog": "issue", "pid": 46, "ops": [
        {"op": "put", "slot": 1, "upload": U1, "focus": True, "retouch": 1}, {"op": "put", "slot": 2, "upload": U1, "edited": True}]})
    a, b = out["ops"]
    assert a["edited"] is True and a["focus"] is True and a["retouch"] == 1  # 사람만 남기기 = 편집한 사진
    assert "focus" not in b and "retouch" not in b
    for bad in ({"focus": "yes"}, {"retouch": 41}, {"retouch": True}):
        with pytest.raises(ActionError):
            clean_args("blog.photos_batch", {"blog": "issue", "pid": 46, "ops": [{"op": "put", "slot": 1, "upload": U1, **bad}]})


def test_app_js_has_the_board_badges_and_photo_tap() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    ed = (webapp.STATIC_DIR / "edit.js").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    for word in ("인기 검색어", "이미 쓴 글 있음", "▲", "▼", "NEW", "20위까지 보기", "{keyword: x.keyword}", "todoN", "할 일 ${n}개",
                 "pendingOf(i.iid)", "edPhoto(it, s)", "E.photo(h, s"):
        assert word in js, word
    for word in ("'원본', -1", "'1:1'", "'4:5'", "'16:9'", "'자유'", "90° 돌리기", "사람만 남기기", "{focus: true}", "{retouch: c.re}",
                 "'저장'", "교체·비우기", "W.SBEdit = {batch, editor, photo", "'blog.photos_batch'", "edited: true"):
        assert word in ed, word
    assert "const V = 'sb-v30'" in sw  # 10/21 merge post-edit: 발행 글 고치기 + 사진 테스트 + 제품 테스트 (sb-v30)
    assert json.dumps(fixture("action_photos_batch.json")["args"]["ops"][1]).count("retouch") == 1
