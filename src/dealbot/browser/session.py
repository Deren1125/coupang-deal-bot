"""Playwright 브라우저 세션 (로그인 상태를 data_dir/browser-profile 에 영구 저장).

playwright 패키지와 크로미움이 없으면 BrowserUnavailable 을 던진다 (선택 기능).
"""

from __future__ import annotations

import asyncio
import glob
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def find_chromium_executable(explicit: str | None = None) -> str | None:
    """설치된 크로미움 실행 파일을 찾는다 (playwright 가 요구하는 버전과 달라도 쓸 수 있게)."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    env = os.environ.get("DEALBOT_CHROMIUM_PATH")
    if env and os.path.exists(env):
        return env
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if base:
        for pattern in ("chromium-*/chrome-linux/chrome", "chromium-*/chrome-linux64/chrome", "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"):
            hits = sorted(glob.glob(os.path.join(base, pattern)))
            if hits:
                return hits[-1]
    return None


class BrowserUnavailable(Exception):
    pass


class BrowserSession:
    def __init__(self, profile_dir: Path, *, headless: bool = True, locale: str = "ko-KR", executable_path: str | None = None) -> None:
        self.profile_dir = Path(profile_dir)
        self.headless = headless
        self.locale = locale
        self.executable_path = executable_path
        self._pw: Any = None
        self._context: Any = None
        self._lock = asyncio.Lock()

    @property
    def started(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        if self._context is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:  # pragma: no cover
            raise BrowserUnavailable("playwright is not installed (pip install playwright && playwright install chromium)") from e
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "locale": self.locale,
            "viewport": {"width": 1280, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            self._pw = await async_playwright().start()
            try:
                self._context = await self._pw.chromium.launch_persistent_context(str(self.profile_dir), **launch_kwargs)
            except Exception as first:  # noqa: BLE001
                # playwright 가 기대하는 버전의 브라우저가 없으면 설치된 다른 크로미움으로 시도
                exe = find_chromium_executable(self.executable_path)
                if not exe:
                    raise first
                log.info("default chromium launch failed (%s) — retrying with %s", str(first).splitlines()[0][:80], exe)
                self._context = await self._pw.chromium.launch_persistent_context(
                    str(self.profile_dir), executable_path=exe, **launch_kwargs
                )
        except Exception as e:  # noqa: BLE001
            await self.close()
            raise BrowserUnavailable(f"cannot launch chromium: {str(e).splitlines()[0]}") from e
        log.info("browser session started (profile=%s, headless=%s)", self.profile_dir, self.headless)

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception as e:  # noqa: BLE001
            log.debug("context close: %s", e)
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception as e:  # noqa: BLE001
            log.debug("playwright stop: %s", e)
        self._context = None
        self._pw = None

    async def new_page(self) -> Any:
        await self.start()
        return await self._context.new_page()

    async def cookies(self, name: str | None = None, domain_contains: str | None = None) -> list[dict[str, Any]]:
        await self.start()
        out = []
        for c in await self._context.cookies():
            if name and c.get("name") != name:
                continue
            if domain_contains and domain_contains not in c.get("domain", ""):
                continue
            out.append(c)
        return out

    async def screenshot(self, url: str, *, wait_ms: int = 1500, full_page: bool = False) -> bytes:
        page = await self.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(wait_ms)
            return await page.screenshot(full_page=full_page, type="png")
        finally:
            await page.close()

    async def run(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """페이지를 열어 fn(page, *args) 를 실행하고 닫는다 (직렬화)."""
        async with self._lock:
            page = await self.new_page()
            try:
                return await fn(page, *args, **kwargs)
            finally:
                await page.close()
