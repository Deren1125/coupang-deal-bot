"""특가 판정.

0) 관심도 게이트: 추천/댓글/조회수/순위 중 하나라도 기준을 넘는 딜만 판정 (전 상품 대조는 불가능하므로)
1) (d) 시중가 대조가 기본: 쿠팡 검색으로 찾은 같은 상품보다 min_below_market_pct % 이상 싸면 특가,
   쿠팡보다 싸지 않으면 탈락 (veto)
2) 대조 결과가 없을 때의 보조 규칙:
   (b) 최근 history_days 일 평균가 대비 min_below_average_pct % 이상 저렴
   (c) 커뮤니티 추천 수 >= community_min_recommend   (가격 없는 쿠폰/이벤트는 이 규칙으로만)
   (a) 표시 할인율 >= min_discount_rate — 서브 신호. 대조가 가능한 환경이면 (d) 확인 없이는 통과 못 함
"""

from __future__ import annotations

from dealbot.config import DealConfig, SourceRule
from dealbot.models import DealVerdict, PriceStats, Product
from dealbot.pricing.market import MarketQuote


class DealEvaluator:
    def __init__(self, cfg: DealConfig) -> None:
        self.cfg = cfg

    def _rule(self, product: Product) -> SourceRule:
        return self.cfg.per_source.get(product.source, SourceRule())

    def min_recommend_for(self, product: Product) -> int:
        """이 딜에 적용할 (c) 추천 수 기준.
        가격이 없는 글(쿠폰/이벤트)은 별도의 높은 기준 — 추천이 몰리는 '공짜 이벤트'를 걸러내기 위함."""
        rule = self._rule(product)
        if product.deal_kind in ("coupon", "event") or not product.has_price:
            override = rule.coupon_min_recommend
            return self.cfg.coupon_min_recommend if override is None else override
        override = rule.community_min_recommend
        return self.cfg.community_min_recommend if override is None else override

    def _excluded(self, product: Product) -> str | None:
        lowered = f"{product.name} {product.headline or ''}".lower()
        for kw in self.cfg.exclude_keywords:
            if kw and kw.lower() in lowered:
                return f"excluded:{kw}"
        return None

    def _rule_c(self, product: Product) -> bool:
        threshold = self.min_recommend_for(product)
        return threshold > 0 and product.recommend_count is not None and product.recommend_count >= threshold

    def interest_signal(self, product: Product) -> str | None:
        """관심도 게이트를 통과시킨 신호 이름. 통과 못 하면 None."""
        ic = self.cfg.interest
        if not ic.enabled or product.source in ic.always_pass_sources:
            return "always"
        rule = self._rule(product)
        min_rec = ic.min_recommend if rule.interest_min_recommend is None else rule.interest_min_recommend
        min_com = ic.min_comments if rule.interest_min_comments is None else rule.interest_min_comments
        min_views = ic.min_views if rule.interest_min_views is None else rule.interest_min_views
        if product.recommend_count is not None and min_rec > 0 and product.recommend_count >= min_rec:
            return f"recommend>={min_rec}"
        if product.comment_count is not None and min_com > 0 and product.comment_count >= min_com:
            return f"comments>={min_com}"
        if product.view_count is not None and min_views > 0 and product.view_count >= min_views:
            return f"views>={min_views}"
        if product.rank is not None and ic.max_rank > 0 and product.rank <= ic.max_rank:
            return f"rank<={ic.max_rank}"
        return None

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

        signal = self.interest_signal(product)
        if signal is None:
            return DealVerdict(is_deal=False, reasons=["low_interest"], sample_count=stats.count)
        if signal != "always":
            reasons.append(f"interest:{signal}")

        # 가격이 없는 글(쿠폰/이벤트/공지) 또는 제목이 쿠폰/이벤트로 보이는 글
        if product.deal_kind in ("coupon", "event") or not product.has_price:
            if cfg.accept_coupons_and_events:
                # 추천 수로만 판정
                if self._rule_c(product):
                    return DealVerdict(
                        is_deal=True,
                        reasons=reasons + [f"recommend>={self.min_recommend_for(product)}"],
                        sample_count=stats.count,
                        score=float(min(product.recommend_count or 0, 100)),
                    )
                return DealVerdict(is_deal=False, reasons=reasons + ["no_price_low_recommend"], sample_count=stats.count)
            if not product.has_price:
                # 이벤트/쿠폰 발행 끔: 가격이 없어 싼지 판단할 수 없는 글은 제외
                return DealVerdict(is_deal=False, reasons=reasons + ["no_price"], sample_count=stats.count)
            # 가격이 적힌 딜은 제목에 '이벤트/증정' 이 있어도 아래 가격 기준으로 판정

        if product.price < cfg.min_price:
            return DealVerdict(is_deal=False, reasons=reasons + ["below_min_price"], sample_count=stats.count)

        discount = product.effective_discount_rate()
        below_avg_pct: float | None = None
        if stats.count >= cfg.min_history_samples and stats.avg and stats.avg > 0:
            below_avg_pct = round((1 - product.price / stats.avg) * 100, 1)

        # ---- (d) 시중가 대조: 결과가 있으면 이것이 결정한다
        below_market_pct: float | None = None
        if quote is not None and quote.price > 0:
            below_market_pct = round((1 - product.price / quote.price) * 100, 1)
            if below_market_pct >= mcfg.min_below_market_pct:
                reasons.append(f"below_{quote.source}_price>={mcfg.min_below_market_pct:g}%")
                return DealVerdict(
                    is_deal=True, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, score=round(below_market_pct, 1),
                    market_price=quote.price, market_source=quote.source, below_market_pct=below_market_pct,
                )
            if mcfg.strict:
                # 대조 결과가 있으면 기준 미만은 무조건 탈락 (보조 규칙으로 안 넘어감)
                reasons.append("above_market_price" if below_market_pct <= 0 else f"below_{quote.source}_price<{mcfg.min_below_market_pct:g}%")
                return DealVerdict(
                    is_deal=False, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, market_price=quote.price, market_source=quote.source,
                    below_market_pct=below_market_pct,
                )
            if mcfg.veto_if_not_cheaper and below_market_pct <= 0:
                reasons.append("above_market_price")
                return DealVerdict(
                    is_deal=False, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, market_price=quote.price, market_source=quote.source,
                    below_market_pct=below_market_pct,
                )
            reasons.append(f"market_diff={below_market_pct:g}%")  # 0~N% 사이: 보조 규칙으로 넘어감

        # ---- 보조 규칙
        rule_b = below_avg_pct is not None and below_avg_pct >= cfg.min_below_average_pct
        if rule_b:
            reasons.append(f"below_{cfg.history_days}d_avg>={cfg.min_below_average_pct:g}%")

        rule_c = self._rule_c(product)
        if rule_c:
            reasons.append(f"recommend>={self.min_recommend_for(product)}")

        rule_a = discount is not None and discount >= cfg.min_discount_rate
        if rule_a and market_available and mcfg.require_for_discount_rule:
            rule_a = False
            reasons.append("discount_unconfirmed")
        if rule_a:
            reasons.append(f"discount_rate>={cfg.min_discount_rate:g}%")

        score = max(
            below_avg_pct if rule_b and below_avg_pct else 0.0,
            float(min(product.recommend_count or 0, 100)) if rule_c else 0.0,
            (discount or 0.0) * 0.5 if rule_a else 0.0,  # 서브 신호라 절반 가중
        )
        return DealVerdict(
            is_deal=rule_b or rule_c or rule_a,
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
