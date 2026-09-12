"""정보 글: 상품 링크가 없는 게시판 글(이벤트·공지 등)을 본문·사진만 정리해 올린다.

수익은 없지만 채널 콘텐츠 가치용. 게시판 글을 발행 시점에 다시 읽어 본문 텍스트와 사진을 뽑고,
(요약기가 켜져 있으면) 채널 양식에 맞게 짧게 다시 쓴 뒤 플랫폼별 템플릿(텔레그램/카톡/스레드)으로 만든다.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from dealbot.collectors.ppomppu import BROWSER_HEADERS, decode_html
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer

log = logging.getLogger(__name__)

# 게시판별 본문 컨테이너 (앞에서부터 처음 맞는 것). 페이지 전체를 감싸는 넓은 셀렉터(article, #content 등)는
# 넣지 않는다 — 메뉴까지 통째로 긁혀 나간 적이 있다. 못 찾으면 아래 _best_content_block 이 링크 밀도로
# 본문 덩어리를 고르고, 그 경우 confident=False 로 표시해 관리자 확인을 받게 한다.
CONTENT_SELECTORS = [
    "div.view_content",  # 루리웹
    "td.board-contents",  # 뽐뿌 — 이 셀은 class 속성이 두 번 적혀 있어 on_duplicate_attribute="ignore" 로 읽어야 잡힌다
    "div.board-contents",
    "div.board_main_view",
    "div.view_body",
]
_STRIP_TAGS = ["script", "style", "iframe", "noscript", "button", "form", "nav", "header", "footer", "aside"]
_IMG_SKIP = re.compile(
    r"emoticon|emoji|/icon|smile|/level|blank|spacer|loading|logo|banner|btn_|button|\.svg(\?|$)|/images/(main|menu|common)/|dot\d",
    re.I,
)
_URL_LINE = re.compile(r"^\s*(https?://\S+)\s*$")
_BOARD_HOSTS = ("ruliweb.com", "ppomppu.co.kr", "algumon.com", "clien.net", "fmkorea.com", "quasarzone.com")
# 본문 상자 안에 섞여 들어오는 게시판 버튼 글자들 (한 줄이 정확히 이것뿐이면 뺀다)
_NAV_LINES = {"목록", "댓글", "추천", "비추천", "신고", "인쇄", "스크랩", "글쓰기", "답글", "수정", "삭제", "이전글", "다음글", "공유", "URL 복사", "닫기", "더보기"}
_NOISE_BLOCK = re.compile(r"(^|[_\-\s])(comment|comments|reply|replies|cmt|sns|share|related|banner|ads?)([_\-\s]|$)", re.I)
_MIN_BLOCK_CHARS = 60
_MAX_LINK_DENSITY = 0.4


@dataclass(slots=True)
class PostBody:
    text: str = ""  # 채널에 그대로 낼 수 있게 길이를 맞춘 본문
    images: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # 본문에 적힌 외부 링크 (이벤트 페이지 등)
    raw: str = ""  # 요약기에 넘기는 더 긴 원문 (정리만 하고 자르지 않은 것)
    confident: bool = True  # 게시판 전용 셀렉터로 본문을 찾았으면 True, 추정으로 골랐으면 False

    @property
    def empty(self) -> bool:
        return not self.text and not self.images


def unwrap_redirect(href: str) -> str:
    """게시판이 외부 링크를 자기 서버로 감싼 경우(뽐뿌 s.ppomppu.co.kr?target=base64) 원래 주소를 꺼낸다."""
    try:
        u = urlparse(href)
    except ValueError:
        return href
    if not u.netloc.lower().endswith("ppomppu.co.kr"):
        return href
    target = (parse_qs(u.query).get("target") or [""])[0]
    if not target:
        return href
    try:
        decoded = base64.b64decode(target + "=" * (-len(target) % 4)).decode("utf-8", "ignore").strip()
    except (ValueError, UnicodeDecodeError):
        return href
    return decoded if decoded.startswith("http") else href


def _clean_lines(raw: str) -> list[str]:
    out: list[str] = []
    for line in raw.splitlines():
        s = re.sub(r"[ \t ]+", " ", line).strip()
        if not s or s in _NAV_LINES:
            continue
        out.append(s)
    return out


def _text_len(el: Tag) -> int:
    return len(el.get_text(" ", strip=True))


def _link_density(el: Tag) -> float:
    total = _text_len(el)
    if total == 0:
        return 1.0
    linked = sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))
    return min(1.0, linked / total)


def _is_noise_block(el: Tag) -> bool:
    ident = " ".join([*(el.get("class") or []), str(el.get("id") or "")])
    return bool(_NOISE_BLOCK.search(ident))


def _best_content_block(root: Tag) -> Tag | None:
    """셀렉터가 안 맞을 때: 글자는 많고 링크는 적은 덩어리를 본문으로 추정한다 (메뉴·댓글은 링크 투성이라 밀려남)."""
    for el in root.find_all(["div", "td", "ul", "section", "aside"]):
        if _is_noise_block(el):
            el.decompose()  # 댓글·공유·배너 상자는 본문 후보에서 통째로 뺀다
    best: Tag | None = None
    best_score = 0.0
    for el in root.find_all(["div", "td", "article", "section", "main"]):
        n = _text_len(el)
        if n < _MIN_BLOCK_CHARS:
            continue
        density = _link_density(el)
        if density > _MAX_LINK_DENSITY:
            continue
        score = n * (1 - density) ** 2
        if score > best_score:
            best, best_score = el, score
    if best is None:
        return None
    # 같은 글을 담은 가장 안쪽 상자로 좁힌다 (자손 하나가 글의 거의 전부를 갖고 있으면 그쪽으로)
    total = _text_len(best)
    for el in best.find_all(["div", "td", "article", "section"]):
        if _text_len(el) >= 0.9 * total:
            best = el  # 문서 순서라 더 깊은 자손이 뒤에 온다
    return best


def _cut(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # 줄 중간에서 끊기지 않게 마지막 줄바꿈까지
    if "\n" in cut[max_chars // 2 :]:
        cut = cut[: cut.rfind("\n")]
    return cut.rstrip() + "…"


def extract_post_body(
    html: str,
    base_url: str,
    *,
    selectors: list[str] | None = None,
    max_chars: int = 500,
    max_images: int = 3,
    raw_chars: int = 4000,
) -> PostBody:
    """게시판 글 HTML → 본문 텍스트(정리·요약), 본문 사진 URL, 본문 외부 링크.

    본문 상자를 못 찾으면 페이지 전체를 내보내지 않고 추정 덩어리(confident=False)만 쓰거나 빈 본문을 돌려준다.
    """
    # 뽐뿌 본문 셀처럼 속성이 중복된 태그는 첫 값을 쓴다 (기본값은 뒷값이라 class 가 사라짐)
    soup = BeautifulSoup(html, "html.parser", on_duplicate_attribute="ignore")
    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()
    container: Tag | None = None
    confident = True
    for sel in selectors or CONTENT_SELECTORS:
        container = soup.select_one(sel)
        if container is not None:
            break
    if container is None:
        confident = False
        container = _best_content_block(soup.body or soup)
        if container is None:
            log.info("info post: no content block found in %s", base_url)
            return PostBody(confident=False)

    # 사진: 본문 안의 img (아이콘·이모티콘·배너 제외, 상대 주소는 절대 주소로)
    images: list[str] = []
    for img in container.find_all("img"):
        src = img.get("data-original") or img.get("data-src") or img.get("src") or ""
        src = str(src).strip()
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
        href = unwrap_redirect(str(a["href"]).strip())
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if any(host.endswith(b) for b in _BOARD_HOSTS):
            continue
        if href not in links:
            links.append(href)

    # 본문 텍스트: 줄 단위로 정리, URL 만 있는 줄은 빼고, 같은 줄 반복 제거
    lines = [ln for ln in _clean_lines(container.get_text("\n")) if not _URL_LINE.match(ln)]
    joined = "\n".join(dict.fromkeys(lines))
    return PostBody(
        text=_cut(joined, max_chars),
        images=images,
        links=links[:3],
        raw=_cut(joined, raw_chars),
        confident=confident,
    )


class InfoPostBuilder:
    """게시판 글을 읽어 정보 글 본문을 만든다."""

    MAX_IMAGE_BYTES = 5 * 1024 * 1024

    def __init__(
        self,
        http: httpx.AsyncClient,
        renderer: TemplateRenderer,
        *,
        max_chars: int = 500,
        raw_chars: int = 4000,
        timeout: float = 20,
    ) -> None:
        self.http = http
        self.renderer = renderer
        self.max_chars = max_chars
        self.raw_chars = raw_chars
        self.timeout = timeout

    async def fetch(self, post_url: str) -> PostBody | None:
        try:
            resp = await self.http.get(post_url, headers=BROWSER_HEADERS, follow_redirects=True, timeout=self.timeout)
            resp.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("info post fetch failed %s: %s", post_url, e)
            return None
        return extract_post_body(decode_html(resp), post_url, max_chars=self.max_chars, raw_chars=self.raw_chars)

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
