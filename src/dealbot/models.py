"""도메인 모델."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Product:
    """수집기가 돌려주는 상품 정보. 가격은 원 단위 정수."""

    source: str  # 수집기 이름 (config.collectors[].name)
    product_id: str  # 쿠팡 상품 ID (문자열). 가격 이력/중복 판단의 키
    name: str
    price: int
    url: str  # 원본 상품 URL (www.coupang.com/vp/products/...)
    image_url: str | None = None
    original_price: int | None = None  # 정가 (있을 때만)
    discount_rate: float | None = None  # 표시 할인율 % (있을 때만)
    category: str | None = None
    is_rocket: bool | None = None
    is_free_shipping: bool | None = None
    affiliate_url: str | None = None  # 이미 "내" 파트너스 링크인 경우 (API 응답의 productUrl)
    external_id: str | None = None  # 외부 소스의 글 번호 등
    extra: dict[str, Any] = field(default_factory=dict)

    def effective_discount_rate(self) -> float | None:
        if self.discount_rate is not None:
            return float(self.discount_rate)
        if self.original_price and self.original_price > self.price > 0:
            return round((1 - self.price / self.original_price) * 100, 1)
        return None


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


@dataclass(slots=True)
class Deal:
    product: Product
    verdict: DealVerdict
    affiliate_url: str | None = None
    detected_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "product": asdict(self.product),
            "verdict": asdict(self.verdict),
            "affiliate_url": self.affiliate_url,
            "detected_at": self.detected_at.isoformat() if self.detected_at else None,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Deal:
        return cls(
            product=Product(**d["product"]),
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
