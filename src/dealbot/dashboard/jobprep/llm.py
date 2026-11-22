"""취준 AI 부르기 — 모두 Claude API (ANTHROPIC_API_KEY · Max API 크레딧 — 주인 11/23) + 취준 몫 상한 + 캐시 (llm_cache).

- ask(kind, tier, …): tier 'fast' = 설정 claude_fast (기본 haiku — 분류·적합도·정리·짧은 피드백), 'good' = claude_good (기본 sonnet —
  사다리·힌트·설명·교정·그림 읽기). 별칭 → 실제 모델 id 는 api_credit.resolve (.env DEALBOT_API_MODEL_<별칭> 또는 Models API) — 이름을 박지 않음.
- 상한 (설정값): 오늘 < claude_daily_usd($2) · 이번 달 < claude_monthly_usd($30) + api_credit.allow() (키·키 오류·블로그 서버 달 상한).
  취준 몫 = 취준 장부(usage 'claude') 와 공용 장부의 job 'jobprep.*' 줄 중 큰 값. 넘으면 부르지 않고 CapError(429 'cap').
  **구독 CLI 로 넘어가지 않는다.** api_credit.py 는 고치지 않음 (resolve·record·allow 만 씀).
- Gemini: JOBPREP_GEMINI_API_KEY(무료 키)를 넣었을 때만 (기본 꺼짐 · 하루 횟수 상한) — 공용 GEMINI_API_KEY 는 절대 안 씀 (유료 · 주인 11/23 0원).
- JSON 이 필요한 일은 스키마를 시스템 글에 넣고 답에서 JSON 을 꺼냄 (필수 칸이 없으면 한 번 더).
- 키 값은 os.environ 에서 읽어 머리글에만. 프롬프트·답 글은 로그에 안 남김 (해시 8자·글자 수·모델·상태만).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot import api_credit

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KST = ZoneInfo("Asia/Seoul")
CLAUDE_MAX_TOKENS = 4000
CUT_NOTE = "\n\n(잘림 — 답이 길어 여기까지)"
JSON_RULE = ("\n\n[답 형식] 설명 없이 JSON 객체 하나만 답한다 (코드 블록 표시 없이). 아래 JSON 스키마를 따른다 — required 칸은 꼭 채우고, "
             "모르면 null 이나 빈 글자.\n")


class CapError(Exception):
    def __init__(self, code: str, text: str, status: int = 429) -> None:
        super().__init__(code)
        self.code, self.text, self.status = code, text, status


class LlmError(Exception):
    def __init__(self, text: str, status: int = 502) -> None:
        super().__init__(text)
        self.text, self.status = text, status


def h8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:8]


def parse_json(text: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    s, e = (text or "").find("{"), (text or "").rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        data = json.loads(text[s:e + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or any(k not in data for k in schema.get("required") or []):
        return None
    return data


async def claude_call(task: str, model: str, system: str, user: str, *, max_tokens: int = CLAUDE_MAX_TOKENS, timeout: float = 120,
                      images: list[tuple[bytes, str]] | None = None, client: Any = None, **_kw: Any) -> str:
    """Claude API 한 번 (api_credit 과 같은 장부·키·모델 고르기). 답이 max_tokens 로 잘리면 오류 대신 잘린 글 + '(잘림)' (이미 값을 냈으므로).
    거절·빈 답·연결 실패는 ApiError (키 오류면 api_credit 이 하루 쉼)."""
    import anthropic

    client = client or anthropic.AsyncAnthropic(api_key=api_credit.api_key(), timeout=timeout, max_retries=1)
    content: Any = user
    if images:
        content = [*({"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(b).decode("ascii")}}
                     for b, mt in images), {"type": "text", "text": user}]
    kw: dict[str, Any] = {"max_tokens": max_tokens, "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                          "messages": [{"role": "user", "content": content}]}
    try:
        kw["model"] = model = await api_credit.resolve(model, client)
        r = await client.messages.create(**kw)
    except api_credit.ApiError:
        raise
    except Exception as e:  # noqa: BLE001
        if api_credit.is_key_error(e):
            api_credit.mark_failed(type(e).__name__)
        raise api_credit.ApiError(f"API {type(e).__name__}") from None
    api_credit.record(task, str(getattr(r, "model", "") or model), getattr(r, "usage", None))
    stop = getattr(r, "stop_reason", None)
    text = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text").strip()
    if stop == "refusal" or not text:
        raise api_credit.ApiError(f"API 답 없음 ({stop})")
    return text + CUT_NOTE if stop == "max_tokens" else text


class Llm:
    def __init__(self, store: Any, cfg: Callable[[], Any], *, http: Any = None, clock: Callable[[], float] = time.time,
                 claude_ask: Callable[..., Awaitable[Any]] | None = None) -> None:
        self.store, self.cfg, self.http, self.clock = store, cfg, http, clock
        self.claude_ask = claude_ask or claude_call

    # ── 날짜 ──
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), KST)

    def day(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def month(self) -> str:
        return self._now().strftime("%Y-%m")

    # ── 키·모델 ──
    def gemini_key(self) -> str | None:
        """무료 취준 키만 (공용 GEMINI_API_KEY 는 쓰지 않음)."""
        return (os.environ.get(self.cfg().gemini_key_env or "") or "").strip() or None

    def provider(self) -> str:
        return "gemini" if self.gemini_key() else "claude"

    def claude_model(self, tier: str) -> str:
        cfg = self.cfg()
        return api_credit.api_model(cfg.claude_good if tier == "good" else cfg.claude_fast, posted=False)

    def model_of(self, tier: str) -> str:
        if self.provider() == "gemini":
            cfg = self.cfg()
            return cfg.gemini_good if tier == "good" else cfg.gemini_fast
        return self.claude_model(tier)

    # ── 쓴 양 ──
    def _add_usage(self, kind: str, usd: float) -> None:
        self.store.x("INSERT INTO usage (day, kind, n, usd) VALUES (?, ?, 1, ?) ON CONFLICT(day, kind) DO UPDATE SET n=n+1, usd=usd+excluded.usd",
                     (self.day(), kind, float(usd)))

    def _sum(self, kind_like: str, *, month: bool) -> tuple[int, float]:
        if month:
            r = self.store.one("SELECT COALESCE(SUM(n),0) n, COALESCE(SUM(usd),0) usd FROM usage WHERE day LIKE ? AND kind LIKE ?",
                               (self.month() + "-%", kind_like))
        else:
            r = self.store.one("SELECT COALESCE(SUM(n),0) n, COALESCE(SUM(usd),0) usd FROM usage WHERE day=? AND kind LIKE ?",
                               (self.day(), kind_like))
        return int(r["n"] if r else 0), float(r["usd"] if r else 0.0)

    def kind_spent_today(self, kind: str) -> float:
        """그 일(kind 의 ':' 앞)에 오늘 쓴 Claude 추정액 — 사다리 미리 만들기 하루 몫에 씀."""
        return self._sum(f"claudek:{kind.split(':')[0]}", month=False)[1]

    def ledger_jobprep(self) -> dict[str, float]:
        """공용 장부에서 job 이 'jobprep.' 로 시작하는 Claude 줄 합계 {today, month}."""
        now = self._now().replace(tzinfo=None)
        out = {"today": 0.0, "month": 0.0}
        try:
            text = (api_credit.ledger_dir() / f"{now:%Y-%m}.jsonl").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return out
        day = f"{now:%Y-%m-%d}"
        for line in text.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or r.get("prov") or not str(r.get("job") or "").startswith("jobprep."):
                continue
            u = float(r.get("usd") or 0)
            out["month"] += u
            if str(r.get("at") or "").startswith(day):
                out["today"] += u
        return out

    def claude_spent(self) -> dict[str, float]:
        led = self.ledger_jobprep()
        _, t = self._sum("claude", month=False)
        _, m = self._sum("claude", month=True)
        return {"today": round(max(t, led["today"]), 4), "month": round(max(m, led["month"]), 4)}

    def claude_daily_cap(self) -> float:
        return float(self.cfg().claude_daily_usd)

    def gemini_spent(self) -> dict[str, Any]:
        return {"fast": self._sum("gemini:fast", month=False)[0], "good": self._sum("gemini:good", month=False)[0]}

    def view(self) -> dict[str, Any]:
        cfg = self.cfg()
        c = self.claude_spent()
        return {"provider": self.provider(),
                "claude": {"today": c["today"], "month": c["month"], "day_cap": self.claude_daily_cap(), "month_cap": cfg.claude_monthly_usd,
                           "key": bool(api_credit.api_key())},
                "gemini": {**self.gemini_spent(), "caps": dict(cfg.gemini_calls_per_day), "key": "own" if self.gemini_key() else "none"}}

    # ── 캐시 ──
    @staticmethod
    def cache_key(kind: str, model: str, ver: int, payload: Any) -> str:
        raw = json.dumps([kind, model, ver, payload], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def cached(self, key: str) -> Any:
        r = self.store.one("SELECT body FROM llm_cache WHERE key=?", (key,))
        if r is None:
            return None
        try:
            return json.loads(r["body"])
        except ValueError:
            return None

    def put_cache(self, key: str, kind: str, model: str, body: Any) -> None:
        self.store.x("INSERT OR REPLACE INTO llm_cache (key, kind, model, made_at, body) VALUES (?,?,?,?,?)",
                     (key, kind[:80], model, self.clock(), json.dumps(body, ensure_ascii=False)))

    # ── 한 번 묻기 (모든 탭) ──
    async def ask(self, kind: str, tier: str, system: str, user: str, *, schema: dict[str, Any] | None = None,
                  images: list[tuple[bytes, str]] | None = None, ver: int = 1, cache: bool = True, max_tokens: int = 4096,
                  thinking: int | None = None, timeout: float = 120) -> Any:
        """tier: fast|good. schema 가 있으면 dict, 없으면 글. 캐시에 있으면 부르지 않음."""
        model = self.model_of(tier)
        payload = {"s": system, "u": user, "schema": schema, "img": [hashlib.sha256(b).hexdigest() for b, _ in (images or [])]}
        ck = self.cache_key(kind, model, ver, payload)
        if cache:
            hit = self.cached(ck)
            if hit is not None:
                return hit
        if self.provider() == "gemini":
            result = await self._gemini(kind, tier, model, system, user, schema=schema, images=images, max_tokens=max_tokens,
                                        thinking=thinking, timeout=timeout)
        else:
            result = await self._claude_ask(kind, model, system, user, schema=schema, images=images, max_tokens=max_tokens)
        if cache:
            self.put_cache(ck, kind, model, result)
        return result

    async def _claude_ask(self, kind: str, model: str, system: str, user: str, *, schema: dict[str, Any] | None,
                          images: list[tuple[bytes, str]] | None, max_tokens: int) -> Any:
        sys_ = system + (JSON_RULE + json.dumps(schema, ensure_ascii=False) if schema is not None else "")
        for attempt in range(2 if schema is not None else 1):
            text = await self._claude_once(kind, model, sys_, user, max_tokens=max_tokens, images=images)
            if schema is None:
                return text
            data = parse_json(text, schema)
            if data is not None:
                return data
            log.info("jobprep claude %s: JSON 이 아님 (%d)", kind[:30], attempt + 1)
        raise LlmError("Claude 답이 JSON 이 아니에요")

    # ── Claude ──
    def claude_check(self) -> None:
        cfg = self.cfg()
        ok, why = api_credit.allow("jobprep.ask")
        if not ok:
            text = {"no_key": "Claude API 키가 없어요", "key_failed": "Claude 키 오류로 잠깐 쉬는 중이에요",
                    "month_cap": "블로그 서버 Claude 달 예산을 다 썼어요", "day_cap": "오늘 서버 Claude 몫을 다 썼어요"}.get(why, why)
            raise CapError("cap" if why.endswith("cap") else why, text, 429 if why.endswith("cap") else 503)
        spent = self.claude_spent()
        day_cap = self.claude_daily_cap()
        if spent["today"] >= day_cap:
            raise CapError("cap", f"오늘 취준 AI 몫(${day_cap:g})을 다 썼어요 — 내일 다시")
        if spent["month"] >= float(cfg.claude_monthly_usd):
            raise CapError("cap", f"이번 달 취준 AI 몫(${cfg.claude_monthly_usd:g})을 다 썼어요")

    async def _claude_once(self, kind: str, model: str, system: str, user: str, *, max_tokens: int,
                           images: list[tuple[bytes, str]] | None = None) -> str:
        self.claude_check()
        before = self.ledger_jobprep()["month"]
        try:
            text = await self.claude_ask(f"jobprep.{kind.split(':')[0]}"[:60], model, system, user, max_tokens=max_tokens, images=images)
        except api_credit.ApiError:
            log.info("jobprep claude %s hash=%s: api error", kind[:30], h8(user))
            raise LlmError("Claude 가 답하지 않았어요 (구독으로 넘기지 않음)") from None
        cost = max(0.0, self.ledger_jobprep()["month"] - before)
        self._add_usage("claude", cost)
        self._add_usage(f"claudek:{kind.split(':')[0]}", cost)
        log.info("jobprep claude %s model=%s in=%d hash=%s ok", kind[:30], model, len(user), h8(user))
        return str(text)

    async def claude(self, kind: str, system: str, user: str, *, max_tokens: int = CLAUDE_MAX_TOKENS) -> str:
        """대화창 — 늘 Claude (good 모델 · 캐시 없음)."""
        return await self._claude_once(kind, self.claude_model("good"), system, user, max_tokens=max_tokens)

    # ── Gemini (무료 취준 키를 넣었을 때만) ──
    async def _gemini(self, kind: str, tier: str, model: str, system: str, user: str, *, schema: dict[str, Any] | None,
                      images: list[tuple[bytes, str]] | None, max_tokens: int, thinking: int | None, timeout: float) -> Any:
        cfg = self.cfg()
        key = self.gemini_key()
        cap = int((cfg.gemini_calls_per_day or {}).get(tier, 0))
        if self._sum(f"gemini:{tier}", month=False)[0] >= cap:
            raise CapError("cap", f"오늘 Gemini({tier}) 몫 {cap}번을 다 썼어요 — 내일 다시")
        parts: list[dict[str, Any]] = [{"inlineData": {"mimeType": mt, "data": base64.b64encode(b).decode("ascii")}} for b, mt in (images or [])]
        parts.append({"text": user})
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens,
                               "thinkingConfig": {"thinkingBudget": int(thinking if thinking is not None else (0 if schema is not None else 512))}}
        if schema is not None:
            gen.update({"responseMimeType": "application/json", "responseJsonSchema": schema})
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        if self.http is None:
            raise LlmError("연결 준비 안 됨", 503)
        try:
            r = await self.http.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                                     headers={"x-goog-api-key": key or "", "Content-Type": "application/json"})
        except Exception as e:  # noqa: BLE001
            log.info("jobprep gemini %s %s: %s", kind[:30], h8(user), type(e).__name__)
            raise LlmError("Gemini 에 닿지 못했어요") from None
        log.info("jobprep gemini %s model=%s in=%d hash=%s status=%d", kind[:30], model, len(user), h8(user), r.status_code)
        if r.status_code in (401, 403):
            raise LlmError("Gemini 키 오류", 503)
        if r.status_code == 429:
            raise CapError("rate", "Gemini 가 잠깐 바빠요 — 조금 뒤 다시")
        if r.status_code >= 400:
            raise LlmError(f"Gemini 오류 {r.status_code}")
        try:
            out = r.json()
        except ValueError:
            out = {}
        self._add_usage(f"gemini:{tier}", 0.0)  # 무료 키 — 횟수만 셈 (공용 장부에 안 적음)
        cand = (out.get("candidates") or [{}])[0]
        text = "".join(p.get("text") or "" for p in ((cand.get("content") or {}).get("parts") or [])
                       if isinstance(p, dict) and not p.get("thought")).strip()
        if schema is None:
            if not text:
                raise LlmError("Gemini 답이 비었어요")
            return text
        data = parse_json(text, {})
        if data is None:
            raise LlmError("Gemini 답이 JSON 이 아니에요")
        return data
