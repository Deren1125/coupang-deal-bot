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
