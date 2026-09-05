"""SQLite 저장소: 상품/가격 이력, 발행 기록(중복 방지), 발행 대기열, 수집 실행 기록, 이벤트."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dealbot.models import Deal, PriceStats, Product
from dealbot.utils.timeutil import from_iso, to_iso, utcnow

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    url            TEXT NOT NULL,
    image_url      TEXT,
    category       TEXT,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL,
    last_price     INTEGER
);

CREATE TABLE IF NOT EXISTS price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL,
    source         TEXT NOT NULL,
    price          INTEGER NOT NULL,
    original_price INTEGER,
    discount_rate  REAL,
    observed_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_price_history_time ON price_history(observed_at);

CREATE TABLE IF NOT EXISTS posts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL,
    source         TEXT NOT NULL,
    price          INTEGER NOT NULL,
    channel_id     TEXT,
    message_id     INTEGER,
    affiliate_url  TEXT,
    dry_run        INTEGER NOT NULL DEFAULT 0,
    posted_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_product ON posts(product_id, posted_at);
CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(posted_at);

CREATE TABLE IF NOT EXISTS deal_queue (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL,
    source         TEXT NOT NULL,
    payload        TEXT NOT NULL,
    score          REAL NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending',
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
DROP INDEX IF EXISTS idx_queue_pending_product;
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_open_product
    ON deal_queue(product_id) WHERE status IN ('pending', 'awaiting_link');
CREATE INDEX IF NOT EXISTS idx_queue_status ON deal_queue(status, score DESC, created_at);

CREATE TABLE IF NOT EXISTS source_items (
    source         TEXT NOT NULL,
    external_id    TEXT NOT NULL,
    product_id     TEXT,
    first_seen_at  TEXT NOT NULL,
    PRIMARY KEY (source, external_id)
);

CREATE TABLE IF NOT EXISTS collector_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    collector      TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    collected      INTEGER NOT NULL DEFAULT 0,
    deals          INTEGER NOT NULL DEFAULT 0,
    queued         INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_collector ON collector_runs(collector, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    level          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    message        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS kv (
    key            TEXT PRIMARY KEY,
    value          TEXT
);

CREATE TABLE IF NOT EXISTS market_quotes (
    product_id     TEXT PRIMARY KEY,
    price          INTEGER NOT NULL,
    source         TEXT NOT NULL,
    title          TEXT,
    url            TEXT,
    checked_at     TEXT NOT NULL
);
"""


@dataclass(slots=True)
class QueueItem:
    id: int
    product_id: str
    source: str
    deal: Deal
    score: float
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime


@dataclass(slots=True)
class RunRecord:
    id: int
    collector: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    collected: int
    deals: int
    queued: int
    error: str | None


@dataclass(slots=True)
class PeriodSummary:
    since: datetime
    until: datetime
    runs: int = 0
    run_errors: int = 0
    collected: int = 0
    deals_found: int = 0
    queued: int = 0  # 새로 대기열에 들어간 딜 (deals_found 는 재확인마다 다시 세므로 더 큼)
    published: int = 0
    publish_failed: int = 0
    expired: int = 0
    skipped: int = 0
    errors: int = 0
    pending: int = 0
    awaiting: int = 0
    community: dict[str, dict[str, Any]] | None = None
    top_posts: list[dict[str, Any]] | None = None


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise RuntimeError(
                    f"데이터 폴더에 쓸 수 없습니다: {self.path.parent} ({e}). "
                    "볼륨 권한 문제일 수 있습니다. DEALBOT_DATA_DIR 을 쓰기 가능한 경로로 바꾸거나 "
                    "컨테이너를 root 로 실행하세요."
                ) from e
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(source_items)").fetchall()}
        for col, typ in (("url", "TEXT"), ("title", "TEXT"), ("recommend", "INTEGER"), ("views", "INTEGER"), ("updated_at", "TEXT"), ("comments", "INTEGER")):
            if col not in cols:
                self._conn.execute(f"ALTER TABLE source_items ADD COLUMN {col} {typ}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _q(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ----------------------------------------------------------- products
    def record_observation(self, product: Product, now: datetime | None = None) -> None:
        """상품 메타 업서트 + 가격 이력 1행 추가."""
        now = now or utcnow()
        ts = to_iso(now)
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO products (product_id, name, url, image_url, category, first_seen_at, last_seen_at, last_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    image_url = COALESCE(excluded.image_url, products.image_url),
                    category = COALESCE(excluded.category, products.category),
                    last_seen_at = excluded.last_seen_at,
                    last_price = excluded.last_price
                """,
                (
                    product.product_id,
                    product.name,
                    product.url,
                    product.image_url,
                    product.category,
                    ts,
                    ts,
                    product.price,
                ),
            )
            c.execute(
                """
                INSERT INTO price_history (product_id, source, price, original_price, discount_rate, observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    product.product_id,
                    product.source,
                    product.price,
                    product.original_price,
                    product.discount_rate,
                    ts,
                ),
            )

    def price_stats(self, product_id: str, days: int, now: datetime | None = None) -> PriceStats:
        """최근 N일 가격 통계 (현재 시점 이전 관측만)."""
        now = now or utcnow()
        since = to_iso(now - timedelta(days=days))
        row = self._one(
            """
            SELECT COUNT(*) AS cnt, AVG(price) AS avg_price, MIN(price) AS min_price,
                   MAX(price) AS max_price, MIN(observed_at) AS first_seen
            FROM price_history
            WHERE product_id = ? AND observed_at >= ? AND observed_at < ?
            """,
            (product_id, since, to_iso(now)),
        )
        last = self._one(
            "SELECT price FROM price_history WHERE product_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (product_id,),
        )
        if not row or not row["cnt"]:
            return PriceStats(last_price=last["price"] if last else None)
        return PriceStats(
            count=int(row["cnt"]),
            avg=float(row["avg_price"]),
            min=int(row["min_price"]),
            max=int(row["max_price"]),
            first_seen_at=from_iso(row["first_seen"]),
            last_price=last["price"] if last else None,
        )

    def last_observed_at(self, product_id: str) -> datetime | None:
        row = self._one(
            "SELECT observed_at FROM price_history WHERE product_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
            (product_id,),
        )
        return from_iso(row["observed_at"]) if row else None

    def product_count(self) -> int:
        row = self._one("SELECT COUNT(*) AS c FROM products")
        return int(row["c"]) if row else 0

    def price_history_count(self) -> int:
        row = self._one("SELECT COUNT(*) AS c FROM price_history")
        return int(row["c"]) if row else 0

    # -------------------------------------------------------------- posts
    def last_posted_at(self, product_id: str) -> datetime | None:
        row = self._one(
            "SELECT posted_at FROM posts WHERE product_id = ? ORDER BY posted_at DESC LIMIT 1",
            (product_id,),
        )
        return from_iso(row["posted_at"]) if row else None

    def posted_within(self, product_id: str, days: int, now: datetime | None = None) -> bool:
        last = self.last_posted_at(product_id)
        if last is None:
            return False
        now = now or utcnow()
        return last >= now - timedelta(days=days)

    def record_post(
        self,
        deal: Deal,
        *,
        channel_id: str | None,
        message_id: int | None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> None:
        now = now or utcnow()
        with self._tx() as c:
            c.execute(
                """
                INSERT INTO posts (product_id, source, price, channel_id, message_id, affiliate_url, dry_run, posted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deal.product.product_id,
                    deal.product.source,
                    deal.product.price,
                    channel_id,
                    message_id,
                    deal.affiliate_url,
                    1 if dry_run else 0,
                    to_iso(now),
                ),
            )

    def count_posts_since(self, since: datetime) -> int:
        row = self._one("SELECT COUNT(*) AS c FROM posts WHERE posted_at >= ?", (to_iso(since),))
        return int(row["c"]) if row else 0

    def last_post_time(self) -> datetime | None:
        row = self._one("SELECT posted_at FROM posts ORDER BY posted_at DESC LIMIT 1")
        return from_iso(row["posted_at"]) if row else None

    def recent_posts(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._q(
            """
            SELECT p.product_id, p.source, p.price, p.posted_at, p.affiliate_url, pr.name
            FROM posts p LEFT JOIN products pr ON pr.product_id = p.product_id
            ORDER BY p.posted_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- queue
    def enqueue(self, deal: Deal, *, score: float, now: datetime | None = None) -> bool:
        """대기열에 추가. 이미 열려 있으면(pending/awaiting_link) False."""
        now = now or utcnow()
        ts = to_iso(now)
        payload = json.dumps(deal.to_dict(), ensure_ascii=False)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO deal_queue (product_id, source, payload, score, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (deal.product.product_id, deal.product.source, payload, score, ts, ts),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def _row_to_queue_item(self, r: sqlite3.Row) -> QueueItem:
        return QueueItem(
            id=int(r["id"]),
            product_id=r["product_id"],
            source=r["source"],
            deal=Deal.from_dict(json.loads(r["payload"])),
            score=float(r["score"]),
            status=r["status"],
            attempts=int(r["attempts"]),
            last_error=r["last_error"],
            created_at=from_iso(r["created_at"]) or utcnow(),
        )

    def next_pending(self) -> QueueItem | None:
        row = self._one(
            "SELECT * FROM deal_queue WHERE status = 'pending' ORDER BY score DESC, created_at ASC LIMIT 1"
        )
        return self._row_to_queue_item(row) if row else None

    def pending_items(self, limit: int = 20) -> list[QueueItem]:
        rows = self._q(
            "SELECT * FROM deal_queue WHERE status = 'pending' ORDER BY score DESC, created_at ASC LIMIT ?",
            (limit,),
        )
        return [self._row_to_queue_item(r) for r in rows]

    def update_queue_item(
        self,
        item_id: int,
        *,
        status: str,
        error: str | None = None,
        increment_attempts: bool = False,
        deal: Deal | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or utcnow()
        with self._tx() as c:
            if deal is not None:
                c.execute(
                    "UPDATE deal_queue SET payload = ? WHERE id = ?",
                    (json.dumps(deal.to_dict(), ensure_ascii=False), item_id),
                )
            c.execute(
                """
                UPDATE deal_queue
                SET status = ?, last_error = ?, updated_at = ?,
                    attempts = attempts + ?
                WHERE id = ?
                """,
                (status, error, to_iso(now), 1 if increment_attempts else 0, item_id),
            )

    def last_published_item(self) -> QueueItem | None:
        row = self._one("SELECT * FROM deal_queue WHERE status = 'published' ORDER BY updated_at DESC, id DESC LIMIT 1")
        return self._row_to_queue_item(row) if row else None

    def get_queue_item(self, item_id: int) -> QueueItem | None:
        row = self._one("SELECT * FROM deal_queue WHERE id = ?", (item_id,))
        return self._row_to_queue_item(row) if row else None

    def awaiting_items(self, limit: int = 20) -> list[QueueItem]:
        rows = self._q(
            "SELECT * FROM deal_queue WHERE status = 'awaiting_link' ORDER BY created_at ASC LIMIT ?", (limit,)
        )
        return [self._row_to_queue_item(r) for r in rows]

    def set_queue_link(self, item_id: int, url: str, now: datetime | None = None) -> QueueItem | None:
        """관리자가 만든 제휴 링크를 붙이고 다시 pending 으로."""
        item = self.get_queue_item(item_id)
        if item is None:
            return None
        item.deal.affiliate_url = url
        self.update_queue_item(item_id, status="pending", error=None, deal=item.deal, now=now)
        return self.get_queue_item(item_id)

    def expire_queue(
        self,
        older_than: datetime,
        now: datetime | None = None,
        *,
        awaiting_older_than: datetime | None = None,
    ) -> int:
        now = now or utcnow()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE deal_queue SET status = 'expired', updated_at = ? WHERE status = 'pending' AND created_at < ?",
                (to_iso(now), to_iso(older_than)),
            )
            n = cur.rowcount
            if awaiting_older_than is not None:
                cur2 = self._conn.execute(
                    "UPDATE deal_queue SET status = 'expired', updated_at = ? WHERE status = 'awaiting_link' AND created_at < ?",
                    (to_iso(now), to_iso(awaiting_older_than)),
                )
                n += cur2.rowcount
            return n

    def queue_counts(self) -> dict[str, int]:
        rows = self._q("SELECT status, COUNT(*) AS c FROM deal_queue GROUP BY status")
        return {r["status"]: int(r["c"]) for r in rows}

    # ------------------------------------------------------- source items
    def is_seen(self, source: str, external_id: str) -> bool:
        return self._one(
            "SELECT 1 FROM source_items WHERE source = ? AND external_id = ?", (source, external_id)
        ) is not None

    def mark_seen(
        self,
        source: str,
        external_id: str,
        product_id: str | None = None,
        now: datetime | None = None,
        *,
        url: str | None = None,
        title: str | None = None,
        recommend: int | None = None,
        views: int | None = None,
        comments: int | None = None,
    ) -> None:
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO source_items (source, external_id, product_id, first_seen_at, url, title, recommend, views, comments, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source, external_id, product_id, to_iso(now), url, title, recommend, views, comments, to_iso(now)),
            )

    def touch_seen(
        self,
        source: str,
        external_id: str,
        *,
        recommend: int | None,
        views: int | None,
        comments: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """이미 본 글의 추천/조회/댓글 수를 최신값으로 갱신 (통계용)."""
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                "UPDATE source_items SET recommend = COALESCE(?, recommend), views = COALESCE(?, views), comments = COALESCE(?, comments), updated_at = ? WHERE source = ? AND external_id = ?",
                (recommend, views, comments, to_iso(now), source, external_id),
            )

    def hot_items(self, since: datetime, *, min_recommend: int = 5, limit: int = 30) -> list[dict[str, Any]]:
        rows = self._q(
            """
            SELECT source, external_id, product_id, title, url, recommend, views, comments, first_seen_at
            FROM source_items WHERE first_seen_at >= ? AND recommend IS NOT NULL AND recommend >= ?
            ORDER BY recommend DESC, first_seen_at DESC LIMIT ?
            """,
            (to_iso(since), min_recommend, limit),
        )
        return [dict(r) for r in rows]

    def find_items(self, keyword: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._q(
            """
            SELECT source, external_id, product_id, title, url, recommend, views, comments, first_seen_at
            FROM source_items WHERE title LIKE ? ORDER BY first_seen_at DESC LIMIT ?
            """,
            (f"%{keyword}%", limit),
        )
        return [dict(r) for r in rows]

    def community_stats(self, since: datetime, thresholds: tuple[int, ...] = (1, 3, 5, 10, 20)) -> dict[str, dict[str, Any]]:
        """소스별: 기간 내 처음 본 글 수와 추천 N개 이상 글 수 (규칙 (c) 임계값이 필터 구실을 하는지 보기 위함)."""
        rows = self._q(
            "SELECT source, recommend, views, comments FROM source_items WHERE first_seen_at >= ? AND recommend IS NOT NULL",
            (to_iso(since),),
        )
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            d = out.setdefault(r["source"], {"posts": 0, "rec_ge": {t: 0 for t in thresholds}, "views_ge_500": 0, "comments_ge_3": 0})
            d["posts"] += 1
            for t in thresholds:
                if int(r["recommend"]) >= t:
                    d["rec_ge"][t] += 1
            if r["views"] is not None and int(r["views"]) >= 500:
                d["views_ge_500"] += 1
            if r["comments"] is not None and int(r["comments"]) >= 3:
                d["comments_ge_3"] += 1
        return out

    def seen_item(self, source: str, external_id: str) -> tuple[str | None, str | None] | None:
        """(product_id, url) 또는 None."""
        row = self._one(
            "SELECT product_id, url FROM source_items WHERE source = ? AND external_id = ?", (source, external_id)
        )
        return (row["product_id"], row["url"]) if row else None

    def seen_product_id(self, source: str, external_id: str) -> str | None:
        row = self._one(
            "SELECT product_id FROM source_items WHERE source = ? AND external_id = ?", (source, external_id)
        )
        return row["product_id"] if row else None

    def product_url(self, product_id: str) -> str | None:
        row = self._one("SELECT url FROM products WHERE product_id = ?", (product_id,))
        return row["url"] if row else None

    # ------------------------------------------------------- collector runs
    def start_run(self, collector: str, now: datetime | None = None) -> int:
        now = now or utcnow()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO collector_runs (collector, started_at, status) VALUES (?, ?, 'running')",
                (collector, to_iso(now)),
            )
            return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        collected: int = 0,
        deals: int = 0,
        queued: int = 0,
        error: str | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                """
                UPDATE collector_runs SET finished_at = ?, status = ?, collected = ?, deals = ?, queued = ?, error = ?
                WHERE id = ?
                """,
                (to_iso(now), status, collected, deals, queued, error, run_id),
            )

    def _row_to_run(self, r: sqlite3.Row) -> RunRecord:
        return RunRecord(
            id=int(r["id"]),
            collector=r["collector"],
            started_at=from_iso(r["started_at"]) or utcnow(),
            finished_at=from_iso(r["finished_at"]),
            status=r["status"],
            collected=int(r["collected"]),
            deals=int(r["deals"]),
            queued=int(r["queued"]),
            error=r["error"],
        )

    def last_run(self, collector: str) -> RunRecord | None:
        row = self._one(
            "SELECT * FROM collector_runs WHERE collector = ? ORDER BY started_at DESC, id DESC LIMIT 1",
            (collector,),
        )
        return self._row_to_run(row) if row else None

    def recent_runs(self, limit: int = 20) -> list[RunRecord]:
        rows = self._q("SELECT * FROM collector_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
        return [self._row_to_run(r) for r in rows]

    # ------------------------------------------------------------- events
    def log_event(self, level: str, kind: str, message: str, now: datetime | None = None) -> None:
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, level, kind, message) VALUES (?, ?, ?, ?)",
                (to_iso(now), level.upper(), kind, message[:2000]),
            )

    def recent_events(self, limit: int = 10, level: str | None = None) -> list[dict[str, Any]]:
        if level:
            rows = self._q(
                "SELECT * FROM events WHERE level = ? ORDER BY ts DESC, id DESC LIMIT ?", (level.upper(), limit)
            )
        else:
            rows = self._q("SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def last_error(self) -> dict[str, Any] | None:
        rows = self.recent_events(1, level="ERROR")
        return rows[0] if rows else None

    # ------------------------------------------------------ market quotes
    def get_market_quote(self, product_id: str, max_age_hours: int, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or utcnow()
        row = self._one("SELECT * FROM market_quotes WHERE product_id = ?", (product_id,))
        if not row:
            return None
        checked = from_iso(row["checked_at"])
        if checked is None or checked < now - timedelta(hours=max_age_hours):
            return None
        return dict(row)

    def set_market_quote(self, product_id: str, *, price: int, source: str, title: str | None, url: str | None, now: datetime | None = None) -> None:
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO market_quotes (product_id, price, source, title, url, checked_at) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET price = excluded.price, source = excluded.source,
                    title = excluded.title, url = excluded.url, checked_at = excluded.checked_at
                """,
                (product_id, price, source, title, url, to_iso(now)),
            )

    # ----------------------------------------------------------------- kv
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------ summary
    def summary(self, since: datetime, until: datetime | None = None) -> PeriodSummary:
        until = until or utcnow()
        s, u = to_iso(since), to_iso(until)
        out = PeriodSummary(since=since, until=until)

        row = self._one(
            """
            SELECT COUNT(*) AS runs, SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errs,
                   COALESCE(SUM(collected),0) AS collected, COALESCE(SUM(deals),0) AS deals,
                   COALESCE(SUM(queued),0) AS queued
            FROM collector_runs WHERE started_at >= ? AND started_at <= ?
            """,
            (s, u),
        )
        if row:
            out.runs = int(row["runs"] or 0)
            out.run_errors = int(row["errs"] or 0)
            out.collected = int(row["collected"] or 0)
            out.deals_found = int(row["deals"] or 0)
            out.queued = int(row["queued"] or 0)

        row = self._one("SELECT COUNT(*) AS c FROM posts WHERE posted_at >= ? AND posted_at <= ?", (s, u))
        out.published = int(row["c"]) if row else 0

        rows = self._q(
            "SELECT status, COUNT(*) AS c FROM deal_queue WHERE updated_at >= ? AND updated_at <= ? GROUP BY status",
            (s, u),
        )
        by_status = {r["status"]: int(r["c"]) for r in rows}
        out.publish_failed = by_status.get("failed", 0)
        out.expired = by_status.get("expired", 0)
        out.skipped = by_status.get("skipped", 0)
        counts = self.queue_counts()
        out.pending = counts.get("pending", 0)
        out.awaiting = counts.get("awaiting_link", 0)

        row = self._one(
            "SELECT COUNT(*) AS c FROM events WHERE level = 'ERROR' AND ts >= ? AND ts <= ?", (s, u)
        )
        out.errors = int(row["c"]) if row else 0

        out.community = self.community_stats(since) or None
        out.top_posts = [
            dict(r)
            for r in self._q(
                """
                SELECT p.product_id, p.price, p.posted_at, pr.name, p.affiliate_url
                FROM posts p LEFT JOIN products pr ON pr.product_id = p.product_id
                WHERE p.posted_at >= ? AND p.posted_at <= ?
                ORDER BY p.posted_at DESC LIMIT 5
                """,
                (s, u),
            )
        ]
        return out

    # -------------------------------------------------------------- prune
    def prune(self, *, price_history_days: int, events_days: int, now: datetime | None = None) -> dict[str, int]:
        now = now or utcnow()
        with self._tx() as c:
            ph = c.execute(
                "DELETE FROM price_history WHERE observed_at < ?",
                (to_iso(now - timedelta(days=price_history_days)),),
            ).rowcount
            ev = c.execute(
                "DELETE FROM events WHERE ts < ?", (to_iso(now - timedelta(days=events_days)),)
            ).rowcount
            q = c.execute(
                "DELETE FROM deal_queue WHERE status != 'pending' AND updated_at < ?",
                (to_iso(now - timedelta(days=events_days)),),
            ).rowcount
            runs = c.execute(
                "DELETE FROM collector_runs WHERE started_at < ?",
                (to_iso(now - timedelta(days=events_days)),),
            ).rowcount
        return {"price_history": ph, "events": ev, "queue": q, "runs": runs}

    def db_size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0
