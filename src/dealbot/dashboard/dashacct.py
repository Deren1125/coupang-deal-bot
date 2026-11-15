"""대시보드 로그인 보기·바꾸기 (주인 11/16 — '대시보드 로그인한 다음에 아이디·비번 볼 수 있는 기능, 거기서 수정도').

연결 탭의 접는 칸 '대시보드 로그인' (static/dashacct.js · 바로 가기 #/connect/login).

값이 사는 곳
- 처음 값은 서버 .env 의 DASHBOARD_USER / DASHBOARD_PASSWORD (그대로 둠 — 이 화면은 .env 를 고치지 않는다).
- 화면에서 바꾸면 데이터 폴더의 dashboard_login.json (권한 600 · 임시 파일 → os.replace) 에 적고, 그 값이 .env 보다 먼저다.
  요청마다 파일을 다시 본다(바뀐 때만 읽음) → 서버를 다시 켜지 않아도 바로 적용.
- 복구 길 둘 (잊거나 잘못 바꿔도 잠기지 않게):
  ① 그 파일을 지우면 .env 값으로 돌아간다.
  ② 서버에서 `bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD` 로 예전과 *다른* 값을 정하고 `sudo systemctl restart dealbot` 하면 그 값이 이긴다
     (아이디도 .env 의 DASHBOARD_USER 로 돌아감 · 같은 값으로 다시 정하면 지문이 같아 파일이 계속 이김) —
     파일에는 '바꿀 때의 .env 값 지문'(scrypt)을 같이 적어 두고, 지금 .env 지문이 그것과 다르면 파일을 쓰지 않는다 (.env 가 더 새것).
- 파일이 깨졌거나 규칙에 안 맞으면 쓰지 않고 .env 값으로 (로그 한 줄 — 값은 남기지 않음).

API (모두 /dashboard 로그인 아래 · 답은 Cache-Control: no-store)
  GET  /dashboard/api/dashacct          {user, custom} — 아이디와 '화면에서 바꾼 값인지'. 비밀번호 없음.
  POST /dashboard/api/dashacct/reveal   {user, password, seconds} — 같은 사이트 + X-Dash-Token (DashApi.guard) · 로그인 세션(쿠키)만 ·
                                        1분에 5번 · 화면은 seconds 초 보였다 다시 가림.
  POST /dashboard/api/dashacct/save     {user?, password, password2} — 같은 확인 · 규칙 검사 (setenv.sh 와 같은 규칙) 뒤 저장.
                                        지금 세션은 새 지문으로 옮겨 그대로, 다른 세션은 모두 풀림.
아이디·비밀번호 값은 로그·접속 기록(dash_audit)에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any

from dealbot.dashboard.auth import MIN_PASSWORD_LEN, SAFE_PASSWORD_RE
from dealbot.utils.timeutil import to_iso, utcnow

log = logging.getLogger(__name__)

FILE_NAME = "dashboard_login.json"
FILE_MAX = 4096
USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")  # 기본 인증은 ':' 앞까지가 아이디 · 로그인 화면은 앞뒤 공백을 지움 → 이 글자만
PASSWORD_MAX = 128
SHOW_SECONDS = 10
PER_MINUTE = 5
BODY_MAX = 1024
API_PATH = "/dashboard/api/dashacct"
REVEAL_PATH = API_PATH + "/reveal"
SAVE_PATH = API_PATH + "/save"
NO_STORE = {"Cache-Control": "no-store"}


def user_problem(user: str) -> str | None:
    if not USER_RE.fullmatch(user):
        return "아이디는 영문·숫자·-_.@ 만, 64자까지 써 주세요"
    return None


def password_problem(password: str) -> str | None:
    """deploy/lightsail/setenv.sh DASHBOARD_PASSWORD 와 같은 규칙 (12자 이상, 영문·숫자·-_. 만)."""
    if len(password) < MIN_PASSWORD_LEN:
        return f"비밀번호는 {MIN_PASSWORD_LEN}자 이상으로 써 주세요"
    if len(password) > PASSWORD_MAX:
        return f"비밀번호는 {PASSWORD_MAX}자까지 써 주세요"
    if not SAFE_PASSWORD_RE.fullmatch(password):
        return "비밀번호는 영문·숫자·-_. 만 써 주세요"
    return None


def _mark(user: str | None, password: str | None, salt: str) -> str:
    """.env 값 지문 (값 자체는 파일에 적지 않음)."""
    secret = f"{user or ''}\0{password or ''}".encode()
    return hashlib.scrypt(secret, salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=16).hex()


class LoginFile:
    """지금 쓰는 아이디·비밀번호: 데이터 폴더의 파일(화면에서 바꾼 값)이 있으면 그것, 없으면 .env 값."""

    def __init__(self, path: Path | None, env_user: str | None, env_password: str | None) -> None:
        self.path = path
        self._env = (env_user, env_password)
        self._sig: tuple[int, int, int] | None = None
        self._cached: tuple[str, str] | None = None

    def _warn(self, why: str) -> None:
        log.warning("dashboard: 로그인 파일을 쓰지 않고 서버 설정 값으로 (%s — 값은 기록하지 않음)", why)

    def _parse(self) -> tuple[str, str] | None:
        assert self.path is not None
        try:
            with open(self.path, "rb") as f:
                raw = f.read(FILE_MAX + 1)
            data = json.loads(raw.decode("utf-8")) if len(raw) <= FILE_MAX else None
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            self._warn("읽을 수 없음")
            return None
        password = data.get("password")
        user = data.get("user") if data.get("user") is not None else (self._env[0] or "")
        if not isinstance(password, str) or not isinstance(user, str) or password_problem(password) or user_problem(user):
            self._warn("규칙에 안 맞음")
            return None
        salt, mark = data.get("salt"), data.get("env")
        if mark is not None:  # 바꾼 뒤에 서버에서 .env 를 새로 정했으면(setenv.sh) 그쪽이 더 새것
            try:
                same = isinstance(mark, str) and isinstance(salt, str) and secrets.compare_digest(_mark(*self._env, salt), mark)
            except ValueError:
                same = False
            if not same:
                self._warn("서버 설정이 그 뒤에 바뀜")
                return None
        return user, password

    def _override(self) -> tuple[str, str] | None:
        if self.path is None:
            return None
        try:
            st = os.stat(self.path)
        except OSError:
            self._sig = self._cached = None
            return None
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
        if sig != self._sig:
            self._sig, self._cached = sig, self._parse()
        return self._cached

    @property
    def custom(self) -> bool:
        return self._override() is not None

    def current(self) -> tuple[str | None, str | None]:
        return self._override() or self._env

    def save(self, user: str, password: str) -> None:
        """규칙을 통과한 값만 받는다 (ValueError). 임시 파일(600)에 다 쓰고 os.replace — 반쪽 파일이 남지 않게."""
        problem = user_problem(user) or password_problem(password)
        if problem or self.path is None:
            raise ValueError(problem or "저장할 곳이 없어요")
        salt = secrets.token_hex(16)
        body = json.dumps({"user": user, "password": password, "salt": salt, "env": _mark(*self._env, salt), "at": to_iso(utcnow())})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{secrets.token_hex(6)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._sig = self._cached = None


def register(web: Any, api: Any) -> None:
    """DashApi.register 가 부른다. api = DashApi (guard·caller·audit), api.dash = service.Dashboard (creds·sessions)."""
    from dealbot.dashboard.actions import RateWindow
    from dealbot.dashboard.api import jresp

    dash = api.dash
    reveal_rate, save_rate = RateWindow(PER_MINUTE, 60.0), RateWindow(PER_MINUTE, 60.0)

    def out(status: int, obj: dict[str, Any]) -> Any:
        return jresp(status, obj, NO_STORE)

    def bad(status: int, code: str, text: str) -> Any:
        return out(status, {"status": "error", "code": code, "text": text})

    def audit(caller: Any, action: str, code: str) -> None:
        api.audit(session8=caller.session, client8=caller.client8, action=f"dashacct.{action}", target="", args={}, code=code,
                  action_id=None)  # 값은 남기지 않는다

    def enter(headers: dict[str, str], action: str, rate: Any) -> Any:
        """같은 사이트 + X-Dash-Token → 로그인 세션(쿠키) → 1분 5번. 통과하면 (Caller, 세션 쿠키 값)."""
        g = api.guard(headers)
        if len(g) == 4:
            return jresp(g[0], json.loads(g[1]), NO_STORE)
        caller = g[0]
        token = api.caller(headers)[2]
        if token is None:  # 기본 인증(브라우저 창)으로 들어온 경우 — 유지할 세션이 없음
            audit(caller, action, "no_session")
            return bad(403, "session", "로그인 화면(/dashboard/login)으로 로그인한 뒤 써 주세요")
        if not rate.hit("all"):
            audit(caller, action, "rate")
            return bad(429, "rate", "너무 자주 눌렀어요 — 1분 뒤 다시")
        return caller, token

    async def view(query: dict[str, str]) -> Any:
        return out(200, {"user": dash.creds.current()[0] or "", "custom": dash.creds.custom})

    async def reveal(body: dict[str, Any], headers: dict[str, str]) -> Any:
        e = enter(headers, "reveal", reveal_rate)
        if len(e) == 4:
            return e
        user, password = dash.creds.current()
        audit(e[0], "reveal", "done")
        return out(200, {"user": user or "", "password": password or "", "seconds": SHOW_SECONDS})

    async def save(body: dict[str, Any], headers: dict[str, str]) -> Any:
        e = enter(headers, "save", save_rate)
        if len(e) == 4:
            return e
        caller, token = e
        old_user = dash.creds.current()[0] or ""
        raw_user, password, again = body.get("user"), body.get("password"), body.get("password2")
        user = raw_user if isinstance(raw_user, str) and raw_user else old_user  # 비우면 아이디는 그대로
        if not isinstance(password, str) or not isinstance(again, str) or not isinstance(user, str):
            return bad(400, "bad_args", "새 비밀번호를 두 번 넣어 주세요")
        problem = user_problem(user) or password_problem(password)
        if problem is None and password != again:
            problem = "새 비밀번호 두 칸이 서로 달라요"
        if problem:
            audit(caller, "save", "refused")
            return bad(400, "rule", problem)
        try:
            dash.creds.save(user, password)
        except (OSError, ValueError):
            audit(caller, "save", "failed")
            log.warning("dashboard: 로그인 파일을 저장하지 못함 (값은 기록하지 않음)")
            return bad(500, "failed", "저장하지 못했어요 — 바뀐 것은 없어요")
        kept = dash.sessions.adopt(token)  # 이 기기는 그대로
        gone = dash.sessions.purge()  # 다른 기기는 모두 풀림 (지문이 달라짐)
        audit(caller, "save", "done")
        log.info("dashboard: 로그인 아이디·비밀번호를 화면에서 바꿈 (다른 세션 %d개 풀림 — 값은 기록하지 않음)", gone)
        return out(200, {"status": "ok", "code": "done", "user": user, "custom": True, "kept": kept,
                         "text": "바꿨어요 — 이 기기는 그대로, 다른 기기는 새 비밀번호로 다시 로그인해요"})

    web.route(API_PATH, view)
    web.route_json(REVEAL_PATH, reveal, max_body=BODY_MAX)
    web.route_json(SAVE_PATH, save, max_body=BODY_MAX)
