from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from dealbot.coupang.auth import build_authorization, signed_date
from dealbot.coupang.client import (
    PATH_DEEPLINK,
    PATH_GOLDBOX,
    CoupangApiError,
    CoupangClient,
    parse_api_product,
)


def test_signature_matches_reference_algorithm() -> None:
    ak, sk = "ACCESS", "SECRET"
    fixed = 1_700_000_000.0  # 2023-11-14T22:13:20Z
    header = build_authorization("GET", PATH_GOLDBOX, "limit=10", ak, sk, now=fixed)
    date = signed_date(fixed)
    assert date == "231114T221320Z"
    expected_sig = hmac.new(sk.encode(), (date + "GET" + PATH_GOLDBOX + "limit=10").encode(), hashlib.sha256).hexdigest()
    assert header == f"CEA algorithm=HmacSHA256, access-key={ak}, signed-date={date}, signature={expected_sig}"


def _client(handler) -> CoupangClient:  # type: ignore[no-untyped-def]
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CoupangClient("ak", "sk", http=http, sub_id="sub1", max_retries=3, retry_backoff=0.01)


async def test_goldbox_and_parse() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == PATH_GOLDBOX
        assert req.url.params["subId"] == "sub1"
        assert req.headers["Authorization"].startswith("CEA algorithm=HmacSHA256, access-key=ak")
        return httpx.Response(
            200,
            json={
                "rCode": "0",
                "rMessage": "",
                "data": [
                    {
                        "productId": 123,
                        "productName": " 테스트 상품 ",
                        "productPrice": "12900",
                        "productUrl": "https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey=123",
                        "productImage": "https://img/1.jpg",
                        "isRocket": True,
                        "isFreeShipping": False,
                        "categoryName": "식품",
                        "rank": 1,
                    },
                    {"productId": 1, "productName": "no price", "productUrl": "x"},
                ],
            },
        )

    c = _client(handler)
    raw = await c.goldbox(limit=10)
    assert len(raw) == 2
    parsed = [p for p in (parse_api_product(r, "goldbox") for r in raw) if p]
    assert len(parsed) == 1
    p = parsed[0]
    assert p.product_id == "123" and p.price == 12900 and p.name == "테스트 상품"
    assert p.url == "https://www.coupang.com/vp/products/123"
    assert p.affiliate_url and "lptag=AF1" in p.affiliate_url
    assert p.is_rocket is True and p.category == "식품"
    assert p.discount_rate is None and p.effective_discount_rate() is None


def test_parse_optional_discount_fields() -> None:
    p = parse_api_product(
        {"productId": 5, "productName": "n", "productPrice": 7000, "productUrl": "u", "originalPrice": 10000, "discountRate": "30"},
        "s",
    )
    assert p is not None and p.original_price == 10000 and p.discount_rate == 30.0
    p2 = parse_api_product({"productId": 5, "productName": "n", "productPrice": 7000, "productUrl": "u", "originalPrice": 5000}, "s")
    assert p2 is not None and p2.original_price is None


async def test_rcode_error_raises() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rCode": "400", "rMessage": "Invalid signature", "data": None})

    c = _client(handler)
    with pytest.raises(CoupangApiError, match="Invalid signature"):
        await c.goldbox()


async def test_retries_on_5xx_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"rCode": "0", "data": []})

    c = _client(handler)
    assert await c.goldbox() == []
    assert calls["n"] == 3


async def test_4xx_not_retried() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    c = _client(handler)
    with pytest.raises(CoupangApiError, match="401"):
        await c.goldbox()
    assert calls["n"] == 1


async def test_deeplink() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST" and req.url.path == PATH_DEEPLINK
        body = json.loads(req.content)
        assert body["coupangUrls"] == ["https://www.coupang.com/vp/products/1"]
        assert body["subId"] == "sub1"
        return httpx.Response(
            200,
            json={
                "rCode": "0",
                "data": [
                    {
                        "originalUrl": body["coupangUrls"][0],
                        "shortenUrl": "https://link.coupang.com/a/xyz",
                        "landingUrl": "https://link.coupang.com/re/AFFSDP?...",
                    }
                ],
            },
        )

    c = _client(handler)
    res = await c.deeplink(["https://www.coupang.com/vp/products/1"])
    assert res[0].shorten_url == "https://link.coupang.com/a/xyz"
    assert await c.deeplink([]) == []
