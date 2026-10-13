"""일반 요청이 막힌 네이버 스토어 페이지를 실제 크롬으로 한 번 열어 상품 사진(JSON-LD·og:image)을 읽는다.

주인 결정 (10/10): 발행 직전에, 사진이 없는 네이버 스토어 딜만 한 번.
이 서버에서 스토어는 일반 요청(httpx)엔 거의 늘 429 지만 실제 크롬으로는 열린다 (10/10: 200, og:image ?type=o1000).
- 로그인 없는 빈 창만 쓴다 (프로필 폴더 없음 — 블로그 봇의 네이버 로그인 프로필과 섞이지 않게).
- 한 번에 하나, 열기 사이 min_interval 초. 서버 여유 메모리가 min_free_mb 보다 적으면 열지 않는다 (블로그 크롬이 떠 있을 때 등).
- 사진·글꼴·동영상은 받지 않는다 (사진 주소는 HTML 메타에 있음, 메모리·전송량 절약). 다 읽으면 바로 닫는다.
- 먼저 창 없는 크롬. 브라우저 이름(HeadlessChrome)·실행 방식은 그대로 둔다 — 위장(stealth)하지 않음.
  그게 막히면(403·429) 서버 가상 화면(Xvfb :99)에 창을 띄운 보통 크롬으로 한 번 더 — 단 다른 크롬(블로그 봇)이 떠 있지
  않을 때만. 같은 화면에 창이 뜨면 블로그 봇 글쓰기(포커스·클립보드 붙여넣기)를 건드릴 수 있어서.
  그래도 막히면 사진은 네이버 쇼핑 검색 API 로 찾는다 (app.deal_photo).
playwright 나 크롬이 없으면 한 번 알리고 다시 시작할 때까지 건너뛴다.

수집 때 가격 대조(주인 결정 10/12 후속, page_reader)도 같은 크롬을 쓴다: 일반 요청이 막힌 네이버 상품 페이지를 같은 방식으로
한 번 읽는다 (read). 그쪽은 차례를 기다리지 않고(wait=False) 띄우기부터 닫기까지 시간 제한을 짧게 준다.
크롬도 막히면(429·403·캡차·빈 페이지) cooldown 초 동안 크롬으로 안 읽는다 (사진용도 같이. 또 막히면 두 배씩, 최대 cooldown_max).
로그는 쉬기 시작할 때 한 번.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dealbot.browser.session import find_chromium_executable
from dealbot.enrich import (
    PageMeta,
    is_naver_product_page,
    is_naver_store,
    page_images,
    page_meta_at,
)

log = logging.getLogger(__name__)

XVFB_SOCKET = Path("/tmp/.X11-unix/X99")  # 서버 가상 화면 (블로그 봇이 띄워 둔 것)
_SKIP_TYPES = ("image", "media", "font")
_CAPTCHA = re.compile(r"captcha|자동\s*입력\s*방지|보안\s*(?:확인|문자)", re.I)


class _Unavailable(Exception):
    """playwright·크롬이 없음 → 다시 시작할 때까지 안 씀."""


@dataclass(slots=True)
class BrowserPage:
    """크롬으로 한 번 읽은 결과 (StoreBrowser.read)."""

    meta: PageMeta | None = None
    status: int = 0  # 응답 코드 (안 열었거나 실패면 0)
    opened: bool = False  # 이번에 크롬을 실제로 띄움 (기억해 둔 페이지를 줬거나 안 열었으면 False)
    blocked: bool = False  # 크롬도 막힘 (429·403·캡차·빈 페이지) → 크롬 읽기를 쉬기 시작함
    why: str = ""  # 못 읽은 이유 (meta 가 None 일 때)


def blocked_reason(status: int, final_url: str, html: str, meta: PageMeta | None) -> str:
    """크롬으로 연 페이지가 막혔는지: 429·403, 캡차 화면, 빈 페이지(응답은 200 인데 상품 가격도 상품 사진도 없음).
    막혔으면 이유, 아니면 ''. 다른 오류(404·5xx·응답 없음)는 막힌 게 아니다."""
    if status in (403, 429):
        return f"HTTP {status}"
    if status != 200:
        return ""
    if meta is not None and (meta.prices is not None or page_images(meta)):
        return ""
    return "캡차" if _CAPTCHA.search(final_url) or _CAPTCHA.search(html[:50000]) else "빈 페이지"


def other_chrome_running(proc: str = "/proc") -> bool:
    """다른 크롬 브라우저(블로그 봇 등)가 떠 있는지: 명령줄에 크롬과 --user-data-dir 이 있는 살아 있는 프로세스.
    (닫힌 크롬의 좀비는 명령줄이 비어 있어 안 셈.) 못 읽으면 True — 모르면 화면에 창을 띄우지 않는다."""
    try:
        pids = [d for d in os.listdir(proc) if d.isdigit()]
    except OSError:
        return True
    for pid in pids:
        try:
            cmd = Path(proc, pid, "cmdline").read_bytes()
        except OSError:
            continue
        if b"--user-data-dir=" in cmd and b"chrom" in cmd.lower():
            return True
    return False


def mem_available_mb(path: str = "/proc/meminfo") -> int | None:
    """서버 여유 메모리(MemAvailable, MB). 모르면 None."""
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class StoreBrowser:
    def __init__(
        self,
        *,
        enabled: bool = True,
        min_interval: float = 60.0,
        min_free_mb: int = 500,
        timeout: float = 45.0,
        executable_path: str | None = None,
        cache_seconds: float = 1800.0,
        cooldown_seconds: float = 1800.0,
        cooldown_max_seconds: float = 3600.0,
    ) -> None:
        self.enabled = enabled
        # 크롬도 막히면 쉬는 시간 (연달아 막히면 두 배씩, 최대 cooldown_max. 0 = 안 쉼)
        self.cooldown = cooldown_seconds
        self.cooldown_max = max(cooldown_seconds, cooldown_max_seconds)
        self._cool_until = 0.0  # 이 시각(_clock 기준)까지 크롬으로 안 읽음
        self._cool_strikes = 0  # 연달아 막힌 횟수 (막히지 않은 페이지를 한 번 읽으면 처음으로)
        # 읽은 페이지는 잠시 기억: 쇼핑커넥트에 묻기 전 가격 대조로 연 페이지를 발행 때 사진용으로 다시 열지 않게
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, PageMeta]] = {}
        self.min_interval = min_interval
        self.min_free_mb = min_free_mb
        self.timeout = timeout
        self.executable_path = executable_path
        self.disabled_reason: str | None = None
        self._lock = asyncio.Lock()
        self._next = 0.0  # 다음에 열어도 되는 시각 (_clock 기준)
        self._sleep = asyncio.sleep  # 테스트에서 가짜로 바꿔 끼움
        self._clock = time.monotonic
        self._mem = mem_available_mb

    def cached(self, url: str) -> PageMeta | None:
        """잠시 기억해 둔, 크롬으로 읽은 페이지. 없거나 오래됐으면 None."""
        hit = self._cache.get(url)
        return hit[1] if hit is not None and hit[0] > self._clock() else None

    def forget(self, url: str) -> None:
        """잠시 기억해 둔 페이지를 버린다 (가격 대조가 지금 페이지를 다시 읽어야 할 때)."""
        self._cache.pop(url, None)

    def cooling(self) -> float:
        """크롬도 막혀 쉬는 중이면 남은 초, 아니면 0."""
        return max(0.0, self._cool_until - self._clock())

    def busy(self) -> bool:
        """지금 바로 못 엶: 다른 페이지를 읽는 중이거나 열기 간격(min_interval) 안."""
        return self._lock.locked() or self._next > self._clock()

    async def page_meta(self, url: str) -> PageMeta | None:
        """스토어 상품 페이지를 크롬으로 열어 읽은 메타 (발행 직전 사진용). 스토어 주소가 아니거나, 꺼졌거나, 못 열면 None."""
        if not is_naver_store(url):
            return None
        return (await self.read(url)).meta

    async def read(self, url: str, *, wait: bool = True, timeout: float | None = None) -> BrowserPage:
        """네이버 상품 페이지를 크롬으로 한 번 읽는다: 크롬 하나를 띄워 읽고 바로 닫는다 (페이지·창 모두).
        wait=False 면 다른 페이지를 읽는 중이거나 열기 간격 안일 때 기다리지 않고 안 엶 (수집 중 가격 대조 — 5분 수집이 안 멈추게).
        timeout: 띄우기부터 닫기까지의 시간 제한 (없으면 페이지 시간 제한 + 30초). 넘으면 끊고 닫는다."""
        if not self.enabled:
            return BrowserPage(why="꺼짐")
        if self.disabled_reason:
            return BrowserPage(why=self.disabled_reason)
        if not is_naver_product_page(url):
            return BrowserPage(why="네이버 상품 페이지 아님")
        hit = self.cached(url)
        if hit is not None:
            return BrowserPage(meta=hit, status=200)
        if self.cooling():
            return BrowserPage(why=self._cool_note())
        if not wait and self.busy():
            return BrowserPage(why="다른 페이지를 읽는 중이거나 열기 간격 안")
        async with self._lock:
            pause = self._next - self._clock()
            if pause > 0:
                await self._sleep(pause)
            if self.cooling():  # 기다리는 사이 다른 읽기가 막혔으면 이번엔 안 엶
                return BrowserPage(why=self._cool_note())
            free = self._mem()
            if free is not None and free < self.min_free_mb:
                log.info("store browser: 여유 메모리 %dMB < %dMB — 이번엔 안 엶 %s", free, self.min_free_mb, url)
                return BrowserPage(why=f"여유 메모리 {free}MB")
            started = self._clock()
            try:
                # 페이지 안 시간 제한(goto)이 먼저 걸리고, 이건 크롬이 멈췄을 때의 마지막 안전장치 (가격 대조는 더 짧게)
                status, final_url, html = await asyncio.wait_for(self._open_page(url), timeout or self.timeout + 30)
            except _Unavailable as e:
                self.disabled_reason = str(e)
                log.warning("store browser: 쓸 수 없음 — %s (다시 시작할 때까지 끔)", e)
                return BrowserPage(why=str(e))
            except Exception as e:  # noqa: BLE001 — 사진·가격 대조 하나 때문에 수집·발행이 멈추면 안 됨
                log.info("store browser failed for %s: %s %s", url, type(e).__name__, str(e).splitlines()[0][:120] if str(e) else "")
                return BrowserPage(opened=True, why=f"실패 ({type(e).__name__})")
            finally:
                self._next = self._clock() + self.min_interval  # 간격은 닫은 뒤부터
        took = self._clock() - started
        meta = page_meta_at(html, final_url or url) if status == 200 and html else None
        blocked = blocked_reason(status, final_url or url, html, meta)
        if blocked:
            self._block(blocked, url)
            return BrowserPage(status=status, opened=True, blocked=True, why=f"크롬도 막힘 ({blocked})")
        if meta is None:
            log.info("store browser: HTTP %s for %s (%.0f초)", status, url, took)
            return BrowserPage(status=status, opened=True, why=f"HTTP {status}")
        self._cool_strikes = 0
        log.info("store browser: %s → 사진 %s (%.0f초)", url, (meta.ld_image or meta.image or "없음")[:120], took)
        if self.cache_seconds > 0:
            now = self._clock()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[url] = (now + self.cache_seconds, meta)
        return BrowserPage(meta=meta, status=status, opened=True)

    def _block(self, why: str, url: str) -> None:
        """크롬도 막힘: 한동안 크롬으로 안 읽는다 (연달아 막히면 두 배씩, 최대 cooldown_max). 로그는 쉬기 시작할 때 한 번."""
        self._cool_strikes += 1
        pause = min(self.cooldown_max, self.cooldown * 2 ** min(self._cool_strikes - 1, 16))
        if pause <= 0:
            log.info("store browser: 크롬도 막힘 (%s) %s", why, url)
            return
        self._cool_until = self._clock() + pause
        log.warning("store browser: 크롬도 막힘 (%s) — %.0f분 동안 크롬으로 안 읽음 %s", why, pause / 60, url)

    def _cool_note(self) -> str:
        return f"크롬도 막혀 쉬는 중 ({self.cooling() / 60:.0f}분 남음)"

    async def _open_page(self, url: str) -> tuple[int, str, str]:
        """(응답 코드, 최종 주소, HTML). playwright·크롬이 없으면 _Unavailable."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise _Unavailable("playwright 미설치") from e
        async with async_playwright() as pw:
            return await self._visit(pw, url)

    async def _visit(self, pw: Any, url: str) -> tuple[int, str, str]:
        """창 없는 크롬 → 막히면(403·429) 가상 화면 크롬 한 번 (다른 크롬이 떠 있지 않을 때만)."""
        got = await self._load(pw, url, headless=True)
        if got[0] in (403, 429) and XVFB_SOCKET.exists():
            if other_chrome_running():
                log.info("store browser: 창 없는 크롬이 막힘(%s) — 다른 크롬(블로그 봇)이 떠 있어 화면 크롬은 안 띄움", got[0])
            else:
                log.info("store browser: 창 없는 크롬이 막힘(%s) — 가상 화면 크롬으로 한 번 더", got[0])
                got = await self._load(pw, url, headless=False)
        return got

    async def _load(self, pw: Any, url: str, *, headless: bool) -> tuple[int, str, str]:
        # 브라우저 이름(user agent)·채널은 건드리지 않는다: 창 없는 크롬은 HeadlessChrome 그대로 (위장 금지)
        kwargs: dict[str, Any] = {"headless": headless, "args": ["--no-first-run", "--disable-dev-shm-usage"]}
        if not headless:
            kwargs["env"] = {**os.environ, "DISPLAY": ":99"}
        browser = await self._launch(pw, **kwargs)
        try:
            ctx = await browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900})
            await ctx.route("**/*", _skip_heavy)
            page = await ctx.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            await page.wait_for_timeout(1500)  # 스크립트가 JSON-LD 를 채울 시간
            return (resp.status if resp is not None else 0), page.url, await page.content()
        finally:
            # 창을 닫으면 페이지도 같이 닫힌다. 시간 제한으로 끊겨도 꼭 닫고, 닫기가 멈추면 playwright 를 끝낼 때 정리된다
            with contextlib.suppress(Exception):
                await asyncio.wait_for(browser.close(), 15)

    async def _launch(self, pw: Any, **kwargs: Any) -> Any:
        """playwright 에 딸린 크롬 → 없으면 설치된 다른 크로미움(서버: 블로그 봇이 설치한 것)."""
        try:
            if self.executable_path:
                return await pw.chromium.launch(executable_path=self.executable_path, **kwargs)
            return await pw.chromium.launch(**kwargs)
        except Exception as first:  # noqa: BLE001
            if "executable" not in str(first).lower():  # "Executable doesn't exist at …" 말고(메모리 등)는 이번만 실패
                raise
            exe = find_chromium_executable()
            if not exe or exe == self.executable_path:
                raise _Unavailable(f"크롬 실행 파일 없음 ({str(first).splitlines()[0][:80]})") from first
            log.info("store browser: playwright 기본 크롬이 없어 %s 로 엶", exe)
            return await pw.chromium.launch(executable_path=exe, **kwargs)


async def _skip_heavy(route: Any) -> None:
    if route.request.resource_type in _SKIP_TYPES:
        await route.abort()
    else:
        await route.continue_()


def store_browser_check() -> tuple[bool | None, str]:
    """시작 점검 한 줄: playwright·크롬이 있는지만 본다 (크롬을 띄우지는 않음)."""
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        return None, "네이버 스토어 사진·가격 브라우저: playwright 미설치 — 막힌(429) 네이버 딜은 사진·가격 확인 없이"
    exe = find_chromium_executable()
    if not exe:
        return None, "네이버 스토어 사진·가격 브라우저: 크롬 실행 파일을 못 찾음 — 막힌(429) 네이버 딜은 사진·가격 확인 없이"
    where = next((part for part in Path(exe).parts if part.startswith("chromium")), Path(exe).name)
    return True, f"네이버 스토어 사진·가격 브라우저 준비 ({where})"
