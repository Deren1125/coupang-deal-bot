"""취준 화면·경로 — 아래 탭 링크 · APP_FILES · PAGE_PREFIXES · 로그인 없이 302/401 · CSP(media-src 'self' blob:) · POST 는 guard 없으면 403 ·
static 파일에 데이터 낱말 없음 · 설정으로 끄면 404·링크 숨김."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dash_helpers import ORIGIN, get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import api as apimod
from dealbot.dashboard import jobprep, service, webapp


@pytest.fixture
def bot(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_DATA_DIR", str(tmp_path / "jpdata"))
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.dashboard.jobprep.sandbox_mode = "rlimit"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


JS = ("jobprep.js", "jp_jobs.js", "jp_code.js", "jp_talk.js", "jp_misc.js")


async def test_tab_link_and_files_registered(bot: DealBot) -> None:
    html = (await bot.web.dispatch("GET", "/dashboard/", session_headers(bot)))[1]
    assert '<a class="tab" href="/dashboard/jobprep/">' in html and "<span>취준</span>" in html
    for n in (*JS, "jobprep.css"):
        assert n in apimod.APP_FILES
        res = await bot.web.dispatch("GET", f"/dashboard/static/{n}", {})
        assert res[0] == 200 and res[3]["X-Content-Type-Options"] == "nosniff"
    assert "/dashboard/jobprep" in service.PAGE_PATHS and "/dashboard/jobprep/" in service.PAGE_PREFIXES


async def test_page_needs_login_and_has_csp(bot: DealBot) -> None:
    res = await bot.web.dispatch("GET", "/dashboard/jobprep/", {":peer": "127.0.0.1"})
    assert res[0] == 302 and "/dashboard/login" in res[1]
    res = await bot.web.dispatch("GET", "/dashboard/api/jobprep/today", {":peer": "127.0.0.1"})
    assert res[0] == 401
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/jobprep/", h)
    assert res[0] == 200 and res[3]["Cache-Control"] == "no-store"
    csp = res[3]["Content-Security-Policy"]
    assert "media-src 'self' blob:" in csp and "script-src 'self';" in csp and "frame-ancestors 'none'" in csp
    body = res[1]
    assert "<script>" not in body and not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)", body)
    boot = json.loads(re.search(r'id="jp-boot">(.*?)</script>', body).group(1))  # type: ignore[union-attr]
    assert set(boot) == {"csrf", "v", "tabs"} and len(boot["tabs"]) == 10
    assert (await bot.web.dispatch("GET", "/dashboard/jobprep", h))[0] == 301


async def test_api_get_and_post_guard(bot: DealBot) -> None:
    h = session_headers(bot)
    status, data, hdr = await get_json(bot, "/dashboard/api/jobprep/today", h)
    assert status == 200 and hdr["Cache-Control"] == "no-store" and len(data["todo"]) == 4
    status, data, _ = await get_json(bot, "/dashboard/api/jobprep/profile", h)
    assert status == 200 and data["ver"] == 1
    # guard: X-Dash-Token 없으면 403 · 다른 사이트면 403
    no_token = session_headers(bot, csrf=False)
    status, data, _ = await post_json(bot, "/dashboard/api/jobprep/lib/add", {"title": "x"}, no_token)
    assert status == 403
    cross = dict(h, **{"sec-fetch-site": "cross-site"})
    status, _d, _ = await post_json(bot, "/dashboard/api/jobprep/lib/add", {"title": "x"}, cross)
    assert status == 403
    status, data, _ = await post_json(bot, "/dashboard/api/jobprep/lib/add", {"title": "메모", "body": "b", "tags": ["코딩"]}, h)
    assert status == 200 and data["status"] == "ok"
    status, data, _ = await get_json(bot, "/dashboard/api/jobprep/lib?tag=코딩", h)
    assert [i["title"] for i in data["items"]] == ["메모"]
    status, data, _ = await get_json(bot, "/dashboard/api/jobprep/code/q/1.1", h)
    assert status == 404  # 목록 없음
    status, data, _ = await get_json(bot, "/dashboard/api/jobprep/file/acad/../../x.jpg", h)
    assert status == 404


async def test_upload_needs_guard_and_kind(bot: DealBot, tmp_path: Path) -> None:
    h = session_headers(bot)
    p = tmp_path / "up.bin"
    p.write_bytes(b"not an image")
    res = await bot.web.dispatch("POST", "/dashboard/api/jobprep/upload", dict(session_headers(bot, csrf=False), **{"x-jp-kind": "acad"}), body_path=p)
    assert res[0] == 403 and not p.exists()
    p.write_bytes(b"not an image")
    res = await bot.web.dispatch("POST", "/dashboard/api/jobprep/upload", dict(h, **{"x-jp-kind": "acad", "content-type": "text/plain"}), body_path=p)
    assert res[0] == 415 and not p.exists()
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, "PNG")
    p.write_bytes(buf.getvalue())
    res = await bot.web.dispatch("POST", "/dashboard/api/jobprep/upload", dict(h, **{"x-jp-kind": "acad", "content-type": "image/png"}), body_path=p)
    out = json.loads(res[1])
    assert res[0] == 200 and out["doc_id"] >= 1 and out["file"].startswith("acad/")
    got = await bot.web.dispatch("GET", "/dashboard/api/jobprep/file/" + out["file"], h)
    assert got[0] == 200 and got[2] == "image/jpeg" and got[1][:2] == b"\xff\xd8"


def test_static_files_have_no_data() -> None:
    for n in (*JS, "jobprep.css"):
        text = (webapp.STATIC_DIR / n).read_text(encoding="utf-8")
        for word in ("연세", "3.83", "2027-07-13", "SOLUTION", "Cracking", "api_key", "GEMINI_API_KEY", "innerHTML"):
            assert word not in text, (n, word)


async def test_disabled_closes_routes_and_hides_link(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBPREP_DATA_DIR", str(tmp_path / "jpdata"))
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.dashboard.jobprep.enabled = False
    b = DealBot(push_settings(settings))
    try:
        h = session_headers(b)
        assert (await b.web.dispatch("GET", "/dashboard/jobprep/", h))[0] == 404
        assert (await b.web.dispatch("GET", "/dashboard/api/jobprep/today", h))[0] == 404
        html = (await b.web.dispatch("GET", "/dashboard/", h))[1]
        assert "/dashboard/jobprep/" not in html and b.dashboard.api.jobprep is None and not jobprep.enabled()
    finally:
        b.db.close()
    assert not (tmp_path / "jpdata").exists()
    _ = ORIGIN
