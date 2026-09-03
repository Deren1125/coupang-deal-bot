"""쿠팡파트너스 Open API 클라이언트 (골드박스 / 카테고리 베스트 / 검색 / 딥링크).

- 엔드포인트 경로는 파트너스 문서와 다를 수 있으니 4xx 가 나면 PATH_* 를 대조하세요.
- 파트너스 API 는 호출 횟수 제한이 빡빡합니다(검색 API 기준 시간당 10회로 알려짐). ApiBudget 으로
  시간당 총 호출 수를 관리하고, 발행에 꼭 필요한 딥링크 호출 몫(reserve)을 남겨 둡니다.
"""

from __future__ import annotations

import logging
import time
from collections import deque
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


class CoupangRateLimited(CoupangApiError):
    """시간당 호출 예산 소진 (로컬 예산). 재시도하지 않고 다음 주기로 넘긴다."""


@dataclass(slots=True)
class DeeplinkResult:
    original_url: str
    shorten_url: str
    landing_url: str | None = None


class ApiBudget:
    """슬라이딩 1시간 창의 호출 예산. reserve 에 적힌 종류(예: deeplink)는 예약 몫까지 쓸 수 있다."""

    def __init__(self, max_per_hour: int = 10, reserve: dict[str, int] | None = None) -> None:
        self.max_per_hour = max(0, max_per_hour)
        self.reserve = dict(reserve or {})
        self._calls: deque[tuple[float, str]] = deque()

    def _prune(self, now: float) -> None:
        while self._calls and now - self._calls[0][0] > 3600:
            self._calls.popleft()

    def used(self, now: float | None = None) -> int:
        self._prune(now if now is not None else time.monotonic())
        return len(self._calls)

    def available(self, kind: str = "general", now: float | None = None) -> bool:
        """일반 호출은 (최대 - 예약 합계)까지, 예약된 종류는 (최대 - 다른 종류의 예약 합계)까지."""
        if self.max_per_hour <= 0:
            return True
        used = self.used(now)
        reserved_total = sum(self.reserve.values())
        if kind in self.reserve:
            return used < self.max_per_hour - (reserved_total - self.reserve[kind])
        return used < self.max_per_hour - reserved_total

    def record(self, kind: str = "general", now: float | None = None) -> None:
        self._calls.append((now if now is not None else time.monotonic(), kind))

    def usage(self) -> dict[str, int]:
        self._prune(time.monotonic())
        out: dict[str, int] = {}
        for _, k in self._calls:
            out[k] = out.get(k, 0) + 1
        return out


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
        budget: ApiBudget | None = None,
    ) -> None:
        self._ak = access_key
        self._sk = secret_key
        self._http = http
        self.sub_id = sub_id
        self._retries = max_retries
        self._backoff = retry_backoff
        self._base = base_url.rstrip("/")
        self.budget = budget

    # ------------------------------------------------------------------ core
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        kind: str = "general",
    ) -> Any:
        if self.budget is not None and not self.budget.available(kind):
            raise CoupangRateLimited(f"coupang api hourly budget exhausted (kind={kind}, used={self.budget.usage()})")
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        query = urlencode(clean, doseq=True)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        if self.budget is not None:
            self.budget.record(kind)

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
            "GET", PATH_GOLDBOX, params={"subId": self.sub_id, "imageSize": image_size, "limit": limit}, kind="goldbox"
        )
        return list(data or [])

    async def best_category(
        self, category_id: int | str, *, limit: int = 50, image_size: str | None = None
    ) -> list[dict[str, Any]]:
        path = PATH_BEST_CATEGORY.format(category_id=category_id)
        data = await self._request(
            "GET", path, params={"limit": limit, "subId": self.sub_id, "imageSize": image_size}, kind="bestcategory"
        )
        return list(data or [])

    async def search(self, keyword: str, *, limit: int = 10, image_size: str | None = None) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            PATH_SEARCH,
            params={"keyword": keyword, "limit": limit, "subId": self.sub_id, "imageSize": image_size},
            kind="search",
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
        data = await self._request("POST", PATH_DEEPLINK, json=body, kind="deeplink")
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

    할인율/정가는 API 응답에 항상 있는 필드가 아니므로 있을 때만 채운다.
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
        product_id=f"coupang:{pid}",
        shop="coupang",
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
