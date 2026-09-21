"""작은 웹 서버와 스레드 OAuth 콜백(승인만으로 연결) 검사."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dealbot.app import KV_OAUTH_STATE, DealBot
from dealbot.config import Settings, load_settings
from dealbot.publisher.threads import KV_TOKEN, ThreadsClient
from dealbot.web import WebServer, page

ROOT = Path(__file__).resolve().parents[1]


async def test_web_server_routes() -> None:
    srv = WebServer(0, host="127.0.0.1")
    seen: dict[str, str] = {}

    async def cb(query: dict[str, str]) -> tuple[int, str]:
        seen.update(query)
        return 200, page("확인", "done")

    srv.route("/threads/callback", cb)
    await srv.start()
    try:
        assert srv.running and srv.port > 0
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            r = await c.get("/health")
            assert r.status_code == 200 and r.text == "ok"
            r = await c.get("/threads/callback", params={"code": "abc", "state": "s1"})
            assert r.status_code == 200 and "done" in r.text and "text/html" in r.headers["content-type"]
            assert seen == {"code": "abc", "state": "s1"}
            assert (await c.get("/nope")).status_code == 404
            assert (await c.post("/threads/callback")).status_code == 405
    finally:
        await srv.stop()
    assert not srv.running


def _auth_link(msg: str) -> str:
    return next(line for line in msg.splitlines() if line.startswith("https://threads.com/oauth/authorize"))


async def test_threads_callback_connects_without_code_copy(settings: Settings) -> None:
    settings.collectors = []
    settings.secrets.threads_app_id = "APPID"
    settings.secrets.threads_app_secret = "SECRET"
    settings.secrets.public_domain = "bot.up.railway.app"
    settings.secrets.threads_redirect_uri = "https://bot.up.railway.app/threads/callback"
    assert settings.secrets.threads_callback_served

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/oauth/access_token":
            assert req.url.host == "graph.threads.net"
            return httpx.Response(200, json={"access_token": "S", "user_id": "999"})
        if req.url.path == "/access_token":
            return httpx.Response(200, json={"access_token": "L", "expires_in": 5184000})
        return httpx.Response(200, json={"id": "999", "username": "hotdeal"})

    bot = DealBot(settings)
    try:
        assert "🧵 <b>스레드 자동 게시</b> ❌ 미연결 → /threadsauth" in bot.reporter.status_text()
        msg = await bot.threads_auth_url()
        assert "자동" in msg and "<code>https://bot.up.railway.app/threads/callback</code>" in msg
        assert "localhost" in msg  # 메타가 localhost 를 받지 않는다는 안내
        link = _auth_link(msg)
        q = parse_qs(urlsplit(link).query)
        assert q["redirect_uri"] == ["https://bot.up.railway.app/threads/callback"]
        state = q["state"][0]
        assert len(state) >= 12 and bot.db.kv_get(KV_OAUTH_STATE) == state

        bot.threads.client = ThreadsClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="APPID", app_secret="SECRET", retry_backoff=0.01
        )
        # state 가 다르면 거절
        status, body = await bot.web.dispatch("GET", "/threads/callback?code=X&state=wrong")
        assert status == 400 and "state" in body
        assert bot.db.kv_get(KV_TOKEN) is None
        # 사용자가 승인 거부
        status, body = await bot.web.dispatch("GET", "/threads/callback?error=access_denied&error_description=User+denied")
        assert status == 400 and "User denied" in body
        # 정상 승인 → 연결
        status, body = await bot.web.dispatch("GET", f"/threads/callback?code=CODE&state={state}")
        assert status == 200 and "연결 완료" in body and "@hotdeal" in body
        assert bot.db.kv_get(KV_TOKEN) == "L"
        # 같은 state 재사용 불가
        status, _ = await bot.web.dispatch("GET", f"/threads/callback?code=CODE&state={state}")
        assert status == 400
        assert "이미 연결" in await bot.threads_auth_url()
        status = bot.reporter.status_text()
        assert "✅ 연결됨 @hotdeal" in status and "토큰 " in status and "최근 실패" not in status
        bot.db.log_event("WARNING", "threads", "p1: threads api 400 (publish): The requested resource does not exist [code 24]")
        assert "↳ 최근 실패: <code>" in bot.reporter.status_text()

        # /threadstest: 연습 모드면 미리보기만, 실제 모드면 올리고 링크를 돌려준다
        preview = await bot.threads_test()
        assert "🧵 <b>스레드에는 이렇게 올라갑니다 (샘플 딜)</b>" in preview and "<pre>" in preview and "연습 모드" in preview
        assert "답글(링크):" in preview and "link.coupang.com" in preview

        posted: list[str] = []

        def post_handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/threads"):
                posted.append(dict(req.url.params).get("media_type", ""))
                return httpx.Response(200, json={"id": "C1"})
            if req.url.path.endswith("/threads_publish"):
                return httpx.Response(200, json={"id": "777"})
            if req.url.path.endswith("/777"):
                return httpx.Response(200, json={"permalink": "https://www.threads.com/@hotdeal/post/ABC"})
            return httpx.Response(200, json={"status": "FINISHED"})

        bot.threads.client = ThreadsClient(
            httpx.AsyncClient(transport=httpx.MockTransport(post_handler)), app_id="APPID", app_secret="SECRET", retry_backoff=0.01
        )
        bot.threads.dry_run = False
        msg = await bot.threads_test()
        assert "✅ 스레드에 올렸습니다: https://www.threads.com/@hotdeal/post/ABC" in msg and "지워 주세요" in msg
        assert posted == ["IMAGE", "TEXT"]  # 훅(사진) + 링크 답글
        assert "글이 없습니다" in await bot.threads_test(999)
    finally:
        await bot.close()


def test_default_redirect_uses_railway_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "hotdeal.up.railway.app")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.delenv("THREADS_REDIRECT_URI", raising=False)
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.threads_redirect_uri == "https://hotdeal.up.railway.app/threads/callback"
    assert s.secrets.threads_callback_served and s.secrets.threads_callback_path == "/threads/callback"
    assert s.secrets.web_port == 9000

    monkeypatch.setenv("THREADS_REDIRECT_URI", "https://example.com/cb")
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.threads_redirect_uri == "https://example.com/cb"
    assert not s.secrets.threads_callback_served and s.secrets.threads_callback_path == "/cb"

    monkeypatch.delenv("THREADS_REDIRECT_URI")
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN")
    monkeypatch.delenv("PORT")
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.threads_redirect_uri == "https://localhost/callback" and s.secrets.web_port == 8080


async def test_static_files_and_redirects(tmp_path: Path) -> None:
    media = tmp_path / "media"
    media.mkdir()
    (media / "a.jpg").write_bytes(b"\xff\xd8\xff\xe0JPEG")
    (media / "secret.txt").write_text("no")
    srv = WebServer(0, host="127.0.0.1")
    srv.serve_static("/media/", media)

    async def go(query: dict[str, str]) -> tuple[int, str]:
        return 302, "https://link.coupang.com/a/" + query["path"]

    srv.route_prefix("/d/", go)
    ok = await srv.dispatch("GET", "/media/a.jpg")
    assert ok[0] == 200 and ok[1] == b"\xff\xd8\xff\xe0JPEG" and ok[2] == "image/jpeg"  # type: ignore[misc]
    assert (await srv.dispatch("GET", "/media/secret.txt"))[0] == 404
    assert (await srv.dispatch("GET", "/media/../a.jpg"))[0] == 404
    assert (await srv.dispatch("GET", "/media/missing.jpg"))[0] == 404
    assert (await srv.dispatch("GET", "/d/42"))[:2] == (302, "https://link.coupang.com/a/42")
    await srv.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            r = await c.get("/media/a.jpg")
            assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "max-age" in r.headers["cache-control"]
            r = await c.get("/d/42")
            assert r.status_code == 302 and r.headers["location"] == "https://link.coupang.com/a/42"
    finally:
        await srv.stop()


async def test_deals_page_lists_published_deals_with_buy_links(settings: Settings) -> None:
    from dealbot.cli import sample_deal

    settings.collectors = []
    settings.secrets.public_domain = "bot.up.railway.app"
    bot = DealBot(settings)
    try:
        status, body = await bot.web.dispatch("GET", "/")
        assert status == 200 and "오늘의 핫딜" in body and "지금은 올라온 딜이 없어요" in body
        deal = sample_deal()
        assert bot.db.enqueue(deal, score=40.0)
        bot.db.update_queue_item(1, status="published", deal=deal)
        bot.db.record_post(deal, channel_id=None, message_id=1, dry_run=True)
        status, body = await bot.web.dispatch("GET", "/")
        assert status == 200 and "스탠리 텀블러" in body and 'href="/d/1"' in body and "29,900원" in body
        assert "쿠팡 파트너스" in body and "t.me/" in body
        status, target = await bot.web.dispatch("GET", "/d/1")
        assert (status, target) == (302, "https://link.coupang.com/a/sample")
        assert (await bot.web.dispatch("GET", "/d/999"))[0] == 404
        assert (await bot.web.dispatch("GET", "/d/abc"))[0] == 404
        # 상태 화면에 페이지 주소 안내
        assert "🔗 핫딜 페이지 https://bot.up.railway.app" in bot.reporter.status_text()
    finally:
        await bot.close()


async def test_instagram_callback_and_test_command(settings: Settings, tmp_path: Path) -> None:
    from dealbot.publisher import instagram as ig
    from dealbot.publisher.instagram import InstagramClient

    settings.collectors = []
    settings.secrets.instagram_app_id = "IGAPP"
    settings.secrets.instagram_app_secret = "IGSECRET"
    settings.secrets.public_domain = "bot.up.railway.app"
    settings.secrets.instagram_redirect_uri = "https://bot.up.railway.app/instagram/callback"
    settings.card.font_path = None
    bot = DealBot(settings)
    try:
        assert "📸 <b>인스타그램 자동 게시</b> ❌ 미연결 → /instaauth" in bot.reporter.status_text()
        msg = await bot.instagram_auth_url()
        assert "자동" in msg and "<code>https://bot.up.railway.app/instagram/callback</code>" in msg and "프로페셔널" in msg
        link = next(line for line in msg.splitlines() if line.startswith("https://www.instagram.com/oauth/authorize"))
        state = parse_qs(urlsplit(link).query)["state"][0]

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.host == "api.instagram.com":
                return httpx.Response(200, json={"access_token": "S", "user_id": 1})
            if req.url.path == "/access_token":
                return httpx.Response(200, json={"access_token": "L", "expires_in": 5184000})
            if req.url.path.endswith("/me"):
                return httpx.Response(200, json={"user_id": "178", "username": "oneul_hot_deal"})
            if req.url.path.endswith("/media"):
                assert req.url.params["image_url"].startswith("https://bot.up.railway.app/media/") and req.url.params["image_url"].endswith(".jpg")
                return httpx.Response(200, json={"id": "C1"})
            if req.url.path.endswith("/media_publish"):
                return httpx.Response(200, json={"id": "555"})
            if req.url.path.endswith("/555"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/ABC/"})
            return httpx.Response(200, json={"status_code": "FINISHED"})

        bot.instagram.client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="IGAPP", app_secret="IGSECRET", retry_backoff=0.01)
        assert (await bot.web.dispatch("GET", "/instagram/callback?code=X&state=bad"))[0] == 400
        status, body = await bot.web.dispatch("GET", f"/instagram/callback?code=CODE&state={state}")
        assert status == 200 and "연결 완료" in body and "@oneul_hot_deal" in body
        assert bot.db.kv_get(ig.KV_TOKEN) == "L" and bot.db.kv_get(ig.KV_USER_ID) == "178" and bot.db.kv_get(ig.KV_USERNAME) == "oneul_hot_deal"
        assert "✅ 연결됨 @oneul_hot_deal" in bot.reporter.status_text()

        preview = await bot.instagram_test()
        assert "📸 <b>인스타그램에는 이렇게 올라갑니다 (샘플 딜)</b>" in preview and "사진(카드): https://bot.up.railway.app/media/" in preview
        assert "연습 모드" in preview
        card_name = preview.split("/media/")[1].split()[0]
        assert (settings.data_dir / "media" / card_name).exists()
        served = await bot.web.dispatch("GET", f"/media/{card_name}")
        assert served[0] == 200 and served[2] == "image/jpeg"  # type: ignore[misc]

        bot.instagram.dry_run = False
        msg = await bot.instagram_test()
        assert "✅ 인스타그램에 올렸습니다: https://www.instagram.com/p/ABC/" in msg
    finally:
        await bot.close()


def test_instagram_redirect_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "hotdeal.up.railway.app")
    monkeypatch.delenv("INSTAGRAM_REDIRECT_URI", raising=False)
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.instagram_redirect_uri == "https://hotdeal.up.railway.app/instagram/callback"
    assert s.secrets.instagram_callback_served and s.secrets.public_base_url == "https://hotdeal.up.railway.app"
    monkeypatch.delenv("RAILWAY_PUBLIC_DOMAIN")
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.instagram_redirect_uri == "https://localhost/instagram/callback" and s.secrets.public_base_url is None
