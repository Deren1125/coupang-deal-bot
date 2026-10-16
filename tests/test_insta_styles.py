"""인스타그램 양식 A/B/C (주인 선택 10/12: B).

- 카드: 세 양식 모두 1080×1350 JPEG, 사진 있음·없음(글자 카드)·아주 긴 이름
- 본문: 첫 줄은 고지 '광고 · …' (주인 결정 10/15 — 공정위 지침: 첫 부분에), 둘째 줄은 사실(이름·가격·데이터로 확인된 비교 가격)만,
  홍보 말·느낌표·링크 주소 없음, 프로필 링크 안내, 끝에 몰 고지 원문, 해시태그 3~5개 (인스타 게시물당 5개 제한)
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from dealbot.commentary import BANNED, THREAD_BANNED
from dealbot.config import load_settings
from dealbot.media.card import DealCard, H, W
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher import insta_style
from dealbot.publisher.instagram import InstagramPublisher
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry

ROOT = Path(__file__).resolve().parent.parent
REG = ShopRegistry()
LONG = "[1+1] 크리넥스 3겹 데코앤소프트 화이트 천연펄프 두루마리 화장지 30m 30롤 x 2팩 (총 60롤) 대용량 생활필수품"


@pytest.fixture
def r() -> TemplateRenderer:
    return TemplateRenderer(ROOT / "templates", "Asia/Seoul", {"telegram_url": "https://t.me/x"})


def _photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1000, 1000), "#FFFFFF").save(buf, "JPEG")
    return buf.getvalue()


def _market_deal(name: str = "도브 인텐스 리페어 샴푸 680ml 2개") -> Deal:
    return Deal(
        Product(source="goldbox", product_id="coupang:1", shop="coupang", name=name, price=15900, original_price=25800,
                url="https://www.coupang.com/vp/products/1", category="뷰티>헤어케어>샴푸", is_rocket=True,
                is_free_shipping=True, rating=4.8, review_count=12431),
        DealVerdict(is_deal=True, discount_rate=38.4, market_price=22900, below_market_pct=30.6, low_price=16900,
                    history_days=30, sample_count=12),
        affiliate_url="https://link.coupang.com/a/1",
        detected_at=datetime(2026, 10, 15, 5, 12, tzinfo=UTC),  # 14:12 KST — 기록 최저 문구의 기준 시각
    )


def _plain_naver_deal() -> Deal:
    """비교 근거 없음 (평균·시중가 없음) — 첫 줄에 비교 가격을 지어내면 안 됨."""
    return Deal(
        Product(source="ppomppu", product_id="naver:1", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm", price=60020,
                original_price=89000, url="https://brand.naver.com/daekket/products/1", category="주방용품>프라이팬>그릴팬",
                shipping="무료배송"),
        DealVerdict(is_deal=True, discount_rate=32.6),
        affiliate_url="https://naver.me/x",
    )


def _pub(r: TemplateRenderer) -> InstagramPublisher:
    return InstagramPublisher(None, None, r, registry=REG)  # type: ignore[arg-type]


# ---------------------------------------------------------------- 설정
def test_config_keeps_the_current_style_until_the_owner_picks() -> None:
    """예전엔 no_photo: skip. 주인 결정 10/15: 확인된 사진이 없는 딜도 건너뛰지 않고 글자 카드로 (text_card).
    인스타 전용 대기열 설정: 하루 3개·07~23시·2시간 간격·12시간 지나면 버림, 저녁 TOP5 캐러셀·점심 릴스·화금 단가표.
    양식은 주인 결정 10/16 으로 B(가격 정보 카드) → P(종이 카드 '1+2 섞은 안'). B 는 되돌리기용으로 남음."""
    s = load_settings(ROOT / "config.yaml", load_env=False)
    ig = s.instagram
    assert ig.style == "P" and ig.no_photo == "text_card" and ig.info_posts is False
    assert (ig.daily_singles, ig.hours, ig.min_gap_minutes, ig.queue_expire_hours) == (3, "07:00-23:00", 120, 12)
    assert (ig.carousel.enabled, ig.carousel.window, ig.carousel.max_deals, ig.carousel.min_deals) == (True, "20:00-21:00", 5, 2)
    assert (ig.reels.enabled, ig.reels.window, ig.reels.max_deals, ig.reels.min_deals) == (True, "12:00-13:00", 3, 2)
    assert ig.unit_carousel.enabled and ig.unit_carousel.days == ["tue", "fri"] and ig.unit_carousel.window == "19:00-20:00"


def test_style_accepts_lowercase() -> None:
    from dealbot.config import InstagramConfig

    assert InstagramConfig(style="b").style == "B"  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        InstagramConfig(style="D")  # type: ignore[arg-type]


# ---------------------------------------------------------------- 카드
@pytest.mark.parametrize("layout", ["A", "B", "C"])
@pytest.mark.parametrize("name", ["도브 인텐스 리페어 샴푸 680ml 2개", LONG])
def test_variant_cards_render(r: TemplateRenderer, tmp_path: Path, layout: str, name: str) -> None:
    card = DealCard(tmp_path, layout=layout)
    for deal in (_market_deal(name), _plain_naver_deal()):
        info = insta_style.card_info(deal, r, shop=REG.get(deal.product.shop), deal_no=1532)
        for photo in (_photo(), None, b"broken"):
            raw = card.render(deal, photo, info=info)
            with Image.open(io.BytesIO(raw)) as im:
                assert im.format == "JPEG" and im.size == (W, H)
    path = card.save(_market_deal(), _photo(), info=insta_style.card_info(_market_deal(), r, shop=REG.get("coupang")))
    assert path.exists() and path != DealCard(tmp_path).save(_market_deal(), _photo())  # 양식마다 다른 파일


@pytest.mark.parametrize("layout", ["current", "A", "B", "C"])
def test_single_cards_never_strike_a_price_that_is_not_our_own_record(r: TemplateRenderer, tmp_path: Path, layout: str,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """표시광고법: 쿠팡·네이버 최저가(다른 판매처 값)·표시 정가·평소 가격대를 취소선으로 그리면 이 상품의 종전 가격처럼 읽힌다.
    딜 하나짜리 카드(모든 양식, 사진 있음·없음)는 취소선을 긋지 않고 '쿠팡 최저가 22,900원보다' 처럼 따로 쓴다."""
    from PIL import ImageDraw

    from dealbot.media import card_variants

    def no_strike(*a: object, **kw: object) -> int:
        raise AssertionError("single card struck a comparison price")

    monkeypatch.setattr(card_variants, "_strike", no_strike)
    drawn: list[str] = []
    text = ImageDraw.ImageDraw.text

    def spy(self: ImageDraw.ImageDraw, xy: object, t: str, *a: object, **kw: object) -> None:
        drawn.append(t)
        text(self, xy, t, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    card = DealCard(tmp_path, layout=layout)
    for deal in (_market_deal(), _plain_naver_deal()):
        info = insta_style.card_info(deal, r, shop=REG.get(deal.product.shop), deal_no=3)
        for photo in (_photo(), None):
            card.render(deal, photo, info=info)
    if layout in ("A", "C"):  # 사진 없는 카드(글자 카드)·A 사진 카드: 비교 줄은 '…원보다' 로 따로
        assert "쿠팡 최저가 22,900원보다" in drawn and "정가 89,000원보다" in drawn
    if layout == "current":
        assert "정가 25,800원" in drawn  # 표시 정가는 취소선 없이 '정가' 를 붙여서


def test_card_info_uses_only_data(r: TemplateRenderer) -> None:
    """기록 최저 문구는 주인 결정 10/15 로 바뀜: 예전 '30일 최저가 갱신' → '우리가 본 최근 30일 중 가장 낮음 (MM/DD HH시 기준)'."""
    info = insta_style.card_info(_market_deal(), r, shop=REG.get("coupang"), deal_no=7)
    assert info.ref_text == "쿠팡 최저가 22,900원" and info.ref_pct == 31
    assert info.low_label == "우리가 본 최근 30일 중 가장 낮음 (10/15 14시 기준)"
    assert info.ship == "무료 로켓배송" and info.deal_no == 7 and info.disclosure == "쿠팡 파트너스 활동으로 수수료를 받습니다"
    assert info.ad == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다"
    plain = insta_style.card_info(_plain_naver_deal(), r, shop=REG.get("naver"))
    assert plain.ref_price is None and plain.ref_text == "" and plain.low_label is None
    assert plain.list_price == 89000  # 표시 정가는 보조로만


# ---------------------------------------------------------------- 본문
@pytest.mark.parametrize("style", ["A", "B", "C"])
def test_variant_captions_follow_the_rules(r: TemplateRenderer, style: str) -> None:
    pub = _pub(r)
    for deal, shop in ((_market_deal(), "coupang"), (_market_deal(LONG), "coupang"), (_plain_naver_deal(), "naver")):
        text = pub.render(deal, deal_no=1532, style=style)
        first, second = text.splitlines()[:2]
        price = f"{deal.product.price:,}원"
        # 첫 줄 = 고지 (주인 결정 10/15, 예전엔 첫 줄이 이름 + 가격), 둘째 줄 = 이름 + 가격 (사실)
        assert first == f"광고 · {insta_style.short_disclosure(REG.get(shop).disclosure)}"  # type: ignore[union-attr]
        assert price in second
        if deal.verdict.market_price:
            assert "쿠팡 최저가 22,900원" in second
        else:
            assert "최저가" not in text and "평소" not in text  # 근거 없는 비교는 안 씀
        assert not insta_style.validate(text, allowed=["쿠팡 최저가"] if deal.verdict.market_price else [])
        assert not BANNED.search(text.replace("https://", "")), text  # '역대급'·'강추'·느낌표·★ 등
        assert not THREAD_BANNED.search(text), text
        assert "http" not in text  # 인스타 본문 링크는 눌리지 않음 → 주소를 안 씀
        assert "프로필 링크" in text and "#1532" in text
        assert REG.get(shop).disclosure in text  # type: ignore[union-attr]
        tags = re.findall(r"(?<!\S)#[^\s#\d][^\s#]*", text)
        assert 3 <= len(tags) <= insta_style.MAX_HASHTAGS and len(set(tags)) == len(tags), tags
        assert "#핫딜" in tags and "#오늘의핫딜" in tags
        assert len(text) <= 2200


def test_default_style_caption_is_unchanged(r: TemplateRenderer) -> None:
    pub = _pub(r)
    deal = _market_deal()
    assert pub.render(deal) == r.render_deal(deal, deal.affiliate_url or "", shop=REG.get("coupang"),
                                             template="deal_instagram.j2", autoescape=False)


def test_b_caption_has_plain_info_lines_instead_of_save_and_share_prompts(r: TemplateRenderer) -> None:
    """예전 test_b_caption_has_save_and_share_prompts: '저장해 두세요'·'같이 쓸 가족·친구가 있으면 보내 주세요' 가 있었다.
    주인 결정 10/15 (조사: 인스타는 저장·공유를 조르는 글을 추천에서 뺄 수 있음): 그 줄을 빼고 사실 안내 줄로."""
    text = _pub(r).render(_market_deal(), deal_no=3, style="B")
    assert "저장해" not in text and "보내 주세요" not in text and "✔ 가격  15,900원" in text
    lines = text.splitlines()
    assert lines[0] == "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다"
    assert lines[1] == "도브 인텐스 리페어 샴푸 680ml 2개 15,900원 · 쿠팡 최저가 22,900원"
    assert "✔ 1개당  7,950원 · 100mL당 1,169원" in lines  # 카드 표 둘째 줄과 같은 줄
    for line in ("1개당 가격은 카드 표 둘째 줄에 있어요", "가격·재고는 올린 시점 기준이에요",
                 "실시간 전체 딜은 텔레그램에 먼저 올라와요 (프로필 링크)", "구매 링크: 프로필 링크 → 핫딜 페이지 #3"):
        assert line in lines
    assert "✔ 기록  우리가 본 최근 30일 중 가장 낮음 (10/15 14시 기준)" in lines
    assert "최저가 갱신" not in text and "역대" not in text


def test_hashtags_are_relevant_and_capped() -> None:
    tags = insta_style.hashtags(_market_deal(LONG), REG.get("coupang"))
    assert tags[:2] == ["#핫딜", "#쿠팡핫딜"] and len(tags) <= 5
    naver = insta_style.hashtags(_plain_naver_deal(), REG.get("naver"))
    assert "#네이버쇼핑" in naver and "#그릴팬" in naver and "#살림템" in naver


async def test_deals_page_shows_numbers_only_for_variant_styles(settings) -> None:  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    bot = DealBot(settings)
    try:
        deal = _market_deal()
        assert bot.db.enqueue(deal, score=40.0)
        bot.db.update_queue_item(1, status="published", deal=deal)
        bot.db.record_post(deal, channel_id=None, message_id=1, dry_run=True)
        bot.settings.instagram.style = "current"
        _, body = await bot.web.dispatch("GET", "/")
        assert 'class="no"' not in body  # current 는 예전 페이지 그대로
        bot.settings.instagram.style = "A"
        _, body = await bot.web.dispatch("GET", "/")
        assert '<span class="no">#1</span>' in body
    finally:
        await bot.close()


async def test_numbered_page_lists_every_deal_and_caption_numbers_only_page_deals(settings, ig_post_now) -> None:  # type: ignore[no-untyped-def]
    """번호 양식이면 핫딜 페이지가 기간 안의 딜을 점수로 자르지 않고 모두 번호 순(최근 먼저)으로 보여 준다 —
    인스타 글의 '#번호' 를 페이지에서 찾을 수 있게. 페이지에 없는 글(기간이 지난 글)엔 카드·본문에 번호를 적지 않는다."""
    from datetime import timedelta

    from dealbot.app import DealBot
    from dealbot.models import PublishResult
    from dealbot.utils.timeutil import utcnow

    settings.collectors = []
    bot = DealBot(settings)
    try:
        bot.settings.instagram.style = "B"
        bot.settings.web.page_max_items = 3
        for i in range(1, 6):  # 최근 글일수록 점수가 낮음 → 점수로 3개만 자르면 #4·#5 가 빠졌다
            d = _market_deal(f"샴푸 {i}호")
            d.product.product_id = f"coupang:{i}"
            assert bot.db.enqueue(d, score=50.0 - i)
            bot.db.update_queue_item(i, status="published", deal=d)
            bot.db.record_post(d, channel_id=None, message_id=i, dry_run=True)
        info = _market_deal("이벤트 안내")
        info.product.product_id, info.product.deal_kind = "info:1", "info"
        assert bot.db.enqueue(info, score=99.0)
        bot.db.update_queue_item(6, status="published", deal=info)
        _, body = await bot.web.dispatch("GET", "/")
        shown = [int(n) for n in re.findall(r'<span class="no">#(\d+)</span>', body)]
        assert shown == [5, 4, 3, 2, 1]  # 정보 글은 빠지고, 낮은 점수의 최근 글도 모두
        bot.settings.instagram.style = "current"
        _, body = await bot.web.dispatch("GET", "/")
        assert body.count('href="/d/') == 3  # 예전 양식은 그대로 점수 순 상위 page_max_items
        bot.settings.instagram.style = "B"

        old = _market_deal("오래된 샴푸")
        old.product.product_id = "coupang:old"
        assert bot.db.enqueue(old, score=10.0)
        bot.db.update_queue_item(7, status="published", deal=old, now=utcnow() - timedelta(hours=60))
        assert bot._page_deal_no(5) == 5 and bot._page_deal_no(7) is None and bot._page_deal_no(None) is None

        got: list[int | None] = []

        async def publish(deal: Deal, image_url: str | None, *, deal_no: int | None = None) -> PublishResult:
            got.append(deal_no)
            return PublishResult(ok=True, dry_run=True)

        bot.instagram.publish = publish  # type: ignore[method-assign]
        new = _market_deal("샴푸 5호")
        new.product.product_id = "coupang:5"
        await bot._publish_side_channels(new, photo=_photo(), deal_no=5)
        await bot._publish_side_channels(old, photo=_photo(), deal_no=7)
        assert got == []  # 인스타는 바로 안 올리고 대기열로 (주인 결정 10/15)
        await ig_post_now(bot)
        await ig_post_now(bot)
        assert got == [5, None]  # 페이지에 없는 글엔 '#번호' 를 적지 않음 (번호는 올리는 때 다시 봄)
        assert "핫딜 페이지 #7" not in bot.instagram.render(old, deal_no=bot._page_deal_no(7))
    finally:
        await bot.close()
