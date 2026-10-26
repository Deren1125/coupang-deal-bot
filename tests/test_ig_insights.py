"""인스타 글마다 숫자 (주인 10/26 — 2주 시험): 하루 지난 글을 하루 한 번 읽어 ig_posts 에 남기고 통계 카드에 목록으로."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.growth import ig_insights
from dealbot.publisher.instagram import InstagramClient, InstagramToken
from dealbot.publisher.scopes import IG_INSIGHTS
from dealbot.utils.timeutil import utcnow


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.instagram_app_id = "IGAPP"
    settings.secrets.instagram_app_secret = "IGSECRET"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _meta(bot: DealBot, *, reject: set[str] = frozenset(), deny: bool = False) -> list[tuple[str, str]]:
    """media_id → 숫자. reject 의 지표 이름이 들어 있으면 400 (그 글 종류엔 없는 지표). deny 면 권한 없음 (code 10)."""
    calls: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        media = req.url.path.split("/")[-2]
        metrics = req.url.params["metric"]
        calls.append((media, metrics))
        if deny:
            return httpx.Response(400, json={"error": {"message": "(#10) Application does not have permission", "code": 10}})
        if any(m in metrics.split(",") for m in reject):
            return httpx.Response(400, json={"error": {"message": "(#100) metric not supported", "code": 100}})
        data = [{"name": m, "values": [{"value": 7 if m == "saved" else 1200 if m == "reach" else 3}]} for m in metrics.split(",")]
        return httpx.Response(200, json={"data": data})

    client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="IGAPP", app_secret="IGSECRET",
                             retry_backoff=0.01)
    bot.instagram.client = client
    bot.instagram_look.client = client
    bot.instagram.save_token(InstagramToken("DEAL", "178", expires_at=utcnow() + timedelta(days=50)), "oneul_hot_deal")
    bot.instagram_look.save_token(InstagramToken("LOOK", "901", expires_at=utcnow() + timedelta(days=50)), "derenpick.look")
    return calls


async def test_reads_day_old_posts_once_a_day_for_both_accounts(bot: DealBot) -> None:
    now = utcnow()
    old = bot.db.ig_record_post("single", media_id="M1", items=[1], now=now - timedelta(hours=30))
    look = bot.db.ig_record_post("look", media_id="L1", items=[], now=now - timedelta(days=2))
    bot.db.ig_record_post("single", media_id="NEW", items=[2], now=now - timedelta(hours=3))  # 아직 하루 안 됨
    bot.db.ig_record_post("single", media_id="OLD", items=[3], now=now - timedelta(days=10))  # 시험 기간 밖
    bot.db.ig_record_post("single", media_id="dry-run", items=[4], dry_run=True, now=now - timedelta(days=2))
    calls = _meta(bot)
    assert await ig_insights.collect(bot, now) is True
    assert sorted(c[0] for c in calls) == ["L1", "M1"]
    deal = bot.db.ig_recent_insights(now - timedelta(days=14), look=False)
    assert [r["id"] for r in deal] == [old] and deal[0]["insights"]["reach"] == 1200 and deal[0]["insights"]["saved"] == 7
    assert [r["id"] for r in bot.db.ig_recent_insights(now - timedelta(days=14), look=True)] == [look]
    calls.clear()
    assert await ig_insights.collect(bot, now + timedelta(hours=2)) is False and calls == []  # 하루에 한 번만
    await ig_insights.collect(bot, now + timedelta(hours=25))
    assert sorted(c[0] for c in calls) == ["L1", "M1", "NEW"]  # 하루 뒤: 다시 읽고, 그사이 하루가 된 글도


async def test_reels_ask_watch_time_and_fall_back_when_a_metric_is_unknown(bot: DealBot) -> None:
    now = utcnow()
    bot.db.ig_record_post("reel", media_id="R1", items=[], now=now - timedelta(days=2))
    calls = _meta(bot, reject={"views"})
    await ig_insights.collect(bot, now)
    assert "ig_reels_avg_watch_time" in calls[0][1] and calls[1][1] == ",".join(ig_insights.SAFE)
    row = bot.db.ig_recent_insights(now - timedelta(days=14))[0]
    assert row["insights"]["reach"] == 1200 and "views" not in row["insights"]


async def test_no_insights_permission_is_remembered_and_not_asked_again(bot: DealBot) -> None:
    now = utcnow()
    bot.db.ig_record_post("single", media_id="M1", items=[], now=now - timedelta(days=2))
    calls = _meta(bot, deny=True)
    assert await ig_insights.collect(bot, now) is False
    assert not bot.instagram.scopes.usable(IG_INSIGHTS)
    calls.clear()
    await ig_insights.collect(bot, now + timedelta(days=1, hours=1))
    assert calls == []  # 권한이 없다고 안 토큰으로는 다시 부르지 않음
