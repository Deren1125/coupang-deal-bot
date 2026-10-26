"""수집 때 빈 칸 채우기: 같은 상품 페이지는 remember_hours 동안 다시 읽지 않음 (10/26 운영 AI — 몰 페이지를 5분마다 다시 읽음)."""
from __future__ import annotations

import pytest

from dealbot.config import Settings
from dealbot.enrich import PageMeta
from dealbot.models import Product


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _product() -> Product:
    return Product(source="ppomppu", product_id="gmarket:7", shop="gmarket", name="스타벅스 JAR 65g", price=9900,
                   url="https://item.gmarket.co.kr/Item?goodscode=7")


async def test_same_product_page_is_read_once_and_reused(bot) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def fetch(url: str) -> PageMeta:
        calls.append(url)
        return PageMeta(final_url=url, rating=4.5)

    bot.enricher.fetch = fetch  # type: ignore[method-assign]
    first, again = _product(), _product()  # 판마다 새로 만든 Product (별점 빈 칸)
    await bot._enrich(first)
    await bot._enrich(again)
    assert len(calls) == 1 and first.rating == again.rating == 4.5  # 기억한 값으로 채움


async def test_failed_reads_are_remembered_and_zero_turns_memory_off(bot) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    async def fetch(url: str) -> PageMeta | None:
        calls.append(url)
        return None

    bot.enricher.fetch = fetch  # type: ignore[method-assign]
    await bot._enrich(_product())
    await bot._enrich(_product())
    assert len(calls) == 1  # 못 읽은 페이지도 매 판 다시 묻지 않음
    bot.settings.deal.enrich.remember_hours = 0
    await bot._enrich(_product())
    await bot._enrich(_product())
    assert len(calls) == 3
