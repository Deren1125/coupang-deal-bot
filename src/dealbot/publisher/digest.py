"""하루치 발행 딜을 모아 블로그 글 한 편으로 만든다 (복붙용). 매일 밤 정해진 시각에 관리자 챗으로 보낸다."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from dealbot.config import BlogDigestConfig
from dealbot.publisher.copyblocks import CopyBlock
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry
from dealbot.storage.db import QueueItem

log = logging.getLogger(__name__)

WEEKDAYS = "월화수목금토일"
CHUNK_LIMIT = 3000  # 텔레그램 한 메시지(4096자) 안에 <pre> 째로 안전하게 들어가는 길이
BASE_TAGS = ["핫딜", "오늘의핫딜", "특가", "할인정보", "쇼핑핫딜", "가성비", "핫딜정보", "쿠팡핫딜"]


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def split_sections(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """긴 글을 '■' 단락 경계에서 나눈다 (한 메시지에 다 안 들어갈 때). 단락 하나가 너무 길면 줄 단위로."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for part in text.split("\n■"):
        piece = part if not current and not chunks else "■" + part
        if len(current) + len(piece) + 1 > limit and current:
            chunks.append(current.rstrip())
            current = ""
        while len(piece) > limit:
            cut = piece.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append((current + piece[:cut]).rstrip())
            current, piece = "", piece[cut:].lstrip("\n")
        current = piece if not current else current + "\n" + piece
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


class BlogDigestBuilder:
    def __init__(self, cfg: BlogDigestConfig, renderer: TemplateRenderer, registry: ShopRegistry | None = None) -> None:
        self.cfg = cfg
        self.renderer = renderer
        self.registry = registry or ShopRegistry()

    def _ctx(self, item: QueueItem) -> dict[str, Any]:
        deal = item.deal
        p = deal.product
        v = deal.verdict
        shop = self.registry.get(p.shop)
        draft = p.extra.get("info_draft") if isinstance(p.extra.get("info_draft"), dict) else {}
        rate = v.discount_rate if v.discount_rate is not None else p.effective_discount_rate()
        return {
            "name": p.name,
            "headline": p.headline,
            "price": p.price,
            "has_price": p.has_price,
            "original_price": p.original_price,
            "discount_rate": rate,
            "shop_name": shop.name if shop else p.shop,
            "disclosure": shop.disclosure if shop else None,
            "link": deal.affiliate_url or p.url,
            "rating": p.rating,
            "review_count": p.review_count,
            "shipping": p.shipping,
            "market_price": v.market_price,
            "below_market_pct": v.below_market_pct,
            "avg_price": v.avg_price,
            "below_avg_pct": v.below_avg_pct,
            "kind": p.deal_kind,
            "body": str(p.extra.get("info_body") or draft.get("text") or ""),
            "source_url": str(p.extra.get("post_url") or p.url),
            "info_links": list(p.extra.get("info_links") or draft.get("links") or []),
        }

    def build(self, items: list[QueueItem], *, when: datetime) -> list[CopyBlock]:
        """블로그 본문(길면 여러 조각) + 태그 한 줄. 첫 조각의 첫 줄이 제목."""
        deals = [self._ctx(it) for it in items]
        hot = [d for d in deals if d["kind"] != "info"]
        infos = [d for d in deals if d["kind"] == "info"] if self.cfg.include_info else []
        if not hot and not infos:
            return []
        top = sorted(
            [d for d in hot if d["has_price"]],
            key=lambda d: ((d["discount_rate"] or 0), (d["below_market_pct"] or 0)),
            reverse=True,
        )[:3]
        headline = " · ".join(_short(d["name"], 18) for d in (top or hot or infos)[:2])
        # 고지 문구는 제휴 링크가 붙는 딜의 쇼핑몰만 (정보 글의 원문 링크는 제휴 링크가 아니다)
        disclosures = list(dict.fromkeys(d["disclosure"] for d in hot if d["disclosure"]))
        date_label = f"{when.month}월 {when.day}일"
        text = self.renderer.render(
            self.cfg.template,
            autoescape=False,
            date_label=date_label,
            weekday=WEEKDAYS[when.weekday()],
            deals=hot,
            infos=infos,
            top=top,
            count=len(hot) + len(infos),
            headline=headline,
            disclosures=disclosures,
        )
        chunks = split_sections(text)
        blocks = [
            CopyBlock(
                key="blog_daily",
                name=f"네이버 블로그 — {date_label} 핫딜 정리" + (f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""),
                text=chunk,
            )
            for i, chunk in enumerate(chunks, 1)
        ]
        shops = list(dict.fromkeys(d["shop_name"] for d in hot))
        tags = BASE_TAGS + [f"{s}핫딜" for s in shops[:4]] + [f"{when.month}월핫딜"]
        blocks.append(CopyBlock(key="blog_tags", name="블로그 태그", text=", ".join(dict.fromkeys(t.replace(" ", "") for t in tags))))
        return blocks
