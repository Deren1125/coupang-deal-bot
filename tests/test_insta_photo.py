"""인스타 카드 사진 = 텔레그램 글에 쓴 확인된 상품 사진 (주인 지적 10/12 '사진 잘 안 들어가는 경우').

예전 make_card 는 deal.product.image_url 을 날것으로 다시 받아서 /instatest 카드에 쿠팡 로고가 크게 들어가고,
외부 반출을 막는 네이버 스토어 사진은 다시 받다 실패해 사진 없는 카드가 됐다. 이제:
1. 카드는 deal_photo 가 고른 사진 바이트를 그대로 쓴다 (다시 받지 않음)
2. 확인된 사진이 없으면 instagram.no_photo: skip(기본) → 인스타엔 안 올리고 기록만, text_card → 사진 없는 글자 카드
3. 로고·기본 이미지는 어느 쪽이든 카드에 안 들어간다
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult

LOGO = "https://static.coupangcdn.com/image/coupang/common/logo_coupang_w350.png"
STORE_URL = "https://brand.naver.com/daekket/products/13008954661"
BOARD_THUMB = "https://cdn4.ppomppu.co.kr/zboard/data/_thumb/ppomppu/1/small_739081.jpg"
OG = "https://shop-phinf.pstatic.net/20260123_260/1769_JPEG/1032.jpeg?type=o1000"
STORE_HTML = f'<html><head><title>데켓 주물팬 : 데켓</title><meta property="og:image" content="{OG}"></head></html>'


def _jpeg(w: int, h: int, color: tuple[int, int, int] = (90, 140, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    settings.secrets.public_domain = "bot.example.com"
    settings.threads.enabled = False
    settings.copy_cfg.enabled = False
    b = DealBot(settings)
    assert b.instagram.dry_run and b.settings.instagram.enabled
    yield b
    b.db.close()


class _Spy:
    """card.save · instagram.publish 가 무엇으로 불렸는지 기록 (진짜 함수도 그대로 부름)."""

    def __init__(self, bot) -> None:  # type: ignore[no-untyped-def]
        self.saved: list[bytes | None] = []
        self.posted: list[str | None] = []
        save, publish = bot.card.save, bot.instagram.publish

        def spy_save(deal: Deal, photo: bytes | None = None, **kw: object) -> Path:
            self.saved.append(photo)
            return save(deal, photo, **kw)  # type: ignore[arg-type]

        async def spy_publish(deal: Deal, image_url: str | None, **kw: object) -> PublishResult:
            self.posted.append(image_url)
            return await publish(deal, image_url, **kw)  # type: ignore[arg-type]

        bot.card.save = spy_save
        bot.instagram.publish = spy_publish


def _fetcher(images: dict[str, bytes], seen: list[str], *, once: bool = False):  # type: ignore[no-untyped-def]
    """가짜 사진 서버. once=True 면 같은 주소를 두 번째로 받을 때 막힘(외부 반출 차단 CDN 처럼)."""

    async def fetch(url: str, **_kw: object) -> bytes | None:
        seen.append(url)
        if once and seen.count(url) > 1:
            return None
        return images.get(url)

    return fetch


def _ig_events(bot) -> list[str]:  # type: ignore[no-untyped-def]
    return [e["message"] for e in bot.db.recent_events(20) if e["kind"] == "instagram"]


# ---------------------------------------------------------------- 1. 로고만 있는 딜: 로고 카드 금지
async def test_logo_only_deal_never_becomes_a_card(bot) -> None:  # type: ignore[no-untyped-def]
    """사진 주소가 쿠팡 로고뿐이면(샘플 딜과 같음) 사진 없음 → 인스타엔 안 올림. 로고가 큰 그림이어도 받아 보지도 않는다."""
    seen: list[str] = []
    bot._fetch_image = _fetcher({LOGO: _jpeg(1200, 1200, (230, 60, 40))}, seen)  # 로고가 크기 검사를 통과할 만큼 커도
    spy = _Spy(bot)
    d = Deal(Product(source="goldbox", product_id="coupang:9", shop="coupang", name="텀블러", price=29900,
                     url="https://www.coupang.com/vp/products/9", image_url=LOGO), DealVerdict(is_deal=True))
    photo = await bot.deal_photo(d)
    assert photo is None and d.product.image_url is None and seen == []
    await bot._publish_side_channels(d, photo=photo, deal_no=9)
    assert spy.saved == [] and spy.posted == [] and seen == []  # 카드도 안 만들고, 사진을 다시 받지도 않음
    assert any("coupang:9 skipped: 확인된 상품 사진 없음" in m for m in _ig_events(bot))
    assert not list((bot.settings.data_dir / "media").glob("*.jpg"))


async def test_instatest_sample_follows_the_same_rule(bot) -> None:  # type: ignore[no-untyped-def]
    """/instatest(샘플 딜)도 실제 딜과 같은 규칙: 샘플의 사진 주소는 쿠팡 로고 → 카드 없이 '안 올림' 안내."""
    bot.db.kv_set("instagram_access_token", "T")
    bot.db.kv_set("instagram_user_id", "1")
    seen: list[str] = []
    bot._fetch_image = _fetcher({}, seen)
    msg = await bot.instagram_test()
    assert "올리지 않습니다: 확인된 상품 사진 없음" in msg and "/media/" not in msg
    assert seen == [] and not list((bot.settings.data_dir / "media").glob("*.jpg"))


async def test_make_card_never_downloads_the_photo_itself(bot) -> None:  # type: ignore[no-untyped-def]
    async def no_fetch(url: str, **_kw: object) -> bytes | None:
        raise AssertionError(f"make_card 가 사진을 다시 받음: {url}")

    bot._fetch_image = no_fetch
    d = Deal(Product(source="goldbox", product_id="coupang:5", shop="coupang", name="상품", price=9900,
                     url="https://www.coupang.com/vp/products/5", image_url=LOGO), DealVerdict(is_deal=True))
    url = await bot.make_card(d, _jpeg(900, 900))
    assert url and url.startswith("https://bot.example.com/media/")
    assert await bot.make_card(d, None) is None  # 사진 없으면 (skip) 카드 없음


# ---------------------------------------------------------------- 2. 네이버 스토어: 크롬으로 읽은 사진을 카드에도
async def test_naver_store_browser_photo_is_the_card_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """일반 요청은 막힌 스토어(conftest), 크롬으로 연 페이지의 og:image 를 deal_photo 가 받음 → 카드도 그 바이트.
    그 CDN 은 두 번째 요청을 막지만(once) 카드는 다시 받지 않으니 사진이 빠지지 않는다."""
    calls: list[str] = []

    async def page_meta(url: str):  # type: ignore[no-untyped-def]
        from dealbot.enrich import page_meta_at

        calls.append(url)
        return page_meta_at(STORE_HTML, STORE_URL)

    bot.store_browser.page_meta = page_meta

    async def no_api(p: Product) -> str | None:
        return None

    bot.naver_shop.find_image = no_api
    seen: list[str] = []
    store_photo = _jpeg(1000, 1000, (120, 80, 60))
    bot._fetch_image = _fetcher({OG: store_photo}, seen, once=True)
    spy = _Spy(bot)
    d = Deal(Product(source="ppomppu", product_id="naver:13008954661", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm",
                     price=60020, url=STORE_URL, image_url=BOARD_THUMB), DealVerdict(is_deal=True),
             affiliate_url=STORE_URL)
    photo = await bot.deal_photo(d)
    assert photo is not None and calls == [STORE_URL] and d.product.image_url == OG
    await bot._publish_side_channels(d, photo=photo, deal_no=12)
    assert spy.saved == [photo]  # 텔레그램에 쓴 바로 그 바이트
    assert seen == [OG]  # 카드 때문에 다시 받지 않음
    assert len(spy.posted) == 1 and (spy.posted[0] or "").startswith("https://bot.example.com/media/")
    card_file = bot.settings.data_dir / "media" / (spy.posted[0] or "").rsplit("/", 1)[-1]
    with Image.open(card_file) as im:
        assert im.format == "JPEG" and im.size == (1080, 1350)
        # 카드 사진 칸 가운데가 상품 사진 색 (글자 카드가 아님)
        r, g, b = im.convert("RGB").getpixel((540, 340))
        assert abs(r - 120) < 30 and abs(g - 80) < 30 and abs(b - 60) < 30


# ---------------------------------------------------------------- 3. 사진 없음: skip(기본) / text_card
async def test_no_photo_skips_instagram_by_default(bot) -> None:  # type: ignore[no-untyped-def]
    assert bot.settings.instagram.no_photo == "skip"
    spy = _Spy(bot)
    d = Deal(Product(source="ppomppu", product_id="gmarket:1", shop="gmarket", name="상품", price=9900,
                     url="https://item.gmarket.co.kr/Item?goodscode=1", image_url=None), DealVerdict(is_deal=True))
    await bot._publish_side_channels(d, photo=None, deal_no=4)
    assert spy.saved == [] and spy.posted == []
    assert any("gmarket:1 skipped" in m for m in _ig_events(bot))


@pytest.mark.parametrize("style", ["current", "A", "B", "C"])
async def test_no_photo_text_card_option_makes_a_clean_card(bot, style: str) -> None:  # type: ignore[no-untyped-def]
    bot.settings.instagram.no_photo = "text_card"
    bot.card.layout = style
    spy = _Spy(bot)
    d = Deal(Product(source="goldbox", product_id="coupang:6", shop="coupang", name="곰곰 특란 30구", price=6900,
                     url="https://www.coupang.com/vp/products/6", image_url=None), DealVerdict(is_deal=True))
    await bot._publish_side_channels(d, photo=None, deal_no=6)
    assert spy.saved == [None] and len(spy.posted) == 1 and (spy.posted[0] or "").endswith(".jpg")
    assert not any("skipped" in m for m in _ig_events(bot))


async def test_publish_hands_the_telegram_photo_to_instagram(bot) -> None:  # type: ignore[no-untyped-def]
    """발행 흐름: 텔레그램 글에 쓴 그 사진 바이트와 큐 번호(핫딜 페이지 /d/번호)가 그대로 인스타 쪽으로 간다."""
    got: dict[str, object] = {}

    async def side(deal: Deal, *, photo: bytes | None = None, deal_no: int | None = None) -> None:
        got.update(photo=photo, deal_no=deal_no)

    async def fake_publish(deal: Deal, **kw: object) -> PublishResult:
        got["telegram_photo"] = kw.get("photo")
        return PublishResult(ok=True, message_id=1)

    bot._publish_side_channels = side
    bot.publisher.publish = fake_publish
    bot.settings.publish.min_interval_seconds = 0
    bot.state.dry_run = False
    seen: list[str] = []
    bot._fetch_image = _fetcher({"https://img.example/p.jpg": _jpeg(900, 900)}, seen)
    d = Deal(Product(source="goldbox", product_id="coupang:77", shop="coupang", name="에어프라이어 77", price=39900,
                     discount_rate=60, url="https://www.coupang.com/vp/products/77", image_url="https://img.example/p.jpg",
                     affiliate_url="https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey=77"),
             DealVerdict(is_deal=True, discount_rate=60), affiliate_url="https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey=77")
    assert bot.db.enqueue(d, score=40.0)
    assert await bot.process_queue_once()
    assert got.get("photo") is not None and got["photo"] is got["telegram_photo"] and got["deal_no"] == 1
    assert seen == ["https://img.example/p.jpg"]  # 사진은 한 번만 받음


# ---------------------------------------------------------------- 4. 스레드가 예외로 끝나도 인스타는 올린다
@pytest.mark.parametrize("boom", ["retryable", "timeout"])
async def test_threads_server_error_does_not_skip_instagram(bot, boom: str) -> None:  # type: ignore[no-untyped-def]
    """스레드가 5xx·429 를 재시도 뒤에도 돌려주거나(RetryableError) 시간 초과로 끝나도: 스레드 실패만 알리고 인스타·복붙 문구는 그대로."""
    import httpx

    from dealbot.utils.retry import RetryableError

    bot.settings.threads.enabled = True
    bot.threads.dry_run = True  # 연결된 것처럼 (실제 요청은 아래 가짜가 받음)

    async def threads_down(deal: Deal, **_kw: object) -> PublishResult:
        if boom == "retryable":
            raise RetryableError("threads api 500: upstream error")
        raise httpx.ReadTimeout("timed out")

    bot.threads.publish = threads_down
    sent: list[str] = []

    async def send(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    bot.notifier.send = send
    spy = _Spy(bot)
    d = Deal(Product(source="goldbox", product_id="coupang:8", shop="coupang", name="에어프라이어", price=39900,
                     url="https://www.coupang.com/vp/products/8"), DealVerdict(is_deal=True))
    await bot._publish_side_channels(d, photo=_jpeg(900, 900), deal_no=8)
    assert len(spy.posted) == 1  # 인스타는 올라감
    assert any("스레드 발행 실패" in t for t in sent)
    assert any("coupang:8" in e["message"] for e in bot.db.recent_events(20) if e["kind"] == "threads")
