"""취준 CtCI — 가짜 쪽 글로 문제/풀이 짝짓기 · 장별 개수 검증(종료 코드 1) · overrides · 간격 1/3/7/21 · 힌트 열면 처음부터 ·
주간 몫 · 몸풀기 수 조절 · 사다리 시험은 ref 와 expected 가 같은 경우만 (다르면 버림)."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import ctci
from jobprep_helpers import ctci_rows, make_jp, write_ctci

ROOT = Path(__file__).resolve().parents[1]


def fake_pages() -> tuple[list[str], list[int], list[int]]:
    pages = [
        "1\nArrays and Strings\nintro text",
        "Interview Questions\n1.1 Is Unique: Implement an algorithm to determine if a string has all unique characters.\nHints: #44, #117\n"
        "1.2\n1.3\nCheck Permutation: Given two strings, decide if one is a permutation.\nHints: #1\n"
        "URLify: Replace spaces with %20.\nHlnts: #53\n",
        "2\nLinked Lists\n",
        "Interview Questions\n2.1 R\ufffdmov\ufffd Dups! Write code to remove duplicates.\nHints: #9\n",
        "X. Solutions",
        "1\nSolutions to Arrays and Strings\n1.1 Is Unique: Implement an algorithm ...\npg90\nSOLUTION\nUse a set.\n",
        "Solutions to Chapter 1 I Arrays\n1.2 Check Permutation: Given two strings ...\nSOLUTION\nSort both.\n"
        "1.3 URLify: Replace spaces ...\nSOLUTI0!'.1\nWalk backwards.\n",
        "2\nSolutions to Linked Lists\n2.1 Remove Dups: Write code ...\nSOLUTION\nHash set.\n",
        "XI. Advanced Topics",
    ]
    return pages, [0, 2, 4], [5, 7, 8]


def test_split_and_pair_from_fake_pages() -> None:
    pages, chp, solp = fake_pages()
    qs = ctci.split_questions(pages, chp)
    assert [(q["qid"], q["title"]) for q in qs] == [("1.1", "Is Unique"), ("1.2", "Check Permutation"), ("1.3", "URLify"), ("2.1", "Remove Dups")]
    missing = ctci.find_solutions(pages, solp, qs)
    assert missing == []
    assert "Use a set" in qs[0]["solution"] and "Sort both" in qs[1]["solution"] and "Walk backwards" in qs[2]["solution"]
    assert "Sort both" not in qs[0]["solution"] and qs[3]["sol_page"] == 7
    errs = ctci.verify(qs, missing)
    assert errs and errs[0].startswith("1장: 3개")  # 189 개수와 안 맞음
    qs = ctci.apply_overrides(qs, {"1.3": {"title": "URLify!", "q_page": 9}, "9.9": {"title": "x"}})
    assert qs[2]["title"] == "URLify!" and qs[2]["q_page"] == 9
    rows = ctci.index_rows(qs, part1_pages=6)
    assert rows[0]["kind"] == "code" and rows[3]["sol_part"] == 2 and rows[3]["sol_page"] == 2


def test_extract_tool_exits_1_when_counts_do_not_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    pages1 = ["toc"] * 2
    outline = []
    for ch in range(1, 18):
        outline.append((f"{ch}. {ctci.CH_TITLES[ch]}", len(pages1)))
        pages1 += [f"Interview Questions\n{ch}.1 Title{ch}: text.\nHints: #1\n"]
    outline.append(("X. Solutions", len(pages1)))
    pages1 += ["X. Solutions"]
    pages2 = []
    for ch in range(1, 18):
        pages2 += [f"{ch}\nSolutions", f"Solutions to Chapter {ch} I x\n{ch}.1 Title{ch}: text\nSOLUTION\nbody\n"]
    pages2 += ["XI. Advanced Topics"]

    class Page:
        def __init__(self, t: str) -> None:
            self.t = t

        def extract_text(self) -> str:
            return self.t

    class Item:
        def __init__(self, title: str, page: int) -> None:
            self.title, self.page = title, page

    class Reader:
        def __init__(self, path: str) -> None:
            part1 = "part1" in path
            self.pages = [Page(t) for t in (pages1 if part1 else pages2)]
            self.outline = [Item(t, p) for t, p in outline] if part1 else []

        def get_destination_page_number(self, it: Item) -> int:
            return it.page

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=Reader))
    d = tmp_path / "ctci"
    d.mkdir()
    (d / "CtCI_part1_x.pdf").write_bytes(b"x")
    (d / "CtCI_part2_x.pdf").write_bytes(b"x")
    spec = importlib.util.spec_from_file_location("jx", ROOT / "tools" / "jobprep_ctci_extract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    monkeypatch.setattr(sys, "argv", ["x", "--data", str(tmp_path)])
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "1장: 1개 (책 9개)" in out and "text." not in out  # 책 글은 화면에 안 찍음
    assert (d / "index.json").exists() and oct((d / "text" / "1.1.json").stat().st_mode & 0o777) == "0o600"


@pytest.fixture
def jp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    j = make_jp(tmp_path, monkeypatch)
    write_ctci(j, ctci_rows({1: 3, 2: 2, 6: 2}), {"1.1": {"question": "Is the string unique?", "solution": "Use a set", "book_hints_raw": "Hints: #1"}})
    return j


def test_spaced_review_and_restart_after_hints(jp) -> None:  # type: ignore[no-untyped-def]
    ctci.mark_solved(jp, "1.1")
    assert jp.store.one("SELECT due, n FROM reviews WHERE qid='1.1'") == {"due": "2026-11-19", "n": 1}  # 1일 뒤
    ctci.schedule_review(jp, "1.1", reset=False)
    assert jp.store.one("SELECT due, n FROM reviews WHERE qid='1.1'")["n"] == 2  # 3일
    ctci.schedule_review(jp, "1.1", reset=False)
    ctci.schedule_review(jp, "1.1", reset=False)
    assert jp.store.one("SELECT due FROM reviews WHERE qid='1.1'")["due"] == "2026-12-09"  # 21일
    ctci.schedule_review(jp, "1.1", reset=False)
    assert jp.store.one("SELECT * FROM reviews WHERE qid='1.1'") is None  # 끝
    # 힌트 3단을 열고 풀었으면 간격을 처음부터
    jp.store.x("INSERT INTO ctci_progress (qid, state, opened_hints) VALUES ('1.2', 'open', 3)")
    ctci.mark_solved(jp, "1.2")
    ctci.schedule_review(jp, "1.2", reset=False)
    ctci.mark_solved(jp, "1.2")  # 복습에서 다시 풀었지만 힌트를 열었던 문제
    assert jp.store.one("SELECT n FROM reviews WHERE qid='1.2'")["n"] == 1


def test_weekly_quota(jp) -> None:  # type: ignore[no-untyped-def]
    q = ctci.quota(jp)
    assert q["total"] == 7 and q["remain"] == 7 and q["per_day"] == 2  # 하루 최소 2
    jp.conf.book_finish_by = "2026-11-25"  # 1주 남음
    q = ctci.quota(jp)
    assert q["weeks"] == 1 and q["per_week"] == 7
    ctci.mark_solved(jp, "1.1")
    assert ctci.quota(jp)["week_done"] == 1
    assert [r["qid"] for r in ctci.next_new(jp, 2)] == ["6.1", "1.2"]  # 수요일: 설명 문제 하나 + 코드 장 순서
    jp.fake_clock.t += 86400  # 목요일
    assert [r["qid"] for r in ctci.next_new(jp, 2)] == ["1.2", "1.3"]


def test_warmups_shown(jp) -> None:  # type: ignore[no-untyped-def]
    write_ctci(jp, ctci_rows({1: 6}))
    assert ctci.warmups_to_show(jp, 1) == 2
    for i, q in enumerate(("1.1", "1.2", "1.3")):
        jp.fake_clock.t += 10
        ctci.mark_solved(jp, q)
    assert ctci.warmups_to_show(jp, 1) == 1  # 최근 3문제 힌트 없이
    jp.store.x("UPDATE ctci_progress SET opened_hints=1 WHERE qid='1.3'")
    assert ctci.warmups_to_show(jp, 1) == 3


def ladder_answer(book_tests: list[dict], ref: str) -> dict:
    return {"concept": "set 으로 본 글자를 기억", "hints": ["h1", "h2", "h3"], "twist": "추가 공간 없이", "leetcode": [{"name": "x", "url": "https://leetcode.com/problems/x/"},
            {"name": "bad", "url": "https://evil.example/"}],
            "warmups": [{"title": "w", "statement": "s", "fn": "w", "compare": "exact", "codec": "none", "ref_solution": "def w(a):\n    return a + 1\n",
                         "tests": [{"args": [i], "expected": i + 1} for i in range(4)]}],
            "book": {"title": "b", "statement": "s", "fn": "is_unique", "compare": "exact", "codec": "none", "ref_solution": ref, "tests": book_tests}}


async def test_ladder_keeps_only_tests_ref_agrees_with(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "k")
    ref = "def is_unique(s):\n    return len(set(s)) == len(s)\n"
    good = [{"args": [w], "expected": len(set(w)) == len(w)} for w in ("abc", "aa", "", "abca", "xyz", "q", "qq", "abcd", "zz", "pq")]
    bad = [{"args": ["abc"], "expected": False}]  # Gemini 가 틀린 기대값 → 버림
    jp.fake_http.gemini = [ladder_answer(good + bad, ref)]
    lad = await ctci.make_ladder(jp, "1.1")
    assert len(lad["book"]["tests"]) == 10 and lad["tests_ready"] and bad[0] not in lad["book"]["tests"]
    assert [x["url"] for x in lad["leetcode"]] == ["https://leetcode.com/problems/x/"]
    view = ctci.q_view(jp, "1.1")
    assert view["hints"] == [] and "solution" not in view and view["book"]["n_tests"] == 10
    r = await ctci.run(jp, {"qid": "1.1", "item": "book", "code": ref})
    assert r["solved"] and r["passed"] == 10
    r = await ctci.run(jp, {"qid": "1.1", "item": "book", "code": "def is_unique(s):\n    return True\n"})
    assert not r["solved"] and r["fail"]["expected"] == "false"
    st = ctci.step(jp, {"qid": "1.1", "step": "hint"})
    assert st["hints"] == ["h1"]
    st = ctci.step(jp, {"qid": "1.1", "step": "solution"})
    assert st["solution"] == "Use a set" and ctci.q_view(jp, "1.1")["saw_solution"]


async def test_ladder_with_too_few_valid_tests_retries_then_self_grade(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "k")
    ref = "def is_unique(s):\n    return True\n"
    few = [{"args": ["ab"], "expected": True}, {"args": ["aa"], "expected": False}]
    jp.fake_http.gemini = [ladder_answer(few, ref), ladder_answer(few, ref)]
    lad = await ctci.make_ladder(jp, "1.1")
    assert not lad["tests_ready"] and len([c for c in jp.fake_http.calls if c[0] == "POST"]) == 2
    assert ctci.q_view(jp, "1.1")["tests_ready"] is False


async def test_run_limits_per_minute_day_and_memory(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from dealbot.dashboard.jobprep import store
    from dealbot.dashboard.jobprep.store import JpError

    jp.store.kv_set("ladder:1.1", {"kind": "code", "book": {"fn": "f", "tests": [{"args": [1], "expected": 1}], "compare": "exact", "codec": "none"}})
    monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 3000)
    for _ in range(ctci.RUN_PER_MIN):
        await ctci.run(jp, {"qid": "1.1", "item": "book", "code": "def f(x):\n    return x\n"})
    with pytest.raises(JpError) as e:
        await ctci.run(jp, {"qid": "1.1", "item": "book", "code": "def f(x):\n    return x\n"})
    assert e.value.status == 429
    jp.fake_clock.t += 61
    monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 100)
    with pytest.raises(JpError) as e:
        await ctci.run(jp, {"qid": "1.1", "item": "book", "code": "def f(x):\n    return x\n"})
    assert e.value.status == 503
    monkeypatch.setattr(store, "mem_free_mb", lambda meminfo=None: 3000)
    jp.store.x("UPDATE usage SET n=? WHERE kind='sandbox'", (ctci.RUN_PER_DAY,))
    with pytest.raises(JpError) as e:
        await ctci.run(jp, {"qid": "1.1", "item": "book", "code": "def f(x):\n    return x\n"})
    assert e.value.status == 429 and "오늘" in e.value.text


async def test_ladder_failure_waits_an_hour(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", "k")
    jp.fake_http.gemini = ["not json"]
    jp.fake_clock.t += 86400  # 목요일 — 다음 새 문제가 1.1
    v = ctci.q_view(jp, "1.1")
    assert v["making"] and v["started"]
    await asyncio.wait_for(jp.bg["ladder:1.1"], 10)
    v = ctci.q_view(jp, "1.1")
    assert not v["started"] and "1시간" in v["err"]
    await ctci.pregen(jp, 1)
    assert ctci.ladder(jp, "1.1") is None and ctci.ladder_err(jp, "1.1")  # 미리 만들기도 1.1 은 쉬고 다음 문제로
    jp.fake_clock.t += ctci.LADDER_RETRY_S + 1
    assert ctci.start_ladder(jp, "1.1") is True


async def test_nightly_prebuild_stays_under_daily_budget(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from dealbot import api_credit

    monkeypatch.delenv("JOBPREP_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    jp.fake_clock.t += 86400  # 목요일 — 코드 문제만
    ref = "def is_unique(s):\n    return len(set(s)) == len(s)\n"
    import json as _json

    calls = []

    async def fake(task: str, model: str, system: str, user: str, **kw: object) -> str:
        calls.append(model)
        api_credit.record(task, "sonnet", {"input_tokens": int(0.6 / 3.0 * 1e6), "output_tokens": 0})  # 사다리 하나 $0.6
        return _json.dumps(ladder_answer([{"args": ["ab"], "expected": True}] * 9, ref))

    jp.llm.claude_ask = fake
    assert jp.conf.ladder_prebuild_daily_usd == 1.0 and jp.conf.ladder_prebuild_max == 3
    made = await ctci.pregen(jp)
    assert made == 2 and set(calls) == {"sonnet"}  # $0.6 → $1.2 에서 멈춤 (3개 몫이 남아도)
    assert ctci.ladder(jp, "1.1") is not None
