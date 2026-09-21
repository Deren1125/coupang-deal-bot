"""스레드(Threads) 자동 발행.

인증 흐름 (한 번만):
  1) Meta 개발자 앱 생성 → Threads API 사용 사례 추가 → 앱 ID / 시크릿
  2) `dealbot threads-auth` 또는 관리자 챗 `/threadsauth` → 인증 URL 접속 → 승인 →
     리디렉션 주소의 code 값을 봇에 전달 → 장기 토큰(60일) 저장
  3) 봇이 만료 전에 자동 갱신

발행: 컨테이너 생성 → 게시 2단계 (Meta 규격).
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

GRAPH_BASE = "https://graph.threads.net"
AUTH_BASE = "https://threads.com/oauth/authorize"  # 메타 문서 기준 (threads.net 은 여기로 리디렉션됨)
API_VERSION = "v1.0"
SCOPES = "threads_basic,threads_content_publish,threads_manage_replies"  # 링크는 첫 글의 답글로 올리므로 답글 권한도 필요
TEXT_LIMIT = 500

KV_TOKEN = "threads_access_token"
KV_TOKEN_EXPIRES = "threads_token_expires_at"
KV_USER_ID = "threads_user_id"
KV_USERNAME = "threads_username"

# 메타는 컨테이너(특히 사진)를 처리할 시간이 필요하다 — 바로 게시하면 "(#24) The requested resource does not exist" /
# "Media ID is not available" 이 난다. 상태를 물어 FINISHED 가 될 때까지 기다리고, 그래도 안 되면 조금 쉬었다 다시 게시한다.
CONTAINER_WAIT_SECONDS = 90.0
CONTAINER_POLL_SECONDS = 3.0
PUBLISH_ATTEMPT_DELAYS: tuple[float, ...] = (0.0, 5.0, 10.0, 15.0)
REPLY_ATTEMPT_DELAYS: tuple[float, ...] = (0.0, 5.0, 10.0)


class ThreadsError(Exception):
    def __init__(self, message: str, *, code: int | None = None, subcode: int | None = None, step: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.step = step


class ThreadsMediaError(ThreadsError):
    """사진(컨테이너 처리) 쪽 문제. 사진 없이 다시 올려 볼 수 있다."""


def _not_ready(e: ThreadsError) -> bool:
    """컨테이너/글이 아직 준비되지 않았을 때 메타가 주는 오류들."""
    msg = str(e).lower()
    return e.code == 24 or e.subcode == 4279009 or "not available" in msg or "does not exist" in msg


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


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

    async def _request(self, method: str, url: str, *, step: str = "", **kwargs: Any) -> dict[str, Any]:
        async def _do() -> dict[str, Any]:
            resp = await self.http.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise RetryableError(f"threads api {resp.status_code}: {resp.text[:200]}")
            try:
                body = resp.json()
            except ValueError as e:
                raise ThreadsError(f"invalid JSON from threads api: {resp.text[:200]}", step=step) from e
            if resp.status_code >= 400 or "error" in body:
                err = body.get("error") or {}
                code = subcode = None
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("error_user_msg")
                    code, subcode = _int(err.get("code")), _int(err.get("error_subcode"))
                else:
                    msg = str(err)
                where = f" ({step})" if step else ""
                detail = f" [code {code}{'/' + str(subcode) if subcode else ''}]" if code is not None else ""
                raise ThreadsError(
                    f"threads api {resp.status_code}{where}: {msg or resp.text[:200]}{detail}", code=code, subcode=subcode, step=step
                )
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
    async def container_status(self, token: ThreadsToken, creation_id: str) -> tuple[str, str | None]:
        body = await self._request(
            "GET",
            f"{GRAPH_BASE}/{API_VERSION}/{creation_id}",
            params={"fields": "status,error_message", "access_token": token.access_token},
            step="container_status",
        )
        return str(body.get("status") or ""), body.get("error_message")

    async def wait_ready(
        self, token: ThreadsToken, creation_id: str, *, timeout: float | None = None, interval: float | None = None
    ) -> None:
        """컨테이너가 FINISHED 가 될 때까지 기다린다. ERROR/EXPIRED/시간 초과면 ThreadsMediaError."""
        timeout = CONTAINER_WAIT_SECONDS if timeout is None else timeout
        interval = CONTAINER_POLL_SECONDS if interval is None else interval
        deadline = time.monotonic() + timeout
        while True:
            try:
                status, err = await self.container_status(token, creation_id)
            except ThreadsError as e:
                log.warning("threads container status check failed (%s) — publishing anyway", e)
                return
            if status in ("", "FINISHED", "PUBLISHED"):  # 상태를 안 주면 바로 시도
                return
            if status in ("ERROR", "EXPIRED"):
                raise ThreadsMediaError(f"컨테이너 처리 실패 ({status}): {err or '사유 없음'}", step="container")
            if time.monotonic() >= deadline:
                raise ThreadsMediaError(f"컨테이너 처리 대기 시간 초과 ({timeout:.0f}s, 상태 {status})", step="container")
            await asyncio.sleep(interval)

    async def post(self, token: ThreadsToken, text: str, image_url: str | None = None, *, reply_to_id: str | None = None) -> str:
        """컨테이너 생성 → 처리 대기 → 게시. 게시된 글 id 반환. reply_to_id 를 주면 그 글의 답글로 올린다.

        사진이 문제면(다운로드 실패, 처리 오류, 준비 안 됨) ThreadsMediaError 를 던져 호출자가 사진 없이 다시 올릴 수 있게 한다.
        """
        params: dict[str, Any] = {"text": text[:TEXT_LIMIT], "access_token": token.access_token}
        if image_url:
            params["media_type"] = "IMAGE"
            params["image_url"] = image_url
        else:
            params["media_type"] = "TEXT"
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        try:
            created = await self._request(
                "POST", f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/threads", params=params, step="create_container"
            )
        except ThreadsMediaError:
            raise
        except ThreadsError as e:
            if image_url:
                raise ThreadsMediaError(str(e), code=e.code, subcode=e.subcode, step=e.step) from e
            raise
        creation_id = created.get("id")
        if not creation_id:
            raise ThreadsError(f"컨테이너 생성 실패: {created}", step="create_container")
        await self.wait_ready(token, str(creation_id))

        published: dict[str, Any] | None = None
        last: ThreadsError | None = None
        delays = PUBLISH_ATTEMPT_DELAYS
        for i, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                published = await self._request(
                    "POST",
                    f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/threads_publish",
                    params={"creation_id": creation_id, "access_token": token.access_token},
                    step="publish",
                )
                break
            except ThreadsError as e:
                last = e
                if not _not_ready(e):
                    raise
                log.warning("threads publish: not ready yet (%s), attempt %d/%d", e, i + 1, len(delays))
        if published is None:
            assert last is not None
            if image_url:
                raise ThreadsMediaError(str(last), code=last.code, subcode=last.subcode, step="publish") from last
            raise last
        post_id = published.get("id")
        if not post_id:
            raise ThreadsError(f"게시 실패: {published}", step="publish")
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
        reply_template: str | None = "deal_threads_reply.j2",
        enabled: bool = True,
        dry_run: bool = False,
        refresh_before_days: int = 7,
    ) -> None:
        self.client = client
        self.db = db
        self.renderer = renderer
        # 잘 되는 핫딜 계정들은 첫 글을 짧은 훅으로 쓰고, 링크와 수수료 고지는 답글에 단다.
        # 링크가 든 글은 노출이 줄기도 해서 이 구조가 유리하다. reply_template 을 비우면 한 글로 올린다.
        self.reply_template = reply_template
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

    def render_reply(self, deal: Deal) -> str | None:
        if not self.reply_template:
            return None
        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        return self.renderer.render_deal(deal, link, shop=shop, template=self.reply_template, autoescape=False)

    async def _post_with_fallback(self, token: ThreadsToken, text: str, image_url: str | None) -> tuple[str, str | None]:
        """사진이 있으면 사진과 함께, 사진 쪽이 문제면 사진 없이 다시. (글 id, 참고 메모)"""
        if not image_url:
            return await self.client.post(token, text), None
        try:
            return await self.client.post(token, text, image_url), None
        except ThreadsMediaError as e:
            log.warning("threads image rejected (%s) — posting without photo: %s", image_url, e)
            return await self.client.post(token, text), f"사진 없이 올림: {e}"

    async def _reply(self, token: ThreadsToken, text: str, post_id: str) -> None:
        """방금 올린 글이 아직 조회되지 않아 실패하면 조금 쉬었다 다시."""
        delays = REPLY_ATTEMPT_DELAYS
        for i, delay in enumerate(delays):
            if delay:
                await asyncio.sleep(delay)
            try:
                await self.client.post(token, text, reply_to_id=post_id)
                return
            except ThreadsError as e:
                if i + 1 >= len(delays) or not _not_ready(e):
                    raise
                log.warning("threads reply: parent not ready yet (%s), attempt %d/%d", e, i + 1, len(delays))

    async def publish_text(self, text: str, image_url: str | None = None) -> PublishResult:
        """완성된 평문 한 글(답글 없음). 정보 글 등에 씀."""
        if not self.enabled:
            return PublishResult(ok=False, error="threads disabled")
        text = text[:TEXT_LIMIT]
        if self.dry_run:
            log.info("[DRY-RUN] would post to threads:\n%s", text)
            return PublishResult(ok=True, dry_run=True)
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="threads not authorized (/threadsauth)")
        try:
            post_id, note = await self._post_with_fallback(token, text, image_url)
        except ThreadsError as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, message_id=int(post_id) if post_id.isdigit() else None, error=note)

    async def publish(self, deal: Deal) -> PublishResult:
        if not self.enabled:
            return PublishResult(ok=False, error="threads disabled")
        text = self.render(deal)
        reply = self.render_reply(deal)
        if self.dry_run:
            log.info("[DRY-RUN] would post to threads:\n%s\n--- reply ---\n%s", text, reply or "(없음)")
            return PublishResult(ok=True, dry_run=True)
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="threads not authorized (/threadsauth)")
        try:
            post_id, note = await self._post_with_fallback(token, text, deal.product.image_url)
        except ThreadsError as e:
            return PublishResult(ok=False, error=str(e))
        message_id = int(post_id) if post_id.isdigit() else None
        if reply:
            try:
                await self._reply(token, reply, post_id)
            except ThreadsError as e:
                # 훅은 올라갔으니 실패로 치지 않되, 링크가 빠진 글이 되므로 알린다
                log.warning("threads reply (link) failed for %s: %s", post_id, e)
                return PublishResult(ok=True, message_id=message_id, error=f"답글(링크) 게시 실패: {e}")
        return PublishResult(ok=True, message_id=message_id, error=note)
