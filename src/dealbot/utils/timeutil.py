from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def local_now(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def fmt_local(dt: datetime | None, tz: str, fmt: str = "%m-%d %H:%M") -> str:
    if dt is None:
        return "-"
    return dt.astimezone(ZoneInfo(tz)).strftime(fmt)


def in_time_window(now_local: datetime, spec: str | None) -> bool:
    """spec = "HH:MM-HH:MM" (로컬 시각). 자정을 넘는 구간("23:00-07:00")도 됨. 비어 있으면 False."""
    if not spec:
        return False
    try:
        start_s, end_s = spec.replace(" ", "").split("-")
        sh, sm = (int(x) for x in start_s.split(":"))
        eh, em = (int(x) for x in end_s.split(":"))
    except ValueError as e:
        raise ValueError(f"시간 구간 형식이 잘못됐습니다: {spec!r} (예: 00:00-07:00)") from e
    start, end, cur = sh * 60 + sm, eh * 60 + em, now_local.hour * 60 + now_local.minute
    if start == end:
        return False
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def humanize_delta(delta: timedelta) -> str:
    secs = int(abs(delta.total_seconds()))
    if secs < 60:
        return f"{secs}초"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}분"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours}시간 {mins}분"
    days, hours = divmod(hours, 24)
    return f"{days}일 {hours}시간"


def next_daily_time(now_local: datetime, hhmm: str) -> datetime:
    """오늘/내일 중 다음 HH:MM (now 와 같은 tz)."""
    hh, mm = (int(x) for x in hhmm.split(":"))
    candidate = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now_local:
        candidate += timedelta(days=1)
    return candidate
