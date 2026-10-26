"""'데런픽 룩' 인스타 캐러셀 카드 (주인 10/26 — @derenpick.look, 데런픽 패션 글).

인스타 글 기준(Blog-Auto 인스타 기준 문서 2~5장)대로:
- 1080×1350 (4:5). 글자는 가장자리 80px 안쪽, 오른쪽 위 210×110 은 비움 (인스타 장 번호 자리). 글꼴은 Pretendard.
- 제휴 링크가 든 글이라 **모든 장 왼쪽 위에 '광고' 칩** (브랜드는 그 옆으로). **셀럽 사진은 한 장도 쓰지 않는다** (퍼블리시티권) —
  쓰는 사진은 판매처 상품 사진뿐이고, 그 장에는 '사진: <판매처> 상품 이미지' 를 작게.
- 표지는 B(종이 띠): 위쪽은 상품 사진 모음, 아래 종이 띠에 작은 머리말 · 두 줄 제목 · 보조 줄 · 브랜드. 상품 사진이 하나도 없으면
  C(검은 바탕 글자 카드).
- 상품 카드: 번호 · 같은/비슷한 상품 · 글 속 아이템 · 상품 사진(자르지 않고 칸 안에) · 이름 · 가격 · 판매처 · 기준 날짜 ·
  '<인물> 착용 제품 아님' (비슷한 상품일 때).
- 끝 카드: 한눈에 정리(이름·가격) + 프로필 링크 안내 한 줄 + 가격 기준 날짜.
그리는 글은 모두 LookDeck 에서 온다 (texts() — 올리기 전 검사가 같은 글을 본다). 같은 덱이면 늘 같은 그림.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger(__name__)

W, H = 1080, 1350
M = 80  # 글자 안전 구역
CORNER = (210, 110)  # 오른쪽 위 비움 (가로·세로)
AD = "광고"
BRAND = "DEREN'S PICK"
NEXT = "넘겨 보기 →"

PAPER = (246, 245, 242)
TOP = (234, 232, 227)
INK = (17, 17, 17)
SUB = (104, 104, 104)
RED = (225, 29, 36)
DARK = (14, 14, 14)
WHITE = (247, 247, 245)
LINE = (70, 70, 70)
TILE = (255, 255, 255)

FONT_DIR = Path(__file__).with_name("fonts")
FONT_FILES = {"xb": "Pretendard-ExtraBold.otf", "sb": "Pretendard-SemiBold.otf", "md": "Pretendard-Medium.otf"}
_FALLBACK = {"xb": "sb", "md": "sb"}


class FontMissing(RuntimeError):
    """한글 글꼴이 없음 — 빈 네모 카드를 올리지 않게 멈춘다."""


@dataclass(slots=True)
class LookItem:
    name: str
    price: int = 0
    mall: str = ""
    image: str | None = None  # 판매처 상품 사진 파일 (없으면 사진 칸에 판매처 이름)
    piece: str = ""  # 글 속 아이템 ('블랙 레더 재킷')
    same: bool = False  # 같은 제품 (공식몰 착용 제품 · 같은 제품) — 아니면 비슷한 상품


@dataclass(slots=True)
class LookDeck:
    person: str  # 인물 이름 (글자로만 — 사진 없음)
    kicker: str  # 표지 작은 머리말 '카리나 · 공항 패션'
    title: list[str]  # 표지 두 줄
    sub: str  # 표지 보조 줄
    items: list[LookItem]
    end_line: str  # 끝 카드 프로필 링크 안내
    price_note: str = ""  # '가격은 10/26 기준이에요'
    price_day: str = ""  # '10/26'
    end_kicker: str = "한눈에 정리"
    fonts: dict[str, str] = field(default_factory=dict)  # 글꼴 바꿔 쓰기 {xb|sb|md: 파일} (시험·샘플)

    def texts(self) -> list[str]:
        """카드에 그리는 글 전부 (검사용)."""
        out = [AD, BRAND, self.kicker, *self.title, self.sub, self.end_kicker, self.end_line, self.price_note]
        for i, it in enumerate(self.items, 1):
            out += [item_label(i, it), piece_line(it), it.name, won(it.price), it.mall, not_worn(self.person, it)]
        return [t for t in out if t]


def won(price: int) -> str:
    return f"{int(price):,}원" if int(price or 0) > 0 else ""


def item_label(n: int, it: LookItem) -> str:
    return f"{n:02d} · {'같은 제품' if it.same else '비슷한 상품'}"


def piece_line(it: LookItem) -> str:
    p = " ".join(str(it.piece or "").split())
    return f"글 속 아이템: {p}" if p else ""


def not_worn(person: str, it: LookItem) -> str:
    """비슷한 상품이면 '<인물> 착용 제품 아님' (인스타 기준 5-4 — 그 사람이 입은 제품처럼 보이지 않게)."""
    return "" if it.same else (f"{person} 착용 제품 아님" if person else "착용 제품 아님")


# ---------------------------------------------------------------- 글꼴·글
@lru_cache(maxsize=96)
def _font_file(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def font(deck: LookDeck, key: str, px: int) -> ImageFont.FreeTypeFont:
    for k in (key, _FALLBACK.get(key, key), "sb"):
        path = deck.fonts.get(k) or str(FONT_DIR / FONT_FILES[k])
        try:
            return _font_file(path, int(px))
        except OSError:
            continue
    from dealbot.media.card import find_font

    sys_font = find_font(None)
    if sys_font:
        try:
            return _font_file(sys_font, int(px))
        except OSError:
            pass
    raise FontMissing(f"look card font missing: {FONT_FILES[key]}")


def tw(f: ImageFont.FreeTypeFont, s: str) -> float:
    return f.getlength(s) if s else 0.0


def fit(deck: LookDeck, key: str, size: int, lines: list[str], max_w: float, min_size: int) -> ImageFont.FreeTypeFont:
    """가장 긴 줄이 max_w 안에 들어가는 가장 큰 글꼴 (min_size 까지)."""
    px = size
    while px > min_size:
        f = font(deck, key, px)
        if max(tw(f, ln) for ln in lines if ln) <= max_w if any(lines) else True:
            return f
        px -= 2
    return font(deck, key, min_size)


def ellipsize(f: ImageFont.FreeTypeFont, s: str, max_w: float) -> str:
    if tw(f, s) <= max_w:
        return s
    while s and tw(f, s + "…") > max_w:
        s = s[:-1]
    return s.rstrip() + "…"


def wrap(f: ImageFont.FreeTypeFont, s: str, max_w: float, max_lines: int) -> list[str]:
    """낱말 단위로 줄 바꿈 (낱말 하나가 넘치면 글자 단위). 넘치는 마지막 줄은 '…'."""
    words, lines, cur = s.split(), [], ""
    for w in words:
        nxt = f"{cur} {w}".strip()
        if tw(f, nxt) <= max_w:
            cur = nxt
            continue
        if cur:
            lines.append(cur)
        cur = w
        while tw(f, cur) > max_w:  # 아주 긴 낱말
            cut = len(cur)
            while cut > 1 and tw(f, cur[:cut]) > max_w:
                cut -= 1
            lines.append(cur[:cut])
            cur = cur[cut:]
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = ellipsize(f, lines[-1] + " …", max_w) if not lines[-1].endswith("…") else lines[-1]
    return lines


# ---------------------------------------------------------------- 조각
def chip(d: ImageDraw.ImageDraw, deck: LookDeck, *, dark: bool) -> float:
    """왼쪽 위 '광고' 칩 + 그 옆 브랜드. 브랜드 끝 x 를 돌려줌 (오른쪽 위 비움 칸을 넘지 않음)."""
    f = font(deck, "sb", 30)
    w = tw(f, AD) + 36
    box = (M, 70, M + w, 70 + 50)
    d.rounded_rectangle(box, radius=10, fill=WHITE if dark else INK)
    d.text((M + w / 2, 95), AD, font=f, fill=INK if dark else WHITE, anchor="mm")
    return brand(d, deck, M + w + 30, 95, dark=dark)


def brand(d: ImageDraw.ImageDraw, deck: LookDeck, x: float, y: float, *, dark: bool) -> float:
    f = font(deck, "sb", 30)
    d.ellipse((x, y - 9, x + 18, y + 9), fill=RED)
    d.text((x + 30, y), BRAND, font=f, fill=WHITE if dark else INK, anchor="lm")
    return x + 30 + tw(f, BRAND)


def paste_contain(canvas: Image.Image, src: str | None, box: tuple[int, int, int, int], pad: int = 24) -> bool:
    """사진을 자르지 않고 칸 안에 (흰 바탕). 못 열면 False."""
    if not src:
        return False
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            x0, y0, x1, y1 = box
            bw, bh = x1 - x0 - 2 * pad, y1 - y0 - 2 * pad
            im.thumbnail((bw, bh), Image.LANCZOS)
            if im.width < bw and im.height < bh:  # 작은 사진은 칸에 맞게 키움 (흐림은 상품 사진 검수가 따로 봄)
                r = min(bw / im.width, bh / im.height)
                im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
            canvas.paste(im, (x0 + (x1 - x0 - im.width) // 2, y0 + (y1 - y0 - im.height) // 2))
            return True
    except Exception as e:  # noqa: BLE001 — 깨진 사진은 그 칸만 비움
        log.info("look card photo skipped: %s", type(e).__name__)
        return False


def tile(canvas: Image.Image, d: ImageDraw.ImageDraw, deck: LookDeck, it: LookItem, box: tuple[int, int, int, int]) -> None:
    d.rounded_rectangle(box, radius=18, fill=TILE)
    if not paste_contain(canvas, it.image, box):
        f = font(deck, "sb", 34)
        x0, y0, x1, y1 = box
        d.text(((x0 + x1) / 2, (y0 + y1) / 2), ellipsize(f, it.mall or "판매처", x1 - x0 - 40), font=f, fill=SUB, anchor="mm")


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _malls(deck: LookDeck, items: list[LookItem]) -> str:
    names = list(dict.fromkeys(it.mall for it in items if it.mall))
    return "·".join(names[:2]) + (" 외" if len(names) > 2 else "")


# ---------------------------------------------------------------- 장
def cover(deck: LookDeck) -> Image.Image:
    with_photo = [it for it in deck.items if it.image and Path(it.image).exists()]
    if not with_photo:
        return cover_text(deck)
    im = Image.new("RGB", (W, H), TOP)
    d = ImageDraw.Draw(im)
    band = 800
    # 위쪽: 상품 사진 모음 (자르지 않고 흰 칸 안에) — 셀럽 사진 없음
    x0, y0, x1, y1, g = M, 150, W - M, band - 40, 24
    shown = with_photo[:4]
    n = len(shown)
    if n == 1:
        boxes = [(x0 + 150, y0, x1 - 150, y1)]
    elif n == 2:
        mid = (x0 + x1) // 2
        boxes = [(x0, y0, mid - g // 2, y1), (mid + g // 2, y0, x1, y1)]
    elif n == 3:
        lx = x0 + int((x1 - x0) * 0.6)
        my = (y0 + y1) // 2
        boxes = [(x0, y0, lx - g // 2, y1), (lx + g // 2, y0, x1, my - g // 2), (lx + g // 2, my + g // 2, x1, y1)]
    else:
        mx, my = (x0 + x1) // 2, (y0 + y1) // 2
        boxes = [(x0, y0, mx - g // 2, my - g // 2), (mx + g // 2, y0, x1, my - g // 2),
                 (x0, my + g // 2, mx - g // 2, y1), (mx + g // 2, my + g // 2, x1, y1)]
    for it, box in zip(shown, boxes, strict=False):
        tile(im, d, deck, it, box)
    chip(d, deck, dark=False)
    # 아래 종이 띠
    d.rectangle((0, band, W, H), fill=PAPER)
    fk = font(deck, "sb", 34)
    fn = font(deck, "sb", 30)
    room = W - 2 * M - tw(fn, NEXT) - 40
    d.text((M, band + 70), ellipsize(fk, deck.kicker, room), font=fk, fill=SUB, anchor="ls")
    d.rectangle((M, band + 92, M + 90, band + 100), fill=RED)
    d.text((W - M, band + 70), NEXT, font=fn, fill=INK, anchor="rs")
    ft = fit(deck, "xb", 100, deck.title, W - 2 * M, 64)
    y = band + 140
    lh = int(ft.size * 1.12)
    for ln in deck.title[:2]:
        d.text((M, y), ln, font=ft, fill=INK, anchor="lt")
        y += lh
    fs = fit(deck, "md", 36, [deck.sub], W - 2 * M, 28)
    d.text((M, y + 22), deck.sub, font=fs, fill=SUB, anchor="lt")
    fc = font(deck, "md", 28)  # 브랜드는 '광고' 칩 옆에 이미 있음 — 바닥엔 사진 출처만
    credit = f"사진: {_malls(deck, shown)} 상품 이미지"
    d.text((M, H - 80), ellipsize(fc, credit, W - 2 * M), font=fc, fill=SUB, anchor="lm")
    return im


def cover_text(deck: LookDeck) -> Image.Image:
    """C 글자 카드 표지 (상품 사진이 하나도 없을 때)."""
    im = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(im)
    chip(d, deck, dark=True)
    fk = font(deck, "sb", 36)
    d.rectangle((M, 362, M + 9, 400), fill=RED)
    d.text((M + 28, 395), ellipsize(fk, deck.kicker, W - 2 * M - 30), font=fk, fill=(200, 200, 200), anchor="ls")
    ft = fit(deck, "xb", 128, deck.title, W - 2 * M, 72)
    y, lh = 460, int(ft.size * 1.15)
    for i, ln in enumerate(deck.title[:2]):
        d.text((M, y), ln, font=ft, fill=RED if i == 1 else WHITE, anchor="lt")
        y += lh
    fs = fit(deck, "md", 38, [deck.sub], W - 2 * M, 28)
    d.text((M, y + 30), deck.sub, font=fs, fill=(200, 200, 200), anchor="lt")
    d.text((W - M, H - 92), NEXT, font=font(deck, "sb", 32), fill=WHITE, anchor="rs")
    return im


def product(deck: LookDeck, n: int, it: LookItem) -> Image.Image:
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    chip(d, deck, dark=False)
    fl = font(deck, "sb", 36)
    d.text((M, 210), item_label(n, it), font=fl, fill=RED, anchor="ls")
    top = 240
    if (pl := piece_line(it)):
        fp = font(deck, "md", 32)
        d.text((M, 262), ellipsize(fp, pl, W - 2 * M), font=fp, fill=SUB, anchor="ls")
        top = 292
    has_photo = bool(it.image and Path(it.image).exists())
    if has_photo:
        box = (M, top, W - M, top + 620)
        tile(im, d, deck, it, box)
        fname = font(deck, "sb", 46)
        lines = wrap(fname, it.name, W - 2 * M, 2)
        y = box[3] + 50
    else:  # 사진이 없는 상품: 빈 칸 대신 이름을 크게 (꾸민 그림·가짜 사진 없음)
        fname = font(deck, "xb", 78)
        lines = wrap(fname, it.name, W - 2 * M, 3)
        y = top + 150
    for ln in lines:
        d.text((M, y), ln, font=fname, fill=INK, anchor="lt")
        y += int(fname.size * 1.25)
    fpz = font(deck, "xb", 84)
    price = won(it.price)
    if price:
        d.text((M, y + 14), price, font=fpz, fill=INK, anchor="lt")
        y += 14 + int(fpz.size * 1.12)
    fm = font(deck, "md", 32)
    meta = " · ".join(x for x in (it.mall, f"{deck.price_day} 기준" if deck.price_day and price else "") if x)
    if meta:
        d.text((M, y + 8), ellipsize(fm, meta, W - 2 * M), font=fm, fill=SUB, anchor="lt")
    fs = font(deck, "md", 28)
    if (nw := not_worn(deck.person, it)):
        d.text((M, H - 70), ellipsize(fs, nw, 430), font=fs, fill=SUB, anchor="ls")
    if has_photo:
        d.text((W - M, H - 70), ellipsize(fs, f"사진: {it.mall or '판매처'} 상품 이미지", 480), font=fs, fill=SUB, anchor="rs")
    return im


def end(deck: LookDeck) -> Image.Image:
    im = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(im)
    chip(d, deck, dark=True)
    fk = font(deck, "sb", 36)
    fn = font(deck, "sb", 42)
    fp = font(deck, "xb", 46)
    fm = font(deck, "md", 30)
    fnum = font(deck, "xb", 40)
    rows = deck.items[:4]
    step = 165 if len(rows) <= 3 else 135
    block = 60 + step * len(rows) + 10 + 60 + 50 + (70 if deck.price_note else 0)  # 머리말 ~ 기준 날짜
    top = max(230, int(160 + (H - 160 - 120 - block) / 2))  # 칩 아래 · 바닥 여백 사이 가운데
    d.text((M, top), ellipsize(fk, deck.end_kicker, W - 2 * M), font=fk, fill=RED, anchor="ls")
    y = top + 60
    for i, it in enumerate(rows, 1):
        d.text((M, y + 6), f"{i:02d}", font=fnum, fill=RED, anchor="lt")
        price = won(it.price)
        pw = tw(fp, price)
        nx = M + 90
        d.text((nx, y), ellipsize(fn, it.name, W - M - nx - pw - 40), font=fn, fill=WHITE, anchor="lt")
        if price:
            d.text((W - M, y), price, font=fp, fill=WHITE, anchor="rt")
        tag = " · ".join(x for x in (it.mall, "같은 제품" if it.same else "비슷한 상품") if x)
        d.text((nx, y + 62), ellipsize(fm, tag, W - M - nx), font=fm, fill=(170, 170, 170), anchor="lt")
        y += step
    y += 10
    d.line((M, y, W - M, y), fill=LINE, width=2)
    fe = fit(deck, "sb", 42, [deck.end_line], W - 2 * M, 32)
    d.text((M, y + 60), deck.end_line, font=fe, fill=WHITE, anchor="lt")
    if deck.price_note:
        d.text((M, y + 130), ellipsize(fm, deck.price_note, W - 2 * M), font=fm, fill=(170, 170, 170), anchor="lt")
    return im


def render(deck: LookDeck) -> list[tuple[bytes, str]]:
    """덱 → [(JPEG, 대체 텍스트)] — 표지 · 상품 카드 N장 · 끝 카드."""
    if not deck.items:
        raise ValueError("look deck needs items")
    who = deck.person or "셀럽"
    kinds = "같은 제품" if all(it.same for it in deck.items) else "비슷한 상품" if not any(it.same for it in deck.items) else "같은·비슷한 상품"
    out = [(_jpeg(cover(deck)), f"{who} 패션 {kinds} {len(deck.items)}가지 표지 (판매처 상품 사진)")]
    for i, it in enumerate(deck.items, 1):
        kind = "같은 제품" if it.same else "비슷한 상품"
        out.append((_jpeg(product(deck, i, it)), f"{kind} {i}: {it.name} {won(it.price)} {it.mall} 상품 사진".strip()))
    out.append((_jpeg(end(deck)), f"{who} 패션 비슷한 상품 가격 정리 카드"))
    return out
