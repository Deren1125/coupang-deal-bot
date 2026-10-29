"""대시보드 '오늘 할 일' 칸 (주인 10/27 — 네이버 블로그가 반자동이 됨: 발행·예약·이웃 공감/댓글/신청·SNS 팔로우는 주인이 손으로,
하루 5번(아침·점심·오후·저녁·밤) 10~15분씩. "깔끔하고 부담 없이" — 지금 시간대만 펼치고 나머지는 접음 · 복사 단추 · 줄마다 체크 · 3/8).

GET /dashboard/api/routine (할 일 탭을 열 때만 — 첫 상태 값 예산 밖, 그리기는 static/routine.js) → view():
    {"day", "today": bool, "slots": [{key, at, label, items: [{…item, done, tag}], done, total, min_left, current}],
     "overflow", "done", "total", "ably": {n, note, rows: [{rid, pid, title, piece, name, price, shop, url, due, due_text}]} | None}
  목록이 없으면 slots 가 빈 칸 · total 0 → 칸을 숨긴다 (에이블리 링크 부탁이 있으면 그 칸만).
에이블리 링크 부탁 (주인 10/28): Blog-Auto 칸 ably_requests (blogauto._ably_requests) — 서버는 에이블리를 열지 않고 주인이 폰 앱에서
  링크를 만들어 붙여 넣음 → 대시보드 일 blog.similar_links {blog: issue, pid, links: [붙여 넣은 글], rid}. 없거나 0개면 None.
출처: 블로그 프로그램 기록 파일 status.routine (blogauto._routine) + EXTRA_SOURCES (지금은 스레드 세션 몫 threads_items 하나 — 늘 []).
  두 번째 출처는 같은 줄 모양(blogauto._routine_item)으로 함수 하나만 더하면 된다 — 같은 id 는 먼저 온 줄만, 없는 시간대 줄은 뺀다.
체크: 봇 DB kv "routine_done:<그 목록의 날짜>" = id 목록 (JSON). 바꾸는 것은 대시보드 일 routine.done {id, done} (actions.py) 하나뿐.
  전날 것까지만 남기고 지운다. 이 칸은 사이트를 열거나 로그인하지 않는다 (읽고 체크만).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.blogauto import (
    NV_BLOGS,
    ROUTINE_DEFAULT_SLOTS,
    ROUTINE_ITEMS_MAX,
    _routine_item,
)

log = logging.getLogger(__name__)

DONE_KEY = "routine_done:"
DONE_MAX = 300


def _tz(bot: Any) -> ZoneInfo:
    return ZoneInfo(bot.settings.app.timezone)


def today(bot: Any, now: float) -> str:
    return datetime.fromtimestamp(now, _tz(bot)).strftime("%Y-%m-%d")


def blog_routine(bot: Any) -> dict[str, Any] | None:
    """블로그 프로그램 몫 (status.routine — blogauto._routine 이 거른 것)."""
    st = getattr(bot, "dash_state", None)
    export = st._export() if st is not None else None
    return ((getattr(export, "v2", None) or {}).get("status") or {}).get("routine")


def threads_items(bot: Any) -> list[dict[str, Any]]:
    """스레드 세션 몫 — threads-auto(127.0.0.1:8710, threads_proxy._call) GET /api/routine 의 items (같은 줄 모양, 주인 10/27 반자동).
    threads-auto 가 하루 한 번 읽기만 해서 만든 스레드·인스타 맞팔·팔로우·좋아요·댓글 초안·답글 초안·글 초안. 꺼져 있거나 오늘 것이 아니면 []."""
    from dealbot.dashboard import threads_proxy

    try:
        status, raw, _ = threads_proxy._call("/api/routine", None, 3)
    except (OSError, ConnectionError):
        return []
    if status != 200:
        return []
    try:
        got = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    if not isinstance(got, dict) or got.get("day") != today(bot, time.time()) or not isinstance(got.get("items"), list):
        return []
    return got["items"]


EXTRA_SOURCES: tuple[Callable[[Any], list[dict[str, Any]]], ...] = (threads_items,)  # 출처를 더할 때는 여기 함수 하나


def merged(bot: Any, now: float) -> dict[str, Any] | None:
    """{day, slots, items, overflow} — 블로그 몫 + EXTRA_SOURCES. 어느 쪽에도 없으면 None."""
    base = blog_routine(bot)
    extra: list[dict[str, Any]] = []
    for src in EXTRA_SOURCES:
        try:
            got = src(bot)
        except Exception as e:  # noqa: BLE001 — 한 출처가 틀려도 나머지는 보이게
            log.warning("routine source %s failed: %s", getattr(src, "__name__", "?"), type(e).__name__)
            got = []
        extra += [it for x in (got if isinstance(got, list) else [])[:ROUTINE_ITEMS_MAX] if (it := _routine_item(x)) is not None]
    if base is None and not extra:
        return None
    r = base or {"day": today(bot, now), "slots": [{"key": k, "at": a, "label": lab} for k, a, lab in ROUTINE_DEFAULT_SLOTS],
                 "items": [], "overflow": 0}
    keys = {s["key"] for s in r["slots"]}
    items = list(r["items"])
    seen = {i["id"] for i in items}
    for it in extra:
        if it["slot"] in keys and it["id"] not in seen:
            items.append(it)
            seen.add(it["id"])
    return {**r, "items": items}


def day_of(bot: Any, now: float) -> str:
    """체크를 적을 날짜 — 지금 보이는 목록의 날짜 (목록이 없으면 오늘)."""
    try:
        r = merged(bot, now)
    except Exception:  # noqa: BLE001
        r = None
    return r["day"] if r else today(bot, now)


def done_ids(db: Any, day: str) -> set[str]:
    try:
        v = json.loads(db.kv_get(DONE_KEY + day) or "[]")
    except (TypeError, ValueError):
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def set_done(db: Any, day: str, rid: str, done: bool) -> None:
    """체크 하나 바꾸기 — 그날 목록에 넣거나 뺀다. 그 날짜와 전날 것만 남긴다."""
    ids = done_ids(db, day)
    if done:
        ids.add(rid)
    else:
        ids.discard(rid)
    db.kv_set(DONE_KEY + day, json.dumps(sorted(ids)[:DONE_MAX], ensure_ascii=False))
    try:
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        return
    db.kv_delete_prefix(DONE_KEY, keep=lambda k, _v: k in (DONE_KEY + day, DONE_KEY + prev))


def _due_text(due: float | None, now: float, z: ZoneInfo) -> str:
    """예약 발행 시각 — 오늘이면 'HH:MM', 다른 날이면 'M/D HH:MM'. 없거나 지났으면 ''."""
    if due is None or due <= now:
        return ""
    d, n = datetime.fromtimestamp(due, z), datetime.fromtimestamp(now, z)
    return d.strftime("%H:%M") if d.date() == n.date() else f"{d.month}/{d.day} {d:%H:%M}"


def ably_view(bot: Any, now: float) -> dict[str, Any] | None:
    """에이블리 링크 부탁 칸 (blogauto._ably_requests 가 거른 것 + 발행 시각 글). 줄이 없으면 None."""
    st = getattr(bot, "dash_state", None)
    export = st._export() if st is not None else None
    a = (getattr(export, "v2", None) or {}).get("ably_requests")
    if not a or not a.get("rows"):
        return None
    z = _tz(bot)
    rows = [{**r, "due_text": _due_text(r["due"], now, z)} for r in a["rows"]]
    return {"n": len(rows), "note": a["note"], "rows": rows}


def view(bot: Any, now: float | None = None) -> dict[str, Any]:
    """GET /dashboard/api/routine 몸. 지금 시간대 = 시각(대시보드 시간대)이 지난 마지막 칸 (첫 칸 전이면 첫 칸)."""
    now = now or time.time()
    try:
        r = merged(bot, now)
    except Exception as e:  # noqa: BLE001
        log.warning("routine view failed: %s", type(e).__name__)
        r = None
    try:
        ably = ably_view(bot, now)
    except Exception as e:  # noqa: BLE001 — 부탁 칸이 틀려도 할 일은 보이게
        log.warning("routine ably view failed: %s", type(e).__name__)
        ably = None
    if not r:
        return {"day": None, "today": False, "slots": [], "overflow": 0, "done": 0, "total": 0, "ably": ably}
    hm = datetime.fromtimestamp(now, _tz(bot)).strftime("%H:%M")
    by_at = sorted(r["slots"], key=lambda s: s["at"])
    cur = next((s["key"] for s in reversed(by_at) if s["at"] <= hm), by_at[0]["key"])
    done = done_ids(bot.db, r["day"])
    slots = []
    for s in r["slots"]:
        its = [{**i, "done": i["id"] in done, "tag": " · ".join(x for x in (i["group"], NV_BLOGS.get(i["blog"], "")) if x)}
               for i in r["items"] if i["slot"] == s["key"]]
        slots.append({**s, "items": its, "done": sum(i["done"] for i in its), "total": len(its),
                      "min_left": sum(i["min"] for i in its if not i["done"]), "current": s["key"] == cur})
    return {"day": r["day"], "today": r["day"] == today(bot, now), "slots": slots, "overflow": r["overflow"],
            "done": sum(s["done"] for s in slots), "total": sum(s["total"] for s in slots), "ably": ably,
            "times": list(r.get("times") or [])}
