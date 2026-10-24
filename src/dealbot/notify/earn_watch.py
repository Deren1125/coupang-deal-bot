"""새 제휴 수익 앱 알림 (주인 10/24 — "통계 탭에서 제휴 수익 하나 새로 추가될 때마다 나한테 앱 알림 보내줘. sns 운영 초창기에 힘이 될 것 같아서").

통계 탭 '제휴 수익'(dashboard/earn.py)과 같은 곳에서 주문 수만 센다 — 1분마다 (scheduler.dashboard_loop):
- 쿠팡 파트너스: 날짜 × 서브아이디마다 주문 수 (DB earn_coupang — 3시간마다 읽음)
- 네이버 쇼핑커넥트: 날짜마다 주문 수 (Blog-Auto 기록 파일 earnings.shopconnect — 확인된 뒤에만)
- 에이블리·지그재그·토스쇼핑: 쇼핑몰마다 '오늘' 주문 수 (기록 파일 creator_mall — 읽은 날짜와 함께)
직접 넣은 수익(무신사·29CM·기타)은 주인이 넣은 것이라 알리지 않는다.
지난번보다 주문 수가 늘어난 곳만 모아 한 통 ('🎉 제휴 수익이 생겼어요' · '쿠팡 1건 · 수익 1,230원'). 누르면 통계 탭.
처음 한 번은 지금 숫자를 기억만 한다 (지난 30일 주문으로 알림이 쏟아지지 않게). 기억은 kv earn_watch:seen.
할 일 줄을 만들지 않는다 (알림만) — 기기가 없으면 아무것도 보내지 않는다 (카톡 없음).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KV_SEEN = "earn_watch:seen"
KEEP_DAYS = 40  # 이보다 오래된 날짜 열쇠는 기억에서 지움
STATS_URL = "/dashboard/#/stats"
LABEL_COUPANG, LABEL_SC = "쿠팡", "쇼핑커넥트"


def won(n: int) -> str:
    return f"{int(n):,}원"


def snapshot(store: Any, export: Any, tz: str, now: float) -> dict[str, dict[str, Any]]:
    """{열쇠: {orders, commission, label}} — 통계 탭과 같은 출처, 주문 수만."""
    today = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
    first = (today - timedelta(days=29)).isoformat()
    out: dict[str, dict[str, Any]] = {}
    if store is not None:
        for r in store.coupang_days(first, today.isoformat()):
            k = f"coupang:{r.get('day')}:{r.get('sub_id') or ''}"
            out[k] = {"orders": int(r.get("orders") or 0), "commission": int(r.get("commission") or 0), "label": LABEL_COUPANG}
    v2 = (getattr(export, "v2", None) or {}) if export is not None else {}
    sc = (v2.get("earnings") or {}).get("shopconnect")
    if isinstance(sc, dict) and sc.get("confirmed") is True:
        for d in sc.get("days") or []:
            if isinstance(d, dict) and d.get("day"):
                out[f"sc:{d['day']}"] = {"orders": int(d.get("orders") or 0), "commission": int(d.get("commission") or 0),
                                          "label": LABEL_SC}
    cm = v2.get("creator_mall") or {}
    day = str(cm.get("read_at") or "")[:10]
    for m in cm.get("malls") or [] if day else []:
        if not isinstance(m, dict) or m.get("state") != "ok":
            continue
        w = next((x for x in m.get("windows") or [] if isinstance(x, dict) and x.get("label") == "오늘"), None)
        if w is not None:
            out[f"cm:{m.get('key')}:{day}"] = {"orders": int(w.get("orders") or 0), "commission": int(w.get("commission") or 0),
                                                "label": str(m.get("name") or m.get("key") or "쇼핑몰")}
    return out


def news(seen: dict[str, Any], cur: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    """늘어난 주문만 출처 이름별로 {label: {orders, commission}} (수익은 늘어난 몫만, 줄면 0)."""
    out: dict[str, dict[str, int]] = {}
    for k, v in cur.items():
        old = seen.get(k) or {}
        d = v["orders"] - int(old.get("orders") or 0)
        if d <= 0:
            continue
        s = out.setdefault(v["label"], {"orders": 0, "commission": 0})
        s["orders"] += d
        s["commission"] += max(0, v["commission"] - int(old.get("commission") or 0))
    return out


def message(new: dict[str, dict[str, int]]) -> tuple[str, str]:
    n = sum(v["orders"] for v in new.values())
    parts = [f"{k} {v['orders']}건" + (f" · 수익 {won(v['commission'])}" if v["commission"] else "") for k, v in new.items()]
    return (f"🎉 제휴 수익이 생겼어요! ({n}건)" if n > 1 else "🎉 제휴 수익이 생겼어요!"), " / ".join(parts)[:120]


def _prune(seen: dict[str, Any], today: date) -> dict[str, Any]:
    edge = (today - timedelta(days=KEEP_DAYS)).isoformat()
    keep = {}
    for k, v in seen.items():
        parts = k.split(":")
        day = parts[1] if parts[0] in ("coupang", "sc") else parts[-1]
        if day >= edge:
            keep[k] = v
    return keep


async def check(bot: Any, now: float | None = None) -> str | None:
    """1분마다. 새 주문이 있으면 앱 알림 한 통 (보낸 기기 수와 함께 한 줄), 없으면 None."""
    router = getattr(bot, "router", None)
    store = getattr(bot, "earn_store", None)
    state = getattr(bot, "dash_state", None)
    if router is None or store is None:
        return None
    now = now or time.time()
    tz = bot.settings.app.timezone
    export = state._export() if state is not None else None
    cur = snapshot(store, export, tz, now)
    raw = bot.db.kv_get(KV_SEEN)
    try:
        seen = json.loads(raw) if raw else None
    except ValueError:
        seen = None
    today = datetime.fromtimestamp(now, ZoneInfo(tz)).date()
    if seen is None:  # 처음: 기억만
        bot.db.kv_set(KV_SEEN, json.dumps(_prune(cur, today), ensure_ascii=False))
        return None
    new = news(seen, cur)
    merged = _prune({**seen, **{k: {"orders": v["orders"], "commission": v["commission"]} for k, v in cur.items()}}, today)
    bot.db.kv_set(KV_SEEN, json.dumps(merged, ensure_ascii=False))
    if not new:
        return None
    title, body = message(new)
    sent = await router.push_event(title, body, url=STATS_URL, tag="earn")
    log.info("new affiliate earnings: %s — pushed to %d device(s)", body, sent)
    return f"{body} (알림 {sent}대)"
