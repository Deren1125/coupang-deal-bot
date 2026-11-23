"""취준 저장소 — jobprep-data/jobprep.db (딜봇 DB 와 따로 · WAL) + 파일 경로 (data_dir 밖으로 못 나감 · 폴더 700 · 파일 600).

처음 쓸 때 연다 (경로 등록만으로는 디스크를 건드리지 않음 — 다른 테스트·꺼진 설정에서 폴더가 생기지 않게)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, kind TEXT, model TEXT, made_at REAL, body TEXT);
CREATE TABLE IF NOT EXISTS usage (day TEXT, kind TEXT, n INTEGER DEFAULT 0, usd REAL DEFAULT 0, PRIMARY KEY (day, kind));
CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY, tab TEXT, ctx TEXT, role TEXT, model TEXT, text TEXT, at REAL);
CREATE INDEX IF NOT EXISTS chat_tab ON chat (tab, ctx, at);
CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT, ver INTEGER, at REAL);
CREATE TABLE IF NOT EXISTS acad_docs (id INTEGER PRIMARY KEY, file TEXT, at REAL, parsed TEXT);
CREATE TABLE IF NOT EXISTS acad_courses (id INTEGER PRIMARY KEY, name TEXT, credits REAL, grade TEXT, area TEXT, term TEXT, retake INTEGER DEFAULT 0,
  no_retake INTEGER DEFAULT 0, status TEXT DEFAULT 'done', military INTEGER DEFAULT 0, ge_area TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS acad_req (id INTEGER PRIMARY KEY CHECK (id = 1), body TEXT, at REAL);
CREATE TABLE IF NOT EXISTS acad_plan (term TEXT PRIMARY KEY, body TEXT, at REAL);
CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY, name TEXT, norm TEXT UNIQUE, cat TEXT, region TEXT, ats TEXT, token TEXT,
  career_url TEXT, growth_note TEXT, on_ INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, src TEXT, src_id TEXT, ukey TEXT UNIQUE, company_id INTEGER, title TEXT, loc TEXT,
  region TEXT, url TEXT, alt_urls TEXT DEFAULT '[]', desc_hash TEXT, body TEXT, start TEXT, deadline TEXT, season TEXT, conversion INTEGER,
  bachelor_ok INTEGER, grad_ok INTEGER, is_intern INTEGER, fit INTEGER, fit_why TEXT, auto_out INTEGER DEFAULT 0, state TEXT DEFAULT 'open',
  closed_why TEXT, closed_at REAL, miss INTEGER DEFAULT 0, first_seen REAL, last_seen REAL, user_status TEXT DEFAULT '', applied_at REAL,
  cat TEXT, extracted INTEGER DEFAULT 0, profile_ver INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs (state);
CREATE TABLE IF NOT EXISTS job_events (at REAL, job_id INTEGER, kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS watch_pages (company_id INTEGER, url TEXT PRIMARY KEY, hash TEXT, changed_at REAL, note TEXT, checked_at REAL);
CREATE TABLE IF NOT EXISTS exclusions (id INTEGER PRIMARY KEY, scope TEXT, key TEXT, label TEXT, at REAL, UNIQUE (scope, key));
CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY, cat TEXT, company TEXT, role TEXT, school TEXT, major TEXT, gpa TEXT, english TEXT,
  exp TEXT, certs TEXT, questions TEXT, prep TEXT, url TEXT UNIQUE, src_kind TEXT, at REAL);
CREATE TABLE IF NOT EXISTS resumes (id INTEGER PRIMARY KEY, label TEXT, kind TEXT, url TEXT UNIQUE, summary TEXT);
CREATE TABLE IF NOT EXISTS ctci_progress (qid TEXT PRIMARY KEY, state TEXT, opened_hints INTEGER DEFAULT 0, saw_solution INTEGER DEFAULT 0,
  solved_at REAL, warmups_shown INTEGER, clean INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY, qid TEXT, item TEXT, code TEXT, passed INTEGER, total INTEGER, fail_case TEXT, at REAL);
CREATE TABLE IF NOT EXISTS reviews (qid TEXT PRIMARY KEY, due TEXT, n INTEGER);
CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY, kind TEXT, title TEXT, url TEXT, body TEXT, tags TEXT, src_tab TEXT, at REAL);
CREATE TABLE IF NOT EXISTS speech_cards (id INTEGER PRIMARY KEY, week TEXT, video_id TEXT, setup TEXT, twist TEXT, t_start REAL, pause REAL,
  wps REAL, quote TEXT, at REAL);
CREATE TABLE IF NOT EXISTS speech_takes (id INTEGER PRIMARY KEY, card_id INTEGER, file TEXT, at REAL, result TEXT);
CREATE TABLE IF NOT EXISTS human_topics (day TEXT PRIMARY KEY, title TEXT, body TEXT, week TEXT);
CREATE TABLE IF NOT EXISTS human_takes (id INTEGER PRIMARY KEY, day TEXT, file TEXT, said TEXT, review TEXT, at REAL);
CREATE TABLE IF NOT EXISTS eng_week (day TEXT PRIMARY KEY, body TEXT, week TEXT);
CREATE TABLE IF NOT EXISTS eng_sessions (id INTEGER PRIMARY KEY, day TEXT, text_len INTEGER, result TEXT, at REAL);
CREATE TABLE IF NOT EXISTS eng_fix (id INTEGER PRIMARY KEY, session_id INTEGER, mine TEXT, better TEXT, why TEXT, kind TEXT, at REAL);
CREATE TABLE IF NOT EXISTS eng_expr (day TEXT PRIMARY KEY, text TEXT, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pron_sents (id INTEGER PRIMARY KEY, text TEXT UNIQUE, focus TEXT, src TEXT, at REAL);
CREATE TABLE IF NOT EXISTS pron_takes (id INTEGER PRIMARY KEY, sent_id INTEGER, at REAL, score REAL, per_phone TEXT, state TEXT, file TEXT, heard TEXT,
  started_at REAL, tries INTEGER DEFAULT 0);
"""
KINDS = ("acad", "pron", "speech", "human", "tmp", "cache", "ctci", "models")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


class PathError(ValueError):
    pass


class Store:
    def __init__(self, data_dir: str | Path) -> None:
        self.root = Path(os.path.expanduser(str(data_dir))).resolve()
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._batch = 0

    # ── 연결 ──
    def conn(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self.root.mkdir(parents=True, exist_ok=True)
                with contextlib.suppress(OSError):
                    os.chmod(self.root, 0o700)
                db = self.root / "jobprep.db"
                if not db.exists():
                    os.close(os.open(db, os.O_WRONLY | os.O_CREAT, 0o600))
                with contextlib.suppress(OSError):
                    os.chmod(db, 0o600)
                c = sqlite3.connect(str(db), check_same_thread=False, timeout=10)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(SCHEMA)
                migrate(c)
                self._conn = c
            return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def q(self, sql: str, args: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self.conn().execute(sql, tuple(args)).fetchall()]

    def one(self, sql: str, args: tuple | list = ()) -> dict[str, Any] | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql: str, args: tuple | list = ()) -> int:
        with self._lock:
            c = self.conn()
            cur = c.execute(sql, tuple(args))
            if not self._batch:
                c.commit()
            return int(cur.lastrowid or cur.rowcount or 0)

    @contextlib.contextmanager
    def batch(self) -> Any:
        """안에서 쓴 것을 한 번에 commit (공고 보드 한 번 = 쓰기 한 번). 오류면 되돌림."""
        with self._lock:
            c = self.conn()
            self._batch += 1
            try:
                yield self
            except BaseException:
                self._batch -= 1
                if not self._batch:
                    c.rollback()
                raise
            self._batch -= 1
            if not self._batch:
                c.commit()

    def kv_get(self, k: str, default: Any = None) -> Any:
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        if r is None:
            return default
        try:
            return json.loads(r["v"])
        except ValueError:
            return default

    def kv_set(self, k: str, v: Any) -> None:
        self.x("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
               (k, json.dumps(v, ensure_ascii=False, separators=(",", ":"))))

    # ── 파일 ──
    def path(self, kind: str, name: str = "") -> Path:
        """data_dir/<kind>/<name> — kind 는 정해진 것만, name 은 서버가 붙인 이름 꼴만 (경로 나누개·'..' 없음)."""
        if kind not in KINDS:
            raise PathError("kind")
        if name and (not NAME_RE.match(name) or ".." in name):
            raise PathError("name")
        base = (self.root / kind)
        p = (base / name) if name else base
        rp = p.resolve()
        if rp != self.root and self.root not in rp.parents:
            raise PathError("outside")
        return p

    def ensure_dir(self, kind: str) -> Path:
        d = self.path(kind)
        d.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
            os.chmod(d, 0o700)
        return d

    def write(self, kind: str, name: str, data: bytes) -> Path:
        self.ensure_dir(kind)
        p = self.path(kind, name)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        with contextlib.suppress(OSError):
            os.chmod(p, 0o600)
        return p

    def read(self, kind: str, name: str) -> bytes | None:
        try:
            p = self.path(kind, name)
        except PathError:
            return None
        if not p.is_file() or p.is_symlink():
            return None
        return p.read_bytes()

    def prune(self, kind: str, older_s: float, now: float | None = None) -> int:
        now = now or time.time()
        n = 0
        try:
            for p in self.path(kind).rglob("*"):
                if p.is_file() and now - p.stat().st_mtime > older_s:
                    p.unlink()
                    n += 1
        except (OSError, PathError):
            pass
        return n


# 표에 나중에 더한 칸 (예전 DB 에도 붙임 · 몇 번 돌려도 같음)
COLUMNS = {"acad_courses": (("no_retake", "INTEGER DEFAULT 0"), ("status", "TEXT DEFAULT 'done'"), ("military", "INTEGER DEFAULT 0"),
                            ("ge_area", "TEXT DEFAULT ''"))}


def migrate(c: sqlite3.Connection) -> None:
    for table, cols in COLUMNS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    # 11/23 학사: 수강 중(35 전쟁과역사 2026-2)·계획(36·37 2027-1 군강좌) — 그 줄이 아직 성적 없음일 때만, 한 번만
    done = c.execute("SELECT v FROM kv WHERE k='mig:acad_1123'").fetchone()
    if not done:
        c.execute("UPDATE acad_courses SET status='in_progress', military=1 WHERE id=35 AND COALESCE(grade,'')='' AND term='2026-2'")
        c.execute("UPDATE acad_courses SET status='planned', military=1 WHERE id IN (36, 37) AND COALESCE(grade,'')='' AND term='2027-1'")
        c.execute("UPDATE acad_courses SET military=1 WHERE id IN (33, 34) AND term='2026-여름' AND grade='P'")  # 2026 여름 군강좌 (GD) 2과목
        c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('mig:acad_1123', '1')")
    c.commit()


def mem_free_mb(meminfo: str | None = None) -> int:
    """/proc/meminfo 의 MemAvailable (MB). 못 읽으면 크게 (막지 않음)."""
    try:
        text = meminfo if meminfo is not None else Path("/proc/meminfo").read_text()
        m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", text, re.M)
        return int(m.group(1)) // 1024 if m else 99999
    except OSError:
        return 99999


class JpError(Exception):
    """화면에 보일 쉬운 말 오류 (status, code, text)."""

    def __init__(self, status: int, code: str, text: str) -> None:
        super().__init__(code)
        self.status, self.code, self.text = status, code, text


def clip(v: Any, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]
