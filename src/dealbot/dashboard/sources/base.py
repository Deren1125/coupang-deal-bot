"""대시보드 소스(카드)의 뼈대.

새 SNS 를 붙이는 법 (docs/DASHBOARD.md):
  1) 이 폴더에 모듈 하나: `@register` 를 붙인 Source 하위 클래스 (type·kind·icon, fetch() 또는 live(), card())
  2) config.yaml 의 dashboard.cards 에 한 줄 ({key, type, title, goals, options})
폴더 안의 모듈은 자동으로 읽히므로 다른 곳을 고칠 필요가 없다.

kind:
  api      — 바깥 API 로 읽음. 정해진 시각(collect_times)과 '다시 읽기' 버튼에서만 fetch() (한도 아끼기)
  export   — 다른 프로그램이 쓴 파일 등 로컬 기록. 페이지를 열 때마다 live() 로 다시 읽음
  internal — 봇 DB. 페이지를 열 때마다 live()
  manual   — 주인이 카드에서 직접 입력
api 소스도 live() 로 싼 값(예: 봇 DB 의 채널 글 수)을 함께 낼 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar
from zoneinfo import ZoneInfo

from dealbot.config import DashboardCardConfig, DashboardConfig
from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    SKIPPED,
    CardView,
    FetchResult,
    GoalRow,
    ManualField,
    Stat,
    explain,
    fmt_short_day,
)
from dealbot.utils.timeutil import from_iso


@dataclass
class SourceContext:
    """소스가 쓰는 봇 자원. 테스트는 가짜를 넣는다 (네트워크 없음)."""

    db: Any
    tz: str
    config: DashboardConfig
    now: Callable[[], datetime]
    telegram_bot: Callable[[], Any] = lambda: None  # 지금 쓰는 텔레그램 Bot (없으면 None)
    channel_id: str | None = None
    instagram: Any = None  # InstagramPublisher (토큰 저장·갱신은 게시기가 하던 그대로)
    threads: Any = None  # ThreadsPublisher
    coupang: Any = None  # CoupangClient | None
    budget: Any = None  # 쿠팡 ApiBudget (딥링크 몫은 건드리지 않음)
    include_dry_run: Callable[[], bool] = lambda: False
    blogauto: Any = None  # BlogAutoReader
    shop_label: Callable[[str], str] = lambda key: key  # 몰 키 → 이름 (텔레그램 링크 요청과 같은 이름)
    history_days: int = 30  # 봇 DB 가 딜 기록을 지우는 기간 (app.prune_events_days) — 그보다 오래된 날은 다시 세지 않음
    # 링크·확인을 기다리는 글이 끝나는 시간 (publish.manual_link_ttl_hours) — '지금 할 일' 의 '14시간 남음'. 모르면 남은 시간 없이
    link_ttl_hours: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def local_now(self) -> datetime:
        return self.now().astimezone(ZoneInfo(self.tz))

    def today(self) -> date:
        return self.local_now().date()

    def day_start_utc(self, day: date) -> datetime:
        """그날 0시(app.timezone)를 UTC 로."""
        return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(self.tz)).astimezone(ZoneInfo("UTC"))

    def local_day(self, dt: datetime) -> date:
        return dt.astimezone(ZoneInfo(self.tz)).date()


class CardData:
    """카드를 그릴 때 보는 저장된 값들 (metrics 표). 한 번 읽은 지표는 기억해 둔다."""

    def __init__(self, ctx: SourceContext, key: str, *, status: dict[str, Any] | None = None,
                 live: FetchResult | None = None) -> None:
        self.ctx = ctx
        self.key = key
        self.today = ctx.today()
        self.spark_days = ctx.config.spark_days
        self.window = max(self.spark_days + 7, 62)  # 그래프 기간 + 7일 합계용 앞 6일
        self.status = status or {}
        self.live = live
        self._series: dict[str, list[tuple[str, float]]] = {}

    def _rows(self, metric: str) -> list[tuple[str, float]]:
        if metric not in self._series:
            since = (self.today - timedelta(days=self.window)).isoformat()
            self._series[metric] = self.ctx.db.metric_series(self.key, metric, since, self.today.isoformat())
        return self._series[metric]

    def value(self, metric: str, day: date | str | None = None) -> float | None:
        d = (day or self.today)
        d = d.isoformat() if isinstance(d, date) else d
        return next((v for k, v in self._rows(metric) if k == d), None)

    def latest(self, metric: str) -> tuple[str, float] | None:
        rows = self._rows(metric)
        if rows:
            return rows[-1]
        row = self.ctx.db.metric_latest(self.key, metric, until_day=self.today.isoformat())
        return (row["day"], row["value"]) if row else None

    def latest_value(self, metric: str) -> float | None:
        got = self.latest(metric)
        return got[1] if got else None

    def value_on_or_before(self, metric: str, day: date | str) -> float | None:
        """그날(또는 그 전 가장 가까운 날)의 값."""
        d = day.isoformat() if isinstance(day, date) else day
        row = self.ctx.db.metric_latest(self.key, metric, until_day=d)
        return row["value"] if row else None

    def change(self, metric: str, *, max_gap_days: int = 7) -> tuple[float, date, date] | None:
        """(가장 최근 값 − 그 앞의 값, 최근 날짜, 앞 날짜). 앞 값이 없거나 max_gap_days 보다 먼 날이면 None."""
        rows = self._rows(metric)
        if len(rows) < 2:
            return None
        (d1, v1), (d0, v0) = rows[-1], rows[-2]
        last, prev = date.fromisoformat(d1), date.fromisoformat(d0)
        if (last - prev).days > max_gap_days:
            return None
        return v1 - v0, last, prev

    def delta(self, metric: str, *, max_gap_days: int = 7) -> tuple[float, str] | None:
        """가장 최근 값 − 그 앞의 값 (최대 max_gap_days 전까지). 바로 전날과 비교면 '오늘'/'10. 13.', 아니면 'N. N. 대비'."""
        got = self.change(metric, max_gap_days=max_gap_days)
        if got is None:
            return None
        diff, last, prev = got
        if (last - prev).days == 1:
            label = "오늘" if last == self.today else fmt_short_day(last)
        else:
            label = f"{fmt_short_day(prev)} 대비"
        return diff, label

    def today_delta(self, metric: str) -> float | None:
        """오늘 값 − 어제 값 (둘 다 있을 때만) — 맨 위 '오늘 늘어난' 합계용."""
        t = self.value(metric, self.today)
        y = self.value(metric, self.today - timedelta(days=1))
        return t - y if t is not None and y is not None else None

    def series(self, metric: str) -> list[tuple[str, float]]:
        since = (self.today - timedelta(days=self.spark_days - 1)).isoformat()
        return [(d, v) for d, v in self._rows(metric) if d >= since]

    def window_sum(self, metric: str, days: int, *, end: date | None = None) -> float | None:
        end = end or self.today
        start = (end - timedelta(days=days - 1)).isoformat()
        vals = [v for d, v in self._rows(metric) if start <= d <= end.isoformat()]
        return sum(vals) if vals else None

    def window_avg(self, metric: str, days: int, *, end: date | None = None) -> float | None:
        end = end or self.today
        start = (end - timedelta(days=days - 1)).isoformat()
        vals = [v for d, v in self._rows(metric) if start <= d <= end.isoformat()]
        return sum(vals) / len(vals) if vals else None

    def month_sum(self, metric: str, *, end: date | None = None) -> float | None:
        """이번 달(오늘이 속한 달) 1일부터 end(기본 오늘)까지 합."""
        start = self.today.replace(day=1).isoformat()
        last = (end or self.today).isoformat()
        vals = [v for d, v in self._rows(metric) if start <= d <= last]
        return sum(vals) if vals else None

    def rolling(self, metric: str, days: int = 7, *, how: str = "sum", end: date | None = None) -> list[tuple[str, float]]:
        """그래프용: 그래프 기간의 날마다 '그날까지 days 일' 합계(sum) 또는 평균(avg) — 날마다 오르내리는 값을 부드럽게.
        sum 은 그 days 일이 모두 기록 안에 있을 때만 (기록이 시작된 첫 며칠이 낮게 그려지지 않게), avg 는 있는 값만으로."""
        rows = self._rows(metric)
        if not rows:
            return []
        vals = dict(rows)
        first = date.fromisoformat(rows[0][0])
        end = end or self.today
        out: list[tuple[str, float]] = []
        for i in range(self.spark_days - 1, -1, -1):
            d = end - timedelta(days=i)
            win = [vals[k] for k in ((d - timedelta(days=j)).isoformat() for j in range(days)) if k in vals]
            if not win or (how == "sum" and d - timedelta(days=days - 1) < first):
                continue
            out.append((d.isoformat(), sum(win) if how == "sum" else sum(win) / len(win)))
        return out

    # ---- 읽기 상태
    @property
    def fetch_status(self) -> str | None:
        if self.live is not None and not self.status:
            return self.live.status
        return self.status.get("status")

    @property
    def read_at(self) -> datetime | None:
        return from_iso(self.status.get("read_at"))

    @property
    def note(self) -> str | None:
        return self.status.get("note")


SOURCE_TYPES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """`@register` — config 의 type 이름으로 찾을 수 있게 등록."""
    if not cls.type:
        raise ValueError(f"{cls.__name__}: type 이 비어 있음")
    SOURCE_TYPES[cls.type] = cls
    return cls


class Source:
    type: ClassVar[str] = ""
    kind: ClassVar[str] = "api"
    default_title: ClassVar[str] = ""
    icon: ClassVar[str] = icons.GENERIC
    program: ClassVar[str | None] = None  # 목표 줄 묶음의 제목 (예: '네이버 애드포스트')
    manual_fields: ClassVar[tuple[ManualField, ...]] = ()
    default_goals: ClassVar[dict[str, float]] = {}

    def __init__(self, cfg: DashboardCardConfig, ctx: SourceContext) -> None:
        self.cfg = cfg
        self.ctx = ctx

    @property
    def key(self) -> str:
        return self.cfg.key

    @property
    def title(self) -> str:
        return self.cfg.title or self.default_title or self.cfg.key

    @property
    def options(self) -> dict[str, Any]:
        return self.cfg.options

    def goal(self, name: str) -> float | None:
        v = self.cfg.goals.get(name, self.default_goals.get(name))
        return float(v) if v is not None else None

    @property
    def refreshable(self) -> bool:
        return self.kind in ("api", "export")

    @property
    def has_live(self) -> bool:
        return type(self).live is not Source.live

    async def fetch(self) -> FetchResult:
        """바깥에서 읽기 (api 소스). 예외를 던지지 말고 FetchResult 로 상태를 돌려준다."""
        return FetchResult(SKIPPED)

    def live(self) -> FetchResult | None:
        """페이지를 열 때마다 읽는 싼 값 (DB·로컬 파일)."""
        return None

    def card(self, data: CardData) -> CardView:
        raise NotImplementedError

    # ---- 카드 만들 때 쓰는 도우미
    def view(self, data: CardData, **kw: Any) -> CardView:
        """공통 칸을 채운 CardView. 목표 줄 설명·안내 줄·읽은 시각을 기본으로 넣는다."""
        rows: list[GoalRow] = kw.pop("rows", [])
        hints: list[str] = kw.pop("hints", [])
        note = data.note
        if data.live is not None and data.live.note:
            hints = [data.live.note, *hints]
        if note and note not in hints:
            hints = [*hints, note]
        v = CardView(
            key=self.key,
            title=self.title,
            type=self.type,
            kind=self.kind,
            icon=self.icon,
            program=kw.pop("program", self.program),
            rows=rows,
            hints=[h for h in hints if h],
            read_at=kw.pop("read_at", data.read_at),
            refreshable=self.refreshable,
            manual=list(self.manual_fields),
            fetch_status=data.fetch_status,
            **kw,
        )
        if not v.note:
            v.note = explain(v.program, rows)
        for f in self.manual_fields:
            last = data.latest(f.metric)
            v.manual_values[f.metric] = last[1] if last else None
            v.manual_days[f.metric] = last[0] if last else None
        return v

    @staticmethod
    def stat_with_delta(data: CardData, metric: str, label: str, unit: str = "", **kw: Any) -> Stat:
        d = data.delta(metric)
        return Stat(label, data.latest_value(metric), unit, delta=d[0] if d else None, delta_label=d[1] if d else "", **kw)
