"""사이트·계정마다 접속 상한 (주인 10/26 — "로그인이 필요한 모든 사이트들이 너무 로그인이 자주 풀려 … 각 사이트, 각 계정별로
1분·1시간·하루 접속 횟수에 상한을 두고 강력하게").

블로그 프로그램(Blog-Auto blogauto/site_visits.py)과 같은 기록 파일(Blog-Auto var/site_gate.db)·같은 상한 표를 쓴다 — 같은 서버(같은 IP)
에서 두 프로그램이 같은 사이트를 여니까 수를 합쳐야 한다. 상한 표는 계약 파일 tests/fixtures/dash_contract/site_gate_caps.json 과 같다.

- 딜봇 크롬(쇼핑커넥트 로그인 크롬 browser.session · 상품 화면 크롬 store_browser)의 맨 위 창 화면 열기만 센다.
- 상한을 넘으면 그 화면 열기를 취소한다 (그 일은 실패로 넘어가 다음 차례에).
- 지난번 화면이 로그인 화면에서 멈췄으면(로그인 풀림) 그 계정의 그 사이트는 주인이 다시 로그인할 때까지(또는 LOCK_H 시간 뒤 한 번)
  자동으로 열지 않는다. 주인이 /naverlogin 으로 다시 로그인하면 unlock.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

WINDOWS = (("1분", 60), ("1시간", 3600), ("하루", 86400))
CAPS: dict[str, tuple[int, int, int]] = {
    "naver.com": (4, 25, 80),
    "a-bly.com": (2, 6, 15),
    "zigzag.kr": (3, 20, 40),
    "instagram.com": (3, 30, 100),
    "musinsa.com": (3, 15, 40),
    "29cm.co.kr": (3, 15, 30),
    "wconcept.co.kr": (3, 15, 30),
    "coupang.com": (3, 20, 40),
    "toss.im": (3, 15, 30),
    "keyzard.cc": (2, 6, 15),
    "kakao.com": (3, 10, 30),
}
ALL_CAPS: dict[str, tuple[int, int, int]] = {"naver.com": (8, 50, 160), "a-bly.com": (3, 20, 50), "zigzag.kr": (4, 30, 110)}  # 서버 IP 기준 (Blog-Auto site_visits 10/27)
LOGIN_CAPS = (1, 2, 4)
LOGIN_RE = re.compile(r"login|signin|sign-in", re.I)
LOCK_H = 6
SETTLE_S = 5
DEFAULT_DB = Path("/home/ubuntu/Blog-Auto/var/site_gate.db")  # 서버: 블로그 프로그램과 같은 파일

OWNER_S = 600  # 주인이 /naverlogin 으로 직접 로그인하는 동안은 세지도 막지도 않음

_path: Path | None = None
_lock = threading.Lock()
_owner_until: dict[str, float] = {}  # 계정 → 주인 로그인 창이 열린 시각까지 (이 프로세스)


def owner_window(acct: str, seconds: float = OWNER_S) -> None:
    """주인이 직접 로그인 — 멈춤을 풀고 잠시 동안 그 계정 화면 열기를 세지도 막지도 않는다."""
    unlock(acct)
    _owner_until[acct] = time.time() + seconds


def configure(path: Path | None) -> None:
    """기록 파일 자리 (app 시작 때 — Blog-Auto var 옆, 없으면 딜봇 data 폴더). None 이면 DEFAULT_DB."""
    global _path
    _path = path


def db_path() -> Path:
    return _path or DEFAULT_DB


def site_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if not host or re.fullmatch(r"[\d.]+", host):
        return host
    parts = host.split(".")
    n = 3 if len(parts) >= 3 and parts[-2] in ("co", "or", "go", "ne", "ac") and len(parts[-1]) == 2 else 2
    return ".".join(parts[-n:])


def is_login(url: str) -> bool:
    try:
        p = urlsplit(url)
    except ValueError:
        return False
    return bool(LOGIN_RE.search(p.path or "")) or (p.hostname or "").startswith(("login.", "accounts.kakao"))


@contextlib.contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE IF NOT EXISTS hits (site TEXT, acct TEXT, kind TEXT, ts REAL)")
        con.execute("CREATE INDEX IF NOT EXISTS hits_k ON hits (site, kind, acct, ts)")
        con.execute("CREATE TABLE IF NOT EXISTS locks (site TEXT, acct TEXT, since REAL, why TEXT, PRIMARY KEY (site, acct))")
        con.execute("CREATE TABLE IF NOT EXISTS last (site TEXT, acct TEXT, login INTEGER, ts REAL, PRIMARY KEY (site, acct))")
        yield con
    finally:
        con.close()


def _count(con: sqlite3.Connection, site: str, kind: str, acct: str | None, since: float) -> int:
    if acct is None:
        return int(con.execute("SELECT COUNT(*) FROM hits WHERE site=? AND kind=? AND ts>=?", (site, kind, since)).fetchone()[0])
    return int(con.execute("SELECT COUNT(*) FROM hits WHERE site=? AND kind=? AND acct=? AND ts>=?",
                           (site, kind, acct, since)).fetchone()[0])


def _over(con: sqlite3.Connection, site: str, kind: str, acct: str | None, cap: tuple[int, int, int], now: float) -> str:
    for (label, sec), n in zip(WINDOWS, cap, strict=True):
        if _count(con, site, kind, acct, now - sec) >= n:
            return f"{label} {n}번"
    return ""


def gate(url: str, acct: str, now: float | None = None) -> str:
    """자동 작업이 이 화면을 열어도 되나 — '' 이면 됨(그리고 센다), 아니면 막은 이유. 상한 표에 없는 사이트는 그냥 됨."""
    site = site_of(url)
    cap = CAPS.get(site)
    now = now or time.time()
    if cap is None or _owner_until.get(acct, 0) > now:
        return ""
    why = ""
    with _lock, _db() as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            row = con.execute("SELECT since FROM locks WHERE site=? AND acct=?", (site, acct)).fetchone()
            last = con.execute("SELECT login, ts FROM last WHERE site=? AND acct=?", (site, acct)).fetchone()
            if not (row and now - float(row[0]) < LOCK_H * 3600) and last and last[0] and now - float(last[1]) >= SETTLE_S:
                con.execute("INSERT OR REPLACE INTO locks VALUES (?, ?, ?, ?)", (site, acct, now, "login_page"))
                con.execute("DELETE FROM last WHERE site=? AND acct=?", (site, acct))
                row = (now,)
                log.warning("site gate: %s 로그인이 풀린 채 (%s) — 다시 로그인할 때까지 자동으로 열지 않음", site, acct)
            if row and now - float(row[0]) < LOCK_H * 3600:
                why = f"{site} 로그인이 풀려 멈춤 — 다시 로그인할 때까지 자동으로 열지 않음"
            elif over := _over(con, site, "page", acct, cap, now):
                why = f"{site} {over} 다 엶 (이 계정)"
            elif site in ALL_CAPS and (over := _over(con, site, "page", None, ALL_CAPS[site], now)):
                why = f"{site} {over} 다 엶 (모든 계정 합)"
            elif is_login(url) and (over := _over(con, site, "login", acct, LOGIN_CAPS, now)):
                why = f"{site} 로그인 화면 {over} — 더 두드리지 않음"
            con.execute("INSERT INTO hits VALUES (?, ?, ?, ?)", (site, acct, "blocked" if why else "page", now))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return why


def saw(url: str, acct: str, now: float | None = None) -> bool:
    """열린 화면 주소 — 마지막 주소를 남긴다. 로그인 화면이면 셈 (True)."""
    site = site_of(url)
    now = now or time.time()
    if site not in CAPS or _owner_until.get(acct, 0) > now:
        return False
    login = is_login(url)
    with _lock, _db() as con:
        if login:
            con.execute("INSERT INTO hits VALUES (?, ?, 'login', ?)", (site, acct, now))
        con.execute("INSERT OR REPLACE INTO last VALUES (?, ?, ?, ?)", (site, acct, int(login), now))
    return login


def unlock(acct: str, site: str | None = None) -> int:
    """다시 로그인함 → 그 계정의 멈춤을 푼다."""
    where, args = ("acct=? AND site=?", (acct, site)) if site else ("acct=?", (acct,))
    with _lock, _db() as con:
        con.execute(f"DELETE FROM last WHERE {where}", args)
        return con.execute(f"DELETE FROM locks WHERE {where}", args).rowcount


def _top_nav(req: Any) -> bool:
    try:
        return bool(req.is_navigation_request()) and req.frame.parent_frame is None
    except Exception:  # noqa: BLE001 — 창이 사라졌으면 그냥 보냄
        return False


async def check_route(route: Any, acct: str) -> bool:
    """route 처리기 안에서: 맨 위 창 화면 열기면 gate — 막히면 abort 하고 True (호출한 쪽은 더 하지 않음)."""
    req = route.request
    if not _top_nav(req):
        return False
    try:
        why = gate(req.url, acct)
    except sqlite3.Error as e:  # 기록 파일 문제로 일 전체를 막지는 않는다 (기록만)
        log.warning("site gate: 기록을 못 씀 (%s)", type(e).__name__)
        return False
    if not why:
        return False
    log.warning("site gate: 화면 열기 막음 — %s (%s)", why, acct)
    await route.abort("blockedbyclient")
    return True


async def watch(ctx: Any, acct: str) -> None:
    """딜봇 크롬에 건다: 상한 표 사이트의 화면 열기는 gate · 열린 화면 주소는 saw (로그인 화면에서 멈췄는지)."""
    pat = re.compile(r"^https?://([^/]*\.)?(" + "|".join(re.escape(s) for s in CAPS) + r")(:\d+)?/", re.I)

    async def route_it(route: Any) -> None:
        if not await check_route(route, acct):
            await route.continue_()

    def seen(frame: Any) -> None:
        try:
            if frame.parent_frame is None and str(frame.url).startswith("http"):
                saw(frame.url, acct)
        except Exception:  # noqa: BLE001 — 세기 실패는 일을 막지 않는다
            pass

    try:
        await ctx.route(pat, route_it)
        ctx.on("page", lambda page: page.on("framenavigated", seen))
        for page in ctx.pages:
            page.on("framenavigated", seen)
    except Exception as e:  # noqa: BLE001 — 걸지 못해도 크롬 일은 그대로
        log.warning("site gate: 크롬에 걸지 못함 (%s)", type(e).__name__)
