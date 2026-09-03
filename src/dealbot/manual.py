"""관리자가 직접 보내는 딜(/post) 파싱.

예)
  /post
  [토스쇼핑 첫 구매 시 3,000원 추가 할인]
  상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트
  가격: 14,890원
  https://toss.im/_m/P4Qr1ope
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dealbot.shops import ShopRegistry, find_urls
from dealbot.utils.text import parse_price

_HEADLINE_RE = re.compile(r"^\s*\[(?P<h>.+)\]\s*$")
_FIELD_RE = re.compile(r"^\s*(?P<k>상품명|상품|제목|가격|판매가|쇼핑몰|몰|이미지|사진)\s*[:：]\s*(?P<v>.+?)\s*$")


@dataclass(slots=True)
class ManualPost:
    url: str
    name: str
    price: int
    shop_key: str
    headline: str | None = None
    image_url: str | None = None
    shop_tag: str | None = None


def parse_manual_post(text: str, registry: ShopRegistry) -> ManualPost:
    body = re.sub(r"^\s*/post(@\w+)?\s*", "", text or "", count=1, flags=re.IGNORECASE)
    urls = find_urls(body)
    if not urls:
        raise ValueError("링크(URL)가 없습니다. 상품/쿠폰 링크를 포함해 주세요.")
    url = urls[0]
    image_url: str | None = None

    headline = None
    name = None
    price: int | None = None
    shop_tag = None
    leftovers: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line == url or line.startswith("http"):
            continue
        m = _HEADLINE_RE.match(line)
        if m and headline is None:
            headline = m.group("h").strip()
            continue
        m = _FIELD_RE.match(line)
        if m:
            k, v = m.group("k"), m.group("v")
            if k in ("상품명", "상품", "제목"):
                name = v
            elif k in ("가격", "판매가"):
                price = parse_price(v)
            elif k in ("쇼핑몰", "몰"):
                shop_tag = v
            elif k in ("이미지", "사진"):
                image_url = v
            continue
        if line.startswith("이 포스팅은"):
            continue
        leftovers.append(line)

    if name is None and leftovers:
        name = leftovers[0]
    if not name:
        raise ValueError("상품명을 찾을 수 없습니다. '상품: ...' 줄을 넣어 주세요.")

    shop = registry.by_alias(shop_tag) if shop_tag else None
    if shop is None:
        shop = registry.by_url(url)
    if shop is None and headline:
        shop = registry.by_alias(headline.split()[0]) if headline.split() else None
    shop_key = shop.key if shop else "unknown"

    return ManualPost(
        url=url,
        name=name,
        price=int(price or 0),
        shop_key=shop_key,
        headline=headline,
        image_url=image_url,
        shop_tag=shop_tag,
    )
