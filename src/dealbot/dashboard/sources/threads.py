"""스레드 @oneul_hot_deal: 팔로워·조회수 (공식 Threads API threads_insights — threads_manage_insights 권한이 있을 때만).

토큰은 게시기가 저장·갱신하는 그대로 (/threadstoken). 권한이 없으면 '권한 필요' 로 보이고 멈추지 않는다.
"""

from __future__ import annotations

from datetime import timedelta

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    ERROR,
    OK,
    PARTIAL,
    PERMISSION,
    UNAVAILABLE,
    CardView,
    FetchResult,
    GoalRow,
    Stat,
    safe_error,
)
from dealbot.dashboard.sources._meta import insight_value, is_permission, is_token_problem
from dealbot.dashboard.sources.base import CardData, Source, register

INSIGHT_DAYS = 28
NEED_INSIGHTS = "권한 필요: 팔로워·조회수는 인사이트 권한(threads_manage_insights)이 있는 토큰이어야 읽혀요 — 권한을 더해 /threadstoken 으로 다시 넣어 주세요"


@register
class ThreadsSource(Source):
    type = "threads"
    kind = "api"
    default_title = "스레드"
    icon = icons.THREADS
    program = "스레드 목표"
    default_goals = {"followers": 1000}

    async def fetch(self) -> FetchResult:
        pub = self.ctx.threads
        if pub is None or pub.stored_token() is None:
            return FetchResult(UNAVAILABLE, note="스레드가 연결되지 않음 (/threadstoken)")
        now = self.ctx.now()
        try:
            token = await pub.ensure_fresh()
            if token is None:
                return FetchResult(UNAVAILABLE, note="스레드가 연결되지 않음 (/threadstoken)")
            followers = await pub.client.user_insights(token, "followers_count")  # 지금 팔로워 (기간 없음)
        except Exception as e:  # noqa: BLE001
            if is_token_problem(e):
                return FetchResult(PERMISSION, note="스레드 토큰이 만료됐거나 잘못됨 — /threadstoken 으로 다시 연결해 주세요")
            if is_permission(e):
                return FetchResult(PERMISSION, note=NEED_INSIGHTS)
            return FetchResult(ERROR, note=f"스레드를 못 읽음 ({safe_error(e)})")
        values: dict[str, float] = {}
        if (f := insight_value(followers, "followers_count")) is not None:
            values["followers"] = f
        try:  # 조회수는 최근 28일 (하루 값의 합)
            views = await pub.client.user_insights(
                token, "views", since=int((now - timedelta(days=INSIGHT_DAYS)).timestamp()), until=int(now.timestamp()))
        except Exception as e:  # noqa: BLE001
            return FetchResult(PARTIAL, values, note=f"조회수를 못 읽음 ({safe_error(e)})")
        if (v := insight_value(views, "views")) is not None:
            values[f"views_{INSIGHT_DAYS}d"] = v
        if not values:
            return FetchResult(PARTIAL, note="스레드 인사이트 응답에 숫자가 없음")
        return FetchResult(OK if len(values) == 2 else PARTIAL, values)

    def card(self, data: CardData) -> CardView:
        missing = "권한 필요" if (data.note or "").startswith("권한 필요") else "-"
        followers = self.stat_with_delta(data, "followers", "팔로워", "명", missing=missing)
        views = Stat(f"조회수 · 최근 {INSIGHT_DAYS}일", data.latest_value(f"views_{INSIGHT_DAYS}d"), "회", missing=missing)
        rows = [GoalRow("팔로워 수", followers.value, g, "명")] if (g := self.goal("followers")) is not None else []
        spark_metric = f"views_{INSIGHT_DAYS}d" if len(data.series(f"views_{INSIGHT_DAYS}d")) >= 2 else "followers"
        return self.view(
            data, stats=[followers, views], spark=data.series(spark_metric),
            spark_label="조회수" if spark_metric != "followers" else "팔로워", rows=rows,
            summary={"audience_delta": d} if (d := data.today_delta("followers")) is not None else {},
        )
