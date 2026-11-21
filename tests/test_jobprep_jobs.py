"""취준 공고 — miss 2번 닫힘 · 읽기 실패는 miss 아님 · 마감 지남 바로 닫힘 · 지원한 건 보드에 남음 · 다시 열림 · 제외/되돌리기 ·
시기 묶음(6월·7/14 뒤가 한 묶음) · ★ 낮아도 보임 · 지켜보는 회사 해시 · Gemini 뽑기(가짜)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import jobs
from jobprep_helpers import make_jp


@pytest.fixture
def jp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    j = make_jp(tmp_path, monkeypatch)
    cid = j.store.x("INSERT INTO companies (name, norm, cat, region, ats, token, career_url, growth_note, on_) VALUES ('Acme','acme','ai_growth','KR','greenhouse','acme','','',1)")
    j.comp = {"id": cid, "name": "Acme", "cat": "ai_growth"}
    return j


def post(i: str, title: str = "Software Intern", **kw: object) -> dict:
    return {"src_id": i, "title": title, "loc": "Seoul", "url": f"https://a/{i}", "text": "", "deadline": None, **kw}


def test_miss_twice_closes_and_failed_read_does_not_count(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1"), post("2", "Data Intern")], ok=True, now=1)
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1")], ok=True, now=2)
    assert s.one("SELECT state, miss FROM jobs WHERE src_id='2'") == {"state": "open", "miss": 1}
    jobs.apply_board(s, c, "greenhouse", "acme", [], ok=False, now=3)  # 읽기 실패
    assert s.one("SELECT state, miss FROM jobs WHERE src_id='2'") == {"state": "open", "miss": 1}
    st = jobs.apply_board(s, c, "greenhouse", "acme", [post("1")], ok=True, now=4)
    assert st["closed"] == 1 and s.one("SELECT state, closed_why FROM jobs WHERE src_id='2'") == {"state": "closed", "closed_why": "목록에서 사라짐"}
    assert s.one("SELECT kind FROM job_events WHERE kind='closed'")
    st = jobs.apply_board(s, c, "greenhouse", "acme", [post("1"), post("2", "Data Intern")], ok=True, now=5)
    assert st["reopened"] == 1 and s.one("SELECT state FROM jobs WHERE src_id='2'")["state"] == "open"


def test_reappear_resets_miss(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1")], ok=True, now=1)
    jobs.apply_board(s, c, "greenhouse", "acme", [], ok=True, now=2)
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1")], ok=True, now=3)
    jobs.apply_board(s, c, "greenhouse", "acme", [], ok=True, now=4)
    assert s.one("SELECT state FROM jobs")["state"] == "open"


def test_deadline_passed_closes_now(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1", deadline="2026-11-01"), post("2", "Data Intern", deadline="2026-12-31")], ok=True, now=1)
    n = jobs.close_past_deadlines(s, jp.clock())
    assert n == 1 and s.one("SELECT closed_why FROM jobs WHERE src_id='1'")["closed_why"] == "마감일 지남"


def test_applied_stays_on_board_when_closed(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1")], ok=True, now=1)
    jid = s.one("SELECT id FROM jobs")["id"]
    jobs.mark(jp, {"id": jid, "status": "applied"})
    jobs.close(s, jid, "목록에서 사라짐", 2)
    v = jobs.view(jp, {})
    assert [x["id"] for x in v["board"]["applied"]] == [jid] and v["closed"][0]["id"] == jid


def test_exclude_and_restore(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1"), post("2", "Data Intern")], ok=True, now=1)
    ids = [r["id"] for r in s.q("SELECT id FROM jobs ORDER BY id")]
    jobs.exclude(jp, {"scope": "job", "key": str(ids[0])})
    shown = [x["id"] for g in jobs.view(jp, {})["groups"] for x in g["items"]]
    assert shown == [ids[1]]
    jobs.exclude(jp, {"scope": "company", "key": str(c["id"])})
    assert not [x for g in jobs.view(jp, {})["groups"] for x in g["items"]]
    for e in jobs.view(jp, {})["exclusions"]:
        jobs.restore(jp, {"id": e["id"]})
    assert len([x for g in jobs.view(jp, {})["groups"] for x in g["items"]]) == 2
    jobs.exclude(jp, {"scope": "cat", "key": "ai_growth"})
    assert not [x for g in jobs.view(jp, {})["groups"] for x in g["items"]]


def test_season_groups_and_low_stars_visible(jp) -> None:  # type: ignore[no-untyped-def]
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1"), post("2", "Data Intern"), post("3", "Ops Intern")], ok=True, now=1)
    ids = [r["id"] for r in s.q("SELECT id FROM jobs ORDER BY id")]
    s.x("UPDATE jobs SET season='2027_summer_june', fit=1, extracted=1 WHERE id=?", (ids[0],))
    s.x("UPDATE jobs SET season='2027_summer_july', fit=5, extracted=1 WHERE id=?", (ids[1],))
    s.x("UPDATE jobs SET season='2027_winter', fit=2, extracted=1 WHERE id=?", (ids[2],))
    g = {x["label"]: x["items"] for x in jobs.view(jp, {})["groups"]}
    summer = g["2027 여름"]
    assert {x["id"] for x in summer} == {ids[0], ids[1]} and {x["tag"] for x in summer} == {"6월 시작", "7/14 뒤 시작"}
    assert any(x["fit"] == 1 for x in summer)  # ★1 도 보임
    assert [x["id"] for x in g["2027 겨울"]] == [ids[2]]


def test_watch_page_hash_change(jp) -> None:  # type: ignore[no-untyped-def]
    s = jp.store
    assert jobs.watch_update(s, 1, "https://c/careers", "<html><p>Jobs</p><p>Updated 2026-11-01</p></html>", 1) is None
    assert jobs.watch_update(s, 1, "https://c/careers", "<html><p>Jobs</p><p>Updated 2026-11-20</p></html>", 2) is None  # 숫자만 바뀜 → 그대로
    diff = jobs.watch_update(s, 1, "https://c/careers", "<html><p>Jobs</p><p>Summer Intern 2027 open</p></html>", 3)
    assert diff and "Summer Intern" in diff
    assert s.one("SELECT kind FROM job_events WHERE kind='watch'")


def test_page_check_html(jp) -> None:  # type: ignore[no-untyped-def]
    assert jobs.judge_page(404, "") == "gone" and jobs.judge_page(200, "<p>접수 마감되었습니다</p>") == "closed_text"
    assert jobs.judge_page(200, "<p>apply now</p>") == "ok" and jobs.judge_page(500, "") == "fail"
    r = jobs.add(jp, {"url": "https://co.example/job/1", "company": "Co", "title": "Intern"})
    jobs.apply_page_check(jp.store, r["id"], "closed_text", 1)
    assert jp.store.one("SELECT state FROM jobs WHERE id=?", (r["id"],))["state"] == "open"
    jobs.apply_page_check(jp.store, r["id"], "closed_text", 2)
    assert jp.store.one("SELECT state, closed_why FROM jobs WHERE id=?", (r["id"],)) == {"state": "closed", "closed_why": "마감 문구"}


async def test_extract_with_fake_gemini(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "k")
    s, c = jp.store, jp.comp
    jobs.apply_board(s, c, "greenhouse", "acme", [post("1"), post("2", "Data Intern")], ok=True, now=1)
    ids = [r["id"] for r in s.q("SELECT id FROM jobs ORDER BY id")]
    jp.fake_http.gemini = [{"items": [
        {"id": ids[0], "start": "2027-06", "deadline": "2027-01-15", "conversion": True, "bachelor_ok": True, "grad_ok": True,
         "season": "2027_summer_june", "is_intern": True, "fit": 4, "fit_why": "관심 분야"},
        {"id": ids[1], "start": None, "deadline": None, "conversion": None, "bachelor_ok": False, "grad_ok": None, "season": "other",
         "is_intern": True, "fit": 2, "fit_why": "석사만"}]}]
    assert await jobs.extract_pending(jp) == 2
    a = s.one("SELECT * FROM jobs WHERE id=?", (ids[0],))
    assert (a["fit"], a["deadline"], a["conversion"], a["auto_out"]) == (4, "2027-01-15", 1, 0)
    assert s.one("SELECT auto_out FROM jobs WHERE id=?", (ids[1],))["auto_out"] == 1
    v = jobs.view(jp, {})
    assert v["auto_out_n"] == 1
    req = json.dumps(jp.fake_http.calls[-1][3], ensure_ascii=False)
    assert "Software Intern" in req and "연세대" in req


async def test_collect_runs_boards_with_fake_http(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from dealbot.dashboard.jobprep import collectors as co

    jp.fake_http.routes[co.URLS["greenhouse"].format(token="acme")] = (200, json.dumps({"jobs": [
        {"id": 5, "title": "Quant Intern", "location": {"name": "Hong Kong"}, "absolute_url": "https://a/5", "content": ""}]}).encode())
    jp.store.x("UPDATE companies SET on_=0 WHERE id<>?", (jp.comp["id"],))
    res = await jobs.collect(jp)
    assert res["boards"]["Acme"]["new"] == 1
    assert all(c[1].startswith("https://") for c in jp.fake_http.calls)
