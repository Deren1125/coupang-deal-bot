"""채널 대시보드 웹: 기본 인증(401·상수 시간 비교·설정 없으면 404), 화면 값 이스케이프(XSS), 폼 토큰, 돌아가기 주소."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dealbot.app import DealBot
from dealbot.config import DashboardCardConfig, DashboardConfig, Settings
from dealbot.dashboard import auth as dash_auth
from dealbot.dashboard.blogauto import BlogAutoReader
from dealbot.dashboard.service import Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.publisher import instagram as ig
from dealbot.storage.db import Database
from dealbot.web import WebServer

NOW = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)
USER, PASSWORD = "owner", "pw-Ω-123"


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


GOOD = {"authorization": basic(USER, PASSWORD)}


class BadBot:
    def __init__(self, message: str) -> None:
        self.message = message

    async def get_chat_member_count(self, chat_id: Any) -> int:
        raise RuntimeError(self.message)


def make_dash(db: Database, tmp_path: Path, cards: list[dict[str, Any]], **ctx: Any) -> Dashboard:
    cfg = DashboardConfig(cards=[DashboardCardConfig(**c) for c in cards], blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    sctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=lambda: NOW,
                         blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=lambda: NOW), **ctx)
    return Dashboard(cfg, sctx, user=USER, password=PASSWORD, public_base_url="https://1-2-3-4.sslip.io")


# ---------------------------------------------------------------- 인증
def test_basic_auth_check_and_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []
    real = dash_auth.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(dash_auth.hmac, "compare_digest", spy)
    assert dash_auth.check_basic(basic(USER, PASSWORD), USER, PASSWORD)
    assert len(seen) == 2 and all(len(a) == len(b) == 32 for a, b in seen)  # 길이가 드러나지 않게 해시끼리
    seen.clear()
    assert not dash_auth.check_basic(basic("intruder", PASSWORD), USER, PASSWORD)
    assert len(seen) == 2  # 아이디가 틀려도 비밀번호까지 비교 (앞에서 끊지 않음)
    for header in (None, "", "Bearer x", "Basic", "Basic !!!", "Basic " + base64.b64encode(b"nocolon").decode(),
                   basic(USER, "wrong"), basic(USER.upper(), PASSWORD), basic(USER, PASSWORD + " ")):
        assert not dash_auth.check_basic(header, USER, PASSWORD), header
    assert not dash_auth.check_basic(basic("", ""), "", "")  # 설정이 비어 있으면 늘 거절
    assert dash_auth.check_basic("basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode(), USER, PASSWORD)


async def test_dashboard_routes_require_auth_over_http(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, [{"key": "kakao", "type": "kakao_openchat"}])
    srv = WebServer(0, host="127.0.0.1")
    assert dash.register(srv)
    await srv.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            for path in ("/dashboard", "/dashboard/data.json", "/dashboard/refresh?src=kakao", "/dashboard/input?src=kakao&members=1"):
                r = await c.get(path)
                assert r.status_code == 401 and r.headers["www-authenticate"].startswith('Basic realm="dashboard"'), path
                assert PASSWORD not in r.text
                r = await c.get(path, auth=(USER, "wrong"))
                assert r.status_code == 401, path
            assert db.metric_latest("kakao", "members") is None  # 인증 없이는 아무것도 안 바뀜
            r = await c.get("/dashboard", auth=(USER, PASSWORD))
            assert r.status_code == 200 and "text/html" in r.headers["content-type"]
            assert "카카오 오픈채팅" in r.text and "채널 대시보드" in r.text
            assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-frame-options"] == "DENY"
            assert r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
            assert "<script" not in r.text.lower()
            r = await c.get("/dashboard/data.json", auth=(USER, PASSWORD))
            assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
            assert r.json()["cards"][0]["key"] == "kakao"
            # 입력: 폼 토큰이 있어야 하고, 저장 뒤 같은 서버의 카드로 돌아감
            r = await c.get("/dashboard/input", params={"src": "kakao", "members": "57"}, auth=(USER, PASSWORD))
            assert r.status_code == 403 and db.metric_latest("kakao", "members") is None
            r = await c.get("/dashboard/input", params={"src": "kakao", "members": "57", "t": dash.form_token}, auth=(USER, PASSWORD))
            assert r.status_code == 302 and r.headers["location"] == "/dashboard?msg=saved&src=kakao#card-kakao"
            assert db.metric_latest("kakao", "members")["value"] == 57
            r = await c.get(r.headers["location"], auth=(USER, PASSWORD))
            assert "카카오 오픈채팅: 저장했어요." in r.text
    finally:
        await srv.stop()


async def test_dashboard_is_404_when_credentials_are_unset(db: Database, tmp_path: Path) -> None:
    cfg = DashboardConfig(cards=[DashboardCardConfig(key="kakao", type="kakao_openchat")])
    ctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=lambda: NOW)
    for user, password in ((None, None), (USER, None), (None, PASSWORD), ("", "")):
        dash = Dashboard(cfg, ctx, user=user, password=password)
        srv = WebServer(0)
        assert not dash.register(srv) and not dash.served
        for path in ("/dashboard", "/dashboard/data.json", "/dashboard/input"):
            assert (await srv.dispatch("GET", path, GOOD))[0] == 404
        assert dash.self_check()[-1] == (None, "채널 대시보드 페이지: 꺼짐 (DASHBOARD_USER·DASHBOARD_PASSWORD 미설정 — 숫자는 계속 모음)")


async def test_dealbot_wires_dashboard_routes_and_self_check(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.collectors = []
    bot = DealBot(settings)
    try:
        assert (await bot.web.dispatch("GET", "/dashboard", GOOD))[0] == 404
        lines = await bot.self_check()
        assert (None, "채널 대시보드 페이지: 꺼짐 (DASHBOARD_USER·DASHBOARD_PASSWORD 미설정 — 숫자는 계속 모음)") in lines
        assert bot.dashboard is not None and [s.key for s in bot.dashboard.sources][:3] == ["telegram", "instagram", "threads"]
    finally:
        await bot.close()

    settings.secrets.dashboard_user, settings.secrets.dashboard_password = USER, PASSWORD
    settings.secrets.public_domain = "52-79-136-64.sslip.io"
    bot = DealBot(settings)
    try:
        assert (await bot.web.dispatch("GET", "/dashboard"))[0] == 401
        res = await bot.web.dispatch("GET", "/dashboard", GOOD)
        assert res[0] == 200 and "텔레그램 채널" in res[1] and "Deren의 아카이브" in res[1]  # type: ignore[operator]
        lines = await bot.self_check()
        assert (True, "채널 대시보드 https://52-79-136-64.sslip.io/dashboard (카드 9개, 비밀번호 보호)") in lines
        assert not any(PASSWORD in text for _, text in lines)
        # 다른 경로는 그대로 (인증 없이)
        assert (await bot.web.dispatch("GET", "/health"))[:2] == (200, "ok")
    finally:
        await bot.close()


async def test_load_settings_reads_dashboard_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.config import load_settings

    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path))
    assert not load_settings(root / "config.yaml", load_env=False).secrets.has_dashboard_auth
    monkeypatch.setenv("DASHBOARD_USER", "owner")
    assert not load_settings(root / "config.yaml", load_env=False).secrets.has_dashboard_auth
    monkeypatch.setenv("DASHBOARD_PASSWORD", "'secret'")
    s = load_settings(root / "config.yaml", load_env=False)
    assert s.secrets.has_dashboard_auth and s.secrets.dashboard_password == "secret"


# ---------------------------------------------------------------- XSS·비밀값
async def test_every_value_is_escaped_and_no_tokens_leak(db: Database, tmp_path: Path) -> None:
    evil = "<script>alert(1)</script>"
    db.kv_set(ig.KV_TOKEN, "IG-SECRET-TOKEN-XYZ")
    db.kv_set(ig.KV_USER_ID, "17841")
    (tmp_path / "blogauto.json").write_text(json.dumps({"written_at": "2026-10-14T18:00:00+09:00", "blogs": {
        "finance": {"posts_published_today": "<img src=x onerror=alert(2)>", "posts_published_total": 3}}}), encoding="utf-8")
    dash = make_dash(db, tmp_path, [
        {"key": "telegram", "type": "telegram", "title": evil},
        {"key": "blog", "type": "naver_blog", "title": "A&B \"blog\"", "options": {"blog": "finance", "open_date": "<b>"}},
        {"key": "kakao", "type": "kakao_openchat", "title": "<img src=x onerror=alert(3)>"},
    ], telegram_bot=lambda: BadBot('<img src=x onerror=alert(4)> "quoted"'), channel_id="@c")
    assert await dash.refresh("telegram") == "failed"
    status, body, ctype, headers = await dash._page({"msg": "<script>", "src": "<script>"})
    assert status == 200 and ctype.startswith("text/html")
    for raw in (evil, "<img src=x", 'onerror=alert(4)> "quoted"', "IG-SECRET-TOKEN-XYZ", PASSWORD):
        assert raw not in body, raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;img src=x onerror=alert(4)&gt; &quot;quoted&quot;" in body  # 오류 글은 보이되 이스케이프
    assert "A&amp;B &quot;blog&quot;" in body
    assert body.lower().count("<script") == 0
    _, data, jtype, _ = await dash._data({})
    assert jtype.startswith("application/json") and "IG-SECRET-TOKEN-XYZ" not in data and PASSWORD not in data
    parsed = json.loads(data)
    assert parsed["cards"][0]["title"] == evil  # JSON 은 값 그대로 (application/json + nosniff 로만 나감)
    assert "IG-SECRET-TOKEN-XYZ" not in json.dumps({k: db.kv_get(k) for k in ("dash:status:telegram",)})
    # 주소의 msg·src 는 정해진 문구·카드 키만 화면에 나옴
    _, body, _, _ = await dash._page({"msg": "saved", "src": "kakao"})
    assert "&lt;img src=x onerror=alert(3)&gt;: 저장했어요." in body


async def test_refresh_and_input_need_the_form_token_and_redirect_safely(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, [{"key": "kakao", "type": "kakao_openchat"}, {"key": "telegram", "type": "telegram"}])
    tok = dash.form_token
    assert len(tok) >= 20 and db.kv_get("dash:form_token") == tok
    assert make_dash(db, tmp_path, []).form_token == tok  # 재시작해도 같은 토큰 (열어 둔 페이지가 그대로 됨)
    assert (await dash._refresh({"src": "telegram"}))[0] == 403
    assert (await dash._refresh({"src": "telegram", "t": tok + "x"}))[0] == 403
    assert (await dash._input({"src": "kakao", "members": "5", "t": ""}))[0] == 403
    assert await dash._refresh({"src": "telegram", "t": tok}) == (302, "/dashboard?msg=failed&src=telegram#card-telegram")
    assert await dash._refresh({"src": "telegram", "t": tok}) == (302, "/dashboard?msg=too_soon&src=telegram#card-telegram")
    assert await dash._refresh({"src": "//evil.example/x", "t": tok}) == (302, "/dashboard?msg=unknown")
    assert await dash._input({"src": "kakao", "members": "x", "t": tok}) == (302, "/dashboard?msg=bad_number&src=kakao#card-kakao")
    _, body, _, _ = await dash._page({"msg": "too_soon", "src": "telegram"})
    assert "카드마다 10분에 한 번만" in body
    _, body, _, _ = await dash._page({"msg": "bad_date", "src": "kakao"})
    assert "최근 60일 안의 날짜만" in body


async def test_web_server_redirect_and_header_rules() -> None:
    srv = WebServer(0, host="127.0.0.1")

    async def go(query: dict[str, str]) -> Any:
        return 302, query.get("to", "/x"), None, {"X-Test": "a\r\nSet-Cookie: bad", "X-Ok": "yes"}

    srv.route("/go", go)
    await srv.start()
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            r = await c.get("/go", params={"to": "/dashboard?msg=saved#card-x"})
            assert r.status_code == 302 and r.headers["location"] == "/dashboard?msg=saved#card-x"
            assert r.headers["x-ok"] == "yes" and "x-test" not in r.headers and "set-cookie" not in r.headers
            r = await c.get("/go", params={"to": "//evil.example"})
            assert r.status_code == 302 and "location" not in r.headers
    finally:
        await srv.stop()


def test_page_renders_reference_layout(db: Database, tmp_path: Path) -> None:
    """참고 화면 구성: 칩·다시 읽기·읽은 시각·숫자 칸 둘·보라색 계단 그래프·목표 막대·설명·입력 칸."""
    dash = make_dash(db, tmp_path, [
        {"key": "blog", "type": "naver_blog", "title": "Deren의 아카이브", "options": {"blog": "finance"}},
        {"key": "telegram", "type": "telegram", "goals": {"subscribers": 1000}},
    ])
    for i in range(10):
        db.metric_put("telegram", "subscribers", (NOW.date() - timedelta(days=9 - i)).isoformat(), 5 + i, now=NOW)
    db.kv_set("dash:status:telegram", json.dumps({"status": "ok", "read_at": "2026-10-14T09:00:00+00:00"}))
    html_text = dash.page_html()
    assert '<span class="chip unmet">조건 미충족</span>' in html_text
    assert '<span class="chip unknown">판단 불가</span>' in html_text
    assert html_text.count("다시 읽기</a>") == 2 and "읽은 시각 10. 14. 18:00" in html_text
    assert 'stroke="#5b4fd6"' in html_text and "<h3>네이버 애드포스트</h3>" in html_text
    assert "14 / 1,000 명" in html_text and 'style="width:1.5%"' in html_text
    assert 'action="/dashboard/input"' in html_text and 'name="neighbors"' in html_text and 'name="visitors"' in html_text
    assert 'max="2026-10-14" min="2026-08-15"' in html_text  # 미래·60일보다 전 날짜는 고를 수 없음
    assert "@media (max-width:699px)" in html_text and "repeat(3,minmax(0,1fr))" in html_text
