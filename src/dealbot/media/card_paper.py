"""인스타그램 '종이' 카드 (instagram.style: P — 주인 결정 10/16, 시안 '1+2 섞은 안' · 가격 C).

카드가 'AI 가 만든 것 같다'는 말에 바꾼 그림. 크림색 종이 결, 사진 위 마스킹 테이프(분류 이름), 핫딜 번호를 적은 포스트잇 한 장,
가는 세리프 숫자(Bodoni Moda) 가격과 그 아래 빨간 펜 밑줄 한 줄, 빨간 손글씨 메모 하나(화살표), 표 대신 체크 표시 사실 줄.

- 그리는 글은 모두 SlideSpec 에서 (insta_style.validate 가 spec.texts() 로 고지·근거 없는 말을 본다). 사실 줄·메모는
  card_slides 의 데이터 함수(deal_rows · unit_parts · ad_of)와 CardInfo 의 비교·기록 값으로만 만든다 (deal_spec).
- 맨 위 첫 줄은 언제나 고지 '광고 · …' — 검은 '광고' 칩 + 나머지 글 (spec.ad 를 그대로 나눠 그림).
- '#번호' 와 '프로필 링크 → 링크 페이지' 는 포스트잇 한 곳에만, 카드마다 한 번. 번호가 없는 인스타 카드는 포스트잇 없이 바닥
  오른쪽에 '구매 링크는 프로필 링크에서' 한 번, 인스타 밖(스레드·블로그) 카드는 바닥 오른쪽에 spec.footer (NEUTRAL_CTA) 만.
- 사진이 없으면(네이버 글자 카드·스레드·블로그) 사진 자리에 같은 종이의 이름 카드 — 꾸민 그림·가짜 사진은 없다.
- 같은 딜이면 늘 같은 그림: 무늬·기울기는 spec 에서 만든 씨앗의 random.Random 으로만 (Image.effect_noise 는 안 씀).
- 2배 크기로 그리고 마지막에 줄인다 (펜 선·기울인 종이를 깨끗하게). 글꼴은 media/fonts (OFL) — 모듈에서 한 번만 연다.
- 360px 폭(휴대폰 격자)에서도 읽히게 가장 작은 글이 30px (1080 기준).
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

if TYPE_CHECKING:
    from dealbot.media.card import CardInfo
    from dealbot.media.card_slides import SlideSpec

log = logging.getLogger(__name__)

LAYOUT = "P"  # config instagram.style 값
W, H = 1080, 1350
SS = 2  # 2배로 그리고 마지막에 줄임
M = 60  # 바깥 여백 (프로필 격자 3:4 잘림 34px 보다 넉넉히)
BRAND = "데미얌의 쇼핑일기"
GUIDE = "프로필 링크 → 링크 페이지"  # 포스트잇의 구매 안내 (번호와 함께 한 번)
GUIDE_NO_NUMBER = "구매 링크는 프로필 링크에서"  # 번호 없는 인스타 카드의 바닥 안내 (B 카드·card_slides.deal_spec 과 같은 말)
STICKY_LABEL = "번호"
MIN_TEXT = 30  # 가장 작은 글 (1080 기준 — 360px 화면에서 10px)

BRAND_RED = (220, 35, 40)
PEN_RED = (204, 36, 40)
INK = (30, 26, 22)
SUB = (112, 102, 90)
QUAL = (100, 96, 90)  # 기준 시각 '(10/16 07시 기준)' — 근거라 흐리게 하지 않는다

# ---------------------------------------------------------------- 글꼴
FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = {
    "sb": "Pretendard-SemiBold.otf",
    "md": "Pretendard-Medium.otf",
    "num": "BodoniModa-Medium-opsz11.ttf",  # 가격 숫자·'#번호'
    "hand": "GamjaFlower-Regular-KSX1001.ttf",  # 빨간 펜 메모·테이프 글씨
}
_FALLBACK = {"num": "sb", "hand": "md", "md": "sb"}
Font = ImageFont.FreeTypeFont


class FontMissing(RuntimeError):
    """한글 글꼴이 하나도 없음 — 고지가 빈 네모로 나오는 카드를 올리지 않게 그리기를 멈춘다 (make_card·캐러셀이 건너뜀)."""


@lru_cache(maxsize=64)
def _font(key: str, px: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / FONT_FILES[key]), px)
    except OSError:
        if key in _FALLBACK:
            return _font(_FALLBACK[key], px)
        from dealbot.media.card import find_font

        path = find_font(None)
        if path:
            try:
                return ImageFont.truetype(path, px)
            except OSError:
                pass
        # Pillow 기본 글꼴엔 한글이 없다 → '광고 · …' 고지가 빈 네모. 그런 카드는 그리지 않는다
        raise FontMissing(f"paper card font missing: {FONT_FILES[key]} (no Korean system font either)") from None


def F(key: str, size: float) -> ImageFont.FreeTypeFont:
    """1배 크기 size 의 글꼴 (2배 캔버스용으로 연다). 크기는 1배 정수로 맞춤 — 글꼴 캐시가 연속 값으로 불어나지 않게."""
    return _font(key, max(1, int(round(size))) * SS)


_GLYPH: dict[tuple[str, str], bool] = {}


def _ink_of(font: Font, ch: str) -> bytes:
    im = Image.new("L", (S(60), S(60)), 0)
    ImageDraw.Draw(im).text((S(4), S(4)), ch, font=font, fill=255)
    return im.tobytes()


def covers(key: str, text: str) -> bool:
    """이 글꼴에 text 의 글자가 다 있나 (부분 글꼴 손글씨 — 없는 글자는 빈 네모로 나옴)."""
    f = F(key, 40)
    if not isinstance(f, ImageFont.FreeTypeFont):
        return False
    notdef = None
    for ch in set(text):
        if ch.isspace():
            continue
        k = (key, ch)
        if k not in _GLYPH:
            if notdef is None:
                notdef = _ink_of(f, "￿")
            _GLYPH[k] = _ink_of(f, ch) != notdef
        if not _GLYPH[k]:
            return False
    return True


def hand(text: str) -> str:
    """손글씨 글꼴 키 — 그 글꼴에 없는 글자가 있으면 Pretendard."""
    return "hand" if covers("hand", text) else "md"


def S(v: float) -> int:
    return int(round(v * SS))


def tw(font: Font, s: str) -> float:
    """1배 기준 글 너비."""
    return font.getlength(s) / SS if s else 0.0


def text(d: ImageDraw.ImageDraw, x: float, y: float, s: str, font: Font, fill: object, anchor: str = "ls") -> None:
    if s:
        d.text((S(x), S(y)), s, font=font, fill=fill, anchor=anchor)


# ---------------------------------------------------------------- 색
PALETTES = {
    "butter": dict(paper=(246, 240, 229), panel=(237, 226, 203), sticky=(255, 226, 132), tape=(238, 200, 186)),
    "blush": dict(paper=(247, 241, 237), panel=(241, 225, 219), sticky=(252, 212, 204), tape=(196, 219, 205)),
    "sage": dict(paper=(241, 242, 234), panel=(221, 230, 214), sticky=(200, 228, 203), tape=(242, 210, 176)),
    "powder": dict(paper=(240, 242, 245), panel=(220, 229, 239), sticky=(204, 222, 244), tape=(242, 221, 160)),
}
CATEGORY_THEME = [
    (("로켓프레시", "식품", "신선", "간식", "음료", "과자", "주방", "생수", "커피", "TOP"), "butter"),
    (("유아", "출산", "패션", "의류", "뷰티", "화장품", "잡화", "기저귀"), "blush"),
    (("생활", "홈", "인테리어", "반려", "건강", "욕실", "세제", "휴지", "물티슈", "생필품"), "sage"),
    (("가전", "디지털", "컴퓨터", "모바일", "자동차", "스포츠", "캠핑", "완구", "문구", "도서"), "powder"),
]


def theme_of(category: str, rng: random.Random) -> dict[str, tuple[int, int, int]]:
    """분류 이름으로 색 (데이터에서). 모르면 씨앗으로 고름 (같은 딜이면 같은 색)."""
    for keys, name in CATEGORY_THEME:
        if any(k in (category or "") for k in keys):
            return PALETTES[name]
    return PALETTES[rng.choice(list(PALETTES))]


def ink(pct: float, bg: tuple[int, int, int]) -> tuple[int, ...]:
    return tuple(int(round(i * pct + b * (1 - pct))) for i, b in zip(INK, bg, strict=True))


def seed_of(*parts: object) -> random.Random:
    """같은 딜·같은 장이면 같은 씨앗 (번호가 없을 수도 있어 이름·가격도 같이)."""
    key = "|".join(str(p) for p in parts)
    return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:12], 16))


# ---------------------------------------------------------------- 종이 결 (고정 씨앗 — 늘 같은 무늬)
def _noise(rng: random.Random, size: tuple[int, int], sigma: float) -> Image.Image:
    """128 둘레의 잔무늬 (L). 고른 난수(표준편차 ≈ 73.9)를 sigma 로 줄임."""
    w, h = max(1, size[0]), max(1, size[1])
    k = sigma / 73.9
    lut = [max(0, min(255, int(round(128 + (v - 127.5) * k)))) for v in range(256)]
    return Image.frombytes("L", (w, h), rng.randbytes(w * h)).point(lut)


@lru_cache(maxsize=1)
def _grain() -> Image.Image:
    """2배 캔버스 크기의 종이 결 (L, 평균 128). 잔결 + 얼룩. 한 번 만들어 모든 카드가 잘라 씀."""
    w, h = S(W), S(H)
    rng = random.Random(20261016)
    fine = _noise(rng, (w // 2, h // 2), 34).resize((w, h), Image.Resampling.BILINEAR)
    mottle = _noise(rng, (w // 48 + 2, h // 48 + 2), 26).resize((w, h), Image.Resampling.BICUBIC)
    mottle = mottle.filter(ImageFilter.GaussianBlur(max(2, w // 60)))
    return Image.blend(fine, mottle, 0.5)


def _grain_crop(size: tuple[int, int], corner: int = 0) -> Image.Image:
    g = _grain()
    w, h = min(size[0], g.width), min(size[1], g.height)
    box = (0, 0, w, h) if corner == 0 else (g.width - w, g.height - h, g.width, g.height)
    out = g.crop(box)
    return out if out.size == size else out.resize(size, Image.Resampling.BILINEAR)


def paper(size: tuple[int, int], color: tuple[int, int, int], strength: float = 0.30, corner: int = 0) -> Image.Image:
    base = Image.new("RGB", size, color)
    g = _grain_crop(size, corner)
    out = ImageChops.add(base, Image.merge("RGB", (g, g, g)), scale=1.0, offset=-128)
    return Image.blend(base, out, strength)


# ---------------------------------------------------------------- 사진
def open_photo(photo: bytes | None) -> Image.Image | None:
    """확인된 판매처 사진 → RGB (투명 PNG 는 흰 바탕에). 깨졌으면 None (사진 없는 카드로)."""
    if not photo:
        return None
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo)))
        if img.mode in ("RGBA", "LA", "P", "PA"):
            img = img.convert("RGBA")
            bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
            bg.alpha_composite(img)
            img = bg
        img = img.convert("RGB")
        if min(img.size) < 8:
            return None
        return img
    except Exception as e:  # noqa: BLE001 — 깨진 사진은 사진 없는 카드로
        log.warning("paper card photo unusable: %s", e)
        return None


def is_cutout(photo: Image.Image) -> bool:
    """흰 스튜디오 바탕 사진인가 (테두리가 밝고 고름)."""
    im = photo.convert("L")
    w, h = im.size
    b = max(2, min(w, h) // 40)
    strips = [im.crop((0, 0, w, b)), im.crop((0, h - b, w, h)), im.crop((0, 0, b, h)), im.crop((w - b, 0, w, h))]
    means = [ImageStat.Stat(s).mean[0] for s in strips]
    stds = [ImageStat.Stat(s).stddev[0] for s in strips]
    return min(means) > 236 and max(stds) < 14


def product_mask(photo: Image.Image, thresh: int = 14) -> Image.Image | None:
    """테두리에서 흰 바탕을 채워 상품 모양(255 = 상품). 작은 사본에서 구해 키움 (채우기가 느려서). 그럴듯하지 않으면 None."""
    small = photo.copy()
    small.thumbnail((360, 360))
    w, h = small.size
    key = (255, 0, 255)
    pts = [(x, 0) for x in range(0, w, 3)] + [(x, h - 1) for x in range(0, w, 3)]
    pts += [(0, y) for y in range(0, h, 3)] + [(w - 1, y) for y in range(0, h, 3)]
    for p in pts:
        px = small.getpixel(p)
        if px != key and sum(px) > 3 * 225:  # type: ignore[arg-type]
            ImageDraw.floodfill(small, p, key, thresh=thresh)
    diff = ImageChops.difference(small, Image.new("RGB", small.size, key)).convert("L")
    mask = diff.point(lambda v: 255 if v >= 3 else 0)
    mask = mask.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MinFilter(3))
    ratio = ImageStat.Stat(mask).mean[0] / 255
    if not 0.04 < ratio < 0.96:
        return None
    return mask.resize(photo.size, Image.Resampling.BILINEAR).point(lambda v: 255 if v > 127 else 0)


def upscale(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    out = img.resize(size, Image.Resampling.LANCZOS)
    if size[0] / max(1, img.size[0]) > 1.6:
        out = out.filter(ImageFilter.UnsharpMask(radius=2.4 * SS, percent=60, threshold=2))
    return out


def cover_crop(img: Image.Image, bw: int, bh: int, zoom: float = 1.0, fx: float = 0.5, fy: float = 0.5) -> Image.Image:
    iw, ih = img.size
    if iw / ih > bw / bh:
        ch, cw = ih, ih * bw / bh
    else:
        cw, ch = iw, iw * bh / bw
    cw, ch = cw / zoom, ch / zoom
    x0, y0 = (iw - cw) * fx, (ih - ch) * fy
    return upscale(img.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))), (bw, bh))


def photo_mode(photo: Image.Image, bw: int, bh: int) -> str:
    """cutout(흰 바탕 → 색판 위 오린 상품) | cover(사진을 꽉 채움) | framed(작거나 너무 길쭉한 사진 → 색판 위 인화지)."""
    if is_cutout(photo):
        return "cutout"
    pw, ph = photo.size
    loss = max((pw / ph) / (bw / bh), (bw / bh) / (pw / ph))  # 꽉 채우면 잘려 나가는 비율
    scale = max(bw / pw, bh / ph) / SS  # 1배 기준 확대
    return "framed" if loss > 1.8 or scale > 4.2 else "cover"


def _shadow(size: tuple[int, int], box: tuple[int, int, int, int], blur: float, alpha: int,
            color: tuple[int, int, int] = (60, 44, 28)) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rectangle(box, fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", size, color + (0,))
    out.putalpha(m)
    return out


def hero_image(photo: Image.Image, bw: int, bh: int, P: dict, rng: random.Random, sticky_right: bool) -> Image.Image:
    """사진 칸 (캔버스 크기 bw×bh, 이미 2배)."""
    mode = photo_mode(photo, bw, bh)
    off = rng.choice((0.34, 0.40)) if sticky_right else rng.choice((0.60, 0.66))  # 포스트잇 반대쪽으로
    if mode == "cutout":
        pan = paper((bw, bh), P["panel"], 0.22, corner=1)
        m = product_mask(photo)
        box = (m.getbbox() if m is not None else
               ImageChops.difference(photo, Image.new("RGB", photo.size, (255, 255, 255))).convert("L")
               .point(lambda v: 255 if v > 14 else 0).getbbox())
        if box:
            iw, ih = photo.size
            mx, my = int((box[2] - box[0]) * 0.04) + 2, int((box[3] - box[1]) * 0.04) + 2
            box = (max(0, box[0] - mx), max(0, box[1] - my), min(iw, box[2] + mx), min(ih, box[3] + my))
            photo = photo.crop(box)
            m = m.crop(box) if m is not None else None
        ph = int(bh * 0.84)
        pw = int(ph * photo.size[0] / photo.size[1])
        if pw > bw * 0.78:
            pw = int(bw * 0.78)
            ph = int(pw * photo.size[1] / photo.size[0])
        pw, ph = max(1, pw), max(1, ph)
        prod = upscale(photo, (pw, ph))
        ox, oy = int((bw - pw) * off), int((bh - ph) * 0.5)
        if m is not None:
            m = m.resize((pw, ph), Image.Resampling.BICUBIC).filter(ImageFilter.GaussianBlur(SS))
            pad = 48 * SS
            sh = Image.new("L", (pw + 2 * pad, ph + 2 * pad), 0)
            sh.paste(m.point(lambda v: int(v * 0.20)), (pad, pad))
            sh = sh.filter(ImageFilter.GaussianBlur(20 * SS))
            shadow = Image.new("RGBA", sh.size, INK + (0,))
            shadow.putalpha(sh)
            pan.paste(shadow, (ox - pad + 8 * SS, oy - pad + 14 * SS), shadow)
            pan.paste(prod, (ox, oy), m)
        else:
            region = pan.crop((ox, oy, ox + pw, oy + ph))
            pan.paste(ImageChops.multiply(region, prod), (ox, oy))
        return pan
    if mode == "framed":
        pan = paper((bw, bh), P["panel"], 0.22, corner=1)
        iw, ih = photo.size
        # 아주 길쭉한 사진: 긴 쪽 끝을 조금(합쳐 22%까지)만 잘라 인화지를 키움 — 더 자르면 상품이 잘려 나감
        target = bw / bh
        if iw / ih < target / 1.8:
            ch = max(int(ih * 0.78), min(ih, int(iw / (target / 1.8))))
            top = (ih - ch) // 2
            photo = photo.crop((0, top, iw, top + ch))
        elif iw / ih > target * 1.8:
            cw = max(int(iw * 0.78), min(iw, int(ih * target * 1.8)))
            left = (iw - cw) // 2
            photo = photo.crop((left, 0, left + cw, ih))
        iw, ih = photo.size
        sc = min(bw * 0.80 / iw, bh * 0.86 / ih, 4.0 * SS)
        pw, ph = max(1, int(iw * sc)), max(1, int(ih * sc))
        border = S(12)
        fw, fh = pw + 2 * border, ph + 2 * border
        ox, oy = int((bw - fw) * off), int((bh - fh) * 0.5)
        pad = S(30)
        pan_rgba = pan.convert("RGBA")
        pan_rgba.alpha_composite(_shadow((fw + 2 * pad, fh + 2 * pad), (pad + S(4), pad + S(8), pad + fw, pad + fh + S(4)), S(10), 80),
                                 (ox - pad, oy - pad))
        pan = pan_rgba.convert("RGB")
        ImageDraw.Draw(pan).rectangle((ox, oy, ox + fw - 1, oy + fh - 1), fill=(252, 250, 246))
        pan.paste(upscale(photo, (pw, ph)), (ox + border, oy + border))
        return pan
    zoom = rng.choice((1.0, 1.04, 1.08))
    hero = cover_crop(photo, bw, bh, zoom=zoom, fx=rng.choice((0.45, 0.5, 0.55)), fy=rng.choice((0.4, 0.5, 0.6)))
    g = _grain_crop((bw, bh), corner=1)
    return Image.blend(hero, ImageChops.add(hero, Image.merge("RGB", (g, g, g)), offset=-128), 0.16)


# ---------------------------------------------------------------- 손으로 만든 것들
def make_tape(length: float, color: tuple[int, int, int], rng: random.Random, label: str = "") -> Image.Image:
    """마스킹 테이프 (찢긴 끝, 펜 글씨). RGBA, 2배."""
    th = 58
    w, h = S(length), S(th)
    t = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(t)
    n = 8
    pts = [(S(rng.uniform(0, 6)), h * i / n) for i in range(n + 1)]
    pts += [(w - S(rng.uniform(0, 6)), h * i / n) for i in range(n, -1, -1)]
    d.polygon(pts, fill=color + (240,))
    fib = _noise(rng, (max(1, w // 3), max(1, h // 3)), 40).resize((w, h), Image.Resampling.BILINEAR)
    fib = fib.point(lambda v: max(0, min(255, (v - 128) // 6 + 128)))
    a = t.getchannel("A")
    rgb = ImageChops.add(Image.new("RGB", (w, h), color), Image.merge("RGB", (fib, fib, fib)), offset=-128)
    t = Image.merge("RGBA", (*rgb.split(), a))
    if label:
        key = hand(label)
        f = F(key, tape_size(label))
        ImageDraw.Draw(t).text((w / 2, h / 2 + S(2)), label, font=f, fill=(48, 38, 30, 255), anchor="mm",
                               stroke_width=1 if key == "hand" else 0, stroke_fill=(48, 38, 30, 255))
    return t


def tape_size(label: str) -> float:
    return 38 if hand(label) == "hand" else 32


def tape_len(label: str, max_len: float = 520) -> tuple[str, float]:
    """테이프 글과 길이 (너무 긴 분류 이름은 줄임)."""
    if not label:
        return "", 170
    f = F(hand(label), tape_size(label))
    while label and tw(f, label) + 64 > max_len:
        label = label[:-1]
    return label, max(170.0, tw(f, label) + 64)


def tape_clear_cy(want: float, length: float, angle: float, floor: float) -> float:
    """기울인 테이프의 가운데 y — 테이프 맨 위가 floor(고지 줄 바닥 + 틈) 아래에 오게. 긴 테이프는 기울이면 위로 솟아 고지를 덮었다."""
    a = math.radians(abs(angle))
    rot_h = 58 * math.cos(a) + length * math.sin(a)
    return max(want, floor + rot_h / 2 + 2)


def make_sticky(num: str, guide: list[str], P: dict, size: tuple[int, int]) -> Image.Image:
    """포스트잇: '번호 / #71 / 프로필 링크 → / 링크 페이지' — 카드에서 '#번호' 가 나오는 유일한 곳. RGBA 2배 (그림자 포함)."""
    sw, sh = size
    w, h = S(sw), S(sh)
    col = P["sticky"]
    note = Image.new("RGBA", (w, h), col + (255,))
    d = ImageDraw.Draw(note)
    d.rectangle([0, 0, w, S(26)], fill=tuple(max(0, c - 12) for c in col) + (255,))  # 풀 띠
    curl = Image.new("L", (w, h), 0)
    ImageDraw.Draw(curl).polygon([(w, h - S(64)), (w, h), (w - S(84), h)], fill=70)
    curl = curl.filter(ImageFilter.GaussianBlur(S(16)))
    note = Image.composite(Image.new("RGBA", (w, h), (255, 255, 255, 255)), note, curl)
    d = ImageDraw.Draw(note)
    x = 20
    text(d, x, 64, STICKY_LABEL, F("sb", MIN_TEXT), (82, 72, 60))
    hs = 80.0
    while tw(F("num", hs), num) > sw - 2 * x + 4 and hs > 44:
        hs -= 4
    text(d, x - 3, 146, num, F("num", hs), ink(0.88, col))
    cs = 32.0
    while any(tw(F("sb", cs), g) > sw - 2 * x for g in guide) and cs > 26:
        cs -= 1
    f = F("sb", cs)
    y = 200.0
    for g in guide[:2]:
        text(d, x, y, g, f, INK)
        y += cs * 1.22
    pad = S(30)
    out = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    out.alpha_composite(_shadow(out.size, (pad + S(6), pad + S(12), pad + w - S(2), pad + h + S(4)), S(9), 95, (50, 38, 24)))
    out.alpha_composite(note, (pad, pad))
    return out


def guide_lines(footer: str) -> list[str]:
    """'프로필 링크 → 링크 페이지' → ['프로필 링크 →', '링크 페이지'] (포스트잇 두 줄)."""
    head, sep, tail = footer.partition("→")
    if sep and tail.strip():
        return [f"{head.strip()} →", tail.strip()]
    return [footer]


def pen_line(d: ImageDraw.ImageDraw, pts: list[tuple[float, float]], width: float, color: tuple[int, int, int]) -> None:
    w_ = S(width)
    d.line([(S(x), S(y)) for x, y in pts], fill=color, width=w_, joint="curve")
    r = w_ / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse([S(x) - r, S(y) - r, S(x) + r, S(y) + r], fill=color)


def hand_arrow(d: ImageDraw.ImageDraw, p0: tuple[float, float], p1: tuple[float, float], bend: float, rng: random.Random,
               color: tuple[int, int, int] = PEN_RED, width: float = 3.6, head: str = "v") -> None:
    (x0, y0), (x1, y1) = p0, p1
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    nx, ny = -(y1 - y0), (x1 - x0)
    L = math.hypot(nx, ny) or 1
    cx, cy = mx + nx / L * bend, my + ny / L * bend
    pts = []
    ph = rng.random() * 6
    for i in range(41):
        t = i / 40
        x = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1
        y = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1
        j = math.sin(t * 8 + ph) * 0.7
        pts.append((x + j, y - j))
    pen_line(d, pts, width, color)
    ex, ey = pts[-1]
    px, py = pts[-6]
    ang = math.atan2(ey - py, ex - px)
    if head == "hook":
        pen_line(d, [(ex, ey), (ex + 26 * math.cos(ang + 2.5), ey + 26 * math.sin(ang + 2.5))], width, color)
    else:
        for da in (2.6, -2.45):
            pen_line(d, [(ex, ey), (ex + 22 * math.cos(ang + da), ey + 22 * math.sin(ang + da))], width, color)


def pen_underline(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float, rng: random.Random, width: float = 3.0) -> None:
    """숫자 아래 가는 빨간 펜 한 줄 (살짝 오르는)."""
    def curve(t: float) -> float:
        return y - 6 * t + 3 * math.sin(t * math.pi)
    pen_line(d, [(x0 + (x1 - x0) * i / 30, curve(i / 30) + rng.uniform(-0.3, 0.3)) for i in range(31)], width, PEN_RED)


def check_mark(d: ImageDraw.ImageDraw, x: float, y: float) -> None:
    pen_line(d, [(x + 2, y + 15), (x + 10, y + 25), (x + 30, y - 2)], 4.0, PEN_RED)


def price_tag_icon(d: ImageDraw.ImageDraw, x: float, y: float, s: float, hole: tuple[int, int, int]) -> None:
    pts = [(x, y + s * 0.42), (x + s * 0.42, y), (x + s, y), (x + s, y + s * 0.58), (x + s * 0.58, y + s)]
    d.polygon([(S(a), S(b)) for a, b in pts], fill=BRAND_RED)
    r = s * 0.11
    cx, cy = x + s * 0.74, y + s * 0.26
    d.ellipse([S(cx - r), S(cy - r), S(cx + r), S(cy + r)], fill=hole)


def paste_rot(canvas: Image.Image, layer: Image.Image, cx: float, cy: float, angle: float) -> tuple[float, float]:
    r = layer.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(r, (S(cx) - r.width // 2, S(cy) - r.height // 2))
    return r.width / SS, r.height / SS


# ---------------------------------------------------------------- 글 배치
def wrap(s: str, font: Font, max_w: float) -> list[str]:
    """띄어쓰기에서 먼저, 한 낱말이 너무 길면 글자 단위로."""
    lines: list[str] = []
    cur = ""
    for word in s.split():
        cand = f"{cur} {word}" if cur else word
        if tw(font, cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if tw(font, cur + ch) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def ellipsize(s: str, font: Font, max_w: float) -> str:
    if tw(font, s) <= max_w:
        return s
    while s and tw(font, s.rstrip() + "…") > max_w:
        s = s[:-1]
    return s.rstrip() + "…"


def fit_lines(s: str, key: str, max_w: float, plan: tuple[tuple[float, int], ...]) -> tuple[Font, list[str]]:
    """plan = ((크기, 최대 줄 수), …) 차례로 — 들어가는 첫 크기. 다 안 되면 마지막 크기로 줄 수까지 + '…'."""
    for size, n in plan:
        f = F(key, size)
        lines = wrap(s, f, max_w)
        if len(lines) <= n:
            return f, lines
    size, n = plan[-1]
    f = F(key, size)
    lines = wrap(s, f, max_w)
    rest = " ".join(lines[n - 1:])
    return f, lines[: n - 1] + [ellipsize(rest, f, max_w)]


def fit_size(s: str, key: str, size: float, max_w: float, min_size: float) -> float:
    while size > min_size and tw(F(key, size), s) > max_w:
        size -= 2
    return size


def split_amount(value: str) -> tuple[str, str]:
    """'15,900원' → ('15,900', '원'), '3개' → ('3', '개'). 숫자로 시작하지 않으면 ('', value)."""
    m = re.match(r"^([\d][\d,]*)(.*)$", value.strip())
    return (m.group(1), m.group(2).strip()) if m else ("", value)


def draw_amount(d: ImageDraw.ImageDraw, x: float, base: float, value: str, size: float, fill: object,
                unit_ratio: float = 0.36, right: bool = False) -> tuple[float, float]:
    """Bodoni 숫자 + Pretendard 단위('원'·'개'). right=True 면 x 가 오른쪽 끝. (왼쪽 x, 숫자 오른쪽 x)."""
    num, unit = split_amount(value)
    if not num:
        f = F("sb", size * 0.6)
        w_ = tw(f, value)
        x0 = x - w_ if right else x
        text(d, x0, base, value, f, fill)
        return x0, x0 + w_
    fn, fu = F("num", size), F("sb", max(MIN_TEXT, size * unit_ratio))
    gap = size * 0.06
    total = tw(fn, num) + (gap + tw(fu, unit) if unit else 0)
    x0 = x - total if right else x
    text(d, x0, base, num, fn, fill)
    end = x0 + tw(fn, num)
    if unit:
        text(d, end + gap, base - size * 0.02, unit, fu, INK)
    return x0, end


def amount_width(value: str, size: float, unit_ratio: float = 0.36) -> float:
    num, unit = split_amount(value)
    if not num:
        return tw(F("sb", size * 0.6), value)
    fu = F("sb", max(MIN_TEXT, size * unit_ratio))
    return tw(F("num", size), num) + (size * 0.06 + tw(fu, unit) if unit else 0)


def _disc_layout(ad: str, right: str) -> tuple[str, float, float, list[str]]:
    """고지 줄 배치 (나머지 글, 글 x, 글 크기, 줄들) — 그리기와 높이 재기가 같은 값을 쓰게."""
    rest = ad[2:].lstrip(" ·") if ad.startswith("광고") else ad
    tag_w = tw(F("sb", 31), "광고") + 24
    right_w = tw(F("num", 34), right) + 28 if right else 0.0
    x = M + tag_w + 14
    avail = W - M - right_w - x
    size = fit_size(rest, "sb", 34, avail, MIN_TEXT)
    f = F("sb", size)
    lines = wrap(rest, f, avail) if tw(f, rest) > avail else [rest]  # 고지는 자르지 않는다 (몰 고지 짧은 꼴은 48자까지)
    return rest, x, size, lines


DISC_TY = 54  # 고지 줄 가운데 y


def disclosure_bottom(ad: str, right: str = "") -> float:
    _, _, size, lines = _disc_layout(ad, right)
    return DISC_TY + 23 + (len(lines) - 1) * size * 1.3


def disclosure(d: ImageDraw.ImageDraw, ad: str, paper_col: tuple[int, int, int], *, right: str = "") -> float:
    """맨 위 첫 줄 고지: 검은 '광고' 칩 + 나머지 ('광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다' 를 그대로 나눠).
    오른쪽에 장 번호('2/7')가 있으면 그만큼 비우고, 안 들어가면 두 줄로 (MIN_TEXT 보다 작게 줄이지 않음). 고지 줄 바닥 y."""
    if not ad:  # 수수료 없는 딜 (원본 주소, CardInfo.ad_free): '광고' 칩·고지 없이 자리만 — 배치는 그대로
        if right:
            fr = F("num", 34)
            text(d, W - M - tw(fr, right), DISC_TY + 12, right, fr, SUB)
        return disclosure_bottom(ad, right)
    _, x, size, lines = _disc_layout(ad, right)
    f_tag = F("sb", 31)
    ty = DISC_TY
    tag_w = tw(f_tag, "광고") + 24
    d.rounded_rectangle([S(M), S(ty - 23), S(M + tag_w), S(ty + 23)], radius=S(6), fill=INK)
    text(d, M + tag_w / 2, ty + 1, "광고", f_tag, paper_col, anchor="mm")
    f = F("sb", size)
    for i, ln in enumerate(lines):
        text(d, x, ty + 1 + i * size * 1.3, ln, f, INK, anchor="lm")
    if right:  # 장 번호 '2/7' (고지 다음에 그림)
        fr = F("num", 34)
        text(d, W - M - tw(fr, right), ty + 12, right, fr, SUB)
    return disclosure_bottom(ad, right)


def folio(d: ImageDraw.ImageDraw, paper_col: tuple[int, int, int], right: str = "") -> None:
    """바닥: 가는 줄 + 빨간 가격표 + '데미얌의 쇼핑일기'. 오른쪽에 글(인스타 밖 카드의 안내)."""
    fy = H - FOLIO
    d.line([(S(M), S(fy)), (S(W - M), S(fy))], fill=INK, width=S(1.6))
    price_tag_icon(d, M, fy + 24, 26, hole=paper_col)
    text(d, M + 36, fy + 48, BRAND, F("sb", MIN_TEXT), INK)
    if right:  # 구매 안내('구매 링크는 프로필 링크에서')는 진하게, 기준 시각 안내는 흐리게
        guide = right.startswith("구매 링크")
        f = F("sb" if guide else "md", MIN_TEXT)
        right = ellipsize(right, f, W - 2 * M - 260)
        text(d, W - M - tw(f, right), fy + 48, right, f, INK if guide else SUB)


FOLIO = 76  # 바닥 줄 높이


def fact_rows(d: ImageDraw.ImageDraw, facts: list[tuple[str, str, str]], x: float, y: float, max_w: float,
              *, draw: bool = True, size: float = 35) -> float:
    """체크 표시 사실 줄 (본문, 빨간 덧말, 작은 근거). 작은 근거('10/16 07시 기준')는 안 들어가면 다음 줄로 — 자르지 않는다.
    본문이 길면 두 줄. 다음 y."""
    f_fact, f_qual = F("sb", size), F("md", MIN_TEXT)
    lh = FACT_LH * size / FACT
    for main, red, small in facts:
        avail = max_w - 46 - (12 + tw(f_fact, red) if red else 0)
        lines = wrap(main, f_fact, avail) or [""]
        if len(lines) > 2:
            lines = [lines[0], ellipsize(" ".join(lines[1:]), f_fact, avail)]
        if draw:
            check_mark(d, x + 2, y + 8)
            yy = y + 36
            for i, ln in enumerate(lines):
                text(d, x + 46, yy, ln, f_fact, INK)
                if i < len(lines) - 1:
                    yy += lh
        end = x + 46 + tw(f_fact, lines[-1])
        if red:
            if draw:
                text(d, end + 12, y + 36 + lh * (len(lines) - 1), red, f_fact, BRAND_RED)
            end += 12 + tw(f_fact, red)
        y += lh * len(lines)
        if small:
            qual = f"({small})"
            nl = end + 12 + tw(f_qual, qual) > x + max_w
            if draw:
                if nl:
                    text(d, x + 46, y + 30, qual, f_qual, QUAL)
                else:
                    text(d, end + 12, y - lh + 36, qual, f_qual, QUAL)
            if nl:
                y += QUAL_LH
    return y


FACT, FACT_LH, QUAL_LH = 35, 56, 42


# ---------------------------------------------------------------- 데이터 → 장 (딜 하나)
def pen_note(info: CardInfo) -> tuple[str, str]:
    """빨간 손글씨 메모 하나 (데이터에서): 1개당 가격이 먼저, 없으면 비교 폭. (글, 종류 unit|cmp|'')."""
    from dealbot.media.card_slides import unit_parts

    parts = unit_parts(info.unit) if info.price else []
    if parts:
        return f"{parts[0][0]} {parts[0][1]}", "unit"
    if info.price and info.ref_text and info.ref_pct:
        if info.ref_label.endswith("최저가"):  # 다른 판매처 값 — '평소보다' 라고 하면 틀린 말
            return f"{info.ref_label}보다 {info.ref_pct}%↓", "cmp"
        return f"평소보다 {info.ref_pct}%↓", "cmp"
    return "", ""


def facts_of(info: CardInfo, note_kind: str) -> list[tuple[str, str, str]]:
    """체크 표시 사실 줄 (본문, 빨간 덧말, 작은 근거) — deal_rows 와 같은 근거: 비교(쿠팡 최저가·평소 가격, 없으면 표시 정가),
    1개당(메모로 못 쓴 것), 기록(기준 시각은 따로), 판매처 · 배송, (줄이 적으면) 평점."""
    from dealbot.media.card_slides import unit_parts

    out: list[tuple[str, str, str]] = []
    if info.price and info.ref_text and info.ref_pct:
        # 빨간 덧말 'N%↓' — 메모가 같은 폭을 말하면(cmp) 그릴 때 한쪽만 (render_deal)
        out.append((info.ref_text, f"{info.ref_pct}%↓", ""))
    elif info.price and info.list_price:
        out.append((f"정가 {info.list_price:,}원", "", f"{info.list_pct}%↓" if info.list_pct else ""))
    parts = unit_parts(info.unit) if info.price else []
    rest = parts[1:] if note_kind == "unit" else parts
    if rest:
        out.append((" · ".join(f"{a} {b}" for a, b in rest), "", ""))
    if info.low_label:
        head, sep, tail = info.low_label.partition(" (")
        out.append((head, "", tail.rstrip(")") if sep else ""))
    bits = [b for b in (info.shop_name, info.ship) if b]
    if bits:
        out.append((" · ".join(bits), "", ""))
    if info.rating and len(out) < 3:
        out.append((f"평점 {info.rating}" + (f" · 리뷰 {info.review_count:,}건" if info.review_count else ""), "", ""))
    return out


def deal_spec(info: CardInfo, photo: bytes | None, *, page: str = "") -> SlideSpec:
    """종이 카드 한 장 (딜 하나짜리 글·TOP5 딜 장·사진 없는 글자 카드·스레드·블로그 카드).
    tag = '#번호' (포스트잇, 인스타 카드에 번호가 있을 때만), footer = 포스트잇의 구매 안내 '프로필 링크 → 링크 페이지' —
    번호 없는 인스타 카드는 포스트잇 없이 바닥에 '구매 링크는 프로필 링크에서', 인스타 밖 카드(info.cta)는 바닥에 cta.
    items = 그리는 사실 줄, rows = 그 바탕 데이터(대체 텍스트·본문과 같은 표)."""
    from dealbot.media.card_slides import SlideSpec, ad_of, alt_text, deal_rows

    rows = deal_rows(info)
    note, kind = pen_note(info)
    instagram = info.cta is None
    tag = f"#{info.deal_no}" if info.deal_no and instagram else ""
    if not instagram:
        footer = info.cta or ""
    else:
        # 번호가 없으면 포스트잇 없이 바닥에 B 카드와 같은 안내 한 번 ('#' 없이 — 캡션의 '구매 링크: 프로필 링크 → …' 와 같은 길)
        footer = GUIDE if tag else GUIDE_NO_NUMBER
    return SlideSpec(
        kind="deal", ad=ad_of(info), head=info.category, tag=tag, page=page, title=info.name,
        big=f"{info.price:,}원" if info.price else "", big_note=note, rows=rows, items=facts_of(info, kind),
        footer=footer, photo=photo, alt=alt_text(info, rows),
    )


# ---------------------------------------------------------------- 그리기: 딜 하나
STICKY = (228, 252)
STICKY_LIFT = 44  # 포스트잇 가운데가 사진 아래 끝에서 이만큼 위


def _sticky_x0(right: bool) -> float:
    return W - M - STICKY[0] + 16 if right else M - 16


def _corner_busy(photo: Image.Image, P: dict, rng: random.Random, mirror: bool, bh: int) -> tuple[float, float, float]:
    """포스트잇 후보 자리(오른쪽·왼쪽 아래)의 윤곽 밀도 (작은 사진으로)."""
    k = 4
    bw = (W - M) // k
    bhk = max(1, bh // k)
    state = rng.getstate()
    hero = hero_image(photo, bw * SS, bhk * SS, P, rng, True).resize((bw, bhk), Image.Resampling.BILINEAR)
    rng.setstate(state)
    e = hero.convert("L").filter(ImageFilter.FIND_EDGES)
    px0 = M if mirror else 0
    sh = (STICKY_LIFT + STICKY[1] / 2) / k
    out = []
    for right in (True, False):
        x0 = (_sticky_x0(right) - px0) / k
        box = (int(max(0, x0)), int(max(0, bhk - sh)), int(min(bw, x0 + STICKY[0] / k)), bhk)
        out.append(ImageStat.Stat(e.crop(box)).mean[0] if box[2] > box[0] and box[3] > box[1] else 0.0)
    return out[0], out[1], ImageStat.Stat(e).mean[0]


def render_deal(spec: SlideSpec) -> Image.Image:
    rng = seed_of("deal", spec.tag, spec.title, spec.big, spec.head)
    P = theme_of(spec.head, rng)
    paper_col = P["paper"]
    m = re.search(r"\d+", spec.tag or "")
    no = int(m.group()) if m else None
    mirror = (no % 2 == 0) if no is not None else rng.random() < 0.5  # 사진이 왼쪽/오른쪽 끝으로 (격자에서 번갈아)
    photo = open_photo(spec.photo)
    if photo is not None and max(ImageStat.Stat(photo).stddev) < 3:
        photo = None  # 거의 한 색뿐인 그림(빈 자리표시 그림)은 사진이 아님 → 이름 카드로
    has_sticky = bool(spec.tag)

    canvas = paper((S(W), S(H)), paper_col).convert("RGBA")
    d = ImageDraw.Draw(canvas)
    # ---- 1. 고지 — 언제나 첫 줄
    disc_bot = disclosure(d, spec.ad, paper_col, right=spec.page)
    hero_top = disc_bot + 35

    # ---- 2. 포스트잇 쪽
    sticky_right = not mirror
    bh_guess = 640
    if photo is not None and has_sticky and photo_mode(photo, S(W - M), S(bh_guess)) == "cover":
        b_r, b_l, b_all = _corner_busy(photo, P, rng, mirror, bh_guess)
        mine, other = (b_r, b_l) if sticky_right else (b_l, b_r)
        if mine > 0.7 * b_all and mine > 1.5 * other:
            sticky_right = not sticky_right
    sx0 = _sticky_x0(sticky_right)
    sx1 = sx0 + STICKY[0]
    overhang = STICKY[1] / 2 - STICKY_LIFT + 8 if has_sticky else 0  # 포스트잇이 사진 아래로 내려온 만큼

    # ---- 3. 글 덩어리 재기
    in_panel = photo is None  # 사진이 없으면 이름이 종이 이름 카드 안에 (크게)
    sticky_left_beside = has_sticky and not sticky_right and not in_panel
    px0 = M if mirror else 0
    bw = W - M
    sticky_zone = STICKY_LIFT + STICKY[1] / 2 + 30 if has_sticky else 0

    def panel_name(plan: tuple[tuple[float, int], ...]) -> tuple[float, Font, list[str], bool]:
        """이름 카드 안의 이름 (x, 글꼴, 줄, 포스트잇 칸을 비켜 카드 전체 가운데에 놓이나)."""
        x = (px0 + 56) if mirror else M + 4
        max_w = ((W - M - 8) if mirror else (px0 + bw - 56)) - x
        f, lines = fit_lines(spec.title, "sb", max_w, plan)
        if has_sticky:
            n_x = x if sticky_right else max(x, sx1 + 28)
            n_max = ((sx0 - 28) if sticky_right else (x + max_w)) - n_x
            n_f, n_l = fit_lines(spec.title, "sb", n_max, plan)
            if n_f.size >= f.size - S(12) and len(n_l) <= 3:
                return n_x, n_f, n_l, True
        return x, f, lines, False

    def panel_need(font: Font, lines: list[str], full: bool) -> float:
        """이름 카드에 이 이름이 들어가는 높이 (위아래 여백, 포스트잇 자리 포함)."""
        block = font.size / SS * 1.2 * len(lines)
        return max(2 * 84 + block, sticky_zone + 150) if full else 76 + block + (sticky_zone or 76)

    name_full_panel = False  # 이름이 포스트잇 칸을 비켜 가서 이름 카드 전체의 가운데에 놓이나
    if in_panel:  # 이름 카드 안: 바깥 끝 쪽 여백을 두고 카드 폭 그대로 (포스트잇은 아래 모서리 — 이름은 그 위에서 끝남)
        plan: tuple[tuple[float, int], ...] = ((96, 2), (84, 2), (76, 3), (66, 3), (58, 4), (52, 4))
        name_x, name_font, name_lines, name_full_panel = panel_name(plan)
    else:
        if sticky_left_beside:
            name_x = sx1 + 34
            name_max = W - M - name_x
        else:
            name_x = M
            name_max = (sx0 - 30 - M) if has_sticky else W - 2 * M
        plan = ((56, 1), (52, 1), (48, 1), (52, 2), (48, 2), (44, 2))
        name_font, name_lines = fit_lines(spec.title, "sb", name_max, plan)
    name_size = name_font.size / SS
    name_lh = name_size * (1.2 if in_panel else 1.26)
    name_block = name_lh * len(name_lines)
    name_h = 0.0 if in_panel else name_block

    num, _ = split_amount(spec.big)
    f_won = F("sb", 50)
    note = spec.big_note
    note_min = 250 if note else 0
    p_size = 190.0 if in_panel else 166.0  # 사진이 없으면 가격이 더 크게 (빈 자리 없이)

    def price_w_of(size: float) -> float:
        return tw(F("num", size), num) + 10 + tw(f_won, "원") if num else 0.0

    while num and p_size > 100 and price_w_of(p_size) > W - 2 * M - note_min:
        p_size -= 6

    # 빨간 손글씨 메모 자리: 가격 오른쪽 → (안 들어가면) 가격 위 오른쪽 띠에 아래로 짧은 화살표 → 그래도 안 되면 사실 줄 (같은 글).
    # 메모가 비교 폭이면 사실 줄의 빨간 덧말은 빼서 한 번만
    note_key = hand(note) if note else "hand"
    note_size = 54.0
    note_mode = ""
    if note and num:
        nw54 = tw(F(note_key, note_size), note)
        room = W - M - (M + price_w_of(p_size)) - 60
        if nw54 * 42 / note_size <= room:
            note_size = min(note_size, max(42.0, note_size * room / nw54))
            note_mode = "beside"
        elif nw54 * 44 / note_size <= W - 2 * M - 160:
            note_size = min(note_size, (W - 2 * M - 160) * note_size / nw54)
            note_mode = "above"
    band_h = note_size * 1.25 + 8 if note_mode == "above" else 0.0
    facts = list(spec.items)
    same = [i for i, (_, red, _) in enumerate(facts) if red and note.endswith(red)] if note else []
    if note and not note_mode:
        if not same:
            facts.insert(0, (note, "", ""))
        note = ""
    elif note:
        for i in same:
            facts[i] = (facts[i][0], "", facts[i][2])

    fact_w = W - 2 * M
    fsz = 38.0 if in_panel else FACT  # 사진이 없으면 사실 줄도 조금 크게
    g_photo_name, g_name_price, g_bottom = 40.0, 22.0, 26.0

    def p_top_of(gpn: float) -> float:
        """사진(이름 카드) 아래 끝에서 가격 줄 맨 위까지 (위 메모 띠 포함)."""
        top = gpn + (name_h + g_name_price if name_h else 0)
        if has_sticky and (sticky_left_beside or in_panel):
            top = max(top, overhang + 22)  # 가격 줄은 포스트잇 아래에서
        if band_h:
            if has_sticky and sticky_right:
                top = max(top, overhang + 8)  # 오른쪽 메모 띠는 포스트잇 아래에서
            top += band_h
        return top

    def measure() -> tuple[float, float, float, float]:
        """(글자 높이 cap, 쉼표 아래 desc, 가격→사실 줄 틈, 사진 칸에 남는 높이 space)."""
        f = F("num", p_size)
        cap_ = -f.getbbox("0123456789", anchor="ls")[1] / SS if num else 0.0
        desc_ = max(f.getbbox(",", anchor="ls")[3] / SS, 4.0) if num else 0.0
        gpf = (max(34.0, desc_ + 30) + 14 if facts else desc_ + 20) if num else 10.0  # 사실 줄이 없으면 밑줄 아래만
        facts_h = fact_rows(d, facts, M, 0, fact_w, draw=False, size=fsz)
        text_h = p_top_of(g_photo_name) + (cap_ if num else 0.0) + gpf + facts_h + g_bottom
        return cap_, desc_, gpf, H - FOLIO - text_h - hero_top

    cap, desc, g_price_facts, space = measure()
    if in_panel:  # 이름 (+ 포스트잇 자리) 가 들어가는 만큼부터 — 남는 자리는 거의 다 이름 카드로 (이름도 그만큼 크게)
        lo = max(360.0, panel_need(name_font, name_lines, name_full_panel))
    else:
        lo = 420.0
    while space < lo and len(facts) > 1:  # 넘치면 덜 중요한 줄부터 (평점 → 판매처·배송 …) — 기준 시각이 붙은 기록 줄은 남김
        drop = next((i for i in range(len(facts) - 1, -1, -1) if not facts[i][2]), None)
        if drop is None:
            break
        facts.pop(drop)
        cap, desc, g_price_facts, space = measure()
    if in_panel:
        # 자리가 많이 남으면 가격·사실 줄을 키우고 (메모 자리는 남김), 나머지는 이름 카드가 받는다 — 아래에 빈 띠가 생기지 않게
        p_max = min(250.0, p_size + 60)
        while num and p_size < p_max and space - lo > 300:
            grown = p_size + 6
            if price_w_of(grown) > W - 2 * M - note_min or (
                    note_mode == "beside" and tw(F(note_key, note_size), note) > W - M - (M + price_w_of(grown)) - 60):
                break
            p_size = grown
            cap, desc, g_price_facts, space = measure()
        if facts and space - lo > 360 and fsz < 42:
            fsz = 42.0
            cap, desc, g_price_facts, space = measure()
        extra = max(0.0, space - lo)
        add_top = min(extra * 0.25, 56.0)
        add_mid = min(extra * 0.10, 24.0) if facts else 0.0
        add_bot = min(extra * 0.10, 30.0) if len(facts) >= 2 else min(extra * 0.05, 12.0)
        g_photo_name += add_top
        g_price_facts += add_mid
        g_bottom += add_bot
        hero_h = int(max(lo, space - add_top - add_mid - add_bot))
        # 이름 카드가 커졌으면 이름도 크게 (낱말을 자르지 않는 크기만 — 짧은 이름이 넓은 카드에 한 줄로 떠 있지 않게)
        for size, n in ((140, 2), (128, 2), (116, 2), (106, 3)):
            if size * SS <= name_font.size:
                break
            cand = panel_name(((size, n),))
            whole = " ".join(cand[2]) == " ".join(spec.title.split())  # 다 들어가고, 낱말 가운데서 끊지 않음
            if whole and panel_need(cand[1], cand[2], cand[3]) + 40 <= hero_h:
                name_x, name_font, name_lines, name_full_panel = cand
                name_size = name_font.size / SS
                name_lh = name_size * 1.2
                name_block = name_lh * len(name_lines)
                break
    else:
        hi = 800.0 if len(facts) >= 2 else 900.0  # 사실 줄이 적으면 사진을 더 크게 (글 아래 빈 띠 대신)
        hero_h = int(max(lo, min(space, hi)))
        slack = max(0.0, space - hero_h)
        g_photo_name += slack * 0.40
        g_price_facts += slack * 0.25
        g_bottom += slack * 0.35
    f_num = F("num", p_size)
    digits_w = tw(f_num, num) if num else 0.0

    # ---- 4. 사진 칸 (한쪽 끝으로 붙여, 위에 테이프) / 사진이 없으면 이름 카드
    if photo is not None:
        hero = hero_image(photo, S(bw), S(hero_h), P, rng, sticky_right)
    else:
        hero = paper((S(bw), S(hero_h)), P["panel"], 0.24, corner=1)
    pad = S(26)
    canvas.alpha_composite(_shadow((S(bw) + 2 * pad, S(hero_h) + 2 * pad), (pad, pad + S(8), pad + S(bw), pad + S(hero_h) + S(6)),
                                   S(10), 70), (S(px0) - pad + (S(-4) if mirror else S(4)), S(hero_top) - pad))
    canvas.paste(hero, (S(px0), S(hero_top)))
    del hero
    d = ImageDraw.Draw(canvas)
    hero_bot = hero_top + hero_h
    if in_panel:  # 이름: 포스트잇 칸을 비켜 갔으면 카드 가운데, 아니면 포스트잇 위쪽
        zone = hero_h - (STICKY_LIFT + STICKY[1] / 2 + 10 if has_sticky and not name_full_panel else 0)
        y = hero_top + max(64.0, (zone - name_block) / 2 + 6)
        for ln in name_lines:
            y += name_size
            text(d, name_x, y, ln, name_font, INK)
            y += name_lh - name_size
    label, t_len = tape_len(spec.head, 440)
    tape = make_tape(t_len, P["tape"], rng, label)
    t_ang = rng.uniform(4, 8) * (1 if sticky_right else -1)
    t_cx = (M + 130 + rng.uniform(0, 70)) if sticky_right else (W - M - 120 - t_len / 2 - rng.uniform(0, 60))
    t_cx = min(max(t_cx, M + t_len / 2), W - M - t_len / 2)
    paste_rot(canvas, tape, t_cx, tape_clear_cy(hero_top + 10, t_len, t_ang, disc_bot + 8), t_ang)
    if has_sticky:
        s_ang = rng.uniform(2.5, 4.5) * (-1 if sticky_right else 1)
        sticky = make_sticky(spec.tag, guide_lines(spec.footer), P, STICKY)
        paste_rot(canvas, sticky, sx0 + STICKY[0] / 2, hero_bot - STICKY_LIFT, s_ang)
    d = ImageDraw.Draw(canvas)

    # ---- 5. 이름 (사진 아래)
    if not in_panel:
        y = hero_bot + g_photo_name
        for ln in name_lines:
            y += name_size
            text(d, name_x, y, ln, name_font, INK)
            y += name_lh - name_size

    # ---- 6. 가격 + 빨간 펜 밑줄
    p_base = hero_bot + p_top_of(g_photo_name) + cap
    price_right = M
    if num:
        text(d, M - 4, p_base, num, f_num, ink(0.96, paper_col))
        end = M - 4 + digits_w
        text(d, end + 10, p_base - 3, "원", f_won, INK)
        price_right = end + 10 + tw(f_won, "원")
        pen_underline(d, M - 2, end + 4, p_base + desc + 12, rng)

    # ---- 7. 빨간 손글씨 메모 + 짧은 화살표 (가격 오른쪽, 또는 가격 위 오른쪽 띠)
    if note:
        f_hand = F(note_key, note_size)
        nw = tw(f_hand, note)
        layer = Image.new("RGBA", (S(nw + 24), S(note_size * 1.5)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((S(10), S(note_size * 1.08)), note, font=f_hand, fill=PEN_RED, anchor="ls",
                                   stroke_width=1 if note_key == "hand" else 0, stroke_fill=PEN_RED)
        if note_mode == "beside":
            nx0 = min(price_right + rng.uniform(88, 112), W - M - nw)
            ncy = p_base - cap * rng.uniform(0.66, 0.84)
            if has_sticky and sticky_right and not in_panel:
                ncy = max(ncy, hero_bot + overhang + 46 + note_size * 0.55)
            ncy = min(ncy, p_base - note_size * 0.15)
        else:  # 위 띠: 오른쪽 끝에 맞추고, 화살표는 아래 왼쪽 숫자로
            nx0 = W - M - nw - rng.uniform(0, 14)
            ncy = p_base - cap - band_h / 2 - 4
        paste_rot(canvas, layer, nx0 + (nw + 24) / 2 - 10, ncy, rng.choice((-1, 1)) * rng.uniform(1.5, 5.0))
        d = ImageDraw.Draw(canvas)
        if note_mode == "beside":
            start = (nx0 - 18, ncy + note_size * rng.uniform(0.3, 0.5))
            tip = (price_right + 14, p_base - cap * rng.uniform(0.32, 0.5))
            if start[0] - tip[0] > 30:
                hand_arrow(d, start, tip, rng.choice((-1, 1)) * rng.uniform(10, 22), rng, head=rng.choice(("v", "v", "hook")))
        else:
            start = (nx0 - 6, ncy + note_size * 0.05)
            tip = (max(M + 40, min(start[0] - 44, price_right - 30)), p_base - cap - 6)
            hand_arrow(d, start, tip, rng.choice((-1, 1)) * rng.uniform(4, 9), rng, head="v")

    # ---- 8. 체크 표시 사실 줄
    fact_rows(d, facts, M, p_base + g_price_facts if num else hero_bot + p_top_of(g_photo_name) + 10, fact_w, size=fsz)

    # ---- 9. 바닥
    folio(d, paper_col, "" if has_sticky else spec.footer)
    return canvas.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------- 그리기: 표지·단가표 장·마지막 장
def _title_fit(title: str, size: float, lines_max: int) -> tuple[Font, list[str]]:
    parts = [p for p in title.split("\n") if p.strip()]
    f, lines = fit_lines(" ".join(parts), "sb", W - 2 * M, ((size, lines_max), (size - 8, lines_max), (size - 16, lines_max)))
    if len(parts) > 1 and all(tw(f, p) <= W - 2 * M for p in parts) and len(parts) <= lines_max:
        lines = parts
    return f, lines


def _title_h(title: str, size: float = 80, lines_max: int = 2) -> float:
    f, lines = _title_fit(title, size, lines_max)
    return f.size / SS * 1.24 * len(lines) + 22


def _title(d: ImageDraw.ImageDraw, title: str, y: float, rng: random.Random, size: float = 80, lines_max: int = 2) -> float:
    """편집 디자인 제목 (Pretendard SemiBold) — 마지막 줄 아래 빨간 펜 밑줄. 다음 y."""
    f, lines = _title_fit(title, size, lines_max)
    s = f.size / SS
    last_w = 0.0
    for ln in lines:
        y += s
        text(d, M - 2, y, ln, f, INK)
        last_w = tw(f, ln)
        y += s * 0.24
    if last_w:
        pen_underline(d, M, M + min(last_w, W - 2 * M), y + 4, rng, width=3.0)
    return y + 22


# 목록 줄 크기 차례 (이름 글, 값 글, 줄 간격 배수, 위아래 여백) — 들어가는 첫 크기. 마지막(촘촘)도 안 되면 그때만 줄을 뺀다
ROW_PLANS = ((42, 72, 1.3, 34), (38, 60, 1.3, 34), (34, 52, 1.25, 22), (32, 46, 1.2, 12))


def _row_plan(items: list[tuple[str, str, str]], avail: float) -> tuple[float, float, float, int]:
    """(이름 글 크기, 값 글 크기, 줄 높이, 그릴 줄 수)."""
    n = len(items)
    has_sub = any(sub for _, _, sub in items)
    plans = ROW_PLANS if n <= 3 else ROW_PLANS[1:]
    row_min = 1.0
    for name_size, val_size, lh, pad in plans:
        row_min = name_size * lh + (MIN_TEXT * lh if has_sub else 0) + pad
        if row_min * n <= avail:
            return name_size, val_size, max(row_min, min(avail / n, row_min + 36)), n
    name_size, val_size, _, _ = plans[-1]
    return name_size, val_size, row_min, max(0, min(n, int(avail // row_min)))


def fit_sub(sub: str, font: Font, max_w: float) -> str:
    """목록 작은 글을 칸에 — 숫자 가운데서 자르지 않는다: 끝의 '(N%↓)' 를 먼저 빼고, 그래도 길면 낱말(괄호 묶음은 통째로) 단위로 '…'."""
    if tw(font, sub) <= max_w:
        return sub
    short = re.sub(r"\s*\(\d+%↓\)$", "", sub)
    if tw(font, short) <= max_w:
        return short
    tokens = re.findall(r"\([^)]*\)|[^\s(]+", short)
    while len(tokens) > 1 and tw(font, " ".join(tokens) + "…") > max_w:
        tokens.pop()
    out = " ".join(tokens)
    return out + "…" if tw(font, out + "…") <= max_w else ellipsize(out, font, max_w)


def _items(d: ImageDraw.ImageDraw, items: list[tuple[str, str, str]], y: float, bottom: float, P: dict, rng: random.Random,
           *, mark_first: bool = False) -> float:
    """목록: 왼쪽 '#12' (Bodoni) + 이름 한 줄 + 작은 글, 오른쪽 값(Bodoni 숫자 + 단위). 줄 사이 가는 선.
    딜이 적으면 글을 키우고, 많으면 촘촘하게 — 그래도 넘치는 줄만 뺀다 (items_that_fit 과 같은 계산). 다음 y."""
    if not items:
        return y
    name_size, val_size, row_h, count = _row_plan(items, bottom - y)
    items = items[:count]
    sub_size = MIN_TEXT
    f_name, f_no = F("sb", name_size), F("num", name_size + 6)
    f_sub = F("md", sub_size)
    nos = [name.partition(" ")[0] for name, _, _ in items if name.startswith("#")]
    no_col = max((tw(f_no, no) for no in nos), default=0.0)
    no_col = no_col + 20 if no_col else 0.0
    x = M + no_col

    def sub_room(vs: float) -> float:
        return min(W - M - amount_width(v, vs) - 28 - x for _, v, _ in items) if any(v for _, v, _ in items) else W - M - x

    # 작은 글(비교·기록)이 값 칸에 밀려 잘리면 값 글을 조금 줄여 자리를 냄 (7자리 가격)
    need = max((tw(f_sub, sub) for _, _, sub in items if sub), default=0.0)
    for k in (1.0, 0.88, 0.78):
        if sub_room(val_size * k) >= need:
            val_size *= k
            break
    else:
        val_size *= 0.78
    line_col = ink(0.18, P["paper"])
    for i, (name, value, sub) in enumerate(items):
        no, _, rest = name.partition(" ") if name.startswith("#") else ("", "", name)
        val_w = amount_width(value, val_size) if value else 0
        right_x = W - M - val_w - 28
        content_h = name_size * 1.25 + (sub_size * 1.3 if sub else 0)
        top = y + (row_h - content_h) / 2
        if no:
            text(d, M, top + name_size, no, f_no, ink(0.82, P["paper"]))
        text(d, x, top + name_size, ellipsize(rest, f_name, right_x - x), f_name, INK)
        if sub:
            text(d, x, top + name_size * 1.25 + sub_size * 1.05, fit_sub(sub, f_sub, right_x - x), f_sub, SUB)
        if value:
            base = y + row_h / 2 + val_size * 0.34
            x0, end = draw_amount(d, W - M, base, value, val_size, ink(0.96, P["paper"]), right=True)
            if mark_first and i == 0:
                pen_underline(d, x0, end, base + 12, rng, width=2.8)
        y += row_h
        if i < len(items) - 1:
            d.line([(S(M), S(y)), (S(W - M), S(y))], fill=line_col, width=S(1.2))
    return y


def _guide_sticky(footer: str, P: dict, width: float = 400) -> Image.Image:
    """표지·단가표의 구매 안내를 포스트잇에: '구매 링크' + 나머지 (번호는 목록에 있음). RGBA 2배 (그림자 포함)."""
    label, sep, body = footer.partition(":")
    if not sep:
        label, body = "", footer
    f = F("sb", MIN_TEXT + 2)
    lines = [ln for part in guide_lines(body.strip()) for ln in wrap(part, f, width - 44)]
    h = 40 + (44 if label else 0) + len(lines) * (MIN_TEXT + 2) * 1.32 + 26
    w_, h_ = S(width), S(h)
    note = Image.new("RGBA", (w_, h_), P["sticky"] + (255,))
    d = ImageDraw.Draw(note)
    d.rectangle([0, 0, w_, S(22)], fill=tuple(max(0, c - 12) for c in P["sticky"]) + (255,))
    y = 40.0
    if label:
        y += MIN_TEXT
        text(d, 22, y, label.strip(), F("sb", MIN_TEXT), (82, 72, 60))
        y += 14
    for ln in lines:
        y += (MIN_TEXT + 2) * 1.05
        text(d, 22, y, ln, f, INK)
        y += (MIN_TEXT + 2) * 0.27
    pad = S(30)
    out = Image.new("RGBA", (w_ + 2 * pad, h_ + 2 * pad), (0, 0, 0, 0))
    out.alpha_composite(_shadow(out.size, (pad + S(6), pad + S(12), pad + w_ - S(2), pad + h_ + S(4)), S(9), 95, (50, 38, 24)))
    out.alpha_composite(note, (pad, pad))
    return out


FOOT_SIZE, FOOT_LH, NOTE_LH = 32, 32 * 1.3, MIN_TEXT * 1.38


def _foot_lines(spec: SlideSpec) -> tuple[Font, list[str], list[str]]:
    """바닥 안내 한 줄 꼴 (구매 안내 글꼴, 그 줄들, 작은 안내 줄들)."""
    f = F("sb", fit_size(spec.footer, "sb", FOOT_SIZE, W - 2 * M, MIN_TEXT)) if spec.footer else F("sb", FOOT_SIZE)
    guide = wrap(spec.footer, f, W - 2 * M)[:2] if spec.footer else []
    notes = wrap(spec.note, F("md", MIN_TEXT), W - 2 * M)[:2] if spec.note else []
    return f, guide, notes


def _bottom(canvas: Image.Image, spec: SlideSpec, y_end: float, P: dict, rng: random.Random) -> Image.Image:
    """표지·단가표 바닥: 목록 아래 자리가 넉넉하면 구매 안내를 포스트잇으로(빈 자리 없이), 아니면 바닥 위 한 줄. 작은 안내."""
    d = ImageDraw.Draw(canvas)
    sticky = _guide_sticky(spec.footer, P) if spec.footer else None
    sticky_h = sticky.height / SS - 60 if sticky else 0
    note_lines = wrap(spec.note, F("md", MIN_TEXT), W - 2 * M - (430 if sticky else 0))[:2] if spec.note else []
    free = H - FOLIO - 24 - y_end
    if sticky is not None and free >= sticky_h + 40:
        cy = y_end + (free - sticky_h) / 2 + sticky_h / 2 + 6
        paste_rot(canvas, sticky, W - M - 200 + 6, cy, rng.uniform(1.8, 3.2))
        d = ImageDraw.Draw(canvas)
        if note_lines:  # 포스트잇 왼쪽, 바닥 위
            yy = H - FOLIO - 30 - (len(note_lines) - 1) * NOTE_LH
            for ln in note_lines:
                text(d, M, yy, ln, F("md", MIN_TEXT), QUAL)
                yy += NOTE_LH
        return canvas
    f, guide, notes = _foot_lines(spec)
    yy = H - FOLIO - 26
    for ln in reversed(guide):
        text(d, M, yy, ln, f, INK)
        yy -= FOOT_LH
    if guide:
        yy -= 8
    for ln in reversed(notes):
        text(d, M, yy, ln, F("md", MIN_TEXT), QUAL)
        yy -= NOTE_LH
    return canvas


def _foot_h(spec: SlideSpec) -> float:
    """바닥 안내(한 줄 꼴)가 차지하는 높이 — 목록이 여기까지 내려옴. _bottom 이 실제로 그리는 줄 수로 잰다."""
    _, guide, notes = _foot_lines(spec)
    if not guide and not notes:
        return 24.0
    h = 26.0
    if guide:
        h += FOOT_LH * len(guide) + 8
    h += NOTE_LH * len(notes)
    return h + (30 - NOTE_LH if notes else 32 - FOOT_LH - 8) + 18


def _head_bottom(spec: SlideSpec, title_size: float) -> float:
    """_head 가 그린 뒤의 y (그리지 않고 잼)."""
    y = disclosure_bottom(spec.ad, spec.tag) + 40
    if spec.head:
        y += 70
    y += 10 + _title_h(spec.title, title_size)
    if spec.big_note:
        y += 44 * len(wrap(spec.big_note, F("md", 32), W - 2 * M)[:2])
    return y + 30


def _head(canvas: Image.Image, spec: SlideSpec, P: dict, rng: random.Random, title_size: float) -> tuple[Image.Image, float]:
    """표지·단가표 위: 고지(첫 줄) + 장 번호, 테이프(날짜·묶음 이름), 제목(빨간 펜 밑줄), 작은 설명. (캔버스, 다음 y)."""
    d = ImageDraw.Draw(canvas)
    y = disclosure(d, spec.ad, P["paper"], right=spec.tag) + 40
    if spec.head:
        label, t_len = tape_len(spec.head)
        ang = rng.uniform(-4, -2)
        paste_rot(canvas, make_tape(t_len, P["tape"], rng, label), M + t_len / 2 - 6, tape_clear_cy(y + 26, t_len, ang, y - 32), ang)
        d = ImageDraw.Draw(canvas)
        y += 70
    y = _title(d, spec.title, y + 10, rng, size=title_size)
    if spec.big_note:
        f = F("md", 32)
        for ln in wrap(spec.big_note, f, W - 2 * M)[:2]:
            y += 32
            text(d, M, y, ln, f, SUB)
            y += 12
    return canvas, y + 30


COVER_TITLE, LIST_TITLE = 84, 80


def _baseline_h(spec: SlideSpec) -> float:
    if not spec.rows:
        return 0.0
    rest = spec.rows[1][1] if len(spec.rows) > 1 else ""
    n_rest = len(wrap(rest, F("md", MIN_TEXT), W - 2 * M - 64)[:2]) if rest else 0
    return 118 + n_rest * MIN_TEXT * 1.4


def _list_frame(spec: SlideSpec) -> tuple[float, float]:
    """표지·단가표의 목록 칸 (위 y, 아래 y)."""
    top = _head_bottom(spec, COVER_TITLE if spec.kind == "cover" else LIST_TITLE)
    box_h = _baseline_h(spec) if spec.kind == "list" else 0.0
    return top, H - FOLIO - _foot_h(spec) - (box_h + 30 if box_h else 0)


def items_that_fit(spec: SlideSpec) -> int:
    """표지·단가표 장에 그려지는 목록 줄 수 (_items 와 같은 계산). 다른 장은 spec.items 전부."""
    if spec.kind not in ("cover", "list") or not spec.items:
        return len(spec.items)
    top, bottom = _list_frame(spec)
    return _row_plan(spec.items, bottom - top)[3]


def render_cover(spec: SlideSpec) -> Image.Image:
    """표지 (TOP5·단가표): 고지, 날짜 테이프, 편집 디자인 제목, 목록, 구매 안내(포스트잇 또는 한 줄)."""
    rng = seed_of("cover", spec.title, spec.head, spec.tag)
    P = theme_of(spec.title, rng)
    canvas = paper((S(W), S(H)), P["paper"]).convert("RGBA")
    canvas, y = _head(canvas, spec, P, rng, COVER_TITLE)
    d = ImageDraw.Draw(canvas)
    _, bottom = _list_frame(spec)
    y = _items(d, spec.items, y, bottom, P, rng)
    canvas = _bottom(canvas, spec, y, P, rng)
    folio(ImageDraw.Draw(canvas), P["paper"])
    return canvas.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)


def render_list(spec: SlideSpec) -> Image.Image:
    """단가표 한 장 (한 단위): 테이프에 묶음 이름, 제목, 낮은 순 목록(맨 위 값에 빨간 밑줄), 기준선 카드(있을 때), 안내."""
    rng = seed_of("list", spec.title, spec.head, spec.tag)
    P = theme_of(f"{spec.head} {spec.title}", rng)
    canvas = paper((S(W), S(H)), P["paper"]).convert("RGBA")
    canvas, y = _head(canvas, spec, P, rng, LIST_TITLE)
    d = ImageDraw.Draw(canvas)
    box_h = _baseline_h(spec)
    _, bottom = _list_frame(spec)
    y = _items(d, spec.items, y, bottom, P, rng, mark_first=True)
    if spec.rows:
        canvas = _baseline_card(canvas, spec.rows, y + 22, box_h, P, rng)
        y += 22 + box_h
    canvas = _bottom(canvas, spec, y, P, rng)
    folio(ImageDraw.Draw(canvas), P["paper"])
    return canvas.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)


def _baseline_card(canvas: Image.Image, rows: list[tuple[str, str]], y: float, h: float, P: dict,
                   rng: random.Random) -> Image.Image:
    """기준선 카드: 포스트잇 색 종이에 '사도 되는 기준선' + '1롤당 480원' (Bodoni), 아래 근거."""
    (label, value), rest = rows[0], rows[1:]
    w = W - 2 * M
    card = Image.new("RGBA", (S(w), S(h)), P["sticky"] + (255,))
    pad = S(26)
    canvas.alpha_composite(_shadow((S(w) + 2 * pad, S(h) + 2 * pad), (pad + S(4), pad + S(8), pad + S(w), pad + S(h) + S(3)),
                                   S(8), 80, (50, 38, 24)), (S(M) - pad, S(y) - pad))
    canvas.alpha_composite(card, (S(M), S(y)))
    d = ImageDraw.Draw(canvas)
    text(d, M + 30, y + 62, label, F("sb", 34), INK)
    m = re.match(r"^(\S+당)\s+(.+)$", value)
    if m:
        per, amount = m.groups()
        x0, _ = draw_amount(d, W - M - 30, y + 74, amount, 64, ink(0.96, P["sticky"]), right=True)
        fp = F("sb", 32)
        text(d, x0 - 14 - tw(fp, per), y + 72, per, fp, INK)
    else:
        f = F("sb", fit_size(value, "sb", 44, w / 2, MIN_TEXT))
        text(d, W - M - 30 - tw(f, value), y + 70, value, f, INK)
    if rest:
        f = F("md", MIN_TEXT)
        yy = y + 112
        for ln in wrap(rest[0][1], f, w - 64)[:2]:
            yy += MIN_TEXT
            text(d, M + 30, yy, ln, f, SUB)
            yy += MIN_TEXT * 0.4
    tape = make_tape(150, P["tape"], rng)
    paste_rot(canvas, tape, W / 2, y + 2, rng.uniform(-6, -3))
    return canvas


def render_end(spec: SlideSpec) -> Image.Image:
    """마지막 장: 큰 제목(빨간 펜 밑줄), 포스트잇에 '프로필 링크', 작은 안내 — 세로 가운데, 바닥 브랜드."""
    rng = seed_of("end", spec.title, spec.tag)
    P = PALETTES["butter"]
    canvas = paper((S(W), S(H)), P["paper"]).convert("RGBA")
    d = ImageDraw.Draw(canvas)
    top = disclosure(d, spec.ad, P["paper"], right=spec.tag) + 40
    title_lines = len(fit_lines(" ".join(spec.title.split("\n")), "sb", W - 2 * M, ((100, 3), (92, 3), (84, 3)))[1])
    sw, sh = 560, 210
    f_note = F("md", 36)
    note_lines = wrap(spec.note, f_note, W - 2 * M)[:3] if spec.note else []
    block = title_lines * 100 * 1.24 + 22 + (sh + 100 if spec.big else 0) + len(note_lines) * 36 * 1.45
    y = top + max(40.0, (H - FOLIO - top - block) / 2 - 20)
    y = _title(d, spec.title, y, rng, size=100, lines_max=3) + 50
    if spec.big:
        note = Image.new("RGBA", (S(sw), S(sh)), P["sticky"] + (255,))
        nd = ImageDraw.Draw(note)
        nd.rectangle([0, 0, S(sw), S(24)], fill=tuple(max(0, c - 12) for c in P["sticky"]) + (255,))
        f = F("sb", fit_size(spec.big, "sb", 72, sw - 70, 40))
        text(nd, sw / 2, sh / 2 + 14, spec.big, f, INK, anchor="mm")
        pad = S(30)
        out = Image.new("RGBA", (note.width + 2 * pad, note.height + 2 * pad), (0, 0, 0, 0))
        out.alpha_composite(_shadow(out.size, (pad + S(6), pad + S(12), pad + note.width - S(2), pad + note.height + S(4)),
                                    S(9), 95, (50, 38, 24)))
        out.alpha_composite(note, (pad, pad))
        paste_rot(canvas, out, M + sw / 2 + 10, y + sh / 2 + 10, rng.uniform(-3.5, -2))
        d = ImageDraw.Draw(canvas)
        y += sh + 70
    for ln in note_lines:
        y += 36
        text(d, M, y, ln, f_note, SUB)
        y += 36 * 0.45
    folio(d, P["paper"], "" if spec.footer == BRAND else spec.footer)
    return canvas.convert("RGB").resize((W, H), Image.Resampling.LANCZOS)


RENDER = {"deal": render_deal, "cover": render_cover, "list": render_list, "end": render_end}


def render(spec: SlideSpec) -> Image.Image:
    return RENDER[spec.kind](spec)
