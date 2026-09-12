from __future__ import annotations

from pathlib import Path

import httpx
import pytest

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
    t5 = parse_title("[롯데온] 칠성사이다 제로 355ml 24캔 (14,150원/무료)", reg)
    assert t5["name"] == "칠성사이다 제로 355ml 24캔" and t5["price"] == 14150 and t5["shop"].key == "lotteon"
    t6 = parse_title("[G마켓] 나랑드사이다 제로 345ml 24입 [10,370원 / 무료배송]", reg)
    assert t6["name"] == "나랑드사이다 제로 345ml 24입" and t6["price"] == 10370
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


@pytest.mark.real_fetch
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


@pytest.mark.real_fetch
async def test_submit_manual_enriches(settings: Settings) -> None:
    from dealbot.app import DealBot

    settings.collectors = []
    bot = DealBot(settings)
    try:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><head><meta property='og:image' content='https://img/t.jpg'></head><body>별점 4.7 리뷰 12건</body></html>")

        bot.enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        msg = await bot.submit_manual("/post\n상품: 한입 삼겹살 500g 3팩\n가격: 9,990원\nhttps://toss.im/_m/x3ayNq1B")
        assert "맨 앞에 넣었습니다" in msg
        item = bot.db.next_pending()
        assert item is not None and item.deal.product.image_url == "https://img/t.jpg" and item.deal.product.rating == 4.7
        text = bot.publisher.render(item.deal)
        assert "별점 4.7 · 리뷰 12건" in text and "가격: 9,990원" in text
    finally:
        await bot.close()


def test_parse_title_does_not_mangle_names_or_take_conditions_as_prices() -> None:
    reg = ShopRegistry()
    t = parse_title("[던킨도너츠] 네이버페이로 12000원 결제시 4800원 할인 외 (9/...", reg)
    assert t["shop"] is None and t["price"] is None  # "네이버"가 "네이버페이로"의 앞부분이라고 몰로 잡지 않고, 조건·혜택 금액은 가격이 아니다
    assert t["name"] == "네이버페이로 12000원 결제시 4800원 할인 외"
    t = parse_title("[음식] [롯데온] 농심 빵부장 솔티꽈베기빵8개+소금빵8개+굿즈증정 (14,220...", reg)
    assert t["shop"] is not None and t["shop"].key == "lotteon" and t["price"] is None
    assert t["name"] == "농심 빵부장 솔티꽈베기빵8개+소금빵8개+굿즈증정"  # 잘린 괄호는 뗀다
    assert parse_title("[네이버] 네이버페이 5000원 적립 이벤트", reg)["price"] is None
    t = parse_title("[생활용품] [토스] 올챌린지 천연펄프 화장지 30롤, 2팩 (12,900원/무료)", reg)
    assert t["shop"].key == "toss" and t["price"] == 12900 and t["name"] == "올챌린지 천연펄프 화장지 30롤, 2팩"


_LIST = """<table class="board_list_table"><tbody>
<tr class="table_body"><td class="id">1</td><td class="subject"><div class="relative"><a class="deco" href="/market/board/1020/read/107141">[생활용품] [토스] 올챌린지 천연펄프 화장지 30롤, 2팩 (12,9...</a></div></td><td class="recomd">17</td><td class="hit">3000</td><td class="time">10:00</td><td class="writer">a</td></tr>
<tr class="table_body"><td class="id">2</td><td class="subject"><a class="deco" href="/market/board/1020/read/107142">[음식] [롯데온] 농심 빵부장 솔티꽈베기빵8개+소금빵8개+굿즈증정 (14,220...</a></td><td class="recomd">29</td><td class="hit">5000</td><td class="time">10:00</td><td class="writer">b</td></tr>
<tr class="table_body"><td class="id">3</td><td class="subject"><a class="deco" href="/market/board/1020/read/107150">[던킨도너츠] 네이버페이로 12000원 결제시 4800원 할인 외 (9/...</a></td><td class="recomd">12</td><td class="hit">800</td><td class="time">10:00</td><td class="writer">c</td></tr>
</tbody></table>"""
_DETAIL = {
    "107141": """<html><body><h4 class="subject"><span class="subject_text">[생활용품] [토스] 올챌린지 천연펄프 화장지 30롤, 2팩 (12,900원/무료)</span> [9]</h4>
<div class="source_url box_line_with_shadow"><span class="text_bar">출처 : </span><a href="https://toss.shopping/t/40594131">https://toss.shopping/t/40594131</a></div>
<div class="view_content"><p>화장지 사는데 엄청 저렴하길래 들고 왔습니다.</p><img src="https://i1.ruliweb.com/img/a.webp"></div></body></html>""",
    "107142": """<html><body><span class="subject_text">[음식] [롯데온] 농심 빵부장 솔티꽈베기빵8개+소금빵8개+굿즈증정 (14,220원/무료)</span>
<div class="source_url"><span class="text_bar">출처 : </span><a href="https://web.ruliweb.com/link.php?ol=https%3A%2F%2Fwww.lotteon.com%2Fp%2Fproduct%2FLO2767238184&amp;bbs=1020">https://www.lotteon.com/p/product/LO2767238184</a></div>
<div class="view_content"><p>1봉당 880원 꼴 나옵니다</p></div></body></html>""",
    "107150": """<html><body><span class="subject_text">[던킨도너츠] 네이버페이로 12000원 결제시 4800원 할인 외 (9/12~9/14)</span>
<div class="source_url"><a href="https://www.dunkindonuts.co.kr/event/view?id=5443">https://www.dunkindonuts.co.kr/event/view?id=5443</a></div>
<div class="view_content"><p>행사 매장은 링크 상단에서 확인할 수 있습니다.</p></div></body></html>""",
}


async def test_collect_reads_full_title_and_source_box(settings: Settings, db: Database) -> None:
    """목록 제목은 잘려 있고 쇼핑몰 링크는 '출처' 상자에 있다 — 상세에서 전체 제목·가격·링크를 다시 읽는다."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/market/board/1020":
            return httpx.Response(200, text=_LIST, headers={"content-type": "text/html; charset=utf-8"})
        body = _DETAIL.get(req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"}) if body else httpx.Response(404)

    reg = settings.shop_registry()
    lotteon = reg.get("lotteon")
    assert lotteon is not None
    lotteon.enabled, lotteon.disabled_reason = True, None
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=reg)
    cfg = CollectorConfig(name="ruliweb_user", type="ruliweb", options={"board_id": "1020", "request_delay_seconds": 0})
    products = {p.external_id: p for p in await build_collector(cfg, ctx).collect()}
    toss = products["107141"]
    assert toss.shop == "toss" and toss.price == 12900 and toss.url == "https://toss.shopping/t/40594131" and toss.product_id == "toss:40594131"
    assert toss.name == "올챌린지 천연펄프 화장지 30롤, 2팩" and toss.extra["title"].endswith("(12,900원/무료)")
    lo = products["107142"]
    assert lo.shop == "lotteon" and lo.price == 14220 and lo.url == "https://www.lotteon.com/p/product/LO2767238184"  # 리다이렉트를 풀어 몰 주소로
    assert lo.name == "농심 빵부장 솔티꽈베기빵8개+소금빵8개+굿즈증정"
    ev = products["107150"]  # 모르는 몰의 가격 없는 이벤트 글은 정보 글 후보로 남긴다
    assert ev.shop == "unknown" and ev.price == 0 and ev.url == "https://www.dunkindonuts.co.kr/event/view?id=5443"
    assert ev.name == "네이버페이로 12000원 결제시 4800원 할인 외 (9/12~9/14)" and ev.extra["post_url"].endswith("/read/107150")  # 기간은 남긴다
