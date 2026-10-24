"""대시보드에서 원격 로그인 화면 열기 (주인 10/23 — "로그인 풀리면 대시보드에서 바로 서버 화면을 열어 다시 로그인, 너한테 요청 안 하고").

- 할 일의 로그인 줄(네이버 블로그 셋 · 쇼핑커넥트 · 토스)에 '원격 화면으로 다시 로그인' (login.remote {target}).
- 누르면: 새 비밀번호(VNC)를 만들어 kv 에 세션으로 두고, Blog-Auto 받은편지함에 {"login_remote": {target, pass, at}} 한 줄.
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
PREFIX = "/dashboard/vnc/"
TARGETS = {"finance": "아카이브 블로그(쇼핑커넥트도)", "issue": "Pick 블로그", "hotdeal": "핫딜 블로그", "toss": "토스 쉐어링크",
           "ably": "에이블리", "zigzag": "지그재그"}
PASS_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def session(db: Any, now: float | None = None) -> dict[str, Any] | None:
    """살아 있는 세션 {target, pass, at} — 없거나 SESSION_MIN 분이 지났으면 None."""
    try:
        s = json.loads(db.kv_get(KV) or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(s, dict) or not isinstance(s.get("at"), int | float):
        return None
    return s if (now or time.time()) - float(s["at"]) < SESSION_MIN * 60 else None


def start(db: Any, relay: Any, target: str, now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    s = {"target": target, "pass": "".join(secrets.choice(PASS_CHARS) for _ in range(8)), "at": now}  # VNC 비밀번호는 8자까지만 쓰임
    db.kv_set(KV, json.dumps(s))
    relay({"login_remote": s})
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
    if up and not s.get("seen"):
        s["seen"] = True
        db.kv_set(KV, json.dumps(s))
    elif not up and s.get("seen"):
        db.kv_set(KV, json.dumps(None))
        return None
    return s


def port(db: Any) -> int | None:
    return PORT if session(db) else None


def url(s: dict[str, Any]) -> str:
    q = urlencode({"autoconnect": "true", "resize": "scale", "path": PREFIX.strip("/") + "/websockify", "password": s["pass"]})
    return f"{PREFIX}vnc.html?{q}"


def item(db: Any, act_open: Any) -> dict[str, Any] | None:
    """할 일 맨 위 한 줄 (세션이 살아 있고, 열렸던 화면이 아직 닫히지 않았을 때만)."""
    s = refresh(db)
    if not s:
        return None
    left = max(1, int(SESSION_MIN - (time.time() - float(s["at"])) / 60))
    return {"iid": "sys-vnc", "src": "dealbot", "blog": "", "kind": "remote_login", "sev": "todo", "urgent": True,
            "title": f"원격 로그인 화면 — {TARGETS.get(s['target'], s['target'])}",
            "status": f"30초쯤 뒤 열기를 눌러 로그인하세요 · 로그인하면 저절로 닫혀요 ({left}분 남음)", "since": float(s["at"]),
            "expires_at": float(s["at"]) + SESSION_MIN * 60, "primary": act_open("원격 화면 열기", url(s)), "more": [],
            "detail": {"lines": ["화면이 안 뜨면 20~30초 뒤 다시 눌러 주세요 (서버가 화면을 띄우는 중)",
                                 "아이패드: 화면 왼쪽 작은 탭 → 키보드 아이콘으로 글자 입력, 네이버는 QR 로그인도 돼요"]}}
