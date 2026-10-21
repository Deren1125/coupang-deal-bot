"""일정 화면 (주인 10/21 — "발행 예약한 글들 대시보드에서 좀 편하게 볼 수 있으면 좋겠어 … 앞으로의 일정을 세 블로그 통합으로 볼 수 있는 페이지"):
state['sched'] = 아카이브 · Pick · 핫딜의 예약을 시간순 한 목록으로 (오늘 · 내일 · 날짜(요일)) — 간격 두고 발행 줄의 차례 시각 포함 ·
멈춘 글은 시간 없이 마지막 칸 · 블로그 칩 편수 · 행동은 글 줄의 more 그대로. 화면은 처음 열 때만 받는 sched.js."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import schedview, shell, webapp
from test_dash_schedule import (
    export,  # noqa: F401 — 기록 파일 만들기 (아카이브 준비 3편 · 예약 #60 · Pick 예약 #47 · 멈춤 #48)
)

KST = ZoneInfo("Asia/Seoul")
TZ = "Asia/Seoul"
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=KST).timestamp()  # 수요일


def at(day: int, hm: str) -> str:
    return f"2026-10-{day:02d}T{hm}:00+09:00"


def acts(*names: str) -> list[dict]:
    labels = {"blog.schedule_move": "시간 바꾸기", "blog.unschedule": "예약 취소", "blog.publish": "지금 발행"}
    return [{"action": n, "label": labels[n], "args": {}} for n in names]


def post(blog: str, pid: int, title: str, *, thumb: str | None = None, more: list[dict] | None = None, **kw: object) -> dict:
    slots = [{"slot": 1, "thumb": None}, {"slot": 2, "thumb": thumb}] if thumb else []
    return dict({"iid": f"post-{blog}-{pid}", "title": title, "status": "x", "more": more if more is not None else acts(
        "blog.schedule_move", "blog.unschedule"), "detail": {"slots": slots}}, **kw)


def v2_of(**rows: list[dict]) -> dict:
    """기록 파일: 글마다 schedule(시각·상태) + schedule 칸 items(moved)."""
    return {"schedule": {"blogs": {b: {"items": rows.get(b, [])} for b in ("finance", "issue", "hotdeal")}},
            "posts": {b: [{"pid": e["pid"], "schedule": {k: e[k] for k in ("scheduled_at", "status", "reason")}} for e in rows.get(b, [])]
                      for b in ("finance", "issue", "hotdeal")}}


def entry(pid: int, when: str | None, status: str = "scheduled", moved: bool = False) -> dict:
    return {"pid": pid, "scheduled_at": when, "status": status, "reason": "", "moved": moved}


def three_blogs() -> tuple[dict, dict]:
    blogs = {
        "finance": {"schedule": {"items": [post("finance", 60, "청년 지원금 정리", more=acts("blog.schedule_move", "blog.unschedule", "blog.publish")),
                                           post("finance", 61, "연말정산 체크")]}, "waiting": []},
        "issue": {"schedule": {"items": [post("issue", 47, "차은우 가을 니트 코디", thumb="/dashboard/thumb/a.jpg"),
                                         post("issue", 49, "수지 공항패션"), post("issue", 50, "카리나 화보")]},
                  "waiting": [post("issue", 48, "아이유 콘서트룩", kind="sched_blocked", status="예약 발행 멈춤 · 사진이 아직 없어요"),
                              post("issue", 51, "기다리는 글", kind="waiting")]},
        "hotdeal": {"schedule": {"items": [post("hotdeal", 70, "오늘의 핫딜 5선")]}, "waiting": []},
    }
    v2 = v2_of(
        finance=[entry(60, at(21, "11:00")), entry(61, at(24, "09:00"))],
        issue=[entry(47, at(21, "12:10")), entry(49, at(21, "14:40"), moved=True), entry(50, at(30, "08:47")), entry(48, None, "blocked")],
        hotdeal=[entry(70, at(22, "08:47"))])
    return blogs, v2


def build(blogs: dict, v2: dict, now: float = NOW, ok: bool = True) -> dict | None:
    return schedview.build(blogs, v2, TZ, now, ok)


def test_three_blogs_merge_into_one_chronological_list_grouped_by_day() -> None:
    s = build(*three_blogs())
    assert s is not None
    assert [d["label"] for d in s["days"]] == ["오늘", "내일", "10/24 (토)", "10/30 (금)", "멈춘 글 · 시간이 없어요"]
    assert [d["day"] for d in s["days"]] == ["2026-10-21", "2026-10-22", "2026-10-24", "2026-10-30", None]
    flat = [(r["time"] if r["state"] != "blocked" else None, r["chip"], r["title"]) for d in s["days"] for r in d["items"]]
    assert flat == [("11:00", "아카이브", "청년 지원금 정리"), ("12:10", "Pick", "차은우 가을 니트 코디"), ("14:40", "Pick", "수지 공항패션"),
                    ("08:47", "핫딜", "오늘의 핫딜 5선"), ("09:00", "아카이브", "연말정산 체크"), ("08:47", "Pick", "카리나 화보"),
                    (None, "Pick", "아이유 콘서트룩")]
    ats = [r["at"] for d in s["days"][:-1] for r in d["items"]]
    assert ats == sorted(ats)  # 블로그가 섞여도 시간순
    assert s["total"] == 7 and [b["n"] for b in s["blogs"]] == [2, 4, 1]  # 칩 편수 (멈춘 글은 그 블로그에 셈)
    assert all(r["blog"] in ("finance", "issue", "hotdeal") for d in s["days"] for r in d["items"])


def test_summary_counts_the_next_seven_days_and_names_stopped_posts() -> None:
    s = build(*three_blogs())
    assert s["counts"] == {"finance": 2, "issue": 2, "hotdeal": 1}  # 10/30 Pick 는 7일 밖
    assert s["text"] == "앞으로 7일 · 아카이브 2 · Pick 2 · 핫딜 1 · 멈춤 1"
    blogs, v2 = three_blogs()
    for b in blogs.values():
        b["schedule"]["items"], b["waiting"] = [], []
    empty = build(blogs, v2_of())
    assert empty["text"] == "앞으로 예약된 글이 없어요" and empty["days"] == [] and empty["total"] == 0


def test_queued_posts_show_their_planned_times_and_the_status_words() -> None:
    s = build(*three_blogs())
    rows = {r["iid"]: r for d in s["days"] for r in d["items"]}
    assert rows["post-issue-47"]["status"] == "간격 두고 발행 대기" and rows["post-issue-47"]["moved"] is False
    assert rows["post-issue-49"]["status"] == "예약됨 · 직접 정한 시간" and rows["post-issue-49"]["moved"] is True
    assert rows["post-issue-48"]["status"] == "예약 발행 멈춤 · 사진이 아직 없어요" and rows["post-issue-48"]["state"] == "blocked"
    assert rows["post-issue-48"]["at"] is None and "time" not in rows["post-issue-48"]
    assert "post-issue-51" not in rows  # 그냥 기다리는 글은 일정이 아님
    blogs, v2 = three_blogs()
    v2["posts"]["issue"][0]["schedule"]["status"] = "publishing"
    assert {r["iid"]: r for d in build(blogs, v2)["days"] for r in d["items"]}["post-issue-47"]["status"] == "지금 발행하는 중"


def test_rows_reuse_the_existing_actions_and_the_first_thumbnail() -> None:
    rows = {r["iid"]: r for d in build(*three_blogs())["days"] for r in d["items"]}
    fin, pick = rows["post-finance-60"], rows["post-issue-47"]
    assert [fin[k]["action"] for k in ("move", "now", "remove")] == ["blog.schedule_move", "blog.publish", "blog.unschedule"]
    assert pick["move"]["action"] == "blog.schedule_move" and pick["now"] is None and pick["remove"]["action"] == "blog.unschedule"  # Pick 은 바로 발행 없음
    assert pick["thumb"] == "/dashboard/thumb/a.jpg" and fin["thumb"] is None


def test_overdue_item_stays_on_today_and_old_blogauto_has_no_page() -> None:
    blogs, v2 = three_blogs()
    v2["posts"]["finance"][0]["schedule"]["scheduled_at"] = at(20, "23:30")  # 어제 차례 (봇이 아직 못 올림)
    s = build(blogs, v2)
    assert s["days"][0]["label"] == "오늘" and s["days"][0]["items"][0]["title"] == "청년 지원금 정리" and s["days"][0]["items"][0]["time"] == "23:30"
    assert build(blogs, v2, ok=False) is None


def test_filter_chips_follow_the_rows() -> None:
    s = build(*three_blogs())
    for key in ("finance", "issue", "hotdeal"):
        n = sum(1 for d in s["days"] for r in d["items"] if r["blog"] == key)
        assert next(b for b in s["blogs"] if b["key"] == key)["n"] == n
    assert [(b["key"], b["label"]) for b in s["blogs"]] == [("finance", "아카이브"), ("issue", "Pick"), ("hotdeal", "핫딜")]


# ---------------------------------------------------------------- 전체 (기록 파일 → state)


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def state(bot: DealBot) -> dict:
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    return body


async def test_state_has_the_schedule_page_with_the_existing_actions(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    data = export(tmp_path / "blogauto.json", planned_in_h=1.0)
    data["schedule"]["blogs"]["issue"]["items"][0]["moved"] = True
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    blogauto_status(settings)
    s = (await state(bot))["sched"]
    rows = {r["iid"]: r for d in s["days"] for r in d["items"]}
    assert set(rows) == {"post-finance-60", "post-issue-47", "post-issue-48"} and s["days"][-1]["day"] is None
    pick = rows["post-issue-47"]
    assert pick["state"] == "scheduled" and pick["moved"] is True and pick["status"] == "예약됨 · 직접 정한 시간"
    want = datetime.now(KST).replace(second=0, microsecond=0)
    assert abs(pick["at"] - (time.time() + 3600)) < 120 and pick["time"] == datetime.fromtimestamp(pick["at"], KST).strftime("%H:%M") and want
    assert pick["move"]["input"] == "when" and pick["move"]["args"] == {"blog": "issue", "pid": 47}
    assert pick["remove"]["action"] == "blog.unschedule" and pick["now"] is None
    assert rows["post-finance-60"]["now"]["action"] == "blog.publish" and rows["post-finance-60"]["now"]["confirm"] is True
    assert rows["post-issue-48"]["state"] == "blocked" and "사진이 아직 없어요" in rows["post-issue-48"]["status"]
    assert s["total"] == 3 and s["text"].startswith("앞으로 7일 · 아카이브 1 · Pick 1 · 핫딜 0")


async def test_old_blogauto_without_schedule_has_no_page(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json", schedule=False)
    blogauto_status(settings)
    assert (await state(bot))["sched"] is None


async def test_move_action_is_dropped_when_blogauto_does_not_know_it(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    export(tmp_path / "blogauto.json", planned_in_h=1.0)
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "schedule_move"])
    rows = {r["iid"]: r for d in (await state(bot))["sched"]["days"] for r in d["items"]}
    assert rows["post-issue-47"]["move"] is None and rows["post-issue-47"]["remove"] is not None


# ---------------------------------------------------------------- 껍데기 · 파일


async def test_sched_js_is_served_and_loaded_lazily(bot: DealBot) -> None:
    res = await bot.web.dispatch("GET", "/dashboard/static/sched.js", {})
    assert res[0] == 200 and res[2].startswith("text/javascript") and res[3]["X-Content-Type-Options"] == "nosniff"
    html = shell.page({"v": 1})
    assert '<meta name="sb-sched" content="/dashboard/static/sched.js?v=' in html
    assert html.count("<script src=") == 1  # sched.js 는 처음 열 때만


def test_app_js_entry_and_sched_js_flow() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    sj = (webapp.STATIC_DIR / "sched.js").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "const V = 'sb-v29'" in sw  # 앱 파일은 /dashboard/static/ 아래면 모두 처음 받을 때 저장 — 목록에 넣을 파일 없음
    assert "/^(pics|search|trial|ptrial|sched|threads)$/" in js and "key === 'sched'" in js and "meta[name=sb-sched]" in js
    assert "href: '#/blog/sched'" in js and "S.sched && S.sched.total" in js and "예약 일정 보기" in js
    for word in ("일정", "블로그로", "전체", "시간 바꾸기", "바로 발행", "빼기", "앞으로 예약된 글이 없어요", "W.SBSched = {view}", "h.act(r, a", "h.open(r.iid)",
                 "멈춤", "하는 중이에요", "sc-css"):
        assert word in sj, word
    assert "innerHTML" not in sj and "https://" not in sj and "eval(" not in sj
