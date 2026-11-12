"""'통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음 · 주인이 아이패드 사파리 '내 통계'에서 하루 한 번 누름).

static/statscap.js: 이미 로그인된 네이버 내 통계 화면(blog.stat.naver.com · m.blog.naver.com 통계 …)에서 보이는 글·표·주소·제목·고른 탭을
  POST /dashboard/api/stats_capture (폼 — 교차 사이트 '간단한 요청', 쿠키 없음) 로 보낸다. 로그인 대신 책갈피에 든 열쇠 k 로만 받는다:
  key(db) = HMAC(kv statscap_secret, "statscap") — 열쇠는 연결 탭 '통계 보내기 버튼 설치'(로그인 아래 GET /dashboard/api/statscap)에서만 보임.
  kv statscap_secret 을 지우면 예전 책갈피는 더는 안 받음 (다시 설치).
save(): 크기 제한(글 60KB · 표 300줄 · 칸 200자) · 꺾쇠 태그·제어 글자 빼기 · 이메일·전화번호 가리기 → kv
  "stats_capture:<보낸 날 KST>:<HHMMSS>-<4hex>" = JSON {at, sent, day, blog, blog_id, blog_name, kind, url, title, sel, text, rows, frames, json}.
  30일 지나면 지움. 11/12 '한 번에 모두': 책갈피가 메뉴 화면마다 하나씩 보내며 n = 메뉴 이름(→ kind 그대로) · j = 화면이 읽은 같은 출처 JSON.
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
BLOG_NAMES = {"finance": "아카이브", "issue": "데런픽", "hotdeal": "핫딜"}
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
    "쓰는 법: 사파리에서 네이버 블로그 '내 통계'(일간 현황·조회수 순위·유입 분석·검색어·방문 분석 등)를 열고 책갈피 '통계 보내기' → "
    "한 번 누르면 메뉴 화면(일간 현황·유입분석·순위 …)을 차례로 열어 모두 보내요 — 왼쪽 위 '3/18 유입분석…' 이 끝나고 "
    "'통계 보냄 ✓ N개 화면' 알림이면 끝 (1분 남짓 · 하루 한 번이면 충분).",
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


def view(db: Any, public_url: str) -> dict[str, Any]:
    code = bookmarklet(public_url.rstrip("/") + PATH, key(db))
    return {"code": code, "len": len(code), "steps": list(SENDING_STEPS)}


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
    blog, bid = guess_blog(url, frames, str(form.get("b") or "")[:400], text)
    tab = clean(form.get("n"), KIND_MAX * 3)[:KIND_MAX]
    kind = tab or guess_kind(url, sel, title, text)
    day = guess_date(url, sel, text, sent)
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


def nudge(db: Any, now: float, tz: ZoneInfo) -> str | None:
    """할 일 탭 한 줄 — 오늘 보낸 통계가 없으면."""
    if today_count(db, now, tz):
        return None
    return "오늘 통계 보내기 — 사파리 네이버 '내 통계'에서 책갈피 '통계 보내기' (설치: 연결 탭)"
