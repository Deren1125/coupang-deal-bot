"""대시보드에서 누르는 일 (POST /dashboard/api/action) — 무엇을 누를 수 있는지·확인 창·딜봇에서 바로 하는 일 (spec §1.9·§2·§4.5).

본문 {"action":"blog.publish","args":{…},"cid":"c_…","ctoken":null} → 응답:
    200 {"status":"ok|error","code":…,"text":…,"refresh":true}   딜봇에서 바로 한 일 (deal.* · bot.* · sns.* · push.* · todo.* · stats.*)
    202 {"status":"queued","id":"a_…"}                           Blog-Auto 에 맡긴 일 (blog.*) — 화면은 /api/actions?ids= 로 결과를 봄
    409 {"status":"confirm","ctoken":…,"title":…,"body":…,"ok":…,"danger":…}   되돌리기 어려운 일(★)의 확인 창 — ctoken 을 붙여 다시
    400/403/429/503 {"status":"error","code":…,"text":…}

- 확인 창(★) 목록은 CONFIRM 이 하나뿐인 기준. ctoken = HMAC(세션 | 일 | 정리한 args | 시각), 5분 · 한 번만
- 같은 cid(누를 때마다 화면이 만든 난수)는 10분 동안 같은 결과 (두 번 눌러도 한 번)
- 딜봇 일은 텔레그램 명령과 같은 DealBot 함수를 같은 검사로 부른다. 그동안 그 줄(target)에 새로 열린 알림만 quiet (주인이 그 줄을 보는 중)
- 대시보드는 force 를 넘기지 않는다 — Blog-Auto 의 '직접 고친 글' 지킴(A-37)이 그대로 막는다. 덮어쓰기는 oedit_redo(★★) 하나
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dealbot.bridge import spool
from dealbot.bridge.actions_out import ActionsBusy, ActionsOut, ActionStore, redact, status_of
from dealbot.dashboard import srvcheck
from dealbot.dashboard.blogauto import (
    LB_ID_RE,
    PIC_ID,
    PIC_JOB_ID,
    PT_ITEM_ID,
    PT_REASONS,
    TH_ITEM_ID,
    TH_MEDIA_ID,
    TRIAL_REASONS,
    VISIT_ID,
)
from dealbot.dashboard.earn import EarnError
from dealbot.dashboard.model import safe_text
from dealbot.notify import iid as iidmod
from dealbot.utils.text import truncate

log = logging.getLogger(__name__)

BLOGS = ("finance", "issue", "hotdeal")
BLOG_NAMES = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
CTOKEN_S = 300
CID_S = 600
CID_RE = re.compile(r"^c_[A-Za-z0-9_-]{6,40}$")
ACTION_RE = re.compile(r"^[a-z]+\.[a-z_]+$")
ACTION_RATE = 30  # 세션마다 1분에
URL_MAX = 2000
SECRET_MAX = 600
CREDIT_MAX = 60  # Blog-Auto 와 같은 한도 (넘으면 사진을 다 올린 뒤에야 거절되지 않게 여기서 바로)
CHANNEL_URL_MAX = 300
IG_LINKS_MAX, IG_URL_MAX = 12, 300  # Blog-Auto bridge._ig_links 와 같은 한도
IG_CODE_RE = re.compile(r"[\w-]{5,40}")
# 링크에서 사진 고르기 (주인 10/18): 열지 않는 곳 — Blog-Auto llm_router.NO_FETCH 와 같게 (메타 SNS · 남의 네이버 블로그·카페)
NO_FETCH = ("instagram.com", "instagr.am", "cdninstagram.com", "facebook.com", "fb.com", "fb.me", "fbcdn.net",
            "threads.net", "threads.com", "blog.naver.com", "blog.me", "cafe.naver.com")
PICS_URL_MAX, PICS_WANT_MAX, PICS_PICK_MAX = 500, 200, 24
SEARCH_URLS_MAX = 60

# ★ 확인 창 (spec §4.5 — 하나뿐인 기준). blog.mode 는 발행 모드로 바꿀 때만, push.unsubscribe 는 다른 기기일 때만
CONFIRM = frozenset({
    "deal.skip", "deal.repost", "deal.post", "bot.pause", "sns.test", "sns.disconnect",
    "blog.publish", "blog.drop", "blog.fix", "blog.rewrite", "blog.restyle", "blog.share", "blog.channel", "blog.oedit_redo",
    "blog.mode", "blog.pause", "blog.drafts_cleanup", "blog.ledger", "push.unsubscribe", "blog.unschedule_all",
    "blog.post_edit",  # 발행 글 고치기 (주인 10/21) — 공개된 글을 바로 바꿈
    "blog.threads_post", "blog.threads_reply",  # 스레드 글·답글 (주인 10/21) — 밖으로 나가는 글
})
DANGER = frozenset({"blog.oedit_redo", "blog.drop", "deal.skip"})
# 그 블로그가 자동 발행 모드면 바로 네이버에 공개되는 일 (Blog-Auto nl.CATALOG 의 may_publish 와 같게) — 그때만 ★
MAY_PUBLISH = frozenset({"blog.retry", "blog.fix", "blog.photos", "blog.photos_batch", "blog.write", "blog.issue", "blog.pick", "blog.hotdeal_now", "blog.fixpic",
                         "blog.similar_links"})
FIXED_BLOG = {"blog.write": "finance", "blog.pick": "finance", "blog.issue": "issue", "blog.hotdeal_now": "hotdeal",
              "blog.fixpic": "finance", "blog.similar_links": "issue", "blog.photo_feedback": "issue"}
HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")  # 예약 시간 바꾸기 (주인 10/19)
LEARN_ID = re.compile(r"^l[pr]-[0-9a-f]{8}$")  # Blog-Auto learn.py 개선안(lp-)·규칙(lr-) id
FEEDBACK_REASON_MAX = 80  # Blog-Auto learn.x_post_feedback 과 같은 한도
PT_NOTE_MAX = 200  # 제품 테스트 한 줄 (Blog-Auto product_trial.NOTE_MAX)
# 비슷한 아이템 링크 넣기 (주인 10/20) — Blog-Auto bridge.x_similar_links 와 같은 한도 · 화면 글 칸은 한 줄에 '이름 | 링크'
SIMILAR_LINKS_MAX, SIMILAR_URL_MAX, SIMILAR_NOTE_MAX, SIMILAR_TEXT_MAX = 6, 500, 40, 4000
HTTPS_URL = re.compile(r"https://[^\s|<>\"']+")
# Blog-Auto 에 맡기는 일 (blog.<이름> → Blog-Auto 의 이름)
BLOG_ACTIONS = ("publish", "drop", "retry", "fix", "fixpic", "rewrite", "restyle", "photos", "photo_clear", "answer", "noanswer",
                "photos_batch",  # 사진 일괄 수정 (주인 10/19 — '수정 완료' 한 번)
                "post_notes",  # 내 후기 쓰기 (주인 10/20 — 핫딜 글에 주인 메모·사진 → 1인칭 후기로 다시)
                "oedit_keep", "oedit_redo", "share", "noshare", "channel", "scan", "pick", "write", "issue", "hotdeal_now", "mode",
                "pause", "resume", "drafts_cleanup", "ledger", "login_check", "server_check", "info", "kakao_auth_url", "kakao_connect", "kakao_test",
                "dismiss", "schedule", "unschedule", "unschedule_all", "schedule_move", "visit_done", "pics_fetch", "pics_clear",
                "post_feedback", "learn_decide",  # 배운 것으로 나아지기 (주인 10/20): 글마다 👍/👎 · 이번 주 개선안 적용·빼기·규칙 지우기
                "similar_links",  # 비슷한 아이템 링크 넣기 (주인 10/20 — Pick 패션 글에 에이블리 링크)
                "topic_seed",  # 소재 넣기 (주인 10/21 — 카페 인기글 주제·아이디어를 다음 글감의 첫 후보로)
                "photo_feedback",  # 사진 테스트 (주인 10/21 — 시험 글 사진마다 ✅/❌·이유 · 글 전체 · 테스트 끝)
                "product_feedback",  # 제품 테스트 (주인 10/21 — Pick 비슷한 아이템 후보 ✅/❌·이유 · 글마다 한 줄·테스트 끝)
                "threads_post", "threads_cancel", "threads_reply",  # 스레드 (주인 10/21 — 주인이 쓴 글 바로·예약 · 예약 취소 · 내 글 답글에 답)
                "post_edit",  # 발행 글 고치기 (주인 10/21 — 최근 글 '고치기': 사진 빼기·교체 · 문장 빼기)
                "x_posted", "x_drop", "x_redo",  # X 초안 (주인 10/25 — 올렸어요 · 버리기 · 다시 쓰기 · X API 없음, 올리기는 주인이 X 앱에서)
                "lookbook_posted", "lookbook_drop", "lookbook_redo",  # 룩북 릴스 (주인 10/25 — 올렸어요 {id, url} · 버리기 · 다시 만들기 · 올리기는 주인이 인스타 앱에서)
                "lookbook_trial", "lookbook_style")  # 사진 시험 (주인 10/26 — 시험 새로 만들기 {base?} · 이 양식으로 정하기 {style})
DEAL_ACTIONS = ("deal.link", "deal.skip", "deal.nolink", "deal.sc_forget", "deal.ok", "deal.ok_text", "deal.posted", "deal.repost", "deal.late_photo", "deal.post",
                "bot.pause", "bot.resume", "bot.run", "sns.token", "sns.code", "sns.test", "sns.invites", "push.test",
                "todo.dismiss", "todo.hide", "login.remote", "stats.refresh", "stats.input", "search.check",
                "login.qr",  # 딜봇 네이버 QR 로그인 (주인 10/26 — 대시보드 '로그인 관리' · 텔레그램 /naverlogin 과 같은 길)
                "earn.save", "earn.delete",  # 제휴 수익 직접 넣기 (주인 10/23 — 토스쇼핑·에이블리·지그재그·무신사·29CM·기타)
                "x.stats", "x.handles")  # X 진행률 숫자·계정 이름 직접 넣기 (주인 10/25 — X API 없음, 크리에이터 스튜디오 숫자를 주인이 주마다)
# 다 했어요 (todo.dismiss): 밖에서 끝내는 일만. 링크·확인·사진·답·발행·직접 고친 글 같은 진짜 일은 일을 해야 닫힌다
DISMISS_POST_HEADS = ("dup", "cards", "openchat")
REAL_WORK_HEADS = ("link", "review", "unsure", "photo", "ask", "draft", "oedit", "fail", "chan", "share", "hot", "sched")
# Blog-Auto 가 닫아 주는 키 머리 (blogauto/bridge.py DISMISS_HEADS 와 같게) — 이 밖의 Blog-Auto 줄은 '다 했어요'를 보이지 않는다
BLOGAUTO_DISMISS_HEADS = ("dup", "cards", "openchat", "cq", "buddy", "stalled", "scan", "err", "loop", "neighbors", "restyle_once")
REPLIES_KEY = "threads_replies:"
# 알림 없애기 (todo.hide — 주인 10/23 "할일 알림 다 내가 임의로 없앨 수 있게"): 어떤 줄이든 할 일 화면에서만 뺀다 (일 자체는 그대로).
# 같은 줄이 새로 열리면(시각 since 가 더 늦음) 다시 보인다. 기록은 봇 DB kv 에 (since 가 없는 줄은 사흘, 나머지는 2주 뒤 잊음)
HIDDEN_KEY = "dash:hidden"
HIDDEN_DAYS, HIDDEN_NOSINCE_DAYS, HIDDEN_MAX = 14, 3, 300
# X 진행률 (주인 10/25 — 유료 API 없이 주인이 주마다 직접 넣는 숫자): 봇 DB kv 에 [{at, followers, verified, impressions_90d}] 최근 60번
X_STATS_KEY, X_STATS_KEEP = "dash_x_stats", 60
X_STATS_MAX = {"followers": 10**9, "verified": 10**9, "impressions_90d": 10**12}
X_HANDLES_KEY = "dash_x_handles"  # {pick: 데런픽 계정, revenue: 수익 계정} — 아직 안 정했으면 빈 값
X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
# 룩북 릴스 '올렸어요' 주소 (주인 10/25): Blog-Auto bridge._reel_url 과 같은 규칙 — https · instagram.com · /p|reel|reels|tv/<코드>/ (앞에 /계정/ 가능).
# 글자만 본다 (인스타그램에 접속하지 않음) · 물음표 뒤(igsh= 공유 꼬리)는 버리고 www.instagram.com 주소로
LB_ACTIONS = ("blog.lookbook_posted", "blog.lookbook_drop", "blog.lookbook_redo")
LB_STYLES = ("mirror", "studio")  # 사진 시험 양식 (Blog-Auto lookbook.STYLES — 거울 셀카 · 예전 양식)
REEL_URL_MAX = 300
REEL_HOSTS = ("instagram.com", "www.instagram.com", "m.instagram.com")
REEL_PATH_RE = re.compile(r"^/(?:[A-Za-z0-9_.]{1,30}/)?(?:p|reels?|tv)/[A-Za-z0-9_-]{5,40}/?$")


def hidden_rows(db: Any, now: float) -> dict[str, dict[str, Any]]:
    """{iid: {since, at}} — 지난 것은 뺀 채로."""
    try:
        raw = json.loads(db.kv_get(HIDDEN_KEY) or "{}")
    except (ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for iid, h in raw.items():
        if not isinstance(h, dict) or not isinstance(h.get("at"), int | float):
            continue
        days = HIDDEN_DAYS if h.get("since") is not None else HIDDEN_NOSINCE_DAYS
        if now - float(h["at"]) < days * 86400:
            out[str(iid)] = h
    return out


def x_stats_rows(db: Any) -> list[dict[str, Any]]:
    """X 진행률 기록 [{at, followers, verified, impressions_90d}] (오래된 것부터). 모양이 틀린 줄은 뺀다."""
    try:
        raw = json.loads(db.kv_get(X_STATS_KEY) or "[]")
    except (ValueError, TypeError):
        return []
    return [r for r in raw if isinstance(r, dict) and isinstance(r.get("at"), int | float)
            and all(isinstance(r.get(k), int) and not isinstance(r.get(k), bool) and 0 <= r[k] <= hi for k, hi in X_STATS_MAX.items())] \
        if isinstance(raw, list) else []


def x_handles(db: Any) -> dict[str, str]:
    try:
        raw = json.loads(db.kv_get(X_HANDLES_KEY) or "{}")
    except (ValueError, TypeError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    return {k: h if isinstance(h := raw.get(k), str) and X_HANDLE_RE.match(h) else "" for k in ("pick", "revenue")}


def is_hidden(item: dict[str, Any], hidden: dict[str, dict[str, Any]]) -> bool:
    """주인이 없앤 줄인가 — 없앤 뒤 같은 줄이 새로 열렸으면(since 가 더 늦음) 아님."""
    h = hidden.get(item.get("iid") or "")
    if h is None:
        return False
    since, hs = item.get("since"), h.get("since")
    if since is None or hs is None:
        return since is None
    try:
        return float(since) <= float(hs) + 1
    except (TypeError, ValueError):
        return False


def dismissible(iid: str, key: str, *, dealbot_row: bool) -> bool:
    """'다 했어요'를 보여 줄·받을 줄인가. dealbot_row: 딜봇이 연 알림 줄 (딜봇이 바로 닫음) — 아니면 Blog-Auto 가 닫을 수 있는 머리만."""
    if iid == "sns-replies":
        return True
    head = key.split(":", 1)[0]
    kind_ok = (iid.startswith("post-") and head in DISMISS_POST_HEADS) or iid.startswith(("blog-", "sys-")) or iid == "sns-status"
    if not kind_ok or head in REAL_WORK_HEADS or not key or key.endswith(":"):
        return False
    return dealbot_row or head in BLOGAUTO_DISMISS_HEADS

# 화면 안내 (코드 → 한 줄). 화면은 코드로 고르고, text 는 대신 쓸 글
TEXTS = {
    "done": "했어요",
    "linked": "링크를 넣었어요 — 차례가 되면 채널에 올라가요",
    "linked_twin": "같은 상품이 새 번호로 다시 줄 서 있어 그 딜에 넣었어요",
    "link_not_waiting": "지금은 링크를 기다리는 딜이 아니에요 (이미 넣었거나 내려간 딜)",
    "link_replaced_late": "더 좋은 딜로 바뀐 뒤 시간이 지나 넣지 않았어요",
    "link_twin_review": "같은 상품이 확인을 기다리고 있어요 — 먼저 올리기·안 올리기를 정해 주세요",
    "link_bad": "이 링크는 붙일 수 없어요 — 그 몰 앱에서 만든 상품·제휴 링크를 넣어 주세요",
    "link_failed": "링크를 넣지 못했어요 — 잠시 뒤 다시 눌러 주세요",
    "link_busy": "링크 넣기는 1분에 10번까지예요 — 조금 뒤에 다시",
    "photo_refused": "사진을 쓸 수 없어요 — 다른 사진으로 다시",
    "approved": "올릴 차례에 넣었어요 — 곧 채널에 올라가요",
    "skipped": "안 올리기로 했어요",
    "posted": "채널에 올라간 글로 기록했어요",
    "no_link": "넣어 둔 링크가 없어요 — 링크를 넣어 다시 올려 주세요",
    "queued": "맨 앞에 넣었어요 — 차례가 되면 바로 채널에 올라가요",
    "paused": "딜봇을 멈췄어요",
    "resumed": "딜봇을 다시 시작했어요",
    "run": "곧 확인할게요 (1분 안에 시작)",
    "connected": "연결했어요",
    "not_found": "없는 항목이에요",
    "wrong_state": "지금 상태에서는 할 수 없어요",
    "bad_args": "입력한 값을 다시 확인해 주세요",
    "bad_upload": "올린 파일을 쓸 수 없어요 — 다시 올려 주세요",
    "failed": "하지 못했어요 — 잠시 뒤 다시 눌러 주세요",
    "unsupported": "블로그 프로그램 업데이트를 기다리는 중이에요",
    "blogauto_down": "블로그 프로그램이 응답하지 않아요",
    "not_dismissible": "이 일은 직접 처리해야 닫혀요",
    "busy": "앞의 작업이 아직 하는 중이에요 — 끝나면 다시 눌러 주세요",
    "rate": "너무 자주 눌렀어요 — 조금 뒤에 다시",
    "confirm_expired": "확인 창이 오래됐어요 — 다시 눌러 주세요",
    "off": "지금은 대시보드에서 할 수 없어요",
    "pushed": "테스트 알림을 보냈어요",
    "no_device": "알림 켠 기기가 없어 테스트 카톡을 보냈어요",
    "scheduled": "간격 두고 발행 예약했어요",
    "spaced_only": "이 블로그는 간격 두고 발행만 돼요 — 블로그 프로그램 업데이트를 기다리는 중이에요",
    "no_fetch": "인스타·페이스북·스레드·네이버 블로그·카페 링크는 열지 않아요 — 기사나 쇼핑몰 링크를 넣어 주세요",
}


class ActionError(Exception):
    def __init__(self, code: str, status: int = 400, text: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.text = text or TEXTS.get(code, TEXTS["failed"])


@dataclass(slots=True)
class Caller:
    """누가 눌렀나: session = 세션 해시 앞 8자 (Basic 이면 'basic'), client8 = 접속 주소 해시 앞 8자."""

    session: str
    client8: str = ""


def plain(text: str, limit: int = 120) -> str:
    """DealBot 답(텔레그램 HTML) → 화면 한 줄 (태그·명령 줄 없이, 토큰처럼 보이는 글자는 가림)."""
    s = re.sub(r"<[^>]+>", "", text or "")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    first = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    return safe_text(first, limit)


# ---------------------------------------------------------------- args 걸러 받기
def _int(v: Any, lo: int = 1, hi: int = 10**9) -> int:
    if isinstance(v, bool):
        raise ActionError("bad_args")
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        v = int(v.strip().lstrip("#"))
    if not isinstance(v, int) or not lo <= v <= hi:
        raise ActionError("bad_args")
    return v


def _str(v: Any, limit: int, *, required: bool = True) -> str:
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        raise ActionError("bad_args")
    v = v.strip()
    if (required and not v) or len(v) > limit or "\x00" in v:
        raise ActionError("bad_args")
    return v


def _blog(v: Any) -> str:
    if v not in BLOGS:
        raise ActionError("bad_args")
    return str(v)


def _uploads(v: Any, lo: int, hi: int) -> list[str]:
    if v is None:
        v = []
    if not isinstance(v, list) or not lo <= len(v) <= hi or not all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{32}", x) for x in v):
        raise ActionError("bad_args")
    return list(dict.fromkeys(v))


ANSWER_Q_MAX, ANSWER_PHOTOS_MAX = 40, 20
NOTES_MAX, NOTES_PHOTOS_MAX, PRODUCT_MAX = 2000, 10, 60  # Blog-Auto bridge.x_post_notes 와 같은 한도
SEED_MAX = 300  # 소재 넣기 (주인 10/21) — Blog-Auto early_radar.SEED_MAX 와 같은 한도
TH_TEXT_MAX = 500  # 스레드 글·답글 (Blog-Auto threads_api.TEXT_MAX — 제휴 고지를 붙여 넘으면 Blog-Auto 가 거절하며 알려 줌)
TRIAL_NOTE_MAX = 200  # 사진 테스트 메모 (주인 10/21) — Blog-Auto photo_trial.NOTE_MAX 와 같은 한도


def _answer_photos(v: Any) -> list[dict[str, Any]] | None:
    """답하기에 붙인 사진 (주인 10/18 — 공식 페이지 캡처 같은 '근거'): 질문마다 {n, text, uploads}.
    사진이 하나도 없으면 None (args 는 전과 같은 {pid, blog, text} — 계약 action_answer_photos.json)."""
    if v is None:
        return None
    if not isinstance(v, list) or not 1 <= len(v) <= ANSWER_Q_MAX or not all(isinstance(x, dict) for x in v):
        raise ActionError("bad_args")
    out = [{"n": _int(x.get("n"), 1, ANSWER_Q_MAX), "text": _str(x.get("text"), 2000, required=False),
            "uploads": _uploads(x.get("uploads"), 0, 10)} for x in v]
    ids = [u for x in out for u in x["uploads"]]
    if len({x["n"] for x in out}) < len(out) or len(set(ids)) < len(ids) or len(ids) > ANSWER_PHOTOS_MAX:
        raise ActionError("bad_args")
    return out if ids else None


def instagram_post(url: str) -> bool:
    """인스타그램 '게시물' 주소인가 (Blog-Auto issue.sns_post 의 인스타 갈래와 같게: /p/·/reel/·/reels/·/tv/ 코드, 앞에 /계정/ 가능).
    프로필·스토리·다른 사이트는 아님. 주소 글자만 본다 — 인스타그램에 접속하지 않는다."""
    try:
        p = urlparse("https:" + url if url.startswith("//") else url)
        host = re.sub(r"^(?:www|m|mobile)\.", "", (p.hostname or "").lower())
    except ValueError:
        return False
    if p.scheme not in ("http", "https") or host not in ("instagram.com", "instagr.am"):
        return False
    parts = [x for x in p.path.split("/") if x]
    return any(len(parts) > i + 1 and parts[i] in ("p", "reel", "reels", "tv") and IG_CODE_RE.fullmatch(parts[i + 1])
               and parts[i + 1] != "audio" for i in (0, 1))


def reel_url(v: Any) -> str:
    """주인이 붙여 넣은 릴스 주소 → 'https://www.instagram.com/reel/<코드>/' (REEL_PATH_RE). 아니면 ActionError (쉬운 말)."""
    bad = ActionError("bad_args", text="인스타 릴스 주소를 넣어 주세요 (instagram.com/reel/… 주소)")
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > REEL_URL_MAX or any(c.isspace() for c in v.strip()):
        raise bad
    try:
        p = urlparse(v.strip())
        ok = p.scheme == "https" and (p.hostname or "").lower() in REEL_HOSTS and "@" not in p.netloc and p.port is None \
            and bool(REEL_PATH_RE.match(p.path))
    except ValueError:
        ok = False
    if not ok:
        raise bad
    return "https://www.instagram.com" + (p.path if p.path.endswith("/") else p.path + "/")


def _ig_links(v: Any) -> list[dict[str, Any]]:
    """사진 올리기의 인스타 링크 (Pick, 계약: Blog-Auto bridge._ig_links): [{slot: 0~40 (0 = 빈 자리에), url}] 최대 12개."""
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > IG_LINKS_MAX:
        raise ActionError("bad_args")
    out = []
    for x in v:
        if not isinstance(x, dict):
            raise ActionError("bad_args")
        url = _str(x.get("url"), IG_URL_MAX)
        if not instagram_post(url):
            raise ActionError("bad_args")
        out.append({"slot": _int(x.get("slot") or 0, 0, 40), "url": url})
    return out


def fetchable(url: str) -> bool:
    """사진을 뽑으러 열어 봐도 되는 주소인가 (http(s) · NO_FETCH 가 아님). 주소 글자만 본다 — 여기서 열지 않는다."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(host) and not any(host == d or host.endswith("." + d) for d in NO_FETCH)


def _pics_url(v: Any) -> str:
    url = _str(v, PICS_URL_MAX)
    if not re.match(r"^https?://\S+$", url, re.I):
        raise ActionError("bad_args")
    if not fetchable(url):
        raise ActionError("bad_args", text=TEXTS["no_fetch"])
    return url


def _pic_picks(v: Any) -> list[dict[str, Any]]:
    """고른 후보 사진 [{id: pk-xxxxxxxx, slot: 0~40 (0 = 빈 자리에)}] 최대 24개 (같은 사진은 한 번)."""
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > PICS_PICK_MAX:
        raise ActionError("bad_args")
    out: dict[str, int] = {}
    for x in v:
        if not isinstance(x, dict) or not isinstance(x.get("id"), str) or not PIC_ID.match(x["id"]):
            raise ActionError("bad_args")
        out.setdefault(x["id"], _int(x.get("slot") or 0, 0, 40))
    return [{"id": k, "slot": n} for k, n in out.items()]


PHOTO_OPS_MAX = 40  # Blog-Auto photo_batch.OPS_MAX 와 같게


def _photo_blog(v: Any) -> str:
    if v not in ("finance", "issue"):
        raise ActionError("bad_args")
    return str(v)


def _photo_ops(v: Any) -> list[dict[str, Any]]:
    """사진 일괄 수정 (주인 10/19 — 계약 action_photos_batch.json): [{op: remove, slot} | {op: put, slot, upload|pic, credit, edited, focus?, retouch?}
    | {op: move, from, to}] 1~40개, 적은 순서대로. upload 는 올린 사진 id (보낼 때 경로로 바꾼다), pic 은 고른 후보 id."""
    if not isinstance(v, list) or not 1 <= len(v) <= PHOTO_OPS_MAX:
        raise ActionError("bad_args")
    out: list[dict[str, Any]] = []
    for x in v:
        if not isinstance(x, dict):
            raise ActionError("bad_args")
        op = x.get("op")
        if op == "remove":
            out.append({"op": op, "slot": _int(x.get("slot"), 1, 40)})
        elif op == "move":
            a, b = _int(x.get("from"), 1, 40), _int(x.get("to"), 1, 40)
            if a == b:
                raise ActionError("bad_args")
            out.append({"op": op, "from": a, "to": b})
        elif op == "put":
            put: dict[str, Any] = {"op": op, "slot": _int(x.get("slot"), 1, 40)}
            up, pic = x.get("upload"), x.get("pic")
            if (up is None) == (pic is None) or not isinstance(x.get("edited", False), bool):
                raise ActionError("bad_args")
            if pic is not None:
                if not isinstance(pic, str) or not PIC_ID.match(pic):
                    raise ActionError("bad_args")
                put["pic"] = pic
            else:
                if not isinstance(x.get("focus", False), bool):
                    raise ActionError("bad_args")
                put.update(upload=_uploads([up], 1, 1)[0], credit=_str(x.get("credit"), CREDIT_MAX, required=False),
                           edited=x.get("edited") is True or x.get("focus") is True)
                if x.get("focus") is True:  # '사람만 남기기' (10/20) — Blog-Auto 가 주인공 둘레만 (media.focus_subject)
                    put["focus"] = True
                if x.get("retouch") is not None:  # 이 글 자리 N 사진을 편집기로 고친 것 (10/20) — 출처를 이어받음
                    put["retouch"] = _int(x.get("retouch"), 1, 40)
            out.append(put)
        else:
            raise ActionError("bad_args")
    return out


# 발행 글 고치기 (주인 10/21 — 계약 action_post_edit.json) — Blog-Auto post_edit.parse 와 같은 한도
EDIT_OPS = ("remove_photo", "replace_photo", "remove_text", "replace_text", "rebuild")
EDIT_OPS_MAX, EDIT_TEXT_MAX, EDIT_NEW_TEXT_MAX = 20, 500, 1000


def _edit_line(v: Any, limit: int) -> str:
    s = _str(v, limit)
    if re.search(r"[\x00-\x1f\x7f]", s):
        raise ActionError("bad_args")
    return " ".join(s.split())


def _edit_ops(v: Any) -> list[dict[str, Any]]:
    """[{op: remove_photo|replace_photo, index, file?} | {op: remove_text|replace_text, text, new_text?} | {op: rebuild}] 1~20개.
    index = 글 순서 N번째 사진 (최근 글 photos[].n) · file = 올린 사진 id (보낼 때 경로로 바꾼다)."""
    if not isinstance(v, list) or not 1 <= len(v) <= EDIT_OPS_MAX:
        raise ActionError("bad_args")
    out: list[dict[str, Any]] = []
    for x in v:
        op = x.get("op") if isinstance(x, dict) else None
        if op not in EDIT_OPS:
            raise ActionError("bad_args")
        o: dict[str, Any] = {"op": op}
        if op in ("remove_photo", "replace_photo"):
            o["index"] = _int(x.get("index"), 1, 40)
            if op == "replace_photo":
                o["file"] = _uploads([x.get("file")], 1, 1)[0]
        elif op in ("remove_text", "replace_text"):
            o["text"] = _edit_line(x.get("text"), EDIT_TEXT_MAX)
            if op == "replace_text":
                o["new_text"] = _edit_line(x.get("new_text"), EDIT_NEW_TEXT_MAX)
        out.append(o)
    return out


def _pids(v: Any, *, allow_all: bool = False) -> list[int] | str:
    if allow_all and v == "all":
        return "all"
    if isinstance(v, int | str) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, list) or not 1 <= len(v) <= 20:
        raise ActionError("bad_args")
    return list(dict.fromkeys(_int(x) for x in v))


def own_blog_url(url: str, blog_id: str | None) -> bool:
    """채널에 올릴 주소: 주인 블로그 글만 (blog.naver.com · m.blog.naver.com, 첫 경로가 그 블로그 id)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ("blog.naver.com", "m.blog.naver.com"):
        return False
    first = parts.path.strip("/").split("/", 1)[0].lower()
    return bool(blog_id) and first == str(blog_id).lower()


def _naver_post(url: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and (parts.hostname or "").lower() in ("blog.naver.com", "m.blog.naver.com") and bool(parts.path.strip("/"))


def clean_args(action: str, args: dict[str, Any], *, blog_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """일마다 받을 수 있는 args 만 (모양이 다르면 ActionError bad_args). 모르는 키는 버린다."""
    a = args if isinstance(args, dict) else {}
    g = a.get
    if action == "deal.link":
        out: dict[str, Any] = {"qid": _int(g("qid")), "url": _str(g("url"), URL_MAX)}
        up = g("upload_id") or ((g("uploads") or [None])[0] if isinstance(g("uploads"), list) else None)
        if up is not None:
            out["upload_id"] = _uploads([up], 1, 1)[0]
        return out
    if action in ("deal.skip", "deal.ok", "deal.repost"):
        return {"qid": _int(g("qid"))}
    if action == "deal.nolink":  # 쇼핑 커넥트에 없음: post = 링크 없이 올리기 · skip = 안 올리기
        if g("name") not in ("post", "skip"):
            raise ActionError("bad_args")
        return {"qid": _int(g("qid")), "name": g("name")}
    if action == "deal.sc_forget":  # 쇼핑 커넥트 없는 판매처 하나 빼기
        return {"name": _str(g("name"), 80)}
    if action == "deal.ok_text":
        return {"qid": _int(g("qid")), "text": _str(g("text"), 4000)}
    if action == "deal.posted":
        return {"qid": _int(g("qid")), "url": _str(g("url"), URL_MAX, required=False)}
    if action == "deal.late_photo":
        ups = g("uploads") if g("uploads") is not None else ([g("upload_id")] if g("upload_id") else None)
        return {"qid": _int(g("qid")), "uploads": _uploads(ups, 1, 1)}
    if action == "deal.post":
        return {"text": _str(g("text"), 4000)}
    if action in ("bot.pause", "bot.resume", "sns.invites", "push.test"):
        return {}
    if action == "bot.run":
        name = g("name")
        return {"name": None if name in (None, "", "all") else _str(name, 40)}
    if action in ("sns.token", "sns.code"):
        sns = g("sns")
        if sns not in ("threads", "instagram", "instagram_look"):
            raise ActionError("bad_args")
        return {"sns": sns, ("token" if action == "sns.token" else "code"): _str(g("token" if action == "sns.token" else "code"),
                                                                              SECRET_MAX)}
    if action == "sns.test":
        sns = g("sns")
        if sns not in ("threads", "instagram"):
            raise ActionError("bad_args")
        return {"sns": sns, "qid": _int(g("qid")) if g("qid") not in (None, "") else None}
    if action == "todo.dismiss":
        iid = _str(g("iid"), 64)
        if not iidmod.ok(iid):
            raise ActionError("bad_args")
        return {"iid": iid, "key": _str(g("key"), 120)}
    if action == "login.remote":
        from dealbot.dashboard.remote_login import TARGETS

        if g("target") not in TARGETS:
            raise ActionError("bad_args")
        return {"target": g("target")}
    if action == "login.qr":
        return {}
    if action == "todo.hide":
        iid = _str(g("iid"), 64)
        since = g("since")
        if not iidmod.ok(iid) or not (since is None or isinstance(since, int | float) and not isinstance(since, bool)):
            raise ActionError("bad_args")
        return {"iid": iid, "since": since}
    if action == "stats.refresh":
        return {"src": _str(g("src"), 40)}
    if action == "search.check":  # 구글 검색 등록 '등록함' (주인 10/18 22:20) — url 하나 또는 그날 urls 한 번에, done=False 는 되돌림
        raw = g("urls") if g("urls") is not None else [g("url")]
        if not isinstance(raw, list) or not 1 <= len(raw) <= SEARCH_URLS_MAX or not isinstance(g("done"), bool):
            raise ActionError("bad_args")
        urls = [_str(u, 300) for u in raw]
        if not all(_naver_post(u) for u in urls):
            raise ActionError("bad_args")
        return {"urls": list(dict.fromkeys(urls)), "done": g("done")}
    if action == "earn.save":  # 값 확인(날짜·금액·가게)은 EarnStore.clean_manual 이 — 여기서는 모양과 길이만
        amounts = {k: g(k) if isinstance(g(k), str | int) and not isinstance(g(k), bool) else "" for k in ("amount", "commission")}
        out = {"shop": _str(g("shop"), 20), "day": _str(g("day"), 10), "product": _str(g("product"), 200, required=False),
               "memo": _str(g("memo"), 400, required=False), **{k: str(v)[:20] for k, v in amounts.items()}}
        if g("id") not in (None, ""):
            out["id"] = _int(g("id"))
        return out
    if action == "earn.delete":
        return {"id": _int(g("id"))}
    if action == "x.stats":  # 0 이상 정수 셋 (인증 팔로워가 전체 팔로워보다 많으면 잘못 넣은 것)
        out = {k: _int(g(k), 0, hi) for k, hi in X_STATS_MAX.items()}
        if out["verified"] > out["followers"]:
            raise ActionError("bad_args", text="인증 팔로워가 전체 팔로워보다 많아요 — 숫자를 확인해 주세요")
        return out
    if action == "x.handles":  # 계정 이름 둘 (@ 없이 영문·숫자·_ 15자, 빈 값 = 아직 안 정함)
        out = {}
        for k in ("pick", "revenue"):
            h = _str(g(k), 16, required=False).lstrip("@")
            if h and not X_HANDLE_RE.match(h):
                raise ActionError("bad_args", text="계정 이름은 @ 뒤 영문·숫자·_ 15자까지예요")
            out[k] = h
        return out
    if action == "stats.input":
        values = g("values")
        if not isinstance(values, dict) or len(values) > 10 or not all(isinstance(k, str) and isinstance(v, str | int) for k, v in values.items()):
            raise ActionError("bad_args")
        return {"src": _str(g("src"), 40), "values": {k[:40]: str(v)[:20] for k, v in values.items()},
                "date": _str(g("date"), 10, required=False)}
    if not action.startswith("blog."):
        raise ActionError("bad_args")
    return _clean_blog(action[5:], a, blog_ids or {})


def _similar_url(v: Any) -> str:
    u = _str(v, SIMILAR_URL_MAX)
    if not HTTPS_URL.fullmatch(u) or not urlparse(u).hostname:
        raise ActionError("bad_args")
    return u


def similar_lines(text: str) -> list[tuple[str, str]]:
    """'비슷한 아이템 링크 넣기' 글 칸: 한 줄에 하나 — '이름 | 링크' · '링크' · '이름 링크'. (링크, 이름) 들 (이름은 40자)."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = HTTPS_URL.search(line)
        if not m:
            raise ActionError("bad_args")
        name = " ".join((line[: m.start()] + " " + line[m.end():]).replace("|", " ").split())
        if len(name) > SIMILAR_NOTE_MAX:
            name = name[:SIMILAR_NOTE_MAX].rstrip()
        out.append((_similar_url(m.group(0)), name))
    return out


def _clean_blog(name: str, a: dict[str, Any], blog_ids: dict[str, str]) -> dict[str, Any]:
    g = a.get
    if name in ("publish", "retry", "share", "noshare", "photo_clear") or name in ("fix", "photos", "channel"):
        out: dict[str, Any] = {"blog": _blog(g("blog")), "pid": _int(g("pid"))}
        if name == "fix":
            out["text"] = _str(g("text"), 1000)
        if name == "photo_clear":
            out["slot"] = _int(g("slot"), 1, 40)
        if name == "photos":
            links, pics = _ig_links(g("links")), _pic_picks(g("pics"))
            if links and out["blog"] != "issue":
                raise ActionError("bad_args")
            if links and not pics and not g("uploads"):  # 링크만 (사진 없이 그 자리에 게시물) — Blog-Auto 도 사진 칸 값은 보지 않는다
                return {**out, "links": links}
            # 고른 후보(pics)는 올린 사진과 같은 길 (자리·자르기·출처 — 계약 action_photos_pics.json: 올린 사진 없으면 uploads [])
            out["uploads"] = _uploads(g("uploads"), 0 if pics else 1, 20)
            mode = g("mode") or ("slot" if g("slot") else "add")
            if mode not in ("add", "replace", "slot"):
                raise ActionError("bad_args")
            out.update(mode=mode, slot=_int(g("slot"), 1, 40) if mode == "slot" else 0, in_order=g("in_order") is True,
                       crop=g("crop") is True, credit=_str(g("credit"), CREDIT_MAX, required=False))
            if links:  # 사진만 보낼 때의 args 는 전과 같다 (links 없음)
                out["links"] = links
            if pics:
                out["pics"] = pics
        if name == "channel":
            url = _str(g("url"), CHANNEL_URL_MAX, required=False)
            if url and not own_blog_url(url, blog_ids.get(out["blog"])):
                raise ActionError("bad_args")
            out["url"] = url
        return out
    if name == "photos_batch":
        return {"blog": _photo_blog(g("blog")), "pid": _int(g("pid")), "ops": _photo_ops(g("ops"))}
    if name == "post_edit":  # 발행 글 고치기 (주인 10/21): 우리 세 블로그 글 · 발행된 글인지는 Blog-Auto 가 본다
        return {"blog": _blog(g("blog")), "pid": _int(g("pid")), "ops": _edit_ops(g("ops"))}
    if name == "post_notes":  # 내 후기 쓰기 (주인 10/20 — 계약 action_post_notes.json): 핫딜 글(pid) 또는 새 제품(product)
        if g("blog") not in (None, "hotdeal"):
            raise ActionError("bad_args")
        out = {"blog": "hotdeal"}
        if g("pid") is not None:
            out["pid"] = _int(g("pid"))
        else:  # 후기 쓸 제품 고르기 — 제품 이름으로 새 제품 소개 글 (/guide 제품 과 같은 길)
            out["product"] = " ".join(_str(g("product"), PRODUCT_MAX).split())
        out["notes"] = _str(g("notes") if g("notes") is not None else g("text"), NOTES_MAX)  # 화면은 글 칸(text)으로 보냄
        out["photos"] = _uploads(g("photos") if g("photos") is not None else g("uploads"), 0, NOTES_PHOTOS_MAX)
        return out
    if name == "topic_seed":  # 소재 넣기 (주인 10/21 — 계약 action_topic_seed.json): {blog, text} · 링크는 Blog-Auto 가 지우고 열지 않음
        return {"blog": _blog(g("blog")), "text": _str(g("text"), SEED_MAX)}
    if name == "drop":
        return {"blog": _blog(g("blog")), "pids": _pids(g("pids") if g("pids") is not None else g("pid"))}
    if name in ("rewrite", "restyle"):
        return {"blog": "issue", "pids": _pids(g("pids") if g("pids") is not None else g("pid"), allow_all=name == "restyle")}
    if name in ("fixpic", "answer", "noanswer", "oedit_keep", "oedit_redo"):
        out = {"pid": _int(g("pid"))}
        if g("blog") is not None:
            out["blog"] = _blog(g("blog"))
        if name == "answer":
            out["text"] = _str(g("text"), 4000)
            if (per := _answer_photos(g("answers"))) is not None:
                out["answers"] = per
        return out
    if name in ("scan", "hotdeal_now", "kakao_auth_url", "kakao_test"):
        return {}
    if name == "pick":
        return {"sid": _str(g("sid"), 40), "n": _int(g("n"), 1, 50)}
    if name == "write":
        return {"text": _str(g("text"), 4000), "uploads": _uploads(g("uploads"), 0, 20)}
    if name == "issue":
        return {"keyword": _str(g("keyword"), 40, required=False)}
    if name == "mode":
        if g("mode") not in ("draft", "publish"):
            raise ActionError("bad_args")
        return {"blog": _blog(g("blog")), "mode": g("mode")}
    if name in ("pause", "resume", "drafts_cleanup", "login_check"):
        return {"blog": _blog(g("blog"))}
    if name == "server_check":  # 서버 점검 (주인): 고를 값이 없다 — 정해진 점검만, 명령에 끼어들 자리 없음
        return {}
    if name == "ledger":
        return {"uploads": _uploads(g("uploads") if g("uploads") is not None else [g("upload_id")], 1, 1)}
    if name == "info":
        if g("what") not in ("trends", "plan", "creator"):
            raise ActionError("bad_args")
        return {"what": g("what")}
    if name == "kakao_connect":
        url = _str(g("url"), URL_MAX)
        if not url.startswith("https://"):
            raise ActionError("bad_args")
        return {"url": url}
    if name == "dismiss":
        return {"key": _str(g("key"), 120)}
    if name == "schedule":  # 간격 두고 발행 (주인 10/18): 준 순서대로 그 블로그 예약 줄 끝에
        out = {"blog": _blog(g("blog")), "pids": _pids(g("pids") if g("pids") is not None else g("pid"))}
        if g("first_now") is True:
            out["first_now"] = True
        at = g("at")  # 확인 창에 보여 준 시각 (서버가 넣고 ctoken 이 묶음 — 확인 없는 일에서는 버림)
        if at is not None:
            if not isinstance(at, list) or len(at) != len(out["pids"]) or not all(
                    isinstance(x, int | float) and not isinstance(x, bool) and 1e9 < x < 1e10 for x in at):
                raise ActionError("bad_args")
            out["at"] = [round(float(x), 1) for x in at]
        return out
    if name == "unschedule":
        return {"blog": _blog(g("blog")), "pid": _int(g("pid"))}
    if name == "unschedule_all":
        return {"blog": _blog(g("blog"))}
    if name == "schedule_move":  # 예약 시간 바꾸기 (주인 10/19): 'HH:MM' + 오늘/내일 (시각 계산·규칙 확인은 Blog-Auto, KST)
        at, day = g("at"), g("day")
        if not isinstance(at, str) or not HHMM.match(at) or day not in ("today", "tomorrow"):
            raise ActionError("bad_args")
        return {"blog": _blog(g("blog")), "pid": _int(g("pid")), "at": at, "day": day}
    if name in ("pics_fetch", "pics_clear"):  # 링크에서 사진 고르기 (주인 10/18) — 글 하나(blog+pid) 또는 붙여 넣은 링크 하나
        out = {}
        if g("pid") is not None:
            out = {"blog": _blog(g("blog")), "pid": _int(g("pid"))}
        if name == "pics_clear":
            job = g("job")
            if job is not None:
                if not isinstance(job, str) or not PIC_JOB_ID.match(job):
                    raise ActionError("bad_args")
                out["job"] = job
            if not out:
                raise ActionError("bad_args")
            return out
        if g("url") not in (None, "") or not out:
            out["url"] = _pics_url(g("url"))
        want = _str(g("want"), PICS_WANT_MAX, required=False)
        if want:
            out["want"] = " ".join(want.split())
        return out
    if name == "similar_links":  # 비슷한 아이템 링크 (주인 10/20 — 계약 action_similar_links.json): 글 칸(text) 또는 links·notes
        if g("blog") not in (None, "issue"):
            raise ActionError("bad_args")
        extra: dict[str, dict] = {}
        if g("links") is not None:
            links, notes = g("links"), g("notes") if g("notes") is not None else []
            if not isinstance(links, list) or not isinstance(notes, list) or len(notes) > len(links):
                raise ActionError("bad_args")
            pairs = [(_similar_url(u), _str(n, SIMILAR_NOTE_MAX, required=False) if n != "" else "")
                     for u, n in zip(links, notes + [""] * (len(links) - len(notes)), strict=True)]
            for k, hi in (("ratings", 10), ("reviews", 10 ** 8)):  # 주인이 본 평점·후기 수 (선택 — 10/21 평점 기준, links 와 같은 순서)
                v = g(k)
                if v is not None and (not isinstance(v, list) or len(v) > len(links) or any(
                        x is not None and (isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= hi) for x in v)):
                    raise ActionError("bad_args")
                extra[k] = {pairs[i][0]: x for i, x in reversed(list(enumerate(v or []))) if x is not None}
        else:
            pairs = similar_lines(_str(g("text"), SIMILAR_TEXT_MAX))
        seen: dict[str, str] = {}
        for u, n in pairs:
            seen.setdefault(u, " ".join(n.split()))
        if not 1 <= len(seen) <= SIMILAR_LINKS_MAX:
            raise ActionError("bad_args")
        out = {"blog": "issue", "pid": _int(g("pid")), "links": list(seen)}
        if any(seen.values()):
            out["notes"] = list(seen.values())
        for k, m in extra.items():
            if m:
                out[k] = [m.get(u) for u in seen]
        return out
    if name == "product_feedback":  # 제품 테스트 (주인 10/21 — 계약 action_product_feedback.json)
        iid, v = g("item_id"), g("verdict")
        if v not in (("ok", "bad") if iid is not None else ("note", "done")) \
                or (iid is not None and (not isinstance(iid, str) or not PT_ITEM_ID.match(iid))):
            raise ActionError("bad_args")
        rs = g("reasons") if g("reasons") is not None else []
        if not isinstance(rs, list) or len(rs) > len(PT_REASONS) or any(r not in PT_REASONS for r in rs) or (rs and v != "bad"):
            raise ActionError("bad_args")
        note = " ".join(_str(g("note"), PT_NOTE_MAX * 2, required=False).split())
        if len(note) > PT_NOTE_MAX:
            raise ActionError("bad_args")
        out = {"blog": "issue", "pid": _int(g("pid"))}
        if iid is not None:
            out["item_id"] = iid
        out["verdict"] = v
        if rs:
            out["reasons"] = list(dict.fromkeys(rs))
        if note:
            out["note"] = note
        return out
    if name == "post_feedback":  # 👍/👎 (주인 10/20 — 계약 action_post_feedback.json): vote none = 지우기, reason 한 줄
        if g("vote") not in ("up", "down", "none"):
            raise ActionError("bad_args")
        out = {"blog": _blog(g("blog")), "pid": _int(g("pid")), "vote": g("vote")}
        reason = " ".join(_str(g("reason"), FEEDBACK_REASON_MAX * 2, required=False).split())
        if len(reason) > FEEDBACK_REASON_MAX:
            raise ActionError("bad_args")
        if reason:
            out["reason"] = reason
        return out
    if name == "photo_feedback":  # 사진 테스트 (주인 10/21 — 계약 action_photo_feedback.json): slot 없으면 글 전체 · done = 테스트 끝
        verdict, reasons = g("verdict"), g("reasons") if g("reasons") is not None else []
        if verdict not in ("ok", "bad", "done") or not isinstance(reasons, list) or len(reasons) > 12 \
                or not all(isinstance(r, str) and r in TRIAL_REASONS for r in reasons):
            raise ActionError("bad_args")
        out = {"blog": "issue", "pid": _int(g("pid"))}
        if g("slot") is not None:
            out["slot"] = _int(g("slot"), 1, 40)
        out.update(verdict=verdict, reasons=list(dict.fromkeys(reasons)) if verdict == "bad" else [])
        note = " ".join(_str(g("note"), TRIAL_NOTE_MAX * 2, required=False).split())
        if len(note) > TRIAL_NOTE_MAX or (verdict == "bad" and not out["reasons"] and not note):
            raise ActionError("bad_args")
        if note and verdict != "done":
            out["note"] = note
        return out
    if name == "learn_decide":  # 이번 주 개선안 ✅ 적용·❌ 빼기 (lp-) · 적용 규칙 지우기 (lr-) — 계약 action_learn_decide.json
        lid, d = g("id"), g("decision")
        if not isinstance(lid, str) or not LEARN_ID.match(lid) or d not in ("apply", "reject", "remove") \
                or (d == "remove") != lid.startswith("lr-"):
            raise ActionError("bad_args")
        return {"id": lid, "decision": d}
    if name == "threads_post":  # 스레드 글 (주인 10/21 — 계약 action_threads_post.json): {text, image?(올린 사진 id), at?('HH:MM' + day | epoch 초)}
        out = {"text": _str(g("text"), TH_TEXT_MAX)}
        if g("image") not in (None, ""):
            out["image"] = _uploads([g("image")], 1, 1)[0]
        at = g("at")
        if isinstance(at, str) and at:
            if not HHMM.match(at) or g("day") not in ("today", "tomorrow"):
                raise ActionError("bad_args")
            out.update(at=at, day=g("day"))
        elif isinstance(at, int | float) and not isinstance(at, bool):
            if not 1e9 < at < 1e10:
                raise ActionError("bad_args")
            out["at"] = round(float(at), 1)
        elif at not in (None, ""):
            raise ActionError("bad_args")
        return out
    if name == "threads_cancel":  # 스레드 예약 취소 — {id: th-xxxxxxxx}
        tid = g("id")
        if not isinstance(tid, str) or not TH_ITEM_ID.match(tid):
            raise ActionError("bad_args")
        return {"id": tid}
    if name == "threads_reply":  # 내 글에 달린 답글에 주인이 쓴 답 — {media_id, text} (AI 초안 없음)
        mid = g("media_id")
        if not isinstance(mid, str) or not TH_MEDIA_ID.match(mid):
            raise ActionError("bad_args")
        return {"media_id": mid, "text": _str(g("text"), TH_TEXT_MAX)}
    if name in ("x_posted", "x_drop", "x_redo"):  # X 초안 (주인 10/25) — {pid} 하나 (블로그는 데런픽뿐이라 안 받음)
        return {"pid": _int(g("pid"))}
    if name in ("lookbook_posted", "lookbook_drop", "lookbook_redo"):  # 룩북 릴스 (주인 10/25 — 계약 action_lookbook_posted.json)
        jid = g("id")
        if not isinstance(jid, str) or not LB_ID_RE.match(jid):
            raise ActionError("bad_args", text="룩북 릴스 번호가 맞지 않아요")
        return {"id": jid, "url": reel_url(g("url"))} if name == "lookbook_posted" else {"id": jid}
    if name == "lookbook_trial":  # 사진 시험 새로 만들기 (주인 10/26 — 계약 action_lookbook_trial.json): {} 또는 {base: 바탕 릴스 번호}
        base = g("base")
        if base in (None, ""):
            return {}
        if not isinstance(base, str) or not LB_ID_RE.fullmatch(base):
            raise ActionError("bad_args", text="룩북 릴스 번호가 맞지 않아요")
        return {"base": base}
    if name == "lookbook_style":  # 이 양식으로 정하기 (계약 action_lookbook_style.json): {style: mirror|studio}
        style = g("style")
        if not isinstance(style, str) or style not in LB_STYLES:
            raise ActionError("bad_args", text="사진 양식이 맞지 않아요")
        return {"style": style}
    if name == "visit_done":  # '오늘 들를 이웃 글' 했어요 (주인 10/18) — 블로그는 id 에서 (visit-<블로그>-<번호>)
        vid = g("id")
        if not isinstance(vid, str) or not (m := VISIT_ID.match(vid)):
            raise ActionError("bad_args")
        return {"blog": m.group(1), "id": vid}
    raise ActionError("bad_args")


def publish_blog(action: str, args: dict[str, Any]) -> str | None:
    """MAY_PUBLISH 일이 걸린 블로그 (그 블로그 모드를 보고 ★ 를 정한다). 아니면 None."""
    if action not in MAY_PUBLISH:
        return None
    blog = args.get("blog")
    return str(blog) if blog in BLOGS else FIXED_BLOG.get(action)


def needs_confirm(action: str, args: dict[str, Any], *, this_device: bool = True, publish_mode: bool = False) -> bool:
    """★ 확인 창이 필요한가. publish_mode: 그 일이 걸린 블로그가 자동 발행 모드(또는 모름)라 MAY_PUBLISH 일이 바로 공개되는 때."""
    if action in MAY_PUBLISH and publish_mode:
        return True
    if action == "blog.schedule":  # 여러 편(시각 목록을 보여 줌)·첫 글은 지금만 ★ — 한 편 예약은 언제든 취소할 수 있다
        return len(args.get("pids") or []) > 1 or args.get("first_now") is True
    if action not in CONFIRM:  # (blog.fix 처럼 원래 ★ 인 일은 임시저장 모드여도 ★)
        return False
    if action == "blog.mode":
        return args.get("mode") == "publish"
    if action == "push.unsubscribe":
        return not this_device
    return True


def target_of(action: str, args: dict[str, Any]) -> str:
    """그 일이 걸린 항목 (iid) — 같은 일 두 번 막기·기록에."""
    if action.startswith("deal.") and "qid" in args:
        return iidmod.deal_iid(args["qid"])
    blog, pid = args.get("blog"), args.get("pid")
    if isinstance(pid, int) and blog in BLOGS:
        return iidmod.post_iid(blog, pid)
    pids = args.get("pids")
    if isinstance(pids, list) and len(pids) == 1 and blog in BLOGS:
        return iidmod.post_iid(blog, pids[0])
    if action in ("todo.dismiss", "todo.hide"):
        return str(args.get("iid") or "")
    if action == "blog.visit_done":
        return str(args.get("id") or "")
    if action == "blog.learn_decide":
        return "learn"
    if action == "blog.threads_cancel":  # 스레드 글·통계 화면 (주인 10/21) — 줄마다 따로 (여러 줄을 잇달아 눌러도 '하는 중' 아님)
        return f"thpost-{args.get('id')}"
    if action == "blog.threads_reply":
        return f"thpost-r{args.get('media_id')}"
    if action == "blog.threads_post":  # 글마다 따로 (같은 글을 두 번 누르면 한 번 · 다른 글은 앞 글이 끝나기 전에도)
        key = json.dumps([args.get("text"), args.get("at"), args.get("day")], ensure_ascii=False)
        return "thpost-p" + hashlib.sha256(key.encode()).hexdigest()[:8]
    if action == "search.check":
        return "search"
    if action in ("blog.x_posted", "blog.x_drop", "blog.x_redo"):  # X 초안마다 따로 (글 줄과 겹치지 않게)
        return f"xdraft-{args.get('pid')}"
    if action in ("x.stats", "x.handles"):
        return "x"
    if action in ("blog.lookbook_trial", "blog.lookbook_style"):  # 사진 시험 — 한 번에 하나씩 (같은 단추 두 번 누름 막기)
        return "lookbook-" + action[len("blog.lookbook_"):]
    if action in LB_ACTIONS:  # 룩북 릴스마다 따로 (할 일 줄 lookbook-<id> 와 같은 이름 — 누른 뒤 그 줄이 '하는 중')
        return f"lookbook-{args.get('id')}"
    if action in ("blog.mode", "blog.pause", "blog.resume", "blog.drafts_cleanup", "blog.unschedule_all",
                  "blog.topic_seed") and blog in BLOGS:
        return f"blog-{blog}"
    if action == "blog.login_check" and blog in BLOGS:
        return f"conn-naver-{blog}"
    if action.startswith("blog.kakao"):
        return "conn-kakao"
    return ""


# ---------------------------------------------------------------- 확인 창 글 (지금 사실로)
def confirm_text(action: str, args: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    blog = BLOG_NAMES.get(str(args.get("blog") or ""), "")
    pids = args.get("pids")
    pid = args.get("pid") or (pids[0] if isinstance(pids, list) and pids else None)
    num = f" #{pid}" if pid else ""
    qid = args.get("qid")
    edited = bool(facts.get("owner_edited"))
    ed_note = " 직접 고친 글이라 바로 바뀌지 않고, 할 일에서 한 번 더 물어봐요."
    c = {
        "deal.skip": (f"#{qid} 딜을 안 올릴까요?", "채널에 올리지 않아요. 나중에 올리려면 새 딜로 직접 넣어야 해요.", "안 올리기"),
        "deal.repost": (f"#{qid} 딜을 다시 올릴까요?", "채널을 먼저 확인했나요? 이미 올라갔으면 두 번 올라가요.", "다시 올리기"),
        "deal.post": ("새 딜을 올릴까요?", "맨 앞에 넣고 차례가 되면 채널에 바로 올라가요.", "올리기"),
        "bot.pause": ("딜봇을 멈출까요?", "게시판 확인과 채널 올리기를 모두 멈춰요. '다시 시작'을 누르면 이어서 해요.", "멈추기"),
        "sns.test": (f"{'스레드' if args.get('sns') == 'threads' else '인스타'}에 테스트 글을 올릴까요?",
                     "공개 글로 올라가요. 확인한 뒤 앱에서 지워 주세요.", "올리기"),
        "sns.disconnect": ("연결을 끊을까요?", "끊으면 자동으로 올리지 않아요. 다시 연결해야 해요.", "끊기"),
        "blog.publish": (f"{blog}{num} 글을 발행할까요?", "네이버 블로그에 바로 올라가요. 발행 후에는 네이버 앱에서만 지울 수 있어요.", "발행"),
        "blog.drop": (f"{blog}{num} 글을 버릴까요?", "이 글은 올리지 않아요. 봇이 만든 임시저장 글만 지워요."
                      + (" 직접 고치신 임시저장 글은 네이버에 그대로 남아요." if edited else ""), "버리기"),
        "blog.fix": ((f"{blog}{num} 글을 말한 대로 고칠까요?", "고친 뒤 자동 발행 모드라 바로 네이버에 공개돼요."
                      + (ed_note if edited else ""), "고치고 발행") if facts.get("publish") else
                     (f"{blog}{num} 글을 말한 대로 고칠까요?", "지금 글을 고쳐 다시 저장해요." + (ed_note if edited else ""), "고치기")),
        "blog.rewrite": (f"Pick{num} 글을 다시 쓸까요?", "지금 글을 새로 써서 바꿔요." + (ed_note if edited else ""), "다시 쓰기"),
        "blog.restyle": (("Pick 글 전체 스타일을 다시 입힐까요?", "기다리는 Pick 글의 꾸밈을 새로 입혀요. 직접 고친 글은 건너뛰어요.", "스타일 다시")
                         if args.get("pids") == "all" else
                         (f"Pick{num} 글 스타일을 다시 입힐까요?", "글 꾸밈을 새로 입혀요." + (ed_note if edited else ""), "스타일 다시")),
        "blog.share": (f"{blog}{num} 글을 채널·카톡으로 보낼까요?", "텔레그램 채널과 카톡 오픈채팅에 이 글을 알려요.", "보내기"),
        "blog.channel": (f"{blog}{num} 글을 채널에 올릴까요?", "텔레그램 채널에 이 글 소개를 올려요.", "채널에 올리기"),
        "blog.oedit_redo": ("봇 글로 덮어쓸까요?", "직접 고치신 내용이 봇 글로 바뀌어요. 되돌릴 수 없어요.", "덮어쓰기"),
        "blog.mode": (f"{blog} 블로그를 발행 모드로 바꿀까요?", "앞으로 이 블로그 글은 임시저장 없이 바로 발행돼요.", "바꾸기"),
        "blog.pause": (f"{blog} 블로그를 멈출까요?", "새 글 쓰기와 발행을 멈춰요. '다시 시작'으로 이어서 해요.", "멈추기"),
        "blog.drafts_cleanup": (f"{blog} 임시저장 글을 정리할까요?", "봇이 만든 오래된 임시저장 글만 지워요. 직접 쓰신 글은 그대로 둬요.", "정리하기"),
        "blog.ledger": ("정리본을 바꿀까요?", "올린 파일로 아카이브 정리본을 바꿔요. 지금 정리본은 백업해 둬요.", "바꾸기"),
        "push.unsubscribe": (f"{facts.get('device') or '그 기기'} 알림을 끌까요?", "그 기기에는 알림이 더 가지 않아요.", "끄기"),
        "blog.schedule": (f"{blog} {len(pids or [])}편을 간격 두고 발행할까요?" if isinstance(pids, list) and len(pids) > 1
                          else f"{blog}{num} 글을 간격 두고 발행할까요?",
                          "2~3시간 간격으로 08~22시에 한 편씩 올라가요. 블로그 탭에서 언제든 취소할 수 있어요.", "예약하기"),
        "blog.unschedule_all": (f"{blog} 예약을 모두 취소할까요?", "예약된 글은 발행하지 않고 기다리는 글로 돌아가요.", "모두 취소"),
        "blog.threads_post": (("스레드 예약할까요?", "정한 시각에 공개 글로 올라가요. 그 전에는 취소할 수 있어요.", "예약하기")
                              if args.get("at") else
                              ("스레드에 올릴까요?", "바로 공개 글로 올라가요. 올린 뒤에는 스레드 앱에서만 지울 수 있어요.", "올리기")),
        "blog.threads_reply": ("이 답글에 답할까요?", "내 스레드 글 아래에 공개 답글로 바로 올라가요.", "답글 달기"),
        "blog.post_edit": (f"{blog}{num} 발행 글을 고칠까요?", "네이버에 공개된 글을 바로 고쳐요. 뺀 사진·문장은 되돌리기 어려워요."
                           " 카테고리·태그·공개 설정은 그대로예요.", "고치기"),
    }
    pub = "자동 발행 모드라 바로 네이버에 공개돼요."
    ph = "사진" if args.get("uploads") or not args.get("links") else "인스타 게시물"
    blog_p = BLOG_NAMES.get(publish_blog(action, args) or "", blog)
    c.update({
        "blog.retry": (f"{blog}{num} 글을 다시 올릴까요?", pub, "공개로 올리기"),
        "blog.photos": (f"{blog}{num} 글에 {ph}을 넣을까요?", f"{ph}을 넣고 " + pub, "넣고 발행"),
        "blog.photos_batch": (f"{blog}{num} 글 사진을 한 번에 바꿀까요?", "바꾼 사진을 넣고 " + pub, "바꾸고 발행"),
        "blog.fixpic": (f"{blog_p}{num} 글 사진을 바꿀까요?", "사진을 바꾸고 " + pub, "바꾸고 발행"),
        "blog.write": (f"{blog_p} 새 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.issue": (f"{blog_p} 새 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.pick": (f"이 글감으로 {blog_p} 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.hotdeal_now": (f"{blog_p} 글을 지금 한 편 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
    })
    title, body, ok = c.get(action, ("이대로 할까요?", "되돌리기 어려운 일이에요.", "하기"))
    return {"title": title, "body": body, "ok": ok, "danger": action in DANGER}


class ConfirmTokens:
    """확인 창 표 (메모리 — 5분, 한 번만). 같은 세션·같은 일·같은 args 에만 맞는다."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time) -> None:
        self.key = key
        self.clock = clock
        self._used: dict[str, float] = {}

    def _sig(self, session: str, action: str, args: dict[str, Any], ts: int) -> str:
        canon = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        mac = hmac.new(self.key, f"{session}|{action}|{canon}|{ts}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def issue(self, session: str, action: str, args: dict[str, Any]) -> str:
        ts = int(self.clock())
        return f"{ts}.{secrets.token_hex(4)}.{self._sig(session, action, args, ts)}"

    def check(self, token: Any, session: str, action: str, args: dict[str, Any]) -> bool:
        if not isinstance(token, str) or len(token) > 120:
            return False
        ts_raw, _, rest = token.partition(".")
        nonce, _, sig = rest.partition(".")
        if not ts_raw.isdigit() or not nonce or not sig:
            return False
        now = self.clock()
        for t in [t for t, at in self._used.items() if now - at > CTOKEN_S * 2]:
            self._used.pop(t, None)
        ts = int(ts_raw)
        if now - ts > CTOKEN_S or ts - now > 60 or token in self._used:
            return False
        if not hmac.compare_digest(sig.encode(), self._sig(session, action, args, ts).encode()):
            return False
        self._used[token] = now
        return True


class RateWindow:
    def __init__(self, per: int, window: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per = per
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> bool:
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.per:
            return False
        q.append(now)
        if len(self._hits) > 2048:
            self._hits.pop(next(iter(self._hits)))
        return True


# ---------------------------------------------------------------- 실행
class ActionDesk:
    """딜봇 쪽 일은 바로, 블로그 일은 Blog-Auto 에 맡긴다. bot = app.DealBot (가짜로 바꿔 끼울 수 있게 Any)."""

    def __init__(self, bot: Any, *, outbox: ActionsOut, uploads: Any, key: bytes, audit: Callable[..., None],
                 facts: Callable[[str], dict[str, Any]] | None = None, clock: Callable[[], float] = time.time,
                 test_rate: RateWindow | None = None) -> None:
        self.bot = bot
        self.outbox = outbox
        self.store: ActionStore = outbox.store
        self.uploads = uploads
        self.tokens = ConfirmTokens(key, clock=clock)
        self.audit = audit
        self.facts = facts or (lambda _iid: {})
        self.clock = clock
        self.rate = RateWindow(ACTION_RATE, 60.0)
        self.srv_cool = srvcheck.Cooldown(clock)  # 서버 점검 2분에 한 번 (모든 기기 합쳐서)
        self.test_rate = test_rate  # 테스트 알림 한도 (POST /api/push/test 와 같은 셈 — 기기가 없으면 카톡 한 통이라)

    # 바깥에서 부르는 하나
    async def run(self, body: dict[str, Any], caller: Caller) -> tuple[int, dict[str, Any]]:
        action = body.get("action")
        cid = body.get("cid") if isinstance(body.get("cid"), str) and CID_RE.match(body.get("cid") or "") else None
        if not isinstance(action, str) or not ACTION_RE.match(action) or not (
                action in DEAL_ACTIONS or (action.startswith("blog.") and action[5:] in BLOG_ACTIONS)):
            return 400, self._err("bad_args")
        try:
            args = clean_args(action, body.get("args") or {}, blog_ids=self._blog_ids())
        except ActionError as e:
            self._audit(caller, action, "", {}, e.code)
            return e.status, self._err(e.code, e.text)
        action, args = self._spaced(action, args)
        target = target_of(action, args)
        if cid is not None and (prev := self.store.by_cid(cid, self.clock() - CID_S)) is not None:
            return self._replay(prev)
        if not self.rate.hit(caller.session):
            return 429, self._err("rate")
        if action == "push.test" and self.test_rate is not None and not self.test_rate.hit(caller.session):
            return 429, self._err("rate")
        if not getattr(getattr(self.bot.settings, "dashboard", None), "actions_enabled", True):
            return 403, self._err("off")
        if action == "blog.server_check":
            return await self._server_check(action, args, target, caller, cid)
        if action.startswith("blog.") and (down := self._blog_down(action[5:], caller, target, args)) is not None:
            return down  # 확인 창을 띄우기 전에 (맡길 곳이 없는데 '발행할까요?'를 묻지 않게)
        this_device = True
        pub = self._publish_mode(action, args)
        need = needs_confirm(action, args, this_device=this_device, publish_mode=pub)
        if action == "blog.schedule" and not need:
            args.pop("at", None)  # 확인 창에서 보여 준 시각만 넘긴다 (한 편 예약은 Blog-Auto 가 정함)
        if need:
            if body.get("ctoken") is None and action == "blog.schedule":
                args, extra = self._plan(args, caller)
                tok = self.tokens.issue(caller.session, action, args)
                return 409, {"status": "confirm", "ctoken": tok, **confirm_text(action, args, {}), **extra, "args": args}
            if body.get("ctoken") is None:
                facts = self.facts(target) if target else {}
                if pub:
                    facts = {**facts, "publish": True}
                tok = self.tokens.issue(caller.session, action, args)
                return 409, {"status": "confirm", "ctoken": tok, **confirm_text(action, args, facts)}
            if not self.tokens.check(body.get("ctoken"), caller.session, action, args):
                return 409, {**self._err("confirm_expired"), "status": "confirm_expired"}
        if action.startswith("blog."):
            return self._queue(action, args, target, caller, cid, confirmed=need)
        return await self._deal(action, args, target, caller, cid)

    async def _server_check(self, action: str, args: dict[str, Any], target: str, caller: Caller,
                            cid: str | None) -> tuple[int, dict[str, Any]]:
        """서버 점검: 2분에 한 번. 블로그 프로그램 몫은 맡기고(결과를 읽을 때 딜봇 몫과 합침), 맡길 수 없으면 딜봇 몫만 바로."""
        if left := self.srv_cool.hit():
            self._audit(caller, action, target, args, "cooldown")
            return 429, self._err("cooldown", srvcheck.Cooldown.text(left))
        down = self._blog_down("server_check", caller, target, args)
        if down is None:
            got = self._queue(action, args, target, caller, cid)
            if got[0] != 202:
                self.srv_cool.last = 0.0  # 맡기지 못했으니 바로 다시 눌러도 됨
            return got
        why = "아직 이 점검을 몰라요 — 블로그 프로그램 업데이트가 필요해요" if down[1].get("code") == "unsupported" else "응답이 없어요"
        local = await asyncio.to_thread(srvcheck.local_lines, self.bot, with_memory=True)
        return 200, {"status": "ok", "code": "done", "text": srvcheck.compose(srvcheck.down_part(why), local), "sheet": True}

    def _publish_mode(self, action: str, args: dict[str, Any]) -> bool:
        """MAY_PUBLISH 일의 블로그가 자동 발행 모드인가 (모르면 True — 확인 창 한 번이 더 안전).
        Blog-Auto 도 실행할 때 모드를 다시 보고 confirmed 가 없으면 받지 않는다 (누른 뒤 모드가 바뀌어도).
        간격 두고만 발행하는 블로그(Pick)는 바로 공개되지 않고 예약 줄로 가니 False."""
        blog = publish_blog(action, args)
        if blog is None or self.spaced_only(blog):
            return False
        return self.modes().get(blog) != "draft"

    def schedules(self) -> dict[str, dict[str, Any]]:
        """블로그마다 간격 두고 발행 상태 (기록 파일 schedule.blogs — 예전 Blog-Auto 면 빈 사전)."""
        try:
            st = self.bot.dash_state
            export = st._export() if st is not None else None
            return dict(((getattr(export, "v2", None) or {}).get("schedule") or {}).get("blogs") or {})
        except Exception:  # noqa: BLE001
            return {}

    def spaced_only(self, blog: str) -> bool:
        return bool((self.schedules().get(blog) or {}).get("spaced_only"))

    def _spaced(self, action: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """간격 두고만 발행하는 블로그 (주인 10/18: Pick 은 무조건): '발행' 은 예약 줄 끝으로, '첫 글은 지금' 은 없음.
        Blog-Auto 도 같은 규칙으로 막는다 (bridge.x_publish · schedule.add) — 여기는 확인 창·화면을 맞추려고."""
        blog = str(args.get("blog") or "")
        if action == "blog.publish" and self.spaced_only(blog):
            return "blog.schedule", {"blog": blog, "pids": [args["pid"]]}
        if action == "blog.schedule" and self.spaced_only(blog):
            args = {k: v for k, v in args.items() if k != "first_now"}
        return action, args

    def _plan(self, args: dict[str, Any], caller: Caller) -> tuple[dict[str, Any], dict[str, Any]]:
        """여러 편 예약 확인 창: Blog-Auto 와 같은 규칙으로 미리 계산한 시각 (확인하면 이 시각 그대로 — args.at, ctoken 이 묶음).
        같은 세션·같은 글·같은 선택이면 10분 동안 같은 시각 ('첫 글은 지금' 을 켰다 꺼도 그대로)."""
        from dealbot.dashboard import schedule as sch

        blog, pids, first = args["blog"], list(args["pids"]), args.get("first_now") is True
        info = self.schedules().get(blog) or {}
        now = self.clock()
        tail, last = sch.iso_ts(info.get("tail_at")), sch.iso_ts(info.get("last_pub"))
        rng = sch.seeded(caller.session, blog, pids, first, tail, last, int(now // 600))
        times = sch.plan(len(pids), now=now, r=sch.rules_of(info), rng=rng, tail=tail, last_pub=last, first_now=first)
        titles = self._titles(blog)
        rows = [{"pid": p, "title": titles.get(p) or f"#{p}", "at": round(t, 1),
                 "when": "지금" if first and i == 0 and t <= now + 60 else sch.when_text(t, now)} for i, (p, t) in enumerate(zip(pids, times, strict=True))]
        # '첫 글은 지금' 은 지금 바로 올릴 수 있을 때만 보인다: 예약 줄이 비었고 · 마지막 발행에서 2시간이 지났고 · 08~22시 안
        # (아니면 켜도 시각이 같아 헷갈림). Pick 은 늘 간격 두고라 없음
        can_now = not self.spaced_only(blog) and sch.plan(1, now=now, r=sch.rules_of(info), rng=sch.seeded(now), tail=tail,
                                                           last_pub=last, first_now=True)[0] <= now + 60
        toggle = {"key": "first_now", "label": "첫 글은 지금", "on": first} if can_now else None
        return dict(args, at=[round(t, 1) for t in times]), {"plan": rows, "toggle": toggle}

    def _titles(self, blog: str) -> dict[int, str]:
        try:
            export = self.bot.dash_state._export()
            return {p["pid"]: p["title"] for p in ((getattr(export, "v2", None) or {}).get("posts") or {}).get(blog, [])}
        except Exception:  # noqa: BLE001
            return {}

    def modes(self) -> dict[str, str]:
        """블로그마다 지금 모드 (Blog-Auto 기록 파일 status.blogs.*.mode)."""
        try:
            st = self.bot.dash_state
            export = st._export() if st is not None else None
            blogs = ((getattr(export, "v2", None) or {}).get("status") or {}).get("blogs") or {}
            return {b: str((blogs.get(b) or {}).get("mode") or "") for b in BLOGS}
        except Exception:  # noqa: BLE001
            return {}

    def _blog_ids(self) -> dict[str, str]:
        try:
            return dict(getattr(self.bot, "blog_ids", lambda: {})())
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _err(code: str, text: str | None = None) -> dict[str, Any]:
        return {"status": "error", "code": code, "text": text or TEXTS.get(code, TEXTS["failed"])}

    def _replay(self, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        st = status_of(row)
        if row["action"].startswith("blog."):
            return 202, {"status": "queued", "id": row["id"]}
        return 200, {"status": "ok" if row["status"] == "ok" else "error", "code": st["code"], "text": st["text"],
                     "refresh": True, "id": row["id"]}

    def _audit(self, caller: Caller, action: str, target: str, args: dict[str, Any], code: str, aid: str | None = None) -> None:
        try:
            self.audit(session8=caller.session, client8=caller.client8, action=action, target=target,
                       args=redact(action, args), code=code, action_id=aid)
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard audit failed: %s", type(e).__name__)

    # ---------------------------------------------------------- 블로그 일 (Blog-Auto)
    def _blog_down(self, name: str, caller: Caller, target: str, args: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
        """Blog-Auto 에 맡길 수 없으면 503 (신호 없음·다리 없음·모르는 일)."""
        st = self.outbox.status()
        if not st.bridge_ok(self.clock()):
            self._audit(caller, f"blog.{name}", target, args, "blogauto_down")
            return 503, self._err("blogauto_down")
        if st.actions and not st.supports(name):
            self._audit(caller, f"blog.{name}", target, args, "unsupported")
            return 503, self._err("unsupported")
        return None

    def _queue(self, action: str, args: dict[str, Any], target: str, caller: Caller, cid: str | None, *,
               confirmed: bool = False) -> tuple[int, dict[str, Any]]:
        name = action[5:]
        if (down := self._blog_down(name, caller, target, args)) is not None:
            return down
        spool_args = dict(args)
        ups = args.get("uploads") or []
        if name == "post_edit":  # 바꿀 사진 id → 받은 파일 경로 (Blog-Auto 는 id 를 모른다)
            ops, ups = [], [o["file"] for o in args["ops"] if o.get("file")]
            for o in args["ops"]:
                p = self.uploads.path(o["file"], kind="photo") if o.get("file") else None
                if o.get("file") and p is None:
                    self._audit(caller, action, target, args, "bad_upload")
                    return 400, self._err("bad_upload")
                ops.append({**o, "file": str(p)} if p is not None else o)
            spool_args["ops"] = ops
        elif name == "photos_batch":  # 바꿀 사진마다 올린 사진 id → 경로 (Blog-Auto 는 id 를 모른다)
            ops, ups = [], [o["upload"] for o in args["ops"] if o.get("upload")]
            for o in args["ops"]:
                p = self.uploads.path(o["upload"], kind="photo") if o.get("upload") else None
                if o.get("upload") and p is None:
                    self._audit(caller, action, target, args, "bad_upload")
                    return 400, self._err("bad_upload")
                ops.append({**o, "upload": str(p)} if p is not None else o)
            spool_args["ops"] = ops
        elif name == "answer" and args.get("answers"):  # 질문마다 붙인 사진 → 그 자리에서 경로로 (Blog-Auto 는 id 를 모른다)
            per = []
            for x in args["answers"]:
                paths = [self.uploads.path(uid, kind="photo") for uid in x["uploads"]]
                if None in paths:
                    self._audit(caller, action, target, args, "bad_upload")
                    return 400, self._err("bad_upload")
                per.append({**x, "uploads": [str(p) for p in paths]})
                ups = [*ups, *x["uploads"]]
            spool_args["answers"] = per
        elif name == "threads_post" and args.get("image"):  # 붙인 사진 id → 경로 (Blog-Auto 는 id 를 모른다)
            ups = [args["image"]]
            p = self.uploads.path(args["image"], kind="photo")
            if p is None:
                self._audit(caller, action, target, args, "bad_upload")
                return 400, self._err("bad_upload")
            spool_args["image"] = str(p)
        elif name == "post_notes":  # 붙인 사진 id → 경로 (photos — Blog-Auto 는 id 를 모른다)
            ups = list(args.get("photos") or [])
            paths = [self.uploads.path(uid, kind="photo") for uid in ups]
            if None in paths:
                self._audit(caller, action, target, args, "bad_upload")
                return 400, self._err("bad_upload")
            spool_args["photos"] = [str(p) for p in paths]
        elif ups:
            kind = "ledger" if name == "ledger" else "photo"
            paths = []
            for uid in ups:
                p = self.uploads.path(uid, kind=kind)
                if p is None:
                    self._audit(caller, action, target, args, "bad_upload")
                    return 400, self._err("bad_upload")
                paths.append(str(p))
            if name == "ledger":  # Blog-Auto x_ledger 는 {"upload": 경로} 하나 (계약 §2.4 ledger: {upload})
                spool_args.pop("uploads", None)
                spool_args["upload"] = paths[0]
            else:
                spool_args["uploads"] = paths
        try:
            aid, existed = self.outbox.submit(action=name, blog=args.get("blog"), args=spool_args, target=target,
                                              confirmed=confirmed, session=caller.session, cid=cid)
        except ActionsBusy:  # 같은 글에 같은 일이 다른 값으로 아직 하는 중 → 올린 사진은 화면에 그대로 (다시 누르면 됨)
            self._audit(caller, action, target, args, "busy")
            return 409, self._err("busy")
        except (OSError, ValueError) as e:
            log.warning("dashboard action spool write failed: %s", type(e).__name__)
            self._audit(caller, action, target, args, "failed")
            return 503, self._err("failed")
        if ups and not existed:
            self.uploads.mark_used(ups, aid)
        self._audit(caller, action, target, args, "queued", aid)
        return 202, {"status": "queued", "id": aid}

    # ---------------------------------------------------------- 딜봇 일 (바로)
    async def _deal(self, action: str, args: dict[str, Any], target: str, caller: Caller, cid: str | None) -> tuple[int, dict[str, Any]]:
        router = getattr(self.bot, "router", None)
        quiet = router.quiet(target) if router is not None else contextlib.nullcontext()  # 그 줄에 열린 알림만 조용히
        try:
            with quiet:
                code, text = await self._do(action, args, caller)
        except ActionError as e:
            code, text = e.code, e.text
        except Exception as e:  # noqa: BLE001 — 한 일이 터져도 대시보드는 그대로
            log.exception("dashboard action %s failed", action)
            code, text = "failed", TEXTS["failed"] + f" ({type(e).__name__})"
        ok = code in OK_CODES
        now = self.clock()
        aid = spool.new_id("a", now)
        result = {"code": code, "text": safe_text(text, 120), "done_at": now}
        self.store.insert(aid=aid, ts=now, blog=None, action=action, target=target, args=args, status="ok" if ok else "error",
                          cid=cid, session=caller.session, result=result)
        self._audit(caller, action, target, args, code, aid)
        return 200, {"status": "ok" if ok else "error", "code": code, "text": result["text"], "refresh": True, "id": aid}

    async def _do(self, action: str, args: dict[str, Any], caller: Caller) -> tuple[str, str]:
        bot = self.bot
        db = bot.db
        if action == "deal.link":
            dash = bot.dashboard
            if dash is None:
                raise ActionError("off")
            if not dash.link_rate_ok():
                raise ActionError("link_busy", 429)
            photo = None
            if args.get("upload_id"):
                photo = self.uploads.deal_jpeg(args["upload_id"])
                if photo is None:
                    raise ActionError("bad_upload")
            code, note = await dash.owner_link(args["qid"], args["url"], photo=photo)
            text = TEXTS.get(code, TEXTS["failed"])
            if code == "linked_twin" and note:
                text = f"같은 상품이 #{note} 번으로 다시 줄 서 있어 #{note} 번으로 받아요"
            if code == "photo_refused" and note:
                text = f"{TEXTS['photo_refused']} ({truncate(note, 60)})"
            return code, text
        if action == "deal.skip":
            item = db.get_queue_item(args["qid"])
            if item is None:
                return "not_found", TEXTS["not_found"]
            reply = bot.skip_item(args["qid"])
            after = db.get_queue_item(args["qid"])
            return ("skipped", TEXTS["skipped"]) if after is not None and after.status == "skipped" else ("wrong_state", plain(reply))
        if action == "deal.nolink":
            code, text = bot.no_connect(args["qid"], post=args["name"] == "post")
            if code in ("nolink", "skipped"):
                try:
                    await bot.refresh_pending_notice()  # 텔레그램 고정 공지('내 링크 기다리는 글')에서 빠지게
                except Exception as e:  # noqa: BLE001 — 공지를 못 고쳐도 한 일은 됐다
                    log.warning("dashboard nolink: pending notice refresh failed: %s", type(e).__name__)
            return code, text or TEXTS.get(code, TEXTS["failed"])
        if action == "deal.sc_forget":
            if not bot.sc_store_forget(args["name"]):
                return "not_found", TEXTS["not_found"]
            return "done", "목록에서 뺐어요 — 다음 딜부터 다시 링크를 물어요"
        if action in ("deal.ok", "deal.ok_text"):
            item = db.get_queue_item(args["qid"])
            if item is None:
                return "not_found", TEXTS["not_found"]
            if item.status != "awaiting_approval":
                return "wrong_state", TEXTS["wrong_state"]
            reply = bot.approve_item(args["qid"], args.get("text"))
            after = db.get_queue_item(args["qid"])
            ok = after is not None and after.status == "pending"
            return ("approved", TEXTS["approved"]) if ok else ("wrong_state", plain(reply))
        if action == "deal.posted":
            if db.get_queue_item(args["qid"]) is None:
                return "not_found", TEXTS["not_found"]
            reply = await bot.mark_posted(args["qid"], args.get("url") or "")
            after = db.get_queue_item(args["qid"])
            return ("posted", TEXTS["posted"]) if after is not None and after.status == "published" else ("wrong_state", plain(reply))
        if action == "deal.repost":
            code, text = await bot.repost_unsure(args["qid"])
            return code, text or TEXTS.get(code, TEXTS["failed"])
        if action == "deal.late_photo":
            if not bot.late_photo_waiting(args["qid"]):
                return "wrong_state", "지금은 사진을 기다리는 딜이 아니에요"
            jpeg = self.uploads.deal_jpeg(args["uploads"][0])
            if jpeg is None:
                raise ActionError("bad_upload")
            reply = await bot.attach_late_photo(args["qid"], jpeg)
            ok = plain(reply).startswith("✅")
            return ("done" if ok else "photo_refused"), plain(reply)
        if action == "deal.post":
            reply = await bot.submit_manual(args["text"])
            return ("queued", plain(reply)) if plain(reply).startswith("📝") else ("bad_args", plain(reply))
        if action == "bot.pause":
            bot.pause()
            return "paused", TEXTS["paused"]
        if action == "bot.resume":
            bot.resume()
            return "resumed", TEXTS["resumed"]
        if action == "bot.run":
            name = args.get("name")
            if name is not None and name not in bot.collector_names():
                return "bad_args", TEXTS["bad_args"]
            bot.request_run(name)
            return "run", TEXTS["run"]
        if action in ("sns.token", "sns.code"):
            field = "token" if action == "sns.token" else "code"
            if args["sns"] == "threads":
                fn = bot.threads_submit_token if field == "token" else bot.threads_submit_code
                reply = plain(await fn(args[field]))
            else:  # 인스타: instagram = 데미얌의 쇼핑일기 · instagram_look = 데런픽 룩 (주인 10/26)
                fn = bot.instagram_submit_token if field == "token" else bot.instagram_submit_code
                acct = "look" if args["sns"] == "instagram_look" else "main"
                reply = plain(await (fn(args[field], account=acct) if acct != "main" else fn(args[field])))
            return ("connected" if reply.startswith("✅") else "failed"), reply
        if action == "sns.test":
            fn = bot.threads_test if args["sns"] == "threads" else bot.instagram_test
            reply = await fn(args.get("qid"))
            ok = "✅" in reply
            lines = [plain(ln) for ln in reply.splitlines() if ("✅" in ln or "❌" in ln)]
            return ("done" if ok else "failed"), (lines[0] if lines else plain(reply))
        if action == "sns.invites":
            reply = await bot.invite_links()
            return ("done" if "t.me/" in reply else "failed"), "초대 링크를 새로 만들었어요" if "t.me/" in reply else plain(reply)
        if action == "push.test":
            router = getattr(bot, "router", None)
            if router is None:
                raise ActionError("off")
            got = await router.push_test()
            if got["sent"]:
                return "pushed", f"테스트 알림을 {got['sent']}대에 보냈어요"
            if got.get("kakao"):
                return "no_device", TEXTS["no_device"]
            return "failed", "알림을 보내지 못했어요 — 기기에서 알림을 다시 켜 주세요"
        if action == "todo.dismiss":
            return self._dismiss(args, caller)
        if action == "todo.hide":
            return self._hide(args)
        if action == "login.remote":
            from dealbot.dashboard import remote_login

            s = remote_login.refresh(self.bot.db)
            if s and remote_login.live(s):  # 화면이 떠 있거나 띄우는 중 — 로그인 화면을 거듭 띄우지 않는다 (주인 10/26)
                if s.get("target") == args["target"]:
                    return "done", "이미 원격 화면이 열려 있어요 — 할 일 맨 위 '원격 화면 열기'를 눌러 주세요"
                # 서버 화면은 한 번에 하나 (127.0.0.1:6080) — 다른 대상을 띄우면 열린 화면의 비밀번호가 바뀌어 들어갈 수 없게 됨
                name = remote_login.TARGETS.get(s.get("target"), s.get("target"))
                return "busy", f"지금 {name} 원격 화면이 열려 있어요 — 그 화면이 닫힌 뒤(로그인하면 저절로 닫혀요) 다시 눌러 주세요"
            remote_login.start(self.bot.db, self.bot.relay_to_blog, args["target"])
            return "done", "서버에 로그인 화면을 띄우는 중이에요 — 30초쯤 뒤 할 일 맨 위 '원격 화면 열기'를 눌러 주세요"
        if action == "login.qr":
            from dealbot.dashboard import logins

            return await logins.start_qr(self.bot)
        if action == "stats.refresh":
            dash = bot.dashboard
            got = await dash.refresh(args["src"])
            return ("done" if got in ("refreshed", "reread") else got), {"refreshed": "다시 읽었어요", "reread": "다시 읽었어요"}.get(
                got, TEXTS.get(got, "읽지 못했어요"))
        if action == "search.check":
            st = bot.dash_state
            if st is None:
                raise ActionError("off")
            n = st.search_reg().check(args["urls"], args["done"])
            if not n:
                return "not_found", TEXTS["not_found"]
            what = f"{n}개를 " if n > 1 else ""
            return "done", f"{what}등록함으로 표시했어요" if args["done"] else f"{what}등록 안 함으로 되돌렸어요"
        if action in ("earn.save", "earn.delete"):
            return self._earn(action, args)
        if action in ("x.stats", "x.handles"):
            return self._x(action, args)
        if action == "stats.input":
            dash = bot.dashboard
            query = dict(args["values"])
            if args.get("date"):
                query["date"] = args["date"]
            got = dash.save_manual(args["src"], query)
            return ("done" if got == "saved" else got), ("저장했어요" if got == "saved" else TEXTS.get(got, TEXTS["bad_args"]))
        raise ActionError("bad_args")

    def _earn(self, action: str, args: dict[str, Any]) -> tuple[str, str]:
        """제휴 수익 직접 넣기·고치기·지우기 (봇 DB earn_manual — 밖으로 나가는 것 없음)."""
        store = getattr(self.bot, "earn_store", None)
        if store is None:
            raise ActionError("off")
        if action == "earn.delete":
            return ("done", "지웠어요") if store.delete_manual(args["id"]) else ("not_found", TEXTS["not_found"])
        today = datetime.fromtimestamp(self.clock(), ZoneInfo(self.bot.settings.app.timezone)).date()
        try:
            row = store.clean_manual(args, today)
        except EarnError as e:
            return "bad_args", str(e)
        now = datetime.fromtimestamp(self.clock(), UTC).isoformat(timespec="seconds")
        if "id" in args:
            return ("done", "고쳤어요") if store.update_manual(args["id"], row, now) else ("not_found", TEXTS["not_found"])
        try:
            store.add_manual(row, now)
        except EarnError as e:
            return "bad_args", str(e)
        return "done", "넣었어요"

    def _x(self, action: str, args: dict[str, Any]) -> tuple[str, str]:
        """X 진행률 숫자 저장 (최근 X_STATS_KEEP 번) · 계정 이름 저장 — 봇 DB 만 (밖으로 나가는 것 없음)."""
        db = self.bot.db
        if action == "x.handles":
            db.kv_set(X_HANDLES_KEY, json.dumps(args, ensure_ascii=False))
            return "done", "계정 이름을 저장했어요"
        rows = x_stats_rows(db)
        rows.append({"at": round(self.clock(), 1), **args})
        db.kv_set(X_STATS_KEY, json.dumps(rows[-X_STATS_KEEP:], ensure_ascii=False))
        return "done", "X 숫자를 저장했어요"

    def _hide(self, args: dict[str, Any]) -> tuple[str, str]:
        """알림 없애기: 할 일 화면에서만 뺀다 (딜·글·알림 자체는 건드리지 않음 — 같은 줄이 새로 열리면 다시 보임)."""
        db = self.bot.db
        now = self.clock()
        rows = hidden_rows(db, now)
        rows[args["iid"]] = {"since": args["since"], "at": now}
        if len(rows) > HIDDEN_MAX:
            rows = dict(sorted(rows.items(), key=lambda kv: kv[1]["at"])[-HIDDEN_MAX:])
        db.kv_set(HIDDEN_KEY, json.dumps(rows, ensure_ascii=False))
        return "done", "할 일에서 없앴어요"

    def _dismiss(self, args: dict[str, Any], caller: Caller) -> tuple[str, str]:
        """다 했어요: 밖에서 끝낸 일만 (두 번 발행·구매 링크 빠짐·서로이웃·멈춤·스레드 답글·시스템 참고)."""
        iid, key = args["iid"], args["key"]
        bot = self.bot
        router = getattr(bot, "router", None)
        row = router.store.get("dealbot", key) if router is not None and key else None
        dealbot_row = row is not None and row.iid == iid
        if not dismissible(iid, key, dealbot_row=dealbot_row):
            raise ActionError("not_dismissible")
        if iid == "sns-replies":
            if router is not None:
                router.store.replies_done(self.clock())
                router.resolve("dealbot", [REPLIES_KEY])
            return "done", "다 했어요"
        if dealbot_row:
            router.resolve("dealbot", [key])
            return "done", "다 했어요"
        # Blog-Auto 키 (알림 또는 Blog-Auto 가 셈한 줄) → Blog-Auto 가 닫는다. 여기까지 오는 건 Blog-Auto 가 닫아 주는 머리뿐
        # (dismissible — BLOGAUTO_DISMISS_HEADS) 이라 화면 줄은 바로 뺀다
        code, body = self._queue("blog.dismiss", {"key": key}, iid, caller, None)
        if code == 202:
            if router is not None:
                router.resolve("blogauto", [key])  # 화면에서 바로 빠지게 (Blog-Auto 도 곧 같은 키를 닫음)
            return "done", "다 했어요"
        return str(body.get("code") or "failed"), str(body.get("text") or TEXTS["failed"])


OK_CODES = frozenset({"done", "linked", "linked_twin", "nolink", "approved", "skipped", "posted", "queued", "paused", "resumed", "run",
                      "connected", "pushed", "no_device"})
