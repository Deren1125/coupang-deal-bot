"""스레드 화면 (주인 10/21 — "sns-board 에서 스레드 페이지를 아예 만들면 되겠네"): 블로그 탭 '스레드 →' → threads.js (처음 열 때만),
데이터는 /dashboard/api/threads/* → 같은 서버의 threads/ 서비스 (threads_proxy.py, X-Threads-Key). POST 는 다른 대시보드 POST 와 같은 확인."""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import shell, threads_proxy, webapp

STATIC = Path(webapp.STATIC_DIR)
THREADS_JS_MAX = 16 * 1024
THREADS_JS_GZIP_MAX = 6 * 1024
KEY = "k" * 43


class FakeThreads(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):  # noqa: D401
        pass

    def _send(self, code: int, obj: object, ctype: str = "application/json") -> None:
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.headers.get("X-Threads-Key") != KEY:
            return self._send(200, b"<html>login</html>", "text/html")
        if self.path == "/shot.png":
            return self._send(200, b"\x89PNG fake", "image/png")
        self._send(200, {"username": "oneul_hot_deal", "today": {"follow": {"done": 1, "cap": 10}}})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeThreads.seen.append((self.path, body))
        if self.path == "/api/token":
            return self._send(400, {"error": "이 토큰은 @x 계정 거예요"})
        self._send(200, {"settings": body})


@pytest.fixture
def threads_srv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeThreads)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    key = tmp_path / "internal.key"
    key.write_text(KEY)
    monkeypatch.setattr(threads_proxy, "BASE", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(threads_proxy, "KEY_FILE", key)
    FakeThreads.seen = []
    yield srv
    srv.shutdown()


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def test_threads_js_is_lazy_small_and_safe() -> None:
    js = (STATIC / "threads.js").read_bytes()
    assert len(js) <= THREADS_JS_MAX and len(gzip.compress(js, 9)) <= THREADS_JS_GZIP_MAX
    text = js.decode("utf-8")
    assert "'use strict'" in text and "W.SBThreads = {view, slots}" in text
    assert "innerHTML" not in text and "eval(" not in text and "http://" not in text
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "lazy('meta[name=sb-threads]', 'threads', 'SBThreads')" in app and "'#/blog/threads'" in app
    page = shell.page({})
    assert f'<meta name="sb-threads" content="{shell.asset_url("threads.js")}">' in page


async def test_threads_js_is_served_like_other_app_files(bot: DealBot) -> None:
    res = await bot.web.dispatch("GET", "/dashboard/static/threads.js", {})
    assert res[0] == 200 and res[2].startswith("text/javascript") and res[3]["X-Content-Type-Options"] == "nosniff"


async def test_state_and_shot_pass_through_with_the_internal_key(bot: DealBot, threads_srv) -> None:  # type: ignore[no-untyped-def]
    status, data, hdr = await get_json(bot, "/dashboard/api/threads/state", session_headers(bot))
    assert status == 200 and data["username"] == "oneul_hot_deal" and hdr["Cache-Control"] == "no-store"
    res = await bot.web.dispatch("GET", "/dashboard/api/threads/shot.png", session_headers(bot))
    assert res[0] == 200 and res[2] == "image/png" and res[1].startswith(b"\x89PNG")


async def test_login_is_required(bot: DealBot, threads_srv) -> None:  # type: ignore[no-untyped-def]
    status, data, _ = await get_json(bot, "/dashboard/api/threads/state", {":peer": "127.0.0.1"})
    assert status == 401 and data["code"] == "login"


async def test_act_needs_csrf_and_only_known_ops(bot: DealBot, threads_srv) -> None:  # type: ignore[no-untyped-def]
    status, data, _ = await post_json(bot, "/dashboard/api/threads/act", {"op": "settings", "args": {"mode": "live"}},
                                      session_headers(bot, csrf=False))
    assert status == 403 and data["code"] == "csrf" and FakeThreads.seen == []
    status, data, _ = await post_json(bot, "/dashboard/api/threads/act", {"op": "post", "args": {"text": "x"}}, session_headers(bot))
    assert status == 400 and FakeThreads.seen == []  # 글 직접 올리기는 thpost(개발 세션) 몫
    status, data, _ = await post_json(bot, "/dashboard/api/threads/act", {"op": "settings", "args": {"mode": "live"}}, session_headers(bot))
    assert status == 200 and FakeThreads.seen == [("/api/settings", {"mode": "live"})]


async def test_errors_from_the_service_become_dashboard_errors(bot: DealBot, threads_srv) -> None:  # type: ignore[no-untyped-def]
    status, data, _ = await post_json(bot, "/dashboard/api/threads/act", {"op": "token", "args": {"token": "t" * 30}}, session_headers(bot))
    assert status == 400 and data["code"] == "threads" and "@x" in data["text"]


async def test_service_off_or_key_missing(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(threads_proxy, "KEY_FILE", tmp_path / "none.key")
    status, data, _ = await get_json(bot, "/dashboard/api/threads/state", session_headers(bot))
    assert status == 503 and data["code"] == "threads_off"
    key = tmp_path / "k.key"
    key.write_text(KEY)
    monkeypatch.setattr(threads_proxy, "KEY_FILE", key)
    monkeypatch.setattr(threads_proxy, "BASE", "http://127.0.0.1:9")  # 아무도 안 듣는 포트
    status, data, _ = await get_json(bot, "/dashboard/api/threads/state", session_headers(bot))
    assert status == 503 and data["code"] == "threads_off"


async def test_wrong_key_is_treated_as_off(bot: DealBot, threads_srv, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    bad = tmp_path / "bad.key"
    bad.write_text("wrong")
    monkeypatch.setattr(threads_proxy, "KEY_FILE", bad)
    status, data, _ = await get_json(bot, "/dashboard/api/threads/state", session_headers(bot))
    assert status == 503 and data["code"] == "threads_off"
