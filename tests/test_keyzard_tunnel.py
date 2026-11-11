"""키자드 도구 화면 이어 주기 (주인 11/11): /dashboard/keyzard/ → 127.0.0.1:8110 (Blog-Auto tools/keyzard_indexer).

대시보드 로그인 그대로 · 버튼(POST·DELETE)은 본문까지 넘김 · 다른 사이트에서 온 POST 는 막음 · 원격 로그인(vnc)은 여전히 GET 만."""
from __future__ import annotations

import asyncio

from dealbot.dashboard import api as dash_api
from dealbot.web import WebServer


async def _upstream(seen: list[bytes]) -> tuple[asyncio.AbstractServer, int]:
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        n = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1])
        body = await r.readexactly(n) if n else b""
        seen.append(head + body)
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n{\"ok\":true}")
        await w.drain()
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _req(port: int, method: str, path: str, headers: str = "", body: bytes = b"") -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"{method} {path} HTTP/1.1\r\nHost: dash.example\r\n{headers}Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await w.drain()
    data = await asyncio.wait_for(r.read(), timeout=5)
    w.close()
    return data


async def _server(up_port: int) -> tuple[WebServer, int]:
    web = WebServer(0, host="127.0.0.1")
    web.guard_prefix("/dashboard", lambda h: h.get("authorization") == "ok")
    web.route_tunnel(dash_api.KEYZARD_PREFIX, lambda: up_port, methods=("GET", "POST", "DELETE"))
    web.route_tunnel("/dashboard/vnc/", lambda: up_port)
    await web.start()
    return web, web._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]


async def test_keyzard_page_and_buttons_go_through_with_login() -> None:
    seen: list[bytes] = []
    up, up_port = await _upstream(seen)
    web, port = await _server(up_port)
    try:
        assert b" 401 " in (await _req(port, "GET", "/dashboard/keyzard/"))[:20]  # 로그인 없음
        assert (await _req(port, "GET", "/dashboard/keyzard/api/status", "Authorization: ok\r\n")).endswith(b'{"ok":true}')
        assert seen[-1].startswith(b"GET /api/status HTTP/1.1")
        body = b'{"link":"https://blog.naver.com/a/1","submit":true}'
        got = await _req(port, "POST", "/dashboard/keyzard/api/kz/prepare/b1",
                         "Authorization: ok\r\nOrigin: https://dash.example\r\nContent-Type: application/json\r\n", body)
        assert got.endswith(b'{"ok":true}')
        assert seen[-1].startswith(b"POST /api/kz/prepare/b1 HTTP/1.1") and seen[-1].endswith(body)  # 본문까지
        got = await _req(port, "DELETE", "/dashboard/keyzard/api/look/abc", "Authorization: ok\r\nReferer: https://dash.example/dashboard/keyzard/\r\n")
        assert got.endswith(b'{"ok":true}') and seen[-1].startswith(b"DELETE /api/look/abc ")
    finally:
        await web.stop()
        up.close()


async def test_cross_site_or_unauthenticated_posts_are_blocked() -> None:
    seen: list[bytes] = []
    up, up_port = await _upstream(seen)
    web, port = await _server(up_port)
    try:
        for hdr in ("Authorization: ok\r\nOrigin: https://evil.example\r\n", "Authorization: ok\r\n",
                    "Authorization: ok\r\nOrigin: null\r\n"):
            got = await _req(port, "POST", "/dashboard/keyzard/api/kz/register_all/b1", hdr, b"{}")
            assert b" 403 " in got[:20], hdr
        assert b" 401 " in (await _req(port, "POST", "/dashboard/keyzard/api/kz/rss/b1", "Origin: https://dash.example\r\n", b"{}"))[:20]
        big = b"x" * (64 * 1024 + 1)
        got = await _req(port, "POST", "/dashboard/keyzard/api/settings/global", "Authorization: ok\r\nOrigin: https://dash.example\r\n", big)
        assert b" 200 " not in got[:20]  # 너무 큰 본문은 넘기지 않음
        # 원격 로그인 화면은 그대로 GET 만 — POST 는 이어 주지 않음
        got = await _req(port, "POST", "/dashboard/vnc/x", "Authorization: ok\r\nOrigin: https://dash.example\r\n", b"{}")
        assert b" 200 " not in got[:20]
        assert not seen
    finally:
        await web.stop()
        up.close()
