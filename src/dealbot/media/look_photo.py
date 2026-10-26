"""데런픽 룩 카드의 상품 사진 다루기 (media/look_styles A·B·C 가 같이 씀).

몰 상품 사진은 두 가지로 온다: 흰·회색 스튜디오 바탕(누끼 컷이 되는 사진)과 바탕이 복잡한 연출 사진. 어느 쪽이든 '대충 붙인' 느낌이
나지 않게:
- 바탕이 고른 사진(clean): 사진 바탕색을 그대로 판(plate) 색으로 써서 사진 가장자리가 판에 녹아들게 (가장자리 흐림 마스크). 상품만
  오려지면(cut) 색판 위에 띄우고 그림자를 깐다. 오리기가 미심쩍으면(상품이 바탕과 비슷한 색 등) 오리지 않는다.
- 바탕이 복잡한 사진(busy): 판을 꽉 채우게 잘라 쓴다 (가운데 기준).
- 상품 색(tone)으로 어울리는 연한 색판(swatch)을 고른다 — 사진에서 뽑은 색을 그대로 쓰면 탁해져서, 정해 둔 색 중 가까운 것.
셀럽 사진은 여기 오지 않는다 (판매처 상품 사진 사본만 — look_share.check · card_look.render 의 photo_root).
"""

from __future__ import annotations

import colorsys
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat

log = logging.getLogger(__name__)

RGB = tuple[int, int, int]

# 색판 — 사진 색과 가까운 것 하나 (채도 낮은 상품은 무채색 판)
SWATCHES: dict[str, RGB] = {
    "stone": (234, 230, 223),
    "sand": (238, 229, 214),
    "mist": (223, 230, 237),
    "sage": (225, 231, 220),
    "blush": (241, 227, 223),
    "butter": (243, 235, 212),
    "lilac": (231, 227, 239),
}
_HUES = (("blush", 0.0), ("sand", 0.08), ("butter", 0.14), ("sage", 0.30), ("mist", 0.58), ("lilac", 0.75), ("blush", 0.95))


@dataclass(slots=True)
class Photo:
    img: Image.Image  # RGB 원본
    clean: bool  # 바탕이 고른 사진
    bg: RGB  # 바탕색 (테두리 가운데 값)
    mask: Image.Image | None  # 오린 모양 (L, 원본 크기, 255 = 상품) — 깔끔히 오려졌을 때만
    bbox: tuple[int, int, int, int]  # 상품이 있는 칸 (원본 좌표)
    tone: RGB  # 상품의 대표 색

    @property
    def cut(self) -> bool:
        return self.mask is not None


@lru_cache(maxsize=6)  # 한 덱(상품 4개까지)을 그리는 동안만 — 사진이 커서 많이 들고 있지 않음
def _load(path: str, mtime: float) -> Photo | None:
    try:
        with Image.open(path) as raw:
            im = ImageOps.exif_transpose(raw)
            if im.mode in ("RGBA", "LA", "P", "PA"):
                im = im.convert("RGBA")
                base = Image.new("RGBA", im.size, (255, 255, 255, 255))
                base.alpha_composite(im)
                im = base
            im = im.convert("RGB")
    except Exception as e:  # noqa: BLE001 — 깨진 사진은 그 칸만 사진 없이
        log.info("look photo unusable: %s", type(e).__name__)
        return None
    if min(im.size) < 40:
        return None
    if max(im.size) > 1400:
        im.thumbnail((1400, 1400), Image.Resampling.LANCZOS)
    return analyze(im)


def load(path: str | None) -> Photo | None:
    if not path:
        return None
    p = Path(path)
    try:
        return _load(str(p), p.stat().st_mtime)
    except OSError:
        return None


def _border(im: Image.Image) -> tuple[RGB, bool]:
    w, h = im.size
    b = max(2, min(w, h) // 40)
    strips = [im.crop((0, 0, w, b)), im.crop((0, h - b, w, h)), im.crop((0, 0, b, h)), im.crop((w - b, 0, w, h))]
    stats = [ImageStat.Stat(s) for s in strips]
    means = [s.mean for s in stats]
    spread = max(max(m[c] for m in means) - min(m[c] for m in means) for c in range(3))
    clean = max(max(s.stddev) for s in stats) < 12 and spread < 16
    joined = Image.new("RGB", (w * 2 + h * 2, b))
    x = 0
    for s in strips:
        s2 = s if s.size[1] == b else s.transpose(Image.Transpose.ROTATE_90)
        joined.paste(s2, (x, 0))
        x += s2.size[0]
    med = ImageStat.Stat(joined).median
    return (int(med[0]), int(med[1]), int(med[2])), clean


def _cut_mask(im: Image.Image, bg: RGB) -> Image.Image | None:
    """테두리에서 바탕을 채워 상품 모양. 미심쩍으면 None (오리지 않고 판에 녹이기)."""
    small = im.copy()
    small.thumbnail((420, 420))
    w, h = small.size
    key = (255, 0, 255) if bg != (255, 0, 255) else (0, 255, 0)
    near = 34
    pts = [(x, 0) for x in range(0, w, 4)] + [(x, h - 1) for x in range(0, w, 4)]
    pts += [(0, y) for y in range(0, h, 4)] + [(w - 1, y) for y in range(0, h, 4)]
    for p in pts:
        px = small.getpixel(p)
        if px != key and max(abs(int(a) - b) for a, b in zip(px, bg, strict=True)) < near:  # type: ignore[arg-type]
            ImageDraw.floodfill(small, p, key, thresh=32)
    diff = ImageChops.difference(small, Image.new("RGB", small.size, key)).convert("L")
    mask = diff.point(lambda v: 255 if v >= 6 else 0)
    mask = mask.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.MinFilter(3))
    ratio = ImageStat.Stat(mask).mean[0] / 255
    box = mask.getbbox()
    if box is None or not 0.06 < ratio < 0.85:
        return None
    if box[0] <= 1 and box[1] <= 1 and box[2] >= w - 1 and box[3] >= h - 1:
        return None  # 네 변에 다 닿음 — 바탕이 아니라 상품을 채운 것
    # 상품 칸 안에서 상품이 차지하는 비율 — 너무 적으면 바탕 얼룩을 상품으로 본 것
    fill = ImageStat.Stat(mask.crop(box)).mean[0] / 255
    if fill < 0.30:
        return None
    # 가장자리 띠에서는 바탕색과 얼마나 다른지로 투명도를 정함 — 흰 테두리(바탕 번짐)는 빠지고 사진 속 그림자는 옅게 남는다.
    # 띠 안쪽(상품 속 흰 로고 등)은 그대로 상품.
    def up(m: Image.Image) -> Image.Image:
        return m.resize(im.size, Image.Resampling.BILINEAR).point(lambda v: 255 if v > 127 else 0)

    inner = up(mask.filter(ImageFilter.MinFilter(5)))
    outer = up(mask.filter(ImageFilter.MaxFilter(3)))
    chans = ImageChops.difference(im, Image.new("RGB", im.size, bg)).split()
    dist = ImageChops.lighter(ImageChops.lighter(chans[0], chans[1]), chans[2])
    soft = dist.point(lambda v: 0 if v < 12 else 255 if v > 52 else int((v - 12) * 255 / 40))
    alpha = ImageChops.lighter(inner, ImageChops.darker(outer, soft))
    return alpha.filter(ImageFilter.GaussianBlur(0.7))


def _tone(im: Image.Image, mask: Image.Image | None, bg: RGB, clean: bool) -> RGB:
    small = im.copy()
    small.thumbnail((120, 120))
    if mask is not None:
        m = mask.resize(small.size).point(lambda v: 255 if v > 128 else 0)
    else:
        w, h = small.size
        m = Image.new("L", small.size, 0)
        ImageDraw.Draw(m).rectangle((w * 0.2, h * 0.2, w * 0.8, h * 0.8), fill=255)
    q = small.quantize(6)
    pal = q.getpalette() or []
    counts: dict[int, int] = {}
    for idx, mv in zip(q.getdata(), m.getdata(), strict=True):
        if mv:
            counts[idx] = counts.get(idx, 0) + 1
    best: RGB | None = None
    for idx, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        c = (pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2])
        if clean and max(abs(a - b) for a, b in zip(c, bg, strict=True)) < 18:
            continue
        best = c
        break
    return best or bg


def analyze(im: Image.Image) -> Photo:
    bg, clean = _border(im)
    mask = _cut_mask(im, bg) if clean and lum(bg) > 170 else None  # 오리기는 밝은 스튜디오 바탕만 (어두운 바탕은 상품과 섞임)
    if mask is not None:
        bbox = mask.point(lambda v: 255 if v > 128 else 0).getbbox() or (0, 0, *im.size)
    elif clean:
        diff = ImageChops.difference(im, Image.new("RGB", im.size, bg)).convert("L").point(lambda v: 255 if v > 18 else 0)
        bbox = diff.filter(ImageFilter.MedianFilter(5)).getbbox() or (0, 0, *im.size)
    else:
        bbox = (0, 0, *im.size)
    return Photo(img=im, clean=clean, bg=bg, mask=mask, bbox=bbox, tone=_tone(im, mask, bg, clean))


# ---------------------------------------------------------------- 색
def mix(a: RGB, b: RGB, t: float) -> RGB:
    """a 와 b 사이 (t=0 → a, 1 → b)."""
    return (int(round(a[0] + (b[0] - a[0]) * t)), int(round(a[1] + (b[1] - a[1]) * t)), int(round(a[2] + (b[2] - a[2]) * t)))


def swatch_name(tone: RGB) -> str:
    h, light, s = colorsys.rgb_to_hls(*(c / 255 for c in tone))
    if s < 0.18 or light < 0.12 or light > 0.92:
        return "stone" if light < 0.6 else "sand"
    return min(_HUES, key=lambda hv: min(abs(h - hv[1]), 1 - abs(h - hv[1])))[0]


def swatch(tone: RGB) -> RGB:
    return SWATCHES[swatch_name(tone)]


def lum(c: RGB) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


# ---------------------------------------------------------------- 판에 앉히기
def _pad_box(box: tuple[int, int, int, int], size: tuple[int, int], frac: float) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    mx, my = int((x1 - x0) * frac) + 2, int((y1 - y0) * frac) + 2
    return (max(0, x0 - mx), max(0, y0 - my), min(size[0], x1 + mx), min(size[1], y1 + my))


def cover(img: Image.Image, size: tuple[int, int], fx: float = 0.5, fy: float = 0.5) -> Image.Image:
    """칸을 꽉 채우게 잘라 키움."""
    bw, bh = size
    iw, ih = img.size
    if iw / ih > bw / bh:
        cw, ch = ih * bw / bh, ih
    else:
        cw, ch = iw, iw * bh / bw
    x0, y0 = (iw - cw) * fx, (ih - ch) * fy
    out = img.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch))).resize(size, Image.Resampling.LANCZOS)
    if bw / max(1, cw) > 1.6:
        out = out.filter(ImageFilter.UnsharpMask(radius=2, percent=50, threshold=2))
    return out


LOSS_MAX = 1.75  # 이보다 많이 잘려 나가면(가로 칸에 세로 사진 등) 자르지 않고 흐린 같은 사진 위에 통째로


def framed(img: Image.Image, size: tuple[int, int], fill: RGB, inset: float) -> Image.Image:
    """복잡한 사진을 자르지 않고 색판 가운데에 (얇은 그림자) — 잡지 지면에 사진을 앉힌 것처럼."""
    bw, bh = size
    pad = min(bw, bh) * max(0.06, inset * 0.8)
    pw, ph = _fit(img.width, img.height, bw - 2 * pad, bh - 2 * pad)
    base = Image.new("RGBA", size, fill + (255,))
    x, y = (bw - pw) // 2, (bh - ph) // 2
    card = Image.new("RGBA", (pw, ph), (0, 0, 0, 255))
    sh, sp = drop_shadow(card, blur=max(5, bw / 90), alpha=0.22)
    base.alpha_composite(sh, (x - sp, y - sp + max(2, int(bh / 150))))
    base.paste(img.resize((pw, ph), Image.Resampling.LANCZOS), (x, y))
    return base.convert("RGB")


def contain_on_blur(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """복잡한 사진을 자르지 않고: 같은 사진을 크게 흐려 깐 바탕 위에 통째로 (상품이 잘려 나가지 않게)."""
    bw, bh = size
    small = cover(img, (max(1, bw // 8), max(1, bh // 8)))
    back = small.filter(ImageFilter.GaussianBlur(max(2, min(bw, bh) // 60))).resize(size, Image.Resampling.BICUBIC)
    mean = tuple(int(v) for v in ImageStat.Stat(back).mean)
    back = Image.blend(back, Image.new("RGB", size, mean), 0.35)
    pw, ph = _fit(img.width, img.height, bw, bh)
    fg = img.resize((pw, ph), Image.Resampling.LANCZOS)
    x, y = (bw - pw) // 2, (bh - ph) // 2
    sh = Image.new("L", size, 0)
    ImageDraw.Draw(sh).rectangle((x, y + max(2, bh // 120), x + pw, y + ph + max(2, bh // 120)), fill=90)
    sh = sh.filter(ImageFilter.GaussianBlur(max(4, bw // 80)))
    back.paste(Image.new("RGB", size, (20, 16, 12)), (0, 0), sh)
    back.paste(fg, (x, y))
    return back


def _fit(w: int, h: int, bw: float, bh: float) -> tuple[int, int]:
    r = min(bw / w, bh / h)
    return max(1, int(w * r)), max(1, int(h * r))


def _feather(size: tuple[int, int], px: int) -> Image.Image:
    """가장자리로 갈수록 0 이 되는 마스크 (사진 가장자리를 판에 녹임)."""
    w, h = size
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rectangle((px, px, w - 1 - px, h - 1 - px), fill=255)
    return m.filter(ImageFilter.GaussianBlur(max(1, px / 2)))


def cutout(photo: Photo, max_w: int, max_h: int) -> Image.Image:
    """오린 상품 (RGBA, 상품 칸에 꼭 맞게). 오리지 못한 사진이면 바탕째 (가장자리 흐림)."""
    box = _pad_box(photo.bbox, photo.img.size, 0.015)
    src = photo.img.crop(box)
    pw, ph = _fit(src.width, src.height, max_w, max_h)
    out = src.resize((pw, ph), Image.Resampling.LANCZOS).convert("RGBA")
    if photo.mask is not None:
        out.putalpha(photo.mask.crop(box).resize((pw, ph), Image.Resampling.LANCZOS))
    else:
        out.putalpha(_feather((pw, ph), max(4, min(pw, ph) // 30)))
    return out


def drop_shadow(rgba: Image.Image, *, blur: float, alpha: float, color: RGB = (40, 32, 24), spread: int = 0) -> tuple[Image.Image, int]:
    """상품 모양 그림자 (RGBA) 와 바깥 여백 pad."""
    pad = int(blur * 3) + spread
    a = rgba.getchannel("A").point(lambda v: int(v * alpha))
    if spread:
        a = a.filter(ImageFilter.MaxFilter(spread | 1))
    sh = Image.new("L", (rgba.width + 2 * pad, rgba.height + 2 * pad), 0)
    sh.paste(a, (pad, pad))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    out = Image.new("RGBA", sh.size, color + (0,))
    out.putalpha(sh)
    return out, pad


def plate(photo: Photo, size: tuple[int, int], *, fill: RGB | None = None, inset: float = 0.10,
          fy: float = 0.5, shadow: bool = True, scale: float = 1.0, loss_max: float = 0.0) -> Image.Image:
    """사진 한 장을 size 판에 (RGB).

    - 오려진 사진 + fill: fill 색판 위에 상품을 띄우고 그림자.
    - 바탕이 고른 사진: 판 색 = 사진 바탕색, 상품을 안쪽 여백 inset 으로 (가장자리를 흐려 판에 녹임).
    - 복잡한 사진: 판을 꽉 채우게 자름.
    """
    bw, bh = size
    if not photo.clean:
        iw, ih = photo.img.size
        loss = max((iw / ih) / (bw / bh), (bw / bh) / (iw / ih))  # 꽉 채우면 잘려 나가는 비율
        if loss <= (loss_max or LOSS_MAX):
            return cover(photo.img, size, 0.5, fy)
        if fill is not None:
            return framed(photo.img, size, fill, inset)
        return contain_on_blur(photo.img, size)
    if photo.cut and fill is not None:
        base = Image.new("RGBA", size, fill + (255,))
        prod = cutout(photo, int(bw * (1 - 2 * inset) * scale), int(bh * (1 - 2 * inset) * scale))
        x, y = (bw - prod.width) // 2, int((bh - prod.height) * fy)
        if shadow:
            sh, pad = drop_shadow(prod, blur=max(6, bw / 60), alpha=0.28)
            base.alpha_composite(sh, (x - pad + int(bw / 140), y - pad + int(bh / 90)))
        base.alpha_composite(prod, (x, y))
        return base.convert("RGB")
    base = Image.new("RGB", size, photo.bg)
    box = _pad_box(photo.bbox, photo.img.size, 0.03)
    src = photo.img.crop(box)
    pw, ph = _fit(src.width, src.height, bw * (1 - 2 * inset) * scale, bh * (1 - 2 * inset) * scale)
    # 여백까지 같이 잘라 가장자리를 판에 녹임
    margin = 0.06
    big = photo.img.crop(_pad_box(box, photo.img.size, margin))
    r = pw / src.width
    gw, gh = max(1, int(big.width * r)), max(1, int(big.height * r))
    g = big.resize((gw, gh), Image.Resampling.LANCZOS)
    x, y = (bw - gw) // 2, int((bh - gh) * fy)
    base.paste(g, (x, y), _feather((gw, gh), max(6, min(gw, gh) // 18)))
    return base


def round_mask(size: tuple[int, int], radius: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return m
