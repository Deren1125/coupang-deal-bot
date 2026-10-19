"""사진 부탁 알림의 '사진 받을 곳' 링크 (주인 10/18: "링크를 같이 줘야 내가 바로 첨부할 수 있지") + 주인 딜 사진 화면 캡처 다듬기.

- 알림 모양 links (최대 3 · http(s) · 500자 · 짧은 이름) — 예전 알림(칸 없음)은 그대로
- 딜봇 딜 사진 부탁(📷 사진 필요 · 네이버 링크+사진)은 상품 페이지(제휴 링크가 아님)
- 웹 푸시: 첫 링크 = 알림 버튼 하나 (묶음은 버튼 없음) · 서비스 워커가 event.action 으로 그 주소를 연다
- 카톡 예비: 첫 링크를 끝 줄에 (200자 안)
- 대시보드: 사진 올리기 옆 '상품 열기 ↗'·'사진 찾을 곳 ↗', 시트에 모두
- 화면 캡처: 상품 사진 덩어리를 확신하면 그 부분만, 애매하면 상태바·여백만, 진짜 사진은 그대로
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import random
import re
import time
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dash_helpers import fixture, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.media import imagecheck, screenshot
from dealbot.monitoring.admin import product_links
from dealbot.notify import ingest
from dealbot.notify.links import action_title, clean_links, kakao_url
from dealbot.utils.timeutil import utcnow
from test_notify_router import Env

FIXTURES = Path(__file__).parent / "fixtures" / "dash_contract"
# Blog-Auto tests/fixtures/dash_contract/notice_open_links.json 과 같은 파일 (두 저장소 테스트가 같은 값을 본다)
LINKS_FIXTURE_SHA256 = "524259644a1f5b417476bc8298f3180f589df10280d907550b2098e575f37398"


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    e = Env(tmp_path)
    yield e
    e.db.close()


# ---------------------------------------------------------------- 모양 · 거르기
def test_clean_links_caps_and_validation() -> None:
    raw = [{"label": "상품 페이지", "url": "https://smartstore.naver.com/x/products/1"},
           {"label": "same", "url": "https://smartstore.naver.com/x/products/1"},  # 같은 주소는 한 번
           {"label": "js", "url": "javascript:alert(1)"}, {"label": "ftp", "url": "ftp://x.com/a"},
           {"label": "space", "url": "https://x.com/a b"}, {"label": "at", "url": "https://user@x.com/"},
           {"label": "long", "url": "https://x.com/" + "a" * 500}, "nope", {"url": 5},
           {"label": "가" * 60, "url": "http://news.example.com/a"}, {"url": "https://b.example.com/2"},
           {"label": "넷째", "url": "https://c.example.com/3"}]
    out = clean_links(raw)
    assert [x["url"] for x in out] == ["https://smartstore.naver.com/x/products/1", "http://news.example.com/a",
                                       "https://b.example.com/2"]  # 최대 3개
    assert out[0]["label"] == "상품 페이지" and len(out[1]["label"]) == 24 and out[1]["label"].endswith("…")
    assert out[2]["label"] == "사진 출처 3"
    assert clean_links(None) == [] and clean_links({"url": "https://x.com"}) == []
    assert action_title("dealbot", "") == "상품 열기" and action_title("blogauto", "hotdeal") == "상품 열기"
    assert action_title("blogauto", "issue") == "사진 찾기"


def test_contract_fixture_is_the_shared_file_and_ingests_links(env: Env) -> None:
    raw = (FIXTURES / "notice_open_links.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == LINKS_FIXTURE_SHA256
    n = ingest.clean_open(fixture("notice_open_links.json"))
    assert n is not None and n["links"] == fixture("notice_open_links.json")["links"]
    assert ingest.clean_open(fixture("notice_open.json"))["links"] == []  # type: ignore[index]  # 예전 알림 그대로
    bad = dict(fixture("notice_open_links.json"), links=[{"label": "x", "url": "javascript:x"}, 7])
    assert ingest.clean_open(bad)["links"] == []  # type: ignore[index]  # 링크가 틀려도 알림은 받는다
    assert ingest.clean_open(dict(fixture("notice_open_links.json"), links="https://x.com"))["links"] == []  # type: ignore[index]
    assert env.router.open(n) == "new"
    row = env.router.store.get("blogauto", "photo:issue:52")
    assert row is not None and [x["url"] for x in row.links] == [x["url"] for x in n["links"]]


# ---------------------------------------------------------------- 웹 푸시
async def test_single_push_has_one_action_button_and_batch_has_none(env: Env) -> None:
    env.device()
    n = ingest.clean_open(fixture("notice_open_links.json"))
    env.router.open(dict(n, at=env.clock()))  # type: ignore[arg-type]
    env.clock.tick(21)
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "item" and p["url"] == "/dashboard/go/post-issue-52"  # 알림 자체는 그 할 일
    assert p["open"] == {"title": "사진 찾기", "url": "https://www.news1.kr/entertain/celebrity-topic/5900001"}
    # 딜봇 딜 사진 부탁: '상품 열기'
    env.open("photo:80", "photo_need", iid="deal-80", links=[{"label": "상품 페이지", "url": "https://smartstore.naver.com/p/products/80"}])
    env.clock.tick(21)
    await env.router.tick()
    p = env.pushes()[-1]
    assert p["open"] == {"title": "상품 열기", "url": "https://smartstore.naver.com/p/products/80"}
    # 링크 없는 알림은 버튼 없음 · 두 개 이상이면 묶음 (버튼 없음)
    env.open("link:5", iid="deal-5")
    env.clock.tick(21)
    await env.router.tick()
    assert "open" not in env.pushes()[-1]
    env.open("photo:81", "photo_need", iid="deal-81", links=[{"label": "상품 페이지", "url": "https://a.example.com/81"}])
    env.open("photo:82", "photo_need", iid="deal-82", links=[{"label": "상품 페이지", "url": "https://a.example.com/82"}])
    env.clock.tick(21)
    await env.router.tick()
    b = env.pushes()[-1]
    assert b["type"] == "batch" and "open" not in b


def test_service_worker_handles_the_action_and_keeps_the_dashboard_tap() -> None:
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    sw_v = re.search(r"'sb-v(\d+)'", sw)
    assert sw_v and int(sw_v.group(1)) >= 8  # 이 일로 올린 캐시 판 (그 뒤로 더 올라가도 됨)
    assert "d.type === 'item' && d.open" in sw and "actions: o ? [{action: 'open'" in sw
    assert "/^https?:\\/\\//.test(d.open.url" in sw  # http(s) 주소만 버튼으로
    assert "e.action === 'open' && e.notification.data && e.notification.data.open" in sw
    assert "self.clients.openWindow(ext)" in sw
    assert "win.postMessage({go: url})" in sw  # 알림 자체를 누르면 그대로 대시보드 그 할 일


# ---------------------------------------------------------------- 카톡 예비
async def test_kakao_fallback_puts_the_first_link_on_its_own_line(env: Env) -> None:
    env.router.kakao_kinds = ("photo_need", "link_request")  # 설정으로 넓혔을 때의 문구 (기본은 카톡 없음 — 주인 10/19)
    url = "https://smartstore.naver.com/paulham/products/80?NaPm=" + "x" * 200  # 긴 추적 쿼리는 뗀다
    env.open("photo:80", "photo_need", iid="deal-80", title="🔔 사진 필요 #80 폴햄 여성 에코베로 라운드 긴팔티",
             body="→ 대시보드 할 일에서 사진 올리기 " + "가" * 150, links=[{"label": "상품 페이지", "url": url}])
    env.clock.tick(21)
    await env.router.tick()  # 기기 없음 → 카톡
    [k] = env.kakao_files()
    lines = k["text"].split("\n")
    assert lines[-1] == "https://smartstore.naver.com/paulham/products/80" and len(k["text"]) <= 200
    assert lines[0].startswith("🔔 사진 필요 #80") and k["link"].endswith("/dashboard/go/deal-80")  # 버튼은 대시보드 그대로
    assert kakao_url([{"label": "x", "url": "https://a.example.com/" + "p" * 300}]) is None  # 쿼리를 떼도 길면 넣지 않음


async def test_kakao_batch_names_the_link_only_when_one_item_has_it(env: Env) -> None:
    env.router.kakao_kinds = ("photo_need", "link_request")  # 설정으로 넓혔을 때의 문구 (기본은 카톡 없음 — 주인 10/19)
    env.open("photo:80", "photo_need", iid="deal-80", short="딜 사진 #80",
             links=[{"label": "상품 페이지", "url": "https://smartstore.naver.com/p/products/80"}])
    env.open("link:7", iid="deal-7")
    env.clock.tick(21)
    await env.router.tick()
    [k] = env.kakao_files()
    assert k["text"].split("\n")[-1] == "딜 사진 #80: https://smartstore.naver.com/p/products/80" and len(k["text"]) <= 200


# ---------------------------------------------------------------- 딜봇 딜 사진 부탁
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_late_photo_and_naver_link_requests_carry_the_product_page(bot: DealBot) -> None:
    store = "https://smartstore.naver.com/paulham/products/8080"
    item = queued(bot, status="published", product_id="naver:80", shop="naver", name="폴햄 여성 에코베로 라운드 긴팔티",
                  url=store)
    item.deal.affiliate_url = "https://naver.me/aff1234"
    await bot.late_photo.ask(item.id, item.deal, utcnow())
    row = bot.router.store.get("dealbot", f"photo:{item.id}")  # type: ignore[union-attr]
    assert row is not None and row.links == [{"label": "상품 페이지", "url": store}]  # 제휴 링크가 아닌 상품 페이지
    link = queued(bot, product_id="naver:81", shop="naver", name="네이버 딜", url="https://smartstore.naver.com/a/products/81")
    await bot.notifier.notify_manual_link(link, bot.registry.get("naver"))
    row = bot.router.store.get("dealbot", f"link:{link.id}")  # type: ignore[union-attr]
    assert row is not None and row.links[0]["url"] == "https://smartstore.naver.com/a/products/81"
    toss = queued(bot, product_id="toss:9")
    await bot.notifier.notify_manual_link(toss, bot.registry.get("toss"))
    assert bot.router.store.get("dealbot", f"link:{toss.id}").links == []  # type: ignore[union-attr]  # 사진 부탁이 아닌 딜
    assert product_links(dataclasses.replace(item.deal.product, url="http://x.example.com/a")) == []  # https 만


# ---------------------------------------------------------------- 대시보드
def _export(path: Path, posts: dict) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    data = fixture("export_v2.json")
    data["written_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    data["todo"]["generated_at"] = data["written_at"]
    data["posts"] = posts
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def test_dashboard_photo_items_show_the_link_button(bot: DealBot, tmp_path: Path) -> None:
    from dash_helpers import get_json

    item = queued(bot, status="published", product_id="naver:80", shop="naver", name="폴햄 여성 에코베로 라운드 긴팔티",
                  url="https://smartstore.naver.com/paulham/products/8080")
    await bot.late_photo.ask(item.id, item.deal, utcnow())
    data = fixture("export_v2.json")
    posts = data["posts"]
    posts["issue"].append({"pid": 52, "title": "윤아 공항패션", "status": "saved", "needs": ["photos"], "slots": [],
                           "photo_request": None})  # 기록 파일에 출처가 없으면 알림 링크로
    _export(tmp_path / "blogauto.json", posts)
    bot.router.open(dict(ingest.clean_open(fixture("notice_open_links.json")), at=time.time()))  # type: ignore[union-attr, arg-type]
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    by = {it["iid"]: it for it in body["todo"]}
    deal = by[f"deal-{item.id}"]
    assert deal["kind"] == "photo_need" and deal["primary"]["action"] == "deal.late_photo"
    assert deal["links"] == [{"label": "상품 페이지", "url": "https://smartstore.naver.com/paulham/products/8080"}]
    assert not deal["detail"].get("open")  # 시트에서도 같은 버튼 하나 (두 번 그리지 않음)
    pick = by["post-issue-46"]  # 기록 파일의 사진 출처
    assert pick["links"] and pick["links"][0]["url"].startswith("https://m.search.naver.com/")
    p52 = by["post-issue-52"]
    assert [x["url"] for x in p52["links"]][0] == "https://www.news1.kr/entertain/celebrity-topic/5900001"
    assert p52["detail"]["photo_request"]["links"] == p52["links"]  # 시트에 모두
    assert not by["post-finance-12"].get("links")  # 사진 부탁이 아닌 줄은 버튼 없음


def test_app_js_draws_the_link_button_next_to_the_photo_button() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "a.input === 'photos' ? it.links || []" in js
    assert "ext('pb soft', it.src === 'deal' || it.blog === 'hotdeal' ? '상품 열기' : '사진 찾을 곳', L[0].url)" in js
    assert "링크 ${L.length}개 모두" in js
    css = (webapp.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert ".two{display:flex;gap:8px}" in css


# ---------------------------------------------------------------- 화면 캡처 다듬기 (주인 딜 사진)
RED = (200, 40, 50)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _shot(w: int = 1080, h: int = 2400, *, product: bool = True, text_to: int = 2200) -> Image.Image:
    """휴대폰 상품 페이지 캡처: 상태바(시각 + 아이콘 3개) + 흰 화면 + 색 있는 상품 사진 덩어리 + 아래 글 줄."""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    s = w / 1080
    d.rectangle([50 * s, 35 * s, 170 * s, 75 * s], fill=(0, 0, 0))
    for x0, x1 in ((850, 890), (905, 945), (960, 1030)):
        d.rectangle([x0 * s, 40 * s, x1 * s, 72 * s], fill=(0, 0, 0))
    d.rectangle([60 * s, 180 * s, 380 * s, 230 * s], fill=(40, 40, 40))  # 가게 이름 줄
    if product:
        rnd = random.Random(3)
        for y in range(300, 1380, 12):  # 상품 사진 (결이 있는 색 덩어리)
            for x in range(0, 1080, 12):
                d.rectangle([x * s, y * s, (x + 12) * s, (y + 12) * s],
                            fill=(rnd.randint(150, 220), rnd.randint(30, 80), rnd.randint(40, 90)))
    rnd = random.Random(2)
    for y in range(1460, text_to, 50):  # 상품명·가격·설명 글 줄 (낱말 사이가 띄어진 글자)
        x = 60
        while x < rnd.randint(500, 1000):
            wd = rnd.randint(30, 110)
            d.rectangle([x * s, y * s, (x + wd) * s, (y + 22) * s], fill=(60, 60, 60))
            x += wd + rnd.randint(18, 30)
    return im


def test_screenshot_is_cropped_to_the_product_block() -> None:
    out, what = screenshot.tidy(_shot())
    assert what == "photo"
    assert abs(out.width - 1080) <= 12 and 1000 <= out.height <= 1110  # 사진 덩어리 (1080×1080 안팎)
    jpeg, why = imagecheck.owner_photo(_png(_shot()), crop=True)
    assert why == "" and jpeg is not None
    with Image.open(io.BytesIO(jpeg)) as im:
        assert max(im.size) / min(im.size) < 1.2  # 길쭉한 캡처가 아니라 상품 사진


def test_ambiguous_screenshot_only_loses_the_bars_and_margins() -> None:
    src = _shot(product=False)  # 사진 덩어리 없음 (글만) → 상태바·여백만
    out, what = screenshot.tidy(src)
    assert what == "bars" and out.height < src.height and out.width <= src.width
    assert screenshot.phone_ui_bounds(src)[0] > 0
    jpeg, why = imagecheck.owner_photo(_png(src), crop=True)
    assert why == "" and jpeg is not None
    with Image.open(io.BytesIO(jpeg)) as im:
        assert im.height < src.height
    # 다듬고도 길쭉하면 예전처럼 친절한 이유 (자르고도 안 되는 것만)
    long = _shot(h=3200, product=False, text_to=3150)
    jpeg, why = imagecheck.owner_photo(_png(long), crop=True)
    assert jpeg is None and "길쭉" in why and "상품 부분만" in why


def test_real_photos_and_old_path_are_unchanged() -> None:
    rnd = random.Random(1)
    photo = Image.new("RGB", (900, 1600))  # 세로로 긴 진짜 사진 (16:9) — 가장자리까지 그림
    px = photo.load()
    for y in range(1600):
        for x in range(0, 900, 4):
            v = 110 + rnd.randint(-40, 40) if y > 600 else 205 + rnd.randint(-12, 12)
            for k in range(4):
                px[x + k, y] = (v, v - 10, v - 20)
    out, what = screenshot.tidy(photo)
    assert what == "" and out is photo
    square = Image.new("RGB", (1000, 1000), (120, 90, 60))
    assert screenshot.tidy(square) == (square, "")
    data = _png(_shot())
    assert screenshot.tidy_bytes(b"not an image") == (b"not an image", "")
    # 발행 때 다시 볼 때(crop 없음)는 예전 규칙 그대로: 긴 캡처는 받지 않음
    jpeg, why = imagecheck.owner_photo(data)
    assert jpeg is None and "길쭉" in why


def test_small_telegram_screenshot_still_gets_the_hint_after_cropping() -> None:
    small = _shot().resize((554, 1231))  # 텔레그램이 줄인 캡처 → 잘라도 짧은 변이 600 아래
    jpeg, why = imagecheck.owner_photo(_png(small), crop=True)
    assert jpeg is None and "너무 작아요" in why and "파일로 보내" in why


async def test_dashboard_and_telegram_owner_photos_are_cropped(bot: DealBot) -> None:
    jpeg, why = await bot._check_owner_photo(_png(_shot()))
    assert why == "" and jpeg is not None
