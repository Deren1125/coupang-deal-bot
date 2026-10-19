"""사진 일괄 수정 (주인 10/19 — 바꿀 것을 화면에서 모아 '수정 완료' 한 번에 블로그로): blog.photos_batch.
계약 tests/fixtures/dash_contract/action_photos_batch.json (Blog-Auto 와 같은 파일). 네트워크 없이."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import MAY_PUBLISH, ActionError, clean_args
from test_dash_api_actions import act, photo_upload
from test_dash_state import write_export

U1 = "9f0e1d2c3b4a59687766554433221100"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def batch(ops: object, blog: str = "issue") -> dict:
    return clean_args("blog.photos_batch", {"blog": blog, "pid": 46, "ops": ops})


def test_ops_are_checked_and_normalised() -> None:
    out = batch([{"op": "remove", "slot": 2, "junk": 1}, {"op": "put", "slot": 1, "upload": U1, "edited": True},
                 {"op": "put", "slot": 3, "pic": "pk-3fa9c2d1"}, {"op": "move", "from": 3, "to": 4}])
    assert out == {"blog": "issue", "pid": 46, "ops": [
        {"op": "remove", "slot": 2}, {"op": "put", "slot": 1, "upload": U1, "credit": "", "edited": True},
        {"op": "put", "slot": 3, "pic": "pk-3fa9c2d1"}, {"op": "move", "from": 3, "to": 4}]}
    assert "blog.photos_batch" in MAY_PUBLISH  # 자동 발행 모드면 ★ (다시 올리면 바로 공개)


@pytest.mark.parametrize("ops", [
    [], None, "remove", [{"op": "remove", "slot": 1}] * 41, [{"op": "wipe", "slot": 1}], [{"op": "remove", "slot": 41}],
    [{"op": "remove", "slot": True}], [{"op": "move", "from": 2, "to": 2}], [{"op": "put", "slot": 1}],
    [{"op": "put", "slot": 1, "upload": U1, "pic": "pk-3fa9c2d1"}], [{"op": "put", "slot": 1, "upload": "../x.jpg"}],
    [{"op": "put", "slot": 1, "pic": "pk-zz"}], [{"op": "put", "slot": 1, "upload": U1, "edited": "yes"}],
    [{"op": "put", "slot": 1, "upload": U1, "credit": "x" * 61}],
])
def test_bad_ops_are_refused(ops: object) -> None:
    with pytest.raises(ActionError):
        batch(ops)


def test_only_pick_and_archive() -> None:
    with pytest.raises(ActionError):
        batch([{"op": "remove", "slot": 1}], blog="hotdeal")
    assert batch([{"op": "remove", "slot": 1}], blog="finance")["blog"] == "finance"


async def test_batch_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")  # Pick = 임시저장 → 확인 없음 (계약 confirmed false)
    blogauto_status(settings)
    want = fixture("action_photos_batch.json")
    h = session_headers(bot)
    uid = photo_upload(bot, h, (1600, 1200))
    ops = [dict(o) for o in want["args"]["ops"]]
    ops[1]["upload"] = uid
    status, body = await act(bot, "blog.photos_batch", {"blog": "issue", "pid": 46, "ops": ops}, h)
    assert status == 202, body
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    got = f["args"]
    assert Path(got["ops"][1]["upload"]).name == f"{uid}.jpg"  # 올린 사진 id → 받은 파일 경로 (Blog-Auto 는 id 를 모른다)
    got["ops"][1]["upload"] = want["args"]["ops"][1]["upload"]
    assert got == want["args"] and list(got) == list(want["args"])
    used = {r["used_by"] for r in bot.db._q("SELECT used_by FROM dash_uploads WHERE id = ?", (uid,))}
    assert used == {body["id"]}


async def test_unknown_upload_and_old_blogauto_are_refused(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    ops = [{"op": "put", "slot": 1, "upload": U1}]
    status, body = await act(bot, "blog.photos_batch", {"blog": "issue", "pid": 46, "ops": ops})
    assert status == 400 and body["code"] == "bad_upload"
    assert not list((spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new").glob("*.json"))
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "photos_batch"])
    status, body = await act(bot, "blog.photos_batch", {"blog": "issue", "pid": 46, "ops": [{"op": "remove", "slot": 1}]})
    assert status == 503 and body["code"] == "unsupported"


def test_app_js_loads_the_editor_lazily_and_edit_js_has_the_flow() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    ed = (webapp.STATIC_DIR / "edit.js").read_text(encoding="utf-8")
    assert "'사진 한 번에 고치기" in js and "meta[name=sb-edit]" in js and "SBEdit" in js
    assert "function editor" not in js and "photos_batch" not in js  # 편집기·일괄 수정은 edit.js 에만
    for word in ("수정 완료", "취소", "바꿀 것 ", "✕ 빼기", "교체", "사진 추가", "편집", "'blog.photos_batch'", "1:1", "4:5", "3:4",
                 "자유", "90° 돌리기", "밝기", "'image/jpeg', 0.9", "2000", "edited"):
        assert word in ed, word
    assert "innerHTML" not in ed and "eval(" not in ed
