"""네이버 쇼핑 검색 API 로 스마트스토어·브랜드스토어 딜의 큰 상품 사진 찾기.

뽐뿌의 네이버 딜은 게시판 목록 썸네일(120px)뿐이고, 스토어 페이지는 429 로 자주 막힌다.
상품명으로 검색해서 '같은 상품'(검색 결과의 링크·productId 에 딜 주소와 같은 스토어 상품번호가 있고,
스토어·몰 이름이 둘 다 있으면 서로 같은 것)일 때만 그 사진을 쓴다. 비슷한 다른 상품의 사진은 쓰지 않는다.
NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없으면 건너뛴다 (로그 한 번). 키 값은 로그에 남기지 않는다.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from dealbot.enrich import is_naver_store
from dealbot.media.imagecheck import is_generic_image
from dealbot.models import Product
from dealbot.utils.text import clean_name

log = logging.getLogger(__name__)

SEARCH_URL = "https://openapi.naver.com/v1/search/shop.json"
_PRODUCT_NO = re.compile(r"/products/(\d+)")
# 키가 틀렸거나 API 를 더 쓸 수 없음 (401·403 키/권한, 404·410 종료) → 다시 시작할 때까지 안 부름
_DEAD = (401, 403, 404, 410)
_CACHE_MAX = 500


class _Unavailable(Exception):
    pass


def store_product_no(url: str | None) -> str | None:
    """스마트스토어·브랜드스토어 상품 주소의 상품번호 ('smartstore.naver.com/가게/products/123' → '123')."""
    if not url or not is_naver_store(url):
        return None
    m = _PRODUCT_NO.search(urlparse(url).path or "")
    return m.group(1) if m else None


def store_name(url: str | None) -> str | None:
    """스토어 주소의 가게 이름 칸 ('/가게/products/1' → '가게'). 'main'(가게를 안 밝힌 주소)이면 None."""
    if not url or not is_naver_store(url):
        return None
    parts = [x for x in (urlparse(url).path or "").split("/") if x]
    if len(parts) < 2 or parts[1] != "products" or parts[0].lower() == "main":
        return None
    return parts[0].lower()


def _norm(text: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def search_queries(name: str) -> list[str]:
    """검색어: 꾸밈·괄호(가격·배송·기간)를 걷어낸 상품명, 못 찾으면 앞 네 낱말."""
    s = clean_name(name)
    s = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", s)
    words = re.sub(r"[^\w.+%/-]+", " ", s).split()
    out: list[str] = []
    for q in (" ".join(words)[:100].strip(), " ".join(words[:4])):
        if q and q not in out:
            out.append(q)
    return out


def same_product(item: dict[str, Any], deal_url: str, mall: str | None = None) -> bool:
    """검색 결과가 딜과 같은 상품인지: 링크(스토어 주소)의 상품번호 또는 productId 가 딜 주소의 상품번호와 같아야 하고,
    가게 이름(주소)·몰 이름이 양쪽에 다 있으면 서로 같아야 한다. 이름만 비슷한 상품은 안 됨."""
    no = store_product_no(deal_url)
    if not no:
        return False
    link = str(item.get("link") or "").strip()
    link_no = store_product_no(link)
    if link_no:
        if link_no != no:
            return False
    else:
        host = urlparse(link).netloc.lower().split(":")[0]
        pid = str(item.get("productId") or "").strip()
        if pid != no or not (host == "naver.com" or host.endswith(".naver.com")):
            return False  # 다른 몰 링크의 productId 는 스토어 상품번호와 다른 번호 체계
    a, b = store_name(link), store_name(deal_url)
    if a and b and a != b:
        return False
    their, ours = _norm(item.get("mallName")), _norm(mall)
    return not (their and ours and their not in ours and ours not in their)


def _deal_mall(p: Product) -> str | None:
    """딜에서 알고 있는 판매처 이름 (스토어 페이지 JSON-LD 판매자, 정품 확인 때 남김)."""
    auth = p.extra.get("auth")
    seller = auth.get("seller") if isinstance(auth, dict) else None
    return str(seller) if seller else None


class NaverShopSearch:
    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str | None,
        client_secret: str | None,
        *,
        timeout: float = 20,
        display: int = 40,
    ) -> None:
        self.http = http
        self._id = client_id or None
        self._secret = client_secret or None
        self.timeout = timeout
        self.display = max(1, min(100, display))
        self.disabled_reason: str | None = None
        self._told_missing = False
        self._cache: dict[str, str | None] = {}  # 상품번호 → 찾은 사진 (못 찾았으면 None)

    @property
    def configured(self) -> bool:
        return bool(self._id and self._secret)

    async def find_image(self, product: Product) -> str | None:
        """같은 상품(상품번호 일치)의 검색 결과 사진 주소. 스토어 딜이 아니거나, 키가 없거나, 같은 상품이 없으면 None."""
        no = store_product_no(product.url)
        if not no:
            return None
        if not self.configured:
            if not self._told_missing:
                self._told_missing = True
                log.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET not set — 네이버 쇼핑 검색으로 큰 사진 찾기는 건너뜀")
            return None
        if self.disabled_reason:
            return None
        if no in self._cache:
            return self._cache[no]
        mall = _deal_mall(product)
        for q in search_queries(product.name):
            try:
                items = await self._search(q)
            except _Unavailable:
                return None
            except (httpx.HTTPError, ValueError) as e:  # 일시 오류(429·5xx·연결): 기억하지 않고 다음에 다시
                log.info("naver shop search failed for %s: %s", product.name[:40], type(e).__name__)
                return None
            for item in items:
                if not same_product(item, product.url, mall):
                    continue
                img = str(item.get("image") or "").strip()
                if img.startswith("//"):
                    img = "https:" + img
                found = img if img.startswith("http") and not is_generic_image(img) else None
                log.info("naver shop search: %s → 같은 상품(상품번호 %s, %s)%s", product.name[:40], no,
                         item.get("mallName") or "-", "" if found else " 이지만 사진 없음")
                self._remember(no, found)
                return found
        log.info("naver shop search: %s — 상품번호 %s 와 같은 상품 없음", product.name[:40], no)
        self._remember(no, None)
        return None

    async def _search(self, query: str) -> list[dict[str, Any]]:
        resp = await self.http.get(
            SEARCH_URL,
            params={"query": query, "display": self.display, "sort": "sim"},
            headers={"X-Naver-Client-Id": self._id or "", "X-Naver-Client-Secret": self._secret or ""},
            timeout=self.timeout,
        )
        if resp.status_code in _DEAD:
            self.disabled_reason = f"HTTP {resp.status_code}"
            log.warning("naver shop search: HTTP %s — 키가 틀렸거나 API 를 쓸 수 없음. 다시 시작할 때까지 끔", resp.status_code)
            raise _Unavailable
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []

    def _remember(self, no: str, image: str | None) -> None:
        if len(self._cache) >= _CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[no] = image
