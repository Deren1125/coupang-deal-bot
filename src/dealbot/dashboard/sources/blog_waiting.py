"""발행 대기 글 (블로그 세 개를 한 카드에): Blog-Auto 기록 파일의 "waiting" 만 본다 (주인 요청 10/15).

블로그마다 한 줄: '대기 N개 · 인기 예상 M개' 와 점수가 높은 글 3개(제목 + 점수 알약). 대기 글이 없으면 '대기 글 없음',
파일·"waiting"·그 블로그가 없으면 '판단 불가' (오류 띠가 아니라 그 줄만). 기록 시각은 다른 Blog-Auto 카드처럼 written_at.
Blog-Auto 는 자기 DB 만 보고 센다 (네이버에 요청하지 않음). 딜봇은 파일만 읽고, 임시저장 글은 읽지도 고치지도 않는다.

options:
  blogs  [{blog: finance, title: 아카이브}, ...] — 없으면 config 의 naver_blog 카드(options.blog·카드 제목)를 그 순서대로
"""

from __future__ import annotations

from typing import Any

from dealbot.dashboard import icons
from dealbot.dashboard.blogauto import WaitingBlog, WaitingExport
from dealbot.dashboard.model import (
    GOOD,
    OK,
    PARTIAL,
    UNAVAILABLE,
    CardView,
    FetchResult,
    ListEntry,
    ListGroup,
    Stat,
    fmt_num,
)
from dealbot.dashboard.sources.base import CardData, Source, register

UNKNOWN_TEXT = "판단 불가"
EMPTY_TEXT = "대기 글 없음"
SHOW_TOP = 3
DEFAULT_THRESHOLD = 70  # 파일에 threshold 가 없을 때 알약 강조 기준 (Blog-Auto 기본값과 같음)
# naver_blog 카드가 하나도 없을 때의 줄 (Blog-Auto 블로그 이름, 줄 이름)
DEFAULT_BLOGS = (("finance", "아카이브"), ("issue", "픽"), ("hotdeal", "핫딜"))
NO_WAITING = "no_waiting"  # 기록 파일은 있는데 "waiting" 이 없음 (예전 Blog-Auto) — 오류가 아니라 회색 안내 한 줄


@register
class BlogWaitingSource(Source):
    type = "blog_waiting"
    kind = "export"
    default_title = "발행 대기 글"
    icon = icons.WAITING

    def blogs(self) -> list[tuple[str, str]]:
        """(Blog-Auto 블로그 이름(소문자), 줄 이름) 목록."""
        raw = self.options.get("blogs")
        out: list[tuple[str, str]] = []
        if isinstance(raw, list):
            for b in raw:
                if isinstance(b, dict) and isinstance(b.get("blog"), str) and b["blog"].strip():
                    name = b["blog"].strip().lower()
                    out.append((name, str(b.get("title") or name)))
            if out:
                return out
        for c in self.ctx.config.cards:
            blog = c.options.get("blog")
            if c.enabled and c.type == "naver_blog" and isinstance(blog, str) and blog.strip():
                out.append((blog.strip().lower(), c.title or c.key))
        return out or list(DEFAULT_BLOGS)

    def live(self) -> FetchResult:
        reader = self.ctx.blogauto
        if reader is None:
            return FetchResult(UNAVAILABLE, note="Blog-Auto 활동 기록 경로가 설정되지 않음")
        export = reader.read()
        if not export.ok:
            return FetchResult(export.status, note=export.note, read_at=export.written_at)
        assert export.written_at is not None
        waiting = export.waiting
        if waiting is None:
            return FetchResult(UNAVAILABLE, read_at=export.written_at, detail=NO_WAITING)
        values: dict[str, float] = {}
        known: list[WaitingBlog] = []
        for blog, _ in self.blogs():
            b = waiting.blogs.get(blog)
            if b is None:
                continue
            known.append(b)
            values[f"waiting_{blog}"] = float(b.count)
            if b.popular is not None:
                values[f"popular_{blog}"] = float(b.popular)
        if known:
            values["waiting_total"] = float(sum(b.count for b in known))
            if all(b.popular is not None for b in known):
                values["popular_total"] = float(sum(b.popular or 0 for b in known))
        n_blogs = len(self.blogs())
        status = OK if len(known) == n_blogs else PARTIAL if known else UNAVAILABLE
        day = self.ctx.local_day(export.written_at).isoformat()
        return FetchResult(status, days={day: values} if values else {}, read_at=export.written_at, detail=waiting)

    def card(self, data: CardData) -> CardView:
        live = data.live
        waiting = live.detail if live is not None and live.status in GOOD and isinstance(live.detail, WaitingExport) else None
        threshold = waiting.threshold if waiting is not None and waiting.threshold is not None else DEFAULT_THRESHOLD
        groups: list[ListGroup] = []
        detail_blogs: list[dict[str, Any]] = []
        known: list[WaitingBlog] = []
        for blog, label in self.blogs():
            b = waiting.blogs.get(blog) if waiting is not None else None
            if b is None:
                groups.append(ListGroup(label, UNKNOWN_TEXT, unknown=True))
                detail_blogs.append({"blog": blog, "title": label, "status": "unknown", "count": None, "popular": None, "top": []})
                continue
            known.append(b)
            if b.count == 0:
                summary = EMPTY_TEXT
            else:
                summary = f"대기 {fmt_num(b.count)}개 · 인기 예상 {fmt_num(b.popular)}개"
            entries = [ListEntry(p.title, pill=f"{p.score}점" if p.score is not None else "",
                                 hot=p.score is not None and p.score >= threshold) for p in b.top[:SHOW_TOP]]
            groups.append(ListGroup(label, summary, entries))
            detail_blogs.append({
                "blog": blog, "title": label, "status": "ok", "count": b.count, "popular": b.popular,
                "top": [{"title": p.title, "score": p.score, "saved_at": p.saved_at.isoformat() if p.saved_at else None,
                         "category": p.category} for p in b.top],
            })
        total = float(sum(b.count for b in known)) if known else None
        popular = float(sum(b.popular or 0 for b in known)) if known and all(b.popular is not None for b in known) else None
        partial = len(known) < len(groups)
        note = ""
        if live is not None and live.detail == NO_WAITING:
            note = "Blog-Auto 가 아직 발행 대기 글 수를 보내지 않아요 — Blog-Auto 를 새 버전으로 올리면 보여요."
        elif waiting is not None and partial:
            note = f"블로그 {len(groups) - len(known)}개는 Blog-Auto 가 대기 글을 세지 못했어요 (그 줄은 판단 불가)."
        stats = [
            Stat("대기 글", total, "개", missing=UNKNOWN_TEXT,
                 sub=f"블로그 {len(groups)}개 중 {len(known)}개만" if known and partial else f"블로그 {len(groups)}개 합계"),
            Stat("인기 예상", popular, "개", missing=UNKNOWN_TEXT, sub=f"{threshold}점 이상"),
        ]
        return self.view(
            data, stats=stats, spark=data.series("waiting_total"), spark_label="대기 글", groups=groups, note=note,
            read_at=live.read_at if live is not None else None,
            detail={"threshold": waiting.threshold if waiting is not None else None, "blogs": detail_blogs},
        )
