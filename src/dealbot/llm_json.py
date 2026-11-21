"""JSON 으로만 답하게 묻는 작은 AI 호출기 (관리자 챗 '말 명령' 해석용).

정보 글 요약기(summarize.InfoSummarizer)와 같은 방식: ANTHROPIC_API_KEY 와 API 모델 이름이 있으면 API, 아니면 서버에 로그인된 claude CLI.
둘 다 답을 JSON 스키마에 맞춰 받는다 (API 는 output_config.format, CLI 는 --json-schema 의 structured_output).
실패는 모두 LLMError 로 — 부르는 쪽은 아무것도 실행하지 않고 명령어를 안내한다.
"""

from __future__ import annotations

import asyncio  # noqa: F401 — 시험이 llm_json.asyncio.create_subprocess_exec 를 갈아 끼움 (실행은 cli_env.spawn)
import json
import logging
import re
import time
from typing import Any

import anthropic

from dealbot import (  # find_claude 는 모듈째로 불러 쓴다 (테스트가 갈아 끼울 수 있게)
    api_credit,
    cli_env,
    commentary,
)
from dealbot.config import posted_api_model, posted_cli_model
from dealbot.summarize import _CLI_AUTH, CLI_AUTH_RETRY_SECONDS

log = logging.getLogger(__name__)

# claude CLI 사용량 한도 문구 (Blog-Auto llm_router.LIMIT_HINTS 와 같은 말들 — 예 "You've hit your weekly limit · resets 9pm (Asia/Seoul)")
_CLI_LIMIT = re.compile(r"usage limit|rate limit|limit reached|hit your (?:session |weekly )?limit|(?:session|weekly|5-hour) limit|"
                        r"resource_exhausted|quota|resets\s", re.I)
LIMIT_TAG = "claude CLI 한도"  # LLMError 글 머리 — 부르는 쪽(xstudio.ai_error)이 이 말로 한도인지 안다


def limit_text(detail: str) -> str:
    """한도 문구에서 '풀리는 때' 부분만 (예 'resets 9pm (Asia/Seoul)') — 화면에 보일 말을 만들 때 쓴다. 없으면 ''."""
    m = re.search(r"resets?\s+[^·|\n]{1,40}", detail or "", re.I)
    return m.group(0).strip()[:60] if m else ""


class LLMError(Exception):
    """AI 가 답하지 않음 (오류·시간 초과·읽을 수 없는 답·거절). 메시지는 관리자에게 보여 줄 짧은 설명."""


class JsonAsker:
    def __init__(
        self,
        api_key: str | None,
        *,
        model: str | None = None,
        cli_bin: str | None = None,
        cli_model: str | None = None,
        timeout: float = 30,
        effort: str = "low",
        max_tokens: int = 2000,
        task: str | None = None,
        posted: bool = True,
    ) -> None:
        # task: API 크레딧 일 이름 (주인 11/21 — 'xstudio'·'nl' …). 주면 키(.env CLAUDE_API_CREDIT_KEY)·예산이 될 때 API 먼저, 안 되면 CLI.
        # posted: 올라가는 글이면 API 에서도 Fable·Mythos·Haiku 안 씀 (→ Sonnet)
        self.task = task
        self.posted = posted
        self.api_key = api_key
        model = posted_api_model(model, where="llm_json")
        self.model = model  # API 모델 이름 (서버 .env). 없거나 Fable 이면 키가 있어도 API 는 안 씀
        # API 가 있어도 찾아 둔다 — 예산·오류 때 CLI 로 (주인 11/21)
        self.cli_bin = cli_bin if cli_bin is not None else commentary.find_claude()
        # 늘 --model 을 넘긴다 (주인 11/14 — config.posted_cli_model: 비었거나 Fable 이면 CLI 별칭 'sonnet') · 칸은 정해 준 이름 그대로 (없으면 None)
        self.cli_model = posted_cli_model(cli_model, where="llm_json") if cli_model else None
        self.timeout = timeout
        self.effort = effort
        self.max_tokens = max_tokens
        self.last_backend: str | None = None
        self.last_cli_models: list[str] = []  # 마지막 CLI 답이 실제로 쓴 모델 이름들 (CLI 가 알려 준 modelUsage — 없으면 빈 목록)
        self.disabled_reason: str | None = None  # 키·모델 오류처럼 다시 해도 안 되는 것
        self._cli_down_until = 0.0
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def backend(self) -> str | None:
        if self.api_key and self.model:
            return "api"
        if self.cli_bin:
            return "cli"
        return None

    @property
    def configured(self) -> bool:
        return self.backend is not None

    async def ask(self, system: str, user: str, schema: dict[str, Any], *, images: list[bytes] | None = None) -> dict[str, Any]:
        """images = JPEG 바이트 목록 (사진을 보고 답해야 할 때 — dashboard/xphoto.py). 없으면 예전 그대로."""
        if not self.configured:
            raise LLMError("AI 없음 (ANTHROPIC_API_KEY·서버 claude CLI 둘 다 없음)")
        if self.disabled_reason:
            raise LLMError(self.disabled_reason)
        prov = api_credit.provider(self.task) if self.task else "cli"
        if prov == "gemini" and not images and api_credit.gemini_allow(self.task or "")[0]:
            try:  # B 층 (주인 11/22 — 분류·경로는 Gemini 싼 모델): 안 되면 Claude API → CLI
                data = await api_credit.ask_gemini(self.task or "", system, user, schema, timeout=max(self.timeout, 30))
                self.last_backend, self.last_cli_models = "gemini", []
                return data
            except api_credit.ApiError as e:
                log.warning("llm_json %s gemini → Claude: %s", self.task, e)
        if self.backend == "api":  # .env 키 + API 모델 (예전 길) — 예산 안에서만 (CLI 가 없으면 그대로)
            if api_credit.allow(self.task or "nl", key=self.api_key)[0] or not self.cli_bin:
                data = await self._ask_api(system, user, schema, images)
                self.last_backend = "api"
                return data
        elif prov in ("api", "gemini") and api_credit.allow(self.task or "")[0]:
            # Claude API (주인 11/21·11/22): CLI 와 같은 모델로 API 먼저 · 안 되면 CLI
            model = api_credit.api_model(self.cli_model, posted=self.posted)
            try:
                data = await api_credit.ask(self.task or "", model, system, user, schema=schema, images=images, effort=self.effort,
                                            max_tokens=max(self.max_tokens, 8000), timeout=max(self.timeout, 60))
                self.last_backend, self.last_cli_models = "api", [model]
                return data
            except api_credit.ApiError as e:
                log.warning("llm_json %s api → CLI: %s", self.task, e)
        if time.monotonic() < self._cli_down_until:
            raise LLMError("서버 claude CLI 로그인이 풀림")
        data = await self._ask_cli(system, user, schema, images)
        self.last_backend = "cli"
        return data

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout, max_retries=1)
        return self._client

    async def _ask_api(self, system: str, user: str, schema: dict[str, Any], images: list[bytes] | None = None) -> dict[str, Any]:
        content: Any = [*image_blocks(images), {"type": "text", "text": user}] if images else user
        try:
            response = await self._get_client().messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                # 규칙·명령 목록은 매번 같다 → 캐시
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
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
        api_credit.record(self.task or "nl", str(getattr(response, "model", "") or self.model), getattr(response, "usage", None))
        stop = getattr(response, "stop_reason", None)
        if stop in ("refusal", "max_tokens"):
            raise LLMError(f"답을 받지 못함 ({stop})")
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        return _loads(text)

    async def _ask_cli(self, system: str, user: str, schema: dict[str, Any], images: list[bytes] | None = None) -> dict[str, Any]:
        self.last_cli_models = []
        args = [str(self.cli_bin), "-p", "--output-format", "json", "--model", posted_cli_model(self.cli_model, where="llm_json")]
        args += ["--system-prompt", system, "--json-schema", json.dumps(schema, ensure_ascii=False), "--tools", ""]
        if images:  # 사진은 stream-json 입력으로만 넘길 수 있다 (도구는 그대로 없음 — 파일을 읽게 하지 않는다). 답은 줄마다 JSON, 끝 줄이 result
            args[2:4] = ["--output-format", "stream-json", "--input-format", "stream-json", "--verbose"]
            user = json.dumps({"type": "user", "message": {"role": "user", "content": [*image_blocks(images), {"type": "text", "text": user}]}},
                              ensure_ascii=False) + "\n"
        try:  # .env 구독 토큰이 끊겼으면 토큰 없이 한 번 더 (서버 claude 로그인 — cli_env, 11/18)
            rc, out, err = await cli_env.run(args, user.encode(), self.timeout)
        except TimeoutError as e:
            raise LLMError(f"claude CLI 시간 초과 ({self.timeout:.0f}초)") from e
        except OSError as e:
            raise LLMError(f"claude CLI 실행 실패 ({e.strerror or e})") from e
        try:
            payload = json.loads(out.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = _stream_rows(out.decode("utf-8", "replace")) if images else None
        if isinstance(payload, list):
            payload = next((m for m in reversed(payload) if isinstance(m, dict) and m.get("type") == "result"), None)
        if not isinstance(payload, dict) or not payload:
            raw = (out.decode("utf-8", "replace") + "\n" + err.decode("utf-8", "replace"))[:2000]
            if _CLI_LIMIT.search(raw):  # 한도면 CLI 가 JSON 대신 한 줄만 낼 때가 있다
                raise LLMError(f"{LIMIT_TAG} ({limit_text(raw) or '풀리는 때 모름'})")
            if cli_env.AUTH_HINTS.search(raw):  # 인증 오류를 JSON 없이 한 줄로 낼 때 — '읽지 못함' 대신 로그인 풀림으로
                self._cli_down_until = time.monotonic() + CLI_AUTH_RETRY_SECONDS
                raise LLMError("서버 claude CLI 로그인이 풀림")
            log.warning("nl cli: unreadable output (exit %s): %s", rc, err.decode("utf-8", "replace")[:200])
            raise LLMError("claude CLI 응답을 읽지 못함")
        usage = payload.get("modelUsage")
        self.last_cli_models = [str(k)[:80] for k in usage] if isinstance(usage, dict) else []
        if payload.get("is_error"):
            detail = str(payload.get("result") or payload.get("subtype") or "")[:200]
            if _CLI_AUTH.search(detail):
                self._cli_down_until = time.monotonic() + CLI_AUTH_RETRY_SECONDS
                raise LLMError("서버 claude CLI 로그인이 풀림")
            if _CLI_LIMIT.search(detail):
                raise LLMError(f"{LIMIT_TAG} ({limit_text(detail) or '풀리는 때 모름'})")
            raise LLMError(f"claude CLI 오류 ({detail[:80] or '내용 없음'})")
        data = payload.get("structured_output")
        if isinstance(data, dict):
            return data
        return _loads(str(payload.get("result") or ""))


def image_blocks(images: list[bytes] | None) -> list[dict[str, Any]]:
    """JPEG 바이트 → 메시지 그림 칸."""
    import base64

    return [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(b).decode("ascii")}} for b in images or []]


def _stream_rows(text: str) -> list[Any] | None:
    """stream-json 출력(줄마다 JSON) → 목록 (읽을 줄이 없으면 None)."""
    rows = []
    for ln in text.splitlines():
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return rows or None


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
