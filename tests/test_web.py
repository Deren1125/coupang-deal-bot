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
