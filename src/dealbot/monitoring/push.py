"""휴대폰 푸시 알림 (텔레그램과 별도).

- ntfy    : 무료. 폰에 ntfy 앱을 깔고 토픽을 구독하면 됨. 공개 서버 ntfy.sh 또는 셀프호스팅.
- pushover: 유료(1회 결제). PUSHOVER_USER_KEY + PUSHOVER_APP_TOKEN.
"""

from __future__ import annotations

import logging

import httpx

from dealbot.config import PushConfig, Secrets

log = logging.getLogger(__name__)

_NTFY_PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}
_PUSHOVER_PRIORITY = {"min": -2, "low": -1, "default": 0, "high": 1, "urgent": 1}


class PushNotifier:
    def __init__(self, cfg: PushConfig, secrets: Secrets, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.secrets = secrets
        self.http = http
        self.provider = self._resolve_provider()

    def _resolve_provider(self) -> str | None:
        p = (self.cfg.provider or "auto").lower()
        if p in ("none", "off", "false"):
            return None
        if p == "pushover" or (p == "auto" and self.secrets.has_pushover):
            return "pushover" if self.secrets.has_pushover else None
        if p == "ntfy" or (p == "auto" and self.secrets.has_ntfy):
            return "ntfy" if self.secrets.has_ntfy else None
        return None

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def wants(self, event: str) -> bool:
        return self.enabled and event in self.cfg.events

    async def send(
        self,
        title: str,
        message: str,
        *,
        click_url: str | None = None,
        priority: str = "default",
        tags: list[str] | None = None,
    ) -> bool:
        if not self.enabled:
            return False
        try:
            if self.provider == "ntfy":
                return await self._send_ntfy(title, message, click_url, priority, tags)
            if self.provider == "pushover":
                return await self._send_pushover(title, message, click_url, priority)
        except httpx.HTTPError as e:
            log.error("push (%s) failed: %s", self.provider, e)
        return False

    async def _send_ntfy(self, title: str, message: str, click_url: str | None, priority: str, tags: list[str] | None) -> bool:
        body = {
            "topic": self.secrets.ntfy_topic,
            "title": title[:200],
            "message": message[:3800],
            "priority": _NTFY_PRIORITY.get(priority, 3),
        }
        if click_url:
            body["click"] = click_url
        if tags:
            body["tags"] = tags
        headers = {}
        if self.secrets.ntfy_token:
            headers["Authorization"] = f"Bearer {self.secrets.ntfy_token}"
        resp = await self.http.post(self.cfg.ntfy_url.rstrip("/"), json=body, headers=headers, timeout=15)
        if resp.status_code >= 400:
            log.error("ntfy error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True

    async def _send_pushover(self, title: str, message: str, click_url: str | None, priority: str) -> bool:
        data = {
            "token": self.secrets.pushover_app_token,
            "user": self.secrets.pushover_user_key,
            "title": title[:250],
            "message": message[:1024],
            "priority": _PUSHOVER_PRIORITY.get(priority, 0),
        }
        if click_url:
            data["url"] = click_url
            data["url_title"] = "텔레그램에서 열기"
        resp = await self.http.post("https://api.pushover.net/1/messages.json", data=data, timeout=15)
        if resp.status_code >= 400:
            log.error("pushover error %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
