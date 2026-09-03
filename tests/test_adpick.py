from __future__ import annotations

from dealbot.collectors.adpick_hotdeal import DEFAULT_FIELD_MAP, extract_items, parse_item
from dealbot.shops import ShopRegistry


def test_extract_items_shapes() -> None:
    assert extract_items([{"a": 1}, "x"]) == [{"a": 1}]
    assert extract_items({"data": {"list": [{"a": 1}]}}) == [{"a": 1}]
    assert extract_items({"foo": "bar"}) == []


def test_parse_item_maps_fields_and_shop() -> None:
    reg = ShopRegistry()
    raw = {
        "id": "55",
        "title": "삼성 SSD 1TB",
        "price": "89,000",
        "org_price": "129,000",
        "link": "https://adpick.co.kr/shop/go?x=1",
        "landing_url": "https://www.11st.co.kr/products/12345",
        "image": "https://img/1.jpg",
        "mall_name": "11번가",
    }
    p = parse_item(raw, source="adpick", registry=reg, field_map=DEFAULT_FIELD_MAP)
    assert p is not None
    assert p.shop == "11st" and p.product_id == "11st:12345" and p.price == 89000 and p.original_price == 129000
    assert p.affiliate_url == "https://adpick.co.kr/shop/go?x=1" and p.url == "https://www.11st.co.kr/products/12345"
    assert p.effective_discount_rate() == 31.0
    assert parse_item({"title": "no url"}, source="a", registry=reg, field_map=DEFAULT_FIELD_MAP) is None
    p2 = parse_item({"name": "x", "url": "https://adpick.co.kr/go/2", "idx": 2}, source="a", registry=reg, field_map=DEFAULT_FIELD_MAP)
    assert p2 is not None and p2.shop == "adpick" and p2.product_id == "adpick:2"
