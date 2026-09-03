"""특가 판정.

(a) 표시 할인율 >= min_discount_rate
(b) 최근 history_days 일 평균가 대비 min_below_average_pct % 이상 저렴
    (평균가는 최소 min_history_samples 회 이상 관측된 경우에만 사용)
(c) 커뮤니티 추천 수 >= community_min_recommend  (가격 정보가 없는 쿠폰/이벤트는 이 규칙으로만 판정)
"""

from __future__ import annotations

from dealbot.config import DealConfig
from dealbot.models import DealVerdict, PriceStats, Product


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

    def evaluate(self, product: Product, stats: PriceStats) -> DealVerdict:
        cfg = self.cfg
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

        discount = product.effective_discount_rate()
        rule_a = discount is not None and discount >= cfg.min_discount_rate
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

        score = max(discount or 0.0, below_avg_pct or 0.0, float(min(product.recommend_count or 0, 100)) if rule_c else 0.0)
        return DealVerdict(
            is_deal=rule_a or rule_b or rule_c,
            reasons=reasons,
            discount_rate=discount,
            avg_price=round(stats.avg, 0) if stats.avg else None,
            below_avg_pct=below_avg_pct,
            sample_count=stats.count,
            score=round(score, 1),
        )
