"""'지금 할 일' — 대시보드 맨 위 카드 (주인 요청 10/15: "그거 기반으로 대시보드에 다 넣어줘 지금 해야할 일 목록 느낌으로").

두 곳을 합친다:
  1) Blog-Auto 기록 파일의 "todo" — Blog-Auto 알림 목록(notify)에서 아직 열린 주인 일 (보낼 목록에 있거나, 보냈는데
     ttl 안에 해결되지 않은 것 — notify.is_open 과 같은 뜻. 딜봇이 맡긴 link:·review:·unsure:·publish_broken·linkprice:·sns: 도
     여기 들어 있음) + Blog-Auto 가 셈한 상태 (블로그마다 발행 대기 글, 받은 서로이웃 신청, 통계 읽기 막힘, 7일째 새 글 없음)
  2) 봇 DB 의 지금 상태 (파일보다 새로움): 링크 기다리는 딜 (한 줄로 묶어 가장 먼저 끝나는 것의 'N시간 남음' +
     '링크 기다리는 딜' 카드로 가는 버튼), 확인(/ok)을 기다리는 글 (정품 확인·정보 글 확인), 채널에 올라갔는지 모르는 글 (/posted)
같은 키면 봇 DB 줄이 이긴다. 파일의 link:·review:·unsure: 줄은 그 글이 지금 열려 있지 않으면 버린다 (이미 처리함).
순서: ⛔ > 급함·🔥·⚠️ > 끝나는 시간이 가까운 것(링크·확인 기다림) > 🔔 > ℹ️ (같은 칸 안에서는 오래 기다린 것부터).

바깥 요청 없음 — 봇 DB 와 로컬 파일만 본다. 버튼 주소는 https 만, 텔레그램 주소(t.me)는 그리지 않는다
(봇 이름이 설정에 없어서 — 대신 '→ 텔레그램 핫딜봇 …' 글로 어디서 하는지 적는다).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from dealbot.dashboard.blogauto import TODO_DEFAULT_MARKER, TodoItem
from dealbot.dashboard.model import https_url
from dealbot.monitoring.admin import is_telegram_url
from dealbot.storage.db import LINK_WAITING, MAYBE_POSTED
from dealbot.utils.text import truncate

log = logging.getLogger(__name__)

STOP, HOT, WARN, TODO, INFO = "⛔[멈춤]", "🔥[핫]", "⚠️[할 일]", "🔔[할 일]", "ℹ️"
SEVERITY = (STOP, HOT, WARN, TODO, INFO)  # Blog-Auto notify.SEVERITY 순서 + 참고
# 화면 칩: (이모지, 글, css — 'm-' 를 붙여 .todo 카드 같은 다른 규칙과 겹치지 않게)
CHIPS = {STOP: ("⛔", "멈춤", "m-stop"), HOT: ("🔥", "핫", "m-hot"), WARN: ("⚠️", "할 일", "m-warn"),
         TODO: ("🔔", "할 일", "m-todo"), INFO: ("ℹ️", "참고", "m-info")}
TITLE = "지금 할 일"
EMPTY_TEXT = "지금 할 일 없음 ✓"
SHOW = 6  # 처음에 보이는 줄 (나머지는 '더 보기')
NO_BLOG = "블로그 쪽 할 일을 못 읽음"
NO_DEAL = "딜봇 쪽 할 일을 못 읽음"
UNSURE_DAYS = 7  # '채널 확인' 은 이 기간 안에 생긴 것만 (같은 딜을 다시 올릴 수 있게 되는 publish.dedup_days 와 같은 폭)
REVIEW_MAX = 30
UNSURE_MAX = 20
LINK_IDS_MAX = 20  # data.json 의 ids (글에는 앞의 LINK_IDS_TEXT 개만)
LINK_IDS_TEXT = 5
NAME_MAX = 50
LINK_GROUP = "link_request"  # 링크 기다리는 딜 한 줄의 키·묶음 (두 봇 알림의 group 과 같은 이름)
DEAL_KEY = re.compile(r"^(link|review|unsure):([0-9]{1,9})$")  # 딜봇이 맡긴 알림 키 (봇 DB 의 글 번호)
WAITING_KINDS = ("waiting", "drafts_waiting", "draft_ready", "drafts")  # 주소가 없으면 '발행 대기 글' 카드로 가는 버튼
BLOG_LABELS = {"finance": "아카이브", "issue": "Deren's Pick", "hotdeal": "오늘의 핫딜 블로그", "dealbot": "핫딜 봇"}
_FAR = datetime.max.replace(tzinfo=UTC)


@dataclass(slots=True)
class TodoRow:
    """할 일 한 줄 (화면·data.json)."""

    key: str
    marker: str
    short: str  # 굵은 제목
    what: str = ""  # 카톡 첫 줄 (표시 뺀 것)
    how: str = ""  # '→ 어떻게' (두 줄까지)
    since: datetime | None = None
    urgent: bool = False
    blog: str = ""
    blog_label: str = ""  # 화면 작은 줄의 이름 (블로그 카드 제목 · '핫딜 봇')
    kind: str = ""
    link: str | None = None  # https (텔레그램 주소 아님) — 주인 브라우저 새 탭
    link_label: str = ""
    anchor: str | None = None  # 같은 페이지 안 카드 ('#card-links')
    anchor_label: str = ""
    expires_at: datetime | None = None  # 이때 지나면 봇이 그 글을 버림 (링크·확인 기다림)
    group: str = ""
    count: int = 1
    source: str = "blogauto"  # blogauto | dealbot (봇 DB 에서 바로 읽은 줄)
    ids: list[int] = field(default_factory=list)

    @property
    def text(self) -> str:
        """카톡 메모 모양: '표시 첫 줄\n→ 어떻게'."""
        head = f"{self.marker} {self.what or self.short}"
        return f"{head}\n{self.how}" if self.how else head


@dataclass(slots=True)
class TodoView:
    rows: list[TodoRow] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)  # 회색 작은 안내 ('블로그 쪽 할 일을 못 읽음' 등)
    blog_at: datetime | None = None  # Blog-Auto 가 할 일 목록을 만든 시각


# ---------------------------------------------------------------- 글자
def ago(since: datetime, now: datetime) -> str:
    """'방금' · '5분 전' · '3시간 전' · '2일 전' (시계가 어긋나 앞날이면 '방금')."""
    secs = (now - since).total_seconds()
    if secs < 60:
        return "방금"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}분 전"
    hours = mins // 60
    return f"{hours}시간 전" if hours < 24 else f"{hours // 24}일 전"


def left(expires_at: datetime, now: datetime) -> str:
    """'40분 남음' · '14시간 남음' · '3일 남음' (시간은 내림 — 넉넉하게 보이지 않게). 지났으면 '곧 끝남' (다음 정리 때 버려짐)."""
    secs = (expires_at - now).total_seconds()
    if secs < 60:
        return "곧 끝남"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}분 남음"
    hours = mins // 60
    return f"{hours}시간 남음" if hours < 48 else f"{hours // 24}일 남음"


def link_target(url: Any) -> str | None:
    """버튼으로 그려도 되는 주소: https 이고 텔레그램 주소가 아닌 것 (model.https_url 이 따옴표·꺾쇠·'@' 등도 거름)."""
    u = https_url(url)
    return None if u is None or is_telegram_url(u) else u


def link_label(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith("naver.com") and "/buddy" in parts.path:
        return "신청 보기"
    if host.endswith("blog.naver.com"):
        return "네이버에서 열기"
    return "열기"


def _name(text: str, limit: int = NAME_MAX) -> str:
    return truncate(" ".join(text.split()), limit)


# ---------------------------------------------------------------- Blog-Auto 가 보낸 줄
def from_export(items: list[TodoItem], *, waiting_anchor: str | None = None) -> list[TodoRow]:
    out: list[TodoRow] = []
    for it in items:
        url = link_target(it.link)
        anchor = waiting_anchor if url is None and it.kind in WAITING_KINDS else None
        # 칩에 이미 있는 이모지로 시작하는 요약('🔥 Pick 「…」')은 이모지를 뺀다 (두 번 보이지 않게)
        short = it.short
        if it.marker in CHIPS:
            short = short.removeprefix(CHIPS[it.marker][0]).strip() or it.short
        out.append(TodoRow(
            key=it.key, marker=it.marker if it.marker in SEVERITY else TODO_DEFAULT_MARKER, short=short, what=it.what,
            how=it.how, since=it.since, urgent=it.urgent, blog=it.blog, kind=it.kind, link=url,
            link_label=link_label(url) if url else "", anchor=anchor, anchor_label="대기 글 보기" if anchor else "",
            group=it.group, count=it.count,
        ))
    return out


# ---------------------------------------------------------------- 봇 DB 지금 상태
def live_rows(db: Any, now: datetime, *, ttl_hours: float | None, links_anchor: str | None,
              shop_label: Callable[[str], str] = lambda k: k) -> list[TodoRow]:
    """링크 기다리는 딜(한 줄) · 확인 기다리는 글 · 채널에 올라갔는지 모르는 글. 글은 카톡 알림(monitoring.admin)과 같은 말."""
    ttl = timedelta(hours=ttl_hours) if ttl_hours else None
    rows: list[TodoRow] = []
    total = db.awaiting_count()
    first = db.awaiting_items(LINK_IDS_MAX) if total else []  # 오래된 것부터 = 먼저 끝나는 것부터
    if first:
        oldest = first[0]
        ids = [it.id for it in first]
        if total == 1:
            p = oldest.deal.product
            price = f" — {p.price:,}원" if p.has_price else ""
            short = f"핫딜 링크 #{oldest.id}"
            what = f"링크 필요 #{oldest.id} [{shop_label(p.shop) or p.shop}] {_name(p.name)}{price}"
        else:
            shown = ids[:LINK_IDS_TEXT]
            more = f" 외 {total - len(shown)}건" if total > len(shown) else ""
            short = f"핫딜 링크 {total}건"
            what = f"링크 필요 {total}건 " + " ".join(f"#{i}" for i in shown) + more
        how = ("→ 아래 「링크 기다리는 딜」에서 바로 넣거나 텔레그램 핫딜봇 📌고정 목록에 답장" if links_anchor
               else "→ 텔레그램 핫딜봇 📌고정 목록에 한 번에 답장")
        rows.append(TodoRow(
            key=LINK_GROUP, marker=TODO, short=short, what=what, how=how, since=oldest.created_at, blog="dealbot",
            kind="link_request", anchor=links_anchor, anchor_label="링크 넣기" if links_anchor else "",
            expires_at=oldest.created_at + ttl if ttl else None, group=LINK_GROUP, count=total, source="dealbot", ids=ids,
        ))
    for it in db.awaiting_items(REVIEW_MAX, statuses=("awaiting_approval",)):
        p = it.deal.product
        if p.deal_kind == "info" or "info_draft" in p.extra:
            short, kind, url = f"정보 글 확인 #{it.id}", "review_info", None
            what = f"정보 글 확인 #{it.id} {_name(p.name)} — 이대로 올릴까요?"
            how = f"→ 텔레그램 핫딜봇에서 /ok {it.id} · /skip {it.id}"
        else:
            short, kind, url = f"정품 확인 #{it.id}", "review_auth", link_target(p.url)
            what = f"정품 확인 #{it.id} {_name(p.name)}"
            how = f"→ 텔레그램 핫딜봇에서 /ok {it.id} 또는 /skip {it.id}"
        rows.append(TodoRow(
            key=f"review:{it.id}", marker=TODO, short=short, what=what, how=how, since=it.created_at, blog="dealbot",
            kind=kind, link=url, link_label="상품 열기" if url else "", expires_at=it.created_at + ttl if ttl else None,
            source="dealbot", ids=[it.id],
        ))
    for it, at in db.maybe_posted_items(now - timedelta(days=UNSURE_DAYS), UNSURE_MAX):
        rows.append(TodoRow(
            key=f"unsure:{it.id}", marker=TODO, short=f"채널 확인 #{it.id}", what=f"채널 확인 #{it.id} {_name(it.deal.product.name)}",
            how=f"→ 채널에 있으면 텔레그램 핫딜봇 /posted {it.id} · 없으면 /link {it.id}", since=at, blog="dealbot",
            kind="publish_unsure", source="dealbot", ids=[it.id],
        ))
    return rows


def deal_open(db: Any, kind: str, queue_id: int) -> bool:
    """딜봇이 맡긴 알림(link:·review:·unsure:)의 글이 지금도 주인 손을 기다리나 (아니면 이미 처리함)."""
    item = db.get_queue_item(queue_id)
    if item is None:
        return False
    if kind == "link":
        return item.status in LINK_WAITING
    if kind == "review":
        return item.status == "awaiting_approval"
    return item.status == "failed" and (item.last_error or "").startswith(MAYBE_POSTED)


# ---------------------------------------------------------------- 합치기·순서
def _severity(r: TodoRow) -> tuple[int, int]:
    return SEVERITY.index(r.marker) if r.marker in SEVERITY else SEVERITY.index(TODO), 0 if r.urgent else 1


def sort_key(r: TodoRow) -> tuple[int, int, datetime]:
    """⛔ > 급함·🔥·⚠️ > 끝나는 시간이 가까운 것 > 🔔 > ℹ️. 같은 칸은 오래 기다린 것(끝나는 칸은 먼저 끝나는 것)부터."""
    since = r.since or _FAR
    sev = _severity(r)[0]
    if r.marker == STOP:
        return 0, 0, since
    if r.urgent or r.marker in (HOT, WARN):
        return 1, sev, since
    if r.expires_at is not None:
        return 2, 0, r.expires_at
    if r.marker == INFO:
        return 4, 0, since
    return 3, 0, since


def merge(exported: list[TodoRow], live: list[TodoRow] | None, *,
          is_open: Callable[[str, int], bool] = lambda kind, qid: True) -> list[TodoRow]:
    """Blog-Auto 줄 + 봇 DB 줄 → 순서대로. live 가 None(봇 DB 를 못 읽음)이면 Blog-Auto 가 보낸 그대로.
    같은 키는 봇 DB 줄이 이기고, 파일의 link:·review:·unsure: 는 그 글이 지금 열려 있을 때만 남는다
    (링크 기다리는 딜은 열려 있어도 봇 DB 의 한 줄에 들어 있으므로 버림). 같은 키가 또 오면 더 급한 것 하나만."""
    rows: list[TodoRow] = []
    if live is None:
        rows = list(exported)
    else:
        live_keys = {r.key for r in live}
        for r in exported:
            if r.key in live_keys or r.group == LINK_GROUP:
                continue
            m = DEAL_KEY.match(r.key)
            if m:
                if m.group(1) == "link":
                    continue
                try:
                    still = is_open(m.group(1), int(m.group(2)))
                except Exception as e:  # noqa: BLE001 — 확인을 못 하면 Blog-Auto 가 보낸 대로 둔다 (일을 잃지 않게)
                    log.warning("dashboard todo: %s 확인 실패: %s", r.key, e)
                    still = True
                if not still:
                    continue
            rows.append(r)
        rows.extend(live)
    best: dict[str, TodoRow] = {}
    for r in rows:
        cur = best.get(r.key)
        if cur is None or _severity(r) < _severity(cur):
            best[r.key] = r
    return sorted(best.values(), key=sort_key)


def build(export: Any, db: Any, now: datetime, *, ttl_hours: float | None, links_anchor: str | None,
          waiting_anchor: str | None = None, stale_hours: float = 3.0, labels: dict[str, str] | None = None,
          shop_label: Callable[[str], str] = lambda k: k) -> TodoView:
    """export: BlogAutoExport (또는 None — 기록 경로가 없음). labels: 블로그 이름 → 화면 이름 (블로그 카드 제목)."""
    hints: list[str] = []
    todo = getattr(export, "todo", None) if export is not None else None
    exported = from_export(todo.items, waiting_anchor=waiting_anchor) if todo is not None else []
    blog_at = todo.generated_at if todo is not None else None
    if todo is None:
        if export is None or getattr(export, "written_at", None) is None:
            hints.append(f"{NO_BLOG} — Blog-Auto 기록 파일이 없거나 읽지 못했어요")
        else:
            hints.append(f"{NO_BLOG} — Blog-Auto 가 아직 할 일 목록을 보내지 않아요 (새 버전으로 올리면 보여요)")
    else:
        at = blog_at or export.written_at
        if at is not None and (now - at) > timedelta(hours=stale_hours):
            hours = int((now - at).total_seconds() // 3600)
            hints.append(f"블로그 쪽 할 일은 {hours}시간 전 것이에요 — Blog-Auto 가 멈췄는지 확인해 주세요")
    try:
        live: list[TodoRow] | None = live_rows(db, now, ttl_hours=ttl_hours, links_anchor=links_anchor, shop_label=shop_label)
    except Exception as e:  # noqa: BLE001 — 봇 DB 를 못 읽어도 Blog-Auto 가 보낸 줄은 보인다
        log.warning("dashboard todo: 봇 DB 읽기 실패: %s", e)
        live = None
        hints.append(NO_DEAL)
    rows = merge(exported, live, is_open=lambda kind, qid: deal_open(db, kind, qid))
    names = {**BLOG_LABELS, **(labels or {})}
    for r in rows:
        r.blog_label = names.get(r.blog, "")
    return TodoView(rows=rows, hints=hints, blog_at=blog_at)
