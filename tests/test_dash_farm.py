"""파밍 대시보드 넘기기 /dashboard/farm/* (주인 11/15 — "sns 대시보드랑 파밍 대시보드 합치기. tailscale 안 켜도 앱에 접근").

가짜 허브(httpx.MockTransport · 진짜 소켓 한 번)로만 — 실제 tailnet·ts.net 접속 없음. 주소는 https://….ts.net 만 받음.
- 로그인 없으면 막힘 (허브를 부르지 않음) · 설정이 없으면 안내 화면
- 브라우저의 쿠키·Authorization·X-Dash-Token 은 넘어가지 않고 서버가 Bearer 열쇠만 붙임 · 열쇠는 로그·응답에 없음
- Set-Cookie 버림 · Location 고쳐 씀 · 보안 머리글은 이쪽 것
- 15MB 넘으면 413 · 30초 넘으면 504 · 다른 사이트에서 온 POST 거절
- 아래 탭 '파밍' · 앱 이름 Deren · 아이콘 바꾸기 도구
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import io
import json
import logging
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image

from dash_helpers import ORIGIN, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings, load_settings
from dealbot.dashboard import farm, shell, webapp

HUB = "https://perpfarm.tail1234.ts.net"
TOKEN = "farm-tok-9f8e7d6c5b4a-SECRET"
ROOT = Path(__file__).resolve().parents[1]


class Hub:
    """가짜 파밍 허브: 받은 요청을 모으고 정한 대로 답한다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.reply = lambda req: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<h1>farm</h1>")
        self.delay = 0.0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        got = self.reply(request)
        # 진짜 서버처럼 본문을 흘려 보냄 (MockTransport 에 다 읽은 응답을 주면 프록시가 조각으로 읽지 못함)
        return httpx.Response(got.status_code, headers=got.headers, stream=httpx.ByteStream(got.content))


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.farm_hub_url = HUB
    settings.secrets.farm_hub_token = TOKEN
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture
def hub(bot: DealBot) -> Hub:
    h = Hub()
    bot.dashboard.farm._client = httpx.AsyncClient(transport=httpx.MockTransport(h.handler))
    return h


def login(bot: DealBot, **extra: str) -> dict[str, str]:
    """로그인한 브라우저 — 파밍 화면처럼 X-Dash-Token 은 모른다."""
    return session_headers(bot, csrf=False, **extra)


async def call(bot: DealBot, method: str, path: str, headers: dict[str, str], body: bytes | None = None):  # type: ignore[no-untyped-def]
    h = dict(headers)
    if body is not None:
        h["content-length"] = str(len(body))
    res = await bot.web.dispatch(method, path, h, body)
    return res[0], res[1], (res[2] if len(res) >= 3 else None), (res[3] if len(res) >= 4 else {})


# ------------------------------------------------------------------ 로그인 · 설정
async def test_needs_login_and_never_calls_hub(bot: DealBot, hub: Hub) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9"}
    status, body, _ct, _h = await call(bot, "GET", "/dashboard/farm/", anon)
    assert status == 302 and body == "/dashboard/login?next=/dashboard/farm/"  # 기존처럼 로그인 화면으로 (로그인 뒤 파밍으로 돌아옴)
    assert (await call(bot, "GET", "/dashboard/farm/api/positions?x=1", anon))[0] == 302
    assert (await call(bot, "GET", "/dashboard/farm", anon))[0] == 302
    for method in ("POST", "PUT", "DELETE"):
        assert (await call(bot, method, "/dashboard/farm/api/stop", anon, b"{}"))[0] == 401
    bad = dict(anon, cookie="dash_session=" + "0" * 43)
    assert (await call(bot, "POST", "/dashboard/farm/api/stop", bad, b"{}"))[0] == 401
    assert hub.requests == []


async def test_bare_prefix_redirects_to_slash(bot: DealBot, hub: Hub) -> None:
    status, body, _ct, _h = await call(bot, "GET", "/dashboard/farm", login(bot))
    assert (status, body) == (302, "/dashboard/farm/") and hub.requests == []
    assert (await call(bot, "GET", "/dashboard/farm?tab=pos", login(bot)))[1] == "/dashboard/farm/?tab=pos"
    assert (await call(bot, "GET", "/dashboard/farmx", login(bot)))[0] == 404  # 비슷한 이름은 넘기지 않음


@pytest.mark.parametrize(("url", "token"), [(None, None), (HUB, None), (None, TOKEN), ("ftp://x", TOKEN), (HUB + "/path", TOKEN),
                                            (HUB, "bad token\twith space"), ("https://evil.example", TOKEN),
                                            ("http://perpfarm.tail1234.ts.net", TOKEN), ("https://ts.net.evil.example", TOKEN),
                                            ("https://evil-ts.net", TOKEN), ("https://100.64.0.7:8799", TOKEN)])
async def test_setup_page_when_not_configured(bot: DealBot, hub: Hub, url: str | None, token: str | None) -> None:
    bot.settings.secrets.farm_hub_url, bot.settings.secrets.farm_hub_token = url, token
    status, body, ctype, headers = await call(bot, "GET", "/dashboard/farm/", login(bot))
    assert status == 200 and ctype.startswith("text/html") and hub.requests == []
    assert "파밍 연결 준비 중 — 주인 할 일 2개" in body and "FARM_HUB_URL" in body and "FARM_HUB_TOKEN" in body
    assert "대시보드로 돌아가기" in body and "href='/dashboard/'" in body and "테일스케일" not in body  # 설치 안내 없음 — .env 두 줄만
    assert TOKEN not in body and "bad token" not in body and "tail1234" not in body
    assert "/dashboard/farm/" in headers["Content-Security-Policy"] and "connect-src 'self'" not in headers["Content-Security-Policy"]
    assert (await call(bot, "GET", "/dashboard/farm/static/app.js", login(bot)))[0] == 200  # 아래 경로도 같은 안내


def test_config_reads_env_and_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path / "data"))
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.dashboard.farm_hub_url == "" and s.secrets.farm_hub_url is None and s.secrets.farm_hub_token is None
    monkeypatch.setenv("FARM_HUB_URL", HUB + "/")
    monkeypatch.setenv("FARM_HUB_TOKEN", TOKEN)
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert farm.clean_url(s.secrets.farm_hub_url) == HUB and s.secrets.farm_hub_token == TOKEN
    assert farm.clean_url("HTTPS://Perpfarm.Tail1234.ts.net/") == HUB and farm.clean_url(HUB + ":8443") == HUB + ":8443"
    for bad in ("", None, "perpfarm.ts.net", "https://a b", "https://x/y", "https://user:pw@x.ts.net", "javascript:1", "https://x.ts.net?y",
                "http://100.64.0.7:8799", "http://perpfarm.tail1234.ts.net", "https://example.com", "https://ts.net", "https://xts.net",
                "https://perpfarm.ts.net.evil.example", "https://evil.example/.ts.net", "https://evil.example#.ts.net", "https://a..ts.net"):
        assert farm.clean_url(bad) == ""
    with pytest.raises(ValueError):
        type(s.dashboard)(farm_hub_url="https://x/y")
    with pytest.raises(ValueError):
        type(s.dashboard)(farm_hub_url="https://evil.example")
    assert type(s.dashboard)(farm_hub_url=HUB + "/").farm_hub_url == HUB


# ------------------------------------------------------------------ 요청 머리글
async def test_forwards_path_query_and_only_allowed_headers(bot: DealBot, hub: Hub, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    h = login(bot, authorization="Basic b3duZXI6cGx1bQ==", accept="text/html", range="bytes=0-9",
              **{"x-dash-token": "dash-csrf-value", "accept-language": "ko", "user-agent": "Mozilla/5.0 (iPhone)",
                 "x-forwarded-host": "evil.example", "x-forwarded-prefix": "/evil", "referer": ORIGIN + "/dashboard/"})
    session = h["cookie"]
    status, body, ctype, _h = await call(bot, "GET", "/dashboard/farm/api/positions?acct=a%20b&x=1", h)
    assert status == 200 and body == b"<h1>farm</h1>" and ctype == "text/html; charset=utf-8"
    req = hub.requests[0]
    assert str(req.url) == HUB + "/api/positions?acct=a%20b&x=1" and req.method == "GET"
    sent = {k.lower(): v for k, v in req.headers.items()}
    assert sent["authorization"] == f"Bearer {TOKEN}"  # 브라우저 것이 아니라 서버 열쇠
    assert sent["host"] == "perpfarm.tail1234.ts.net"
    assert sent["x-forwarded-prefix"] == "/dashboard/farm" and "x-forwarded-host" not in sent
    assert (sent["accept"], sent["accept-language"], sent["range"]) == ("text/html", "ko", "bytes=0-9")
    for gone in ("cookie", "x-dash-token", "origin", "referer", "sec-fetch-site", "x-forwarded-for", ":peer"):
        assert gone not in sent, gone
    raw = repr(sorted(sent.items()))
    assert session.split("=", 1)[1] not in raw and "dash-csrf-value" not in raw and "b3duZXI6cGx1bQ" not in raw
    # 루트 · 깊은 경로
    await call(bot, "GET", "/dashboard/farm/", login(bot))
    await call(bot, "GET", "/dashboard/farm/static/css/deren.css", login(bot))
    assert [str(r.url) for r in hub.requests[1:]] == [HUB + "/", HUB + "/static/css/deren.css"]
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
async def test_forwards_body_and_methods_without_dash_token(bot: DealBot, hub: Hub, method: str) -> None:
    hub.reply = lambda req: httpx.Response(201, json={"ok": True})
    body = json.dumps({"bot": "a", "on": False}).encode()
    status, raw, ctype, _h = await call(bot, method, "/dashboard/farm/api/bots/a", login(bot), body)
    assert status == 201 and json.loads(raw) == {"ok": True} and ctype == "application/json"
    req = hub.requests[0]
    assert req.method == method and req.content == body and req.headers["content-type"] == "application/json"
    assert req.headers["authorization"] == f"Bearer {TOKEN}" and "cookie" not in req.headers


async def test_unknown_method_is_405(bot: DealBot, hub: Hub) -> None:
    for method in ("PATCH", "OPTIONS", "TRACE", "CONNECT"):
        assert (await call(bot, method, "/dashboard/farm/api/x", login(bot)))[0] == 405
    assert hub.requests == []


# ------------------------------------------------------------------ 응답 머리글
async def test_response_drops_cookies_and_keeps_our_security_headers(bot: DealBot, hub: Hub) -> None:
    hub.reply = lambda req: httpx.Response(200, content=b"body{}", headers=[
        ("content-type", "text/css"), ("set-cookie", "hub=1; Path=/"), ("set-cookie", "dash_session=stolen; Path=/dashboard"),
        ("content-security-policy", "default-src *"), ("access-control-allow-origin", "*"), ("etag", '"abc"'),
        ("cache-control", "public, max-age=600"), ("x-frame-options", "ALLOWALL"), ("server", "hub/1"),
        ("www-authenticate", "Bearer"), ("service-worker-allowed", "/"), ("clear-site-data", '"*"')])
    status, raw, ctype, headers = await call(bot, "GET", "/dashboard/farm/static/deren.css", login(bot))
    assert (status, raw, ctype) == (200, b"body{}", "text/css")
    low = {k.lower(): v for k, v in headers.items()}
    assert "set-cookie" not in low and "stolen" not in repr(headers)
    assert low["etag"] == '"abc"' and low["cache-control"] == "public, max-age=600"
    for gone in ("access-control-allow-origin", "server", "www-authenticate", "service-worker-allowed", "clear-site-data"):
        assert gone not in low, gone
    csp = low["content-security-policy"]
    assert low["x-frame-options"] == "DENY"
    assert low["x-content-type-options"] == "nosniff" and low["referrer-policy"] == "no-referrer"
    # 이 서버의 /dashboard/farm/ 아래 스크립트·스타일·요청만 — 페이지 안 스크립트는 막음 (11/16 허브 인라인 0개 확인) · 바깥 주소·eval 은 안 됨
    parts = dict(p.strip().split(" ", 1) for p in csp.split(";"))
    src = parts["connect-src"]
    assert src and all(s.endswith("/dashboard/farm/") for s in src.split()) and ORIGIN.split("://")[1] + "/dashboard/farm/" in src.split()
    assert parts["script-src"] == src and "unsafe-inline" not in parts["script-src"] and parts["default-src"] == "'none'"
    assert parts["worker-src"] == src and "unsafe-eval" not in csp  # 허브 알림 서비스 워커 (범위 /dashboard/farm/ 까지만)
    assert parts["style-src"].startswith(src) and parts["img-src"].startswith(src) and parts["font-src"].startswith(src)
    assert parts["form-action"] == src and parts["frame-ancestors"] == "'none'"
    assert "http" not in csp and "*" not in csp
    # 보안 검토 11/15: 파밍 화면(같은 출처)의 fetch·폼이 대시보드 API 로 가지 못하게 — 'self'(서버 전체)는 base-uri 에만
    assert csp.count("'self'") == 1 and parts["base-uri"] == "'self'"
    assert low["cross-origin-opener-policy"] == "unsafe-none"  # 대시보드(same-origin)와 달라야 서로 연 창을 못 만짐
    # 캐시 말이 없으면 저장하지 않음
    hub.reply = lambda req: httpx.Response(200, json={"a": 1})
    assert (await call(bot, "GET", "/dashboard/farm/api/a", login(bot)))[3]["Cache-Control"] == "no-store"


@pytest.mark.parametrize(("loc", "want"), [
    (HUB + "/login?next=%2F", "/dashboard/farm/login?next=%2F"),
    (HUB, "/dashboard/farm/"),
    (HUB.upper().replace("HTTPS", "https") + "/a#top", "/dashboard/farm/a#top"),
    ("/bots/3", "/dashboard/farm/bots/3"),
    ("/", "/dashboard/farm/"),
    ("next?x=1", "next?x=1"),
    ("./a", "./a"),
    ("https://evil.example/x", None),
    ("http://perpfarm.tail1234.ts.net/x", None),  # 같은 이름이라도 다른 길(http)이면 다른 곳
    ("//evil.example/x", None),
    ("/\\evil.example", None),
    ("javascript:alert(1)", None),
    ("/a b", None),
    ("", None),
    # '..' 로 /dashboard/farm 밖(대시보드 API)으로 올라가는 주소 — CSP 는 리디렉션 뒤 경로를 보지 않으므로 여기서 막는다
    ("../api/state", None),
    ("/../api/state", None),
    ("/a/%2e%2E/../../api/state", None),
    ("a/..\\..\\api", None),
    (HUB + "/x/../../api/state", None),
    ("/a..b/c...", "/dashboard/farm/a..b/c..."),
])
def test_rewrite_location(loc: str, want: str | None) -> None:
    assert farm.rewrite_location(loc, HUB) == want


async def test_redirects_are_rewritten_not_followed(bot: DealBot, hub: Hub) -> None:
    hub.reply = lambda req: httpx.Response(303, headers={"location": HUB + "/bots?saved=1", "set-cookie": "a=b"})
    status, _raw, _ct, headers = await call(bot, "POST", "/dashboard/farm/bots/save", login(bot), b"{}")
    assert status == 303 and headers["Location"] == "/dashboard/farm/bots?saved=1" and "Set-Cookie" not in headers
    assert len(hub.requests) == 1  # 서버가 따라가지 않고 브라우저에 넘김
    hub.reply = lambda req: httpx.Response(302, headers={"location": "/"})
    assert (await call(bot, "GET", "/dashboard/farm/old", login(bot)))[3]["Location"] == "/dashboard/farm/"
    hub.reply = lambda req: httpx.Response(302, headers={"location": "https://evil.example/phish"})
    status, body, _ct, headers = await call(bot, "GET", "/dashboard/farm/out", login(bot))
    assert status == 502 and "Location" not in headers and "evil.example" not in body and "다른 곳" in body


async def test_real_request_writes_location_header_once(bot: DealBot, hub: Hub) -> None:
    """진짜 소켓으로: Location·Content-Length 가 한 번씩, Set-Cookie 없음, HEAD 는 본문 없이 허브가 말한 길이."""
    hub.reply = lambda req: httpx.Response(302, headers={"location": "/bots", "set-cookie": "a=b"}) if req.url.path == "/go" else \
        httpx.Response(200, content=b"" if req.method == "HEAD" else b"x" * 1234, headers={"content-type": "text/plain", "content-length": "1234"})
    bot.web.host, bot.web.port = "127.0.0.1", 0
    await bot.web.start()
    try:
        cookie = login(bot)["cookie"]

        async def raw(req: bytes) -> bytes:
            r, w = await asyncio.open_connection("127.0.0.1", bot.web.port)
            w.write(req)
            await w.drain()
            data = await asyncio.wait_for(r.read(), 5)
            w.close()
            return data

        got = await raw(f"GET /dashboard/farm/go HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode())
        head = got.split(b"\r\n\r\n", 1)[0].decode().lower()
        assert head.startswith("http/1.1 302 found") and head.count("location: /dashboard/farm/bots") == 1
        assert head.count("location:") == 1 and "set-cookie" not in head and head.count("content-length:") == 1
        # 창 묶음: 파밍 응답은 unsafe-none, 대시보드의 다른 응답(화면·정적 파일·로그인으로 보내기)은 모두 same-origin
        assert head.count("cross-origin-opener-policy: unsafe-none") == 1 and "same-origin" not in head
        assert "connect-src x/dashboard/farm/ " in head  # 브라우저가 부른 호스트(Host: x)의 /dashboard/farm/ 아래만
        for other in ("/dashboard/", "/dashboard/static/app.js", "/dashboard/api/state", "/nope"):
            for ck in (f"Cookie: {cookie}\r\n", ""):
                oh = (await raw(f"GET {other} HTTP/1.1\r\nHost: x\r\n{ck}\r\n".encode())).split(b"\r\n\r\n", 1)[0].decode().lower()
                assert oh.count("cross-origin-opener-policy: same-origin") == 1, other
        got = await raw(f"HEAD /dashboard/farm/big HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode())
        head, _, rest = got.partition(b"\r\n\r\n")
        assert b"content-length: 1234" in head.lower() and rest == b""
        # 너무 큰 올리기: 머리글만 보고 413 (본문을 기다리지 않음), 로그인 없으면 그 전에 401
        big = f"POST /dashboard/farm/upload HTTP/1.1\r\nHost: x\r\nContent-Length: {farm.UPLOAD_MAX + 1}\r\n"
        assert (await raw((big + f"Cookie: {cookie}\r\nSec-Fetch-Site: same-origin\r\n\r\n").encode())).startswith(b"HTTP/1.1 413 ")
        assert (await raw((big + "\r\n").encode())).startswith(b"HTTP/1.1 401 ")
        # 본문이 있는 POST 는 다 읽어 넘김
        n = len(hub.requests)
        got = await raw(f"POST /dashboard/farm/api/x HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\nContent-Type: text/plain\r\n"
                        f"Content-Length: 5\r\n\r\nhello".encode())
        assert got.startswith(b"HTTP/1.1 200 ") and hub.requests[n].content == b"hello"
    finally:
        await bot.web.stop()


# ------------------------------------------------------------------ 크기 · 시간
async def test_upload_over_15mb_is_413_and_exactly_15mb_passes(bot: DealBot, hub: Hub) -> None:
    assert farm.UPLOAD_MAX == 15 * 1024 * 1024 and farm.RESPONSE_MAX == 20 * 1024 * 1024 and farm.TIMEOUT_S == 30
    h = dict(login(bot), **{"content-length": str(farm.UPLOAD_MAX + 1), "content-type": "application/octet-stream"})
    status, body, _ct, _h = (lambda r: (r[0], r[1], None, None))(await bot.web.dispatch("POST", "/dashboard/farm/upload", h, None))
    assert status == 413 and "15MB" in body and hub.requests == []
    assert (await call(bot, "POST", "/dashboard/farm/upload", login(bot), b"x" * (farm.UPLOAD_MAX + 1)))[0] == 413
    status, _raw, _ct, _h = await call(bot, "POST", "/dashboard/farm/upload", login(bot), b"x" * farm.UPLOAD_MAX)
    assert status == 200 and len(hub.requests) == 1 and len(hub.requests[0].content) == farm.UPLOAD_MAX
    chunked = dict(login(bot), **{"transfer-encoding": "chunked"})
    assert (await bot.web.dispatch("POST", "/dashboard/farm/upload", chunked, None))[0] == 411


async def test_response_over_limit_is_refused(bot: DealBot, hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(farm, "RESPONSE_MAX", 1000)
    hub.reply = lambda req: httpx.Response(200, content=b"y" * 1001)
    status, body, _ct, _h = await call(bot, "GET", "/dashboard/farm/huge", login(bot))
    assert status == 502 and "너무 커" in body and "y" * 50 not in body
    hub.reply = lambda req: httpx.Response(200, content=b"y" * 1000)
    assert (await call(bot, "GET", "/dashboard/farm/ok", login(bot)))[0] == 200


async def test_timeout_is_504_with_plain_words(bot: DealBot, hub: Hub, monkeypatch: pytest.MonkeyPatch,
                                               caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(farm, "TIMEOUT_S", 0.15)
    hub.delay = 2.0
    status, body, ctype, _h = await asyncio.wait_for(call(bot, "GET", "/dashboard/farm/slow?k=secret-in-query", login(bot)), 1.5)
    assert status == 504 and ctype.startswith("text/html") and "파밍 서버가 대답이 없어요" in body and "대시보드로 돌아가기" in body
    assert "Traceback" not in body and "Timeout" not in body
    assert TOKEN not in caplog.text and "secret-in-query" not in caplog.text and TOKEN not in body


async def test_hub_down_or_rejecting_token_shows_plain_page_without_secrets(bot: DealBot, hub: Hub,
                                                                             caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {req.url} with {req.headers['authorization']}")

    hub.reply = boom
    status, body, _ct, _h = await call(bot, "GET", "/dashboard/farm/", login(bot))
    assert status == 502 and "파밍 서버에 닿지 않아요" in body and TOKEN not in body and "tail1234" not in body
    hub.reply = lambda req: httpx.Response(401, headers={"www-authenticate": "Bearer"}, text=f"bad token {TOKEN}")
    status, body, _ct, headers = await call(bot, "GET", "/dashboard/farm/", login(bot))
    assert status == 502 and "열쇠" in body and TOKEN not in body and "WWW-Authenticate" not in headers
    hub.reply = lambda req: httpx.Response(404, text="nope")
    assert (await call(bot, "GET", "/dashboard/farm/none", login(bot)))[:2] == (404, b"nope")  # 그 밖의 상태는 그대로
    assert TOKEN not in caplog.text and "Bearer farm" not in caplog.text


# ------------------------------------------------------------------ 다른 사이트에서 온 요청
async def test_cross_site_writes_are_refused(bot: DealBot, hub: Hub) -> None:
    for extra in ({"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}, {"sec-fetch-site": "none"},
                  {"origin": "https://evil.example"}, {"origin": "null"}, {"origin": HUB}):
        for method in ("POST", "PUT", "DELETE"):
            status, body, _ct, _h = await call(bot, method, "/dashboard/farm/api/stop", login(bot, **extra), b"{}")
            assert status == 403 and "다른 곳" in body, (extra, method)
    assert hub.requests == []
    # 같은 사이트 (Origin 이 대시보드 주소 · 또는 머리글 없이) 는 X-Dash-Token 없이도 넘어감
    ok = login(bot)
    assert "x-dash-token" not in ok
    assert (await call(bot, "POST", "/dashboard/farm/api/stop", ok, b"{}"))[0] == 200
    bare = {k: v for k, v in ok.items() if k not in ("origin", "sec-fetch-site")}
    assert (await call(bot, "POST", "/dashboard/farm/api/stop", bare, b"{}"))[0] == 200
    # 읽기(GET)는 다른 사이트의 링크로 열어도 됨 (쿠키 SameSite=Lax 이동 — 상태를 바꾸지 않음)
    assert (await call(bot, "GET", "/dashboard/farm/", login(bot, **{"sec-fetch-site": "cross-site"})))[0] == 200
    assert len(hub.requests) == 3


async def test_other_dashboard_routes_untouched(bot: DealBot, hub: Hub) -> None:
    h = session_headers(bot)
    assert (await bot.web.dispatch("GET", "/dashboard/api/state", h))[0] == 200
    assert (await bot.web.dispatch("GET", "/dashboard/", h))[0] == 200
    assert (await bot.web.dispatch("DELETE", "/dashboard/api/state", h))[0] == 405
    assert hub.requests == []


# ------------------------------------------------------------------ 화면 · 이름 · 아이콘
async def test_bottom_tab_has_farm_as_seventh_and_jobprep_eighth(bot: DealBot) -> None:
    html = (await bot.web.dispatch("GET", "/dashboard/", session_headers(bot)))[1]
    nav = re.search(r'<nav class="tabs"[^>]*>(.*?)</nav>', html, re.S).group(1)  # type: ignore[union-attr]
    tabs = re.findall(r'<a class="tab" href="([^"]+)"[^>]*>.*?<span>([^<]+)</span></a>', nav, re.S)
    # 11/22 취준 탭이 여덟째 (파밍은 일곱째 그대로)
    assert len(tabs) == 8 and tabs[6] == ("/dashboard/farm/", "파밍") and tabs[7] == ("/dashboard/jobprep/", "취준") and tabs[3] == ("#/sns", "SNS")
    assert nav.count("<svg") == 8 and 'target="' not in nav  # 같은 탭 안 이동
    css = (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert ".tabs{" in css and "grid-template-columns:repeat(8,minmax(0,1fr))" in css
    assert max(len(lab) for _href, lab in tabs) <= 3  # 320px 폭(한 칸 40px)에 12px 글자 세 자까지 ('취준' 두 자)
    assert shell.FARM_URL == farm.HOME


def test_app_name_is_deren_everywhere_the_phone_reads_it() -> None:
    m = webapp.manifest()
    assert m["name"] == m["short_name"] == "Deren" and m["id"] == "/dashboard"  # id 는 그대로 — 깐 앱이 둘이 되지 않게
    head = webapp.head_tags()
    assert "<title>Deren</title>" in head and '<meta name="apple-mobile-web-app-title" content="Deren">' in head
    assert '<meta name="application-name" content="Deren">' in head


def test_icon_urls_follow_the_files(tmp_path: Path) -> None:
    """아이콘 파일을 넣기만 하면 주소(?v=)가 바뀐다 · tools/app_icons.py 가 다섯 파일과 승인 표를 같이 만든다."""
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
        digest = hashlib.sha256((webapp.STATIC_DIR / name).read_bytes()).hexdigest()[:10]
        assert webapp.icon_url(name) == f"/dashboard/static/{name}?v={digest}"
    assert [i["src"].split("?")[0].rsplit("/", 1)[1] for i in webapp.manifest()["icons"]] == [
        "icon-192.png", "icon-512.png", "icon-maskable-512.png"]
    spec = importlib.util.spec_from_file_location("app_icons", ROOT / "tools" / "app_icons.py")
    tool = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(tool)  # type: ignore[union-attr]
    src, static, table = tmp_path / "duck", tmp_path / "static", tmp_path / "icons.json"
    src.mkdir()
    static.mkdir()
    Image.new("RGB", (1024, 1024), "#ffd23f").save(src / "icon-1024.jpg", quality=92)
    Image.new("RGB", (192, 192), "#000000").save(src / "icon-192.png")  # 작은 것보다 큰 밑그림을 쓴다
    (static / "icon.svg").write_bytes(b"<svg/>")
    table.write_text(json.dumps({"icon.svg": "old", "icon-192.png": "old"}), encoding="utf-8")
    files = tool.build(src)
    assert tool.apply(files, static, table, write=False) == list(files) and not (static / "icon-192.png").exists()
    assert sorted(tool.apply(files, static, table)) == sorted(files)
    sizes = {"icon-192.png": 192, "icon-512.png": 512, "icon-maskable-512.png": 512, "apple-touch-icon.png": 180, "favicon-64.png": 64}
    for name, size in sizes.items():
        img = Image.open(io.BytesIO((static / name).read_bytes())).convert("RGBA")
        assert img.size == (size, size)
        corner, mid = img.getpixel((0, 0)), img.getpixel((size // 2, size // 2))
        assert mid[3] == 255 and mid[0] > 200 and mid[2] < 120  # 큰 밑그림(노랑)
        assert corner[3] == (255 if name in ("icon-maskable-512.png", "apple-touch-icon.png") else 0)
    got = json.loads(table.read_text(encoding="utf-8"))
    assert set(got) == {*sizes, "icon.svg"}
    assert all(got[n] == hashlib.sha256((static / n).read_bytes()).hexdigest() for n in got)
    assert tool.apply(files, static, table) == []  # 다시 돌려도 그대로
    with pytest.raises(SystemExit):
        tool.build(tmp_path / "none")  # 그림이 없는 폴더


def test_farm_csp_takes_only_plain_hosts() -> None:
    """CSP 출처는 '호스트[:포트]/dashboard/farm/' 만 — 이상한 Host 값(빈칸·세미콜론·따옴표)은 버리고, 하나도 없으면 아무것도 못 부름."""
    csp = farm.farm_csp(["Dash.Example.com:8443", "a b", "x;script-src *", "'self'", "evil.com/", "*", "", "dash.example.com:8443"])
    parts = dict(p.strip().split(" ", 1) for p in csp.split(";"))
    assert parts["connect-src"] == "dash.example.com:8443/dashboard/farm/" and "*" not in csp and "evil" not in csp
    assert dict(p.strip().split(" ", 1) for p in farm.farm_csp([]).split(";"))["connect-src"] == "'none'"


async def test_cross_site_link_into_farm_goes_to_farm_home_without_calling_hub(bot: DealBot, hub: Hub) -> None:
    """다른 사이트의 링크로 파밍 안쪽 주소(GET)를 열면 허브를 부르지 않고 첫 화면으로 — 첫 화면·주소창·앱 안 이동은 그대로."""
    for site in ("cross-site", "same-site"):
        status, body, _ct, headers = await call(bot, "GET", "/dashboard/farm/bots/3/stop?now=1", login(bot, **{"sec-fetch-site": site}))
        assert (status, body) == (303, "/dashboard/farm/") and not hub.requests
        assert headers["Cross-Origin-Opener-Policy"] == "unsafe-none"
        assert (await call(bot, "GET", "/dashboard/farm/?x=1", login(bot, **{"sec-fetch-site": site})))[0] == 303 and not hub.requests
    assert (await call(bot, "GET", "/dashboard/farm/", login(bot, **{"sec-fetch-site": "cross-site"})))[0] == 200
    for site in ("none", "same-origin"):
        assert (await call(bot, "GET", "/dashboard/farm/bots/3?tab=a", login(bot, **{"sec-fetch-site": site})))[0] == 200
    assert [r.url.path for r in hub.requests] == ["/", "/bots/3", "/bots/3"]


async def test_gzip_is_asked_and_passed_through_untouched(bot: DealBot, hub: Hub) -> None:
    """11/16 '파밍 탭 느림': 브라우저가 gzip 을 받으면 허브에도 gzip 을 부탁하고, 압축된 본문을 풀지 않고 Content-Encoding 과 같이 넘긴다."""
    import gzip as _gz
    page = b"<h1>farm</h1>" * 200
    packed = _gz.compress(page)
    seen: list[httpx.Request] = []

    async def raw_hub(request: httpx.Request) -> httpx.Response:  # 압축된 바이트를 그대로 흘려 보냄 (.content 는 풀려 버려서)
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", "content-encoding": "gzip"},
                              stream=httpx.ByteStream(packed))
    bot.dashboard.farm._client = httpx.AsyncClient(transport=httpx.MockTransport(raw_hub))
    status, body, _c, h = await call(bot, "GET", "/dashboard/farm/", login(bot, **{"accept-encoding": "gzip, deflate, br"}))
    sent = {k.lower(): v for k, v in seen[-1].headers.items()}
    assert status == 200 and sent["accept-encoding"] == "gzip"
    assert body == packed and _gz.decompress(body) == page  # 풀린 본문에 gzip 표시가 붙는 일 없음
    assert h["Content-Encoding"] == "gzip" and h["Vary"] == "Accept-Encoding"
    # gzip 을 못 받는 브라우저 → identity
    bot.dashboard.farm._client = httpx.AsyncClient(transport=httpx.MockTransport(hub.handler))
    hub.reply = lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=b"plain")
    status, body, _c, h = await call(bot, "GET", "/dashboard/farm/", login(bot))
    sent = {k.lower(): v for k, v in hub.requests[-1].headers.items()}
    assert sent["accept-encoding"] == "identity" and body == b"plain" and "Content-Encoding" not in h


async def test_static_cache_etag_304_and_server_timing_pass_through(bot: DealBot, hub: Hub) -> None:
    """11/16 퍼프덱스 허브 속도 개선: 해시 붙은 정적 파일의 immutable 캐시·ETag·304·Server-Timing 을 덮지 않고 그대로."""
    hub.reply = lambda req: (httpx.Response(304, headers={"etag": '"h1"', "cache-control": "no-cache"})
                             if req.headers.get("if-none-match") == '"h1"' else
                             httpx.Response(200, headers={"content-type": "text/javascript", "etag": '"h1"', "vary": "Origin",
                                                          "cache-control": "public, max-age=31536000, immutable",
                                                          "server-timing": "app;dur=3.2"}, content=b"x()"))
    status, body, _c, h = await call(bot, "GET", "/dashboard/farm/static/js/x.js?v=abc", login(bot))
    assert status == 200 and body == b"x()"
    assert h["Cache-Control"] == "public, max-age=31536000, immutable" and h["ETag"] == '"h1"'
    assert h["Server-Timing"] == "app;dur=3.2" and h["Vary"] == "Origin"
    status, body, _c, h = await call(bot, "GET", "/dashboard/farm/static/js/x.js?v=abc", login(bot, **{"if-none-match": '"h1"'}))
    sent = {k.lower(): v for k, v in hub.requests[-1].headers.items()}
    assert sent["if-none-match"] == '"h1"' and status == 304 and body == b"" and h["ETag"] == '"h1"'
