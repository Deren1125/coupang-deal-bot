"""시중가 대조: 다른 몰의 딜 가격을 쿠팡 검색 API 에서 찾은 같은 상품 가격과 비교한다.

- 네이버 쇼핑 검색 API 는 2026-07 종료되어 쓸 수 없고, 쿠팡 검색 API 는 시간당 호출 제한이 빡빡하므로
  후보 딜(가격 조건/추천 조건을 통과한 것)에만, 시간당 max_checks_per_hour 번까지만 조회한다.
- 상품명 매칭은 토큰 기반: 수량 토큰(8개입, 2세트, 500ml …)은 반드시 일치, 나머지 토큰은 비율로 판단.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

from dealbot.config import MarketCheckConfig
from dealbot.coupang.client import CoupangClient, CoupangRateLimited, parse_api_product
from dealbot.models import Product

log = logging.getLogger(__name__)

_QTY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(개입|개|입|매|팩|세트|박스|병|캔|봉|포|정|kg|g|ml|l|리터|인치|cm|mm)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣]+")
_STOP = {"무료", "무료배송", "배송", "특가", "핫딜", "할인", "쿠폰", "행사", "정품", "국내", "당일", "로켓", "로켓배송", "와우", "세일", "이벤트", "추가", "택배", "본품", "신상", "new", "the", "x"}


@dataclass(slots=True)
class MarketQuote:
    price: int
    source: str
    title: str
    url: str | None = None
    checked_at: datetime | None = None


def normalize_qty(text: str) -> set[str]:
    out = set()
    for num, unit in _QTY_RE.findall(text.lower()):
        unit = {"개입": "개", "입": "개", "리터": "l"}.get(unit, unit)
        try:
            n = float(num)
            num_s = str(int(n)) if n.is_integer() else str(n)
        except ValueError:
            num_s = num
        out.add(f"{num_s}{unit}")
    return out


def tokens(text: str) -> list[str]:
    t = text.lower()
    t = re.sub(r"\[[^\]]*\]", " ", t)  # [쿠팡] 같은 태그 제거
    t = _QTY_RE.sub(" ", t)
    toks = [x for x in _TOKEN_RE.findall(t) if len(x) >= 2 and x not in _STOP and not x.isdigit()]
    seen: list[str] = []
    for x in toks:
        if x not in seen:
            seen.append(x)
    return seen


def build_keyword(name: str, max_tokens: int = 5) -> str:
    qty = sorted(normalize_qty(name))
    core = tokens(name)[:max_tokens]
    return " ".join(core + qty[:1]).strip() or name[:40]


def match_ratio(deal_name: str, candidate_title: str) -> float:
    """0~1. 수량 토큰이 있는데 후보에 없으면 0."""
    deal_qty = normalize_qty(deal_name)
    cand_text = candidate_title.lower()
    cand_qty = normalize_qty(candidate_title)
    if deal_qty and not deal_qty <= cand_qty:
        return 0.0
    deal_tokens = tokens(deal_name)
    if not deal_tokens:
        return 0.0
    hit = sum(1 for t in deal_tokens if t in cand_text)
    return hit / len(deal_tokens)


class CoupangMarketReference:
    source = "coupang"

    def __init__(self, client: CoupangClient, cfg: MarketCheckConfig) -> None:
        self.client = client
        self.cfg = cfg
        self._checks: deque[float] = deque()

    def budget_available(self) -> bool:
        now = time.monotonic()
        while self._checks and now - self._checks[0] > 3600:
            self._checks.popleft()
        return len(self._checks) < max(0, self.cfg.max_checks_per_hour)

    def pick_best(self, product: Product, candidates: list[Product]) -> MarketQuote | None:
        best: MarketQuote | None = None
        for c in candidates:
            if c.product_id == product.product_id:
                continue
            ratio = match_ratio(product.name, c.name)
            if ratio < self.cfg.min_token_match:
                continue
            if best is None or c.price < best.price:
                best = MarketQuote(price=c.price, source=self.source, title=c.name, url=c.affiliate_url or c.url)
        return best

    async def lookup(self, product: Product) -> MarketQuote | None:
        if product.shop == "coupang" or not product.has_price:
            return None
        if not self.budget_available():
            log.info("market check budget exhausted — skipping %s", product.name[:40])
            return None
        keyword = build_keyword(product.name)
        self._checks.append(time.monotonic())
        try:
            raw = await self.client.search(keyword, limit=10)
        except CoupangRateLimited as e:
            log.info("market check skipped (coupang budget): %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("market check failed for '%s': %s", keyword, e)
            return None
        candidates = [p for p in (parse_api_product(x, "market") for x in raw) if p]
        quote = self.pick_best(product, candidates)
        if quote:
            log.info("market ref for '%s' → %s원 (%s)", product.name[:40], f"{quote.price:,}", quote.title[:40])
        else:
            log.info("market ref: no match for '%s' (%d candidates)", keyword, len(candidates))
        return quote
