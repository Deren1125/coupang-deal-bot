"""/dashboard/jobprep/ 화면 한 장 (HTML 틀). 페이지 안 스크립트 없음 — boot 는 application/json (csrf·설정 요약만, 비밀값·책 글 없음).
보안 머리글은 service.SECURITY_HEADERS 그대로 + 녹음 다시 듣기용 media-src 'self' blob:."""

from __future__ import annotations

from html import escape
from typing import Any

TABS = (("today", "오늘"), ("me", "내 소개"), ("acad", "학사"), ("jobs", "공고"), ("cases", "합격 사례"), ("code", "코딩"),
        ("lib", "자료함"), ("speech", "스피치"), ("eng", "영어"), ("set", "설정"))
LAZY = ("jp_jobs.js", "jp_code.js", "jp_talk.js", "jp_misc.js")


def headers() -> dict[str, str]:
    from dealbot.dashboard.service import SECURITY_HEADERS

    h = dict(SECURITY_HEADERS)
    h["Content-Security-Policy"] = h["Content-Security-Policy"].replace("media-src 'self';", "media-src 'self' blob:;")
    h["Cache-Control"] = "no-store"
    return h


def html(boot: dict[str, Any]) -> str:
    from dealbot.dashboard.api import boot_json
    from dealbot.dashboard.shell import asset_url

    tabs = "".join(f'<a class="jp-tab" href="#/{k}" data-tab="{k}">{escape(v)}</a>' for k, v in TABS)
    metas = "".join(f'<meta name="jp-{n.split(".")[0]}" content="{asset_url(n)}">' for n in LAZY)
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="format-detection" content="telephone=no"><meta name="robots" content="noindex,nofollow"><title>취준</title>'
        f'<link rel="stylesheet" href="{asset_url("app.css")}"><link rel="stylesheet" href="{asset_url("jobprep.css")}">'
        f'<script blocking="render" src="{asset_url("theme.js")}"></script>'
        f'<script defer src="{asset_url("jobprep.js")}"></script>{metas}'
        '</head><body class="jp">'
        '<header class="hd jp-hd"><a class="jp-back" href="/dashboard/">← 대시보드</a><h1 id="jp-ttl">취준</h1></header>'
        f'<nav class="jp-tabs" aria-label="취준 메뉴">{tabs}</nav>'
        '<main id="jp-main" tabindex="-1"><p class="boot">불러오는 중…</p></main>'
        '<div class="jp-chatbar" id="jp-chatbar"><button type="button" id="jp-chat-open" class="jp-chat-open">무엇이든 물어보기…</button></div>'
        '<div class="jp-sheet" id="jp-sheet" hidden></div>'
        '<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>'
        f'<script type="application/json" id="jp-boot">{boot_json(boot)}</script>'
        '</body></html>'
    )
