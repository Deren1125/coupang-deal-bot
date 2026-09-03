from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from dealbot.utils.timeutil import utcnow


@dataclass(slots=True)
class CollectorStatus:
    name: str
    type: str
    interval_minutes: int
    enabled: bool = True
    available: bool = True
    unavailable_reason: str | None = None
    next_run_at: datetime | None = None
    running: bool = False
    run_requested: bool = False


@dataclass(slots=True)
class BotState:
    started_at: datetime = field(default_factory=utcnow)
    paused: bool = False
    dry_run: bool = False
    collectors: dict[str, CollectorStatus] = field(default_factory=dict)
    last_error: str | None = None
    last_error_at: datetime | None = None

    def set_error(self, message: str) -> None:
        self.last_error = message[:500]
        self.last_error_at = utcnow()
