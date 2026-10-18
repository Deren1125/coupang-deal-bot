"""딜 탭 '오늘 올림' 줄의 링크 (주인 10/18 '왜 링크가 텔레그램 밖에 없어?'): 채널 글 · 핫딜 블로그 글 · 인스타 · 스레드 — 아는 주소만.

- 블로그 글은 Blog-Auto 기록 파일 recent.hotdeal[].deals 의 product_id(+posted_at) 로만 잇는다 (제목으로는 잇지 않음)
- 인스타·스레드 주소는 딜봇이 올린 뒤 permalink 로 읽어 남긴 것 (ig_posts.permalink · threads_jobs.permalink), 연습 글은 뺌
- 화면: '채널 ↗'·'블로그 ↗'·'인스타 ↗'·'스레드 ↗' (아는 것만, 넘치면 둘째 줄)
네트워크 없이."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, push_settings, queued
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.blogauto import POST_DEALS_MAX, parse_v2
from dealbot.dashboard.state import blog_url_for, sns_url
from dealbot.models import PublishResult
from test_dash_state import state, write_export

TZ = "Asia/Seoul"
BLOG = "https://m.blog.naver.com/demiyum/224000000009"
IG = "https://www.instagram.com/p/DPq7x2aE1bc/"
TH = "https://www.threads.com/@oneul_hot_deal/post/DPr1a2b3c4d"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    b.notifier.channel_post_base = "https://t.me/hot_deal_and_info"
    yield b
    b.db.close()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ------------------------------------------------------------ 기록 파일 읽기
def test_recent_hotdeal_deals_are_parsed_and_filtered() -> None:
    raw = {"recent": {"hotdeal": [{"pid": 1, "title": "핫딜", "url": BLOG, "deals": [
        {"product_id": "coupang:123:456", "posted_at": "2026-10-18T09:00:00+00:00"},
        "naver:smartstore:777",  # product_id 만 적은 모양도
        {"product_id": "bad key with spaces"}, {"product_id": "<script>"}, {"posted_at": "x"}, 42, None,
    ]}], "issue": [{"pid": 2, "title": "Pick", "url": "https://m.blog.naver.com/derenpick/1", "deals": ["coupang:1"]}]}}
    v2 = parse_v2(raw)
    assert v2["recent"]["hotdeal"][0]["deals"] == [
        {"product_id": "coupang:123:456", "posted_at": "2026-10-18T09:00:00+00:00"},
        {"product_id": "naver:smartstore:777", "posted_at": None}]
    assert "deals" not in v2["recent"]["issue"][0]  # 핫딜 글만
    many = parse_v2({"recent": {"hotdeal": [{"title": "모음", "url": BLOG, "deals": [f"coupang:{i}" for i in range(30)]}]}})
    assert len(many["recent"]["hotdeal"][0]["deals"]) == POST_DEALS_MAX
    old = parse_v2({"recent": {"hotdeal": [{"title": "예전 Blog-Auto", "url": BLOG}]}})  # 칸이 없던 기록 파일도 그대로
    assert "deals" not in old["recent"]["hotdeal"][0]


# ------------------------------------------------------------ 블로그 글 잇기
def test_blog_match_needs_the_same_product_and_the_same_posting() -> None:
    at = datetime(2026, 10, 18, 0, 5, tzinfo=UTC)
    t = at.timestamp()
    post = {"title": "SUNGOD 헤어 드라이기", "url": BLOG, "at": "2026-10-18T09:40:00",
            "deals": [{"product_id": "coupang:1:2", "posted_at": _iso(at + timedelta(seconds=3))}]}
    assert blog_url_for("coupang:1:2", t, [post], TZ) == BLOG
    assert blog_url_for("coupang:1:3", t, [post], TZ) is None  # 같은 상품의 다른 옵션은 다른 딜
    assert blog_url_for("coupang:1:2", t + 6 * 3600, [post], TZ) is None  # 같은 상품을 다시 올린 딜에 예전 글을 잇지 않음
    assert blog_url_for("coupang:1:2", t, [{**post, "url": None}], TZ) is None
    # posted_at 이 없으면: 블로그 글이 딜보다 뒤에 쓰였을 때만
    bare = {**post, "deals": [{"product_id": "coupang:1:2", "posted_at": None}]}
    assert blog_url_for("coupang:1:2", t, [bare], TZ) == BLOG
    assert blog_url_for("coupang:1:2", t + 3 * 3600, [bare], TZ) is None
    # 제목이 같아도 딜 칸이 없으면 잇지 않는다
    assert blog_url_for("coupang:1:2", t, [{"title": "SUNGOD 헤어 드라이기", "url": BLOG, "at": post["at"]}], TZ) is None


def test_sns_urls_only_from_that_service_over_https() -> None:
    assert sns_url(IG, ("www.instagram.com",)) == IG
    for bad in ("http://www.instagram.com/p/x/", "https://evil.io/p/x/", "javascript:alert(1)", "", None, 5,
                "https://t.me/hot_deal_and_info/12"):
        assert sns_url(bad, ("www.instagram.com", "t.me")) is None, bad


# ------------------------------------------------------------ 딜 탭 값
async def test_deal_row_carries_channel_blog_instagram_and_threads(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    it = queued(bot, status="published", product_id="coupang:55:66", name="SUNGOD 헤어 드라이기")
    other = queued(bot, status="published", product_id="coupang:77:88", name="다른 딜")
    now = datetime.now(UTC)
    bot.db.record_post(it.deal, channel_id="@hot_deal_and_info", message_id=1201, now=now)
    bot.db.record_post(other.deal, channel_id="@hot_deal_and_info", message_id=1202, now=now)
    local = now.astimezone(ZoneInfo(TZ)).replace(tzinfo=None)
    write_export(tmp_path / "blogauto.json", recent={"hotdeal": [
        {"pid": 50, "title": "SUNGOD 헤어 드라이기 29,900원", "url": BLOG, "at": _iso(local + timedelta(minutes=40)),
         "deals": [{"product_id": "coupang:55:66", "posted_at": _iso(now)}]},
        # 제목만 같은 글 (딜 칸 없음) — 다른 딜에 잇지 않음
        {"pid": 49, "title": "다른 딜", "url": "https://m.blog.naver.com/demiyum/224000000008", "at": _iso(local)}]})
    blogauto_status(settings)
    # 인스타: 딜 하나짜리 글 + 같은 딜이 든 TOP5 (하나짜리가 먼저) · 연습 글 · 다른 딜
    single = bot.db.ig_record_post("single", media_id="1789", items=[{"product_id": "coupang:55:66", "queue_id": it.id}])
    bot.db.ig_set_permalink(single, IG)
    top5 = bot.db.ig_record_post("top5", media_id="1790", items=[{"product_id": "coupang:55:66", "queue_id": it.id}],
                                 now=now + timedelta(hours=2))
    bot.db.ig_set_permalink(top5, "https://www.instagram.com/p/TOP5xxxxxx/")
    dry = bot.db.ig_record_post("single", media_id="dry-run", items=[{"product_id": "coupang:77:88", "queue_id": other.id}], dry_run=True)
    bot.db.ig_set_permalink(dry, "https://www.instagram.com/p/DRYxxxxxxx/")
    # 스레드
    job = bot.db.threads_enqueue("deal", "coupang:55:66", {}, queue_id=it.id)
    assert job
    bot.db.threads_update(job, status="posted", post_id="1801")
    bot.db.threads_set_permalink(job, TH)

    rows = {r["qid"]: r for r in (await state(bot))["deal"]["today"]}
    row = rows[it.id]
    assert row["url"] == "https://t.me/hot_deal_and_info/1201"
    assert row["blog_url"] == BLOG and row["ig_url"] == IG and row["th_url"] == TH
    bare = rows[other.id]
    assert bare["url"] == "https://t.me/hot_deal_and_info/1202"
    assert not ({"blog_url", "ig_url", "th_url"} & set(bare))  # 모르는 곳은 칸도 없음 (단추 없음)


async def test_deal_rows_without_export_or_permalinks_keep_only_the_channel(bot: DealBot) -> None:
    it = queued(bot, status="published", product_id="coupang:9:9")
    bot.db.record_post(it.deal, channel_id="@hot_deal_and_info", message_id=7)
    job = bot.db.threads_enqueue("deal", "coupang:9:9", {}, queue_id=it.id)
    assert job
    bot.db.threads_update(job, status="posted", post_id="1")  # 주소를 못 읽은 (예전) 글
    bot.db.ig_record_post("single", media_id="2", items=[{"product_id": "coupang:9:9", "queue_id": it.id}])
    row = (await state(bot))["deal"]["today"][0]
    assert row["url"].endswith("/7") and not ({"blog_url", "ig_url", "th_url"} & set(row))


# ------------------------------------------------------------ 올린 뒤 주소 남기기
async def test_threads_and_instagram_keep_the_permalink_after_posting(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    async def th_link(mid: int | None) -> str | None:
        return f"https://www.threads.com/@oneul_hot_deal/post/{mid}"

    async def ig_link(mid: int | None) -> str | None:
        return f"https://www.instagram.com/p/{mid}/"

    monkeypatch.setattr(bot, "_threads_permalink", th_link)
    monkeypatch.setattr(bot, "_instagram_permalink", ig_link)
    job = bot.db.threads_enqueue("deal", "coupang:1", {}, queue_id=31)
    assert job
    bot.db.threads_update(job, status="posted", post_id="555")
    await bot.threads_flow._keep_permalink(job, 555)
    since = datetime.now(UTC) - timedelta(hours=1)
    assert bot.db.threads_permalink_for(31, since) == "https://www.threads.com/@oneul_hot_deal/post/555"
    row = bot.db.ig_record_post("single", media_id="777", items=[{"product_id": "coupang:1", "queue_id": 31}])
    await bot.ig._keep_permalink(row, PublishResult(ok=True, message_id=777))
    assert bot.db.ig_permalink_for(31, since) == "https://www.instagram.com/p/777/"
    # 연습 글은 주소를 읽지 않음 · 주소 읽기가 실패해도 발행은 그대로 (예외가 새지 않음)
    dry = bot.db.ig_record_post("single", media_id="dry-run", items=[{"queue_id": 32}], dry_run=True)
    await bot.ig._keep_permalink(dry, PublishResult(ok=True, dry_run=True, message_id=1))

    async def broken(mid: int | None) -> str | None:
        raise RuntimeError("network down")

    monkeypatch.setattr(bot, "_instagram_permalink", broken)
    monkeypatch.setattr(bot, "_threads_permalink", broken)
    await bot.ig._keep_permalink(dry, PublishResult(ok=True, message_id=1))
    await bot.threads_flow._keep_permalink(job, 1)
    assert bot.db.ig_permalink_for(32, since) is None


def test_old_database_gets_the_permalink_columns(tmp_path: Path) -> None:
    import sqlite3

    from dealbot.storage.db import Database

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE ig_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, media_id TEXT, items TEXT, "
                      "dry_run INTEGER NOT NULL DEFAULT 0, posted_at TEXT NOT NULL);")
    con.close()
    db = Database(path)
    try:
        row = db.ig_record_post("single", media_id="1", items=[{"queue_id": 5}])
        db.ig_set_permalink(row, IG)
        assert db.ig_permalink_for(5, datetime.now(UTC) - timedelta(hours=1)) == IG
    finally:
        db.close()


# ------------------------------------------------------------ 화면
def test_deal_row_link_buttons_render_in_korean() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    css = (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "dealLinks(r)" in js and "'채널 글'" not in js
    assert re.search(r"\.row:has\(\.dl\)\{[^}]*flex-wrap:wrap", css) and re.search(r"\.dl\{[^}]*flex-wrap:wrap", css)
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    parts = [re.search(r"^const ext = .*$", js, re.M), re.search(r"^const DEAL_LINKS = .*$", js, re.M),
             re.search(r"^function dealLinks\(r\) \{.*?^\}$", js, re.M | re.S)]
    assert all(parts)
    script = ("const el = (t, a, ...k) => ({t, a, k: k.flat()});\n" + "\n".join(m.group(0) for m in parts if m) + "\n"
              "const out = [{url: 'https://t.me/c/1', blog_url: 'B', ig_url: 'I', th_url: 'T'}, {url: 'https://t.me/c/2'}, {}].map(dealLinks);\n"
              "console.log(JSON.stringify(out.map(o => o && o.k.map(b => [b.k.join(''), b.a.href, b.a.target]))));")
    got = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout)
    assert got == [[["채널 ↗", "https://t.me/c/1", "_blank"], ["블로그 ↗", "B", "_blank"], ["인스타 ↗", "I", "_blank"],
                    ["스레드 ↗", "T", "_blank"]], [["채널 ↗", "https://t.me/c/2", "_blank"]], None]
