"""하루치 한 번에 v5·v6 — 누른 때 기준 예약 계획 (주인 11/23 13:45: "예약 시간이 다 똑같은 거 같다. 내가 책갈피를 언제 누를지 모르니까,
한 번에 넣기 누른 시간 기준으로 적당히 시간 분배해서 세팅해줘" · 네이버 예약 칸은 10분 단위).

autopub.schedule(): 첫 글 = 누른 때 + 20~45분 · 다음 70~160분 · 분은 10·20·40·50 (정각·30분 없음 · 이웃 글과 같은 분 자리 아님) · 07:30~23:00 ·
넘치면 다음 날 아침 · 토·일은 월요일로 (주인이 '주말에도 시작'이면 주말 자리) · 이미 한 시각과 40분 넘게 · 같은 계획 다시 · 지난 자리만 새로 · 블로그마다 따로.
act(next): 계획에서 가장 이른 글 · 그 시각을 글에 · plan 한 줄 · 발행을 눌러 본 글의 시각은 '쓴 시각'으로.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dealbot.dashboard import autopub, kkumi
from test_dash_statscap import FakeDb

TZ = ZoneInfo("Asia/Seoul")


def _ts(y: int, mo: int, d: int, h: int, mi: int) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp()


def _rows(n: int, blog: str = "issue") -> list[dict]:
    return [{"id": f"rsv:{blog}:{30 + k}"} for k in range(n)]


def _d(t: str) -> datetime:
    return datetime.strptime(t, "%Y-%m-%dT%H:%M")


def _check(slots: dict[str, str], tap: datetime) -> list[datetime]:
    ds = sorted(_d(t) for t in slots.values())
    assert len(set(ds)) == len(ds)  # 같은 블로그 두 글이 같은 자리 아님
    for k, d in enumerate(ds):
        assert d.minute in (10, 20, 30, 40, 50), d  # 10분 단위 · 정각 없음 (30분은 v6 부터 아주 가끔)
        assert (7, 30) <= (d.hour, d.minute) <= (23, 0), d  # 주인 11/24 — 토·일도 자리
        if k:
            assert d - ds[k - 1] > timedelta(minutes=40) and d.minute != ds[k - 1].minute, ds  # 이웃 글끼리 분 자리 다름
    assert ds[0] > tap + timedelta(minutes=12)
    return ds


def test_four_posts_spread_from_the_tap_time_not_the_same_slot() -> None:
    for seed in range(30):
        db, rng = FakeDb(), random.Random(seed)
        tap = datetime(2026, 11, 23, 13, 47)  # 월요일
        slots = autopub.schedule(db, "issue", _rows(4), tap.replace(tzinfo=TZ).timestamp(), TZ, rng=rng)
        ds = _check(slots, tap)
        assert len(ds) == 4 and timedelta(minutes=20) <= ds[0] - tap <= timedelta(minutes=55)  # 20~45분 + 10분 자리로 올림
        gaps = [(b - a).total_seconds() / 60 for a, b in zip(ds, ds[1:], strict=False) if a.date() == b.date()]
        assert all(40 < g <= 330 for g in gaps), gaps  # v6: 짧게 40~60 · 보통 · 길게 2시간 반~4시간 (10분 자리로 옮겨 조금 넘을 수 있음)
    # 들쭉날쭉: 씨앗마다 간격이 같지 않음
    seen = {tuple(sorted(autopub.schedule(FakeDb(), "issue", _rows(3), _ts(2026, 11, 23, 13, 47), TZ, rng=random.Random(s)).values())) for s in range(8)}
    assert len(seen) > 4


def test_late_tap_rolls_to_next_morning_and_friday_night_rolls_to_saturday() -> None:
    slots = autopub.schedule(FakeDb(), "issue", _rows(3), _ts(2026, 11, 23, 21, 50), TZ, rng=random.Random(1))
    ds = _check(slots, datetime(2026, 11, 23, 21, 50))
    nxt = [d for d in ds if d.date() == datetime(2026, 11, 24).date()]
    assert nxt and nxt[0].hour < 9 and len(nxt) >= 1  # 23시 넘는 글은 다음 날 아침 07:30~ 부터
    fri = autopub.schedule(FakeDb(), "issue", _rows(3), _ts(2026, 11, 27, 21, 30), TZ, rng=random.Random(2))  # 금요일 밤
    ds = _check(fri, datetime(2026, 11, 27, 21, 30))
    assert {d.weekday() for d in ds} <= {4, 5} and ds[-1].weekday() == 5  # 주인 11/24 — 주말도 평일처럼: 금요일 밤에 넘치면 토요일 아침
    sat0 = autopub.schedule(FakeDb(), "issue", _rows(3), _ts(2026, 11, 28, 10, 5), TZ, rng=random.Random(4))  # 토요일에 누르면 토요일 자리
    assert all(_d(t).weekday() == 5 for t in sat0.values()) and all(_d(t).minute in (10, 20, 30, 40, 50) for t in sat0.values())
    # 주인이 주말에 '주말에도 시작'을 눌렀으면 그 주말 자리 그대로
    sat = autopub.schedule(FakeDb(), "issue", _rows(2), _ts(2026, 11, 28, 10, 5), TZ, wk=True, rng=random.Random(3))
    assert all(_d(t).weekday() == 5 for t in sat.values())


def test_same_plan_on_retap_only_past_or_new_posts_get_new_slots_and_blogs_are_separate() -> None:
    db, t = FakeDb(), _ts(2026, 11, 23, 13, 47)
    a = autopub.schedule(db, "issue", _rows(4), t, TZ, rng=random.Random(5))
    # 새 글쓰기 화면에서 다시 누름 (10분 뒤): 남은 글은 같은 시각
    assert autopub.schedule(db, "issue", _rows(4), t + 600, TZ, rng=random.Random(9)) == a
    # 첫 글을 발행(눌러 봄) → 남은 셋은 그대로 · 그 시각은 쓴 시각
    first = min(a, key=a.get)
    autopub._used(db, "issue", first)
    rest = [r for r in _rows(4) if r["id"] != first]
    assert autopub.schedule(db, "issue", rest, t + 900, TZ, rng=random.Random(9)) == {k: v for k, v in a.items() if k != first}
    # 둘째 글의 자리가 지난 뒤 다시 누르면 그 글만 새로 (남은 글들 뒤 · 다른 글과 40분 넘게 · 쓴 시각과도)
    second = sorted(a, key=a.get)[1]
    late = (_d(a[second]) + timedelta(minutes=5)).replace(tzinfo=TZ).timestamp()
    b = autopub.schedule(db, "issue", rest, late, TZ, rng=random.Random(4))
    assert all(b[r["id"]] == a[r["id"]] for r in rest if r["id"] != second)
    assert _d(b[second]) > _d(a[second]) and all(abs((_d(b[second]) - _d(v)).total_seconds()) > 2400 for k, v in a.items() if k != second)
    # 다른 블로그는 따로 (데런픽 계획을 건드리지 않음)
    f = autopub.schedule(db, "finance", _rows(2, "finance"), t, TZ, rng=random.Random(5))
    assert set(f) == {"rsv:finance:30", "rsv:finance:31"} and autopub._plans(db)["issue"]["slots"] == b


def test_already_reserved_times_of_that_blog_are_avoided_by_40_minutes() -> None:
    t = _ts(2026, 11, 23, 13, 47)
    for seed in range(20):
        taken = ["2026-11-23T14:20", "2026-11-23T16:10"]
        s = autopub.schedule(FakeDb(), "issue", _rows(3), t, TZ, taken=taken, rng=random.Random(seed))
        for v in s.values():
            assert all(abs((_d(v) - _d(x)).total_seconds()) > 2400 for x in taken), (v, seed)


def test_plan_text_is_the_banner_line() -> None:
    now = _ts(2026, 11, 23, 13, 47)
    assert autopub.plan_text({"a": "2026-11-23T16:10", "b": "2026-11-23T14:20", "c": "2026-11-24T07:50"}, now, TZ) == "오늘 예약 14:20 · 16:10 · 내일 07:50"
    assert autopub.plan_text({"a": "2026-11-26T09:40"}, now, TZ) == "11/26 예약 09:40"


def test_next_serves_the_earliest_planned_post_stamped_with_its_slot() -> None:
    from test_dash_kkumi import _tags_line

    line = _tags_line(["태그"], "셀럽 소식")
    rv = {"today": True, "day": "2026-11-23", "slots": [{"items": [
        {"id": "rsv:issue:41", "group": "예약", "blog": "issue", "paste": {"title": "가", "html": "<p>가</p>" + "⁤‌"}, "gate": {"ok": True},
         "when": {"at": "2026-11-23T16:40", "label": "16:40"}},
        {"id": "rsv:issue:42", "group": "예약", "blog": "issue", "paste": {"title": "나", "html": "<p>나</p>" + line}, "gate": {"ok": True},
         "when": {"at": "2026-11-23T16:40", "label": "16:40"}},
        {"id": "rsv:issue:43", "group": "예약", "blog": "issue", "done": True, "when": {"at": "2026-11-23T15:00"}}]}]}
    db, now = FakeDb(), _ts(2026, 11, 23, 13, 47)
    q = autopub.act(db, {"k": autopub.key(db), "a": "next", "b": "issue"}, tz=TZ, now=now, rv=rv)
    plan = autopub._plans(db)["issue"]["slots"]
    assert set(plan) == {"rsv:issue:41", "rsv:issue:42"} and len(set(plan.values())) == 2  # 둘 다 16:40 이던 것을 나눔
    first = min(plan, key=plan.get)
    assert q["next"]["id"] == first and q["next"]["at"] == plan[first] and kkumi.stamped(q["next"]["html"]) == plan[first]
    assert q["plan"].startswith("오늘 예약 ") and q["next"]["label"].startswith("오늘 ")
    assert all(abs((_d(v) - datetime(2026, 11, 23, 15, 0)).total_seconds()) > 2400 for v in plan.values())  # 이미 한 15:00 과 겹치지 않음


# ---------------------------------------------------------------- v6 (주인 11/24 — "시간 간격도 좀 불규칙하게 바꿔줘. 시간 단위만 불규칙으로 하는 게 아니라")
def _three(day: int, seed: int) -> dict[str, list[datetime]]:
    """세 블로그를 몇 분 사이로 차례로 누름 (주인이 아침에 하는 그대로) · 같은 db (다른 블로그 계획을 봄)."""
    db, r, out = FakeDb(), random.Random(seed), {}
    h, m = 7 + r.randint(0, 4), r.randint(0, 50)
    for k, b in enumerate(autopub.NAMES):
        t = datetime(2026, 11, day, h, m, tzinfo=TZ) + timedelta(minutes=3 * k + r.randint(0, 5))
        s = autopub.schedule(db, b, _rows(5, b), t.timestamp(), TZ, rng=random.Random(seed * 10 + k))
        out[b] = _check(s, t.replace(tzinfo=None))
    return out


def _gaps(ds: list[datetime]) -> list[float]:
    return [(b - a).total_seconds() / 60 for a, b in zip(ds, ds[1:], strict=False) if a.date() == b.date()]


def test_gaps_mix_short_and_long_and_neighbour_gaps_are_never_alike() -> None:
    allg: list[float] = []
    for seed in range(60):
        for ds in _three(24, seed).values():
            allg += _gaps(ds)
            for a, b, c in zip(ds, ds[1:], ds[2:], strict=False):  # 같은 날 이웃 간격끼리 20분 넘게 차이
                assert a.date() != c.date() or abs((c - b) - (b - a)) > timedelta(minutes=autopub.GAP_DIFF), ds
    short, long_ = sum(g <= 70 for g in allg) / len(allg), sum(g >= 150 for g in allg) / len(allg)
    assert 0.08 < short < 0.4 and 0.3 < long_ < 0.7, (short, long_)  # 짧은 것도 긴 것도 섞임 (기계처럼 고르지 않게)
    assert min(allg) < 60 and max(allg) >= 220


def test_minutes_spread_no_minute_more_than_twice_a_day_and_rarely_thirty_never_zero() -> None:
    from collections import Counter

    mins: list[int] = []
    for seed in range(60):
        for ds in _three(25, seed).values():
            mins += [d.minute for d in ds]
            assert max(Counter((d.date(), d.minute) for d in ds).values()) <= 2, ds  # 하루 한 블로그에서 같은 분 두 번까지
    c = Counter(mins)
    assert c[0] == 0 and 0 < c[30] < 0.05 * len(mins), c  # 정각 없음 · 30분은 아주 가끔
    assert all(c[m] > 0.15 * len(mins) for m in (10, 20, 40, 50)), c  # 네 자리를 고루


def test_three_blogs_never_reserve_within_20_minutes_of_each_other_on_the_same_day() -> None:
    for seed in range(60):
        plans = _three(26, seed)
        names = list(plans)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                for x in plans[a]:
                    for y in plans[b]:
                        assert x.date() != y.date() or abs((x - y).total_seconds()) > 20 * 60, (seed, a, x, b, y)
    # 다른 블로그의 이미 한(체크된) 예약도 near 로 피함
    t = _ts(2026, 11, 23, 13, 47)
    near = [f"2026-11-23T{h:02d}:{m:02d}" for h in range(14, 23) for m in (0, 40)]
    for seed in range(10):
        s = autopub.schedule(FakeDb(), "issue", _rows(3), t, TZ, near=near, rng=random.Random(seed))
        assert all(abs((_d(v) - _d(x)).total_seconds()) > 1200 for v in s.values() for x in near if v[:10] == x[:10]), s


def test_default_seed_is_blog_and_day_so_days_and_blogs_differ_but_a_retap_is_stable() -> None:
    def plan(b: str, day: int) -> tuple[str, ...]:
        return tuple(t[11:] for t in sorted(autopub.schedule(FakeDb(), b, _rows(5, b), _ts(2026, 11, day, 8, 5), TZ).values()))

    days = [plan("issue", d) for d in (23, 24, 25, 26, 27)]
    assert len(set(days)) == 5 and plan("issue", 24) == days[1]  # 날마다 다른 모양 · 같은 날·같은 때는 같은 씨앗
    assert len({plan(b, 24) for b in autopub.NAMES}) == 3
    gaps = [tuple(round(g) for g in _gaps([_d(f"2026-11-01T{x}") for x in p])) for p in days]
    assert len(set(gaps)) == 5  # 간격 모양도 날마다 다름 (분만 바뀌는 것이 아님)
    spans = {(_d(f"2026-11-01T{p[-1]}") - _d(f"2026-11-01T{p[0]}")).total_seconds() // 600 for p in days if p[-1] > p[0]}
    assert len(spans) >= 3  # 하루 전체 폭도 날마다 다름
