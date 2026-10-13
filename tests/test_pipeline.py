"""파이프라인 통합 테스트 (외부 네트워크/텔레그램 없음, dry-run)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, CollectorContext, register
from dealbot.config import CollectorConfig, Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult
from dealbot.utils.timeutil import utcnow


@register("fake")
class FakeCollector(BaseCollector):
    products: list[Product] = []
    fail = False

    async def collect(self) -> list[Product]:
        if FakeCollector.fail:
            raise RuntimeError("boom")
        return list(FakeCollector.products)


def _p(pid: str, price: int, **kw) -> Product:  # type: ignore[no-untyped-def]
    kw.setdefault("rank", 1)
    return Product(
        source="fake", product_id=f"coupang:{pid}", shop="coupang", name=f"상품 {pid}", price=price,
        url=f"https://www.coupang.com/vp/products/{pid}",
        affiliate_url=f"https://link.coupang.com/re/AFFSDP?lptag=AF1&pageKey={pid}", **kw,
    )


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 2
    FakeCollector.products = []
    FakeCollector.fail = False
    b = DealBot(settings)
    yield b
    b.db.close()


async def test_collect_evaluate_queue_publish(bot: DealBot) -> None:
    FakeCollector.products = [
        _p("1", 7000, discount_rate=60),
        _p("2", 9000, discount_rate=10),
        _p("3", 500, discount_rate=90),
    ]
    results = await bot.run_once()
    assert results[0]["collected"] == 3 and results[0]["deals"] == 1 and results[0]["queued"] == 1
    assert bot.db.price_history_count() == 3
    await bot.run_once()
    assert bot.db.price_history_count() == 3  # 6시간 안에는 같은 상품 가격을 다시 기록하지 않음
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.posted_within("coupang:1", 7)
    results = await bot.run_once()
    assert results[0]["deals"] == 1 and results[0]["queued"] == 0
    assert bot.db.count_posts_since(utcnow() - timedelta(hours=1)) == 1


async def test_rule_b_uses_history(bot: DealBot) -> None:
    now = utcnow()
    for i in range(3):
        bot.db.record_observation(_p("10", 10000), now - timedelta(days=i + 1))
    FakeCollector.products = [_p("10", 8000)]
    results = await bot.run_once()
    assert results[0]["deals"] == 1
    assert bot.db.recent_posts()[0]["product_id"] == "coupang:10"


async def test_rate_limit_leaves_items_pending(bot: DealBot) -> None:
    FakeCollector.products = [_p(str(i), 5000, discount_rate=50) for i in range(4)]
    await bot.run_once()
    counts = bot.db.queue_counts()
    assert counts["published"] == 2 and counts["pending"] == 2
    assert "쇼핑몰별 링크 처리" in bot.reporter.status_text()


async def test_collector_error_is_recorded(bot: DealBot) -> None:
    FakeCollector.fail = True
    results = await bot.run_once()
    assert results[0]["status"] == "error" and "boom" in results[0]["error"]
    assert bot.state.last_error and "boom" in bot.state.last_error
    assert bot.db.last_error()["kind"] == "collector:fake"  # type: ignore[index]


async def test_pause_and_run_request(bot: DealBot) -> None:
    bot.pause()
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}
    assert not await bot.process_queue_once()
    bot.resume()
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert "fake" in bot.request_run(None) and "그런 게시판은 없어요" in bot.request_run("nope")


async def test_failure_attempts_and_expiry(bot: DealBot, settings: Settings) -> None:
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])

    async def failing_publish(deal, **_kw):  # type: ignore[no-untyped-def]
        return PublishResult(ok=False, error="telegram down")

    bot.publisher.publish = failing_publish  # type: ignore[method-assign]
    for _ in range(settings.publish.max_publish_attempts):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"failed": 1}

    settings.publish.queue_ttl_hours = 0
    stale = Deal(product=_p("2", 1000, discount_rate=50), verdict=DealVerdict(is_deal=True, score=1))
    assert bot.db.enqueue(stale, score=1, now=utcnow() - timedelta(minutes=1))
    await bot.process_queue_once()
    assert bot.db.queue_counts().get("expired") == 1


async def test_timeout_after_send_is_not_published_twice(bot: DealBot) -> None:
    """텔레그램이 글을 받은 뒤 답만 늦은(시간 초과) 경우: 이미 올라갔을 수 있어 20초 뒤에 다시 보내지 않고,
    같은 딜이 다시 수집돼도 줄 세우지 않는다. 주인이 채널을 보고 없으면 /link 로 다시 올린다."""
    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])
    sent: list[str] = []

    async def timed_out(deal, **_kw):  # type: ignore[no-untyped-def]
        sent.append(deal.product.product_id)
        return PublishResult(ok=False, error="TimedOut: Timed out", maybe_posted=True)

    notes: list[tuple[int, str]] = []

    async def unsure(queue_id: int, deal: Deal, error: str) -> None:
        notes.append((queue_id, error))

    bot.publisher.publish = timed_out  # type: ignore[method-assign]
    bot.notifier.notify_publish_unsure = unsure  # type: ignore[method-assign]
    assert await bot.process_queue_once()
    item = bot.db.items_for_product("coupang:1", ("failed",))[0]
    assert item.last_error == "maybe posted: TimedOut: Timed out" and item.attempts == 1
    assert notes == [(item.id, item.last_error)]
    assert not await bot.process_queue_once() and sent == ["coupang:1"]  # 자동으로 다시 보내지 않음
    await bot.run_collector(bot.collectors[0])  # 게시판이 같은 딜을 또 돌려줘도
    assert bot.db.queue_counts() == {"failed": 1}

    async def ok_publish(deal, **_kw):  # type: ignore[no-untyped-def]
        sent.append(deal.product.product_id)
        return PublishResult(ok=True, message_id=9)

    bot.publisher.publish = ok_publish  # type: ignore[method-assign]
    await bot.attach_link(item.id, "https://link.coupang.com/a/again")  # 채널에 없어서 주인이 다시 올림
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1} and sent == ["coupang:1", "coupang:1"]


async def test_posted_records_an_unsure_post_and_runs_what_was_skipped(bot: DealBot) -> None:
    """올라갔는지 모르는 글이 채널에 있으면 주인이 /posted: 다시 보내지 않고 올라간 글로 기록한 뒤, 시간 초과 때 건너뛴 뒷일
    (블로그 내보내기·스레드·인스타)을 이어서 한다. 채널 글 링크를 주면 그 번호로 품절 표시를 할 수 있다."""
    import json

    from dealbot.storage.db import MAYBE_POSTED

    FakeCollector.products = [_p("1", 5000, discount_rate=50)]
    await bot.run_collector(bot.collectors[0])
    sent: list[str] = []

    async def timed_out(deal, **_kw):  # type: ignore[no-untyped-def]
        sent.append(deal.product.product_id)
        return PublishResult(ok=False, error="TimedOut: Timed out", maybe_posted=True)

    side: list[tuple[str, int | None]] = []

    async def side_channels(deal: Deal, *, photo: bytes | None = None, deal_no: int | None = None) -> None:
        side.append((deal.product.product_id, deal_no))

    bot.publisher.publish = timed_out  # type: ignore[method-assign]
    bot._publish_side_channels = side_channels  # type: ignore[method-assign]
    assert await bot.process_queue_once()
    item = bot.db.items_for_product("coupang:1", ("failed",))[0]
    assert item.last_error.startswith(MAYBE_POSTED) and side == [] and not bot.db.posted_within("coupang:1", 7)
    export = bot.settings.data_dir / "published_deals.jsonl"
    assert not export.exists()

    assert "없습니다" in await bot.mark_posted(item.id + 1)  # 없는 번호
    # 우리 채널의 글 링크 (다른 채널 글 번호는 품절 표시에 쓰지 않음 — 아래 test_posted_link_from_another_channel_…)
    reply = await bot.mark_posted(item.id, f"{bot.notifier.channel_post_base}/321")
    assert reply.startswith(f"✅ #{item.id} 번을 채널에 올라간 글로 기록했어요")
    assert bot.db.get_queue_item(item.id).status == "published" and sent == ["coupang:1"]  # 다시 보내지 않음
    assert bot.db.latest_post("coupang:1")["message_id"] == 321
    assert [json.loads(x)["product_id"] for x in export.read_text(encoding="utf-8").splitlines()] == ["coupang:1"]
    assert side == [("coupang:1", item.id)]
    assert "아닙니다" in await bot.mark_posted(item.id)  # 두 번 기록하지 않음
    await bot.run_collector(bot.collectors[0])  # 게시판이 같은 딜을 또 돌려줘도
    assert bot.db.queue_counts() == {"published": 1}


async def _unsure_items(bot: DealBot, *pids: str) -> tuple[list, list[str], list[int | None]]:  # type: ignore[type-arg]
    """보낸 뒤 텔레그램 답만 시간 초과 → '채널에 올라갔는지 모름'(failed · maybe posted) 글들. (글들, 채널에 보낸 상품, 뒷일 번호)."""
    FakeCollector.products = [_p(pid, 5000, discount_rate=50) for pid in pids]
    await bot.run_collector(bot.collectors[0])
    sent: list[str] = []
    side: list[int | None] = []

    async def timed_out(deal, **_kw):  # type: ignore[no-untyped-def]
        sent.append(deal.product.product_id)
        return PublishResult(ok=False, error="TimedOut: Timed out", maybe_posted=True)

    async def side_channels(deal: Deal, *, photo: bytes | None = None, deal_no: int | None = None) -> None:
        side.append(deal_no)

    bot.publisher.publish = timed_out  # type: ignore[method-assign]
    bot._publish_side_channels = side_channels  # type: ignore[method-assign]
    bot.settings.publish.max_per_hour = 10
    while await bot.process_queue_once():
        pass
    items = [bot.db.items_for_product(f"coupang:{pid}", ("failed",))[0] for pid in pids]
    return items, sent, side


async def test_channel_post_link_on_an_unsure_post_records_it_instead_of_reposting(bot: DealBot) -> None:
    """리뷰 지적 (7f8ef13): '채널에 올라갔는지 모릅니다' 글에 채널 글 링크(t.me)를 붙이면(/link·답장·일괄 줄 모두 attach_link)
    그 주소가 제휴 링크가 되어 failed → pending, 다음 차례에 같은 딜이 t.me 링크로 또 올라가고 그 상품의 '내 링크'로도 남았다.
    채널 글 링크 = 채널에 있다는 뜻 → /posted 처럼 다시 보내지 않고 올라간 글로 기록한다 (그 글 번호로 품절 표시)."""
    (item,), sent, side = await _unsure_items(bot, "1")
    post_link = f"{bot.notifier.channel_post_base}/321"
    reply = await bot.attach_link(item.id, post_link)
    assert reply.startswith(f"✅ #{item.id} 번을 채널에 올라간 글로 기록했어요"), reply
    after = bot.db.get_queue_item(item.id)
    assert after.status == "published" and after.deal.affiliate_url == item.deal.affiliate_url  # t.me 가 링크로 붙지 않음
    assert bot.db.kv_get("mylink:coupang:1") is None  # 다음 딜에 다시 쓰일 '내 링크'로도 남지 않음
    assert bot.db.latest_post("coupang:1")["message_id"] == 321 and side == [item.id]
    assert not await bot.process_queue_once() and sent == ["coupang:1"]  # 다시 올리지 않음


async def test_telegram_address_is_never_attached_as_a_deal_link(bot: DealBot, settings: Settings) -> None:
    """t.me·telegram.me·telegram.org 주소는 어느 글에도 제휴·상품 링크로 붙지 않는다 (주인에게 까닭을 알림).
    '채널에 올라갔는지 모름' 글에 채널 글이 아닌 텔레그램 주소(채널·초대 링크)를 주면 /posted·/link 를 안내한다.
    예전에 잘못 남은 t.me '내 링크'는 같은 상품이 다시 나와도 쓰지 않고 링크를 다시 묻는다. 진짜 상품 링크는 그대로 붙는다."""
    settings.publish.dry_run = False  # 연습 모드에서는 링크 요청을 생략하므로 실제 모드로
    toss = Product(source="fake", product_id="toss:ABC", shop="toss", name="토스 핫도그", price=14890,
                   url="https://toss.im/_m/ABC", recommend_count=9)
    FakeCollector.products = [toss]
    bot.db.kv_set("mylink:toss:ABC", "https://t.me/hot_deal_and_info/321")  # 고치기 전 버전이 남긴 잘못된 '내 링크'
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}  # t.me 를 재사용해 올리지 않고 링크를 다시 물음
    item = bot.db.awaiting_items()[0]
    for url in ("https://t.me/hot_deal_and_info/321", "https://telegram.me/hot_deal_and_info", "https://t.me/+AbCdEf123",
                "https://web.telegram.org/k/#@hot_deal_and_info", "http://T.ME/hot_deal_and_info/9?single"):
        msg = await bot.attach_link(item.id, url)
        assert "텔레그램 주소" in msg and "제휴" in msg, (url, msg)
        assert bot.attach_auto_link(item.id, url) is not None
    now = bot.db.get_queue_item(item.id)
    assert now.status == "awaiting_link" and now.deal.affiliate_url is None
    assert bot.db.kv_get("mylink:toss:ABC") == "https://t.me/hot_deal_and_info/321"  # 그대로 (덮어쓰지도 않음)

    msg = await bot.attach_link(item.id, "https://toss.im/_m/MYLINK")  # 진짜 상품 링크는 그대로 붙는다
    assert "상품 링크 완료" in msg and bot.db.queue_counts() == {"pending": 1}
    assert bot.db.kv_get("mylink:toss:ABC") == "https://toss.im/_m/MYLINK"

    settings.publish.dry_run = True
    (unsure,), sent, _side = await _unsure_items(bot, "7")
    for url in ("https://t.me/hot_deal_and_info", "https://t.me/+AbCdEf123"):  # 채널 글이 아닌 텔레그램 주소
        msg = await bot.attach_link(unsure.id, url)
        assert "상품 링크가 아니라" in msg and f"/posted {unsure.id}" in msg and f"/link {unsure.id}" in msg, msg
    assert bot.db.get_queue_item(unsure.id).status == "failed" and bot.db.kv_get("mylink:coupang:7") is None
    msg = await bot.attach_link(unsure.id, "https://www.coupang.com/vp/products/7")  # 채널에 없음 → 상품 링크로 다시 올림
    assert "상품 링크 완료" in msg and bot.db.get_queue_item(unsure.id).status == "pending"


async def test_posted_link_from_another_channel_records_no_post_number(bot: DealBot) -> None:
    """품절 표시는 기록한 글 번호의 '우리 채널' 글을 고쳐 쓴다 → 다른 채널 글 링크(또는 채널 글이 아닌 링크)의 번호를 쓰면
    우리 채널의 엉뚱한 글을 덮어쓴다. 그런 링크면 올라간 글로는 기록하되 번호는 남기지 않고 주인에게 알린다."""
    (a, b, c, d), sent, side = await _unsure_items(bot, "1", "2", "3", "4")
    reply = await bot.mark_posted(a.id, "https://t.me/some_other_channel/321")
    assert "우리 채널 글 링크가 아니라" in reply and bot.db.latest_post("coupang:1")["message_id"] is None
    reply = await bot.mark_posted(b.id, "https://www.coupang.com/vp/products/2")
    assert "채널 글 링크가 아니라" in reply and bot.db.latest_post("coupang:2")["message_id"] is None
    bot.publisher.channel_id = -1001234567890  # 비공개 채널: t.me/c/<-100 뒤 숫자>/글번호
    assert "아니라" in await bot.mark_posted(c.id, "https://t.me/c/999/55")
    assert bot.db.latest_post("coupang:3")["message_id"] is None
    assert "아니라" not in await bot.mark_posted(d.id, "https://t.me/c/1234567890/56")
    assert bot.db.latest_post("coupang:4")["message_id"] == 56
    assert bot.db.queue_counts() == {"published": 4} and sent == ["coupang:1", "coupang:2", "coupang:3", "coupang:4"]
    assert sorted(side) == sorted([a.id, b.id, c.id, d.id])


async def test_manual_link_flow_for_toss(bot: DealBot) -> None:
    bot.settings.publish.dry_run = False  # 연습 모드에서는 링크 요청을 생략하므로 실제 모드로
    toss = Product(source="fake", product_id="toss:ABC", shop="toss", name="토스 핫도그", price=14890,
                   url="https://toss.im/_m/ABC", recommend_count=9)
    FakeCollector.products = [toss]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 1}

    # 자동 변환 불가 → 관리자 링크 대기
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}
    assert not await bot.process_queue_once()  # 대기 중엔 발행 없음
    item = bot.db.awaiting_items()[0]
    assert "링크" in bot.reporter.pending_text() and f"#{item.id}" in bot.reporter.pending_text()

    # 같은 상품 재수집 → 중복 등록 안 됨
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"awaiting_link": 1}

    assert "http" in await bot.attach_link(item.id, "not a url")
    msg = await bot.attach_link(item.id, "https://toss.im/_m/MYLINK")
    assert "상품 링크 완료" in msg and "토스 핫도그" in msg and bot.db.queue_counts() == {"pending": 1}  # 10/10: 무슨 상품인지
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/MYLINK"


async def test_skip_and_manual_link_expiry(bot: DealBot, settings: Settings) -> None:
    settings.publish.dry_run = False
    toss = Product(source="fake", product_id="toss:X", shop="toss", name="x", price=1000, url="https://toss.im/_m/X", recommend_count=9)
    FakeCollector.products = [toss]
    await bot.run_collector(bot.collectors[0])
    await bot.process_queue_once()
    item = bot.db.awaiting_items()[0]
    assert "건너" in bot.skip_item(item.id) and bot.db.queue_counts() == {"skipped": 1}
    assert "없습니다" in bot.skip_item(9999)

    settings.publish.manual_link_ttl_hours = 0
    stale = Deal(product=Product(source="f", product_id="toss:Y", shop="toss", name="y", price=1000, url="https://toss.im/_m/Y"), verdict=DealVerdict(is_deal=True))
    bot.db.enqueue(stale, score=1, now=utcnow() - timedelta(minutes=1))
    await bot.process_queue_once()  # → awaiting_link
    assert bot.db.queue_counts().get("awaiting_link") == 1
    await bot.process_queue_once()  # TTL 0 → 만료
    assert bot.db.queue_counts().get("expired") == 1


async def test_submit_manual_post(bot: DealBot) -> None:
    text = "/post\n[토스쇼핑 첫 구매 시 3,000원 추가 할인]\n상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트\n가격: 14,890원\nhttps://toss.im/_m/P4Qr1ope"
    msg = await bot.submit_manual(text)
    assert "맨 앞에 넣었습니다" in msg and bot.db.queue_counts() == {"pending": 1}
    assert await bot.process_queue_once()
    post = bot.db.recent_posts()[0]
    assert post["product_id"] == "toss:P4Qr1ope" and post["affiliate_url"] == "https://toss.im/_m/P4Qr1ope" and post["price"] == 14890
    rendered = bot.publisher.render(Deal.from_dict(bot.db.get_queue_item(1).deal.to_dict()))  # type: ignore[union-attr]
    assert rendered.endswith("<i>이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</i>")
    assert "\n[토스쇼핑 첫 구매 시 3,000원 추가 할인]\n" in rendered
    assert "<b>14,890원</b>" in rendered and "애슐리 크리스피 핫도그" in rendered


async def test_dry_run_without_coupang_uses_raw_url(bot: DealBot) -> None:
    p = Product(source="fake", product_id="coupang:77", shop="coupang", name="뽐뿌 상품", price=5000, url="https://www.coupang.com/vp/products/77", discount_rate=50, recommend_count=2)
    FakeCollector.products = [p]
    await bot.run_once()
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://www.coupang.com/vp/products/77"


async def test_disabled_shop_products_are_ignored(settings: Settings) -> None:
    from dealbot.config import ShopConfig

    settings.collectors = [CollectorConfig(name="fake", type="fake", interval_minutes=1)]
    settings.shops = [ShopConfig(key="temu", enabled=False)]
    b = DealBot(settings)
    try:
        FakeCollector.products = [Product(source="fake", product_id="temu:1", shop="temu", name="t", price=1000, url="https://temu.com/1", recommend_count=99)]
        r = await b.run_once()
        assert r[0]["collected"] == 1 and r[0]["deals"] == 0
    finally:
        b.db.close()


def test_context_type() -> None:
    assert CollectorContext.__dataclass_fields__.keys() >= {"settings", "http", "db", "coupang", "shops"}


async def test_dry_run_skips_manual_link_requests(bot: DealBot) -> None:
    assert bot.settings.publish.dry_run
    FakeCollector.products = [Product(source="fake", product_id="toss:D", shop="toss", name="연습", price=5000, url="https://toss.im/_m/D", recommend_count=9)]
    await bot.run_once()
    assert bot.db.queue_counts() == {"published": 1}
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/D"


async def test_dry_run_previews_do_not_block_real_publishing(bot: DealBot) -> None:
    """연습 모드에서 미리보기로 본 딜이, 실제 모드로 바꾼 뒤 발행에서 빠지면 안 된다."""
    assert bot.state.dry_run
    FakeCollector.products = [_p("77", 7000, discount_rate=60)]
    await bot.run_once()
    assert bot.db.queue_counts() == {"published": 1}  # 연습 발행(미리보기)
    assert bot.db.latest_post("coupang:77")["dry_run"] == 1

    # 연습 모드에서는 같은 딜을 다시 잡지 않는다 (미리보기 도배 방지)
    results = await bot.run_once()
    assert results[0]["deals"] == 1 and results[0]["queued"] == 0

    # 실제 모드로 전환하면 연습 기록은 무시하고 진짜로 올린다
    # (테스트 환경엔 텔레그램 봇이 없어 publisher 가 항상 연습으로 기록하므로 실제 발행만 흉내낸다)
    async def real_publish(deal, **_kw):  # type: ignore[no-untyped-def]
        return PublishResult(ok=True, message_id=1, dry_run=False)

    bot.publisher.publish = real_publish  # type: ignore[method-assign]
    bot.state.dry_run = False
    bot.settings.publish.dry_run = False
    results = await bot.run_once()
    assert results[0]["queued"] == 1, "연습 때 본 딜이라고 진짜 발행에서 빠지면 안 됨"
    assert bot.db.count_posts_since(utcnow() - timedelta(hours=1)) == 2

    # 실제로 올린 뒤에는 다시 중복으로 걸러진다
    results = await bot.run_once()
    assert results[0]["queued"] == 0
