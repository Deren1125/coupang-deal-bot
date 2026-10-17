"""표준 웹 푸시 보내기 (RFC 8291 aes128gcm 암호화 + RFC 8030 전송 + RFC 8292 VAPID). pywebpush 없이 cryptography + httpx 로
(pywebpush 는 aiohttp·requests 를 더 끌어와서 — 주인 결정 D8). RFC 8291 부록 A 의 시험 값으로 확인 (tests/test_webpush.py).

- 받는 곳(endpoint)은 정해 둔 푸시 서버만: fcm.googleapis.com(안드로이드 크롬·삼성 인터넷)·*.push.apple.com(아이폰·아이패드)·
  updates.push.services.mozilla.com·*.notify.windows.com — https·443·1024자까지 (아무 주소로나 요청을 보내지 못하게)
- 기록에는 주소 대신 호스트 + 주소 해시 앞 8자만 (주소 자체가 그 기기로 보내는 열쇠라서)
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from dealbot.notify import vapid

log = logging.getLogger(__name__)

RECORD_SIZE = 4096
MAX_PLAINTEXT = 3000
ENDPOINT_MAX = 1024
ALLOWED_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com")
ALLOWED_SUFFIXES = (".push.apple.com", ".notify.windows.com")
TIMEOUT = 10.0


def _hmac(key: bytes, data: bytes) -> bytes:
    h = hmac.HMAC(key, hashes.SHA256())
    h.update(data)
    return h.finalize()


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 (한 블록이면 충분: 길이 ≤ 32)."""
    prk = _hmac(salt, ikm)
    return _hmac(prk, info + b"\x01")[:length]


def encrypt(payload: bytes, ua_public: bytes, auth_secret: bytes, *, as_private: ec.EllipticCurvePrivateKey | None = None,
            salt: bytes | None = None, record_size: int = RECORD_SIZE) -> bytes:
    """RFC 8291 aes128gcm 본문 한 덩어리 (머리: salt‖rs‖idlen‖keyid, 이어서 암호문 한 record). as_private·salt 는 시험용."""
    if len(ua_public) != 65 or ua_public[0] != 4:
        raise ValueError("p256dh 는 65바이트 비압축 점")
    if len(auth_secret) != 16:
        raise ValueError("auth 는 16바이트")
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt if salt is not None else os.urandom(16)
    as_public = vapid.public_raw(as_private)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    cipher = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 마지막 record 표시 0x02, 덧붙임 없음
    if len(cipher) + 86 > record_size:
        raise ValueError("payload too big for one record")
    return salt + struct.pack("!IB", record_size, len(as_public)) + as_public + cipher


def endpoint_problem(endpoint: str) -> str | None:
    """구독 주소를 받을 수 없는 이유 (받을 수 있으면 None): https·443·정해 둔 푸시 서버·1024자."""
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > ENDPOINT_MAX:
        return "length"
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in endpoint):
        return "chars"
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return "url"
    if parts.scheme != "https" or parts.username or parts.password:
        return "scheme"
    if port not in (None, 443):
        return "port"
    host = (parts.hostname or "").lower().rstrip(".")
    if host in ALLOWED_HOSTS or any(host.endswith(s) and len(host) > len(s) for s in ALLOWED_SUFFIXES):
        return None
    return "host"


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def log_name(endpoint: str) -> str:
    """기록용 이름: 호스트 + 해시 앞 8자 (주소 자체는 남기지 않음)."""
    return f"{(urlsplit(endpoint).hostname or '?')}#{endpoint_hash(endpoint)[:8]}"


def topic(tag: str) -> str:
    """Topic 헤더: 같은 일의 아직 안 간 알림을 새 것으로 바꾼다 (base64url(sha256(tag)) 앞 32자)."""
    return vapid.b64u(hashlib.sha256(tag.encode("utf-8")).digest())[:32]


@dataclass(slots=True)
class SendResult:
    status: int  # HTTP 상태 (네트워크 오류면 0)
    retry_after: float | None = None

    @property
    def ok(self) -> bool:
        return self.status in (200, 201, 202)

    @property
    def gone(self) -> bool:
        return self.status in (404, 410)

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status >= 500 or self.status == 429


def _retry_after(resp: httpx.Response) -> float | None:
    raw = (resp.headers.get("retry-after") or "").strip()
    return min(float(raw), 600.0) if raw.isdigit() else None


async def send(http: httpx.AsyncClient, key: ec.EllipticCurvePrivateKey, sub: str, *, endpoint: str, p256dh: str, auth: str,
               payload: bytes, urgent: bool, ttl: int, tag: str) -> SendResult:
    """푸시 하나. 주소 확인은 구독 때 했지만 보내기 직전에 한 번 더 (DB 를 누가 바꿔도 아무 데로나 보내지 않게)."""
    if endpoint_problem(endpoint) is not None:
        return SendResult(400)
    if len(payload) > MAX_PLAINTEXT:
        raise ValueError("payload too big")
    body = encrypt(payload, vapid.b64u_decode(p256dh), vapid.b64u_decode(auth))
    parts = urlsplit(endpoint)
    headers = {
        "TTL": str(max(0, int(ttl))),
        "Urgency": "high" if urgent else "normal",
        "Topic": topic(tag),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid.authorization(key, f"{parts.scheme}://{parts.netloc}", sub),
    }
    try:
        resp = await http.post(endpoint, content=body, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        log.info("web push %s failed: %s", log_name(endpoint), type(e).__name__)
        return SendResult(0)
    if resp.status_code not in (200, 201, 202):
        log.info("web push %s → %s", log_name(endpoint), resp.status_code)
    return SendResult(resp.status_code, _retry_after(resp))
