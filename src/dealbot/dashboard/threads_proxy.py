"""Sns-board 스레드 페이지 ↔ 스레드 자동화 서비스 (주인 10/21 — "sns-board 에서 스레드 페이지를 아예 만들면 되겠네").

스레드 자동화(맞팔·좋아요·AI 댓글·답글·AI 글)는 Blog-Auto 저장소 threads/ 의 따로 도는 서비스(threads-auto, 127.0.0.1:8710)다.
sns-board 는 그 화면을 다시 만들지 않고, 그 서비스의 상태·설정을 그대로 넘겨받아 보여 준다 (화면: static/threads.js).

- GET  /dashboard/api/threads/state     → 그 서비스 /api/state (읽기만 — state 처럼 CSRF 없이, 로그인은 /dashboard 앞부분이 건다)
- GET  /dashboard/api/threads/shot.png  → 그 서비스가 남긴 마지막 화면 사진 (스레드 로그인 확인용)
- POST /dashboard/api/threads/act       → {op, args} — op 는 OPS 에 적은 것만. 대시보드 다른 POST 와 같은 확인
                                          (같은 사이트 + X-Dash-Token, DashApi.guard)
그 서비스는 같은 서버 안에서 온 요청을 X-Threads-Key(그 서비스가 만든 var/internal.key)로 확인한다 — 열쇠 파일이 없거나
서비스가 꺼져 있으면 {"code": "threads_off"} (화면이 '스레드 서비스가 꺼져 있어요'로).
주인이 직접 쓰는 글·예약·댓글 알림·통계(state 의 thpost 칸)는 개발 세션 몫 — 이 파일은 threads/ 서비스 쪽만 잇는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from dealbot.dashboard.api import API, err, jresp

BASE = os.environ.get("THREADS_DASH_URL", "http://127.0.0.1:8710")
KEY_FILE = Path(os.environ.get("THREADS_KEY_FILE", str(Path.home() / "Blog-Auto" / "threads" / "var" / "internal.key")))
PREFIX = f"{API}/threads"
# 화면이 할 수 있는 일 → 그 서비스 경로 (글 직접 올리기는 sns-board thpost 몫이라 넘기지 않는다)
OPS = {"settings": "/api/settings", "run": "/api/run", "pause": "/api/pause", "resume": "/api/resume",
       "tlogin": "/api/tlogin", "tcode": "/api/tcode", "tcheck": "/api/tcheck", "token": "/api/token",
       "manual_done": "/api/manual_done",  # 직접 팔로우할 사람 '했어요' (10/23)
       # X 화면 (주인 10/25, static/x.js) — 설정·승인함 보내기/건너뛰기·지금 찾기
       "x_settings": "/api/x/settings", "x_approve": "/api/x/approve", "x_approve_all": "/api/x/approve_all",
       "x_skip": "/api/x/skip", "x_find": "/api/x/find",
       "x_done": "/api/x/done"}  # 폰 방식 카드 '했어요' (주인 10/26 — 서버는 X 에 접속 안 함)
SLOW_OPS = {"tlogin", "tcode", "tcheck", "token"}  # 브라우저 로그인·토큰 확인은 오래 걸림
BODY_MAX = 16 * 1024
SHOT_MAX = 3_000_000
NO_STORE = {"Cache-Control": "no-store"}


def _key() -> str | None:
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _call(path: str, body: dict[str, Any] | None, timeout: float) -> tuple[int, bytes, str]:
    key = _key()
    if key is None:
        raise ConnectionError("no key")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + path, data=data, method="GET" if body is None else "POST",
                                 headers={"X-Threads-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — 고정된 127.0.0.1 주소
            return r.status, r.read(SHOT_MAX + 1), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(BODY_MAX), e.headers.get("Content-Type", "")


def _off() -> Any:
    return err(503, "threads_off", "스레드 서비스가 꺼져 있어요")


def _json(status: int, raw: bytes) -> Any:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _off()  # 로그인 화면(HTML) 등 — 열쇠가 안 맞는 것
    if status >= 400 and isinstance(obj, dict) and "error" in obj:
        return jresp(status, {"status": "error", "code": "threads", "text": str(obj["error"])[:300]}, NO_STORE)
    return jresp(status, obj, NO_STORE)


class ThreadsProxy:
    def __init__(self, api: Any) -> None:
        self.api = api  # DashApi — POST 확인(guard)을 같이 쓴다

    def register(self, web: Any) -> None:
        web.route(f"{PREFIX}/state", self.state, with_headers=True)
        web.route(f"{PREFIX}/shot.png", self.shot)
        web.route_json(f"{PREFIX}/act", self.act, max_body=BODY_MAX)

    async def state(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        try:
            status, raw, _ = await asyncio.to_thread(_call, "/api/state", None, 10)
        except (OSError, ConnectionError):
            return _off()
        return _json(status, raw)

    async def shot(self, query: dict[str, str]) -> Any:
        try:
            status, raw, ctype = await asyncio.to_thread(_call, "/shot.png", None, 10)
        except (OSError, ConnectionError):
            return _off()
        if status != 200 or not ctype.startswith("image/png") or len(raw) > SHOT_MAX:
            return err(404, "not_found", "화면 사진이 없어요")
        return 200, raw, "image/png", dict(NO_STORE)

    async def act(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = self.api.guard(headers)
        if len(g) == 4:
            return g
        op, args = body.get("op"), body.get("args") or {}
        if op not in OPS or not isinstance(args, dict):
            return err(400, "bad_request", "할 수 없는 일이에요")
        try:
            status, raw, _ = await asyncio.to_thread(_call, OPS[op], args, 170 if op in SLOW_OPS else 20)
        except (OSError, ConnectionError):
            return _off()
        return _json(status, raw)


def register(web: Any, api: Any) -> None:
    """service.register 가 한 줄로 부른다 (DashApi 가 있을 때만)."""
    ThreadsProxy(api).register(web)
