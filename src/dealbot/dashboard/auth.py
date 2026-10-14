"""대시보드 기본 인증(HTTP Basic). 아이디·비밀번호는 서버 .env 의 DASHBOARD_USER / DASHBOARD_PASSWORD.

비교는 늘 둘 다 하고(아이디가 틀려도 비밀번호까지), 길이가 드러나지 않게 해시끼리 hmac.compare_digest 로 한다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac


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
