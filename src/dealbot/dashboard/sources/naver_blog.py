"""네이버 블로그 (블로그마다 카드 하나): 이웃 수·어제 방문자·게시글 수는 주인이 직접 입력, 활동은 Blog-Auto 기록 파일.

네이버 블로그 통계·방문자·이웃 목록·글 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다 (blog.stat.naver.com, /NVisitor4Ajax,
/NBuddyList, /BlogInfo, m.blog /api 등). 그래서 이웃 수·방문자·게시글 수는 카드의 작은 입력 칸으로만 들어온다.
Blog-Auto 는 자기 DB 만 보고 var/metrics/blogauto.json 을 한 시간마다 쓴다 (오늘 발행·임시저장·서로이웃 신청·댓글).
애드포스트 '게시글 수' = 주인이 넣은 게시글 수 + 그 뒤로 Blog-Auto 가 더 발행한 수 (넣은 적이 없으면 Blog-Auto 발행 합계 —
봇 없이 손으로 쓴 예전 글은 Blog-Auto 가 모르므로 한 번 넣어 두면 정확해진다).
이웃·방문자를 읽지 못하는 대신 카드에 '네이버 통계 열기 ↗'(그 블로그의 통계 페이지)·'내 블로그 ↗' 링크를 둔다 — 주인이
로그인한 자기 브라우저에서 눌러 여는 링크일 뿐, 서버(봇)는 이 주소를 한 번도 요청하지 않는다 (이 모듈에는 HTTP 클라이언트가 없다).

options:
  blog       Blog-Auto 의 블로그 이름 (finance | issue | hotdeal)
  blog_id    블로그 주소 아이디 (blog.naver.com/<blog_id>, 기록 파일에서 이름 대신 찾을 때도 씀). 영문·숫자·_- 만
  open_date  블로그 개설일 YYYY-MM-DD (애드포스트 '개설 기간'). 모르면 비워 두면 '판단 불가'
  stats_url  '네이버 통계 열기' 주소 틀 ({blog_id} 자리에 blog_id). 기본 STATS_URL, https 주소만
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    OK,
    UNAVAILABLE,
    CardLink,
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
# 주인이 자기 브라우저에서 여는 주소 (서버는 요청하지 않음). 통계 '일간 현황' — 네이버가 주소를 바꾸면 options.stats_url 로
STATS_URL = "https://admin.blog.naver.com/{blog_id}/stat/today"
BLOG_URL = "https://m.blog.naver.com/{blog_id}"
BLOG_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
STATS_NOTE = "이웃·방문자는 네이버가 봇 접근을 막아 자동으로 못 읽어요"


def owner_url(template: Any, blog_id: str) -> str | None:
    """template 의 {blog_id} 자리를 채운 주소. blog_id 가 영문·숫자·_- 가 아니거나, https 주소가 아니거나,
    공백·따옴표·꺾쇠 같은 글자가 섞였으면 None (링크를 그리지 않음). str.format 은 쓰지 않는다 ({blog_id.x} 같은 틀 방지)."""
    if not isinstance(template, str) or not BLOG_ID_RE.fullmatch(blog_id):
        return None
    url = template.replace("{blog_id}", blog_id)
    if any(c.isspace() or not c.isprintable() or c in "\"'<>\\`{}" for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        return None
    return url


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

    def owner_links(self) -> tuple[list[CardLink], list[str]]:
        """'네이버 통계 열기 ↗'(버튼)·'내 블로그 ↗' 링크와, 설정이 이상해 링크를 뺐을 때의 안내 줄.
        주인이 누르면 그 브라우저(네이버에 로그인한 상태)에서 열린다 — 서버는 이 주소를 읽지 않는다."""
        raw = self.options.get("blog_id")
        if raw is None or raw == "":
            return [], []
        blog_id = str(raw) if isinstance(raw, (str, int)) and not isinstance(raw, bool) else ""
        if not BLOG_ID_RE.fullmatch(blog_id):
            return [], ["options.blog_id 는 영문·숫자·_- 만 쓸 수 있어요 (통계 열기 링크를 숨김)"]
        links: list[CardLink] = []
        hints: list[str] = []
        stats = owner_url(self.options.get("stats_url") or STATS_URL, blog_id)
        if stats:
            links.append(CardLink("네이버 통계 열기", stats, primary=True))
        else:
            hints.append("options.stats_url 은 https 주소여야 해요 (통계 열기 링크를 숨김)")
        if blog := owner_url(BLOG_URL, blog_id):
            links.append(CardLink("내 블로그", blog))
        return links, hints

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
        links, link_hints = self.owner_links()
        has_stats = any(link.primary for link in links)
        return self.view(
            data, stats=[neighbors, visitors],
            links=links, hints=link_hints,
            stats_note=STATS_NOTE + (" — 통계 열기로 바로 확인" if has_stats else ""),
            # 방문자는 날마다 오르내려 '7일 평균'(애드포스트 기준과 같은 셈)으로, 이웃 수는 쌓이는 수라 그대로
            spark=data.rolling("visitors", 7, how="avg") if use_visitors else data.series("neighbors"),
            spark_label="방문자 7일 평균" if use_visitors else "이웃 수", rows=rows, activity=activity,
            read_at=read_at, summary=summary,
        )
