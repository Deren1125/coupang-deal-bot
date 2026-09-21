"""딜 카드 이미지 — 인스타그램·스레드에 올릴 1080×1350(4:5) JPEG.

인스타그램은 사진 없는 글을 못 올리고 사진 규격(JPEG, 4:5 ~ 1.91:1, 320~1440px)이 있어 게시판 썸네일은 대부분 거절된다.
그래서 상품 사진(있으면)에 상품명·가격·할인율을 얹은 카드를 봇이 직접 만들어 공개 도메인(/media/…)으로 서빙한다.
Pillow 만 쓰고, 한글 글꼴은 서버(도커)의 나눔고딕이나 config 로 준 글꼴을 쓴다. 글꼴이 없으면 Pillow 기본 글꼴로라도 만든다.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from dealbot.models import Deal

log = logging.getLogger(__name__)

W, H = 1080, 1350
MARGIN = 72
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]


@dataclass(slots=True)
class CardStyle:
    background: str = "#FFFFFF"
    ink: str = "#111111"
    muted: str = "#6B6B6B"
    accent: str = "#E8442B"  # 가격·할인율
    badge_bg: str = "#111111"
    badge_ink: str = "#FFFFFF"
    footer: str = "오늘의 핫딜"
    footer_note: str = "구매 링크는 프로필 링크에서"


def find_font(explicit: str | None = None) -> str | None:
    for path in ([explicit] if explicit else []) + FONT_CANDIDATES:
        if path and Path(path).exists():
            return path
    return None


class DealCard:
    def __init__(self, out_dir: Path, *, font_path: str | None = None, style: CardStyle | None = None) -> None:
        self.out_dir = Path(out_dir)
        self.style = style or CardStyle()
        self.font_path = find_font(font_path)
        if self.font_path is None:
            log.warning("한글 글꼴을 찾지 못했습니다 — 카드의 한글이 깨질 수 있습니다 (도커는 나눔고딕 포함, 로컬은 card.font_path 설정)")

    # ------------------------------------------------------------ 글꼴
    def font(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        if self.font_path:
            try:
                return ImageFont.truetype(self.font_path, size)
            except OSError:
                pass
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # 옛 Pillow
            return ImageFont.load_default()

    # ------------------------------------------------------------ 그리기
    def render(self, deal: Deal, photo: bytes | None = None, *, shop_name: str | None = None) -> bytes:
        """카드 JPEG 바이트. photo 는 상품 사진 원본(없으면 글자만)."""
        p = deal.product
        st = self.style
        img = Image.new("RGB", (W, H), st.background)
        draw = ImageDraw.Draw(img)

        # 사진 영역(위 절반). 사진이 없으면 연한 배경에 큰 상품명
        photo_h = int(H * 0.50)
        placed = False
        if photo:
            try:
                src = Image.open(io.BytesIO(photo))
                src = ImageOps.exif_transpose(src).convert("RGB")
                fitted = ImageOps.contain(src, (W - 2 * MARGIN, photo_h - 2 * MARGIN))
                x = (W - fitted.width) // 2
                y = MARGIN + (photo_h - 2 * MARGIN - fitted.height) // 2
                img.paste(fitted, (x, y))
                placed = True
            except Exception as e:  # noqa: BLE001 — 깨진 사진은 글자 카드로
                log.warning("card photo unusable: %s", e)
        if not placed:
            draw.rectangle((0, 0, W, photo_h), fill="#F4F4F2")
            self._text_block(draw, p.name, self.font(64), st.ink, MARGIN, photo_h // 2 - 100, W - 2 * MARGIN, max_lines=4)

        # 배지
        badge = "핫딜"
        pct = max(deal.verdict.below_avg_pct or 0.0, deal.verdict.below_market_pct or 0.0)
        if pct >= 70:
            badge = "역대급 가격"
        elif pct >= 50:
            badge = "반값 핫딜"
        self._badge(draw, badge, self.font(34), MARGIN, photo_h + 40)

        y = photo_h + 130
        # 상품명 (사진이 있을 때만 여기서. 없으면 위에 크게 썼음)
        if placed:
            y = self._text_block(draw, p.name, self.font(46), st.ink, MARGIN, y, W - 2 * MARGIN, max_lines=2) + 30

        # 가격 줄
        if p.has_price:
            price = f"{p.price:,}원"
            price_font = self.font(96)
            draw.text((MARGIN, y), price, font=price_font, fill=st.accent)
            px = MARGIN + self._width(draw, price, price_font) + 28
            rate = p.effective_discount_rate()
            if p.original_price and p.original_price > p.price:
                orig = f"{p.original_price:,}원"
                of = self.font(40)
                oy = y + 48
                draw.text((px, oy), orig, font=of, fill=st.muted)
                ow = self._width(draw, orig, of)
                draw.line((px, oy + 24, px + ow, oy + 24), fill=st.muted, width=4)
                px += ow + 24
            if rate:
                draw.text((px, y + 36), f"{rate:.0f}%↓", font=self.font(52), fill=st.accent)
            y += 130
        elif not placed:
            y += 10

        # 부가 정보: 몰 · 로켓/무료배송 · 별점 (바닥 줄과 겹치면 생략)
        bits: list[str] = []
        shop_name = shop_name or p.extra.get("shop_name") or p.shop
        if shop_name and shop_name != "unknown":
            bits.append(str(shop_name))
        if p.is_rocket:
            bits.append("로켓배송")
        elif p.is_free_shipping or (p.shipping and "무료" in p.shipping):
            bits.append("무료배송")
        if p.rating:
            bits.append(f"★ {p.rating}" + (f" ({p.review_count:,})" if p.review_count else ""))
        if bits and y + 60 < H - 170:
            draw.text((MARGIN, y), "  ·  ".join(bits), font=self.font(36), fill=st.muted)

        # 바닥: 브랜드 + 안내
        ff = self.font(34)
        draw.line((MARGIN, H - 150, W - MARGIN, H - 150), fill="#E5E5E5", width=3)
        draw.text((MARGIN, H - 120), st.footer, font=ff, fill=st.ink)
        note_w = self._width(draw, st.footer_note, ff)
        draw.text((W - MARGIN - note_w, H - 120), st.footer_note, font=ff, fill=st.muted)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()

    def save(self, deal: Deal, photo: bytes | None = None, *, key: str | None = None, shop_name: str | None = None) -> Path:
        """카드를 만들어 out_dir 에 저장하고 경로를 돌려준다. 파일명은 상품 키 해시라 같은 딜은 덮어쓴다."""
        key = key or deal.product.product_id
        name = hashlib.sha1(key.encode()).hexdigest()[:16] + ".jpg"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / name
        path.write_bytes(self.render(deal, photo, shop_name=shop_name))
        return path

    def prune(self, keep_days: int = 7) -> int:
        """오래된 카드 파일 정리."""
        import time

        cutoff = time.time() - keep_days * 86400
        n = 0
        for f in self.out_dir.glob("*.jpg"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    n += 1
            except OSError:
                pass
        return n

    # ------------------------------------------------------------ 보조
    @staticmethod
    def _width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        return int(right - left)

    def _text_block(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        fill: str,
        x: int,
        y: int,
        max_w: int,
        *,
        max_lines: int,
    ) -> int:
        """글자 단위로 줄바꿈(한글은 띄어쓰기가 드물다). 마지막 줄은 …. 다음 y 를 돌려준다."""
        lines: list[str] = []
        cur = ""
        for ch in text.strip():
            trial = cur + ch
            if self._width(draw, trial, font) <= max_w:
                cur = trial
                continue
            lines.append(cur.rstrip())
            cur = ch.lstrip()
            if len(lines) == max_lines:
                break
        if len(lines) < max_lines and cur:
            lines.append(cur)
        if len(lines) == max_lines and (cur and lines[-1] != cur or self._width(draw, text, font) > max_w * max_lines):
            last = lines[-1]
            while last and self._width(draw, last + "…", font) > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
        line_h = int(getattr(font, "size", 40) * 1.35)
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            y += line_h
        return y

    def _badge(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, x: int, y: int) -> None:
        w = self._width(draw, text, font)
        pad_x, pad_y = 22, 12
        h = int(getattr(font, "size", 34) * 1.2)
        draw.rounded_rectangle((x, y, x + w + 2 * pad_x, y + h + 2 * pad_y), radius=14, fill=self.style.badge_bg)
        draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=self.style.badge_ink)
