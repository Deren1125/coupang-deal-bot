"""인스타그램 후보 양식(A/B/C)의 카드 사실·본문 재료. 주인이 고르기 전까지 기본은 current (config instagram.style).

- 숫자는 모두 데이터에서: 가격, 글 첫 줄과 같은 비교 근거(TemplateRenderer.reference — 쿠팡 최저가·믿을 만한 평소 가격,
  10% 이상일 때만), 기록 최저가 라벨(low_label), 표시 정가(근거가 없을 때만 보조), 단가, 배송, 평점.
- 첫 줄은 사실만 ('이름 가격 · 비교 가격'). '역대급'·'강추' 같은 말, 느낌표, 링크 주소는 안 쓴다 (인스타 본문 링크는 눌리지 않음).
- 해시태그는 5개까지 (인스타가 2025-12 부터 게시물당 5개로 제한, 적은 수의 구체적인 태그가 낫다는 안내).
- 고지: 카드 안에 짧게(공정위 추천·보증 심사지침 — 사진 게시물은 사진 안에) + 본문 끝에 몰의 고지 문구 그대로.
"""

from __future__ import annotations

import re
from typing import Any

from dealbot.media.card import CardInfo
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer, price_band, shipping_text, short_name
from dealbot.shops import Shop
from dealbot.utils.text import clean_name, unit_price

STYLE_TEMPLATES = {"A": "deal_instagram_a.j2", "B": "deal_instagram_b.j2", "C": "deal_instagram_c.j2"}
MAX_HASHTAGS = 5
BRAND_TAG = "#오늘의핫딜"
SHOP_TAGS = {"coupang": "#쿠팡핫딜", "naver": "#네이버쇼핑"}
# 살림 계정 독자가 찾는 말 — 분류·이름에 이 말이 있을 때만 붙인다
_LIVING = re.compile(r"주방|생활|세제|세탁|욕실|청소|수납|화장지|휴지|물티슈|키친|식기|냄비|프라이팬|텀블러")
_TAG_CHARS = re.compile(r"[^0-9A-Za-z가-힣]")
_DISCLOSURE = re.compile(r"이 포스팅은\s*(.+?)\s*활동의 일환")


def template_for(style: str, default: str) -> str:
    return STYLE_TEMPLATES.get(style, default)


def short_disclosure(text: str | None) -> str:
    """카드 안에 넣을 짧은 고지. '이 포스팅은 쿠팡 파트너스 활동의 일환으로…' → '쿠팡 파트너스 활동으로 수수료를 받습니다'."""
    if not text:
        return ""
    m = _DISCLOSURE.search(text)
    if m:
        return f"{m.group(1)} 활동으로 수수료를 받습니다"
    return text if len(text) <= 48 else text[:47] + "…"


def _tag(word: str | None) -> str | None:
    w = _TAG_CHARS.sub("", word or "")
    return f"#{w}" if 2 <= len(w) <= 12 else None


def hashtags(deal: Deal, shop: Shop | None) -> list[str]:
    """핫딜 · 몰 · 분류 · (살림) · 채널 — 이 딜과 관련 있는 것만, 5개까지."""
    p = deal.product
    key = shop.key if shop else p.shop
    cat = (p.category or "").split(">")[-1].strip()
    tags: list[str | None] = ["#핫딜", SHOP_TAGS.get(key) or _tag(f"{shop.name if shop else key}핫딜"), _tag(cat)]
    if _LIVING.search(f"{p.category or ''} {clean_name(p.name)}"):
        tags.append("#살림템")
    tags.append(BRAND_TAG)
    out: list[str] = []
    for t in tags:
        if t and t not in out:
            out.append(t)
    return out[:MAX_HASHTAGS]


def card_info(deal: Deal, renderer: TemplateRenderer, *, shop: Shop | None, deal_no: int | None = None) -> CardInfo:
    p, v = deal.product, deal.verdict
    ref_price, ref_pct, is_market = renderer.reference(deal)
    days = int(min(v.history_days or 0, 30))
    # 배송: deal_facts 와 같은 말
    if p.is_rocket:
        ship = "무료 로켓배송" if p.is_free_shipping else "로켓배송"
    else:
        ship = shipping_text(p.shipping) or ("무료배송" if p.is_free_shipping else "")
    price = p.price if p.has_price else None
    list_price = p.original_price if p.original_price and price and p.original_price > price else None
    list_pct = round((1 - price / list_price) * 100) if list_price and price else None
    return CardInfo(
        name=clean_name(p.name),
        price=price,
        short=(p.extra or {}).get("short_name") or short_name(p.name, limit=22),
        ref_price=ref_price,
        ref_label=("쿠팡 최저가" if is_market else "평소 가격") if ref_price else "",
        ref_text=(f"쿠팡 최저가 {ref_price:,}원" if is_market else f"평소 {price_band(ref_price, p.price):,}원대")
        if ref_price and ref_pct else "",
        ref_pct=round(ref_pct) if ref_price and ref_pct else None,
        list_price=list_price,
        list_pct=list_pct if list_pct and list_pct >= 5 else None,
        low_label=renderer.low_label(deal, days),
        ship=ship,
        unit=unit_price(clean_name(p.name), p.price) if price else None,
        rating=p.rating,
        review_count=p.review_count,
        shop_name=shop.name if shop else (p.shop if p.shop and p.shop != "unknown" else ""),
        category=(p.category or "").split(">")[-1].strip(),
        deal_no=deal_no,
        disclosure=short_disclosure(shop.disclosure if shop else None),
    )


def caption_context(deal: Deal, info: CardInfo, *, shop: Shop | None) -> dict[str, Any]:
    """후보 양식 본문(deal_instagram_a/b/c.j2)의 ig 변수."""
    p = deal.product
    name = (p.extra or {}).get("short_name") or short_name(p.name)
    compare = info.ref_text or None
    hook = name
    if info.price:
        hook = f"{name} {info.price:,}원"
        extra = compare or info.low_label
        if compare and info.low_label:
            extra = f"{compare} · {info.low_label}"
        if extra:
            hook += f" · {extra}"
    rows: list[tuple[str, str]] = []
    if info.price:
        rows.append(("가격", f"{info.price:,}원"))
    if compare:
        rows.append(("비교", f"{compare} ({info.ref_pct}%↓)"))
    elif info.list_price:
        rows.append(("정가", f"{info.list_price:,}원"))
    if info.low_label:
        rows.append(("기록", info.low_label))
    if info.unit:
        rows.append(("단가", info.unit))
    if info.ship:
        rows.append(("배송", info.ship))
    if info.rating:
        rows.append(("평점", f"{info.rating}" + (f" · 리뷰 {info.review_count:,}건" if info.review_count else "")))
    if info.shop_name:
        rows.append(("판매", info.shop_name))
    if info.deal_no:
        cta = f"구매 링크: 프로필 링크 → 핫딜 페이지 #{info.deal_no}"
    else:
        cta = "구매 링크: 프로필 링크 → 핫딜 페이지"
    return {
        "hook": hook,
        "name": name,
        "compare": compare,
        "rows": rows,
        "cta": cta,
        "deal_no": info.deal_no,
        "hashtags": " ".join(hashtags(deal, shop)),
        "disclosure": shop.disclosure if shop and shop.disclosure else "",
    }
