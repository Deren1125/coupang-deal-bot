"""대시보드에서 주고받는 값들: 소스가 읽은 결과(FetchResult) → 카드 화면(CardView).

카드 상태 칩(조건 충족 / 조건 미충족 / 판단 불가)은 목표 줄(GoalRow)에서 정한다.
- 값을 아는 줄 중 하나라도 목표에 못 미치면 '조건 미충족'
- 그렇지 않은데 모르는 줄이 있으면 '판단 불가'
- 모든 줄이 목표를 채웠으면 '조건 충족'
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

# ---- 읽기 결과 상태
OK = "ok"
PARTIAL = "partial"  # 일부만 읽음 (예: 팔로워는 읽고 조회수는 권한이 없어 못 읽음)
PERMISSION = "permission"  # 권한·토큰 문제로 못 읽음
UNAVAILABLE = "unavailable"  # 연결 안 됨·키 없음·기록 파일 없음
STALE = "stale"  # 기록이 너무 오래됨
ERROR = "error"  # 네트워크 오류 등
SKIPPED = "skipped"  # 한도·간격 때문에 이번엔 안 읽음 (지난 값 유지)
GOOD = (OK, PARTIAL)

# ---- 카드 상태 칩
MET = "met"
UNMET = "unmet"
UNKNOWN = "unknown"
NO_CHIP = ""  # 목표 줄이 없는 카드 (칩 없음)
CHIP_LABELS = {MET: "조건 충족", UNMET: "조건 미충족", UNKNOWN: "판단 불가"}

KINDS = ("api", "export", "manual", "internal")


@dataclass(slots=True)
class FetchResult:
    """소스 하나를 읽은 결과. values 는 오늘(app.timezone) 값, days 는 날짜별 값 {"2026-10-13": {"clicks": 3}}."""

    status: str = OK
    values: dict[str, float] = field(default_factory=dict)
    days: dict[str, dict[str, float]] = field(default_factory=dict)
    note: str | None = None  # 카드 아래 한 줄 안내 (권한 필요 등). 토큰·비밀값은 넣지 않는다
    read_at: datetime | None = None  # 값이 만들어진 시각 (Blog-Auto 기록 파일의 written_at 등). 없으면 읽은 시각
    detail: Any = None  # 숫자가 아닌 화면용 값 (발행 대기 글 제목·링크 기다리는 딜 목록). metrics 표에는 저장하지 않는다


@dataclass(slots=True)
class ManualField:
    """카드에서 주인이 직접 넣는 값. day_offset=-1 이면 '어제 값' (입력한 날짜의 전날로 저장)."""

    metric: str
    label: str
    unit: str = ""
    day_offset: int = 0
    max_value: float = 1_000_000_000


@dataclass(slots=True)
class Stat:
    """카드 위쪽 숫자 칸."""

    label: str
    value: float | None
    unit: str = ""
    delta: float | None = None  # 증감 (+3)
    delta_label: str = ""  # "오늘", "10. 12. 대비" 등
    missing: str = "-"  # 값이 없을 때 보일 말 ("권한 필요" 등)
    sub: str = ""  # 작은 보조 줄 ("최근 7일 35")
    money: bool = False
    stale: bool = False  # 오래된 값 (회색으로 — 블로그 자기 통계를 36시간 넘게 못 읽었을 때)


@dataclass(slots=True)
class CardLink:
    """카드 안 바깥 링크 (주인이 자기 브라우저에서 눌러 여는 것 — 서버는 이 주소를 읽지 않는다). https 주소만."""

    label: str
    url: str
    primary: bool = False  # True 면 버튼 모양, 아니면 옆의 작은 글 링크


@dataclass(slots=True)
class ListEntry:
    """카드 안 목록의 한 줄 (발행 대기 글 제목 · 링크 기다리는 딜).
    url 은 '상품 열기 ↗' (https 만, 주인 브라우저가 새 탭으로 여는 것), queue_id 가 있으면 그 줄에 '링크 넣기' 폼."""

    title: str
    pill: str = ""  # 오른쪽 작은 알약 (점수)
    hot: bool = False  # 알약 강조 (인기 예상)
    sub: str = ""  # 회색 작은 줄 (몰 · 가격 · 기다린 시간)
    url: str | None = None
    url_label: str = ""
    queue_id: int | None = None


@dataclass(slots=True)
class ListGroup:
    """카드 안 목록 묶음 (블로그 하나): 이름 · 요약('대기 4개 · 인기 예상 1개' / '대기 글 없음' / '판단 불가') · 줄들."""

    title: str = ""
    summary: str = ""
    entries: list[ListEntry] = field(default_factory=list)
    unknown: bool = False  # 요약을 '판단 불가' 회색 칩으로


@dataclass(slots=True)
class GoalRow:
    """목표 줄: '구독자 수  7 / 1,000 명' + 얇은 막대."""

    label: str
    value: float | None
    goal: float
    unit: str = ""
    lower_is_better: bool = False  # True 면 goal 이하일 때 충족 (예: 링크 기다리는 딜 3개 이하)

    @property
    def met(self) -> bool | None:
        if self.value is None:
            return None
        return self.value <= self.goal if self.lower_is_better else self.value >= self.goal

    @property
    def ratio(self) -> float:
        """막대 길이 (0~1). 적을수록 좋은 줄은 늘 꽉 찬 막대 — 충족이면 초록, 넘치면 주황 (render._row)."""
        if self.value is None:
            return 0.0
        if self.lower_is_better:
            return 1.0
        if self.goal <= 0:
            return 1.0
        return max(0.0, min(1.0, self.value / self.goal))


@dataclass(slots=True)
class PostCount:
    """통계 탭 카드의 '올린 글' 한 줄 (주인 10/25 '통계탭에서, 각 블로그별 올린 글 수, 각 채널별 올린글수 도' ·
    '그냥 각 sns 탭에 추가만 해줘'): 오늘 · 최근 7일 · 최근 30일 (app.timezone 날짜). why 가 있으면 숫자 대신 '— why'
    (믿을 만한 기록이 없을 때 — 짐작해서 숫자를 만들지 않는다). sub 는 숫자 밑 작은 글 (어디서 셌는지 나눠 보기)."""

    today: int = 0
    d7: int = 0
    d30: int = 0
    why: str = ""
    sub: str = ""


POST_DAYS = 30


def post_count(days: Any, today: date, *, sub: str = "") -> PostCount:
    """날마다 센 수 {date|'YYYY-MM-DD': n} (또는 날짜 목록 — 하나에 1) → 오늘·7일·30일. 앞날·이상한 칸은 셈에서 뺀다."""
    pairs: list[tuple[Any, Any]] = list(days.items()) if isinstance(days, dict) else [(d, 1) for d in days or ()]
    out = PostCount(sub=sub)
    for d, n in pairs:
        try:
            day = d if isinstance(d, date) else date.fromisoformat(str(d))
        except ValueError:
            continue
        if isinstance(day, datetime):
            day = day.date()
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            continue
        age = (today - day).days
        if 0 <= age < POST_DAYS:
            out.d30 += n
            out.d7 += n if age < 7 else 0
            out.today += n if age == 0 else 0
    return out


@dataclass(slots=True)
class CardView:
    key: str
    title: str
    type: str
    kind: str
    icon: str  # 인라인 SVG (코드에 박아 둔 것만 — 설정·외부 값은 넣지 않음)
    stats: list[Stat] = field(default_factory=list)
    spark: list[tuple[str, float]] = field(default_factory=list)
    spark_label: str = ""
    program: str | None = None
    rows: list[GoalRow] = field(default_factory=list)
    activity: list[str] = field(default_factory=list)
    note: str = ""
    hints: list[str] = field(default_factory=list)
    read_at: datetime | None = None
    read_label: str = "읽은 시각"
    refreshable: bool = False
    manual: list[ManualField] = field(default_factory=list)
    manual_values: dict[str, float | None] = field(default_factory=dict)  # 지표 → 마지막으로 넣은 값
    manual_days: dict[str, str | None] = field(default_factory=dict)  # 지표 → 그 값의 날짜
    fetch_status: str | None = None
    summary: dict[str, float] = field(default_factory=dict)  # 맨 위 요약 줄에 더할 값
    links: list[CardLink] = field(default_factory=list)  # 숫자 칸 바로 밑 링크 (네이버 통계 열기 등)
    stats_note: str = ""  # 숫자 칸 밑 회색 작은 안내 (숫자가 어디서 왔는지 등)
    stats_warn: str = ""  # 그 안내 뒤에 붙는 주황 글 ('어제 못 읽음' 등)
    groups: list[ListGroup] = field(default_factory=list)  # 그래프 밑 목록 (발행 대기 글 · 링크 기다리는 딜)
    empty_text: str = ""  # 목록 카드인데 줄이 하나도 없을 때 한 줄
    detail: dict[str, Any] = field(default_factory=dict)  # data.json 에 그대로 나갈 목록 값 (폼 토큰은 넣지 않음)
    posted: PostCount | None = None  # 통계 탭 '올린 글 오늘 · 7일 · 30일' 한 줄 (없으면 줄 없음 — 쿠팡처럼 글을 안 올리는 카드)

    @property
    def status(self) -> str:
        return chip_status(self.rows, self.fetch_status)


def chip_status(rows: list[GoalRow], fetch_status: str | None = None) -> str:
    if not rows:
        return NO_CHIP if fetch_status in GOOD else UNKNOWN
    if any(r.met is False for r in rows):
        return UNMET
    if any(r.met is None for r in rows):
        return UNKNOWN
    return MET


# ---------------------------------------------------------------- 숫자·날짜 글자
def fmt_num(v: float | None, *, missing: str = "-") -> str:
    """1234 → '1,234', 12.5 → '12.5' (소수는 한 자리). 무한·NaN 은 값이 없는 것처럼."""
    if v is None or not math.isfinite(v):
        return missing
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    return f"{v:,.1f}"


def fmt_signed(v: float) -> str:
    """+3 / -2 / 0."""
    if not math.isfinite(v):
        return "-"
    if v < 0:
        return "-" + fmt_num(-v)
    return ("+" if v > 0 else "") + fmt_num(v)


def fmt_day(day: str | date) -> str:
    """'2026-10-13' → '2026. 10. 13.' (참고 화면 표기)."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return f"{d.year}. {d.month}. {d.day}."


def fmt_short_day(day: str | date) -> str:
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return f"{d.month}. {d.day}."


def fmt_time(dt: datetime | None) -> str:
    """'10. 14. 18:00' (이미 현지 시각으로 바꾼 값)."""
    if dt is None:
        return "-"
    return f"{dt.month}. {dt.day}. {dt:%H:%M}"


# ---------------------------------------------------------------- 문장
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


def _last_char(word: str) -> str:
    """조사를 고를 글자. 끝의 괄호 덧말은 빼고 본다 — '이번 달 수익 (어제까지)' → '익' (괄호 앞 낱말에 붙는 조사)."""
    base = word.rstrip()
    while (stripped := _TRAILING_PAREN.sub("", base)) != base and stripped:
        base = stripped
    return base[-1:]


def _has_batchim(word: str) -> bool | None:
    """마지막 글자에 받침이 있는지 (한글이 아니면 None)."""
    ch = _last_char(word)
    if not ch or not ("가" <= ch <= "힣"):
        return None
    return (ord(ch) - 0xAC00) % 28 != 0


def josa(word: str, with_batchim: str, without: str) -> str:
    has = _has_batchim(word)
    if has is None:
        return f"{with_batchim}({without})"
    return with_batchim if has else without


def josa_ro(word: str) -> str:
    """으로/로 (받침 ㄹ 은 '로')."""
    ch = _last_char(word)
    if ch and "가" <= ch <= "힣":
        jong = (ord(ch) - 0xAC00) % 28
        return "로" if jong in (0, 8) else "으로"
    return "(으)로"


def _amount(v: float | None, unit: str) -> str:
    return f"{fmt_num(v)}{unit}" if unit else fmt_num(v)


def explain(program: str | None, rows: list[GoalRow]) -> str:
    """목표 줄을 한두 문장으로 (참고 화면 아래쪽 회색 문단)."""
    if not rows:
        return ""
    unmet = [r for r in rows if r.met is False]
    unknown = [r for r in rows if r.met is None]
    parts: list[str] = []
    for r in unmet:
        now = _amount(r.value, r.unit)
        if r.lower_is_better:
            parts.append(f"{r.label}{josa(r.label, '이', '가')} {now}{josa_ro(now)} 기준({_amount(r.goal, r.unit)} 이하)보다 많습니다.")
        else:
            gap = _amount(max(0.0, r.goal - (r.value or 0)), r.unit)
            parts.append(f"{r.label}{josa(r.label, '이', '가')} {now}{josa_ro(now)} 목표 {_amount(r.goal, r.unit)}까지 {gap} 남았습니다.")
    if unknown:
        names = "·".join(r.label for r in unknown)
        tail = "판단할 수 없습니다." if len(unknown) == len(rows) else "아직 확인할 수 없습니다."
        if len(unknown) == len(rows):
            goals = ", ".join(f"{r.label} {_amount(r.goal, r.unit)}" for r in rows)
            head = f"{program} 기준({goals})을" if program else f"목표({goals})를"
            parts.append(f"{head} 지금 확인할 수 없어 {tail}")
        else:
            parts.append(f"{names}{josa(names, '은', '는')} {tail}")
    if not unmet and not unknown:
        parts.append("목표를 모두 채웠습니다.")
    return " ".join(parts)


# ---------------------------------------------------------------- 바깥 링크
def https_url(url: Any) -> str | None:
    """카드에 그려도 되는 바깥 주소: https 이고 호스트가 있고, 공백·제어 문자·따옴표·꺾쇠·역슬래시·중괄호·'@'(아이디 끼우기)가
    없는 것만 그대로, 아니면 None (링크를 그리지 않음). 화면에 넣을 때는 따로 이스케이프한다."""
    if not isinstance(url, str) or not url or len(url) > 2000:
        return None
    if any(c.isspace() or not c.isprintable() or c in "\"'<>\\`{}" for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    return url


# ---------------------------------------------------------------- 오류 글자 다듬기
_SECRETISH = [
    re.compile(r"(access_token|client_secret|token|secret|password|signature)=([^&\s]+)", re.I),
    re.compile(r"(?<![0-9])[0-9]{6,}:[A-Za-z0-9_-]{30,}"),  # 텔레그램 봇 토큰 모양 (주소 /bot123:abc… 안에 있어도)
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),  # 긴 토큰 모양 문자열
]


def safe_text(text: Any, limit: int = 160) -> str:
    """오류 글을 화면·DB 에 둘 때: 토큰처럼 보이는 것은 가리고, 한 줄로, 짧게."""
    s = " ".join(str(text or "").split())
    s = _SECRETISH[0].sub(lambda m: f"{m.group(1)}=***", s)
    for rx in _SECRETISH[1:]:
        s = rx.sub("***", s)
    return s[:limit]


def safe_error(e: BaseException) -> str:
    msg = safe_text(e, 120)
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__
