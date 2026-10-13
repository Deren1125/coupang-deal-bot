"""수집 때 가격 대조용 상품 페이지 읽기 (주인 결정 10/12 후속).

10/12 서버: 17:16 부터 네이버 스마트스토어·브랜드스토어 일반 요청이 모두 429 ('페이지를 못 읽음') → 네이버 딜은 하나도
대조하지 못했고(확인 못 함 207줄, 맞음 0·보류 0), 같은 상품이 5분마다 다시 찍혔다. 그래서
- 읽은 페이지(가격·옵션)는 상품(product_id)마다 cache_seconds 동안 기억한다. 그동안은 일반 요청도 크롬도 다시 하지 않는다.
  단 기억해 둔 페이지는 제목 가격이 맞는지(진행) 확인할 때만 쓰고, 보류는 그 페이지를 읽을 때 대조한 제목 가격만 되풀이한다.
  그사이 가게가 값을 내리고 새 글이 새 가격을 적었으면 옛 페이지로 보류하지 않고 새로 읽는다 (못 읽으면 '확인 못 함'으로 진행).
- 일반 요청이 막히면(429·403·쉬는 중·가격 없는 빈 페이지) 네이버 상품 페이지(스마트스토어·브랜드스토어·네이버 쇼핑 상품 페이지,
  블로그는 절대 아님)는 사진용으로 쓰던 그 크롬(store_browser)으로 읽는다. 위장하지 않는다: 창 없는 크롬 그대로, 막히면 가상 화면의
  보통 크롬 한 번. 한 번 읽을 때 크롬 하나를 띄우고 다 읽으면 닫는다. 띄우기부터 닫기까지 timeout 초를 넘으면 끊는다.
- 수집 한 판에 per_run 번, 한 시간에 per_hour 번까지. 크롬이 다른 페이지를 읽는 중이거나 열기 간격 안이면 기다리지 않는다
  (5분 수집이 오래 멈추지 않게). 이번에 못 읽은 딜은 '확인 못 함'으로 지금처럼 진행하고(올라감) 다음 판에 다시 본다.
  크롬으로 열었는데 못 읽은 상품(시간 초과·로그인 같은 다른 페이지로 넘어감 등)은 retry_seconds 뒤에 다시, 또 못 읽으면 두 배씩
  (최대 retry_max_seconds) — 한 상품이 매번 한도를 먹지 않게. 크롬도 막힌 상품은 한 번 더 길게: 크롬 쉬는 시간이 끝나면 다른 상품부터.
- 크롬도 막히면(429·403·캡차·빈 페이지) store_browser 가 크롬 읽기를 쉰다 (로그는 쉬기 시작할 때 한 번).
- '확인 못 함' 로그는 상품마다 log_every_seconds(하루)에 한 번, 이유와 함께.
- 네이버 블로그·카페 같은 쇼핑이 아닌 네이버 주소는 일반 요청으로도 크롬으로도 읽지 않는다 (남의 글, enrich.is_naver_off_shop).
보류 규칙은 그대로다 (page_price): 읽은 페이지의 어느 가격·옵션에도 제목 가격이 안 맞을 때만 보류. 못 읽으면 절대 보류하지 않는다.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from dealbot.enrich import (
    PageEnricher,
    PageMeta,
    is_naver_off_shop,
    is_naver_product_page,
    same_product_page,
)
from dealbot.store_browser import StoreBrowser

# 기억해 둔 페이지로는 보류가 되는 새 제목 가격이라 새로 읽으려 했는데 못 읽음 ('확인 못 함' 이유 끝에 붙임)
NEW_PRICE = "옛 페이지와 안 맞는 새 가격"


@dataclass(slots=True)
class RunBudget:
    """수집 한 판에 크롬으로 더 읽을 수 있는 횟수."""

    browser_left: int


@dataclass(slots=True)
class PageRead:
    """가격 대조용으로 읽은 페이지."""

    meta: PageMeta | None = None
    why: str = ""  # 가격을 못 읽은 이유 ('확인 못 함' 로그용, 읽었으면 '')
    source: str = ""  # page(일반 요청) | browser(크롬) | cache(기억해 둔 페이지)


@dataclass(slots=True)
class _Kept:
    """기억해 둔 페이지."""

    until: float  # 이 시각(_clock)까지
    meta: PageMeta
    # 이 페이지를 새로 읽고 대조한 제목 가격. 기억해 둔 페이지로는 맞음(진행)만 확인하고, 보류는 이 가격들만 되풀이한다
    prices: set[int] = field(default_factory=set)


def same_page(url: str, meta: PageMeta) -> bool:
    """읽은 페이지가 그 상품 페이지인지. 다른 곳(스토어 첫 화면·로그인·다른 상품)으로 넘어갔으면 거기 가격은 이 상품 값이 아니다."""
    return same_product_page(url, meta.final_url)


class PageReader:
    def __init__(
        self,
        *,
        browser: bool = False,
        per_run: int = 1,
        per_hour: int = 12,
        timeout: float = 40.0,
        cache_seconds: float = 7200.0,
        retry_seconds: float = 1800.0,
        retry_max_seconds: float = 6 * 3600.0,
        log_every_seconds: float = 86400.0,
    ) -> None:
        self.browser = browser  # 일반 요청이 막힌 네이버 상품 페이지를 크롬으로 (꺼져 있으면 예전처럼 일반 요청만)
        self.per_run = per_run
        self.per_hour = per_hour
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.retry_seconds = retry_seconds
        self.retry_max = max(retry_seconds, retry_max_seconds)
        self.log_every = log_every_seconds
        self._cache: dict[str, _Kept] = {}  # 상품 → 읽은 페이지
        self._retry: dict[str, tuple[float, int]] = {}  # 상품 → (크롬으로 다시 읽어도 되는 시각, 연달아 못 읽은 횟수)
        self._opens: deque[float] = deque()  # 최근 한 시간 크롬을 띄운 시각
        self._logged: dict[str, float] = {}  # 상품 → '확인 못 함' 로그를 남긴 시각
        self._clock = time.monotonic  # 테스트에서 가짜로 바꿔 끼움

    def run_budget(self) -> RunBudget:
        return RunBudget(self.per_run)

    def kept(
        self, key: str, title_price: int | None = None, holds: Callable[[PageMeta], bool] | None = None
    ) -> tuple[PageMeta | None, bool]:
        """(기억해 둔 페이지, 새로 읽어야 함). 없거나 오래됐으면 (None, False).
        holds(그 페이지로 이 딜이 보류되는지)를 주면: 보류가 되는데 제목 가격이 그 페이지를 읽을 때 대조한 가격이 아니면 (None, True).
        그사이 가게가 값을 내렸을 수 있다 (타임세일 등) — 옛 페이지로 새 가격을 보류하면 진짜 싼 딜을 놓친다.
        맞음(진행)·같은 제목 가격의 보류·가격 없는 페이지는 기억해 둔 그대로 쓴다."""
        hit = self._cache.get(key)
        if hit is None or hit.until <= self._clock():
            return None, False
        if holds is not None and title_price not in hit.prices and holds(hit.meta):
            return None, True
        return hit.meta, False

    def _remember(self, key: str, meta: PageMeta, title_price: int | None = None) -> None:
        """읽은 페이지를 기억한다. title_price: 이 페이지를 새로 읽어 대조한 제목 가격 (그 가격의 보류만 기억해 둔 페이지로 되풀이)."""
        if self.cache_seconds <= 0:
            return
        now = self._clock()
        if len(self._cache) >= 500:
            self._cache = {k: v for k, v in self._cache.items() if v.until > now}
            while len(self._cache) >= 500:
                self._cache.pop(next(iter(self._cache)))
        prices = {title_price} if title_price is not None else set()
        old = self._cache.get(key)
        if old is not None and old.meta.prices == meta.prices:
            prices |= old.prices  # 값이 그대로인 같은 페이지: 전에 대조한 가격의 보류도 그대로 (새로 읽기를 오가지 않게)
        self._cache[key] = _Kept(now + self.cache_seconds, meta, prices)

    def should_log(self, key: str) -> bool:
        """이 상품의 '확인 못 함' 을 지금 로그에 남길지 (상품마다 log_every_seconds 에 한 번)."""
        now = self._clock()
        last = self._logged.get(key)
        if last is not None and now - last < self.log_every:
            return False
        if len(self._logged) >= 1000:
            self._logged = {k: v for k, v in self._logged.items() if now - v < self.log_every}
        self._logged[key] = now
        return True

    async def read(
        self,
        key: str,
        url: str,
        budget: RunBudget,
        *,
        enricher: PageEnricher,
        browser: StoreBrowser,
        title_price: int | None = None,
        holds: Callable[[PageMeta], bool] | None = None,
    ) -> PageRead:
        """가격 대조용으로 상품 페이지를 읽는다: 기억해 둔 페이지 → 일반 요청 → (네이버 상품 페이지면) 크롬.
        title_price·holds 를 주면 기억해 둔 페이지(여기·일반 요청·크롬 것 모두)로는 새 제목 가격을 보류하지 않는다 (kept):
        그땐 새로 읽고(한도 안에서), 못 읽으면 '확인 못 함' — 절대 보류하지 않음."""
        if is_naver_off_shop(url):
            # 네이버 블로그·카페 등 (쇼핑 아님): 일반 요청으로도 크롬으로도 안 읽음 — 남의 글
            return PageRead(why="네이버 쇼핑 페이지가 아님 (블로그·카페 등은 안 읽음)", source="page")
        hit, recheck = self.kept(key, title_price, holds)
        if hit is not None:
            return PageRead(hit, source="cache")
        if recheck:
            # 일반 요청(5분)·크롬(30분)이 잠깐 기억해 둔 같은 페이지도 버리고 지금 페이지로
            enricher.forget(url)
            browser.forget(url)
        elif holds is not None and (seen := browser.cached(url)) is not None and holds(seen):
            browser.forget(url)  # 크롬이 사진용으로 읽어 둔(최대 30분) 페이지도 이 제목 가격을 대조해 읽은 게 아니다
        got = await self._read(key, url, budget, enricher=enricher, browser=browser, title_price=title_price)
        if recheck and got.why:
            got.why = f"{got.why} ({NEW_PRICE})"
        return got

    async def _read(
        self, key: str, url: str, budget: RunBudget, *, enricher: PageEnricher, browser: StoreBrowser, title_price: int | None
    ) -> PageRead:
        plain = await enricher.fetch(url)
        head = "페이지를 못 읽음"
        if plain is not None and not same_page(url, plain):
            plain, head = None, "다른 페이지로 넘어감"
        if plain is not None and plain.prices is not None:
            self._remember(key, plain, title_price)
            return PageRead(plain, source="page")
        if plain is not None:
            head = "페이지에 가격 없음"
        if not self.browser or not is_naver_product_page(url):
            # 크롬으로 더 볼 페이지가 아님 (토스 등) 또는 크롬 대조 꺼짐: 예전처럼. 가격 없이 읽은 페이지도 다음 판에 같으니 기억
            if plain is not None and not is_naver_product_page(url):
                self._remember(key, plain, title_price)
            return PageRead(plain, why="" if plain is not None else head, source="page")
        status = enricher.failed_status(url)
        if status in (404, 410):
            return PageRead(plain, why=f"{head} (HTTP {status})", source="page")  # 없는 상품 — 크롬으로 봐도 같음
        skip = self._browser_skip(key, url, budget, browser)
        if skip:
            return PageRead(plain, why=f"{head} · {skip}", source="page")
        got = await browser.read(url, wait=False, timeout=self.timeout)
        now = self._clock()
        if got.opened:
            budget.browser_left -= 1
            self._opens.append(now)
        if got.meta is not None and same_page(url, got.meta):
            # 가격이 없어도 기억 (구조가 달라 못 읽은 페이지는 다시 열어도 같음). 크롬이 기억해 둔 페이지는 이번에 대조해 읽은 게 아님
            self._remember(key, got.meta, title_price if got.opened else None)
            self._retry.pop(key, None)
            return PageRead(got.meta, source="browser")
        if got.opened:
            self._failed(key, now, blocked=got.blocked)
        why = got.why if got.meta is None else "다른 페이지로 넘어감"
        if not why.startswith("크롬"):
            why = f"크롬 {why}"
        return PageRead(plain, why=f"{head} · {why}", source="page")

    def _failed(self, key: str, now: float, *, blocked: bool) -> None:
        """크롬으로 열었는데 못 읽은 상품: retry_seconds 뒤에 다시, 또 못 읽으면 두 배씩 (최대 retry_max).
        크롬도 막힌 상품(blocked)은 처음부터 두 배 — 크롬 쉬는 시간(cooldown)보다 길게 쉬어, 쉬는 시간이 끝나면 다른 상품을 먼저 읽는다
        (한 상품이 열 때마다 막혀 크롬 전체를 계속 쉬게 하지 않게)."""
        prev = self._retry.get(key)
        strikes = prev[1] + 1 if prev is not None and now - prev[0] < self.retry_max else 1  # 오래전 일은 처음부터 셈
        delay = min(self.retry_max, self.retry_seconds * 2 ** min(strikes - (0 if blocked else 1), 16))
        if key not in self._retry and len(self._retry) >= 500:
            # 오래전에 풀린 상품부터 버린다 (다시 못 읽어도 처음부터 셀 뿐)
            self._retry = {k: v for k, v in self._retry.items() if v[0] + self.retry_max > now}
            while len(self._retry) >= 500:
                self._retry.pop(next(iter(self._retry)))
        self._retry[key] = (now + delay, strikes)

    def _browser_skip(self, key: str, url: str, budget: RunBudget, browser: StoreBrowser) -> str:
        """지금 크롬으로 안 읽는 이유. 읽어도 되면 ''."""
        if not browser.enabled:
            return "크롬 꺼짐"
        if browser.disabled_reason:
            return f"크롬 없음 ({browser.disabled_reason})"
        if browser.cached(url) is not None:
            return ""  # 크롬이 방금 읽어 둔 페이지 (띄우지 않음)
        if left := browser.cooling():
            return f"크롬도 막혀 쉬는 중 ({left / 60:.0f}분 남음)"
        now = self._clock()
        retry = self._retry.get(key, (0.0, 0))[0] - now
        if retry > 0:
            return f"크롬으로 못 읽은 지 얼마 안 됨 ({retry / 60:.0f}분 뒤 다시)"
        if budget.browser_left <= 0:
            return "이번 판 크롬 한도"
        while self._opens and now - self._opens[0] >= 3600:
            self._opens.popleft()
        if len(self._opens) >= self.per_hour:
            return f"한 시간 크롬 한도 ({self.per_hour}번)"
        return ""
