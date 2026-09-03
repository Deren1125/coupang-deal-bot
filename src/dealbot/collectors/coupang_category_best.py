"""쿠팡 카테고리별 베스트 상품 수집기."""

from __future__ import annotations

import asyncio

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.coupang.client import CoupangRateLimited, parse_api_product
from dealbot.models import Product

DEFAULT_CATEGORIES = [1016, 1012, 1014]


@register("coupang_category_best")
class CoupangCategoryBestCollector(BaseCollector):
    requires_coupang = True

    async def collect(self) -> list[Product]:
        self.check_available()
        assert self.ctx.coupang is not None
        categories = [int(c) for c in self.opt("category_ids", DEFAULT_CATEGORIES)]
        limit = int(self.opt("limit", 50))
        delay = float(self.opt("delay_between_categories_seconds", 2))
        image_size = self.opt("image_size")

        products: list[Product] = []
        errors: list[str] = []
        for i, cid in enumerate(categories):
            if i > 0 and delay > 0:
                await asyncio.sleep(delay)
            try:
                raw = await self.ctx.coupang.best_category(cid, limit=limit, image_size=image_size)
            except CoupangRateLimited as e:
                self.log.warning("api budget exhausted — stopping at category %s (%s)", cid, e)
                errors.append(f"{cid}: budget")
                break
            except Exception as e:  # noqa: BLE001 — 한 카테고리 실패가 전체를 막지 않게
                self.log.warning("category %s failed: %s", cid, e)
                errors.append(f"{cid}: {e}")
                continue
            parsed = [p for p in (parse_api_product(x, self.name) for x in raw) if p]
            for p in parsed:
                p.extra["category_id"] = cid
            products.extend(parsed)
            self.log.info("category %s: %d items", cid, len(parsed))

        if errors and not products:
            raise RuntimeError("all categories failed: " + "; ".join(errors))
        # 여러 카테고리에 같은 상품이 있을 수 있음 → product_id 로 중복 제거
        seen: set[str] = set()
        unique: list[Product] = []
        for p in products:
            if p.product_id in seen:
                continue
            seen.add(p.product_id)
            unique.append(p)
        return unique
