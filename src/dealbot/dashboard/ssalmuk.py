"""이벤트 참여 (쌀먹 — 돈좀요 이벤트 자동 응모) 를 Sns-board 에 (주인 11/14 "쌀먹을 Sns-board 에 합치자, 지금 쌀먹 대시보드는 예비로 놔두자").
화면에 보이는 이름은 '이벤트 참여' (주인 11/14) — 코드·주소·헤더 이름은 쌀먹 쪽 명세(ssalmuk docs/SNSBOARD.md) 그대로.

쌀먹 서버(perpfarm, 테일스케일 안)는 밖으로만 부른다 — Sns-board 가 쌀먹을 부르는 일은 없다:
  POST /dashboard/api/ssalmuk/push      상태 전체 (덮어쓰기 + 받은 시각) · v==1, app=="ssalmuk" 아니면 400
  GET  /dashboard/api/ssalmuk/commands  아직 ack 안 된 명령 (오래된 순, 50개까지)
  POST /dashboard/api/ssalmuk/ack       {"ids": [...], "results": {id: "ok" | 오류 글}} → 그 명령을 지움
  GET  /dashboard/api/ssalmuk/search    ?type=blog|news|webkr&q=&display=1-20&sort=sim|date → 네이버 검색 Open API 를 딜봇 키로 대신 (딜봇 키가 401/403 이면 Blog-Auto .env 키로 이어서 · state 의 search_proxy.keysrc) (주인 11/14 —
                                        쌀먹 서버에 키를 옮기지 않음). {"items":[{title,link,description,postdate|pubDate,bloggername}],"total"}
                                        11/15 네이버 키가 둘 다 401/403/없음이면 카카오(다음) 검색 API 로 이어서 (KAKAO_REST_API_KEY — 딜봇 환경, 없으면 Blog-Auto .env 한 줄) —
                                        같은 모양으로 바꿔 주고 "source":"kakao" 만 더함 (blog→blog · webkr→web · news→web 최신순, 카카오엔 뉴스 검색이 없음).
                                        네이버가 둘 다 막혀 카카오로 된 뒤엔 카카오 먼저 (네이버 키는 하루 한 번만 다시 봄 — KEY_RECHECK).
                                        키 1개당 1분 60번 · 하루 300번 (KST, kv ssalmuk_search_day — 키를 여럿 써 봐도 한 번) · 넘으면 429 {"code":"limit"} ·
                                        11/15 주인 네이버 키는 API HUB 키 → 키마다 HUB(naverapihub.apigw.ntruss.com) 먼저, 401/403 (webkr 은 404 — HUB 에 없음) 이면 예전 주소로 ·
                                        HUB 로 받으면 "source":"naver_hub" 더함 (keysrc 'dealbot_hub'·'blog_hub') · webkr HUB 404 를 보면 하루 동안 webkr 은 카카오 먼저 (kv ssalmuk_webkr_hub404).
                                        네이버·카카오가 401/429/5xx 등을 주면 502 {"code":"naver_error","status"} (카카오면 "source":"kakao" 더함) · 키가 하나도 없으면 503 {"code":"no_key"}
  POST /dashboard/api/ssalmuk/llm       LLM 대리 호출 (11/15 — ssalmuk_llm.py: Claude 는 서버 claude CLI 로 haiku·sonnet 만 · Gemini 는 서버 GEMINI_API_KEY 로 generateContent 만 ·
                                        하루 Gemini 40·Claude 150(Sonnet 50)·1분 10회·몸 32KB · 전화·이메일 꼴이 있으면 400 pii · 글은 로그·kv 에 안 남김)
  이 다섯은 로그인·CSRF 없이 X-Ssalmuk-Key 로만 (해시끼리 hmac.compare_digest — 상수 시간) · 틀리면 401 {"code":"bad_key"} ·
  몸 512KB 까지 · 같은 손님 1분 120번.
화면(로그인 + 같은 사이트 + X-Dash-Token — DashApi.guard):
  GET  /dashboard/api/ssalmuk/state     {"state", "receivedAt", "pending", "stale", "results"} (no-store — 주인 이름·전화가 들어 있음)
                                        있을 때만: "health" (마지막 실행 점검 줄 — clean_health) · "quiz_pending" (보내고 아직 안 가져간 퀴즈 답 [{id, at, question, answer, key}] 새것 30개)
  POST /dashboard/api/ssalmuk/act       {"op": "run"|"done"|"profile"|"search_retry"|"trust_host"|"quiz_answer", "args": {...}} → 명령 쌓기
                                        (done 은 받아 둔 상태에도 먼저 · quiz_answer {question, answer, key} — 퀴즈 카드 보기 단추, 쌀먹 6663b6c)
  GET  /dashboard/api/ssalmuk/key       {"key", "url", "at"} — 연결 칸을 펼칠 때만 (no-store)
  POST /dashboard/api/ssalmuk/rotate    새 키 (예전 키는 바로 안 받음 — 쌀먹 예비 대시보드에 다시 붙여넣기)
저장은 딜봇 DB kv (ssalmuk_key · ssalmuk_state · ssalmuk_cmds · ssalmuk_results). 받은 상태·키는 로그에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dealbot.dashboard import naverhub

log = logging.getLogger(__name__)

BASE = "/dashboard/api/ssalmuk"
PUSH_PATH = f"{BASE}/push"
COMMANDS_PATH = f"{BASE}/commands"
ACK_PATH = f"{BASE}/ack"
STATE_PATH = f"{BASE}/state"
ACT_PATH = f"{BASE}/act"
KEY_PATH = f"{BASE}/key"
ROTATE_PATH = f"{BASE}/rotate"
# 로그인 없이 여는 것 — 이 다섯뿐 (정확한 경로)
SEARCH_PATH = f"{BASE}/search"
LLM_PATH = f"{BASE}/llm"  # LLM 대리 호출 (ssalmuk_llm.py)
PUBLIC_PATHS = (PUSH_PATH, COMMANDS_PATH, ACK_PATH, SEARCH_PATH, LLM_PATH)

BODY_MAX = 512 * 1024
ACT_BODY_MAX = 64 * 1024
KEY_HEADER = "x-ssalmuk-key"
KV_KEY = "ssalmuk_key"
KV_STATE = "ssalmuk_state"
KV_CMDS = "ssalmuk_cmds"
KV_RESULTS = "ssalmuk_results"
STALE_AFTER = 20 * 60  # 20분 넘게 push 가 없으면 '연결 끊김' (쌀먹은 적어도 10분마다 보냄)
CMD_KEEP = 86400  # 명령 보관은 하루
CMD_MAX = 200
CMD_LIST = 50
RESULTS_KEEP = 30
OPS = ("run", "done", "profile", "search_retry", "trust_host", "quiz_answer")  # 11/14 쌀먹 5469c7b: 검색 다시 시도 · 이 사이트 허용 · 6663b6c: 퀴즈 답
# 퀴즈 답 (쌀먹 docs/SNSBOARD.md 'quiz_answer'): 질문은 카드 글 그대로 (자르면 쌀먹 질문 은행 키가 달라짐 — 카드는 200자까지) · 길면 자르지 않고 거절
QUIZ_Q_MAX = 1000
QUIZ_A_MAX = 300  # 쌀먹이 100자·'카드 보기 중 하나'를 다시 봄 — 여기선 모양만
QUIZ_KEY_MAX = 1000
QUIZ_PENDING_MAX = 30  # 화면 응답에 싣는 '보내고 아직 안 가져간 퀴즈 답' (새것부터 — 쌓여도 응답이 커지지 않게)
HOST_RE = re.compile(r"^(?=.{4,253}$)(?!-)[a-z0-9-]{1,63}(?:\.[a-z0-9-]{1,63})*\.[a-z]{2,24}$")
# 쌀먹 sync.js PROFILE_KEYS 와 같게 (점 키 그대로)
PROFILE_KEYS = ("name", "phone", "email", "birth", "gender", "zipcode", "address", "addressDetail", "shareUrl", "surveyMode",
                "watchVideoSeconds", "agreeOptional", "sns.nickname", "sns.instagram", "sns.instagramUrl", "sns.youtube", "sns.blog",
                "demographics.age", "demographics.region", "demographics.job", "commentTemplates", "quizAnswers", "customAnswers")
SEARCH_TYPES = ("blog", "news", "webkr")
SEARCH_URL = "https://openapi.naver.com/v1/search/{type}.json"  # 예전 개발자센터 주소 (HUB 는 naverhub)
SEARCH_TIMEOUT = 8.0
SEARCH_PER_MIN = 60
SEARCH_PER_DAY = 300
SEARCH_Q_MAX = 100
KV_SEARCH_DAY = "ssalmuk_search_day"
KV_SEARCH_KEYS = "ssalmuk_search_keys"  # 키마다 마지막 네이버 답 {"dealbot": {"s": "401 키틀림", "at": …}, "blog": {"s": 200, "at": …}, "at": …} (값만 — 키는 안 남김, 주인 11/14 '401 원인' · note_keys)
KV_SEARCH_KEYSRC = "ssalmuk_search_keysrc"  # 'dealbot' | 'blog' | 'kakao' (+ '_hub' = 네이버 API HUB 로 통함, 11/15) — 어느 키가 통했는지 (값만, 비밀 아님)
KEYSRCS = ("dealbot", "blog", "kakao")
KV_LLM_DAY = "ssalmuk_llm_day"  # LLM 대리 호출 오늘(KST) 횟수만 {"d", "gemini", "claude", "sonnet"} — 글·키는 안 남김
LLM_CAPS = {"gemini": 40, "claude": 150, "sonnet": 50}  # 기본값 (설정 dashboard.ssalmuk_llm_* 가 덮음 — ssalmuk_llm.register)
KEYSRC_VALUES = ("dealbot", "dealbot_hub", "blog", "blog_hub", "kakao")
KV_WEBKR_HUB404 = "ssalmuk_webkr_hub404"  # 웹문서 검색이 HUB 에 없음(404)을 본 뒤 이 시각까지 webkr 은 곧장 카카오 (네이버는 예전 주소만)
WEBKR_HUB404_S = 86400.0
KEY_RECHECK = 86400.0  # 블로그 키(또는 카카오)로 넘어간 뒤에도 앞 네이버 키를 하루 한 번 다시 봄
# 카카오(다음) 검색 (11/15 — 네이버 검색 앱이 지워져 두 키 다 401 · 7/31 부터 새 키 신청 막힘). 공식 문서 developers.kakao.com/docs/latest/ko/daum-search/dev-guide
KAKAO_URL = "https://dapi.kakao.com/v2/search/{type}"
KAKAO_TYPE = {"blog": "blog", "webkr": "web", "news": "web"}  # 카카오엔 뉴스 검색이 없음 → 웹문서 최신순
KAKAO_SORT = {"sim": "accuracy", "date": "recency"}
KAKAO_SIZE_MAX = 50  # web·blog size 1~50
_TAG_RE = re.compile(r"<[^>]*>")
OP_LABEL = {"run": "지금 확인하기", "done": "완료 표시", "profile": "내 정보 저장", "search_retry": "검색 다시 켜기", "trust_host": "사이트 허용",
            "quiz_answer": "퀴즈 답 저장"}


KST = timezone(timedelta(hours=9))


def _plain(v: Any) -> str:
    """네이버 검색 글의 <b> 같은 태그를 지우고 &quot; 같은 글자 표현을 되돌림."""
    return html.unescape(_TAG_RE.sub("", str(v or ""))).strip()


ALERT_LEVELS = ("bad", "warn")
ALERT_MAX = 10
ALERT_TEXT = 200
_WS_RE = re.compile(r"\s+")


def _line(v: Any, n: int) -> str:
    """한 줄 글 (태그·글자 표현 풀고 공백 하나로, n 자까지). 글이 아니면 ''."""
    if not isinstance(v, str):
        return ""
    return _WS_RE.sub(" ", _plain(v))[:n].strip()


def clean_alerts(state: Any) -> list[dict[str, Any]]:
    """받아 둔 push 의 alerts[] → 화면용 (주인 11/14 — 쌀먹 오류 검수 칸, docs/SNSBOARD.md '오류 검수 칸').
    dict 항목만 · level 은 bad/warn 만 · msg·todo 는 글로만 200 자까지 · bad 먼저 · 10 개까지. 칸이 없거나 이상하면 []."""
    raw = state.get("alerts") if isinstance(state, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for x in raw[:100]:
        if not isinstance(x, dict) or x.get("level") not in ALERT_LEVELS:
            continue
        msg = _line(x.get("msg"), ALERT_TEXT)
        if not msg:
            continue
        out.append({"id": _line(x.get("id"), 64), "level": x["level"], "msg": msg, "since": _line(x.get("since"), 40),
                    "todo": _line(x.get("todo"), ALERT_TEXT), "new": x.get("new") is True})
    out.sort(key=lambda a: ALERT_LEVELS.index(a["level"]))  # bad 먼저 (같은 단계는 받은 순서 그대로)
    return out[:ALERT_MAX]


HEALTH_LEVELS = ("ok", "warn", "bad")
HEALTH_STATUS = ("done", "aborted", "failed", "crashed", "refused", "no-profile")
LLM_CHECK_IDS = ("llm", "llm-accuracy")  # 쌀먹 health.js judge 의 Gemini 줄 (push 의 health 에는 llm 칸이 없고 checks[] 로만 옴)


def clean_health(state: Any) -> dict[str, Any] | None:
    """받아 둔 push 의 health → 화면 한 줄용 (쌀먹 docs/SNSBOARD.md '오류 검수 칸' — 계약에 있는 칸만: level·status·label·at·checks[id, level, msg, todo]).
    - stopped: status 가 'aborted' 이고 checks 의 run 줄이 'warn' = 서버 종료 신호로 멈춘 것 (쌀먹 35aafbb) → 화면은 빨강이 아니라 노랑 '중단(서버 종료 신호)'.
      run 줄이 bad 인 aborted(50분 넘어 강제 종료)는 그대로 빨강.
    - llm: checks 중 id 가 llm·llm-accuracy 인 줄 (제미나이 답 상태) — 없으면 [] (화면에서 숨김).
    칸이 없거나 모르는 값뿐이면 None (화면은 그대로)."""
    h = state.get("health") if isinstance(state, dict) else None
    if not isinstance(h, dict):
        return None
    level = h.get("level") if h.get("level") in HEALTH_LEVELS else None
    status = h.get("status") if h.get("status") in HEALTH_STATUS else None
    if level is None and status is None:
        return None
    raw = h.get("checks")
    checks = [c for c in (raw[:50] if isinstance(raw, list) else []) if isinstance(c, dict) and c.get("level") in HEALTH_LEVELS]
    run = next((c for c in checks if c.get("id") == "run"), None)
    llm = [{"id": c["id"], "level": c["level"], "msg": _line(c.get("msg"), ALERT_TEXT), "todo": _line(c.get("todo"), ALERT_TEXT)}
           for c in checks if c.get("id") in LLM_CHECK_IDS and _line(c.get("msg"), ALERT_TEXT)]
    llm.sort(key=lambda c: -HEALTH_LEVELS.index(c["level"]))  # 급한 것 먼저
    return {"level": level, "status": status, "label": _line(h.get("label"), 40), "at": _line(h.get("at"), 40),
            "stopped": status == "aborted" and run is not None and run["level"] == "warn", "llm": llm[:2]}


def _kakao_dt(v: Any) -> datetime | None:
    """카카오 datetime ('2026-11-14T10:24:00.000+09:00') → datetime (모르면 None)."""
    try:
        d = datetime.fromisoformat(str(v or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=KST)


def kakao_items(data: Any, typ: str) -> tuple[list[dict[str, str]], int]:
    """카카오 documents → 네이버 items 꼴 (쌀먹 계약 그대로 — search_items 와 같은 칸).
    title·contents 의 <b> 와 글자 표현은 네이버와 같이 풀고 · blog 는 postdate(YYYYMMDD)·bloggername(blogname) ·
    news(=웹문서 최신순) 는 pubDate (네이버와 같은 RFC 822 꼴, KST) · webkr 은 네이버처럼 날짜 칸 없음."""
    raw = data.get("documents") if isinstance(data, dict) else None
    items: list[dict[str, str]] = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        it = {"title": _plain(x.get("title")), "link": str(x.get("url") or ""), "description": _plain(x.get("contents"))}
        d = _kakao_dt(x.get("datetime"))
        if typ == "blog":
            if d is not None:
                it["postdate"] = d.astimezone(KST).strftime("%Y%m%d")
            if x.get("blogname"):
                it["bloggername"] = _plain(x["blogname"])
        elif typ == "news" and d is not None:
            k = d.astimezone(KST)  # 요일·달 이름은 영어 (네이버 pubDate 와 같게 — 지역 설정과 상관없이)
            it["pubDate"] = (f"{('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')[k.weekday()]}, {k.day:02d} "
                             f"{('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')[k.month - 1]} "
                             f"{k.year} {k:%H:%M:%S} +0900")
        items.append(it)
    meta = data.get("meta") if isinstance(data, dict) else None
    total = meta.get("total_count") if isinstance(meta, dict) else None
    return items, total if isinstance(total, int) and not isinstance(total, bool) else len(items)


def search_items(data: Any) -> tuple[list[dict[str, str]], int]:
    raw = data.get("items") if isinstance(data, dict) else None
    items: list[dict[str, str]] = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        it = {"title": _plain(x.get("title")), "link": str(x.get("link") or ""), "description": _plain(x.get("description"))}
        for k in ("postdate", "pubDate", "bloggername"):
            if x.get(k):
                it[k] = _plain(x[k])
        items.append(it)
    total = data.get("total") if isinstance(data, dict) else None
    return items, total if isinstance(total, int) else len(items)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(v: str) -> bytes:
    return hashlib.sha256(v.encode("utf-8")).digest()


def key_ok(given: str | None, stored: str | None) -> bool:
    """X-Ssalmuk-Key 확인 — 길이가 드러나지 않게 해시끼리 상수 시간 비교. 키가 없거나 비었으면 늘 거절."""
    ok = hmac.compare_digest(_digest(given or ""), _digest(stored or ""))
    return bool(given) and bool(stored) and ok


def _clean_profile(p: Any) -> dict[str, Any] | None:
    if not isinstance(p, dict):
        return None
    out: dict[str, Any] = {}
    for k in PROFILE_KEYS:
        if k not in p:
            continue
        v = p[k]
        if k == "commentTemplates":
            lines = v if isinstance(v, list) else str(v or "").splitlines()
            out[k] = "\n".join(str(s).strip()[:300] for s in lines[:20] if str(s).strip())
        elif k in ("quizAnswers", "customAnswers"):
            out[k] = {str(a)[:100]: str(b)[:300] for a, b in list(v.items())[:100]} if isinstance(v, dict) else {}
        elif k == "agreeOptional":
            out[k] = bool(v)
        elif k == "watchVideoSeconds":
            try:
                out[k] = max(0, min(120, int(float(v or 0))))
            except (TypeError, ValueError):
                out[k] = 0
        else:
            out[k] = str(v if v is not None else "").strip()[:300]
    return out


def _why_401(resp: Any) -> str:
    """네이버 401 까닭 낱말만 (값·본문은 안 남김): 검색 API 가 앱에 없으면 'Scope Status Invalid' → 검색미등록, 그 밖은 키틀림."""
    try:
        msg = str((resp.json() or {}).get("errorMessage") or "")
    except ValueError:
        msg = ""
    return "검색미등록" if "scope" in msg.lower() else "키틀림"


BLOG_ENV_NAMES = ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "KAKAO_REST_API_KEY", "GEMINI_API_KEY")  # 이 네 줄만 읽음 (11/15 카카오 · Gemini 한 줄씩 더함)


class BlogKeyFile:
    """Blog-Auto .env 에서 NAVER_CLIENT_ID/SECRET · KAKAO_REST_API_KEY · GEMINI_API_KEY 네 줄만 읽음 (다른 줄은 보지도 않음 · 값은 로그·응답에 안 남김).
    처음 쓸 때 읽고 메모리에 두며 파일 수정 시각이 바뀌면 다시 읽음."""

    def __init__(self, path: Callable[[], str]) -> None:
        self._path = path
        self._sig: tuple[str, int] | None = None
        self._vals: dict[str, str] = {}

    def _load(self) -> dict[str, str]:
        try:
            p = Path(self._path()).expanduser()
            sig = (str(p), os.stat(p).st_mtime_ns)
        except (OSError, ValueError):
            self._sig, self._vals = None, {}
            return self._vals
        if sig != self._sig:
            self._sig, self._vals = sig, self._parse(p) or {}
        return self._vals

    def get(self) -> tuple[str, str] | None:
        """네이버 키 한 쌍 (둘 다 있을 때만)."""
        v = self._load()
        i, s = v.get("NAVER_CLIENT_ID"), v.get("NAVER_CLIENT_SECRET")
        return (i, s) if i and s else None

    def kakao(self) -> str | None:
        return self._load().get("KAKAO_REST_API_KEY") or None

    def gemini(self) -> str | None:
        return self._load().get("GEMINI_API_KEY") or None

    @staticmethod
    def _parse(p: Path) -> dict[str, str] | None:
        vals: dict[str, str] = {}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, eq, v = line.partition("=")
            name = name.strip()
            if eq and name in BLOG_ENV_NAMES:
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if v:
                    vals[name] = v
        return vals


class Ssalmuk:
    """kv 에 둔 상태·명령·키. clock 은 시험용."""

    def __init__(self, db: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock
        self.llm_caps = dict(LLM_CAPS)

    # ------------------------------------------------------------ 키
    def key(self) -> str:
        k = self.db.kv_get(KV_KEY)
        if not k:
            k = self.rotate()
        return json.loads(k)["key"] if k.startswith("{") else k

    def key_at(self) -> str | None:
        raw = self.db.kv_get(KV_KEY) or ""
        return json.loads(raw).get("at") if raw.startswith("{") else None

    def rotate(self) -> str:
        """새 연결 키 (32바이트 무작위 → 64글자). 예전 키는 바로 안 받는다."""
        raw = json.dumps({"key": secrets.token_hex(32), "at": _iso(self.clock())})
        self.db.kv_set(KV_KEY, raw)
        return raw

    def machine_ok(self, headers: dict[str, str]) -> bool:
        raw = self.db.kv_get(KV_KEY) or ""  # 키가 아직 없으면 만들지 않고 거절 (화면에서 연결 칸을 열 때 생김)
        stored = json.loads(raw).get("key") if raw.startswith("{") else raw
        return key_ok((headers.get(KEY_HEADER) or "").strip(), stored)

    # ------------------------------------------------------------ 상태
    def _load(self, k: str, default: Any) -> Any:
        try:
            v = json.loads(self.db.kv_get(k) or "null")
        except ValueError:
            return default
        return default if v is None else v

    def push(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if body.get("v") != 1 or body.get("app") != "ssalmuk":
            return 400, {"ok": False, "code": "bad_body", "text": "v=1 · app=ssalmuk 상태만 받아요"}
        now = self.clock()
        self.db.kv_set(KV_STATE, json.dumps({"state": body, "receivedAt": _iso(now), "ts": now}, ensure_ascii=False))
        return 200, {"ok": True}

    def _cmds(self) -> list[dict[str, Any]]:
        now = self.clock()
        cmds = self._load(KV_CMDS, [])
        return [c for c in cmds if isinstance(c, dict) and now - float(c.get("ts") or 0) < CMD_KEEP] if isinstance(cmds, list) else []

    def commands(self) -> dict[str, Any]:
        return {"commands": [{k: c[k] for k in ("id", "op", "args", "at")} for c in self._cmds()[:CMD_LIST]]}

    def ack(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        ids = body.get("ids")
        results = body.get("results") or {}
        if not isinstance(ids, list) or not isinstance(results, dict):
            return 400, {"ok": False, "code": "bad_body", "text": "ids 목록이 필요해요"}
        gone = {str(i) for i in ids[:500]}
        cmds = self._cmds()
        done = [c for c in cmds if c["id"] in gone]
        self.db.kv_set(KV_CMDS, json.dumps([c for c in cmds if c["id"] not in gone], ensure_ascii=False))
        if done:
            now = self.clock()
            rs = self._load(KV_RESULTS, [])
            rs = rs if isinstance(rs, list) else []
            for c in done:
                r = results.get(c["id"], "ok")
                row = {"id": c["id"], "op": c["op"], "result": str(r)[:200] if r is not None else "ok", "at": _iso(now)}
                if c["op"] == "quiz_answer":  # 어느 퀴즈 카드의 결과인지 (카드에 '반영됨'·안 된 이유) — 질문·보기는 공개 이벤트 글
                    row["ref"] = {k: c["args"].get(k) for k in ("question", "answer", "key")}
                rs.append(row)
            self.db.kv_set(KV_RESULTS, json.dumps(rs[-RESULTS_KEEP:], ensure_ascii=False))
        return 200, {"ok": True, "removed": len(done)}

    def search_day_hit(self, limit: int = SEARCH_PER_DAY) -> bool:
        """하루(KST) 300번 — 안 넘었으면 하나 세고 True."""
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        saved = self._load(KV_SEARCH_DAY, {})
        n = int(saved.get("n") or 0) if isinstance(saved, dict) and saved.get("d") == day else 0
        if n >= limit:
            return False
        self.db.kv_set(KV_SEARCH_DAY, json.dumps({"d": day, "n": n + 1}))
        return True

    def note_keys(self, seen: dict[str, Any]) -> None:
        """키마다 마지막 네이버 답을 {s: 답, at: 그때} 로 — 이번에 안 써 본 키('-')는 지난 값을 그대로 둔다 (주인 11/14: 블로그 키로 한 번 되면
        딜봇 키의 '401' 줄이 '-' 로 덮여 원인이 사라졌음). 예전 모양(값만)도 읽는다."""
        now = int(self.clock())
        old = self._load(KV_SEARCH_KEYS, {})
        old = old if isinstance(old, dict) else {}
        out: dict[str, Any] = {"at": now}
        for k in KEYSRCS:
            v, prev = seen.get(k, "-"), old.get(k)
            if v not in ("-", None):
                out[k] = {"s": v, "at": now}
            elif isinstance(prev, dict) and "s" in prev:
                out[k] = prev
            elif prev not in (None, "-"):  # 예전 모양 (값만)
                out[k] = {"s": prev, "at": old.get("at")}
            else:
                out[k] = {"s": "-", "at": None}
        self.db.kv_set(KV_SEARCH_KEYS, json.dumps(out, ensure_ascii=False))

    def search_keysrc(self) -> str | None:
        v = self.db.kv_get(KV_SEARCH_KEYSRC)
        return v if v in KEYSRC_VALUES else None

    def webkr_hub_missing(self) -> bool:
        """웹문서 검색이 HUB 에서 404 였던 걸 하루 동안 기억 (쓸데없는 HUB·예전 주소 호출 줄이기)."""
        try:
            return float(self.db.kv_get(KV_WEBKR_HUB404) or 0) > self.clock()
        except (TypeError, ValueError):
            return False

    def set_webkr_hub_missing(self) -> None:
        self.db.kv_set(KV_WEBKR_HUB404, str(int(self.clock() + WEBKR_HUB404_S)))

    def set_search_keysrc(self, src: str) -> None:
        if self.search_keysrc() != src:
            self.db.kv_set(KV_SEARCH_KEYSRC, src)

    def search_today(self) -> int:
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        saved = self._load(KV_SEARCH_DAY, {})
        return int(saved.get("n") or 0) if isinstance(saved, dict) and saved.get("d") == day else 0

    def _llm_day(self) -> tuple[str, dict[str, int]]:
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        saved = self._load(KV_LLM_DAY, {})
        same = isinstance(saved, dict) and saved.get("d") == day
        out: dict[str, int] = {}
        for k in ("gemini", "claude", "sonnet"):
            v = saved.get(k) if same else 0
            out[k] = v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0
        return day, out

    def llm_day_hit(self, provider: str, *, sonnet: bool = False) -> bool:
        """LLM 대리 호출 하루(KST) 한도 — 안 넘었으면 하나 세고 True. Sonnet 은 Claude 몫 안에서 따로 한 번 더 봄."""
        day, n = self._llm_day()
        if n[provider] >= self.llm_caps[provider] or (sonnet and n["sonnet"] >= self.llm_caps["sonnet"]):
            return False
        n[provider] += 1
        if sonnet:
            n["sonnet"] += 1
        self.db.kv_set(KV_LLM_DAY, json.dumps({"d": day, **n}))
        return True

    def llm_view(self) -> dict[str, Any]:
        _d, n = self._llm_day()
        c = self.llm_caps
        return {"gemini": {"today": n["gemini"], "cap": c["gemini"]},
                "claude": {"today": n["claude"], "cap": c["claude"], "sonnet": n["sonnet"], "sonnet_cap": c["sonnet"]}}

    def view(self) -> dict[str, Any]:
        """화면용 (로그인 아래에서만): 받아 둔 상태 · 받은 시각 · 안 끝난 명령 수 · 끊김 여부 · 최근 처리 결과."""
        saved = self._load(KV_STATE, {})
        ts = saved.get("ts") if isinstance(saved, dict) else None
        cmds = self._cmds()
        out: dict[str, Any] = {
            "state": saved.get("state") if isinstance(saved, dict) else None,
            "receivedAt": saved.get("receivedAt") if isinstance(saved, dict) else None,
            "pending": len(cmds), "stale": stale(ts, self.clock()),
            "results": self._load(KV_RESULTS, [])[-10:],
            "search_proxy": {"keysrc": self.search_keysrc(), "today_count": self.search_today(),
                             "keys": self._load(KV_SEARCH_KEYS, {})},
            "llm_proxy": self.llm_view(),  # 11/15 LLM 대리 호출 오늘 횟수·한도 (숫자만)
            # 오류 검수 경고 한 줄 (주인 11/14) — 거른 목록만 · 칸이 없으면 [] (화면은 그대로)
            "alerts": clean_alerts(saved.get("state") if isinstance(saved, dict) else None)}
        # 있을 때만 싣는 칸 (없으면 화면은 그대로): 마지막 실행 점검 한 줄 · 보내고 아직 쌀먹이 안 가져간 퀴즈 답
        health = clean_health(out["state"])
        if health is not None:
            out["health"] = health
        # at = 보낸 시각 (화면이 5분 넘은 대기를 '아직 대기'로 — 명령은 하루 뒤 _cmds 에서 사라짐)
        quiz = [{"id": c["id"], "at": c.get("at"), **{k: c["args"].get(k) for k in ("question", "answer", "key")}}
                for c in cmds if c["op"] == "quiz_answer"][-QUIZ_PENDING_MAX:]
        if quiz:
            out["quiz_pending"] = quiz
        return out

    # ------------------------------------------------------------ 명령
    def act(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        op = body.get("op")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if op not in OPS:
            return 400, {"status": "error", "code": "bad_op", "text": "모르는 명령이에요"}
        cmds = self._cmds()
        if op in ("run", "search_retry"):
            args = {}
            same = next((c for c in cmds if c["op"] == op), None)
            if same is not None:  # 겹치면 하나만
                return 200, {"status": "ok", "ok": True, "id": same["id"], "dup": True}
        elif op == "done":
            url = args.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 2000:
                return 400, {"status": "error", "code": "bad_args", "text": "이벤트 주소가 이상해요"}
            args = {"url": url, "done": bool(args.get("done"))}
            cmds = [c for c in cmds if not (c["op"] == "done" and c["args"].get("url") == url)]  # 같은 이벤트는 마지막 것만
        elif op == "trust_host":  # 쌀먹이 http·IP·포트를 다시 거르고 위험 문구·민감정보·하루 상한도 그대로 봄 — 여기선 모양만
            host = str(args.get("host") or "").strip().lower().rstrip(".")
            if not HOST_RE.match(host):
                return 400, {"status": "error", "code": "bad_args", "text": "사이트 주소가 이상해요"}
            args = {"host": host}
            cmds = [c for c in cmds if not (c["op"] == "trust_host" and c["args"].get("host") == host)]
        elif op == "quiz_answer":  # 쌀먹이 '카드 보기 중 하나'·짧은 질문·key 없는 같은 질문 여럿을 다시 거르고 ack 결과에 이유를 줌 — 여기선 모양만
            q, a, k = args.get("question"), args.get("answer"), args.get("key")
            if not isinstance(q, str) or not q.strip() or len(q) > QUIZ_Q_MAX or not isinstance(a, str) or not a.strip() or len(a) > QUIZ_A_MAX:
                return 400, {"status": "error", "code": "bad_args", "text": "퀴즈 질문·답이 이상해요"}
            if k is not None and (not isinstance(k, str) or len(k) > QUIZ_KEY_MAX):
                return 400, {"status": "error", "code": "bad_args", "text": "퀴즈 카드 정보가 이상해요 — 새로고침 뒤 다시"}
            args = {"question": q, "answer": a, "key": k or None}  # 질문·답은 카드 글 그대로 (다듬지 않음) · key 는 카드 값 그대로 (없으면 null)
            cmds = [c for c in cmds if not (c["op"] == "quiz_answer" and c["args"].get("question") == q
                                            and c["args"].get("key") == args["key"])]  # 같은 퀴즈는 마지막에 누른 답만
        else:
            prof = _clean_profile(args.get("profile"))
            if not prof:
                return 400, {"status": "error", "code": "bad_args", "text": "바꿀 내 정보가 없어요"}
            args = {"profile": prof}
        if len(cmds) >= CMD_MAX:
            return 429, {"status": "error", "code": "too_many", "text": "이벤트 참여 서버가 명령을 안 가져가고 있어요 — 연결을 확인해 주세요"}
        now = self.clock()
        cmd = {"id": secrets.token_hex(8), "op": op, "args": args, "at": _iso(now), "ts": now}
        cmds.append(cmd)
        self.db.kv_set(KV_CMDS, json.dumps(cmds, ensure_ascii=False))
        if op == "done":
            self._mark_done(args["url"], args["done"])
        return 200, {"status": "ok", "ok": True, "id": cmd["id"]}

    def _mark_done(self, url: str, done: bool) -> None:
        """화면이 바로 바뀌게 받아 둔 상태에도 먼저 (쌀먹이 처리하고 다시 보내면 그걸로 덮임)."""
        saved = self._load(KV_STATE, {})
        st = saved.get("state") if isinstance(saved, dict) else None
        todo = st.get("todo") if isinstance(st, dict) else None
        if not isinstance(todo, list):
            return
        for t in todo:
            if isinstance(t, dict) and t.get("url") == url:
                t["done"] = done
        left = [t for t in todo if isinstance(t, dict) and not t.get("done")]
        if isinstance(st.get("counts"), dict):
            st["counts"]["left"] = len(left)
            st["counts"]["today"] = sum(1 for t in left if t.get("today"))
        self.db.kv_set(KV_STATE, json.dumps(saved, ensure_ascii=False))


def stale(received_ts: float | None, now: float) -> bool:
    """'연결 끊김': 받은 적이 없거나 20분 넘게 push 가 없음."""
    return received_ts is None or now - float(received_ts) > STALE_AFTER


# ---------------------------------------------------------------- 웹
JT = "application/json; charset=utf-8"
NO_STORE = {"Cache-Control": "no-store"}


def _j(status: int, obj: Any) -> tuple[int, str, str, dict[str, str]]:
    return status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), JT, dict(NO_STORE)


def register(web: Any, api: Any) -> Ssalmuk:
    """DashApi.register 가 부른다. api = DashApi (guard · caller · audit · bot.settings)."""
    from dealbot.dashboard.actions import RateWindow
    from dealbot.dashboard.auth import client_key

    desk = Ssalmuk(api.dash.db)
    rate = RateWindow(120, 60.0)  # 쌀먹 서버 (같은 손님 1분 120번)
    screen_rate = RateWindow(60, 60.0)
    bad = _j(401, {"code": "bad_key", "text": "연결 키가 맞지 않아요"})

    def machine(headers: dict[str, str]) -> tuple[int, str, str, dict[str, str]] | None:
        if not rate.hit(client_key(headers)):
            return _j(429, {"code": "rate", "text": "너무 자주 불렀어요"})
        if not desk.machine_ok(headers):
            log.info("ssalmuk: wrong key from %s", hashlib.sha256(client_key(headers).encode()).hexdigest()[:8])
            return bad
        return None

    async def push(body: dict[str, Any], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(*desk.push(body))

    async def commands(query: dict[str, str], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(200, desk.commands())

    async def ack(body: dict[str, Any], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(*desk.ack(body))

    search_rate = RateWindow(SEARCH_PER_MIN, 60.0)  # 키 하나당 (손님 주소와 상관없이)
    blog_keys = BlogKeyFile(lambda: api.bot.settings.dashboard.ssalmuk_search_env_file)
    # 앞 키를 마지막으로 써 본 때 (메모리 — 재시작하면 한 번 다시 봄): dealbot = 블로그 키로 넘어간 뒤 · naver = 카카오로 넘어간 뒤
    state_rt = {"dealbot_try": 0.0, "naver_try": 0.0}

    async def search(query: dict[str, str], headers: dict[str, str]) -> Any:
        import httpx

        denied = machine(headers)
        if denied is not None:
            return denied
        typ = (query.get("type") or "blog").strip()
        q = (query.get("q") or "").strip()
        sort = (query.get("sort") or "sim").strip()
        if typ not in SEARCH_TYPES or not 1 <= len(q) <= SEARCH_Q_MAX or sort not in ("sim", "date"):
            return _j(400, {"code": "bad_query", "text": "type(blog|news|webkr)·q(1~100자)·sort(sim|date) 를 확인해 주세요"})
        try:
            display = max(1, min(20, int(query.get("display") or 10)))
        except ValueError:
            display = 10
        sec = api.bot.settings.secrets
        keys: dict[str, tuple[str, str]] = {}
        if sec.naver_client_id and sec.naver_client_secret:
            keys["dealbot"] = (sec.naver_client_id, sec.naver_client_secret)
        blog = blog_keys.get()
        if blog:
            keys["blog"] = blog
        kakao = (getattr(sec, "kakao_rest_api_key", None) or "").strip() or blog_keys.kakao()  # 딜봇 환경 먼저, 없으면 Blog-Auto .env 한 줄
        seen: dict[str, Any] = {"dealbot": "none" if "dealbot" not in keys else "-",
                                "blog": "no_file" if not blog else "-", "kakao": "none" if not kakao else "-"}
        if not keys and not kakao:
            desk.note_keys(seen)
            return _j(503, {"code": "no_key", "text": "딜봇에 검색 키(네이버·카카오)가 없어요"})
        if not search_rate.hit("key") or not desk.search_day_hit():
            return _j(429, {"code": "limit", "text": "검색 한도(1분 60번·하루 300번)를 넘었어요"})
        now = desk.clock()
        src_now = (desk.search_keysrc() or "").removesuffix("_hub") or None
        webkr_skip = typ == "webkr" and desk.webkr_hub_missing()  # HUB 에 웹문서 검색이 없음 → 카카오 web 먼저, 네이버는 예전 주소만
        if webkr_skip and kakao:
            order = ["kakao", "dealbot", "blog"]
        elif src_now == "kakao" and kakao and now - state_rt["naver_try"] < KEY_RECHECK:
            order = ["kakao", "dealbot", "blog"]  # 네이버 키가 둘 다 막혀 카카오로 된 뒤 — 네이버는 하루 한 번만 다시 봄
        elif src_now == "blog" and now - state_rt["dealbot_try"] < KEY_RECHECK:
            order = ["blog", "dealbot", "kakao"]
        else:
            order = ["dealbot", "blog", "kakao"]
        status, data, used, via_hub = 0, None, None, False
        for src in (o for o in order if o in keys or (o == "kakao" and kakao)):
            used, via_hub = src, False
            if src == "dealbot" and not webkr_skip:
                state_rt["dealbot_try"] = now
            if src != "kakao" and not webkr_skip:
                state_rt["naver_try"] = now
            try:
                if src == "kakao":
                    resp = await api.bot.http.get(
                        KAKAO_URL.format(type=KAKAO_TYPE[typ]),
                        params={"query": q, "sort": "recency" if typ == "news" else KAKAO_SORT[sort],
                                "size": min(display, KAKAO_SIZE_MAX), "page": 1},
                        headers={"Authorization": f"KakaoAK {kakao}"}, timeout=SEARCH_TIMEOUT)
                else:
                    cid, csec = keys[src]
                    params = {"query": q, "display": display} if typ == "webkr" else {"query": q, "display": display, "sort": sort}  # 웹문서 검색은 sort 가 없음
                    # 11/15 주인 키는 네이버 API HUB 키 — HUB 먼저, 401/403 (webkr 은 404 도) 이면 예전 주소로 (naverhub)
                    resp, via_hub, hub_status = await naverhub.get(api.bot.http, typ, cid, csec, params=params, timeout=SEARCH_TIMEOUT,
                                                                   hub=not webkr_skip)
                    if typ == "webkr" and hub_status == 404:
                        webkr_skip = True  # 하루 동안 webkr 은 곧장 카카오 · 이번 요청의 다음 키도 HUB 를 건너뜀
                        desk.set_webkr_hub_missing()
                status = resp.status_code
                if src == "kakao":
                    seen[src] = status
                elif via_hub:
                    seen[src] = f"HUB {status}"
                elif typ == "webkr" and webkr_skip and status in (401, 403):
                    pass  # 웹문서만 안 되는 것 — 블로그·뉴스에서 본 이 키의 답을 덮지 않음
                else:
                    seen[src] = status if status != 401 else f"401 {_why_401(resp)}"
                data = resp.json() if status == 200 else None
            except (httpx.HTTPError, ValueError) as e:  # 시간 초과·연결 실패·깨진 답 (키·주소는 로그에 안 남김)
                log.info("ssalmuk search: %s (%s key)", type(e).__name__, src)
                seen[src] = "no_answer"
                desk.note_keys(seen)  # 여기까지 써 본 키의 답도 남김 (앞 키가 401 이었으면 그 줄이 보이게)
                out: dict[str, Any] = {"code": "naver_error", "status": 0, "text": "네이버 검색이 답하지 않아요"}
                if src == "kakao":
                    out.update(source="kakao", text="카카오 검색이 답하지 않아요")
                return _j(502, out)
            if status in (401, 403):  # 이 키는 막힘 — 다른 키로 (카카오 401/403 도 — 남은 네이버 키가 있으면)
                log.info("ssalmuk search: HTTP %s (%s key)", status, src)
                continue
            if status == 200 and not (typ == "webkr" and src == "kakao"):  # 웹문서만 카카오로 간 건 키 순서를 바꾸지 않음
                desk.set_search_keysrc(f"{src}_hub" if via_hub else src)
            break
        desk.note_keys(seen)
        if status != 200:
            log.info("ssalmuk search: HTTP %s (%s)", status, used)
            out = {"code": "naver_error", "status": status, "text": "네이버 검색이 오류를 줬어요"}
            if used == "kakao":
                out.update(source="kakao", text="카카오 검색이 한도를 넘었어요" if status == 429 else "카카오 검색이 오류를 줬어요")
            return _j(502, out)
        if used == "kakao":
            items, total = kakao_items(data, typ)
            return _j(200, {"items": items, "total": total, "source": "kakao"})
        items, total = search_items(data)
        out = {"items": items, "total": total}
        if via_hub:
            out["source"] = "naver_hub"  # 쌀먹이 네이버 결과를 구분하게 (9/7 네이버 약관: 검색 결과를 AI 입력에 쓰지 말 것 — 막지는 않음)
        return _j(200, out)

    async def state(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        if not screen_rate.hit(caller.session):
            return _j(429, {"status": "error", "code": "rate", "text": "너무 자주 불렀어요 — 잠시 뒤 다시"})
        return _j(200, desk.view())

    async def act(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        status, out = desk.act(body)
        # 기록엔 명령 이름만 (내 정보 값·주소는 남기지 않음)
        api.audit(session8=caller.session, client8=caller.client8, action=f"ssalmuk.{str(body.get('op'))[:12]}", target="",
                  args={}, code=out.get("code") or "done", action_id=None)
        return _j(status, out)

    def key_view() -> dict[str, Any]:
        return {"key": desk.key(), "at": desk.key_at(), "url": api.bot.settings.dashboard.public_url.rstrip("/")}

    async def key(query: dict[str, str], headers: dict[str, str]) -> Any:
        return _j(200, key_view())

    async def rotate(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        desk.rotate()
        api.audit(session8=caller.session, client8=caller.client8, action="ssalmuk.rotate", target="", args={}, code="done",
                  action_id=None)
        return _j(200, key_view())

    web.route_json(PUSH_PATH, push, max_body=BODY_MAX)
    web.route(COMMANDS_PATH, commands, with_headers=True)
    web.route_json(ACK_PATH, ack, max_body=BODY_MAX)
    web.route(SEARCH_PATH, search, with_headers=True)
    from dealbot.dashboard import ssalmuk_llm

    ssalmuk_llm.register(web, api, desk, machine, blog_keys, _j)
    web.route(STATE_PATH, state, with_headers=True)
    web.route_json(ACT_PATH, act, max_body=ACT_BODY_MAX)
    web.route(KEY_PATH, key, with_headers=True)
    web.route_json(ROTATE_PATH, rotate, max_body=1024)
    return desk
