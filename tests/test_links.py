from __future__ import annotations

import httpx
import pytest

from dealbot.config import LinksConfig, PublishConfig
from dealbot.coupang.client import CoupangClient
from dealbot.links import (
    CoupangDeeplinkProvider,
    LinkConversionError,
    LinkPriceProvider,
    LinkRouter,
    ManualLinkRequired,
    ShopSkipped,
)
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry


def _product(shop: str, url: str, affiliate: str | None = None) -> Product:
    return Product(source="s", product_id=f"{shop}:1", shop=shop, name="n", price=1000, url=url, affiliate_url=affiliate)


def _handler(req: httpx.Request) -> httpx.Response:
    if req.url.host == "link.coupang.com" and req.url.path.startswith("/a/"):
        return httpx.Response(302, headers={"location": "https://www.coupang.com/vp/products/777?itemId=5&src=zzz"})
    if req.url.host == "www.coupang.com":
        return httpx.Response(200, text="ok")
    if req.url.path.endswith("/deeplink"):
        return httpx.Response(200, json={"rCode": "0", "data": [{"originalUrl": "x", "shortenUrl": "https://link.coupang.com/a/MINE"}]})
    if req.url.host == "api.linkprice.com":
        assert req.url.params["a_id"] == "A123" and req.url.params["url"].startswith("https://www.11st.co.kr")
        return httpx.Response(200, json={"result": "S", "url": "https://click.linkprice.com/click.php?m=11st&a=A123&l=9999&l_cd1=0&u_id=&l_cd2=0&tu=https%3A%2F%2Fwww.11st.co.kr"})
    return httpx.Response(404)


@pytest.fixture
def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(_handler))


def _router(http: httpx.AsyncClient, *, providers: dict | None = None, allow_raw: bool = True, always: bool = False) -> LinkRouter:  # type: ignore[type-arg]
    return LinkRouter(ShopRegistry(), LinksConfig(always_deeplink=always), PublishConfig(allow_raw_links=allow_raw), providers=providers or {})


async def test_existing_affiliate_url_used_without_provider(http: httpx.AsyncClient) -> None:
    r = _router(http)
    assert await r.to_affiliate(_product("coupang", "https://www.coupang.com/vp/products/1", "https://link.coupang.com/re/x")) == "https://link.coupang.com/re/x"


async def test_coupang_provider_converts_and_resolves_short_links(http: httpx.AsyncClient) -> None:
    api = CoupangClient("a", "s", http=http, retry_backoff=0.01)
    prov = CoupangDeeplinkProvider(api, http, LinksConfig())
    r = _router(http, providers={"coupang": prov})
    assert await r.to_affiliate(_product("coupang", "https://link.coupang.com/a/OTHER")) == "https://link.coupang.com/a/MINE"
    assert await prov.canonical_url("https://link.coupang.com/a/OTHER") == "https://www.coupang.com/vp/products/777?itemId=5"
    # always_deeplink → 이미 있는 제휴 링크도 재변환
    r2 = _router(http, providers={"coupang": prov}, always=True)
    assert await r2.to_affiliate(_product("coupang", "https://www.coupang.com/vp/products/1", "https://link.coupang.com/re/x")) == "https://link.coupang.com/a/MINE"


async def test_manual_raw_skip_modes(http: httpx.AsyncClient) -> None:
    r = _router(http)
    with pytest.raises(ManualLinkRequired) as ei:
        await r.to_affiliate(_product("toss", "https://toss.im/_m/abc"))
    assert ei.value.shop.key == "toss"
    # 관리자가 넣어준 링크가 있으면 manual 이어도 그대로
    assert await r.to_affiliate(_product("toss", "https://toss.im/_m/abc", "https://toss.im/_m/MINE")) == "https://toss.im/_m/MINE"
    # raw 모드: 테무는 기본 꺼짐이라 명시적으로 켠 레지스트리로 확인
    raw_reg = ShopRegistry([Shop(key="temu", name="테무", domains=["temu.com"], link_mode="raw")])
    assert await LinkRouter(raw_reg, LinksConfig(), PublishConfig()).to_affiliate(_product("temu", "https://temu.com/x")) == "https://temu.com/x"
    reg = ShopRegistry([Shop(key="dead", name="d", link_mode="skip")])
    r_skip = LinkRouter(reg, LinksConfig(), PublishConfig())
    with pytest.raises(ShopSkipped):
        await r_skip.to_affiliate(_product("dead", "https://d/x"))


async def test_api_mode_without_provider_falls_back_to_raw_or_errors(http: httpx.AsyncClient) -> None:
    assert await _router(http).to_affiliate(_product("11st", "https://www.11st.co.kr/products/1")) == "https://www.11st.co.kr/products/1"
    with pytest.raises(LinkConversionError):
        await _router(http, allow_raw=False).to_affiliate(_product("11st", "https://www.11st.co.kr/products/1"))
    # 미등록 쇼핑몰
    assert await _router(http).to_affiliate(_product("unknown", "https://x.y/z")) == "https://x.y/z"
    with pytest.raises(LinkConversionError):
        await _router(http, allow_raw=False).to_affiliate(_product("unknown", "https://x.y/z"))


async def test_linkprice_provider(http: httpx.AsyncClient) -> None:
    prov = LinkPriceProvider("A123", http, retries=1)
    r = _router(http, providers={"linkprice": prov})
    link = await r.to_affiliate(_product("11st", "https://www.11st.co.kr/products/1"))
    assert link.startswith("https://click.linkprice.com/click.php")
    assert r.describe(ShopRegistry().get("11st")) == "자동 (링크프라이스)"  # type: ignore[arg-type]
    assert r.describe(ShopRegistry().get("toss")) == "내 링크 요청 (앱에서 만들어 답장)"  # type: ignore[arg-type]


async def test_coupang_without_api_asks_manual(http: httpx.AsyncClient) -> None:
    r = _router(http)
    with pytest.raises(ManualLinkRequired) as ei:
        await r.to_affiliate(_product("coupang", "https://www.coupang.com/vp/products/1"))
    assert ei.value.shop.key == "coupang"
    assert r.describe(ShopRegistry().get("coupang")) == "내 링크 요청 (자동 변환기 없음)"  # type: ignore[arg-type]
