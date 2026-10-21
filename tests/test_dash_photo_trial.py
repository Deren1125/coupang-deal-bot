"""대시보드 주인 10/21 — '사진 테스트' 화면. 네트워크 없이.

Blog-Auto 가 기록 파일 photo_trial 칸(시험 글 — 계약 export_v2.json)을 주면 블로그 탭 Pick 에 '사진 테스트 →' 링크와 화면:
글마다 자리 사진 크게(올라가는 판 final · 체크무늬) · 자리 설명 · ✅ 괜찮아 / ❌ 별로(이유 칩 · 메모) · 글 전체(배치·분포·개수) · 테스트 끝 →
다리 일 photo_feedback (계약 action_photo_feedback.json — Blog-Auto 와 같은 파일). 맨 위 '배운 원칙'. 사진을 누르면 평소 편집기.
시험 글이 없거나 예전 Blog-Auto 면 화면 없음."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import (
    BLOG_ACTIONS,
    ActionError,
    clean_args,
    needs_confirm,
    target_of,
)
from dealbot.dashboard.blogauto import TRIAL_REASONS, _photo_trial, parse_v2
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export


def test_parse_keeps_the_contract_and_drops_bad_rows() -> None:
    fx = fixture("export_v2.json")["photo_trial"]
    got = parse_v2(fixture("export_v2.json"))["photo_trial"]
    p, want = got["posts"][0], fx["posts"][0]
    assert p["pid"] == want["pid"] and p["title"] == want["title"] and p["feedback_done"] == 2
    assert p["slots"][0] == want["slots"][0] and p["slots"][1]["qa"] == {"ok": False, "issues": ["border"]}
    assert "final" not in p["slots"][2] and p["slots"][2]["desc"] == "마무리 컷"  # 빈 자리
    assert got["rules"] == fx["rules"] and list(TRIAL_REASONS) == fx["reasons"]
    bad = _photo_trial({"posts": [{"pid": "x"}, {"pid": 3, "slots": [{"slot": 1, "final": "../etc/passwd", "qa": {"issues": ["<b>"]}}]}],
                        "rules": [{"id": "nope", "area": "photo", "text": "x"}]})
    assert [q["pid"] for q in bad["posts"]] == [3] and bad["posts"][0]["slots"][0] == {
        "slot": 1, "credit": "", "desc": "", "qa": {"ok": False, "issues": []}} and bad["rules"] == []
    assert _photo_trial({"posts": []}) is None and _photo_trial(None) is None and parse_v2({})["photo_trial"] is None


def test_feedback_args_match_the_contract() -> None:
    a = fixture("action_photo_feedback.json")
    assert "photo_feedback" in BLOG_ACTIONS and "photo_feedback" in fixture("status_blogauto.json")["actions"]
    out = clean_args("blog.photo_feedback", a["args"])
    assert out == a["args"] and target_of("blog.photo_feedback", out) == a["target"]
    assert not needs_confirm("blog.photo_feedback", out)
    assert clean_args("blog.photo_feedback", {"pid": 52, "verdict": "ok", "reasons": ["spread"]}) == {
        "blog": "issue", "pid": 52, "verdict": "ok", "reasons": []}  # 글 전체 · 괜찮아면 이유 없이
    assert clean_args("blog.photo_feedback", {"pid": 52, "verdict": "done", "note": "끝"}) == {
        "blog": "issue", "pid": 52, "verdict": "done", "reasons": []}
    for bad in ({"pid": 52, "verdict": "bad"}, {"pid": 52, "verdict": "meh"}, {"pid": 52, "slot": 41, "verdict": "ok"},
                {"pid": 52, "verdict": "bad", "reasons": ["nope"]}, {"pid": 52, "verdict": "bad", "note": "가" * 201},
                {"verdict": "ok"}):
        with pytest.raises(ActionError):
            clean_args("blog.photo_feedback", bad)


async def test_page_view_buttons_queue_the_bridge_work(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    t = body["photo_trial"]
    p = t["posts"][0]
    assert t["rules"][0]["text"].startswith("TV·방송 화면 캡처") and p["iid"] == "post-issue-52"
    assert p["slots"][0]["final"] == "/dashboard/thumb/post-issue-52-1-5d6e7f80-u.jpg"
    assert p["edit"][0] == {"slot": 1, "label": "정면 전신", "thumb": "/dashboard/thumb/post-issue-52-1-5d6e7f80-u.jpg",
                            "full": "/dashboard/thumb/post-issue-52-1-5d6e7f80-f.jpg",
                            "final": "/dashboard/thumb/post-issue-52-1-5d6e7f80-u.jpg"}  # 평소 편집기 모양
    assert p["done"]["args"] == {"blog": "issue", "pid": 52, "verdict": "done"} and p["done"]["label"] == "테스트 끝"
    want = fixture("action_photo_feedback.json")
    status, res = await act(bot, p["fb"]["action"], dict(p["fb"]["args"], **{k: want["args"][k] for k in ("slot", "verdict",
                                                                                                       "reasons", "note")}),
                            session_headers(bot))
    assert status == 202, res
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{res['id']}.json")
    assert (f["action"], f["args"], f["target"], f["confirmed"]) == (want["action"], want["args"], want["target"], False)
    status, res = await act(bot, p["done"]["action"], p["done"]["args"], session_headers(bot))
    assert status == 409 and res["code"] == "busy"  # 같은 글의 앞 일이 끝나기 전에는 (두 번 막기)
    assert clean_args(p["done"]["action"], p["done"]["args"]) == {"blog": "issue", "pid": 52, "verdict": "done", "reasons": []}


async def test_page_hidden_without_trial_posts_or_for_old_blogauto(bot: DealBot, settings: Settings,  # noqa: F811
                                                                   tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", photo_trial=None)
    blogauto_status(settings)
    assert (await state(bot))["photo_trial"] is None
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "photo_feedback"])
    assert (await state(bot))["photo_trial"] is None


def test_app_and_edit_js_have_the_page() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    ed = (webapp.STATIC_DIR / "edit.js").read_text(encoding="utf-8")
    assert "/^(pics|search|trial|ptrial)$/" in js and "'사진 테스트 →'" in js and "function vTrial()" in js and "E.trial(" in js
    assert "S.photo_trial && key === 'issue'" in js and "E2.photo(h, s" in js  # 사진을 누르면 평소 편집기
    for word in ("'배운 원칙'", "'✅ 괜찮아'", "'❌ 별로'", "여백 남음", "UI 남음", "얼굴/몸 잘림", "너무 넓음", "너무 좁음", "다른 사람", "흐림",
                 "중복", "글자 많음", "방송 캡처", "비율 이상", "어두움", "기타", "사진 배치", "사진 분포", "사진 개수", "'보내기'",
                 "W.SBEdit = {batch, editor, photo, trial}", "class: 'ck'"):
        assert word in ed, word
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "const V = 'sb-v27'" in sw  # 10/21 merge (sb-v27)
