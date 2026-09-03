"""발행 속도 제한: 시간당/일당 개수 + 최소 간격. 기록은 DB(posts) 기반이라 재시작해도 유지됨."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dealbot.config import PublishConfig
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow


@dataclass(slots=True)
class RateDecision:
    allowed: bool
    reason: str | None = None
    retry_after: timedelta | None = None


class RateLimiter:
    def __init__(self, db: Database, cfg: PublishConfig) -> None:
        self.db = db
        self.cfg = cfg

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        return {
            "posts_hour": self.db.count_posts_since(now - timedelta(hours=1)),
            "posts_day": self.db.count_posts_since(now - timedelta(days=1)),
            "max_hour": self.cfg.max_per_hour,
            "max_day": self.cfg.max_per_day,
            "last_post_at": self.db.last_post_time(),
            "min_interval_seconds": self.cfg.min_interval_seconds,
        }

    def check(self, now: datetime | None = None) -> RateDecision:
        now = now or utcnow()
        last = self.db.last_post_time()
        if last is not None and self.cfg.min_interval_seconds > 0:
            elapsed = now - last
            min_gap = timedelta(seconds=self.cfg.min_interval_seconds)
            if elapsed < min_gap:
                return RateDecision(False, "min_interval", min_gap - elapsed)

        if self.cfg.max_per_hour > 0 and self.db.count_posts_since(now - timedelta(hours=1)) >= self.cfg.max_per_hour:
            return RateDecision(False, "hourly_limit", timedelta(minutes=5))

        if self.cfg.max_per_day > 0 and self.db.count_posts_since(now - timedelta(days=1)) >= self.cfg.max_per_day:
            return RateDecision(False, "daily_limit", timedelta(minutes=30))

        return RateDecision(True)
