"""아주 작은 HTTP 서버.

용도는 둘뿐이다.
- 스레드(Threads) OAuth 리디렉션(`/threads/callback?code=...`)을 봇이 직접 받아 코드 복사 없이 연결을 끝낸다.
  메타는 리디렉션 주소로 localhost 를 받지 않고 https 실제 도메인만 받으므로, Railway 공개 도메인을 여기에 연결한다.
- `/health` 응답 (플랫폼 상태 확인용).

의존성 없이 asyncio 로만 만들었고, 요청 한 줄과 헤더만 읽는다. 본문(POST)은 다루지 않는다.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, str]], Awaitable[tuple[int, str]]]

_STATUS_TEXT = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}
_MAX_HEADER_BYTES = 16 * 1024
_READ_TIMEOUT = 10.0


def page(title: str, body: str) -> str:
    """결과 안내용 최소 HTML. body 는 이미 이스케이프된 텍스트/HTML."""
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:-apple-system,system-ui,sans-serif;margin:2rem;line-height:1.6;max-width:36rem}"
        "h1{font-size:1.25rem}</style></head>"
        f"<body><h1>{html.escape(title)}</h1><p>{body}</p></body></html>"
    )


class WebServer:
    """`routes` 는 경로 → 핸들러. 핸들러는 쿼리 딕셔너리를 받아 (status, html) 을 돌려준다."""

    def __init__(self, port: int, *, host: str = "0.0.0.0") -> None:
        self.port = port
        self.host = host
        self.routes: dict[str, Handler] = {}
        self._server: asyncio.AbstractServer | None = None

    def route(self, path: str, handler: Handler) -> None:
        self.routes[path] = handler

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle, host=self.host, port=self.port)
        sock = self._server.sockets[0] if self._server.sockets else None
        if sock is not None:
            self.port = sock.getsockname()[1]
        log.info("web server listening on %s:%s (routes: %s)", self.host, self.port, ", ".join(sorted(self.routes)) or "-")

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        try:
            await self._server.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, body = 400, page("잘못된 요청", "요청을 읽을 수 없습니다.")
        try:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=_READ_TIMEOUT)
            except asyncio.LimitOverrunError:
                head = b""
            except asyncio.IncompleteReadError as e:
                head = e.partial
            if len(head) > _MAX_HEADER_BYTES or not head:
                raise ValueError("bad request head")
            request_line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = request_line.split(" ")
            if len(parts) < 2:
                raise ValueError("bad request line")
            method, target = parts[0].upper(), parts[1]
            status, body = await self.dispatch(method, target)
        except Exception as e:  # noqa: BLE001 — 서버는 어떤 요청에도 죽지 않는다
            log.debug("web request rejected: %s", e)
        try:
            await self._respond(writer, status, body)
        except Exception:  # noqa: BLE001
            pass

    async def dispatch(self, method: str, target: str) -> tuple[int, str]:
        """테스트에서도 바로 부를 수 있게 분리."""
        split = urlsplit(target)
        path = split.path or "/"
        if path in ("/", "/health", "/healthz"):
            return 200, "ok"
        handler = self.routes.get(path)
        if handler is None:
            return 404, page("없는 주소", "이 주소는 쓰지 않습니다.")
        if method not in ("GET", "HEAD"):
            return 405, page("허용되지 않는 방식", "GET 만 받습니다.")
        query = {k: v for k, v in parse_qsl(split.query, keep_blank_values=True)}
        try:
            return await handler(query)
        except Exception as e:  # noqa: BLE001
            log.exception("web handler %s failed: %s", path, e)
            return 500, page("오류", html.escape(str(e)))

    async def _respond(self, writer: asyncio.StreamWriter, status: int, body: str) -> None:
        raw = body.encode("utf-8")
        ctype = "text/html; charset=utf-8" if raw.startswith(b"<") else "text/plain; charset=utf-8"
        head = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(raw)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(head + raw)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
