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
    assert bars["교양"] == {"area": "교양", "done": 3.0, "pending": 0.0, "need": 30.0, "left": 27.0} and bars["전선"]["done"] == 0.0 and bars["총"]["done"] == 6.0
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


async def test_chat_send_is_claude_and_stores_history(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "free")  # 무료 키가 있어도 대화는 Claude
    seen = []

    async def fake(task: str, model: str, system: str, user: str, **kw: object) -> str:
        seen.append(model)
        return "코치 답"

    jp.llm.claude_ask = fake
    out = await chat.send(jp, {"tab": "jobs", "ctx": "", "text": "어디 지원할까?", "model": "gemini"})
    assert out["reply"] == "코치 답" and out["model"] == "claude" and seen == ["sonnet"] and jp.fake_http.calls == []
    h = chat.history(jp, "jobs", "")
    assert [m["role"] for m in h] == ["user", "assistant"]


def test_failed_slot_retries_once(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dealbot.dashboard.jobprep import store

    jp.fake_clock.t = datetime(2026, 11, 24, 18, 5, tzinfo=ZoneInfo("Asia/Seoul")).timestamp()  # 화요일 18:05 — recheck 만
    monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 3000)
    calls: list[str] = []

    async def boom(name: str) -> None:
        calls.append(name)
        raise RuntimeError("x")

    jp._work = boom  # type: ignore[method-assign]
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        for _ in range(3):
            if jp.tick():
                loop.run_until_complete(jp.task)
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    assert calls == ["recheck:18:00", "recheck:18:00"]  # 처음 + 한 번 더 · 그 뒤엔 그날 쉼
    assert jp.store.kv_get("runs")["recheck:18:00"]["state"] == "started"


def test_deadlines_and_chat_context_respect_exclusions(jp) -> None:  # type: ignore[no-untyped-def]
    cid = jp.store.x("INSERT INTO companies (name, norm, cat, on_) VALUES ('NoCo','noco','ib',1)")
    jp.store.x("INSERT INTO jobs (ukey, company_id, title, deadline, state, auto_out, cat) VALUES ('a', ?, 'Hidden Intern', '2026-11-20', 'open', 0, 'ib')", (cid,))
    assert today.view(jp)["deadlines"] and "Hidden Intern" in chat.context(jp, "jobs", "")
    from dealbot.dashboard.jobprep import jobs

    jobs.exclude(jp, {"scope": "company", "key": str(cid)})
    assert today.view(jp)["deadlines"] == [] and "Hidden Intern" not in chat.context(jp, "jobs", "")


def test_tick_runs_due_slot_once_and_waits_on_low_memory(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dealbot.dashboard.jobprep import store

    kst = ZoneInfo("Asia/Seoul")
    jp.fake_clock.t = datetime(2026, 11, 23, 6, 1, tzinfo=kst).timestamp()  # 월요일 06:01
    assert jp.due(jp.clock()) == "collect"
    ran: list[str] = []

    async def fake_work(name: str) -> None:
        ran.append(name)

    jp._work = fake_work  # type: ignore[method-assign]
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
        assert runs["collect"]["state"] == "done" and runs["collect"]["tries"] == 1
        jp.store.kv_set("runs", {**runs, "ladder": runs["collect"], "clean": runs["collect"], "weekly": runs["collect"], "growth": runs["collect"]})
        assert jp.due(jp.clock()) == ""
        jp.fake_clock.t += 19 * 60  # 06:20
        assert jp.due(jp.clock()) == "extract"
        jp.fake_clock.t = datetime(2026, 11, 23, 10, 0, tzinfo=kst).timestamp()  # 3시간 넘게 놓친 05:30 은 따라잡지 않음
        assert jp.due(jp.clock()) == ""
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def test_retake_plan_candidates_counts_and_load(jp) -> None:  # type: ignore[no-untyped-def]
    rows = [{"name": "공수2", "credits": 3, "grade": "A-", "area": "교양", "term": "2024-2", "retake": 1},
            {"name": "대중음악", "credits": 3, "grade": "B0", "area": "교양", "term": "2023-1"},
            {"name": "디지털", "credits": 3, "grade": "B+", "area": "전필", "term": "2024-1"},
            {"name": "전자회로", "credits": 3, "grade": "B+", "area": "전필", "term": "2024-2"},
            {"name": "채플", "credits": 1, "grade": "P", "area": "교양", "term": "2023-1"},
            {"name": "철회", "credits": 3, "grade": "W", "area": "전선", "term": "2024-1"},
            {"name": "낙제", "credits": 3, "grade": "F", "area": "전선", "term": "2024-1"},
            {"name": "비통과", "credits": 1, "grade": "NP", "area": "기타", "term": "2024-1"}]
    academic.save_courses(jp, {"courses": rows})
    academic.save_req(jp, {"총": 130})
    v = academic.view(jp)
    bars = {b["area"]: b["done"] for b in v["bars"]}
    assert bars["총"] == 13.0 and bars["전선"] == 0.0  # W·F·NP 는 학점 아님 (P 는 학점)
    r = v["retake"]
    assert (r["total"], r["used"], r["left"], r["max"]) == (6, 1, 5, "A0") and "sc/279" in r["src"]
    assert [c["name"] for c in r["b0"]["cands"]] == ["낙제", "대중음악"]  # 기본 B0 이하 · F 가 이득 가장 큼
    assert [c["name"] for c in r["bplus"]["cands"]][:2] == ["낙제", "대중음악"] and len(r["bplus"]["cands"]) == 4
    assert r["bplus"]["after"] > r["b0"]["after"] > r["now"]
    load = r["b0"]["load"]
    assert load["left_credits"] == 117.0 and load["retake_credits"] == 6.0 and load["per_term"] == 41.0 and load["over"]


def test_retake_top_n_limited_by_left(jp) -> None:  # type: ignore[no-untyped-def]
    rows = [{"name": f"C{i}", "credits": 3, "grade": "C0", "area": "전선", "term": "x", "retake": 1 if i < 5 else 0} for i in range(8)]
    academic.save_courses(jp, {"courses": rows})
    r = academic.view(jp)["retake"]
    assert r["left"] == 1 and len(r["b0"]["top"]) == 1


def test_acad_migration_adds_columns_and_sets_statuses(tmp_path: Path) -> None:
    import sqlite3

    from dealbot.dashboard.jobprep import store as st

    d = tmp_path / "old"
    d.mkdir()
    c = sqlite3.connect(d / "jobprep.db")
    c.execute("CREATE TABLE acad_courses (id INTEGER PRIMARY KEY, name TEXT, credits REAL, grade TEXT, area TEXT, term TEXT, retake INTEGER DEFAULT 0)")
    c.execute("INSERT INTO acad_courses VALUES (34, 'YCA1101 기독교와세계문화', 3, 'P', '교양', '2026-여름', 0)")
    c.execute("INSERT INTO acad_courses VALUES (35, 'YCE1251 전쟁과역사', 3, '', '교양', '2026-2', 0)")
    c.execute("INSERT INTO acad_courses VALUES (36, '[계획] 군강좌', 3, '', '교양', '2027-1', 0)")
    c.execute("INSERT INTO acad_courses VALUES (37, '[계획] 군강좌', 3, 'P', '교양', '2027-1', 0)")  # 성적이 있으면 안 바꿈
    c.commit()
    c.close()
    s = st.Store(d)
    rows = {r["id"]: r for r in s.q("SELECT * FROM acad_courses")}
    assert (rows[35]["status"], rows[35]["military"]) == ("in_progress", 1) and rows[36]["status"] == "planned"
    assert rows[37]["status"] == "done" and rows[34]["military"] == 1 and rows[34]["no_retake"] == 0
    s.x("UPDATE acad_courses SET status='done' WHERE id=35")
    s.close()
    assert st.Store(d).one("SELECT status FROM acad_courses WHERE id=35")["status"] == "done"  # 한 번만


def test_acad_check_real_shape(jp) -> None:  # type: ignore[no-untyped-def]
    jp.conf.required_low_level = {"EEE2113": 3}
    rows = [{"id": 1, "name": "EEE3313 기초디지털실험", "credits": 3, "grade": "A-", "area": "전필", "term": "2025-1"},
            {"id": 2, "name": "YCD1602 대중음악의이해", "credits": 3, "grade": "B0", "area": "교양", "term": "2024-1", "no_retake": 1},
            {"id": 3, "name": "EEE2040 디지털논리회로", "credits": 3, "grade": "C0", "area": "전필", "term": "2025-1"},
            {"id": 4, "name": "YCE1251 전쟁과역사", "credits": 3, "grade": "", "area": "교양", "term": "2026-2", "status": "in_progress", "military": 1},
            {"id": 5, "name": "[계획] 군강좌", "credits": 3, "grade": "", "area": "교양", "term": "2027-1", "status": "planned", "military": 1},
            {"id": 6, "name": "AIC2100 인공지능", "credits": 3, "grade": "P", "area": "기타", "term": "2026-여름", "military": 1},
            {"id": 7, "name": "YCG1804 감정", "credits": 3, "grade": "A+", "area": "교양", "term": "2026-여름", "military": 1}]
    academic.save_courses(jp, {"courses": rows})
    academic.save_req(jp, {"교양": 6, "전필": 9, "기타": 3, "총": 30})
    v = academic.view(jp)
    got = {r["id"]: r for r in v["courses"]}
    assert got[2]["no_retake"] == 1 and got[4]["status"] == "in_progress" and got[5]["military"] == 1  # 저장해도 칸 유지
    assert v["gpa"] == round((3.7 * 3 + 3.0 * 3 + 2.0 * 3) / 9, 2)  # 군강좌(7)·수강 중·계획은 평점 밖
    bars = {b["area"]: b for b in v["bars"]}
    assert (bars["교양"]["done"], bars["교양"]["pending"]) == (6.0, 6.0) and bars["총"]["done"] == 15.0 and bars["총"]["pending"] == 6.0
    assert bars["기타"]["left"] == 0.0  # 교양 넘친 6 이 일반선택으로
    assert v["upper"]["done"] == 3.0 and v["upper"]["short"] == 42.0
    # 남은: 칸별 전필 3 (총 30−21=9 가 더 큼) · 3000+ 로 채울 수 있는 것 9−3(EEE2113) = 6 → 42−6 = 36 더 → 45
    assert v["real_left"] == 9.0 + 36.0 and v["upper"]["extra"] == 36.0
    assert v["ge"]["done"] == 2 and "인간과역사" not in v["ge"]["missing"] and v["ge"]["planned_unknown"] == 1
    assert [a["state"] for a in v["ge"]["areas"] if a["area"] == "인간과역사"] == ["in_progress"]
    m = v["military"]
    assert m["used"] == 12.0 and [t["credits"] for t in m["terms"]] == [3.0, 6.0, 3.0] and not m["over"]
    cands = [c["name"] for c in v["retake"]["bplus"]["cands"]]
    assert cands == ["EEE2040 디지털논리회로", "EEE3313 기초디지털실험"] or cands == ["EEE2040 디지털논리회로"]  # no_retake·군강좌 A+ 빠짐
    assert v["retake"]["b0"]["load"]["left_credits"] == 45.0
    jp.conf.military_per_term = 5
    assert academic.view(jp)["military"]["over"]


def test_owner_rules_major_prefixes_and_required_missing(jp) -> None:  # type: ignore[no-untyped-def]
    rows = [{"name": "EEE2010 기초회로이론", "credits": 3, "grade": "A0", "area": "전필", "term": "2024-1"},
            {"name": "EEE3540 전공과목", "credits": 3, "grade": "A0", "area": "기타", "term": "2024-1"},  # 소속변경 전 EEE → 전공(전선)
            {"name": "CEE3201 기초유체역학", "credits": 3, "grade": "A0", "area": "전선", "term": "2024-1"},  # CEE → 전선 아님
            {"name": "EEE2060 신호및시스템", "credits": 3, "grade": "", "area": "전필", "term": "2027-2", "status": "planned"}]
    academic.save_courses(jp, {"courses": rows})
    academic.save_req(jp, {"전필": 30, "전선": 30, "기타": 21, "총": 130})
    v = academic.view(jp)
    bars = {b["area"]: b for b in v["bars"]}
    assert bars["전선"]["done"] == 3.0 and bars["기타"]["done"] == 3.0
    miss = {r["code"]: r for r in v["required_missing"]}
    assert "EEE2010" not in miss and miss["EEE2060"]["pending"] and miss["EEE2113"]["note"] == "확인 필요 (학과 사무실)"
    assert miss["EEE4610"]["note"] == ""
