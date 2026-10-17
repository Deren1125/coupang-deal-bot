"""Blog-Auto → 딜봇 알림 받기(owner_notify/) · 아침·저녁 정리 (주인 10/18)."""

from __future__ import annotations

import random
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import Clock, PushServer, fixture, subscription
from dealbot.bridge import spool
from dealbot.notify import ingest
from dealbot.notify.digest import (
    WINDOWS,
    Digests,
    dealbot_sentences,
    plan_minutes,
    waiting_sentences,
)
from test_notify_router import Env


def put(tmp: Path, name: str, obj: dict) -> Path:
    r = spool.root(tmp, spool.OWNER_NOTIFY)
    return spool.write(r, name, obj)


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    e = Env(tmp_path)
    yield e
    e.db.close()


def test_fixture_notices_are_applied_in_order_and_deleted(env: Env) -> None:
    opened = fixture("notice_open.json")
    group = fixture("notice_open_group.json")
    put(env.tmp, opened["id"], opened)
    put(env.tmp, group["id"], group)
    got = ingest.ingest(env.tmp, env.router)
    assert got["open"] == 2
    rows = {n.key: n for n in env.router.store.open_rows(env.clock())}
    d = rows["draft:issue:31"]
    assert d.src == "blogauto" and d.iid == "post-issue-31" and d.delay_s == 300 and d.urgent and d.title == "Pick #31 발행 대기"
    g = rows["photo:issue:46"]
    assert g.group == "pick_photos" and g.wait_s == 7200 and g.wait_n == 3
    assert spool.names(spool.root(env.tmp, spool.OWNER_NOTIFY), "new") == []
    res = fixture("notice_resolve.json")
    put(env.tmp, res["id"], res)
    assert ingest.ingest(env.tmp, env.router)["resolve"] == 1
    assert "draft:issue:31" not in {n.key for n in env.router.store.open_rows(env.clock())}


def test_duplicate_id_is_a_noop(env: Env) -> None:
    opened = fixture("notice_open.json")
    put(env.tmp, opened["id"], opened)
    ingest.ingest(env.tmp, env.router)
    env.router.resolve("blogauto", ["draft:issue:31"])
    put(env.tmp, opened["id"], opened)  # 같은 파일이 또 옴
    assert ingest.ingest(env.tmp, env.router)["dup"] == 1
    assert env.router.store.open_rows(env.clock()) == []


def test_bad_names_sizes_and_shapes_go_to_expired(env: Env) -> None:
    r = spool.root(env.tmp, spool.OWNER_NOTIFY)
    (r / "new" / "not-a-name.json").write_text("{}")
    (r / "new" / f"n_{'0' * 26}.json").write_text("x" * (spool.MAX_BYTES + 1))
    bad = dict(fixture("notice_open.json"), id=f"n_{'1' * 26}", iid="../../etc")
    put(env.tmp, bad["id"], bad)
    forged = dict(fixture("notice_open.json"), id=f"n_{'2' * 26}", src="dealbot")
    put(env.tmp, forged["id"], forged)
    got = ingest.ingest(env.tmp, env.router)
    assert got["open"] == 0 and got["bad"] == 2
    assert sorted(p.name for p in (r / "expired").iterdir()) == sorted(
        ["not-a-name.json", f"n_{'0' * 26}.json", f"n_{'1' * 26}.json", f"n_{'2' * 26}.json"])
    assert env.router.store.open_rows(env.clock()) == []


def test_prefix_resolve_only_with_a_trailing_colon(env: Env) -> None:
    for key in ("oedit:31:2026-10-18T09:10", "oedit:310:x", "draft:issue:31"):
        env.open(key, "owner_edit", src="blogauto", iid="post-issue-31")
    res = fixture("notice_resolve.json")  # ["draft:issue:31", "oedit:31:"] prefix true
    put(env.tmp, res["id"], res)
    ingest.ingest(env.tmp, env.router)
    assert [n.key for n in env.router.store.open_rows(env.clock())] == ["oedit:310:x"]


def test_texts_are_trimmed_and_scrubbed(env: Env) -> None:
    n = dict(fixture("notice_open.json"), id=f"n_{'3' * 26}", title="가" * 80, body="token=abcdefghijklmnopqrstuvwxyz1234 " + "나" * 200)
    put(env.tmp, n["id"], n)
    ingest.ingest(env.tmp, env.router)
    [row] = env.router.store.open_rows(env.clock())
    assert len(row.title) <= 40 and len(row.body) <= 120 and "abcdefghijklmnopqrstuvwxyz1234" not in row.body


# ---------------------------------------------------------------- 정리
def test_planned_times_are_inside_the_windows_and_skip_00_30() -> None:
    rng = random.Random(1)
    for _ in range(300):
        plan = plan_minutes(rng)
        for slot, (lo, hi) in WINDOWS.items():
            hh, mm = (int(x) for x in plan[slot].split(":"))
            assert lo <= hh * 60 + mm <= hi and mm not in (0, 30)


def _at(day: date, hhmm: str) -> float:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo("Asia/Seoul")).timestamp()


async def test_digest_plan_is_stable_sent_once_and_empty_means_no_push(env: Env) -> None:
    day = date(2026, 10, 18)
    dg = Digests(env.db, env.router, tz="Asia/Seoul", rng=random.Random(3), todo=lambda: [])
    plan = dg.plan(day)
    assert Digests(env.db, env.router, tz="Asia/Seoul", rng=random.Random(99)).plan(day) == plan  # 재시작해도 그대로
    env.clock.t = _at(day, plan["am"]) - 60
    env.device()
    assert await dg.tick() is None
    env.clock.t = _at(day, plan["am"]) + 30
    assert await dg.tick() == "digest-20261018-am"
    assert await dg.tick() is None  # 한 번만
    assert env.server.requests == []  # 빈 정리는 푸시하지 않음
    assert env.router.store.digest("digest-20261018-am") is not None


async def test_late_digest_more_than_2h_is_skipped(env: Env) -> None:
    day = date(2026, 10, 18)
    dg = Digests(env.db, env.router, tz="Asia/Seoul", rng=random.Random(3))
    env.clock.t = _at(day, dg.plan(day)["am"]) + 3 * 3600
    assert await dg.tick() is None
    assert env.db.kv_get("digest_sent:2026-10-18:am") == "skipped"


async def test_evening_digest_pushes_sentences_and_one_kakao_memo(env: Env) -> None:
    day = date(2026, 10, 18)
    todo = [{"kind": "link_request", "title": "무선청소기"}, {"kind": "link_request", "title": "텀블러"},
            {"kind": "draft_ready", "title": "장원영 공항패션"}]
    dg = Digests(env.db, env.router, tz="Asia/Seoul", rng=random.Random(5), todo=lambda: todo)
    plan = dg.plan(day)
    env.clock.t = _at(day, plan["pm"]) - 3600
    env.device()
    env.router.summary("day", "오늘 딜봇은 채널에 올린 딜이 14개예요.", replace=True)
    env.open("collector:ppomppu", "collector_stuck", iid="sys-dealbot", short="딜봇 뽐뿌 수집 실패")
    env.clock.t = _at(day, plan["pm"]) + 10
    did = await dg.tick()
    assert did == "digest-20261018-pm"
    d = env.router.store.digest(did)
    labels = [s["label"] for s in d["sections"]]  # type: ignore[index]
    assert labels[:2] == ["기다리는 일", "딜봇"] and "소식" in labels
    assert "링크를 기다리는 딜 2개(무선청소기, 텀블러)가 있어요." in d["lines"]  # type: ignore[index]
    for line in d["lines"]:  # type: ignore[index]
        assert line.endswith((".", "요")) and not line.replace(" ", "").replace(",", "").isdigit()
    [p] = [__import__("dash_helpers").decrypt(r.content, env.ua) for r in env.server.requests]
    assert p["type"] == "digest" and p["title"] == "🌙 저녁 정리" and p["url"] == "/dashboard/go/digest-20261018-pm"
    assert len(p["body"]) <= 120
    [k] = env.kakao_files()
    assert k["kind"] == "daily" and k["key"] == "digest:20261018" and len(k["text"]) <= 200
    assert k["link"].endswith("/dashboard/go/digest-20261018-pm") and "대시보드" in k["text"] and "할 일 3개" in k["text"]


def test_sentence_builders() -> None:
    s = waiting_sentences([{"kind": "link_request", "title": "a"}, {"kind": "zzz", "title": "b"}])
    assert s == ["링크를 기다리는 딜 1개(a)가 있어요.", "그 밖의 할 일 1개(b)가 있어요."]
    out = dealbot_sentences([{"cat": "published", "text": "가"}, {"cat": "held", "text": "나"}, {"cat": "soldout", "text": "다"}])
    assert all(x.endswith(".") for x in out) and len(out) == 3


def test_unused_imports_are_fine() -> None:
    assert Clock and PushServer and subscription
