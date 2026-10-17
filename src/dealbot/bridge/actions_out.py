"""대시보드 → Blog-Auto 할 일 (dash_actions/new/<a_…>.json) 쓰기 + 결과(results/) 읽기 + 오래된 것 정리. 표는 dash_actions.

DashAction (spec §2.3):
    {"v":1,"id":"a_…","ts":…,"expires_at":ts+600,"action":"photos","blog":"issue","args":{…},"target":"post-issue-46",
     "confirmed":true,"origin":{"via":"dashboard","session":"3fa9c2d1","cid":"c_…"}}
DashResult (Blog-Auto 가 results/<id>.json 에, 끝날 때까지 고쳐 씀):
    {"v":1,"id":"a_…","status":"running|ok|error|rejected","code":"…","text":"≤120","post":{"blog","pid","status"},
     "started_at":…,"updated_at":…,"done_at":…}

- 같은 일(action + target)이 아직 안 끝났으면 새로 쓰지 않고 그 id 를 돌려준다 (두 기기·오래된 화면에서 두 번 눌러도 한 번)
- 결과는 DB 에 넣은 뒤(끝난 것만) 파일을 지운다. 10분 안에 Blog-Auto 가 안 맡으면 expired/ 로 옮기고 'expired'
  (비밀이 든 파일 — kakao_connect — 은 옮기지 않고 지운다), 20분 넘게 running 이면 'unknown'(확인 필요, 실패 아님)
- args 는 DB 에 비밀값을 가려서 (토큰·코드·카카오 주소는 "[비밀]"). kakao_auth_url 결과의 주소는 DB 에 넣지 않고
  메모리에 5분만 — 화면이 한 번 받아 가면 지운다
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dealbot.bridge import spool
from dealbot.bridge.status import BlogAutoStatus
from dealbot.dashboard.model import safe_text

log = logging.getLogger(__name__)

EXPIRE_S = 600
UNKNOWN_S = 20 * 60
KEEP_DAYS = 14
TERMINAL = ("ok", "error", "rejected", "expired")
OPEN = ("queued", "running")
SECRET_ACTIONS = ("kakao_connect",)  # 파일에 비밀값이 든 일 (expired 로 옮기지 않고 지움)
SECRET_KEYS = ("token", "code", "url", "redirect", "password", "secret")
URL_TTL_S = 300
TEXT_MAX = 120
INFO_TEXT_MAX = 1500
RESULT_STATUSES = ("running", "ok", "error", "rejected")


def redact(action: str, args: dict[str, Any]) -> dict[str, Any]:
    """DB·기록에 남길 args: 비밀값은 '[비밀]'. 사진 경로는 파일 이름만."""
    out: dict[str, Any] = {}
    secret_action = action.split(".")[-1] in (*SECRET_ACTIONS, "token", "code")
    for k, v in args.items():
        if k in SECRET_KEYS and (secret_action or k != "url"):
            out[k] = "[비밀]"
        elif k == "uploads" and isinstance(v, list):
            out[k] = [Path(str(x)).name[:40] for x in v][:20]
        elif isinstance(v, str):
            out[k] = safe_text(v, 200)
        else:
            out[k] = v
    return out


class ActionStore:
    """dash_actions 표 (딜봇에서 바로 한 일도 같이)."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def insert(self, *, aid: str, ts: float, blog: str | None, action: str, target: str, args: dict[str, Any], status: str,
               cid: str | None, session: str, result: dict[str, Any] | None = None, error: str | None = None) -> None:
        with self.db._lock:
            self.db._conn.execute(
                "INSERT INTO dash_actions (id, ts, blog, action, target, args, status, result, error, cid, session, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, ts, blog, action, target, json.dumps(redact(action, args), ensure_ascii=False), status,
                 json.dumps(result, ensure_ascii=False) if result is not None else None, error, cid, session, ts))

    def update(self, aid: str, *, status: str, result: dict[str, Any] | None, now: float, error: str | None = None) -> None:
        with self.db._lock:
            self.db._conn.execute("UPDATE dash_actions SET status = ?, result = ?, error = ?, updated_at = ? WHERE id = ?",
                                  (status, json.dumps(result, ensure_ascii=False) if result is not None else None, error, now, aid))

    def get(self, aid: str) -> dict[str, Any] | None:
        r = self.db._one("SELECT * FROM dash_actions WHERE id = ?", (aid,))
        return dict(r) if r else None

    def by_cid(self, cid: str, since: float) -> dict[str, Any] | None:
        r = self.db._one("SELECT * FROM dash_actions WHERE cid = ? AND ts >= ? ORDER BY ts DESC LIMIT 1", (cid, since))
        return dict(r) if r else None

    def open_for(self, action: str, target: str) -> dict[str, Any] | None:
        marks = ",".join("?" for _ in OPEN)
        r = self.db._one(f"SELECT * FROM dash_actions WHERE action = ? AND target = ? AND status IN ({marks}) "
                         "ORDER BY ts DESC LIMIT 1", (action, target, *OPEN))
        return dict(r) if r else None

    def open_rows(self) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in OPEN)
        return [dict(r) for r in self.db._q(f"SELECT * FROM dash_actions WHERE status IN ({marks})", OPEN)]

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db._q("SELECT * FROM dash_actions ORDER BY ts DESC LIMIT ?", (limit,))]

    def prune(self, before: float) -> int:
        with self.db._lock:
            return int(self.db._conn.execute("DELETE FROM dash_actions WHERE ts < ?", (before,)).rowcount or 0)


def status_of(row: dict[str, Any]) -> dict[str, Any]:
    """dash_actions 줄 → 화면의 ActionStatus."""
    try:
        res = json.loads(row.get("result") or "null") or {}
    except ValueError:
        res = {}
    out = {"id": row["id"], "action": row["action"], "target": row.get("target") or "", "status": row["status"],
           "code": str(res.get("code") or ""), "text": str(res.get("text") or ""), "at": row["ts"],
           "done_at": res.get("done_at") if row["status"] in TERMINAL else None}
    if isinstance(res.get("post"), dict):
        out["post"] = res["post"]
    return out


class ActionsOut:
    def __init__(self, db: Any, data_dir: Path | str, *, status: Callable[[], BlogAutoStatus],
                 clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.store = ActionStore(db)
        self.data_dir = Path(data_dir)
        self.status = status
        self.clock = clock
        self._urls: dict[str, tuple[str, float]] = {}  # kakao_auth_url 결과 (메모리에만, 5분)

    def root(self) -> Path:
        return spool.root(self.data_dir, spool.DASH_ACTIONS)

    def submit(self, *, action: str, blog: str | None, args: dict[str, Any], target: str, confirmed: bool, session: str,
               cid: str | None) -> tuple[str, bool]:
        """Blog-Auto 에 맡긴다 → (id, 이미 같은 일이 있어 그 id 를 돌려줬는지). action 은 Blog-Auto 이름 (publish …).
        표의 action 은 'blog.publish' 처럼."""
        full = f"blog.{action}"
        if target:
            cur = self.store.open_for(full, target)
            if cur is not None:
                return cur["id"], True
        now = self.clock()
        aid = spool.new_id("a", now)
        obj = {"v": 1, "id": aid, "ts": now, "expires_at": now + EXPIRE_S, "action": action, "blog": blog, "args": args,
               "target": target, "confirmed": bool(confirmed), "origin": {"via": "dashboard", "session": session, "cid": cid or ""}}
        spool.write(self.root(), aid, obj)
        self.store.insert(aid=aid, ts=now, blog=blog, action=full, target=target, args=args, status="queued", cid=cid,
                          session=session)
        return aid, False

    def take_url(self, aid: str) -> str | None:
        """kakao_auth_url 결과 주소 — 한 번만 (5분 안)."""
        got = self._urls.pop(aid, None)
        if got is None or self.clock() - got[1] > URL_TTL_S:
            return None
        return got[0]

    def _clean_result(self, row: dict[str, Any], data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        status = data.get("status") if data.get("status") in RESULT_STATUSES else "error"
        code = data.get("code") if isinstance(data.get("code"), str) and len(data.get("code")) <= 40 else ""
        limit = INFO_TEXT_MAX if row["action"] == "blog.info" else TEXT_MAX
        text = data.get("text") if isinstance(data.get("text"), str) else ""
        text = "\n".join(safe_text(ln, limit) for ln in text.splitlines()[:40] if ln.strip())[:limit] if row["action"] == "blog.info" \
            else safe_text(text, limit)
        res: dict[str, Any] = {"code": code, "text": text}
        post = data.get("post")
        if isinstance(post, dict):
            res["post"] = {k: post.get(k) for k in ("blog", "pid", "status") if isinstance(post.get(k), str | int)}
        for k in ("started_at", "updated_at", "done_at"):
            if isinstance(data.get(k), int | float) and not isinstance(data.get(k), bool):
                res[k] = float(data[k])
        if row["action"] == "blog.kakao_auth_url":
            url = data.get("url")
            if not isinstance(url, str) and isinstance(data.get("result"), dict):
                url = data["result"].get("url")
            if isinstance(url, str) and url.startswith("https://") and len(url) <= 2000:
                self._urls[row["id"]] = (url, self.clock())
            res = {"code": code or ("done" if status == "ok" else "failed"), "text": ""}
        return status, res

    def ingest(self) -> int:
        """results/*.json → 표. 끝난 결과는 DB 에 넣은 뒤 파일을 지운다. 바뀐 줄 수."""
        r = self.root()
        n = 0
        now = self.clock()
        for name in spool.names(r, "results"):
            path = r / "results" / f"{name}.json"
            data = spool.read(path)
            row = self.store.get(name)
            if data is None or data.get("id") != name or row is None:
                spool.expire(r, path, "bad result")
                continue
            status, res = self._clean_result(row, data)
            if row["status"] in TERMINAL:
                spool.remove(path)  # 이미 끝난 일 (같은 결과를 다시 씀)
                continue
            same = json.dumps(res, ensure_ascii=False) == row.get("result")
            if not (same and (status == row["status"] or row["status"] == "unknown")):  # 그대로면 쓰지 않음 ('확인 필요'도 그대로)
                self.store.update(name, status=status, result=res, now=now)
                n += 1
            if status in TERMINAL:
                spool.remove(path)
        return n

    def sweep(self) -> dict[str, int]:
        """안 맡은 지 10분 → expired, running 20분 넘게 소식 없음 → unknown, 14일 지난 것 정리."""
        r = self.root()
        now = self.clock()
        out = {"expired": 0, "unknown": 0, "pruned": 0}
        for name in spool.names(r, "new"):
            path = r / "new" / f"{name}.json"
            data = spool.read(path) or {}
            exp = data.get("expires_at")
            if isinstance(exp, int | float) and now <= exp:
                continue
            if data.get("action") in SECRET_ACTIONS:
                spool.remove(path)  # 비밀값이 든 파일은 남기지 않는다
            else:
                spool.expire(r, path, "not claimed in time")
            if (row := self.store.get(name)) is not None and row["status"] == "queued":
                self.store.update(name, status="expired", result={"code": "expired", "text": "", "done_at": now}, now=now)
            out["expired"] += 1
        for row in self.store.open_rows():
            if row["status"] != "running":
                continue
            try:
                res = json.loads(row.get("result") or "null") or {}
            except ValueError:
                res = {}
            seen = res.get("updated_at") or res.get("started_at") or row["ts"]  # Blog-Auto 가 결과를 마지막으로 고친 시각
            if now - float(seen) > UNKNOWN_S:
                self.store.update(row["id"], status="unknown", result=res, now=now)
                out["unknown"] += 1
        cut = now - KEEP_DAYS * 86400
        out["pruned"] = self.store.prune(cut)
        for p in (r / "expired").glob("*.json"):
            try:
                if p.stat().st_mtime < cut:
                    spool.remove(p)
            except OSError:
                pass
        return out
