"""인스타그램 @oneul_hot_deal: 팔로워·게시물 수(공식 Instagram Graph API) + 조회수·도달(인사이트 권한이 있을 때만).

토큰은 게시기가 저장·갱신하는 그대로 쓴다 (/instatoken 으로 넣은 것, 만료 전 자동 갱신). instagram.com 페이지는 읽지 않는다.
인사이트 권한이 없다고 한 번 안 토큰으로는 조회수를 다시 부르지 않는다 (publisher.scopes, 주인 결정 10/15).
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
from dealbot.dashboard.sources._meta import (
    finite_number,
    insight_value,
    is_permission,
    is_token_problem,
)
from dealbot.dashboard.sources.base import CardData, Source, register
from dealbot.publisher.scopes import IG_INSIGHTS

INSIGHT_DAYS = 28
NEED_INSIGHTS = ("조회수는 인사이트 권한(instagram_business_manage_insights)이 있는 토큰이어야 읽혀요 — "
                 "메타 앱에 권한을 더한 뒤 /instaauth (또는 /instatoken) 로 다시 연결하면 보입니다")


@register
class InstagramSource(Source):
    type = "instagram"
    kind = "api"
    default_title = "인스타그램"
    icon = icons.INSTAGRAM
    program = "인스타그램 목표"
    default_goals = {"followers": 1000, "posts": 100}

    async def fetch(self) -> FetchResult:
        pub = self.ctx.instagram
        if pub is None or pub.stored_token() is None:
            return FetchResult(UNAVAILABLE, note="인스타그램이 연결되지 않음 (/instatoken)")
        try:
            token = await pub.ensure_fresh()
            if token is None:
                return FetchResult(UNAVAILABLE, note="인스타그램이 연결되지 않음 (/instatoken)")
            acc = await pub.client.account_stats(token)
        except Exception as e:  # noqa: BLE001
            if is_token_problem(e):
                return FetchResult(PERMISSION, note="인스타그램 토큰이 만료됐거나 잘못됨 — /instatoken 으로 다시 연결해 주세요")
            if is_permission(e):
                return FetchResult(PERMISSION, note="권한 필요: 팔로워 수를 읽을 권한이 없음 (instagram_business_basic)")
            return FetchResult(ERROR, note=f"인스타그램을 못 읽음 ({safe_error(e)})")
        values: dict[str, float] = {}
        for field, metric in (("followers_count", "followers"), ("media_count", "posts")):
            if (v := finite_number(acc.get(field))) is not None:
                values[metric] = v
        now = self.ctx.now()
        scopes = getattr(pub, "scopes", None)
        if scopes is not None and not scopes.usable(IG_INSIGHTS):
            return FetchResult(PARTIAL, values, note=NEED_INSIGHTS)  # 이 토큰엔 권한이 없다고 이미 앎
        try:
            body = await pub.client.account_insights(
                token, "views,reach", since=int((now - timedelta(days=INSIGHT_DAYS)).timestamp()), until=int(now.timestamp()))
        except Exception as e:  # noqa: BLE001
            if is_permission(e) and not is_token_problem(e) and scopes is not None:
                scopes.mark(IG_INSIGHTS, False)
            if is_permission(e) or is_token_problem(e):
                return FetchResult(PARTIAL, values, note=NEED_INSIGHTS)
            return FetchResult(PARTIAL, values, note=f"조회수를 못 읽음 ({safe_error(e)})")
        if scopes is not None:
            scopes.mark(IG_INSIGHTS, True)
        for name in ("views", "reach"):
            v = insight_value(body, name)
            if v is not None:
                values[f"{name}_{INSIGHT_DAYS}d"] = v
        return FetchResult(OK, values)

    def card(self, data: CardData) -> CardView:
        followers = self.stat_with_delta(data, "followers", "팔로워", "명")
        views = data.latest_value(f"views_{INSIGHT_DAYS}d")
        missing = "권한 필요" if (data.note or "").startswith("조회수는 인사이트 권한") else "-"
        reach = data.latest_value(f"reach_{INSIGHT_DAYS}d")
        stat_views = Stat(f"조회수 · 최근 {INSIGHT_DAYS}일", views, "회", missing=missing,
                          sub=f"도달 {int(reach):,}명" if reach is not None else "")
        rows = []
        if (g := self.goal("followers")) is not None:
            rows.append(GoalRow("팔로워 수", followers.value, g, "명"))
        if (g := self.goal("posts")) is not None:
            rows.append(GoalRow("게시물 수", data.latest_value("posts"), g, "개"))
        spark_metric = f"views_{INSIGHT_DAYS}d" if len(data.series(f"views_{INSIGHT_DAYS}d")) >= 2 else "followers"
        return self.view(
            data, stats=[followers, stat_views], spark=data.series(spark_metric),
            spark_label="조회수" if spark_metric != "followers" else "팔로워", rows=rows,
            summary={"audience_delta": d} if (d := data.today_delta("followers")) is not None else {},
        )
