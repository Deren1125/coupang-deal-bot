"""대시보드 앱 받는 파일·보안 (주인 10/18): 종류·크기·EXIF·방향·압축 폭탄·이름·정리·멈춘 업로드 끊기 /
로그인 없이 401·302 · next 열린 주소 막기 · 쿠키 · 기간 늘리기 · CSRF · 다른 사이트 · CSP · 비밀값 · 기록 · 로그아웃."""

from __future__ import annotations

import asyncio
import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image

from dash_helpers import ORIGIN, get_json, post_json, push_settings, session_headers, subscription
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.uploads import UploadError, Uploads
from dealbot.web import StreamTimeout, read_to_file


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def jpeg_with_gps(size: tuple[int, int] = (1600, 1000), orientation: int = 6) -> bytes:
    im = Image.new("RGB", size, (10, 120, 200))
    exif = Image.Exif()
    exif[0x0112] = orientation  # 방향: 90도 돌려서 보기
    exif[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0), 3: "E", 4: (126.0, 58.0, 0.0)}  # GPS
    exif[0x010F] = "SecretPhone"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


async def upload(bot: DealBot, data: bytes, h: dict, *, kind: str = "photo", ctype: str = "image/jpeg") -> tuple[int, dict]:
    path = bot.dashboard.api.uploads.tmp_path()  # type: ignore[union-attr]
    path.write_bytes(data)
    hh = dict(h, **{"content-type": ctype, "x-upload-kind": kind, "content-length": str(len(data))})
    res = await bot.web.dispatch("POST", "/dashboard/api/upload", hh, body_path=path)
    return res[0], json.loads(res[1])


# ---------------------------------------------------------------- 받는 파일
async def test_photo_exif_and_gps_are_gone_and_orientation_applied(bot: DealBot) -> None:
    h = session_headers(bot)
    status, body = await upload(bot, jpeg_with_gps(), h)
    assert status == 200 and len(body["upload_id"]) == 32 and body["thumb"] == f"/dashboard/upload/{body['upload_id']}/thumb"
    assert (body["w"], body["h"]) == (1000, 1600)  # 방향을 먼저 반영 (세로 사진)
    p = bot.dashboard.api.uploads.path(body["upload_id"])  # type: ignore[union-attr]
    assert p is not None and p.name == f"{body['upload_id']}.jpg"
    raw = p.read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        assert not im.getexif() and "icc_profile" not in im.info and "exif" not in im.info
    assert b"SecretPhone" not in raw
    res = await bot.web.dispatch("GET", body["thumb"], h)
    assert res[0] == 200 and res[2] == "image/jpeg" and res[3]["X-Content-Type-Options"] == "nosniff"
    with Image.open(io.BytesIO(res[1])) as t:  # type: ignore[arg-type]
        assert max(t.size) <= 320
    row = bot.db._one("SELECT * FROM dash_audit WHERE action = 'upload'")
    assert row is not None and row["result_code"] == "done"


async def test_png_stays_png_and_webp_becomes_jpeg(bot: DealBot) -> None:
    h = session_headers(bot)
    for fmt, ctype, ext in (("PNG", "image/png", "png"), ("WEBP", "image/webp", "jpg")):
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), (0, 0, 0)).save(buf, fmt)
        status, body = await upload(bot, buf.getvalue(), h, ctype=ctype)
        assert status == 200
        assert bot.dashboard.api.uploads.path(body["upload_id"]).suffix == f".{ext}"  # type: ignore[union-attr]


async def test_type_size_small_and_bomb_are_refused(bot: DealBot, tmp_path: Path) -> None:
    h = session_headers(bot)
    status, body = await upload(bot, jpeg_with_gps(), h, ctype="image/gif")
    assert status == 415 and body["code"] == "bad_upload"
    small = io.BytesIO()
    Image.new("RGB", (200, 200)).save(small, "JPEG")
    status, body = await upload(bot, small.getvalue(), h)
    assert status == 400 and "작아요" in body["text"]
    status, body = await upload(bot, b"not an image at all", h)
    assert status == 400
    up = Uploads(bot.db, tmp_path)
    bomb = io.BytesIO()
    Image.new("1", (9000, 9000)).save(bomb, "PNG")  # 8,100만 화소 — 작은 파일이지만 풀면 큼
    p = up.tmp_path()
    p.write_bytes(bomb.getvalue())
    with pytest.raises(UploadError):
        up.process(p, kind="photo", ctype="image/png", session="s")
    assert not p.exists()
    # 너무 큰 본문은 읽기 전에 413
    hh = dict(h, **{"content-type": "image/jpeg", "x-upload-kind": "photo", "content-length": str(30 * 1024 * 1024)})
    res = await bot.web.dispatch("POST", "/dashboard/api/upload", hh)
    assert res[0] == 413
    hh["x-upload-kind"] = "../../etc"
    assert (await bot.web.dispatch("POST", "/dashboard/api/upload", hh))[0] == 415


async def test_ledger_text_rules(bot: DealBot) -> None:
    h = session_headers(bot)
    status, body = await upload(bot, ("# 정리본\n" + "내용 " * 40).encode(), h, kind="ledger", ctype="text/markdown")
    assert status == 200 and bot.dashboard.api.uploads.path(body["upload_id"]).suffix == ".md"  # type: ignore[union-attr]
    status, body = await upload(bot, "짧음".encode(), h, kind="ledger", ctype="text/plain")
    assert status == 400
    status, body = await upload(bot, b"\xff\xfe" + b"a" * 100, h, kind="ledger", ctype="text/plain")
    assert status == 400


async def test_names_are_server_generated_and_traversal_headers_are_ignored(bot: DealBot) -> None:
    h = session_headers(bot, **{"x-upload-name": "../../../etc/passwd", "content-disposition": 'attachment; filename="../x.sh"'})
    status, body = await upload(bot, jpeg_with_gps(), h)
    files = list((bot.settings.data_dir / "dash_uploads" / "files").iterdir())
    assert status == 200 and [f.name for f in files] == [f"{body['upload_id']}.jpg"]
    for bad in ("../x", "0" * 31, "g" * 32):
        assert (await bot.web.dispatch("GET", f"/dashboard/upload/{bad}/thumb", h))[0] == 404
    assert (await bot.web.dispatch("GET", "/dashboard/thumb/..%2F..%2Fdealbot.db", h))[0] == 404


async def test_upload_gc(bot: DealBot) -> None:
    h = session_headers(bot)
    _s, body = await upload(bot, jpeg_with_gps(), h)
    up = bot.dashboard.api.uploads  # type: ignore[union-attr]
    up.clock = lambda: time.time() + 25 * 3600
    assert up.gc() == 1 and up.path(body["upload_id"]) is None


async def test_slow_upload_trips_the_idle_timeout(tmp_path: Path) -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * 10)  # 10바이트만 오고 멈춤
    with pytest.raises(StreamTimeout):
        await read_to_file(reader, tmp_path / "up", 100, idle=0.05, total=5)
    assert not (tmp_path / "up").exists()


async def test_upload_needs_login_before_any_byte_is_written(bot: DealBot) -> None:
    res = bot.web._precheck("POST", "/dashboard/api/upload", {})
    assert res is not None and res[0] == 401 and json.loads(res[1])["code"] == "login"


# ---------------------------------------------------------------- 보안
NEW_GETS = ("/dashboard/api/state", "/dashboard/api/item/deal-1", "/dashboard/api/actions?ids=", "/dashboard/api/push/key",
            "/dashboard/api/digest/digest-20261018-am", "/dashboard/api/deal/hot", "/dashboard/api/deal/find?q=x",
            "/dashboard/api/stats.html", "/dashboard/upload/" + "0" * 32 + "/thumb", "/dashboard/thumb/x.jpg")
NEW_POSTS = ("/dashboard/api/action", "/dashboard/api/push/subscribe", "/dashboard/api/push/unsubscribe", "/dashboard/api/push/test")


async def test_every_new_route_needs_login(bot: DealBot) -> None:
    for path in NEW_GETS:
        res = await bot.web.dispatch("GET", path, {})
        assert res[0] == 401, path
        if path.startswith("/dashboard/api/"):  # 앱 API 는 JSON (앱이 로그인 시트를 띄움)
            assert json.loads(res[1])["code"] == "login", path
    for path in NEW_POSTS:
        status, body, _ = await post_json(bot, path, {}, {"content-type": "application/json"})
        assert status == 401 and body["code"] == "login", path
    for path in ("/dashboard/go/deal-12", "/dashboard/connect/threads"):
        res = await bot.web.dispatch("GET", path, {})
        assert res[0] == 302 and res[1].startswith("/dashboard/login"), path
    res = await bot.web.dispatch("GET", "/dashboard/go/deal-12", {})
    assert res[1] == "/dashboard/login?next=/dashboard/go/deal-12"


@pytest.mark.parametrize("given", ["//evil.example", "/dashboard/../x", "/dashboard/go/..%5Cx", "/dashboard/go/" + "a" * 65,
                                   "https://evil.example/dashboard", "/dashboard/go/DEAL", "/dashboard\\go", "/dashboard/api/state"])
async def test_next_open_redirect_matrix(bot: DealBot, given: str) -> None:
    from dealbot.dashboard.login import safe_next

    assert safe_next(given) == "/dashboard/"


async def test_login_follows_next_and_go_redirects_to_the_item(bot: DealBot) -> None:
    from urllib.parse import urlencode

    from dealbot.dashboard.login import LOGIN_COOKIE, safe_next

    assert safe_next("/dashboard/go/deal-12") == "/dashboard/go/deal-12"
    res = await bot.web.dispatch("GET", "/dashboard/login?next=/dashboard/go/deal-12", {":peer": "127.0.0.1"})
    assert 'name="next" value="/dashboard/go/deal-12"' in res[1]
    nonce = res[3]["Set-Cookie"].split(";")[0].split("=", 1)[1]
    t = res[1].split('name="t" value="', 1)[1].split('"', 1)[0]
    from dash_helpers import PASSWORD, USER

    body = urlencode({"t": t, "user": USER, "password": PASSWORD, "next": "/dashboard/go/deal-12"}).encode()
    res = await bot.web.dispatch("POST", "/dashboard/login", {":peer": "127.0.0.1", "cookie": f"{LOGIN_COOKIE}={nonce}",
                                                            "content-type": "application/x-www-form-urlencoded",
                                                            "content-length": str(len(body))}, body)
    assert res[:2] == (303, "/dashboard/go/deal-12")
    cookie = res[3]["Set-Cookie"]
    assert "SameSite=Lax" in cookie and "Secure" in cookie and "HttpOnly" in cookie and "Path=/dashboard" in cookie
    token = cookie.split(";")[0].split("=", 1)[1]
    go = await bot.web.dispatch("GET", "/dashboard/go/deal-12", {"cookie": f"dash_session={token}"})
    assert go[:2] == (302, "/dashboard/#/i/deal-12")
    assert (await bot.web.dispatch("GET", "/dashboard/go/todo", {"cookie": f"dash_session={token}"}))[:2] == (302, "/dashboard/#/todo")
    # 앱의 로그인 시트: JSON 으로 서명만
    res = await bot.web.dispatch("GET", "/dashboard/login", {":peer": "127.0.0.1", "accept": "application/json"})
    assert res[0] == 200 and set(json.loads(res[1])) == {"t"} and "dash_login=" in res[3]["Set-Cookie"]


async def test_sliding_renewal(bot: DealBot) -> None:
    from datetime import timedelta

    h = session_headers(bot)
    status, _b, extra = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200 and "Set-Cookie" not in extra  # 아직 많이 남음
    sessions = bot.dashboard.sessions
    real = sessions.now
    sessions.now = lambda: real() + timedelta(days=25)
    status, _b, extra = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200 and extra["Set-Cookie"].startswith("dash_session=") and "SameSite=Lax" in extra["Set-Cookie"]


async def test_csrf_origin_and_fetch_site_checks(bot: DealBot) -> None:
    h = session_headers(bot)
    body = {"action": "bot.resume", "args": {}}
    for bad in ({"x-dash-token": ""}, {"x-dash-token": "nope"}, {"x-dash-token": session_headers(bot)["x-dash-token"]}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", body, dict(h, **bad))
        assert status == 403 and got["code"] == "csrf", bad
    for bad in ({"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}, {"origin": "https://evil.example"},
                {"origin": "null"}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", body, dict(h, **bad))
        assert status == 403, bad
    status, got, _ = await post_json(bot, "/dashboard/api/action", body, h)
    assert status == 200 and got["code"] == "resumed"
    hh = dict(h, **{"content-type": "text/plain"})
    status, got, _ = await post_json(bot, "/dashboard/api/action", body, hh)
    assert status == 415
    del h["origin"], h["sec-fetch-site"]  # 없는 것은 받음 (같은 앱 안의 요청)
    status, got, _ = await post_json(bot, "/dashboard/api/action", body, h)
    assert status == 200


async def test_basic_auth_gets_its_own_csrf_value(bot: DealBot) -> None:
    import base64

    from dash_helpers import PASSWORD, USER

    h = {"authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode(), ":peer": "127.0.0.1",
         "content-type": "application/json"}
    status, state, _ = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "bot.resume", "args": {}}, dict(h, **{"x-dash-token": state["me"]["csrf"]}))
    assert status == 200 and got["code"] == "resumed"
    assert state["me"]["csrf"] != session_headers(bot)["x-dash-token"]


async def test_state_has_no_secrets_and_supports_etag(bot: DealBot, settings: Settings) -> None:
    settings.secrets.threads_app_secret = "FAKE_APP_SECRET_123"
    body, _ua = subscription()
    h = session_headers(bot)
    await post_json(bot, "/dashboard/api/push/subscribe", body, h)
    status, state, extra = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200 and state["v"] == 1 and state["me"]["csrf"] == h["x-dash-token"]
    assert state["me"]["push_key"] and state["connect"]["push"]["devices"][0]["this"] is True
    dump = json.dumps(state, ensure_ascii=False)
    for secret in (body["endpoint"], body["keys"]["auth"], body["keys"]["p256dh"], "FAKE_APP_SECRET_123", "plum-river-cactus-42",
                   bot.db.kv_get("dash:csrf_key") or "x" * 64):
        assert secret not in dump
    assert (bot.settings.data_dir / "vapid_private.pem").read_text() not in dump
    status, _b, extra2 = await get_json(bot, "/dashboard/api/state", dict(h, **{"if-none-match": extra["ETag"]}))
    assert status == 304
    assert set(state) >= {"me", "banner", "counts", "todo", "deal", "blogs", "sns", "connect", "pending", "digest", "etag"}


async def test_push_subscribe_allowlist_and_unsubscribe_other_device_needs_confirm(bot: DealBot) -> None:
    h = session_headers(bot)
    body, _ua = subscription()
    for endpoint in ("http://fcm.googleapis.com/x", "https://evil.example.com/x", "https://192.168.0.1/x"):
        status, got, _ = await post_json(bot, "/dashboard/api/push/subscribe", dict(body, endpoint=endpoint), h)
        assert status == 400 and got["code"] == "bad_endpoint"
    status, got, _ = await post_json(bot, "/dashboard/api/push/subscribe", body, h)
    assert status == 200 and got["id"].startswith("d")
    other = session_headers(bot)
    status, conf, _ = await post_json(bot, "/dashboard/api/push/unsubscribe", {"id": got["id"]}, other)
    assert status == 409 and conf["status"] == "confirm"
    status, done, _ = await post_json(bot, "/dashboard/api/push/unsubscribe", {"id": got["id"], "ctoken": conf["ctoken"]}, other)
    assert status == 200 and bot.router.store.sub(got["id"]) is None  # type: ignore[union-attr]
    rows = [r["action"] for r in bot.db._q("SELECT action FROM dash_audit")]
    assert "push.subscribe" in rows and "push.unsubscribe" in rows


async def test_rate_limits(bot: DealBot) -> None:
    h = session_headers(bot)
    for _ in range(5):
        status, _g, _ = await post_json(bot, "/dashboard/api/push/test", {}, h)
        assert status == 200
    status, got, _ = await post_json(bot, "/dashboard/api/push/test", {}, h)
    assert status == 429
    for i in range(30):
        status, _g, _ = await post_json(bot, "/dashboard/api/action", {"action": "bot.resume", "args": {}}, h)
        assert status == 200, i
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "bot.resume", "args": {}}, h)
    assert status == 429 and got["code"] == "rate"


async def test_csp_logout_and_app_files(bot: DealBot) -> None:
    from urllib.parse import urlencode

    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/", h)
    assert res[0] == 200 and "script-src 'self'" in res[3]["Content-Security-Policy"]
    body = urlencode({"t": bot.dashboard.form_token}).encode()
    res = await bot.web.dispatch("POST", "/dashboard/logout", dict(h, **{"content-type": "application/x-www-form-urlencoded",
                                                                          "content-length": str(len(body))}), body)
    assert res[0] == 303 and res[3]["Clear-Site-Data"] == '"cache"' and "storage" not in res[3]["Clear-Site-Data"]
    assert bot.db._one("SELECT 1 FROM dash_audit WHERE action = 'logout'") is not None
    for path in ("/dashboard/sw.js", "/dashboard/static/app.js", "/dashboard/static/app.css"):  # 로그인 없이 (C 의 파일이 없으면 404)
        assert (await bot.web.dispatch("GET", path, {}))[0] in (200, 404), path


async def test_connect_needs_same_origin_and_never_logs_the_url(bot: DealBot, settings: Settings,
                                                                caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    settings.secrets.threads_app_id, settings.secrets.threads_app_secret = "123456", "FAKE_SECRET_X"
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/connect/threads", dict(h, **{"sec-fetch-site": "cross-site"}))
    assert res[0] == 403
    res = await bot.web.dispatch("GET", "/dashboard/connect/threads", h)
    assert res[0] == 302 and res[1].startswith("https://") and "/oauth/authorize" in res[1]
    state = bot.db.kv_get("threads_oauth_state") or ""
    assert state and state not in caplog.text and "threads auth redirect" in caplog.text


def test_origin_list_uses_the_public_url(bot: DealBot) -> None:
    assert ORIGIN in bot.dashboard.api.allowed_origins()  # type: ignore[union-attr]
