"""주인 알림·웹 푸시 구독·정리·스레드 답글 표 다루기 (표는 storage/db.py 의 DASH_SCHEMA). 시각은 epoch 초.

알림 한 줄 = (src, key). 같은 키가 다시 열리면 그 줄을 고친다 (같은 일은 한 줄 · 한 번):
- 해결된 뒤 다시 열림: 해결한 시각보다 오래된 열림(at)은 무시 (해결이 먼저 들어와도 되살아나지 않게). 아니면 다시 연다 —
  30분 안에 같은 글로 다시 열리면 푸시는 다시 보내지 않는다
- 열려 있는데 ttl_h 가 지난 줄에 또 열림 → 새 일로 (푸시도 다시)
- 해결(resolve)은 같은 src 줄만. 키가 ':' 로 끝나면 앞부분이 같은 키 모두 (Blog-Auto 의 'login:' 이 딜봇 'login:claude_cli' 를 닫지 않게)
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from dealbot.notify.links import clean_links

REOPEN_SAME_S = 30 * 60
SEEN_DAYS = 7
REPLIES_DAYS = 7


@dataclass(slots=True)
class Notice:
    src: str
    key: str
    kind: str
    blog: str
    iid: str
    sev: str
    urgent: bool
    quiet: bool
    title: str
    body: str
    short: str
    ttl_h: float
    group: str
    wait_s: float | None
    wait_n: int | None
    delay_s: float
    opened_at: float
    updated_at: float
    resolved_at: float | None
    pushed_at: float | None
    push_ok: bool | None
    push_tries: int
    next_try: float | None
    kakao_at: float | None
    links: list[dict[str, str]] = field(default_factory=list)  # 사진 받을 곳 [{label, url}] (notify/links.py)

    def open_at(self, now: float) -> bool:
        return self.resolved_at is None and now < self.opened_at + self.ttl_h * 3600

    @property
    def ref(self) -> tuple[str, str]:
        return self.src, self.key


def _notice(r: Any) -> Notice:
    return Notice(
        src=r["src"], key=r["key"], kind=r["kind"], blog=r["blog"], iid=r["iid"], sev=r["sev"], urgent=bool(r["urgent"]),
        quiet=bool(r["quiet"]), title=r["title"], body=r["body"], short=r["short"], ttl_h=float(r["ttl_h"]), group=r["grp"],
        wait_s=r["wait_s"], wait_n=r["wait_n"], delay_s=float(r["delay_s"] or 0), opened_at=float(r["opened_at"]),
        updated_at=float(r["updated_at"]), resolved_at=r["resolved_at"], pushed_at=r["pushed_at"],
        push_ok=None if r["push_ok"] is None else bool(r["push_ok"]), push_tries=int(r["push_tries"] or 0),
        next_try=r["next_try"], kakao_at=r["kakao_at"], links=_links(r),
    )


def _links(r: Any) -> list[dict[str, str]]:
    try:
        raw = r["links"]
    except (IndexError, KeyError):
        return []
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return clean_links(data)


class NotifyStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ------------------------------------------------------------ 알림
    def get(self, src: str, key: str) -> Notice | None:
        r = self.db._one("SELECT * FROM owner_notices WHERE src = ? AND key = ?", (src, key))
        return _notice(r) if r else None

    def upsert_open(self, n: dict[str, Any], now: float) -> tuple[str, Notice | None]:
        """열림 하나 적용 → ('new'|'reopened'|'renewed'|'updated'|'stale', 줄). n 은 걸러 둔 알림 (ingest·router)."""
        src, key = n["src"], n["key"]
        at = float(n.get("at") or now)
        cur = self.get(src, key)
        fields = dict(kind=n.get("kind", ""), blog=n.get("blog", ""), iid=n["iid"], sev=n.get("sev", "todo"),
                      urgent=int(bool(n.get("urgent"))), quiet=int(bool(n.get("quiet"))), title=n.get("title", ""),
                      body=n.get("body", ""), short=n.get("short", ""), ttl_h=float(n.get("ttl_h") or 24),
                      grp=n.get("group", "") or "", wait_s=n.get("wait_s"), wait_n=n.get("wait_n"),
                      delay_s=float(n.get("delay_s") or 0),
                      links=json.dumps(clean_links(n.get("links")), ensure_ascii=False) if n.get("links") else None)
        with self.db._lock:
            if cur is None:
                self.db._conn.execute(
                    "INSERT INTO owner_notices (src, key, kind, blog, iid, sev, urgent, quiet, title, body, short, ttl_h, grp, "
                    "wait_s, wait_n, delay_s, links, opened_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (src, key, *fields.values(), now, now))
                return "new", self.get(src, key)
            if cur.resolved_at is not None:
                if at < cur.resolved_at:
                    return "stale", cur
                same = (now - cur.resolved_at) <= REOPEN_SAME_S and cur.title == fields["title"] and cur.body == fields["body"]
                reset = "" if same else ", pushed_at = NULL, push_ok = NULL, push_tries = 0, next_try = NULL, kakao_at = NULL"
                self._update(src, key, fields, f"resolved_at = NULL, opened_at = ?, updated_at = ?{reset}", (now, now))
                return "reopened", self.get(src, key)
            if now >= cur.opened_at + cur.ttl_h * 3600:  # 열린 채 ttl 이 지난 일에 또 열림 → 새 일
                self._update(src, key, fields, "opened_at = ?, updated_at = ?, pushed_at = NULL, push_ok = NULL, push_tries = 0, "
                             "next_try = NULL, kakao_at = NULL", (now, now))
                return "renewed", self.get(src, key)
            # 이미 열린 일: 글·급함만 새로 (푸시 상태는 그대로 — 같은 일은 한 번). 급함은 한 번 급했으면 그대로
            fields["urgent"] = int(bool(fields["urgent"]) or cur.urgent)
            fields["quiet"] = int(bool(fields["quiet"]) and cur.quiet)
            self._update(src, key, fields, "updated_at = ?", (now,))
            return "updated", self.get(src, key)

    def _update(self, src: str, key: str, fields: dict[str, Any], extra: str, extra_params: tuple[Any, ...]) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db._conn.execute(f"UPDATE owner_notices SET {sets}, {extra} WHERE src = ? AND key = ?",
                              (*fields.values(), *extra_params, src, key))

    def resolve(self, src: str, keys: list[str], now: float, *, resolved_at: float | None = None) -> list[Notice]:
        """같은 src 의 열린 줄만 닫는다. ':' 로 끝나는 키는 앞부분. 닫은 줄들 (닫기 전 상태).
        resolved_at: 해결 시각 (그 알림을 낸 쪽 시계 — 다시 열림의 at 과 비교하는 값). 없으면 now."""
        done: list[Notice] = []
        when = now if resolved_at is None else resolved_at
        for key in keys:
            if not key:
                continue
            if key.endswith(":"):
                like = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                rows = self.db._q("SELECT * FROM owner_notices WHERE src = ? AND key LIKE ? ESCAPE '\\' AND resolved_at IS NULL",
                                  (src, like))
            else:
                rows = self.db._q("SELECT * FROM owner_notices WHERE src = ? AND key = ? AND resolved_at IS NULL", (src, key))
            for r in rows:
                n = _notice(r)
                with self.db._lock:
                    self.db._conn.execute("UPDATE owner_notices SET resolved_at = ?, updated_at = ? WHERE src = ? AND key = ?",
                                          (when, now, n.src, n.key))
                done.append(n)
        return done

    def open_rows(self, now: float) -> list[Notice]:
        rows = self.db._q("SELECT * FROM owner_notices WHERE resolved_at IS NULL ORDER BY opened_at")
        return [n for n in map(_notice, rows) if n.open_at(now)]

    def since(self, start: float, end: float, *, urgent: bool | None = None) -> list[Notice]:
        """start~end 사이에 열린 줄 (해결됐어도) — 정리의 '소식'."""
        sql = "SELECT * FROM owner_notices WHERE opened_at >= ? AND opened_at < ?"
        params: tuple[Any, ...] = (start, end)
        if urgent is not None:
            sql += " AND urgent = ?"
            params += (int(urgent),)
        return [_notice(r) for r in self.db._q(sql + " ORDER BY opened_at", params)]

    def set_push(self, refs: list[tuple[str, str]], **values: Any) -> None:
        if not refs or not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.db._lock:
            for src, key in refs:
                self.db._conn.execute(f"UPDATE owner_notices SET {sets} WHERE src = ? AND key = ?", (*values.values(), src, key))

    def prune(self, before: float) -> int:
        with self.db._lock:
            a = self.db._conn.execute("DELETE FROM owner_notices WHERE (resolved_at IS NOT NULL AND resolved_at < ?) "
                                      "OR (opened_at + ttl_h * 3600 < ?)", (before, before)).rowcount
            b = self.db._conn.execute("DELETE FROM owner_notice_seen WHERE at < ?", (before,)).rowcount
            c = self.db._conn.execute("DELETE FROM push_log WHERE at < ?", (before,)).rowcount
        return int(a or 0) + int(b or 0) + int(c or 0)

    # ------------------------------------------------------------ 받은 파일 id
    def seen(self, nid: str) -> bool:
        return self.db._one("SELECT 1 FROM owner_notice_seen WHERE id = ?", (nid,)) is not None

    def mark_seen(self, nid: str, now: float) -> None:
        with self.db._lock:
            self.db._conn.execute("INSERT OR IGNORE INTO owner_notice_seen (id, at) VALUES (?, ?)", (nid, now))

    # ------------------------------------------------------------ 푸시 구독
    def subs(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM push_subs" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY created"
        return [dict(r) for r in self.db._q(sql)]

    def sub(self, sub_id: str) -> dict[str, Any] | None:
        r = self.db._one("SELECT * FROM push_subs WHERE id = ?", (sub_id,))
        return dict(r) if r else None

    def sub_by_hash(self, h: str) -> dict[str, Any] | None:
        r = self.db._one("SELECT * FROM push_subs WHERE endpoint_hash = ?", (h,))
        return dict(r) if r else None

    def add_sub(self, *, endpoint: str, endpoint_hash: str, p256dh: str, auth: str, label: str, session: str,
                now: float) -> str:
        """같은 주소면 그 줄을 새로 (기기 하나에 한 줄). 기기 id."""
        cur = self.sub_by_hash(endpoint_hash)
        with self.db._lock:
            if cur is not None:
                self.db._conn.execute("UPDATE push_subs SET p256dh = ?, auth = ?, label = ?, session = ?, enabled = 1, fails = 0, "
                                      "last_err_code = NULL WHERE id = ?", (p256dh, auth, label, session, cur["id"]))
                return str(cur["id"])
            sub_id = "d" + secrets.token_hex(5)
            self.db._conn.execute(
                "INSERT INTO push_subs (id, endpoint, endpoint_hash, p256dh, auth, label, session, created) VALUES (?,?,?,?,?,?,?,?)",
                (sub_id, endpoint, endpoint_hash, p256dh, auth, label, session, now))
            return sub_id

    def drop_sub(self, sub_id: str) -> bool:
        with self.db._lock:
            return bool(self.db._conn.execute("DELETE FROM push_subs WHERE id = ?", (sub_id,)).rowcount)

    def sub_result(self, sub_id: str, *, ok: bool, code: int, now: float) -> None:
        with self.db._lock:
            if ok:
                self.db._conn.execute("UPDATE push_subs SET last_ok = ?, fails = 0, last_err_code = NULL WHERE id = ?", (now, sub_id))
            else:
                self.db._conn.execute("UPDATE push_subs SET fails = fails + 1, last_err_code = ? WHERE id = ?", (code, sub_id))

    def labels(self) -> list[str]:
        return [str(r["label"]) for r in self.db._q("SELECT label FROM push_subs")]

    # ------------------------------------------------------------ 보낸 푸시 (한 시간 셈)
    def log_push(self, *, type_: str, tag: str, sent: int, failed: int, now: float) -> None:
        with self.db._lock:
            self.db._conn.execute("INSERT INTO push_log (at, type, tag, sent, failed) VALUES (?,?,?,?,?)",
                                  (now, type_, tag, sent, failed))

    def pushes_since(self, start: float, *, exclude: tuple[str, ...] = ("test",)) -> list[float]:
        marks = ",".join("?" for _ in exclude) or "''"
        rows = self.db._q(f"SELECT at FROM push_log WHERE at >= ? AND sent > 0 AND type NOT IN ({marks}) ORDER BY at",
                          (start, *exclude))
        return [float(r["at"]) for r in rows]

    def last_push_ok(self) -> float | None:
        r = self.db._one("SELECT MAX(at) AS t FROM push_log WHERE sent > 0")
        return float(r["t"]) if r and r["t"] is not None else None

    # ------------------------------------------------------------ 정리
    def save_digest(self, did: str, *, at: float, title: str, lines: list[str], sections: list[dict[str, Any]]) -> None:
        with self.db._lock:
            self.db._conn.execute(
                "INSERT INTO owner_digests (id, at, title, lines, sections) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "at = excluded.at, title = excluded.title, lines = excluded.lines, sections = excluded.sections",
                (did, at, title, json.dumps(lines, ensure_ascii=False), json.dumps(sections, ensure_ascii=False)))

    def mark_digest(self, did: str, *, pushed: bool | None = None, kakao: bool | None = None) -> None:
        with self.db._lock:
            if pushed is not None:
                self.db._conn.execute("UPDATE owner_digests SET pushed = ? WHERE id = ?", (int(pushed), did))
            if kakao is not None:
                self.db._conn.execute("UPDATE owner_digests SET kakao = ? WHERE id = ?", (int(kakao), did))

    def digest(self, did: str) -> dict[str, Any] | None:
        r = self.db._one("SELECT * FROM owner_digests WHERE id = ?", (did,))
        if r is None:
            return None
        return {"id": r["id"], "at": r["at"], "title": r["title"], "lines": json.loads(r["lines"] or "[]"),
                "sections": json.loads(r["sections"] or "[]"), "pushed": bool(r["pushed"]), "kakao": bool(r["kakao"])}

    def latest_digest(self) -> str | None:
        r = self.db._one("SELECT id FROM owner_digests ORDER BY at DESC LIMIT 1")
        return str(r["id"]) if r else None

    # ------------------------------------------------------------ 스레드 답글
    def add_reply(self, *, reply_id: str, post_id: str, post_excerpt: str, reply_text: str, username: str, drafts: list[str],
                  open_url: str | None, now: float) -> bool:
        with self.db._lock:
            cur = self.db._conn.execute(
                "INSERT OR IGNORE INTO threads_replies (reply_id, post_id, post_excerpt, reply_text, username, drafts, open_url, at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (reply_id, post_id, post_excerpt, reply_text, username, json.dumps(drafts, ensure_ascii=False), open_url, now))
            return bool(cur.rowcount)

    def open_replies(self, now: float, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db._q("SELECT * FROM threads_replies WHERE done_at IS NULL AND at >= ? ORDER BY at DESC LIMIT ?",
                          (now - REPLIES_DAYS * 86400, limit))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["drafts"] = [str(x) for x in json.loads(d.get("drafts") or "[]")][:2]
            except ValueError:
                d["drafts"] = []
            out.append(d)
        return out

    def replies_done(self, now: float) -> int:
        with self.db._lock:
            return int(self.db._conn.execute("UPDATE threads_replies SET done_at = ? WHERE done_at IS NULL", (now,)).rowcount or 0)

    def prune_replies(self, now: float) -> int:
        with self.db._lock:
            return int(self.db._conn.execute("DELETE FROM threads_replies WHERE at < ?",
                                             (now - REPLIES_DAYS * 86400,)).rowcount or 0)
