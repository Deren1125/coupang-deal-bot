"""스레드 글·통계 화면 값 (주인 10/21 — Blog-Auto threads_api: 공식 Threads API · 주인이 쓴 글만).

state['thpost'] = Blog-Auto 기록 파일 threads 칸(blogauto._threads 로 걸러 둔 것)을 화면에 바로 쓸 한국어 글로 바꾼 값.
새로 받는 것 없음. 누르는 것은 다리 일 blog.threads_post (★) · blog.threads_cancel · blog.threads_reply (★) 셋뿐 —
AI 가 쓰는 글·답글 초안·저절로 올리기는 없다. 그리기는 화면 쪽(다른 세션이 만드는 스레드 화면)이 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

WEEK = "월화수목금토일"
TEXT_MAX = 500
NOT_CONNECTED = "스레드 연결 필요"
HOW = "Blog-Auto .env 에 THREADS_ACCESS_TOKEN·THREADS_USER_ID 를 넣고 블로그 프로그램을 다시 시작해 주세요"
STATUS = {"scheduled": "예약", "posting": "올리는 중", "posted": "올림", "failed": "실패", "cancelled": "취소함"}
METRIC_LABELS = (("views", "조회"), ("likes", "좋아요"), ("replies", "답글"), ("reposts", "리포스트"), ("quotes", "인용"))


def _dt(v: Any, tz: ZoneInfo) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=tz)).astimezone(tz)


def when(v: Any, tz: ZoneInfo, now: float) -> str:
    """'오늘 21:30' · '내일 08:00' · '어제 20:15' · '10/17 20:15'."""
    d = _dt(v, tz)
    if d is None:
        return ""
    n = datetime.fromtimestamp(now, tz)
    gap = (d.date() - n.date()).days
    head = {0: "오늘", 1: "내일", -1: "어제"}.get(gap, f"{d.month}/{d.day}")
    return f"{head} {d:%H:%M}"


def _day(day: str) -> str:
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{d.month}/{d.day} ({WEEK[d.weekday()]})"


def build(th: dict[str, Any] | None, tz: ZoneInfo, now: float, act: Callable[..., dict[str, Any]], *,
          bridge_ok: bool = True) -> dict[str, Any] | None:
    """화면 값. Blog-Auto 가 threads 칸을 아직 안 쓰면(예전 판) None. act = state.act (확인 창 ★ 여부를 같은 기준으로)."""
    if th is None:
        return None
    if not th.get("connected"):
        return {"connected": False, "status": NOT_CONNECTED, "how": th.get("how") or HOW, "limit": TEXT_MAX,
                "today": 0, "cap": th.get("cap") or 0, "cap_text": "", "compose": None, "queue": [], "posts": [],
                "replies": {"new": 0, "items": []}, "stats": None, "badge": 0}
    days = th.get("token_days")
    status = "연결됨" + (f" · 토큰 {days}일 남음" if isinstance(days, int) else "")
    if isinstance(days, int) and days <= 7:
        status += " — 곧 만료, 새 토큰을 넣어 주세요"
    today, cap = int(th.get("today") or 0), int(th.get("cap") or 0)
    full = cap > 0 and today >= cap
    compose = act("blog.threads_post", "바로 올리기", {}, input="text", limit=TEXT_MAX, schedule_label="예약",
                  photo=True, disabled=not bridge_ok or None)
    queue = []
    for x in th.get("queue") or []:
        queue.append({"id": x["id"], "text": x["text"], "when": when(x.get("at"), tz, now), "status": x["status"],
                      "status_text": STATUS.get(x["status"], x["status"]) + (f" · {x['error']}" if x.get("error") else ""),
                      "image": bool(x.get("image")), "permalink": x.get("permalink") or "",
                      "cancel": act("blog.threads_cancel", "취소", {"id": x["id"]}) if x["status"] == "scheduled" else None})
    queue.sort(key=lambda q: (q["status"] != "scheduled", q["when"]))
    per = {p["id"]: p for p in ((th.get("stats") or {}).get("posts") or [])}
    posts = [{"id": p["id"], "text": p["text"], "when": when(p.get("at"), tz, now), "permalink": p.get("permalink") or "",
              "stats": {k: per[p["id"]][k] for k, _ in METRIC_LABELS} if p["id"] in per else None}
             for p in th.get("posts") or []]
    items = [{"id": r["id"], "post_text": r["post_text"], "username": r["username"], "text": r["text"],
              "when": when(r.get("at"), tz, now),
              "reply": act("blog.threads_reply", "답글 달기", {"media_id": r["id"]}, input="text", limit=TEXT_MAX)}
             for r in (th.get("replies") or {}).get("items") or []]
    stats = None
    st = th.get("stats")
    if st:
        stats = {"when": when(st.get("at"), tz, now), "range": "최근 7일",
                 "totals": [{"key": k, "label": lab, "n": int((st.get("totals") or {}).get(k) or 0)} for k, lab in METRIC_LABELS],
                 "followers": st.get("followers"),
                 "days": [{"day": d["day"], "label": _day(d["day"]), "views": d["views"]} for d in st.get("days") or []]}
    return {"connected": True, "status": status, "how": "", "limit": TEXT_MAX, "today": today, "cap": cap,
            "cap_text": f"오늘 {today}/{cap}편" + (" — 오늘 한도에 닿았어요 (내일로 예약은 돼요)" if full else ""),
            "compose": compose, "queue": queue, "posts": posts, "replies": {"new": len(items), "items": items},
            "stats": stats, "badge": len(items)}
