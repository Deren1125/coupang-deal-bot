"""API 크레딧 (주인 11/21 — "쌓여 있는 API 크레딧($200/달)으로 뒤 작업 옮김 · 구독 사용량 줄이기, 한도 넘으면 구독으로").

Blog-Auto blogauto/api_credit.py 와 같은 장부·같은 예산 (두 프로그램 합계): API_CREDIT_DIR (기본 ~/.local/share/api-credit) 의
YYYY-MM.jsonl — 한 줄 = 한 호출 {at, src, job, model, in, out, cw, cr, ws, usd}. 이 파일은 src="dealbot" 로 적는다.

- 키: .env 의 ANTHROPIC_API_KEY 또는 CLAUDE_API_CREDIT_KEY (주인 11/22 — 키는 서버 .env 에만). claude CLI 에는 두 이름 모두 빼고 넘긴다 (cli_env) — 넘기면 CLI 가
  구독 대신 API 로 청구해 예산 장부 밖으로 나간다. 키 값은 어디에도 쓰지 않는다.
- 일마다 먼저 부를 방식: DEFAULT_TASKS (한줄평·정보 글 요약·X 초안·말 명령·쌀먹 대리·이웃 댓글 = api 먼저 — 주인 11/22). .env DEALBOT_API_TASKS 로 덮음
  (예 'commentary:cli,xstudio:api'). 못 쓰면(키 없음·키 오류·달/하루 예산) 조용히 claude CLI 로 — 경고는 이유마다 하루 한 번.
- 모델: CLI 와 같은 이름 (DEALBOT_MODEL_COMMENTARY · 비면 'sonnet') → API id. 올라가는 글은 Fable·Mythos·Haiku 안 씀 (→ Sonnet).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SRC = "dealbot"
KEY_ENVS = ("CLAUDE_API_CREDIT_KEY", "ANTHROPIC_API_KEY")
CLI_STRIP_ENVS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_CREDIT_KEY")
DIR_ENV = "API_CREDIT_DIR"
MONTHLY_ENV = "API_CREDIT_MONTHLY_USD"
DAILY_ENV = "API_CREDIT_DAILY_USD"
TASKS_ENV = "DEALBOT_API_TASKS"
MONTHLY_DEFAULT = 95.0  # 블로그 서버 합계 (Blog-Auto + 딜봇 · 장부 하나) — 주인 11/23: 총 $167 = 블로그 $95·쇼츠 $30·기타 $12·취준 $30
FAIL_HOURS = 24
# 주인 11/22 'Sns-board 의 Claude 호출(분류·경로·글쓰기, 쌀먹 대리 포함)을 API 키 방식으로 — 하루 호출·비용 상한은 지금처럼':
# 한줄평·정보 글 요약·X 초안(xstudio — xaggro·xtrend·xphoto 도 이 길)·말 명령(분류·경로)·쌀먹 대리·이웃 댓글 초안 = 모두 api 먼저
# 주인 11/22 '중요한 건 Claude API, 별로 안 중요한 건 Gemini API': 올라가는 글(한줄평·요약·X 초안·이웃 댓글)·쌀먹 대리(Claude 로 물은 것) =
# Claude API · 말 명령(분류·경로) = Gemini(싼 모델) → Claude API → CLI
DEFAULT_TASKS = {"commentary": "api", "summarize": "api", "xstudio": "api", "nl": "gemini", "ssalmuk": "api", "seoi": "api"}
GEMINI = "gemini"
GEMINI_KEY_ENV = "GEMINI_API_KEY"
GEMINI_MONTHLY_ENV = "GEMINI_CREDIT_MONTHLY_USD"
GEMINI_MONTHLY_DEFAULT = 20.0
GEMINI_CHEAP = "gemini-2.5-flash-lite"  # .env DEALBOT_GEMINI_MODEL 로 바꿈
GEMINI_PRICES = {"gemini-2.5-flash-lite": (0.10, 0.40, 0.0, 0.025), "gemini-2.5-flash": (0.30, 2.50, 0.0, 0.075)}
GEMINI_UNKNOWN = (4.0, 18.0, 0.0, 1.0)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# 모델 이름은 저장소에 적지 않는다 (딜봇 원칙 — test_no_model_ids_in_the_repo): API 에는 CLI 와 같은 별칭(opus·sonnet·haiku)으로 정하고,
# 실제 id 는 .env DEALBOT_API_MODEL_<별칭> 이 있으면 그 값, 없으면 Models API 목록에서 그 별칭이 든 가장 새 모델 (Fable·Mythos 제외 · 하루 기억)
ALIASES = ("opus", "sonnet", "haiku")
POSTED_NO = ("fable", "mythos", "haiku")
RESOLVE_KEEP_S = 24 * 3600

# $ / 1M 토큰 (입력, 출력, 캐시 쓰기, 캐시 읽기) — 갈래마다 넉넉하게 (같은 갈래의 옛 모델 값까지 덮게 · 예산을 넘지 않는 쪽)
PRICES = {"opus": (5.0, 25.0, 6.25, 0.50), "sonnet": (3.0, 15.0, 3.75, 0.30), "haiku": (1.0, 5.0, 1.25, 0.10),
          "fable": (10.0, 50.0, 12.5, 1.0), "mythos": (10.0, 50.0, 12.5, 1.0)}
UNKNOWN_PRICE = (10.0, 50.0, 12.5, 1.0)
_resolved: dict[str, tuple[float, str]] = {}
WEB_SEARCH_USD = 0.01
_warned_mem: set[str] = set()


def api_key() -> str | None:
    for name in KEY_ENVS:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return None


def strip_cli_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k not in CLI_STRIP_ENVS}


def ledger_dir() -> Path:
    return Path(os.path.expanduser(os.environ.get(DIR_ENV) or "~/.local/share/api-credit"))


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def monthly_budget() -> float:
    v = _num(os.environ.get(MONTHLY_ENV))
    return v if v > 0 else MONTHLY_DEFAULT


def provider(task: str) -> str:
    """그 일을 먼저 부를 방식 'api' · 'cli' (DEALBOT_API_TASKS 로 덮음)."""
    table = dict(DEFAULT_TASKS)
    for part in (os.environ.get(TASKS_ENV) or "").split(","):
        k, _, v = part.partition(":")
        if k.strip() and v.strip().lower() in ("api", "cli", "gemini"):
            table[k.strip()] = v.strip().lower()
    return table.get(task, "cli")


def api_model(cli_model: str | None, *, posted: bool = True) -> str:
    """CLI 모델 이름(별칭·서버 .env 의 id) → API 에 쓸 이름 (별칭이면 ask 가 실제 id 로). 올라가는 글은 Fable·Mythos·Haiku 면 sonnet
    (주인 11/14 'Opus 까지만 · Fable 안 씀' · 11/16 Haiku 안 씀)."""
    v = str(cli_model or "").strip() or "sonnet"
    if posted and any(no in v.lower() for no in POSTED_NO):
        return "sonnet"
    return v.lower() if v.lower() in ALIASES else v


async def resolve(name: str, client: Any) -> str:
    """별칭 → 실제 모델 id (.env DEALBOT_API_MODEL_<별칭> > Models API 의 가장 새 모델). 별칭이 아니면 그대로."""
    alias = str(name or "").strip().lower()
    if alias not in ALIASES:
        return name
    env = (os.environ.get(f"DEALBOT_API_MODEL_{alias.upper()}") or "").strip()
    if env and not any(no in env.lower() for no in ("fable", "mythos")):
        return env
    hit = _resolved.get(alias)
    now = time.time()
    if hit and now - hit[0] < RESOLVE_KEEP_S:
        return hit[1]
    try:
        page = await client.models.list(limit=100)
        ids = [str(getattr(m, "id", "")) for m in getattr(page, "data", None) or []]
    except Exception as e:  # noqa: BLE001
        if is_key_error(e):
            mark_failed(type(e).__name__)
        raise ApiError(f"모델 목록 못 받음 ({type(e).__name__})") from None
    pick = next((i for i in ids if alias in i.lower() and not any(no in i.lower() for no in ("fable", "mythos"))), "")
    if not pick:
        raise ApiError(f"{alias} 모델 없음")
    _resolved[alias] = (now, pick)
    return pick


# ── 값 셈·장부 ──────────────────────────────────────────────

def _get(usage: Any, name: str) -> Any:
    return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)


def usage_numbers(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"in": 0, "out": 0, "cw": 0, "cr": 0, "ws": 0}
    stu = _get(usage, "server_tool_use")
    return {"in": int(_num(_get(usage, "input_tokens"))), "out": int(_num(_get(usage, "output_tokens"))),
            "cw": int(_num(_get(usage, "cache_creation_input_tokens"))), "cr": int(_num(_get(usage, "cache_read_input_tokens"))),
            "ws": int(_num(_get(stu, "web_search_requests"))) if stu is not None else 0}


def price_of(model: str) -> tuple[float, float, float, float]:
    m = str(model or "").lower()
    if m.startswith("gemini"):
        return next((GEMINI_PRICES[k] for k in sorted(GEMINI_PRICES, key=len, reverse=True) if m.startswith(k)), GEMINI_UNKNOWN)
    return next((v for k, v in PRICES.items() if k in m), UNKNOWN_PRICE)


def usd(model: str, nums: dict[str, int]) -> float:
    pi, po, pw, pr = price_of(model)
    return round((nums.get("in", 0) * pi + nums.get("out", 0) * po + nums.get("cw", 0) * pw + nums.get("cr", 0) * pr) / 1e6
                 + nums.get("ws", 0) * WEB_SEARCH_USD, 6)


def record(task: str, model: str, usage: Any, now: datetime | None = None, prov: str = "") -> float:
    """API 응답 한 건을 장부에 한 줄 (실패해도 일은 그대로). prov="gemini" 면 Gemini 줄 (usage = 숫자 dict)."""
    now = now or datetime.now()
    nums = usage if prov == GEMINI and isinstance(usage, dict) and "in" in usage else usage_numbers(usage)
    if not any(nums.values()):
        return 0.0
    cost = usd(model, nums)
    row = {"at": now.isoformat(timespec="seconds"), "src": SRC, "job": str(task or "etc")[:60], "model": str(model or "")[:60],
           **nums, "usd": cost, **({"prov": prov} if prov else {})}
    try:
        d = ledger_dir()
        d.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(d, 0o700)
        fd = os.open(d / f"{now:%Y-%m}.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        log.warning("API 크레딧 장부 쓰기 실패: %s", type(e).__name__)
    return cost


def totals(now: datetime | None = None, prov: str = "") -> dict[str, float]:
    """{month, today, before_today} — 두 프로그램 합계 · prov 의 줄만 ('' = Claude)."""
    now = now or datetime.now()
    day = f"{now:%Y-%m-%d}"
    t = {"month": 0.0, "today": 0.0, "before_today": 0.0}
    try:
        text = (ledger_dir() / f"{now:%Y-%m}.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return t
    for line in text.splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or str(r.get("prov") or "") != prov:
            continue
        u = _num(r.get("usd"))
        t["month"] += u
        t["today" if str(r.get("at") or "").startswith(day) else "before_today"] += u
    return t


def _fail_path(prov: str = "") -> Path:
    return ledger_dir() / f"failed-{SRC}{':' + prov if prov else ''}.json"


def mark_failed(why: str, now: datetime | None = None, hours: float = FAIL_HOURS, prov: str = "") -> None:
    now = now or datetime.now()
    try:
        ledger_dir().mkdir(parents=True, exist_ok=True)
        _fail_path(prov).write_text(json.dumps({"until": (now + timedelta(hours=hours)).isoformat(timespec="seconds"),
                                            "why": str(why)[:120]}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    warn_daily(f"key_failed:{SRC}{':' + prov if prov else ''}", f"{'Gemini' if prov else 'API'} 키 오류({str(why)[:60]}) → {hours:g}시간 claude CLI(구독)로", now)


def failed_until(now: datetime | None = None, prov: str = "") -> datetime | None:
    now = now or datetime.now()
    try:
        until = datetime.fromisoformat(json.loads(_fail_path(prov).read_text(encoding="utf-8")).get("until") or "")
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return until if now < until else None


def warn_daily(reason: str, text: str, now: datetime | None = None) -> bool:
    """이유마다 하루 한 번 (Blog-Auto 와 같은 표시 파일)."""
    now = now or datetime.now()
    key = f"{now:%Y-%m-%d}:{reason}"
    if key in _warned_mem:
        return False
    _warned_mem.add(key)
    p = ledger_dir() / f"warned-{now:%Y-%m-%d}.json"
    try:
        seen = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = []
    if reason in seen:
        return False
    try:
        ledger_dir().mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps([*seen, reason], ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    log.warning("API 크레딧: %s", text)
    return True


def allow(task: str, now: datetime | None = None, key: str | None = None) -> tuple[bool, str]:
    """지금 이 일을 API 로 불러도 되나 → (되나, 까닭: ok·no_key·key_failed·month_cap·day_cap). key: 부르는 쪽이 따로 받은 키."""
    now = now or datetime.now()
    if not (key or api_key()):
        warn_daily("no_key", "키 없음(CLAUDE_API_CREDIT_KEY) → claude CLI(구독)로", now)
        return False, "no_key"
    if failed_until(now):
        return False, "key_failed"
    t = totals(now)
    budget = monthly_budget()
    if t["month"] >= budget:
        warn_daily("month_cap", f"이번 달 ${t['month']:.2f} / ${budget:.0f} 다 씀 → 다음 달까지 claude CLI(구독)로", now)
        return False, "month_cap"
    # 딜봇은 달 상한만 본다 (주인 11/22 'CLI 는 키가 없거나·실패하거나·달 상한일 때만') — 하루 몫은 Blog-Auto 쪽 나눔.
    # 고정 하루 상한(API_CREDIT_DAILY_USD)을 적었으면 그것만 지킨다
    fixed = _num(os.environ.get(DAILY_ENV))
    if fixed > 0 and t["today"] >= fixed:
        warn_daily("day_cap", f"오늘 몫 ${fixed:.2f} 다 씀 → 내일까지 claude CLI(구독)로", now)
        return False, "day_cap"
    return True, "ok"


def route(task: str, now: datetime | None = None) -> bool:
    """이 일을 지금 API 로 먼저 부를까 (먼저 부를 방식이 api 이고 예산·키가 될 때)."""
    return provider(task) == "api" and allow(task, now)[0]


def is_key_error(e: BaseException) -> bool:
    """다시 해도 안 되는 키 오류 (401·403·크레딧 없음) — mark_failed 로 하루 쉼."""
    import anthropic

    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    return isinstance(e, anthropic.BadRequestError) and "credit balance" in str(e).lower()


class ApiError(Exception):
    """API 가 답하지 않음 — 부르는 쪽은 claude CLI(구독)로 넘어간다. 키 오류면 이미 mark_failed 됨."""


def strict(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON 스키마 강제(output_config.format)가 받는 모양: 객체마다 additionalProperties false."""
    out = dict(schema)
    if out.get("type") == "object":
        out.setdefault("additionalProperties", False)
        out["properties"] = {k: strict(v) if isinstance(v, dict) else v for k, v in (out.get("properties") or {}).items()}
    if isinstance(out.get("items"), dict):
        out["items"] = strict(out["items"])
    return out


async def ask(task: str, model: str, system: str, user: str, *, schema: dict[str, Any] | None = None,
              images: list[bytes] | None = None, effort: str | None = "low", max_tokens: int = 2000, timeout: float = 60,
              client: Any = None) -> Any:
    """Claude API 한 번 → schema 가 있으면 dict, 없으면 글. 쓴 토큰은 장부에. 실패는 ApiError (키 오류면 하루 쉼)."""
    import base64

    import anthropic

    content: Any = user
    if images:
        content = [*({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                  "data": base64.b64encode(b).decode("ascii")}} for b in images),
                   {"type": "text", "text": user}]
    kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                          "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                          "messages": [{"role": "user", "content": content}]}
    oc: dict[str, Any] = {}
    if effort:
        oc["effort"] = effort
    if schema is not None:
        oc["format"] = {"type": "json_schema", "schema": strict(schema)}
    if oc:
        kw["output_config"] = oc
    client = client or anthropic.AsyncAnthropic(api_key=api_key(), timeout=timeout, max_retries=1)
    try:
        kw["model"] = model = await resolve(model, client)
        response = await client.messages.create(**kw)
    except Exception as e:  # noqa: BLE001 — 무엇이든 CLI 로 넘어간다
        if is_key_error(e):
            mark_failed(type(e).__name__)
        status = getattr(e, "status_code", None)
        raise ApiError(f"API {type(e).__name__}{f' {status}' if status else ''}") from None
    record(task, str(getattr(response, "model", "") or model), getattr(response, "usage", None))
    stop = getattr(response, "stop_reason", None)
    if stop in ("refusal", "max_tokens"):
        raise ApiError(f"API 답 없음 ({stop})")
    text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text").strip()
    if schema is None:
        if not text:
            raise ApiError("API 답이 비어 있음")
        return text
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ApiError("API 답이 JSON 이 아님")
    return data



# ── Gemini (주인 11/22 B 층 — 말 명령 분류·경로) ──────────────────

def gemini_key() -> str | None:
    return (os.environ.get(GEMINI_KEY_ENV) or "").strip() or None


def gemini_allow(task: str, now: datetime | None = None) -> tuple[bool, str]:
    """Gemini 를 지금 불러도 되나 (키·키 오류 쉼·Gemini 달 상한 — Blog-Auto 와 같은 장부)."""
    now = now or datetime.now()
    if not gemini_key():
        return False, "no_key"
    if failed_until(now, GEMINI):
        return False, "key_failed"
    v = _num(os.environ.get(GEMINI_MONTHLY_ENV))
    budget = v if v > 0 else GEMINI_MONTHLY_DEFAULT
    t = totals(now, GEMINI)
    if t["month"] >= budget:
        warn_daily("gemini:month_cap", f"Gemini 이번 달 ${t['month']:.2f} / ${budget:.0f} 다 씀 → Claude API·CLI 로", now)
        return False, "month_cap"
    return True, "ok"


def _gemini_data(text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    try:
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except ValueError:
        return None
    if not isinstance(data, dict) or any(k not in data for k in schema.get("required") or []):
        return None
    return data


async def ask_gemini(task: str, system: str, user: str, schema: dict[str, Any], *, timeout: float = 30,
                     http: Any = None) -> dict[str, Any]:
    """Gemini(싼 모델) 한 번 — JSON·필수 칸이 안 맞으면 한 번 더. 그래도 안 되면 ApiError (부르는 쪽이 Claude API → CLI).
    키는 헤더로만 (주소·로그에 안 남김). 쓴 토큰은 장부(prov=gemini)."""
    import httpx

    model = (os.environ.get("DEALBOT_GEMINI_MODEL") or "").strip() or GEMINI_CHEAP
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema, "maxOutputTokens": 2048,
                                 "thinkingConfig": {"thinkingBudget": 0}}}
    own = http is None
    http = http or httpx.AsyncClient(timeout=timeout)
    try:
        for _ in range(2):
            try:
                r = await http.post(GEMINI_URL.format(model=model), json=body,
                                    headers={"x-goog-api-key": gemini_key() or "", "Content-Type": "application/json"})
            except Exception as e:  # noqa: BLE001
                raise ApiError(f"Gemini 연결 실패 ({type(e).__name__})") from None
            if r.status_code in (401, 403) or (r.status_code == 400 and "API_KEY" in (r.text or "")):
                mark_failed(f"gemini {r.status_code}", prov=GEMINI)
                raise ApiError(f"Gemini 키 오류 {r.status_code}")
            if r.status_code >= 400:
                raise ApiError(f"Gemini 오류 {r.status_code}")
            try:
                out = r.json()
            except ValueError:
                out = {}
            meta = out.get("usageMetadata") or {}
            cached = int(_num(meta.get("cachedContentTokenCount")))
            record(task, model, {"in": max(0, int(_num(meta.get("promptTokenCount"))) - cached),
                                 "out": int(_num(meta.get("candidatesTokenCount"))) + int(_num(meta.get("thoughtsTokenCount"))),
                                 "cw": 0, "cr": cached, "ws": 0}, prov=GEMINI)
            cand = (out.get("candidates") or [{}])[0]
            text = "".join(p.get("text") or "" for p in ((cand.get("content") or {}).get("parts") or [])
                           if isinstance(p, dict) and not p.get("thought"))
            data = _gemini_data(text, schema)
            if data is not None:
                return data
            log.info("gemini %s: 답이 JSON·스키마에 안 맞음 → 한 번 더", task)
        raise ApiError("Gemini 답이 JSON·스키마에 안 맞음 (두 번)")
    finally:
        if own:
            await http.aclose()
