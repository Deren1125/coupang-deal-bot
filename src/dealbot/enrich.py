"""상품 페이지 메타데이터 보강 (OpenGraph / JSON-LD).

토스 쉐어링크(toss.im/_m/...) 같은 상품 페이지에서 제목·이미지·가격·정상가·별점·리뷰 수를 읽어
Product 의 빈 칸을 채운다. 어떤 몰이든 og:* / JSON-LD Product 를 쓰면 동작한다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup

from dealbot.models import Product
from dealbot.utils.text import parse_price

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PageMeta:
    title: str | None = None
    image: str | None = None
    description: str | None = None
    price: int | None = None
    original_price: int | None = None
    rating: float | None = None
    review_count: int | None = None
    final_url: str | None = None
    available: bool | None = None  # 재고 표시. True 있음 / False 품절 / None 모름


def _availability(value: object) -> bool | None:
    v = str(value or "").strip().lower()
    if not v:
        return None
    if any(k in v for k in ("outofstock", "out of stock", "soldout", "sold out", "oos", "discontinued", "품절")):
        return False
    if any(k in v for k in ("instock", "in stock", "instoreonly", "onlineonly", "preorder", "limitedavailability", "backorder")):
        return True
    return None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for n in names:
        el = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if el is not None and el.get("content"):
            return str(el["content"]).strip()
    return None


def _walk_jsonld(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).lower() == "product" for x in types if x):
            out.append(node)
        for v in node.values():
            _walk_jsonld(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_jsonld(v, out)


def parse_page_meta(html: str) -> PageMeta:
    soup = BeautifulSoup(html, "html.parser")
    meta = PageMeta(
        title=_meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else None),
        image=_meta(soup, "og:image", "twitter:image"),
        description=_meta(soup, "og:description", "description"),
    )
    price_txt = _meta(soup, "product:price:amount", "product:sale_price:amount", "og:price:amount")
    if price_txt:
        meta.price = parse_price(price_txt)
    orig_txt = _meta(soup, "product:original_price:amount", "product:regular_price:amount")
    if orig_txt:
        meta.original_price = parse_price(orig_txt)
    meta.available = _availability(_meta(soup, "product:availability", "og:availability"))

    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        products: list[dict[str, Any]] = []
        _walk_jsonld(data, products)
        for p in products:
            meta.title = meta.title or p.get("name")
            img = p.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            meta.image = meta.image or (img if isinstance(img, str) else None)
            offers = p.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict):
                if meta.price is None:
                    meta.price = parse_price(str(offers.get("price") or offers.get("lowPrice") or ""))
                if meta.original_price is None and offers.get("highPrice"):
                    meta.original_price = parse_price(str(offers.get("highPrice")))
                if meta.available is None:
                    meta.available = _availability(offers.get("availability"))
            agg = p.get("aggregateRating")
            if isinstance(agg, dict):
                try:
                    meta.rating = float(agg.get("ratingValue")) if agg.get("ratingValue") is not None else meta.rating
                except (TypeError, ValueError):
                    pass
                rc = agg.get("reviewCount") or agg.get("ratingCount")
                if rc is not None:
                    meta.review_count = parse_price(str(rc)) if not isinstance(rc, int) else rc
        if products:
            break

    # 본문 텍스트에서 별점/리뷰 보조 추출 (JSON-LD 가 없을 때)
    text = soup.get_text(" ", strip=True)[:20000]
    if meta.rating is None:
        m = re.search(r"(?:별점|평점)\s*[:：]?\s*(\d(?:\.\d)?)", text)
        if m:
            meta.rating = float(m.group(1))
    if meta.review_count is None:
        m = re.search(r"리뷰\s*[:：]?\s*(\d[\d,]*)\s*(?:건|개)?", text)
        if m:
            meta.review_count = int(m.group(1).replace(",", ""))
    return meta


class PageEnricher:
    def __init__(self, http: httpx.AsyncClient, *, timeout: float = 20) -> None:
        self.http = http
        self.timeout = timeout

    async def fetch(self, url: str) -> PageMeta | None:
        try:
            resp = await self.http.get(url, follow_redirects=True, timeout=self.timeout)
            if resp.status_code >= 400:
                log.info("enrich: HTTP %s for %s", resp.status_code, url)
                return None
            meta = parse_page_meta(resp.text)
            meta.final_url = str(resp.url)
            return meta
        except httpx.HTTPError as e:
            log.info("enrich failed for %s: %s", url, e)
            return None

    async def check_available(self, url: str) -> bool | None:
        """상품 페이지의 재고 표시만 읽는다. 페이지를 못 읽거나 표시가 없으면 None(모름)."""
        meta = await self.fetch(url)
        return None if meta is None else meta.available

    @staticmethod
    def apply(product: Product, meta: PageMeta) -> list[str]:
        """빈 칸만 채운다. 채운 필드 이름 목록을 돌려준다."""
        filled: list[str] = []
        if not product.image_url and meta.image:
            product.image_url = meta.image
            filled.append("image_url")
        if (not product.name or product.name == product.url) and meta.title:
            product.name = meta.title
            filled.append("name")
        if not product.has_price and meta.price:
            product.price = meta.price
            filled.append("price")
        if product.original_price is None and meta.original_price and meta.original_price > product.price > 0:
            product.original_price = meta.original_price
            filled.append("original_price")
        if product.rating is None and meta.rating is not None:
            product.rating = meta.rating
            filled.append("rating")
        if product.review_count is None and meta.review_count is not None:
            product.review_count = meta.review_count
            filled.append("review_count")
        return filled
