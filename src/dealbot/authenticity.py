"""정품·공식 판매처 확인.

오픈마켓(누구나 파는 몰)의 딜은 병행수입·구매대행·리퍼 같은 표시가 있으면 올리지 않고,
공식 판매처 표시(공식스토어, 브랜드관 등)가 확인돼야 자동으로 올린다. 확인이 안 되면 관리자에게 묻는다.
공식 몰(올리브영, 컬리, LF몰 같은 브랜드·직영 몰)과 쿠팡은 병행수입 표시만 없으면 통과.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dealbot.config import AuthenticityConfig
from dealbot.models import Product


@dataclass(slots=True)
class AuthResult:
    status: str  # ok | reject | unknown
    reason: str
    seller: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {"status": self.status, "reason": self.reason, "seller": self.seller}


def _find(text: str, words: list[str], *, whole_word: bool = False) -> str | None:
    """낱말이 글에 있는가. whole_word 면 두 글자짜리('리퍼', '중고')가 다른 말의 일부('리퍼러', '중고등')로 붙어 있을 때는 세지 않는다."""
    low = text.lower()
    for w in words:
        w = (w or "").strip()
        if not w:
            continue
        if whole_word and len(w) <= 2 and re.search(r"[가-힣]", w):
            if re.search(rf"(?<![가-힣]){re.escape(w.lower())}(?![가-힣])", low):
                return w
        elif w.lower() in low:
            return w
    return None


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def check_authenticity(
    cfg: AuthenticityConfig,
    product: Product,
    *,
    post_text: str = "",
    page_text: str = "",
    seller: str | None = None,
) -> AuthResult:
    """제목·게시글·상품 페이지 글에서 정품 여부를 판단한다.

    reject: 병행수입 등 표시가 어디든 있음 / ok: 공식 몰이거나 공식 판매처 표시가 있음 / unknown: 오픈마켓인데 표시를 못 찾음
    """
    if not cfg.enabled:
        return AuthResult("ok", "정품 확인 끔")
    title = " ".join(_clean(x) for x in (product.name, product.headline, str(product.extra.get("title") or "")) if x)
    own = " ".join(x for x in (title, _clean(post_text), _clean(seller)) if x)  # 게시글·제목·판매자명 (우리가 신뢰하는 쪽)
    everything = " ".join(x for x in (own, _clean(page_text)) if x)

    hit = _find(everything, cfg.reject_keywords, whole_word=True)
    if hit:
        where = "상품 페이지" if not _find(own, [hit], whole_word=True) else "제목·게시글"
        return AuthResult("reject", f"'{hit}' 표시 ({where})", seller)
    if product.shop not in cfg.open_markets:
        return AuthResult("ok", "공식 몰·직영 몰", seller)
    marker = _find(own, cfg.official_markers)
    if marker:
        return AuthResult("ok", f"공식 판매처 표시 '{marker}'", seller)
    tail = f" (판매자: {seller})" if seller else ""
    return AuthResult("unknown", f"오픈마켓인데 공식 판매처 표시를 찾지 못함{tail}", seller)
