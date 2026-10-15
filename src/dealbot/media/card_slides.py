"""인스타그램 정보 카드·캐러셀 장·릴스 화면 (주인 결정 10/15, 조사 10/15).

인스타는 남의 사진을 주로 다시 올리는 계정을 2026-04-30 부터 덜 보여 준다(판매처 사진도 그런지는 확인 안 됨). 그래서 화면의 주인공은
우리 데이터 — 가격, 1개당 가격, 비교 가격, '우리가 본 최근 N일 중 가장 낮음' — 이고, 판매처 사진은 작게(높이 30% 이하) 둔다.

- 장마다 그릴 글은 SlideSpec 에 모두 들어 있고, 그리는 함수는 그 글만 쓴다 → 올리기 전 검사(insta_style.validate)가
  spec.texts() 로 '장 안 고지가 있나'·'근거 없는 말이 없나'를 본다.
- 4:5(1080×1350): deal(딜 하나 — 딜 하나짜리 글의 B 카드와 캐러셀 딜 장), cover(표지), end(마지막 장), list(단가표·기준선).
- 9:16(1080×1920): reel_hook(첫 1초 — 가격이 내려간 폭), reel_deal(딜 하나), reel_end(마지막 장). 릴스 화면 위·아래는 인스타 단추·본문이
  덮으므로 글은 가운데(y 250~1500)에만 둔다.
- 모든 장 맨 위 띠에 고지 줄 '광고 · …' (공정위 지침: 사진·영상 게시물은 그 안에도 표시, 첫 부분에).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from dealbot.media.card_variants import (
    ACCENT,
    BRAND,
    INK,
    LINE,
    MUTED,
    PANEL,
    _chip,
    _edge_color,
    _fit,
    _lines,
    _open_photo,
    _paste_contained,
    _size,
    _strike,
    _w,
    _whiten_to,
    _wrap,
)

if TYPE_CHECKING:
    from dealbot.media.card import CardInfo, DealCard

W, H = 1080, 1350  # 피드 4:5
RW, RH = 1080, 1920  # 릴스 9:16
SIDE = 72
BAND = 92  # 맨 위 고지 띠
FOOT = 96  # 바닥 안내 띠
PHOTO_MAX = 0.30  # 판매처 사진은 화면 높이의 이만큼까지만
GENERIC_AD = "광고 · 제휴 링크로 수수료를 받아요"
SOFT = "#FFF1EE"


@dataclass(slots=True)
class SlideSpec:
    """한 장에 그릴 글과 사진. 그리는 함수는 여기 있는 글만 쓴다 (texts() 가 검사 대상)."""

    kind: str  # deal | cover | end | list | reel_hook | reel_deal | reel_end
    ad: str  # 고지 줄 '광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다' (모든 장)
    head: str = ""  # 작은 머리글 ('오늘의 핫딜 · 샴푸', '점심 TOP3')
    tag: str = ""  # 오른쪽 위 ('#12', '2/7')
    title: str = ""  # 상품명·큰 제목
    big: str = ""  # 큰 숫자 (가격)
    was: str = ""  # 큰 가격 앞의 비교 가격 (취소선 — 데이터 근거가 있을 때만)
    big_note: str = ""  # 큰 글 아래 한 줄
    rows: list[tuple[str, str]] = field(default_factory=list)  # 표 (항목, 값)
    items: list[tuple[str, str, str]] = field(default_factory=list)  # 목록 (이름, 값, 작은 글)
    note: str = ""  # 작은 안내 (기준 시각·자료 범위)
    footer: str = ""  # 바닥 안내 (구매 링크)
    photo: bytes | None = None  # 확인된 판매처 사진 (작게) — 없으면 글만
    alt: str = ""  # 인스타 대체 텍스트

    def texts(self) -> list[str]:
        out = [self.ad, self.head, self.tag, self.title, self.big, self.was, self.big_note, self.note, self.footer]
        for a, b in self.rows:
            out += [a, b]
        for a, b, c in self.items:
            out += [a, b, c]
        return [t for t in out if t]


# ---------------------------------------------------------------- 데이터 → 글
def unit_parts(unit: str | None) -> list[tuple[str, str]]:
    """unit_price 결과를 (항목, 값) 으로: '롤당 530원 (100g당 50원)' → [('1롤당', '530원'), ('100g당', '50원')]."""
    out: list[tuple[str, str]] = []
    for per, amount in re.findall(r"([^\s(]+?)당 ([\d,]+원)", unit or ""):
        label = per.replace("ml", "mL")
        if not label[:1].isdigit():
            label = f"1{label}"
        out.append((f"{label}당", amount))
    return out


def deal_rows(info: CardInfo) -> list[tuple[str, str]]:
    """카드 표와 본문 목록에 같이 쓰는 (항목, 값). 둘째 줄은 1개당 가격 (있을 때) — 본문 '카드 표 둘째 줄' 안내와 맞춘다."""
    rows: list[tuple[str, str]] = []
    if info.price:
        rows.append(("가격", f"{info.price:,}원"))
    parts = unit_parts(info.unit)
    if parts and info.price:
        label, value = parts[0]
        rows.append((label, value + "".join(f" · {lab} {val}" for lab, val in parts[1:])))
    if info.ref_text and info.ref_pct:
        rows.append(("비교", f"{info.ref_text} ({info.ref_pct}%↓)"))
    elif info.list_price:
        rows.append(("정가", f"{info.list_price:,}원" + (f" ({info.list_pct}%↓)" if info.list_pct else "")))
    if info.low_label:
        rows.append(("기록", info.low_label))
    if info.ship:
        rows.append(("배송", info.ship))
    if info.rating:
        rows.append(("평점", f"{info.rating}" + (f" · 리뷰 {info.review_count:,}건" if info.review_count else "")))
    if info.shop_name:
        rows.append(("판매", info.shop_name))
    return rows


def ad_of(info: CardInfo) -> str:
    return info.ad or (f"광고 · {info.disclosure}" if info.disclosure else GENERIC_AD)


def alt_text(info: CardInfo, rows: list[tuple[str, str]] | None = None) -> str:
    """대체 텍스트: 상품명과 표 내용 (사진을 못 보는 사람·검색용)."""
    rows = deal_rows(info) if rows is None else rows
    body = ", ".join(f"{a} {b}" for a, b in rows[:5])
    return f"{info.name} 가격 정보 카드 — {body}"[:900]


def deal_spec(info: CardInfo, photo: bytes | None, *, tag: str | None = None, head: str | None = None,
              footer: str | None = None) -> SlideSpec:
    """딜 하나 장 (B 카드·캐러셀 딜 장): 위 고지 띠, 이름 + 작은 사진, 표(가격 크게 → 1개당 → 비교 → 기록 …), 바닥 구매 안내."""
    rows = deal_rows(info)
    if footer is None:
        footer = f"구매 링크: 프로필 링크 → 핫딜 페이지 #{info.deal_no}" if info.deal_no else "구매 링크는 프로필 링크에서"
    return SlideSpec(
        kind="deal",
        ad=ad_of(info),
        head=head if head is not None else BRAND + (f" · {info.category}" if info.category else ""),
        tag=tag if tag is not None else (f"#{info.deal_no}" if info.deal_no else ""),
        title=info.name,
        rows=rows,
        footer=footer,
        photo=photo,
        alt=alt_text(info, rows),
    )


# ---------------------------------------------------------------- 그리기 보조
def _band(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, width: int) -> None:
    """맨 위 고지 띠: 왼쪽 '광고 · …', 오른쪽 번호."""
    d.rectangle((0, 0, width, BAND), fill=INK)
    tf = card.font(30)
    tag_w = _w(d, spec.tag, tf) + 24 if spec.tag else 0
    af = _fit(d, spec.ad, card.font, 30, width - 2 * SIDE - tag_w, min_size=20)
    d.text((SIDE, (BAND - _size(af)) // 2 - 2), spec.ad, font=af, fill="#FFFFFF")
    if spec.tag:
        d.text((width - SIDE - _w(d, spec.tag, tf), (BAND - 30) // 2 - 2), spec.tag, font=tf, fill="#FFFFFF")


def _foot(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, width: int, height: int) -> None:
    if not spec.footer:
        return
    d.rectangle((0, height - FOOT, width, height), fill=SOFT)
    ff = _fit(d, spec.footer, card.font, 32, width - 2 * SIDE, min_size=22)
    d.text((SIDE, height - FOOT + (FOOT - _size(ff)) // 2 - 4), spec.footer, font=ff, fill=INK)


def _photo_box(img: Image.Image, photo: Image.Image, box: tuple[int, int, int, int]) -> None:
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=24, fill=PANEL)
    edge = _edge_color(photo)
    ph = _whiten_to(photo, PANEL) if min(edge) > 235 else photo
    x0, y0, x1, y1 = box
    _paste_contained(img, ph, (x0 + 14, y0 + 14, x1 - 14, y1 - 14))


def _table(card: DealCard, d: ImageDraw.ImageDraw, rows: list[tuple[str, str]], x0: int, y: int, x1: int, bottom: int,
           *, big: int = 112, value_size: int = 36) -> int:
    """표: '가격' 줄은 아주 크게, 나머지는 항목·값 (값은 두 줄까지). 바닥을 넘는 줄은 그리지 않는다."""
    lf = card.font_regular(30)
    label_w = 180
    for label, value in rows:
        if label == "가격":
            pf = _fit(d, value, card.font_heavy, big, x1 - x0 - label_w, min_size=56)
            h = int(_size(pf) * 1.16) + 16
            if y + h > bottom:
                break
            d.text((x0, y + h // 2 - 20), label, font=lf, fill=MUTED)
            d.text((x0 + label_w, y + 4), value, font=pf, fill=ACCENT)
        else:
            vf = card.font(value_size)
            lines = _wrap(d, value, vf, x1 - x0 - label_w, 2)
            h = max(value_size * 2, int(value_size * 1.3) * len(lines) + 24)
            if y + h > bottom:
                break
            d.text((x0, y + 16), label, font=lf, fill=MUTED)
            _lines(d, lines, vf, ACCENT if label == "기록" else INK, x0 + label_w, y + 12, gap=1.3)
        y += h
        d.line((x0, y, x1, y), fill=LINE, width=2)
    return y


# ---------------------------------------------------------------- 4:5 장
def _deal(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    _band(card, d, spec, W)
    y = BAND + 26
    if spec.head:
        d.text((SIDE, y), spec.head, font=card.font_regular(30), fill=MUTED)
    photo = _open_photo(spec.photo)
    side = 300  # 사진 칸 (높이 1350 의 22%)
    top = y + 48
    name_w = W - 2 * SIDE - (side + 28 if photo else 0)
    nf = card.font(46)
    name_bottom = _lines(d, _wrap(d, spec.title, nf, name_w, 4 if photo else 3), nf, INK, SIDE, top, gap=1.28)
    if photo is not None:
        _photo_box(img, photo, (W - SIDE - side, top, W - SIDE, top + side))
    y = max(name_bottom, top + side if photo else 0) + 26
    d.line((SIDE, y, W - SIDE, y), fill=INK, width=3)
    _table(card, d, spec.rows, SIDE, y + 10, W - SIDE, H - FOOT - 16)
    _foot(card, d, spec, W, H)
    return img


def _cover(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), "#F6F3EE")
    d = ImageDraw.Draw(img)
    _band(card, d, spec, W)
    y = BAND + 50
    if spec.head:
        d.text((SIDE, y), spec.head, font=card.font_regular(36), fill=MUTED)
        y += 64
    tf = card.font_heavy(84)
    y = _lines(d, _wrap(d, spec.title, tf, W - 2 * SIDE, 2), tf, INK, SIDE, y, gap=1.16) + 6
    if spec.big_note:
        bf = card.font_regular(32)
        y = _lines(d, _wrap(d, spec.big_note, bf, W - 2 * SIDE, 2), bf, MUTED, SIDE, y, gap=1.3) + 18
    # 목차: 딜마다 한 줄 이름 + 작은 비교 글, 오른쪽 가격 (5개가 다 들어가게)
    y = _items(card, d, spec.items, SIDE, y, W - SIDE, H - FOOT - 64, name_size=36, value_size=46, name_lines=1)
    if spec.note:
        d.text((SIDE, H - FOOT - 56), spec.note, font=_fit(d, spec.note, card.font_regular, 28, W - 2 * SIDE, 20), fill=MUTED)
    _foot(card, d, spec, W, H)
    return img


def _items(card: DealCard, d: ImageDraw.ImageDraw, items: list[tuple[str, str, str]], x0: int, y: int, x1: int, bottom: int,
           *, name_size: int = 38, value_size: int = 52, name_lines: int = 2) -> int:
    """목록: 왼쪽 이름(+ 작은 글), 오른쪽 값(크게). 바닥을 넘는 줄은 그리지 않는다."""
    for name, value, sub in items:
        vf = card.font_heavy(value_size)
        vw = _w(d, value, vf) if value else 0
        nf = card.font(name_size)
        lines = _wrap(d, name, nf, x1 - x0 - vw - 30, name_lines)
        h = int(name_size * 1.28) * len(lines) + (40 if sub else 0) + 28
        h = max(h, int(value_size * 1.3) + 20)
        if y + h > bottom:
            break
        yy = _lines(d, lines, nf, INK, x0, y + 14, gap=1.28)
        if sub:
            d.text((x0, yy + 2), sub, font=_fit(d, sub, card.font_regular, 28, x1 - x0 - vw - 30, 20), fill=MUTED)
        if value:
            d.text((x1 - vw, y + (h - _size(vf)) // 2 - 6), value, font=vf, fill=ACCENT)
        y += h
        d.line((x0, y, x1, y), fill=LINE, width=2)
    return y


def _list(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)
    _band(card, d, spec, W)
    y = BAND + 40
    if spec.head:
        d.text((SIDE, y), spec.head, font=card.font_regular(32), fill=MUTED)
        y += 56
    tf = card.font_heavy(84)
    y = _lines(d, _wrap(d, spec.title, tf, W - 2 * SIDE, 2), tf, INK, SIDE, y, gap=1.18)
    if spec.big_note:
        bf = card.font(34)
        y = _lines(d, _wrap(d, spec.big_note, bf, W - 2 * SIDE, 2), bf, ACCENT, SIDE, y + 6, gap=1.3)
    d.line((SIDE, y + 18, W - SIDE, y + 18), fill=INK, width=3)
    _items(card, d, spec.items, SIDE, y + 24, W - SIDE, H - FOOT - 80, name_size=34, value_size=48)
    if spec.note:
        nf = card.font_regular(26)
        _lines(d, _wrap(d, spec.note, nf, W - 2 * SIDE, 2), nf, MUTED, SIDE, H - FOOT - 74, gap=1.3)
    _foot(card, d, spec, W, H)
    return img


def _end(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)
    _band(card, d, spec, W)
    d.line((SIDE, BAND + 2, W - SIDE, BAND + 2), fill="#3A3A3A", width=2)
    tf = card.font_heavy(92)
    lines = [ln for part in spec.title.split("\n") for ln in _wrap(d, part, tf, W - 2 * SIDE, 2)]
    y = 330
    y = _lines(d, lines, tf, "#FFFFFF", SIDE, y, gap=1.2) + 40
    if spec.big:
        _, h = _chip(d, spec.big, card.font_heavy(56), SIDE, y, fill=ACCENT, ink="#FFFFFF")
        y += h + 50
    if spec.note:
        nf = card.font_regular(34)
        y = _lines(d, _wrap(d, spec.note, nf, W - 2 * SIDE, 3), nf, "#CFCFCF", SIDE, y, gap=1.35)
    if spec.footer:
        ff = _fit(d, spec.footer, card.font, 32, W - 2 * SIDE, 22)
        d.text((SIDE, H - FOOT + 20), spec.footer, font=ff, fill="#FFFFFF")
    return img


# ---------------------------------------------------------------- 9:16 릴스 화면
REEL_TOP = 230  # 이 위는 인스타 단추가 덮음
REEL_BOTTOM = 1500  # 이 아래는 본문·단추가 덮음


def _reel_ad(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, *, fill: str = INK) -> int:
    """릴스 고지: 위쪽 가운데 둥근 띠 (인스타 단추에 안 가리는 자리). 다음 y."""
    af = _fit(d, spec.ad, card.font, 34, RW - 2 * SIDE - 40, min_size=22)
    tw = _w(d, spec.ad, af)
    h = int(_size(af) * 1.3) + 24
    x = (RW - tw) // 2 - 20
    d.rounded_rectangle((x, REEL_TOP, x + tw + 40, REEL_TOP + h), radius=h // 2, fill=fill)
    d.text((x + 20, REEL_TOP + 10), spec.ad, font=af, fill="#FFFFFF")
    return REEL_TOP + h + 40


def _reel_head(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, y: int) -> int:
    hf = card.font(40)
    if spec.head:
        _, h = _chip(d, spec.head, hf, SIDE, y, fill=ACCENT, ink="#FFFFFF")
    else:
        h = int(_size(hf) * 1.25) + 20
    if spec.tag:
        tf = card.font_regular(40)
        d.text((RW - SIDE - _w(d, spec.tag, tf), y + 10), spec.tag, font=tf, fill=MUTED)
    return y + h + 40


def _reel_hook(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    d = ImageDraw.Draw(img)
    y = _reel_ad(card, d, spec)
    y = _reel_head(card, d, spec, y + 30) + 80
    if spec.was:  # '22,900원 →' (취소선) 다음 줄에 큰 가격
        wf = _fit(d, spec.was + " →", card.font_heavy, 104, RW - 2 * SIDE, min_size=60)
        ww = _strike(d, spec.was, wf, SIDE, y, MUTED)
        d.text((SIDE + ww + 24, y), "→", font=wf, fill=INK)
        y += int(_size(wf) * 1.3)
    pf = _fit(d, spec.big, card.font_heavy, 190, RW - 2 * SIDE, min_size=80)
    d.text((SIDE, y), spec.big, font=pf, fill=ACCENT)
    y += int(_size(pf) * 1.22)
    if spec.big_note:
        bf = card.font(48)
        y = _lines(d, _wrap(d, spec.big_note, bf, RW - 2 * SIDE, 2), bf, INK, SIDE, y, gap=1.3) + 30
    if spec.title:
        tf = card.font_regular(48)
        y = _lines(d, _wrap(d, spec.title, tf, RW - 2 * SIDE, 2), tf, INK, SIDE, y, gap=1.3) + 30
    if spec.note and y < REEL_BOTTOM - 60:
        nf = card.font_regular(36)
        _lines(d, _wrap(d, spec.note, nf, RW - 2 * SIDE, 2), nf, MUTED, SIDE, y, gap=1.3)
    return img


def _reel_deal(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    d = ImageDraw.Draw(img)
    y = _reel_ad(card, d, spec)
    y = _reel_head(card, d, spec, y)
    photo = _open_photo(spec.photo)
    if photo is not None:
        side = min(480, int(RH * PHOTO_MAX))
        _photo_box(img, photo, ((RW - side) // 2, y, (RW + side) // 2, y + side))
        y += side + 30
    nf = card.font(52)
    y = _lines(d, _wrap(d, spec.title, nf, RW - 2 * SIDE, 2 if photo else 3), nf, INK, SIDE, y, gap=1.25) + 10
    rows = [r for r in spec.rows if r[0] != "가격"]
    if spec.big:
        pf = _fit(d, spec.big, card.font_heavy, 150 if photo else 180, RW - 2 * SIDE, min_size=70)
        d.text((SIDE, y), spec.big, font=pf, fill=ACCENT)
        y += int(_size(pf) * 1.18)
    _table(card, d, rows, SIDE, y, RW - SIDE, REEL_BOTTOM, value_size=38)
    return img


def _reel_end(card: DealCard, spec: SlideSpec) -> Image.Image:
    img = Image.new("RGB", (RW, RH), INK)
    d = ImageDraw.Draw(img)
    _reel_ad(card, d, spec, fill="#3A3A3A")
    tf = card.font_heavy(100)
    lines = [ln for part in spec.title.split("\n") for ln in _wrap(d, part, tf, RW - 2 * SIDE, 2)]
    y = _lines(d, lines, tf, "#FFFFFF", SIDE, 620, gap=1.2) + 50
    if spec.big:
        _, h = _chip(d, spec.big, card.font_heavy(64), SIDE, y, fill=ACCENT, ink="#FFFFFF")
        y += h + 60
    if spec.note:
        nf = card.font_regular(40)
        _lines(d, _wrap(d, spec.note, nf, RW - 2 * SIDE, 3), nf, "#CFCFCF", SIDE, y, gap=1.35)
    return img


_RENDER = {
    "deal": _deal, "cover": _cover, "end": _end, "list": _list,
    "reel_hook": _reel_hook, "reel_deal": _reel_deal, "reel_end": _reel_end,
}


def render(card: DealCard, spec: SlideSpec) -> Image.Image:
    return _RENDER[spec.kind](card, spec)


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
