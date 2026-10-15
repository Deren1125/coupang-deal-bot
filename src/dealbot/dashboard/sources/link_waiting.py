"""링크 기다리는 딜: 주인이 제휴 링크를 만들어 줘야 올라가는 핫딜 (주인 요청 10/15 — '링크를 내가 줘야 하니 대시보드에 다 띄우자').

봇 DB 만 본다. 목록은 링크 요청 카톡·텔레그램 링크 요청·고정 공지(/pending)와 **같은 묶음** — db.awaiting_items() 의 기본
상태 묶음 LINK_WAITING ('awaiting_link'). 새것부터 최대 SHOW 개 + 모두 몇 개인지.
줄마다 상품명·가격·딜 점수·기다린 시간, '상품 열기 ↗'(그 상품 페이지 — https 만, 주인 브라우저가 새 탭으로 엶) 와
'링크 넣기' 칸 (POST /dashboard/link → 텔레그램 답장과 같은 attach_link, service.Dashboard.attach_owner_link).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    OK,
    CardView,
    FetchResult,
    ListEntry,
    ListGroup,
    Stat,
    fmt_num,
    fmt_time,
    https_url,
)
from dealbot.dashboard.sources.base import CardData, Source, register
from dealbot.utils.text import truncate

SHOW = 10
NAME_MAX = 60


def waited(delta: timedelta) -> tuple[float, str]:
    """기다린 시간 → (숫자, 단위): 1시간 안은 분, 이틀 안은 시간, 그 뒤는 일."""
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return float(mins), "분"
    if mins < 48 * 60:
        return float(mins // 60), "시간"
    return float(mins // (24 * 60)), "일"


@register
class LinkWaitingSource(Source):
    type = "dealbot_links"
    kind = "internal"
    default_title = "링크 기다리는 딜"
    icon = icons.LINK

    def live(self) -> FetchResult:
        db = self.ctx.db
        items = db.awaiting_items(SHOW, newest_first=True)
        total = db.awaiting_count()
        oldest = db.awaiting_items(1)
        return FetchResult(OK, values={"links_waiting": float(total)}, read_at=self.ctx.now(),
                           detail={"items": items, "total": total, "oldest": oldest[0].created_at if oldest else None})

    def card(self, data: CardData) -> CardView:
        detail: dict[str, Any] = data.live.detail if data.live is not None and isinstance(data.live.detail, dict) else {}
        items = list(detail.get("items") or [])
        total = int(detail.get("total") or 0)
        now = self.ctx.now()
        tz = ZoneInfo(self.ctx.tz)
        entries: list[ListEntry] = []
        rows: list[dict[str, Any]] = []
        for it in items:
            p = it.deal.product
            since: datetime = it.created_at
            n, unit = waited(now - since)
            shop = self.ctx.shop_label(p.shop)
            price = f"{p.price:,}원" if p.has_price else ""
            url = https_url(p.url)
            sub = " · ".join(x for x in (f"#{it.id}", shop, price, f"{fmt_time(since.astimezone(tz))}부터 {fmt_num(n)}{unit}째") if x)
            entries.append(ListEntry(truncate(" ".join(p.name.split()), NAME_MAX), pill=f"점수 {fmt_num(round(it.score, 1))}",
                                     sub=sub, url=url, url_label="상품 열기", queue_id=it.id))
            rows.append({"queue_id": it.id, "name": p.name, "shop": shop, "price": p.price if p.has_price else None,
                         "score": it.score, "waiting_since": since.astimezone(tz).isoformat(), "url": url})
        oldest = detail.get("oldest")
        old_n, old_unit = waited(now - oldest) if isinstance(oldest, datetime) else (None, "")
        stats = [
            Stat("기다리는 딜", float(total) if data.live is not None else None, "개",
                 sub=f"새것부터 {len(items)}개만 보여요" if total > len(items) else ""),
            Stat("가장 오래 기다림", old_n, old_unit),
        ]
        note = "링크를 넣으면 텔레그램 링크 요청에 답장한 것과 같아요 — 올릴 차례가 되면 채널에 올라가요."
        if total > len(items):
            note += f" 나머지 {total - len(items)}개는 텔레그램 /pending 에서 볼 수 있어요."
        return self.view(
            data, stats=stats, spark=data.series("links_waiting"), spark_label="링크 기다리는 딜",
            groups=[ListGroup(entries=entries)] if entries else [], empty_text="지금 링크를 기다리는 딜이 없어요.",
            note=note if entries else "", read_at=data.live.read_at if data.live else None,
            detail={"total": total, "shown": len(rows), "items": rows},
        )
