"""카카오 오픈채팅: 참여자 수 (공개 API 가 없어 주인이 카드에서 직접 입력)."""

from __future__ import annotations

from datetime import date, timedelta

from dealbot.dashboard import icons
from dealbot.dashboard.model import CardView, GoalRow, ManualField, PostCount, Stat
from dealbot.dashboard.sources.base import CardData, Source, register


# 올린 글 (주인 10/25): 오픈톡방 전송 API 가 없어 주인이 휴대폰 공유창으로 보낸다 (notify/openchat_watch) — 보낸 기록이 없음
NO_RECORD = "기록 없음 · 카톡 공유창으로 직접 보내서 셀 수 없어요"


@register
class KakaoOpenChatSource(Source):
    type = "kakao_openchat"
    kind = "manual"
    default_title = "카카오 오픈채팅"
    icon = icons.KAKAO
    program = "오픈채팅 목표"
    manual_fields = (ManualField("members", "참여자 수", "명"),)
    default_goals = {"members": 300}

    def card(self, data: CardData) -> CardView:
        members = self.stat_with_delta(data, "members", "참여자", "명")
        week: float | None = None
        last = data.latest("members")
        if last:  # 마지막 입력과 그 7~10일 전 입력의 차이 (매일 넣지 않아도 되게)
            end = date.fromisoformat(last[0])
            before = [v for d, v in data.series("members") if end - timedelta(days=10) <= date.fromisoformat(d) <= end - timedelta(days=7)]
            week = last[1] - before[-1] if before else None
        rows = [GoalRow("참여자 수", members.value, g, "명")] if (g := self.goal("members")) is not None else []
        return self.view(
            data, stats=[members, Stat("참여자 · 7일 증감", week, "명")], spark=data.series("members"), spark_label="참여자",
            rows=rows, read_at=self.ctx.db.metric_last_read(self.key, origin="manual"), read_label="입력한 시각",
            posted=PostCount(why=NO_RECORD),
            summary={"audience_delta": d} if (d := data.today_delta("members")) is not None else {},
        )
