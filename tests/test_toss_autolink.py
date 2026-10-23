"""토스쇼핑 쉐어링크 자동 링크 (주인 10/23 — 토스 딜 전부 자동) — 딜봇 쪽.

딜봇은 Blog-Auto 받은편지함에 {"toss_link": …} 를 남기고, Blog-Auto 가 toss_results.jsonl 에 답한다 (blogauto/tosslink.py).
- 쉐어링크가 오면 모양(toss.shopping/_m/…)과 상품(tacaId)을 보고 주인이 붙인 것처럼 붙인다 (주인 알림 없음, 링크는 열지 않음)
- 판매 중이 아니면 주인에게 묻지 않고 내린다 · 오류·로그인·시간 초과면 예전처럼 주인에게 링크 요청 · 로그인은 카톡 한 번
- 남의 쉐어링크(toss.im/_m/…)처럼 상품 주소가 아닌 딜·꺼짐은 예전 흐름 · 같은 답을 두 번 읽어도 한 번만
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from dealbot import autolink
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product

TACA = "2282607549"
URL = f"https://toss.shopping/t/{TACA}"
LINK = "https://toss.shopping/_m/AbC12xyz"


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False
    settings.autolink.toss.enabled = True
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
    yield b
    b.db.close()


def _product(url: str = URL, **kw: Any) -> Product:
    base: dict[str, Any] = dict(
        source="algumon", product_id=f"toss:{url.rstrip('/').rsplit('/', 1)[-1]}", shop="toss",
        name="잘풀리는집 퀼팅 화장지 30m x 24롤", price=15900, url=url, recommend_count=9,
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
    with (settings.data_dir / autolink.TOSS_RESULTS_FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _ok(qid: int, job: dict, **kw: Any) -> dict:
    return {"id": qid, "job": job["job"], "url": job["url"], "ok": True, "link": LINK, "taca_id": int(TACA),
            "item_id": 2270532369, **kw}


async def _ask(bot: DealBot, settings: Settings, product: Product | None = None) -> tuple[int, dict]:
    qid = _enqueue(bot, product)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]
    return qid, [u["toss_link"] for u in _inbox(settings) if "toss_link" in u][-1]


async def test_toss_deal_asks_blogauto_instead_of_owner(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    assert job["id"] == qid and job["url"] == URL and job["job"] and job["name"].startswith("잘풀리는집")
    assert not [u for u in _inbox(settings) if "connect_link" in u]  # 쇼핑커넥트 일이 아님
    assert bot.sent == []  # type: ignore[attr-defined]
    assert not [u for u in _inbox(settings) if "owner_notice" in u]
    assert bot.db.queue_counts().get("awaiting_link") is None


async def test_share_link_is_attached_like_an_owner_reply(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, _ok(qid, job))
    assert await bot.autolink_tick() == 1
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.affiliate_url == LINK  # type: ignore[union-attr]
    assert item.deal.product.extra.get("auto_link") is True  # type: ignore[union-attr]
    assert bot.db.kv_get(f"mylink:toss:{TACA}") == LINK  # 같은 상품은 다음에 묻지 않고 재사용
    assert bot.sent == [] and not [u for u in _inbox(settings) if "owner_notice" in u]  # type: ignore[attr-defined]
    # 같은 답을 다시 읽어도 한 번만
    bot.db.kv_set(autolink.KV_TOSS_POS, "0")
    assert await bot.autolink_tick() == 0


@pytest.mark.parametrize("bad", [
    {"link": "https://toss.im/shopping/xyz"},              # 쉐어링크 모양이 아님
    {"link": URL},                                          # 딜 주소 그대로
    {"taca_id": 999},                                       # 다른 상품
])
async def test_wrong_link_is_never_attached(bot: DealBot, settings: Settings, bad: dict) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, _ok(qid, job, **bad))
    await bot.autolink_tick()
    item = bot.db.get_queue_item(qid)
    assert item.status == "awaiting_link" and item.deal.affiliate_url is None  # type: ignore[union-attr]


async def test_reply_for_another_job_or_url_is_ignored(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, _ok(qid, {**job, "job": "other"}))
    assert await bot.autolink_tick() == 0
    assert bot.db.get_queue_item(qid).status == autolink.STATUS  # type: ignore[union-attr]
    _reply(settings, _ok(qid, {**job, "url": "https://toss.shopping/t/1"}))
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]


async def test_not_on_sale_is_skipped_without_asking(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "url": URL, "ok": False, "reason": "unavailable"})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "skipped"  # type: ignore[union-attr]
    assert bot.sent == []  # type: ignore[attr-defined]


async def test_login_stops_toss_alerts_once_and_falls_back(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    _reply(settings, {"id": qid, "job": job["job"], "url": URL, "ok": False, "reason": "login", "retry_at": time.time() + 3600})
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    notes = [u["owner_notice"] for u in _inbox(settings) if "owner_notice" in u and u["owner_notice"]["kind"] == "autolink_stop"]
    assert len(notes) == 1 and "토스" in notes[0]["text"] and "affiliate-login.sh 토스" in notes[0]["text"]
    # 멈춘 동안 새 토스 딜은 묻지 않고 바로 예전처럼, 네이버 쇼핑커넥트 멈춤과는 따로
    assert not bot.db.kv_get(autolink.KV_STOP)
    asked = len([u for u in _inbox(settings) if "toss_link" in u])
    bot.db.update_queue_item(qid, status="skipped")
    qid2 = _enqueue(bot, _product(url="https://toss.shopping/t/78829901"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid2).status == "awaiting_link"  # type: ignore[union-attr]
    assert len([u for u in _inbox(settings) if "toss_link" in u]) == asked
    bot._note_toss_stop({"reason": "login"})  # 같은 날 또 와도 카톡은 한 번
    assert len([u for u in _inbox(settings) if "owner_notice" in u and u["owner_notice"]["kind"] == "autolink_stop"]) == 1
    # Blog-Auto 에서 다시 로그인하고 resume → 다시 묻는다
    _reply(settings, {"resume": True, "at": time.time()})
    await bot.autolink_tick()
    bot.db.update_queue_item(qid2, status="skipped")
    await _ask(bot, settings, _product(url="https://toss.shopping/t/114368443"))


async def test_no_reply_in_time_falls_back_and_cancels_with_toss_cancel(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    bot.settings.autolink.toss.wait_minutes = 0
    await bot.autolink_tick()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert [u["toss_cancel"]["job"] for u in _inbox(settings) if "toss_cancel" in u] == [job["job"]]
    assert not [u for u in _inbox(settings) if "connect_cancel" in u]
    _reply(settings, _ok(qid, job))  # 늦게 온 답은 무시 (이미 주인에게 넘어감)
    assert await bot.autolink_tick() == 0
    assert bot.db.get_queue_item(qid).deal.affiliate_url is None  # type: ignore[union-attr]


async def test_skip_and_owner_link_cancel_the_toss_job(bot: DealBot, settings: Settings) -> None:
    qid, job = await _ask(bot, settings)
    bot.skip_item(qid)
    assert [u["toss_cancel"]["job"] for u in _inbox(settings) if "toss_cancel" in u] == [job["job"]]
    bot.db.update_queue_item(qid, status="expired")
    qid2, job2 = await _ask(bot, settings, _product(price=12000))
    await bot.attach_link(qid2, "https://toss.im/_m/OwnerMade")
    assert job2["job"] in [u["toss_cancel"]["job"] for u in _inbox(settings) if "toss_cancel" in u]
    assert bot.db.get_queue_item(qid2).deal.affiliate_url == "https://toss.im/_m/OwnerMade"  # type: ignore[union-attr]


async def test_disabled_or_share_link_deals_keep_the_old_flow(bot: DealBot, settings: Settings) -> None:
    bot.settings.autolink.toss.enabled = False
    qid = _enqueue(bot)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    bot.db.update_queue_item(qid, status="skipped")
    bot.settings.autolink.toss.enabled = True
    # 남의 쉐어링크·앱 화면 주소는 Blog-Auto 가 열지 않으므로 바로 주인에게
    for url in ("https://toss.im/_m/P4Qr1ope", "https://service.toss.im/shopping/p/27098529"):
        qid = _enqueue(bot, _product(url=url, product_id=f"toss:url:{url[-6:]}"))
        assert await bot.process_queue_once()
        assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
        bot.db.update_queue_item(qid, status="skipped")
    assert not [u for u in _inbox(settings) if "toss_link" in u]


async def test_saved_owner_link_is_reused_without_asking(bot: DealBot, settings: Settings) -> None:
    bot.db.kv_set(f"mylink:toss:{TACA}", "https://toss.shopping/_m/Saved1")
    qid = _enqueue(bot)
    await bot.process_queue_once()
    assert not [u for u in _inbox(settings) if "toss_link" in u]
    assert bot.db.get_queue_item(qid).deal.affiliate_url == "https://toss.shopping/_m/Saved1"  # type: ignore[union-attr]


def test_toss_key_and_verify_rules() -> None:
    assert autolink.toss_key(URL) == ("t", TACA)
    assert autolink.toss_key("https://toss.shopping/i/85495093") == ("i", "85495093")
    for url in ("https://toss.im/_m/abc", "http://toss.shopping/t/1", "https://evil.com/t/1", "https://toss.shopping/t/1/x", None):
        assert autolink.toss_key(url) is None
    row = {"taca_id": int(TACA)}
    assert autolink.verify_toss_link(LINK, URL, row) is None
    assert autolink.verify_toss_link("https://toss.im/_m/Ab_c-1", URL, row) is None
    assert autolink.verify_toss_link(LINK + "?x=1", URL, row) == "not_toss_short"
    assert autolink.verify_toss_link(LINK, URL, {"taca_id": 1}) == "mismatch"
    assert autolink.verify_toss_link(LINK, "https://toss.shopping/i/5", {"item_id": 5}) is None
    assert autolink.verify_toss_link(LINK, "https://toss.im/_m/x", row) == "deal_url"
