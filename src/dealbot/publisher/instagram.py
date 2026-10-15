"""인스타그램 자동 게시 — "Instagram API with Instagram Login" (페이스북 페이지 없이, 프로페셔널 계정만).

흐름은 스레드와 같다: 승인 링크 → code → 단기 토큰 → 장기 토큰(60일, 자동 갱신) → 컨테이너 생성 → 처리 대기 → 게시.
인스타는 사진 없는 글을 못 올리고 JPEG 만 받으므로, 봇이 만든 카드 이미지(공개 도메인 /media/…)를 올린다.
본문 링크는 눌리지 않으니 "프로필 링크" 로 유도하고, 수수료 고지는 본문 첫 줄과 카드 안에 넣는다 (insta_style.validate 로 확인).
- 캐러셀(공식 흐름): 장마다 is_carousel_item=true 컨테이너(대체 텍스트 alt_text) → media_type=CAROUSEL 컨테이너(children) → 게시. 10장까지.
- 릴스: media_type=REELS 컨테이너(video_url·cover_url·share_to_feed) → 처리 끝(FINISHED)까지 기다림 → 게시.
- 게시 한도는 24시간에 100개 (봇은 하루 6개 안팎).
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

CHECK_FAILED = "올리기 전 검사 실패"  # PublishResult.error 머리말 — 실패 알림이 아니라 건너뜀 기록으로
REEL_TIMEOUT = "timeout"

KV_TOKEN = "instagram_access_token"
KV_TOKEN_EXPIRES = "instagram_token_expires_at"
KV_USER_ID = "instagram_user_id"
KV_USERNAME = "instagram_username"

CONTAINER_WAIT_SECONDS = 120.0
CONTAINER_POLL_SECONDS = 4.0
PUBLISH_ATTEMPT_DELAYS: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0)
REEL_POLL_SECONDS = 10.0  # 릴스 영상 처리 상태를 보는 간격
CAROUSEL_MAX = 10
ALT_TEXT_LIMIT = 1000


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

    async def from_user_token(self, token: str) -> InstagramToken:
        """메타 개발자 화면(Instagram API 설정)의 '액세스 토큰 생성'으로 만든 토큰을 그대로 받는다 (웹 로그인이 돌기만 할 때 — 주인 10/12).
        단기면 장기(60일)로 바꾸고, 이미 장기면 그대로. 만료일을 모르면 59일로 잡아 자동 갱신이 돌게."""
        token = token.strip()
        expires_in = 0
        if self.app_secret:
            try:
                long = await self._request(
                    "GET",
                    f"{GRAPH_BASE}/access_token",
                    params={"grant_type": "ig_exchange_token", "client_secret": self.app_secret, "access_token": token},
                    step="long_token",
                )
                token = long.get("access_token") or token
                expires_in = int(long.get("expires_in") or 0)
            except InstagramError as e:  # 이미 장기 토큰이면 바꿀 수 없다고 나옴 → 그대로 씀
                log.info("instagram user token: long-lived exchange skipped (%s)", e)
        me = await self.me(token)  # 토큰이 진짜인지 여기서 확인 (틀리면 InstagramError)
        return InstagramToken(
            access_token=token,
            user_id=str(me.get("user_id") or me.get("id") or ""),
            expires_at=utcnow() + (timedelta(seconds=expires_in) if expires_in else timedelta(days=59)),
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

    # ------------------------------------------------------------ 대시보드 (읽기만, 공식 Graph API)
    async def account_stats(self, token: InstagramToken) -> dict[str, Any]:
        """팔로워 수·게시물 수 (instagram_business_basic 권한으로 읽힘)."""
        return await self._request(
            "GET",
            f"{GRAPH_BASE}/{API_VERSION}/me",
            params={"fields": "user_id,username,followers_count,media_count", "access_token": token.access_token},
            step="account_stats",
        )

    async def account_insights(self, token: InstagramToken, metrics: str, *, since: int, until: int) -> dict[str, Any]:
        """계정 인사이트 합계 (views·reach 등). instagram_business_manage_insights 권한이 없으면 InstagramError."""
        return await self._request(
            "GET",
            f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/insights",
            params={
                "metric": metrics, "period": "day", "metric_type": "total_value", "since": since, "until": until,
                "access_token": token.access_token,
            },
            step="insights",
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

    async def wait_finished(self, token: InstagramToken, container_id: str, *, timeout: float, poll: float | None = None) -> bool:
        """릴스: 영상 처리가 끝날(FINISHED) 때까지 기다린다. 시간이 넘으면 False (조용히 포기), ERROR·EXPIRED 면 InstagramError.
        상태 확인 자체가 실패해도 다시 본다 (영상 처리는 몇 분 걸릴 수 있음)."""
        deadline = time.monotonic() + timeout
        poll = REEL_POLL_SECONDS if poll is None else poll
        while True:
            try:
                status, detail = await self.container_status(token, container_id)
            except InstagramError as e:
                log.info("instagram reel status check failed (%s) — retrying", e)
                status, detail = "", None
            if status in ("FINISHED", "PUBLISHED"):
                return True
            if status in ("ERROR", "EXPIRED"):
                raise InstagramError(f"릴스 영상 처리 실패 ({status}): {detail or '사유 없음'}", step="container")
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(poll)

    async def _create(self, token: InstagramToken, params: dict[str, Any], *, step: str = "create_container") -> str:
        created = await self._request(
            "POST", f"{GRAPH_BASE}/{API_VERSION}/{token.user_id}/media",
            params={**params, "access_token": token.access_token}, step=step,
        )
        container_id = created.get("id")
        if not container_id:
            raise InstagramError(f"컨테이너 생성 실패: {created}", step=step)
        return str(container_id)

    async def post_image(self, token: InstagramToken, image_url: str, caption: str, *, alt_text: str | None = None) -> str:
        """사진 한 장 + 본문. 게시된 미디어 id 반환. alt_text: 대체 텍스트 (사진을 못 보는 사람·검색용)."""
        params: dict[str, Any] = {"image_url": image_url, "caption": caption[:CAPTION_LIMIT]}
        if alt_text:
            params["alt_text"] = alt_text[:ALT_TEXT_LIMIT]
        container_id = await self._create(token, params)
        await self.wait_ready(token, container_id)
        return await self._publish(token, container_id)

    async def post_carousel(self, token: InstagramToken, images: list[tuple[str, str]], caption: str) -> str:
        """캐러셀(공식 흐름): 장마다 is_carousel_item 컨테이너 → CAROUSEL 컨테이너(children) → 게시. images: (사진 주소, 대체 텍스트)."""
        if not 2 <= len(images) <= CAROUSEL_MAX:
            raise InstagramError(f"캐러셀은 2~{CAROUSEL_MAX}장이어야 합니다 ({len(images)}장)", step="carousel")
        children: list[str] = []
        for url, alt in images:
            params: dict[str, Any] = {"image_url": url, "is_carousel_item": "true"}
            if alt:
                params["alt_text"] = alt[:ALT_TEXT_LIMIT]
            children.append(await self._create(token, params, step="carousel_item"))
        for child in children:
            await self.wait_ready(token, child)
        parent = await self._create(
            token, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption[:CAPTION_LIMIT]}, step="carousel",
        )
        await self.wait_ready(token, parent)
        return await self._publish(token, parent)

    async def post_reel(self, token: InstagramToken, video_url: str, caption: str, *, cover_url: str | None = None,
                        timeout: float = 300.0, poll: float | None = None) -> str | None:
        """릴스: REELS 컨테이너 → 처리 끝(FINISHED)까지 기다림 → 게시. 기다리는 시간이 넘으면 None (게시하지 않음)."""
        params: dict[str, Any] = {"media_type": "REELS", "video_url": video_url, "caption": caption[:CAPTION_LIMIT],
                                  "share_to_feed": "true"}
        if cover_url:
            params["cover_url"] = cover_url
        container_id = await self._create(token, params, step="reel")
        if not await self.wait_finished(token, container_id, timeout=timeout, poll=poll):
            log.info("instagram reel container %s not finished within %.0fs — giving up", container_id, timeout)
            return None
        return await self._publish(token, container_id)

    async def _publish(self, token: InstagramToken, container_id: str) -> str:
        """처리된 컨테이너를 게시한다 (아직 준비 안 됨이면 조금씩 기다렸다 다시)."""
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
    """인스타그램에 올린다: 딜 하나(카드 사진 + 본문), 캐러셀, 릴스. 사진·영상은 호출자가 공개 URL 로 준다.
    언제 무엇을 올릴지는 publisher/ig_scheduler (인스타 대기열·하루 시간표)가 정한다."""

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
        style: str = "current",
    ) -> None:
        self.client = client
        self.db = db
        self.renderer = renderer
        self.registry = registry or ShopRegistry()
        self.template = template
        self.enabled = enabled
        self.dry_run = dry_run
        self.refresh_before_days = refresh_before_days
        self.style = style  # current | A | B | C (config instagram.style)

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
    def render(self, deal: Deal, *, deal_no: int | None = None, style: str | None = None) -> str:
        """본문. style 이 A/B/C 면 후보 양식 템플릿(deal_instagram_a/b/c.j2), 아니면 config 의 template.
        deal_no: 핫딜 페이지의 번호 (프로필 링크에서 찾는 표시, 후보 양식만 씀)."""
        return self._render(deal, deal_no=deal_no, style=style)[0]

    def _render(self, deal: Deal, *, deal_no: int | None = None, style: str | None = None) -> tuple[str, list[str], str]:
        """(본문, 검사에서 허용할 데이터 근거 말, 대체 텍스트)."""
        from dealbot.media.card_slides import alt_text
        from dealbot.publisher import insta_style

        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        style = style or self.style
        template = insta_style.template_for(style, self.template)
        info = insta_style.card_info(deal, self.renderer, shop=shop, deal_no=deal_no)
        extra = None
        if template != self.template:
            extra = {"ig": insta_style.caption_context(deal, info, shop=shop)}
        text = self.renderer.render_deal(deal, link, shop=shop, template=template, autoescape=False, extra=extra)[:CAPTION_LIMIT]
        return text, insta_style.backed_phrases(info), alt_text(info)

    def check(self, deal: Deal, *, deal_no: int | None = None) -> list[str]:
        """올리기 전 검사 (첫 줄 고지·근거 없는 '최저가'·'역대'). 문제 목록."""
        from dealbot.publisher import insta_style

        caption, allowed, _ = self._render(deal, deal_no=deal_no)
        return insta_style.validate(caption, allowed=allowed)

    async def publish(self, deal: Deal, image_url: str | None, *, deal_no: int | None = None) -> PublishResult:
        from dealbot.publisher import insta_style

        if not self.enabled:
            return PublishResult(ok=False, error="instagram disabled")
        caption, allowed, alt = self._render(deal, deal_no=deal_no)
        problems = insta_style.validate(caption, allowed=allowed)
        if problems:
            return PublishResult(ok=False, error=f"{CHECK_FAILED}: {'; '.join(problems)}")
        if self.dry_run:
            log.info("[DRY-RUN] would post to instagram (%s):\n%s", image_url, caption)
            return PublishResult(ok=True, dry_run=True)
        if not image_url:
            return PublishResult(ok=False, error="인스타그램은 사진이 필요합니다 (카드 이미지를 만들지 못함)")
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="instagram not authorized (/instaauth)")
        try:
            media_id = await self.client.post_image(token, image_url, caption, alt_text=alt)
        except InstagramError as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, message_id=int(media_id) if media_id.isdigit() else None)

    async def publish_carousel(self, images: list[tuple[str, str]], caption: str) -> PublishResult:
        """캐러셀 (사진 주소·대체 텍스트 목록). 본문·장 검사는 부르는 쪽(ig_scheduler)이 장 내용과 함께 먼저 한다."""
        if not self.enabled:
            return PublishResult(ok=False, error="instagram disabled")
        if self.dry_run:
            log.info("[DRY-RUN] would post an instagram carousel (%d images):\n%s", len(images), caption)
            return PublishResult(ok=True, dry_run=True)
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="instagram not authorized (/instaauth)")
        try:
            media_id = await self.client.post_carousel(token, images, caption)
        except InstagramError as e:
            return PublishResult(ok=False, error=str(e))
        return PublishResult(ok=True, message_id=int(media_id) if media_id.isdigit() else None)

    async def publish_reel(self, video_url: str, caption: str, *, cover_url: str | None = None, timeout: float = 300.0,
                           poll: float | None = None) -> PublishResult:
        """릴스. 영상 처리가 timeout 안에 안 끝나면 ok=False, error='timeout' (알림 없이 기록만 — 부르는 쪽)."""
        if not self.enabled:
            return PublishResult(ok=False, error="instagram disabled")
        if self.dry_run:
            log.info("[DRY-RUN] would post an instagram reel (%s):\n%s", video_url, caption)
            return PublishResult(ok=True, dry_run=True)
        token = await self.ensure_fresh()
        if token is None:
            return PublishResult(ok=False, error="instagram not authorized (/instaauth)")
        try:
            media_id = await self.client.post_reel(token, video_url, caption, cover_url=cover_url, timeout=timeout, poll=poll)
        except InstagramError as e:
            return PublishResult(ok=False, error=str(e))
        if media_id is None:
            return PublishResult(ok=False, error=REEL_TIMEOUT)
        return PublishResult(ok=True, message_id=int(media_id) if media_id.isdigit() else None)
