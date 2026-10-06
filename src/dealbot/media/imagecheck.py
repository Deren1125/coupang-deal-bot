"""발행 전 사진 검사: 진짜 사진인지, 너무 작거나(게시판 목록 썸네일) 길쭉하지 않은지 보고 JPEG 로 맞춘다."""

from __future__ import annotations

import io
import logging
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# 커뮤니티 게시판의 목록 썸네일 (작고 흐리며, 외부 서버가 가져가지 못하게 막혀 있는 경우가 많음)
BOARD_HOSTS = ("ppomppu.co.kr", "ruliweb.com", "ruliweb.net", "algumon.com", "quasarzone.com", "fmkorea.com")


def is_board_thumb(url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    return any(host.endswith(h) for h in BOARD_HOSTS) or "/thumb" in url.lower()


def clean_image(data: bytes | None, *, min_side: int = 300, max_aspect: float = 2.2, max_side: int = 1280) -> bytes | None:
    """쓸 만한 상품 사진이면 JPEG 바이트(긴 변 max_side 이하)를, 아니면 None.
    - 이미지가 아닌 응답(HTML 오류 페이지 등), 짧은 변 min_side 미만(목록 썸네일), 가로세로 비 max_aspect 초과(배너) 는 버린다."""
    if not data:
        return None
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:  # noqa: BLE001
        log.debug("not an image: %s", e)
        return None
    w, h = img.size
    if min(w, h) < min_side or max(w, h) / max(1, min(w, h)) > max_aspect:
        log.debug("image rejected %sx%s", w, h)
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
