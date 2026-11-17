"""키워드로 글 쓰기 결과 줄 (주인 11/17 아침 — "인기 키워드·막 뜨는 중 누르면 글 쓰기 눌렀는데 글이 안 써짐").

원인 (Blog-Auto server-status dash_actions 11/17 08:03~08:13): 일은 Blog-Auto 에 갔다. AI 주간 한도로 error/failed
('AI 사용량이 다 차서 08:13 이후에 다시 눌러 주세요') ×4 · 라커룸 「양의지」「두산 베어스」는 rejected/not_found ('기사로는 규칙에 맞는
이야기를 못 찾았어요'). 그런데 화면은 code 뜻말만 3초 띄우고(failed → '하지 못했어요', not_found → '이미 없는 항목이에요') 결과가 남지
않았다. 할 일 탭 '⚡ 막 뜨는 중'(routine.js)은 결과를 아예 보지 않았다.

→ 오늘 누른 키워드 글쓰기(blog.issue · blog.channel_topic)의 마지막 결과를 키워드마다 한 줄로: 보내는 중 · 쓰는 중 · 완료(어디서 보는지) ·
  못 씀(Blog-Auto 가 쓴 까닭 그대로 — AI 한도면 풀리는 시각). 화면은 static/kwrun.js 가 그 단추 옆에 붙인다 (app.js 는 크기 한도 끝).

  GET /dashboard/api/kwruns   {"runs": [{kw, action, blog, state, text, at, go}]} (오늘 KST · 키워드마다 마지막 것 · no-store · 로그인 아래)
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

PATH = "/dashboard/api/kwruns"
ACTIONS = ("blog.issue", "blog.channel_topic")
KST = timezone(timedelta(hours=9))
SCAN = 300  # 최근 줄 이만큼에서 고른다 (하루 누르는 수보다 넉넉히)
RUNS_MAX = 40
TEXT_MAX = 160
# Blog-Auto 가 글을 안 남긴 때의 말 (code 뜻말이 아니라 키워드 글쓰기에 맞는 말)
STATE_TEXT = {
    "queued": "보냈어요 — 블로그 프로그램이 받기를 기다려요",
    "running": "쓰는 중이에요 (몇 분 걸려요)",
    "expired": "블로그 프로그램이 10분 안에 받지 않았어요 — 다시 눌러 주세요",
    "unknown": "20분 넘게 소식이 없어요 — 잠시 뒤 결과를 다시 보거나 다시 눌러 주세요",
}
CODE_TEXT = {
    "interrupted": "서버가 다시 켜지며 끊겼어요 — 다시 눌러 주세요",
    "busy": "다른 글을 쓰는 중이었어요 — 끝난 뒤 다시 눌러 주세요",
    "unsupported": "블로그 프로그램 업데이트를 기다리는 중이에요",
    "ai_limit": "AI 사용량 한도가 다 찼어요 — 한도가 풀린 뒤 다시 눌러 주세요",
}


def _day_start(now: float) -> float:
    d = datetime.fromtimestamp(now, KST)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, KST).strftime("%H:%M")


def _go(action: str, blog: str, post: dict[str, Any] | None) -> str:
    """완료된 글을 보는 곳 (화면 안 주소만)."""
    if action == "blog.channel_topic":
        return "#/todo"  # 붙여넣기 글 — 할 일 탭 '붙여넣기 대기'
    pid = (post or {}).get("pid")
    return f"#/i/post-issue-{int(pid)}" if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else "#/blog/issue"


def run_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """dash_actions 한 줄 → 결과 줄 (키워드가 없으면 None)."""
    try:
        args = json.loads(row.get("args") or "{}") or {}
        res = json.loads(row.get("result") or "null") or {}
    except ValueError:
        return None
    kw = " ".join(str(args.get("keyword") or "").split())[:40] if isinstance(args, dict) else ""
    if not kw:
        return None
    action, status = str(row["action"]), str(row["status"])
    blog = str(row.get("blog") or (args.get("blog") if isinstance(args.get("blog"), str) else "") or ("issue" if action == "blog.issue" else ""))
    code, text = str(res.get("code") or ""), " ".join(str(res.get("text") or "").split())[:TEXT_MAX]
    if status in ("queued", "running"):
        state = "run"
        text = STATE_TEXT[status] if status == "queued" or not text or text == "요청 받았어요" else f"쓰는 중 — {text}"
    elif status == "ok":
        state = "ok"
        text = text or "썼어요"
    else:
        state = "fail"
        if status in STATE_TEXT:
            text = STATE_TEXT[status]
        elif code == "interrupted" or not text:
            text = CODE_TEXT.get(code) or "쓰지 못했어요 — 잠시 뒤 다시 눌러 주세요"
    post = res.get("post") if isinstance(res.get("post"), dict) else None
    return {"kw": kw, "action": action, "blog": blog, "state": state, "text": text, "at": _hm(float(row["ts"])), "ts": float(row["ts"]),
            "go": _go(action, blog, post) if state == "ok" else ""}


def runs(store: Any, now: float) -> list[dict[str, Any]]:
    """오늘 키워드 글쓰기 결과 — (일, 블로그, 키워드)마다 마지막 것, 새것부터."""
    start = _day_start(now)
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in store.recent(SCAN):
        if row.get("action") not in ACTIONS or float(row.get("ts") or 0) < start:
            continue
        r = run_row(row)
        if r is None:
            continue
        key = (r["action"], r["blog"], r["kw"])
        if key not in out or out[key]["ts"] < r["ts"]:
            out[key] = r
    return sorted(out.values(), key=lambda r: -r["ts"])[:RUNS_MAX]


def register(web: Any, api: Any) -> None:
    """DashApi.register 가 부른다. 읽기만 (state 처럼 CSRF 없이 · 로그인 아래)."""
    from dealbot.dashboard.actions import RateWindow

    rate = RateWindow(60, 60.0)

    async def handler(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        if not rate.hit(caller.session):
            return 429, json.dumps({"status": "error", "code": "rate", "text": "너무 자주 불렀어요"}, ensure_ascii=False), \
                "application/json; charset=utf-8", {"Cache-Control": "no-store"}
        out = api.bot.actions_out
        try:
            out.ingest()  # 결과를 먼저 읽는다 (/actions 와 같이)
        except Exception:  # noqa: BLE001 — 표에 있는 것만이라도
            pass
        rows = [{k: v for k, v in r.items() if k != "ts"} for r in runs(out.store, time.time())]
        return 200, json.dumps({"runs": rows}, ensure_ascii=False, separators=(",", ":")), "application/json; charset=utf-8", \
            {"Cache-Control": "no-store"}

    web.route(PATH, handler, with_headers=True)
