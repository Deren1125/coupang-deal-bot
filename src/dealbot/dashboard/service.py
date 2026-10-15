"""채널 대시보드 본체: 카드(소스) 목록, 정해진 시각에 읽기, '다시 읽기', 직접 입력, 웹 경로.

경로 (/dashboard 아래 전부 로그인 — DASHBOARD_USER / DASHBOARD_PASSWORD 가 둘 다 있을 때만 열림, 아니면 404.
로그인은 로그인 화면의 세션 쿠키(login.py, 30일) 또는 기본 인증. 너무 많이 틀리면 잠시 429 — auth.FailureLimiter.
로그인 없이 연 화면(/dashboard·refresh·input)은 로그인 화면으로 보내고, data.json·POST·그 밖의 경로는 401):
  /dashboard                    페이지 (맨 위 '지금 할 일' — todo.py)
  /dashboard/login              로그인 화면 (GET) · 로그인 (POST 폼: t·user·password) — 로그인 없이 열림
  POST /dashboard/logout        로그아웃 (폼: t) — 그 쿠키의 세션을 지움, 로그인 없이 열림
  /dashboard/manifest.webmanifest, /dashboard/static/<아이콘>   바탕화면 앱용 (webapp.py) — 로그인 없이 열림
  /dashboard/refresh?src=키      그 카드를 지금 다시 읽고 돌아감 (카드마다 refresh_min_minutes 에 한 번)
  /dashboard/input?src=키&지표=값&date=YYYY-MM-DD   직접 입력 저장 후 돌아감 (숫자만, 날짜는 오늘~manual_max_days_back)
  /dashboard/data.json          같은 내용을 JSON 으로 (나중에 다른 곳에서 쓰려고, 폼 토큰은 없음)
  POST /dashboard/link          '링크 기다리는 딜' 카드의 '링크 넣기' (폼: t·q=글 번호·url) → 텔레그램 답장과 같은 attach_link
refresh·input·link 는 페이지가 넣어 주는 t(폼 토큰)가 맞아야 한다 — 다른 사이트가 링크로 값을 바꾸지 못하게.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import re
import secrets
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from dealbot.config import DashboardConfig
from dealbot.dashboard import render, webapp
from dealbot.dashboard import todo as todo_mod
from dealbot.dashboard.auth import (
    FailureLimiter,
    check_basic,
    check_credentials,
    client_key,
    password_problems,
)
from dealbot.dashboard.login import (
    LOGIN_BODY_MAX,
    LOGIN_COOKIE,
    LOGIN_NOTICES,
    LOGIN_PATH,
    LOGOUT_PATH,
    SESSION_COOKIE,
    SESSION_DAYS,
    AttemptLimiter,
    LoginCsrf,
    SessionStore,
    clear_session_cookie,
    cookie_values,
    login_cookie,
    login_page,
    session_cookie,
    wait_text,
)
from dealbot.dashboard.model import (
    ERROR,
    GOOD,
    KINDS,
    MET,
    SKIPPED,
    UNKNOWN,
    UNMET,
    CardView,
    FetchResult,
    fmt_num,
    fmt_signed,
    safe_error,
    safe_text,
)
from dealbot.dashboard.sources import CardData, Source, SourceContext, load_all
from dealbot.dashboard.sources.blog_waiting import BlogWaitingSource
from dealbot.dashboard.sources.link_waiting import LinkWaitingSource
from dealbot.shops import find_urls
from dealbot.storage.db import LINK_WAITING, QueueItem
from dealbot.utils.timeutil import from_iso, to_iso
from dealbot.web import Throttled, WebServer, page

log = logging.getLogger(__name__)

KV_STATUS = "dash:status:{key}"  # 마지막 읽기 결과 {status, read_at, tried_at, note}
KV_SLOT = "dash:slot"  # 마지막으로 다 읽은 정해진 시각 ("YYYY-MM-DD HH:MM")
KV_LIVE_AT = "dash:live_at"  # 로컬 기록(Blog-Auto 파일·봇 DB)을 마지막으로 저장한 시각
KV_FORM_TOKEN = "dash:form_token"

LINK_PATH = "/dashboard/link"
LINK_BODY_MAX = 16 * 1024  # 폼 하나 (토큰·번호·링크 칸 1,500자 — 한글 공유 글도 들어가게)
LINK_TEXT_MAX = 2000  # 링크 칸 글자 수 상한 (넘으면 붙이지 않음)
LINK_RATE = 10  # '링크 넣기' 는 이 프로세스 전체에서 1분에 이만큼까지 (토큰 맞혀 보기·연타 막기)
_QUEUE_ID_RE = re.compile(r"^[0-9]{1,9}$")
_NUMBER_RE = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)$")  # 1234 또는 1,234 (전각 숫자·소수·음수는 안 받음)
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# 로그인 없이 열면 401 대신 로그인 화면으로 보내는 경로 (브라우저로 여는 화면들). data.json·POST·그 밖은 401
PAGE_PATHS = frozenset({"/dashboard", "/dashboard/", "/dashboard/refresh", "/dashboard/input"})
LOGOUT_BODY_MAX = 1024

SECURITY_HEADERS = {
    # img-src·manifest-src 'self': 바탕화면 앱 아이콘과 매니페스트 (webapp.py). 나머지는 그대로 아무것도 안 불러옴
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; manifest-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

# 돌아간 뒤 보일 안내 (주소의 msg=코드 → 고정 문구. 주소의 글자를 그대로 보여 주지 않음).
# 카드에서 누른 것(src=카드 키)은 그 카드 안 머리 밑에 — 돌아가면 그 카드로 바로 내려가므로 맨 위에 두면 안 보인다
NOTICES: dict[str, tuple[str, str]] = {
    "saved": ("저장했어요.", "ok"),
    "refreshed": ("다시 읽었어요.", "ok"),
    "reread": ("다시 읽었어요.", "ok"),
    "too_soon": ("방금 읽었어요 — 카드마다 {minutes}분에 한 번만 다시 읽을 수 있어요.", "bad"),
    "failed": ("읽지 못했어요 — 카드 아래 안내를 확인해 주세요.", "bad"),
    "skipped": ("이번엔 건너뛰었어요 — 호출 한도를 아끼려고 지난 값을 그대로 보여 줘요.", "bad"),
    "bad_number": ("숫자만 넣어 주세요 (예: 1,234).", "bad"),
    "bad_date": ("날짜가 올바르지 않아요 — 오늘부터 최근 {days}일 안의 날짜만 넣을 수 있어요.", "bad"),
    "empty": ("넣은 값이 없어요.", "bad"),
    "unknown": ("없는 카드예요.", "bad"),
    "not_manual": ("이 카드는 직접 넣는 값이 없어요.", "bad"),
    # '링크 넣기' ({qid} 는 주소의 q 가 숫자일 때만 '#12 ', 아니면 빈칸 — 넣은 링크 글자는 화면에 다시 보이지 않음)
    "linked": ("{qid}링크를 넣었어요 — 올릴 차례가 되면 채널에 올라가요.", "ok"),
    # 화면을 연 뒤 더 좋은 딜에 자리를 내준 글 (텔레그램 답장과 같은 길 — 주인 결정 10/15)
    "linked_twin": ("{qid}같은 상품이 새 번호로 다시 줄 서 있어 그 딜에 링크를 넣었어요 (텔레그램에 새 번호를 알렸어요) — "
                    "올릴 차례가 되면 채널에 올라가요.", "ok"),
    "link_replaced_late": ("{qid}더 좋은 딜에 자리를 내준 뒤 링크 유효 시간이 지나 넣지 않았어요 — "
                           "아직 싸게 팔고 있으면 텔레그램에서 /post 로 직접 올려 주세요.", "bad"),
    "link_twin_review": ("{qid}같은 상품이 새 번호로 다시 줄 섰는데 확인(/ok)을 기다리는 글이라 링크를 넣지 않았어요 — "
                         "텔레그램에서 /ok 나 /skip 으로 답해 주세요.", "bad"),
    "link_not_waiting": ("{qid}지금은 링크를 기다리는 딜이 아니에요 (이미 넣었거나 내려간 딜) — 아무것도 바꾸지 않았어요.", "bad"),
    "link_bad": ("{qid}이 링크는 붙일 수 없어요 — 그 몰 앱에서 만든 상품·제휴 링크(https://…)를 넣어 주세요. "
                 "텔레그램 주소(t.me)는 안 돼요.", "bad"),
    "link_failed": ("{qid}링크를 넣지 못했어요 — 텔레그램 /pending 에서 확인해 주세요.", "bad"),
    "link_busy": ("잠깐만요 — 링크 넣기는 1분에 {rate}번까지예요. 조금 뒤에 다시 눌러 주세요.", "bad"),
    "link_off": ("지금은 대시보드에서 링크를 넣을 수 없어요 — 텔레그램 링크 요청에 답장해 주세요.", "bad"),
}


class LinkDesk(Protocol):
    """'링크 넣기' 가 부르는 봇 쪽 (app.DealBot). 텔레그램 링크 요청에 답장했을 때와 같은 함수들."""

    def owner_link_target(self, queue_id: int) -> QueueItem | None: ...

    def owner_link_problem(self, queue_id: int, url: str) -> str | None: ...

    async def attach_link(self, queue_id: int, url: str) -> str: ...

    async def refresh_pending_notice(self) -> None: ...


class Dashboard:
    def __init__(self, cfg: DashboardConfig, ctx: SourceContext, *, user: str | None = None, password: str | None = None,
                 public_base_url: str | None = None, links: LinkDesk | None = None) -> None:
        self.cfg = cfg
        self.ctx = ctx
        self.db = ctx.db
        self._user = user
        self._password = password
        self.public_base_url = public_base_url
        self.links = links
        self._link_times: deque[datetime] = deque()  # '링크 넣기' 를 받은 시각 (1분 창)
        self.problems: list[str] = []
        types = load_all()
        self.sources: list[Source] = []
        for card in cfg.cards:
            if not card.enabled:
                continue
            cls = types.get(card.type)
            if cls is None:
                self.problems.append(f"대시보드 카드 '{card.key}': 모르는 종류 '{card.type}' (쓸 수 있는 것: {', '.join(sorted(types))})")
                continue
            if cls.kind not in KINDS:
                self.problems.append(f"대시보드 카드 '{card.key}': kind '{cls.kind}' 가 이상함")
                continue
            self.sources.append(cls(card, ctx))
        self.by_key = {s.key: s for s in self.sources}
        # '링크 넣기' 안내를 넣을 카드 (링크 기다리는 딜). 없으면 맨 위에
        self.links_key = next((s.key for s in self.sources if isinstance(s, LinkWaitingSource)), None)
        # '지금 할 일' 버튼이 내려갈 카드 (발행 대기 글)
        self.waiting_key = next((s.key for s in self.sources if isinstance(s, BlogWaitingSource)), None)
        self._locks: dict[str, asyncio.Lock] = {}
        self._token: str | None = None
        self.limiter = FailureLimiter()  # 틀린 비밀번호 (기본 인증·로그인 화면 합쳐서)
        self.login_rate = AttemptLimiter()  # 로그인 버튼 1분 5번 (맞든 틀리든)
        self.sessions = SessionStore(self.db, user=user, password=password, now=ctx.now)
        self.csrf = LoginCsrf(self.db, clock=lambda: ctx.now().timestamp())

    # ------------------------------------------------------------ 상태
    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def served(self) -> bool:
        return self.cfg.enabled and bool(self._user and self._password)

    def check_auth(self, headers: dict[str, str]) -> bool | Throttled:
        """로그인 세션 쿠키(로그인 화면) 또는 기본 인증.
        세션은 32바이트 난수라 맞혀 볼 수 없어 막힘과 상관없이 먼저 본다 (다른 곳이 마구 넣어 봐도 주인 휴대폰은 그대로 열림).
        기본 인증은 너무 많이 틀린 손님(또는 전체)이면 확인하지 않고 Throttled → 429.
        Authorization 없이 온 요청(브라우저가 처음 물어보기 전)은 틀린 시도로 세지 않는다 (화면은 로그인 화면으로)."""
        if self.sessions.any_valid(cookie_values(headers, SESSION_COOKIE)):
            return True
        header = headers.get("authorization")
        if not header:
            return False
        client = client_key(headers)
        wait = self.limiter.blocked(client)
        if wait is not None:
            return Throttled(wait)
        if check_basic(header, self._user, self._password):
            self.limiter.success(client)
            return True
        self._failed(client)
        return False

    def _failed(self, client: str) -> None:
        self.limiter.failure(client)
        if n := self.limiter.log_due():  # 1분에 한 줄만 (마구 넣어 봐도 로그가 밀려나지 않게)
            log.warning("dashboard: 인증 실패 %d번 (값은 기록하지 않음)", n)

    @property
    def form_token(self) -> str:
        """refresh·input 주소에 붙는 폼 토큰 (DB 에 둬서 재시작해도 열어 둔 페이지가 그대로 됨). 비밀값과 무관한 난수."""
        if self._token is None:
            tok = self.db.kv_get(KV_FORM_TOKEN)
            if not tok:
                tok = secrets.token_urlsafe(18)
                self.db.kv_set(KV_FORM_TOKEN, tok)
            self._token = tok
        return self._token

    def _token_ok(self, given: str | None) -> bool:
        return hmac.compare_digest((given or "").encode("utf-8"), self.form_token.encode("utf-8"))

    def status(self, key: str) -> dict[str, Any]:
        try:
            data = json.loads(self.db.kv_get(KV_STATUS.format(key=key)) or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------ 읽기·저장
    def _store(self, source: Source, res: FetchResult, *, origin: str = "auto") -> int:
        """읽은 값을 metrics 표에. 숫자가 아닌 값·무한·NaN 은 그 줄만 버린다 (나머지는 저장, 화면이 깨지지 않게)."""
        today = self.ctx.today().isoformat()
        items: list[tuple[str, str, Any]] = []
        for day, values in res.days.items():
            if _DAY_RE.match(day):
                items.extend((m, day, v) for m, v in values.items())
        items.extend((m, today, v) for m, v in res.values.items())
        rows: list[tuple[str, str, str, float]] = []
        dropped = 0
        for metric, day, value in items:
            try:
                f = float(value)
            except (TypeError, ValueError):
                f = math.nan
            if math.isfinite(f):
                rows.append((source.key, metric, day, f))
            else:
                dropped += 1
        if dropped:
            log.warning("dashboard %s: 숫자가 아닌 값 %d개는 저장하지 않음", source.key, dropped)
        return self.db.metric_put_many(rows, origin=origin, now=self.ctx.now())

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def collect_one(self, source: Source) -> FetchResult:
        """api 소스 하나를 지금 읽어 저장 (같은 카드를 동시에 두 번 읽지 않음)."""
        async with self._lock(source.key):
            return await self._collect(source)

    async def _collect(self, source: Source) -> FetchResult:
        now = self.ctx.now()
        st = self.status(source.key)
        st["tried_at"] = to_iso(now)
        self.db.kv_set(KV_STATUS.format(key=source.key), json.dumps(st, ensure_ascii=False))  # 읽는 중에도 한도가 보이게
        try:
            res = await asyncio.wait_for(source.fetch(), timeout=self.cfg.fetch_timeout_seconds)
        except TimeoutError:
            res = FetchResult(ERROR, note=f"응답이 {self.cfg.fetch_timeout_seconds:.0f}초 안에 오지 않아 건너뜀")
        except Exception as e:  # noqa: BLE001 — 소스 하나가 터져도 대시보드·봇은 계속
            log.warning("dashboard %s fetch failed: %s", source.key, safe_error(e))
            res = FetchResult(ERROR, note=f"읽기 실패 ({safe_error(e)})")
        try:
            self._store(source, res)
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard %s store failed: %s", source.key, e)
        if res.status != SKIPPED:
            st["status"] = res.status
            if res.status in GOOD:
                st["read_at"] = to_iso(res.read_at or now)
        st["note"] = safe_text(res.note, 240) if res.note else None
        self.db.kv_set(KV_STATUS.format(key=source.key), json.dumps(st, ensure_ascii=False))
        if res.status not in GOOD and res.status != SKIPPED:
            log.info("dashboard %s: %s %s", source.key, res.status, st["note"] or "")
        return res

    def run_live(self, source: Source) -> FetchResult | None:
        """로컬 값(봇 DB·Blog-Auto 파일)을 읽고 저장. 실패해도 페이지는 뜬다."""
        if not source.has_live:
            return None
        try:
            res = source.live()
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard %s live read failed: %s", source.key, e)
            return FetchResult(ERROR, note=f"읽기 실패 ({safe_error(e)})")
        if res is not None:
            try:
                self._store(source, res)
            except Exception as e:  # noqa: BLE001
                log.warning("dashboard %s store failed: %s", source.key, e)
            if res.note:
                res.note = safe_text(res.note, 240)
        return res

    def current_slot(self, now_local: datetime) -> str | None:
        """지금 기준 가장 최근의 정해진 읽기 시각 ("YYYY-MM-DD HH:MM"). 오늘 첫 시각 전이면 어제 마지막 시각."""
        times = self.cfg.collect_times
        if not times:
            return None
        hhmm = now_local.strftime("%H:%M")
        past = [t for t in times if t <= hhmm]
        if past:
            return f"{now_local.date().isoformat()} {past[-1]}"
        return f"{(now_local.date() - timedelta(days=1)).isoformat()} {times[-1]}"

    async def tick(self) -> list[str]:
        """스케줄러가 1분마다 부른다: 새 정해진 시각이 지났으면 api 카드를 모두 읽고, 로컬 기록은 export_interval 마다."""
        ran: list[str] = []
        if not self.enabled:
            return ran
        now = self.ctx.now()
        slot = self.current_slot(self.ctx.local_now())
        if slot and self.db.kv_get(KV_SLOT) != slot:
            self.db.kv_set(KV_SLOT, slot)  # 먼저 적어 둔다 (읽다 죽어도 같은 시각을 계속 다시 읽지 않게)
            for s in self.sources:
                if s.kind == "api":
                    await self.collect_one(s)
                    ran.append(s.key)
        last = from_iso(self.db.kv_get(KV_LIVE_AT))
        if last is None or now - last >= timedelta(minutes=self.cfg.export_interval_minutes):
            self.db.kv_set(KV_LIVE_AT, to_iso(now))
            for s in self.sources:
                if s.has_live and self.run_live(s) is not None:
                    ran.append(f"{s.key}:live")
        return ran

    async def refresh(self, key: str) -> str:
        """'다시 읽기' 버튼: 결과 코드 (refreshed|reread|too_soon|failed|skipped|unknown)."""
        source = self.by_key.get(key)
        if source is None:
            return "unknown"
        if source.kind != "api":
            self.run_live(source)
            return "reread"
        async with self._lock(key):  # 두 번 연달아 눌러도 한도 확인과 읽기가 엇갈리지 않게
            tried = from_iso(self.status(key).get("tried_at"))
            if tried is not None and self.ctx.now() - tried < timedelta(minutes=self.cfg.refresh_min_minutes):
                return "too_soon"
            res = await self._collect(source)
        self.run_live(source)
        if res.status == SKIPPED:
            return "skipped"
        return "refreshed" if res.status in GOOD else "failed"

    def save_manual(self, key: str, query: dict[str, str]) -> str:
        """직접 입력: 결과 코드 (saved|bad_number|bad_date|empty|unknown|not_manual). 하나라도 틀리면 아무것도 저장 안 함."""
        source = self.by_key.get(key)
        if source is None:
            return "unknown"
        if not source.manual_fields:
            return "not_manual"
        today = self.ctx.today()
        raw_day = (query.get("date") or "").strip()
        if raw_day:
            if not _DAY_RE.match(raw_day):
                return "bad_date"
            try:
                day = date.fromisoformat(raw_day)
            except ValueError:
                return "bad_date"
        else:
            day = today
        oldest = today - timedelta(days=self.cfg.manual_max_days_back)
        if day > today or day < oldest:
            return "bad_date"
        rows: list[tuple[str, str, str, float]] = []
        for f in source.manual_fields:
            raw = (query.get(f.metric) or "").strip().replace(" ", "")
            if not raw:
                continue
            if len(raw) > 13 or not _NUMBER_RE.match(raw):
                return "bad_number"
            value = float(int(raw.replace(",", "")))
            if value > f.max_value:
                return "bad_number"
            target = day + timedelta(days=f.day_offset)
            if target < oldest - timedelta(days=1):
                return "bad_date"
            rows.append((source.key, f.metric, target.isoformat(), value))
        if not rows:
            return "empty"
        self.db.metric_put_many(rows, origin="manual", now=self.ctx.now())
        log.info("dashboard %s: 직접 입력 %d개 저장 (%s)", key, len(rows), day.isoformat())
        return "saved"

    def link_rate_ok(self) -> bool:
        """'링크 넣기' 1분 창 (이 프로세스 전체). 넘으면 False — 그 요청은 아무것도 하지 않는다."""
        now = self.ctx.now()
        while self._link_times and now - self._link_times[0] >= timedelta(minutes=1):
            self._link_times.popleft()
        if len(self._link_times) >= LINK_RATE:
            return False
        self._link_times.append(now)
        return True

    async def attach_owner_link(self, queue_id: int, text: str) -> str:
        """'링크 넣기' 결과 코드 (linked|linked_twin|link_not_waiting|link_replaced_late|link_twin_review|link_bad|link_failed|link_off).
        텔레그램 링크 요청에 링크로 답장했을 때와 같은 길: 링크를 붙일 글 찾기(DealBot.owner_link_target — 화면을 연 뒤 더 좋은
        딜에 자리를 내준 글도 attach_link 와 같은 _replaced_target 으로: 유효 시간 안이면 받고, 같은 상품이 다시 줄 섰으면 그 글로,
        지났으면 그 이유) → 글에서 주소 찾기(shops.find_urls 의 첫 주소) → attach_link 와 같은 기준(DealBot.owner_link_problem)
        으로 거르기 → attach_link 한 번 → 고정 공지 고치기.
        링크를 기다리는 글(LINK_WAITING·유효 시간 안의 자리를 내준 글)이 아니거나 이미 링크가 붙은 글이면 아무것도 부르지 않는다
        (두 번 눌러도 한 번만 붙음)."""
        if self.links is None:
            return "link_off"
        item = self.db.get_queue_item(queue_id)
        target = self.links.owner_link_target(queue_id) if item is not None else None
        if item is None or target is None:
            return "link_not_waiting"
        moved = target.id != queue_id  # 자리를 내준 글 → 다시 줄 선 같은 상품 글 (텔레그램 답장도 그 글로 받음)
        if moved:
            if target.status == "awaiting_approval":
                return "link_twin_review"
            if target.deal.affiliate_url:
                return "link_not_waiting"  # 그 글엔 이미 링크가 있음 (한 번 넣은 뒤 같은 칸을 또 누름)
        elif target.status not in LINK_WAITING:
            # 유효 시간 안의 자리를 내준 글은 target 이 링크를 기다리는 글처럼 열려 여기 오지 않는다 — 지났으면 그 이유를
            return "link_replaced_late" if target.link_replaced else "link_not_waiting"
        if len(text) > LINK_TEXT_MAX:
            return "link_bad"
        urls = find_urls(text)
        if not urls:
            return "link_bad"
        url = urls[0]
        if self.links.owner_link_problem(queue_id, url) is not None:
            return "link_bad"
        await self.links.attach_link(queue_id, url)
        after = self.db.get_queue_item(target.id)
        try:
            await self.links.refresh_pending_notice()  # 텔레그램 고정 공지('내 링크 기다리는 글')도 답장했을 때처럼 고침
        except Exception as e:  # noqa: BLE001 — 공지를 못 고쳐도 링크는 붙었다
            log.warning("dashboard link: pending notice refresh failed: %s", safe_error(e))
        if after is None or after.status in (*LINK_WAITING, "skipped") or not after.deal.affiliate_url:
            return "link_failed"
        if moved:
            log.info("dashboard: #%d 링크를 대시보드에서 넣음 (자리를 내준 #%d 와 같은 상품)", target.id, queue_id)
            return "linked_twin"
        log.info("dashboard: #%d 링크를 대시보드에서 넣음", queue_id)
        return "linked"

    # ------------------------------------------------------------ 화면 값
    def views(self) -> list[CardView]:
        out = []
        for s in self.sources:
            live = self.run_live(s)
            data = CardData(self.ctx, s.key, status=self.status(s.key) if s.kind == "api" else {}, live=live)
            try:
                out.append(s.card(data))
            except Exception as e:  # noqa: BLE001 — 카드 하나가 깨져도 페이지는 뜬다
                log.exception("dashboard card %s failed", s.key)
                out.append(CardView(key=s.key, title=s.title, type=s.type, kind=s.kind, icon=s.icon,
                                    hints=[f"카드를 그리지 못함 ({safe_error(e)})"], fetch_status=ERROR))
        return out

    @staticmethod
    def chip_counts(views: list[CardView]) -> dict[str, int]:
        """맨 위 줄의 칩 개수 (조건 충족·미충족·판단 불가)."""
        return {k: sum(1 for v in views if v.status == k) for k in (MET, UNMET, UNKNOWN)}

    @staticmethod
    def summary(views: list[CardView]) -> list[tuple[str, str, str]]:
        """맨 위 요약 줄: (이름, 값, 작은 글)."""
        def total(name: str) -> tuple[float | None, int]:
            vals = [v.summary[name] for v in views if v.summary.get(name) is not None]
            return (sum(vals) if vals else None), len(vals)

        aud, n_aud = total("audience_delta")
        posts, _ = total("posts_today")
        visitors, n_vis = total("visitors_yesterday")
        revenue, _ = total("revenue_7d")
        orders, _ = total("orders_7d")
        return [
            ("오늘 늘어난 팔로워", fmt_signed(aud) if aud is not None else "-",
             f"구독자·팔로워·이웃, 어제와 비교한 카드 {n_aud}개" if n_aud else "어제·오늘 값이 다 있으면 보여요"),
            ("오늘 올린 글", f"{fmt_num(posts)}개" if posts is not None else "-", "핫딜 채널 딜 + 블로그 발행"),
            ("어제 블로그 방문자", f"{fmt_num(visitors)}명" if visitors is not None else "-",
             f"직접 넣은 블로그 {n_vis}개" if n_vis else "블로그 카드에서 직접 넣어 주세요"),
            ("쿠팡 수익 · 최근 7일", f"{fmt_num(revenue)}원" if revenue is not None else "-",
             (f"주문 {fmt_num(orders)}건 · 어제까지" if orders is not None else "어제까지") if revenue is not None
             else "실적을 읽으면 보여요"),
        ]

    def todo(self) -> todo_mod.TodoView:
        """맨 위 '지금 할 일': Blog-Auto 기록 파일의 todo + 봇 DB 지금 상태 (todo.build). 바깥 요청 없음.
        만들다 터져도 페이지는 뜬다 (빈 목록 + 안내 한 줄)."""
        try:
            reader = self.ctx.blogauto
            export = reader.read() if reader is not None else None
            labels = {}
            for c in self.cfg.cards:  # 블로그 이름 → 블로그 카드 제목 ('발행 대기 글' 줄 이름과 같게)
                blog = c.options.get("blog")
                if c.enabled and c.type == "naver_blog" and isinstance(blog, str) and blog.strip():
                    labels.setdefault(blog.strip().lower(), c.title or c.key)
            return todo_mod.build(
                export, self.db, self.ctx.now(), ttl_hours=self.ctx.link_ttl_hours,
                links_anchor=f"#card-{self.links_key}" if self.links_key else None,
                waiting_anchor=f"#card-{self.waiting_key}" if self.waiting_key else None,
                stale_hours=self.cfg.blogauto_stale_hours, labels=labels, shop_label=self.ctx.shop_label,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("dashboard todo failed")
            return todo_mod.TodoView(hints=[f"할 일 목록을 만들지 못함 ({safe_error(e)})"])

    @staticmethod
    def _updated_at(views: list[CardView]) -> datetime | None:
        """맨 위 '마지막 업데이트': 바깥에서 읽은 값(API·Blog-Auto 기록·직접 입력) 중 가장 최근 (봇 DB 카드는 늘 지금이라 뺌)."""
        times = [v.read_at for v in views if v.read_at is not None and v.kind != "internal"]
        return max(times) if times else None

    def page_html(self, notice: tuple[str, str] | None = None, *, notice_src: str | None = None) -> str:
        """notice_src 가 카드 키면 안내를 그 카드 안에, 아니면 맨 위에."""
        views = self.views()
        times = ", ".join(self.cfg.collect_times) or "-"
        footer = f"API 로 읽는 카드는 매일 {times} 에 자동으로 읽어요 ('다시 읽기'는 카드마다 {self.cfg.refresh_min_minutes}분에 한 번). "
        if any("own_stats" in v.detail for v in views):  # Blog-Auto 가 블로그 자기 통계를 보내기 시작한 뒤
            footer += ("네이버 블로그 이웃 수·방문자·조회수는 Blog-Auto 가 블로그마다 하루 한 번(날마다 다른 시각) 읽어 와요 — "
                       "읽은 뒤에 이웃 수를 직접 넣으면 그날은 넣은 값이 보여요. 게시글 수와 오픈채팅 참여자는 카드에서 직접 넣어요.")
        else:
            footer += "네이버 블로그 이웃 수·방문자·게시글 수와 오픈채팅 참여자는 카드에서 직접 넣어요."
        return render.render_page(
            title=self.cfg.title, views=views, summary=self.summary(views), updated_at=self._updated_at(views),
            token=self.form_token, today=self.ctx.today(), tz=self.ctx.tz, spark_days=self.cfg.spark_days,
            notice=notice, notice_src=notice_src if notice_src in self.by_key else None, footer=footer,
            min_day=self.ctx.today() - timedelta(days=self.cfg.manual_max_days_back), counts=self.chip_counts(views),
            todo=self.todo(), now=self.ctx.now(), logout_path=LOGOUT_PATH,
        )

    def json_text(self) -> str:
        views = self.views()
        return render.render_json(title=self.cfg.title, views=views, generated_at=self.ctx.now(), tz=self.ctx.tz,
                                  summary=self.summary(views), todo=self.todo())

    # ------------------------------------------------------------ 웹
    def register(self, web: WebServer) -> bool:
        """아이디·비밀번호가 둘 다 있을 때만 경로를 연다 (없으면 /dashboard 아래는 모두 404).
        인증은 경로마다가 아니라 /dashboard 앞부분 전체에 건다 — 나중에 이 아래 경로를 auth 없이 붙여도 열리지 않게."""
        if not self.served:
            web.guard_prefix("/dashboard", None)
            return False
        # 로그인 없이 여는 건 이것들뿐 (정확한 경로만): 로그인·로그아웃, 바탕화면 앱 매니페스트·아이콘
        public = [LOGIN_PATH, LOGOUT_PATH, *webapp.public_paths()]
        web.guard_prefix("/dashboard", self.check_auth, public=public, on_denied=self._denied)
        for path, handler in (
            ("/dashboard", self._page),
            ("/dashboard/", self._page),
            ("/dashboard/refresh", self._refresh),
            ("/dashboard/input", self._input),
            ("/dashboard/data.json", self._data),
            (webapp.MANIFEST_PATH, self._manifest),
        ):
            web.route(path, handler)
        for name in webapp.STATIC_TYPES:
            web.route(webapp.STATIC_PREFIX + name, self._icon(name))
        web.route_post(LINK_PATH, self._link, max_body=LINK_BODY_MAX)
        web.route(LOGIN_PATH, self._login_page, with_headers=True)
        web.route_post(LOGIN_PATH, self._login, max_body=LOGIN_BODY_MAX, with_headers=True)
        web.route_post(LOGOUT_PATH, self._logout, max_body=LOGOUT_BODY_MAX, with_headers=True)
        return True

    # ------------------------------------------------------------ 로그인 (login.py) · 바탕화면 앱 (webapp.py)
    def _denied(self, method: str, path: str, headers: dict[str, str]) -> Any:
        """로그인 없이 브라우저로 연 화면 → 로그인 화면으로 (302). 기본 인증을 넣었는데 틀렸으면 그대로 401
        (브라우저가 다시 묻게), data.json·POST·그 밖의 경로도 그대로 401. 만료된 세션 쿠키는 지운다."""
        if method not in ("GET", "HEAD") or path not in PAGE_PATHS or headers.get("authorization"):
            return None
        extra = dict(SECURITY_HEADERS)
        if cookie_values(headers, SESSION_COOKIE):
            extra["Set-Cookie"] = clear_session_cookie()
        return 302, LOGIN_PATH, None, extra

    async def _manifest(self, query: dict[str, str]) -> Any:
        return webapp.manifest_response()

    @staticmethod
    def _icon(name: str) -> Any:
        async def handler(query: dict[str, str]) -> Any:
            return webapp.static_response(name)

        return handler

    def _login_form(self, headers: dict[str, str], status: int, notice: tuple[str, str] | None = None, *,
                    cross_site: bool = False, retry_after: int | None = None) -> Any:
        """로그인 화면 + CSRF 난수 쿠키 (받은 난수 쿠키가 있으면 그대로, 없으면 새로) — 폼에는 지금 시각으로 새 서명."""
        nonce, field = self.csrf.issue(cookie_values(headers, LOGIN_COOKIE))
        headers = {**SECURITY_HEADERS, "Set-Cookie": login_cookie(nonce)}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return status, login_page(title=self.cfg.title, field_token=field, notice=notice, cross_site=cross_site), \
            "text/html; charset=utf-8", headers

    @staticmethod
    def _locked_notice(wait: int) -> tuple[str, str]:
        return f"비밀번호가 여러 번 틀려 잠시 막아 두었어요 — {wait_text(wait)} 뒤에 다시 넣어 주세요.", "bad"

    async def _login_page(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        """GET /dashboard/login. 이미 로그인한 기기면 바로 대시보드로."""
        if self.sessions.any_valid(cookie_values(headers, SESSION_COOKIE)):
            return 303, "/dashboard", None, SECURITY_HEADERS
        notice = LOGIN_NOTICES.get(query.get("msg", ""))
        wait = self.limiter.blocked(client_key(headers))
        if wait is not None:
            notice = self._locked_notice(wait)
        cross = (headers.get("sec-fetch-site") or "").strip().lower() == "cross-site"
        return self._login_form(headers, 200, notice, cross_site=cross)

    async def _login(self, form: dict[str, str], headers: dict[str, str]) -> Any:
        """POST /dashboard/login: 1분 한도(429) → 틀린 시도 막힘(429) → CSRF(403) → 아이디·비밀번호(상수 시간 비교) →
        세션 쿠키와 함께 대시보드로 (303). 넣은 값·쿠키 값은 로그에 남기지 않는다."""
        client = client_key(headers)
        wait = self.login_rate.hit(client)
        if wait is not None:
            return self._login_form(headers, 429, (f"너무 자주 눌렀어요 — {wait_text(wait)} 뒤에 다시 눌러 주세요.", "bad"),
                                    retry_after=wait)
        wait = self.limiter.blocked(client)
        if wait is not None:
            return self._login_form(headers, 429, self._locked_notice(wait), retry_after=wait)
        if not self.csrf.check(cookie_values(headers, LOGIN_COOKIE), form.get("t")):
            return self._login_form(headers, 403, ("화면이 오래됐어요 — 아이디와 비밀번호를 다시 넣어 주세요.", "bad"))
        given_user = (form.get("user") or "").strip().encode("utf-8")
        given_password = (form.get("password") or "").encode("utf-8")
        if check_credentials(given_user, given_password, self._user, self._password):
            self.limiter.success(client)
            token = self.sessions.create()
            log.info("dashboard: 로그인 — 이 기기는 %d일 동안 로그인 유지 (값은 기록하지 않음)", SESSION_DAYS)
            return 303, "/dashboard", None, {**SECURITY_HEADERS, "Set-Cookie": session_cookie(token)}
        self._failed(client)
        wait = self.limiter.blocked(client)
        if wait is not None:
            return self._login_form(headers, 429, self._locked_notice(wait), retry_after=wait)
        return self._login_form(headers, 200, ("아이디나 비밀번호가 맞지 않아요.", "bad"))

    async def _logout(self, form: dict[str, str], headers: dict[str, str]) -> Any:
        """POST /dashboard/logout (폼 토큰 t): 이 요청의 세션 쿠키를 DB 에서 지우고 쿠키도 지운 뒤 로그인 화면으로."""
        if not self._token_ok(form.get("t")):
            return self._forbidden()
        for token in cookie_values(headers, SESSION_COOKIE):
            self.sessions.revoke(token)
        return 303, f"{LOGIN_PATH}?msg=out", None, {**SECURITY_HEADERS, "Set-Cookie": clear_session_cookie()}

    def _notice(self, query: dict[str, str]) -> tuple[tuple[str, str] | None, str | None]:
        """((문구, 종류), 안내를 넣을 카드 키 | None)."""
        code = query.get("msg", "")
        if code not in NOTICES:
            return None, None
        text, kind = NOTICES[code]
        q = query.get("q", "")
        text = text.format(minutes=self.cfg.refresh_min_minutes, days=self.cfg.manual_max_days_back, rate=LINK_RATE,
                           qid=f"#{int(q)} " if _QUEUE_ID_RE.match(q) else "")
        key = query.get("src", "")
        return (text, kind), (key if key in self.by_key else None)

    def _back(self, code: str, key: str | None = None, *, queue_id: int | None = None, status: int = 302) -> tuple[int, str]:
        q = f"&q={queue_id}" if queue_id is not None else ""
        if key and key in self.by_key:
            return status, f"/dashboard?msg={code}&src={key}{q}#card-{key}"
        return status, f"/dashboard?msg={code}{q}"

    async def _page(self, query: dict[str, str]) -> Any:
        notice, src = self._notice(query)
        return 200, self.page_html(notice, notice_src=src), "text/html; charset=utf-8", SECURITY_HEADERS

    async def _data(self, query: dict[str, str]) -> Any:
        return 200, self.json_text(), "application/json; charset=utf-8", SECURITY_HEADERS

    def _forbidden(self) -> Any:
        return 403, page("다시 열어 주세요", "페이지가 오래됐어요. <a href='/dashboard'>대시보드</a>를 새로 연 뒤 다시 눌러 주세요."), \
            "text/html; charset=utf-8", SECURITY_HEADERS

    async def _refresh(self, query: dict[str, str]) -> Any:
        if not self._token_ok(query.get("t")):
            return self._forbidden()
        key = query.get("src", "")
        code = await self.refresh(key)
        return self._back(code, key)

    async def _input(self, query: dict[str, str]) -> Any:
        if not self._token_ok(query.get("t")):
            return self._forbidden()
        key = query.get("src", "")
        return self._back(self.save_manual(key, query), key)

    async def _link(self, form: dict[str, str]) -> Any:
        """POST /dashboard/link (폼 본문만 — 주소의 쿼리는 안 씀). 1분 한도 → 폼 토큰(403) → 글 번호(숫자만, 아니면 400) →
        attach_owner_link → 결과 안내와 함께 대시보드로 돌아감 (303, 새로고침해도 다시 보내지 않게)."""
        if not self.link_rate_ok():
            return self._back("link_busy", self.links_key, status=303)
        if not self._token_ok(form.get("t")):
            return self._forbidden()
        raw = (form.get("q") or "").strip().removeprefix("#")
        if not _QUEUE_ID_RE.match(raw):
            return 400, page("잘못된 요청", "글 번호가 없어요. <a href='/dashboard'>대시보드</a>를 새로 열어 주세요."), \
                "text/html; charset=utf-8", SECURITY_HEADERS
        queue_id = int(raw)
        code = await self.attach_owner_link(queue_id, form.get("url") or "")
        return self._back(code, self.links_key, queue_id=queue_id, status=303)

    # ------------------------------------------------------------ 자기 점검
    def self_check(self) -> list[tuple[bool | None, str]]:
        """대시보드는 덤이라 문제가 있어도 ⚠️(None) — ❌ 는 '딜봇 멈춤' 카톡이 가므로 쓰지 않는다."""
        out: list[tuple[bool | None, str]] = [(None, p) for p in self.problems]
        if not self.cfg.enabled:
            return [*out, (None, "채널 대시보드: 꺼짐 (설정 dashboard.enabled)")]
        if not self.served:
            return [*out, (None, "채널 대시보드 페이지: 꺼짐 (DASHBOARD_USER·DASHBOARD_PASSWORD 미설정 — 숫자는 계속 모음)")]
        where = f"{self.public_base_url}/dashboard" if self.public_base_url else "/dashboard"
        if weak := password_problems(self._password):
            return [*out, (None, f"채널 대시보드 {where}: 비밀번호를 바꿔 주세요 ({'; '.join(weak)}) — "
                                 "bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD")]
        return [*out, (True, f"채널 대시보드 {where} (카드 {len(self.sources)}개, 비밀번호 보호)")]
