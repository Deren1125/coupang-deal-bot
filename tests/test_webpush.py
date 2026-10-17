"""웹 푸시 암호화·VAPID (주인 10/18 — 대시보드 앱 알림). RFC 8291 부록 A 시험 값, JWT 서명, 키 파일, 받는 곳 제한, 보내는 머리글."""

from __future__ import annotations

import base64
import json
import logging
import os
import stat

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from dash_helpers import PushServer, subscription
from dealbot.notify import vapid, webpush

d = vapid.b64u_decode


def test_rfc8291_appendix_a_vector() -> None:
    as_priv = ec.derive_private_key(int.from_bytes(d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1())
    assert vapid.public_b64(as_priv) == "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
    ua_pub = d("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4")
    body = webpush.encrypt(b"When I grow up, I want to be a watermelon", ua_pub, d("BTBZMqHH6r4Tts7J_aSIgg"),
                           as_private=as_priv, salt=d("DGv6ra1nlYgDCS1FRnbzlw"))
    assert vapid.b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_y"
        "l95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")


def test_encrypt_rejects_bad_keys() -> None:
    with pytest.raises(ValueError):
        webpush.encrypt(b"x", b"\x04" + b"\x00" * 10, b"0" * 16)
    _body, ua = subscription()
    with pytest.raises(ValueError):
        webpush.encrypt(b"x", vapid.public_raw(ua), b"short")


def test_vapid_jwt_verifies_and_has_the_claims(tmp_path) -> None:  # type: ignore[no-untyped-def]
    key = vapid.load_or_create(tmp_path)
    token = vapid.jwt(key, "https://fcm.googleapis.com", "https://52-79-136-64.sslip.io/dashboard", now=1_760_000_000)
    head, claims, sig = token.split(".")
    assert json.loads(d(head)) == {"typ": "JWT", "alg": "ES256"}
    c = json.loads(d(claims))
    assert c["aud"] == "https://fcm.googleapis.com" and c["sub"] == "https://52-79-136-64.sslip.io/dashboard"
    assert 0 < c["exp"] - 1_760_000_000 <= 24 * 3600
    raw = d(sig)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # 틀리면 예외
    auth = vapid.authorization(key, "https://fcm.googleapis.com", "https://x/dashboard")
    assert auth.startswith("vapid t=") and f"k={vapid.public_b64(key)}" in auth


def test_key_file_is_0600_never_regenerated_and_never_logged(tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    key = vapid.load_or_create(tmp_path)
    path = tmp_path / vapid.KEY_FILE
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    pem = path.read_bytes()
    again = vapid.load_or_create(tmp_path)
    assert path.read_bytes() == pem and vapid.public_b64(again) == vapid.public_b64(key)
    secret = pem.decode().split("-----")[2].strip().replace("\n", "")[:40]
    assert secret not in caplog.text
    path.write_text("broken")
    with pytest.raises(vapid.VapidError):
        vapid.load_or_create(tmp_path)
    assert path.read_text() == "broken"  # 못 읽어도 덮어쓰지 않는다


@pytest.mark.parametrize("endpoint, ok", [
    ("https://fcm.googleapis.com/fcm/send/abc", True),
    ("https://web.push.apple.com/QGx1", True),
    ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
    ("https://db5p.notify.windows.com/w/?token=x", True),
    ("http://fcm.googleapis.com/fcm/send/abc", False),
    ("https://fcm.googleapis.com:8443/x", False),
    ("https://evil.example.com/x", False),
    ("https://127.0.0.1/x", False),
    ("https://10.0.0.5/x", False),
    ("https://push.apple.com.evil.com/x", False),
    ("https://user@fcm.googleapis.com/x", False),
    ("https://fcm.googleapis.com/" + "a" * 1100, False),
])
def test_endpoint_allowlist(endpoint: str, ok: bool) -> None:
    assert (webpush.endpoint_problem(endpoint) is None) is ok


async def test_send_headers_and_encrypted_body(tmp_path) -> None:  # type: ignore[no-untyped-def]
    server = PushServer(201)
    key = vapid.load_or_create(tmp_path)
    body, _ua = subscription()
    async with server.client() as http:
        res = await webpush.send(http, key, "https://52-79-136-64.sslip.io/dashboard", endpoint=body["endpoint"],
                                 p256dh=body["keys"]["p256dh"], auth=body["keys"]["auth"], payload=b'{"v":1}', urgent=True,
                                 ttl=3600, tag="deal-1")
    assert res.ok
    req = server.requests[0]
    assert req.headers["TTL"] == "3600" and req.headers["Urgency"] == "high"
    assert req.headers["Topic"] == webpush.topic("deal-1") and len(req.headers["Topic"]) == 32
    assert req.headers["Content-Encoding"] == "aes128gcm" and req.headers["Authorization"].startswith("vapid t=")
    assert b'{"v":1}' not in req.content and len(req.content) > 86
    assert base64.urlsafe_b64encode(req.content[16:20]) and int.from_bytes(req.content[16:20], "big") == 4096


async def test_send_refuses_a_bad_endpoint_without_a_request(tmp_path) -> None:  # type: ignore[no-untyped-def]
    server = PushServer(201)
    key = vapid.load_or_create(tmp_path)
    body, _ua = subscription()
    async with server.client() as http:
        res = await webpush.send(http, key, "https://x/dashboard", endpoint="https://evil.example.com/x",
                                 p256dh=body["keys"]["p256dh"], auth=body["keys"]["auth"], payload=b"{}", urgent=False, ttl=60, tag="t")
    assert res.status == 400 and server.requests == []
