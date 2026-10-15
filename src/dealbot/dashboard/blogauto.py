"""Blog-Auto 가 한 시간마다 쓰는 활동 기록 파일(var/metrics/blogauto.json) 읽기.

Blog-Auto 는 자기 DB·kv 만 보고 이 파일을 쓴다 (네이버에 요청하지 않음). 딜봇도 이 파일만 읽는다 —
네이버 블로그 통계·방문자·이웃 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다 (이웃 수·방문자는 주인이 직접 입력).

파일 모양 (docs/DASHBOARD.md):
    {"written_at": "2026-10-14T18:00:05+09:00",
     "blogs": {"finance": {"posts_published_today": 1, "posts_published_total": 23, "drafts_saved_today": 2,
                           "neighbor_requests_today": 10, "neighbor_requests_7d": 55,
                           "comments_today": 3, "replies_today": 2}, ...}}
이름이 조금 달라도(중첩 {"posts": {"published_today": 1}} 등) 읽히게 별칭을 둔다.

'발행 대기 글' 카드용 "waiting" (Blog-Auto 가 더한 값, 없어도 나머지는 그대로 읽힘 — 예전 Blog-Auto 와도 맞음):
    "waiting": {"threshold": 70, "blogs": {
        "finance": {"count": 4, "popular": 1, "top": [{"title": "…", "score": 82,
                    "saved_at": "2026-10-15T09:12:00+09:00", "category": "MONEY"}]}, "issue": {...}, "hotdeal": {...}}}
count = 봇이 네이버 임시저장으로 올려 두고 아직 발행·버림·삭제되지 않은 글 (주인이 고친 임시저장 글도 셈),
popular = 그중 score(0~100 인기 예상) 가 threshold 이상인 글 수, top = 점수 높은 순(같으면 최근 저장 순) 최대 5개.
세지 못한 블로그는 blogs 에 없다 → 카드의 그 줄은 '판단 불가'.
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
WAITING_TITLE_MAX = 60  # Blog-Auto 가 60자로 잘라 보내지만 한 번 더
WAITING_TOP_MAX = 5
WAITING_CATEGORY_MAX = 20


@dataclass(slots=True)
class WaitingPost:
    title: str
    score: int | None  # 0~100 인기 예상 (모르면 None)
    saved_at: datetime | None = None
    category: str | None = None


@dataclass(slots=True)
class WaitingBlog:
    count: int
    popular: int | None  # 인기 예상(점수 ≥ threshold) 글 수, 모르면 None
    top: list[WaitingPost] = field(default_factory=list)


@dataclass(slots=True)
class WaitingExport:
    threshold: int | None
    blogs: dict[str, WaitingBlog] = field(default_factory=dict)  # Blog-Auto 블로그 이름(소문자) → 대기 글


@dataclass(slots=True)
class BlogAutoExport:
    status: str
    note: str | None = None
    written_at: datetime | None = None
    blogs: dict[str, dict[str, float]] = field(default_factory=dict)  # 이름(소문자) → 지표
    waiting: WaitingExport | None = None  # 파일에 "waiting" 이 없거나 모양이 다르면 None ('발행 대기 글' 은 판단 불가)

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


def _count(v: Any, *, top: int | None = None) -> int | None:
    """0 이상의 정수 (12.0 도 받음, 참·거짓·글자·음수·무한은 None). top 이 있으면 그보다 크면 None."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    if isinstance(v, float) and not (math.isfinite(v) and v.is_integer()):
        return None
    n = int(v)
    if n < 0 or (top is not None and n > top):
        return None
    return n


def _one_line(v: Any, limit: int) -> str | None:
    if not isinstance(v, str):
        return None
    text = " ".join(v.split())
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _waiting_post(v: Any, tz: str) -> WaitingPost | None:
    if not isinstance(v, dict):
        return None
    title = _one_line(v.get("title"), WAITING_TITLE_MAX)
    if title is None:
        return None
    return WaitingPost(title=title, score=_count(v.get("score"), top=100), saved_at=_parse_time(v.get("saved_at"), tz),
                       category=_one_line(v.get("category"), WAITING_CATEGORY_MAX))


def _post_order(p: WaitingPost) -> tuple[int, float]:
    """점수 높은 순, 같으면 최근 저장 순 (점수·시각을 모르면 뒤로)."""
    return (-(p.score if p.score is not None else -1), -(p.saved_at.timestamp() if p.saved_at else float("-inf")))


def _waiting_blog(v: Any, tz: str) -> WaitingBlog | None:
    """블로그 하나. count 를 모르면 None (그 블로그 줄은 '판단 불가')."""
    if not isinstance(v, dict):
        return None
    count = _count(v.get("count"))
    if count is None:
        return None
    popular = _count(v.get("popular"))
    raw_top = v.get("top")
    posts = [p for p in (_waiting_post(x, tz) for x in (raw_top[:50] if isinstance(raw_top, list) else [])) if p]
    posts.sort(key=_post_order)
    return WaitingBlog(count=count, popular=min(popular, count) if popular is not None else None,
                       top=posts[: min(WAITING_TOP_MAX, count)])


def parse_waiting(data: Any, tz: str) -> WaitingExport | None:
    """파일의 "waiting" → WaitingExport. 없거나 모양이 다르면 None. 블로그마다 따로 걸러서 이상한 블로그만 빠진다."""
    if not isinstance(data, dict):
        return None
    raw = data.get("blogs")
    blogs: dict[str, WaitingBlog] = {}
    if isinstance(raw, dict):
        for name, v in raw.items():
            if isinstance(name, str) and name.strip() and (b := _waiting_blog(v, tz)) is not None:
                blogs.setdefault(name.strip().lower(), b)
    elif raw is not None:
        return None
    return WaitingExport(threshold=_count(data.get("threshold"), top=100), blogs=blogs)


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
    return BlogAutoExport(OK, written_at=written, blogs=blogs, waiting=parse_waiting(data.get("waiting"), tz))


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
                waiting=parsed.waiting,
            )
        return parsed
