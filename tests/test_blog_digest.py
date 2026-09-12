"""하루치 발행 딜 → 블로그 글 한 편 (복붙용), 매일 밤 관리자 챗으로."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.digest import split_sections
from dealbot.utils.timeutil import utcnow


def _published(bot: DealBot, product: Product, *, link: str | None, when: datetime, **verdict) -> None:  # type: ignore[no-untyped-def]
    deal = Deal(product=product, verdict=DealVerdict(is_deal=True, reasons=["test"], score=10, **verdict), affiliate_url=link, detected_at=when)
    assert bot.db.enqueue(deal, score=10, now=when)
    item = bot.db.items_for_product(product.product_id, ("pending",))[0]
    bot.db.update_queue_item(item.id, status="published", deal=deal, now=when)
    bot.db.record_post(deal, channel_id="-100", message_id=1, dry_run=False, now=when)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.publish.dry_run = False
    b = DealBot(settings)
    b.state.dry_run = False
    sent: list[str] = []

    async def capture(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    b.notifier.send = capture  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    yield b
    b.db.close()


async def test_blog_digest_collects_the_day(bot: DealBot) -> None:
    now = utcnow()
    _published(
        bot,
        Product(source="ppomppu", product_id="coupang:1", shop="coupang", name="스탠리 텀블러 1.18L", price=29900, original_price=49900,
                url="https://www.coupang.com/vp/products/1", review_count=1200, rating=4.8, shipping="무료"),
        link="https://link.coupang.com/a/abc", when=now - timedelta(hours=5), discount_rate=40.0, market_price=45000, below_market_pct=33.5,
    )
    _published(bot, Product(source="ruliweb_user", product_id="toss:2", shop="toss", name="토스 화장지 30롤", price=12900, url="https://toss.im/_m/ABC"),
               link="https://toss.im/share/xyz", when=now - timedelta(hours=2))
    info = Product(
        source="ppomppu", product_id="info:ppomppu:3", shop="lfmall", name="라코스테 최대 60% 세일", price=0, deal_kind="info",
        url="https://www.ppomppu.co.kr/zboard/view.php?no=3",
        extra={"post_url": "https://www.ppomppu.co.kr/zboard/view.php?no=3", "info_body": "라코스테 최대 60% 세일\n· 기간: 9/11~9/14",
               "info_links": ["https://www.lfmall.co.kr/app/event/105798"]},
    )
    _published(bot, info, link=None, when=now - timedelta(hours=1))
    # 기준 시각(어제 이 시각) 이전 것은 안 들어간다
    _published(bot, Product(source="ppomppu", product_id="coupang:9", shop="coupang", name="옛날 딜", price=1000, url="https://www.coupang.com/vp/products/9"),
               link="https://link.coupang.com/a/old", when=now - timedelta(hours=30))

    msg = await bot.blog_digest(preview=True)
    assert msg.startswith("📝") and "3건" in msg
    assert bot.db.kv_get("blog_digest_last_at") is None  # 미리 보기는 기준 시각을 안 옮긴다
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    assert sent[0].startswith("📝 <b>미리 보기") and "첫 줄이 제목" in sent[0]
    body = [s for s in sent if "<pre>" in s and "네이버 블로그" in s]
    assert len(body) == 1, sent
    text = body[0]
    assert "오늘의 핫딜 3건 정리 — 스탠리 텀블러 1.18L" in text
    assert "■ 1. 스탠리 텀블러 1.18L — 29,900원" in text and "40% 할인" in text and "(정가 49,900원)" in text
    assert "쿠팡 최저가 45,000원보다 34% 저렴" in text and "별점 4.8점, 리뷰 1,200건" in text and "배송: 무료" in text
    assert "👉 구매 링크: https://link.coupang.com/a/abc" in text
    assert "■ 2. 토스 화장지 30롤 — 12,900원" in text and "https://toss.im/share/xyz" in text
    assert "■ 오늘의 이벤트·혜택" in text and "▶ 라코스테 최대 60% 세일" in text and "· 기간: 9/11~9/14" in text
    assert "👉 https://www.lfmall.co.kr/app/event/105798" in text
    assert "옛날 딜" not in text
    assert "쿠팡 파트너스 활동의 일환" in text and "토스쇼핑 쉐어링크 활동의 일환" in text
    assert "링크프라이스" not in text  # 정보 글(LF몰 원문 링크)은 제휴 링크가 아니라 고지 문구를 안 붙인다
    assert "https://t.me/hot_deal_and_info" in text and "open.kakao.com" in text
    tags = [s for s in sent if "블로그 태그" in s]
    assert tags and "핫딜, 오늘의핫딜" in tags[0] and "쿠팡핫딜" in tags[0] and "토스쇼핑핫딜" in tags[0]

    # 실제(밤 21:30) 실행은 기준 시각을 옮기고, 그 뒤엔 새 딜이 없으면 안 보낸다
    assert (await bot.blog_digest()).startswith("📝 블로그 글 문구를 보냈습니다")
    assert bot.db.kv_get("blog_digest_last_at")
    assert "만들 글이 없습니다" in await bot.blog_digest()


async def test_blog_digest_skips_dry_run_posts_in_real_mode(bot: DealBot) -> None:
    now = utcnow()
    p = Product(source="ppomppu", product_id="coupang:5", shop="coupang", name="연습 딜", price=1000, url="https://www.coupang.com/vp/products/5")
    when = now - timedelta(minutes=1)
    deal = Deal(product=p, verdict=DealVerdict(is_deal=True, reasons=["t"], score=1), affiliate_url="https://link.coupang.com/a/x", detected_at=when)
    assert bot.db.enqueue(deal, score=1, now=when)
    item = bot.db.items_for_product(p.product_id, ("pending",))[0]
    bot.db.update_queue_item(item.id, status="published", deal=deal, now=when)
    bot.db.record_post(deal, channel_id=None, message_id=None, dry_run=True, now=when)
    assert "만들 글이 없습니다" in await bot.blog_digest(preview=True)
    bot.state.dry_run = True  # 연습 모드에서는 미리보기 발행도 넣어서 양식을 볼 수 있다
    assert "1건" in await bot.blog_digest(preview=True)


def test_split_sections_keeps_paragraphs_together() -> None:
    text = "제목\n\n소개\n\n" + "\n\n".join(f"■ {i}. 상품 {i}\n· 줄\n👉 링크" for i in range(1, 60))
    chunks = split_sections(text, limit=400)
    assert len(chunks) > 1 and all(len(c) <= 400 for c in chunks)
    assert chunks[0].startswith("제목") and all(c.startswith("■") for c in chunks[1:])
    assert "".join(chunks).count("■") == 59
    assert split_sections("짧은 글", limit=400) == ["짧은 글"]
