"""관리자 개인 챗: 알림(발행/실패/에러/수동 링크 요청/일일 요약) + 명령어(/status /link /post ...)."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import html
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from dealbot import __version__
from dealbot.config import MonitoringConfig, Settings
from dealbot.links import LinkRouter
from dealbot.media.imagecheck import (
    OWNER_PHOTO_MAX_BYTES,
    OWNER_PHOTO_NOT_IMAGE,
    OWNER_PHOTO_TOO_BIG,
)
from dealbot.models import Deal, Product, PublishResult
from dealbot.monitoring import nl as nlmod
from dealbot.monitoring.push import PushNotifier
from dealbot.monitoring.state import BotState
from dealbot.page_price import PageCheck, hold_line
from dealbot.pricing.market import market_name
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import normalize_chat_id
from dealbot.publisher.templates import TemplateRenderer
from dealbot.publisher.threads import KV_TOKEN, KV_TOKEN_EXPIRES, KV_USER_ID, KV_USERNAME
from dealbot.shops import Shop, ShopRegistry, find_urls
from dealbot.storage.db import Database, PeriodSummary, QueueItem
from dealbot.utils.text import truncate
from dealbot.utils.timeutil import fmt_local, from_iso, humanize_delta, utcnow

log = logging.getLogger(__name__)
_QUEUE_REF_RE = re.compile(r"#(\d+)")
REVIEW_MARK = "📝"  # 정보 글 확인 요청 메시지의 첫 글자 (답장 판별용)
LINK_USAGE = "이렇게 보내주세요: <code>/link 12 https://...</code> (번호는 링크 요청 메시지의 #번호)"
PHOTO_NOT_RECEIVED = "사진을 받지 못했어요 (텔레그램 오류) — 잠시 뒤 다시 보내 주세요"
OWNER_PHOTO_MIMES = ("image/jpeg", "image/png", "image/webp")  # 파일로 보낸 사진 (HEIC·GIF·동영상은 안 받음)
# 링크와 같이 온 상품 사진: 바이트, 또는 받아 오는 함수 (받은 바이트 또는 None, 못 받은 이유). 함수면 attach_link 가 링크를
# 붙일 수 있는 네이버 상품 딜인 걸 확인한 뒤에만 부른다 — 못 붙일 링크·안 쓸 사진 때문에 받거나 거절하지 않게 (검토 지적)
PhotoSource = bytes | Callable[[], Awaitable[tuple[bytes | None, str]]]
# 사진만 답장하면(설명 칸 비움) 붙일 링크가 없다 — 링크 요청·링크 완료 메시지에 답장한 사진이면 한 줄 알려 준다 (사진이 조용히 버려지지 않게)
PHOTO_ONLY_REPLY = ("📸 #{q} 사진만 받았어요 — 링크를 사진 설명 칸에 같이 넣어 다시 보내 주세요 "
                    "(사진과 링크를 따로 보내면 사진은 쓰지 않아요)")
# 고정 공지에 한 번에 답할 때 한 줄 형식: "675 https://링크" / "677 ok" / "673 skip"
_BATCH_LINE_RE = re.compile(r"^\s*#?(\d+)\s*[:：.)]?\s+(https?://\S+|ok|okay|확인|승인|ㅇㅋ|skip|스킵|패스|건너뛰기|x)\s*$", re.I)
_BATCH_OK = {"ok", "okay", "확인", "승인", "ㅇㅋ"}
_TAG_RE = re.compile(r"<[^>]+>")

TELEGRAM_TEXT_LIMIT = 4096
# 한도보다 넉넉히 작게 잘라야 태그가 중간에 끊기지 않는다
SAFE_CHUNK = 3500
# 연속으로 보낼 때 최소 간격. 너무 빨리 보내면 텔레그램이 Peer_flood 로 잠시 막는다
SEND_GAP_SECONDS = 0.4
FLOOD_RETRIES = 3
SEND_FAILED = object()  # 전송 자체가 실패했음을 뜻하는 표시 (텔레그램이 None 을 돌려주는 경우와 구분)


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _code_version(root: Path = _REPO_ROOT) -> tuple[str, str]:
    """지금 돌고 있는 코드의 git 커밋: (짧은 커밋, '짧은 커밋 MM/DD HH:MM'). 프로세스가 시작할 때 한 번만 읽는다 —
    서버가 새 코드를 받아 놓고 재시작을 못 했으면 /status 에 옛 커밋이 그대로 보여서 바로 알 수 있다.
    git 체크아웃이 아니면(도커) 배포 환경변수의 커밋, 그것도 없으면 ('?', '')."""
    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%h %cd", "--date=format-local:%m/%d %H:%M"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out:
            return out.split(" ", 1)[0], out
    sha = (os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_SHA") or "").strip()[:7]
    return (sha, sha) if sha else ("?", "")


CODE_SHA, CODE_VERSION = _code_version()


def version_label() -> str:
    """'0.1.0 · 4ccfbc1 10/07 11:05' — 커밋을 모르면 '0.1.0' 만."""
    return f"{__version__} · {CODE_VERSION}" if CODE_VERSION else __version__


def split_message(text: str, limit: int = SAFE_CHUNK) -> list[str]:
    """텔레그램 길이 제한에 맞춰 줄 단위로 나눈다.

    글자 수로 그냥 자르면 <b> 같은 태그나 &amp; 중간이 끊겨 텔레그램이 메시지를 통째로 거부한다
    (그러면 답이 아예 안 온다). 그래서 항상 줄 경계에서 나눈다.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for raw in text.split("\n"):
        line = raw
        while len(line) > limit:  # 한 줄 자체가 너무 길면 그 줄만 어쩔 수 없이 자른다
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        if size + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()] or [text[:limit]]


async def send_one(send: Any, text: str, **kwargs: Any) -> Any:
    """한 조각 전송. 텔레그램이 기다리라고 하면 기다렸다 다시, 서식을 거부하면 평문으로."""
    for attempt in range(FLOOD_RETRIES):
        try:
            return await send(text=text, parse_mode=ParseMode.HTML, **kwargs)
        except RetryAfter as e:
            wait = min(float(getattr(e, "retry_after", 5)) + 1, 60)
            log.warning("텔레그램이 %.0f초 기다리라고 합니다 — 기다렸다 다시 보냅니다", wait)
            await asyncio.sleep(wait)
        except BadRequest as e:
            if "flood" in str(e).lower():
                wait = 5 * (attempt + 1)
                log.warning("텔레그램 전송 제한 (%s) — %d초 뒤 다시 보냅니다", e, wait)
                await asyncio.sleep(wait)
                continue
            log.warning("HTML 서식이 거부됨 (%s) — 평문으로 보냅니다", e)
            return await send(text=strip_html(text)[:TELEGRAM_TEXT_LIMIT], **kwargs)
    log.error("전송 제한이 풀리지 않아 이 메시지는 보내지 못했습니다: %s", text[:80])
    return SEND_FAILED


async def send_chunks(
    send: Any, text: str, *, gap: float = SEND_GAP_SECONDS, on_sent: Callable[[Any], None] | None = None, **kwargs: Any
) -> Any:
    """긴 글을 줄 단위로 나눠, 사이에 간격을 두고 보낸다. 첫 메시지를 돌려준다.
    on_sent: 보낸 조각마다 불림 (보낸 메시지 기록 — 긴 보고의 가운데 조각에 답장해도 어느 메시지인지 알게)."""
    first = SEND_FAILED
    for i, chunk in enumerate(split_message(text)):
        if i:
            await asyncio.sleep(gap)
        msg = await send_one(send, chunk, **kwargs)
        if on_sent is not None and msg is not SEND_FAILED and msg is not None:
            on_sent(msg)
        if first is SEND_FAILED:
            first = msg
    return first


def strip_html(text: str) -> str:
    """HTML 서식이 깨져 전송이 거부될 때 쓰는 평문 변환."""
    plain = _TAG_RE.sub("", text)
    return plain.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&")
STALE_COMMAND_MAX_AGE = timedelta(minutes=5)  # 봇이 꺼져 있던 동안 쌓인 명령 중 이보다 오래된 것은 무시
# (배포 중 답이 없어 같은 명령을 여러 번 보내는 일이 잦다. 오래된 것까지 한꺼번에 처리하면 flood 에 걸린다)
SECRET_COMMANDS = ("/threadstoken", "/instatoken")  # 뒤에 토큰이 붙는 명령: 오래돼 실행하지 않아도 메시지는 지운다


# 수집기(출처) 코드명 → 관리자 챗 표시명. config.yaml 의 collectors[].label 이 있으면 그쪽이 우선
COLLECTOR_LABELS = {
    "ppomppu": "뽐뿌",
    "ruliweb_user": "루리웹 유저 핫딜",
    "ruliweb_biz": "루리웹 업체 핫딜",
    "goldbox": "쿠팡 골드박스",
    "category_best": "쿠팡 카테고리 베스트",
    "algumon": "알구몬",
    "adpick": "애드픽",
    "manual": "내가 직접 올림",
}

_REASON_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^few_reviews<(\d+)$"), "후기 {0}개 미만이라 제외"),
    (re.compile(r"^food_below_ref<(\d+)%$"), "식품은 평소 가격 대비 {0}% 이상 싸야 해서 제외"),
    (re.compile(r"^info_post$"), "정보 글(상품 링크 없는 게시판 글)"),
    (re.compile(r"^interest:recommend>=(\d+)$"), "관심도 통과(추천 {0}개 이상)"),
    (re.compile(r"^interest:comments>=(\d+)$"), "관심도 통과(댓글 {0}개 이상)"),
    (re.compile(r"^interest:views>=(\d+)$"), "관심도 통과(조회 {0}회 이상)"),
    (re.compile(r"^interest:rank<=(\d+)$"), "관심도 통과(순위 {0}위 안)"),
    (re.compile(r"^below_coupang_price>=([\d.]+)%$"), "쿠팡 최저가보다 {0}% 이상 쌈"),
    (re.compile(r"^below_naver_price>=([\d.]+)%$"), "네이버 최저가보다 {0}% 이상 쌈"),
    (re.compile(r"^below_(\w+)_price>=([\d.]+)%$"), "{0} 최저가보다 {1}% 이상 쌈"),
    (re.compile(r"^below_(\d+)d_avg>=([\d.]+)%$"), "최근 {0}일 평균가보다 {1}% 이상 쌈"),
    (re.compile(r"^(\d+)d_low&below_avg>=([\d.]+)%$"), "최근 {0}일 중 제일 싸고 평균가보다 {1}% 이상 쌈"),
    (re.compile(r"^recommend>=(\d+)$"), "커뮤니티 추천 {0}개 이상"),
    (re.compile(r"^discount_rate>=([\d.]+)%$"), "표시 할인율 {0}% 이상"),
    (re.compile(r"^market_diff=([-\d.]+)%$"), "쿠팡보다 {0}% 쌈(기준 미만)"),
    (re.compile(r"^naver_diff=([-\d.]+)%$"), "네이버 최저가보다 {0}% 쌈(기준 미만)"),
    (re.compile(r"^market_shipping_unknown$"), "배송비를 몰라 쿠팡 대조 보류"),
    (re.compile(r"^manual$"), "내가 직접 올림"),
    (re.compile(r"^discount_unconfirmed$"), "할인율만 있고 쿠팡 대조로 확인 안 됨"),
]


_KIND_LABELS = {
    "publish": "채널에 올리기",
    "publisher": "발행 작업",
    "summary": "일일 요약",
    "maintenance": "정리 작업",
    "threads": "스레드 게시",
    "commentary": "AI 한줄평",
    "control": "관리자 조작",
    "lifecycle": "시작/종료",
}


def describe_kind(kind: str, labels: dict[str, str] | None = None) -> str:
    """에러 종류 코드 → 사람 말 ("collector:ppomppu" → "뽐뿌 핫딜 확인 중")."""
    if kind.startswith("collector:"):
        name = kind.split(":", 1)[1]
        label = (labels or {}).get(name) or COLLECTOR_LABELS.get(name, name)
        return f"{label} 확인 중"
    return _KIND_LABELS.get(kind, kind)


def humanize_reason(code: str) -> str:
    for rx, fmt in _REASON_RULES:
        m = rx.match(code)
        if m:
            return fmt.format(*m.groups())
    return code


def humanize_reasons(reasons: list[str]) -> str:
    return " · ".join(humanize_reason(r) for r in reasons) or "-"


def heartbeat_due(last_activity: datetime, now: datetime, minutes: int) -> bool:
    """관리자 챗이 minutes 동안 조용했으면 True (특가/링크/에러 알림이 있었으면 그것으로 생존 신고를 대신한다)."""
    return minutes > 0 and now - last_activity >= timedelta(minutes=minutes)


# 텔레그램 "/" 메뉴에 등록할 명령 (이름은 영문 소문자·숫자·밑줄만 가능 — 텔레그램 규칙)
BLOG_RELAY_COMMANDS = ("blog", "blognow", "blogmode", "blogpause", "blogresume", "blogposts", "blogpublish",
                       "blogretry", "blogdrafts", "blogstatus", "bloglogin", "blogsubmit", "kakao")

BOT_COMMANDS: list[tuple[str, str]] = [
    ("status", "지금 상태 (수집기·발행·대기열)"),
    ("queue", "발행 대기열"),
    ("pending", "내 링크가 필요한 항목"),
    ("recent", "최근 발행 목록"),
    ("hot", "추천 많이 받은 게시판 글과 원문 링크 (/hot 5)"),
    ("find", "수집된 글 검색 (/find 키워드)"),
    ("errors", "최근 에러"),
    ("run", "지금 바로 수집 (/run 수집기이름)"),
    ("pause", "발행 일시정지"),
    ("resume", "발행 재개"),
    ("post", "직접 딜 올리기"),
    ("link", "링크 요청에 답: 만든 제휴 링크 붙이기 (/link 번호 링크)"),
    ("skip", "그 글은 올리지 않기 (/skip 번호)"),
    ("posted", "올라갔는지 모르는 글이 채널에 있음 (/posted 번호 [채널 글 링크])"),
    ("ok", "확인 요청에 답: 정품 확인·정보 글 그대로 올리기 (/ok 번호)"),
    ("copy", "올린 글의 카카오·블로그 복붙 문구 (/copy 번호)"),
    ("blog", "블로그(demiyum) 글 미리보기 — 올리지 않음"),
    ("blognow", "블로그: 지금 모인 딜로 모음글 1편"),
    ("blogmode", "블로그: 임시저장/자동발행 (/blogmode publish)"),
    ("blogpause", "블로그 자동 작업 멈춤"),
    ("blogresume", "블로그 자동 작업 재개"),
    ("blogposts", "블로그 최근 글 목록"),
    ("blogpublish", "블로그: 저장된 글 발행 (/blogpublish 번호)"),
    ("blogretry", "블로그: 실패한 글 다시 올리기 (/blogretry 번호)"),
    ("blogdrafts", "블로그 임시저장함 정리"),
    ("blogstatus", "블로그 상태"),
    ("bloglogin", "블로그 네이버 로그인 확인"),
    ("blogsubmit", "블로그 검색 등록 기록 (/blogsubmit 번호 = 다시)"),
    ("kakao", "카카오톡 나에게 보내기 연결 (/kakao test)"),
    ("test", "채널에 올라갈 글 양식 미리 보기 (샘플)"),
    ("threadstest", "스레드에 샘플 글 올려 보기 (번호 주면 그 글)"),
    ("pushtest", "휴대폰 푸시(ntfy) 연결 확인"),
    ("ppstats", "커뮤니티 글 추천 분포"),
    ("threadsauth", "스레드 연결 (최초 1회)"),
    ("threadscode", "스레드 인증 코드 입력"),
    ("threadstoken", "스레드 연결: 개발자 화면에서 만든 토큰으로 (/threadstoken 토큰)"),
    ("instaauth", "인스타그램 연결 (최초 1회)"),
    ("instacode", "인스타그램 인증 코드 입력"),
    ("instatoken", "인스타그램 연결: 개발자 화면에서 만든 토큰으로 (/instatoken 토큰)"),
    ("instatest", "인스타그램에 샘플 글 올려 보기 (번호 주면 그 글)"),
    ("channelguide", "채널에 안내 글(인스타·스레드·블로그 링크)을 올리고 고정"),
    ("invitelinks", "채널 초대 링크 만들기·보기 (인스타·스레드 프로필·핫딜 페이지·블로그·QR)"),
    ("help", "도움말"),
]


def naver_search_help(name: str, url: str, shop_key: str) -> str:
    """네이버 쇼핑 커넥트는 상품 주소가 아니라 상품명·스토어명으로 찾는다 → 눌러서 복사할 검색어를 같이 준다."""
    if shop_key != "naver":
        return ""
    import re

    from dealbot.utils.text import clean_name

    store = re.search(r"(?:smartstore|brand)\.naver\.com/([^/?#]+)", url or "")
    query = truncate(clean_name(name), 40)
    return ("\n🔎 쇼핑 커넥트 '상품 찾기' 검색어 (눌러서 복사)\n"
            f"<code>{html.escape(query)}</code>\n"
            + (f"스토어: <code>{html.escape(store.group(1))}</code>\n" if store else "")
            + "바로가기: https://brandconnect.naver.com/\n")


def photo_ask(p: Product) -> str:
    """네이버 딜 링크 요청의 한 줄 (주인 결정 10/15): 휴대폰으로 가격·옵션을 본 김에 상품 사진도 같이 — 그 사진이 이 딜의 상품 사진.
    가격은 글 제목의 가격 (페이지 가격으로 바꿔 둔 뒤에도 제목 값, app._title_price 와 같음)."""
    price = f" — 제목 가격 {int(p.extra.get('title_price') or p.price):,}원" if p.has_price else ""
    return f"📸 가격·옵션 확인 후 사진도 같이 보내 주세요{price}"


def wants_owner_photo(shop_key: str, p: Product) -> bool:
    """링크와 같이 보낸 사진을 상품 사진으로 쓰는 딜: 네이버 상품 딜만 (쿠팡·토스 등은 지금처럼)."""
    return shop_key == "naver" and p.deal_kind != "info"


def photo_refused(queue_id: int, why: str) -> str:
    """주인이 링크와 같이 보낸 사진을 못 씀: 링크도 붙이지 않고 이유와 다시 보내는 법 (주인 결정 10/15)."""
    return (f"⚠️ #{queue_id} 사진을 쓸 수 없어 링크도 아직 안 붙였어요: {html.escape(why, quote=False)}\n"
            f"다른 사진과 함께 다시 보내 주세요 (사진 설명 칸에 <code>/link {queue_id} 링크</code>). "
            "사진 없이 올리려면 링크만 보내 주세요.")


def is_stale_message(sent_at: datetime | None, now: datetime | None = None, max_age: timedelta = STALE_COMMAND_MAX_AGE) -> bool:
    if sent_at is None:
        return False
    now = now or utcnow()
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=now.tzinfo)
    return now - sent_at > max_age


KAKAO_LIMIT = 200  # 카카오 텍스트 한 통
KAKAO_SHORT = 48  # 카톡 묶음 알림의 한 줄
STATUS_FRESH = timedelta(minutes=15)  # Blog-Auto 생존 신호가 이보다 오래되면 카톡을 못 믿고 텔레그램으로 소리 내서
LOUD_AGAIN = timedelta(hours=6)  # 소리 내서 대신 알리는 것은 같은 일마다 이 간격으로 한 번
FALLBACK_NOTE = "\n(카톡 알림이 안 돼서 텔레그램으로 알려요)"
_AUTH_RE = re.compile(r"token|oauth|auth|permission|expired|session|권한|인증|만료|로그인", re.I)


def kakao_fit(head: str, tail: str = "", limit: int = KAKAO_LIMIT) -> str:
    """카톡 한 통(200자): 첫 줄(이름이 든 줄)만 '…' 로 줄이고, 둘째 줄(어디서 할지 · '→' 안내)은 그대로."""
    tail = f"\n{tail}" if tail else ""
    room = limit - len(tail)
    if len(head) > room:
        head = head[: max(1, room - 1)].rstrip() + "…"
    return head + tail


def _short_url(url: str, limit: int = 90) -> str:
    """카톡 한 통(200자)에 들어가게: 긴 상품 주소는 쿼리(추적값)를 뗀다 (상품 경로는 그대로)."""
    return url if len(url) <= limit else url.split("#", 1)[0].split("?", 1)[0]


_PUBLIC_CHANNEL_RE = re.compile(r"^(?:@|https?://t\.me/)([A-Za-z]\w{3,})/?$")


def channel_post_base(channel_id: str | int | None, telegram_url: str = "") -> str | None:
    """공개 채널 글 주소의 앞부분 (https://t.me/이름). 비공개 채널(-100…)·초대 링크(t.me/+…)면 None."""
    for cand in (str(channel_id or "").strip(), (telegram_url or "").strip()):
        m = _PUBLIC_CHANNEL_RE.match(cand)
        if m:
            return f"https://t.me/{m.group(1)}"
    return None


# 텔레그램 주소 (채널 글·채널·초대 링크·웹 텔레그램)는 제휴·상품 링크가 아니다 (리뷰 지적 10/13: 채널 글 링크가 제휴 링크로 붙었다)
_TELEGRAM_HOSTS = ("t.me", "telegram.me", "telegram.dog", "telegram.org")
_POST_HOSTS = ("t.me", "telegram.me", "telegram.dog")
# 채널 글 주소의 경로: /채널이름/123 · /c/1234567890/123 (비공개 채널) · /s/채널이름/123 (웹 미리보기)
_CHANNEL_POST_PATH_RE = re.compile(r"^/(?:s/)?(c/\d+|[A-Za-z]\w*)/(\d+)/?$")


def _url_host(url: str) -> str:
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_telegram_url(url: str) -> bool:
    """텔레그램 주소인지 (t.me·telegram.me·telegram.org 와 그 하위 주소)."""
    host = _url_host(url)
    return any(host == h or host.endswith("." + h) for h in _TELEGRAM_HOSTS)


def channel_post_ref(url: str) -> tuple[str, int] | None:
    """텔레그램 채널 글 주소 → (채널 이름 또는 'c/내부번호', 글 번호). 채널 글 주소가 아니면 None."""
    if _url_host(url) not in _POST_HOSTS:
        return None
    m = _CHANNEL_POST_PATH_RE.match(urlparse(url.strip()).path)
    return (m.group(1), int(m.group(2))) if m else None


class AdminNotifier:
    def __init__(
        self,
        bot: Bot | None,
        admin_chat_id: int | str | None,
        cfg: MonitoringConfig,
        renderer: TemplateRenderer,
        tz: str,
        push: PushNotifier | None = None,
        registry: ShopRegistry | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.bot = bot
        self.chat_id = normalize_chat_id(admin_chat_id)
        self.cfg = cfg
        self.renderer = renderer
        self.tz = tz
        self.push = push
        self.registry = registry
        self.labels = labels or {}
        self.bot_username: str | None = None
        self.last_sent_at: datetime | None = None  # 마지막으로 관리자 챗에 무언가 보낸 시각 (하트비트 판단용)
        self._last_alert: dict[str, datetime] = {}
        # 카톡 '나에게 보내기' 중계 (app 이 Blog-Auto 받은편지함에 쓰는 함수를 넣어 줌). None 이면 안 보냄
        self.owner_relay: Callable[[str, str], None] | None = None
        self.channel_post_base: str | None = None  # 공개 채널이면 'https://t.me/이름' (카톡 '올렸어요'에 글 주소 붙임)
        # 보낸 메시지 기록 (message_id, 글, 종류, 딜 번호) — 같은 챗에 Blog-Auto 도 같은 봇으로 보내서, 답장 대상이
        # 딜봇 메시지인지 블로그 메시지인지 가리는 데 쓴다 (말 명령). app 이 넣어 줌. None 이면 기록 안 함
        self.msg_log: Callable[[int | None, str, str, int | None], None] | None = None
        # 알림 정책 (주인 결정 10/11): Blog-Auto 받은편지함에 owner_notice v2 한 줄 쓰기 (app.relay_to_blog) · Blog-Auto 생존 신호 파일
        self.owner_inbox: Callable[[dict], None] | None = None
        self.status_path: Path | None = None
        self._loud_sent: dict[str, datetime] = {}  # 소리 내서 대신 알린 일 (같은 일은 6시간에 한 번)
        self._final_fails: list[datetime] = []  # 채널 발행 최종 실패 (성공하면 비움) — 3번 이어지면 '발행 막힘'
        self._err_since: dict[str, datetime] = {}  # 오류 종류별로 실패가 이어진 첫 시각 (성공하면 지움)
        self._err_recent: dict[str, list[datetime]] = {}

    @property
    def silent_policy(self) -> bool:
        """주인 결정 10/11: 관리자 챗 알림은 소리 없이, 할 일은 카톡으로 (monitoring.telegram_silent)."""
        return bool(getattr(self.cfg, "telegram_silent", False))

    def blogauto_ok(self) -> bool:
        """Blog-Auto 가 살아 있고(15분 안 생존 신호) 카톡이 되는가 — 아니면 할 일 알림은 텔레그램으로 소리 내서."""
        if self.status_path is None:
            return False
        try:
            data = json.loads(self.status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        at = data.get("at")
        if not isinstance(at, (int, float)) or time.time() - at > STATUS_FRESH.total_seconds():
            return False
        return bool(data.get("kakao_ok"))

    def blogauto_reads_v2(self) -> bool:
        """Blog-Auto 가 요약·해결 줄(v2)을 알아듣는 판인가 — 생존 신호 파일(blogauto_status.json)은 v2 를 읽는 판만 쓴다.
        예전 판은 글이 든 owner_notice 를 모두 바로 카톡으로 보내서, 요약 줄이 상품 이름 한 줄짜리 카톡으로 따로 간다
        (두 저장소가 따로 자동 업데이트되니 딜봇이 먼저 바뀌는 사이). 오래된 파일이어도 v2 판이 있던 것이라 줄은 남긴다."""
        return self.status_path is not None and self.status_path.exists()

    def owner_line(self, note: dict[str, Any]) -> bool:
        """Blog-Auto 받은편지함에 owner_notice v2 한 줄 (카톡 알림·해결·저녁 요약). 실패해도 텔레그램은 그대로.
        한 줄을 남겼으면 True — 못 남겼으면(카톡 넘기기 꺼짐·파일 오류) 할 일 알림은 텔레그램으로 소리 내서 (alert)."""
        if self.owner_inbox is None or not getattr(self.cfg, "kakao_relay", True):
            return False
        try:
            return self.owner_inbox({"owner_notice": {"v": 2, "at": time.time(), **note}}) is not False
        except Exception as e:  # noqa: BLE001
            log.warning("owner notice failed: %s", e)
            return False

    def resolve(self, *keys: str) -> None:
        """주인이 처리함 (링크 붙임·/skip·/ok·품절) — Blog-Auto 가 아직 안 보낸 카톡은 보내지 않게."""
        if self.silent_policy and keys and self.blogauto_reads_v2():  # 예전 Blog-Auto 는 해결 줄을 모른다
            self.owner_line({"resolve": list(keys)})

    def summary(self, cat: str, text: str, *, replace: bool = False) -> None:
        """저녁 요약(카톡)에 넣을 한 줄 — 딜봇 몫 (Blog-Auto 가 21~22시에 문장으로 보냄)."""
        # replace 에 빈 글이면 그 줄을 비움 (기다리는 일이 없어짐). 예전 Blog-Auto 는 요약 줄을 바로 카톡으로 보내서 남기지 않는다
        if self.silent_policy and (text or replace) and self.blogauto_reads_v2():
            self.owner_line({"kind": "summary", "cat": cat, "text": text, "replace": replace})

    async def alert(self, kind: str, tg_text: str | None, kakao_text: str, *, key: str, short: str, group: str = "",
                    urgent: bool = False, link: str = "", ttl_h: float = 24, msg_kind: str = "",
                    queue_id: int | None = None) -> int | None:
        """주인이 해야 할 일: 텔레그램 자세한 내용(답장할 자리) + Blog-Auto 가 보낼 카톡 한 통(v2).
        Blog-Auto 가 살아 있고 카톡이 되면 텔레그램은 소리 없이, 아니면 소리 내서(같은 일은 6시간에 한 번) — 그래도 카톡 줄은 남긴다
        (Blog-Auto 가 곧 살아나면 보내고, 같은 키라 두 번 가지 않음)."""
        if not self.silent_policy:
            return await self.send_with_id(tg_text, kind=msg_kind, queue_id=queue_id) if tg_text else None
        # 카톡 줄을 먼저 남긴다 — 못 남겼으면 Blog-Auto 가 살아 있어도 카톡이 안 가니 텔레그램으로 소리 내서 (할 일을 잃지 않게)
        queued = self.owner_line({"kind": kind, "key": key, "group": group, "text": kakao_fit(kakao_text),
                                  "short": short[:KAKAO_SHORT], "urgent": urgent, "link": link, "ttl_h": ttl_h})
        loud = False
        text = tg_text
        if not queued or not self.blogauto_ok():
            now = utcnow()
            last = self._loud_sent.get(key)
            if last is None or now - last >= LOUD_AGAIN:
                self._loud_sent[key] = now
                loud = True
                # 첫 글자는 그대로 (📝 정보 글 확인처럼 첫 글자로 답장 대상을 알아보는 메시지가 있음)
                text = f"{tg_text or html.escape(kakao_text)}{FALLBACK_NOTE}"
        return await self.send_with_id(text, loud=loud, kind=msg_kind, queue_id=queue_id) if text else None

    def _relay(self, kind: str, text: str) -> None:
        """주인에게 카톡으로도 (예전 v1: 링크 요청·링크 완료·올렸어요 — telegram_silent 를 끈 경우). 실패해도 텔레그램 알림은 그대로."""
        if self.owner_relay is None or not getattr(self.cfg, "kakao_relay", True):
            return
        try:
            self.owner_relay(kind, text)
        except Exception as e:  # noqa: BLE001
            log.warning("kakao relay failed: %s", e)

    @property
    def enabled(self) -> bool:
        return self.bot is not None and self.chat_id is not None

    @property
    def telegram_link(self) -> str | None:
        return f"https://t.me/{self.bot_username}" if self.bot_username else None

    async def _push(self, event: str, title: str, message: str, *, priority: str = "default", tags: list[str] | None = None) -> None:
        if self.push is None or not self.push.wants(event):
            return
        await self.push.send(title, message, click_url=self.telegram_link, priority=priority, tags=tags)

    async def send(self, text: str, *, silent: bool = False) -> bool:
        return (await self.send_with_id(text, silent=silent)) is not None

    async def send_with_id(self, text: str, *, silent: bool = False, loud: bool = False, kind: str = "",
                           queue_id: int | None = None) -> int | None:
        """보내고 텔레그램 message_id 를 돌려준다 (나중에 고치거나 지우려고). 조각마다 보낸 메시지 기록에 남긴다.
        소리: telegram_silent(주인 결정 10/11)면 소리 없이, loud=True(카톡 대신 알림)만 소리와 함께."""
        if not self.enabled:
            log.warning(
                "관리자 알림을 보낼 수 없어 건너뜁니다 (봇 토큰/관리자 챗 ID 확인 필요): %s",
                text.replace("\n", " | ")[:150],
            )
            return None
        assert self.bot is not None
        # telegram_silent 면 모두 소리 없이 (loud 만 예외). 끄면 예전처럼 silent + monitoring.quiet_notices 가 결정
        quiet = (self.silent_policy or (silent and self.cfg.quiet_notices)) and not loud
        send = functools.partial(self.bot.send_message, chat_id=self.chat_id, link_preview_options=None)
        def record(m: Any) -> None:
            if self.msg_log is not None:
                self.msg_log(getattr(m, "message_id", None), text, kind, queue_id)

        try:
            msg = await send_chunks(send, text, disable_notification=quiet, on_sent=record)
        except TelegramError as e:
            log.error("admin notify failed: %s", e)
            return None
        if msg is SEND_FAILED:
            return None
        self.last_sent_at = utcnow()
        return int(getattr(msg, "message_id", 0) or 0)  # id 를 모르면 0 (성공은 성공)

    async def edit(self, message_id: int, text: str) -> bool:
        """내가 보낸 관리자 챗 메시지를 고친다 (예: 품절된 링크 요청)."""
        if not self.enabled:
            return False
        assert self.bot is not None
        try:
            await self.bot.edit_message_text(chat_id=self.chat_id, message_id=message_id, text=text[:4096], parse_mode=ParseMode.HTML)
            return True
        except TelegramError as e:
            log.warning("admin edit failed (message %s): %s", message_id, e)
            return False

    async def pin(self, message_id: int) -> bool:
        """관리자 챗 맨 위에 고정한다 (예: 내 링크 기다리는 글 목록)."""
        if not self.enabled:
            return False
        assert self.bot is not None
        try:
            await self.bot.pin_chat_message(chat_id=self.chat_id, message_id=message_id, disable_notification=True)
            return True
        except TelegramError as e:
            log.warning("admin pin failed (message %s): %s", message_id, e)
            return False

    def source_label(self, source: str) -> str:
        return self.labels.get(source) or COLLECTOR_LABELS.get(source, source)

    def shop_label(self, key: str) -> str:
        shop = self.registry.get(key) if self.registry else None
        return shop.name if shop else key

    async def notify_startup(self, status_text: str, check_lines: list[str] | None = None) -> bool:
        text = f"🟢 <b>봇이 켜졌습니다</b> (v{html.escape(version_label())})\n"
        if check_lines:
            text += "\n<b>자기 점검</b> — ✅ 정상 · ⚠️ 아직 설정 안 함(선택) · ❌ 문제\n" + "\n".join(html.escape(line) for line in check_lines) + "\n"
        text += f"\n{status_text}"
        sent = await self.send(text, silent=True)
        await self._push("startup", "DealBot 시작", "\n".join(check_lines or [])[:500] or "봇이 시작되었습니다.")
        return sent

    async def notify_published(self, deal: Deal, result: PublishResult, preview: str | None = None) -> None:
        """발행 알림. DRY-RUN 이면 채널에 올라갔을 글 전체(preview)를 그대로 보여준다 (연습 모드 미리보기는 설정과 무관하게 항상).
        주인이 링크를 붙여 준 딜은 알림을 꺼 두었어도 '채널에 올렸어요'를 보낸다 (링크 완료 뒤 한 번 더 — 10/10)."""
        owner_link = bool(deal.product.extra.get("owner_link")) and not result.dry_run
        if not result.dry_run:
            self._final_fails.clear()  # 채널에 올라갔으니 '발행 막힘' 셈은 처음부터
        if owner_link and self.silent_policy:  # 주인 결정 10/11: '채널에 올렸어요'는 카톡·텔레그램 대신 저녁 요약에만
            self.summary("published", truncate(deal.product.name, 40))
            if not self.cfg.notify_on_publish:
                return
            owner_link = False  # 발행 알림을 켜 두었으면 보통 발행 알림으로 (소리 없이)
        if not self.cfg.notify_on_publish and not (result.dry_run and preview) and not owner_link:
            return
        p = deal.product
        where = f"어디서: {html.escape(self.source_label(p.source))} → {html.escape(self.shop_label(p.shop))}"
        why = f"고른 이유: {html.escape(humanize_reasons(deal.verdict.reasons))} · 점수 {deal.verdict.score:g}"
        if deal.verdict.market_title and deal.verdict.market_price:  # 같은 상품(같은 수량)과 비교했는지 눈으로 확인
            where_cmp = market_name(deal.verdict.market_source)
            why += f"\n비교한 {where_cmp} 상품: {html.escape(truncate(deal.verdict.market_title, 60))} {deal.verdict.market_price:,}원"
        if result.dry_run and preview:
            photo = " · 🖼 사진 있음" if p.image_url else ""
            text = (
                f"🧪 <b>미리보기</b> — 연습 모드라 채널에는 올리지 않았습니다\n"
                f"{where}{photo}\n"
                f"{why}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{preview}"
            )
            await self.send(text, silent=True)
            return
        # '채널에 올렸어요' 카톡은 없앰 (주인 결정 10/11 — 알림 정책 스위치와 상관없이)
        head = ("🧪 <b>연습 발행</b>" if result.dry_run else
                "📣 <b>링크를 붙여 주신 상품을 채널에 올렸어요</b>" if owner_link else "✅ <b>채널에 올렸습니다</b>")
        price = f"{p.price:,}원" if p.has_price else "가격 없음"
        text = (
            f"{head}\n"
            f"{html.escape(truncate(p.name, 80))} · {price}\n"
            f"{where}\n{why}"
        )
        if deal.affiliate_url:
            text += f"\n{html.escape(deal.affiliate_url)}"
        await self.send(text, silent=True)

    async def notify_publish_failed(self, deal: Deal, error: str, *, final: bool) -> None:
        if not self.cfg.notify_on_failure or (not final and not self.cfg.notify_failure_attempts):
            return
        p = deal.product
        head = "❌ <b>채널에 올리지 못했습니다</b> (여러 번 실패해서 포기)" if final else "⚠️ <b>채널에 올리지 못했습니다</b> (잠시 뒤 다시 시도합니다)"
        await self.send(
            f"{head} [{html.escape(self.shop_label(p.shop))}]\n{html.escape(truncate(p.name, 80))}\n이유: <code>{html.escape(error[:500])}</code>"
        )
        if final:
            await self._push("publish_failed", f"채널에 올리지 못함 [{self.shop_label(p.shop)}]", f"{truncate(p.name, 60)}\n{error[:200]}")

    async def notify_publish_unsure(self, queue_id: int, deal: Deal, error: str) -> None:
        """채널에 보냈지만 텔레그램 답이 시간 안에 안 옴: 이미 올라갔을 수 있어 다시 보내지 않았다 (같은 딜 두 번 방지).
        주인이 채널을 보고 할 일 — 있으면 /posted 로 올라간 글로 기록(블로그·스레드·인스타를 이어서), 없으면 /link 로 다시 올린다.
        할 일이라 카톡으로 (주인 결정 10/11, 링크 요청과 같은 방식). 소리 없는 텔레그램 한 줄만으로는 7일 동안 묻혔다."""
        if not self.cfg.notify_on_failure:
            return
        p = deal.product
        link = deal.affiliate_url or p.url
        text = (
            f"❓ <b>채널에 올라갔는지 모릅니다 #{queue_id}</b> [{html.escape(self.shop_label(p.shop))}]\n"
            f"{html.escape(truncate(p.name, 80))}\n"
            f"텔레그램 답이 늦어(시간 초과) 두 번 올라가지 않게 다시 보내지 않았어요. 채널을 확인해 주세요.\n"
            f"· 채널에 있으면 <code>/posted {queue_id}</code> 또는 이 메시지에 채널 글 링크(t.me)로 답장 (링크가 있으면 품절 표시도 돼요)"
            f" — 블로그·스레드·인스타로 이어서 올려요\n"
            f"· 채널에 없으면 <code>/link {queue_id} {html.escape(link)}</code> 로 다시 올려 주세요\n"
            f"이유: <code>{html.escape(error[:300])}</code>"
        )
        await self.alert(
            "publish_unsure", text,
            kakao_fit(f"🔔[할 일] 채널 확인 #{queue_id} {truncate(p.name, 60)}",
                      f"→ 채널에 있으면 텔레그램 핫딜봇 /posted {queue_id} · 없으면 /link {queue_id}"),
            key=f"unsure:{queue_id}", short=f"채널 확인 #{queue_id}", group="publish_unsure", queue_id=queue_id,
        )

    async def publish_failed_final(self, error: str) -> None:
        """채널 발행 최종 실패 한 번 (알림 설정과 상관없이 셈): 3번 이어지거나 한 시간 안에 3번이면 '발행 막힘' 급한 카톡."""
        if not self.silent_policy:
            return
        now = utcnow()
        self._final_fails = [t for t in self._final_fails if now - t < timedelta(hours=24)] + [now]
        recent = [t for t in self._final_fails if now - t <= timedelta(minutes=60)]
        if len(self._final_fails) >= 3 or len(recent) >= 3:
            n = len(self._final_fails)
            await self.alert(
                "publish_broken",
                f"⛔ <b>채널에 {n}건 연속 못 올리고 있어요</b>\n마지막 이유: <code>{html.escape(error[:300])}</code>\n/errors 로 확인해 주세요",
                kakao_fit(f"⛔[멈춤] 딜봇이 채널에 못 올리고 있어요 ({n}건 연속 실패)", "→ 텔레그램 핫딜봇 /errors"),
                key="publish_broken", short="딜봇 채널 발행 막힘", urgent=True, ttl_h=6,
            )

    async def notify_manual_link(self, item: QueueItem, shop: Shop, *, auth_note: str | None = None) -> int | None:
        """자동 변환이 안 되는 쇼핑몰: 관리자에게 링크 생성을 요청. 보낸 메시지 id 를 돌려준다."""
        if not self.cfg.notify_on_manual_link:
            return None
        p = item.deal.product
        price = f" — {p.price:,}원" if p.has_price else ""
        hint = shop.manual_hint or "앱/사이트에서 내 제휴 링크를 만들어 보내주세요"
        # 네이버 딜 (주인 결정 10/15): 가격·옵션을 휴대폰으로 확인하고 상품 사진도 같이 — 그 사진이 채널·블로그·인스타 상품 사진
        ask = photo_ask(p) if wants_owner_photo(shop.key, p) else ""
        how_photo = (
            f"{ask}\n"
            f"사진을 <b>이 메시지에 답장</b>으로 보내며 설명 칸에 링크를 넣거나, 사진 설명 칸에 <code>/link {item.id} https://...</code>\n"
            "(화면 캡처는 상품 부분만 잘라서. 사진 없이 링크만 보내도 올라가요 — 그때는 채널엔 글만, 인스타는 글자 카드)\n"
        ) if ask else ""
        text = (
            f"🔗 <b>내 링크가 필요합니다 #{item.id}</b> [{html.escape(shop.name)}]\n"
            f"{html.escape(truncate(p.name, 80))}{price}\n"
            f"원본 주소: {html.escape(p.url)}\n"
            + (f"글: {html.escape(str(p.extra.get('post_url')))}\n" if p.extra.get("post_url") else "")
            + (f"{html.escape(auth_note)}\n" if auth_note else "")
            + naver_search_help(p.name, p.url, shop.key)
            + f"\n👉 {html.escape(hint)}\n"
            + how_photo
            + f"만든 링크를 <b>이 메시지에 답장</b>으로 보내면 바로 올라갑니다. 또는 <code>/link {item.id} https://...</code>\n"
            f"안 올리려면 <code>/skip {item.id}</code>"
        )
        if self.silent_policy:  # 주인 결정 10/11: 할 일은 카톡 (주소 줄 없이 한 통), 텔레그램은 답장할 자리
            # 사진 부탁은 둘째 줄(잘리지 않는 쪽)에 — 긴 상품명이 잘려도 부탁은 남게
            message_id = await self.alert(
                "link_request", text,
                kakao_fit(f"🔔[할 일] 링크 필요 #{item.id} [{shop.name}] {truncate(p.name, 60)}{price}",
                          (f"{ask}\n" if ask else "") + f"→ 텔레그램 핫딜봇 #{item.id} 에 답장"),
                key=f"link:{item.id}", short=f"핫딜 링크 #{item.id}", group="link_request", msg_kind="link", queue_id=item.id,
            )
        else:
            message_id = await self.send_with_id(text, kind="link", queue_id=item.id)
            if ask:  # 200자 한 통: 주소·사진 부탁은 잘리지 않는 쪽에, 넘치면 상품명이 줄어든다
                v1 = kakao_fit(f"🔗 내 링크가 필요해요 #{item.id} [{shop.name}]\n{truncate(p.name, 60)}{price}",
                               f"{_short_url(p.url)}\n{ask}\n→ 텔레그램 딜봇 링크 요청에 링크·사진을 답장해 주세요")
            else:
                v1 = (f"🔗 내 링크가 필요해요 #{item.id} [{shop.name}]\n{truncate(p.name, 60)}{price}\n{_short_url(p.url)}\n"
                      "→ 텔레그램 딜봇의 링크 요청 메시지에 답장으로 링크를 보내 주세요")
            self._relay("link_request", v1)
        await self._push(
            "manual_link",
            f"내 링크가 필요합니다 #{item.id} [{shop.name}]",
            f"{truncate(p.name, 70)}{price}\n{p.url}\n\n{hint}" + (f"\n{ask}" if ask else ""),
            priority="high",
            tags=["link"],
        )
        return message_id

    def link_done_text(self, item: QueueItem, *, photo: str | None = None) -> str:
        """주인이 링크를 붙였을 때 답장 (주인 요청 10/10: '○○ 상품 링크 완료' 알림).
        photo (네이버 딜, 주인 결정 10/15): 'ok' 같이 보낸 사진을 받음 · 'kept' 전에 받은 사진을 그대로 · 'none' 사진 없이 ·
        'unused' 사진은 왔지만 네이버 상품 딜이 아니라 안 씀 · None 사진과 상관없는 딜."""
        p = item.deal.product
        price = f" · {p.price:,}원" if p.has_price else ""
        shop = self.shop_label(p.shop)
        note = {
            "ok": "📸 상품 사진도 받았어요 (채널·블로그·인스타에 씀)\n",
            "kept": "📸 전에 받은 상품 사진을 그대로 써요\n",
            "none": "사진 없이 받았어요 — 채널엔 글만, 인스타는 상품명·가격 글자 카드로 올라가요\n",
            "unused": "사진은 네이버 딜에만 써요 — 링크만 붙였어요\n",
        }.get(photo or "", "")
        head = f"✅ <b>상품 링크 완료 #{item.id}</b> [{html.escape(shop)}]\n{html.escape(truncate(p.name, 80))}{price}\n{note}"
        if self.silent_policy:  # 주인 결정 10/11: '상품 링크 완료' 카톡은 없이 저녁 요약에만 · 아직 안 보낸 링크 요청 카톡은 취소
            self.resolve(f"link:{item.id}")
            self.summary("link_done", truncate(p.name, 40))
            return head + "올릴 차례가 되면 바로 채널에 올라가요"
        # '상품 링크 완료' 카톡은 없앰 (주인 결정 10/11 — 알림 정책 스위치와 상관없이)
        return head + "올릴 차례가 되면 바로 채널에 올라가요 (올라가면 한 번 더 알려 드려요)"

    async def notify_deal_review(self, item: QueueItem, shop_name: str, reason: str) -> int | None:
        """오픈마켓 딜의 공식 판매처가 확인되지 않음: 관리자가 보고 /ok 하면 링크를 만들어 올린다."""
        p = item.deal.product
        price = f" · {p.price:,}원" if p.has_price else ""
        text = (
            f"🔎 <b>정품 확인 필요 #{item.id}</b> [{html.escape(shop_name)}]\n"
            f"{html.escape(truncate(p.name, 80))}{price}\n"
            f"이유: {html.escape(reason)}\n"
            f"상품 페이지: {html.escape(p.url)}\n"
            + (f"게시글: {html.escape(str(p.extra.get('post_url')))}\n" if p.extra.get("post_url") else "")
            + f"\n👉 공식 판매처가 맞으면 <code>/ok {item.id}</code> → 링크를 만들어 채널에 올립니다 · 아니면 <code>/skip {item.id}</code>"
        )
        message_id = await self.alert(
            "review_auth", text,
            kakao_fit(f"🔔[할 일] 정품 확인 #{item.id} {truncate(p.name, 60)}", f"→ 텔레그램 핫딜봇에서 /ok {item.id} 또는 /skip {item.id}"),
            key=f"review:{item.id}", short=f"정품 확인 #{item.id}", group="review_auth", msg_kind="review_auth", queue_id=item.id,
        )
        await self._push("info_review", f"정품 확인 필요 #{item.id} [{shop_name}]", f"{truncate(p.name, 70)}\n{reason}", tags=["mag"])
        return message_id

    async def notify_info_review(self, item: QueueItem, preview: str, *, reason: str, image_count: int = 0) -> int | None:
        """정보 글을 자동으로 올리지 않고 관리자에게 먼저 보여 준다 (요약을 못 만들었거나 확인 설정일 때)."""
        photo = f" · 🖼 사진 {image_count}장 (첫 장을 같이 올림)" if image_count else ""
        text = (
            f"{REVIEW_MARK} <b>정보 글 확인 #{item.id}</b> — 이대로 올릴까요?\n"
            f"이유: {html.escape(reason)}{photo}\n"
            f"━━━━━━━━━━━━━━\n"
            f"{preview}\n"
            f"━━━━━━━━━━━━━━\n"
            f"👉 <code>/ok {item.id}</code> 이대로 올리기 · <code>/skip {item.id}</code> 안 올리기\n"
            f"글을 고치려면 <b>이 메시지에 답장</b>으로 본문을 새로 써 보내세요. 제목·원문 주소는 봇이 붙입니다."
        )
        message_id = await self.alert(
            "review_info", text,
            kakao_fit(f"🔔[할 일] 정보 글 확인 #{item.id} {truncate(item.deal.product.name, 50)} — 이대로 올릴까요?",
                      f"→ 텔레그램 핫딜봇에서 /ok {item.id} · /skip {item.id}"),
            key=f"review:{item.id}", short=f"정보 글 확인 #{item.id}", group="review_info", msg_kind="review_info", queue_id=item.id,
        )
        await self._push("info_review", f"정보 글 확인 #{item.id}", f"{truncate(item.deal.product.name, 70)}\n{reason}", tags=["memo"])
        return message_id

    async def notify_sold_out_cancel(self, item: QueueItem, via: str, notice_message_id: int | None) -> None:
        """내 링크를 기다리던 글이 품절됨: 보냈던 링크 요청 메시지를 고쳐서 헛수고를 막는다."""
        p = item.deal.product
        self.resolve(f"link:{item.id}")  # 링크를 만들 필요가 없어졌으니 아직 안 보낸 카톡은 취소
        text = (
            f"⛔ <b>품절되어 취소했습니다 #{item.id}</b> [{html.escape(self.shop_label(p.shop))}]\n"
            f"{html.escape(truncate(p.name, 80))}\n"
            f"확인 경로: {html.escape(via)}. 링크를 만들 필요가 없습니다."
        )
        if notice_message_id and await self.edit(notice_message_id, text):
            return
        await self.send(text, silent=True)

    async def notify_link_replaced(self, item: QueueItem, new_id: int, notice_message_id: int | None, *, until: str) -> bool:
        """링크 요청 자리가 다 차서 점수가 더 높은 딜에 자리를 내줌 (주인 결정 10/15): 보냈던 링크 요청 메시지만 고친다
        (새 메시지는 보내지 않음 — 하루에 여러 번 생길 수 있어서). 아직 안 보낸 링크 요청 카톡은 취소. 고쳤으면 True."""
        p = item.deal.product
        self.resolve(f"link:{item.id}")
        if not notice_message_id:
            return False
        price = f" — {p.price:,}원" if p.has_price else ""
        text = (
            f"↪️ <b>더 좋은 딜로 바꿨어요 #{item.id}</b> [{html.escape(self.shop_label(p.shop))}]\n"
            f"{html.escape(truncate(p.name, 80))}{price}\n"
            # 그래도 올리고 싶을 때 링크를 만들 주소 (내린 글은 /pending·고정 공지에서 빠지니 여기에 남긴다)
            f"원본 주소: {html.escape(p.url)}\n"
            + (f"글: {html.escape(str(p.extra.get('post_url')))}\n" if p.extra.get("post_url") else "")
            + f"링크 요청 자리가 다 차서 점수가 더 높은 #{new_id} 딜을 대신 물었어요. 이 링크는 만들지 않아도 돼요.\n"
            f"그래도 올리고 싶으면 {html.escape(until)} 까지 이 메시지에 답장으로 링크를 보내 주세요 "
            f"(또는 <code>/link {item.id} 링크</code>). 그대로 올려요.\n"
            + naver_search_help(p.name, p.url, p.shop)
        )
        return await self.edit(notice_message_id, text)

    def error_cleared(self, kind: str) -> None:
        """그 종류의 작업이 다시 성공함 — '계속 실패' 셈을 처음부터."""
        self._err_since.pop(kind, None)
        self._err_recent.pop(kind, None)

    async def _escalate(self, kind: str, message: str) -> None:
        """오류가 이어지면 주인 카톡: 게시판 확인이 2시간 넘게 계속 실패 · 발행 작업이 30분 안에 3번 실패(급함)."""
        if not self.silent_policy:
            return
        now = utcnow()
        since = self._err_since.setdefault(kind, now)
        recent = [t for t in self._err_recent.get(kind, []) if now - t <= timedelta(minutes=30)] + [now]
        self._err_recent[kind] = recent
        if kind.startswith("collector:") and now - since >= timedelta(hours=2):
            site = describe_kind(kind, self.labels).removesuffix(" 확인 중")
            await self.alert(
                "collector_stuck", None,
                kakao_fit(f"⚠️[할 일] 딜봇 {site} 확인이 2시간째 실패 중", "→ 서버 점검 필요 (텔레그램 핫딜봇 /errors)"),
                key=kind, short=f"딜봇 {site} 수집 실패", ttl_h=12,
            )
        elif kind == "publisher" and len(recent) >= 3:
            await self.alert(
                "publisher_stuck", None,
                kakao_fit(f"⛔[멈춤] 딜봇 발행 대기열이 멈췄어요 ({truncate(message, 60)})", "→ 서버 점검 필요 · 텔레그램 핫딜봇 /errors"),
                key="publisher_stuck", short="딜봇 발행 멈춤", urgent=True, ttl_h=6,
            )

    async def notify_price_hold(
        self, queue_id: int, product: Product, check: PageCheck, *, notice_message_id: int | None = None
    ) -> int | None:
        """제목 가격이 상품 페이지 가격과 안 맞아 보류함 (링크 요청 없음): 관리자 챗에 조용히 한 줄 + 상품.
        notice_message_id: 이미 보낸 링크 요청 메시지 — 있으면 그 메시지를 보류 안내로 고친다 (헛수고 방지)."""
        text = (
            f"{html.escape(hold_line(queue_id, check), quote=False)}\n"
            f"[{html.escape(self.shop_label(product.shop))}] {html.escape(truncate(product.name, 60))} · {html.escape(product.url)}"
        )
        # 알림 정책 (주인 결정 10/11): 보류는 주인이 할 일이 아니라 소식 — 텔레그램은 조용히, 카톡은 저녁 요약에만 (할 일 카톡 아님)
        self.summary("held", truncate(product.name, 40))
        if notice_message_id and await self.edit(notice_message_id, text + "\n링크를 만들 필요가 없습니다."):
            return notice_message_id
        return await self.send_with_id(text, silent=True, kind="price_hold", queue_id=queue_id)

    async def notify_error(self, kind: str, message: str) -> None:
        await self._escalate(kind, message)
        if not self.cfg.notify_on_error:
            return
        now = utcnow()
        last = self._last_alert.get(kind)
        cooldown = timedelta(minutes=self.cfg.error_alert_cooldown_minutes)
        if last is not None and now - last < cooldown:
            log.debug("error alert for %s suppressed (cooldown)", kind)
            return
        self._last_alert[kind] = now
        where = describe_kind(kind, self.labels)
        await self.send(
            f"🚨 <b>에러가 났습니다</b> — {html.escape(where)}\n<code>{html.escape(message[:1500])}</code>\n"
            f"(같은 종류는 {self.cfg.error_alert_cooldown_minutes}분에 한 번만 알립니다. 봇은 계속 돌아갑니다)"
        )
        await self._push("error", f"에러: {where}", message[:300], tags=["warning"])

    async def notify_daily_summary(self, text: str) -> None:
        await self.send(text)
        plain = re.sub(r"<[^>]+>", "", text)
        await self._push("daily_summary", "DealBot 일일 요약", plain[:800])


class StatusReporter:
    """/status, /queue, /pending, 일일 요약 텍스트 생성."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        state: BotState,
        rate_limiter: RateLimiter,
        renderer: TemplateRenderer,
        registry: ShopRegistry | None = None,
        links: LinkRouter | None = None,
        budget: Any | None = None,
        summarizer: Any | None = None,
    ) -> None:
        self.settings = settings
        self.summarizer = summarizer  # InfoSummarizer — /status 의 정보 글 요약 줄 (API / 서버 CLI / 꺼짐)
        self.db = db
        self.state = state
        self.rate = rate_limiter
        self.renderer = renderer
        self.registry = registry or settings.shop_registry()
        self.links = links
        self.budget = budget
        self.labels = {c.name: c.label or COLLECTOR_LABELS.get(c.name, c.name) for c in settings.collectors}

    def collector_label(self, name: str) -> str:
        return self.labels.get(name) or COLLECTOR_LABELS.get(name, name)

    def _collector_rows(self, now: datetime) -> list[dict[str, Any]]:
        tz = self.settings.app.timezone
        rows: list[dict[str, Any]] = []
        for st in self.state.collectors.values():
            last = self.db.last_run(st.name)
            rows.append(
                {
                    "name": st.name,
                    "label": self.collector_label(st.name),
                    "type": st.type,
                    "enabled": st.enabled,
                    "available": st.available,
                    "unavailable_reason": st.unavailable_reason,
                    "running": st.running,
                    "interval_minutes": st.interval_minutes,
                    "last_status": last.status if last else "-",
                    "last_run_ago": humanize_delta(now - last.started_at) + " 전" if last else "-",
                    "last_run_at": fmt_local(last.started_at, tz) if last else "-",
                    "collected": last.collected if last else 0,
                    "deals": last.deals if last else 0,
                    "queued": last.queued if last else 0,
                    "error": truncate(last.error, 120) if last and last.error else None,
                    "next_in": humanize_delta(st.next_run_at - now) if st.next_run_at and st.next_run_at > now else "곧",
                }
            )
        return rows

    def _shop_rows(self) -> list[dict[str, Any]]:
        rows = []
        for s in self.registry.all():
            mode = self.links.describe(s) if self.links else s.link_mode
            if s.key == "naver" and s.enabled and s.link_mode == "manual" and self.settings.autolink.naver.enabled:
                mode = "자동 (쇼핑커넥트, Blog-Auto) — 없는 상품은 원본 주소, 안 되면 내 링크 요청"
            rows.append({"key": s.key, "name": s.name, "enabled": s.enabled, "mode": mode, "reason": s.disabled_reason})
        return rows

    def _info_summary_text(self) -> str:
        cfg = self.settings.info_posts
        if not cfg.enabled:
            return "끔"
        sm = cfg.summarizer
        backend = getattr(self.summarizer, "backend", None) if self.summarizer is not None else (
            "api" if sm.enabled and self.settings.secrets.has_anthropic and sm.model else None)
        if backend == "api":
            how = f"요약 자동 (API {sm.model})"
        elif backend == "cli":
            how = f"요약 자동 (서버 claude CLI · {getattr(self.summarizer, 'cli_model', None) or '기본 모델'})"
        elif not sm.enabled:
            how = "요약 꺼짐 (설정) → 확인 요청으로 옴"
        else:
            how = "요약 꺼짐 (ANTHROPIC_API_KEY·서버 claude CLI 없음 → 확인 요청으로 옴)"
        down = None
        if self.summarizer is not None and backend and not getattr(self.summarizer, "available", True):
            # 키 오류로 꺼졌거나, CLI 로그인이 풀려 잠깐 쉬는 중 (다시 로그인하면 이어서 됨)
            down = getattr(self.summarizer, "disabled_reason", None) or getattr(self.summarizer, "last_error", None)
        if down:
            how += f" · ⚠️ {down}"
        mode = {"auto": "요약되면 바로 올림", "always": "항상 확인 후 올림", "never": "확인 없이 올림"}.get(cfg.review, cfg.review)
        return f"{how} · {mode}"

    def _threads_row(self, now: datetime) -> dict[str, Any]:
        """/status 의 스레드 줄: 연결 여부·계정·토큰 만료·최근 실패."""
        tz = self.settings.app.timezone
        row: dict[str, Any] = {"state": "off", "username": None, "expires": None, "last_fail": None}
        if not self.settings.threads.enabled:
            return row
        if not self.settings.secrets.has_threads_app:
            return row | {"state": "noapp"}
        if not (self.db.kv_get(KV_TOKEN) and self.db.kv_get(KV_USER_ID)):
            return row | {"state": "unlinked"}
        expires = from_iso(self.db.kv_get(KV_TOKEN_EXPIRES))
        last_fail = None
        for ev in self.db.recent_events(limit=30, level="WARNING"):
            if ev.get("kind") != "threads":
                continue
            ts = from_iso(str(ev.get("ts") or ""))
            if ts is None or now - ts <= timedelta(hours=24):
                when = fmt_local(ts, tz, "%m-%d %H:%M") if ts else ""
                last_fail = f"{when} {truncate(str(ev.get('message') or ''), 160)}".strip()
            break
        return row | {
            "state": "ok",
            "username": self.db.kv_get(KV_USERNAME) or None,
            "expires": fmt_local(expires, tz, "%m-%d") if expires else None,
            "last_fail": last_fail,
        }

    def _instagram_row(self, now: datetime) -> dict[str, Any]:
        from dealbot.publisher import instagram as ig

        row: dict[str, Any] = {"state": "off", "username": None, "expires": None, "last_fail": None, "no_domain": False}
        if not self.settings.instagram.enabled:
            return row
        if not self.settings.secrets.has_instagram_app:
            return row | {"state": "noapp"}
        if not (self.db.kv_get(ig.KV_TOKEN) and self.db.kv_get(ig.KV_USER_ID)):
            return row | {"state": "unlinked"}
        tz = self.settings.app.timezone
        expires = from_iso(self.db.kv_get(ig.KV_TOKEN_EXPIRES))
        last_fail = None
        for ev in self.db.recent_events(limit=30, level="WARNING"):
            if ev.get("kind") != "instagram":
                continue
            ts = from_iso(str(ev.get("ts") or ""))
            if ts is None or now - ts <= timedelta(hours=24):
                when = fmt_local(ts, tz, "%m-%d %H:%M") if ts else ""
                last_fail = f"{when} {truncate(str(ev.get('message') or ''), 160)}".strip()
            break
        return row | {
            "state": "ok",
            "username": self.db.kv_get(ig.KV_USERNAME) or None,
            "expires": fmt_local(expires, tz, "%m-%d") if expires else None,
            "last_fail": last_fail,
            "no_domain": not self.settings.secrets.public_base_url,
        }

    def status_context(self) -> dict[str, Any]:
        now = utcnow()
        tz = self.settings.app.timezone
        # 이번 실행(프로세스) 중에 난 에러만. 재시작 전 기록은 /errors 로 본다
        last_err = self.state.last_error
        last_err_at = self.state.last_error_at
        data_dir = Path(self.settings.data_dir)
        # 컨테이너에서는 /data 가 볼륨으로 마운트돼 있어야 재배포 후에도 기록이 남는다. 로컬 실행은 해당 없음
        data_persistent = not str(data_dir).startswith("/data") or os.path.ismount(data_dir)
        created = from_iso(self.db.kv_get("db_created_at"))
        return {
            "version": version_label(),  # 버전 + 지금 돌고 있는 커밋 (서버가 옛 코드인지 /status 로 확인)
            "data_persistent": data_persistent,
            "info_summary": self._info_summary_text(),
            "db_since": fmt_local(created, tz, "%Y-%m-%d") if created else None,
            "uptime": humanize_delta(now - self.state.started_at),
            "paused": self.state.paused,
            "dry_run": self.state.dry_run,
            "publish_enabled": self.settings.publish.enabled,
            "has_coupang": self.settings.secrets.has_coupang,
            "has_channel": self.settings.secrets.has_channel,
            "collectors": self._collector_rows(now),
            "shops": self._shop_rows(),
            # 꺼진 몰도 이유와 함께 보여준다: 제외한 몰 / 링크 변환기(ID)만 넣으면 자동으로 켜질 몰
            "shops_off_excluded": [s.name for s in self.registry.all() if not s.enabled and not s.disabled_reason],
            "shops_off_pending": {
                reason: [s.name for s in self.registry.all() if not s.enabled and s.disabled_reason == reason]
                for reason in dict.fromkeys(s.disabled_reason for s in self.registry.all() if not s.enabled and s.disabled_reason)
            },
            "rate": self.rate.snapshot(now),
            "threads": self._threads_row(now),
            "instagram": self._instagram_row(now),
            "site_url": self.settings.secrets.public_base_url if self.settings.web.public_page else None,
            "queue": self.db.queue_counts(),
            "products": self.db.product_count(),
            "price_points": self.db.price_history_count(),
            "db_mb": round(self.db.db_size_bytes() / 1024 / 1024, 1),
            "api_budget": (
                {
                    "used": self.budget.used(),
                    "max": self.budget.max_per_hour,
                    "by_kind": ", ".join(f"{k} {v}" for k, v in sorted(self.budget.usage().items())),
                }
                if self.budget is not None and self.settings.secrets.has_coupang
                else None
            ),
            "last_error": truncate(last_err.splitlines()[0], 300) if last_err else None,
            "last_error_at": fmt_local(last_err_at, tz) if last_err_at else None,
            "tz": tz,
        }

    def status_text(self) -> str:
        return self.renderer.render("status.j2", **self.status_context())

    def _shop_name(self, key: str) -> str:
        shop = self.registry.get(key)
        return shop.name if shop else key

    def _item_line(self, it: QueueItem) -> str:
        p = it.deal.product
        price = f" · {p.price:,}원" if p.has_price else ""
        tries = f" · 실패 {it.attempts}번" if it.attempts else ""
        return f"• #{it.id} {html.escape(self._shop_name(p.shop))} · {html.escape(truncate(p.name, 50))}{price} · 점수 {it.score:g}{tries}"

    def queue_text(self, limit: int = 10) -> str:
        items = self.db.pending_items(limit)
        counts = self.db.queue_counts()
        lines = [
            "🗂 <b>올릴 차례를 기다리는 글</b>",
            f"차례 대기 {counts.get('pending', 0)}건 · 내 링크 대기 {counts.get('awaiting_link', 0)}건 · 확인 대기 {counts.get('awaiting_approval', 0)}건 · 실패 {counts.get('failed', 0)}건 · 시간 지나 버림 {counts.get('expired', 0)}건",
        ]
        lines += [self._item_line(it) for it in items]
        if not items:
            lines.append("(지금 기다리는 글 없음 — 특가가 잡히면 여기 쌓였다가 순서대로 올라갑니다)")
        else:
            lines.append("\n점수가 높은 글부터 올라갑니다. 빼려면 <code>/skip 번호</code>")
        return "\n".join(lines)

    def pending_text(self, limit: int | None = None, *, notice: bool = False) -> str:
        """내 링크·확인을 기다리는 글. limit 을 안 주면 링크 요청 자리 수만큼(최소 15) — 자리에 든 글을 다 보여 준다.
        notice: 고쳐 쓰는 고정 공지(한 메시지)용 — 자리 15개에 원본 주소까지 넣어 한 메시지를 넘으면 원본 주소 줄을 뺀다."""
        limit = limit or max(15, self.settings.publish.max_awaiting_links)
        text = self._pending_text(limit, urls=True)
        if notice and len(text) > SAFE_CHUNK:
            text = self._pending_text(limit, urls=False)
        return text

    def _pending_text(self, limit: int, *, urls: bool) -> str:
        items = self.db.awaiting_items(limit)
        lines = [f"🔗 <b>내가 링크를 만들어 줘야 하는 글</b> ({len(items)}건)" if items else "🔗 <b>내가 링크를 만들어 줘야 하는 글</b>: 지금은 없습니다"]
        for it in items:
            p = it.deal.product
            lines.append(self._item_line(it))
            if urls:
                lines.append(f"   원본 주소: {html.escape(p.url)}")
        if items and not urls:
            lines.append("   (원본 주소는 각 링크 요청 메시지와 /pending 에 있어요)")
        if not items:
            lines.append("(없음 — 토스·네이버처럼 링크를 직접 만들어야 하는 글이 생기면 여기 뜁니다)")
        reviews = self.db.awaiting_items(limit, statuses=("awaiting_approval",))
        if reviews:
            lines.append("")
            lines.append(f"{REVIEW_MARK} <b>내가 확인해 줘야 하는 글</b> ({len(reviews)}건)")
            for it in reviews:
                p = it.deal.product
                why = (it.last_error or "").removeprefix("needs approval: ")
                lines.append(f"• #{it.id} {html.escape(truncate(p.name, 50))}" + (f" · {html.escape(why)}" if why else ""))
                if p.deal_kind != "info":
                    lines.append(f"   상품 페이지: {html.escape(p.url)}")
        if items or reviews:
            lines.append(
                "\n한 번에 답하기: 이 공지에 답장(또는 그냥 메시지)으로 한 줄에 하나씩 — "
                "<code>675 https://만든링크</code> · <code>677 ok</code> · <code>673 skip</code>. 여러 줄을 한 번에 보내도 됩니다. "
                "정보 글 본문을 고쳐 쓰려면 그 확인 요청 메시지에 답장하세요."
            )
        return "\n".join(lines)

    def recent_text(self, limit: int = 10) -> str:
        posts = self.db.recent_posts(limit)
        tz = self.settings.app.timezone
        lines = ["📤 <b>최근에 올린 글</b>" + (" (연습 모드라 미리보기만 보낸 것)" if self.state.dry_run else "")]
        for r in posts:
            when = fmt_local(datetime.fromisoformat(r["posted_at"]), tz)
            lines.append(f"• {when} · {html.escape(self.collector_label(r['source']))} · {html.escape(truncate(r.get('name') or r['product_id'], 50))} · {r['price']:,}원")
        if not posts:
            lines.append("(아직 없음)")
        return "\n".join(lines)

    def errors_text(self, limit: int = 8) -> str:
        events = self.db.recent_events(limit, level="ERROR")
        tz = self.settings.app.timezone
        lines = ["🚨 <b>최근 에러</b> (재시작 전 것도 포함)"]
        for e in events:
            when = fmt_local(datetime.fromisoformat(e["ts"]), tz)
            lines.append(f"• {when} · {html.escape(describe_kind(e['kind'], self.labels))}\n   <code>{html.escape(truncate(e['message'].splitlines()[0], 160))}</code>")
        if not events:
            lines.append("(없음 — 깨끗합니다)")
        return "\n".join(lines)

    def community_stats_text(self) -> str:
        lines = ["📈 <b>게시판별 글 통계</b> — 글이 추천을 얼마나 받는지 보고, 기준이 너무 높거나 낮은지 판단하는 용도"]
        for label, hours in (("최근 24시간", 24), ("최근 7일", 24 * 7)):
            stats = self.db.community_stats(utcnow() - timedelta(hours=hours))
            lines.append(f"\n<b>{label}</b>")
            if not stats:
                lines.append("(아직 데이터 없음)")
            for src, st in stats.items():
                ge = st["rec_ge"]
                lines.append(
                    f"• {html.escape(self.collector_label(src))}: 글 {st['posts']}개 중 추천 1개↑ {ge[1]} · 3개↑ {ge[3]} · 5개↑ {ge[5]} · 10개↑ {ge[10]} · 20개↑ {ge[20]}"
                    f" · 조회 500↑ {st.get('views_ge_500', 0)} · 댓글 3개↑ {st.get('comments_ge_3', 0)}"
                )
        ic = self.settings.deal.interest
        lines.append(
            f"\n지금 기준: 추천 {ic.min_recommend}개 이상, 댓글 {ic.min_comments}개 이상, 조회 {ic.min_views} 이상, 순위 {ic.max_rank}위 안 중 하나면 판정 대상이 되고,"
            f" 가격이 적힌 글은 커뮤니티 추천 {self.settings.deal.community_min_recommend}개 이상이면 특가로 봅니다 (쿠팡 최저가 비교가 켜지면 그쪽이 우선)"
        )
        return "\n".join(lines)

    def hot_text(self, min_recommend: int = 5, hours: int = 24) -> str:
        tz = self.settings.app.timezone
        items = self.db.hot_items(utcnow() - timedelta(hours=hours), min_recommend=min_recommend)
        lines = [f"🔥 <b>최근 {hours}시간에 추천 {min_recommend}개 이상 받은 글</b> ({len(items)}건)\n"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz)
            block = (
                f"<b>{html.escape(truncate(it['title'] or '', 70))}</b>\n"
                f"{html.escape(self.collector_label(it['source']))} · {when} · 추천 {it['recommend']} · 조회 {it['views'] or '-'} · 댓글 {it['comments'] or '-'}"
            )
            if it.get("url"):
                block += f"\n원문: {html.escape(str(it['url']))}"
            lines.append(block + "\n")
        if not items:
            lines.append("(없음 — 아직 게시판을 안 봤거나, 그만큼 추천받은 글이 없습니다. 숫자를 낮춰 보세요: /hot 2)")
        return "\n".join(lines).rstrip()

    def find_text(self, keyword: str) -> str:
        tz = self.settings.app.timezone
        items = self.db.find_items(keyword)
        lines = [f"🔎 <b>'{html.escape(keyword)}' 가 들어간 글</b> ({len(items)}건)\n"]
        for it in items:
            when = fmt_local(datetime.fromisoformat(it["first_seen_at"]), tz, "%m/%d %H:%M")
            rec = it["recommend"] if it["recommend"] is not None else "-"
            block = (
                f"<b>{html.escape(truncate(it['title'] or '', 70))}</b>\n"
                f"{html.escape(self.collector_label(it['source']))} · 처음 본 시각 {when} · 추천 {rec}"
            )
            if it.get("url"):
                block += f"\n원문: {html.escape(str(it['url']))}"
            lines.append(block + "\n")
        if not items:
            lines.append("(수집한 글 중에는 없습니다)")
        return "\n".join(lines).rstrip()

    def summary_text(self, summary: PeriodSummary) -> str:
        return self.renderer.render("daily_summary.j2", s=summary, tz=self.settings.app.timezone)

    def heartbeat_text(self, minutes: int) -> str:
        """조용할 때 보내는 짧은 생존 신고 (최근 N분 동안 한 일)."""
        now = utcnow()
        s = self.db.summary(now - timedelta(minutes=minutes), now)
        counts = self.db.queue_counts()
        mode = " · DRY-RUN" if self.state.dry_run else ""
        paused = " · ⏸ 일시정지" if self.state.paused else ""
        span = f"{minutes // 60}시간" if minutes % 60 == 0 else f"{minutes}분"
        mode = " · 🧪 연습 모드" if self.state.dry_run else ""
        paused = " · ⏸ 일시정지 중" if self.state.paused else ""
        dup = max(s.deals_found - s.queued, 0)
        # 같은 글을 5분마다 다시 확인하므로 이 숫자는 글 개수가 아니라 '판정 횟수' 다
        dup_note = f" (이미 올린 글이라 그냥 지나친 판정 {dup}번 — 같은 글을 5분마다 다시 보기 때문에 글 수보다 큽니다)" if dup else ""
        fails = f" (확인 실패 {s.run_errors}번)" if s.run_errors else ""
        pending = counts.get("pending", 0)
        awaiting = counts.get("awaiting_link", 0)
        if pending or awaiting:
            waiting = f"{pending}건 발행 차례 기다리는 중" + (f" · {awaiting}건 내 링크 기다리는 중 (/pending)" if awaiting else "")
        else:
            waiting = "없음"
        posted_label = "미리보기로 보낸 글" if self.state.dry_run else "채널에 올린 글"
        lines = [
            f"🐥 <b>봇이 잘 돌고 있습니다</b> · 켜진 지 {humanize_delta(now - self.state.started_at)}{mode}{paused}",
            f"지난 {span} 동안 게시판을 {s.runs}번 확인해서{fails} 글 {s.collected}개를 봤습니다.",
            f"새로 잡은 특가: {s.queued}건{dup_note}",
            f"{posted_label}: {s.published}건",
            f"지금 기다리는 글: {waiting}",
        ]
        nxt = [
            f"{self.collector_label(st.name)} {humanize_delta(st.next_run_at - now) + ' 뒤' if st.next_run_at and st.next_run_at > now else '곧'}"
            for st in self.state.collectors.values()
            if st.enabled and st.available
        ]
        if nxt:
            lines.append("다음 확인: " + " · ".join(nxt))
        if s.errors:
            lines.append(f"🚨 에러 {s.errors}건 — /errors 로 확인")
        if s.queued == 0 and s.published == 0:
            lines.append(f"특가가 없으면 조용한 것이 정상입니다. {span} 동안 아무 소식이 없을 때만 이 메시지를 보냅니다.")
        return "\n".join(lines)


class BotController(Protocol):
    """관리자 명령이 조작하는 인터페이스 (app.DealBot 이 구현)."""

    state: BotState

    def pause(self) -> None: ...

    def resume(self) -> None: ...

    def request_run(self, collector: str | None) -> str: ...

    def collector_names(self) -> list[str]: ...

    async def attach_link(self, queue_id: int, url: str, *, photo: PhotoSource | None = None) -> str: ...

    def skip_item(self, queue_id: int) -> str: ...

    async def mark_posted(self, queue_id: int, link: str = "") -> str: ...

    def approve_item(self, queue_id: int, text: str | None = None) -> str: ...

    async def refresh_pending_notice(self) -> None: ...

    async def submit_manual(self, text: str) -> str: ...

    async def blog_digest(self, *, preview: bool = False) -> str: ...

    def relay_to_blog(self, update: dict) -> bool | None: ...

    async def test_post(self) -> str: ...

    async def threads_test(self, queue_id: int | None = None) -> str: ...

    async def push_test(self) -> str: ...

    async def naver_login(self) -> tuple[bytes | None, str]: ...

    async def naver_login_wait(self) -> str: ...

    async def screenshot(self, url: str) -> tuple[bytes | None, str]: ...

    async def fetch_html(self, url: str) -> tuple[bytes | None, str]: ...

    async def threads_auth_url(self) -> str: ...

    async def threads_submit_code(self, code: str) -> str: ...

    async def threads_submit_token(self, token: str) -> str: ...

    async def instagram_auth_url(self) -> str: ...

    async def instagram_submit_code(self, code: str) -> str: ...

    async def instagram_submit_token(self, token: str) -> str: ...

    async def instagram_test(self, queue_id: int | None = None) -> str: ...

    async def send_copy_blocks(self, queue_id: int | None = None) -> str: ...

    async def naver_link_test(self, url: str) -> str: ...

    async def channel_guide(self) -> str: ...

    async def invite_links(self) -> str: ...

    def late_photo_waiting(self, queue_id: int) -> bool: ...

    async def attach_late_photo(self, queue_id: int, photo: PhotoSource) -> str: ...


HELP_NL = (
    "💬 <b>이제 말로 하셔도 돼요</b>\n"
    "· 링크 요청(🔗 #번호)에 답장으로 링크만 보내면 지금처럼 바로 붙여서 올려요\n"
    "· 네이버 딜은 가격·옵션을 확인한 뒤 상품 사진을 링크 요청에 답장으로 보내며 설명 칸에 링크를 넣으면 그 사진도 같이 써요\n"
    "· \"675번 올리지 마\", \"677 정품 맞아 올려\", \"대기열 보여줘\", \"딜봇 잠깐 멈춰\"\n"
    "· 블로그(demiyum) 일도 여기서: \"블로그 12번 발행해\", \"블로그 상태 어때?\" — 블로그 프로그램이 받아서 처리해요\n"
    "· 채널에 올라가거나 건너뛰는 일은 버튼으로 한 번 물어봐요. 조회는 바로 해요\n"
    "· '멈춰'처럼 딜봇인지 블로그인지 애매하면 물어봐요\n"
    "· 링크와 말을 같이 보내면 예전엔 바로 채널에 올라갔지만, 이제는 먼저 물어봐요\n\n"
)

HELP_TEXT = HELP_NL + (
    "🤖 <b>명령어 안내</b>\n"
    "\n<b>상태 보기</b>\n"
    "/status — 지금 상태 한눈에. 게시판 확인 현황, 올린 글 수, 기다리는 글, 쇼핑몰별 링크 처리 방식.\n"
    "/queue — 올릴 차례를 기다리는 글. 글마다 #번호가 붙어 있고, /skip 에 이 번호를 씁니다.\n"
    "/pending — 내가 링크를 만들어 줘야 하는 글. 토스·네이버처럼 자동 링크가 안 되는 몰의 딜이 여기 쌓입니다.\n"
    "/recent — 최근에 올린 글.\n"
    "/hot 5 — 최근 24시간에 추천 5개 이상 받은 게시판 글과 원문 링크. 숫자를 바꾸면 기준이 바뀝니다 (/hot 2).\n"
    "/find 키워드 — 수집한 글 제목에서 찾기. 어느 게시판에 언제 올라왔는지와 원문 링크.\n"
    "/errors — 최근 에러.\n"
    "/ppstats — 게시판별 추천 수 분포. 판정 기준이 너무 높거나 낮은지 볼 때.\n"
    "\n<b>글 올리기·다루기</b>\n"
    "/link 번호 링크 — 봇이 '내 링크가 필요합니다 #번호' 를 보내면, 그 몰 앱에서 제휴 링크를 만들어 이 명령으로 붙입니다. 그 메시지에 답장으로 링크만 보내도 됩니다. 붙이는 순간 채널에 올라갑니다. "
    "네이버 딜은 휴대폰으로 가격·옵션을 확인한 뒤 상품 사진의 설명 칸에 이 명령을 적어 보내면(또는 링크 요청 메시지에 사진을 답장하며 설명 칸에 링크) 그 사진이 채널·블로그·인스타 상품 사진이 됩니다 (JPG·PNG·WEBP, 10MB 이하, 짧은 변 600px 이상 — 화면 캡처는 상품 부분만 잘라서). 앨범이면 첫 사진만 씁니다. 사진 없이 링크만 붙이면 채널엔 글만, 인스타는 상품명·가격 글자 카드로 올라갑니다 (스토어·게시판 사진은 쓰지 않음).\n"
    "/skip 번호 — 그 글은 올리지 않고 건너뜁니다. 품절이거나 별로일 때. 번호는 링크 요청 메시지나 /queue 에 있습니다.\n"
    "/posted 번호 [채널 글 링크] — 봇이 '채널에 올라갔는지 모릅니다 #번호' 를 보냈는데 채널에 그 글이 있으면 보냅니다. 올라간 글로 기록하고 블로그·스레드·인스타로 이어서 올립니다 (채널 글 링크를 붙이면 품절 표시도 됩니다). 그 메시지에 답장으로 채널 글 링크(t.me)만 보내도 됩니다. 채널에 없으면 /link 번호 상품 링크 로 다시 올립니다 (텔레그램 주소는 상품 링크로 붙지 않습니다).\n"
    "/ok 번호 — 봇이 '정품 확인 필요 #번호'(오픈마켓인데 공식 판매처 표시가 없음) 나 '정보 글 확인 #번호' 를 보내면, 확인했다는 뜻으로 보냅니다. 정품 확인이면 링크를 만들어 올리고, 정보 글이면 그대로 올립니다. 정보 글은 그 메시지에 답장으로 본문을 새로 써 보내면 그 내용으로 올라갑니다.\n"
    "/post — 내가 찾은 딜을 직접 올립니다. 아래처럼 보내면 맨 앞 차례로 채널에 올라갑니다 (연습 모드에서는 미리보기만).\n"
    "<code>/post\n[머리글, 없으면 생략]\n상품: 상품명\n가격: 14,890원\nhttps://내가-만든-제휴-링크</code>\n"
    "/copy 번호 — 올린 글의 카카오 오픈채팅용·네이버 블로그용 복붙 문구를 다시 받습니다. 번호 없으면 마지막 글. (자동 전송은 꺼 둠 — 블로그·카톡은 블로그 자동화가 처리)\n"
    "\n<b>블로그 (demiyum, 자동 발행)</b>\n"
    "/blog — 블로그 글 미리보기 (모음글·단일 딜 글, 올리지 않음)\n"
    "/blognow — 지금 모인 딜로 모음글 1편\n"
    "/blogmode draft|publish — 임시저장만 / 완전 자동 발행\n"
    "/blogpause · /blogresume — 블로그 자동 작업 멈춤·재개\n"
    "/blogposts — 블로그 최근 글 · /blogstatus — 블로그 상태 · /bloglogin — 네이버 로그인 확인\n"
    "/blogpublish 번호 — 저장된 글 발행 · /blogretry 번호 — 실패한 글 다시 올리기 · /blogdrafts — 임시저장함 정리\n"
    "/blogsubmit — 구글 노출 등록(키자드) 기록 · /blogsubmit 번호 — 그 글 다시 등록\n"
    "/kakao — 카카오톡 나에게 보내기 연결 (/kakao test 로 확인)\n\n"
    "/run — 지금 바로 게시판을 확인합니다. /run ppomppu 처럼 하나만도 됩니다.\n"
    "/pause — 잠시 멈춤 (게시판 확인과 올리기 모두). /resume — 다시 시작.\n"
    "\n<b>확인·연결</b>\n"
    "/test — 샘플 딜로 채널에 올라갈 글 양식을 이 챗에 보여줍니다. 양식을 바꿨을 때 확인용이고 채널에는 안 올라갑니다.\n"
    "/threadstest — 샘플 딜을 스레드에 실제로 올려 어떻게 보이는지 확인합니다 (본 뒤 스레드 앱에서 삭제). /threadstest 번호 — 채널에 올렸던 그 글을 스레드에 다시 올립니다.\n"
    "/pushtest — 휴대폰 푸시(ntfy) 연결 확인.\n"
    "/threadsauth — 스레드 자동 게시 연결. Threads 앱 ID·시크릿을 변수에 넣은 뒤 1회. Railway 도메인이 있으면 승인만 누르면 끝, 없으면 /threadscode 코드 로 인증 코드 입력. 승인 화면 로그인이 계속 돌면 /threadstoken 토큰 — 메타 앱 Threads API 설정의 사용자 토큰 생성기로 만든 토큰으로 연결(토큰 메시지는 봇이 지움).\n"
    "/instaauth — 인스타그램 자동 게시 연결 (프로페셔널 계정, Instagram 앱 ID·시크릿을 변수에 넣은 뒤 1회). /instacode 코드 — 수동 연결 때 인증 코드 입력. /instatest — 샘플 딜을 인스타에 실제로 올려 카드·본문 확인. 승인 화면 로그인이 계속 돌면 /instatoken 토큰 — Instagram API 설정의 액세스 토큰 생성으로 만든 토큰으로 연결(토큰 메시지는 봇이 지움).\n"
    "\n<b>채널 키우기</b>\n"
    "/channelguide — 텔레그램 채널에 짧은 안내 글(인스타·스레드·블로그 링크)을 올리고 맨 위에 고정합니다. 보낼 때만 올라갑니다.\n"
    "/invitelinks — 이름 붙은 채널 초대 링크(인스타 프로필 ig_bio · 스레드 프로필 threads_bio · 핫딜 페이지 deals_page · 블로그 demiyum · qr)를 만들어 보여 줍니다. 어디서 들어왔는지 세서 대시보드 텔레그램 카드에 나옵니다 (봇에 '링크로 초대' 권한 필요).\n"
    "📷 사진 필요 #번호 — 확인된 상품 사진 없이 올라간 딜이면 봇이 보냅니다. 4시간 안에 그 메시지에 사진을 답장(또는 사진 설명 칸에 /photo 번호)하면 블로그·인스타에 그 사진을 씁니다.\n"
    "\n<b>확인·연결 (계속)</b>\n"
    "/naverlogin — 네이버 쇼핑커넥트 링크를 봇이 대신 만들도록 서버 브라우저에 QR 로그인. 블로그 글쓰기가 아니라 링크 생성 자동화이고, 브라우저 자동화를 켰을 때만 됩니다.\n"
    "/naverlink 상품URL — 위 자동화로 링크 하나 만들어 보기.\n"
    "/shot URL — 서버 브라우저로 그 페이지 화면을 찍어 보냅니다. 봇이 게시판을 잘못 읽는 것 같을 때 확인용.\n"
    "/html URL — 그 페이지 원문을 파일로 받습니다. 게시판 구조가 바뀌어 봇이 못 읽을 때 저에게 보내주는 용도.\n"
    "/help — 이 안내"
)


def register_admin_handlers(
    app: Application,  # type: ignore[type-arg]
    *,
    admin_chat_id: int | str,
    reporter: StatusReporter,
    controller: BotController,
    nl: nlmod.NLRouter | None = None,
) -> None:
    chat_id = normalize_chat_id(admin_chat_id)
    only_admin = filters.Chat(chat_id=chat_id) if isinstance(chat_id, int) else filters.Chat(username=str(chat_id).lstrip("@"))

    def _track(sent: Any, text: str = "", kind: str = "") -> Any:
        """관리자 챗에 보낸 메시지를 기록한다 (답장 대상이 딜봇 메시지인지 Blog-Auto 메시지인지 가리려고).
        이 파일의 reply_text·reply_photo·reply_document 는 모두 이것을 거친다 (테스트가 확인)."""
        if nl is not None and sent is not None and sent is not SEND_FAILED:
            nl.record(getattr(sent, "message_id", None), text, kind)
        return sent

    async def reply(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        msg = update.effective_message
        if msg is not None:
            extra = {"reply_markup": markup} if markup is not None else {}
            await send_chunks(msg.reply_text, text, on_sent=lambda m: _track(m, text), **extra)

    async def say(update: Update, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        """말 명령의 답 (주인이 쓴 말·AI 가 쓴 말이 섞여 있어 HTML 로 해석되지 않게)."""
        await reply(update, html.escape(text, quote=False), markup)

    async def drop_stale(update: object, _: ContextTypes.DEFAULT_TYPE) -> None:
        # 봇이 꺼져 있던 동안 쌓인 오래된 명령은 실행하지 않는다 (재배포 직후 옛 /run, /post 가 다시 도는 것 방지)
        # 버튼은 예외: 버튼의 effective_message 는 버튼이 달린 (오래전에 보낸) 메시지라 5분 넘은 버튼이 조용히 버려졌다
        if getattr(update, "callback_query", None) is not None:
            return
        msg = getattr(update, "effective_message", None)
        if msg is not None and is_stale_message(getattr(msg, "date", None)):
            # 명령 이름만 남긴다 (/threadstoken 처럼 뒤에 비밀 값이 붙는 명령이 있음). 명령이 아닌 글은 첫 낱말도 안 남김 (토큰만 붙여넣은 글)
            words = (getattr(msg, "text", "") or "").split() or [""]
            shown = words[0][:40] if words[0].startswith("/") else "(명령 아닌 글)"
            log.info("ignoring stale command from %s: %s", getattr(msg, "date", None), shown)
            if len(words) > 1 and words[0].split("@", 1)[0].lower() in SECRET_COMMANDS and only_admin.filter(msg):
                await _drop_stale_token(msg)
            raise ApplicationHandlerStop

    async def _drop_stale_token(msg: Any) -> None:
        """봇이 멈춰 있던 동안 온 토큰 명령: 실행은 안 하지만 토큰이 든 메시지는 지운다 (_token_cmd 까지 가지 않으므로 여기서)."""
        try:
            await msg.delete()
            note = "봇이 멈춰 있던 동안 온 토큰 명령이라 연결하지 않았어요. 토큰이 든 메시지는 지웠어요 — 다시 보내 주세요."
        except Exception as e:  # noqa: BLE001 — 못 지워도 알림은 보냄 (48시간 넘은 메시지는 봇이 못 지움)
            log.warning("stale token command: could not delete the owner's message: %s", type(e).__name__)
            note = "봇이 멈춰 있던 동안 온 토큰 명령이라 연결하지 않았어요. 토큰이 든 메시지를 지우지 못했어요 — 직접 지우고 다시 보내 주세요."
        try:
            await msg.chat.send_message(note)
        except Exception as e:  # noqa: BLE001
            log.warning("stale token command: notice not sent: %s", type(e).__name__)

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.status_text())

    async def cmd_queue(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.queue_text())

    async def cmd_pending(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.pending_text())

    async def cmd_recent(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.recent_text())

    async def cmd_errors(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.errors_text())

    async def cmd_pause(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.pause()
        await reply(update, "⏸ 잠시 멈췄습니다. 게시판 확인과 채널에 올리기를 모두 멈춥니다. /resume 으로 다시 시작")

    async def cmd_resume(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        controller.resume()
        await reply(update, "▶️ 다시 시작했습니다")

    async def cmd_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        name = ctx.args[0] if ctx.args else None
        await reply(update, controller.request_run(name))

    async def cmd_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if len(args) < 2 or not args[0].lstrip("#").isdigit():
            await reply(update, LINK_USAGE)
            return
        photo = getattr(ctx, "photo", None)
        if photo is not None:  # 사진과 같이 보낸 말 명령을 버튼으로 확인한 뒤 (사진은 file_id 로 넘어옴)
            await link_with_photo(update, ctx, int(args[0].lstrip("#")), args[1], photo)
            return
        await reply(update, await controller.attach_link(int(args[0].lstrip("#")), args[1]))
        await controller.refresh_pending_notice()

    # ---------------------------------------------------------------- 링크와 같이 보낸 상품 사진 (네이버 딜, 주인 결정 10/15)
    def photo_of(md: dict[str, Any]) -> dict[str, Any]:
        """메시지(to_dict)의 사진: {id, size} — 사진이 아니거나 안 받는 형식(HEIC·GIF·동영상 파일)이면 {problem: 이유}."""
        sizes = md.get("photo") or []
        if sizes:
            best = sizes[-1]  # 텔레그램은 작은 크기부터 준다 — 마지막이 가장 큰 사진
            return {"id": best.get("file_id"), "size": best.get("file_size")}
        doc = md.get("document") or {}
        if str(doc.get("mime_type") or "").lower() in OWNER_PHOTO_MIMES:
            return {"id": doc.get("file_id"), "size": doc.get("file_size")}
        return {"problem": OWNER_PHOTO_NOT_IMAGE}

    def first_photo(group: dict[str, Any] | None, md: dict[str, Any]) -> dict[str, Any]:
        """앨범이면 첫 사진 (설명 없이 먼저 온 사진이 있으면 그것), 아니면 이 메시지의 사진."""
        held = (group or {}).get("held") or []
        return photo_of(held[0] if held else md)

    async def download_photo(bot: Any, ref: dict[str, Any]) -> tuple[bytes | None, str]:
        """주인이 보낸 사진 받기: 받기 전에 크기(10MB), 받은 뒤 길이를 다시 본다. 못 받으면 (None, 이유)."""
        if ref.get("problem"):
            return None, str(ref["problem"])
        size = ref.get("size")
        if isinstance(size, int) and size > OWNER_PHOTO_MAX_BYTES:
            return None, OWNER_PHOTO_TOO_BIG
        if bot is None or not ref.get("id"):
            return None, PHOTO_NOT_RECEIVED
        try:
            f = await bot.get_file(str(ref["id"]))
            data = bytes(await f.download_as_bytearray())
        except Exception as e:  # noqa: BLE001 — 텔레그램 오류·시간 초과: 링크도 안 붙이고 다시 보내 달라고
            log.warning("owner photo download failed: %s", type(e).__name__)
            return None, PHOTO_NOT_RECEIVED
        if len(data) > OWNER_PHOTO_MAX_BYTES:
            return None, OWNER_PHOTO_TOO_BIG
        return data, ""

    async def link_with_photo(update: Update, ctx: Any, queue_id: int, url: str, ref: dict[str, Any]) -> None:
        """링크 + 상품 사진. 사진은 받아 오는 함수로 넘긴다 — 딜봇이 링크를 붙일 수 있는 네이버 상품 딜인 걸 확인한 뒤에만 받는다
        (없는 번호·채널 글 주소·토스 딜이면 받지도 거절하지도 않음). 사진을 못 쓰면 링크도 붙이지 않고 이유를 알린다 (다시 보내면 됨)."""
        bot = getattr(ctx, "bot", None)

        async def load() -> tuple[bytes | None, str]:
            return await download_photo(bot, ref)

        await reply(update, await controller.attach_link(queue_id, url, photo=load))
        await controller.refresh_pending_notice()

    async def cmd_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].lstrip("#").isdigit():
            await reply(update, "이렇게 보내주세요: <code>/skip 12</code> (번호는 /queue 나 링크 요청 메시지의 #번호)")
            return
        await reply(update, controller.skip_item(int(args[0].lstrip("#"))))
        await controller.refresh_pending_notice()

    async def cmd_posted(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].lstrip("#").isdigit():
            await reply(update, "이렇게 보내주세요: <code>/posted 12</code> 또는 <code>/posted 12 https://t.me/채널/글번호</code> "
                                "(번호는 '채널에 올라갔는지 모릅니다 #번호' 메시지의 번호)")
            return
        await reply(update, await controller.mark_posted(int(args[0].lstrip("#")), args[1] if len(args) > 1 else ""))

    async def cmd_ok(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].lstrip("#").isdigit():
            await reply(update, "이렇게 보내주세요: <code>/ok 12</code> (번호는 '정보 글 확인 #번호' 메시지의 번호)")
            return
        await reply(update, controller.approve_item(int(args[0].lstrip("#"))))
        await controller.refresh_pending_notice()

    async def cmd_post(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.effective_message.text if update.effective_message else ""
        await reply(update, await controller.submit_manual(text or ""))

    async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, HELP_TEXT)

    async def cmd_test(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.test_post())

    async def cmd_instaauth(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.instagram_auth_url())

    async def cmd_instacode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await reply(update, "이렇게 보내주세요: <code>/instacode 코드값</code>")
            return
        await reply(update, await controller.instagram_submit_code(args[0]))

    async def cmd_instatest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        qid = int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdigit() else None
        await reply(update, await controller.instagram_test(qid))

    async def cmd_threadstest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        qid = int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdigit() else None
        await reply(update, await controller.threads_test(qid))

    async def cmd_pushtest(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.push_test())

    async def cmd_threadsauth(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.threads_auth_url())

    async def _token_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE, usage: str, submit: Any) -> None:
        """토큰을 받아 연결하고, 토큰이 든 주인 메시지는 지운다 (채팅에 남지 않게)."""
        args = ctx.args or []
        if not args:
            await reply(update, usage)
            return
        msg = update.effective_message
        if msg is not None:
            try:
                await msg.delete()
            except Exception as e:  # noqa: BLE001 — 못 지워도 연결은 진행
                log.warning("token command: could not delete the owner's message: %s", e)
        text = await submit(args[0])
        if msg is not None:
            await msg.chat.send_message(text + "\n(토큰이 든 메시지는 지웠어요)", parse_mode="HTML")

    async def cmd_threadstoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _token_cmd(update, ctx, "이렇게 보내주세요: <code>/threadstoken 토큰값</code> (메타 앱 → Threads API → 설정 맨 아래 '사용자 토큰 생성기')",
                         controller.threads_submit_token)

    async def cmd_instatoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _token_cmd(update, ctx, "이렇게 보내주세요: <code>/instatoken 토큰값</code> (메타 앱 → Instagram → API 설정의 '액세스 토큰 생성')",
                         controller.instagram_submit_token)

    async def cmd_threadscode(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args:
            await reply(update, "이렇게 보내주세요: <code>/threadscode 코드값</code>")
            return
        await reply(update, await controller.threads_submit_code(args[0]))

    async def cmd_copy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        qid = int(args[0].lstrip("#")) if args and args[0].lstrip("#").isdigit() else None
        await reply(update, await controller.send_copy_blocks(qid))

    async def cmd_blog(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.blog_digest(preview=True))

    async def cmd_ppstats(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, reporter.community_stats_text())

    async def cmd_hot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        n = int(args[0]) if args and args[0].isdigit() else 5
        await reply(update, reporter.hot_text(min_recommend=n))

    async def cmd_find(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        kw = " ".join(ctx.args or []).strip()
        if not kw:
            await reply(update, "이렇게 보내주세요: <code>/find 펩시제로</code>")
            return
        await reply(update, reporter.find_text(kw))

    async def _send_photo(update: Update, data: bytes | None, caption: str) -> None:
        msg = update.effective_message
        if msg is None:
            return
        if data:
            _track(await msg.reply_photo(photo=data, caption=caption[:1000]), caption)
        else:
            _track(await msg.reply_text(caption[:4096]), caption)

    async def cmd_naverlogin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        data, text = await controller.naver_login()
        await _send_photo(update, data, text)
        if data:
            async def _wait() -> None:
                result = await controller.naver_login_wait()
                if update.effective_message:
                    _track(await update.effective_message.reply_text(result), result)

            ctx.application.create_task(_wait())

    async def cmd_shot(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/shot https://...</code>")
            return
        data, text = await controller.screenshot(args[0])
        await _send_photo(update, data, text)

    async def cmd_html(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/html https://www.algumon.com/n/deal</code>")
            return
        data, text = await controller.fetch_html(args[0])
        msg = update.effective_message
        if msg is None:
            return
        if data:
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", args[0].split("//", 1)[-1])[:60] + ".html"
            _track(await msg.reply_document(document=data, filename=name, caption=f"{text}\n이 파일을 Claude 에게 올려주면 셀렉터를 맞춥니다."), text)
        else:
            _track(await msg.reply_text(text), text)

    async def cmd_channelguide(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.channel_guide())

    async def cmd_invitelinks(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await reply(update, await controller.invite_links())

    async def late_photo_reply(update: Update, ctx: Any, queue_id: int, ref: dict[str, Any]) -> None:
        """발행 뒤 받는 딜 사진 (📷 사진 필요 #번호 에 답장, 또는 설명 칸 '/photo 번호'). 사진은 받아 오는 함수로 넘긴다."""
        bot = getattr(ctx, "bot", None)

        async def load() -> tuple[bytes | None, str]:
            return await download_photo(bot, ref)

        await reply(update, await controller.attach_late_photo(queue_id, load))

    async def cmd_naverlink(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        if not args or not args[0].startswith("http"):
            await reply(update, "사용법: <code>/naverlink https://smartstore.naver.com/...</code>")
            return
        await reply(update, await controller.naver_link_test(args[0]))

    async def batch_lines(update: Update, text: str) -> bool:
        """고정 공지에 한 번에 답하기: "번호 링크" / "번호 ok" / "번호 skip" 줄들. 처리했으면 True."""
        rows = [ln for ln in text.splitlines() if ln.strip()]
        parsed = [m for m in (_BATCH_LINE_RE.match(ln) for ln in rows) if m]
        if not rows or len(parsed) != len(rows):
            return False
        answers: list[str] = []
        for m in parsed:
            qid, action = int(m.group(1)), m.group(2)
            if action.lower().startswith("http"):
                answers.append(await controller.attach_link(qid, action))
            elif action.lower() in _BATCH_OK:
                answers.append(controller.approve_item(qid))
            else:
                answers.append(controller.skip_item(qid))
        await reply(update, "\n".join(answers))
        await controller.refresh_pending_notice()
        return True

    async def on_text_legacy(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """말 명령을 끈 경우의 예전 동작: (1) 링크 요청에 답장 → 링크 붙이기, (2) URL 포함 메시지 → 직접 발행."""
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        urls = find_urls(msg.text)
        if await batch_lines(update, msg.text):
            return
        replied = msg.reply_to_message
        if replied is not None and replied.text:
            m = _QUEUE_REF_RE.search(replied.text)
            if m and replied.text.startswith(REVIEW_MARK):
                # 정보 글 확인 요청에 답장: 답장 내용을 본문으로 삼아 올린다
                await reply(update, controller.approve_item(int(m.group(1)), msg.text))
                await controller.refresh_pending_notice()
                return
            if m and urls:
                await reply(update, await controller.attach_link(int(m.group(1)), urls[0]))
                await controller.refresh_pending_notice()
                return
        if urls:
            await reply(update, await controller.submit_manual(msg.text))
            return
        await reply(update, "무엇을 할지 모르겠습니다. 링크 요청 메시지에 답장으로 링크를 보내거나, /help 로 명령어를 확인하세요.")

    # ---------------------------------------------------------------- 말 명령 (주인 요청 10/11)
    class _NLCtx:
        """말에서 바꾼 기존 명령을 기존 처리 함수에 넣을 때 쓰는 ctx (ctx.args 만 바꿔 끼움).
        photo: 말과 같이 보낸 상품 사진 ({id, size} 또는 {problem}) — /link 만 쓴다."""

        def __init__(self, ctx: Any, args: list[str], photo: dict[str, Any] | None = None) -> None:
            self.args = args
            self.application = getattr(ctx, "application", None)
            self.bot = getattr(ctx, "bot", None)
            self.photo = photo

    def _slash_name(text: str) -> str:
        head = (text or "").strip().split(maxsplit=1)
        return head[0][1:].split("@")[0].lower() if head and head[0].startswith("/") else ""

    def _late_photo_target(caption: str, ref: Any) -> int | None:
        """이 사진이 발행 뒤 받는 딜 사진이면 그 딜 번호: 📷 사진 필요 메시지에 답장(설명 없음·'/photo 번호'), 또는 설명 칸
        '/photo 번호' (블로그 메시지에 단 답장이 아니고, 뒤에 '자리N' 같은 말이 없고, 그 번호 딜이 지금 사진을 기다릴 때만 —
        블로그 글 번호와 헷갈리지 않게)."""
        name = _slash_name(caption)
        if ref.kind == "deal" and ref.sub == "photo_need" and ref.queue_id and (not caption.strip() or name in ("photo", "사진")):
            return int(ref.queue_id)
        if name in ("photo", "사진") and ref.kind != "blog":
            parts = caption.split()
            # '/photo 번호' 딱 두 마디만 — '/photo 12 자리3' 처럼 뒤에 더 붙은 건 블로그 사진 자리 바꾸기라 딜 번호와 겹쳐도 블로그로
            if len(parts) == 2 and parts[1].lstrip("#").isdigit():
                qid = int(parts[1].lstrip("#"))
                waiting = getattr(controller, "late_photo_waiting", None)
                if waiting is not None and waiting(qid):
                    return qid
        return None

    def _from_bot(msg: Any) -> bool:
        return bool(getattr(getattr(msg, "from_user", None), "is_bot", False))

    def _looks_like_body(text: str) -> bool:
        return len([ln for ln in text.splitlines() if ln.strip()]) >= 2 or len(text.strip()) >= 80

    def _base(msg: Any) -> dict[str, Any]:
        """버튼을 누른 뒤 블로그 별칭 명령을 넘길 때 쓸 챗·보낸 사람."""
        user = getattr(msg, "from_user", None)
        return {"chat": {"id": getattr(getattr(msg, "chat", None), "id", chat_id), "type": "private"},
                "from": user.to_dict() if user is not None and hasattr(user, "to_dict") else None}

    async def nl_exec(update: Update, ctx: Any, cmds: list[str], text: str, *, photo: dict[str, Any] | None = None) -> None:
        """바꾼 명령을 기존 처리 함수로 실행 (명령어로 보낸 것과 똑같이). /post 는 주인의 원래 글로 올린다.
        photo: 말과 같이 보낸 사진 — 첫 /link 에만 (그 딜의 상품 사진)."""
        for canonical in cmds:
            name, _, rest = canonical.partition(" ")
            if name == "/post":
                await reply(update, await controller.submit_manual(text))
                continue
            fn = command_fns.get(name.lstrip("/"))
            if fn is None:
                log.warning("nl: no handler for %s", name)
                continue
            mine = photo if name == "/link" else None
            if mine is not None:
                photo = None
            await fn(update, _NLCtx(ctx, rest.split(), mine))

    def _buttons(rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data)] for label, data in rows])

    async def nl_apply(update: Update, ctx: Any, d: nlmod.Decision, text: str, msg: Any, ref: nlmod.Ref, *, why: str = "router",
                       photo: dict[str, Any] | None = None) -> None:
        """photo: 사진과 같이 온 말 (설명 칸) — 버튼을 누를 때까지 file_id 로 들고 있다가 /link 에 쓴다."""
        assert nl is not None
        keep = {"photo": photo} if photo is not None else {}
        if d.blog_text and d.act != "blog":  # 섞인 메시지: 블로그 부분은 바로 넘김 (Blog-Auto 가 자기 일은 자기가 확인)
            nl.relay_nl(msg.to_dict(), why, text=d.blog_text, partial=True)
        if d.act == "blog":
            nl.relay_nl(msg.to_dict(), why, text=None if d.blog_text in ("", text) else d.blog_text)
        elif d.act in ("chat", "none"):
            await say(update, d.reply)
        elif d.act == "fail":
            if d.reply:
                await say(update, d.reply)
            elif nl.wants_blog_anyway(text):
                nl.relay_nl(msg.to_dict(), why)
            else:
                await say(update, nl.suggest(text, ref))
        elif d.act == "ask":
            if not d.options:
                await say(update, d.reply)
                return
            opts = [{"label": o.label, "cmds": o.cmds, "relay": o.relay} for o in d.options[: nlmod.MAX_BUTTONS]]
            token = nl.put_pending({"options": opts, "text": text, **_base(msg), **keep})
            rows = [(o["label"], f"dnl:pick:{token}:{i}") for i, o in enumerate(opts)] + [("취소", f"dnl:no:{token}")]
            await say(update, d.reply, _buttons(rows))
        elif d.act == "confirm":
            token = nl.put_pending({"cmds": d.canonicals, "text": text, **_base(msg), **keep})
            ok = "올리기" if d.post_preview else "실행"
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(ok, callback_data=f"dnl:ok:{token}"),
                                        InlineKeyboardButton("취소", callback_data=f"dnl:no:{token}")]])
            await say(update, d.confirm_text(), kb)
        elif d.act == "exec":
            await say(update, d.echo())
            await nl_exec(update, ctx, d.canonicals, text, photo=photo)

    async def passthrough(update: Update, msg: Any, text: str) -> None:
        """딜봇에 없는 명령: 블로그 명령이면 Blog-Auto 로 그대로, 아니면 모른다고."""
        if nl is not None and _slash_name(text) in nlmod.BLOG_PASSTHROUGH:
            nl.relay_nl(msg.to_dict(), "passthrough")
            log.info("nl deal act=relay why=passthrough cmd=/%s", _slash_name(text))
            return
        await say(update, nlmod.UNKNOWN_COMMAND)

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """관리자의 일반 메시지. 정해진 흐름(일괄 줄·링크 답장·정보 글 본문 답장·블로그 메시지 답장)이 먼저, 나머지는 말 명령."""
        msg = update.effective_message
        if msg is None or not msg.text:
            return
        if nl is None or not nl.enabled:
            await on_text_legacy(update, ctx)
            return
        if _from_bot(msg):
            return
        text = msg.text
        if await batch_lines(update, text):
            return
        if text.lstrip().startswith("/"):  # 텔레그램이 명령으로 표시하지 않는 한글 명령 (/사진 …)
            await passthrough(update, msg, text)
            return
        ref = nl.reply_ref(msg)
        urls = find_urls(text)
        if ref.kind == "deal" and ref.queue_id:
            info = ref.sub == "review_info" or nlmod.strip_html(ref.text).lstrip().startswith(REVIEW_MARK)
            if info and _looks_like_body(text):
                # 정보 글 확인 요청에 본문을 새로 써서 답장 → 그 본문으로 올림 (짧은 말은 본문으로 쓰지 않고 해석)
                await reply(update, controller.approve_item(ref.queue_id, text))
                await controller.refresh_pending_notice()
                return
            if urls:  # 링크 요청(🔗 #번호)에 링크 답장 → 바로 붙임 (주인이 정한 흐름)
                await reply(update, await controller.attach_link(ref.queue_id, urls[0]))
                await controller.refresh_pending_notice()
                return
        if ref.kind == "blog":  # Blog-Auto 가 보낸 메시지에 답장 → 그쪽 해석기가 처리 (#번호는 블로그 글 번호)
            nl.relay_nl(msg.to_dict(), "reply_blog")
            log.info("nl deal act=relay why=reply_blog")
            return
        if nl.is_chitchat(text, ref):
            await say(update, nlmod.CHAT_OK)
            return
        if not nl.rate_ok():
            await say(update, nlmod.RATE_LIMITED)
            return
        try:
            d = await nl.route(text, ref, msg_id=getattr(msg, "message_id", None))
            await nl_apply(update, ctx, d, text, msg, ref)
        except Exception:  # noqa: BLE001
            log.exception("nl router failed")
            await say(update, nlmod.ROUTER_BUG)

    async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """사진·파일. 블로그 글 사진(블로그 메시지에 답장·/photo 설명·말로 블로그 글)이면 Blog-Auto 로 통째로 넘긴다.
        앨범은 설명이나 답장이 있는 첫 사진이 정한 대로 나머지도 따라간다 (Blog-Auto 가 묶음으로 받게).
        네이버 딜 상품 사진 (주인 결정 10/15): 설명 칸이 '/link 번호 링크' 이거나 링크 요청(🔗)·딜봇 메시지에 답장하며 설명 칸에 링크
        ('번호 링크' 한 줄도 딜봇 메시지에 답장할 때만 — 고정 공지 일괄 답처럼) → 링크와 그 사진(앨범이면 첫 사진)을 같이 붙인다.
        앨범의 나머지 사진은 블로그로 넘기지 않고 버린다 (state 'deal')."""
        msg = update.effective_message
        if msg is None or nl is None or not nl.enabled or _from_bot(msg):
            return
        group = nl.group(getattr(msg, "media_group_id", None))
        md = msg.to_dict()
        if group is not None and group["state"] is not None:
            if group["state"] == "relay":
                nl.relay_nl(md, "media")
            return
        caption = msg.caption or ""
        ref = nl.reply_ref(msg)
        urls = find_urls(caption)
        # '12 https://…' 한 줄은 딜봇 메시지(고정 공지·링크 요청)에 답장한 사진만 /link 로 — 그냥 보낸 사진은 예전처럼 말 명령으로,
        # 블로그 메시지에 단 답장은 지금처럼 Blog-Auto 로 (거기서 '12 https://…' 는 블로그 글 번호일 수 있음, 검토 지적)
        line = _BATCH_LINE_RE.match(caption.strip()) if "\n" not in caption.strip() and ref.kind == "deal" else None
        state, why, text = "ignore", "media", None
        late = _late_photo_target(caption, ref)
        if late is not None:
            # 발행 뒤 4시간 안에 받는 딜 사진 (📷 사진 필요 #번호 에 답장 · 설명 칸 '/photo 번호' — 그 번호가 사진을 기다리는 딜일 때만,
            # 아니면 '/photo 번호' 는 예전처럼 블로그 명령으로 넘어감)
            if group is not None:
                group["state"] = "deal"
            state = "deal"
            await late_photo_reply(update, ctx, late, first_photo(group, md))
        elif _slash_name(caption) == "link" or (line and line.group(2).lower().startswith("http")):
            # '/link 12 https://…' (사진 설명은 텔레그램이 명령으로 보지 않아 여기로 옴) · 고정 공지처럼 '12 https://…'
            if group is not None:
                group["state"] = "deal"  # 사진을 받는 동안 온 같은 앨범의 사진도 블로그로 안 감
            state = "deal"
            parts = caption.split() if line is None else ["/link", line.group(1), line.group(2)]
            if len(parts) < 3 or not parts[1].lstrip("#").isdigit():
                await reply(update, LINK_USAGE)
            else:
                await link_with_photo(update, ctx, int(parts[1].lstrip("#")), parts[2], first_photo(group, md))
        elif caption.lstrip().startswith("/"):
            if _slash_name(caption) in nlmod.BLOG_MEDIA_COMMANDS:
                state, why = "relay", "passthrough"
            else:
                await say(update, nlmod.UNKNOWN_COMMAND)
        elif ref.kind == "blog":
            state, why = "relay", "reply_blog"
        elif ref.kind == "deal" and ref.queue_id and urls:
            # 링크 요청(🔗 #번호)에 사진을 답장하며 설명 칸에 링크 → 글자 답장(on_text)처럼 바로 붙임 + 그 사진
            if group is not None:
                group["state"] = "deal"
            state = "deal"
            await link_with_photo(update, ctx, ref.queue_id, urls[0], first_photo(group, md))
        elif not caption.strip():
            if group is not None:
                group["held"].append(md)  # 설명 없는 앞 사진: 설명 있는 사진이 오면 같이 처리
            elif ref.kind == "deal" and ref.queue_id and ref.sub in ("link", "link_done"):
                # 링크 요청·링크 완료에 사진만 답장: 붙일 링크가 없다 — 조용히 버리지 않고 링크를 설명 칸에 같이 넣으라고 (검토 지적)
                await reply(update, PHOTO_ONLY_REPLY.format(q=ref.queue_id))
            return  # 설명도 답장도 없는 사진은 예전처럼 무시
        elif not nl.rate_ok():
            await say(update, nlmod.RATE_LIMITED)
        else:
            doc = getattr(msg, "document", None)
            is_image = bool(getattr(msg, "photo", None)) or str(getattr(doc, "mime_type", "") or "").startswith("image/")
            try:
                d = await nl.route(caption, ref, photos=1 if is_image else 0,
                                   file="" if doc is None else str(getattr(doc, "file_name", "") or ""),
                                   msg_id=getattr(msg, "message_id", None))
            except Exception:  # noqa: BLE001
                log.exception("nl router failed (media)")
                d = nlmod.Decision("fail", reply=nlmod.ROUTER_BUG)
            if d.act == "blog" or (d.act == "fail" and not d.reply and nl.wants_blog_anyway(caption)):
                state, text = "relay", None if d.blog_text in ("", caption) else d.blog_text
            else:
                # 말로 보낸 링크 + 사진 (/link 로 알아들으면 확인 버튼 뒤 그 사진도 같이 붙임). 사진이 아닌 파일(PDF·동영상)은
                # 사진으로 넘기지 않는다 — 넘기면 '사진으로 열리지 않아요'로 링크까지 막힘 (HEIC 는 image/* 라 그 이유로 거절)
                await nl_apply(update, ctx, d, caption, msg, ref, why="media", photo=first_photo(group, md) if is_image else None)
        if group is not None:
            group["state"] = state
        if state == "relay":
            for held in (group or {}).get("held", []):
                nl.relay_nl(held, why)
            nl.relay_nl(md, why, text=text)
            log.info("nl deal act=relay why=%s media=%s", why, "album" if group is not None else "1")

    async def on_other_command(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """딜봇에 등록되지 않은 명령 (/fix /drop /publish …): 블로그 명령이면 Blog-Auto 로 그대로 넘긴다."""
        msg = update.effective_message
        if msg is None or _from_bot(msg):
            return
        await passthrough(update, msg, msg.text or "")

    async def on_nl_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """말 명령의 확인·고르기 버튼 (dnl:ok:T / dnl:no:T / dnl:pick:T:i). 한 번만 처리된다 (두 번째 누름은 '이미 처리')."""
        q = update.callback_query
        chat = update.effective_chat
        if q is None or nl is None or chat is None or not (
            chat.id == chat_id or (isinstance(chat_id, str) and chat.username == chat_id.lstrip("@"))
        ):
            return
        with contextlib.suppress(TelegramError):
            await q.answer("접수")
        parts = (q.data or "").split(":")
        if len(parts) < 3:
            return
        kind, token = parts[1], parts[2]
        status, p = nl.take_pending(token)
        with contextlib.suppress(TelegramError, AttributeError):
            await q.edit_message_reply_markup(reply_markup=None)  # 누른 버튼은 지운다
        if status == "gone" or p is None:
            await say(update, nlmod.EXPIRED if status == "expired" else nlmod.HANDLED)
            return
        if kind == "no":
            await say(update, nlmod.CANCELLED)
            return
        cmds: list[str] = list(p.get("cmds") or [])
        if kind == "pick":
            options = p.get("options") or []
            i = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else -1
            if not 0 <= i < len(options):
                await say(update, nlmod.HANDLED)
                return
            opt = options[i]
            if opt.get("relay"):
                nl.relay_alias(opt["relay"], p.get("chat") or {"id": chat_id}, p.get("from"))
                await say(update, f"📨 블로그 프로그램에 넘겼어요: {opt['relay']}")
                log.info("nl confirm token=%s pick relay=%s", token, opt["relay"])
                return
            cmds = list(opt.get("cmds") or [])
        log.info('nl confirm token=%s %s cmds="%s" age=%ds', token, kind, nlmod.log_text(" · ".join(cmds)),
                 int(time.time() - float(p.get("at") or time.time())))
        photo = p.get("photo")
        await nl_exec(update, ctx, cmds, str(p.get("text") or ""), photo=photo if isinstance(photo, dict) else None)

    async def cmd_unknown_chat(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and update.effective_message:
            await update.effective_message.reply_text(  # 기록 안 함: 관리자 챗이 아님
                f"이 봇은 개인 관리용입니다. (chat id: {update.effective_chat.id})"
            )

    async def relay(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        # 블로그(demiyum) 명령·버튼은 Blog-Auto 가 처리한다 (봇 하나를 두 프로그램이 같이 읽을 수 없어서 넘겨줌)
        chat = update.effective_chat
        if chat is None or not (chat.id == chat_id or (isinstance(chat_id, str) and chat.username == chat_id.lstrip("@"))):
            return  # 관리자 챗 것만
        if (getattr(update.callback_query, "data", None) or "").startswith("dnl:"):
            return  # 딜봇 말 명령 버튼은 여기서 처리 (on_nl_callback)
        controller.relay_to_blog(update.to_dict())

    for cmd in BLOG_RELAY_COMMANDS:
        app.add_handler(CommandHandler(cmd, relay, filters=only_admin))
    if nl is not None:
        app.add_handler(CallbackQueryHandler(on_nl_callback, pattern=r"^dnl:"))
    app.add_handler(CallbackQueryHandler(relay))

    command_fns: dict[str, Callable[..., Any]] = {}
    for cmd, fn in (
        ("status", cmd_status),
        ("queue", cmd_queue),
        ("pending", cmd_pending),
        ("recent", cmd_recent),
        ("errors", cmd_errors),
        ("pause", cmd_pause),
        ("resume", cmd_resume),
        ("run", cmd_run),
        ("link", cmd_link),
        ("skip", cmd_skip),
        ("posted", cmd_posted),
        ("ok", cmd_ok),
        ("post", cmd_post),
        ("test", cmd_test),
        ("threadstest", cmd_threadstest),
        ("pushtest", cmd_pushtest),
        ("threadsauth", cmd_threadsauth),
        ("threadscode", cmd_threadscode),
        ("threadstoken", cmd_threadstoken),
        ("instaauth", cmd_instaauth),
        ("instacode", cmd_instacode),
        ("instatoken", cmd_instatoken),
        ("instatest", cmd_instatest),
        ("copy", cmd_copy),
        ("ppstats", cmd_ppstats),
        ("hot", cmd_hot),
        ("find", cmd_find),
        ("naverlogin", cmd_naverlogin),
        ("naverlink", cmd_naverlink),
        ("channelguide", cmd_channelguide),
        ("invitelinks", cmd_invitelinks),
        ("shot", cmd_shot),
        ("html", cmd_html),
        ("help", cmd_help),
        ("start", cmd_help),
    ):
        app.add_handler(CommandHandler(cmd, fn, filters=only_admin))
        command_fns[cmd] = fn
    async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """명령 처리 중 예외가 나면 조용히 묻지 말고 관리자에게 알린다."""
        err = ctx.error
        log.error("명령 처리 실패: %s: %s", type(err).__name__, err, exc_info=err)
        message = getattr(update, "effective_message", None)
        if message is None:
            return
        detail = f"{type(err).__name__}: {err}"
        if "flood" in detail.lower():
            text = (
                "⏳ <b>텔레그램이 잠시 전송을 막았습니다</b>\n"
                "짧은 시간에 메시지를 너무 많이 보내서 생기는 일시적인 제한입니다. "
                "보통 몇 분 뒤 저절로 풀리니 조금 기다렸다 다시 보내주세요."
            )
        else:
            text = (
                "⚠️ 명령을 처리하다 문제가 생겼습니다. 잠시 뒤 다시 시도해 보세요.\n"
                f"<code>{html.escape(detail)[:400]}</code>"
            )
        try:
            _track(await message.reply_text(text, parse_mode=ParseMode.HTML), text)
        except TelegramError as e:
            log.error("오류 알림 전송도 실패: %s", e)

    app.add_error_handler(on_error)
    app.add_handler(TypeHandler(Update, drop_stale), group=-1)
    app.add_handler(MessageHandler(only_admin & filters.TEXT & ~filters.COMMAND, on_text))
    if nl is not None and nl.enabled:
        # 사진·파일 (설명 칸 글은 TEXT 가 아니라 따로), 그리고 딜봇에 없는 명령 — 모든 CommandHandler 뒤에 둬야 함
        app.add_handler(MessageHandler(only_admin & (filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND, on_media))
        app.add_handler(MessageHandler(only_admin & filters.COMMAND, on_other_command))
    app.add_handler(CommandHandler(["start", "status", "help"], cmd_unknown_chat, filters=~only_admin))
