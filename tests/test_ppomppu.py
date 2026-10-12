from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dealbot.collectors import CollectorContext, build_collector
from dealbot.collectors.ppomppu import (
    decode_html,
    find_shop_urls,
    guess_kind,
    parse_list_page,
    parse_title,
)
from dealbot.config import CollectorConfig, Settings
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database


def test_parse_title_date_range_is_not_a_price() -> None:
    t = parse_title("[토스] 9,900원 샴푸 + 선크림 900원, 같이 담으면 무료배송 (~9/13)")
    assert t["price"] == 9900 and t["shipping"] is None and t["name"].endswith("(~9/13)")  # 예전엔 9원/배송 '13' 으로 읽었다
    assert parse_title("[네이버] 5,000원 할인 쿠폰 (9/12~9/14)")["price"] is None  # 할인 금액은 가격이 아니다
    assert parse_title("[쿠팡] 세제 3개 (3/무료)")["price"] is None  # 개수처럼 가격일 리 없는 수


def test_parse_title() -> None:
    t = parse_title("[쿠팡] 스탠리 텀블러 1.18L (29,900원/무료)")
    assert t == {"shop_tag": "쿠팡", "name": "스탠리 텀블러 1.18L", "price": 29900, "shipping": "무료"}
    t2 = parse_title("[쿠팡] 로켓와우 10% 쿠폰 (무료)")
    assert t2["price"] is None and t2["shipping"] is None
    t3 = parse_title("제목만 있음")
    assert t3["shop_tag"] is None and t3["name"] == "제목만 있음"
    t4 = parse_title("[쿠팡(로켓)] 상품 (1.5만원/3,000원)")
    assert t4["shop_tag"] == "쿠팡(로켓)" and t4["price"] == 15000 and t4["shipping"] == "3,000원"
    assert guess_kind("로켓와우 10% 쿠폰", None) == "coupon"
    assert guess_kind("무료체험 이벤트", None) == "event"
    assert guess_kind("상품", 1000) == "hotdeal"


def test_parse_title_price_skips_quantities_and_benefit_amounts() -> None:
    """괄호 속 맨 앞 숫자가 아니라 판매가: 개수·용량·옛 가격(화살표 앞)·할인 금액은 가격이 아니다."""
    assert parse_title("[쿠팡] 상품 (29,900원→19,900원/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (정가 29,900원 -> 19,900원 / 무료)")["price"] == 19900
    assert parse_title("[쿠팡] 앤커 보조배터리 (10,000mAh 19,900원/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 비비고 왕교자 (2개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 비비고 왕교자 (1+1 12,900원/무료)")["price"] == 12900
    assert parse_title("[네이버] 하림 닭가슴살 (2팩 25,800원/무료)")["price"] == 25800
    assert parse_title("[쿠팡] 코카콜라 제로 (500ml 24개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 크리넥스 물티슈 (100매 9,900원/무료)")["price"] == 9900
    assert parse_title("[쿠팡] 삼다수 (2Lx24개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (1만원 할인 후 19,900원/무료)")["price"] == 19900
    t = parse_title("[쿠팡] 로켓프레시 첫구매 쿠폰 (10,000원 할인)")
    assert t["price"] is None and guess_kind(t["name"], t["price"]) == "coupon"  # 쿠폰 금액을 상품 가격으로 올리지 않음
    assert parse_title("[쿠팡] 로켓프레시 (5,000원 쿠폰)")["price"] is None
    assert parse_title("[쿠팡] 상품 (5만원 이상 구매시 1만원 할인)")["price"] is None
    # 지금껏 읽던 모양은 그대로
    assert parse_title("[쿠팡] 상품 (12900/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (12900x2/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (1만2000원/무료)")["price"] == 12000
    assert parse_title("[쿠팡] 상품 (3만5천원/무료)")["price"] == 35000
    assert parse_title("[쿠팡] 상품 (19,900원 쿠폰가/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (19,900원 쿠폰적용/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (현대카드 7,700원/무료)")["price"] == 7700
    assert parse_title("[쿠팡] 상품 (12900 + 배송비 3,000원)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (19,900원 + 3,000원 적립/무료)")["price"] == 19900


def test_parse_title_nested_shipping_and_trailing_note() -> None:
    """끝 괄호 안 괄호("3,000원(3만↑무료)")와 괄호 뒤 꼬리말("와우", "카드할인")이 이름·가격·배송을 망치지 않는다."""
    t = parse_title("[네이버] 동원 리챔 오리지널 200g 10개 (25,900원/3,000원(3만↑무료))")
    assert t == {"shop_tag": "네이버", "name": "동원 리챔 오리지널 200g 10개", "price": 25900, "shipping": "3,000원(3만↑무료)"}
    t = parse_title("[G마켓] 오뚜기 진라면 매운맛 120g 40개 (19,800원/무료(스마일클럽))")
    assert t["name"] == "오뚜기 진라면 매운맛 120g 40개" and t["shipping"] == "무료(스마일클럽)"
    t = parse_title("[쿠팡] 삼다수 2L 24병 (15,840원/무료) 와우")
    assert t == {"shop_tag": "쿠팡", "name": "삼다수 2L 24병", "price": 15840, "shipping": "무료"}
    t = parse_title("[11번가] 갤럭시 버즈3 프로 (189,000원/무료) 카드할인")
    assert t["name"] == "갤럭시 버즈3 프로" and t["price"] == 189000 and guess_kind(t["name"], t["price"]) == "hotdeal"
    assert parse_title("[네이버] 굽네 닭가슴살 30팩 (29,900원/무료) 적립포함")["price"] == 29900
    assert parse_title("[쿠팡] 상품 (19,900원(쿠폰적용)/무료)")["price"] == 19900
    # 가격 괄호가 아니면 예전처럼 통째로 이름
    assert parse_title("[쿠팡] 에어팟 프로 2 (USB-C) 화이트")["name"] == "에어팟 프로 2 (USB-C) 화이트"
    assert parse_title("[쿠팡] 상품 (옵션) 블랙 (12,900원/무료)")["name"] == "상품 (옵션) 블랙"
    t = parse_title("[토스] 9,900원 샴푸 (~9/13) 선착순")
    assert t["name"] == "9,900원 샴푸 (~9/13) 선착순" and t["price"] == 9900 and t["shipping"] is None


def test_parse_list_page(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "ppomppu_list.html").read_text(encoding="utf-8")
    items = parse_list_page(html, row_selector="tr.baseList", title_selector="a.baseList-title", thumb_selector="img")
    assert [i["external_id"] for i in items] == [str(n) for n in range(600001, 600008)]
    first = items[0]
    assert first["shop_tag"] == "쿠팡" and first["price"] == 29900
    assert first["recommend"] == 10 and first["views"] == 5000 and first["comments"] == 12
    assert items[1]["comments"] is None
    assert first["thumb"] == "https://cdn2.ppomppu.co.kr/zboard/data3/2026/0903/m_thumb_600001.jpg"
    assert first["post_url"].startswith("https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu")


def test_find_shop_urls(fixtures_dir: Path) -> None:
    reg = ShopRegistry()
    html = (fixtures_dir / "ppomppu_view_600001.html").read_text(encoding="utf-8")
    urls = find_shop_urls(html, reg.get("coupang"))
    assert urls[0].startswith("https://www.coupang.com/vp/products/7381234")
    assert all("ppomppu.co.kr" not in u for u in urls)
    html4 = (fixtures_dir / "ppomppu_view_600004.html").read_text(encoding="utf-8")
    assert find_shop_urls(html4, reg.get("coupang")) == ["https://link.coupang.com/a/OTHERS"]
    html5 = (fixtures_dir / "ppomppu_view_600005.html").read_text(encoding="utf-8")
    assert find_shop_urls(html5, reg.get("toss")) == ["https://toss.im/_m/ABC123"]
    assert find_shop_urls(html5, reg.get("coupang")) == []
    assert find_shop_urls(html5, None) == ["https://toss.im/_m/ABC123"]


def test_decode_html_euc_kr() -> None:
    body = "안녕".encode("euc-kr")
    resp = httpx.Response(200, content=body, headers={"content-type": "text/html"})
    assert decode_html(resp) == "안녕"
    resp2 = httpx.Response(200, content="안녕".encode(), headers={"content-type": "text/html; charset=utf-8"})
    assert decode_html(resp2) == "안녕"


def _transport(fixtures_dir: Path, calls: list[str]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/zboard/zboard.php":
            return httpx.Response(200, content=(fixtures_dir / "ppomppu_list.html").read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        if req.url.path == "/zboard/view.php":
            f = fixtures_dir / f"ppomppu_view_{req.url.params['no']}.html"
            if f.exists():
                return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
            return httpx.Response(404)
        if req.url.host == "link.coupang.com":
            return httpx.Response(302, headers={"location": "https://www.coupang.com/vp/products/999?itemId=1"})
        if req.url.host == "www.coupang.com":
            return httpx.Response(200, text="product page")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_collect_multi_shop(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []
    http = httpx.AsyncClient(transport=_transport(fixtures_dir, calls))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="ppomppu", type="ppomppu", options={"request_delay_seconds": 0})
    collector = build_collector(cfg, ctx)

    products = await collector.collect()
    by_ext = {p.external_id: p for p in products}
    assert set(by_ext) == {"600001", "600002", "600003", "600004", "600005", "600006"}  # 듣보잡몰은 skip

    c = by_ext["600001"]
    assert c.shop == "coupang" and c.product_id == "coupang:7381234:22" and c.price == 29900 and c.recommend_count == 10 and c.comment_count == 12
    assert c.url == "https://www.coupang.com/vp/products/7381234?itemId=11&vendorItemId=22" and c.affiliate_url is None
    assert by_ext["600004"].product_id == "coupang:999"  # 타인 단축링크 → 원본 상품으로
    g = by_ext["600002"]
    assert g.shop == "gmarket" and g.product_id == "gmarket:3344556"
    coupon = by_ext["600003"]
    assert coupon.deal_kind == "coupon" and coupon.price == 0 and coupon.recommend_count == 7
    t = by_ext["600005"]
    assert t.shop == "toss" and t.product_id == "toss:ABC123" and t.price == 14890 and t.url == "https://toss.im/_m/ABC123"
    n = by_ext["600006"]
    assert n.shop == "naver" and n.product_id == "naver:123456"
    assert not any("no=600007" in u for u in calls)

    # 두 번째 실행: 상세 요청 없이, 관심도 게이트(추천≥1/댓글≥3/조회≥500)를 넘는 글만 저장된 링크로 다시 올라옴
    calls.clear()
    again = await collector.collect()
    assert all("view.php" not in u for u in calls)
    assert {p.external_id for p in again} == {"600001", "600002", "600003", "600005", "600006"}  # 600004 는 추천0·조회100
    assert {"coupang:7381234:22", by_ext["600003"].product_id, "toss:ABC123"} <= {p.product_id for p in again}
    assert all(p.url for p in again)


async def test_collect_shop_filter_and_unknown_raw(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []
    http = httpx.AsyncClient(transport=_transport(fixtures_dir, calls))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp", type="ppomppu", options={"request_delay_seconds": 0, "shops": ["toss"], "unknown_shop": "raw"})
    products = await build_collector(cfg, ctx).collect()
    assert [p.shop for p in products] == ["toss"]
    cfg2 = CollectorConfig(name="pp2", type="ppomppu", options={"request_delay_seconds": 0, "unknown_shop": "raw"})
    products2 = await build_collector(cfg2, ctx).collect()
    unknown = [p for p in products2 if p.external_id == "600007"]
    assert unknown and unknown[0].shop == "unknown" and unknown[0].url == "https://unknown-mall.example/item/1"


def test_registry_plugin_path() -> None:
    from dealbot.collectors import available_types, resolve_type
    from dealbot.collectors.ppomppu import PpomppuCollector

    assert {"coupang_goldbox", "coupang_category_best", "ppomppu", "adpick_hotdeal"} <= set(available_types())
    assert resolve_type("dealbot.collectors.ppomppu:PpomppuCollector") is PpomppuCollector


async def test_403_falls_back_to_mobile(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    """데스크톱이 403 이면 모바일 주소로 재시도하고, 브라우저 헤더와 워밍업 요청이 들어간다."""
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.url.path))
        assert req.headers.get("upgrade-insecure-requests") == "1"
        assert "ko-KR" in req.headers.get("accept-language", "")
        if req.url.host == "www.ppomppu.co.kr" and req.url.path == "/zboard/zboard.php":
            return httpx.Response(403, text="blocked")
        if req.url.path in ("/", "/new/bbs_list.php", "/zboard/zboard.php"):
            return httpx.Response(200, content=(fixtures_dir / "ppomppu_list.html").read_bytes(),
                                  headers={"content-type": "text/html; charset=utf-8"})
        f = fixtures_dir / f"ppomppu_view_{req.url.params.get('no', '')}.html"
        if f.exists():
            return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp403", type="ppomppu", options={"request_delay_seconds": 0, "max_detail_fetch_per_run": 1})
    products = await build_collector(cfg, ctx).collect()

    hosts_paths = [f"{h}{p}" for h, p in seen]
    assert "www.ppomppu.co.kr/" in hosts_paths          # 워밍업
    assert "m.ppomppu.co.kr/new/bbs_list.php" in hosts_paths  # 모바일 폴백
    assert products  # 모바일에서 목록을 읽어 상품이 나온다


async def test_403_without_fallback_raises(settings: Settings, db: Database) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>") if req.url.path == "/" else httpx.Response(403, text="no")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp", type="ppomppu", options={"request_delay_seconds": 0, "mobile_fallback": False})
    with pytest.raises(httpx.HTTPStatusError, match="403"):
        await build_collector(cfg, ctx).collect()
