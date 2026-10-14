"""Blog-Auto 가 한 시간마다 쓰는 활동 기록 파일(var/metrics/blogauto.json) 읽기.

Blog-Auto 는 자기 DB·kv 만 보고 이 파일을 쓴다 (네이버에 요청하지 않음). 딜봇도 이 파일만 읽는다 —
네이버 블로그 통계·방문자·이웃 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다 (이웃 수·방문자는 주인이 직접 입력).

파일 모양 (docs/DASHBOARD.md):
    {"written_at": "2026-10-14T18:00:05+09:00",
     "blogs": {"finance": {"posts_published_today": 1, "posts_published_total": 23, "drafts_saved_today": 2,
                           "neighbor_requests_today": 10, "neighbor_requests_7d": 55,
                           "comments_today": 3, "replies_today": 2}, ...}}
이름이 조금 달라도(중첩 {"posts": {"published_today": 1}} 등) 읽히게 별칭을 둔다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import OK, STALE, UNAVAILABLE

MAX_BYTES = 1_000_000

# 대시보드 지표 이름 → 파일에서 찾을 이름들 (중첩은 '_' 로 이어 붙여 찾음, 소문자)
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "posts_today": ("posts_published_today", "published_today", "posts_today", "published_posts_today"),
    "posts_total": ("posts_published_total", "published_total", "posts_total", "total_posts", "published_posts_total"),
    "drafts_today": ("drafts_saved_today", "drafts_today", "saved_today", "drafts_saved"),
    "neighbor_req_today": (
        "neighbor_requests_today", "neighbor_requests_sent_today", "neighbors_requests_today", "neighbors_requested_today",
        "neighbor_requests_today_sent",
    ),
    "neighbor_req_7d": (
        "neighbor_requests_7d", "neighbor_requests_sent_7d", "neighbors_requests_7d", "neighbors_requested_7d",
        "neighbor_requests_last_7d",
    ),
    "comments_today": ("comments_today", "comments_written_today", "comments_sent_today"),
    "replies_today": ("replies_today", "comment_replies_today", "comments_replies_today", "replies_written_today"),
}
_TIME_KEYS = ("written_at", "generated_at", "updated_at")


@dataclass(slots=True)
class BlogAutoExport:
    status: str
    note: str | None = None
    written_at: datetime | None = None
    blogs: dict[str, dict[str, float]] = field(default_factory=dict)  # 이름(소문자) → 지표

    @property
    def ok(self) -> bool:
        return self.status == OK

    def blog(self, *names: str | None) -> dict[str, float] | None:
        for n in names:
            if n and n.lower() in self.blogs:
                return self.blogs[n.lower()]
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int | float):
        f = float(v)
    elif isinstance(v, str):
        try:
            f = float(v.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    if math.isnan(f) or math.isinf(f) or f < 0:
        return None
    return f


def _flatten(d: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{str(k).lower()}"
        if isinstance(v, dict) and depth < 3:
            out.update(_flatten(v, key + "_", depth + 1))
        else:
            out[key] = v
    return out


def normalize(raw: dict[str, Any]) -> dict[str, float]:
    flat = _flatten(raw)
    out: dict[str, float] = {}
    for canon, names in FIELD_ALIASES.items():
        for n in names:
            num = _num(flat.get(n))
            if num is not None:
                out[canon] = num
                break
    return out


def _parse_time(v: Any, tz: str) -> datetime | None:
    if isinstance(v, int | float) and not isinstance(v, bool):
        try:
            return datetime.fromtimestamp(float(v), ZoneInfo(tz))
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))  # 시간대가 없으면 Blog-Auto 서버 시각(=app.timezone)으로
    return dt


def parse_export(data: Any, tz: str) -> BlogAutoExport:
    if not isinstance(data, dict):
        return BlogAutoExport(UNAVAILABLE, "Blog-Auto 활동 기록의 모양이 달라 읽지 못함")
    written = next((t for k in _TIME_KEYS if (t := _parse_time(data.get(k), tz)) is not None), None)
    if written is None:
        return BlogAutoExport(UNAVAILABLE, "Blog-Auto 활동 기록에 written_at 이 없어 언제 것인지 모름")
    blogs: dict[str, dict[str, float]] = {}
    raw = data.get("blogs")
    entries: list[tuple[list[Any], dict[str, Any]]] = []
    if isinstance(raw, dict):
        entries = [([name, v.get("name"), v.get("key"), v.get("blog"), v.get("blog_id")], v) for name, v in raw.items() if isinstance(v, dict)]
    elif isinstance(raw, list):
        entries = [([v.get("name"), v.get("key"), v.get("blog"), v.get("blog_id")], v) for v in raw if isinstance(v, dict)]
    for names, v in entries:
        metrics = normalize(v)
        for n in names:
            if isinstance(n, str) and n.strip():
                blogs.setdefault(n.strip().lower(), metrics)
    return BlogAutoExport(OK, written_at=written, blogs=blogs)


class BlogAutoReader:
    """파일이 바뀌었을 때만 다시 읽는다 (카드 세 개가 한 번 읽은 것을 같이 씀)."""

    def __init__(self, path: str | Path, *, tz: str, stale_hours: float, now: Callable[[], datetime]) -> None:
        self.path = Path(str(path)).expanduser()
        self.tz = tz
        self.stale_hours = stale_hours
        self.now = now
        self._cache: tuple[tuple[int, int], BlogAutoExport] | None = None

    def read(self) -> BlogAutoExport:
        try:
            st = self.path.stat()
        except OSError:
            return BlogAutoExport(UNAVAILABLE, "Blog-Auto 활동 기록 파일이 아직 없음 (Blog-Auto 가 한 시간마다 씀)")
        sig = (st.st_mtime_ns, st.st_size)
        if self._cache is not None and self._cache[0] == sig:
            parsed = self._cache[1]
        else:
            if st.st_size > MAX_BYTES:
                parsed = BlogAutoExport(UNAVAILABLE, "Blog-Auto 활동 기록 파일이 너무 큼")
            else:
                try:
                    parsed = parse_export(json.loads(self.path.read_text(encoding="utf-8")), self.tz)
                except (OSError, ValueError, UnicodeDecodeError):
                    parsed = BlogAutoExport(UNAVAILABLE, "Blog-Auto 활동 기록을 읽지 못함 (쓰는 중이거나 형식 오류)")
            self._cache = (sig, parsed)
        if parsed.status != OK or parsed.written_at is None:
            return parsed
        age_h = (self.now() - parsed.written_at).total_seconds() / 3600
        if age_h > self.stale_hours:
            return BlogAutoExport(
                STALE,
                f"Blog-Auto 활동 기록이 {age_h:.0f}시간 전 것 — Blog-Auto 가 멈췄는지 확인해 주세요",
                written_at=parsed.written_at,
                blogs=parsed.blogs,
            )
        return parsed
