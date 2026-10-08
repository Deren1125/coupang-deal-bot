"""특가 판정.

0) 관심도 게이트: 추천/댓글/조회수/순위 중 하나라도 기준을 넘는 딜만 판정 (전 상품 대조는 불가능하므로)
1) (d) 시중가 대조가 기본: 쿠팡 검색으로 찾은 같은 상품보다 min_below_market_pct % 이상 싸면 특가,
   쿠팡보다 싸지 않으면 탈락 (veto). 딜 쪽은 배송비까지 더한 값으로 비교하고, 배송비를 모르면(착불·조건부) 대조로 통과시키지 않음
2) 대조 결과가 없을 때의 보조 규칙:
   (b) 최근 history_days 일 평균가 대비 min_below_average_pct % 이상 저렴
   (c) 커뮤니티 추천 수 >= community_min_recommend   (가격 없는 쿠폰/이벤트는 이 규칙으로만)
   (a) 표시 할인율 >= min_discount_rate — 서브 신호. 대조가 가능한 환경이면 (d) 확인 없이는 통과 못 함
"""

from __future__ import annotations

import dataclasses
import re

from dealbot.config import DealConfig, SourceRule
from dealbot.models import DealVerdict, PriceStats, Product
from dealbot.pricing.market import MarketQuote

# ---- 배송비: 뽐뿌 제목 '(6,900원/3,500원)' 의 뒤쪽 원문(Product.shipping)을 원 단위로
_SHIP_FREE_WORDS = ("무료", "무배", "free")
_SHIP_PAID_WORDS = ("착불", "별도", "유료", "배송비", "택배비", "선불")  # 돈을 내는데 금액이 안 적힌 경우
_SHIP_THRESHOLD_RE = re.compile(r"(\d+(?:\.\d+)?)만원?(?:이상|↑)|(\d{1,3}(?:,\d{3})+|\d{4,})원?(?:이상|↑)")  # '3만↑무료'
_SHIP_FEE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(천)?")


def shipping_fee(product: Product) -> int | None:
    """딜의 배송비(원). 무료이거나 적힌 게 없으면 0, 착불·조건부처럼 금액을 알 수 없으면 None.
    '2,500(3만↑무료)' 같은 조건부 무료는 상품가가 조건을 넘을 때만 0 (뽐뿌 수집기는 '무료' 글자만 보고
    is_free_shipping 을 켜므로 그 값보다 원문을 먼저 본다)."""
    s = (product.shipping or "").replace(" ", "").lower()
    if not s:
        return 0
    threshold = None
    m = _SHIP_THRESHOLD_RE.search(s)
    if m:
        threshold = int(float(m.group(1)) * 10000) if m.group(1) else int(m.group(2).replace(",", ""))
        if product.price >= threshold:
            return 0
        s = s[: m.start()] + s[m.end() :]
    head = re.split(r"[(/]", s)[0]  # '무료(제주 3,000원)', '무료/카드할인' 은 앞쪽만
    fee_m = _SHIP_FEE_RE.search(head)
    if fee_m:
        fee = int(float(fee_m.group(1).replace(",", "")) * (1000 if fee_m.group(2) else 1))
        if fee == 0:
            return 0
        return fee if fee >= 100 else None  # '3만원' 처럼 못 읽은 금액은 모르는 것으로
    if threshold is not None or "조건" in s:
        return None  # 조건부 무료인데 조건을 못 넘었거나 조건을 모름
    if any(w in head for w in _SHIP_FREE_WORDS):
        return 0
    if any(w in s for w in _SHIP_PAID_WORDS):
        return None
    return 0  # '로켓배송', '카드할인' 처럼 배송비 얘기가 아닌 글 (예전처럼 상품가로 비교)


# ---- 식품 판정 보강 (설정의 keywords / non_food_keywords 는 그대로 쓰고, 그 위에 덧붙이는 규칙)
# 한 글자 식품 낱말 중 다른 낱말 속에 흔히 들어가는 것은 낱말 '끝'에 올 때만 식품으로 본다
# (비빔면·전복죽·양배추즙 O / 면도기·면봉·착즙기 X). 끝에 와도 식품이 아닌 낱말은 뺀다.
# 값: (그 글자 하나만 쓴 낱말도 식품인가, 식품이 아닌 낱말 패턴). 수면·장면은 낱말 전체일 때만 (칼국수면·짜장면은 식품)
_WORD_END_FOOD: dict[str, tuple[bool, re.Pattern[str] | None]] = {
    # '면' 하나만 쓴 낱말은 대개 옷감('면 100%')
    "면": (False, re.compile(r"(?:화|양|단|측|평|곡|표|순|앞|뒷|옆|윗|밑|겉|대)면$|^(?:전|후|정|이|내|수|지|장|안|세|가)면$")),
    "죽": (True, re.compile(r"가죽$|^폭죽$")),
    "즙": (True, None),
}
# 낱말 그대로 찾으면 엉뚱한 데 걸리는 것: '다시 입고' 의 다시 ≠ 다시마·다시팩
_FOOD_WORD_RE = {"다시": re.compile(r"다시(?:마|팩|백)|(?<=[가-힣])다시(?![가-힣])")}
_HANGUL_WORD_RE = re.compile(r"[가-힣]+")
# 식품 낱말(커피·냉동·면·즙…)이 들어 있지만 식품이 아닌 물건: 있으면 식품 아님
_NON_FOOD_EXTRA = (
    "면도기", "면봉", "화장솜", "냉동고", "냉동실", "냉장고", "착즙기", "원액기", "머신", "그라인더", "드리퍼", "커피포트", "커피잔",
    "텀블러", "머그", "모니터", "지갑", "앰플", "부탄",
)
# 상품명에 식품 낱말이 없는 음료 (예: 뽐뿌 제목 '할리스 바닐라 딜라이트 로우슈거, 24개')
_FOOD_EXTRA = ("캔커피", "아메리카노", "콜드브루", "로우슈거", "할리스", "맥심", "칸타타", "조지아", "바리스타룰스", "펩시")
_DRINK_COUNT_RE = re.compile(r"\d+\s*(?:캔|병|펫|페트)(?![가-힣])")  # '24캔', '40병'
_SIZE_COMMA_RE = re.compile(r"(?<=[a-z가-힣])\s*,\s*(?=\d)")  # '500ml, 40병' → '500ml 40병' (규격 패턴이 읽도록)


def _food_word_hit(word: str, text: str) -> bool:
    """설정 keywords 의 낱말 하나가 text 에서 식품 뜻으로 쓰였는가."""
    rule = _WORD_END_FOOD.get(word)
    if rule is not None:
        alone_ok, not_food = rule
        return any(
            w.endswith(word) and (alone_ok or w != word) and not (not_food and not_food.search(w))
            for w in _HANGUL_WORD_RE.findall(text)
        )
    rx = _FOOD_WORD_RE.get(word)
    if rx is not None:
        return bool(rx.search(text))
    return word in text


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

    def is_food(self, product: Product) -> bool:
        """식품류인가: 상품 분류(쿠팡 categoryName·루리웹 말머리)나 상품명의 낱말로 판단."""
        fc = self.cfg.food
        if not fc.enabled:
            return False
        text = f"{product.name} {product.headline or ''}".lower()
        if any(k and k.lower() in text for k in (*fc.non_food_keywords, *_NON_FOOD_EXTRA)):
            return False
        cat = (product.category or "").lower()
        if cat and any(c.lower() in cat for c in fc.categories):
            return True
        if any(k and _food_word_hit(k.lower(), text) for k in fc.keywords):
            return True
        if any(k in text for k in _FOOD_EXTRA) or _DRINK_COUNT_RE.search(text):
            return True
        sized = _SIZE_COMMA_RE.sub(" ", text)
        return any(re.search(pat, sized, re.I) for pat in fc.unit_patterns)

    @staticmethod
    def _with_quote_title(product: Product, quote: MarketQuote | None) -> Product:
        """식품 판정용: 대조한 쿠팡 상품 이름('…, 285ml, 24개')도 같이 보게 머리글에 붙인 사본."""
        if quote is None or not quote.title:
            return product
        return dataclasses.replace(product, headline=f"{product.headline or ''} {quote.title}".strip())

    def evaluate(
        self,
        product: Product,
        stats: PriceStats,
        quote: MarketQuote | None = None,
        *,
        market_available: bool = False,
    ) -> DealVerdict:
        verdict = self._evaluate(product, stats, quote, market_available=market_available)
        # 글에 쓰는 가격 근거: 기록된 기간의 최저가와 기록 일수
        verdict.low_price = stats.min
        if stats.first_seen_at is not None:
            from dealbot.utils.timeutil import utcnow

            verdict.history_days = round((utcnow() - stats.first_seen_at).total_seconds() / 86400, 1)
        fc = self.cfg.food
        if verdict.is_deal and self.is_food(self._with_quote_title(product, quote)):
            # 식품은 평소 가격 대비 확실히 쌀 때만 (표시 할인율·추천 수만으로는 안 됨)
            ref = max(verdict.below_avg_pct or 0.0, verdict.below_market_pct or 0.0)
            if ref < fc.min_below_reference_pct:
                verdict.is_deal = False
                verdict.reasons.append(f"food_below_ref<{fc.min_below_reference_pct:g}%")
        return verdict

    def _evaluate(
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

        q = cfg.quality
        if (
            q.enabled
            and product.review_count is not None
            and product.review_count < q.min_review_count
            and (not q.shops or product.shop in q.shops)
        ):
            return DealVerdict(is_deal=False, reasons=reasons + [f"few_reviews<{q.min_review_count}"], sample_count=stats.count)

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
        span_ok = True
        if cfg.min_history_days and stats.first_seen_at is not None:
            from dealbot.utils.timeutil import utcnow

            span_ok = (utcnow() - stats.first_seen_at).total_seconds() >= cfg.min_history_days * 86400
        elif cfg.min_history_days:
            span_ok = False
        if stats.count >= cfg.min_history_samples and stats.avg and stats.avg > 0 and span_ok:
            below_avg_pct = round((1 - product.price / stats.avg) * 100, 1)

        # ---- (d) 시중가 대조: 결과가 있으면 이것이 결정한다
        below_market_pct: float | None = None
        market_title = (quote.title or None) if quote is not None else None
        if quote is not None and quote.price > 0:
            # 딜은 배송비까지 낸 값으로 비교 (6,900원 + 배송비 3,500원은 쿠팡 9,900원보다 비쌈).
            # 쿠팡 쪽 배송비는 더하지 않음: 대부분 로켓·무료배송이고, 유료여도 우리 딜이 덜 싸 보일 뿐이라 안전한 쪽
            fee = shipping_fee(product)
            below_market_pct = round((1 - (product.price + (fee or 0)) / quote.price) * 100, 1)
            # 배송비를 몰라(착불·조건부) 기준을 넘는지 확정할 수 없음 → 대조로는 통과 못 하고, 글에 '쿠팡보다 N%↓'도 안 씀
            fee_unknown = fee is None and below_market_pct >= mcfg.min_below_market_pct
            if fee_unknown:
                reasons.append("market_shipping_unknown")
            elif below_market_pct >= mcfg.min_below_market_pct:
                reasons.append(f"below_{quote.source}_price>={mcfg.min_below_market_pct:g}%")
                return DealVerdict(
                    is_deal=True, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, score=round(below_market_pct, 1),
                    market_price=quote.price, market_source=quote.source, below_market_pct=below_market_pct,
                    market_title=market_title,
                )
            if mcfg.strict:
                # 대조 결과가 있으면 기준 미만은 무조건 탈락 (보조 규칙으로 안 넘어감)
                if not fee_unknown:
                    reasons.append("above_market_price" if below_market_pct <= 0 else f"below_{quote.source}_price<{mcfg.min_below_market_pct:g}%")
                return DealVerdict(
                    is_deal=False, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, market_price=quote.price, market_source=quote.source,
                    below_market_pct=None if fee_unknown else below_market_pct, market_title=market_title,
                )
            if mcfg.veto_if_not_cheaper and below_market_pct <= 0:
                reasons.append("above_market_price")
                return DealVerdict(
                    is_deal=False, reasons=reasons, discount_rate=discount,
                    avg_price=round(stats.avg, 0) if stats.avg else None, below_avg_pct=below_avg_pct,
                    sample_count=stats.count, market_price=quote.price, market_source=quote.source,
                    below_market_pct=below_market_pct, market_title=market_title,
                )
            if not fee_unknown:
                reasons.append(f"market_diff={below_market_pct:g}%")  # 0~N% 사이: 보조 규칙으로 넘어감
            if fee is None:
                below_market_pct = None  # 배송비를 모르면 몇 % 싼지도 모름 → 글에 쿠팡 대비 % 를 쓰지 않게

        # ---- 보조 규칙
        rule_b = below_avg_pct is not None and below_avg_pct >= cfg.min_below_average_pct
        if rule_b and cfg.near_low_pct is not None and stats.min:
            if product.price > stats.min * (1 + cfg.near_low_pct / 100):
                rule_b = False  # 평균보다는 싸도 최근 최저가보다 비싸면 '지금이 특가'는 아님
                reasons.append(f"above_{cfg.history_days}d_low+{cfg.near_low_pct:g}%")
        if rule_b:
            reasons.append(f"below_{cfg.history_days}d_avg>={cfg.min_below_average_pct:g}%")

        rule_c = self._rule_c(product)
        if rule_c:
            reasons.append(f"recommend>={self.min_recommend_for(product)}")

        rule = self._rule(product)
        min_disc = cfg.min_discount_rate if rule.min_discount_rate is None else rule.min_discount_rate
        discount_alone = cfg.discount_alone if rule.discount_alone is None else rule.discount_alone
        rule_a = discount is not None and discount >= min_disc
        if rule_a and market_available and mcfg.require_for_discount_rule:
            rule_a = False
            reasons.append("discount_unconfirmed")
        if rule_a:
            reasons.append(f"discount_rate>={min_disc:g}%")

        # 추천 수·표시 할인율은 '가격 근거'가 아니다 → 설정에 따라 단독 통과를 막는다
        strong = cfg.community_strong_recommend > 0 and (product.recommend_count or 0) >= cfg.community_strong_recommend
        support = rule_a or (below_avg_pct is not None and below_avg_pct >= cfg.support_below_avg_pct)
        pass_c = rule_c and (strong or not cfg.recommend_needs_support or support)
        if rule_c and not pass_c:
            reasons.append("recommend_without_price_support")
        pass_a = rule_a and (discount_alone or rule_b or pass_c)
        if rule_a and not pass_a:
            reasons.append("discount_alone_not_trusted")

        score = max(
            below_avg_pct if rule_b and below_avg_pct else 0.0,
            float(min(product.recommend_count or 0, 100)) if rule_c else 0.0,
            (discount or 0.0) * 0.5 if rule_a else 0.0,  # 서브 신호라 절반 가중
        )
        return DealVerdict(
            is_deal=rule_b or pass_c or pass_a,
            reasons=reasons,
            discount_rate=discount,
            avg_price=round(stats.avg, 0) if stats.avg else None,
            below_avg_pct=below_avg_pct,
            sample_count=stats.count,
            score=round(score, 1),
            market_price=quote.price if quote else None,
            market_source=quote.source if quote else None,
            below_market_pct=below_market_pct,
            market_title=market_title,
        )
