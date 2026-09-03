"""수집기 플러그인 기반 클래스."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from dealbot.models import Product

if TYPE_CHECKING:
    from dealbot.config import Settings
    from dealbot.coupang.client import CoupangClient
    from dealbot.shops import ShopRegistry
    from dealbot.storage.db import Database


@dataclass(slots=True)
class CollectorContext:
    """수집기가 사용할 공용 자원."""

    settings: Settings
    http: httpx.AsyncClient
    db: Database
    coupang: CoupangClient | None = None
    shops: ShopRegistry | None = None


class CollectorUnavailable(Exception):
    """자격 증명 부족 등으로 이 수집기를 실행할 수 없음."""


class BaseCollector(ABC):
    """새 수집기를 만들려면 이 클래스를 상속하고 `collect()` 를 구현한 뒤
    `@register("이름")` 으로 등록하거나 config 의 type 에 "패키지.모듈:클래스" 를 적으면 됩니다.
    """

    #: 쿠팡 API 자격 증명이 필요한 수집기인지
    requires_coupang: bool = False

    def __init__(self, name: str, options: dict[str, Any], ctx: CollectorContext) -> None:
        self.name = name
        self.options = options or {}
        self.ctx = ctx
        self.log = logging.getLogger(f"dealbot.collector.{name}")

    def check_available(self) -> None:
        if self.requires_coupang and self.ctx.coupang is None:
            raise CollectorUnavailable("coupang api credentials not configured")

    @abstractmethod
    async def collect(self) -> list[Product]:
        """상품 목록을 수집해 돌려준다. 예외를 던지면 파이프라인이 기록/알림 처리한다."""

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)
