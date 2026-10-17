"""대시보드 앱·주인 알림 테스트가 같이 쓰는 것 (주인 10/18). 바깥 요청 없음 — 가짜 푸시 서버(httpx.MockTransport)만."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.notify import vapid

USER, PASSWORD = "owner", "plum-river-cactus-42"
ORIGIN = "https://52-79-136-64.sslip.io"
FCM = "https://fcm.googleapis.com/fcm/send/fake-endpoint-1"
FIXTURES = Path(__file__).parent / "fixtures" / "dash_contract"


def push_settings(settings: Settings) -> Settings:
    settings.collectors = []
    settings.notify.router = "push"
    settings.secrets.dashboard_user = USER
    settings.secrets.dashboard_password = PASSWORD
    return settings


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def blogauto_status(settings: Settings, *, age: float = 0, bridge: int = 1, actions: list[str] | None = None,
                    kakao_state: str = "ok") -> None:
    data = fixture("status_blogauto.json")
    data["at"] = time.time() - age
    data["bridge"] = bridge
    data["kakao"]["state"] = kakao_state
    data["kakao"]["expires_at"] = time.time() + 40 * 86400
    data["kakao_ok"] = kakao_state == "ok"
    if actions is not None:
        data["actions"] = actions
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "blogauto_status.json").write_text(json.dumps(data), encoding="utf-8")


def subscription() -> tuple[dict[str, Any], Any]:
    """가짜 브라우저 구독 (진짜 P-256 키 — 서버가 암호화할 수 있게). (subscribe 본문, 브라우저 개인 키)."""
    from cryptography.hazmat.primitives.asymmetric import ec

    ua = ec.generate_private_key(ec.SECP256R1())
    body = {"endpoint": FCM, "keys": {"p256dh": vapid.public_b64(ua), "auth": vapid.b64u(b"0123456789abcdef")}, "label": "Galaxy"}
    return body, ua


class PushServer:
    """가짜 푸시 서버: 받은 요청을 모으고, 정한 상태 코드로 답한다."""

    def __init__(self, *statuses: int, retry_after: str | None = None) -> None:
        self.statuses = list(statuses) or [201]
        self.retry_after = retry_after
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        headers = {"Retry-After": self.retry_after} if self.retry_after and code == 429 else {}
        return httpx.Response(code, headers=headers)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def product(**kw: Any) -> Product:
    base: dict[str, Any] = dict(source="ppomppu", product_id="toss:1", shop="toss", name="무선청소기 프로 X1", price=129000,
                                url="https://service.toss.im/shopping-share?p=1")
    base.update(kw)
    return Product(**base)


def queued(bot: Any, *, status: str = "awaiting_link", error: str | None = None, **kw: Any) -> Any:
    assert bot.db.enqueue(Deal(product(**kw), DealVerdict(is_deal=True)), score=1)
    item = bot.db.pending_items(50)[-1]
    if status != "pending":
        bot.db.update_queue_item(item.id, status=status, error=error)
    return bot.db.get_queue_item(item.id)


def session_headers(bot: Any, *, csrf: bool = True, **extra: str) -> dict[str, str]:
    token = bot.dashboard.sessions.create()
    h = {"cookie": f"dash_session={token}", ":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9",
         "sec-fetch-site": "same-origin", "origin": ORIGIN, "content-type": "application/json"}
    if csrf:
        h["x-dash-token"] = bot.dashboard.api.csrf_token(h)
    h.update(extra)
    return h


async def post_json(bot: Any, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any, dict[str, str]]:
    raw = json.dumps(body).encode()
    h = dict(headers, **{"content-length": str(len(raw))})
    res = await bot.web.dispatch("POST", path, h, raw)
    data = json.loads(res[1]) if isinstance(res[1], str) and res[1].startswith("{") else res[1]
    return res[0], data, (res[3] if len(res) >= 4 else {})


async def get_json(bot: Any, path: str, headers: dict[str, str]) -> tuple[int, Any, dict[str, str]]:
    res = await bot.web.dispatch("GET", path, headers)
    body = res[1]
    data = json.loads(body) if isinstance(body, str) and body[:1] in "{[" else body
    return res[0], data, (res[3] if len(res) >= 4 else {})


def decrypt(body: bytes, ua_private: Any, auth: bytes = b"0123456789abcdef") -> dict[str, Any]:
    """시험용: 받은 푸시 본문을 브라우저 쪽 키로 풀어 본다 (RFC 8291 — 서비스 워커가 받는 글)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from dealbot.notify.webpush import _hkdf

    salt, idlen = body[:16], body[20]
    as_public = body[21:21 + idlen]
    cipher = body[21 + idlen:]
    shared = ua_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ua_public = vapid.public_raw(ua_private)
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, cipher, None)
    return json.loads(plain.rstrip(b"\x00")[:-1].decode("utf-8"))


class Clock:
    def __init__(self, t: float = 1_760_772_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, s: float) -> None:
        self.t += s
