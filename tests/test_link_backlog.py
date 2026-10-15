"""링크 요청 자리 (주인 결정 10/15): 6 → 15칸, 유효 시간 12 → 24시간, 자리가 다 차면 점수가 가장 낮은 글을 더 좋은 딜로 교체.

10/10 뒤로 채널에 상품 딜이 하나도 안 올라감 — 뽐뿌 딜 대부분이 주인 링크가 필요한 네이버·토스 딜인데 6칸이 늘 차 있어
새 딜이 모두 'manual link backlog full (6)' 로 버려졌다 (서버 10/15, 하루 24건)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import TelegramError

from dealbot.app import DealBot
from dealbot.config import Settings, load_settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring.admin import SAFE_CHUNK
from dealbot.publisher.templates import TemplateRenderer
from dealbot.storage.db import REPLACED
from dealbot.utils.timeutil import to_iso, utcnow

ROOT = Path(__file__).resolve().parent.parent


class _Tg:
    """관리자 챗 텔레그램 가짜: 보낸 글과 고친 글을 남긴다. fail_edit 이면 고치기가 TelegramError."""

    def __init__(self, *, fail_edit: bool = False) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[int, str]] = []
        self.fail_edit = fail_edit

    async def send_message(self, **kw: Any) -> Any:
        self.sent.append(kw["text"])
        return SimpleNamespace(message_id=100 + len(self.sent))

    async def edit_message_text(self, **kw: Any) -> Any:
        if self.fail_edit:
            raise TelegramError("Message to edit not found")
        self.edits.append((kw["message_id"], kw["text"]))
        return True

    async def pin_chat_message(self, **_kw: Any) -> bool:
        return True


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    settings.publish.dry_run = False  # 연습 모드는 링크를 묻지 않으므로 실제 모드로
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 100
    b = DealBot(settings)
    yield b
    b.db.close()


def _toss(key: str, price: int = 10000) -> Product:
    return Product(source="fake", product_id=f"toss:{key}", shop="toss", name=f"토스 상품 {key}", price=price,
                   url=f"https://toss.im/_m/{key}")


def _waiting(bot: DealBot, key: str, score: float, *, age_h: float = 1.0) -> int:
    """이미 내 링크를 기다리고 있는 글 (점수·나이를 정해서)."""
    at = utcnow() - timedelta(hours=age_h)
    assert bot.db.enqueue(Deal(_toss(key), DealVerdict(is_deal=True, score=score)), score=score, now=at)
    item = bot.db.items_for_product(f"toss:{key}", ("pending",))[0]
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual link required", now=at)
    return item.id


def _fill(bot: DealBot, scores: list[float]) -> list[int]:
    """링크 요청 자리를 채운다. 앞의 것이 더 오래된 글."""
    return [_waiting(bot, f"w{i}", s, age_h=10 - i * 0.1) for i, s in enumerate(scores)]


async def _offer(bot: DealBot, key: str, score: float) -> int:
    """새 딜이 줄 서서 링크를 물을 차례가 됨. 그 글 번호."""
    assert bot.db.enqueue(Deal(_toss(key), DealVerdict(is_deal=True, score=score)), score=score)
    item = bot.db.items_for_product(f"toss:{key}", ("pending",))[0]
    assert await bot.process_queue_once()
    return item.id


def _status(bot: DealBot, qid: int) -> tuple[str, str | None]:
    it = bot.db.get_queue_item(qid)
    assert it is not None
    return it.status, it.last_error


def test_config_has_the_owner_numbers() -> None:
    s = load_settings(ROOT / "config.yaml", load_env=False)
    p = s.publish
    assert p.max_awaiting_links == 15 and p.manual_link_ttl_hours == 24
    assert p.swap_min_score_gain == 5 and p.max_awaiting_reviews == 6


async def test_fifteen_link_requests_then_backlog_full(bot: DealBot) -> None:
    for i in range(16):  # 같은 점수: 바꿀 이유가 없어 16번째는 예전처럼 건너뜀
        await _offer(bot, f"n{i}", 10)
    counts = bot.db.queue_counts()
    assert counts == {"awaiting_link": 15, "skipped": 1}, counts
    skipped = bot.db.items_for_product("toss:n15", ("skipped",))[0]
    assert skipped.last_error == "manual link backlog full (15)"


async def test_swap_needs_the_score_margin(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    low = await _offer(bot, "small", 14.9)  # 가장 낮은 10 보다 4.9 높음 → 기준(5) 미만
    assert _status(bot, low) == ("skipped", "manual link backlog full (15)")
    assert all(_status(bot, i)[0] == "awaiting_link" for i in ids)
    new = await _offer(bot, "big", 15)  # 정확히 5 높음 → 교체
    assert _status(bot, new) == ("awaiting_link", "manual link required")
    assert _status(bot, ids[0]) == ("skipped", f"{REPLACED} #{new} (score 10→15)")  # 같은 점수면 가장 오래된 글
    assert bot.db.queue_counts()["awaiting_link"] == 15
    assert any(f"replaced by #{new}" in e["message"] for e in bot.db.recent_events(10, level="INFO"))


async def test_swap_picks_lowest_score_then_oldest(bot: DealBot) -> None:
    scores = [30, 20, 12, 40, 12, 50, 25, 25, 25, 25, 25, 25, 25, 25, 25]
    ids = _fill(bot, scores)
    first = await _offer(bot, "a", 40)
    assert _status(bot, ids[2])[0] == "skipped" and _status(bot, ids[4])[0] == "awaiting_link"  # 12 둘 중 오래된 쪽
    second = await _offer(bot, "b", 40)
    assert _status(bot, ids[4]) == ("skipped", f"{REPLACED} #{second} (score 12→40)")
    third = await _offer(bot, "c", 24.9)  # 이제 가장 낮은 것은 20 → 4.9 차이라 교체 없음
    assert _status(bot, third)[0] == "skipped" and _status(bot, ids[1])[0] == "awaiting_link"
    assert _status(bot, first)[0] == "awaiting_link" and bot.db.queue_counts()["awaiting_link"] == 15


async def test_swap_can_be_turned_off(bot: DealBot) -> None:
    bot.settings.publish.swap_min_score_gain = None
    ids = _fill(bot, [1] * 15)
    new = await _offer(bot, "big", 90)
    assert _status(bot, new)[1] == "manual link backlog full (15)" and _status(bot, ids[0])[0] == "awaiting_link"


async def test_replaced_deal_is_not_a_decline(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    now = utcnow()
    assert bot.db.declined_since("toss:w0", now - timedelta(days=3)) is None  # 주인이 넘긴 게 아님
    assert not bot._declined_recently(_toss("w0"), now)
    assert bot._not_queued_reason(_toss("w0"), now) is None  # 같은 상품이 다시 나오면 다시 줄 설 수 있음
    # 주인이 /skip 한 것은 그대로 '넘김'
    bot.skip_item(ids[1])
    assert bot.db.declined_since("toss:w1", now - timedelta(days=3)) is not None
    # 저녁 요약 이유는 '기타' 가 아니라 따로
    counts = bot.skipped_deal_counts(now - timedelta(days=1))
    assert counts.get("더 좋은 딜로 교체") == 1 and "기타" not in counts


async def test_no_swap_of_a_deal_the_owner_answered_meanwhile(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    ids = _fill(bot, [10] + [20] * 14)
    real = bot.db.replace_awaiting_link

    def owner_first(item_id: int, error: str, now: Any = None) -> bool:
        # 가장 낮은 글을 고른 바로 뒤, 내리기 전에 주인이 링크를 붙임 (상태가 pending 으로 바뀜)
        if item_id == ids[0] and bot.db.get_queue_item(item_id).status == "awaiting_link":  # type: ignore[union-attr]
            bot.db.set_queue_link(item_id, "https://toss.im/_m/OWNER")
        return real(item_id, error, now)

    monkeypatch.setattr(bot.db, "replace_awaiting_link", owner_first)
    new = await _offer(bot, "big", 30)
    item = bot.db.get_queue_item(ids[0])
    assert item is not None and item.status == "pending" and item.last_error is None  # 주인 링크가 그대로
    assert item.deal.affiliate_url == "https://toss.im/_m/OWNER"
    # 주인이 링크를 붙여 자리가 하나 났으니 새 딜은 아무것도 내리지 않고 물음
    assert _status(bot, new)[0] == "awaiting_link"
    assert all(_status(bot, i)[0] == "awaiting_link" for i in ids[1:])


async def test_no_swap_of_a_waiting_deal_that_already_has_a_link(bot: DealBot) -> None:
    ids = _fill(bot, [10] + [20] * 14)
    # 링크는 붙었는데 상태는 아직 바뀌기 전 (붙이는 중)
    item = bot.db.get_queue_item(ids[0])
    assert item is not None
    item.deal.affiliate_url = "https://toss.im/_m/OWNER"
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual link required", deal=item.deal)
    assert not bot.db.replace_awaiting_link(ids[0], "replaced by #99 (score 10→30)")  # 조건부 UPDATE 가 막음
    new = await _offer(bot, "big", 30)
    assert _status(bot, ids[0]) == ("awaiting_link", "manual link required")
    assert _status(bot, ids[1]) == ("skipped", f"{REPLACED} #{new} (score 20→30)")  # 다음으로 낮은 글


async def test_replaced_request_message_is_edited_and_kakao_cancelled(bot: DealBot) -> None:
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    resolved: list[str] = []
    bot.notifier.resolve = lambda *keys: resolved.extend(keys)  # type: ignore[method-assign]
    ids = _fill(bot, [10] * 15)
    bot.db.kv_set(f"link_notice:{ids[0]}", "77")
    new = await _offer(bot, "big", 30)
    edit = next(t for mid, t in tg.edits if mid == 77)
    assert f"더 좋은 딜로 바꿨어요 #{ids[0]}" in edit and f"#{new} 딜을 대신 물었어요" in edit
    assert f"/link {ids[0]} 링크" in edit and "까지" in edit
    assert f"link:{ids[0]}" in resolved  # 아직 안 간 링크 요청 카톡은 취소
    assert any(f"#{new}" in t for t in tg.sent)  # 새 딜은 예전처럼 링크 요청


async def test_edit_failure_does_not_stop_the_swap(bot: DealBot) -> None:
    bot.notifier.bot, bot.notifier.chat_id = _Tg(fail_edit=True), 1  # type: ignore[assignment]
    ids = _fill(bot, [10] * 15)
    bot.db.kv_set(f"link_notice:{ids[0]}", "77")
    new = await _offer(bot, "big", 30)
    assert _status(bot, ids[0])[0] == "skipped" and _status(bot, new)[0] == "awaiting_link"


async def test_owner_link_on_replaced_deal_within_ttl_is_posted(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    msg = await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    assert "상품 링크 완료" in msg and "토스 상품 w0" in msg
    item = bot.db.get_queue_item(ids[0])
    assert item is not None and item.status == "pending" and item.deal.affiliate_url == "https://toss.im/_m/MINE"
    assert bot.db.queue_counts()["awaiting_link"] == 15  # 다시 묻지 않으니 링크 자리는 그대로
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(ids[0]).status == "published"  # type: ignore[union-attr]
    assert bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/MINE"


async def test_sold_out_replaced_deal_is_not_reopened_by_a_late_link(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    out = await bot._on_sold_out(_toss("w0"), via="게시판에 품절/종료 표시")
    assert ids[0] in out["cancelled"] and _status(bot, ids[0]) == ("expired", "sold out (게시판에 품절/종료 표시)")
    assert "'expired' 상태라" in await bot.attach_link(ids[0], "https://toss.im/_m/MINE")


async def test_owner_link_on_replaced_deal_after_ttl_is_refused_clearly(bot: DealBot) -> None:
    ids = [_waiting(bot, "old", 1, age_h=23)] + _fill(bot, [10] * 14)
    await _offer(bot, "big", 30)
    assert _status(bot, ids[0])[0] == "skipped"
    bot.db._conn.execute("UPDATE deal_queue SET created_at = ? WHERE id = ?",
                         (to_iso(utcnow() - timedelta(hours=25)), ids[0]))
    msg = await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    assert "유효 시간이 지나" in msg and _status(bot, ids[0])[0] == "skipped"
    # 다른 이유로 건너뛴 글은 예전과 같은 말
    bot.db.update_queue_item(ids[0], status="skipped", error="skipped by admin")
    assert "'skipped' 상태라" in await bot.attach_link(ids[0], "https://toss.im/_m/MINE")


async def test_owner_link_on_replaced_deal_goes_to_the_requeued_twin(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    # 내린 상품이 게시판에 다시 나와 새 글로 줄 섬 (넘긴 게 아니라 다시 물을 수 있음)
    assert bot.db.enqueue(Deal(_toss("w0"), DealVerdict(is_deal=True, score=10)), score=10)
    twin = bot.db.items_for_product("toss:w0", ("pending",))[0]
    msg = await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    assert f"#{twin.id} 번으로 다시 줄 서 있어" in msg and "상품 링크 완료" in msg
    assert bot.db.get_queue_item(twin.id).deal.affiliate_url == "https://toss.im/_m/MINE"  # type: ignore[union-attr]
    assert _status(bot, ids[0])[0] == "skipped"


async def test_link_requests_expire_after_24_hours(bot: DealBot) -> None:
    young = _waiting(bot, "young", 10, age_h=23)
    old = _waiting(bot, "old", 10, age_h=25)
    await bot.process_queue_once()
    assert _status(bot, young)[0] == "awaiting_link"
    assert _status(bot, old) == ("expired", "unanswered")


def _review(bot: DealBot, key: str) -> None:
    assert bot.db.enqueue(Deal(_toss(key), DealVerdict(is_deal=True)), score=1)
    item = bot.db.items_for_product(f"toss:{key}", ("pending",))[0]
    bot.db.update_queue_item(item.id, status="awaiting_approval", error="needs approval: x")


def test_review_cap_stays_six_and_links_come_first(bot: DealBot) -> None:
    assert bot._review_backlog_full() is None
    for i in range(6):
        _review(bot, f"r{i}")
    assert bot._review_backlog_full() == 6  # 링크 자리를 15로 늘려도 확인 요청은 6개까지
    bot.db._conn.execute("DELETE FROM deal_queue")
    _fill(bot, [10] * 12)
    _review(bot, "r0")
    _review(bot, "r1")
    assert bot._review_backlog_full() is None  # 12 + 2 = 14
    _review(bot, "r2")
    assert bot._review_backlog_full() == 15  # 링크 대기와 합쳐 15
    bot.settings.publish.max_awaiting_links = 0
    assert bot._review_backlog_full() is None  # 링크 상한을 끄면 확인 요청 상한(6)만
    bot.settings.publish.max_awaiting_reviews = 0
    for i in range(3, 9):
        _review(bot, f"r{i}")
    assert bot._review_backlog_full() is None


async def test_summaries_count_replaced_and_dropped_deals(bot: DealBot, repo_root: Path) -> None:
    _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)  # 교체 1
    await _offer(bot, "small", 12)  # 못 물어봄
    bot.db.enqueue(Deal(_toss("small"), DealVerdict(is_deal=True, score=12)), score=12)  # 게시판이 다시 돌려줌 → 또 못 물어봄
    await bot.process_queue_once()
    await _offer(bot, "small2", 11)
    now = utcnow()
    s = bot.db.summary(now - timedelta(days=1), now + timedelta(seconds=1))
    assert (s.link_replaced, s.link_backlog_full) == (1, 2)  # 상품 수로
    text = TemplateRenderer(repo_root / "templates").render("daily_summary.j2", s=s, tz="Asia/Seoul")
    assert "링크 요청 자리가 차서: 더 좋은 딜로 바꾼 딜 1개 · 못 묻고 건너뛴 딜 2개" in text
    sent: list[tuple[str, str]] = []
    bot.notifier.summary = lambda cat, text, replace=False: sent.append((cat, text))  # type: ignore[method-assign]
    await bot.owner_summary()
    lines = dict(sent)
    assert "요청 밀림" not in lines["day"] and "교체" not in lines["day"]  # 잘리는 '안 올린 딜' 문장이 아니라 따로
    assert "내 링크를 기다리는 딜이 15개" in lines["waiting"]
    assert "오늘 링크 요청 자리(15개)가 차서 더 좋은 딜로 바꾼 게 1개, 못 묻고 건너뛴 게 2개예요." in lines["waiting"]
    # 아무 일도 없던 날은 그 문장 없음
    quiet = bot.db.summary(now + timedelta(days=2), now + timedelta(days=3))
    assert (quiet.link_replaced, quiet.link_backlog_full) == (0, 0)
    assert "링크 요청 자리가 차서" not in TemplateRenderer(repo_root / "templates").render("daily_summary.j2", s=quiet, tz="Asia/Seoul")


def test_pinned_notice_fits_one_message_with_fifteen_slots(bot: DealBot) -> None:
    for i in range(15):
        p = _toss(f"long{i}")
        p.name = f"아주 긴 이름의 네이버 스마트스토어 상품 {i} 대용량 묶음 특가 무료배송 한정 수량 이벤트"
        p.url = f"https://smartstore.naver.com/somestore/products/{9000000000 + i}?NaPm=" + "x" * 220
        bot.db.enqueue(Deal(p, DealVerdict(is_deal=True, score=10)), score=10)
        item = bot.db.items_for_product(p.product_id, ("pending",))[0]
        bot.db.update_queue_item(item.id, status="awaiting_link", error="manual link required")
    for i in range(6):
        _review(bot, f"r{i}")
    full = bot.reporter.pending_text()
    assert len(full) > SAFE_CHUNK  # 원본 주소까지 넣으면 한 메시지를 넘음
    assert "(15건)" in full and full.count("원본 주소:") == 15  # /pending 은 15칸을 다 (나눠서 보냄)
    notice = bot.reporter.pending_text(notice=True)
    assert len("📌 " + notice) <= SAFE_CHUNK and "(15건)" in notice and "원본 주소:" not in notice
    assert all(f"#{it.id} " in notice for it in bot.db.awaiting_items(50))
