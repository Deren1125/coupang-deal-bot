"""시중가 대조: 다른 몰의 딜 가격을 쿠팡 검색 API 에서 찾은 같은 상품 가격과 비교한다.

- 네이버 쇼핑 검색 API 는 2026-07 종료되어 쓸 수 없고, 쿠팡 검색 API 는 시간당 호출 제한이 빡빡하므로
  후보 딜(가격 조건/추천 조건을 통과한 것)에만, 시간당 max_checks_per_hour 번까지만 조회한다.
- 상품명 매칭은 토큰 기반: 수량(8개입, 2세트, 500ml …)은 반드시 같아야 하고, 나머지 토큰은 비율로 판단.
  수량은 '총 몇 개인가'로 비교한다 (40병 = 40개, 24개 x 2박스 = 48개, 1+1 = 2개). 묶음·대용량 상품을
  '쿠팡 최저가'로 잡으면 '쿠팡보다 N%↓'가 부풀려지기 때문.
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

# 영문 단위 뒤에 영문이 이어지면 단위가 아님 ('128GB'). 단 x 는 곱하기 기호라 허용 ('2Lx6'). '3개월'은 개수가 아님
_QTY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(개입|개(?!월)|입|매|팩|세트|박스|병|캔|봉지|봉|포|정|리터|인치|(?:ea|kg|g|ml|l|cm|mm)(?![a-wyz]))",
    re.IGNORECASE,
)
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # '1,000ml' → '1000ml' (안 지우면 '0ml' 로 읽힘)
_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣]+")
# 낱개 단위는 모두 '개'로 본다 (뽐뿌 제목 '40병' = 쿠팡 표준 이름 '40개')
_COUNT_UNITS = {"개", "개입", "입", "병", "캔", "봉", "봉지", "포", "정", "매", "ea"}
_SIZE_UNITS = {"ml": ("ml", 1), "l": ("ml", 1000), "리터": ("ml", 1000), "g": ("g", 1), "kg": ("g", 1000),
               "cm": ("cm", 1), "mm": ("mm", 1), "인치": ("인치", 1)}
# 1+1, 2+1 (뒤에 단위가 붙은 '10캔+2캔' 은 덤 묶음이라 아래 '+' 묶음으로 봄)
_PROMO_RE = re.compile(r"(?<![\d.])([1-9])\s*\+\s*([1-9])(?![\d.]|\s*(?:개|입|병|캔|팩|봉|포|세트|박스|kg|g|ml|l|리터))")
# 단위 없는 곱수 'x 2' ('500ml x 2', '2Lx6'). 뒤에 단위가 붙으면 _QTY_RE 가 읽는다 ('x 2팩')
_TIMES_RE = re.compile(r"(?:(?<=[\s,)가-힣])|(?<=ml)|(?<=\dl)|(?<=\dg))x\s*(\d{1,2})(?![\d.,]*\s*[a-wyz가-힣])")
_TOTAL_RE = re.compile(r"총\s*(\d+)\s*(?:개입|개|입|병|캔|봉지|봉|포|정|매)")  # '(총 48개)' 가 있으면 그게 낱개 총수
_STOP = {"무료", "무료배송", "배송", "특가", "핫딜", "할인", "쿠폰", "행사", "정품", "국내", "당일", "로켓", "로켓배송", "와우", "세일", "이벤트", "추가", "택배", "본품", "신상", "new", "the", "x"}


@dataclass(slots=True)
class MarketQuote:
    price: int
    source: str
    title: str
    url: str | None = None
    checked_at: datetime | None = None


def _num(text: str) -> str:
    """소문자로 바꾸고 천 단위 쉼표('1,000ml')를 지운 문자열."""
    return _THOUSANDS_RE.sub("", text.lower())


def normalize_qty(text: str) -> set[str]:
    out = set()
    for num, unit in _QTY_RE.findall(_num(text)):
        unit = {"개입": "개", "입": "개", "리터": "l"}.get(unit, unit)
        try:
            n = float(num)
            num_s = str(int(n)) if n.is_integer() else str(n)
        except ValueError:
            num_s = num
        out.add(f"{num_s}{unit}")
    return out


@dataclass(frozen=True, slots=True)
class Quantity:
    """상품명에서 읽은 규격. sizes: 단위 종류(ml/g/cm…)별 값, count: 낱개 총수, bundle: '+' 로 다른 상품을 끼운 묶음."""

    sizes: dict[str, frozenset[float]]
    count: float
    bundle: bool


def parse_quantity(text: str) -> Quantity:
    t = _num(text).replace("×", "x").replace("*", "x")
    promo = 1
    for a, b in _PROMO_RE.findall(t):
        promo *= int(a) + int(b)
    t = _PROMO_RE.sub(" ", t)
    sizes: dict[str, set[float]] = {}
    counts: set[tuple[str, float]] = set()  # 같은 수량을 두 번 적은 제목('40병 (40개)')은 한 번만 센다
    count = 1.0
    for m in _QTY_RE.finditer(t):
        n, unit = float(m.group(1)), m.group(2)
        if unit in _SIZE_UNITS:
            dim, scale = _SIZE_UNITS[unit]
            sizes.setdefault(dim, set()).add(round(n * scale, 3))
        elif t[: m.start()].rstrip().endswith("x"):
            count *= n  # '6개입 x 6개', '40병 x 2팩' 의 뒤쪽은 곱수 (같은 수여도 곱함)
        else:
            counts.add(("개" if unit in _COUNT_UNITS else unit, n))
    for _, n in counts:
        count *= n
    for n in _TIMES_RE.findall(t):
        count *= int(n)
    total = _TOTAL_RE.search(t)
    if total:
        count = float(total.group(1))
    return Quantity({k: frozenset(v) for k, v in sizes.items()}, count * promo, "+" in t)


def same_quantity(deal_name: str, candidate_title: str) -> bool:
    """딜과 후보가 같은 규격인가.
    - 딜에 적힌 용량(500ml, 1kg …)은 후보에도 같은 값으로만 있어야 함. 딜에 없는 종류의 용량은 후보에 더 있어도 됨
      (쿠팡 표준 이름은 '할리스 …, 285ml, 24개' 처럼 용량을 덧붙임)
    - 낱개 총수는 같아야 함 (2팩·2박스·1+1 묶음은 다른 상품). 딜에 개수가 없으면 1개로 봄
    - 후보가 '+ 2L 12병' 처럼 다른 상품을 끼운 묶음이면, 딜도 그런 묶음일 때만 같은 상품으로 봄"""
    d, c = parse_quantity(deal_name), parse_quantity(candidate_title)
    if c.bundle and not d.bundle:
        return False
    if any(c.sizes.get(dim) != vals for dim, vals in d.sizes.items()):
        return False
    return d.count == c.count


def tokens(text: str) -> list[str]:
    t = _num(text)
    t = re.sub(r"\[[^\]]*\]", " ", t)  # [쿠팡] 같은 태그 제거
    t = _QTY_RE.sub(" ", t)
    t = _TIMES_RE.sub(" ", t)  # '2Lx6' 의 'x6' 은 낱말이 아님
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
    """0~1. 규격(용량·낱개 총수·묶음)이 다르면 0."""
    if not same_quantity(deal_name, candidate_title):
        return 0.0
    cand_text = candidate_title.lower()
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
            first = tokens(product.name)[:1]
            if self.cfg.require_first_token and first and first[0] not in c.name.lower():
                continue  # 브랜드(첫 낱말)가 다르면 다른 상품
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
