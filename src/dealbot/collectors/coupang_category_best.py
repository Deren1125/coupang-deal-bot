"""쿠팡 카테고리별 베스트 상품 수집기.

카테고리를 돌아가며 한 판에 categories_per_run 개씩만 조회한다 (주인 결정 10/13: 3개만 보던 것을 넓히되 호출 예산 안에서).
다음 판은 그다음 카테고리부터 — 어디까지 봤는지는 DB(kv)에 남겨 재시작해도 이어서 돈다.
한 시간 공용 몫(쿠팡 API 예산에서 딥링크 예약분을 뺀 것)이 keep_free_calls 이하로 남으면 멈추고 나머지는 다음 판에
(골드박스·시중가 조회·딥링크 몫을 남김). 예산이 차서 하나도 못 본 판은 고장이 아니라 '건너뜀'.
"""

from __future__ import annotations

import asyncio

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.coupang.client import CoupangRateLimited, parse_api_product
from dealbot.models import Product

DEFAULT_CATEGORIES = [1016, 1012, 1014]
CURSOR_KV = "category_best_cursor:"  # + 수집기 이름 → 다음 판에 처음 볼 카테고리의 자리 (category_ids 안의 순번)


@register("coupang_category_best")
class CoupangCategoryBestCollector(BaseCollector):
    requires_coupang = True

    def _cursor(self, n: int) -> int:
        try:
            return int(self.ctx.db.kv_get(CURSOR_KV + self.name) or 0) % n
        except (TypeError, ValueError):
            return 0

    def _save_cursor(self, value: int) -> None:
        try:
            self.ctx.db.kv_set(CURSOR_KV + self.name, str(value))
        except Exception as e:  # noqa: BLE001 — 자리를 못 남겨도 수집은 계속 (다음 판에 같은 카테고리부터)
            self.log.warning("could not save category cursor: %s", e)

    def slice_for_run(self) -> list[int]:
        """이번 판에 볼 카테고리 (돌아가며). categories_per_run 이 없거나 0 이면 예전처럼 전부."""
        categories = [int(c) for c in self.opt("category_ids", DEFAULT_CATEGORIES)]
        if not categories:
            return []
        per_run = int(self.opt("categories_per_run", 0) or 0)
        per_run = len(categories) if per_run <= 0 else min(per_run, len(categories))
        start = self._cursor(len(categories))
        return [categories[(start + i) % len(categories)] for i in range(per_run)]

    async def collect(self) -> list[Product]:
        self.check_available()
        assert self.ctx.coupang is not None
        categories = [int(c) for c in self.opt("category_ids", DEFAULT_CATEGORIES)]
        limit = int(self.opt("limit", 50))
        delay = float(self.opt("delay_between_categories_seconds", 2))
        image_size = self.opt("image_size")
        keep_free = int(self.opt("keep_free_calls", 0) or 0)
        todo = self.slice_for_run()
        start = self._cursor(len(categories)) if categories else 0

        products: list[Product] = []
        errors: list[str] = []
        done = 0  # 이번 판에 처리한 카테고리 수 (실패도 넘어감 — 한 카테고리가 막혀 계속 같은 자리에 머물지 않게)
        budget_stop: str | None = None
        for i, cid in enumerate(todo):
            budget = getattr(self.ctx.coupang, "budget", None)
            left = budget.remaining("bestcategory") if budget is not None else None
            if left is not None and left <= keep_free:
                budget_stop = f"{left} coupang call(s) left this hour (keep {keep_free} for goldbox/market/deeplink)"
                self.log.info("category best: stopping before category %s — %s; the rest next run", cid, budget_stop)
                break
            if i > 0 and delay > 0:
                await asyncio.sleep(delay)
            try:
                raw = await self.ctx.coupang.best_category(cid, limit=limit, image_size=image_size)
            except CoupangRateLimited as e:
                self.log.warning("api budget exhausted — stopping at category %s (%s)", cid, e)
                budget_stop = str(e)
                break
            except Exception as e:  # noqa: BLE001 — 한 카테고리 실패가 전체를 막지 않게
                self.log.warning("category %s failed: %s", cid, e)
                errors.append(f"{cid}: {e}")
                done += 1
                continue
            done += 1
            parsed = [p for p in (parse_api_product(x, self.name) for x in raw) if p]
            for p in parsed:
                p.extra["category_id"] = cid
            products.extend(parsed)
            self.log.info("category %s: %d items", cid, len(parsed))
        if categories and done:
            self._save_cursor((start + done) % len(categories))

        if not products:
            if errors:
                raise RuntimeError("all categories failed: " + "; ".join(errors))
            if budget_stop:
                # 예산이 차서 하나도 못 봄: 고장이 아님 → run_collector 가 '건너뜀'으로 남기고 다음 판에 같은 자리부터
                raise CoupangRateLimited(f"category best skipped: {budget_stop}")
        # 여러 카테고리에 같은 상품이 있을 수 있음 → product_id 로 중복 제거
        seen: set[str] = set()
        unique: list[Product] = []
        for p in products:
            if p.product_id in seen:
                continue
            seen.add(p.product_id)
            unique.append(p)
        return unique
