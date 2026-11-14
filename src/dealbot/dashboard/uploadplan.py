"""올리기 할 일 — 쇼츠 서버가 정한 영상 올리기 일정 (주인 11/08: "룩북, 숏폼, 롱폼, 설명글 모두 대시보드에 알아보기 쉽게,
간소화해서, 영상 업로드 일정 최적으로 세팅해서, to do list 처럼 — 합쳐진 대시보드에").

- 데이터: 쇼츠 서버 upload_tasks.json (일정은 쇼츠 총괄이 정함 · 코딩 4 가 만듦). 딜봇은 **읽기만** — 10분마다(scheduler.dashboard_loop → tick)
  타임아웃 5초 · 크기 512KB 까지 · 실패하면 지난 목록 그대로 두고 '쇼츠 서버 응답 없음' 한 줄. 쇼츠 서버에 쓰는 일은 없음.
- 항목 필드 (코딩 4 이름): id · when(ISO 시각) · platform · platform_label · what(롱폼/쇼츠 A~D/룩북 릴스) · file_url(https) · title · description ·
  tags · order · gate_ok(false = 감독 검수 전 — '검수 대기 · 아직 올리지 마세요' 회색, '영상 보기'만(미리 보기) · 체크 없음, 할 일 수에 안 셈) · note.
  맨 위는 목록 그대로 또는 {"items"|"tasks": [...]} 둘 다 받음. id 가 없으면 when·platform·what·title 로 만든 짧은 해시 (체크가 붙도록).
- 체크(올렸어요): 봇 DB kv "uploads_done" = id 목록 (JSON). 바꾸는 일은 대시보드 일 uploads.done {id, done} 하나뿐 (actions.py).
- 화면: GET /dashboard/api/uploads → view() (SNS 탭 '올리기' 칸을 열 때만 · 그리기는 static/shorts.js).
  묶음: 오늘 · 내일 · 이번 주(7일 안) · 나중 · 지난 것(맨 아래 접힘). 할 일 탭 '지금 할 일' 에는 todo_item() 한 줄 '쇼츠 올리기 N' (오늘·밀린 것).
- 룩북 릴스는 기존 '룩북 릴스' 칸과 같은 편(id 같음)이면 여기서는 그 칸으로 가는 줄만 (lb: true) — 할 일 수에 두 번 세지 않음.
- 주소는 환경 변수 DEALBOT_UPLOAD_TASKS_URL (없으면 아래 기본값). 화면에는 주소가 오지 않음 (file_url 만).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

DEFAULT_URL = "https://3-35-144-76.sslip.io/o-7b0c7fb732a4/upload_tasks.json"
URL_ENV = "DEALBOT_UPLOAD_TASKS_URL"
YT_URL = "https://3-35-144-76.sslip.io/o-7b0c7fb732a4/sub/yt_alive.json"  # 유튜브 받기 계정 살아 있나 (11/14 쇼츠 총괄) — 같은 쇼츠 서버, 읽기만
YT_BASE = "https://3-35-144-76.sslip.io"  # 다시 로그인 주소 = 이 주소 + yt_alive.json 의 login_page (기본 /ytlogin.html) — '다시 로그인' 단추 — 주인이 누를 때만 새 탭 (자동으로 열지 않음)
YT_KINDS = ("ok", "login", "runtime", "other")
YT_EVERY = 300
CACHE_KEY = "uploads:cache"
DONE_KEY = "uploads_done"
DONE_MAX = 500
EVERY = 600  # 10분
TIMEOUT = 5.0
MAX_BYTES = 512 * 1024
ITEMS_MAX = 200
PAST_DAYS = 7  # 지난 것은 일주일만 보여 줌
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
TODO_IID = "uploads"

PLATFORMS = {"youtube_long": "유튜브 (롱폼)", "youtube_shorts": "유튜브 쇼츠", "instagram_reels": "인스타 릴스", "youtube": "유튜브", "yt": "유튜브", "instagram": "인스타", "ig": "인스타", "reels": "인스타",
             "naver_clip": "네이버 클립", "clip": "네이버 클립", "naverclip": "네이버 클립", "tiktok": "틱톡", "threads": "스레드"}
WEEK = "월화수목금토일"


def url() -> str:
    u = (os.environ.get(URL_ENV) or "").strip()
    return u if u.startswith("https://") else DEFAULT_URL


def _s(v: Any, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _when(v: Any, tz: ZoneInfo) -> float | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        d = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()


def _tags(v: Any) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[\s,]+", v)
    if not isinstance(v, list):
        return []
    out = []
    for t in v[:30]:
        t = _s(t, 40).lstrip("#")
        if t and t not in out:
            out.append(t)
    return out


def clean(raw: Any, tz: ZoneInfo) -> list[dict[str, Any]]:
    """받은 JSON → 고른 항목 목록 (모르는 필드는 버림 · file_url 은 https 만 · id 겹치면 먼저 온 것만)."""
    rows = raw.get("items", raw.get("tasks")) if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError("no list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows[:ITEMS_MAX]:
        if not isinstance(r, dict):
            continue
        when = _when(r.get("when"), tz)
        plat = _s(r.get("platform"), 30)
        what, title = _s(r.get("what"), 40), _s(r.get("title"), 200)
        rid = r.get("id")
        if not (isinstance(rid, str) and ID_RE.match(rid)):
            rid = "h" + hashlib.sha256(f"{r.get('when')}|{plat}|{what}|{title}".encode()).hexdigest()[:12]
        if rid in seen:
            continue
        seen.add(rid)
        fu = _s(r.get("file_url"), 1000)
        order = r.get("order")
        out.append({"id": rid, "when": when, "platform": plat, "plat": _s(r.get("platform_label"), 30) or PLATFORMS.get(plat.lower(), plat),
                    "gate": r.get("gate_ok") is not False,
                    "what": what, "title": title, "desc": _s(r.get("description"), 5000), "tags": _tags(r.get("tags")),
                    "file_url": fu if fu.startswith("https://") else "",
                    "order": order if isinstance(order, (int, float)) and not isinstance(order, bool) else 0,
                    "note": _s(r.get("note"), 600)})
    return out


def _tz(bot: Any) -> ZoneInfo:
    try:
        return ZoneInfo(bot.settings.app.timezone)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Seoul")


def cache(db: Any) -> dict[str, Any]:
    try:
        v = json.loads(db.kv_get(CACHE_KEY) or "{}")
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


async def fetch(tz: ZoneInfo) -> tuple[str, list[dict[str, Any]] | None]:
    """('ok', 목록) · ('none', None) 파일이 아직 없음(404) · ('down', None) 응답 없음·이상함."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
            async with c.stream("GET", url(), headers={"Accept": "application/json"}) as r:
                if r.status_code == 404:
                    return "none", None
                if r.status_code != 200:
                    return "down", None
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        return "down", None
        return "ok", clean(json.loads(body), tz)
    except Exception as e:  # noqa: BLE001 — 쇼츠 서버가 이상해도 대시보드는 그대로
        log.info("upload tasks fetch failed: %s", type(e).__name__)
        return "down", None


def yt_clean(raw: Any, tz: ZoneInfo) -> dict[str, Any]:
    """yt_alive.json {ok, kind, why, at} → {kind, why, hm}. 모르는 kind 는 other (kind 없이 ok:true 면 ok) · at 은 ISO 또는 초."""
    if not isinstance(raw, dict):
        raise ValueError("no object")
    kind = raw.get("kind")
    if kind not in YT_KINDS:
        kind = "ok" if raw.get("ok") is True and kind in (None, "") else "other"
    at = raw.get("at")
    ts = at if isinstance(at, (int, float)) and not isinstance(at, bool) else _when(at, tz)
    if ts and ts > 1e11:
        ts /= 1000
    page = raw.get("login_page")
    if not (isinstance(page, str) and re.fullmatch(r"/[A-Za-z0-9_./-]{1,100}", page) and "//" not in page and ".." not in page):
        page = "/ytlogin.html"  # 모르는 값·바깥 주소는 버리고 기본 (주소는 항상 쇼츠 서버 안)
    return {"kind": kind, "why": _s(raw.get("why"), 200), "ts": ts or None, "page": page}


def yt_when(ts: Any, now: float, tz: ZoneInfo) -> str:
    """'오늘 08:50' · '어제 08:50' · '11/12 08:50' (하루 한 번 08:50 에 갱신되니 나이를 같이 보여 줌)."""
    try:
        d = datetime.fromtimestamp(float(ts), tz)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    days = (_day(now, tz).date() - d.date()).days
    return f"{'오늘' if days == 0 else '어제' if days == 1 else f'{d.month}/{d.day}'} {d:%H:%M}"


async def fetch_yt(tz: ZoneInfo) -> tuple[str, dict[str, Any] | None]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
            r = await c.get(YT_URL, headers={"Accept": "application/json"})
        if r.status_code == 404:
            return "none", None
        if r.status_code != 200 or len(r.content) > 16 * 1024:
            return "down", None
        return "ok", yt_clean(json.loads(r.content), tz)
    except Exception as e:  # noqa: BLE001 — 실패해도 대시보드는 그대로
        log.info("yt_alive fetch failed: %s", type(e).__name__)
        return "down", None


async def tick_yt(bot: Any, now: float | None = None) -> str | None:
    """5분마다 (tick 과 따로 — 로그인 풀림은 빨리 알려야 함). 없음(404) = 카드 없음 · 실패 = 지난 값 그대로."""
    now = now or time.time()
    if now - float(cache(bot.db).get("yt_tried") or 0) < YT_EVERY:
        return None
    st, yt = await fetch_yt(_tz(bot))
    c = cache(bot.db)
    c["yt_tried"] = now
    if st == "ok":
        c["yt"] = yt
    elif st == "none":
        c.pop("yt", None)
    bot.db.kv_set(CACHE_KEY, json.dumps(c, ensure_ascii=False, separators=(",", ":")))
    return st


async def tick(bot: Any, now: float | None = None) -> str | None:
    """1분마다 불림 (scheduler.dashboard_loop). 10분이 안 지났으면 바로 돌아옴. 한 일을 짧게."""
    now = now or time.time()
    try:
        await tick_yt(bot, now)
    except Exception:  # noqa: BLE001
        log.info("yt_alive tick failed")
    db = bot.db
    c = cache(db)
    if now - float(c.get("tried") or 0) < EVERY:
        return None
    st, items = await fetch(_tz(bot))
    c["tried"], c["state"] = now, st
    if st == "ok":
        c["items"], c["at"] = items, now
    elif st == "none":
        c["items"] = []
    db.kv_set(CACHE_KEY, json.dumps(c, ensure_ascii=False, separators=(",", ":")))
    return f"{st} {len(c.get('items') or [])}"


def done_ids(db: Any) -> set[str]:
    try:
        v = json.loads(db.kv_get(DONE_KEY) or "[]")
    except (ValueError, TypeError):
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def set_done(db: Any, rid: str, done: bool) -> None:
    ids = [x for x in json.loads(db.kv_get(DONE_KEY) or "[]") if isinstance(x, str) and x != rid]
    if done:
        ids.append(rid)
    db.kv_set(DONE_KEY, json.dumps(ids[-DONE_MAX:], ensure_ascii=False))


def _lb_ids(lookbook: dict[str, Any] | None) -> set[str]:
    return {str(x.get("id")) for x in (lookbook or {}).get("items") or [] if isinstance(x, dict) and x.get("id")}


def _day(ts: float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0)


def view(bot: Any, now: float | None = None, lookbook: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET /dashboard/api/uploads 몸."""
    now = now or time.time()
    tz = _tz(bot)
    c = cache(bot.db)
    done = done_ids(bot.db)
    lbs = _lb_ids(lookbook)
    today = _day(now, tz)
    edges = [("past", "지난 것", None), ("today", "오늘", today + timedelta(days=1)), ("tomorrow", "내일", today + timedelta(days=2)),
             ("week", "이번 주", today + timedelta(days=7)), ("later", "나중", None)]
    groups: dict[str, list[dict[str, Any]]] = {k: [] for k, _, _ in edges}
    rows = sorted(c.get("items") or [], key=lambda x: (x.get("when") is None, x.get("when") or 0, x.get("order") or 0))
    for x in rows:
        w = x.get("when")
        if w is not None and w < today.timestamp():
            if w < (today - timedelta(days=PAST_DAYS)).timestamp():
                continue
            key = "past"
        elif w is None:
            key = "later"
        else:
            key = next((k for k, _, e in edges[1:4] if w < e.timestamp()), "later")
        d = datetime.fromtimestamp(w, tz) if w is not None else None
        groups[key].append({**x, "done": x["id"] in done, "lb": x["id"] in lbs,
                            "when_text": f"{d.month}/{d.day}({WEEK[d.weekday()]}) {d:%H:%M}" if d else "날짜 미정",
                            "hm": f"{d:%H:%M}" if d else ""})
    for g in groups.values():
        for x in g:
            x.pop("order", None)
    state = c.get("state") or "wait"
    at = c.get("at")
    err = None
    if state == "down":
        err = "쇼츠 서버 응답 없음" + (f" — {datetime.fromtimestamp(at, tz):%m/%d %H:%M} 목록을 보여 줘요" if at else "")
    elif state == "wait":
        err = "쇼츠 서버 일정을 읽는 중이에요 (10분마다)"
    yt = c.get("yt") if isinstance(c.get("yt"), dict) else None
    return {"ok": state == "ok", "state": state, "err": err, "yt": yt and {"kind": yt.get("kind"), "why": yt.get("why") or "", "hm": yt_when(yt.get("ts"), now, tz), "login_url": YT_BASE + (yt.get("page") or "/ytlogin.html")}, "empty": state == "none" or not rows,
            "groups": [{"key": k, "label": lab, "items": groups[k]} for k, lab, _ in edges[1:] + edges[:1] if groups[k]],  # 지난 것은 맨 아래
            "todo": count(bot, now, lookbook)}


def count(bot: Any, now: float, lookbook: dict[str, Any] | None = None) -> int:
    """'지금 할 일' 수: 오늘 끝까지 올릴 차례인데 아직 체크 안 한 것 (밀린 것 7일까지 · 룩북 칸과 같은 편은 빼고)."""
    tz = _tz(bot)
    today = _day(now, tz)
    lo, hi = (today - timedelta(days=PAST_DAYS)).timestamp(), (today + timedelta(days=1)).timestamp()
    done, lbs = done_ids(bot.db), _lb_ids(lookbook)
    return sum(1 for x in cache(bot.db).get("items") or []
               if x.get("when") is not None and lo <= x["when"] < hi and x.get("gate", True) and x["id"] not in done and x["id"] not in lbs)


def todo_item(bot: Any, now: float, goto: Any, lookbook: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """할 일 한 줄 '쇼츠 올리기 N' (없으면 None) — 누르면 SNS 탭 '올리기' 칸."""
    n = count(bot, now, lookbook)
    if not n:
        return None
    return {"iid": TODO_IID, "src": "blog", "blog": "", "kind": "uploads", "sev": "todo", "urgent": False,
            "title": f"숏폼·롱폼 올리기 {n}", "status": "오늘 올릴 영상 · 파일 받기 → 제목·설명 복사 → 앱에서 올리고 체크",
            "since": None, "expires_at": None, "primary": goto("보기", "#/sns/shorts"), "more": [], "detail": {"lines": []}}
