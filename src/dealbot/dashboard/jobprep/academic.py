"""② 학사·졸업 — 성적표·졸업 자가진단 화면 올리기 → Claude(good) 이 읽음 → **주인이 고치고 '맞아요' 해야 저장** → 남은 학점 막대 ·
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
NOT_EARNED = {"W", "F", "NP", "U", "I", ""}  # 학점으로 안 침 (철회·낙제·비통과·미완)
RETAKE_SRC = "https://www.yonsei.ac.kr/sc/279/subview.do"
RETAKE_NOTE = "졸업까지 6회 · 최고 A0 · 모든 성적 가능(13학번~) · 이전 성적 소거"
B0, BPLUS = 3.0, 3.3
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


def earned(c: dict[str, Any]) -> bool:
    return str(c.get("grade") or "").strip().upper() not in NOT_EARNED and float(c.get("credits") or 0) > 0


def candidates(courses: list[dict[str, Any]], *, max_grade: str = "A0", upto: float = B0) -> list[dict[str, Any]]:
    """재수강 후보 — 성적이 upto(B0, 단추로 B+) 이하이고 A0 보다 낮은 것, 평점 이득(학점 × (A0 − 지금)) 큰 순."""
    cap = GRADES.get(max_grade, 4.0)
    out = []
    for c in courses:
        if int(c.get("no_retake") or 0) or status_of(c) != "done":
            continue
        g = GRADES.get(str(c.get("grade") or "").strip().upper())
        if g is None or g >= cap or g > upto:
            continue
        cr = float(c.get("credits") or 0)
        out.append({"id": c.get("id"), "name": c.get("name"), "grade": c.get("grade"), "credits": cr, "gain": round(cr * (cap - g), 2)})
    out.sort(key=lambda x: (-x["gain"], str(x["name"])))
    return out


def retake_plan(courses: list[dict[str, Any]], *, cfg: Any, real_left: float | None, term_max: int) -> dict[str, Any]:
    used = sum(1 for c in courses if int(c.get("retake") or 0))
    total = int(cfg.retake_total)
    left = max(0, total - used)
    out: dict[str, Any] = {"total": total, "used": used, "left": left, "max": cfg.retake_max_grade, "src": RETAKE_SRC, "note": RETAKE_NOTE,
                           "now": gpa(courses)}
    for key, upto in (("b0", B0), ("bplus", BPLUS)):
        cands = candidates(courses, max_grade=cfg.retake_max_grade, upto=upto)
        top = cands[:left]
        after = retake_gpa(courses, {str(c["id"]): cfg.retake_max_grade for c in top}, cfg.retake_max_grade)
        rest = real_left
        extra = sum(c["credits"] for c in top)
        per = round((rest + extra) / len(TERMS), 1) if rest is not None else None
        out[key] = {"cands": cands, "top": [c["id"] for c in top], "after": after["new"], "after45": after["new45"],
                    "load": {"left_credits": rest, "retake_credits": extra, "terms": len(TERMS), "per_term": per,
                             "over": per is not None and per > term_max, "term_max": term_max}}
    return out


def gpa(courses: list[dict[str, Any]]) -> float | None:
    pts = cr = 0.0
    for c in courses:
        g = GRADES.get(str(c.get("grade") or "").strip().upper().replace("O", "0"))
        if g is None or int(c.get("military") or 0) or status_of(c) != "done":  # 군강좌(P/NP)·수강 중·계획은 평점 밖
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


GE6 = ("문학과예술", "인간과역사", "언어와표현", "가치와윤리", "국가와사회", "지역과세계")
GE_PREFIX = {"YCD": "문학과예술", "YCE": "인간과역사", "YCG": "가치와윤리", "YCH": "국가와사회", "YCI": "지역과세계"}  # 학정번호 앞 세 글자 (언어와표현은 칸에 직접)
STATUSES = ("done", "in_progress", "planned")
CODE_RE = re.compile(r"^([A-Z]{3})(\d)(\d{3})\b")


def status_of(c: dict[str, Any]) -> str:
    st = str(c.get("status") or "done")
    return st if st in STATUSES else "done"


def code_of(c: dict[str, Any]) -> tuple[str, int] | None:
    m = CODE_RE.match(str(c.get("name") or "").strip())
    return (m.group(1) + m.group(2) + m.group(3), int(m.group(2))) if m else None


def ge_area_of(c: dict[str, Any]) -> str:
    if str(c.get("ge_area") or "") in GE6:
        return str(c["ge_area"])
    m = CODE_RE.match(str(c.get("name") or "").strip())
    return GE_PREFIX.get(m.group(1), "") if m else ""


def counts(c: dict[str, Any]) -> bool:
    """학점으로 칠 줄 — 끝난 과목은 통과 성적만, 수강 중·계획은 (P/NP·성적 전이라도) 예정으로."""
    if status_of(c) != "done":
        return float(c.get("credits") or 0) > 0
    return earned(c)


def area_of(c: dict[str, Any], cfg: Any) -> str:
    """졸업 칸 — 적힌 영역 + 주인 확인(11/23): EEE 과목은 전공(전필이 아니면 전선) · CEE 과목은 전선으로 안 침 (일반선택)."""
    a = c.get("area") if c.get("area") in AREAS else "기타"
    pre = ((code_of(c) or ("",))[0])[:3]
    if pre and pre in (cfg.not_major_prefixes or []) and a in ("전선", "전필"):
        return "기타"
    if pre and pre in (cfg.major_prefixes or []) and a not in ("전필", "전선"):
        return "전선"
    return a


def check(courses: list[dict[str, Any]], req: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """졸업 칸 · 3000/4000 · 교양 6영역 · 군강좌 · 진짜 남은 학점."""
    done = {a: 0.0 for a in AREAS}
    pend = {a: 0.0 for a in AREAS}
    for c in courses:
        if not counts(c):
            continue
        a = area_of(c, cfg)
        (done if status_of(c) == "done" else pend)[a] += float(c.get("credits") or 0)
    bars = []
    cat_left = 0.0
    overflow = 0.0
    for a in AREAS:  # 넘친 학점은 일반선택(기타)으로
        need = float(req.get(a) or 0)
        have = done[a] + pend[a]
        if a == "기타":
            have += overflow
        elif need and have > need:
            overflow += have - need
        left = max(0.0, need - have) if need else None
        cat_left += left or 0.0
        bars.append({"area": a, "done": done[a], "pending": pend[a], "need": need, "left": left})
    td, tp = sum(done.values()), sum(pend.values())
    need_total = float(req.get("총") or 0)
    total_left = max(0.0, need_total - td - tp) if need_total else 0.0
    bars.append({"area": "총", "done": td, "pending": tp, "need": need_total, "left": total_left if need_total else None})
    # 3000/4000 단위
    up_done = sum(float(c.get("credits") or 0) for c in courses if counts(c) and status_of(c) == "done" and (code_of(c) or ("", 0))[1] >= 3)
    up_pend = sum(float(c.get("credits") or 0) for c in courses if counts(c) and status_of(c) != "done" and (code_of(c) or ("", 0))[1] >= 3)
    up_short = max(0.0, float(cfg.upper_level_min) - up_done - up_pend)
    taken = {(code_of(c) or ("",))[0] for c in courses if counts(c)}
    done_codes = {(code_of(c) or ("",))[0] for c in courses if counts(c) and status_of(c) == "done"}
    pend_codes = taken - done_codes
    low_needed = sum(float(v) for k, v in (cfg.required_low_level or {}).items() if k not in taken)
    rest = max(cat_left, total_left)
    able = max(0.0, rest - low_needed)  # 남은 학점 중 3000/4000 으로 채울 수 있는 몫
    extra = max(0.0, up_short - able)
    real_left = rest + extra
    upper = {"done": up_done, "pending": up_pend, "need": float(cfg.upper_level_min), "short": up_short, "extra": extra}
    # 교양 6영역
    have_ge: dict[str, str] = {}
    planned_unknown = 0
    for c in courses:
        if not counts(c) or c.get("area") != "교양":
            continue
        g = ge_area_of(c)
        if g:
            st = status_of(c)
            if have_ge.get(g) != "done":
                have_ge[g] = st
        elif status_of(c) != "done" and c.get("military"):
            planned_unknown += 1
    ge = {"need": int(cfg.ge_areas_required), "areas": [{"area": g, "state": have_ge.get(g, "")} for g in GE6],
          "done": sum(1 for v in have_ge.values() if v == "done"), "with_pending": len(have_ge), "planned_unknown": planned_unknown,
          "missing": [g for g in GE6 if g not in have_ge]}
    ge["ok_if_plan"] = ge["with_pending"] + planned_unknown >= ge["need"]
    # 군강좌
    mil_terms: dict[str, float] = {}
    for c in courses:
        if c.get("military") and float(c.get("credits") or 0) > 0 and str(c.get("grade") or "").upper() not in ("NP", "F", "W"):
            mil_terms[str(c.get("term") or "")] = mil_terms.get(str(c.get("term") or ""), 0.0) + float(c["credits"])
    mil = {"used": sum(mil_terms.values()), "total": int(cfg.military_total), "per_term": int(cfg.military_per_term),
           "terms": [{"term": t, "credits": v, "over": v > cfg.military_per_term} for t, v in sorted(mil_terms.items())]}
    mil["over"] = mil["used"] > mil["total"] or any(t["over"] for t in mil["terms"])
    req_missing = [{"code": k, "name": v, "pending": k in pend_codes, "note": (cfg.confirm_courses or {}).get(k, "")}
                   for k, v in (cfg.required_courses or {}).items() if k not in done_codes]
    return {"bars": bars, "upper": upper, "ge": ge, "military": mil, "real_left": real_left, "earned_done": td, "required_missing": req_missing}


def view(jp: Any) -> dict[str, Any]:
    courses = jp.store.q("SELECT * FROM acad_courses ORDER BY term, id")
    req_r = jp.store.one("SELECT body FROM acad_req WHERE id=1")
    req = json.loads(req_r["body"]) if req_r else {}
    cfg = jp.cfg()
    ch = check(courses, req, cfg)
    plans = {r["term"]: json.loads(r["body"]) for r in jp.store.q("SELECT * FROM acad_plan")}
    term_max = int(req.get("term_max") or TERM_MAX)
    retake = retake_plan(courses, cfg=cfg, real_left=ch["real_left"] if req.get("총") else None, term_max=term_max)
    return {"retake": retake, "courses": courses, "gpa": gpa(courses), "bars": ch["bars"], "upper": ch["upper"], "ge": ch["ge"],
            "military": ch["military"], "real_left": ch["real_left"], "required_missing": ch["required_missing"], "req": req, "plans": plans, "terms": TERMS, "term_max": term_max,
            "retake_max": cfg.retake_max_grade, "grades": GRADES, "statuses": STATUSES, "ge6": GE6,
            "docs": jp.store.q("SELECT id, at FROM acad_docs ORDER BY at DESC LIMIT 20")}


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
    out = await jp.llm.ask("acad.parse", "good", "너는 대학 성적표·졸업 자가진단 화면을 읽어 표로 옮긴다. 화면에 있는 것만 적고, 안 보이면 비운다.",
                              "그림의 과목(이름·학점·성적·영역(교양|전필|전선|기타)·학기·재수강 여부)과 영역별 이수 합계·남은 학점을 JSON 으로.",
                              schema=PARSE_SCHEMA, images=imgs)
    return {"draft": out, "note": "확인 전에는 저장하지 않아요 — 고친 뒤 '맞아요'"}


def save_courses(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    """'맞아요' — 표를 통째로 바꿈. 번호·재수강 안 함(no_retake)·상태·군강좌·교양 영역 칸은 보낸 대로 (안 보낸 칸은 같은 번호의 지금 값 그대로)."""
    rows = body.get("courses")
    if not isinstance(rows, list) or len(rows) > 200:
        raise JpError(400, "bad", "과목 목록이 없어요")
    old = {r["id"]: r for r in jp.store.q("SELECT * FROM acad_courses")}
    clean, ids = [], set()
    for c in rows:
        if not isinstance(c, dict) or not clip(c.get("name"), 200):
            continue
        try:
            cr = float(c.get("credits") or 0)
        except (TypeError, ValueError):
            cr = 0.0
        cid = c.get("id") if isinstance(c.get("id"), int) and c.get("id") not in ids else None
        if cid is not None:
            ids.add(cid)
        prev = old.get(cid, {}) if cid is not None else {}

        def keep(k: str, default: Any, c: dict[str, Any] = c, prev: dict[str, Any] = prev) -> Any:
            return c[k] if k in c else prev.get(k, default)

        st = keep("status", "done")
        ge = str(keep("ge_area", "") or "")
        clean.append((cid, clip(c.get("name"), 200), max(0.0, min(cr, 10.0)), clip(c.get("grade"), 4).upper(),
                      c.get("area") if c.get("area") in AREAS else "기타", clip(c.get("term"), 12), 1 if keep("retake", 0) else 0,
                      1 if keep("no_retake", 0) else 0, st if st in STATUSES else "done", 1 if keep("military", 0) else 0, ge if ge in GE6 else ""))
    with jp.store.batch():
        jp.store.x("DELETE FROM acad_courses")
        for row in clean:
            jp.store.x("INSERT INTO acad_courses (id, name, credits, grade, area, term, retake, no_retake, status, military, ge_area) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
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
        out = await jp.llm.ask("acad.plan", "good", "너는 대학 수강 계획 도우미다. 남은 학점을 채우되 전필을 먼저, 4-2 는 가볍게. 과목 이름은 일반 이름으로.",
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
