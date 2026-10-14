"""대시보드 기본 인증(HTTP Basic). 아이디·비밀번호는 서버 .env 의 DASHBOARD_USER / DASHBOARD_PASSWORD.

비교는 늘 둘 다 하고(아이디가 틀려도 비밀번호까지), 길이가 드러나지 않게 해시끼리 hmac.compare_digest 로 한다.
틀린 시도는 FailureLimiter 가 센다: 한 곳에서 10분에 10번, 또는 모두 합쳐 10분에 100번 틀리면 그 창이 지날 때까지
비밀번호를 확인하지 않고 429 (맞는 비밀번호여도 — 맞았는지 알려 주지 않게). 공개 주소라 마구 넣어 보는 걸 막는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import time
from collections import deque
from collections.abc import Callable

from dealbot.web import PEER_HEADER

MIN_PASSWORD_LEN = 12
# systemd EnvironmentFile·.env 를 거쳐도 그대로 남는 글자 (따옴표·역슬래시·$ 는 바뀌거나 지워져 로그인이 안 될 수 있음)
SAFE_PASSWORD_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_LOOPBACK = ("127.", "::1", "::ffff:127.")


def password_problems(password: str | None) -> list[str]:
    """자기 점검용: 비밀번호의 약점 (값·길이는 말하지 않음)."""
    if not password:
        return []
    out = []
    if len(password) < MIN_PASSWORD_LEN:
        out.append(f"{MIN_PASSWORD_LEN}자보다 짧음")
    if not SAFE_PASSWORD_RE.match(password):
        out.append("영문·숫자·-_. 말고 다른 글자가 있음 (따옴표·역슬래시·$ 는 서버에서 바뀔 수 있음)")
    return out


def client_key(headers: dict[str, str]) -> str:
    """틀린 시도를 셀 '손님' 이름. Caddy 뒤에서는 접속 주소가 늘 127.0.0.1 이라, 접속이 이 서버 안(루프백)에서 왔을 때만
    Caddy 가 붙인 X-Forwarded-For 의 마지막 주소(Caddy 가 본 실제 주소)를 쓴다. 바깥에서 바로 온 접속은 그 주소 그대로."""
    peer = (headers.get(PEER_HEADER) or "").strip()
    if peer.startswith(_LOOPBACK) or peer == "localhost":
        last = (headers.get("x-forwarded-for") or "").split(",")[-1].strip()
        if last:
            return last[:64]
    return peer[:64] or "-"


class FailureLimiter:
    """틀린 비밀번호 시도 제한 (메모리 안 — 재시작하면 처음부터).

    - 한 손님이 window 초 안에 per_client 번 틀리면, 그 가운데 가장 오래된 시도가 창 밖으로 나갈 때까지 막음
    - 모든 손님을 합쳐 window 초 안에 global_cap 번 틀리면 모두 막음 (주소를 바꿔 가며 넣어 보는 경우)
    막혀 있는 동안은 비밀번호를 확인하지 않는다.
    """

    def __init__(self, *, per_client: int = 10, global_cap: int = 100, window: float = 600.0,
                 clock: Callable[[], float] = time.monotonic, max_clients: int = 4096, log_every: float = 60.0) -> None:
        self.per_client = max(1, per_client)
        self.global_cap = max(1, global_cap)
        self.window = float(window)
        self.clock = clock
        self.max_clients = max(1, max_clients)
        self.log_every = log_every
        self._fails: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._unlogged = 0
        self._last_log: float | None = None

    def _prune(self, now: float) -> None:
        cut = now - self.window
        while self._all and self._all[0] <= cut:
            self._all.popleft()
        for key in list(self._fails):
            q = self._fails[key]
            while q and q[0] <= cut:
                q.popleft()
            if not q:
                del self._fails[key]

    def blocked(self, client: str) -> int | None:
        """막혀 있으면 풀릴 때까지 남은 초, 아니면 None."""
        now = self.clock()
        self._prune(now)
        waits = []
        q = self._fails.get(client)
        if q is not None and len(q) >= self.per_client:
            waits.append(q[-self.per_client] + self.window - now)
        if len(self._all) >= self.global_cap:
            waits.append(self._all[-self.global_cap] + self.window - now)
        return max(1, int(max(waits)) + 1) if waits else None

    def failure(self, client: str) -> None:
        now = self.clock()
        self._prune(now)
        if client not in self._fails and len(self._fails) >= self.max_clients:
            self._fails.pop(next(iter(self._fails)))  # 주소가 너무 많으면 가장 먼저 들어온 것부터 잊음 (전체 한도는 그대로)
        self._fails.setdefault(client, deque()).append(now)
        self._all.append(now)
        self._unlogged += 1

    def success(self, client: str) -> None:
        self._fails.pop(client, None)

    def log_due(self) -> int:
        """로그에 남길 때가 됐으면(log_every 초에 한 번) 그동안 쌓인 실패 수, 아니면 0."""
        now = self.clock()
        if self._unlogged and (self._last_log is None or now - self._last_log >= self.log_every):
            n, self._unlogged, self._last_log = self._unlogged, 0, now
            return n
        return 0


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


def parse_basic(header: str | None) -> tuple[bytes, bytes] | None:
    """'Basic base64(id:pw)' → (id, pw). 모양이 틀리면 None."""
    if not header:
        return None
    scheme, _, param = header.strip().partition(" ")
    if scheme.lower() != "basic" or not param.strip():
        return None
    try:
        raw = base64.b64decode(param.strip(), validate=True)
    except (binascii.Error, ValueError):
        return None
    user, sep, password = raw.partition(b":")
    if not sep:
        return None
    return user, password


def check_basic(header: str | None, user: str | None, password: str | None) -> bool:
    """Authorization 헤더가 설정된 아이디·비밀번호와 같은지. 설정이 비어 있으면 늘 거절."""
    given = parse_basic(header)
    given_user, given_password = given if given is not None else (b"", b"")
    ok_user = hmac.compare_digest(_digest(given_user), _digest((user or "").encode("utf-8")))
    ok_password = hmac.compare_digest(_digest(given_password), _digest((password or "").encode("utf-8")))
    # & 로 묶어 둘 다 비교한 뒤에 판단 (and 처럼 앞에서 끊지 않음)
    return bool(user) and bool(password) and given is not None and bool(ok_user & ok_password)
