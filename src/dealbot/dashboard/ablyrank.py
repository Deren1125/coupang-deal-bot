"""'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 코디 랭킹 캡처 올리기(LB-15) 대신 · 서버는 에이블리·그 사진 서버를 열지 않음, 10/28 결정).

static/ablyrank.js: 주인 브라우저(아이패드 사파리·갤럭시 크롬)에서 연 에이블리 랭킹 화면(a-bly.com · *.a-bly.com)의 보이는 순위 카드
  (순위·상품명·쇼핑몰·가격·할인·리뷰·별점·찜·상품 주소·사진 주소)·코디 카드(제목·좋아요·든 상품)·주소·제목·고른 탭·보이는 글을
  POST /dashboard/api/ably_rank_capture (폼 — 쿠키 없음) 로 보낸다. 열쇠 k = HMAC(kv ablyrank_secret, "ablyrank") —
  연결 탭 'Ably 랭킹 보내기 버튼 설치'(로그인 아래 GET /dashboard/api/ablyrank)에서만 보임. kv 를 지우면 예전 책갈피는 안 받음.
save(): 열쇠 · 에이블리 주소만 · 칸마다 크기·모양 확인(주소는 https 만, 상품·코디 주소는 a-bly.com 만) · 상품 100 · 코디 50(코디마다 상품 12) ·
  기록 60KB → kv "ably_rank_capture:<보낸 날 KST>:<HHMMSS>-<4hex>" = JSON {at, sent, url, title, sel, heads, tab, items, looks, text}. 30일.
  사진 주소는 적기만 한다 (서버가 받아 오지 않음).
넘기기: Blog-Auto 가 상태 파일 actions 에 'ably_rank_capture' 를 알리면 같은 룩북 감독 길로 일 하나
  (계약 action_ably_rank_capture.json — {captures: [kv 키]}, Blog-Auto 가 딜봇 DB 에서 읽음). 알리기 전에는 kv 에만 둔다.
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
from dealbot.dashboard.statscap import CaptureError, clean

SRC = Path(__file__).resolve().parent / "static" / "ablyrank.js"
KV_SECRET = "ablyrank_secret"
PREFIX = "ably_rank_capture:"
PATH = "/dashboard/api/ably_rank_capture"
ACTION = "ably_rank_capture"  # Blog-Auto 가 이 이름을 알려야 넘김
KEEP_DAYS = 30
DAY_MAX = 30
BODY_MAX = 600_000
JSON_MAX = 200_000  # 받는 j (글자)
REC_MAX = 60_000  # 저장하는 기록 (UTF-8 바이트)
TEXT_MAX = 15_000
ITEMS_MAX = 100
LOOKS_MAX = 50
LOOK_ITEMS_MAX = 12
ABLY_URL = re.compile(r"https://([a-z0-9-]+\.)*a-bly\.com(/[^\s<>\"']*)?")
HTTPS_URL = re.compile(r"https://[A-Za-z0-9.\-]+(:\d+)?(/[^\s<>\"'\\]*)?")
COUNT = re.compile(r"[\d,.]{1,10}[만천kK]?\+?")
INSTALL_STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "브라우저에서 아무 페이지나 연 뒤 책갈피(북마크) 추가 → 이름을 'Ably 랭킹 보내기'로 저장해요.",
    "책갈피 목록 → '편집' → 'Ably 랭킹 보내기'의 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'.",
    "쓰는 법: 브라우저에서 에이블리(a-bly.com) 랭킹·코디 랭킹 화면을 열고 책갈피 'Ably 랭킹 보내기' → 'Ably 랭킹 보냄 ✓' 알림이면 끝. "
    "더 보고 싶으면 아래로 내린 뒤 한 번 더 (캡처 올리기는 그대로 써도 돼요).",
)


def _secret(db: Any) -> bytes:
    k = db.kv_get(KV_SECRET)
    if not k:
        k = secrets.token_hex(32)
        db.kv_set(KV_SECRET, k)
    return bytes.fromhex(k)


def key(db: Any) -> str:
    mac = hmac.new(_secret(db), b"ablyrank", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def key_ok(db: Any, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(key(db).encode(), str(given).encode())


@lru_cache(maxsize=1)
def _code() -> str:
    return kkumi.minify(SRC.read_text(encoding="utf-8"))


def bookmarklet(endpoint: str, k: str) -> str:
    if not re.fullmatch(r"https://[A-Za-z0-9.\-:]+/[A-Za-z0-9_/\-]+", endpoint) or not re.fullmatch(r"[A-Za-z0-9_\-]+", k):
        raise ValueError("bad endpoint or key")
    return "javascript:" + kkumi.encode(_code().replace("@@URL@@", endpoint).replace("@@KEY@@", k))


def view(db: Any, public_url: str) -> dict[str, Any]:
    code = bookmarklet(public_url.rstrip("/") + PATH, key(db))
    return {"code": code, "len": len(code), "steps": list(INSTALL_STEPS)}


# ---------------------------------------------------------------- 받기
def _url(v: Any, ably_only: bool) -> str:
    s = str(v or "").strip()[:600]
    rx = ABLY_URL if ably_only else HTTPS_URL
    return s if rx.fullmatch(s) else ""


def _int(v: Any, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    n = int(v)
    return n if lo <= n <= hi else None


def _item(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    o: dict[str, Any] = {}
    for k, lim in (("name", 200), ("shop", 80)):
        if s := clean(v.get(k), lim * 3)[:lim]:
            o[k] = s
    for k, lo, hi in (("rank", 1, 999), ("price", 100, 99_999_999), ("orig", 100, 99_999_999), ("disc", 1, 99)):
        if (n := _int(v.get(k), lo, hi)) is not None:
            o[k] = n
    if isinstance(v.get("star"), int | float) and not isinstance(v.get("star"), bool) and 0 <= v["star"] <= 5:
        o["star"] = round(float(v["star"]), 1)
    for k in ("rev", "like"):
        s = str(v.get(k) or "")[:12]
        if COUNT.fullmatch(s):
            o[k] = s
    if u := _url(v.get("url"), True):
        o["url"] = u
    if u := _url(v.get("img"), False):
        o["img"] = u
    if "orig" in o and ("price" not in o or o["orig"] <= o["price"]):
        del o["orig"]
    return o if (o.get("name") or o.get("url") or o.get("price")) else None


def _look(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict):
        return None
    o: dict[str, Any] = {}
    if s := clean(v.get("title"), 600)[:200]:
        o["title"] = s
    if s := clean(v.get("shop"), 240)[:80]:
        o["shop"] = s
    if (n := _int(v.get("rank"), 1, 999)) is not None:
        o["rank"] = n
    if COUNT.fullmatch(s := str(v.get("like") or "")[:12]):
        o["like"] = s
    if u := _url(v.get("url"), True):
        o["url"] = u
    if u := _url(v.get("img"), False):
        o["img"] = u
    raw = v.get("items") if isinstance(v.get("items"), list) else []
    o["items"] = [x for x in (_item(i) for i in raw[:LOOK_ITEMS_MAX * 2]) if x][:LOOK_ITEMS_MAX]
    return o if (o.get("title") or o.get("url") or o["items"]) else None


def parse_cards(raw: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    items = data.get("items") if isinstance(data.get("items"), list) else []
    looks = data.get("looks") if isinstance(data.get("looks"), list) else []
    its = [x for x in (_item(i) for i in items[:ITEMS_MAX * 2]) if x][:ITEMS_MAX]
    lks = [x for x in (_look(i) for i in looks[:LOOKS_MAX * 2]) if x][:LOOKS_MAX]
    for n, it in enumerate(its, 1):
        it["pos"] = n  # 화면 차례 (순위 숫자가 안 보이는 화면)
    for n, lk in enumerate(lks, 1):
        lk["pos"] = n
    return its, lks


def _fit(rec: dict[str, Any]) -> str:
    """REC_MAX 바이트 안으로 — 글부터 줄이고, 그다음 끝 상품·코디부터 뺀다."""
    def dump() -> str:
        return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    s = dump()
    while len(s.encode()) > REC_MAX and rec["text"]:
        rec["text"] = rec["text"][: len(rec["text"]) // 2]
        s = dump()
    while len(s.encode()) > REC_MAX and (rec["items"] or rec["looks"]):
        (rec["items"] if len(rec["items"]) >= len(rec["looks"]) else rec["looks"]).pop()
        s = dump()
    return s


def _day(now: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")


def prune(db: Any, now: float, tz: ZoneInfo) -> int:
    cut = (datetime.fromtimestamp(now, tz) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    return db.kv_delete_prefix(PREFIX, keep=lambda k, _v: k[len(PREFIX):len(PREFIX) + 10] >= cut)


def save(db: Any, form: dict[str, str], *, now: float | None = None, tz: ZoneInfo) -> dict[str, Any]:
    """책갈피 폼 → kv. 돌려줌 {ok, label, key, items, looks}. 틀리면 CaptureError."""
    if not key_ok(db, form.get("k")):
        raise CaptureError(403, "key", "열쇠가 맞지 않아요 — 대시보드 연결 탭에서 'Ably 랭킹 보내기' 책갈피를 다시 설치해 주세요")
    now = now or time.time()
    sent = _day(now, tz)
    if len(db.kv_items(f"{PREFIX}{sent}:")) >= DAY_MAX:
        raise CaptureError(429, "day_max", f"오늘은 {DAY_MAX}번까지 받았어요 — 내일 다시")
    url = _url(form.get("u"), True)
    if not url:
        raise CaptureError(400, "where", "에이블리(a-bly.com) 화면에서 보낸 것만 받아요")
    items, looks = parse_cards(str(form.get("j") or "")[:JSON_MAX])
    text = clean(form.get("x"), TEXT_MAX)
    if not items and not looks and not text:
        raise CaptureError(400, "empty", "화면을 못 읽었어요 — 랭킹이 다 뜬 뒤 다시 눌러 주세요")
    sel = [s for s in clean(form.get("s"), 1000).splitlines() if s][:12]
    heads = [s for s in clean(form.get("h"), 1000).splitlines() if s][:8]
    rec = {"at": int(now), "sent": sent, "url": url, "title": clean(form.get("t"), 300), "sel": sel, "heads": heads,
           "tab": sel[0] if sel else "", "items": items, "looks": looks, "text": text}
    body = _fit(rec)
    k = f"{PREFIX}{sent}:{datetime.fromtimestamp(now, tz).strftime('%H%M%S')}-{secrets.token_hex(2)}"
    db.kv_set(k, body)
    prune(db, now, tz)
    parts = [f"상품 {len(rec['items'])}"] + ([f"코디 {len(rec['looks'])}"] if rec["looks"] else [])
    if rec["tab"]:
        parts.append(rec["tab"][:20])
    return {"ok": True, "label": " · ".join(parts), "key": k, "items": len(rec["items"]), "looks": len(rec["looks"])}


def hand_off(outbox: Any, key_: str) -> str | None:
    """Blog-Auto 가 ACTION 을 알리면 룩북 감독 길로 넘김 → 일 id. 아니면(예전 Blog-Auto)·바쁘면 None (kv 에는 있음)."""
    try:
        if outbox is None or ACTION not in (outbox.status().actions or []):
            return None
        aid, _dup = outbox.submit(action=ACTION, blog=None, args={"captures": [key_]}, target=f"ably-rank:{key_[len(PREFIX):]}",
                                  confirmed=False, session="bookmarklet", cid=None)
        return aid
    except Exception:  # noqa: BLE001 — 넘기기가 안 돼도 받은 것은 kv 에
        return None


def today_count(db: Any, now: float, tz: ZoneInfo) -> int:
    return len(db.kv_items(f"{PREFIX}{_day(now, tz)}:"))
