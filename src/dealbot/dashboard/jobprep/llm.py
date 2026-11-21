"""취준 AI 부르기 — Gemini(기본) · Claude('클로드에게 묻기' 단추만) + 취준 몫 상한 + 캐시 (llm_cache).

- Gemini 키: 설정 gemini_key_env(JOBPREP_GEMINI_API_KEY) 가 있으면 그 키, 없으면 공용 GEMINI_API_KEY — 공용 키일 때만 취준 몫 달
  추정액 상한(gemini_monthly_usd, 기본 $3)을 보고, 공용 장부(api_credit.record prov=gemini)에 적는다. 하루 횟수 상한(fast·good)은 늘.
- Claude: api_credit.allow() (키·키 오류·블로그 서버 달 상한) + 취준 몫 (오늘 < claude_daily_usd · 첫 7일은 claude_first_week_daily_usd ·
  이번 달 < claude_monthly_usd). 취준 몫 = 취준 장부(usage 표 'claude') 와 공용 장부의 job 'jobprep.*' 줄 중 큰 값.
  넘으면 부르지 않고 CapError(429 'cap'). **구독 CLI 로 넘어가지 않는다** (api_credit.ask 만 씀). api_credit.py 는 고치지 않음.
- 키 값은 os.environ 에서 읽어 머리글(x-goog-api-key)에만. 프롬프트·답 글은 로그에 안 남김 (해시 8자·글자 수·모델·상태만).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot import api_credit

log = logging.getLogger(__name__)

SHARED_GEMINI_ENV = "GEMINI_API_KEY"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
KST = ZoneInfo("Asia/Seoul")


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


class Llm:
    def __init__(self, store: Any, cfg: Callable[[], Any], *, http: Any = None, clock: Callable[[], float] = time.time,
                 claude_ask: Callable[..., Awaitable[Any]] | None = None) -> None:
        self.store, self.cfg, self.http, self.clock = store, cfg, http, clock
        self.claude_ask = claude_ask or api_credit.ask

    # ── 날짜 ──
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), KST)

    def day(self) -> str:
        return self._now().strftime("%Y-%m-%d")

    def month(self) -> str:
        return self._now().strftime("%Y-%m")

    # ── 키 ──
    def gemini_key(self) -> tuple[str | None, str]:
        own = (os.environ.get(self.cfg().gemini_key_env or "") or "").strip()
        if own:
            return own, "own"
        shared = (os.environ.get(SHARED_GEMINI_ENV) or "").strip()
        return (shared or None), ("shared" if shared else "none")

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
        cfg = self.cfg()
        first = self.store.kv_get("claude_first_day")
        if cfg.claude_first_week_days > 0:
            if not first:
                return float(cfg.claude_first_week_daily_usd)
            try:
                start = date.fromisoformat(first)
            except ValueError:
                return float(cfg.claude_first_week_daily_usd)
            if date.fromisoformat(self.day()) < start + timedelta(days=cfg.claude_first_week_days):
                return float(cfg.claude_first_week_daily_usd)
        return float(cfg.claude_daily_usd)

    def gemini_spent(self) -> dict[str, Any]:
        n_fast, _ = self._sum("gemini:fast", month=False)
        n_good, _ = self._sum("gemini:good", month=False)
        _, usd_day = self._sum("gemini:%", month=False)
        _, usd_month = self._sum("gemini:%", month=True)
        return {"fast": n_fast, "good": n_good, "today_usd": round(usd_day, 4), "month_usd": round(usd_month, 4)}

    def view(self) -> dict[str, Any]:
        cfg = self.cfg()
        c = self.claude_spent()
        g = self.gemini_spent()
        _key, src = self.gemini_key()
        return {"claude": {"today": c["today"], "month": c["month"], "day_cap": self.claude_daily_cap(), "month_cap": cfg.claude_monthly_usd,
                           "key": bool(api_credit.api_key())},
                "gemini": {**g, "caps": dict(cfg.gemini_calls_per_day), "month_cap": cfg.gemini_monthly_usd if src == "shared" else None,
                           "key": src}}

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

    # ── Gemini ──
    def gemini_check(self, tier: str) -> None:
        cfg = self.cfg()
        key, src = self.gemini_key()
        if not key:
            raise CapError("no_key", "Gemini 키가 없어요 (서버 설정)", 503)
        cap = int((cfg.gemini_calls_per_day or {}).get(tier, 0))
        n, _ = self._sum(f"gemini:{tier}", month=False)
        if n >= cap:
            raise CapError("cap", f"오늘 Gemini({tier}) 몫 {cap}번을 다 썼어요 — 내일 다시")
        if src == "shared":
            _, usd = self._sum("gemini:%", month=True)
            if usd >= float(cfg.gemini_monthly_usd):
                raise CapError("cap", f"이번 달 취준 Gemini 몫 ${cfg.gemini_monthly_usd:g} 을 다 썼어요 — 다음 달까지 쉼")
        if api_credit.failed_until(prov=api_credit.GEMINI) and src == "shared":
            raise CapError("key_failed", "Gemini 키 오류로 잠깐 쉬는 중이에요", 503)

    async def gemini(self, kind: str, tier: str, system: str, user: str, *, schema: dict[str, Any] | None = None,
                     images: list[tuple[bytes, str]] | None = None, ver: int = 1, cache: bool = True, max_tokens: int = 4096,
                     timeout: float = 60) -> Any:
        """Gemini 한 번 (tier: fast|good). schema 가 있으면 dict, 없으면 글. 캐시에 있으면 부르지 않음."""
        cfg = self.cfg()
        model = cfg.gemini_good if tier == "good" else cfg.gemini_fast
        payload = {"s": system, "u": user, "schema": schema, "img": [hashlib.sha256(b).hexdigest() for b, _ in (images or [])]}
        ck = self.cache_key(kind, model, ver, payload)
        if cache:
            hit = self.cached(ck)
            if hit is not None:
                return hit
        self.gemini_check(tier)
        key, src = self.gemini_key()
        parts: list[dict[str, Any]] = [{"inlineData": {"mimeType": mt, "data": base64.b64encode(b).decode("ascii")}} for b, mt in (images or [])]
        parts.append({"text": user})
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if schema is not None:
            gen.update({"responseMimeType": "application/json", "responseJsonSchema": schema})
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
        if self.http is None:
            raise LlmError("연결 준비 안 됨", 503)
        status = 0
        try:
            r = await self.http.post(GEMINI_URL.format(model=model), json=body, timeout=timeout,
                                     headers={"x-goog-api-key": key or "", "Content-Type": "application/json"})
            status = r.status_code
        except Exception as e:  # noqa: BLE001
            log.info("jobprep gemini %s %s: %s", kind[:30], h8(user), type(e).__name__)
            raise LlmError("Gemini 에 닿지 못했어요") from None
        log.info("jobprep gemini %s model=%s in=%d hash=%s status=%d", kind[:30], model, len(user), h8(user), status)
        if status in (401, 403):
            if src == "shared":
                api_credit.mark_failed(f"gemini {status}", prov=api_credit.GEMINI)
            raise LlmError("Gemini 키 오류", 503)
        if status == 429:
            raise CapError("rate", "Gemini 가 잠깐 바빠요 — 조금 뒤 다시")
        if status >= 400:
            raise LlmError(f"Gemini 오류 {status}")
        try:
            out = r.json()
        except ValueError:
            out = {}
        meta = out.get("usageMetadata") or {}
        cached_t = int(meta.get("cachedContentTokenCount") or 0)
        nums = {"in": max(0, int(meta.get("promptTokenCount") or 0) - cached_t),
                "out": int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0), "cw": 0, "cr": cached_t, "ws": 0}
        cost = api_credit.usd(model, nums)
        self._add_usage(f"gemini:{tier}", cost)
        if src == "shared":
            api_credit.record(f"jobprep.{kind.split(':')[0]}"[:60], model, nums, prov=api_credit.GEMINI)
        cand = (out.get("candidates") or [{}])[0]
        text = "".join(p.get("text") or "" for p in ((cand.get("content") or {}).get("parts") or [])
                       if isinstance(p, dict) and not p.get("thought")).strip()
        if schema is None:
            if not text:
                raise LlmError("Gemini 답이 비었어요")
            result: Any = text
        else:
            try:
                s, e = text.find("{"), text.rfind("}")
                result = json.loads(text[s:e + 1]) if s >= 0 and e > s else None
            except ValueError:
                result = None
            if not isinstance(result, dict):
                raise LlmError("Gemini 답이 JSON 이 아니에요")
        if cache:
            self.put_cache(ck, kind, model, result)
        return result

    # ── Claude ──
    def claude_check(self) -> None:
        cfg = self.cfg()
        ok, why = api_credit.allow("jobprep.chat")
        if not ok:
            text = {"no_key": "Claude API 키가 없어요", "key_failed": "Claude 키 오류로 잠깐 쉬는 중이에요",
                    "month_cap": "블로그 서버 Claude 달 예산을 다 썼어요", "day_cap": "오늘 서버 Claude 몫을 다 썼어요"}.get(why, why)
            raise CapError("cap" if why.endswith("cap") else why, text + " · Gemini 로 물을 수 있어요", 429 if why.endswith("cap") else 503)
        spent = self.claude_spent()
        day_cap = self.claude_daily_cap()
        if spent["today"] >= day_cap:
            raise CapError("cap", f"오늘 Claude 몫(${day_cap:g})을 다 썼어요 · Gemini 로 물을 수 있어요")
        if spent["month"] >= float(cfg.claude_monthly_usd):
            raise CapError("cap", f"이번 달 Claude 몫(${cfg.claude_monthly_usd:g})을 다 썼어요 · Gemini 로 물을 수 있어요")

    async def claude(self, kind: str, system: str, user: str, *, max_tokens: int = 1500) -> str:
        self.claude_check()
        cfg = self.cfg()
        if not self.store.kv_get("claude_first_day"):
            self.store.kv_set("claude_first_day", self.day())
        before = self.ledger_jobprep()["month"]
        model = api_credit.api_model(cfg.claude_model, posted=False)
        try:
            text = await self.claude_ask(f"jobprep.{kind}"[:60], model, system, user, effort="low", max_tokens=max_tokens)
        except api_credit.ApiError:
            log.info("jobprep claude %s hash=%s: api error", kind[:30], h8(user))
            raise LlmError("Claude 가 답하지 않았어요 (구독으로 넘기지 않음)") from None
        after = self.ledger_jobprep()["month"]
        self._add_usage("claude", max(0.0, after - before))
        log.info("jobprep claude %s in=%d hash=%s ok", kind[:30], len(user), h8(user))
        return str(text)
