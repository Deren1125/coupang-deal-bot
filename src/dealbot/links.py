"""원본 URL → 제휴(수익) 링크 변환.

쇼핑몰별 link_mode:
  api    → provider(coupang / linkprice) 로 자동 변환
  manual → 자동 변환 불가. 관리자가 앱/사이트에서 만든 링크를 /link 로 넘겨줘야 함 (ManualLinkRequired)
  raw    → 원본 링크 그대로 발행
  skip   → 발행하지 않음
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from dealbot.config import LinksConfig, PublishConfig
from dealbot.coupang.client import CoupangClient
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry
from dealbot.utils.retry import retry_async
from dealbot.utils.urls import canonical_product_url, is_short_affiliate_link

log = logging.getLogger(__name__)


class LinkConversionError(Exception):
    pass


class ManualLinkRequired(Exception):
    """이 쇼핑몰은 사람이 앱에서 링크를 만들어야 한다."""

    def __init__(self, shop: Shop):
        super().__init__(f"manual link required for {shop.name}")
        self.shop = shop


class ShopSkipped(Exception):
    pass


# ----------------------------------------------------------------- providers
class CoupangDeeplinkProvider:
    key = "coupang"

    def __init__(self, client: CoupangClient, http: httpx.AsyncClient | None, cfg: LinksConfig) -> None:
        self._client = client
        self._http = http
        self._cfg = cfg

    async def _resolve_short(self, url: str) -> str | None:
        if self._http is None:
            return None
        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
            final = str(resp.url)
            return final if "coupang.com" in final else None
        except httpx.HTTPError as e:
            log.warning("short link resolve failed for %s: %s", url, e)
            return None

    async def canonical_url(self, url: str) -> str | None:
        canon = canonical_product_url(url)
        if canon:
            return canon
        if self._cfg.resolve_short_links and is_short_affiliate_link(url):
            resolved = await self._resolve_short(url)
            if resolved:
                return canonical_product_url(resolved)
        return None

    async def convert(self, url: str) -> str:
        canon = await self.canonical_url(url)
        if not canon:
            raise LinkConversionError(f"cannot canonicalize coupang url: {url}")
        results = await self._client.deeplink([canon])
        if not results or not results[0].shorten_url:
            raise LinkConversionError(f"deeplink api returned no link for {canon}")
        return results[0].shorten_url


class LinkPriceProvider:
    """링크프라이스 딥링크 API.
    GET https://api.linkprice.com/ci/service/custom_link_xml?a_id=..&url=..&mode=json
    응답 형식은 계정 승인 후 실제 응답으로 검증 필요 (url 필드 또는 click.linkprice.com 링크를 찾는다).
    """

    key = "linkprice"
    ENDPOINT = "https://api.linkprice.com/ci/service/custom_link_xml"
    _CLICK_RE = re.compile(r"https?://click\.linkprice\.com/[^\s\"'<>]+")

    def __init__(self, affiliate_id: str, http: httpx.AsyncClient, *, retries: int = 3, backoff: float = 2.0) -> None:
        self._aid = affiliate_id
        self._http = http
        self._retries = retries
        self._backoff = backoff

    async def convert(self, url: str) -> str:
        async def _do() -> str:
            resp = await self._http.get(self.ENDPOINT, params={"a_id": self._aid, "url": url, "mode": "json"}, timeout=20)
            resp.raise_for_status()
            text = resp.text
            try:
                data: Any = resp.json()
            except ValueError:
                data = None
            if isinstance(data, dict):
                for k in ("url", "link", "deeplink", "result_url"):
                    v = data.get(k)
                    if isinstance(v, str) and v.startswith("http"):
                        return v
                if str(data.get("result", "")).upper() not in ("", "S", "SUCCESS", "0"):
                    raise LinkConversionError(f"linkprice api error: {data}")
            m = self._CLICK_RE.search(text)
            if m:
                return m.group(0)
            raise LinkConversionError(f"linkprice: unexpected response: {text[:200]}")

        return await retry_async(_do, attempts=self._retries, backoff=self._backoff, label="linkprice deeplink")


# ------------------------------------------------------------------- router
PROVIDER_LABELS = {"coupang": "쿠팡 API", "linkprice": "링크프라이스", "naver_connect": "네이버 브라우저"}


class LinkRouter:
    def __init__(
        self,
        registry: ShopRegistry,
        links_cfg: LinksConfig,
        publish_cfg: PublishConfig,
        *,
        providers: dict[str, Any] | None = None,
    ) -> None:
        self.registry = registry
        self._links = links_cfg
        self._publish = publish_cfg
        self.providers: dict[str, Any] = providers or {}

    def provider_for(self, shop: Shop) -> Any | None:
        return self.providers.get(shop.provider or "") if shop.provider else None

    def describe(self, shop: Shop) -> str:
        """상태 표시용: 이 쇼핑몰의 링크가 어떻게 처리되는지."""
        if not shop.enabled:
            return f"꺼짐 ({shop.disabled_reason})" if shop.disabled_reason else "꺼짐"
        if shop.link_mode == "api":
            if self.provider_for(shop):
                label = PROVIDER_LABELS.get(shop.provider or "", shop.provider)
                return f"자동 ({label})" + (", 안 되면 내 링크 요청" if shop.manual_fallback else "")
            if shop.manual_fallback:
                return "내 링크 요청 (자동 변환기 없음)"
            return "원본 링크 그대로 (제휴 키 없음)" if self._publish.allow_raw_links else "올리지 않음 (제휴 키 없음)"
        if shop.link_mode == "manual":
            return "내 링크 요청 (앱에서 만들어 답장)"
        if shop.link_mode == "raw":
            return "원본 링크 그대로"
        return "올리지 않음"

    async def to_affiliate(self, product: Product) -> str:
        shop = self.registry.get(product.shop)
        if shop is None:
            if self._publish.allow_raw_links:
                return product.url
            raise LinkConversionError(f"unknown shop '{product.shop}' and raw links disabled")
        if not shop.enabled or shop.link_mode == "skip":
            raise ShopSkipped(f"shop '{shop.key}' disabled")

        if product.affiliate_url and not (self._links.always_deeplink and shop.link_mode == "api" and self.provider_for(shop)):
            return product.affiliate_url

        if shop.link_mode == "manual":
            raise ManualLinkRequired(shop)
        if shop.link_mode == "raw":
            return product.url

        provider = self.provider_for(shop)
        if provider is None:
            if product.affiliate_url:
                return product.affiliate_url
            if shop.manual_fallback:
                raise ManualLinkRequired(shop)
            if self._publish.allow_raw_links:
                log.info("no provider for %s — posting raw link", shop.key)
                return product.url
            raise LinkConversionError(f"no link provider configured for shop '{shop.key}' ({shop.provider})")
        try:
            return await provider.convert(product.url)
        except Exception as e:  # noqa: BLE001
            if shop.manual_fallback:
                log.warning("auto link (%s) failed for %s: %s — falling back to manual", shop.provider, shop.key, e)
                raise ManualLinkRequired(shop) from e
            if isinstance(e, LinkConversionError):
                raise
            raise LinkConversionError(f"{shop.provider}: {type(e).__name__}: {e}") from e
