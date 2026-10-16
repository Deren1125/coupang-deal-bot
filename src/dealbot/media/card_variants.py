"""인스타그램 카드 양식 A/B/C (config instagram.style — 주인 선택 10/12: B).

A 사진 크게 + 큰 가격: 위 60% 상품 사진, 아래 이름·큰 가격·비교 가격.
B 가격 정보 카드 (10/15 바뀜): 우리 데이터가 주인공 — 위 고지 띠, 이름 옆에 작은 판매처 사진(높이 22%), 표 (가격 크게 → 1개당 →
  비교 → 기록 → 배송 …). 캐러셀 딜 장과 같은 그림 (media/card_slides.deal_spec). 사진이 없으면 같은 카드에서 사진 칸만 빠진다.
C 사진 꽉 채움 + 가격 스티커: 사진을 크게, 모서리에 기울인 가격 스티커, 아래 이름 한 줄.
A·C 는 사진이 없으면(instagram.no_photo: text_card) 같은 글자 카드. 로고·기본 이미지는 애초에 photo 로 오지 않는다.
카드 안 고지는 '광고 · …' 한 줄 (공정위 지침: 사진 게시물은 사진 안에도).

모든 숫자는 CardInfo 에 온 데이터만 쓴다. 근거 없는 말('역대급' 등)은 카드에 안 쓴다.
프로필 격자는 3:4 로 잘려 보이므로(좌우 약 34px) 글자는 양옆 56px 안쪽에만 둔다.
"""

from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

if TYPE_CHECKING:
    from dealbot.media.card import CardInfo, DealCard

log = logging.getLogger(__name__)

W, H = 1080, 1350
SIDE = 72  # 글자 양옆 여백 (격자 3:4 잘림보다 넉넉히)
INK = "#141414"
MUTED = "#6E6E6E"
LINE = "#E6E6E6"
ACCENT = "#E8442B"
PANEL = "#F4F4F2"
BRAND = "오늘의 핫딜"

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


# ---------------------------------------------------------------- 보조
def _w(draw: ImageDraw.ImageDraw, text: str, font: Font) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return int(right - left)


def _size(font: Font) -> int:
    return int(getattr(font, "size", 40))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Font, max_w: int, max_lines: int) -> list[str]:
    """띄어쓰기에서 먼저 끊고, 한 낱말이 너무 길면 글자 단위로. 넘치면 마지막 줄 끝에 …"""
    words = text.strip().split()
    lines: list[str] = []
    cur = ""
    overflow = False
    i = 0
    while i < len(words):
        word = words[i]
        trial = f"{cur} {word}" if cur else word
        if _w(draw, trial, font) <= max_w:
            cur = trial
            i += 1
            continue
        if not cur:  # 낱말 하나가 한 줄보다 김 → 글자 단위
            part = ""
            for ch in word:
                if _w(draw, part + ch, font) > max_w:
                    break
                part += ch
            part = part or word[:1]
            lines.append(part)
            words[i] = word[len(part):]
        else:
            lines.append(cur)
            cur = ""
        if len(lines) == max_lines:
            overflow = True
            break
    if not overflow and cur:
        lines.append(cur)
    elif overflow and (cur or i < len(words)):
        last = lines[-1]
        while last and _w(draw, last + "…", font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines[:max_lines]


def _split_basis(text: str) -> list[str]:
    """'우리가 본 최근 30일 중 가장 낮음 (10/15 14시 기준)' → 두 줄 (기준 시각은 잘리지 않게 따로)."""
    head, sep, tail = text.partition(" (")
    return [head, f"({tail}"] if sep else [text]


def _lines(draw: ImageDraw.ImageDraw, lines: list[str], font: Font, fill: str, x: int, y: int, *, gap: float = 1.32) -> int:
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += int(_size(font) * gap)
    return y


def _fit(draw: ImageDraw.ImageDraw, text: str, make: object, size: int, max_w: int, min_size: int = 40) -> Font:
    """text 가 max_w 안에 들어가는 가장 큰 글꼴 (긴 가격 '1,299,000원' 대비)."""
    f = make(size)  # type: ignore[operator]
    while size > min_size and _w(draw, text, f) > max_w:
        size -= 4
        f = make(size)  # type: ignore[operator]
    return f


def _chip(draw: ImageDraw.ImageDraw, text: str, font: Font, x: int, y: int, *, fill: str, ink: str,
          outline: str | None = None, right: bool = False) -> tuple[int, int]:
    """둥근 칩. right=True 면 x 를 오른쪽 끝으로. (너비, 높이) 반환."""
    pad_x, pad_y = 20, 10
    tw = _w(draw, text, font)
    h = int(_size(font) * 1.25) + 2 * pad_y
    w = tw + 2 * pad_x
    if right:
        x -= w
    draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=fill, outline=outline, width=3 if outline else 0)
    draw.text((x + pad_x, y + pad_y - 1), text, font=font, fill=ink)
    return w, h


def _strike(draw: ImageDraw.ImageDraw, text: str, font: Font, x: int, y: int, fill: str) -> int:
    draw.text((x, y), text, font=font, fill=fill)
    tw = _w(draw, text, font)
    mid = y + int(_size(font) * 0.62)
    draw.line((x, mid, x + tw, mid), fill=fill, width=max(2, _size(font) // 14))
    return tw


def _ref(draw: ImageDraw.ImageDraw, text: str, font: Font, x: int, y: int, fill: str) -> int:
    """비교 가격 '쿠팡 최저가 22,900원보다' 를 취소선 없이. 다른 판매처 최저가·표시 정가·평소 가격대는 이 상품의 종전 가격이 아니라
    취소선을 긋지 않는다 (표시광고법 — 취소선 '전 → 지금' 은 우리 기록의 바로 앞 가격에만, card_slides 릴스 첫 장). 그린 너비 반환."""
    draw.text((x, y), text, font=font, fill=fill)
    return _w(draw, text, font)


def _open_photo(photo: bytes | None) -> Image.Image | None:
    if not photo:
        return None
    try:
        img = Image.open(io.BytesIO(photo))
        return ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:  # noqa: BLE001 — 깨진 사진은 글자 카드로
        log.warning("card photo unusable: %s", e)
        return None


def _edge_color(img: Image.Image) -> tuple[int, int, int]:
    """사진 테두리의 평균 색 (흰 배경 상품 사진이면 거의 흰색) — 사진을 카드 배경에 자연스럽게 잇는다."""
    w, h = img.size
    b = max(2, min(w, h) // 40)
    strips = [img.crop((0, 0, w, b)), img.crop((0, h - b, w, h)), img.crop((0, 0, b, h)), img.crop((w - b, 0, w, h))]
    rgb = [0.0, 0.0, 0.0]
    for s in strips:
        m = ImageStat.Stat(s).mean
        for i in range(3):
            rgb[i] += m[i] / len(strips)
    return int(rgb[0]), int(rgb[1]), int(rgb[2])


def _won(n: int) -> str:
    return f"{n:,}원"


def _compare(info: CardInfo) -> tuple[str, str, bool] | None:
    """(비교 가격 글, 퍼센트 글, 강한 근거인가). 쿠팡 최저가·평소 가격(10%↑)이 먼저, 없으면 표시 정가(약한 근거)."""
    if info.ref_price and info.ref_pct:
        text = info.ref_text or f"{info.ref_label or '평소 가격'} {_won(info.ref_price)}"
        return text, f"{info.ref_pct}%↓", True
    if info.list_price and info.price and info.list_price > info.price:
        return f"정가 {_won(info.list_price)}", f"{info.list_pct}%↓" if info.list_pct else "", False
    return None


def _cta(info: CardInfo) -> str:
    if info.cta is not None:  # 인스타 밖(블로그·스레드) 카드: 채널 중립 안내
        return info.cta
    return f"구매 링크: 프로필 링크 → #{info.deal_no}" if info.deal_no else "구매 링크는 프로필 링크에서"


def _ad(info: CardInfo) -> str:
    """카드 안 고지 줄 '광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다' (몰 고지가 없으면 '광고 · 제휴 링크로 수수료를 받아요')."""
    return info.ad or (f"광고 · {info.disclosure}" if info.disclosure else "광고 · 제휴 링크로 수수료를 받아요")


def _footer(card: DealCard, draw: ImageDraw.ImageDraw, info: CardInfo, *, y: int, bg: str | None = None) -> None:
    """바닥: 채널 이름 + 구매 안내, 그 아래 작은 고지."""
    if bg:
        draw.rectangle((0, y - 24, W, H), fill=bg)
    else:
        draw.line((SIDE, y - 24, W - SIDE, y - 24), fill=LINE, width=3)
    f = card.font(32)
    draw.text((SIDE, y), BRAND, font=f, fill=INK)
    cta = _cta(info)
    cf = card.font_regular(30)
    draw.text((W - SIDE - _w(draw, cta, cf), y + 2), cta, font=cf, fill=MUTED)
    df = _fit(draw, _ad(info), card.font, 28, W - 2 * SIDE, min_size=20)
    draw.text((SIDE, y + 50), _ad(info), font=df, fill=INK)


def _paste_contained(canvas: Image.Image, img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    fitted = ImageOps.contain(img, (x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x0 + (x1 - x0 - fitted.width) // 2, y0 + (y1 - y0 - fitted.height) // 2))


# ---------------------------------------------------------------- 사진 없는 글자 카드 (no_photo: text_card)
def text_card(card: DealCard, info: CardInfo) -> Image.Image:
    bg = "#F6F3EE"
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    y = 96
    _chip(d, BRAND, card.font(30), SIDE, y, fill=INK, ink="#FFFFFF")
    if info.deal_no and info.cta is None:  # 핫딜 페이지 번호는 인스타 카드에만 (스레드·블로그는 글 안에 구매 링크)
        _chip(d, f"#{info.deal_no}", card.font(30), W - SIDE, y, fill=bg, ink=INK, outline=INK, right=True)
    nf = card.font_heavy(72)
    name_lines = _wrap(d, info.name, nf, W - 2 * SIDE, 4)
    cmp_ = _compare(info) if info.price else None
    bits = [b for b in (info.low_label, info.ship) if b]
    # 내용 덩어리를 위 칩과 바닥 사이 가운데에
    block = (60 if info.category else 0) + int(72 * 1.28) * len(name_lines) + 50
    block += (64 if cmp_ else 0) + (190 if info.price else 0) + (50 if bits else 0)
    y = max(220, 170 + (H - 170 - 170 - block) // 2)
    if info.category:
        d.text((SIDE, y), info.category, font=card.font_regular(34), fill=MUTED)
        y += 60
    y = _lines(d, name_lines, nf, INK, SIDE, y, gap=1.28) + 50
    if info.price:
        if cmp_:
            sf = card.font_regular(40)
            tw = _ref(d, cmp_[0] + ("보다" if cmp_[1] else ""), sf, SIDE, y, MUTED)
            if cmp_[1]:
                d.text((SIDE + tw + 20, y), cmp_[1], font=card.font(40), fill=ACCENT if cmp_[2] else MUTED)
            y += 64
        pf = _fit(d, _won(info.price), card.font_heavy, 150, W - 2 * SIDE)
        d.text((SIDE, y), _won(info.price), font=pf, fill=ACCENT)
        y += int(_size(pf) * 1.2) + 10
    if bits:
        bf = card.font_regular(38)
        one = "  ·  ".join(bits)
        # 기록 라벨('우리가 본 최근 N일 중 가장 낮음 (MM/DD HH시 기준)')은 길어서 한 줄에 안 들어가면 나눠 쓴다 (기준 시각은 안 자름)
        rows = [one] if _w(d, one, bf) <= W - 2 * SIDE else [ln for b in bits for ln in _split_basis(b)]
        for ln in rows[:3]:
            d.text((SIDE, y), ln, font=bf, fill=INK)
            y += int(_size(bf) * 1.3)
    _footer(card, d, info, y=H - 140)
    return img


# ---------------------------------------------------------------- A: 사진 크게 + 큰 가격
def layout_a(card: DealCard, info: CardInfo, photo: Image.Image) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    photo_h = 780
    edge = _edge_color(photo)
    # 흰 배경 사진은 연한 회색 판 위에, 배경색이 있는 사진은 그 색으로 이어서
    panel = PANEL if min(edge) > 235 else edge
    d.rectangle((0, 0, W, photo_h), fill=panel)
    if min(edge) > 235:
        # 흰 배경 사진의 흰 테두리가 회색 판에서 네모로 보이지 않게 흰 바탕을 판 색으로
        photo = _whiten_to(photo, panel)
    _paste_contained(img, photo, (56, 56, W - 56, photo_h - 40))
    top = 40
    if info.shop_name:
        _chip(d, info.shop_name, card.font(28), SIDE - 16, top, fill="#FFFFFF", ink=INK)
    if info.deal_no and info.cta is None:
        _chip(d, f"#{info.deal_no}", card.font(28), W - SIDE + 16, top, fill=INK, ink="#FFFFFF", right=True)

    y = photo_h + 36
    y = _lines(d, _wrap(d, info.name, card.font(44), W - 2 * SIDE, 2), card.font(44), INK, SIDE, y, gap=1.3) + 14
    if info.price:
        cmp_ = _compare(info)
        if cmp_:
            sf = card.font_regular(36)
            tw = _ref(d, cmp_[0] + ("보다" if cmp_[1] else ""), sf, SIDE, y, MUTED)
            if cmp_[1]:
                d.text((SIDE + tw + 18, y - 2), cmp_[1], font=card.font(38), fill=ACCENT if cmp_[2] else MUTED)
            y += 50
        pf = _fit(d, _won(info.price), card.font_heavy, 124, W - 2 * SIDE)
        d.text((SIDE, y - 8), _won(info.price), font=pf, fill=ACCENT)
        y += int(_size(pf) * 1.08)
    bits = [b for b in (info.low_label, info.ship) if b]
    if bits and y + 50 < H - 150:
        cx = SIDE
        cf = card.font(28)
        chips: list[tuple[str, bool]] = []
        for i, b in enumerate(bits):
            low = i == 0 and bool(info.low_label)
            # 긴 기록 라벨은 칩 두 개로 (기준 시각은 안 자름)
            parts = _split_basis(b) if _w(d, b, cf) + 40 > W - 2 * SIDE else [b]
            chips += [(part, low) for part in parts]
        for text, low in chips:
            w = _w(d, text, cf) + 40
            if cx > SIDE and cx + w > W - SIDE:  # 이 줄에 안 들어가면 다음 줄
                cx, y = SIDE, y + int(_size(cf) * 1.25) + 30
            if y + 50 >= H - 150:
                break
            w, _ = _chip(d, text, cf, cx, y, fill="#FFF1EE" if low else PANEL, ink=ACCENT if low else INK)
            cx += w + 12
    _footer(card, d, info, y=H - 112)
    return img


def _whiten_to(photo: Image.Image, color: str) -> Image.Image:
    """거의 흰 픽셀을 판 색으로 (흰 배경 상품 사진이 회색 판 위에서 흰 네모로 보이지 않게). 상품 안의 흰색도 조금 바뀌지만 차이가 작다."""
    target = Image.new("RGB", photo.size, color)
    gray = photo.convert("L")
    mask = gray.point(lambda v: 255 if v >= 246 else (0 if v < 232 else int((v - 232) / 14 * 255)))
    out = photo.copy()
    out.paste(target, (0, 0), mask)
    return out


# ---------------------------------------------------------------- B: 가격 정보 카드 (우리 데이터 크게, 사진 작게)
def layout_b(card: DealCard, info: CardInfo, photo: bytes | None) -> Image.Image:
    """캐러셀 딜 장과 같은 그림 (card_slides.deal_spec). 사진(바이트)이 없거나 깨졌으면 사진 칸 없이 글만.
    card.layout 과 상관없이 B 그림으로 (card_slides.render 는 카드 양식이 P 면 종이 카드로 보냄 — 한 번만 B 로 그릴 때도 B)."""
    from dealbot.media.card_slides import deal_spec, render

    return render(card, deal_spec(info, photo), layout="B")


# ---------------------------------------------------------------- C: 사진 꽉 채움 + 가격 스티커
def layout_c(card: DealCard, info: CardInfo, photo: Image.Image) -> Image.Image:
    edge = _edge_color(photo)
    img = Image.new("RGB", (W, H), edge)
    d = ImageDraw.Draw(img)
    bar_h = 220
    _paste_contained(img, photo, (90, 110, W - 90, H - bar_h - 30))
    # 아래 흰 띠: 이름 한 줄 + 몰·배송·번호
    d.rectangle((0, H - bar_h, W, H), fill="#FFFFFF")
    nf = card.font(44)
    name = _wrap(d, info.short or info.name, nf, W - 2 * SIDE, 1)
    d.text((SIDE, H - bar_h + 30), name[0] if name else "", font=nf, fill=INK)
    sub = [b for b in (info.shop_name, info.ship) if b]
    sub.append(_cta(info).replace("구매 링크: ", ""))
    d.text((SIDE, H - bar_h + 100), "  ·  ".join(sub), font=card.font_regular(30), fill=MUTED)
    d.text((SIDE, H - bar_h + 158), _ad(info), font=_fit(d, _ad(info), card.font, 28, W - 2 * SIDE, min_size=20), fill=INK)
    if info.price:
        _sticker(card, img, info)
    return img


def _sticker(card: DealCard, img: Image.Image, info: CardInfo) -> None:
    """오른쪽 위에 살짝 기울인 둥근 가격 스티커: (근거 있는 비교면) 'N%↓' + 가격."""
    size = 330
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((6, 10, size - 2, size - 2), fill=(0, 0, 0, 40))  # 그림자
    d.ellipse((0, 0, size - 8, size - 8), fill=ACCENT)
    c = (size - 8) // 2
    cmp_ = _compare(info)
    price = _won(info.price or 0)
    pf = _fit(d, price, card.font_heavy, 64, size - 70, min_size=34)
    if cmp_ and cmp_[2] and cmp_[1]:
        tf = card.font_heavy(78)
        top = cmp_[1]
        d.text((c - _w(d, top, tf) // 2, c - 104), top, font=tf, fill="#FFFFFF")
        d.text((c - _w(d, price, pf) // 2, c + 6), price, font=pf, fill="#FFFFFF")
        lab = (info.ref_label or "평소 가격") + " 대비"
        lf = card.font_regular(24)
        d.text((c - _w(d, lab, lf) // 2, c + 6 + int(_size(pf) * 1.15)), lab, font=lf, fill="#FFE3DD")
    else:
        d.text((c - _w(d, price, pf) // 2, c - _size(pf) // 2 - 6), price, font=pf, fill="#FFFFFF")
    layer = layer.rotate(8, resample=Image.Resampling.BICUBIC, expand=True)
    img.paste(layer, (W - layer.width - 30, 30), layer)


def layout_p(card: DealCard, info: CardInfo, photo: bytes | None) -> Image.Image:
    """P 종이 카드 (주인 결정 10/16): media/card_paper. 사진이 없거나 깨졌으면 같은 종이의 이름 카드로."""
    from dealbot.media.card_paper import LAYOUT, deal_spec
    from dealbot.media.card_slides import render

    return render(card, deal_spec(info, photo), layout=LAYOUT)  # card.layout 과 상관없이 P 그림으로


def render_variant(card: DealCard, layout: str, info: CardInfo, photo: bytes | None) -> Image.Image:
    if layout == "P":
        return layout_p(card, info, photo)
    if layout == "B":
        return layout_b(card, info, photo)
    src = _open_photo(photo)
    if src is None:
        return text_card(card, info)
    if layout == "A":
        return layout_a(card, info, src)
    return layout_c(card, info, src)
