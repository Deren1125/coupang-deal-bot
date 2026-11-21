"""③ 공고 — 규칙 거르기 · 겹침 합치기 · 닫힘 판정 · Gemini 뽑기(적합도) · 화면·보드·제외·지켜보는 회사.

닫힘 (06:00·18:00): ATS 보드를 **성공적으로** 읽었는데 id 가 없으면 miss+1, 2번 연속이면 닫힘 · 읽기 실패는 miss 아님 ·
마감일 지남은 바로 닫힘 · HTML(주인이 넣은 공고) 404/410 은 바로, 마감 문구는 2번 · 지원한 건 보드에 남음 · 다시 나타나면 열림.
'관심 없음'은 이 공고만 / 이 회사 / 이 분야 (exclusions — 되돌리기). ★ 는 낮아도 숨기지 않음.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep import collectors, companies, profile
from dealbot.dashboard.jobprep.store import JpError, clip

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

INTERN_RE = re.compile(r"(?i)\bintern(ship)?s?\b|인턴|체험형|채용연계|전환형|\bco-?op\b|off-cycle|summer analyst|winter analyst|research assistant")
RA_RE = re.compile(r"\bRA\b")
NEG_TITLE_RE = re.compile(r"(?i)\bph\.?d\b|박사|\bsenior\b|\bsr\.?\b|\bstaff\b|\blead\b|\bprincipal\b|경력\s*\d+\s*년|\bmanager\b")
NEG_TEXT_RE = re.compile(r"(?i)ph\.?d\.?\s+(only|required)|박사\s*(학위)?\s*(필수|소지자만)")
SEMI_NEG_RE = re.compile(r"(?i)회로\s*설계|analog\s+design|\brtl\b|layout")
CLOSED_TEXT_RE = re.compile(r"(?i)마감|접수\s*마감|채용\s*종료|no longer accepting|position (has been )?filled|\bclosed\b")
STATUSES = ("", "interest", "applied", "doc_pass", "interview", "result")
APPLIED_UP = ("applied", "doc_pass", "interview", "result")
SEASONS = ("2027_summer_june", "2027_summer_july", "2027_winter", "term_remote", "2028_summer", "other")
GROUPS = (("2027 여름", ("2027_summer_june", "2027_summer_july")), ("2027 겨울", ("2027_winter",)), ("학기 중 원격", ("term_remote",)),
          ("2028 여름", ("2028_summer",)), ("미정", ("other", "", None)))
TAGS = {"2027_summer_june": "6월 시작", "2027_summer_july": "7/14 뒤 시작"}
BATCH = 10
EXTRACT_PER_RUN = 12  # 한 번 돌 때 Gemini 묶음 수 (≤120 공고)
REFIT_PER_DAY = 50


def region_of(loc: str) -> str:
    s = (loc or "").lower()
    if not s.strip():
        return "UNK"
    if re.search(r"remote|원격|anywhere", s):
        return "REMOTE"
    if re.search(r"korea|seoul|서울|판교|성남|한국|busan|부산|대전|수원|경기", s):
        return "KR"
    if "singapore" in s or "싱가포르" in s:
        return "SG"
    if "hong kong" in s or "홍콩" in s:
        return "HK"
    if re.search(r"united states|\busa?\b|new york|san francisco|seattle|chicago|boston|austin|los angeles|palo alto|mountain view|"
                 r"menlo park|miami|denver|washington|\b(ca|ny|wa|il|tx|ma|fl|co|nj|ga|pa)\b", s):
        return "US"
    return "OTHER"


def rule_pass(title: str, text: str, cat: str, loc: str) -> tuple[bool, str]:
    t = title or ""
    head = (text or "")[:400]
    if not (INTERN_RE.search(t) or RA_RE.search(t) or INTERN_RE.search(head)):
        return False, "not_intern"
    if NEG_TITLE_RE.search(t) or NEG_TEXT_RE.search(text or ""):
        return False, "senior_phd"
    if cat == "semi" and (SEMI_NEG_RE.search(t) or SEMI_NEG_RE.search(head)):
        return False, "circuit"
    if region_of(loc) == "OTHER":
        return False, "region"
    return True, ""


def title_norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", s)
    s = re.sub(r"\b20\d\d\b|\(m/f/d\)|m/w/d|\bh/f\b", " ", s)
    return re.sub(r"[^0-9a-z가-힣]+", " ", s).strip()


def overlap(a: str, b: str) -> float:
    x, y = set(title_norm(a).split()), set(title_norm(b).split())
    return len(x & y) / max(1, len(x | y))


def now_day(clock: float) -> str:
    return datetime.fromtimestamp(clock, KST).strftime("%Y-%m-%d")


def event(store: Any, job_id: int, kind: str, note: str = "", at: float | None = None) -> None:
    store.x("INSERT INTO job_events (at, job_id, kind, note) VALUES (?,?,?,?)", (at or time.time(), job_id, kind, note[:300]))


def find_dupe(store: Any, company_id: int, title: str, region: str, deadline: str | None, src: str) -> dict[str, Any] | None:
    for r in store.q("SELECT id, title, region, deadline, src, url, alt_urls FROM jobs WHERE company_id=? AND state='open' AND src<>?", (company_id, src)):
        if title_norm(r["title"]) == title_norm(title) and (r["region"] == region or "UNK" in (r["region"], region)):
            return r
        if overlap(r["title"], title) >= 0.9 and (r["deadline"] or None) == (deadline or None):
            return r
    return None


def apply_board(store: Any, company: dict[str, Any], src: str, token: str, rows: list[dict[str, Any]], *, ok: bool, now: float) -> dict[str, int]:
    """보드 한 번 읽은 결과 → jobs 표. ok=False (읽기 실패) 면 아무것도 닫지 않음."""
    st = {"new": 0, "seen": 0, "rule_out": 0, "closed": 0, "reopened": 0, "merged": 0}
    if not ok:
        return st
    seen: set[str] = set()
    for p in rows:
        good, _why = rule_pass(p["title"], p.get("text") or "", company.get("cat") or "", p.get("loc") or "")
        if not good:
            st["rule_out"] += 1
            continue
        ukey = f"{src}:{token}:{p['src_id']}"
        seen.add(ukey)
        dh = hashlib.sha256((p["title"] + "\n" + (p.get("text") or "")).encode()).hexdigest()[:16]
        region = region_of(p.get("loc") or "")
        cur = store.one("SELECT id, state, closed_why, desc_hash FROM jobs WHERE ukey=?", (ukey,))
        if cur:
            st["seen"] += 1
            sets = "last_seen=?, miss=0, title=?, loc=?, region=?, url=?"
            args: list[Any] = [now, p["title"], p.get("loc") or "", region, p.get("url") or ""]
            if cur["desc_hash"] != dh:
                sets += ", desc_hash=?, body=?, extracted=0"
                args += [dh, (p.get("text") or "")[:collectors.TEXT_MAX]]
            if cur["state"] == "closed" and cur["closed_why"] == "목록에서 사라짐":
                sets += ", state='open', closed_why=NULL, closed_at=NULL"
                event(store, cur["id"], "reopened", p["title"], now)
                st["reopened"] += 1
            store.x(f"UPDATE jobs SET {sets} WHERE id=?", (*args, cur["id"]))
            continue
        d = find_dupe(store, company["id"], p["title"], region, p.get("deadline"), src)
        if d:
            alts = json.loads(d["alt_urls"] or "[]")
            if p.get("url") and p["url"] not in alts and p["url"] != d["url"]:
                alts.append(p["url"])
                store.x("UPDATE jobs SET alt_urls=?, last_seen=? WHERE id=?", (json.dumps(alts[:10]), now, d["id"]))
            st["merged"] += 1
            continue
        jid = store.x("INSERT INTO jobs (src, src_id, ukey, company_id, title, loc, region, url, desc_hash, body, deadline, first_seen, last_seen, cat) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (src, p["src_id"], ukey, company["id"], p["title"], p.get("loc") or "", region, p.get("url") or "", dh,
                       (p.get("text") or "")[:collectors.TEXT_MAX], p.get("deadline"), now, now, company.get("cat") or ""))
        event(store, jid, "new", f"{company['name']} · {p['title']}", now)
        st["new"] += 1
    prefix = f"{src}:{token}:"
    for r in store.q("SELECT id, ukey, miss, title FROM jobs WHERE state='open' AND ukey LIKE ?", (prefix + "%",)):
        if r["ukey"] in seen:
            continue
        miss = int(r["miss"] or 0) + 1
        if miss >= 2:
            close(store, r["id"], "목록에서 사라짐", now)
            st["closed"] += 1
        else:
            store.x("UPDATE jobs SET miss=? WHERE id=?", (miss, r["id"]))
    return st


def close(store: Any, job_id: int, why: str, now: float) -> None:
    store.x("UPDATE jobs SET state='closed', closed_why=?, closed_at=?, miss=0 WHERE id=? AND state='open'", (why, now, job_id))
    r = store.one("SELECT title FROM jobs WHERE id=?", (job_id,))
    event(store, job_id, "closed", f"{why} · {r['title'] if r else ''}", now)


def close_past_deadlines(store: Any, now: float) -> int:
    today = now_day(now)
    n = 0
    for r in store.q("SELECT id, deadline FROM jobs WHERE state='open' AND deadline IS NOT NULL AND deadline<>'' AND deadline<>'상시'"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["deadline"] or "") and r["deadline"] < today:
            close(store, r["id"], "마감일 지남", now)
            n += 1
    return n


def judge_page(status: int, text: str) -> str:
    """주인이 넣은 공고 쪽 다시 보기 → 'gone'(바로 닫힘) · 'closed_text'(miss) · 'ok' · 'fail'."""
    if status in (404, 410):
        return "gone"
    if status != 200:
        return "fail"
    if CLOSED_TEXT_RE.search(collectors.strip_html(text)[:4000]):
        return "closed_text"
    return "ok"


def apply_page_check(store: Any, job_id: int, verdict: str, now: float) -> None:
    if verdict == "gone":
        close(store, job_id, "페이지 없음 (404/410)", now)
    elif verdict == "closed_text":
        r = store.one("SELECT miss FROM jobs WHERE id=?", (job_id,))
        miss = int((r or {}).get("miss") or 0) + 1
        if miss >= 2:
            close(store, job_id, "마감 문구", now)
        else:
            store.x("UPDATE jobs SET miss=? WHERE id=?", (miss, job_id))
    elif verdict == "ok":
        store.x("UPDATE jobs SET miss=0, last_seen=? WHERE id=?", (now, job_id))


def watch_update(store: Any, company_id: int, url: str, html: str, now: float) -> str | None:
    """지켜보는 회사 쪽 글 해시가 바뀌었으면 바뀐 줄 앞 200자 (처음 읽은 때는 None)."""
    h, lines = collectors.page_text_hash(html)
    r = store.one("SELECT hash, note FROM watch_pages WHERE url=?", (url,))
    prev_lines = store.kv_get(f"watch_lines:{hashlib.sha256(url.encode()).hexdigest()[:16]}", []) or []
    store.kv_set(f"watch_lines:{hashlib.sha256(url.encode()).hexdigest()[:16]}", lines[:400])
    if r is None:
        store.x("INSERT INTO watch_pages (company_id, url, hash, changed_at, note, checked_at) VALUES (?,?,?,?,?,?)", (company_id, url, h, None, "", now))
        return None
    if r["hash"] == h:
        store.x("UPDATE watch_pages SET checked_at=? WHERE url=?", (now, url))
        return None
    old = set(prev_lines)
    diff = " / ".join(ln for ln in lines if ln not in old)[:200]
    store.x("UPDATE watch_pages SET hash=?, changed_at=?, note=?, checked_at=? WHERE url=?", (h, now, diff, now, url))
    store.x("INSERT INTO job_events (at, job_id, kind, note) VALUES (?,?,?,?)", (now, -company_id, "watch", diff))
    return diff


# ── Gemini 뽑기 ──
EXTRACT_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "integer"}, "start": {"type": ["string", "null"]}, "deadline": {"type": ["string", "null"]},
    "conversion": {"type": ["boolean", "null"]}, "bachelor_ok": {"type": ["boolean", "null"]}, "grad_ok": {"type": ["boolean", "null"]},
    "season": {"type": "string", "enum": list(SEASONS)}, "is_intern": {"type": "boolean"}, "fit": {"type": "integer"},
    "fit_why": {"type": "string"}}, "required": ["id", "season", "is_intern", "fit", "fit_why"]}}}, "required": ["items"]}
EXTRACT_SYSTEM = "너는 채용 공고를 읽어 정해진 JSON 칸만 채운다. 공고에 없는 것은 null. 추측 금지."


def extract_prompt(prof: str, batch: list[dict[str, Any]]) -> str:
    items = [{"id": j["id"], "company": j["company"], "title": j["title"], "location": j["loc"], "text": (j["body"] or "")[:2500]} for j in batch]
    return ("지원자: " + prof + "\n공고 " + str(len(items)) + "개: " + json.dumps(items, ensure_ascii=False) + "\n"
            "각 공고마다: start(YYYY-MM|null) · deadline(YYYY-MM-DD|\"상시\"|null) · conversion(true|false|null) · "
            "bachelor_ok(학사 재학생 지원 가능?) · grad_ok(2029-02 졸업 예정자 가능? — 졸업 연도 조건이 있으면 그걸로) · "
            "season(2027_summer_june|2027_summer_july|2027_winter|term_remote|2028_summer|other) · is_intern · "
            "fit(1~5: 5=전공·관심·시기 모두 맞음, 3=일부, 1=거의 안 맞음) · fit_why(한국어 40자 안)")


def _tri(v: Any) -> int | None:
    return None if v is None else (1 if v else 0)


async def extract_pending(jp: Any, *, max_batches: int = EXTRACT_PER_RUN) -> int:
    store = jp.store
    prof, ver = profile.get(store)
    summ = profile.summary(prof)
    done = 0
    for _ in range(max_batches):
        rows = store.q("SELECT j.id, j.title, j.loc, j.body, c.name company FROM jobs j LEFT JOIN companies c ON c.id=j.company_id "
                       "WHERE j.state='open' AND (j.extracted=0 OR (j.profile_ver<? AND j.extracted=1)) ORDER BY j.extracted, j.first_seen DESC LIMIT ?",
                       (ver, BATCH))
        if not rows:
            break
        out = await jp.llm.gemini("jobs.extract", "fast", EXTRACT_SYSTEM, extract_prompt(summ, rows), schema=EXTRACT_SCHEMA)
        got = {int(i.get("id")): i for i in (out.get("items") or []) if isinstance(i, dict) and isinstance(i.get("id"), int)}
        for r in rows:
            it = got.get(r["id"])
            if it is None:
                store.x("UPDATE jobs SET extracted=2, profile_ver=? WHERE id=?", (ver, r["id"]))  # 답이 빠짐 — 다시 묻지 않음 (★ 없음)
                continue
            fit = max(1, min(5, int(it.get("fit") or 1)))
            dl = it.get("deadline")
            dl = dl if isinstance(dl, str) and (dl == "상시" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl)) else None
            st = it.get("start") if isinstance(it.get("start"), str) and re.fullmatch(r"\d{4}-\d{2}", it.get("start") or "") else None
            season = it.get("season") if it.get("season") in SEASONS else "other"
            auto_out = 1 if (it.get("is_intern") is False or it.get("bachelor_ok") is False or it.get("grad_ok") is False) else 0
            store.x("UPDATE jobs SET start=?, deadline=COALESCE(?, deadline), conversion=?, bachelor_ok=?, grad_ok=?, is_intern=?, season=?, fit=?, "
                    "fit_why=?, auto_out=?, extracted=1, profile_ver=? WHERE id=?",
                    (st, dl, _tri(it.get("conversion")), _tri(it.get("bachelor_ok")), _tri(it.get("grad_ok")), _tri(it.get("is_intern")),
                     season, fit, clip(it.get("fit_why"), 60), auto_out, ver, r["id"]))
            done += 1
    return done


# ── 화면 ──
def excluded(store: Any) -> tuple[set[int], set[int], set[str]]:
    jobs_, comps, cats = set(), set(), set()
    for e in store.q("SELECT scope, key FROM exclusions"):
        if e["scope"] == "job" and str(e["key"]).isdigit():
            jobs_.add(int(e["key"]))
        elif e["scope"] == "company" and str(e["key"]).isdigit():
            comps.add(int(e["key"]))
        elif e["scope"] == "cat":
            cats.add(str(e["key"]))
    return jobs_, comps, cats


def card(r: dict[str, Any], cases_n: dict[str, int]) -> dict[str, Any]:
    return {"id": r["id"], "company": r.get("company") or "", "company_id": r["company_id"], "title": r["title"], "loc": r["loc"],
            "region": r["region"], "url": r["url"], "alt_urls": json.loads(r.get("alt_urls") or "[]"), "start": r["start"],
            "deadline": r["deadline"], "conversion": r["conversion"], "bachelor_ok": r["bachelor_ok"], "grad_ok": r["grad_ok"],
            "fit": r["fit"], "fit_why": r["fit_why"] or "", "season": r["season"] or "", "tag": TAGS.get(r["season"] or "", ""),
            "status": r["user_status"] or "", "state": r["state"], "closed_why": r["closed_why"] or "", "cat": r["cat"] or "",
            "cases": cases_n.get(companies.norm(r.get("company") or ""), 0), "first_seen": r["first_seen"]}


def group_of(season: str | None) -> str:
    for label, keys in GROUPS:
        if season in keys:
            return label
    return "미정"


def view(jp: Any, query: dict[str, str]) -> dict[str, Any]:
    store = jp.store
    cat, region, season = clip(query.get("cat"), 30), clip(query.get("region"), 10), clip(query.get("season"), 30)
    ex_jobs, ex_comps, ex_cats = excluded(store)
    cases_n = {r["n"]: r["c"] for r in store.q("SELECT company n, COUNT(*) c FROM cases GROUP BY company")}
    cases_n = {companies.norm(k or ""): v for k, v in cases_n.items()}
    rows = store.q("SELECT j.*, c.name company FROM jobs j LEFT JOIN companies c ON c.id=j.company_id ORDER BY COALESCE(j.fit,0) DESC, j.first_seen DESC LIMIT 1500")
    groups: dict[str, list[dict[str, Any]]] = {label: [] for label, _ in GROUPS}
    closed, auto_out, board = [], [], {s: [] for s in STATUSES[1:]}
    for r in rows:
        c = card(r, cases_n)
        if r["user_status"] in board:
            board[r["user_status"]].append(c)
        if r["id"] in ex_jobs or r["company_id"] in ex_comps or (r["cat"] or "") in ex_cats:
            continue
        if r["state"] == "closed":
            if len(closed) < 100:
                closed.append(c)
            continue
        if r["auto_out"]:
            auto_out.append(c)
            continue
        if cat and r["cat"] != cat or region and r["region"] != region or season and group_of(r["season"]) != season:
            continue
        groups[group_of(r["season"])].append(c)
    exc = []
    for e in store.q("SELECT * FROM exclusions ORDER BY at DESC"):
        exc.append({"id": e["id"], "scope": e["scope"], "key": e["key"], "label": e["label"]})
    return {"groups": [{"label": k, "items": v} for k, v in groups.items()], "closed": closed, "auto_out": auto_out[:100],
            "auto_out_n": len(auto_out), "board": board, "exclusions": exc, "cats": companies.CATS,
            "watch": watch_view(jp), "last": store.kv_get("collect_last", {})}


def watch_view(jp: Any) -> list[dict[str, Any]]:
    rows = jp.store.q("SELECT c.id, c.name, c.cat, c.career_url, c.on_, c.growth_note, w.changed_at, w.note, w.checked_at FROM companies c "
                      "LEFT JOIN watch_pages w ON w.url=c.career_url WHERE (c.ats='' OR c.ats IS NULL) ORDER BY c.on_ DESC, w.changed_at DESC, c.name")
    return [{"id": r["id"], "name": r["name"], "cat": r["cat"], "url": r["career_url"] or "", "on": bool(r["on_"]), "growth": r["growth_note"] or "",
             "changed_at": r["changed_at"], "note": r["note"] or "", "checked_at": r["checked_at"]} for r in rows]


def board_view(jp: Any) -> dict[str, Any]:
    return {"board": view(jp, {})["board"]}


def mark(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    i, status = body.get("id"), body.get("status")
    if not isinstance(i, int) or status not in STATUSES:
        raise JpError(400, "bad", "상태를 알 수 없어요")
    r = jp.store.one("SELECT id FROM jobs WHERE id=?", (i,))
    if r is None:
        raise JpError(404, "gone", "그 공고가 없어요")
    jp.store.x("UPDATE jobs SET user_status=?, applied_at=CASE WHEN ?='applied' THEN ? ELSE applied_at END WHERE id=?", (status, status, time.time(), i))
    event(jp.store, i, "status", status)
    return {"ok": True}


def exclude(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    scope, key = body.get("scope"), body.get("key")
    if scope not in ("job", "company", "cat") or key in (None, ""):
        raise JpError(400, "bad", "무엇을 뺄지 몰라요")
    key = str(key)[:40]
    label = key
    if scope == "job":
        r = jp.store.one("SELECT j.title, c.name FROM jobs j LEFT JOIN companies c ON c.id=j.company_id WHERE j.id=?", (int(key) if key.isdigit() else -1,))
        label = f"{r['name']} · {r['title']}" if r else key
    elif scope == "company":
        r = jp.store.one("SELECT name FROM companies WHERE id=?", (int(key) if key.isdigit() else -1,))
        label = r["name"] if r else key
    else:
        label = companies.CATS.get(key, key)
    jp.store.x("INSERT OR IGNORE INTO exclusions (scope, key, label, at) VALUES (?,?,?,?)", (scope, key, label[:120], time.time()))
    return {"ok": True}


def restore(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    i = body.get("id")
    if not isinstance(i, int):
        raise JpError(400, "bad", "번호가 없어요")
    jp.store.x("DELETE FROM exclusions WHERE id=?", (i,))
    return {"ok": True}


def watch_post(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    """{id, on} 켜고 끔 · {id, url} 주소 고침 · {name, url, cat} 새 회사."""
    url = clip(body.get("url"), 500)
    if url and not url.startswith("https://"):
        raise JpError(400, "bad_url", "https:// 주소만 넣어 주세요")
    if isinstance(body.get("id"), int):
        if "on" in body:
            jp.store.x("UPDATE companies SET on_=? WHERE id=?", (1 if body.get("on") else 0, body["id"]))
        if "url" in body:
            jp.store.x("UPDATE companies SET career_url=? WHERE id=? AND (ats='' OR ats IS NULL)", (url, body["id"]))
        return {"ok": True, "watch": watch_view(jp)}
    name = clip(body.get("name"), 80)
    if not name:
        raise JpError(400, "bad", "회사 이름이 필요해요")
    cid = companies.get_or_add(jp.store, name, clip(body.get("cat"), 30), "", on=1)
    if url:
        jp.store.x("UPDATE companies SET career_url=?, on_=1 WHERE id=?", (url, cid))
    return {"ok": True, "watch": watch_view(jp)}


def add(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    """주인이 직접 넣는 공고 (서버는 넣을 때 읽지 않음 · 06:00·18:00 다시 보기만 — robots.txt 허용일 때)."""
    url = clip(body.get("url"), 600)
    if not url.startswith("https://"):
        raise JpError(400, "bad_url", "https:// 주소를 넣어 주세요")
    company = clip(body.get("company"), 80) or (re.sub(r"^www\.", "", url.split("/")[2]) if url.count("/") >= 2 else "직접 넣음")
    title = clip(body.get("title"), 200) or "직접 넣은 공고"
    cid = companies.get_or_add(jp.store, company, clip(body.get("cat"), 30))
    now = time.time()
    ukey = "manual:" + hashlib.sha256(url.encode()).hexdigest()[:20]
    if jp.store.one("SELECT id FROM jobs WHERE ukey=?", (ukey,)):
        raise JpError(409, "dupe", "이미 넣은 공고예요")
    dl = clip(body.get("deadline"), 10)
    jid = jp.store.x("INSERT INTO jobs (src, src_id, ukey, company_id, title, loc, region, url, desc_hash, body, deadline, first_seen, last_seen, "
                     "cat, user_status, extracted, fit, fit_why, season) VALUES ('manual',?,?,?,?,?,?,?,'','',?,?,?,?,'interest',1,NULL,'',?)",
                     (ukey[7:], ukey, cid, title, clip(body.get("loc"), 80), region_of(clip(body.get("loc"), 80)), url,
                      dl if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dl) else None, now, now, clip(body.get("cat"), 30),
                      body.get("season") if body.get("season") in SEASONS else "other"))
    event(jp.store, jid, "new", f"{company} · {title} (직접)", now)
    return {"id": jid}


# ── 모으기 (06:00) · 다시 보기 (18:00) ──
async def collect(jp: Any, *, recheck_only: bool = False) -> dict[str, Any]:
    store, f = jp.store, jp.fetcher
    now = jp.clock()
    if not store.one("SELECT id FROM companies LIMIT 1"):
        companies.seed(store)
    res: dict[str, Any] = {"at": now, "boards": {}, "watch": {}, "recheck_only": recheck_only}
    for c in store.q("SELECT * FROM companies WHERE on_=1 AND ats IN ('greenhouse','lever','ashby','workable','smartrecruiters') AND token<>''"):
        ok, rows, why = await collectors.read_board(f, c["ats"], c["token"])
        st = apply_board(store, c, c["ats"], c["token"], rows, ok=ok, now=jp.clock())
        res["boards"][f"{c['name']}"] = {"ok": ok, "why": why, **st}
    res["deadline_closed"] = close_past_deadlines(store, jp.clock())
    for j in store.q("SELECT id, url FROM jobs WHERE state='open' AND src='manual' LIMIT 40"):
        status, html = await collectors.read_page(f, j["url"], "manual:" + (j["url"].split("/")[2] if j["url"].count("/") >= 2 else "x"))
        if status in (-2, -3):
            continue
        apply_page_check(store, j["id"], judge_page(status, html), jp.clock())
    if not recheck_only:
        for c in store.q("SELECT * FROM companies WHERE on_=1 AND (ats='' OR ats IS NULL) AND career_url LIKE 'https://%'"):
            status, html = await collectors.read_page(f, c["career_url"], "watch:" + c["career_url"].split("/")[2])
            if status == 200:
                watch_update(store, c["id"], c["career_url"], html, jp.clock())
            res["watch"][c["name"]] = status
    store.kv_set("collect_last", res)
    return res


async def growth(jp: Any) -> dict[str, Any]:
    """월 05:50 — DefiLlama 30일 수익 상위 · 거래소 거래량 · YC 채용 중 (한국·원격) → 회사 표 growth_note (기본 꺼짐 — 주인이 켬)."""
    f, store = jp.fetcher, jp.store
    out: dict[str, Any] = {}
    for key, parse, cat in (("defillama", collectors.parse_defillama, "crypto_growth"), ("coingecko", collectors.parse_coingecko, "crypto"),
                            ("yc", collectors.parse_yc, "ai_growth")):
        if f.is_off(key):
            out[key] = "쉼"
            continue
        r = await f.get(collectors.GROWTH_URLS[key])
        if r.status != 200:
            f.note(key, False, collectors.why_of(r.status))
            out[key] = collectors.why_of(r.status)
            continue
        try:
            rows = parse(json.loads(r.body))
        except ValueError:
            out[key] = "읽기 실패"
            continue
        f.note(key, True)
        for row in rows:
            cid = companies.get_or_add(store, row["name"], cat, "global")
            note = (f"30일 수익 ${row['rev30d'] / 1e6:,.1f}M" if key == "defillama" else
                    f"24h 거래량 {row['btc24h']:,.0f} BTC" if key == "coingecko" else f"YC {row['batch']} · {row['one_liner']}")
            store.x("UPDATE companies SET growth_note=? WHERE id=?", (note[:200], cid))
            if key == "yc" and row.get("url", "").startswith("https://"):
                store.x("UPDATE companies SET career_url=COALESCE(NULLIF(career_url,''), ?) WHERE id=?", (row["url"], cid))
        out[key] = len(rows)
    store.kv_set("growth_last", {"at": jp.clock(), **out})
    return out


def deadlines_soon(store: Any, today: str, days: int = 7, limit: int = 5) -> list[dict[str, Any]]:
    end = date.fromordinal(date.fromisoformat(today).toordinal() + days).isoformat()
    rows = store.q("SELECT j.id, j.title, j.deadline, j.user_status, c.name company FROM jobs j LEFT JOIN companies c ON c.id=j.company_id "
                   "WHERE j.state='open' AND j.auto_out=0 AND j.deadline>=? AND j.deadline<=? AND j.deadline LIKE '____-__-__'", (today, end))
    rank = {"interest": 0, "applied": 0, "doc_pass": 0, "interview": 0}
    rows.sort(key=lambda r: (rank.get(r["user_status"] or "", 1), r["deadline"]))
    return rows[:limit]
