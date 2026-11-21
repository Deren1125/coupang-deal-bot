"""취준 탭들 — 오늘 4줄 체크 · D-day · 마감 5개 상한 · 영어 프롬프트 조립 · 재수강 계산 · 학점 막대 · 대화 맥락 6천 토큰 상한 · 주기 일."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import academic, chat, english, profile, today
from jobprep_helpers import make_jp


@pytest.fixture
def jp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    return make_jp(tmp_path, monkeypatch)


def test_today_checklist_and_ddays(jp) -> None:  # type: ignore[no-untyped-def]
    now = jp.clock()
    v = today.view(jp)
    assert [t["done"] for t in v["todo"]] == [False, False, False, False]
    assert v["discharge"]["d"] == 237 or v["discharge"]["d"] > 200
    assert v["window"]["label"] == "해외 2027 여름 지원" and v["window"]["open"]
    for q in ("1.1", "1.2"):
        jp.store.x("INSERT INTO attempts (qid, item, code, passed, total, fail_case, at) VALUES (?, 'book', '', 0, 1, '', ?)", (q, now))
    for _ in range(5):
        jp.store.x("INSERT INTO pron_takes (sent_id, at, state) VALUES (1, ?, 'done')", (now,))
    jp.store.x("INSERT INTO eng_sessions (day, text_len, result, at) VALUES (?, 100, '{}', ?)", (v["day"], now))
    jp.store.x("INSERT INTO speech_takes (card_id, file, at, result) VALUES (1, 'x', ?, '{}')", (now,))
    assert all(t["done"] for t in today.view(jp)["todo"])
    jp.fake_clock.t += 86400
    assert not any(t["done"] for t in today.view(jp)["todo"])


def test_deadlines_capped_and_interest_first(jp) -> None:  # type: ignore[no-untyped-def]
    cid = jp.store.x("INSERT INTO companies (name, norm, cat, on_) VALUES ('C','c','ib',1)")
    for i in range(8):
        jp.store.x("INSERT INTO jobs (ukey, company_id, title, deadline, state, auto_out, user_status) VALUES (?,?,?,?, 'open', 0, ?)",
                   (f"k{i}", cid, f"T{i}", f"2026-11-2{i % 4}", "interest" if i == 7 else ""))
    jp.store.x("INSERT INTO jobs (ukey, company_id, title, deadline, state, auto_out) VALUES ('far', ?, 'Far', '2027-03-01', 'open', 0)", (cid,))
    d = today.view(jp)["deadlines"]
    assert len(d) == 5 and d[0]["title"] == "T7" and all(x["title"] != "Far" for x in d)


def test_english_prompt_is_assembled_by_code(jp) -> None:  # type: ignore[no-untyped-def]
    import json

    day = english.day_of(jp.clock())
    yday = english.day_of(jp.clock(), 1)
    jp.store.x("INSERT INTO eng_week (day, body, week) VALUES (?,?,?)", (day, json.dumps({"name": "Mia", "personality": "chatty", "accent": "AU",
                                                                                          "situation": "at a cafe", "kind": "일상", "opener": "Hey!"}), day))
    sid = jp.store.x("INSERT INTO eng_sessions (day, text_len, result, at) VALUES (?, 1, '{}', 0)", (yday,))
    jp.store.x("INSERT INTO eng_fix (session_id, mine, better, why, kind, at) VALUES (?, 'I go yesterday', 'I went yesterday', 'past', 'grammar', 0)", (sid,))
    english.set_expr(jp, {"text": "It slipped my mind"})
    p = english.today_view(jp)["prompt"]
    assert '"Sure, take your time."' in p and "Australian" in p and "I go yesterday -> I went yesterday" in p and "It slipped my mind" in p
    assert "feedback" in p and "recast" in p and jp.fake_http.calls == []  # AI 안 씀


def test_retake_gpa_and_bars(jp) -> None:  # type: ignore[no-untyped-def]
    cs = [{"id": 1, "name": "회로", "credits": 3, "grade": "C+"}, {"id": 2, "name": "선대", "credits": 3, "grade": "A+"}]
    r = academic.retake_gpa(cs, {"1": "A+"}, "A0")
    assert r["now"] == round((2.3 * 3 + 4.3 * 3) / 6, 2) and r["new"] == round((4.0 * 3 + 4.3 * 3) / 6, 2)  # 재수강 상한 A0
    assert r["new45"] == round(r["new"] * 4.5 / 4.3, 2)
    academic.save_courses(jp, {"courses": [{"name": "글쓰기", "credits": 3, "grade": "A0", "area": "교양", "term": "2023-1"},
                                           {"name": "회로", "credits": 3, "grade": "B+", "area": "전필", "term": "2023-2"},
                                           {"name": "실패", "credits": 3, "grade": "F", "area": "전선", "term": "2023-2"}]})
    academic.save_req(jp, {"교양": 30, "전필": 20, "총": 130})
    bars = {b["area"]: b for b in academic.view(jp)["bars"]}
    assert bars["교양"] == {"area": "교양", "done": 3.0, "need": 30.0, "left": 27.0} and bars["전선"]["done"] == 0.0 and bars["총"]["done"] == 6.0
    errs = academic.check_plan({"2027-2": [{"credits": 21}], "2027-W": []}, 18)
    assert any("최대" in e for e in errs) and any("겨울" in e for e in errs)


def test_profile_seed_and_version(jp) -> None:  # type: ignore[no-untyped-def]
    p, ver = profile.get(jp.store)
    assert ver == 1 and "3.83" in p["gpa"] and "2027-07-13" in p["military"]
    assert "연세대" in profile.summary(p) and len(profile.summary(p)) <= 400
    out = profile.save(jp.store, {**p, "english": "TOEIC 900"})
    assert out == {"ver": 2, "changed": True} and profile.save(jp.store, {**p, "english": "TOEIC 900"})["changed"] is False


def test_chat_context_capped(jp) -> None:  # type: ignore[no-untyped-def]
    cid = jp.store.x("INSERT INTO companies (name, norm, cat, on_) VALUES ('Big','big','ib',1)")
    for i in range(30):
        jp.store.x("INSERT INTO jobs (ukey, company_id, title, loc, state, auto_out) VALUES (?,?,?,?, 'open', 0)", (f"k{i}", cid, "가" * 3000, "Seoul"))
    ctx = chat.context(jp, "jobs", "")
    assert chat.tokens(ctx) <= chat.CTX_TOKENS and ctx.startswith("[내 스펙]")


async def test_chat_send_gemini_stores_history(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "k")
    jp.fake_http.gemini = ["코치 답"]
    out = await chat.send(jp, {"tab": "jobs", "ctx": "", "text": "어디 지원할까?"})
    assert out["reply"] == "코치 답" and out["model"] == "gemini"
    h = chat.history(jp, "jobs", "")
    assert [m["role"] for m in h] == ["user", "assistant"]


def test_tick_runs_due_slot_once_and_waits_on_low_memory(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dealbot.dashboard.jobprep import store

    kst = ZoneInfo("Asia/Seoul")
    jp.fake_clock.t = datetime(2026, 11, 23, 6, 1, tzinfo=kst).timestamp()  # 월요일 06:01
    assert jp.due(jp.clock()) == "collect"
    ran: list[str] = []

    async def fake_run(name: str) -> None:
        ran.append(name)

    jp._run = fake_run  # type: ignore[method-assign]
    monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 100)
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        assert jp.tick() is False and ran == []  # 메모리 부족 → 미룸 (기록도 안 함)
        monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 3000)
        assert jp.tick() is True
        loop.run_until_complete(jp.task)
        assert ran == ["collect"] and jp.due(jp.clock()) not in ("collect", "")  # 같은 날 두 번 안 돎 · 다음은 놓친 03:30~05:50 일들
        runs = jp.store.kv_get("runs")
        jp.store.kv_set("runs", {**runs, "ladder": runs["collect"], "clean": runs["collect"], "weekly": runs["collect"], "growth": runs["collect"]})
        assert jp.due(jp.clock()) == ""
        jp.fake_clock.t += 19 * 60  # 06:20
        assert jp.due(jp.clock()) == "extract"
        jp.fake_clock.t = datetime(2026, 11, 23, 10, 0, tzinfo=kst).timestamp()  # 3시간 넘게 놓친 05:30 은 따라잡지 않음
        assert jp.due(jp.clock()) == ""
    finally:
        loop.close()
        asyncio.set_event_loop(None)
