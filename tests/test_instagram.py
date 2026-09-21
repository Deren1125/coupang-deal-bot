"""인스타그램(Instagram API with Instagram Login) 클라이언트·발행기."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dealbot.cli import sample_deal
from dealbot.publisher import instagram as ig
from dealbot.publisher.instagram import (
    InstagramClient,
    InstagramError,
    InstagramPublisher,
    InstagramToken,
    authorize_url,
)
from dealbot.publisher.templates import TemplateRenderer
from dealbot.storage.db import Database


def _client(handler) -> InstagramClient:  # type: ignore[no-untyped-def]
    return InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="IGAPP", app_secret="IGSECRET", retry_backoff=0.01)


def test_authorize_url() -> None:
    url = authorize_url("IGAPP", "https://bot.up.railway.app/instagram/callback", state="s1")
    assert url.startswith("https://www.instagram.com/oauth/authorize?")
    assert "client_id=IGAPP" in url and "instagram_business_content_publish" in url and "state=s1" in url


async def test_exchange_code_resolves_account_id_and_refreshes() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.url.host}{req.url.path}")
        if req.url.host == "api.instagram.com" and req.url.path == "/oauth/access_token":
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            assert body["code"] == "ABC" and body["client_secret"] == "IGSECRET" and body["grant_type"] == "authorization_code"
            return httpx.Response(200, json={"access_token": "SHORT", "user_id": 111, "permissions": ["instagram_business_basic"]})
        if req.url.path == "/access_token":
            assert req.url.params["grant_type"] == "ig_exchange_token"
            return httpx.Response(200, json={"access_token": "LONG", "token_type": "bearer", "expires_in": 5184000})
        if req.url.path == "/refresh_access_token":
            assert req.url.params["grant_type"] == "ig_refresh_token"
            return httpx.Response(200, json={"access_token": "LONG2", "expires_in": 5184000})
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"user_id": "17841400000", "username": "oneul_hot_deal"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    c = _client(handler)
    token = await c.exchange_code("ABC#_", "https://bot.up.railway.app/instagram/callback")
    assert token.access_token == "LONG" and token.user_id == "17841400000" and token.expires_at is not None
    refreshed = await c.refresh(token)
    assert refreshed.access_token == "LONG2" and refreshed.user_id == "17841400000"


async def test_oauth_error_format_is_readable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error_type": "OAuthException", "code": 400, "error_message": "Invalid platform app"})

    with pytest.raises(InstagramError, match="Invalid platform app"):
        await _client(handler).exchange_code("X", "https://x/cb")


async def test_post_image_waits_for_container_then_retries_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ig, "CONTAINER_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ig, "PUBLISH_ATTEMPT_DELAYS", (0.0, 0.01, 0.01))
    status_calls = {"n": 0}
    publish_calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v25.0/UID/media":
            assert req.url.params["image_url"] == "https://bot.up.railway.app/media/abc.jpg" and "캡션" in req.url.params["caption"]
            return httpx.Response(200, json={"id": "C9"})
        if req.url.path == "/v25.0/C9":
            status_calls["n"] += 1
            return httpx.Response(200, json={"status_code": "IN_PROGRESS" if status_calls["n"] < 2 else "FINISHED"})
        if req.url.path == "/v25.0/UID/media_publish":
            publish_calls["n"] += 1
            if publish_calls["n"] == 1:
                return httpx.Response(400, json={"error": {"message": "Media is not ready", "code": 9007, "error_subcode": 2207027}})
            return httpx.Response(200, json={"id": "M9"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    media_id = await _client(handler).post_image(InstagramToken("T", "UID"), "https://bot.up.railway.app/media/abc.jpg", "캡션")
    assert media_id == "M9" and status_calls["n"] == 2 and publish_calls["n"] == 2


async def test_container_error_surfaces_with_step() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"status_code": "ERROR", "status": "Media download failed"})

    with pytest.raises(InstagramError) as ei:
        await _client(handler).post_image(InstagramToken("T", "UID"), "https://x/a.jpg", "c")
    assert ei.value.step == "container" and "Media download failed" in str(ei.value)


async def test_publisher_needs_token_and_image(db: Database, repo_root: Path) -> None:
    pub = InstagramPublisher(_client(lambda req: httpx.Response(200, json={"id": "X"})), db, TemplateRenderer(repo_root / "templates"), dry_run=True)
    r = await pub.publish(sample_deal(), "https://x/card.jpg")
    assert r.ok and r.dry_run

    pub2 = InstagramPublisher(_client(lambda req: httpx.Response(200, json={"id": "X"})), db, TemplateRenderer(repo_root / "templates"))
    assert not pub2.configured
    assert (await pub2.publish(sample_deal(), None)).error == "인스타그램은 사진이 필요합니다 (카드 이미지를 만들지 못함)"
    assert "/instaauth" in ((await pub2.publish(sample_deal(), "https://x/card.jpg")).error or "")

    pub2.save_token(InstagramToken("T", "UID"), "oneul")
    assert pub2.configured and db.kv_get(ig.KV_USERNAME) == "oneul"
    caption = pub2.render(sample_deal())
    assert "프로필 링크" in caption and "쿠팡 파트너스" in caption and "#핫딜" in caption
    r = await pub2.publish(sample_deal(), "https://x/card.jpg")
    assert r.ok and r.message_id is None  # "X" 는 숫자가 아니라 id 없음
