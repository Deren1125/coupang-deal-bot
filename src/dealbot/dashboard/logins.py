"""대시보드 '로그인 관리' 칸 (주인 10/26 — "혹시 나중에 연결이 튕길 때를 대비해서, 지금 있는 모든 로그인이 필요한 사이트들은 다 원격으로
탭 열어서 로그인 기능 넣어 주라. 대시보드에. 너가 알림 주면 대시보드에서 바로 처리할 수 있게").

GET /dashboard/api/logins (연결 탭 맨 위 칸을 열 때만 — 첫 상태 값 예산 밖, 그리기는 static/logins.js) → view():
    {"rows": [{id, label, state, text, where, bad, button, last}], "need": n, "qr": {img, left_s} | null, "vnc": {target, label, url, left_min} | null}
  state = ok · needed · resting · off · unknown · null(여기서 안 보는 것) — text 는 화면에 그대로 쓰는 한 줄 (한국어).
  button = {action: "login.remote", args: {target}, label} · {action: "login.qr", args: {}, label} · {url, label}(열린 원격 화면) · null.
  last = 마지막 원격 로그인 한 줄 '마지막 원격 로그인: ✅ 성공 · 13:56' (Blog-Auto 기록 status.logins[].last — 네이버 세 줄) · 없거나 기록이
  오래됐으면 None. 화면에서는 줄 글 아래 작은 회색 둘째 줄.
줄:
  · 블로그 프로그램 기록 파일 status.logins — 아카이브(+쇼핑커넥트) · Pick · 핫딜 · 토스 · 에이블리 · 지그재그 · 인스타 검색 계정 (원격 로그인)
    · 스레드 API 토큰 (읽기만). Blog-Auto 가 사이트를 열지 않고 읽은 상태. 기록이 없는 대상도 '모름' 줄로 늘 보인다.
  · 딜봇 네이버 (쇼핑커넥트 링크용 딜봇 크롬): 'QR 로그인' — 텔레그램 /naverlogin 과 같은 흐름 (app.naver_login · naver_login_wait).
    QR 화면은 GET /dashboard/api/naver_qr.png (대시보드 로그인 아래 · 떠 있는 동안만). 다른 기기 화면의 QR 을 네이버 앱으로 찍는다.
  · 다른 곳에서 하는 것 (읽기만, 단추 없음): 스레드·인스타·X 자동 활동 계정 (스레드·X 봇 세션 담당 — 이 칸은 그 로그인을 열지 않는다) ·
    텔레그램 봇 토큰. 딜봇 인스타·스레드 API 연결과 카톡 알림 토큰은 연결 탭 '인스타 · 스레드' · '알림' 칸에 상태와 단추가 있어
    여기엔 다시 쓰지 않는다 (주인 10/26 '중복 없이').
할 일 줄 (todo_items): QR 이 떠 있으면 'QR 보기'(sys-naverqr) · 딜봇이 일하다 네이버 쿠키가 없다고 본 뒤면 'QR 로그인'(conn-dealbot-naver).
주인이 누를 때만 연다 — 이 칸은 상태를 읽기만 하고, 정해진 때 로그인 화면을 열거나 실패한 로그인을 다시 시도하지 않는다
(주인 10/26 '서버 로그인 반복 금지'). 비밀번호·쿠키는 다루지 않는다 (원격 화면 비밀번호는 remote_login 세션 것 그대로).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import remote_login
from dealbot.dashboard.model import OK, safe_text

log = logging.getLogger(__name__)

NAVER_KV = "dash:naver_login"  # 딜봇 네이버: 마지막으로 본 로그인 쿠키 {ok, at} (naver_connect.is_logged_in 이 볼 때마다 — 사이트는 안 엶)
NAVER_NOTE_S = 600  # 같은 상태면 10분에 한 번만 다시 적음 (QR 기다리는 동안은 3초마다 봄)
QR_KV = "dash:naver_qr"  # QR 로그인 {at, until, result: null|ok|timeout|failed}
QR_S = 130  # wait_login 120초 + 여유
QR_GAP_S = 60  # 끝난 뒤 이만큼은 다시 열지 않음 (두 번 눌러 로그인 화면을 거듭 열지 않게)
QR_FILE = "dash_naver_qr.png"
QR_MAX_BYTES = 4_000_000
STATE_TEXT = {"ok": "로그인됨", "needed": "로그인 필요", "resting": "쉬는 중", "off": "안 씀", "unknown": "모름"}
REMOTE_LABEL, QR_LABEL = "원격 로그인", "QR 로그인"
QR_HOW = "다른 기기(PC·아이패드)에 뜬 QR 을 휴대폰 네이버 앱 › QR 로 찍어 주세요"
LAST_TEXT = {"ok": "✅ 성공", "keep_off": "⛔ '로그인 상태 유지' 꺼짐", "timeout": "⏰ 시간 초과", "closed": "창이 닫힘",
             "replaced": "새 창으로 바뀜"}  # 마지막 원격 로그인 (Blog-Auto remote_login 결과 — blogauto.LOGIN_LAST_RESULTS)
THREADS_AUTO_WHERE = "X 봇 세션 담당 — SNS 탭 X 화면 (스레드·핫딜 인스타는 위 줄에서 원격 로그인, 주인 10/27)"
_TASKS: set[asyncio.Task[Any]] = set()  # QR 기다림 (끝날 때까지 잡아 둠)


# ---------------------------------------------------------------- 딜봇 네이버 상태
def note_naver(db: Any, ok: bool, *, now: float | None = None, force: bool = False) -> None:
    """딜봇 크롬의 네이버 로그인 쿠키를 봤다 — 바뀌었거나 10분 지났을 때만 적는다."""
    now = now or time.time()
    old = naver_state(db)
    if force or old is None or old["ok"] != ok or now - old["at"] >= NAVER_NOTE_S:
        db.kv_set(NAVER_KV, json.dumps({"ok": bool(ok), "at": round(now, 1)}))


def naver_state(db: Any) -> dict[str, Any] | None:
    try:
        v = json.loads(db.kv_get(NAVER_KV) or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(v, dict) or not isinstance(v.get("ok"), bool) or not isinstance(v.get("at"), int | float):
        return None
    return {"ok": v["ok"], "at": float(v["at"])}


def qr_state(db: Any) -> dict[str, Any] | None:
    try:
        v = json.loads(db.kv_get(QR_KV) or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(v, dict) or not all(isinstance(v.get(k), int | float) for k in ("at", "until")):
        return None
    return v


def qr_open(db: Any, now: float | None = None) -> dict[str, Any] | None:
    """지금 떠 있는 QR (끝나지 않았고 시간 안) — 없으면 None."""
    s = qr_state(db)
    return s if s and not s.get("result") and (now or time.time()) < float(s["until"]) else None


def qr_path(bot: Any) -> Path:
    return Path(bot.settings.data_dir) / QR_FILE


def qr_png(bot: Any, now: float | None = None) -> Path | None:
    """GET /dashboard/api/naver_qr.png — QR 이 떠 있는 동안만 (그 밖에는 None → 404)."""
    if qr_open(bot.db, now) is None:
        return None
    p = qr_path(bot)
    try:
        return p if p.is_file() and not p.is_symlink() and p.stat().st_size <= QR_MAX_BYTES else None
    except OSError:
        return None


def browser_off(bot: Any) -> str | None:
    """딜봇 네이버를 못 쓰는 이유 (쓰면 None)."""
    if not bot.settings.browser.enabled:
        return "딜봇 브라우저 자동화가 꺼져 있어요 (쇼핑커넥트 링크는 아카이브 블로그 계정이 만들어요)"
    if getattr(bot, "naver_connect", None) is None:
        return "딜봇 크롬을 띄울 수 없어요 — 서버 점검이 필요해요"
    return None


async def start_qr(bot: Any, now: float | None = None) -> tuple[str, str]:
    """login.qr — 주인이 누를 때만. 텔레그램 /naverlogin 과 같은 길 (QR 화면 → 2분 기다림). 이미 떠 있거나 방금 끝났으면 다시 열지 않는다."""
    now = now or time.time()
    off = browser_off(bot)
    if off:
        return "off", off
    s = qr_state(bot.db)
    if s and not s.get("result") and now < float(s["until"]):
        return "done", "QR 이 이미 떠 있어요 — 연결 탭 '로그인 관리'에서 찍어 주세요"
    if s and now - float(s.get("ended") or s["at"]) < QR_GAP_S:
        return "busy", "방금 QR 을 열었어요 — 1분쯤 뒤에 다시 눌러 주세요"
    bot.db.kv_set(QR_KV, json.dumps({"at": now, "until": now + QR_S, "result": None}))  # 여는 동안 두 번 눌러도 하나만
    try:
        png, text = await bot.naver_login()
    except Exception as e:  # noqa: BLE001 — naver_login 이 이미 잡지만 한 번 더
        png, text = None, f"로그인 화면 열기 실패: {type(e).__name__}"
    if not png:
        done = text.startswith("이미")
        bot.db.kv_set(QR_KV, json.dumps({"at": now, "until": now, "ended": time.time(), "result": "ok" if done else "failed"}))
        if done:
            note_naver(bot.db, True, force=True)
            return "done", "이미 네이버에 로그인돼 있어요"
        return "failed", safe_text(text.lstrip("❌ "), 120) or "로그인 화면을 열지 못했어요"
    p = qr_path(bot)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(png)
    with contextlib.suppress(OSError):
        os.chmod(tmp, 0o600)
    tmp.replace(p)
    task = asyncio.create_task(_wait_qr(bot, now))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return "done", f"QR 을 띄웠어요 — 연결 탭 '로그인 관리'나 할 일 'QR 보기'에서 열어 {QR_HOW} (2분 안)"


async def _wait_qr(bot: Any, at: float) -> None:
    result = "failed"
    try:
        msg = await bot.naver_login_wait()
        result = "ok" if str(msg).startswith("✅") else "timeout"
    except Exception as e:  # noqa: BLE001
        log.warning("naver QR wait failed: %s", type(e).__name__)
    finally:
        with contextlib.suppress(OSError):
            qr_path(bot).unlink()
        s = qr_state(bot.db)
        if s and float(s["at"]) == at:
            bot.db.kv_set(QR_KV, json.dumps(dict(s, result=result, ended=time.time())))
        if result == "ok":
            note_naver(bot.db, True, force=True)


# ---------------------------------------------------------------- 칸
def _when(iso: str | float | None, tz: ZoneInfo, now: float) -> str:
    """'14:05'(오늘) · '10/25 14:05'."""
    if iso is None:
        return ""
    try:
        d = datetime.fromtimestamp(iso, tz) if isinstance(iso, int | float) else datetime.fromisoformat(iso).astimezone(tz)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return f"{d:%H:%M}" if d.date() == datetime.fromtimestamp(now, tz).date() else f"{d:%m/%d %H:%M}"


def _text(state: str | None, note: str, tz: ZoneInfo, now: float, *, checked: Any = None, until: Any = None) -> str:
    parts = [STATE_TEXT.get(state or "", "") or "여기서는 상태를 안 봐요"]
    if note:
        parts.append(note)
    if state == "resting" and (u := _when(until, tz, now)):
        parts.append(f"{u}까지")
    if c := _when(checked, tz, now):
        parts.append(f"확인 {c}")
    return " · ".join(parts)


def _row(rid: str, label: str, state: str | None, text: str, *, where: str = "", button: dict[str, Any] | None = None,
         last: str | None = None) -> dict[str, Any]:
    return {"id": rid, "label": label, "state": state, "text": text, "where": where, "bad": state == "needed", "button": button,
            "last": last}


def _last(v: Any, tz: ZoneInfo, now: float) -> str | None:
    """'마지막 원격 로그인: ✅ 성공 · 13:56' — 기록이 없거나(예전 Blog-Auto · 아직 안 함) 모르는 결과면 None."""
    if not isinstance(v, dict) or v.get("result") not in LAST_TEXT:
        return None
    w = _when(v.get("at"), tz, now)
    return f"마지막 원격 로그인: {LAST_TEXT[v['result']]}" + (f" · {w}" if w else "")


def _remote_button(target: str, vnc: dict[str, Any] | None, now: float | None = None) -> dict[str, Any]:
    vnc = vnc if remote_login.live(vnc, now) else None  # 못 뜬 화면(띄운 지 GRACE_S 지나도록 안 열림)은 없는 것으로 — 다시 누를 수 있게
    if vnc and vnc.get("target") == target:
        return {"url": remote_login.url(vnc), "label": "원격 화면 열기"}
    b: dict[str, Any] = {"action": "login.remote", "args": {"target": target}, "label": REMOTE_LABEL}
    if vnc and vnc.get("target") not in remote_login.NAVER:  # 서버 화면은 한 번에 하나 (127.0.0.1:6080) — 열린 화면이 닫힌 뒤에
        b["disabled"] = True  # 네이버 창은 새 부탁을 보면 스스로 닫히므로 다른 대상도 바로 누를 수 있다 (주인 10/27 — Pick 창 때문에 핫딜이 막힘)
    return b


def naver_row(bot: Any, tz: ZoneInfo, now: float) -> dict[str, Any]:
    label = "딜봇 네이버 (쇼핑커넥트 링크용)"
    off = browser_off(bot)
    if off:
        return _row("dealbot_naver", label, "off", f"안 씀 · {off}")
    q = qr_open(bot.db, now)
    if q:
        return _row("dealbot_naver", label, "needed", f"QR 로그인 기다리는 중 · {max(1, int(float(q['until']) - now))}초 남음")
    st = naver_state(bot.db)
    state = "unknown" if st is None else "ok" if st["ok"] else "needed"
    note = "아직 확인 전" if st is None else ""
    last = qr_state(bot.db)
    if last and last.get("result") == "timeout" and now - float(last.get("ended") or 0) < 3600 and state != "ok":
        note = "지난 QR 은 2분이 지나 닫혔어요"
    return _row("dealbot_naver", label, state, _text(state, note, tz, now, checked=st["at"] if st else None),
                button={"action": "login.qr", "args": {}, "label": QR_LABEL})


def _stale(export: Any, now: float) -> str:
    """기록 파일이 오래됐으면(STALE · 못 읽음) 그 안내 한 줄 — 새것이면 ''."""
    if getattr(export, "status", OK) == OK:
        return ""
    wa = getattr(export, "written_at", None)
    try:
        h = max(1, int((now - wa.timestamp()) // 3600)) if wa is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        h = None
    return f"블로그 프로그램 기록이 {h}시간 전 것" if h else "블로그 프로그램 기록이 오래됐어요"


def rows(bot: Any, export: Any = None, now: float | None = None) -> list[dict[str, Any]]:
    now = now or time.time()
    tz = ZoneInfo(bot.settings.app.timezone)
    if export is None:
        export = bot.dash_state._export() if getattr(bot, "dash_state", None) is not None else None
    got = (((getattr(export, "v2", None) or {}).get("status") or {}).get("logins")) or []
    by_id = {r["id"]: r for r in got}
    stale = _stale(export, now) if got else ""
    vnc = remote_login.refresh(bot.db, now)
    out: list[dict[str, Any]] = []

    def text_of(r: dict[str, Any]) -> tuple[str, str]:
        """(상태, 한 줄) — 기록이 오래됐으면 '모름' (그때 상태만 덧붙임 · Blog-Auto 가 멈췄을 수 있어 지금 상태로 보이지 않게)."""
        if stale:
            return "unknown", f"모름 · {stale} (그때 {STATE_TEXT.get(r['state'], '모름')})"
        return r["state"], _text(r["state"], r["note"], tz, now, checked=r["checked_at"], until=r["until"])

    for target, label in remote_login.TARGETS.items():  # 원격 로그인 대상은 기록이 없어도 늘 한 줄 (예전 Blog-Auto · 아직 안 씀)
        r = by_id.get(target)
        if r is None:
            out.append(_row(target, label, "unknown", "모름 · 블로그 프로그램이 아직 상태를 안 보냈어요", button=_remote_button(target, vnc, now)))
            continue
        btn = _remote_button(r["target"], vnc, now) if r["target"] and r["state"] != "off" else None
        last = None if stale else _last(r.get("last"), tz, now)  # 기록이 오래됐으면 안 보임 (지금 일처럼 보이지 않게)
        out.append(_row(target, r["label"], *text_of(r), where=r["where"], button=btn, last=last))
    out.append(naver_row(bot, tz, now))
    for r in got:  # 블로그 프로그램의 읽기만 하는 줄 (스레드 API 토큰 …) — 단추 없음
        if r["id"] not in remote_login.TARGETS:
            out.append(_row(r["id"], r["label"], *text_of(r), where=r["where"]))
    out.append(_row("threads_auto", "X 자동 활동 계정", None, "여기서는 상태를 안 봐요", where=THREADS_AUTO_WHERE))
    tg = bool(getattr(bot.settings.secrets, "telegram_bot_token", None))
    out.append(_row("telegram", "텔레그램 봇", "ok" if tg else "off", "로그인 없음 · 봇 토큰으로 연결" if tg else "안 씀 · 토큰 없음",
                    where="토큰 — 서버 .env (주인이 넣음)"))
    return out


def visits(bot: Any) -> dict[str, Any] | None:
    """네이버 접속 칸 (주인 10/27 '얼마나 줄었는지 대시보드에'): 블로그마다 한 줄 글. Blog-Auto 기록에 칸이 없으면 None."""
    try:
        export = bot.dash_state._export() if getattr(bot, "dash_state", None) is not None else None
        nv = (((getattr(export, "v2", None) or {}).get("status") or {}).get("naver_visits"))
    except Exception:  # noqa: BLE001
        nv = None
    if not nv:
        return None
    rows = []
    for b in nv["blogs"]:
        bits = [f"오늘 {b['today']}번", f"24시간 {b['day24']}/{b['cap']}번"]
        if b["blocked24"]:
            bits.append(f"막힘 {b['blocked24']}")
        if b["warm"]:
            bits.append("적응 기간" + (" · 곁일 쉼" if b["side_rest"] else ""))
        top = ", ".join(f"{j['job']} {j['n']}" for j in b["jobs"][:3])
        rows.append({"name": b["name"], "text": " · ".join(bits), "jobs": f"많이 연 일: {top}" if top else "",
                     "bad": b["cap"] > 0 and b["day24"] >= b["cap"]})
    return {"rows": rows, "total": f"서버 전체 24시간 {nv['all24']}/{nv['all_cap']}번"}


def view(bot: Any, now: float | None = None) -> dict[str, Any]:
    """GET /dashboard/api/logins 몸. 줄 하나가 틀려도 칸 전체는 보이게 (그 줄만 '확인 못 함')."""
    now = now or time.time()
    try:
        rs = rows(bot, now=now)
    except Exception as e:  # noqa: BLE001
        log.warning("logins view failed: %s", type(e).__name__)
        rs = [_row(t, label, "unknown", "모름 · 확인 못 함", button={"action": "login.remote", "args": {"target": t}, "label": REMOTE_LABEL})
              for t, label in remote_login.TARGETS.items()]
    q = qr_open(bot.db, now)
    s = remote_login.session(bot.db, now)
    s = s if remote_login.live(s, now) else None
    vnc = {"target": s["target"], "label": remote_login.TARGETS.get(s["target"], s["target"]), "url": remote_login.url(s),
           "left_min": max(1, int(remote_login.SESSION_MIN - (now - float(s["at"])) / 60))} if s else None
    return {"rows": rs, "need": sum(1 for r in rs if r["bad"]), "at": round(now, 1), "vnc": vnc, "visits": visits(bot),
            "qr": {"img": f"/dashboard/api/naver_qr.png?t={int(q['at'])}", "left_s": max(1, int(float(q["until"]) - now)), "how": QR_HOW}
            if q and qr_png(bot, now) is not None else None}


# ---------------------------------------------------------------- 할 일 줄
def todo_items(bot: Any, now: float, *, act: Any, open_link: Any, goto: Any) -> list[dict[str, Any]]:
    """QR 이 떠 있으면 'QR 보기' 한 줄 · 딜봇이 일하다 네이버 쿠키가 없다고 봤으면 'QR 로그인' 한 줄 (브라우저 자동화를 켰을 때만)."""
    if browser_off(bot):
        return []
    base = {"src": "dealbot", "blog": "", "sev": "todo", "urgent": True, "expires_at": None}
    q = qr_open(bot.db, now)
    if q and qr_png(bot, now) is not None:
        return [dict(base, iid="sys-naverqr", kind="naver_qr", title="딜봇 네이버 QR 로그인",
                     status=f"{QR_HOW} ({max(1, int(float(q['until']) - now))}초 남음)", since=float(q["at"]),
                     primary=open_link("QR 보기", f"/dashboard/api/naver_qr.png?t={int(q['at'])}"),
                     more=[goto("로그인 관리", "#/connect")], detail={"lines": ["로그인되면 이 줄은 저절로 사라져요"]})]
    st = naver_state(bot.db)
    if st is not None and not st["ok"]:
        return [dict(base, iid="conn-dealbot-naver", kind="naver_login", sev="stop", title="딜봇 네이버 로그인 필요",
                     status="쇼핑커넥트 링크용 딜봇 크롬 — QR 로그인 (네이버 앱으로 찍기)", since=st["at"],
                     primary=act("login.qr", QR_LABEL), more=[goto("로그인 관리", "#/connect")],
                     detail={"lines": ["누르면 서버가 네이버 QR 화면을 한 번 열어요 — 로그인은 주인이 직접 (2분 안)"]})]
    return []
