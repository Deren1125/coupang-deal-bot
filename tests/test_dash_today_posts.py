"""'오늘 올린 글' (주인 10/23): 오늘 한 일 시트 맨 위 — 블로그 셋·텔레그램·인스타·스레드를 채널별로. 오늘(KST)만 · 연습 글 제외 ·
스레드는 딜봇 글 + 계정 전체 글을 주소·id 로 합침 · 빈 칸 숨김 · 링크는 https(블로그는 네이버)만."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import today_posts as tp
from dealbot.dashboard.today import day_start
from dealbot.utils.timeutil import to_iso

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=KST)
START = day_start(NOW.timestamp(), "Asia/Seoul")
END = NOW.timestamp() + 60


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    b.notifier.channel_post_base = "https://t.me/hot_deal_and_info"
    yield b
    b.db.close()


def at(h: int, m: int = 0, days: int = 0) -> datetime:
    return (NOW.replace(hour=h, minute=m) - timedelta(days=days))


def product(bot: DealBot, pid: str, name: str) -> None:
    with bot.db._lock:
        bot.db._conn.execute("INSERT INTO products (product_id, name, url, first_seen_at, last_seen_at) VALUES (?, ?, 'https://x.kr/p', ?, ?)",
                             (pid, name, to_iso(NOW), to_iso(NOW)))


def tg(bot: DealBot, pid: str, mid: int, when: datetime, dry: int = 0) -> None:
    with bot.db._lock:
        bot.db._conn.execute("INSERT INTO posts (product_id, source, price, channel_id, message_id, dry_run, posted_at) VALUES (?, 's', 1000, 'c', ?, ?, ?)",
                             (pid, mid, dry, to_iso(when)))


def build(bot: DealBot, v2: dict | None = None) -> dict:
    return tp.build(bot, v2 or {}, START, END, "Asia/Seoul")


def test_empty_day_hides_every_group(bot: DealBot) -> None:
    assert build(bot) == {"count": 0, "groups": []}


def test_blogs_today_only_kst_and_naver_links(bot: DealBot) -> None:
    v2 = {"recent": {
        "finance": [{"title": "세금 정리", "url": "https://m.blog.naver.com/derenlog/1", "at": "2026-10-23T09:05:00"},
                    {"title": "어제 글", "url": "https://m.blog.naver.com/derenlog/0", "at": "2026-10-22T23:59:00"}],
        "issue": [{"title": "Pick 글", "url": "https://evil.example/x", "at": "2026-10-23T00:00:00"}],
        "hotdeal": []}}
    g = {x["key"]: x for x in build(bot, v2)["groups"]}
    assert list(g) == ["finance", "issue"] and g["finance"]["label"] == "아카이브 (derenlog)" and g["issue"]["label"] == "Pick (derenpick)"
    assert g["finance"]["rows"] == [{"title": "세금 정리", "when": "09:05", "link": {"label": "글", "url": "https://m.blog.naver.com/derenlog/1"}}]
    assert g["issue"]["rows"] == [{"title": "Pick 글", "when": "00:00"}]  # 네이버 아닌 주소는 뺌


def test_telegram_today_real_posts_with_channel_link(bot: DealBot) -> None:
    for pid, name in (("a", "청소기"), ("b", "연습딜"), ("c", "어제딜")):
        product(bot, pid, name)
    tg(bot, "a", 77, at(10, 30))
    tg(bot, "b", 78, at(11), dry=1)
    tg(bot, "c", 79, at(23, 0, days=1))
    (g,) = build(bot)["groups"]
    assert g["key"] == "telegram" and g["rows"] == [{"title": "청소기", "when": "10:30", "link": {"label": "채널 글", "url": "https://t.me/hot_deal_and_info/77"}}]


def test_telegram_without_channel_username_has_no_link(bot: DealBot) -> None:
    bot.notifier.channel_post_base = None
    product(bot, "a", "청소기")
    tg(bot, "a", 77, at(10))
    assert "link" not in build(bot)["groups"][0]["rows"][0]


def test_instagram_today_with_permalink_and_first_item(bot: DealBot) -> None:
    product(bot, "a", "청소기")
    product(bot, "b", "에어프라이어")
    one = bot.db.ig_record_post("single", media_id="1", items=[{"product_id": "a", "queue_id": 1}], now=at(12).astimezone(ZoneInfo("UTC")))
    bot.db.ig_set_permalink(one, "https://www.instagram.com/p/AAA/")
    bot.db.ig_record_post("top5", media_id="2", items=[{"product_id": "b"}, {"product_id": "a"}], dry_run=True, now=at(13).astimezone(ZoneInfo("UTC")))
    bot.db.ig_record_post("single", media_id="3", items=[{"product_id": "a"}], now=at(20, 0, days=1).astimezone(ZoneInfo("UTC")))
    (g,) = build(bot)["groups"]
    assert g["key"] == "instagram"
    assert g["rows"] == [{"title": "청소기", "when": "12:00", "link": {"label": "인스타", "url": "https://www.instagram.com/p/AAA/"}}]


def job(bot: DealBot, pid: str, post_id: str, when: datetime, permalink: str | None = None, text: str = "스레드 본문\n둘째 줄") -> None:
    jid = bot.db.threads_enqueue("info", pid, {"text": text}, now=when)
    bot.db.threads_update(jid, status="posted", post_id=post_id, now=when)
    if permalink:
        bot.db.threads_set_permalink(jid, permalink)


def test_threads_merge_dedupe_by_permalink_and_id(bot: DealBot) -> None:
    L1, L2, L3 = (f"https://www.threads.com/@deren/post/D{i}" for i in "123")
    job(bot, "p1", "111", at(9), L1)           # 딜봇 글 + 계정 글(같은 주소) → 한 줄
    job(bot, "p2", "222", at(10))              # 주소 못 남김 → 계정 글 id 로 합침, 주소는 계정 글에서
    job(bot, "p3", "333", at(11))              # 계정 글에 없음 → 딜봇 글만
    job(bot, "p4", "444", at(12, days=1), L1.replace("D1", "D9"))  # 어제
    v2 = {"threads": {"posts": [
        {"id": "111", "text": "계정쪽 본문", "permalink": L1, "at": at(9).isoformat()},
        {"id": "222", "text": "둘째", "permalink": L2, "at": at(10).isoformat()},
        {"id": "555", "text": "threads 프로그램 글", "permalink": L3, "at": at(13).astimezone(ZoneInfo("UTC")).isoformat()},
        {"id": "666", "text": "어제 글", "permalink": "https://www.threads.com/@deren/post/D6", "at": at(23, days=1).isoformat()}]}}
    (g,) = build(bot, v2)["groups"]
    assert g["key"] == "threads"
    assert [(r["when"], r["title"], r.get("link", {}).get("url")) for r in g["rows"]] == [
        ("13:00", "threads 프로그램 글", L3), ("11:00", "스레드 본문", None), ("10:00", "둘째", L2), ("09:00", "계정쪽 본문", L1)]


def test_threads_dry_run_jobs_are_skipped(bot: DealBot) -> None:
    jid = bot.db.threads_enqueue("info", "p1", {"text": "연습"}, now=at(9))
    bot.db.threads_update(jid, status="posted", post_id="1", dry_run=True, now=at(9))
    assert build(bot) == {"count": 0, "groups": []}


def test_group_order_and_count(bot: DealBot) -> None:
    product(bot, "a", "청소기")
    tg(bot, "a", 1, at(8))
    job(bot, "p1", "9", at(9))
    out = build(bot, {"recent": {"hotdeal": [{"title": "핫딜 글", "url": None, "at": "2026-10-23T07:00:00"}]}})
    assert [g["key"] for g in out["groups"]] == ["hotdeal", "telegram", "threads"] and out["count"] == 3


@pytest.mark.asyncio
async def test_today_api_carries_posts_next_to_actions(bot: DealBot) -> None:
    from dash_helpers import get_json, session_headers

    product(bot, "a", "청소기")
    tg(bot, "a", 5, datetime.now(KST))
    status, body, _ = await get_json(bot, "/dashboard/api/today", session_headers(bot))
    assert status == 200 and body["groups"] == [] and body["posts"]["count"] == 1
    assert body["posts"]["groups"][0]["rows"][0]["link"]["url"] == "https://t.me/hot_deal_and_info/5"
