"""claude CLI 를 부를 때 쓰는 환경 — .env 구독 토큰이 끊겼을 때 서버 claude 로그인으로 한 번 더 (11/18).

11/17 23:52 .env 의 CLAUDE_CODE_OAUTH_TOKEN 이 'OAuth token revoked' 로 끊김 → 그 변수를 물려받은 claude CLI 가 모두 인증 오류
(X 내 주제로 글 쓰기·한줄평·요약 …). 서버 ~/.claude 로그인은 그대로 됨. Blog-Auto llm_router(52cae50) 와 같은 길:
인증 오류면 그 변수만 빼고 같은 부탁을 한 번 더 → 되면 24시간 동안 변수를 빼고 부른다 (이 프로세스 메모리 · 경고는 하루 한 번).
둘 다 안 되면 첫 결과 그대로 (부르는 쪽이 '로그인 풀림'으로 보여 줌). 토큰 값은 어디에도 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

ENV_TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
DROP_S = 24 * 3600
AUTH_HINTS = re.compile(r"oauth token revoked|invalid_grant|authentication_error|invalid api key|failed to authenticate"
                        r"|api error:?\s*401|\b401\b[^\n]{0,40}unauthori|unauthori[^\n]{0,40}\b401\b", re.I)
_drop: dict[str, Any] = {"until": 0.0, "warned": ""}

Result = tuple[int | None, bytes, bytes]


def dropped(now: float | None = None) -> bool:
    """지금 .env 구독 토큰을 빼고 부르는 중인가 (24시간 기억)."""
    return (time.time() if now is None else now) < float(_drop["until"] or 0)


def without_token(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k != ENV_TOKEN}


def cli_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """claude CLI 에 넘길 환경: os.environ + extra (토큰이 끊긴 걸 본 뒤 24시간은 토큰 빼고)."""
    env = os.environ | {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"} | (extra or {})
    return without_token(env) if dropped() else env


def _result_event(out: bytes) -> dict[str, Any] | None:
    """CLI 출력의 마지막 결과 (json 한 덩어리·목록 또는 stream-json 의 마지막 type=result 줄)."""
    text = (out or b"").decode("utf-8", "replace").strip()
    try:
        ev = json.loads(text)
    except ValueError:
        ev = None
        for line in reversed(text.splitlines()):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("type") == "result":
                ev = row
                break
    if isinstance(ev, list):
        ev = next((m for m in reversed(ev) if isinstance(m, dict) and m.get("type") == "result"), None)
    return ev if isinstance(ev, dict) else None


def auth_failed(res: Result) -> bool:
    """CLI 가 실패했고(종료 코드·결과 is_error) 그 까닭이 인증(토큰 끊김·401)인가."""
    rc, out, err = res
    ev = _result_event(out)
    if ev is not None:
        if not (ev.get("is_error") or rc not in (0, None)):
            return False
        text = f"{ev.get('result') or ''}\n{(err or b'').decode('utf-8', 'replace')}"
    else:
        text = (out or b"").decode("utf-8", "replace") + "\n" + (err or b"").decode("utf-8", "replace")
    return bool(AUTH_HINTS.search(text))


def _ok(res: Result) -> bool:
    ev = _result_event(res[1])
    return res[0] == 0 and ev is not None and not ev.get("is_error")


def _mark(now: float | None = None) -> None:
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    if _drop["warned"] != day:
        log.warning(".env 구독 토큰 끊김 → 서버 claude 로그인으로 씀 (24시간)")
        _drop["warned"] = day
    _drop["until"] = now + DROP_S


async def with_fallback(call: Callable[[dict[str, str]], Awaitable[Result]], env: dict[str, str]) -> Result:
    """call(env) 한 번. 토큰을 넘겼는데 인증 오류면 토큰 없이 한 번 더 — 되면 24시간 기억. 안 되면 첫 결과 그대로.
    call 이 내는 TimeoutError·OSError 는 첫 번째엔 그대로 올라가고, 두 번째엔 첫 결과로 돌아간다."""
    has = bool(env.get(ENV_TOKEN))
    if has and dropped():
        env, has = without_token(env), False
    first = await call(env)
    if not (has and auth_failed(first)):
        return first
    try:
        retry = await call(without_token(env))
    except (TimeoutError, OSError) as e:
        log.warning("claude CLI 토큰 없이 다시 부르기 실패: %s", type(e).__name__)
        return first
    if _ok(retry):
        _mark()
        return retry
    log.warning("claude CLI: .env 토큰도 서버 로그인도 인증 실패")
    return first


async def spawn(args: list[str], stdin: bytes, timeout: float, env: dict[str, str]) -> Result:
    """claude CLI 한 번 (시간 초과는 TimeoutError — 그때·취소 때 CLI 가 남지 않게 죽임). 실행 실패는 OSError."""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        return proc.returncode, out or b"", err or b""
    finally:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)


async def run(args: list[str], stdin: bytes, timeout: float, extra: dict[str, str] | None = None) -> Result:
    """spawn + 끊긴 토큰 다시 부르기."""
    return await with_fallback(lambda env: spawn(args, stdin, timeout, env), cli_env(extra))
