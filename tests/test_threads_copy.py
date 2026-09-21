from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from dealbot.cli import sample_deal
from dealbot.config import CopyConfig, CopyTarget, Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult
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
    assert url.startswith("https://threads.com/oauth/authorize?")
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
        seen.append({"method": req.method, "path": req.url.path, **dict(req.url.params)})
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "CONTAINER1"})
        if req.url.path.endswith("/CONTAINER1"):
            return httpx.Response(200, json={"id": "CONTAINER1", "status": "FINISHED"})
        if req.url.path.endswith("/threads_publish"):
            assert req.url.params["creation_id"] == "CONTAINER1"
            return httpx.Response(200, json={"id": "POST42"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    c = _client(handler)
    post_id = await c.post(ThreadsToken("TOKEN", "999"), "안녕", image_url="https://img/x.jpg")
    assert post_id == "POST42"
    assert [x["path"].rsplit("/", 1)[1] for x in seen] == ["threads", "CONTAINER1", "threads_publish"]
    assert seen[0]["media_type"] == "IMAGE" and seen[0]["image_url"] == "https://img/x.jpg"
    assert seen[0]["text"] == "안녕"
    assert seen[1]["method"] == "GET" and seen[1]["fields"] == "status,error_message"

    await c.post(ThreadsToken("TOKEN", "999"), "텍스트만")
    assert seen[3]["media_type"] == "TEXT"


async def test_post_waits_for_container_then_retries_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """사진 컨테이너는 처리 시간이 필요: IN_PROGRESS → FINISHED 를 기다리고, 게시가 '(#24) 리소스 없음' 이면 잠시 후 다시."""
    import dealbot.publisher.threads as th

    monkeypatch.setattr(th, "CONTAINER_POLL_SECONDS", 0.01)
    monkeypatch.setattr(th, "PUBLISH_ATTEMPT_DELAYS", (0.0, 0.01, 0.01))
    status_calls = {"n": 0}
    publish_calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "C7"})
        if req.url.path.endswith("/C7"):
            status_calls["n"] += 1
            return httpx.Response(200, json={"status": "IN_PROGRESS" if status_calls["n"] < 3 else "FINISHED"})
        if req.url.path.endswith("/threads_publish"):
            publish_calls["n"] += 1
            if publish_calls["n"] == 1:
                return httpx.Response(
                    400,
                    json={"error": {"message": "The requested resource does not exist", "type": "THApiException", "code": 24}},
                )
            return httpx.Response(200, json={"id": "P7"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    post_id = await _client(handler).post(ThreadsToken("T", "1"), "x", image_url="https://img/y.jpg")
    assert post_id == "P7" and status_calls["n"] == 3 and publish_calls["n"] == 2


async def test_post_error_carries_code() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "Media ID is not available", "code": 24, "error_subcode": 4279009}}
        )

    with pytest.raises(ThreadsError) as ei:
        await _client(handler).post(ThreadsToken("T", "1"), "x")
    assert ei.value.code == 24 and ei.value.subcode == 4279009 and ei.value.step == "create_container"
    assert "[code 24/4279009]" in str(ei.value) and "(create_container)" in str(ei.value)


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


async def test_publisher_falls_back_to_text_when_image_fails(db: Database, repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import dealbot.publisher.threads as th

    monkeypatch.setattr(th, "CONTAINER_POLL_SECONDS", 0.01)
    monkeypatch.setattr(th, "REPLY_ATTEMPT_DELAYS", (0.0, 0.01))
    creates: list[dict[str, str]] = []
    reply_tries = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/threads"):
            params = dict(req.url.params)
            creates.append(params)
            if params.get("reply_to_id"):
                reply_tries["n"] += 1
                if reply_tries["n"] == 1:  # 부모 글이 아직 조회 안 됨 → 한 번 더
                    return httpx.Response(400, json={"error": {"message": "The requested resource does not exist", "code": 24}})
                return httpx.Response(200, json={"id": "C_REPLY"})
            return httpx.Response(200, json={"id": "C_IMG" if params.get("media_type") == "IMAGE" else "C_TXT"})
        if req.url.path.endswith("/C_IMG"):
            return httpx.Response(200, json={"status": "ERROR", "error_message": "Media download failed"})
        if req.url.path.endswith("/C_TXT") or req.url.path.endswith("/C_REPLY"):
            return httpx.Response(200, json={"status": "FINISHED"})
        if req.url.path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "9"})
        return httpx.Response(404, json={"error": {"message": "nope"}})

    pub = _publisher(db, repo_root, handler)
    db.kv_set(KV_TOKEN, "T")
    db.kv_set(KV_USER_ID, "999")
    deal = sample_deal()
    deal.product.image_url = "https://cdn2.ppomppu.co.kr/zboard/data3/m_thumb_1.jpg"
    result = await pub.publish(deal)
    assert result.ok and result.message_id == 9
    assert result.error and result.error.startswith("사진 없이 올림") and "Media download failed" in result.error
    kinds = [c.get("media_type") for c in creates]
    assert kinds == ["IMAGE", "TEXT", "TEXT", "TEXT"]  # 사진 → 실패 → 글만 → 답글(1회 실패) → 답글
    assert creates[-1]["reply_to_id"] == "9" and reply_tries["n"] == 2


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
    shop = ShopRegistry().get("coupang")
    hook = r.render_deal(sample_deal(), "https://link.coupang.com/a/x", shop=shop, template="deal_threads.j2")
    reply = r.render_deal(sample_deal(), "https://link.coupang.com/a/x", shop=shop, template="deal_threads_reply.j2")
    assert len(hook) <= 500 and len(reply) <= 500
    assert "<b>" not in hook and "<b>" not in reply  # 스레드는 평문
    # 첫 글(훅): 가격 비교와 "링크는 댓글에", 링크와 고지는 없음
    assert "49,900원 > 29,900원" in hook and "링크는 댓글에" in hook
    assert "https://" not in hook and "수수료" not in hook
    # 답글: 고지 + 상품 + 가격 + 링크
    assert reply.startswith("이 포스팅은 쿠팡 파트너스 활동의 일환으로")
    assert "29,900원" in reply and "https://link.coupang.com/a/x" in reply and "품절" in reply


async def test_publisher_posts_hook_then_reply(db: Database, repo_root: Path) -> None:
    seen: list[dict[str, str]] = []

    published = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append({"path": req.url.path, **dict(req.url.params)})
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": f"C{len(seen)}"})
        if req.url.path.endswith("/threads_publish"):
            published["n"] += 1
            return httpx.Response(200, json={"id": "100" if published["n"] == 1 else "200"})
        return httpx.Response(200, json={"status": "FINISHED"})  # 컨테이너 상태 조회

    pub = _publisher(db, repo_root, handler)
    db.kv_set(KV_TOKEN, "T")
    db.kv_set(KV_USER_ID, "999")
    result = await pub.publish(sample_deal())
    assert result.ok and result.message_id == 100 and result.error is None
    containers = [c for c in seen if c["path"].endswith("/threads")]
    assert len(containers) == 2
    assert "reply_to_id" not in containers[0] and "링크는 댓글에" in containers[0]["text"]
    assert containers[1]["reply_to_id"] == "100" and "https://link.coupang.com" in containers[1]["text"]

    # 답글 실패는 훅이 올라갔으니 실패로 치지 않되 error 로 알린다
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if "reply_to_id" in req.url.params:
            return httpx.Response(400, json={"error": {"message": "reply blocked"}})
        return httpx.Response(200, json={"id": "X"})

    pub2 = _publisher(db, repo_root, flaky)
    r2 = await pub2.publish(sample_deal())
    assert r2.ok and r2.error and "reply blocked" in r2.error

    # reply_template 을 비우면 한 글만
    pub3 = _publisher(db, repo_root, handler, reply_template=None)
    seen.clear()
    assert (await pub3.publish(sample_deal())).ok
    assert len([c for c in seen if c["path"].endswith("/threads")]) == 1


def test_copy_blocks(repo_root: Path) -> None:
    renderer = TemplateRenderer(repo_root / "templates")
    builder = CopyBlockBuilder(CopyConfig(), renderer, ShopRegistry())
    assert builder.enabled
    blocks = builder.build(sample_deal())
    assert [b.key for b in blocks] == ["kakao", "blog"]

    kakao = blocks[0]
    assert "<구매 링크>" in kakao.text and "29,900원" in kakao.text and "<b>" not in kakao.text
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

    async def fake_publish(deal, **_kw):  # type: ignore[no-untyped-def]
        return PublishResult(ok=True, message_id=1)  # 스레드/복붙 문구는 채널에 실제 발행됐을 때만 따라간다

    bot.notifier.send = fake_send  # type: ignore[method-assign]
    bot.publisher.publish = fake_publish  # type: ignore[method-assign]
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
        assert "threads.com/oauth/authorize" in url_msg and "/threadscode" in url_msg
        assert "client_id <code>APPID</code>" in url_msg and "수동" in url_msg

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
