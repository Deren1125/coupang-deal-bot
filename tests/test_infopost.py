"""정보 글(상품 링크 없는 게시판 글) 발행과 관리자 챗 고정 목록."""

from __future__ import annotations

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.infopost import PostBody, extract_post_body
from dealbot.models import Product, PublishResult
from dealbot.monitoring.admin import AdminNotifier

HTML = """
<html><body>
<div class="header"><img src="/img/logo.png"></div>
<div class="view_content">
  <p>페이코 결제 이벤트 안내</p>
  <p>9월 12일 ~ 9월 14일, 12,000원 이상 결제 시 4,800원 할인</p>
  <img src="/img/emoticon/smile.gif">
  <img src="//img.example.com/notice/event_2026.jpg" width="600">
  <img src="/uploads/tiny.png" width="40" height="40">
  <p>https://event.payco.com/promo/123</p>
  <p>자세한 내용은 <a href="https://event.payco.com/promo/123">이벤트 페이지</a> 참고.
     <a href="https://bbs.ruliweb.com/market/board/1020/read/1">관련 글</a></p>
  <p>페이코 결제 이벤트 안내</p>
  <script>alert(1)</script>
</div>
<div class="comments">댓글 영역</div>
</body></html>
"""


def test_extract_post_body() -> None:
    body = extract_post_body(HTML, "https://bbs.ruliweb.com/market/board/1020/read/107150")
    assert body.text.startswith("페이코 결제 이벤트 안내\n9월 12일 ~ 9월 14일, 12,000원 이상 결제 시 4,800원 할인")
    assert "https://event.payco.com" not in body.text.splitlines()[0]
    assert "alert" not in body.text and "댓글 영역" not in body.text
    assert body.text.count("페이코 결제 이벤트 안내") == 1  # 반복 줄 제거
    assert body.images == ["https://img.example.com/notice/event_2026.jpg"]  # 이모티콘·작은 이미지·로고 제외, 절대 주소
    assert body.links == ["https://event.payco.com/promo/123"]  # 게시판 링크 제외


def test_extract_post_body_truncates_and_falls_back() -> None:
    long_html = "<div class='view_content'>" + "".join(f"<p>줄 {i} 내용입니다</p>" for i in range(80)) + "</div>"
    body = extract_post_body(long_html, "https://x", max_chars=120)
    assert len(body.text) <= 125 and body.text.endswith("…") and "\n" in body.text
    assert extract_post_body("<html><body><p>본문 없음</p></body></html>", "https://x").text == "본문 없음"


@register("fake_info")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


def _event(i: int, *, rec: int = 9, url: str | None = None, price: int = 4800) -> Product:
    post = f"https://bbs.ruliweb.com/market/board/1020/read/{i}"
    return Product(
        source="fake", product_id=f"naver:board{i}", shop="naver", name=f"페이코 이벤트 {i}", price=price,
        url=url or post, external_id=str(i), recommend_count=rec, extra={"post_url": post, "title": f"[네이버] 페이코 이벤트 {i}"},
    )


@pytest.fixture
def bot(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_info", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 10
    settings.publish.dry_run = False
    FakeCollector.products = []
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


async def test_board_post_becomes_info_post(bot: DealBot) -> None:
    published: list[dict] = []

    async def fake_fetch(url: str) -> PostBody:
        return PostBody(text="12,000원 이상 결제 시 4,800원 할인", images=["https://img.example.com/e.jpg"], links=["https://event.payco.com/1"])

    async def fake_image(url: str, *, referer: str) -> bytes:
        return b"\x89PNG" + b"0" * 2000

    async def fake_publish_raw(text: str, *, photo=None, preview_url=None, silent=False):  # type: ignore[no-untyped-def]
        published.append({"text": text, "photo": photo, "silent": silent})
        return PublishResult(ok=True, message_id=42)

    bot.info.fetch = fake_fetch  # type: ignore[method-assign]
    bot.info.download_image = fake_image  # type: ignore[method-assign]
    bot.publisher.publish_raw = fake_publish_raw  # type: ignore[method-assign]
    bot.publisher.dry_run = False

    FakeCollector.products = [_event(1), _event(2, rec=2)]  # 두 번째는 추천 부족
    await bot.run_collector(bot.collectors[0])
    counts = bot.db.queue_counts()
    assert counts == {"pending": 1}, counts
    item = bot.db.next_pending()
    assert item is not None and item.deal.product.product_id == "info:fake:1" and item.deal.product.deal_kind == "info"

    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert len(published) == 1
    text = published[0]["text"]
    assert "📢 <b>페이코 이벤트 1</b>" in text and "4,800원" in text and "12,000원 이상 결제 시" in text
    assert "https://event.payco.com/1" in text and "원문: https://bbs.ruliweb.com/market/board/1020/read/1" in text
    assert "카톡 오픈채팅" in text and published[0]["photo"] and published[0]["photo"].startswith(b"\x89PNG")
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    assert any("채널에 올렸습니다" in s for s in sent)
    kakao = [s for s in sent if "카카오 오픈채팅</b> 복사용" in s]
    assert kakao and "실시간 전체 딜(텔레그램)" in kakao[0] and "<pre>" in kakao[0]
    assert bot.db.count_posts_since(item.deal.detected_at, product_prefix="info:") == 1

    # 같은 글은 다시 안 올라가고, 하루 상한을 넘기면 새 글도 안 들어간다
    FakeCollector.products = [_event(1), _event(3)]
    bot.settings.info_posts.max_per_day = 1
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"published": 1}


async def test_no_price_event_with_shop_url_becomes_info_post(bot: DealBot) -> None:
    assert bot.settings.deal.accept_coupons_and_events is False
    FakeCollector.products = [_event(5, url="https://smartstore.naver.com/x/products/1", price=0)]
    await bot.run_collector(bot.collectors[0])
    item = bot.db.next_pending()
    assert item is not None and item.deal.product.deal_kind == "info" and item.deal.product.url.startswith("https://bbs.ruliweb.com")


async def test_info_posts_can_be_disabled(bot: DealBot) -> None:
    bot.settings.info_posts.enabled = False
    FakeCollector.products = [_event(7)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {}


async def test_pending_notice_is_pinned_and_updated(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, list] = {"send": [], "edit": [], "pin": []}

    async def send_with_id(text: str, *, silent: bool = False) -> int:
        calls["send"].append(text)
        return 77

    async def edit(message_id: int, text: str) -> bool:
        calls["edit"].append((message_id, text))
        return True

    async def pin(message_id: int) -> bool:
        calls["pin"].append(message_id)
        return True

    monkeypatch.setattr(AdminNotifier, "enabled", property(lambda self: True))
    bot.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    bot.notifier.edit = edit  # type: ignore[method-assign]
    bot.notifier.pin = pin  # type: ignore[method-assign]

    toss = Product(source="fake", product_id="toss:ABC", shop="toss", name="토스 화장지", price=12900, url="https://toss.im/_m/ABC", recommend_count=9)
    FakeCollector.products = [toss]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()  # 링크 요청 → 고정 목록 생성
    assert bot.db.queue_counts() == {"awaiting_link": 1}
    assert bot.db.kv_get("pending_notice_id") == "77" and calls["pin"] == [77]
    assert calls["send"][0].startswith("📌") and "지금은 없습니다" in calls["send"][0]  # 처음엔 빈 목록으로 고정
    pinned = calls["edit"][-1][1]  # 링크 요청이 생기면 고정 글을 고친다
    assert "📌" in pinned and "토스 화장지" in pinned and "(1건)" in pinned

    item = bot.db.awaiting_items()[0]
    bot.skip_item(item.id)
    await bot.refresh_pending_notice()
    assert calls["edit"] and calls["edit"][-1][0] == 77 and "지금은 없습니다" in calls["edit"][-1][1]
    n_edits = len(calls["edit"])
    await bot.refresh_pending_notice()  # 내용이 같으면 다시 고치지 않는다
    assert len(calls["edit"]) == n_edits
