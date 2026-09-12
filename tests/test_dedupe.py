"""같은 상품이 다른 글·다른 게시판으로 다시 올라오는 것을 막는다."""

from __future__ import annotations

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.dedupe import find_duplicate, normalize_name, similarity
from dealbot.models import Product


def test_similarity_ignores_tags_prices_and_shipping_words() -> None:
    assert normalize_name("[쿠팡] 스탠리 텀블러 1.18L (29,900원/무료)") == "스탠리 텀블러 1.18l ( / )"
    assert similarity("[쿠팡] 스탠리 텀블러 1.18L 29,900원 무료배송", "스탠리 텀블러 1.18L") >= 0.8
    assert similarity("스탠리 텀블러 1.18L", "스탠리 퀜처 텀블러 1.18L") >= 0.8
    # 숫자(모델·용량·수량)가 다르면 다른 상품
    assert similarity("갤럭시 S26 자급제", "갤럭시 S25 자급제") == 0.0
    assert similarity("올챌린지 화장지 30롤", "올챌린지 화장지 24롤") == 0.0
    assert similarity("롯데온 상품 0", "롯데온 상품 1") == 0.0
    assert similarity("다이슨 에어랩", "다이슨 슈퍼소닉 드라이어") < 0.8


def test_find_duplicate_allows_clearly_cheaper_deal() -> None:
    recent = [{"product_id": "coupang:1", "name": "스탠리 텀블러 1.18L", "price": 29900, "posted_at": "2026-09-11T10:00:00+00:00"}]
    dup = find_duplicate("[네이버] 스탠리 텀블러 1.18L", 29900, recent)
    assert dup and dup["product_id"] == "coupang:1" and dup["similarity"] >= 0.8
    assert find_duplicate("스탠리 텀블러 1.18L", 25000, recent) is None  # 16% 싸짐 → 새 딜
    assert find_duplicate("스탠리 텀블러 1.18L", 25000, recent, allow_cheaper=False)  # 대기 중인 글과 겹치면 가격과 무관하게 중복
    assert find_duplicate("다이슨 에어랩", 25000, recent) is None


@register("fake_dedupe")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_dedupe", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 20
    FakeCollector.products = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _deal(pid: str, shop: str, name: str, price: int, url: str) -> Product:
    return Product(source="fake", product_id=pid, shop=shop, name=name, price=price, url=url, recommend_count=9, discount_rate=50)


async def test_same_product_from_another_board_is_not_queued_twice(bot: DealBot) -> None:
    FakeCollector.products = [
        _deal("coupang:1", "coupang", "[쿠팡] 스탠리 텀블러 1.18L", 29900, "https://www.coupang.com/vp/products/1"),
        _deal("coupang:2", "coupang", "스탠리 텀블러 1.18L 무료배송", 29900, "https://www.coupang.com/vp/products/2"),  # 다른 주소, 같은 상품
        _deal("coupang:3", "coupang", "스탠리 텀블러 0.7L", 24900, "https://www.coupang.com/vp/products/3"),  # 용량이 다르면 다른 상품
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 2}
    names = sorted(c["name"] for c in bot.db.open_queue_names())
    assert names == ["[쿠팡] 스탠리 텀블러 1.18L", "스탠리 텀블러 0.7L"]
    events = [e for e in bot.db.recent_events(10) if e["kind"] == "dedupe"]
    assert events and "대기 중인 #1" in events[0]["message"]

    # 올린 뒤에는 같은 이름이 다시 들어오지 않지만, 확실히 싸진 딜은 들어온다
    for _ in range(2):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 2}
    FakeCollector.products = [
        _deal("coupang:4", "coupang", "스탠리 텀블러 1.18L", 29900, "https://www.coupang.com/vp/products/4"),
        _deal("coupang:5", "coupang", "스탠리 텀블러 1.18L", 24900, "https://www.coupang.com/vp/products/5"),
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"published": 2, "pending": 1}
    assert bot.db.open_queue_names()[0]["product_id"] == "coupang:5"
