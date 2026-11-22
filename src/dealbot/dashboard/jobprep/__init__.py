"""취준 탭 (주인 11/22 — docs/jobprep_plan.md). /dashboard/jobprep/ 한 장 화면 + /dashboard/api/jobprep/… (모두 대시보드 로그인 아래).

- register(web, api) → Jobprep: 경로 등록 (설정 dashboard.jobprep.enabled 가 false 면 아무것도 안 붙임 → 404 · 아래 탭 링크도 숨김)
- Jobprep.tick(): scheduler 1분 일 — 정한 시각이 지났으면 뒤에서 하나 (도는 중이면 건너뜀 · 여유 메모리 min_free_mb 아래면 미룸 ·
  마지막 실행은 kv — 재시작해도 두 번 안 돎 · 3시간 안 놓친 건 따라잡음)
- GET 은 손님마다 1분 120번, POST 는 api.guard (같은 사이트 + X-Dash-Token) + 1분 30번. 답은 모두 Cache-Control: no-store.
- 데이터는 data_dir (기본 ~/jobprep-data, JOBPREP_DATA_DIR 가 이김) — 처음 쓸 때 연다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PAGE = "/dashboard/jobprep/"
API = "/dashboard/api/jobprep/"
KST = ZoneInfo("Asia/Seoul")
UPLOAD_MAX = {"acad": 8 * 1024 * 1024, "pron": 3 * 1024 * 1024, "speech": 3 * 1024 * 1024, "human": 3 * 1024 * 1024}
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
AUDIO_TYPES = ("audio/webm", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/mpeg", "audio/aac", "video/webm", "video/mp4",
               "audio/x-m4a")
FILE_RE = re.compile(r"^(acad|pron|speech|human)/([A-Za-z0-9_-]{1,40}\.(?:jpg|wav))$")
UA = "jobprep-reader/1.0 (public job listings; no login)"
COLLECT_NOW_PER_DAY = 2
CATCHUP_S = 3 * 3600
SLOT_TRIES = 2  # 처음 + 한 번 더 (리뷰 11/22)
AUDIO_KEEP_S = 90 * 86400
_on = False


def enabled() -> bool:
    """아래 탭 링크를 보일까 (register 가 켬)."""
    return _on


class Jobprep:
    def __init__(self, cfg: Any, *, http: Any, ua: str, clock: Any = time.time, data_dir: str | None = None) -> None:
        from dealbot.dashboard.jobprep import collectors, llm, pron, sandbox, store

        self.cfg = cfg
        self.clock = clock
        self.http = http
        self.store = store.Store(os.environ.get("JOBPREP_DATA_DIR") or data_dir or cfg().data_dir)
        self.llm = llm.Llm(self.store, cfg, http=http, clock=clock)
        self.fetcher = collectors.Fetcher(self.store, cfg, http, ua, clock=clock)
        self.sandbox = sandbox.Sandbox(cfg().sandbox_mode, tmp_root=None)
        self.run_worker = pron.run_worker
        self.bg: dict[str, asyncio.Future] = {}
        self.task: asyncio.Future | None = None
        self.task_name = ""
        self.collect_lock = asyncio.Lock()

    # ── 주기 일 ──
    def slots(self) -> list[tuple[str, str, int | None]]:
        c = self.cfg()
        hh, mm = (int(x) for x in c.collect_time.split(":"))
        ext = (datetime(2000, 1, 1, hh, mm) + timedelta(minutes=20)).strftime("%H:%M")
        out: list[tuple[str, str, int | None]] = [("collect", c.collect_time, None), ("extract", ext, None)]
        out += [(f"recheck:{t}", t, None) for t in c.recheck_times if t != c.collect_time]
        out += [("ladder", "03:30", None), ("clean", "04:00", None), ("weekly", "05:30", 0), ("growth", "05:50", 0)]
        return out

    def due(self, now: float) -> str:
        dt = datetime.fromtimestamp(now, KST)
        day = dt.strftime("%Y-%m-%d")
        runs = self.store.kv_get("runs", {}) or {}
        for name, hm, wd in self.slots():
            if wd is not None and dt.weekday() != wd:
                continue
            h, m = (int(x) for x in hm.split(":"))
            at = dt.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
            if at <= now <= at + CATCHUP_S and not self._done_today(runs.get(name), day):
                return name
        return ""

    @staticmethod
    def _done_today(entry: Any, day: str) -> bool:
        """그날 끝났나 — 'started' 로 남은 것(실패·재시작)은 3시간 안에 한 번 더 (tries 2 까지)."""
        if isinstance(entry, str):
            return entry == day
        if not isinstance(entry, dict) or entry.get("day") != day:
            return False
        return entry.get("state") == "done" or int(entry.get("tries") or 0) >= SLOT_TRIES

    def _mark(self, name: str, state: str) -> None:
        runs = self.store.kv_get("runs", {}) or {}
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        cur = runs.get(name) if isinstance(runs.get(name), dict) and runs[name].get("day") == day else {"day": day, "tries": 0}
        if state == "started":
            cur["tries"] = int(cur.get("tries") or 0) + 1
        cur["state"] = state
        runs[name] = cur
        self.store.kv_set("runs", runs)

    def tick(self) -> bool:
        if not self.cfg().enabled:
            return False
        from dealbot.dashboard.jobprep import pron, store

        try:
            if self.store.one("SELECT id FROM pron_takes WHERE state IN ('queued','running') LIMIT 1"):
                pron.pump(self)
        except Exception as e:  # noqa: BLE001
            log.info("jobprep pron pump: %s", type(e).__name__)
        if self.task is not None and not self.task.done():
            return False
        now = self.clock()
        name = self.due(now)
        if not name:
            return False
        if store.mem_free_mb() < int(self.cfg().min_free_mb):
            return False
        self._mark(name, "started")
        self.task_name = name
        self.task = asyncio.ensure_future(self._run(name))
        return True

    async def _run(self, name: str) -> None:
        """한 슬롯 — 끝까지 가면 'done', 오류면 'started' 그대로 (3시간 안 한 번 더)."""
        t0 = self.clock()
        try:
            result = await self._work(name)
            self._mark(name, "done")
        except Exception as e:  # noqa: BLE001
            result = {"error": getattr(e, "text", type(e).__name__)}
            log.warning("jobprep %s: %s", name, type(e).__name__)
        log_ = self.store.kv_get("run_log", []) or []
        log_.insert(0, {"name": name, "at": t0, "s": round(self.clock() - t0, 1), "result": json.loads(json.dumps(result, default=str))})
        self.store.kv_set("run_log", log_[:30])

    async def _work(self, name: str) -> Any:
        from dealbot.dashboard.jobprep import cases, ctci, english, jobs, pron, speech

        result: Any = None
        if name == "collect":
            async with self.collect_lock:
                result = await jobs.collect(self)
            result = {"boards": len(result.get("boards") or {})}
        elif name.startswith("recheck"):
            async with self.collect_lock:
                result = await jobs.collect(self, recheck_only=True)
            result = {"boards": len(result.get("boards") or {})}
        elif name == "extract":
            result = await jobs.extract_pending(self)
        elif name == "ladder":
            result = await ctci.pregen(self)
        elif name == "clean":
            result = self.clean()
        elif name == "weekly":
            result = {}
            for key, fn in (("eng", english.make_week), ("speech", speech.weekly), ("human", speech.human_week), ("pron", pron.weekly_practice)):
                try:
                    result[key] = await fn(self)
                except Exception as e:  # noqa: BLE001
                    result[key] = getattr(e, "text", type(e).__name__)
        elif name == "growth":
            result = {"growth": await jobs.growth(self)}
            try:
                result["cases"] = await cases.collect(self)
            except Exception as e:  # noqa: BLE001
                result["cases"] = getattr(e, "text", type(e).__name__)
        return result

    def clean(self) -> dict[str, int]:
        now = self.clock()
        out = {k: self.store.prune(k, AUDIO_KEEP_S, now) for k in ("pron", "speech", "human")}
        out["tmp"] = self.store.prune("tmp", 86400, now)
        out["cache"] = self.store.prune("cache", 7 * 86400, now)
        return out

    async def collect_now(self) -> dict[str, Any]:
        from dealbot.dashboard.jobprep import jobs
        from dealbot.dashboard.jobprep.store import JpError

        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        n = self.store.kv_get(f"collect_now:{day}", 0) or 0
        if n >= COLLECT_NOW_PER_DAY:
            raise JpError(429, "rate", f"수집 '지금 한 번'은 하루 {COLLECT_NOW_PER_DAY}번까지예요")
        if self.collect_lock.locked() or (self.task is not None and not self.task.done()):
            raise JpError(409, "busy", "지금 다른 일을 하는 중이에요 — 조금 뒤 다시")
        self.store.kv_set(f"collect_now:{day}", n + 1)

        async def go() -> None:
            async with self.collect_lock:
                await jobs.collect(self)
            try:
                await jobs.extract_pending(self)
            except Exception as e:  # noqa: BLE001
                log.info("jobprep extract: %s", type(e).__name__)

        self.task_name = "collect_now"
        self.task = asyncio.ensure_future(go())
        return {"started": True, "left": COLLECT_NOW_PER_DAY - n - 1}

    # ── ⑨ 설정 ──
    def settings_view(self) -> dict[str, Any]:
        from dealbot.dashboard.jobprep import jobs, pron

        c = self.cfg()
        return {"spend": self.llm.view(), "windows": list(c.apply_windows), "discharge": c.discharge, "book_finish_by": c.book_finish_by,
                "sandbox": {"mode": self.sandbox.mode(), "strong": self.sandbox.strong()}, "pron_ready": pron.ready(self),
                "sources": self.store.kv_get("src_state", {}), "collect_last": self.store.kv_get("collect_last", {}),
                "growth_last": self.store.kv_get("growth_last", {}), "speech_last": self.store.kv_get("speech_last", {}),
                "cases_last": self.store.kv_get("cases_last", {}), "runs": self.store.kv_get("run_log", [])[:10],
                "service_mode": bool(self.store.kv_get("service_mode", True)), "retake_max": c.retake_max_grade,
                "exclusions": jobs.view(self, {})["exclusions"], "busy": self.task_name if self.task is not None and not self.task.done() else "",
                "caps": {"claude_month": c.claude_monthly_usd, "claude_day": c.claude_daily_usd, "ladder_day": c.ladder_prebuild_daily_usd,
                         "gemini_calls": dict(c.gemini_calls_per_day)},
                "models": {"fast": self.llm.model_of("fast"), "good": self.llm.model_of("good"), "provider": self.llm.provider()}}

    # ── 올리기 ──
    async def upload(self, kind: str, ref: str, ctype: str, path: Path) -> dict[str, Any]:
        from dealbot.dashboard.jobprep import academic, audio, pron, speech
        from dealbot.dashboard.jobprep.store import JpError

        stamp = datetime.fromtimestamp(self.clock(), KST).strftime("%Y%m%d")
        name = f"{stamp}_{secrets.token_hex(5)}"
        if kind == "acad":
            if ctype not in IMAGE_TYPES:
                raise JpError(415, "bad_type", "사진(jpg·png·webp)만 올릴 수 있어요")
            data = await asyncio.to_thread(_to_jpeg, path)
            if data is None:
                raise JpError(400, "bad_image", "사진을 읽지 못했어요")
            self.store.write("acad", name + ".jpg", data)
            return {"doc_id": academic.add_doc(self, name + ".jpg"), "file": f"acad/{name}.jpg"}
        if kind not in ("pron", "speech", "human"):
            raise JpError(415, "bad_type", "받지 않는 종류예요")
        if ctype not in AUDIO_TYPES:
            raise JpError(415, "bad_type", "녹음(webm·mp4·ogg·wav)만 올릴 수 있어요")
        d = self.store.ensure_dir(kind)
        wav = d / f"{name}.wav"
        if not await audio.to_wav(path, wav, max_s=pron.MAX_AUDIO_S if kind == "pron" else audio.MAX_S):
            raise JpError(422, "audio", "녹음을 읽지 못했어요")
        os.chmod(wav, 0o600)
        ref_id = int(ref) if ref.isdigit() else -1
        if kind == "pron":
            tid = pron.enqueue(self, ref_id, wav.name)
            state = pron.pump(self)
            return {"take_id": tid, "pump": state, "file": f"pron/{wav.name}"}
        if kind == "speech":
            return {**(await speech.take(self, ref_id, wav, wav.name)), "file": f"speech/{wav.name}"}
        m = await audio.measure(wav)
        return {"file": f"human/{wav.name}", "measure": m}

    def file(self, rel: str) -> tuple[bytes, str] | None:
        m = FILE_RE.match(rel or "")
        if not m:
            return None
        data = self.store.read(m.group(1), m.group(2))
        if data is None:
            return None
        return data, "image/jpeg" if m.group(2).endswith(".jpg") else "audio/wav"


def make_client() -> Any:
    """취준 전용 http (리뷰 11/22): 딜봇 공용 client 와 쿠키 통을 나누지 않고, 쿠키는 아예 안 받음 · 시간 상한 · 넘겨주기 안 따라감 ·
    User-Agent 는 프로그램 이름만 (개인 정보 없음)."""
    import http.cookiejar

    import httpx

    class NoJar(http.cookiejar.CookieJar):
        def set_cookie(self, cookie: Any) -> None:
            return None

        def extract_cookies(self, response: Any, request: Any) -> None:
            return None

    jar = NoJar()
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False, cookies=jar,
                             headers={"User-Agent": UA}, limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))


def _to_jpeg(path: Path) -> bytes | None:
    import io

    from PIL import Image, ImageOps

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((2400, 2400))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _routes(jp: Jobprep) -> tuple[dict[str, Any], dict[str, Any]]:
    from dealbot.dashboard.jobprep import academic, cases, chat, ctci, english, jobs, library, pron, profile, speech, today

    get = {
        "today": lambda q: today.view(jp), "profile": lambda q: profile.view(jp), "acad": lambda q: academic.view(jp),
        "jobs": lambda q: jobs.view(jp, q), "jobs/board": lambda q: jobs.board_view(jp), "jobs/watch": lambda q: {"watch": jobs.watch_view(jp)},
        "cases": lambda q: cases.view(jp, q), "code/today": lambda q: ctci.today_view(jp), "code/progress": lambda q: ctci.today_view(jp),
        "lib": lambda q: library.view(jp, q), "speech": lambda q: speech.view(jp), "human/today": lambda q: {"topic": speech.human_today(jp)},
        "eng/today": lambda q: english.today_view(jp), "eng/cards": lambda q: english.cards(jp), "pron/set": lambda q: pron.view_set(jp),
        "pron/history": lambda q: pron.history(jp), "settings": lambda q: jp.settings_view(), "chat": lambda q: chat.view(jp, q),
    }
    post = {
        "profile": lambda b: profile.post(jp, b), "profile/intro": lambda b: profile.intro(jp, b),
        "acad/parse": lambda b: academic.parse(jp, b), "acad/courses": lambda b: academic.save_courses(jp, b),
        "acad/req": lambda b: academic.save_req(jp, b), "acad/plan": lambda b: academic.plan(jp, b),
        "jobs/mark": lambda b: jobs.mark(jp, b), "jobs/exclude": lambda b: jobs.exclude(jp, b), "jobs/restore": lambda b: jobs.restore(jp, b),
        "jobs/watch": lambda b: jobs.watch_post(jp, b), "jobs/add": lambda b: jobs.add(jp, b), "jobs/collect": lambda b: jp.collect_now(),
        "cases/compare": lambda b: cases.compare(jp, b), "cases/add": lambda b: cases.add(jp, b),
        "code/step": lambda b: ctci.step(jp, b), "code/run": lambda b: ctci.run(jp, b), "code/feedback": lambda b: ctci.feedback(jp, b),
        "code/compare": lambda b: ctci.compare(jp, b), "code/self": lambda b: ctci.self_grade(jp, b), "code/mode": lambda b: ctci.set_mode(jp, b),
        "lib/add": lambda b: library.add(jp, b), "lib/del": lambda b: library.delete(jp, b), "lib/summary": lambda b: library.summarize(jp, b),
        "speech/url": lambda b: speech.from_url(jp, b), "human/say": lambda b: speech.human_say(jp, b),
        "eng/expr": lambda b: english.set_expr(jp, b), "eng/after": lambda b: english.after(jp, b), "chat": lambda b: chat.send(jp, b),
    }
    return get, post


def register(web: Any, api: Any) -> Jobprep | None:
    """DashApi.register 끝에서 부른다. 꺼져 있으면 None (경로 없음 → 404)."""
    global _on
    bot = api.bot
    cfg = lambda: bot.settings.dashboard.jobprep  # noqa: E731
    if not cfg().enabled:
        _on = False
        return None
    from dealbot.dashboard.actions import RateWindow
    from dealbot.dashboard.api import jresp
    from dealbot.dashboard.jobprep import ctci, page, pron, sandbox
    from dealbot.dashboard.jobprep.llm import CapError, LlmError
    from dealbot.dashboard.jobprep.store import JpError, PathError

    jp = Jobprep(cfg, http=make_client(), ua=UA)
    get_routes, post_routes = _routes(jp)
    get_rate, post_rate, up_rate = RateWindow(120, 60.0), RateWindow(30, 60.0), RateWindow(20, 600.0)
    ns = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}

    def j(status: int, obj: Any) -> Any:
        return jresp(status, obj, ns)

    def fail(e: Exception) -> Any:
        if isinstance(e, (JpError, CapError, LlmError, sandbox.SandboxError)):
            return j(e.status, {"status": "error", "code": getattr(e, "code", "failed"), "text": e.text})
        if isinstance(e, PathError):
            return j(400, {"status": "error", "code": "path", "text": "잘못된 파일 이름이에요"})
        log.warning("jobprep handler: %s", type(e).__name__)
        return j(500, {"status": "error", "code": "failed", "text": "서버에서 처리하지 못했어요"})

    async def page_get(query: dict[str, str], headers: dict[str, str]) -> Any:
        boot = {"csrf": api.csrf_token(headers), "v": 1, "tabs": [k for k, _ in page.TABS]}
        return 200, page.html(boot), "text/html; charset=utf-8", page.headers()

    async def root(query: dict[str, str]) -> Any:
        return 301, PAGE, None, {"Cache-Control": "no-store"}

    async def api_get(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        if not get_rate.hit(caller.session):
            return j(429, {"status": "error", "code": "rate", "text": "너무 자주 열었어요 — 1분 뒤 다시"})
        rest = query.pop("path", "")
        try:
            m = re.fullmatch(r"code/q/(\d{1,2}\.\d{1,2})", rest)
            if m:
                return j(200, ctci.q_view(jp, m.group(1)))
            m = re.fullmatch(r"pron/take/(\d{1,9})", rest)
            if m:
                pron.pump(jp)
                return j(200, pron.take_view(jp, int(m.group(1))))
            if rest.startswith("file/"):
                got = jp.file(rest[5:])
                if got is None:
                    return 404, "없는 파일", "text/plain; charset=utf-8", ns
                return 200, got[0], got[1], {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
            fn = get_routes.get(rest)
            if fn is None:
                return j(404, {"status": "error", "code": "not_found", "text": "없는 주소예요"})
            out = fn(query)
            if asyncio.iscoroutine(out):
                out = await out
            return j(200, out)
        except Exception as e:  # noqa: BLE001
            return fail(e)

    def post_handler(name: str, fn: Any) -> Any:
        async def handler(body: dict[str, Any], headers: dict[str, str]) -> Any:
            g = api.guard(headers)
            if len(g) == 4:
                return g
            caller, _ident = g
            if not post_rate.hit(caller.session):
                return j(429, {"status": "error", "code": "rate", "text": "너무 자주 눌렀어요 — 1분 뒤 다시"})
            try:
                out = fn(body)
                if asyncio.iscoroutine(out):
                    out = await out
                return j(200, {"status": "ok", **(out if isinstance(out, dict) else {"result": out})})
            except Exception as e:  # noqa: BLE001
                return fail(e)

        return handler

    async def upload(query: dict[str, str], headers: dict[str, str], path: Path) -> Any:
        try:
            g = api.guard(headers)
            if len(g) == 4:
                return g
            caller, _ident = g
            if not up_rate.hit(caller.session):
                return j(429, {"status": "error", "code": "rate", "text": "너무 자주 올렸어요 — 잠깐 뒤 다시"})
            kind = (headers.get("x-jp-kind") or "").strip()
            ctype = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            try:
                return j(200, {"status": "ok", **(await jp.upload(kind, (headers.get("x-jp-ref") or "").strip(), ctype, path))})
            except Exception as e:  # noqa: BLE001
                return fail(e)
        finally:
            path.unlink(missing_ok=True)

    def tmp_path() -> Path:
        return jp.store.ensure_dir("tmp") / f"up_{secrets.token_hex(8)}"

    web.route("/dashboard/jobprep", root)
    web.route(PAGE, page_get, with_headers=True)
    web.route_prefix(API, api_get, with_headers=True)

    def exact(name: str) -> Any:  # 같은 주소에 POST 도 있는 GET (profile·chat·jobs/watch …) — 정확한 경로로도 붙임 (없으면 405)
        async def handler(query: dict[str, str], headers: dict[str, str]) -> Any:
            return await api_get({**query, "path": name}, headers)

        return handler

    for name in get_routes:
        web.route(API + name, exact(name), with_headers=True)
    for name, fn in post_routes.items():
        web.route_json(API + name, post_handler(name, fn), max_body=64 * 1024 if name not in ("eng/after", "code/run") else 160 * 1024)
    web.route_stream(API + "upload", upload, max_body=lambda h: UPLOAD_MAX.get((h.get("x-jp-kind") or "").strip(), 0), tmp_path=tmp_path)
    _on = True
    return jp
