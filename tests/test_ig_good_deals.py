"""'할인 괜찮은 딜'은 플랫폼 무관하게 다 올림 (주인 10/18 — "너무 남발하지만 않으면 돼").

- 고르는 기준: 강조 단계 top·must, 또는 할인율(평소 가격 근거 대비 %·표시 할인율 중 큰 값) ≥ publish.good_deal_min_discount_pct(30)
- 인스타: 괜찮은 딜만 딜 하나짜리 글 (하루 10개·30~50분·묶음 글 앞 30분 — test_ig_schedule.py), 보통 딜은 릴스·TOP5 에만
  (대기열엔 'bundle' 로 사진 사본만 — 12시간 지나 '버림'으로 남지 않음)
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from dealbot.config import InstagramConfig
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.good_deal import good_deal_reason
from test_ig_schedule import (  # noqa: F401 — bot·clock: 픽스처
    _jpeg,
    _plan,
    at,
    bot,
    clock,
    published,
    singles,
    tick,
    to_instagram,
)


def _d(*, price: int = 10000, original: int | None = None, shown: float | None = None, market: int | None = None,
       market_pct: float | None = None, kind: str = "hotdeal") -> Deal:
    return Deal(Product(source="s", product_id="coupang:9", name="상품", price=price, url="u", original_price=original, deal_kind=kind),
                DealVerdict(is_deal=True, discount_rate=shown, market_price=market, below_market_pct=market_pct))


def test_good_deal_is_a_strong_tier_or_a_discount_over_the_threshold(bot) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    r = bot.renderer
    assert good_deal_reason(_d(market=40000, market_pct=75.0), r, 30) == "tier top 75%"  # 70% 이상 = top
    assert good_deal_reason(_d(market=22000, market_pct=55.0), r, 99) == "tier must 55%"  # must 는 기준 % 와 상관없이
    assert good_deal_reason(_d(market=14500, market_pct=31.0), r, 30) == "discount 31%"  # 평소 가격 근거 대비
    assert good_deal_reason(_d(shown=30.0), r, 30) == "discount 30%"  # 표시 할인율 (판정)
    assert good_deal_reason(_d(original=16000), r, 30) == "discount 38%"  # 원래 가격에서 계산한 할인율
    assert good_deal_reason(_d(shown=29.9), r, 30) is None
    assert good_deal_reason(_d(market=14500, market_pct=31.0), r, 40) is None  # 기준은 설정값
    assert good_deal_reason(_d(), r, 30) is None  # 할인 근거 없음
    assert good_deal_reason(_d(shown=60, kind="info"), r, 30) is None  # 정보 글은 아님
    assert good_deal_reason(_d(price=0, shown=60), r, 30) is None  # 가격 없는 글도
    assert good_deal_reason(_d(), r, 0) is not None  # 0 = 모든 상품 딜


def test_config_keeps_well_inside_the_instagram_publishing_limit() -> None:
    with pytest.raises(ValueError):
        InstagramConfig(daily_singles=60)  # 인스타 API 24시간 100개(예전 50개) — 단일 글 안전 상한 40


async def test_only_good_deals_become_single_posts_ordinary_ones_stay_for_bundles(bot, clock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    bot.settings.publish.good_deal_min_discount_pct = 30  # 운영 기본값
    _plan(bot, {})
    plain_q, plain = published(bot, "coupang:1", "보통 딜", 9900, when=at(8), score=95)  # 점수는 높지만 할인 근거 없음
    photo = _jpeg()
    await to_instagram(bot, clock, plain_q, plain, photo=photo)
    good_q, good = published(bot, "coupang:2", "괜찮은 딜", 9900, when=at(8, 1), score=10, market=14900, pct=33.6)
    await to_instagram(bot, clock, good_q, good)
    must_q, must = published(bot, "coupang:3", "반값 딜", 9900, when=at(8, 2), score=5, market=21900, pct=54.8)
    await to_instagram(bot, clock, must_q, must)
    assert [r["product_id"] for r in bot.db.ig_queued()] == ["coupang:2", "coupang:3"]  # 점수 순 (보통 딜은 줄에 없음)
    row = bot.db.ig_items_for(plain_q)[0]
    assert (row["status"], row["reason"]) == ("bundle", "not a good-discount deal (reel/TOP5 only)")
    assert bot.ig._photo_for(plain_q) == photo  # 릴스·TOP5 는 보통 딜의 사진 사본을 그대로 씀
    await to_instagram(bot, clock, plain_q, plain, photo=photo)
    assert len(bot.db.ig_items_for(plain_q)) == 1  # 같은 채널 글은 한 번만
    assert await tick(bot, clock, at(9)) == "single: coupang:2"
    assert await tick(bot, clock, at(9, 51)) == "single: coupang:3"
    assert await tick(bot, clock, at(11)) is None
    assert singles(bot) == ["coupang:2", "coupang:3"]
    await tick(bot, clock, at(21))  # 12시간 넘게 지나도 보통 딜은 '버림'으로 남지 않음 (하루 0개 알림 이유를 흐리지 않게)
    assert bot.db.ig_items_for(plain_q)[0]["status"] == "bundle"
    assert not [r for r in bot.db.sns_log_since("instagram", at(0)) if r["product_id"] == "coupang:1"]


async def test_cheaper_good_repost_of_an_ordinary_deal_gets_a_single(bot, clock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    bot.settings.publish.good_deal_min_discount_pct = 30
    _plan(bot, {})
    q1, d1 = published(bot, "coupang:1", "샴푸", 12900, when=at(8))
    await to_instagram(bot, clock, q1, d1)
    assert bot.db.ig_queued() == []
    q2, d2 = published(bot, "coupang:1x", "샴푸", 8900, when=at(9), market=13900, pct=36.0)
    d2.product.product_id = "coupang:1"
    await to_instagram(bot, clock, q2, d2)
    assert [(r["product_id"], r["queue_id"]) for r in bot.db.ig_queued()] == [("coupang:1", q2)]
    assert await tick(bot, clock, at(9) + timedelta(minutes=5)) == "single: coupang:1"


async def test_ordinary_deal_queued_before_the_rule_is_not_posted_as_a_single(bot, clock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    _plan(bot, {})
    qid, deal = published(bot, "coupang:1", "예전에 줄 선 보통 딜", 9900, when=at(8))
    await to_instagram(bot, clock, qid, deal)  # 기준 0 (모든 딜) 일 때 줄 섬
    assert len(bot.db.ig_queued()) == 1
    bot.settings.publish.good_deal_min_discount_pct = 30
    assert await tick(bot, clock, at(9)) is None
    assert bot.db.ig_items_for(qid)[0]["status"] == "bundle" and singles(bot) == []
