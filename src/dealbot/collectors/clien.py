"""클리앙 알뜰구매(jirum) 게시판 수집기 (주인 승인 10/23).

robots.txt 규칙을 지킨다 (www.clien.net/robots.txt: `Allow:/service/board/`, `Disallow: /*?*`):
- 목록은 쿼리 없는 첫 페이지 `https://www.clien.net/service/board/jirum` 하나만 읽는다. 쿼리 문자열(?od=…, ?po=…)·페이지 넘기기는 하지 않는다.
- 글 본문(`/service/board/jirum/<id>`)은 쇼핑몰 링크를 찾을 때만 열고, 목록 링크에 붙은 `?od=…` 같은 쿼리는 떼고 경로만 쓴다.
- 요청 사이 request_delay_seconds(기본 3초), 한 판에 max_detail_fetch_per_run(기본 8)개까지만 본문을 연다.
- 다른 수집기와 같은 점잖은 HTTP 클라이언트(http.user_agent: 봇이라고 밝힌 UA)와 받을 형식·언어 헤더만 쓴다 — 위장 없음.

목록 행: 제목 / 글 주소 / 공감(추천) / 댓글 / 조회수 / 시간. 품절·종료 표시가 붙은 행은 건너뛴다.
셀렉터는 options.selectors 로 바꿀 수 있고, 아무 행도 못 찾으면 algumon 처럼 페이지 구조 요약을 에러 로그에 남긴다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from dealbot.collectors.algumon import _all, _first, dom_summary
from dealbot.collectors.base import BaseCollector
from dealbot.collectors.ppomppu import (
    HTML_HEADERS,
    decode_html,
    find_shop_urls,
    guess_kind,
    parse_title,
)
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry
from dealbot.soldout import looks_sold_out
from dealbot.utils.retry import retry_async

BASE_URL = "https://www.clien.net"
LIST_URL = BASE_URL + "/service/board/jirum"  # 쿼리 없는 맨 주소만 (robots: Disallow /*?*)

DEFAULT_SELECTORS = {
    # 공지·홍보 줄(div.list_item.notice / .hongbo)은 글 주소가 /jirum/<번호> 꼴이 아니라 아래에서 걸러진다
    "row": "div.list_item.jirum, div.list_item",
    "title": "span.list_subject a:not(.list_reply), a.list_subject, a[data-role='list-title-text']",
    "likes": ".list_symph em, .list_votes",  # 공감 수
    "comments": ".list_reply .rSymph05, .rSymph05",
    "views": ".list_hit .hit",
    "time": ".list_time .timestamp, .list_time .time",
    "sold_label": ".icon_info",  # '품절'·'종료' 라벨 칸
    "sold_class": "sold_out",  # 행에 붙는 CSS 클래스 (쉼표로 여러 개 가능)
}

_POST_PATH_RE = re.compile(r"^/service/board/jirum/(\d+)/?$")
_SOLD_LABEL_RE = re.compile(r"품절|종료")
_LEAD_NOISE_RE = re.compile(r"^\s*(?:끌올|재업|재게시|re)\s*[:\-]?\s*", re.IGNORECASE)  # '끌올 [네이버] …' → 머리말을 읽을 수 있게
_COUNT_RE = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*([kKmM])?")
_SKIP_LINK_HOSTS = ("clien.net",)  # 클리앙 안의 링크(다른 글·이미지 서버)는 쇼핑몰 링크가 아니다


def _count(text: str | None) -> int | None:
    """'6' → 6, '1,234' → 1234, '13.1 k' → 13100, '4.6 M' → 4600000."""
    if not text:
        return None
    m = _COUNT_RE.search(text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return int(num * (1000 if unit == "k" else 1_000_000 if unit == "m" else 1))


def post_path(href: str) -> tuple[str, str] | None:
    """목록 링크 → (글 번호, 쿼리 없는 글 주소). `/service/board/jirum/<번호>` 꼴이 아니면 None.
    `?od=T31&po=0…`·`#comment-point` 는 버린다 (robots: Disallow /*?*)."""
    path = urlparse(href.strip()).path
    m = _POST_PATH_RE.match(path)
    if not m:
        return None
    return m.group(1), BASE_URL + path.rstrip("/")


def is_allowed_url(url: str) -> bool:
    """우리가 요청해도 되는 주소: www.clien.net, 쿼리 없음, /service/board/ 아래 (robots 가 막는 sold·hongbo 제외)."""
    p = urlparse(url)
    if p.scheme != "https" or p.netloc != "www.clien.net" or p.query or p.params:
        return False
    if not p.path.startswith("/service/board/"):
        return False
    return not p.path.startswith(("/service/board/sold/", "/service/board/hongbo/"))


def _row_sold_out(row: Any, title: str, selectors: dict[str, str]) -> bool:
    classes = set(row.get("class") or [])
    if any(c.strip() in classes for c in selectors.get("sold_class", "").split(",") if c.strip()):
        return True
    label_el = _first(row, selectors.get("sold_label", ""))
    if label_el is not None and _SOLD_LABEL_RE.search(label_el.get_text(" ", strip=True)):
        return True
    return looks_sold_out(title)


def parse_list(html: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """목록 HTML → 행 목록 (품절 행도 sold_out=True 로 돌려줌). 같은 글이 두 번 나오면 처음 것만."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in _all(soup, selectors["row"]):
        a = _first(row, selectors["title"])
        if a is None:
            continue
        parsed_url = post_path(a.get("href") or "")
        if parsed_url is None:
            continue
        ext_id, url = parsed_url
        title = a.get_text(" ", strip=True)
        if not title or ext_id in seen_ids:
            continue
        seen_ids.add(ext_id)
        likes_el = _first(row, selectors["likes"])
        comments_el = _first(row, selectors["comments"])
        views_el = _first(row, selectors["views"])
        time_el = _first(row, selectors["time"])
        comments = _count(comments_el.get_text(" ", strip=True)) if comments_el is not None else None
        if comments is None:
            comments = _count(str(row.get("data-comment-count") or ""))
        clean_title = _LEAD_NOISE_RE.sub("", title)
        parsed = parse_title(clean_title)
        items.append(
            {
                "external_id": ext_id,
                "title": title,
                "post_url": url,
                "shop_tag": parsed["shop_tag"],
                "name": parsed["name"],
                "price": parsed["price"],
                "shipping": parsed["shipping"],
                "recommend": _count(likes_el.get_text(" ", strip=True)) if likes_el is not None else None,
                "comments": comments,
                "views": _count(views_el.get_text(" ", strip=True)) if views_el is not None else None,
                "time": time_el.get_text(" ", strip=True) if time_el is not None else None,
                "sold_out": _row_sold_out(row, title, selectors),
            }
        )
    return items


def parse_detail_links(html: str, shop: Shop | None) -> list[str]:
    """글 본문 HTML 에서 쇼핑몰 링크 후보 (본문·구매링크 칸만 보고, 클리앙 안의 링크는 뺌)."""
    soup = BeautifulSoup(html, "html.parser")
    parts = soup.select("div.attached_link, div.post_article")
    scope = "".join(str(p) for p in parts) if parts else html
    urls = find_shop_urls(scope, shop)
    return [u for u in urls if not (urlparse(u).hostname or "").endswith(_SKIP_LINK_HOSTS)]


@register("clien")
class ClienCollector(BaseCollector):
    requires_coupang = False

    @property
    def registry(self) -> ShopRegistry:
        reg = getattr(self.ctx, "shops", None)
        return reg if reg is not None else self.ctx.settings.shop_registry()

    async def _get(self, url: str) -> str:
        if not is_allowed_url(url):  # 쿼리가 붙었거나 robots 가 막는 곳이면 요청하지 않는다
            raise ValueError(f"clien: robots.txt 규칙상 요청하지 않는 주소: {url}")
        http_cfg = self.ctx.settings.http

        async def _do() -> str:
            # 리다이렉트를 따라가면 쿼리가 붙은 주소를 읽게 될 수 있어 따라가지 않는다 (3xx 는 실패로 기록)
            resp = await self.ctx.http.get(url, headers=HTML_HEADERS, follow_redirects=False)
            resp.raise_for_status()
            return decode_html(resp)

        return await retry_async(_do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label=f"GET {url}")

    def _pick_shop(self, item: dict[str, Any], registry: ShopRegistry) -> Shop | None:
        tag = item.get("shop_tag")
        shop = registry.by_alias(tag) if tag else None
        return shop or registry.by_title_hint(_LEAD_NOISE_RE.sub("", item["title"]))  # 머리말이 없거나 모르는 말이어도 토스 단서가 있으면

    async def collect(self) -> list[Product]:
        selectors = {**DEFAULT_SELECTORS, **(self.opt("selectors") or {})}
        delay = float(self.opt("request_delay_seconds", 3))
        max_detail = int(self.opt("max_detail_fetch_per_run", 8))
        only_shops = {s.lower() for s in self.opt("shops", [])}
        unknown_policy = self.opt("unknown_shop", "skip")
        registry = self.registry
        interest = self.ctx.settings.deal.interest

        html = await self._get(LIST_URL)  # 목록 주소는 고정: 쿼리·페이지 넘기기 없음
        items = parse_list(html, selectors)
        if not items:
            summary = dom_summary(html)
            self.log.warning("clien: no rows matched — page structure: %s", summary)
            self.ctx.db.log_event("ERROR", f"collector:{self.name}", f"no rows matched selectors. DOM: {summary}")
            raise RuntimeError(
                f"클리앙 목록에서 행을 못 찾음 (셀렉터 조정 필요). /html {LIST_URL} 로 원문을 받아 Claude 에게 주세요. DOM 요약: {summary[:600]}"
            )

        products: list[Product] = []
        fetched = 0
        for item in items:
            if item["sold_out"]:
                continue
            shop = self._pick_shop(item, registry)
            if shop is None and unknown_policy != "raw":
                continue
            if shop is not None and (not shop.enabled or shop.link_mode == "skip"):
                continue
            if only_shops and (shop is None or shop.key not in only_shops):
                continue
            if shop is None:
                shop = Shop(key="unknown", name=item.get("shop_tag") or "기타", link_mode="raw")
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
                detail_html = await self._get(item["post_url"])  # 경로만 (쿼리 없음)
                urls = parse_detail_links(detail_html, None if shop.key == "unknown" else shop)
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
        self.log.info("clien: %d listed, %d fetched, %d products", len(items), fetched, len(products))
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
            shipping=item.get("shipping"),
            external_id=item["external_id"],
            recommend_count=item.get("recommend"),
            view_count=item.get("views"),
            comment_count=item.get("comments"),
            extra={"post_url": item["post_url"], "title": item["title"], "posted_at": item.get("time")},
        )

