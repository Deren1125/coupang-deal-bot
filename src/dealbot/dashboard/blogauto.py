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
로그인 할 일 줄에만 "login": 원격 로그인 대상 (remote_login.TARGETS 의 이름 — 주인 10/26, 딜봇이 그 줄에 '원격 로그인' 단추).

v2 status.logins (주인 10/26 — Blog-Auto remote_login.states): 프로그램이 쓰는 로그인마다 지금 상태 (사이트를 열지 않고 읽은 것) —
    [{"id", "label", "state": ok|needed|resting|off|unknown, "note", "checked_at", "since", "until", "target", "where"}] → logins.py '로그인 관리' 칸.
v2 status.routine (주인 10/27 — 블로그 반자동, 주인이 하루 5번 손으로): {"day", "slots": [{"key": s1~s5, "at": "HH:MM", "label"}],
    "items": [{"id", "slot", "group": 발행|예약|이웃|SNS, "blog", "title", "note", "min", "copy": [{label, text}], "links": [{label, url}]}],
    "overflow"} → routine.py '오늘 할 일' 칸 (할 일 탭 맨 위).
  발행·예약 줄의 "paste" (주인 10/27 — 서버가 네이버에 로그인하지 않고 주인이 아이패드 사파리에서 붙여 넣기):
    {"title", "html" (≤200000자 · 사진은 <img src="media:<파일>">), "tags": [≤30], "cards", "photos"} → _paste 가 엄격히 거름
    (media: → <공개 주소>/media/<파일> · https 가 아닌 사진은 뺌 · 허용한 태그·속성만 다시 씀). 공개 주소가 없으면 paste 는 None.
"""

from __future__ import annotations

import html as htmllib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import OK, STALE, UNAVAILABLE, https_url, safe_text
from dealbot.dashboard.remote_login import TARGETS as REMOTE_TARGETS

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
    login: str | None = None  # 로그인 할 일 줄의 원격 로그인 대상 (REMOTE_TARGETS 의 이름만 — 주인 10/26)


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
        login=v["login"] if isinstance(v.get("login"), str) and v["login"] in REMOTE_TARGETS else None,
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


READ_TEXT_MAX = 6000


def _read(v: Any) -> dict[str, str] | None:
    """테스트 글 '글 보기' (주인 10/23 — 어느 글인지 보게): {url: 발행 주소(https 만, 없으면 ''), text: 본문 여러 줄}. 둘 다 없으면 None."""
    if not isinstance(v, dict):
        return None
    out = {"url": https_url(v.get("url")) or "", "text": _th_text(v.get("text"), READ_TEXT_MAX)}
    return out if out["url"] or out["text"] else None


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
            posts.append({"pid": pid, "title": title, "pieces": pieces, "items": items, "read": _read(x.get("read"))})
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
                      "slots": slots, "feedback_done": _count(x.get("feedback_done"), top=10**6) or 0, "read": _read(x.get("read"))})
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


DEVLOG_MAX = 15
DEVLOG_REPOS = ("블로그", "딜봇")


def _devlog(v: Any) -> dict[str, Any] | None:
    """'오늘 바뀐 것' (주인 10/23 — Blog-Auto devlog.py): {day, items: [{at, repo, text, count?}], n}. 칸이 없으면 None.
    줄마다 따로 걸러 이상한 줄만 빠진다 (시각은 HH:MM, 프로그램은 블로그|딜봇)."""
    if not isinstance(v, dict) or not isinstance(v.get("items"), list):
        return None
    items = []
    for x in v["items"][:DEVLOG_MAX]:
        if (isinstance(x, dict) and x.get("repo") in DEVLOG_REPOS and isinstance(x.get("at"), str)
                and re.fullmatch(r"\d{2}:\d{2}", x["at"]) and (t := _one_line(x.get("text"), 100))):
            items.append({"at": x["at"], "repo": x["repo"], "text": t, "count": _count(x.get("count"), top=999) or 1})
    return {"day": _one_line(v.get("day"), 10), "items": items, "n": max(_count(v.get("n"), top=9999) or 0, len(items))}


OPS_RESULTS = ("ok", "fixed", "blocked", "owner")
OPS_LINES_MAX = 30


def _opscheck(v: Any) -> dict[str, Any] | None:
    """'직전 AI 점검' (주인 10/25 — Blog-Auto opscheck.py): {at 'MM/DD HH:MM', result, summary, lines}. 모양이 틀리면 None (칸이 안 보임)."""
    if not isinstance(v, dict) or v.get("result") not in OPS_RESULTS or not isinstance(v.get("at"), str) \
            or not re.fullmatch(r"\d{2}/\d{2} \d{2}:\d{2}", v["at"]) or not (summary := _one_line(v.get("summary"), 80)):
        return None
    lines = [t for x in (v.get("lines") if isinstance(v.get("lines"), list) else [])[:OPS_LINES_MAX] if (t := _one_line(x, 160))]
    return {"at": v["at"], "result": v["result"], "summary": summary, "lines": lines}


# 쇼핑몰 크리에이터 통계 (주인 10/23 — Blog-Auto creator_stats.export): 쇼핑몰별 이번 달 방문·구매·예상 리워드와 합계
CM_STATES = ("ok", "login", "failed", "pending", "none")
CM_CONN = ("ok", "expired", "failed", "unknown")
CM_NUMS = ("visits", "orders", "order_amount", "reward", "expected", "last_month")
CM_MALLS_MAX, CM_MAX = 8, 10**10
CM_ITEMS_MAX = 20  # 구매 상품 목록 (토스 — 주인 10/23 "토스쇼핑도 무슨 물건을 샀는지")


def _cm_items(v: Any) -> list[dict[str, Any]] | None:
    """[{name, qty, refunded, amount, reward, via}] — 이름 없는 줄은 빠지고 숫자는 0 이상 정수만. 목록이 아니면 None (토스 말고는 없음)."""
    if not isinstance(v, list):
        return None
    out = []
    for it in v[:CM_ITEMS_MAX]:
        if not isinstance(it, dict) or not (name := _one_line(it.get("name"), 80)):
            continue
        out.append({"name": name, **{k: _count(it.get(k), top=CM_MAX) for k in ("qty", "refunded", "amount", "reward")},
                    "via": _one_line(it.get("via"), 20) or ""})
    return out


CM_WINDOWS = ("오늘", "7일", "30일")


def _cm_windows(v: Any) -> list[dict[str, Any]] | None:
    """제휴 수익 양식 (주인 10/23): [{label 오늘|7일|30일, visits, orders, amount, commission}] — 숫자는 _count (모르면 None)."""
    if not isinstance(v, list):
        return None
    got = {w.get("label"): w for w in v if isinstance(w, dict) and w.get("label") in CM_WINDOWS}
    return [{"label": label, **{k: _count((got.get(label) or {}).get(k), top=CM_MAX) for k in ("visits", "orders", "amount", "commission")}}
            for label in CM_WINDOWS]


CM_RULES_MAX = 8


def _cm_rules(v: Any) -> list[str]:
    """보상 기준 글 (주인 10/23 — "보상이 얼마인지, 기준이 어떻게 되는지"): 한 줄 글만, 최대 CM_RULES_MAX 줄."""
    if not isinstance(v, list):
        return []
    return [t for t in (_one_line(x, 200) for x in v[:CM_RULES_MAX]) if t]


def _cm_challenge(v: Any) -> dict[str, Any] | None:
    """에이블리 방문 수 챌린지 {status, round, visits, started, ends, content, earned, next {visits, reward}, tiers [{visits, reward}],
    paid, result, history [{round, visits, reward, ended, result}]} — 숫자는 _count, 이상한 단계·지난 판 줄은 빠짐. 없으면 None."""
    if not isinstance(v, dict):
        return None

    def tier(t: Any) -> dict[str, int] | None:
        if not isinstance(t, dict):
            return None
        a, b = _count(t.get("visits"), top=CM_MAX), _count(t.get("reward"), top=CM_MAX)
        return {"visits": a, "reward": b} if a is not None and b is not None else None

    hist = []
    for h in (v.get("history") if isinstance(v.get("history"), list) else [])[:10]:
        if isinstance(h, dict):
            hist.append({"round": _count(h.get("round"), top=CM_MAX), "visits": _count(h.get("visits"), top=CM_MAX),
                         "reward": _count(h.get("reward"), top=CM_MAX), "ended": _one_line(h.get("ended"), 10) or "",
                         "result": _one_line(h.get("result"), 60) or ""})
    return {"status": _one_line(v.get("status"), 30) or "", "round": _count(v.get("round"), top=CM_MAX),
            "visits": _count(v.get("visits"), top=CM_MAX), "started": _one_line(v.get("started"), 10),
            "ends": _one_line(v.get("ends"), 10), "content": v.get("content") is True,
            "earned": _count(v.get("earned"), top=CM_MAX), "next": tier(v.get("next")),
            "tiers": [t for t in map(tier, v.get("tiers") if isinstance(v.get("tiers"), list) else []) if t][:12],
            "paid": _count(v.get("paid"), top=CM_MAX), "result": _one_line(v.get("result"), 60) or "", "history": hist}


def _creator_mall(v: Any) -> dict[str, Any] | None:
    """{read_at, month, malls: [{key, name, state, text, conn, checked_at, conn_since, visits?, orders?, order_amount?, reward?,
    expected?, last_month?, items (토스: 구매 상품 목록 · 그 밖 None), windows, rules (보상 기준 글), challenge (에이블리 방문 수 챌린지)}],
    total: {visits, orders, expected, last_month}}. 칸이 없으면(예전 Blog-Auto) None. 이름이 없거나 상태가 이상한 줄은 빠지고,
    숫자는 0 이상 CM_MAX 이하 정수만 (아니면 None — 짐작하지 않음). 합계는 남은 줄로 다시 셈."""
    if not isinstance(v, dict) or not isinstance(v.get("malls"), list):
        return None
    malls = []
    for m in v["malls"][:CM_MALLS_MAX]:
        if not isinstance(m, dict) or not (name := _one_line(m.get("name"), 20)) or m.get("state") not in CM_STATES:
            continue
        row: dict[str, Any] = {"key": _one_line(m.get("key"), 20) or name, "name": name, "state": m["state"],
                               "text": _one_line(m.get("text"), 80) or "",
                               "conn": m.get("conn") if m.get("conn") in CM_CONN else "unknown",
                               "checked_at": _one_line(m.get("checked_at"), 20), "conn_since": _one_line(m.get("conn_since"), 20)}
        for k in CM_NUMS:
            row[k] = _count(m.get(k), top=CM_MAX)
        row["items"] = _cm_items(m.get("items"))
        row["windows"] = _cm_windows(m.get("windows"))
        row["rules"] = _cm_rules(m.get("rules"))
        row["challenge"] = _cm_challenge(m.get("challenge"))
        malls.append(row)

    def total(k: str) -> int | None:
        got = [r[k] for r in malls if r.get(k) is not None]
        return sum(got) if got else None

    return {"read_at": _one_line(v.get("read_at"), 20), "month": _one_line(v.get("month"), 10), "malls": malls,
            "total": {k: total(k) for k in ("visits", "orders", "expected", "last_month")}}


EARN_DAYS_MAX = 35
EARN_ORDERS_MAX = 40


def _earnings(v: Any) -> dict[str, Any] | None:
    """제휴 수익 (주인 10/23 — Blog-Auto earnings.py): {shopconnect: {confirmed, read_at, days: [{day, clicks, orders, amount, commission}],
    orders: [{day, name, amount, commission}]}}. 칸이 없으면(예전 Blog-Auto) None. confirmed 가 false 면 줄 없이 그것만. 금액은 원 정수."""
    sc = v.get("shopconnect") if isinstance(v, dict) else None
    if not isinstance(sc, dict) or not isinstance(sc.get("confirmed"), bool):
        return None
    top = 10**11
    days, orders = [], []
    if sc["confirmed"]:
        for x in sc.get("days")[:EARN_DAYS_MAX] if isinstance(sc.get("days"), list) else []:
            if isinstance(x, dict) and isinstance(x.get("day"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x["day"]):
                days.append({"day": x["day"], **{k: _count(x.get(k), top=top) or 0 for k in ("clicks", "orders", "amount", "commission")}})
        for x in sc.get("orders")[:EARN_ORDERS_MAX] if isinstance(sc.get("orders"), list) else []:
            if isinstance(x, dict) and isinstance(x.get("day"), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", x["day"]):
                orders.append({"day": x["day"], "name": _one_line(x.get("name"), 80) or "(상품 이름 없음)",
                               "amount": _count(x.get("amount"), top=top) or 0, "commission": _count(x.get("commission"), top=top) or 0})
    return {"shopconnect": {"confirmed": sc["confirmed"], "read_at": _one_line(sc.get("read_at"), 40) if sc["confirmed"] else None,
                            "days": days, "orders": orders}}


SC_STATES = ("off", "ok", "resting", "stopped", "unknown")
SC_REASON_RE = re.compile(r"^[a-z_]{1,30}$")


def _secs(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 10**11 else None


# 로그인 관리 (주인 10/26 — Blog-Auto remote_login.states → status.logins). 줄마다 따로 걸러 이상한 줄만 빠진다
LOGIN_STATES = ("ok", "needed", "resting", "off", "unknown")
LOGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
LOGINS_MAX, LOGIN_LABEL_MAX, LOGIN_NOTE_MAX, LOGIN_WHERE_MAX = 12, 40, 80, 80
_ISO_TZ_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:[+-][0-9]{2}:[0-9]{2}|Z)$")


def _iso_tz(v: Any) -> str | None:
    """시간대가 붙은 ISO 시각 글자만 그대로 (없거나 시간대가 없거나 날짜가 틀리면 None)."""
    if not isinstance(v, str) or not _ISO_TZ_RE.match(v):
        return None
    try:
        return v if datetime.fromisoformat(v.replace("Z", "+00:00")).tzinfo is not None else None
    except ValueError:
        return None


LOGIN_LAST_RESULTS = ("ok", "keep_off", "timeout", "closed", "replaced")


def _login_last(v: Any) -> dict[str, str] | None:
    """마지막 원격 로그인 {result, at} (Blog-Auto 10/27 — 네이버 세 줄). 결과가 정한 다섯 가지가 아니거나 시각이 틀리면 None."""
    if not isinstance(v, dict) or v.get("result") not in LOGIN_LAST_RESULTS:
        return None
    at = _iso_tz(v.get("at"))
    return {"result": v["result"], "at": at} if at else None


def _logins(v: Any) -> list[dict[str, Any]] | None:
    """[{id, label, state, note, checked_at, since, until, target, where, last}] — 칸이 없으면(예전 Blog-Auto) None.
    last = 마지막 원격 로그인 {result: ok|keep_off|timeout|closed|replaced, at} 또는 None (_login_last).
    remote_today = 24시간 안에 연 원격 로그인 창 수 (0 이상 정수, 기본 0) · remote_block = 다음 누름은 force 없으면 거절 (참/거짓, 기본 거짓).
    target 은 딜봇 remote_login.TARGETS 에 있는 이름만 (모르는 이름·null 은 None → 단추 없이 읽기만). 같은 id 는 첫 줄만."""
    if not isinstance(v, list):
        return None
    out: list[dict[str, Any]] = []
    for x in v[:LOGINS_MAX]:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not LOGIN_ID_RE.match(x["id"]):
            continue
        label = _one_line(safe_text(x.get("label"), LOGIN_LABEL_MAX * 2), LOGIN_LABEL_MAX) if isinstance(x.get("label"), str) else None
        if not label or any(r["id"] == x["id"] for r in out):
            continue
        text = {k: _one_line(safe_text(x.get(k), n * 2), n) or "" if isinstance(x.get(k), str) else ""
                for k, n in (("note", LOGIN_NOTE_MAX), ("where", LOGIN_WHERE_MAX))}
        out.append({"id": x["id"], "label": label, "state": x.get("state") if x.get("state") in LOGIN_STATES else "unknown",
                    "note": text["note"], **{k: _iso_tz(x.get(k)) for k in ("checked_at", "since", "until")},
                    "target": x["target"] if isinstance(x.get("target"), str) and x["target"] in REMOTE_TARGETS else None,
                    "where": text["where"], "last": _login_last(x.get("last")),
                    "remote_today": n if type(n := x.get("remote_today")) is int and n >= 0 else 0,
                    "remote_block": x.get("remote_block") is True})
    return out


NV_BLOGS = {"finance": "아카이브", "issue": "데런픽", "hotdeal": "핫딜"}


def _naver_visits(v: Any) -> dict[str, Any] | None:
    """네이버 접속 (주인 10/27 — Blog-Auto site_visits.naver_summary): {blogs: [{blog, name, today, day24, cap, blocked24, warm,
    side_rest, jobs: [{job, n}]}], all24, all_cap}. 칸이 없으면(예전 Blog-Auto) None."""
    if not isinstance(v, dict) or not isinstance(v.get("blogs"), list):
        return None
    out: list[dict[str, Any]] = []
    for b in v["blogs"][:3]:
        if not isinstance(b, dict) or b.get("blog") not in NV_BLOGS or any(x["blog"] == b["blog"] for x in out):
            continue
        jobs = [{"job": _one_line(safe_text(j.get("job"), 80), 40) or "", "n": _count(j.get("n"), top=10000) or 0}
                for j in (b.get("jobs") or [])[:5] if isinstance(j, dict) and isinstance(j.get("job"), str)]
        out.append({"blog": b["blog"], "name": NV_BLOGS[b["blog"]], **{k: _count(b.get(k), top=10000) or 0
                    for k in ("today", "day24", "cap", "blocked24")},
                    "warm": b.get("warm") is True, "side_rest": b.get("side_rest") is True, "jobs": jobs})
    return {"blogs": out, "all24": _count(v.get("all24"), top=100000) or 0, "all_cap": _count(v.get("all_cap"), top=100000) or 0}


# 오늘 할 일 (주인 10/27 — 네이버 블로그 반자동: 발행·예약·이웃 공감/댓글/신청·SNS 팔로우를 주인이 하루 5번 10~15분씩 손으로).
# Blog-Auto 가 status.routine 에 그날 목록을 쓴다 → routine.py · static/routine.js (할 일 탭 맨 위). 줄마다 따로 걸러 이상한 줄만 빠진다
ROUTINE_SLOT_KEYS = ("s1", "s2", "s3", "s4", "s5")
ROUTINE_DEFAULT_SLOTS = (("s1", "09:00", "아침"), ("s2", "12:00", "점심"), ("s3", "15:00", "오후"), ("s4", "18:00", "저녁"),
                         ("s5", "21:00", "밤"))
ROUTINE_GROUPS = ("발행", "예약", "이웃", "SNS")
ROUTINE_BLOGS = ("finance", "issue", "hotdeal", "")
ROUTINE_ID_RE = re.compile(r"^[a-z0-9:_-]{1,80}$")
_ROUTINE_DAY_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
_ROUTINE_AT_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
ROUTINE_ITEMS_MAX, ROUTINE_COPY_MAX, ROUTINE_LINKS_MAX = 80, 4, 3
ROUTINE_TITLE_MAX, ROUTINE_NOTE_MAX, ROUTINE_LABEL_MAX, ROUTINE_COPY_TEXT_MAX, ROUTINE_COPY_LINES = 80, 160, 20, 1000, 12


def _rt_line(v: Any, n: int) -> str:
    """한 줄 글 (토큰 모양은 가림 · 보이지 않는 글자 뺌 · n 자). 글자가 아니면 ''."""
    if not isinstance(v, str):
        return ""
    return _one_line("".join(c for c in safe_text(v, n * 2) if c.isprintable()), n) or ""


def _rt_copy_text(v: Any) -> str:
    """복사할 글 — 줄바꿈은 살린다 (댓글·SNS 글), 줄마다 _rt_line · 모두 ROUTINE_COPY_TEXT_MAX 자 안."""
    if not isinstance(v, str):
        return ""
    lines = [_rt_line(x, ROUTINE_COPY_TEXT_MAX) for x in v.splitlines()[:ROUTINE_COPY_LINES]]
    return "\n".join(lines).strip("\n")[:ROUTINE_COPY_TEXT_MAX]


# 붙여 넣기 (주인 10/27 — 네이버 글쓰기 화면에 대시보드에서 복사한 글을 붙여 넣음: 서버가 네이버에 로그인하지 않음)
PASTE_TITLE_MAX, PASTE_HTML_MAX, PASTE_TAGS_MAX, PASTE_TAG_MAX, PASTE_COUNT_MAX = 120, 200_000, 30, 40, 1000
PASTE_GROUPS = ("발행", "예약")
PASTE_NO_BASE = "본문 복사 꺼짐 — 봇 공개 주소(public_domain)가 없어요"
_MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
# 다시 쓰는 태그 (그 밖의 태그는 빼고 안의 글만 남김) · 안의 글까지 통째로 빼는 태그
_PASTE_TAGS = frozenset("p div span br b strong i em u s strike del ins sub sup small mark font center h1 h2 h3 h4 h5 h6 blockquote q "
                        "ul ol li dl dt dd a img table thead tbody tfoot tr td th caption colgroup col hr pre code figure figcaption "
                        "section article header footer".split())
_PASTE_DROP = frozenset("script style iframe frame frameset object embed applet noscript template textarea select option button "
                        "svg math head title xmp plaintext noembed noframes".split())
_PASTE_VOID = frozenset(("br", "img", "hr", "col"))
_PASTE_BLOCK = frozenset("p div h1 h2 h3 h4 h5 h6 blockquote li dt dd tr pre figure figcaption section article header footer table ul ol "
                         "center hr".split())
_PASTE_ATTRS = frozenset(("style", "class", "align", "color", "size", "face", "href", "src", "alt", "title", "width", "height",
                          "colspan", "rowspan", "valign", "border", "cellpadding", "cellspacing"))
_BAD_STYLE = re.compile(r"url\s*\(|expression|javascript:|@import|behavior|-moz-binding|\\", re.I)


def _link_ok(u: str) -> bool:
    """글 속 링크 (a href): http·https 만, 공백·따옴표·꺾쇠 없이."""
    return bool(re.match(r"^https?://[^\s\"'<>`\\]+$", u, re.I)) and len(u) <= 2000


class _PasteHTML(HTMLParser):
    """붙여 넣을 본문을 다시 쓴다 — 믿지 않는 HTML: 허용한 태그·속성만, on* 속성·스크립트·바깥 틀(iframe 등)은 뺌,
    a 는 http(s) 만, 사진은 media:<파일> → <base>/media/<파일> 로 바꾼 뒤 https 인 것만 (아니면 사진째 뺌). 글만 뽑은 text 도 같이."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.text: list[str] = []
        self.skip, self.skip_tag = 0, ""  # _PASTE_DROP 안 (글까지 뺌) — 바깥 태그 이름과 같은 태그가 겹친 수
        self.open: list[str] = []
        self.photos = 0

    def _src(self, v: str | None) -> str | None:
        v = (v or "").strip()
        if v.lower().startswith("media:"):
            name = v[6:]
            return f"{self.base}/media/{name}" if _MEDIA_NAME_RE.match(name) else None
        return https_url(v)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or tag in _PASTE_DROP:
            if not self.skip or tag == self.skip_tag:
                self.skip, self.skip_tag = self.skip + 1, tag
            return
        if tag not in _PASTE_TAGS:
            return
        kept: list[str] = []
        for k, v in attrs:
            k, v = k.lower(), v or ""
            if k not in _PASTE_ATTRS or (k == "href" and (tag != "a" or not _link_ok(v.strip()))) or k == "src":
                continue
            if k == "style" and _BAD_STYLE.search(v):
                continue
            kept.append(f' {k}="{htmllib.escape(v.strip() if k == "href" else v, quote=True)}"')
        if tag == "img":
            src = self._src(next((v for k, v in attrs if k.lower() == "src"), None))
            if src is None:
                return  # https 가 아닌 사진은 통째로 뺌
            kept.insert(0, f' src="{htmllib.escape(src, quote=True)}"')
            self.photos += 1
        if tag in _PASTE_BLOCK or tag == "br":
            self.text.append("\n")
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in _PASTE_VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or tag in _PASTE_DROP:
            return  # <x/> — 열린 채로 남지 않음
        self.handle_starttag(tag, attrs)
        if tag not in _PASTE_VOID and self.open and self.open[-1] == tag:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
            return
        if tag not in self.open:
            return
        while self.open:  # 안 닫힌 태그는 같이 닫음 (짝이 맞는 HTML 만 내보냄)
            t = self.open.pop()
            self.out.append(f"</{t}>")
            if t in _PASTE_BLOCK:
                self.text.append("\n")
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(htmllib.escape(data, quote=False))
            self.text.append(data)

    def result(self) -> tuple[str, str]:
        self.close()
        self.out += [f"</{t}>" for t in reversed(self.open)]
        lines = [" ".join(x.split()) for x in "".join(self.text).split("\n")]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
        return "".join(self.out), text


def _paste_tag(v: Any) -> str | None:
    """태그 하나: 한 줄 · 앞의 # 와 사이 공백·쉼표는 뺌 (네이버 태그는 띄어 쓰지 않음) · PASTE_TAG_MAX 자를 넘으면 버림."""
    if not isinstance(v, str):
        return None
    t = re.sub(r"[\s#,]+", "", "".join(c for c in v if c.isprintable()))
    return t if 0 < len(t) <= PASTE_TAG_MAX else None


def _paste(v: Any, media_base: str | None) -> dict[str, Any] | None:
    """{title, html, text, tags, tag_text, cards, photos} — 모양이 틀리면 None. media_base = 봇 공개 주소 (https://도메인)."""
    if not isinstance(v, dict) or not isinstance(v.get("html"), str) or len(v["html"]) > PASTE_HTML_MAX or not media_base:
        return None
    title = _rt_line(v.get("title"), PASTE_TITLE_MAX)
    base = media_base.rstrip("/")
    if not title or https_url(base + "/media/x") is None:
        return None
    p = _PasteHTML(base)
    try:
        p.feed(v["html"])
        body, text = p.result()
    except Exception:  # noqa: BLE001 — html.parser 가 못 읽는 글이면 이 칸만 버림
        return None
    if not text and not p.photos:
        return None
    tags: list[str] = []
    for t in v.get("tags")[:PASTE_TAGS_MAX * 2] if isinstance(v.get("tags"), list) else []:
        if (t := _paste_tag(t)) and t not in tags and len(tags) < PASTE_TAGS_MAX:
            tags.append(t)
    return {"title": title, "html": body, "text": text, "tags": tags, "tag_text": " ".join("#" + t for t in tags),
            "cards": _count(v.get("cards"), top=PASTE_COUNT_MAX) or 0, "photos": _count(v.get("photos"), top=PASTE_COUNT_MAX) or 0}


def _routine_item(x: Any, slot_keys: tuple[str, ...] | set[str] = ROUTINE_SLOT_KEYS, *,
                  media_base: str | None = None) -> dict[str, Any] | None:
    """{id, slot, group, blog, title, note, min, copy: [{label, text}], links: [{label, url}], paste} — 모양이 틀리면 None.
    스레드 세션 몫(나중에 routine.threads_items)도 같은 모양이라 이 함수 하나로 거른다.
    paste (발행·예약 줄만): _paste 가 거른 붙여 넣기 글 · 없거나 틀리면 None · 공개 주소가 없으면 None 에 note 끝에 PASTE_NO_BASE."""
    if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not ROUTINE_ID_RE.match(x["id"]):
        return None
    if x.get("slot") not in slot_keys or x.get("group") not in ROUTINE_GROUPS or not (title := _rt_line(x.get("title"), ROUTINE_TITLE_MAX)):
        return None
    m = x.get("min")
    mins = min(15, max(1, round(m))) if isinstance(m, int | float) and not isinstance(m, bool) and math.isfinite(m) else 5
    copy = []
    for c in x.get("copy")[:ROUTINE_COPY_MAX] if isinstance(x.get("copy"), list) else []:
        if isinstance(c, dict) and (text := _rt_copy_text(c.get("text"))):
            copy.append({"label": _rt_line(c.get("label"), ROUTINE_LABEL_MAX) or "복사", "text": text})
    links = []
    for k in x.get("links")[:ROUTINE_LINKS_MAX] if isinstance(x.get("links"), list) else []:
        if isinstance(k, dict) and (u := https_url(k.get("url"))) is not None:
            links.append({"label": _rt_line(k.get("label"), ROUTINE_LABEL_MAX) or "열기", "url": u})
    note = _rt_line(x.get("note"), ROUTINE_NOTE_MAX)
    paste = _paste(x.get("paste"), media_base) if x["group"] in PASTE_GROUPS else None
    if paste is None and x["group"] in PASTE_GROUPS and isinstance(x.get("paste"), dict) and not media_base:
        note = " · ".join(t for t in (note, PASTE_NO_BASE) if t)[:ROUTINE_NOTE_MAX]
    return {"id": x["id"], "slot": x["slot"], "group": x["group"], "blog": x.get("blog") if x.get("blog") in ROUTINE_BLOGS else "",
            "title": title, "note": note, "min": mins, "copy": copy, "links": links, "paste": paste}


def _routine_slots(v: Any) -> list[dict[str, str]]:
    """[{key, at, label}] s1~s5 순서 · 같은 key 는 첫 줄만 · 하나도 맞지 않으면 기본 5칸 (아침·점심·오후·저녁·밤)."""
    got: dict[str, dict[str, str]] = {}
    for s in v[:10] if isinstance(v, list) else []:
        if isinstance(s, dict) and s.get("key") in ROUTINE_SLOT_KEYS and s["key"] not in got and isinstance(s.get("at"), str) \
                and _ROUTINE_AT_RE.match(s["at"]):
            dflt = next(lab for k, _a, lab in ROUTINE_DEFAULT_SLOTS if k == s["key"])
            got[s["key"]] = {"key": s["key"], "at": s["at"], "label": _rt_line(s.get("label"), 10) or dflt}
    if not got:
        return [{"key": k, "at": a, "label": lab} for k, a, lab in ROUTINE_DEFAULT_SLOTS]
    return [got[k] for k in ROUTINE_SLOT_KEYS if k in got]


def _routine(v: Any, media_base: str | None = None) -> dict[str, Any] | None:
    """오늘 할 일 {day, slots: [{key, at, label}], items: [_routine_item], overflow} — 칸이 없으면(예전 Blog-Auto)·날짜가 틀리면 None.
    없는 시간대(slot)의 줄은 뺀다 · 같은 id 는 첫 줄만 · 바깥 주소는 https 만."""
    if not isinstance(v, dict) or not isinstance(v.get("items"), list) or not isinstance(v.get("day"), str) \
            or not _ROUTINE_DAY_RE.match(v["day"]):
        return None
    try:
        date.fromisoformat(v["day"])
    except ValueError:
        return None
    slots = _routine_slots(v.get("slots"))
    keys = {s["key"] for s in slots}
    items: list[dict[str, Any]] = []
    for x in v["items"][:ROUTINE_ITEMS_MAX]:
        if (it := _routine_item(x, keys, media_base=media_base)) is not None and not any(i["id"] == it["id"] for i in items):
            items.append(it)
    return {"day": v["day"], "slots": slots, "items": items, "overflow": _count(v.get("overflow"), top=1000) or 0,
            "times": _routine_times(v.get("times"))}


_ROUTINE_TIME_RE = re.compile(r"^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|바로)$")


def _routine_times(v: Any) -> list[dict[str, Any]]:
    """블로그별 예약 추천 시각 [{blog, name, times, note}] (주인 10/29 — Blog-Auto routine.best_times). 블로그마다 한 줄 · 틀린 줄은 뺌."""
    out: list[dict[str, Any]] = []
    for x in v[:len(ROUTINE_BLOGS)] if isinstance(v, list) else []:
        if not isinstance(x, dict) or x.get("blog") not in ROUTINE_BLOGS or not x.get("blog") or any(o["blog"] == x["blog"] for o in out):
            continue
        times = [t for t in x.get("times")[:8] if isinstance(t, str) and _ROUTINE_TIME_RE.match(t)] if isinstance(x.get("times"), list) else []
        if times:
            out.append({"blog": x["blog"], "name": _rt_line(x.get("name"), 10) or x["blog"], "times": times,
                        "note": _rt_line(x.get("note"), 80)})
    return out


def _shopconnect(v: Any) -> dict[str, Any] | None:
    """쇼핑커넥트 자동 링크 상태 (주인 10/24 — 연결 탭에서 실시간으로, Blog-Auto shopconnect.snapshot). 없으면 None."""
    if not isinstance(v, dict) or v.get("state") not in SC_STATES:
        return None
    t = v.get("today") if isinstance(v.get("today"), dict) else {}
    lim = v.get("limits") if isinstance(v.get("limits"), dict) else {}
    reason = v.get("reason") if isinstance(v.get("reason"), str) and SC_REASON_RE.match(v.get("reason")) else ""
    return {"state": v["state"], "reason": reason, "until": _secs(v.get("until")), "hours": _one_line(v.get("hours"), 20) or "",
            "queued": _count(v.get("queued"), top=1000) or 0,
            "today": {k: _count(t.get(k), top=1000) or 0 for k in ("searches", "issues")},
            "limits": {k: _count(lim.get(k), top=1000) or 0 for k in ("searches", "issues")},
            "last_at": _secs(v.get("last_at")), "last_ok_at": _secs(v.get("last_ok_at"))}


# 에이블리 링크 부탁 (주인 10/28 — Blog-Auto ably_requests.export: 서버는 에이블리를 열지 않고, 주인이 폰 에이블리 앱에서 링크를 만들어
# '오늘 할 일'에 붙여 넣음 → 다리 일 similar_links {blog: issue, pid, links: [하나], rid}). rid = '<글 번호>-<에이블리 상품 번호>'
ABLY_REQ_ROWS_MAX = 30  # Blog-Auto ably_requests.MAX_ROWS
ABLY_REQ_RID = re.compile(r"^\d{1,9}-[0-9A-Za-z_-]{1,40}$")  # actions.ABLY_RID 와 같게
ABLY_REQ_HOSTS = ("a-bly.com",)  # 상품 주소는 에이블리(하위 주소 포함 — m.a-bly.com)만
ABLY_REQ_NOTE_MAX = 160
ABLY_REQ_PRICE_MAX = 100_000_000
ABLY_REQ_REVIEWS_MAX = 10_000_000
# 주인 10/29 '사진 보고 디자인이 비슷해야' — 같은 상품 사진(blog_view/thumbs/ar-…, 네이버 이미지 검색 · 에이블리 서버 아님)·후기 수·만족 %·
# 글 속 옷과 AI 닮음(0~10). 모양이 틀리면 그 칸만 비움 (줄은 그대로)


def _ably_req_row(x: Any) -> dict[str, Any] | None:
    """{rid, pid, title, piece, name, price, shop, url, due, thumb, reviews, satisfaction, sim} — 모양이 틀리면 None (줄만 뺌).
    thumb·reviews·satisfaction·sim 은 덧붙인 정보라 틀리면 그 칸만 null·0."""
    if not isinstance(x, dict) or not isinstance(x.get("rid"), str) or not ABLY_REQ_RID.match(x["rid"]):
        return None
    pid = x.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid < 10**9 or x["rid"].split("-", 1)[0] != str(pid):
        return None
    u = https_url(x.get("url"))
    from urllib.parse import urlsplit

    host = (urlsplit(u).hostname or "").lower() if u else ""
    if not any(host == h or host.endswith("." + h) for h in ABLY_REQ_HOSTS):
        return None
    price, due = x.get("price"), x.get("due")
    if price is not None and _count(price, top=ABLY_REQ_PRICE_MAX) is None:
        return None
    if due is not None and (isinstance(due, bool) or not isinstance(due, int | float) or not math.isfinite(due) or not 1e9 < due < 1e10):
        return None
    name, piece = _rt_line(x.get("name"), 80), _rt_line(x.get("piece"), 20)
    if not name and not piece:
        return None
    return {"rid": x["rid"], "pid": pid, "title": _rt_line(x.get("title"), 60), "piece": piece, "name": name or piece,
            "price": _count(price) or 0, "shop": _rt_line(x.get("shop"), 40) or "에이블리", "url": u,
            "due": float(due) if due is not None else None, "thumb": th if isinstance(th := x.get("thumb"), str) and th.startswith("ar-")
            and THUMB_RE.match(th) else None, "reviews": _count(x.get("reviews"), top=ABLY_REQ_REVIEWS_MAX) or 0,
            "satisfaction": _count(x.get("satisfaction"), top=100) or 0, "sim": _count(x.get("sim"), top=10) or 0}


def _ably_requests(v: Any) -> dict[str, Any] | None:
    """{n, rows, note} — 칸이 없거나(예전 Blog-Auto) 모양이 틀리면 None. 틀린 줄·같은 rid 는 빼고 ABLY_REQ_ROWS_MAX 줄까지."""
    if not isinstance(v, dict) or not isinstance(v.get("rows"), list) or _count(v.get("n"), top=100_000) is None:
        return None
    rows: list[dict[str, Any]] = []
    for x in v["rows"][:ABLY_REQ_ROWS_MAX * 2]:
        if (r := _ably_req_row(x)) is not None and not any(o["rid"] == r["rid"] for o in rows):
            rows.append(r)
    rows = rows[:ABLY_REQ_ROWS_MAX]
    return {"n": len(rows), "rows": rows, "note": _rt_line(v.get("note"), ABLY_REQ_NOTE_MAX)}


# X(트위터) 초안함 (주인 10/25 — Blog-Auto x_drafts.export: X API 없음 · 주인이 'X에 올리기'로 손으로 올림)
X_STATUSES = ("draft", "posted", "dropped")
X_TEXT_MAX = 1000  # Blog-Auto 는 X 셈 280 안으로 보냄 — 한 번 더 (긴 글 구독이어도 화면 칸은 이만큼)
X_DRAFTS_MAX, X_IDEAS_MAX, X_FACTS_MAX = 20, 5, 6
X_REPLY_MAX, X_REPLY_LINES = 500, 8  # 첫 답글 (주인 10/26 새 기준 — 블로그 주소는 본문 말고 주인이 손으로 다는 첫 답글 · Blog-Auto 는 X 셈 280 안)
X_PLAN_DAYS, X_PLAN_POSTS, X_PLAN_REPLIES = 31, 6, 40  # 수익 계정 2주 계획 (주인 10/26) — 첫 상태 값엔 안 넣고 /dashboard/api/xplan 으로
X_DATE_RE = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
X_TIME_RE = re.compile(r"^[0-2][0-9]:[0-5][0-9]$")


def _x(v: Any) -> dict[str, Any] | None:
    """{per_day, limit, url_len, drafts: [{pid, title, url, text, status, created_at, posted_at, shop, reply}], ideas: [{id, title, facts, prompt}],
    plan: _x_plan | None}.
    칸이 없으면(예전 Blog-Auto) None → 초안 없음. 줄마다 따로 걸러 이상한 줄만 빠진다 (글 주소는 네이버 블로그만)."""
    if not isinstance(v, dict) or not isinstance(v.get("drafts"), list):
        return None
    drafts = []
    for x in v["drafts"][:X_DRAFTS_MAX]:
        if not isinstance(x, dict) or x.get("status") not in X_STATUSES or not (pid := _count(x.get("pid"), top=10**9)):
            continue
        text = _th_text(x.get("text"), X_TEXT_MAX)
        if not text:
            continue
        url = _naver_url(x.get("url")) or ""
        drafts.append({"pid": pid, "title": _one_line(x.get("title"), 80) or "", "url": url,
                       "text": text, "status": x["status"], "created_at": _one_line(x.get("created_at"), 40),
                       "posted_at": _one_line(x.get("posted_at"), 40), "shop": x.get("shop") is True, "reply": _x_reply(x.get("reply"), url)})
    ideas = []
    for c in (v.get("ideas") if isinstance(v.get("ideas"), list) else [])[:X_IDEAS_MAX]:
        if isinstance(c, dict) and (title := _one_line(c.get("title"), 60)):
            ideas.append({"id": _one_line(c.get("id"), 20) or "", "title": title, "prompt": _one_line(c.get("prompt"), 200) or "",
                          "facts": _str_list(c.get("facts"), X_FACTS_MAX, 120)})
    return {"per_day": _count(v.get("per_day"), top=50) or 0, "limit": _count(v.get("limit"), top=25_000) or 280,
            "url_len": _count(v.get("url_len"), top=100) or 23, "drafts": drafts, "ideas": ideas, "plan": _x_plan(v.get("plan"))}


def _plan_line(v: Any, limit: int) -> str | None:
    """한 줄 글자 — 제어 문자를 빼고 (_one_line 은 공백만 정리)."""
    return _one_line(re.sub(r"[\x00-\x1f\x7f]", "", v), limit) if isinstance(v, str) else None


def _x_reply(v: Any, url: str) -> str:
    """첫 답글 글자 (제휴 글이면 첫 줄 고지 · 끝 줄은 블로그 주소) — 줄은 그대로, 줄마다 제어 문자를 빼고 공백 정리 (_plan_line), 빈 줄은 뺌.
    글자가 아니거나 X_REPLY_LINES 줄·X_REPLY_MAX 자를 넘거나 끝 줄이 이 초안의 블로그 주소가 아니면 '' (새 기준 전 초안도 '' — 화면은 답글 칸을 숨김)."""
    if not isinstance(v, str) or not url or len(v) > X_REPLY_MAX * 2:
        return ""
    lines = [ln for ln in (_plan_line(x, X_REPLY_MAX) for x in v.replace("\r\n", "\n").split("\n")) if ln]
    text = "\n".join(lines)
    return text if 0 < len(lines) <= X_REPLY_LINES and len(text) <= X_REPLY_MAX and lines[-1] == url else ""


def _x_plan(v: Any) -> dict[str, Any] | None:
    """수익 계정 2주 계획 {title, start, end, rules, reply_rules, days: [{date, label, posts: [{time, head, outline, attach}]}],
    replies: [{for, text}]} — Blog-Auto config/x_plan.json. 주인이 고쳐서 손으로 올리는 초안 (주소 칸 없음 — 글자만). 글이 하나도 없으면 None."""
    if not isinstance(v, dict) or not isinstance(v.get("days"), list):
        return None
    days = []
    for d in v["days"][:X_PLAN_DAYS]:
        if not isinstance(d, dict) or not X_DATE_RE.match(str(d.get("date") or "")) or not isinstance(d.get("posts"), list):
            continue
        posts = [{"time": p["time"], "head": head, "outline": _plan_line(p.get("outline"), 300) or "", "attach": _plan_line(p.get("attach"), 160) or ""}
                 for p in d["posts"][:X_PLAN_POSTS]
                 if isinstance(p, dict) and X_TIME_RE.match(str(p.get("time") or "")) and (head := _plan_line(p.get("head"), 200))]
        if posts:
            days.append({"date": d["date"], "label": _plan_line(d.get("label"), 20) or d["date"][5:], "posts": posts})
    if not days:
        return None
    replies = [{"for": _plan_line(r.get("for"), 40) or "", "text": text} for r in (v.get("replies") if isinstance(v.get("replies"), list) else [])[:X_PLAN_REPLIES]
               if isinstance(r, dict) and (text := _plan_line(r.get("text"), 200))]
    return {"title": _plan_line(v.get("title"), 40) or "2주 계획", "start": days[0]["date"], "end": days[-1]["date"],
            "rules": _str_list(v.get("rules"), 6, 160), "reply_rules": _plan_line(v.get("reply_rules"), 200) or "", "days": days, "replies": replies}


# 룩북 릴스 (주인 10/25 — Blog-Auto lookbook_jobs.export): 지그재그 실제 상품 → 쇼츠 서버가 얼굴 없는 AI 모델 사진·영상(음악 없음) →
# 주인이 새 인스타 계정에 유행 음악을 붙여 손으로 올림. 영상·표지는 Blog-Auto 가 blog_view 에 둔 사본(없으면 var/lookbook)을 대시보드가 내보냄
LB_ID_RE = re.compile(r"^lb-[0-9]{8}-[0-9]{4}$")
LB_STATUSES = ("new", "waiting_server", "submitted", "rendering", "ready", "failed", "failed_match", "posted", "dropped", "expired")
LB_WARNS = ("video_has_dropped_look", "unscored")
LB_ABLY = ("queued", "put", "start", "skip", "none", "login", "failed", "no_link")
LB_MALLS = ("zigzag", "ably")
LB_LINK_HOSTS = ("zigzag.kr", "a-bly.com")  # 상품 링크는 이 두 쇼핑몰 주소(하위 주소 포함 — s.zigzag.kr · applink.a-bly.com)만
LB_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")  # 이유 코드 (no_token · looks_left · http_503 …) — 화면이 쉬운 말로 바꿈
LB_POSTED_RE = re.compile(r"^https://www\.instagram\.com/(?:[A-Za-z0-9_.]{1,30}/)?(?:p|reels?|tv)/[A-Za-z0-9_-]{5,40}/$")
LB_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
LB_ITEMS_MAX, LB_LOOKS_MAX, LB_CAPTION_MAX = 14, 10, 2200
LB_STYLES = ("mirror", "studio")  # 사진 양식 (주인 10/26 사진 시험 — 거울 셀카 · 예전 양식)


def _lb_link(v: Any) -> str:
    u = https_url(v)
    if u is None:
        return ""
    from urllib.parse import urlsplit

    host = (urlsplit(u).hostname or "").lower()
    return u if any(host == h or host.endswith("." + h) for h in LB_LINK_HOSTS) else ""


def _lb_reason(v: Any) -> str:
    """이유 코드만 ('server: …' 처럼 서버가 준 글은 'server' 로 — 영어 오류문을 주인 화면에 그대로 두지 않음)."""
    if not isinstance(v, str) or not v.strip():
        return ""
    v = v.strip()
    if v.startswith("server"):
        return "server"
    return v if LB_REASON_RE.match(v) else ""


def _lb_look(x: Any, jid: str) -> dict[str, Any] | None:
    if not isinstance(x, dict) or not (n := _count(x.get("n"), top=LB_LOOKS_MAX)):
        return None
    thumb = x.get("thumb")
    thumb = thumb if isinstance(thumb, str) and THUMB_RE.match(thumb) and thumb.startswith(jid + "-") else None
    product = x.get("product_thumb") if x.get("product_thumb") == f"{jid}-p{n}.jpg" else None  # 사진 시험의 상품 사진 사본 (이름이 정해짐)
    return {"n": n, "name": _one_line(x.get("name"), 60) or f"Look {n}", "link": _lb_link(x.get("link")),
            "price": _count(x.get("price"), top=100_000_000) or 0, "mall": x.get("mall") if x.get("mall") in LB_MALLS else "",
            "score": _count(x.get("score"), top=10), "dropped": x.get("dropped") is True, "thumb": thumb, "product_thumb": product}


def _lb_item(x: Any) -> dict[str, Any] | None:
    if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not LB_ID_RE.match(x["id"]) or x.get("status") not in LB_STATUSES:
        return None
    jid = x["id"]
    looks, seen = [], set()
    for lk in (x.get("looks") if isinstance(x.get("looks"), list) else [])[:LB_LOOKS_MAX]:
        if (row := _lb_look(lk, jid)) is not None and row["n"] not in seen:
            seen.add(row["n"])
            looks.append(row)
    video = x.get("video") if x.get("video") == f"lookbook/{jid}/video.mp4" else None  # var 기준 (Blog-Auto 가 쓴 그대로만)
    cover = x.get("cover") if x.get("cover") == f"lookbook/{jid}/cover.jpg" else None
    view_video = x.get("view_video") if x.get("view_video") == f"{jid}.mp4" else None  # blog_view/reels 사본
    view_cover = x.get("view_cover") if x.get("view_cover") == f"{jid}-c.jpg" else None  # blog_view/thumbs 사본
    posted = x.get("posted_url") if isinstance(x.get("posted_url"), str) and LB_POSTED_RE.match(x["posted_url"]) else None
    trial = x.get("trial") is True  # 사진 시험 (주인 10/26) — 바탕 릴스 번호(trial_of)가 맞아야만
    base = x.get("trial_of")
    base = base if trial and isinstance(base, str) and LB_ID_RE.fullmatch(base) and base != jid else None
    if trial and base is None:
        return None  # 바탕 번호가 이상한 시험 줄은 버림 (보통 릴스로 보여 '올리기' 할 일이 생기면 안 됨)
    return {"id": jid, "theme": _one_line(x.get("theme"), 40) or "", "hook": _one_line(x.get("hook"), 80) or "",
            "status": x["status"], "reason": _lb_reason(x.get("reason")), "warn": x.get("warn") if x.get("warn") in LB_WARNS else None,
            "caption": _th_text(x.get("caption"), LB_CAPTION_MAX), "looks": looks,
            "has_video": x.get("has_video") is True and bool(video or view_video),
            "has_cover": x.get("has_cover") is True and bool(cover or view_cover),
            "video": video, "cover": cover, "view_video": view_video, "view_cover": view_cover,
            "created_at": _one_line(x.get("created_at"), 40), "posted_url": posted,
            "ably": x.get("ably") if x.get("ably") in LB_ABLY else None,
            "trial": trial, "trial_of": base, "style": x.get("style") if x.get("style") in LB_STYLES else None}


def _lookbook(v: Any) -> dict[str, Any] | None:
    """{enabled, per_day, server, instagram, style, items: [{id, theme, hook, status, reason, warn, caption, looks, has_video, has_cover,
    video, cover, view_video, view_cover, created_at, posted_url, ably, trial, trial_of, style}]} (새 것부터). 칸이 없으면(예전 Blog-Auto) None → 화면은
    '업데이트를 기다리는 중'. 줄마다 따로 걸러 이상한 줄만 빠진다 (같은 번호는 처음 것만 · 링크는 지그재그·에이블리 https 만).
    사진 시험(주인 10/26): trial 줄은 trial_of(바탕 릴스 번호)가 있어야 시험으로 보고 · looks[].product_thumb = <id>-p<n>.jpg 만 · style(칸) = 지금 양식."""
    if not isinstance(v, dict) or not isinstance(v.get("items"), list):
        return None
    items, seen = [], set()
    for x in v["items"][:LB_ITEMS_MAX]:
        if (row := _lb_item(x)) is not None and row["id"] not in seen:
            seen.add(row["id"])
            items.append(row)
    ig = v.get("instagram").lstrip("@") if isinstance(v.get("instagram"), str) else ""
    return {"enabled": v.get("enabled") is True, "per_day": _count(v.get("per_day"), top=10) or 0,
            "server": v.get("server") if v.get("server") in ("ok", "no_token") else "",
            "instagram": ig if LB_HANDLE_RE.match(ig) else None, "style": v.get("style") if v.get("style") in LB_STYLES else None,
            "items": items}


POST_COUNT_NAMES = {"blogs": V2_BLOGS, "channels": ("threads", "threads_auto", "x")}
POST_COUNT_DAYS_MAX = 62  # 30일 칸 — 넉넉히 이만큼까지만 읽음
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day_counts(v: Any) -> dict[str, int] | None:
    """{'YYYY-MM-DD': n} — 이상한 줄만 뺀다. 사전이 아니면 None (그 프로그램 기록을 못 읽음 → 카드에 '—')."""
    if not isinstance(v, dict):
        return None
    out: dict[str, int] = {}
    for d, n in list(v.items())[:POST_COUNT_DAYS_MAX]:
        if isinstance(d, str) and DAY_RE.fullmatch(d) and isinstance(n, int) and not isinstance(n, bool) and 0 < n <= 10_000:
            out[d] = n
    return out


def _post_counts(v: Any) -> dict[str, dict[str, dict[str, int] | None]] | None:
    """올린 글 수 (주인 10/25 — Blog-Auto post_counts.py): {blogs: {finance|issue|hotdeal: {날짜: n}},
    channels: {threads|threads_auto|x: {날짜: n} | None}}. 칸이 없으면(예전 Blog-Auto) None → 카드마다 '—'."""
    if not isinstance(v, dict):
        return None
    out: dict[str, dict[str, dict[str, int] | None]] = {}
    for group, names in POST_COUNT_NAMES.items():
        g = v.get(group) if isinstance(v.get(group), dict) else {}
        out[group] = {name: _day_counts(g.get(name)) for name in names}
    return out


def parse_v2(data: dict[str, Any], media_base: str | None = None) -> dict[str, Any]:
    """v2 의 새 칸들 → 걸러 둔 사전 (블로그마다 따로 걸러 이상한 줄만 빠짐). 바깥 주소는 https 만, 글 주소는 네이버 블로그만."""
    out: dict[str, Any] = {"posts": {}, "recent": {}, "scan": None, "status": {"blogs": {}, "kakao": {}}, "digest": {},
                           "schedule": _sched(data.get("schedule")), "visits": _visits(data.get("visits")),
                           "pics_jobs": _pic_jobs(data.get("pics_jobs")), "trends": _trends(data.get("trends")),
                           "learn": _learn(data.get("learn")), "photo_trial": _photo_trial(data.get("photo_trial")),
                           "product_trial": _product_trial(data.get("product_trial")),
                           "threads": _threads(data.get("threads")), "devlog": _devlog(data.get("devlog")),
                           "opscheck": _opscheck(data.get("opscheck")),
                           "creator_mall": _creator_mall(data.get("creator_mall")),
                           "earnings": _earnings(data.get("earnings")), "x": _x(data.get("x")),
                           "post_counts": _post_counts(data.get("post_counts")),
                           "lookbook": _lookbook(data.get("lookbook")),
                           "ably_requests": _ably_requests(data.get("ably_requests"))}  # 에이블리 링크 부탁 (주인 10/28 — 할 일 탭)
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
             # 인기 예상 점수 (주인 10/25 — 키자드 하루 5개를 높은 순으로 고를 때) — 없으면 칸 없음
             **({"score": int(x["score"])} if isinstance(x.get("score"), int | float) and not isinstance(x.get("score"), bool) else {}),
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
    out["status"]["shopconnect"] = _shopconnect(st.get("shopconnect"))
    out["status"]["logins"] = _logins(st.get("logins"))  # 로그인 관리 칸 (주인 10/26 — 없으면 None: 예전 Blog-Auto)
    out["status"]["naver_visits"] = _naver_visits(st.get("naver_visits"))  # 네이버 접속 칸 (주인 10/27)
    out["status"]["routine"] = _routine(st.get("routine"), media_base)  # 오늘 할 일 (주인 10/27 — 없으면 None: 예전 Blog-Auto)
    dg = data.get("digest") if isinstance(data.get("digest"), dict) else {}
    out["digest"] = {"day": _one_line(dg.get("day"), 10), "generated_at": _one_line(dg.get("generated_at"), 40),
                     "blogs": {b: _str_list((dg.get("blogs") or {}).get(b) if isinstance(dg.get("blogs"), dict) else None, 8, 200)
                               for b in V2_BLOGS},
                     "waiting": _str_list(dg.get("waiting"), 8, 200)}
    return out


def parse_export(data: Any, tz: str, media_base: str | None = None) -> BlogAutoExport:
    """media_base: 봇 공개 주소 (https://도메인 — 할 일 '붙여 넣기' 글의 사진 주소 앞부분). 없으면 붙여 넣기 칸은 None."""
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
                          version=version, v2=parse_v2(data, media_base) if version == 2 else {})


class BlogAutoReader:
    """파일이 바뀌었을 때만 다시 읽는다 (카드 세 개가 한 번 읽은 것을 같이 씀)."""

    def __init__(self, path: str | Path, *, tz: str, stale_hours: float, now: Callable[[], datetime],
                 media_base: str | None = None) -> None:
        self.path = Path(str(path)).expanduser()
        self.media_base = media_base  # 봇 공개 주소 (settings.secrets.public_base_url) — 붙여 넣기 글의 사진 주소
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
                    parsed = parse_export(json.loads(self.path.read_text(encoding="utf-8")), self.tz, self.media_base)
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
