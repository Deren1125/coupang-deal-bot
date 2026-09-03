"""상품 URL → 내 파트너스 트래킹 링크 변환."""

from __future__ import annotations

import logging

import httpx

from dealbot.config import LinksConfig
from dealbot.coupang.client import CoupangClient
from dealbot.models import Product
from dealbot.utils.urls import canonical_product_url, is_short_affiliate_link

log = logging.getLogger(__name__)


class LinkConversionError(Exception):
    pass


class LinkConverter:
    def __init__(
        self,
        cfg: LinksConfig,
        *,
        coupang: CoupangClient | None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._coupang = coupang
        self._http = http

    @property
    def available(self) -> bool:
        return self._coupang is not None

    async def resolve_short_link(self, url: str) -> str | None:
        """link.coupang.com/a/xxx → 리다이렉트를 따라가 실제 상품 URL 획득."""
        if self._http is None:
            return None
        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
            final = str(resp.url)
            return final if "coupang.com" in final else None
        except httpx.HTTPError as e:
            log.warning("short link resolve failed for %s: %s", url, e)
            return None

    async def canonical_url(self, product: Product) -> str | None:
        url = product.url
        canon = canonical_product_url(url)
        if canon:
            return canon
        if self._cfg.resolve_short_links and is_short_affiliate_link(url):
            resolved = await self.resolve_short_link(url)
            if resolved:
                return canonical_product_url(resolved)
        return None

    async def to_affiliate(self, product: Product) -> str:
        """발행에 사용할 링크를 돌려준다. 실패 시 LinkConversionError."""
        if product.affiliate_url and not self._cfg.always_deeplink:
            return product.affiliate_url

        if self._coupang is None:
            if product.affiliate_url:
                return product.affiliate_url
            raise LinkConversionError("coupang api credentials not configured; cannot create deeplink")

        canon = await self.canonical_url(product)
        if not canon:
            raise LinkConversionError(f"cannot canonicalize product url: {product.url}")

        results = await self._coupang.deeplink([canon])
        if not results or not results[0].shorten_url:
            raise LinkConversionError(f"deeplink api returned no link for {canon}")
        return results[0].shorten_url
