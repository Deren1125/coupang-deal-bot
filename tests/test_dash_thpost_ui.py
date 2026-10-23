"""스레드 글·예약·댓글·통계 칸 (주인 10/21): thpost.js 가 threads.js 의 세 칸(compose · alerts · stats)에 붙는다 — 크기·칸 붙이기·불러오기."""

from __future__ import annotations

import gzip

from dealbot.dashboard import api, shell, webapp

STATIC = webapp.STATIC_DIR
THPOST_JS_MAX = 10 * 1024
THPOST_JS_GZIP_MAX = 4 * 1024


def test_thpost_js_is_small_safe_and_served() -> None:
    js = (STATIC / "thpost.js").read_bytes()
    assert len(js) <= THPOST_JS_MAX and len(gzip.compress(js, 9)) <= THPOST_JS_GZIP_MAX
    text = js.decode("utf-8")
    assert "'use strict'" in text
    assert "innerHTML" not in text and "eval(" not in text and "http://" not in text
    assert "thpost.js" in api.APP_FILES and api.APP_FILES["thpost.js"].startswith("text/javascript")


def test_thpost_js_fills_the_three_slots_whichever_loads_first() -> None:
    text = (STATIC / "thpost.js").read_text(encoding="utf-8")
    assert "const mine = {compose, alerts, stats}" in text
    assert "Object.defineProperty(W, 'SBThreads'" in text and "o.slots[k] = mine[k]" in text
    threads = (STATIC / "threads.js").read_text(encoding="utf-8")
    assert "W.SBThreads = {view, slots}" in threads  # 덮어쓰기 — 그래서 setter
    for action in ("compose", "q.cancel", "x.reply"):
        assert action in text and "h.exec(" in text


def test_thpost_js_is_loaded_early_and_app_hands_over_exec_and_upload() -> None:
    page = shell.page({})
    assert f'<script defer src="{shell.asset_url("thpost.js")}"></script>' in page
    assert page.count("<script src=") == 1
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "T.view(b, {el, btn, api, toast, S, exec, uploadAll})" in app
    assert len((STATIC / "app.js").read_bytes()) <= 88 * 1024  # test_dash_shell.APP_JS_MAX 와 같게 (10/23)
