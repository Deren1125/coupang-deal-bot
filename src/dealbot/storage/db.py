"""SQLite 저장소: 상품/가격 이력, 발행 기록(중복 방지), 발행 대기열, 수집 실행 기록, 이벤트."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from dealbot.models import Deal, PriceStats, Product
from dealbot.utils.timeutil import from_iso, to_iso, utcnow

log = logging.getLogger(__name__)

# 채널에 보냈지만 텔레그램 답이 없던(시간 초과) 발행 실패의 last_error 머리말 — 올라갔을 수 있어 다시 보내지 않은 글
MAYBE_POSTED = "maybe posted"
# 주인의 링크를 기다리는 글 (링크 요청 카톡·텔레그램 링크 요청·고정 공지 /pending·대시보드 '링크 기다리는 딜' 이 모두 이 상태로 셈)
LINK_WAITING = ("awaiting_link",)

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
DROP INDEX IF EXISTS idx_queue_open_product;
DROP INDEX IF EXISTS idx_queue_open_product_v2;
CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_open_product_v3
    ON deal_queue(product_id) WHERE status IN ('pending', 'awaiting_link', 'awaiting_approval', 'awaiting_autolink');
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

-- 관리자 챗에 이 봇(딜봇)이 보낸 메시지 기록. 같은 챗에 Blog-Auto 도 같은 봇 토큰으로 글을 보내서,
-- 답장 대상의 #번호가 딜 대기열 번호인지 블로그 글 번호인지 이 기록으로 가린다 (말 명령)
CREATE TABLE IF NOT EXISTS admin_msgs (
    message_id     INTEGER PRIMARY KEY,
    kind           TEXT,
    queue_id       INTEGER,
    at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_quotes (
    product_id     TEXT PRIMARY KEY,
    price          INTEGER NOT NULL,
    source         TEXT NOT NULL,
    title          TEXT,
    url            TEXT,
    checked_at     TEXT NOT NULL,
    meta           TEXT
);

-- 채널 대시보드 숫자 (구독자·이웃·조회수 등). 소스·지표·날짜마다 한 값 — 같은 날 다시 읽으면 나중 값이 이긴다.
-- day 는 app.timezone 기준 YYYY-MM-DD, origin 은 auto(봇이 읽음) | manual(주인이 입력)
CREATE TABLE IF NOT EXISTS metrics (
    source         TEXT NOT NULL,
    metric         TEXT NOT NULL,
    day            TEXT NOT NULL,
    value          REAL NOT NULL,
    read_at        TEXT NOT NULL,
    origin         TEXT NOT NULL DEFAULT 'auto',
    PRIMARY KEY (source, metric, day)
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
        if self.kv_get("db_created_at") is None:
            # 기존 DB 를 새 버전으로 처음 열 때는 가장 오래된 기록 시각을 쓴다 (기록이 언제부터 쌓였는지 보여주기 위함)
            row = self._conn.execute("SELECT MIN(first_seen_at) AS t FROM products").fetchone()
            self.kv_set("db_created_at", (row["t"] if row and row["t"] else None) or to_iso(utcnow()))
        if self.kv_get("admin_msgs_since") is None:
            self._seed_admin_msgs()

    def _migrate(self) -> None:
        adds = (
            ("source_items", (("url", "TEXT"), ("title", "TEXT"), ("recommend", "INTEGER"), ("views", "INTEGER"),
                              ("updated_at", "TEXT"), ("comments", "INTEGER"))),
            # 대조한 상품의 분류(식품 판정 — 기억한 값도 처음 찾은 값과 같게)·네이버 탈락 기준 등 (JSON)
            ("market_quotes", (("meta", "TEXT"),)),
        )
        for table, columns in adds:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for col, typ in columns:
                if col in cols:
                    continue
                try:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError as e:
                    # 봇 서비스와 check 명령이 동시에 처음 열면 다른 쪽이 먼저 칸을 만들 수 있음
                    if "duplicate column" not in str(e).lower():
                        raise

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
    def last_posted_at(self, product_id: str, *, include_dry_run: bool = True) -> datetime | None:
        # 연습 모드에서 만든 미리보기도 posts 에 남는다. 실제 발행 때는 그 기록 때문에
        # "이미 올렸다" 고 판단해 진짜 발행을 건너뛰면 안 되므로 걸러낼 수 있게 한다.
        where = "" if include_dry_run else " AND dry_run = 0"
        row = self._one(
            f"SELECT posted_at FROM posts WHERE product_id = ?{where} ORDER BY posted_at DESC LIMIT 1",
            (product_id,),
        )
        return from_iso(row["posted_at"]) if row else None

    def maybe_posted_within(self, product_id: str, days: int, now: datetime | None = None) -> bool:
        """최근 days 안에 채널에 보냈지만 텔레그램 답을 못 받아(시간 초과) 올라갔는지 모르는 같은 상품 글이 있나.
        다시 줄 세우면 같은 딜이 두 번 올라갈 수 있다. 주인이 /link 로 다시 올리면 그 글은 pending 이 되어 여기서 빠진다."""
        now = now or utcnow()
        return self._one(
            "SELECT 1 FROM deal_queue WHERE product_id = ? AND status = 'failed' AND last_error LIKE ? AND updated_at >= ? LIMIT 1",
            (product_id, f"{MAYBE_POSTED}%", to_iso(now - timedelta(days=days))),
        ) is not None

    def maybe_posted_items(self, since: datetime, limit: int = 20) -> list[tuple[QueueItem, datetime]]:
        """since 뒤에 '채널에 올라갔는지 모름'(보낸 뒤 텔레그램 답 시간 초과)이 된 글과 그 시각 — 오래된 것부터.
        주인이 /posted·/link·/skip 하면 상태가 바뀌어 빠진다 (대시보드 '지금 할 일' 의 '채널 확인')."""
        rows = self._q(
            "SELECT * FROM deal_queue WHERE status = 'failed' AND last_error LIKE ? AND updated_at >= ? "
            "ORDER BY updated_at ASC, id ASC LIMIT ?",
            (f"{MAYBE_POSTED}%", to_iso(since), limit),
        )
        return [(self._row_to_queue_item(r), from_iso(r["updated_at"]) or utcnow()) for r in rows]

    def posted_within(self, product_id: str, days: int, now: datetime | None = None, *, include_dry_run: bool = True) -> bool:
        last = self.last_posted_at(product_id, include_dry_run=include_dry_run)
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

    def count_posts_since(self, since: datetime, *, product_prefix: str | None = None) -> int:
        if product_prefix:
            row = self._one(
                "SELECT COUNT(*) AS c FROM posts WHERE posted_at >= ? AND product_id LIKE ? AND dry_run = 0",
                (to_iso(since), product_prefix.replace("%", "") + "%"),
            )
        else:
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
        """대기열에 추가. 이미 열려 있으면(pending/awaiting_link/awaiting_approval/awaiting_autolink) False."""
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

    def add_held(self, deal: Deal, *, score: float, error: str, now: datetime | None = None) -> int:
        """가격이 맞지 않아 보류(held)한 딜을 기록하고 번호를 돌려준다. 링크 요청·발행은 하지 않는다 (열린 글로 세지 않음)."""
        now = now or utcnow()
        ts = to_iso(now)
        payload = json.dumps(deal.to_dict(), ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO deal_queue (product_id, source, payload, score, status, last_error, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'held', ?, ?, ?)
                """,
                (deal.product.product_id, deal.product.source, payload, score, error, ts, ts),
            )
            return int(cur.lastrowid or 0)

    def held_since(self, product_id: str, since: datetime, *, price: int | None = None) -> Deal | None:
        """since 이후 가격 대조로 보류한 같은 상품의 마지막 글 (같은 글을 다시 볼 때 메모를 또 보내지 않으려고).
        price 를 주면 그 값(제목 가격)으로 보류한 글만 — 같은 상품의 값이 다른 두 글이 번갈아 보류돼도 같은 글을 또 알리지 않게."""
        rows = self._q(
            "SELECT payload FROM deal_queue WHERE product_id = ? AND status = 'held' AND updated_at >= ? ORDER BY id DESC",
            (product_id, to_iso(since)),
        )
        for row in rows:
            deal = Deal.from_dict(json.loads(row["payload"]))
            if price is None or deal.product.price == price:
                return deal
        return None

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
        reset_created: bool = False,
    ) -> None:
        """reset_created: 관리자가 링크를 붙이거나 승인해서 다시 pending 이 될 때 유효 시간을 새로 센다."""
        now = now or utcnow()
        with self._tx() as c:
            if deal is not None:
                c.execute(
                    "UPDATE deal_queue SET payload = ? WHERE id = ?",
                    (json.dumps(deal.to_dict(), ensure_ascii=False), item_id),
                )
            if reset_created:
                c.execute("UPDATE deal_queue SET created_at = ? WHERE id = ?", (to_iso(now), item_id))
            c.execute(
                """
                UPDATE deal_queue
                SET status = ?, last_error = ?, updated_at = ?,
                    attempts = attempts + ?
                WHERE id = ?
                """,
                (status, error, to_iso(now), 1 if increment_attempts else 0, item_id),
            )

    def recent_post_names(self, days: int, now: datetime | None = None, *, include_dry_run: bool = True) -> list[dict[str, Any]]:
        """최근 며칠 안에 올린 글의 상품명·가격 (이름이 비슷한 중복을 거르는 용도).
        정보 글(info:…)은 products 표에 없어서 대기열에 남은 이름을 쓴다 — 없으면 이름이 비어 중복 검사를 그냥 통과했다
        (10/12 「토너세이브 프린터 특가」가 뽐뿌 글 번호만 다르게 두 번 올라감)."""
        now = now or utcnow()
        where = "" if include_dry_run else " AND p.dry_run = 0"
        rows = self._q(
            f"""
            SELECT p.product_id, p.price, p.posted_at,
                   COALESCE(pr.name, (SELECT json_extract(q.payload, '$.product.name') FROM deal_queue q
                                      WHERE q.product_id = p.product_id ORDER BY q.id DESC LIMIT 1)) AS name
            FROM posts p LEFT JOIN products pr ON pr.product_id = p.product_id
            WHERE p.posted_at >= ?{where}
            ORDER BY p.posted_at DESC LIMIT 500
            """,
            (to_iso(now - timedelta(days=days)),),
        )
        return [dict(r) for r in rows if r["name"]]

    def open_queue_names(self) -> list[dict[str, Any]]:
        """아직 처리 중인(대기·링크 대기·확인 대기) 글의 상품명·가격."""
        rows = self._q(
            "SELECT id, product_id, payload, status FROM deal_queue "
            "WHERE status IN ('pending', 'awaiting_link', 'awaiting_approval', 'awaiting_autolink')"
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                prod = json.loads(r["payload"]).get("product") or {}
            except (ValueError, TypeError):
                continue
            out.append({"queue_id": int(r["id"]), "product_id": r["product_id"], "name": prod.get("name") or "", "price": prod.get("price") or 0, "status": r["status"]})
        return out

    def published_items_between(self, start: datetime, end: datetime, *, include_dry_run: bool = False) -> list[QueueItem]:
        """그 기간에 채널에 올라간 딜 (대기열 payload 로 상품·링크까지). 기본은 실제 발행만."""
        sql = "SELECT q.* FROM deal_queue q WHERE q.status = 'published' AND q.updated_at >= ? AND q.updated_at < ?"
        params: list[Any] = [to_iso(start), to_iso(end)]
        if not include_dry_run:
            sql += " AND EXISTS (SELECT 1 FROM posts p WHERE p.product_id = q.product_id AND p.dry_run = 0 AND p.posted_at >= ? AND p.posted_at < ?)"
            params += [to_iso(start), to_iso(end)]
        sql += " ORDER BY q.updated_at ASC"
        return [self._row_to_queue_item(r) for r in self._q(sql, tuple(params))]

    def last_published_item(self) -> QueueItem | None:
        row = self._one("SELECT * FROM deal_queue WHERE status = 'published' ORDER BY updated_at DESC, id DESC LIMIT 1")
        return self._row_to_queue_item(row) if row else None

    def get_queue_item(self, item_id: int) -> QueueItem | None:
        row = self._one("SELECT * FROM deal_queue WHERE id = ?", (item_id,))
        return self._row_to_queue_item(row) if row else None

    def items_for_product(self, product_id: str, statuses: tuple[str, ...]) -> list[QueueItem]:
        marks = ",".join("?" for _ in statuses)
        rows = self._q(
            f"SELECT * FROM deal_queue WHERE product_id = ? AND status IN ({marks}) ORDER BY id DESC",
            (product_id, *statuses),
        )
        return [self._row_to_queue_item(r) for r in rows]

    def latest_post(self, product_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT id, product_id, source, channel_id, message_id, dry_run, posted_at FROM posts "
            "WHERE product_id = ? ORDER BY posted_at DESC LIMIT 1",
            (product_id,),
        )
        return dict(row) if row else None

    def awaiting_items(self, limit: int = 20, *, statuses: tuple[str, ...] = LINK_WAITING,
                       newest_first: bool = False) -> list[QueueItem]:
        """관리자 손을 기다리는 항목: 기본은 내 링크 대기, statuses=("awaiting_approval",) 이면 정보 글 확인 대기.
        오래된 것부터 (newest_first 면 새것부터 — 대시보드 목록)."""
        marks = ",".join("?" * len(statuses))
        order = "DESC" if newest_first else "ASC"
        rows = self._q(
            f"SELECT * FROM deal_queue WHERE status IN ({marks}) ORDER BY created_at {order}, id {order} LIMIT ?",
            (*statuses, limit),
        )
        return [self._row_to_queue_item(r) for r in rows]

    def awaiting_count(self, *, statuses: tuple[str, ...] = LINK_WAITING) -> int:
        """awaiting_items 와 같은 항목의 수 (목록은 몇 개만 보여도 모두 몇 개인지)."""
        marks = ",".join("?" * len(statuses))
        row = self._one(f"SELECT COUNT(*) AS c FROM deal_queue WHERE status IN ({marks})", statuses)
        return int(row["c"]) if row else 0

    def set_queue_link(self, item_id: int, url: str, now: datetime | None = None, *, auto: bool = False) -> QueueItem | None:
        """관리자가 만든 제휴 링크를 붙이고 다시 pending 으로. auto: 쇼핑커넥트 자동 링크 (주인 알림 없이)."""
        item = self.get_queue_item(item_id)
        if item is None:
            return None
        item.deal.affiliate_url = url
        extra = item.deal.product.extra
        extra.pop("raw_link", None)  # 원본 주소로 올리려던 글이어도 링크가 생겼으면 링크·고지로
        if auto:
            extra["auto_link"] = True
        else:
            extra["owner_link"] = True  # 주인이 만든 링크 (발행되면 '채널에 올렸어요'를 따로 알림)
        self.update_queue_item(item_id, status="pending", error=None, deal=item.deal, now=now, reset_created=True)
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
                cur2 = self._conn.execute(  # 관리자에게 물었는데 답이 없던 글 — 다시 묻지 않으려고 표시해 둔다
                    "UPDATE deal_queue SET status = 'expired', last_error = 'unanswered', updated_at = ? "
                    "WHERE status IN ('awaiting_link', 'awaiting_approval') AND created_at < ?",
                    (to_iso(now), to_iso(awaiting_older_than)),
                )
                n += cur2.rowcount
                cur3 = self._conn.execute(  # 자동 링크 답을 끝내 못 받은 글 (보통은 그 전에 주인 요청으로 넘어감) — 주인이 넘긴 것은 아님
                    "UPDATE deal_queue SET status = 'expired', last_error = 'autolink unanswered', updated_at = ? "
                    "WHERE status = 'awaiting_autolink' AND created_at < ?",
                    (to_iso(now), to_iso(awaiting_older_than)),
                )
                n += cur3.rowcount
            return n

    def declined_since(self, product_id: str, since: datetime) -> Deal | None:
        """since 이후 관리자가 넘겼거나(skipped) 묻고도 답이 없어 버린(unanswered) 같은 상품의 마지막 글.
        자리가 없어 못 물어본 것(review backlog full · manual link backlog full)은 넘긴 게 아니므로 뺀다."""
        row = self._one(
            "SELECT payload FROM deal_queue WHERE product_id = ? AND updated_at >= ? AND ("
            "(status = 'expired' AND last_error = 'unanswered') OR "
            "(status = 'skipped' AND COALESCE(last_error, '') NOT LIKE 'review backlog full%' "
            "AND COALESCE(last_error, '') NOT LIKE 'manual link backlog full%')"
            ") ORDER BY id DESC LIMIT 1",
            (product_id, to_iso(since)),
        )
        return Deal.from_dict(json.loads(row["payload"])) if row else None

    def queue_counts(self) -> dict[str, int]:
        rows = self._q("SELECT status, COUNT(*) AS c FROM deal_queue GROUP BY status")
        return {r["status"]: int(r["c"]) for r in rows}

    def closed_queue_reasons(self, since: datetime, until: datetime | None = None) -> list[tuple[str, str | None, str]]:
        """since~until 에 발행 전에 빠진(skipped·expired) 상품 딜의 (상태, 이유, 상품 키). 정보 글은 뺀다 (상품 딜이 아님).
        같은 상품이 같은 이유로 여러 번 줄 섰다 빠져도 한 번."""
        rows = self._q(
            "SELECT product_id, status, last_error, payload FROM deal_queue "
            "WHERE status IN ('skipped', 'expired') AND updated_at >= ? AND updated_at <= ?",
            (to_iso(since), to_iso(until or utcnow())),
        )
        out: dict[tuple[str, str | None, str], None] = {}
        for r in rows:
            try:
                kind = (json.loads(r["payload"]).get("product") or {}).get("deal_kind")
            except (ValueError, AttributeError):
                kind = None
            if kind != "info":
                out[(r["status"], r["last_error"], r["product_id"])] = None
        return list(out)

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

    def set_market_quote(
        self, product_id: str, *, price: int, source: str, title: str | None, url: str | None,
        now: datetime | None = None, meta: str | None = None,
    ) -> None:
        """meta: 대조한 상품의 분류 등 (JSON 글자). 기억한 값을 꺼낼 때도 처음 찾은 값과 같은 판정이 나오게."""
        now = now or utcnow()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO market_quotes (product_id, price, source, title, url, checked_at, meta) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET price = excluded.price, source = excluded.source,
                    title = excluded.title, url = excluded.url, checked_at = excluded.checked_at, meta = excluded.meta
                """,
                (product_id, price, source, title, url, to_iso(now), meta),
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

    def kv_take(self, key: str) -> str | None:
        """읽고 바로 지운다 (버튼을 두 번 눌러도 한 번만 처리)."""
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            return row["value"]

    def kv_delete_prefix(self, prefix: str, *, keep: Callable[[str, str | None], bool] | None = None) -> int:
        """prefix 로 시작하는 kv 를 지운다 (keep(key, value) 가 True 인 것은 남김)."""
        rows = self._q("SELECT key, value FROM kv WHERE key LIKE ?", (prefix.replace("%", "") + "%",))
        gone = [r["key"] for r in rows if keep is None or not keep(r["key"], r["value"])]
        with self._lock:
            for k in gone:
                self._conn.execute("DELETE FROM kv WHERE key = ?", (k,))
        return len(gone)

    # ------------------------------------------------------------ metrics
    def metric_put_many(
        self, rows: list[tuple[str, str, str, float]], *, origin: str = "auto", now: datetime | None = None
    ) -> int:
        """(source, metric, day, value) 들을 한 번에 저장. 같은 소스·지표·날짜는 덮어쓴다 (나중 값이 이김)."""
        if not rows:
            return 0
        ts = to_iso(now or utcnow())
        with self._tx() as c:
            c.executemany(
                "INSERT INTO metrics (source, metric, day, value, read_at, origin) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source, metric, day) DO UPDATE SET value = excluded.value, read_at = excluded.read_at, "
                "origin = excluded.origin",
                [(src, metric, day, float(value), ts, origin) for src, metric, day, value in rows],
            )
        return len(rows)

    def metric_put(self, source: str, metric: str, day: str, value: float, *, origin: str = "auto",
                   now: datetime | None = None) -> None:
        self.metric_put_many([(source, metric, day, value)], origin=origin, now=now)

    def metric_series(self, source: str, metric: str, since_day: str, until_day: str) -> list[tuple[str, float]]:
        """since_day ~ until_day (둘 다 포함, YYYY-MM-DD) 의 (날짜, 값), 날짜 순."""
        rows = self._q(
            "SELECT day, value FROM metrics WHERE source = ? AND metric = ? AND day >= ? AND day <= ? ORDER BY day",
            (source, metric, since_day, until_day),
        )
        return [(r["day"], float(r["value"])) for r in rows]

    def metric_latest(self, source: str, metric: str, *, until_day: str | None = None) -> dict[str, Any] | None:
        """가장 최근 날짜의 값 {day, value, read_at, origin}. until_day 를 주면 그날까지만 본다."""
        sql = "SELECT day, value, read_at, origin FROM metrics WHERE source = ? AND metric = ?"
        params: list[Any] = [source, metric]
        if until_day:
            sql += " AND day <= ?"
            params.append(until_day)
        row = self._one(sql + " ORDER BY day DESC LIMIT 1", tuple(params))
        return {"day": row["day"], "value": float(row["value"]), "read_at": row["read_at"], "origin": row["origin"]} if row else None

    def metric_last_read(self, source: str, *, origin: str | None = None) -> datetime | None:
        """그 소스의 값이 마지막으로 저장된 시각 (origin 을 주면 그 종류만)."""
        sql = "SELECT MAX(read_at) AS t FROM metrics WHERE source = ?"
        params: tuple[Any, ...] = (source,)
        if origin:
            sql += " AND origin = ?"
            params = (source, origin)
        row = self._one(sql, params)
        return from_iso(row["t"]) if row and row["t"] else None

    def channel_post_times(self, since: datetime, *, include_dry_run: bool = False) -> list[datetime]:
        """그 뒤로 채널에 올린 글(딜·정보 글)의 시각. 기본은 실제 발행만 (대시보드의 하루 글 수)."""
        sql = "SELECT posted_at FROM posts WHERE posted_at >= ?"
        if not include_dry_run:
            sql += " AND dry_run = 0"
        return [t for r in self._q(sql, (to_iso(since),)) if (t := from_iso(r["posted_at"])) is not None]

    def published_deal_times(self, since: datetime, *, include_dry_run: bool = False) -> list[datetime]:
        """그 뒤로 채널에 올라간 상품 딜(정보 글 제외)의 발행 시각 (대시보드 '올린 딜')."""
        # payload 는 봇이 json.dumps 기본 구분자로 쓴 것 — SQLite JSON 함수 없이 찾는다 (오래된 SQLite 에도 되게)
        sql = (
            "SELECT q.updated_at FROM deal_queue q WHERE q.status = 'published' AND q.updated_at >= ? "
            "AND q.payload NOT LIKE '%\"deal_kind\": \"info\"%'"
        )
        if not include_dry_run:
            sql += " AND EXISTS (SELECT 1 FROM posts p WHERE p.product_id = q.product_id AND p.dry_run = 0 AND p.posted_at >= ?)"
            params: tuple[Any, ...] = (to_iso(since), to_iso(since))
        else:
            params = (to_iso(since),)
        return [t for r in self._q(sql, params) if (t := from_iso(r["updated_at"])) is not None]

    # ------------------------------------------------------- admin_msgs
    def _seed_admin_msgs(self) -> None:
        """기록을 처음 켤 때: 이미 보낸 링크 요청·확인 요청 메시지(kv 에 id 가 남아 있음)를 옮겨 둔다.
        확인 요청은 정품 확인·정보 글 확인 둘 중 무엇인지 몰라 종류를 비워 둔다 (답장 때 글 첫머리로 가림)."""
        now = to_iso(utcnow())
        for prefix, kind in (("link_notice:", "link"), ("review_notice:", "")):
            for r in self._q("SELECT key, value FROM kv WHERE key LIKE ?", (prefix + "%",)):
                qid, mid = r["key"][len(prefix):], str(r["value"] or "")
                if qid.isdigit() and mid.isdigit():
                    self.record_admin_msg(int(mid), kind, int(qid), now=now)
        self.kv_set("admin_msgs_since", now)

    def record_admin_msg(self, message_id: int, kind: str, queue_id: int | None, *, now: str | None = None) -> None:
        if not message_id:
            return
        with self._lock:
            self._conn.execute(
                "INSERT INTO admin_msgs (message_id, kind, queue_id, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(message_id) DO UPDATE SET kind = excluded.kind, queue_id = excluded.queue_id",
                (int(message_id), kind, queue_id, now or to_iso(utcnow())),
            )

    def admin_msg(self, message_id: int) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM admin_msgs WHERE message_id = ?", (int(message_id),))
        return dict(row) if row else None

    def prune_admin_msgs(self, days: int = 30, now: datetime | None = None) -> int:
        cut = to_iso((now or utcnow()) - timedelta(days=days))
        with self._lock:
            return self._conn.execute("DELETE FROM admin_msgs WHERE at < ?", (cut,)).rowcount

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
