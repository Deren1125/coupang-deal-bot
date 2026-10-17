"""대시보드를 휴대폰 바탕화면 앱처럼 (주인 10/15): 매니페스트·아이콘(로그인 없이, 정확한 경로만), <head> 링크·메타·CSP,
로그인 화면(CSRF·1분 한도·틀린 시도 막힘 두 배씩), 세션 쿠키(SHA-256 만 저장·30일·로그아웃·비밀번호 바꾸면 모두 풀림),
기본 인증은 그대로, 로그인 없이 연 화면은 로그인 화면으로 · data.json·POST 는 401."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest
from PIL import Image

from dealbot.config import DashboardCardConfig, DashboardConfig
from dealbot.dashboard import auth as dash_auth
from dealbot.dashboard import login as dash_login
from dealbot.dashboard import render, webapp
from dealbot.dashboard.blogauto import BlogAutoReader
from dealbot.dashboard.service import SECURITY_HEADERS, Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.storage.db import Database
from dealbot.web import FORM_TYPE, WebServer

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
USER, PASSWORD = "owner", "plum-river-cactus-42"
APPROVED_ICONS = {  # 주인이 고른 아이콘 파일 그대로 (바꾸면 여기도 같이 — 실수로 다른 그림이 나가지 않게)
    "apple-touch-icon.png": "0bfc7dbf429268ade42345774e80d115cf165bd4080bc4b64aad34976b057c13",
    "favicon-64.png": "f8ea7248ea1e0b3cad90305126d123e374d21d303d7196230ef3f287d62646e7",
    "icon-192.png": "2c1f620f432e0eccc324a880e4e49abf4e09a3065e33f9150011b63793a98de8",
    "icon-512.png": "689c3d6b9a51da30b0a45022966a7d79ebc2d1db156792d90ca44d6e5557fabc",
    "icon-maskable-512.png": "520759c2e3ded4b65b184c17641d9be12873102b232720d9e32861edfb640944",
    "icon.svg": "eb027b384d1239501ce68e3d37deff8d0b2b6470be7e3ea951c3e69204aef87d",
}
SESSION_FLAGS = "; Max-Age=2592000; Path=/dashboard; Secure; HttpOnly; SameSite=Lax"  # 10/18: 카톡·알림 링크로 열 때도 실리게


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Tick:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def make(db: Database, tmp_path: Path, *, user: str = USER, password: str = PASSWORD, clock: Clock | None = None,
         title: str = "채널 대시보드") -> tuple[Dashboard, WebServer]:
    clock = clock or Clock()
    cfg = DashboardConfig(title=title, cards=[DashboardCardConfig(key="kakao", type="kakao_openchat")],
                          blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    ctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=clock,
                        blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=clock))
    dash = Dashboard(cfg, ctx, user=user, password=password, public_base_url="https://1-2-3-4.sslip.io")
    srv = WebServer(0, host="127.0.0.1")
    assert dash.register(srv)
    return dash, srv


def frm(ip: str = "203.0.113.5", **more: str) -> dict[str, str]:
    """Caddy 뒤 요청 헤더 (접속은 127.0.0.1, 실제 주소는 X-Forwarded-For)."""
    return {":peer": "127.0.0.1", "x-forwarded-for": ip, **more}


def set_cookie(res: Any) -> str:
    return res[3].get("Set-Cookie", "") if len(res) >= 4 and res[3] else ""


def cookie_value(header: str, name: str) -> str | None:
    m = re.match(rf"^{name}=([^;]*);", header)
    return m.group(1) if m else None


async def open_login(srv: WebServer, ip: str = "203.0.113.5") -> tuple[str, str]:
    """로그인 화면을 열어 (CSRF 쿠키 난수, 폼의 t)."""
    res = await srv.dispatch("GET", "/dashboard/login", frm(ip))
    assert res[0] == 200, res[:2]
    nonce = cookie_value(set_cookie(res), "dash_login")
    field = re.search(r'name="t" value="([^"]+)"', res[1]).group(1)  # type: ignore[union-attr, arg-type]
    assert nonce
    return nonce, field


async def post_login(srv: WebServer, *, ip: str = "203.0.113.5", user: str = USER, password: str = PASSWORD,
                     nonce: str | None = None, field: str | None = None, extra: dict[str, str] | None = None) -> Any:
    if nonce is None or field is None:
        got_nonce, got_field = await open_login(srv, ip)
        nonce = got_nonce if nonce is None else nonce
        field = got_field if field is None else field
    body = urlencode({"t": field, "user": user, "password": password}).encode()
    headers = frm(ip, **{"content-type": FORM_TYPE, "content-length": str(len(body)), "cookie": f"dash_login={nonce}"})
    headers.update(extra or {})
    return await srv.dispatch("POST", "/dashboard/login", headers, body)


async def login(srv: WebServer, ip: str = "203.0.113.5", password: str = PASSWORD) -> str:
    res = await post_login(srv, ip=ip, password=password)
    assert res[:2] == (303, "/dashboard/"), res[:2]
    token = cookie_value(set_cookie(res), "dash_session")
    assert token
    return token


def with_session(token: str, ip: str = "203.0.113.5", **more: str) -> dict[str, str]:
    return frm(ip, cookie=f"dash_session={token}", **more)


def sessions_in_db(db: Database) -> dict[str, dict[str, Any]]:
    rows = db._q("SELECT key, value FROM kv WHERE key LIKE 'dash:session:%'")
    return {r["key"]: json.loads(r["value"]) for r in rows}


def all_kv_text(db: Database) -> str:
    return "\n".join(f"{r['key']}={r['value']}" for r in db._q("SELECT key, value FROM kv"))


# ---------------------------------------------------------------- 매니페스트·아이콘
async def test_manifest_and_icons_are_public_with_right_types_and_long_cache(db: Database, tmp_path: Path) -> None:
    _, srv = make(db, tmp_path)
    await srv.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:  # 로그인 없이 (휴대폰이 받아 감)
            r = await c.get("/dashboard/manifest.webmanifest")
            assert r.status_code == 200 and r.headers["content-type"] == "application/manifest+json; charset=utf-8"
            assert r.headers["cache-control"] == "public, max-age=86400" and r.headers["x-content-type-options"] == "nosniff"
            assert "set-cookie" not in r.headers
            m = r.json()
            assert (m["name"], m["short_name"]) == ("Sns-board", "Sns-board")  # 주인이 고른 표기 그대로
            assert (m["start_url"], m["scope"], m["id"], m["display"]) == ("/dashboard", "/dashboard", "/dashboard", "standalone")
            assert m["background_color"] == m["theme_color"] == "#f4f5f7" and "--bg:#f4f5f7" in render.CSS  # 화면 바탕과 같은 색
            assert [(i["sizes"], i["purpose"], i["type"]) for i in m["icons"]] == [
                ("192x192", "any", "image/png"), ("512x512", "any", "image/png"), ("512x512", "maskable", "image/png")]
            for icon in m["icons"]:
                r = await c.get(icon["src"])
                assert r.status_code == 200 and r.headers["content-type"] == "image/png", icon
                assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
                assert "set-cookie" not in r.headers
                img = Image.open(io.BytesIO(r.content))
                size = int(icon["sizes"].split("x")[0])
                assert img.size == (size, size)
                corner = img.convert("RGBA").getpixel((0, 0))
                if icon["purpose"] == "maskable":
                    assert corner[3] == 255  # 가장자리까지 꽉 참 (휴대폰이 동그랗게·네모나게 잘라 씀)
                else:
                    assert corner[3] == 0  # 둥근 모서리 바깥은 투명
            for name, (ctype, size) in {"apple-touch-icon.png": ("image/png", 180), "favicon-64.png": ("image/png", 64)}.items():
                r = await c.get(webapp.icon_url(name))
                assert r.status_code == 200 and r.headers["content-type"] == ctype
                assert Image.open(io.BytesIO(r.content)).size == (size, size)
            r = await c.get(webapp.icon_url("icon.svg"))
            assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml"
            assert "sandbox" in r.headers["content-security-policy"] and "default-src 'none'" in r.headers["content-security-policy"]
            assert r.content.startswith(b"<svg")
            # 페이지 쪽은 그대로 로그인 필요
            assert (await c.get("/dashboard/data.json")).status_code == 401
    finally:
        await srv.stop()


def test_icons_are_exactly_the_owner_approved_files() -> None:
    assert set(webapp.STATIC_TYPES) == set(APPROVED_ICONS)
    for name, digest in APPROVED_ICONS.items():
        data = (webapp.STATIC_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest, name
        assert webapp.icon_url(name) == f"/dashboard/static/{name}?v={digest[:10]}"  # 바꾸면 주소도 바뀜 (1년 캐시)
    assert webapp.static_response("nope.png")[0] == 404
    # Docker(-e 없이 설치)에서도 아이콘이 들어가게
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"dealbot.dashboard" = ["static/*"]' in pyproject


async def test_only_the_exact_public_paths_skip_login(db: Database, tmp_path: Path) -> None:
    _, srv = make(db, tmp_path)
    for path in ("/dashboard/static/nope.png", "/dashboard/static/icon-192.PNG", "/dashboard/static/", "/dashboard/static",
                 "/dashboard/manifest.webmanifest/x", "/dashboard/static/../data.json", "/dashboard/static/icon-192.png/",
                 "/dashboard/data.json", "/dashboard/nope", "/dashboard/login/x", "/dashboard/logout/x"):
        res = await srv.dispatch("GET", path, frm())
        assert res[0] == 401, path
    assert (await srv.dispatch("GET", "/dashboard/static/icon-192.png?v=whatever", frm()))[0] == 200  # 쿼리는 상관없음
    assert (await srv.dispatch("GET", "/dashboard/logout", frm()))[0] == 405  # 로그아웃은 POST 만
    assert (await srv.dispatch("POST", "/dashboard/manifest.webmanifest", frm()))[0] == 405

    # 아이디·비밀번호가 없으면(대시보드 꺼짐) 공개 경로까지 모두 404
    off = Dashboard(DashboardConfig(), SourceContext(db=db, tz="Asia/Seoul", config=DashboardConfig(), now=Clock()))
    srv2 = WebServer(0)
    assert not off.register(srv2)
    for path in ("/dashboard/manifest.webmanifest", "/dashboard/static/icon-192.png", "/dashboard/login"):
        assert (await srv2.dispatch("GET", path, {}))[0] == 404, path


async def test_guard_public_paths_must_sit_under_the_prefix() -> None:
    srv = WebServer(0)

    async def secret(query: dict[str, str]) -> Any:
        return 200, "secret"

    srv.route("/dashboard", secret)
    srv.route("/dashboard/x", secret)
    srv.guard_prefix("/dashboard", lambda h: False, public=["/dashboard", "/other", "dashboard/x", "/dashboardx/y"])
    assert srv.guard_public["/dashboard"] == frozenset()  # 앞부분 자체·바깥 경로·모양이 다른 경로는 공개 목록에 안 들어감
    assert (await srv.dispatch("GET", "/dashboard/", {}))[0] == 401
    assert (await srv.dispatch("GET", "/dashboard/x", {}))[0] == 401
    srv.guard_prefix("/dashboard", lambda h: False, public=["/dashboard/x"], on_denied=lambda m, p, h: None)
    assert (await srv.dispatch("GET", "/dashboard/x", {}))[:2] == (200, "secret")
    assert (await srv.dispatch("GET", "/dashboard/", {}))[0] == 401  # on_denied 가 None 이면 그대로 401
    srv.guard_prefix("/dashboard", None, public=["/dashboard/x"])  # 꺼지면 공개 경로도 404
    assert (await srv.dispatch("GET", "/dashboard/x", {}))[0] == 404


# ---------------------------------------------------------------- <head>·CSP
async def test_page_and_login_have_app_head_tags_and_a_minimal_csp(db: Database, tmp_path: Path) -> None:
    dash, srv = make(db, tmp_path, title="<b>채널</b> 대시보드")
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert csp == ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                   "connect-src 'self'; manifest-src 'self'; worker-src 'self'; form-action 'self'; base-uri 'none'; "
                   "frame-ancestors 'none'")  # 10/18 앱: 스크립트는 이 서버 파일만 (페이지 안 스크립트 없음)
    assert "unsafe-eval" not in csp and "script-src 'self';" in csp
    page = await srv.dispatch("GET", "/dashboard/", frm(authorization=basic(USER, PASSWORD)))
    login = await srv.dispatch("GET", "/dashboard/login", frm())
    for res in (page, login):
        status, body, ctype, headers = res
        assert status == 200 and ctype.startswith("text/html") and headers["Content-Security-Policy"] == csp
        assert headers["X-Frame-Options"] == "DENY" and headers["Referrer-Policy"] == "no-referrer"
        head = body.split("</head>", 1)[0]
        assert head.count("<title>") == 1 and "<title>Sns-board</title>" in head
        for tag in ('<link rel="manifest" href="/dashboard/manifest.webmanifest" crossorigin="use-credentials">',
                    '<meta name="application-name" content="Sns-board">',
                    '<meta name="apple-mobile-web-app-title" content="Sns-board">',
                    '<meta name="apple-mobile-web-app-capable" content="yes">',
                    '<meta name="mobile-web-app-capable" content="yes">',
                    '<meta name="apple-mobile-web-app-status-bar-style" content="default">',
                    '<meta name="theme-color" media="(prefers-color-scheme: light)" content="#f4f5f7">',
                    '<meta name="theme-color" media="(prefers-color-scheme: dark)" content="#151a2d">',
                    f'<link rel="apple-touch-icon" href="{webapp.icon_url("apple-touch-icon.png")}" sizes="180x180">',
                    f'<link rel="icon" href="{webapp.icon_url("favicon-64.png")}" type="image/png" sizes="64x64">'):
            assert tag in head, tag
        assert "<script" not in body.lower() and "<b>" not in body
    # 화면 맨 위 이름은 설정 그대로(이스케이프), 머리 오른쪽에 로그아웃 (폼 토큰과 함께 POST)
    assert "<h1>&lt;b&gt;채널&lt;/b&gt; 대시보드</h1>" in page[1]
    assert (f'<form class="logout" method="post" action="/dashboard/logout"><input type="hidden" name="t" '
            f'value="{dash.form_token}"><button type="submit">로그아웃</button></form>') in page[1]
    # 로그인 화면: 비밀번호 칸·자동 채우기 이름표·16px(아이폰이 확대하지 않게)·아이콘은 같은 서버에서
    body = login[1]
    assert 'autocomplete="username"' in body and 'type="password" autocomplete="current-password"' in body
    assert 'action="/dashboard/login"' in body and "font-size:16px" in body
    assert f'<img src="{webapp.icon_url("icon-192.png")}"' in body and "&lt;b&gt;채널&lt;/b&gt; 대시보드" in body
    assert PASSWORD not in body and USER not in body.replace("username", "")


# ---------------------------------------------------------------- 로그인
async def test_login_sets_a_strict_session_cookie_and_stores_only_its_hash(db: Database, tmp_path: Path,
                                                                         caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    dash, srv = make(db, tmp_path)
    res = await srv.dispatch("GET", "/dashboard/login", frm())
    nonce = cookie_value(set_cookie(res), "dash_login")
    assert nonce and len(nonce) >= 43
    assert set_cookie(res) == f"dash_login={nonce}; Max-Age=7200; Path=/dashboard/login; Secure; HttpOnly; SameSite=Strict"
    res = await post_login(srv)
    assert res[:2] == (303, "/dashboard/")
    header = set_cookie(res)
    token = cookie_value(header, "dash_session")
    assert token and header == f"dash_session={token}{SESSION_FLAGS}"
    assert len(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))) >= 32  # 32바이트 이상 난수
    rows = sessions_in_db(db)
    assert list(rows) == [f"dash:session:{hashlib.sha256(token.encode()).hexdigest()}"]  # DB 에는 SHA-256 만
    record = next(iter(rows.values()))
    assert record["exp"] == (NOW + timedelta(days=30)).isoformat() and len(record["fp"]) == 32
    dump = all_kv_text(db)
    assert token not in dump and PASSWORD not in dump and nonce not in dump
    for secret in (token, PASSWORD, nonce):
        assert secret not in caplog.text
    assert "로그인 — 이 기기는 30일 동안 로그인 유지" in caplog.text

    # 세션 쿠키로: 화면·JSON·다시 읽기·링크 넣기(POST) 모두 통과, 로그인 화면은 바로 대시보드로
    page = await srv.dispatch("GET", "/dashboard/", with_session(token))
    assert page[0] == 200 and "카카오 오픈채팅" in page[1]
    assert (await srv.dispatch("GET", "/dashboard/data.json", with_session(token)))[0] == 200
    assert (await srv.dispatch("GET", f"/dashboard/refresh?src=kakao&t={dash.form_token}", with_session(token)))[0] == 302
    assert (await srv.dispatch("GET", "/dashboard/login", with_session(token)))[:2] == (303, "/dashboard/")
    body = urlencode({"t": dash.form_token, "q": "7", "url": "https://link.coupang.com/a/x"}).encode()
    res = await srv.dispatch("POST", "/dashboard/link", with_session(
        token, **{"content-type": FORM_TYPE, "content-length": str(len(body))}), body)
    assert res[:2] == (303, "/dashboard?msg=link_off&q=7")  # 인증은 통과 (링크 넣기 자체는 이 테스트에선 꺼짐)
    # 같은 이름 쿠키가 여럿이어도 맞는 게 하나 있으면 통과, 모양이 이상한 값은 그냥 로그인 안 한 것
    assert (await srv.dispatch("GET", "/dashboard/", frm(cookie=f"x=1; dash_session=bogus; dash_session={token}")))[0] == 200
    for bad in ("", "bogus", "a" * 43, token[:-1], token + "x", '"' + "b" * 43 + '"', "../../etc", token.upper()):
        res = await srv.dispatch("GET", "/dashboard/", frm(cookie=f"dash_session={bad}"))
        assert res[:2] == (302, "/dashboard/login"), bad


async def test_login_failures_csrf_and_cross_site(db: Database, tmp_path: Path) -> None:
    clock = Clock()
    dash, srv = make(db, tmp_path, clock=clock)
    dash.login_rate = dash_login.AttemptLimiter(per_client=1000)  # 1분 한도는 아래 테스트에서 따로
    res = await post_login(srv, password=PASSWORD + " ")
    assert res[0] == 200 and "아이디나 비밀번호가 맞지 않아요." in res[1] and "dash_session" not in set_cookie(res)
    assert (await post_login(srv, user="Owner"))[0] == 200  # 아이디도 정확히
    assert (await post_login(srv, user=f"  {USER} "))[:2] == (303, "/dashboard/")  # 아이디 앞뒤 빈칸만 지움
    nonce, field = await open_login(srv)
    other_nonce, _ = await open_login(srv)
    for kwargs in ({"field": ""}, {"field": "nope"}, {"nonce": other_nonce, "field": field},  # 다른 화면의 쿠키
                   {"nonce": "", "field": field}, {"nonce": nonce, "field": field.split(".")[0] + ".AAAA"},
                   {"nonce": nonce, "field": str(int(field.split(".")[0]) + 1) + "." + field.split(".")[1]}):
        res = await post_login(srv, nonce=kwargs.get("nonce", nonce), field=kwargs.get("field", field))
        assert res[0] == 403 and "화면이 오래됐어요" in res[1] and "dash_session" not in set_cookie(res), kwargs
        assert cookie_value(set_cookie(res), "dash_login")  # 새 화면(새 난수)을 다시 줌
    assert (await post_login(srv, nonce=nonce, field=field))[0] == 303  # 맞는 짝이면 됨
    # 로그인 화면을 탭 두 개로 열어도(쿠키는 하나) 먼저 연 화면의 폼이 그대로 됨 — 받은 난수 쿠키를 다시 씀
    nonce, first = await open_login(srv)
    again = await srv.dispatch("GET", "/dashboard/login", frm(cookie=f"dash_login={nonce}"))
    assert cookie_value(set_cookie(again), "dash_login") == nonce
    second = re.search(r'name="t" value="([^"]+)"', again[1]).group(1)  # type: ignore[union-attr, arg-type]
    assert (await post_login(srv, nonce=nonce, field=first))[0] == 303
    assert (await post_login(srv, nonce=nonce, field=second))[0] == 303
    odd = await srv.dispatch("GET", "/dashboard/login", frm(cookie="dash_login=short"))  # 모양이 이상한 쿠키는 새로
    assert cookie_value(set_cookie(odd), "dash_login") not in (None, "short")
    nonce, field = await open_login(srv)
    clock.t += timedelta(hours=2, seconds=1)  # 2시간 넘게 열어 둔 화면
    assert (await post_login(srv, nonce=nonce, field=field))[0] == 403
    # 다른 사이트에서 보낸 폼·폼이 아닌 본문은 핸들러까지 가지 않음
    assert (await post_login(srv, extra={"sec-fetch-site": "cross-site"}))[0] == 403
    assert (await post_login(srv, extra={"content-type": "application/json"}))[0] == 415
    assert not any(v.get("fp") is None for v in sessions_in_db(db).values())


async def test_login_rate_limit_and_lockout_backoff(db: Database, tmp_path: Path) -> None:
    dash, srv = make(db, tmp_path)
    tick = Tick()
    dash.login_rate = dash_login.AttemptLimiter(per_client=5, window=60, clock=tick)
    dash.limiter = dash_auth.FailureLimiter(per_client=3, global_cap=100, window=600, clock=tick)
    # 1분에 5번까지 (맞든 틀리든) — 6번째는 429, 다른 곳은 그대로
    for _ in range(5):
        assert (await post_login(srv, ip="198.51.100.9", user="x", password="y"))[0] in (200, 429)
    res = await post_login(srv, ip="198.51.100.9")
    assert res[0] == 429 and "너무 자주 눌렀어요" in res[1] and 1 <= int(res[3]["Retry-After"]) <= 61
    assert "dash_session" not in set_cookie(res)
    tick.t += 61
    dash.limiter = dash_auth.FailureLimiter(per_client=3, global_cap=100, window=600, clock=tick)

    async def fail(ip: str = "203.0.113.7") -> Any:
        tick.t += 15  # 1분 5번 한도에 안 걸리게 띄엄띄엄
        return await post_login(srv, ip=ip, password="guess")

    assert (await fail())[0] == 200 and (await fail())[0] == 200
    res = await fail()  # 세 번째 틀림 → 막힘 (10분)
    assert res[0] == 429 and "막아 두었어요 — 10분 뒤에" in res[1] and 590 <= int(res[3]["Retry-After"]) <= 601
    tick.t += 15
    res = await post_login(srv, ip="203.0.113.7")  # 막힌 동안은 맞는 비밀번호도 확인 안 함
    assert res[0] == 429 and "dash_session" not in set_cookie(res)
    # 기본 인증과 같은 기록 (한쪽에서 막히면 다른 쪽도)
    assert (await srv.dispatch("GET", "/dashboard/", frm("203.0.113.7", authorization=basic(USER, PASSWORD))))[0] == 429
    page = await srv.dispatch("GET", "/dashboard/login", frm("203.0.113.7"))
    assert page[0] == 200 and "막아 두었어요" in page[1]
    assert (await post_login(srv, ip="198.51.100.20"))[0] == 303  # 다른 곳은 그대로
    # 풀린 뒤 또 세 번 틀리면 두 배(20분), 그다음은 40분
    tick.t += 600
    for _ in range(2):
        assert (await fail())[0] == 200
    res = await fail()
    assert res[0] == 429 and "20분" in res[1] and 1190 <= int(res[3]["Retry-After"]) <= 1201
    tick.t += 1201
    for _ in range(2):
        await fail()
    res = await fail()
    assert res[0] == 429 and "40분" in res[1] and 2390 <= int(res[3]["Retry-After"]) <= 2401
    # 맞는 비밀번호로 들어오면 처음부터 (다음 막힘은 다시 10분)
    tick.t += 2401
    assert (await post_login(srv, ip="203.0.113.7"))[0] == 303
    for _ in range(2):
        await fail()
    res = await fail()
    assert res[0] == 429 and 590 <= int(res[3]["Retry-After"]) <= 601


def test_failure_limiter_backoff_caps_and_resets() -> None:
    tick = Tick()
    lim = dash_auth.FailureLimiter(per_client=2, global_cap=1000, window=100, clock=tick, max_lock=350, reset_after=1000)
    waits = []
    for _ in range(4):
        lim.failure("a")
        assert lim.blocked("a") is None
        lim.failure("a")
        wait = lim.blocked("a")
        waits.append(wait)
        tick.t += wait  # type: ignore[operator]
        assert lim.blocked("a") is None
    assert waits == [101, 201, 351, 351]  # 두 배씩, 최대 max_lock
    tick.t += 1001  # 오래 조용하면 처음부터
    lim.failure("a")
    lim.failure("a")
    assert lim.blocked("a") == 101 and lim.blocked("b") is None
    lim.success("a")
    assert lim.blocked("a") is None


# ---------------------------------------------------------------- 세션: 만료·로그아웃·비밀번호 바꾸기
async def test_session_expires_after_30_days(db: Database, tmp_path: Path) -> None:
    clock = Clock()
    _, srv = make(db, tmp_path, clock=clock)
    token = await login(srv)
    clock.t = NOW + timedelta(days=30, minutes=-1)
    assert (await srv.dispatch("GET", "/dashboard/", with_session(token)))[0] == 200
    clock.t = NOW + timedelta(days=30, minutes=1)
    res = await srv.dispatch("GET", "/dashboard/", with_session(token))
    assert res[:2] == (302, "/dashboard/login")
    assert set_cookie(res) == "dash_session=; Max-Age=0; Path=/dashboard; Secure; HttpOnly; SameSite=Lax"  # 지난 쿠키는 지움
    assert sessions_in_db(db) == {}  # 만료된 세션은 DB 에서도 지움
    assert (await srv.dispatch("GET", "/dashboard/data.json", with_session(token)))[0] == 401


async def test_logout_revokes_the_session(db: Database, tmp_path: Path) -> None:
    dash, srv = make(db, tmp_path)
    token = await login(srv)
    keep = await login(srv, ip="198.51.100.30")  # 다른 기기 (로그아웃해도 그대로)
    assert len(sessions_in_db(db)) == 2

    def logout(form: dict[str, str], tok: str, **more: str) -> Any:
        body = urlencode(form).encode()
        return srv.dispatch("POST", "/dashboard/logout", with_session(
            tok, **{"content-type": FORM_TYPE, "content-length": str(len(body)), **more}), body)

    assert (await logout({}, token))[0] == 403  # 폼 토큰 없이는 안 됨
    assert (await logout({"t": dash.form_token}, token, **{"sec-fetch-site": "cross-site"}))[0] == 403
    assert (await srv.dispatch("GET", "/dashboard/", with_session(token)))[0] == 200
    res = await logout({"t": dash.form_token}, token)
    assert res[:2] == (303, "/dashboard/login?msg=out")
    assert set_cookie(res) == "dash_session=; Max-Age=0; Path=/dashboard; Secure; HttpOnly; SameSite=Lax"
    assert len(sessions_in_db(db)) == 1
    assert (await srv.dispatch("GET", "/dashboard/", with_session(token)))[:2] == (302, "/dashboard/login")
    assert (await srv.dispatch("GET", "/dashboard/", with_session(keep, "198.51.100.30")))[0] == 200
    page = await srv.dispatch("GET", "/dashboard/login?msg=out", frm())
    assert "로그아웃했어요." in page[1]
    assert "&lt;" not in (await srv.dispatch("GET", "/dashboard/login?msg=<script>", frm()))[1]  # 정해진 문구만
    # 이미 풀린 쿠키로 눌러도(로그인 없이 열리는 경로) 쿠키만 지우고 로그인 화면으로
    assert (await logout({"t": dash.form_token}, token))[:2] == (303, "/dashboard/login?msg=out")


async def test_changing_the_password_or_user_logs_every_device_out(db: Database, tmp_path: Path) -> None:
    _, srv = make(db, tmp_path)
    token = await login(srv)
    # 재시작(같은 아이디·비밀번호)은 그대로 로그인
    _, same = make(db, tmp_path)
    assert (await same.dispatch("GET", "/dashboard/", with_session(token)))[0] == 200
    assert PASSWORD not in all_kv_text(db)  # 지문만 (scrypt + 소금), 비밀번호는 저장 안 함
    # 서버에서 DASHBOARD_PASSWORD 를 바꾸면 모든 세션이 풀림
    _, changed = make(db, tmp_path, password="new-river-cactus-77")
    assert (await changed.dispatch("GET", "/dashboard/", with_session(token)))[:2] == (302, "/dashboard/login")
    assert sessions_in_db(db) == {}
    _, back = make(db, tmp_path)  # 예전 비밀번호로 되돌려도 지운 세션은 돌아오지 않음
    assert (await back.dispatch("GET", "/dashboard/", with_session(token)))[0] == 302
    token = await login(back)
    _, renamed = make(db, tmp_path, user="owner2")  # 아이디를 바꿔도 마찬가지
    assert (await renamed.dispatch("GET", "/dashboard/", with_session(token)))[0] == 302
    # 새 비밀번호로는 다시 로그인하면 됨
    fresh = await login(changed, password="new-river-cactus-77")
    assert (await changed.dispatch("GET", "/dashboard/", with_session(fresh)))[0] == 200


def test_session_store_purges_expired_and_caps_live_sessions(db: Database) -> None:
    clock = Clock()
    store = dash_login.SessionStore(db, user=USER, password=PASSWORD, now=clock, max_sessions=5)
    old = store.create()
    clock.t += timedelta(days=31)
    tokens = []
    for _ in range(7):
        clock.t += timedelta(minutes=1)
        tokens.append(store.create())
    assert len(sessions_in_db(db)) == 5  # 만료된 것은 지우고, 살아 있는 건 5개까지 (오래된 것부터 지움)
    assert not store.valid(old) and [store.valid(t) for t in tokens] == [False, False, True, True, True, True, True]
    assert store.revoke(tokens[-1]) and not store.valid(tokens[-1]) and not store.revoke(tokens[-1])
    assert not store.revoke("bad token") and not store.valid(None)
    db.kv_set("dash:session:" + hashlib.sha256(b"x" * 43).hexdigest(), "{not json")
    assert not store.valid("x" * 43)


# ---------------------------------------------------------------- 기본 인증은 그대로 · 로그인 화면으로 vs 401
async def test_basic_auth_still_works_and_redirect_vs_401(db: Database, tmp_path: Path) -> None:
    dash, srv = make(db, tmp_path)
    good = frm(authorization=basic(USER, PASSWORD))
    for path in ("/dashboard/", "/dashboard/data.json"):
        assert (await srv.dispatch("GET", path, good))[0] == 200, path
    assert (await srv.dispatch("GET", "/dashboard", good))[:2] == (301, "/dashboard/")  # 앱의 범위가 /dashboard/ 아래
    assert (await srv.dispatch("GET", f"/dashboard/input?src=kakao&members=3&t={dash.form_token}", good))[0] == 302
    assert db.metric_latest("kakao", "members")["value"] == 3
    # 로그인 없이 브라우저로 연 화면 → 로그인 화면 (HEAD 도)
    for method, path in (("GET", "/dashboard/"), ("GET", "/dashboard/"), ("HEAD", "/dashboard"),
                         ("GET", "/dashboard/refresh?src=kakao"), ("GET", "/dashboard/input?src=kakao&members=9")):
        res = await srv.dispatch(method, path, frm())
        assert res[:2] == (302, "/dashboard/login") and "Set-Cookie" not in res[3], (method, path)
    assert db.metric_latest("kakao", "members")["value"] == 3  # 로그인 없이는 아무것도 안 바뀜
    # 기본 인증을 넣었는데 틀리면 그대로 401 (브라우저가 다시 묻게), data.json·POST·없는 경로도 401
    res = await srv.dispatch("GET", "/dashboard/", frm(authorization=basic(USER, "wrong")))
    assert res[0] == 401 and res[3]["WWW-Authenticate"].startswith('Basic realm="dashboard"')
    for path in ("/dashboard/data.json", "/dashboard/nope"):
        res = await srv.dispatch("GET", path, frm())
        assert res[0] == 401 and "WWW-Authenticate" in res[3], path
    body = urlencode({"t": dash.form_token, "q": "1", "url": "https://x.example"}).encode()
    res = await srv.dispatch("POST", "/dashboard/link", frm(**{"content-type": FORM_TYPE, "content-length": str(len(body))}), body)
    assert res[0] == 401
    # 세션 없이 다른 앱에서 연 링크(SameSite=Strict 라 쿠키가 안 실림)면 로그인 화면에 '대시보드 열기' 를 같이
    res = await srv.dispatch("GET", "/dashboard/login", frm(**{"sec-fetch-site": "cross-site"}))
    assert '<a href="/dashboard">대시보드 열기</a>' in res[1]
    assert "대시보드 열기" not in (await srv.dispatch("GET", "/dashboard/login", frm(**{"sec-fetch-site": "same-origin"})))[1]


async def test_full_login_flow_over_real_http(db: Database, tmp_path: Path) -> None:
    dash, srv = make(db, tmp_path)
    await srv.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            r = await c.get("/dashboard/")
            assert r.status_code == 302 and r.headers["location"] == "/dashboard/login"
            r = await c.get("/dashboard/login")
            assert r.status_code == 200 and "default-src 'none'" in r.headers["content-security-policy"]
            assert r.headers["cache-control"] == "no-store"
            nonce = cookie_value(r.headers["set-cookie"], "dash_login")
            field = re.search(r'name="t" value="([^"]+)"', r.text).group(1)  # type: ignore[union-attr]
            # Secure 쿠키라 http 로는 클라이언트가 다시 보내지 않으므로 직접 넣는다 (실제는 Caddy 뒤 https)
            r = await c.post("/dashboard/login", data={"t": field, "user": USER, "password": PASSWORD},
                             headers={"Cookie": f"dash_login={nonce}"})
            assert r.status_code == 303 and r.headers["location"] == "/dashboard/"
            assert len(r.headers.get_list("set-cookie")) == 1
            token = cookie_value(r.headers["set-cookie"], "dash_session")
            assert r.headers["set-cookie"] == f"dash_session={token}{SESSION_FLAGS}"
            c.cookies.clear()
            r = await c.get("/dashboard/", headers={"Cookie": f"dash_session={token}"})
            assert r.status_code == 200 and "<title>Sns-board</title>" in r.text and "로그아웃" in r.text
            r = await c.post("/dashboard/logout", data={"t": dash.form_token}, headers={"Cookie": f"dash_session={token}"})
            assert r.status_code == 303 and r.headers["location"] == "/dashboard/login?msg=out"
            c.cookies.clear()
            r = await c.get("/dashboard/", headers={"Cookie": f"dash_session={token}"})
            assert r.status_code == 302
    finally:
        await srv.stop()


def test_wait_text() -> None:
    assert [dash_login.wait_text(s) for s in (0, 40, 60, 61, 601, 1201, 3600, 4801, 86401)] == [
        "1초", "40초", "1분", "1분", "10분", "20분", "1시간", "1시간 20분", "24시간"]
