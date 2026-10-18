"""'할 일' 순서 (주인 10/18 — "중요한, 핫할것 같은 순으로"): 고장 먼저 · 곧 끝나는 핫한 딜이 여유 있는 딜보다 위 ·
마감이 이른 글 먼저 · 참고는 맨 뒤 · 늘 같은 순서. 화면 값에는 점수가 없고 맨 위 몇 줄에만 작은 칩."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dash_helpers import get_json, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import rank
from dealbot.utils.timeutil import to_iso

NOW = 1_800_000_000.0


def it(iid: str, *, sev: str = "todo", kind: str = "", src: str = "blog", hot: float = 0, exp_h: float | None = None,
       since_h: float = 1, urgent: bool = False) -> dict:
    return {"iid": iid, "sev": sev, "kind": kind, "src": src, "_hot": hot, "urgent": urgent,
            "expires_at": NOW + exp_h * 3600 if exp_h is not None else None, "since": NOW - since_h * 3600}


def ids(items: list[dict]) -> list[str]:
    return [x["iid"] for x in rank.order(items, NOW)]


def test_broken_things_come_first_and_info_last() -> None:
    items = [it("blog-finance-buddy", sev="info", kind="buddy"),
             it("deal-1", src="deal", kind="link_request", hot=90, exp_h=0.5),
             it("post-issue-1", kind="publish", hot=80),
             it("sys-dealbot", sev="stop", kind="publish_broken", src="dealbot"),
             it("conn-naver-hotdeal", kind="login")]
    out = ids(items)
    assert out[:2] == ["sys-dealbot", "conn-naver-hotdeal"]
    assert out[-1] == "blog-finance-buddy"


def test_hot_deal_near_expiry_beats_a_calm_deal_with_time_left() -> None:
    hot_soon = it("deal-1", src="deal", kind="link_request", hot=rank.deal_hot(40, price=9000, low_price=9000), exp_h=2)
    calm = it("deal-2", src="deal", kind="link_request", hot=rank.deal_hot(3), exp_h=20)
    hot_later = it("deal-3", src="deal", kind="link_request", hot=rank.deal_hot(40), exp_h=20)
    assert ids([calm, hot_later, hot_soon]) == ["deal-1", "deal-3", "deal-2"]
    # 같은 딜이라도 끝이 가까워지면 앞으로
    assert rank.rank(it("d", src="deal", kind="link_request", exp_h=0.5), NOW) > rank.rank(it("d", src="deal", kind="link_request", exp_h=5), NOW)


def test_expired_deal_drops_to_the_bottom() -> None:
    out = ids([it("deal-9", src="deal", kind="link_request", hot=100, exp_h=-1), it("blog-x-info", sev="info"),
               it("post-finance-3", kind="publish")])
    assert out[-1] == "deal-9"


def test_owner_questions_with_a_sooner_deadline_come_first_and_drafts_after() -> None:
    out = ids([it("post-finance-1", kind="answers", exp_h=20), it("post-finance-2", kind="answers", exp_h=2),
               it("post-issue-3", kind="publish", hot=90), it("post-issue-4", kind="photos")])
    assert out[0] == "post-finance-2"
    assert out.index("post-finance-1") < out.index("post-issue-3")
    assert out.index("post-issue-4") < out.index("post-issue-3")


def test_ready_drafts_rank_by_expected_traffic_then_newer() -> None:
    out = ids([it("post-issue-1", kind="publish", hot=10, since_h=1), it("post-issue-2", kind="publish", hot=80, since_h=9),
               it("post-issue-3", kind="publish", hot=10, since_h=5)])
    assert out == ["post-issue-2", "post-issue-1", "post-issue-3"]  # 인기 먼저 · 같으면 새 것


def test_order_is_deterministic_and_hides_scores() -> None:
    items = [it(f"post-issue-{n}", kind="publish") for n in range(6)] + [it("deal-1", src="deal", kind="link_request", exp_h=5)]
    a = ids([dict(x) for x in items])
    b = ids([dict(x) for x in reversed(items)])
    assert a == b
    out = rank.order([dict(x) for x in items], NOW)
    assert all("_hot" not in x for x in out)
    assert {x["group"] for x in out} <= {"지금 꼭", "오늘 안에", "여유 있을 때"}


def test_cue_only_on_the_top_few_rows() -> None:
    items = [it(f"deal-{n}", src="deal", kind="link_request", exp_h=1 + n / 10, hot=10) for n in range(8)]
    out = rank.order(items, NOW)
    cues = [x["cue"] for x in out]
    assert cues[: rank.CUE_TOP] == ["급해요"] * rank.CUE_TOP and set(cues[rank.CUE_TOP:]) == {None}
    pop = rank.order([it("post-issue-1", kind="publish", hot=90), it("sys-x", sev="stop")], NOW)
    assert [x["cue"] for x in pop] == [None, "인기"]  # ⛔ 줄에는 칩을 더하지 않음


def test_deal_hot_uses_score_record_low_discount_and_recommend() -> None:
    assert rank.deal_hot(0) == 0
    assert rank.deal_hot(10) < rank.deal_hot(10, price=100, low_price=100) < rank.deal_hot(10, price=100, low_price=100, discount=50)
    assert rank.deal_hot(1000, price=1, low_price=1, discount=90, recommend=99) == 100
    assert rank.post_hot({"score": 50, "popular": True}) > rank.post_hot({"score": 50}) > rank.post_hot(None)


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _age(bot: DealBot, qid: int, hours: float, score: float) -> None:
    with bot.db._lock:
        bot.db._conn.execute("UPDATE deal_queue SET created_at=?, score=? WHERE id=?",
                             (to_iso(datetime.now(UTC) - timedelta(hours=hours)), score, qid))
        bot.db._conn.commit()


async def test_state_lists_todo_in_rank_order(bot: DealBot) -> None:
    calm = queued(bot, product_id="toss:1", name="여유 있는 딜")
    soon = queued(bot, product_id="toss:2", name="곧 끝나는 핫딜")
    _age(bot, calm.id, 2, 2)  # 22시간 남음, 점수 낮음
    _age(bot, soon.id, 22, 40)  # 2시간 남음, 점수 높음
    bot.router.open({"src": "dealbot", "key": "threads_replies:r1", "kind": "threads_replies", "iid": "sns-replies",  # type: ignore[union-attr]
                     "sev": "info", "title": "스레드 답글 1개", "at": time.time()})
    bot.router.open({"src": "dealbot", "key": "publish_broken", "kind": "publish_broken", "iid": "sys-dealbot", "sev": "stop",  # type: ignore[union-attr]
                     "title": "딜봇 채널 발행 막힘", "at": time.time() - 3600})
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    order = [x["iid"] for x in body["todo"]]
    assert order == ["sys-dealbot", f"deal-{soon.id}", f"deal-{calm.id}", "sns-replies"]
    first = body["todo"][1]
    assert first["cue"] == "급해요" and first["group"] == "지금 꼭" and body["todo"][-1]["group"] == "여유 있을 때"
    assert "_hot" not in json.dumps(body, ensure_ascii=False)


def test_questions_are_shown_in_full_on_the_todo_list(bot: DealBot) -> None:
    """주인 10/18: 할 일 목록의 답하기 칸에서 질문이 '…' 으로 잘려 무엇을 묻는지 몰랐다 — 질문은 자르지 않는다."""
    from dealbot.dashboard.state import StateBuilder

    long_q = "검수 중 수정: 후보 요약의 '정부기여금 6% 또는 12% 매칭'과 '중복 가입 불가 조건'은 이번에 연 소득 기준이 바뀌어 다시 확인이 필요합니다. 맞나요?"
    post = {"needs": ["answers"], "title": "청년미래적금 2차", "questions": [{"n": 1, "q": long_q}]}
    item = StateBuilder(bot)._post_item("finance", post, pid=7)
    assert item["detail"]["questions"][0]["q"] == long_q
