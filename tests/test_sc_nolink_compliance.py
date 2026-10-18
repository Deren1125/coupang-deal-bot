"""링크 없이 올린 네이버 딜(쇼핑 커넥트에 없음 — 수수료 없음)의 고지 (주인 10/18).

- 제휴 딜: 고지는 예전처럼 늘 첫 부분에 (텔레그램 둘째 줄 '광고 · …', 스레드 첫 줄, 인스타 첫 줄 + 카드 맨 위 '광고' 칩)
- 수수료 없는 딜: 어디에도 '수수료' 말이 없다 — 텔레그램·스레드·인스타 본문·인스타 카드('광고' 칩 없음)·핫딜 페이지('제휴 아님')
- 블로그 내보내기(published_deals.jsonl): affiliate=false · no_affiliate=까닭 · disclosure='' → Blog-Auto 가 이 딜 몫 고지를 붙이지 않게
- 여러 딜을 묶은 인스타 글(TOP5·릴스)은 글 전체가 광고라 장마다 고지 그대로
가짜만 쓴다 (네트워크 없음).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from PIL import ImageDraw

from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.media import card_paper, card_slides
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher import insta_style

URL = "https://smartstore.naver.com/paulham/products/1001"
LINK = "https://naver.me/xAbC12"
NAVER_DISCLOSURE = "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다."


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    settings.collectors = []
    settings.secrets.public_domain = "bot.example.com"
    b = DealBot(settings)
    yield b
    b.db.close()


def _deal(raw: str | None = "owner_no_sc", **kw: Any) -> Deal:
    p = dict(source="ppomppu", product_id="naver:1001", shop="naver", name="폴햄 여성 에코베로 라운드 긴팔티", price=9900,
             original_price=19900, url=URL, category="패션의류>여성의류>티셔츠", shipping="무료배송")
    p.update(kw)
    prod = Product(**p)  # type: ignore[arg-type]
    if raw:
        prod.extra["raw_link"] = raw
    return Deal(prod, DealVerdict(is_deal=True, discount_rate=50), affiliate_url=URL if raw else LINK,
                detected_at=datetime(2026, 10, 18, 3, 0, tzinfo=UTC))


def _drawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    orig = ImageDraw.ImageDraw.text

    def spy(d: ImageDraw.ImageDraw, xy: Any, t: str, *a: Any, **kw: Any) -> None:
        seen.append(t)
        orig(d, xy, t, *a, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    return seen


# ---------------------------------------------------------------- 텔레그램·스레드·카톡 글
def test_channel_threads_and_kakao_text_carry_no_commission_claim(bot: DealBot) -> None:
    shop = bot.registry.get("naver")
    raw = _deal()
    for template in ("deal_post.j2", "deal_threads.j2", "deal_kakao.j2", "deal_instagram.j2"):
        text = bot.renderer.render_deal(raw, URL, shop=shop, template=template, autoescape=template == "deal_post.j2")
        assert "수수료" not in text and "광고" not in text and "쇼핑 커넥트" not in text, template
    paid = _deal(None)
    text = bot.publisher.render(paid)
    assert text.splitlines()[1] == "광고 · 네이버 쇼핑 커넥트 수수료" and NAVER_DISCLOSURE in text  # 제휴 딜은 그대로


# ---------------------------------------------------------------- 인스타 (딜 하나짜리 글)
def test_instagram_single_post_has_no_ad_chip_and_passes_the_check(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    shop = bot.registry.get("naver")
    info = insta_style.card_info(_deal(), bot.renderer, shop=shop, deal_no=12)
    assert info.ad_free and info.ad == "" and info.disclosure == ""
    spec = card_paper.deal_spec(info, None)
    assert spec.ad == "" and not any("수수료" in t or "광고" in t for t in spec.texts())
    seen = _drawn(monkeypatch)
    card_paper.render(spec)
    assert "광고" not in seen and not any("수수료" in t for t in seen)  # 카드에 '광고' 칩·고지 줄 없음
    monkeypatch.undo()
    caption = bot.instagram.render(_deal(), deal_no=12, style="P")
    assert "수수료" not in caption and not caption.startswith("광고")
    assert bot.instagram.check(_deal(), deal_no=12, slides=[spec]) == []
    # 제휴 딜은 예전처럼 첫 줄·카드 맨 위 고지가 있어야 통과
    paid = _deal(None)
    pinfo = insta_style.card_info(paid, bot.renderer, shop=shop, deal_no=12)
    assert not pinfo.ad_free and pinfo.ad == "광고 · 네이버 쇼핑 커넥트 활동으로 수수료를 받습니다"
    pspec = card_paper.deal_spec(pinfo, None)
    assert bot.instagram.render(paid, deal_no=12, style="P").startswith("광고 · 네이버 쇼핑 커넥트")
    assert bot.instagram.check(paid, deal_no=12, slides=[pspec]) == []
    assert bot.instagram.check(paid, deal_no=12, slides=[spec])  # 고지 없는 카드를 제휴 딜에 쓰면 막힘


def test_validate_rejects_a_commission_claim_on_a_non_affiliate_post() -> None:
    assert insta_style.validate("폴햄 긴팔티 9,900원", ad_free=True) == []
    assert insta_style.validate("광고 · 제휴 링크로 수수료를 받아요\n폴햄 긴팔티", ad_free=True)
    assert insta_style.validate("폴햄 긴팔티 9,900원")  # 제휴 딜 기준이면 첫 줄 고지가 없어 막힘


def test_other_card_layouts_draw_no_disclosure_for_a_non_affiliate_deal(bot: DealBot) -> None:
    info = insta_style.card_info(_deal(), bot.renderer, shop=bot.registry.get("naver"))
    assert card_slides.ad_of(info) == ""
    spec = card_slides.deal_spec_for("B", info, None)
    assert not any("수수료" in t for t in spec.texts())
    if bot.card is not None:
        card_slides.render(bot.card, spec)  # 띠에 채널 이름만 (그려지는지)


def test_bundles_keep_the_ad_on_every_slide(bot: DealBot) -> None:
    """TOP5·릴스: 글 전체가 광고 → 수수료 없는 딜 장에도 고지 (ig_scheduler._picks 가 ad_free 를 끈다)."""
    info = replace(insta_style.card_info(_deal(), bot.renderer, shop=bot.registry.get("naver")), ad_free=False)
    assert card_slides.ad_of(info) == card_slides.GENERIC_AD
    assert card_paper.deal_spec(info, None).texts()[0] == card_slides.GENERIC_AD


# ---------------------------------------------------------------- 핫딜 페이지 · 블로그 내보내기
async def test_deals_page_marks_plain_links(bot: DealBot) -> None:
    bot.state.dry_run = True
    for i, deal in enumerate((_deal(), _deal(None, product_id="naver:2002", url="https://smartstore.naver.com/x/products/2002")), 1):
        assert bot.db.enqueue(deal, score=10.0)
        bot.db.update_queue_item(i, status="published", deal=deal)
        bot.db.record_post(deal, channel_id=None, message_id=i, dry_run=True)
    status, body = await bot._deals_page({})
    assert status == 200 and body.count("제휴 아님") == 2  # 맨 위 안내 한 번 + 원본 주소 딜 한 번
    assert "구매 링크는 제휴 아님 표시가 없으면 제휴 링크라" in body


def test_blog_export_row_tells_blogauto_there_is_no_affiliate_link(bot: DealBot, settings: Settings) -> None:
    bot.export_published(_deal())
    bot.export_published(_deal(None))
    rows = [json.loads(x) for x in (settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").splitlines()]
    raw, paid = rows
    assert raw["affiliate"] is False and raw["no_affiliate"] == "owner_no_sc" and raw["disclosure"] == ""
    assert raw["link"] == URL == raw["product_url"] and "수수료" not in raw["kakao_text"]
    assert paid["affiliate"] is True and paid["no_affiliate"] == "" and paid["disclosure"] == NAVER_DISCLOSURE
    assert paid["link"] == LINK
