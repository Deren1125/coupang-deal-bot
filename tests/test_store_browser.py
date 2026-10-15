"""막힌 네이버 스토어 페이지를 발행 직전 한 번 실제 크롬으로 (주인 결정 10/10 '발행 직전 한 번만').

서버에서 스토어는 일반 요청엔 429 지만 크롬으로는 200 + og:image(?type=o1000). 테스트는 크롬을 띄우지 않고 _open_page 를 가짜로 바꾼다.
10/15 주인 결정 ②: 네이버 딜 사진은 주인이 링크와 같이 보낸 사진만 — 크롬은 가격 확인용으로만 열고 그 페이지 사진은 쓰지 않는다
(아래 '발행 사진' 묶음).
"""

from __future__ import annotations

import io
import logging

import httpx
import pytest
from PIL import Image

import dealbot.store_browser as sbm
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.naver_shop import NaverShopSearch
from dealbot.store_browser import (
    StoreBrowser,
    _Unavailable,
    mem_available_mb,
    other_chrome_running,
    store_browser_check,
)

STORE_URL = "https://brand.naver.com/daekket/products/13008954661"
BOARD_THUMB = "https://cdn4.ppomppu.co.kr/zboard/data/_thumb/ppomppu/1/small_739081.jpg"
OG = "https://shop-phinf.pstatic.net/20260123_260/1769_JPEG/1032.jpeg?type=o1000"
OG_FULL = "https://shop-phinf.pstatic.net/20260123_260/1769_JPEG/1032.jpeg"
HTML = f'<html><head><title>데켓 주물팬 : 데켓</title><meta property="og:image" content="{OG}"></head></html>'


def _jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (90, 90, 90)).save(out, "JPEG")
    return out.getvalue()


def _browser(*pages: tuple[int, str, str] | Exception, free: int | None = 1500, **kw: object) -> tuple[StoreBrowser, list[str], list[float]]:
    """가짜 크롬: 차례대로 (코드, 최종 주소, HTML) 을 주거나 예외를 던진다. 가짜 시계·잠."""
    sb = StoreBrowser(**kw)  # type: ignore[arg-type]
    opened: list[str] = []
    clock, sleeps = [1000.0], []

    async def open_page(url: str) -> tuple[int, str, str]:
        opened.append(url)
        clock[0] += 5  # 크롬 한 번 = 5초
        got = pages[min(len(opened), len(pages)) - 1]
        if isinstance(got, Exception):
            raise got
        return got

    async def sleep(s: float) -> None:
        sleeps.append(round(s, 1))
        clock[0] += s

    sb._open_page = open_page  # type: ignore[method-assign]
    sb._sleep = sleep  # type: ignore[assignment]
    sb._clock = lambda: clock[0]  # type: ignore[assignment]
    sb._mem = lambda: free  # type: ignore[assignment]
    return sb, opened, sleeps


async def test_store_page_is_read_with_the_browser() -> None:
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    meta = await sb.page_meta(STORE_URL)
    assert meta is not None and meta.image == OG and meta.final_url == STORE_URL and opened == [STORE_URL]


async def test_relative_photo_is_resolved_against_the_final_page() -> None:
    sb, _, _ = _browser((200, STORE_URL, '<meta property="og:image" content="//shop-phinf.pstatic.net/a.jpg">'))
    meta = await sb.page_meta(STORE_URL)
    assert meta is not None and meta.image == "https://shop-phinf.pstatic.net/a.jpg"


@pytest.mark.parametrize("url", ["https://item.gmarket.co.kr/Item?goodscode=1", "https://search.shopping.naver.com/catalog/1", ""])
async def test_only_naver_store_pages_open_the_browser(url: str) -> None:
    sb, opened, _ = _browser((200, url, HTML))
    assert await sb.page_meta(url) is None and opened == []


async def test_turned_off_never_opens() -> None:
    sb, opened, _ = _browser((200, STORE_URL, HTML), enabled=False)
    assert await sb.page_meta(STORE_URL) is None and opened == []


async def test_low_memory_skips(caplog: pytest.LogCaptureFixture) -> None:
    sb, opened, _ = _browser((200, STORE_URL, HTML), free=300, min_free_mb=500)
    with caplog.at_level(logging.INFO, logger="dealbot.store_browser"):
        assert await sb.page_meta(STORE_URL) is None
    assert opened == [] and "300MB" in caplog.text  # 블로그 크롬이 떠 있을 때 등: 이번엔 안 엶 (다음 발행 땐 다시 봄)
    sb._mem = lambda: 900  # type: ignore[assignment]
    assert await sb.page_meta(STORE_URL) is not None and opened == [STORE_URL]


async def test_unknown_memory_does_not_block() -> None:
    sb, opened, _ = _browser((200, STORE_URL, HTML), free=None)
    assert await sb.page_meta(STORE_URL) is not None and opened == [STORE_URL]


async def test_opens_are_spaced_from_when_the_last_one_closed() -> None:
    other = STORE_URL.replace("13008954661", "1")
    sb, opened, sleeps = _browser((200, STORE_URL, HTML), (200, other, HTML), min_interval=60)
    assert await sb.page_meta(STORE_URL) is not None
    assert await sb.page_meta(other) is not None
    assert len(opened) == 2 and sleeps == [60.0]  # 앞 창을 닫은 뒤 60초


async def test_missing_playwright_turns_it_off_once(caplog: pytest.LogCaptureFixture) -> None:
    sb, opened, _ = _browser(_Unavailable("playwright 미설치"))
    with caplog.at_level(logging.INFO, logger="dealbot.store_browser"):
        assert await sb.page_meta(STORE_URL) is None
        assert await sb.page_meta(STORE_URL) is None
    assert len(opened) == 1 and sb.disabled_reason == "playwright 미설치"
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1


async def test_a_failed_open_is_not_fatal() -> None:
    sb, opened, _ = _browser(TimeoutError("goto timeout"), (200, STORE_URL, HTML), min_interval=0)
    assert await sb.page_meta(STORE_URL) is None and sb.disabled_reason is None  # 이번만 실패 (끄지 않음)
    assert await sb.page_meta(STORE_URL) is not None and len(opened) == 2


@pytest.mark.parametrize("status", [429, 403, 0])
async def test_blocked_or_empty_page_gives_nothing(status: int) -> None:
    sb, _, _ = _browser((status, STORE_URL, HTML if status else ""))
    assert await sb.page_meta(STORE_URL) is None


async def test_blocked_browser_rests_before_opening_again(caplog: pytest.LogCaptureFixture) -> None:
    """크롬도 막히면(429) 사진용으로도 한동안 안 띄운다 (가격 대조와 같은 쉬는 시간). 로그는 쉬기 시작할 때 한 번."""
    sb, opened, _ = _browser((429, STORE_URL, ""), (200, STORE_URL, HTML), min_interval=0, cooldown_seconds=1800)
    with caplog.at_level(logging.INFO, logger="dealbot.store_browser"):
        assert await sb.page_meta(STORE_URL) is None
        assert await sb.page_meta(STORE_URL) is None and len(opened) == 1  # 쉬는 중: 띄우지 않음
    assert sum(r.levelno == logging.WARNING for r in caplog.records) == 1 and "30분" in caplog.text
    sb._clock = lambda: 10_000_000.0  # type: ignore[assignment]  # 쉬는 시간이 지나면 다시
    assert await sb.page_meta(STORE_URL) is not None and len(opened) == 2 and not sb.cooling()


async def test_blog_and_other_pages_never_open_the_browser() -> None:
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    for url in ("https://blog.naver.com/someone/223000000000", "https://search.shopping.naver.com/catalog/1", "https://toss.shopping/t/1"):
        got = await sb.read(url, wait=False)
        assert got.meta is None and not got.opened and got.why == "네이버 상품 페이지 아님"
    assert opened == []


async def test_cut_off_read_still_closes_the_browser() -> None:
    """시간 제한으로 끊겨도 띄운 크롬은 닫는다 (창을 닫으면 그 안의 페이지도 닫힘)."""
    import asyncio

    pw = _FakePlaywright()
    closed: list[bool] = []

    async def hang(url: str, **_kw: object) -> object:
        await asyncio.Event().wait()

    async def close() -> None:
        closed.append(True)

    pw.goto = hang  # type: ignore[method-assign]
    pw.close = close  # type: ignore[method-assign]
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(StoreBrowser()._load(pw, STORE_URL, headless=True), 0.05)
    assert closed == [True]


class _FakeLoads:
    """_visit 가 부르는 _load 가짜: 모드별 (코드, 주소, HTML)."""

    def __init__(self, headless: int, headed: int = 200) -> None:
        self.codes = {True: headless, False: headed}
        self.calls: list[bool] = []

    async def __call__(self, pw: object, url: str, *, headless: bool) -> tuple[int, str, str]:
        self.calls.append(headless)
        return self.codes[headless], url, HTML


@pytest.mark.parametrize(
    ("headless", "screen", "other", "calls", "code"),
    [
        (200, True, False, [True], 200),  # 창 없는 크롬으로 열리면 끝 (화면에 창을 안 띄움)
        (429, True, False, [True, False], 200),  # 막히면 가상 화면 크롬 한 번
        (403, True, False, [True, False], 200),
        (429, True, True, [True], 429),  # 블로그 봇 크롬이 떠 있으면 화면 크롬은 안 띄움 (포커스·클립보드)
        (429, False, False, [True], 429),  # 가상 화면이 없으면 그대로
        (500, True, False, [True], 500),  # 막힌 게 아닌 오류는 다시 안 함
    ],
)
async def test_headless_first_then_screen_only_when_blocked_and_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path, headless: int, screen: bool, other: bool, calls: list[bool], code: int  # type: ignore[no-untyped-def]
) -> None:
    sock = tmp_path / "X99"
    if screen:
        sock.write_text("")
    monkeypatch.setattr(sbm, "XVFB_SOCKET", sock)
    monkeypatch.setattr(sbm, "other_chrome_running", lambda: other)
    sb = StoreBrowser()
    loads = _FakeLoads(headless)
    sb._load = loads  # type: ignore[method-assign]
    got = await sb._visit(object(), STORE_URL)
    assert loads.calls == calls and got[0] == code


class _FakePlaywright:
    """_load 가 부르는 playwright 가짜: 띄운 옵션·만든 창 옵션·페이지에서 실행한 스크립트를 기록한다."""

    def __init__(self) -> None:
        self.launches: list[dict[str, object]] = []
        self.contexts: list[dict[str, object]] = []
        self.evaluated: list[str] = []
        self.chromium = self

    async def launch(self, **kw: object) -> _FakePlaywright:
        self.launches.append(kw)
        return self

    async def new_page(self) -> _FakePlaywright:
        return self

    async def new_context(self, **kw: object) -> _FakePlaywright:
        self.contexts.append(kw)
        return self

    async def route(self, *_a: object) -> None:
        return None

    async def evaluate(self, script: str) -> str:
        self.evaluated.append(script)
        return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/140.0.0.0 Safari/537.36"

    async def goto(self, url: str, **_kw: object) -> object:
        self.url = url
        return type("Resp", (), {"status": 200})()

    async def wait_for_timeout(self, _ms: float) -> None:
        return None

    async def content(self) -> str:
        return HTML

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("headless", [True, False])
async def test_browser_is_not_disguised(headless: bool) -> None:
    """위장 금지: 브라우저 이름(user agent)을 바꾸지 않고 (창 없는 크롬은 HeadlessChrome 그대로),
    탐지를 피하려는 채널·플래그도 주지 않는다. 막히면 가상 화면의 보통 크롬 → 그래도 막히면 사진 없이 (쇼핑 검색 API 는 종료)."""
    pw = _FakePlaywright()
    got = await StoreBrowser()._load(pw, STORE_URL, headless=headless)
    assert got == (200, STORE_URL, HTML)
    (launch,) = pw.launches
    assert "channel" not in launch and not any("AutomationControlled" in str(a) for a in launch["args"])  # type: ignore[attr-defined]
    assert [k for kw in pw.contexts for k in kw if k in ("user_agent", "extra_http_headers")] == []
    assert pw.evaluated == []  # navigator.userAgent 를 읽어 고쳐 쓰지 않음


async def test_login_browser_does_not_hide_automation(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """쇼핑커넥트 로그인 창(BrowserSession)도 자동화 표시를 숨기는 플래그(AutomationControlled)를 넣지 않는다."""
    import playwright.async_api as pwa

    from dealbot.browser.session import BrowserSession

    seen: dict[str, object] = {}

    class _Pw:
        def __init__(self) -> None:
            self.chromium = self

        async def start(self) -> _Pw:
            return self

        async def launch_persistent_context(self, _profile: str, **kw: object) -> _Pw:
            seen.update(kw)
            return self

        async def close(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(pwa, "async_playwright", _Pw)
    session = BrowserSession(tmp_path / "profile")
    await session.start()
    await session.close()
    assert seen and not any("AutomationControlled" in str(a) for a in seen.get("args") or [])  # type: ignore[attr-defined]


def test_other_chrome_running_reads_command_lines(tmp_path) -> None:  # type: ignore[no-untyped-def]
    def proc(pid: str, cmd: bytes) -> None:
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(cmd)

    proc("1", b"/sbin/init\0")
    proc("20", b"")  # 닫힌 크롬의 좀비: 명령줄이 비어 있음
    proc("30", b"/home/ubuntu/coupang-deal-bot/.venv/bin/python\0-m\0dealbot\0run\0")
    (tmp_path / "self").mkdir()
    assert not other_chrome_running(str(tmp_path))
    proc("40", b"/home/ubuntu/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome\0--user-data-dir=/home/ubuntu/Blog-Auto/var/profiles/issue\0")
    assert other_chrome_running(str(tmp_path))
    assert other_chrome_running(str(tmp_path / "missing"))  # 못 읽으면 띄우지 않는 쪽으로


def test_mem_available_reads_meminfo(tmp_path) -> None:  # type: ignore[no-untyped-def]
    f = tmp_path / "meminfo"
    f.write_text("MemTotal:        1951744 kB\nMemFree:          100000 kB\nMemAvailable:     925696 kB\n")
    assert mem_available_mb(str(f)) == 904
    assert mem_available_mb(str(tmp_path / "none")) is None


def test_self_check_line(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    ok, text = store_browser_check()
    assert ok is None and "playwright 미설치" in text
    monkeypatch.undo()
    monkeypatch.setattr(sbm, "find_chromium_executable", lambda *a: "/home/ubuntu/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome")
    ok, text = store_browser_check()
    assert ok is True and "chromium-1243" in text
    monkeypatch.setattr(sbm, "find_chromium_executable", lambda *a: None)
    ok, text = store_browser_check()
    assert ok is None and "크롬 실행 파일" in text


# ---------------------------------------------------------------- 발행 사진(deal_photo)에서
@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _product(**kw: object) -> Product:
    base: dict = dict(source="ppomppu", product_id="naver:13008954661", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm",
                      price=60020, url=STORE_URL, image_url=BOARD_THUMB)
    base.update(kw)
    return Product(**base)


def _images(images: dict[str, bytes], seen: list[str]):  # type: ignore[no-untyped-def]
    async def fetch(url: str, **_kw: object) -> bytes | None:
        seen.append(url)
        return images.get(url)
    return fetch


def _unverified(p: Product) -> Product:
    """수집 때 스토어가 막혀(429) 가격을 확인 못 한 딜 (page_price 'unverified')."""
    p.extra["page_price"] = {"status": "unverified", "title_price": p.price, "reason": "페이지를 못 읽음"}
    return p


async def test_blocked_store_deal_opens_no_browser_for_a_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_blocked_store_deal_gets_the_browser_photo_at_publish: 일반 요청이 막힌 스토어 딜은 발행 직전 크롬으로 연 페이지의
    og:image 를 썼다 (10/10). 주인 결정 10/15 ②(검토 지적으로 그대로 따름): 주인 사진이 없는 네이버 딜은 사진을 찾지 않는다 —
    크롬을 사진 때문에 열지 않고(최대 45초) 채널엔 글만. 가격을 이미 확인한 딜이면 크롬을 아예 안 연다."""
    assert bot.settings.deal.enrich.store_browser  # config.yaml 에서 켬 (이제 가격 확인용)
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    got: list[str] = []
    bot._fetch_image = _images({OG: _jpeg(1000, 1000)}, got)
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None
    assert opened == [] and got == []
    assert not hasattr(bot.naver_shop, "find_image")  # 쇼핑 검색 사진 찾기는 없음 (10/15)


async def test_unchecked_store_deal_reads_the_price_with_the_browser_but_not_its_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_small_browser_photo_is_upsized (크롬 페이지의 510px 축소본 → 원본 주소로 받음). 이제 크롬은 가격 확인용:
    수집 때 가격을 확인 못 한 딜이면 발행 직전에 한 번 열어 가격만 보고, 그 페이지의 사진은 큰 주소든 작은 주소든 받지 않는다."""
    small = OG_FULL + "?type=m510"
    sb, opened, _ = _browser((200, STORE_URL, HTML.replace(OG, small)))
    bot.store_browser = sb
    got: list[str] = []
    bot._fetch_image = _images({OG_FULL: _jpeg(1000, 1000), small: _jpeg(510, 510)}, got)
    d = Deal(_unverified(_product()), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None
    assert opened == [STORE_URL] and got == []


async def test_naver_deal_photo_address_is_not_used_without_the_owner_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_browser_is_not_used_when_the_deal_already_has_a_photo: 딜에 큰 스토어 사진 주소가 있으면 그걸 썼다 (크롬 안 엶).
    10/15 ②: 주인 사진이 없는 네이버 딜은 그 주소도 쓰지 않는다 (받지도 않음). 크롬도 안 엶."""
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    big = "https://shop-phinf.pstatic.net/x/1.jpg"
    got: list[str] = []
    bot._fetch_image = _images({big: _jpeg(800, 800)}, got)
    d = Deal(_product(image_url=big), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and opened == [] and got == [] and d.product.image_url is None


async def test_browser_without_a_usable_photo_posts_without_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """크롬 페이지에도 쓸 사진이 없으면 사진 없이 (예전엔 쇼핑 검색 API 사진으로 넘어갔지만 그 API 는 2026-07-31 종료 — 주인 결정 10/15:
    채널엔 글만, 인스타는 글자 카드). 키가 있어도 검색 API 를 부르지 않는다. 가격을 이미 확인한 딜이라 크롬도 안 엶."""
    sb, opened, _ = _browser((200, STORE_URL, "<html><title>x</title></html>"))
    bot.store_browser = sb
    api_img = "https://shopping-phinf.pstatic.net/main_1/1.jpg"
    asked: list[str] = []

    def api(req: httpx.Request) -> httpx.Response:
        asked.append(str(req.url))
        return httpx.Response(200, json={"items": [{"link": STORE_URL, "image": api_img, "productId": "13008954661"}]})

    bot.naver_shop = NaverShopSearch(httpx.AsyncClient(transport=httpx.MockTransport(api)), "id", "secret")
    seen: list[str] = []
    bot._fetch_image = _images({api_img: _jpeg(700, 700)}, seen)
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None and opened == []
    assert asked == [] and seen == []


async def test_browser_is_not_opened_when_the_read_page_had_the_price(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_browser_is_not_opened_when_the_page_was_read_but_its_photo_is_unusable (사진 기준). 이제 가격 기준:
    일반 요청으로 읽은 페이지에 가격이 있으면 그걸로 대조하고 크롬은 열지 않는다. 그 페이지 사진도 쓰지 않는다."""
    from pathlib import Path

    from dealbot.enrich import page_meta_at

    store = (Path(__file__).parent / "fixtures" / "naver_store_state.html").read_text(encoding="utf-8")
    url = "https://smartstore.naver.com/water/products/11223344556"  # 그 페이지(가격·옵션이 든 스토어 상태)의 상품

    async def read_page(page_url: str) -> object:
        return page_meta_at(store, page_url)

    bot.enricher.fetch = read_page  # type: ignore[method-assign]
    sb, opened, _ = _browser((200, url, HTML))
    bot.store_browser = sb
    got: list[str] = []
    bot._fetch_image = _images({OG: _jpeg(1000, 1000)}, got)
    d = Deal(_unverified(_product(url=url, product_id="naver:11223344556")), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None
    assert opened == [] and got == []
    assert d.product.extra["page_price"]["status"] != "unverified"  # 읽은 페이지로 대조함


async def test_browser_is_opened_when_the_read_page_had_no_price(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_browser_is_opened_when_the_read_page_had_no_photo. 읽은 페이지에 가격이 없으면(막힘 안내 화면 등) 크롬으로 한 번 —
    가격만 보고 사진은 쓰지 않는다."""
    from dealbot.enrich import page_meta_at

    async def read_page(url: str) -> object:
        return page_meta_at("<html><title>잠시 후 다시</title></html>", STORE_URL)

    bot.enricher.fetch = read_page  # type: ignore[method-assign]
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    got: list[str] = []
    bot._fetch_image = _images({OG: _jpeg(1000, 1000)}, got)
    d = Deal(_unverified(_product()), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and opened == [STORE_URL] and got == [] and d.product.image_url is None


def test_newest_full_chromium_is_found_across_layouts(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from dealbot.browser.session import find_chromium_executable

    for rel in ("chromium-1194/chrome-linux/chrome", "chromium-1243/chrome-linux64/chrome",
                "chromium_headless_shell-1300/chrome-headless-shell-linux64/chrome-headless-shell"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True)
        f.write_text("")
    monkeypatch.delenv("DEALBOT_CHROMIUM_PATH", raising=False)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert find_chromium_executable().endswith("chromium-1243/chrome-linux64/chrome")  # 본체 크롬 중 가장 새 판 (셸보다 먼저)


async def test_page_read_is_remembered_for_a_while() -> None:
    """쇼핑커넥트에 묻기 전 가격 대조로 연 페이지를 발행 때 사진용으로 다시 열지 않는다 (30분 기억)."""
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    first = await sb.page_meta(STORE_URL)
    again = await sb.page_meta(STORE_URL)
    assert first is not None and again is first and opened == [STORE_URL]
    sb._clock = lambda: 10_000_000.0  # type: ignore[assignment]  # 한참 뒤
    await sb.page_meta(STORE_URL)
    assert opened == [STORE_URL, STORE_URL]


async def test_sent_elsewhere_is_not_a_block() -> None:
    """크롬이 그 상품이 아닌 곳(19세 상품의 로그인 등)으로 넘어가면 그 상품만 못 읽은 것 — 크롬 전체를 쉬지 않음. 캡차 주소면 막힘."""
    login = (200, "https://nid.naver.com/nidlogin.login?mode=form&url=" + STORE_URL, "<html><title>네이버 : 로그인</title></html>")
    sb, opened, _ = _browser(login, (200, "https://ncpt.naver.com/captcha?x=1", "<html></html>"), min_interval=0)
    got = await sb.read(STORE_URL)
    assert got.meta is None and got.opened and not got.blocked and got.why == "다른 페이지로 넘어감" and not sb.cooling()
    got = await sb.read(STORE_URL)
    assert got.blocked and got.why == "크롬도 막힘 (캡차)" and sb.cooling() and len(opened) == 2
