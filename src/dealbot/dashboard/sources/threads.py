"""스레드 @oneul_hot_deal: 팔로워·프로필 조회 (공식 Threads API threads_insights — threads_manage_insights 권한이 있을 때만).

사용자 단위 'views' 는 글 조회수가 아니라 프로필 조회수다 (Threads API 문서). 그래서 '프로필 조회' 로 보여 준다 —
스레드 앱 인사이트의 글 조회수보다 훨씬 작다. 글 조회수는 글마다 따로 물어야 해서(호출이 많음) 읽지 않는다.

토큰은 게시기가 저장·갱신하는 그대로 (/threadstoken). 권한이 없으면 '권한 필요' 로 보이고 멈추지 않는다.
권한이 없다고 한 번 안 토큰으로는 다시 부르지 않는다 (publisher.scopes — 다시 연결해 토큰이 바뀌면 그때 다시 불러 봄, 주인 결정 10/15).
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
    POST_DAYS,
    GoalRow,
    PostCount,
    Stat,
    post_count,
    safe_error,
)
from dealbot.dashboard.sources._meta import insight_value, is_permission, is_token_problem
from dealbot.dashboard.sources.base import CardData, Source, register
from dealbot.publisher.scopes import THREADS_INSIGHTS

INSIGHT_DAYS = 28
NEED_INSIGHTS = ("권한 필요: 팔로워·프로필 조회는 인사이트 권한(threads_manage_insights)이 있는 토큰이어야 읽혀요 — "
                 "메타 앱에 권한을 더한 뒤 /threadsauth (또는 /threadstoken) 로 다시 연결해 주세요")


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
        scopes = getattr(pub, "scopes", None)
        if scopes is not None and not scopes.usable(THREADS_INSIGHTS):
            return FetchResult(PERMISSION, note=NEED_INSIGHTS)  # 이 토큰엔 권한이 없다고 이미 앎 — 다시 두드리지 않음
        try:
            token = await pub.ensure_fresh()
            if token is None:
                return FetchResult(UNAVAILABLE, note="스레드가 연결되지 않음 (/threadstoken)")
            followers = await pub.client.user_insights(token, "followers_count")  # 지금 팔로워 (기간 없음)
        except Exception as e:  # noqa: BLE001
            if is_token_problem(e):
                return FetchResult(PERMISSION, note="스레드 토큰이 만료됐거나 잘못됨 — /threadstoken 으로 다시 연결해 주세요")
            if is_permission(e):
                if scopes is not None:
                    scopes.mark(THREADS_INSIGHTS, False)
                return FetchResult(PERMISSION, note=NEED_INSIGHTS)
            return FetchResult(ERROR, note=f"스레드를 못 읽음 ({safe_error(e)})")
        if scopes is not None:
            scopes.mark(THREADS_INSIGHTS, True)
        values: dict[str, float] = {}
        if (f := insight_value(followers, "followers_count")) is not None:
            values["followers"] = f
        try:  # 프로필 조회는 최근 28일 (하루 값의 합)
            views = await pub.client.user_insights(
                token, "views", since=int((now - timedelta(days=INSIGHT_DAYS)).timestamp()), until=int(now.timestamp()))
        except Exception as e:  # noqa: BLE001
            return FetchResult(PARTIAL, values, note=f"프로필 조회를 못 읽음 ({safe_error(e)})")
        if (v := insight_value(views, "views")) is not None:
            values[f"views_{INSIGHT_DAYS}d"] = v
        if not values:
            return FetchResult(PARTIAL, note="스레드 인사이트 응답에 숫자가 없음")
        return FetchResult(OK if len(values) == 2 else PARTIAL, values)

    def posted(self) -> PostCount:
        """올린 글 (주인 10/25): 세 프로그램이 따로 올리고 따로 적는다 — 겹치지 않으니 더한다.
        딜봇 threads_jobs(올림 · 연습 글·글 id 없는 것 빼고) + Blog-Auto 스레드 줄(주인이 쓴 글·데런픽 글) +
        스레드 자동 프로그램(AI 글·예약 글 — Blog-Auto 가 그 기록을 읽어 보냄). 못 읽은 쪽은 작은 글에 '기록 없음'."""
        today = self.ctx.today()
        try:
            jobs = self.ctx.db.threads_posted_since(self.ctx.day_start_utc(today - timedelta(days=POST_DAYS - 1)),
                                                     real_only=not self.ctx.include_dry_run())
            mine: dict[str, int] | None = {}
            for j in jobs:
                if j.posted_at is not None:
                    d = self.ctx.local_day(j.posted_at).isoformat()
                    mine[d] = mine.get(d, 0) + 1
        except Exception:  # noqa: BLE001
            mine = None
        ch = (self.blogauto_post_counts() or {}).get("channels") or {}
        parts = [("딜봇", mine), ("블로그", ch.get("threads")), ("스레드 자동", ch.get("threads_auto"))]
        known = [(name, m) for name, m in parts if m is not None]
        if not known:
            return PostCount(why="기록을 못 읽음")
        total: dict[str, int] = {}
        for _, m in known:
            for d, n in m.items():
                total[d] = total.get(d, 0) + n
        bits = [f"{name} {post_count(m, today).d30}" if m is not None else f"{name} 기록 없음" for name, m in parts]
        return post_count(total, today, sub="30일 · " + " · ".join(bits))

    def card(self, data: CardData) -> CardView:
        missing = "권한 필요" if (data.note or "").startswith("권한 필요") else "-"
        followers = self.stat_with_delta(data, "followers", "팔로워", "명", missing=missing)
        views = Stat(f"프로필 조회 · 최근 {INSIGHT_DAYS}일", data.latest_value(f"views_{INSIGHT_DAYS}d"), "회", missing=missing)
        rows = [GoalRow("팔로워 수", followers.value, g, "명")] if (g := self.goal("followers")) is not None else []
        spark_metric = f"views_{INSIGHT_DAYS}d" if len(data.series(f"views_{INSIGHT_DAYS}d")) >= 2 else "followers"
        return self.view(
            data, stats=[followers, views], spark=data.series(spark_metric),
            spark_label="프로필 조회" if spark_metric != "followers" else "팔로워", rows=rows, posted=self.posted(),
            summary={"audience_delta": d} if (d := data.today_delta("followers")) is not None else {},
        )
