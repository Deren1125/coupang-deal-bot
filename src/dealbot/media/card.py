"""딜 카드 이미지 — 인스타그램·스레드에 올릴 1080×1350(4:5) JPEG.

인스타그램은 사진 없는 글을 못 올리고 사진 규격(JPEG, 4:5 ~ 1.91:1, 320~1440px)이 있어 게시판 썸네일은 대부분 거절된다.
그래서 상품 사진에 상품명·가격·할인율을 얹은 카드를 봇이 직접 만들어 공개 도메인(/media/…)으로 서빙한다.
사진은 텔레그램 글에 쓴 확인된 상품 사진(app.deal_photo)만 받는다 — 여기서 따로 받아 오지 않는다 (로고·기본 이미지가 카드에 크게 들어가던 문제).
양식(layout): current = 처음 카드, A/B/C = 후보 양식 (media/card_variants.py), P = 종이 카드 (media/card_paper.py — 기본, 10/16).
Pillow 만 쓰고, 한글 글꼴은 서버(도커)의 나눔고딕이나 config 로 준 글꼴을 쓴다. 글꼴이 없으면 Pillow 기본 글꼴로라도 만든다.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps

from dealbot.models import Deal

log = logging.getLogger(__name__)

W, H = 1080, 1350
MARGIN = 72
# 등급 → 배지. top/must 는 쿠팡 시중가·믿을 만한 평소 가격보다 70%/50% 이상 싼 것 (TemplateRenderer.emphasis)
TIER_BADGES = {"top": "초특가", "must": "반값 핫딜"}
# 인스타 밖(블로그 내보내기·스레드)에 쓰는 카드의 바닥 안내 — '구매 링크는 프로필 링크에서' 는 인스타에서만 맞는 말
# (블로그는 글 안에, 스레드는 본문에 구매 링크가 있다)
NEUTRAL_CTA = "가격·재고는 올린 시점 기준이에요"
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
# 큰 가격용 아주 굵은 글꼴, 작은 안내 글자용 보통 굵기 (없으면 위 굵은 글꼴로)
HEAVY_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothicExtraBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumSquareEB.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
]
REGULAR_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "C:/Windows/Fonts/malgun.ttf",
]
Layout = Literal["current", "A", "B", "C", "P"]
LAYOUTS: tuple[str, ...] = ("current", "A", "B", "C", "P")


@dataclass(slots=True)
class CardInfo:
    """후보 양식(A/B/C) 카드에 쓰는 사실들 — 모두 데이터에서 계산한 값만 (insta_style.card_info 가 만든다).
    비교 가격은 글 첫 줄과 같은 근거(TemplateRenderer.reference): 쿠팡 최저가 또는 믿을 만한 평소 가격, 10% 이상 차이일 때만."""

    name: str
    price: int | None = None
    short: str = ""  # 한 줄용 짧은 이름 (수량은 남김, templates.short_name) — C 양식 아래 띠
    ref_price: int | None = None
    ref_label: str = ""  # '쿠팡 최저가' | '네이버 최저가'(쿠팡 딜) | '평소 가격'
    ref_text: str = ""  # 비교 가격 글 — 채널 글과 같은 말: '쿠팡 최저가 22,900원' | '평소 40,000원대' (평균은 가격대로만)
    ref_pct: int | None = None
    list_price: int | None = None  # 표시 정가 (근거가 약해 비교 가격이 없을 때만 보조로)
    list_pct: int | None = None
    low_label: str | None = None  # '우리가 본 최근 30일 중 가장 낮음 (10/15 14시 기준)' 처럼 기록으로 확인된 것만 (insta_style.low_text)
    # 이 상품의 바로 앞 가격 — 우리 가격 기록에서 (ig_scheduler.own_before). 취소선 '전 → 지금' 은 이 값이 있을 때만 쓴다
    # (표시광고법: 다른 판매처의 최저가·표시 정가를 취소선으로 그리면 이 상품의 종전 가격처럼 읽힌다)
    own_was: int | None = None
    own_was_label: str = ""  # 근거 '우리가 본 10/08 가격'
    ship: str = ""
    unit: str | None = None
    rating: float | None = None
    review_count: int | None = None
    shop_name: str = ""
    category: str = ""
    deal_no: int | None = None  # 핫딜 페이지(/d/번호)의 번호 — 프로필 링크에서 찾는 표시
    disclosure: str = ""  # 몰 고지의 짧은 꼴 ('쿠팡 파트너스 활동으로 수수료를 받습니다'). 몰 고지가 없는 딜(원본 링크)은 빈칸
    ad: str = ""  # 카드 안 고지 줄 '광고 · …' (공정위 지침: 사진 게시물은 사진 안에도). 비면 disclosure 로 만든다
    cta: str | None = None  # 바닥 구매 안내. None 이면 인스타용('구매 링크: 프로필 링크 → …'), 블로그·스레드 카드는 NEUTRAL_CTA


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


def find_font(explicit: str | None = None, candidates: list[str] | None = None) -> str | None:
    for path in ([explicit] if explicit else []) + (FONT_CANDIDATES if candidates is None else candidates):
        if path and Path(path).exists():
            return path
    return None


class DealCard:
    def __init__(
        self,
        out_dir: Path,
        *,
        font_path: str | None = None,
        style: CardStyle | None = None,
        layout: str = "current",
        font_heavy_path: str | None = None,
        font_regular_path: str | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.style = style or CardStyle()
        self.layout = layout if layout in LAYOUTS else "current"
        self.font_path = find_font(font_path)
        if self.font_path is None:
            log.warning("한글 글꼴을 찾지 못했습니다 — 카드의 한글이 깨질 수 있습니다 (도커는 나눔고딕 포함, 로컬은 card.font_path 설정)")
        self.font_heavy_path = find_font(font_heavy_path, HEAVY_FONT_CANDIDATES) or self.font_path
        self.font_regular_path = find_font(font_regular_path, REGULAR_FONT_CANDIDATES) or self.font_path

    # ------------------------------------------------------------ 글꼴
    def font(self, size: int, path: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        path = path or self.font_path
        if path:
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # 옛 Pillow
            return ImageFont.load_default()

    def font_heavy(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return self.font(size, self.font_heavy_path)

    def font_regular(self, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        return self.font(size, self.font_regular_path)

    # ------------------------------------------------------------ 그리기
    def render(
        self, deal: Deal, photo: bytes | None = None, *, shop_name: str | None = None, tier: str | None = None,
        layout: str | None = None, info: CardInfo | None = None, cta: str | None = None,
    ) -> bytes:
        """카드 JPEG 바이트. photo 는 확인된 상품 사진(deal_photo 결과, 없으면 글자 카드).
        tier: 글 첫 줄과 같은 등급(TemplateRenderer.deal_tier — 믿을 수 있는 평소 가격 기준). 모르면 '핫딜'만 단다
        (예전처럼 날것의 평균 대비 비율로 '역대급'을 붙이면 글과 어긋나는 근거 없는 말이 됨).
        layout: 없으면 이 카드의 기본 양식. A/B/C 는 info(CardInfo) 의 사실로 그린다.
        cta: 바닥 구매 안내를 바꿔 쓴다 (블로그·스레드 카드는 NEUTRAL_CTA — 없으면 인스타용 '프로필 링크' 안내)."""
        layout = layout or self.layout
        if layout in ("A", "B", "C", "P"):
            from dealbot.media.card_variants import render_variant

            if info is None:
                info = CardInfo(name=deal.product.name, price=deal.product.price if deal.product.has_price else None,
                                shop_name=shop_name or "")
            if cta is not None:
                info = replace(info, cta=cta)
            img = render_variant(self, layout, info, photo)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            return buf.getvalue()
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

        # 배지: 채널 글 첫 줄 배지(🔥 초특가 / 👍 강추)와 같은 등급으로
        badge = TIER_BADGES.get(tier or "", "핫딜")
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
                # 표시 정가는 취소선 없이 '정가 N원' (우리 가격 기록이 아닌 값을 이 상품의 종전 가격처럼 그리지 않는다 — 표시광고법)
                orig = f"정가 {p.original_price:,}원"
                of = self.font(40)
                oy = y + 48
                draw.text((px, oy), orig, font=of, fill=st.muted)
                px += self._width(draw, orig, of) + 24
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
            bits.append(f"별점 {p.rating}" + (f" ({p.review_count:,})" if p.review_count else ""))  # ★ 기호는 안 씀 (H-08)
        if bits and y + 60 < H - 170:
            draw.text((MARGIN, y), "  ·  ".join(bits), font=self.font(36), fill=st.muted)

        # 바닥: 브랜드 + 안내
        ff = self.font(34)
        draw.line((MARGIN, H - 150, W - MARGIN, H - 150), fill="#E5E5E5", width=3)
        draw.text((MARGIN, H - 120), st.footer, font=ff, fill=st.ink)
        note = st.footer_note if cta is None else cta
        if note:
            note_w = self._width(draw, note, ff)
            draw.text((W - MARGIN - note_w, H - 120), note, font=ff, fill=st.muted)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()

    def save(
        self, deal: Deal, photo: bytes | None = None, *, key: str | None = None, shop_name: str | None = None,
        tier: str | None = None, layout: str | None = None, info: CardInfo | None = None, cta: str | None = None,
    ) -> Path:
        """카드를 만들어 out_dir 에 저장하고 경로를 돌려준다. 파일명은 상품 키(+양식·바닥 안내) 해시라 같은 딜은 덮어쓴다
        (인스타 카드와 스레드 카드는 바닥 안내가 달라 파일도 따로)."""
        key = key or deal.product.product_id
        layout = layout or self.layout
        if layout != "current":
            key = f"{key}|{layout}"
        if cta is not None:
            key = f"{key}|cta:{cta}"
        name = hashlib.sha1(key.encode()).hexdigest()[:16] + ".jpg"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / name
        path.write_bytes(self.render(deal, photo, shop_name=shop_name, tier=tier, layout=layout, info=info, cta=cta))
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
