"""채널 대시보드 본체: 카드(소스) 목록, 정해진 시각에 읽기, '다시 읽기', 직접 입력, 웹 경로.

경로 (모두 기본 인증 — DASHBOARD_USER / DASHBOARD_PASSWORD 가 둘 다 있을 때만 열림, 아니면 404):
  /dashboard                    페이지
  /dashboard/refresh?src=키      그 카드를 지금 다시 읽고 돌아감 (카드마다 refresh_min_minutes 에 한 번)
  /dashboard/input?src=키&지표=값&date=YYYY-MM-DD   직접 입력 저장 후 돌아감 (숫자만, 날짜는 오늘~manual_max_days_back)
  /dashboard/data.json          같은 내용을 JSON 으로 (나중에 다른 곳에서 쓰려고)
refresh·input 은 페이지가 넣어 주는 t(폼 토큰)가 맞아야 한다 — 다른 사이트가 링크로 값을 바꾸지 못하게.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta
from typing import Any

from dealbot.config import DashboardConfig
from dealbot.dashboard import render
from dealbot.dashboard.auth import check_basic
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
from dealbot.utils.timeutil import from_iso, to_iso
from dealbot.web import WebServer, page

log = logging.getLogger(__name__)

KV_STATUS = "dash:status:{key}"  # 마지막 읽기 결과 {status, read_at, tried_at, note}
KV_SLOT = "dash:slot"  # 마지막으로 다 읽은 정해진 시각 ("YYYY-MM-DD HH:MM")
KV_LIVE_AT = "dash:live_at"  # 로컬 기록(Blog-Auto 파일·봇 DB)을 마지막으로 저장한 시각
KV_FORM_TOKEN = "dash:form_token"

_NUMBER_RE = re.compile(r"^(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)$")  # 1234 또는 1,234 (전각 숫자·소수·음수는 안 받음)
_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}

# 돌아간 뒤 페이지 위에 보일 안내 (주소의 msg=코드 → 고정 문구. 주소의 글자를 그대로 보여 주지 않음)
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
}


class Dashboard:
    def __init__(self, cfg: DashboardConfig, ctx: SourceContext, *, user: str | None = None, password: str | None = None,
                 public_base_url: str | None = None) -> None:
        self.cfg = cfg
        self.ctx = ctx
        self.db = ctx.db
        self._user = user
        self._password = password
        self.public_base_url = public_base_url
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
        self._locks: dict[str, asyncio.Lock] = {}
        self._token: str | None = None

    # ------------------------------------------------------------ 상태
    @property
    def enabled(self) -> bool:
        return self.cfg.enabled

    @property
    def served(self) -> bool:
        return self.cfg.enabled and bool(self._user and self._password)

    def check_auth(self, headers: dict[str, str]) -> bool:
        ok = check_basic(headers.get("authorization"), self._user, self._password)
        if not ok and headers.get("authorization"):
            log.info("dashboard: 인증 실패 (값은 기록하지 않음)")
        return ok

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
        today = self.ctx.today().isoformat()
        rows: list[tuple[str, str, str, float]] = []
        for day, values in res.days.items():
            if _DAY_RE.match(day):
                rows.extend((source.key, m, day, float(v)) for m, v in values.items())
        rows.extend((source.key, m, today, float(v)) for m, v in res.values.items())
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
    def summary(views: list[CardView]) -> list[tuple[str, str, str]]:
        """맨 위 요약 줄: (이름, 값, 작은 글)."""
        def total(name: str) -> tuple[float | None, int]:
            vals = [v.summary[name] for v in views if v.summary.get(name) is not None]
            return (sum(vals) if vals else None), len(vals)

        aud, n_aud = total("audience_delta")
        posts, _ = total("posts_today")
        visitors, n_vis = total("visitors_yesterday")
        revenue, _ = total("revenue_7d")
        counts = {k: sum(1 for v in views if v.status == k) for k in (MET, UNMET, UNKNOWN)}
        return [
            ("오늘 늘어난 팔로워", fmt_signed(aud) if aud is not None else "-",
             f"구독자·팔로워·이웃, 어제와 비교한 카드 {n_aud}개" if n_aud else "어제·오늘 값이 다 있으면 보여요"),
            ("오늘 올린 글", f"{fmt_num(posts)}개" if posts is not None else "-", "핫딜 채널 딜 + 블로그 발행"),
            ("어제 블로그 방문자", f"{fmt_num(visitors)}명" if visitors is not None else "-",
             f"직접 넣은 블로그 {n_vis}개" if n_vis else "블로그 카드에서 직접 넣어 주세요"),
            ("쿠팡 수익 · 최근 7일", f"{fmt_num(revenue)}원" if revenue is not None else "-",
             f"조건 충족 {counts[MET]} · 미충족 {counts[UNMET]} · 판단 불가 {counts[UNKNOWN]}"),
        ]

    @staticmethod
    def _updated_at(views: list[CardView]) -> datetime | None:
        """맨 위 '마지막 업데이트': 바깥에서 읽은 값(API·Blog-Auto 기록·직접 입력) 중 가장 최근 (봇 DB 카드는 늘 지금이라 뺌)."""
        times = [v.read_at for v in views if v.read_at is not None and v.kind != "internal"]
        return max(times) if times else None

    def page_html(self, notice: tuple[str, str] | None = None) -> str:
        views = self.views()
        times = ", ".join(self.cfg.collect_times) or "-"
        footer = (f"API 로 읽는 카드는 매일 {times} 에 자동으로 읽어요 ('다시 읽기'는 카드마다 {self.cfg.refresh_min_minutes}분에 한 번). "
                  "네이버 블로그 이웃 수·방문자와 오픈채팅 참여자는 카드에서 직접 넣어요.")
        return render.render_page(
            title=self.cfg.title, views=views, summary=self.summary(views), updated_at=self._updated_at(views),
            token=self.form_token, today=self.ctx.today(), tz=self.ctx.tz, spark_days=self.cfg.spark_days,
            notice=notice, footer=footer, min_day=self.ctx.today() - timedelta(days=self.cfg.manual_max_days_back),
        )

    def json_text(self) -> str:
        views = self.views()
        return render.render_json(title=self.cfg.title, views=views, generated_at=self.ctx.now(), tz=self.ctx.tz,
                                  summary=self.summary(views))

    # ------------------------------------------------------------ 웹
    def register(self, web: WebServer) -> bool:
        """아이디·비밀번호가 둘 다 있을 때만 경로를 연다 (없으면 404 그대로)."""
        if not self.served:
            return False
        for path, handler in (
            ("/dashboard", self._page),
            ("/dashboard/", self._page),
            ("/dashboard/refresh", self._refresh),
            ("/dashboard/input", self._input),
            ("/dashboard/data.json", self._data),
        ):
            web.route(path, handler, auth=self.check_auth)
        return True

    def _notice(self, query: dict[str, str]) -> tuple[str, str] | None:
        code = query.get("msg", "")
        if code not in NOTICES:
            return None
        text, kind = NOTICES[code]
        text = text.format(minutes=self.cfg.refresh_min_minutes, days=self.cfg.manual_max_days_back)
        source = self.by_key.get(query.get("src", ""))
        return (f"{source.title}: {text}" if source else text), kind

    def _back(self, code: str, key: str | None = None) -> tuple[int, str]:
        if key and key in self.by_key:
            return 302, f"/dashboard?msg={code}&src={key}#card-{key}"
        return 302, f"/dashboard?msg={code}"

    async def _page(self, query: dict[str, str]) -> Any:
        return 200, self.page_html(self._notice(query)), "text/html; charset=utf-8", SECURITY_HEADERS

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

    # ------------------------------------------------------------ 자기 점검
    def self_check(self) -> list[tuple[bool | None, str]]:
        out: list[tuple[bool | None, str]] = [(False, p) for p in self.problems]
        if not self.cfg.enabled:
            return [*out, (None, "채널 대시보드: 꺼짐 (설정 dashboard.enabled)")]
        if not self.served:
            return [*out, (None, "채널 대시보드 페이지: 꺼짐 (DASHBOARD_USER·DASHBOARD_PASSWORD 미설정 — 숫자는 계속 모음)")]
        where = f"{self.public_base_url}/dashboard" if self.public_base_url else "/dashboard"
        return [*out, (True, f"채널 대시보드 {where} (카드 {len(self.sources)}개, 비밀번호 보호)")]
