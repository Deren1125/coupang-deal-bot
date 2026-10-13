"""네이버 쇼핑커넥트 자동 링크 (주인 결정 10/11) — 딜봇 쪽.

딜봇은 Blog-Auto 받은편지함에 {"connect_link": …} 를 남기고, Blog-Auto 가 connect_results.jsonl 에 답한다.
- 링크가 오면 naver.me 가 같은 상품번호로 넘어가는지 (따라가지 않고) 확인한 뒤 주인이 붙인 것처럼 붙인다 (주인 알림 없음)
- 쇼핑커넥트에 없는 상품은 원본 주소로, 제휴 고지 없이 (주인에게 묻지 않음)
- 오류·로그인·시간 초과면 예전처럼 주인에게 링크 요청. 로그인·제한 안내는 카톡으로 한 번
- 같은 답을 두 번 읽어도 한 번만 반영
진짜 네이버·텔레그램·카톡은 부르지 않는다 (가짜 HTTP·가짜 알림).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest

from dealbot import autolink
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.shops import deal_disclosure

NO = "13008954661"
URL = f"https://brand.naver.com/daekket/products/{NO}"
LINK = "https://naver.me/xAbC12"
NAVER_DISCLOSURE = "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다."


def _redirect_to(location: str, seen: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.method} {request.url}")
        if request.url.host == "naver.me":
            return httpx.Response(307, headers={"location": location})
        return httpx.Response(200, text="store page")  # 따라가면 여기에 기록이 남는다

    return httpx.MockTransport(handler)


GOOD = f"https://brandconnect.naver.com/affiliates/9001?channelProductNo={NO}"


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False
    settings.autolink.naver.enabled = True
    b = DealBot(settings)
    b.state.dry_run = False  # 테스트에는 채널이 없어 연습 모드로 켜짐 — 자동 링크 흐름을 보려고 끈다
    sent: list[str] = []

    async def send_with_id(text: str, *, silent: bool = False, **_kw: object) -> int:
        sent.append(text)
        return 70 + len(sent)

    async def edit(message_id: int, text: str) -> bool:
        return True

    async def available(url: str) -> bool | None:
        return None

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.edit = edit  # type: ignore[method-assign]
    b.enricher.check_available = available  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    b.http = httpx.AsyncClient(transport=_redirect_to(GOOD), follow_redirects=False)
    yield b
    b.db.close()


def _product(url: str = URL, auth: str = "ok", **kw: Any) -> Product:
    no = url.rsplit("/", 1)[-1]
    base: dict[str, Any] = dict(
        source="ppomppu", product_id=f"naver:{no}", shop="naver", name="[네이버] 데켓 주물 IH 오발쿡플레이트 31cm (무료배송)",
        price=60020, url=url, recommend_count=9, extra={"auth": {"status": auth, "reason": "공식 판매처"}},
    )
    base.update(kw)
    return Product(**base)


def _enqueue(bot: DealBot, product: Product | None = None) -> int:
    assert bot.db.enqueue(Deal(product or _product(), DealVerdict(is_deal=True, score=5)), score=5)
    return bot.db.pending_items()[0].id


def _inbox(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _reply(settings: Settings, row: dict) -> None:
    with (settings.data_dir / autolink.RESULTS_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _job(settings: Settings) -> dict:
    return [u["connect_link"] for u in _inbox(settings) if "connect_link" in u][-1]


async def _ask(bot: DealBot, settings: Settings, product: Product | None = None) -> tuple[int, dict]:
    qid = _enqueue(bot, product)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]
    return qid, _job(settings)


async def test_naver_deal_asks_blogauto_instead_of_owner(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    assert job["id"] == qid and job["product_no"] == NO and job["store"] == "daekket" and job["job"]
    assert job["queries"][0].startswith("데켓 주물") and "[" not in job["queries"][0] and "…" not in job["queries"][0]
    assert bot.sent == []  # type: ignore[attr-defined]  # 주인에게 링크 요청 안 함
    assert not [u for u in _inbox(settings) if "owner_notice" in u]
    assert bot.db.queue_counts().get("awaiting_link") is None  # 주인 '내 링크 대기' 목록·한도에 안 셈
    # 같은 상품이 또 들어와도 대기열에 두 번 열리지 않는다
    assert not bot.db.enqueue(Deal(_product(), DealVerdict(is_deal=True)), score=1)


async def test_found_link_is_verified_and_attached_like_an_owner_reply(bot: DealBot, settings: Settings) -> None:
    seen: list[str] = []
    bot.http = httpx.AsyncClient(transport=_redirect_to(GOOD, seen), follow_redirects=False)
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": True, "link": LINK, "existing": False, "commission": 7})
    await bot.autolink_tick()
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.affiliate_url == LINK  # type: ignore[union-attr]
    extra = item.deal.product.extra  # type: ignore[union-attr]
    assert extra.get("auto_link") is True and "owner_link" not in extra  # '링크 붙여 주신 상품' 알림 대상 아님
    assert bot.db.kv_get(f"mylink:naver:{NO}") == LINK  # 다음에 같은 상품은 브라우저 없이 재사용
    assert seen == [f"HEAD {LINK}"]  # naver.me 한 번만, 따라가지 않음 (내 링크를 누르지 않음)
    assert bot.sent == [] and not [u for u in _inbox(settings) if "owner_notice" in u]  # type: ignore[attr-defined]


async def test_same_reply_read_twice_is_applied_once(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    row = {"id": qid, "job": job["job"], "product_no": NO, "ok": True, "link": LINK}
    _reply(settings, row)
    assert await bot.autolink_tick() == 1
    bot.db.update_queue_item(qid, status="published")
    bot.db.kv_set(autolink.KV_POS, "0")  # 재시작 등으로 같은 줄을 다시 읽어도
    _reply(settings, row)  # Blog-Auto 가 같은 답을 두 번 써도
    assert await bot.autolink_tick() == 0
    assert bot.db.get_queue_item(qid).status == "published"  # type: ignore[union-attr]
    assert [e for e in bot.db.recent_events(20) if e["kind"] == "autolink"].__len__() == 1


async def test_reply_for_another_job_is_ignored(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": "old-job", "product_no": NO, "ok": True, "link": LINK})
    assert await bot.autolink_tick() == 0
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "location",
    [
        "https://brandconnect.naver.com/affiliates/9001?channelProductNo=12133409758",  # 다른 상품
        "https://smartstore.naver.com/baleuljung/products/12133409758",
        "https://www.naver.com/",
    ],
)
async def test_link_for_another_product_is_never_attached(bot: DealBot, settings: Settings, location: str) -> None:
    bot.http = httpx.AsyncClient(transport=_redirect_to(location), follow_redirects=False)
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": True, "link": LINK})
    await bot.autolink_tick()
    item = bot.db.get_queue_item(qid)
    assert item.status == "awaiting_link" and item.deal.affiliate_url is None  # type: ignore[union-attr]
    assert bot.db.kv_get(f"mylink:naver:{NO}") is None
    assert bot.sent and "내 링크가 필요합니다" in bot.sent[-1]  # type: ignore[attr-defined]  # 예전처럼 주인에게


async def test_reply_with_other_product_number_or_bad_link_falls_back(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": "999", "ok": True, "link": LINK})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    p2 = _product(url="https://smartstore.naver.com/nextsolution7/products/12584256827")
    qid2, job2 = await _ask(bot, settings, p2)
    _reply(settings, {"id": qid2, "job": job2["job"], "product_no": "12584256827", "ok": True,
                      "link": "https://smartstore.naver.com/nextsolution7/products/12584256827"})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid2).status == "awaiting_link"  # type: ignore[union-attr]


async def test_not_in_shopping_connect_posts_original_url_without_disclosure(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": False, "reason": "not_found"})
    await bot.autolink_tick()
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.affiliate_url == URL  # type: ignore[union-attr]
    assert bot.sent == []  # type: ignore[attr-defined]  # 주인에게 묻지 않음
    deal = item.deal  # type: ignore[union-attr]
    shop = bot.registry.get("naver")
    assert deal_disclosure(shop, deal.product.extra) is None
    text = bot.publisher.render(deal)
    assert URL in text and NAVER_DISCLOSURE not in text and "수수료" not in text
    # 보통 네이버 딜(내 링크)은 고지가 그대로
    plain = Deal(_product(), DealVerdict(is_deal=True), affiliate_url=LINK)
    assert NAVER_DISCLOSURE in bot.publisher.render(plain)
    # 발행까지: 원본 주소 그대로, 블로그 내보내기에도 고지 없음
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "published"  # type: ignore[union-attr]
    rows = [json.loads(x) for x in (settings.data_dir / "preview_deals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["link"] == URL and rows[-1]["disclosure"] == ""


async def test_known_missing_product_skips_the_browser(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": False, "reason": "not_found"})
    await bot.autolink_tick()
    bot.db.update_queue_item(qid, status="published")
    asked = len([u for u in _inbox(settings) if "connect_link" in u])
    qid2 = _enqueue(bot, _product(price=50000))
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(qid2)
    assert item.status == "pending" and item.deal.affiliate_url == URL  # type: ignore[union-attr]
    assert len([u for u in _inbox(settings) if "connect_link" in u]) == asked  # Blog-Auto 에 다시 묻지 않음


async def test_owner_link_on_a_raw_item_brings_the_disclosure_back(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": False, "reason": "not_found"})
    await bot.autolink_tick()
    await bot.attach_link(qid, LINK)
    deal = bot.db.get_queue_item(qid).deal  # type: ignore[union-attr]
    assert "raw_link" not in deal.product.extra and NAVER_DISCLOSURE in bot.publisher.render(deal)


async def test_login_stops_autolink_alerts_once_and_falls_back(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": False, "reason": "login", "retry_at": time.time() + 3600})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u and u["owner_notice"]["kind"] == "autolink_stop"]
    assert len(notes) == 1 and "로그인" in notes[0]["text"] and len(notes[0]["text"]) <= 200
    # 멈춘 동안 새 네이버 딜은 Blog-Auto 에 묻지 않고 바로 예전처럼
    asked = len([u for u in _inbox(settings) if "connect_link" in u])
    bot.db.update_queue_item(qid, status="skipped")
    qid2 = _enqueue(bot, _product(url="https://smartstore.naver.com/baleuljung/products/12133409758"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid2).status == "awaiting_link"  # type: ignore[union-attr]
    assert len([u for u in _inbox(settings) if "connect_link" in u]) == asked
    # 같은 날 또 로그인 답이 와도 카톡은 한 번
    bot._note_autolink_stop({"reason": "login"})
    assert len([u for u in _inbox(settings) if "owner_notice" in u and u["owner_notice"]["kind"] == "autolink_stop"]) == 1


async def test_cap_reason_falls_back_quietly(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "ok": False, "reason": "cap", "retry_at": time.time() + 3600})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert not [u for u in _inbox(settings) if "owner_notice" in u and u["owner_notice"]["kind"] == "autolink_stop"]


async def test_no_reply_in_time_falls_back_and_cancels(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    bot.settings.autolink.naver.wait_minutes = 0
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    cancels = [u["connect_cancel"] for u in _inbox(settings) if "connect_cancel" in u]
    assert cancels == [{"id": qid, "job": job["job"], "at": cancels[0]["at"]}]
    # 늦게 온 답은 무시 (이미 주인에게 넘어감)
    _reply(settings, {"id": qid, "job": job["job"], "product_no": NO, "ok": True, "link": LINK})
    assert await bot.autolink_tick() == 0
    assert bot.db.get_queue_item(qid).deal.affiliate_url is None  # type: ignore[union-attr]


async def test_sold_out_while_waiting_cancels_quietly(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    await bot._on_sold_out(bot.db.get_queue_item(qid).deal.product, via="test")  # type: ignore[union-attr]
    assert bot.db.get_queue_item(qid).status == "expired"  # type: ignore[union-attr]
    assert [u["connect_cancel"]["job"] for u in _inbox(settings) if "connect_cancel" in u] == [job["job"]]
    assert bot.sent == []  # type: ignore[attr-defined]  # 주인에게 물은 적 없는 글이라 알림 없음


async def test_skip_and_owner_link_cancel_the_job(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    bot.skip_item(qid)
    assert [u["connect_cancel"]["job"] for u in _inbox(settings) if "connect_cancel" in u] == [job["job"]]
    bot.db.update_queue_item(qid, status="expired")
    qid2, job2 = await _ask(bot, settings, _product(price=50000))
    msg = await bot.attach_link(qid2, "https://naver.me/OwnerMade")
    assert "상품 링크 완료" in msg
    assert job2["job"] in [u["connect_cancel"]["job"] for u in _inbox(settings) if "connect_cancel" in u]


async def test_disabled_or_unverified_or_other_shops_keep_the_old_flow(bot: DealBot, settings: Settings) -> None:
    bot.settings.autolink.naver.enabled = False
    qid = _enqueue(bot)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    bot.db.update_queue_item(qid, status="skipped")
    bot.settings.autolink.naver.enabled = True
    qid = _enqueue(bot, _product(url="https://smartstore.naver.com/baleuljung/products/12133409758", auth="unknown"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]  # 판매처 확인 안 됨 → 주인이 봄
    bot.db.update_queue_item(qid, status="skipped")
    toss = Product(source="ppomppu", product_id="toss:ABC", shop="toss", name="토스 핫도그", price=14890,
                   url="https://toss.im/_m/ABC", extra={"auth": {"status": "ok", "reason": "x"}})
    qid = _enqueue(bot, toss)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    # 상품번호가 없는 네이버 주소(쇼핑 창 등)도 예전처럼
    bot.db.update_queue_item(qid, status="skipped")
    qid = _enqueue(bot, _product(url="https://shopping.naver.com/window-products/12345", product_id="naver:w12345"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert not [u for u in _inbox(settings) if "connect_link" in u]


async def test_unknown_shop_is_still_skipped_without_raw_links(bot: DealBot) -> None:
    assert bot.settings.publish.allow_raw_links is False
    p = Product(source="ppomppu", product_id="etc:1", shop="etcmall", name="다른 몰 상품", price=10000,
                url="https://etcmall.example/p/1", extra={"auth": {"status": "ok", "reason": "x"}})
    qid = _enqueue(bot, p)
    await bot.process_queue_once()
    item = bot.db.get_queue_item(qid)
    assert item.status != "published" and item.deal.affiliate_url is None  # type: ignore[union-attr]
    assert "raw links disabled" in (item.last_error or "")  # type: ignore[union-attr]


def test_redirect_product_no() -> None:
    assert autolink.redirect_product_no(GOOD) == NO
    assert autolink.redirect_product_no(f"https://smartstore.naver.com/main/products/{NO}") == NO
    assert autolink.redirect_product_no(f"https://brand.naver.com/daekket/products/{NO}?NaPm=x") == NO
    assert autolink.redirect_product_no("https://brandconnect.naver.com/affiliates/1?channelProductNo=1&channelProductNo=2") is None
    assert autolink.redirect_product_no(f"https://evil.example/affiliates/1?channelProductNo={NO}") is None
    assert autolink.redirect_product_no(None) is None


async def test_verify_link_rules() -> None:
    seen: list[str] = []
    http = httpx.AsyncClient(transport=_redirect_to(GOOD, seen), follow_redirects=False)
    assert await autolink.verify_link(http, LINK, NO) is None
    assert await autolink.verify_link(http, LINK, "1") == "mismatch"
    assert await autolink.verify_link(http, "http://naver.me/x", NO) == "not_naver_me"
    assert await autolink.verify_link(http, "https://naver.me/x/y", NO) == "not_naver_me"
    assert all(s.startswith("HEAD https://naver.me/") for s in seen)

    def no_head(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(302, headers={"location": f"/affiliates/1?channelProductNo={NO}"})

    seen.clear()
    http2 = httpx.AsyncClient(transport=httpx.MockTransport(no_head), follow_redirects=False)
    # 상대 주소는 naver.me 기준이라 brandconnect 가 아님 → 받지 않음
    assert await autolink.verify_link(http2, LINK, NO) == "mismatch" and seen == ["HEAD", "GET"]


def test_result_reader_waits_for_whole_lines_and_restarts_on_new_file(tmp_path: Any, db: Any) -> None:
    path = tmp_path / "r.jsonl"
    r = autolink.ResultReader(path, db)
    assert r.read() == ([], 0)
    path.write_text('{"id": 1}\n{"id": 2', encoding="utf-8")
    rows, pos = r.read()
    assert rows == [{"id": 1}] and pos == len('{"id": 1}\n')
    r.commit(pos)
    with path.open("a", encoding="utf-8") as f:
        f.write("}\nnot json\n")
    rows, pos = r.read()
    assert rows == [{"id": 2}]
    r.commit(pos)
    path.write_text('{"id": 3}\n', encoding="utf-8")  # 새 파일 (더 작음) → 처음부터
    assert r.read()[0] == [{"id": 3}]


def test_job_queries() -> None:
    q = autolink.job_queries("[네이버] 데켓 주물 IH 오발쿡플레이트 31cm (무료배송) 아주 긴 상품명 뒤쪽 낱말들")
    assert q and len(q[0]) <= 40 and "…" not in q[0] and len(q) <= 3 and len(set(q)) == len(q)


async def test_resume_from_blogauto_lets_the_bot_ask_again(bot: DealBot, settings: Settings) -> None:
    # 해결 줄은 v2 를 읽는 Blog-Auto 가 있을 때만 (그 판만 생존 신호 파일을 쓴다)
    (settings.data_dir / "blogauto_status.json").write_text(json.dumps({"at": int(time.time()), "kakao_ok": True}), encoding="utf-8")
    bot._note_autolink_stop({"reason": "login", "retry_at": time.time() + 3600})
    qid = _enqueue(bot)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    bot.db.update_queue_item(qid, status="skipped")
    _reply(settings, {"resume": True, "at": time.time()})
    await bot.autolink_tick()
    await _ask(bot, settings, _product(price=50000))
    # 알림 정책(10/11): 멈춤 카톡은 v2 할 일 한 통(⛔[멈춤], 키 autolink:login), 다시 시작하면 그 키를 해결
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u]
    stop = [n for n in notes if n.get("kind") == "autolink_stop"]
    assert len(stop) == 1 and stop[0]["v"] == 2 and stop[0]["key"] == "autolink:login" and stop[0]["urgent"] is True
    assert stop[0]["text"].startswith("⛔[멈춤] 쇼핑커넥트 자동 링크 멈춤")
    assert any("autolink:login" in (n.get("resolve") or []) for n in notes)


# ── 가격 대조 보류(page-price)와 쇼핑커넥트: 보류할 딜은 찾지도 발급하지도 않는다 ──────────────

WATER_URL = "https://smartstore.naver.com/water/products/11223344556"
WATER_NAME = "풀무원 샘물 생수 무라벨 2L 24개"


def _water_page(url: str = WATER_URL) -> Any:
    from pathlib import Path

    from dealbot.enrich import page_meta_at

    return page_meta_at((Path(__file__).parent / "fixtures" / "naver_store_state.html").read_text(encoding="utf-8"), url)


def _unverified_water(price: int) -> Product:
    return _product(url=WATER_URL, name=WATER_NAME, price=price, extra={
        "auth": {"status": "ok", "reason": "공식 판매처"},
        "page_price": {"status": "unverified", "title_price": price, "reason": "페이지를 못 읽음"},
    })


def _pages(bot: DealBot, *, plain: Any = None, chrome: Any = None) -> list[str]:
    opened: list[str] = []

    async def fetch(url: str) -> Any:
        opened.append(f"plain {url}")
        return plain

    async def page_meta(url: str) -> Any:
        opened.append(f"chrome {url}")
        return chrome

    bot.enricher.fetch = fetch  # type: ignore[method-assign]
    bot.store_browser.page_meta = page_meta  # type: ignore[method-assign]
    return opened


async def test_price_held_deal_never_reaches_shopping_connect(bot: DealBot, settings: Settings) -> None:
    """수집 때 페이지를 못 읽은 네이버 딜: 쇼핑커넥트에 묻기 전에 (품절 확인 때 읽은 페이지 → 안 되면 스토어 크롬) 가격을 보고,
    안 맞으면 보류 — Blog-Auto 에 부탁도, 주인 링크 요청도 없음."""
    opened = _pages(bot, plain=None, chrome=_water_page())
    qid = _enqueue(bot, _unverified_water(6410))
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(qid)
    assert item is not None and item.status == "held" and item.last_error.endswith("before link)")  # type: ignore[union-attr]
    assert not [u for u in _inbox(settings) if "connect_link" in u]
    assert opened == [f"plain {WATER_URL}", f"chrome {WATER_URL}"]
    assert len(bot.sent) == 1 and "보류" in bot.sent[0]  # type: ignore[attr-defined]  # 조용한 보류 메모 한 줄, 링크 요청 아님
    assert bot.db.queue_counts() == {"held": 1}


async def test_held_items_are_never_picked_for_a_link(bot: DealBot, settings: Settings) -> None:
    bot.db.add_held(Deal(_product(), DealVerdict(is_deal=True, score=5)), score=5, error="held: page price (price)")
    assert not await bot.process_queue_once()
    await bot.autolink_tick()
    assert _inbox(settings) == [] and bot.sent == []  # type: ignore[attr-defined]


async def test_unverified_deal_whose_page_matches_goes_to_shopping_connect(bot: DealBot, settings: Settings) -> None:
    _pages(bot, plain=_water_page())  # 품절 확인 때 읽은 페이지로 충분 → 크롬은 안 엶
    qid = _enqueue(bot, _unverified_water(14500))
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(qid)
    assert item is not None and item.status == autolink.STATUS
    assert item.deal.product.extra["page_price"]["status"] in ("ok", "option")  # 발행 때 다시 대조하지 않게 남김
    assert _job(settings)["product_no"] == "11223344556"


async def test_unreadable_page_still_asks_shopping_connect(bot: DealBot, settings: Settings) -> None:
    opened = _pages(bot)  # 둘 다 못 읽음 → 예전처럼 진행
    qid = _enqueue(bot, _unverified_water(6410))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]
    assert len(opened) == 2


async def test_late_login_answer_still_stops_and_alerts(bot: DealBot, settings: Settings) -> None:
    """딜봇이 이미 시간 초과로 주인에게 넘긴 글의 늦은 답이라도 로그인 멈춤은 따르고 카톡으로 알린다 (한 번)."""
    qid, job = await _ask(bot, settings)
    bot.settings.autolink.naver.wait_minutes = 0
    await bot.autolink_tick()  # 시간 초과 → 주인 요청
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    row = {"id": qid, "job": job["job"], "product_no": NO, "ok": False, "reason": "login", "retry_at": time.time() + 3600,
           "at": time.time()}
    _reply(settings, row)
    assert await bot.autolink_tick() == 0
    notes = [u["owner_notice"]["text"] for u in _inbox(settings) if u.get("owner_notice", {}).get("kind") == "autolink_stop"]
    assert len(notes) == 1 and "deploy/login.sh finance" in notes[0] and "shopconnect resume" in notes[0]
    assert bot._autolink_product_no(_enqueue_deal(), bot.registry.get("naver"), _ok_auth()) is None  # 멈춘 동안은 묻지 않음
    # 오래된 줄(한 시간 넘음)은 다시 읽어도 알림·멈춤에 쓰지 않는다
    bot.db.kv_set(autolink.KV_STOP, "")
    bot.db.kv_delete_prefix("autolink_alert:")
    _reply(settings, {**row, "at": time.time() - 7200})
    await bot.autolink_tick()
    assert len([u for u in _inbox(settings) if u.get("owner_notice", {}).get("kind") == "autolink_stop"]) == 1


def _enqueue_deal() -> Deal:
    return Deal(_product(), DealVerdict(is_deal=True, score=5))


def _ok_auth() -> Any:
    from dealbot.authenticity import AuthResult

    return AuthResult("ok", "")


def test_instagram_styles_drop_the_disclosure_for_raw_naver_links(bot: DealBot) -> None:
    from dealbot.publisher import insta_style

    naver = bot.registry.get("naver")
    deal = Deal(_product(), DealVerdict(is_deal=True, discount_rate=30), affiliate_url=URL)
    deal.product.extra["raw_link"] = "sc_not_found"
    info = insta_style.card_info(deal, bot.renderer, shop=naver)
    assert not info.disclosure and insta_style.caption_context(deal, info, shop=naver)["disclosure"] == ""
    deal.product.extra.pop("raw_link")
    info = insta_style.card_info(deal, bot.renderer, shop=naver)
    assert info.disclosure and "쇼핑 커넥트" in insta_style.caption_context(deal, info, shop=naver)["disclosure"]


async def test_name_without_search_words_goes_to_the_owner_at_once(bot: DealBot, settings: Settings) -> None:
    qid = _enqueue(bot, _product(name="[네이버] (무료배송)"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert not [u for u in _inbox(settings) if "connect_link" in u]
