"""로그: stdout + 회전 파일."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class _TzFormatter(logging.Formatter):
    """로그 시각을 지정한 시간대로. 컨테이너에 시스템 tzdata 가 없어도 KST 로 찍힌다."""

    def __init__(self, fmt: str, tz: str) -> None:
        super().__init__(fmt)
        try:
            self._tz = ZoneInfo(tz)
        except Exception:  # noqa: BLE001 - 알 수 없는 시간대면 UTC
            self._tz = ZoneInfo("UTC")

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created, self._tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    filename: str = "dealbot.log",
    timezone: str = "Asia/Seoul",
) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = _TzFormatter(_FORMAT, timezone)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / filename, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # 시끄러운 라이브러리 로그 억제
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
