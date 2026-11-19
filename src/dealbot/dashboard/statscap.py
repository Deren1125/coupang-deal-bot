"""'통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음 · 주인이 아이패드 사파리 '내 통계'에서 하루 한 번 누름).

static/statscap.js: 이미 로그인된 네이버 내 통계 화면(blog.stat.naver.com · m.blog.naver.com 통계 …)에서 보이는 글·표·주소·제목·고른 탭을
  POST /dashboard/api/stats_capture (폼 — 교차 사이트 '간단한 요청', 쿠키 없음) 로 보낸다. 로그인 대신 책갈피에 든 열쇠 k 로만 받는다:
  key(db) = HMAC(kv statscap_secret, "statscap") — 열쇠는 연결 탭 '통계 보내기 버튼 설치'(로그인 아래 GET /dashboard/api/statscap)에서만 보임.
  kv statscap_secret 을 지우면 예전 책갈피는 더는 안 받음 (다시 설치).
save(): 크기 제한(글 60KB · 표 300줄 · 칸 200자) · 꺾쇠 태그·제어 글자 빼기 · 이메일·전화번호 가리기 → kv
  "stats_capture:<보낸 날 KST>:<HHMMSS>-<4hex>" = JSON {at, sent, day, blog, blog_id, blog_name, kind, url, title, sel, text, rows, frames, json}.
  30일 지나면 지움. 11/12 '한 번에 모두': 책갈피가 메뉴 화면마다 하나씩 보내며 n = 메뉴 이름(→ kind 그대로) · j = 화면이 읽은 같은 출처 JSON.
  11/12 저녁: 숫자는 blog.stat.naver.com(통계 앱 · 네이버 주소라 그대로 받음 · blog_id 는 blogId=) — 관리 화면은 '주소 목록'(n) 하나만 보냄.
  하루 DAY_MAX 개까지. Blog-Auto ops_report 'stats_capture' 칸이 읽기 전용으로 최근 7일을 읽는다 (클라우드 세션이 status.md 로 봄).
nudge(): 할 일 탭 '오늘 통계 보내기' 한 줄 — 오늘 보낸 것이 있으면 None.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import kkumi

SRC = Path(__file__).resolve().parent / "static" / "statscap.js"
DAY_SRC = SRC.parent / "statday.js"  # 11/18 한 번에 v3 의 통계 자리 (조회수 순위·유입분석·검색 유입 · 블로그 이름)
KV_SECRET = "statscap_secret"
PREFIX = "stats_capture:"
KEEP_DAYS = 30
DAY_MAX = 120  # 11/12 한 번 누르면 40개까지 (메뉴 화면 · 안쪽 탭 · 전날)
KIND_MAX = 40
JSON_MAX = 30_000
BODY_MAX = 1_500_000  # 폼으로 한 번 (글 4만 자 · 표 6만 자를 %XX 로 — 한글은 9바이트)
TEXT_MAX = 60_000  # 저장하는 글 (UTF-8 바이트)
ROWS_MAX = 300
CELLS_MAX = 12
CELL_MAX = 200
SEL_MAX = 12
PATH = "/dashboard/api/stats_capture"
# 블로그 주소 → Blog-Auto 블로그 (config/<blog>.yaml blog_id)
BLOG_IDS = {"derenlog": "finance", "derenpick": "issue", "demiyum": "hotdeal"}
BLOG_NAMES = {"finance": "드라마", "issue": "데런픽", "hotdeal": "라커룸"}
# 화면 종류 (고른 탭 → 주소 → 제목 → 글 머리 차례로 먼저 맞는 것)
KINDS = (
    ("유입 분석", re.compile(r"유입|referer|inflow", re.I)),
    ("검색어", re.compile(r"검색어|search_?keyword|keyword", re.I)),
    ("조회수 순위", re.compile(r"조회수\s*순위|순위|rank", re.I)),
    ("평균 사용 시간", re.compile(r"평균\s*사용\s*시간|사용\s*시간|dwell|usetime|duration", re.I)),
    ("방문 분석", re.compile(r"방문\s*분석|방문\s*횟수|순방문자|재방문|visit|\buv\b", re.I)),
    ("사용자 분석", re.compile(r"사용자\s*분석|성별|연령|기기|gender|age|device", re.I)),
    ("조회수", re.compile(r"조회수|pageview|\bpv\b", re.I)),
    ("동영상", re.compile(r"동영상|video", re.I)),
    ("일간 현황", re.compile(r"일간\s*현황|일간|오늘|today|daily", re.I)),
)
SENDING_STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '통계 보내기'로 저장해요.",
    "책갈피 목록 → '편집' → '통계 보내기'를 눌러 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'.",
    "쓰는 법 — 처음 한 번: 사파리에서 네이버 블로그 '내 통계'(admin.blog.naver.com)를 열고 책갈피 '통계 보내기' → "
    "'N개 찾음 ✓ 통계 화면으로 옮겨서 모을까요?' 에 확인 → 열린 통계 화면(blog.stat.naver.com)에서 책갈피를 한 번 더. "
    "매일: 그 통계 화면을 즐겨찾기해 두고 열어서 책갈피 한 번 — 왼쪽 위 '3/18 유입분석…' 이 끝나고 '통계 보냄 ✓ N개 화면' 알림이면 끝 "
    "(2분 남짓 · 하루 한 번이면 충분).",
)

_TAG = re.compile(r"<[^<>]{0,500}>")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f  ]")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_PHONE = re.compile(r"(?<![\d.])(?:\+82[-. ]?1[016789]|01[016789])[-. ]?\d{3,4}[-. ]?\d{4}(?![\d.])"
                    r"|(?<![\d.])0(?:2|[3-6][1-5]|70|50\d?)[-. )]\d{3,4}[-. ]\d{4}(?![\d.])")
_SCRIPTY = re.compile(r"(?i)javascript:|on[a-z]{3,20}\s*=")
_DATE = re.compile(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_DATE8 = re.compile(r"(?<!\d)(20\d\d)(\d\d)(\d\d)(?!\d)")
_BLOGID = re.compile(r"blogId=([A-Za-z0-9_-]{3,30})")
_JSON_BLOGID = re.compile(r'"blogId"\s*:\s*"([A-Za-z0-9_-]{3,30})"')
_DAY = re.compile(r"20\d\d-\d\d-\d\d")
JSON_RAW_MAX = 400_000  # 표 줄로 바꾸기 전 책갈피가 보낸 JSON (저장은 JSON_MAX 까지만)
# 11/18 '한 번에' v3 가 보내는 세 화면 (홈판 분석 세션 — 블로그·날짜마다 어느 화면이 왔는지 연결 탭 한 줄)
VIEWS = ("조회수 순위", "유입분석", "검색 유입")


class CaptureError(Exception):
    def __init__(self, status: int, code: str, text: str) -> None:
        super().__init__(code)
        self.status, self.code, self.text = status, code, text


def _secret(db: Any) -> bytes:
    key = db.kv_get(KV_SECRET)
    if not key:
        key = secrets.token_hex(32)
        db.kv_set(KV_SECRET, key)
    return bytes.fromhex(key)


def key(db: Any) -> str:
    mac = hmac.new(_secret(db), b"statscap", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def key_ok(db: Any, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(key(db).encode(), str(given).encode())


@lru_cache(maxsize=1)
def _code() -> str:
    return kkumi.minify(SRC.read_text(encoding="utf-8"))


def bookmarklet(endpoint: str, k: str) -> str:
    """javascript: 주소 (주소·열쇠를 넣고 encodeURIComponent). 둘 다 따옴표·역슬래시가 없는 글자만."""
    if not re.fullmatch(r"https://[A-Za-z0-9.\-:]+/[A-Za-z0-9_/\-]+", endpoint) or not re.fullmatch(r"[A-Za-z0-9_\-]+", k):
        raise ValueError("bad endpoint or key")
    return "javascript:" + kkumi.encode(_code().replace("@@URL@@", endpoint).replace("@@KEY@@", k))


def view(db: Any, public_url: str, *, now: float | None = None, tz: ZoneInfo | None = None) -> dict[str, Any]:
    code = bookmarklet(public_url.rstrip("/") + PATH, key(db))
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": list(SENDING_STEPS)}
    if tz is not None:  # 연결 탭 설치 칸 확인 한 줄 (11/17 — '통계 n개 받았어요 (블로그·화면 날짜)')
        out["got"] = received(db, now or time.time(), tz)["line"]
    return out


# ---------------------------------------------------------------- 받기
def clean(s: Any, limit: int) -> str:
    """태그·제어 글자·스크립트 꼴을 빼고 이메일·전화번호를 가린다. limit = UTF-8 바이트."""
    t = str(s or "")[: limit * 2]
    t = _TAG.sub(" ", t)
    t = _CTRL.sub(" ", t).replace("\r", "")
    t = t.replace("<", "‹").replace(">", "›")
    t = _SCRIPTY.sub("[x]", t)
    t = _EMAIL.sub("[이메일]", t)
    t = _PHONE.sub("[전화]", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    raw = t.encode("utf-8")[:limit]
    return raw.decode("utf-8", "ignore")


def _rows(raw: str) -> list[list[list[str]]]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    out: list[list[list[str]]] = []
    n = 0
    for tb in data if isinstance(data, list) else []:
        if not isinstance(tb, list):
            continue
        rows = []
        for r in tb:
            if n >= ROWS_MAX:
                break
            if isinstance(r, list):
                cells = [clean(c, CELL_MAX * 3)[:CELL_MAX] for c in r[:CELLS_MAX]]
                if any(cells):
                    rows.append(cells)
                    n += 1
        if rows:
            out.append(rows)
        if n >= ROWS_MAX:
            break
    return out


def json_rows(raw: str) -> list[list[list[str]]]:
    """책갈피가 보낸 JSON 묶음('주소\n본문' 을 빈 줄로 이음) → 표 [[머리 칸, …], [칸, …], …] 들.
    통계 앱 꼴 {result: {statDataList: [{data: {columnInfo: [이름…], rows: {이름: [값…]} 또는 [[값…]]}}]}} 만 — 그 밖은 건너뜀."""
    out: list[list[list[str]]] = []
    for part in re.split(r"\n\n(?=https://)", raw or ""):
        body = part.split("\n", 1)[1] if part.startswith("https://") and "\n" in part else part
        try:
            o = json.loads(body)
        except ValueError:
            continue
        res = o.get("result") if isinstance(o, dict) else None
        for sd in (res.get("statDataList") if isinstance(res, dict) else None) or []:
            data = sd.get("data") if isinstance(sd, dict) else None
            cols = data.get("columnInfo") if isinstance(data, dict) else None
            if not isinstance(cols, list) or not cols:
                continue
            cols = [str(c) for c in cols][:CELLS_MAX]
            rs = data.get("rows")
            if isinstance(rs, dict):
                n = max((len(v) for v in rs.values() if isinstance(v, list)), default=0)
                body_rows = [[rs.get(c)[i] if isinstance(rs.get(c), list) and i < len(rs[c]) else "" for c in cols] for i in range(min(n, ROWS_MAX))]
            elif isinstance(rs, list):
                body_rows = [r[:CELLS_MAX] for r in rs[:ROWS_MAX] if isinstance(r, list)]
            else:
                continue
            if body_rows:
                out.append([cols, *[["" if v is None else str(v) for v in r] for r in body_rows]])
    return out


def guess_blog(url: str, frames: str, ids: str, text: str) -> tuple[str | None, str]:
    """(Blog-Auto 블로그 | None, 네이버 블로그 주소). 주소의 blogId → 책갈피가 찾은 후보 → 글 속 우리 블로그 주소."""
    cands = [m.group(1) for m in _BLOGID.finditer(url + "\n" + frames)]
    cands += [x.strip() for x in ids.split(",") if re.fullmatch(r"[A-Za-z0-9_-]{3,30}", x.strip())]
    for seg in re.split(r"[/?&=#]", url + "/" + frames):
        if seg in BLOG_IDS:
            cands.append(seg)
    for c in cands:
        if c.lower() in BLOG_IDS:
            return BLOG_IDS[c.lower()], c.lower()
    low = (text[:20000]).lower()
    for bid, blog in BLOG_IDS.items():
        if bid in low:
            return blog, bid
    return None, (cands[0] if cands else "")


def guess_kind(url: str, sel: str, title: str, text: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    for src in (sel, path, title, text[:600]):
        for label, rx in KINDS:
            if src and rx.search(src):
                return label
    return "통계"


def guess_date(url: str, sel: str, text: str, today: str) -> str:
    """화면에 보이는 날짜 (주소 → 고른 탭 → 글 머리 4000자). 못 찾거나 이상하면 보낸 날."""
    for src in (url, sel, text[:4000]):
        for rx in (_DATE, _DATE8):
            for m in rx.finditer(src or ""):
                try:
                    d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    continue
                t = datetime.strptime(today, "%Y-%m-%d")
                if t - timedelta(days=400) <= d <= t + timedelta(days=1):
                    return d.strftime("%Y-%m-%d")
    return today


def _day(now: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")


def prune(db: Any, now: float, tz: ZoneInfo) -> int:
    cut = (datetime.fromtimestamp(now, tz) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    return db.kv_delete_prefix(PREFIX, keep=lambda k, _v: k[len(PREFIX):len(PREFIX) + 10] >= cut)


def save(db: Any, form: dict[str, str], *, now: float | None = None, tz: ZoneInfo) -> dict[str, Any]:
    """책갈피가 보낸 폼 → 저장. 돌려줌 {ok, label, kind, blog, day}. 틀리면 CaptureError."""
    if not key_ok(db, form.get("k")):
        raise CaptureError(403, "key", "열쇠가 맞지 않아요 — 대시보드 연결 탭에서 '통계 보내기' 책갈피를 다시 설치해 주세요")
    now = now or time.time()
    sent = _day(now, tz)
    if len(db.kv_items(f"{PREFIX}{sent}:")) >= DAY_MAX:
        raise CaptureError(429, "day_max", f"오늘은 {DAY_MAX}번까지 받았어요 — 내일 다시")
    url = clean(form.get("u"), 600)
    if not re.match(r"https://([a-z0-9-]+\.)*naver\.com(:\d{2,5})?(/|$)", url):
        raise CaptureError(400, "where", "네이버 화면에서 보낸 것만 받아요")
    frames = clean(form.get("f"), 3000)
    title = clean(form.get("t"), 300)
    sel = clean(form.get("s"), 1000)
    text = clean(form.get("x"), TEXT_MAX)
    rows = _rows(str(form.get("r") or "")[:200_000])
    if not text and not rows and not frames:  # 11/12: 메뉴만 읽힌 화면도 iframe 주소(f)는 남김
        raise CaptureError(400, "empty", "화면 글을 못 읽었어요 — 통계가 다 뜬 뒤 다시 눌러 주세요")
    raw_j = str(form.get("j") or "")[:JSON_RAW_MAX]
    if not rows:  # 11/18: 통계 앱 JSON(statDataList — columnInfo + 칸별 줄)을 표 줄로 (조회수 순위·유입분석·검색 유입)
        rows = _rows(json.dumps(json_rows(raw_j), ensure_ascii=False))
    ids = ",".join([str(form.get("b") or "")[:400], *_JSON_BLOGID.findall(raw_j[:200_000])[:3]])  # 11/18: 화면이 부른 user-info 의 blogId
    blog, bid = guess_blog(url, frames, ids, text)
    tab = clean(form.get("n"), KIND_MAX * 3)[:KIND_MAX]
    kind = tab or guess_kind(url, sel, title, text)
    day = guess_date(url, sel, text, sent)
    if (d := str(form.get("d") or "")) and _DAY.fullmatch(d) and guess_date(d, "", "", sent) == d:  # 11/18 책갈피가 받은 날짜 (일간 · 어제·오늘)
        day = d
    name = BLOG_NAMES.get(blog or "", bid or "블로그 모름")
    rec = {"at": int(now), "sent": sent, "day": day, "blog": blog, "blog_id": bid, "blog_name": name, "kind": kind,
           "url": url, "frames": frames, "title": title, "sel": sel, "text": text, "rows": rows}
    if js := clean(form.get("j"), JSON_MAX):
        rec["json"] = js
    k = f"{PREFIX}{sent}:{datetime.fromtimestamp(now, tz).strftime('%H%M%S')}-{secrets.token_hex(2)}"
    db.kv_set(k, json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    prune(db, now, tz)
    label = f"{name} · {kind} · {int(day[5:7])}/{int(day[8:10])}"
    return {"ok": True, "label": label, "kind": kind, "blog": blog, "day": day}


def today_count(db: Any, now: float, tz: ZoneInfo) -> int:
    return len(db.kv_items(f"{PREFIX}{_day(now, tz)}:"))



# ---------------------------------------------------------------- 받은 것 한 줄 · 못 받은 것 (11/17 점검)
# 주인 11/17 '11/16 통계 다 보냈어' — 서버에는 11/13 뒤로 한 개도 없었음 (한 번에 책갈피 첫날). 받은 것은 블로그·화면 날짜로 한 줄,
# 받지 않은 요청(열쇠·빈 화면·한도 …)은 kv statscap:fails 에 최근 FAIL_KEEP 개 (시각·까닭 코드·네이버 주소 앞부분만 — 글은 안 둠).
FAIL_KV = "statscap:fails"
FAIL_KEEP = 20


def note_fail(db: Any, code: str, url: Any = "", *, now: float | None = None) -> None:
    try:
        old = json.loads(db.kv_get(FAIL_KV) or "[]")
    except ValueError:
        old = []
    host = re.sub(r"[?#].*$", "", str(url or ""))[:80]
    row = {"at": int(now or time.time()), "code": str(code)[:20], "u": host}
    db.kv_set(FAIL_KV, json.dumps(([*old, row] if isinstance(old, list) else [row])[-FAIL_KEEP:], ensure_ascii=False, separators=(",", ":")))


def received(db: Any, now: float, tz: ZoneInfo, *, days: int = 7) -> dict[str, Any]:
    """최근 days 일 받은 통계: {today, n, by: {블로그 이름: {n, today, last, dates}}, line}. line = 대시보드 확인 한 줄."""
    t = datetime.fromtimestamp(now, tz)
    since = (t - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    today = t.strftime("%Y-%m-%d")
    by: dict[str, dict[str, Any]] = {}
    n = n_today = 0
    for k, raw in db.kv_items(PREFIX):
        sent = k[len(PREFIX):len(PREFIX) + 10]
        if sent < since:
            continue
        try:
            rec = json.loads(raw or "{}")
        except ValueError:
            continue
        if not isinstance(rec, dict) or rec.get("kind") == "주소 목록":
            continue
        name = str(rec.get("blog_name") or "블로그 모름")
        b = by.setdefault(name, {"n": 0, "today": 0, "last": 0, "dates": set()})
        b["n"] += 1
        n += 1
        if sent == today:
            b["today"] += 1
            n_today += 1
        b["last"] = max(b["last"], int(rec.get("at") or 0))
        if isinstance(rec.get("day"), str) and len(rec["day"]) == 10:
            b["dates"].add(rec["day"])
            v = _view(rec.get("kind"))
            if v:
                b.setdefault("views", {}).setdefault(rec["day"], set()).add(v)
    md = lambda d: f"{int(d[5:7])}/{int(d[8:10])}"  # noqa: E731
    parts, parts_today, views_line = [], [], []
    for name, b in sorted(by.items(), key=lambda x: -x[1]["last"]):
        ds = sorted(b["dates"])
        b["dates"] = ds
        b["last_at"] = datetime.fromtimestamp(b["last"], tz).strftime("%m/%d %H:%M") if b["last"] else "-"
        span = (md(ds[0]) if ds[0] == ds[-1] else f"{md(ds[0])}~{md(ds[-1])}") if ds else "-"
        parts.append(f"{name} {b['n']}개 · 화면 날짜 {span} · 받은 때 {b['last_at']}")
        if b["today"]:
            parts_today.append(f"{name} {b['today']}개 · 받은 때 {b['last_at']}")
        vw = b.pop("views", {})
        b["views"] = {d: [v for v in VIEWS if v in vw[d]] for d in sorted(vw)}
        for d in sorted(vw)[-2:]:  # 블로그·화면 날짜마다 세 화면 중 온 것 (최근 두 날)
            miss = [v for v in VIEWS if v not in vw[d]]
            views_line.append(f"{name} {md(d)} " + ("·".join(v for v in VIEWS if v in vw[d])) + (f" (없음: {'·'.join(miss)})" if miss else " ✓"))
    if n_today:
        line = f"오늘 통계 {n_today}개 받았어요 ({' / '.join(parts_today)})"
    elif n:
        line = f"오늘 받은 통계 0개 · 최근 {days}일 {n}개 ({' / '.join(parts)})"
    else:
        line = f"최근 {days}일 받은 통계 0개 — 네이버 내 통계 화면(blog.stat.naver.com)에서 책갈피"
    if n:
        line += " · 세 화면: " + (" / ".join(views_line) if views_line else "아직 없음 (조회수 순위·유입분석·검색 유입 — 한 번에 v3)")
    return {"today": n_today, "n": n, "by": by, "line": line}


def _view(kind: Any) -> str | None:
    """화면 종류 → VIEWS 중 하나 (옛 이름 '유입 분석'·'검색어' 도) · 전날·안쪽 탭 꼬리(' · …')는 뺌."""
    k = re.sub(r"\s+", "", str(kind or "").split(" · ")[0])
    return {"조회수순위": VIEWS[0], "유입분석": VIEWS[1], "검색유입": VIEWS[2], "검색어": VIEWS[2]}.get(k)
