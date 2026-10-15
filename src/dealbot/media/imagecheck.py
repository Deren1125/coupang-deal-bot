"""발행 전 사진 검사: 진짜 사진인지, 너무 작거나(게시판 목록 썸네일) 길쭉하지 않은지 보고 JPEG 로 맞춘다.

텔레그램·스레드·블로그에 깨지거나 흐린 사진이 올라가지 않게 (T-09, TH-01): 디코딩되고 짧은 변 600px 이상인 상품 사진만 쓰고,
아니면 사진 없이 올린다. 로고·기본 배너·게시판 목록 썸네일은 상품 사진으로 치지 않는다.
"""

from __future__ import annotations

import io
import logging
import re
import warnings
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

log = logging.getLogger(__name__)

# 커뮤니티 게시판의 목록 썸네일 (작고 흐리며, 외부 서버가 가져가지 못하게 막혀 있는 경우가 많음)
BOARD_HOSTS = ("ppomppu.co.kr", "ruliweb.com", "ruliweb.net", "algumon.com", "quasarzone.com", "fmkorea.com")
# 상품 사진이 아닌 몰 공통 이미지 (로고·기본 배너·공유용 대표 이미지·'이미지 준비중').
# 낱말 단위로만 본다: 'logo_coupang.png'·'default_banner.jpg'·'og_image.jpg' 는 걸리고 'weighted-blanket.jpg'·'/common/goods/…' 는 안 걸림
_GENERIC_IMAGE = re.compile(
    r"(?<![a-z0-9])(?:logos?|banners?|blank|default|placeholder|sprites?|icons?)(?:[_-]?(?:img|image))?(?![a-z])"
    r"|(?<![a-z0-9])no[_-]?(?:image|img)(?![a-z])"
    r"|(?<![a-z0-9])og[_-]?(?:img|image)(?![a-z])"
    r"|(?<![a-z0-9])(?:sns[_-]?)?share(?![a-z])",
    re.I,
)
# 쿠팡 CDN 썸네일 주소의 크기 칸 ('/thumbnails/remote/230x230ex/') — 큰 크기로 바꿔 받는다
_COUPANG_THUMB = re.compile(r"(/thumbnails/remote/)(\d+)x(\d+)ex/", re.I)
COUPANG_IMAGE_SIDE = 1000


def is_board_thumb(url: str | None) -> bool:
    """커뮤니티 게시판 서버의 사진인지 (목록 썸네일·외부에서 못 가져가게 막힌 사진 → 상품 사진으로 안 씀).
    쇼핑몰 CDN 은 큰 상품 사진도 '/thumbnails/' 아래에 두는 곳이 있어(쿠팡·무신사 등) 주소의 '/thumb' 로 거르지 않고,
    받아 본 뒤 clean_image(짧은 변 600px) 가 크기로 판단한다."""
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in BOARD_HOSTS)


def is_generic_image(url: str | None) -> bool:
    """로고·기본 배너처럼 어느 상품에나 붙는 몰 공통 이미지 주소인지 (상품 사진으로 쓰지 않음)."""
    if not url:
        return False
    u = urlparse(url)
    return bool(_GENERIC_IMAGE.search(f"{u.path}?{u.query}"))


def coupang_image_url(url: str | None, side: int = COUPANG_IMAGE_SIDE) -> str | None:
    """쿠팡 CDN 썸네일 주소를 side x side 로 ('…/remote/230x230ex/…' → '…/remote/1000x1000ex/…'). 이미 크거나 다른 주소면 그대로."""
    if not url:
        return url

    def _bigger(m: re.Match[str]) -> str:
        if min(int(m.group(2)), int(m.group(3))) >= side:
            return m.group(0)
        return f"{m.group(1)}{side}x{side}ex/"

    return _COUPANG_THUMB.sub(_bigger, url, count=1)


def naver_image_url(url: str | None, side: int = 1000) -> str | None:
    """네이버 이미지 CDN(*.pstatic.net) 주소의 작은 크기 칸('?type=m510', 'f640_640')을 떼어 원본 크기로. 이미 크거나 다른 주소면 그대로.
    스토어 페이지 og:image 는 510px 축소본이라 그대로 받으면 짧은 변 600px 검사에서 떨어진다."""
    if not url:
        return url
    u = urlparse(url)
    host = u.netloc.lower().split(":")[0]
    if not (host == "pstatic.net" or host.endswith(".pstatic.net")):
        return url
    q = parse_qsl(u.query, keep_blank_values=True)
    sizes = [int(n) for k, v in q if k == "type" for n in re.findall(r"\d+", v)]
    if not sizes or max(sizes) >= side:
        return url
    return urlunparse(u._replace(query=urlencode([(k, v) for k, v in q if k != "type"])))


def clean_image(data: bytes | None, *, min_side: int = 600, max_aspect: float = 2.2, max_side: int = 1280) -> bytes | None:
    """쓸 만한 상품 사진이면 JPEG 바이트(긴 변 max_side 이하)를, 아니면 None.
    - 이미지가 아닌 응답(HTML 오류 페이지 등), 짧은 변 min_side 미만(목록 썸네일·저해상도), 가로세로 비 max_aspect 초과(배너) 는 버린다.
    - 휴대폰·게시판 사진의 EXIF 방향을 반영해 바로 세운다 (다시 저장하면 방향 정보가 빠져 옆으로 누운 채 남음)."""
    if not data:
        return None
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as e:  # noqa: BLE001
        log.debug("not an image: %s", e)
        return None
    w, h = img.size
    if min(w, h) < min_side or max(w, h) / max(1, min(w, h)) > max_aspect:
        log.info("photo rejected %sx%s (짧은 변 %dpx 이상, 비율 %.1f 이하만 씀)", w, h, min_side, max_aspect)
        return None
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode == "L":
        img = img.convert("RGB")
    if max(w, h) > max_side:
        img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


# ---------------------------------------------------------------- 주인이 링크와 같이 보낸 네이버 딜 상품 사진 (주인 결정 10/15)
OWNER_PHOTO_MAX_BYTES = 10_000_000  # 텔레그램에서 받는 파일 한도 (받기 전 크기·받은 뒤 길이 둘 다)
OWNER_PHOTO_MAX_PIXELS = 40_000_000  # 디코딩 폭탄 방지 (휴대폰 사진은 1,200만~5,000만 화소 안팎이라 넉넉히 4,000만까지)
OWNER_PHOTO_FORMATS = ("JPEG", "PNG", "WEBP")
OWNER_PHOTO_TOO_BIG = "사진 파일이 너무 커요 (10MB 이하로 보내 주세요)"
OWNER_PHOTO_NOT_IMAGE = "사진으로 열리지 않아요 — JPG·PNG·WEBP 사진만 받아요 (HEIC·GIF·동영상은 안 돼요)"


def owner_photo(data: bytes | None, *, min_side: int = 600, max_aspect: float = 2.2) -> tuple[bytes | None, str]:
    """주인이 보낸 상품 사진을 검사해 (JPEG 바이트, "") 또는 (None, 주인에게 보일 이유).
    상품 사진 규칙은 clean_image 와 같다 (짧은 변 min_side 이상, 비율 max_aspect 이하, 긴 변 1280 이하로 줄임, EXIF 방향 반영).
    다시 저장하므로 EXIF(위치·기기 정보)는 남지 않는다. 열기 전에 파일 크기·형식·화소 수를 먼저 본다 (큰 그림으로 서버가 멈추지 않게)."""
    if not data:
        return None, OWNER_PHOTO_NOT_IMAGE
    if len(data) > OWNER_PHOTO_MAX_BYTES:
        return None, OWNER_PHOTO_TOO_BIG
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:  # 머리글만 읽음 (그림은 아직 안 풂)
                fmt, (w, h) = img.format, img.size
    except Exception as e:  # noqa: BLE001 — HEIC(플러그인 없음)·깨진 파일·화소 폭탄 경고 모두 '못 씀'
        if "DecompressionBomb" in type(e).__name__:
            return None, "사진 해상도가 너무 커요"
        log.info("owner photo not an image: %s", type(e).__name__)
        return None, OWNER_PHOTO_NOT_IMAGE
    if fmt not in OWNER_PHOTO_FORMATS:
        return None, OWNER_PHOTO_NOT_IMAGE
    if w * h > OWNER_PHOTO_MAX_PIXELS:
        return None, f"사진 해상도가 너무 커요 ({w}×{h})"
    if min(w, h) < min_side:
        return None, f"사진이 너무 작아요 ({w}×{h}) — 짧은 변이 {min_side}px 이상이어야 해요"
    if max(w, h) / max(1, min(w, h)) > max_aspect:
        return None, f"사진이 너무 길쭉해요 ({w}×{h}) — 가로세로 {max_aspect:g}배 이하로 (화면 캡처면 상품 부분만 잘라서)"
    out = clean_image(data, min_side=min_side, max_aspect=max_aspect)
    if out is None:
        return None, OWNER_PHOTO_NOT_IMAGE
    return out, ""
