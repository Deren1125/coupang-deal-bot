"""품절/종료 처리: 게시판 표시, 상품 페이지 재고, 링크 요청 메시지 수정, 채널 글 품절 표시."""

from __future__ import annotations

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.enrich import parse_page_meta
from dealbot.models import Product, PublishResult
from dealbot.soldout import looks_sold_out


@register("fake_soldout")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


def _toss(title: str, pid: str = "toss:ABC") -> Product:
    return Product(
        source="fake", product_id=pid, shop="toss", name="토스 핫도그", price=14890, url="https://toss.im/_m/ABC",
        recommend_count=9, extra={"title": title, "post_url": "https://p/1"},
    )


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_soldout", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False
    FakeCollector.products = []
    b = DealBot(settings)
    sent: list[str] = []
    edits: list[tuple[int, str]] = []

    async def send_with_id(text: str, *, silent: bool = False) -> int:
        sent.append(text)
        return 70 + len(sent)

    async def edit(message_id: int, text: str) -> bool:
        edits.append((message_id, text))
        return True

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.edit = edit  # type: ignore[method-assign]
    b.sent, b.edits = sent, edits  # type: ignore[attr-defined]

    async def not_sold_out(url: str) -> bool | None:
        return None

    b.enricher.check_available = not_sold_out  # type: ignore[method-assign]
    yield b
    b.db.close()


def test_looks_sold_out() -> None:
    assert looks_sold_out("[토스쇼핑] 핫도그 (품절)")
    assert looks_sold_out("[종료] 애슐리 핫도그 14,890원")
    assert looks_sold_out("매진 - 삼겹살 500g")
    assert looks_sold_out("판매 종료된 딜")
    assert not looks_sold_out("[토스쇼핑] 핫도그 14,890원")
    assert not looks_sold_out("종료 임박! 오늘까지 9,900원")
    assert not looks_sold_out("마감 예정 D-2 특가")
    assert not looks_sold_out("9/10 종료일 전까지 할인")
    assert not looks_sold_out(None)
    assert looks_sold_out("완전 SOLD OUT", words=["sold out"]) and not looks_sold_out("완전 SOLD OUT", words=["없는말"])


def test_page_availability_parsing() -> None:
    ld = '<script type="application/ld+json">{"@type":"Product","name":"x","offers":{"@type":"Offer","price":"9900","availability":"https://schema.org/OutOfStock"}}</script>'
    assert parse_page_meta(f"<html><head>{ld}</head></html>").available is False
    ld2 = ld.replace("OutOfStock", "InStock")
    assert parse_page_meta(f"<html><head>{ld2}</head></html>").available is True
    og = '<meta property="product:availability" content="oos">'
    assert parse_page_meta(f"<html><head>{og}</head></html>").available is False
    assert parse_page_meta("<html><head><title>x</title></head></html>").available is None


async def test_board_marker_cancels_awaiting_and_edits_notice(bot: DealBot) -> None:
    FakeCollector.products = [_toss("[토스쇼핑] 토스 핫도그 14,890원")]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}
    item = bot.db.awaiting_items()[0]
    assert bot.db.kv_get(f"link_notice:{item.id}") == "71"  # 링크 요청 메시지 id 저장

    # 게시판을 다시 읽었더니 제목에 품절 표시
    FakeCollector.products = [_toss("[토스쇼핑] 토스 핫도그 14,890원 (품절)")]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"expired": 1}
    assert bot.edits and bot.edits[0][0] == 71 and "품절되어 취소했습니다" in bot.edits[0][1]  # type: ignore[attr-defined]
    assert bot.db.get_queue_item(item.id).last_error.startswith("sold out")  # type: ignore[union-attr]
    # 다시 같은 품절 글을 봐도 조용함
    n_edits = len(bot.edits)  # type: ignore[attr-defined]
    await bot.run_collector(bot.collectors[0])
    assert len(bot.edits) == n_edits  # type: ignore[attr-defined]


async def test_page_check_before_link_request_skips_quietly(bot: DealBot) -> None:
    async def sold_out(url: str) -> bool | None:
        return False

    bot.enricher.check_available = sold_out  # type: ignore[method-assign]
    FakeCollector.products = [_toss("[토스쇼핑] 토스 핫도그 14,890원")]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"skipped": 1} and bot.sent == []  # type: ignore[attr-defined]


async def test_recheck_awaiting_drops_sold_out_page(bot: DealBot) -> None:
    FakeCollector.products = [_toss("[토스쇼핑] 토스 핫도그 14,890원")]
    await bot.run_collector(bot.collectors[0])
    await bot.process_queue_once()
    assert bot.db.queue_counts() == {"awaiting_link": 1}
    assert await bot.recheck_awaiting() == 0  # 아직 재고 있음(모름)

    async def sold_out(url: str) -> bool | None:
        return False

    bot.enricher.check_available = sold_out  # type: ignore[method-assign]
    assert await bot.recheck_awaiting() == 1
    assert bot.db.queue_counts() == {"expired": 1} and "상품 페이지" in bot.edits[0][1]  # type: ignore[attr-defined]


async def test_published_post_gets_sold_out_banner(bot: DealBot) -> None:
    marked: list[tuple[str | int, int, str]] = []

    async def fake_publish(deal):  # type: ignore[no-untyped-def]
        return PublishResult(ok=True, message_id=5)

    async def fake_mark(channel_id, message_id, text):  # type: ignore[no-untyped-def]
        marked.append((channel_id, message_id, text))
        return True

    bot.publisher.publish = fake_publish  # type: ignore[method-assign]
    bot.publisher.mark_sold_out = fake_mark  # type: ignore[method-assign]
    bot.publisher.channel_id = "@ch"
    p = _toss("[토스쇼핑] 토스 핫도그 14,890원")
    p.affiliate_url = "https://toss.im/_m/MINE"
    FakeCollector.products = [p]
    await bot.run_collector(bot.collectors[0])
    assert await bot.process_queue_once() and bot.db.queue_counts() == {"published": 1}

    FakeCollector.products = [_toss("[종료] 토스 핫도그 14,890원")]
    await bot.run_collector(bot.collectors[0])
    assert len(marked) == 1 and marked[0][:2] == ("@ch", 5) and "토스 핫도그" in marked[0][2]
    assert bot.db.kv_get("soldout_marked:toss:ABC")
    await bot.run_collector(bot.collectors[0])
    assert len(marked) == 1  # 한 번만 표시
