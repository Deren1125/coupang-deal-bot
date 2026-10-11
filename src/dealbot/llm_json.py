"""JSON 으로만 답하게 묻는 작은 AI 호출기 (관리자 챗 '말 명령' 해석용).

정보 글 요약기(summarize.InfoSummarizer)와 같은 방식: ANTHROPIC_API_KEY 가 있으면 API, 없으면 서버에 로그인된 claude CLI.
둘 다 답을 JSON 스키마에 맞춰 받는다 (API 는 output_config.format, CLI 는 --json-schema 의 structured_output).
실패는 모두 LLMError 로 — 부르는 쪽은 아무것도 실행하지 않고 명령어를 안내한다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Any

import anthropic

from dealbot import commentary  # find_claude 는 모듈째로 불러 쓴다 (테스트가 갈아 끼울 수 있게)
from dealbot.summarize import _CLI_AUTH, CLI_AUTH_RETRY_SECONDS

log = logging.getLogger(__name__)


class LLMError(Exception):
    """AI 가 답하지 않음 (오류·시간 초과·읽을 수 없는 답·거절). 메시지는 관리자에게 보여 줄 짧은 설명."""


class JsonAsker:
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str,
        cli_bin: str | None = None,
        cli_model: str | None = None,
        timeout: float = 30,
        effort: str = "low",
        max_tokens: int = 2000,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.cli_bin = cli_bin if cli_bin is not None else (None if api_key else commentary.find_claude())
        self.cli_model = cli_model  # None 이면 --model 을 넘기지 않고 CLI 기본 모델
        self.timeout = timeout
        self.effort = effort
        self.max_tokens = max_tokens
        self.last_backend: str | None = None
        self.disabled_reason: str | None = None  # 키·모델 오류처럼 다시 해도 안 되는 것
        self._cli_down_until = 0.0
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def backend(self) -> str | None:
        if self.api_key:
            return "api"
        if self.cli_bin:
            return "cli"
        return None

    @property
    def configured(self) -> bool:
        return self.backend is not None

    async def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise LLMError("AI 없음 (ANTHROPIC_API_KEY·서버 claude CLI 둘 다 없음)")
        if self.disabled_reason:
            raise LLMError(self.disabled_reason)
        if self.backend == "cli":
            if time.monotonic() < self._cli_down_until:
                raise LLMError("서버 claude CLI 로그인이 풀림")
            data = await self._ask_cli(system, user, schema)
        else:
            data = await self._ask_api(system, user, schema)
        self.last_backend = self.backend
        return data

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout, max_retries=1)
        return self._client

    async def _ask_api(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._get_client().messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # 규칙·명령 목록은 매번 같다 → 캐시
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}, "effort": self.effort},
            )
        except anthropic.AuthenticationError as e:
            self.disabled_reason = "ANTHROPIC_API_KEY 가 올바르지 않음"
            raise LLMError(self.disabled_reason) from e
        except anthropic.PermissionDeniedError as e:
            self.disabled_reason = f"API 키 권한 부족 ({e.message})"
            raise LLMError(self.disabled_reason) from e
        except anthropic.NotFoundError as e:
            self.disabled_reason = f"모델 이름이 잘못됨 ({self.model})"
            raise LLMError(self.disabled_reason) from e
        except anthropic.RateLimitError as e:
            raise LLMError("API 요청 한도 초과") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API 오류 {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("API 연결 실패") from e
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"{type(e).__name__}: {e}") from e
        stop = getattr(response, "stop_reason", None)
        if stop in ("refusal", "max_tokens"):
            raise LLMError(f"답을 받지 못함 ({stop})")
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return _loads(text)

    async def _ask_cli(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        args = [str(self.cli_bin), "-p", "--output-format", "json"]
        if self.cli_model:
            args += ["--model", self.cli_model]
        args += ["--system-prompt", system, "--json-schema", json.dumps(schema, ensure_ascii=False), "--tools", ""]
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=os.environ | {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
            )
            out, err = await asyncio.wait_for(proc.communicate(user.encode()), timeout=self.timeout)
        except TimeoutError as e:
            raise LLMError(f"claude CLI 시간 초과 ({self.timeout:.0f}초)") from e
        except OSError as e:
            raise LLMError(f"claude CLI 실행 실패 ({e.strerror or e})") from e
        finally:
            if proc is not None and proc.returncode is None:  # 시간 초과·취소 때 CLI 가 남지 않게 (요약기와 같음)
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
        try:
            payload = json.loads(out.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = None
        if isinstance(payload, list):
            payload = next((m for m in reversed(payload) if isinstance(m, dict) and m.get("type") == "result"), None)
        if not isinstance(payload, dict) or not payload:
            log.warning("nl cli: unreadable output (exit %s): %s", proc.returncode if proc else "?", err.decode("utf-8", "replace")[:200])
            raise LLMError("claude CLI 응답을 읽지 못함")
        if payload.get("is_error"):
            detail = str(payload.get("result") or payload.get("subtype") or "")[:200]
            if _CLI_AUTH.search(detail):
                self._cli_down_until = time.monotonic() + CLI_AUTH_RETRY_SECONDS
                raise LLMError("서버 claude CLI 로그인이 풀림")
            raise LLMError(f"claude CLI 오류 ({detail[:80] or '내용 없음'})")
        data = payload.get("structured_output")
        if isinstance(data, dict):
            return data
        return _loads(str(payload.get("result") or ""))


def _loads(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    start, end = t.find("{"), t.rfind("}")
    try:
        data = json.loads(t[start : end + 1]) if start >= 0 and end > start else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise LLMError("AI 답이 JSON 이 아님")
    return data
