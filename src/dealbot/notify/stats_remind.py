"""'통계 보내기' 책갈피 알림 (주인 11/14 — "홈판 책갈피도 알림 오게"): 할 일 줄 대신 하루 한 번 앱 알림.

매일 21:30 (앱 시간대, 그날 글이 다 나간 뒤) 뒤 처음 도는 때, 그날 책갈피로 받은 통계(kv stats_capture:<날>:…)가
없는 블로그가 있으면 한 통: '오늘 통계 보내기 — 아이패드 사파리 네이버 내 통계에서 책갈피 (세 블로그)' —
일부만 왔으면 '(아직: 라커룸·드라마)'. 세 블로그가 다 왔으면 없음. 할 일 줄·카톡 없음 · 기기가 없으면 아무것도 안 보냄.
하루 한 번은 kv stats_remind:day 로 (1분 일이 몇 번 돌아도). 책갈피 설치·받기는 그대로 (dashboard/statscap.py).
주인 11/14 점검 13: 'day' 는 기기 한 대 이상에 알림이 간 뒤에만 적는다 — 기기 0대·오류면 23:30 까지 10분마다 다시
(kv stats_remind:tried = 마지막으로 해 본 시각, 1분 일이 매분 보내지 않게). 블로그를 못 알아본 기록(blog None, '블로그 모름')이
있으면 '아무것도 안 왔다'고 하지 않고 '블로그를 못 알아본 기록 n건 — 통계 화면에서 다시' 로."""
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
KV_TRIED = "stats_remind:tried"  # 마지막으로 보내 본 시각 (epoch) — 못 보냈을 때 10분마다만 다시
HOUR, MINUTE = 21, 30
LAST = (23, 30)  # 이때까지 못 보냈으면 그날은 그만
RETRY_EVERY = 600
BLOGS = ("hotdeal", "finance", "issue")  # 라커룸 · 드라마 · 데런픽
URL = "/dashboard/#/connect"


def _scan(db: Any, now: float, tz: ZoneInfo) -> tuple[set[str], int]:
    """오늘 받은 기록: (알아본 블로그들, 블로그를 못 알아본 기록 수)."""
    day = datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
    got: set[str] = set()
    unknown = 0
    for _k, raw in db.kv_items(f"{statscap.PREFIX}{day}:"):
        try:
            rec = json.loads(raw or "{}") or {}
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        blog = rec.get("blog")
        if isinstance(blog, str) and blog:
            got.add(blog)
        else:
            unknown += 1
    return got, unknown


def missing(db: Any, now: float, tz: ZoneInfo) -> list[str]:
    """오늘 책갈피로 통계가 안 온 블로그 (BLOGS 순서)."""
    got, _unknown = _scan(db, now, tz)
    return [b for b in BLOGS if b not in got]


def unknown_count(db: Any, now: float, tz: ZoneInfo) -> int:
    """오늘 받았지만 어느 블로그인지 못 알아본 기록 수 (statscap 'blog': None — '블로그 모름')."""
    return _scan(db, now, tz)[1]


def message(left: list[str], unknown: int = 0) -> tuple[str, str]:
    names = "·".join(statscap.BLOG_NAMES[b] for b in left)
    if unknown:
        # 보낸 건 있음 — '세 블로그'·'아직' 이라고 하면 틀린 말 (주인 11/14 점검 13)
        rest = f" (확인된 것 빼고: {names})" if len(left) < len(BLOGS) else ""
        return "오늘 통계 다시 보내기", f"블로그를 못 알아본 기록 {unknown}건 — 통계 화면에서 다시 책갈피{rest}"
    who = "세 블로그" if len(left) >= len(BLOGS) else "아직: " + names
    return "오늘 통계 보내기", f"오늘 통계 보내기 — 아이패드 사파리 네이버 내 통계에서 책갈피 ({who})"


async def check(bot: Any, now: float | None = None) -> str | None:
    """1분 일에서 부름. 보냈으면 한 줄 설명, 아니면 None."""
    router = getattr(bot, "router", None)
    if router is None:
        return None
    now = now or time.time()
    tz = ZoneInfo(bot.settings.app.timezone)
    t = datetime.fromtimestamp(now, tz)
    if (t.hour, t.minute) < (HOUR, MINUTE) or (t.hour, t.minute) >= LAST:
        return None
    day = t.strftime("%Y-%m-%d")
    if bot.db.kv_get(KV_DAY) == day:
        return None
    try:
        tried = float(bot.db.kv_get(KV_TRIED) or 0)
    except ValueError:
        tried = 0.0
    if 0 <= now - tried < RETRY_EVERY:
        return None
    got, unknown = _scan(bot.db, now, tz)
    left = [b for b in BLOGS if b not in got]
    if not left:
        bot.db.kv_set(KV_DAY, day)  # 다 왔음 — 오늘은 끝
        return None
    bot.db.kv_set(KV_TRIED, str(now))  # 먼저 적는다 — 보내다 오류가 나도 10분 뒤에야 다시
    title, body = message(left, unknown)
    try:
        sent = int(await router.push_event(title, body, url=URL, tag="stats-remind") or 0)
    except Exception as e:  # noqa: BLE001 — 10분 뒤 다시 (23:30 까지)
        log.info("stats reminder push failed: %s", type(e).__name__)
        return None
    log.info("stats reminder: %s — pushed to %d device(s)", body, sent)
    if sent < 1:
        return None  # 받은 기기가 없음 — day 를 안 적어 10분 뒤 다시
    bot.db.kv_set(KV_DAY, day)
    return f"{body} (알림 {sent}대)"
