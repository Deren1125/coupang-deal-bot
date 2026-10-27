"""'꾸미기' 책갈피 (주인 10/27 — 서버가 네이버에 로그인하지 않고, 주인이 아이패드 사파리에서 대시보드 글을 붙여 넣어 발행).

붙여 넣은 글은 서식·인용·사진은 남지만 링크 카드는 빠진다. 글쓰기 화면에서 책갈피 '꾸미기'를 누르면 static/kkumi.js 가
주소만 있는 줄마다 툴바 링크 단추로 카드를 만든다 (주소 줄은 남기고 그 아래 카드 — 서버 봇이 하던 모양 · 바깥 요청 없음).

  bookmarklet(): static/kkumi.js 에서 머리 주석·'//' 주석 줄·들여쓰기·빈 줄을 뺀 코드(minified) → "javascript:" + encodeURIComponent.
  view(): GET /dashboard/api/kkumi 몸 {code, len, steps} — 연결 탭 '꾸미기 버튼 설치' 칸(logins.js)이 열 때 받아 '코드 복사'.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

SRC = Path(__file__).resolve().parent / "static" / "kkumi.js"
STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '꾸미기'로 저장해요. (이미 있으면 건너뛰어요)",
    "책갈피 목록 → '편집' → '꾸미기'를 눌러 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '완료'.",
    "네이버 글쓰기에 본문을 붙여 넣은 다음, 주소창의 책갈피에서 '꾸미기'를 눌러요. 끝나면 '링크 카드 n/m개' 알림이 떠요.",
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
    return "\n".join(_squeeze(s) for line in src.splitlines() if (s := line.strip()) and not s.startswith("//"))


def encode(code: str) -> str:
    """자바스크립트 encodeURIComponent 와 같은 글자만 남기고 % 로 바꿈."""
    return quote(code, safe="-_.!~*'()")


@lru_cache(maxsize=1)
def bookmarklet() -> str:
    return "javascript:" + encode(minify(SRC.read_text(encoding="utf-8")))


def view() -> dict[str, Any]:
    code = bookmarklet()
    return {"code": code, "len": len(code), "steps": list(STEPS)}
