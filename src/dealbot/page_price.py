"""상품 페이지 가격 대조 (주인 결정 10/12, 큐 #22).

게시판 제목 가격(예: '[토스] 풀무원 생수 2L 24개 (6,410원/무료)')을 그대로 믿으면 다른 옵션 값(12개짜리 6,390원)을
24개 값으로 올리는 일이 생긴다 (#22: 실제 24개는 14,465원인데 쿠팡 대비 79.9% 싸다고 링크 요청까지 감).
그래서 수집 때 이미 읽는 상품 페이지(사진·별점을 읽는 그 페이지)에서 가격과 옵션을 같이 읽어 대조한다.

- 페이지에 보이는 가격은 모두 후보: 판매가·할인가·모바일 할인가·정가, 옵션별 가격, (배송비를 알면) 배송비 포함가,
  '2개 구매' 같은 묶음(2~4배). 쿠폰가·회원가처럼 가격이 여러 개면 어느 하나에만 맞아도 맞은 것으로 본다.
- 제목 가격 ±tolerance_pct 안에 드는 후보가 있으면 통과. 대표(기본) 옵션이 아니라 다른 옵션에 맞으면 그 옵션 이름을 남긴다.
  · 그 옵션의 수량·용량이 제목과 다르면(제목 24개, 맞는 옵션 12개) 보류 — 이름을 옵션에 맞춰 고치면 커뮤니티 글과 다른
    상품을 올리게 되고, 쿠팡 시중가 대조도 다시 해야 해서 사람이 보는 쪽이 안전하다.
- 어느 후보도 ±tolerance_pct 안에 없으면 보류.
- 페이지를 못 읽었거나(막힘·시간 초과) 페이지에 가격이 없으면 '확인 못 함' — 지금처럼 진행하고 로그만 남긴다.

가격을 읽는 곳:
- 네이버 스마트스토어·브랜드스토어: 페이지에 들어 있는 window.__PRELOADED_STATE__ 의 상품 데이터
  (salePrice·discountedSalePrice·benefitsView·optionCombinations[].price(기본가 대비 차액)·배송비 baseFee).
  수집 때 일반 요청이 막히면(429) 같은 페이지를 사진용 크롬(store_browser)으로 읽는다 (page_reader: 한 판·한 시간 한도, 막히면 쉼).
  그래도 못 읽으면 확인 못 함이고, 발행 직전 사진 때문에 이미 여는 크롬 페이지가 있으면 그걸 다시 쓴다.
  TODO: 이 구조는 10/12 기준 이 환경에서 실제 스토어 페이지를 열 수 없어(일반 요청·크롬 모두 429) 실물로 확인하지 못했다.
  서버 로그에 'page price unverified … 페이지에 가격 없음' 이 네이버 딜에 찍히면(크롬이 연 페이지에 사진은 있는데 가격이 없음)
  구조가 바뀐 것이니 store_browser 가 받은 HTML 로 다시 맞출 것.
- 그 밖의 몰: JSON-LD Product.offers(옵션별 Offer 목록·AggregateOffer 포함), og/product:price 메타.
- 토스쇼핑: 웹 상품 페이지(toss.shopping/t/…, toss.im/_m/… 리디렉션)는 가격을 아예 보여 주지 않는다
  (10/12 확인: SSR 데이터·JSON-LD 에 가격 칸이 없고 화면엔 'QR코드를 찍어 가격을 확인해보세요'). 그래서 토스는 늘 '확인 못 함'.
  토스가 웹에 가격(JSON-LD offers 등)을 넣으면 위 일반 경로로 그대로 대조된다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from dealbot.utils.text import parse_price

MIN_PRICE = 100  # 이보다 작은 숫자는 가격이 아님 (개수·퍼센트 등)
MAX_MULTIPLE = 4  # '2개 구매 시' 같은 묶음 가격은 4배까지


@dataclass(slots=True)
class PageOption:
    """페이지에 보이는 옵션 하나. label 이 None 이면 옵션 없는 상품의 대표 가격."""

    label: str | None
    prices: tuple[int, ...]  # 이 옵션에 보이는 가격들 (판매가·할인가·쿠폰가·정가 …)
    lead: bool = False  # 기본(선택된) 옵션


@dataclass(slots=True)
class PageCheck:
    status: str  # ok | option | hold | unverified
    title_price: int
    page_price: int | None = None  # 대표(기본) 옵션 가격
    page_label: str | None = None  # 대표 옵션 이름 (없으면 페이지 상품명의 규격 '2L 24개')
    matched_price: int | None = None  # 제목 가격에 맞은 페이지 가격
    matched_label: str | None = None  # 맞은 옵션 이름
    reason: str = ""  # price(맞는 가격 없음) | quantity(맞는 옵션의 수량이 제목과 다름) | 확인 못 한 이유

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PagePrices:
    """페이지에서 읽은 가격 묶음 (enrich.PageMeta.prices)."""

    options: list[PageOption] = field(default_factory=list)
    shipping_fee: int | None = None  # 배송비를 알면 (무료면 0)
    source: str = ""  # naver_state | jsonld | meta


# ------------------------------------------------------------------ 읽기
def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    if isinstance(v, str):
        return parse_price(v)
    return None


def _ok(v: int | None) -> int | None:
    return v if v is not None and v >= MIN_PRICE else None


def _uniq(values: list[int | None]) -> tuple[int, ...]:
    out: list[int] = []
    for v in values:
        if v is not None and v >= MIN_PRICE and v not in out:
            out.append(v)
    return tuple(out)


_STATE_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")
_STORE_NO_RE = re.compile(r"/products/(\d+)")


def _walk(node: Any, out: list[dict[str, Any]], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(node, dict):
        if isinstance(node.get("salePrice"), int | float) and not isinstance(node.get("salePrice"), bool):
            out.append(node)
        for v in node.values():
            _walk(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out, depth + 1)


def naver_state_prices(html: str, url: str | None = None) -> PagePrices | None:
    """네이버 스토어 페이지의 window.__PRELOADED_STATE__ 에서 상품 가격·옵션. 없거나 못 읽으면 None."""
    m = _STATE_RE.search(html)
    if not m:
        return None
    try:
        state, _ = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError:
        return None
    nodes: list[dict[str, Any]] = []
    _walk(state, nodes)
    if not nodes:
        return None
    # 같은 페이지에 다른 상품(추천·함께 본 상품)이 있을 수 있어 주소의 상품번호와 같은 것을 먼저, 없으면 옵션이 있는 것
    no = (_STORE_NO_RE.search(url or "") or [None, None])[1]
    if no:
        same = [n for n in nodes if str(n.get("productNo") or "") == no or str(n.get("id") or "") == no]
        nodes = same or nodes
    nodes.sort(key=lambda n: (0 if n.get("optionCombinations") else 1, 0 if n.get("productNo") or n.get("name") else 1))
    node = nodes[0]
    benefits = node.get("benefitsView") if isinstance(node.get("benefitsView"), dict) else {}
    sale = _ok(_int(node.get("salePrice")))
    base = [
        _ok(_int(node.get("discountedSalePrice"))),
        _ok(_int(node.get("mobileDiscountedSalePrice"))),
        # 할인 묶음(benefitsView)에 보이는 '…Price' 는 모두 후보 (즉시할인·쿠폰·멤버십가 등, 이름이 바뀌어도 잡히게)
        *(_ok(_int(v)) for k, v in benefits.items() if k.lower().endswith("price")),
        sale,
    ]
    lead_prices = _uniq(base)
    if not lead_prices:
        return None
    options: list[PageOption] = []
    combos = node.get("optionCombinations")
    if isinstance(combos, list):
        for c in combos:
            if not isinstance(c, dict):
                continue
            if c.get("usable") is False or (isinstance(c.get("stockQuantity"), int) and c["stockQuantity"] <= 0):
                continue  # 품절·판매 안 하는 옵션
            names = [str(c.get(f"optionName{i}") or "").strip() for i in range(1, 6)]
            label = " ".join(n for n in names if n) or None
            delta = _int(c.get("price")) or 0
            options.append(PageOption(label=label, prices=_uniq([p + delta for p in lead_prices]), lead=delta == 0))
        options = [o for o in options if o.prices]
        if options and not any(o.lead for o in options):
            options[0].lead = True
    if not options:
        options = [PageOption(label=None, prices=lead_prices, lead=True)]
    return PagePrices(options=options, shipping_fee=_naver_fee(node), source="naver_state")


def _naver_fee(node: dict[str, Any]) -> int | None:
    for key in ("productDeliveryInfo", "deliveryInfo", "delivery"):
        d = node.get(key)
        if isinstance(d, dict):
            kind = str(d.get("deliveryFeeType") or "").upper()
            if kind == "FREE":
                return 0
            fee = _int(d.get("baseFee"))
            if fee is not None and kind in ("PAID", ""):
                return fee if fee >= 0 else None
    return None


def jsonld_prices(products: list[dict[str, Any]]) -> PagePrices | None:
    """JSON-LD Product 의 offers (Offer 하나 / Offer 목록(옵션별) / AggregateOffer)."""
    for p in products:
        offers = p.get("offers")
        flat: list[dict[str, Any]] = []
        if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
            flat = [o for o in offers["offers"] if isinstance(o, dict)]
            agg = offers
        else:
            agg = offers if isinstance(offers, dict) else None
            flat = [o for o in (offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []) if isinstance(o, dict)]
        options: list[PageOption] = []
        multi = len(flat) > 1
        for i, o in enumerate(flat):
            prices = _uniq([_int(o.get("price")), _int(o.get("lowPrice"))])
            if not prices:
                continue
            label = str(o.get("name") or o.get("sku") or "").strip() or None if multi else None
            options.append(PageOption(label=label, prices=prices, lead=i == 0))
        if not options and isinstance(agg, dict):
            low = _ok(_int(agg.get("lowPrice")))
            if low:
                options.append(PageOption(label=None, prices=(low,), lead=True))
            high = _ok(_int(agg.get("highPrice")))
            if high and high != low:
                options.append(PageOption(label=None, prices=(high,)))
        if options:
            if not any(o.lead for o in options):
                options[0].lead = True
            return PagePrices(options=options, source="jsonld")
    return None


# ------------------------------------------------------------------ 대조
def _qty_text(title: str | None) -> str | None:
    """페이지 상품명에서 규격만 ('풀무원 샘물 생수, 무라벨, 2L, 24개 | 토스쇼핑' → '2L 24개')."""
    from dealbot.pricing.market import _QTY_RE

    if not title:
        return None
    parts = [m.group(0).replace(" ", "") for m in _QTY_RE.finditer(title.split("|")[0])]
    return " ".join(dict.fromkeys(parts)) or None


def _has_count(text: str) -> bool:
    from dealbot.pricing.market import _QTY_RE, _SIZE_UNITS, _TIMES_RE, _num

    t = _num(text)
    return any(m.group(2).lower() not in _SIZE_UNITS for m in _QTY_RE.finditer(t)) or bool(_TIMES_RE.search(t))


def quantity_conflict(name: str, label: str | None) -> bool:
    """옵션 이름의 수량·용량이 상품명(제목)과 분명히 다른가. 한쪽에라도 적혀 있지 않은 값은 비교하지 않는다
    (색상·맛 옵션, 수량 없는 제목은 다르다고 보지 않음)."""
    if not label:
        return False
    from dealbot.pricing.market import parse_quantity

    d, o = parse_quantity(name), parse_quantity(label)
    if any(dim in d.sizes and d.sizes[dim] != vals for dim, vals in o.sizes.items()):
        return True
    return _has_count(label) and _has_count(name) and d.count != o.count


# 제목에 이런 말이 있으면 글 가격이 페이지에 안 보이는 할인(카드 청구할인·다운로드 쿠폰·멤버십·적립)을 뺀 값일 수 있다
DISCOUNT_WORDS = re.compile(r"카드|쿠폰|적립|멤버십|회원|첫\s*구매|신규|중복|청구|페이\s*(?:할인|결제)|[가-힣]+페이|캐시백")


def _within(title: int, price: int, tol: float, below: float = 0) -> bool:
    """페이지 가격이 제목 가격의 ±tol% 안. below 가 있으면 제목이 페이지보다 그만큼(%)까지 싼 것도 맞은 것으로.
    싼 정도는 페이지 가격 기준으로 잰다 (25% 면 페이지 10,000원에 제목 7,500원까지)."""
    if abs(price - title) <= title * tol / 100:
        return True
    return below > 0 and price * (1 - below / 100) <= title < price


def _candidates(opt: PageOption, fee: int | None) -> list[tuple[int, str | None, int]]:
    """(가격, 수량 비교에 쓸 이름, 몇 개 묶음). 묶음은 이름 뒤에 'xN' 을 붙여 수량도 N배로 비교한다."""
    out: list[tuple[int, str | None, int]] = []
    for p in opt.prices:
        out.append((p, opt.label, 1))
        if fee:
            out.append((p + fee, opt.label, 1))  # 제목 가격이 배송비 포함가일 때
        for k in range(2, MAX_MULTIPLE + 1):
            out.append((p * k, f"{opt.label} x{k}" if opt.label else None, k))
    return out


def check_page_price(
    name: str,
    title_price: int,
    prices: PagePrices | None,
    *,
    tolerance_pct: float = 10,
    page_title: str | None = None,
    discount_tolerance_pct: float = 0,
) -> PageCheck:
    """제목 가격과 페이지 가격 대조. prices 가 None 이거나 비면 unverified.
    맞는 후보가 여럿이면: 수량이 제목과 어긋나지 않는 것 중 낱개(묶음 아님) → 제목 가격과 가장 가까운 것 → 기본 옵션 순.
    discount_tolerance_pct: 제목에 카드·쿠폰 같은 말이 있을 때(부르는 쪽이 판단) 글 가격이 페이지보다 이만큼(%)까지 싸도 맞은 것으로."""
    if not prices or not any(o.prices for o in prices.options):
        return PageCheck("unverified", title_price, reason="페이지에 가격 없음")
    lead = next((o for o in prices.options if o.lead and o.prices), None) or next(o for o in prices.options if o.prices)
    page_label = lead.label or _qty_text(page_title)
    base = PageCheck("hold", title_price, page_price=min(lead.prices), page_label=page_label)

    hits = [
        (price, qty_label, k, opt)
        for opt in prices.options
        for price, qty_label, k in _candidates(opt, prices.shipping_fee)
        if _within(title_price, price, tolerance_pct, discount_tolerance_pct)
    ]
    rank = lambda h: (h[2] > 1, abs(h[0] - title_price), h[3] is not lead)  # noqa: E731
    fits = sorted((h for h in hits if not quantity_conflict(name, h[1])), key=rank)
    if fits:
        price, qty_label, k, opt = fits[0]
        base.matched_price, base.matched_label = price, qty_label
        base.status = "ok" if opt is lead and k == 1 or not qty_label else "option"
        return base
    if hits:
        # 값은 맞는데 그 옵션의 수량이 제목과 다름 (#22: 제목 24개 6,410원 = '2L 12개' 옵션 6,390원)
        price, qty_label, _, _ = sorted(hits, key=rank)[0]
        base.matched_price, base.matched_label = price, qty_label
        base.reason = "quantity"
    else:
        base.reason = "price"
    return base


def hold_line(queue_id: int, check: PageCheck) -> str:
    """관리자 챗 보류 메모 첫 줄 (해요체). HTML 이스케이프는 부르는 쪽에서."""
    page = f"{check.page_price:,}원" if check.page_price else "?"
    label = f" ({check.page_label})" if check.page_label else ""
    line = f"⏸ #{queue_id} 보류: 글 가격 {check.title_price:,}원 ≠ 상품 페이지 {page}{label}"
    if check.matched_price and check.matched_label:
        line += f" — {check.matched_price:,}원짜리는 '{check.matched_label}' 옵션이에요"
    else:
        line += " — 맞는 옵션이 없어요"
    return line
