"""대시보드 '오늘 할 일' (주인 10/27 — 블로그 반자동: 발행·예약·이웃·SNS 를 주인이 하루 5번 10~15분씩 손으로. "깔끔하고 부담 없이").

1) 기록 파일 status.routine 을 엄격히 거름 (blogauto._routine): 틀린 id·http 주소·없는 시간대는 빠지고, 글자는 잘리고, 분은 1~15.
2) routine.view: 시간대마다 줄·체크 수·남은 분 · 지금 시간대(대시보드 시간대로 지난 마지막 칸, 09:00 전이면 첫 칸) · 두 번째 출처 자리.
3) routine.done {id, done}: 봇 DB 에 그 목록 날짜별로 · 다른 날 목록엔 안 묻어남 · 틀린 args 는 bad_args.
4) GET /dashboard/api/routine (로그인 아래 · no-store) · routine.js (셸이 defer · 크기 예산은 test_dash_shell · 바깥 주소·innerHTML 없음).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import (
    blogauto_status,
    fixture,
    get_json,
    post_json,
    push_settings,
    session_headers,
)
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import routine
from dealbot.dashboard.actions import BLOG_ACTIONS, clean_args
from dealbot.dashboard.blogauto import _routine, parse_export
from test_dash_state import write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
SLOTS = [{"key": "s1", "at": "09:00", "label": "아침"}, {"key": "s2", "at": "12:30", "label": "점심"},
         {"key": "s3", "at": "15:00", "label": "오후"}, {"key": "s4", "at": "18:00", "label": "저녁"},
         {"key": "s5", "at": "21:00", "label": "밤"}]


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def item(i: str, slot: str = "s1", **kw: object) -> dict:
    return {"id": i, "slot": slot, "group": "이웃", "blog": "finance", "title": f"할 일 {i}", "note": "", "min": 3, "copy": [],
            "links": [], **kw}


def tz_now(bot: DealBot) -> datetime:
    return datetime.now(ZoneInfo(bot.settings.app.timezone))


def at(bot: DealBot, day: str, hm: str) -> float:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=ZoneInfo(bot.settings.app.timezone)).timestamp()


def with_routine(tmp_path: Path, rt: object) -> None:
    st = fixture("export_v2.json")["status"]
    write_export(tmp_path / "blogauto.json", status={**st, "routine": rt})


def sample(day: str) -> dict:
    return {"day": day, "slots": SLOTS, "overflow": 2, "items": [
        item("pub:finance:1", "s1", group="발행", title="아카이브 글 발행", min=5,
             copy=[{"label": "제목 복사", "text": "제목 한 줄\n둘째 줄"}], links=[{"label": "글쓰기", "url": "https://blog.naver.com/x"}]),
        item("nb:issue:a", "s1", blog="issue", min=4),
        item("sns:x:1", "s3", group="SNS", blog="", min=2),
    ]}


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버에서 돌려도 실제 threads-auto(127.0.0.1:8710) 목록이 섞이지 않게"""
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)
    routine._TH_CACHE.clear()  # 2분 기억 (11/14) — 테스트끼리 섞이지 않게


# ---------------------------------------------------------------- 1) 거름
def test_routine_parser_drops_bad_rows_and_clips_text() -> None:
    assert _routine(None) is None and _routine({"day": "2026-10-27"}) is None  # 예전 Blog-Auto (칸 없음)
    assert _routine({"day": "2026-13-40", "items": []}) is None and _routine({"day": "어제", "items": []}) is None
    long = "가" * 500
    got = _routine({"day": "2026-10-27", "slots": SLOTS[:4] + [{"key": "s9", "at": "23:00"}, {"key": "s1", "at": "07:00"}],
                    "overflow": -3, "items": [
                        item("ok-1", title=long, note=long, min=99,
                             copy=[{"label": long, "text": "줄1\n" + "나다 " * 600}, {"label": "빈 글", "text": "  "}, "nope"] + [
                                 {"label": f"c{i}", "text": "t"} for i in range(6)],
                             links=[{"label": "나쁨", "url": "http://evil.example/"}, {"label": "자바", "url": "javascript:alert(1)"},
                                    {"label": "좋음", "url": "https://m.blog.naver.com/a"}, {"label": "따옴", "url": 'https://a.b/"x'}]),
                        item("BAD ID"), item("x" * 81), item("ok-1", title="같은 id 두 번째"), item("s5-row", "s5"),
                        item("no-title", title="  "), item("bad-group", group="쇼핑"), item("evil-blog", blog="../x", min=0),
                        item("ok-2", min=True), {"id": "ok-3"}, "줄 아님"]})
    assert got is not None and got["day"] == "2026-10-27" and got["overflow"] == 0
    assert [s["key"] for s in got["slots"]] == ["s1", "s2", "s3", "s4"] and got["slots"][0]["at"] == "09:00"  # s9·두 번째 s1 은 뺌
    ids = [i["id"] for i in got["items"]]
    assert ids == ["ok-1", "evil-blog", "ok-2"]  # s5 칸이 없으니 s5-row 도 뺌
    one = got["items"][0]
    assert len(one["title"]) == 80 and one["title"].endswith("…") and len(one["note"]) == 160 and one["min"] == 15
    assert len(one["copy"]) == 2 and len(one["copy"][0]["label"]) == 20 and one["copy"][1] == {"label": "c0", "text": "t"}  # 앞 4개만 · 빈 글·줄 아님은 뺌
    assert one["copy"][0]["text"].startswith("줄1\n") and len(one["copy"][0]["text"]) <= 1000
    assert one["links"] == [{"label": "좋음", "url": "https://m.blog.naver.com/a"}]
    assert got["items"][1]["blog"] == "" and got["items"][1]["min"] == 1 and got["items"][2]["min"] == 5
    # 시간대가 하나도 맞지 않으면 기본 5칸 (아침·점심·오후·저녁·밤)
    d = _routine({"day": "2026-10-27", "slots": "x", "items": [item("a", "s5")]})
    assert [s["label"] for s in d["slots"]] == ["아침", "점심", "오후", "저녁", "밤"] and d["items"][0]["slot"] == "s5"


def test_parse_export_puts_routine_next_to_naver_visits(tmp_path: Path) -> None:
    data = fixture("export_v2.json")
    assert parse_export(data, "Asia/Seoul").v2["status"]["routine"] is None
    data["status"]["routine"] = sample("2026-10-27")
    got = parse_export(data, "Asia/Seoul").v2["status"]["routine"]
    assert [i["id"] for i in got["items"]] == ["pub:finance:1", "nb:issue:a", "sns:x:1"] and got["overflow"] == 2


# ---------------------------------------------------------------- 2) 보기
def test_view_groups_by_slot_marks_current_and_counts(bot: DealBot, tmp_path: Path) -> None:
    assert routine.view(bot) == {"day": None, "today": False, "slots": [], "overflow": 0, "done": 0, "total": 0,
                                 "ably": None, "rising": [], "rests": [], "stats": None,
                                 # 11/14: 늘 실리는 칸 — 기록 나이·자동 점검·꾸미기 새 판·못 전한 체크·이웃 글 들르기
                                 "age_min": None, "gate": None, "kkumi_new": True, "relay": 0,
                                 "nb": None}  # 목록 없음 → 칸 숨김 (에이블리 링크 부탁도 없음 — test_dash_ably_requests)
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    v = routine.view(bot, now=at(bot, day, "13:10"))
    assert v["day"] == day and v["today"] is True and v["total"] == 3 and v["done"] == 0 and v["overflow"] == 2
    assert [s["key"] for s in v["slots"]] == ["s1", "s2", "s3", "s4", "s5"]
    assert [s["key"] for s in v["slots"] if s["current"]] == ["s2"]  # 12:30 이 지난 마지막 칸
    s1 = v["slots"][0]
    assert s1["total"] == 2 and s1["min_left"] == 9 and s1["items"][0]["tag"] == "발행 · 드라마"
    assert s1["items"][1]["tag"] == "이웃 · 데런픽" and v["slots"][2]["items"][0]["tag"] == "SNS"
    assert s1["items"][0]["copy"][0]["text"] == "제목 한 줄\n둘째 줄"
    assert [s["key"] for s in routine.view(bot, now=at(bot, day, "07:00"))["slots"] if s["current"]] == ["s1"]  # 09:00 전 → 첫 칸
    assert [s["key"] for s in routine.view(bot, now=at(bot, day, "21:00"))["slots"] if s["current"]] == ["s5"]
    routine.set_done(bot.db, day, "nb:issue:a", True)
    v = routine.view(bot, now=at(bot, day, "09:30"))
    s1 = v["slots"][0]
    assert v["done"] == 1 and s1["done"] == 1 and s1["min_left"] == 5 and s1["items"][1]["done"] is True
    tomorrow = (tz_now(bot) + timedelta(days=1)).date().isoformat()
    assert routine.view(bot, now=at(bot, tomorrow, "10:00"))["today"] is False  # 어제 목록이면 표시


def test_second_source_merges_by_one_function(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """스레드 세션 몫(threads-auto /api/routine) — 꺼져 있으면 [] · 오늘 것만 · 같은 모양으로 걸러 합침."""
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("no key")
    monkeypatch.setattr(threads_proxy, "_call", off)
    assert routine.threads_items(bot) == [] and routine.threads_items in routine.EXTRA_SOURCES
    today_ = tz_now(bot).date().isoformat()
    row = {"id": "th:x:thback:0", "slot": "s1", "group": "SNS", "title": "스레드 맞팔 1", "copy": [], "links": []}
    seen: list[tuple] = []

    def ok(*a: object) -> tuple:
        seen.append(a)
        return 200, json.dumps({"day": today_, "items": [row]}).encode(), "application/json"
    assert routine.threads_items(bot) == []  # 못 읽은 것도 2분 기억 (꺼진 서비스를 요청마다 기다리지 않게 — 주인 11/14 '화면이 굳음')
    monkeypatch.setattr(threads_proxy, "_call", ok)
    assert routine.threads_items(bot) == [] and seen == []
    routine._TH_CACHE.clear()
    assert routine.threads_items(bot) == [row] and routine.threads_items(bot) == [row]
    assert seen == [("/api/routine", None, 1.0)]  # 1초만 기다림 · 두 번째는 기억한 것
    routine._TH_CACHE.clear()
    monkeypatch.setattr(threads_proxy, "_call", lambda *a: (200, json.dumps({"day": "2000-01-01", "items": [row]}).encode(), ""))
    assert routine.threads_items(bot) == []  # 어제 목록은 안 씀

    def never(*a: object) -> None:
        raise AssertionError("체크 날짜를 정하는 데 다른 출처를 부르면 안 됨")
    routine._TH_CACHE.clear()
    monkeypatch.setattr(threads_proxy, "_call", never)
    assert routine.day_of(bot, tz_now(bot).timestamp()) == today_  # day_of 는 블로그 몫만 봄
    monkeypatch.setattr(threads_proxy, "_call", off)
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))

    def extra(_bot: object) -> list[dict]:
        return [item("th:1", "s4", group="SNS", blog=""), item("nb:issue:a", "s4"), item("BAD"), item("th:2", "s9")]

    def boom(_bot: object) -> list[dict]:
        raise RuntimeError("threads-auto 꺼짐")

    monkeypatch.setattr(routine, "EXTRA_SOURCES", (extra, boom))
    v = routine.view(bot, now=at(bot, day, "18:30"))
    assert v["total"] == 4 and [i["id"] for i in v["slots"][3]["items"]] == ["th:1"]  # 같은 id·틀린 줄·없는 칸은 뺌, 터진 출처는 건너뜀
    write_export(tmp_path / "blogauto.json")  # 블로그 목록이 없어도 다른 출처 줄은 보임 (오늘 날짜 · 기본 5칸)
    v = routine.view(bot)
    assert v["day"] == tz_now(bot).date().isoformat() and v["total"] == 2 and v["slots"][3]["total"] == 2


# ---------------------------------------------------------------- 3) 체크
async def test_routine_done_action_toggles_and_keeps_days_apart(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    h = session_headers(bot)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb:issue:a", "done": True}}, h)
    assert status == 200 and got["status"] == "ok" and got["code"] == "done" and got["text"] == "체크했어요"
    assert json.loads(bot.db.kv_get(f"routine_done:{day}")) == ["nb:issue:a"]
    status, v, headers = await get_json(bot, "/dashboard/api/routine", h)
    assert status == 200 and headers["Cache-Control"] == "no-store" and v["done"] == 1 and v["total"] == 3
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb:issue:a", "done": False}}, h)
    assert status == 200 and got["text"] == "체크를 풀었어요" and json.loads(bot.db.kv_get(f"routine_done:{day}")) == []
    for bad in ({"id": "BAD ID", "done": True}, {"id": "a", "done": "yes"}, {"id": 3, "done": True}, {"done": True}, {"id": "x" * 81, "done": True}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": bad}, h)
        assert status == 400 and got["code"] == "bad_args", bad
    # 다른 날 목록: 어제 체크는 안 묻어나고, 이틀 넘은 기록은 지움
    old = (tz_now(bot) - timedelta(days=3)).date().isoformat()
    bot.db.kv_set(f"routine_done:{old}", json.dumps(["nb:issue:a"]))
    routine.set_done(bot.db, day, "sns:x:1", True)
    assert bot.db.kv_get(f"routine_done:{old}") is None
    tomorrow = (tz_now(bot) + timedelta(days=1)).date().isoformat()
    with_routine(tmp_path, sample(tomorrow))
    assert routine.view(bot)["done"] == 0
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "sns:x:1", "done": True}}, h)
    assert status == 200 and json.loads(bot.db.kv_get(f"routine_done:{tomorrow}")) == ["sns:x:1"]  # 그 목록의 날짜로 적음
    assert json.loads(bot.db.kv_get(f"routine_done:{day}")) == ["sns:x:1"]  # 전날 것은 남김


async def test_publish_row_check_also_tells_blogauto_it_was_pasted(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """주인 11/09 '붙여넣기 대기로 발행한 글들이 블로그 탭에 그대로 남아 있다': 발행·예약 줄(pub:/rsv:) 체크는 blog.pasted 도 맡김 —
    Blog-Auto 가 '발행 확인 중'으로 기다리는 글에서 빼고 직접 발행 확인이 주소를 붙임. 이웃 줄·예전 Blog-Auto 는 체크만."""
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    h = session_headers(bot)
    new = spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new"

    def queued() -> list[dict]:
        return [spool.read(f) for f in sorted(new.glob("*.json"))] if new.exists() else []

    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "rsv:issue:270", "done": True}}, h)
    # 블로그 프로그램 신호 없음 → 체크는 남기고 깨어나면 전함 (주인 11/14 — 예전엔 조용히 체크만 해서 같은 글이 다시 떴음)
    assert status == 200 and got["status"] == "ok" and got["text"] == "체크했어요 — 블로그 프로그램이 깨어나면 전해요" and queued() == []
    assert [(x["name"], x["rid"], x["args"]) for x in routine.relay_rows(bot.db)] == [("pasted", "rsv:issue:270", {"blog": "issue", "pid": 270, "done": True})]
    bot.db.kv_set(routine.RELAY_KEY, "[]")
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "pasted"])
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "rsv:issue:270", "done": True}}, h)
    assert status == 200 and got["text"] == "체크했어요" and queued() == []  # 예전 Blog-Auto
    blogauto_status(settings)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb:issue:a", "done": True}}, h)
    assert status == 200 and got["text"] == "체크했어요" and queued() == []  # 이웃 줄은 글이 아님
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "rsv:issue:270", "done": True}}, h)
    assert status == 200 and got["status"] == "ok" and "발행 확인 중" in got["text"]
    assert json.loads(bot.db.kv_get(f"routine_done:{day}")) == ["nb:issue:a", "rsv:issue:270"]
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "pub:hotdeal:289", "done": False}}, h)
    assert status == 200 and "다시 기다리는 글" in got["text"]  # 바로 풀어도 '하는 중'으로 막히지 않음
    q = queued()
    assert [(x["action"], x["blog"], x["args"]) for x in q] == [
        ("pasted", "issue", {"blog": "issue", "pid": 270, "done": True}),
        ("pasted", "hotdeal", {"blog": "hotdeal", "pid": 289, "done": False})]
    assert all(x["confirmed"] is False for x in q)
    assert "pasted" in BLOG_ACTIONS and clean_args("blog.pasted", {"blog": "issue", "pid": 3, "done": True}) == {"blog": "issue", "pid": 3, "done": True}


async def test_neighbour_request_check_tells_blogauto(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """주인 11/14 '서이추 명단 … 블로그마다 30명': 서로이웃 신청 줄(nb-add:) 체크는 blog.nb_requested 도 맡김 — Blog-Auto 가 신청했다고
    적어 다음 명단·다른 블로그 명단에서 뺌. 예전 Blog-Auto(nb_requested 모름)는 체크만."""
    day = tz_now(bot).date().isoformat()
    rows = [item(f"nb-add:hotdeal:fan{i:02d}", "s4", blog="hotdeal", min=1, note=f'최근 "LG 트윈스 직관 일기 {i}" 글 (오늘)',
                 copy=[{"label": "블로그 아이디", "text": f"fan{i:02d}"}, {"label": "멘트", "text": "LG 트윈스 글 잘 읽었어요."}])
            for i in range(110)]
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": rows})
    assert len(_routine({"day": day, "slots": SLOTS, "items": rows})["items"]) == 110  # 이웃 105줄 + 글도 다 실림 (예전 80 에서 잘림)
    h = session_headers(bot)
    new = spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new"

    def queued() -> list[dict]:
        return [spool.read(f) for f in sorted(new.glob("*.json"))] if new.exists() else []

    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "nb_requested"])
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb-add:hotdeal:fan01", "done": True}}, h)
    assert status == 200 and got["text"] == "체크했어요" and queued() == []  # 예전 Blog-Auto
    blogauto_status(settings)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb-add:hotdeal:fan02", "done": True}}, h)
    assert status == 200 and "다음 명단에서 빼요" in got["text"]
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "nb-add:hotdeal:fan02", "done": False}}, h)
    assert status == 200 and got["text"] == "체크를 풀었어요"
    assert [(x["action"], x["blog"], x["args"]) for x in queued()] == [
        ("nb_requested", "hotdeal", {"blog": "hotdeal", "target": "fan02", "done": True}),
        ("nb_requested", "hotdeal", {"blog": "hotdeal", "target": "fan02", "done": False})]
    assert "nb_requested" in BLOG_ACTIONS
    assert clean_args("blog.nb_requested", {"blog": "issue", "target": "ab_1", "done": True}) == {"blog": "issue", "target": "ab_1", "done": True}
    for bad in ({"blog": "issue", "target": "../x", "done": True}, {"blog": "issue", "target": "ab", "done": "y"},
                {"blog": "nope", "target": "ab", "done": True}):
        with pytest.raises(Exception):
            clean_args("blog.nb_requested", bad)


async def test_routine_api_needs_login(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/routine", anon))[0] == 401
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert status == 200 and v["total"] == 0 and v["slots"] == []


# ---------------------------------------------------------------- 4) routine.js
def test_routine_js_is_local_safe_and_korean() -> None:
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js and "new Function" not in js
    assert "/dashboard/api/routine" in js and "X-Dash-Token" in js and "MutationObserver" in js and "routine.done" in js
    assert "clipboard" in js and "writeText(" in js and "type: 'checkbox'" not in js  # 체크는 단추 (app.js 그리기를 막지 않게)
    assert js.count("'input'") == 1 and "placeholder: '여기에 링크 붙여넣기'" in js  # 입력 칸은 에이블리 링크 부탁 하나뿐 (10/28)
    assert "nb-add:" in js and "멘트 · " in js  # 서로이웃 줄: 고른 이유 + 멘트 글 (주인 11/14)
    for word in ("오늘 할 일", "복사했어요", "다시 보기", "지금"):
        assert word in js, word
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "routine.js")], capture_output=True).returncode == 0


# 작은 가짜 DOM (jsdom 없이): 할 일 탭에 붙는지 · 지금 칸만 펼쳤는지 · 체크가 X-Dash-Token 으로 routine.done 을 보내고 3/8 이 바로 바뀌는지 ·
# 복사 단추 · 다시 그려도 같은 칸 · 다른 탭엔 안 붙는지 · 목록이 없으면 숨는지
DOM_SHIM = r"""
const calls = [], out = {}, clip = [];
let observer = null;
class T { constructor(s) { this.data = s; this.parentNode = null; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.ls = {}; this.parentNode = null; this.className = ''; this._t = ''; this.hidden = false; this.style = {}; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  _add(c, front) { if (!c.nodeType) c = new T(String(c)); if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; front ? this.children.unshift(c) : this.children.push(c); }
  append(...cs) { cs.forEach(c => this._add(c, false)); this._n(); }
  prepend(...cs) { cs.reverse().forEach(c => this._add(c, true)); this._n(); }
  replaceChildren(...cs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; cs.forEach(c => this._add(c, false)); this._n(); }
  _n() { if (observer && observer.t === this) queueMicrotask(observer.cb); }
  all(f, acc = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) acc.push(c); c.all(f, acc); } return acc; }
  click() { (this.ls.click || []).forEach(f => f({currentTarget: this})); }
}
const ids = {}, mk = (t, id) => { const e = new E(t); ids[id] = e; return e; };
const main = mk('main', 'main'), boot = mk('script', 'boot'), toast = mk('div', 'toast'), rf = mk('button', 'rf');
boot.textContent = JSON.stringify({me: {csrf: 'tok123'}});
main.append(new E('p'));
global.location = {hash: '#/todo'};
global.window = {addEventListener() {}, localStorage: {getItem: () => 'slot'}, navigator: {clipboard: {writeText: async t => { clip.push(t); }}}};
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.t = t; observer = this; } };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), getElementById: i => ids[i] || null,
  createElement: t => new E(t), addEventListener() {}};
const VIEW = JSON.parse(process.argv[2]);
global.fetch = async (url, opt) => { calls.push({url, opt: opt || {}}); return url === '/dashboard/api/routine'
  ? {status: 200, json: async () => VIEW} : {status: 200, json: async () => ({status: 'ok', code: 'done', text: '체크했어요'})}; };
const tick = () => new Promise(r => setTimeout(r, 5));
const h2 = b => b.all(c => c.tagName === 'H2')[0].textContent;
(async () => {
  require(process.argv[1]);
  await tick(); await tick();
  const box = () => main.children.find(c => c.attrs.id === 'rtp');
  out.first = main.firstChild.attrs.id; out.hidden = box().hidden;
  out.head = h2(box());
  out.heads = box().all(c => c.className.startsWith('sl')).map(c => [c.textContent, c.attrs['aria-expanded']]);
  out.items = box().all(c => c.className.startsWith('it')).map(c => c.attrs['data-id']);
  out.bar = box().all(c => c.tagName === 'I')[0].style.width;
  box().all(c => c.tagName === 'BUTTON' && c.textContent === '제목 복사')[0].click();
  await tick();
  out.clip = clip; out.copyToast = toast.textContent;
  out.links = box().all(c => c.tagName === 'A').map(a => [a.attrs.href, a.attrs.target, a.attrs.rel]);
  box().all(c => c.className === 'cb')[1].click();
  out.optimistic = h2(box());
  await tick(); await tick();
  const post = calls.find(c => c.url === '/dashboard/api/action');
  out.post = {token: post.opt.headers['X-Dash-Token'], body: JSON.parse(post.opt.body)};
  out.after = h2(box()); out.checked = box().all(c => c.className === 'cb').map(c => c.attrs['aria-checked']);
  box().all(c => c.className.startsWith('sl'))[2].click();  // 접힌 칸 펼치기
  out.opened = box().all(c => c.className.startsWith('it')).map(c => c.attrs['data-id']);
  main.replaceChildren(new E('div'), new E('div'));  // app.js 가 할 일 탭을 다시 그림
  await tick();
  out.again = main.firstChild.attrs.id; out.keep = h2(box());
  location.hash = '#/connect';
  main.replaceChildren(new E('div'));
  await tick();
  out.connect = main.children.some(c => c.attrs.id === 'rtp');
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def test_routine_js_mounts_on_the_todo_tab_and_checks(bot: DealBot, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, sample(day))
    view = routine.view(bot, now=at(bot, day, "10:00"))
    out = subprocess.run([node, "-e", DOM_SHIM, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == "rtp" and got["hidden"] is False and got["head"] == "오늘 할 일 · 0/3" and got["bar"] == "0%"
    assert got["heads"][0] == ["▾ 아침 09:00" + "0/2 · 약 9분 · 지금", "true"]  # 지금 칸만 펼침
    assert [x[1] for x in got["heads"]] == ["true", "false", "false", "false", "false"] and got["heads"][1][0].endswith("없음")
    assert got["items"] == ["pub:finance:1", "nb:issue:a"]
    assert got["clip"] == ["제목 한 줄\n둘째 줄"] and got["copyToast"] == "복사했어요"
    assert got["links"] == [["https://blog.naver.com/x", "_blank", "noopener noreferrer"]]
    assert got["optimistic"] == "오늘 할 일 · 1/3" and got["after"] == "오늘 할 일 · 1/3" and got["checked"] == ["false", "true"]
    assert got["post"] == {"token": "tok123", "body": {"action": "routine.done", "args": {"id": "nb:issue:a", "done": True},
                                                       "cid": got["post"]["body"]["cid"], "ctoken": None}}
    assert got["opened"] == ["pub:finance:1", "nb:issue:a", "sns:x:1"]
    assert got["again"] == "rtp" and got["keep"] == "오늘 할 일 · 1/3" and got["connect"] is False
    # 목록이 없으면 칸 전체를 숨김
    empty = routine.view(bot, now=at(bot, day, "10:00")) | {"slots": [], "total": 0, "stats": None}  # '오늘 통계 보내기' 줄만 있어도 보임 (11/11)
    out = subprocess.run([node, "-e", DOM_SHIM.split("  out.head = ")[0] + "  console.log(JSON.stringify(out)); process.exit(0);\n})();",
                          str(STATIC / "routine.js"), json.dumps(empty, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"first": "rtp", "hidden": True}


def test_best_times_pass_through_and_bad_rows_drop(bot: DealBot, tmp_path: Path) -> None:
    """블로그별 예약 추천 시각 (주인 10/29 — Blog-Auto routine.best_times) — 블로그마다 한 줄, 시각은 HH:MM 또는 '바로'만."""
    times = [{"blog": "hotdeal", "name": "핫딜", "times": ["바로"], "note": "딜이 끝나기 전에"},
             {"blog": "finance", "name": "아카이브", "times": ["07:30", "25:00", "<b>", 3, "21:00"], "note": "출근길"},
             {"blog": "finance", "name": "또", "times": ["09:00"]}, {"blog": "x", "times": ["09:00"]},
             {"blog": "issue", "name": "데런픽", "times": []}]
    got = _routine({"day": "2026-10-29", "items": [], "times": times})["times"]
    assert got == [{"blog": "hotdeal", "name": "핫딜", "times": ["바로"], "note": "딜이 끝나기 전에"},
                   {"blog": "finance", "name": "아카이브", "times": ["07:30", "21:00"], "note": "출근길"}]
    assert _routine({"day": "2026-10-29", "items": []})["times"] == []  # 예전 Blog-Auto
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, {**sample(day), "times": times})
    assert [t["blog"] for t in routine.view(bot)["times"]] == ["hotdeal", "finance"]
    js = (Path(__file__).resolve().parents[1] / "src/dealbot/dashboard/static/routine.js").read_text(encoding="utf-8")
    assert "블로그별 예약 추천 시각" not in js and "data.times" not in js  # 칸은 뺐고 데이터만 둠 (주인 11/14)


def test_rising_keywords_start_a_pick_post() -> None:
    """11/09 주인 '막 뜨는 중 … Pick 쓰기 눌러도 연결이 안 돼': 키워드마다 단추 → blog.issue {keyword} (예전엔 화면만 옮김)."""
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "action: 'blog.issue', args: {keyword: k}" in js and "xs.map(k => el('button'" in js and "href: '#/blog/issue'" not in js


def test_todo_tab_groups_by_blog() -> None:
    """11/09 주인 '한 블로그 로그인하면 그 블로그 작업 싹 몰아서 — 할 일 탭 블로그별로, 이웃추가도 거기에'."""
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "W.SBRoutine = {load, mount, ids, by, part, cnt, meta}" in rt
    assert "filter(i => (i.blog || (i.group === 'SNS' ? 'sns' : '')) === k)" in rt and "'이웃': 2" in rt  # 그 블로그 손일 (이웃 신청·공감 포함) · SNS 손일은 SNS 칸
    assert "setBy(by() ? 'slot' : 'blog')" in rt  # 예전 시간대별로 돌아가기
    # 11/14 주인 '할 일 첫 화면은 종류별 목록만': 블로그 셋 → 딜 → SNS → 숏폼 → 기타 (이벤트는 ssalmuk.js) — 칸마다 data-k
    assert "W.SBRoutine.part(k, l," in app and ("[['issue', '데런픽'], ['finance', '드라마'], ['hotdeal', '라커룸'], ['deals', '딜'], ['sns', 'SNS'], "
                                                "['shorts', '숏폼·릴스·룩북'], ['', '기타']]") in app
    assert "'data-k': k" in rt and "cnt[k] = {n, m, u: hn, w};" in rt  # w = 만드는 중인 글 (남은 일로 안 셈 — 11/14)


def test_todo_tab_puts_urgent_first() -> None:
    """11/09 주인 '할 일 탭 급한 순으로 — 중요도 순': 칸 안에서 '급한 것' 맨 위 · 급한 수는 종류 목록(todohub.js)의 빨간 점.
    11/14 부터 칸 순서는 고정 (종류 목록) — 종류 화면에선 칸을 늘 펼침."""
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (hot.length) body.push(sh('급한 것', hot.length), hot);" in rt and "W.SBHub || (bo[k] == null ? k === 'issue' : bo[k])" in rt
    assert ".sort((a, b) => b[4] - a[4])" not in app and "i.sev === 'stop' || i.sev === 'hot'" in app


def test_todo_tab_has_video_box() -> None:
    """11/09 주인 '릴스·쇼츠·룩북 등도 다 할 일 탭에': 쇼츠 서버 올리기 일정 줄(밀린·오늘·내일) + 룩북 카드가 '숏폼·릴스·룩북' 칸에."""
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "fetch('/dashboard/api/uploads'" in rt and "action: 'uploads.done'" in rt and "'검수 대기 · 아직 올리지 마세요'" in rt
    assert "['shorts', '숏폼·릴스·룩북']" in app and "i.kind !== 'uploads'" in app


def test_post_rows_sorted_by_score_with_chip(bot: DealBot, tmp_path: Path) -> None:
    """11/10 주인 '할 일 탭 점수 높은 순으로 — 블로그 안에서도': 발행·예약 줄에 인기 예상 점수 칩, 같은 시간대 안에서 점수 높은 순,
    점수 모르는 줄(이웃·SNS·점수 없는 글)은 원래 차례 그대로 뒤에."""
    day = tz_now(bot).date().isoformat()
    data = fixture("export_v2.json")
    for p in data["posts"]["issue"]:
        p["popular"] = p["pid"] == 46
    st = data["status"]
    rt = {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("nb:issue:a", "s1", blog="issue"),
        item("pub:issue:48", "s1", group="발행", blog="issue", note="사진 5장"),
        item("pub:issue:999", "s1", group="발행", blog="issue"),
        item("rsv:issue:46", "s1", group="예약", blog="issue"),
        item("pub:issue:47", "s1", group="발행", blog="issue"),
    ]}
    write_export(tmp_path / "blogauto.json", posts=data["posts"], status={**st, "routine": rt})
    its = routine.view(bot, now=at(bot, day, "09:30"))["slots"][0]["items"]
    assert [i["id"] for i in its] == ["rsv:issue:46", "pub:issue:47", "pub:issue:48", "nb:issue:a", "pub:issue:999"]
    # 11/10 주인 '점수도 글 옆에': 칩은 회색 줄(note) 앞이 아니라 제목 옆 (sc·hot → routine.js 알약)
    assert its[0]["sc"] == "🔥 82점" and its[0]["hot"] is True and its[0]["sco"] == 82 and "점" not in its[0]["note"]
    assert its[2]["sc"] == "60점" and its[2]["hot"] is False and its[2]["note"] == "사진 5장"
    assert "sco" not in its[3] and "sc" not in its[3] and "sco" not in its[4]
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "a.done - b.done || nr(a) - nr(b) || (b.sco + 1 || 0) - (a.sco + 1 || 0)" in js  # 블로그별 칸: 준비된 글 먼저 → 점수 높은 순 (점수 모르는 줄은 뒤)
    assert "el('span', {class: 't'}, it.title, it.sc ? el('i', {class: 'sc' + (it.hot ? ' h' : '')}, it.sc) : null," in js


def test_row_own_score_wins_even_if_post_missing_from_v2_posts(bot: DealBot, tmp_path: Path) -> None:
    """11/10 19:37 주인 화면 '점수가 안 뜨고 정렬도 안 됨': v2 posts 는 블로그마다 잘려서 많은 예약 줄이 점수를 못 찾았음 →
    Blog-Auto 가 줄에 직접 실은 score·popular 를 먼저 쓴다 (v2 posts 에 없는 글도 칩·정렬)."""
    day = tz_now(bot).date().isoformat()
    data = fixture("export_v2.json")
    st = data["status"]
    rt = {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("rsv:issue:7001", "s1", group="예약", blog="issue", score=12),
        item("nb:issue:a", "s1", blog="issue"),
        item("rsv:issue:7002", "s1", group="예약", blog="issue", score=91, popular=True),
        item("rsv:issue:7003", "s1", group="예약", blog="issue", score=55),
        item("rsv:issue:7004", "s1", group="예약", blog="issue", score="99"),  # 틀린 값은 없는 것으로
    ]}
    write_export(tmp_path / "blogauto.json", posts=data["posts"], status={**st, "routine": rt})
    its = routine.view(bot, now=at(bot, day, "09:30"))["slots"][0]["items"]
    assert [i["id"] for i in its] == ["rsv:issue:7002", "rsv:issue:7003", "rsv:issue:7001", "nb:issue:a", "rsv:issue:7004"]
    assert its[0]["sc"] == "🔥 91점" and its[0]["hot"] is True and its[1]["sc"] == "55점" and its[2]["sco"] == 12
    assert "sc" not in its[4]


# ---------------------------------------------------------------- 5) 못 쓰는 묶음 · 자동 점검 · 못 전한 체크 (주인 11/14)
BASE = "https://bot.example.com"
READY = {"title": "준비된 글", "html": "<p>본문이에요</p>", "tags": ["가"], "cards": 0, "photos": 1, "stale": False}


def pub(i: str, **kw: object) -> dict:
    return item(i, "s1", group="발행", blog="hotdeal", **kw)


def test_stale_bundle_has_no_html_in_both_export_shapes() -> None:
    """주인 11/14 '만드는 중인 옛 묶음이 복사됨': 새 모양(paste {title, stale: true, why, reason} — html 없음)과 예전 모양(html 은 있는데
    note 가 '꾸밈 새 원고 만드는 중…') 둘 다 stale True · html·text 빈 글. 점검에 걸린 줄은 gate.ok False + 쉬운 말 한마디."""
    from dealbot.dashboard.blogauto import _paste, _routine_item

    new = _routine_item(pub("pub:hotdeal:1", paste={"title": "새 모양", "tags": ["가", "나"], "photos": 4, "stale": True, "why": "building",
                                                     "reason": "새 원고로 다시 만드는 중"}), media_base=BASE)
    assert new["paste"] == {"title": "새 모양", "stale": True, "why": "building", "reason": "새 원고로 다시 만드는 중", "html": "", "text": "",
                            "tags": ["가", "나"], "tag_text": "#가 #나", "cards": 0, "photos": 4}
    # 공개 주소가 없어도 (사진 주소를 만들 일이 없음) · html 이 딸려 와도 쓰지 않음 · 모르는 why 는 building
    sneaky = _paste({"title": "t", "stale": True, "why": "???", "html": "<p>옛 본문</p>"}, None)
    assert sneaky["stale"] is True and sneaky["html"] == "" and sneaky["text"] == "" and sneaky["why"] == "building"
    failed = _paste({"title": "t", "stale": True, "why": "failed", "reason": "KeyError"}, BASE)
    assert failed["why"] == "failed" and failed["reason"] == "묶음을 못 만들어 다시 하는 중"  # 영어 오류 이름은 쉬운 말로
    assert _paste({"title": "t", "stale": True, "why": "failed", "reason": "사진 3장이 없음"}, BASE)["reason"] == "사진 3장이 없음"
    assert _paste({"stale": True}, BASE) is None and _paste({"title": "t", "stale": "yes"}, BASE) is None  # 제목 없음 · stale 이 참이 아니면 예전 길 (html 없음 → None)
    old = _routine_item(pub("pub:hotdeal:2", note="꾸밈 새 원고 만드는 중 — 이 말이 없어지면 붙여넣기 (11/08)", paste=dict(READY, title="예전 모양")),
                        media_base=BASE)
    assert old["paste"]["stale"] is True and old["paste"]["html"] == "" and old["paste"]["text"] == "" and old["paste"]["why"] == "building"
    assert old["paste"]["title"] == "예전 모양" and old["paste"]["tags"] == ["가"] and old["paste"]["photos"] == 1
    ok = _routine_item(pub("pub:hotdeal:3", paste=READY, gate={"ok": True, "codes": []}), media_base=BASE)
    assert ok["paste"]["stale"] is False and ok["paste"]["html"] == "<p>본문이에요</p>" and ok["gate"] == {"ok": True, "codes": [], "text": ""}
    bad = _routine_item(pub("pub:hotdeal:4", paste=READY, gate={"ok": False, "codes": ["G4", "G2", "<x>", 7, "G1"]}), media_base=BASE)
    assert bad["gate"] == {"ok": False, "codes": ["G4", "G2", "G1"], "text": "같은 사진이 두 번 들어가서"}
    assert _routine_item(pub("pub:hotdeal:5", paste=READY, gate={"ok": False, "codes": ["ZZ9"]}), media_base=BASE)["gate"]["text"] == "자동 점검에 걸려서"
    for junk in ("ok", {"ok": "true"}, {"codes": ["G1"]}, ["G1"], None, 1):  # 틀린 모양은 칸을 뺌 (줄은 예전처럼)
        assert "gate" not in _routine_item(pub("pub:hotdeal:6", paste=READY, gate=junk), media_base=BASE)
    assert "gate" not in _routine_item(item("nb:issue:a", gate={"ok": False, "codes": ["G1"]}), media_base=BASE)  # 글 줄만


def test_view_carries_gate_summary_and_export_age(bot: DealBot, tmp_path: Path) -> None:
    day = tz_now(bot).date().isoformat()
    rt = {"day": day, "slots": SLOTS, "overflow": 0, "gate": {"checked": 3, "ok": False, "blocked": [{"id": "pub:hotdeal:4", "codes": ["G4"]}],
                                                              "at": f"{day}T17:40:05"},
          "items": [pub("pub:hotdeal:3", paste=READY, gate={"ok": True, "codes": []}), pub("pub:hotdeal:4", paste=READY, gate={"ok": False, "codes": ["G4"]}),
                    pub("pub:hotdeal:5", paste=READY, gate={"ok": True, "codes": ["G8"]}), item("nb:issue:a")]}
    with_routine(tmp_path, rt)
    v = routine.view(bot, now=at(bot, day, "18:00"))
    assert v["gate"] == {"checked": 3, "blocked": 1, "ok": False, "hm": "17:40", "line": "자동 점검: 1편 고치는 중 · 2편 통과 (17:40)"}
    assert isinstance(v["age_min"], int) and v["today"] is True and v["relay"] == 0
    rt["items"][1]["gate"] = {"ok": True, "codes": []}
    rt["gate"] = "깨진 값"
    with_routine(tmp_path, rt)
    assert routine.view(bot, now=at(bot, day, "18:00"))["gate"]["line"] == "자동 점검: 3편 모두 통과"  # 시각을 모르면 빼고
    for it in rt["items"]:
        it.pop("gate", None)
    with_routine(tmp_path, rt)
    assert routine.view(bot, now=at(bot, day, "18:00"))["gate"] is None  # 예전 Blog-Auto — 줄 없음
    # 기록이 쓰인 지 몇 분 (화면이 30분 넘으면 알림)
    from datetime import UTC

    wrote = datetime.fromtimestamp(at(bot, day, "12:00"), UTC).astimezone(ZoneInfo(bot.settings.app.timezone)).isoformat(timespec="seconds")
    write_export(tmp_path / "blogauto.json", status={**fixture("export_v2.json")["status"], "routine": rt}, written_at=wrote)
    assert routine.view(bot, now=at(bot, day, "12:47"))["age_min"] == 47


STALE_FLOW = r"""
  const box = () => main.children.find(c => c.attrs.id === 'rtp');
  const rows = () => box().all(c => c.className.startsWith('it')).map(r => ({id: r.attrs['data-id'],
    btns: r.all(c => c.tagName === 'BUTTON' && c.className === 'sm').map(b => [b.textContent, 'disabled' in b.attrs, !!(b.ls.click || []).length]),
    chips: r.all(c => c.tagName === 'I' && c.className.startsWith('sc')).map(c => c.textContent)}));
  out.head = box().all(c => c.tagName === 'H2')[0].textContent;
  out.rows = rows();
  const copyBtn = () => box().all(c => c.tagName === 'BUTTON' && c.textContent === '본문 복사');
  out.copyBtns = copyBtn().length;
  copyBtn()[0].click(); await tick();
  out.copied = wrote.length; out.toast1 = toast.textContent;
  // 받은 지 10분 넘은 목록: 복사하지 않고 먼저 새로 받음
  const n0 = calls.filter(c => c.url === '/dashboard/api/routine').length, real = Date.now;
  Date.now = () => real() + 11 * 60000;
  copyBtn()[0].click(); await tick(); await tick();
  out.copiedOld = wrote.length; out.reloads = calls.filter(c => c.url === '/dashboard/api/routine').length - n0; out.toast2 = toast.textContent;
  copyBtn()[0].click(); await tick();  // 새로 받은 뒤 한 번 더 → 복사
  out.copiedAgain = wrote.length;
  // 새로 받지 못하면(서버·인터넷 끊김) 복사도 안 하고 그렇게 알림
  const f = global.fetch;
  global.fetch = async () => { throw new Error('offline'); };
  Date.now = () => real() + 23 * 60000;
  copyBtn()[0].click(); await tick(); await tick();
  out.copiedOffline = wrote.length; out.toast3 = toast.textContent;
  global.fetch = f; Date.now = () => real() + 11 * 60000;
  out.meta = window.SBRoutine.meta();
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def test_not_ready_rows_cannot_be_copied(bot: DealBot, tmp_path: Path) -> None:
    """화면: 만드는 중·못 만든 묶음·점검에 걸린 글은 누를 수 없는 단추 하나 (본문 복사 없음 · 제목 복사는 그대로) · 준비된 글이 먼저 ·
    '붙여넣기 대기 (준비 n · 만드는 중 m)' · 통과한 글은 '점검 통과' 칩 · 10분 넘은 목록은 복사 전에 새로 받기."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    bot.dashboard.ctx.blogauto.media_base = BASE  # 사진 주소를 만들 공개 주소 (없으면 묶음이 안 실림)
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        pub("pub:hotdeal:1", paste={"title": "만드는 중", "stale": True, "why": "building", "reason": "새 원고로 다시 만드는 중"}),
        pub("pub:hotdeal:2", note="꾸밈 새 원고 만드는 중 — 이 말이 없어지면 붙여넣기 (11/08)", paste=dict(READY, title="예전 모양")),
        pub("pub:hotdeal:3", paste={"title": "못 만든 글", "stale": True, "why": "failed", "reason": "사진 3장이 없음"}),
        pub("pub:hotdeal:4", paste=dict(READY, title="점검에 걸린 글"), gate={"ok": False, "codes": ["G4"]}),
        pub("pub:hotdeal:5", paste=dict(READY, title="준비된 글"), gate={"ok": True, "codes": []}),
        pub("pub:hotdeal:6", paste=dict(READY, title="점검 전 글")),
    ]})
    view = routine.view(bot, now=at(bot, day, "10:00"))
    by_id = {i["id"]: i for s in view["slots"] for i in s["items"]}
    assert [by_id[f"pub:hotdeal:{n}"]["paste"]["stale"] for n in range(1, 7)] == [True, True, True, False, False, False]
    assert all(by_id[f"pub:hotdeal:{n}"]["paste"]["html"] == "" for n in (1, 2, 3)) and by_id["pub:hotdeal:5"]["paste"]["html"]
    shim = DOM_SHIM.replace("navigator: {clipboard: {writeText: async t => { clip.push(t); }}}",
                            "ClipboardItem: class { constructor(o) { this.o = o; } }, "
                            "navigator: {clipboard: {writeText: async t => { clip.push(t); }, write: async x => { wrote.push(x); }}}") \
        .replace("const calls = [], out = {}, clip = [];", "const calls = [], out = {}, clip = [], wrote = [];")
    assert "ClipboardItem" in shim and "wrote = []" in shim
    flow = shim.split("  const box = () =>")[0] + STALE_FLOW
    out = subprocess.run([node, "-e", flow, str(STATIC / "routine.js"), json.dumps(view, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["head"] == "붙여넣기 대기 (준비 2 · 만드는 중 4)"
    rows = {r["id"]: r for r in got["rows"]}
    assert [r["id"] for r in got["rows"]][:2] == ["pub:hotdeal:5", "pub:hotdeal:6"]  # 준비된 글이 먼저
    for n, label in ((1, "새 원고 만드는 중 — 잠시 뒤"), (2, "새 원고 만드는 중 — 잠시 뒤"), (3, "고치는 중 · 사진 3장이 없음"),
                     (4, "고치는 중 · 같은 사진이 두 번 들어가서")):
        btns = rows[f"pub:hotdeal:{n}"]["btns"]
        assert btns == [["제목 복사", False, True], [label, True, False]], btns  # 못 누르는 단추 하나 · 누르는 손잡이 없음 · 본문 복사 없음
    assert rows["pub:hotdeal:5"]["btns"] == [["제목 복사", False, True], ["본문 복사", False, True]] and rows["pub:hotdeal:5"]["chips"] == ["점검 통과"]
    assert rows["pub:hotdeal:6"]["chips"] == [] and rows["pub:hotdeal:4"]["chips"] == []
    assert got["copyBtns"] == 2 and got["copied"] == 1 and got["toast1"] == "복사했어요"
    assert got["copiedOld"] == 1 and got["reloads"] == 1 and got["toast2"] == "목록을 새로 받았어요 — 한 번 더 눌러 주세요"
    assert got["copiedAgain"] == 2
    assert got["copiedOffline"] == 2 and got["toast3"] == "목록을 새로 받지 못했어요 — 인터넷 연결을 확인해 주세요"
    assert got["meta"]["today"] is True and got["meta"]["kkumi_new"] is True and got["meta"]["gate"]["line"] == "자동 점검: 1편 고치는 중 · 1편 통과"
    js = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert js.count("rich(p)") == 2 and "big('본문 복사', () => body(p))" in js  # 본문 복사는 body() 한 길로만 (rich 정의 + body 안)


async def test_checks_wait_for_blogauto_and_survive_the_night(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """주인 11/14 '블로그 프로그램이 몇 시간 멈춘 동안 누른 발행했어요·서로이웃 체크가 사라짐':
    다리가 끊긴 때 누른 체크는 적어 뒀다가 깨어나면 전하고 · 맡겼는데 10분 안에 안 가져간(expired) 것은 다시 맡기고 · 날이 바뀌어도 그 줄은 체크로."""
    day = tz_now(bot).date().isoformat()
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        pub("rsv:hotdeal:289", paste=READY), item("nb-add:hotdeal:fan01", "s4", blog="hotdeal")]})
    h = session_headers(bot)
    new = spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new"

    def queued() -> list[tuple]:
        return [(x["action"], x["args"]) for x in (spool.read(f) for f in sorted(new.glob("*.json")))] if new.exists() else []

    blogauto_status(settings, age=3 * 3600)  # 블로그 프로그램 신호가 3시간 전 — 다리가 끊긴 것으로 봄
    for rid in ("rsv:hotdeal:289", "nb-add:hotdeal:fan01"):
        status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": rid, "done": True}}, h)
        assert status == 200 and got["status"] == "ok" and got["text"] == "체크했어요 — 블로그 프로그램이 깨어나면 전해요"
    assert queued() == [] and routine.relay_waiting(bot.db) == 2 and routine.view(bot)["relay"] == 2
    assert routine.relay_tick(bot) == {"sent": 0, "done": 0, "dropped": 0, "left": 2} and queued() == []  # 아직 끊김 — 맡기지 않음
    blogauto_status(settings)  # 깨어남 → 다음 tick 에 전함
    assert routine.relay_tick(bot)["sent"] == 2
    assert queued() == [("pasted", {"blog": "hotdeal", "pid": 289, "done": True}), ("nb_requested", {"blog": "hotdeal", "target": "fan01", "done": True})]
    assert routine.relay_tick(bot)["sent"] == 0 and len(queued()) == 2  # 맡긴 것은 결과를 기다림 (또 보내지 않음)
    rows = routine.relay_rows(bot.db)
    a1, a2 = rows[0]["aid"], rows[1]["aid"]
    # 첫째는 10분 안에 안 가져감 → expired → 다시 맡김 · 둘째는 됨
    now = tz_now(bot).timestamp()
    bot.actions_out.store.update(a1, status="expired", result={"code": "expired", "text": "", "done_at": now}, now=now)
    bot.actions_out.store.update(a2, status="ok", result={"code": "done", "text": "", "done_at": now}, now=now)
    for f in new.glob("*.json"):
        f.unlink()
    got = routine.relay_tick(bot, now=now + 601)
    assert got["sent"] == 1 and got["done"] == 1 and queued() == [("pasted", {"blog": "hotdeal", "pid": 289, "done": True})]
    again = routine.relay_rows(bot.db)
    assert again[0]["aid"] != a1 and again[0]["tries"] == 2 and again[1].get("ok")
    # 또 expired — 이번엔 20분 뒤에야 다시 (간격이 늘어남)
    bot.actions_out.store.update(again[0]["aid"], status="expired", result={"code": "expired", "text": "", "done_at": now}, now=now)
    blogauto_status(settings, age=-1300)  # (가짜 시각으로 앞서가므로 신호도 그때 것으로)
    assert routine.relay_tick(bot, now=now + 601 + 700)["sent"] == 0
    assert routine.relay_tick(bot, now=now + 601 + 1201)["sent"] == 1
    # 다음 날 목록에 그 줄이 그대로 와도 체크로 (발행 줄은 pub·rsv 어느 쪽이든)
    tomorrow = (tz_now(bot) + timedelta(days=1)).date().isoformat()
    with_routine(tmp_path, {"day": tomorrow, "slots": SLOTS, "overflow": 0, "items": [
        pub("pub:hotdeal:289", paste=READY), pub("pub:hotdeal:300", paste=READY), item("nb-add:hotdeal:fan01", "s4", blog="hotdeal")]})
    v = routine.view(bot, now=at(bot, tomorrow, "09:10"))
    done = {i["id"]: i["done"] for s in v["slots"] for i in s["items"]}
    assert done == {"pub:hotdeal:289": True, "pub:hotdeal:300": False, "nb-add:hotdeal:fan01": True}
    # 블로그 프로그램이 답함(거절) → 지움 · 전한 것은 2시간 뒤 지움 · 3일 넘게 못 전하면 버림
    last = routine.relay_rows(bot.db)[0]["aid"]
    bot.actions_out.store.update(last, status="rejected", result={"code": "rejected", "text": "", "done_at": now}, now=now)
    assert routine.relay_tick(bot, now=now + 3000)["dropped"] == 1
    assert [x["rid"] for x in routine.relay_rows(bot.db)] == ["nb-add:hotdeal:fan01"]
    routine.relay_tick(bot, now=now + 601 + 7201)
    assert routine.relay_rows(bot.db) == []
    blogauto_status(settings, age=3 * 3600)
    await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "rsv:hotdeal:289", "done": True}}, h)
    await post_json(bot, "/dashboard/api/action", {"action": "routine.done", "args": {"id": "rsv:hotdeal:289", "done": False}}, h)
    assert [(x["rid"], x["args"]["done"]) for x in routine.relay_rows(bot.db)] == [("rsv:hotdeal:289", False)]  # 같은 줄은 마지막에 누른 것만
    assert routine.carried(bot.db, tz_now(bot).timestamp()) == set()  # 푼 체크는 넘기지 않음
    assert routine.relay_tick(bot, now=tz_now(bot).timestamp() + 3 * 86400 + 1)["dropped"] == 1 and routine.relay_rows(bot.db) == []
    src = (Path(routine.__file__).parents[2] / "dealbot" / "scheduler.py").read_text(encoding="utf-8")
    assert "routine.relay_tick(bot)" in src  # 1분마다 (dashboard_loop)
