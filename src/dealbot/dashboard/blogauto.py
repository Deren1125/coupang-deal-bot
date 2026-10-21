"""Blog-Auto 가 한 시간마다 쓰는 활동 기록 파일(var/metrics/blogauto.json) 읽기.

Blog-Auto 는 자기 DB·kv (자기 통계 own_stats 는 하루 한 번 따로 읽어 둔 값) 만 보고 이 파일을 쓴다.
딜봇은 이 파일만 읽는다 — 딜봇(서버)은 네이버에 한 번도 요청하지 않는다.

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

블로그 카드용 "own_stats" (주인 결정 10/15 — Blog-Auto 가 주인 블로그 세 개만, 블로그마다 자기 로그인 브라우저로
하루 한 번 날마다 다른 시각에 자기 통계·이웃 수를 읽어 넣음. 없거나 어떤 블로그만 빠져도 나머지는 그대로 읽힘):
    "own_stats": {"finance": {"day": "2026-10-15", "read_at": "2026-10-15T14:23:05+09:00", "neighbors": 183,
        "visitors_today": 2, "visitors_total": 2751, "views_today": 5, "likes_today": 5, "comments_today": 2,
        "neighbor_change_today": 0, "buddy_requests_pending": 2,
        "yesterday": {"day": "2026-10-14", "views": 16, "likes": 3, "comments": 1, "neighbor_change": 1} | null,
        "prev": {"day": "2026-10-14", "neighbors": 182, "visitors_total": 2735} | null}, "issue": {...}, "hotdeal": {...}}
어느 칸이든 null 일 수 있다 (그 칸만 '-'). '오늘' 숫자는 read_at 까지의 값 (하루 중간에 읽은 것).

대시보드 맨 위 '지금 할 일' 용 "todo" (주인 요청 10/15 — Blog-Auto 알림 목록에서 아직 열린 일 + 대기 글·서로이웃 신청 같은 상태.
없어도 나머지는 그대로 읽힘):
    "todo": {"generated_at": "2026-10-15T14:30:05+09:00", "items": [
        {"key": "draft_ready:issue:31", "marker": "🔔[할 일]", "blog": "issue", "kind": "draft_ready", "short": "≤30자 요약",
         "text": "첫 줄 무엇\n→ 어떻게 (두 줄 이하)", "since": "…", "urgent": false, "link": "https://…" | "", "group": "", "count": 1}]}
글은 카톡 메모와 같은 주인용 문구. 항목마다 따로 걸러서 이상한 항목만 버린다 (링크는 https 만, 토큰 모양 글자는 가림).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import OK, STALE, UNAVAILABLE, https_url, safe_text

MAX_BYTES = 4_000_000  # v2 (글·사진 자리·질문까지) — 4MB 까지

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
OWN_MAX = 1_000_000_000  # 자기 통계 숫자가 이보다 크면 잘못 읽은 것으로 보고 그 칸만 버림
# '지금 할 일' 표시 (Blog-Auto notify.SEVERITY 순서 + 참고). 모르는 표시는 글 첫머리로 찾고, 그래도 없으면 🔔 (Blog-Auto 와 같게)
TODO_MARKERS = ("⛔[멈춤]", "🔥[핫]", "⚠️[할 일]", "🔔[할 일]", "ℹ️")
TODO_DEFAULT_MARKER = "🔔[할 일]"
_MARKER_ALIASES = {"⚠[할 일]": "⚠️[할 일]", "ℹ": "ℹ️"}  # 이모지 뒤 VS16(️) 이 빠진 모양
TODO_MAX_ITEMS = 100
TODO_SHORT_MAX = 40  # Blog-Auto 는 30자 안으로 보냄 — 한 번 더
TODO_LINE_MAX = 140  # 글 한 줄 (카톡 한 통이 200자)
TODO_HOW_LINES = 2  # '→ 어떻게' 는 두 줄까지
TODO_KEY_MAX = 120
TODO_COUNT_MAX = 100_000


@dataclass(slots=True)
class WaitingPost:
    title: str
    score: int | None  # 0~100 인기 예상 (모르면 None)
    saved_at: datetime | None = None
    category: str | None = None
    id: int | None = None  # Blog-Auto 글 번호 (v2 부터 읽음 — 대시보드 글 항목 post-<블로그>-<번호>)


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
class OwnDay:
    """읽은 날의 전날 하루 전체 (통계 '일간 현황' 에서 날짜를 한 칸 뒤로 넘긴 값)."""

    day: date
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    neighbor_change: int | None = None  # 이웃증감수 (줄면 음수)


@dataclass(slots=True)
class OwnPrev:
    """그 앞에 읽은 값 (보통 어제) — 이웃 수 증감을 셀 때."""

    day: date
    neighbors: int | None = None
    visitors_total: int | None = None


@dataclass(slots=True)
class OwnStats:
    """Blog-Auto 가 주인 블로그 하나를 하루 한 번(날마다 다른 시각) 읽은 자기 통계. 숫자 칸은 모르면 None."""

    day: date  # 읽은 날 (app.timezone)
    read_at: datetime | None  # 읽은 시각 (모르면 None — 그날 언제인지 모름)
    neighbors: int | None = None
    visitors_today: int | None = None  # 읽은 시각까지의 그날 방문자
    visitors_total: int | None = None
    views_today: int | None = None  # 읽은 시각까지의 그날 조회수
    likes_today: int | None = None
    comments_today: int | None = None  # 블로그에 달린 댓글 (봇이 쓴 댓글이 아님)
    neighbor_change_today: int | None = None
    buddy_requests_pending: int | None = None  # 받은 서로이웃 신청 중 아직 수락·거절하지 않은 것
    yesterday: OwnDay | None = None
    prev: OwnPrev | None = None


@dataclass(slots=True)
class TodoItem:
    """Blog-Auto 가 보낸 '지금 할 일' 한 줄 (카톡 메모와 같은 주인용 문구를 걸러 둔 것)."""

    key: str  # 같은 일 (Blog-Auto 알림 키 — 딜봇 일은 link:31·review:12·unsure:5·publish_broken 처럼 딜봇이 준 키 그대로)
    marker: str  # TODO_MARKERS 중 하나
    short: str  # 한 줄 요약 (≤30자)
    what: str = ""  # 글 첫 줄에서 표시를 뺀 것
    how: str = ""  # '→ 어떻게' (두 줄까지, 줄은 \n)
    since: datetime | None = None
    urgent: bool = False
    blog: str = ""  # finance | issue | hotdeal | dealbot | ''
    kind: str = ""
    link: str | None = None  # https 만 (아니면 None)
    group: str = ""
    count: int = 1


@dataclass(slots=True)
class TodoExport:
    generated_at: datetime | None
    items: list[TodoItem] = field(default_factory=list)


@dataclass(slots=True)
class BlogAutoExport:
    status: str
    note: str | None = None
    written_at: datetime | None = None
    blogs: dict[str, dict[str, float]] = field(default_factory=dict)  # 이름(소문자) → 지표
    waiting: WaitingExport | None = None  # 파일에 "waiting" 이 없거나 모양이 다르면 None ('발행 대기 글' 은 판단 불가)
    own_stats: dict[str, OwnStats] = field(default_factory=dict)  # 이름(소문자)·blog_id → 자기 통계 (없으면 직접 입력만)
    todo: TodoExport | None = None  # 파일에 "todo" 가 없거나 모양이 다르면 None ('지금 할 일' 은 딜봇 것만 + 안내)
    version: int = 1
    # v2 (주인 10/18 대시보드 앱): posts·recent·scan·status·digest — 걸러 둔 사전 (parse_v2). 없으면 빈 사전
    v2: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == OK

    def blog(self, *names: str | None) -> dict[str, float] | None:
        for n in names:
            if n and n.lower() in self.blogs:
                return self.blogs[n.lower()]
        return None

    def own(self, *names: str | None) -> OwnStats | None:
        for n in names:
            if isinstance(n, str) and n.lower() in self.own_stats:
                return self.own_stats[n.lower()]
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
    pid = _count(v.get("id"), top=10**9)
    return WaitingPost(title=title, score=_count(v.get("score"), top=100), saved_at=_parse_time(v.get("saved_at"), tz),
                       category=_one_line(v.get("category"), WAITING_CATEGORY_MAX), id=pid if pid else None)


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


_INT_TEXT = re.compile(r"-?[0-9]{1,3}(?:,[0-9]{3})+|-?[0-9]+")


def _int(v: Any, *, signed: bool = False) -> int | None:
    """자기 통계 숫자 한 칸: 정수(12.0·'2,751' 도 받음). 참·거짓·소수·무한·OWN_MAX 넘는 수는 None, signed 가 아니면 음수도 None."""
    if isinstance(v, str) and _INT_TEXT.fullmatch(v.strip()):
        v = int(v.strip().replace(",", ""))
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    if isinstance(v, float) and not (math.isfinite(v) and v.is_integer()):
        return None
    n = int(v)
    if abs(n) > OWN_MAX or (n < 0 and not signed):
        return None
    return n


_DAY_TEXT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _day(v: Any) -> date | None:
    """'YYYY-MM-DD' 만 (그 밖의 모양·없는 날짜는 None)."""
    if not isinstance(v, str) or not _DAY_TEXT.fullmatch(v.strip()):
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def _own_stats(v: Any, tz: str) -> OwnStats | None:
    """블로그 하나. 읽은 날(day, 없으면 read_at 의 날)을 모르거나 숫자가 하나도 없으면 None (그 카드는 직접 입력만)."""
    if not isinstance(v, dict):
        return None
    read_at = _parse_time(v.get("read_at"), tz)
    day = _day(v.get("day")) or (read_at.astimezone(ZoneInfo(tz)).date() if read_at else None)
    if day is None:
        return None
    if read_at is not None and read_at.astimezone(ZoneInfo(tz)).date() != day:
        read_at = None  # 날짜와 시각이 어긋나면 시각은 버린다 (어느 쪽이 맞는지 모름 — 날짜만 믿음)
    own = OwnStats(
        day=day, read_at=read_at, neighbors=_int(v.get("neighbors")), visitors_today=_int(v.get("visitors_today")),
        visitors_total=_int(v.get("visitors_total")), views_today=_int(v.get("views_today")),
        likes_today=_int(v.get("likes_today")), comments_today=_int(v.get("comments_today")),
        neighbor_change_today=_int(v.get("neighbor_change_today"), signed=True),
        buddy_requests_pending=_int(v.get("buddy_requests_pending")),
    )
    y = v.get("yesterday")
    if isinstance(y, dict) and (yd := _day(y.get("day"))) is not None and yd < day:
        own.yesterday = OwnDay(day=yd, views=_int(y.get("views")), likes=_int(y.get("likes")), comments=_int(y.get("comments")),
                               neighbor_change=_int(y.get("neighbor_change"), signed=True))
    p = v.get("prev")
    if isinstance(p, dict) and (pd := _day(p.get("day"))) is not None and pd < day:
        own.prev = OwnPrev(day=pd, neighbors=_int(p.get("neighbors")), visitors_total=_int(p.get("visitors_total")))
    numbers = (own.neighbors, own.visitors_today, own.visitors_total, own.views_today, own.likes_today, own.comments_today,
               own.neighbor_change_today, own.buddy_requests_pending)
    if all(n is None for n in numbers) and own.yesterday is None:
        return None
    return own


def parse_own_stats(data: Any, tz: str) -> dict[str, OwnStats]:
    """파일의 "own_stats" → {블로그 이름(소문자)·blog_id: OwnStats}. 없거나 모양이 다르면 {} — 블로그마다 따로 걸러 이상한 블로그만 빠진다."""
    out: dict[str, OwnStats] = {}
    if not isinstance(data, dict):
        return out
    for name, v in data.items():
        if not isinstance(name, str) or not name.strip() or (own := _own_stats(v, tz)) is None:
            continue
        for n in (name, v.get("blog_id")):
            if isinstance(n, str) and n.strip():
                out.setdefault(n.strip().lower(), own)
    return out


def _marker(raw: Any, first_line: str) -> str:
    """표시: 준 값이 아는 표시면 그것, 아니면 글 첫머리, 그래도 없으면 🔔 (Blog-Auto notify._marker 와 같게)."""
    if isinstance(raw, str):
        m = _MARKER_ALIASES.get(raw.strip(), raw.strip())
        if m in TODO_MARKERS:
            return m
    for m in (*TODO_MARKERS, *_MARKER_ALIASES):
        if first_line.startswith(m):
            return _MARKER_ALIASES.get(m, m)
    return TODO_DEFAULT_MARKER


def _strip_marker(line: str) -> str:
    for m in (*TODO_MARKERS, *_MARKER_ALIASES):
        if line.startswith(m):
            return line[len(m):].strip()
    return line


def _todo_line(v: Any, limit: int = TODO_LINE_MAX) -> str:
    """주인에게 보일 한 줄: 공백 정리 + 토큰처럼 보이는 글자는 가림 (safe_text — 자르기 전에 가려서 잘린 토큰이 새지 않게) + 길이."""
    if not isinstance(v, str):
        return ""
    line = safe_text(v[:2000], 2000)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _todo_item(v: Any, tz: str) -> TodoItem | None:
    """할 일 한 줄. 글도 요약도 없으면 None (그 줄만 버림)."""
    if not isinstance(v, dict):
        return None
    text = v.get("text") if isinstance(v.get("text"), str) else ""
    lines = [ln for ln in (_todo_line(x) for x in text.splitlines()) if ln]
    first = lines[0] if lines else ""
    marker = _marker(v.get("marker"), first)
    what = _strip_marker(first)
    how = "\n".join(lines[1:1 + TODO_HOW_LINES])
    short = _todo_line(v.get("short"), TODO_SHORT_MAX) or (what[: TODO_SHORT_MAX - 1] + "…" if len(what) > TODO_SHORT_MAX else what)
    if not short:
        return None
    key = _one_line(v.get("key"), TODO_KEY_MAX) or f"todo:{short}"  # 키가 빠진 줄도 버리지 않는다 (요약으로 키)
    link = https_url(v.get("link").strip()) if isinstance(v.get("link"), str) else None
    return TodoItem(
        key=key, marker=marker, short=short, what=what, how=how, since=_parse_time(v.get("since"), tz),
        urgent=v.get("urgent") is True, blog=(_one_line(v.get("blog"), 20) or "").lower(),
        kind=_one_line(v.get("kind"), 40) or "", link=link, group=_one_line(v.get("group"), 40) or "",
        count=max(1, _count(v.get("count"), top=TODO_COUNT_MAX) or 1),
    )


def parse_todo(data: Any, tz: str) -> TodoExport | None:
    """파일의 "todo" → TodoExport. 없거나 모양이 다르면 None ('블로그 쪽 할 일을 못 읽음'). 이상한 항목만 버린다."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    items = [it for it in (_todo_item(x, tz) for x in data["items"][:TODO_MAX_ITEMS]) if it is not None]
    return TodoExport(generated_at=_parse_time(data.get("generated_at"), tz), items=items)


# ---------------------------------------------------------------- v2 (spec §5.2)
V2_BLOGS = ("finance", "issue", "hotdeal")
POST_STATUSES = ("saved", "needs_input", "drafting", "failed", "published")
POST_NEEDS = ("photos", "publish", "answers", "retry", "owner_edit", "share", "channel")
THUMB_RE = re.compile(r"^[a-z0-9-]{1,64}\.(jpg|webp)$")
V2_POSTS_MAX = 60
SHARE_MAX = 1500  # 핫딜 오픈채팅 문구 (recent[].share)
_NAVER_HOSTS = ("blog.naver.com", "m.blog.naver.com")


def _naver_url(v: Any) -> str | None:
    u = https_url(v)
    if u is None:
        return None
    from urllib.parse import urlsplit

    return u if (urlsplit(u).hostname or "").lower() in _NAVER_HOSTS else None


def _str_list(v: Any, n: int, limit: int) -> list[str]:
    if not isinstance(v, list):
        return []
    return [x for x in (_one_line(safe_text(i, limit * 2), limit) if isinstance(i, str) else None for i in v[:n]) if x]


def _links(v: Any, n: int = 8) -> list[dict[str, str]]:
    out = []
    for x in v[:n] if isinstance(v, list) else []:
        if isinstance(x, dict) and (u := https_url(x.get("url"))) is not None:
            out.append({"label": _one_line(x.get("label"), 40) or "열기", "url": u})
    return out


def _export_post(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    pid = _count(v.get("pid"), top=10**9)
    title = _one_line(v.get("title"), 80)
    if not pid or title is None:
        return None
    pr = v.get("photo_request")
    photo_request = None
    if isinstance(pr, dict):
        photo_request = {"must": _str_list(pr.get("must"), 12, 80), "links": _links(pr.get("links"))}
    slots = []
    for x in v.get("slots")[:40] if isinstance(v.get("slots"), list) else []:
        if isinstance(x, dict) and (n := _count(x.get("slot"), top=40)):
            thumb = x.get("thumb") if isinstance(x.get("thumb"), str) and THUMB_RE.match(x.get("thumb")) else None
            # full (10/20): 사진 편집기가 자를 큰 사본 (같은 thumbs 폴더 · 이름 규칙 같음) — 썸네일이 있는 자리만
            full = x.get("full") if thumb and isinstance(x.get("full"), str) and THUMB_RE.match(x.get("full")) else None
            # final (10/20, 선택 칸): 봇이 그대로 올릴 사진 (사진을 누르면 먼저 보여 줌) — 없는 예전 기록이면 full
            final = x.get("final") if thumb and isinstance(x.get("final"), str) and THUMB_RE.match(x.get("final")) else None
            slots.append({"slot": n, "label": _one_line(x.get("label"), 40) or f"자리 {n}", "thumb": thumb,
                          **({"full": full} if full else {}), **({"final": final} if final else {})})
    questions = []
    for x in v.get("questions")[:10] if isinstance(v.get("questions"), list) else []:
        if isinstance(x, dict) and isinstance(x.get("q"), str) and x["q"].strip():
            questions.append({"n": _count(x.get("n"), top=50) or len(questions) + 1, "q": safe_text(x["q"], 400)})
    needs = [n for n in v.get("needs", []) if n in POST_NEEDS] if isinstance(v.get("needs"), list) else []
    return {
        "pid": pid, "title": title, "status": v.get("status") if v.get("status") in POST_STATUSES else "saved",
        "stage": _one_line(v.get("stage"), 40) or "", "saved_at": _one_line(v.get("saved_at"), 40),
        "score": _count(v.get("score"), top=100), "popular": v.get("popular") is True, "owner_edited": v.get("owner_edited") is True,
        "needs": list(dict.fromkeys(needs)), "photo_request": photo_request, "slots": slots, "questions": questions,
        "deadline": _one_line(v.get("deadline"), 40), "naver_url": _naver_url(v.get("naver_url")),
        "schedule": _sched_post(v.get("schedule")), "pics": _pics(v.get("pics")), "ig_suggest": _ig_suggest(v.get("ig_suggest")),
        # 인기 검색어 상위 10 키워드 글 (주인 10/20 — Blog-Auto hotfirst, 선택 칸: 핫 글만) → 할 일 맨 위 · '🔥 인기 N위'
        **({"hot_rank": hr} if (hr := _count(v.get("hot_rank"), top=10)) else {}),
        # 업로드 실패 이유 (주인 10/21 — Blog-Auto failwhy, 선택 칸: 실패한 글만) → 할 일 줄 '업로드 실패 · 이유'
        **({"fail": fl} if (fl := _fail(v.get("fail"))) is not None else {}),
        # 비슷한 아이템 (주인 10/20 — Blog-Auto issue_similar, 선택 칸: Pick 패션 글만) → '비슷한 아이템 링크 넣기'
        **({"similar": sim} if (sim := _similar(v.get("similar"))) is not None else {}),
    }


FAIL_KINDS = ("memory", "busy", "login", "editor", "ai", "other")
FAIL_AUTO = ("pending", "done", "no")


def _fail(v: Any) -> dict[str, str] | None:
    """{why, kind, at, auto_retry} — 이유 글이 없으면 None (예전 Blog-Auto 는 이 칸이 없음)."""
    if not isinstance(v, dict) or not (why := _one_line(safe_text(v.get("why"), 120), 80)):
        return None
    return {"why": why, "kind": v.get("kind") if v.get("kind") in FAIL_KINDS else "other", "at": _one_line(v.get("at"), 40) or "",
            "auto_retry": v.get("auto_retry") if v.get("auto_retry") in FAIL_AUTO else "no"}


def _similar(v: Any) -> dict[str, int] | None:
    """{n: 넣은 수, ably: 주인 링크 수} (둘 다 0~20). 모양이 다르면 None (단추 없음)."""
    if not isinstance(v, dict):
        return None
    n, ably = v.get("n"), v.get("ably")
    if not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 20 for x in (n, ably)):
        return None
    return {"n": n, "ably": ably}


# 넣으면 좋을 인스타 게시물 (주인 10/18 — Blog-Auto ig_suggest: 그 글이 쓴 기사 속 임베드·링크·'인스타그램에 올렸다' 문장).
# 주소 글자만 본다 — 인스타그램에 접속하지 않는다. url = 게시물 대표 주소만 (있으면 '넣기'), handle = 계정 이름 (없으면 None)
IG_POST_RE = re.compile(r"^https://www\.instagram\.com/(?:p|reel)/[A-Za-z0-9_-]{5,40}/$")
IG_HANDLE_RE = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9_.]{0,28}[A-Za-z0-9_])?$")
IG_SUGGEST_MAX = 6


def _ig_suggest(v: Any) -> list[dict[str, Any]]:
    """[{url, handle, what, when, source, host, inserted}] 최대 6 — 설명이 없거나 열 곳(게시물·계정·기사)이 없는 줄은 뺀다."""
    out: list[dict[str, Any]] = []
    for x in v[:IG_SUGGEST_MAX] if isinstance(v, list) else []:
        if not isinstance(x, dict):
            continue
        url = x.get("url") if isinstance(x.get("url"), str) and IG_POST_RE.match(x["url"]) else None
        handle = x.get("handle") if isinstance(x.get("handle"), str) and IG_HANDLE_RE.match(x["handle"]) else None
        what, src = _one_line(safe_text(x.get("what"), 200), 80), https_url(x.get("source"))
        if not what or not (url or handle or src) or (url and any(r["url"] == url for r in out)):
            continue
        out.append({"url": url, "handle": handle, "what": what, "when": _one_line(safe_text(x.get("when"), 40), 20) or "",
                    "source": src if src and len(src) <= 500 else None, "host": _one_line(x.get("host"), 60) or "",
                    "inserted": x.get("inserted") is True})
    return out


# 링크에서 사진 고르기 (주인 10/18 — Blog-Auto pickphotos): 후보 사진 (썸네일 blog_view/thumbs · 원본 blog_view/pics)
PIC_ID = re.compile(r"^pk-[0-9a-f]{8}$")
PIC_JOB_ID = re.compile(r"^pj-[0-9a-f]{8}$")
PIC_FILE_RE = re.compile(r"^[a-z0-9-]{1,80}\.(jpg|jpeg|png|webp)$")
PIC_STATES = ("working", "ready", "failed", "none", "inserted")  # inserted: Pick 자동으로 넣음 (남은 후보는 골라 넣기)
PICS_MAX, PIC_JOBS_MAX = 24, 5


def _pic_items(v: Any) -> list[dict[str, Any]]:
    """후보 사진 [{id, thumb, full, why, slot, host}] (최대 24, Blog-Auto 순서 그대로). 파일 이름이 이상한 줄은 뺀다."""
    out: list[dict[str, Any]] = []
    for x in v[:PICS_MAX] if isinstance(v, list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not PIC_ID.match(x["id"]):
            continue
        thumb, full = x.get("thumb"), x.get("full")
        if not (isinstance(thumb, str) and THUMB_RE.match(thumb) and isinstance(full, str) and PIC_FILE_RE.match(full)):
            continue
        if any(p["id"] == x["id"] for p in out):
            continue
        slot = x.get("slot")
        out.append({"id": x["id"], "thumb": thumb, "full": full, "why": _one_line(safe_text(x.get("why"), 40), 20) or "",
                    "slot": slot if isinstance(slot, int) and not isinstance(slot, bool) and 0 <= slot <= 40 else None,
                    "host": _one_line(x.get("host"), 60) or ""})
    return out


def _pics(v: Any) -> dict[str, Any] | None:
    """글의 후보 사진 상태 {state, text, at, items} — 없거나 모르는 상태면 None (화면은 '링크에서 사진 찾기')."""
    if not isinstance(v, dict) or v.get("state") not in PIC_STATES:
        return None
    return {"state": v["state"], "text": _one_line(safe_text(v.get("text"), 160), 80) or "", "at": _one_line(v.get("at"), 40),
            "items": _pic_items(v.get("items"))}


def _pic_jobs(v: Any) -> list[dict[str, Any]] | None:
    """링크만 붙여 넣은 사진 뽑기 (최근 것 먼저, 5개까지). 칸이 없으면(예전 Blog-Auto) None."""
    if not isinstance(v, list):
        return None
    out = []
    for x in v[:PIC_JOBS_MAX]:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not PIC_JOB_ID.match(x["id"]):
            continue
        p = _pics(x)
        url = x.get("url") if isinstance(x.get("url"), str) and re.match(r"^https?://", x["url"]) and len(x["url"]) <= 500 else ""
        if p is not None:
            out.append({"id": x["id"], "url": url, "want": _one_line(safe_text(x.get("want"), 200), 100) or "", **p})
    return out


SCHED_STATUSES = ("scheduled", "blocked", "publishing")


def _sched_post(v: Any) -> dict[str, Any] | None:
    """글의 간격 두고 발행 상태 {scheduled_at, status, reason} (Blog-Auto schedule.post_view) — 없거나 모르면 None."""
    if not isinstance(v, dict) or v.get("status") not in SCHED_STATUSES:
        return None
    return {"scheduled_at": _one_line(v.get("scheduled_at"), 40), "status": v["status"], "reason": _one_line(v.get("reason"), 80) or ""}


def _hhmm_pair(v: Any, default: list[str]) -> list[str]:
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) and re.fullmatch(r"\d{2}:\d{2}", x) for x in v):
        return list(v)
    return list(default)


def _sched(data: Any) -> dict[str, Any] | None:
    """기록 파일 schedule 칸 (간격 두고 발행 — Blog-Auto schedule.export). 예전 Blog-Auto 면 None (화면이 그 기능을 숨김)."""
    if not isinstance(data, dict) or not isinstance(data.get("blogs"), dict):
        return None
    out: dict[str, Any] = {"blogs": {}}
    for blog in V2_BLOGS:
        b = data["blogs"].get(blog)
        if not isinstance(b, dict):
            continue
        gm = b.get("gap_minutes")
        items = []
        for x in b.get("items")[:60] if isinstance(b.get("items"), list) else []:
            if isinstance(x, dict) and (pid := _count(x.get("pid"), top=10**9)) and x.get("status") in SCHED_STATUSES:
                items.append({"pid": pid, "scheduled_at": _one_line(x.get("scheduled_at"), 40), "status": x["status"],
                              "reason": _one_line(x.get("reason"), 80) or "", "moved": x.get("moved") is True})  # moved: 주인이 시각을 직접 정함 (선택 칸)
        out["blogs"][blog] = {
            "spaced_only": b.get("spaced_only") is True,
            "gap_minutes": [int(g) for g in gm] if isinstance(gm, list) and len(gm) == 2 and all(isinstance(g, int) and 30 <= g <= 600 for g in gm)
            else [120, 180],
            "window": _hhmm_pair(b.get("window"), ["08:00", "22:00"]), "morning": _hhmm_pair(b.get("morning"), ["08:00", "09:30"]),
            "next_at": _one_line(b.get("next_at"), 40), "tail_at": _one_line(b.get("tail_at"), 40),
            "last_pub": _one_line(b.get("last_pub"), 40), "count": _count(b.get("count"), top=1000) or 0,
            "blocked": _count(b.get("blocked"), top=1000) or 0, "items": items,
        }
    return out


VISIT_ID = re.compile(r"^visit-(finance|issue|hotdeal)-[0-9]{1,9}$")
VISITS_MAX = 60  # 블로그마다 하루 10개 · 2일 (Blog-Auto dashview.visits)


def _visits(data: Any) -> list[dict[str, Any]] | None:
    """'오늘 들를 이웃 글' (주인 10/18 — Blog-Auto dashview.visits). 칸이 없으면(예전 Blog-Auto) None — 화면이 숨김.
    주소는 네이버 블로그 글만 (그 밖이면 그 줄을 뺌). 순서는 Blog-Auto 그대로 (할 일 먼저 · 최근 것부터)."""
    if not isinstance(data, list):
        return None
    out = []
    for x in data[:VISITS_MAX]:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not (m := VISIT_ID.match(x["id"])):
            continue
        url, title = _naver_url(x.get("url")), _one_line(x.get("title"), 80)
        if url is None or title is None or x.get("blog") != m.group(1):
            continue
        out.append({"id": x["id"], "blog": m.group(1), "target": _one_line(x.get("target"), 40) or "",
                    "blog_name": _one_line(safe_text(x.get("blog_name"), 80), 40) or "", "title": title, "url": url,
                    "comment": safe_text(x.get("comment"), 500), "created_at": _one_line(x.get("created_at"), 40),
                    "status": "done" if x.get("status") == "done" else "todo"})
    return out


def _neighbors(v: Any) -> dict[str, Any] | None:
    """status.blogs.<b>.neighbors {cap_today, cap_range, reduced, accepted_today} (주인 10/18) — 없으면 None."""
    if not isinstance(v, dict):
        return None
    rng = v.get("cap_range")
    ok = isinstance(rng, list) and len(rng) == 2 and all(_count(n, top=1000) is not None for n in rng)
    return {"cap_today": _count(v.get("cap_today"), top=1000), "cap_range": [int(n) for n in rng] if ok else None,
            "reduced": v.get("reduced") is True, "accepted_today": _count(v.get("accepted_today"), top=1000)}


def _share(v: Any) -> str:
    """핫딜 최근 글의 오픈채팅 문구 (주인 10/18 — 카톡 대신 대시보드에서 복사). 줄바꿈은 두고 줄마다 safe_text, 전체 SHARE_MAX."""
    if not isinstance(v, str):
        return ""
    lines = [safe_text(x, SHARE_MAX) for x in v.replace("\r", "").split("\n")[:60]]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:SHARE_MAX]


DEAL_KEY_RE = re.compile(r"^[A-Za-z0-9_.:/?=&%+-]{3,200}$")  # 딜봇 product_id ('coupang:123:456' · 'naver:…' · 'coupang:url:해시')
POST_DEALS_MAX = 12


def _post_deals(v: Any) -> list[dict[str, str | None]]:
    """핫딜 최근 글에 들어간 딜 (recent.hotdeal[].deals — 딜봇 published_deals.jsonl 의 product_id·posted_at 그대로).
    대시보드 딜 줄의 '블로그 ↗' 를 이것으로만 잇는다 (제목으로는 잇지 않음). 글자 목록(product_id 만)도 받는다."""
    out: list[dict[str, str | None]] = []
    for x in v[:POST_DEALS_MAX] if isinstance(v, list) else []:
        key, at = (x, None) if isinstance(x, str) else ((x.get("product_id"), x.get("posted_at")) if isinstance(x, dict) else (None, None))
        if isinstance(key, str) and DEAL_KEY_RE.match(key):
            out.append({"product_id": key, "posted_at": _one_line(at, 40)})
    return out


# Pick '인기 검색어' (주인 10/20 — Blog-Auto hotboard: 구글 트렌드 한국 RSS · Pick 후보 기록 · 네이버 뉴스 API 낱말)
TREND_SOURCES = ("advisor", "google", "pick", "news", "early")  # advisor: 크리에이터 어드바이저 트렌드 (10/20) · early: 급상승 (10/21)
TREND_BADGE_MAX = 20  # 선택 칸 badge — '⚡ 막 뜨는 중' (Blog-Auto hotboard.EARLY_BADGE, early 줄만)
TRENDS_MAX = 20


def _trends(v: Any) -> dict[str, Any] | None:
    """{at, items: [{keyword, rank, sources, change, new, written, tag, badge?}]} — 칸이 없으면(예전 Blog-Auto) None (카드를 숨김).
    badge 는 선택 칸 (막 뜨는 중 줄만 — 한 줄 TREND_BADGE_MAX 자, 없으면 칸 없음)
    키워드는 한 줄 40자 (/issue 키워드 길이와 같게) · 같은 키워드는 한 번만 · 순위 순서."""
    if not isinstance(v, dict) or not isinstance(v.get("items"), list):
        return None
    out, seen = [], set()
    for x in v["items"][:TRENDS_MAX * 2]:
        if not isinstance(x, dict) or not (kw := _one_line(x.get("keyword"), 40)) or kw in seen:
            continue
        seen.add(kw)
        ch = x.get("change")
        out.append({"keyword": kw, "rank": _count(x.get("rank"), top=100) or len(out) + 1,
                    "sources": [s for s in TREND_SOURCES if isinstance(x.get("sources"), list) and s in x["sources"]],
                    "change": ch if isinstance(ch, int) and not isinstance(ch, bool) and -100 <= ch <= 100 else None,
                    "new": x.get("new") is True, "written": x.get("written") is True,
                    "tag": x.get("tag") if x.get("tag") in ("fashion", "ent") else None,
                    **({"badge": b} if (b := _one_line(x.get("badge"), TREND_BADGE_MAX)) else {})})
    out.sort(key=lambda x: x["rank"])
    return {"at": _one_line(v.get("at"), 40), "items": out[:TRENDS_MAX]}


LEARN_AREAS = ("style", "title", "photo", "tags", "topic", "schedule")  # Blog-Auto learn.AREAS
LEARN_STATES = ("open", "applied", "rejected", "expired")
LEARN_PROP_ID, LEARN_RULE_ID = re.compile(r"^lp-[0-9a-f]{8}$"), re.compile(r"^lr-[0-9a-f]{8}$")
LEARN_PROPS_MAX, LEARN_RULES_MAX, LEARN_VOTES_MAX = 12, 40, 100


def _learn(v: Any) -> dict[str, Any] | None:
    """배운 것으로 나아지기 (주인 10/20 — Blog-Auto learn.export): {week, at, lessons_week, auto_apply_days, proposals, rules, votes}.
    칸이 없으면(예전 Blog-Auto) None (카드·👍/👎 숨김). 개선안·규칙 글은 한 줄 200자, 모르는 칸·상태는 빠진다."""
    if not isinstance(v, dict):
        return None
    blogs = (*V2_BLOGS, "all")
    props = []
    for x in v.get("proposals")[:LEARN_PROPS_MAX] if isinstance(v.get("proposals"), list) else []:
        if not isinstance(x, dict) or not LEARN_PROP_ID.match(str(x.get("id") or "")) or x.get("blog") not in blogs \
                or x.get("area") not in LEARN_AREAS or not (t := _one_line(safe_text(x.get("rule_text") or "", 400), 200)):
            continue
        props.append({"id": x["id"], "blog": x["blog"], "area": x["area"], "rule_text": t,
                      "evidence": _count(x.get("evidence"), top=1000) or 0, "examples": _str_list(x.get("examples"), 3, 80),
                      "effect": _one_line(safe_text(x.get("effect") or "", 240), 120) or "",
                      "state": x.get("state") if x.get("state") in LEARN_STATES else "open"})
    rules = []
    for x in v.get("rules")[:LEARN_RULES_MAX] if isinstance(v.get("rules"), list) else []:
        if not isinstance(x, dict) or not LEARN_RULE_ID.match(str(x.get("id") or "")) or x.get("blog") not in blogs \
                or x.get("area") not in LEARN_AREAS or not (t := _one_line(safe_text(x.get("rule_text") or "", 400), 200)):
            continue
        c = x.get("config") if isinstance(x.get("config"), dict) else None
        rules.append({"id": x["id"], "blog": x["blog"], "area": x["area"], "rule_text": t, "at": _one_line(x.get("at"), 40),
                      "by": "auto" if x.get("by") == "auto" else "owner",
                      **({"config": _one_line(f"{c.get('key')} = {json.dumps(c.get('value'), ensure_ascii=False)}", 80)}
                         if c and isinstance(c.get("key"), str) else {})})
    votes = {}
    raw = v.get("votes") if isinstance(v.get("votes"), dict) else {}
    for k, x in list(raw.items())[-LEARN_VOTES_MAX:]:
        if isinstance(k, str) and k.isdigit() and len(k) <= 9 and x in ("up", "down"):
            votes[k] = x
    days = v.get("auto_apply_days")
    return {"week": _one_line(v.get("week"), 10), "at": _one_line(v.get("at"), 40), "lessons_week": _count(v.get("lessons_week"), top=10**6) or 0,
            "auto_apply_days": days if isinstance(days, int) and not isinstance(days, bool) and 0 < days <= 60 else None,
            "proposals": props, "rules": rules, "votes": votes}


def _recent_photos(v: Any) -> list[dict[str, Any]] | None:
    """최근 글 '고치기' 사진 (Blog-Auto dashview.recent[].photos): [{n, label, thumb}] 40장까지 · 칸이 없으면 None (고치기 없음)."""
    if not isinstance(v, list):
        return None
    out: list[dict[str, Any]] = []
    for x in v[:40]:
        if isinstance(x, dict) and (n := _count(x.get("n"), top=40)) and n not in {p["n"] for p in out}:
            t = x.get("thumb")
            out.append({"n": n, "label": _one_line(x.get("label"), 40) or f"사진 {n}",
                        "thumb": t if isinstance(t, str) and THUMB_RE.match(t) else None})
    return out


# 제품 테스트 (주인 10/21 — Blog-Auto product_trial.py): Pick 패션 글 '비슷한 아이템' 후보를 주인이 검수
PT_REASONS = ("스타일 다름", "색 다름", "아이템 종류 다름", "싸 보임/품질", "브랜드·판매처 별로", "가격 이상", "사진 별로", "너무 흔함",
              "셀럽 느낌 아님", "기타")  # Blog-Auto product_trial.REASONS 와 같게
PT_ITEM_ID = re.compile(r"^pt-[0-9a-f]{8}$")
PT_POSTS_MAX, PT_ITEMS_MAX, PT_PIECES_MAX, PT_LEARNED_MAX = 12, 8, 6, 12


def _product_trial(v: Any) -> dict[str, Any] | None:
    """{posts: [{pid, title, pieces: [{part, desc}], items: [{id, piece, name, shop, price, at, img, link, query, verdict?}]}],
    learned, ok, goal, reasons}. 칸이 없으면(예전 Blog-Auto) None. 후보가 없는 글·이름·주소가 이상한 후보는 빠진다."""
    if not isinstance(v, dict):
        return None
    posts = []
    for x in v.get("posts")[:PT_POSTS_MAX] if isinstance(v.get("posts"), list) else []:
        if not isinstance(x, dict) or not (pid := _count(x.get("pid"), top=10**9)) or not (title := _one_line(x.get("title"), 80)):
            continue
        pieces = [{"part": _one_line(p.get("part"), 20) or "", "desc": _one_line(safe_text(p.get("desc") or "", 120), 60) or ""}
                  for p in (x.get("pieces") if isinstance(x.get("pieces"), list) else [])[:PT_PIECES_MAX] if isinstance(p, dict)]
        items = []
        for it in (x.get("items") if isinstance(x.get("items"), list) else [])[:PT_ITEMS_MAX]:
            if not isinstance(it, dict) or not PT_ITEM_ID.match(str(it.get("id") or "")) \
                    or not (name := _one_line(safe_text(it.get("name") or "", 160), 80)) or not (link := https_url(it.get("link"))):
                continue
            n = _count(it.get("piece"), top=PT_PIECES_MAX)
            img = it.get("img")
            items.append({"id": it["id"], "piece": n if n is not None and n < len(pieces) else 0, "name": name,
                          "shop": _one_line(it.get("shop"), 40) or "", "price": _count(it.get("price"), top=10**9) or 0,
                          "at": _one_line(it.get("at"), 20) or "", "img": img if isinstance(img, str) and THUMB_RE.match(img) else None,
                          "link": link, "query": _one_line(it.get("query"), 60) or "",
                          **({"verdict": it["verdict"]} if it.get("verdict") in ("ok", "bad") else {})})
        if items:
            posts.append({"pid": pid, "title": title, "pieces": pieces, "items": items})
    reasons = [r for r in PT_REASONS if isinstance(v.get("reasons"), list) and r in v["reasons"]]
    return {"posts": posts, "learned": _str_list(v.get("learned"), PT_LEARNED_MAX, 120), "ok": _count(v.get("ok"), top=10**6) or 0,
            "goal": _count(v.get("goal"), top=10**4) or 20, "reasons": reasons or list(PT_REASONS)}


# 사진 테스트 (주인 10/21 — Blog-Auto photo_trial.export): 시험 글마다 자리 사진(올라가는 판·큰 사본)·설명·출처·숫자 검수·받은 피드백 수
TRIAL_REASONS = ("margin_left_over", "ui_remnant", "subject_cut", "too_wide", "too_tight", "wrong_person", "low_quality_blur",
                 "duplicate", "text_overlay", "bad_aspect", "dark", "broadcast_frame", "screen_capture_graphics", "other",
                 "placement", "spread", "count", "fit")  # Blog-Auto photo_trial.REASONS
TRIAL_QA = re.compile(r"^[a-z_]{2,32}$")
TRIAL_POSTS_MAX, TRIAL_RULES_MAX = 30, 20


def _photo_trial(v: Any) -> dict[str, Any] | None:
    """{posts: [{pid, title, at, slots: [{slot, final, full, credit, desc, section?, ctx?, where?, why?, qa: {ok, issues}}], feedback_done}], rules: [{id, area, text, at}]}.
    칸이 없거나 시험 글이 없으면 None (화면이 '사진 테스트' 를 숨김). 사진 이름은 thumbs 규칙(THUMB_RE)만."""
    if not isinstance(v, dict) or not isinstance(v.get("posts"), list):
        return None
    posts = []
    for x in v["posts"][:TRIAL_POSTS_MAX]:
        if not isinstance(x, dict) or not (pid := _count(x.get("pid"), top=10**9)):
            continue
        slots = []
        for s in x.get("slots")[:40] if isinstance(x.get("slots"), list) else []:
            if not isinstance(s, dict) or not (n := _count(s.get("slot"), top=40)):
                continue
            pic = {k: s[k] for k in ("final", "full") if isinstance(s.get(k), str) and THUMB_RE.match(s[k])}
            qa = s.get("qa") if isinstance(s.get("qa"), dict) else {}
            slots.append({"slot": n, **pic, "credit": _one_line(s.get("credit"), 40) or "", "desc": _one_line(s.get("desc"), 80) or "",
                          # 어느 사진인지 읽히게 (선택 칸): 소제목 · 바로 앞 문장 · 위치 · 고른 이유
                          **{k: _one_line(safe_text(s.get(k) or "", 200), m) or "" for k, m in (("section", 30), ("ctx", 80), ("where", 40), ("why", 80))},
                          "qa": {"ok": qa.get("ok") is True,
                                 "issues": [i for i in qa.get("issues") or [] if isinstance(i, str) and TRIAL_QA.match(i)][:6]}})
        posts.append({"pid": pid, "title": _one_line(x.get("title"), 80) or f"#{pid}", "at": _one_line(x.get("at"), 40),
                      "slots": slots, "feedback_done": _count(x.get("feedback_done"), top=10**6) or 0})
    if not posts:
        return None
    rules = []
    for r in v.get("rules")[:TRIAL_RULES_MAX] if isinstance(v.get("rules"), list) else []:
        if isinstance(r, dict) and LEARN_RULE_ID.match(str(r.get("id") or "")) and r.get("area") in LEARN_AREAS \
                and (t := _one_line(safe_text(r.get("text") or "", 400), 200)):
            rules.append({"id": r["id"], "area": r["area"], "text": t, "at": _one_line(r.get("at"), 40)})
    return {"posts": posts, "rules": rules}


# 스레드 (주인 10/21 — Blog-Auto threads_api.export): 주인이 쓴 글 바로·예약 줄 · 계정의 최근 글 · 내 글의 새 답글 · 공식 인사이트
TH_ITEM_ID = re.compile(r"^th-[0-9a-f]{8}$")
TH_MEDIA_ID = re.compile(r"^[0-9]{1,30}$")
TH_STATUSES = ("scheduled", "posting", "posted", "failed", "cancelled")
TH_METRICS = ("views", "likes", "replies", "reposts", "quotes")
TH_TEXT_MAX = 500
TH_QUEUE_MAX, TH_POSTS_MAX, TH_REPLIES_MAX, TH_STATS_POSTS_MAX = 40, 15, 20, 15
_CTRL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def _th_text(v: Any, limit: int) -> str:
    """여러 줄 그대로 (줄바꿈만 남기고 제어 문자는 뺌), limit 자까지."""
    if not isinstance(v, str):
        return ""
    t = _CTRL.sub(" ", v.replace("\r\n", "\n")).strip()
    return t[:limit]


def _th_nums(v: Any) -> dict[str, int]:
    v = v if isinstance(v, dict) else {}
    return {m: _count(v.get(m), top=10**12) or 0 for m in TH_METRICS}


def _threads(v: Any) -> dict[str, Any] | None:
    """{connected, how, token_days, today, cap, queue, posts, replies: {new, items}, stats} (계약 export_v2.json threads).
    칸이 없으면(예전 Blog-Auto) None. 줄마다 따로 걸러 이상한 줄만 빠진다. 주소는 https 만."""
    if not isinstance(v, dict) or not isinstance(v.get("connected"), bool):
        return None
    queue = []
    for x in v.get("queue")[:TH_QUEUE_MAX] if isinstance(v.get("queue"), list) else []:
        if isinstance(x, dict) and TH_ITEM_ID.match(str(x.get("id") or "")) and x.get("status") in TH_STATUSES:
            queue.append({"id": x["id"], "text": _th_text(x.get("text"), TH_TEXT_MAX), "at": _one_line(x.get("at"), 40),
                          "status": x["status"], "image": x.get("image") is True, "permalink": https_url(x.get("permalink")) or "",
                          "error": _one_line(safe_text(x.get("error") or "", 200), 120) or ""})
    posts = []
    for x in v.get("posts")[:TH_POSTS_MAX] if isinstance(v.get("posts"), list) else []:
        if isinstance(x, dict) and TH_MEDIA_ID.match(str(x.get("id") or "")):
            posts.append({"id": x["id"], "text": _one_line(x.get("text"), 140) or "", "permalink": https_url(x.get("permalink")) or "",
                          "at": _one_line(x.get("at"), 40)})
    rep = v.get("replies") if isinstance(v.get("replies"), dict) else {}
    items = []
    for x in rep.get("items")[:TH_REPLIES_MAX] if isinstance(rep.get("items"), list) else []:
        if isinstance(x, dict) and TH_MEDIA_ID.match(str(x.get("id") or "")):
            items.append({"id": x["id"], "post_id": str(x.get("post_id") or "") if TH_MEDIA_ID.match(str(x.get("post_id") or "")) else "",
                          "post_text": _one_line(x.get("post_text"), 80) or "", "username": _one_line(x.get("username"), 40) or "",
                          "text": _one_line(x.get("text"), 300) or "", "at": _one_line(x.get("at"), 40)})
    stats = None
    st = v.get("stats")
    if isinstance(st, dict):
        days = [{"day": d["day"], "views": _count(d.get("views"), top=10**12) or 0}
                for d in (st.get("days") if isinstance(st.get("days"), list) else [])[:7]
                if isinstance(d, dict) and isinstance(d.get("day"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["day"])]
        per = [{"id": p["id"], **_th_nums(p)} for p in (st.get("posts") if isinstance(st.get("posts"), list) else [])[:TH_STATS_POSTS_MAX]
               if isinstance(p, dict) and TH_MEDIA_ID.match(str(p.get("id") or ""))]
        stats = {"at": _one_line(st.get("at"), 40), "totals": _th_nums(st.get("totals")),
                 "followers": _count(st.get("followers"), top=10**12), "days": days, "posts": per}
    return {"connected": v["connected"], "how": _one_line(v.get("how"), 200) or "",
            "token_days": _count(v.get("token_days"), top=10**4), "today": _count(v.get("today"), top=10**4) or 0,
            "cap": _count(v.get("cap"), top=250) or 0, "queue": queue, "posts": posts,
            "replies": {"new": len(items), "items": items}, "stats": stats}


def parse_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v2 의 새 칸들 → 걸러 둔 사전 (블로그마다 따로 걸러 이상한 줄만 빠짐). 바깥 주소는 https 만, 글 주소는 네이버 블로그만."""
    out: dict[str, Any] = {"posts": {}, "recent": {}, "scan": None, "status": {"blogs": {}, "kakao": {}}, "digest": {},
                           "schedule": _sched(data.get("schedule")), "visits": _visits(data.get("visits")),
                           "pics_jobs": _pic_jobs(data.get("pics_jobs")), "trends": _trends(data.get("trends")),
                           "learn": _learn(data.get("learn")), "photo_trial": _photo_trial(data.get("photo_trial")),
                           "product_trial": _product_trial(data.get("product_trial")),
                           "threads": _threads(data.get("threads"))}
    posts = data.get("posts") if isinstance(data.get("posts"), dict) else {}
    for blog in V2_BLOGS:
        rows = posts.get(blog) if isinstance(posts.get(blog), list) else []
        out["posts"][blog] = [p for p in (_export_post(x) for x in rows[:V2_POSTS_MAX]) if p]
    recent = data.get("recent") if isinstance(data.get("recent"), dict) else {}
    for blog in V2_BLOGS:
        rows = recent.get(blog) if isinstance(recent.get(blog), list) else []
        out["recent"][blog] = [
            {"pid": _count(x.get("pid"), top=10**9), "title": _one_line(x.get("title"), 80) or "",
             "url": _naver_url(x.get("url")), "at": _one_line(x.get("at"), 40),
             **({"share": sh} if blog == "hotdeal" and (sh := _share(x.get("share"))) else {}),
             # 검색 등록 체크리스트 (주인 10/18): 발행 시각 · Blog-Auto 가 구글 등록을 마친 글 (자동 등록을 켠 경우) — 없으면 칸 없음
             **({"published_at": pa} if (pa := _one_line(x.get("published_at"), 40)) else {}),
             **({"google_done": True} if x.get("google_done") is True or x.get("google") == "done" else {}),
             **({"deals": ds} if blog == "hotdeal" and (ds := _post_deals(x.get("deals"))) else {}),
             # 발행 글 고치기 (주인 10/21 — Blog-Auto post_edit 를 켠 블로그만): 글 순서 사진 [{n, label, thumb}] (사진 없으면 [])
             **({"photos": ph} if (ph := _recent_photos(x.get("photos"))) is not None else {})}
            for x in rows[:10] if isinstance(x, dict) and _one_line(x.get("title"), 80)]
    scan = data.get("scan")
    if isinstance(scan, dict) and isinstance(scan.get("items"), list):
        items = []
        for x in scan["items"][:20]:
            if isinstance(x, dict) and (n := _count(x.get("n"), top=50)) and (t := _one_line(x.get("title"), 80)):
                items.append({"n": n, "title": t, "code": _one_line(x.get("code"), 10) or "", "score": _count(x.get("score"), top=100),
                              "recommend": _count(x.get("recommend"), top=100)})
        sid = scan.get("sid") if isinstance(scan.get("sid"), str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,40}", scan.get("sid")) else None
        out["scan"] = {"sid": sid, "at": _one_line(scan.get("at"), 40), "items": items} if sid else None
    st = data.get("status") if isinstance(data.get("status"), dict) else {}
    blogs = st.get("blogs") if isinstance(st.get("blogs"), dict) else {}
    for blog in V2_BLOGS:
        b = blogs.get(blog)
        if isinstance(b, dict):
            out["status"]["blogs"][blog] = {
                "mode": b.get("mode") if b.get("mode") in ("draft", "publish") else "draft", "paused": b.get("paused") is True,
                "today": _count(b.get("today"), top=1000) or 0,
                "login": b.get("login") if b.get("login") in ("ok", "needed", "unknown") else "unknown",
                "login_checked": _one_line(b.get("login_checked"), 40),
                "blog_id": b.get("blog_id") if isinstance(b.get("blog_id"), str) and re.fullmatch(r"[a-z0-9_-]{2,40}", b.get("blog_id")) else None,
                "neighbors": _neighbors(b.get("neighbors")),
            }
    k = st.get("kakao") if isinstance(st.get("kakao"), dict) else {}
    out["status"]["kakao"] = {"state": k.get("state") if k.get("state") in ("ok", "down", "expiring", "unknown") else "unknown",
                              "expires_at": _one_line(k.get("expires_at"), 40)}
    dg = data.get("digest") if isinstance(data.get("digest"), dict) else {}
    out["digest"] = {"day": _one_line(dg.get("day"), 10), "generated_at": _one_line(dg.get("generated_at"), 40),
                     "blogs": {b: _str_list((dg.get("blogs") or {}).get(b) if isinstance(dg.get("blogs"), dict) else None, 8, 200)
                               for b in V2_BLOGS},
                     "waiting": _str_list(dg.get("waiting"), 8, 200)}
    return out


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
    version = data.get("version") if data.get("version") in (1, 2) else 1
    return BlogAutoExport(OK, written_at=written, blogs=blogs, waiting=parse_waiting(data.get("waiting"), tz),
                          own_stats=parse_own_stats(data.get("own_stats"), tz), todo=parse_todo(data.get("todo"), tz),
                          version=version, v2=parse_v2(data) if version == 2 else {})


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
                own_stats=parsed.own_stats,  # 자기 통계는 읽은 시각(read_at)으로 따로 오래됐는지 본다
                todo=parsed.todo,  # '지금 할 일' 은 그대로 보이고 '몇 시간 전 것' 안내만 붙는다
                version=parsed.version,
                v2=parsed.v2,
            )
        return parsed
