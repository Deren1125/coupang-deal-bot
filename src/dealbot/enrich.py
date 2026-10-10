"""상품 페이지 메타데이터 보강 (OpenGraph / JSON-LD).

토스 쉐어링크(toss.im/_m/...) 같은 상품 페이지에서 제목·이미지·가격·정상가·별점·리뷰 수를 읽어
Product 의 빈 칸을 채운다. 어떤 몰이든 og:* / JSON-LD Product 를 쓰면 동작한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from dealbot.models import Product
from dealbot.utils.text import parse_price

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PageMeta:
    title: str | None = None
    image: str | None = None  # og:image (없으면 JSON-LD Product.image)
    ld_image: str | None = None  # JSON-LD Product.image — 상품 자체 사진이라 og:image(공유용 대표 이미지)보다 먼저 씀
    description: str | None = None
    price: int | None = None
    original_price: int | None = None
    rating: float | None = None
    review_count: int | None = None
    final_url: str | None = None
    available: bool | None = None  # 재고 표시. True 있음 / False 품절 / None 모름
    seller: str | None = None  # JSON-LD offers.seller.name (정품 확인용)
    text: str = ""  # 페이지에 보이는 글 (앞부분만, 정품 확인용)


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
            meta.ld_image = meta.ld_image or (img if isinstance(img, str) else None)
            meta.image = meta.image or meta.ld_image
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
            if meta.seller is None and isinstance(offers, dict):
                sel = offers.get("seller")
                if isinstance(sel, dict) and sel.get("name"):
                    meta.seller = str(sel["name"]).strip()
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
    meta.text = text
    if meta.rating is None:
        m = re.search(r"(?:별점|평점)\s*[:：]?\s*(\d(?:\.\d)?)", text)
        if m:
            meta.rating = float(m.group(1))
    if meta.review_count is None:
        m = re.search(r"리뷰\s*[:：]?\s*(\d[\d,]*)\s*(?:건|개)?", text)
        if m:
            meta.review_count = int(m.group(1).replace(",", ""))
    return meta


# 최종 주소가 이런 곳이면 상품 페이지가 아님 (홈·로그인·검색·기획전/이벤트로 돌려보낸 경우) → 거기 og:image 는 상품 사진이 아님
_NOT_PRODUCT_PATH = re.compile(
    r"log-?in|sign-?in|logon"
    r"|/(?:auth|member|members|search|event|events|promotion|promotions|exhibition|exhibitions|planshop|plan|special"
    r"|campaigns?)(?:[/.]|$)"
    r"|/display/main(?:[/.]|$)"  # 롯데온 '/p/display/main/…' 같은 몰 첫 화면
    r"|^/(?:main|home|index)(?:\.\w+)?/?$|/(?:main|index)\.(?:html?|php|jsp|aspx?|do)$",  # '/MW/html/main.html'
    re.I,
)
# 분류 목록 ('/np/categories/186764'). 카페24 상품 주소('/product/이름/123/category/24/display/1/')엔 분류 칸이 같이 있어 상품 표시가 있으면 둔다
_LISTING_PATH = re.compile(r"/categor(?:y|ies)(?:[/.]|$)", re.I)
_PRODUCT_MARK = re.compile(r"/(?:products?|goods)/", re.I)
_NOT_PRODUCT_HOST = re.compile(r"^(?:login|nid|accounts?|auth|member|signin|search|event|events|promotion|sale|campaign)\.", re.I)
# 스마트스토어·브랜드스토어는 '/{스토어}' 가 스토어 첫 화면(og:image = 스토어 프로필·로고) — '/products/' 가 있어야 상품
_STORE_HOSTS = ("smartstore.naver.com", "brand.naver.com")


def is_naver_store(url: str | None) -> bool:
    """네이버 스마트스토어·브랜드스토어 주소인지 (m. 주소 포함)."""
    if not url:
        return False
    host = urlparse(url).netloc.lower().split(":")[0]
    return any(host == h or host.endswith("." + h) for h in _STORE_HOSTS)


def retry_after_seconds(value: str | None, *, now: float | None = None) -> float | None:
    """Retry-After 머리글 → 기다릴 초. 숫자('7') 또는 HTTP 날짜. 없거나 못 읽으면 None."""
    v = (value or "").strip()
    if not v:
        return None
    if v.isdigit():
        return float(v)
    try:
        when = parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, when.timestamp() - (time.time() if now is None else now))


def is_product_page(url: str | None) -> bool:
    """상품 페이지로 보이는 주소인지. 홈(경로 없음)·로그인·검색·이벤트/기획전·분류 목록·스토어 첫 화면이면 False."""
    if not url:
        return False
    u = urlparse(url)
    host = u.netloc.lower().split(":")[0]
    path = u.path or "/"
    if path.rstrip("/") == "" or _NOT_PRODUCT_HOST.match(host):
        return False
    if any(host == h or host.endswith("." + h) for h in _STORE_HOSTS) and "/products/" not in path:
        return False
    if _LISTING_PATH.search(path) and not _PRODUCT_MARK.search(path):
        return False
    return not _NOT_PRODUCT_PATH.search(path)


def page_meta_at(html: str, final_url: str) -> PageMeta:
    """페이지 HTML → PageMeta (최종 주소 기준). '//cdn…/a.jpg', '/upload/1.jpg' 같은 상대 사진 주소는 페이지 주소 기준으로
    (그대로 두면 사진을 못 받음)."""
    meta = parse_page_meta(html)
    meta.final_url = final_url
    if meta.image:
        meta.image = urljoin(final_url, meta.image)
    if meta.ld_image:
        meta.ld_image = urljoin(final_url, meta.ld_image)
    return meta


def page_images(meta: PageMeta) -> list[str]:
    """상품 페이지에서 상품 사진으로 써도 되는 주소 (JSON-LD Product.image 먼저, 그다음 og:image).
    최종 주소(리디렉션 뒤)가 상품 페이지가 아니거나, 로고·기본 배너 주소면 빈 목록. final_url 을 모르면(직접 만든 meta) 주소만 본다."""
    from dealbot.media.imagecheck import is_board_thumb, is_generic_image

    if meta.final_url is not None and not is_product_page(meta.final_url):
        return []
    out: list[str] = []
    for url in (meta.ld_image, meta.image):
        if url and url not in out and not is_generic_image(url) and not is_board_thumb(url):
            out.append(url)
    return out


class PageEnricher:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout: float = 20,
        store_min_interval: float = 3.0,
        store_attempts: int = 3,
        store_backoff: float = 2.0,
        store_max_wait: float = 30.0,
        store_breaker_after: int = 2,
        store_breaker_seconds: float = 600.0,
        store_breaker_max_seconds: float = 7200.0,
        store_cache_seconds: float = 300.0,
    ) -> None:
        self.http = http
        self.timeout = timeout
        # 네이버 스토어(스마트스토어·브랜드스토어)는 몰아서 읽으면 429 → 요청 사이 간격 + Retry-After 를 지키는 몇 번의 재시도
        self.store_min_interval = store_min_interval
        self.store_attempts = max(1, store_attempts)
        self.store_backoff = store_backoff
        self.store_max_wait = store_max_wait
        self._store_lock = asyncio.Lock()
        self._store_next = 0.0  # 스토어에 다음 요청을 보내도 되는 시각 (_clock 기준)
        # 이 서버에서 스토어는 거의 늘 429 (10/10: 한 딜에 수집·정품 확인·품절 확인이 각자 3번씩, 상품당 하루 13~88번).
        # 연속으로 막힌 판(재시도까지 다 실패)이 store_breaker_after 번이면 스토어 전체를 쉬고(10분→20분→… 최대 2시간), 한 번 열리면 처음으로
        self.store_breaker_after = store_breaker_after
        self.store_breaker = store_breaker_seconds
        self.store_breaker_max = store_breaker_max_seconds
        self._store_strikes = 0
        self._cool_noted = 0.0  # '쉬는 중' 로그를 쉬는 구간마다 한 번만
        # 같은 딜을 수집·정품 확인·발행 사진에서 연달아 읽으므로, 읽은 스토어 페이지는 잠깐 기억해 다시 묻지 않는다
        self.store_cache = store_cache_seconds
        self._store_cache: dict[str, tuple[float, PageMeta]] = {}
        self._sleep = asyncio.sleep  # 테스트에서 가짜로 바꿔 끼움 (실제로 안 잠)
        self._clock = time.monotonic

    async def _store_turn(self) -> bool:
        """스토어에 요청할 차례를 기다린다. 남은 시간이 store_max_wait 보다 길면(긴 Retry-After) 기다리지 않고 False."""
        async with self._store_lock:
            # 기다리는 사이 다른 요청이 429 로 쉬는 시간(Retry-After)을 늘렸을 수 있어 깨어날 때마다 다시 본다
            while (wait := self._store_next - self._clock()) > 0:
                if wait > self.store_max_wait:
                    return False
                await self._sleep(wait)
            self._store_next = max(self._store_next, self._clock() + self.store_min_interval)
            return True

    async def _get_store(self, url: str) -> httpx.Response | None:
        """네이버 스토어 페이지 읽기: 요청 사이 store_min_interval 초, 429·5xx 는 Retry-After(없으면 지수 백오프+지터)만큼 쉬고
        store_attempts 번까지. Retry-After 가 store_max_wait 보다 길면 더 묻지 않고, 그 시각 전에는 스토어에 요청하지 않는다.
        차례를 못 받으면 None, 아니면 마지막 응답."""
        resp: httpx.Response | None = None
        for i in range(1, self.store_attempts + 1):
            if not await self._store_turn():
                if self._clock() >= self._cool_noted:
                    self._cool_noted = self._store_next
                    left = (self._store_next - self._clock()) / 60
                    log.info("enrich: naver store cooling down (%.0f분 남음) — skip %s", left, url)
                return resp
            resp = await self.http.get(url, follow_redirects=True, timeout=self.timeout)
            if resp.status_code != 429 and resp.status_code < 500:
                self._store_strikes = 0
                return resp
            wait = retry_after_seconds(resp.headers.get("retry-after"))
            if wait is None:
                wait = self.store_backoff * (2 ** (i - 1)) * (0.8 + random.random() * 0.4)
            # 다음 차례는 wait 뒤 (다른 딜의 스토어 요청도 같이 기다림)
            self._store_next = max(self._store_next, self._clock() + wait)
            if wait > self.store_max_wait:
                log.info("enrich: HTTP %s for %s — Retry-After %.0fs, 이번엔 건너뜀", resp.status_code, url, wait)
                self._strike(resp.status_code)
                return resp
            if i < self.store_attempts:
                log.info("enrich: HTTP %s for %s — %.1fs 뒤 다시 (%d/%d)", resp.status_code, url, wait, i, self.store_attempts)
        self._strike(resp.status_code if resp is not None else 0)
        return resp

    def _strike(self, status: int) -> None:
        """한 판(재시도 포함)이 끝내 막힘. store_breaker_after 번 연속이면 스토어 전체를 점점 길게 쉰다."""
        self._store_strikes += 1
        over = self._store_strikes - self.store_breaker_after
        if self.store_breaker_after <= 0 or over < 0:
            return
        pause = min(self.store_breaker_max, self.store_breaker * (2 ** min(over, 16)))
        self._store_next = max(self._store_next, self._clock() + pause)
        log.warning(
            "enrich: naver store %d판 연속 막힘(HTTP %s) — %.0f분 동안 스토어 페이지를 읽지 않음", self._store_strikes, status, pause / 60
        )

    async def fetch(self, url: str) -> PageMeta | None:
        store = is_naver_store(url)
        if store:
            hit = self._store_cache.get(url)
            if hit is not None and hit[0] > self._clock():
                return hit[1]
        try:
            if store:
                resp = await self._get_store(url)
                if resp is None:
                    return None
            else:
                resp = await self.http.get(url, follow_redirects=True, timeout=self.timeout)
            if resp.status_code >= 400:
                log.info("enrich: HTTP %s for %s", resp.status_code, url)
                return None
            meta = page_meta_at(resp.text, str(resp.url))
            if store and self.store_cache > 0:
                if len(self._store_cache) >= 200:
                    now = self._clock()
                    self._store_cache = {k: v for k, v in self._store_cache.items() if v[0] > now}
                    while len(self._store_cache) >= 200:
                        self._store_cache.pop(next(iter(self._store_cache)))
                self._store_cache[url] = (self._clock() + self.store_cache, meta)
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
        from dealbot.media.imagecheck import is_board_thumb

        images = page_images(meta)  # 상품 페이지가 아니거나 로고·배너면 안 씀
        if images and (not product.image_url or is_board_thumb(product.image_url)):
            product.image_url = images[0]  # 게시판 목록 썸네일보다 상품 페이지 사진을 우선
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
