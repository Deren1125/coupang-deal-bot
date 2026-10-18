"""'할인 괜찮은 딜' (주인 10/18 — "할인율이 괜찮은 애들은 플랫폼 무관하게 다 올려. 너무 남발하지만 않으면 돼").

강조 단계가 top·must(TemplateRenderer.deal_tier — 평소 가격 근거 대비 %)이거나 할인율이 publish.good_deal_min_discount_pct(기본 30) 이상인
상품 딜. 할인율은 평소 가격 근거(시중가·믿을 만한 평균) 대비 % 와 표시 할인율(판정·상품의 할인율) 중 큰 값.
이 딜은 인스타 딜 하나짜리 글·스레드에 가벼운 간격(안전 한도)만으로 올라가고, 아닌 딜은 예전 규칙 그대로다
(스레드 45분 간격·하루 10개 안, 인스타는 릴스·TOP5 에만). 글에 쓰는 말·고지 검사는 그대로 (이 판정은 '올릴지'만 정한다).
"""

from __future__ import annotations

from typing import Any

from dealbot.models import Deal

STRONG_TIERS = ("top", "must")


def good_deal_reason(deal: Deal, renderer: Any, min_discount_pct: float) -> str | None:
    """괜찮은 딜이면 그 이유('tier top 72%' · 'discount 35%'), 아니면 None. 정보 글·가격 없는 글은 아님."""
    p = deal.product
    if p.deal_kind == "info" or not p.has_price:
        return None
    pct = 0.0
    if renderer is not None:
        tier, pct = renderer.deal_tier(deal)
        if tier in STRONG_TIERS:
            return f"tier {tier} {pct:.0f}%"
    if min_discount_pct <= 0:
        return "all deals (good_deal_min_discount_pct 0)"
    rates = [r for r in (pct, deal.verdict.discount_rate, p.effective_discount_rate()) if r is not None]
    best = max(rates) if rates else 0.0
    if best >= min_discount_pct:
        return f"discount {best:.0f}%"
    return None


def is_good_deal(bot: Any, deal: Deal) -> bool:
    """bot(DealBot)의 설정·글 렌더러로 판정."""
    return good_deal_reason(deal, getattr(bot, "renderer", None), bot.settings.publish.good_deal_min_discount_pct) is not None
