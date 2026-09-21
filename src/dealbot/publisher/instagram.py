"""인스타그램 자동 게시 — "Instagram API with Instagram Login" (페이스북 페이지 없이, 프로페셔널 계정만).

흐름은 스레드와 같다: 승인 링크 → code → 단기 토큰 → 장기 토큰(60일, 자동 갱신) → 컨테이너 생성 → 처리 대기 → 게시.
인스타는 사진 없는 글을 못 올리고 JPEG 만 받으므로, 봇이 만든 카드 이미지(공개 도메인 /media/…)를 올린다.
본문 링크는 눌리지 않으니 "프로필 링크" 로 유도하고, 수수료 고지는 본문에 넣는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from dealbot.models import Deal, PublishResult
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry
from dealbot.utils.retry import RetryableError, retry_async
from dealbot.utils.timeutil import from_iso, to_iso, utcnow

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.instagram.com"
API_VERSION = "v25.0"
AUTH_BASE = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
CAPTION_LIMIT = 2200

KV_TOKEN = "instagram_access_token"
KV_TOKEN_EXPIRES = "instagram_token_expires_at"
KV_USER_ID = "instagram_user_id"
KV_USERNAME = "instagram_username"

CONTAINER_WAIT_SECONDS = 120.0
CONTAINER_POLL_SECONDS = 4.0
PUBLISH_ATTEMPT_DELAYS: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)


class InstagramError(Exception):
    def __init__(self, message: str, *, code: int | None = None, subcode: int | None = None, step: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.step = step


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _not_ready(e: InstagramError) -> bool:
    msg = str(e).lower()
    return e.subcode == 2207027 or e.code == 24 or "not ready" in msg or "not available" in msg or "does not exist" in msg


@dataclass(slots=True)
class InstagramToken:
    access_token: str
    user_id: str
    expires_at: datetime | None = None


def authorize_url(app_id: str, redirect_uri: str, state: str = "dealbot") -> str:
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "response_type": "code",
        "state": state,
        "enable_fb_login": "0",
        "force_authentication": "1",
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


class InstagramClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        app_id: str | None = None,
        app_secret: str | None = None,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ) -> None:
        self.http = http
        self.app_id = app_id
        self.app_secret = app_secret
        self._retries = max_retries
        self._backoff = retry_backoff

    async def _request(self, method: str, url: str, *, step: str = "", **kwargs: Any) -> dict[str, Any]:
        async def _do() -> dict[str, Any]:
            resp = await self.http.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableError(f"instagram api {resp.status_code}: {resp.text[:200]}")
            try:
                body = resp.json()
            except ValueError as e:
                raise InstagramError(f"invalid JSON from instagram api: {resp.text[:200]}", step=step) from e
            if resp.status_code >= 400 or "error" in body or "error_message" in body:
                err = body.get("error") or {}
                code = subcode = None
                if isinstance(err, dict) and err:
                    msg = err.get("message") or err.get("error_user_msg")
                    code, subcode = _int(err.get("code")), _int(err.get("error_subcode"))
                elif "error_message" in body:  # api.instagram.com 의 OAuth 오류 형식
                    msg = body.get("error_message")
                    code = _int(body.get("code"))
                else:
                    msg = str(err)
                where = f" ({step})" if step else ""
                detail = f" [code {code}{'/' + str(subcode) if subcode else ''}]" if code is not None else ""
                raise InstagramError(
                    f"instagram api {resp.status_code}{where}: {msg or resp.text[:200]}{detail}", code=code, subcode=subcode, step=step
                )
            return body

        return await retry_async(_do, attempts=self._retries, backoff=self._backoff, label=f"instagram {method} {url}")

    # ------------------------------------------------------------ 인증
    async def exchange_code(self, code: str, redirect_uri: str) -> InstagramToken:
        """OAuth code → 단기 토큰 → 장기 토큰(60일). 계정 ID 는 /me 로 확정한다."""
        if not (self.app_id and self.app_secret):
            raise InstagramError("INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET 이 필요합니다")
        code = code.split("#")[0].strip()
        short = await self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
            step="oauth_code",
        )
        short_token = short.get("access_token")
        if not short_token:
            raise InstagramError(f"단기 토큰 발급 실패: {short}", step="oauth_code")
        long = await self._request(
            "GET",
            f"{GRAPH_BASE}/access_token",
            params={"grant_type": "ig_exchange_token", "client_secret": self.app_secret, "access_token": short_token},
            step="long_token",
        )
        token = long.get("access_token") or short_token
        expires_in = int(long.get("expires_in") or 0)
        me = await self.me(token)
        user_id = str(me.get("user_id") or me.get("id") or short.get("user_id") or "")
        return InstagramToken(
            access_token=token,
            user_id=user_id,
            expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
        )

    async def refresh(self, token: InstagramToken) -> InstagramToken:
        body = await self._request(
            "GET",
            f"{GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": token.access_token},
            step="refresh",
        )
        new_token = body.get("access_token")
        if not new_token:
            raise InstagramError(f"토큰 갱신 실패: {body}", step="refresh")
        expires_in = int(body.get("expires_in") or 0)
        return InstagramToken(
            access_token=new_token,
            user_id=token.user_id,
            expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
        )

    async def me(self, token: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{GRAPH_BASE}/{API_VERSION}/me", params={"fields": "user_id,username", "access_token": token}, step="me"
        )

    async def permalink(self, token: InstagramToken, media_id: str) -> str | None:
        body = await self._request(
            "GET", f"{GRAPH_BASE}/{API_VERSION}/{media_id}", params={"fields": "permalink", "access_token": token.access_token}, step="permalink"
        )
        return str(body.get("permalink") or "") or None

    # ------------------------------------------------------------ 게시
    async def container_status(self, token: InstagramToken, container_id: str) -> tuple[str, str | None]:
        body = await self._request(
            "GET",
            f"{GRAPH_BASE}/{API_VERSION}/{container_id}",
            params={"fields": "status_code,status", "access_token": token.access_token},
            step="container_status",
        )
        return str(body.get("status_code") or ""), body.get("status")

    async def wait_ready(self, token: InstagramToken, container_id: str) -> None:
        deadline = time.monotonic() + CONTAINER_WAIT_SECONDS
        while True:
            try:
                status, detail = await self.container_status(token, container_id)
            except InstagramError as e:
                log.warning("instagram container status check failed (%s) — publishing anyway", e)
                return
            if status in ("", "FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"컨테이너 처리 실패 ({status}): {detail or '사유 없음'}", step="container")
            if time.monotonic() >= deadline:
                raise InstagramError(f"컨테이너 처리 대기 시간 초과 ({CONTAINER_WAIT_SECONDS:.0f}s, 상태 {status})", step="container")
            await asyncio.sleep(CONTAINER_POLL_SECONDS)

    async def post_image(self, token: InstagramToken, image_url: str, caption: str) -> str:
        """사진 한 장 + 본문. 게시된 미디어 id 반환."""
        created = await self._request(
            "POST",
            f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/media",
            params={"image_url": image_url, "caption": caption[:CAPTION_LIMIT], "access_token": token.access_token},
            step="create_container",
        )
        container_id = created.get("id")
        if not container_id:
            raise InstagramError(f"컨테이너 생성 실패: {created}", step="create_container")
        await self.wait_ready(token, str(container_id))
        last: InstagramError | None = None
        delays = PUBLISH_ATTEMPT_DELAYS
        for i, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                published = await self._request(
                    "POST",
                    f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/media_publish",
                    params={"creation_id": container_id, "access_token": token.access_token},
                    step="publish",
                )
                media_id = published.get("id")
                if not media_id:
                    raise InstagramError(f"게시 실패: {published}", step="publish")
                return str(media_id)
            except InstagramError as e:
                last = e
                if not _not_ready(e):
                    raise
                log.warning("instagram publish: not ready yet (%s), attempt %d/%d", e, i + 1, len(delays))
        assert last is not None
        raise last


class InstagramPublisher:
    """딜 하나를 인스타그램에 올린다. 사진은 호출자가 공개 URL(카드)로 준다."""

    def __init__(
        self,
        client: InstagramClient,
        db: Any,
        renderer: TemplateRenderer,
        *,
        registry: ShopRegistry | None = None,
        template: str = "deal_instagram.j2",
        enabled: bool = True,
        dry_run: bool = False,
        refresh_before_days: int = 7,
    ) -> None:
        self.client = client
        self.db = db
        self.renderer = renderer
        self.registry = registry or ShopRegistry()
        self.template = template
        self.enabled = enabled
        self.dry_run = dry_run
        self.refresh_before_days = refresh_before_days

    # ------------------------------------------------------------ 토큰
    def stored_token(self) -> InstagramToken | None:
        token = self.db.kv_get(KV_TOKEN)
        user_id = self.db.kv_get(KV_USER_ID)
        if not token or not user_id:
            return None
        return InstagramToken(access_token=token, user_id=user_id, expires_at=from_iso(self.db.kv_get(KV_TOKEN_EXPIRES)))

    def save_token(self, token: InstagramToken, username: str | None = None) -> None:
        self.db.kv_set(KV_TOKEN, token.access_token)
        self.db.kv_set(KV_USER_ID, token.user_id)
        if token.expires_at:
            self.db.kv_set(KV_TOKEN_EXPIRES, to_iso(token.expires_at))
        if username:
            self.db.kv_set(KV_USERNAME, username)

    @property
    def configured(self) -> bool:
        return self.enabled and self.stored_token() is not None

    async def ensure_fresh(self) -> InstagramToken | None:
        token = self.stored_token()
        if token is None:
            return None
        if token.expires_at and token.expires_at - utcnow() < timedelta(days=self.refresh_before_days):
            try:
                token = await self.client.refresh(token)
                self.save_token(token)
                log.info("instagram token refreshed (expires %s)", token.expires_at)
            except InstagramError as e:
                log.error("instagram token refresh failed: %s", e)
        return token

    # ------------------------------------------------------------ 발행
    def render(self, deal: Deal) -> str:
        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        return self.renderer.render_deal(deal, link, shop=shop, template=self.template, autoescape=False)[:CAPTION_LIMIT]

    async def publish(self, deal: Deal, image_url: str | None) -> PublishResult:
        if not self.enabled:
            return PublishResult(ok=False, error="instagram disabled")
        caption = self.render(deal)
        if self.dry_run:
            log.info("[DRY-RUN] would post to instagram (%s):\n%s", image_url, caption)
            return PublishResult(ok=True, dry_run=True)
        if not image_url:
            return PublishResult(ok=False, error="인스타그램은 사진이 필요합니다 (카드 이미지를 만들지 못함)")
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="instagram not authorized (/instaauth)")
        try:
            media_id = await self.client.post_image(token, image_url, caption)
        except InstagramError as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, message_id=int(media_id) if media_id.isdigit() else None)
