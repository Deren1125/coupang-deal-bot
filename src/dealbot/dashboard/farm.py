"""파밍 대시보드를 이 앱 안에서 (주인 11/15 — "sns 대시보드랑 파밍 대시보드 합치기. tailscale 안 켜도 앱에 접근").

파밍 허브(perpfarm)는 Tailscale Funnel 로 공개 HTTPS 주소(https://<이름>.<tailnet>.ts.net)를 열고 열쇠(Bearer)로만 답한다
(주인 11/15 — 블로그 서버에는 테일스케일을 깔지 않음, 메모리 아낌). 딜봇이 /dashboard/farm/* 를 그 주소로 넘겨, 주인은 휴대폰에서
테일스케일을 켜지 않고도 대시보드 로그인 하나로 파밍 화면을 본다. 열쇠는 이 서버만 안다 (브라우저로 가지 않음).

- 대상: FARM_HUB_URL (.env) 또는 설정 dashboard.farm_hub_url — https 이고 호스트가 *.ts.net 인 것만 (그 밖이면 없는 것으로:
  열쇠가 엉뚱한 곳에 실리지 않게) · 열쇠: FARM_HUB_TOKEN (.env — 값은 로그·응답·화면에 남기지 않음).
  둘 중 하나라도 없으면 허브를 아예 부르지 않고 안내 화면 (200, '파밍 연결 준비 중 — 주인 할 일 2개' · 허브는 틀린 열쇠 10분 10번 제한)
- 리디렉션은 따라가지 않는다 (열쇠를 단 채 다른 곳으로 가지 않게) — 브라우저에 넘기되 허브 밖을 가리키면 막는다
- 대시보드 로그인(guard_prefix) 아래에서만 — 로그인 전에는 본문을 읽지도, 허브를 부르지도 않는다 (web.route_proxy)
- 넘기는 요청 머리글은 REQ_ALLOW 뿐: 브라우저의 Authorization·Cookie(대시보드 세션 쿠키)·X-Dash-Token·Host 는 넘기지 않고
  서버가 Authorization: Bearer <열쇠> 와 X-Forwarded-Prefix: /dashboard/farm 을 붙인다
- 받는 응답 머리글은 RESP_ALLOW 뿐: Set-Cookie 는 버리고(허브가 대시보드 주소에 쿠키를 심지 못하게), Location 은 허브 주소·절대 경로면
  /dashboard/farm 을 앞에 붙이고(다른 곳으로 보내려 하면 막음), 보안 머리글은 여기서 정한 것(FARM_HEADERS)으로 덮는다
- 크기·시간: 올리기 15MB(넘으면 413) · 받기 20MB · 30초(넘으면 504 쉬운 말 화면). 웹소켓·SSE 없음
- GET·HEAD 가 아닌 요청은 같은 사이트에서 온 것만 (DashApi.same_site — Sec-Fetch-Site 가 same-origin 또는 없음 + Origin 이 있으면
  대시보드 주소만). 파밍 화면은 X-Dash-Token 을 모르므로 이 경로는 그 확인을 쓰지 않는다

파밍 화면의 스크립트는 대시보드와 같은 출처에서 돈다 — CSP 로 불러오기·요청을 이 서버로만 묶는다 (바깥 주소 없음, FARM_HEADERS).
허브가 지킬 것: 주소는 상대 경로(또는 X-Forwarded-Prefix 를 앞에 붙임) · 바깥 주소(CDN·글꼴) 없음 · '← SNS' 는 /dashboard/ 로.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from dealbot.web import BACK_HOME, redirect_target_ok
from dealbot.web import page as web_page

log = logging.getLogger(__name__)

PREFIX = "/dashboard/farm"
HOME = PREFIX + "/"
METHODS = ("GET", "HEAD", "POST", "PUT", "DELETE")
UPLOAD_MAX = 15 * 1024 * 1024
RESPONSE_MAX = 20 * 1024 * 1024
TIMEOUT_S = 30.0
URL_RE = re.compile(r"https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net(:[0-9]{1,5})?")  # Tailscale 주소만 (Funnel · MagicDNS)
# 브라우저 → 허브로 넘기는 머리글 (이것만). authorization·cookie·x-dash-token·host·origin·referer·x-forwarded-* 는 없음
REQ_ALLOW = ("accept", "accept-language", "content-type", "range", "if-none-match", "if-modified-since", "if-match", "if-range",
             "x-requested-with")
# 허브 → 브라우저로 넘기는 머리글 (이것만 · Content-Type 은 따로). set-cookie·CSP·CORS·www-authenticate 등은 버림
RESP_ALLOW = {"cache-control": "Cache-Control", "etag": "ETag", "last-modified": "Last-Modified", "content-range": "Content-Range",
              "accept-ranges": "Accept-Ranges", "content-disposition": "Content-Disposition", "retry-after": "Retry-After",
              "content-encoding": "Content-Encoding"}  # 허브가 그래도 압축해 보냈으면 그 표시는 같이 (본문을 받은 그대로 넘기므로)
# 파밍 화면에 붙이는 보안 머리글 — 대시보드 기본(service.SECURITY_HEADERS)과 같은 틀: 불러오기·요청·폼 모두 이 서버로만 (바깥 주소 없음).
# 다른 점: 허브 화면(crime_v3.html·research.html — 11/15 feat/oi-farming-core 에서 확인)이 페이지 안 <script>·onclick= 으로 돌아서
# script-src 에 'unsafe-inline' (없으면 화면이 안 뜸) · 글꼴(font-src) · 상대 주소(base-uri 'self') · 허브 알림용 서비스 워커(worker-src —
# 주소가 /dashboard/farm/sw.js 라 범위는 /dashboard/farm/ 까지만, Service-Worker-Allowed 는 넘기지 않아 대시보드 범위로 못 넓힘).
# 허브가 스크립트를 파일로 옮기면 'unsafe-inline' 을 뺀다 (파밍 화면은 대시보드와 같은 출처에서 돈다)
FARM_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; media-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; form-action 'self'; "
        "base-uri 'self'; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
HT = "text/html; charset=utf-8"


def clean_url(raw: str | None) -> str:
    """'https://perpfarm.tailnet.ts.net' 모양만 (https · 호스트가 *.ts.net · 경로·쿼리 없이, 끝의 / 는 뗌). 아니면 ''."""
    v = (raw or "").strip().rstrip("/").lower()
    return v if URL_RE.fullmatch(v) else ""


def _page(status: int, title: str, body: str) -> tuple[int, str, str, dict[str, str]]:
    return status, web_page(title, body, back=BACK_HOME), HT, {**FARM_HEADERS, "Cache-Control": "no-store"}


def rewrite_location(loc: str, base: str) -> str | None:
    """허브가 준 Location → 브라우저에 줄 값. 허브 주소·절대 경로는 /dashboard/farm 을 앞에, 상대 주소는 그대로, 그 밖(다른 곳)은 None."""
    loc = (loc or "").strip()
    if not loc or not all(0x20 < ord(ch) < 0x7F for ch in loc) or "\\" in loc:
        return None
    if loc.startswith("//"):
        return None
    if loc.startswith("/"):
        out = PREFIX + loc
        return out if redirect_target_ok(out) else None
    sp = urlsplit(loc)
    if sp.scheme or sp.netloc:
        if f"{sp.scheme}://{sp.netloc}".lower() != base:
            return None
        tail = (sp.path or "/") + (f"?{sp.query}" if sp.query else "") + (f"#{sp.fragment}" if sp.fragment else "")
        return rewrite_location(tail, base)
    return loc  # 'next?x=1' · './a' — 브라우저가 지금 주소(/dashboard/farm/…) 기준으로 푼다


class FarmProxy:
    def __init__(self, api: Any, *, client: httpx.AsyncClient | None = None) -> None:
        self.api = api
        self._client = client

    # ------------------------------------------------------------ 설정
    def target(self) -> tuple[str, str]:
        """(허브 주소 | '', 열쇠 | ''). 부를 때마다 읽는다 — 값은 어디에도 적지 않는다."""
        s = self.api.bot.settings
        url = clean_url(getattr(s.secrets, "farm_hub_url", None)) or clean_url(getattr(s.dashboard, "farm_hub_url", ""))
        token = (getattr(s.secrets, "farm_hub_token", None) or "").strip()
        if not all(0x20 < ord(ch) < 0x7F for ch in token):  # 머리글에 넣을 수 없는 글자(빈칸·줄바꿈·한글)가 섞인 열쇠는 없는 것으로
            token = ""
        return url, token

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            # trust_env=False: 서버의 프록시 환경변수를 타지 않고 바로. 리디렉션은 따라가지 않는다 (열쇠를 단 채 다른 곳으로 가지 않게)
            self._client = httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=httpx.Timeout(TIMEOUT_S, connect=10.0),
                                             limits=httpx.Limits(max_connections=8, max_keepalive_connections=4))
        return self._client

    def setup_page(self, url: str, token: str) -> tuple[int, str, str, dict[str, str]]:
        def mark(ok: bool) -> str:
            return "됨" if ok else "아직"

        body = ("파밍 화면을 이 앱에서 보려면 서버 .env 에 두 줄만 넣으면 돼요 (설치할 것 없음).<br><br>"
                f"1. 파밍 주소 FARM_HUB_URL (https://….ts.net) — {mark(bool(url))}<br>"
                f"2. 파밍 열쇠 FARM_HUB_TOKEN — {mark(bool(token))}<br><br>"
                "둘 다 넣고 딜봇을 다시 켜면 이 자리에 파밍 화면이 열려요.")
        return _page(200, "파밍 연결 준비 중 — 주인 할 일 2개", body)

    # ------------------------------------------------------------ 넘기기
    async def handle(self, method: str, path: str, query: str, headers: dict[str, str], body: bytes) -> Any:
        if method not in ("GET", "HEAD") and not self.api.same_site(headers):
            return _page(403, "막았어요", "다른 곳에서 보낸 요청이라 파밍으로 넘기지 않았어요.")
        url, token = self.target()
        if not url or not token:
            return self.setup_page(url, token)
        out = {k: headers[k] for k in REQ_ALLOW if headers.get(k)}
        out["authorization"] = f"Bearer {token}"
        out["x-forwarded-prefix"] = PREFIX
        out["x-forwarded-proto"] = "https"
        out["accept-encoding"] = "identity"  # 받은 그대로 크기를 세고 넘기려고 (압축은 앞단 Caddy 가)
        target = url + (path or "/") + (f"?{query}" if query else "")
        try:
            async with asyncio.timeout(TIMEOUT_S):
                async with self.client().stream(method, target, headers=out, content=body or None) as r:
                    chunks, size = [], 0
                    async for chunk in r.aiter_raw():
                        size += len(chunk)
                        if size > RESPONSE_MAX:
                            log.info("farm proxy: %s %s → 응답이 너무 큼", method, path[:120])
                            return _page(502, "너무 커요", "파밍 화면이 보낸 내용이 너무 커서 열지 못했어요.")
                        chunks.append(chunk)
                    status, rh, raw = r.status_code, r.headers, b"".join(chunks)
        except TimeoutError:
            log.info("farm proxy: %s %s → 시간 초과", method, path[:120])
            return _page(504, "파밍 서버가 대답이 없어요", f"{int(TIMEOUT_S)}초를 기다렸는데 답이 없었어요 — 잠시 뒤에 다시 열어 주세요.")
        except httpx.TimeoutException:
            log.info("farm proxy: %s %s → 시간 초과", method, path[:120])
            return _page(504, "파밍 서버가 대답이 없어요", "기다렸는데 답이 없었어요 — 잠시 뒤에 다시 열어 주세요.")
        except httpx.HTTPError as e:
            log.info("farm proxy: %s %s → 닿지 않음 (%s)", method, path[:120], type(e).__name__)  # 예외 글은 적지 않음 (주소가 들어 있음)
            return _page(502, "파밍 서버에 닿지 않아요", "파밍 서버가 꺼져 있거나 주소가 닫혀 있어요 — 잠시 뒤에 다시 열어 주세요.")
        if status == 401:  # 허브가 열쇠를 안 받음 — 브라우저에 401 을 넘기면 로그인 창이 뜨거나 앱이 '다시 로그인'으로 읽는다
            log.warning("farm proxy: 허브가 열쇠를 받지 않음 (401) — FARM_HUB_TOKEN 확인")
            return _page(502, "파밍 열쇠가 맞지 않아요", "서버의 FARM_HUB_TOKEN 을 다시 넣어 주세요.")
        if status < 200 or status > 599 or status == 101:
            return _page(502, "파밍 화면을 열지 못했어요", "잠시 뒤에 다시 열어 주세요.")
        extra = {name: rh[key] for key, name in RESP_ALLOW.items() if rh.get(key)}
        if "location" in rh:
            loc = rewrite_location(rh["location"], url)
            if loc is None:
                if 300 <= status < 400:
                    return _page(502, "막았어요", "파밍 화면이 다른 곳으로 보내려 해서 열지 않았어요.")
            else:
                extra["Location"] = loc
        extra.update(FARM_HEADERS)
        extra.setdefault("Cache-Control", "no-store")
        if method == "HEAD":
            extra[":length"] = rh.get("content-length", "0")
            raw = b""
        if status in (204, 304):
            raw = b""
        return status, raw, rh.get("content-type") or "application/octet-stream", extra


def register(web: Any, api: Any, *, client: httpx.AsyncClient | None = None) -> FarmProxy:
    proxy = FarmProxy(api, client=client)
    web.route_proxy(PREFIX, proxy.handle, methods=METHODS, max_body=UPLOAD_MAX)
    return proxy
