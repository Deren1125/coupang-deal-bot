"""일정 화면 값 (주인 10/21 — "발행 예약한 글들 대시보드에서 좀 편하게 볼 수 있으면 좋겠어 … 앞으로의 일정을 세 블로그 통합으로").

state['sched'] = 블로그 탭 '예약된 글'(블로그마다)을 아카이브 · Pick · 핫딜 합쳐 시간순으로 한 화면에 모은 값. 새로 받는 것 없음 —
이미 만든 블로그 칸(blogs[*].schedule.items · 멈춘 글은 blogs[*].waiting)과 Blog-Auto 기록 파일 schedule 칸(차례 시각 · moved)만 쓴다.
간격 두고 발행 줄(대기 N편 → '간격 두고 발행')의 글은 줄에 넣을 때 계산해 둔 차례 시각이 Blog-Auto 에서 오므로 그대로 보인다.
누르는 것(시간 바꾸기 · 지금 발행 · 예약 취소)은 글 줄의 more 에 이미 있는 행동 그대로 — 확인 창도 그대로 (app.js exec).
글자는 한국어만. 그리기는 처음 열 때만 받는 sched.js.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

BLOGS = ("finance", "issue", "hotdeal")
LABELS = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
WEEK = "월화수목금토일"
AHEAD_DAYS = 7  # 맨 위 한 줄 '앞으로 7일'
ACTS = {"blog.schedule_move": "move", "blog.publish": "now", "blog.unschedule": "remove"}
IDLE = "앞으로 예약된 글이 없어요"


def _ts(v: Any, tz: ZoneInfo) -> float | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).timestamp()


def _pid(iid: str) -> int | None:
    try:
        return int(iid.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def _thumb(it: dict[str, Any]) -> str | None:
    return next((s["thumb"] for s in (it.get("detail") or {}).get("slots") or [] if s.get("thumb")), None)


def _day_label(day: datetime, today: datetime) -> str:
    n = (day.date() - today.date()).days
    if n == 0:
        return "오늘"
    if n == 1:
        return "내일"
    return f"{day.month}/{day.day} ({WEEK[day.weekday()]})"


def _row(blog: str, it: dict[str, Any], at: float | None, moved: bool, state: str) -> dict[str, Any]:
    acts = {ACTS[a["action"]]: a for a in it.get("more") or [] if a.get("action") in ACTS and not a.get("disabled")}
    if state == "scheduled":
        status = "예약됨 · 직접 정한 시간" if moved else "간격 두고 발행 대기"
    elif state == "publishing":
        status = "지금 발행하는 중"
    else:
        status = it.get("status") or "예약 발행 멈춤"
    return {"iid": it["iid"], "blog": blog, "chip": LABELS[blog], "title": it.get("title") or "", "state": state, "status": status,
            "at": at, "moved": moved, "thumb": _thumb(it), "move": acts.get("move"), "now": acts.get("now"), "remove": acts.get("remove")}


def build(blogs: dict[str, Any], v2: dict[str, Any], tz_name: str, now: float, ok: bool) -> dict[str, Any] | None:
    """Blog-Auto 가 예약(schedule 칸)을 안 주는 옛 버전이면 None (화면 단추도 없음).
    {text 맨 위 한 줄, counts{블로그: 앞으로 7일 편수}, total(시간 있는 글 + 멈춘 글 — 블로그 탭 입구 단추), blogs[{key,label,n}] 칩, days[{day, label, items[]}]} — 시간 없는 '멈춘 글'은 마지막 칸 (day=None)."""
    if not ok:
        return None
    tz = ZoneInfo(tz_name)
    today = datetime.fromtimestamp(now, tz)
    sch = {b: {e.get("pid"): e for e in ((((v2.get("schedule") or {}).get("blogs") or {}).get(b) or {}).get("items") or [])} for b in BLOGS}
    posts = {b: {p.get("pid"): p.get("schedule") or {} for p in (v2.get("posts") or {}).get(b, [])} for b in BLOGS}  # 블로그 탭 '예약된 글'과 같은 시각
    timed: list[dict[str, Any]] = []
    stuck: list[dict[str, Any]] = []
    for blog in BLOGS:
        b = blogs.get(blog) or {}
        for it in (b.get("schedule") or {}).get("items") or []:
            pid = _pid(it["iid"])
            e = posts[blog].get(pid) or {}
            at = _ts(e.get("scheduled_at"), tz)
            if at is None:
                continue
            timed.append(_row(blog, it, at, bool((sch[blog].get(pid) or {}).get("moved")), "publishing" if e.get("status") == "publishing" else "scheduled"))
        stuck += [_row(blog, it, None, False, "blocked") for it in b.get("waiting") or [] if it.get("kind") == "sched_blocked"]
    timed.sort(key=lambda r: (r["at"], r["blog"]))
    days: list[dict[str, Any]] = []
    for r in timed:
        d = datetime.fromtimestamp(max(r["at"], now), tz)  # 차례가 지났는데 아직 안 올라간 글은 오늘 칸
        key = f"{d:%Y-%m-%d}"
        if not days or days[-1]["day"] != key:
            days.append({"day": key, "label": _day_label(d, today), "items": []})
        r["time"] = f"{datetime.fromtimestamp(r['at'], tz):%H:%M}"
        days[-1]["items"].append(r)
    if stuck:
        days.append({"day": None, "label": "멈춘 글 · 시간이 없어요", "items": stuck})
    edge = now + AHEAD_DAYS * 86400
    counts = {b: sum(1 for r in timed if r["blog"] == b and r["at"] < edge) for b in BLOGS}
    text = f"앞으로 {AHEAD_DAYS}일 · " + " · ".join(f"{LABELS[b]} {counts[b]}" for b in BLOGS) if sum(counts.values()) else IDLE
    if stuck:
        text += f" · 멈춤 {len(stuck)}"
    return {"text": text, "counts": counts, "total": len(timed) + len(stuck), "days": days,
            "blogs": [{"key": b, "label": LABELS[b], "n": sum(1 for r in timed + stuck if r["blog"] == b)} for b in BLOGS]}
