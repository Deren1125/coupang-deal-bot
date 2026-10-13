"""쿠팡 골드박스(오늘의 특가) 수집기."""

from __future__ import annotations

from typing import ClassVar

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.coupang.client import parse_api_product
from dealbot.models import Product


@register("coupang_goldbox")
class CoupangGoldboxCollector(BaseCollector):
    requires_coupang = True
    # 응답 첫 상품의 칸 이름을 프로세스마다 한 번만 로그 (값은 안 남김) — 정가·할인율 칸이 정말 없는지 서버 로그로 보이게.
    # 10/14 서버: 골드박스 상품 가격 기록의 정가·할인율이 전부 비어 있었다 (parse_api_product 가 읽는 이름의 칸이 없음)
    _keys_logged: ClassVar[bool] = False

    async def collect(self) -> list[Product]:
        self.check_available()
        assert self.ctx.coupang is not None
        raw = await self.ctx.coupang.goldbox(
            limit=self.opt("limit"), image_size=self.opt("image_size")
        )
        if raw and not CoupangGoldboxCollector._keys_logged and isinstance(raw[0], dict):
            CoupangGoldboxCollector._keys_logged = True
            self.log.info("goldbox response fields (first item): %s", ", ".join(sorted(str(k) for k in raw[0])))
        products: list[Product] = []
        for item in raw:
            p = parse_api_product(item, self.name)
            if p is not None:
                products.append(p)
        self.log.info("goldbox: %d items (%d parsed)", len(raw), len(products))
        return products
