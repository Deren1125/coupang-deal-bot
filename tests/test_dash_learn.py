"""배운 것으로 나아지기 (주인 10/20 — "워크플로우도 완전히 자동화하고 싶어") — 대시보드 쪽, 네트워크 없이.

Blog-Auto learn.py 가 기록 파일 learn 칸(export_v2.json)으로 이번 주 개선안·적용 규칙·👍/👎 를 주면
· 블로그 탭 '이번 주 개선안' 카드: 그 블로그(+모든 블로그) 개선안 ✅ 적용 / ❌ 빼기 · 적용 규칙 지우기 → 다리 일 learn_decide
· 최근 글 👍/👎 (+ 한 줄 이유) → 다리 일 post_feedback (계약 action_post_feedback.json — 두 저장소 같은 파일)"""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args, needs_confirm, target_of
from dealbot.dashboard.blogauto import _learn, parse_v2
from test_dash_api_actions import act
from test_dash_state import state, write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def test_learn_parse_keeps_the_contract_and_drops_bad_rows() -> None:
    fx = fixture("export_v2.json")["learn"]
    got = parse_v2(fixture("export_v2.json"))["learn"]
    assert [p["id"] for p in got["proposals"]] == [p["id"] for p in fx["proposals"]]
    assert got["proposals"][0] == {k: fx["proposals"][0][k] for k in ("id", "blog", "area", "rule_text", "evidence", "examples", "effect",
                                                                     "state")}
    assert got["rules"][1]["config"] == "tags.min = 12" and got["votes"] == {"40": "up", "46": "down"}
    assert got["auto_apply_days"] is None and got["lessons_week"] == 9
    bad = _learn({"proposals": [{"id": "lp-zz", "blog": "issue", "area": "title", "rule_text": "x"},
                                {"id": "lp-00000001", "blog": "nope", "area": "title", "rule_text": "x"},
                                {"id": "lp-00000002", "blog": "issue", "area": "politics", "rule_text": "x"},
                                {"id": "lp-00000003", "blog": "all", "area": "style", "rule_text": "가" * 300, "state": "weird"}],
                  "rules": ["junk", {"id": "lr-00000001", "blog": "issue", "area": "photo", "rule_text": ""}],
                  "votes": {"../x": "up", "12": "meh", "13": "down"}, "auto_apply_days": True})
    assert [p["id"] for p in bad["proposals"]] == ["lp-00000003"] and len(bad["proposals"][0]["rule_text"]) <= 200
    assert bad["proposals"][0]["state"] == "open" and bad["rules"] == [] and bad["votes"] == {"13": "down"}
    assert bad["auto_apply_days"] is None and _learn(None) is None and parse_v2({})["learn"] is None


def test_feedback_and_decide_args_match_the_contract() -> None:
    a = fixture("action_post_feedback.json")
    assert clean_args("blog.post_feedback", a["args"]) == a["args"]
    assert target_of("blog.post_feedback", a["args"]) == a["target"] and not needs_confirm("blog.post_feedback", a["args"])
    assert clean_args("blog.post_feedback", {"blog": "issue", "pid": 3, "vote": "none", "reason": "  "}) == {
        "blog": "issue", "pid": 3, "vote": "none"}
    d = fixture("action_learn_decide.json")
    assert clean_args("blog.learn_decide", d["args"]) == d["args"] and target_of("blog.learn_decide", d["args"]) == d["target"]
    assert not needs_confirm("blog.learn_decide", d["args"])
    assert clean_args("blog.learn_decide", {"id": "lr-0000abcd", "decision": "remove"}) == {"id": "lr-0000abcd", "decision": "remove"}
    for bad in ({"blog": "issue", "pid": 3, "vote": "meh"}, {"blog": "issue", "pid": 3, "vote": "up", "reason": "가" * 81},
                {"blog": "x", "pid": 3, "vote": "up"}, {"blog": "issue", "vote": "up"}):
        with pytest.raises(ActionError):
            clean_args("blog.post_feedback", bad)
    for bad in ({"id": "lp-0000abcd", "decision": "remove"}, {"id": "lr-0000abcd", "decision": "apply"}, {"id": "../x", "decision": "apply"},
                {"id": "lp-0000abcd", "decision": "maybe"}):
        with pytest.raises(ActionError):
            clean_args("blog.learn_decide", bad)


async def test_blog_view_has_the_card_votes_and_buttons_queue_bridge_work(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    lr = body["blogs"]["issue"]["learn"]
    p = lr["proposals"][0]
    assert p["rule_text"].startswith("제목 끝에") and p["apply"]["args"] == {"id": "lp-1a2b3c4d", "decision": "apply"}
    assert p["reject"]["label"] == "❌ 빼기" and not p["apply"]["confirm"]
    assert [r["id"] for r in lr["rules"]] == ["lr-9c0d1e2f"] and lr["rules"][0]["remove"]["args"]["decision"] == "remove"
    fin = body["blogs"]["finance"]["learn"]
    assert [p["state"] for p in fin["proposals"]] == ["rejected"] and "apply" not in fin["proposals"][0]  # 정한 것은 버튼 없음
    rec = body["blogs"]["issue"]["recent"][0]
    assert rec["fb"]["vote"] == "up" and rec["fb"]["act"]["args"] == {"blog": "issue", "pid": 40}
    status, res = await act(bot, "blog.post_feedback", dict(rec["fb"]["act"]["args"], vote="down", reason="사진이 너무 어두웠어요"))
    assert status == 202, res
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{res['id']}.json")
    want = fixture("action_post_feedback.json")
    assert (f["action"], f["args"], f["target"]) == (want["action"], want["args"], want["target"])
    status, res = await act(bot, p["apply"]["action"], p["apply"]["args"])
    assert status == 202, res
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{res['id']}.json")
    assert (f["action"], f["args"]) == ("learn_decide", {"id": "lp-1a2b3c4d", "decision": "apply"})


async def test_card_hidden_for_old_blogauto_and_buttons_off_without_the_actions(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", learn=None)
    blogauto_status(settings)
    body = await state(bot)
    assert body["blogs"]["issue"]["learn"] is None and "fb" not in body["blogs"]["issue"]["recent"][0]
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=["publish", "dismiss"])
    body = await state(bot)
    lr = body["blogs"]["issue"]["learn"]
    assert lr["proposals"] and "apply" not in lr["proposals"][0] and lr["rules"][0]["remove"] is None  # 보기만
    assert "fb" not in body["blogs"]["issue"]["recent"][0]
    status, res = await act(bot, "blog.post_feedback", {"blog": "issue", "pid": 40, "vote": "up"})
    assert status == 503 and res["code"] == "unsupported"


def test_app_js_has_the_card_and_votes() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for word in ("이번 주 개선안", "learnBox(b.learn)", "적용 중인 규칙", "fbBtns(r)", "'👍'", "'👎'", "한 줄 이유", "{vote: 'none'}",
                 "{vote: v, reason: f.value.trim()}", "저절로 적용돼요"):
        assert word in js, word
