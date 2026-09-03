from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from dealbot.cli import sample_deal
from dealbot.config import PublishConfig
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import normalize_chat_id
from dealbot.publisher.templates import TemplateRenderer
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow


def test_render_deal_post(repo_root: Path) -> None:
    r = TemplateRenderer(repo_root / "templates")
    text = r.render_deal(sample_deal(), "https://link.coupang.com/a/sample")
    assert "<b>29,900원</b>" in text
    assert "<s>49,900원</s>" in text
    assert "40% 할인" in text
    assert "42,000원 대비 <b>29%</b>" in text
    assert "🚀 로켓배송 · 📦 무료배송" in text
    assert '<a href="https://link.coupang.com/a/sample">' in text
    assert "쿠팡 파트너스 활동의 일환" in text
    assert len(text) < 1024


def test_render_escapes_html(repo_root: Path) -> None:
    r = TemplateRenderer(repo_root / "templates")
    p = Product(source="s", product_id="1", name="<script>alert(1)</script> & 상품", price=1000, url="u")
    d = Deal(product=p, verdict=DealVerdict(is_deal=True), affiliate_url="https://l")
    text = r.render_deal(d, "https://l")
    assert "&lt;script&gt;" in text and "&amp;" in text
    assert "할인" not in text and "평균가" not in text


def test_status_and_summary_templates_render(repo_root: Path, db: Database) -> None:
    r = TemplateRenderer(repo_root / "templates")
    ctx = {
        "version": "0.1.0",
        "uptime": "1시간",
        "paused": False,
        "dry_run": True,
        "publish_enabled": True,
        "has_coupang": False,
        "has_channel": False,
        "collectors": [
            {"name": "a", "type": "t", "enabled": True, "available": True, "unavailable_reason": None, "running": False,
             "interval_minutes": 5, "last_status": "ok", "last_run_ago": "1분 전", "collected": 3, "deals": 1, "queued": 1,
             "error": None, "next_in": "4분"},
            {"name": "b", "type": "t", "enabled": False},
            {"name": "c", "type": "t", "enabled": True, "available": False, "unavailable_reason": "no key"},
        ],
        "rate": {"posts_hour": 1, "posts_day": 2, "max_hour": 6, "max_day": 40, "last_post_at": utcnow()},
        "queue": {"pending": 2},
        "products": 10,
        "price_points": 100,
        "db_mb": 0.1,
        "last_error": "boom <x>",
        "last_error_at": "09/03 10:00",
        "tz": "Asia/Seoul",
    }
    text = r.render("status.j2", **ctx)
    assert "DRY-RUN" in text and "쿠팡 API 키 미설정" in text and "b — 꺼짐" in text and "no key" in text
    assert "boom &lt;x&gt;" in text

    s = db.summary(utcnow() - timedelta(days=1))
    out = r.render("daily_summary.j2", s=s, tz="Asia/Seoul")
    assert "일일 요약" in out and "에러 없음" in out


def test_rate_limiter(db: Database) -> None:
    cfg = PublishConfig(max_per_hour=2, max_per_day=3, min_interval_seconds=60)
    rl = RateLimiter(db, cfg)
    now = utcnow()
    assert rl.check(now).allowed

    db.record_post(sample_deal(), channel_id=None, message_id=None, now=now - timedelta(seconds=30))
    d = rl.check(now)
    assert not d.allowed and d.reason == "min_interval" and d.retry_after == timedelta(seconds=30)

    db.record_post(sample_deal(), channel_id=None, message_id=None, now=now - timedelta(minutes=10))
    assert rl.check(now).reason == "min_interval"
    assert rl.check(now + timedelta(minutes=2)).reason == "hourly_limit"

    later = now + timedelta(hours=2)
    db.record_post(sample_deal(), channel_id=None, message_id=None, now=later - timedelta(minutes=5))
    assert rl.check(later).reason == "daily_limit"
    assert rl.check(later + timedelta(days=1)).allowed
    snap = rl.snapshot(later)
    assert snap["posts_hour"] == 1 and snap["posts_day"] == 3 and snap["max_hour"] == 2


def test_normalize_chat_id() -> None:
    assert normalize_chat_id("-1001234") == -1001234
    assert normalize_chat_id("42") == 42
    assert normalize_chat_id("@chan") == "@chan"
    assert normalize_chat_id(None) is None
