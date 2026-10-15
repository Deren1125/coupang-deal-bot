"""대시보드 로그인 화면과 로그인 유지 (주인 10/15 — 휴대폰 바탕화면 앱으로 쓸 때).

아이폰·아이패드 바탕화면 앱은 기본 인증(브라우저가 묻는 아이디·비밀번호 창)을 잘 못 다뤄 열 때마다 묻거나 열리지 않는다.
그래서 /dashboard/login 에서 한 번 넣으면 30일 동안 그 기기에서 그대로 열리게 한다 (기본 인증도 그대로 됨 — 예전 즐겨찾기용).

- 세션: 32바이트 난수를 쿠키(dash_session; Secure·HttpOnly·SameSite=Strict·Path=/dashboard·30일)로 주고,
  DB(kv)에는 그 SHA-256 과 만료 시각, 아이디·비밀번호 지문만 둔다 (쿠키 값 자체는 어디에도 남기지 않음)
- 지문: 아이디·비밀번호를 scrypt(무작위 소금)로 줄인 값. 서버의 DASHBOARD_PASSWORD(또는 아이디)를 바꾸면 지문이 달라져
  그전 세션이 모두 풀린다
- 로그아웃: 그 쿠키의 세션을 DB 에서 지우고 쿠키도 지운다
- 로그인 폼의 CSRF: 화면을 열 때 난수 쿠키(dash_login, Path=/dashboard/login)를 주고, 폼에는 그 난수·시각을 서버 키로 서명한
  값을 넣는다. 둘이 맞고 2시간 안이어야 받는다 (다른 사이트가 쿠키를 심어도 서명은 못 만듦)
- 넣어 보기 제한: 한 곳에서 1분에 5번까지 (맞든 틀리든) + 틀린 시도는 기본 인증과 같은 FailureLimiter (막힘은 두 배씩 길어짐)
넣은 아이디·비밀번호·쿠키 값은 로그에 남기지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from html import escape
from typing import Any

from dealbot.dashboard import webapp
from dealbot.utils.timeutil import from_iso, to_iso

LOGIN_PATH = "/dashboard/login"
LOGOUT_PATH = "/dashboard/logout"
SESSION_COOKIE = "dash_session"
LOGIN_COOKIE = "dash_login"
SESSION_DAYS = 30
SESSION_MAX = 20  # 살아 있는 세션 수 상한 (넘으면 가장 오래된 것부터 지움)
LOGIN_TOKEN_SECONDS = 2 * 3600  # 로그인 화면을 연 뒤 이 시간 안에 넣어야 함 (넘으면 화면을 새로 줌)
LOGIN_BODY_MAX = 4096
KV_SESSION = "dash:session:"  # + SHA-256(쿠키 값) 16진수 → {exp, fp, at}
KV_SALT = "dash:session_salt"
KV_LOGIN_KEY = "dash:login_key"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43,64}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def _cookie(name: str, value: str, *, path: str, max_age: int) -> str:
    return f"{name}={value}; Max-Age={max_age}; Path={path}; Secure; HttpOnly; SameSite=Strict"


def session_cookie(token: str) -> str:
    return _cookie(SESSION_COOKIE, token, path="/dashboard", max_age=SESSION_DAYS * 86400)


def clear_session_cookie() -> str:
    return _cookie(SESSION_COOKIE, "", path="/dashboard", max_age=0)


def login_cookie(nonce: str) -> str:
    return _cookie(LOGIN_COOKIE, nonce, path=LOGIN_PATH, max_age=LOGIN_TOKEN_SECONDS)


def cookie_values(headers: dict[str, str], name: str, *, limit: int = 4) -> list[str]:
    """Cookie 헤더에서 그 이름의 값들 (같은 이름이 여럿이면 앞에서부터 limit 개). 모양이 이상한 조각은 건너뜀."""
    out: list[str] = []
    for part in (headers.get("cookie") or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip() == name:
            out.append(value.strip().strip('"'))
            if len(out) >= limit:
                break
    return out


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class SessionStore:
    """로그인 세션 (kv). 쿠키 값은 SHA-256 으로만 저장하고, 아이디·비밀번호 지문이 지금 설정과 같을 때만 유효."""

    def __init__(self, db: Any, *, user: str | None, password: str | None, now: Callable[[], datetime],
                 days: int = SESSION_DAYS, max_sessions: int = SESSION_MAX) -> None:
        self.db = db
        self._user = user or ""
        self._password = password or ""
        self.now = now
        self.days = days
        self.max_sessions = max(1, max_sessions)
        self._fp: str | None = None

    @property
    def fingerprint(self) -> str:
        """아이디·비밀번호 지문 (scrypt, 소금은 DB 에 한 번 만들어 둠). 프로세스마다 한 번만 계산."""
        if self._fp is None:
            salt = self.db.kv_get(KV_SALT)
            if not salt:
                salt = secrets.token_hex(16)
                self.db.kv_set(KV_SALT, salt)
            secret = f"{self._user}\0{self._password}".encode()
            self._fp = hashlib.scrypt(secret, salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=16).hex()
        return self._fp

    def _load(self, key: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self.db.kv_get(key) or "null")
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _alive(self, data: dict[str, Any] | None) -> bool:
        if not data:
            return False
        exp = from_iso(str(data.get("exp") or ""))
        fp = str(data.get("fp") or "")
        return exp is not None and exp > self.now() and hmac.compare_digest(fp.encode(), self.fingerprint.encode())

    def create(self) -> str:
        """새 세션 → 쿠키에 넣을 값 (호출한 쪽은 이 값을 쿠키로만 보내고 어디에도 적지 않는다)."""
        self.purge()
        token = secrets.token_urlsafe(32)
        now = self.now()
        record = {"exp": to_iso(now + timedelta(days=self.days)), "fp": self.fingerprint, "at": to_iso(now)}
        self.db.kv_set(KV_SESSION + _sha(token), json.dumps(record))
        return token

    def valid(self, token: str | None) -> bool:
        if not token or not _TOKEN_RE.match(token):
            return False
        key = KV_SESSION + _sha(token)
        data = self._load(key)
        if data is None:
            return False
        if self._alive(data):
            return True
        self.db.kv_take(key)  # 만료됐거나 비밀번호가 바뀐 세션은 바로 지움
        return False

    def any_valid(self, tokens: list[str]) -> bool:
        return any(self.valid(t) for t in tokens)

    def revoke(self, token: str | None) -> bool:
        if not token or not _TOKEN_RE.match(token):
            return False
        return self.db.kv_take(KV_SESSION + _sha(token)) is not None

    def purge(self) -> int:
        """만료·지문이 다른 세션을 지우고, 살아 있는 세션이 상한을 넘으면 오래된 것부터 지운다 (새로 만들 자리 하나 남김)."""
        alive: list[tuple[str, str]] = []

        def keep(key: str, value: str | None) -> bool:
            try:
                data = json.loads(value or "null")
            except ValueError:
                return False
            if isinstance(data, dict) and self._alive(data):
                alive.append((str(data.get("at") or ""), key))
                return True
            return False

        gone = self.db.kv_delete_prefix(KV_SESSION, keep=keep)
        extra = len(alive) - (self.max_sessions - 1)
        if extra > 0:
            old = {key for _, key in sorted(alive)[:extra]}
            gone += self.db.kv_delete_prefix(KV_SESSION, keep=lambda k, _v: k not in old)
        return gone


class LoginCsrf:
    """로그인 폼 CSRF: 쿠키의 난수 + 폼의 '시각.서명' (서명 키는 DB 에 한 번 만들어 둔 난수)."""

    def __init__(self, db: Any, *, clock: Callable[[], float] = time.time, max_age: int = LOGIN_TOKEN_SECONDS) -> None:
        self.db = db
        self.clock = clock
        self.max_age = max_age
        self._key: bytes | None = None

    def _secret(self) -> bytes:
        if self._key is None:
            key = self.db.kv_get(KV_LOGIN_KEY)
            if not key:
                key = secrets.token_hex(32)
                self.db.kv_set(KV_LOGIN_KEY, key)
            self._key = bytes.fromhex(key)
        return self._key

    def _sign(self, nonce: str, ts: int) -> str:
        mac = hmac.new(self._secret(), f"{nonce}|{ts}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def issue(self, existing: list[str] | None = None) -> tuple[str, str]:
        """(쿠키에 넣을 난수, 폼에 넣을 값). 이미 받은 난수 쿠키가 있으면 그대로 다시 써서, 로그인 화면을 탭 여러 개로
        열어도 먼저 연 화면의 폼이 그대로 된다 (쿠키를 누가 심어 둬도 서명은 이 화면을 받은 쪽만 앎)."""
        nonce = next((n for n in existing or [] if _NONCE_RE.match(n)), None) or secrets.token_urlsafe(32)
        ts = int(self.clock())
        return nonce, f"{ts}.{self._sign(nonce, ts)}"

    def check(self, nonces: list[str], given: str | None) -> bool:
        ts_raw, _, sig = (given or "").partition(".")
        if not ts_raw.isdigit() or len(ts_raw) > 12 or not sig:
            return False
        ts = int(ts_raw)
        age = self.clock() - ts
        if age < -60 or age > self.max_age:
            return False
        return any(_NONCE_RE.match(n) and hmac.compare_digest(self._sign(n, ts).encode(), sig.encode()) for n in nonces)


class AttemptLimiter:
    """로그인 버튼 누르기 제한: 한 손님이 window 초에 per_client 번까지 (맞든 틀리든). 메모리 안."""

    def __init__(self, *, per_client: int = 5, window: float = 60.0, clock: Callable[[], float] = time.monotonic,
                 max_clients: int = 4096) -> None:
        self.per_client = max(1, per_client)
        self.window = float(window)
        self.clock = clock
        self.max_clients = max(1, max_clients)
        self._hits: dict[str, deque[float]] = {}

    def hit(self, client: str) -> int | None:
        """한 번 셈. 한도를 넘었으면 다시 누를 수 있을 때까지 남은 초 (그때는 세지 않음), 아니면 None."""
        now = self.clock()
        cut = now - self.window
        for key in list(self._hits):
            q = self._hits[key]
            while q and q[0] <= cut:
                q.popleft()
            if not q:
                del self._hits[key]
        q = self._hits.get(client)
        if q is not None and len(q) >= self.per_client:
            return max(1, int(q[0] + self.window - now) + 1)
        if q is None:
            if len(self._hits) >= self.max_clients:
                self._hits.pop(next(iter(self._hits)))
            q = self._hits[client] = deque()
        q.append(now)
        return None


def wait_text(seconds: int) -> str:
    """막힌 시간 → '40초' · '10분' · '1시간 20분' (분은 반올림 — Retry-After 는 1초 넉넉히 붙어 601초로 옴)."""
    if seconds < 60:
        return f"{max(1, seconds)}초"
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes}분"
    hours, rest = divmod(minutes, 60)
    return f"{hours}시간" + (f" {rest}분" if rest else "")


# 주소의 msg=코드 → 고정 문구 (주소의 글자를 그대로 보여 주지 않음)
LOGIN_NOTICES = {
    "out": ("로그아웃했어요.", "ok"),
}

LOGIN_CSS = """
:root{--bg:#f4f5f7;--card:#fff;--line:#e6e8eb;--text:#191f28;--sub:#6b7684;--purple:#5b4fd6;--orange:#b45309}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-size:15px;line-height:1.5;-webkit-font-smoothing:antialiased;
word-break:keep-all;overflow-wrap:anywhere;
font-family:Pretendard,"Pretendard Variable",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
.wrap{min-height:100vh;min-height:100dvh;display:flex;align-items:flex-start;justify-content:center;padding:56px 16px 32px}
.box{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px 20px 20px}
.brand{display:flex;align-items:center;gap:12px;margin:0 0 20px}
.brand img{flex:none;width:52px;height:52px;border-radius:12px}
.brand h1{font-size:20px;line-height:1.25;margin:0;font-weight:700;letter-spacing:-.01em}
.brand p{margin:2px 0 0;color:var(--sub);font-size:13px}
.notice{border-radius:10px;padding:10px 12px;margin:0 0 14px;font-size:13.5px;background:#eef0ff;color:#3f35a8;border:1px solid #dcdffc}
.notice.bad{background:#fff4e5;color:var(--orange);border-color:#fde7c7}
.notice a{color:inherit;font-weight:700}
label{display:block;font-size:13px;font-weight:600;color:#4e5968;margin:0 0 12px}
input{display:block;width:100%;margin:6px 0 0;font:inherit;font-size:16px;font-weight:400;padding:11px 12px;min-height:46px;border:1px solid #d1d6db;
border-radius:10px;background:#fff;color:var(--text)}
input:focus{outline:2px solid var(--purple);outline-offset:1px;border-color:var(--purple)}
button{display:block;width:100%;margin:18px 0 0;font:inherit;font-size:16px;font-weight:700;min-height:48px;border:0;border-radius:10px;
background:var(--purple);color:#fff;cursor:pointer}
button:active{opacity:.9}
.hint{margin:16px 0 0;color:var(--sub);font-size:12.5px}
"""


def login_page(*, title: str, field_token: str, notice: tuple[str, str] | None = None, cross_site: bool = False) -> str:
    """로그인 화면 (스크립트 없음). title 은 설정의 대시보드 이름 (작은 글씨). 값은 모두 이스케이프."""
    parts = []
    if notice is not None:
        text, kind = notice
        parts.append(f'<div class="notice{" bad" if kind == "bad" else ""}" role="status">{escape(text)}</div>')
    if cross_site:  # 다른 앱·사이트에서 연 링크라 로그인 쿠키(SameSite=Strict)가 안 실렸을 수 있음 → 여기서 다시 열면 실림
        parts.append('<div class="notice" role="status">이미 로그인했다면 <a href="/dashboard">대시보드 열기</a>를 눌러 주세요.</div>')
    body = (
        '<main class="wrap"><div class="box">'
        f'<div class="brand"><img src="{escape(webapp.icon_url("icon-192.png"), quote=True)}" width="52" height="52" alt="">'
        f"<div><h1>{escape(webapp.APP_NAME)}</h1><p>{escape(title)}</p></div></div>"
        + "".join(parts)
        + f'<form method="post" action="{LOGIN_PATH}">'
        f'<input type="hidden" name="t" value="{escape(field_token, quote=True)}">'
        '<label for="user">아이디<input id="user" name="user" autocomplete="username" autocapitalize="none" autocorrect="off" '
        'spellcheck="false" required></label>'
        '<label for="password">비밀번호<input id="password" name="password" type="password" autocomplete="current-password" '
        'required></label>'
        '<button type="submit">로그인</button></form>'
        f'<p class="hint">한 번 로그인하면 이 기기에서 {SESSION_DAYS}일 동안 그대로 열려요. '
        "서버 비밀번호를 바꾸면 다시 로그인해요.</p>"
        "</div></main>"
    )
    return (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'>"
        f"{webapp.head_tags()}<style>{LOGIN_CSS}</style></head><body>{body}</body></html>"
    )
