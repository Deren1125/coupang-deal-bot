"""제휴 수익 (주인 10/23 — "토스 쇼핑도 … 쿠팡 파트너스든 네이버쇼핑 커넥트든, 에이블리, 무신사… 내 링크 타고 들어와서 구매한 내역도 다 통계 탭에서").

세 곳에서 모아 통계 탭 '제휴 수익' 한 칸에 보인다 (state['earnings'] — 글은 여기서 다 만들고 화면 earn.js 는 그리기만):
1. 쿠팡 파트너스 (자동, 공식 리포트 API): 3시간에 한 번 최근 30일 clicks·orders·cancels·commission 을 읽어 날짜×subId 로 DB 에 둔다.
   기존 호출 예산(ApiBudget 'report' 몫)·하루 호출 수 한도를 따르고, 실패하면 1시간 뒤 다시. 주문 리포트에 상품 이름이 있으면 최근 구매 목록에 쓴다.
2. 네이버 쇼핑커넥트: 공개 API 가 없다. Blog-Auto 가 주인 계정의 성과 화면을 하루 한 번 읽어 기록 파일 earnings.shopconnect 로 준다.
   Blog-Auto 가 아직 확인(report_confirmed) 전이면 '확인 준비 중' 한 줄만.
3. 에이블리·지그재그·토스쇼핑 (자동 — 주인 10/23 '쇼핑몰 크리에이터를 제휴 수익으로 옮기고 양식도 똑같이'): Blog-Auto 기록 파일
   creator_mall (주인 크리에이터 화면을 하루 한 번 읽음 · 4시간마다 로그인 확인)의 쇼핑몰별 windows(오늘·7일·30일 방문·주문·금액·수익)를
   쿠팡처럼 한 줄씩 · 방문 수와 연결 상태는 설명 줄 · 토스가 알려 준 산 상품은 최근 구매에.
4. 무신사·29CM·기타: 주인이 직접 넣는다 (earn_manual 표).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.coupang.client import CoupangApiError, CoupangRateLimited
from dealbot.dashboard.model import safe_error, safe_text
from dealbot.dashboard.sources.coupang import _number, _row_day
from dealbot.storage.db import Database

log = logging.getLogger(__name__)

SHOPS = ("무신사", "29CM", "기타")  # 새로 넣을 때 고르는 곳 — 에이블리·지그재그·토스쇼핑은 자동 (creator_mall) · 직접 넣으면 두 번 센다
OLD_SHOPS = ("토스쇼핑", "에이블리", "지그재그")  # 10/23 전에 직접 넣은 줄을 고칠 때만 받는다
CM_CONN_TEXT = {"ok": "연결됨", "expired": "로그인 필요", "failed": "연결 확인 실패", "unknown": "연결 확인 전"}
WINDOW_DAYS = 30
INTERVAL_SEC = 3 * 3600  # 쿠팡 실적 읽는 간격
RETRY_SEC = 3600  # 실패하면 1시간 뒤
CALLS_PER_RUN = 4  # clicks · orders · cancels · commission
CALLS_PER_DAY = 40
RECENT_MAX = 15
MANUAL_MAX = 60
MONEY_MAX = 100_000_000
K_LAST_OK, K_NEXT, K_ERR, K_CALLS = "earn:coupang:last_ok", "earn:coupang:next", "earn:coupang:err", "earn:coupang:calls:{day}"
LABEL_COUPANG, LABEL_SC, LABEL_MANUAL = "쿠팡 (자동)", "쇼핑커넥트", "직접 넣은 것"
SC_WAIT = "쇼핑커넥트 실적: 확인 준비 중 (서버 점검 후 켜짐)"
SC_NOFIELD = "쇼핑커넥트 실적: 블로그 프로그램에서 아직 못 받았어요"

SUB_FIELDS = ("subId", "sub_id", "subid")
NAME_FIELDS = ("productName", "itemName", "name", "title")
ORDER_ID_FIELDS = ("orderId", "order_id", "orderNo")
PRODUCT_ID_FIELDS = ("productId", "product_id")
QTY_FIELDS = ("quantity", "qty")
AMOUNT_FIELDS = ("gmv", "orderPrice", "salesPrice", "amount", "price")
CANCEL_FIELDS = ("cancelGmv", "cancelAmount", "gmv", "amount", "price")
COMMISSION_FIELDS = ("commission", "commissionAmount", "revenue")
CLICK_FIELDS = ("click", "clicks", "clickCount")


class EarnError(ValueError):
    """주인이 넣은 값이 맞지 않음 — 글은 화면에 그대로 보여 줄 한국어."""


def money(n: int | float) -> str:
    return f"{int(round(n)):,}원"


def _text(row: dict[str, Any], names: tuple[str, ...]) -> str:
    for n in names:
        v = row.get(n)
        if v not in (None, "") and not isinstance(v, bool | dict | list):
            return str(v).strip()
    return ""


def _int(row: dict[str, Any], names: tuple[str, ...]) -> int:
    v = _number(row, names)
    return int(v) if v is not None else 0


# ---------------------------------------------------------------- 쿠팡 리포트 → 행
def parse_coupang(reports: dict[str, list[dict[str, Any]]], fallback: date) -> dict[str, Any]:
    """리포트 이름 → 행 목록 → {days: {(날짜, subId): {...}}, orders: [주문 한 줄씩]}.
    · 하루 수익은 commission 리포트가 있으면 그 값, 없으면 주문 줄의 합 · 주문 수는 주문번호 가짓수 (번호 없으면 줄 수)
    · cancels 의 주문번호+상품이 주문에 있으면 그 줄을 취소로 표시, 취소 금액은 날짜×subId 로 따로 더함."""
    days: dict[tuple[str, str], dict[str, int]] = {}

    def slot(day: str, sub: str) -> dict[str, int]:
        return days.setdefault((day, sub), {"clicks": 0, "orders": 0, "amount": 0, "cancel_amount": 0, "commission": 0, "order_commission": 0})

    for row in reports.get("clicks") or []:
        slot(_row_day(row, fallback), _text(row, SUB_FIELDS))["clicks"] += _int(row, CLICK_FIELDS)
    orders: list[dict[str, Any]] = []
    ids: dict[tuple[str, str], set[str]] = {}
    seen: dict[str, int] = {}
    for row in reports.get("orders") or []:
        day, sub = _row_day(row, fallback), _text(row, SUB_FIELDS)
        oid, pid = _text(row, ORDER_ID_FIELDS), _text(row, PRODUCT_ID_FIELDS)
        amount, comm = _int(row, AMOUNT_FIELDS), _int(row, COMMISSION_FIELDS)
        s = slot(day, sub)
        s["amount"] += amount
        s["order_commission"] += comm
        ids.setdefault((day, sub), set()).add(oid or f"#{len(orders)}")
        base = hashlib.sha1(f"{day}|{oid}|{pid}|{_text(row, NAME_FIELDS)}|{amount}|{comm}".encode()).hexdigest()[:16]
        seen[base] = seen.get(base, 0) + 1
        orders.append({"okey": f"{base}-{seen[base]}", "day": day, "order_id": oid, "product_id": pid, "sub_id": sub,
                       "product": safe_text(_text(row, NAME_FIELDS), 80) or "", "qty": _int(row, QTY_FIELDS) or 1,
                       "amount": amount, "commission": comm, "cancelled": 0})
    for key, got in ids.items():
        days[key]["orders"] = len(got)
    cancelled = {(o["order_id"], o["product_id"]) for o in orders if o["order_id"]}
    hit: set[tuple[str, str]] = set()
    for row in reports.get("cancels") or []:
        slot(_row_day(row, fallback), _text(row, SUB_FIELDS))["cancel_amount"] += _int(row, CANCEL_FIELDS)
        k = (_text(row, ORDER_ID_FIELDS), _text(row, PRODUCT_ID_FIELDS))
        if k[0] and k in cancelled:
            hit.add(k)
    for o in orders:
        if (o["order_id"], o["product_id"]) in hit:
            o["cancelled"] = 1
    if reports.get("commission") is not None:
        for row in reports["commission"]:
            slot(_row_day(row, fallback), _text(row, SUB_FIELDS))["commission"] += _int(row, COMMISSION_FIELDS)
    else:
        for s in days.values():
            s["commission"] = s["order_commission"]
    return {"days": days, "orders": orders}


# ---------------------------------------------------------------- 저장
class EarnStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # 쿠팡
    def replace_coupang(self, parsed: dict[str, Any], start: str, end: str, read_at: str) -> None:
        with self.db._tx() as c:
            c.execute("DELETE FROM earn_coupang WHERE day >= ? AND day <= ?", (start, end))
            c.execute("DELETE FROM earn_coupang_orders WHERE day >= ? AND day <= ?", (start, end))
            for (day, sub), v in parsed["days"].items():
                if start <= day <= end:
                    c.execute("INSERT OR REPLACE INTO earn_coupang (day, sub_id, clicks, orders, amount, cancel_amount, commission, read_at) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                              (day, sub, v["clicks"], v["orders"], v["amount"], v["cancel_amount"], v["commission"], read_at))
            for o in parsed["orders"]:
                if start <= o["day"] <= end:
                    c.execute("INSERT OR REPLACE INTO earn_coupang_orders (okey, day, order_id, product, qty, amount, commission, sub_id, cancelled, read_at) "
                              "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                              (o["okey"], o["day"], o["order_id"], o["product"], o["qty"], o["amount"], o["commission"], o["sub_id"],
                               o["cancelled"], read_at))

    def coupang_days(self, start: str, end: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db._q("SELECT * FROM earn_coupang WHERE day >= ? AND day <= ? ORDER BY day", (start, end))]

    def coupang_orders(self, start: str, limit: int) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db._q("SELECT * FROM earn_coupang_orders WHERE day >= ? ORDER BY day DESC, okey LIMIT ?", (start, limit))]

    # 직접 넣은 것
    @staticmethod
    def clean_manual(args: dict[str, Any], today: date) -> dict[str, Any]:
        """주인이 넣은 값 → 저장할 행 (맞지 않으면 EarnError — 한국어 한 줄)."""
        shop = str(args.get("shop") or "").strip()
        if shop not in SHOPS + OLD_SHOPS:
            raise EarnError("어디서 생긴 수익인지 골라 주세요")
        try:
            day = date.fromisoformat(str(args.get("day") or ""))
        except ValueError:
            raise EarnError("날짜를 넣어 주세요") from None
        if day > today or day < today - timedelta(days=800):
            raise EarnError("날짜가 오늘보다 뒤이거나 너무 오래됐어요")

        def amount(key: str, label: str) -> int:
            raw = args.get(key)
            if raw in (None, ""):
                return 0
            if isinstance(raw, bool) or not re.fullmatch(r"\d{1,3}(,?\d{3})*|\d+", str(raw).strip().replace(" ", "")):
                raise EarnError(f"{label}은 숫자로 넣어 주세요")
            n = int(str(raw).replace(",", "").replace(" ", ""))
            if n > MONEY_MAX:
                raise EarnError(f"{label}이 너무 커요")
            return n

        amt, com = amount("amount", "금액"), amount("commission", "수익")
        if amt <= 0 and com <= 0:
            raise EarnError("금액이나 수익을 넣어 주세요")
        product, memo = str(args.get("product") or "").strip(), str(args.get("memo") or "").strip()
        if len(product) > 80 or len(memo) > 200 or "\x00" in product + memo:
            raise EarnError("상품 이름은 80자, 메모는 200자까지예요")
        return {"shop": shop, "day": day.isoformat(), "product": product, "amount": amt, "commission": com, "memo": memo}

    def add_manual(self, row: dict[str, Any], now: str) -> int:
        with self.db._tx() as c:
            if c.execute("SELECT COUNT(*) FROM earn_manual").fetchone()[0] >= 5000:
                raise EarnError("직접 넣은 기록이 너무 많아요")
            cur = c.execute("INSERT INTO earn_manual (shop, day, product, amount, commission, memo, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (row["shop"], row["day"], row["product"], row["amount"], row["commission"], row["memo"], now, now))
            return int(cur.lastrowid or 0)

    def update_manual(self, mid: int, row: dict[str, Any], now: str) -> bool:
        with self.db._tx() as c:
            return bool(c.execute("UPDATE earn_manual SET shop=?, day=?, product=?, amount=?, commission=?, memo=?, updated_at=? WHERE id=?",
                                  (row["shop"], row["day"], row["product"], row["amount"], row["commission"], row["memo"], now, mid)).rowcount)

    def delete_manual(self, mid: int) -> bool:
        with self.db._tx() as c:
            return bool(c.execute("DELETE FROM earn_manual WHERE id = ?", (mid,)).rowcount)

    def manual(self, start: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
        sql, p = "SELECT * FROM earn_manual", ()
        if start:
            sql, p = sql + " WHERE day >= ?", (start,)
        return [dict(r) for r in self.db._q(sql + " ORDER BY day DESC, id DESC LIMIT ?", (*p, limit))]


# ---------------------------------------------------------------- 쿠팡 읽기 (3시간마다)
class EarnCoupang:
    def __init__(self, store: EarnStore, client_of: Callable[[], Any], budget_of: Callable[[], Any], tz: str,
                 clock: Callable[[], float] = time.time) -> None:
        self.store, self.client_of, self.budget_of, self.tz, self.clock = store, client_of, budget_of, tz, clock

    def today(self) -> date:
        return datetime.fromtimestamp(self.clock(), ZoneInfo(self.tz)).date()

    def due(self) -> bool:
        kv = self.store.db.kv_get
        return self.clock() >= float(kv(K_NEXT) or 0)

    async def tick(self) -> str | None:
        """읽을 때가 됐으면 읽는다. 읽었으면 'ok', 건너뛰었으면 이유 한 낱말 (due 가 아니면 None)."""
        db, now = self.store.db, self.clock()
        client = self.client_of()
        if client is None or not self.due():
            return None
        today = self.today()
        kcalls = K_CALLS.format(day=today.isoformat())
        used = int(db.kv_get(kcalls) or 0)
        if used + CALLS_PER_RUN > CALLS_PER_DAY:
            db.kv_set(K_NEXT, str(now + RETRY_SEC))
            return "daily_cap"
        budget = self.budget_of()
        if budget is not None and budget.remaining("report") < CALLS_PER_RUN:  # 딜 발행 몫을 남겨 둠
            db.kv_set(K_NEXT, str(now + 600))
            return "budget"
        start, end = today - timedelta(days=WINDOW_DAYS - 1), today
        reports: dict[str, list[dict[str, Any]]] = {}
        problem = ""
        for name in ("clicks", "orders", "cancels", "commission"):
            used += 1
            db.kv_set(kcalls, str(used))
            try:
                reports[name] = await client.report(name, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            except CoupangRateLimited:
                problem = "시간당 한도"
                break
            except CoupangApiError as e:
                if name in ("cancels", "commission"):  # 이 리포트만 못 쓰는 계정: 없는 것으로 (주문 줄에서 대신 셈)
                    log.warning("coupang %s report unavailable: %s", name, safe_error(e))
                    continue
                problem = safe_error(e)
                break
            except Exception as e:  # noqa: BLE001 — 5xx·네트워크: 이번엔 그만 (이미 읽은 날을 반쯤 덮지 않게 저장 안 함)
                problem = safe_error(e)
                break
        if problem:
            db.kv_set(K_ERR, problem[:160])
            db.kv_set(K_NEXT, str(now + RETRY_SEC))
            return "error"
        parsed = parse_coupang(reports, end)
        self.store.replace_coupang(parsed, start.isoformat(), end.isoformat(), datetime.fromtimestamp(now, ZoneInfo(self.tz)).isoformat(timespec="seconds"))
        db.kv_set(K_LAST_OK, str(now))
        db.kv_set(K_ERR, "")
        db.kv_set(K_NEXT, str(now + INTERVAL_SEC))
        return "ok"


# ---------------------------------------------------------------- 화면 값
def _sum(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(r.get(key) or 0) for r in rows)


def _md(day: str) -> str:
    return f"{int(day[5:7])}/{int(day[8:10])}"


def _window(today: date) -> list[tuple[str, str, str]]:
    return [("오늘", today.isoformat(), today.isoformat()), ("7일", (today - timedelta(days=6)).isoformat(), today.isoformat()),
            ("30일", (today - timedelta(days=29)).isoformat(), today.isoformat())]


def _in(rows: list[dict[str, Any]], lo: str, hi: str) -> list[dict[str, Any]]:
    return [r for r in rows if lo <= str(r.get("day")) <= hi]


def _tot(orders: int, amount: int, commission: int) -> dict[str, str]:
    return {"orders": f"{orders:,}건", "amount": money(amount), "commission": money(commission)}


def _cm_cells(m: dict[str, Any]) -> list[dict[str, str]]:
    ws = {w["label"]: w for w in m.get("windows") or []}
    out = []
    for label in ("오늘", "7일", "30일"):
        w = ws.get(label) or {}
        out.append({"label": label, "orders": f"{w['orders']:,}건" if w.get("orders") is not None else "-",
                    "amount": money(w["amount"]) if w.get("amount") is not None else "-",
                    "commission": money(w["commission"]) if w.get("commission") is not None else "-"})
    return out


def _cm_source(m: dict[str, Any], read_at: str | None) -> dict[str, Any]:
    """쇼핑몰 한 줄 (모듈 설명 3)."""
    ws = {w["label"]: w for w in m.get("windows") or []}
    visits = " · ".join(f"{k} {ws[k]['visits']:,}" for k in ("오늘", "7일", "30일") if ws.get(k) and ws[k].get("visits") is not None)
    conn = CM_CONN_TEXT.get(m.get("conn") or "unknown", "연결 확인 전") + (f" ({m['checked_at'][5:].replace('-', '/')})" if m.get("checked_at") else "")
    bits = [f"방문 {visits}" if visits else "", conn, f"{read_at[5:].replace('-', '/')} 에 읽음" if read_at else "아직 못 읽었어요"]
    if m["state"] == "ok" and m.get("windows"):
        state = "ok"
    else:
        state = "wait"
        bits.append(m.get("text") or "")
    return {"key": f"cm_{m['key']}", "label": f"{m['name']} (자동)", "state": state, "note": " · ".join(b for b in bits if b),
            "cells": _cm_cells(m)}


def _cm_nums(m: dict[str, Any], label: str) -> tuple[int, int, int]:
    w = next((x for x in m.get("windows") or [] if x["label"] == label), {})
    return int(w.get("orders") or 0), int(w.get("amount") or 0), int(w.get("commission") or 0)


def build(store: EarnStore, tz: str, now: float, sc_field: dict[str, Any] | None, *, coupang_on: bool, sub_id_set: bool,
          cm_field: dict[str, Any] | None = None) -> dict[str, Any]:
    """통계 탭 '제휴 수익' 칸 값. sc_field = Blog-Auto 기록 파일의 earnings.shopconnect (걸러 둔 것, 없으면 None).
    cm_field = 기록 파일의 creator_mall (에이블리·지그재그·토스쇼핑 자동 — 없으면 그 줄들 없이)."""
    cm_malls = [m for m in ((cm_field or {}).get("malls") or []) if m.get("state") != "pending"]
    today = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
    wins = _window(today)
    first = wins[-1][1]
    cdays = store.coupang_days(first, today.isoformat())
    manual = store.manual(first)
    sc = sc_field if isinstance(sc_field, dict) and sc_field.get("confirmed") is True else None
    sdays = list(sc["days"]) if sc else []

    # ---- 합계·원별 (오늘 · 7일 · 30일)
    def csum(lo: str, hi: str) -> tuple[int, int, int]:
        r = _in(cdays, lo, hi)
        return _sum(r, "orders"), max(0, _sum(r, "amount") - _sum(r, "cancel_amount")), _sum(r, "commission")

    def ssum(lo: str, hi: str) -> tuple[int, int, int]:
        r = _in(sdays, lo, hi)
        return _sum(r, "orders"), _sum(r, "amount"), _sum(r, "commission")

    def msum(lo: str, hi: str) -> tuple[int, int, int]:
        r = _in(manual, lo, hi)
        return len(r), _sum(r, "amount"), _sum(r, "commission")

    totals, per = [], {"coupang": [], "shopconnect": [], "manual": []}
    for label, lo, hi in wins:
        parts = {"coupang": csum(lo, hi), "shopconnect": ssum(lo, hi), "manual": msum(lo, hi),
                 **{f"cm_{m['key']}": _cm_nums(m, label) for m in cm_malls if m["state"] == "ok"}}
        for k, v in parts.items():
            if k in per:
                per[k].append({"label": label, **_tot(*v)})
        totals.append({"label": label, **_tot(*(sum(p[i] for p in parts.values()) for i in range(3)))})

    # ---- 쿠팡
    last_ok = float(store.db.kv_get(K_LAST_OK) or 0)
    err = store.db.kv_get(K_ERR) or ""
    crow: list[dict[str, str]] = []
    for label, lo, hi in wins:
        r = _in(cdays, lo, hi)
        crow.append({"label": label, "clicks": f"{_sum(r, 'clicks'):,}회", **_tot(*csum(lo, hi))})
    subs: dict[str, dict[str, int]] = {}
    for r in cdays:
        s = subs.setdefault(r["sub_id"] or "", {"clicks": 0, "orders": 0, "amount": 0, "commission": 0})
        for k in s:
            s[k] += int(r[k] or 0)
    sub_rows = [{"sub": k or "(서브아이디 없음)", "clicks": f"{v['clicks']:,}회", "orders": f"{v['orders']:,}건", "commission": money(v["commission"])}
                for k, v in sorted(subs.items(), key=lambda kv: -kv[1]["commission"])]
    named = [k for k in subs if k]
    if len(named) > 1:
        sub_note = ""
    elif sub_id_set or named:
        sub_note = "모든 채널이 같은 서브아이디를 써서 어느 플랫폼(텔레그램·인스타·스레드 등)에서 팔렸는지는 나눌 수 없어요"
    else:
        sub_note = "서브아이디를 쓰지 않아서 어느 플랫폼에서 팔렸는지는 나눌 수 없어요"
    if not coupang_on:
        cstate, cnote = "off", "쿠팡 파트너스 키가 없어요"
    elif not last_ok:
        cstate, cnote = "wait", f"아직 못 읽었어요{' — ' + err if err else ' (3시간마다 읽어요)'}"
    else:
        at = datetime.fromtimestamp(last_ok, ZoneInfo(tz))
        cstate = "ok"
        cnote = f"{at.month}/{at.day} {at:%H:%M}에 읽음 · 쿠팡은 하루 늦게 모여요" + (f" · 마지막 읽기 실패: {err}" if err else "")

    # ---- 쇼핑커넥트
    if sc_field is None:
        sstate, snote = "wait", SC_NOFIELD
    elif sc is None:
        sstate, snote = "wait", SC_WAIT
    elif not sc.get("read_at"):
        sstate, snote = "wait", "쇼핑커넥트 실적: 아직 읽지 못했어요 (하루 한 번 읽어요)"
    else:
        sstate, snote = "ok", f"{str(sc['read_at'])[5:10].replace('-', '/').lstrip('0')} 에 읽음 · 읽기만 해요"

    # ---- 최근 구매
    recent: list[dict[str, Any]] = []
    for o in store.coupang_orders(first, RECENT_MAX):
        recent.append({"src": "쿠팡", "day": o["day"], "name": o["product"] or "(상품 이름 없음)", "amount": money(o["amount"]),
                       "commission": money(o["commission"]), "cancelled": bool(o["cancelled"]), "id": None})
    for o in (sc["orders"] if sc else [])[:RECENT_MAX]:
        recent.append({"src": LABEL_SC, "day": o["day"], "name": o["name"], "amount": money(o["amount"]),
                       "commission": money(o["commission"]), "cancelled": False, "id": None})
    read_day = str((cm_field or {}).get("read_at") or "")[:10]
    for m in cm_malls:  # 토스가 알려 준 산 상품 (상품명·금액·수수료 — 구매자 정보 없음 · 날짜는 읽은 날)
        for it in (m.get("items") or [])[:RECENT_MAX] if read_day else []:
            recent.append({"src": m["name"], "day": read_day, "name": it.get("name") or "(상품 이름 없음)", "amount": money(it.get("amount") or 0),
                           "commission": money(it.get("reward") or 0), "cancelled": bool(it.get("refunded")), "id": None})
    for m in manual[:RECENT_MAX]:
        recent.append({"src": m["shop"], "day": m["day"], "name": m["product"] or "(상품 이름 없음)", "amount": money(m["amount"]),
                       "commission": money(m["commission"]), "cancelled": False, "id": m["id"]})
    recent.sort(key=lambda r: r["day"], reverse=True)
    recent = [{**r, "day": _md(r["day"])} for r in recent[:RECENT_MAX]]

    rows = store.manual(limit=MANUAL_MAX)
    return {
        "title": "제휴 수익", "today": today.isoformat(), "totals": totals,
        "sources": [{"key": "coupang", "label": LABEL_COUPANG, "state": cstate, "note": cnote, "cells": per["coupang"]},
                    {"key": "shopconnect", "label": LABEL_SC, "state": sstate, "note": snote, "cells": per["shopconnect"]},
                    *[_cm_source(m, (cm_field or {}).get("read_at")) for m in cm_malls],
                    {"key": "manual", "label": LABEL_MANUAL, "state": "ok", "note": "주인이 직접 넣은 것만 더해요 (무신사·29CM·기타)",
                     "cells": per["manual"]}],
        "coupang": {"rows": crow, "subs": sub_rows, "sub_note": sub_note},
        "recent": recent,
        "manual": {"shops": list(SHOPS), "rows": [{"id": m["id"], "shop": m["shop"], "day": m["day"], "product": m["product"], "amount": m["amount"],
                                                   "commission": m["commission"], "memo": m["memo"],
                                                   "text": f"{_md(m['day'])} · {m['shop']} · {m['product'] or '상품 이름 없음'} · {money(m['amount'])} (수익 {money(m['commission'])})"}
                                                  for m in rows]},
        "note": "에이블리·지그재그·토스쇼핑은 자동이에요 (하루 한 번 읽고 4시간마다 로그인 확인) — 무신사·29CM·기타만 ‘수익 직접 넣기’로 넣어 주세요",
    }
