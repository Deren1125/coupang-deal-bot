"""쿠팡 파트너스 실적: 클릭·주문·수익 (공식 파트너스 리포트 API, 기존 HMAC 서명).

호출은 하루 몇 번만: 한 번 읽을 때 리포트 3개(클릭·주문·수익) = 3회, min_interval_hours(기본 8) 에 한 번,
하루 max_calls_per_day(기본 6) 를 넘지 않는다.
시간당 예산(ApiBudget)의 일반 몫만 쓰므로 발행용 딥링크 예약 몫은 그대로 남는다. 남은 몫이 모자라면 이번엔 건너뛴다.
리포트 API 를 못 쓰면(4xx 등) '판단 불가' 로 보이고 다음 시각에 다시 본다.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from dealbot.coupang.client import CoupangApiError, CoupangRateLimited
from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    ERROR,
    OK,
    PARTIAL,
    SKIPPED,
    UNAVAILABLE,
    CardView,
    FetchResult,
    GoalRow,
    Stat,
    fmt_num,
    safe_error,
)
from dealbot.dashboard.sources.base import CardData, Source, register

REPORTS = ("clicks", "orders", "commission")
CLICK_FIELDS = ("click", "clicks", "clickCount")
ORDER_FIELDS = ("orderCount", "orders", "order")
COMMISSION_FIELDS = ("commission", "commissionAmount", "revenue")
KV_CALLS = "dash:coupang_calls:{key}:{day}"


def _number(row: dict[str, Any], names: tuple[str, ...]) -> float | None:
    for n in names:
        v = row.get(n)
        if isinstance(v, bool) or v is None:
            continue
        try:
            return float(str(v).replace(",", ""))
        except ValueError:
            continue
    return None


def _row_day(row: dict[str, Any], fallback: date) -> str:
    raw = str(row.get("date") or row.get("day") or row.get("orderDate") or "")
    digits = re.sub(r"\D", "", raw)[:8]
    if len(digits) == 8:
        try:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:])).isoformat()
        except ValueError:
            pass
    return fallback.isoformat()


def parse_report(name: str, rows: list[dict[str, Any]], fallback: date) -> tuple[dict[str, float], dict[str, float]]:
    """리포트 행 → (날짜별 값, 날짜별 수익 — 주문 리포트에 수익이 같이 올 때)."""
    out: dict[str, float] = {}
    commission: dict[str, float] = {}
    orders_seen: dict[str, set[str]] = {}
    for row in rows:
        day = _row_day(row, fallback)
        if name == "clicks":
            out[day] = out.get(day, 0.0) + (_number(row, CLICK_FIELDS) or 0.0)
        elif name == "commission":
            out[day] = out.get(day, 0.0) + (_number(row, COMMISSION_FIELDS) or 0.0)
        else:  # orders: 건수 칸이 있으면 그 값, 없으면 주문번호 수
            n = _number(row, ORDER_FIELDS)
            if n is not None:
                out[day] = out.get(day, 0.0) + n
            else:
                oid = str(row.get("orderId") or row.get("orderNo") or id(row))
                orders_seen.setdefault(day, set()).add(oid)
            c = _number(row, COMMISSION_FIELDS)
            if c is not None:
                commission[day] = commission.get(day, 0.0) + c
    for day, ids in orders_seen.items():
        out[day] = out.get(day, 0.0) + len(ids)
    return out, commission


@register
class CoupangPartnersSource(Source):
    type = "coupang_partners"
    kind = "api"
    default_title = "쿠팡 파트너스"
    icon = icons.COUPANG
    program = "쿠팡 파트너스 수익"
    default_goals = {"commission_month": 10000}

    async def fetch(self) -> FetchResult:
        client = self.ctx.coupang
        if client is None:
            return FetchResult(UNAVAILABLE, note="쿠팡 API 키가 없어 실적을 못 읽음")
        today = self.ctx.today()
        cap = int(self.options.get("max_calls_per_day", 6))
        kv_key = KV_CALLS.format(key=self.key, day=today.isoformat())
        used = int(self.ctx.db.kv_get(kv_key) or 0)
        if used + len(REPORTS) > cap:
            return FetchResult(SKIPPED, note=f"오늘 실적 조회 한도({cap}회)를 다 써서 내일 다시 읽어요")
        # 정해진 시각(하루 4번)마다 부르지 않고 min_interval_hours 에 한 번 (기본 8시간 → 09시·18시쯤). 조용히 지난 값 유지
        last = self.ctx.db.metric_last_read(self.key)
        gap = float(self.options.get("min_interval_hours", 8))
        if last is not None and self.ctx.now() - last < timedelta(hours=gap):
            return FetchResult(SKIPPED)
        budget = self.ctx.budget
        left = budget.remaining("report") if budget is not None else None
        if left is not None and left < len(REPORTS):
            return FetchResult(SKIPPED, note="쿠팡 API 시간당 한도를 딜 발행에 남겨 두려고 이번엔 건너뜀 (다음 시각에 다시)")
        # 이번 달 1일(최근 7일보다 앞이면)부터 오늘까지, 최대 30일
        start = max(today - timedelta(days=29), min(today.replace(day=1), today - timedelta(days=6)))
        span = [(start + timedelta(days=i)).isoformat() for i in range((today - start).days + 1)]
        days: dict[str, dict[str, float]] = {d: {} for d in span}
        got: list[str] = []
        problems: list[str] = []
        order_commission: dict[str, float] = {}
        for name in REPORTS:
            used += 1
            self.ctx.db.kv_set(kv_key, str(used))
            try:
                rows = await client.report(name, start.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
            except CoupangRateLimited:
                problems.append("시간당 한도")
                break
            except CoupangApiError as e:
                if not got and name == REPORTS[0]:  # 리포트 API 자체를 못 씀 → 나머지는 부르지 않음
                    return FetchResult(UNAVAILABLE, note=f"쿠팡 실적 API 를 못 읽음 ({safe_error(e)})")
                problems.append(safe_error(e))
                continue
            except Exception as e:  # noqa: BLE001
                problems.append(safe_error(e))
                continue
            per_day, comm = parse_report(name, rows, today)
            metric = "commission" if name == "commission" else name
            for d in span:
                days[d][metric] = per_day.get(d, 0.0)
            if name == "orders":
                order_commission = comm
            got.append(name)
        if "commission" not in got and order_commission:
            for d in span:
                days[d]["commission"] = order_commission.get(d, 0.0)
        if not got:
            return FetchResult(ERROR, note=f"쿠팡 실적을 못 읽음 ({'; '.join(problems)[:120]})")
        days = {d: v for d, v in days.items() if v}
        if len(got) < len(REPORTS):
            return FetchResult(PARTIAL, days=days, note=f"일부 리포트를 못 읽음 ({'; '.join(problems)[:120]})")
        return FetchResult(OK, days=days)

    def card(self, data: CardData) -> CardView:
        clicks7 = data.window_sum("clicks", 7)
        orders7 = data.window_sum("orders", 7)
        revenue7 = data.window_sum("commission", 7)
        stats = [
            Stat("클릭 · 최근 7일", clicks7, "회"),
            Stat("수익 · 최근 7일", revenue7, "원", money=True, sub=f"주문 {fmt_num(orders7)}건" if orders7 is not None else ""),
        ]
        rows = []
        if (g := self.goal("commission_month")) is not None:
            rows.append(GoalRow("이번 달 수익", data.month_sum("commission"), g, "원"))
        if (g := self.goal("orders_month")) is not None:
            rows.append(GoalRow("이번 달 주문", data.month_sum("orders"), g, "건"))
        y = data.today - timedelta(days=1)
        activity = []
        if any(data.value(m, y) is not None for m in ("clicks", "orders", "commission")):
            activity.append(f"어제 클릭 {fmt_num(data.value('clicks', y))}회 · 주문 {fmt_num(data.value('orders', y))}건 · "
                            f"수익 {fmt_num(data.value('commission', y))}원")
        spark_metric = "commission" if data.series("commission") else "clicks"
        return self.view(
            data, stats=stats, spark=data.series(spark_metric), spark_label="하루 수익" if spark_metric == "commission" else "하루 클릭",
            rows=rows, activity=activity, summary={"revenue_7d": revenue7} if revenue7 is not None else {},
        )
