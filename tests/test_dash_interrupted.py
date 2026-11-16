"""서버 재시작에 끊긴 대시보드 일 (주인 11/16 — 단추를 누르면 자꾸 '확인이 필요해요'): Blog-Auto 가 code interrupted 를 쓰면
'끊겼어요 — 다시 눌러 주세요' · 옛 Blog-Auto(code failed + '중간에 멈췄어요 …')도 같은 뜻으로 · 20분 답 없음 문구는 이유가 보이게 ·
끊긴 체크는 다시 맡김."""

from __future__ import annotations

import json
import time
from pathlib import Path

from dealbot.bridge import spool, status
from dealbot.bridge.actions_out import ActionsOut, status_of
from dealbot.dashboard import routine
from dealbot.storage.db import Database

STATIC = Path(__file__).parents[1] / "src" / "dealbot" / "dashboard" / "static"


def _ingest(tmp_path: Path, res: dict) -> dict:
    db = Database(tmp_path / "t.db")
    out = ActionsOut(db, tmp_path, status=lambda: status.BlogAutoStatus(at=time.time(), bridge=1))
    aid = spool.new_id("a")
    out.store.insert(aid=aid, ts=time.time(), blog="issue", action="blog.pasted", target="", args={}, status="queued",
                     cid=None, session="s")
    now = time.time()
    spool.write(out.root(), aid, {"v": 1, "id": aid, "started_at": now, "updated_at": now, "done_at": now, "post": None, **res},
                sub="results")
    out.ingest()
    row = out.store.get(aid)
    db.close()
    return status_of(row)  # type: ignore[arg-type]


def test_new_and_old_blogauto_interrupted_results(tmp_path: Path) -> None:
    new = _ingest(tmp_path / "a", {"status": "error", "code": "interrupted", "text": "서버가 다시 켜지며 끊겼어요 — 다시 눌러 주세요"})
    assert (new["status"], new["code"]) == ("error", "interrupted")
    old = _ingest(tmp_path / "b", {"status": "error", "code": "failed", "text": "중간에 멈췄어요 — 상태를 확인하고 다시 눌러 주세요"})
    assert (old["status"], old["code"]) == ("error", "interrupted")
    other = _ingest(tmp_path / "c", {"status": "error", "code": "failed", "text": "실패했어요 (TimeoutError: x)"})
    assert other["code"] == "failed"


def test_app_js_says_why() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "interrupted: '서버가 다시 켜지며 끊겼어요 — 다시 눌러 주세요'" in js
    assert "unknown: '서버가 오래 답이 없어요 — 상태를 보고 필요하면 다시 눌러 주세요'" in js
    assert "확인이 필요해요 — 잠시 뒤 상태를 봐 주세요" not in js


def test_interrupted_relay_is_sent_again() -> None:
    assert routine._code({"result": json.dumps({"code": "interrupted"})}) == "interrupted"
    assert routine._code({"result": "bad"}) == "" and routine._code(None) == ""
    src = Path(routine.__file__).read_text(encoding="utf-8")
    assert 'status == "error" and _code(row) == "interrupted"' in src
