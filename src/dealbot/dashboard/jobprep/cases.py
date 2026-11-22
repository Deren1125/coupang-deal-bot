"""④ 합격 사례 — 공개 글만 (로그인 벽·이웃 공개 글은 버림). 글 전체는 저장하지 않고 요약 칸 + 링크만 · 이름·연락처는 지움.

모으기 (월 05:50 · 주 cases_pages_per_week 쪽까지): 네이버 검색 API(서버 키 — 로그인 아님)로 "<회사> 인턴 합격 후기" → 공개 글 한 쪽 →
Claude(fast) 로 칸 뽑기. 이력서 본보기는 RESUME_SOURCES (학교·회사 공식 본보기 링크만).
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import quote

from dealbot.dashboard.jobprep import collectors, companies, profile
from dealbot.dashboard.jobprep.store import JpError, clip

NAVER_BLOG = "https://openapi.naver.com/v1/search/blog.json?query={q}&display=10&sort=sim"
WALL_RE = re.compile(r"이웃\s*공개|서로이웃|로그인(이|을)?\s*(해야|후|필요)|비공개 글|권한이 없")
PII_RE = re.compile(r"\b01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}\b|[\w.+-]+@[\w-]+\.[\w.]+")
RESUME_SOURCES = [
    ("Harvard OCS 이력서 본보기", "official", "https://careerservices.fas.harvard.edu/resources/create-a-strong-resume/"),
    ("MIT CAPD 이력서 안내", "official", "https://capd.mit.edu/resources/resumes/"),
    ("Yale OCS 이력서 본보기", "official", "https://ocs.yale.edu/resources/resume-samples/"),
]
FIELDS = ("school", "major", "gpa", "english", "exp", "certs", "questions", "prep")
CASE_SCHEMA = {"type": "object", "properties": {"is_case": {"type": "boolean"}, "company": {"type": "string"}, "role": {"type": "string"},
               **{k: {"type": "string"} for k in FIELDS}}, "required": ["is_case", "company", "role", *FIELDS]}
COMPARE_SCHEMA = {"type": "object", "properties": {"gaps": {"type": "array", "items": {"type": "object", "properties": {
    "what": {"type": "string"}, "why": {"type": "string"}, "todo": {"type": "string"}}, "required": ["what", "why", "todo"]}},
    "strengths": {"type": "array", "items": {"type": "string"}}}, "required": ["gaps", "strengths"]}


def scrub(s: str) -> str:
    return PII_RE.sub("●", s or "")


def seed_resumes(store: Any) -> None:
    for label, kind, url in RESUME_SOURCES:
        store.x("INSERT OR IGNORE INTO resumes (label, kind, url, summary) VALUES (?,?,?,?)", (label, kind, url, ""))


def view(jp: Any, query: dict[str, str]) -> dict[str, Any]:
    store = jp.store
    seed_resumes(store)
    cat, comp, role = clip(query.get("cat"), 30), clip(query.get("company"), 80), clip(query.get("role"), 40)
    rows = store.q("SELECT * FROM cases ORDER BY at DESC LIMIT 500")
    tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for r in rows:
        tree.setdefault(r["cat"] or "기타", {}).setdefault(r["company"] or "?", []).append(r)
    items = [r for r in rows if (not cat or r["cat"] == cat) and (not comp or companies.norm(r["company"] or "") == companies.norm(comp))
             and (not role or role.lower() in (r["role"] or "").lower())]
    similar = False
    if comp and not items:
        c = store.one("SELECT cat FROM companies WHERE norm=?", (companies.norm(comp),))
        if c and c["cat"]:
            items = [r for r in rows if r["cat"] == c["cat"] and (not role or role.lower() in (r["role"] or "").lower())][:20]
            similar = bool(items)
    return {"tree": {k: {c: len(v) for c, v in d.items()} for k, d in tree.items()}, "items": [dict(r) for r in items[:60]],
            "similar": similar, "resumes": store.q("SELECT * FROM resumes ORDER BY kind, label"), "cats": companies.CATS,
            "last": store.kv_get("cases_last", {})}


def save_case(store: Any, url: str, cat: str, data: dict[str, Any], src_kind: str) -> int:
    vals = {k: scrub(clip(data.get(k), 400)) for k in FIELDS}
    return store.x("INSERT OR REPLACE INTO cases (cat, company, role, school, major, gpa, english, exp, certs, questions, prep, url, src_kind, at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cat, clip(data.get("company"), 80), clip(data.get("role"), 80), vals["school"], vals["major"], vals["gpa"], vals["english"],
                    vals["exp"], vals["certs"], vals["questions"], vals["prep"], url[:500], src_kind, time.time()))


async def read_one(jp: Any, url: str, company: str, cat: str) -> bool:
    status, html = await collectors.read_page(jp.fetcher, url, "cases:" + url.split("/")[2])
    if status != 200:
        return False
    text = collectors.strip_html(html)
    if len(text) < 300 or WALL_RE.search(text[:3000]):
        return False
    data = await jp.llm.ask("cases.extract", "fast", "합격 후기 글에서 정해진 칸만 뽑는다. 글에 없으면 빈 글자. 이름·연락처는 쓰지 않는다.",
                               f"회사(찾던 곳): {company}\n글:\n{text[:6000]}", schema=CASE_SCHEMA)
    if not data.get("is_case"):
        return False
    save_case(jp.store, url, cat, data, "blog")
    return True


def mobile_blog(url: str) -> str:
    m = re.match(r"https?://blog\.naver\.com/([A-Za-z0-9_-]+)/(\d+)", url or "")
    return f"https://m.blog.naver.com/{m.group(1)}/{m.group(2)}" if m else url


async def collect(jp: Any) -> dict[str, Any]:
    store, cfg = jp.store, jp.cfg()
    cid, csec = (os.environ.get("NAVER_CLIENT_ID") or "").strip(), (os.environ.get("NAVER_CLIENT_SECRET") or "").strip()
    out: dict[str, Any] = {"at": jp.clock(), "read": 0, "saved": 0}
    if not (cid and csec):
        out["why"] = "네이버 검색 키 없음"
        store.kv_set("cases_last", out)
        return out
    budget = int(cfg.cases_pages_per_week)
    comps = store.q("SELECT DISTINCT c.name, c.cat FROM companies c JOIN jobs j ON j.company_id=c.id WHERE j.state='open' AND j.auto_out=0 "
                    "ORDER BY (j.user_status<>'') DESC, j.fit DESC LIMIT 10")
    for c in comps:
        if out["read"] >= budget:
            break
        url = NAVER_BLOG.format(q=quote(f"{c['name']} 인턴 합격 후기"))
        try:
            r = await jp.http.get(url, headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}, timeout=15)
            items = r.json().get("items") or [] if r.status_code == 200 else []
        except Exception:  # noqa: BLE001
            items = []
        for it in items[:3]:
            link = mobile_blog(str(it.get("link") or ""))
            if not link.startswith("https://") or store.one("SELECT id FROM cases WHERE url=?", (link,)) or out["read"] >= budget:
                continue
            out["read"] += 1
            if await read_one(jp, link, c["name"], c["cat"] or ""):
                out["saved"] += 1
    store.kv_set("cases_last", out)
    return out


async def add(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    url = clip(body.get("url"), 500)
    if not url.startswith("https://"):
        raise JpError(400, "bad_url", "https:// 주소를 넣어 주세요")
    ok = await read_one(jp, mobile_blog(url), clip(body.get("company"), 80), clip(body.get("cat"), 30))
    if not ok:
        raise JpError(422, "not_case", "공개 합격 후기로 읽지 못했어요 (로그인·이웃 공개 글은 안 읽어요)")
    return {"ok": True}


async def compare(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    comp, role = clip(body.get("company"), 80), clip(body.get("role"), 40)
    data = view(jp, {"company": comp, "role": role})
    items = data["items"][:12]
    if not items:
        raise JpError(404, "none", "비교할 사례가 아직 없어요")
    prof, _ver = profile.get(jp.store)
    pack = [{k: it.get(k) for k in ("company", "role", *FIELDS)} for it in items]
    out = await jp.llm.ask("cases.compare", "good", "너는 취업 준비 코치다. 주어진 사례와 지원자 스펙만 보고 말한다. 없는 사실은 만들지 않는다.",
                              f"지원자: {profile.summary(prof)}\n합격 사례: {json.dumps(pack, ensure_ascii=False)}\n"
                              "지원자에게 부족한 것 3가지(무엇·왜·할 일)와 강점 2가지를 한국어로.", schema=COMPARE_SCHEMA)
    return {"similar": data["similar"], **out}
