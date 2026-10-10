"""막힌 네이버 스토어 페이지를 발행 직전 한 번 실제 크롬으로 (주인 결정 10/10 '발행 직전 한 번만').

서버에서 스토어는 일반 요청엔 429 지만 크롬으로는 200 + og:image(?type=o1000). 테스트는 크롬을 띄우지 않고 _open_page 를 가짜로 바꾼다.
"""

from __future__ import annotations

import io
import logging

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
    sb, opened, sleeps = _browser((200, STORE_URL, HTML), min_interval=60)
    assert await sb.page_meta(STORE_URL) is not None
    assert await sb.page_meta(STORE_URL.replace("13008954661", "1")) is not None
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


async def test_blocked_store_deal_gets_the_browser_photo_at_publish(bot) -> None:  # type: ignore[no-untyped-def]
    """일반 요청은 막힘(conftest: 상품 페이지 못 읽음) → 크롬으로 연 페이지의 og:image(?type=o1000, 이미 1000px 라 그대로) 가
    600px 검사 통과. 크롬에서 찾았으면 쇼핑 검색 API 는 부르지 않는다."""
    assert bot.settings.deal.enrich.store_browser  # config.yaml 에서 켬
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    api_calls: list[str] = []

    async def no_api(p: Product) -> str | None:
        api_calls.append(p.url)
        return None

    bot.naver_shop.find_image = no_api  # type: ignore[method-assign]
    got: list[str] = []
    bot._fetch_image = _images({OG: _jpeg(1000, 1000)}, got)
    d = Deal(_product(), DealVerdict(is_deal=True))
    photo = await bot.deal_photo(d)
    assert photo is not None and d.product.image_url == OG
    assert opened == [STORE_URL] and got == [OG] and api_calls == []


async def test_small_browser_photo_is_upsized(bot) -> None:  # type: ignore[no-untyped-def]
    """크롬으로 연 페이지가 510px 축소본(?type=m510)을 주면 원본 주소부터 받는다."""
    small = OG_FULL + "?type=m510"
    sb, _, _ = _browser((200, STORE_URL, HTML.replace(OG, small)))
    bot.store_browser = sb
    got: list[str] = []
    bot._fetch_image = _images({OG_FULL: _jpeg(1000, 1000), small: _jpeg(510, 510)}, got)
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is not None and d.product.image_url == OG_FULL and got == [OG_FULL]


async def test_browser_is_not_used_when_the_deal_already_has_a_photo(bot) -> None:  # type: ignore[no-untyped-def]
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    big = "https://shop-phinf.pstatic.net/x/1.jpg"
    bot._fetch_image = _images({big: _jpeg(800, 800)}, [])
    d = Deal(_product(image_url=big), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is not None and opened == []


async def test_browser_without_a_usable_photo_falls_through_to_the_api(bot) -> None:  # type: ignore[no-untyped-def]
    sb, opened, _ = _browser((200, STORE_URL, "<html><title>x</title></html>"))
    bot.store_browser = sb
    api_img = "https://shopping-phinf.pstatic.net/main_1/1.jpg"

    async def api(p: Product) -> str | None:
        return api_img

    bot.naver_shop = NaverShopSearch(bot.http, None, None)
    bot.naver_shop.find_image = api  # type: ignore[method-assign]
    bot._fetch_image = _images({api_img: _jpeg(700, 700)}, [])
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is not None and d.product.image_url == api_img and opened == [STORE_URL]


async def test_browser_is_not_opened_when_the_page_was_read_but_its_photo_is_unusable(bot) -> None:  # type: ignore[no-untyped-def]
    """일반 요청으로 페이지를 읽었는데 사진이 600px 미만이면, 크롬도 같은 사진을 주므로 열지 않고 쇼핑 검색으로 넘어간다 (검토 지적)."""
    from dealbot.enrich import page_meta_at

    async def read_page(url: str) -> object:
        return page_meta_at(HTML, STORE_URL)

    bot.enricher.fetch = read_page  # type: ignore[method-assign]
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    api_calls: list[str] = []

    async def api(p: Product) -> str | None:
        api_calls.append(p.url)
        return None

    bot.naver_shop.find_image = api  # type: ignore[method-assign]
    bot._fetch_image = _images({OG: _jpeg(500, 500)}, [])
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None
    assert opened == [] and api_calls == [STORE_URL]


async def test_browser_is_opened_when_the_read_page_had_no_photo(bot) -> None:  # type: ignore[no-untyped-def]
    """읽은 페이지에 사진 주소가 아예 없으면(막힘 안내 화면 등) 크롬으로 한 번."""
    from dealbot.enrich import page_meta_at

    async def read_page(url: str) -> object:
        return page_meta_at("<html><title>잠시 후 다시</title></html>", STORE_URL)

    bot.enricher.fetch = read_page  # type: ignore[method-assign]
    sb, opened, _ = _browser((200, STORE_URL, HTML))
    bot.store_browser = sb
    bot._fetch_image = _images({OG: _jpeg(1000, 1000)}, [])
    d = Deal(_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is not None and opened == [STORE_URL] and d.product.image_url == OG


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
