"""이벤트 참여(쌀먹) LLM 대리 호출 (주인 11/15 "blog-auto 랑 shorts-auto 가 gemini·claude api 쓰고 있을 거야, 거기서 접근 권한 나눠 받아").
키 값을 주고받지 않게 Sns-board 가 쌀먹 대신 부른다 — 대리 검색(ssalmuk.py search)과 같은 인증·한도·로그 원칙.

  POST /dashboard/api/ssalmuk/llm   (로그인 없이 · X-Ssalmuk-Key · 몸 32KB)
  ① Claude — 서버에 Anthropic API 키는 없고 claude CLI(주인 구독)뿐:
     몸 {"provider":"claude", "model":"haiku"|"sonnet", "system"?, "messages"?:[{"role":"user"|"assistant","content":"…"}] | "prompt"?:"…",
         "max_tokens"?(1~2000, 기본 1000), "json"?:bool}
     → 200 {"ok":true, "text":"…", "model_used":"…"}   (json:true 면 text 는 JSON 글 — 아니면 502 bad_json)
     Opus·Fable·Mythos·모르는 이름은 400 bad_model (쌀먹 몫은 Haiku·Sonnet 만). 실제로 쓴 모델(CLI modelUsage)이 Haiku·Sonnet 이 아니거나
     (Fable·Mythos·Opus·모르는 이름) 알 수 없으면 답을 버리고 503 ai_off. 시간 초과 60초 (차례 기다림 포함). CLI 는 쌀먹 몫으로 한 번에 1개 —
     15초 기다려도 차례가 안 오면 429 busy. 서버 여유 메모리가 300MB 아래면 CLI 를 띄우지 않고 429 busy (횟수는 안 셈).
     CLI 는 빈 폴더에서 · 도구·MCP·슬래시 명령 없이 · 대화 기록을 남기지 않고 돈다 (글은 표준 입력으로만 — 옵션으로 읽히지 않음).
  ② Gemini — 서버에 GEMINI_API_KEY 가 있을 때만 (딜봇 환경, 없으면 Blog-Auto .env 의 같은 이름 한 줄 — BlogKeyFile):
     몸 {"provider":"gemini"(없어도 됨), "model":"gemini-…"(허용 목록), "request": <generateContent 몸 그대로>}  ("v"·"purpose" 는 받아도 안 봄)
     → 200 {"ok":true, "model":"…", "response": <generateContent 응답 그대로>}
     generateContent 만 · 키는 헤더 x-goog-api-key 로만 (주소에 안 실음) · 몸은 generationConfig.maxOutputTokens 상한만 손댐 (tools·systemInstruction 은 더하지도 바꾸지도 않음).
  오류: 401 bad_key · 400 bad_body|bad_model|pii · 413 too_big · 429 rate|limit|busy · 503 no_key|no_cli|cli_login|ai_off ·
        502 gemini_error{status}|claude_error|bad_json
  한도 (설정 dashboard.ssalmuk_llm_*): 하루(KST) Gemini 40 · Claude 150 (그중 Sonnet 50) · 1분 10회 · 몸 32KB. 넘으면 429 limit.
  개인정보: 몸의 글 어디든 전화번호(010-…·+82)·이메일 꼴이 있으면 400 pii — 부르지 않음 (쌀먹이 걸러 보내지만 이중으로).
  프롬프트·응답 글은 로그·kv 에 남기지 않는다 — 해시 앞 8자·글자 수·모델·상태만. 키 값도.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import signal
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LLM_PATH = "/dashboard/api/ssalmuk/llm"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODEL_RE = re.compile(r"^gemini-[a-z0-9][a-z0-9.\-]{0,48}$")
CLAUDE_MODELS = ("haiku", "sonnet")  # CLI 별칭으로만 (날짜 붙은 이름은 받지 않음)
CLAUDE_BANNED_USED_RE = re.compile(r"fable|mythos|opus", re.I)  # 실제로 쓴 모델에 이게 있으면 답을 버림
CLAUDE_OK_USED_RE = re.compile(r"haiku|sonnet", re.I)  # 실제로 쓴 모델은 전부 이 둘 가운데여야 함 (모르는 이름·빈 목록도 버림)
ALIAS_ENV_RE = re.compile(r"^ANTHROPIC_(?:DEFAULT_[A-Z0-9_]*(?:HAIKU|SONNET)[A-Z0-9_]*MODEL|SMALL_FAST_MODEL)$")  # haiku·sonnet 별칭을 푸는 환경 변수
MEM_MIN_MB = 300  # 여유 메모리(MemAvailable)가 이보다 적으면 CLI(노드, 수백 MB)를 띄우지 않음 — 모르면(리눅스 아님) 그냥 진행
# CLI 가 프로젝트 CLAUDE.md·.mcp.json·명령을 읽지 않게 · 프롬프트가 대화 기록 파일로 남지 않게 (--tools "" 는 맨 끝 — 여러 값 옵션)
CLI_FLAGS = ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence")
TIMEOUT = 60.0
BUSY_WAIT = 15.0
MAX_TOKENS_MAX = 2000
MAX_TOKENS_DEFAULT = 1000
MESSAGES_MAX = 40
DEFAULT_SYSTEM = "질문에 짧고 정확하게 답한다. 도구는 쓰지 않는다."
JSON_SUFFIX = "\n\n답은 JSON 하나만 낸다 (설명·코드 울타리 없이)."
# 개인정보 꼴 (쌀먹 assertNoPersonal 과 이중으로): 휴대폰 010-1234-5678 · 01012345678 · 010 - 1234 - 5678 · 010)1234-5678 · +82 10… · 이메일
# 전각 숫자·기호는 반각으로 바꾸고 폭 없는 글자는 뺀 뒤 봄. 유선 번호(02-…·1588-…)·'+82점' 같은 글은 안 막음 (주최 문의 번호는 쌀먹이 지움).
_SEP = r"[\s.\-)]{0,3}"
PHONE_RE = re.compile(rf"(?<!\d)01[016789]{_SEP}\d{{3,4}}{_SEP}\d{{4}}(?!\d)|\+\s?82{_SEP}\(?0?\)?{_SEP}1[016789]{_SEP}\d{{3,4}}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")  # 띄어 쓴 '팔로우 @이름.kr' 은 안 막음
_INVISIBLE_RE = re.compile("[\u00ad\u200b-\u200f\u2060\ufeff]")


def has_pii(v: Any, _depth: int = 0) -> bool:
    """몸 안의 모든 글(키 포함)에 전화번호·이메일 꼴이 있는지."""
    if isinstance(v, str):
        v = _INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", v))
        return bool(PHONE_RE.search(v) or EMAIL_RE.search(v))
    if _depth > 40:
        return True  # 너무 깊은 몸은 보지 못함 → 막음
    if isinstance(v, dict):
        return any(has_pii(k, _depth + 1) or has_pii(x, _depth + 1) for k, x in v.items())
    if isinstance(v, list):
        return any(has_pii(x, _depth + 1) for x in v)
    return False


def _chars(v: Any) -> int:
    if isinstance(v, str):
        return len(v)
    if isinstance(v, dict):
        return sum(_chars(x) for x in v.values())
    if isinstance(v, list):
        return sum(_chars(x) for x in v)
    return 0


def _h8(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]


def claude_prompt(body: dict[str, Any]) -> str | None:
    """messages 또는 prompt → CLI 에 넘길 글 하나. 꼴이 이상하면 None."""
    msgs, prompt = body.get("messages"), body.get("prompt")
    if msgs is None:
        return prompt.strip() if isinstance(prompt, str) and prompt.strip() else None
    if prompt is not None or not isinstance(msgs, list) or not 1 <= len(msgs) <= MESSAGES_MAX:
        return None
    rows: list[tuple[str, str]] = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str) or not m["content"].strip():
            return None
        rows.append((m["role"], m["content"].strip()))
    if rows[-1][0] != "user":
        return None
    if len(rows) == 1:
        return rows[0][1]
    return "\n\n".join(f"[{'사용자' if r == 'user' else '도우미'}]\n{t}" for r, t in rows) + "\n\n위 대화의 마지막 [사용자] 말에 답한다."


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").removeprefix("json").strip()
    return t


def alias_problem(env: Any = None) -> str:
    """haiku·sonnet 별칭을 Opus·Fable·Mythos 로 돌려놓는 환경 변수 이름 (없으면 ''). xstudio.alias_env_problem 은 Fable 쪽만 봄."""
    env = os.environ if env is None else env
    return next((k for k, v in env.items() if ALIAS_ENV_RE.match(k) and CLAUDE_BANNED_USED_RE.search(str(v))), "")


def mem_low() -> bool:
    from dealbot.store_browser import mem_available_mb

    avail = mem_available_mb()
    return avail is not None and avail < MEM_MIN_MB


def workdir() -> str | None:
    """CLI 를 돌릴 빈 폴더 (~/.cache/ssalmuk-llm, 안 되면 임시 폴더) — 딜봇 저장소의 CLAUDE.md·.mcp.json·.claude 를 읽지 않게."""
    d = Path.home() / ".cache" / "ssalmuk-llm"
    try:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not d.is_symlink() and d.stat().st_uid == os.getuid() and not any(d.iterdir()):
            return str(d)
    except OSError:
        pass
    try:
        return tempfile.mkdtemp(prefix="ssalmuk-llm-")
    except OSError:
        return None


def _kill(proc: Any) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # CLI 가 띄운 것까지 (start_new_session — 무리 번호 = pid)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def run_cli(args: list[str], stdin: bytes, timeout: float, env: dict[str, str]) -> tuple[int | None, bytes, bytes]:
    """claude CLI 한 번 (시험은 이 함수를 갈아 끼움). 시간 초과는 TimeoutError, 실행 실패는 OSError."""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            cwd=workdir(), start_new_session=True)
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout=timeout)
        return proc.returncode, out, err
    finally:
        if proc is not None and proc.returncode is None:  # 시간 초과·취소 때 CLI 가 남지 않게
            _kill(proc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)


def register(web: Any, api: Any, desk: Any, machine: Any, blog_keys: Any, _j: Any) -> None:
    """ssalmuk.register 가 부른다. desk = Ssalmuk (하루 셈) · machine = X-Ssalmuk-Key 확인 · blog_keys = BlogKeyFile."""
    from dealbot.dashboard.actions import RateWindow

    cfg = api.bot.settings.dashboard
    desk.llm_caps = {"gemini": int(cfg.ssalmuk_llm_gemini_per_day), "claude": int(cfg.ssalmuk_llm_claude_per_day),
                     "sonnet": int(cfg.ssalmuk_llm_sonnet_per_day)}
    minute = RateWindow(int(cfg.ssalmuk_llm_per_minute), 60.0)  # 키 하나당 (손님 주소와 상관없이 · Gemini·Claude 합쳐서)
    sem = asyncio.Semaphore(1)  # 딜봇·블로그 본업의 CLI 와 겹치지 않게 쌀먹 몫은 한 번에 1개 (딜봇엔 CLI 공용 세마포어가 없음)
    rt = {"cli_down_until": 0.0}
    limit = _j(429, {"code": "limit", "text": "LLM 한도(1분·하루)를 넘었어요"})

    def bad(code: str, text: str) -> Any:
        return _j(400, {"ok": False, "code": code, "text": text})

    def note(provider: str, model: str, status: Any, body: Any, out_chars: int, t0: float) -> None:
        # 글은 남기지 않음 — 해시·글자 수·모델·상태만
        log.info("ssalmuk llm: %s model=%s status=%s in=%d out=%d h=%s ms=%d", provider, model[:40], status, _chars(body), out_chars,
                 _h8(body), int((time.monotonic() - t0) * 1000))

    async def gemini(body: dict[str, Any]) -> Any:
        import httpx

        model, req = body.get("model"), body.get("request")
        if not isinstance(model, str) or not GEMINI_MODEL_RE.match(model) or model not in cfg.ssalmuk_llm_gemini_models:
            return bad("bad_model", "허용된 gemini-* 모델만 받아요")
        if not isinstance(req, dict) or not isinstance(req.get("contents"), list) or not req["contents"]:
            return bad("bad_body", "request 에 generateContent 몸(contents)이 필요해요")
        key = (getattr(api.bot.settings.secrets, "gemini_api_key", None) or "").strip() or blog_keys.gemini()
        if not key:
            return _j(503, {"code": "no_key", "text": "Sns-board 에 Gemini 키가 없어요"})
        if not minute.hit("key") or not desk.llm_day_hit("gemini"):
            return limit
        cap = int(cfg.ssalmuk_llm_gemini_max_output_tokens)
        gen = req.get("generationConfig")
        if gen is None or isinstance(gen, dict):
            cur = (gen or {}).get("maxOutputTokens")
            if not isinstance(cur, int) or isinstance(cur, bool) or cur > cap or cur < 1:
                req = {**req, "generationConfig": {**(gen or {}), "maxOutputTokens": cap}}
        t0 = time.monotonic()
        try:
            resp = await api.bot.http.post(GEMINI_URL.format(model=model), json=req, timeout=TIMEOUT,
                                           headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            status = resp.status_code
            data = resp.json() if status == 200 else None
        except (httpx.HTTPError, ValueError) as e:  # 시간 초과·연결 실패·깨진 답 (키·주소·몸은 로그에 안 남김)
            note("gemini", model, type(e).__name__, body, 0, t0)
            return _j(502, {"code": "gemini_error", "status": 0, "text": "Gemini 가 답하지 않아요"})
        if status != 200 or not isinstance(data, dict):
            note("gemini", model, status, body, 0, t0)
            return _j(502, {"code": "gemini_error", "status": status, "text": "Gemini 가 오류를 줬어요"})
        note("gemini", model, 200, body, _chars(data), t0)
        return _j(200, {"ok": True, "model": model, "response": data})

    async def claude(body: dict[str, Any]) -> Any:
        from dealbot import commentary
        from dealbot.dashboard.xstudio import alias_env_problem
        from dealbot.summarize import _CLI_AUTH, CLI_AUTH_RETRY_SECONDS

        model = body.get("model")
        if model not in CLAUDE_MODELS:
            return bad("bad_model", "model 은 haiku·sonnet 만 받아요")
        system, want_json, max_tokens = body.get("system"), body.get("json", False), body.get("max_tokens", MAX_TOKENS_DEFAULT)
        prompt = claude_prompt(body)
        if (prompt is None or (system is not None and (not isinstance(system, str) or "\x00" in system)) or not isinstance(want_json, bool)
                or not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= MAX_TOKENS_MAX):
            return bad("bad_body", "messages(마지막이 user) 또는 prompt · system(글) · max_tokens(1~2000) · json(참/거짓) 을 확인해 주세요")
        cli = commentary.find_claude()
        if not cli:
            return _j(503, {"code": "no_cli", "text": "서버에 claude CLI 가 없어요"})
        if time.monotonic() < rt["cli_down_until"]:  # 로그인이 풀린 걸 본 뒤 10분은 다시 부르지 않음 (로그인은 주인 몫)
            return _j(503, {"code": "cli_login", "text": "서버 claude CLI 로그인이 풀렸어요"})
        bad_env = alias_env_problem() or alias_problem()
        if bad_env:  # 별칭이 Fable·Opus 쪽으로 돌려져 있으면 부르지 않음 (이름만 적음)
            log.error("ssalmuk llm: %s 가 모델 별칭을 규칙 밖 모델로 돌려놓음 → 부르지 않음", bad_env)
            return _j(503, {"code": "ai_off", "text": "서버 AI 설정이 모델 규칙과 달라요"})
        if mem_low():  # 서버 메모리가 바닥이면 CLI 를 더 띄우지 않음 (횟수도 안 셈)
            log.warning("ssalmuk llm: 여유 메모리 %dMB 아래 → CLI 를 띄우지 않음", MEM_MIN_MB)
            return _j(429, {"code": "busy", "text": "서버 메모리가 모자라요 — 잠시 뒤 다시"})
        if not minute.hit("key"):
            return limit
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=BUSY_WAIT)
        except TimeoutError:
            return _j(429, {"code": "busy", "text": "앞 요청이 아직 돌고 있어요 — 잠시 뒤 다시"})
        try:
            if not desk.llm_day_hit("claude", sonnet=model == "sonnet"):
                return limit
            sys_text = (system.strip() if isinstance(system, str) and system.strip() else DEFAULT_SYSTEM) + (JSON_SUFFIX if want_json else "")
            args = [str(cli), "-p", "--output-format", "json", "--model", model, "--system-prompt", sys_text, *CLI_FLAGS, "--tools", ""]
            env = os.environ | {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_tokens)}
            left = max(5.0, TIMEOUT - (time.monotonic() - t0))
            try:
                code, out, _err = await run_cli(args, prompt.encode("utf-8"), left, env)
            except TimeoutError:
                note("claude", model, "timeout", body, 0, t0)
                return _j(502, {"code": "claude_error", "status": 0, "text": "claude CLI 시간 초과"})
            except OSError as e:
                note("claude", model, type(e).__name__, body, 0, t0)
                return _j(502, {"code": "claude_error", "status": 0, "text": "claude CLI 실행 실패"})
        finally:
            sem.release()
        try:
            payload = json.loads(out.decode("utf-8", "replace") or "{}")
        except ValueError:
            payload = None
        if isinstance(payload, list):
            payload = next((m for m in reversed(payload) if isinstance(m, dict) and m.get("type") == "result"), None)
        if not isinstance(payload, dict) or not payload:
            note("claude", model, f"unreadable exit={code}", body, 0, t0)
            return _j(502, {"code": "claude_error", "status": 0, "text": "claude CLI 응답을 읽지 못했어요"})
        usage = payload.get("modelUsage")
        used = [str(k)[:80] for k in usage] if isinstance(usage, dict) else []
        if payload.get("is_error"):
            detail = str(payload.get("result") or payload.get("subtype") or "")[:200]
            if _CLI_AUTH.search(detail):
                rt["cli_down_until"] = time.monotonic() + CLI_AUTH_RETRY_SECONDS
                note("claude", model, "cli_login", body, 0, t0)
                return _j(503, {"code": "cli_login", "text": "서버 claude CLI 로그인이 풀렸어요"})
            note("claude", model, "is_error", body, 0, t0)  # 오류 글에 프롬프트가 섞일 수 있어 남기지 않음
            return _j(502, {"code": "claude_error", "status": 0, "text": "claude CLI 가 오류를 줬어요"})
        banned = [m for m in used if CLAUDE_BANNED_USED_RE.search(m) or not CLAUDE_OK_USED_RE.search(m)]
        if banned or not used:  # CLI 설정이 별칭을 다른 모델로 돌려놓았거나 쓴 모델을 알 수 없으면 그 답은 버림 (Haiku·Sonnet 만)
            log.error("ssalmuk llm: 답을 쓴 모델이 규칙 밖 (%s) → 답을 버림", ", ".join(banned)[:80] or "모름")
            return _j(503, {"code": "ai_off", "text": "서버 AI 설정이 모델 규칙과 달라요"})
        text = str(payload.get("result") or "")
        if want_json:
            text = _strip_fence(text)
            try:
                json.loads(text)
            except ValueError:
                note("claude", model, "bad_json", body, len(text), t0)
                return _j(502, {"code": "bad_json", "text": "답이 JSON 이 아니에요"})
        if not text.strip():
            note("claude", model, "empty", body, 0, t0)
            return _j(502, {"code": "claude_error", "status": 0, "text": "claude CLI 답이 비었어요"})
        note("claude", model, 200, body, len(text), t0)
        res: dict[str, Any] = {"ok": True, "text": text}
        mine = next((m for m in used if model in m.lower()), used[0] if used else None)
        if mine:
            res["model_used"] = mine
        return _j(200, res)

    async def llm(body: dict[str, Any], headers: dict[str, str]) -> Any:
        denied = machine(headers)
        if denied is not None:
            return denied
        provider = body.get("provider")
        if provider is None and isinstance(body.get("request"), dict):
            provider = "gemini"  # 쌀먹 제안 꼴 {v, purpose, model, request}
        if provider not in ("claude", "gemini"):
            return bad("bad_body", "provider 는 claude·gemini 만 받아요")
        try:  # 짝 없는 대리 문자(\\ud800…)는 UTF-8 로 못 보냄 → 부르기 전에 거절
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (UnicodeEncodeError, ValueError, RecursionError):
            return bad("bad_body", "보낸 글을 읽을 수 없어요")
        if has_pii(body):
            log.info("ssalmuk llm: %s pii → 부르지 않음 (in=%d)", provider, _chars(body))
            return bad("pii", "전화번호·이메일 꼴이 들어 있어 부르지 않았어요")
        return await (claude(body) if provider == "claude" else gemini(body))

    web.route_json(LLM_PATH, llm, max_body=int(cfg.ssalmuk_llm_body_kb) * 1024)
