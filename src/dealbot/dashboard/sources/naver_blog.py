"""네이버 블로그 (블로그마다 카드 하나): 자기 통계는 Blog-Auto 가 하루 한 번 읽은 값, 없으면 주인이 직접 입력. 활동도 Blog-Auto 기록 파일.

딜봇(서버)은 네이버에 한 번도 요청하지 않는다 (이 모듈에는 HTTP 클라이언트가 없다). 숫자는 모두 Blog-Auto 기록 파일
(var/metrics/blogauto.json) 이나 주인이 카드에 넣은 값이다.
- 자기 통계 "own_stats" (주인 결정 10/15): Blog-Auto 가 주인 블로그 세 개만, 블로그마다 자기 로그인 브라우저로 하루 한 번
  날마다 다른 시각에 읽는다 (이웃 수·방문자 오늘/전체·조회수·공감·댓글·받은 서로이웃 신청, 전날 하루 조회수·공감·댓글).
  있으면 숫자 칸 네 개(이웃 수 + 증감, 방문자, 조회수 오늘, 조회수 어제)와 '자동 · 10. 15. 14:23 읽음' 한 줄.
  36시간 넘게 못 읽었거나 어제를 통째로 건너뛰었으면 숫자를 회색으로 + '어제 못 읽음'.
- 이웃 수는 직접 입력과 같은 지표(neighbors)에 쌓는다 — 그날 자동으로 읽은 뒤에 주인이 넣은 값이 있으면 주인 값('직접 입력'),
  아니면 자동 값이 이긴다. 증감은 바로 앞에 쌓인 값과 비교 ('+1 어제 대비' / '+3 10. 12. 대비').
- 자기 통계가 없으면(예전 Blog-Auto·아직 한 번도 못 읽음) 예전처럼 이웃 수·어제 방문자는 카드의 작은 입력 칸으로만.
Blog-Auto 활동(오늘 발행·임시저장·서로이웃 신청·댓글)은 자기 DB 만 보고 한 시간마다 쓴다.
애드포스트 '게시글 수' = 주인이 넣은 게시글 수 + 그 뒤로 Blog-Auto 가 더 발행한 수 (넣은 적이 없으면 Blog-Auto 발행 합계 —
봇 없이 손으로 쓴 예전 글은 Blog-Auto 가 모르므로 한 번 넣어 두면 정확해진다).
카드에 '네이버 통계 열기 ↗'(그 블로그의 통계 페이지)·'내 블로그 ↗' 링크를 둔다 — 주인이 로그인한 자기 브라우저에서
눌러 여는 링크일 뿐, 서버(봇)는 이 주소를 한 번도 요청하지 않는다.

options:
  blog       Blog-Auto 의 블로그 이름 (finance | issue | hotdeal)
  blog_id    블로그 주소 아이디 (blog.naver.com/<blog_id>, 기록 파일에서 이름 대신 찾을 때도 씀). 영문·숫자·_- 만
  open_date  블로그 개설일 YYYY-MM-DD (애드포스트 '개설 기간'). 모르면 비워 두면 '판단 불가'
  stats_url  '네이버 통계 열기' 주소 틀 ({blog_id} 자리에 blog_id). 기본 STATS_URL, https 주소만
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import icons
from dealbot.dashboard.blogauto import OwnStats
from dealbot.dashboard.model import (
    OK,
    UNAVAILABLE,
    CardLink,
    CardView,
    FetchResult,
    GoalRow,
    ListEntry,
    ListGroup,
    ManualField,
    PostCount,
    Stat,
    fmt_num,
    fmt_short_day,
    fmt_signed,
    fmt_time,
    https_url,
    post_count,
)
from dealbot.dashboard.sources.base import CardData, Source, register
from dealbot.utils.timeutil import from_iso

NBSP = "\u00a0"
AUTO_METRICS = ("posts_today", "posts_total", "drafts_today", "neighbor_req_today", "neighbor_req_7d", "comments_today", "replies_today")
# 주인이 자기 브라우저에서 여는 주소 (서버는 요청하지 않음). 통계 '일간 현황' — 네이버가 주소를 바꾸면 options.stats_url 로
STATS_URL = "https://admin.blog.naver.com/{blog_id}/stat/today"
BLOG_URL = "https://m.blog.naver.com/{blog_id}"
BLOG_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
STATS_NOTE = "이웃·방문자는 직접 넣은 값이에요"
OWN_STALE_HOURS = 36  # 자기 통계를 이보다 오래 못 읽었으면 숫자를 회색으로 + '어제 못 읽음' (날마다 다른 시각에 읽어 하루 넘게 벌어질 수 있음)
# 그제 이전에 읽은 값이면 36시간이 안 돼도 회색 (어제를 통째로 못 읽음)
# 자기 통계 중 날짜별로 쌓아 두는 값 (이웃 수는 직접 입력과 같은 'neighbors'). 하루 중간 값(오늘 조회수 등)은 쌓지 않고
# 다음 날 읽은 '전날 하루 전체' 값만 그 날짜에
OWN_TOTAL = "own_visitors_total"
OWN_DAY_METRICS = (("own_views", "views"), ("own_likes", "likes"), ("own_comments", "comments"))


@dataclass(slots=True)
class BlogLive:
    """live() 가 card() 에 넘기는 화면용 값 (FetchResult.detail): 활동 기록(그 날짜·지표)과 자기 통계."""

    activity_day: str | None = None
    activity: dict[str, float] = field(default_factory=dict)
    own: OwnStats | None = None
    public: dict[str, Any] | None = None  # 로그인 없이 읽은 글별 공감·댓글·검색 순위·오르는 키워드 (주인 10/30 — public_stats)


def day_word(d: date, today: date) -> str:
    """'오늘' / '어제' / '10. 12.' (날짜 가운데서 줄이 바뀌지 않게)."""
    if d == today:
        return "오늘"
    if d == today - timedelta(days=1):
        return "어제"
    return fmt_short_day(d).replace(" ", NBSP)


def missed_text(last_day: date, today: date) -> str:
    """마지막으로 읽은 날 뒤로 못 읽은 날: '어제 못 읽음' / '10. 12.부터 못 읽음' / '오늘 아직 못 읽음'."""
    first = last_day + timedelta(days=1)
    if first >= today:
        return "오늘 아직 못 읽음"
    if first == today - timedelta(days=1):
        return "어제 못 읽음"
    return f"{fmt_short_day(first)}부터 못 읽음"


def owner_url(template: Any, blog_id: str) -> str | None:
    """template 의 {blog_id} 자리를 채운 주소. blog_id 가 영문·숫자·_- 가 아니거나, https 주소가 아니거나,
    공백·따옴표·꺾쇠 같은 글자가 섞였으면 None (링크를 그리지 않음). str.format 은 쓰지 않는다 ({blog_id.x} 같은 틀 방지)."""
    if not isinstance(template, str) or not BLOG_ID_RE.fullmatch(blog_id):
        return None
    return https_url(template.replace("{blog_id}", blog_id))


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
        names = (self.options.get("blog"), self.options.get("blog_id"), self.key)
        own = export.own(*names)
        if own is not None and own.day > self.ctx.today():
            own = None  # 앞날 값은 믿지 않는다 (시계가 어긋남)
        # 자기 통계는 활동 기록이 몇 시간 멈춰 있어도(STALE) 쓴다 — 오래됐는지는 자기 읽은 시각(read_at)으로 따로 본다
        days = self.own_days(own)
        pub = (export.v2.get("public_stats") or {}).get(str(self.options.get("blog") or self.key)) if export.v2 else None
        if not export.ok:
            return FetchResult(export.status, note=export.note, read_at=export.written_at, days=days, detail=BlogLive(own=own, public=pub))
        blog = export.blog(*names)
        if blog is None:
            return FetchResult(UNAVAILABLE, note="Blog-Auto 활동 기록에 이 블로그가 없음 (options.blog 확인)", read_at=export.written_at,
                               days=days, detail=BlogLive(own=own, public=pub))
        assert export.written_at is not None
        day = self.ctx.local_day(export.written_at).isoformat()
        values = {k: v for k, v in blog.items() if k in AUTO_METRICS}
        days.setdefault(day, {}).update(values)
        return FetchResult(OK, days=days, read_at=export.written_at, detail=BlogLive(day, values, own, pub))

    # ---- 자기 통계 (Blog-Auto own_stats)
    def stored(self, metric: str, day: date) -> dict[str, Any] | None:
        """그날 저장된 값 {day, value, read_at, origin} (없으면 None)."""
        row = self.ctx.db.metric_latest(self.key, metric, until_day=day.isoformat())
        return row if row and row["day"] == day.isoformat() else None

    def manual_after(self, own: OwnStats) -> bool:
        """그날 자동으로 읽은 뒤에 주인이 이웃 수를 직접 넣었는지 (그러면 주인 값을 덮지 않는다).
        읽은 시각을 모르면 그날 넣은 값은 모두 주인 값이 이긴다."""
        row = self.stored("neighbors", own.day)
        if row is None or row.get("origin") != "manual":
            return False
        entered = from_iso(row.get("read_at"))
        return entered is not None and entered > (own.read_at or self.ctx.day_start_utc(own.day))

    def own_days(self, own: OwnStats | None) -> dict[str, dict[str, float]]:
        """자기 통계 중 날짜별로 쌓아 둘 값. 이웃 수: 읽은 날은 그 뒤에 주인이 넣은 값이 없을 때만,
        앞에 읽은 날(prev)은 그날 값이 하나도 없을 때만 (Blog-Auto 가 기억하는 어제 값으로 증감을 바로 셀 수 있게)."""
        days: dict[str, dict[str, float]] = {}
        if own is None:
            return days

        def put(d: date, metric: str, v: int | None) -> None:
            if v is not None:
                days.setdefault(d.isoformat(), {})[metric] = float(v)

        if not self.manual_after(own):
            put(own.day, "neighbors", own.neighbors)
        put(own.day, OWN_TOTAL, own.visitors_total)
        if (p := own.prev) is not None:
            if self.stored("neighbors", p.day) is None:
                put(p.day, "neighbors", p.neighbors)
            if self.stored(OWN_TOTAL, p.day) is None:
                put(p.day, OWN_TOTAL, p.visitors_total)
        if (y := own.yesterday) is not None:
            for metric, attr in OWN_DAY_METRICS:
                put(y.day, metric, getattr(y, attr))
        return days

    def own_stale(self, own: OwnStats) -> bool:
        """OWN_STALE_HOURS 넘게 못 읽었거나, 마지막으로 읽은 날이 그제 이전(어제를 통째로 못 읽음)인지.
        (그제 밤에 읽고 어제를 건너뛰면 36시간이 안 돼도 어제 값이 없다 — 읽은 시각을 모를 때도 날짜로)"""
        if (self.ctx.today() - own.day).days >= 2:
            return True
        return own.read_at is not None and self.ctx.now() - own.read_at > timedelta(hours=OWN_STALE_HOURS)

    def own_view(self, data: CardData, own: OwnStats) -> tuple[list[Stat], str, str, dict[str, Any]]:
        """자기 통계가 있을 때의 숫자 칸 네 개, 회색 안내, 주황 경고, data.json 값."""
        today = data.today
        stale = self.own_stale(own)
        local_read = own.read_at.astimezone(ZoneInfo(self.ctx.tz)) if own.read_at else None
        # 이웃 수: 직접 입력과 같은 지표의 가장 최근 값 (그날 읽은 뒤에 넣은 주인 값이면 '직접 입력')
        row = self.ctx.db.metric_latest(self.key, "neighbors", until_day=today.isoformat())
        manual = row is not None and row["origin"] == "manual"
        n_day = date.fromisoformat(row["day"]) if row else None
        change = data.change("neighbors")
        delta, delta_label = None, ""
        if change is not None:
            delta, _, prev_day = change
            delta_label = "어제 대비" if prev_day == today - timedelta(days=1) else f"{fmt_short_day(prev_day)} 대비"
        sub: list[str] = []
        if manual and n_day is not None:
            sub.append("직접 입력" + ("" if n_day == today else f" · {fmt_short_day(n_day)}"))
        elif n_day is not None and n_day != own.day:
            sub.append(f"{fmt_short_day(n_day)} 값")  # 이번에 이웃 수만 못 읽음
        if own.buddy_requests_pending:
            sub.append(f"받은 서로이웃 신청 {fmt_num(own.buddy_requests_pending)}")
        neighbors = Stat("이웃 수", row["value"] if row else None, "명", delta=delta, delta_label=delta_label,
                         sub=" · ".join(sub), stale=stale and not manual)
        visitors = Stat(f"방문자 · {day_word(own.day, today)}", own.visitors_today, "명",
                        sub=f"전체 {fmt_num(own.visitors_total)}명" if own.visitors_total is not None else "", stale=stale)
        # 오늘 조회수는 하루 중간에 읽은 값이라 몇 시 기준인지 함께
        bits = [f"{local_read:%H:%M} 기준"] if local_read else []
        bits += [f"{name} {fmt_num(v)}" for name, v in (("공감", own.likes_today), ("댓글", own.comments_today)) if v is not None]
        # 로그인 없이 공개 화면으로 읽은 날(주인 10/30 — 네이버 접속 끔)은 조회수가 없음 → 빈 칸을 그리지 않음
        public = own.views_today is None and own.likes_today is None and own.comments_today is None
        stats = [neighbors, visitors]
        if not public:
            stats.append(Stat(f"조회수 · {day_word(own.day, today)}", own.views_today, "회", sub=" · ".join(bits), stale=stale))
        if (y := own.yesterday) is not None and y.views is not None:
            ybits = [f"{name} {fmt_num(v)}" for name, v in (("공감", y.likes), ("댓글", y.comments)) if v is not None]
            if y.neighbor_change is not None:
                ybits.append(f"이웃 {fmt_signed(y.neighbor_change)}")
            stats.append(Stat(f"조회수 · {day_word(y.day, today)}", y.views, "회", sub=" · ".join(ybits), stale=stale))
        note = f"{'공개 화면' if public else '자동'} · {fmt_time(local_read) if local_read else fmt_short_day(own.day)} 읽음"
        warn = missed_text(own.day, today) if stale else ""
        y, p = own.yesterday, own.prev
        detail = {
            "day": own.day.isoformat(), "read_at": local_read.isoformat() if local_read else None, "stale": stale,
            # 카드의 이웃 수가 어디서 왔는지 (쌓인 값이 하나도 없으면 None)
            "neighbors_from": ("manual" if manual else "auto") if row else None, "neighbors": own.neighbors,
            "visitors_today": own.visitors_today, "visitors_total": own.visitors_total, "views_today": own.views_today,
            "likes_today": own.likes_today, "comments_today": own.comments_today,
            "neighbor_change_today": own.neighbor_change_today, "buddy_requests_pending": own.buddy_requests_pending,
            "yesterday": {"day": y.day.isoformat(), "views": y.views, "likes": y.likes, "comments": y.comments,
                          "neighbor_change": y.neighbor_change} if y else None,
            "prev": {"day": p.day.isoformat(), "neighbors": p.neighbors, "visitors_total": p.visitors_total} if p else None,
        }
        return stats, note, warn, detail

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
            hints.append("options.stats_url 을 못 써요 — https 주소, 공백·따옴표·괄호 없이 (통계 열기 링크를 숨김)")
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

    def posted(self, today: date) -> PostCount:
        """올린 글 (주인 10/25): Blog-Auto publishes 표로 센 날마다 발행 수 (기록 파일 post_counts.blogs — 지금도 발행 상태인 글만)."""
        days = ((self.blogauto_post_counts() or {}).get("blogs") or {}).get(str(self.options.get("blog") or self.key))
        return post_count(days, today) if days is not None else PostCount(why="기록 없음 · 블로그 프로그램이 아직 안 보내요")

    @staticmethod
    def public_groups(pub: dict[str, Any] | None) -> list[ListGroup]:
        """로그인 없이 읽은 목록 (주인 10/30): 공감·댓글 많은 글 · 검색 순위 · 오르는 키워드 — 각 5줄까지, 줄이 없으면 그 묶음은 뺌."""
        if not pub:
            return []
        out = []
        posts = [p for p in pub.get("posts") or [] if (p.get("likes") or 0) + (p.get("comments") or 0) > 0][:5]
        if posts:
            out.append(ListGroup("공감·댓글 많은 글", "공개 화면 · 로그인 없이", [
                ListEntry(p["title"], pill=f"공감 {fmt_num(p.get('likes'))}", sub=f"댓글 {fmt_num(p.get('comments'))}") for p in posts]))
        ranks = (pub.get("ranks") or [])[:5]
        if ranks:
            out.append(ListGroup("검색 순위", "네이버 검색 정확도순 · 100위까지", [
                ListEntry(r["title"], pill=f"{r['rank']}위" if r.get("rank") else "100위 밖", hot=bool(r.get("rank") and r["rank"] <= 10),
                          sub=f"검색어 「{r['q']}」" if r.get("q") else "") for r in ranks]))
        trends = [t for t in pub.get("trends") or [] if t.get("growth", 0) > 0][:5]
        if trends:
            out.append(ListGroup("오르는 키워드", "데이터랩 · 최근 3일 검색", [
                ListEntry(t["keyword"], pill=f"+{round(t['growth'] * 100)}%", hot=t["growth"] >= 0.5) for t in trends]))
        return out

    def card(self, data: CardData) -> CardView:
        live = data.live
        info = live.detail if live is not None and isinstance(live.detail, BlogLive) else BlogLive()
        auto_day = info.activity_day if live is not None and live.status == OK else None
        auto = info.activity if auto_day else {}
        own = info.own
        today = data.today.isoformat()
        visit = data.latest("visitors")
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
            bits = [f"{when} 발행 {fmt_num(auto.get('posts_today'))}"]
            # 봇이 안 하는 일(임시저장·서로이웃·댓글 — 주인 10/27·10/30 끔)은 0 이면 빼서 늘 0 인 줄이 안 보이게
            if auto.get("drafts_today"):
                bits.append(f"임시저장 {fmt_num(auto.get('drafts_today'))}")
            if auto.get("neighbor_req_today") or auto.get("neighbor_req_7d"):
                req = f"서로이웃 신청 {fmt_num(auto.get('neighbor_req_today'))}"
                if auto.get("neighbor_req_7d") is not None:
                    req += f" (7일 {fmt_num(auto.get('neighbor_req_7d'))})"
                bits.append(req)
            bits += [f"{name} {fmt_num(auto.get(k))}" for name, k in (("댓글", "comments_today"), ("답글", "replies_today")) if auto.get(k)]
            activity.append(" · ".join(bits))
        use_visitors = len(data.series("visitors")) >= 2 or not data.series("neighbors")
        read_at = live.read_at if live and live.read_at else None
        manual_at = self.ctx.db.metric_last_read(self.key, origin="manual")
        if manual_at and (read_at is None or manual_at > read_at):
            read_at = manual_at
        summary: dict[str, float] = {}
        if (d := data.today_delta("neighbors")) is not None:  # 자동으로 읽은 이웃 수도 같은 지표라 함께
            summary["audience_delta"] = d
        elif own is not None and own.day == data.today and own.neighbor_change_today is not None:
            summary["audience_delta"] = float(own.neighbor_change_today)  # 어제 이웃 수가 없으면 통계의 오늘 이웃증감수
        if auto_day == today and auto.get("posts_today") is not None:
            summary["posts_today"] = auto["posts_today"]
        if visit and visit[0] == (data.today - timedelta(days=1)).isoformat():
            summary["visitors_yesterday"] = visit[1]
        links, link_hints = self.owner_links()
        if own is not None:
            stats, stats_note, stats_warn, own_detail = self.own_view(data, own)
            detail: dict[str, Any] = {"own_stats": own_detail}
        else:  # 자기 통계가 아직 없음 → 예전처럼 직접 넣은 이웃 수·어제 방문자
            # 날짜는 짧게, 칸이 좁아도 날짜 가운데서 줄이 바뀌지 않게
            stats = [self.stat_with_delta(data, "neighbors", "이웃 수", "명"),
                     Stat(f"방문자 · {fmt_short_day(visit[0]).replace(' ', NBSP)}" if visit else "어제 방문자",
                          visit[1] if visit else None, "명")]
            has_stats = any(link.primary for link in links)
            stats_note, stats_warn, detail = STATS_NOTE + (" — 지금 숫자는 통계 열기로 확인" if has_stats else ""), "", {}
        return self.view(
            data, stats=stats,
            links=links, hints=link_hints, stats_note=stats_note, stats_warn=stats_warn,
            # 방문자는 날마다 오르내려 '7일 평균'(애드포스트 기준과 같은 셈)으로, 이웃 수는 쌓이는 수라 그대로
            spark=data.rolling("visitors", 7, how="avg") if use_visitors else data.series("neighbors"),
            spark_label="방문자 7일 평균" if use_visitors else "이웃 수", rows=rows, activity=activity,
            read_at=read_at, summary=summary, detail=detail, posted=self.posted(data.today), groups=self.public_groups(info.public),
        )
