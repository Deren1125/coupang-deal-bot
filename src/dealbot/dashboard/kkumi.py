"""'꾸미기' 책갈피 (주인 10/27 — 서버가 네이버에 로그인하지 않고, 주인이 아이패드 사파리에서 대시보드 글을 붙여 넣어 발행).

static/kkumi.js = Blog-Auto blogauto/naver/kkumi_v4.js (10/29 아이패드 시험 통과): 글쓰기에서 빈 본문을 한 번 누르고 책갈피를 누르면
칸이 뜨고, 대시보드 '본문 복사'한 글을 그 칸에 붙여 넣으면 위에서부터 조각마다 에디터에 붙이며 짧은 구분선 · 주소 줄 다음 링크 카드 ·
포스트잇 · 발행 설정창 태그까지 넣는다 (아이패드에서는 스크립트로 커서를 옮길 수 없어 맨 끝에 차례로 쌓음 · 바깥 요청 없음).

  bookmarklet(): static/kkumi.js 에서 머리 주석·'//' 주석 줄·들여쓰기·빈 줄을 뺀 코드(minified) → "javascript:" + encodeURIComponent.
  view(): GET /dashboard/api/kkumi 몸 {code, len, steps, ver, version, ack} — 연결 탭 '꾸미기 버튼 설치' 칸(logins.js)이 열 때 받아 '코드 복사'.
설치한 책갈피가 옛 판인지 알리기 (주인 11/14 — 책갈피는 주인이 다시 복사·저장해야 바뀌는데 그동안은 바뀐 줄 몰라 옛 판으로 붙여 넣음):
  ver() = 지금 코드의 sha256 앞 10자 · version() = kkumi.js 의 'const V = n' (끝 알림 'v<n>' 과 같은 수) ·
  설치 칸 '설치했어요' 단추 → 대시보드 일 kkumi.installed {ver} → kv "kkumi_ack" = 그 ver (지금 ver 과 같을 때만 받음) ·
  is_new(db) = ver != ack → routine.view()["kkumi_new"] → 할 일 종류 목록(todohub.js)이 블로그 셋에 '꾸미기 책갈피가 바뀌었어요 — 다시 설치 (1분)'.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

SRC = Path(__file__).resolve().parent / "static" / "kkumi.js"
KV_ACK = "kkumi_ack"  # 주인이 '설치했어요'를 누른 판의 지문
STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '꾸미기'로 저장해요. (이미 있으면 건너뛰어요)",
    "책갈피 목록 → '편집' → '꾸미기'를 눌러 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'. (꾸미기 책갈피는 하나만 두세요)",
    "쓰는 법: 대시보드 '본문 복사' → 사파리 네이버 글쓰기에서 제목을 쓰고 빈 본문을 한 번 누름 → 책갈피 '꾸미기' → 뜨는 초록 칸을 길게 눌러 "
    "붙여넣기 → 1~2분 기다리면 끝 알림 · 발행 설정창에서 태그를 보고 발행해요.",
)


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


@lru_cache(maxsize=1)
def bookmarklet() -> str:
    return "javascript:" + encode(minify(SRC.read_text(encoding="utf-8")))


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
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": list(STEPS), "ver": ver(), "version": version()}
    if db is not None:
        out["ack"] = acked(db) == ver()
    return out
