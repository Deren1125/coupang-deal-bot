"""딜 상품 사진을 자동으로 찾는 곳들과 어디서 찾았는지 세는 기록 (주인 11/14 '사진이 필요한 딜은 왜 있는거야. 자동으로 해줘').

왜 사진이 없었나 (11/11~11/14 서버 deal_photos 보고서 — 사진 필요 #229·#230·#213 모두 쿠팡 게시판 딜, img 없음):
- 쿠팡 딜은 상품 페이지를 읽지 않는다 (enrich.exclude_shops — 쿠팡 페이지는 봇 확인으로 막힘, 우회하지 않음).
  골드박스·카테고리 베스트(파트너스 API)로 온 쿠팡 딜은 API 사진이 있지만, 뽐뿌·루리웹 등 게시판에서 온 쿠팡 딜은
  게시판 목록 썸네일뿐이라(작고, 게시판 사진은 상품 사진으로 안 씀) 사진을 찾을 곳이 하나도 없었다.
- 네이버 쇼핑 검색 Open API 는 2026-07-31 종료 (401) — 이름으로 네이버에서 사진을 찾는 길은 없다.

그래서 더한 것 (둘 다 공식 경로, 로그인·페이지 우회 없음):
1. remembered: 같은 상품(쿠팡은 같은 상품번호)을 전에 API·상품 페이지에서 본 사진 주소 (products.image_url — 이제 게시판 썸네일로 덮이지 않음)
2. coupang_api: 쿠팡 파트너스 검색 API 로 상품명을 찾아 **상품번호가 똑같은** 결과의 사진만 (다른 상품 사진을 쓰지 않음).
   검색은 시간당 coupang.photo_search_per_hour 번까지 ('photo' 몫 — 딥링크 예약 몫은 건드리지 않음), 못 찾은 상품은 하루 동안 다시 안 찾음.
찾은 사진은 다른 사진과 같은 검사(clean_image: 짧은 변 600px·비율·로고/배너 주소 제외)를 거친다.

PhotoStats: 날마다 어디서 사진을 찾았는지 kv photo_stats 에 센다 (/status 의 '📷 상품 사진' 줄, Blog-Auto 보고서가 DB 에서 바로 읽을 수 있음).
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.media.imagecheck import coupang_image_url, is_board_thumb, is_generic_image
from dealbot.pricing.market import build_keyword
from dealbot.utils.text import clean_name
from dealbot.utils.urls import extract_product_id

log = logging.getLogger(__name__)

STATS_KEY = "photo_stats"
STATS_KEEP_DAYS = 14
MISS_KV = "photo_miss:"  # photo_miss:{쿠팡 상품번호} → 마지막으로 검색했는데 같은 상품이 없던 시각 (epoch)
MISS_TTL = 86400
PHOTO_KIND = "photo"  # 쿠팡 API 예산의 종류 이름 (ApiBudget caps)
# 게시판 제목의 가격·할인 표기 ('479,000원부터~', '30%', '(카드 할인)') — 검색어에 넣으면 쿠팡 검색이 엉뚱해짐
_PRICE_RE = re.compile(r"\d[\d,.]*\s*(?:만\s*)?원\S*|\d+\s*%|\([^)]*\)")


def search_keyword(name: str) -> str:
    """쿠팡 검색어: 상품명에서 가격·할인율·괄호 말을 뺀 핵심 낱말."""
    return build_keyword(_PRICE_RE.sub(" ", clean_name(name)))

# 사진을 찾은 곳 (보이는 이름) — 순서는 deal_photo 가 찾는 차례
SOURCE_LABELS = {
    "owner": "주인 사진",
    "deal": "딜 사진",
    "remembered": "전에 본 사진",
    "page": "상품 페이지",
    "browser": "스토어 크롬",
    "coupang_api": "쿠팡 검색",
    "late_auto": "발행 뒤 자동",
    "asked": "주인에게 부탁",
    "none": "못 찾음",
}


class PhotoStats:
    """날마다(한국 시간) 사진을 찾은 곳별 수. kv photo_stats = {"2026-11-14": {"deal": 3, "coupang_api": 1, ...}}."""

    def __init__(self, db: Any, tz: str = "Asia/Seoul") -> None:
        self.db = db
        self.tz = ZoneInfo(tz)

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self.db.kv_get(STATS_KEY) or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def record(self, source: str, now: datetime | None = None) -> None:
        try:
            day = (now or datetime.now(self.tz)).astimezone(self.tz).strftime("%Y-%m-%d")
            data = self._load()
            row = data.setdefault(day, {})
            row[source] = int(row.get(source, 0)) + 1
            for old in sorted(data)[:-STATS_KEEP_DAYS]:
                data.pop(old, None)
            self.db.kv_set(STATS_KEY, json.dumps(data, ensure_ascii=False, sort_keys=True))
        except Exception as e:  # noqa: BLE001 — 세는 일이 사진 찾기·발행을 막지 않게
            log.debug("photo stats not recorded: %s", e)

    def totals(self, days: int = 7, now: datetime | None = None) -> dict[str, int]:
        today = (now or datetime.now(self.tz)).astimezone(self.tz).date()
        since = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        out: dict[str, int] = {}
        for day, row in self._load().items():
            if day >= since and isinstance(row, dict):
                for k, v in row.items():
                    out[k] = out.get(k, 0) + int(v or 0)
        return out

    def summary(self, days: int = 7, now: datetime | None = None) -> str:
        """'딜 사진 12 · 쿠팡 검색 2 · 못 찾음 1' (없으면 '')."""
        t = self.totals(days, now)
        parts = [f"{label} {t[k]}" for k, label in SOURCE_LABELS.items() if t.get(k)]
        parts += [f"{k} {v}" for k, v in sorted(t.items()) if k not in SOURCE_LABELS and v]
        return " · ".join(parts)


def usable_url(url: str | None) -> bool:
    return bool(url) and not is_board_thumb(url) and not is_generic_image(url)


def coupang_number(p: Any) -> str | None:
    """쿠팡 딜의 상품번호 (상품 주소 → 없으면 product_id 'coupang:123[:옵션]')."""
    if getattr(p, "shop", None) != "coupang":
        return None
    num = extract_product_id(str(getattr(p, "url", "") or ""))
    if num:
        return num
    parts = str(getattr(p, "product_id", "") or "").split(":")
    return parts[1] if len(parts) >= 2 and parts[0] == "coupang" and parts[1].isdigit() else None


class CoupangPhotoSearch:
    """쿠팡 파트너스 검색 API 로 게시판 쿠팡 딜의 상품 사진 찾기 — 상품번호가 똑같은 결과만."""

    def __init__(self, client: Any, db: Any, per_hour: int = 2) -> None:
        self.client = client
        self.db = db
        self.per_hour = per_hour

    def _missed_recently(self, num: str) -> bool:
        try:
            at = float(self.db.kv_get(f"{MISS_KV}{num}") or 0)
        except ValueError:
            return False
        return time.time() - at < MISS_TTL

    async def find(self, p: Any) -> tuple[str | None, str]:
        """(사진 주소, 상태). 상태: found · no_match(검색했는데 같은 상품 없음 — 하루 기억) · budget(이번 시간 몫 다 씀 — 나중에 다시)
        · error(일시 오류 — 나중에 다시) · skip(쿠팡 딜이 아님·꺼짐·이름 없음)."""
        from dealbot.coupang.client import CoupangRateLimited

        num = coupang_number(p)
        if self.client is None or self.per_hour <= 0 or not num:
            return None, "skip"
        if self._missed_recently(num):
            return None, "no_match"
        keyword = search_keyword(str(getattr(p, "name", "") or ""))
        if not keyword.strip():
            return None, "skip"
        try:
            raw = await self.client.search(keyword, limit=10, kind=PHOTO_KIND)
        except CoupangRateLimited:
            return None, "budget"
        except Exception as e:  # noqa: BLE001
            log.info("coupang photo search failed for '%s': %s", keyword[:40], type(e).__name__)
            return None, "error"
        for x in raw or []:
            if not isinstance(x, dict) or str(x.get("productId") or "") != num:
                continue
            img = coupang_image_url(str(x.get("productImage") or "") or None)
            if usable_url(img):
                log.info("coupang photo search: same product %s found for '%s'", num, keyword[:40])
                return img, "found"
        self.db.kv_set(f"{MISS_KV}{num}", str(time.time()))
        log.info("coupang photo search: product %s not in %d results for '%s'", num, len(raw or []), keyword[:40])
        return None, "no_match"

    def prune(self) -> int:
        now = time.time()

        def keep(_key: str, value: str | None) -> bool:
            try:
                return now - float(value or 0) < MISS_TTL
            except ValueError:
                return False

        return int(self.db.kv_delete_prefix(MISS_KV, keep=keep))
