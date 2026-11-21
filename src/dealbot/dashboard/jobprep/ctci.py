"""⑤ 코딩 (CtCI) — 문제 목록(한 번 뽑기) · 사다리(문제마다 딱 한 번 · Gemini good) · 시험 정답 맞추기(샌드박스) · 간격 복습 · 주간 몫.

책 글은 git·로그·static 에 안 들어감 — jobprep-data/ctci/ 에만, API 답은 no-store (로그인 아래).
뽑기 순수 함수(split_questions·find_solutions·verify·apply_overrides)는 tools/jobprep_ctci_extract.py 가 PDF 글로 부른다.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import math
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep.store import JpError, clip

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

EXPECTED = [9, 8, 6, 12, 8, 10, 12, 14, 8, 11, 6, 11, 8, 7, 7, 26, 26]  # 장별 문제 수 (합 189)
CODE_CH = {1, 2, 3, 4, 5, 8, 10, 16, 17}
ORDER = [1, 2, 3, 4, 8, 10, 5, 16, 17]
EXPLAIN_PER_WEEK = 2
INTERVALS = [1, 3, 7, 21]
QID_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")
MIN_TESTS = 8
LADDER_VER = 1
LADDER_THINKING = 1024  # Gemini 생각 토큰 상한 (출력이 잘리지 않게 — 리뷰 11/22)
LADDER_RETRY_S = 3600
RUN_PER_MIN = 6
RUN_PER_DAY = 300
FOOT_RE = re.compile(r"^(Chapter \d.*|\d+ Cracking the Coding Interview.*|CrackingTheCoding.*|Cracking the Coding Interview, 6th Edition\s*|"
                     r"Solutions to Chapter.*)$")
HINTS_RE = re.compile(r"^\s*H[il1]nts\s*[:;]?\s*#")
SOL_RE = re.compile(r"SOLUT[I1l][O0]\S{0,4}")


# ── 뽑기 (순수 함수) ──
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _lines(pages: list[str], a: int, b: int) -> list[tuple[int, str]]:
    out = []
    for i in range(a, min(b, len(pages))):
        for ln in (pages[i] or "").split("\n"):
            if not FOOT_RE.match(ln.strip()):
                out.append((i, ln))
    return out


def split_questions(pages: list[str], ch_pages: list[int]) -> list[dict[str, Any]]:
    """ch_pages = [1장 시작, 2장 시작, …, 17장 시작, 풀이 시작] (전역 쪽 번호). 문제는 'Hints: #' 줄로 끝난다 → 장 안 차례로 번호."""
    out: list[dict[str, Any]] = []
    for c in range(len(ch_pages) - 1):
        ch = c + 1
        lines = _lines(pages, ch_pages[c], ch_pages[c + 1])
        k = next((j for j, (_i, ln) in enumerate(lines) if "Interview Questions" in ln), None)
        if k is None:
            k = next((j for j, (_i, ln) in enumerate(lines) if re.match(rf"^\s*{ch}\s*[.,]\s*1\s", ln)), 0) - 1
        cur: list[tuple[int, str]] = []
        n = 0
        for i, ln in lines[k + 1:]:
            if HINTS_RE.match(ln):
                n += 1
                text = " ".join(x.strip() for _p, x in cur)
                m = re.search(rf"\b{ch}\s*[.,]\s*[\dOo]", text)
                if m:
                    text = text[m.start():]
                m2 = re.match(r"^\d{1,2}\s*[.,]\s*[\dOo]{1,2}(?:\s[o\d](?=\s))?\s*(.{1,60}?)\s*[:!;]\s?(.*)$", text)
                if m2:
                    title, body = m2.group(1), m2.group(2)
                else:
                    m3 = re.match(r"^\s*(?:\d{1,2}\s*[.,]\s*\d{1,2}\s+)*([A-Z][^:]{1,60}?)\s*[:!;]\s?(.*)$", text)
                    title, body = (m3.group(1), m3.group(2)) if m3 else ("", text)
                title = re.sub(r"^(\d{1,2}\s*[.,]\s*\d{1,2}\s+)+", "", title)
                title = re.sub(r"^[Oo0]\s+", "", title).replace("\ufffd", "e").strip()
                body = re.sub(rf"\s\b{ch}\s*\.\s*\d{{1,2}}\s(?=\s*{ch}\s*\.|\s*[A-Z])", " ", body)
                out.append({"qid": f"{ch}.{n}", "ch": ch, "title": title, "q_page": cur[0][0] if cur else ch_pages[c],
                            "question": body.strip(), "book_hints_raw": ln.strip()})
                cur = []
            else:
                cur.append((i, ln))
    return out


def _title_in(nt: str, pre: str) -> bool:
    if not nt:
        return False
    if nt in pre:
        return True
    w = len(nt)
    sm = difflib.SequenceMatcher(None, nt, "", autojunk=False)
    for s in range(0, max(1, len(pre) - w + 1)):
        cand = pre[s:s + w + 1]
        if cand[:1] != nt[:1] and cand[1:2] != nt[1:2]:
            continue
        sm.set_seq2(cand)
        if sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85:
            return True
    return False


def find_solutions(pages: list[str], sol_pages: list[int], questions: list[dict[str, Any]]) -> list[str]:
    """sol_pages = [1장 풀이 시작, …, 17장 풀이 시작, 풀이 끝]. 질문마다 sol_page·sol_end·solution 을 채움. 짝 없는 qid 목록을 돌려줌."""
    missing = []
    for c in range(len(sol_pages) - 1):
        ch = c + 1
        a, b = sol_pages[c], sol_pages[c + 1]
        text, starts = "", []
        for i in range(a, min(b, len(pages))):
            starts.append((len(text), i))
            text += (pages[i] or "") + "\n"

        def page_at(off: int) -> int:
            p = a
            for s, i in starts:
                if s <= off:
                    p = i
            return p

        marks = [m.start() for m in SOL_RE.finditer(text)]
        qs = [q for q in questions if q["ch"] == ch]
        j = 0
        found: list[tuple[dict[str, Any], int]] = []
        for q in qs:
            nt = norm(q["title"])
            num = re.compile(rf"\b{ch}\s*[.,]\s*{q['qid'].split('.')[1]}\b")
            best = None
            for mi in range(j, len(marks)):
                raw_pre = text[max(0, marks[mi] - 1500):marks[mi]]
                if _title_in(nt, norm(raw_pre)) or (num.search(raw_pre[-700:]) and mi == j):
                    best = mi
                    break
            if best is None:
                missing.append(q["qid"])
                continue
            j = best + 1
            found.append((q, marks[best]))
        for k, (q, off) in enumerate(found):
            end = len(text)
            if k + 1 < len(found):
                nxt = found[k + 1][1]
                window = text[max(off, nxt - 1500):nxt]
                hs = list(re.finditer(rf"\n\s*{ch}\s*[.,]\s*[\dOo]{{1,2}}\s+\S", window))
                end = (max(off, nxt - 1500) + hs[-1].start()) if hs else max(off, nxt - 600)
            q["sol_page"] = page_at(off)
            q["sol_end"] = page_at(max(off, end - 1))
            q["solution"] = text[off:end].strip()
    return missing


def verify(questions: list[dict[str, Any]], missing: list[str]) -> list[str]:
    errs = []
    for ch, n in enumerate(EXPECTED, 1):
        got = sum(1 for q in questions if q["ch"] == ch)
        if got != n:
            errs.append(f"{ch}장: {got}개 (책 {n}개)")
    errs += [f"{qid}: 풀이 짝 없음" for qid in missing]
    return errs


def apply_overrides(questions: list[dict[str, Any]], over: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by = {q["qid"]: q for q in questions}
    for qid, o in (over or {}).items():
        if qid in by and isinstance(o, dict):
            by[qid].update({k: v for k, v in o.items() if k in ("title", "q_page", "sol_page", "sol_end", "question", "solution")})
    return questions


def index_rows(questions: list[dict[str, Any]], part1_pages: int) -> list[dict[str, Any]]:
    def part(p: int | None) -> tuple[int | None, int | None]:
        if p is None:
            return None, None
        return (1, p + 1) if p < part1_pages else (2, p - part1_pages + 1)

    out = []
    for q in questions:
        qp, qn = part(q.get("q_page"))
        sp, sn = part(q.get("sol_page"))
        _ep, en = part(q.get("sol_end"))
        out.append({"qid": q["qid"], "ch": q["ch"], "ch_title": CH_TITLES.get(q["ch"], ""), "title": q["title"], "q_part": qp, "q_page": qn,
                    "sol_part": sp, "sol_page": sn, "sol_end": en, "kind": "code" if q["ch"] in CODE_CH else "explain"})
    return out


CH_TITLES = {1: "Arrays and Strings", 2: "Linked Lists", 3: "Stacks and Queues", 4: "Trees and Graphs", 5: "Bit Manipulation",
             6: "Math and Logic Puzzles", 7: "Object-Oriented Design", 8: "Recursion and Dynamic Programming", 9: "System Design and Scalability",
             10: "Sorting and Searching", 11: "Testing", 12: "C and C++", 13: "Java", 14: "Databases", 15: "Threads and Locks", 16: "Moderate",
             17: "Hard"}


# ── 책 자료 읽기 ──
def ctci_dir(jp: Any) -> Path:
    return jp.store.root / "ctci"


def load_index(jp: Any) -> list[dict[str, Any]]:
    cache = getattr(jp, "_ctci_index", None)
    p = ctci_dir(jp) / "index.json"
    try:
        mt = p.stat().st_mtime
    except OSError:
        return []
    if cache and cache[0] == mt:
        return cache[1]
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = [r for r in rows if isinstance(r, dict) and QID_RE.match(str(r.get("qid") or ""))]
    jp._ctci_index = (mt, rows)
    return rows


def load_text(jp: Any, qid: str) -> dict[str, Any]:
    if not QID_RE.match(qid or ""):
        return {}
    try:
        return json.loads((ctci_dir(jp) / "text" / f"{qid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def qsort(qid: str) -> tuple[int, int]:
    m = QID_RE.match(qid)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)


# ── 진도·복습·몫 ──
def today(jp: Any) -> date:
    return datetime.fromtimestamp(jp.clock(), KST).date()


def progress_map(jp: Any) -> dict[str, dict[str, Any]]:
    return {r["qid"]: r for r in jp.store.q("SELECT * FROM ctci_progress")}


def schedule_review(jp: Any, qid: str, *, reset: bool) -> None:
    r = jp.store.one("SELECT n FROM reviews WHERE qid=?", (qid,))
    n = 0 if (reset or r is None) else int(r["n"])
    if n >= len(INTERVALS):
        jp.store.x("DELETE FROM reviews WHERE qid=?", (qid,))
        return
    due = (today(jp) + timedelta(days=INTERVALS[n])).isoformat()
    jp.store.x("INSERT INTO reviews (qid, due, n) VALUES (?,?,?) ON CONFLICT(qid) DO UPDATE SET due=excluded.due, n=excluded.n", (qid, due, n + 1))


def mark_solved(jp: Any, qid: str) -> None:
    p = jp.store.one("SELECT * FROM ctci_progress WHERE qid=?", (qid,)) or {}
    helped = int(p.get("opened_hints") or 0) >= 3 or bool(p.get("saw_solution"))
    first = p.get("state") != "solved"
    jp.store.x("INSERT INTO ctci_progress (qid, state, solved_at, clean) VALUES (?, 'solved', ?, ?) ON CONFLICT(qid) DO UPDATE SET state='solved', "
               "solved_at=COALESCE(solved_at, excluded.solved_at), clean=excluded.clean",
               (qid, jp.clock(), 0 if (helped or int(p.get("opened_hints") or 0) > 0) else 1))
    if first:
        schedule_review(jp, qid, reset=True)
    else:
        schedule_review(jp, qid, reset=helped)


def quota(jp: Any) -> dict[str, Any]:
    idx = load_index(jp)
    prog = progress_map(jp)
    total = len(idx)
    solved = sum(1 for r in idx if (prog.get(r["qid"]) or {}).get("state") == "solved")
    remain = total - solved
    t = today(jp)
    try:
        finish = date.fromisoformat(jp.cfg().book_finish_by)
    except ValueError:
        finish = t + timedelta(days=280)
    weeks = max(1, math.ceil(max(1, (finish - t).days) / 7))
    per_week = math.ceil(remain / weeks) if remain else 0
    per_day = max(2, math.ceil(per_week / 7)) if remain else 0
    week_start = t - timedelta(days=t.weekday())
    ws = datetime(week_start.year, week_start.month, week_start.day, tzinfo=KST).timestamp()
    done_week = sum(1 for r in prog.values() if r.get("state") == "solved" and float(r.get("solved_at") or 0) >= ws)
    return {"total": total, "solved": solved, "remain": remain, "weeks": weeks, "per_week": per_week, "per_day": per_day,
            "week_done": done_week, "week_left": max(0, per_week - done_week)}


def next_new(jp: Any, n: int) -> list[dict[str, Any]]:
    idx = load_index(jp)
    prog = progress_map(jp)
    open_ = [r for r in idx if (prog.get(r["qid"]) or {}).get("state") != "solved"]
    code = sorted((r for r in open_ if r["ch"] in CODE_CH), key=lambda r: (ORDER.index(r["ch"]) if r["ch"] in ORDER else 99, qsort(r["qid"])[1]))
    explain = sorted((r for r in open_ if r["ch"] not in CODE_CH), key=lambda r: qsort(r["qid"]))
    t = today(jp)
    ws = datetime(*(t - timedelta(days=t.weekday())).timetuple()[:3], tzinfo=KST).timestamp()
    ex_done = sum(1 for qid, r in prog.items() if r.get("state") == "solved" and float(r.get("solved_at") or 0) >= ws
                  and qsort(qid)[0] not in CODE_CH)
    out = []
    if explain and ex_done < EXPLAIN_PER_WEEK and t.weekday() in (2, 5):  # 수·토에 설명 문제 하나씩
        out.append(explain[0])
    out += code[:max(0, n - len(out))]
    return out[:n]


def due_reviews(jp: Any) -> list[dict[str, Any]]:
    t = today(jp).isoformat()
    by = {r["qid"]: r for r in load_index(jp)}
    return [{**by[r["qid"]], "due": r["due"], "n": r["n"]} for r in jp.store.q("SELECT * FROM reviews WHERE due<=? ORDER BY due", (t,)) if r["qid"] in by]


def warmups_to_show(jp: Any, ch: int) -> int:
    rows = [r for r in jp.store.q("SELECT * FROM ctci_progress WHERE state='solved' ORDER BY solved_at DESC") if qsort(r["qid"])[0] == ch][:3]
    if len(rows) < 3:
        return 2
    if any(int(r.get("opened_hints") or 0) > 0 or r.get("saw_solution") for r in rows):
        return 3
    if all(int(r.get("clean") or 0) for r in rows):
        return 1
    return 2


# ── 사다리 ──
ITEM = {"type": "object", "properties": {"title": {"type": "string"}, "statement": {"type": "string"}, "fn": {"type": "string"},
        "compare": {"type": "string", "enum": ["exact", "unordered", "float", "any_of"]}, "codec": {"type": "string", "enum": ["none", "linked_list", "tree"]},
        "tests": {"type": "array", "items": {"type": "object", "properties": {"args": {"type": "array", "items": {}}, "expected": {}},
                                             "required": ["args", "expected"]}},
        "ref_solution": {"type": "string"}}, "required": ["title", "statement", "fn", "compare", "codec", "tests", "ref_solution"]}
LADDER_SCHEMA = {"type": "object", "properties": {
    "concept": {"type": "string"}, "warmups": {"type": "array", "items": ITEM}, "hints": {"type": "array", "items": {"type": "string"}},
    "twist": {"type": "string"}, "book": ITEM, "leetcode": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "url": {"type": "string"}}, "required": ["name", "url"]}}},
    "required": ["concept", "warmups", "hints", "twist", "book", "leetcode"]}
EXPLAIN_SCHEMA = {"type": "object", "properties": {"concept": {"type": "string"}, "hints": {"type": "array", "items": {"type": "string"}},
                  "twist": {"type": "string"}, "rubric": {"type": "array", "items": {"type": "string"}}}, "required": ["concept", "hints", "twist", "rubric"]}
LADDER_SYSTEM = ("너는 코딩 면접 튜터다. 학생은 이 주제를 처음 본다고 생각하고 한국어로 아주 자세히 설명한다(비유·글자 그림(ASCII)·작은 예를 손으로 따라가기). "
                 "코드는 파이썬. 시험은 함수 인자(args 배열)와 기대값(expected)만 적는다 — 연결 리스트는 리스트로, 트리는 레벨 순서 리스트(None 포함)로 적고 codec 을 맞춘다.")


def ladder_prompt(q: dict[str, Any], text: dict[str, Any]) -> str:
    return (f"CtCI {q['qid']} {q['title']} ({q.get('ch_title', '')})\n문제:\n{(text.get('question') or '')[:2500]}\n\n"
            "만들 것: concept(개념 설명) · warmups 2~3개(중급부터, 각 title·statement·fn·compare·codec·tests 6~10개·ref_solution) · "
            "hints 3개(방향 → 자료구조 → 거의 풀이) · twist(한 단계 더) · book(이 책 문제를 함수로: fn·compare·codec·tests 12~20개(작은 것·경계·큰 것)·"
            "ref_solution) · leetcode 2~3개(비슷한 문제 이름·https://leetcode.com/problems/… 주소).")


async def _valid_tests(jp: Any, item: dict[str, Any]) -> list[dict[str, Any]]:
    tests = {"fn": item.get("fn") or "", "cases": [c for c in (item.get("tests") or []) if isinstance(c, dict) and isinstance(c.get("args"), list)][:30],
             "compare": item.get("compare") or "exact", "codec": item.get("codec") or "none"}
    if not tests["cases"] or not tests["fn"]:
        return []
    res = await jp.sandbox.run(str(item.get("ref_solution") or ""), tests)
    return [c for c, r in zip(tests["cases"], res.get("cases") or [], strict=False) if r.get("ok")]


async def make_ladder(jp: Any, qid: str) -> dict[str, Any]:
    by = {r["qid"]: r for r in load_index(jp)}
    q = by.get(qid)
    if q is None:
        raise JpError(404, "no_q", "그 문제가 목록에 없어요")
    text = load_text(jp, qid)
    if q["kind"] != "code":
        out = await jp.llm.gemini(f"ctci.ladder:{qid}:v{LADDER_VER}", "good", LADDER_SYSTEM,
                                  f"CtCI {qid} {q['title']}\n문제:\n{(text.get('question') or '')[:2500]}\n\n설명 문제다. concept·hints 3개·twist·"
                                  "rubric(좋은 답에 꼭 들어갈 요점 4~6개)을 만들어 줘.", schema=EXPLAIN_SCHEMA, ver=LADDER_VER, thinking=LADDER_THINKING, max_tokens=8000)
        lad = {"kind": "explain", **out}
        jp.store.kv_set(f"ladder:{qid}", lad)
        return lad
    best: dict[str, Any] | None = None
    for attempt in range(2):
        raw = await jp.llm.gemini(f"ctci.ladder:{qid}:v{LADDER_VER}", "good", LADDER_SYSTEM, ladder_prompt(q, text), schema=LADDER_SCHEMA,
                                  ver=LADDER_VER + attempt * 100, max_tokens=16000, thinking=LADDER_THINKING)
        book = dict(raw.get("book") or {})
        book["tests"] = await _valid_tests(jp, book)
        warm = []
        for w in (raw.get("warmups") or [])[:3]:
            w = dict(w)
            w["tests"] = await _valid_tests(jp, w)
            if len(w["tests"]) >= 3:
                warm.append(w)
        lad = {"kind": "code", "concept": raw.get("concept") or "", "warmups": warm, "hints": (raw.get("hints") or [])[:3],
               "twist": raw.get("twist") or "", "book": book, "leetcode": [x for x in (raw.get("leetcode") or [])
                                                                             if str(x.get("url") or "").startswith("https://leetcode.com/")][:3],
               "tests_ready": len(book["tests"]) >= MIN_TESTS}
        if best is None or len(book["tests"]) > len(best["book"]["tests"]):
            best = lad
        if lad["tests_ready"]:
            break
    assert best is not None
    jp.store.kv_set(f"ladder:{qid}", best)
    return best


def ladder(jp: Any, qid: str) -> dict[str, Any] | None:
    return jp.store.kv_get(f"ladder:{qid}")


def ladder_err(jp: Any, qid: str) -> dict[str, Any] | None:
    """최근(LADDER_RETRY_S 안) 만들기 실패 {at, why} — 그동안은 다시 부르지 않음."""
    e = jp.store.kv_get(f"ladder_err:{qid}")
    return e if isinstance(e, dict) and jp.clock() - float(e.get("at") or 0) < LADDER_RETRY_S else None


def start_ladder(jp: Any, qid: str) -> bool:
    """뒤에서 만들기 (도는 중이거나 1시간 안에 실패했으면 그대로). True = 시작함."""
    busy = jp.bg.get(f"ladder:{qid}")
    if (busy is not None and not busy.done()) or ladder_err(jp, qid):
        return False

    async def go() -> None:
        try:
            await make_ladder(jp, qid)
        except Exception as e:  # noqa: BLE001
            jp.store.kv_set(f"ladder_err:{qid}", {"at": jp.clock(), "why": getattr(e, "text", type(e).__name__)})
            log.info("jobprep ladder %s: %s", qid, type(e).__name__)

    jp.bg[f"ladder:{qid}"] = asyncio.ensure_future(go())
    return True


async def pregen(jp: Any, n: int = 3) -> int:
    made = 0
    for r in next_new(jp, n):
        if ladder(jp, r["qid"]) is None and not ladder_err(jp, r["qid"]):
            try:
                await make_ladder(jp, r["qid"])
                made += 1
            except Exception as e:  # noqa: BLE001
                jp.store.kv_set(f"ladder_err:{r['qid']}", {"at": jp.clock(), "why": getattr(e, "text", type(e).__name__)})
    return made


# ── 화면 ──
def public_item(item: dict[str, Any], *, with_tests: bool = True) -> dict[str, Any]:
    tests = item.get("tests") or []
    return {"title": item.get("title") or "", "statement": item.get("statement") or "", "fn": item.get("fn") or "",
            "examples": tests[:2] if with_tests else [], "n_tests": len(tests)}


def today_view(jp: Any) -> dict[str, Any]:
    idx = load_index(jp)
    if not idx:
        return {"ready": False, "text": "CtCI 목록을 아직 만들지 않았어요 (tools/jobprep_ctci_extract.py 한 번)"}
    qt = quota(jp)
    prog = progress_map(jp)
    chapters = []
    for ch in range(1, 18):
        rows = [r for r in idx if r["ch"] == ch]
        chapters.append({"ch": ch, "title": CH_TITLES[ch], "n": len(rows), "solved": sum(1 for r in rows if (prog.get(r["qid"]) or {}).get("state") == "solved"),
                         "kind": "code" if ch in CODE_CH else "explain"})
    new = [{**r, "ladder": ladder(jp, r["qid"]) is not None} for r in next_new(jp, max(2, qt["per_day"]))]
    return {"ready": True, "quota": qt, "new": new, "reviews": due_reviews(jp), "chapters": chapters,
            "service_mode": bool(jp.store.kv_get("service_mode", True)), "sandbox": {"mode": jp.sandbox.mode(), "strong": jp.sandbox.strong()}}


def q_view(jp: Any, qid: str) -> dict[str, Any]:
    by = {r["qid"]: r for r in load_index(jp)}
    q = by.get(qid)
    if q is None:
        raise JpError(404, "no_q", "그 문제가 목록에 없어요")
    text = load_text(jp, qid)
    lad = ladder(jp, qid)
    prog = jp.store.one("SELECT * FROM ctci_progress WHERE qid=?", (qid,)) or {}
    if lad is None:
        started = start_ladder(jp, qid)
        err = ladder_err(jp, qid)
        return {"q": q, "question": text.get("question") or "", "making": True, "started": started,
                "err": (err["why"] + " · 1시간 뒤 다시 만들어요") if err else ""}
    shown = prog.get("warmups_shown")
    if shown is None:
        shown = warmups_to_show(jp, q["ch"])
        jp.store.x("INSERT INTO ctci_progress (qid, state, warmups_shown) VALUES (?, 'open', ?) ON CONFLICT(qid) DO UPDATE SET warmups_shown=excluded.warmups_shown",
                   (qid, shown))
    opened = int(prog.get("opened_hints") or 0)
    attempts = jp.store.q("SELECT id, item, passed, total, at FROM attempts WHERE qid=? ORDER BY at DESC LIMIT 10", (qid,))
    out: dict[str, Any] = {"q": q, "question": text.get("question") or "", "making": False, "concept": lad.get("concept") or "",
                           "hints": (lad.get("hints") or [])[:opened], "n_hints": len(lad.get("hints") or []), "twist": lad.get("twist") or "",
                           "state": prog.get("state") or "open", "saw_solution": bool(prog.get("saw_solution")), "attempts": attempts,
                           "leetcode": lad.get("leetcode") or [], "kind": lad.get("kind")}
    if lad.get("kind") == "code":
        out["warmups"] = [public_item(w) for w in lad.get("warmups") or []]
        out["warmups_shown"] = int(shown)
        out["book"] = public_item(lad.get("book") or {})
        out["tests_ready"] = bool(lad.get("tests_ready"))
    else:
        out["rubric_n"] = len(lad.get("rubric") or [])
    if prog.get("saw_solution"):
        out["solution"] = text.get("solution") or ""
        out["ref_solution"] = (lad.get("book") or {}).get("ref_solution") or ""
    return out


def step(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    qid, st = str(body.get("qid") or ""), body.get("step")
    lad = ladder(jp, qid)
    if lad is None:
        raise JpError(409, "making", "아직 만드는 중이에요")
    jp.store.x("INSERT OR IGNORE INTO ctci_progress (qid, state) VALUES (?, 'open')", (qid,))
    if st == "hint":
        p = jp.store.one("SELECT opened_hints FROM ctci_progress WHERE qid=?", (qid,)) or {}
        n = min(len(lad.get("hints") or []), int(p.get("opened_hints") or 0) + 1)
        jp.store.x("UPDATE ctci_progress SET opened_hints=? WHERE qid=?", (n, qid))
        return {"hints": (lad.get("hints") or [])[:n]}
    if st == "solution":
        jp.store.x("UPDATE ctci_progress SET saw_solution=1 WHERE qid=?", (qid,))
        text = load_text(jp, qid)
        return {"solution": text.get("solution") or "", "ref_solution": (lad.get("book") or {}).get("ref_solution") or ""}
    raise JpError(400, "bad", "모르는 단계예요")


def _item(lad: dict[str, Any], item: str) -> dict[str, Any] | None:
    if item == "book":
        return lad.get("book")
    m = re.fullmatch(r"w(\d)", item or "")
    ws = lad.get("warmups") or []
    return ws[int(m.group(1))] if m and int(m.group(1)) < len(ws) else None


def run_gate(jp: Any) -> None:
    """코드 돌리기 상한: 1분 6번 · 하루 300번 · 여유 메모리 min_free_mb(400MB) 아래면 거절."""
    from dealbot.dashboard.jobprep import store as storemod

    now = jp.clock()
    recent = [t for t in getattr(jp, "_run_times", []) if now - t < 60]
    if len(recent) >= RUN_PER_MIN:
        raise JpError(429, "rate", f"채점은 1분에 {RUN_PER_MIN}번까지예요 — 잠깐 뒤 다시")
    day = datetime.fromtimestamp(now, KST).strftime("%Y-%m-%d")
    r = jp.store.one("SELECT n FROM usage WHERE day=? AND kind='sandbox'", (day,))
    if r and int(r["n"]) >= RUN_PER_DAY:
        raise JpError(429, "rate", f"오늘 채점 {RUN_PER_DAY}번을 다 썼어요 — 내일 다시")
    if storemod.mem_free_mb() < int(jp.cfg().min_free_mb):
        raise JpError(503, "low_mem", "서버 메모리가 모자라요 — 잠깐 뒤 다시")
    jp._run_times = [*recent, now]
    jp.store.x("INSERT INTO usage (day, kind, n, usd) VALUES (?, 'sandbox', 1, 0) ON CONFLICT(day, kind) DO UPDATE SET n=n+1", (day,))


async def run(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    qid, item, code = str(body.get("qid") or ""), str(body.get("item") or "book"), body.get("code")
    if not isinstance(code, str) or not code.strip():
        raise JpError(400, "empty", "코드를 넣어 주세요")
    lad = ladder(jp, qid)
    it = _item(lad or {}, item)
    if it is None or not it.get("tests"):
        raise JpError(409, "no_tests", "시험이 준비되지 않았어요 — 자기 채점으로 해 주세요")
    tests = {"fn": it["fn"], "cases": it["tests"], "compare": it.get("compare") or "exact", "codec": it.get("codec") or "none"}
    run_gate(jp)
    res = await jp.sandbox.run(code, tests)
    fail = None
    for c, r in zip(it["tests"], res.get("cases") or [], strict=False):
        if not r.get("ok"):
            fail = {"args": json.dumps(c["args"], ensure_ascii=False)[:300], "expected": json.dumps(c["expected"], ensure_ascii=False)[:200],
                    "got": r.get("got"), "err": r.get("err")}
            break
    if res.get("error") and fail is None:
        fail = {"args": "", "expected": "", "got": "", "err": res["error"]}
    aid = jp.store.x("INSERT INTO attempts (qid, item, code, passed, total, fail_case, at) VALUES (?,?,?,?,?,?,?)",
                     (qid, item, code[:50000], res["passed"], res["total"], json.dumps(fail, ensure_ascii=False) if fail else "", jp.clock()))
    passed_all = res["total"] > 0 and res["passed"] == res["total"]
    if passed_all and item == "book":
        mark_solved(jp, qid)
    return {"attempt_id": aid, "passed": res["passed"], "total": res["total"], "fail": fail, "stdout": res.get("stdout") or "",
            "solved": passed_all and item == "book", "mode": res.get("mode")}


FEEDBACK_SYSTEM = "너는 친절한 코딩 튜터다. 한국어로 짧게. 정답 코드나 풀이 전체는 말하지 않는다."


async def feedback(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    a = jp.store.one("SELECT * FROM attempts WHERE id=?", (body.get("attempt_id"),)) if isinstance(body.get("attempt_id"), int) else None
    if a is None:
        raise JpError(404, "gone", "그 채점 기록이 없어요")
    by = {r["qid"]: r for r in load_index(jp)}
    q = by.get(a["qid"], {})
    text = load_text(jp, a["qid"])
    ok = a["passed"] == a["total"]
    ask = ("통과했다. 시간/공간 복잡도와 더 나은 길만 3줄로." if ok else
           "실패했다. 왜 틀렸나(실패 입력·기대값·내 값) · 시간/공간 복잡도 · 고칠 점 · 다음 힌트 한 줄 (풀이는 말하지 않음).")
    reply = await jp.llm.gemini("code.feedback", "fast", FEEDBACK_SYSTEM,
                                f"문제 {a['qid']} {q.get('title', '')}: {(text.get('question') or '')[:1200]}\n내 코드:\n{a['code'][:6000]}\n"
                                f"결과: {a['passed']}/{a['total']} · 실패한 경우: {a['fail_case'] or '-'}\n{ask}")
    jp.store.x("INSERT INTO chat (tab, ctx, role, model, text, at) VALUES ('code', ?, 'assistant', 'gemini', ?, ?)", (a["qid"], reply, jp.clock()))
    return {"text": reply}


async def compare(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    qid = str(body.get("qid") or "")
    text = load_text(jp, qid)
    if not text.get("solution"):
        raise JpError(404, "no_sol", "책 풀이 글이 없어요")
    last = jp.store.one("SELECT code FROM attempts WHERE qid=? AND item='book' ORDER BY at DESC LIMIT 1", (qid,))
    reply = await jp.llm.gemini(f"code.compare:{qid}", "good", FEEDBACK_SYSTEM.replace("정답 코드나 풀이 전체는 말하지 않는다.", "책 풀이와 비교해 준다."),
                                f"책 풀이 요점:\n{text['solution'][:8000]}\n\n내 코드:\n{(last or {}).get('code', '(없음)')[:6000]}\n"
                                "책 풀이의 핵심 생각·복잡도와 내 코드의 차이를 한국어로 5줄 안에.")
    return {"text": reply}


async def self_grade(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    qid, result = str(body.get("qid") or ""), body.get("result")
    if result not in ("solved", "stuck", "explain"):
        raise JpError(400, "bad", "결과를 골라 주세요")
    jp.store.x("INSERT OR IGNORE INTO ctci_progress (qid, state) VALUES (?, 'open')", (qid,))
    if result == "solved":
        mark_solved(jp, qid)
        return {"ok": True}
    if result == "stuck":
        jp.store.x("UPDATE ctci_progress SET state='stuck' WHERE qid=? AND state<>'solved'", (qid,))
        return {"ok": True}
    answer = clip(body.get("text"), 6000)
    lad = ladder(jp, qid) or {}
    text = load_text(jp, qid)
    reply = await jp.llm.gemini("code.explain", "good", FEEDBACK_SYSTEM,
                                f"설명 문제 {qid}. 요점: {json.dumps(lad.get('rubric') or [], ensure_ascii=False)}\n책 풀이 요점:\n{(text.get('solution') or '')[:5000]}\n"
                                f"내 답:\n{answer}\n빠진 요점·좋은 점·고칠 점을 한국어로 짧게.")
    return {"text": reply}


def set_mode(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    jp.store.kv_set("service_mode", bool(body.get("service")))
    return {"service_mode": bool(body.get("service"))}
