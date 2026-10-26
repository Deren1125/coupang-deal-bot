"""네이버 쇼핑커넥트 링크 생성 (브라우저 자동화).

흐름
  1) /naverlogin · 대시보드 '로그인 관리' › QR 로그인 (주인 10/26): 네이버 로그인 페이지의 QR 탭을 열어 스크린샷을 주인에게 보냄 →
     네이버 앱으로 스캔 → 로그인 쿠키가 프로필에 저장됨 (이후 재로그인 불필요, 만료되면 다시). QR 화면은 wait_login 이 끝날 때까지
     열어 둔다 — 닫으면 네이버가 앱에서 승인한 로그인을 이 브라우저에 넘기지 못한다. 주인이 누를 때만 (다시 시도·정해진 때 열기 없음)
  2) 발행 시 convert(url): 쇼핑커넥트 링크 생성 페이지에서 상품 URL 입력 → 발급 → 결과 링크 추출
  실제 화면 구조는 이 환경에서 확인하지 못했으므로 셀렉터(config.browser.naver_connect.selectors)는
  /shot <url> 스크린샷을 보고 맞춰야 한다. 실패하면 라우터가 수동(관리자 링크)으로 넘긴다.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable
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
        self.on_state: Callable[[bool], None] | None = None  # 로그인 쿠키를 볼 때마다 (대시보드 '로그인 관리' 칸 — app 이 붙임)
        self._qr_page: Any = None  # 주인이 QR 을 찍는 동안 열어 둔 로그인 화면 (wait_login · close_qr 가 닫음)

    # ------------------------------------------------------------ login
    async def is_logged_in(self) -> bool:
        cookies = await self.session.cookies(name=self.cfg.login_cookie, domain_contains="naver.com")
        if self.on_state is not None:
            try:
                self.on_state(bool(cookies))
            except Exception as e:  # noqa: BLE001 — 상태 기록이 안 돼도 로그인 판단은 그대로
                log.debug("naver login state note failed: %s", type(e).__name__)
        return bool(cookies)

    async def start_qr_login(self) -> bytes:
        """로그인 페이지를 열고 QR 탭을 눌러 스크린샷을 돌려준다. 주인이 직접 하는 로그인 — 접속 상한(site_gate) 멈춤을 풀고 잠시 세지 않음.
        화면은 닫지 않고 둔다 (wait_login 이 끝나면 닫힘 — 앱에서 승인한 로그인을 이 화면이 받아 쿠키로 남김)."""
        from dealbot import site_gate

        site_gate.owner_window(self.session.gate_acct)
        await self.close_qr()

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

        png, self._qr_page = await self.session.run_keep(_do)
        return png

    async def close_qr(self) -> None:
        page, self._qr_page = self._qr_page, None
        if page is not None:
            with contextlib.suppress(Exception):
                await page.close()

    async def wait_login(self, timeout_seconds: int = 120, poll_seconds: float = 3.0) -> bool:
        import asyncio

        page = self._qr_page  # 이 기다림의 QR 화면 — 그사이 다른 쪽(텔레그램 /naverlogin · 대시보드)이 새 QR 을 열었으면 그건 닫지 않음
        try:
            waited = 0.0
            while waited < timeout_seconds:
                if await self.is_logged_in():
                    return True
                await asyncio.sleep(poll_seconds)
                waited += poll_seconds
            return await self.is_logged_in()
        finally:
            if self._qr_page is page:
                await self.close_qr()

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
