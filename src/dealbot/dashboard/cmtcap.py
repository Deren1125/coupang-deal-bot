"""독자 댓글 모으기 (주인 11/27 — '댓글 모아 글 방향 보완' · 서버는 네이버에 접속하지 않음 · 자동 답글·공감 없음).

'한 번에' 책갈피(one.js)가 모바일 블로그 화면(m.blog.naver.com — 주인 브라우저 · 이웃 관리 자리 바로 앞)에서 하루 한 번
static/cmtcap.js 를 돌려 우리 세 블로그의 최근 글(공개 글 목록 /api/blogs/<id>/post-list)에 달린 지난 7일 댓글(공개 댓글창 cbox ·
블로그마다 100개까지)을 읽어 POST /dashboard/api/comments_capture 로 보낸다 (열쇠·CORS·크기·횟수 한도는 stats_capture 와 같게 —
열쇠는 '통계 보내기'의 것 그대로: statscap.key).
남기는 것은 댓글 글·시각·글 번호·글 제목·주인 답글 여부만 — 댓글 쓴 사람의 아이디·별명·프로필 주소는 책갈피가 보내지 않고 서버도 받지 않는다
(아래 칸 외에는 버림 · 글 속 이메일·전화번호는 statscap.clean 이 가림).
kv:
  reader_cmt:<블로그>:<댓글 번호>  = {blog, no, post, title, at(댓글 시각 epoch), text, lv(1 댓글·2 답글), replied(주인 답글 0/1), seen, last}
                                    KEEP_DAYS 지나면 지움 · 다시 오면 replied·title·last 만 바뀜
  reader_cmt_last:<블로그>        = {at, day, n, new, posts, diag} 마지막으로 받은 때 (diag = 책갈피가 못 읽은 단계의 짧은 진단 — 화면 글 없음)
  reader_cmt_day:<블로그>:<날>    = 그날 받은 횟수 (DAY_MAX)
  reader_cite:<블로그>:<날>       = {blog, day, n, raw, at} 그날 AI 브리핑 인용 수 (11/27 v6 — 같은 POST 의 qn·qt · 블로그 프로필 '7.2천 인용' ·
                                    천=1,000 만=10,000 · 같은 날 다시 오면 덮어씀 · CITE_KEEP_DAYS)
  reader_cite_last:<블로그>       = {at, day, n?, raw?, diag?} 마지막 시도 (못 찾았으면 diag = '인용'이 든 칸 짧은 진단)
Blog-Auto reader_comments.py 가 읽기 전용으로 읽어 나눠 본다 (오류 지적·요청·말투·칭찬) — stats_capture 와 같은 길 (딜봇 DB kv)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import statscap

SRC = Path(__file__).resolve().parent / "static" / "cmtcap.js"  # one.py 가 '@@CMT@@' 자리에 (주소 @@CURL@@ · 열쇠 @@SKEY@@ = 통계 것)
PREFIX = "reader_cmt:"
LAST_PREFIX = "reader_cmt_last:"
DAY_PREFIX = "reader_cmt_day:"
PATH = "/dashboard/api/comments_capture"
KEEP_DAYS = 45
SINCE_DAYS = 8  # 책갈피는 7일 — 시계가 조금 달라도 받게
PER_BLOG_MAX = 100
DAY_MAX = 6  # 블로그마다 하루 (책갈피는 하루 한 번 · 다시 누름·실패 다시 보내기 몫)
TEXT_MAX = 600
TITLE_MAX = 100
DIAG_MAX = 600
BODY_MAX = 400_000
CITE_PREFIX = "reader_cite:"
CITE_LAST_PREFIX = "reader_cite_last:"
CITE_KEEP_DAYS = 120
CITE_MAX = 100_000_000
_CITE = re.compile(r"(\d[\d,.]*)\s?([천만])?\s?인용|인용\s?(\d[\d,.]*)\s?([천만])?")
_NUM = re.compile(r"\d{1,20}")


def _at(s: Any) -> float | None:
    """'2026-11-20T09:00:00+0900' (cbox regTime) → epoch. 못 읽으면 None."""
    t = str(s or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    return None


def _items(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    return [x for x in data if isinstance(x, dict)][:PER_BLOG_MAX * 2] if isinstance(data, list) else []


def cite_num(raw: Any) -> int | None:
    """'7.2천 인용' → 7200 · '1.2만 인용' → 12000 · '인용 1,345' → 1345 · 'citeCount:345' (JSON 칸) → 345. 못 읽으면 None."""
    t = " ".join(str(raw or "").split())
    m = _CITE.search(t)
    num, unit = (m.group(1) or m.group(3), m.group(2) or m.group(4)) if m else ((re.search(r"[:=](\d[\d,]*)$", t) or [None, None])[1], None)
    try:
        v = float(str(num).replace(",", ""))
    except (TypeError, ValueError):
        return None
    n = round(v * (10000 if unit == "만" else 1000 if unit == "천" else 1))
    return n if 0 <= n <= CITE_MAX else None


def _save_cite(db: Any, blog: str, form: dict[str, str], now: float, day: str) -> int | None:
    """책갈피가 같이 보낸 인용 수 (qn·qt·qg). 글(qt)을 여기서 다시 읽고, 못 읽으면 책갈피 정수(qn). 아무 칸도 없으면 (옛 책갈피) 아무것도 안 함."""
    raw = statscap.clean(form.get("qt"), 120)[:60]
    diag = statscap.clean(form.get("qg"), 800)[:400]
    if not raw and not diag and not form.get("qn"):
        return None
    n = cite_num(raw) if raw else None
    if n is None and raw and re.fullmatch(r"\d{1,9}", str(form.get("qn") or "")) and int(form["qn"]) <= CITE_MAX:
        n = int(form["qn"])
    last: dict[str, Any] = {"at": int(now), "day": day}
    if n is not None:
        rec = {"blog": blog, "day": day, "n": n, "raw": raw, "at": int(now)}
        db.kv_set(f"{CITE_PREFIX}{blog}:{day}", json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        last.update(n=n, raw=raw)
    else:
        last["diag"] = diag or (f"못 읽음: {raw}" if raw else "없음")
    db.kv_set(f"{CITE_LAST_PREFIX}{blog}", json.dumps(last, ensure_ascii=False, separators=(",", ":")))
    return n


def save(db: Any, form: dict[str, str], *, now: float | None = None, tz: ZoneInfo) -> dict[str, Any]:
    """책갈피가 보낸 한 블로그의 댓글 → kv. 돌려줌 {ok, label, blog, n, new}. 틀리면 statscap.CaptureError."""
    if not statscap.key_ok(db, form.get("k")):
        raise statscap.CaptureError(403, "key", "열쇠가 맞지 않아요 — 대시보드 연결 탭에서 '한 번에' 책갈피를 다시 설치해 주세요")
    now = now or time.time()
    url = statscap.clean(form.get("u"), 300)
    if not re.match(r"https://([a-z0-9-]+\.)*naver\.com(/|$)", url):
        raise statscap.CaptureError(400, "where", "네이버 화면에서 보낸 것만 받아요")
    bid = str(form.get("b") or "").strip().lower()
    blog = statscap.BLOG_IDS.get(bid)
    if not blog:
        raise statscap.CaptureError(400, "blog", "우리 블로그 댓글만 받아요")
    day = datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")
    dk = f"{DAY_PREFIX}{blog}:{day}"
    sent = int(db.kv_get(dk) or 0)
    if sent >= DAY_MAX:
        raise statscap.CaptureError(429, "day_max", f"오늘은 {DAY_MAX}번까지 받았어요 — 내일 다시")
    since = now - SINCE_DAYS * 86400
    got, new, posts = 0, 0, set()
    for c in _items(str(form.get("c") or "")[:BODY_MAX]):
        no, post = str(c.get("no") or ""), str(c.get("p") or "")
        at = _at(c.get("at"))
        text = statscap.clean(c.get("x"), TEXT_MAX * 3)[:TEXT_MAX]
        if not _NUM.fullmatch(no) or not _NUM.fullmatch(post) or at is None or not since <= at <= now + 3600 or not text:
            continue
        if got >= PER_BLOG_MAX:
            break
        k = f"{PREFIX}{blog}:{no}"
        try:
            old = json.loads(db.kv_get(k) or "null")
        except ValueError:
            old = None
        rec = {"blog": blog, "no": no, "post": post, "title": statscap.clean(c.get("t"), TITLE_MAX * 3)[:TITLE_MAX],
               "at": int(at), "text": text, "lv": 2 if str(c.get("lv")) == "2" else 1,
               "replied": 1 if str(c.get("r")) == "1" else 0,
               "seen": int(old["seen"]) if isinstance(old, dict) and old.get("seen") else int(now), "last": int(now)}
        if isinstance(old, dict) and old.get("replied"):
            rec["replied"] = 1  # 한 번 답한 댓글은 답한 것으로 (답글 쪽이 다음 쪽으로 밀려도)
        new += not isinstance(old, dict)
        got += 1
        posts.add(post)
        db.kv_set(k, json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
    diag = statscap.clean(form.get("g"), DIAG_MAX * 2)[:DIAG_MAX]
    last = {"at": int(now), "day": day, "n": got, "new": new, "posts": len(posts), **({"diag": diag} if diag else {})}
    db.kv_set(f"{LAST_PREFIX}{blog}", json.dumps(last, ensure_ascii=False, separators=(",", ":")))
    db.kv_set(dk, str(sent + 1))
    cite = _save_cite(db, blog, form, now, day)
    prune(db, now, tz)
    name = statscap.BLOG_NAMES.get(blog, blog)
    return {"ok": True, "label": f"{name} 댓글 {got}개 (새 {new})" + (f" · 인용 {cite:,}" if cite is not None else ""), "blog": blog, "n": got, "new": new,
            **({"cite": cite} if cite is not None else {})}


def prune(db: Any, now: float, tz: ZoneInfo) -> int:
    cut = now - KEEP_DAYS * 86400

    def keep(_k: str, v: str | None) -> bool:
        try:
            return float(json.loads(v or "{}").get("at") or 0) >= cut
        except (ValueError, AttributeError):
            return False

    day_cut = (datetime.fromtimestamp(now, tz) - timedelta(days=3)).strftime("%Y-%m-%d")
    n = db.kv_delete_prefix(PREFIX, keep=keep)
    cite_cut = (datetime.fromtimestamp(now, tz) - timedelta(days=CITE_KEEP_DAYS)).strftime("%Y-%m-%d")
    n += db.kv_delete_prefix(CITE_PREFIX, keep=lambda k, _v: k.rsplit(":", 1)[-1] >= cite_cut)
    return n + db.kv_delete_prefix(DAY_PREFIX, keep=lambda k, _v: k.rsplit(":", 1)[-1] >= day_cut)


def received_line(db: Any, now: float, tz: ZoneInfo) -> str:
    """연결 탭 '한 번에' 칸 확인 한 줄 — '댓글: 데런픽 11/27 09:12 12개 · …'."""
    parts = []
    for blog in ("issue", "finance", "hotdeal"):
        try:
            last = json.loads(db.kv_get(f"{LAST_PREFIX}{blog}") or "null")
        except ValueError:
            last = None
        name = statscap.BLOG_NAMES.get(blog, blog)
        if not isinstance(last, dict):
            parts.append(f"{name} 아직")
            continue
        at = datetime.fromtimestamp(float(last.get("at") or 0), tz).strftime("%m/%d %H:%M")
        try:
            q = json.loads(db.kv_get(f"{CITE_LAST_PREFIX}{blog}") or "null")
        except ValueError:
            q = None
        qs = "" if not isinstance(q, dict) else f" · 인용 {int(q['n']):,}" if q.get("n") is not None else " · 인용 못 찾음"
        parts.append(f"{name} {at} {int(last.get('n') or 0)}개" + (" (못 읽은 것 있음)" if last.get("diag") else "") + qs)
    return "댓글: " + " · ".join(parts)
