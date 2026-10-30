"""대시보드에서 원격 로그인 화면 열기 (주인 10/23 — "로그인 풀리면 대시보드에서 바로 서버 화면을 열어 다시 로그인, 너한테 요청 안 하고").

- 할 일의 로그인 줄(네이버 블로그 셋 · 쇼핑커넥트 · 토스 · 에이블리 · 지그재그 · 인스타 검색 계정)에 '원격 화면으로 다시 로그인'
  (login.remote {target}) — 연결 탭 맨 위 '로그인 관리' 칸(logins.py)에도 같은 단추.
- 누르면: 새 비밀번호(VNC)를 만들어 kv 에 세션으로 두고, Blog-Auto 받은편지함에 {"login_remote": {target, pass, at}} 한 줄.
  네이버 블로그는 하루 한 번 (Blog-Auto remote_block — 10/27 계정 잠김 뒤): 막힌 줄에서 주인이 '그래도 열기'를 누르면 force: true 를 붙인다.
  Blog-Auto 가 서버 크롬 로그인 화면(deploy/login.sh · affiliate-login.sh)을 127.0.0.1:6080 에만 연다 — 바깥 포트는 열지 않는다.
- 화면은 대시보드 주소 아래 /dashboard/vnc/ 로만 보인다 (web.route_tunnel — 대시보드 로그인을 통과해야 하고, 세션이 살아 있는
  SESSION_MIN 분 동안만 이어 줌). 할 일 맨 위에 '원격 로그인 화면' 줄 (열기 링크에 이번 비밀번호).
- 로그인이 끝나면 Blog-Auto 쪽 스크립트가 화면을 닫고, 쇼핑커넥트·토스는 멈춤을 바로 푼다 (resume_if_login).
"""

from __future__ import annotations

import json
import secrets
import socket
import time
from typing import Any
from urllib.parse import urlencode

KV = "vnc_session"
PORT = 6080
SESSION_MIN = 25
GRACE_S = 180  # 누른 뒤 화면이 뜰 때까지 (login.sh 는 서비스를 멈추고 띄워 1~2분) — 이 안에 한 번도 안 뜨면 못 뜬 것
SEEN_AFTER_S = 20  # 누른 뒤 이만큼은 6080 이 열려 있어도 '떴음'으로 안 침 — 다시 누르면 닫히는 중인 예전 창일 수 있음 (주인 10/27)
PREFIX = "/dashboard/vnc/"
# Blog-Auto blogauto/remote_login.TARGETS 와 같은 이름·순서 (instagram = ig_lookup 인스타 검색 전용 계정 — deploy/ig-login.sh, 주인 10/26)
TARGETS = {"finance": "아카이브 블로그(쇼핑커넥트도)", "issue": "Pick 블로그", "hotdeal": "핫딜 블로그", "toss": "토스 쉐어링크",
           "ably": "에이블리", "zigzag": "지그재그", "instagram": "인스타 검색 계정",
           # 스레드 자동화(threads-auto) 계정의 스레드·인스타 (주인 10/27 '로그인 관리에 스레드, 인스타 등도 넣어줘')
           "threads": "스레드 (자동 팔로우·좋아요 계정)", "threads_ig": "핫딜 인스타 (자동 팔로우·좋아요 계정)"}
NAVER = ("finance", "issue", "hotdeal")
# '취소'로 닫을 수 있는 창 (Blog-Auto remote_login.CANCELABLE 와 같게 — 10/30 스레드 창도 TERM 에 정리가 생겨 모두)
CANCELABLE = tuple(TARGETS)
NAVER_HEADS = ("login", "login_down", "login_keep")  # 네이버 로그인 풀림·다시 로그인 (키 = <앞>:<블로그>)
PASS_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def target_of(key: str) -> str | None:
    """알림·할 일 키 → 원격 로그인 대상 (Blog-Auto remote_login.target_of 와 같은 규칙 — notice 의 login 칸을 따로 받지 않아도 같은 답).
    login:B · login_down:B · login_keep:B → B · mall_login:<몰>[_…] → 몰 (ably_cf → ably · instagram = 인스타 검색 계정).
    보안 확인(ownstats)·키자드(kz)·own_stats_block 은 None (로그인 화면인지는 기록 파일 todo 줄의 login 칸이 알려 줌)."""
    parts = str(key or "").split(":")
    if parts[0] in NAVER_HEADS and len(parts) == 2 and parts[1] in NAVER:
        return parts[1]
    if parts[0] == "mall_login" and len(parts) == 2:
        t = parts[1].split("_", 1)[0]
        return t if t in TARGETS and t not in NAVER else None
    return None


def session(db: Any, now: float | None = None) -> dict[str, Any] | None:
    """살아 있는 세션 {target, pass, at} — 없거나 SESSION_MIN 분이 지났으면 None."""
    try:
        s = json.loads(db.kv_get(KV) or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(s, dict) or not isinstance(s.get("at"), int | float):
        return None
    return s if (now or time.time()) - float(s["at"]) < SESSION_MIN * 60 else None


def start(db: Any, relay: Any, target: str, now: float | None = None, *, force: bool = False) -> dict[str, Any]:
    """force = 주인이 '그래도 열기'를 누름 — Blog-Auto 가 하루 한 번 막음(remote_block)을 넘기게 부탁에만 "force": true (참일 때만)."""
    now = now or time.time()
    s = {"target": target, "pass": "".join(secrets.choice(PASS_CHARS) for _ in range(8)), "at": now}  # VNC 비밀번호는 8자까지만 쓰임
    db.kv_set(KV, json.dumps(s))
    relay({"login_remote": dict(s, force=True) if force is True else s})
    return s


def cancel(db: Any, relay: Any, now: float | None = None) -> dict[str, Any] | None:
    """열린 원격 화면 취소 (주인 10/29 '원격화면 열기 한거, 취소할 수 있는 기능') — 세션을 지우고(할 일 줄·연결 탭 줄이 바로 빠짐,
    다시 열기도 바로 됨) Blog-Auto 받은편지함에 {"login_remote_cancel": {target, since, at}} 한 줄: 쇼핑몰·인스타 창은 서비스가
    그 창을 멈추고, 네이버 창은 창이 직접 보고 저장 없이 닫는다. 취소할 세션이 없거나 스레드 창이면 None (아무것도 안 보냄)."""
    s = session(db, now)
    if not s or s.get("target") not in CANCELABLE:
        return None
    db.kv_set(KV, json.dumps(None))
    relay({"login_remote_cancel": {"target": s["target"], "since": float(s["at"]), "at": now or time.time()}})
    return s


def listening(port: int = PORT) -> bool:
    """서버 화면(websockify)이 127.0.0.1:port 에서 받고 있나."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def refresh(db: Any, now: float | None = None, probe=listening) -> dict[str, Any] | None:
    """살아 있는 세션 — 화면이 한 번 열렸다가(seen) 닫혔으면 끝난 것으로 지운다 (주인 10/24 — '로그인했는데 할 일이 왜 남아 있어':
    로그인이 끝나면 Blog-Auto 스크립트가 화면을 닫는데, 줄은 25분 동안 그대로였다). 아직 한 번도 안 열렸으면(서버가 띄우는 중) 그대로."""
    s = session(db, now)
    if not s:
        return None
    up = probe()
    if up and not s.get("seen") and (now or time.time()) - float(s["at"]) >= SEEN_AFTER_S:
        s["seen"] = True
        db.kv_set(KV, json.dumps(s))
    elif not up and s.get("seen"):
        db.kv_set(KV, json.dumps(None))
        return None
    return s


def live(s: dict[str, Any] | None, now: float | None = None) -> bool:
    """화면이 떠 있거나(seen) 아직 띄우는 중(GRACE_S 안) — 이때만 새 부탁을 막고 다른 단추를 흐린다.
    한 번도 안 뜬 채 GRACE_S 가 지나면 못 뜬 것 (받은편지함을 늦게 읽음 · 6080 이 바쁨 · 스크립트 실패) — 다시 누를 수 있게.
    (세션은 그대로 둬 늦게라도 뜨면 refresh 가 seen 으로 바꾸고 터널도 이어 줌)"""
    return bool(s) and (bool(s.get("seen")) or (now or time.time()) - float(s["at"]) < GRACE_S)


def port(db: Any) -> int | None:
    return PORT if session(db) else None


def url(s: dict[str, Any]) -> str:
    q = urlencode({"autoconnect": "true", "resize": "scale", "path": PREFIX.strip("/") + "/websockify", "password": s["pass"]})
    return f"{PREFIX}vnc.html?{q}"


def item(db: Any, act_open: Any) -> dict[str, Any] | None:
    """할 일 맨 위 한 줄 (세션이 살아 있고, 화면이 떠 있거나 아직 띄우는 중일 때만 — 못 뜬 화면에 죽은 링크를 25분 두지 않음)."""
    s = refresh(db)
    if not s or not live(s):
        return None
    left = max(1, int(SESSION_MIN - (time.time() - float(s["at"])) / 60))
    return {"iid": "sys-vnc", "src": "dealbot", "blog": "", "kind": "remote_login", "sev": "todo", "urgent": True,
            "title": f"원격 로그인 화면 — {TARGETS.get(s['target'], s['target'])}",
            "status": f"30초쯤 뒤 열기를 눌러 로그인하세요 · 로그인하면 저절로 닫혀요 ({left}분 남음)", "since": float(s["at"]),
            "expires_at": float(s["at"]) + SESSION_MIN * 60, "primary": act_open("원격 화면 열기", url(s)), "more": [],
            "detail": {"lines": ["화면이 안 뜨면 20~30초 뒤 다시 눌러 주세요 (서버가 화면을 띄우는 중)",
                                 "아이패드: 화면 왼쪽 작은 탭 → 키보드 아이콘으로 글자 입력",
                                 "네이버는 아이디·비밀번호로 ('로그인 상태 유지' 켜기 — QR·일회용 번호는 저장이 안 돼요)",
                                 "창을 닫았거나 화면이 이상하면 '원격 로그인'을 다시 누르면 새로 열려요 (네이버)"]}}
