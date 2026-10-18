"""사진 부탁 알림에 붙는 '사진 받을 곳' 링크 (주인 10/18: "링크를 같이 줘야 내가 바로 첨부할 수 있지").

알림 모양(spec §3.3)의 선택 칸 `links: [{label, url}]` — 최대 3개, http(s) 만, 주소 500자 안, 이름은 짧은 한국어
('상품 페이지' · '사진 출처 1'). 딜봇 딜 사진 부탁은 상품 페이지(제휴 링크가 아닌 원래 주소), Blog-Auto 글은 그 글이 이미 가진
사진 출처(공식 페이지·캡처할 기사·검색 링크)만 — 새로 찾아 오지 않는다. 예전 알림(칸 없음)은 그대로 [].
쓰는 곳: 웹 푸시 첫 링크 = 알림 버튼('상품 열기'·'사진 찾기'), 카톡 예비 글 끝 줄, 대시보드 할 일 줄의 '↗' 버튼.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from dealbot.dashboard.model import safe_text

MAX_LINKS = 3
URL_MAX = 500
LABEL_MAX = 24
KAKAO_URL_MAX = 150  # 카톡 한 통(200자)에 넣을 주소 — 길면 쿼리를 떼고, 그래도 길면 넣지 않음


def ok_url(url: Any) -> str | None:
    """http(s) · 호스트 있음 · 500자 안 · 공백·제어·따옴표·꺾쇠·역슬래시·중괄호·'@'(아이디 끼우기) 없음 → 그대로, 아니면 None."""
    if not isinstance(url, str) or not url or len(url) > URL_MAX:
        return None
    if any(c.isspace() or not c.isprintable() or c in "\"'<>\\`{}" for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        return None
    return url


def clean_links(raw: Any) -> list[dict[str, str]]:
    """[{label, url}] 를 걸러서 (모양이 틀린 것은 빼고, 같은 주소는 한 번, 최대 3개). 이름이 없으면 '사진 출처 N'."""
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for x in raw[:12]:
        if not isinstance(x, dict) or (u := ok_url(x.get("url"))) is None or u in seen:
            continue
        seen.add(u)
        label = safe_text(x["label"][:200], LABEL_MAX * 2) if isinstance(x.get("label"), str) else ""
        label = label if len(label) <= LABEL_MAX else label[: LABEL_MAX - 1].rstrip() + "…"
        out.append({"label": label or f"사진 출처 {len(out) + 1}", "url": u})
        if len(out) >= MAX_LINKS:
            break
    return out


def action_title(src: str, blog: str) -> str:
    """푸시 알림 버튼 이름: 딜(상품 페이지)은 '상품 열기', 블로그 글은 '사진 찾기'."""
    return "상품 열기" if src == "dealbot" or blog == "hotdeal" else "사진 찾기"


def kakao_url(links: list[dict[str, str]]) -> str | None:
    """카톡 글 끝 줄에 넣을 첫 링크 (긴 주소는 쿼리를 떼고, 그래도 150자를 넘으면 None)."""
    if not links:
        return None
    u = links[0]["url"]
    if len(u) > KAKAO_URL_MAX:
        u = u.split("#", 1)[0].split("?", 1)[0]
    return u if len(u) <= KAKAO_URL_MAX else None
