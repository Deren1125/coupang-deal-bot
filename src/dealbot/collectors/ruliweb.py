"""루리웹 핫딜 게시판 수집기.

- 기본은 '업체 예판 핫딜' 게시판(600004): 제휴 마케터들이 토스쇼핑 딜을 "토스쇼핑상품명,가격원,첫구매추가할인" 형식으로
  올리는 곳. 카톡 핫딜방보다 하루 정도 먼저 뜨는 것이 확인됨. 추천/조회가 거의 없으므로 관심도 게이트는
  deal.interest.always_pass_sources 에 이 수집기 이름을 넣어 통과시키고, 시중가 대조(d)로만 판정하는 것을 권장.
- 유저 핫딜 게시판(1020)도 board_id 만 바꾸면 같은 코드로 수집.
- 목록 페이지 구조는 확인 전이라 셀렉터는 options 로 조정 가능. 0건이면 DOM 요약과 함께 에러를 올린다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from dealbot.collectors.algumon import _first, dom_summary
from dealbot.collectors.base import BaseCollector
from dealbot.collectors.ppomppu import decode_html, find_shop_urls, guess_kind
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price

BASE_URL = "https://bbs.ruliweb.com"
DEFAULT_BOARD = "600004"

DEFAULT_SELECTORS = {
    "row": "tr.table_body, table.board_list_table tbody tr",
    "title": "td.subject a.deco, td.subject a, a.deco",
    "id": "td.id",
    "recommend": "td.recomd",
    "views": "td.hit",
    "time": "td.time",
    "writer": "td.writer",
    "content": "div.view_content, article, div.board_main_view",
}
_TAG_RE = re.compile(r"^\s*\[([^\]]+)\]\s*")
_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")
_TRAILING_RE = re.compile(r"[,\s/]*(첫\s*구매\s*추가\s*할인|무료배송|무배|무료)\s*$")


def parse_title(title: str, registry: ShopRegistry) -> dict[str, Any]:
    """'[음식] [토스쇼핑]애슐리크리스피핫도그4종,80g,8개입,2세트,14890원,첫구매추가할인' →
    shop / name / price / tags."""
    text = title.strip()
    tags: list[str] = []
    while True:
        m = _TAG_RE.match(text)
        if not m:
            break
        tags.append(m.group(1).strip())
        text = text[m.end():]
    shop: Shop | None = None
    for t in tags:
        shop = registry.by_alias(t)
        if shop:
            break
    if shop is None:
        # 태그 없이 "토스쇼핑상품명..." 처럼 시작하는 경우
        for s in registry.all():
            for alias in sorted(s.aliases, key=len, reverse=True):
                if text.lower().startswith(alias.lower()):
                    shop = s
                    text = text[len(alias):].lstrip(" ,:-")
                    break
            if shop:
                break
    price: int | None = None
    m_price = None
    for m_price in _PRICE_RE.finditer(text):
        pass
    if m_price:
        price = parse_price(m_price.group(1))
        name = text[: m_price.start()]
    else:
        name = text
    name = _TRAILING_RE.sub("", name).strip(" ,/-")
    # 단위 뒤 붙어 있는 콤마를 공백으로 보기 좋게
    name = re.sub(r",(?=\S)", ", ", name)
    return {"shop": shop, "name": name or text, "price": price, "tags": tags}


def _int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*", text)
    return int(m.group(0).replace(",", "")) if m else None


def parse_list(html: str, selectors: dict[str, str], registry: ShopRegistry) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[Any] = []
    for sel in [x.strip() for x in selectors["row"].split(",") if x.strip()]:
        rows = soup.select(sel)
        if rows:
            break
    items: list[dict[str, Any]] = []
    for row in rows:
        classes = " ".join(row.get("class") or [])
        if "notice" in classes:
            continue
        a = _first(row, selectors["title"])
        if a is None:
            continue
        href = a.get("href") or ""
        m = re.search(r"/read/(\d+)", href)
        if not m:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        parsed = parse_title(title, registry)
        rec_el = _first(row, selectors["recommend"])
        views_el = _first(row, selectors["views"])
        time_el = _first(row, selectors["time"])
        writer_el = _first(row, selectors["writer"])
        items.append(
            {
                "external_id": m.group(1),
                "title": title,
                "post_url": urljoin(BASE_URL, href),
                "shop": parsed["shop"],
                "name": parsed["name"],
                "price": parsed["price"],
                "recommend": _int(rec_el.get_text(" ", strip=True)) if rec_el else None,
                "views": _int(views_el.get_text(" ", strip=True)) if views_el else None,
                "time": time_el.get_text(" ", strip=True) if time_el else None,
                "writer": writer_el.get_text(" ", strip=True) if writer_el else None,
            }
        )
    return items


@register("ruliweb")
class RuliwebCollector(BaseCollector):
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
        board = str(self.opt("board_id", DEFAULT_BOARD))
        url = self.opt("url", f"{BASE_URL}/market/board/{board}")
        selectors = {**DEFAULT_SELECTORS, **(self.opt("selectors") or {})}
        delay = float(self.opt("request_delay_seconds", 2))
        max_detail = int(self.opt("max_detail_fetch_per_run", 10))
        only_shops = {s.lower() for s in self.opt("shops", [])}
        unknown_policy = self.opt("unknown_shop", "skip")
        registry = self.registry
        interest = self.ctx.settings.deal.interest

        html = await self._get(url)
        items = parse_list(html, selectors, registry)
        if not items:
            summary = dom_summary(html)
            self.ctx.db.log_event("ERROR", f"collector:{self.name}", f"no rows matched selectors. DOM: {summary}")
            raise RuntimeError(
                f"루리웹 목록에서 행을 못 찾음 (셀렉터 조정 필요). /html {url} 로 원문을 받아 Claude 에게 주세요. DOM 요약: {summary[:600]}"
            )

        products: list[Product] = []
        fetched = 0
        for item in items:
            shop = item["shop"]
            if shop is None and unknown_policy != "raw":
                continue
            if shop is not None and (not shop.enabled or shop.link_mode == "skip"):
                continue
            if only_shops and (shop is None or shop.key not in only_shops):
                continue
            if shop is None:
                shop = Shop(key="unknown", name="기타", link_mode="raw")
            ext = item["external_id"]

            if self.ctx.db.is_seen(self.name, ext):
                self.ctx.db.touch_seen(self.name, ext, recommend=item.get("recommend"), views=item.get("views"))
                rec, views = item.get("recommend") or 0, item.get("views") or 0
                if (interest.min_recommend > 0 and rec >= interest.min_recommend) or (interest.min_views > 0 and views >= interest.min_views):
                    seen = self.ctx.db.seen_item(self.name, ext)
                    if seen and seen[0] and seen[1]:
                        products.append(self._build(item, shop, seen[1], product_id=seen[0]))
                continue

            if fetched >= max_detail:
                break
            if fetched > 0 and delay > 0:
                await asyncio.sleep(delay)
            fetched += 1
            deal_url: str | None = None
            try:
                detail_html = await self._get(item["post_url"])
                urls = find_shop_urls(detail_html, None if shop.key == "unknown" else shop)
                deal_url = urls[0] if urls else None
            except Exception as e:  # noqa: BLE001
                self.log.warning("detail fetch failed %s: %s", item["post_url"], e)
            deal_url = deal_url or item["post_url"]
            product = self._build(item, shop, deal_url)
            self.ctx.db.mark_seen(
                self.name, ext, product.product_id, url=deal_url,
                title=item["title"], recommend=item.get("recommend"), views=item.get("views"),
            )
            products.append(product)
        self.log.info("ruliweb(%s): %d listed, %d fetched, %d products", board, len(items), fetched, len(products))
        return products

    def _build(self, item: dict[str, Any], shop: Shop, deal_url: str, *, product_id: str | None = None) -> Product:
        price = item.get("price")
        return Product(
            source=self.name,
            product_id=product_id or ShopRegistry.product_key(shop.key, deal_url),
            shop=shop.key,
            deal_kind=guess_kind(item["name"], price),
            name=item["name"],
            price=int(price or 0),
            url=deal_url,
            external_id=item["external_id"],
            recommend_count=item.get("recommend"),
            view_count=item.get("views"),
            extra={"post_url": item["post_url"], "title": item["title"], "writer": item.get("writer")},
        )
