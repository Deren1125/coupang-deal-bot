from __future__ import annotations

import os
from pathlib import Path

import pytest

from dealbot.collectors.algumon import DEFAULT_SELECTORS, dom_summary, parse_list

SAMPLE = """
<html><body><ul>
<li class="post-li"><a class="product-link" href="/n/deal/12345">[쿠팡] 스탠리 텀블러 (29,900원/무료)</a>
  <span class="product-mall">쿠팡</span><span class="product-like">7</span>
  <a class="origin" href="https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&no=600001">원문</a></li>
<li class="post-li"><a class="product-link" href="/n/deal/12346">[토스쇼핑] 핫도그 8개입 2세트 (14,890원)</a>
  <span class="product-like">12</span></li>
</ul></body></html>
"""


def test_parse_list_default_selectors() -> None:
    items = parse_list(SAMPLE, DEFAULT_SELECTORS)
    assert len(items) == 2
    a, b = items
    assert a["shop_tag"] == "쿠팡" and a["price"] == 29900 and a["recommend"] == 7
    assert a["origin_url"].startswith("https://www.ppomppu.co.kr/zboard/view.php")
    assert a["link"] == "https://www.algumon.com/n/deal/12345"
    assert b["shop_tag"] == "토스쇼핑" and b["price"] == 14890 and b["origin_url"] == b["link"]
    assert a["external_id"] != b["external_id"]


def test_dom_summary_and_no_rows() -> None:
    assert parse_list("<html><body><div class='x'>nothing</div></body></html>", DEFAULT_SELECTORS) == []
    summary = dom_summary(SAMPLE)
    assert "li.post-li×2" in summary and "a.product-link×2" in summary


@pytest.mark.skipif(not os.path.isdir(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent")), reason="chromium not installed")
async def test_browser_session_local_page(tmp_path: Path) -> None:
    from dealbot.browser.naver_connect import NaverConnectProvider
    from dealbot.browser.session import BrowserSession
    from dealbot.config import NaverConnectConfig

    page = tmp_path / "connect.html"
    page.write_text(
        """<html><body>
        <a href="#" id="qr">QR코드</a>
        <input type="text" placeholder="상품 URL 을 입력" id="u">
        <button onclick="document.getElementById('r').value='https://naver.me/ABC?x='+document.getElementById('u').value.length">링크 발급</button>
        <input readonly id="r">
        </body></html>""",
        encoding="utf-8",
    )
    url = page.as_uri()
    session = BrowserSession(tmp_path / "profile", headless=True)
    try:
        png = await session.screenshot(url, wait_ms=100)
        assert png[:4] == b"\x89PNG"
        cfg = NaverConnectConfig(login_url=url, create_url=url, login_cookie="NID_AUT")
        prov = NaverConnectProvider(session, cfg)
        assert not await prov.is_logged_in()
        shot = await prov.start_qr_login()
        assert shot[:4] == b"\x89PNG"
        with pytest.raises(Exception, match="not logged in"):
            await prov.convert("https://smartstore.naver.com/x/products/1")
        # 로그인 쿠키를 흉내 내 변환 흐름 검증
        await session._context.add_cookies([{"name": "NID_AUT", "value": "x", "domain": ".naver.com", "path": "/"}])
        assert await prov.is_logged_in()
        link = await prov.convert("https://smartstore.naver.com/x/products/1")
        assert link.startswith("https://naver.me/ABC")
    finally:
        await session.close()
