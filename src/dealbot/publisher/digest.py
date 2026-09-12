"""하루치 발행 딜을 모아 블로그 글 한 편으로 만든다 (복붙용). 매일 밤 정해진 시각에 관리자 챗으로 보낸다.

양식은 Deren 블로그 규칙을 따른다: 도입부 고정 2줄 → 채널 초대 블록 → 주제 도입 → 딜 단락 → 주의(느낌표 없이) →
마무리 → 대가성·면책 → 서명블록. 제목은 본문과 따로 후보 3개를 만든다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dealbot.config import BlogDigestConfig
from dealbot.publisher.copyblocks import CopyBlock
from dealbot.publisher.templates import TemplateRenderer, format_won
from dealbot.shops import ShopRegistry
from dealbot.storage.db import QueueItem

log = logging.getLogger(__name__)

WEEKDAYS = "월화수목금토일"
CHUNK_LIMIT = 3000  # 텔레그램 한 메시지(4096자) 안에 <pre> 째로 안전하게 들어가는 길이
BASE_TAGS = ["핫딜", "오늘의핫딜", "특가", "할인정보", "쇼핑핫딜", "가성비", "핫딜정보", "쿠팡핫딜"]
# 영문 마지막 글자를 한국어로 읽었을 때 받침이 있는 것 (L→엘, M→엠, N→엔, R→알)
_LATIN_JONG = set("lmnr")
_DIGIT_JONG = set("013678")  # 영·일·삼·육·칠·팔


@dataclass(slots=True)
class Digest:
    titles: list[str] = field(default_factory=list)
    blocks: list[CopyBlock] = field(default_factory=list)


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def has_jongseong(word: str) -> bool | None:
    """마지막 글자에 받침이 있는가. 판단할 수 없으면 None."""
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isdigit():
            return ch in _DIGIT_JONG
        if ch.isascii() and ch.isalpha():
            return ch.lower() in _LATIN_JONG
    return None


def particle(word: str, with_jong: str, without_jong: str) -> str:
    j = has_jongseong(word)
    if j is None:
        return f"{with_jong}({without_jong})"
    return with_jong if j else without_jong


def split_sections(text: str, limit: int = CHUNK_LIMIT) -> list[str]:
    """긴 글을 번호 단락(빈 줄 + 'N. ') 경계에서 나눈다. 단락 하나가 너무 길면 줄 단위로."""
    if len(text) <= limit:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        piece = para
        if len(current) + len(piece) + 2 > limit and current:
            chunks.append(current.rstrip())
            current = ""
        while len(piece) > limit:
            cut = piece.rfind("\n", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append((current + piece[:cut]).rstrip())
            current, piece = "", piece[cut:].lstrip("\n")
        current = piece if not current else current + "\n\n" + piece
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
        body = str(p.extra.get("info_body") or draft.get("text") or "")
        body_lines = [ln[2:] if ln.startswith("· ") else ln for ln in body.splitlines() if ln.strip()]
        name = " ".join(p.name.split())
        return {
            "name": name,
            "name_ga": name + particle(name, "이", "가"),
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
            "body_lines": body_lines,
            "source_url": str(p.extra.get("post_url") or p.url),
            "info_links": list(p.extra.get("info_links") or draft.get("links") or []),
            "score": item.score,
        }

    @staticmethod
    def _titles(deals: list[dict[str, Any]], infos: list[dict[str, Any]], date_label: str, weekday: str) -> list[str]:
        """Deren 블로그 제목 패턴대로 후보 3개: 정보형 / 놀람형(또는 선언형) / 모음형."""
        n = len(deals) + len(infos)
        out: list[str] = []
        top = deals[0] if deals else None
        if top is not None:
            short = _short(top["name"], 22)
            rate = top["discount_rate"] or 0
            tail = f" {rate:.0f}% 할인" if rate else ""
            rest = f" 외 {n - 1}건" if n > 1 else ""
            out.append(f"{date_label} 오늘의 핫딜 총정리 ({short}{tail}{rest})")
            price = format_won(top["price"])
            if rate >= 40 or (top["below_market_pct"] or 0) >= 30:
                out.append(f"{short}{particle(short, '이', '가')} {price}이라구요?? ({date_label} 핫딜 모음)")
            else:
                out.append(f"{top['shop_name']}에서 {short}{particle(short, '을', '를')} {price}에 팝니다! ({date_label} 핫딜 모음)")
        elif infos:
            out.append(f"{date_label} 세일·이벤트 소식 모음 ({_short(infos[0]['name'], 22)}{' 외 ' + str(n - 1) + '건' if n > 1 else ''})")
        names = ", ".join(_short(d["name"], 16) for d in (deals + infos)[:2])
        out.append(f"{date_label}({weekday}) 핫딜 {n}건 모음 — {names}")
        return out[:3]

    def build(self, items: list[QueueItem], *, when: datetime) -> Digest:
        """점수 높은 순으로 고른 딜 → 블로그 본문(길면 여러 조각) + 태그 한 줄 + 제목 후보 3개."""
        ranked = sorted(items, key=lambda it: it.score, reverse=True)
        if self.cfg.max_items > 0:
            ranked = ranked[: self.cfg.max_items]
        deals_all = [self._ctx(it) for it in ranked]
        hot = [d for d in deals_all if d["kind"] != "info"]
        infos = [d for d in deals_all if d["kind"] == "info"] if self.cfg.include_info else []
        if not hot and not infos:
            return Digest()
        disclosures = list(dict.fromkeys(d["disclosure"] for d in hot if d["disclosure"]))
        date_label = f"{when.month}월 {when.day}일"
        weekday = WEEKDAYS[when.weekday()]
        text = self.renderer.render(
            self.cfg.template,
            autoescape=False,
            blog_name=self.cfg.blog_name,
            author=self.cfg.author,
            date_label=date_label,
            weekday=weekday,
            deals=hot,
            infos=infos,
            count=len(hot) + len(infos),
            disclosures=disclosures,
        )
        chunks = split_sections(text)
        blocks = [
            CopyBlock(
                key="blog_daily",
                name=f"네이버 블로그 본문 — {date_label} 핫딜 정리" + (f" ({i}/{len(chunks)})" if len(chunks) > 1 else ""),
                text=chunk,
            )
            for i, chunk in enumerate(chunks, 1)
        ]
        shops = list(dict.fromkeys(d["shop_name"] for d in hot))
        tags = BASE_TAGS + [f"{s}핫딜" for s in shops[:4]] + [f"{when.month}월핫딜"]
        blocks.append(CopyBlock(key="blog_tags", name="블로그 태그", text=", ".join(dict.fromkeys(t.replace(" ", "") for t in tags))))
        return Digest(titles=self._titles(hot, infos, date_label, weekday), blocks=blocks)
