"""특가 판정.

(a) 표시 할인율 >= min_discount_rate  (시중가 대조가 가능한 환경이면 (d) 확인이 있어야 통과)
(b) 최근 history_days 일 평균가 대비 min_below_average_pct % 이상 저렴
(c) 커뮤니티 추천 수 >= community_min_recommend  (가격 없는 쿠폰/이벤트는 이 규칙으로만)
(d) 시중가(쿠팡 검색) 대비 min_below_market_pct % 이상 저렴. veto_if_not_cheaper 면 쿠팡보다 비싼 딜은 탈락
"""

from __future__ import annotations

from dealbot.config import DealConfig
from dealbot.models import DealVerdict, PriceStats, Product
from dealbot.pricing.market import MarketQuote


class DealEvaluator:
    def __init__(self, cfg: DealConfig) -> None:
        self.cfg = cfg

    def _excluded(self, product: Product) -> str | None:
        lowered = f"{product.name} {product.headline or ''}".lower()
        for kw in self.cfg.exclude_keywords:
            if kw and kw.lower() in lowered:
                return f"excluded:{kw}"
        return None

    def _rule_c(self, product: Product) -> bool:
        return (
            self.cfg.community_min_recommend > 0
            and product.recommend_count is not None
            and product.recommend_count >= self.cfg.community_min_recommend
        )

    def evaluate(
        self,
        product: Product,
        stats: PriceStats,
        quote: MarketQuote | None = None,
        *,
        market_available: bool = False,
    ) -> DealVerdict:
        cfg = self.cfg
        mcfg = cfg.market
        reasons: list[str] = []

        excluded = self._excluded(product)
        if excluded:
            return DealVerdict(is_deal=False, reasons=[excluded], sample_count=stats.count)

        # 가격이 없는 글(쿠폰/이벤트): 추천 수로만 판정
        if product.deal_kind in ("coupon", "event") or not product.has_price:
            if not cfg.accept_coupons_and_events:
                return DealVerdict(is_deal=False, reasons=["no_price"], sample_count=stats.count)
            if self._rule_c(product):
                return DealVerdict(
                    is_deal=True,
                    reasons=[f"recommend>={cfg.community_min_recommend}"],
                    sample_count=stats.count,
                    score=float(min(product.recommend_count or 0, 100)),
                )
            return DealVerdict(is_deal=False, reasons=["no_price_low_recommend"], sample_count=stats.count)

        if product.price < cfg.min_price:
            return DealVerdict(is_deal=False, reasons=["below_min_price"], sample_count=stats.count)

        # (d) 시중가 대조
        below_market_pct: float | None = None
        rule_d = False
        market_worse = False
        if quote is not None and quote.price > 0:
            below_market_pct = round((1 - product.price / quote.price) * 100, 1)
            rule_d = below_market_pct >= mcfg.min_below_market_pct
            market_worse = below_market_pct <= 0

        discount = product.effective_discount_rate()
        rule_a = discount is not None and discount >= cfg.min_discount_rate
        if rule_a and market_available and mcfg.require_for_discount_rule and not rule_d:
            # 대조가 가능한데 시중가로 확인되지 않은 "표시 할인율" 은 믿지 않는다
            rule_a = False
            reasons.append("discount_unconfirmed")
        if rule_a:
            reasons.append(f"discount_rate>={cfg.min_discount_rate:g}%")

        below_avg_pct: float | None = None
        rule_b = False
        if stats.count >= cfg.min_history_samples and stats.avg and stats.avg > 0:
            below_avg_pct = round((1 - product.price / stats.avg) * 100, 1)
            rule_b = below_avg_pct >= cfg.min_below_average_pct
            if rule_b:
                reasons.append(f"below_{cfg.history_days}d_avg>={cfg.min_below_average_pct:g}%")

        rule_c = self._rule_c(product)
        if rule_c:
            reasons.append(f"recommend>={cfg.community_min_recommend}")
        if rule_d:
            reasons.append(f"below_{quote.source if quote else 'market'}_price>={mcfg.min_below_market_pct:g}%")

        is_deal = rule_a or rule_b or rule_c or rule_d
        if is_deal and market_worse and mcfg.veto_if_not_cheaper:
            is_deal = False
            reasons.append("above_market_price")

        score = max(
            discount if rule_a and discount else 0.0,
            below_avg_pct or 0.0,
            float(min(product.recommend_count or 0, 100)) if rule_c else 0.0,
            below_market_pct if rule_d and below_market_pct else 0.0,
        )
        return DealVerdict(
            is_deal=is_deal,
            reasons=reasons,
            discount_rate=discount,
            avg_price=round(stats.avg, 0) if stats.avg else None,
            below_avg_pct=below_avg_pct,
            sample_count=stats.count,
            score=round(score, 1),
            market_price=quote.price if quote else None,
            market_source=quote.source if quote else None,
            below_market_pct=below_market_pct,
        )
