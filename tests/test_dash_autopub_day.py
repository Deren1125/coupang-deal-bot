"""'하루치 한 번에' 하루치 모드 (주인 11/25 — "하루에 한 번만, 전날 밤에 눌러 그날 글을 다 예약").

20:00 뒤에 누르면 지금 발행 없이 준비된 글을 모두 내일 07:30~23:00 에 예약 · 블로그마다 하루 DAY_MAX 편 · 한 판 RUN_MAX(10) ·
누른 때부터 HORIZON_H 시간 안 · 분 10·20·40·50 · 같은 블로그 40분 넘게 · 다른 블로그 20분 넘게 · 낮에 눌러도 하루 상한.
누른 뒤 새로 쓴 글은 할 일 줄에 '속보 n편 — 원하면 한 번 더' (조르지 않음).
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from dealbot.app import DealBot
from dealbot.dashboard import autopub, kkumi, routine
from test_dash_autopub import DAY, OK, TZ, Rng, _paste, bot, _no_live_threads  # noqa: F401 — 고정 장치
from test_dash_routine import SLOTS, at, item, with_routine
from test_dash_statscap import FakeDb


def _d(t: str) -> datetime:
    return datetime.strptime(t, "%Y-%m-%dT%H:%M")


def _rows(ids: list[int], blog: str = "issue") -> dict:
    its = [item(f"rsv:{blog}:{n}", "s1", group="예약", blog=blog, title=f"글 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in ids]
    return {"day": DAY, "slots": SLOTS, "overflow": 0, "items": its}


def _go(bot: DealBot, now: float, **form: str) -> dict:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), **form}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng(),
                       pub={"issue": 0, "finance": 0, "hotdeal": 0} if form.get("a") == "next" else None)


def _ok_spacing(ds: list[datetime], first_soft: bool = False) -> None:
    ds = sorted(ds)
    for k, d in enumerate(ds):
        assert (d.minute in autopub.MINUTES or (first_soft and k == 0 and d.minute % 10 == 0)) and (7, 30) <= (d.hour, d.minute) <= (23, 0), d
        if k:
            assert d - ds[k - 1] > timedelta(minutes=40), ds


def test_evening_press_reserves_all_of_tomorrow_nothing_now_up_to_daily_cap(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows(list(range(31, 41))))  # 준비된 글 10편 · 오늘 발행한 글 없음
    t = at(bot, DAY, "21:10")
    tmr = (datetime.fromisoformat(DAY) + timedelta(days=1)).strftime("%Y-%m-%d")
    q = _go(bot, t, a="next", b="issue")
    assert q["next"]["at"] and q["next"]["label"].startswith("내일 ") and "지금" not in q["plan"], q  # 저녁엔 지금 발행 없음
    assert q["plan"].startswith("내일 예약 ") and (q["n"], q["m"]) == (1, autopub.DAY_MAX)
    slots = autopub._plans(bot.db)["issue"]["slots"]
    assert len(slots) == autopub.DAY_MAX and all(v.startswith(tmr) for v in slots.values()), slots
    _ok_spacing([_d(v) for v in slots.values()])
    assert all(_d(v) <= datetime.fromtimestamp(t, TZ).replace(tzinfo=None) + timedelta(hours=autopub.HORIZON_H) for v in slots.values())
    got = []
    for k in range(autopub.DAY_MAX):  # 한 자리에서 차례로 (책갈피가 하는 그대로)
        q = _go(bot, t + 300 * k, a="next", b="issue")
        nx = q["next"]
        assert nx and nx["at"] == slots[nx["id"]] and kkumi.stamped(nx["html"]) == nx["at"], (k, q)
        got.append(nx["at"])
        _go(bot, t + 300 * k + 1, a="done", b="issue", id=nx["id"], r="hold")
        _go(bot, t + 300 * k + 2, a="done", b="issue", id=nx["id"], r="ok")
    assert got == sorted(got) and len(set(got)) == autopub.DAY_MAX
    q = _go(bot, t + 300 * autopub.DAY_MAX, a="next", b="issue")  # 9번째 글: 내일은 다 참 (한 판 10편 상한보다 먼저)
    assert q["next"] is None and q["left"] == 2 and "자리가 다 찼어요" in q["text"], q
    line = autopub.today(bot.db, t + 3000, TZ, routine.view(bot, now=t + 3000))["by"]["issue"]["line"]
    assert f"내일 예약 {autopub.DAY_MAX}편 넣음" in line and "속보 2편 — 원하면 한 번 더" in line, line


def test_late_written_post_is_an_optional_line_not_a_nag(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows([31, 32, 33]))
    t = at(bot, DAY, "20:20")
    for k in range(3):
        nx = _go(bot, t + 300 * k, a="next", b="issue")["next"]
        _go(bot, t + 300 * k + 1, a="done", b="issue", id=nx["id"], r="hold")
        _go(bot, t + 300 * k + 2, a="done", b="issue", id=nx["id"], r="ok")
    line = autopub.today(bot.db, t + 1000, TZ, routine.view(bot, now=t + 1000))["by"]["issue"]["line"]
    assert "준비된 글" not in line and "속보" not in line and "내일 예약 3편 넣음" in line, line
    with_routine(tmp_path, _rows([31, 32, 33, 34]))  # Blog-Auto 가 밤에 새 글(속보) 하나를 더 씀
    for n in (31, 32, 33):
        routine.set_done(bot.db, DAY, f"rsv:issue:{n}", True)
    v = autopub.today(bot.db, t + 2000, TZ, routine.view(bot, now=t + 2000))["by"]["issue"]
    assert "속보 1편 — 원하면 한 번 더 (안 눌러도 돼요)" in v["line"] and "준비된 글" not in v["line"] and v["fresh"] == 1, v
    q = _go(bot, t + 2100, a="next", b="issue")  # 원하면 한 번 더 — 내일 계획 뒤에 40분 넘게 · 지금 발행 아님
    plan = autopub._plans(bot.db)["issue"]
    assert q["next"]["id"] == "rsv:issue:34" and q["next"]["at"] and all(abs((_d(q["next"]["at"]) - _d(u)).total_seconds()) > 2400 for u in plan["used"])


def test_daytime_press_keeps_first_post_now_and_daily_cap_spills_to_next_day(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows(list(range(41, 53))))  # 12편
    t = at(bot, DAY, "10:03")
    q = _go(bot, t, a="next", b="issue")
    assert q["next"]["at"] == "" and q["plan"].startswith("지금 발행")  # 낮에는 v7 그대로 (첫 글 지금)
    assert (q["n"], q["m"]) == (1, autopub.RUN_MAX)  # 한 판 10편
    slots = autopub._plans(bot.db)["issue"]["slots"]
    today_n = sum(v.startswith(DAY) for v in slots.values()) + 1  # + 지금 발행
    assert today_n <= autopub.DAY_MAX and len(slots) + 1 <= 12
    end = datetime.fromtimestamp(t, TZ).replace(tzinfo=None) + timedelta(hours=autopub.HORIZON_H)
    assert all(_d(v) <= end for v in slots.values())
    _ok_spacing([_d(v) for v in slots.values()] + [datetime(2026, 11, 18, 10, 10)])
    for k in range(autopub.RUN_MAX):  # 한 판 10편까지
        q = _go(bot, t + 300 * k, a="next", b="issue")
        if q["next"] is None:
            break
        _go(bot, t + 300 * k + 1, a="done", b="issue", id=q["next"]["id"], r="hold")
        _go(bot, t + 300 * k + 2, a="done", b="issue", id=q["next"]["id"], r="ok")
    q = _go(bot, t + 300 * autopub.RUN_MAX + 10, a="next", b="issue")
    assert q["next"] is None and (f"한 번에 {autopub.RUN_MAX}편까지만" in q["text"] or "자리가 다 찼어요" in q["text"]), q


def test_three_blogs_at_night_fill_tomorrow_apart_and_capped() -> None:
    for seed in range(40):
        db, r = FakeDb(), random.Random(seed)
        base = datetime(2026, 11, 25, 20 + r.randint(0, 3), r.randint(0, 59), tzinfo=TZ)
        plans = {}
        for k, b in enumerate(autopub.NAMES):
            tap = base + timedelta(minutes=2 * k)
            rows = [{"id": f"rsv:{b}:{60 + j}"} for j in range(10)]
            s = autopub.schedule(db, b, rows, tap.timestamp(), TZ, rng=random.Random(seed * 7 + k), first_now=True)
            ds = [_d(v) for v in s.values()]
            assert "" not in s.values() and len(ds) == autopub.DAY_MAX, (seed, b, s)  # 저녁: 지금 발행 없음 · 내일 하루 상한까지 다 들어감
            assert {d.date() for d in ds} == {(base + timedelta(days=1)).date()}, (seed, b, ds)
            assert max(ds) <= tap.replace(tzinfo=None) + timedelta(hours=autopub.HORIZON_H)
            _ok_spacing(ds, first_soft=True)  # 첫 글은 08:40 을 넘기느니 분 규칙을 풀 수 있음 (주인 11/26)
            assert (min(ds).hour, min(ds).minute) <= autopub.FIRST_BY, (seed, b, min(ds))  # 블로그마다 첫 글 07:30~08:40
            plans[b] = ds
        names = list(plans)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                assert all(abs((x - y).total_seconds()) >= 20 * 60 for x in plans[a] for y in plans[b]
                           if x != min(plans[a]) and y != min(plans[b])), (seed, a, b)  # 20분 이상 (빈 자리 찾기는 꼭 20분도 · 첫 글끼리는 아래)
                assert min(plans[a]) != min(plans[b])


def test_every_blogs_first_night_reserve_is_by_0840_whoever_presses_last() -> None:
    """주인 11/26: 하루치 모드에서 데런픽(마지막에 누른 블로그) 첫 글이 09시 넘어 나감 → 블로그마다 첫 글은 꼭 07:30~08:40, 날마다 다르게."""
    import itertools

    firsts: dict[str, set] = {b: set() for b in autopub.NAMES}
    soft = total = 0
    for day in range(20):
        for order in itertools.permutations(autopub.NAMES):
            db = FakeDb()
            base = datetime(2026, 11, 1, 20, 30, tzinfo=TZ) + timedelta(days=day, minutes=7 * day % 50)
            for k, b in enumerate(order):
                rows = [{"id": f"rsv:{b}:{80 + j}"} for j in range(5 + (day + k) % 4)]
                s = autopub.schedule(db, b, rows, (base + timedelta(minutes=6 * k)).timestamp(), TZ)
                f = min(_d(v) for v in s.values())
                assert f.date() == (base + timedelta(days=1)).date() and (7, 30) <= (f.hour, f.minute) <= (8, 40), (day, order, b, f)
                assert f.minute % 10 == 0
                firsts[b].add(f.strftime("%H:%M"))
                soft += f.minute not in autopub.MINUTES
                total += 1
    assert all(len(v) >= 4 for v in firsts.values()), firsts  # 날마다 다른 시각
    assert soft <= total * 0.15  # 분 10·20·40·50 은 되도록 지킴


def test_night_mode_is_from_20_and_retap_keeps_the_plan() -> None:
    assert not autopub.night(datetime(2026, 11, 25, 19, 59)) and autopub.night(datetime(2026, 11, 25, 20, 0)) and autopub.night(datetime(2026, 11, 25, 23, 50))
    assert not autopub.night(datetime(2026, 11, 26, 0, 10))  # 자정 뒤는 그날 아침부터 (전처럼)
    db, t = FakeDb(), datetime(2026, 11, 25, 21, 0, tzinfo=TZ).timestamp()
    rows = [{"id": f"rsv:issue:{70 + j}"} for j in range(6)]
    a = autopub.schedule(db, "issue", rows, t, TZ, rng=random.Random(1))
    assert autopub.schedule(db, "issue", rows, t + 900, TZ, rng=random.Random(2)) == a
    json.dumps(a)
