"""간격 두고 발행 미리 보기 (주인 10/18 — "시간 간격 두고 올리는건 어떻개 해야해?").

Blog-Auto(blogauto/schedule.py)가 블로그마다 발행 예약 줄을 갖고 차례가 되면 발행한다. 딜봇은 확인 창에 '몇 시에 올라가는지'를
보여 주려고 **같은 규칙**으로 시각을 미리 계산하고, 확인한 시각을 그대로 넘긴다 (Blog-Auto 가 규칙에 맞는지 다시 보고, 맞으면 그 시각).
규칙·계산 순서는 두 저장소가 같아야 한다 — tests/fixtures/dash_contract/sched_plan.json.
  · 글 사이 2~3시간 무작위 (정수 초) · 08:00~22:00 (KST) 안 · 넘기면 다음 날 아침 08:00~09:30 무작위
  · 줄 끝(tail)·마지막 발행(last_pub) 뒤로 · first_now: 줄이 비었으면 첫 글은 지금 (마지막 발행에서 2시간이 안 지났으면 그 뒤)
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Rules:
    gap: tuple[int, int] = (120 * 60, 180 * 60)  # 초
    window: tuple[int, int] = (8 * 60, 22 * 60)  # 하루 몇 분째부터 몇 분째 전까지
    morning: tuple[int, int] = (8 * 60, 9 * 60 + 30)


def _mins(v: Any, default: int) -> int:
    try:
        h, m = str(v).split(":")
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return default


def rules_of(blog: dict[str, Any] | None) -> Rules:
    """기록 파일 schedule.blogs.<블로그> 의 gap_minutes·window·morning (없거나 이상하면 기본)."""
    d = Rules()
    b = blog or {}
    try:
        gm = b.get("gap_minutes") or [120, 180]
        lo, hi = sorted((max(30, int(gm[0])), max(30, int(gm[-1]))))
    except (TypeError, ValueError, IndexError):
        lo, hi = d.gap[0] // 60, d.gap[1] // 60
    win, mor = b.get("window") or [], b.get("morning") or []
    w0, w1 = _mins(win[0] if win else None, d.window[0]), _mins(win[-1] if win else None, d.window[1])
    m0, m1 = _mins(mor[0] if mor else None, d.morning[0]), _mins(mor[-1] if mor else None, d.morning[1])
    return Rules(gap=(lo * 60, hi * 60), window=(w0, w1) if w0 < w1 else d.window, morning=(m0, max(m0, m1)))


def local(t: float) -> datetime:
    return datetime.fromtimestamp(t, KST)


def _minute_of_day(t: float) -> float:
    d = local(t)
    return d.hour * 60 + d.minute + d.second / 60


def in_window(t: float, r: Rules) -> bool:
    return r.window[0] <= _minute_of_day(t) < r.window[1]


def morning_of(day: date, r: Rules, rng: Any) -> float:
    start = datetime(day.year, day.month, day.day, tzinfo=KST) + timedelta(minutes=r.morning[0])
    return start.timestamp() + float(rng.randint(0, (r.morning[1] - r.morning[0]) * 60))


def fit(t: float, r: Rules, rng: Any) -> float:
    if in_window(t, r):
        return t
    d = local(t)
    day = d.date() if _minute_of_day(t) < r.window[0] else d.date() + timedelta(days=1)
    return morning_of(day, r, rng)


def plan(n: int, *, now: float, r: Rules, rng: Any, tail: float | None = None, last_pub: float | None = None,
         first_now: bool = False) -> list[float]:
    """n 편의 발행 시각 (Blog-Auto schedule.plan 과 같은 계산 · 같은 무작위 순서: 글마다 간격 → 아침이면 아침 시각)."""
    out: list[float] = []
    prev = tail
    for i in range(n):
        g = float(rng.randint(r.gap[0], r.gap[1]))
        if i == 0 and first_now and tail is None:
            cand = now if last_pub is None or now - last_pub >= r.gap[0] else last_pub + g
        else:
            base = max(x for x in (now, prev, last_pub if i == 0 else None) if x is not None)
            cand = base + g
        at = fit(cand, r, rng)
        out.append(at)
        prev = at
    return out


def seeded(*parts: Any) -> random.Random:
    """확인 창을 열 때마다(첫 글은 지금 켜고 끄기) 시각이 바뀌지 않게 — 같은 입력이면 같은 무작위 (10분 단위)."""
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def when_text(t: float, now: float) -> str:
    """'오늘 14:20' · '내일 08:47' · '10/21 08:47' (KST)."""
    d, n = local(t), local(now)
    if d.date() == n.date():
        return f"오늘 {d:%H:%M}"
    if d.date() == n.date() + timedelta(days=1):
        return f"내일 {d:%H:%M}"
    return f"{d.month}/{d.day} {d:%H:%M}"


def iso_ts(v: Any) -> float | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=KST)).timestamp()
