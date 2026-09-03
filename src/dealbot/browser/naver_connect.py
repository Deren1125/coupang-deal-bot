"""네이버 쇼핑커넥트 링크 생성 (브라우저 자동화).

흐름
  1) /naverlogin : 네이버 로그인 페이지의 QR 탭을 열어 스크린샷을 관리자에게 보냄 → 네이버 앱으로 스캔 → 로그인 쿠키가
     프로필에 저장됨 (이후 재로그인 불필요, 만료되면 다시 /naverlogin)
  2) 발행 시 convert(url): 쇼핑커넥트 링크 생성 페이지에서 상품 URL 입력 → 발급 → 결과 링크 추출
  실제 화면 구조는 이 환경에서 확인하지 못했으므로 셀렉터(config.browser.naver_connect.selectors)는
  /shot <url> 스크린샷을 보고 맞춰야 한다. 실패하면 라우터가 수동(관리자 링크)으로 넘긴다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from dealbot.browser.session import BrowserSession
from dealbot.config import NaverConnectConfig

log = logging.getLogger(__name__)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


class NaverConnectError(Exception):
    pass


class NaverConnectProvider:
    key = "naver_connect"

    def __init__(self, session: BrowserSession, cfg: NaverConnectConfig) -> None:
        self.session = session
        self.cfg = cfg

    # ------------------------------------------------------------ login
    async def is_logged_in(self) -> bool:
        cookies = await self.session.cookies(name=self.cfg.login_cookie, domain_contains="naver.com")
        return bool(cookies)

    async def start_qr_login(self) -> bytes:
        """로그인 페이지를 열고 QR 탭을 눌러 스크린샷을 돌려준다."""

        async def _do(page: Any) -> bytes:
            await page.goto(self.cfg.login_url, wait_until="domcontentloaded", timeout=45000)
            sel = self.cfg.selectors.get("qr_tab")
            if sel:
                try:
                    await page.locator(sel).first.click(timeout=8000)
                except Exception as e:  # noqa: BLE001
                    log.warning("qr tab click failed (%s) — sending page as is", e)
            await page.wait_for_timeout(2500)
            return await page.screenshot(type="png")

        return await self.session.run(_do)

    async def wait_login(self, timeout_seconds: int = 120, poll_seconds: float = 3.0) -> bool:
        import asyncio

        waited = 0.0
        while waited < timeout_seconds:
            if await self.is_logged_in():
                return True
            await asyncio.sleep(poll_seconds)
            waited += poll_seconds
        return await self.is_logged_in()

    # ------------------------------------------------------------ convert
    async def convert(self, url: str) -> str:
        if not await self.is_logged_in():
            raise NaverConnectError("naver not logged in — send /naverlogin first")
        sel = self.cfg.selectors
        timeout = self.cfg.timeout_seconds * 1000

        async def _do(page: Any) -> str:
            await page.goto(self.cfg.create_url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            inp = page.locator(sel["url_input"]).first
            await inp.fill(url, timeout=timeout)
            await page.locator(sel["create_button"]).first.click(timeout=timeout)
            await page.wait_for_timeout(2500)
            result = page.locator(sel["result"]).first
            try:
                await result.wait_for(timeout=timeout)
            except Exception as e:  # noqa: BLE001
                raise NaverConnectError(f"result element not found: {e}") from e
            value = ""
            for getter in ("input_value", "inner_text"):
                try:
                    value = await getattr(result, getter)()
                except Exception:  # noqa: BLE001
                    continue
                if value:
                    break
            if not value:
                href = await result.get_attribute("href")
                value = href or ""
            m = _URL_RE.search(value or "")
            if not m:
                raise NaverConnectError(f"no link in result: {value[:80]!r}")
            link = m.group(0)
            if url.split("?")[0] in link:
                raise NaverConnectError("result looks like the original url, not a connect link")
            return link

        return await self.session.run(_do)
