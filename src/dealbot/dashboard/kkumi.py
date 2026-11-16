"""'꾸미기' 책갈피 (주인 10/27 — 서버가 네이버에 로그인하지 않고, 주인이 아이패드 사파리에서 대시보드 글을 붙여 넣어 발행).

static/kkumi.js = Blog-Auto blogauto/naver/kkumi_v4.js (10/29 아이패드 시험 통과): 글쓰기에서 빈 본문을 한 번 누르고 책갈피를 누르면
칸이 뜨고, 대시보드 '본문 복사'한 글을 그 칸에 붙여 넣으면 위에서부터 조각마다 에디터에 붙이며 짧은 구분선 · 주소 줄 다음 링크 카드 ·
포스트잇 · 발행 설정창 태그까지 넣는다 (아이패드에서는 스크립트로 커서를 옮길 수 없어 맨 끝에 차례로 쌓음 · 바깥 요청 없음).

  bookmarklet(): static/kkumi.js 에서 머리 주석·'//' 주석 줄·들여쓰기·빈 줄을 뺀 코드(minified) → "javascript:" + encode_short
    (encodeURI 와 같되 '#' 은 %23 — 11/15 부터 · 그 전은 encodeURIComponent · 다른 책갈피(통계·이웃 관리 등)는 encode 그대로).
  view(): GET /dashboard/api/kkumi 몸 {code, len, steps, check, use, ver, version, ack} — 연결 탭 '꾸미기 버튼 설치' 칸(logins.js)이 열 때 받아 '코드 복사'.
설치한 책갈피가 옛 판인지 알리기 (주인 11/14 — 책갈피는 주인이 다시 복사·저장해야 바뀌는데 그동안은 바뀐 줄 몰라 옛 판으로 붙여 넣음):
  ver() = 지금 코드의 sha256 앞 10자 · version() = kkumi.js 의 'const V = n' (끝 알림 'v<n>' 과 같은 수) ·
  설치 칸 '설치했어요' 단추 → 대시보드 일 kkumi.installed {ver} → kv "kkumi_ack" = 그 ver (지금 ver 과 같을 때만 받음) ·
  is_new(db) = ver != ack → routine.view()["kkumi_new"] → 할 일 종류 목록(todohub.js)이 블로그 셋에 '꾸미기 새 판 v<n> — 다시 깔아 주세요 (1분)' (11/16 — n = version(), meta kkumi_v).
예약 시각 자동 채움 (v6 · 주인 11/14 — 글마다 발행 설정에서 날짜·시각을 고르는 수고를 없앰 · 마지막 발행 단추는 주인이 직접):
  책갈피는 바깥 요청을 하지 않으므로 시각은 복사하는 글 안에 싣는다 — 글 끝 태그 줄(\u2064 + 안 보이는 비트, Blog-Auto paste.tags_line
  '<카테고리>\x01<태그들>')의 가운데에 '@YYYY-MM-DDTHH:MM' 한 칸: '<카테고리>\x01@2026-11-15T19:00\x01<태그들>' (stamp).
  옛 책갈피(v5)와 Blog-Auto 는 첫 칸(카테고리)·끝 칸(태그)만 읽어 그대로 돈다.
  when_plan(items, day, now): '예약' 줄마다 채울 시각 {줄 id: {at, label, rule}} — Blog-Auto 가 고른 추천 시각(줄의 복사 칸 '예약 시각':
  '19:30'·'내일 09:00'·'11/16(월) 09:00', 그 목록의 날 기준)을 그대로. 없거나 20분 안으로 다가왔으면(지났으면) 규칙으로 (rule 참):
  블로그별 후보 시각(WHEN_TIMES — Blog-Auto routine.RESERVE_TIMES·BEST_TIMES 와 같은 값) 중 지금부터 30분 뒤 · 같은 블로그의 다른 예약 줄과
  3시간(WHEN_GAP_MIN) 넘게 떨어진 첫 칸. '발행' 줄(바로 올리는 글)과 '바로 발행' 줄은 채우지 않는다. routine.view 가 줄에 when 을 달고 html 에 stamp.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

SRC = Path(__file__).resolve().parent / "static" / "kkumi.js"
KV_ACK = "kkumi_ack"  # 주인이 '설치했어요'를 누른 판의 지문
# 설치 칸 (주인 11/16 14시 '꾸미기 책갈피 코드만' — 연결 탭 맨 위에 크게: 코드 복사 → 쉬운 3단계 → 판 확인 · 쓰는 법은 작게)
STEPS = (
    "사파리에서 아무 페이지나 열고 공유 단추(네모에 화살표)를 눌러요.",
    "'책갈피 추가' → 이름을 '꾸미기'로 저장해요. (이미 있으면 건너뛰어요 · 꾸미기 책갈피는 하나만)",
    "책갈피 목록 → '편집' → '꾸미기' → 주소 칸을 모두 지우고 복사한 코드를 붙여 넣은 뒤 저장해요.",
)
USE = ("쓰는 법: 대시보드 '본문 복사' → 사파리 네이버 글쓰기에서 제목을 쓰고 빈 본문을 한 번 누름 → 책갈피 '꾸미기' → 뜨는 초록 칸을 길게 눌러 "
       "붙여넣기 → 1~2분 기다리면 끝 알림 · 발행 설정창에서 태그를 보고 발행해요. 예약 글은 예약 날짜·시각도 채워 줘요 — 맞는지 보고 "
       "발행 단추는 직접 눌러요.")


def check_line() -> str:
    """지금 깔린 판 확인 한 줄 — 끝 알림 맨 아래 'v<n>' (kkumi.js alert 끝 '\\nv' + V)."""
    return f"지금 깔린 판 확인: 꾸미기를 쓰고 난 끝 알림 맨 아래가 v{version()} 이면 새 판이에요."


_WORD = re.compile(r"[A-Za-z0-9_$]")


def _squeeze(line: str) -> str:
    """따옴표 글 밖의 공백: 양쪽이 이름 글자면 하나만, 아니면 뺌. kkumi.js 는 정규식 안에 공백·따옴표를 쓰지 않아
    따옴표만 따라가면 된다 (테스트가 new Function 으로 확인)."""
    out: list[str] = []
    q, i = "", 0
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == "\\":
                out.append(line[i + 1] if i + 1 < len(line) else "")
                i += 1
            elif c == q:
                q = ""
        elif c in "'\"`":
            q = c
            out.append(c)
        elif c.isspace():
            j = i
            while j < len(line) and line[j].isspace():
                j += 1
            if out and j < len(line) and _WORD.match(out[-1]) and _WORD.match(line[j]):
                out.append(" ")
            i = j
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def minify(src: str) -> str:
    """머리 /* */ 주석 · '//' 로 시작하는 줄 · 빈 줄을 빼고 줄마다 공백을 줄인다 (줄바꿈은 남김 — 문장 끝을 바꾸지 않게)."""
    src = re.sub(r"\A\s*/\*.*?\*/", "", src, count=1, flags=re.S)
    out = ""
    for line in src.splitlines():
        if (s := line.strip()) and not s.startswith("//"):
            # ';'·'{' 로 끝난 줄 다음은 줄바꿈 없이 (책갈피 주소에서 줄바꿈은 %0A 세 글자 — 11/09 사진 올리기 넣으며 26KB 안으로)
            out += ("" if not out or out[-1] in ";{,([&|+?:=" else "\n") + _squeeze(s)
    return out


def encode(code: str) -> str:
    """자바스크립트 encodeURIComponent 와 같은 글자만 남기고 % 로 바꿈."""
    return quote(code, safe="-_.!~*'()")


def encode_short(code: str) -> str:
    """encode 보다 짧게 (꾸미기만 · 11/15 v6 — 33.4KB → 29KB): 주소에 그대로 써도 되는 글자 ; / ? : @ & = + $ , 는 % 로 바꾸지 않는다
    (자바스크립트 encodeURI 와 같고 '#' 만 %23 으로 — '#' 뒤는 주소의 딴 부분으로 읽힘). 브라우저는 javascript: 주소를 통째로
    % 풀기 한 뒤 돌리므로 푼 결과는 encode 와 같다 (decodeURIComponent 로 풀면 같은 코드 — 테스트가 확인)."""
    return quote(code, safe="-_.!~*'();/?:@&=+$,")


@lru_cache(maxsize=1)
def bookmarklet() -> str:
    return "javascript:" + encode_short(minify(SRC.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def ver() -> str:
    """지금 책갈피 코드의 지문 (sha256 앞 10자) — 코드가 한 글자라도 바뀌면 달라짐."""
    return hashlib.sha256(bookmarklet().encode("utf-8")).hexdigest()[:10]


@lru_cache(maxsize=1)
def version() -> int:
    """kkumi.js 의 판 번호 ('const V = 5' → 5 · 끝 알림 'v5'). 못 찾으면 0."""
    m = re.search(r"^const V = (\d+);", SRC.read_text(encoding="utf-8"), re.M)
    return int(m.group(1)) if m else 0


def acked(db: Any) -> str:
    return str(db.kv_get(KV_ACK) or "") if db is not None else ""


def ack(db: Any, given: str) -> bool:
    """'설치했어요' — 지금 판의 지문일 때만 적는다 (옛 화면에서 누른 옛 지문은 받지 않음)."""
    if given != ver():
        return False
    db.kv_set(KV_ACK, given)
    return True


def is_new(db: Any) -> bool:
    """설치했다고 한 판과 지금 판이 다름 (한 번도 안 눌렀어도 참 — 그때는 '다시 설치'가 곧 첫 확인)."""
    return acked(db) != ver()


def view(db: Any = None) -> dict[str, Any]:
    code = bookmarklet()
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": list(STEPS), "check": check_line(), "use": USE, "ver": ver(), "version": version()}
    if db is not None:
        out["ack"] = acked(db) == ver()
    return out


# ── 예약 시각 자동 채움 (v6) ─────────────────────────────────────
WHEN_COPY = "예약 시각"  # Blog-Auto routine 이 예약 줄에 싣는 복사 칸 이름
WHEN_GROUP = "예약"
WHEN_TIMES: dict[str, tuple[str, ...]] = {  # Blog-Auto routine.RESERVE_TIMES (데런픽 — 하루 3편 · 3시간 넘게) · BEST_TIMES (아카이브)
    "issue": ("09:00", "12:30", "16:00", "19:30", "22:30"),
    "finance": ("07:30", "12:10", "21:00"),
}
WHEN_GAP_MIN = 180  # 같은 블로그 예약끼리 (Blog-Auto routine.RESERVE_GAP_MIN)
WHEN_LEAD_MIN = 30  # 규칙으로 고를 때 지금부터 이만큼 뒤 (Blog-Auto routine.RESERVE_LEAD)
WHEN_SOON_MIN = 20  # 추천 시각이 이 안으로 다가왔으면 지난 것으로 보고 규칙으로 (복사 → 붙여넣기 → 꾸미기에 몇 분 걸림)
MARK_TAGS, BIT0, BIT1 = "\u2064", "\u200c", "\u200d"  # Blog-Auto paste.py · static/kkumi.js 와 같은 글자
_TAGS_LINE = re.compile(MARK_TAGS + "[" + BIT0 + BIT1 + "]+")
_AT = re.compile(r"^@\d{4}-\d\d-\d\dT\d\d:\d\d$")


def _hm(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def parse_when(label: Any, day: str) -> datetime | None:
    """Blog-Auto routine._label 의 거꾸로: '19:30' · '내일 09:00' · '11/16(월) 09:00' (목록의 날 day 'YYYY-MM-DD' 기준) → 시각. 못 읽으면 None."""
    s = str(label or "").strip()
    try:
        day0 = datetime.strptime(day, "%Y-%m-%d")
        if m := re.fullmatch(r"(\d{1,2}:\d{2})", s):
            return _hm(day0, m.group(1))
        if m := re.fullmatch(r"내일 (\d{1,2}:\d{2})", s):
            return _hm(day0 + timedelta(days=1), m.group(1))
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:\(.\))? (\d{1,2}:\d{2})", s):
            d = _hm(day0.replace(month=int(m.group(1)), day=int(m.group(2))), m.group(3))
            return d if d >= day0 - timedelta(days=1) else d.replace(year=d.year + 1)
    except ValueError:
        return None
    return None


def pick_when(blog: str, earliest: datetime, taken: list[datetime]) -> datetime:
    """규칙으로 고른 예약 시각: 그 블로그 후보 시각 중 earliest 뒤 · taken 과 WHEN_GAP_MIN 넘게 떨어진 첫 칸 (8일 안 · 없으면 내일 마지막 후보)."""
    times = WHEN_TIMES.get(blog) or WHEN_TIMES["issue"]
    gap = timedelta(minutes=WHEN_GAP_MIN)
    day0 = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
    for n in range(8):
        for d in sorted(_hm(day0 + timedelta(days=n), t) for t in times):
            if d >= earliest and all(abs(d - x) >= gap for x in taken):
                return d
    return _hm(day0 + timedelta(days=1), times[-1])


def when_plan(items: list[dict[str, Any]], day: str, now: datetime) -> dict[str, dict[str, Any]]:
    """'예약' 줄마다 꾸미기가 채울 시각 {줄 id: {"at": "2026-11-15T19:00", "label": "11/15 19:00", "rule": 규칙으로 골랐나}}.
    now = 그 목록 시간대(KST)의 지금 (tz 없는 시각). 다 한 줄(done)·복사할 글이 없는 줄(만드는 중)·'바로 발행' 줄은 뺀다 —
    다만 그 줄들의 추천 시각도 '이미 잡힌 시각'으로 쳐서 규칙이 피해 간다."""
    rows = [i for i in items if isinstance(i, dict) and i.get("group") == WHEN_GROUP]
    known: dict[str, datetime] = {}
    for i in rows:
        t = next((c.get("text") for c in i.get("copy") or [] if isinstance(c, dict) and c.get("label") == WHEN_COPY), None)
        if (d := parse_when(t, day)) is not None:
            known[i["id"]] = d
    soon = now + timedelta(minutes=WHEN_SOON_MIN)
    picked: dict[str, tuple[datetime, bool]] = {}
    for by_rule in (False, True):  # 추천 시각이 살아 있는 줄 먼저 — 규칙으로 고를 줄이 그 시각들을 피하게
        for i in rows:
            p = i.get("paste")
            if i.get("done") or not isinstance(p, dict) or p.get("stale") or not p.get("html") or "바로 발행" in str(i.get("title") or ""):
                continue
            d = known.get(i["id"])
            if not by_rule:
                if d is not None and d >= soon:
                    picked[i["id"]] = (d, False)
                continue
            if i["id"] in picked:
                continue
            blog = str(i.get("blog") or "")
            taken = [x for k, x in known.items() if k != i["id"] and x >= soon and _blog_of(rows, k) == blog]
            taken += [x for k, (x, r) in picked.items() if r and _blog_of(rows, k) == blog]
            picked[i["id"]] = (pick_when(blog, now + timedelta(minutes=WHEN_LEAD_MIN), taken), True)
    return {k: {"at": d.strftime("%Y-%m-%dT%H:%M"), "label": f"{d.month}/{d.day} {d:%H:%M}", "rule": r} for k, (d, r) in picked.items()}


def _blog_of(rows: list[dict[str, Any]], iid: str) -> str:
    return next((str(i.get("blog") or "") for i in rows if i["id"] == iid), "")


def _bits(data: str) -> str:
    return "".join(f"{b:08b}" for b in data.encode("utf-8")).replace("0", BIT0).replace("1", BIT1)


def _unbits(line: str) -> str | None:
    bits = "".join("0" if c == BIT0 else "1" for c in line if c in (BIT0, BIT1))
    try:
        return bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8)).decode("utf-8")
    except ValueError:
        return None


def stamp(text: str, at: str) -> str:
    """글 끝 태그 줄에 예약 시각 칸을 넣는다 ('<카테고리>\x01@<at>\x01<태그들>' — at 'YYYY-MM-DDTHH:MM'). 태그 줄이 없거나 못 읽으면 그대로
    (태그 줄이 없는 글에 새 줄을 만들지 않는다 — 옛 책갈피가 빈 줄로 헤맴). 이미 든 시각은 바꿔 쓴다."""
    if not _AT.match("@" + at):
        return text

    def swap(m: re.Match[str]) -> str:
        data = _unbits(m.group(0))
        if data is None:
            return m.group(0)
        parts = data.split("\x01")
        cat, tags = (parts[0] if len(parts) > 1 else ""), parts[-1]
        return MARK_TAGS + _bits(f"{cat}\x01@{at}\x01{tags}")

    return _TAGS_LINE.sub(swap, text)


def stamped(text: str) -> str:
    """글의 태그 줄에 든 예약 시각 'YYYY-MM-DDTHH:MM' (없으면 '')."""
    for m in _TAGS_LINE.finditer(text):
        parts = (_unbits(m.group(0)) or "").split("\x01")
        if len(parts) == 3 and _AT.match(parts[1]):
            return parts[1][1:]
    return ""
