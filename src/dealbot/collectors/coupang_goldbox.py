"""쿠팡 골드박스(오늘의 특가) 수집기."""

from __future__ import annotations

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.coupang.client import parse_api_product
from dealbot.models import Product


@register("coupang_goldbox")
class CoupangGoldboxCollector(BaseCollector):
    requires_coupang = True

    async def collect(self) -> list[Product]:
        self.check_available()
        assert self.ctx.coupang is not None
        raw = await self.ctx.coupang.goldbox(
            limit=self.opt("limit"), image_size=self.opt("image_size")
        )
        products: list[Product] = []
        for item in raw:
            p = parse_api_product(item, self.name)
            if p is not None:
                products.append(p)
        self.log.info("goldbox: %d items (%d parsed)", len(raw), len(products))
        return products
