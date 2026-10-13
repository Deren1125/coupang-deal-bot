"""수집 때 가격 대조용 상품 페이지 읽기 (주인 결정 10/12 후속).

10/12 서버: 17:16 부터 네이버 스마트스토어·브랜드스토어 일반 요청이 모두 429 ('페이지를 못 읽음') → 네이버 딜은 하나도
대조하지 못했고(확인 못 함 207줄, 맞음 0·보류 0), 같은 상품이 5분마다 다시 찍혔다. 그래서
- 읽은 페이지(가격·옵션)는 상품(product_id)마다 cache_seconds 동안 기억한다. 그동안은 일반 요청도 크롬도 다시 하지 않는다.
- 일반 요청이 막히면(429·403·쉬는 중·가격 없는 빈 페이지) 네이버 상품 페이지(스마트스토어·브랜드스토어·네이버 쇼핑 상품 페이지,
  블로그는 절대 아님)는 사진용으로 쓰던 그 크롬(store_browser)으로 읽는다. 위장하지 않는다: 창 없는 크롬 그대로, 막히면 가상 화면의
  보통 크롬 한 번. 한 번 읽을 때 크롬 하나를 띄우고 다 읽으면 닫는다. 띄우기부터 닫기까지 timeout 초를 넘으면 끊는다.
- 수집 한 판에 per_run 번, 한 시간에 per_hour 번까지. 크롬이 다른 페이지를 읽는 중이거나 열기 간격 안이면 기다리지 않는다
  (5분 수집이 오래 멈추지 않게). 이번에 못 읽은 딜은 '확인 못 함'으로 지금처럼 진행하고(올라감) 다음 판에 다시 본다.
  크롬으로 열었는데 못 읽은 상품(시간 초과 등)은 retry_seconds 뒤에 다시 — 한 상품이 매번 한도를 먹지 않게.
- 크롬도 막히면(429·403·캡차·빈 페이지) store_browser 가 크롬 읽기를 쉰다 (로그는 쉬기 시작할 때 한 번).
- '확인 못 함' 로그는 상품마다 log_every_seconds(하루)에 한 번, 이유와 함께.
보류 규칙은 그대로다 (page_price): 읽은 페이지의 어느 가격·옵션에도 제목 가격이 안 맞을 때만 보류. 못 읽으면 절대 보류하지 않는다.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

from dealbot.enrich import PageEnricher, PageMeta, is_naver_product_page, is_product_page
from dealbot.store_browser import StoreBrowser

_PRODUCT_NO = re.compile(r"/products/(\d+)")


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


def same_page(url: str, meta: PageMeta) -> bool:
    """읽은 페이지가 그 상품 페이지인지. 다른 곳(스토어 첫 화면·로그인·다른 상품)으로 넘어갔으면 거기 가격은 이 상품 값이 아니다."""
    final = meta.final_url
    if not final or final == url:
        return True
    if not is_product_page(final):
        return False
    a, b = _PRODUCT_NO.search(url), _PRODUCT_NO.search(final)
    return not (a and b and a.group(1) != b.group(1))


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
        log_every_seconds: float = 86400.0,
    ) -> None:
        self.browser = browser  # 일반 요청이 막힌 네이버 상품 페이지를 크롬으로 (꺼져 있으면 예전처럼 일반 요청만)
        self.per_run = per_run
        self.per_hour = per_hour
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self.retry_seconds = retry_seconds
        self.log_every = log_every_seconds
        self._cache: dict[str, tuple[float, PageMeta]] = {}  # 상품 → (언제까지, 읽은 페이지)
        self._retry_at: dict[str, float] = {}  # 상품 → 크롬으로 다시 읽어도 되는 시각
        self._opens: deque[float] = deque()  # 최근 한 시간 크롬을 띄운 시각
        self._logged: dict[str, float] = {}  # 상품 → '확인 못 함' 로그를 남긴 시각
        self._clock = time.monotonic  # 테스트에서 가짜로 바꿔 끼움

    def run_budget(self) -> RunBudget:
        return RunBudget(self.per_run)

    def cached(self, key: str) -> PageMeta | None:
        hit = self._cache.get(key)
        return hit[1] if hit is not None and hit[0] > self._clock() else None

    def _remember(self, key: str, meta: PageMeta) -> None:
        if self.cache_seconds <= 0:
            return
        now = self._clock()
        if len(self._cache) >= 500:
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            while len(self._cache) >= 500:
                self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (now + self.cache_seconds, meta)

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

    async def read(self, key: str, url: str, budget: RunBudget, *, enricher: PageEnricher, browser: StoreBrowser) -> PageRead:
        """가격 대조용으로 상품 페이지를 읽는다: 기억해 둔 페이지 → 일반 요청 → (네이버 상품 페이지면) 크롬."""
        hit = self.cached(key)
        if hit is not None:
            return PageRead(hit, source="cache")
        plain = await enricher.fetch(url)
        head = "페이지를 못 읽음"
        if plain is not None and not same_page(url, plain):
            plain, head = None, "다른 페이지로 넘어감"
        if plain is not None and plain.prices is not None:
            self._remember(key, plain)
            return PageRead(plain, source="page")
        if plain is not None:
            head = "페이지에 가격 없음"
        if not self.browser or not is_naver_product_page(url):
            # 크롬으로 더 볼 페이지가 아님 (토스 등) 또는 크롬 대조 꺼짐: 예전처럼. 가격 없이 읽은 페이지도 다음 판에 같으니 기억
            if plain is not None and not is_naver_product_page(url):
                self._remember(key, plain)
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
            self._remember(key, got.meta)  # 가격이 없어도 기억 (구조가 달라 못 읽은 페이지는 다시 열어도 같음)
            self._retry_at.pop(key, None)
            return PageRead(got.meta, source="browser")
        if got.opened and not got.blocked:
            self._retry_at[key] = now + self.retry_seconds
        why = got.why if got.meta is None else "다른 페이지로 넘어감"
        if not why.startswith("크롬"):
            why = f"크롬 {why}"
        return PageRead(plain, why=f"{head} · {why}", source="page")

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
        retry = self._retry_at.get(key, 0.0) - now
        if retry > 0:
            return f"크롬으로 못 읽은 지 얼마 안 됨 ({retry / 60:.0f}분 뒤 다시)"
        if budget.browser_left <= 0:
            return "이번 판 크롬 한도"
        while self._opens and now - self._opens[0] >= 3600:
            self._opens.popleft()
        if len(self._opens) >= self.per_hour:
            return f"한 시간 크롬 한도 ({self.per_hour}번)"
        return ""
