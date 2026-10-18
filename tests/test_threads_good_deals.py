"""스레드: 할인 괜찮은 딜은 먼저, 가벼운 안전 한도로만 (주인 10/18) — 글 사이 15~30분 무작위, 딜 글 하루 20개까지
(스레드 API 한도 24시간 250개보다 한참 아래). 보통 딜·정보 글은 예전 그대로 (45분 간격, 그날 딜 글 10개 전까지)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dealbot.config import ThreadsRhythmConfig
from dealbot.models import Deal
from test_sns_growth import _at, _deal, bot  # noqa: F401 — bot: 픽스처

T7 = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)  # 07:00 KST


def _good(pid: str, score: float = 10.0) -> Deal:
    d = _deal(pid, score=score)
    d.verdict.discount_rate = 35.0  # 할인율 30% 이상 = 괜찮은 딜
    return d


def _queue(bot, deal: Deal, n: int) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    assert bot.threads_flow.enqueue_deal(deal, queue_id=n, has_photo=False) is not None


async def _post(bot, when: datetime) -> str | None:  # type: ignore[no-untyped-def]  # noqa: F811
    _at(bot, when)
    before = {j.product_id for j in bot.db.threads_waiting()}
    if not await bot.threads_flow.tick(when):
        return None
    after = {j.product_id for j in bot.db.threads_waiting()}
    (pid,) = before - after
    return pid


async def test_good_deals_go_first_with_a_15_to_30_minute_gap_and_ordinary_ones_keep_45(bot) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    _at(bot, T7 - timedelta(hours=1))
    _queue(bot, _deal("coupang:plain", score=90), 1)  # 점수는 가장 높지만 보통 딜
    _queue(bot, _good("coupang:g1", score=10), 2)
    _queue(bot, _good("coupang:g2", score=20), 3)
    assert [j.payload["good"] for j in bot.db.threads_waiting()] == [False, True, True]
    assert await _post(bot, T7) == "coupang:g2"  # 괜찮은 딜 먼저 (그 안에서 점수 순)
    assert await _post(bot, T7 + timedelta(minutes=14)) is None  # 15분 안
    assert await _post(bot, T7 + timedelta(minutes=30)) == "coupang:g1"  # 15~30분 무작위 간격의 끝 — 늘 지남
    assert await _post(bot, T7 + timedelta(minutes=70)) is None  # 보통 딜은 예전처럼 45분
    assert await _post(bot, T7 + timedelta(minutes=76)) == "coupang:plain"


def test_good_gap_is_random_inside_15_to_30_and_stable_per_post(bot) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    gaps = {bot.threads_flow.good_gap(T7 + timedelta(minutes=i)) for i in range(200)}
    assert min(gaps) >= timedelta(minutes=15) and max(gaps) <= timedelta(minutes=30) and len(gaps) > 5
    assert bot.threads_flow.good_gap(T7) == bot.threads_flow.good_gap(T7)


async def test_ordinary_deals_stop_at_10_good_deals_at_20(bot) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    rc = bot.settings.threads.rhythm
    rc.min_gap_minutes = rc.good_min_gap_minutes = rc.good_max_gap_minutes = 0
    _at(bot, T7)
    for i in range(11):
        _queue(bot, _deal(f"coupang:p{i}"), i + 1)
    for _ in range(15):
        await bot.threads_flow.tick(T7)
    assert len(bot.fake.posts()) == 10 and len(bot.db.threads_waiting()) == 1  # 보통 딜은 예전처럼 하루 10개
    for i in range(11):
        _queue(bot, _good(f"coupang:g{i}"), 100 + i)
    for _ in range(15):
        await bot.threads_flow.tick(T7)
    assert len(bot.fake.posts()) == 20  # 괜찮은 딜은 그날 딜 글 20개까지 (안전 상한)
    assert sorted(j.product_id for j in bot.db.threads_waiting()) == ["coupang:g10", "coupang:p10"]


async def test_jobs_queued_before_the_rule_are_judged_from_the_deal(bot) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    _at(bot, T7)
    _queue(bot, _good("coupang:g"), 1)
    job = bot.db.threads_waiting()[0]
    job.payload.pop("good")
    assert bot.threads_flow.is_good(job)


def test_config_defaults_and_threads_api_ceiling() -> None:
    rc = ThreadsRhythmConfig()
    assert (rc.good_min_gap_minutes, rc.good_max_gap_minutes, rc.good_max_deal_posts_per_day) == (15, 30, 20)
    assert (rc.min_gap_minutes, rc.max_deal_posts_per_day, rc.hours) == (45, 10, "07:00-24:00")
    with pytest.raises(ValueError):
        ThreadsRhythmConfig(good_max_deal_posts_per_day=250)  # 공식 한도(24시간 250개)에 닿는 값은 안 받음
