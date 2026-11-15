"""계정 정리 (구글·인스타) — 어느 계정을 어디에 쓰는지 한 표 (주인 11/15 — '구글 계정이 여러 개 필요할 듯한데, 계정별 사용처를 정리해서 대시보드에').

연결 탭의 접는 칸 '계정 정리 (구글·인스타)' (static/accounts.js · 바로 가기 #/connect/accounts) 가 GET /dashboard/api/accounts 로 받아 그린다. 읽기 전용.

여기에는 **쓰임만** 적는다 — 계정 주소(이메일)·비밀번호·토큰·쿠키·웹 주소는 절대 넣지 않는다 (tests/test_dash_accounts.py 가 막음).
고칠 땐 아래 ACCOUNTS·RULES 만 바꾸면 된다 (쇼츠 총괄 / 블로그 메인). 근거는 쇼츠 서버 config/upload_channels.yaml 의 layers.
"""

from __future__ import annotations

import re
from typing import Any

TITLE = "계정 정리 (구글·인스타)"
COLS = ("계정", "쓰는 곳", "누가·어디서 쓰나", "상태")

# 한 줄 = 계정 하나. state: on = 사용 중 · todo = 주인이 만들 차례 · wait = 나중·진행 중 (화면 색만 가름)
ACCOUNTS: tuple[dict[str, str], ...] = (
    {"name": "① 본 구글 (주인 기존)",
     "use": "유튜브 창작 채널: 정부작전파일·시니어·지도 '왜'·진짜 사건 + 구글 클라우드(Gemini·Vertex 크레딧 결제) + 블로그 기존 연결",
     "who": "쇼츠 서버 업로드 토큰(OAuth) · 블로그 서버", "status": "사용 중", "state": "on"},
    {"name": "② 새 구글① '클립층'",
     "use": "유튜브 털뭉치(웃긴 동물)·이게 왜 되지?",
     "who": "쇼츠 서버 업로드 토큰", "status": "주인이 만들 차례 (채널 생성·연결 전)", "state": "todo"},
    {"name": "③ 새 구글② '격리층'",
     "use": "유튜브 명장면(예능·드라마), 수익 신청 0",
     "who": "쇼츠 서버 업로드 토큰", "status": "만들 차례 (11/22~23 전)", "state": "todo"},
    {"name": "④ 클립 받기용 버림 계정",
     "use": "유튜브 영상 내려받기(yt-dlp 쿠키)만 — 업로드·채널 0",
     "who": "쇼츠 서버 · 로그인 1회 뒤 재로그인 0", "status": "사용 중", "state": "on"},
    {"name": "⑤ 쌀먹 전용 구글",
     "use": "쌀먹(이벤트 응모) — 숏폼 계정과 섞지 않음",
     "who": "퍼프덱스 오라클 서버", "status": "11/18 이후 새로 만듦", "state": "wait"},
    {"name": "인스타 (구글 아님)",
     "use": "@derenpick.look 룩북 · 채널별 인스타는 각 층과 같은 묶음",
     "who": "쇼츠 서버 Instagram 로그인 API", "status": "연결 진행 중", "state": "wait"},
)

RULES: tuple[str, ...] = (
    "한 계정 = 한 층 (섞지 않기 — 하나 정지돼도 다른 층 안전)",
    "같은 서버에서 여러 구글 로그인 겹치지 않게",
    "버림 계정은 업로드에 절대 안 씀",
)

SOURCE = "근거: 쇼츠 서버 config/upload_channels.yaml layers (11/15) · 고칠 땐 쇼츠 총괄/블로그 메인"

# 비밀·주소 꼴 (표에 들어오면 안 되는 것) — 이메일 · 웹 주소 · 긴 영숫자 덩어리(토큰·열쇠) · 비밀번호 적는 꼴
SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    re.compile(r"(?i)\b(?:https?|ftp)://|\bwww\."),
    re.compile(r"[A-Za-z0-9_\-+=]{20,}"),  # 파일 길(config/….yaml)은 걸리지 않게 / 는 뺌
    re.compile(r"(?i)(?:password|passwd|pwd|secret|token|비밀번호|비번|암호)\s*[:=]"),
)


def leaks(text: str) -> bool:
    """글에 비밀·주소 꼴이 있으면 참."""
    return any(p.search(text) for p in SECRET_SHAPES)


def view() -> dict[str, Any]:
    """GET /dashboard/api/accounts 몸. 비밀 꼴이 섞인 줄은 통째로 뺀다 (누가 실수로 적어도 화면에 안 나감)."""
    rows = [{k: r[k] for k in ("name", "use", "who", "status", "state")} for r in ACCOUNTS
            if not any(leaks(str(v)) for v in r.values())]
    return {"title": TITLE, "cols": list(COLS), "rows": rows, "rules": [x for x in RULES if not leaks(x)],
            "source": "" if leaks(SOURCE) else SOURCE, "hidden": len(ACCOUNTS) - len(rows)}
