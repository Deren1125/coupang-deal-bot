"""품절/종료 판별.

신호 1) 커뮤니티 글 제목의 표시: 글쓴이나 다른 회원이 "[품절]", "(종료)", "마감" 같은 말을 제목에 붙인다.
신호 2) 상품 페이지의 재고 표시(JSON-LD offers.availability, og product:availability) — enrich.py 가 읽는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# 확실한 품절 표현. 이 말이 있으면 바로 품절로 본다
STRONG_WORDS: tuple[str, ...] = ("품절", "매진", "완판", "재고소진", "재고 소진", "판매종료", "판매 종료", "소진")
# "종료"/"마감" 은 "종료 임박", "마감 예정" 처럼 아직 진행 중인 뜻으로도 쓰여서 예외를 둔다
WEAK_WORDS: tuple[str, ...] = ("종료", "마감", "끝")
_NOT_YET = re.compile(r"임박|예정|전까지|까지|D-\d|곧\s*(종료|마감)|(종료|마감)\s*(일|시간|시각|기한)")


def looks_sold_out(text: str | None, words: Iterable[str] | None = None) -> bool:
    """제목에 품절/종료 표시가 있으면 True. words 를 주면 그 목록을 '확실한 표현'으로 쓴다."""
    if not text:
        return False
    t = text.strip()
    low = t.lower()
    strong = tuple(words) if words else STRONG_WORDS
    if any(w.lower() in low for w in strong):
        return True
    if any(w in t for w in WEAK_WORDS) and not _NOT_YET.search(t):
        return True
    return False
