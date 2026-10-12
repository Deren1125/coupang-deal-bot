"""인스타그램 후보 양식 A/B/C (주인이 고르기 전까지 기본은 current).

- 카드: 세 양식 모두 1080×1350 JPEG, 사진 있음·없음(글자 카드)·아주 긴 이름
- 본문: 첫 줄은 사실(이름·가격·데이터로 확인된 비교 가격)만, 홍보 말·느낌표·링크 주소 없음, 프로필 링크 안내,
  고지 문구, 해시태그 3~5개 (인스타 게시물당 5개 제한)
"""

from __future__ import annotations

import io
import re
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
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.instagram.style == "B" and s.instagram.no_photo == "skip" and s.instagram.info_posts is False


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


def test_card_info_uses_only_data(r: TemplateRenderer) -> None:
    info = insta_style.card_info(_market_deal(), r, shop=REG.get("coupang"), deal_no=7)
    assert info.ref_text == "쿠팡 최저가 22,900원" and info.ref_pct == 31 and info.low_label == "30일 최저가 갱신"
    assert info.ship == "무료 로켓배송" and info.deal_no == 7 and info.disclosure == "쿠팡 파트너스 활동으로 수수료를 받습니다"
    plain = insta_style.card_info(_plain_naver_deal(), r, shop=REG.get("naver"))
    assert plain.ref_price is None and plain.ref_text == "" and plain.low_label is None
    assert plain.list_price == 89000  # 표시 정가는 보조로만


# ---------------------------------------------------------------- 본문
@pytest.mark.parametrize("style", ["A", "B", "C"])
def test_variant_captions_follow_the_rules(r: TemplateRenderer, style: str) -> None:
    pub = _pub(r)
    for deal, shop in ((_market_deal(), "coupang"), (_market_deal(LONG), "coupang"), (_plain_naver_deal(), "naver")):
        text = pub.render(deal, deal_no=1532, style=style)
        first = text.splitlines()[0]
        price = f"{deal.product.price:,}원"
        assert price in first  # 첫 줄 = 이름 + 가격 (사실)
        if deal.verdict.market_price:
            assert "쿠팡 최저가 22,900원" in first
        else:
            assert "최저가" not in text and "평소" not in text  # 근거 없는 비교는 안 씀
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


def test_b_caption_has_save_and_share_prompts(r: TemplateRenderer) -> None:
    text = _pub(r).render(_market_deal(), deal_no=3, style="B")
    assert "저장해 두세요" in text and "보내 주세요" in text and "✔ 가격  15,900원" in text


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
