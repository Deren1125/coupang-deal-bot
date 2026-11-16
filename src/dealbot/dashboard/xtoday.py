"""오늘 X 글 · 큰 사건 바로 알림 (주인 11/15 08:3x — "오늘 x 글 보내는게 아니라 대시보드에 띄워줘 그리고 젠슨황 사건같이 큰 사건
조회수 달달할거 같은사건은 바로 알림보내 대시보드 그렇게 세팅해줘").

화면은 static/xtoday.js (SNS 탭 X 칸 '오늘 X 글' 카드 → #/sns/xt). 글은 xstudio 와 같은 길로만 쓴다 (Studio.draft_src —
POST_SYSTEM · 숫자 검사 · 관점 한 줄 · '내 한마디' 빈칸 · 짧은 글+긴 글 · 모델은 Opus 까지: model_ok/assert_writer_model).
카드의 '열기'는 그 초안을 xstudio 글쓰기 화면에 그대로 넣는다 → '내 한마디'를 직접 써야 'X에서 올리기'가 켜지고, 올리기는 주인 손.

A. 오늘 X 글 — 1분 일(scheduler)에서 tick():
   · 아침 x_today.morning (07:40) 뒤 처음 도는 때 그날 초안을 만든다. 빈 시각이 남으면 x_today.fills (11:20·17:20) 에 보충 (2번까지).
   · 글감: 큰 사건(B) 먼저 → 화제 목록(Blog-Auto x_hot — xstudio 의 hot 과 같은 것). 같은 사건은 한 번만 (7일 기억 kv xtoday_used —
     오늘 카드끼리 · 어제 카드 · 큰 사건과 화제가 같은 이야기일 때). 올린 글 기록(dash_x_posts)에는 카드 번호(topic)가 남는다.
   · 시각은 xstudio.post_times(그날) 열 (주인 11/16 "하루 10개 정도" — POST_TIMES 07:30~22:30 을 날마다 ±10분 흔듦).
     글감 하나 = AI 한 번. 서버가 스스로 쓰는 초안은 하루 auto_drafts_per_day(13 = 카드 10 + 큰 사건 3)번, 주인 몫 reserve(15)번은
     남긴다 (xstudio 하루 50번 안). 실패하면 그 차례는 끝 — 다음 보충 때만 다시 (돌려 가며 다시 부르지 않음).
   · '팔로워 목표' 글 (주인 11/16 — "중간중간 '팔로워 몇 명이 목표다, 팔로우 부탁드린다' 같은 글도"): 하루 1편 · 7일에 3편까지 ·
     앞 글에서 이틀 넘게 띄움 · 저녁 시각 하나를 씀 (GOAL_FROM 뒤). 팔로워 수(dash_x_stats, 7일 안 기록)를 알면 '다음 목표 N명', 모르면 숫자 없이.
     글은 xstudio.draft_goal (GOAL_SYSTEM — 감사·소통 · 대가 약속·맞팔·스팸 금지 · 최근 14일 문구와 겹치면 버림). kv xtoday_goal 에는
     만든 때·목표·첫 줄·낱말만 14일.
   · 카드: 출처 기사 제목·링크 · 숫자 검사 통과 여부(원문에 없는 숫자 경고) · 첫 답글(출처) · 확인 안 된 이야기 표시.
   · 카드마다 [복사]·[긴 글 복사]·[첫 답글 복사] + '올렸어요' 체크 상자 (주인 11/15): 체크 = xstudio posted {op:add, tid} 그대로
     (오늘 올린 글 수·dash_x_posts 같이) · 다시 누르면 undo (10분 안) · 화면 값 done·undo_s 는 서버 기록에서.
B. 큰 사건 바로 알림 — big.poll_minutes(20분)마다:
   · 네이버 공식 뉴스 검색 API (키만 — 로그인·크롤링 없음 · 기사 페이지를 열지 않음 · 딜봇 키가 막히면 Blog-Auto .env 키: ssalmuk 과 같은 길)
     + 화제 목록의 출처 기사 제목. 네이버 키가 둘 다 막히면(11/15 — 앱이 지워져 401) 카카오(다음) 웹문서 검색 최신순으로 이어서
     (KAKAO_REST_API_KEY — 기사 제목·주소·매체 도메인·시각만, 블로그·카페·영상 주소는 뺌). 셋 다 막히면 6시간 쉰다 (돌려 가며 다시 부르지 않음).
   · 점수는 규칙만 (AI 판정 없음 — score()): 돈 크기 0~5 + 이름(인물 3·회사 2·AI 1, 4까지) + 센 낱말 0~2 + 매체 수 0~4 + 속도 0~2.
     같은 이름 + 같은 금액(또는 같은 센 낱말)을 window_minutes 안에 쓴 기사끼리 한 사건. 사망·참사·정치 기사는 뺀다.
     알림: 이름이 있고(또는 10조 넘는 돈) · 매체 min_outlets(3)곳 넘게 · 점수 min_score(9) 넘게.
   · 같은 사건 한 번 (72시간) · 같은 이름은 하루 한 번 · 하루 per_day(3)개 · 알림 사이 gap_minutes(90분) · quiet_hours 면 미뤘다가.
   · 알림: notify.router 급한 종류 x_big (딜봇 전용 LOCAL_URGENT — 두 저장소 계약 표 밖 · 카톡 예비 없음). 누르면 #/sns/xt/<번호>.
     초안은 미리 만들어 두거나(predraft) 화면에서 '초안 만들기'.

  GET  /dashboard/api/xtoday/state   오늘 카드·큰 사건·남은 몫 (no-store)
  POST /dashboard/api/xtoday/make    {id: 큰 사건 번호} → 그 사건 초안 · {op: "fill"} → 지금 보충 (10분에 한 번 · 뒤에서 만듦)
  POST /dashboard/api/xtoday/skip    {id} → 카드·큰 사건 안 쓰기 (알림 닫힘)
  POST /dashboard/api/xtoday/react   {key, done} → '오늘 반응 할 것' 줄 체크 (주인 11/16 체크리스트 — 링크 열기는 주인 손, 기록은 그날 kv xtoday_react 만)
C. 체크리스트 (주인 11/16 "X 는 책갈피 굳이 안 쓰는 게 나을 것 같아. 그냥 체크리스트 느낌으로만"): 카드는 '오늘 X n/10 올림' + 글마다
   [복사]·[✓ 올렸어요] 한 줄 (올린 글은 취소 10분이 지나면 카드에서 빠짐) · '오늘 반응 할 것' = 반응 목록(xengage 큐)·자주 보는 계정에서
   REACT_MAX 줄 — [열기](X 링크, 주인이 누름) + [✓ 했어요]. 자동 클릭·X 요청 없음 그대로. 반응 연속 모드·X 도우미 책갈피는 설정으로 숨김.
모두 로그인 아래 · POST 는 같은 사이트 + X-Dash-Token (DashApi.guard).
하지 않는 것: X 로그인 · X API · 자동 게시 · 남의 타임라인 긁기 · 기사 페이지 긁기. 초안 글은 화면에 띄우려고 kv xtoday 에 하루만 둔다
(날이 바뀌면 다음 1분 일에서 지움 — 로그에는 종류·길이만).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

from dealbot.dashboard import naverhub
from dealbot.dashboard import xstudio as xs

log = logging.getLogger(__name__)

BASE = "/dashboard/api/xtoday"
STATE_PATH, MAKE_PATH, SKIP_PATH, REACT_PATH = f"{BASE}/state", f"{BASE}/make", f"{BASE}/skip", f"{BASE}/react"
KV_REACT = "xtoday_react"  # {"day", "done": [줄 키]} — '오늘 반응 할 것' 체크 (그날만)
REACT_MAX = 5  # '오늘 반응 할 것' 줄 수 (주인 11/16 '체크 줄 몇 개')
REACT_KEY_RE = re.compile(r"^(?:p[0-9]{5,20}|h[a-z0-9_]{1,15})$")  # p+글 번호 (반응 목록) · h+아이디 (자주 보는 계정)
KV = "xtoday"  # {"day", "items": [...], "runs": {이름: 시각}, "ai": n, "err": "", "skip": [카드 번호], "doing": 만드는 중인 일, "resumed": 이어서 한 번 했나}
KV_USED = "xtoday_used"  # [{"k": 글감 키, "toks": [...], "lead": 이름, "nums": [...], "at": 시각}] — 같은 사건을 다시 쓰지 않게 (7일)
KV_GOAL = "xtoday_goal"  # [{"at": 시각, "day", "goal": n|None, "line": 첫 줄, "toks": [...]}] — 팔로워 목표 글 (14일)
GOAL_KEEP_S, GOAL_PER_WEEK, GOAL_GAP_DAYS = 14 * 86400, 3, 2  # 문구 비교 14일 · 7일에 3편 · 앞 글에서 이틀 띄움
GOAL_FROM = "18:30"  # 이 뒤의 빈 시각에 (없으면 남은 마지막 시각)
GOAL_STATS_S = 7 * 86400  # 팔로워 수 기록이 이보다 오래면 숫자 없이
KV_BIG = "xbig"  # {"day", "api": n, "last": 시각, "dead_until": 시각, "events": [...], "log": [...]}
USED_KEEP_S, USED_MAX = 7 * 86400, 200
EVENT_KEEP_S, EVENT_MAX, EVENT_ARTS = 72 * 3600, 40, 6
EVENT_SHOW_S = 24 * 3600  # 화면에 보이는 동안
LEAD_GAP_S = 24 * 3600  # 같은 이름은 하루 한 번
HELD_MAX_S = 8 * 3600  # 조용한 시간에 미룬 알림은 이만큼 지나면 버림
NOTICE_TTL_H = 6.0  # 알림 줄이 열려 있는 시간 (뉴스는 금방 식는다)
KEY_DEAD_S = 6 * 3600  # 키가 막히면 이만큼 쉼
BLOG_KEY_PER_DAY = 600  # 빌려 쓰는 Blog-Auto 키로는 하루 이만큼만 (그 키 하루 25,000번은 블로그·이벤트 참여 몫 — api_per_day 를 올려도 여기까지)
BORROWED_SRCS = ("blog", "kakao")  # 하루 BLOG_KEY_PER_DAY 를 지키는 키 (카카오 키도 이벤트 참여 검색과 같이 씀)
HOT_MAX_AGE_S = 18 * 3600  # 이보다 오래된 화제는 오늘 글감으로 안 씀
SLOT_GRACE_MIN = 30  # 추천 시각이 이만큼 지나기 전까지는 그 시각 카드를 만든다
FILL_WINDOW_MIN = 90  # 보충 시각 뒤 이 안에 처음 도는 때만
FILL_TAP_S = 600.0  # '지금 보충하기'는 10분에 한 번
LOG_KEEP = 30
NEWS_URL = "https://openapi.naver.com/v1/search/news.json"  # 예전 개발자센터 주소 (HUB 먼저 — naverhub.url("news", True))
NEWS_TIMEOUT = 6.0
NEWS_DISPLAY = 100
KAKAO_SIZE = 50  # 카카오 웹문서 검색 한 번에 50개까지
KAKAO_RECHECK_S = 86400.0  # 카카오로 넘어간 뒤 네이버 키는 하루 한 번만 다시 봄 (ssalmuk.KEY_RECHECK 와 같게)
# 카카오 웹문서는 뉴스만이 아니라서 — 블로그·카페·영상·사전·커뮤니티·쇼핑·앱 장터 주소는 매체로 세지 않음 (매체 수가 점수에 들어감 — 오탐 방지)
NOT_OUTLET_RE = re.compile(
    r"(?:^|\.)(?:blog\.[a-z0-9.-]+|cafe\.[a-z0-9.-]+|tistory\.com|brunch\.co\.kr|velog\.io|youtube\.com|youtu\.be|"
    r"namu\.wiki|wikipedia\.org|instagram\.com|facebook\.com|x\.com|twitter\.com|threads\.net|threads\.com|tiktok\.com|"
    r"kakao\.com|t\.me|band\.us|reddit\.com|medium\.com|linkedin\.com|pinterest\.[a-z.]+|github\.com|github\.io|substack\.com|"
    r"blogspot\.com|wordpress\.com|egloos\.com|postype\.com|notion\.site|"
    r"dcinside\.com|fmkorea\.com|clien\.net|ppomppu\.co\.kr|theqoo\.net|ruliweb\.com|mlbpark\.donga\.com|inven\.co\.kr|"
    r"bobaedream\.co\.kr|instiz\.net|82cook\.com|todayhumor\.co\.kr|humoruniv\.com|etoland\.co\.kr|dogdrip\.net|"
    r"arca\.live|quasarzone\.com|blind\.com|teamblind\.com|"
    r"coupang\.com|gmarket\.co\.kr|11st\.co\.kr|auction\.co\.kr|danawa\.com|aliexpress\.com|"
    r"play\.google\.com|apps\.apple\.com)$")
# 포털은 뉴스 칸만 매체로 (지식iN·포스트·쇼핑·스마트스토어·카페 등은 뺌)
PORTAL_RE = re.compile(r"(?:^|\.)(?:naver\.com|daum\.net|nate\.com|zum\.com)$")
PORTAL_NEWS_RE = re.compile(r"(?:^|\.)(?:news\.naver\.com|v\.daum\.net|news\.daum\.net|news\.nate\.com|news\.zum\.com)$")


def not_outlet(host: str) -> bool:
    return bool(NOT_OUTLET_RE.search(host) or (PORTAL_RE.search(host) and not PORTAL_NEWS_RE.search(host)))


BODY_MAX = 1024
ID_RE = re.compile(r"^(?:t[0-9a-f]{10}|[0-9a-f]{8})$")

# ── 큰 사건 점수 (규칙만 — 숫자·이름 사전·매체 수·속도) ────────────────────────────────────────────────────────────
# 이름 사전: 값은 정규식 조각 (헷갈리는 낱말은 앞뒤를 봄 — '파인애플'·'메타버스'·'토스트'). 설정 big.names 로 보탤 수 있다 (회사로 셈)
VIPS: dict[str, str] = {
    "젠슨 황": r"젠슨\s?황", "일론 머스크": r"머스크", "샘 올트먼": r"올트먼|알트먼", "이재용": r"이재용", "최태원": r"최태원",
    "정의선": r"정의선", "구광모": r"구광모", "마크 저커버그": r"저커버그", "제프 베이조스": r"베이조스|베조스", "팀 쿡": r"팀\s?쿡",
    "손정의": r"손정의", "워런 버핏": r"버핏", "빌 게이츠": r"빌\s?게이츠", "리사 수": r"리사\s?수", "순다르 피차이": r"피차이",
    "사티아 나델라": r"나델라", "김범석": r"김범석", "김범수": r"김범수", "이해진": r"이해진", "방시혁": r"방시혁", "마윈": r"마윈",
}
FIRMS: dict[str, str] = {
    "엔비디아": r"엔비디아|NVIDIA", "삼성전자": r"삼성전자", "SK하이닉스": r"하이닉스", "테슬라": r"테슬라",
    "애플": r"(?<![가-힣])애플(?!망고|리케이션)|아이폰", "오픈AI": r"오픈\s?AI|OpenAI|챗GPT", "구글": r"구글|알파벳",
    "메타": r"(?<![가-힣])메타(?!버스|인지|데이터|세쿼이아|몽|볼)", "마이크로소프트": r"마이크로소프트|(?<![A-Za-z])MS(?![A-Za-z])",
    "아마존": r"아마존", "TSMC": r"TSMC", "앤트로픽": r"앤트로픽|앤스로픽|Anthropic", "xAI": r"(?<![A-Za-z])xAI(?![A-Za-z])",
    "스페이스X": r"스페이스\s?X", "딥시크": r"딥시크", "소프트뱅크": r"소프트뱅크", "팔란티어": r"팔란티어",
    "인텔": r"(?<![가-힣])인텔(?!리)", "AMD": r"(?<![A-Za-z])AMD(?![A-Za-z])", "넷플릭스": r"넷플릭스", "현대차": r"현대차|현대자동차",
    "LG에너지솔루션": r"LG에너지솔루션|LG엔솔", "네이버": r"네이버", "카카오": r"카카오", "쿠팡": r"쿠팡",
    "토스": r"(?<![가-힣])토스(?!트|카나)", "하이브": r"(?<![가-힣])하이브(?!리드)", "두나무": r"두나무|업비트", "비트코인": r"비트코인",
}
AI_RE = re.compile(r"(?<![A-Za-z])AI(?![A-Za-z])|인공지능|GPU|LLM|데이터센터")
SHOCK_RE = re.compile(r"증발|폭락|폭등|급락|급등|역대|사상\s*최(?:대|고|초|저)|최초|신기록|해고|감원|구조조정|파산|부도|상장폐지|인수|매각|합병|"
                      r"사임|해임|경질|해킹|유출|과징금|벌금|소송|배상|기부|상속|연봉|보너스|성과급|스톡옵션|잭팟|쇼크|충격|먹통|리콜|파업|횡령|배임")
# 정치 기사는 뺀다 (주인 안전선 — 어느 편도 들지 않는다 · xstudio POST_SYSTEM 과 같은 결)
POLITICS_RE = re.compile(r"대통령|국회|여당|야당|민주당|국민의힘|탄핵|총선|대선|지방선거|국정감사|국감|의원|장관|특검|검찰|북한|김정은|계엄")
_UNITS = {"조": 1e12, "천억": 1e11, "백억": 1e10, "십억": 1e9, "억": 1e8, "천만": 1e7, "백만": 1e6, "십만": 1e5, "만": 1e4}
_PART = r"\d[\d,]*(?:\.\d+)?\s*(?:조|천억|백억|십억|억|천만|백만|십만|만)"
MONEY_RE = re.compile(rf"((?:{_PART}\s*)+)(?:여\s*)?(달러|유로|파운드|위안|엔|원)?")
_PART_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(조|천억|백억|십억|억|천만|백만|십만|만)")
NOT_MONEY_RE = re.compile(r"\s*(?:여\s*)?(?:명|개|건|대|회|배|년|주|톤|곳|번|가구|세대|마리|장|편|평|병|잔|통|권|뷰|km|㎞|㎡|시간|관객|구독|조회|팔로)")
# 돈 크기를 견주려는 어림값 (원으로) — 점수에만 쓴다. 글·화면에는 기사에 적힌 표기(shown)만 나간다
RATE_FOR_SCORE = {"달러": 1400.0, "유로": 1500.0, "파운드": 1800.0, "위안": 190.0, "엔": 9.0, "원": 1.0, "": 1.0}
MONEY_TIERS = ((1e14, 5), (1e13, 4), (1e12, 3), (1e11, 2), (1e10, 1))  # 100조·10조·1조·1,000억·100억 원
OUTLET_TIERS = ((12, 4), (8, 3), (5, 2), (3, 1))
SPEED_TIERS = ((10, 2), (5, 1))  # 최근 30분 기사 수
SPEED_S = 30 * 60


def plain(v: Any, limit: int) -> str:
    """API 가 준 글(<b>·&quot; 섞임) → 한 줄."""
    return xs.one_line(html.unescape(re.sub(r"<[^>]+>", "", str(v or ""))), limit)


def web_link(v: Any) -> str:
    return v if isinstance(v, str) and len(v) <= 500 and re.match(r"^https?://[^\s\"'<>]+$", v) else ""


def outlet(link: str) -> str:
    """기사 주소 → 매체 (호스트 — 흔한 앞머리는 뗌)."""
    try:
        host = (urlsplit(link).hostname or "").lower()
    except ValueError:
        return ""
    return re.sub(r"^(?:www|m|mobile|news|biz|view|it|n)\.", "", host)


def article(x: Any, now: float) -> dict[str, Any] | None:
    """뉴스 검색 API 한 줄 → {title, desc, link, outlet, at}. 제목·주소가 없으면 None."""
    if not isinstance(x, dict):
        return None
    title, link = plain(x.get("title"), 160), web_link(x.get("originallink")) or web_link(x.get("link"))
    if not title or not link:
        return None
    at = now
    try:
        at = min(now, parsedate_to_datetime(str(x.get("pubDate") or "")).timestamp())
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    return {"title": title, "desc": plain(x.get("description"), 300), "link": link, "outlet": outlet(link), "at": at}


def who(text: str, extra: list[str] | tuple[str, ...] = ()) -> tuple[list[str], list[str]]:
    """글 속 (인물, 회사) — 사전에 있는 이름만, 사전 순서."""
    people = [k for k, pat in VIPS.items() if re.search(pat, text, re.I)]
    firms = [k for k, pat in FIRMS.items() if re.search(pat, text, re.I)]
    firms += [n for n in extra if n and n not in firms and n not in people and n in text]
    return people, firms


def money(text: str) -> tuple[float, str]:
    """글 속 가장 큰 돈 → (원으로 어림한 크기, 기사 표기 그대로 붙여 쓴 것 '1억800만달러'). 없으면 (0, '').
    '3만 명'·'5억 뷰'처럼 돈이 아닌 수, 단위가 '억' 아래인데 화폐가 없는 수는 안 봄."""
    best, shown = 0.0, ""
    for m in MONEY_RE.finditer(text or ""):
        cur = m.group(2) or ""
        parts = _PART_RE.findall(m.group(1))
        if not cur and (NOT_MONEY_RE.match(text, m.end()) or max(_UNITS[u] for _n, u in parts) < 1e8):
            continue
        try:
            amount = sum(float(n.replace(",", "")) * _UNITS[u] for n, u in parts) * RATE_FOR_SCORE[cur]
        except ValueError:
            continue
        if amount > best:
            best, shown = amount, re.sub(r"\s+", "", m.group(1)) + cur
    return best, shown


def _tier(v: float, tiers: tuple[tuple[float, int], ...]) -> int:
    return next((s for top, s in tiers if v >= top), 0)


def clusters(arts: list[dict[str, Any]], now: float, *, window_s: float, extra: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """기사들 → 사건 묶음. 한 사건 = 같은 이름(인물 먼저) + 같은 금액 표기 (금액이 없으면 같은 센 낱말).
    사망·참사·정치 기사, 사전에 없는 이름만 나오는 기사는 묶지 않는다 (10조 넘는 돈은 이름 없이도)."""
    out: dict[str, dict[str, Any]] = {}
    for a in arts:
        t = a["title"]
        if now - a["at"] > window_s or xs.TRAGIC_RE.search(t) or POLITICS_RE.search(t):
            continue
        people, firms = who(t, extra)
        won, shown = money(t)
        shocks = sorted(set(SHOCK_RE.findall(t)))
        lead = (people or firms or [""])[0]
        if not lead and _tier(won, MONEY_TIERS) < 4:
            continue
        mark = shown or (shocks[0] if shocks else "")
        if not mark:
            continue
        c = out.setdefault(f"{lead}|{mark}", {"key": f"{lead}|{mark}", "lead": lead, "shown": shown, "arts": [], "people": set(), "firms": set(),
                                             "shocks": set(), "won": 0.0, "ai": False})
        c["arts"].append(a)
        c["people"].update(people)
        c["firms"].update(firms)
        c["shocks"].update(shocks)
        c["won"] = max(c["won"], won)
        c["ai"] = c["ai"] or bool(AI_RE.search(t))
    return list(out.values())


def score(c: dict[str, Any], now: float, *, min_score: int, min_outlets: int) -> dict[str, Any]:
    """사건 묶음 하나의 점수와 알림 여부. parts = {m 돈, w 이름, s 센 낱말, o 매체 수, v 속도}."""
    outlets = sorted({a["outlet"] for a in c["arts"] if a["outlet"]})
    m = _tier(c["won"], MONEY_TIERS)
    w = min(4, (3 if c["people"] else 0) + (2 if c["firms"] else 0) + (1 if c["ai"] else 0))
    s = min(2, len(c["shocks"]))
    o = _tier(len(outlets), OUTLET_TIERS)
    v = _tier(sum(1 for a in c["arts"] if now - a["at"] <= SPEED_S), SPEED_TIERS)
    total = m + w + s + o + v
    why = ""
    if w < 2 and m < 4:
        why = "큰 이름 없음"
    elif len(outlets) < min_outlets:
        why = f"매체 {len(outlets)}곳"
    elif m < 1 and s < 1:
        why = "큰 숫자·센 낱말 없음"
    elif total < min_score:
        why = f"점수 {total}"
    arts = sorted(c["arts"], key=lambda a: (not (c["shown"] and c["shown"][:3] in a["title"].replace(" ", "")), len(a["title"])))
    seen: set[str] = set()
    top = [a for a in arts if not (a["outlet"] in seen or seen.add(a["outlet"]))][:EVENT_ARTS]  # 매체마다 한 편
    return {"key": c["key"], "lead": c["lead"], "shown": c["shown"], "title": top[0]["title"], "outlets": len(outlets), "score": total,
            "parts": {"m": m, "w": w, "s": s, "o": o, "v": v}, "ok": not why, "why": why,
            "arts": [{"title": a["title"], "desc": a["desc"], "link": a["link"]} for a in top]}


def event_id(key: str) -> str:
    return hashlib.sha256(("xbig:" + key).encode("utf-8")).hexdigest()[:8]


def item_id(key: str) -> str:
    return "t" + hashlib.sha256(("xtoday:" + key).encode("utf-8")).hexdigest()[:10]


# ── 같은 사건인지 ─────────────────────────────────────────────────────────────────────────────
def _toks(text: str) -> set[str]:
    return {t for t in re.findall(r"[0-9A-Za-z가-힣]{2,}", (text or "").lower())}


def sig(keyword: str, titles: list[str], lead: str = "") -> dict[str, Any]:
    """글감의 지문: 낱말(키워드) · 이름(사전) · 두 자리 넘는 숫자(제목)."""
    text = " ".join([keyword, *titles])
    if not lead:
        people, firms = who(text)
        lead = (people or firms or [""])[0]
    nums = sorted({n for t in titles for n in xs.nums(t) if len(n.replace(".", "")) >= 2})[:12]
    return {"toks": sorted(_toks(keyword))[:12], "lead": lead, "nums": nums}


def same_event(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """두 글감이 같은 이야기인가: 같은 이름 + 같은 숫자, 또는 키워드 낱말이 절반 넘게 겹침."""
    if a.get("lead") and a.get("lead") == b.get("lead") and set(a.get("nums") or []) & set(b.get("nums") or []):
        return True
    ta, tb = set(a.get("toks") or []), set(b.get("toks") or [])
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, xs.KST).strftime("%H:%M")


def _mins(hm: str) -> int:
    h, _, m = hm.partition(":")
    return int(h) * 60 + int(m)


def in_quiet(spec: str, now: float) -> bool:
    """'01:00-07:00' 안인가 (자정을 넘는 구간도). 비었거나 이상하면 False."""
    m = re.fullmatch(r"(\d\d):(\d\d)-(\d\d):(\d\d)", spec or "")
    if not m:
        return False
    s, e, cur = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4)), _mins(_hm(now))
    return s <= cur < e if s < e else s != e and (cur >= s or cur < e)


# ── 뉴스 검색 (네이버 공식 Open API — 키만) ─────────────────────────────────────────────────────────
def kakao_rows(data: Any) -> list[dict[str, Any]]:
    """카카오 웹문서 검색 답 → 네이버 뉴스 줄 꼴 {title, link, originallink, pubDate} (요약은 안 씀 — 제목·주소·매체·시각만).
    블로그·카페·영상·커뮤니티·쇼핑 주소는 뺀다 (not_outlet) · 쓴 시각을 모르는 줄도 뺀다 — article() 이 시각 없는 줄을 '지금'으로 보기 때문에
    (오래된 글이 방금 난 기사로 세어져 속도·매체 수가 부풀지 않게)."""
    from dealbot.dashboard.ssalmuk import kakao_items

    items, _total = kakao_items(data, "news")
    out = []
    for it in items:
        link = it.get("link") or ""
        try:
            host = (urlsplit(link).hostname or "").lower()
        except ValueError:
            continue
        if not host or not_outlet(host) or not it.get("pubDate"):
            continue
        out.append({"title": it.get("title") or "", "link": link, "originallink": link, "pubDate": it["pubDate"]})
    return out


class NaverNews:
    """최신 뉴스 제목·요약. 네이버 키마다 API HUB 먼저 → 401/403 이면 예전 주소 (11/15 — naverhub). 키는 딜봇 것 → 막히면(401·403) Blog-Auto .env 것 (ssalmuk 검색과 같은 길 · 값은 어디에도 안 남김)
    → 그것도 막히면 카카오(다음) 웹문서 최신순 (11/15 — KAKAO_REST_API_KEY: 딜봇 환경, 없으면 Blog-Auto .env 한 줄).
    카카오로 된 뒤엔 카카오 먼저, 네이버 키는 하루 한 번만 다시 봄.
    search() 는 기사 줄 목록, 못 읽으면 None, 쓸 키가 없으면 dead=True (부르는 쪽이 6시간 쉼)."""

    def __init__(self, bot: Any, *, clock: Callable[[], float] = time.time) -> None:
        from dealbot.dashboard.ssalmuk import BlogKeyFile

        self.bot = bot
        self.blog_keys = BlogKeyFile(lambda: bot.settings.dashboard.ssalmuk_search_env_file)
        self.src = ""  # 통했던 키
        self.dead = False
        self.clock = clock
        self.naver_try = 0.0  # 네이버 키를 마지막으로 써 본 때 (메모리)
        self.via = ""  # 네이버로 받았을 때 'hub' (API HUB) | 'old' (예전 개발자센터 주소)

    def _kakao(self) -> str | None:
        return (getattr(self.bot.settings.secrets, "kakao_rest_api_key", None) or "").strip() or self.blog_keys.kakao()

    def _keys(self) -> dict[str, tuple[str, str]]:
        sec, keys = self.bot.settings.secrets, {}
        if sec.naver_client_id and sec.naver_client_secret:
            keys["dealbot"] = (sec.naver_client_id, sec.naver_client_secret)
        blog = self.blog_keys.get()
        if blog:
            keys["blog"] = blog
        return keys

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        import httpx

        keys = self._keys()
        kakao = self._kakao()
        naver = [k for k in ([self.src] if self.src in keys else []) + [k for k in ("dealbot", "blog") if k != self.src] if k in keys]
        now = self.clock()
        if self.src == "kakao" and kakao and now - self.naver_try < KAKAO_RECHECK_S:
            order = ["kakao", *naver]  # 네이버 키는 하루 한 번만 다시 봄
        else:
            order = [*naver, *(["kakao"] if kakao else [])]
        if not order:
            self.dead = True
            return None
        for src in order:
            try:
                if src == "kakao":
                    r = await self.bot.http.get("https://dapi.kakao.com/v2/search/web",
                                                params={"query": query, "sort": "recency", "size": KAKAO_SIZE, "page": 1},
                                                headers={"Authorization": f"KakaoAK {kakao}"}, timeout=NEWS_TIMEOUT)
                else:
                    self.naver_try = now
                    cid, csec = keys[src]
                    # 11/15 주인 키는 네이버 API HUB 키 — HUB 먼저, 401/403 이면 예전 주소로 (naverhub)
                    r, hub, _hs = await naverhub.get(self.bot.http, "news", cid, csec, timeout=NEWS_TIMEOUT,
                                                     params={"query": query, "display": NEWS_DISPLAY, "sort": "date"})
                    self.via = "hub" if hub else "old"
            except httpx.HTTPError as e:  # 키·주소는 로그에 안 남김
                log.info("xbig news: %s (%s key)", type(e).__name__, src)
                return None
            if r.status_code in (401, 403):  # 이 키는 막힘 — 다른 키로 한 번
                log.info("xbig news: HTTP %s (%s key)", r.status_code, src)
                continue
            if r.status_code != 200:
                log.info("xbig news: HTTP %s (%s key)", r.status_code, src)
                return None
            try:
                data = r.json()
            except ValueError:
                return None
            self.src, self.dead = src, False
            if src == "kakao":
                return kakao_rows(data)
            items = data.get("items") if isinstance(data, dict) else None
            return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
        self.dead = True
        return None


# ── 본체 ─────────────────────────────────────────────────────────────────────────────────
class XToday:
    """kv 에 둔 오늘 카드·큰 사건 + 만들기. studio = xstudio.Studio (글은 그쪽 길로만), cfg() = XTodayConfig,
    router() = NotifyRouter | None, news = NaverNews 같은 것 (search·dead) | None. clock 은 시험에서 갈아 끼운다."""

    def __init__(self, db: Any, studio: xs.Studio, *, cfg: Callable[[], Any], router: Callable[[], Any] = lambda: None,
                 news: Any = None, clock: Callable[[], float] = time.time) -> None:
        self.db, self.studio, self.cfg, self.router, self.news, self.clock = db, studio, cfg, router, news, clock
        self.pool: dict[str, dict[str, Any]] = {}  # 기사 주소 → 기사 (메모리 — window 안 것만)
        self.lock = asyncio.Lock()  # 만들기는 한 번에 하나
        self.bg: asyncio.Task[Any] | None = None
        self.fill_at = 0.0
        self.fill_task: asyncio.Task[Any] | None = None
        self.engage: Any = None  # xengage.Engage (DashApi 가 건다 — 반응 목록이 있으면 '오늘 반응 할 것' 앞쪽에)
        studio.on_posted = self.mark_posted

    # ---------------------------------------------------------- kv
    def _load(self, key: str) -> dict[str, Any]:
        try:
            v = json.loads(self.db.kv_get(key) or "null")
        except ValueError:
            v = None
        return v if isinstance(v, dict) else {}

    def _save(self, key: str, v: Any) -> None:
        self.db.kv_set(key, json.dumps(v, ensure_ascii=False, separators=(",", ":")))

    def plan(self, now: float | None = None) -> dict[str, Any]:
        """오늘 계획 (날이 바뀌면 새로 — 어제 초안은 여기서 사라진다)."""
        day = xs._day(self.clock() if now is None else now)
        st = self._load(KV)
        if st.get("day") != day:
            st = {"day": day}
        st["items"] = [i for i in st.get("items") or [] if isinstance(i, dict) and isinstance(i.get("id"), str) and isinstance(i.get("res"), dict)]
        st["runs"] = st["runs"] if isinstance(st.get("runs"), dict) else {}
        st["skip"] = [x for x in st.get("skip") or [] if isinstance(x, str)][-40:]
        st["ai"] = int(st.get("ai") or 0)
        st["err"] = str(st.get("err") or "")
        st["doing"], st["resumed"] = str(st.get("doing") or ""), st.get("resumed") is True
        return st

    def big(self, now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else now
        st = self._load(KV_BIG)
        if st.get("day") != xs._day(now):
            st.update(day=xs._day(now), api=0)
        st["events"] = [e for e in st.get("events") or [] if isinstance(e, dict) and isinstance(e.get("id"), str)
                        and isinstance(e.get("at"), (int, float)) and now - e["at"] <= EVENT_KEEP_S][-EVENT_MAX:]
        st["log"] = [x for x in st.get("log") or [] if isinstance(x, dict)][-LOG_KEEP:]
        for k in ("last", "dead_until"):
            st[k] = float(st[k]) if isinstance(st.get(k), (int, float)) else 0.0
        st["api"] = int(st.get("api") or 0)
        return st

    def used(self, now: float) -> list[dict[str, Any]]:
        try:
            rows = json.loads(self.db.kv_get(KV_USED) or "[]")
        except ValueError:
            rows = []
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and now - r["at"] <= USED_KEEP_S][-USED_MAX:] \
            if isinstance(rows, list) else []

    def _use(self, key: str, sg: dict[str, Any], now: float) -> None:
        self._save(KV_USED, [*[r for r in self.used(now) if r.get("k") != key], {"k": key, **sg, "at": now}][-USED_MAX:])

    # ---------------------------------------------------------- B. 큰 사건
    def _hot_arts(self, now: float) -> list[dict[str, Any]]:
        """화제 목록의 출처 기사 제목도 같은 저울에 (처음 본 때를 기사 시각으로)."""
        out = []
        for h in self.studio.hot_list():
            for s in h.get("sources") or []:
                link = web_link((s or {}).get("link")) if isinstance(s, dict) else ""
                title = plain(s.get("title"), 160) if link else ""
                if title and link not in self.pool:
                    out.append({"title": title, "desc": "", "link": link, "outlet": outlet(link), "at": now})
        return out

    async def poll(self, now: float | None = None) -> list[dict[str, Any]]:
        """뉴스를 한 번 보고 알림을 낸다 (간격·하루 몫 안에서). 알린 사건들."""
        now = self.clock() if now is None else now
        cfg = self.cfg().big
        st = self.big(now)
        if not cfg.enabled or now - st["last"] < cfg.poll_minutes * 60 - 5:
            return []
        st["last"] = now
        self._save(KV_BIG, st)  # 먼저 적는다 — 중간에 오류가 나도 간격은 지킴
        fresh: list[dict[str, Any]] = list(self._hot_arts(now))
        api, dead = st["api"], st["dead_until"]
        if self.news is not None and now >= dead:
            for q in cfg.queries:
                if api >= cfg.api_per_day or (getattr(self.news, "src", "") in BORROWED_SRCS and api >= BLOG_KEY_PER_DAY):
                    break
                api += 1
                rows = await self.news.search(q)
                if getattr(self.news, "dead", False):
                    dead = now + KEY_DEAD_S
                    log.warning("xbig: 뉴스 검색 키(네이버·카카오)를 쓸 수 없어요 — %d시간 쉼", KEY_DEAD_S // 3600)
                    break
                if rows is None:  # 못 읽음 (한도·서버 오류·시간 초과) — 남은 검색어로 더 두드리지 않고 다음 차례에
                    break
                fresh += [a for a in (article(x, now) for x in rows or []) if a]
        st = self.big(now)  # 뉴스를 기다리는 동안 주인이 누른 것(안 써요·올렸어요)을 덮어쓰지 않게 다시 읽는다 — 아래로는 기다림 없음
        st.update(last=now, api=api, dead_until=dead)
        src = getattr(self.news, "src", "") if self.news is not None else ""
        if src:  # 어느 키로 통했나 (값 없이 이름만) — 'dealbot_hub'·'blog_hub' = 네이버 API HUB · 'kakao' (11/15)
            st["keysrc"] = f"{src}_hub" if src != "kakao" and getattr(self.news, "via", "") == "hub" else src
        window = cfg.window_minutes * 60
        for a in fresh:
            self.pool.setdefault(a["link"], a)
        self.pool = {k: a for k, a in self.pool.items() if now - a["at"] <= window}
        cands = sorted((score(c, now, min_score=cfg.min_score, min_outlets=cfg.min_outlets)
                        for c in clusters(list(self.pool.values()), now, window_s=window, extra=cfg.names)),
                       key=lambda c: -c["score"])
        st["log"] = (st["log"] + [{"at": now, "lead": c["lead"], "shown": c["shown"], "title": c["title"][:80], "score": c["score"],
                                   "parts": c["parts"], "outlets": c["outlets"], "why": c["why"]} for c in cands[:5]])[-LOG_KEEP:]
        quiet = in_quiet(cfg.quiet_hours, now)
        sent = self._release(st, cfg, now) if not quiet else []
        for c in cands:
            if not c["ok"] or self._known(st, c, now):
                continue
            ev = {"id": event_id(c["key"]), "key": c["key"], "lead": c["lead"], "shown": c["shown"], "title": c["title"], "outlets": c["outlets"],
                  "score": c["score"], "parts": c["parts"], "arts": c["arts"], "at": now, "state": "held"}
            if any(same_event(sig(c["lead"], [a["title"] for a in c["arts"]], c["lead"]), u) for u in self.used(now)):
                continue  # 이미 오늘 X 글 카드로 만든 이야기
            st["events"].append(ev)  # 조용한 시간·하루 몫에 걸리면 held 로 남는다 (같은 사건을 다시 재지 않게)
            if not quiet and self._room(st, cfg, now) and self._alert(ev, now):
                sent.append(ev)
        self._save(KV_BIG, st)
        if sent:
            log.info("xbig: 알림 %d건 (%s)", len(sent), ", ".join(f"{e['lead']} {e['score']}점" for e in sent))
        return sent

    @staticmethod
    def _known(st: dict[str, Any], c: dict[str, Any], now: float) -> bool:
        """같은 사건(72시간)이거나 같은 이름을 하루 안에 이미 다뤘나."""
        return any(e["key"] == c["key"] or (c["lead"] and e.get("lead") == c["lead"] and now - e["at"] <= LEAD_GAP_S) for e in st["events"])

    @staticmethod
    def _room(st: dict[str, Any], cfg: Any, now: float) -> bool:
        """오늘 몫·알림 간격이 남았나."""
        told = [e.get("told") for e in st["events"] if isinstance(e.get("told"), (int, float))]
        today = [t for t in told if xs._day(t) == xs._day(now)]
        return len(today) < cfg.per_day and all(now - t >= cfg.gap_minutes * 60 for t in told)

    def _release(self, st: dict[str, Any], cfg: Any, now: float) -> list[dict[str, Any]]:
        """미뤄 둔 알림 (조용한 시간·간격) 가운데 아직 싱싱한 것을 점수 순으로."""
        out = []
        for ev in sorted((e for e in st["events"] if e.get("state") == "held"), key=lambda e: -int(e.get("score") or 0)):
            if now - ev["at"] > HELD_MAX_S:
                ev["state"] = "old"
            elif self._room(st, cfg, now) and self._alert(ev, now):
                out.append(ev)
        return out

    def _alert(self, ev: dict[str, Any], now: float) -> bool:
        router = self.router()
        if router is None:
            return False
        head = " ".join(x for x in (ev["lead"], ev["shown"]) if x) or ev["title"][:30]
        router.open({"src": "dealbot", "key": f"xbig:{ev['id']}", "kind": "x_big", "blog": "", "iid": f"x-big-{ev['id']}", "sev": "hot",
                     "urgent": True, "title": f"🔥 X 큰 사건 — {head}"[:80], "short": f"X 큰 사건 {head}"[:40],
                     "body": f"{ev['title']} · 매체 {ev['outlets']}곳 — 눌러서 초안 보기"[:200], "ttl_h": NOTICE_TTL_H})
        ev.update(state="new", told=now)
        return True

    def _close(self, eid: str, state: str) -> None:
        """큰 사건을 닫는다 (올림·안 씀) — 알림 줄도."""
        st = self.big()
        for e in st["events"]:
            if e["id"] == eid and e.get("state") in ("new", "held"):
                e["state"] = state
        self._save(KV_BIG, st)
        router = self.router()
        if router is not None:
            router.resolve("dealbot", [f"xbig:{eid}"])

    # ---------------------------------------------------------- A. 오늘 X 글
    def _ai_room(self, st: dict[str, Any]) -> bool:
        cfg = self.cfg()
        return st["ai"] < cfg.auto_drafts_per_day and self.studio._left("draft", xs.DRAFT_PER_DAY) > cfg.reserve

    def _free_slots(self, st: dict[str, Any], now: float) -> list[str]:
        live = [i for i in st["items"] if i.get("slot") and i["id"] not in st["skip"]]  # '안 써요' 한 카드의 시각은 다시 빈다
        taken = {i["slot"] for i in live}
        cur = _mins(_hm(now)) - SLOT_GRACE_MIN
        room = self.cfg().per_day - len(live)
        return [t for t in xs.post_times(st["day"]) if _mins(t) >= cur and t not in taken][:max(0, room)]

    # ---------------------------------------------------------- 팔로워 목표 글
    def goal_rows(self, now: float) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self.db.kv_get(KV_GOAL) or "[]")
        except ValueError:
            raw = []
        return [r for r in raw if isinstance(r, dict) and isinstance(r.get("at"), (int, float)) and 0 <= now - r["at"] <= GOAL_KEEP_S] \
            if isinstance(raw, list) else []

    def goal_due(self, st: dict[str, Any], now: float) -> bool:
        """오늘 팔로워 목표 글을 쓸 차례인가: 하루 1편 · 7일에 GOAL_PER_WEEK 편 · 앞 글에서 GOAL_GAP_DAYS 날 띄움 · 오늘 카드에 없음."""
        if not self.cfg().goal_posts or any(i.get("kind") == "goal" for i in st["items"]):
            return False
        rows, today = self.goal_rows(now), datetime.fromisoformat(st["day"]).date()
        days = [datetime.fromisoformat(str(r.get("day") or xs._day(r["at"]))).date() for r in rows]
        week = [d for d in days if 0 <= (today - d).days < 7]
        return len(week) < GOAL_PER_WEEK and all((today - d).days >= GOAL_GAP_DAYS for d in days)

    def _followers(self, now: float) -> int | None:
        from dealbot.dashboard.actions import x_stats_rows

        rows = x_stats_rows(self.db)
        last = rows[-1] if rows else None
        return int(last["followers"]) if last and 0 <= now - last["at"] <= GOAL_STATS_S and last["followers"] > 0 else None

    async def _draft_goal(self, st: dict[str, Any], slot: str, now: float) -> dict[str, Any] | None:
        """팔로워 목표 카드 (AI 한 번 · 자동 몫에 셈). 규칙에 맞는 초안이 없으면 None (그날은 다시 안 씀)."""
        try:
            xs.assert_writer_model(self.studio.model)
        except ValueError as e:
            raise xs.StudioError(503, "ai_off", "서버 AI 설정이 글 쓰는 모델 규칙과 달라요 — 메인 세션에 알려 주세요") from e
        followers, recent = self._followers(now), self.goal_rows(now)
        st["ai"] += 1
        self._save(KV, st)
        key = f"goal:{st['day']}"
        try:
            res = await self.studio.draft_goal(followers, recent)
        except xs.StudioError as e:
            if e.code != "declined":
                raise
            res = None
        goal = res.get("goal") if res else xs.next_goal(followers)
        lines = [d["text"] for d in (res or {}).get("drafts") or []]
        row = {"at": now, "day": st["day"], "goal": goal, "line": xs.one_line(lines[0].split("\n", 1)[0], 60) if lines else "",
               "toks": sorted(set().union(*[xs.goal_toks(t) for t in lines]))[:60] if lines else []}
        self._save(KV_GOAL, (self.goal_rows(now) + [row])[-20:])  # 만든 때 센다 (초안이 없어도 그날 몫은 씀 — 다시 부르지 않게)
        if res is None:
            log.info("xtoday: 팔로워 목표 글은 오늘 쉼")
            return None
        res = {k: res[k] for k in ("drafts", "src_nums", "confirmed", "first_reply", "source", "model") if k in res}
        item = {"id": item_id(key), "key": key, "kind": "goal", "eid": "", "slot": slot,
                "keyword": f"팔로워 다음 목표 {goal:,}명" if followers is not None and goal else "팔로워 감사 글", "sources": [],
                "sig": {}, "res": res, "made_at": now}
        st["items"].append(item)
        st["skip"] = self.plan(now)["skip"]
        self._save(KV, st)
        log.info("xtoday: 카드 %s kind=goal slot=%s 초안 %d", item["id"], slot, len(res["drafts"]))
        return item

    def due(self, st: dict[str, Any], now: float) -> str:
        """지금 돌 차례인 일 이름 ('morning' · 'fill:11:20' · 'resume' · '')."""
        cfg, hm = self.cfg(), _hm(now)
        if not cfg.enabled or cfg.per_day < 1 or self.lock.locked():
            return ""
        if st["doing"] and not st["resumed"]:  # 만들던 중에 프로그램이 다시 켜짐 (자동 업데이트) → 남은 시각만 한 번 이어서
            return "resume" if self._free_slots(st, now) else ""
        if "morning" not in st["runs"]:
            return "morning" if hm >= cfg.morning and self._free_slots(st, now) else ""
        for f in cfg.fills:
            if f"fill:{f}" not in st["runs"] and 0 <= _mins(hm) - _mins(f) < FILL_WINDOW_MIN and self._free_slots(st, now):
                return f"fill:{f}"
        return ""

    def _event_src(self, ev: dict[str, Any]) -> dict[str, Any]:
        arts = [a for a in ev.get("arts") or [] if isinstance(a, dict)]
        text = "\n".join([f"막 뜨는 말: {ev.get('lead') or ''} {ev.get('shown') or ''}".rstrip(), *[f"기사 제목: {a.get('title') or ''}" for a in arts],
                          "기사 요약:", *[str(a["desc"]) for a in arts if a.get("desc")]])
        return {"kind": "hot", "text": xs.clean(text)[:xs.TEXT_MAX], "title": xs.one_line(ev.get("title"), 160),
                "url": web_link((arts[0] if arts else {}).get("link")), "fetched": False}

    def candidates(self, st: dict[str, Any], now: float) -> list[dict[str, Any]]:
        """오늘 글감 차례: 큰 사건(점수 순) → 화제(막 뜨는 것·점수 순). 이미 카드가 된 이야기·7일 안에 쓴 이야기는 뺀다."""
        seen = [*self.used(now), *[i["sig"] for i in st["items"] if isinstance(i.get("sig"), dict)]]
        have = {i.get("key") for i in st["items"]} | {u.get("k") for u in seen}
        out: list[dict[str, Any]] = []

        def add(c: dict[str, Any]) -> None:
            if c["key"] not in have and not any(same_event(c["sig"], s) for s in seen):
                out.append(c)
                seen.append(c["sig"])
                have.add(c["key"])

        events = [e for e in self.big(now)["events"] if e.get("state") == "new" and now - e["at"] <= EVENT_SHOW_S]
        for e in sorted(events, key=lambda e: -int(e.get("score") or 0)):
            titles = [str(a.get("title") or "") for a in e.get("arts") or [] if isinstance(a, dict)]
            add({"key": f"big:{e['id']}", "kind": "big", "eid": e["id"], "keyword": " ".join(x for x in (e.get("lead"), e.get("shown")) if x),
                 "sig": sig(str(e.get("lead") or ""), titles, str(e.get("lead") or "")),
                 "sources": [{"title": str(a.get("title") or ""), "link": web_link(a.get("link"))} for a in (e.get("arts") or [])[:3]
                             if isinstance(a, dict) and web_link(a.get("link"))]})
        for h in sorted(self.studio.hot_list(), key=lambda h: (h.get("breaking") is not True, -int(h.get("score") or 0), h.get("confirmed") is not True)):
            try:
                age = now - datetime.fromisoformat(str(h.get("created_at") or "")).timestamp()
            except (TypeError, ValueError):
                age = 0.0
            if age > HOT_MAX_AGE_S:
                continue
            srcs = [s for s in h.get("sources") or [] if isinstance(s, dict)]
            add({"key": f"hot:{h['id']}", "kind": "hot", "hot_id": h["id"], "keyword": str(h.get("keyword") or "")[:40],
                 "sig": sig(str(h.get("keyword") or ""), [str(s.get("title") or "") for s in srcs]),
                 "sources": [{"title": str(s.get("title") or "")[:160], "link": web_link(s.get("link"))} for s in srcs[:3] if web_link(s.get("link"))]})
        return out

    async def _draft(self, cand: dict[str, Any], st: dict[str, Any], slot: str, now: float, *, auto: bool = True) -> dict[str, Any] | None:
        """글감 하나 → 카드 (AI 한 번). 쓰지 않기로 한 글감(비극·모델 거절)은 None — 다시 고르지 않게 기억한다.
        auto=False 는 주인이 누른 것 — 서버 자동 몫(ai)에 세지 않는다 (xstudio 하루 몫에는 늘 셈).
        AI 가 안 되면 StudioError 를 그대로 올린다 (부르는 쪽이 그 차례를 끝냄)."""
        try:  # 글 쓰는 모델은 Opus 까지 — xstudio 와 같은 확인을 여기서도 한 번 (Fable·모르는 이름이면 AI 를 부르지 않는다)
            xs.assert_writer_model(self.studio.model)
        except ValueError as e:
            raise xs.StudioError(503, "ai_off", "서버 AI 설정이 글 쓰는 모델 규칙과 달라요 — 메인 세션에 알려 주세요") from e
        try:
            if cand["kind"] == "big":
                ev = next((e for e in self.big(now)["events"] if e["id"] == cand["eid"]), None)
                if ev is None:
                    return None
                src = self._event_src(ev)
            else:
                src = await self.studio._source({"kind": "hot", "hot_id": cand["hot_id"]})
            st["ai"] += int(auto)
            self._save(KV, st)  # 부르기 전에 센다 — 오류가 나도 하루 몫은 줄어듦
            res = await self.studio.draft_src(src, "auto", True)
        except xs.StudioError as e:
            if e.code not in ("declined", "gone"):
                raise
            self._use(cand["key"], cand["sig"], now)
            log.info("xtoday: 글감 %s 은 쓰지 않음 (%s)", xs.sha(cand["key"]), e.text[:60])
            return None
        res = {k: res[k] for k in ("drafts", "src_nums", "confirmed", "first_reply", "source", "model") if k in res}
        item = {"id": item_id(cand["key"]), "key": cand["key"], "kind": cand["kind"], "eid": cand.get("eid") or "", "slot": slot,
                "keyword": cand["keyword"], "sources": cand["sources"], "sig": cand["sig"], "res": res, "made_at": now}
        st["items"].append(item)
        st["skip"] = self.plan(now)["skip"]  # AI 를 기다리는 동안 주인이 누른 '안 써요'를 덮어쓰지 않게
        self._save(KV, st)
        self._use(cand["key"], cand["sig"], now)
        log.info("xtoday: 카드 %s kind=%s slot=%s 초안 %d (경고 %d)", item["id"], item["kind"], slot or "-", len(res["drafts"]),
                 sum(1 for d in res["drafts"] if d.get("warn")))
        return item

    async def build(self, name: str, now: float | None = None) -> int:
        """빈 시각만큼 카드를 만든다 (name = 'morning'·'fill:HH:MM'·'manual'). 만든 수."""
        now = self.clock() if now is None else now
        made = 0
        async with self.lock:
            st = self.plan(now)
            if name == "resume":
                st["resumed"] = True
            else:
                st["runs"][name] = now
            st["err"], st["doing"] = "", name
            self._save(KV, st)  # 먼저 적는다 — 실패해도 같은 차례를 다시 돌지 않음
            slots = self._free_slots(st, now)
            if slots and self.goal_due(st, now) and self._ai_room(st):  # 팔로워 목표 글 — 저녁 빈 시각 하나 (뉴스 글감보다 먼저 자리만 잡음)
                late = [t for t in slots if _mins(t) >= _mins(GOAL_FROM)]
                gslot = late[0] if late else slots[-1]
                try:
                    if await self._draft_goal(st, gslot, now) is not None:
                        slots.remove(gslot)
                        made += 1
                except xs.StudioError as e:
                    st["err"] = e.text
                    log.warning("xtoday %s: 팔로워 목표 글 멈춤 (%s)", name, e.code)
                    slots = []
            for cand in self.candidates(st, now):
                if not slots:
                    break
                if not self._ai_room(st):
                    st["err"] = "오늘 자동 초안 몫을 다 썼어요"
                    break
                try:
                    item = await self._draft(cand, st, slots[0], now)
                except xs.StudioError as e:  # AI 가 안 됨·모델 설정 문제·하루 몫 — 이 차례는 끝 (다음 보충 때 다시)
                    st["err"] = e.text
                    log.warning("xtoday %s: 멈춤 (%s)", name, e.code)
                    break
                if item is not None:
                    slots.pop(0)
                    made += 1
            st["doing"], st["skip"] = "", self.plan(now)["skip"]
            self._save(KV, st)
        log.info("xtoday %s: 카드 %d개 (오늘 자동 초안 %d번)", name, made, st["ai"])
        return made

    async def make_big(self, eid: str, *, owner: bool, now: float | None = None) -> dict[str, Any] | None:
        """큰 사건 하나의 초안 카드 (시각 없음 — 바로 올릴 글). owner=True 는 주인이 누른 것 (자동 몫과 상관없이 xstudio 하루 몫만)."""
        now = self.clock() if now is None else now
        async with self.lock:
            st = self.plan(now)
            key = f"big:{eid}"
            hit = next((i for i in st["items"] if i.get("key") == key), None)
            ev = next((e for e in self.big(now)["events"] if e["id"] == eid), None)
            if hit is not None or ev is None or (not owner and not self._ai_room(st)):
                return hit
            titles = [str(a.get("title") or "") for a in ev.get("arts") or [] if isinstance(a, dict)]
            cand = {"key": key, "kind": "big", "eid": eid, "keyword": " ".join(x for x in (ev.get("lead"), ev.get("shown")) if x),
                    "sig": sig(str(ev.get("lead") or ""), titles, str(ev.get("lead") or "")),
                    "sources": [{"title": str(a.get("title") or ""), "link": web_link(a.get("link"))} for a in (ev.get("arts") or [])[:3]
                                if isinstance(a, dict) and web_link(a.get("link"))]}
            return await self._draft(cand, st, "", now, auto=not owner)

    async def run(self, now: float | None = None) -> dict[str, Any]:
        """1분 일 한 번: 큰 사건 확인 → (새 사건이면 초안 미리) → 아침·보충 차례면 오늘 카드."""
        now = self.clock() if now is None else now
        cfg = self.cfg()
        out: dict[str, Any] = {"big": 0, "made": 0}
        if self._load(KV).get("day") not in (None, xs._day(now)) and not self.lock.locked():
            self._save(KV, self.plan(now))  # 날이 바뀜 — 어제 초안 글을 바로 지운다 (꺼져 있어도 · 아침 만들기를 기다리지 않고)
        if not cfg.enabled:
            return out
        sent = await self.poll(now)
        out["big"] = len(sent)
        if cfg.big.predraft:
            for ev in sent:
                try:
                    out["made"] += int(await self.make_big(ev["id"], owner=False, now=now) is not None)
                except xs.StudioError as e:
                    log.info("xtoday: 큰 사건 초안을 미리 못 씀 (%s) — 화면에서 누르면 다시", e.code)
                    break
        name = self.due(self.plan(now), now)
        if name:
            out["made"] += await self.build(name, now)
        return out

    def tick(self) -> bool:
        """scheduler 1분 일: run() 을 뒤에서 돌린다 (AI 가 몇 분 걸려도 다른 1분 일을 붙잡지 않게 · 도는 중이면 건너뜀)."""
        if self.bg is not None and not self.bg.done():
            return False

        async def safe() -> None:
            try:
                await self.run()
            except Exception as e:  # noqa: BLE001 — 다음 1분에 다시
                log.warning("xtoday tick: %s", type(e).__name__)

        self.bg = asyncio.ensure_future(safe())
        return True

    # ---------------------------------------------------------- 주인이 누른 것
    def mark_posted(self, tid: str, on: bool) -> None:
        """xstudio '올렸어요' 가 부른다 — 큰 사건 카드면 그 알림을 닫는다."""
        item = next((i for i in self.plan()["items"] if i["id"] == tid), None)
        if on and item is not None and item.get("eid"):
            self._close(item["eid"], "done")

    def add_mine(self, tid: str, topic: str, draft: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """'내 주제로 글 쓰기'(xtrend) 초안을 오늘 체크리스트에 한 줄로 (시각 없음 · 이미 있으면 글만 바꿈). AI 를 부르지 않는다."""
        now = self.clock() if now is None else now
        st = self.plan(now)
        item = next((i for i in st["items"] if i["id"] == tid), None)
        res = {"drafts": [{"text": str(draft.get("text") or ""), "format": "내 주제", "angle": str(draft.get("angle") or ""), "hints": [],
                           "warn": list(draft.get("warn") or []), "soft": []}], "src_nums": [], "confirmed": True, "first_reply": "",
               "source": {"title": topic, "url": "", "chars": 0, "fetched": False, "quote_url": ""}}
        if item is None:
            item = {"id": tid, "key": f"mine:{tid}", "kind": "mine", "eid": "", "slot": "", "keyword": topic, "sources": [], "made_at": now}
            st["items"].append(item)
        item["res"] = res
        self._save(KV, st)
        return item

    def skip(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """'안 써요' — 카드는 오늘 숨기고, 큰 사건이면 알림도 닫는다."""
        i = body.get("id")
        if not isinstance(i, str) or not ID_RE.match(i):
            raise xs.StudioError(400, "bad_args", "다시 눌러 주세요")
        st = self.plan()
        item = next((x for x in st["items"] if x["id"] == i), None)
        eid = item.get("eid") if item is not None else i if not i.startswith("t") else ""
        if item is not None and i not in st["skip"]:
            st["skip"].append(i)
            self._save(KV, st)
        if eid:
            self._close(eid, "skipped")
        return 200, {"status": "ok", **self.view()}

    def react_rows(self, now: float) -> list[dict[str, Any]]:
        """'오늘 반응 할 것' 줄 — 반응 목록(xengage 큐) 글 먼저, 그다음 자주 보는 계정 프로필. 링크만 (누르기는 주인 · X 요청 없음)."""
        st = self._load(KV_REACT)
        done = set(st.get("done") or []) if st.get("day") == xs._day(now) else set()
        rows: list[dict[str, Any]] = []
        try:
            queue = self.engage._st()["queue"] if self.engage is not None else []
        except Exception as e:  # noqa: BLE001 — 반응 목록을 못 읽어도 체크리스트는 그대로
            log.info("xtoday react queue: %s", type(e).__name__)
            queue = []
        for q in queue:
            rows.append({"key": f"p{q['sid']}", "label": f"@{q['h']} 글에 반응" if q["h"] else "X 글에 반응",
                         "url": f"{xs.PROFILE}{q['h'] or 'i/web'}/status/{q['sid']}"})
        for t in self.studio.targets():
            rows.append({"key": f"h{t['h'].lower()}", "label": f"@{t['h']} 최근 글에 반응" + (f" · {t['memo']}" if t.get("memo") else ""),
                         "url": f"{xs.PROFILE}{t['h']}"})
        return [{**r, "done": r["key"] in done} for r in rows[:REACT_MAX]]

    def react(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """'오늘 반응 할 것' 줄의 [✓ 했어요] — 그날 체크만 남긴다 (다시 누르면 풂)."""
        key, now = body.get("key"), self.clock()
        if not isinstance(key, str) or not REACT_KEY_RE.match(key) or all(r["key"] != key for r in self.react_rows(now)):
            raise xs.StudioError(400, "bad_args", "그 줄은 없어요 — 새로고침해 주세요")
        day, st = xs._day(now), self._load(KV_REACT)
        done = [k for k in (st.get("done") or []) if isinstance(k, str)] if st.get("day") == day else []
        done = [k for k in done if k != key] + ([key] if body.get("done") is not False else [])
        self._save(KV_REACT, {"day": day, "done": done[-50:]})
        return 200, {"status": "ok", **self.view()}

    async def _fill(self, now: float) -> None:
        try:
            await self.build("manual", now)
        except Exception as e:  # noqa: BLE001 — 화면의 '다시 보기'에 오류 줄이 보임
            log.warning("xtoday manual: %s", type(e).__name__)

    async def make(self, body: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        if body.get("op") == "fill":
            if now - self.fill_at < FILL_TAP_S:
                raise xs.StudioError(429, "rate", "방금 보충했어요 — 10분 뒤 다시")
            if not self._free_slots(self.plan(now), now):
                raise xs.StudioError(409, "full", "오늘 남은 시각의 카드가 다 있어요")
            self.fill_at = now
            self.fill_task = asyncio.ensure_future(self._fill(now))  # 글감마다 AI 한 번이라 몇 분 — 답은 바로 주고 뒤에서 만든다
            return {"status": "ok", "started": True, **self.view()}
        i = body.get("id")
        if not isinstance(i, str) or not re.fullmatch(r"[0-9a-f]{8}", i):
            raise xs.StudioError(400, "bad_args", "다시 눌러 주세요")
        item = await self.make_big(i, owner=True, now=now)
        if item is None:
            raise xs.StudioError(404, "gone", "그 사건은 초안을 쓰지 않았어요 — 지나갔거나 쓰지 않는 주제예요")
        return {"status": "ok", "made": item["id"], **self.view()}

    # ---------------------------------------------------------- 화면 값
    def view(self) -> dict[str, Any]:
        now, cfg = self.clock(), self.cfg()
        st, bg = self.plan(now), self.big(now)
        done: dict[str, float] = {}  # 카드 번호 → 마지막으로 '올렸어요' 누른 때 (체크 상자 · 새로고침해도 서버 기록 그대로)
        for r in self.studio.posted_rows():
            if isinstance(r.get("topic"), str) and xs._day(r["posted_at"]) == st["day"]:
                done[r["topic"]] = max(done.get(r["topic"], 0.0), float(r["posted_at"]))
        items = []
        for i in sorted(st["items"], key=lambda i: (i.get("slot") or "00:00", i.get("made_at") or 0)):
            if i["id"] in st["skip"]:
                continue
            drafts = [d for d in i["res"].get("drafts") or [] if isinstance(d, dict)]
            first = drafts[0] if drafts else {}
            items.append({"id": i["id"], "slot": i.get("slot") or "", "kind": i.get("kind") or "hot", "eid": i.get("eid") or "",
                          "keyword": str(i.get("keyword") or ""), "sources": i.get("sources") or [],
                          "nums_ok": bool(drafts) and not first.get("warn"), "warn": list(first.get("warn") or [])[:3],
                          "confirmed": i["res"].get("confirmed") is not False, "preview": str(first.get("text") or ""),
                          "angle": str(first.get("angle") or ""), "first_reply": str(i["res"].get("first_reply") or ""),
                          "done": i["id"] in done, "undo_s": max(0, int(xs.UNDO_WITHIN - (now - done[i["id"]]))) if i["id"] in done else 0,
                          "res": i["res"]})
        made = {i.get("eid"): i["id"] for i in st["items"] if i.get("eid")}
        events = [{"id": e["id"], "title": str(e.get("title") or ""), "lead": str(e.get("lead") or ""), "shown": str(e.get("shown") or ""),
                   "outlets": int(e.get("outlets") or 0), "score": int(e.get("score") or 0), "at": _hm(e.get("told") or e["at"]),
                   "item": made.get(e["id"], ""),
                   "sources": [{"title": str(a.get("title") or ""), "link": web_link(a.get("link"))} for a in (e.get("arts") or [])[:3]
                               if isinstance(a, dict) and web_link(a.get("link"))]}
                  for e in reversed(bg["events"]) if e.get("state") == "new" and now - e["at"] <= EVENT_SHOW_S]
        hm = _hm(now)
        nxt = cfg.morning if "morning" not in st["runs"] and hm < cfg.morning else next((f for f in cfg.fills if f > hm and f"fill:{f}" not in st["runs"]), "")
        note = "" if now >= bg["dead_until"] else "네이버 검색 키가 막혀서 큰 사건 확인을 쉬고 있어요 — 메인 세션에 알려 주세요"
        return {"v": 1, "on": bool(cfg.enabled), "day": st["day"], "slots": list(xs.post_times(st["day"])), "items": items, "big": events,
                "free": self._free_slots(st, now), "next": nxt, "err": st["err"], "note": note, "ran": bool(st["runs"]),
                "ai": {"used": st["ai"], "cap": cfg.auto_drafts_per_day, "left": self.studio._left("draft", xs.DRAFT_PER_DAY)},
                "bigcfg": {"on": bool(cfg.big.enabled), "min_score": cfg.big.min_score, "per_day": cfg.big.per_day},
                "log": [{"at": _hm(x.get("at") or 0), "lead": str(x.get("lead") or ""), "shown": str(x.get("shown") or ""),
                         "score": int(x.get("score") or 0), "why": str(x.get("why") or "")} for x in bg["log"][-8:]],
                "model": xs.model_label(self.studio.model), "react": self.react_rows(now)}


def register(web: Any, api: Any, studio: xs.Studio) -> XToday:
    """DashApi.register 가 부른다 (xstudio 다음). api = DashApi (guard · caller · audit · bot)."""
    from dealbot.dashboard.actions import RateWindow

    bot = api.bot
    desk = XToday(api.dash.db, studio, cfg=lambda: bot.settings.dashboard.x_today, router=lambda: getattr(bot, "router", None),
                  news=NaverNews(bot))
    view_rate, tap_rate, make_rate = RateWindow(60, 60.0), RateWindow(30, 60.0), RateWindow(xs.DRAFT_PER_MIN, 60.0)
    slow = xs._j(429, {"status": "error", "code": "rate", "text": "너무 자주 눌렀어요 — 1분 뒤 다시"})

    def audit(caller: Any, action: str, code: str) -> None:
        api.audit(session8=caller.session, client8=caller.client8, action=f"xtoday.{action}", target="", args={}, code=code, action_id=None)

    async def state(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        return xs._j(200, desk.view()) if view_rate.hit(caller.session) else slow

    async def make(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        if not make_rate.hit(caller.session):
            return slow
        try:
            out = await desk.make(body)
        except xs.StudioError as e:
            audit(caller, "make", e.code)
            return xs._j(e.status, e.body())
        audit(caller, "make", "done")
        return xs._j(200, out)

    def tapper(name: str, run: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]) -> Any:
        async def handler(body: dict[str, Any], headers: dict[str, str]) -> Any:
            g = api.guard(headers)
            if len(g) == 4:
                return g
            caller, _ident = g
            if not tap_rate.hit(caller.session):
                return slow
            try:
                status, out = run(body)
            except xs.StudioError as e:
                status, out = e.status, e.body()
            audit(caller, name, str(out.get("code") or "done"))
            return xs._j(status, out)

        return handler

    web.route(STATE_PATH, state, with_headers=True)
    web.route_json(MAKE_PATH, make, max_body=BODY_MAX)
    web.route_json(SKIP_PATH, tapper("skip", desk.skip), max_body=BODY_MAX)
    web.route_json(REACT_PATH, tapper("react", desk.react), max_body=1024)
    return desk
