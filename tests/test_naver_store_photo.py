"""뽐뿌의 네이버 스토어 딜 큰 사진 (주인 결정 '큰 사진 찾기' → 10/15 바뀜).

1. 스마트스토어·브랜드스토어 페이지는 점잖게: 요청 사이 간격, 429·5xx 는 Retry-After(없으면 지수 백오프+지터) 지키며 몇 번만 다시
2. (10/15 지움) 예전엔 그래도 사진이 없으면 네이버 쇼핑 검색 API 의 같은 상품 사진을 썼다. 그 API 는 2026-07-31 종료 (대신할 API 없음,
   서버 키는 401) — 이제 키가 있어도 openapi.naver.com 에 사진을 찾으러 가지 않는다 (아래 2. 묶음은 '안 부름'을 확인).
   네이버 딜 사진은 주인이 링크와 같이 보낸 사진 (test_naver_owner_photo.py)
3. 주인 사진이 없으면 사진 없이 (채널엔 글만, 블로그는 링크 카드, 인스타는 글자 카드) — 스토어 페이지·크롬 사진도 쓰지 않음
테스트는 가짜 HTTP 만 쓰고 실제로 자지 않는다.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image

import dealbot.naver_shop as naver_shop_mod
from dealbot.config import Settings, load_settings
from dealbot.enrich import PageEnricher, is_naver_store, retry_after_seconds
from dealbot.media.imagecheck import naver_image_url
from dealbot.models import Deal, DealVerdict, Product
from dealbot.naver_shop import (
    NaverShopSearch,
    search_queries,
    store_name,
    store_product_no,
)

ROOT = Path(__file__).resolve().parent.parent
STORE_URL = "https://smartstore.naver.com/mystore/products/1234567890"
BOARD_THUMB = "https://cdn4.ppomppu.co.kr/zboard/data/_thumb/ppomppu/7/small_738412.jpg"
API_IMAGE = "https://shopping-phinf.pstatic.net/main_8812345/88123456789.20260901.jpg"
OG_SMALL = "https://shop-phinf.pstatic.net/20260901_1/abc_JPEG/1.jpg?type=m510"
OG_BIG = "https://shop-phinf.pstatic.net/20260901_1/abc_JPEG/1.jpg"
STORE_HTML = f'<html><head><meta property="og:title" content="샴푸 1000ml"><meta property="og:image" content="{OG_SMALL}"></head></html>'
CLIENT_ID, CLIENT_SECRET = "test-client-id", "test-client-secret-value"


def _jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 60, 60)).save(out, "JPEG")
    return out.getvalue()


def _fake_time(enricher: PageEnricher) -> tuple[list[float], list[float]]:
    """가짜 시계·잠: 잔 시간을 적고 시계만 앞으로 돌린다 (실제로 안 잠)."""
    clock, sleeps = [1000.0], []

    async def sleep(s: float) -> None:
        sleeps.append(round(s, 3))
        clock[0] += s

    enricher._sleep = sleep  # type: ignore[assignment]
    enricher._clock = lambda: clock[0]  # type: ignore[assignment]
    return clock, sleeps


def _store_server(*responses: httpx.Response) -> tuple[httpx.AsyncClient, list[str]]:
    """차례대로 응답하는 가짜 스토어 (다 쓰면 마지막 응답을 되풀이)."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return responses[min(len(seen), len(responses)) - 1]

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


# ---------------------------------------------------------------- 1. 스토어 페이지: 간격 + 재시도
@pytest.mark.real_fetch
async def test_store_429_then_200_is_retried_with_backoff() -> None:
    http, seen = _store_server(httpx.Response(429), httpx.Response(200, text=STORE_HTML))
    enricher = PageEnricher(http, store_min_interval=0.5)
    _, sleeps = _fake_time(enricher)
    meta = await enricher.fetch(STORE_URL)
    assert meta is not None and meta.image == OG_SMALL
    assert len(seen) == 2
    assert len(sleeps) == 1 and 1.6 <= sleeps[0] <= 2.4  # Retry-After 가 없으면 지수 백오프(2초) + 지터
    # 백오프가 요청 간격보다 짧으면 간격(3초)만큼은 쉰다
    http, seen = _store_server(httpx.Response(503), httpx.Response(200, text=STORE_HTML))
    enricher = PageEnricher(http, store_min_interval=3.0)
    _, sleeps = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is not None and len(seen) == 2 and sleeps == [3.0]


@pytest.mark.real_fetch
async def test_store_retry_after_is_honoured() -> None:
    http, seen = _store_server(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text=STORE_HTML))
    enricher = PageEnricher(http)
    _, sleeps = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is not None
    assert len(seen) == 2 and sleeps == [7.0]  # 머리글이 말한 7초를 그대로 기다림 (백오프 값 아님)


@pytest.mark.real_fetch
async def test_store_long_retry_after_is_not_waited_and_cools_down() -> None:
    http, seen = _store_server(httpx.Response(429, headers={"Retry-After": "120"}), httpx.Response(200, text=STORE_HTML))
    enricher = PageEnricher(http, store_max_wait=30)
    clock, sleeps = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is None  # 2분은 기다리지 않고 이번엔 건너뜀
    assert await enricher.fetch("https://brand.naver.com/brand/products/1") is None  # 그 시각 전엔 스토어에 다시 안 감
    assert len(seen) == 1 and sleeps == []
    clock[0] += 121
    assert await enricher.fetch(STORE_URL) is not None and len(seen) == 2


@pytest.mark.real_fetch
async def test_store_gives_up_after_a_few_attempts() -> None:
    http, seen = _store_server(httpx.Response(429))
    enricher = PageEnricher(http, store_attempts=3, store_backoff=2.0, store_min_interval=0.5)
    _, sleeps = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is None
    assert len(seen) == 3
    assert len(sleeps) == 2 and 1.6 <= sleeps[0] <= 2.4 and 3.2 <= sleeps[1] <= 4.8  # 2초, 4초 (지터 ±20%)


@pytest.mark.real_fetch
async def test_store_requests_are_spaced_but_other_shops_are_not() -> None:
    http, seen = _store_server(httpx.Response(200, text=STORE_HTML))
    enricher = PageEnricher(http, store_min_interval=3.0)
    _, sleeps = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is not None
    assert await enricher.fetch("https://m.smartstore.naver.com/mystore/products/2") is not None
    assert sleeps == [3.0]  # 스토어끼리는 3초 간격
    assert await enricher.fetch("https://item.gmarket.co.kr/Item?goodscode=1") is not None
    assert sleeps == [3.0] and len(seen) == 3  # 다른 몰은 그대로


def _switch_server(status: list[int]) -> tuple[httpx.AsyncClient, list[str]]:
    """status[0] 으로 응답하는 가짜 스토어 (테스트 중에 바꿀 수 있음)."""
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(status[0], text=STORE_HTML if status[0] == 200 else "")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.mark.real_fetch
async def test_store_breaker_pauses_after_blocked_rounds_and_backs_off(caplog: pytest.LogCaptureFixture) -> None:
    """10/10 서버: 스토어가 늘 429 인데 수집·정품 확인·품절 확인이 같은 주소를 3번씩 두드림 → 막힌 판이 2번 연속이면
    스토어 전체를 10분 쉬고, 또 막히면 20분… (최대 2시간). 한 번 열리면 처음으로."""
    status = [429]
    http, seen = _switch_server(status)
    enricher = PageEnricher(http, store_attempts=1, store_min_interval=0.5)
    clock, _ = _fake_time(enricher)
    caplog.set_level(logging.INFO, logger="dealbot.enrich")
    url = "https://brand.naver.com/b/products/{}".format
    assert await enricher.fetch(url(1)) is None and len(seen) == 1  # 1판 막힘: 아직 안 쉼
    assert await enricher.fetch(url(2)) is None and len(seen) == 2  # 2판 연속 → 10분 쉼
    for n in range(3, 6):
        assert await enricher.fetch(url(n)) is None
    assert len(seen) == 2  # 쉬는 동안은 어느 스토어 주소에도 안 감
    assert sum("cooling down" in r.getMessage() for r in caplog.records) == 1  # '쉬는 중' 로그는 한 번만
    clock[0] += 601
    assert await enricher.fetch(url(6)) is None and len(seen) == 3  # 다시 한 판 → 또 막힘 → 20분
    clock[0] += 601
    assert await enricher.fetch(url(7)) is None and len(seen) == 3
    clock[0] += 600
    status[0] = 200
    assert await enricher.fetch(url(8)) is not None and len(seen) == 4  # 열림 → 처음으로
    status[0] = 429
    assert await enricher.fetch(url(9)) is None and len(seen) == 5
    clock[0] += 1
    assert await enricher.fetch(url(10)) is None and len(seen) == 6  # 1판만 막힌 뒤라 바로 쉬지 않음
    warns = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 3 and "10분" in warns[0] and "20분" in warns[1] and "10분" in warns[2]


@pytest.mark.real_fetch
async def test_store_breaker_caps_the_pause() -> None:
    http, _ = _switch_server([429])
    enricher = PageEnricher(http, store_attempts=1, store_min_interval=0.5, store_breaker_seconds=600, store_breaker_max_seconds=7200)
    clock, _ = _fake_time(enricher)
    pauses: list[float] = []
    for _ in range(12):
        await enricher.fetch(STORE_URL + "?n=1")
        pauses.append(round(enricher._store_next - clock[0]))  # 이 판 직후 정한 쉬는 시간 (시계를 넘기기 전에 잼)
        clock[0] = max(clock[0], enricher._store_next) + 1  # 쉬는 시간이 끝날 때마다 한 판
    assert pauses[1:6] == [600, 1200, 2400, 4800, 7200]  # 10분부터 두 배씩
    assert max(pauses) == 7200 and pauses[-1] == pauses[-2] == 7200  # 아무리 막혀도 2시간 넘게는 안 쉼 (상한에 닿고 머묾)


@pytest.mark.real_fetch
async def test_store_page_is_remembered_briefly_but_failures_are_not() -> None:
    status = [429]
    http, seen = _switch_server(status)
    enricher = PageEnricher(http, store_attempts=1, store_min_interval=0.5, store_cache_seconds=300)
    clock, _ = _fake_time(enricher)
    assert await enricher.fetch(STORE_URL) is None and len(seen) == 1
    status[0] = 200
    clock[0] += 1
    first = await enricher.fetch(STORE_URL)  # 막힌 결과는 기억하지 않음 → 다시 읽음
    assert first is not None and len(seen) == 2
    assert await enricher.fetch(STORE_URL) is first and len(seen) == 2  # 수집 → 정품 확인 → 발행 사진: 같은 페이지 다시 안 읽음
    clock[0] += 301
    assert await enricher.fetch(STORE_URL) is not None and len(seen) == 3  # 5분 지나면 새로 (품절 확인이 너무 낡지 않게)


def test_retry_after_parsing() -> None:
    assert retry_after_seconds("7") == 7.0
    assert retry_after_seconds(None) is None and retry_after_seconds("soon") is None
    when = datetime(2026, 10, 9, 12, 0, 30, tzinfo=UTC)
    assert retry_after_seconds(format_datetime(when, usegmt=True), now=when.timestamp() - 30) == 30.0
    assert is_naver_store(STORE_URL) and is_naver_store("https://m.brand.naver.com/b/products/1")
    assert not is_naver_store("https://search.shopping.naver.com/catalog/1") and not is_naver_store(BOARD_THUMB)


def test_naver_cdn_thumbnail_is_upsized() -> None:
    assert naver_image_url(OG_SMALL) == OG_BIG  # 510px 축소본 → 원본
    assert naver_image_url(OG_BIG + "?type=w1000") == OG_BIG + "?type=w1000"  # 이미 큼
    assert naver_image_url(API_IMAGE) == API_IMAGE and naver_image_url("https://img.example/a.jpg?type=m510") == "https://img.example/a.jpg?type=m510"


# ---------------------------------------------------------------- 2. 쇼핑 검색 API: 사진 찾기에는 안 부름 (10/15, API 종료)
def _item(link: str, *, image: str = API_IMAGE, mall: str = "마이스토어", pid: str = "88123456789") -> dict:
    return {"title": "<b>샴푸</b> 1000ml", "link": link, "image": image, "lprice": "12900", "mallName": mall, "productId": pid}


def _api(items: list[dict] | None = None, *, status: int = 200) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    reqs: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        reqs.append(req)
        assert req.url.host == "openapi.naver.com" and req.url.path == "/v1/search/shop.json"
        if status != 200:
            return httpx.Response(status, json={"errorMessage": "x"})
        return httpx.Response(200, json={"total": len(items or []), "items": items or []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), reqs


def _deal_product(**kw: object) -> Product:
    base: dict = dict(source="ppomppu", product_id="naver:1234567890", shop="naver", name="★역대급★ 마이 샴푸 1000ml 2개",
                      price=12900, url=STORE_URL, image_url=BOARD_THUMB)
    base.update(kw)
    return Product(**base)


def test_photo_lookup_by_shop_search_is_gone() -> None:
    """예전 test_api_item_with_the_same_product_number_is_used: 같은 상품번호의 검색 결과 사진을 썼다.
    10/15: 쇼핑 검색 API 종료 → 사진 찾기(find_image)·같은 상품 판정(same_product)·사진 기억(_cache)을 지웠다."""
    search = NaverShopSearch(_api()[0], CLIENT_ID, CLIENT_SECRET)
    assert not hasattr(search, "find_image") and not hasattr(search, "_cache") and not hasattr(search, "_remember")
    assert not hasattr(naver_shop_mod, "same_product") and not hasattr(naver_shop_mod, "_deal_mall")
    assert search.configured and search.disabled_reason is None  # 최저가 비교(설정으로만 켬)가 쓰는 부분은 그대로


def test_store_product_number_rules() -> None:
    """예전 test_same_product_rules 의 스토어 주소 도구 부분 (쇼핑커넥트 자동 링크가 아직 씀). same_product 판정은 지움."""
    assert store_product_no("https://smartstore.naver.com/mystore/products/1234567890?NaPm=x") == "1234567890"
    assert store_product_no("https://brand.naver.com/brand/products/55") == "55"
    assert store_name(STORE_URL) == "mystore" and store_name("https://smartstore.naver.com/main/products/1") is None
    assert store_product_no("https://www.11st.co.kr/products/1234567890") is None
    assert search_queries("[무배] 마이 샴푸 1000ml 2개 (~9/13)") == ["마이 샴푸 1000ml 2개"]


@pytest.mark.parametrize(
    ("item", "mall"),
    [
        (_item("https://smartstore.naver.com/main/products/1234567890"), None),  # 예전엔 쓰던 같은 상품번호
        (_item("https://smartstore.naver.com/main/products/1234567899"), None),  # 다른 상품번호 (비슷한 상품)
        (_item("https://smartstore.naver.com/otherstore/products/1234567890"), None),  # 번호는 같아도 다른 가게 주소
        (_item("https://www.11st.co.kr/products/1234567890", pid="1234567890"), None),  # 다른 몰 링크의 같은 숫자
        (_item("https://search.shopping.naver.com/gate.nhn?id=88123456789"), None),  # 가격비교 번호뿐
        (_item("https://search.shopping.naver.com/catalog/1234567"), None),  # 카탈로그 (여러 판매처 묶음)
        (_item("https://smartstore.naver.com/main/products/1234567890", mall="다른가게"), "마이스토어"),  # 몰 이름이 다름
    ],
)
async def test_search_results_never_become_the_deal_photo(bot, item: dict, mall: str | None) -> None:  # type: ignore[no-untyped-def]
    """예전 test_lookalike_items_are_rejected: 비슷한 상품은 거르고 같은 상품만 썼다. 이제는 어떤 검색 결과도 사진이 되지 않고,
    키가 있어도 발행 사진(deal_photo)·수집 빈 칸 채우기(_enrich) 모두 검색 API 를 부르지 않는다."""
    http, reqs = _api([item])
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    seen: list[str] = []
    bot._fetch_image = _images({API_IMAGE: _jpeg(1200, 1200)}, seen)
    p = _deal_product()
    if mall:
        p.extra["auth"] = {"status": "ok", "reason": "", "seller": mall}
    assert await bot._enrich(p) == [] and p.image_url == BOARD_THUMB
    assert await bot.deal_photo(Deal(p, DealVerdict(is_deal=True))) is None
    assert reqs == [] and API_IMAGE not in seen


async def test_no_photo_search_even_for_a_long_name(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_api_without_the_same_product_gives_no_photo: 긴 이름이면 짧은 검색어로 한 번 더 찾았다. 이제는 한 번도 안 찾음."""
    http, reqs = _api([_item("https://smartstore.naver.com/main/products/1234567890")])
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    d = Deal(_deal_product(name="마이 샴푸 1000ml 2개 + 트리트먼트 증정"), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None and reqs == []


async def test_missing_keys_say_nothing_about_photos(bot, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    """예전 test_missing_keys_skip_quietly_and_log_once: 키가 없으면 '큰 사진 찾기 건너뜀' 을 한 번 남겼다. 이제 사진 때문에 키를
    찾지 않으니 그 로그도 없다 (키 조치 필요 없음)."""
    assert not bot.naver_shop.configured  # 테스트 환경엔 NAVER_CLIENT_ID/SECRET 이 없다
    bot._fetch_image = _images({}, [])
    with caplog.at_level(logging.INFO):
        assert await bot.deal_photo(Deal(_deal_product(), DealVerdict(is_deal=True))) is None
        await bot._enrich(_deal_product(url="https://smartstore.naver.com/mystore/products/2"))
    assert "NAVER_CLIENT_ID" not in caplog.text and "큰 사진" not in caplog.text


@pytest.mark.parametrize("status", [401, 404])
async def test_dead_api_is_never_hit_so_no_warning_after_restart(bot, status: int, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    """예전 test_dead_keys_or_closed_api_turn_the_lookup_off: 첫 사진 찾기가 401·404 를 맞고 경고를 남긴 뒤 껐다 — 다시 시작할
    때마다 'naver shop search: HTTP 401' 경고가 한 번씩 남았다. 이제 사진·자기 점검 어디서도 부르지 않아 경고가 없다."""
    http, reqs = _api(status=status)
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    bot._fetch_image = _images({}, [])
    with caplog.at_level(logging.INFO):
        assert await bot.deal_photo(Deal(_deal_product(), DealVerdict(is_deal=True))) is None
        await bot._enrich(_deal_product())
        lines = await bot.self_check()
    assert reqs == [] and bot.naver_shop.disabled_reason is None
    assert "HTTP 401" not in caplog.text and "HTTP 404" not in caplog.text and CLIENT_SECRET not in caplog.text
    line = next(text for _ok, text in lines if text.startswith("네이버 쇼핑 검색"))
    assert "2026-07-31 종료" in line and "키 조치 필요 없음" in line


async def test_rate_limited_api_is_not_touched_for_photos(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_api_rate_limit_is_not_remembered: 429 는 기억하지 않고 다음에 다시 물었다. 이제 사진 때문에는 묻지 않는다."""
    http, reqs = _api(status=429)
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    assert await bot.deal_photo(Deal(_deal_product(), DealVerdict(is_deal=True))) is None
    assert await bot._enrich(_deal_product()) == []
    assert reqs == [] and bot.naver_shop.disabled_reason is None


def test_keys_come_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path))
    assert not load_settings(ROOT / "config.yaml", load_env=False).secrets.has_naver_search
    monkeypatch.setenv("NAVER_CLIENT_ID", "abc")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "'def'")
    s = load_settings(ROOT / "config.yaml", load_env=False)
    assert s.secrets.has_naver_search and s.secrets.naver_client_secret == "def"


# ---------------------------------------------------------------- 발행 사진 (deal_photo → photo_file)
@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _images(images: dict[str, bytes], seen: list[str]):  # type: ignore[no-untyped-def]
    async def fetch(url: str, **_kw: object) -> bytes | None:
        seen.append(url)
        return images.get(url)
    return fetch


def _rows(settings: Settings, name: str) -> list[dict]:
    p = settings.data_dir / name
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


async def test_blocked_store_deal_without_owner_photo_posts_without_photo(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """예전 test_blocked_store_deal_gets_the_api_photo_and_exports_it: 스토어가 막히면 쇼핑 검색 사진을 써서 내보냈다.
    10/15: 주인 사진이 없으면 사진 없이 (뽐뿌 글·목록 사진도 안 씀) — 블로그 내보내기는 photo_kind 'text_card'
    (사진 칸은 비우고 글자 카드는 text_card_file 로 따로, 주인 사진을 4시간 기다릴 수 있음 — 약속 10/15)."""
    http, reqs = _api([_item("https://smartstore.naver.com/main/products/1234567890")])
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    seen: list[str] = []
    bot._fetch_image = _images({API_IMAGE: _jpeg(1200, 1200), BOARD_THUMB: _jpeg(1200, 1200)}, seen)
    d = Deal(_deal_product(), DealVerdict(is_deal=True), affiliate_url="https://naver.me/aff")
    photo = await bot.deal_photo(d)  # 스토어 페이지는 못 읽음 (conftest: fetch → None)
    assert photo is None and d.product.image_url is None and reqs == []
    assert seen == []  # 게시판 사진(뽐뿌)은 커도 받아 보지도 않음
    bot.export_published(d, photo)
    row = _rows(settings, "published_deals.jsonl")[-1]
    assert row["photo_kind"] == "text_card" and row["photo_file"] == "" and row["photo_src"] is None and row["image_url"] is None
    assert Path(row["text_card_file"]).is_file() and row["photo_wait_until"]


async def test_small_photo_still_goes_through_the_size_check(bot) -> None:  # type: ignore[no-untyped-def]
    """예전엔 작은 검색 API 사진을 크기 검사로 걸렀다. 이제 그 사진은 없다. 주인 사진이 없는 네이버 딜은 딜의 사진도 받지 않고
    (10/15 ②), 다른 몰 딜의 작은 사진은 그대로 크기 검사(600px)로 거른다."""
    small = "https://shop-phinf.pstatic.net/20260901_1/abc_JPEG/small.jpg"
    seen: list[str] = []
    bot._fetch_image = _images({small: _jpeg(300, 300)}, seen)
    d = Deal(_deal_product(image_url=small), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None and seen == []
    other = Deal(_deal_product(shop="toss", product_id="toss:1", url="https://toss.im/_m/abc", image_url=small), DealVerdict(is_deal=True))
    assert await bot.deal_photo(other) is None and other.product.image_url is None and small in seen


async def test_no_matching_product_keeps_posting_without_photo(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    http, reqs = _api([_item("https://smartstore.naver.com/main/products/1234567899")])  # 비슷한 다른 상품뿐
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    seen: list[str] = []
    bot._fetch_image = _images({API_IMAGE: _jpeg(1200, 1200)}, seen)
    d = Deal(_deal_product(), DealVerdict(is_deal=True), affiliate_url="https://naver.me/aff")
    assert await bot.deal_photo(d) is None and d.product.image_url is None and seen == [] and reqs == []
    bot.export_published(d, None)
    row = _rows(settings, "published_deals.jsonl")[-1]
    assert row["photo_kind"] == "text_card" and row["photo_file"] == "" and row["image_url"] is None


async def test_bot_without_keys_skips_the_api(bot) -> None:  # type: ignore[no-untyped-def]
    assert not bot.naver_shop.configured  # 테스트 환경엔 NAVER_CLIENT_ID/SECRET 이 없다
    bot._fetch_image = _images({}, [])
    d = Deal(_deal_product(), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None


async def test_collect_time_enrich_keeps_the_board_thumb_and_asks_no_api(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_collect_time_enrich_swaps_the_board_thumb_for_the_api_photo: 수집 때 썸네일을 검색 API 사진으로 바꿨다.
    10/15: 검색 API 를 안 부른다 — 썸네일은 그대로 두고 발행 때 걸러진다 (뽐뿌 사진은 안 씀)."""
    http, reqs = _api([_item("https://smartstore.naver.com/main/products/1234567890")])
    bot.naver_shop = NaverShopSearch(http, CLIENT_ID, CLIENT_SECRET)
    p = _deal_product()
    assert bot._should_enrich(p)
    assert await bot._enrich(p) == [] and p.image_url == BOARD_THUMB
    other = _deal_product(url="https://smartstore.naver.com/mystore/products/7")
    assert await bot._enrich(other) == [] and other.image_url == BOARD_THUMB
    assert reqs == []


@pytest.mark.real_fetch
async def test_store_page_after_429_is_read_for_the_price_but_its_photo_is_not_used(bot) -> None:  # type: ignore[no-untyped-def]
    """예전 test_store_page_after_429_gives_the_full_size_photo: 429 뒤 다시 읽은 스토어 페이지의 og:image(510px 축소본)를 원본 주소로
    받아 발행 사진으로 썼다. 10/15 ②(검토 지적으로 그대로 따름): 주인 사진이 없는 네이버 딜은 사진을 찾지 않는다 — 가격을 확인 못 한
    딜이면 발행 직전에 같은 재시도로 페이지를 읽어 가격만 보고(Retry-After 1초여도 스토어 간격 3초는 지킴), 그 사진은 받지 않는다."""
    http, seen = _store_server(httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, text=STORE_HTML))
    bot.enricher = PageEnricher(http)
    _, sleeps = _fake_time(bot.enricher)
    api, reqs = _api([_item("https://smartstore.naver.com/main/products/1234567890")])
    bot.naver_shop = NaverShopSearch(api, CLIENT_ID, CLIENT_SECRET)
    browser: list[str] = []

    async def page_meta(url: str) -> None:  # 읽은 페이지에 가격이 없어 크롬도 한 번 (가짜 — 못 읽음)
        browser.append(url)

    bot.store_browser.page_meta = page_meta
    got: list[str] = []
    bot._fetch_image = _images({OG_BIG: _jpeg(1000, 1000), OG_SMALL: _jpeg(510, 510)}, got)
    p = _deal_product()
    p.extra["page_price"] = {"status": "unverified", "title_price": p.price, "reason": "페이지를 못 읽음"}
    d = Deal(p, DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None and got == []
    assert len(seen) == 2 and sleeps == [3.0] and reqs == [] and browser == [STORE_URL]
    assert naver_image_url(OG_SMALL) == OG_BIG  # 축소본 → 원본 주소 규칙은 그대로 (다른 몰 사진에 씀)


@pytest.mark.real_fetch
async def test_waiting_turn_respects_cooldown_set_meanwhile() -> None:
    """차례를 기다리던 요청은, 그 사이 다른 요청이 429 로 늘린 쉬는 시간(Retry-After)을 지키고 덮어쓰지 않는다 (검토 지적)."""
    import asyncio

    enricher = PageEnricher(httpx.AsyncClient(), store_min_interval=3, store_max_wait=30)
    clock, sleeps = _fake_time(enricher)
    fake_sleep = enricher._sleep

    async def yielding_sleep(s: float) -> None:  # 자는 동안 다른 작업이 돌 수 있게 (실제 asyncio.sleep 처럼)
        await fake_sleep(s)
        await asyncio.sleep(0)

    enricher._sleep = yielding_sleep  # type: ignore[assignment]
    enricher._store_next = clock[0] + 3  # 앞 요청 직후: 3초 간격 대기 중

    async def other_request_gets_429() -> None:  # 첫 요청이 자는 동안 돈다 (gather 가 첫 요청부터 시작)
        enricher._store_next = max(enricher._store_next, clock[0] + 20)  # _get_store 가 Retry-After 20 을 반영하는 것과 같음

    ok, _ = await asyncio.gather(enricher._store_turn(), other_request_gets_429())
    assert ok and sum(sleeps) >= 20  # 3초만 자고 나가지 않음
    assert enricher._store_next >= clock[0] + 3  # 다음 차례도 간격 유지 (쉬는 시간을 지우지 않음)

