"""스레드(Threads) 자동 발행.

인증 흐름 (한 번만):
  1) Meta 개발자 앱 생성 → Threads API 사용 사례 추가 → 앱 ID / 시크릿
  2) `dealbot threads-auth` 또는 관리자 챗 `/threadsauth` → 인증 URL 접속 → 승인 →
     리디렉션 주소의 code 값을 봇에 전달 → 장기 토큰(60일) 저장
  3) 봇이 만료 전에 자동 갱신

발행: 컨테이너 생성 → 게시 2단계 (Meta 규격).
"""

from __future__ import annotations

import logging
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

GRAPH_BASE = "https://graph.threads.net"
AUTH_BASE = "https://threads.net/oauth/authorize"
API_VERSION = "v1.0"
SCOPES = "threads_basic,threads_content_publish"
TEXT_LIMIT = 500

KV_TOKEN = "threads_access_token"
KV_TOKEN_EXPIRES = "threads_token_expires_at"
KV_USER_ID = "threads_user_id"


class ThreadsError(Exception):
    pass


@dataclass(slots=True)
class ThreadsToken:
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
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


class ThreadsClient:
    """토큰 발급·갱신·게시를 담당. 토큰은 DB(kv)에 저장해 재시작해도 유지."""

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

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        async def _do() -> dict[str, Any]:
            resp = await self.http.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableError(f"threads api {resp.status_code}: {resp.text[:200]}")
            try:
                body = resp.json()
            except ValueError as e:
                raise ThreadsError(f"invalid JSON from threads api: {resp.text[:200]}") from e
            if resp.status_code >= 400 or "error" in body:
                err = body.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(err)
                raise ThreadsError(f"threads api {resp.status_code}: {msg or resp.text[:200]}")
            return body

        return await retry_async(_do, attempts=self._retries, backoff=self._backoff, label=f"threads {method} {url}")

    # ------------------------------------------------------------ 인증
    async def exchange_code(self, code: str, redirect_uri: str) -> ThreadsToken:
        """OAuth code → 단기 토큰 → 장기 토큰(60일)."""
        if not (self.app_id and self.app_secret):
            raise ThreadsError("THREADS_APP_ID / THREADS_APP_SECRET 이 필요합니다")
        code = code.split("#")[0].strip()  # 리디렉션 URL 에 붙는 #_ 제거
        short = await self._request(
            "POST",
            f"{GRAPH_BASE}/oauth/access_token",
            data={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        short_token = short.get("access_token")
        user_id = str(short.get("user_id") or "")
        if not short_token:
            raise ThreadsError(f"단기 토큰 발급 실패: {short}")
        long = await self._request(
            "GET",
            f"{GRAPH_BASE}/access_token",
            params={
                "grant_type": "th_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_token,
            },
        )
        token = long.get("access_token") or short_token
        expires_in = int(long.get("expires_in") or 0)
        if not user_id:
            me = await self._request("GET", f"{GRAPH_BASE}/{API_VERSION}/me", params={"fields": "id,username", "access_token": token})
            user_id = str(me.get("id") or "")
        return ThreadsToken(
            access_token=token,
            user_id=user_id,
            expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
        )

    async def refresh(self, token: str) -> ThreadsToken:
        body = await self._request(
            "GET", f"{GRAPH_BASE}/refresh_access_token", params={"grant_type": "th_refresh_token", "access_token": token}
        )
        new_token = body.get("access_token")
        if not new_token:
            raise ThreadsError(f"토큰 갱신 실패: {body}")
        expires_in = int(body.get("expires_in") or 0)
        me = await self._request("GET", f"{GRAPH_BASE}/{API_VERSION}/me", params={"fields": "id", "access_token": new_token})
        return ThreadsToken(
            access_token=new_token,
            user_id=str(me.get("id") or ""),
            expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
        )

    async def me(self, token: str) -> dict[str, Any]:
        return await self._request(
            "GET", f"{GRAPH_BASE}/{API_VERSION}/me", params={"fields": "id,username", "access_token": token}
        )

    # ------------------------------------------------------------ 게시
    async def post(self, token: ThreadsToken, text: str, image_url: str | None = None) -> str:
        """컨테이너 생성 → 게시. 게시된 글 id 반환."""
        params: dict[str, Any] = {"text": text[:TEXT_LIMIT], "access_token": token.access_token}
        if image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"
        created = await self._request("POST", f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/threads", params=params)
        creation_id = created.get("id")
        if not creation_id:
            raise ThreadsError(f"컨테이너 생성 실패: {created}")
        published = await self._request(
            "POST",
            f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/threads_publish",
            params={"creation_id": creation_id, "access_token": token.access_token},
        )
        post_id = published.get("id")
        if not post_id:
            raise ThreadsError(f"게시 실패: {published}")
        return str(post_id)


class ThreadsPublisher:
    """딜 하나를 스레드에 올린다. 토큰은 db.kv 에서 읽고 만료 전 자동 갱신."""

    def __init__(
        self,
        client: ThreadsClient,
        db: Any,
        renderer: TemplateRenderer,
        *,
        registry: ShopRegistry | None = None,
        template: str = "deal_threads.j2",
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
    def stored_token(self) -> ThreadsToken | None:
        token = self.db.kv_get(KV_TOKEN)
        user_id = self.db.kv_get(KV_USER_ID)
        if not token or not user_id:
            return None
        return ThreadsToken(access_token=token, user_id=user_id, expires_at=from_iso(self.db.kv_get(KV_TOKEN_EXPIRES)))

    def save_token(self, token: ThreadsToken) -> None:
        self.db.kv_set(KV_TOKEN, token.access_token)
        self.db.kv_set(KV_USER_ID, token.user_id)
        if token.expires_at:
            self.db.kv_set(KV_TOKEN_EXPIRES, to_iso(token.expires_at))

    @property
    def configured(self) -> bool:
        return self.enabled and self.stored_token() is not None

    async def ensure_fresh(self) -> ThreadsToken | None:
        token = self.stored_token()
        if token is None:
            return None
        if token.expires_at and token.expires_at - utcnow() < timedelta(days=self.refresh_before_days):
            try:
                token = await self.client.refresh(token.access_token)
                self.save_token(token)
                log.info("threads token refreshed (expires %s)", token.expires_at)
            except ThreadsError as e:
                log.error("threads token refresh failed: %s", e)
        return token

    # ------------------------------------------------------------ 발행
    def render(self, deal: Deal) -> str:
        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        return self.renderer.render_deal(deal, link, shop=shop, template=self.template, autoescape=False)[:TEXT_LIMIT]

    async def publish(self, deal: Deal) -> PublishResult:
        if not self.enabled:
            return PublishResult(ok=False, error="threads disabled")
        text = self.render(deal)
        if self.dry_run:
            log.info("[DRY-RUN] would post to threads:\n%s", text)
            return PublishResult(ok=True, dry_run=True)
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="threads not authorized (/threadsauth)")
        try:
            post_id = await self.client.post(token, text, deal.product.image_url)
            return PublishResult(ok=True, message_id=int(post_id) if post_id.isdigit() else None)
        except ThreadsError as e:
            return PublishResult(ok=False, error=str(e))
