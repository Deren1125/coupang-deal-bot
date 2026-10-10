"""일반 요청이 막힌 네이버 스토어 페이지를 실제 크롬으로 한 번 열어 상품 사진(JSON-LD·og:image)을 읽는다.

주인 결정 (10/10): 발행 직전에, 사진이 없는 네이버 스토어 딜만 한 번.
이 서버에서 스토어는 일반 요청(httpx)엔 거의 늘 429 지만 실제 크롬으로는 열린다 (10/10: 200, og:image ?type=o1000).
- 로그인 없는 빈 창만 쓴다 (프로필 폴더 없음 — 블로그 봇의 네이버 로그인 프로필과 섞이지 않게).
- 한 번에 하나, 열기 사이 min_interval 초. 서버 여유 메모리가 min_free_mb 보다 적으면 열지 않는다 (블로그 크롬이 떠 있을 때 등).
- 사진·글꼴·동영상은 받지 않는다 (사진 주소는 HTML 메타에 있음, 메모리·전송량 절약). 다 읽으면 바로 닫는다.
- 먼저 창 없는 크롬(새 headless — 진짜 크롬과 같은 본체, 브라우저 이름에서 'Headless' 를 뺌). 그게 막히면(403·429)
  서버 가상 화면(Xvfb :99)에 창을 띄워 한 번 더 — 단 다른 크롬(블로그 봇)이 떠 있지 않을 때만. 같은 화면에 창이 뜨면
  블로그 봇 글쓰기(포커스·클립보드 붙여넣기)를 건드릴 수 있어서.
playwright 나 크롬이 없으면 한 번 알리고 다시 시작할 때까지 건너뛴다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from dealbot.browser.session import find_chromium_executable
from dealbot.enrich import PageMeta, is_naver_store, page_meta_at

log = logging.getLogger(__name__)

XVFB_SOCKET = Path("/tmp/.X11-unix/X99")  # 서버 가상 화면 (블로그 봇이 띄워 둔 것)
_SKIP_TYPES = ("image", "media", "font")


class _Unavailable(Exception):
    """playwright·크롬이 없음 → 다시 시작할 때까지 안 씀."""


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
    ) -> None:
        self.enabled = enabled
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

    async def page_meta(self, url: str) -> PageMeta | None:
        """스토어 상품 페이지를 크롬으로 열어 읽은 메타. 스토어 주소가 아니거나, 꺼졌거나, 못 열면 None."""
        if not self.enabled or self.disabled_reason or not is_naver_store(url):
            return None
        async with self._lock:
            wait = self._next - self._clock()
            if wait > 0:
                await self._sleep(wait)
            free = self._mem()
            if free is not None and free < self.min_free_mb:
                log.info("store browser: 여유 메모리 %dMB < %dMB — 이번엔 안 엶 %s", free, self.min_free_mb, url)
                return None
            started = self._clock()
            try:
                # 페이지 안 시간 제한(goto)이 먼저 걸리고, 이건 크롬이 멈췄을 때의 마지막 안전장치
                status, final_url, html = await asyncio.wait_for(self._open_page(url), self.timeout + 30)
            except _Unavailable as e:
                self.disabled_reason = str(e)
                log.warning("store browser: 쓸 수 없음 — %s (다시 시작할 때까지 끔)", e)
                return None
            except Exception as e:  # noqa: BLE001 — 사진 하나 때문에 발행이 멈추면 안 됨
                log.info("store browser failed for %s: %s %s", url, type(e).__name__, str(e).splitlines()[0][:120] if str(e) else "")
                return None
            finally:
                self._next = self._clock() + self.min_interval  # 간격은 닫은 뒤부터
        took = self._clock() - started
        if status != 200 or not html:
            log.info("store browser: HTTP %s for %s (%.0f초)", status, url, took)
            return None
        meta = page_meta_at(html, final_url or url)
        log.info("store browser: %s → 사진 %s (%.0f초)", url, (meta.ld_image or meta.image or "없음")[:120], took)
        return meta

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
        kwargs: dict[str, Any] = {"headless": headless, "args": ["--no-first-run", "--disable-dev-shm-usage"]}
        if headless:
            kwargs["channel"] = "chromium"  # 새 headless (진짜 크롬 본체). 기본값은 예전 headless 셸이라 티가 남
        else:
            kwargs["env"] = {**os.environ, "DISPLAY": ":99"}
        browser = await self._launch(pw, **kwargs)
        try:
            ua = None
            if headless:
                probe = await browser.new_page()
                ua = str(await probe.evaluate("navigator.userAgent")).replace("HeadlessChrome", "Chrome")
                await probe.close()
            ctx = await browser.new_context(
                locale="ko-KR",
                timezone_id="Asia/Seoul",
                viewport={"width": 1280, "height": 900},
                **({"user_agent": ua} if ua else {}),
            )
            await ctx.route("**/*", _skip_heavy)
            page = await ctx.new_page()
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            await page.wait_for_timeout(1500)  # 스크립트가 JSON-LD 를 채울 시간
            return (resp.status if resp is not None else 0), page.url, await page.content()
        finally:
            await browser.close()

    async def _launch(self, pw: Any, **kwargs: Any) -> Any:
        """playwright 에 딸린 크롬 → 없으면 설치된 다른 크로미움(서버: 블로그 봇이 설치한 것)."""
        try:
            if self.executable_path:
                return await pw.chromium.launch(executable_path=self.executable_path, **{k: v for k, v in kwargs.items() if k != "channel"})
            return await pw.chromium.launch(**kwargs)
        except Exception as first:  # noqa: BLE001
            if "executable" not in str(first).lower():  # "Executable doesn't exist at …" 말고(메모리 등)는 이번만 실패
                raise
            exe = find_chromium_executable()
            if not exe or exe == self.executable_path:
                raise _Unavailable(f"크롬 실행 파일 없음 ({str(first).splitlines()[0][:80]})") from first
            log.info("store browser: playwright 기본 크롬이 없어 %s 로 엶", exe)
            kwargs.pop("channel", None)  # 실행 파일을 직접 주면 channel 은 안 씀 (본체 크롬의 --headless 는 새 headless)
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
        return None, "네이버 스토어 사진 브라우저: playwright 미설치 — 막힌(429) 네이버 딜은 사진 없이"
    exe = find_chromium_executable()
    if not exe:
        return None, "네이버 스토어 사진 브라우저: 크롬 실행 파일을 못 찾음 — 막힌(429) 네이버 딜은 사진 없이"
    where = next((part for part in Path(exe).parts if part.startswith("chromium")), Path(exe).name)
    return True, f"네이버 스토어 사진 브라우저 준비 ({where})"
