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
    slots = autopub._plans(bot.db)["issue"]["slots"]
    today_n = sum(v.startswith(DAY) for v in slots.values()) + 1  # + 지금 발행
    assert today_n <= autopub.DAY_MAX and len(slots) + 1 <= 12
    later = len(slots) + 1 - today_n
    assert (q["n"], q["m"]) == (1, today_n) and later > 0 and q["left"] == today_n  # 11/26: 낮에는 오늘 자리 글만 (내일 글은 저녁에)
    assert "내일" not in q["plan"].replace(f"내일 글 {later}편은 저녁 8시 뒤 한 번에", ""), q["plan"]
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
    assert q["next"] is None and f"내일 글 {later}편은 저녁 8시 뒤 한 번에" in q["text"] and q["later"] == later, q  # 날짜 오류로 멈추지 않고 깔끔히 끝
    q = _go(bot, at(bot, DAY, "20:30"), a="next", b="issue")  # 저녁에 누르면 그 글들을 계획한 자리 그대로 예약
    assert q["next"] and q["next"]["at"] > DAY + "T23:59" and q["next"]["at"] == slots[q["next"]["id"]], q


def test_daytime_done_left_skips_tomorrow_posts_and_stop_shows_version(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows(list(range(41, 53))))
    t = at(bot, DAY, "10:03")
    q = _go(bot, t, a="next", b="issue", v="9")
    m = q["m"]
    _go(bot, t + 1, a="done", b="issue", id=q["next"]["id"], r="hold", v="9")
    d = _go(bot, t + 2, a="done", b="issue", id=q["next"]["id"], r="ok", v="9")
    assert d["left"] == m - 1, (d, m)  # 내일 자리 글은 남은 글로 세지 않음 ('글마다 한 번'이 내일 글 때문에 화면을 다시 열지 않게)
    q = _go(bot, t + 300, a="next", b="issue", v="9")
    _go(bot, t + 301, a="done", b="issue", id=q["next"]["id"], r="stop", n="예약 못 채움 (날짜 칸 못 찾음)", v="9")
    line = autopub.today(bot.db, t + 302, TZ, routine.view(bot, now=t + 302))["by"]["issue"]["line"]
    assert "날짜 칸 못 찾음) (v9)" in line, line
    assert autopub.ver({"v": "9"}) == "9" and autopub.ver({"v": "<b>"}) == "" and autopub.ver({}) == ""


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


# ── 주인 11/26 '하루 지난 속보는 내일 예약 맨 뒤로': 앞날에 쓰고 못 올린 글은 그날 쓴 새 글 뒤 · 하루 상한 남은 자리만
def _rows_saved(saved: dict[int, str], blog: str = "issue") -> dict:
    """v2 posts 의 쓴 시각 (saved_at · Blog-Auto 임시저장 시각)."""
    from dash_helpers import fixture

    data = fixture("export_v2.json")
    tpl = data["posts"]["issue"][0]
    posts = {**data["posts"], blog: [{**tpl, "pid": n, "saved_at": s, "status": "saved"} for n, s in saved.items()]}
    return posts


def _with_saved(tmp_path: Path, ids: list[int], saved: dict[int, str]) -> None:
    from dash_helpers import fixture
    from test_dash_state import write_export

    st = fixture("export_v2.json")["status"]
    write_export(tmp_path / "blogauto.json", posts=_rows_saved(saved), status={**st, "routine": _rows(ids)})


def test_late_last_puts_earlier_day_posts_after_fresh_keeping_order() -> None:
    rows = [{"id": "a", "saved": "2026-11-17T23:50:00+09:00"}, {"id": "b", "saved": "2026-11-18T08:00:00+09:00"},
            {"id": "c", "saved": "2026-11-17T14:59:00Z"},  # = 11/17 23:59 KST → 앞날
            {"id": "d"}, {"id": "e", "saved": "2026-11-17T15:10:00Z"},  # = 11/18 00:10 KST → 그날
            {"id": "f", "saved": "2026-11-16T10:00"}, {"id": "g", "saved": "틀림"}]
    got, back = autopub.late_last(rows, DAY, TZ)
    assert [i["id"] for i in got] == ["b", "d", "e", "g", "a", "c", "f"] and back == {"a", "c", "f"}  # 모르는 쓴 시각 = 새 글
    assert autopub.late_last([], DAY, TZ) == ([], set())


def test_evening_press_reserves_fresh_posts_first_and_yesterdays_leftovers_only_in_spare_room(bot: DealBot, tmp_path: Path) -> None:
    ids = list(range(31, 41))  # 10편: 31~35 어제 쓴 속보·🔥·남은 글 (화면 차례로는 앞) · 36~40 오늘 쓴 밤·새벽·자리 글
    saved = {n: (f"2026-11-17T{10 + n - 31}:00:00+09:00" if n <= 35 else f"2026-11-18T{n - 20}:00:00+09:00") for n in ids}
    _with_saved(tmp_path, ids, saved)
    rv = routine.view(bot, now=at(bot, DAY, "21:10"))
    assert {i["id"]: i.get("saved") for s in rv["slots"] for i in s["items"]}["rsv:issue:31"] == saved[31]  # 줄에 쓴 시각이 실림
    t = at(bot, DAY, "21:10")
    q = _go(bot, t, a="next", b="issue")
    slots = autopub._plans(bot.db)["issue"]["slots"]
    fresh, old = [f"rsv:issue:{n}" for n in range(36, 41)], [f"rsv:issue:{n}" for n in range(31, 36)]
    assert all(k in slots for k in fresh) and len(slots) == autopub.DAY_MAX  # 새 글 5편 다 · 지난 글은 남은 3자리만
    got_old = [k for k in old if k in slots]
    assert got_old == old[:3], slots  # 지난 글 안 차례 그대로
    assert max(slots[k] for k in fresh) < min(slots[k] for k in got_old), slots  # 새 글 뒤에
    assert [slots[k] for k in fresh] == sorted(slots[k] for k in fresh)  # 새 글 안 차례 그대로
    assert q["next"]["id"] == fresh[0]
    order = []
    for k in range(autopub.DAY_MAX):
        nx = _go(bot, t + 300 * k, a="next", b="issue")["next"]
        order.append(nx["id"])
        _go(bot, t + 300 * k + 1, a="done", b="issue", id=nx["id"], r="hold")
        _go(bot, t + 300 * k + 2, a="done", b="issue", id=nx["id"], r="ok")
    assert order == fresh + old[:3]


def test_fresh_post_written_after_first_evening_press_goes_before_yesterdays_planned_posts(bot: DealBot, tmp_path: Path) -> None:
    saved = {n: "2026-11-17T18:00:00+09:00" for n in (31, 32, 33)}
    _with_saved(tmp_path, [31, 32, 33], saved)
    t = at(bot, DAY, "20:05")
    q = _go(bot, t, a="next", b="issue")  # 지난 글만 — 그대로 계획 (아직 넣지 않고 화면을 닫음)
    assert q["next"]["id"] == "rsv:issue:31"
    _with_saved(tmp_path, [31, 32, 33, 34], {**saved, 34: "2026-11-18T20:40:00+09:00"})  # 밤에 새 글
    _go(bot, t + 3600, a="next", b="issue")
    slots = autopub._plans(bot.db)["issue"]["slots"]
    assert slots["rsv:issue:34"] < min(slots[f"rsv:issue:{n}"] for n in (31, 32, 33)), slots
    assert [slots[f"rsv:issue:{n}"] for n in (31, 32, 33)] == sorted(slots[f"rsv:issue:{n}"] for n in (31, 32, 33))
    again = _go(bot, t + 3700, a="next", b="issue")  # 다시 눌러도 같은 계획
    assert autopub._plans(bot.db)["issue"]["slots"] == slots and again["next"]["id"] == "rsv:issue:34"


def test_daytime_press_order_unchanged_by_written_day(bot: DealBot, tmp_path: Path) -> None:
    saved = {31: "2026-11-17T18:00:00+09:00", 32: "2026-11-18T08:00:00+09:00", 33: "2026-11-18T09:00:00+09:00"}
    _with_saved(tmp_path, [31, 32, 33], saved)
    q = _go(bot, at(bot, DAY, "10:03"), a="next", b="issue")
    assert q["next"]["id"] == "rsv:issue:31" and q["next"]["at"] == ""  # 낮: 예전처럼 화면 차례 (첫 글 지금 발행)
