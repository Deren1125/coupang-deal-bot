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


async def test_tunnel_serves_one_request_per_connection() -> None:
    """주인 10/24: 원격 화면이 꾸밈 없이 오류 — 같은 연결로 온 다음 요청(CSS·JS)이 prefix 를 떼지 못해 404 였다.
    웹소켓이 아닌 요청은 양쪽에 Connection: close · 응답이 끝나면 닫음 (다음 요청은 새 연결로 와서 prefix 를 뗌)."""
    seen: list[bytes] = []

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        seen.append(await r.readuntil(b"\r\n\r\n"))
        w.write(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nConnection: keep-alive\r\n\r\ncss")
        await w.drain()  # 닫지 않음 (keep-alive 서버)
        await asyncio.sleep(2)
        w.close()

    up = await asyncio.start_server(handle, "127.0.0.1", 0)
    web = WebServer(0, host="127.0.0.1")
    web.guard_prefix("/dashboard", lambda h: True)
    web.route_tunnel("/dashboard/vnc/", lambda: up.sockets[0].getsockname()[1])
    await web.start()
    port = web._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        data = await _get(port, "/dashboard/vnc/app/styles/base.css", "Connection: keep-alive\r\n")
        assert data.endswith(b"css") and b"Connection: close" in data and b"keep-alive" not in data
        assert seen[0].startswith(b"GET /app/styles/base.css HTTP/1.1") and b"Connection: close" in seen[0]
        assert b"keep-alive" not in seen[0].lower()
    finally:
        await web.stop()
        up.close()


def test_row_closes_once_the_opened_screen_closes(bot: DealBot) -> None:
    """주인 10/24 '원격 화면 열고 로그인했는데 이 할 일이 왜 남아 있어': 화면이 한 번 열렸다가 닫히면(로그인 끝 — 스크립트가 닫음)
    세션을 지워 줄도 사라진다. 아직 한 번도 안 열렸으면(서버가 띄우는 중) 그대로 둔다."""
    at = remote_login.start(bot.db, lambda msg: None, "finance")["at"]
    later = at + remote_login.SEEN_AFTER_S + 10
    assert remote_login.refresh(bot.db, probe=lambda: False)  # 띄우는 중 — 아직 안 열림
    assert not remote_login.refresh(bot.db, at + 2, probe=lambda: True).get("seen")  # 누른 바로 뒤 6080 은 닫히는 중인 예전 창일 수 있음
    assert remote_login.refresh(bot.db, later, probe=lambda: True)["seen"] is True  # 열림
    assert remote_login.refresh(bot.db, later, probe=lambda: True)  # 로그인하는 중
    assert remote_login.refresh(bot.db, later, probe=lambda: False) is None  # 로그인 끝 → 화면 닫힘 → 줄 사라짐
    assert remote_login.session(bot.db) is None and remote_login.port(bot.db) is None


@pytest.mark.asyncio
async def test_pressing_again_on_an_open_naver_screen_reopens_it(bot: DealBot, settings: Settings) -> None:
    """주인 10/27 '창을 껐거나 원격 탭 열기를 한 번 더 누르면 새로 열어 줘': 네이버 창이 떠 있어도 새 부탁(새 비밀번호)을 보낸다 —
    Blog-Auto 로그인 창이 그 부탁을 보고 닫히면 서비스가 다시 켜지며 새 창을 연다."""
    from dash_helpers import blogauto_status

    blogauto_status(settings)
    first = remote_login.start(bot.db, lambda msg: None, "hotdeal")
    bot.db.kv_set(remote_login.KV, json.dumps(dict(first, seen=True)))  # 화면이 떠 있음
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote", "args": {"target": "hotdeal"}},
                                      session_headers(bot))
    assert status == 200 and body["code"] == "done"
    s = remote_login.session(bot.db)
    assert s["pass"] != first["pass"] and not s.get("seen")
    inbox = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert inbox[-1]["login_remote"]["pass"] == s["pass"]


@pytest.mark.asyncio
async def test_cancel_clears_the_session_and_asks_blogauto_to_close(bot: DealBot, settings: Settings) -> None:
    """주인 10/29 '원격화면 열기 한거, 취소할 수 있는 기능': '취소' → 세션을 지워 줄이 바로 빠지고, Blog-Auto 에 취소 한 줄
    (since = 그 창을 부탁한 시각). 스레드 창은 TERM 정리가 생길 때까지 취소하지 않는다."""
    from dash_helpers import blogauto_status

    blogauto_status(settings)
    await post_json(bot, "/dashboard/api/action", {"action": "login.remote", "args": {"target": "issue"}}, session_headers(bot))
    s = remote_login.session(bot.db)
    row = next(i for i in bot.dash_state.items() if i["iid"] == "sys-vnc")
    assert [a["action"] for a in row["more"]][:2] == ["login.remote", "login.remote_cancel"]
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote_cancel", "args": {}}, session_headers(bot))
    assert status == 200 and body["code"] == "done"
    assert remote_login.session(bot.db) is None and not [i for i in bot.dash_state.items() if i["iid"] == "sys-vnc"]
    inbox = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    c = inbox[-1]["login_remote_cancel"]
    assert c["target"] == "issue" and c["since"] == s["at"] and c["at"] >= s["at"]
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote_cancel", "args": {}}, session_headers(bot))
    assert body["code"] == "done" and "없어요" in body["text"]
    remote_login.start(bot.db, lambda msg: None, "threads")
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "login.remote_cancel", "args": {}}, session_headers(bot))
    assert body["code"] == "busy" and remote_login.session(bot.db)


def test_logins_view_offers_cancel_only_for_cancelable_screens(bot: DealBot) -> None:
    from dealbot.dashboard import logins

    remote_login.start(bot.db, lambda msg: None, "toss")
    assert logins.view(bot)["vnc"]["cancel"] is True
    remote_login.start(bot.db, lambda msg: None, "threads_ig")
    assert logins.view(bot)["vnc"]["cancel"] is False
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "src/dealbot/dashboard/static/logins.js").read_text(encoding="utf-8")
    assert "login.remote_cancel" in js and "v.cancel" in js
