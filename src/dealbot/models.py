"""도메인 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

DEAL_KINDS = ("hotdeal", "coupon", "event")


@dataclass(slots=True)
class Product:
    """수집기가 돌려주는 딜 1건. 상품 딜이면 가격은 원 단위 정수, 쿠폰/이벤트면 0 이어도 됨."""

    source: str  # 수집기 이름 (config.collectors[].name)
    product_id: str  # 쇼핑몰 범위의 안정적인 키. 예) coupang:123, toss:P4Qr1ope, naver:url:abcd1234
    name: str  # 상품명 또는 딜 제목
    price: int  # 원. 쿠폰/이벤트처럼 가격이 없으면 0
    url: str  # 원본 URL (쇼핑몰 상품 페이지 등)
    shop: str = "unknown"  # 쇼핑몰 키 (config.shops[].key)
    deal_kind: str = "hotdeal"  # hotdeal | coupon | event
    headline: str | None = None  # "[토스쇼핑 첫 구매 시 3,000원 추가 할인]" 같은 머리글
    image_url: str | None = None
    original_price: int | None = None
    discount_rate: float | None = None  # 표시 할인율 %
    category: str | None = None
    is_rocket: bool | None = None
    is_free_shipping: bool | None = None
    shipping: str | None = None  # "무료", "3,000원" 등 원문
    affiliate_url: str | None = None  # 이미 "내" 제휴 링크인 경우
    external_id: str | None = None  # 외부 소스의 글 번호 등
    recommend_count: int | None = None  # 커뮤니티 추천 수
    view_count: int | None = None
    comment_count: int | None = None
    rank: int | None = None  # API 목록(골드박스/베스트) 순위
    rating: float | None = None  # 상품 페이지 별점 (보강 시)
    review_count: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def effective_discount_rate(self) -> float | None:
        if self.discount_rate is not None:
            return float(self.discount_rate)
        if self.original_price and self.original_price > self.price > 0:
            return round((1 - self.price / self.original_price) * 100, 1)
        return None

    @property
    def has_price(self) -> bool:
        return bool(self.price and self.price > 0)


@dataclass(slots=True)
class PriceStats:
    """특정 상품의 최근 N일 가격 통계 (현재 관측 제외)."""

    count: int = 0
    avg: float | None = None
    min: int | None = None
    max: int | None = None
    first_seen_at: datetime | None = None
    last_price: int | None = None


@dataclass(slots=True)
class DealVerdict:
    is_deal: bool
    reasons: list[str] = field(default_factory=list)
    discount_rate: float | None = None
    avg_price: float | None = None
    below_avg_pct: float | None = None
    sample_count: int = 0
    score: float = 0.0
    market_price: int | None = None  # 시중가(쿠팡 검색) 대조 결과
    market_source: str | None = None
    below_market_pct: float | None = None


@dataclass(slots=True)
class Deal:
    product: Product
    verdict: DealVerdict
    affiliate_url: str | None = None
    detected_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "product": asdict(self.product),
            "verdict": asdict(self.verdict),
            "affiliate_url": self.affiliate_url,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Deal:
        pdata = dict(d["product"])
        known = set(Product.__dataclass_fields__)
        pdata = {k: v for k, v in pdata.items() if k in known}
        return cls(
            product=Product(**pdata),
            verdict=DealVerdict(**d["verdict"]),
            affiliate_url=d.get("affiliate_url"),
            detected_at=datetime.fromisoformat(d["detected_at"]) if d.get("detected_at") else None,
        )


@dataclass(slots=True)
class PublishResult:
    ok: bool
    message_id: int | None = None
    error: str | None = None
    dry_run: bool = False
