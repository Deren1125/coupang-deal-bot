"""간단한 비동기 재시도 헬퍼 (지수 백오프 + 지터)."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

T = TypeVar("T")
log = logging.getLogger(__name__)


class RetryableError(Exception):
    """재시도해도 되는 오류 (5xx, 429, 일시적 네트워크 오류 등)."""


def is_retryable_http(exc: BaseException) -> bool:
    if isinstance(exc, RetryableError | httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    backoff: float = 2.0,
    max_backoff: float = 60.0,
    should_retry: Callable[[BaseException], bool] = is_retryable_http,
    label: str = "operation",
) -> T:
    attempts = max(1, attempts)
    last: BaseException | None = None
    for i in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i >= attempts or not should_retry(exc):
                raise
            delay = min(max_backoff, backoff * (2 ** (i - 1))) * (0.8 + random.random() * 0.4)
            log.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs", label, i, attempts, exc, delay)
            await asyncio.sleep(delay)
    assert last is not None
    raise last
