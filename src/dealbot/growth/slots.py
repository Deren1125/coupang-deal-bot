"""하루 한 번, 시간대 안의 '아무 분'에 도는 일 (답글 모으기 12:30·20:30대, 일요일 20시대 주간 글 등).

분은 날짜·일 이름으로 정해져(난수 씨앗) 재시작해도 같은 날엔 같은 분이다. 그날 이미 돌았으면(kv 표식) 다시 안 돈다.
시간대가 지난 뒤에 켜졌으면 그날은 건너뛴다 (밤늦게 점심 답글 묶음이 오는 일 없이).
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dealbot.utils.timeutil import to_iso


def _hm(s: str) -> int:
    hh, _, mm = s.strip().partition(":")
    return int(hh) * 60 + int(mm)


def window_minutes(window: str) -> tuple[int, int]:
    """'HH:MM-HH:MM' → (시작 분, 끝 분) — 끝 분은 포함 (24:00 은 1439)."""
    start, _, end = window.partition("-")
    return _hm(start), min(_hm(end), 24 * 60 - 1)


def slot_time(key: str, day: date, window: str, tz: str) -> datetime:
    """그날 이 일이 도는 시각 (UTC). 시간대 안의 분 하나를 날짜·이름으로 고른다."""
    lo, hi = window_minutes(window)
    minute = random.Random(f"{key}|{day.isoformat()}|{window}").randint(lo, max(lo, hi))
    local = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(tz)) + timedelta(minutes=minute)
    return local.astimezone(ZoneInfo("UTC"))


def slot_due(db, key: str, window: str, now: datetime, tz: str) -> bool:  # type: ignore[no-untyped-def]
    """지금 이 일을 돌릴 때인가: 오늘 고른 분이 지났고, 시간대가 아직 안 끝났고, 오늘 안 돌았음."""
    local = now.astimezone(ZoneInfo(tz))
    day = local.date()
    if db.kv_get(f"slot:{key}") == day.isoformat():
        return False
    lo, hi = window_minutes(window)
    cur = local.hour * 60 + local.minute
    if cur > hi or cur < lo:
        return False
    return now >= slot_time(key, day, window, tz)


def mark_slot(db, key: str, now: datetime, tz: str) -> None:  # type: ignore[no-untyped-def]
    db.kv_set(f"slot:{key}", now.astimezone(ZoneInfo(tz)).date().isoformat())
    db.kv_set(f"slot_at:{key}", to_iso(now))
