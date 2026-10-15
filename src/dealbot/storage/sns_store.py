"""SNS(스레드·인스타)·채널 성장 기록 — Database 에 섞어 쓰는 표들 (주인 결정 10/15).

- threads_jobs: 스레드 대기열. 텔레그램에 올린 딜·정보 글·투표 글을 넣고, 리듬(07~24시·45분 간격·하루 딜 10개)이 허락할 때 올린다.
  올린 글의 스레드 글 id 도 여기 남아 답글 모으기·품절 업데이트 답글(글마다 한 번)이 쓴다.
- threads_replies_seen: 관리자 챗으로 이미 보낸 답글 (같은 답글을 두 번 안 보냄). 답글 글자는 남기지 않는다.
- threads_polls: 투표 글과 결과 (일요일 텔레그램 'TOP5 + 투표 결과' 에 씀).
- sns_log: 인스타·스레드 발행 결과 (올림·건너뜀·실패와 이유) — 24시간 0개 알림의 이유 요약.
- invite_joins: 텔레그램 채널 초대 링크별 가입 수 (날짜·링크 이름·수만 — 누가 들어왔는지는 남기지 않음).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from dealbot.utils.timeutil import from_iso, to_iso, utcnow

SNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS threads_jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT NOT NULL,              -- deal | info | poll
    product_id       TEXT NOT NULL,
    queue_id         INTEGER,                    -- 딜 대기열 번호 (텔레그램 글 #번호)
    payload          TEXT NOT NULL,              -- JSON
    score            REAL NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'waiting',   -- waiting | posted | skipped | failed
    reason           TEXT,
    post_id          TEXT,                       -- 올린 스레드 글 id
    dry_run          INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    posted_at        TEXT,
    soldout_reply_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_jobs_status ON threads_jobs(status, score DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_threads_jobs_posted ON threads_jobs(posted_at);
CREATE INDEX IF NOT EXISTS idx_threads_jobs_product ON threads_jobs(product_id, status);

CREATE TABLE IF NOT EXISTS threads_replies_seen (
    reply_id   TEXT PRIMARY KEY,
    post_id    TEXT NOT NULL,
    seen_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads_polls (
    post_id      TEXT PRIMARY KEY,
    question     TEXT NOT NULL,
    options      TEXT NOT NULL,
    posted_at    TEXT NOT NULL,
    results      TEXT,
    results_at   TEXT,
    reported_at  TEXT
);

CREATE TABLE IF NOT EXISTS sns_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    channel     TEXT NOT NULL,      -- threads | instagram
    product_id  TEXT,
    outcome     TEXT NOT NULL,      -- posted | skipped | failed
    reason      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sns_log ON sns_log(channel, at);

CREATE TABLE IF NOT EXISTS invite_joins (
    day    TEXT NOT NULL,
    link   TEXT NOT NULL,
    joins  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, link)
);
"""


@dataclass(slots=True)
class ThreadsJob:
    id: int
    kind: str
    product_id: str
    queue_id: int | None
    payload: dict[str, Any]
    score: float
    status: str
    reason: str | None
    post_id: str | None
    dry_run: bool
    created_at: datetime
    posted_at: datetime | None
    soldout_reply_at: datetime | None


class SnsStoreMixin:
    """Database 에 섞는 메서드들 (self._conn · self._lock · self._q · self._one 은 Database 것)."""

    _conn: sqlite3.Connection
    _lock: threading.RLock

    if TYPE_CHECKING:  # Database 가 구현
        def _q(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]: ...

        def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None: ...

    def _init_sns(self) -> None:
        self._conn.executescript(SNS_SCHEMA)

    # ------------------------------------------------------------ 스레드 대기열
    @staticmethod
    def _job(r: sqlite3.Row) -> ThreadsJob:
        try:
            payload = json.loads(r["payload"] or "{}")
        except ValueError:
            payload = {}
        return ThreadsJob(
            id=int(r["id"]), kind=r["kind"], product_id=r["product_id"], queue_id=r["queue_id"],
            payload=payload if isinstance(payload, dict) else {}, score=float(r["score"]), status=r["status"],
            reason=r["reason"], post_id=r["post_id"], dry_run=bool(r["dry_run"]),
            created_at=from_iso(r["created_at"]) or utcnow(), posted_at=from_iso(r["posted_at"]),
            soldout_reply_at=from_iso(r["soldout_reply_at"]),
        )

    def threads_enqueue(self, kind: str, product_id: str, payload: dict[str, Any], *, queue_id: int | None = None,
                        score: float = 0.0, now: datetime | None = None) -> int | None:
        """스레드 대기열에 넣는다. 같은 상품이 이미 기다리고 있으면 넣지 않고 None."""
        ts = to_iso(now or utcnow())
        with self._lock:
            if self._one("SELECT id FROM threads_jobs WHERE product_id = ? AND status = 'waiting'", (product_id,)):
                return None
            cur = self._conn.execute(
                "INSERT INTO threads_jobs (kind, product_id, queue_id, payload, score, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?)",
                (kind, product_id, queue_id, json.dumps(payload, ensure_ascii=False, default=str), score, ts, ts),
            )
            return int(cur.lastrowid or 0)

    def threads_waiting(self, limit: int = 50) -> list[ThreadsJob]:
        rows = self._q("SELECT * FROM threads_jobs WHERE status = 'waiting' ORDER BY score DESC, created_at ASC LIMIT ?", (limit,))
        return [self._job(r) for r in rows]

    def threads_job(self, job_id: int) -> ThreadsJob | None:
        r = self._one("SELECT * FROM threads_jobs WHERE id = ?", (job_id,))
        return self._job(r) if r else None

    def threads_update(self, job_id: int, *, status: str, reason: str | None = None, post_id: str | None = None,
                       dry_run: bool = False, now: datetime | None = None) -> None:
        ts = to_iso(now or utcnow())
        posted = ts if status == "posted" else None
        with self._lock:
            self._conn.execute(
                "UPDATE threads_jobs SET status = ?, reason = ?, post_id = COALESCE(?, post_id), dry_run = ?, updated_at = ?, "
                "posted_at = COALESCE(?, posted_at) WHERE id = ?",
                (status, reason, post_id, 1 if dry_run else 0, ts, posted, job_id),
            )

    def threads_expire(self, older_than: datetime, now: datetime | None = None) -> list[str]:
        """오래 기다린 글을 내린다 (값·재고가 바뀌었을 수 있음). 내린 글의 상품 키 목록."""
        with self._lock:
            rows = self._q("SELECT id, product_id FROM threads_jobs WHERE status = 'waiting' AND created_at < ?", (to_iso(older_than),))
            if rows:
                self._conn.execute(
                    f"UPDATE threads_jobs SET status = 'skipped', reason = 'waited too long', updated_at = ? "
                    f"WHERE id IN ({','.join('?' for _ in rows)})",
                    (to_iso(now or utcnow()), *(int(r["id"]) for r in rows)),
                )
            return [str(r["product_id"]) for r in rows]

    def threads_skip_product(self, product_id: str, reason: str, now: datetime | None = None) -> int:
        """그 상품의 기다리는 스레드 글을 내린다 (품절 등)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE threads_jobs SET status = 'skipped', reason = ?, updated_at = ? WHERE product_id = ? AND status = 'waiting'",
                (reason, to_iso(now or utcnow()), product_id),
            )
            return cur.rowcount or 0

    def threads_last_posted_at(self) -> datetime | None:
        r = self._one("SELECT MAX(posted_at) AS t FROM threads_jobs WHERE status = 'posted'")
        return from_iso(r["t"]) if r and r["t"] else None

    def threads_posted_count(self, since: datetime, *, kind: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM threads_jobs WHERE status = 'posted' AND posted_at >= ?"
        params: tuple[Any, ...] = (to_iso(since),)
        if kind:
            sql += " AND kind = ?"
            params += (kind,)
        r = self._one(sql, params)
        return int(r["n"]) if r else 0

    def threads_posted_since(self, since: datetime, *, kind: str | None = None, real_only: bool = True) -> list[ThreadsJob]:
        """이때부터 올린 스레드 글 (최근 것부터). real_only: 연습 모드 글·글 id 없는 것은 뺌."""
        sql = "SELECT * FROM threads_jobs WHERE status = 'posted' AND posted_at >= ?"
        params: tuple[Any, ...] = (to_iso(since),)
        if kind:
            sql += " AND kind = ?"
            params += (kind,)
        if real_only:
            sql += " AND dry_run = 0 AND post_id IS NOT NULL AND post_id != ''"
        rows = self._q(sql + " ORDER BY posted_at DESC", params)
        return [self._job(r) for r in rows]

    def threads_posts_for_product(self, product_id: str, since: datetime) -> list[ThreadsJob]:
        rows = self._q(
            "SELECT * FROM threads_jobs WHERE product_id = ? AND status = 'posted' AND posted_at >= ? AND dry_run = 0 "
            "AND post_id IS NOT NULL AND post_id != '' ORDER BY posted_at DESC",
            (product_id, to_iso(since)),
        )
        return [self._job(r) for r in rows]

    def threads_mark_soldout_reply(self, job_id: int, now: datetime | None = None) -> bool:
        """품절 업데이트 답글을 달았다고 적는다. 이미 달았으면 False (글마다 한 번)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE threads_jobs SET soldout_reply_at = ? WHERE id = ? AND soldout_reply_at IS NULL",
                (to_iso(now or utcnow()), job_id),
            )
            return (cur.rowcount or 0) > 0

    def threads_unmark_soldout_reply(self, job_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE threads_jobs SET soldout_reply_at = NULL WHERE id = ?", (job_id,))

    # ------------------------------------------------------------ 답글
    def reply_seen(self, reply_id: str) -> bool:
        return self._one("SELECT 1 FROM threads_replies_seen WHERE reply_id = ?", (reply_id,)) is not None

    def mark_reply_seen(self, reply_id: str, post_id: str, now: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO threads_replies_seen (reply_id, post_id, seen_at) VALUES (?, ?, ?)",
                (reply_id, post_id, to_iso(now or utcnow())),
            )

    # ------------------------------------------------------------ 투표
    def poll_add(self, post_id: str, question: str, options: list[str], now: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO threads_polls (post_id, question, options, posted_at) VALUES (?, ?, ?, ?)",
                (post_id, question, json.dumps(options, ensure_ascii=False), to_iso(now or utcnow())),
            )

    def polls_since(self, since: datetime) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM threads_polls WHERE posted_at >= ? ORDER BY posted_at DESC", (to_iso(since),))
        out = []
        for r in rows:
            d = dict(r)
            for k in ("options", "results"):
                try:
                    d[k] = json.loads(d[k]) if d[k] else None
                except ValueError:
                    d[k] = None
            out.append(d)
        return out

    def poll_set_results(self, post_id: str, results: dict[str, Any], now: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE threads_polls SET results = ?, results_at = ? WHERE post_id = ?",
                (json.dumps(results, ensure_ascii=False), to_iso(now or utcnow()), post_id),
            )

    def poll_mark_reported(self, post_id: str, now: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute("UPDATE threads_polls SET reported_at = ? WHERE post_id = ?", (to_iso(now or utcnow()), post_id))

    # ------------------------------------------------------------ 발행 결과 (인스타·스레드)
    def sns_log_add(self, channel: str, outcome: str, *, product_id: str | None = None, reason: str | None = None,
                    now: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO sns_log (at, channel, product_id, outcome, reason) VALUES (?, ?, ?, ?, ?)",
                (to_iso(now or utcnow()), channel, product_id, outcome, (reason or "")[:300] or None),
            )

    def sns_log_since(self, channel: str, since: datetime) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM sns_log WHERE channel = ? AND at >= ? ORDER BY at DESC", (channel, to_iso(since)))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 초대 링크별 가입
    def invite_join_add(self, day: str, link: str, n: int = 1) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO invite_joins (day, link, joins) VALUES (?, ?, ?) "
                "ON CONFLICT(day, link) DO UPDATE SET joins = joins + excluded.joins",
                (day, link, n),
            )

    def invite_joins_since(self, since_day: str) -> dict[str, int]:
        rows = self._q("SELECT link, SUM(joins) AS n FROM invite_joins WHERE day >= ? GROUP BY link", (since_day,))
        return {r["link"]: int(r["n"] or 0) for r in rows}

    # ------------------------------------------------------------ 정리
    def prune_sns(self, cutoff: datetime) -> int:
        ts = to_iso(cutoff)
        n = 0
        with self._lock:
            for sql in (
                "DELETE FROM threads_jobs WHERE status != 'waiting' AND updated_at < ?",
                "DELETE FROM threads_replies_seen WHERE seen_at < ?",
                "DELETE FROM sns_log WHERE at < ?",
            ):
                n += self._conn.execute(sql, (ts,)).rowcount or 0
        return n
