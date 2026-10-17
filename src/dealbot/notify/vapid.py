"""웹 푸시 서버 키 (VAPID, RFC 8292) — P-256 키 하나를 데이터 폴더에 한 번 만들어 두고 계속 쓴다.

- 키 파일: <data_dir>/vapid_private.pem, 처음 켤 때 O_CREAT|O_EXCL 로 0600 (이미 있으면 절대 새로 만들지 않음 — 새로 만들면
  모든 휴대폰 구독이 끊긴다). 읽을 수 없으면 덮어쓰지 않고 푸시만 끈다 (자기 점검이 ❌)
- 키 값은 어디에도 출력·기록하지 않는다. 자기 점검은 '알림 키 있음' 만
- 공개 키(브라우저 구독에 쓰는 applicationServerKey)는 base64url 비압축 점 (65바이트)
- JWT (ES256): aud = 푸시 서버 주소의 origin, exp = 지금 + 12시간, sub = 대시보드 주소 (이메일 없음)
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

KEY_FILE = "vapid_private.pem"
JWT_HOURS = 12


class VapidError(Exception):
    """키 파일이 있는데 읽을 수 없음 (덮어쓰지 않는다)."""


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64u_decode(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def load_or_create(data_dir: Path | str) -> ec.EllipticCurvePrivateKey:
    """키를 읽는다. 없으면 한 번 만든다 (0600). 있는데 못 읽으면 VapidError — 파일은 그대로 둔다."""
    path = Path(data_dir) / KEY_FILE
    if path.exists():
        try:
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        except (OSError, ValueError, TypeError) as e:
            raise VapidError(f"알림 키 파일을 읽지 못함 ({type(e).__name__})") from None
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise VapidError("알림 키 파일이 P-256 키가 아님")
        return key
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:  # 다른 프로세스가 먼저 만듦 — 그 키를 쓴다
        return load_or_create(data_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
        f.flush()
        os.fsync(f.fileno())
    return key


def public_raw(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    pub = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_b64(key: ec.EllipticCurvePrivateKey) -> str:
    """브라우저 pushManager.subscribe 의 applicationServerKey (base64url, 65바이트 비압축 점)."""
    return b64u(public_raw(key))


def jwt(key: ec.EllipticCurvePrivateKey, aud: str, sub: str, *, now: float | None = None, hours: float = JWT_HOURS) -> str:
    """ES256 JWT (서명은 DER 이 아니라 r‖s 64바이트)."""
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": aud, "exp": int((time.time() if now is None else now) + hours * 3600), "sub": sub}
    signing = f"{b64u(json.dumps(header, separators=(',', ':')).encode())}.{b64u(json.dumps(claims, separators=(',', ':')).encode())}"
    der = key.sign(signing.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return f"{signing}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def authorization(key: ec.EllipticCurvePrivateKey, aud: str, sub: str, *, now: float | None = None) -> str:
    """Authorization 헤더 값: 'vapid t=<jwt>, k=<공개 키>'."""
    return f"vapid t={jwt(key, aud, sub, now=now)}, k={public_b64(key)}"
