"""네이버 블로그 (블로그마다 카드 하나): 이웃 수·어제 방문자·게시글 수는 주인이 직접 입력, 활동은 Blog-Auto 기록 파일.

네이버 블로그 통계·방문자·이웃 목록·글 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다 (blog.stat.naver.com, /NVisitor4Ajax,
/NBuddyList, /BlogInfo, m.blog /api 등). 그래서 이웃 수·방문자·게시글 수는 카드의 작은 입력 칸으로만 들어온다.
Blog-Auto 는 자기 DB 만 보고 var/metrics/blogauto.json 을 한 시간마다 쓴다 (오늘 발행·임시저장·서로이웃 신청·댓글).
애드포스트 '게시글 수' = 주인이 넣은 게시글 수 + 그 뒤로 Blog-Auto 가 더 발행한 수 (넣은 적이 없으면 Blog-Auto 발행 합계 —
봇 없이 손으로 쓴 예전 글은 Blog-Auto 가 모르므로 한 번 넣어 두면 정확해진다).

options:
  blog       Blog-Auto 의 블로그 이름 (finance | issue | hotdeal)
  blog_id    블로그 주소 아이디 (blog.naver.com/<blog_id>, 기록 파일에서 이름 대신 찾을 때도 씀)
  open_date  블로그 개설일 YYYY-MM-DD (애드포스트 '개설 기간'). 모르면 비워 두면 '판단 불가'
"""

from __future__ import annotations

from datetime import date, timedelta

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    OK,
    UNAVAILABLE,
    CardView,
    FetchResult,
    GoalRow,
    ManualField,
    Stat,
    fmt_num,
    fmt_short_day,
)
from dealbot.dashboard.sources.base import CardData, Source, register

NBSP = "\u00a0"
AUTO_METRICS = ("posts_today", "posts_total", "drafts_today", "neighbor_req_today", "neighbor_req_7d", "comments_today", "replies_today")


@register
class NaverBlogSource(Source):
    type = "naver_blog"
    kind = "export"
    default_title = "네이버 블로그"
    icon = icons.NAVER_BLOG
    program = "네이버 애드포스트"
    manual_fields = (
        ManualField("neighbors", "이웃 수", "명"),
        ManualField("visitors", "어제 방문자", "명", day_offset=-1),
        ManualField("posts", "게시글 수", "개"),  # 블로그에 보이는 전체 글 수 (손으로 쓴 예전 글까지)
    )
    default_goals = {"open_days": 90, "posts": 50, "avg_visitors": 100}

    def open_date(self) -> date | None:
        raw = self.options.get("open_date")
        if isinstance(raw, date):
            return raw
        try:
            return date.fromisoformat(str(raw).strip()) if raw else None
        except ValueError:
            return None

    def live(self) -> FetchResult:
        reader = self.ctx.blogauto
        if reader is None:
            return FetchResult(UNAVAILABLE, note="Blog-Auto 활동 기록 경로가 설정되지 않음")
        export = reader.read()
        if not export.ok:
            return FetchResult(export.status, note=export.note, read_at=export.written_at)
        blog = export.blog(self.options.get("blog"), self.options.get("blog_id"), self.key)
        if blog is None:
            return FetchResult(UNAVAILABLE, note="Blog-Auto 활동 기록에 이 블로그가 없음 (options.blog 확인)", read_at=export.written_at)
        assert export.written_at is not None
        day = self.ctx.local_day(export.written_at).isoformat()
        values = {k: v for k, v in blog.items() if k in AUTO_METRICS}
        return FetchResult(OK, days={day: values}, read_at=export.written_at)

    def posts_count(self, data: CardData, auto_total: float | None) -> float | None:
        """애드포스트 '게시글 수': 주인이 넣은 수 + 그날 뒤로 Blog-Auto 가 더 발행한 수 (Blog-Auto 합계보다 작지 않게)."""
        manual = data.latest("posts")
        if manual is None:
            return auto_total
        day, value = manual
        if auto_total is None:
            return value
        base = data.value_on_or_before("posts_total", day)  # 넣은 날 Blog-Auto 가 센 발행 합계
        grown = max(0.0, auto_total - base) if base is not None else 0.0
        return max(value + grown, auto_total)

    def card(self, data: CardData) -> CardView:
        live = data.live
        fresh = live is not None and live.status == OK and bool(live.days)
        auto_day, auto = next(iter(live.days.items())) if fresh and live is not None else (None, {})
        today = data.today.isoformat()
        neighbors = self.stat_with_delta(data, "neighbors", "이웃 수", "명")
        visit = data.latest("visitors")
        # 날짜는 짧게, 칸이 좁아도 날짜 가운데서 줄이 바뀌지 않게
        visitors = Stat(f"방문자 · {fmt_short_day(visit[0]).replace(' ', NBSP)}" if visit else "어제 방문자",
                        visit[1] if visit else None, "명")
        rows = []
        opened = self.open_date()
        if (g := self.goal("open_days")) is not None:
            rows.append(GoalRow("개설 기간", float((data.today - opened).days) if opened else None, g, "일"))
        if (g := self.goal("posts")) is not None:  # 발행 합계는 날짜와 상관없는 수라 자정 직후의 기록도 씀
            rows.append(GoalRow("게시글 수", self.posts_count(data, auto.get("posts_total")), g, "개"))
        if (g := self.goal("avg_visitors")) is not None:
            avg = data.window_avg("visitors", 7, end=data.today - timedelta(days=1))
            rows.append(GoalRow("일평균 방문자", round(avg, 1) if avg is not None else None, g, "명"))
        activity = []
        if auto:
            # 자정 직후(Blog-Auto 가 다음 기록을 쓰기 전)에는 어제 기록이라 '오늘' 이라 하지 않는다
            when = ("오늘" if auto_day == today else
                    "어제" if auto_day == (data.today - timedelta(days=1)).isoformat() else fmt_short_day(str(auto_day)))
            bits = [f"{when} 발행 {fmt_num(auto.get('posts_today'))}", f"임시저장 {fmt_num(auto.get('drafts_today'))}"]
            req = f"서로이웃 신청 {fmt_num(auto.get('neighbor_req_today'))}"
            if auto.get("neighbor_req_7d") is not None:
                req += f" (7일 {fmt_num(auto.get('neighbor_req_7d'))})"
            bits += [req, f"댓글 {fmt_num(auto.get('comments_today'))}", f"답글 {fmt_num(auto.get('replies_today'))}"]
            activity.append(" · ".join(bits))
        use_visitors = len(data.series("visitors")) >= 2 or not data.series("neighbors")
        read_at = live.read_at if live and live.read_at else None
        manual_at = self.ctx.db.metric_last_read(self.key, origin="manual")
        if manual_at and (read_at is None or manual_at > read_at):
            read_at = manual_at
        summary: dict[str, float] = {}
        if (d := data.today_delta("neighbors")) is not None:
            summary["audience_delta"] = d
        if auto_day == today and auto.get("posts_today") is not None:
            summary["posts_today"] = auto["posts_today"]
        if visit and visit[0] == (data.today - timedelta(days=1)).isoformat():
            summary["visitors_yesterday"] = visit[1]
        return self.view(
            data, stats=[neighbors, visitors],
            # 방문자는 날마다 오르내려 '7일 평균'(애드포스트 기준과 같은 셈)으로, 이웃 수는 쌓이는 수라 그대로
            spark=data.rolling("visitors", 7, how="avg") if use_visitors else data.series("neighbors"),
            spark_label="방문자 7일 평균" if use_visitors else "이웃 수", rows=rows, activity=activity,
            read_at=read_at, summary=summary,
        )
