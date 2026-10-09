"""상품 사진 (T-09·TH-01·A-16): 깨지거나 작은 사진·로고·배너·게시판 썸네일은 안 올리고, 글자 카드를 블로그 사진으로 내보내지 않는다.

- 쿠팡 사진은 1000x1000 으로 (설정 image_size + CDN 썸네일 주소의 NxNex 칸)
- EXIF 방향을 반영해 바로 세움
- 상품 페이지 사진(og:image)은 최종 주소가 상품 페이지(홈·로그인·검색·이벤트 아님)이고 로고·기본 배너가 아닐 때만
- 디코딩되고 짧은 변 600px 이상일 때만 올림, 아니면 사진 없이
- 블로그 내보내기에는 photo_kind 를 적고, 연습 모드(미리보기)도 실제 상품 사진을 확인해 적는다 (보내지는 않음)
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import yaml
from PIL import Image

from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.coupang.client import parse_api_product
from dealbot.enrich import PageEnricher, PageMeta, is_product_page, page_images
from dealbot.media.imagecheck import clean_image, coupang_image_url, is_generic_image
from dealbot.models import Deal, DealVerdict, Product

ROOT = Path(__file__).resolve().parent.parent


def _jpeg(w: int, h: int, *, orientation: int | None = None) -> bytes:
    img = Image.new("RGB", (w, h), (180, 40, 40))
    img.paste((20, 20, 200), (0, 0, w // 3, h // 5))  # 방향을 알아볼 표시
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        img.save(out, "JPEG", exif=exif)
    else:
        img.save(out, "JPEG")
    return out.getvalue()


# ---------------------------------------------------------------- 1000x1000
def test_coupang_collectors_ask_for_1000px_images() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    sizes = [c["options"]["image_size"] for c in cfg["collectors"] if "image_size" in (c.get("options") or {})]
    assert sizes and all(s == "1000x1000" for s in sizes), sizes


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://thumbnail7.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/images/a.jpg",
         "https://thumbnail7.coupangcdn.com/thumbnails/remote/1000x1000ex/image/retail/images/a.jpg"),
        ("https://thumbnail9.coupangcdn.com/thumbnails/remote/230x230ex/image/vendor_inventory/b.jpg",
         "https://thumbnail9.coupangcdn.com/thumbnails/remote/1000x1000ex/image/vendor_inventory/b.jpg"),
        ("https://thumbnail9.coupangcdn.com/thumbnails/remote/1200x1200ex/image/c.jpg",
         "https://thumbnail9.coupangcdn.com/thumbnails/remote/1200x1200ex/image/c.jpg"),  # 이미 크면 그대로
        ("https://img.example/1.jpg", "https://img.example/1.jpg"),
        (None, None),
    ],
)
def test_coupang_thumbnail_url_is_upsized(url: str | None, want: str | None) -> None:
    assert coupang_image_url(url) == want


def test_api_product_image_is_upsized() -> None:
    raw = {"productId": 1, "productName": "상품", "productPrice": 10000, "productUrl": "https://link.coupang.com/a/x",
           "productImage": "https://thumbnail7.coupangcdn.com/thumbnails/remote/512x512ex/image/a.jpg"}
    p = parse_api_product(raw, "goldbox")
    assert p is not None and p.image_url == "https://thumbnail7.coupangcdn.com/thumbnails/remote/1000x1000ex/image/a.jpg"


# ---------------------------------------------------------------- clean_image: 600px, EXIF
def test_clean_image_needs_600px_and_fixes_orientation() -> None:
    assert clean_image(_jpeg(512, 512)) is None  # 예전 512 쿠팡 사진 크기
    assert clean_image(_jpeg(599, 900)) is None
    assert clean_image(_jpeg(600, 600)) is not None
    sideways = clean_image(_jpeg(1200, 900, orientation=6))  # 휴대폰 사진: 옆으로 누운 채 저장 + 방향 정보
    assert sideways is not None
    img = Image.open(io.BytesIO(sideways))
    assert img.size == (900, 1200) and img.getexif().get(0x0112) in (None, 1)
    assert clean_image(b"<html>login</html>") is None


# ---------------------------------------------------------------- 상품 페이지 사진: 상품 페이지일 때만
@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://smartstore.naver.com/shop/products/123", True),
        ("https://item.gmarket.co.kr/Item?goodscode=1", True),
        ("https://www.gmarket.co.kr/", False),  # 홈으로 돌려보냄
        ("https://nid.naver.com/nidlogin.login?url=x", False),  # 로그인
        ("https://www.11st.co.kr/search?kwd=x", False),  # 검색
        ("https://shop.example.com/event/2026", False),  # 이벤트·기획전
        ("https://shop.example.com/main.html", False),
        ("https://shop.example.com/specialty-coffee/9", True),
        # 리뷰 2차: 이벤트·기획전 호스트, 캠페인·분류 목록, 몰 첫 화면, 스토어 첫 화면(og:image = 스토어 프로필·로고)
        ("https://event.11st.co.kr/x", False),
        ("https://sale.aliexpress.com/__pc/campaign.htm", False),
        ("https://www.coupang.com/np/campaigns/82", False),
        ("https://www.coupang.com/np/categories/186764", False),
        ("https://m.11st.co.kr/MW/html/main.html", False),
        ("https://www.lotteon.com/p/display/main/lotteon", False),
        ("https://smartstore.naver.com/mystore", False),
        ("https://brand.naver.com/samsung", False),
        ("https://m.smartstore.naver.com/mystore/products/123", True),
        ("https://brand.naver.com/samsung/products/9", True),
        ("https://www.coupang.com/vp/products/1", True),
        ("https://shop.example.com/product/name/1234/category/24/display/1/", True),  # 카페24 상품 주소엔 분류 칸이 같이 있음
    ],
)
def test_product_page_detection(url: str, ok: bool) -> None:
    assert is_product_page(url) is ok


def test_page_image_rules() -> None:
    good = PageMeta(final_url="https://smartstore.naver.com/s/products/1", image="https://shop-phinf.pstatic.net/a/og.jpg",
                    ld_image="https://shop-phinf.pstatic.net/a/item.jpg")
    assert page_images(good) == ["https://shop-phinf.pstatic.net/a/item.jpg", "https://shop-phinf.pstatic.net/a/og.jpg"]
    assert page_images(PageMeta(final_url="https://www.gmarket.co.kr/", image="https://gdimg.gmarket.co.kr/1/big.jpg")) == []
    logo = PageMeta(final_url="https://item.gmarket.co.kr/Item?goodscode=1", image="https://pics.gmarket.co.kr/common/logo_og.png")
    assert page_images(logo) == []
    for u in ("https://static.coupangcdn.com/image/coupang/common/logo_coupang_w350.png", "https://cdn.x.com/img/default_banner.jpg",
              "https://cdn.x.com/no_image.png", "https://cdn.x.com/og_image.jpg"):
        assert is_generic_image(u), u
    assert not is_generic_image("https://thumbnail7.coupangcdn.com/thumbnails/remote/1000x1000ex/image/retail/a.jpg")
    # 게시판 썸네일을 바꿔 끼울 때도 같은 규칙 (홈으로 돌려보낸 페이지의 대표 이미지는 안 씀)
    p = Product(source="ppomppu", product_id="gmarket:1", shop="gmarket", name="x", price=10000, url="https://item.gmarket.co.kr/Item?goodscode=1",
                image_url="https://cdn2.ppomppu.co.kr/zboard/data3/small.jpg")
    assert PageEnricher.apply(p, PageMeta(final_url="https://www.gmarket.co.kr/", image="https://gdimg.gmarket.co.kr/1/big.jpg")) == []
    assert p.image_url == "https://cdn2.ppomppu.co.kr/zboard/data3/small.jpg"


@pytest.mark.real_fetch
async def test_relative_og_image_is_resolved() -> None:
    import httpx

    html = '<html><head><meta property="og:image" content="//cdn.shop.example/a/b.jpg"></head></html>'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    enricher = PageEnricher(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    meta = await enricher.fetch("https://shop.example/products/1")
    assert meta is not None and meta.image == "https://cdn.shop.example/a/b.jpg"


# ---------------------------------------------------------------- deal_photo: 확인된 상품 사진만, 아니면 사진 없이
@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _fake_fetch(images: dict[str, bytes], seen: list[str]):  # type: ignore[no-untyped-def]
    async def fetch(url: str, **_kw: object) -> bytes | None:
        seen.append(url)
        return images.get(url)
    return fetch


async def test_deal_photo_upsizes_coupang_and_keeps_the_url(bot) -> None:  # type: ignore[no-untyped-def]
    small = "https://thumbnail7.coupangcdn.com/thumbnails/remote/492x492ex/image/retail/a.jpg"
    big = small.replace("492x492ex", "1000x1000ex")
    seen: list[str] = []
    bot._fetch_image = _fake_fetch({big: _jpeg(1000, 1000), small: _jpeg(492, 492)}, seen)
    d = Deal(Product(source="goldbox", product_id="coupang:1", shop="coupang", name="상품", price=9900,
                     url="https://www.coupang.com/vp/products/1", image_url=small), DealVerdict(is_deal=True))
    photo = await bot.deal_photo(d)
    assert photo is not None and seen[0] == big and d.product.image_url == big  # 스레드도 같은(확인된) 사진 주소를 씀
    assert min(Image.open(io.BytesIO(photo)).size) >= 600


async def test_deal_photo_skips_small_board_and_logo_images(bot) -> None:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    bot._fetch_image = _fake_fetch({"https://img.example/512.jpg": _jpeg(512, 512)}, seen)
    # 512px 사진 → 못 씀 → 사진 없이 (글자 카드도 안 만듦)
    d = Deal(Product(source="goldbox", product_id="coupang:2", shop="coupang", name="상품", price=9900,
                     url="https://www.coupang.com/vp/products/2", image_url="https://img.example/512.jpg"), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None
    # 게시판 목록 썸네일·로고는 받아 보지도 않음
    seen.clear()
    for url in ("https://cdn2.ppomppu.co.kr/zboard/data3/m_thumb_1.jpg", "https://static.coupangcdn.com/image/coupang/common/logo_coupang_w350.png"):
        d = Deal(Product(source="ppomppu", product_id="coupang:3", shop="coupang", name="상품", price=9900,
                         url="https://www.coupang.com/vp/products/3", image_url=url), DealVerdict(is_deal=True))
        assert await bot.deal_photo(d) is None and d.product.image_url is None
    assert seen == []


async def test_deal_photo_uses_page_image_only_from_a_product_page(bot) -> None:  # type: ignore[no-untyped-def]
    page_img = "https://gdimg.gmarket.co.kr/1/big.jpg"
    seen: list[str] = []
    bot._fetch_image = _fake_fetch({page_img: _jpeg(800, 800)}, seen)
    metas = {"good": PageMeta(final_url="https://item.gmarket.co.kr/Item?goodscode=1", image=page_img),
             "home": PageMeta(final_url="https://www.gmarket.co.kr/", image=page_img)}
    which = {"k": "good"}

    async def fetch_page(url: str) -> PageMeta:
        return metas[which["k"]]

    bot.enricher.fetch = fetch_page  # type: ignore[method-assign]

    def deal() -> Deal:
        return Deal(Product(source="ppomppu", product_id="gmarket:1", shop="gmarket", name="상품", price=9900,
                            url="https://item.gmarket.co.kr/Item?goodscode=1", image_url="https://cdn2.ppomppu.co.kr/zboard/data3/m_thumb_1.jpg"),
                    DealVerdict(is_deal=True))

    d = deal()
    assert await bot.deal_photo(d) is not None and d.product.image_url == page_img
    which["k"] = "home"  # 상품 페이지가 홈으로 돌려보냄 → 그 대표 이미지는 상품 사진이 아님
    d = deal()
    assert await bot.deal_photo(d) is None and d.product.image_url is None


# ---------------------------------------------------------------- 블로그 내보내기: photo_kind, 카드는 안 내보냄
def _rows(settings: Settings, name: str) -> list[dict]:
    p = settings.data_dir / name
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


async def test_export_marks_photo_kind_and_never_exports_the_card(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    assert bot.card is not None  # 카드 기능이 켜져 있어도
    d = Deal(Product(source="goldbox", product_id="coupang:5", shop="coupang", name="상품", price=9900,
                     url="https://www.coupang.com/vp/products/5", image_url="https://img.example/tiny.jpg"), DealVerdict(is_deal=True),
             affiliate_url="https://link.coupang.com/a/5")
    photo = await bot.deal_photo(d)  # 받을 수 없는 사진 → None (글자 카드로 대신하지 않음)
    assert photo is None
    bot.export_published(d, photo)
    row = _rows(settings, "published_deals.jsonl")[-1]
    assert row["photo_kind"] == "none" and row["photo_file"] == "" and row["photo_src"] is None

    good = _jpeg(900, 900)
    d.product.image_url = "https://img.example/good.jpg"
    bot.export_published(d, clean_image(good))
    row = _rows(settings, "published_deals.jsonl")[-1]
    assert row["photo_kind"] == "product" and Path(row["photo_file"]).exists() and row["photo_src"] == "https://img.example/good.jpg"
    assert min(Image.open(row["photo_file"]).size) >= 600


@register("fake_photo")
class FakePhotoCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakePhotoCollector.products)


async def test_dry_run_preview_export_has_the_real_photo(settings: Settings) -> None:
    """연습 모드에서도 실제 상품 사진을 확인해 미리보기 내보내기에 적는다 (블로그 미리보기·시험 글에서 사진을 볼 수 있게). 보내지는 않음."""
    from dealbot.app import DealBot

    settings.collectors = [CollectorConfig(name="fake", type="fake_photo", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    bot = DealBot(settings)
    seen: list[str] = []
    bot._fetch_image = _fake_fetch({"https://img.example/p.jpg": _jpeg(1000, 1000)}, seen)  # type: ignore[method-assign]
    sent_photos: list[object] = []
    original = bot.publisher.publish_raw

    async def spy(text: str, **kw: object):  # type: ignore[no-untyped-def]
        sent_photos.append(kw.get("photo"))
        return await original(text, **kw)  # type: ignore[arg-type]

    bot.publisher.publish_raw = spy  # type: ignore[method-assign]
    try:
        assert bot.state.dry_run
        FakePhotoCollector.products = [Product(
            source="fake", product_id="coupang:77", shop="coupang", name="에어프라이어 77", price=39900, discount_rate=60, rank=1,
            url="https://www.coupang.com/vp/products/77", affiliate_url="https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey=77",
            image_url="https://img.example/p.jpg")]
        await bot.run_collector(bot.collectors[0])
        assert await bot.process_queue_once()
        rows = _rows(settings, "preview_deals.jsonl")
        assert rows and rows[-1]["photo_kind"] == "product" and Path(rows[-1]["photo_file"]).exists()
        assert seen == ["https://img.example/p.jpg"]
        assert not _rows(settings, "published_deals.jsonl")  # 실제 발행 기록은 안 생김
    finally:
        await bot.close()
