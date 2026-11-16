"""자동 링크만 올리기 (주인 11/16 '내가 링크 넣어 줘야 하는 것들은 빼자. 자동 링크만 올려') — publish.owner_link_requests: false.

- 주인 링크 말고는 길이 없는 딜(네이버 — 서버 네이버 접속 끔·자동 링크 꺼짐, 남의 토스 쉐어링크 등): 할 일 '링크 넣기' 카드·카톡 없이,
  채널에도 안 올리고 건너뜀 — 기록에 '자동 링크 불가로 건너뜀' + 저녁 요약 '자동 링크 불가' 건수
- 토스 자동 쉐어링크는 그대로: 로그인 풀림이면 묻지 않고 기다렸다가 다시 되면 자동으로. 자동이 실패하면 주인에게 묻지 않고 건너뜀
- 이미 떠 있던 주인 링크 요청은 정리 (자동 링크를 거칠 수 있으면 자동 링크 대기로, 아니면 건너뜀)
- owner_link_requests: true 면 예전처럼 링크 요청 (나머지 테스트가 그 흐름을 본다 — conftest)
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from dealbot import autolink
from dealbot.app import NO_AUTOLINK, NO_AUTOLINK_LABEL, DealBot, _closed_label
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product

TOSS_URL = "https://toss.shopping/t/2282607549"
NAVER_URL = "https://smartstore.naver.com/goodstore/products/9876543210"


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 100
    settings.publish.dry_run = False
    settings.publish.owner_link_requests = False  # 운영 기본 (주인 11/16)
    settings.autolink.toss.enabled = True
    settings.autolink.naver.enabled = False  # 서버 네이버 접속 끔 (주인 10/30)
    b = DealBot(settings)
    b.state.dry_run = False
    sent: list[str] = []
    manual: list[int] = []

    async def send_with_id(text: str, *, silent: bool = False, **_kw: object) -> int:
        sent.append(text)
        return 70 + len(sent)

    async def edit(message_id: int, text: str) -> bool:
        return True

    async def available(url: str) -> bool | None:
        return None

    async def notify_manual_link(item: Any, shop: Any, **_kw: Any) -> int:
        manual.append(item.id)
        return 1

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.notifier.edit = edit  # type: ignore[method-assign]
    b.notifier.notify_manual_link = notify_manual_link  # type: ignore[method-assign]
    b.enricher.check_available = available  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    b.manual = manual  # type: ignore[attr-defined]
    yield b
    b.db.close()


def _naver(no: str = "9876543210") -> Product:
    return Product(source="ppomppu", product_id=f"naver:{no}", shop="naver", name="쌍화차 10팩 30팩", price=9900,
                   url=f"https://smartstore.naver.com/goodstore/products/{no}", recommend_count=9)


def _toss(url: str = TOSS_URL) -> Product:
    return Product(source="algumon", product_id=f"toss:{url.rstrip('/').rsplit('/', 1)[-1]}", shop="toss",
                   name="잘풀리는집 퀼팅 화장지 30m x 24롤", price=15900, url=url, recommend_count=9)


def _enqueue(bot: DealBot, product: Product) -> int:
    assert bot.db.enqueue(Deal(product, DealVerdict(is_deal=True, score=5)), score=5)
    return bot.db.items_for_product(product.product_id, ("pending",))[0].id


def _inbox(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _events(bot: DealBot) -> list[str]:
    return [e["message"] for e in bot.db.recent_events(50)]


async def test_naver_deal_is_skipped_without_a_link_card(bot: DealBot, settings: Settings) -> None:
    checked: list[str] = []

    async def sold_out(p: Product) -> bool:
        checked.append(p.url)
        return False

    bot._page_sold_out = sold_out  # type: ignore[method-assign]
    qid = _enqueue(bot, _naver())
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(qid)
    assert item.status == "skipped" and item.last_error.startswith(NO_AUTOLINK)  # type: ignore[union-attr]
    assert bot.manual == [] and bot.db.queue_counts().get("awaiting_link") is None  # type: ignore[attr-defined]
    assert checked == []  # 묻지도 않을 딜의 페이지는 열지 않음
    assert any("자동 링크 불가로 건너뜀" in m and "네이버" in m for m in _events(bot))
    assert _closed_label("skipped", item.last_error) == NO_AUTOLINK_LABEL  # type: ignore[union-attr]


async def test_switch_on_keeps_the_old_link_request(bot: DealBot) -> None:
    bot.settings.publish.owner_link_requests = True
    qid = _enqueue(bot, _naver())
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert bot.manual == [qid]  # type: ignore[attr-defined]


async def test_toss_still_autolinks_and_waits_on_login(bot: DealBot, settings: Settings) -> None:
    qid = _enqueue(bot, _toss())
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]
    job = [u["toss_link"] for u in _inbox(settings) if "toss_link" in u][-1]
    with (settings.data_dir / autolink.TOSS_RESULTS_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": qid, "job": job["job"], "url": TOSS_URL, "ok": False, "reason": "login",
                            "retry_at": time.time() + 3600}) + "\n")
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]  # 기다림 (다시 로그인하면 자동)
    assert bot.manual == []  # type: ignore[attr-defined]


async def test_toss_autolink_failure_is_skipped_not_asked(bot: DealBot, settings: Settings) -> None:
    qid = _enqueue(bot, _toss())
    assert await bot.process_queue_once()
    bot.settings.autolink.toss.wait_minutes = 0
    await bot.autolink_tick()  # 첫 시간 초과 → 한 번 더 자동으로
    await bot.autolink_tick()  # 또 시간 초과 → 예전엔 주인에게, 이제는 건너뜀
    item = bot.db.get_queue_item(qid)
    assert item.status == "skipped" and item.last_error.startswith(NO_AUTOLINK)  # type: ignore[union-attr]
    assert bot.manual == []  # type: ignore[attr-defined]


async def test_share_link_toss_deal_is_skipped(bot: DealBot) -> None:
    qid = _enqueue(bot, _toss("https://toss.im/_m/P4Qr1ope"))  # 남의 쉐어링크 — 자동 링크 불가
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "skipped"  # type: ignore[union-attr]
    assert bot.manual == []  # type: ignore[attr-defined]


async def test_existing_link_requests_are_cleared(bot: DealBot, settings: Settings) -> None:
    naver = _enqueue(bot, _naver())
    toss = _enqueue(bot, _toss())
    toss_tried = _enqueue(bot, _toss("https://toss.shopping/t/78829901"))
    owned = _enqueue(bot, _naver("1112223334"))
    for qid in (naver, toss, toss_tried, owned):
        bot.db.update_queue_item(qid, status="awaiting_link", error="manual link required")
    bot.db.kv_set(f"{autolink.KV_TRIED}{toss_tried}", "2026-11-15T00:00:00+00:00")
    it = bot.db.get_queue_item(owned)
    it.deal.affiliate_url = "https://naver.me/OwnerMade"  # type: ignore[union-attr]
    bot.db.update_queue_item(owned, status="awaiting_link", deal=it.deal)  # type: ignore[union-attr]
    resolved: list[str] = []
    orig = bot.notifier.resolve
    bot.notifier.resolve = lambda *keys: (resolved.extend(keys), orig(*keys))[1]  # type: ignore[method-assign]
    await bot.autolink_tick()
    assert bot.db.get_queue_item(naver).status == "skipped"  # type: ignore[union-attr]
    assert bot.db.get_queue_item(toss_tried).status == "skipped"  # type: ignore[union-attr]
    assert bot.db.get_queue_item(toss).status == autolink.STATUS  # type: ignore[union-attr]  # 토스 자동 링크로
    assert bot.db.get_queue_item(owned).status == "awaiting_link"  # type: ignore[union-attr]  # 주인 링크가 붙은 글은 그대로
    assert {f"link:{naver}", f"link:{toss}", f"link:{toss_tried}"} <= set(resolved)
    assert any("기다리던 링크 요청 정리" in m for m in _events(bot))
    # 다시 돌아도 또 정리하지 않음
    assert bot._clear_owner_link_requests() == 0
    # 스위치를 켜면 정리하지 않음
    bot.settings.publish.owner_link_requests = True
    again = _enqueue(bot, _naver("5556667778"))
    bot.db.update_queue_item(again, status="awaiting_link")
    assert bot._clear_owner_link_requests() == 0
    assert bot.db.get_queue_item(again).status == "awaiting_link"  # type: ignore[union-attr]
