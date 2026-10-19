"""간격 두고 발행 (주인 10/18 — "시간 간격 두고 올리는건 어떻개 해야해?" · "데런픽은 일단 무조건 간격 두고 발행하자").

대시보드: 준비된 글의 큰 버튼은 '간격 두고 발행', '지금 발행' 은 더보기(Pick 은 없음) · '대기 N편 간격 두고 발행' 확인 창에 시각 목록과
'첫 글은 지금' · 예약된 글은 할 일이 아님 · 차례에 멈춘 글은 할 일 · Pick '발행' 은 서버에서도 예약으로 · 예전 Blog-Auto 면 예전 그대로."""

from __future__ import annotations

import json
import time
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
from dealbot.dashboard import rank
from dealbot.dashboard import schedule as sch

KST = ZoneInfo("Asia/Seoul")


class Draws:
    def __init__(self, xs: list[int]) -> None:
        self.xs = list(xs)

    def randint(self, a: int, b: int) -> int:
        v = self.xs.pop(0)
        assert a <= v <= b
        return v


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, KST).isoformat(timespec="seconds")


def export(path: Path, *, schedule: bool = True, ready: tuple[int, ...] = (51, 52, 53), planned_in_h: float = 3.0) -> dict:
    """기록 파일: 아카이브 준비된 글 셋(needs publish) + Pick 예약된 글 #47 · 멈춘 글 #48 (fixture) + 아카이브 예약된 글 #60."""
    data = fixture("export_v2.json")
    now = time.time()
    data["written_at"] = datetime.now(KST).isoformat(timespec="seconds")
    data["todo"]["generated_at"] = data["written_at"]
    fin = [{"pid": p, "title": f"아카이브 글 {p}", "status": "saved", "stage": "", "saved_at": data["written_at"], "score": 50,
            "popular": False, "owner_edited": False, "needs": ["publish"], "photo_request": None, "slots": [], "questions": [],
            "deadline": None, "naver_url": None, "schedule": None} for p in ready]
    fin.append(dict(fin[0], pid=60, title="예약된 아카이브 글", needs=[],
                    schedule={"scheduled_at": iso(now + planned_in_h * 3600), "status": "scheduled", "reason": ""}))
    data["posts"]["finance"] = fin
    for p in data["posts"]["issue"]:
        if p["pid"] == 47:
            p["schedule"]["scheduled_at"] = iso(now + planned_in_h * 3600)
    data["recent"]["finance"] = [{"pid": 9, "title": "청년 지원금 정리", "url": "https://m.blog.naver.com/derenlog/224001", "at": "2026-10-18T08:00:00"}]
    if not schedule:
        data.pop("schedule")
        for b in data["posts"].values():
            for p in b:
                p.pop("schedule", None)
    else:
        data["schedule"]["blogs"]["finance"]["items"] = [{"pid": 60, "scheduled_at": iso(now + planned_in_h * 3600),
                                                          "status": "scheduled", "reason": ""}]
        data["schedule"]["blogs"]["finance"]["count"] = 1
        data["schedule"]["blogs"]["finance"]["tail_at"] = iso(now + planned_in_h * 3600)
        data["schedule"]["blogs"]["issue"]["tail_at"] = iso(now + planned_in_h * 3600)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def act(bot: DealBot, action: str, args: dict, h: dict, **extra: object) -> tuple[int, dict]:
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": action, "args": args, **extra}, h)
    return status, body


def new_files(bot: DealBot) -> list[dict]:
    r = bot.actions_out.root()
    return [spool.read(r / "new" / f"{n}.json") for n in spool.names(r, "new")]  # type: ignore[misc]


async def state(bot: DealBot) -> dict:
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    return body


# ---------------------------------------------------------------- 시각 계산 (두 저장소가 같게)
def test_plan_matches_the_shared_contract_fixture() -> None:
    data = fixture("sched_plan.json")
    r = sch.rules_of({"gap_minutes": data["rules"]["gap_minutes"], "window": data["rules"]["window"], "morning": data["rules"]["morning"]})
    assert r == sch.Rules()
    for c in data["cases"]:
        d = Draws(c["draws"])
        got = sch.plan(c["n"], now=sch.iso_ts(c["now"]), r=r, rng=d, tail=sch.iso_ts(c["tail"]), last_pub=sch.iso_ts(c["last_pub"]),  # type: ignore[arg-type]
                       first_now=c["first_now"])
        assert [iso(t) for t in got] == c["want"], c["name"]
        assert not d.xs, c["name"]


def test_seeded_plan_is_stable_and_keeps_the_rules() -> None:
    now = sch.iso_ts("2026-10-18T19:30:00+09:00")
    a = sch.plan(4, now=now, r=sch.Rules(), rng=sch.seeded("s", "finance", [1, 2, 3, 4], False, None, None, 1))  # type: ignore[arg-type]
    b = sch.plan(4, now=now, r=sch.Rules(), rng=sch.seeded("s", "finance", [1, 2, 3, 4], False, None, None, 1))  # type: ignore[arg-type]
    assert a == b
    prev = now
    for t in a:
        assert sch.in_window(t, sch.Rules()) and t - prev >= 7200  # type: ignore[operator]
        prev = t
    assert sch.when_text(now + 3600, now) == "오늘 20:30" and sch.when_text(now + 14 * 3600, now).startswith("내일 ")  # type: ignore[operator]


# ---------------------------------------------------------------- 누르는 일
async def test_single_schedule_needs_no_confirm_and_sends_no_times(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    status, body = await act(bot, "blog.schedule", {"blog": "finance", "pids": [51], "at": [1e9 + 5]}, h)
    assert status == 202, body
    [f] = new_files(bot)
    assert f["action"] == "schedule" and f["args"] == {"blog": "finance", "pids": [51]} and f["target"] == "post-finance-51"


async def test_batch_confirm_lists_titles_and_times_and_the_confirmed_times_go_to_blogauto(bot: DealBot, settings: Settings,
                                                                                         tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    args = {"blog": "finance", "pids": [51, 52, 53]}
    status, d = await act(bot, "blog.schedule", args, h)
    assert status == 409 and d["status"] == "confirm" and d["ok"] == "예약하기" and "3편" in d["title"]
    assert [x["title"] for x in d["plan"]] == ["아카이브 글 51", "아카이브 글 52", "아카이브 글 53"]
    assert d["toggle"] is None  # 예약 줄에 글이 있으면 '첫 글은 지금' 은 뜻이 없어 보이지 않는다 (그 뒤로 2~3시간)
    at = d["args"]["at"]
    tail = sch.iso_ts(fixture("export_v2.json")["schedule"]["blogs"]["finance"]["tail_at"]) or 0
    assert at == sorted(at) and at[0] >= max(time.time(), tail) + 7200 - 5
    assert all(b - a >= 7200 for a, b in zip(at, at[1:], strict=False)) and all(sch.in_window(t, sch.Rules()) for t in at)
    assert all(x["when"].startswith(("오늘 ", "내일 ")) or "/" in x["when"] for x in d["plan"])
    # 다시 열면 같은 시각 ('첫 글은 지금' 을 켰다 꺼도)
    _, d2 = await act(bot, "blog.schedule", args, h)
    assert d2["args"]["at"] == at
    # 첫 글은 지금 — 아카이브 예약 줄에 이미 글이 있으면 줄 끝 뒤로 (지금이 아님)
    _, d3 = await act(bot, "blog.schedule", dict(args, first_now=True), h)
    assert d3["toggle"] is None and d3["args"]["first_now"] is True and d3["args"]["at"][0] >= d["args"]["at"][0] - 10800
    # 보여 준 시각을 바꿔 보내면 확인표가 맞지 않는다
    bad = dict(d["args"], at=[t + 60 for t in at])
    status, e = await act(bot, "blog.schedule", bad, h, ctoken=d["ctoken"])
    assert status == 409 and e["code"] == "confirm_expired"
    status, ok = await act(bot, "blog.schedule", d2["args"], h, ctoken=d2["ctoken"])
    assert status == 202, ok
    [f] = new_files(bot)
    assert f["action"] == "schedule" and f["args"]["at"] == at and f["confirmed"] is True and f["args"]["pids"] == [51, 52, 53]


async def test_first_now_shows_now_when_the_queue_is_empty(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    data = export(tmp_path / "blogauto.json")
    data["schedule"]["blogs"]["finance"].update(tail_at=None, last_pub=iso(time.time() - 3600), items=[], count=0)
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    blogauto_status(settings)
    h = session_headers(bot)
    # 마지막 발행이 1시간 전 — 지금 올릴 수 없으니 '첫 글은 지금' 이 보이지 않는다
    _, d = await act(bot, "blog.schedule", {"blog": "finance", "pids": [51, 52]}, h)
    assert d["toggle"] is None
    data["schedule"]["blogs"]["finance"]["last_pub"] = None
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    blogauto_status(settings)
    h = session_headers(bot)
    _, off = await act(bot, "blog.schedule", {"blog": "finance", "pids": [51, 52]}, h)
    _, d = await act(bot, "blog.schedule", {"blog": "finance", "pids": [51, 52], "first_now": True}, h)
    now = time.time()
    first = d["args"]["at"][0]
    if sch.in_window(now, sch.Rules()):
        assert off["toggle"] == {"key": "first_now", "label": "첫 글은 지금", "on": False} and d["toggle"]["on"] is True
        assert d["plan"][0]["when"] == "지금" and abs(first - now) < 5
    else:  # 22시 넘으면 '지금' 이 없다 (다음 날 아침)
        assert off["toggle"] is None and d["plan"][0]["when"].startswith(("오늘 ", "내일 "))


async def test_pick_publish_becomes_a_schedule_on_the_server_and_has_no_first_now(bot: DealBot, settings: Settings,
                                                                                 tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    # 오래된 화면·다른 기기에서 Pick '발행' 을 눌러도 바로 공개되지 않고 예약 줄로 (확인 창도 없이)
    status, body = await act(bot, "blog.publish", {"blog": "issue", "pid": 46}, h)
    assert status == 202, body
    [f] = new_files(bot)
    assert f["action"] == "schedule" and f["args"] == {"blog": "issue", "pids": [46]}
    # Pick 여러 편: '첫 글은 지금' 은 없다 (보내도 버림)
    status, d = await act(bot, "blog.schedule", {"blog": "issue", "pids": [46, 31], "first_now": True}, h)
    assert status == 409 and d["toggle"] is None and "first_now" not in d["args"]
    # 아카이브 '지금 발행' 은 예전처럼 확인 창 → 발행
    status, d = await act(bot, "blog.publish", {"blog": "finance", "pid": 51}, h)
    assert status == 409 and "발행" in d["title"]


async def test_unschedule_and_cancel_all(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    status, _ = await act(bot, "blog.unschedule", {"blog": "finance", "pid": 60}, h)
    assert status == 202
    status, d = await act(bot, "blog.unschedule_all", {"blog": "finance"}, h)
    assert status == 409 and d["ok"] == "모두 취소" and d["danger"] is False
    status, _ = await act(bot, "blog.unschedule_all", {"blog": "finance"}, h, ctoken=d["ctoken"])
    assert status == 202
    assert [f["action"] for f in new_files(bot)] == ["unschedule", "unschedule_all"]
    assert new_files(bot)[1]["target"] == "blog-finance"


async def test_schedule_move_is_confirm_free_and_validated(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """예약 시간 바꾸기 (주인 10/19 — "데런 픽 글 예약 시간 조정할 수 있는 기능도 넣어줘") — 계약 action_schedule_move.json."""
    export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    fx = fixture("action_schedule_move.json")
    status, _ = await act(bot, "blog.schedule_move", dict(fx["args"], pid=47), h)
    assert status == 202
    [f] = new_files(bot)
    assert f["action"] == fx["action"] and f["args"] == dict(fx["args"], pid=47) and set(f) == set(fx)
    for bad in ({"at": "25:00"}, {"at": "9:5"}, {"day": "모레"}, {"at": 1760000000}, {"day": None}):
        status, d = await act(bot, "blog.schedule_move", dict(fx["args"], pid=47, **bad), h)
        assert status == 400 and d["code"] == "bad_args", bad
    # 예전 Blog-Auto (schedule_move 를 모름) — 버튼 없음
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "schedule_move"])
    body = await state(bot)
    assert [a["label"] for a in body["blogs"]["issue"]["schedule"]["items"][0]["more"]] == ["예약 취소"]


async def test_old_blogauto_without_schedule_keeps_the_old_buttons(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json", schedule=False)
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if "schedule" not in a])
    body = await state(bot)
    by = {it["iid"]: it for it in body["todo"]}
    assert by["post-finance-51"]["primary"]["action"] == "blog.publish" and by["post-finance-51"]["primary"]["label"] == "발행"
    assert all(b["schedule"] is None for b in body["blogs"].values())
    h = session_headers(bot)
    status, d = await act(bot, "blog.publish", {"blog": "issue", "pid": 46}, h)
    assert status == 409  # 기록 파일이 간격 두고 발행을 모르면 예전처럼 (Blog-Auto 가 새 판이면 거기서 예약으로)
    status, d = await act(bot, "blog.schedule", {"blog": "finance", "pids": [51]}, h)
    assert status == 503 and d["code"] == "unsupported"
    # 새 기록 파일이지만 Blog-Auto 가 아직 schedule 일을 모른다고 알리면 예전 버튼
    export(tmp_path / "blogauto.json")
    body = await state(bot)
    assert {it["iid"]: it for it in body["todo"]}["post-finance-51"]["primary"]["action"] == "blog.publish"


# ---------------------------------------------------------------- 화면 값
async def test_state_hides_scheduled_posts_from_todo_and_lists_them_in_the_blog_tab(bot: DealBot, settings: Settings,
                                                                                    tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json", planned_in_h=1.0)
    blogauto_status(settings)
    # 예약 전에 열린 '발행 대기' 알림이 남아 있어도 할 일에 넣지 않는다
    bot.router.open({"src": "blogauto", "key": "draft:finance:60", "kind": "draft_ready", "iid": "post-finance-60",  # type: ignore[union-attr]
                     "title": "아카이브 #60 발행 대기", "at": time.time()})
    bot.router.open({"src": "blogauto", "key": "sched:issue:48", "kind": "sched_blocked", "iid": "post-issue-48",  # type: ignore[union-attr]
                     "title": "Pick #48 예약 발행 멈춤", "at": time.time()})
    body = await state(bot)
    iids = [it["iid"] for it in body["todo"]]
    assert "post-finance-60" not in iids and "post-issue-47" not in iids
    by = {it["iid"]: it for it in body["todo"]}
    ready = by["post-finance-51"]
    assert ready["primary"]["action"] == "blog.schedule" and ready["primary"]["label"] == "간격 두고 발행"
    assert ready["more"][0]["action"] == "blog.publish" and ready["more"][0]["label"] == "지금 발행" and ready["more"][0]["confirm"] is True
    blocked = by["post-issue-48"]
    assert blocked["kind"] == "sched_blocked" and blocked["urgent"] is True and "사진이 아직 없어요" in blocked["status"]
    fin = body["blogs"]["finance"]
    assert all(it["iid"] != "post-finance-60" for it in fin["waiting"])  # 예약된 글은 '예약된 글' 칸에만
    sc = fin["schedule"]
    assert sc["count"] == 1 and sc["spaced_only"] is False and sc["cancel_all"]["action"] == "blog.unschedule_all"
    item = sc["items"][0]
    want = (datetime.now(KST) + timedelta(hours=1))
    day = "오늘" if want.date() == datetime.now(KST).date() else "내일"
    assert item["status"] == f"{day} {want:%H:%M} 발행 예정" and item["kind"] == "scheduled" and item["primary"] is None
    assert [a["label"] for a in item["more"]] == ["시간 바꾸기", "예약 취소", "지금 발행"]
    mv = item["more"][0]  # 10/19: 예약 시간 바꾸기 — 시각 칸에 지금 예약 시각, 오늘/내일
    assert mv["action"] == "blog.schedule_move" and mv["input"] == "when" and mv["confirm"] is False
    assert mv["value"] == f"{want:%H:%M}" and mv["day"] == ("today" if day == "오늘" else "tomorrow")
    pick = body["blogs"]["issue"]["schedule"]
    assert pick["spaced_only"] is True and [a["label"] for a in pick["items"][0]["more"]] == ["시간 바꾸기", "예약 취소"]  # Pick 은 '지금 발행' 없음
    # 블로그 탭에서도 Pick 준비된 글에 '지금 발행' 이 없다
    for it in body["blogs"]["issue"]["waiting"] + [x for x in body["todo"] if x["blog"] == "issue"]:
        assert all(a["action"] != "blog.publish" for a in it["more"]), it["iid"]
    # 최근 글 검색 등록: 구글은 주소 복사 + 키자드, 다음은 블로그 검색
    r = fin["recent"][0]
    assert r["google"] == {"copy": "https://blog.naver.com/derenlog/224001", "url": "https://keyzard.cc/nb"}
    assert r["daum"].startswith("https://search.daum.net/search?w=fusion&col=blog&q=") and "%EC%B2%AD%EB%85%84" in r["daum"]


def test_blocked_schedule_ranks_with_other_blocked_owner_work() -> None:
    def it(kind: str) -> dict:
        return {"iid": f"post-finance-{len(kind)}", "src": "blog", "sev": "todo", "kind": kind}

    now = time.time()
    assert rank.rank(it("sched_blocked"), now) == rank.rank(it("retry"), now)
    assert rank.rank(it("sched_blocked"), now) > rank.rank(it("publish"), now)
    assert rank.rank(it("sched_blocked"), now) > rank.rank(it("photos"), now)


def test_blocked_notice_is_urgent_and_lands_on_the_post() -> None:
    from dealbot.notify import iid, router

    assert router.urgency("sched_blocked") == "URGENT"
    assert iid.iid_for("sched:issue:48", src="blogauto") == "post-issue-48"
