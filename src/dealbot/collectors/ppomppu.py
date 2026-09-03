"""뽐뿌 핫딜 게시판 크롤러 (쿠팡 딜만).

- 목록 페이지에서 제목/링크/썸네일을 읽고, 쿠팡 딜만 골라 글 상세에서 쿠팡 상품 URL 을 찾는다.
- 사이트 구조가 바뀌면 config 의 options.*_selector 만 바꾸면 되도록 셀렉터를 설정으로 뺐다.
- 이미 본 글(external_id)은 DB 에 기록해 상세 페이지를 다시 요청하지 않는다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price
from dealbot.utils.urls import (
    canonical_product_url,
    extract_product_id,
    is_coupang_url,
    is_short_affiliate_link,
)

BASE_URL = "https://www.ppomppu.co.kr"
LIST_URL = BASE_URL + "/zboard/zboard.php"

# "[쿠팡] 상품명 (12,900원/무료)" 형태
_TITLE_RE = re.compile(r"^\s*\[(?P<shop>[^\]]+)\]\s*(?P<name>.+?)\s*(?:\((?P<meta>[^()]*)\))?\s*$")
_COUPANG_URL_RE = re.compile(r"https?://(?:www\.|m\.|link\.)?coupang\.com/[^\s\"'<>]+")


def parse_title(title: str) -> dict[str, Any]:
    """제목 → shop / name / price / shipping. price 는 원 단위 정수 또는 None."""
    m = _TITLE_RE.match(title.strip())
    if not m:
        return {"shop": None, "name": title.strip(), "price": None, "shipping": None}
    meta = (m.group("meta") or "").strip()
    price_text, _, shipping = meta.partition("/")
    return {
        "shop": m.group("shop").strip(),
        "name": m.group("name").strip(),
        "price": parse_price(price_text) if price_text else None,
        "shipping": shipping.strip() or None,
    }


def decode_html(resp: httpx.Response) -> str:
    """뽐뿌는 EUC-KR 이 섞여 있어 헤더 charset 이 없으면 euc-kr → utf-8 순으로 시도."""
    ctype = resp.headers.get("content-type", "").lower()
    if "charset=" in ctype:
        try:
            return resp.text
        except (UnicodeDecodeError, LookupError):
            pass
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return resp.content.decode("utf-8", errors="replace")


def parse_list_page(html: str, *, row_selector: str, title_selector: str, thumb_selector: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(row_selector)
    items: list[dict[str, Any]] = []
    for row in rows:
        a = row.select_one(title_selector)
        if a is None:
            continue
        href = a.get("href") or ""
        if "view.php" not in href:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        no = parse_qs(urlparse(href).query).get("no", [None])[0]
        if not no:
            continue
        img = row.select_one(thumb_selector)
        thumb = None
        if img is not None:
            src = img.get("src") or img.get("data-src")
            if src:
                thumb = src if src.startswith("http") else ("https:" + src if src.startswith("//") else urljoin(BASE_URL, src))
        items.append(
            {
                "external_id": str(no),
                "title": title,
                "post_url": urljoin(LIST_URL, href),
                "thumb": thumb,
                **parse_title(title),
            }
        )
    return items


def find_coupang_urls(html: str) -> list[str]:
    """글 상세 HTML 에서 쿠팡 URL 후보를 우선순위대로 돌려준다 (상품 페이지 > 단축 링크 > 기타)."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if is_coupang_url(href):
            candidates.append(href)
    for m in _COUPANG_URL_RE.finditer(html):
        candidates.append(m.group(0).rstrip(".,)"))

    def rank(u: str) -> int:
        if extract_product_id(u):
            return 0
        if is_short_affiliate_link(u):
            return 1
        return 2

    unique: list[str] = []
    for u in sorted(dict.fromkeys(candidates), key=rank):
        unique.append(u)
    return unique


@register("ppomppu")
class PpomppuCollector(BaseCollector):
    requires_coupang = False

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> str:
        http_cfg = self.ctx.settings.http

        async def _do() -> str:
            resp = await self.ctx.http.get(url, params=params, follow_redirects=True)
            resp.raise_for_status()
            return decode_html(resp)

        return await retry_async(
            _do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label=f"GET {url}"
        )

    async def _resolve_product_url(self, url: str) -> str | None:
        canon = canonical_product_url(url)
        if canon:
            return canon
        if is_short_affiliate_link(url) and self.ctx.settings.links.resolve_short_links:
            try:
                resp = await self.ctx.http.get(url, follow_redirects=True)
                return canonical_product_url(str(resp.url))
            except httpx.HTTPError as e:
                self.log.debug("short link resolve failed %s: %s", url, e)
        return None

    async def collect(self) -> list[Product]:
        board = self.opt("board_id", "ppomppu")
        pages = int(self.opt("pages", 1))
        shop_keywords = [k.lower() for k in self.opt("shop_keywords", ["쿠팡"])]
        delay = float(self.opt("request_delay_seconds", 2))
        max_detail = int(self.opt("max_detail_fetch_per_run", 15))
        row_sel = self.opt("list_row_selector", "tr.baseList")
        title_sel = self.opt("title_selector", "a.baseList-title")
        thumb_sel = self.opt("thumb_selector", "img")

        listed: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            if page > 1:
                await asyncio.sleep(delay)
            html = await self._get(LIST_URL, params={"id": board, "page": page})
            items = parse_list_page(html, row_selector=row_sel, title_selector=title_sel, thumb_selector=thumb_sel)
            if not items:
                self.log.warning("ppomppu list page %d: no rows matched '%s' — site layout may have changed", page, row_sel)
            listed.extend(items)

        products: list[Product] = []
        fetched = 0
        for item in listed:
            hay = f"{item.get('shop') or ''} {item['title']}".lower()
            if not any(k in hay for k in shop_keywords):
                continue
            if self.ctx.db.is_seen(self.name, item["external_id"]):
                continue
            if item["price"] is None:
                self.log.debug("skip (no price in title): %s", item["title"])
                self.ctx.db.mark_seen(self.name, item["external_id"])
                continue
            if fetched >= max_detail:
                break

            if fetched > 0 and delay > 0:
                await asyncio.sleep(delay)
            fetched += 1
            try:
                detail_html = await self._get(item["post_url"])
            except Exception as e:  # noqa: BLE001
                self.log.warning("detail fetch failed %s: %s", item["post_url"], e)
                continue

            product_url: str | None = None
            for cand in find_coupang_urls(detail_html):
                product_url = await self._resolve_product_url(cand)
                if product_url:
                    break
            self.ctx.db.mark_seen(self.name, item["external_id"], extract_product_id(product_url) if product_url else None)
            if not product_url:
                self.log.info("no coupang product url in post %s (%s)", item["external_id"], item["title"])
                continue

            pid = extract_product_id(product_url)
            if not pid:
                continue
            products.append(
                Product(
                    source=self.name,
                    product_id=pid,
                    name=item["name"],
                    price=int(item["price"]),
                    url=product_url,
                    image_url=item.get("thumb"),
                    is_free_shipping=("무료" in (item.get("shipping") or "")) or None,
                    external_id=item["external_id"],
                    extra={"post_url": item["post_url"], "shipping": item.get("shipping"), "shop": item.get("shop")},
                )
            )
        self.log.info("ppomppu: %d listed, %d coupang candidates fetched, %d products", len(listed), fetched, len(products))
        return products
