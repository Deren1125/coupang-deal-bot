"""같은 상품이 다른 글·다른 게시판으로 다시 올라오는 것을 막는다.

상품 ID 가 달라도(주소 변형, 다른 게시판) 이름이 거의 같으면 같은 딜로 본다.
다만 전에 올린 것보다 눈에 띄게 싸졌으면 새 딜로 인정한다.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_BRACKET = re.compile(r"^\s*[\[(][^\])]{1,20}[\])]\s*")  # 앞머리 [쿠팡] (토스) 같은 태그
_PRICE = re.compile(r"\d[\d,]*\s*(?:원|won)", re.I)
_NOISE = re.compile(r"무료\s*배송|무배|무료|배송비|핫딜|특가|세일|할인|쿠폰|\(\s*\d+\s*\)|~\s*\d{1,2}/\d{1,2}|\bfree\b", re.I)
_TOKEN = re.compile(r"[가-힣a-z0-9.]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?[a-z가-힣]{0,3}")  # 26, 1.18l, 30롤, 3팩 같은 규격·모델 번호


def normalize_name(name: str) -> str:
    s = name.lower()
    s = _BRACKET.sub("", s)
    s = _PRICE.sub(" ", s)
    s = _NOISE.sub(" ", s)
    return " ".join(s.split())


def _compact(s: str) -> str:
    return re.sub(r"[^가-힣a-z0-9]", "", s)


def similarity(a: str, b: str) -> float:
    """0~1. 글자 순서 비교와 낱말 집합 비교 중 큰 값."""
    na, nb = normalize_name(a), normalize_name(b)
    ca, cb = _compact(na), _compact(nb)
    if not ca or not cb:
        return 0.0
    # 숫자(모델·용량·수량)가 다르면 다른 상품이다: S26 vs S25, 1.18L vs 0.7L, 30롤 vs 24롤
    numa, numb = set(_NUMBER.findall(na)), set(_NUMBER.findall(nb))
    if numa != numb:
        return 0.0
    ratio = SequenceMatcher(None, ca, cb).ratio()
    ta = {t for t in _TOKEN.findall(na) if len(t) >= 2}
    tb = {t for t in _TOKEN.findall(nb) if len(t) >= 2}
    jaccard = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(ratio, jaccard)


def find_duplicate(
    name: str,
    price: int,
    candidates: list[dict[str, Any]],
    *,
    threshold: float = 0.8,
    cheaper_pct: float = 10.0,
    allow_cheaper: bool = True,
) -> dict[str, Any] | None:
    """후보(최근 올린 글·대기 중인 글) 중 같은 상품이 있으면 그 후보를, 없으면 None.

    후보 dict: {product_id, name, price, ...}. allow_cheaper 면 전보다 cheaper_pct% 이상 싸진 딜은 중복으로 보지 않는다.
    """
    best: dict[str, Any] | None = None
    best_score = 0.0
    for c in candidates:
        cname = str(c.get("name") or "")
        if not cname:
            continue
        score = similarity(name, cname)
        if score >= threshold and score > best_score:
            best, best_score = c, score
    if best is None:
        return None
    old_price = int(best.get("price") or 0)
    if allow_cheaper and price > 0 and old_price > 0 and price <= old_price * (1 - cheaper_pct / 100):
        return None  # 확실히 싸졌으면 새 딜
    return {**best, "similarity": round(best_score, 2)}
