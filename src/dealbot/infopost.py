"""정보 글: 상품 링크가 없는 게시판 글(이벤트·공지 등)을 본문·사진만 정리해 올린다.

수익은 없지만 채널 콘텐츠 가치용. 게시판 글을 발행 시점에 다시 읽어 본문 텍스트와 사진을 뽑고,
플랫폼별 템플릿(텔레그램/카톡/스레드)으로 만든다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from dealbot.collectors.ppomppu import BROWSER_HEADERS, decode_html
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer

log = logging.getLogger(__name__)

# 게시판별 본문 컨테이너 후보 (앞에서부터 처음 맞는 것)
CONTENT_SELECTORS = [
    "div.view_content",  # 루리웹
    "td.board-contents",  # 뽐뿌
    "div.board-contents",
    "div.board_main_view",
    "article",
    "div.view_body",
    "div#content",
]
_IMG_SKIP = re.compile(r"emoticon|emoji|/icon|smile|/level|blank|spacer|loading|logo|banner|btn_|button|\.svg(\?|$)", re.I)
_URL_LINE = re.compile(r"^\s*(https?://\S+)\s*$")
_BOARD_HOSTS = ("ruliweb.com", "ppomppu.co.kr", "algumon.com", "clien.net", "fmkorea.com", "quasarzone.com")


@dataclass(slots=True)
class PostBody:
    text: str = ""
    images: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # 본문에 적힌 외부 링크 (이벤트 페이지 등)

    @property
    def empty(self) -> bool:
        return not self.text and not self.images


def _clean_lines(raw: str) -> list[str]:
    out: list[str] = []
    for line in raw.splitlines():
        s = re.sub(r"[ \t ]+", " ", line).strip()
        if not s:
            continue
        out.append(s)
    return out


def extract_post_body(html: str, base_url: str, *, selectors: list[str] | None = None, max_chars: int = 500, max_images: int = 3) -> PostBody:
    """게시판 글 HTML → 본문 텍스트(정리·요약), 본문 사진 URL, 본문 외부 링크."""
    soup = BeautifulSoup(html, "html.parser")
    container = None
    for sel in selectors or CONTENT_SELECTORS:
        container = soup.select_one(sel)
        if container is not None:
            break
    if container is None:
        container = soup.body or soup
    for tag in container.find_all(["script", "style", "iframe", "noscript", "button", "form"]):
        tag.decompose()

    # 사진: 본문 안의 img (아이콘·이모티콘·배너 제외, 상대 주소는 절대 주소로)
    images: list[str] = []
    for img in container.find_all("img"):
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
        src = src.strip()
        if not src or src.startswith("data:"):
            continue
        if _IMG_SKIP.search(src) or _IMG_SKIP.search(" ".join(img.get("class") or [])):
            continue
        try:
            w, h = int(str(img.get("width") or "0").rstrip("px") or 0), int(str(img.get("height") or "0").rstrip("px") or 0)
        except ValueError:
            w = h = 0
        if (w and w < 120) or (h and h < 120):
            continue
        full = urljoin(base_url, src)
        if full not in images:
            images.append(full)
        if len(images) >= max_images:
            break

    # 외부 링크: 본문 <a href> 중 게시판 자체가 아닌 것
    links: list[str] = []
    for a in container.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if any(host.endswith(b) for b in _BOARD_HOSTS):
            continue
        if href not in links:
            links.append(href)

    # 본문 텍스트: 줄 단위로 정리, URL 만 있는 줄은 빼고, 너무 길면 자름
    lines = [ln for ln in _clean_lines(container.get_text("\n")) if not _URL_LINE.match(ln)]
    text = "\n".join(dict.fromkeys(lines))  # 같은 줄 반복 제거
    if len(text) > max_chars:
        cut = text[:max_chars]
        # 줄 중간에서 끊기지 않게 마지막 줄바꿈까지
        if "\n" in cut[max_chars // 2 :]:
            cut = cut[: cut.rfind("\n")]
        text = cut.rstrip() + "…"
    return PostBody(text=text, images=images, links=links[:3])


class InfoPostBuilder:
    """게시판 글을 읽어 정보 글 본문을 만든다."""

    MAX_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(self, http: httpx.AsyncClient, renderer: TemplateRenderer, *, max_chars: int = 500, timeout: float = 20) -> None:
        self.http = http
        self.renderer = renderer
        self.max_chars = max_chars
        self.timeout = timeout

    async def fetch(self, post_url: str) -> PostBody | None:
        try:
            resp = await self.http.get(post_url, headers=BROWSER_HEADERS, follow_redirects=True, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("info post fetch failed %s: %s", post_url, e)
            return None
        return extract_post_body(decode_html(resp), post_url, max_chars=self.max_chars)

    async def download_image(self, url: str, *, referer: str) -> bytes | None:
        """게시판 사진은 외부에서 바로 못 여는(핫링크 차단) 경우가 많아 봇이 받아서 올린다."""
        headers = {**BROWSER_HEADERS, "Referer": referer}
        try:
            resp = await self.http.get(url, headers=headers, follow_redirects=True, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("info post image download failed %s: %s", url, e)
            return None
        ctype = resp.headers.get("content-type", "")
        if not ctype.startswith("image/") or len(resp.content) > self.MAX_IMAGE_BYTES or len(resp.content) < 1024:
            log.info("info post image skipped (%s, %d bytes)", ctype, len(resp.content))
            return None
        return resp.content

    def context(self, deal: Deal, body: PostBody) -> dict[str, Any]:
        p = deal.product
        return {
            "product": p,
            "body": body.text,
            "images": body.images,
            "links": body.links,
            "source_url": p.extra.get("post_url") or p.url,
            "source_name": p.extra.get("source_label") or p.source,
        }

    def render(self, template: str, deal: Deal, body: PostBody, *, autoescape: bool) -> str:
        return self.renderer.render(template, autoescape=autoescape, **self.context(deal, body))
