"""대시보드 '오늘 할 일' 칸 (주인 10/27 — 네이버 블로그가 반자동이 됨: 발행·예약·이웃 공감/댓글/신청·SNS 팔로우는 주인이 손으로,
하루 5번(아침·점심·오후·저녁·밤) 10~15분씩. "깔끔하고 부담 없이" — 지금 시간대만 펼치고 나머지는 접음 · 복사 단추 · 줄마다 체크 · 3/8).

GET /dashboard/api/routine (할 일 탭을 열 때만 — 첫 상태 값 예산 밖, 그리기는 static/routine.js) → view():
    {"day", "today": bool, "slots": [{key, at, label, items: [{…item, done, tag}], done, total, min_left, current}],
     "overflow", "done", "total", "ably": {n, note, rows: [{rid, pid, title, piece, name, price, shop, url, due, due_text}]} | None,
     "times", "rising": [키워드], "rests": [한 줄], "stats": 한 줄 | None, "seoi": {…} | None (목록이 있을 때만)} — rising·rests 는 extras() (주인 10/30) ·
     stats = 늘 None ('오늘 통계 보내기' 할 일 줄은 11/14 에 뺌 — 알림: notify/stats_remind) ·
     seoi = '이웃 관리' 책갈피의 오늘 수 (seoi.today — {visit, visit_cap, add_cap, how, feed, note, by: {블로그: {name, add, add_cap, visit, line}}} · 주인 11/14)
  목록이 없으면 slots 가 빈 칸 · total 0 → 칸을 숨긴다 (에이블리 링크 부탁이 있으면 그 칸만).
  주인 11/14 ('오류가 다시 안 나게 · 주인이 보기 전에 걸러지게') — 늘 실리는 칸:
    age_min = 블로그 프로그램 기록 파일이 쓰인 지 몇 분 (모르면 None) → 30분 넘거나 today 가 거짓이면 화면이 '소식이 N분째 없어요' ·
    gate = {checked, blocked, ok, hm, line} | None — 올리기 전 자동 점검 요약 ('자동 점검: 8편 모두 통과 (17:40)' · '2편 고치는 중') ·
    kkumi_new = 꾸미기 책갈피가 '설치했어요' 한 판과 다름 (kkumi.is_new) · relay = 블로그 프로그램에 아직 못 전한 체크 수 ·
    nb = 이웃 글 들르기 줄 (nbvisit.view — 블로그마다 남은 줄·오늘 책갈피로 체크한 수).
  줄의 paste.stale (만드는 중·못 만든 묶음 — html 없음)·gate {ok, codes, text} 는 blogauto._routine_item 이 거른 그대로.
에이블리 링크 부탁 (주인 10/28): Blog-Auto 칸 ably_requests (blogauto._ably_requests) — 서버는 에이블리를 열지 않고 주인이 폰 앱에서
  링크를 만들어 붙여 넣음 → 대시보드 일 blog.similar_links {blog: issue, pid, links: [붙여 넣은 글], rid}. 없거나 0개면 None.
출처: 블로그 프로그램 기록 파일 status.routine (blogauto._routine) + EXTRA_SOURCES (지금은 스레드 세션 몫 threads_items 하나 — 늘 []).
  두 번째 출처는 같은 줄 모양(blogauto._routine_item)으로 함수 하나만 더하면 된다 — 같은 id 는 먼저 온 줄만, 없는 시간대 줄은 뺀다.
체크: 봇 DB kv "routine_done:<그 목록의 날짜>" = id 목록 (JSON). 바꾸는 것은 대시보드 일 routine.done {id, done} (actions.py) 하나뿐
  (+ '이웃 관리' 책갈피가 들른 글의 줄 — nbvisit.tick 이 같은 set_done 으로).
  전날 것까지만 남기고 지운다. 이 칸은 사이트를 열거나 로그인하지 않는다 (읽고 체크만).
전하지 못한 체크 (주인 11/14 — 블로그 프로그램이 몇 시간 멈춘 동안 '발행했어요'·서로이웃 체크가 사라져 같은 글이 다음 날 다시 뜸):
  kv "routine_relay_pending" = [{name: pasted|nb_requested, args, rid, ts, aid?, tries?, tried?, ok?}] (200개 · 3일).
  actions._pasted 가 맡길 때마다 relay_add — 못 맡겼으면 aid 없이, 맡겼으면 aid 와 함께 (10분 안에 안 가져가 'expired' 되면 다시 보내려고).
  relay_tick (scheduler.dashboard_loop 1분마다): 다리가 살아 있으면 aid 없는 것을 다시 맡기고, 맡긴 것은 결과를 봄 —
  ok → 2시간 뒤 지움 (그동안 다음 날 목록에서도 체크로) · rejected·error → 지움 · expired·unknown → 다시 (10분·20분·40분·…·1시간 간격).
  carried() = 아직 못 전했거나 방금 전한 '했어요' 줄 id — view 가 그 목록의 done 에 더해 날이 바뀌어도 체크가 남는다.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.blogauto import (
    NV_BLOGS,
    ROUTINE_DEFAULT_SLOTS,
    ROUTINE_ITEMS_MAX,
    _routine_item,
)

log = logging.getLogger(__name__)

DONE_KEY = "routine_done:"
DONE_MAX = 300
THREADS_TIMEOUT_S = 1.0  # threads-auto 가 느려도 화면 요청을 붙잡지 않게 (주인 11/14 — 예전 3초 · 요청마다)
THREADS_CACHE_S = 120.0
RELAY_KEY = "routine_relay_pending"
RELAY_MAX = 200
RELAY_KEEP_S = 3 * 86400.0
RELAY_OK_KEEP_S = 7200.0  # 전한 뒤에도 이만큼은 체크로 (블로그 프로그램이 다음 목록에서 그 줄을 뺄 때까지)
RELAY_RETRY_S = (600.0, 1200.0, 2400.0, 3600.0)  # 'expired' 뒤 다시 맡기는 간격 (그 뒤로는 1시간마다)
RELAY_NAMES = ("pasted", "nb_requested")
STALE_MIN = 30  # 기록이 이보다 오래면 화면이 알림 (todohub.js 와 같게)


def _tz(bot: Any) -> ZoneInfo:
    return ZoneInfo(bot.settings.app.timezone)


def today(bot: Any, now: float) -> str:
    return datetime.fromtimestamp(now, _tz(bot)).strftime("%Y-%m-%d")


def blog_routine(bot: Any) -> dict[str, Any] | None:
    """블로그 프로그램 몫 (status.routine — blogauto._routine 이 거른 것)."""
    st = getattr(bot, "dash_state", None)
    export = st._export() if st is not None else None
    return ((getattr(export, "v2", None) or {}).get("status") or {}).get("routine")


def threads_items(bot: Any) -> list[dict[str, Any]]:
    """스레드 세션 몫 — threads-auto(127.0.0.1:8710, threads_proxy._call) GET /api/routine 의 items (같은 줄 모양, 주인 10/27 반자동).
    threads-auto 가 하루 한 번 읽기만 해서 만든 스레드·인스타 맞팔·팔로우·좋아요·댓글 초안·답글 초안·글 초안. 꺼져 있거나 오늘 것이 아니면 []."""
    day = today(bot, time.time())
    with _TH_LOCK:
        hit = _TH_CACHE.get("v")
        if hit is not None and hit[0] == day and time.monotonic() - hit[1] < THREADS_CACHE_S:
            return list(hit[2])
    items = _threads_fetch(day)
    with _TH_LOCK:  # 못 읽은 것([])도 기억 — 꺼진 서비스를 요청마다 1초씩 기다리지 않게
        _TH_CACHE["v"] = (day, time.monotonic(), items)
    return list(items)


_TH_CACHE: dict[str, tuple[str, float, list[dict[str, Any]]]] = {}
_TH_LOCK = threading.Lock()


def _threads_fetch(day: str) -> list[dict[str, Any]]:
    from dealbot.dashboard import threads_proxy

    try:
        status, raw, _ = threads_proxy._call("/api/routine", None, THREADS_TIMEOUT_S)
    except (OSError, ConnectionError):
        return []
    if status != 200:
        return []
    try:
        got = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    if not isinstance(got, dict) or got.get("day") != day or not isinstance(got.get("items"), list):
        return []
    return got["items"]


EXTRA_SOURCES: tuple[Callable[[Any], list[dict[str, Any]]], ...] = (threads_items,)  # 출처를 더할 때는 여기 함수 하나


def merged(bot: Any, now: float) -> dict[str, Any] | None:
    """{day, slots, items, overflow} — 블로그 몫 + EXTRA_SOURCES. 어느 쪽에도 없으면 None."""
    base = blog_routine(bot)
    extra: list[dict[str, Any]] = []
    for src in EXTRA_SOURCES:
        try:
            got = src(bot)
        except Exception as e:  # noqa: BLE001 — 한 출처가 틀려도 나머지는 보이게
            log.warning("routine source %s failed: %s", getattr(src, "__name__", "?"), type(e).__name__)
            got = []
        extra += [it for x in (got if isinstance(got, list) else [])[:ROUTINE_ITEMS_MAX] if (it := _routine_item(x)) is not None]
    if base is None and not extra:
        return None
    r = base or {"day": today(bot, now), "slots": [{"key": k, "at": a, "label": lab} for k, a, lab in ROUTINE_DEFAULT_SLOTS],
                 "items": [], "overflow": 0}
    keys = {s["key"] for s in r["slots"]}
    items = list(r["items"])
    seen = {i["id"] for i in items}
    for it in extra:
        if it["slot"] in keys and it["id"] not in seen:
            items.append(it)
            seen.add(it["id"])
    return {**r, "items": items}


def day_of(bot: Any, now: float) -> str:
    """체크를 적을 날짜 — 지금 보이는 목록의 날짜 (목록이 없으면 오늘).
    블로그 몫만 본다 (다른 출처는 늘 오늘 날짜의 줄만 내므로 날짜가 같음 — 체크 한 번에 threads-auto 를 부르지 않게, 주인 11/14)."""
    try:
        r = blog_routine(bot)
    except Exception:  # noqa: BLE001
        r = None
    return r["day"] if r else today(bot, now)


def done_ids(db: Any, day: str) -> set[str]:
    try:
        v = json.loads(db.kv_get(DONE_KEY + day) or "[]")
    except (TypeError, ValueError):
        return set()
    return {x for x in v if isinstance(x, str)} if isinstance(v, list) else set()


def set_done(db: Any, day: str, rid: str, done: bool) -> None:
    """체크 하나 바꾸기 — 그날 목록에 넣거나 뺀다. 그 날짜와 전날 것만 남긴다."""
    ids = done_ids(db, day)
    if done:
        ids.add(rid)
    else:
        ids.discard(rid)
    db.kv_set(DONE_KEY + day, json.dumps(sorted(ids)[:DONE_MAX], ensure_ascii=False))
    try:
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    except ValueError:
        return
    db.kv_delete_prefix(DONE_KEY, keep=lambda k, _v: k in (DONE_KEY + day, DONE_KEY + prev))


# ---------------------------------------------------------------- 전하지 못한 체크 (주인 11/14)
def relay_rows(db: Any) -> list[dict[str, Any]]:
    try:
        v = json.loads(db.kv_get(RELAY_KEY) or "[]")
    except (TypeError, ValueError):
        return []
    return [x for x in v if isinstance(x, dict) and x.get("name") in RELAY_NAMES and isinstance(x.get("args"), dict)
            and isinstance(x.get("rid"), str) and isinstance(x.get("ts"), int | float)] if isinstance(v, list) else []


def _relay_put(db: Any, rows: list[dict[str, Any]]) -> None:
    db.kv_set(RELAY_KEY, json.dumps(rows[-RELAY_MAX:], ensure_ascii=False, separators=(",", ":")))


def relay_add(db: Any, name: str, args: dict[str, Any], rid: str, now: float, aid: str | None = None) -> None:
    """체크 하나를 적어 둔다 — 같은 줄의 앞선 것은 지운다 (마지막에 누른 것만 전함). aid = 이미 맡긴 일 번호 (없으면 다음 tick 이 맡김)."""
    rows = [x for x in relay_rows(db) if not (x["name"] == name and x["rid"] == rid) and now - x["ts"] < RELAY_KEEP_S]
    row: dict[str, Any] = {"name": name, "args": args, "rid": rid, "ts": now}
    if aid:
        row.update(aid=aid, tries=1, tried=now)
    _relay_put(db, [*rows, row])


def relay_waiting(db: Any) -> int:
    """아직 블로그 프로그램에 닿지 않은 체크 수 (화면 한 줄)."""
    return sum(1 for x in relay_rows(db) if not x.get("ok"))


def carried(db: Any, now: float) -> set[str]:
    """아직 못 전했거나 방금 전한 '했어요' 줄 id — 날이 바뀐 목록에서도 체크로 (발행 줄은 pub·rsv 둘 다: 줄 종류가 하루 사이 바뀜)."""
    out: set[str] = set()
    for x in relay_rows(db):
        if x["args"].get("done") is not True or now - x["ts"] >= RELAY_KEEP_S:
            continue
        m = POST_ROW.match(x["rid"])
        out |= {f"pub:{m.group(1)}:{m.group(2)}", f"rsv:{m.group(1)}:{m.group(2)}"} if m else {x["rid"]}
    return out


def relay_tick(bot: Any, now: float | None = None) -> dict[str, int]:
    """1분마다 (scheduler.dashboard_loop): 못 전한 체크를 다시 맡기고, 맡긴 것의 결과를 본다. 돌려줌 {sent, done, dropped, left}."""
    now = now or time.time()
    db = bot.db
    rows = relay_rows(db)
    out = {"sent": 0, "done": 0, "dropped": 0, "left": 0}
    if not rows:
        return out
    outbox = getattr(bot, "actions_out", None)
    try:
        st = outbox.status() if outbox is not None else None
        fresh = st is not None and st.bridge_ok(now)
    except Exception as e:  # noqa: BLE001 — 상태를 못 읽으면 이번엔 보내지 않음
        log.warning("routine relay status failed: %s", type(e).__name__)
        st, fresh = None, False
    keep: list[dict[str, Any]] = []
    for x in rows:
        if x.get("ok"):
            if now - x["ok"] < RELAY_OK_KEEP_S:
                keep.append(x)
            continue
        if now - x["ts"] >= RELAY_KEEP_S:
            out["dropped"] += 1
            log.warning("routine relay: 3일 동안 못 전해 버림 (%s %s)", x["name"], x["rid"])
            continue
        if x.get("aid") and outbox is not None:
            row = outbox.store.get(x["aid"])
            status = row["status"] if row else "expired"  # 기록이 사라졌으면 다시
            if status == "ok":
                out["done"] += 1
                keep.append({**x, "ok": now})
                continue
            if status in ("rejected", "error"):  # 블로그 프로그램이 받아서 답함 — 다시 보내도 같은 답
                out["dropped"] += 1
                log.info("routine relay: %s %s → %s (다시 보내지 않음)", x["name"], x["rid"], status)
                continue
            if status not in ("expired", "unknown"):  # queued·running — 기다림
                keep.append(x)
                continue
            # expired = 10분 안에 안 가져감 · unknown = 가져갔는데 20분 넘게 답이 없음 (하다가 다시 켜짐 — actions_out.sweep) → 다시 맡김 (같은 표시를 두 번 해도 같음)
            x = {k: v for k, v in x.items() if k != "aid"}
        tries = int(x.get("tries") or 0)
        wait = RELAY_RETRY_S[min(tries, len(RELAY_RETRY_S)) - 1] if tries else 0.0
        if not fresh or outbox is None or (st.actions and not st.supports(x["name"])) or now - float(x.get("tried") or 0) < wait:
            keep.append(x)
            continue
        try:
            aid, _ = outbox.submit(action=x["name"], blog=x["args"].get("blog"), args=x["args"], target="", confirmed=False,
                                   session="routine-relay", cid=None)
        except Exception as e:  # noqa: BLE001 — 다음 tick 에 다시
            log.warning("routine relay submit failed: %s", type(e).__name__)
            keep.append(x)
            continue
        out["sent"] += 1
        keep.append({**x, "aid": aid, "tries": tries + 1, "tried": now})
    out["left"] = sum(1 for x in keep if not x.get("ok"))
    if keep != rows:
        _relay_put(db, keep)
    if out["sent"] or out["done"] or out["dropped"]:
        log.info("routine relay: 다시 맡김 %d · 전해짐 %d · 버림 %d · 남음 %d", out["sent"], out["done"], out["dropped"], out["left"])
    return out


def _due_text(due: float | None, now: float, z: ZoneInfo) -> str:
    """예약 발행 시각 — 오늘이면 'HH:MM', 다른 날이면 'M/D HH:MM'. 없거나 지났으면 ''."""
    if due is None or due <= now:
        return ""
    d, n = datetime.fromtimestamp(due, z), datetime.fromtimestamp(now, z)
    return d.strftime("%H:%M") if d.date() == n.date() else f"{d.month}/{d.day} {d:%H:%M}"


def ably_view(bot: Any, now: float) -> dict[str, Any] | None:
    """에이블리 링크 부탁 칸 (blogauto._ably_requests 가 거른 것 + 발행 시각 글). 줄이 없으면 None."""
    st = getattr(bot, "dash_state", None)
    export = st._export() if st is not None else None
    a = (getattr(export, "v2", None) or {}).get("ably_requests")
    if not a or not a.get("rows"):
        return None
    z = _tz(bot)
    rows = [{**r, "due_text": _due_text(r["due"], now, z)} for r in a["rows"]]
    return {"n": len(rows), "note": a["note"], "rows": rows}


RISING_MAX = 3


def extras(bot: Any) -> dict[str, Any]:
    """할 일 칸 위아래 두 줄 (주인 10/30 '대시보드 전면 수정'):
    rising = Pick 인기 검색어 중 '⚡ 막 뜨는 중'(badge) 이고 아직 안 쓴 키워드 (최대 RISING_MAX — 누르면 Pick 화면) ·
    rests = 지금 쉬는 것 한 줄씩 (네이버 접속 끔 · 붙여넣기 · 토스 점검 — 예전 단추가 왜 없는지)."""
    st = getattr(bot, "dash_state", None)
    v2 = (getattr(st._export(), "v2", None) or {}) if st is not None else {}
    tr = v2.get("trends") or {}
    rising = [x["keyword"] for x in tr.get("items") or [] if x.get("badge") and not x.get("written")][:RISING_MAX]
    status = v2.get("status") or {}
    paste = [NV_BLOGS.get(b, b) for b in ("finance", "issue", "hotdeal") if ((status.get("blogs") or {}).get(b) or {}).get("paste")]
    rests: list[str] = []
    if status.get("naver_off"):
        rests.append("네이버 계정 서버 접속 꺼 둠 — 블로그 글은 붙여넣기로 올리고, 로그인·쇼핑커넥트·예약 단추는 없어요")
    elif paste:
        rests.append(f"붙여넣기 방식 ({'·'.join(paste)}) — 서버는 네이버에 글을 올리지 않아요")
    toss = getattr(bot, "_toss_no_link", None)
    if callable(toss) and toss():
        rests.append("토스 쉐어링크 점검 — 11/4까지 토스 딜은 링크 없이 올려요")
    return {"rising": rising, "rests": rests}


POST_ROW = re.compile(r"^(?:pub|rsv):(finance|issue|hotdeal):(\d{1,9})$")  # 발행·예약 줄 id = 글 하나 (actions.PASTE_ROW_RE 와 같음)


def post_scores(bot: Any) -> dict[tuple[str, int], tuple[int, bool]]:
    """(블로그, 글 번호) → (인기 예상 점수 0~100, 🔥) — v2 posts 에서 점수가 있는 글만 (주인 11/10 '할 일 탭 점수 높은 순')."""
    st = getattr(bot, "dash_state", None)
    export = st._export() if st is not None else None
    posts = (getattr(export, "v2", None) or {}).get("posts") or {}
    out: dict[tuple[str, int], tuple[int, bool]] = {}
    for blog, rows in posts.items():
        for p in rows if isinstance(rows, list) else []:
            if isinstance(p, dict) and isinstance(p.get("pid"), int) and isinstance(p.get("score"), int):
                out[(blog, p["pid"])] = (p["score"], bool(p.get("popular")))
    return out


def with_score(it: dict[str, Any], scores: dict[tuple[str, int], tuple[int, bool]]) -> dict[str, Any]:
    """발행·예약 줄에 점수(sco)와 제목 옆 칩 글자(sc '🔥 88점'·'62점', hot)를 단다. 글 줄이 아니거나 점수를 모르면 그대로."""
    m = POST_ROW.match(str(it.get("id") or ""))
    own = it.get("score")  # Blog-Auto 가 줄에 직접 실은 점수가 먼저 (11/10 — v2 posts 는 블로그마다 잘려 많은 줄이 못 찾았음)
    if m and isinstance(own, int) and not isinstance(own, bool):
        got: tuple[int, bool] | None = (own, it.get("popular") is True)
    else:
        got = scores.get((m.group(1), int(m.group(2)))) if m else None
    if not got:
        return it
    chip = ("🔥 " if got[1] else "") + f"{got[0]}점"
    return {**it, "sco": got[0], "sc": chip, "hot": got[1]}  # 칩은 제목 옆 (주인 11/10 '글 옆에 점수') — routine.js


def by_score(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 시간대 안: 점수 있는 글 줄 높은 순 → 점수 모르는 줄은 원래 차례 그대로 뒤에 (안정 정렬)."""
    return sorted(items, key=lambda i: -i["sco"] if isinstance(i.get("sco"), int) else 1)


def view(bot: Any, now: float | None = None) -> dict[str, Any]:
    """GET /dashboard/api/routine 몸. 지금 시간대 = 시각(대시보드 시간대)이 지난 마지막 칸 (첫 칸 전이면 첫 칸)."""
    now = now or time.time()
    try:
        r = merged(bot, now)
    except Exception as e:  # noqa: BLE001
        log.warning("routine view failed: %s", type(e).__name__)
        r = None
    try:
        ably = ably_view(bot, now)
    except Exception as e:  # noqa: BLE001 — 부탁 칸이 틀려도 할 일은 보이게
        log.warning("routine ably view failed: %s", type(e).__name__)
        ably = None
    try:
        ex = extras(bot)
    except Exception as e:  # noqa: BLE001 — 두 줄이 틀려도 할 일은 보이게
        log.warning("routine extras failed: %s", type(e).__name__)
        ex = {"rising": [], "rests": []}
    ex["stats"] = None  # '오늘 통계 보내기' 할 일 줄은 뺌 (주인 11/14 — 알림으로: notify/stats_remind)
    ex.update(meta(bot, now))
    if not r:
        return {"day": None, "today": False, "slots": [], "overflow": 0, "done": 0, "total": 0, "ably": ably, "gate": None, "nb": None, **ex}
    hm = datetime.fromtimestamp(now, _tz(bot)).strftime("%H:%M")
    by_at = sorted(r["slots"], key=lambda s: s["at"])
    cur = next((s["key"] for s in reversed(by_at) if s["at"] <= hm), by_at[0]["key"])
    done = done_ids(bot.db, r["day"])
    try:  # 아직 못 전한 '발행했어요'·서로이웃 체크는 날이 바뀐 목록에서도 체크로 (주인 11/14)
        done |= carried(bot.db, now)
    except Exception as e:  # noqa: BLE001
        log.warning("routine carried failed: %s", type(e).__name__)
    try:
        scores = post_scores(bot)
    except Exception as e:  # noqa: BLE001 — 점수를 못 읽어도 할 일은 원래 차례로
        log.warning("routine scores failed: %s", type(e).__name__)
        scores = {}
    try:  # '이웃 관리' 책갈피로 한 오늘 서이추·이웃 들르기 수 (주인 11/14 — 블로그 칸 한 줄 · 목록이 있을 때만)
        from dealbot.dashboard import seoi

        ex["seoi"] = seoi.today(bot.db, now, _tz(bot))
    except Exception as e:  # noqa: BLE001 — 수를 못 읽어도 할 일은 보이게
        log.warning("routine seoi failed: %s", type(e).__name__)
        ex["seoi"] = None
    slots = []
    for s in r["slots"]:
        its = by_score([with_score({**i, "done": i["id"] in done, "tag": " · ".join(x for x in (i["group"], NV_BLOGS.get(i["blog"], "")) if x)}, scores)
                        for i in r["items"] if i["slot"] == s["key"]])
        slots.append({**s, "items": its, "done": sum(i["done"] for i in its), "total": len(its),
                      "min_left": sum(i["min"] for i in its if not i["done"]), "current": s["key"] == cur})
    try:  # 이웃 글 들르기 줄 ↔ '이웃 관리' 책갈피 (주인 11/14 — 블로그마다 다음에 열 글 · 오늘 책갈피로 체크한 수)
        from dealbot.dashboard import nbvisit

        nb = nbvisit.view(bot.db, [i for s in slots for i in s["items"]], r["day"])
    except Exception as e:  # noqa: BLE001 — 못 읽어도 할 일은 보이게
        log.warning("routine nbvisit failed: %s", type(e).__name__)
        nb = None
    return {"day": r["day"], "today": r["day"] == today(bot, now), "slots": slots, "overflow": r["overflow"],
            "done": sum(s["done"] for s in slots), "total": sum(s["total"] for s in slots), "ably": ably,
            "times": list(r.get("times") or []), "gate": gate_view(r, _tz(bot)), "nb": nb, **ex}


def gate_view(r: dict[str, Any], z: ZoneInfo) -> dict[str, Any] | None:
    """올리기 전 자동 점검 요약 — 줄마다의 gate 를 센다 (목록 요약 칸은 시각만 씀 · 줄과 어긋나지 않게). 검사한 줄이 없으면 None."""
    gated = [i for i in r["items"] if isinstance(i.get("gate"), dict)]
    if not gated:
        return None
    blocked = sum(1 for i in gated if not i["gate"]["ok"])
    hm = ""
    at = (r.get("gate") or {}).get("at")
    if at:
        try:
            d = datetime.fromisoformat(at)
            hm = (d.astimezone(z) if d.tzinfo else d).strftime("%H:%M")
        except ValueError:
            hm = ""
    tail = f" ({hm})" if hm else ""
    line = (f"자동 점검: {blocked}편 고치는 중 · {len(gated) - blocked}편 통과{tail}" if blocked
            else f"자동 점검: {len(gated)}편 모두 통과{tail}")
    return {"checked": len(gated), "blocked": blocked, "ok": not blocked, "hm": hm, "line": line}


def meta(bot: Any, now: float) -> dict[str, Any]:
    """목록이 있든 없든 실리는 칸 (주인 11/14): age_min · kkumi_new · relay."""
    out: dict[str, Any] = {"age_min": None, "kkumi_new": False, "relay": 0}
    try:
        st = getattr(bot, "dash_state", None)
        w = getattr(st._export() if st is not None else None, "written_at", None)
        if w is not None:
            out["age_min"] = max(0, int((now - w.timestamp()) // 60))
    except Exception as e:  # noqa: BLE001
        log.warning("routine age failed: %s", type(e).__name__)
    try:
        from dealbot.dashboard import kkumi

        out["kkumi_new"] = kkumi.is_new(bot.db)
    except Exception as e:  # noqa: BLE001
        log.warning("routine kkumi failed: %s", type(e).__name__)
    try:
        out["relay"] = relay_waiting(bot.db)
    except Exception as e:  # noqa: BLE001
        log.warning("routine relay count failed: %s", type(e).__name__)
    return out
