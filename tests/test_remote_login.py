"""원격 로그인 화면 (주인 10/23): 대시보드 로그인 뒤에만, 세션이 살아 있을 때만 127.0.0.1 의 websockify 로 잇는다."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from dash_helpers import post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import remote_login
from dealbot.web import WebServer


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def _upstream(seen: list[bytes]) -> tuple[asyncio.AbstractServer, int]:
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = await r.readuntil(b"\r\n\r\n")
        seen.append(head)
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello")
        await w.drain()
        w.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _get(port: int, path: str, headers: str = "") -> bytes:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n{headers}\r\n".encode())
    await w.drain()
    data = await asyncio.wait_for(r.read(), timeout=5)
    w.close()
    return data


async def test_tunnel_needs_login_and_an_open_session() -> None:
    seen: list[bytes] = []
    up, up_port = await _upstream(seen)
    open_now = {"port": None}
    web = WebServer(0, host="127.0.0.1")
    web.guard_prefix("/dashboard", lambda h: h.get("authorization") == "ok")
    web.route_tunnel("/dashboard/vnc/", lambda: open_now["port"])
    await web.start()
    port = web._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        assert b" 401 " in (await _get(port, "/dashboard/vnc/vnc.html"))[:20]  # 로그인 없음
        assert b" 404 " in (await _get(port, "/dashboard/vnc/vnc.html", "Authorization: ok\r\n"))[:20]  # 세션 없음
        open_now["port"] = up_port
        body = await _get(port, "/dashboard/vnc/vnc.html?autoconnect=true", "Authorization: ok\r\n")
        assert body.endswith(b"hello") and seen[0].startswith(b"GET /vnc.html?autoconnect=true HTTP/1.1")
    finally:
        await web.stop()
        up.close()


def test_unguarded_tunnel_prefix_never_opens() -> None:
    web = WebServer(0)
    web.route_tunnel("/open/", lambda: 1)
    guarded, guard, _ = web._guard_for("/open/x")
    assert not guarded and guard is None  # _handle 은 지키지 않는 앞부분이면 404 (여는 쪽 실수로 바깥에 열리지 않게)


@pytest.mark.asyncio
async def test_login_remote_action_relays_a_session_and_shows_the_open_row(bot: DealBot, settings: Settings) -> None:
    from dash_helpers import blogauto_status

    blogauto_status(settings)
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote", "args": {"target": "hotdeal"}},
                                      session_headers(bot))
    assert status == 200 and body["code"] == "done"
    s = remote_login.session(bot.db)
    assert s["target"] == "hotdeal" and len(s["pass"]) == 8
    inbox = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert inbox[-1]["login_remote"]["pass"] == s["pass"]
    row = next(i for i in bot.dash_state.items() if i["iid"] == "sys-vnc")
    assert row["primary"]["input"] == "open" and row["primary"]["url"].startswith("/dashboard/vnc/vnc.html?")
    assert f"password={s['pass']}" in row["primary"]["url"] and "path=dashboard%2Fvnc%2Fwebsockify" in row["primary"]["url"]
    assert remote_login.port(bot.db) == remote_login.PORT
    bot.db.kv_set(remote_login.KV, json.dumps(dict(s, at=time.time() - 3600)))  # 지나면 닫힘
    assert remote_login.port(bot.db) is None and not [i for i in bot.dash_state.items() if i["iid"] == "sys-vnc"]
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote", "args": {"target": "rm -rf"}},
                                      session_headers(bot))
    assert body["code"] == "bad_args"
