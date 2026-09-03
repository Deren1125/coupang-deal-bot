from __future__ import annotations

from pathlib import Path

from dealbot.config import ShopConfig, load_settings
from dealbot.shops import DEFAULT_SHOPS, ShopRegistry, find_urls


def test_alias_and_url_matching() -> None:
    reg = ShopRegistry()
    assert reg.by_alias("[쿠팡]").key == "coupang"  # type: ignore[union-attr]
    assert reg.by_alias("쿠팡(로켓)").key == "coupang"  # type: ignore[union-attr]
    assert reg.by_alias("토스쇼핑").key == "toss"  # type: ignore[union-attr]
    assert reg.by_alias("네이버플러스스토어").key == "naver"  # type: ignore[union-attr]
    assert reg.by_alias("G마켓").key == "gmarket"  # type: ignore[union-attr]
    assert reg.by_alias("알리").key == "aliexpress"  # type: ignore[union-attr]
    assert reg.by_alias("듣보잡몰") is None
    assert reg.by_url("https://www.coupang.com/vp/products/1").key == "coupang"  # type: ignore[union-attr]
    assert reg.by_url("https://toss.im/_m/P4Qr1ope").key == "toss"  # type: ignore[union-attr]
    assert reg.by_url("https://smartstore.naver.com/store/products/123").key == "naver"  # type: ignore[union-attr]
    assert reg.by_url("https://example.com/x") is None


def test_product_keys() -> None:
    pk = ShopRegistry.product_key
    assert pk("coupang", "https://www.coupang.com/vp/products/7381234?itemId=1") == "coupang:7381234"
    assert pk("toss", "https://toss.im/_m/P4Qr1ope") == "toss:P4Qr1ope"
    assert pk("naver", "https://smartstore.naver.com/abc/products/123456?x=1") == "naver:123456"
    assert pk("11st", "https://www.11st.co.kr/products/8899") == "11st:8899"
    assert pk("gmarket", "https://item.gmarket.co.kr/Item?goodscode=123") == "gmarket:123"
    assert pk("aliexpress", "https://ko.aliexpress.com/item/1005001.html") == "aliexpress:1005001"
    k = pk("toss", "https://toss.im/shopping/whatever")
    assert k.startswith("toss:url:") and k == pk("toss", "https://toss.im/shopping/whatever#frag")


def test_find_urls() -> None:
    assert find_urls("링크 https://toss.im/_m/abc (본문) 그리고 http://a.b/c.") == ["https://toss.im/_m/abc", "http://a.b/c"]


def test_config_override(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "shops:\n"
        "  - {key: naver, link_mode: raw}\n"
        "  - {key: temu, enabled: false}\n"
        "  - {key: mymall, name: 마이몰, aliases: [마이몰], domains: [mymall.kr], link_mode: raw}\n",
        encoding="utf-8",
    )
    s = load_settings(cfg, load_env=False)
    reg = s.shop_registry()
    assert reg.get("naver").link_mode == "raw"  # type: ignore[union-attr]
    assert reg.get("naver").disclosure  # 기본값 유지  # type: ignore[union-attr]
    assert reg.get("temu").enabled is False  # type: ignore[union-attr]
    assert reg.by_alias("마이몰").key == "mymall"  # type: ignore[union-attr]
    assert len(reg.all()) == len(DEFAULT_SHOPS) + 1
    assert ShopConfig(key="x", link_mode="raw").link_mode == "raw"
