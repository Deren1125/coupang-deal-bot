"""애드픽 쇼핑메이트 핫딜 JSON API 수집기.

GET https://adpick.co.kr/apis/sdk_shopping_hotdeal.php?affid=<내 affid>
- 제휴몰 핫딜 목록을 제휴 링크와 함께 준다 (1분에 1회 이하 호출 권장).
- 응답 필드명은 계정 발급 후 실제 응답으로 확인해야 한다. options.field_map 으로 매핑을 바꿀 수 있고,
  파싱에 실패하면 첫 항목의 키 목록을 로그에 남긴다.
"""

from __future__ import annotations

from typing import Any

from dealbot.collectors.base import BaseCollector, CollectorUnavailable
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.shops import ShopRegistry
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price

ENDPOINT = "https://adpick.co.kr/apis/sdk_shopping_hotdeal.php"

DEFAULT_FIELD_MAP: dict[str, list[str]] = {
    "id": ["id", "hotdeal_id", "p_id", "product_id", "idx", "no"],
    "name": ["title", "name", "product_name", "p_name", "goods_name"],
    "price": ["price", "sale_price", "p_price", "sprice", "discount_price"],
    "original_price": ["org_price", "origin_price", "list_price", "normal_price", "oprice"],
    "discount_rate": ["discount_rate", "sale_rate", "dc_rate", "rate"],
    "url": ["link", "url", "deeplink", "aff_link", "landing_url", "click_url"],
    "landing": ["landing", "landing_url", "origin_url", "product_url", "p_url"],
    "image": ["image", "img", "thumb", "thumbnail", "image_url", "img_url"],
    "shop": ["shop", "mall", "mall_name", "site", "store", "shop_name", "advertiser"],
}


def _pick(item: dict[str, Any], keys: list[str]) -> Any:
    for k in keys:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return None


def extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "list", "items", "result", "hotdeal", "hotdeals", "products"):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                inner = extract_items(v)
                if inner:
                    return inner
    return []


def parse_item(item: dict[str, Any], *, source: str, registry: ShopRegistry, field_map: dict[str, list[str]]) -> Product | None:
    name = _pick(item, field_map["name"])
    url = _pick(item, field_map["url"])
    if not name or not url:
        return None
    price = parse_price(str(_pick(item, field_map["price"]) or "")) or 0
    original = parse_price(str(_pick(item, field_map["original_price"]) or ""))
    rate_raw = _pick(item, field_map["discount_rate"])
    try:
        rate = float(str(rate_raw).replace("%", "")) if rate_raw is not None else None
    except ValueError:
        rate = None
    landing = _pick(item, field_map["landing"]) or ""
    shop_name = str(_pick(item, field_map["shop"]) or "")
    shop = registry.by_alias(shop_name) if shop_name else None
    if shop is None and landing:
        shop = registry.by_url(str(landing))
    shop_key = shop.key if shop else "adpick"
    ident = _pick(item, field_map["id"])
    if landing and shop:
        pid = ShopRegistry.product_key(shop.key, str(landing))
    elif ident is not None:
        pid = f"adpick:{ident}"
    else:
        pid = ShopRegistry.product_key(shop_key, str(url))
    return Product(
        source=source,
        product_id=pid,
        shop=shop_key,
        name=str(name).strip(),
        price=price,
        url=str(landing or url),
        image_url=str(_pick(item, field_map["image"]) or "") or None,
        original_price=original if original and original > price else None,
        discount_rate=rate,
        affiliate_url=str(url),  # affid 로 받은 링크이므로 이미 내 제휴 링크
        extra={"shop_name": shop_name, "adpick_id": ident},
    )


@register("adpick_hotdeal")
class AdpickHotdealCollector(BaseCollector):
    requires_coupang = False

    def check_available(self) -> None:
        if not self.ctx.settings.secrets.has_adpick:
            raise CollectorUnavailable("ADPICK_AFFID not configured")

    async def collect(self) -> list[Product]:
        self.check_available()
        affid = self.ctx.settings.secrets.adpick_affid
        field_map = {**DEFAULT_FIELD_MAP, **{k: list(v) for k, v in (self.opt("field_map") or {}).items()}}
        registry = getattr(self.ctx, "shops", None) or self.ctx.settings.shop_registry()
        http_cfg = self.ctx.settings.http

        async def _do() -> Any:
            resp = await self.ctx.http.get(self.opt("endpoint", ENDPOINT), params={"affid": affid})
            resp.raise_for_status()
            return resp.json()

        data = await retry_async(_do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label="adpick hotdeal")
        items = extract_items(data)
        products = [p for p in (parse_item(x, source=self.name, registry=registry, field_map=field_map) for x in items) if p]
        if items and not products:
            self.log.warning("adpick: %d items but none parsed — first item keys: %s", len(items), sorted(items[0].keys()))
        self.log.info("adpick: %d items, %d parsed", len(items), len(products))
        return products
