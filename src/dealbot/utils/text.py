from __future__ import annotations

import re

_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)")
_MAN_RE = re.compile(r"(\d+(?:\.\d+)?)만")


def parse_price(text: str | None) -> int | None:
    """'12,900원', '12900', '1.2만', '3만5천원' 등에서 원 단위 정수 추출. 실패 시 None."""
    if not text:
        return None
    t = text.replace(" ", "")
    m_man = _MAN_RE.search(t)
    m_num = _PRICE_RE.search(t)
    if m_man and (not m_num or m_num.start() >= m_man.start()):
        value = int(float(m_man.group(1)) * 10000)
        rest = t[m_man.end() :]
        m_cheon = re.match(r"(\d)천", rest)
        m_tail = re.match(r"(\d{1,4})(?!\d)", rest)
        if m_cheon:
            value += int(m_cheon.group(1)) * 1000
        elif m_tail:
            value += int(m_tail.group(1))
        return value
    if not m_num:
        return None
    try:
        return int(m_num.group(1).replace(",", ""))
    except ValueError:
        return None


def format_won(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}원"


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


_DECOR = re.compile(r"[★☆♥♡◆◇■□●○※♪♬✔✅🔥🚨⭐]+")
_HYPE = re.compile(r"(초특가|역대급|핫딜|특가|대박|강추|최저가|단독)(?=[\s:!\]\)]|$)")
_TAG = re.compile(r"^\s*[\[(【](?:무배|무료배송|무료|타임딜|오늘만|한정|광고|카드|쿠폰)[^\])】]*[\])】]\s*")


def clean_name(name: str | None) -> str:
    """게시판 제목 꾸밈 걷어내기: ★·♥ 같은 기호, '초특가'·'역대급' 같은 홍보 낱말, 앞머리의 [무배]·(타임딜) 태그."""
    if not name:
        return ""
    s = _DECOR.sub(" ", name)
    for _ in range(3):
        s = _TAG.sub("", s)
    s = _HYPE.sub("", s)
    s = re.sub(r"\s*→\s*", "→", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -·:,")
    return s or name.strip()


_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)(?![a-z])", re.I)
_COUNT = re.compile(r"(?:x\s*)?(\d{1,4})\s*(개입|개|입|팩|봉|캔|병|롤|매|포|구|정|봉지|박스|ea)(?![가-힣a-z])", re.I)
_TIMES = re.compile(r"[x×*]\s*(\d{1,4})(?![\d.,]*\s*(?:kg|g|ml|l|cm|mm|m)\b)", re.I)


def unit_price(name: str | None, price: int | None) -> str | None:
    """상품명의 용량·개수로 단가를 계산한다. 예) '왕교자 1.05kg x 2봉' 13,900원 → '100g당 662원'.
    확실히 읽히는 경우만 (용량 1개 + 개수 최대 2개). 애매하면 None."""
    if not name or not price:
        return None
    text = name.replace("×", "x").replace("*", "x")
    sizes = _SIZE.findall(text)
    counts = [int(n) for n, _ in _COUNT.findall(text)]
    unit_word = (_COUNT.findall(text) or [("", "")])[0][1]
    if not counts:
        counts = [int(n) for n in _TIMES.findall(text)]
        unit_word = "개"
    if len(counts) > 2 or len(sizes) > 1:
        return None
    count = 1
    for c in counts:
        count *= c
    if not 1 <= count <= 1000:
        return None
    word = {"개입": "개", "입": "개", "ea": "개"}.get(unit_word.lower(), unit_word or "개")
    each = f"{word}당 {round(price / count):,}원" if count >= 2 else None
    per = None
    if sizes:
        qty = float(sizes[0][0].replace(",", "."))
        unit = sizes[0][1].lower()
        base = "g" if unit in ("g", "kg") else "ml"
        total = qty * (1000 if unit in ("kg", "l") else 1) * count
        if total > 100:
            per = f"100{base}당 {round(price / total * 100):,}원"
    if each and per:
        return f"{each} ({per})"
    return each or per
