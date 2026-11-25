"""'하루치 한 번에' v7 (11/24 밤 주인 아이패드 v6 — 「구자욱 FA A등급, ESPN 메츠 추천 보도… 5년 120억 그다음 몸값은 얼마일까」 가
'포스트잇 4 「120억 그다음」 글이 포스트잇 밖에도 있어요' 로 멈춤).

진짜 SE ONE 은 제목 칸(.se-documentTitle)도 .se-content 안 → 포스트잇 밖 세기에 제목 글이 들어가 제목에 같은 말이 있으면 잘못 멈췄음
(원고 html 에는 제목이 없음). 가짜 화면은 제목 칸이 .se-content 밖이라 못 잡았음 → 여기서는 .se-content 맨 앞에 제목 칸 글을 따라 그리는
제목 칸을 하나 더 둠 (진짜처럼). 진짜로 포스트잇 글이 밖에도 들어간 것(dup)은 그대로 멈춤.
"""
from __future__ import annotations

from test_dash_autopub import needs_node
from test_dash_autopub_v6 import _run

TITLE = "구자욱 FA A등급, ESPN 메츠 추천 보도… 5년 120억 그다음 몸값은 얼마일까"
PIT = "120억 그다음 몸값은 얼마일까"

# .se-content 안 맨 앞에 제목 칸 (원래 제목 칸 글을 따라 그림 · 누르는 칸은 원래 제목 칸 그대로) · 첫 붙여넣기의 빈 줄 정리는 제목 칸을 빼고 셈
TITLE_IN = (
    ("C.children.length === 1", "C.querySelectorAll(':scope > .se-component:not(.se-documentTitle)').length === 1"),
    ("</body></html>",
     "<script>(() => { const C = document.querySelector('.se-content'), TB = document.querySelector('.se-documentTitle'), m = document.createElement('div');"
     " m.className = 'se-component se-documentTitle'; m.innerHTML = '<span class=\"se-title-text\"></span>'; C.prepend(m);"
     " setInterval(() => { m.firstChild.textContent = TB.textContent; }, 50); })();</script></body></html>"),
)
# 에디터가 포스트잇 글을 포스트잇 안과 그 뒤 본문 줄에 두 번 넣음 (진짜 밖 겹침)
DUP = ("inPostit.querySelector('.qbox').append(q); }",
       "inPostit.querySelector('.qbox').append(q); if (!window.__dupd) { window.__dupd = 1; const c = comp('se-text', '<div class=\"se-section se-section-align-left\"><p class=\"se-text-paragraph\"></p></div>');"
       " c.querySelector('p').textContent = t; inPostit.after(c); } }")


def _post() -> dict:
    html = ("<p>구자욱이 FA 시장에 나왔어요.</p><p>본문 둘째 줄이에요 길게.</p>"
            f"<blockquote><p>{PIT}</p></blockquote><p>본문 셋째 줄이에요 길게.</p><p>끝 문단이에요.</p>"
            + _tags_line(["구자욱", "FA"], "셀럽 소식"))
    return {"id": "pub:issue:11", "title": TITLE, "at": "", "label": "지금", "photos": 0, "disc": False, "text": "", "html": html}


@needs_node
def test_title_with_the_postit_phrase_is_not_counted_as_outside() -> None:
    got = _run([_post()], mods=TITLE_IN)
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and len(got["pub"]) == 1, got["done"]
    assert not any("포스트잇 밖" in (d.get("n") or "") for d in got["done"]), got["done"]


@needs_node
def test_postit_text_really_outside_still_stops() -> None:
    got = _run([_post()], mods=TITLE_IN + (DUP,), give_up=40)
    assert got["pub"] == [] and got["done"][0]["r"] == "stop", got["done"]
    assert "포스트잇 1 「120억 그다음 몸값은 얼」 글이 포스트잇 밖에도 있어요" in got["done"][0]["n"], got["done"]


# ── 첫 글 지금 발행 (주인 11/16 owner_decisions '오늘 발행 글 없으면 1번 글 지금 발행' — v5 에서 빠졌던 것을 되살림 · 11/24 밤 드라마 첫 글이 예약으로 감)
import json  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

from dealbot.app import DealBot  # noqa: E402
from dealbot.dashboard import autopub, kkumi, routine  # noqa: E402
from test_dash_autopub import DAY, OK, TZ, Rng, _flow, _tags_line, _paste, bot, _no_live_threads  # noqa: E402,F401 — 고정 장치
from test_dash_routine import SLOTS, at, item, with_routine  # noqa: E402


def _rows3(done: str = "") -> dict:
    its = [item(f"rsv:issue:{n}", "s1", group="예약", blog="issue", title=f"데런픽 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in (21, 22, 23)]
    if done:
        its.append(item(done, "s1", group="발행", blog="issue", title="데런픽 · 아침에 올린 글", paste=_paste("아침 글"), gate=OK))
    return {"day": DAY, "slots": SLOTS, "overflow": 0, "items": its}


def _next(bot: DealBot, now: float, pub: dict[str, Any] | None = None) -> dict:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "next", "b": "issue"}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng(), pub=pub)


def _done(bot: DealBot, now: float, rid: str, r: str) -> dict:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "done", "b": "issue", "id": rid, "r": r}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng())


def test_no_post_today_first_post_publishes_now_rest_reserved_from_now(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows3())
    t = at(bot, DAY, "10:03")
    q = _next(bot, t)
    nx = q["next"]
    assert nx["id"] == "rsv:issue:21" and nx["at"] == "" and nx["label"] == "지금" and not kkumi.stamped(nx["html"]), nx
    assert q["plan"].startswith("지금 발행 · 오늘 예약 ") and q["plan"].count(" · ") == 2, q["plan"]
    rest = json.loads(bot.db.kv_get(autopub.KV_PLAN))["issue"]["slots"]
    assert set(rest) == {"rsv:issue:22", "rsv:issue:23"} and all(datetime.fromisoformat(v) - datetime(2026, 11, 18, 10, 3) > timedelta(minutes=40) for v in rest.values())
    assert _next(bot, t + 30)["next"]["at"] == ""  # 다시 눌러도 같은 글이 지금
    _done(bot, t + 40, "rsv:issue:21", "hold")
    _done(bot, t + 50, "rsv:issue:21", "ok")
    q = _next(bot, t + 60)  # 오늘 올린 글이 생김 → 다음 글은 계획대로 예약
    assert q["next"]["id"] in rest and q["next"]["at"] == rest[q["next"]["id"]] and kkumi.stamped(q["next"]["html"]) == q["next"]["at"]
    assert q["plan"].startswith("오늘 예약 ") and "지금" not in q["plan"]


def test_blog_already_published_today_reserves_everything(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows3())
    t = at(bot, DAY, "10:03")
    q = _next(bot, t, pub={"issue": 1, "finance": 0, "hotdeal": 0})  # Blog-Auto 활동 기록: 데런픽 오늘 1편
    assert q["next"]["at"] and "지금" not in q["plan"]
    with_routine(tmp_path, _rows3(done="pub:issue:24"))  # 할 일 목록에서 오늘 발행 줄이 체크됨 (주인이 손으로 올림)
    routine.set_done(bot.db, DAY, "pub:issue:24", True)
    bot.db.kv_set(autopub.KV_PLAN, "{}")
    q = _next(bot, t + 10, pub=None)
    assert q["next"]["at"] and "지금" not in q["plan"]


def test_outside_human_hours_first_post_is_reserved(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows3())
    q = _next(bot, at(bot, DAY, "06:40"))
    assert q["next"]["at"] >= "2026-11-18T07:30" and "지금" not in q["plan"]
    bot.db.kv_set(autopub.KV_PLAN, "{}")
    q = _next(bot, at(bot, DAY, "23:20"))
    assert q["next"]["at"] >= "2026-11-19T07:30" and "지금" not in q["plan"]


@needs_node
def test_fake_editor_first_post_now_with_current_then_second_reserved(bot: DealBot, tmp_path: Path) -> None:
    # 대시보드가 실제로 내주는 두 글 (첫 글 지금 · 둘째 예약) 을 가짜 글쓰기 화면에 — 첫 글은 발행창 '현재'로 발행, 둘째는 예약 칸을 채워 발행
    with_routine(tmp_path, _rows3())
    t = at(bot, DAY, "10:03")
    rv = routine.view(bot, now=t)
    a = autopub.act(bot.db, {"k": autopub.key(bot.db), "a": "next", "b": "issue"}, tz=TZ, now=t, rv=rv, rng=Rng())
    posts = [a["next"]]
    slots = json.loads(bot.db.kv_get(autopub.KV_PLAN))["issue"]["slots"]
    rid = min(slots, key=slots.get)
    tmr = datetime.now() + timedelta(days=1)  # 가짜 화면 달력은 앞날만 — 계획의 시·분을 내일 날짜로
    at_ = f"{tmr:%Y-%m-%d}T{slots[rid][11:]}"
    posts.append({**posts[0], "id": "rsv:issue:22", "title": "둘째 글 제목", "at": at_, "label": "예약",
                  "html": kkumi.stamp("<p>둘째 글 첫 문단.</p><p>둘째 글 끝 문단.</p>" + _tags_line(["태그"], "셀럽 소식"), at_)})
    posts[0] = {**posts[0], "title": "첫 글 제목이에요", "html": "<p>첫 글 첫 문단.</p><p>첫 글 끝 문단.</p>" + _tags_line(["태그"], "셀럽 소식")}
    assert posts[0]["at"] == "" and not kkumi.stamped(posts[0]["html"])
    got = _flow(posts)
    assert [d["r"] for d in got["done"]] == ["hold", "ok", "hold", "ok"], got["done"]
    p1, p2 = got["pub"]
    assert p1["book"] is False and p2["book"] is True, got["pub"]
    assert any("「첫 글 제목이에요」 지금 발행을 눌렀어요" in x for x in got["bars"]) and any("「둘째 글 제목」 예약 " in x for x in got["bars"])


def test_api_reads_blog_auto_published_today(bot: DealBot, tmp_path: Path) -> None:
    from test_dash_state import write_export

    assert bot.dashboard.api._autopub_pub() is None  # 기록 파일 없음 → 딜봇 기록·할 일 목록만
    write_export(tmp_path / "blogauto.json", blogs={"issue": {"posts_published_today": 2}, "finance": {"posts_published_today": 0}})
    got = bot.dashboard.api._autopub_pub()
    assert got is not None and got["issue"] == 2 and got["finance"] == 0 and got["hotdeal"] == 0, got
