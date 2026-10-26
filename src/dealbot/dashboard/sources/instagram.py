"""인스타그램 @oneul_hot_deal (+ 데런픽 룩 @derenpick.look — InstagramLookSource): 팔로워·게시물 수(공식 Instagram Graph API) + 조회수·도달(인사이트 권한이 있을 때만).

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
    POST_DAYS,
    UNAVAILABLE,
    CardView,
    FetchResult,
    GoalRow,
    ListEntry,
    ListGroup,
    PostCount,
    Stat,
    post_count,
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

    NOT_LINKED = "인스타그램이 연결되지 않음 (/instatoken)"
    RELINK = "인스타그램 토큰이 만료됐거나 잘못됨 — /instatoken 으로 다시 연결해 주세요"

    def publisher(self):  # type: ignore[no-untyped-def]
        """이 카드가 읽는 계정의 게시기 (토큰 저장·갱신은 게시기 그대로)."""
        return self.ctx.instagram

    async def fetch(self) -> FetchResult:
        pub = self.publisher()
        if pub is None or pub.stored_token() is None:
            return FetchResult(UNAVAILABLE, note=self.NOT_LINKED)
        try:
            token = await pub.ensure_fresh()
            if token is None:
                return FetchResult(UNAVAILABLE, note=self.NOT_LINKED)
            acc = await pub.client.account_stats(token)
        except Exception as e:  # noqa: BLE001
            if is_token_problem(e):
                return FetchResult(PERMISSION, note=self.RELINK)
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

    def counts(self, row: dict) -> bool:
        """이 카드가 세는 글인가 — 딜 계정 카드는 데런픽 룩('look') 글을 빼고 (다른 계정)."""
        return row.get("kind") != "look"

    def posted(self) -> PostCount:
        """올린 글 (주인 10/25): 봇 DB ig_posts — 딜 하나 · TOP5 · 릴스 · 단가표 · 데런픽 글(blog_share, Blog-Auto sns_share 가 준 것도
        딜봇이 올리고 여기에 남김). 올리다 끊겨 '올라갔을 수도' 로 남긴 줄(글 id 없음)과 연습 글은 뺀다. 기록은 30일만 남는다."""
        today = self.ctx.today()
        try:
            rows = self.ctx.db.ig_posts_since(self.ctx.day_start_utc(today - timedelta(days=POST_DAYS - 1)))
        except Exception:  # noqa: BLE001 — 기록을 못 읽어도 카드는 그대로
            return PostCount(why="기록을 못 읽음")
        dry_ok = self.ctx.include_dry_run()
        days = [self.ctx.local_day(r["posted_at"]) for r in rows
                if r.get("posted_at") is not None and r.get("media_id") and self.counts(r)
                and (dry_ok or (not r.get("dry_run") and r["media_id"] != "dry-run"))]
        return post_count(days, today)

    KIND_LABEL = {"single": "딜 하나", "top5": "TOP5", "reel": "릴스", "unit": "단가표", "blog": "데런픽 글", "look": "패션 글"}

    def recent(self) -> list[ListGroup]:
        """최근 2주 글마다 숫자 (주인 10/26 — 2주 시험, growth/ig_insights 가 하루 한 번 읽어 둔 것). 숫자가 없으면 목록 없음."""
        try:
            rows = [r for r in self.ctx.db.ig_recent_insights(self.ctx.day_start_utc(self.ctx.today() - timedelta(days=13)))
                    if self.counts(r)][:10]
        except Exception:  # noqa: BLE001 — 기록을 못 읽어도 카드는 그대로
            return []
        entries = []
        for r in rows:
            v = r["insights"]

            def n(key: str, v: dict = v) -> str:
                x = finite_number(v.get(key))
                return f"{int(x):,}" if x is not None else "-"

            sub = f"저장 {n('saved')} · 공유 {n('shares')} · 좋아요 {n('likes')} · 댓글 {n('comments')}"
            if (w := finite_number(v.get("ig_reels_avg_watch_time"))) is not None:
                sub += f" · 평균 시청 {w / 1000:.1f}초"
            when = self.ctx.local_day(r["posted_at"]) if r.get("posted_at") else None
            label = self.KIND_LABEL.get(str(r.get("kind")), str(r.get("kind") or "글"))
            url = r.get("permalink") if str(r.get("permalink") or "").startswith("https://") else None
            entries.append(ListEntry(f"{label} · {when:%m/%d}" if when else label, pill=f"도달 {n('reach')}", sub=sub,
                                     url=url, url_label="글 열기 ↗" if url else ""))
        return [ListGroup("글마다 숫자 · 최근 2주", f"{len(entries)}개 (하루 지난 글부터)", entries)] if entries else []

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
            spark_label="조회수" if spark_metric != "followers" else "팔로워", rows=rows, posted=self.posted(), groups=self.recent(),
            summary={"audience_delta": d} if (d := data.today_delta("followers")) is not None else {},
        )


@register
class InstagramLookSource(InstagramSource):
    """두 번째 인스타 '데런픽 룩' @derenpick.look (주인 10/26): 같은 읽기(팔로워·게시물 수 + 인사이트 권한이 있으면 28일 조회수·도달),
    토큰만 데런픽 룩 것 (ctx.extra['instagram_look']). 올린 글 수는 ig_posts 의 데런픽 룩 글만."""

    type = "instagram_look"
    default_title = "데런픽 룩 인스타"
    program = "데런픽 룩 목표"
    default_goals = {"followers": 1000}
    NOT_LINKED = "데런픽 룩이 아직 연결되지 않음 — 연결 탭 '데런픽 룩 연결'"
    RELINK = "데런픽 룩 토큰이 만료됐거나 잘못됨 — 연결 탭 '데런픽 룩 인스타' 다시 연결"

    def publisher(self):  # type: ignore[no-untyped-def]
        return self.ctx.extra.get("instagram_look")

    def counts(self, row: dict) -> bool:
        return row.get("kind") == "look"
