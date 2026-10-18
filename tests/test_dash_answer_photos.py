"""답하기에 사진 첨부 (주인 10/18 — 질문마다 '사진 첨부', 공식 페이지 캡처 같은 근거). Blog-Auto bridge.x_answer 의 answers[].uploads.
계약 tests/fixtures/dash_contract/action_answer_photos.json (Blog-Auto 와 같은 파일). 네트워크 없이."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args
from test_dash_api_actions import act, bot, photo_upload  # noqa: F401 — bot: 픽스처

U1, U2 = "9f0e1d2c3b4a59687766554433221100", "0a1b2c3d4e5f60718293a4b5c6d7e8f9"


def answer(**kw: object) -> dict:
    return clean_args("blog.answer", {"blog": "finance", "pid": 12, "text": "1) 네", **kw})


def test_answer_without_photos_stays_as_before() -> None:
    assert answer() == {"pid": 12, "blog": "finance", "text": "1) 네"}
    # 사진 칸이 모두 비면 answers 는 보내지 않는다 (옛 Blog-Auto 와 같은 args)
    assert answer(answers=[{"n": 1, "text": "네", "uploads": []}]) == {"pid": 12, "blog": "finance", "text": "1) 네"}


def test_per_answer_uploads_are_checked() -> None:
    out = answer(answers=[{"n": 1, "text": " 캡처 ", "uploads": [U1, U2]}, {"n": 2, "uploads": []}])
    assert out["answers"] == [{"n": 1, "text": "캡처", "uploads": [U1, U2]}, {"n": 2, "text": "", "uploads": []}]
    for bad in ({"n": 1, "uploads": ["../etc/passwd"]}, {"n": 0, "uploads": [U1]}, {"n": True, "uploads": [U1]},
                {"n": 1, "uploads": [U1] * 0 + [f"{i:032x}" for i in range(11)]}, {"n": 1, "text": "가" * 2001, "uploads": [U1]}, "x"):
        with pytest.raises(ActionError):
            answer(answers=[bad])
    with pytest.raises(ActionError):  # 같은 질문 두 번 · 같은 사진 두 번
        answer(answers=[{"n": 1, "uploads": [U1]}, {"n": 1, "uploads": [U2]}])
    with pytest.raises(ActionError):
        answer(answers=[{"n": 1, "uploads": [U1]}, {"n": 2, "uploads": [U1]}])
    with pytest.raises(ActionError):  # 다 합쳐 20장까지
        answer(answers=[{"n": q, "uploads": [f"{q:02x}{i:030x}" for i in range(7)]} for q in (1, 2, 3)])
    with pytest.raises(ActionError):
        answer(answers={"1": [U1]})


async def test_answer_photos_reach_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings)
    want = fixture("action_answer_photos.json")
    h = session_headers(bot)
    ids = [photo_upload(bot, h, size) for size in ((1200, 900), (900, 1200), (800, 800))]
    per = [{"n": a["n"], "text": a["text"], "uploads": ids[:2] if a["n"] == 1 else ids[2:] if a["n"] == 2 else []} for a in want["args"]["answers"]]
    args = {k: want["args"][k] for k in ("blog", "pid", "text")}
    status, body = await act(bot, "blog.answer", {**args, "answers": per}, h)
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    got = dict(f["args"])
    names = [[Path(p).name for p in a.pop("uploads")] for a in got["answers"]]
    assert names == [[f"{ids[0]}.jpg", f"{ids[1]}.jpg"], [f"{ids[2]}.jpg"], []]
    assert got == {**want["args"], "answers": [{k: v for k, v in a.items() if k != "uploads"} for a in want["args"]["answers"]]}
    assert list(got) == list(want["args"])
    used = {r["used_by"] for r in bot.db._q(f"SELECT used_by FROM dash_uploads WHERE id IN ({','.join('?' * 3)})", tuple(ids))}
    assert used == {body["id"]}


async def test_unknown_upload_is_refused(bot: DealBot, settings: Settings) -> None:  # noqa: F811
    blogauto_status(settings)
    status, body = await act(bot, "blog.answer", {"blog": "finance", "pid": 12, "text": "1) 네", "answers": [{"n": 1, "uploads": [U1]}]})
    assert status == 400 and body["code"] == "bad_upload"
    assert not list((spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new").glob("*.json"))


def test_app_js_has_a_photo_button_per_question() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "'사진 첨부'" in js and "extra.answers = per" in js and "'사진 빼기'" in js
    assert ".pv .x{" in (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
