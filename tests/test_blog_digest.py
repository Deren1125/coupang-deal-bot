"""하루치 발행 딜 → 블로그 글 한 편 (Deren 양식, 복붙용), 매일 밤 관리자 챗으로."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.digest import particle, split_sections
from dealbot.utils.timeutil import utcnow


def _published(bot: DealBot, product: Product, *, link: str | None, when: datetime, score: float = 10, **verdict) -> None:  # type: ignore[no-untyped-def]
    deal = Deal(product=product, verdict=DealVerdict(is_deal=True, reasons=["test"], score=score, **verdict), affiliate_url=link, detected_at=when)
    assert bot.db.enqueue(deal, score=score, now=when)
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


def test_particles() -> None:
    assert particle("텀블러", "이", "가") == "가" and particle("화장지", "이", "가") == "가"
    assert particle("스탠리 텀블러 1.18L", "이", "가") == "이" and particle("갤럭시 S26", "을", "를") == "을"
    assert particle("스탠리 퀜처", "이", "가") == "가" and particle("에어팟", "이", "가") == "이"


async def test_blog_digest_follows_deren_format(bot: DealBot) -> None:
    now = utcnow()
    _published(
        bot,
        Product(source="ppomppu", product_id="coupang:1", shop="coupang", name="스탠리 텀블러 1.18L", price=29900, original_price=49900,
                url="https://www.coupang.com/vp/products/1", review_count=1200, rating=4.8, shipping="무료"),
        link="https://link.coupang.com/a/abc", when=now - timedelta(hours=5), score=80, discount_rate=40.0, market_price=45000, below_market_pct=33.5,
    )
    _published(bot, Product(source="ruliweb_user", product_id="toss:2", shop="toss", name="토스 화장지 30롤", price=12900, url="https://toss.im/_m/ABC"),
               link="https://toss.im/share/xyz", when=now - timedelta(hours=2), score=30)
    info = Product(
        source="ppomppu", product_id="info:ppomppu:3", shop="lfmall", name="라코스테 최대 60% 세일", price=0, deal_kind="info",
        url="https://www.ppomppu.co.kr/zboard/view.php?no=3",
        extra={"post_url": "https://www.ppomppu.co.kr/zboard/view.php?no=3", "info_body": "라코스테 최대 60% 세일\n· 기간: 9/11~9/14",
               "info_links": ["https://www.lfmall.co.kr/app/event/105798"]},
    )
    _published(bot, info, link=None, when=now - timedelta(hours=1), score=20)
    # 기준 시각(어제 이 시각) 이전 것은 안 들어간다
    _published(bot, Product(source="ppomppu", product_id="coupang:9", shop="coupang", name="옛날 딜", price=1000, url="https://www.coupang.com/vp/products/9"),
               link="https://link.coupang.com/a/old", when=now - timedelta(hours=30))

    msg = await bot.blog_digest(preview=True)
    assert msg.startswith("📝") and "3건" in msg
    assert bot.db.kv_get("blog_digest_last_at") is None  # 미리 보기는 기준 시각을 안 옮긴다
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    head = sent[0]
    assert head.startswith("📝 <b>미리 보기") and "제목 후보" in head and "\n1. " in head and "\n2. " in head and "\n3. " in head
    assert "9월" in head and "스탠리 텀블러 1.18L" in head and ("이라구요??" in head or "팝니다!" in head)
    body = [s for s in sent if "<pre>" in s and "네이버 블로그 본문" in s]
    assert len(body) == 1, sent
    text = body[0]
    # 도입부·채널 초대 블록(고정)
    assert "안녕하세요?\n\nDeren의 아카이브를 운영하고 있는 Deren입니다.\n\n본문 들어가기 전에 하나만 말씀드리면," in text
    assert "텔레그램 오늘의 핫딜이랑 카카오 오픈톡방에 블로그보다 먼저 올리고 있어요!!" in text
    assert "- 텔레그램 · 오늘의 핫딜\nhttps://t.me/hot_deal_and_info\n\n- 카카오톡 · 오늘의 핫딜 오픈채팅\nhttps://open.kakao.com/o/pHi1MkMi" in text
    # 딜 단락: 점수 높은 순, 어미 규칙
    assert "1. 스탠리 텀블러 1.18L\n\n쿠팡에서 스탠리 텀블러 1.18L이 29,900원입니다!\n정가 49,900원에서 40% 내려온 가격이에요!" in text
    assert "쿠팡 최저가 45,000원보다도 34% 싼 가격이구요!" in text
    assert "별점 4.8점에 리뷰가 1,200건이라,\n검증은 충분히 된 제품이에요!\n배송은 무료예요!\n\nhttps://link.coupang.com/a/abc\n" in text
    assert "2. 토스 화장지 30롤\n\n토스쇼핑에서 토스 화장지 30롤이 12,900원입니다!" in text
    assert text.index("1. 스탠리") < text.index("2. 토스")
    # 이벤트 단락: 요약본을 글머리 없이
    assert "오늘은 세일 소식도 있었습니다!\n\n라코스테 최대 60% 세일\n\n라코스테 최대 60% 세일\n기간: 9/11~9/14\n\nhttps://www.lfmall.co.kr/app/event/105798" in text
    assert "옛날 딜" not in text
    # 주의(느낌표 없이) → 마무리 → 대가성·고지 → 면책 → 서명
    assert "다만 몇 가지는 알고 가셔야 합니다.\n\n핫딜은 재고가 빠지면 가격이 원래대로 돌아가고,\n카드 즉시할인은" in text
    assert "본문의 구매 링크는 제 제휴 링크로" in text
    assert "이 포스팅은 쿠팡 파트너스 활동의 일환" in text and "토스쇼핑 쉐어링크 활동의 일환" in text and "링크프라이스" not in text
    assert text.rstrip().endswith("- 카카오톡 · 오늘의 핫딜 오픈채팅\nhttps://open.kakao.com/o/pHi1MkMi\n\n\n궁금한 점은 댓글로 남겨주세요!</pre>")
    assert "※" not in text and "■" not in text and "👉" not in text
    tags = [s for s in sent if "블로그 태그" in s]
    assert tags and "핫딜, 오늘의핫딜" in tags[0] and "쿠팡핫딜" in tags[0] and "토스쇼핑핫딜" in tags[0]

    # 실제(밤 21:30) 실행은 기준 시각을 옮기고, 그 뒤엔 새 딜이 없으면 안 보낸다
    assert (await bot.blog_digest()).startswith("📝 블로그 글 문구를 보냈습니다")
    assert bot.db.kv_get("blog_digest_last_at")
    assert "만들 글이 없습니다" in await bot.blog_digest()


async def test_blog_digest_picks_best_deals_only(bot: DealBot) -> None:
    now = utcnow()
    for i, score in enumerate((5, 50, 20), 1):
        _published(bot, Product(source="ppomppu", product_id=f"coupang:{i}", shop="coupang", name=f"상품 {i}호", price=10000 * i, url=f"https://www.coupang.com/vp/products/{i}"),
                   link=f"https://link.coupang.com/a/{i}", when=now - timedelta(hours=i), score=score)
    bot.settings.blog_digest.max_items = 2
    msg = await bot.blog_digest(preview=True)
    assert "3건 중 점수 높은 2건" in msg or "2건" in msg
    text = [s for s in bot.sent if "<pre>" in s and "네이버 블로그 본문" in s][0]  # type: ignore[attr-defined]
    assert "1. 상품 2호" in text and "2. 상품 3호" in text and "상품 1호" not in text


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
    text = "안녕하세요?\n\n소개\n\n" + "\n\n".join(f"{i}. 상품 {i}\n\n쿠팡에서 상품 {i}이 1,000원입니다!\n\nhttps://x/{i}" for i in range(1, 60))
    chunks = split_sections(text, limit=400)
    assert len(chunks) > 1 and all(len(c) <= 400 for c in chunks)
    assert chunks[0].startswith("안녕하세요?") and "\n\n".join(chunks).count("쿠팡에서 상품") == 59
    assert split_sections("짧은 글", limit=400) == ["짧은 글"]


async def test_blog_digest_drops_bodyless_infos_and_always_discloses_coupang(bot: DealBot) -> None:
    now = utcnow()
    _published(bot, Product(source="ruliweb_user", product_id="toss:2", shop="toss", name="토스 화장지 30롤", price=12900, url="https://toss.im/_m/ABC"),
               link="https://toss.im/share/xyz", when=now - timedelta(hours=2))
    # 옛 방식으로 올라간 정보 글: 정리된 본문이 없다 → 블로그에 넣지 않는다
    old = Product(source="ruliweb_user", product_id="info:ruliweb_user:9", shop="naver", name="일일적립, 클릭 58원 (17)", price=0, deal_kind="info",
                  url="https://bbs.ruliweb.com/market/board/1020/read/9", extra={"post_url": "https://bbs.ruliweb.com/market/board/1020/read/9"})
    _published(bot, old, link=None, when=now - timedelta(hours=1))
    for i in range(4):  # 정리된 이벤트는 max_info_items 개까지만
        p = Product(source="ppomppu", product_id=f"info:ppomppu:{i}", shop="lfmall", name=f"세일 {i}", price=0, deal_kind="info",
                    url=f"https://www.ppomppu.co.kr/zboard/view.php?no={i}", extra={"post_url": f"https://x/{i}", "info_body": f"세일 {i} 요약\n· 기간: 9/1{i}"})
        _published(bot, p, link=None, when=now - timedelta(minutes=30 - i), score=10 + i)
    waiting = Product(source="ppomppu", product_id="naver:77", shop="naver", name="네이버 화장지", price=9900, url="https://smartstore.naver.com/x/products/77")
    deal = Deal(product=waiting, verdict=DealVerdict(is_deal=True, reasons=["t"], score=5), detected_at=now)
    assert bot.db.enqueue(deal, score=5, now=now)
    bot.db.update_queue_item(bot.db.items_for_product("naver:77", ("pending",))[0].id, status="awaiting_link", error="manual link required", deal=deal)
    await bot.blog_digest(preview=True)
    head = bot.sent[0]  # type: ignore[attr-defined]
    assert "핫딜 1 · 정리된 이벤트 4" in head and "본문 없는 옛 정보 글 1건은 뺐습니다" in head
    assert "내 링크를 기다리는 글 1건은 링크를 붙이면 다음 정리에 들어갑니다" in head
    text = [s for s in bot.sent if "<pre>" in s and "네이버 블로그 본문" in s][0]  # type: ignore[attr-defined]
    assert "일일적립" not in text and "read/9" not in text
    assert text.count("요약\n기간:") == 3 and "세일 0" not in text  # 점수 낮은 하나가 빠진다
    assert "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다." in text  # 쿠팡 딜이 없어도 항상
    assert "토스쇼핑 쉐어링크 활동의 일환" in text
