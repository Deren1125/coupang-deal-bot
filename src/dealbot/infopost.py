"""정보 글: 상품 링크가 없는 게시판 글(이벤트·공지 등)을 본문·사진만 정리해 올린다.

수익은 없지만 채널 콘텐츠 가치용. 게시판 글을 발행 시점에 다시 읽어 본문 텍스트와 사진을 뽑고,
(요약기가 켜져 있으면) 채널 양식에 맞게 짧게 다시 쓴 뒤 플랫폼별 템플릿(텔레그램/카톡/스레드)으로 만든다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

from dealbot.collectors.ppomppu import BROWSER_HEADERS, decode_html
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer
from dealbot.utils.urls import is_affiliate_link, unwrap_redirect

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
_URL_LINE = re.compile(r"^\s*(?:·\s*)?(https?://\S+)\s*$")
_INLINE_URL = re.compile(r"https?://[^\s<>\"']+")
_BOARD_HOSTS = ("ruliweb.com", "ppomppu.co.kr", "algumon.com", "clien.net", "fmkorea.com", "quasarzone.com")
# 남의 제휴(수수료) 링크. 정보 글은 수익 링크·제휴 고지 없이 올리므로, 그대로 내면 남의 파트너스 링크를 공시 없이 퍼 나르게 된다
_AFFILIATE_HOSTS = ("link.coupang.com", "coupa.ng", "linkprice.com", "s.click.aliexpress.com")
_AFFILIATE_PATHS = (("toss.im", "/_m/"),)  # 토스 쉐어링크
_AFFILIATE_PARAMS = {"lptag"}  # 링크프라이스 추적값: 떼면 원래 상품·이벤트 주소만 남는다
THREADS_BODY_CHARS = 260  # 스레드 글(500자 제한)에 넣는 본문 길이
# 게시판 표시: 인용(>)과 글머리(▶ ☞ ■ …, 빈칸이 뒤따르는 '-' '*'). '-10%' 는 숫자라 그대로, ※(주의)·→ 는 뜻이 있어 둔다
_QUOTE_MARK = re.compile(r"^>+\s*")
_ITEM_MARK = re.compile(r"^(?:[▶►▷▸☞•■□●○◆◇]+\s*|[-*]\s+)")
# 글 끝의 짧은 끝인사·잡담 ("좋은 딜 되세요~", "즐쇼하세요", "감사합니다"). "응모하시면 되세요" 같은 안내는 건드리지 않게 좁게
_SIGN_OFF = re.compile(r"좋은\s*(?:딜|쇼핑|하루)|득템\s*하세요|즐(?:거운)?\s*쇼핑|즐쇼|감사합니다\s*[~!.]*$")
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


def strip_foreign_affiliate(url: str) -> str | None:
    """본문 링크에서 남의 제휴 표시를 걷어 낸다: lptag 같은 추적값은 떼고, 제휴 전용 주소(파트너스 단축 링크 등)는 None (버림)."""
    try:
        u = urlparse(url)
    except ValueError:
        return url
    host = u.netloc.lower()
    if any(host == h or host.endswith("." + h) for h in _AFFILIATE_HOSTS):
        return None
    if any((host == h or host.endswith("." + h)) and u.path.startswith(p) for h, p in _AFFILIATE_PATHS):
        return None
    params = parse_qsl(u.query, keep_blank_values=True)
    if any(k.lower() in _AFFILIATE_PARAMS for k, _ in params):
        url = urlunparse(u._replace(query=urlencode([(k, v) for k, v in params if k.lower() not in _AFFILIATE_PARAMS])))
    return None if is_affiliate_link(url) else url


def _clean_lines(raw: str) -> list[str]:
    """줄 정리: 남의 제휴 주소 지우기, 빈칸 합치기, 게시판 버튼 글자 빼기, ▶ / > 같은 게시판 표시는 '· ' 글머리로."""
    out: list[str] = []
    in_item = False  # 바로 위가 글머리(▶) 줄이거나 거기 딸린 줄인지
    for line in raw.splitlines():
        s = _INLINE_URL.sub(lambda m: strip_foreign_affiliate(m.group(0)) or "", line)
        s = re.sub(r"[ \t ]+", " ", s).strip()
        if not s or s in _NAV_LINES:
            continue
        if _QUOTE_MARK.match(s):
            s = _QUOTE_MARK.sub("", s)
            # 상품 줄(▶) 바로 아래의 '>' 줄은 그 상품에 딸린 설명(쿠폰·조건)이라 들여 써서 붙인다
            s = ("  └ " if in_item else "· ") + s if s else ""
        elif _ITEM_MARK.match(s):
            s = _ITEM_MARK.sub("", s)
            s = "· " + s if s else ""
            in_item = True
        else:
            in_item = False
        if s:
            out.append(s)
    return out


def _dedupe_lines(lines: list[str]) -> list[str]:
    """바로 위 줄 반복, 제목(첫 줄) 반복, 긴 문단 반복만 지운다.
    상품마다 붙는 짧은 줄('└ 쿠폰 할인 최대 7%')은 남긴다 — 첫 상품에만 남으면 쿠폰이 한 모델 얘기처럼 보인다."""
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if out and (ln == out[-1] or ln == out[0] or (len(ln) >= 40 and ln in seen)):
            continue
        out.append(ln)
        seen.add(ln)
    # 글 끝의 짧은 끝인사(작성자 잡담)는 뺀다. 숫자가 있으면 정보일 수 있어 둔다
    while len(out) > 1 and len(out[-1]) <= 30 and not re.search(r"\d", out[-1]) and _SIGN_OFF.search(out[-1]):
        out.pop()
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
    # 줄 중간에서 끊기지 않게 마지막 줄바꿈까지 (뒷부분에 줄바꿈이 없으면 마지막 빈칸까지: 낱말 중간은 피한다)
    if "\n" in cut[max_chars // 2 :]:
        cut = cut[: cut.rfind("\n")]
    elif " " in cut[max_chars // 2 :]:
        cut = cut[: cut.rfind(" ")]
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
        href = strip_foreign_affiliate(href)  # 남의 제휴 링크는 버리고, lptag 같은 추적값은 뗀다
        if href and href not in links:
            links.append(href)

    # 본문 텍스트: 줄 단위로 정리, URL 만 있는 줄은 빼고, 반복 줄·끝인사 제거
    lines = [ln for ln in _clean_lines(container.get_text("\n")) if not _URL_LINE.match(ln)]
    joined = "\n".join(_dedupe_lines(lines))
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
        # 관리자 확인(/ok)으로 저장돼 있던 예전 초안의 링크도 같은 기준으로 거른다
        links = [u for u in (strip_foreign_affiliate(x) for x in body.links) if u]
        return {
            "product": p,
            "body": body.text,
            "threads_body": _cut(body.text, THREADS_BODY_CHARS),  # 스레드용: 줄 끝(없으면 낱말 끝)에서 자름
            "images": body.images,
            "links": links,
            "source_url": p.extra.get("post_url") or p.url,
            "source_name": p.extra.get("source_label") or p.source,
        }

    def render(self, template: str, deal: Deal, body: PostBody, *, autoescape: bool) -> str:
        return self.renderer.render(template, autoescape=autoescape, **self.context(deal, body))
