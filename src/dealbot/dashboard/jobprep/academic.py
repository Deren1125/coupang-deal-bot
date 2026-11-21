"""② 학사·졸업 — 성적표·졸업 자가진단 화면 올리기 → Gemini good 이 읽음 → **주인이 고치고 '맞아요' 해야 저장** → 남은 학점 막대 ·
3학기 수강 계획 · 재수강 평점 계산 (화면 JS 와 같은 셈 — retake_gpa). 졸업 기준은 주인이 폼에서 확정 (전과생 기준이 달라서).
요람: 포털 로그인 절대 없음 — 공개 PDF 주소를 주인이 넣을 때만 (find_yoram 은 지금은 안내만).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from dealbot.dashboard.jobprep import profile
from dealbot.dashboard.jobprep.store import JpError, clip

AREAS = ("교양", "전필", "전선", "기타")
GRADES = {"A+": 4.3, "A0": 4.0, "A": 4.0, "A-": 3.7, "B+": 3.3, "B0": 3.0, "B": 3.0, "B-": 2.7, "C+": 2.3, "C0": 2.0, "C": 2.0, "C-": 1.7,
          "D+": 1.3, "D0": 1.0, "D": 1.0, "D-": 0.7, "F": 0.0}
TERMS = ("2027-2", "2028-1", "2028-2")
TERM_MAX = 18
PARSE_SCHEMA = {"type": "object", "properties": {
    "courses": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "credits": {"type": "number"},
                "grade": {"type": "string"}, "area": {"type": "string", "enum": list(AREAS)}, "term": {"type": "string"}, "retake": {"type": "boolean"}},
                "required": ["name", "credits", "grade", "area", "term", "retake"]}},
    "totals": {"type": "object", "properties": {a: {"type": "number"} for a in (*AREAS, "총")}},
    "remaining": {"type": "object", "properties": {a: {"type": "number"} for a in (*AREAS, "총")}}}, "required": ["courses"]}
PLAN_SCHEMA = {"type": "object", "properties": {"terms": {"type": "array", "items": {"type": "object", "properties": {
    "term": {"type": "string"}, "courses": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"}, "credits": {"type": "number"}, "area": {"type": "string"}, "why": {"type": "string"}},
        "required": ["name", "credits", "area", "why"]}}}, "required": ["term", "courses"]}}}, "required": ["terms"]}


def gpa(courses: list[dict[str, Any]]) -> float | None:
    pts = cr = 0.0
    for c in courses:
        g = GRADES.get(str(c.get("grade") or "").strip().upper().replace("O", "0"))
        if g is None:
            continue
        pts += g * float(c.get("credits") or 0)
        cr += float(c.get("credits") or 0)
    return round(pts / cr, 2) if cr else None


def retake_gpa(courses: list[dict[str, Any]], changes: dict[str, str], max_grade: str = "A0") -> dict[str, Any]:
    """changes = {과목 id(문자): 기대 성적}. 재수강 성적은 max_grade 를 넘지 않음. → {now, new, now45, new45}."""
    cap = GRADES.get(max_grade, 4.0)
    new = []
    for c in courses:
        cid = str(c.get("id"))
        if cid in changes and changes[cid] in GRADES:
            g = min(GRADES[changes[cid]], cap)
            grade = next(k for k, v in GRADES.items() if v == g)
            new.append({**c, "grade": grade})
        else:
            new.append(c)
    a, b = gpa(courses), gpa(new)
    return {"now": a, "new": b, "now45": round(a * 4.5 / 4.3, 2) if a is not None else None, "new45": round(b * 4.5 / 4.3, 2) if b is not None else None}


def view(jp: Any) -> dict[str, Any]:
    courses = jp.store.q("SELECT * FROM acad_courses ORDER BY term, id")
    req_r = jp.store.one("SELECT body FROM acad_req WHERE id=1")
    req = json.loads(req_r["body"]) if req_r else {}
    done = {a: 0.0 for a in AREAS}
    for c in courses:
        if GRADES.get(str(c["grade"]).upper()) != 0.0:
            done[c["area"] if c["area"] in AREAS else "기타"] += float(c["credits"] or 0)
    total_done = sum(done.values())
    bars = []
    for a in (*AREAS, "총"):
        need = float(req.get(a) or 0)
        got = total_done if a == "총" else done.get(a, 0.0)
        bars.append({"area": a, "done": got, "need": need, "left": max(0.0, need - got) if need else None})
    plans = {r["term"]: json.loads(r["body"]) for r in jp.store.q("SELECT * FROM acad_plan")}
    return {"courses": courses, "gpa": gpa(courses), "bars": bars, "req": req, "plans": plans, "terms": TERMS, "term_max": int(req.get("term_max") or TERM_MAX),
            "retake_max": jp.cfg().retake_max_grade, "grades": GRADES, "docs": jp.store.q("SELECT id, at FROM acad_docs ORDER BY at DESC LIMIT 20")}


def add_doc(jp: Any, name: str) -> int:
    return jp.store.x("INSERT INTO acad_docs (file, at, parsed) VALUES (?,?, '')", (name, time.time()))


async def parse(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    ids = [i for i in (body.get("doc_ids") or []) if isinstance(i, int)][:6]
    imgs = []
    for i in ids:
        r = jp.store.one("SELECT file FROM acad_docs WHERE id=?", (i,))
        data = jp.store.read("acad", r["file"]) if r else None
        if data:
            imgs.append((data, "image/jpeg"))
    if not imgs:
        raise JpError(400, "no_doc", "읽을 그림을 먼저 올려 주세요")
    out = await jp.llm.gemini("acad.parse", "good", "너는 대학 성적표·졸업 자가진단 화면을 읽어 표로 옮긴다. 화면에 있는 것만 적고, 안 보이면 비운다.",
                              "그림의 과목(이름·학점·성적·영역(교양|전필|전선|기타)·학기·재수강 여부)과 영역별 이수 합계·남은 학점을 JSON 으로.",
                              schema=PARSE_SCHEMA, images=imgs)
    return {"draft": out, "note": "확인 전에는 저장하지 않아요 — 고친 뒤 '맞아요'"}


def save_courses(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    rows = body.get("courses")
    if not isinstance(rows, list) or len(rows) > 200:
        raise JpError(400, "bad", "과목 목록이 없어요")
    clean = []
    for c in rows:
        if not isinstance(c, dict) or not clip(c.get("name"), 80):
            continue
        try:
            cr = float(c.get("credits") or 0)
        except (TypeError, ValueError):
            cr = 0.0
        clean.append((clip(c.get("name"), 80), max(0.0, min(cr, 10.0)), clip(c.get("grade"), 4).upper(),
                      c.get("area") if c.get("area") in AREAS else "기타", clip(c.get("term"), 12), 1 if c.get("retake") else 0))
    jp.store.x("DELETE FROM acad_courses")
    for row in clean:
        jp.store.x("INSERT INTO acad_courses (name, credits, grade, area, term, retake) VALUES (?,?,?,?,?,?)", row)
    return view(jp)


def save_req(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    req = {}
    for k in (*AREAS, "총", "term_max"):
        v = body.get(k)
        if isinstance(v, (int, float)) and 0 <= v <= 300:
            req[k] = v
    jp.store.x("INSERT INTO acad_req (id, body, at) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body, at=excluded.at",
               (json.dumps(req), time.time()))
    return view(jp)


def check_plan(terms: dict[str, list[dict[str, Any]]], term_max: int) -> list[str]:
    errs = []
    for t, cs in terms.items():
        if t not in TERMS:
            errs.append(f"{t}: 계획 칸이 아니에요 (겨울 계절학기는 넣지 않음 — 겨울 인턴과 겹침)")
            continue
        s = sum(float(c.get("credits") or 0) for c in cs)
        if s > term_max:
            errs.append(f"{t}: {s:g}학점 — 학기 최대 {term_max}학점 넘음")
    if "2028-2" in terms and sum(float(c.get("credits") or 0) for c in terms["2028-2"]) > term_max - 3:
        errs.append("2028-2(4-2)는 가볍게 — 취업 준비 학기")
    return errs


async def plan(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    v = view(jp)
    if body.get("action") == "suggest":
        prof, _ = profile.get(jp.store)
        out = await jp.llm.gemini("acad.plan", "good", "너는 대학 수강 계획 도우미다. 남은 학점을 채우되 전필을 먼저, 4-2 는 가볍게. 과목 이름은 일반 이름으로.",
                                  f"학생: {profile.summary(prof)}\n남은 학점: {json.dumps(v['bars'], ensure_ascii=False)}\n들은 과목: "
                                  f"{', '.join(c['name'] for c in v['courses'])[:3000]}\n학기: {', '.join(TERMS)} (겨울 계절학기 없음 · 여름 계절은 선택). "
                                  f"학기 최대 {v['term_max']}학점.", schema=PLAN_SCHEMA)
        terms = {t["term"]: t["courses"] for t in out.get("terms") or [] if isinstance(t, dict)}
        return {"suggest": terms, "errors": check_plan(terms, v["term_max"])}
    terms = body.get("terms")
    if not isinstance(terms, dict):
        raise JpError(400, "bad", "계획이 없어요")
    terms = {str(k): [c for c in (cs or []) if isinstance(c, dict)][:12] for k, cs in terms.items()}
    errs = check_plan(terms, v["term_max"])
    if errs and not body.get("force"):
        return {"errors": errs, "saved": False}
    for t, cs in terms.items():
        if t in TERMS:
            jp.store.x("INSERT OR REPLACE INTO acad_plan (term, body, at) VALUES (?,?,?)", (t, json.dumps(cs, ensure_ascii=False), time.time()))
    return {"errors": errs, "saved": True, **view(jp)}


def find_yoram(jp: Any) -> dict[str, Any]:
    """요람 PDF: 학교 공개 페이지 주소가 정해지면 받음 — 지금은 안내만 (포털·로그인 없음)."""
    return {"skip": "요람은 공개 PDF 주소가 정해지면 받아요 — 졸업 기준은 '졸업 기준' 폼에 직접 넣어 주세요"}


def valid_grade(g: str) -> bool:
    return bool(re.fullmatch(r"[A-DF][+0-]?", g or ""))
