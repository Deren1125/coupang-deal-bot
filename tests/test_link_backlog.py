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
from dealbot.authenticity import AuthResult
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, NLRouterConfig, Settings, load_settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring.admin import SAFE_CHUNK
from dealbot.monitoring.nl import MAX_BUTTONS, MAX_CANDIDATES, NLRouter, Ref
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


def _toss(key: str, price: int = 10000, *, votes: int | None = None) -> Product:
    return Product(source="fake", product_id=f"toss:{key}", shop="toss", name=f"토스 상품 {key}", price=price,
                   url=f"https://toss.im/_m/{key}", recommend_count=votes)


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


ALL_STATUSES = ("pending", "awaiting_link", "awaiting_approval", "awaiting_autolink", "skipped", "expired", "published", "failed")


@register("fake_backlog_board")
class _Board(BaseCollector):
    """게시판 가짜: 테스트가 정해 둔 상품을 돌려준다 (5분마다 같은 글을 다시 봄 — 추천 수만 늘어남)."""

    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(_Board.products)


@pytest.fixture
def board_bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="board", type="fake_backlog_board", interval_minutes=5)]
    settings.publish.dry_run = False
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 100
    _Board.products = []
    b = DealBot(settings)
    yield b
    b.db.close()


async def _drain(bot: DealBot) -> None:
    for _ in range(30):
        if not await bot.process_queue_once():
            return


def test_config_has_the_owner_numbers() -> None:
    s = load_settings(ROOT / "config.yaml", load_env=False)
    p = s.publish
    assert p.max_awaiting_links == 15 and p.manual_link_ttl_hours == 24
    assert p.swap_min_score_gain == 5 and p.max_awaiting_reviews == 6
    assert p.review_ttl_hours == 12  # 확인(/ok) 요청은 링크 요청과 따로 예전 그대로 (리뷰 10/15)


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
    # 링크 요청 유효 시간 안에는 다시 줄 세우지 않음 (내린 글의 메시지에 답하면 그대로 올라감 — 리뷰 10/15: 다시 묻고 또 밀어내며 돌고 돎)
    assert bot._not_queued_reason(_toss("w0"), now) == "replaced"
    # 그 시간이 지나면 넘긴 게 아니니 같은 상품이 다시 나오면 다시 줄 설 수 있음 (w0 은 10시간 전에 줄 섬 → 14시간 뒤면 24시간)
    assert bot._not_queued_reason(_toss("w0"), now + timedelta(hours=14, minutes=1)) is None
    assert bot._not_queued_reason(_toss("w0"), now + timedelta(hours=13, minutes=59)) == "replaced"
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


def _requeue_twin(bot: DealBot, key: str, status: str, score: float = 10) -> int:
    """내린 상품이 (유효 시간이 지나 — 또는 더 싸져서) 게시판에 다시 나와 새 글로 줄 섬."""
    assert bot.db.enqueue(Deal(_toss(key), DealVerdict(is_deal=True, score=score)), score=score)
    twin = bot.db.items_for_product(f"toss:{key}", ("pending",))[0]
    if status != "pending":
        bot.db.update_queue_item(twin.id, status=status, error="x")
    return twin.id


async def test_owner_link_on_replaced_deal_goes_to_the_requeued_twin(bot: DealBot) -> None:
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    twin = _requeue_twin(bot, "w0", "awaiting_link")
    msg = await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    note = f"↪️ #{ids[0]} 번과 같은 상품이 지금 #{twin} 번으로 다시 줄 서 있어 #{twin} 번으로 받아요."
    assert note in tg.sent  # 어느 글로 받았는지는 따로 한 줄 (답은 그 글 기준)
    assert f"상품 링크 완료 #{twin}" in msg and "붙였어요" not in msg
    assert bot.db.get_queue_item(twin).deal.affiliate_url == "https://toss.im/_m/MINE"  # type: ignore[union-attr]
    assert _status(bot, ids[0])[0] == "skipped"


async def test_link_on_replaced_deal_whose_twin_cannot_take_it_does_not_claim_success(bot: DealBot) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    twin = _requeue_twin(bot, "w0", "awaiting_approval")  # 다시 줄 선 글이 확인(/ok)을 기다림
    msg = await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    assert msg.startswith(f"#{twin} 번은 확인을 기다리는 글이라 링크가 아니라 답이 필요합니다") and "붙였어요" not in msg
    assert bot.db.get_queue_item(twin).deal.affiliate_url is None  # type: ignore[union-attr]
    assert _status(bot, twin)[0] == "awaiting_approval" and _status(bot, ids[0])[0] == "skipped"
    # 링크를 받을 수 있는 글이어도 잘못된 주소면 그 이유만 (붙였다는 말 없이)
    bot.db.update_queue_item(twin, status="awaiting_link", error="manual link required")
    msg = await bot.attach_link(ids[0], "https://t.me/mychannel/12")
    assert msg.startswith("텔레그램 주소(t.me)는") and "붙였어요" not in msg
    assert bot.db.get_queue_item(twin).deal.affiliate_url is None  # type: ignore[union-attr]
    msg = await bot.attach_link(ids[0], "toss.im/_m/MINE")
    assert msg.startswith("링크는 http:// 또는 https:// 로 시작해야") and _status(bot, twin)[0] == "awaiting_link"


async def test_owner_link_sent_while_the_twin_is_being_checked_is_kept(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """다시 줄 선 글(대기)을 발행 루프가 확인하는 사이(품절 확인을 기다림) 주인이 내린 글에 링크를 보냄:
    루프가 예전 딜로 링크 요청을 하며 주인 링크를 지우고 또 묻지 않는다 (리뷰 10/15). 다른 글을 내리지도 않음."""
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    twin = _requeue_twin(bot, "w0", "pending", score=40)

    async def owner_answers_meanwhile(p: Product) -> bool:
        if p.product_id == "toss:w0":
            await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
        return False

    monkeypatch.setattr(bot, "_page_sold_out", owner_answers_meanwhile)
    asked_before = sum("내 링크가 필요합니다" in t for t in tg.sent)
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(twin)
    assert item is not None and item.status == "pending" and item.deal.affiliate_url == "https://toss.im/_m/MINE"
    assert sum("내 링크가 필요합니다" in t for t in tg.sent) == asked_before  # 또 묻지 않음
    assert _status(bot, ids[1])[0] == "awaiting_link"  # 자리를 만들려고 다른 글을 내리지 않음
    assert await bot.process_queue_once()
    assert _status(bot, twin)[0] == "published" and bot.db.recent_posts()[0]["affiliate_url"] == "https://toss.im/_m/MINE"


async def test_owner_skip_while_the_deal_is_being_checked_stays_a_skip(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    _fill(bot, [20] * 15)
    assert bot.db.enqueue(Deal(_toss("late"), DealVerdict(is_deal=True, score=21)), score=21)  # 바꿀 만큼 높지 않음 → 자리 없음
    late = bot.db.items_for_product("toss:late", ("pending",))[0].id

    async def owner_skips_meanwhile(p: Product) -> bool:
        bot.skip_item(late)
        return False

    monkeypatch.setattr(bot, "_page_sold_out", owner_skips_meanwhile)
    assert await bot.process_queue_once()
    assert _status(bot, late) == ("skipped", "skipped by admin")  # 'manual link backlog full' 로 덮어쓰지 않음
    assert bot.db.declined_since("toss:late", utcnow() - timedelta(days=1)) is not None  # 주인이 넘긴 것으로 남음


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


def test_review_slots_are_their_own_six(bot: DealBot) -> None:
    """확인(/ok) 요청 자리는 링크 요청 자리와 따로 6칸 (리뷰 10/15: 교체로 링크 15칸이 늘 차 있어 합쳐 세면 하나도 못 보냄)."""
    assert bot._review_backlog_full() is None
    _fill(bot, [10] * 15)  # 링크 자리가 다 차 있어도
    for i in range(5):
        _review(bot, f"r{i}")
    assert bot._review_backlog_full() is None  # 확인 요청은 보냄
    _review(bot, "r5")
    assert bot._review_backlog_full() == 6  # 링크 자리를 15로 늘려도 확인 요청은 6개까지
    bot.settings.publish.max_awaiting_links = 0
    assert bot._review_backlog_full() == 6  # 링크 상한과 상관없이
    bot.settings.publish.max_awaiting_reviews = 0
    _review(bot, "r6")
    assert bot._review_backlog_full() is None  # 0 = 제한 없음


async def test_review_request_is_sent_while_link_slots_are_full(bot: DealBot) -> None:
    _fill(bot, [10] * 15)
    assert bot.db.enqueue(Deal(_toss("open"), DealVerdict(is_deal=True, score=10)), score=10)
    item = bot.db.items_for_product("toss:open", ("pending",))[0]
    assert await bot._request_deal_review(item, item.deal, AuthResult("unknown", "공식 판매처 확인 안 됨"))
    assert _status(bot, item.id)[0] == "awaiting_approval"  # 예전엔 'review backlog full (15)' 로 버려짐


def _aged(bot: DealBot, key: str, status: str, age_h: float) -> int:
    at = utcnow() - timedelta(hours=age_h)
    assert bot.db.enqueue(Deal(_toss(key), DealVerdict(is_deal=True, score=10)), score=10, now=at)
    item = bot.db.items_for_product(f"toss:{key}", ("pending",))[0]
    bot.db.update_queue_item(item.id, status=status, error="x", now=at)
    return item.id


def test_review_and_autolink_waits_keep_the_12_hour_ttl(bot: DealBot) -> None:
    """링크 요청만 24시간으로 늘렸다 (주인 결정 10/15). 확인(/ok)·자동 링크 대기는 예전 그대로 12시간 —
    하루 지난 정보 글이 /ok 로 올라가거나 확인 요청이 자리를 오래 잡지 않게 (리뷰 10/15)."""
    link = _aged(bot, "link", "awaiting_link", 13)
    review = _aged(bot, "review", "awaiting_approval", 13)
    young_review = _aged(bot, "young", "awaiting_approval", 11)
    auto = _aged(bot, "auto", "awaiting_autolink", 13)
    assert bot._expire_queue(utcnow()) == 2
    assert _status(bot, link)[0] == "awaiting_link"
    assert _status(bot, review) == ("expired", "unanswered") and _status(bot, young_review)[0] == "awaiting_approval"
    assert _status(bot, auto) == ("expired", "autolink unanswered")
    # 설정으로 따로 바꿀 수 있음 (같게 두면 예전처럼 한 기준)
    bot.settings.publish.review_ttl_hours = 24
    assert bot._expire_queue(utcnow()) == 0 and _status(bot, young_review)[0] == "awaiting_approval"


async def test_summaries_count_replaced_and_dropped_deals(bot: DealBot, repo_root: Path) -> None:
    ids = _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)  # 교체 1
    await _offer(bot, "small", 12)  # 못 물어봄
    bot.db.enqueue(Deal(_toss("small"), DealVerdict(is_deal=True, score=12)), score=12)  # 게시판이 다시 돌려줌 → 또 못 물어봄
    await bot.process_queue_once()
    await _offer(bot, "small2", 11)
    now = utcnow()
    s = bot.db.summary(now - timedelta(days=1), now + timedelta(seconds=1))
    assert (s.link_replaced, s.link_backlog_full) == (1, 2)  # 상품 수로
    assert s.skipped == 0  # 링크 자리 줄에서 따로 세는 글은 '겹쳐서 건너뜀'에 다시 넣지 않음 (리뷰 10/15: 두 번 셈)
    text = TemplateRenderer(repo_root / "templates").render("daily_summary.j2", s=s, tz="Asia/Seoul")
    assert "링크 요청 자리가 차서: 더 좋은 딜로 바꾼 딜 1개 · 못 묻고 건너뛴 딜 2개" in text
    assert "겹쳐서 건너뜀 0건" in text
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
    bot.skip_item(ids[3])  # 다른 이유로 건너뛴 글은 '겹쳐서 건너뜀'에 그대로 셈
    s = bot.db.summary(now - timedelta(days=1), utcnow() + timedelta(seconds=1))
    assert s.skipped == 1 and (s.link_replaced, s.link_backlog_full) == (1, 2)


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


# ------------------------------------------------------------------ 리뷰 10/15 고친 것
async def test_swap_margin_holds_for_decimal_scores(bot: DealBot) -> None:
    """10.2 - 5.2 = 4.999… (소수 뺄셈 오차): 정확히 5 높은 딜도 바꾼다 (10 → 15 와 같게)."""
    ids = _fill(bot, [5.2] + [30] * 14)
    new = await _offer(bot, "dec", 10.2)
    assert _status(bot, new)[0] == "awaiting_link"
    assert _status(bot, ids[0]) == ("skipped", f"{REPLACED} #{new} (score 5.2→10.2)")
    low = await _offer(bot, "dec2", 15.1)  # 이제 가장 낮은 10.2 보다 4.9 높음 → 바꾸지 않음
    assert _status(bot, low) == ("skipped", "manual link backlog full (15)")


async def test_replaced_deal_coming_back_with_more_votes_is_not_asked_again(board_bot: DealBot) -> None:
    """리뷰 10/15 재현: 기다리는 15건(점수 10~24, 처음 물을 때 점수) + 새 딜 30 → 가장 낮은 글을 내림.
    그 뒤 30분(5분마다 6번) 게시판이 같은 글을 추천 +3 씩 다시 돌려줘도: 내린 상품은 다시 줄 서지 않고(그 메시지에 답하면 올라감),
    기다리는 글의 점수는 게시판 값으로 따라가 서로 밀어내지 않는다 → 새 링크 요청·메시지 고침 0건 (예전엔 30분에 요청 14건·고침 28건)."""
    bot = board_bot
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    ids = _fill(bot, [10 + i for i in range(15)])
    for i, qid in enumerate(ids):
        bot.db.kv_set(f"link_notice:{qid}", str(500 + i))
    big = await _offer(bot, "big", 30)

    def asked() -> int:
        return sum("내 링크가 필요합니다" in t for t in tg.sent)

    def flipped() -> int:
        return sum("더 좋은 딜로 바꿨어요" in t for _, t in tg.edits)

    assert _status(bot, ids[0])[0] == "skipped" and asked() == 1 and flipped() == 1
    for run in range(1, 7):
        _Board.products = [_toss(f"w{i}", votes=10 + i + 3 * run) for i in range(15)] + [_toss("big", votes=30 + 3 * run)]
        await bot.run_collector(bot.collectors[0])
        await _drain(bot)
    assert asked() == 1 and flipped() == 1  # 더 묻지도, 고치지도 않음
    assert [it.status for it in bot.db.items_for_product("toss:w0", ALL_STATUSES)] == ["skipped"]  # 다시 줄 서지 않음
    assert bot.db.queue_counts()["awaiting_link"] == 15
    assert bot.db.get_queue_item(ids[1]).score == 11 + 18  # type: ignore[union-attr]  # 기다리는 글 점수는 게시판 값으로
    assert bot.db.get_queue_item(big).score == 48  # type: ignore[union-attr]
    # 저녁 요약: 내린 글은 '더 좋은 딜로 교체' 1건, 다시 본 것은 따로 세지 않음
    counts = bot.skipped_deal_counts(utcnow() - timedelta(days=1))
    assert counts == {"더 좋은 딜로 교체": 1}, counts
    # 주인은 여전히 이김: 내린 글 메시지에 답하면 그대로 올라감
    assert "상품 링크 완료" in await bot.attach_link(ids[0], "https://toss.im/_m/MINE")
    assert _status(bot, ids[0])[0] == "pending"


async def test_waiting_scores_follow_the_board_before_a_swap(board_bot: DealBot) -> None:
    bot = board_bot
    ids = _fill(bot, [10] + [30] * 14)
    _Board.products = [_toss("w0", votes=20)]  # 처음 물을 때 10 이던 글이 지금은 추천 20
    await bot.run_collector(bot.collectors[0])
    assert bot.db.get_queue_item(ids[0]).score == 20  # type: ignore[union-attr]
    new = await _offer(bot, "n24", 24)  # 예전 점수(10)로는 바꿨겠지만 지금 점수(20)보다는 4 높을 뿐
    assert _status(bot, new) == ("skipped", "manual link backlog full (15)") and _status(bot, ids[0])[0] == "awaiting_link"
    new = await _offer(bot, "n25", 25)
    assert _status(bot, ids[0]) == ("skipped", f"{REPLACED} #{new} (score 20→25)")


async def test_replaced_deal_that_got_cheaper_is_a_new_deal(bot: DealBot) -> None:
    _fill(bot, [10] * 15)
    await _offer(bot, "big", 30)
    now = utcnow()
    assert bot._not_queued_reason(_toss("w0", price=10000), now) == "replaced"
    assert bot._not_queued_reason(_toss("w0", price=9000), now) is None  # 그사이 더 싸짐 → 새 딜 (넘긴 글과 같은 규칙)


async def test_replaced_message_keeps_the_source_address(bot: DealBot) -> None:
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    p = Product(source="fake", product_id="naver:777", shop="naver", name="네이버 스토어 상품 777", price=12900,
                url="https://smartstore.naver.com/goodstore/products/777",
                extra={"post_url": "https://www.ppomppu.co.kr/zboard/view.php?no=777"})
    at = utcnow() - timedelta(hours=11)
    assert bot.db.enqueue(Deal(p, DealVerdict(is_deal=True, score=1)), score=1, now=at)
    low = bot.db.items_for_product("naver:777", ("pending",))[0].id
    bot.db.update_queue_item(low, status="awaiting_link", error="manual link required", now=at)
    bot.db.kv_set(f"link_notice:{low}", "77")
    _fill(bot, [10] * 14)
    new = await _offer(bot, "big", 30)
    edit = next(t for mid, t in tg.edits if mid == 77)
    assert f"더 좋은 딜로 바꿨어요 #{low}" in edit and f"#{new} 딜을 대신 물었어요" in edit
    # 그래도 올리고 싶을 때 링크를 만들 주소 (내린 글은 /pending·고정 공지에서 빠짐)
    assert "원본 주소: https://smartstore.naver.com/goodstore/products/777" in edit
    assert "글: https://www.ppomppu.co.kr/zboard/view.php?no=777" in edit
    assert "쇼핑 커넥트 '상품 찾기' 검색어" in edit and "goodstore" in edit
    assert "smartstore.naver.com/goodstore" not in bot.reporter.pending_text()  # /pending 에는 이제 없음


def _nl_item(bot: DealBot, key: str, shop: str, status: str, age_h: float) -> int:
    url = f"https://toss.im/_m/{key}" if shop == "toss" else f"https://smartstore.naver.com/s{key}/products/{len(key)}"
    p = Product(source="fake", product_id=f"{shop}:{key}", shop=shop, name=f"상품 {key}", price=10000, url=url)
    at = utcnow() - timedelta(hours=age_h)
    assert bot.db.enqueue(Deal(p, DealVerdict(is_deal=True, score=10)), score=10, now=at)
    qid = bot.db.items_for_product(p.product_id, ("pending",))[0].id
    if status != "pending":
        bot.db.update_queue_item(qid, status=status, error="x", now=at)
    return qid


def test_nl_router_sees_every_waiting_deal_and_the_last_post(bot: DealBot) -> None:
    """리뷰 10/15: 말 명령 후보가 15개로 잘려 링크 15칸이 차면 새 링크 요청·방금 올린 글·대기 글이 빠졌다."""
    review = _nl_item(bot, "r", "toss", "awaiting_approval", 20)
    toss_old = _nl_item(bot, "t1", "toss", "awaiting_link", 19)
    naver = [_nl_item(bot, f"n{i}", "naver", "awaiting_link", 18 - i) for i in range(13)]
    toss_new = _nl_item(bot, "t2", "toss", "awaiting_link", 1)  # 가장 새 링크 요청 (15번째 링크 자리)
    posted = _nl_item(bot, "pub", "toss", "published", 2)
    pending = [_nl_item(bot, f"p{i}", "toss", "pending", 0.1) for i in range(3)]
    text = "토스 링크 https://toss.im/_m/MINE"
    cands = bot.nl.candidates(text, Ref())
    assert {review, toss_old, toss_new, posted, *naver, *pending} <= set(cands)
    # 그냥 보낸 토스 링크: 토스 딜이 둘이면 둘 다 버튼으로, 새 링크 요청부터 (예전엔 새 것이 안 보여 오래된 #toss_old 를 골랐다)
    d = bot.nl.decide({"intent": "/link", "post": None, "args": "https://toss.im/_m/MINE", "confidence": 0.95}, text, Ref(), cands)
    assert d.act == "ask" and [o.cmds for o in d.options] == [[f"/link {toss_new} https://toss.im/_m/MINE"],
                                                              [f"/link {toss_old} https://toss.im/_m/MINE"]]
    # 토스 딜이 새 것 하나뿐이면 그 딜
    bot.skip_item(toss_old)
    cands = bot.nl.candidates(text, Ref())
    d = bot.nl.decide({"intent": "/link", "post": None, "args": "https://toss.im/_m/MINE", "confidence": 0.95}, text, Ref(), cands)
    assert d.actions and d.actions[0].post == toss_new


def test_nl_link_buttons_put_the_newest_waiting_deals_first(bot: DealBot) -> None:
    """리뷰 10/15: 그냥 보낸 링크의 몰 딜이 버튼(MAX_BUTTONS)보다 많으면 오래된 것부터 잘라 방금 온 링크 요청이 빠졌다."""
    toss = [_nl_item(bot, f"t{i}", "toss", "awaiting_link", 8 - i) for i in range(7)]  # 뒤로 갈수록 새 링크 요청
    _nl_item(bot, "n1", "naver", "awaiting_link", 0.5)  # 다른 몰은 더 새것이어도 토스 링크 버튼에 없음
    text = "토스 링크 https://toss.im/_m/MINE"
    cands = bot.nl.candidates(text, Ref())
    d = bot.nl.decide({"intent": "/link", "post": None, "args": "https://toss.im/_m/MINE", "confidence": 0.95}, text, Ref(), cands)
    assert d.act == "ask" and len(d.options) == MAX_BUTTONS == 5
    assert [o.cmds for o in d.options] == [[f"/link {q} https://toss.im/_m/MINE"] for q in reversed(toss[-MAX_BUTTONS:])]
    assert d.options[0].cmds == [f"/link {toss[-1]} https://toss.im/_m/MINE"]  # 가장 새 링크 요청이 맨 앞
    # 몰을 모르는 링크면 모든 링크 요청 중 새것부터
    text = "이 링크 https://example.com/x"
    cands = bot.nl.candidates(text, Ref())
    d = bot.nl.decide({"intent": "/link", "post": None, "args": "https://example.com/x", "confidence": 0.95}, text, Ref(), cands)
    assert d.act == "ask" and d.options[0].cmds == [f"/link {cands[max(cands)].id} https://example.com/x"]


def test_nl_reads_as_many_waiting_deals_as_the_slots_allow(bot: DealBot) -> None:
    """리뷰 10/15: 기다리는 글을 읽는 수가 40 으로 고정이라 자리를 늘리면 새 링크 요청이 다시 잘림 → 자리 수에서 셈."""
    assert bot.nl.awaiting_limit() == MAX_CANDIDATES == 40  # 링크 15 + 확인 6 + 5 < 40
    assert NLRouter(NLRouterConfig(), bot.db, None, relay=lambda u: None).awaiting_limit() == MAX_CANDIDATES  # 설정 없음
    bot.settings.publish.max_awaiting_links = 50  # 나중에 자리를 늘림 (설정을 그때그때 읽음)
    assert bot.nl.awaiting_limit() == 50 + 6 + 5
    links = [_nl_item(bot, f"t{i}", "toss", "awaiting_link", 60 - i * 0.5) for i in range(50)]
    reviews = [_nl_item(bot, f"r{i}", "naver", "awaiting_approval", 70 - i) for i in range(6)]
    posted = _nl_item(bot, "pub", "toss", "published", 2)
    pending = [_nl_item(bot, f"p{i}", "toss", "pending", 0.1) for i in range(10)]
    text = "토스 링크 https://toss.im/_m/MINE"
    cands = bot.nl.candidates(text, Ref())
    assert {*links, *reviews, posted, *pending} <= set(cands)  # 가장 새 링크 요청(links[-1])까지 다 보임
    d = bot.nl.decide({"intent": "/link", "post": None, "args": "https://toss.im/_m/MINE", "confidence": 0.95}, text, Ref(), cands)
    assert d.options[0].cmds == [f"/link {links[-1]} https://toss.im/_m/MINE"]


# ---------------------------------------------------------------- 자리를 내준 글과 대시보드 '링크 넣기' (리뷰 10/15)
URL = "https://toss.im/_m/MINE"


async def _replaced_on_screen(bot: DealBot) -> tuple[int, int, str]:
    """링크 자리 15칸 중 가장 새(점수가 가장 낮은) 글이 대시보드에 떠 있는 동안 더 좋은 딜에 자리를 내줌 → (그 글, 새 딜, 그때의 화면)."""
    assert bot.dashboard is not None
    ids = _fill(bot, [10] * 14 + [5])
    low = ids[-1]
    page = bot.dashboard.page_html()
    assert f'name="q" value="{low}"' in page  # 링크 넣기 칸이 떠 있음
    new = await _offer(bot, "big", 30)
    assert _status(bot, low) == ("skipped", f"{REPLACED} #{new} (score 5→30)")
    return low, new, page


async def _form(bot: DealBot, qid: int, text: str = f"토스 공유 {URL}") -> tuple[int, str]:
    assert bot.dashboard is not None
    return await bot.dashboard._link({"t": bot.dashboard.form_token, "q": str(qid), "url": text})


async def test_dashboard_link_form_takes_a_deal_replaced_after_the_page_loaded(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    """대시보드를 연 뒤 자리를 내준 글: 카드 목록에서는 빠지지만(진짜 기다리는 글만), 이미 떠 있는 칸은 텔레그램 답장처럼 받는다."""
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    low, new, _page = await _replaced_on_screen(bot)
    assert bot.dashboard is not None
    card = next(v for v in bot.dashboard.views() if v.key == "links")
    assert low not in [e.queue_id for e in card.groups[0].entries] and new in [e.queue_id for e in card.groups[0].entries]
    assert card.detail["total"] == 15 == bot.db.awaiting_count()
    assert f'name="q" value="{low}"' not in bot.dashboard.page_html()
    # 텔레그램 답장과 같은 판단 (유효 시간 안 → 붙일 수 있음)
    assert bot.owner_link_problem(low, URL) is None
    target = bot.owner_link_target(low)
    assert target is not None and (target.id, target.status) == (low, "awaiting_link")
    assert _status(bot, low)[0] == "skipped"  # 보기만 함
    calls: list[tuple[int, str]] = []
    real = bot.attach_link

    async def spy(queue_id: int, url: str) -> str:
        calls.append((queue_id, url))
        return await real(queue_id, url)

    monkeypatch.setattr(bot, "attach_link", spy)
    assert await _form(bot, low) == (303, f"/dashboard?msg=linked&src=links&q={low}#card-links")
    assert calls == [(low, URL)]
    item = bot.db.get_queue_item(low)
    assert item is not None and item.status == "pending" and item.deal.affiliate_url == URL
    assert bot.db.kv_get("mylink:toss:w14") == URL  # 텔레그램 답장처럼 '내 링크' 로 기억
    assert bot.db.queue_counts()["awaiting_link"] == 15  # 다시 묻지 않으니 링크 자리는 그대로
    # 두 번 눌러도 한 번만
    assert (await _form(bot, low))[1].startswith("/dashboard?msg=link_not_waiting") and len(calls) == 1
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(low).status == "published"  # type: ignore[union-attr]


async def test_dashboard_link_on_a_replaced_deal_goes_to_the_requeued_twin(bot: DealBot) -> None:
    tg = _Tg()
    bot.notifier.bot, bot.notifier.chat_id = tg, 1  # type: ignore[assignment]
    low, _new, _page = await _replaced_on_screen(bot)
    twin = _requeue_twin(bot, "w14", "awaiting_approval")  # 다시 줄 선 글이 확인(/ok)을 기다림 → 넣지 않고 그 이유
    target = bot.owner_link_target(low)
    assert target is not None and target.id == twin
    assert await _form(bot, low) == (303, f"/dashboard?msg=link_twin_review&src=links&q={low}#card-links")
    assert bot.db.get_queue_item(twin).deal.affiliate_url is None  # type: ignore[union-attr]
    assert not any("다시 줄 서 있어" in t for t in tg.sent)
    bot.db.update_queue_item(twin, status="pending")  # 이번엔 차례를 기다리는 글 (텔레그램 답장도 이 글로 받음)
    assert (await _form(bot, low, "https://t.me/mychannel/12"))[1].startswith("/dashboard?msg=link_bad")  # 같은 거름
    assert await _form(bot, low) == (303, f"/dashboard?msg=linked_twin&src=links&q={low}#card-links")
    note = f"↪️ #{low} 번과 같은 상품이 지금 #{twin} 번으로 다시 줄 서 있어 #{twin} 번으로 받아요."
    assert note in tg.sent  # 텔레그램 답장과 같은 알림
    assert bot.db.get_queue_item(twin).deal.affiliate_url == URL  # type: ignore[union-attr]
    assert _status(bot, low)[0] == "skipped"
    # 또 눌러도: 그 글엔 이미 링크가 있음 → 아무것도 안 함 (알림도 한 번)
    assert (await _form(bot, low, "https://toss.im/_m/OTHER"))[1].startswith("/dashboard?msg=link_not_waiting")
    assert bot.db.get_queue_item(twin).deal.affiliate_url == URL and tg.sent.count(note) == 1  # type: ignore[union-attr]
    _, body, _, _ = await bot.dashboard._page({"msg": "linked_twin", "src": "links", "q": str(low)})  # type: ignore[union-attr]
    assert f"#{low} 같은 상품이 새 번호로 다시 줄 서 있어 그 딜에 링크를 넣었어요" in body


async def test_dashboard_link_on_a_replaced_deal_past_the_ttl_gives_the_real_reason(bot: DealBot) -> None:
    low, _new, _page = await _replaced_on_screen(bot)
    bot.db._conn.execute("UPDATE deal_queue SET created_at = ? WHERE id = ?", (to_iso(utcnow() - timedelta(hours=25)), low))
    assert await _form(bot, low) == (303, f"/dashboard?msg=link_replaced_late&src=links&q={low}#card-links")
    assert _status(bot, low)[0] == "skipped" and bot.db.get_queue_item(low).deal.affiliate_url is None  # type: ignore[union-attr]
    assert "유효 시간이 지나" in (bot.owner_link_problem(low, URL) or "")  # 텔레그램 답장과 같은 이유
    _, body, _, _ = await bot.dashboard._page({"msg": "link_replaced_late", "src": "links", "q": str(low)})  # type: ignore[union-attr]
    assert f"#{low} 더 좋은 딜에 자리를 내준 뒤 링크 유효 시간이 지나 넣지 않았어요" in body
    # 품절로 내려간 글은 예전처럼 '기다리는 딜이 아님'
    bot.db.update_queue_item(low, status="expired", error="sold out (x)")
    assert (await _form(bot, low))[1].startswith("/dashboard?msg=link_not_waiting")


async def test_link_problem_checks_the_ttl_itself(bot: DealBot) -> None:
    """리뷰 10/15: _link_problem 이 자리를 내준 글이면 시간을 보지 않고 '유효 시간이 지나' 라고 함 (부르는 쪽이 먼저 열어 줄 때만 맞았다)."""
    low, _new, _page = await _replaced_on_screen(bot)
    item = bot.db.get_queue_item(low)
    assert item is not None and item.link_replaced
    ttl = timedelta(hours=bot.settings.publish.manual_link_ttl_hours)
    msg = DealBot._link_problem(item, low, URL, link_ttl=ttl)
    assert msg is not None and "유효 시간이 지나" not in msg and f"<code>/link {low} 링크</code>" in msg
    assert bot.attach_auto_link(low, URL) == msg  # 열어 주지 않는 길(자동 링크)도 거짓 이유 없이
    assert _status(bot, low)[0] == "skipped"
    assert "유효 시간이 지나" in (DealBot._link_problem(item, low, URL, link_ttl=timedelta(minutes=30)) or "")
    bot.db._conn.execute("UPDATE deal_queue SET created_at = ? WHERE id = ?", (to_iso(utcnow() - timedelta(hours=25)), low))
    assert "유효 시간이 지나" in (bot.attach_auto_link(low, URL) or "")
