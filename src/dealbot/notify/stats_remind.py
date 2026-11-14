"""'통계 보내기' 책갈피 알림 (주인 11/14 — "홈판 책갈피도 알림 오게"): 할 일 줄 대신 하루 한 번 앱 알림.

매일 21:30 (앱 시간대, 그날 글이 다 나간 뒤) 뒤 처음 도는 때, 그날 책갈피로 받은 통계(kv stats_capture:<날>:…)가
없는 블로그가 있으면 한 통: '오늘 통계 보내기 — 아이패드 사파리 네이버 내 통계에서 책갈피 (세 블로그)' —
일부만 왔으면 '(아직: 라커룸·드라마)'. 세 블로그가 다 왔으면 없음. 할 일 줄·카톡 없음 · 기기가 없으면 아무것도 안 보냄.
하루 한 번은 kv stats_remind:day 로 (1분 일이 몇 번 돌아도). 책갈피 설치·받기는 그대로 (dashboard/statscap.py)."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import statscap

log = logging.getLogger(__name__)

KV_DAY = "stats_remind:day"
HOUR, MINUTE = 21, 30
BLOGS = ("hotdeal", "finance", "issue")  # 라커룸 · 드라마 · 데런픽
URL = "/dashboard/#/connect"


def missing(db: Any, now: float, tz: ZoneInfo) -> list[str]:
    """오늘 책갈피로 통계가 안 온 블로그 (BLOGS 순서)."""
    day = datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
    got: set[str] = set()
    for _k, raw in db.kv_items(f"{statscap.PREFIX}{day}:"):
        try:
            blog = (json.loads(raw or "{}") or {}).get("blog")
        except ValueError:
            continue
        if isinstance(blog, str):
            got.add(blog)
    return [b for b in BLOGS if b not in got]


def message(left: list[str]) -> tuple[str, str]:
    who = "세 블로그" if len(left) >= len(BLOGS) else "아직: " + "·".join(statscap.BLOG_NAMES[b] for b in left)
    return "오늘 통계 보내기", f"오늘 통계 보내기 — 아이패드 사파리 네이버 내 통계에서 책갈피 ({who})"


async def check(bot: Any, now: float | None = None) -> str | None:
    """1분 일에서 부름. 보냈으면 한 줄 설명, 아니면 None."""
    router = getattr(bot, "router", None)
    if router is None:
        return None
    now = now or time.time()
    tz = ZoneInfo(bot.settings.app.timezone)
    t = datetime.fromtimestamp(now, tz)
    if (t.hour, t.minute) < (HOUR, MINUTE):
        return None
    day = t.strftime("%Y-%m-%d")
    if bot.db.kv_get(KV_DAY) == day:
        return None
    left = missing(bot.db, now, tz)
    bot.db.kv_set(KV_DAY, day)  # 먼저 적는다 — 하루 한 번
    if not left:
        return None
    title, body = message(left)
    sent = await router.push_event(title, body, url=URL, tag="stats-remind")
    log.info("stats reminder: %s — pushed to %d device(s)", body, sent)
    return f"{body} (알림 {sent}대)"
