"""대시보드 앱(Sns-board) 첫 화면 — 껍데기 HTML + 첫 화면 값(boot JSON) (spec §1·§5.5, 주인 10/18).

"휴대폰으로 일 처리를 많이 하다보니 휴대폰에서도 대시보드 (엡) 사용이 용이햇으면 좋겟어" ·
"대시보드 구성은 반드시 깔끔해야해" — 휴대폰(360~412px)이 먼저, 아래 탭 5개(할 일 · 딜 · 블로그 · 통계 · 연결).

- 스크립트는 /dashboard/static/app.js 하나 (CSP script-src 'self' — 페이지 안 스크립트 없음). 사진 편집기 edit.js 는 처음 쓸 때만.
  첫 화면 값은 <script type="application/json" id="boot"> (실행되지 않는 글, api.boot_json 이 '</' 등을 막음)
- 머리(제목·새로고침)와 아래 탭은 HTML 에 미리 그려 둔다 — app.js 를 받기 전에도 화면 틀이 바로 보이게
- app.js·app.css 주소에는 파일 내용의 짧은 해시(?v=) — 바꾸면 서비스 워커가 새 파일을 받는다
"""

from __future__ import annotations

import hashlib
from functools import cache
from html import escape
from typing import Any

from dealbot.dashboard import icons, webapp
from dealbot.dashboard.api import boot_json

APP_JS = "app.js"
APP_CSS = "app.css"
EDIT_JS = "edit.js"  # 사진 고치기·편집기 (10/19) — app.js 가 처음 쓸 때만 불러온다 (주소는 meta sb-edit)
PRODUCT_JS = "product.js"  # 제품 테스트 (10/21) — 블로그 › Pick '제품 테스트'를 처음 열 때만 (주소는 meta sb-product)
SCHED_JS = "sched.js"  # 일정 (10/21) — 블로그 탭 '예약 일정 보기'를 처음 열 때만 (주소는 meta sb-sched)
THREADS_JS = "threads.js"  # 스레드 (10/21) — 블로그 탭 '스레드 →'를 처음 열 때만 (주소는 meta sb-threads, threads_proxy.py)


@cache
def asset_url(name: str) -> str:
    """'/dashboard/static/app.js?v=1a2b3c4d5e' (파일이 없으면 v=0)."""
    try:
        ver = hashlib.sha256((webapp.STATIC_DIR / name).read_bytes()).hexdigest()[:10]
    except OSError:
        ver = "0"
    return f"{webapp.STATIC_PREFIX}{name}?v={ver}"


def _tabs() -> str:
    out = []
    for key, label, svg in icons.TABS:
        badge = '<b class="bdg" id="bdg" hidden></b>' if key == "todo" else ""
        out.append(f'<a class="tab" href="#/{key}" data-tab="{key}">{svg}{badge}<span>{escape(label)}</span></a>')
    return "".join(out)


def page(boot: dict[str, Any], title: str = "") -> str:
    """앱 화면 한 장. boot = /dashboard/api/state 와 같은 값 (비밀값 없음 — state.py 가 만든 것)."""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="format-detection" content="telephone=no">'
        + webapp.head_tags()
        + f'<link rel="stylesheet" href="{asset_url(APP_CSS)}">'
        f'<script src="{asset_url(APP_JS)}" defer></script>'
        f'<meta name="sb-edit" content="{asset_url(EDIT_JS)}">'
        f'<meta name="sb-product" content="{asset_url(PRODUCT_JS)}">'
        f'<meta name="sb-sched" content="{asset_url(SCHED_JS)}">'
        f'<meta name="sb-threads" content="{asset_url(THREADS_JS)}">'
        '</head><body>'
        '<header class="hd"><h1 id="ttl">할 일</h1><span class="old" id="old" hidden></span>'
        '<button type="button" class="rf" id="rf">새로고침</button></header>'
        '<div class="ptr" id="ptr" aria-hidden="true"></div>'
        '<main id="main" tabindex="-1"><p class="boot">불러오는 중…</p></main>'
        f'<nav class="tabs" aria-label="메뉴">{_tabs()}</nav>'
        '<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>'
        '<div id="sheets"></div>'
        f'<script type="application/json" id="boot">{boot_json(boot)}</script>'
        '</body></html>'
    )
