"""쿠팡파트너스 Open API 클라이언트 (골드박스 / 카테고리 베스트 / 검색 / 딥링크).

엔드포인트 경로는 파트너스 문서(https://partners.coupang.com → 링크 생성 → API)와
다를 수 있으니, 4xx 가 나면 아래 PATH_* 상수를 문서와 대조하세요.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from dealbot.coupang.auth import build_authorization
from dealbot.models import Product
from dealbot.utils.retry import RetryableError, retry_async

log = logging.getLogger(__name__)

BASE_URL = "https://api-gateway.coupang.com"
_API_PREFIX = "/v2/providers/affiliate_open_api/apis/openapi/v1"
PATH_GOLDBOX = f"{_API_PREFIX}/products/goldbox"
PATH_BEST_CATEGORY = f"{_API_PREFIX}/products/bestcategories/{{category_id}}"
PATH_SEARCH = f"{_API_PREFIX}/products/search"
PATH_DEEPLINK = f"{_API_PREFIX}/deeplink"


class CoupangApiError(Exception):
    def __init__(self, message: str, *, rcode: str | None = None, status: int | None = None):
        super().__init__(message)
        self.rcode = rcode
        self.status = status


@dataclass(slots=True)
class DeeplinkResult:
    original_url: str
    shorten_url: str
    landing_url: str | None = None


class CoupangClient:
    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        http: httpx.AsyncClient,
        sub_id: str | None = None,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
        base_url: str = BASE_URL,
    ) -> None:
        self._ak = access_key
        self._sk = secret_key
        self._http = http
        self.sub_id = sub_id
        self._retries = max_retries
        self._backoff = retry_backoff
        self._base = base_url.rstrip("/")

    # ------------------------------------------------------------------ core
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(clean, doseq=True)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")

        async def _do() -> Any:
            headers = {
                "Authorization": build_authorization(method, path, query, self._ak, self._sk),
                "Content-Type": "application/json;charset=UTF-8",
            }
            resp = await self._http.request(method, url, headers=headers, json=json)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableError(f"coupang api {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                raise CoupangApiError(
                    f"coupang api HTTP {resp.status_code}: {resp.text[:300]}", status=resp.status_code
                )
            try:
                body = resp.json()
            except ValueError as e:
                raise CoupangApiError(f"invalid JSON from coupang api: {resp.text[:200]}") from e
            rcode = str(body.get("rCode", "0"))
            if rcode != "0":
                raise CoupangApiError(
                    f"coupang api rCode={rcode}: {body.get('rMessage', '')}", rcode=rcode
                )
            return body.get("data")

        return await retry_async(
            _do, attempts=self._retries, backoff=self._backoff, label=f"coupang {method} {path}"
        )

    # ------------------------------------------------------------- endpoints
    async def goldbox(self, *, limit: int | None = None, image_size: str | None = None) -> list[dict[str, Any]]:
        data = await self._request(
            "GET", PATH_GOLDBOX, params={"subId": self.sub_id, "imageSize": image_size, "limit": limit}
        )
        return list(data or [])

    async def best_category(
        self, category_id: int | str, *, limit: int = 50, image_size: str | None = None
    ) -> list[dict[str, Any]]:
        path = PATH_BEST_CATEGORY.format(category_id=category_id)
        data = await self._request(
            "GET", path, params={"limit": limit, "subId": self.sub_id, "imageSize": image_size}
        )
        return list(data or [])

    async def search(self, keyword: str, *, limit: int = 10, image_size: str | None = None) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            PATH_SEARCH,
            params={"keyword": keyword, "limit": limit, "subId": self.sub_id, "imageSize": image_size},
        )
        if isinstance(data, dict):
            return list(data.get("productData") or [])
        return list(data or [])

    async def deeplink(self, urls: list[str]) -> list[DeeplinkResult]:
        if not urls:
            return []
        body: dict[str, Any] = {"coupangUrls": urls}
        if self.sub_id:
            body["subId"] = self.sub_id
        data = await self._request("POST", PATH_DEEPLINK, json=body)
        results: list[DeeplinkResult] = []
        for item in data or []:
            results.append(
                DeeplinkResult(
                    original_url=item.get("originalUrl", ""),
                    shorten_url=item.get("shortenUrl", ""),
                    landing_url=item.get("landingUrl"),
                )
            )
        return results

    async def ping(self) -> int:
        """자격 증명 확인용: 골드박스 1건 조회."""
        items = await self.goldbox(limit=1)
        return len(items)


# ---------------------------------------------------------------- parsing
def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "")))
    except ValueError:
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").replace(",", ""))
    except ValueError:
        return None


def parse_api_product(raw: dict[str, Any], source: str) -> Product | None:
    """API 응답 한 건을 Product 로 변환. 필수 필드가 없으면 None.

    할인율/정가는 API 응답에 항상 있는 필드가 아니므로 있을 때만 채운다
    (없으면 가격 이력 기반 규칙(b)만으로 판정).
    """
    pid = raw.get("productId")
    name = raw.get("productName")
    price = _to_int(raw.get("productPrice"))
    url = raw.get("productUrl")
    if pid is None or not name or not price or not url:
        return None

    original = _to_int(raw.get("originalPrice") or raw.get("productOriginalPrice") or raw.get("basePrice"))
    discount = _to_float(raw.get("discountRate") or raw.get("productDiscountRate"))
    if original is not None and original <= price:
        original = None

    return Product(
        source=source,
        product_id=str(pid),
        name=str(name).strip(),
        price=price,
        url=f"https://www.coupang.com/vp/products/{pid}",
        image_url=raw.get("productImage") or None,
        original_price=original,
        discount_rate=discount,
        category=raw.get("categoryName") or None,
        is_rocket=raw.get("isRocket"),
        is_free_shipping=raw.get("isFreeShipping"),
        affiliate_url=str(url),
        extra={k: v for k, v in raw.items() if k in ("rank", "keyword")},
    )
