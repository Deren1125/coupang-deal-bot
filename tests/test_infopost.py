"""정보 글(상품 링크 없는 게시판 글) 발행과 관리자 챗 고정 목록."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import dealbot.app as app_mod
from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.infopost import InfoPostBuilder, PostBody, extract_post_body
from dealbot.models import Deal, DealVerdict, Product, PublishResult
from dealbot.monitoring.admin import AdminNotifier
from dealbot.publisher.templates import TemplateRenderer
from dealbot.summarize import InfoSummarizer, Summary

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


def test_extract_post_body_truncates_and_never_dumps_the_page() -> None:
    long_html = "<div class='view_content'>" + "".join(f"<p>줄 {i} 내용입니다</p>" for i in range(80)) + "</div>"
    body = extract_post_body(long_html, "https://x", max_chars=120)
    assert len(body.text) <= 125 and body.text.endswith("…") and "\n" in body.text
    assert len(body.raw) > len(body.text) and body.confident
    # 아는 본문 상자가 없고 글자도 몇 자 없으면 빈 본문 (예전처럼 페이지 전체를 내보내지 않는다)
    empty = extract_post_body("<html><body><p>본문 없음</p></body></html>", "https://x")
    assert empty.text == "" and empty.empty and not empty.confident
    # 메뉴가 잔뜩인 페이지: 링크 투성이 메뉴·댓글은 제치고 글자가 많은 덩어리를 본문으로 추정 (confident=False)
    nav = "".join(f'<li><a href="/m{i}">메뉴 {i}</a></li>' for i in range(40))
    page = (
        f"<html><body><div id='gnb'><ul>{nav}</ul></div><div class='wrap'><div class='post'>"
        "<p>9월 한 달간 네이버페이로 결제하면 최대 5,000원이 적립됩니다.</p><p>1일 1회, 선착순 1만 명. 마이페이지에서 응모한 뒤 결제하면 됩니다.</p>"
        "</div><div class='comment_list'>" + "<p>감사합니다 좋은 정보네요</p>" * 10 + "</div></div></body></html>"
    )
    body = extract_post_body(page, "https://x")
    assert not body.confident and body.text.startswith("9월 한 달간") and "메뉴 1" not in body.text and "감사합니다" not in body.text


def test_extract_ppomppu_cell_with_duplicate_class_attribute() -> None:
    """실제 뽐뿌 본문 셀은 class 속성이 두 번 적혀 있다 — 뒷값(han)만 남으면 셀렉터가 빗나가 메뉴가 통째로 올라갔었다."""
    html = """<html><body><div class='menu'><a href='/a'>뽐뿌게시판</a><a href='/b'>휴대폰뽐뿌</a><a href='/c'>해외뽐뿌</a></div>
    <table><tr><td class='board-contents' align="left" valign=top class=han>
    <p>라코스테 60%까지 세일한다고 알람이 와서 정리해 봅니다.</p>
    <img src="//cdn4.ppomppu.co.kr/zboard/data3/2026/0911/900w_a.jpg">
    <p>상품명: 남성 해링턴 자켓 정상가: 449000원 할인가: 179600원 할인율: 60%</p>
    <a href="https://s.ppomppu.co.kr?idno=ppomppu_733532&target=aHR0cHM6Ly93d3cubGZtYWxsLmNvLmtyL2FwcC9ldmVudC8xMDU3OTg=&en">https://www.lfmall.co.kr/app/event/105798</a>
    <a href="https://www.ppomppu.co.kr/zboard/zboard.php?id=ppomppu">목록</a>
    </td></tr></table></body></html>"""
    body = extract_post_body(html, "https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=733532")
    assert body.confident and body.text.startswith("라코스테 60%까지")
    assert "뽐뿌게시판" not in body.text and "목록" not in body.text.splitlines()
    assert body.images == ["https://cdn4.ppomppu.co.kr/zboard/data3/2026/0911/900w_a.jpg"]
    assert body.links == ["https://www.lfmall.co.kr/app/event/105798"]  # 뽐뿌 리다이렉트(target=base64) 를 풀어 원래 주소로


GALAXY = (
    "<html><body><table><tr><td class='board-contents' class=han>"
    "<p>&gt; 카드 즉시할인 최대 10%</p>"
    + "".join(f"<p>▶ Galaxy Tab S12 모델{i} 1,{i}99,000원</p><p>&gt; 쿠폰 할인 최대 7%</p>" for i in range(6))
    + "<p>- 기간: 10/7~10/20</p><p>-10% 추가 쿠폰은 앱 전용</p>"
    "<p>사전 구매하신 분들 좋은 딜 되세요~</p></td></tr></table></body></html>"
)


def test_extract_post_body_keeps_per_item_lines_and_board_markers_become_bullets() -> None:
    """같은 줄 지우기는 바로 위 줄 반복·제목 반복만: 상품마다 붙은 '> 쿠폰…' 줄이 첫 상품에만 남으면 쿠폰이 한 모델 얘기처럼 보인다."""
    body = extract_post_body(GALAXY, "https://www.ppomppu.co.kr/zboard/view.php?id=coupon&no=1", max_chars=2000)
    lines = body.text.splitlines()
    assert body.text.count("쿠폰 할인 최대 7%") == 6
    assert not [ln for ln in lines if ln.lstrip().startswith((">", "▶"))]  # 게시판 인용·화살표 표시는 '· ' 글머리로
    assert lines[0] == "· 카드 즉시할인 최대 10%" and lines[1] == "· Galaxy Tab S12 모델0 1,099,000원"
    assert lines[2] == "  └ 쿠폰 할인 최대 7%"  # 상품 줄 바로 아래 '>' 줄은 그 상품에 딸린 설명
    assert "· 기간: 10/7~10/20" in lines and "-10% 추가 쿠폰은 앱 전용" in lines  # '-10%' 는 글머리가 아니라 숫자
    assert "좋은 딜 되세요" not in body.text  # 끝인사(잡담)는 뺀다
    keep = extract_post_body(GALAXY.replace("사전 구매하신 분들 좋은 딜 되세요~", "마이페이지에서 응모하시면 되세요"), "https://x", max_chars=2000)
    assert keep.text.endswith("마이페이지에서 응모하시면 되세요")  # 안내 문장은 끝에 있어도 남긴다


def test_extract_post_body_drops_other_peoples_affiliate_links() -> None:
    """남의 쿠팡 파트너스·링크프라이스 링크를 공시 없이 퍼 나르지 않는다 (lptag 는 떼고 원래 주소만 남김)."""
    html = (
        "<div class='view_content'><p>이벤트 안내 글입니다. 기간 안에 응모하면 적립금을 줍니다.</p>"
        '<p><a href="https://link.coupang.com/a/cXyZ12">https://link.coupang.com/a/cXyZ12</a></p>'
        '<p>구매는 https://link.coupang.com/a/abc9 여기서</p>'
        '<p><a href="https://coupa.ng/bXk1">쿠팡</a> <a href="https://click.linkprice.com/click.php?m=11st&a=A1&l=9999">링크</a>'
        ' <a href="https://toss.im/_m/AbCd">토스</a></p>'
        '<p><a href="https://www.11st.co.kr/products/1?lptag=P0001234&trTypeCd=22">11번가</a>'
        ' <a href="https://pages.coupang.com/p/177293">쿠팡 이벤트</a></p></div>'
    )
    body = extract_post_body(html, "https://bbs.ruliweb.com/market/board/1020/read/1")
    assert body.links == ["https://www.11st.co.kr/products/1?trTypeCd=22", "https://pages.coupang.com/p/177293"]
    assert "link.coupang.com" not in body.text and "lptag" not in body.raw and "구매는 여기서" in body.text


def _info_deal(name: str = "★[쿠팡] 갤럭시 탭 S12 쿠폰 이벤트★") -> Deal:
    post = "https://www.ppomppu.co.kr/zboard/view.php?id=coupon&no=1"
    p = Product(source="ppomppu", product_id="info:ppomppu:1", shop="coupang", name=name, price=0, url=post, extra={"post_url": post})
    return Deal(product=p, verdict=DealVerdict(is_deal=True))


def test_info_templates_follow_channel_rules(repo_root: Path) -> None:
    """정보 글도 딜 글과 같은 원칙: 채널 링크를 글마다 붙이지 않고, 긴 주소 대신 글자 링크, 장식 뺀 제목."""
    channels = {"kakao_openchat_url": "https://open.kakao.com/o/x", "telegram_url": "https://t.me/x"}
    info = InfoPostBuilder(None, TemplateRenderer(repo_root / "templates", channels=channels))  # type: ignore[arg-type]
    long = "\n".join(f"▶ Galaxy Tab S12 Ultra {i}56GB Wi-Fi 1,{i}99,000원" for i in range(9))
    body = PostBody(text=long, links=["https://pages.coupang.com/p/177293", "https://link.coupang.com/a/old"])

    tg = info.render("info_post.j2", _info_deal(), body, autoescape=True)
    assert tg.startswith("📢 <b>[쿠팡] 갤럭시 탭 S12 쿠폰 이벤트</b>") and "★" not in tg
    assert '<a href="https://pages.coupang.com/p/177293">이벤트 보러 가기</a>' in tg
    assert '<a href="https://www.ppomppu.co.kr/zboard/view.php?id=coupon&amp;no=1">원문 보기</a>' in tg
    assert "카톡 오픈채팅" not in tg and "open.kakao.com" not in tg and "t.me" not in tg

    th = info.render("info_threads.j2", _info_deal(), body, autoescape=False)
    assert "t.me" not in th and "실시간 전체 딜" not in th and len(th) <= 500
    cut = th.split("\n\n")[1]  # 제목 다음 본문 덩어리: 줄 중간이 아니라 줄 끝에서 자르고 '…'
    assert cut.endswith("…") and all(ln in long.splitlines() for ln in cut.rstrip("…").splitlines())

    kakao = info.render("info_kakao.j2", _info_deal(), body, autoescape=False)
    assert "텔레그램" not in kakao and "t.me" not in kakao and "🔗 https://pages.coupang.com/p/177293" in kakao
    assert "link.coupang.com" not in tg + th + kakao  # 예전에 저장된 초안의 남의 제휴 링크도 내보내지 않음


@register("fake_info")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


def _event(i: int, *, rec: int = 9, url: str | None = None, price: int = 0) -> Product:
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
    bot.settings.monitoring.notify_on_publish = True  # 기본 설정은 꺼져 있음 — 알림 내용을 보는 테스트

    async def fake_fetch(url: str) -> PostBody:
        return PostBody(text="12,000원 이상 결제 시 6,000원 할인", images=["https://img.example.com/e.jpg"], links=["https://event.payco.com/1"])

    async def fake_image(url: str, *, referer: str) -> bytes:
        return b"\x89PNG" + b"0" * 2000

    async def fake_publish_raw(text: str, *, photo=None, preview_url=None, silent=False):  # type: ignore[no-untyped-def]
        published.append({"text": text, "photo": photo, "silent": silent})
        return PublishResult(ok=True, message_id=42)

    async def fake_summarize(*, title: str, source: str, text: str, links=None) -> Summary:  # type: ignore[no-untyped-def]
        assert title == "페이코 이벤트 1" and "12,000원" in text and links == ["https://event.payco.com/1"]
        return Summary(text="12,000원 이상 결제하면 6,000원 할인\n· 기간: 9월 12일~14일", discount_rate=0, discount_amount=6000)

    bot.info.fetch = fake_fetch  # type: ignore[method-assign]
    bot.info.download_image = fake_image  # type: ignore[method-assign]
    bot.publisher.publish_raw = fake_publish_raw  # type: ignore[method-assign]
    bot.publisher.dry_run = False
    bot.summarizer.summarize = fake_summarize  # type: ignore[method-assign]

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
    assert "📢 <b>페이코 이벤트 1</b>" in text and "· 기간: 9월 12일~14일" in text and "결제 시 6,000원 할인" not in text  # 요약본으로 대체
    assert '<a href="https://event.payco.com/1">이벤트 보러 가기</a>' in text  # 긴 주소 대신 글자 링크 (딜 글과 같은 양식)
    assert '<a href="https://bbs.ruliweb.com/market/board/1020/read/1">원문 보기</a>' in text
    # 오픈채팅 안내는 글마다 넣지 않음 (채널 설명·고정 공지에만)
    assert "카톡 오픈채팅" not in text and published[0]["photo"] and published[0]["photo"].startswith(b"\x89PNG")
    sent: list[str] = bot.sent  # type: ignore[attr-defined]
    assert any("채널에 올렸습니다" in s for s in sent)
    kakao = [s for s in sent if "카카오 오픈채팅</b> 복사용" in s]
    assert kakao and "<pre>" in kakao[0] and "텔레그램" not in kakao[0].split("<pre>", 1)[1]
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


@pytest.fixture
def admin_calls(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    calls: dict[str, list] = {"send": [], "edit": [], "pin": []}

    async def send_with_id(text: str, *, silent: bool = False) -> int:
        calls["send"].append(text)
        return 100 + len(calls["send"])

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
    return calls


def _wire(bot: DealBot, *, body: PostBody | None, published: list[dict]) -> None:
    async def fake_fetch(url: str) -> PostBody | None:
        return body

    async def fake_image(url: str, *, referer: str) -> bytes:
        return b"\x89PNG" + b"0" * 2000

    async def fake_publish_raw(text: str, *, photo=None, preview_url=None, silent=False):  # type: ignore[no-untyped-def]
        published.append({"text": text, "photo": photo})
        return PublishResult(ok=True, message_id=42)

    bot.info.fetch = fake_fetch  # type: ignore[method-assign]
    bot.info.download_image = fake_image  # type: ignore[method-assign]
    bot.publisher.publish_raw = fake_publish_raw  # type: ignore[method-assign]
    bot.publisher.dry_run = False


def _reviews(bot: DealBot) -> list:
    return bot.db.awaiting_items(statuses=("awaiting_approval",))


async def test_info_post_waits_for_approval_when_summary_unavailable(bot: DealBot, admin_calls: dict[str, list]) -> None:
    """요약기가 없으면(키 없음) 원문을 난사하지 않고 관리자에게 먼저 보여 준다. 답장으로 고쳐 쓴 글이 그대로 올라간다."""
    published: list[dict] = []
    _wire(bot, body=PostBody(text="원문 줄 1 최대 50% 할인\n원문 줄 2", images=["https://img.example.com/e.jpg"], links=[]), published=published)
    assert not bot.summarizer.configured  # 테스트 환경에는 ANTHROPIC_API_KEY 가 없다
    FakeCollector.products = [_event(11)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_approval": 1} and published == []
    notice = [s for s in admin_calls["send"] if s.startswith("📝")]
    assert len(notice) == 1 and "정보 글 확인 #" in notice[0] and "ANTHROPIC_API_KEY" in notice[0]
    assert "원문 줄 1" in notice[0] and "/ok" in notice[0] and "사진 1장" in notice[0]
    pinned = admin_calls["edit"][-1][1]
    assert "내가 확인해 줘야 하는 글" in pinned and "(1건)" in pinned and "페이코 이벤트 11" in pinned
    item = _reviews(bot)[0]
    assert bot.db.kv_get(f"review_notice:{item.id}") == str(100 + len(admin_calls["send"]))

    # 확인을 기다리는 동안 같은 글이 다시 들어오지 않고, 링크를 붙이려 하면 /ok 를 안내한다
    FakeCollector.products = [_event(11)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"awaiting_approval": 1}
    assert "/ok" in await bot.attach_link(item.id, "https://x")
    assert "글이 없습니다" in bot.approve_item(item.id + 500)

    # 답장으로 고쳐 쓴 본문으로 승인 → 다음 차례에 그대로 올라간다 (글을 다시 읽지 않음)
    async def must_not_refetch(url: str) -> PostBody:
        raise AssertionError("approved draft must be published as-is")

    bot.info.fetch = must_not_refetch  # type: ignore[method-assign]
    msg = bot.approve_item(item.id, "네이버페이 결제 시 4,800원 할인\n· 기간: 9/12~9/14")
    assert msg.startswith("✅") and "바꿨습니다" in msg and bot.db.queue_counts() == {"pending": 1}
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1} and len(published) == 1
    text = published[0]["text"]
    assert "· 기간: 9/12~9/14" in text and "원문 줄 1" not in text and "📢 <b>페이코 이벤트 11</b>" in text and published[0]["photo"]
    await bot.refresh_pending_notice()
    assert "확인해 줘야" not in admin_calls["edit"][-1][1]
    assert "확인을 기다리는 글이 아닙니다" in bot.approve_item(item.id)  # 이미 올라간 글


async def test_review_always_then_plain_ok_publishes_summary(bot: DealBot, admin_calls: dict[str, list]) -> None:
    published: list[dict] = []
    _wire(bot, body=PostBody(text="원문", images=[], links=["https://event.payco.com/1"]), published=published)

    async def fake_summarize(**kw) -> Summary:  # type: ignore[no-untyped-def]
        return Summary(text="요약 본문\n· 조건: 1만원 이상", discount_rate=50, discount_amount=0)

    bot.summarizer.summarize = fake_summarize  # type: ignore[method-assign]
    bot.settings.info_posts.review = "always"
    FakeCollector.products = [_event(12)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_approval": 1}
    notice = [s for s in admin_calls["send"] if s.startswith("📝")][0]
    assert "항상 확인" in notice and "요약 본문" in notice
    item = _reviews(bot)[0]
    assert bot.approve_item(item.id).startswith("✅")
    assert await bot.process_queue_once()
    assert len(published) == 1 and "· 조건: 1만원 이상" in published[0]["text"] and "https://event.payco.com/1" in published[0]["text"]


async def test_summarizer_skip_unconfident_body_and_unreadable_post(bot: DealBot, admin_calls: dict[str, list]) -> None:
    published: list[dict] = []
    # 요약기가 "올릴 가치 없음"(SKIP) → 조용히 건너뜀
    _wire(bot, body=PostBody(text="광고 글", images=[], links=[]), published=published)

    async def skip(**kw) -> Summary:  # type: ignore[no-untyped-def]
        return Summary(skip=True)

    bot.summarizer.summarize = skip  # type: ignore[method-assign]
    FakeCollector.products = [_event(13)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 1} and not published

    # 본문 위치를 추정으로 고른 글은 요약이 잘 됐어도 확인을 받는다
    _wire(bot, body=PostBody(text="추정 본문", images=[], links=[], confident=False), published=published)

    async def ok(**kw) -> Summary:  # type: ignore[no-untyped-def]
        return Summary(text="요약된 추정 본문 내용", discount_rate=0, discount_amount=10000)

    bot.summarizer.summarize = ok  # type: ignore[method-assign]
    FakeCollector.products = [_event(14)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 1, "awaiting_approval": 1}
    assert "본문 위치를 확실히 찾지 못해" in [s for s in admin_calls["send"] if s.startswith("📝")][-1]

    # 글을 아예 못 읽으면 건너뜀
    _wire(bot, body=None, published=published)
    FakeCollector.products = [_event(15)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 2, "awaiting_approval": 1}

    # 확인 대기도 시간이 지나면 버려진다 (내 링크 대기와 같은 유효 시간)
    now = datetime.now(UTC)
    assert bot.db.expire_queue(now - timedelta(days=1), now, awaiting_older_than=now + timedelta(hours=1)) == 1
    assert bot.db.queue_counts() == {"skipped": 2, "expired": 1}


async def test_small_benefit_info_posts_are_skipped(bot: DealBot, admin_calls: dict[str, list]) -> None:
    """이벤트성 글은 할인율 40% 이상 또는 할인 금액 5,000원 이상일 때만 올린다 (난사 방지)."""
    published: list[dict] = []
    _wire(bot, body=PostBody(text="12,000원 이상 결제 시 4,800원 할인", images=[], links=[]), published=published)
    answers: list[Summary] = []

    async def fake_summarize(**kw) -> Summary:  # type: ignore[no-untyped-def]
        return answers.pop(0)

    bot.summarizer.summarize = fake_summarize  # type: ignore[method-assign]

    # 요약기가 읽은 혜택이 둘 다 기준 미만 → 조용히 건너뜀 (확인 요청도 없음)
    answers.append(Summary(text="1만원 이상 결제하면 4,800원 할인", discount_rate=20, discount_amount=4800))
    FakeCollector.products = [_event(21)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 1} and not published
    assert not [s for s in admin_calls["send"] if s.startswith("📝")]
    item = bot.db.get_queue_item(1)
    assert item is not None and "benefit below threshold" in (item.last_error or "") and "4,800원" in item.last_error

    # 요약기가 혜택을 안 알려 주면 본문 숫자로 본다: "4,800원 할인" 은 미달, 조건 금액 12,000원은 혜택으로 치지 않는다
    answers.append(Summary(text="1만원 이상 결제하면 4,800원 할인"))
    FakeCollector.products = [_event(22)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 2}

    # 할인율 60% → 통과해서 올라간다
    answers.append(Summary(text="라코스테 최대 60% 세일\n· 기간: 9/11~9/14", discount_rate=60, discount_amount=0))
    FakeCollector.products = [_event(23)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 2, "published": 1} and "60% 세일" in published[0]["text"]

    # 요약기 없이(확인 모드) 도 같은 기준: 58원 적립 글은 확인 요청조차 안 보낸다
    bot.summarizer.summarize = InfoSummarizer(None).summarize  # type: ignore[method-assign]
    _wire(bot, body=PostBody(text="클릭적립 합계 58원\n라이브 예고 적립 3원", images=[], links=[]), published=published)
    FakeCollector.products = [_event(24)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 3, "published": 1}
    assert not [s for s in admin_calls["send"] if s.startswith("📝")]

    # 기준을 끄면(0) 다 통과
    bot.settings.info_posts.min_discount_rate = 0
    bot.settings.info_posts.min_discount_amount = 0
    FakeCollector.products = [_event(25)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 3, "published": 1, "awaiting_approval": 1}


async def test_priced_deal_with_only_a_board_url_is_not_an_info_post(bot: DealBot) -> None:
    """가격은 있는데 쇼핑몰 링크를 못 찾은 글은 정보 글로 올리지 않는다 (링크 없는 핫딜은 쓸모가 없다)."""
    FakeCollector.products = [_event(31, price=12900)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {}


async def test_unanswered_info_post_is_not_asked_again(bot: DealBot, admin_calls: dict[str, list]) -> None:
    """확인 요청에 답이 없어 버려진 글은 다음 수집 때 같은 질문을 다시 보내지 않는다 (3일 뒤엔 다시 물을 수 있음)."""
    published: list[dict] = []
    _wire(bot, body=PostBody(text="원문 최대 50% 할인", images=[], links=[]), published=published)
    FakeCollector.products = [_event(31)]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    asked = len([s for s in admin_calls["send"] if s.startswith("📝")])
    assert asked == 1
    now = datetime.now(UTC)
    assert bot.db.expire_queue(now - timedelta(days=1), now, awaiting_older_than=now + timedelta(hours=1)) == 1

    FakeCollector.products = [_event(31)]
    await bot.run_collector(bot.collectors[0])
    assert not await bot.process_queue_once()
    assert bot.db.queue_counts() == {"expired": 1}
    assert len([s for s in admin_calls["send"] if s.startswith("📝")]) == asked

    later = now + app_mod.REASK_COOLDOWN + timedelta(minutes=1)
    assert not bot._declined_recently(bot.db.get_queue_item(1).deal.product, later)  # type: ignore[union-attr]


def test_passed_deal_is_asked_again_only_when_cheaper(bot: DealBot) -> None:
    now = datetime.now(UTC)
    p = _event(41, price=10000)
    assert bot.db.enqueue(Deal(product=p, verdict=DealVerdict(is_deal=True, reasons=["r"], score=5), detected_at=now), score=5, now=now)
    bot.db.update_queue_item(1, status="skipped", error="skipped by admin")
    assert bot._declined_recently(p, now)
    assert not bot._declined_recently(_event(41, price=9000), now)  # 더 싸졌으면 새 딜
