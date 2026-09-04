from __future__ import annotations

from pathlib import Path

import httpx

from dealbot.collectors import CollectorContext, build_collector
from dealbot.collectors.ruliweb import DEFAULT_SELECTORS, parse_list, parse_title
from dealbot.config import CollectorConfig, Settings
from dealbot.enrich import PageEnricher, parse_page_meta
from dealbot.models import Product
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database


def test_parse_title_variants() -> None:
    reg = ShopRegistry()
    t = parse_title("[음식] [토스쇼핑]애슐리크리스피핫도그4종,80g,8개입,2세트,14890원,첫구매추가할인", reg)
    assert t["shop"].key == "toss" and t["price"] == 14890 and t["tags"] == ["음식", "토스쇼핑"]
    assert t["name"] == "애슐리크리스피핫도그4종, 80g, 8개입, 2세트"
    t2 = parse_title("토스쇼핑쟌슨빌 더진한 부대찌개 500g 3개 15830원첫구매 추가할인", reg)
    assert t2["shop"].key == "toss" and t2["price"] == 15830 and t2["name"] == "쟌슨빌 더진한 부대찌개 500g 3개"
    t3 = parse_title("[쿠팡] 푸드센터 소갈비살 200g 5팩 (29,500원/무료)", reg)
    assert t3["shop"].key == "coupang" and t3["price"] == 29500 and t3["name"].startswith("푸드센터 소갈비살")
    t4 = parse_title("제목만", reg)
    assert t4["shop"] is None and t4["price"] is None


def test_parse_list(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "ruliweb_list.html").read_text(encoding="utf-8")
    items = parse_list(html, DEFAULT_SELECTORS, ShopRegistry())
    assert [i["external_id"] for i in items] == ["132094", "132066", "132010"]  # 공지 제외
    assert items[0]["views"] == 163 and items[0]["recommend"] == 0 and items[0]["writer"] == "CIRCUIT2"
    assert items[1]["post_url"] == "https://bbs.ruliweb.com/market/board/600004/read/132066"


async def test_collect_toss_only(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/market/board/600004":
            return httpx.Response(200, content=(fixtures_dir / "ruliweb_list.html").read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        m = req.url.path.rsplit("/", 1)[-1]
        f = fixtures_dir / f"ruliweb_view_{m}.html"
        if f.exists():
            return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="ruliweb_biz", type="ruliweb", options={"board_id": "600004", "shops": ["toss"], "request_delay_seconds": 0})
    products = await build_collector(cfg, ctx).collect()
    assert [p.product_id for p in products] == ["toss:7GuMJHbn", "toss:ABCDEF"]
    assert products[0].price == 14890 and products[0].url == "https://toss.im/_m/7GuMJHbn" and products[0].view_count == 163
    assert not any("132010" in c for c in calls)  # 쿠팡 글은 상세 요청 안 함
    calls.clear()
    again = await build_collector(cfg, ctx).collect()
    assert all("/read/" not in c for c in calls)
    assert [p.external_id for p in again] == ["132066"]  # 추천 1 → 재판정, 132094 는 추천0·조회163


def test_parse_page_meta_og_and_jsonld() -> None:
    html = """<html><head>
    <meta property="og:title" content="한입 삼겹살 500g 3팩">
    <meta property="og:image" content="https://img/x.jpg">
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"한입 삼겹살 500g 3팩",
      "offers":{"@type":"Offer","price":"9990","highPrice":"29700"},
      "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.7","reviewCount":"12"}}</script>
    </head><body>본문</body></html>"""
    m = parse_page_meta(html)
    assert m.title == "한입 삼겹살 500g 3팩" and m.image == "https://img/x.jpg"
    assert m.price == 9990 and m.original_price == 29700 and m.rating == 4.7 and m.review_count == 12

    html2 = "<html><head><meta property='og:title' content='x'><meta property='product:price:amount' content='14890'></head><body>별점 4.5 리뷰 1,234건</body></html>"
    m2 = parse_page_meta(html2)
    assert m2.price == 14890 and m2.rating == 4.5 and m2.review_count == 1234


async def test_enricher_fills_blanks_only() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><head><meta property='og:title' content='페이지 제목'><meta property='og:image' content='https://img/p.jpg'><meta property='product:price:amount' content='9990'></head><body>평점 4.7 리뷰 12건</body></html>")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    en = PageEnricher(http)
    p = Product(source="s", product_id="toss:1", shop="toss", name="한입 삼겹살", price=0, url="https://toss.im/_m/1")
    meta = await en.fetch(p.url)
    assert meta is not None
    filled = PageEnricher.apply(p, meta)
    assert set(filled) == {"image_url", "price", "rating", "review_count"}
    assert p.name == "한입 삼겹살" and p.price == 9990 and p.rating == 4.7 and p.review_count == 12


async def test_submit_manual_enriches(settings: Settings) -> None:
    from dealbot.app import DealBot

    settings.collectors = []
    bot = DealBot(settings)
    try:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><head><meta property='og:image' content='https://img/t.jpg'></head><body>별점 4.7 리뷰 12건</body></html>")

        bot.enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        msg = await bot.submit_manual("/post\n상품: 한입 삼겹살 500g 3팩\n가격: 9,990원\nhttps://toss.im/_m/x3ayNq1B")
        assert "대기열" in msg
        item = bot.db.next_pending()
        assert item is not None and item.deal.product.image_url == "https://img/t.jpg" and item.deal.product.rating == 4.7
        text = bot.publisher.render(item.deal)
        assert "별점 4.7 · 리뷰 12건" in text and "가격: 9,990원" in text
    finally:
        await bot.close()
