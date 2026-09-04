from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealbot.cli import sample_deal
from dealbot.config import CopyConfig, CopyTarget, Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.copyblocks import CopyBlockBuilder
from dealbot.publisher.templates import TemplateRenderer
from dealbot.publisher.threads import (
    KV_TOKEN,
    KV_TOKEN_EXPIRES,
    KV_USER_ID,
    ThreadsClient,
    ThreadsError,
    ThreadsPublisher,
    ThreadsToken,
    authorize_url,
)
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database
from dealbot.utils.timeutil import to_iso, utcnow


def test_authorize_url() -> None:
    url = authorize_url("APPID", "https://localhost/callback")
    assert url.startswith("https://threads.net/oauth/authorize?")
    assert "client_id=APPID" in url and "threads_content_publish" in url


def _client(handler) -> ThreadsClient:  # type: ignore[no-untyped-def]
    return ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="APPID", app_secret="SECRET", retry_backoff=0.01)


async def test_exchange_code_and_refresh() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/oauth/access_token":
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            assert body["code"] == "ABC" and body["client_secret"] == "SECRET"
            return httpx.Response(200, json={"access_token": "SHORT", "user_id": "999"})
        if req.url.path == "/access_token":
            assert req.url.params["grant_type"] == "th_exchange_token"
            return httpx.Response(200, json={"access_token": "LONG", "expires_in": 5184000})
        if req.url.path == "/refresh_access_token":
            return httpx.Response(200, json={"access_token": "LONG2", "expires_in": 5184000})
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "999", "username": "hotdeal"})
        return httpx.Response(404, json={"error": {"message": "not found"}})

    c = _client(handler)
    token = await c.exchange_code("ABC#_", "https://localhost/callback")
    assert token.access_token == "LONG" and token.user_id == "999" and token.expires_at is not None
    assert "/oauth/access_token" in calls and "/access_token" in calls

    refreshed = await c.refresh("LONG")
    assert refreshed.access_token == "LONG2" and refreshed.user_id == "999"


async def test_post_two_step() -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"path": req.url.path, **dict(req.url.params)})
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "CONTAINER1"})
        if req.url.path.endswith("/threads_publish"):
            assert req.url.params["creation_id"] == "CONTAINER1"
            return httpx.Response(200, json={"id": "POST42"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    c = _client(handler)
    post_id = await c.post(ThreadsToken("TOKEN", "999"), "안녕", image_url="https://img/x.jpg")
    assert post_id == "POST42"
    assert seen[0]["media_type"] == "IMAGE" and seen[0]["image_url"] == "https://img/x.jpg"
    assert seen[0]["text"] == "안녕"

    await c.post(ThreadsToken("TOKEN", "999"), "텍스트만")
    assert seen[2]["media_type"] == "TEXT"


async def test_post_error_surfaces() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid image URL"}})

    with pytest.raises(ThreadsError, match="Invalid image URL"):
        await _client(handler).post(ThreadsToken("T", "1"), "x")


def _publisher(db: Database, repo_root: Path, handler=None, **kw):  # type: ignore[no-untyped-def]
    handler = handler or (lambda req: httpx.Response(200, json={"id": "POST1"}))
    return ThreadsPublisher(_client(handler), db, TemplateRenderer(repo_root / "templates"), **kw)


async def test_publisher_token_storage_and_refresh(db: Database, repo_root: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/refresh_access_token":
            return httpx.Response(200, json={"access_token": "NEW", "expires_in": 5184000})
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": "999", "username": "u"})
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "C1"})
        return httpx.Response(200, json={"id": "P1"})

    pub = _publisher(db, repo_root, handler)
    assert pub.stored_token() is None and not pub.configured
    assert await pub.ensure_fresh() is None

    # 만료 임박 토큰 → 자동 갱신
    db.kv_set(KV_TOKEN, "OLD")
    db.kv_set(KV_USER_ID, "999")
    db.kv_set(KV_TOKEN_EXPIRES, to_iso(utcnow() + timedelta(days=2)))
    assert pub.configured
    token = await pub.ensure_fresh()
    assert token is not None and token.access_token == "NEW"
    assert db.kv_get(KV_TOKEN) == "NEW"


async def test_publisher_publish_and_dry_run(db: Database, repo_root: Path) -> None:
    pub = _publisher(db, repo_root, dry_run=True)
    result = await pub.publish(sample_deal())
    assert result.ok and result.dry_run

    pub2 = _publisher(db, repo_root)
    assert (await pub2.publish(sample_deal())).error == "threads not authorized (/threadsauth)"

    db.kv_set(KV_TOKEN, "T")
    db.kv_set(KV_USER_ID, "999")
    ok = await pub2.publish(sample_deal())
    assert ok.ok and not ok.dry_run

    pub3 = _publisher(db, repo_root, enabled=False)
    assert not (await pub3.publish(sample_deal())).ok


def test_threads_template_within_limit(repo_root: Path) -> None:
    r = TemplateRenderer(repo_root / "templates")
    text = r.render_deal(sample_deal(), "https://link.coupang.com/a/x", shop=ShopRegistry().get("coupang"), template="deal_threads.j2")
    assert len(text) <= 500
    assert "<b>" not in text and "<s>" not in text  # 스레드는 평문
    assert "29,900원" in text and "https://link.coupang.com/a/x" in text
    assert "&" not in text.replace("&", "&", 1) or "&amp;" not in text
    assert text.endswith("이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.")


def test_copy_blocks(repo_root: Path) -> None:
    renderer = TemplateRenderer(repo_root / "templates")
    builder = CopyBlockBuilder(CopyConfig(), renderer, ShopRegistry())
    assert builder.enabled
    blocks = builder.build(sample_deal())
    assert [b.key for b in blocks] == ["kakao", "blog"]

    kakao = blocks[0]
    assert "🔥" in kakao.text and "29,900원" in kakao.text and "<b>" not in kakao.text
    assert "<pre>" in kakao.as_telegram_html() and "카카오 오픈채팅" in kakao.as_telegram_html()

    blog = blocks[1]
    first_line = blog.text.splitlines()[0]
    assert first_line.startswith("[쿠팡]") and "29,900원" in first_line
    assert "구매 링크: https://link.coupang.com/a/sample" in blog.text

    assert [b.key for b in builder.build(sample_deal(), only="kakao")] == ["kakao"]
    off = CopyBlockBuilder(CopyConfig(enabled=False), renderer)
    assert off.build(sample_deal()) == [] and not off.enabled
    one = CopyBlockBuilder(CopyConfig(targets=[CopyTarget(key="k", name="n", template="deal_kakao.j2", enabled=False)]), renderer)
    assert not one.enabled and one.build(sample_deal()) == []


def test_copy_blocks_escape_html(repo_root: Path) -> None:
    renderer = TemplateRenderer(repo_root / "templates")
    p = Product(source="s", product_id="toss:1", shop="toss", name="<b>싼</b> & 좋은", price=1000, url="https://toss.im/_m/1")
    block = CopyBlockBuilder(CopyConfig(), renderer).build(Deal(product=p, verdict=DealVerdict(is_deal=True)))[0]
    # 복사되는 본문은 평문 그대로 (이스케이프 문자가 섞이면 안 됨)
    assert "<b>싼</b> & 좋은" in block.text and "&lt;" not in block.text and "&amp;" not in block.text
    # 텔레그램으로 보낼 때만 <pre> 안에서 이스케이프
    assert "&lt;b&gt;싼&lt;/b&gt; &amp; 좋은" in block.as_telegram_html()


async def test_pipeline_sends_threads_and_copy(settings: Settings) -> None:
    from dealbot.app import DealBot
    from dealbot.collectors import BaseCollector, register
    from dealbot.config import CollectorConfig

    @register("fake_side")
    class FakeSide(BaseCollector):
        products: list[Product] = []

        async def collect(self) -> list[Product]:
            return list(FakeSide.products)

    settings.collectors = [CollectorConfig(name="fake", type="fake_side", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    bot = DealBot(settings)
    sent: list[str] = []
    posted: list[dict[str, str]] = []

    async def fake_send(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    def handler(req: httpx.Request) -> httpx.Response:
        posted.append({"path": req.url.path, **dict(req.url.params)})
        return httpx.Response(200, json={"id": "C1" if req.url.path.endswith("/threads") else "P1"})

    bot.notifier.send = fake_send  # type: ignore[method-assign]
    bot.threads.client = _client(handler)
    bot.threads.dry_run = False
    bot.db.kv_set(KV_TOKEN, "T")
    bot.db.kv_set(KV_USER_ID, "999")
    try:
        FakeSide.products = [Product(source="fake", product_id="coupang:1", shop="coupang", name="상품", price=5000,
                                     url="https://www.coupang.com/vp/products/1", affiliate_url="https://link.coupang.com/a/1",
                                     discount_rate=60, rank=1)]
        await bot.run_once()
        assert bot.db.queue_counts() == {"published": 1}
        assert any(p["path"].endswith("/threads_publish") for p in posted)
        copy_msgs = [t for t in sent if "복사용" in t]
        assert len(copy_msgs) == 2 and "카카오 오픈채팅" in copy_msgs[0] and "네이버 블로그" in copy_msgs[1]

        sent.clear()
        msg = await bot.send_copy_blocks()
        assert "카카오 오픈채팅" in msg and len([t for t in sent if "복사용" in t]) == 2
        assert "찾지 못했습니다" in await bot.send_copy_blocks(9999)
    finally:
        await bot.close()


async def test_threads_auth_commands(settings: Settings) -> None:
    from dealbot.app import DealBot

    settings.collectors = []
    bot = DealBot(settings)
    try:
        assert "설정되어 있지 않습니다" in await bot.threads_auth_url()
        settings.secrets.threads_app_id = "APPID"
        settings.secrets.threads_app_secret = "SECRET"
        url_msg = await bot.threads_auth_url()
        assert "threads.net/oauth/authorize" in url_msg and "/threadscode" in url_msg

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/access_token":
                return httpx.Response(200, json={"access_token": "S", "user_id": "999"})
            if req.url.path == "/access_token":
                return httpx.Response(200, json={"access_token": "L", "expires_in": 5184000})
            return httpx.Response(200, json={"id": "999", "username": "hotdeal"})

        bot.threads.client = _client(handler)
        msg = await bot.threads_submit_code("CODE")
        assert "연결 완료" in msg and "@hotdeal" in msg
        assert bot.db.kv_get(KV_TOKEN) == "L"
        assert "이미 연결" in await bot.threads_auth_url()
    finally:
        await bot.close()
