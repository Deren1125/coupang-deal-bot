from __future__ import annotations

from pathlib import Path

import httpx

from dealbot.collectors import CollectorContext, build_collector
from dealbot.collectors.ppomppu import decode_html, find_coupang_urls, parse_list_page, parse_title
from dealbot.config import CollectorConfig, Settings
from dealbot.storage.db import Database


def test_parse_title() -> None:
    t = parse_title("[쿠팡] 스탠리 텀블러 1.18L (29,900원/무료)")
    assert t == {"shop": "쿠팡", "name": "스탠리 텀블러 1.18L", "price": 29900, "shipping": "무료"}
    t2 = parse_title("[쿠팡] 가격없는 쿠팡딜 (무료)")
    assert t2["price"] is None and t2["shipping"] is None
    t3 = parse_title("제목만 있음")
    assert t3["shop"] is None and t3["name"] == "제목만 있음"
    t4 = parse_title("[쿠팡(로켓)] 상품 (1.5만원/3,000원)")
    assert t4["shop"] == "쿠팡(로켓)" and t4["price"] == 15000 and t4["shipping"] == "3,000원"


def test_parse_list_page(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "ppomppu_list.html").read_text(encoding="utf-8")
    items = parse_list_page(html, row_selector="tr.baseList", title_selector="a.baseList-title", thumb_selector="img")
    assert [i["external_id"] for i in items] == ["600001", "600002", "600003", "600004"]
    first = items[0]
    assert first["shop"] == "쿠팡" and first["price"] == 29900
    assert first["thumb"] == "https://cdn2.ppomppu.co.kr/zboard/data3/2026/0903/m_thumb_600001.jpg"
    assert first["post_url"].startswith("https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu")


def test_find_coupang_urls_prefers_product_pages(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "ppomppu_view_600001.html").read_text(encoding="utf-8")
    urls = find_coupang_urls(html)
    assert urls[0].startswith("https://www.coupang.com/vp/products/7381234")
    html2 = (fixtures_dir / "ppomppu_view_600004.html").read_text(encoding="utf-8")
    assert find_coupang_urls(html2) == ["https://link.coupang.com/a/OTHERS"]


def test_decode_html_euc_kr() -> None:
    body = "안녕".encode("euc-kr")
    resp = httpx.Response(200, content=body, headers={"content-type": "text/html"})
    assert decode_html(resp) == "안녕"
    resp2 = httpx.Response(200, content="안녕".encode(), headers={"content-type": "text/html; charset=utf-8"})
    assert decode_html(resp2) == "안녕"


async def test_collect_end_to_end(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/zboard/zboard.php":
            return httpx.Response(200, content=(fixtures_dir / "ppomppu_list.html").read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        if req.url.path == "/zboard/view.php":
            no = req.url.params["no"]
            f = fixtures_dir / f"ppomppu_view_{no}.html"
            if f.exists():
                return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
            return httpx.Response(404)
        if req.url.host == "link.coupang.com":
            return httpx.Response(302, headers={"location": "https://www.coupang.com/vp/products/999?itemId=1"})
        if req.url.host == "www.coupang.com":
            return httpx.Response(200, text="product page")
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None)
    cfg = CollectorConfig(name="ppomppu", type="ppomppu", options={"request_delay_seconds": 0, "shop_keywords": ["쿠팡"]})
    collector = build_collector(cfg, ctx)

    products = await collector.collect()
    assert [p.product_id for p in products] == ["7381234", "999"]
    p = products[0]
    assert p.name == "스탠리 텀블러 1.18L" and p.price == 29900 and p.is_free_shipping is True
    assert p.url == "https://www.coupang.com/vp/products/7381234?itemId=11&vendorItemId=22"
    assert p.affiliate_url is None and p.external_id == "600001"
    assert products[1].url == "https://www.coupang.com/vp/products/999?itemId=1"

    # G마켓 글은 상세 요청 안 함, 가격 없는 글은 seen 처리만
    assert not any("no=600002" in c for c in calls)
    assert db.is_seen("ppomppu", "600003")

    # 두 번째 실행: 이미 본 글이므로 상세 요청 없음
    calls.clear()
    assert await collector.collect() == []
    assert all("view.php" not in c for c in calls)


def test_registry_plugin_path() -> None:
    from dealbot.collectors import available_types, resolve_type
    from dealbot.collectors.ppomppu import PpomppuCollector

    assert {"coupang_goldbox", "coupang_category_best", "ppomppu"} <= set(available_types())
    assert resolve_type("dealbot.collectors.ppomppu:PpomppuCollector") is PpomppuCollector
