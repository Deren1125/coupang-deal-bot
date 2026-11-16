"""오늘 X 글 하루 10편 · 팔로워 목표 글 (주인 11/16 — "X 글 발행 빈도를 올리자 — 하루 10개 정도. 중간중간 '팔로워 몇 명이 목표다,
팔로우 부탁드린다' 같은 글도 써도 됨").

1) 추천 시각 열 (07:30~22:30 · 날마다 ±10분 · 그날 안에서는 같음) → 아침 한 번에 카드 10장 · AI 는 카드 수만큼만.
2) 팔로워 목표 글: 하루 1편 · 7일에 3편 · 이틀 띄움 · 저녁 시각 · 팔로워 수를 알면 '다음 목표 N명'(모르면 숫자 없이) ·
   대가 약속(경품·맞팔 보장)·스팸 문구 버림 · 최근 14일 문구와 겹치면 버림 · 쓸 게 없으면 그 자리는 화제 글이.
3) 글 쓰는 모델은 Opus 까지 (Fable 이면 AI 를 부르지 않음).
시험 시계는 고정 (desk 의 now — 실제 시각을 보지 않음).
"""

from __future__ import annotations

import json
from typing import Any

from dealbot.config import XTodayConfig
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from dealbot.dashboard.actions import X_STATS_KEY
from test_dash_xtoday import FABLE, FakeAsk, at, desk, hot

TOPICS = ["세무사 시험", "쿠팡 과징금", "카카오 먹통", "테슬라 리콜", "넷플릭스 요금", "아이폰 가격", "배달앱 수수료", "전기요금 인상",
          "항공권 유류세", "편의점 도시락", "중고차 시세", "라면 수출"]
HOTS10 = [hot(f"h{i}", k, f"{k} 기사 제목") for i, k in enumerate(TOPICS)]
GOOD_GOAL = ["늘 읽어 주셔서 감사합니다\n숫자로 따지는 글 계속 씀\n다음 목표는 {g}명, 팔로우 부탁드려요",
             "요즘 글 봐 주는 분 늘어서 힘이 남\n{g}명 채우는 날까지 꾸준히 쓸 예정\n괜찮았으면 팔로우 한 번 부탁드림",
             "이 계정은 뉴스 숫자를 한 번 더 따져 보는 곳임\n같이 볼 분 {g}명 모이면 좋겠음\n감사합니다"]


class GoalAsk(FakeAsk):
    """화제 글은 FakeAsk 그대로, 팔로워 목표 글(GOAL_SYSTEM)에는 goal_texts 를 돌려줌."""

    def __init__(self, goal_texts: list[str] | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.goal_texts = goal_texts if goal_texts is not None else GOOD_GOAL
        self.goal_calls: list[str] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        if system != xs.GOAL_SYSTEM:
            return await super().__call__(system, user, schema)
        self.calls.append((system, user, schema))
        self.goal_calls.append(user)
        g = "300" if "300명" in user else ""
        return {"ok": True, "why": "", "confirmed": True,
                "drafts": [{"text": t.replace("{g}명", f"{g}명" if g else "많이").replace("{g}", g), "format": "팔로워 목표", "angle": "감사",
                            "hints": ["요즘 쓰는 글 중 반응 좋았던 것은?"]} for t in self.goal_texts]}


def followers(d: xt.XToday, n: int, when: float) -> None:
    d.db.kv_set(X_STATS_KEY, json.dumps([{"at": when, "followers": n, "verified": 10, "impressions_90d": 1000}]))


def goal_desk(**kw: Any) -> tuple[xt.XToday, GoalAsk, list[float]]:
    ask = kw.pop("ask", None) or GoalAsk()
    d, _ask, _r, _f, now = desk(ask=ask, hots=kw.pop("hots", HOTS10), per_day=10, goal_posts=kw.pop("goal_posts", True),
                                big={"enabled": False}, **kw)
    return d, ask, now


# ---------------------------------------------------------------- 1) 하루 10편
def test_ten_times_spread_over_the_day_and_jitter_daily() -> None:
    assert xs.POST_TIMES == ("07:30", "09:00", "10:30", "12:00", "13:30", "15:00", "17:00", "19:00", "21:00", "22:30")
    assert xs.POST_TARGET == (8, 10) and xs.DRAFT_PER_DAY == 50
    cfg = XTodayConfig()
    assert (cfg.per_day, cfg.auto_drafts_per_day, cfg.reserve, cfg.goal_posts) == (10, 13, 15, True)
    assert (cfg.big.per_day, cfg.big.gap_minutes) == (3, 90)  # 큰 사건 알림은 그대로
    assert cfg.auto_drafts_per_day == cfg.per_day + cfg.big.per_day  # 하루 AI 는 초안 수만큼만 (카드 10 + 큰 사건 3)
    assert xs.DRAFT_PER_DAY - cfg.auto_drafts_per_day - cfg.reserve >= 20  # 주인 몫 여유
    a, b = xs.post_times("2026-11-15"), xs.post_times("2026-11-16")
    assert a != b and a == xs.post_times("2026-11-15") and len(a) == 10 and list(a) == sorted(a)


async def test_morning_makes_ten_cards_one_ai_call_each() -> None:
    d, ask, now = goal_desk(goal_posts=False)
    assert (await d.run())["made"] == 10 and len(ask.calls) == 10
    v = d.view()
    assert v["slots"] == list(xs.post_times("2026-11-15")) and [i["slot"] for i in v["items"]] == v["slots"]
    assert v["ai"] == {"used": 10, "cap": 13, "left": xs.DRAFT_PER_DAY - 10} and v["free"] == []
    assert {i["kind"] for i in v["items"]} == {"hot"}
    now[0] += 60
    assert (await d.run())["made"] == 0 and len(ask.calls) == 10


# ---------------------------------------------------------------- 2) 팔로워 목표 글
async def test_goal_post_once_a_day_in_the_evening_with_next_goal() -> None:
    d, ask, now = goal_desk()
    followers(d, 250, now[0] - 3600)
    assert (await d.run())["made"] == 10 and len(ask.calls) == 10 and len(ask.goal_calls) == 1  # 카드 10장 = AI 10번 (목표 글 포함)
    v = d.view()
    [g] = [i for i in v["items"] if i["kind"] == "goal"]
    late = [t for t in xs.post_times("2026-11-15") if t >= xt.GOAL_FROM]
    assert g["slot"] == late[0] and g["keyword"] == "팔로워 다음 목표 300명" and g["sources"] == [] and g["first_reply"] == ""
    assert "250명" in ask.goal_calls[0] and "300명" in ask.goal_calls[0] and g["nums_ok"] is True
    assert len(g["res"]["drafts"]) == 3 and all("300" in x["text"] for x in g["res"]["drafts"][:2])
    assert [i["slot"] for i in v["items"]] == v["slots"]
    rows = d.goal_rows(now[0])
    assert len(rows) == 1 and rows[0]["goal"] == 300 and rows[0]["line"] == "늘 읽어 주셔서 감사합니다" and "text" not in rows[0]
    # 같은 날 보충 때는 다시 안 씀 · 다음 날도 안 씀 (이틀 띄움) · 모레는 씀
    d.skip({"id": g["id"]})
    now[0] = at("11:25")
    await d.run()
    assert len(ask.goal_calls) == 1
    for day, want in (("2026-11-16", 1), ("2026-11-17", 2)):
        now[0] = at("07:45", day)
        await d.run()
        assert len(ask.goal_calls) == want, day


async def test_goal_post_at_most_three_a_week() -> None:
    d, ask, now = goal_desk()
    for day in ("2026-11-15", "2026-11-17", "2026-11-19", "2026-11-21", "2026-11-23"):
        now[0] = at("07:45", day)
        w = ["봄비", "단풍", "첫눈", "바다", "노을"][int(day[-2:]) % 5]
        ask.goal_texts = [f"{w} 같은 날에도 읽어 주셔서 감사합니다\n{w} 보며 숫자 글 씀\n팔로우 부탁드려요"]
        await d.run()
    days = [r["day"] for r in d.goal_rows(now[0])]
    assert days == ["2026-11-15", "2026-11-17", "2026-11-19", "2026-11-23"]  # 21일은 7일에 3편이 차서 쉼
    assert all(sum(1 for x in days if 0 <= (int(day[-2:]) - int(x[-2:])) < 7) <= 3 for day in days)


async def test_goal_post_without_follower_count_has_no_numbers() -> None:
    d, ask, now = goal_desk(ask=GoalAsk(["읽어 주셔서 감사합니다\n다음 목표 1000명 가 봄", "늘 고마움\n숫자로 따지는 글 계속 씀\n팔로우 부탁드려요"]))
    followers(d, 250, now[0] - 8 * 86400)  # 8일 지난 기록은 안 씀
    await d.run()
    assert "모름" in ask.goal_calls[0] and "250" not in ask.goal_calls[0]
    [g] = [i for i in d.view()["items"] if i["kind"] == "goal"]
    assert g["keyword"] == "팔로워 감사 글" and [x["text"] for x in g["res"]["drafts"]] == ["늘 고마움\n숫자로 따지는 글 계속 씀\n팔로우 부탁드려요"]


def test_goal_lint_blocks_rewards_follow_back_and_spam() -> None:
    ok = {"250", "300"}
    assert xs.lint_goal("늘 읽어 주셔서 감사합니다\n다음 목표 300명\n팔로우 부탁드려요", ok) == []
    for bad in ("팔로우하면 추첨으로 커피 드림", "맞팔 100% 갑니다", "팔로우 해주시면 꼭 보답 드림 보장", "RT 하고 팔로우 부탁", "300명 되면 경품 이벤트",
                "선팔 환영", "링크 https://x.example 팔로우", "#팔로우 부탁", "수익 나는 계정 팔로우", "좋아요 눌러 주고 팔로우도", "다음 목표 1000명"):
        assert xs.lint_goal(bad, ok), bad
    assert xs.next_goal(0) == 50 and xs.next_goal(250) == 300 and xs.next_goal(300) == 500 and xs.next_goal(9999) == 10000
    assert xs.next_goal(10000) == 15000 and xs.next_goal(None) is None


async def test_goal_drafts_that_repeat_recent_lines_or_break_rules_are_dropped() -> None:
    d, ask, now = goal_desk(ask=GoalAsk(["맞팔 100% 보장합니다", "읽어 주셔서 감사합니다\n팔로우 부탁드려요"]))
    d.db.kv_set(xt.KV_GOAL, json.dumps([{"at": at("07:00", "2026-11-03"), "day": "2026-11-03", "goal": 300, "line": "읽어 주셔서 감사합니다",
                                          "toks": ["감사합니다", "읽어", "주셔서", "팔로우", "부탁드려요"]}]))
    assert (await d.run())["made"] == 10 and len(ask.goal_calls) == 1  # 목표 글은 쉼 — 그 자리는 화제 글이 채움
    assert "읽어 주셔서 감사합니다" in ask.goal_calls[0]  # 최근 문구를 모델에도 보여 줌
    v = d.view()
    assert all(i["kind"] == "hot" for i in v["items"]) and len(v["items"]) == 10 and len(ask.calls) == 11
    assert [r["day"] for r in d.goal_rows(now[0])] == ["2026-11-03", "2026-11-15"]  # 쉰 날도 셈 (다시 부르지 않게)
    now[0] = at("11:25")
    await d.run()
    assert len(ask.goal_calls) == 1
    # 14일 지난 문구는 비교하지 않는다
    assert all(now[0] - r["at"] <= xt.GOAL_KEEP_S for r in d.goal_rows(at("08:00", "2026-11-18")))
    assert [r["day"] for r in d.goal_rows(at("08:00", "2026-11-18"))] == ["2026-11-15"]


async def test_goal_post_uses_only_the_allowed_writer_model() -> None:
    d, ask, now = goal_desk()
    d.studio.model = FABLE
    assert (await d.run())["made"] == 0 and ask.calls == [] and d.goal_rows(now[0]) == []
    assert "모델 규칙" in d.view()["err"]
