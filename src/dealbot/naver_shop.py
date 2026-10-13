"""네이버 쇼핑 검색 API(공식 Open API, openapi.naver.com/v1/search/shop.json) 두 가지 쓰임.

1. 스마트스토어·브랜드스토어 딜의 큰 상품 사진 찾기 (NaverShopSearch.find_image)
   뽐뿌의 네이버 딜은 게시판 목록 썸네일(120px)뿐이고, 스토어 페이지는 429 로 자주 막힌다.
   상품명으로 검색해서 '같은 상품'(검색 결과의 링크·productId 에 딜 주소와 같은 스토어 상품번호가 있고,
   스토어·몰 이름이 둘 다 있으면 서로 같은 것)일 때만 그 사진을 쓴다. 비슷한 다른 상품의 사진은 쓰지 않는다.
2. 쿠팡 딜(골드박스·카테고리 베스트)의 가격 근거: 같은 상품의 네이버 최저가 (NaverPriceReference, 주인 결정 10/13)
   같은 수량·규격, 낱말 대부분 일치, 중고·렌탈·해외직구·옵션 목록·부속품이 아닌 새 상품, 가격비교(카탈로그) 상품이 있거나
   일반 판매글이 여럿일 때만 믿는다. 헷갈리면 근거 없음 (할인을 지어내지 않음).
NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없으면 건너뛴다 (쓰임마다 로그 한 번). 키 값은 로그에 남기지 않는다.
쇼핑 검색은 developers.naver.com(예전 개발자센터) 키로만 된다. 네이버 클라우드 API HUB 에는 쇼핑 검색이 없다
(10/10 확인: HUB /search/v1/shop → 404, 같은 키로 openapi.naver.com → 401). 그런 키면 첫 호출의 401 뒤로 둘 다 끈다.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from dealbot.enrich import is_naver_store
from dealbot.media.imagecheck import is_generic_image
from dealbot.models import Product
from dealbot.pricing.market import NAVER_SOURCE, MarketQuote, same_quantity, tokens
from dealbot.utils.text import clean_name

if TYPE_CHECKING:
    from dealbot.config import NaverReferenceConfig

log = logging.getLogger(__name__)

SEARCH_URL = "https://openapi.naver.com/v1/search/shop.json"
_PRODUCT_NO = re.compile(r"/products/(\d+)")
# 키가 틀렸거나 API 를 더 쓸 수 없음 (401·403 키/권한, 404·410 종료) → 다시 시작할 때까지 안 부름
_DEAD = (401, 403, 404, 410)
_CACHE_MAX = 500


class _Unavailable(Exception):
    pass


def store_product_no(url: str | None) -> str | None:
    """스마트스토어·브랜드스토어 상품 주소의 상품번호 ('smartstore.naver.com/가게/products/123' → '123')."""
    if not url or not is_naver_store(url):
        return None
    m = _PRODUCT_NO.search(urlparse(url).path or "")
    return m.group(1) if m else None


def store_name(url: str | None) -> str | None:
    """스토어 주소의 가게 이름 칸 ('/가게/products/1' → '가게'). 'main'(가게를 안 밝힌 주소)이면 None."""
    if not url or not is_naver_store(url):
        return None
    parts = [x for x in (urlparse(url).path or "").split("/") if x]
    if len(parts) < 2 or parts[1] != "products" or parts[0].lower() == "main":
        return None
    return parts[0].lower()


def _norm(text: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def search_queries(name: str) -> list[str]:
    """검색어: 꾸밈·괄호(가격·배송·기간)를 걷어낸 상품명, 못 찾으면 앞 네 낱말."""
    s = clean_name(name)
    s = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", s)
    words = re.sub(r"[^\w.+%/-]+", " ", s).split()
    out: list[str] = []
    for q in (" ".join(words)[:100].strip(), " ".join(words[:4])):
        if q and q not in out:
            out.append(q)
    return out


def same_product(item: dict[str, Any], deal_url: str, mall: str | None = None) -> bool:
    """검색 결과가 딜과 같은 상품인지: 링크(스토어 주소)의 상품번호 또는 productId 가 딜 주소의 상품번호와 같아야 하고,
    가게 이름(주소)·몰 이름이 양쪽에 다 있으면 서로 같아야 한다. 이름만 비슷한 상품은 안 됨."""
    no = store_product_no(deal_url)
    if not no:
        return False
    link = str(item.get("link") or "").strip()
    link_no = store_product_no(link)
    if link_no:
        if link_no != no:
            return False
    else:
        host = urlparse(link).netloc.lower().split(":")[0]
        pid = str(item.get("productId") or "").strip()
        if pid != no or not (host == "naver.com" or host.endswith(".naver.com")):
            return False  # 다른 몰 링크의 productId 는 스토어 상품번호와 다른 번호 체계
    a, b = store_name(link), store_name(deal_url)
    if a and b and a != b:
        return False
    their, ours = _norm(item.get("mallName")), _norm(mall)
    return not (their and ours and their not in ours and ours not in their)


def _deal_mall(p: Product) -> str | None:
    """딜에서 알고 있는 판매처 이름 (스토어 페이지 JSON-LD 판매자, 정품 확인 때 남김)."""
    auth = p.extra.get("auth")
    seller = auth.get("seller") if isinstance(auth, dict) else None
    return str(seller) if seller else None


class NaverShopSearch:
    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str | None,
        client_secret: str | None,
        *,
        timeout: float = 20,
        display: int = 40,
    ) -> None:
        self.http = http
        self._id = client_id or None
        self._secret = client_secret or None
        self.timeout = timeout
        self.display = max(1, min(100, display))
        self.disabled_reason: str | None = None
        self._told_missing = False
        self._cache: dict[str, str | None] = {}  # 상품번호 → 찾은 사진 (못 찾았으면 None)

    @property
    def configured(self) -> bool:
        return bool(self._id and self._secret)

    async def find_image(self, product: Product) -> str | None:
        """같은 상품(상품번호 일치)의 검색 결과 사진 주소. 스토어 딜이 아니거나, 키가 없거나, 같은 상품이 없으면 None."""
        no = store_product_no(product.url)
        if not no:
            return None
        if not self.configured:
            if not self._told_missing:
                self._told_missing = True
                log.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET not set — 네이버 쇼핑 검색으로 큰 사진 찾기는 건너뜀")
            return None
        if self.disabled_reason:
            return None
        if no in self._cache:
            return self._cache[no]
        mall = _deal_mall(product)
        for q in search_queries(product.name):
            try:
                items = await self._search(q)
            except _Unavailable:
                return None
            except (httpx.HTTPError, ValueError) as e:  # 일시 오류(429·5xx·연결): 기억하지 않고 다음에 다시
                log.info("naver shop search failed for %s: %s", product.name[:40], type(e).__name__)
                return None
            for item in items:
                if not same_product(item, product.url, mall):
                    continue
                img = str(item.get("image") or "").strip()
                if img.startswith("//"):
                    img = "https:" + img
                found = img if img.startswith("http") and not is_generic_image(img) else None
                log.info("naver shop search: %s → 같은 상품(상품번호 %s, %s)%s", product.name[:40], no,
                         item.get("mallName") or "-", "" if found else " 이지만 사진 없음")
                self._remember(no, found)
                return found
        log.info("naver shop search: %s — 상품번호 %s 와 같은 상품 없음", product.name[:40], no)
        self._remember(no, None)
        return None

    async def _search(self, query: str, *, exclude: str | None = None, timeout: float | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "display": self.display, "sort": "sim"}
        if exclude:
            params["exclude"] = exclude
        resp = await self.http.get(
            SEARCH_URL,
            params=params,
            headers={"X-Naver-Client-Id": self._id or "", "X-Naver-Client-Secret": self._secret or ""},
            timeout=self.timeout if timeout is None else timeout,
        )
        if resp.status_code in _DEAD:
            if self.disabled_reason is None:  # 사진 찾기와 최저가 비교가 동시에 맞아도 경고는 한 번
                log.warning(
                    "naver shop search: HTTP %s — 키가 틀렸거나 API 를 쓸 수 없음 (네이버 클라우드 API HUB 키는 쇼핑 검색이 없어 안 됨, "
                    "developers.naver.com 의 검색 API 키만 됨). 다시 시작할 때까지 끔 (큰 사진 찾기·쿠팡 딜 네이버 최저가 비교 모두)",
                    resp.status_code,
                )
            self.disabled_reason = f"HTTP {resp.status_code}"
            raise _Unavailable
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []

    def _remember(self, no: str, image: str | None) -> None:
        if len(self._cache) >= _CACHE_MAX:
            self._cache.pop(next(iter(self._cache)))
        self._cache[no] = image


# ---------------------------------------------------------------- 2. 쿠팡 딜의 네이버 최저가 (가격 근거)
# 검색 결과 productType: 1 = 가격비교(카탈로그) 상품 (lprice = 여러 판매처 중 최저가), 2·3 = 새 상품 일반 판매글.
# 4~6 중고, 7~9 단종, 10~12 판매예정 → 비교에 안 씀
_CATALOG_TYPE = "1"
_NEW_TYPES = frozenset({"1", "2", "3"})
# 검색에서 아예 빼는 것 (API exclude): 중고 · 렌탈 · 해외직구/구매대행
PRICE_EXCLUDE = "used:rental:cbshop"
# 네이버 상품명에 있는데 쿠팡 상품명엔 없으면 같은 상품으로 안 본다 (그 값이 '네이버 최저가'로 잡히면 비교가 틀어짐)
_NOT_SAME_WORDS = (
    # 중고·리퍼·전시·하자
    "중고", "리퍼", "refurb", "전시", "반품", "s급", "a급", "b급", "스크래치", "박스훼손", "하자",
    # 렌탈·약정
    "렌탈", "렌털", "대여", "약정",
    # 해외직구·구매대행·병행수입
    "해외", "직구", "구매대행", "병행", "관부가세",
    # 옵션 목록 (가장 싼 옵션 값이 최저가로 잡힘)
    "택1", "택일", "골라", "모음", "옵션", "균일가", "선택",
    # 부속품
    "케이스", "커버", "필름", "거치대", "호환", "리필", "교체용", "부품", "액세서리", "악세사리", "악세서리", "스트랩", "파우치",
)
_TAG_RE = re.compile(r"<[^>]+>")
_HAS_DIGIT = re.compile(r"\d")
_HAS_NON_DIGIT = re.compile(r"\D")
_PAUSE_ON_429 = 600.0  # 검색 API 가 429 를 주면 이만큼 쉼 (초)


def item_title(item: dict[str, Any]) -> str:
    """검색 결과 상품명 ('<b>샴푸</b> 1000ml &amp; 린스' → '샴푸 1000ml & 린스')."""
    return " ".join(html.unescape(_TAG_RE.sub("", str(item.get("title") or ""))).split())


def _won(v: Any) -> int | None:
    try:
        n = int(str(v or "").replace(",", "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _category(item: dict[str, Any]) -> str | None:
    cats = [str(item.get(f"category{i}") or "").strip() for i in range(1, 5)]
    return ">".join(c for c in cats if c) or None


def same_item(name: str, title: str, cfg: NaverReferenceConfig) -> bool:
    """쿠팡 상품명(name)과 네이버 상품명(title)이 같은 상품인가 (보수적으로).
    - 수량·용량·묶음이 같아야 함 (pricing.market.same_quantity — 2팩·1+1·'+ 증정' 묶음은 다른 상품)
    - 쿠팡 상품명 낱말의 min_token_match 이상이 네이버 상품명에 있어야 함 (띄어쓰기는 무시: '대추 방울토마토' = '대추방울토마토')
    - 첫 낱말(대개 브랜드)과 숫자가 섞인 낱말(버즈3·10세대·128gb 같은 모델·세대·용량)은 모두 있어야 함
    - 쿠팡 상품명에 없는 중고·렌탈·해외직구·옵션 목록·부속품 말이 네이버 상품명에 있으면 다른 상품"""
    lowered, mine = title.lower(), name.lower()
    if any(w in lowered and w not in mine for w in _NOT_SAME_WORDS):
        return False
    if not same_quantity(name, title):
        return False
    words = tokens(name)
    if not words:
        return False
    flat = _norm(title)
    hit = sum(1 for w in words if _norm(w) in flat)
    if hit / len(words) < cfg.min_token_match:
        return False
    if cfg.require_first_token and _norm(words[0]) not in flat:
        return False
    return all(_norm(w) in flat for w in words if _HAS_DIGIT.search(w) and _HAS_NON_DIGIT.search(w))


def naver_lowest(name: str, price: int, items: list[dict[str, Any]], cfg: NaverReferenceConfig) -> tuple[MarketQuote | None, str]:
    """검색 결과에서 쿠팡 상품(name, price)과 확실히 같은 새 상품들의 최저가. (근거, 이유). 확실하지 않으면 (None, 이유).
    가격비교(카탈로그) 상품이 하나 있거나, 일반 판매글이 서로 다른 판매처 min_listings 곳 이상일 때만 믿는다.
    최저가는 맞은 것 전부 중 가장 싼 값 ('네이버 최저가보다 N% 싸요' 가 부풀려지지 않게 — 근거로는 가장 보수적인 값)."""
    catalog: list[MarketQuote] = []
    general: dict[str, MarketQuote] = {}
    for it in items:
        if str(it.get("productType") or "").strip() not in _NEW_TYPES:
            continue  # 중고·단종·판매예정
        lp, title = _won(it.get("lprice")), item_title(it)
        if lp is None or not title or not same_item(name, title, cfg):
            continue
        if lp < price * cfg.min_price_ratio:
            continue  # 쿠팡 값의 절반도 안 됨: 낱개·옵션·부속일 가능성이 커서 최저가로 안 씀
        q = MarketQuote(price=lp, source=NAVER_SOURCE, title=title, url=str(it.get("link") or "") or None, category=_category(it))
        if str(it.get("productType")).strip() == _CATALOG_TYPE:
            catalog.append(q)
        else:
            seller = _norm(it.get("mallName")) or str(it.get("link") or title)
            if seller not in general or lp < general[seller].price:
                general[seller] = q
    found = catalog + list(general.values())
    if not found:
        return None, "같은 상품 없음"
    if not catalog and len(general) < cfg.min_listings:
        return None, f"가격비교 상품 없이 판매글 {len(general)}곳뿐"
    best = min(found, key=lambda q: q.price)
    if (1 - price / best.price) * 100 > cfg.max_below_pct:
        return None, f"네이버 {best.price:,}원보다 {cfg.max_below_pct:g}% 넘게 쌈 — 다른 상품일 수 있음"
    return best, "카탈로그" if any(q is best for q in catalog) else f"판매글 {len(general)}곳"


class LookupRun:
    """수집 한 판의 네이버 조회 한도: 호출 수와 쓴 시간 (5분 수집 루프를 오래 막지 않게). 판이 끝나면 버린다."""

    def __init__(self, max_calls: int, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max(0, max_calls)
        self.seconds = max(0.0, seconds)
        self._clock = clock
        self.started = clock()
        self.calls = 0
        self.matched = 0  # 같은 상품의 최저가를 찾음
        self.no_match = 0  # 검색했는데 확실한 같은 상품이 없음
        self.skipped = 0  # 한도·시간·쉬는 중이라 이번 판엔 못 찾아봄 (다음 판에)
        self.failed = 0  # 일시 오류 (기억하지 않음)

    def time_left(self) -> float:
        return self.seconds - (self._clock() - self.started)

    def allows(self) -> bool:
        return self.calls < self.max_calls and self.time_left() > 0

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in (("calls", self.calls), ("matched", self.matched), ("no_match", self.no_match),
                                          ("skipped", self.skipped), ("failed", self.failed)) if v]
        return " ".join(parts)


class NaverPriceReference:
    """쿠팡 상품의 네이버 쇼핑 최저가 (가격 근거). 검색은 NaverShopSearch 와 같은 키·같은 401 처리(꺼지면 사진 찾기도 같이).
    한 판(LookupRun)·한 시간 호출 수를 넘지 않고, 호출 사이엔 간격을 둔다. 429 면 잠깐 쉰다.
    일시 오류(429·5xx·시간 초과)와 한도로 못 찾은 것은 '없음'으로 기억하지 않는다 (last_no_match 가 False — 부르는 쪽이 다음 판에 다시)."""

    source = NAVER_SOURCE

    def __init__(
        self,
        search: NaverShopSearch,
        cfg: NaverReferenceConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.search = search
        self.cfg = cfg
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._next_at = 0.0
        self._pause_until = 0.0
        self._told_missing = False
        self.last_no_match = False  # 마지막 lookup 이 실제로 검색했는데 확실한 같은 상품이 없었나

    @property
    def usable(self) -> bool:
        """키가 있고 401 등으로 꺼지지 않았나. 키가 없으면 그 사실을 한 번만 로그 (값은 안 남김)."""
        if not self.search.configured:
            if not self._told_missing:
                self._told_missing = True
                log.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET not set — 쿠팡 딜의 네이버 최저가 비교는 꺼짐 (가격 이력으로만 판정)")
            return False
        return self.search.disabled_reason is None

    def new_run(self) -> LookupRun:
        return LookupRun(self.cfg.max_lookups_per_run, self.cfg.run_seconds, self._clock)

    def hour_available(self) -> bool:
        now = self._clock()
        while self._calls and now - self._calls[0] > 3600:
            self._calls.popleft()
        return len(self._calls) < self.cfg.max_lookups_per_hour

    def calls_last_hour(self) -> int:
        self.hour_available()
        return len(self._calls)

    async def _turn(self, run: LookupRun) -> bool:
        """이번 호출을 해도 되나: 한 판·한 시간 한도, 429 쉬는 중, 호출 간격(판의 남은 시간 안에서만 기다림)."""
        now = self._clock()
        if now < self._pause_until or not run.allows() or not self.hour_available():
            return False
        wait = self._next_at - now
        if wait > 0:
            if wait >= run.time_left():
                return False
            await self._sleep(wait)
        now = self._clock()
        run.calls += 1
        self._calls.append(now)
        self._next_at = now + self.cfg.min_interval_seconds
        return True

    async def lookup(self, product: Product, run: LookupRun) -> MarketQuote | None:
        """같은 상품의 네이버 최저가 (확실할 때만). 검색어는 꾸밈을 뺀 상품명, 결과가 하나도 없을 때만 앞 네 낱말로 한 번 더."""
        self.last_no_match = False
        if not product.has_price or not self.usable:
            return None
        items: list[dict[str, Any]] = []
        for query in search_queries(product.name)[:2]:
            if not await self._turn(run):
                run.skipped += 1
                log.debug("naver ref skipped (run/hour limit or pause): %s", product.name[:40])
                return None
            timeout = self.cfg.timeout_seconds
            try:
                items = await asyncio.wait_for(self.search._search(query, exclude=PRICE_EXCLUDE, timeout=timeout), timeout + 2)
            except _Unavailable:
                return None  # 키가 틀렸거나 API 가 닫힘: _search 가 한 번 경고하고 껐다
            except httpx.HTTPStatusError as e:
                run.failed += 1
                if e.response.status_code == 429:
                    self._pause_until = self._clock() + _PAUSE_ON_429
                    log.info("naver ref: HTTP 429 — 네이버 최저가 비교를 %d분 쉼", int(_PAUSE_ON_429 // 60))
                else:
                    log.info("naver ref failed for %s: HTTP %s", product.name[:40], e.response.status_code)
                return None
            except (httpx.HTTPError, ValueError, TimeoutError) as e:
                run.failed += 1
                log.info("naver ref failed for %s: %s", product.name[:40], type(e).__name__)
                return None
            if items:
                break
        quote, why = naver_lowest(product.name, product.price, items, self.cfg)
        if quote is None:
            run.no_match += 1
            self.last_no_match = True
            log.info("naver ref: no confident match for '%s' — %s (%d results)", product.name[:40], why, len(items))
            return None
        run.matched += 1
        log.info("naver ref for '%s' %s원 → 네이버 %s원 (%s, %s)", product.name[:40], f"{product.price:,}",
                 f"{quote.price:,}", why, quote.title[:40])
        return quote
