"""뽐뿌 핫딜 게시판 크롤러 (모든 쇼핑몰).

- 목록에서 제목 "[쇼핑몰] 상품명 (가격/배송)" 을 파싱하고, 등록된 쇼핑몰(config.shops) 글만 고른다.
- 새 글은 상세 페이지를 열어 쇼핑몰 링크를 찾는다. 이미 본 글은 다시 열지 않지만,
  추천 수가 기준 이상으로 올라오면 저장된 정보로 다시 판정 대상에 올린다.
- 사이트 구조가 바뀌면 options.*_selector 만 고치면 된다.
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
from dealbot.shops import Shop, ShopRegistry, find_urls
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price
from dealbot.utils.urls import canonical_product_url, is_short_affiliate_link

BASE_URL = "https://www.ppomppu.co.kr"
LIST_URL = BASE_URL + "/zboard/zboard.php"

# "[쿠팡] 상품명 (12,900원/무료)" 형태
_TITLE_RE = re.compile(r"^\s*\[(?P<shop>[^\]]+)\]\s*(?P<name>.+?)\s*(?:\((?P<meta>[^()]*)\))?\s*$")
_COUPON_WORDS = ("쿠폰", "할인코드", "적립", "캐시백")
_EVENT_WORDS = ("이벤트", "응모", "추첨", "무료체험", "증정")
_SKIP_HOSTS = ("ppomppu.co.kr",)


def parse_title(title: str) -> dict[str, Any]:
    """제목 → shop_tag / name / price / shipping."""
    m = _TITLE_RE.match(title.strip())
    if not m:
        return {"shop_tag": None, "name": title.strip(), "price": None, "shipping": None}
    meta = (m.group("meta") or "").strip()
    price_text, _, shipping = meta.partition("/")
    return {
        "shop_tag": m.group("shop").strip(),
        "name": m.group("name").strip(),
        "price": parse_price(price_text) if price_text else None,
        "shipping": shipping.strip() or None,
    }


def guess_kind(name: str, price: int | None) -> str:
    low = name.lower()
    if any(w in low for w in _EVENT_WORDS):
        return "event"
    if price is None and any(w in low for w in _COUPON_WORDS):
        return "coupon"
    if price is None:
        return "event"
    return "hotdeal"


def _first_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d[\d,]*", text)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


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


def parse_list_page(
    html: str,
    *,
    row_selector: str,
    title_selector: str,
    thumb_selector: str,
    rec_selector: str = "td.baseList-rec",
    views_selector: str = "td.baseList-views",
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    for row in soup.select(row_selector):
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
        rec_el = row.select_one(rec_selector) if rec_selector else None
        views_el = row.select_one(views_selector) if views_selector else None
        items.append(
            {
                "external_id": str(no),
                "title": title,
                "post_url": urljoin(LIST_URL, href),
                "thumb": thumb,
                "recommend": _first_int(rec_el.get_text(" ", strip=True) if rec_el else None),
                "views": _first_int(views_el.get_text(" ", strip=True) if views_el else None),
                **parse_title(title),
            }
        )
    return items


def find_shop_urls(html: str, shop: Shop | None) -> list[str]:
    """글 상세 HTML 에서 링크 후보를 우선순위대로: 해당 쇼핑몰 도메인 > 그 외 외부 링크."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            candidates.append(href)
    candidates.extend(find_urls(html))

    def external(u: str) -> bool:
        host = urlparse(u).netloc.lower()
        return bool(host) and not any(host.endswith(h) for h in _SKIP_HOSTS)

    uniq = [u for u in dict.fromkeys(candidates) if external(u)]
    if shop is None:
        return uniq
    matched = [u for u in uniq if shop.matches_url(u)]
    # 쿠팡은 상품 페이지 > 단축링크 순
    if shop.key == "coupang":
        matched.sort(key=lambda u: 0 if canonical_product_url(u) else (1 if is_short_affiliate_link(u) else 2))
    return matched


@register("ppomppu")
class PpomppuCollector(BaseCollector):
    requires_coupang = False

    @property
    def registry(self) -> ShopRegistry:
        reg = getattr(self.ctx, "shops", None)
        return reg if reg is not None else self.ctx.settings.shop_registry()

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> str:
        http_cfg = self.ctx.settings.http

        async def _do() -> str:
            resp = await self.ctx.http.get(url, params=params, follow_redirects=True)
            resp.raise_for_status()
            return decode_html(resp)

        return await retry_async(
            _do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label=f"GET {url}"
        )

    async def _resolve_deal_url(self, url: str, shop: Shop | None) -> str:
        """쿠팡이면 정규화(타인 단축링크는 원본으로), 그 외는 그대로."""
        if shop is not None and shop.key == "coupang":
            canon = canonical_product_url(url)
            if canon:
                return canon
            if is_short_affiliate_link(url) and self.ctx.settings.links.resolve_short_links:
                try:
                    resp = await self.ctx.http.get(url, follow_redirects=True)
                    canon = canonical_product_url(str(resp.url))
                    if canon:
                        return canon
                except httpx.HTTPError as e:
                    self.log.debug("short link resolve failed %s: %s", url, e)
        return url

    def _build_product(self, item: dict[str, Any], shop: Shop, deal_url: str) -> Product:
        name = item["name"]
        price = item.get("price")
        kind = guess_kind(name, price)
        return Product(
            source=self.name,
            product_id=ShopRegistry.product_key(shop.key, deal_url),
            shop=shop.key,
            deal_kind=kind,
            name=name,
            price=int(price or 0),
            url=deal_url,
            image_url=item.get("thumb"),
            shipping=item.get("shipping"),
            is_free_shipping=("무료" in (item.get("shipping") or "")) or None,
            external_id=item["external_id"],
            recommend_count=item.get("recommend"),
            view_count=item.get("views"),
            extra={"post_url": item["post_url"], "shop_tag": item.get("shop_tag"), "title": item["title"]},
        )

    async def collect(self) -> list[Product]:
        board = self.opt("board_id", "ppomppu")
        pages = int(self.opt("pages", 1))
        delay = float(self.opt("request_delay_seconds", 2))
        max_detail = int(self.opt("max_detail_fetch_per_run", 15))
        row_sel = self.opt("list_row_selector", "tr.baseList")
        title_sel = self.opt("title_selector", "a.baseList-title")
        thumb_sel = self.opt("thumb_selector", "img")
        rec_sel = self.opt("rec_selector", "td.baseList-rec")
        views_sel = self.opt("views_selector", "td.baseList-views")
        only_shops = {s.lower() for s in self.opt("shops", [])}  # 비우면 등록된 모든 쇼핑몰
        unknown_policy = self.opt("unknown_shop", "skip")  # skip | raw
        reyield_min_rec = int(self.opt("reyield_min_recommend", self.ctx.settings.deal.community_min_recommend))
        registry = self.registry

        listed: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            if page > 1:
                await asyncio.sleep(delay)
            html = await self._get(LIST_URL, params={"id": board, "page": page})
            items = parse_list_page(
                html, row_selector=row_sel, title_selector=title_sel, thumb_selector=thumb_sel,
                rec_selector=rec_sel, views_selector=views_sel,
            )
            if not items:
                self.log.warning("ppomppu list page %d: no rows matched '%s' — site layout may have changed", page, row_sel)
            listed.extend(items)

        products: list[Product] = []
        fetched = 0
        for item in listed:
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
                # 이미 본 글: 추천 수가 올라왔으면 저장된 링크로 다시 판정 대상에
                rec = item.get("recommend") or 0
                if reyield_min_rec > 0 and rec >= reyield_min_rec:
                    seen = self.ctx.db.seen_item(self.name, ext)
                    if seen and seen[0] and seen[1]:
                        p = self._build_product(item, shop, seen[1])
                        p.product_id = seen[0]
                        products.append(p)
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

            urls = find_shop_urls(detail_html, None if shop.key == "unknown" else shop)
            deal_url = await self._resolve_deal_url(urls[0], shop) if urls else None
            if not deal_url:
                # 링크가 없으면 뽐뿌 글 자체를 링크로 (쿠폰/이벤트 안내글 등)
                deal_url = item["post_url"]
            product = self._build_product(item, shop, deal_url)
            self.ctx.db.mark_seen(self.name, ext, product.product_id, url=deal_url)
            products.append(product)

        self.log.info("ppomppu(%s): %d listed, %d detail fetched, %d products", board, len(listed), fetched, len(products))
        return products
