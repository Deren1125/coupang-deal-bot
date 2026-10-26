"""두 번째 인스타 '데런픽 룩' @derenpick.look (주인 10/26): 계정별 토큰 · '데런픽 룩 연결' 승인 흐름 · 연결 전 할 일 한 줄.
인스타·메타에는 나가지 않는다 (httpx MockTransport)."""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from dash_helpers import push_settings, session_headers
from dealbot.app import KV_IG_OAUTH_STATE, DealBot
from dealbot.config import Settings
from dealbot.publisher import instagram as ig
from dealbot.publisher.instagram import InstagramClient, InstagramToken
from test_dash_api_actions import act


def test_account_keys_keep_the_old_names_for_the_deal_account() -> None:
    main, look = ig.account_keys("main"), ig.account_keys("look")
    assert (main.token, main.user_id, main.expires, main.username, main.saved_at) == (
        ig.KV_TOKEN, ig.KV_USER_ID, ig.KV_TOKEN_EXPIRES, ig.KV_USERNAME, ig.KV_TOKEN_SAVED_AT)
    assert main.platform == "instagram" and look.platform == "instagram_look"
    assert {look.token, look.user_id, look.expires, look.username, look.saved_at}.isdisjoint(
        {main.token, main.user_id, main.expires, main.username, main.saved_at})
    with pytest.raises(ValueError):
        ig.account_keys("nope")
    assert ig.state_for("main", "abc") == "abc" and ig.state_for("look", "abc") == "look.abc"
    assert ig.account_of_state("look.abc") == "look" and ig.account_of_state("abc") == "main"
    assert ig.account_of_state("main.abc") == "main" and ig.account_of_state("evil.abc") == "main" and ig.account_of_state(None) == "main"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.instagram_app_id = "IGAPP"
    settings.secrets.instagram_app_secret = "IGSECRET"
    settings.secrets.public_domain = "bot.example.com"
    settings.secrets.instagram_redirect_uri = "https://bot.example.com/instagram/callback"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _fake_meta(bot: DealBot, users: dict[str, tuple[str, str]]) -> list[str]:
    """code → (user_id, username). 부른 code 를 돌려줌."""
    seen: list[str] = []
    last: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "api.instagram.com":
            code = parse_qs(req.content.decode())["code"][0]
            seen.append(code)
            last["code"] = code
            return httpx.Response(200, json={"access_token": f"S-{code}", "user_id": 1})
        if req.url.path == "/access_token":
            return httpx.Response(200, json={"access_token": f"L-{last['code']}", "expires_in": 5184000})
        if req.url.path.endswith("/me"):
            uid, name = users[last["code"]]
            return httpx.Response(200, json={"user_id": uid, "username": name})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="IGAPP", app_secret="IGSECRET",
                             retry_backoff=0.01)
    bot.instagram.client = client
    bot.instagram_look.client = client
    return seen


async def _start(bot: DealBot, platform: str) -> str:
    res = await bot.web.dispatch("GET", f"/dashboard/connect/{platform}", session_headers(bot))
    assert res[0] == 302, res
    url = res[1]
    assert url.startswith("https://www.instagram.com/oauth/authorize")
    q = parse_qs(urlsplit(url).query)
    assert q["redirect_uri"] == ["https://bot.example.com/instagram/callback"] and q["force_authentication"] == ["1"]
    return q["state"][0]


async def test_look_button_connects_the_second_account_without_touching_the_deal_account(bot: DealBot) -> None:
    _fake_meta(bot, {"MAIN": ("178", "oneul_hot_deal"), "LOOK": ("901", "derenpick.look")})
    bot.instagram.save_token(InstagramToken("DEAL-TOKEN", "178"), "oneul_hot_deal")
    state = await _start(bot, "instagram_look")
    assert state.startswith("look.") and bot.db.kv_get(KV_IG_OAUTH_STATE + "_look") == state
    # 딜 계정 링크의 state 로는 룩 계정에 들어가지 않음 (계정마다 따로 기억)
    assert (await bot.web.dispatch("GET", f"/instagram/callback?code=LOOK&state={state.split('.', 1)[1]}"))[0] == 400
    status, body = await bot.web.dispatch("GET", f"/instagram/callback?code=LOOK&state={state}")
    assert status == 200 and "데런픽 룩 인스타 연결 완료" in body and "@derenpick.look" in body and "대시보드" in body
    look = ig.account_keys("look")
    assert bot.db.kv_get(look.token) == "L-LOOK" and bot.db.kv_get(look.user_id) == "901"
    assert bot.db.kv_get(look.username) == "derenpick.look"
    assert bot.db.kv_get(ig.KV_TOKEN) == "DEAL-TOKEN" and bot.db.kv_get(ig.KV_USER_ID) == "178"  # 딜 계정은 그대로
    tok = bot.instagram_look.stored_token()
    assert tok is not None and tok.user_id == "901" and bot.instagram.stored_token().user_id == "178"  # type: ignore[union-attr]
    # 한 번 쓴 state 는 버림
    assert (await bot.web.dispatch("GET", f"/instagram/callback?code=LOOK&state={state}"))[0] == 400
    # 딜 계정 다시 연결은 예전 흐름 그대로 (state 에 표시 없음) — 룩 토큰은 그대로
    state2 = await _start(bot, "instagram")
    assert "." not in state2
    status, body = await bot.web.dispatch("GET", f"/instagram/callback?code=MAIN&state={state2}")
    assert status == 200 and "인스타그램 연결 완료: @oneul_hot_deal" in body
    assert bot.db.kv_get(look.token) == "L-LOOK" and bot.db.kv_get(ig.KV_TOKEN) == "L-MAIN"


async def test_approving_with_the_deal_account_does_not_connect_it_as_look(bot: DealBot) -> None:
    """브라우저에 딜 계정이 로그인돼 있어 그 계정으로 승인하면 받지 않는다 (패션 글이 딜 계정에 올라가지 않게)."""
    _fake_meta(bot, {"OOPS": ("178", "oneul_hot_deal"), "BACK": ("901", "derenpick.look")})
    bot.instagram.save_token(InstagramToken("DEAL-TOKEN", "178"), "oneul_hot_deal")
    state = await _start(bot, "instagram_look")
    status, body = await bot.web.dispatch("GET", f"/instagram/callback?code=OOPS&state={state}")
    assert status == 400 and "이미" in body and "데미얌의 쇼핑일기" in body and "derenpick.look" in body
    assert bot.instagram_look.stored_token() is None and bot.db.kv_get(ig.KV_TOKEN) == "DEAL-TOKEN"
    # 거꾸로: 룩이 연결된 뒤 딜 계정 칸을 룩 계정으로 승인해도 안 받음
    bot.instagram_look.save_token(InstagramToken("LOOK-TOKEN", "901"), "derenpick.look")
    msg = await bot.instagram_submit_code("BACK")
    assert msg.startswith("❌") and bot.db.kv_get(ig.KV_TOKEN) == "DEAL-TOKEN"


async def test_dashboard_shows_the_look_row_and_one_todo_until_connected(bot: DealBot) -> None:
    sns = bot.dash_state.sns()  # type: ignore[union-attr]
    row = sns["instagram_look"]
    assert row["state"] == "broken" and row["fix"] == "connect" and row["fix_label"] == "데런픽 룩 연결"
    assert row["connect_url"] == "/dashboard/connect/instagram_look" and "테스터" in row["text"]
    assert sns["instagram"]["fix_label"] == "다시 연결"
    assert any(a["action"] == "sns.code" and a["args"] == {"sns": "instagram_look"} for a in sns["actions"])
    items = [it for it in bot.dash_state.items() if it["iid"] == "sns-look"]  # type: ignore[union-attr]
    assert len(items) == 1 and items[0]["title"] == "데런픽 룩 인스타 연결 필요" and not items[0]["urgent"]
    assert items[0]["primary"]["goto"] == "#/connect"
    assert not any(n.key.startswith("sns") for n in bot.router.store.open_rows(time.time()))  # type: ignore[union-attr]  # 알림 줄 없음
    bot.instagram_look.save_token(InstagramToken("LOOK-TOKEN", "901"), "derenpick.look")
    sns = bot.dash_state.sns()  # type: ignore[union-attr]
    assert sns["instagram_look"]["state"] == "ok" and "@derenpick.look" in sns["instagram_look"]["text"]
    assert not any(it["iid"] == "sns-look" for it in bot.dash_state.items())  # type: ignore[union-attr]
    # 룩을 끄면 줄도 할 일도 없음
    bot.settings.instagram.look.enabled = False
    bot.instagram_look.db.kv_set(ig.account_keys("look").token, "")
    assert bot.dash_state.sns()["instagram_look"]["state"] == "off"  # type: ignore[union-attr]
    assert not any(it["iid"] == "sns-look" for it in bot.dash_state.items())  # type: ignore[union-attr]


async def test_code_and_token_fallbacks_go_to_the_look_account(bot: DealBot) -> None:
    calls: list = []

    async def code(c: str, account: str = "main") -> str:
        calls.append(("code", c, account))
        return "✅ 연결"

    async def token(t: str, account: str = "main") -> str:
        calls.append(("token", t, account))
        return "✅ 연결"

    bot.instagram_submit_code = code  # type: ignore[method-assign]
    bot.instagram_submit_token = token  # type: ignore[method-assign]
    _s, b = await act(bot, "sns.code", {"sns": "instagram_look", "code": "C1"})
    assert b["code"] == "connected"
    _s, b = await act(bot, "sns.token", {"sns": "instagram_look", "token": "T1"})
    _s, b = await act(bot, "sns.code", {"sns": "instagram", "code": "C2"})
    assert calls == [("code", "C1", "look"), ("token", "T1", "look"), ("code", "C2", "main")]


def test_connect_tab_draws_the_look_button() -> None:
    js = (Path(__file__).resolve().parents[1] / "src/dealbot/dashboard/static/app.js").read_text(encoding="utf-8")
    assert "conn('데런픽 룩 인스타', sns.instagram_look, 1)" in js and "x.fix_label || '다시 연결'" in js
