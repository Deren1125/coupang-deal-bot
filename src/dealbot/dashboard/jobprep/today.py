"""⓪ 오늘 — 다른 탭 기록을 모아 보여 주기만 (AI 안 씀). D-day · 다음 지원 창 · 오늘 할 일 4줄 · 마감 7일 안 · 어제 바뀐 것."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep import ctci, jobs

KST = ZoneInfo("Asia/Seoul")
SHOW = 3


def _day_bounds(jp: Any) -> tuple[str, float]:
    now = datetime.fromtimestamp(jp.clock(), KST)
    start = datetime(now.year, now.month, now.day, tzinfo=KST).timestamp()
    return now.strftime("%Y-%m-%d"), start


def dday(target: str, today: str) -> int | None:
    try:
        return (date.fromisoformat(target) - date.fromisoformat(today)).days
    except ValueError:
        return None


def next_window(windows: list[dict[str, str]], today: str) -> dict[str, Any] | None:
    best = None
    for w in windows or []:
        s, e = w.get("start") or "", w.get("end") or ""
        if e and e < today:
            continue
        d = 0 if (s and s <= today) else dday(s, today)
        if d is None:
            continue
        cand = {"label": w.get("label") or "", "start": s, "end": e, "d": d, "open": bool(s and s <= today), "guess": w.get("guess") == "1"}
        if best is None or d < best["d"]:
            best = cand
    return best


def checklist(jp: Any) -> list[dict[str, Any]]:
    day, start = _day_bounds(jp)
    st = jp.store
    code_n = (st.one("SELECT COUNT(DISTINCT qid) n FROM attempts WHERE at>=? AND item='book'", (start,)) or {}).get("n", 0)
    solved = (st.one("SELECT COUNT(*) n FROM ctci_progress WHERE solved_at>=?", (start,)) or {}).get("n", 0)
    pron_n = (st.one("SELECT COUNT(*) n FROM pron_takes WHERE at>=?", (start,)) or {}).get("n", 0)
    call = (st.one("SELECT COUNT(*) n FROM eng_sessions WHERE day=?", (day,)) or {}).get("n", 0)
    speech = (st.one("SELECT COUNT(*) n FROM speech_takes WHERE at>=?", (start,)) or {}).get("n", 0)
    return [
        {"key": "code", "label": "코딩 2문제+", "done": max(code_n, solved) >= 2, "n": max(code_n, solved), "go": "#/code"},
        {"key": "pron", "label": "발음 5~10분", "done": pron_n >= 5, "n": pron_n, "go": "#/eng"},
        {"key": "call", "label": "AI 통화 20~30분 (통화 뒤 정리)", "done": call >= 1, "n": call, "go": "#/eng"},
        {"key": "speech", "label": "스피치 카드 1", "done": speech >= 1, "n": speech, "go": "#/speech"},
    ]


def changes(jp: Any) -> dict[str, Any]:
    since = jp.clock() - 86400
    rows = jp.store.q("SELECT at, job_id, kind, note FROM job_events WHERE at>=? AND kind IN ('new','closed','watch','reopened') ORDER BY at DESC", (since,))
    out: dict[str, Any] = {}
    for k in ("new", "closed", "watch"):
        items = [r for r in rows if r["kind"] == k or (k == "new" and r["kind"] == "reopened")]
        out[k] = {"n": len(items), "items": [{"note": r["note"], "at": r["at"]} for r in items[:SHOW]], "more": max(0, len(items) - SHOW)}
    return out


def view(jp: Any) -> dict[str, Any]:
    cfg = jp.cfg()
    day, _ = _day_bounds(jp)
    q = ctci.quota(jp) if ctci.load_index(jp) else None
    return {"day": day, "discharge": {"date": cfg.discharge, "d": dday(cfg.discharge, day)},
            "window": next_window(list(cfg.apply_windows), day), "todo": checklist(jp),
            "deadlines": jobs.deadlines_soon(jp.store, day), "changes": changes(jp),
            "code_week_left": q["week_left"] if q else None, "spend": jp.llm.view()}
