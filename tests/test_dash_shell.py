"""대시보드 앱(Sns-board) 화면 껍데기·앱 파일·통계 조각 (주인 10/18 — 휴대폰 먼저, 깔끔하게).

- GET /dashboard/ 는 앱 껍데기 + 첫 화면 값(boot JSON) — 페이지 안 스크립트 없음(CSP script-src 'self'), '</script>' 를 막음
- 앱 파일(app.js·app.css·sw.js·badge-72.png)은 로그인 없이, 크기 예산 안
- 화면 글자는 한국어, 바깥 주소·eval 없음
- 통계 탭 조각은 폼·스크립트 없이 data-action 단추만, 할 일·딜·연결과 겹치는 카드는 빼고
"""

from __future__ import annotations

import gzip
import io
import json
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest
from PIL import Image

from dash_helpers import (
    blogauto_status,
    fixture,
    get_json,
    post_json,
    push_settings,
    queued,
    session_headers,
)
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import icons, render, shell, webapp

STATIC = webapp.STATIC_DIR
HANGUL = re.compile(r"[가-힣]")
# spec §5.5 는 app.js 35 KB 를 잡았지만, 그 뒤 더해진 흐름(로그인 시트·딜 바뀜·스레드 답글·다 했어요·카톡 다시 연결·통계 직접 넣기)까지
# 라이브러리 없이 넣은 크기. 휴대폰이 받는 건 gzip(Caddy) 크기 — 서비스 워커가 한 번 받으면 다시 받지 않는다
# 10/18: 간격 두고 발행(확인 창 시각 목록·예약된 글·검색 등록) + 쇼핑 커넥트에 없음 시트 → 60 KB / 19 KB.
# 10/18 저녁: '오늘 들를 이웃 글' (할 일 한 줄 + 목록 시트 + 블로그 탭 카드: 댓글 복사·열기·했어요) + 서로이웃 한 줄 → 64 KB / 20.5 KB
# 10/18 밤: '오늘 한 일' + 인스타 링크 칸·오픈채팅 문구 복사 + 링크에서 사진 고르기 (추천 사진 그리드·'이대로 넣기'·'링크에서 사진 뽑기')
# 세 기능을 합친 크기 → 78 KB / 24.5 KB, css 15.5 KB
# 10/19: 사진 편집·일괄 수정 — app.js 에는 '사진 한 번에 고치기' 단추와 불러오기만 (gzip 24.5 → 25 KB),
# 편집기·일괄 수정 화면은 처음 쓸 때만 받는 edit.js (12 KB · gzip 5 KB)
# 10/20: Pick 인기 검색어 카드(순위 ▲▼ · 이미 쓴 글 · 누르면 키워드로 쓰기) + 블로그 이름 옆 할 일 수 + 사진을 누르면 편집기 로더
# → 79 → 81 KB / gzip 25.5 → 26.5 KB (편집기 자체는 edit.js 에)
# 10/20 (2): 사진을 누르면 올릴 원본 보기(체크무늬·픽셀 크기·편집/교체/비우기) + '내 후기 쓰기'·'후기 쓸 제품 고르기'(제품 이름 칸)
# + 인기 검색어 바로 쓰기 → 81 → 82 KB
# 2026-10-20 (3): 배운 것으로 나아지기 — 블로그 탭 '이번 주 개선안' 카드(✅ 적용 / ❌ 빼기 · 적용 규칙 지우기) + 최근 글 👍/👎·이유 시트
# → 둘을 합쳐 86 KB / gzip 28 KB
APP_JS_MAX = 86 * 1024
# 10/21 (merge post-edit): 사진 테스트 + 제품 테스트 + 발행 글 고치기 버튼이 함께 → 86 → 86.5 KB (88195 B)
APP_JS_MAX = 86 * 1024 + 512
# 10/21 (일정 화면): 화면은 처음 열 때만 받는 sched.js — app.js 에는 불러오기·길 하나·블로그 탭 입구 단추뿐 (+0.6 KB / gzip +0.1 KB)
APP_JS_MAX = 87 * 1024
APP_JS_GZIP_MAX = 28 * 1024 + 256
# 10/23 (제휴 수익): 통계 탭 맨 위 칸은 처음 열 때만 받는 earn.js — app.js 에는 불러오기와 칸 붙이기 한 줄뿐 (+0.3 KB / gzip +0.1 KB)
APP_JS_GZIP_MAX = 28 * 1024 + 512
# 10/23 (자동 새로고침): 20초마다 받기 + 시간 글자만 바뀐 것은 다시 그리지 않기(깜빡임) + 블로그 탭 할 일·오늘 한 일 요약
# + 휴대폰 깜빡임(저장본 무시·새 판이면 스스로 다시 열기) → 88 KB / gzip 28.75 KB
APP_JS_MAX = 88 * 1024
APP_JS_GZIP_MAX = 28 * 1024 + 768
# 10/24 (오픈톡방 알림): #/share/글번호 → 그 핫딜 글의 보내기 판 + 수익·오픈톡방 알림은 화면을 열어도 닫지 않기 (gzip +0.05 KB)
APP_JS_GZIP_MAX = 28 * 1024 + 1024
# 10/24 (연결 탭 쇼핑커넥트 줄 + 새로고침 단추도 새 판이면 다시 열기): 줄 하나·단추 하나 (+0.15 KB)
APP_JS_MAX = 88 * 1024 + 256
# 10/27 (할 일 탭 가볍게): 자동 작업 한 줄(정상은 접고 문제만 줄로) + '더 보기' 접기(오늘 한 일·정리·바뀐 것·점검) + 오늘 할 일 칸과 겹친
# '발행 대기' 빼기 + 빈 칸(null) 안 그리기 — 새 칸이라 줄일 곳이 없어 +1.75 KB / gzip +0.375 KB (91916 B / 30052 B)
APP_JS_MAX = 90 * 1024
APP_JS_GZIP_MAX = 29 * 1024 + 384
# 11/05 (한눈에 — 주인 '뭘 해야 하는지 한눈에, 세부 조정은 빼도'): 할 일 맨 위 종류별 수 칸 + 블로그 글감·도구·통계 채널 카드·연결 설정을
# 접는 칸 하나(tuck)로 → 93.5 KB / gzip 30.5 KB
APP_JS_MAX = 92 * 1024
APP_JS_GZIP_MAX = 30 * 1024
# 11/09: 할 일 블로그별 — vTodo 가 블로그마다 routine.js part() 에 그 블로그 카드를 넘김 (+0.6 KB / gzip +0.2 KB)
APP_JS_MAX = 92 * 1024 + 512
APP_JS_GZIP_MAX = 30 * 1024 + 256
# 11/09: 블로그별이면 '한눈에' 칸 빼기 (숫자 세 곳이 달라 헷갈림) +0.2 KB
APP_JS_MAX = 92 * 1024 + 768
# 11/09: 급한 순 — 급한 카드(멈춤·핫·급해요·3시간 안 마감)를 가르고 블로그 칸을 점수로 줄 세움 (+0.6 KB / gzip +0.25 KB)
APP_JS_MAX = 93 * 1024 + 512
APP_JS_GZIP_MAX = 30 * 1024 + 512
# 11/09: 영상 칸 (룩북·코디 랭킹 카드를 그 칸으로, '숏폼 올리기 N' 카드는 줄로 대신) +0.2 KB
APP_JS_MAX = 93 * 1024 + 768
APP_JS_GZIP_MAX = 30 * 1024 + 576
# 11/11: 키자드 등록 도우미 메뉴 한 줄(96e1216 — 그 커밋에서 gzip 31440 B 로 한도를 넘긴 채 올라감) → gzip 30.75 KB
APP_JS_GZIP_MAX = 30 * 1024 + 768
# 10/20: 편집기 비율(원본·16:9) + 사람만 남기기 + 사진 한 장 바로 저장 + 지금 사진도 편집 → 12 → 14 KB / gzip 5 → 5.75 KB
# 10/21: 발행 글 고치기 시트(사진마다 빼기·교체 + '이 문장 빼기') — app.js 는 버튼 하나만, 시트는 edit.js 에 → 16.5 KB / gzip 6.25 KB
EDIT_JS_MAX = 16 * 1024 + 512
EDIT_JS_GZIP_MAX = 6 * 1024 + 256
# 2026-10-21: 사진 테스트 화면(시험 글 사진마다 ✅/❌·이유 칩·메모 · 글 전체 · 테스트 끝 · 배운 원칙 — app.js 가 예산 끝이라 여기에)
# → 14 → 18 KB / gzip 5.75 → 7.25 KB
EDIT_JS_MAX = 18 * 1024
EDIT_JS_GZIP_MAX = 7 * 1024 + 256
# 10/21 (merge post-edit): 사진 테스트 화면 + 발행 글 고치기 시트 둘 다 edit.js 에 → 18 → 20 KB / gzip 7.25 → 7.5 KB (20201 B / 7668 B)
EDIT_JS_MAX = 20 * 1024
EDIT_JS_GZIP_MAX = 7 * 1024 + 512
APP_CSS_MAX = 15 * 1024 + 512
# 10/27 할 일 탭 여유 (주인 '답답한 감' — 카드·줄 간격, 줄 높이): #main data-tab 아래 할 일 탭 규칙만 → 16 KB (16295 B)
APP_CSS_MAX = 16 * 1024
SW_MAX = 8 * 1024
# 10/21: 제품 테스트 화면 (후보 카드 ✅/❌·이유 칩·한 줄·테스트 끝·배운 기준) — 처음 열 때만 받는 product.js
PRODUCT_JS_MAX = 6 * 1024
PRODUCT_JS_GZIP_MAX = 2 * 1024 + 512
# 10/21: 사진 테스트 화면(글 제목·사진 줄·자리마다 위치·앞 문장·큰 사진·고른 이유)을 edit.js 에서 옮겨 와 읽기 쉽게 — edit.js 는 줄고(20 KB 한도 안)
# 처음 열 때만 받는 product.js 가 늘어남 → 12 KB / gzip 4.5 KB
PRODUCT_JS_MAX = 12 * 1024
PRODUCT_JS_GZIP_MAX = 4 * 1024 + 512
# 10/23: 테스트 글마다 '글 보기' (본문 + 네이버 주소 시트 — 어느 글인지 보며 검수) → gzip 4.75 KB
PRODUCT_JS_GZIP_MAX = 4 * 1024 + 768
# 10/21: 일정 화면(세 블로그 예약을 시간순으로 · 날짜 칸 · 블로그 칩 · 시간 바꾸기/바로 발행/빼기) — 처음 열 때만 받는 sched.js (CSS 도 여기서 넣음)
SCHED_JS_MAX = 10 * 1024
SCHED_JS_GZIP_MAX = 4 * 1024
# 10/25: X(트위터) 화면(진행률 막대·숫자 넣기 · 데런픽 초안함 글 칸·X 글자 수·복사·X에 올리기·올렸어요/버리기/다시 쓰기 · 글감 카드) —
# 처음 열 때만 받는 xpost.js (CSS 도 여기서). app.js 에는 불러오기·길 하나·입구 단추뿐 (긴 주석 셋을 줄여 예산 안)
# 10/26: + 수익 계정 2주 계획(날마다 접는 칸 · 복사 · X에 쓰기 · 올렸어요 표시 · 댓글 틀) — 계획 글은 /dashboard/api/xplan 으로 받아 파일엔 그리는 코드만
XPOST_JS_MAX = 14 * 1024
XPOST_JS_GZIP_MAX = 6 * 1024
# 10/26: + 링크 답글(새 기준 — 블로그 주소는 본문 말고 첫 답글: 초안마다 답글 글자·복사 · 올린 X 글 주소 → 답글 쓰기 화면) —
# 겹치던 칸 그리기(row·note·card)를 작은 함수로 줄이고도 14 KB 를 조금 넘어 15 KB (gzip 은 6 KB 그대로)
XPOST_JS_MAX = 15 * 1024
# 10/25: 룩북 릴스 화면(편마다 상태·영상 미리보기·표지·영상/표지 저장·캡션/링크 복사·룩 목록·올리기 전 확인·릴스 주소·올렸어요/버리기/다시 만들기) —
# 처음 열 때만 받는 lookbook.js (CSS 도 여기서). app.js 에는 불러오기·길 하나·입구 단추뿐 (주석 셋을 줄여 예산 안)
LOOKBOOK_JS_MAX = 12 * 1024
LOOKBOOK_JS_GZIP_MAX = 5 * 1024
# 10/26: + 사진 시험(룩마다 상품 사진 | 거울 셀카 | 예전 양식 · 점수 · 시험 새로 만들기 · 이 양식으로 정하기) — 사진 목록은
# /dashboard/api/lbtrial 로 열 때만 받아 파일엔 그리는 코드만 (12 → 16 KB / gzip 5 → 6.25 KB)
LOOKBOOK_JS_MAX = 16 * 1024
LOOKBOOK_JS_GZIP_MAX = 6 * 1024 + 256
# 11/03: + 상품 후보 5개 고르기(검색어마다 카드 · 사진·이름·가격·리뷰·별점·쇼핑몰·부스트·지그재그에서 보기 · 체크 · n/5 · 정하기 · 정한 5개) —
# 목록은 /dashboard/api/lbcands 로 열 때만 (16 → 20 KB / gzip 6.25 → 7.5 KB)
LOOKBOOK_JS_MAX = 20 * 1024
LOOKBOOK_JS_GZIP_MAX = 7 * 1024 + 512
# 10/26: 로그인 관리 칸(연결 탭 맨 위 — 줄마다 상태·원격 로그인/QR 로그인 단추 · 열린 원격 화면 · QR 화면) — app.js 가 예산 끝이라
# 셸이 defer 로 받는 logins.js (CSS 도 여기서). 줄 값은 /dashboard/api/logins 로 열 때만
LOGINS_JS_MAX = 8 * 1024
LOGINS_JS_GZIP_MAX = 3 * 1024 + 512
# 10/27: 연결 탭 '꾸미기 버튼 설치' 칸(접힌 한 줄 · 펼치면 4단계 + 코드 복사 — 코드·단계 글은 /dashboard/api/kkumi 로 펼칠 때만)
# + 네이버 접속 칸 배열을 펼쳐 넣음 → 9.75 KB / gzip 4.125 KB (9748 B / 4094 B)
LOGINS_JS_MAX = 9 * 1024 + 768
LOGINS_JS_GZIP_MAX = 4 * 1024 + 128
# 11/09: 몰 '지금 확인'(블로그 프로그램에 맡기는 일)의 결과 기다리기 — 202 를 '하지 못했어요'로 보이던 것
LOGINS_JS_MAX = 10 * 1024 + 512
LOGINS_JS_GZIP_MAX = 4 * 1024 + 448
# 11/11: 책갈피 설치 칸을 둘로 ('꾸미기' + '통계 보내기' — 같은 함수, 코드는 펼칠 때 /dashboard/api/<api>) → 10.6 KB (10870 B / 4514 B)
LOGINS_JS_MAX = 10 * 1024 + 768
# 10/27: 오늘 할 일 칸(할 일 탭 맨 위 — 하루 5칸 중 지금 칸만 펼침 · 줄마다 체크 · 복사 단추 · 3/8 진행) — logins.js 처럼 셸이 defer 로 받는
# routine.js (CSS 도 여기서). 목록은 /dashboard/api/routine 으로 열 때만
ROUTINE_JS_MAX = 8 * 1024
ROUTINE_JS_GZIP_MAX = 3 * 1024 + 512
# 10/27 (2): 급한 알림이 있으면 그 바로 아래에 붙기 + 할 일 id 목록(ids)·'sb-routine' 알림 (app.js 가 겹친 '발행 대기'를 뺌)
# — 머리 주석을 줄이고도 +0.25 KB / gzip +0.125 KB (8388 B / 3620 B)
ROUTINE_JS_MAX = 8 * 1024 + 256
ROUTINE_JS_GZIP_MAX = 3 * 1024 + 640
# 10/27 (3): 할 일 탭 여유 — 칸 머리를 시트 .sh 와 겹치지 않는 .sl 로(가운데 정렬·테두리 버그), 체크 칸 44×44 .cb, 줄 사이 선·간격
# → 8.5 KB / gzip 3.75 KB (8671 B / 3729 B)
ROUTINE_JS_MAX = 8 * 1024 + 512
ROUTINE_JS_GZIP_MAX = 3 * 1024 + 768
# 10/27 (4): 발행·예약 줄 '붙여 넣기' 큰 단추 셋(제목·본문·태그 복사 — 본문은 text/html+text/plain, 안 되면 화면 밖 칸 복사) +
# 링크 카드 안내 한 줄 → 10.5 KB / gzip 4.5 KB (10611 B / 4525 B)
ROUTINE_JS_MAX = 10 * 1024 + 512
ROUTINE_JS_GZIP_MAX = 4 * 1024 + 512
# 10/28: 에이블리 링크 부탁 칸(줄마다 상품·가격·글·발행 시각 · '에이블리에서 열기' · 붙여 넣기 칸 + '넣기' · 결과가 실패면 이유와 함께 다시) +
# 일 보내기 함수 하나로 합침 → 14.25 KB / gzip 5.75 KB (14480 B / 5830 B 안팎)
ROUTINE_JS_MAX = 14 * 1024 + 256
ROUTINE_JS_GZIP_MAX = 5 * 1024 + 768
# 10/29: 에이블리 부탁 줄에 같은 상품 사진(/dashboard/thumb/ar-…) · '후기 n개 · 만족 n% · 닮음 n점' 한 줄 (주인 '사진 보고 디자인이 비슷해야')
# → 14.75 KB / gzip 5.9 KB (15123 B / 6066 B)
ROUTINE_JS_MAX = 15 * 1024
ROUTINE_JS_GZIP_MAX = 6 * 1024
# 10/29: '블로그별 예약 추천 시각' 펼침 칸 (주인 '각 블로그 별로 예약하면 좋을 시간 대시보드에') → 15.4 KB / gzip 6.1 KB (15813 B / 6287 B)
ROUTINE_JS_MAX = 16 * 1024
ROUTINE_JS_GZIP_MAX = 6 * 1024 + 384
# 10/30: 맨 위 '붙여넣기 대기 (N)' 칸 (시간대 칸에서 빼서 모두 · 꾸미기 설치 링크) + '⚡ 막 뜨는 중' 한 줄 + 접힌 '쉬는 중' 칸
# (주인 '대시보드 전면 수정' — app.js 예산을 넘지 않게 할 일 칸 쪽에) → 16.9 KB / gzip 6.55 KB (17283 B / 6705 B)
ROUTINE_JS_MAX = 17 * 1024 + 256
ROUTINE_JS_GZIP_MAX = 6 * 1024 + 768
# 11/09: '⚡ 막 뜨는 중' 키워드마다 Pick 글 쓰기 단추 (예전엔 화면만 옮겨 '연결이 안 돼')
ROUTINE_JS_MAX = 17 * 1024 + 768
ROUTINE_JS_GZIP_MAX = 6 * 1024 + 960
# 11/09: 할 일 블로그별 (주인 '한 블로그 로그인하면 그 블로그 작업 싹 몰아서') — 블로그마다 펼치는 칸 part() + 블로그별/시간대별 바꾸기
ROUTINE_JS_MAX = 19 * 1024 + 768
ROUTINE_JS_GZIP_MAX = 7 * 1024 + 512
# 11/09: 할 일 블로그 칸 다시 그림 (주인 '직관적으로 · 깨지는 데') — 색 점·남은 일·막대·열기, 칸 안 작은 제목(올리기·이웃 들르기·서로이웃 신청·확인할 것), 이웃 줄 짧게
ROUTINE_JS_MAX = 22 * 1024 + 512
ROUTINE_JS_GZIP_MAX = 8 * 1024 + 384
# 11/09: 급한 순 (주인 '중요도 순') — 블로그 칸 순서 score() · 칸 안 '급한 것' 맨 위 · 맨 위 칸 펼침 · '급함 N' 표
ROUTINE_JS_MAX = 23 * 1024 + 512
ROUTINE_JS_GZIP_MAX = 8 * 1024 + 768
# 11/09: '숏폼·릴스·룩북' 칸 (주인 '릴스·쇼츠·룩북도 다 할 일 탭에') — 쇼츠 서버 올리기 일정(밀린·오늘·내일) 줄마다 파일 받기·제목·설명 복사·체크
ROUTINE_JS_MAX = 26 * 1024 + 512
ROUTINE_JS_GZIP_MAX = 9 * 1024 + 512
# 11/10: 글 줄 제목 옆 인기 예상 점수 칩 (주인 '점수도 할 일 탭에서 글 옆에') — '🔥 88점'·'62점' 알약 + CSS
ROUTINE_JS_MAX = 27 * 1024
ROUTINE_JS_GZIP_MAX = 9 * 1024 + 640
# 10/26: SNS 탭 상자 (스레드·인스타 · X · 룩북 릴스 칸 고르기 뒤 각 화면 파일을 담기만 · 옮기며 남은 머리 줄 숨김 CSS) — 처음 열 때만 받는 sns.js
SNS_JS_MAX = 4 * 1024
SNS_JS_GZIP_MAX = 2 * 1024


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def boot_of(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="boot">(.*?)</script>', html, re.S)
    assert m, "boot JSON 없음"
    return json.loads(m.group(1))


async def page(bot: DealBot, headers: dict[str, str]) -> tuple[int, str, dict[str, str]]:
    res = await bot.web.dispatch("GET", "/dashboard/", headers)
    return res[0], res[1], res[3]


async def test_shell_page_has_boot_json_and_no_inline_script(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    (tmp_path / "blogauto.json").write_text(json.dumps(fixture("export_v2.json"), ensure_ascii=False), encoding="utf-8")
    blogauto_status(settings)
    evil = queued(bot, name='무선청소기 </script><img src=x onerror="alert(1)">  끝')
    h = session_headers(bot)
    status, html, headers = await page(bot, h)
    assert status == 200 and html.startswith("<!doctype html>")
    csp = headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S)
    # theme.js (화면 밝기, 10/24) · app.js · thpost.js (스레드 칸, 10/21 — threads.js 보다 먼저 떠 있어야 칸이 붙음) ·
    # logins.js (연결 탭 '로그인 관리' 칸, 10/26 — app.js 예산 끝이라 셸이 받음) · routine.js (할 일 탭 '오늘 할 일' 칸, 10/27) · boot
    assert len(scripts) == 6
    (th_attrs, th_body), (src_attrs, src_body), (tp_attrs, tp_body), (lg_attrs, lg_body), (rt_attrs, rt_body), (boot_attrs, _boot) = scripts
    assert re.fullmatch(r' defer src="/dashboard/static/routine\.js\?v=[0-9a-f]{10}"', rt_attrs) and rt_body == ""
    assert re.fullmatch(r' blocking="render" src="/dashboard/static/theme\.js\?v=[0-9a-f]{10}"', th_attrs) and th_body == ""
    assert re.fullmatch(r' defer src="/dashboard/static/thpost\.js\?v=[0-9a-f]{10}"', tp_attrs) and tp_body == ""
    assert re.fullmatch(r' defer src="/dashboard/static/logins\.js\?v=[0-9a-f]{10}"', lg_attrs) and lg_body == ""
    assert re.fullmatch(r' src="/dashboard/static/app\.js\?v=[0-9a-f]{10}" defer', src_attrs) and src_body == ""
    assert boot_attrs == ' type="application/json" id="boot"'
    outside = re.sub(r'<script type="application/json" id="boot">.*?</script>', "", html, flags=re.S)
    assert not re.search(r"\son[a-z]+=", outside)  # onclick= 같은 속성 없음
    assert "<img src=x" not in html and "</script><img" not in html and " " not in html
    boot = boot_of(html)
    assert any(evil.id == int(it["iid"].split("-")[1]) for it in boot["todo"] if it["iid"].startswith("deal-"))
    assert boot["me"]["csrf"] == bot.dashboard.api.csrf_token(h)  # type: ignore[union-attr]
    assert boot["me"]["logout_t"] == bot.dashboard.form_token
    assert '<link rel="stylesheet" href="/dashboard/static/app.css?v=' in html
    assert re.search(r'<meta name="sb-edit" content="/dashboard/static/edit\.js\?v=[0-9a-f]{10}">', html)  # 편집기는 처음 쓸 때만
    assert '<link rel="manifest" href="/dashboard/manifest.webmanifest"' in html and "viewport-fit=cover" in html
    # 아래 탭 6개 (할 일 · 딜 · 블로그 · SNS · 통계 · 연결 — SNS 는 10/26) — 이 순서, 글자가 있는 단추
    assert re.findall(r'data-tab="([a-z]+)"', html) == ["todo", "deal", "blog", "sns", "stats", "connect"]
    assert [lab for _k, lab, _s in icons.TABS] == ["할 일", "딜", "블로그", "SNS", "통계", "연결"]
    for _k, lab, _s in icons.TABS:
        assert f"<span>{lab}</span>" in html


async def test_boot_csrf_and_logout_token_work(bot: DealBot) -> None:
    h = session_headers(bot, csrf=False)
    _s, html, _h = await page(bot, h)
    boot = boot_of(html)
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "bot.resume", "args": {}, "cid": "c_shell01"},
                                     dict(h, **{"x-dash-token": boot["me"]["csrf"]}))
    assert status == 200 and got["status"] == "ok"
    status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "bot.resume", "args": {}},
                                     dict(h, **{"x-dash-token": "wrong"}))
    assert status == 403 and got["code"] == "csrf"
    body = urlencode({"t": boot["me"]["logout_t"]}).encode()
    res = await bot.web.dispatch("POST", "/dashboard/logout", dict(h, **{"content-type": "application/x-www-form-urlencoded",
                                                                          "content-length": str(len(body))}), body)
    assert res[0] == 303 and res[3]["Clear-Site-Data"] == '"cache"'
    assert (await page(bot, h))[0] == 302  # 그 세션은 끝


async def test_app_pages_and_deep_links_need_login(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    res = await bot.web.dispatch("GET", "/dashboard/", anon)
    assert res[0] == 302 and res[1] == "/dashboard/login"
    res = await bot.web.dispatch("GET", "/dashboard/go/deal-12", anon)
    assert res[0] == 302 and res[1] == "/dashboard/login?next=/dashboard/go/deal-12"
    for path in ("/dashboard/api/state", "/dashboard/api/stats.html", "/dashboard/api/item/deal-1"):
        assert (await bot.web.dispatch("GET", path, anon))[0] == 401, path
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/go/post-issue-46", h)
    assert res[0] == 302 and res[1] == "/dashboard/#/i/post-issue-46"


async def test_app_files_are_public_with_right_headers(bot: DealBot) -> None:
    res = await bot.web.dispatch("GET", "/dashboard/sw.js", {})
    assert res[0] == 200 and res[2].startswith("text/javascript")
    assert res[3]["Cache-Control"] == "no-cache" and res[3]["Service-Worker-Allowed"] == "/dashboard/"
    for name, ctype in (("app.js", "text/javascript"), ("app.css", "text/css"), ("badge-72.png", "image/png"),
                        ("edit.js", "text/javascript"), ("product.js", "text/javascript"), ("sched.js", "text/javascript"),
                        ("earn.js", "text/javascript"), ("xpost.js", "text/javascript"), ("lookbook.js", "text/javascript"),
                        ("logins.js", "text/javascript"), ("sns.js", "text/javascript"), ("routine.js", "text/javascript")):
        res = await bot.web.dispatch("GET", f"/dashboard/static/{name}", {})
        assert res[0] == 200 and res[2].startswith(ctype) and res[3]["X-Content-Type-Options"] == "nosniff", name
    img = Image.open(io.BytesIO((STATIC / "badge-72.png").read_bytes()))
    assert img.size == (72, 72) and img.mode == "RGBA"
    # 알림 작은 아이콘은 흰 모양 + 투명 (안드로이드는 알파만 씀)
    assert {px[:3] for px in img.getdata() if px[3] > 200} <= {(255, 255, 255)}


def test_asset_budgets() -> None:
    js = (STATIC / "app.js").read_bytes()
    assert len(js) <= APP_JS_MAX and len(gzip.compress(js, 9)) <= APP_JS_GZIP_MAX
    assert len((STATIC / "app.css").read_bytes()) <= APP_CSS_MAX
    assert len((STATIC / "sw.js").read_bytes()) <= SW_MAX
    ed = (STATIC / "edit.js").read_bytes()
    assert len(ed) <= EDIT_JS_MAX and len(gzip.compress(ed, 9)) <= EDIT_JS_GZIP_MAX
    pj = (STATIC / "product.js").read_bytes()
    assert len(pj) <= PRODUCT_JS_MAX and len(gzip.compress(pj, 9)) <= PRODUCT_JS_GZIP_MAX
    sj = (STATIC / "sched.js").read_bytes()
    assert len(sj) <= SCHED_JS_MAX and len(gzip.compress(sj, 9)) <= SCHED_JS_GZIP_MAX
    xj = (STATIC / "xpost.js").read_bytes()
    assert len(xj) <= XPOST_JS_MAX and len(gzip.compress(xj, 9)) <= XPOST_JS_GZIP_MAX
    lb = (STATIC / "lookbook.js").read_bytes()
    assert len(lb) <= LOOKBOOK_JS_MAX and len(gzip.compress(lb, 9)) <= LOOKBOOK_JS_GZIP_MAX
    lg = (STATIC / "logins.js").read_bytes()
    assert len(lg) <= LOGINS_JS_MAX and len(gzip.compress(lg, 9)) <= LOGINS_JS_GZIP_MAX
    rt = (STATIC / "routine.js").read_bytes()
    assert len(rt) <= ROUTINE_JS_MAX and len(gzip.compress(rt, 9)) <= ROUTINE_JS_GZIP_MAX
    sn = (STATIC / "sns.js").read_bytes()
    assert len(sn) <= SNS_JS_MAX and len(gzip.compress(sn, 9)) <= SNS_JS_GZIP_MAX
    assert shell.asset_url("app.js") != shell.asset_url("app.css")


def test_app_js_is_korean_local_and_safe() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    ed = (STATIC / "edit.js").read_text(encoding="utf-8")
    pj = (STATIC / "product.js").read_text(encoding="utf-8")
    sj = (STATIC / "sched.js").read_text(encoding="utf-8")
    xj = (STATIC / "xpost.js").read_text(encoding="utf-8")  # X 글쓰기 화면 주소는 state.x.intent 로 받는다 (파일에는 바깥 주소 없음)
    lb = (STATIC / "lookbook.js").read_text(encoding="utf-8")  # 룩북 영상·상품 링크는 state.lookbook 으로 (쇼츠 서버 주소는 화면에 없음)
    lg = (STATIC / "logins.js").read_text(encoding="utf-8")  # 로그인 관리 칸 — 원격 화면·QR 주소는 /dashboard/api/logins 로 (이 서버 주소만)
    sn = (STATIC / "sns.js").read_text(encoding="utf-8")  # SNS 탭 상자 — 화면 파일을 담기만
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")  # 오늘 할 일 칸 — 링크는 /dashboard/api/routine 으로 (서버가 https 만 거름)
    for text in (js, css, sw, ed, pj, sj, xj, lb, lg, sn, rt):
        assert "http://" not in text and "https://" not in text  # 바깥 주소·CDN 없음
        assert "eval(" not in text and "new Function" not in text and "document.write" not in text
    assert js.count("innerHTML") == 1  # 서버가 그린 통계 조각 하나뿐
    # 단추 글자는 모두 한국어 (영어 단추 없음)
    labels = re.findall(r"btn\('[^']*', '([^']*)'", js)
    assert labels and all(HANGUL.search(x) for x in labels), [x for x in labels if not HANGUL.search(x)]
    for word in ("할 일 없어요", "더보기", "붙여넣기", "상품 열기", "다시 고르기", "취소", "알림 켜기", "나중에", "오늘 정리 보기",
                 "다시 로그인", "요청했어요", "직접 고친 글이라 멈췄어요", "길게 눌러 붙여넣기", "기다리는 글 없어요"):
        assert word in js, word
    assert "텔레그램" not in js and "Telegram" not in js
    # 알림 권한은 단추를 눌렀을 때만 (iOS) — 시작할 때 묻지 않는다
    assert js.count("Notification.requestPermission") == 1
    push_on = js.split("async function pushOn()", 1)[1].split("\n}\n", 1)[0]
    assert "requestPermission" in push_on
    # 사진 고르기: 카메라·앨범 둘 다 (capture 속성 없음)
    assert "capture" not in js
    # 입력 칸 16px 이상 (iOS 확대 방지), 본문 15px, 누르는 곳 44px 이상
    assert re.search(r"\.fl\{[^}]*font-size:16px", css) and re.search(r"body\{[^}]*font-size:15px", css)
    assert re.search(r"\.pb\{[^}]*min-height:48px", css) and "env(safe-area-inset-bottom" in css
    assert "@media (prefers-color-scheme:dark)" in css and "prefers-reduced-motion" in css


def test_service_worker_rules() -> None:
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "addEventListener('push'" in sw and "addEventListener('notificationclick'" in sw
    assert "showNotification" in sw and "renotify" in sw and "badge-72.png" in sw
    assert "req.method !== 'GET'" in sw  # 보내는 요청(POST)은 건드리지 않음
    assert "t === 'logout'" in sw and "caches.delete" in sw  # 로그아웃이면 저장본을 지움
    install = sw.split("addEventListener('install'", 1)[1].split("});", 1)[0]
    assert "skipWaiting" not in install  # 새 버전은 다음에 열 때 (화면이 갑자기 바뀌지 않게)
    assert "'/dashboard/api/state'" in sw and "x-sw-cache" in sw


async def test_stats_fragment_has_no_forms_or_scripts_and_skips_duplicates(bot: DealBot) -> None:
    h = session_headers(bot)
    res = await bot.web.dispatch("GET", "/dashboard/api/stats.html", h)
    assert res[0] == 200 and res[2].startswith("text/html")
    html = res[1]
    assert "<form" not in html and "<script" not in html and "/dashboard/refresh" not in html and "/dashboard/input" not in html
    assert 'id="card-telegram"' in html and 'data-action="stats.refresh"' in html
    for key in ("links", "blog_waiting", "dealbot"):  # 할 일 · 딜 탭 · 연결과 겹침
        assert f'id="card-{key}"' not in html, key
    assert html.startswith("<style>") and ".stx " in html


def test_stats_fragment_manual_button_carries_fields(bot: DealBot) -> None:
    views = bot.dashboard.views()
    manual = [v for v in views if v.manual]
    assert manual, "직접 넣는 카드가 하나는 있어야"
    html = render.stats_fragment(views=views, today=bot.dashboard.ctx.today(), tz="Asia/Seoul", spark_days=30)
    m = re.search(r'data-action="stats.input" data-src="([^"]+)" data-title="[^"]+" data-today="[0-9-]{10}" data-fields="([^"]+)"', html)
    assert m
    fields = json.loads(m.group(2).replace("&quot;", '"'))
    assert fields and all(set(f) == {"metric", "label"} for f in fields)


async def test_state_api_matches_boot_shape(bot: DealBot) -> None:
    h = session_headers(bot)
    _s, html, _h = await page(bot, h)
    boot = boot_of(html)
    status, state, _ = await get_json(bot, "/dashboard/api/state", h)
    assert status == 200
    assert set(boot) - {"etag"} <= set(state)
    assert {"todo", "deal", "blogs", "sns", "connect", "me", "counts", "pending", "digest"} <= set(boot)


def test_manifest_keeps_the_installed_app() -> None:
    m = webapp.manifest()
    assert m["id"] == "/dashboard" and m["start_url"] == "/dashboard/" and m["scope"] == "/dashboard"
    assert m["name"] == m["short_name"] == "Sns-board" and m["display"] == "standalone"
