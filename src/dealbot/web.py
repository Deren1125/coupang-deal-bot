"""아주 작은 HTTP 서버.

용도:
- 스레드(Threads) OAuth 리디렉션(`/threads/callback?code=...`)을 봇이 직접 받아 코드 복사 없이 연결을 끝낸다.
  메타는 리디렉션 주소로 localhost 를 받지 않고 https 실제 도메인만 받으므로, Railway 공개 도메인을 여기에 연결한다.
- `/health` 응답 (플랫폼 상태 확인용), 카드 이미지·핫딜 페이지.
- `/dashboard` 채널 대시보드 — 경로별 인증(auth)을 붙일 수 있다 (기본 인증, 틀리면 401 + WWW-Authenticate).

의존성 없이 asyncio 로만 만들었고, 요청 한 줄과 헤더만 읽는다. 본문(POST)은 다루지 않는다.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger(__name__)

# (status, html/text) 또는 (status, bytes, content-type) 또는 (status, 본문, content-type|None, 덧붙일 헤더)
Response = tuple[int, str] | tuple[int, bytes, str] | tuple[int, str | bytes, str | None, dict[str, str]]
Handler = Callable[[dict[str, str]], Awaitable[Response]]
AuthCheck = Callable[[dict[str, str]], bool]  # 요청 헤더(이름은 소문자) → 들여보낼지
_STATIC_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}

_STATUS_TEXT = {
    200: "OK", 302: "Found", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 429: "Too Many Requests", 500: "Internal Server Error",
}
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


def parse_headers(lines: list[bytes]) -> dict[str, str]:
    """요청 헤더 줄들 → {소문자 이름: 값}. 같은 이름이 또 오면 첫 값을 쓴다."""
    out: dict[str, str] = {}
    for raw in lines:
        if not raw:
            continue
        name, sep, value = raw.decode("latin-1", "replace").partition(":")
        name = name.strip().lower()
        if sep and name and name not in out:
            out[name] = value.strip()
    return out


def _header_safe(value: str) -> bool:
    return "\r" not in value and "\n" not in value


class WebServer:
    """`routes` 는 경로 → 핸들러. 핸들러는 쿼리 딕셔너리를 받아 (status, html) 을 돌려준다."""

    def __init__(self, port: int, *, host: str = "0.0.0.0") -> None:
        self.port = port
        self.host = host
        self.routes: dict[str, Handler] = {}
        self.prefixes: dict[str, Handler] = {}  # "/d/" 처럼 앞부분만 맞는 경로 → 핸들러 (쿼리에 "path" 로 나머지를 넘김)
        self.static: dict[str, Path] = {}  # URL 앞부분 → 폴더 (카드 이미지 등)
        self.auth: dict[str, AuthCheck] = {}  # 인증이 필요한 경로 → 확인 함수
        self.auth_realm = "dashboard"
        self._server: asyncio.AbstractServer | None = None

    def route(self, path: str, handler: Handler, *, auth: AuthCheck | None = None) -> None:
        """auth 를 주면 그 경로는 확인을 통과한 요청만 핸들러로 간다 (아니면 401)."""
        self.routes[path] = handler
        if auth is not None:
            self.auth[path] = auth
        else:
            self.auth.pop(path, None)

    def route_prefix(self, prefix: str, handler: Handler) -> None:
        self.prefixes[prefix] = handler

    def serve_static(self, prefix: str, directory: Path) -> None:
        self.static[prefix.rstrip("/") + "/"] = Path(directory)

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
            lines = head.split(b"\r\n")
            request_line = lines[0].decode("latin-1", "replace")
            parts = request_line.split(" ")
            if len(parts) < 2:
                raise ValueError("bad request line")
            method, target = parts[0].upper(), parts[1]
            result = await self.dispatch(method, target, parse_headers(lines[1:]))
            status, body = result[0], result[1]
            ctype = result[2] if len(result) >= 3 else None  # type: ignore[misc]
            extra = result[3] if len(result) >= 4 else None  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001 — 서버는 어떤 요청에도 죽지 않는다
            log.debug("web request rejected: %s", e)
            ctype = extra = None
        try:
            await self._respond(writer, status, body, ctype, extra)
        except Exception:  # noqa: BLE001
            pass

    async def dispatch(self, method: str, target: str, headers: dict[str, str] | None = None) -> Response:
        """테스트에서도 바로 부를 수 있게 분리. headers 는 이름을 소문자로 (인증이 필요한 경로만 봄)."""
        split = urlsplit(target)
        path = split.path or "/"
        if path in ("/health", "/healthz"):
            return 200, "ok"
        if method not in ("GET", "HEAD"):
            return 405, page("허용되지 않는 방식", "GET 만 받습니다.")
        for prefix, directory in self.static.items():
            if path.startswith(prefix):
                return self._static(directory, path[len(prefix):])
        query = {k: v for k, v in parse_qsl(split.query, keep_blank_values=True)}
        handler = self.routes.get(path)
        if handler is None:
            for prefix, h in self.prefixes.items():
                if path.startswith(prefix):
                    handler = h
                    query["path"] = path[len(prefix):]
                    break
        if handler is None:
            if path == "/":
                return 200, "ok"
            return 404, page("없는 주소", "이 주소는 쓰지 않습니다.")
        check = self.auth.get(path)
        if check is not None:
            try:
                allowed = bool(check(headers or {}))
            except Exception:  # noqa: BLE001 — 확인이 터지면 들여보내지 않는다
                allowed = False
            if not allowed:
                return 401, page("로그인 필요", "아이디와 비밀번호가 필요합니다."), "text/html; charset=utf-8", {
                    "WWW-Authenticate": f'Basic realm="{self.auth_realm}", charset="UTF-8"',
                }
        try:
            return await handler(query)
        except Exception as e:  # noqa: BLE001
            log.exception("web handler %s failed: %s", path, e)
            return 500, page("오류", html.escape(str(e)))

    @staticmethod
    def _static(directory: Path, name: str) -> Response:
        """폴더 안의 파일 하나만. 하위 경로·숨김 파일·이미지 아닌 확장자는 거절."""
        if not name or "/" in name or name.startswith(".") or Path(name).suffix.lower() not in _STATIC_TYPES:
            return 404, page("없는 파일", "이 주소는 쓰지 않습니다.")
        path = directory / name
        if not path.is_file():
            return 404, page("없는 파일", "파일이 없습니다.")
        return 200, path.read_bytes(), _STATIC_TYPES[path.suffix.lower()]

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: str | bytes,
        ctype: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(body, bytes):
            raw = body
            ctype = ctype or "application/octet-stream"
        else:
            raw = body.encode("utf-8")
            ctype = ctype or ("text/html; charset=utf-8" if raw.startswith(b"<") else "text/plain; charset=utf-8")
        more = dict(headers or {})
        cache = more.pop("Cache-Control", None) or ("public, max-age=86400" if ctype.startswith("image/") else "no-store")
        extra = ""
        # 리디렉션: body 에 목적지 주소 (https://… 또는 같은 서버의 /경로)
        if status in (301, 302, 303, 307) and isinstance(body, str) and body.startswith(("http", "/")) and not body.startswith("//") and _header_safe(body):
            extra = f"Location: {body}\r\n"
        for name, value in more.items():
            if _header_safe(name) and _header_safe(value):  # 줄바꿈이 섞인 값으로 헤더를 끼워 넣지 못하게
                extra += f"{name}: {value}\r\n"
        head = (
            f"HTTP/1.1 {status} {_STATUS_TEXT.get(status, 'OK')}\r\n"
            f"Content-Type: {ctype}\r\n"
            f"Content-Length: {len(raw)}\r\n"
            f"Cache-Control: {cache}\r\n"
            f"{extra}"
            "Connection: close\r\n\r\n"
        ).encode("latin-1", "replace")
        writer.write(head + raw)
        await writer.drain()
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
