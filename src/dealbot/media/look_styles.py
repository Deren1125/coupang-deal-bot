"""데런픽 룩 캐러셀 — 새 디자인 세 갈래 (instagram.look.card_style: A | B | C). 주인 10/26 '카드가 너무 건성건성' 에 다시 그린 시안.

- A 화보형 (editorial): 잡지 '겟 더 룩' 지면. 따뜻한 종이색, 상품을 크게 한 장(색판 위 누끼 또는 사진 판), DM Serif 머리글·가격 숫자,
  아래 가는 줄 + 작은 글(폴리오).
- B 코디 보드형 (outfit board): 연한 색판 위에 모자→상의→하의 순으로 한 벌을 세우고 번호표·설명줄. 상품 장은 흰 카드 + 가격표 카드.
- C 무드보드형 (scrapbook): 종이 결 위 인화 사진 + 마스킹 테이프, 손글씨(감자꽃)는 꾸밈 글에만 — 고지·이름·가격은 Pretendard·Bodoni.

지키는 것 (세 갈래 모두, tests/test_ig_look_design.py):
- 1080×1350. 모든 장 왼쪽 위 '광고' 표시 + 브랜드 'DEREN'S PICK'(빨간 점). 글은 가장자리 80px 안, 오른쪽 위 210×110 은 비움(장 번호 자리).
- 셀럽 사진 없음 — 그리는 사진은 LookItem.image(판매처 상품 사진 사본)뿐. 비슷한 상품이면 '<인물> 착용 제품 아님'.
- 상품 장마다 가격 · 판매처 · '<날짜> 기준'. 상품 이름은 1~2줄 (글자를 줄여서라도 '…' 를 피함). 주소 글자 없음, 끝 장은 '프로필 링크'.
- 그리는 글은 덱의 글(LookDeck.texts(style))과 숫자뿐 — Sheet 가 그린 글을 모두 적어 둔다 (검사·시험용).
2배로 그리고 마지막에 줄인다. 같은 덱이면 늘 같은 그림 (기울기·테이프는 덱에서 만든 씨앗).
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import math
import random
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from dealbot.media import look_photo as lp
from dealbot.media.card_look import (
    AD,
    BRAND,
    CORNER,
    FONT_DIR,
    NEXT,
    H,
    LookDeck,
    LookItem,
    M,
    W,
    not_worn,
    piece_line,
    won,
)
from dealbot.media.card_look import (
    font as deck_font,
)

STYLES = ("A", "B", "C")
K = 2  # 2배로 그림
RED = (225, 29, 36)
PEN = (204, 36, 40)
SAFE_BOTTOM = H - M  # 글 바닥
FOLIO_Y = 1226  # 바닥 가는 줄 (A)

LATIN = {
    "serif": "DMSerifDisplay-400.ttf",
    "jost": "Jost-500.ttf",
    "bodoni": "BodoniModa-Medium-opsz11.ttf",
    "hand": "GamjaFlower-Regular-KSX1001.ttf",
}
_LATIN_FALLBACK = {"serif": "sb", "jost": "sb", "bodoni": "sb", "hand": "md"}

GET_THE_LOOK = "Get the Look"
TOTAL = "모두 합쳐"
STYLE_WORDS: dict[str, tuple[str, ...]] = {"A": (GET_THE_LOOK,), "B": (TOTAL,), "C": ()}
NUMERIC = re.compile(r"^[\d\s/·.,원No]+$")


def credit(items: list[LookItem]) -> str:
    """사진 출처 한 줄 '사진: 에이블리·지그재그 외 상품 이미지'."""
    names = list(dict.fromkeys(it.mall for it in items if it.mall))
    if not names:
        return "사진: 판매처 상품 이미지"
    return f"사진: {'·'.join(names[:2])}{' 외' if len(names) > 2 else ''} 상품 이미지"


def date_line(deck: LookDeck) -> str:
    return f"{deck.price_day} 기준" if deck.price_day else ""


def style_texts(deck: LookDeck, style: str) -> list[str]:
    """이 갈래가 덱 글 말고 더 그리는 글 (LookDeck.texts(style) 에 들어감)."""
    out = [*STYLE_WORDS.get(style, ()), NEXT, credit(deck.items), date_line(deck)]
    shown = [it for it in deck.items if it.image]
    out += [credit(shown[:n]) for n in range(1, len(shown) + 1)]
    for it in deck.items:
        label = "같은 제품" if it.same else "비슷한 상품"
        nw = not_worn(deck.person, it)
        out += [label, it.piece, credit([it]), f"{label} · {nw}" if nw else "", f"{deck.end_line} →"]
    out += [f"{it.mall} · {date_line(deck)}" for it in deck.items if it.mall and deck.price_day]
    return [t for t in out if t]


# ---------------------------------------------------------------- 그리는 판 (그린 글을 적어 둠)
@dataclass(slots=True)
class Drawn:
    text: str
    box: tuple[float, float, float, float]  # 1배 좌표


@dataclass(slots=True)
class Sheet:
    deck: LookDeck
    bg: tuple[int, int, int]
    im: Image.Image = field(init=False)
    d: ImageDraw.ImageDraw = field(init=False)
    drawn: list[Drawn] = field(default_factory=list)
    photos: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)  # (사진 파일, 칸)

    def __post_init__(self) -> None:
        self.im = Image.new("RGB", (W * K, H * K), self.bg)
        self.d = ImageDraw.Draw(self.im)

    # 글꼴 · 글
    def f(self, key: str, px: float) -> ImageFont.FreeTypeFont:
        return _font(self.deck, key, int(round(px)))

    def w(self, key: str, px: float, s: str, track: float = 0.0) -> float:
        if not s:
            return 0.0
        f = self.f(key, px)
        if not track:
            return f.getlength(s) / K
        return sum(f.getlength(ch) / K for ch in s) + track * px * (len(s) - 1)

    def text(self, x: float, y: float, s: str, key: str, px: float, fill: object, anchor: str = "ls",
             track: float = 0.0) -> tuple[float, float, float, float]:
        """글 하나 (x, y 는 1배). anchor 는 Pillow 와 같음 (l/m/r + a/t/m/s/b). 그린 칸을 돌려주고 적어 둠."""
        if not s:
            return (x, y, x, y)
        f = self.f(key, px)
        width = self.w(key, px, s, track)
        h_anchor, v_anchor = anchor[0], anchor[1]
        x0 = x - (width if h_anchor == "r" else width / 2 if h_anchor == "m" else 0)
        if not track:
            self.d.text((x0 * K, y * K), s, font=f, fill=fill, anchor="l" + v_anchor)
        else:
            cx = x0
            for ch in s:
                self.d.text((cx * K, y * K), ch, font=f, fill=fill, anchor="l" + v_anchor)
                cx += f.getlength(ch) / K + track * px
        bb = self.d.textbbox((x0 * K, y * K), s, font=f, anchor="l" + v_anchor)
        box = (x0, bb[1] / K, x0 + width, bb[3] / K)
        self.drawn.append(Drawn(s, box))
        return box

    # 도형
    def rect(self, box: tuple[float, float, float, float], fill: object = None, outline: object = None,
             width: float = 1, r: float = 0) -> None:
        b = [v * K for v in box]
        if r:
            self.d.rounded_rectangle(b, radius=r * K, fill=fill, outline=outline, width=int(round(width * K)))
        else:
            self.d.rectangle(b, fill=fill, outline=outline, width=int(round(width * K)))

    def line(self, pts: list[tuple[float, float]], fill: object, width: float = 1) -> None:
        self.d.line([(a * K, b * K) for a, b in pts], fill=fill, width=max(1, int(round(width * K))))

    def ellipse(self, box: tuple[float, float, float, float], fill: object = None, outline: object = None, width: float = 1) -> None:
        self.d.ellipse([v * K for v in box], fill=fill, outline=outline, width=int(round(width * K)))

    def paste(self, img: Image.Image, x: float, y: float, mask: Image.Image | None = None) -> None:
        """img 는 이미 2배 크기. RGBA 면 투명도대로."""
        pos = (int(round(x * K)), int(round(y * K)))
        if img.mode == "RGBA" and mask is None:
            base = self.im.convert("RGBA")
            base.alpha_composite(img, pos)
            self.im = base.convert("RGB")
            self.d = ImageDraw.Draw(self.im)
        else:
            self.im.paste(img, pos, mask)

    def photo(self, it: LookItem, box: tuple[float, float, float, float]) -> None:
        if it.image:
            self.photos.append((it.image, box))

    def final(self) -> Image.Image:
        return self.im.resize((W, H), Image.Resampling.LANCZOS)


def S(v: float) -> int:
    return max(1, int(round(v * K)))


@lru_cache(maxsize=128)
def _latin(path: str, px: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, px)


def _font(deck: LookDeck, key: str, px: int) -> ImageFont.FreeTypeFont:
    if key in LATIN:
        try:
            return _latin(str(FONT_DIR / LATIN[key]), px * K)
        except OSError:
            key = _LATIN_FALLBACK[key]
    return deck_font(deck, key, px * K)


_GLYPHS: dict[tuple[str, str], bool] = {}


def covers(deck: LookDeck, key: str, s: str) -> bool:
    """글꼴에 이 글자가 다 있나 (부분 글꼴 손글씨 — 없는 글자는 빈 네모)."""
    if key not in LATIN:
        return True
    try:
        f = _latin(str(FONT_DIR / LATIN[key]), 40)
    except OSError:
        return False

    def ink(ch: str) -> bytes:
        im = Image.new("L", (60, 60), 0)
        ImageDraw.Draw(im).text((4, 4), ch, font=f, fill=255)
        return im.tobytes()

    notdef = None
    for ch in set(s):
        if ch.isspace():
            continue
        if (key, ch) not in _GLYPHS:
            notdef = notdef if notdef is not None else ink("￿")
            _GLYPHS[(key, ch)] = ink(ch) != notdef
        if not _GLYPHS[(key, ch)]:
            return False
    return True


def wrap(sh: Sheet, s: str, key: str, px: float, max_w: float) -> list[str]:
    """띄어쓰기에서 먼저, 낱말 하나가 넘치면 글자 단위."""
    lines: list[str] = []
    cur = ""
    for word in s.split():
        cand = f"{cur} {word}" if cur else word
        if sh.w(key, px, cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:
            if sh.w(key, px, cur + ch) <= max_w or not cur:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
    if cur:
        lines.append(cur)
    return lines


def balance(sh: Sheet, lines: list[str], key: str, px: float, max_w: float) -> list[str]:
    """두 줄이면 줄 길이를 고르게 (끝에 낱말 하나만 남는 줄을 피함)."""
    if len(lines) != 2:
        return lines
    words = " ".join(lines).split()
    best, best_w = lines, max(sh.w(key, px, ln) for ln in lines)
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        wa, wb = sh.w(key, px, a), sh.w(key, px, b)
        if wa <= max_w and wb <= max_w and max(wa, wb) < best_w:
            best, best_w = [a, b], max(wa, wb)
    return best


def fit_lines(sh: Sheet, s: str, key: str, sizes: tuple[float, ...], max_w: float, max_lines: int = 2,
              extra: bool = True) -> tuple[float, list[str]]:
    """들어가는 가장 큰 크기 (max_lines 줄 안, 두 줄이면 고르게). 가장 작은 크기로도 넘치면 한 줄 더, 그래도 넘치면 마지막 줄 '…'."""
    for px in sizes:
        lines = wrap(sh, s, key, px, max_w)
        if len(lines) <= max_lines:
            return px, balance(sh, lines, key, px, max_w)
    px = sizes[-1]
    lines = wrap(sh, s, key, px, max_w)
    room = max_lines + (1 if extra else 0)
    if len(lines) <= room:
        return px, lines
    keep = lines[:room]
    last = keep[-1]
    while last and sh.w(key, px, last + "…") > max_w:
        last = last[:-1]
    keep[-1] = last.rstrip() + "…"
    return px, keep


def fit_px(sh: Sheet, s: str, key: str, px: float, max_w: float, min_px: float, track: float = 0.0) -> float:
    while px > min_px and sh.w(key, px, s, track) > max_w:
        px -= 1
    return px


def seed(deck: LookDeck, *parts: object) -> random.Random:
    key = "|".join([deck.person, *deck.title, *(it.name for it in deck.items), *map(str, parts)])
    return random.Random(int(hashlib.md5(key.encode()).hexdigest()[:12], 16))


def split_price(price: str) -> tuple[str, str]:
    m = re.match(r"^([\d,]+)(.*)$", price)
    return (m.group(1), m.group(2)) if m else (price, "")


def price(sh: Sheet, x: float, base: float, it: LookItem, num_key: str, px: float, fill: object, *,
          unit_px: float | None = None, right: bool = False, unit_fill: object | None = None) -> float:
    """가격: 숫자는 num_key 글꼴, '원' 은 Pretendard. right 면 x 가 오른쪽 끝. 그린 너비를 돌려줌."""
    p = won(it.price)
    if not p:
        return 0.0
    num, unit = split_price(p)
    upx = unit_px or max(22, px * 0.40)
    gap = px * 0.05
    total = sh.w(num_key, px, num) + gap + sh.w("sb", upx, unit)
    x0 = x - total if right else x
    sh.text(x0, base, num, num_key, px, fill)
    sh.text(x0 + sh.w(num_key, px, num) + gap, base, unit, "sb", upx, unit_fill or fill)
    return total


# ---------------------------------------------------------------- 공통 조각
def ad_brand(sh: Sheet, *, dark: bool = False, shape: str = "pill", ink: tuple[int, int, int] = (18, 18, 18),
             paper: tuple[int, int, int] = (250, 250, 248)) -> float:
    """왼쪽 위 '광고' 표시 + 'DEREN'S PICK'. 브랜드 끝 x."""
    fg, bg = (paper, ink) if not dark else (ink, paper)
    px = 26
    w = sh.w("sb", px, AD) + 30
    top, h = M, 46
    sh.rect((M, top, M + w, top + h), fill=bg, r=h / 2 if shape == "pill" else 6 if shape == "soft" else 0)
    sh.text(M + w / 2, top + h / 2 + 1, AD, "sb", px, fg, anchor="mm")
    x = M + w + 22
    cy = top + h / 2
    sh.ellipse((x, cy - 7, x + 14, cy + 7), fill=RED)
    bx = x + 26
    sh.text(bx, cy + 1, BRAND, "jost", 23, bg if not dark else paper, anchor="lm", track=0.14)
    return bx + sh.w("jost", 23, BRAND, 0.14)


def item_photo(it: LookItem) -> lp.Photo | None:
    return lp.load(it.image) if it.image and Path(it.image).exists() else None


_ORDER = (
    ("head", ("비니", "모자", "캡", "버킷", "베레", "헤어", "귀마개")),
    ("outer", ("재킷", "자켓", "코트", "점퍼", "패딩", "가디건", "블레이저", "아우터", "베스트", "조끼")),
    ("top", ("니트", "셔츠", "블라우스", "티", "맨투맨", "후드", "탑", "스웨터", "원피스", "드레스")),
    ("bottom", ("데님", "청바지", "팬츠", "바지", "슬랙스", "스커트", "치마", "쇼츠", "레깅스", "진")),
    ("shoes", ("스니커즈", "운동화", "부츠", "로퍼", "구두", "샌들", "슬리퍼", "힐", "신발", "플랫")),
    ("bag", ("가방", "백", "토트", "숄더", "크로스", "파우치")),
)


def part_of(it: LookItem) -> int:
    """코디 순서 (머리 0 · 아우터 1 · 상의 2 · 하의 3 · 신발 4 · 가방 5 · 모름 6)."""
    words = f"{it.piece} {it.name}"
    for i, (_, keys) in enumerate(_ORDER):
        if any(k in words for k in keys):
            return i
    return len(_ORDER)


def deck_tone(deck: LookDeck) -> tuple[int, int, int]:
    for it in deck.items:
        p = item_photo(it)
        if p is not None:
            return p.tone
    return (120, 120, 120)


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92, optimize=True)
    return buf.getvalue()


def rounded(img: Image.Image, radius: float) -> Image.Image:
    out = img.convert("RGBA")
    out.putalpha(lp.round_mask(img.size, S(radius)))
    return out


def soft_shadow(size: tuple[int, int], radius: float, *, blur: float, alpha: int, color: tuple[int, int, int] = (40, 34, 28)) -> tuple[Image.Image, int]:
    """둥근 네모 그림자 (2배). (그림, 바깥 여백)."""
    pad = S(blur * 3)
    m = Image.new("L", (size[0] + 2 * pad, size[1] + 2 * pad), 0)
    ImageDraw.Draw(m).rounded_rectangle((pad, pad, pad + size[0], pad + size[1]), radius=S(radius), fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(S(blur)))
    out = Image.new("RGBA", m.size, color + (0,))
    out.putalpha(m)
    return out, pad


def card(sh: Sheet, box: tuple[float, float, float, float], fill: tuple[int, int, int], radius: float, *,
         shadow: int = 26, blur: float = 18, dy: float = 10) -> None:
    x0, y0, x1, y1 = box
    sz = (S(x1 - x0), S(y1 - y0))
    if shadow:
        shd, pad = soft_shadow(sz, radius, blur=blur, alpha=shadow)
        sh.paste(shd, x0 - pad / K, y0 - pad / K + dy)
    sh.rect(box, fill=fill, r=radius)


def photo_box(sh: Sheet, it: LookItem, box: tuple[float, float, float, float], *, fill: tuple[int, int, int] | None,
              radius: float = 0, inset: float = 0.10, fy: float = 0.5, empty: tuple[int, int, int] = (236, 233, 228),
              scale: float = 1.0, loss_max: float = 0.0) -> lp.Photo | None:
    """칸에 상품 사진 (판에 앉힘). 사진이 없으면 빈 칸 대신 글자 판 (no_photo) — 꾸민 그림·가짜 사진은 없다."""
    x0, y0, x1, y1 = box
    sz = (S(x1 - x0), S(y1 - y0))
    p = item_photo(it)
    if p is None:
        no_photo(sh, it, box, empty, radius)
        return None
    img = lp.plate(p, sz, fill=fill, inset=inset, fy=fy, scale=scale, loss_max=loss_max)
    if radius:
        sh.paste(rounded(img, radius), x0, y0)
    else:
        sh.paste(img, x0, y0)
    sh.photo(it, box)
    return p


def hanger(sh: Sheet, cx: float, cy: float, w: float, color: tuple[int, int, int]) -> None:
    """옷걸이 선 그림 (사진 없는 작은 칸 — 빈 네모 대신)."""
    h = w * 0.42
    lw = max(1.6, w / 40)
    top = cy - h / 2
    sh.line([(cx - w / 2, cy + h / 2), (cx, top + h * 0.18), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)], color, lw)
    r = w * 0.09
    pts = [(cx + r * math.cos(a / 10 * math.pi), top + h * 0.18 - r - r * math.sin(a / 10 * math.pi)) for a in range(-5, 11)]
    sh.line(pts, color, lw)


def no_photo(sh: Sheet, it: LookItem, box: tuple[float, float, float, float], fill: tuple[int, int, int], radius: float = 0) -> None:
    """사진 없는 상품 칸: 큰 칸은 아이템 이름을 크게 + 판매처 (글자 판), 작은 칸은 옷걸이 선 그림."""
    x0, y0, x1, y1 = box
    sh.rect(box, fill=fill, r=radius)
    bw, bh = x1 - x0, y1 - y0
    ink = (118, 112, 104)
    if bw < 260 or bh < 200:
        hanger(sh, (x0 + x1) / 2, (y0 + y1) / 2, min(bw, bh) * 0.5, ink)
        return
    word = it.piece or it.name
    px, lines = fit_lines(sh, word, "xb", (min(96.0, bh * 0.2), 76, 60, 48, 40), bw * 0.8)
    lh = px * 1.18
    total = lh * len(lines) + (54 if it.mall else 0)
    y = (y0 + y1) / 2 - total / 2
    for ln in lines:
        sh.text((x0 + x1) / 2, y, ln, "xb", px, (60, 56, 52), anchor="mt")
        y += lh
    if it.mall:
        sh.text((x0 + x1) / 2, y + 22, it.mall, "sb", 28, ink, anchor="mt")


def plate_fill(p: lp.Photo | None, field_: tuple[int, int, int]) -> tuple[int, int, int]:
    """칸 바탕색: 오려진 상품은 색판, 아니면 사진 바탕색 (복잡한 사진은 어차피 꽉 채움)."""
    if p is None:
        return field_
    return field_ if p.cut else p.bg


# ================================================================ A 화보형
A_PAPER = (245, 242, 237)
A_INK = (20, 19, 18)
A_SUB = (112, 107, 100)
A_HAIR = (40, 38, 36)


def a_folio(sh: Sheet, left: str, right: str) -> None:
    sh.line([(M, FOLIO_Y), (W - M, FOLIO_Y)], A_HAIR, 1.2)
    px = 24
    rw = sh.w("md", px, right)
    lw_max = W - 2 * M - rw - 40
    if left:
        lpx = fit_px(sh, left, "md", px, lw_max, 20)
        sh.text(M, FOLIO_Y + 40, left, "md", lpx, A_SUB)
    if right:
        sh.text(W - M, FOLIO_Y + 40, right, "md", px, A_SUB, anchor="rs")


def a_tab(sh: Sheet, x: float, y: float, n: int) -> None:
    """사진 왼쪽 위 번호 탭 (흰 네모 + 세리프 숫자)."""
    s = f"{n:02d}"
    w = sh.w("serif", 30, s) + 26
    sh.rect((x, y, x + w, y + 46), fill=(252, 251, 249))
    sh.text(x + w / 2, y + 34, s, "serif", 30, A_INK, anchor="ms")


def a_cover(deck: LookDeck) -> Sheet:
    sh = Sheet(deck, A_PAPER)
    ad_brand(sh, shape="square", ink=A_INK, paper=A_PAPER)
    shown = [it for it in deck.items if item_photo(it) is not None][:4]
    top, bot, g = 168, 822, 18
    x0, x1 = M, W - M
    n = len(shown)
    if n >= 3:
        lw = 548
        rh = (bot - top - g * (n - 2)) / (n - 1)
        boxes = [(x0, top, x0 + lw, bot)]
        for i in range(n - 1):
            yy = top + i * (rh + g)
            boxes.append((x0 + lw + g, yy, x1, yy + rh))
    elif n == 2:
        mid = (x0 + x1) / 2
        boxes = [(x0, top, mid - g / 2, bot), (mid + g / 2, top, x1, bot)]
    elif n == 1:
        boxes = [(x0, top, x1, bot)]
    else:
        boxes = []
    for i, (it, box) in enumerate(zip(shown, boxes, strict=False)):
        p = item_photo(it)
        fill = lp.swatch(p.tone) if p is not None else None
        photo_box(sh, it, box, fill=fill, inset=0.12 if i == 0 else 0.13)
        a_tab(sh, box[0], box[1], deck.items.index(it) + 1)
    if not boxes:  # 사진이 하나도 없으면 글자 표지 (가운데 큰 세리프)
        sh.text(W / 2, 520, GET_THE_LOOK, "serif", 120, A_INK, anchor="ms")
    # 글
    y = 892 if boxes else 640
    sh.text(M, y, GET_THE_LOOK, "serif", 52, A_INK)
    kpx = fit_px(sh, deck.kicker, "sb", 26, W - 2 * M - sh.w("serif", 52, GET_THE_LOOK) - 40, 20)
    sh.text(W - M, y - 4, deck.kicker, "sb", kpx, A_SUB, anchor="rs")
    title = [t for t in deck.title[:2] if t]
    tpx = 86.0
    while tpx > 56 and max((sh.w("xb", tpx, t) for t in title), default=0) > W - 2 * M:
        tpx -= 2
    ty = y + 34
    for t in title:
        sh.text(M, ty, t, "xb", tpx, A_INK, anchor="lt")
        ty += tpx * 1.2
    if deck.sub:
        spx = fit_px(sh, deck.sub, "md", 30, W - 2 * M, 22)
        sh.text(M, ty + 22, deck.sub, "md", spx, A_SUB, anchor="lt")
    a_folio(sh, credit(shown) if shown else "", NEXT)
    return sh


def a_product(deck: LookDeck, n: int, it: LookItem) -> Sheet:
    sh = Sheet(deck, A_PAPER)
    ad_brand(sh, shape="square", ink=A_INK, paper=A_PAPER)
    total = len(deck.items)
    # 머리: 큰 세리프 번호 + 분류
    num = f"{n:02d}"
    sh.text(M, 236, num, "serif", 92, A_INK)
    nx = M + sh.w("serif", 92, num) + 16
    sh.text(nx, 236, f"/ {total:02d}", "serif", 32, A_SUB)
    label = "같은 제품" if it.same else "비슷한 상품"
    lx = W - M
    sh.text(lx, 200, label, "sb", 28, RED, anchor="rs")
    if (pl := piece_line(it)):
        ppx = fit_px(sh, pl, "md", 26, W - 2 * M - 260, 20)
        sh.text(lx, 236, pl, "md", ppx, A_SUB, anchor="rs")
    # 사진
    box = (M, 266, W - M, 900)
    p = item_photo(it)
    photo_box(sh, it, box, fill=lp.swatch(p.tone) if p is not None else None, inset=0.09, fy=0.42, loss_max=2.25)
    # 이름 · 가격 · 판매처
    npx, lines = fit_lines(sh, it.name, "sb", (42, 38, 34), W - 2 * M)
    lh = npx * 1.32
    block = lh * len(lines) + 34 + 64  # 이름 줄 + 틈 + 가격 숫자 높이
    y = box[3] + max(30.0, (FOLIO_Y - box[3] - block) / 2)
    for ln in lines:
        sh.text(M, y, ln, "sb", npx, A_INK, anchor="lt")
        y += lh
    base = y + 34 + 64
    price(sh, M, base, it, "serif", 88, A_INK, unit_px=36)
    if it.mall:
        sh.text(W - M, base - 42, it.mall, "sb", 30, A_INK, anchor="rs")
    if (dl := date_line(deck)) and won(it.price):
        sh.text(W - M, base, dl, "md", 24, A_SUB, anchor="rs")
    a_folio(sh, not_worn(deck.person, it), credit([it]) if p is not None else "")
    return sh


def a_end(deck: LookDeck) -> Sheet:
    sh = Sheet(deck, A_PAPER)
    ad_brand(sh, shape="square", ink=A_INK, paper=A_PAPER)
    sh.text(M, 214, GET_THE_LOOK, "serif", 44, A_INK)
    kpx = fit_px(sh, deck.end_kicker, "xb", 52, W - 2 * M, 34)
    sh.text(M, 236, deck.end_kicker, "xb", kpx, A_INK, anchor="lt")
    rows = deck.items[:4]
    th = 196 if len(rows) <= 3 else 150
    step = th + 36
    y = 338
    for i, it in enumerate(rows, 1):
        sh.line([(M, y - 17), (W - M, y - 17)], (210, 205, 198), 1)
        p = item_photo(it)
        photo_box(sh, it, (M, y, M + th, y + th), fill=lp.swatch(p.tone) if p is not None else None, inset=0.08)
        tx = M + th + 30
        sh.text(tx, y + 36, f"{i:02d}", "serif", 32, A_SUB)
        pw = sh.w("serif", 48, split_price(won(it.price))[0]) + sh.w("sb", 22, "원") + 8
        npx, lines = fit_lines(sh, it.name, "sb", (32, 30, 28, 26, 24), W - M - tx - pw - 30, extra=False)
        ly = y + 58
        for ln in lines:
            sh.text(tx, ly, ln, "sb", npx, A_INK, anchor="lt")
            ly += npx * 1.3
        tag = " · ".join(x for x in (it.mall, date_line(deck) if won(it.price) else "") if x)
        sh.text(tx, max(ly + 34, y + th - 14), tag, "md", 24, A_SUB)
        price(sh, W - M, y + 84, it, "serif", 48, A_INK, unit_px=22, right=True)
        y += step
    sh.line([(M, y - 17), (W - M, y - 17)], (210, 205, 198), 1)
    # 프로필 링크 안내 (검은 띠)
    cy = max(y + 20, FOLIO_Y - 60 - 116)
    sh.rect((M, cy, W - M, cy + 116), fill=A_INK)
    epx = fit_px(sh, deck.end_line, "sb", 38, W - 2 * M - 120, 28)
    sh.text(M + 40, cy + 58, deck.end_line, "sb", epx, A_PAPER, anchor="lm")
    sh.text(W - M - 40, cy + 56, "→", "sb", 44, A_PAPER, anchor="rm")
    a_folio(sh, deck.price_note, "")
    return sh


# ================================================================ B 코디 보드형
B_INK = (22, 22, 24)
B_SUB = (98, 98, 104)
B_CARD = (255, 255, 255)


def b_field(deck: LookDeck) -> tuple[int, int, int]:
    """보드 색판 — 첫 상품 색에 어울리는 연한 색."""
    return lp.swatch(deck_tone(deck))


def b_badge(sh: Sheet, cx: float, cy: float, n: int, d: float = 50, *, fg: tuple[int, int, int] = (255, 255, 255),
            bg: tuple[int, int, int] = B_INK) -> None:
    sh.ellipse((cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2), fill=bg)
    sh.text(cx, cy + 1, f"{n:02d}" if d >= 56 else str(n), "sb", d * 0.40, fg, anchor="mm")


def b_board(sh: Sheet, deck: LookDeck, box: tuple[float, float, float, float], field_: tuple[int, int, int]) -> list[tuple[int, LookItem, tuple[float, float, float, float]]]:
    """한 벌 세우기 — 모자→상의→하의→신발 순으로 위에서 아래로. [(번호, 상품, 칸)]."""
    x0, y0, x1, y1 = box
    order = list(enumerate(deck.items, 1))[:4]  # 덱은 arrange 로 이미 한 벌 순서
    weight = {0: 0.62, 1: 1.0, 2: 0.95, 3: 1.0, 4: 0.6, 5: 0.7, 6: 0.85}
    ws = [weight[part_of(it)] for _, it in order]
    g = 16
    avail = (y1 - y0) - g * (len(order) - 1)
    out = []
    y = y0
    for (n, it), wv in zip(order, ws, strict=True):
        h = avail * wv / sum(ws)
        # 작은 것(모자·신발)은 칸도 좁게
        p = item_photo(it)
        cw = (x1 - x0) * (0.72 if wv < 0.7 else 1.0)
        if p is not None and not p.clean:  # 연출 사진은 사진 비율대로 칸을 좁혀 덜 잘리게
            cw = max((x1 - x0) * 0.5, min(cw, h * p.img.width / p.img.height * 1.15))
        cx0 = x1 - cw  # 오른쪽 끝을 맞춤 — 설명줄이 같은 x 에서 시작
        cb = (cx0, y, cx0 + cw, y + h)
        card(sh, cb, plate_fill(p, B_CARD), 26, shadow=22, blur=16, dy=8)
        photo_box(sh, it, cb, fill=B_CARD, radius=26, inset=0.10, fy=0.45, loss_max=2.7)
        out.append((n, it, cb))
        y += h + g
    return out


def b_cover(deck: LookDeck) -> Sheet:
    field_ = b_field(deck)
    sh = Sheet(deck, field_)
    ad_brand(sh, shape="pill", ink=B_INK)
    kpx = fit_px(sh, deck.kicker, "sb", 28, W - 2 * M, 22)
    sh.text(M, 214, deck.kicker, "sb", kpx, B_SUB)
    title = [t for t in deck.title[:2] if t]
    tpx = 80.0
    while tpx > 54 and max((sh.w("xb", tpx, t) for t in title), default=0) > W - 2 * M:
        tpx -= 2
    ty = 236
    for t in title:
        sh.text(M, ty, t, "xb", tpx, B_INK, anchor="lt")
        ty += tpx * 1.18
    if deck.sub:
        spx = fit_px(sh, deck.sub, "md", 28, W - 2 * M, 22)
        sh.text(M, ty + 14, deck.sub, "md", spx, B_SUB, anchor="lt")
    board_top = ty + 74
    placed = b_board(sh, deck, (M, board_top, 584, 1214), field_)
    # 오른쪽 설명줄 (번호 · 아이템 · 판매처 · 가격) — 줄 덩어리를 사진 칸 가운데에
    lx = 646
    prev_bottom = 0.0
    for n, it, cb in placed:
        cy = (cb[1] + cb[3]) / 2
        b_badge(sh, cb[0] + 34, cb[1] + 34, n, 40)
        name = it.piece or it.name
        compact = len(placed) >= 4  # 넷이면 판매처 줄 없이 (판매처는 상품 장·끝 장에)
        npx = fit_px(sh, name, "sb", 30 if compact else 34, W - M - lx, 24)
        nl = wrap(sh, name, "sb", npx, W - M - lx)[:2]
        has_price = bool(won(it.price))
        show_mall = bool(it.mall) and not compact
        block = npx * 1.25 * len(nl) + (32 if show_mall else 0) + (46 if has_price else 0)
        cy = max(cy, prev_bottom + 18 + block / 2)  # 앞 설명줄과 겹치지 않게
        prev_bottom = cy + block / 2
        yy = cy - block / 2 + npx * 0.9
        ly = yy - npx * 0.36  # 설명줄은 이름 첫 줄 가운데로
        sh.line([(cb[2] + 8, ly), (lx - 16, ly)], B_INK, 1.2)
        sh.ellipse((lx - 20, ly - 4, lx - 12, ly + 4), fill=B_INK)
        for ln in nl:
            sh.text(lx, yy, ln, "sb", npx, B_INK)
            yy += npx * 1.25
        if show_mall:
            sh.text(lx, yy + 2, it.mall, "md", 24, B_SUB)
            yy += 32
        if has_price:
            price(sh, lx, yy + 38, it, "xb", 36, B_INK, unit_px=24)
    sh.text(M, SAFE_BOTTOM - 8, credit([it for _, it, _ in placed if item_photo(it)]), "md", 22, B_SUB)
    sh.text(W - M, SAFE_BOTTOM - 8, NEXT, "sb", 26, B_INK, anchor="rs")
    return sh


def b_product(deck: LookDeck, n: int, it: LookItem) -> Sheet:
    field_ = b_field(deck)
    sh = Sheet(deck, field_)
    ad_brand(sh, shape="pill", ink=B_INK)
    p = item_photo(it)
    box = (M, 164, W - M, 870)
    card(sh, box, plate_fill(p, B_CARD), 34, shadow=28, blur=22, dy=12)
    photo_box(sh, it, box, fill=B_CARD, radius=34, inset=0.09)
    b_badge(sh, box[0] + 56, box[1] + 56, n, 64)
    label = "같은 제품" if it.same else "비슷한 상품"
    lw = sh.w("sb", 24, label) + 36
    sh.rect((box[2] - 28 - lw, box[1] + 30, box[2] - 28, box[1] + 82), fill=(255, 255, 255), outline=B_INK, width=1.5, r=26)
    sh.text(box[2] - 28 - lw / 2, box[1] + 57, label, "sb", 24, B_INK, anchor="mm")
    # 가격표 카드
    tb = (M, 898, W - M, 1206)
    card(sh, tb, B_CARD, 28, shadow=18, blur=14, dy=6)
    ix = M + 40
    y = 948
    if (pl := piece_line(it)):
        sh.text(ix, y, pl, "md", 24, B_SUB)
    npx, lines = fit_lines(sh, it.name, "sb", (38, 34, 30), W - 2 * M - 80)
    y += 22
    for ln in lines:
        sh.text(ix, y, ln, "sb", npx, B_INK, anchor="lt")
        y += npx * 1.3
    base = 1166
    sh.line([(ix, base - 84), (W - M - 40, base - 84)], (226, 226, 228), 1.2)
    price(sh, ix, base, it, "xb", 58, B_INK, unit_px=30)
    rx = W - M - 40
    if (dl := date_line(deck)) and won(it.price):
        sh.text(rx, base - 4, dl, "md", 24, B_SUB, anchor="rs")
        rx -= sh.w("md", 24, dl) + 18
    if it.mall:
        mw = sh.w("sb", 24, it.mall) + 32
        sh.rect((rx - mw, base - 40, rx, base + 4), fill=field_, r=22)
        sh.text(rx - mw / 2, base - 17, it.mall, "sb", 24, B_INK, anchor="mm")
    # 바닥: 착용 제품 아님 · 진행 점
    if (nw := not_worn(deck.person, it)):
        sh.text(M, SAFE_BOTTOM - 8, nw, "md", 24, B_SUB)
    dots = len(deck.items)
    x = W - M
    for i in range(dots, 0, -1):
        s = f"{i:02d}"
        sh.text(x, SAFE_BOTTOM - 8, s, "sb" if i == n else "jost", 24, B_INK if i == n else (150, 150, 156), anchor="rs")
        x -= sh.w("jost", 24, s) + 16
    return sh


def b_end(deck: LookDeck) -> Sheet:
    field_ = b_field(deck)
    sh = Sheet(deck, field_)
    ad_brand(sh, shape="pill", ink=B_INK)
    kpx = fit_px(sh, deck.end_kicker, "xb", 54, W - 2 * M, 34)
    sh.text(M, 168, deck.end_kicker, "xb", kpx, B_INK, anchor="lt")
    rows = deck.items[:4]
    rh = 196 if len(rows) <= 3 else 150
    y = 272
    for i, it in enumerate(rows, 1):
        cb = (M, y, W - M, y + rh)
        card(sh, cb, B_CARD, 24, shadow=14, blur=12, dy=5)
        t = rh - 32
        p = item_photo(it)
        tb = (M + 16, y + 16, M + 16 + t, y + 16 + t)
        sh.rect(tb, fill=plate_fill(p, (244, 243, 240)), r=18)
        photo_box(sh, it, tb, fill=(244, 243, 240), radius=18, inset=0.08)
        b_badge(sh, tb[0] + 22, tb[1] + 22, i, 32)
        tx = tb[2] + 28
        pw = sh.w("xb", 40, split_price(won(it.price))[0]) + sh.w("sb", 24, "원") + 6
        npx, lines = fit_lines(sh, it.name, "sb", (30, 28, 26, 24), W - M - 28 - tx - pw - 24, extra=False)
        group = npx * 1.3 * len(lines) + 14 + 26  # 이름 줄 + 틈 + 판매처 줄 — 칸 가운데로
        ly = y + (rh - group) / 2
        for ln in lines:
            sh.text(tx, ly, ln, "sb", npx, B_INK, anchor="lt")
            ly += npx * 1.3
        tag = " · ".join(x for x in (it.mall, date_line(deck) if won(it.price) else "") if x)
        sh.text(tx, ly + 14 + 22, tag, "md", 24, B_SUB)
        price(sh, W - M - 28, y + rh / 2 + 14, it, "xb", 40, B_INK, unit_px=24, right=True)
        y += rh + 20
    priced = [it for it in rows if won(it.price)]
    if len(priced) == len(rows) and len(rows) > 1:
        y += 18
        sh.text(M + 28, y + 30, TOTAL, "sb", 30, B_INK)
        total = LookItem(name="", price=sum(it.price for it in rows))
        price(sh, W - M - 28, y + 34, total, "xb", 46, B_INK, unit_px=26, right=True)
        y += 70
    cy = max(y + 26, SAFE_BOTTOM - 100 - 90)
    sh.rect((M, cy, W - M, cy + 100), fill=B_INK, r=50)
    epx = fit_px(sh, deck.end_line + " →", "sb", 36, W - 2 * M - 80, 26)
    sh.text(W / 2, cy + 50, deck.end_line + " →", "sb", epx, (255, 255, 255), anchor="mm")
    if deck.price_note:
        sh.text(W / 2, cy + 152, deck.price_note, "md", 24, B_SUB, anchor="ms")
    return sh


# ================================================================ C 무드보드형
C_PAPER = (240, 235, 226)
C_INK = (36, 32, 29)
C_SUB = (110, 102, 92)
C_PRINT = (252, 250, 246)
TAPES = ((232, 218, 190), (214, 224, 212), (236, 212, 204), (220, 226, 236))


def c_paper(deck: LookDeck) -> Sheet:
    sh = Sheet(deck, C_PAPER)
    from dealbot.media.card_paper import paper

    sh.im = paper((W * K, H * K), C_PAPER, 0.22)
    # 오른쪽 위 장 번호 자리는 결 없이 고르게 (앱 숫자 뒤가 지저분해 보이지 않게) — 둘레 평균색으로, 가장자리는 서서히
    cw, ch = S(CORNER[0] + 90), S(CORNER[1] + 90)
    region = sh.im.crop((S(W) - cw, 0, S(W), ch))
    flat = Image.new("RGB", region.size, tuple(int(v) for v in ImageStat.Stat(region).mean))
    m = Image.new("L", region.size, 0)
    ImageDraw.Draw(m).rectangle((S(90) - S(20), 0, region.size[0], region.size[1] - S(90) + S(20)), fill=255)
    m = m.filter(ImageFilter.GaussianBlur(S(18)))
    ImageDraw.Draw(m).rectangle((S(90), 0, region.size[0], S(CORNER[1])), fill=255)
    sh.im.paste(Image.composite(flat, region, m), (S(W) - cw, 0))
    sh.d = ImageDraw.Draw(sh.im)
    return sh


def c_tape(length: float, rng: random.Random, color: tuple[int, int, int]) -> Image.Image:
    from dealbot.media.card_paper import make_tape

    t = make_tape(length, color, rng)
    a = t.getchannel("A").point(lambda v: int(v * 0.86))
    t.putalpha(a)
    return t


def c_print(sh: Sheet, it: LookItem, cx: float, cy: float, w: float, h: float, angle: float, rng: random.Random, *,
            border: float = 18, tape: bool = True, bottom: float | None = None) -> tuple[float, float, float, float]:
    """인화 사진 한 장 (흰 테두리 · 그림자 · 테이프). 돌린 뒤의 칸 (1배)."""
    bb = bottom if bottom is not None else border
    fw, fh = S(w), S(h)
    layer = Image.new("RGBA", (fw, fh), C_PRINT + (255,))
    inner = (S(w - 2 * border), S(h - border - bb))
    p = item_photo(it)
    if p is not None:
        img = lp.plate(p, inner, fill=None, inset=0.07)
        layer.paste(img, (S(border), S(border)))
        sh.photo(it, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    else:
        ImageDraw.Draw(layer).rectangle((S(border), S(border), S(w - border), S(h - bb)), fill=(236, 231, 222))
    pad = S(40)
    big = Image.new("RGBA", (fw + 2 * pad, fh + 2 * pad), (0, 0, 0, 0))
    sm = Image.new("L", big.size, 0)
    ImageDraw.Draw(sm).rectangle((pad + S(4), pad + S(10), pad + fw + S(2), pad + fh + S(8)), fill=70)
    sm = sm.filter(ImageFilter.GaussianBlur(S(12)))
    shadow = Image.new("RGBA", big.size, (50, 40, 30, 0))
    shadow.putalpha(sm)
    big.alpha_composite(shadow)
    big.alpha_composite(layer, (pad, pad))
    r = big.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    sh.paste(r, cx - r.width / K / 2, cy - r.height / K / 2)
    if tape:
        tl = min(220.0, w * 0.42)
        t = c_tape(tl, rng, rng.choice(TAPES))
        ta = angle + rng.uniform(-6, 6)
        rad = math.radians(angle)
        tx, ty = cx + (h / 2) * math.sin(rad), cy - (h / 2) * math.cos(rad)
        tr = t.rotate(ta, resample=Image.Resampling.BICUBIC, expand=True)
        sh.paste(tr, tx - tr.width / K / 2, ty - tr.height / K / 2)
    if p is None:  # 사진 없는 인화지: 아이템 이름을 손글씨로 (빈 칸 대신)
        word = it.piece or it.name
        hpx = fit_px(sh, word, "hand", min(64.0, w * 0.13), w * 0.72, 30)
        c_hand(sh, cx, cy, word, hpx, C_SUB, anchor="mm")
        if it.mall:
            sh.text(cx, cy + hpx * 0.6 + 26, it.mall, "sb", 24, C_SUB, anchor="mt")
    rw, rh = r.width / K - 2 * pad / K, r.height / K - 2 * pad / K
    return (cx - rw / 2, cy - rh / 2, cx + rw / 2, cy + rh / 2)


def c_circle(sh: Sheet, cx: float, cy: float, rx: float, ry: float, rng: random.Random, color: tuple[int, int, int] = PEN) -> None:
    """빨간 펜 동그라미 (한 바퀴 조금 넘게, 손으로 그린 듯)."""
    pts = []
    turns = 1.12
    a0 = rng.uniform(0, math.tau)
    for i in range(80):
        t = i / 79
        a = a0 + t * turns * math.tau
        wob = 1 + 0.04 * math.sin(t * 9 + a0)
        pts.append((cx + rx * wob * math.cos(a), cy + ry * wob * math.sin(a)))
    sh.line(pts, color, 3.2)


def c_number(sh: Sheet, cx: float, cy: float, n: int, rng: random.Random, r: float) -> None:
    """빨간 펜 동그라미 안 번호 (숫자는 Bodoni — 손글씨 '01' 은 '이' 처럼 읽혀서)."""
    sh.ellipse((cx - r + 2, cy - r + 4, cx + r + 2, cy + r + 4), fill=C_PRINT)
    c_circle(sh, cx, cy, r * 1.06, r * 0.96, rng)
    sh.text(cx, cy + 2, f"{n:02d}", "bodoni", r * 1.05, PEN, anchor="mm")


def c_hand(sh: Sheet, x: float, y: float, s: str, px: float, fill: object, anchor: str = "ls") -> tuple[float, float, float, float]:
    """손글씨 (감자꽃) — 글꼴에 없는 글자가 있으면 Pretendard."""
    key = "hand" if covers(sh.deck, "hand", s) else "md"
    return sh.text(x, y, s, key, px if key == "hand" else px * 0.8, fill, anchor=anchor)


def c_underline(sh: Sheet, x0: float, x1: float, y: float, rng: random.Random) -> None:
    pts = []
    for i in range(30):
        t = i / 29
        pts.append((x0 + (x1 - x0) * t, y + math.sin(t * 3.1 + rng.uniform(0, 0.4)) * 2.5 + t * 2))
    sh.line(pts, PEN, 3.0)


def c_cover(deck: LookDeck) -> Sheet:
    sh = c_paper(deck)
    ad_brand(sh, shape="soft", ink=C_INK, paper=C_PAPER)
    rng = seed(deck, "C-cover")
    shown = [it for it in deck.items if item_photo(it) is not None][:3]
    spots = [  # (가운데 x, 가운데 y, 너비, 높이, 기울기)
        (318, 548, 452, 600, -3.2),
        (792, 392, 336, 392, 3.6),
        (770, 772, 318, 318, -2.4),
    ]
    if len(shown) == 2:
        spots = [(312, 540, 440, 580, -3.0), (780, 560, 380, 470, 3.2)]
    elif len(shown) == 1:
        spots = [(540, 540, 560, 680, -2.0)]
    for it, sp in zip(shown, spots, strict=False):
        box = c_print(sh, it, *sp, rng)
        n = deck.items.index(it) + 1
        # 번호: 사진 왼쪽 위 바깥에 손글씨 + 빨간 동그라미
        c_number(sh, box[0] + 34, box[1] + 26, n, rng, 30)
    # 메모 종이 (제목)
    nb = (M, 924, 772, 1236)
    shd, pad = soft_shadow((S(nb[2] - nb[0]), S(nb[3] - nb[1])), 2, blur=10, alpha=60)
    sh.paste(shd, nb[0] - pad / K + 3, nb[1] - pad / K + 8)
    sh.rect(nb, fill=C_PRINT)
    t = c_tape(170, rng, TAPES[0])
    tr = t.rotate(-4, resample=Image.Resampling.BICUBIC, expand=True)
    sh.paste(tr, nb[0] + 30, nb[1] - tr.height / K / 2)
    ix = nb[0] + 40
    kpx = 38
    c_hand(sh, ix, nb[1] + 72, deck.kicker, kpx, C_SUB)
    title = [t for t in deck.title[:2] if t]
    tpx = 66.0
    while tpx > 46 and max((sh.w("xb", tpx, s) for s in title), default=0) > nb[2] - nb[0] - 80:
        tpx -= 2
    ty = nb[1] + 98
    for s in title:
        sh.text(ix, ty, s, "xb", tpx, C_INK, anchor="lt")
        ty += tpx * 1.2
    if deck.sub:
        spx = fit_px(sh, deck.sub, "md", 26, nb[2] - nb[0] - 80, 20)
        sh.text(ix, ty + 6, deck.sub, "md", spx, C_SUB, anchor="lt")
    c_hand(sh, W - M, 1196, NEXT, 40, PEN, anchor="rs")
    if shown:
        sh.text(W - M, SAFE_BOTTOM - 6, credit(shown), "md", 22, C_SUB, anchor="rs")
    return sh


def c_tag(deck: LookDeck, it: LookItem, w: float, h: float) -> tuple[Image.Image, list[Drawn]]:
    """크라프트 가격표 (RGBA 2배) — 구멍·끈 · Bodoni 가격 · 판매처 · 기준 날짜. 그린 글도 돌려줌 (태그 좌표)."""
    sub = Sheet(deck, (0, 0, 0))
    sub.im = Image.new("RGBA", (S(w), S(h)), (0, 0, 0, 0))
    sub.d = ImageDraw.Draw(sub.im)
    kraft = (226, 205, 166)
    notch = 40
    poly = [(notch, 0), (w, 0), (w, h), (notch, h), (0, h / 2)]
    sub.d.polygon([(S(a), S(b)) for a, b in poly], fill=kraft + (255,))
    sub.ellipse((notch - 14, h / 2 - 9, notch + 4, h / 2 + 9), fill=(240, 235, 226))
    sub.ellipse((notch - 14, h / 2 - 9, notch + 4, h / 2 + 9), outline=(150, 128, 92), width=1.4)
    x = notch + 30
    if it.mall:
        sub.text(x, 58, it.mall, "sb", 26, C_INK)
    p = won(it.price)
    if p:
        num = split_price(p)[0]
        ppx = fit_px(sub, num, "bodoni", 66, w - x - 30 - sub.w("sb", 30, "원") - 6, 44)
        price(sub, x, 140, it, "bodoni", ppx, C_INK, unit_px=30)
        if (dl := date_line(deck)):
            sub.text(x, 184, dl, "md", 22, (96, 80, 56))
    return sub.im, sub.drawn


def c_product(deck: LookDeck, n: int, it: LookItem) -> Sheet:
    sh = c_paper(deck)
    ad_brand(sh, shape="soft", ink=C_INK, paper=C_PAPER)
    rng = seed(deck, "C", n)
    box = c_print(sh, it, 512, 548, 760, 760, -1.6, rng, border=22, bottom=22)
    # 번호 (빨간 동그라미 손글씨)
    c_number(sh, box[0] + 42, box[1] + 34, n, rng, 38)
    # 가격표 (사진 오른쪽 아래에 걸침, 살짝 기울임)
    tw_, th_ = 330.0, 214.0
    tag, drawn = c_tag(deck, it, tw_, th_)
    ang = 5.0
    pad = S(30)
    big = Image.new("RGBA", (tag.width + 2 * pad, tag.height + 2 * pad), (0, 0, 0, 0))
    sm = Image.new("L", big.size, 0)
    sm.paste(tag.getchannel("A").point(lambda v: int(v * 0.30)), (pad + S(4), pad + S(9)))
    sm = sm.filter(ImageFilter.GaussianBlur(S(9)))
    shadow = Image.new("RGBA", big.size, (40, 30, 20, 0))
    shadow.putalpha(sm)
    big.alpha_composite(shadow)
    big.alpha_composite(tag, (pad, pad))
    r = big.rotate(ang, resample=Image.Resampling.BICUBIC, expand=True)
    tcx, tcy = W - M - tw_ / 2 - 14, 904
    sh.paste(r, tcx - r.width / K / 2, tcy - r.height / K / 2)
    # 끈
    rad = math.radians(ang)
    hole = (tcx + (-tw_ / 2 + 35) * math.cos(rad), tcy - (-tw_ / 2 + 35) * math.sin(rad))
    sh.line([hole, (hole[0] - 26, hole[1] - 40), (hole[0] - 34, hole[1] - 74)], (96, 78, 52), 2.2)
    ca, sa = math.cos(rad), math.sin(rad)
    for dr in drawn:  # 태그 글 칸을 돌린 자리로 옮겨 적어 둠 (안전 구역 검사용)
        x0, y0, x1, y1 = dr.box
        pts = [(x - tw_ / 2, y - th_ / 2) for x in (x0, x1) for y in (y0, y1)]
        xs = [tcx + dx * ca + dy * sa for dx, dy in pts]
        ys = [tcy - dx * sa + dy * ca for dx, dy in pts]
        sh.drawn.append(Drawn(dr.text, (min(xs), min(ys), max(xs), max(ys))))
    # 글
    y = 1046
    if (pl := piece_line(it)):
        c_hand(sh, M, y, pl, 36, C_SUB)
    npx, lines = fit_lines(sh, it.name, "sb", (40, 36, 32), 600)
    y += 22
    for ln in lines:
        sh.text(M, y, ln, "sb", npx, C_INK, anchor="lt")
        y += npx * 1.3
    label = "같은 제품" if it.same else "비슷한 상품"
    nw = not_worn(deck.person, it)
    sh.text(M, SAFE_BOTTOM - 6, " · ".join(x for x in (label, nw) if x), "md", 24, C_SUB)
    if item_photo(it) is not None:
        sh.text(W - M, SAFE_BOTTOM - 6, credit([it]), "md", 22, C_SUB, anchor="rs")
    return sh


def c_end(deck: LookDeck) -> Sheet:
    sh = c_paper(deck)
    ad_brand(sh, shape="soft", ink=C_INK, paper=C_PAPER)
    rng = seed(deck, "C-end")
    rows = deck.items[:4]
    # 줄 노트 한 장
    th = 160 if len(rows) <= 3 else 124
    step = th + 56
    nb = (M, 168, W - M, 168 + 150 + step * len(rows) + 4)
    shd, pad = soft_shadow((S(nb[2] - nb[0]), S(nb[3] - nb[1])), 2, blur=12, alpha=56)
    sh.paste(shd, nb[0] - pad / K + 3, nb[1] - pad / K + 9)
    sh.rect(nb, fill=(252, 251, 247))
    for j in range(len(rows) + 1):  # 상품 사이 가는 줄
        yy = nb[1] + 150 - 28 + j * step
        sh.line([(nb[0] + 70, yy), (nb[2] - 24, yy)], (222, 228, 238), 1.2)
    sh.line([(nb[0] + 70, nb[1]), (nb[0] + 70, nb[3])], (236, 176, 176), 1.2)
    t = c_tape(200, rng, TAPES[1])
    tr = t.rotate(2.5, resample=Image.Resampling.BICUBIC, expand=True)
    sh.paste(tr, W / 2 - tr.width / K / 2, nb[1] - tr.height / K / 2)
    kx = nb[0] + 96
    kb = c_hand(sh, kx, nb[1] + 92, deck.end_kicker, 44, C_INK)
    c_underline(sh, kx, min(kb[2], nb[2] - 40), nb[1] + 106, rng)
    y = nb[1] + 150
    for i, it in enumerate(rows, 1):
        tb = (kx, y, kx + th, y + th)
        sh.rect((tb[0] - 8, tb[1] - 8, tb[2] + 8, tb[3] + 8), fill=C_PRINT, outline=(226, 220, 210), width=1)
        photo_box(sh, it, tb, fill=None, inset=0.06)
        tx = tb[2] + 32
        sh.text(tx, y + 34, f"{i:02d}", "bodoni", 32, PEN)
        pw = sh.w("bodoni", 50, split_price(won(it.price))[0]) + sh.w("sb", 24, "원") + 8
        npx, lines = fit_lines(sh, it.name, "sb", (30, 28, 26, 24), nb[2] - 40 - tx - pw - 24)  # 좁은 칸 — 세 줄까지
        ly = y + 52 - (12 if len(lines) > 2 else 0)
        for ln in lines:
            sh.text(tx, ly, ln, "sb", npx, C_INK, anchor="lt")
            ly += npx * 1.3
        tag = " · ".join(x for x in (it.mall, date_line(deck) if won(it.price) else "") if x)
        sh.text(tx, max(ly + 30, y + th - 4), tag, "md", 24, C_SUB)
        price(sh, nb[2] - 40, y + 78, it, "bodoni", 50, C_INK, unit_px=24, right=True)
        y += step
    # 프로필 링크 (빨간 펜)
    cy = nb[3] + (SAFE_BOTTOM - 60 - nb[3]) / 2 + 22
    hb = c_hand(sh, M + 6, cy, deck.end_line + " →", 50, PEN)
    c_underline(sh, M + 4, hb[2] + 4, cy + 16, rng)
    if deck.price_note:
        sh.text(M, SAFE_BOTTOM - 6, deck.price_note, "md", 24, C_SUB)
    return sh


# ================================================================ 내보내기
RENDER = {
    "A": (a_cover, a_product, a_end),
    "B": (b_cover, b_product, b_end),
    "C": (c_cover, c_product, c_end),
}


def arrange(deck: LookDeck, style: str) -> LookDeck:
    """B 코디 보드형은 한 벌 순서(모자→아우터→상의→하의→신발→가방)로 번호를 매김 — 표지 보드·상품 장·끝 장이 같은 번호."""
    if style != "B":
        return deck
    items = [it for _, it in sorted(enumerate(deck.items), key=lambda t: (part_of(t[1]), t[0]))]
    return dataclasses.replace(deck, items=items)


def sheets(deck: LookDeck, style: str) -> list[Sheet]:
    """표지 · 상품 장 N · 끝 장 (그린 글·사진 기록 포함 — 시험용)."""
    cov, prod, end = RENDER[style]
    deck = arrange(deck, style)
    out = [cov(deck)]
    out += [prod(deck, i, it) for i, it in enumerate(deck.items, 1)]
    out.append(end(deck))
    return out


def render(deck: LookDeck, style: str) -> list[Image.Image]:
    return [s.final() for s in sheets(deck, style)]


def to_jpeg(im: Image.Image) -> bytes:
    return _jpeg(im)


def allowed_text(deck: LookDeck, style: str, s: str) -> bool:
    """그려도 되는 글: 덱의 글 · 이 갈래의 고정 글 · 숫자/가격 조각 · 화살표."""
    s = s.strip()
    if s in ("→", "원") or NUMERIC.match(s):
        return True
    allowed = deck.texts(style)
    if s in allowed:
        return True
    if s.startswith("사진: ") and s.endswith(" 상품 이미지"):  # 출처 줄 (몰 순서는 장마다 다름)
        malls = {it.mall for it in deck.items}
        return all(x in malls for x in s[4:-7].replace(" 외", "").split("·"))
    part = s.rstrip("…").strip()
    return bool(part) and any(part in t for t in allowed)  # 줄 바꿈한 조각

