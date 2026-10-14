"""핫딜 봇 운영: 올린 딜(오늘·7일), 내 링크를 기다리는 딜, 대기열 상태 — 봇 DB 만 본다."""

from __future__ import annotations

from datetime import timedelta

from dealbot.dashboard import icons
from dealbot.dashboard.model import OK, CardView, FetchResult, GoalRow, Stat, fmt_num
from dealbot.dashboard.sources.base import CardData, Source, register

QUEUE_LABELS = (
    ("pending", "발행 대기"),
    ("awaiting_link", "내 링크 기다림"),
    ("awaiting_approval", "확인(/ok) 기다림"),
    ("awaiting_autolink", "자동 링크 중"),
)


@register
class DealBotSource(Source):
    type = "dealbot"
    kind = "internal"
    default_title = "핫딜 봇 운영"
    icon = icons.DEALBOT
    program = "운영 목표"
    default_goals = {"deals_per_day": 10, "links_waiting_max": 3}

    def live(self) -> FetchResult:
        today = self.ctx.today()
        # 그래프(spark_days)와 7일 합계에 쓰는 날만 다시 센다. 단 봇 DB 가 딜 기록을 지운 날(history_days 보다 오래)은
        # 세면 0 이 되므로 다시 쓰지 않고, 그때 저장해 둔 값을 그대로 둔다
        n_days = max(1, min(self.ctx.config.spark_days + 6, self.ctx.history_days - 1))
        since = self.ctx.day_start_utc(today - timedelta(days=n_days - 1))
        counts: dict[str, float] = {(today - timedelta(days=i)).isoformat(): 0.0 for i in range(n_days)}
        for t in self.ctx.db.published_deal_times(since, include_dry_run=self.ctx.include_dry_run()):
            d = self.ctx.local_day(t).isoformat()
            if d in counts:
                counts[d] += 1
        queue = self.ctx.db.queue_counts()
        values = {f"queue_{k}": float(queue.get(k, 0)) for k, _ in QUEUE_LABELS}
        week = sum(v for d, v in counts.items() if d >= (today - timedelta(days=6)).isoformat())
        values.update({"deals_7d": week, "links_waiting": float(queue.get("awaiting_link", 0))})
        days = {d: {"deals": v} for d, v in counts.items()}
        days.setdefault(today.isoformat(), {}).update(values)
        return FetchResult(OK, days=days, read_at=self.ctx.now())

    def card(self, data: CardData) -> CardView:
        live = data.live.days.get(data.today.isoformat(), {}) if data.live else {}
        deals_today = live.get("deals", data.value("deals"))
        y = data.value("deals", data.today - timedelta(days=1))
        stats = [
            Stat("올린 딜 · 오늘", deals_today, "개", delta=(deals_today - y) if deals_today is not None and y is not None else None,
                 delta_label="어제보다", sub=f"최근 7일 {fmt_num(live.get('deals_7d'))}개"),
            Stat("내 링크 기다림", live.get("links_waiting"), "개"),
        ]
        rows = []
        if (g := self.goal("deals_per_day")) is not None:
            rows.append(GoalRow("오늘 올린 딜", deals_today, g, "개"))
        if (g := self.goal("links_waiting_max")) is not None:
            rows.append(GoalRow("링크 기다리는 딜", live.get("links_waiting"), g, "개", lower_is_better=True))
        queue = " · ".join(f"{label} {fmt_num(live.get(f'queue_{k}'))}" for k, label in QUEUE_LABELS)
        return self.view(  # 하루 딜 수는 날마다 오르내려 '최근 7일 합계' 로 부드럽게 (숫자 칸의 '최근 7일' 과 같은 셈)
            data, stats=stats, spark=data.rolling("deals", 7), spark_label="올린 딜 7일 합계", rows=rows,
            activity=[f"대기열: {queue}"] if live else [], read_at=data.live.read_at if data.live else None,
            read_label="읽은 시각", summary={"posts_today": deals_today} if deals_today is not None else {},
        )
