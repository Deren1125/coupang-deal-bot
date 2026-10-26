"""인스타그램 정보 카드·캐러셀 장·릴스 화면 (주인 결정 10/15, 조사 10/15).

인스타는 남의 사진을 주로 다시 올리는 계정을 2026-04-30 부터 덜 보여 준다(판매처 사진도 그런지는 확인 안 됨). 그래서 화면의 주인공은
우리 데이터 — 가격, 1개당 가격, 비교 가격, '우리가 본 최근 N일 중 가장 낮음' — 이고, 판매처 사진은 작게(높이 30% 이하) 둔다.

- 장마다 그릴 글은 SlideSpec 에 모두 들어 있고, 그리는 함수는 그 글만 쓴다 → 올리기 전 검사(insta_style.validate)가
  spec.texts() 로 '장 안 고지가 있나'·'근거 없는 말이 없나'를 본다.
- 4:5(1080×1350): deal(딜 하나 — 딜 하나짜리 글의 B 카드와 캐러셀 딜 장), cover(표지), end(마지막 장), list(단가표·기준선).
- 9:16(1080×1920): reel_hook(첫 1초 — 가격이 내려간 폭), reel_deal(딜 하나), reel_end(마지막 장). 릴스 화면 위 250px·아래 420px 는
  인스타 단추·본문이 덮으므로(안전 영역) 고지·글은 모두 그 사이(y 250~1500)에만 둔다. 고지 띠는 위 안전 영역 바로 아래.
- 모든 장 맨 위 띠에 고지 줄 '광고 · …' (공정위 지침: 사진·영상 게시물은 그 안에도 표시, 첫 부분에).
- 취소선 '전 가격 → 지금 가격' 은 '전 가격' 이 우리 가격 기록에 있는 이 상품의 바로 앞 가격일 때만, 근거('우리가 본 10/08 가격')와 함께
  (표시광고법 — 다른 판매처의 최저가·표시 정가를 취소선으로 그리면 이 상품의 종전 가격처럼 읽힌다). 시중가 비교만 있으면
  지금 가격을 크게, 그 아래 따로 '네이버 최저가 19,900원보다 4,000원 ↓' (취소선 없이).
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
    Font,
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
PAPER = "P"  # 종이 카드 양식 (media/card_paper.py — 주인 결정 10/16, 기본)
SPEC_LAYOUTS = ("P", "B")  # SlideSpec 으로 그리는 양식 (맨 위 고지 줄이 첫 부분)


@dataclass(slots=True)
class SlideSpec:
    """한 장에 그릴 글과 사진. 그리는 함수는 여기 있는 글만 쓴다 (texts() 가 검사 대상)."""

    kind: str  # deal | cover | end | list | reel_hook | reel_deal | reel_end
    ad: str  # 고지 줄 '광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다' (모든 장)
    head: str = ""  # 작은 머리글 ('쇼핑일기 · 샴푸', '점심 TOP3')
    tag: str = ""  # 오른쪽 위 ('#12', '2/7')
    title: str = ""  # 상품명·큰 제목
    big: str = ""  # 큰 숫자 (가격)
    was: str = ""  # 큰 가격 앞의 이 상품 바로 앞 가격 (취소선 — 우리 가격 기록에 있을 때만, was_note 와 함께)
    was_note: str = ""  # 취소선 가격의 근거 '우리가 본 10/08 가격' (was 가 있으면 꼭)
    big_note: str = ""  # 큰 글 아래 한 줄
    rows: list[tuple[str, str]] = field(default_factory=list)  # 표 (항목, 값)
    items: list[tuple[str, str, str]] = field(default_factory=list)  # 목록 (이름, 값, 작은 글)
    note: str = ""  # 작은 안내 (기준 시각·자료 범위)
    footer: str = ""  # 바닥 안내 (구매 링크)
    photo: bytes | None = None  # 확인된 판매처 사진 (작게) — 없으면 글만
    alt: str = ""  # 인스타 대체 텍스트
    page: str = ""  # 종이 카드(P) 딜 장의 장 번호 '2/7' (tag 는 포스트잇의 '#번호')

    def texts(self) -> list[str]:
        out = [self.ad, self.head, self.tag, self.page, self.title, self.big, self.was, self.was_note, self.big_note, self.note,
               self.footer]
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


def compare_line(info: CardInfo) -> str:
    """시중가·평소 가격 비교를 취소선 없이 한 줄로: '네이버 최저가 19,900원보다 4,000원 ↓' | '평소 40,000원대보다 18% ↓'.
    비교 가격은 다른 판매처 값이거나 가격대라 이 상품의 종전 가격처럼 그리지 않는다. 근거가 없으면 빈칸."""
    if not (info.price and info.ref_price and info.ref_pct and info.ref_text):
        return ""
    if info.ref_label.endswith("최저가") and info.ref_price > info.price:
        return f"{info.ref_label} {info.ref_price:,}원보다 {info.ref_price - info.price:,}원 ↓"
    return f"{info.ref_text}보다 {info.ref_pct}% ↓"


def ad_of(info: CardInfo) -> str:
    """카드 안 고지 줄. 수수료 없는 딜 하나짜리 카드(info.ad_free)는 '' (고지·'광고' 칩 없음)."""
    if info.ad_free:
        return ""
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
    if footer is None and info.cta is not None:
        footer = info.cta  # 인스타 밖(블로그·스레드) 카드: 채널 중립 안내
    if footer is None:
        footer = f"구매 링크: 프로필 링크 → 핫딜 페이지 #{info.deal_no}" if info.deal_no else "구매 링크는 프로필 링크에서"
    return SlideSpec(
        kind="deal",
        ad=ad_of(info),
        head=head if head is not None else BRAND + (f" · {info.category}" if info.category else ""),
        # 핫딜 페이지 번호는 인스타 카드에만 (스레드·블로그 카드는 글 안에 구매 링크가 있어 '#번호' 가 필요 없다)
        tag=tag if tag is not None else (f"#{info.deal_no}" if info.deal_no and info.cta is None else ""),
        title=info.name,
        rows=rows,
        footer=footer,
        photo=photo,
        alt=alt_text(info, rows),
    )


@dataclass(slots=True)
class FirstPart:
    """B 가 아닌 카드 양식(current·A·C, 그 양식의 사진 없는 글자 카드)의 첫 부분에 그리는 글 (올리기 전 검사용).
    이 양식들은 고지 줄이 바닥의 작은 글씨에만 있거나(A·C·글자 카드) 아예 없어서(current) 첫 부분 고지가 없다."""

    lines: list[str]

    def texts(self) -> list[str]:
        return [t for t in self.lines if t]


def deal_spec_for(layout: str, info: CardInfo, photo: bytes | None) -> SlideSpec:
    """딜 하나짜리 카드의 장: P(종이 카드, 기본) 는 card_paper.deal_spec, B 는 deal_spec (예전 가격 정보 카드 — 되돌리기용)."""
    if layout == PAPER:
        from dealbot.media import card_paper

        return card_paper.deal_spec(info, photo)
    return deal_spec(info, photo)


def drawn_first_part(layout: str, info: CardInfo, photo: bytes | None) -> SlideSpec | FirstPart:
    """딜 하나짜리 인스타 카드에서 고지가 있어야 할 첫 부분의 글 (insta_style.validate 의 slides 로).
    P·B: 캐러셀 딜 장과 같은 그림 — 맨 위 고지 줄이 첫 부분. 다른 양식: 위 칩·상품명·가격뿐 (고지는 바닥이라 첫 부분이 아님)."""
    if layout in SPEC_LAYOUTS:
        return deal_spec_for(layout, info, photo)
    return FirstPart([BRAND, info.name, f"{info.price:,}원" if info.price else ""])


# ---------------------------------------------------------------- 그리기 보조
def _band(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, width: int) -> None:
    """맨 위 고지 띠: 왼쪽 '광고 · …', 오른쪽 번호."""
    d.rectangle((0, 0, width, BAND), fill=INK)
    tf = card.font(30)
    tag_w = _w(d, spec.tag, tf) + 24 if spec.tag else 0
    head = spec.ad or BRAND  # 수수료 없는 딜(고지 없음)은 띠에 채널 이름만
    af = _fit(d, head, card.font, 30, width - 2 * SIDE - tag_w, min_size=20)
    d.text((SIDE, (BAND - _size(af)) // 2 - 2), head, font=af, fill="#FFFFFF")
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
    box_h = 168 if spec.rows else 0  # 이 단위의 기준선 (있을 때만 — 우리가 올린 딜 3건 이상)
    bottom = H - FOOT - 80 - (box_h + 24 if box_h else 0)
    y = _items(card, d, spec.items, SIDE, y + 24, W - SIDE, bottom, name_size=34, value_size=48)
    if spec.rows:
        _baseline_box(card, d, spec.rows, SIDE, y + 24, W - SIDE, box_h)
    if spec.note:
        nf = card.font_regular(26)
        _lines(d, _wrap(d, spec.note, nf, W - 2 * SIDE, 2), nf, MUTED, SIDE, H - FOOT - 74, gap=1.3)
    _foot(card, d, spec, W, H)
    return img


def _baseline_box(card: DealCard, d: ImageDraw.ImageDraw, rows: list[tuple[str, str]], x0: int, y: int, x1: int, h: int) -> None:
    """단위 장 아래 기준선 칸: 왼쪽 '사도 되는 기준선', 오른쪽 '1롤당 480원' (크게), 아래 근거 한 줄."""
    d.rounded_rectangle((x0, y, x1, y + h), radius=24, fill=SOFT)
    (label, value), rest = rows[0], rows[1:]
    lf = card.font(32)
    vf = _fit(d, value, card.font_heavy, 52, (x1 - x0) // 2, min_size=34)
    d.text((x0 + 28, y + 30), label, font=lf, fill=INK)
    d.text((x1 - 28 - _w(d, value, vf), y + 22), value, font=vf, fill=ACCENT)
    if rest:
        nf = card.font_regular(26)
        _lines(d, _wrap(d, rest[0][1], nf, x1 - x0 - 56, 2), nf, MUTED, x0 + 28, y + 92, gap=1.3)


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
REEL_TOP = 250  # 이 위(위 안전 영역)는 인스타 단추가 덮음
REEL_BOTTOM = RH - 420  # 이 아래(아래 안전 영역 420px)는 본문·단추가 덮음
REEL_AD_Y = REEL_TOP + 12  # 고지 띠: 위 안전 영역 바로 아래 (모든 장)


def _reel_ad(card: DealCard, d: ImageDraw.ImageDraw, spec: SlideSpec, *, fill: str = INK) -> int:
    """릴스 고지: 위쪽 가운데 둥근 띠 (인스타 단추에 안 가리는 자리). 다음 y."""
    af = _fit(d, spec.ad, card.font, 34, RW - 2 * SIDE - 40, min_size=22)
    tw = _w(d, spec.ad, af)
    h = int(_size(af) * 1.3) + 24
    x = (RW - tw) // 2 - 20
    d.rounded_rectangle((x, REEL_AD_Y, x + tw + 40, REEL_AD_Y + h), radius=h // 2, fill=fill)
    d.text((x + 20, REEL_AD_Y + 10), spec.ad, font=af, fill="#FFFFFF")
    return REEL_AD_Y + h + 40


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


def _fits(lines: list[str], font: Font, y: int, gap: float = 1.3) -> bool:
    """줄들이 아래 안전 영역(REEL_BOTTOM) 위에서 끝나나."""
    return y + int(_size(font) * gap) * len(lines) <= REEL_BOTTOM


def _reel_hook(card: DealCard, spec: SlideSpec) -> Image.Image:
    """첫 1초. 우리 기록의 바로 앞 가격이 있으면 근거 줄 + '19,900원 →'(취소선) + 큰 지금 가격,
    시중가 비교만 있으면 큰 지금 가격 + 따로 한 줄 '네이버 최저가 19,900원보다 4,000원 ↓' (취소선 없음)."""
    img = Image.new("RGB", (RW, RH), "#FFFFFF")
    d = ImageDraw.Draw(img)
    y = _reel_ad(card, d, spec)
    y = _reel_head(card, d, spec, y + 30) + 60
    if spec.was and spec.was_note:  # 근거 없는 취소선은 그리지 않는다
        nf = card.font(44)
        d.text((SIDE, y), spec.was_note, font=nf, fill=MUTED)
        y += int(_size(nf) * 1.4)
        wf = _fit(d, spec.was + " →", card.font_heavy, 104, RW - 2 * SIDE, min_size=60)
        ww = _strike(d, spec.was, wf, SIDE, y, MUTED)
        d.text((SIDE + ww + 24, y), "→", font=wf, fill=INK)
        y += int(_size(wf) * 1.3)
    pf = _fit(d, spec.big, card.font_heavy, 190, RW - 2 * SIDE, min_size=80)
    d.text((SIDE, y), spec.big, font=pf, fill=ACCENT)
    y += int(_size(pf) * 1.22)
    if spec.big_note:
        bf = card.font(48)
        lines = _wrap(d, spec.big_note, bf, RW - 2 * SIDE, 2)
        if _fits(lines, bf, y):
            y = _lines(d, lines, bf, INK, SIDE, y, gap=1.3) + 30
    if spec.title:
        tf = card.font_regular(48)
        lines = _wrap(d, spec.title, tf, RW - 2 * SIDE, 2)
        if _fits(lines, tf, y):
            y = _lines(d, lines, tf, INK, SIDE, y, gap=1.3) + 30
    if spec.note:
        nf = card.font_regular(36)
        lines = _wrap(d, spec.note, nf, RW - 2 * SIDE, 2)
        if _fits(lines, nf, y):
            _lines(d, lines, nf, MUTED, SIDE, y, gap=1.3)
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


def render(card: DealCard, spec: SlideSpec, layout: str | None = None) -> Image.Image:
    """장 하나를 그림. 양식(layout, 없으면 카드 기본 양식)이 P(종이 카드)면 4:5 장(deal·cover·list·end)은 card_paper 로 —
    릴스 화면은 양식과 상관없이 그대로. 한 번만 다른 양식으로 그릴 때(DealCard.render(..., layout=))는 layout 을 넘긴다."""
    if (layout or getattr(card, "layout", "")) == PAPER:
        from dealbot.media import card_paper

        if spec.kind in card_paper.RENDER:
            return card_paper.render(spec)
    return _RENDER[spec.kind](card, spec)


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
