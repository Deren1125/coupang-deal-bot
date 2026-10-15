"""텔레그램 채널: 구독자 수(공식 Bot API getChatMemberCount) + 채널에 올린 글 수(봇 DB)
+ 초대 링크별 새 구독 (/invitelinks 로 만든 이름 붙은 링크, chat_member 업데이트로 셈 — 있을 때만 작은 칸 하나 더, 주인 결정 10/15)."""

from __future__ import annotations

from datetime import timedelta

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    ERROR,
    OK,
    UNAVAILABLE,
    CardView,
    FetchResult,
    GoalRow,
    Stat,
    fmt_num,
    safe_error,
)
from dealbot.dashboard.sources.base import CardData, Source, register
from dealbot.growth.invites import joins_summary


@register
class TelegramSource(Source):
    type = "telegram"
    kind = "api"
    default_title = "텔레그램 채널"
    icon = icons.TELEGRAM
    program = "채널 목표"
    default_goals = {"subscribers": 1000}

    async def fetch(self) -> FetchResult:
        bot = self.ctx.telegram_bot()
        if bot is None:
            return FetchResult(UNAVAILABLE, note="텔레그램 봇이 연결되지 않아 구독자 수를 못 읽음")
        if not self.ctx.channel_id:
            return FetchResult(UNAVAILABLE, note="TELEGRAM_CHANNEL_ID 가 없어 구독자 수를 못 읽음")
        try:
            count = await bot.get_chat_member_count(self.ctx.channel_id)
        except Exception as e:  # noqa: BLE001 — 대시보드는 어떤 오류에도 멈추지 않는다
            return FetchResult(ERROR, note=f"구독자 수를 못 읽음 ({safe_error(e)})")
        return FetchResult(OK, values={"subscribers": float(count)})

    def live(self) -> FetchResult:
        """채널에 올린 글 (오늘·최근 7일) — 봇 DB 만 본다."""
        today = self.ctx.today()
        since = self.ctx.day_start_utc(today - timedelta(days=6))
        times = self.ctx.db.channel_post_times(since, include_dry_run=self.ctx.include_dry_run())
        days = [self.ctx.local_day(t) for t in times]
        values = {"posts_today": float(sum(1 for d in days if d == today)), "posts_7d": float(len(days))}
        try:
            joins, _ = joins_summary(self.ctx.db, today)
        except Exception:  # noqa: BLE001 — 가입 경로 표가 없어도 카드는 그대로
            joins = 0
        if joins:
            values["joins_7d"] = float(joins)
        return FetchResult(OK, values=values)

    def card(self, data: CardData) -> CardView:
        subs = self.stat_with_delta(data, "subscribers", "구독자", "명")
        live = data.live.values if data.live else {}
        posts = Stat("채널 글 · 오늘", live.get("posts_today"), "개", sub=f"최근 7일 {fmt_num(live.get('posts_7d'))}개")
        goal = self.goal("subscribers")
        rows = [GoalRow("구독자 수", subs.value, goal, "명")] if goal else []
        stats = [subs, posts]
        if live.get("joins_7d"):  # 초대 링크별 새 구독 (있을 때만 — 예전 카드 모양은 그대로)
            try:
                total, parts = joins_summary(self.ctx.db, self.ctx.today())
            except Exception:  # noqa: BLE001
                total, parts = int(live["joins_7d"]), ""
            stats.append(Stat("새 구독 경로 · 7일", float(total), "명", sub=parts))
        return self.view(
            data, stats=stats, spark=data.series("subscribers"), spark_label="구독자", rows=rows,
            summary={"audience_delta": d} if (d := data.today_delta("subscribers")) is not None else {},
        )
