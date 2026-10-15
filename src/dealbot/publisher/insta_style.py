"""인스타그램 글의 사실·문구·본문 재료와 올리기 전 검사 (양식 A/B/C, TOP5 캐러셀, 점심 릴스, 생필품 단가표).

- 숫자는 모두 데이터에서: 가격, 글 첫 줄과 같은 비교 근거(TemplateRenderer.reference — 쿠팡 최저가·믿을 만한 평소 가격,
  10% 이상일 때만), 기록 최저(low_text), 표시 정가(근거가 없을 때만 보조), 단가, 배송, 평점.
- 고지(공정위 추천·보증 심사지침 2024-12-01): 본문 첫 줄에 '광고 · {몰 짧은 고지}' ('더보기' 아래·댓글 표기는 인정 안 됨),
  카드·캐러셀 모든 장·릴스 화면 안에도 같은 줄, 본문 끝엔 몰의 고지 문구 그대로. 몰 고지가 없는 딜(쇼핑커넥트에 없는 네이버 원본 링크)도
  인스타 글엔 '광고 · 제휴 링크로 수수료를 받아요' (프로필 링크 페이지가 제휴 링크라서) — 고지 없는 인스타 글은 없다.
- 기록 최저는 '우리가 본 최근 N일 중 가장 낮음 (MM/DD HH시 기준)' — 'N일 최저가'·'역대 최저' 라고 쓰지 않는다 (주인 결정 10/15).
- 첫 줄 다음은 사실만 ('이름 가격 · 비교 가격'). '역대급'·'강추' 같은 말, 느낌표, 링크 주소는 안 쓴다 (인스타 본문 링크는 눌리지 않음).
  '저장해 두세요'·'보내 주세요' 같은 부탁 줄도 안 쓴다 (인스타가 저장·공유를 조르는 글은 추천에서 뺄 수 있다고 함 — 10/15 조사).
- 해시태그는 5개까지 (인스타가 2025-12 부터 게시물당 5개로 제한, 적은 수의 구체적인 태그가 낫다는 안내).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dealbot.media.card import CardInfo
from dealbot.media.card_slides import deal_rows, unit_parts
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer, price_band, shipping_text, short_name
from dealbot.shops import Shop, deal_disclosure, disclosure_program
from dealbot.utils.text import clean_name, unit_price

STYLE_TEMPLATES = {"A": "deal_instagram_a.j2", "B": "deal_instagram_b.j2", "C": "deal_instagram_c.j2"}
MAX_HASHTAGS = 5
BRAND_TAG = "#오늘의핫딜"
SHOP_TAGS = {"coupang": "#쿠팡핫딜", "naver": "#네이버쇼핑"}
# 살림 계정 독자가 찾는 말 — 분류·이름에 이 말이 있을 때만 붙인다
_LIVING = re.compile(r"주방|생활|세제|세탁|욕실|청소|수납|화장지|휴지|물티슈|키친|식기|냄비|프라이팬|텀블러|생수|기저귀")
_TAG_CHARS = re.compile(r"[^0-9A-Za-z가-힣]")

AD = "광고"
GENERIC_AD = "제휴 링크로 수수료를 받아요"  # 몰 고지가 없을 때·여러 몰을 묶은 표지의 짧은 고지 (주인 문구 10/15)
TELEGRAM_LINE = "실시간 전체 딜은 텔레그램에 먼저 올라와요 (프로필 링크)"
AS_OF_LINE = "가격·재고는 올린 시점 기준이에요"
# 올리기 전 검사: 고지 줄로 볼 말 / 근거 없이 쓰면 안 되는 말
_AD_LINE = re.compile(r"^광고\s*·\s*\S.*수수료")
_UNBACKED = (("역대", "'역대' — 우리 기록으로 확인할 수 없는 말"), ("최저가", "'최저가' — 데이터로 확인된 비교가 아님"))


def template_for(style: str, default: str) -> str:
    return STYLE_TEMPLATES.get(style, default)


# ---------------------------------------------------------------- 고지
def short_disclosure(text: str | None) -> str:
    """몰 고지의 짧은 꼴. '이 포스팅은 쿠팡 파트너스 활동의 일환으로…' → '쿠팡 파트너스 활동으로 수수료를 받습니다'."""
    if not text:
        return ""
    program = disclosure_program(text)
    if program:
        return f"{program} 활동으로 수수료를 받습니다"
    return text if len(text) <= 48 else text[:47] + "…"


def ad_line(disclosures: Iterable[str | None]) -> str:
    """본문 첫 줄·카드 안 고지: '광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다'. 몰이 여럿이면 프로그램 이름을 '·' 로 잇고,
    몰 고지가 없거나 모양을 모르면 '광고 · 제휴 링크로 수수료를 받아요'."""
    programs: list[str] = []
    for text in disclosures:
        if not text:
            continue
        program = disclosure_program(text)  # 프로그램 이름은 텔레그램·스레드 광고 줄과 같은 곳에서 (shops)
        if program is None:
            return f"{AD} · {GENERIC_AD}"
        if program not in programs:
            programs.append(program)
    if not programs:
        return f"{AD} · {GENERIC_AD}"
    return f"{AD} · {'·'.join(programs)} 활동으로 수수료를 받습니다"


def full_disclosures(deals: Iterable[Deal], registry: Any) -> list[str]:
    """본문 끝에 붙일 몰 고지 문구 (몰마다 한 번, 원문 그대로)."""
    out: list[str] = []
    for d in deals:
        text = deal_disclosure(registry.get(d.product.shop), d.product.extra)
        if text and text not in out:
            out.append(text)
    return out


# ---------------------------------------------------------------- 사실
def ig_name(name: str | None) -> str:
    """인스타 글·카드에 쓰는 상품 이름: 게시판 꾸밈을 걷어내고, 남은 '역대…'·'최저가' 같은 홍보 낱말도 뺀다."""
    s = clean_name(name)
    s = re.sub(r"역대[^\s\[\]()【】]*|최저가", " ", s)
    s = re.sub(r"[\[(【]\s*[\])】]", " ", s)  # 홍보 낱말만 들어 있던 괄호
    return re.sub(r"\s{2,}", " ", s).strip(" -·:,") or clean_name(name)


def low_text(deal: Deal, renderer: TemplateRenderer, days: int) -> str | None:
    """기록 최저 문구 — 'N일 최저가' 대신 '우리가 본 최근 N일 중 가장 낮음 (MM/DD HH시 기준)' (주인 결정 10/15).
    조건은 채널 글과 같다 (renderer.low_label: 기록이 충분하고 실제로 오르내린 적이 있을 때만). 기준 시각은 판정한 때(detected_at) —
    기준 시각을 모르면 쓰지 않는다 (언제 기준인지 없는 '가장 낮음' 은 확인할 수 없는 말)."""
    if deal.detected_at is None:
        return None
    # 문구·기준 시각은 채널 글과 한 곳에서 (renderer.low_label → basis_time) — 채널·스레드·카드가 같은 말
    return renderer.low_label(deal, days)


def card_info(deal: Deal, renderer: TemplateRenderer, *, shop: Shop | None, deal_no: int | None = None) -> CardInfo:
    p, v = deal.product, deal.verdict
    ref_price, ref_pct, is_market = renderer.reference(deal)
    days = int(min(v.history_days or 0, 30))
    # 배송: deal_facts 와 같은 말
    if p.is_rocket:
        ship = "무료 로켓배송" if p.is_free_shipping else "로켓배송"
    else:
        ship = shipping_text(p.shipping) or ("무료배송" if p.is_free_shipping else "")
    price = p.price if p.has_price else None
    list_price = p.original_price if p.original_price and price and p.original_price > price else None
    list_pct = round((1 - price / list_price) * 100) if list_price and price else None
    mall = deal_disclosure(shop, p.extra)
    return CardInfo(
        name=ig_name(p.name),
        price=price,
        short=ig_name((p.extra or {}).get("short_name") or short_name(p.name, limit=22)),
        ref_price=ref_price,
        ref_label=(f"{renderer.market_word(deal)} 최저가" if is_market else "평소 가격") if ref_price else "",
        ref_text=(f"{renderer.market_word(deal)} 최저가 {ref_price:,}원" if is_market else f"평소 {price_band(ref_price, p.price):,}원대")
        if ref_price and ref_pct else "",
        ref_pct=round(ref_pct) if ref_price and ref_pct else None,
        list_price=list_price,
        list_pct=list_pct if list_pct and list_pct >= 5 else None,
        low_label=low_text(deal, renderer, days),
        ship=ship,
        unit=unit_price(clean_name(p.name), p.price) if price else None,
        rating=p.rating,
        review_count=p.review_count,
        shop_name=shop.name if shop else (p.shop if p.shop and p.shop != "unknown" else ""),
        category=(p.category or "").split(">")[-1].strip(),
        deal_no=deal_no,
        disclosure=short_disclosure(mall),
        ad=ad_line([mall]),
    )


def backed_phrases(info: CardInfo) -> list[str]:
    """데이터로 확인된 '최저가' 말 — 시중가 대조 값이 있을 때의 '쿠팡 최저가'·'네이버 최저가' (검사에서 허용)."""
    return [info.ref_label] if info.ref_price and info.ref_pct and "최저가" in info.ref_label else []


def _tag(word: str | None) -> str | None:
    w = _TAG_CHARS.sub("", word or "")
    return f"#{w}" if 2 <= len(w) <= 12 else None


def hashtags(deal: Deal, shop: Shop | None) -> list[str]:
    """핫딜 · 몰 · 분류 · (살림) · 채널 — 이 딜과 관련 있는 것만, 5개까지."""
    p = deal.product
    key = shop.key if shop else p.shop
    cat = (p.category or "").split(">")[-1].strip()
    tags: list[str | None] = ["#핫딜", SHOP_TAGS.get(key) or _tag(f"{shop.name if shop else key}핫딜"), _tag(cat)]
    if _LIVING.search(f"{p.category or ''} {clean_name(p.name)}"):
        tags.append("#살림템")
    tags.append(BRAND_TAG)
    return _dedupe(tags)[:MAX_HASHTAGS]


def multi_hashtags(deals: Sequence[Deal], registry: Any, *, extra: Sequence[str] = ()) -> list[str]:
    """여러 딜을 묶은 글(캐러셀·릴스·단가표): 핫딜 · 채널 · (묶음 이름) · 몰 · (살림) — 5개까지."""
    tags: list[str | None] = ["#핫딜", BRAND_TAG, *extra]
    for d in deals:
        shop = registry.get(d.product.shop)
        key = shop.key if shop else d.product.shop
        tags.append(SHOP_TAGS.get(key) or _tag(f"{shop.name if shop else key}핫딜"))
    if any(_LIVING.search(f"{d.product.category or ''} {clean_name(d.product.name)}") for d in deals):
        tags.append("#살림템")
    return _dedupe(tags)[:MAX_HASHTAGS]


def _dedupe(tags: Iterable[str | None]) -> list[str]:
    out: list[str] = []
    for t in tags:
        if t and t not in out:
            out.append(t)
    return out


# ---------------------------------------------------------------- 딜 하나짜리 글 (양식 A/B/C)
def caption_context(deal: Deal, info: CardInfo, *, shop: Shop | None) -> dict[str, Any]:
    """후보 양식 본문(deal_instagram_a/b/c.j2)의 ig 변수. 첫 줄 고지(first_line), 둘째 줄 사실(hook)."""
    p = deal.product
    name = ig_name((p.extra or {}).get("short_name") or short_name(p.name))
    compare = info.ref_text or None
    hook = name
    if info.price:
        hook = f"{name} {info.price:,}원"
        extra = compare or info.low_label
        if extra:
            hook += f" · {extra}"
    rows = deal_rows(info)
    notes: list[str] = []
    unit = next((label for label, _ in rows[1:2] if label.endswith("당")), None)
    if unit:
        notes.append(f"{unit} 가격은 카드 표 둘째 줄에 있어요")
    notes.append(AS_OF_LINE)
    if info.deal_no:
        cta = f"구매 링크: 프로필 링크 → 핫딜 페이지 #{info.deal_no}"
    else:
        cta = "구매 링크: 프로필 링크 → 핫딜 페이지"
    return {
        "first_line": info.ad or ad_line([deal_disclosure(shop, p.extra)]),
        "hook": hook,
        "name": name,
        "compare": compare,
        "rows": rows,
        "notes": notes,
        "cta": cta,
        "telegram": TELEGRAM_LINE,
        "deal_no": info.deal_no,
        "hashtags": " ".join(hashtags(deal, shop)),
        "disclosure": deal_disclosure(shop, p.extra) or "",
        "allowed": backed_phrases(info),
    }


# ---------------------------------------------------------------- 여러 딜을 묶은 글
@dataclass(slots=True)
class Pick:
    """캐러셀·릴스에 넣을 딜 하나: 딜, 카드 사실, 핫딜 페이지 번호, 확인된 상품 사진."""

    deal: Deal
    info: CardInfo
    deal_no: int | None = None
    photo: bytes | None = None
    queue_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """'#12 휴지 30롤' (핫딜 페이지 번호 + 짧은 이름)."""
        head = f"#{self.deal_no} " if self.deal_no else ""
        return f"{head}{self.info.short or self.info.name}"

    @property
    def line(self) -> str:
        """번호 목록 한 줄: '#12 휴지 30롤 15,900원'."""
        price = f" {self.info.price:,}원" if self.info.price else ""
        return f"{self.label if self.deal_no else '· ' + self.label}{price}"


def date_kr(day: datetime) -> str:
    return f"{day.month}월 {day.day}일 ({'월화수목금토일'[day.weekday()]})"


def bundle_caption(picks: Sequence[Pick], registry: Any, *, title: str, intro: str = "",
                   extra_tags: Sequence[str] = ()) -> str:
    """캐러셀·릴스 본문: 첫 줄 고지, 제목, 번호 목록 '#12 휴지 30롤 15,900원', 안내, 몰 고지 원문, 해시태그."""
    deals = [pk.deal for pk in picks]
    lines = [ad_line(deal_disclosure(registry.get(d.product.shop), d.product.extra) for d in deals), title]
    if intro:
        lines.append(intro)
    lines.append("")
    lines += [pk.line for pk in picks]
    lines += ["", AS_OF_LINE, "구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로 찾아 주세요", TELEGRAM_LINE]
    disclosures = full_disclosures(deals, registry)
    if disclosures:
        lines += ["", *disclosures]
    lines += ["", " ".join(multi_hashtags(deals, registry, extra=extra_tags))]
    return "\n".join(lines)[:2200]


def top5_caption(picks: Sequence[Pick], registry: Any, day: datetime) -> str:
    return bundle_caption(
        picks, registry, title=f"{date_kr(day)} 가격 내려간 딜 TOP{len(picks)}",
        intro="우리가 기록한 가격·비교 근거가 있는 딜만 골랐어요", extra_tags=("#오늘의특가",),
    )


def reel_caption(picks: Sequence[Pick], registry: Any, day: datetime) -> str:
    return bundle_caption(picks, registry, title=f"점심 TOP{len(picks)} · {date_kr(day)} 오늘 올라온 딜", extra_tags=("#점심핫딜",))


# ---------------------------------------------------------------- 생필품 1개당 가격 (화·금)
@dataclass(slots=True)
class UnitKind:
    """단가표의 한 종류: 이름, 찾는 말, 쓸 단가 (unit_parts 의 항목), 장 안에 쓰는 단위."""

    name: str
    pattern: re.Pattern[str]
    unit: str  # '1롤당' | '1매당' | '100mL당' | '100g당' | '1개당'
    label: str  # 장 안에 쓰는 단위 '1롤당', 캡슐 세제는 '1회분(1개)당'
    need: re.Pattern[str] | None = None  # 이름에 이 말도 있어야 (캡슐·시트 세제)
    avoid: re.Pattern[str] | None = None  # 이 말이 있으면 다른 물건 (키친타월은 휴지가 아님)


# (장 제목, 종류들). 한 상품은 위에서부터 처음 맞는 종류 하나로만 센다
UNIT_GROUPS: tuple[tuple[str, tuple[UnitKind, ...]], ...] = (
    ("휴지·물티슈", (
        UnitKind("물티슈", re.compile(r"물티슈"), "1매당", "1매당"),
        UnitKind("휴지", re.compile(r"휴지|화장지|두루마리|\d\s*겹"), "1롤당", "1롤당", avoid=re.compile(r"키친|타월|행주|미용티슈|각티슈")),
    )),
    ("생수", (UnitKind("생수", re.compile(r"생수|먹는샘물|삼다수|아이시스|백산수|평창수|석수|에비앙|볼빅"), "100mL당", "100mL당"),)),
    ("세제", (
        UnitKind("캡슐 세제", re.compile(r"세제"), "1개당", "1회분(1개)당", need=re.compile(r"캡슐|시트|큐브")),
        UnitKind("액체 세제", re.compile(r"세제"), "100mL당", "100mL당"),
        UnitKind("가루 세제", re.compile(r"세제"), "100g당", "100g당"),
    )),
    ("기저귀", (
        UnitKind("기저귀", re.compile(r"기저귀"), "1매당", "1매당"),
        UnitKind("기저귀", re.compile(r"기저귀"), "1개당", "1매당"),
    )),
)
_UNIT_AMOUNT = re.compile(r"([\d,]+)원")


def unit_kind(name: str, unit: str | None) -> tuple[str, UnitKind, int] | None:
    """상품명·단가 글로 (장 제목, 종류, 1개당 원) — 모르면 None. 물티슈를 휴지로 세지 않게 물티슈를 먼저 본다."""
    parts = dict(unit_parts(unit))
    for title, kinds in UNIT_GROUPS:
        for kind in kinds:
            if not kind.pattern.search(name) or (kind.need and not kind.need.search(name)) or (kind.avoid and kind.avoid.search(name)):
                continue
            value = parts.get(kind.unit)
            if value:
                m = _UNIT_AMOUNT.search(value)
                if m:
                    return title, kind, int(m.group(1).replace(",", ""))
            if kind.name == "물티슈":
                return None  # 물티슈인데 매당 값이 없으면 휴지로 넘기지 않음
    return None


@dataclass(slots=True)
class UnitRow:
    title: str  # 장 제목 '휴지·물티슈'
    kind: str  # '휴지'
    label: str  # '1롤당'
    value: int  # 1개당 원
    name: str
    price: int
    deal_no: int | None
    posted: datetime | None = None
    product_id: str = ""


def baseline(values: Sequence[int]) -> int | None:
    """기준선 = 우리가 올린 딜의 1개당 가격 가운데 값 (2건 이상일 때만)."""
    return round(statistics.median(values)) if len(values) >= 2 else None


UNIT_FIND = "구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로"
UNIT_FIND_OLD = "구매 링크: #번호 딜은 프로필 링크 → 핫딜 페이지에서 (지난 딜은 내려감)"


def unit_caption(rows_by_title: dict[str, list[UnitRow]], baselines: list[tuple[str, str, int, int]], deals: Sequence[Deal],
                 registry: Any, *, since: datetime, until: datetime) -> str:
    """단가표 본문: 첫 줄 고지, 제목(기간), 종류마다 낮은 순, 기준선, 몰 고지 원문, 해시태그."""
    lines = [
        ad_line(deal_disclosure(registry.get(d.product.shop), d.product.extra) for d in deals),
        f"이번 주 생필품 1개당 가격 ({since:%m/%d}~{until:%m/%d} 우리가 올린 딜, 우리가 기록한 가격 기준)",
        "",
    ]
    for title, rows in rows_by_title.items():
        lines.append(f"[{title}]")
        for r in rows:
            head = f"#{r.deal_no} " if r.deal_no else "· "
            tail = "" if r.deal_no else " (지난 딜)"
            lines.append(f"{head}{r.name} {r.price:,}원 → {r.kind} {r.label} {r.value:,}원{tail}")
        lines.append("")
    if baselines:
        lines.append("이 가격 밑이면 사도 되는 기준선 (최근 우리가 올린 딜의 가운데 값)")
        lines += [f"· {kind} {label} {value:,}원 (딜 {n}건)" for kind, label, value, n in baselines]
        lines.append("")
    old = any(r.deal_no is None for rows in rows_by_title.values() for r in rows)
    find = ("구매 링크: 프로필 링크 → 핫딜 페이지에서 #번호로 찾아 주세요 (번호 없는 지난 딜은 핫딜 페이지에서 내려갔어요)" if old
            else "구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로 찾아 주세요")
    lines += [AS_OF_LINE, find, TELEGRAM_LINE]
    disclosures = full_disclosures(deals, registry)
    if disclosures:
        lines += ["", *disclosures]
    lines += ["", " ".join(multi_hashtags(deals, registry, extra=("#생필품", "#단가비교")))]
    return "\n".join(lines)[:2200]


# ---------------------------------------------------------------- 올리기 전 검사
def validate(caption: str, *, slides: Sequence[Any] = (), allowed: Iterable[str] = ()) -> list[str]:
    """올리기 전 검사. 문제 목록 (비면 통과).
    - 본문 첫 줄이 '광고 · …수수료…' 고지가 아니면 실패 ('더보기' 아래·끝에만 있는 고지는 인정 안 됨), 본문에 링크 주소가 있어도 실패
    - 카드·캐러셀·릴스의 어느 장이든 그 장에 그리는 글(slide.texts())에 고지 줄이 없으면 실패
    - '역대' 는 늘, '최저가' 는 데이터로 확인된 비교(allowed — '쿠팡 최저가' 같은 시중가 대조 이름)가 아니면 실패"""
    problems: list[str] = []
    first = next((line.strip() for line in caption.strip().splitlines()), "")
    if not _AD_LINE.search(first):
        problems.append(f"본문 첫 줄에 광고·수수료 고지가 없음: {first[:40]!r}")
    if re.search(r"https?://", caption):
        problems.append("본문에 링크 주소가 있음 (인스타 본문 링크는 눌리지 않음 — 프로필 링크로)")
    keep = sorted({a for a in allowed if a}, key=len, reverse=True)
    texts = [("본문", caption)]
    for i, slide in enumerate(slides, 1):
        drawn = list(slide.texts())
        if not any(_AD_LINE.search(t or "") for t in drawn):
            problems.append(f"{i}번째 장에 광고·수수료 고지가 없음")
        texts.append((f"{i}번째 장", "\n".join(drawn)))
    for where, text in texts:
        rest = text
        for phrase in keep:
            rest = rest.replace(phrase, "")
        for word, why in _UNBACKED:
            if word in rest:
                problems.append(f"{where}: {why}")
    return problems
