"""X(트위터) 화면 값 (주인 10/25 — 'X 는 유료 구독(X Plus)만, API 는 안 써').

state['x'] = 블로그 탭 'X(트위터) →' 화면(static/xpost.js)이 그대로 그리는 값. 새로 받는 것 없음 — 봇 DB kv 와 Blog-Auto 기록 파일 x 칸만.
- 진행률: 주인이 주마다 크리에이터 스튜디오에서 보고 넣는 숫자 셋(x.stats) — 인증 팔로워 n/500 · 90일 인증 노출 n/500,000 · 전체 팔로워 ·
  지금 속도면 예상 달성일 (마지막 두 기록으로 직선 — 두 번 넣기 전엔 없음). 수익 배분 조건은 docs/x_strategy.md 2장 (Blog-Auto).
- 초안함: Blog-Auto x_drafts 가 만든 데런픽 초안 — 'X에 올리기'는 x.com/intent/post 링크(무료, X 앱이 열림)일 뿐이고
  올리는 건 주인 손. 누르는 것은 다리 일 blog.x_posted · blog.x_drop · blog.x_redo 셋 (확인 창 없음).
- 링크 답글 (주인 10/26 새 기준 — 블로그 주소는 본문 말고 첫 답글): 초안마다 reply 글자 · 복사 · 주인이 방금 올린 X 글 주소를 넣으면
  x.com/intent/tweet?in_reply_to= 답글 쓰기 화면 링크 (REPLY_INTENT — 이것도 글쓰기 화면만 열림, 올리는 건 주인 손).
- 수익 계정 2주 계획(주인 10/26): 글 수(plan_n)만 여기 · 계획은 GET /dashboard/api/xplan (화면을 열 때만 — 첫 상태 값 예산).
- 하지 않는 것: X API · x.com 읽기 · 자동 게시 · 자동 좋아요·답글·리포스트·팔로우.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import post_count

VERIFIED_GOAL = 500
IMPR_GOAL = 500_000
INTENT = "https://x.com/intent/post?text="  # 글은 화면이 encodeURIComponent 로 붙인다 (xpost.js 에는 바깥 주소를 두지 않음)
REPLY_INTENT = "https://x.com/intent/tweet?in_reply_to="  # + 글 번호(주인이 넣은 X 글 주소의 status 숫자) + '&text=' + 답글 (화면이 붙임)
RULES = "두 계정에 같은 글 금지 · 자동 좋아요·답글 안 함 · 손으로 올리기"
IDEA_NOTE = "직접 쓰는 글 — 숫자는 실제 값만"
ETA_MAX_DAYS = 3 * 365
POSTED_LABEL = "X에 올린 글 (올렸어요 누른 것)"  # 실제로 올라갔는지는 모름 (X API 를 안 씀) — 주인이 누른 수


def posted_line(post_counts: dict[str, Any] | None, tz: ZoneInfo, now: float) -> str:
    """'X에 올린 글 (올렸어요 누른 것) 오늘 N · 7일 N · 30일 N' (주인 10/25 — Blog-Auto post_counts.channels.x). 기록이 없으면 '— 기록 없음'."""
    days = ((post_counts or {}).get("channels") or {}).get("x")
    if days is None:
        return f"{POSTED_LABEL} — 기록 없음 · 블로그 프로그램이 아직 안 보내요"
    p = post_count(days, datetime.fromtimestamp(now, tz).date())
    return f"{POSTED_LABEL} 오늘 {p.today} · 7일 {p.d7} · 30일 {p.d30}"


def _md(ts: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(ts, tz)
    return f"{d.year}/{d.month}/{d.day}" if d.year != datetime.now(tz).year else f"{d.month}/{d.day}"


def eta(rows: list[dict[str, Any]], key: str, goal: int, tz: ZoneInfo) -> str:
    """지금 속도면 언제 goal 에 닿나 — 마지막 두 기록의 직선. '달성' · '12/3쯤' · '늘지 않음' · '' (기록 하나뿐)."""
    if not rows:
        return ""
    cur = rows[-1]
    if cur[key] >= goal:
        return "달성"
    if len(rows) < 2:
        return ""
    prev = rows[-2]
    dt, dv = cur["at"] - prev["at"], cur[key] - prev[key]
    if dt <= 0 or dv <= 0:
        return "늘지 않음"
    left = (goal - cur[key]) / dv * dt
    if left > ETA_MAX_DAYS * 86400:
        return "3년 넘게"
    return f"{_md(cur['at'] + left, tz)}쯤"


def build(xv: dict[str, Any] | None, rows: list[dict[str, Any]], handles: dict[str, str], tz: ZoneInfo, now: float,
          act: Callable[..., dict[str, Any]], *, bridge_ok: bool = True, post_counts: dict[str, Any] | None = None) -> dict[str, Any]:
    """화면 값. xv = blogauto._x 로 걸러 둔 기록 파일 x 칸 (예전 Blog-Auto 면 None → 초안 없음), rows = actions.x_stats_rows,
    post_counts = blogauto._post_counts 로 걸러 둔 올린 글 수 칸 (posted_line — 초안함 밑 한 줄)."""
    cur = rows[-1] if rows else None
    progress = {
        "verified": cur["verified"] if cur else None, "verified_goal": VERIFIED_GOAL,
        "impr": cur["impressions_90d"] if cur else None, "impr_goal": IMPR_GOAL,
        "followers": cur["followers"] if cur else None,
        "at": _md(cur["at"], tz) if cur else "",
        "eta_verified": eta(rows, "verified", VERIFIED_GOAL, tz), "eta_impr": eta(rows, "impressions_90d", IMPR_GOAL, tz),
        "note": "" if len(rows) >= 2 else "숫자를 두 번(일주일 간격) 넣으면 예상 달성일이 나와요",
    }
    drafts, posted = [], []
    for d in (xv or {}).get("drafts") or []:
        if d["status"] == "dropped":
            continue
        row = {"pid": d["pid"], "title": d["title"], "url": d["url"], "text": d["text"], "status": d["status"], "shop": d["shop"],
               "posted_at": d.get("posted_at") or "", "reply": d.get("reply") or ""}
        if d["status"] == "posted":
            posted.append(row)
            continue
        args = {"pid": d["pid"]}
        row.update(posted_act=act("blog.x_posted", "올렸어요", args, disabled=not bridge_ok or None),
                   drop=act("blog.x_drop", "버리기", args, disabled=not bridge_ok or None),
                   redo=act("blog.x_redo", "다시 쓰기", args, disabled=not bridge_ok or None))
        drafts.append(row)
    return {
        "progress": progress, "save": act("x.stats", "저장", {}), "handles": handles, "save_handles": act("x.handles", "저장", {}),
        "drafts": drafts, "posted": posted, "ideas": (xv or {}).get("ideas") or [], "idea_note": IDEA_NOTE, "plan_n": plan_n(xv),
        "limit": (xv or {}).get("limit") or 280, "url_len": (xv or {}).get("url_len") or 23, "intent": INTENT, "reply_intent": REPLY_INTENT,
        "rules": RULES, "ready": xv is not None, "bridge_ok": bridge_ok, "posted_line": posted_line(post_counts, tz, now),
        "empty": "블로그 프로그램이 아직 X 초안을 안 보내요 — 업데이트를 기다리는 중이에요" if xv is None
        else "새 초안이 없어요 — 데런픽 글이 발행되면 하루 3편까지 만들어요",
    }


def plan_n(xv: dict[str, Any] | None) -> int:
    """수익 계정 2주 계획 글 수 — 계획 자체는 첫 상태 값에 넣지 않는다 (상태 104 KB 예산 · 화면이 열릴 때 /dashboard/api/xplan 으로 받음)."""
    return sum(len(d["posts"]) for d in ((xv or {}).get("plan") or {}).get("days") or [])


TODO_IID = "x-stats"
TODO_EVERY_D = 7  # 주인 10/25 '알림 띄워 주라' — 마지막으로 넣은 지 이만큼 지나면 할 일


def todo_item(rows: list[dict[str, Any]], handles: dict[str, str], now: float,
              goto: Callable[..., dict[str, Any]]) -> dict[str, Any] | None:
    """할 일 한 줄 'X 숫자 넣기' — X 를 쓰기 시작했으면(계정 아이디를 넣었거나 숫자를 한 번이라도 넣음) 한 번도 안 넣었거나
    마지막으로 넣은 지 TODO_EVERY_D 일 지났을 때. 넣으면 저절로 빠짐."""
    if not rows and not any((handles or {}).values()):
        return None
    last = rows[-1]["at"] if rows else None
    if last is not None and now - last < TODO_EVERY_D * 86400:
        return None
    return {"iid": TODO_IID, "src": "blog", "blog": "", "kind": "x_stats", "sev": "todo", "urgent": False,
            "title": "X 숫자 넣기 (일주일에 한 번)",
            "status": "주급 계정 X 앱에서 전체 팔로워 · 인증된 팔로워 · 크리에이터 스튜디오 90일 노출 → X(트위터) 화면에 넣기",
            "since": (last + TODO_EVERY_D * 86400) if last is not None else None, "expires_at": None,
            "primary": goto("넣기", "#/blog/x"), "more": [], "detail": {"lines": []}}
