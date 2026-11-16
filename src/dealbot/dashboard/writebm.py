"""'글쓰기 한 번에' 책갈피 (주인 11/16 — 네이버 글쓰기에서 한 번 누르면 오늘 붙여넣기 대기 글을 차례로: 제목·본문 → 꾸미기 → 사진 → 예약 시각 →
멈춤 · 주인은 글마다 '발행'만 누름 → 발행되면 다음 글을 스스로 엶 → 끝나면 할 일 줄 체크 + 요약).

서버는 네이버를 열지도 로그인하지도 않는다 — 모든 일은 주인이 로그인한 자기 브라우저(아이패드 사파리)에서. 발행·예약을 끝내는 단추는 책갈피가 누르지 않는다
(꾸미기 kkumi.js 의 tap() 막이 그대로 · 주인이 누른 것을 지켜보기만 함). 네이버가 자동입력 방지·사람 확인을 보이면 풀지 않고 그 글은 임시저장 → '폰에서 직접'.

책갈피 = 진행기 코드 통째 (메인 11/16 — 원격 코드 실행 금지): 꾸미기·한 번에 책갈피처럼 코드를 책갈피 주소에 다 넣는다 (kkumi.minify + encode_short).
  대시보드는 코드를 내려 주지 않는다 (new Function·eval 없음 — 대시보드가 뚫려도 네이버 로그인 화면에서 새 코드가 돌지 않음) · write_act 는 데이터만.
  고치면 책갈피를 다시 깔아야 함 (판 번호 V · 끝 요약 'v<n>').
진행기 = static/write.js (껍데기) 의 '@@KKUMI@@' 자리에 static/kkumi.js 를 통째로 (코드를 다시 쓰지 않음 — PATCHES 네 줄만 바꿈):
  ① 책갈피 머리 → 함수 (화면·알림·묻기를 껍데기가 넘김 — 알림·확인 창 0개) ② 붙여넣기 칸을 띄우지 않고 받은 글을 바로 ③ 글을 넣기 전에 껍데기의 제목 넣기
  ④ 꼬리. kkumi.js 에서 그 줄이 사라지면 ValueError (test_dash_writebm 이 지킴 — kkumi.js 를 고칠 때 같이).
페이지 이동에도 이어가기: 진행기는 누른 화면(blog.naver.com)에 남고, 글쓰기는 그 안의 같은 출처 iframe 으로 글마다 새로 연다 (write.js 머리 주석).

POST /dashboard/api/write_act (폼 · 로그인 없이 · 열쇠 k = HMAC(kv writebm_secret) · CORS 는 https://*.blog.naver.com 만 · 이 길만 · 데이터만):
  a=next  b=블로그  {ok, next: {id, title, html, text, group, when, photos} | None, left, wait(초), text} — 오늘 할 일의 그 블로그 발행·예약 줄 가운데
                   체크 안 됨 · 다 만든 묶음(만드는 중·점검에 걸린 것 뺌) · 오늘 이미 해 본 것(tried) 뺌. 오늘 목록이 아니면 next 없음.
                   wait = 앞 글을 끝낸 뒤 30~60초(GAP · 무작위)가 되기까지 남은 초 — 진행기가 그만큼 쉰 뒤 다음 글을 연다.
  a=done  b id r   r: ok(발행·예약 확인 — 할 일 줄 체크 + 블로그 프로그램에 pasted · 손으로 체크할 때와 같은 길 routine.set_done·relay_add) ·
                   unsure(발행은 눌렀는데 확인 못 함 — 체크하지 않음) · phone(자동입력 방지 등 — 폰에서 직접) · fail(못 넣음 — 다음에 다시).
                   p=1 사진은 직접 · n=한마디. ok·unsure·phone 은 tried 에 넣어 오늘은 다시 주지 않는다 (같은 글 두 번 발행 막기).
                   진행기는 '준비 끝'(발행 단추를 누를 수 있게 되기) 직전에 r=unsure n='발행 기다림' 을 먼저 보낸다 — 발행 뒤 탭이 닫혀
                   ok 를 못 보내도 다시 눌렀을 때 같은 글을 또 주지 않게 (못 보내면 그 글은 멈춤).
kv: "writebm:day:<날 KST>" = {tried: {줄 id: r}, last, gap, res: [{id, b, r, p, n, at}]} (그날·전날만) · today() = 연결 탭 설치 칸의 한 줄.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import random
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import kkumi, seoi
from dealbot.dashboard.statscap import CaptureError

STATIC = Path(__file__).resolve().parent / "static"
SRC = STATIC / "write.js"
KV_SECRET = "writebm_secret"
DAY_KEY = "writebm:day:"
PATH = "/dashboard/api/write_act"
# 주인 11/16 14시 "꾸미기 책갈피 코드만" — 설정 dashboard.write_bm (기본 false) 이면 설치 칸이 숨고 GET writebm·POST write_act 가 403 으로 이 한 줄만
OFF_TEXT = "이제 안 써요 — 꾸미기 책갈피를 쓰세요"
BODY_MAX = 4000
GAP = (30, 60)  # 글 사이 쉬는 초 (주인 11/16 '글 사이 30~60초')
RESULTS = ("ok", "unsure", "phone", "fail")
TRIED = ("ok", "unsure", "phone")  # 오늘은 다시 주지 않는 결과
RES_MAX = 60
NOTE_MAX = 80
GROUPS = ("발행", "예약")
ROW_RE = re.compile(r"^(?:pub|rsv):(finance|issue|hotdeal):(\d{1,9})$")  # actions.PASTE_ROW_RE 와 같게 (test_dash_writebm 이 지킴)
NAMES = seoi.NAMES
# kkumi.js 의 옛 줄 → 진행기 안에서만 바뀌는 줄 (딱 한 번씩 나와야 함)
PATCHES: tuple[tuple[str, str], ...] = (
    ("(async () => {\n", "async (document, alert, confirm, GOT, PRE) => {\n"),
    ("const got = await ask();\n", "const got = GOT;\n"),
    ("try { window.__kkumiReport = R; } catch (e) {}\n",
     "if (!await PRE({D, W, R, S, vis, press, tap, wait, real, comps, body_, host, setSel, count, tagIn, pubBtn, BOTTOM})) return;\n"),
    ("})().catch(e => alert('꾸미기 오류: ' + e));\n", "}\n"),
)
SLOT = "'@@KKUMI@@'"
STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '글쓰기 한 번에'로 저장해요.",
    "책갈피 목록 → '편집' → '글쓰기 한 번에'의 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'. (코드가 길어요 — 저장이 안 되면 예전처럼 꾸미기 책갈피로)",
    "쓰는 법: 네이버에 로그인한 사파리에서 그 블로그의 글쓰기 화면을 열고 '글쓰기 한 번에'를 한 번 → 오늘 붙여넣기 대기 글을 차례로 가져와 "
    "제목·본문·꾸미기·사진·태그·카테고리·예약 시각까지 채우고 멈춰요 → 화면 위 줄에 '발행만 눌러 주세요'가 뜨면 발행 단추만 눌러요 → "
    "발행되면 30~60초 쉬고 다음 글을 스스로 열어요 → 끝나면 할 일 줄이 체크되고 화면 위에 요약이 남아요.",
    "누를 것은 글마다 '발행' 하나뿐이에요 (확인 창·다른 단추 없음). 화면을 켜 둔 채 그 탭에 두세요. 멈추려면 탭을 닫거나 새로 고침 — 다시 누르면 남은 글부터 이어서 해요.",
    "네이버가 자동입력 방지·사람 확인을 보이면 풀지 않고 그 글은 임시저장한 뒤 다음 글로 넘어가요 — 요약의 '폰에서 직접'에 남은 글만 폰에서 올리면 돼요. "
    "사진을 못 넣은 글은 '사진은 직접'이라고 알려요. 블로그 계정이 여럿이면 계정마다 로그인해서 같은 식으로 (한 번에 로그인된 블로그 하나의 글만 해요).",
)
LEN_MAX = 50000  # 꾸미기(29KB) + 껍데기 = 47KB 남짓 — '한 번에'(45KB)와 비슷 (아이패드 사파리에서 돈 가장 긴 책갈피는 29KB — 저장이 안 되면 꾸미기 책갈피로)
# 책갈피 = 진행기를 그 자리에서 부름 (한 번에 두 번 돌지 않게 · 오류는 화면 위 한 줄 — 알림 창 없음). @@URL@@·@@KEY@@·@@BLOGS@@ 는 bookmarklet() 이 넣음
HEAD = "(async()=>{const W=window;if(W.__wbm)return;W.__wbm=1;try{await("
TAIL = (")({u:'@@URL@@',k:'@@KEY@@',bl:JSON.parse('@@BLOGS@@')})}catch(e){const o=document.createElement('div');"
        "o.style.cssText='position:fixed;left:8px;right:8px;top:8px;z-index:2147483647;padding:14px;border-radius:12px;background:#111;color:#fff;"
        "font:16px/1.5 sans-serif;text-align:center';o.textContent='글쓰기 한 번에 오류: '+e;(document.body||document.documentElement).appendChild(o)}W.__wbm=0})()")


def _secret(db: Any) -> bytes:
    k = db.kv_get(KV_SECRET)
    if not k:
        k = secrets.token_hex(32)
        db.kv_set(KV_SECRET, k)
    return bytes.fromhex(k)


def key(db: Any) -> str:
    mac = hmac.new(_secret(db), b"writebm", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def key_ok(db: Any, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(key(db).encode(), str(given).encode())


@lru_cache(maxsize=1)
def version() -> int:
    m = re.search(r"^const V = (\d+);", SRC.read_text(encoding="utf-8"), re.M)
    return int(m.group(1)) if m else 0


def _core() -> str:
    """꾸미기 책갈피 코드 → 진행기가 부르는 함수 (머리 주석 뺌 · PATCHES)."""
    src = re.sub(r"\A\s*/\*.*?\*/\s*", "", kkumi.SRC.read_text(encoding="utf-8"), count=1, flags=re.S)
    for old, new in PATCHES:
        if src.count(old) != 1:
            raise ValueError(f"kkumi.js 에 없는 줄: {old.strip()[:40]}")
        src = src.replace(old, new)
    return src.rstrip().rstrip(";")


@lru_cache(maxsize=1)
def runner() -> str:
    """진행기 코드 (함수 식 하나 — 'async CFG => {…}')."""
    shell = re.sub(r"\A\s*/\*.*?\*/\s*", "", SRC.read_text(encoding="utf-8"), count=1, flags=re.S)
    if shell.count(SLOT) != 1:
        raise ValueError("write.js 에 꾸미기 자리가 없음")
    return shell.replace(SLOT, _core()).rstrip().rstrip(";")


@lru_cache(maxsize=1)
def _code() -> str:
    return HEAD + kkumi.minify(runner()) + TAIL


def bookmarklet(endpoint: str, k: str) -> str:
    """javascript: 주소 — 진행기 통째 + 주소·열쇠·블로그 표 (따옴표·역슬래시 없는 글자만)."""
    if not re.fullmatch(r"https://[A-Za-z0-9.\-:]+/[A-Za-z0-9_/\-]+", endpoint) or not re.fullmatch(r"[A-Za-z0-9_\-]+", k):
        raise ValueError("bad endpoint or key")
    return "javascript:" + kkumi.encode_short(_code().replace("@@URL@@", endpoint).replace("@@KEY@@", k).replace("@@BLOGS@@", seoi.blogs_json()))


def _day(now: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")


def _load(db: Any, day: str) -> dict[str, Any]:
    try:
        v = json.loads(db.kv_get(DAY_KEY + day) or "{}")
    except (TypeError, ValueError):
        v = {}
    v = v if isinstance(v, dict) else {}
    tried = v.get("tried") if isinstance(v.get("tried"), dict) else {}
    res = v.get("res") if isinstance(v.get("res"), list) else []
    last, gap = v.get("last"), v.get("gap")
    return {"tried": {str(k): str(x) for k, x in tried.items()}, "res": [r for r in res if isinstance(r, dict)][-RES_MAX:],
            "last": float(last) if isinstance(last, int | float) else 0.0, "gap": float(gap) if isinstance(gap, int | float) else 0.0}


def _save(db: Any, day: str, v: dict[str, Any]) -> None:
    db.kv_set(DAY_KEY + day, json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    try:
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        db.kv_delete_prefix(DAY_KEY, keep=lambda k, _v: k in (DAY_KEY + day, DAY_KEY + prev))
    except (ValueError, AttributeError):
        pass


def today(db: Any, now: float, tz: ZoneInfo) -> dict[str, Any]:
    """오늘 한 것 {ok, unsure, phone, fail, photo, note} — note 는 한 것이 없으면 ''."""
    c = _load(db, _day(now, tz))
    last = {str(r.get("id")): r for r in c["res"]}  # 같은 글은 마지막 결과만
    n = {r: sum(1 for x in last.values() if x.get("r") == r) for r in RESULTS}
    photo = sum(1 for x in last.values() if x.get("p"))
    bits = [f"발행·예약 {n['ok']}"] + [f"{t} {v}" for t, v in (("확인 필요", n["unsure"]), ("폰에서 직접", n["phone"]), ("못 넣음", n["fail"]), ("사진은 직접", photo)) if v]
    return {**n, "photo": photo, "note": "오늘 글쓰기 한 번에: " + " · ".join(bits) if last else ""}


def view(db: Any, public_url: str, *, now: float | None = None, tz: ZoneInfo | None = None) -> dict[str, Any]:
    code = bookmarklet(public_url.rstrip("/") + PATH, key(db))
    if len(code) > LEN_MAX:
        raise ValueError("bookmarklet too long")
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": list(STEPS), "version": version()}
    if tz is not None and (note := today(db, now or time.time(), tz)["note"]):
        out["note"] = note
    return out


def pending(rv: Any, blog: str, tried: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """routine.view() 값에서 그 블로그의 붙여넣기 대기 글 (화면 차례 그대로). 오늘 목록이 아니면 []."""
    if not isinstance(rv, dict) or not rv.get("today"):
        return []
    out = []
    for s in rv.get("slots") or []:
        for i in s.get("items") or []:
            p = i.get("paste")
            if (i.get("group") in GROUPS and i.get("blog") == blog and not i.get("done") and isinstance(p, dict) and not p.get("stale") and p.get("html")
                    and (m := ROW_RE.match(str(i.get("id") or ""))) and m.group(1) == blog and i["id"] not in (tried or {})
                    and not (isinstance(i.get("gate"), dict) and i["gate"].get("ok") is False)):
                out.append(i)
    return out


def _blog(form: dict[str, str]) -> str:
    b = str(form.get("b") or "")
    if b not in NAMES:
        raise CaptureError(400, "who", "어느 블로그인지 몰라요 — 우리 블로그 계정으로 로그인한 글쓰기 화면에서 눌러 주세요")
    return b


def act(db: Any, form: dict[str, str], *, tz: ZoneInfo, now: float | None = None, rv: Any = None, rng: Any = random) -> dict[str, Any]:
    """책갈피가 보낸 폼 하나 (데이터만 — 코드는 내려 주지 않음). rv = routine.view() 값 (a=next·done 일 때 · 호출하는 쪽이 열쇠를 본 뒤 읽어 넘김)."""
    if not key_ok(db, form.get("k")):
        raise CaptureError(403, "key", "열쇠가 맞지 않아요 — 대시보드 연결 탭에서 '글쓰기 한 번에' 책갈피를 다시 설치해 주세요")
    now = now or time.time()
    a = str(form.get("a") or "")
    day = _day(now, tz)
    if a == "next":
        blog = _blog(form)
        c = _load(db, day)
        rows = pending(rv, blog, c["tried"])
        if not rows:
            fresh = isinstance(rv, dict) and rv.get("today")
            return {"ok": True, "next": None, "left": 0, "wait": 0,
                    "text": f"{NAMES[blog]}: 오늘 붙여 넣을 글이 더 없어요" if fresh else "오늘 할 일 목록이 아직 없어요 — 잠시 뒤 다시 눌러 주세요"}
        i, p = rows[0], rows[0]["paste"]
        w = i.get("when") if isinstance(i.get("when"), dict) else None
        return {"ok": True, "left": len(rows), "wait": max(0, math.ceil(c["last"] + c["gap"] - now)) if c["last"] else 0,
                "text": f"{NAMES[blog]}: 남은 글 {len(rows)}",
                "next": {"id": i["id"], "title": p["title"], "html": p["html"], "text": p.get("text") or "", "group": i["group"],
                         "when": str(w.get("label") or "") if w else "", "photos": int(p.get("photos") or 0)}}
    if a == "done":
        blog, rid, r = _blog(form), str(form.get("id") or ""), str(form.get("r") or "")
        m = ROW_RE.match(rid)
        if not m or m.group(1) != blog or r not in RESULTS:
            raise CaptureError(400, "bad", "모르는 요청이에요")
        c = _load(db, day)
        checked = False
        if r == "ok" and any(i["id"] == rid for i in pending(rv, blog)):  # 오늘 그 블로그의 대기 줄일 때만 체크 (열쇠가 있어도 아무 줄이나 못 건드림)
            from dealbot.dashboard import routine

            routine.set_done(db, str(rv["day"]), rid, True)
            routine.relay_add(db, "pasted", {"blog": blog, "pid": int(m.group(2)), "done": True}, rid, now)
            checked = True
        if r in TRIED:
            c["tried"][rid] = r
        note = " ".join("".join(ch for ch in str(form.get("n") or "") if ch.isprintable()).split())[:NOTE_MAX]
        c["res"] = [*c["res"], {"id": rid, "b": blog, "r": r, "p": 1 if str(form.get("p") or "") == "1" else 0, "n": note, "at": int(now)}][-RES_MAX:]
        c["last"], c["gap"] = now, float(rng.randint(*GAP))
        _save(db, day, c)
        return {"ok": True, "checked": checked, "text": "할 일 줄 체크 ✓" if checked else ""}
    raise CaptureError(400, "bad", "모르는 요청이에요")

