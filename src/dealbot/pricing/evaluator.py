"""특가 판정.

(a) 표시 할인율 >= min_discount_rate
(b) 최근 history_days 일 평균가 대비 min_below_average_pct % 이상 저렴
    (평균가는 최소 min_history_samples 회 이상 관측된 경우에만 사용)
"""

from __future__ import annotations

from dealbot.config import DealConfig
from dealbot.models import DealVerdict, PriceStats, Product


class DealEvaluator:
    def __init__(self, cfg: DealConfig) -> None:
        self.cfg = cfg

    def evaluate(self, product: Product, stats: PriceStats) -> DealVerdict:
        cfg = self.cfg
        reasons: list[str] = []

        if product.price < cfg.min_price:
            return DealVerdict(is_deal=False, reasons=["below_min_price"], sample_count=stats.count)

        lowered = product.name.lower()
        for kw in cfg.exclude_keywords:
            if kw and kw.lower() in lowered:
                return DealVerdict(is_deal=False, reasons=[f"excluded:{kw}"], sample_count=stats.count)

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

        score = max(discount or 0.0, below_avg_pct or 0.0)
        return DealVerdict(
            is_deal=rule_a or rule_b,
            reasons=reasons,
            discount_rate=discount,
            avg_price=round(stats.avg, 0) if stats.avg else None,
            below_avg_pct=below_avg_pct,
            sample_count=stats.count,
            score=round(score, 1),
        )
