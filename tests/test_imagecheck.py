"""사진 검사: 게시판 목록 썸네일(작음)·이미지가 아닌 응답·배너는 버리고, 쓸 사진은 JPEG 로."""

from __future__ import annotations

import io

from PIL import Image

from dealbot.enrich import PageEnricher, PageMeta
from dealbot.media.imagecheck import clean_image, is_board_thumb
from dealbot.models import Product


def _png(w: int, h: int, mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (w, h), (200, 50, 50, 255) if mode == "RGBA" else (200, 50, 50)).save(out, "PNG")
    return out.getvalue()


def test_clean_image_rules() -> None:
    assert clean_image(_png(120, 120)) is None  # 게시판 썸네일 크기
    assert clean_image(b"<html>404</html>") is None
    assert clean_image(_png(1600, 400)) is None  # 배너
    out = clean_image(_png(2400, 2000))
    assert out and Image.open(io.BytesIO(out)).format == "JPEG" and max(Image.open(io.BytesIO(out)).size) == 1280


def test_board_thumbnail_is_replaced_by_page_image() -> None:
    assert is_board_thumb("https://cdn2.ppomppu.co.kr/zboard/data3/2026/1006/m_20261006_abc.jpg")
    p = Product(source="ppomppu", product_id="gmarket:1", shop="gmarket", name="x", price=10000, url="https://item.gmarket.co.kr/Item?goodscode=1",
                image_url="https://cdn2.ppomppu.co.kr/zboard/data3/small.jpg")
    filled = PageEnricher.apply(p, PageMeta(image="https://gdimg.gmarket.co.kr/1/big.jpg"))
    assert "image_url" in filled and p.image_url == "https://gdimg.gmarket.co.kr/1/big.jpg"
