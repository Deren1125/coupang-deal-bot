"""휴대폰 바탕화면에 앱처럼 (주인 10/15 — "갤럭시 휴대폰이랑 아이패드, 아이폰에서도 앱처럼 바탕화면에 추가할 수 있나?").

- /dashboard/manifest.webmanifest: 이름 'Sns-board'(주인이 고른 표기 — 아이폰에서 잘리지 않게), 시작 주소·범위 /dashboard,
  주소창 없는 화면(standalone), 아이콘 192·512(모서리 투명) + 512 maskable(가장자리까지 꽉 참)
- /dashboard/static/<파일>: 주인이 고른 아이콘 그대로 (static 폴더). 휴대폰(과 크롬의 앱 만들기 서버)이 로그인 없이 받아 가야 해서
  이 두 가지(매니페스트·아이콘)만 인증 없이 연다 — 정확한 경로만, 그 밖은 그대로 로그인
- head_tags(): 페이지·로그인 화면 <head> 에 넣는 링크·메타 (아이폰 홈 화면 아이콘 180, 상태 표시줄, 밝은/어두운 테마 색)

아이콘 주소에는 파일 내용의 짧은 해시(?v=)를 붙여 두고 1년 캐시 — 아이콘을 바꾸면 주소가 바뀌어 새로 받는다.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

APP_NAME = "Sns-board"  # 매니페스트 name·short_name, apple-mobile-web-app-title, application-name, <title> 모두 이것
APP_DESCRIPTION = "채널 숫자와 지금 할 일을 한 화면에"
START_URL = "/dashboard"
SCOPE = "/dashboard"
MANIFEST_PATH = "/dashboard/manifest.webmanifest"
STATIC_PREFIX = "/dashboard/static/"
STATIC_DIR = Path(__file__).resolve().parent / "static"
# 화면 바탕(render.CSS 의 --bg)과 아이콘 바탕(어두운 남색). 페이지는 밝은 화면 하나라 매니페스트는 밝은 색,
# 휴대폰이 어두운 모드면 상태 표시줄만 아이콘 바탕색으로
PAGE_BG = "#f4f5f7"
DARK_BG = "#151a2d"

# 파일 이름 → Content-Type (여기 적은 파일만 열림)
STATIC_TYPES: dict[str, str] = {
    "icon-192.png": "image/png",
    "icon-512.png": "image/png",
    "icon-maskable-512.png": "image/png",
    "apple-touch-icon.png": "image/png",
    "favicon-64.png": "image/png",
    "icon.svg": "image/svg+xml",
}
MANIFEST_TYPE = "application/manifest+json; charset=utf-8"
ICON_CACHE = "public, max-age=31536000, immutable"  # 주소에 내용 해시가 붙어 있어 오래 둬도 됨
MANIFEST_CACHE = "public, max-age=86400"  # 이름·아이콘을 바꾸면 하루 안에 휴대폰이 새로 읽게
# 아이콘 응답에만 붙는 헤더 (SVG 를 주소로 바로 열어도 그 안에서 아무것도 실행·불러오지 못하게)
STATIC_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


def static_paths() -> list[str]:
    return [STATIC_PREFIX + name for name in STATIC_TYPES]


def public_paths() -> list[str]:
    """인증 없이 여는 경로 (매니페스트 + 아이콘). 로그인 화면은 service 가 더한다."""
    return [MANIFEST_PATH, *static_paths()]


@cache
def _read(name: str) -> bytes | None:
    if name not in STATIC_TYPES:
        return None
    try:
        return (STATIC_DIR / name).read_bytes()
    except OSError:
        return None


def icon_url(name: str) -> str:
    """'/dashboard/static/icon-192.png?v=1a2b3c4d5e' (파일이 없으면 v=0 — 받으면 404)."""
    data = _read(name)
    ver = hashlib.sha256(data).hexdigest()[:10] if data is not None else "0"
    return f"{STATIC_PREFIX}{name}?v={ver}"


def static_response(name: str) -> Any:
    data = _read(name)
    if data is None:
        return 404, "없는 파일", "text/plain; charset=utf-8", {"X-Content-Type-Options": "nosniff"}
    return 200, data, STATIC_TYPES[name], {"Cache-Control": ICON_CACHE, **STATIC_HEADERS}


def manifest() -> dict[str, Any]:
    return {
        "id": START_URL,
        "name": APP_NAME,
        "short_name": APP_NAME,
        "description": APP_DESCRIPTION,
        "lang": "ko",
        "dir": "ltr",
        "start_url": START_URL,
        "scope": SCOPE,
        "display": "standalone",
        "background_color": PAGE_BG,
        "theme_color": PAGE_BG,
        "icons": [
            {"src": icon_url("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icon_url("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icon_url("icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


def manifest_response() -> Any:
    body = json.dumps(manifest(), ensure_ascii=False, indent=1)
    return 200, body, MANIFEST_TYPE, {"Cache-Control": MANIFEST_CACHE, "X-Content-Type-Options": "nosniff"}


def head_tags() -> str:
    """<head> 안에 넣을 것 (<title> 포함). 값은 모두 코드에 박은 것이지만 그래도 이스케이프."""
    a = escape(APP_NAME, quote=True)
    return (
        f"<title>{escape(APP_NAME)}</title>"
        f'<link rel="manifest" href="{MANIFEST_PATH}" crossorigin="use-credentials">'
        f'<meta name="application-name" content="{a}">'
        f'<meta name="apple-mobile-web-app-title" content="{a}">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">'
        f'<meta name="theme-color" media="(prefers-color-scheme: light)" content="{PAGE_BG}">'
        f'<meta name="theme-color" media="(prefers-color-scheme: dark)" content="{DARK_BG}">'
        f'<link rel="icon" href="{icon_url("favicon-64.png")}" type="image/png" sizes="64x64">'
        f'<link rel="icon" href="{icon_url("icon.svg")}" type="image/svg+xml" sizes="any">'
        f'<link rel="apple-touch-icon" href="{icon_url("apple-touch-icon.png")}" sizes="180x180">'
    )
