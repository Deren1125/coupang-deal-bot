"""딜 카드 이미지."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw

from dealbot.cli import sample_deal
from dealbot.media.card import DealCard, H, W


def _photo() -> bytes:
    im = Image.new("RGB", (640, 480), "#88AACC")
    ImageDraw.Draw(im).ellipse((100, 50, 500, 400), fill="#FFD166")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_card_renders_jpeg_with_and_without_photo(tmp_path: Path) -> None:
    card = DealCard(tmp_path / "media")
    deal = sample_deal()
    deal.product.name = "[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L 화이트 색상 대용량 보냉 텀블러 아주 긴 이름"
    for photo in (_photo(), None, b"not an image"):
        raw = card.render(deal, photo, shop_name="쿠팡")
        im = Image.open(io.BytesIO(raw))
        assert im.format == "JPEG" and im.size == (W, H)


def test_card_save_is_deterministic_and_prunes(tmp_path: Path) -> None:
    card = DealCard(tmp_path / "media")
    a = card.save(sample_deal(), None)
    b = card.save(sample_deal(), _photo())
    assert a == b and a.suffix == ".jpg" and a.parent == tmp_path / "media" and a.stat().st_size > 1000
    other = card.save(sample_deal(), None, key="other")
    assert other != a
    assert card.prune(keep_days=0) >= 2 and not a.exists()


def test_prune_keeps_blog_paste_photos(tmp_path: Path) -> None:
    """주인이 붙여 넣은 네이버 글이 쓰는 blog_*.jpg 는 카드 정리에서 빠진다 (Blog-Auto 가 90일 관리 · 10/27)."""
    import os

    media = tmp_path / "media"
    card = DealCard(media)
    a = card.save(sample_deal(), None)
    blog = media / "blog_finance_12_1_abcdef12.jpg"
    blog.write_bytes(b"x")
    old = 1_000_000_000
    os.utime(blog, (old, old))
    os.utime(a, (old, old))
    assert card.prune(keep_days=7) == 1 and not a.exists() and blog.exists()
