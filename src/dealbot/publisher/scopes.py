"""메타(인스타그램·스레드) 토큰이 가진 권한(scope) 기억.

메타는 토큰에 어떤 권한이 있는지 바로 알려 주지 않는다 (인스타 로그인 토큰·스레드 토큰 모두 debug 화면 없이는 모름).
그래서 그 권한이 꼭 필요한 API 를 불러 본 결과로 안다: 권한 오류면 'missing', 성공이면 'granted'.
주인 결정 10/15: 통계(인사이트)·답글 읽기 권한을 새로 요청하지만, 주인이 /instaauth·/threadsauth 로 다시 연결하기 전까지는
지금 토큰으로 발행이 그대로 돼야 한다 — 권한이 없다고 알게 된 기능은 조용히 건너뛰고(같은 API 를 계속 두드리지 않음),
주인 할 일에 '다시 연결' 한 줄만 남긴다. 토큰이 바뀌면(재연결·자동 갱신) 다시 '모름'으로 돌아가 한 번 더 불러 본다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

GRANTED = "granted"
MISSING = "missing"
UNKNOWN = "unknown"

# 이번에 새로 요청하는 권한 (없어도 발행은 됨)
THREADS_INSIGHTS = "threads_manage_insights"
THREADS_READ_REPLIES = "threads_read_replies"
IG_INSIGHTS = "instagram_business_manage_insights"


def _fingerprint(token: str | None) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:16] if token else ""


class ScopeState:
    """platform('threads'|'instagram') 토큰의 권한 기억 (db.kv '{platform}_scopes', 토큰 지문과 같이)."""

    def __init__(self, db: Any, platform: str, token: Callable[[], str | None]) -> None:
        self.db = db
        self.platform = platform
        self._token = token
        self.key = f"{platform}_scopes"

    def _load(self) -> dict[str, Any]:
        fp = _fingerprint(self._token())
        try:
            data = json.loads(self.db.kv_get(self.key) or "{}")
        except (TypeError, ValueError):
            data = {}
        if not isinstance(data, dict) or data.get("fp") != fp:
            return {"fp": fp, "granted": [], "missing": []}
        return data

    def status(self, scope: str) -> str:
        if not self._token():
            return UNKNOWN
        data = self._load()
        if scope in (data.get("granted") or []):
            return GRANTED
        if scope in (data.get("missing") or []):
            return MISSING
        return UNKNOWN

    def granted(self, scope: str) -> bool:
        return self.status(scope) == GRANTED

    def usable(self, scope: str) -> bool:
        """불러 봐도 되는가: 권한이 있거나 아직 모름 (없다고 안 권한은 같은 토큰으로 다시 두드리지 않음)."""
        return bool(self._token()) and self.status(scope) != MISSING

    def mark(self, scope: str, ok: bool) -> None:
        if not self._token():
            return
        data = self._load()
        granted = [s for s in (data.get("granted") or []) if s != scope]
        missing = [s for s in (data.get("missing") or []) if s != scope]
        (granted if ok else missing).append(scope)
        before = self.status(scope)
        try:
            self.db.kv_set(self.key, json.dumps({"fp": data["fp"], "granted": granted, "missing": missing}))
        except Exception as e:  # noqa: BLE001 — 기억 못 해도 기능은 그대로
            log.warning("%s scope state not saved: %s", self.platform, e)
            return
        now = GRANTED if ok else MISSING
        if before != now:
            log.info("%s token scope %s: %s", self.platform, scope, now)

    def missing(self, scopes: tuple[str, ...] | list[str]) -> list[str]:
        return [s for s in scopes if self.status(s) == MISSING]
