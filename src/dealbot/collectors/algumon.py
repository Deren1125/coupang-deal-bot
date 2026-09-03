"""알구몬(algumon.com) 수집기 — 뽐뿌·루리웹·클리앙·퀘이사존 등 커뮤니티 핫딜을 한 페이지에서.

알구몬은 원본 커뮤니티 글의 제목과 링크만 보여주므로, 목록에서 제목/원본 링크를 읽고
원본 글을 열어 쇼핑몰 링크를 찾는다. 페이지 구조를 이 환경에서 확인하지 못했으므로
셀렉터는 options 로 바꿀 수 있고, 아무 행도 못 찾으면 페이지 구조 요약을 에러 로그에 남긴다
(/errors 로 확인해서 셀렉터를 맞추면 됨).
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.ppomppu import decode_html, find_shop_urls, guess_kind, parse_title
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price

BASE_URL = "https://www.algumon.com"
LIST_URL = BASE_URL + "/n/deal"

DEFAULT_SELECTORS = {
    # 후보를 여러 개 적어 두면 처음 매칭되는 것을 씀
    "row": "li.post-li, li.deal, div.deal-item, article, tr.deal",
    "title": "a.product-link, a.title, a.deal-title, h3 a, a[href]",
    "price": ".product-price, .price, .deal-price",
    "shop": ".product-mall, .mall, .shop, .site",
    "likes": ".product-like, .likes, .like, .recommend, .vote",
    "origin": "a[href*='ppomppu'], a[href*='ruliweb'], a[href*='clien'], a[href*='quasarzone'], a[href*='coolenjoy'], a[href*='eomisae'], a[href*='arca'], a[href*='zod']",
}


def _first(el: Any, selector: str) -> Any:
    for sel in [x.strip() for x in selector.split(",") if x.strip()]:
        found = el.select_one(sel)
        if found is not None:
            return found
    return None


def _all(soup: Any, selector: str) -> list[Any]:
    for sel in [x.strip() for x in selector.split(",") if x.strip()]:
        found = soup.select(sel)
        if found:
            return list(found)
    return []


def _int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


def dom_summary(html: str, limit: int = 25) -> str:
    """셀렉터 조정을 위한 페이지 구조 요약: 가장 흔한 (태그.클래스) 조합."""
    soup = BeautifulSoup(html, "html.parser")
    counter: Counter[str] = Counter()
    for el in soup.find_all(True):
        cls = el.get("class")
        if cls:
            counter[f"{el.name}.{'.'.join(cls[:2])}"] += 1
    return ", ".join(f"{k}×{v}" for k, v in counter.most_common(limit))


def parse_list(html: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    for row in _all(soup, selectors["row"]):
        a = _first(row, selectors["title"])
        if a is None:
            continue
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        if not title or not href:
            continue
        origin_el = _first(row, selectors["origin"])
        origin = origin_el.get("href") if origin_el is not None else None
        link = urljoin(BASE_URL, href)
        origin_url = urljoin(BASE_URL, origin) if origin else link
        price_el = _first(row, selectors["price"])
        shop_el = _first(row, selectors["shop"])
        likes_el = _first(row, selectors["likes"])
        parsed = parse_title(title)
        price = parsed["price"] or (parse_price(price_el.get_text(" ", strip=True)) if price_el else None)
        items.append(
            {
                "external_id": re.sub(r"\W+", "_", urlparse(link).path + ("?" + urlparse(link).query if urlparse(link).query else ""))[:120],
                "title": title,
                "link": link,
                "origin_url": origin_url,
                "shop_tag": parsed["shop_tag"] or (shop_el.get_text(" ", strip=True) if shop_el else None),
                "name": parsed["name"],
                "price": price,
                "shipping": parsed["shipping"],
                "recommend": _int(likes_el.get_text(" ", strip=True)) if likes_el else None,
            }
        )
    return items


@register("algumon")
class AlgumonCollector(BaseCollector):
    requires_coupang = False

    @property
    def registry(self) -> ShopRegistry:
        reg = getattr(self.ctx, "shops", None)
        return reg if reg is not None else self.ctx.settings.shop_registry()

    async def _get(self, url: str) -> str:
        http_cfg = self.ctx.settings.http

        async def _do() -> str:
            resp = await self.ctx.http.get(url, follow_redirects=True)
            resp.raise_for_status()
            return decode_html(resp)

        return await retry_async(_do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label=f"GET {url}")

    async def collect(self) -> list[Product]:
        url = self.opt("url", LIST_URL)
        selectors = {**DEFAULT_SELECTORS, **(self.opt("selectors") or {})}
        delay = float(self.opt("request_delay_seconds", 2))
        max_detail = int(self.opt("max_detail_fetch_per_run", 10))
        only_shops = {s.lower() for s in self.opt("shops", [])}
        unknown_policy = self.opt("unknown_shop", "skip")
        registry = self.registry

        html = await self._get(url)
        items = parse_list(html, selectors)
        if not items:
            summary = dom_summary(html)
            self.log.warning("algumon: no rows matched — page structure: %s", summary)
            self.ctx.db.log_event("ERROR", f"collector:{self.name}", f"no rows matched selectors. DOM: {summary}")
            return []

        products: list[Product] = []
        fetched = 0
        for item in items:
            tag = item.get("shop_tag")
            shop = registry.by_alias(tag) if tag else None
            if shop is None and unknown_policy != "raw":
                continue
            if shop is not None and (not shop.enabled or shop.link_mode == "skip"):
                continue
            if only_shops and (shop is None or shop.key not in only_shops):
                continue
            if shop is None:
                shop = Shop(key="unknown", name=tag or "기타", link_mode="raw")
            ext = item["external_id"]
            if self.ctx.db.is_seen(self.name, ext):
                self.ctx.db.touch_seen(self.name, ext, recommend=item.get("recommend"), views=None)
                continue
            if fetched >= max_detail:
                break
            if fetched > 0 and delay > 0:
                await asyncio.sleep(delay)
            fetched += 1
            deal_url: str | None = None
            try:
                origin_html = await self._get(item["origin_url"])
                urls = find_shop_urls(origin_html, None if shop.key == "unknown" else shop)
                deal_url = urls[0] if urls else None
            except Exception as e:  # noqa: BLE001
                self.log.warning("origin fetch failed %s: %s", item["origin_url"], e)
            deal_url = deal_url or item["origin_url"]
            price = item.get("price")
            product = Product(
                source=self.name,
                product_id=ShopRegistry.product_key(shop.key, deal_url),
                shop=shop.key,
                deal_kind=guess_kind(item["name"], price),
                name=item["name"],
                price=int(price or 0),
                url=deal_url,
                shipping=item.get("shipping"),
                external_id=ext,
                recommend_count=item.get("recommend"),
                extra={"post_url": item["origin_url"], "algumon_url": item["link"], "title": item["title"]},
            )
            self.ctx.db.mark_seen(self.name, ext, product.product_id, url=deal_url, title=item["title"], recommend=item.get("recommend"))
            products.append(product)
        self.log.info("algumon: %d listed, %d fetched, %d products", len(items), fetched, len(products))
        return products
