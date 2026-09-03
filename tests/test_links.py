from __future__ import annotations

import httpx
import pytest

from dealbot.config import LinksConfig
from dealbot.coupang.client import CoupangClient
from dealbot.links import LinkConversionError, LinkConverter
from dealbot.models import Product


def _product(url: str, affiliate: str | None = None) -> Product:
    return Product(source="s", product_id="1", name="n", price=1000, url=url, affiliate_url=affiliate)


async def test_uses_existing_affiliate_url_without_api() -> None:
    conv = LinkConverter(LinksConfig(), coupang=None)
    assert await conv.to_affiliate(_product("https://www.coupang.com/vp/products/1", "https://link.coupang.com/re/x")) == "https://link.coupang.com/re/x"


async def test_raises_without_api_for_raw_url() -> None:
    conv = LinkConverter(LinksConfig(), coupang=None)
    with pytest.raises(LinkConversionError):
        await conv.to_affiliate(_product("https://www.coupang.com/vp/products/1"))


async def test_deeplink_conversion_and_short_link_resolution() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "link.coupang.com" and req.url.path.startswith("/a/"):
            return httpx.Response(302, headers={"location": "https://www.coupang.com/vp/products/777?itemId=5&src=zzz"})
        if req.url.host == "www.coupang.com":
            return httpx.Response(200, text="ok")
        if req.url.path.endswith("/deeplink"):
            return httpx.Response(200, json={"rCode": "0", "data": [{"originalUrl": "x", "shortenUrl": "https://link.coupang.com/a/MINE"}]})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = CoupangClient("a", "s", http=http, retry_backoff=0.01)
    conv = LinkConverter(LinksConfig(), coupang=api, http=http)

    # 타인의 단축 링크 → 원본으로 풀어서 → 내 딥링크
    link = await conv.to_affiliate(_product("https://link.coupang.com/a/OTHER"))
    assert link == "https://link.coupang.com/a/MINE"
    assert await conv.canonical_url(_product("https://link.coupang.com/a/OTHER")) == "https://www.coupang.com/vp/products/777?itemId=5"

    # always_deeplink 이면 API 링크도 재변환
    conv2 = LinkConverter(LinksConfig(always_deeplink=True), coupang=api, http=http)
    assert await conv2.to_affiliate(_product("https://www.coupang.com/vp/products/1", "https://link.coupang.com/re/x")) == "https://link.coupang.com/a/MINE"
