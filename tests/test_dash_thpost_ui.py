"""스레드 글·예약·댓글·통계 칸 (주인 10/21): thpost.js 가 threads.js 의 세 칸(compose · alerts · stats)에 붙는다 — 크기·칸 붙이기·불러오기."""

from __future__ import annotations

import gzip
import re

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
    assert len((STATIC / "app.js").read_bytes()) <= 90 * 1024  # test_dash_shell.APP_JS_MAX 와 같게 (10/27)


def test_stat_tiles_grow_with_their_label_even_with_the_threads_button_style() -> None:
    """주인 10/26 스크린샷: 통계 숫자 칸의 '조회·좋아요·답글' 글자가 아래 칸에 가려짐 — threads.js 의 .tb{height:34px} 가 같은 이름의
    숫자 칸 단추에도 걸렸다. 숫자 칸은 높이를 내용대로 (.tt .tb 가 더 구체적이라 이김) · 글자는 숫자 아래 한 줄."""
    text = (STATIC / "thpost.js").read_text(encoding="utf-8")
    tile = re.search(r"\.tt \.tb\{([^}]*)\}", text)
    assert tile and "height:auto" in tile.group(1) and "min-height:44px" in tile.group(1)
    assert ".tt span{display:block}" in text
    assert ".tb{flex:none;height:34px" in (STATIC / "threads.js").read_text(encoding="utf-8")  # 부딪히는 쪽이 아직 그대로인지
