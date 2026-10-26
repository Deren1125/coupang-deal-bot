"""파이프라인 오케스트레이터.

수집기 → 가격 이력 저장 → 특가 판정 → 대기열 → (속도 제한/중복 확인) → 링크 변환(자동/수동) → 채널 발행 → 관리자 알림
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import secrets as pysecrets
import time
import traceback
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from telegram import Bot, BotCommand, Update
from telegram.ext import Application

from dealbot import __version__, autolink, sc_stores
from dealbot.authenticity import AuthResult, check_authenticity
from dealbot.bridge.actions_out import ActionsOut
from dealbot.bridge.kakao_out import KakaoOut
from dealbot.bridge.status import read_blogauto
from dealbot.collectors import (
    BaseCollector,
    CollectorContext,
    CollectorUnavailable,
    build_collector,
)
from dealbot.commentary import Commentator
from dealbot.config import ENV_MODEL_COMMENTARY, ENV_MODEL_SUMMARIZER, Settings
from dealbot.coupang.client import ApiBudget, CoupangClient, CoupangRateLimited
from dealbot.dashboard.blogauto import BlogAutoReader
from dealbot.dashboard.service import Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.dedupe import find_duplicate
from dealbot.enrich import PageEnricher, PageMeta, is_naver_store, page_images
from dealbot.growth import alerts as growth_alerts
from dealbot.growth import invites, weekly
from dealbot.growth.late_photo import LatePhoto
from dealbot.growth.slots import mark_slot, slot_due
from dealbot.growth.threads_flow import ThreadsFlow
from dealbot.infopost import InfoPostBuilder, PostBody
from dealbot.keyboard import from_korean_keyboard, is_ascii
from dealbot.links import (
    CoupangDeeplinkProvider,
    LinkConversionError,
    LinkPriceProvider,
    LinkRouter,
    ManualLinkRequired,
    ShopSkipped,
)
from dealbot.llm_json import JsonAsker
from dealbot.manual import parse_manual_post
from dealbot.media import reel as reel_tools
from dealbot.media.card import NEUTRAL_CTA, CardStyle, DealCard
from dealbot.media.imagecheck import (
    clean_image,
    coupang_image_url,
    is_board_thumb,
    is_generic_image,
    naver_image_url,
    owner_photo,
)
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring.admin import (
    _AUTH_RE,
    BOT_COMMANDS,
    PHOTO_NOT_RECEIVED,
    AdminNotifier,
    PhotoSource,
    StatusReporter,
    channel_post_base,
    channel_post_ref,
    is_telegram_url,
    kakao_fit,
    photo_refused,
    register_admin_handlers,
    wants_owner_photo,
)
from dealbot.monitoring.nl import REGISTRY_DAYS, NLRouter
from dealbot.monitoring.push import PushNotifier
from dealbot.monitoring.state import BotState, CollectorStatus
from dealbot.naver_shop import (
    LookupRun,
    NaverPriceReference,
    NaverPrices,
    NaverShopSearch,
    naver_quote,
    store_name,
    store_product_no,
)
from dealbot.notify.digest import Digests
from dealbot.notify.router import NotifyRouter
from dealbot.page_price import DISCOUNT_WORDS, PageCheck, check_page_price, hold_line
from dealbot.page_reader import NEW_PRICE, PageRead, PageReader, RunBudget
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import NAVER_SOURCE, CoupangMarketReference, MarketQuote, market_name
from dealbot.publisher.copyblocks import CopyBlock, CopyBlockBuilder
from dealbot.publisher.digest import BlogDigestBuilder
from dealbot.publisher.ig_scheduler import InstagramScheduler
from dealbot.publisher.instagram import InstagramClient, InstagramError, InstagramPublisher
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.scopes import GRANTED, IG_INSIGHTS, THREADS_INSIGHTS, THREADS_READ_REPLIES
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer
from dealbot.publisher.threads import (
    BASIC_SCOPES as THREADS_BASIC_SCOPES,
)
from dealbot.publisher.threads import (
    KV_USERNAME,
    ThreadsClient,
    ThreadsError,
    ThreadsPublisher,
    ThreadsToken,
    authorize_url,
)
from dealbot.shops import ShopRegistry, deal_disclosure, disclosure_program
from dealbot.soldout import looks_sold_out
from dealbot.storage.db import LINK_WAITING, MAYBE_POSTED, REPLACED, Database, QueueItem
from dealbot.store_browser import StoreBrowser, store_browser_check
from dealbot.summarize import InfoSummarizer, Summary, estimate_discount
from dealbot.utils.text import clean_name, truncate
from dealbot.utils.timeutil import fmt_local, from_iso, in_time_window, local_now, to_iso, utcnow
from dealbot.web import WebServer, page


def _ascii_sub_id(sub_id: str | None) -> str | None:
    """쿠팡 서브 ID 가 한/영 전환 실수로 한글이면(예: 'ㅗㅐㅅ') 영문 키('hot')로 바꿔 쓴다 (링크 집계가 깨지지 않게)."""
    if sub_id and not is_ascii(sub_id):
        fixed = from_korean_keyboard(sub_id)
        log.warning("COUPANG_SUB_ID 에 한글이 섞여 있어 영문 키로 바꿔 씀 (%d자)", len(fixed))
        return fixed
    return sub_id

log = logging.getLogger(__name__)

# 승인 링크(code 교환)를 메타가 'API access blocked'(code 200) 로 막을 때 — 이 계정은 10/12 에도 토큰 생성기로만 연결됐다 (주인 10/16)
_AUTH_BLOCKED_HINT = (
    "\n\n메타가 승인 링크 연결을 막고 있어요(같은 링크를 다시 받아도 똑같이 막혀요). 대신 개발자 화면 {where} 에서 "
    "토큰을 만들어 <code>{cmd} 토큰</code> 으로 보내 주세요. 지금 연결·게시는 그대로 유지돼요."
)


def _auth_blocked(err: Exception) -> bool:
    return getattr(err, "code", None) == 200 or "API access blocked" in str(err)

KV_OAUTH_STATE = "threads_oauth_state"
KV_OAUTH_STATE_AT = "threads_oauth_state_at"
KV_IG_OAUTH_STATE = "instagram_oauth_state"
KV_IG_OAUTH_STATE_AT = "instagram_oauth_state_at"
IG_KV_USERNAME = "instagram_username"
# 관리자가 넘겼거나 답하지 않은 글을 다시 묻기까지 (그사이 더 싸지면 바로 다시 물음)
REASK_COOLDOWN = timedelta(days=3)
# 같은 상품을 다시 줄 세울 수 없는 열린 글 상태 (db 의 idx_queue_open_product_v3 와 같음)
OPEN_STATUSES = ("pending", "awaiting_link", "awaiting_approval", autolink.STATUS)
MARKET_NO_MATCH = "none"  # 쿠팡 검색에 맞는 상품이 없었음 (market_quotes 에 가격 0 으로 남김)
# 쿠팡 딜의 네이버 최저가는 같은 market_quotes 표에 이 머리말을 붙인 키로 (다른 몰 딜의 쿠팡 시중가와 섞이지 않게)
NAVER_QUOTE_KEY = "navershop:"
# 특가로 판정했는데 줄 세우지 않은 딜: 이유별 상품 (저녁 요약용, kv 한 칸에 최근 하루치만)
SKIP_KV = "deal_skips"
SKIP_KEEP = timedelta(hours=36)
# 저녁 요약의 이유 이름은 짧게 (Blog-Auto 가 딜봇 하루 줄을 카톡 200자에서 자름 — 리뷰 10/13)
ALREADY_POSTED = "이미 올린 상품"
SKIP_LABELS = {
    "already_posted": ALREADY_POSTED,
    "declined": "넘긴 상품",
    "held": "보류",
    "held_price": "보류",
    "duplicate": "비슷한 딜 있음",
}
# 보류는 Blog-Auto 가 따로 요약에 넣으니 '안 올린 딜' 문장에서는 뺀다 (수집 로그 한 줄에는 그대로)
SKIP_NOT_SUMMARIZED = frozenset({"held", "held_price"})
# 링크 요청 자리가 다 차서 빠진 딜 (저녁 요약에서 '안 올린 딜' 문장 대신 링크 자리 문장으로 따로 셈 — 주인 결정 10/15)
LINK_FULL_LABEL = "요청 밀림"
LINK_REPLACED_LABEL = "더 좋은 딜로 교체"
# Blog-Auto 카톡 한 줄 200자 = 머리말('🌙 딜봇 10/14') + 빈칸 + 딜봇 하루 줄. 딜봇 하루 줄은 이 길이 안에서만 늘린다
DAY_LINE_MAX = 180


def _closed_label(status: str, error: str | None) -> str:
    """줄 섰다가 발행 전에 빠진 딜(대기열 skipped·expired)의 이유를 저녁 요약 말로 (수집 때 이유와 같은 말은 같은 줄로 셈)."""
    e = (error or "").lower()
    if "sold out" in e:
        return "품절"
    if status == "expired":
        return "답 없음" if "unanswered" in e else "대기 초과"
    if e.startswith("already posted"):
        return ALREADY_POSTED
    if e.startswith("not genuine"):
        return "정품 의심"
    if e.startswith("unverified seller"):
        return "판매처 미확인"
    if e.startswith("review backlog full"):
        return "확인 요청 밀림"
    if "backlog full" in e:
        return LINK_FULL_LABEL
    if e.startswith("skipped by admin"):
        return "주인이 넘김"
    if e.startswith(REPLACED):
        return LINK_REPLACED_LABEL
    return "기타"


def _skip_sentence(counts: dict[str, int], room: int) -> str:
    """저녁 요약 끝에 붙일 '안 올린 딜' 문장 (많은 이유 두 개까지). room 글자 안에 안 들어가면 이유를 줄이고, 그래도 넘으면 안 붙임."""
    items = list(counts.items())
    for n in (2, 1):
        parts = ", ".join(f"{label} {c}개" for label, c in items[:n])
        text = f" 특가인데 안 올린 딜은 {parts}{' 등이에요' if len(items) > n else '예요'}."
        if items and len(text) <= room:
            return text
    return ""


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    """market_quotes 한 줄의 meta(JSON). 없거나 깨졌으면 빈 dict."""
    try:
        data = json.loads(row.get("meta") or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class DealBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # 지금 시각 (UTC). 스레드 리듬·답글 모으기·주간 글이 쓴다 — 테스트는 이걸 바꿔 끼워 시각을 정한다
        self.clock: Callable[[], datetime] = utcnow
        self.state = BotState(dry_run=settings.publish.dry_run)
        self.db = Database(settings.db_path)
        cc = settings.publish.commentary
        self.commentator = Commentator(enabled=cc.enabled, model=cc.model, timeout=cc.timeout_seconds, db=self.db)
        self.registry: ShopRegistry = settings.shop_registry()
        self.http = httpx.AsyncClient(
            timeout=settings.http.timeout_seconds,
            headers={"User-Agent": settings.http.user_agent, "Accept-Language": "ko-KR,ko;q=0.9"},
            follow_redirects=False,
        )

        # ---- 링크 변환기(제휴 프로그램별)
        providers: dict[str, Any] = {}
        self.coupang: CoupangClient | None = None
        self.budget = ApiBudget(
            settings.coupang.max_calls_per_hour,
            reserve={"deeplink": settings.coupang.deeplink_reserve},
            caps={"deeplink": settings.coupang.deeplink_max_per_hour},
        )
        self.market: CoupangMarketReference | None = None
        if settings.secrets.has_coupang:
            self.coupang = CoupangClient(
                settings.secrets.coupang_access_key or "",
                settings.secrets.coupang_secret_key or "",
                http=self.http,
                sub_id=_ascii_sub_id(settings.secrets.coupang_sub_id),
                max_retries=settings.http.max_retries,
                retry_backoff=settings.http.retry_backoff_seconds,
                budget=self.budget,
            )
            providers["coupang"] = CoupangDeeplinkProvider(self.coupang, self.http, settings.links)
            if settings.deal.market.enabled:
                self.market = CoupangMarketReference(self.coupang, settings.deal.market)
        else:
            log.warning("COUPANG_ACCESS_KEY/SECRET_KEY not set — coupang collectors & deeplink disabled")

        # ---- 브라우저 자동화 (네이버 쇼핑커넥트)
        self.browser: Any = None
        self.naver_connect: Any = None
        from dealbot import site_gate

        # 사이트·계정마다 접속 상한 (주인 10/26) — 블로그 프로그램과 같은 기록 파일(Blog-Auto var/site_gate.db). 없으면 딜봇 data 폴더
        ba_var = Path(settings.dashboard.blogauto_metrics_path).parent.parent
        site_gate.configure((ba_var if ba_var.is_dir() else settings.data_dir) / "site_gate.db")
        if settings.browser.enabled:
            try:
                from dealbot.browser.naver_connect import NaverConnectProvider
                from dealbot.browser.session import BrowserSession

                self.browser = BrowserSession(
                    settings.data_dir / settings.browser.profile_dir,
                    headless=settings.browser.headless,
                    executable_path=settings.browser.executable_path,
                )
                self.naver_connect = NaverConnectProvider(self.browser, settings.browser.naver_connect)
                from dealbot.dashboard import logins as dash_logins

                # 대시보드 '로그인 관리' 칸 (주인 10/26): 쿠키를 볼 때마다 상태만 적음 — 이 때문에 사이트를 더 열지 않는다
                self.naver_connect.on_state = lambda ok: dash_logins.note_naver(self.db, ok)
                providers["naver_connect"] = self.naver_connect
            except Exception as e:  # noqa: BLE001
                log.warning("browser automation unavailable: %s", e)
        if settings.secrets.has_linkprice:
            providers["linkprice"] = LinkPriceProvider(
                settings.secrets.linkprice_affiliate_id or "",
                self.http,
                retries=settings.http.max_retries,
                backoff=settings.http.retry_backoff_seconds,
            )
        auto_off = self.registry.apply_providers(providers.keys())
        if auto_off:
            log.info("shops off until their link provider is configured: %s", ", ".join(auto_off))
        self.links = LinkRouter(self.registry, settings.links, settings.publish, providers=providers)

        self.evaluator = DealEvaluator(settings.deal)
        ec = settings.deal.enrich
        self.enricher = PageEnricher(
            self.http,
            timeout=settings.http.timeout_seconds,
            store_min_interval=ec.store_min_interval_seconds,
            store_attempts=ec.store_retry_attempts,
            store_backoff=ec.store_retry_backoff_seconds,
            store_max_wait=ec.store_max_wait_seconds,
            store_breaker_after=ec.store_breaker_after,
            store_breaker_seconds=ec.store_breaker_seconds,
            store_breaker_max_seconds=ec.store_breaker_max_seconds,
            store_cache_seconds=ec.store_cache_seconds,
        )
        self._enrich_memo: dict[str, tuple[float, PageMeta | None]] = {}  # 상품 → (기억 끝 시각, 읽은 페이지) — remember_hours
        # 일반 요청이 막힌(429) 네이버 스토어 딜: 발행 직전에만 실제 크롬(로그인 없는 빈 창)으로 한 번 (주인 결정 10/10)
        pc = settings.deal.page_check
        self.store_browser = StoreBrowser(
            enabled=ec.store_browser,
            min_interval=ec.store_browser_min_interval_seconds,
            min_free_mb=ec.store_browser_min_free_mb,
            timeout=ec.store_browser_timeout_seconds,
            cooldown_seconds=pc.browser_cooldown_minutes * 60,
            cooldown_max_seconds=pc.browser_cooldown_max_minutes * 60,
        )
        # 수집 때 가격 대조용 페이지 읽기: 읽은 페이지 기억, 일반 요청이 막힌 네이버 상품 페이지는 같은 크롬으로 (한도 안에서)
        self.page_reader = PageReader(
            browser=pc.browser,
            per_run=pc.browser_per_run,
            per_hour=pc.browser_per_hour,
            timeout=pc.browser_timeout_seconds,
            cache_seconds=pc.cache_minutes * 60,
            retry_seconds=pc.browser_cooldown_minutes * 60,
            log_every_seconds=pc.unverified_log_hours * 3600,
        )
        # 네이버 쇼핑 검색 Open API 는 2026-07-31 종료 (대신할 API 없음, 이 서버 키는 401): 사진 찾기에는 안 쓴다 (주인 결정 10/15 —
        # 네이버 딜 사진은 주인이 링크와 같이 보낸 사진). 아래 최저가 비교를 설정으로 켤 때만 부른다 (운영은 꺼 둠)
        self.naver_shop = NaverShopSearch(
            self.http,
            settings.secrets.naver_client_id,
            settings.secrets.naver_client_secret,
            timeout=settings.http.timeout_seconds,
        )
        # 쿠팡 딜(골드박스·카테고리 베스트)의 가격 근거: 같은 키로 네이버 쇼핑 최저가 (키가 없거나 401 이면 이 비교만 꺼짐)
        nr = settings.deal.naver_reference
        self.naver_ref: NaverPriceReference | None = NaverPriceReference(self.naver_shop, nr) if nr.enabled else None
        self.renderer = TemplateRenderer(settings.templates_dir, settings.app.timezone, settings.channels.as_dict())
        self.renderer.emphasis = (settings.publish.emphasis_must_pct, settings.publish.emphasis_top_pct)
        self.info = InfoPostBuilder(
            self.http,
            self.renderer,
            max_chars=settings.info_posts.max_chars,
            raw_chars=settings.info_posts.summarizer.raw_chars,
            timeout=settings.http.timeout_seconds,
        )
        sm = settings.info_posts.summarizer
        # API 키가 먼저, 없으면 서버의 claude 로그인(한줄평과 같은 CLI·모델). 설정에서 끄면 둘 다 안 씀
        self.summarizer = InfoSummarizer(
            settings.secrets.anthropic_api_key if sm.enabled else None,
            model=sm.model,
            max_chars=settings.info_posts.max_chars,
            timeout=sm.timeout_seconds,
            use_cli=sm.enabled,
            cli_model=settings.publish.commentary.model,
            cli_timeout=max(sm.timeout_seconds, 120),
        )
        if settings.info_posts.enabled and sm.enabled and self.summarizer.backend is None:
            log.warning("info summarizer off (no ANTHROPIC_API_KEY and no claude CLI) — info posts will wait for admin approval (/ok)")
        self._pending_notice_ready = False
        self.rate_limiter = RateLimiter(self.db, settings.publish)
        # 네이버 쇼핑커넥트 자동 링크: Blog-Auto 가 쓰는 답 파일 (받은편지함과 같은 폴더)
        self.autolink_results = autolink.ResultReader(settings.data_dir / autolink.RESULTS_FILE, self.db)
        # 토스 쉐어링크 자동 링크 (주인 10/23): Blog-Auto 가 쓰는 답 파일
        self.toss_results = autolink.ResultReader(settings.data_dir / autolink.TOSS_RESULTS_FILE, self.db,
                                                  pos_key=autolink.KV_TOSS_POS)

        # ---- 텔레그램
        self.application: Application | None = None  # type: ignore[type-arg]
        self.bot: Bot | None = None
        if settings.secrets.has_telegram:
            try:
                # 응답 대기 시간을 넉넉히: 기본(읽기 5초)이면 텔레그램이 글을 올린 뒤 답이 늦을 때 실패로 보여 같은 딜을 또 올릴 수 있었다
                self.application = (
                    Application.builder().token(settings.secrets.telegram_bot_token or "")
                    .read_timeout(30).write_timeout(30).media_write_timeout(60).build()
                )
                self.bot = self.application.bot
            except Exception as e:  # noqa: BLE001 — 토큰 형식 오류로 봇 전체가 죽지 않도록
                log.error(
                    "TELEGRAM_BOT_TOKEN 이 올바르지 않습니다 (%s). BotFather 가 준 '숫자:문자' 형식 전체를 넣었는지 확인하세요. "
                    "오프라인 모드로 계속합니다.",
                    e,
                )
                self.application = None
                self.bot = None
        else:
            log.warning("TELEGRAM_BOT_TOKEN not set — running offline (dry-run publish, no admin notices)")
        if not settings.secrets.has_channel and not settings.publish.dry_run:
            log.warning("TELEGRAM_CHANNEL_ID not set — publishing falls back to dry-run")
            self.state.dry_run = True

        self.publisher = TelegramPublisher(
            self.bot,
            settings.secrets.telegram_channel_id,
            self.renderer,
            registry=self.registry,
            template=settings.publish.template,
            send_photo=settings.publish.send_photo,
            dry_run=self.state.dry_run,
        )
        self.push = PushNotifier(settings.monitoring.push, settings.secrets, self.http)
        # ---- 스레드 자동 발행 + 복붙 문구
        self.threads = ThreadsPublisher(
            ThreadsClient(
                self.http,
                app_id=settings.secrets.threads_app_id,
                app_secret=settings.secrets.threads_app_secret,
                max_retries=settings.http.max_retries,
                retry_backoff=settings.http.retry_backoff_seconds,
            ),
            self.db,
            self.renderer,
            registry=self.registry,
            template=settings.threads.template,
            reply_template=settings.threads.reply_template,
            enabled=settings.threads.enabled,
            dry_run=self.state.dry_run,
            refresh_before_days=settings.threads.refresh_before_days,
        )
        # ---- 인스타그램 자동 발행 (사진은 카드 이미지)
        self.instagram = InstagramPublisher(
            InstagramClient(
                self.http,
                app_id=settings.secrets.instagram_app_id,
                app_secret=settings.secrets.instagram_app_secret,
                max_retries=settings.http.max_retries,
                retry_backoff=settings.http.retry_backoff_seconds,
            ),
            self.db,
            self.renderer,
            registry=self.registry,
            template=settings.instagram.template,
            enabled=settings.instagram.enabled,
            dry_run=self.state.dry_run,
            refresh_before_days=settings.instagram.refresh_before_days,
            style=settings.instagram.style,
        )
        self.card: DealCard | None = None
        if settings.card.enabled:
            try:
                self.card = DealCard(
                    settings.data_dir / "media",
                    font_path=settings.card.font_path,
                    style=CardStyle(footer=settings.card.footer, footer_note=settings.card.footer_note),
                    layout=settings.instagram.style,
                    font_heavy_path=settings.card.font_heavy_path,
                    font_regular_path=settings.card.font_regular_path,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("deal card disabled: %s", e)
        # 인스타 대기열·시간표 (주인 결정 10/15): 채널에 올린 딜은 여기 줄 서서 하루 3개만, 점심 릴스·저녁 TOP5·화금 단가표는 따로
        self.ig = InstagramScheduler(self)
        self.copy_blocks = CopyBlockBuilder(settings.copy_cfg, self.renderer, self.registry)
        self.digest = BlogDigestBuilder(settings.blog_digest, self.renderer, self.registry)
        # ---- 채널 성장 (주인 결정 10/15): 스레드 리듬·답글 초안·투표, 늦게 받는 딜 사진
        self.threads_flow = ThreadsFlow(self)
        self.late_photo = LatePhoto(self)

        self.notifier = AdminNotifier(
            self.bot,
            settings.secrets.telegram_admin_chat_id,
            settings.monitoring,
            self.renderer,
            settings.app.timezone,
            push=self.push,
            registry=self.registry,
            labels={c.name: c.label for c in settings.collectors if c.label},
        )
        self.notifier.owner_relay = self.owner_notice
        # 알림 정책 (주인 결정 10/11): 할 일 카톡은 Blog-Auto 받은편지함으로(v2), Blog-Auto 생존 신호로 카톡이 되는지 본다
        self.notifier.owner_inbox = self.relay_to_blog
        self.notifier.status_path = settings.data_dir / "blogauto_status.json"
        # ---- 주인 알림 한 곳 (주인 10/18): 대시보드 앱 웹 푸시가 먼저, 카톡은 예비(Blog-Auto 가 보냄), 텔레그램 관리자 챗은 조용히.
        # 대시보드에서 누른 블로그 일은 Blog-Auto 에 파일로 맡긴다 (bridge/). notify.router: legacy 면 예전 그대로
        data_dir = settings.data_dir
        self.kakao_out = KakaoOut(data_dir, bridge_ok=lambda: read_blogauto(data_dir).bridge_ok(), inbox=self.relay_to_blog)
        self.actions_out = ActionsOut(self.db, data_dir, status=lambda: read_blogauto(data_dir))
        self.router: NotifyRouter | None = None
        self.digests: Digests | None = None
        if settings.notify.router == "push":
            self.router = NotifyRouter(self.db, data_dir=data_dir, http=self.http, public_url=settings.dashboard.public_url,
                                       tz=settings.app.timezone, kakao=self.kakao_out, quiet_hours=settings.notify.push_quiet_hours,
                                       badge=self.todo_count, kakao_kinds=settings.notify.kakao_kinds,
                                       kakao_daily=settings.notify.kakao_daily)
            self.notifier.router = self.router
            self.digests = Digests(self.db, self.router, tz=settings.app.timezone, export=self._blog_export,
                                   todo=self._todo_brief)
        self.earn_store: Any = None  # dashboard.earn.EarnStore (대시보드를 만든 뒤)
        self.earn_coupang: Any = None  # dashboard.earn.EarnCoupang — 쿠팡 실적을 3시간마다 읽음 (scheduler.dashboard_loop)
        self.dash_state: Any = None  # dashboard.state.StateBuilder (대시보드를 만든 뒤)
        # 말 명령 (주인 요청 10/11): 일반 말 → 기존 명령. AI 는 정보 글 요약기처럼 API 키가 있으면 API, 없으면 서버 claude CLI
        nc = settings.monitoring.nl_router
        self.nl = NLRouter(
            nc,
            self.db,
            JsonAsker(
                settings.secrets.anthropic_api_key,
                model=nc.model or sm.model,
                cli_model=nc.cli_model or settings.publish.commentary.model,
                timeout=nc.timeout_seconds,
            ),
            relay=self.relay_to_blog,
            parse_post=lambda text: parse_manual_post(text, self.registry),
            shop_of=lambda url: (shop.key if (shop := self.registry.by_url(url)) else None),
            shop_name=self.notifier.shop_label,
            collectors=self.collector_names,
            paused=lambda: self.state.paused,
            tz=settings.app.timezone,
            publish=settings.publish,  # 링크·확인 요청 자리 수만큼 후보를 읽게
        )
        self.notifier.msg_log = self.nl.record
        # 모델 이름은 저장소에 없고 서버 .env 에서 받는다. 없으면 한 번 알리고 안전하게: CLI 는 기본 모델, API 는 건너뜀
        # 그 이름을 실제로 쓰는 기능이 켜져 있을 때만 알린다 (꺼 둔 기능·설정에 따로 적은 모델 때문에 시작 로그가 시끄럽지 않게)
        info_ai = settings.info_posts.enabled and sm.enabled
        nl_cli = self.nl.enabled and not nc.cli_model and self.nl.asker.backend != "api"
        if not cc.model and (cc.enabled or (info_ai and self.summarizer.backend != "api") or nl_cli):
            log.warning("%s 가 없어 claude CLI 는 기본 모델로 씀 (한줄평, CLI 로 도는 요약·말 명령)", ENV_MODEL_COMMENTARY)
        if settings.secrets.anthropic_api_key and not sm.model and (info_ai or (self.nl.enabled and not nc.model)):
            log.warning("%s 가 없어 ANTHROPIC_API_KEY 가 있어도 Claude API 는 건너뜀 — 정보 글 요약·말 명령은 서버 claude CLI 로",
                        ENV_MODEL_SUMMARIZER)
        self.notifier.channel_post_base = channel_post_base(settings.secrets.telegram_channel_id, settings.channels.telegram_url)
        self.reporter = StatusReporter(
            settings, self.db, self.state, self.rate_limiter, self.renderer, self.registry, self.links, budget=self.budget,
            summarizer=self.summarizer,
        )
        # ---- 작은 웹 서버: 스레드 OAuth 콜백·/health. Railway 공개 도메인을 붙이면 승인만 눌러도 스레드가 연결된다
        self.web = WebServer(settings.secrets.web_port)
        self.web.route("/threads/callback", self._threads_callback)
        if settings.secrets.threads_callback_path != "/threads/callback":
            self.web.route(settings.secrets.threads_callback_path, self._threads_callback)
        self.web.route("/instagram/callback", self._instagram_callback)
        if settings.secrets.instagram_callback_path != "/instagram/callback":
            self.web.route(settings.secrets.instagram_callback_path, self._instagram_callback)
        self.web.serve_static("/media/", settings.data_dir / "media")  # 카드 이미지 (메타가 여기서 받아 감)
        if settings.web.public_page:
            self.web.route("/", self._deals_page)  # 인스타 프로필 링크용 핫딜 목록
            self.web.route_prefix("/d/", self._deal_redirect)  # /d/번호 → 구매 링크
        # ---- 채널 대시보드 (/dashboard, 기본 인증 — 주인 요청 10/14). 만들다 실패해도 봇은 계속 돈다
        self.dashboard: Dashboard | None = None
        try:
            self.dashboard = self._build_dashboard()
            from dealbot.dashboard.earn import EarnCoupang, EarnStore
            from dealbot.dashboard.state import StateBuilder

            self.earn_store = EarnStore(self.db)  # 제휴 수익 (10/23 — dashboard/earn.py)
            self.earn_coupang = EarnCoupang(self.earn_store, lambda: self.coupang, lambda: self.budget, settings.app.timezone)
            self.dash_state = StateBuilder(self)
            self.dashboard.attach_app(self)  # 대시보드 앱 API (/dashboard/api/…, dashboard/api.py)
            self.dashboard.register(self.web)
        except Exception as e:  # noqa: BLE001
            log.exception("dashboard disabled: %s", e)
            self.web.guard_prefix("/dashboard", None)  # 만들다 실패하면 /dashboard 아래는 닫아 둠 (404)

        # ---- 수집기
        ctx = CollectorContext(settings=settings, http=self.http, db=self.db, coupang=self.coupang, shops=self.registry)
        self.collectors: list[BaseCollector] = []
        for ccfg in settings.collectors:
            status = CollectorStatus(name=ccfg.name, type=ccfg.type, interval_minutes=ccfg.interval_minutes, enabled=ccfg.enabled)
            self.state.collectors[ccfg.name] = status
            if not ccfg.enabled:
                continue
            collector = build_collector(ccfg, ctx)
            try:
                collector.check_available()
            except CollectorUnavailable as e:
                status.available = False
                status.unavailable_reason = str(e)
                log.warning("collector '%s' unavailable: %s", ccfg.name, e)
            self.collectors.append(collector)
        # 꺼 둔 수집기의 '확인 실패' 할 일은 닫는다 (주인 10/23 — 알구몬을 끈 뒤에도 '2시간째 실패'가 남아 있었음)
        for ccfg in settings.collectors:
            if not ccfg.enabled:
                self.notifier.resolve(f"collector:{ccfg.name}")

        if self.application is not None and settings.secrets.telegram_admin_chat_id:
            register_admin_handlers(
                self.application,
                admin_chat_id=settings.secrets.telegram_admin_chat_id,
                reporter=self.reporter,
                controller=self,
                nl=self.nl,
            )
        if self.application is not None and settings.secrets.telegram_channel_id:
            # 채널 초대 링크별 가입 세기 (chat_member 업데이트 — 봇이 채널 관리자일 때만 옴)
            from telegram.ext import ChatMemberHandler

            self.application.add_handler(ChatMemberHandler(self._on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    def _build_dashboard(self) -> Dashboard:
        s = self.settings
        dc = s.dashboard
        ctx = SourceContext(
            db=self.db,
            tz=s.app.timezone,
            config=dc,
            now=utcnow,
            telegram_bot=lambda: self.bot,  # 시작 중 텔레그램 연결이 실패하면 None 으로 바뀜
            channel_id=s.secrets.telegram_channel_id,
            instagram=self.instagram,  # 토큰 저장·갱신은 게시기 그대로 (/instatoken)
            threads=self.threads,
            coupang=self.coupang,
            budget=self.budget,
            include_dry_run=lambda: self.state.dry_run,
            history_days=s.app.prune_events_days,  # 딜 기록을 지운 날은 다시 세지 않음
            blogauto=BlogAutoReader(dc.blogauto_metrics_path, tz=s.app.timezone, stale_hours=dc.blogauto_stale_hours, now=utcnow),
            shop_label=self.notifier.shop_label,  # '링크 기다리는 딜' 의 몰 이름 (텔레그램 링크 요청과 같은 이름)
            link_ttl_hours=s.publish.manual_link_ttl_hours,  # '지금 할 일' 의 '14시간 남음' (expire_queue 와 같은 기준)
        )
        # links=self: '링크 넣기' 는 텔레그램 답장과 같은 attach_link·owner_link_problem·refresh_pending_notice 를 부른다
        return Dashboard(dc, ctx, user=s.secrets.dashboard_user, password=s.secrets.dashboard_password,
                         public_base_url=s.secrets.public_base_url, links=self)

    # ------------------------------------------------------------ lifecycle
    async def start_telegram(self, *, polling: bool = True) -> None:
        if self.application is None:
            return
        try:
            await self.application.initialize()
            await self.application.start()
        except Exception as e:  # noqa: BLE001 — 토큰 오류 등으로 봇 전체가 죽지 않도록
            log.error(
                "텔레그램 연결 실패 (%s). TELEGRAM_BOT_TOKEN 이 BotFather 가 준 '숫자:문자' 전체인지 확인하세요. "
                "오프라인 모드로 계속합니다 (채널 발행·관리자 알림 없음).",
                e,
            )
            self.application = None
            self.bot = None
            self.publisher.bot = None
            self.publisher.dry_run = True
            self.notifier.bot = None
            self.state.dry_run = True
            return
        try:
            me = await self.application.bot.get_me()
            self.notifier.bot_username = me.username
            self.nl.bot_id = me.id  # 답장 대상이 이 봇(딜봇·Blog-Auto 공용) 메시지인지 가릴 때
        except Exception as e:  # noqa: BLE001
            log.warning("get_me failed: %s", e)
        try:
            # 텔레그램 입력창의 "/" 메뉴에 명령과 설명을 등록 (외우지 않아도 골라 쓸 수 있게)
            await self.application.bot.set_my_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])
        except Exception as e:  # noqa: BLE001
            log.warning("set_my_commands failed: %s", e)
        if polling and self.settings.secrets.telegram_admin_chat_id and self.application.updater:
            # 재배포 중(봇이 잠깐 꺼진 사이)에 보낸 명령도 켜지면 처리한다. 너무 오래된 것은 admin 쪽 가드가 버림
            # CHAT_MEMBER: 채널 초대 링크별 가입 수 (주인 결정 10/15 — 누가 들어왔는지는 안 남기고 링크 이름별 수만)
            await self.application.updater.start_polling(
                allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.CHAT_MEMBER], drop_pending_updates=False)

    async def stop_telegram(self) -> None:
        if self.application is None:
            return
        try:
            if self.application.updater and self.application.updater.running:
                await self.application.updater.stop()
            if self.application.running:
                await self.application.stop()
            await self.application.shutdown()
        except Exception as e:  # noqa: BLE001
            log.warning("telegram shutdown error: %s", e)

    async def start_web(self) -> None:
        """포트를 못 열어도 봇은 계속 돈다 (그때는 /threadscode 수동 연결)."""
        try:
            await self.web.start()
        except Exception as e:  # noqa: BLE001
            log.warning("web server start failed on port %s (스레드 연결은 /threadscode 수동으로): %s", self.web.port, e)

    async def close(self) -> None:
        await self.web.stop()
        await self.stop_telegram()
        if self.browser is not None:
            await self.browser.close()
        await self.http.aclose()
        self.db.close()

    # ----------------------------------------------------------- controller
    def pause(self) -> None:
        self.state.paused = True
        self.db.log_event("INFO", "control", "paused by admin")

    def resume(self) -> None:
        self.state.paused = False
        self.db.log_event("INFO", "control", "resumed by admin")

    def collector_names(self) -> list[str]:
        return [c.name for c in self.collectors]

    def request_run(self, collector: str | None) -> str:
        names = self.collector_names()
        targets = names if collector is None else [collector]
        unknown = [t for t in targets if t not in names]
        if unknown:
            return f"그런 게시판은 없어요: {', '.join(unknown)}\n쓸 수 있는 이름: {', '.join(names)}"
        for t in targets:
            self.state.collectors[t].run_requested = True
        return f"🔄 곧 확인할게요: {', '.join(targets)} (1분 안에 시작)"

    @staticmethod
    def _maybe_posted(item: QueueItem | None) -> bool:
        """보낸 뒤 텔레그램 답만 시간 초과 — 채널에 올라갔는지 모르는 글 ('❓ 채널에 올라갔는지 모릅니다' 알림)."""
        return item is not None and item.status == "failed" and (item.last_error or "").startswith(MAYBE_POSTED)

    @classmethod
    def _link_problem(cls, item: QueueItem | None, queue_id: int, url: str, *, link_ttl: timedelta) -> str | None:
        """링크를 붙일 수 없는 이유 (주인 답장·대시보드 '링크 넣기'·쇼핑커넥트 자동 링크 같은 기준). 붙일 수 있으면 None.
        link_ttl: 링크 요청 유효 시간 (publish.manual_link_ttl_hours) — 자리를 내준 글은 여기서 직접 시간을 보고 이유를 말한다
        (부르는 쪽이 _replaced_target 으로 먼저 열었는지와 상관없이, 유효 시간 안의 글을 '시간이 지났다'고 하지 않게 — 리뷰 10/15)."""
        if item is None:
            return f"#{queue_id} 번 글이 없습니다. /queue 나 /pending 에서 번호를 확인해 주세요."
        if item.status == "awaiting_approval":
            return f"#{queue_id} 번은 확인을 기다리는 글이라 링크가 아니라 답이 필요합니다: <code>/ok {queue_id}</code> 또는 <code>/skip {queue_id}</code>"
        if item.link_replaced:
            if utcnow() - item.created_at >= link_ttl:
                return (f"#{queue_id} 번은 더 좋은 딜에 자리를 내준 뒤 유효 시간이 지나 링크를 붙이지 않았어요. "
                        "아직 싸게 팔고 있으면 <code>/post</code> 로 직접 올려 주세요.")
            # 유효 시간 안: 주인 링크(답장·/link·대시보드)는 _replaced_target 이 열어서 여기까지 오지 않는다 — 그 밖의 길(자동 링크 등)
            return (f"#{queue_id} 번은 더 좋은 딜에 자리를 내준 글이라 이 링크는 붙이지 않았어요. "
                    f"그래도 올리려면 링크 요청 유효 시간 안에 <code>/link {queue_id} 링크</code> 로 보내 주세요.")
        if item.status not in ("awaiting_link", "pending", "failed", autolink.STATUS):
            return f"#{queue_id} 번은 지금 '{item.status}' 상태라 링크를 붙일 수 없습니다."
        if not url.startswith("http"):
            return "링크는 http:// 또는 https:// 로 시작해야 합니다."
        if is_telegram_url(url):
            # 채널 글·채널·초대 주소는 제휴·상품 링크가 아니다. 붙이면 같은 딜이 그 주소를 링크로 다시 올라가고,
            # 그 상품의 '내 링크'(mylink)로 남아 다음 딜에도 쓰인다 (리뷰 지적 10/13)
            if cls._maybe_posted(item):
                return (f"텔레그램 주소는 상품 링크가 아니라 붙이지 않았어요. 채널에 그 글이 있으면 "
                        f"<code>/posted {queue_id}</code> (채널 글 링크를 붙이면 품절 표시도 돼요), "
                        f"없으면 <code>/link {queue_id} 상품 링크</code> 로 다시 올려 주세요.")
            return "텔레그램 주소(t.me)는 제휴·상품 링크가 아니라 붙일 수 없어요. 그 몰 앱에서 만든 제휴 링크를 보내 주세요."
        return None

    def _link_ttl(self) -> timedelta:
        return timedelta(hours=self.settings.publish.manual_link_ttl_hours)

    def owner_link_target(self, queue_id: int) -> QueueItem | None:
        """주인이 queue_id 에 링크를 보내면 붙을 글 — attach_link 와 같은 _replaced_target (알림·기록 없이 보기만).
        자리를 내준 글이면 다시 줄 선 같은 상품 글, 유효 시간 안이면 링크를 기다리는 글처럼(status awaiting_link 로 본 사본),
        지났으면 그 글 그대로. 대시보드 '링크 넣기' 가 화면을 연 뒤 자리를 내준 글도 텔레그램 답장처럼 받으려고 쓴다."""
        return self._replaced_target(self.db.get_queue_item(queue_id), queue_id)[0]

    def owner_link_problem(self, queue_id: int, url: str) -> str | None:
        """주인이 준 링크를 붙일 수 없는 이유 — attach_link 가 붙이기 전에 보는 것과 같은 _replaced_target → _link_problem (같은 기준).
        대시보드 '링크 넣기' 는 이걸로 먼저 거른 뒤에만 attach_link 를 부른다. 붙일 수 있으면 None."""
        item, qid, _moved = self._replaced_target(self.db.get_queue_item(queue_id), queue_id)
        return self._link_problem(item, qid, url, link_ttl=self._link_ttl())

    async def attach_link(self, queue_id: int, url: str, *, photo: PhotoSource | None = None) -> str:
        """주인이 만든 링크를 붙인다. photo: 링크와 같이 보낸 상품 사진 — 바이트, 또는 텔레그램에서 받아 오는 함수 (주인 결정 10/15).
        사진은 링크를 붙일 수 있는 네이버 상품 딜일 때만 받아 검사한다: 없는 번호·붙일 수 없는 상태·채널 글 주소·네이버가 아닌 딜이면
        사진을 받지도 거절하지도 않는다 (예전엔 사진부터 받아 HEIC·10MB 사진이 토스 링크까지 막았다 — 검토 지적)."""
        item = self.db.get_queue_item(queue_id)
        item, queue_id = await self._replaced_for_link(item, queue_id)  # 더 좋은 딜에 자리를 내준 글도 주인 링크는 받는다 (주인 결정 10/15)
        if self._maybe_posted(item) and channel_post_ref(url) is not None:
            # '채널에 올라갔는지 모름' 글에 채널 글 링크로 답함(답장·/link·일괄 줄) = 채널에 있다는 뜻
            # → 그 주소를 링크로 붙여 다시 올리지 않고 /posted 처럼 올라간 글로 기록한다 (그 글 번호로 품절 표시도)
            done = await self.mark_posted(queue_id, url)
            return done + ("\n사진은 쓰지 않았어요 (채널에 이미 올라간 글)" if photo is not None else "")
        problem = self._link_problem(item, queue_id, url, link_ttl=self._link_ttl())
        if problem or item is None:
            return problem or ""
        jpeg: bytes | None = None
        if photo is not None and wants_owner_photo(item.deal.product.shop, item.deal.product):
            jpeg, why = await self._check_owner_photo(photo)
            if jpeg is None:
                log.info("owner photo refused for #%d: %s", queue_id, why)
                return photo_refused(queue_id, why)  # 못 쓰는 사진: 링크도 아직 안 붙임 (사진을 바꿔 다시 보내면 됨)
            # 사진을 받고 푸는 사이 글이 바뀌었을 수 있다 (다른 답장으로 링크가 붙음·건너뜀·만료) → 다시 읽어 같은 기준으로
            # (자리를 내준 글이면 DB 엔 skipped 로 남아 있으니 처음과 같은 _replaced_target 으로 다시 본다)
            item = self._replaced_target(self.db.get_queue_item(queue_id), queue_id)[0]
            problem = self._link_problem(item, queue_id, url, link_ttl=self._link_ttl())
            if problem or item is None:
                return problem or ""
        # 여기서부터 링크를 붙일 때까지 await 없음 (사진을 먼저 적고 링크 → 발행 루프가 사진 없이 집어 가지 않게)
        photo_note, refused = self._take_owner_photo(item, jpeg, sent=photo is not None)
        if refused:
            return photo_refused(queue_id, refused)
        if item.status == autolink.STATUS:
            self._cancel_autolink(queue_id)  # 주인이 먼저 붙였으면 Blog-Auto 일은 거둔다
        if (item.last_error or "").startswith(MAYBE_POSTED):
            self.notifier.resolve(f"unsure:{queue_id}")  # 채널에 없어서 다시 올림 — 아직 안 보낸 '채널 확인' 카톡은 취소
        self.db.set_queue_link(queue_id, url.strip())
        self._sc_store_joined(item, url)
        # 상품 1개에 내 링크 1개 (네이버 쇼핑 커넥트·토스 쉐어링크는 만료 없음) → 같은 상품이 또 나오면 다시 묻지 않고 재사용
        self.db.kv_set(f"mylink:{item.deal.product.product_id}", url.strip())
        self.db.log_event("INFO", "manual_link", f"#{queue_id} {url}" + (" + 상품 사진" if photo_note == "ok" else ""))
        return self.notifier.link_done_text(item, photo=photo_note)

    def _replaced_target(self, item: QueueItem | None, queue_id: int) -> tuple[QueueItem | None, int, bool]:
        """더 좋은 딜에 자리를 내준 글에 주인이 링크를 보냄 → (링크를 붙일 글, 그 번호, 다른 글로 옮겼는지). 주인이 이긴다 (주인 결정 10/15):
        - 그사이 같은 상품이 새 글로 다시 줄 섰으면 그 글로 (한 상품에 열린 글은 하나). 붙일 수 있는지(확인 대기·잘못된 주소)는
          _link_problem 이 그 글 기준으로 그대로 말한다
        - 링크 요청 유효 시간(manual_link_ttl_hours) 안이면 링크를 기다리는 글처럼 받아 다시 올린다 (발행 전 품절·이미 올림 확인은 그대로)
        - 지났으면 그대로 (_link_problem 이 이유를 말함). 자리를 내준 글이 아니면 그대로.
        보기만 한다 (알림·기록 없음) — 텔레그램 답장·/link(_replaced_for_link)와 대시보드 '링크 넣기'(owner_link_target·
        owner_link_problem)가 같은 판단을 쓰게."""
        if item is None or not item.link_replaced:
            return item, queue_id, False
        twin = next(iter(self.db.items_for_product(item.product_id, OPEN_STATUSES)), None)
        if twin is not None:
            return twin, twin.id, True
        if utcnow() - item.created_at >= self._link_ttl():
            return item, queue_id, False
        return replace(item, status="awaiting_link"), queue_id, False

    async def _replaced_for_link(self, item: QueueItem | None, queue_id: int) -> tuple[QueueItem | None, int]:
        """_replaced_target 그대로 + 같은 상품의 새 글로 옮겼으면 그 번호로 받는다고 한 줄 따로 알린다
        (attach_link 안에서 이어서 — 사진 같은 인자도 그대로)."""
        target, target_id, moved = self._replaced_target(item, queue_id)
        if moved:
            self.db.log_event("INFO", "manual_link", f"#{queue_id} was replaced — link goes to #{target_id} (same product queued again)")
            await self.notifier.send(f"↪️ #{queue_id} 번과 같은 상품이 지금 #{target_id} 번으로 다시 줄 서 있어 #{target_id} 번으로 받아요.",
                                     silent=True)
        return target, target_id

    async def repost_unsure(self, queue_id: int) -> tuple[str, str]:
        """대시보드 '없어요 · 다시 올리기' (★): '채널에 올라갔는지 모름' 글을 넣어 둔 링크로 다시 올린다 — 오늘 /link N <그 글의 링크>
        와 똑같다 (attach_link). 링크가 없으면 'no_link' (화면이 '링크 넣기' 를 보여 줌). (코드, 화면 글)."""
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return "not_found", ""
        if not self._maybe_posted(item):
            return "wrong_state", "지금은 '채널에 올라갔는지 모름' 딜이 아니에요"
        link = (item.deal.affiliate_url or "").strip()
        if not link.startswith("http") or channel_post_ref(link) is not None:
            return "no_link", ""
        reply = await self.attach_link(queue_id, link)
        after = self.db.get_queue_item(queue_id)
        if after is not None and after.status == "pending":
            return "done", "다시 올릴 차례에 넣었어요 — 곧 채널에 올라가요"
        return "wrong_state", truncate(re.sub(r"<[^>]+>", "", reply).split("\n", 1)[0], 100)

    # ------------------------------------------------------------ 대시보드 앱이 읽는 값 (dashboard/state.py)
    def queue_view(self, limit: int = 10) -> list[dict[str, Any]]:
        """/queue 의 값 (글이 아니라 줄들): 올릴 차례를 기다리는 딜."""
        out = []
        for it in self.db.pending_items(limit):
            p = it.deal.product
            shop = self.registry.get(p.shop)
            out.append({"qid": it.id, "title": truncate(clean_name(p.name), 40), "shop": shop.name if shop else p.shop,
                        "price": f"{p.price:,}원" if p.has_price else "", "at": it.created_at.timestamp(), "url": None,
                        "photo": "owner" if p.extra.get("owner_photo") else "none", "score": it.score})
        return out

    def toss_counts(self, since: datetime) -> dict[str, int]:
        """딜 탭 한 줄 '토스 후보 N · 링크 대기 N · 오늘 올림 N' (주인 10/23): 후보 = 올릴 차례를 기다리는 토스 딜 + 링크 대기,
        링크 대기 = 내 링크를 기다리는 토스 딜, 오늘 올림 = since(오늘 0시) 뒤 채널에 올린 토스 딜 (연습 모드 글은 뺌)."""
        waiting = self.db.shop_queue_count(("toss",), LINK_WAITING)
        return {"candidates": waiting + self.db.shop_queue_count(("toss",), ("pending",)), "waiting": waiting,
                "today": self.db.count_posts_since(since, product_prefix="toss:")}

    def recent_view(self, limit: int = 10) -> list[dict[str, Any]]:
        """/recent 의 값: 최근에 올린 글."""
        out = []
        for r in self.db.recent_posts(limit):
            at = from_iso(r.get("posted_at"))
            out.append({"title": truncate(clean_name(r.get("name") or r["product_id"]), 40), "price": f"{r['price']:,}원" if r.get("price") else "",
                        "where": self.notifier.source_label(r.get("source") or ""), "at": at.timestamp() if at else None})
        return out

    def errors_view(self, limit: int = 8) -> list[dict[str, Any]]:
        """/errors 의 값: 최근 오류 (한 줄씩, 토큰처럼 보이는 글자는 가림)."""
        from dealbot.dashboard.model import safe_text
        from dealbot.monitoring.admin import describe_kind

        out = []
        for e in self.db.recent_events(limit, level="ERROR"):
            at = from_iso(e.get("ts"))
            out.append({"at": at.timestamp() if at else None, "where": describe_kind(e["kind"], self.notifier.labels),
                        "text": safe_text((e.get("message") or "").splitlines()[0] if e.get("message") else "", 120)})
        return out

    def hot_view(self, n: int = 5, hours: int = 24) -> list[dict[str, Any]]:
        """/hot 의 값: 최근 추천을 많이 받은 글."""
        out = []
        for it in self.db.hot_items(utcnow() - timedelta(hours=hours), min_recommend=max(1, min(n, 100))):
            at = from_iso(it.get("first_seen_at"))
            url = it.get("url") if isinstance(it.get("url"), str) and str(it.get("url")).startswith("https://") else None
            out.append({"title": truncate(it.get("title") or "", 70), "where": self.notifier.source_label(it["source"]),
                        "recommend": it.get("recommend"), "views": it.get("views"), "comments": it.get("comments"),
                        "at": at.timestamp() if at else None, "url": url})
        return out

    def find_view(self, keyword: str) -> list[dict[str, Any]]:
        """/find 의 값: 수집한 글 중 낱말이 든 것."""
        out = []
        for it in self.db.find_items(keyword[:40]):
            at = from_iso(it.get("first_seen_at"))
            url = it.get("url") if isinstance(it.get("url"), str) and str(it.get("url")).startswith("https://") else None
            out.append({"title": truncate(it.get("title") or "", 70), "where": self.notifier.source_label(it["source"]),
                        "recommend": it.get("recommend"), "at": at.timestamp() if at else None, "url": url})
        return out

    def replaced_link_view(self) -> list[QueueItem]:
        """더 좋은 딜에 자리를 내준 글 중 아직 늦게 링크를 받는 것 (유효 시간 안, 같은 상품이 다시 줄 서지 않음)."""
        ttl = self._link_ttl()
        now = utcnow()
        out = []
        for r in self.db._q("SELECT * FROM deal_queue WHERE status = 'skipped' AND last_error LIKE ? AND created_at >= ? "
                            "ORDER BY created_at", (REPLACED + "%", to_iso(now - ttl))):
            it = self.db._row_to_queue_item(r)
            if not it.link_replaced or now - it.created_at >= ttl:
                continue
            if self.db.items_for_product(it.product_id, OPEN_STATUSES):
                continue  # 같은 상품이 다시 줄 섬 — 그 글이 따로 보인다
            out.append(it)
        return out

    def late_photo_view(self, now: datetime | None = None) -> list[tuple[int, datetime]]:
        """사진을 기다리는 딜 (번호, 끝나는 시각) — 받는 창이 열린 것만."""
        now = now or self.clock()
        out = []
        for r in self.db._q("SELECT key FROM kv WHERE key LIKE 'late_photo:%'"):
            raw = str(r["key"]).split(":", 1)[1]
            if not raw.isdigit():
                continue
            info = self.late_photo.waiting(int(raw), now)
            until = from_iso(info.get("until")) if info else None
            if until is not None:
                out.append((int(raw), until))
        return sorted(out, key=lambda x: x[1])

    def blog_ids(self) -> dict[str, str]:
        """Blog-Auto 블로그 이름 → 네이버 블로그 id (대시보드 카드 설정 — '직접 발행한 주소' 는 이 블로그 글만 받음)."""
        out = {}
        for c in self.settings.dashboard.cards:
            blog, bid = c.options.get("blog"), c.options.get("blog_id")
            if c.type == "naver_blog" and isinstance(blog, str) and isinstance(bid, str):
                out[blog.strip().lower()] = bid.strip().lower()
        return out

    def _blog_export(self) -> Any:
        reader = getattr(getattr(self.dashboard, "ctx", None), "blogauto", None) if self.dashboard is not None else None
        try:
            return reader.read() if reader is not None else None
        except Exception:  # noqa: BLE001
            return None

    def _todo_brief(self) -> list[dict[str, Any]]:
        """정리의 '기다리는 일' — 대시보드 할 일 줄 (종류·제목만)."""
        if self.dash_state is None:
            return []
        try:
            return [{"kind": it.get("kind"), "title": it.get("title"), "iid": it.get("iid"), **({"n": it["n"]} if "n" in it else {})}
                    for it in self.dash_state.items()]
        except Exception as e:  # noqa: BLE001
            log.warning("todo brief failed: %s", e)
            return []

    def todo_count(self) -> int:
        """앱 아이콘 숫자 (할 일 수)."""
        return len(self._todo_brief())

    def attach_auto_link(self, queue_id: int, url: str) -> str | None:
        """쇼핑커넥트 자동 링크를 주인이 답장한 것처럼 붙인다 (같은 확인·같은 재사용 기억). 주인 알림은 없음 (주인 결정 10/11).
        붙였으면 None, 못 붙였으면 이유."""
        item = self.db.get_queue_item(queue_id)
        problem = self._link_problem(item, queue_id, url, link_ttl=self._link_ttl())
        if problem or item is None:
            return problem or "no item"
        self.db.set_queue_link(queue_id, url.strip(), auto=True)
        self._sc_store_joined(item, url)
        self.db.kv_set(f"mylink:{item.deal.product.product_id}", url.strip())
        self.db.log_event("INFO", "autolink", f"#{queue_id} {url}")
        return None

    def skip_item(self, queue_id: int) -> str:
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return f"#{queue_id} 번 글이 없습니다."
        if item.status in ("published",):
            return f"#{queue_id} 번은 이미 채널에 올라갔습니다."
        if item.status == autolink.STATUS:
            self._cancel_autolink(queue_id)
        self.db.update_queue_item(queue_id, status="skipped", error="skipped by admin")
        keys = [f"link:{queue_id}", f"review:{queue_id}"]
        if (item.last_error or "").startswith(MAYBE_POSTED):
            keys.append(f"unsure:{queue_id}")
        self.notifier.resolve(*keys)  # 아직 안 보낸 카톡 알림은 취소
        return f"⏭ #{queue_id} 번은 건너뛰었습니다. 올리지 않습니다."

    async def mark_posted(self, queue_id: int, link: str = "") -> str:
        """'채널에 올라갔는지 모름'(보낸 뒤 시간 초과) 글이 채널에 있다고 주인이 알려 줌 (/posted): 다시 보내지 않고 올라간 글로
        기록한 뒤, 그때 건너뛴 뒷일(블로그 내보내기·스레드·인스타·복붙 문구)을 이어서 한다. link: 채널 글 주소(있으면 품절 표시용 번호)."""
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return f"#{queue_id} 번 글이 없습니다."
        if not self._maybe_posted(item):
            return f"#{queue_id} 번은 '채널에 올라갔는지 모름' 글이 아닙니다 (지금 '{item.status}' 상태)."
        deal = item.deal
        message_id, note = self._own_post_number(link)
        note = f"\n{note}" if note else ""
        info = deal.product.deal_kind == "info"
        # 사진은 보낼 때처럼 확인된 상품 사진 (블로그·인스타가 쓴다). 정보 글은 본문이 없어 기록만 한다
        photo = None if info else await self.deal_photo(deal)
        self.db.record_post(
            deal,
            channel_id=str(self.publisher.channel_id) if self.publisher.channel_id is not None else None,
            message_id=message_id,
            dry_run=self.publisher.dry_run,
            now=utcnow(),
        )
        self.db.update_queue_item(queue_id, status="published", deal=deal)
        self.notifier.resolve(f"unsure:{queue_id}")
        self.db.log_event("INFO", "publish", f"{deal.product.product_id} 주인이 채널에서 확인 (/posted #{queue_id})"
                          + (f" 채널 글 {message_id}" if message_id else ""))
        if info:
            return f"✅ #{queue_id} 번을 채널에 올라간 글로 기록했어요.{note}"
        row = self.export_published(await self.channel_deal(deal, "blog"), photo)
        # 사진 기다림 기록을 인스타 대기열에 넣기 전에 — 인스타 시간표가 그 사이에 사진 없는 딜 글을 바로 올리지 않게
        await self._ask_late_photo(queue_id, deal, photo, row)
        await self._publish_side_channels(deal, photo=photo, deal_no=queue_id)
        return f"✅ #{queue_id} 번을 채널에 올라간 글로 기록했어요. 블로그·스레드·인스타 차례도 이어서 처리했어요.{note}"

    def _own_post_number(self, link: str) -> tuple[int | None, str]:
        """주인이 준 채널 글 링크 → (우리 채널의 글 번호, 번호를 쓰지 않은 까닭). 품절 표시는 이 번호의 우리 채널 글을 고쳐 쓰므로
        다른 채널의 글 번호를 쓰면 우리 채널의 엉뚱한 글을 덮어쓴다 → 우리 채널이 아닌 게 분명하면 번호를 쓰지 않는다
        (공개 채널은 이름, 비공개 채널은 -100 뒤 숫자로 비교. 우리 채널을 알 수 없으면 링크를 믿음)."""
        link = (link or "").strip()
        if not link:
            return None, ""
        ref = channel_post_ref(link)
        if ref is None:
            return None, "(채널 글 링크가 아니라 글 번호는 기록하지 않았어요 — 이 글은 품절 표시가 안 돼요)"
        where, number = ref
        cid = str(self.publisher.channel_id or "")
        if where.startswith("c/"):
            theirs, ours = where[2:], (cid[4:] if cid.startswith("-100") else None)
        else:
            base = self.notifier.channel_post_base
            theirs, ours = where.lower(), (base.rsplit("/", 1)[-1].lower() if base else None)
        if ours is not None and theirs != ours:
            return None, "(우리 채널 글 링크가 아니라 글 번호는 기록하지 않았어요 — 이 글은 품절 표시가 안 돼요)"
        return number, ""

    def approve_item(self, queue_id: int, text: str | None = None) -> str:
        """정보 글 확인 요청에 대한 답: 그대로(또는 관리자가 고쳐 쓴 본문으로) 올릴 차례에 넣는다."""
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return f"#{queue_id} 번 글이 없습니다."
        if item.status != "awaiting_approval":
            return f"#{queue_id} 번은 확인을 기다리는 글이 아닙니다 (지금 '{item.status}' 상태)."
        deal = item.deal
        p = deal.product
        self.notifier.resolve(f"review:{queue_id}")  # 답했음 — 아직 안 보낸 확인 요청 카톡은 취소
        if p.deal_kind != "info" and "info_draft" not in p.extra:
            # 정품 확인 요청에 대한 답: 링크 변환과 발행을 이어서 한다
            p.extra["auth_approved"] = True
            self.db.update_queue_item(queue_id, status="pending", error=None, deal=deal, reset_created=True)
            self.db.log_event("INFO", "authenticity", f"#{queue_id} 관리자가 정품으로 확인: {p.name[:50]}")
            return f"✅ #{queue_id} 번을 정품으로 확인했습니다. 링크를 만들어 채널에 올립니다."
        draft = dict(p.extra.get("info_draft") or {})
        edited = bool(text and text.strip())
        if edited:
            draft["text"] = str(text).strip()
        p.extra["info_draft"] = draft
        self.db.update_queue_item(queue_id, status="pending", error=None, deal=deal, reset_created=True)
        self.db.log_event("INFO", "info_review", f"#{queue_id} 관리자 확인" + (" (본문 고쳐 씀)" if edited else ""))
        return f"✅ #{queue_id} 번을 올릴 차례에 넣었습니다. 곧 채널에 올라갑니다." + (" 보내주신 글로 바꿨습니다." if edited else "")

    async def submit_manual(self, text: str) -> str:
        """관리자가 직접 보낸 딜을 대기열 맨 앞에 넣는다 (링크는 관리자가 만든 제휴 링크로 간주)."""
        try:
            post = parse_manual_post(text, self.registry)
        except ValueError as e:
            return f"⚠️ {e}"
        shop = self.registry.get(post.shop_key)
        now = utcnow()
        product = Product(
            source="manual",
            product_id=ShopRegistry.product_key(post.shop_key, post.url),
            shop=post.shop_key,
            deal_kind="hotdeal" if post.price else "event",
            name=post.name,
            price=post.price,
            url=post.url,
            headline=post.headline,
            image_url=post.image_url,
            affiliate_url=post.url,
        )
        if self.settings.deal.enrich.enabled and (not product.image_url or not product.has_price or product.rating is None):
            await self._enrich(product)
        if product.has_price:
            stats = self.db.price_stats(product.product_id, self.settings.deal.history_days, now)
            verdict = self.evaluator.evaluate(product, stats)
            self.db.record_observation(product, now)
        else:
            verdict = DealVerdict(is_deal=True)
        verdict.is_deal = True
        verdict.reasons = ["manual"] + [r for r in verdict.reasons if r != "manual"]
        verdict.score = 1000.0
        deal = Deal(product=product, verdict=verdict, affiliate_url=post.url, detected_at=now)
        if self._posted_recently(product.product_id, now):
            return f"⚠️ 최근 {self.settings.publish.dedup_days}일 안에 이미 올린 상품이에요: {product.name[:40]}"
        if not self.db.enqueue(deal, score=1000.0, now=now):
            return "⚠️ 이미 기다리는 글에 들어 있는 상품입니다."
        shop_name = shop.name if shop else post.shop_key
        return (
            f"📝 맨 앞에 넣었습니다 [{shop_name}] {product.name[:50]}"
            + (f" — {product.price:,}원" if product.has_price else "")
            + "\n올릴 수 있는 차례가 되는 대로 바로 채널에 올라갑니다."
        )

    async def add_commentary(self, deal: Deal) -> None:
        """한줄평 + 스레드 첫 줄·판단·짧은 이름 (서버 Claude 로그인). 데이터로 확인된 정보만 넘긴다.
        실패해도 발행은 그대로: comment 칸을 None 으로 채워 발행 큐가 같은 글에 다시 묻지 않게 한다."""
        p = deal.product
        f = self.publisher.renderer.deal_facts(deal)
        # 등급 낱말('역대급'·'강력 추천')은 봇의 판단이라 '확인된 정보'로 안 넘김 (같은 말이 금지어라 한줄평이 버려짐)
        labels = [x for x in (f.get("labels") or []) if x not in ("역대급", "강력 추천")]
        facts = " / ".join(str(x) for x in (f.get("evidence"), f.get("unit"), f.get("ship"), ", ".join(labels)) if x)
        # 글의 다른 줄에 이미 찍히는 것 (첫 줄 가격·근거, ⚖️ 단가, 🚚 배송) — 한줄평이 같은 숫자를 되풀이하지 않게
        shown = (f"{p.price:,}원" if p.has_price else None, f.get("evidence_short"), f.get("unit_each"), f.get("ship"))
        try:
            wrote = await self.commentator.write(deal, facts, shown=shown)
        except Exception:  # noqa: BLE001 — 한줄평 때문에 발행이 멈추면 안 됨
            log.exception("commentary failed")
            wrote = {}
        alert = self.commentator.take_alert()  # 연달아 실패해서 쉬기 시작했으면 관리자에게 한 번 알림
        if alert:
            try:
                await self.notifier.notify_error("commentary", alert)
                await self.notifier.alert(  # 주인이 서버에서 다시 로그인해야 풀림 → 카톡 (정보 글 요약과 같은 키)
                    "claude_login", None,
                    kakao_fit("⚠️[할 일] 딜봇 서버 claude 로그인을 확인해 주세요", "한줄평·정보 글 요약이 멈췄어요 (발행은 계속)"),
                    key="login:claude_cli", short="딜봇 claude 로그인",
                )
            except Exception:  # noqa: BLE001
                log.exception("commentary alert failed")
        deal.product.extra["comment"] = wrote.get("comment")
        for k in ("thread_hook", "thread_take", "short_name"):
            if wrote.get(k):
                deal.product.extra[k] = wrote[k]

    async def test_post(self) -> str:
        """샘플 딜을 관리자 챗에 보내 양식 확인."""
        from dealbot.cli import sample_deal

        if self.bot is None or not self.notifier.enabled:
            return "텔레그램 봇 토큰과 관리자 챗 ID 가 있어야 합니다."
        deal = sample_deal()
        await self.add_commentary(deal)  # 샘플에도 실제처럼 한줄평·스레드 문구를 붙여 보여 줌
        photo = await self.deal_photo(deal)  # 실제 글처럼 확인된 상품 사진만 (샘플의 쿠팡 로고는 사진으로 안 씀)
        original = (self.publisher.channel_id, self.publisher.dry_run)
        try:
            self.publisher.channel_id = self.notifier.chat_id
            self.publisher.dry_run = False
            result = await self.publisher.publish(deal, photo=photo)
        finally:
            self.publisher.channel_id, self.publisher.dry_run = original
        if not result.ok:
            return f"❌ 보내지 못했습니다: {result.error}"
        text = "✅ 위 메시지가 채널에 올라갈 글 양식입니다."
        if self.settings.threads.enabled:
            text += "\n\n" + self._threads_preview(deal, "샘플 딜") + "\n실제로 올려 보려면 /threadstest"
        return text

    def _threads_preview(self, deal: Deal, label: str) -> str:
        hook, reply = self.threads.render(deal), self.threads.render_reply(deal)
        text = f"🧵 <b>스레드에는 이렇게 올라갑니다 ({html.escape(label)})</b>\n<pre>{html.escape(hook)}</pre>"
        if reply:
            text += f"\n답글:\n<pre>{html.escape(reply)}</pre>"
        return text

    async def threads_test(self, queue_id: int | None = None) -> str:
        """스레드에 실제로 올려 양식을 본다. 번호 없으면 샘플 딜, 번호가 있으면 채널에 올렸던 그 글을 다시.
        자동 게시(config threads.enabled)가 꺼져 있어도 시험 글은 올린다 — 차단이 풀렸는지 켜기 전에 확인하는 용도 (주인 10/12)."""
        from dealbot.cli import sample_deal

        if self.threads.stored_token() is None:
            return "스레드가 연결되어 있지 않습니다. /threadsauth 먼저 해 주세요."
        if queue_id is None:
            deal, label = sample_deal(), "샘플 딜"
        else:
            item = self.db.get_queue_item(queue_id)
            if item is None:
                return f"#{queue_id} 글이 없습니다."
            if item.status != "published":
                return f"#{queue_id} 는 채널에 올라간 글이 아닙니다 (상태 {item.status})."
            deal, label = item.deal, f"#{queue_id} {item.deal.product.name[:30]}"
        if "comment" not in deal.product.extra:  # /test 처럼 실제 글과 같은 AI 첫 줄·판단·짧은 이름으로 미리보기
            await self.add_commentary(deal)
        if queue_id is None:
            await self.deal_photo(deal)  # 샘플도 실제처럼 확인된 상품 사진만 (샘플의 쿠팡 로고는 사진으로 안 씀)
        preview = self._threads_preview(deal, label)
        if self.threads.dry_run:
            return preview + "\n\n연습 모드라 실제로 올리지는 않았습니다."
        result = await self.threads.publish(deal, force=True)  # 자동 게시가 꺼져 있어도 시험 글은
        if not result.ok:
            return preview + f"\n\n❌ 올리지 못했습니다: <code>{html.escape(result.error or '')}</code>"
        link = await self._threads_permalink(result.message_id)
        lines = [preview, "", "✅ 스레드에 올렸습니다" + (f": {link}" if link else "")]
        if result.error:
            lines.append(f"참고: {html.escape(result.error)}")
        if queue_id is None:
            lines.append("샘플 글이니 확인한 뒤 스레드 앱에서 지워 주세요.")
        if not self.settings.threads.enabled:
            lines.append("자동 게시는 아직 꺼져 있어요 (시험 글만 올렸어요). 켜려면 config threads.enabled 를 true 로.")
        return "\n".join(lines)

    async def _threads_permalink(self, message_id: int | None) -> str | None:
        token = self.threads.stored_token()
        if token is None or message_id is None:
            return None
        try:
            return await self.threads.client.permalink(token, str(message_id))
        except ThreadsError as e:
            log.warning("threads permalink lookup failed: %s", e)
            return None

    async def _instagram_permalink(self, message_id: int | None) -> str | None:
        token = self.instagram.stored_token()
        if token is None or message_id is None:
            return None
        try:
            return await self.instagram.client.permalink(token, str(message_id))
        except InstagramError as e:
            log.warning("instagram permalink lookup failed: %s", e)
            return None

    async def push_test(self) -> str:
        """휴대폰 푸시(ntfy/Pushover) 연결 확인용 테스트 알림."""
        pcfg = self.settings.monitoring.push
        events = ", ".join(pcfg.events) or "(없음)"
        if not self.push.enabled:
            return (
                "📵 휴대폰 푸시가 설정되어 있지 않습니다.\n"
                "Railway Variables 에 <code>NTFY_TOPIC</code> (ntfy 앱에서 구독한 토픽 이름) 또는 "
                "<code>PUSHOVER_USER_KEY</code> + <code>PUSHOVER_APP_TOKEN</code> 을 넣고 다시 배포하세요."
            )
        ok = await self.push.send(
            "DealBot 푸시 테스트",
            "이 알림이 보이면 휴대폰 푸시 연결이 정상입니다.",
            click_url=self.notifier.telegram_link,
            tags=["white_check_mark"],
        )
        if not ok:
            return f"❌ {self.push.provider} 전송 실패 — 서버 로그(또는 /errors)를 확인하세요."
        where = f"ntfy 토픽 <code>{html.escape(self.settings.secrets.ntfy_topic or '')}</code> ({html.escape(pcfg.ntfy_url)})" if self.push.provider == "ntfy" else "Pushover"
        return (
            f"📲 {where} 로 테스트 푸시를 보냈습니다.\n"
            "폰에 안 뜨면: ntfy 앱에서 구독한 토픽 이름이 NTFY_TOPIC 과 글자까지 같은지, 앱 알림 권한이 켜져 있는지 확인하세요.\n"
            f"평소 푸시가 오는 경우: <code>{html.escape(events)}</code> (config.yaml 의 monitoring.push.events)"
        )

    async def naver_login(self) -> tuple[bytes | None, str]:
        if self.naver_connect is None:
            return None, "브라우저 자동화가 꺼져 있습니다. config.yaml 의 browser.enabled: true 로 켜고 재시작하세요."
        try:
            if await self.naver_connect.is_logged_in():
                return None, "이미 네이버에 로그인되어 있습니다. 다시 하려면 프로필 폴더를 지우고 재시작하세요."
            png = await self.naver_connect.start_qr_login()
            return png, "네이버 앱 → 렌즈(QR) 로 이 화면의 QR 을 스캔하고 숫자를 선택하세요. 2분 안에 로그인되면 알려드립니다."
        except Exception as e:  # noqa: BLE001
            log.exception("naver login failed")
            return None, f"❌ 로그인 화면 열기 실패: {e}"

    async def naver_login_wait(self) -> str:
        if self.naver_connect is None:
            return "브라우저 자동화가 꺼져 있습니다."
        ok = await self.naver_connect.wait_login(timeout_seconds=120)
        if ok:
            self.db.log_event("INFO", "naver", "logged in via QR")
            return "✅ 네이버 로그인 완료. 이제 네이버 딜은 자동으로 링크를 만듭니다 (실패하면 수동 요청)."
        return "⏰ 2분 안에 로그인이 확인되지 않았습니다. /naverlogin (또는 대시보드 연결 탭 '로그인 관리' › QR 로그인) 을 다시 해 주세요."

    async def screenshot(self, url: str) -> tuple[bytes | None, str]:
        if self.browser is None:
            return None, "브라우저 자동화가 꺼져 있습니다 (browser.enabled)."
        try:
            png = await self.browser.screenshot(url)
            return png, url
        except Exception as e:  # noqa: BLE001
            return None, f"❌ 스크린샷 실패: {e}"

    async def fetch_html(self, url: str) -> tuple[bytes | None, str]:
        """서버에서 페이지 원문을 받아 그대로 돌려준다 (셀렉터 조정용). 브라우저가 켜져 있으면 렌더링된 DOM 을 준다."""
        try:
            if self.browser is not None:
                async def _dom(page: Any) -> str:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(1500)
                    return await page.content()

                html = await self.browser.run(_dom)
                return html.encode("utf-8"), f"렌더링된 DOM ({len(html):,}자)"
            resp = await self.http.get(url, follow_redirects=True, timeout=30)
            from dealbot.collectors.ppomppu import decode_html

            html = decode_html(resp)
            return html.encode("utf-8"), f"HTTP {resp.status_code} ({len(html):,}자)"
        except Exception as e:  # noqa: BLE001
            return None, f"❌ 가져오기 실패: {e}"

    async def naver_link_test(self, url: str) -> str:
        if self.naver_connect is None:
            return "브라우저 자동화가 꺼져 있습니다 (browser.enabled)."
        try:
            link = await self.naver_connect.convert(url)
            return f"✅ {link}"
        except Exception as e:  # noqa: BLE001
            return f"❌ 실패: {e}\n/shot {self.settings.browser.naver_connect.create_url} 로 화면을 확인해 셀렉터를 조정하세요."

    async def self_check(self) -> list[tuple[bool | None, str]]:
        """(ok|None=skip, 설명) 목록. 시작 알림과 `dealbot check` 가 공유."""
        out: list[tuple[bool | None, str]] = []
        try:
            self.publisher.render(__import__("dealbot.cli", fromlist=["sample_deal"]).sample_deal())
            out.append((True, "템플릿 렌더링"))
        except Exception as e:  # noqa: BLE001
            out.append((False, f"템플릿: {e}"))

        sid = self.settings.secrets.coupang_sub_id
        if sid and re.fullmatch(r"AF\d{5,}", sid.strip()):  # 10/10: 채널 ID 자리에 파트너스 코드(AF…)를 넣은 일
            out.append((False, "쿠팡 서브 ID 가 파트너스 코드(AF…) 모양 — 쿠팡 파트너스에서 만든 채널 ID 를 넣어야 채널별 실적이 잡힙니다"))
        if sid and not is_ascii(sid):  # 값은 비밀이 아니지만 그대로 찍지는 않는다
            out.append((False, f"쿠팡 서브 ID 에 한글이 섞여 있음 (한/영 전환 실수로 보임) — 링크에는 영문 "
                               f"'{from_korean_keyboard(sid)}' 로 바꿔 씁니다. setenv 로 COUPANG_SUB_ID 를 영문으로 다시 넣어 주세요"))
        if self.coupang is None:
            out.append((None, "쿠팡 API: 키 미설정 (쿠팡 수집/딥링크 꺼짐)"))
        else:
            try:
                n = await self.coupang.ping()
                out.append((True, f"쿠팡 API 연결 (골드박스 {n}건)"))
            except Exception as e:  # noqa: BLE001
                out.append((False, f"쿠팡 API: {e}"))

        if self.bot is not None:
            try:
                me = await self.bot.get_me()
                out.append((True, f"텔레그램 봇 @{me.username}"))
                if self.publisher.channel_id is not None:
                    chat = await self.bot.get_chat(self.publisher.channel_id)
                    member = await self.bot.get_chat_member(self.publisher.channel_id, me.id)
                    if member.status in ("administrator", "creator"):
                        out.append((True, f"채널 '{chat.title or chat.username}' 관리자 권한"))
                    else:
                        out.append((False, f"채널 '{chat.title or chat.username}' 에서 봇이 관리자가 아님 (메시지 게시 권한 필요)"))
                else:
                    out.append((None, "채널: TELEGRAM_CHANNEL_ID 미설정 (dry-run)"))
            except Exception as e:  # noqa: BLE001
                out.append((False, f"텔레그램: {e}"))

            # 관리자 챗: 여기로 상태·링크요청이 갑니다. 안 되면 봇이 조용해지므로 명확히 알린다.
            if self.settings.secrets.telegram_admin_chat_id is None:
                out.append((False, "관리자 챗: TELEGRAM_ADMIN_CHAT_ID 미설정 — 알림을 받을 수 없습니다"))
            else:
                try:
                    chat = await self.bot.get_chat(self.notifier.chat_id)
                    out.append((True, f"관리자 챗 연결 ({chat.full_name or chat.username or chat.id})"))
                except Exception as e:  # noqa: BLE001
                    out.append((False, f"관리자 챗 전송 불가: {e} — 봇에게 /start 를 먼저 보내세요"))
        else:
            out.append((None, "텔레그램: 토큰 미설정 (오프라인)"))

        out.append((True, f"휴대폰 푸시: {self.push.provider}") if self.push.enabled else (None, "휴대폰 푸시: 미설정 (텔레그램 알림만)"))
        if self.store_browser.enabled:
            out.append(store_browser_check())
        if not self.settings.threads.enabled:
            out.append((None, "스레드: 꺼짐"))
        elif not self.settings.secrets.has_threads_app:
            out.append((None, "스레드: 앱 ID/시크릿 미설정"))
        elif self.threads.stored_token() is None:
            out.append((None, "스레드: 인증 필요 (/threadsauth)"))
        else:
            try:
                stored = self.threads.stored_token() or ThreadsToken("", "")
                me = await self.threads.client.me(stored.access_token)
                out.append((True, f"스레드 @{me.get('username')}"))
            except Exception as e:  # noqa: BLE001
                out.append((False, f"스레드: {e}"))
        if not self.settings.instagram.enabled:
            out.append((None, "인스타그램: 꺼짐"))
        elif not self.settings.secrets.has_instagram_app:
            out.append((None, "인스타그램: 앱 ID/시크릿 미설정"))
        elif self.instagram.stored_token() is None:
            out.append((None, "인스타그램: 인증 필요 (/instaauth)"))
        elif not self.settings.secrets.public_base_url:
            out.append((False, "인스타그램: 공개 도메인이 없어 카드 이미지를 못 올림 (Railway → Networking → Generate Domain)"))
        else:
            out.append((True, f"인스타그램 @{self.db.kv_get(IG_KV_USERNAME) or '?'} (카드 이미지 {self.settings.secrets.public_base_url}/media/…)"))
            if self.settings.instagram.reels.enabled and reel_tools.find_ffmpeg() is None:
                out.append((None, "인스타 점심 릴스: 서버에 ffmpeg 없음 — sudo apt-get install -y ffmpeg (그 전엔 릴스만 건너뜀)"))
        if self.copy_blocks.enabled:
            out.append((True, "복붙 문구: " + ", ".join(t.name for t in self.settings.copy_cfg.targets if t.enabled)))
        if self.market is not None:
            out.append((True, f"시중가 대조: 쿠팡 검색 (시간당 {self.settings.deal.market.max_checks_per_hour}회)"))
        else:
            out.append((None, "시중가 대조: 꺼짐 (쿠팡 키 필요)"))
        if self.naver_ref is None:
            # 네이버 쇼핑 검색 API 는 끝났다 (10/15): 키를 고칠 일이 아님을 분명히 (예전엔 시작할 때마다 401 경고가 남았음)
            out.append((None, "네이버 쇼핑 검색: 2026-07-31 종료 — 대신할 API 없음, 키 조치 필요 없음 "
                              "(네이버 딜 사진은 주인이 링크와 같이 보낸 사진, 쿠팡 딜은 가격 이력으로 판정)"))
        elif not self.naver_shop.configured:
            out.append((None, "쿠팡 딜 네이버 최저가 비교: 꺼짐 (NAVER_CLIENT_ID/SECRET 없음 — developers.naver.com 검색 API 키)"))
        elif self.naver_shop.disabled_reason:
            out.append((None, f"쿠팡 딜 네이버 최저가 비교: 꺼짐 ({self.naver_shop.disabled_reason} — 키 확인 후 재시작)"))
        elif not self.naver_shop.verified:
            # 키가 맞는지는 첫 검색 때야 안다 (10/14 서버 키는 401): 그전엔 켜졌다고 하지 않음. 401 이면 꺼짐 줄로 바뀜
            out.append((None, "쿠팡 딜 네이버 최저가 비교: 키 있음 — 첫 조회 때 확인 (401 이면 꺼짐, /status)"))
        else:
            out.append((True, f"쿠팡 딜 네이버 최저가 비교 (시간당 {self.settings.deal.naver_reference.max_lookups_per_hour}회까지)"))
        if self.settings.browser.enabled:
            if self.naver_connect is None:
                out.append((False, "브라우저 자동화: 켜져 있으나 playwright/크로미움 없음"))
            else:
                try:
                    logged = await self.naver_connect.is_logged_in()
                    out.append((True, "네이버 브라우저 로그인 유지 중") if logged else (None, "네이버 브라우저: 로그인 필요 (대시보드 연결 탭 '로그인 관리' › QR 로그인 · /naverlogin)"))
                except Exception as e:  # noqa: BLE001
                    out.append((False, f"브라우저: {e}"))
        if self.settings.secrets.has_linkprice:
            out.append((True, "링크프라이스 ID 설정됨"))
        else:
            out.append((None, "링크프라이스: 미설정 (11번가/G마켓/알리 등은 원본 링크)"))
        if self.dashboard is None:
            # 대시보드는 덤 — ❌ 면 '딜봇 멈춤' 카톡이 가므로 ⚠️ 로
            out.append((None, "채널 대시보드: 만들지 못함 (로그 확인)" if self.settings.dashboard.enabled else "채널 대시보드: 꺼짐"))
        else:
            out.extend(self.dashboard.self_check())
        out.extend(self._notify_self_check())
        try:  # 내 도메인 (https.sh --domain 으로 옮겼을 때만): DNS A 레코드가 이 서버를 가리키는지. 할 일 알림은 봇 시작 때 따로
            line = await growth_alerts.domain_check(self, alert=False)
            if line is not None:
                out.append(line)
        except Exception as e:  # noqa: BLE001
            out.append((None, f"내 도메인 확인 못 함: {type(e).__name__}"))
        return out

    def _notify_self_check(self) -> list[tuple[bool | None, str]]:
        """주인 알림 길 (주인 10/18): 알림 키 (값은 말하지 않음 — '있음' 만) · 기기 수 · Blog-Auto 다리."""
        if self.router is None:
            return [(None, "대시보드 앱 알림: 꺼짐 (notify.router: legacy — 예전처럼 카톡·텔레그램)")]
        out: list[tuple[bool | None, str]] = []
        if self.router.load_key():
            n = len(self.router.devices())
            out.append((True, f"대시보드 앱 알림 키 있음 · 알림 켠 기기 {n}대") if n else
                       (None, "대시보드 앱 알림 키 있음 · 아직 알림 켠 기기 없음 (급한 일은 카톡으로) — 앱 '연결' 에서 알림 켜기"))
        else:
            out.append((False, f"대시보드 앱 알림 키: {self.router.key_error} — 푸시 꺼짐 (파일은 그대로 둠)"))
        st = read_blogauto(self.settings.data_dir)
        if st.bridge_ok():
            out.append((True, f"Blog-Auto 다리 (대시보드 블로그 일 {len(st.actions)}가지)"))
        else:
            out.append((None, "Blog-Auto 다리: 아직 없음 또는 15분 넘게 신호 없음 — 대시보드의 블로그 일은 잠시 쉼"))
        return out

    # ------------------------------------------------------------- pipeline
    def _shop_allowed(self, product: Product) -> bool:
        shop = self.registry.get(product.shop)
        if shop is None:
            return self.settings.publish.allow_raw_links
        return shop.enabled and shop.link_mode != "skip"

    def _has_shop_url(self, product: Product) -> bool:
        """원본 주소가 그 쇼핑몰 주소인지. 게시판 글 주소만 남은 딜(이벤트 글 등)은 제휴 링크를 만들 수 없다."""
        if not self.settings.deal.require_shop_url or product.affiliate_url:
            return True
        shop = self.registry.get(product.shop)
        if shop is None or shop.link_mode == "raw":
            return True
        return shop.matches_url(product.url)

    def _queue_info_post(self, p: Product, now: Any) -> bool:
        """정보 글 후보를 대기열에 넣는다: 게시판 글이고 추천이 충분하며 오늘 상한을 안 넘겼을 때."""
        cfg = self.settings.info_posts
        post_url = p.extra.get("post_url")
        if not cfg.enabled or not post_url:
            return False
        rec = p.recommend_count or 0
        if rec < cfg.min_recommend:
            return False
        day_start = local_now(self.settings.app.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        if cfg.max_per_day > 0 and self.db.count_posts_since(day_start, product_prefix="info:") >= cfg.max_per_day:
            log.info("info post daily cap reached — skip %s", p.name[:40])
            return False
        key = p.external_id or ShopRegistry.product_key(p.shop, p.url).split(":", 1)[-1]
        info = replace(p, product_id=f"info:{p.source}:{key}", deal_kind="info", url=str(post_url), affiliate_url=None)
        if self._posted_recently(info.product_id, now) or self._declined_recently(info, now):
            return False
        dup = self._duplicate_of_recent(info, now)
        if dup:
            log.info("info post %s skipped as duplicate: %s", p.name[:40], dup)
            self.db.log_event("INFO", "dedupe", f"{info.product_id} {p.name[:50]} — {dup}")
            return False
        verdict = DealVerdict(is_deal=True, reasons=["info_post", f"recommend>={cfg.min_recommend}"], score=float(min(rec, 100)))
        return self.db.enqueue(Deal(product=info, verdict=verdict, detected_at=now), score=verdict.score, now=now)

    async def _publish_info(self, item: QueueItem, deal: Deal) -> bool:
        """정보 글 발행: 게시판 글을 다시 읽어 본문·사진을 정리해 채널에 올린다. 링크 변환 없음."""
        cfg = self.settings.info_posts
        p = deal.product
        pid = p.product_id
        post_url = str(p.extra.get("post_url") or p.url)
        draft = p.extra.get("info_draft")
        if isinstance(draft, dict):
            # 관리자가 확인(/ok)해 준 글: 다시 읽거나 요약하지 않고 그대로 올린다
            body = PostBody(text=str(draft.get("text") or ""), images=list(draft.get("images") or []), links=list(draft.get("links") or []))
        else:
            body = await self.info.fetch(post_url)
            if body is None or body.empty:
                self.db.update_queue_item(item.id, status="skipped", error="post body not readable")
                self.db.log_event("INFO", "info_skip", f"{pid} 본문을 읽지 못해 건너뜀: {post_url}")
                log.info("info post #%d skipped — could not read body (%s)", item.id, post_url)
                return True
            summary = await self.summarizer.summarize(
                title=p.name, source=str(p.extra.get("source_label") or p.source), text=body.raw or body.text, links=body.links
            )
            if summary.skip:
                self.db.update_queue_item(item.id, status="skipped", error="summarizer: not worth posting")
                self.db.log_event("INFO", "info_skip", f"{pid} 요약기가 올릴 내용이 아니라고 판단: {p.name[:60]}")
                log.info("info post #%d skipped — summarizer says not worth posting", item.id)
                return True
            ok, detail = self._info_discount_ok(p.name, body, summary)
            if not ok:
                self.db.update_queue_item(item.id, status="skipped", error=f"benefit below threshold ({detail})")
                self.db.log_event("INFO", "info_skip", f"{pid} 혜택이 기준 미만이라 건너뜀 ({detail}): {p.name[:60]}")
                log.info("info post #%d skipped — benefit below threshold (%s)", item.id, detail)
                return True
            if summary.text:
                body.text = summary.text
            reason = self._info_review_reason(cfg.review, body, summary)
            if reason:
                return await self._request_info_review(item, deal, body, reason)
        text = self.info.render(cfg.template, deal, body, autoescape=True)
        photo: bytes | None = None
        if cfg.send_photo and body.images and not self.publisher.dry_run:
            photo = await self.info.download_image(body.images[0], referer=post_url)
        silent = in_time_window(local_now(self.settings.app.timezone), self.settings.publish.quiet_hours)
        result = await self.publisher.publish_raw(text, photo=photo, silent=silent)
        if not result.ok:
            await self._handle_publish_failure(item, result.error or "unknown error", deal=deal, maybe_posted=result.maybe_posted)
            return True
        self.db.record_post(
            deal,
            channel_id=str(self.publisher.channel_id) if self.publisher.channel_id is not None else None,
            message_id=result.message_id,
            dry_run=result.dry_run,
            now=utcnow(),
        )
        p.extra["info_body"] = body.text  # 밤에 블로그 정리 글에 다시 쓰려고 남겨 둔다
        p.extra["info_links"] = list(body.links)
        self.db.update_queue_item(item.id, status="published", deal=deal)
        self.db.log_event("INFO", "publish", f"{pid} [info] {p.name[:60]}")
        log.info("published info post [%s] %s (%s)", p.source, p.name[:50], "dry-run" if result.dry_run else result.message_id)
        if result.dry_run:
            await self.notifier.notify_published(deal, result, preview=text)
            return True
        await self.notifier.notify_published(deal, result)
        # 스레드: 별도 양식으로 한 글 — 요약 + 원문 링크만, 게시판 사진은 쓰지 않는다 (주인 결정 10/15: 남의 사진을 퍼 나르지 않음).
        # 딜 글과 같은 리듬(07~24시·45분 간격)으로 스레드 대기열에서 올라간다
        if self.settings.threads.enabled and self.threads.configured:
            try:
                job = self.threads_flow.enqueue_info(deal, self.info.render(cfg.threads_template, deal, body, autoescape=False),
                                                     queue_id=item.id)
                if job is not None and not self.settings.threads.rhythm.enabled:
                    await self.threads_flow.post_now(job)
                elif job is not None:
                    await self.threads_flow.tick()
            except Exception as e:  # noqa: BLE001
                log.warning("threads info post error: %s", e)
        # 카톡: 복붙 문구
        if self.copy_blocks.enabled:
            try:
                kakao = self.info.render(cfg.kakao_template, deal, body, autoescape=False)
                block = CopyBlock(key="kakao", name="카카오 오픈채팅", text=kakao)
                await self.notifier.send(block.as_telegram_html(), silent=True)
            except Exception as e:  # noqa: BLE001
                log.warning("kakao info copy failed: %s", e)
        return True

    def _info_discount_ok(self, title: str, body: PostBody, summary: Summary) -> tuple[bool, str]:
        """이벤트성 글은 할인율 또는 할인 금액이 기준 이상일 때만 올린다. 요약기가 읽은 값을 쓰고, 없으면 본문 숫자로 대략 본다."""
        cfg = self.settings.info_posts
        if cfg.min_discount_rate <= 0 and cfg.min_discount_amount <= 0:
            return True, "기준 없음"
        rate, amount = summary.discount_rate, summary.discount_amount
        how = "요약기"
        if rate is None and amount is None:
            rate, amount = estimate_discount(f"{title}\n{body.raw or body.text}")
            how = "본문 숫자"
        ok = bool((rate or 0) >= cfg.min_discount_rate > 0 or (amount or 0) >= cfg.min_discount_amount > 0)
        detail = f"{how}: 할인율 {rate if rate is not None else '?'}% · 할인액 {f'{amount:,}원' if amount is not None else '?'}"
        return ok, detail

    @staticmethod
    def _info_review_reason(review: str, body: PostBody, summary: Summary) -> str | None:
        """정보 글을 바로 올리지 않고 관리자 확인을 받아야 하면 그 이유, 아니면 None."""
        if review == "never":
            return None
        if review == "always":
            return "설정이 '항상 확인' (info_posts.review: always)"
        if not summary.text:
            return summary.error or "요약을 만들지 못함"
        if not body.confident:
            return "본문 위치를 확실히 찾지 못해 추정으로 정리함"
        return None

    def _review_backlog_full(self) -> int | None:
        """확인(/ok)을 기다리는 글이 이미 자리(max_awaiting_reviews)만큼 쌓여 있으면 그 수, 아니면 None. 0 = 제한 없음.
        링크 요청 자리와 따로 센다: 주인 결정 10/15 로 링크 자리를 15로 늘려도 확인 요청은 6개까지만 몰리고, 교체로 늘 차 있는
        링크 15칸 때문에 확인 요청(정보 글·정품 확인)을 하나도 못 보내는 일이 없게."""
        cap = self.settings.publish.max_awaiting_reviews
        if not cap:
            return None
        return cap if self.db.queue_counts().get("awaiting_approval", 0) >= cap else None

    async def _authenticity(self, item: QueueItem, deal: Deal) -> AuthResult:
        """정품·공식 판매처 판정. 한 번 판정한 결과는 대기열 항목에 남겨 다시 읽지 않는다."""
        p = deal.product
        cached = p.extra.get("auth")
        if isinstance(cached, dict) and cached.get("status"):
            return AuthResult(str(cached["status"]), str(cached.get("reason") or ""), cached.get("seller"))
        cfg = self.settings.deal.authenticity
        if not cfg.enabled or p.source == "manual":
            result = AuthResult("ok", "확인 안 함 (직접 올린 글)" if p.source == "manual" else "정품 확인 끔")
        else:
            post_text = ""
            post_url = p.extra.get("post_url")
            if post_url:
                body = await self.info.fetch(str(post_url))
                post_text = (body.raw or body.text) if body else ""
            page_text, seller = "", None
            if p.shop in cfg.open_markets:
                try:
                    meta = await self.enricher.fetch(p.url)
                except Exception as e:  # noqa: BLE001
                    log.info("authenticity page fetch failed for %s: %s", p.url, e)
                    meta = None
                if meta is not None:
                    page_text, seller = meta.text, meta.seller
                    PageEnricher.apply(p, meta)  # 후기 수 등 빈 칸을 채워 두면 후기 양 기준에도 쓰인다
            result = check_authenticity(cfg, p, post_text=post_text, page_text=page_text, seller=seller)
        p.extra["auth"] = result.as_dict()
        self.db.update_queue_item(item.id, status=item.status, error=item.last_error, deal=deal)
        return result

    async def _request_deal_review(self, item: QueueItem, deal: Deal, auth: AuthResult) -> bool:
        """오픈마켓 딜인데 공식 판매처가 확인되지 않음: 관리자가 /ok 로 확인해 주면 그때 링크를 만들어 올린다."""
        p = deal.product
        cap = self._review_backlog_full()
        if cap:
            self.db.update_queue_item(item.id, status="skipped", error=f"review backlog full ({cap})")
            self.db.log_event("INFO", "queue", f"{p.product_id} skipped: {cap} items already waiting for me")
            log.info("queue #%d skipped — %d items already waiting for admin", item.id, cap)
            return True
        self.db.update_queue_item(item.id, status="awaiting_approval", error=f"needs approval: 정품 확인 — {auth.reason}", deal=deal)
        shop = self.registry.get(p.shop)
        notice_id = await self.notifier.notify_deal_review(item, shop.name if shop else p.shop, auth.reason)
        if notice_id:
            self.db.kv_set(f"review_notice:{item.id}", str(notice_id))
        self.db.log_event("INFO", "authenticity", f"{p.product_id} 정품 확인 대기: {auth.reason} — {p.name[:50]}")
        log.info("queue #%d awaiting authenticity approval (%s)", item.id, auth.reason)
        await self.refresh_pending_notice()
        return True

    async def _request_info_review(self, item: QueueItem, deal: Deal, body: PostBody, reason: str) -> bool:
        """정보 글을 바로 올리지 않고 관리자 확인(/ok)을 받는다. 정리한 본문은 대기열 항목에 같이 저장해 둔다."""
        p = deal.product
        cap = self._review_backlog_full()
        if cap:
            self.db.update_queue_item(item.id, status="skipped", error=f"review backlog full ({cap})")
            self.db.log_event("INFO", "queue", f"{p.product_id} skipped: {cap} items already waiting for me")
            return True
        p.extra["info_draft"] = {"text": body.text, "images": body.images, "links": body.links}
        self.db.update_queue_item(item.id, status="awaiting_approval", error=f"needs approval: {reason}", deal=deal)
        preview = self.info.render(self.settings.info_posts.template, deal, body, autoescape=True)
        notice_id = await self.notifier.notify_info_review(item, preview, reason=reason, image_count=len(body.images))
        if notice_id:
            self.db.kv_set(f"review_notice:{item.id}", str(notice_id))
        self.db.log_event("INFO", "info_review", f"{p.product_id} 확인 대기: {reason}")
        log.info("info post #%d awaiting admin approval (%s)", item.id, reason)
        await self.refresh_pending_notice()
        return True

    async def sweep_unverified_reviews(self) -> int:
        """'확인 안 되면 제외' 설정이면, 이미 정품 확인을 기다리던 딜은 더 묻지 않고 내린다 (정보 글 확인은 그대로)."""
        if self.settings.deal.authenticity.unverified_action != "skip":
            return 0
        n = 0
        for it in self.db.awaiting_items(limit=200, statuses=("awaiting_approval",)):
            if it.deal.product.deal_kind == "info":
                continue
            self.db.update_queue_item(it.id, status="skipped", error="unverified seller (auto-skip)")
            n += 1
        if n:
            self.db.log_event("INFO", "authenticity", f"정품 확인을 기다리던 딜 {n}건을 설정(unverified_action=skip)에 따라 내림")
            await self.refresh_pending_notice()
        return n

    async def refresh_pending_notice(self) -> None:
        """관리자 챗 맨 위에 고정해 두는 '내 링크 기다리는 글' 목록을 최신으로 고친다."""
        if not self.notifier.enabled:
            return
        text = "📌 " + self.reporter.pending_text(notice=True)
        digest = hashlib.sha1(text.encode()).hexdigest()
        if self.notifier.tg_quiet:  # 주인 10/18: 고정 공지는 보내지 않는다 (DB 기록만 — 텔레그램에서는 /pending 이 그 자리)
            self.db.kv_set("pending_notice_hash", digest)
            return
        if self.db.kv_get("pending_notice_hash") == digest and self.db.kv_get("pending_notice_id"):
            return
        mid = self.db.kv_get("pending_notice_id")
        if mid and await self.notifier.edit(int(mid), text):
            self.db.kv_set("pending_notice_hash", digest)
            return
        new_id = await self.notifier.send_with_id(text, silent=True)
        if new_id:
            self.db.kv_set("pending_notice_id", str(new_id))
            self.db.kv_set("pending_notice_hash", digest)
            await self.notifier.pin(new_id)

    async def run_collector(self, collector: BaseCollector) -> dict[str, Any]:
        """수집기 1회 실행: 수집 → 가격 이력 저장 → 판정 → 대기열 등록."""
        name = collector.name
        status = self.state.collectors[name]
        cfg = self.settings
        run_id = self.db.start_run(name)
        status.running = True
        result: dict[str, Any] = {"collector": name, "collected": 0, "deals": 0, "queued": 0, "status": "ok"}
        try:
            collector.check_available()
            products = await collector.collect()
            now = utcnow()
            products = self._naver_order(products, now)  # 네이버 조회 한도 안에서 중요한 것부터 (비교 대상이 없으면 그대로)
            deals = queued = 0
            enriched = 0
            pages = self.page_reader.run_budget()  # 이번 판 크롬 한도 (가격 대조)
            naver_run: LookupRun | None = None  # 이번 판 네이버 최저가 조회 한도 (쿠팡 딜이 있을 때 만듦)
            skips: dict[str, set[str]] = {}  # 특가인데 줄 세우지 않은 이유 → 상품
            for p in products:
                if cfg.deal.sold_out.enabled and looks_sold_out(str(p.extra.get("title") or p.name), cfg.deal.sold_out.words or None):
                    await self._on_sold_out(p, via="게시판에 품절/종료 표시")
                    continue
                if not self._shop_allowed(p):
                    continue
                if not self._has_shop_url(p):
                    # 상품 링크가 없는 게시판 글: 가격 없는 이벤트·공지만 정보 글 후보. 가격이 있는데 링크를 못 찾은 글은 올릴 수 없다
                    if p.has_price:
                        log.info("skip %s — has a price but only a board URL (no %s link found): %s", p.name[:40], p.shop, p.url)
                    elif self._queue_info_post(p, now):
                        queued += 1
                    else:
                        log.info("skip %s — source url is not a %s page: %s", p.name[:40], p.shop, p.url)
                    continue
                check_price = self._wants_page_check(p)
                page: list[PageRead] = []  # 이 딜의 가격 대조에 쓴 페이지 (보류할 때 같은 상품의 열린 글도 이 페이지로)
                # 기억해 둔 페이지는 한도와 상관없이 (페이지를 읽지 않으니 이번 판 읽기 한도를 쓰지 않음).
                # 단 그 페이지로 보류가 되는데 새로 읽어 보류로 나온 글이 아니면 안 씀 (stale: 새로 읽어야 함)
                cached, stale = (
                    self.page_reader.kept(p.product_id, self._page_claim(p), self._page_holds(p)) if check_price else (None, False)
                )
                room = enriched < cfg.deal.enrich.max_per_run
                if (self._should_enrich(p) or check_price) and (room or cached is not None):
                    if cached is None:
                        enriched += 1
                    await self._enrich(p, check_price=check_price, pages=pages, seen=page)
                elif check_price:
                    # 이번 판 페이지 읽기 한도: 확인 못 함으로 진행하고 다음 판에 새로 읽음
                    self._check_page_price(p, None, why="이번 판 페이지 읽기 한도" + (f" ({NEW_PRICE})" if stale else ""))
                if not p.has_price and not cfg.deal.accept_coupons_and_events:
                    # 가격 없는 글(쿠폰·이벤트)은 특가 판정을 못 하니 정보 글로만
                    if self._queue_info_post(p, now):
                        queued += 1
                    continue
                stats = self.db.price_stats(p.product_id, cfg.deal.history_days, now)
                verdict = self.evaluator.evaluate(p, stats)
                if (p.extra.get("page_price") or {}).get("status") == "hold":
                    # 제목 가격이 상품 페이지와 안 맞음: 시중가 대조·가격 기록·링크 요청 없이 보류 (주인 결정 10/12, #22)
                    if verdict.is_deal:
                        skips.setdefault("held_price", set()).add(p.product_id)
                    await self._hold_for_price(p, verdict, now, page[-1] if page else None)
                    continue
                if p.has_price and self._should_record(p.product_id, now):
                    self.db.record_observation(p, now)
                if self._needs_market_check(p, verdict, now):
                    quote = await self._market_quote(p)
                    verdict = self.evaluator.evaluate(p, stats, quote, market_available=True)
                elif self._needs_naver_check(p, verdict, now):
                    # 쿠팡 딜: 네이버 최저가와 비교 (확실한 같은 상품일 때만 근거·탈락). 못 찾으면 가격 이력 판정 그대로
                    if naver_run is None:
                        assert self.naver_ref is not None
                        naver_run = self.naver_ref.new_run()
                    quote = await self._naver_quote(p, naver_run)
                    if quote is not None:
                        verdict = self.evaluator.evaluate(p, stats, quote)
                if not verdict.is_deal:
                    continue
                deals += 1
                why_not = self._not_queued_reason(p, now)
                if why_not is None:
                    deal = Deal(product=p, verdict=verdict, detected_at=now)
                    if self.db.enqueue(deal, score=verdict.score, now=now):
                        queued += 1
                        log.info("deal queued [%s/%s] %s %s원 (%s)", name, p.shop, p.name[:40], f"{p.price:,}", ", ".join(verdict.reasons))
                        continue
                    why_not = "in_queue"  # 같은 상품이 이미 대기·링크 요청 중 (그 글이 올라감)
                    # 링크를 기다리는 글이면 점수를 지금 값으로 (자리를 바꿀 때 새 딜과 같은 때의 점수로 견주게 — 추천 수는 계속 늘어남)
                    self.db.refresh_waiting_score(p.product_id, verdict.score)
                skips.setdefault(why_not, set()).add(p.product_id)
            result.update(collected=len(products), deals=deals, queued=queued)
            self.db.finish_run(run_id, status="ok", collected=len(products), deals=deals, queued=queued)
            self.notifier.error_cleared(f"collector:{name}")
            log.info("collector '%s' done: collected=%d deals=%d queued=%d", name, len(products), deals, queued)
            if skips:
                # 특가로 봤는데 안 줄 세운 이유를 한 줄로 (예전엔 '이미 올림'이 DEBUG 라 보이지 않았다)
                log.info("collector '%s' deals not queued: %s", name,
                         " ".join(f"{k}={len(v)}" for k, v in sorted(skips.items())))
                self._remember_skips(skips, now)
            if naver_run is not None and naver_run.summary():
                log.info("collector '%s' naver price check: %s", name, naver_run.summary())
        except CoupangRateLimited as e:
            # 시간당 호출 예산이 찬 것은 고장이 아니다: 조용히 건너뛰고 다음 시간에 다시
            result.update(status="skipped", error=str(e))
            self.db.finish_run(run_id, status="skipped", error=str(e))
            log.info("collector '%s' skipped: %s", name, e)
        except CollectorUnavailable as e:
            result.update(status="skipped", error=str(e))
            self.db.finish_run(run_id, status="skipped", error=str(e))
            log.info("collector '%s' skipped: %s", name, e)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            # 긴 트레이스백보다 한 줄 요약을 먼저 — 폰/웹 로그에서 원인을 바로 볼 수 있게
            log.error("collector '%s' FAILED: %s", name, msg)
            result.update(status="error", error=msg)
            self.db.finish_run(run_id, status="error", error=msg)
            self.db.log_event("ERROR", f"collector:{name}", msg + "\n" + traceback.format_exc()[-1500:])
            self.state.set_error(f"[{name}] {msg}")
            log.exception("collector '%s' failed", name)
            await self.notifier.notify_error(f"collector:{name}", msg)
        finally:
            status.running = False
        return result

    def _can_check_page(self, p: Product) -> bool:
        ec = self.settings.deal.enrich
        return ec.enabled and p.shop not in ec.exclude_shops and ("*" in ec.shops or p.shop in ec.shops)

    async def _page_sold_out(self, p: Product) -> bool:
        """상품 페이지의 재고 표시로 품절이 확인되면 True. 모르면 False."""
        if not self._can_check_page(p):
            return False
        try:
            return (await self.enricher.check_available(p.url)) is False
        except Exception as e:  # noqa: BLE001
            log.info("availability check failed for %s: %s", p.url, e)
            return False

    async def _on_sold_out(self, product: Product, via: str) -> dict[str, Any]:
        """품절 확인 → 기다리는 글 내리기 + 링크 요청 메시지 고치기 + 채널 글에 품절 표시 + 블로그가 안 쓰게 품절 기록."""
        so = self.settings.deal.sold_out
        pid = product.product_id
        out: dict[str, Any] = {"cancelled": [], "marked": False, "exported": False}
        for item in self.db.items_for_product(pid, ("pending", "awaiting_link", autolink.STATUS)):
            self.db.update_queue_item(item.id, status="expired", error=f"sold out ({via})")
            out["cancelled"].append(item.id)
            if item.status == autolink.STATUS:
                self._cancel_autolink(item.id)  # 주인에게 물은 적 없는 글 — 알림 없이 Blog-Auto 일만 거둔다
            if item.status == "awaiting_link":
                await self.refresh_pending_notice()
                notice = self.db.kv_get(f"link_notice:{item.id}")
                await self.notifier.notify_sold_out_cancel(item, via, int(notice) if notice else None)
            log.info("sold out (%s): queue #%d dropped — %s", via, item.id, product.name[:40])
        ttl = self._link_ttl()
        for item in self.db.items_for_product(pid, ("skipped",)):
            # 더 좋은 딜에 자리를 내준 글 (유효 시간 안): 품절이면 나중에 주인이 /link 해도 다시 올리지 않게 내려 둔다
            if item.link_replaced and utcnow() - item.created_at < ttl:
                self.db.update_queue_item(item.id, status="expired", error=f"sold out ({via})")
                out["cancelled"].append(item.id)
        post = self.db.latest_post(pid)
        out["exported"] = self._export_sold_out(pid, post)
        # 스레드: 기다리는 글은 내리고, 이미 올린 내 글 아래에 '업데이트) HH:MM 기준 품절됐어' 하나 (글마다 한 번, 주인 결정 10/15)
        try:
            out["threads_replies"] = await self.threads_flow.soldout_update(pid)
        except Exception as e:  # noqa: BLE001 — 스레드 답글 때문에 품절 처리가 멈추지 않게
            log.warning("threads sold-out update failed for %s: %s", pid, e)
        if (
            post
            and post.get("message_id")
            and post.get("channel_id")
            and not post.get("dry_run")
            and self.db.kv_get(f"soldout_marked:{pid}") is None
        ):
            posted_at = from_iso(post["posted_at"])
            if posted_at and utcnow() - posted_at <= timedelta(hours=so.mark_published_hours):
                published = self.db.items_for_product(pid, ("published",))
                if published:
                    text = self.publisher.render(published[0].deal)
                    if await self.publisher.mark_sold_out(post["channel_id"], int(post["message_id"]), text):
                        self.db.kv_set(f"soldout_marked:{pid}", to_iso(utcnow()))
                        out["marked"] = True
                        await self.notifier.send(f"⛔ 채널 글에 품절 표시를 붙였습니다: {product.name[:60]} ({via})", silent=True)
                        if self.router is not None:  # 관리자 챗이 조용하면 위 줄은 안 가니 정리의 딜봇 문장으로 (spec §3.7)
                            self.notifier.summary("soldout", truncate(product.name, 40))
        if out["cancelled"] or out["marked"] or out["exported"]:
            self.db.log_event(
                "INFO", "soldout", f"{pid} via={via} cancelled={out['cancelled']} marked={out['marked']} exported={out['exported']}"
            )
        return out

    # 스레드 대기열이 올리기 직전에 상품 페이지에서 품절을 본 상품 (같은 상품의 다른 기다리는 글도 그 뒤로는 안 올림). 시각만 —
    # 그 뒤에 다시 올라온 딜은 새 글이라 막지 않는다. 게시판에서 본 품절은 _on_sold_out 이 기다리는 글을 바로 내림
    SOLDOUT_SEEN_KV = "soldout_at:"

    def mark_sold_out_seen(self, product_id: str, now: datetime | None = None) -> None:
        self.db.kv_set(f"{self.SOLDOUT_SEEN_KV}{product_id}", to_iso(now or self.clock()))

    def known_sold_out(self, product_id: str) -> bool:
        """가장 최근 채널 글이 올라간 뒤로 품절로 기록된 상품: 채널 글에 품절 표시를 붙였거나, 블로그에 품절 줄을 남겼거나,
        스레드가 올리기 직전에 품절을 봤다 (그 전 품절 뒤 다시 올라온 딜은 새 글이라 막지 않는다). 인스타·일요일 TOP5 가 쓴다."""
        post = self.db.latest_post(product_id)
        posted = str(post["posted_at"]) if post else ""
        marked = self.db.kv_get(f"soldout_marked:{product_id}")
        if marked and (not posted or marked >= posted):
            return True
        if posted and self.db.kv_get(f"soldout_exported:{product_id}") == posted:
            return True
        seen = from_iso(self.db.kv_get(f"{self.SOLDOUT_SEEN_KV}{product_id}") or None)
        posted_at = from_iso(posted) if posted else None
        return seen is not None and (posted_at is None or seen >= posted_at)

    def _export_sold_out(self, pid: str, post: dict[str, Any] | None) -> bool:
        """채널에 실제로 올린 상품이 품절됨: published_deals.jsonl 에 품절 줄을 남겨 Blog-Auto 가 그 전에 올라온 같은 상품을
        블로그에 쓰지 않게 한다 (링크·가격 없는 줄 — Blog-Auto 가 이 모양을 읽음). 한 번 올린 글마다 한 줄만,
        연습 모드 글(미리보기)에는 남기지 않는다. 남겼으면 True."""
        if not post or post.get("dry_run"):
            return False
        key = f"soldout_exported:{pid}"
        posted_at = str(post.get("posted_at") or "")
        if self.db.kv_get(key) == posted_at:
            return False  # 이 발행 건은 이미 남김 (게시판이 같은 품절 글을 계속 돌려줌)
        try:
            row = {"product_id": pid, "sold_out": True, "posted_at": utcnow().isoformat(timespec="seconds")}
            with (self.settings.data_dir / "published_deals.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001 — 내보내기 실패가 품절 처리를 막지 않게
            log.warning("sold-out export failed: %s", e)
            return False
        self.db.kv_set(key, posted_at)
        log.info("sold out exported for the blog: %s", pid)
        return True

    async def recheck_awaiting(self) -> int:
        """내 링크를 기다리는 글의 상품 페이지를 다시 읽어 품절이면 내린다. 내린 개수를 돌려준다."""
        so = self.settings.deal.sold_out
        if not so.enabled:
            return 0
        dropped = 0
        for item in self.db.awaiting_items(50):
            p = item.deal.product
            if await self._page_sold_out(p):
                await self._on_sold_out(p, via="상품 페이지에 재고 없음")
                dropped += 1
        return dropped

    def _not_queued_reason(self, p: Product, now: Any) -> str | None:
        """특가로 판정된 딜을 줄 세우지 않을 이유 (SKIP_LABELS 의 키). 줄 세워도 되면 None."""
        if self._posted_recently(p.product_id, now):
            log.debug("deal %s already posted within %dd — skip", p.product_id, self.settings.publish.dedup_days)
            return "already_posted"
        if self._declined_recently(p, now):
            log.info("deal %s skipped — admin passed on it or did not answer recently", p.name[:40])
            return "declined"
        if self._replaced_recently(p, now):
            log.info("deal %s not asked again — replaced by a better deal, its link request is still open", p.name[:40])
            return "replaced"
        if self._held_recently(p, now):
            log.info("deal %s skipped — held recently for a page price mismatch at this title price", p.name[:40])
            return "held"
        dup = self._duplicate_of_recent(p, now)
        if dup:
            log.info("deal %s skipped as duplicate: %s", p.name[:40], dup)
            self.db.log_event("INFO", "dedupe", f"{p.product_id} {p.name[:50]} — {dup}")
            return "duplicate"
        return None

    def _remember_skips(self, skips: dict[str, set[str]], now: Any) -> None:
        """줄 세우지 않은 이유별 상품을 kv 에 하루 남짓 남긴다 (저녁 요약에서 상품 수로 셈 — 5분마다 같은 글을 다시 봐도 한 번).
        이미 대기 중인 글(in_queue)은 그 글이 올라가니 '안 올린 딜'이 아니라 남기지 않는다. 더 좋은 딜에 자리를 내준 상품(replaced)은
        내린 글이 '더 좋은 딜로 교체'로 이미 세어지니 남기지 않는다."""
        try:
            data = json.loads(self.db.kv_get(SKIP_KV) or "{}")
            if not isinstance(data, dict):
                data = {}
        except ValueError:
            data = {}
        cutoff = now - SKIP_KEEP
        out: dict[str, dict[str, str]] = {}
        for reason, seen in data.items():
            if isinstance(seen, dict):
                kept = {pid: at for pid, at in seen.items() if (t := from_iso(str(at))) is not None and t >= cutoff}
                if kept:
                    out[reason] = kept
        for reason, pids in skips.items():
            if reason in ("in_queue", "replaced"):
                continue
            bucket = out.setdefault(reason, {})
            for pid in pids:
                bucket[pid] = to_iso(now)
        try:
            self.db.kv_set(SKIP_KV, json.dumps(out, ensure_ascii=False))
        except Exception as e:  # noqa: BLE001 — 기록 실패가 수집을 막지 않게
            log.warning("could not remember skipped deals: %s", e)

    def skipped_deal_counts(self, since: Any, until: Any | None = None) -> dict[str, int]:
        """since 이후 특가로 판정했지만 안 올린 딜의 이유별 상품 수: 수집 때 줄 세우지 않은 것 + 줄 섰다가 발행 전에 빠진 것.
        같은 상품은 이유마다 한 번 (게시판이 같은 글을 5분마다 다시 돌려줘도). 많은 이유부터.
        - '이미 올린 상품'은 이 기간보다 앞서 올린 것만 (오늘 올린 딜을 다시 모아 와도 '안 올린 딜'로 세지 않음 — 리뷰 10/13)
        - 보류는 Blog-Auto 가 따로 알리니 뺀다"""
        until = until or utcnow()
        by_label: dict[str, set[str]] = {}
        try:
            data = json.loads(self.db.kv_get(SKIP_KV) or "{}")
        except ValueError:
            data = {}
        for reason, seen in (data.items() if isinstance(data, dict) else ()):
            if not isinstance(seen, dict) or reason in SKIP_NOT_SUMMARIZED:
                continue
            label = SKIP_LABELS.get(reason, reason)
            for pid, at in seen.items():
                t = from_iso(str(at))
                if t is not None and since <= t <= until:
                    by_label.setdefault(label, set()).add(pid)
        for status, error, pid in self.db.closed_queue_reasons(since, until):
            by_label.setdefault(_closed_label(status, error), set()).add(pid)
        posted = by_label.get(ALREADY_POSTED)
        if posted:
            for pid in list(posted):
                last = self.db.last_posted_at(pid, include_dry_run=self.state.dry_run)
                if last is not None and since <= last:
                    posted.discard(pid)  # 이 기간에 올린 상품: 그 글이 올라갔음
            if not posted:
                del by_label[ALREADY_POSTED]
        return dict(sorted(((k, len(v)) for k, v in by_label.items()), key=lambda kv: -kv[1]))

    def _replaced_recently(self, p: Product, now: Any) -> bool:
        """더 좋은 딜에 자리를 내준 같은 상품 글이 아직 링크 요청 유효 시간(manual_link_ttl_hours) 안: 다시 줄 세우지 않는다.
        내린 글의 링크 요청 메시지에 주인이 답하면 그대로 올라가니(attach_link) 또 묻지 않고, 추천 수가 늘어 점수가 오른 상품이
        5분마다 다시 줄 서서 다른 글을 밀어내고 또 밀려나며 링크 요청이 쏟아지는 일을 막는다 (리뷰 10/15). 그사이 더 싸졌으면 새 딜로 본다."""
        prev = self.db.replaced_since(p.product_id, now - timedelta(hours=self.settings.publish.manual_link_ttl_hours))
        if prev is None:
            return False
        q = prev.deal.product
        return not (p.has_price and q.has_price and p.price < q.price)

    def _declined_recently(self, p: Product, now: Any) -> bool:
        """관리자가 넘겼거나 답하지 않은 글은 REASK_COOLDOWN 동안 다시 묻지 않는다 (같은 질문 반복 방지).
        그사이 더 싸졌으면 새 딜로 본다."""
        prev = self.db.declined_since(p.product_id, now - REASK_COOLDOWN)
        if prev is None:
            return False
        q = prev.product
        return not (p.has_price and q.has_price and p.price < q.price)

    def _posted_recently(self, product_id: str, now: Any | None = None) -> bool:
        """최근 dedup_days 안에 올린 상품인가.

        연습 모드에서는 미리보기 기록까지 세어 같은 글을 반복해서 보여주지 않는다.
        실제 모드에서는 미리보기 기록을 무시한다 (연습 때 본 딜이 진짜 발행에서 빠지면 안 되므로).
        채널에 보냈는데 답이 없어(시간 초과) 올라갔는지 모르는 글도 올린 것으로 센다 (같은 딜 두 번 방지).
        """
        days = self.settings.publish.dedup_days
        return self.db.posted_within(product_id, days, now, include_dry_run=self.state.dry_run) or self.db.maybe_posted_within(
            product_id, days, now
        )

    def _duplicate_of_recent(self, p: Product, now: Any) -> str | None:
        """이름이 거의 같은 상품이 대기 중이거나 최근에 올라갔으면 그 설명, 아니면 None. 확실히 싸진 딜은 새 딜로 본다."""
        cfg = self.settings.publish
        if cfg.dedupe_similarity <= 0:
            return None
        waiting = [c for c in self.db.open_queue_names() if c["product_id"] != p.product_id]
        dup = find_duplicate(p.name, p.price, waiting, threshold=cfg.dedupe_similarity, allow_cheaper=False)
        if dup:
            return f"대기 중인 #{dup['queue_id']} 와 같은 상품 (유사도 {dup['similarity']})"
        recent = [c for c in self.db.recent_post_names(cfg.dedup_days, now, include_dry_run=self.state.dry_run) if c["product_id"] != p.product_id]
        dup = find_duplicate(p.name, p.price, recent, threshold=cfg.dedupe_similarity, cheaper_pct=cfg.dedupe_cheaper_pct)
        if dup:
            when = fmt_local(from_iso(dup.get("posted_at")), self.settings.app.timezone)
            return f"{when} 에 올린 '{truncate(str(dup['name']), 30)}' 와 같은 상품 (유사도 {dup['similarity']})"
        return None

    def _should_enrich(self, p: Product) -> bool:
        ec = self.settings.deal.enrich
        if not ec.enabled or p.shop in ec.exclude_shops or ("*" not in ec.shops and p.shop not in ec.shops):
            return False
        if self._posted_recently(p.product_id):
            return False
        return not p.image_url or is_board_thumb(p.image_url) or p.rating is None or not p.has_price

    async def _enrich(
        self,
        p: Product,
        *,
        check_price: bool = False,
        pages: RunBudget | None = None,
        seen: list[PageRead] | None = None,
    ) -> list[str]:
        """빈 칸(사진·별점 등)을 상품 페이지로 채운다. check_price 면 그 전에 제목 가격을 페이지와 대조 (seen 에 읽은 페이지를 남김).
        사진을 찾으러 다른 곳(쇼핑 검색 등)에 가지 않는다 — 네이버 쇼핑 검색 API 는 2026-07-31 종료 (주인 결정 10/15)."""
        if check_price:
            # 가격 대조: 기억해 둔 페이지 → 일반 요청 → 막힌 네이버 상품 페이지는 크롬 (이번 판·한 시간 한도 안에서)
            got = await self.page_reader.read(
                p.product_id,
                p.url,
                pages or self.page_reader.run_budget(),
                enricher=self.enricher,
                browser=self.store_browser,
                claim=self._page_claim(p),
                holds=self._page_holds(p),  # 기억해 둔 페이지로는 새로 읽어 보류로 나온 적 없는 글을 보류하지 않게
            )
            meta = got.meta
            self._check_page_price(p, meta, why=got.why, source=got.source)  # 제목 가격 대조는 빈 칸 채우기 전에 (제목 가격 그대로)
            if seen is not None:
                seen.append(got)
        else:
            meta = await self._remembered_fetch(p)
        filled = PageEnricher.apply(p, meta) if meta is not None else []
        if filled:
            log.info("enriched %s from page: %s", p.name[:40], ", ".join(filled))
        return filled

    async def _remembered_fetch(self, p: Product) -> PageMeta | None:
        """빈 칸 채우기용 페이지 읽기. 같은 상품은 remember_hours 동안 다시 읽지 않고 기억한 값 (못 읽은 것도 기억 — 매 판 다시 묻지 않게)."""
        keep = self.settings.deal.enrich.remember_hours * 3600
        key = p.product_id or p.url
        now = time.monotonic()
        hit = self._enrich_memo.get(key)
        if keep > 0 and hit is not None and hit[0] > now:
            return hit[1]
        meta = await self.enricher.fetch(p.url)
        if keep > 0:
            if len(self._enrich_memo) >= 500:
                self._enrich_memo = {k: v for k, v in self._enrich_memo.items() if v[0] > now}
                while len(self._enrich_memo) >= 500:
                    self._enrich_memo.pop(next(iter(self._enrich_memo)))
            self._enrich_memo[key] = (now + keep, meta)
        return meta

    def _wants_page_check(self, p: Product) -> bool:
        """제목 가격을 상품 페이지 가격과 대조할 딜인가 (토스·네이버, 가격 있는 상품 딜, 최근에 올린 상품 아님)."""
        pc = self.settings.deal.page_check
        if not pc.enabled or not p.has_price or p.deal_kind == "info" or p.source == "manual":
            return False
        if "*" not in pc.shops and p.shop not in pc.shops:
            return False
        return not self._posted_recently(p.product_id)

    def _page_tolerance(self, p: Product) -> dict[str, float]:
        """가격 대조 허용 폭. 글 제목·배송 칸에 카드·쿠폰 같은 말이 있으면 페이지에 안 보이는 할인 몫(한쪽)을 더 봐 준다."""
        pc = self.settings.deal.page_check
        said = f"{p.extra.get('title') or p.name} {p.shipping or ''}"
        return {"tolerance_pct": pc.tolerance_pct, "discount_tolerance_pct": pc.discount_tolerance_pct if DISCOUNT_WORDS.search(said) else 0}

    @staticmethod
    def _title_price(p: Product) -> int:
        """글 제목의 가격 (페이지 가격으로 바꿔 둔 뒤에도 원래 값)."""
        return int(p.extra.get("title_price") or p.price)

    def _page_claim(self, p: Product) -> tuple[int, str, float, float]:
        """가격 대조 판정을 정하는 글 쪽 값: 제목 가격·상품명(수량 말)·허용 폭(카드·쿠폰 말). 같은 페이지에 이 값이 같으면 판정도 같다
        (page_reader 가 기억해 둔 페이지로 되풀이하는 보류는 새로 읽어 보류로 나온 이 값뿐)."""
        tolerance = self._page_tolerance(p)
        return self._title_price(p), p.name, tolerance["tolerance_pct"], tolerance["discount_tolerance_pct"]

    def _page_holds(self, p: Product) -> Callable[[PageMeta], bool]:
        """그 페이지로 이 딜이 보류되는지 (page_reader 가 기억해 둔 옛 페이지로 새 글을 보류하지 않게). _page_claim 과 같은 값으로 대조."""
        title_price, name, tolerance_pct, discount_pct = self._page_claim(p)
        return lambda meta: check_page_price(
            name, title_price, meta.prices, page_title=meta.title, tolerance_pct=tolerance_pct, discount_tolerance_pct=discount_pct
        ).status == "hold"

    def _price_check(self, p: Product, meta: PageMeta) -> PageCheck:
        """이 글(제목 가격·상품명·카드 말)을 그 페이지 가격·옵션과 대조."""
        return check_page_price(p.name, self._title_price(p), meta.prices, page_title=meta.title, **self._page_tolerance(p))

    def _check_page_price(self, p: Product, meta: PageMeta | None, *, why: str | None = None, source: str = "") -> PageCheck:
        """상품 페이지 가격·옵션을 제목 가격과 대조해 p.extra['page_price'] 에 남긴다.
        맞으면(±tolerance_pct) 맞은 페이지 가격을 p.price 로 (시중가 대조·발행이 실제 값으로), 원래 제목 가격은 extra['title_price'].
        다른 옵션에 맞으면 extra['page_option'] 에 옵션 이름. 못 읽었으면 지금처럼 진행 (unverified — 절대 보류 안 함).
        '확인 못 함' 로그는 상품마다 하루 한 번 (unverified_log_hours), 기억해 둔 페이지(source=cache)로 맞은 건 debug 로만."""
        title_price = self._title_price(p)
        if meta is None:
            check = PageCheck("unverified", title_price, reason=why or "페이지를 못 읽음")
        else:
            check = self._price_check(p, meta)
            if check.status == "unverified" and why:
                check.reason = why  # 가격 없는 페이지 + 크롬도 못 읽은 이유
        p.extra["page_price"] = check.to_dict()
        if check.status == "unverified":
            if self.page_reader.should_log(p.product_id):
                log.info("page price unverified for %s (%s원): %s", p.name[:40], f"{title_price:,}", check.reason)
        elif check.status in ("ok", "option"):
            if check.matched_price and check.matched_price != p.price:
                p.extra["title_price"] = title_price
                p.price = check.matched_price
            if check.status == "option" and check.matched_label:
                p.extra["page_option"] = check.matched_label
            log.log(
                logging.DEBUG if source == "cache" else logging.INFO,
                "page price verified for %s: 글 %s원 ↔ 페이지 %s원%s%s",
                p.name[:40], f"{title_price:,}", f"{check.matched_price or 0:,}",
                f" (옵션 '{check.matched_label}')" if check.status == "option" else "",
                " · 크롬으로 읽음" if source == "browser" else "",
            )
        return check

    def _hold_note_log(self, queue_id: int, p: Product, check: PageCheck) -> None:
        detail = hold_line(queue_id, check)
        log.info("queue #%d held — %s · %s", queue_id, detail, p.name[:40])
        self.db.log_event("INFO", "page_price", f"{p.product_id} {detail}")

    async def _hold_for_price(self, p: Product, verdict: DealVerdict, now: Any, page: PageRead | None = None) -> int | None:
        """수집 때 가격이 안 맞은 딜을 보류로 남기고 관리자 챗에 조용히 한 줄. 관심 없는 글·최근에 올렸거나 넘긴 글·
        같은 제목 가격으로 이미 보류한 글은 조용히 넘어간다. 보류 번호(없으면 None).
        앞서 줄 세운 그 글(대기·링크 요청 중)이 있으면 새 보류 글 대신 그 글을 보류한다 — 예전 판정대로 올라가지 않게 (_open_hold).
        page: 이 딜을 대조한 페이지 (읽은 곳 — 기억해 둔 페이지인지 — 도)."""
        first: int | None = None
        owner_ok = False  # 주인이 휴대폰으로 보고 링크를 붙인 같은 글(네이버)이 있음 → 그 확인을 믿고 새 보류도 만들지 않음
        for item in self.db.items_for_product(p.product_id, ("pending", "awaiting_link", autolink.STATUS)):
            if self._owner_checked(item.deal.product) and self._title_price(item.deal.product) == self._title_price(p):
                owner_ok = True
                continue
            if await self._open_hold(item, p, page) and first is None:
                first = item.id
        if first is not None:
            return first
        if owner_ok:
            log.info("deal %s not held — the owner checked the price on her phone and linked it", p.product_id)
            return None
        if any(r == "low_interest" or r.startswith(("excluded", "few_reviews")) for r in verdict.reasons):
            return None  # 가격이 맞았어도 안 올렸을 글 (관심 없음·제외 낱말·후기 적음) — 메모 안 보냄
        if self._posted_recently(p.product_id, now) or self._declined_recently(p, now):
            return None
        if self.db.held_since(p.product_id, now - REASK_COOLDOWN, price=p.price) is not None:
            log.debug("deal %s already held at the same title price — no new note", p.product_id)
            return None
        check = PageCheck(**p.extra["page_price"])
        deal = Deal(product=p, verdict=verdict, detected_at=now)
        queue_id = self.db.add_held(deal, score=verdict.score, error=f"held: page price ({check.reason})", now=now)
        self._hold_note_log(queue_id, p, check)
        await self.notifier.notify_price_hold(queue_id, p, check)
        return queue_id

    async def _open_hold(self, item: QueueItem, p: Product, page: PageRead | None) -> bool:
        """보류가 나온 딜(p)과 같은 상품의 열린 글(아직 안 올림)을 보류할지 보고, 보류하면 True. 그 글 자체는 그대로 두고 page_price 만
        고친다 (맞았을 때 페이지 가격으로 바꿔 둔 글 값은 제목 가격으로 되돌림).
        - 제목 가격이 다른 글(같은 상품의 다른 글)은 건드리지 않는다. 그 글을 다시 수집할 때 그 글 값으로 새로 읽어 따로 본다.
        - 방금 새로 읽은 페이지(cache 아님): 그 글의 이름·카드 말로 대조해 안 맞으면 보류 (같은 글이면 판정도 같다). 전에 맞았던
          글(ok·option)도 — 지금 페이지가 가장 새 값이다 (그사이 가게가 값을 올렸으면 옛 확인대로 올라가지 않게).
        - 기억해 둔 페이지(cache): 그 페이지를 새로 읽어 보류로 나온 그 글(_page_claim 이 같은 글)의 보류 되풀이만, 그 글이 맞은 적
          없을 때만. 새로 읽어 나온 적 없는 보류는 만들지 않고(page_reader 와 같은 규칙), 맞은 글의 확인은 이 페이지보다 새 것일 수
          있다 (발행 전 크롬 대조 등) — 5분마다 되풀이되는 보류가 맞은 글을 뒤집거나 다른 글을 매번 보류·메모하지 않게."""
        q = item.deal.product
        if self._title_price(q) != self._title_price(p) or self._owner_checked(q):
            return False  # 주인이 휴대폰으로 확인하고 링크를 붙인 네이버 글은 페이지 대조로 보류하지 않음 (주인 결정 10/15)
        if page is not None and page.meta is not None and page.source != "cache":
            check = self._price_check(q, page.meta)  # 새로 읽은 페이지로 그 글 그대로 대조
        elif self._page_claim(q) == self._page_claim(p) and (q.extra.get("page_price") or {}).get("status") not in ("ok", "option"):
            check = PageCheck(**p.extra["page_price"])  # 같은 글: 판정도 같다 (새로 읽어 보류로 나온 판정의 되풀이)
        else:
            return False
        if check.status != "hold":
            return False
        # 맞았을 때 바꿔 둔 페이지 가격·옵션은 거둔다: 보류 글 값은 제목 가격 (held_since 가 같은 제목 가격의 보류로 알아보게)
        q.price = check.title_price
        q.extra.pop("title_price", None)
        q.extra.pop("page_option", None)
        q.extra["page_price"] = check.to_dict()
        await self._hold_item(item, item.deal, when="re-collected")
        return True

    async def _hold_item(self, item: QueueItem, deal: Deal, *, when: str = "before publish") -> None:
        """발행 직전에 읽은 페이지(사진 때문에 이미 연 페이지)로 가격이 안 맞는 게 드러난 글: 올리지 않고 보류.
        when: 'before link' 면 쇼핑커넥트에 묻기 전에 드러난 것 (링크 요청·자동 링크 없음),
        're-collected' 면 줄 서 있던 글을 다시 수집해 읽은 페이지로 드러난 것 (링크 요청 중이면 그 메시지를 보류 안내로 고침)."""
        p = deal.product
        check = PageCheck(**p.extra["page_price"])
        self.db.update_queue_item(item.id, status="held", error=f"held: page price ({check.reason}, {when})", deal=deal)
        self._hold_note_log(item.id, p, check)
        notice: int | None = None
        if item.status == autolink.STATUS:
            self._cancel_autolink(item.id)  # 쇼핑커넥트 자동 링크 일도 거둔다 (주인에게 물은 적 없는 글)
        elif item.status == "awaiting_link":
            self.notifier.resolve(f"link:{item.id}")  # 링크를 만들 필요가 없어졌으니 아직 안 보낸 카톡은 취소
            raw = self.db.kv_get(f"link_notice:{item.id}")
            notice = int(raw) if raw else None
            await self.refresh_pending_notice()
        await self.notifier.notify_price_hold(item.id, p, check, notice_message_id=notice)

    def _held_recently(self, p: Product, now: Any) -> bool:
        """같은 제목 가격으로 최근에 가격 대조 보류한 상품을, 이번엔 페이지를 못 읽었다고(429 등) 다시 줄 세우지 않는다.
        이번에 페이지 가격이 맞았거나(ok·option) 제목 가격이 바뀌었으면 새 딜로 본다."""
        if (p.extra.get("page_price") or {}).get("status") in ("ok", "option"):
            return False
        return p.has_price and self.db.held_since(p.product_id, now - REASK_COOLDOWN, price=p.price) is not None

    @staticmethod
    def _owner_checked(p: Product) -> bool:
        """주인이 휴대폰으로 가격·옵션을 보고 링크를 붙인 네이버 딜 (주인 결정 10/15): 그 확인을 가격 확인으로 친다 —
        상품 페이지 대조로 보류하지 않는다 (다시 수집·발행 직전 모두). 예전에 만든 링크를 다시 쓴 딜(mylink)은 아님."""
        return p.shop == "naver" and bool(p.extra.get("owner_link"))

    def _recheck_page_price(self, p: Product, meta: PageMeta | None) -> None:
        """수집 때 페이지 가격을 확인 못 한 딜: 발행 직전에 읽은 페이지(다른 몰은 사진 때문에 읽은 페이지, 네이버 딜은
        _read_unverified_price 가 가격만 보려고 읽은 페이지)가 있으면 그걸로 대조.
        이미 정한 판정(시중가 대조)은 그대로 두고, 가격이 안 맞으면 보류만 한다.
        주인이 휴대폰으로 확인하고 링크를 붙인 네이버 딜은 대조하지 않는다 (그 확인이 가격·옵션 확인, 주인 결정 10/15)."""
        if self._owner_checked(p):
            return
        state = p.extra.get("page_price")
        if meta is None or meta.prices is None or not isinstance(state, dict) or state.get("status") != "unverified":
            return
        check = check_page_price(
            p.name, int(state.get("title_price") or p.price), meta.prices, page_title=meta.title, **self._page_tolerance(p)
        )
        p.extra["page_price"] = check.to_dict()
        if check.status == "option" and check.matched_label:
            p.extra["page_option"] = check.matched_label
        log.info("page price checked before publish for %s: %s", p.name[:40], check.status)

    def _should_record(self, product_id: str, now: Any) -> bool:
        gap = self.settings.deal.observation_min_gap_hours
        if gap <= 0:
            return True
        last = self.db.last_observed_at(product_id)
        return last is None or (now - last) >= timedelta(hours=gap)

    def _needs_market_check(self, p: Product, verdict: DealVerdict, now: Any | None = None) -> bool:
        """시중가 대조는 쿠팡 검색 예산을 쓰므로 '후보'에만: 다른 몰 + 가격 있음 + (판정 통과 또는 표시 할인율 후보).
        게시판이 5분마다 같은 글을 다시 돌려주므로, 대조 결과로 바뀔 게 없는 글은 예산을 쓰지 않는다 (_reference_can_matter)."""
        if self.market is None or p.shop == "coupang" or not p.has_price:
            return False
        return self._reference_can_matter(p, verdict, now)  # 관심도 게이트를 통과한 다른 몰의 딜은 (d) 대조가 기본

    def _needs_naver_check(self, p: Product, verdict: DealVerdict, now: Any | None = None) -> bool:
        """쿠팡 딜(설정한 수집기: 골드박스·카테고리 베스트)의 네이버 최저가 비교. 키가 없거나 401 로 꺼졌으면 안 함 (로그 한 번).
        시중가 대조처럼 결과로 바뀔 게 없는 상품에는 호출을 쓰지 않는다."""
        if not self._naver_source(p):
            return False
        assert self.naver_ref is not None
        return self.naver_ref.usable and self._reference_can_matter(p, verdict, now)

    def _reference_can_matter(self, p: Product, verdict: DealVerdict, now: Any | None = None) -> bool:
        """가격 비교 결과로 판정·발행이 바뀔 수 있는 딜인가. 아니면 호출을 아낀다:
        대조 전에 이미 떨어진 글(관심 없음·제외 낱말·최저가 미만·가격 없음·후기 적음), 최근에 올렸거나 이미 대기열에 있거나
        최근에 넘겼거나 같은 값으로 보류한 글."""
        if self._posted_recently(p.product_id):
            return False
        if "low_interest" in verdict.reasons or any(r.startswith("excluded") for r in verdict.reasons):
            return False
        if any(r in ("below_min_price", "no_price") or r.startswith("few_reviews") for r in verdict.reasons):
            return False  # 시중가 대조보다 먼저 정해지는 탈락 — 대조해도 판정이 같다
        if self.db.items_for_product(p.product_id, OPEN_STATUSES):
            return False  # 이미 대기·링크 요청 중 (다시 줄 세우지 않음)
        now = now or utcnow()
        if self._declined_recently(p, now) or self._held_recently(p, now):
            return False  # 어차피 아래에서 건너뜀
        return True

    async def _market_quote(self, p: Product) -> MarketQuote | None:
        """쿠팡 시중가. 맞은 값과 '검색했는데 맞는 상품 없음'을 cache_hours 동안 기억한다 (없음은 가격 0 으로).
        예산·한도·오류로 검색을 못 한 것은 기억하지 않는다 (다음 판에 다시).
        대조한 쿠팡 상품의 분류도 같이 기억한다: 기억한 값으로 판정할 때도 식품 기준이 처음과 같게 (리뷰 10/13)."""
        assert self.market is not None
        cached = self.db.get_market_quote(p.product_id, self.settings.deal.market.cache_hours)
        if cached:
            if int(cached["price"]) <= 0:
                return None
            return MarketQuote(price=int(cached["price"]), source=cached["source"], title=cached.get("title") or "",
                               url=cached.get("url"), category=_meta(cached).get("category"))
        quote = await self.market.lookup(p)
        if quote is not None:
            self.db.set_market_quote(p.product_id, price=quote.price, source=quote.source, title=quote.title, url=quote.url,
                                     meta=json.dumps({"category": quote.category}, ensure_ascii=False))
        elif getattr(self.market, "last_no_match", False):
            self.db.set_market_quote(p.product_id, price=0, source=MARKET_NO_MATCH, title=None, url=None)
        return quote

    async def _naver_quote(self, p: Product, run: LookupRun) -> MarketQuote | None:
        """쿠팡 상품의 네이버 최저가 근거 (확실한 같은 상품일 때만). market_quotes 에 'navershop:상품키' 로 기억한다:
        못 찾음은 cache_hours(24시간) 동안 다시 찾지 않고, 찾은 값은 price_cache_hours(6시간)까지만 근거로 쓴다 (글에 금액을 쓰니까).
        기억하는 것은 네이버 쪽 사실(같은 상품 최저가·탈락 기준·최저가 확인 여부·분류)뿐이고, 판정은 판마다 지금 쿠팡 값으로
        다시 한다 (naver_quote — 값이 바뀌어도 절반 값·70% 넘는 차이 같은 거름이 그대로 걸림).
        한도·일시 오류로 못 찾아본 것은 기억하지 않는다 (다음 판에 다시). 그때 cache_hours 안의 지난 값이 '네이버가 더 쌈'이면
        그 탈락은 지킨다 — 다시 못 찾았다고 이미 아는 '네이버보다 비싼 상품'을 특가로 올리지 않게 (리뷰 10/13).
        지난 값으로 'N% 싸요' 근거는 만들지 않는다."""
        assert self.naver_ref is not None
        nc = self.settings.deal.naver_reference
        key = NAVER_QUOTE_KEY + p.product_id
        cached = self.db.get_market_quote(key, nc.cache_hours)  # type: ignore[arg-type]
        known: NaverPrices | None = None
        if cached:
            if int(cached["price"]) <= 0:
                return None  # 확실한 같은 상품이 없었음
            known = NaverPrices.from_row(cached)
            checked = from_iso(cached.get("checked_at"))
            if known is not None and checked is not None and utcnow() - checked <= timedelta(hours=nc.price_cache_hours):
                return naver_quote(known, p.price, nc)[0]
        ref = await self.naver_ref.lookup(p, run)
        if ref is not None:
            if self.naver_ref.last_complete:
                self.db.set_market_quote(key, price=ref.lowest, source=NAVER_SOURCE, title=ref.title, url=ref.url, meta=ref.to_meta())
            quote, why = naver_quote(ref, p.price, nc)
            log.info("naver ref verdict for '%s' %s원: %s", p.name[:40], f"{p.price:,}",
                     f"네이버 {quote.price:,}원 ({why})" if quote else why)
            return quote
        if self.naver_ref.last_no_match:
            self.db.set_market_quote(key, price=0, source=MARKET_NO_MATCH, title=None, url=None)
            return None
        if known is not None:
            quote, _why = naver_quote(known, p.price, nc)
            if quote is not None and quote.price <= p.price:
                run.kept_veto += 1
                log.info("naver ref: 다시 못 찾아 지난 값으로 탈락 유지 — '%s' %s원 ≥ 네이버 %s원", p.name[:40],
                         f"{p.price:,}", f"{quote.price:,}")
                return quote
        return None

    def _naver_source(self, p: Product) -> bool:
        """네이버 최저가와 비교하는 쿠팡 상품인가 (설정한 수집기의 가격 있는 쿠팡 상품)."""
        if self.naver_ref is None or p.shop != "coupang" or not p.has_price:
            return False
        sources = self.settings.deal.naver_reference.sources
        return not sources or p.source in sources

    def _naver_order(self, products: list[Product], now: Any) -> list[Product]:
        """네이버 조회 차례 (한 판 한도 안에서 중요한 것부터, 리뷰 10/13). 비교 대상이 아닌 상품은 앞에 원래 순서대로.
        1) 가격 이력·할인율로 이미 특가인 상품 — 네이버가 더 싸면 탈락시켜야 하니 확인 없이 올라가지 않게 먼저
        2) 아직 찾아본 적 없는 상품 3) 지난 값이 있는 상품 (기억한 값이 아직 쓸 만하면 호출을 안 씀)
        같은 차례 안에서는 카테고리를 순위대로 번갈아 — 카테고리 베스트 한 판(3개 x 30)에서 첫 카테고리가 한도를 다 쓰지 않게."""
        nr = self.naver_ref
        if nr is None or not any(self._naver_source(p) for p in products) or not nr.usable:
            return products
        rest = [p for p in products if not self._naver_source(p)]
        groups: dict[Any, list[Product]] = {}
        for p in products:
            if self._naver_source(p):
                groups.setdefault(p.extra.get("category_id"), []).append(p)
        mixed = [g[i] for i in range(max(len(g) for g in groups.values())) for g in groups.values() if i < len(g)]
        cfg = self.settings.deal

        def turn(p: Product) -> int:
            try:
                if self.evaluator.evaluate(p, self.db.price_stats(p.product_id, cfg.history_days, now)).is_deal:
                    return 0
                return 1 if self.db.get_market_quote(NAVER_QUOTE_KEY + p.product_id, cfg.naver_reference.cache_hours) is None else 2  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — 차례를 못 정해도 수집은 계속
                return 1

        return rest + sorted(mixed, key=turn)

    async def _ensure_link(self, item: QueueItem) -> tuple[str, str | None]:
        """returns (state, error): state ∈ ok | manual | skip | fail"""
        deal = item.deal
        if deal.affiliate_url:
            # 관리자가 /link 로 붙였거나 직접 올린 딜: 그대로 사용
            return "ok", None
        shop = self.registry.get(deal.product.shop)
        # 링크프라이스 몰: 링크 생성이 실패하면(대개 승인 전 머천트) 한동안 그 몰을 건너뛴다
        cooldown_key = f"provider_cooldown:{shop.provider}:{shop.key}" if shop and shop.provider == "linkprice" and shop.link_mode == "api" else None
        if cooldown_key:
            until = from_iso(self.db.kv_get(cooldown_key))
            if until and until > utcnow():
                return "skip", f"{shop.name}: 링크 생성 실패로 {fmt_local(until, self.settings.app.timezone)} 까지 건너뜀"
        try:
            deal.affiliate_url = await self.links.to_affiliate(deal.product)
            return "ok", None
        except ManualLinkRequired as e:
            saved = self.db.kv_get(f"mylink:{deal.product.product_id}")
            if saved and not is_telegram_url(saved):  # 전에 관리자가 만들어 준 같은 상품의 내 링크 (예전에 잘못 남은 채널 글 주소는 안 씀)
                deal.affiliate_url = saved
                self._reuse_owner_photo(deal.product)  # 그때 링크와 같이 보낸 상품 사진도 (남아 있으면)
                return "ok", None
            if self.settings.publish.dry_run:
                # 연습 모드(DEALBOT_DRY_RUN)에서는 링크 요청으로 귀찮게 하지 않고 원본 링크로 기록만
                deal.affiliate_url = deal.product.url
                log.info("[DRY-RUN] manual link would be requested for %s — using raw url", e.shop.key)
                return "ok", None
            return "manual", e.shop.key
        except ShopSkipped as e:
            return "skip", str(e)
        except LinkConversionError as e:
            if self.state.dry_run:
                deal.affiliate_url = deal.product.url
                log.warning("[DRY-RUN] link conversion unavailable (%s) — using raw url", e)
                return "ok", None
            hours = self.settings.links.provider_error_cooldown_hours
            if cooldown_key and shop is not None and hours > 0 and self.links.provider_for(shop):
                until = utcnow() + timedelta(hours=hours)
                self.db.kv_set(cooldown_key, to_iso(until))
                self.db.log_event("WARNING", "links", f"{shop.key}: {e} → {hours:g}h cooldown")
                log.warning("linkprice link failed for %s (%s) — skipping this shop for %gh", shop.key, e, hours)
                await self.notifier.alert(
                    "linkprice",
                    f"⚠️ <b>{html.escape(shop.name)}</b> 링크를 만들지 못했습니다. {hours:g}시간 동안 이 몰의 딜은 건너뜁니다.\n"
                    "링크프라이스에서 이 머천트가 승인됐는지 확인해 주세요. 승인되면 다음 딜부터 자동으로 다시 시도합니다.\n"
                    f"<code>{html.escape(truncate(str(e), 200))}</code>",
                    kakao_fit(f"⚠️[할 일] 링크프라이스 {shop.name} 링크를 못 만들어요", "→ 머천트 승인 확인 (그동안 이 몰 딜은 건너뜀)"),
                    key=f"linkprice:{shop.key}", short=f"링크프라이스 {shop.name} 승인", ttl_h=168,
                )
                return "skip", f"linkprice link failed: {e}"
            return "fail", str(e)
        except CoupangRateLimited as e:
            return "wait", str(e)  # 예산이 풀리면 그대로 다시 시도 (실패로 세지 않음)
        except Exception as e:  # noqa: BLE001
            return "fail", f"{type(e).__name__}: {e}"

    async def _request_manual_link(self, item: QueueItem, deal: Deal, shop: Any, auth: AuthResult, why: str | None) -> bool:
        """주인에게 내 링크를 요청한다 (예전 흐름 그대로). 요청이 이미 많이 쌓였으면 점수가 가장 낮은 글과 바꾸거나 건너뛴다."""
        cap = self.settings.publish.max_awaiting_links
        pid = deal.product.product_id
        now_item = self.db.get_queue_item(item.id)
        if now_item is None or now_item.status not in ("pending", autolink.STATUS) or now_item.deal.affiliate_url:
            # 처리하는 사이(정품·품절 확인을 기다리는 동안) 주인이 이 글에 링크를 붙였거나 넘김: 그 답을 덮어쓰지 않고 또 묻지도 않는다
            # (자리를 만들려고 다른 글을 내리지도 않음). 아래 조건부 UPDATE 는 자리를 만드는 동안의 짧은 틈까지 막는다
            log.info("queue #%d: owner answered while it was being checked — no link request", item.id)
            return True
        if cap and self.db.queue_counts().get("awaiting_link", 0) >= cap and not await self._make_link_room(item, cap):
            # 링크 요청이 이미 여러 건 쌓여 있으면 더 보내지 않는다 (관리자 챗 폭주 방지). 딜은 어차피 곧 지난다
            if self.db.move_unlinked(item.id, status="skipped", error=f"manual link backlog full ({cap})"):
                self.db.log_event("INFO", "queue", f"{pid} skipped: {cap} manual link requests already waiting")
                log.info("queue #%d skipped — %d manual link requests already waiting", item.id, cap)
            return True
        if not self.db.move_unlinked(item.id, status="awaiting_link", error="manual link required", deal=deal):
            log.info("queue #%d: owner answered while it was being checked — no link request", item.id)
            return True
        if shop is not None:
            auth_note = None if auth.status == "ok" else f"⚠️ 정품 확인: {auth.reason}. 링크를 만들기 전에 판매자가 공식 판매처인지 확인해 주세요."
            notice_id = await self.notifier.notify_manual_link(item, shop, auth_note=auth_note)
            if notice_id:
                self.db.kv_set(f"link_notice:{item.id}", str(notice_id))
        log.info("queue #%d awaiting manual link (%s)", item.id, why)
        await self.refresh_pending_notice()
        return True

    async def _make_link_room(self, item: QueueItem, cap: int) -> bool:
        """링크 요청 자리가 다 참 (주인 결정 10/15): 기다리는 글 중 점수가 가장 낮은 글(같으면 가장 오래된 글)보다 이 딜의 점수가
        swap_min_score_gain 이상 높으면 그 글을 내리고 자리를 만든다. 점수는 대기열 점수(판정의 DealVerdict.score — 발행 순서도 이것,
        기다리는 글은 게시판에서 다시 볼 때마다 새 점수로 — refresh_waiting_score).
        주인이 손댄 글(링크를 붙임·넘김·상태가 바뀜)은 내리지 않는다: 조건부 UPDATE 로 링크를 기다리는 그대로일 때만 바꾸고,
        그사이 바뀌었으면 다음 후보를 본다. 내린 글은 넘긴 것이 아니다: 유효 시간 안에는 같은 상품을 다시 묻지 않고(_replaced_recently)
        주인이 /link 하면 그대로 받으며(attach_link), 지나면 같은 상품이 다시 나올 때 다시 물을 수 있다. 자리가 생겼으면 True.
        주인 10/23: 토스 같은 우선 몰(publish.link_priority_shops)은 자리의 link_priority_share 까지는 점수와 상관없이 다른 몰 글을 밀어내고, 그 몫은 다른 몰 새 딜이 못 밀어낸다."""
        pub = self.settings.publish
        gain = pub.swap_min_score_gain
        prio = tuple(k.lower() for k in pub.link_priority_shops)
        reserve = math.ceil(pub.link_priority_share * cap - 1e-9) if prio else 0  # 우선 몰이 지켜 받는 자리 수 (주인 10/23: 토스)
        mine = bool(prio) and reserve > 0 and item.deal.product.shop.lower() in prio
        if gain is None and not mine:
            return False
        for _ in range(3):  # 고르는 사이 주인이 손댄 글이면 다음으로 낮은 글을 다시 고른다
            if self.db.queue_counts().get("awaiting_link", 0) < cap:
                return True  # 그사이 자리가 남 (주인이 링크를 붙였거나 품절로 내림)
            n_prio = self.db.awaiting_shop_count(prio) if reserve else 0
            by_priority = False
            if mine and n_prio < reserve:
                # 우선 몰(토스)의 새 딜: 몫이 안 찼으면 점수와 상관없이 다른 몰의 가장 낮은 글을 내린다 (다른 몰 글이 없으면 아래 보통 규칙)
                low = self.db.weakest_awaiting_link(not_shops=prio)
                by_priority = low is not None
            else:
                # 다른 몰의 새 딜은 우선 몰의 몫(reserve)까지는 밀어내지 못한다
                low = self.db.weakest_awaiting_link(not_shops=prio if reserve and not mine and n_prio <= reserve else ())
            if low is None:
                return False
            if not by_priority:
                if gain is None or low.id == item.id or item.score <= low.score or round(item.score - low.score, 6) < gain:
                    return False
            elif low.id == item.id:
                return False
            # 소수 점수의 뺄셈 오차로 정확히 기준만큼 높은 딜(5.2 → 10.2)이 빠지지 않게 반올림해서 견준다
            if not self.db.replace_awaiting_link(low.id, f"{REPLACED} #{item.id} (score {low.score:g}→{item.score:g})"):
                continue
            self.db.log_event("INFO", "queue", f"#{low.id} {low.product_id} replaced by #{item.id} {item.product_id} "
                                               f"(score {low.score:g}→{item.score:g}, {cap} link requests waiting)")
            log.info("queue #%d replaced by #%d — link backlog full (%d), score %g → %g", low.id, item.id, cap, low.score, item.score)
            notice = self.db.kv_get(f"link_notice:{low.id}")
            until = low.created_at + timedelta(hours=self.settings.publish.manual_link_ttl_hours)
            try:  # 링크 요청 메시지를 '더 좋은 딜로 바꿈'으로 고친다 (못 고쳐도 그대로 진행)
                await self.notifier.notify_link_replaced(low, item.id, int(notice) if notice else None,
                                                         until=fmt_local(until, self.settings.app.timezone, "%m/%d %H:%M"))
            except Exception as e:  # noqa: BLE001
                log.warning("link request #%d: replaced notice failed: %s", low.id, e)
            return True
        return False

    # ------------------------------------------------- 쇼핑 커넥트 없는 판매처 (주인 10/18)
    def _sc_store_known(self, deal: Deal, shop: Any, auth: AuthResult) -> bool:
        """주인이 '쇼핑 커넥트에 없음'으로 표시한 판매처의 네이버 딜이라 링크를 묻지 않고 원본 주소로 올릴 딜인가.
        설정(autolink.naver.no_link_autopost)을 끄면 늘 False. 판매처 확인이 안 된 딜은 자동 링크와 같은 기준(unverified):
        manual 이면 예전처럼 주인에게 링크 요청 (주인이 판매처를 보고 정함 — '쇼핑 커넥트에 없음'을 또 누르면 됨)."""
        cfg = self.settings.autolink.naver
        if not cfg.no_link_autopost or shop is None or shop.key != "naver" or deal.product.deal_kind == "info":
            return False
        if auth.status != "ok" and cfg.unverified != "auto":
            return False
        return sc_stores.known(self.db, deal.product) is not None

    def no_connect(self, queue_id: int, *, post: bool) -> tuple[str, str]:
        """대시보드 '쇼핑 커넥트에 없음' (주인 10/18). post: '링크 없이 올리기' — 원본 주소로, 제휴 고지 없이
        (가격 확인·사진 규칙·품절 확인은 여느 딜처럼 발행 때). 아니면 '안 올리기' (건너뛰기와 같음).
        어느 쪽이든 그 판매처를 '쇼핑 커넥트에 없음'으로 적는다 → 다음 딜부터 묻지 않음. (코드, 화면 글)."""
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return "not_found", ""
        p = item.deal.product
        if item.status != "awaiting_link" or p.shop != "naver" or p.deal_kind == "info" or item.deal.affiliate_url:
            return "wrong_state", "지금은 링크를 기다리는 네이버 딜이 아니에요"
        label = sc_stores.remember(self.db, p, via="post" if post else "skip", qid=queue_id)
        self.db.log_event("INFO", "manual_link", f"#{queue_id} 주인: 쇼핑 커넥트에 없음 → {'링크 없이 올림' if post else '안 올림'}"
                          + (f" (판매처 {label} 기억)" if label else " (판매처 모름)"))
        if post:
            self.notifier.resolve(f"link:{queue_id}")  # 아직 안 보낸 링크 요청 카톡은 취소
            self._post_raw_link(item, item.deal, "주인: 쇼핑 커넥트에 없음", reason="owner_no_sc")
            code, text = "nolink", "링크 없이 올릴 차례에 넣었어요 (제휴 고지 없이)"
        else:
            self.skip_item(queue_id)
            code, text = "skipped", "안 올리기로 했어요"
        if label and self.settings.autolink.naver.no_link_autopost:
            text += " · 이 판매처는 다음부터 묻지 않고 링크 없이 올려요"
        return code, text

    def sc_store_view(self) -> list[dict[str, Any]]:
        """대시보드 딜 탭: '쇼핑 커넥트 없는 판매처' (키, 이름)."""
        return [{"key": e["key"], "label": e["label"]} for e in sc_stores.entries(self.db)]

    def sc_store_forget(self, key: str) -> bool:
        """대시보드에서 판매처 하나를 목록에서 뺀다 (다음 딜부터 다시 링크를 물음)."""
        done = sc_stores.forget(self.db, key)
        if done:
            self.db.log_event("INFO", "manual_link", f"쇼핑 커넥트 없는 판매처에서 뺌: {key}")
        return done

    def _sc_store_joined(self, item: QueueItem, url: str) -> None:
        """주인(또는 자동 링크)이 쇼핑 커넥트 링크를 넣은 딜: 그 판매처가 '없음' 목록에 있으면 뺀다 (쇼핑 커넥트에 들어옴)."""
        label = sc_stores.joined(self.db, item.deal.product, url)
        if label:
            self.db.log_event("INFO", "manual_link", f"#{item.id} 쇼핑 커넥트 링크가 생겨 '없는 판매처'에서 뺌: {label}")
            log.info("queue #%d: store %s joined Shopping Connect — removed from the no-link list", item.id, label)

    # ------------------------------------------------- 네이버 쇼핑커넥트 자동 링크 (Blog-Auto 가 브라우저 일)
    def _autolink_product_no(self, deal: Deal, shop: Any, auth: AuthResult, *, ignore_stop: bool = False) -> str | None:
        """자동 링크를 시도할 네이버 딜이면 스토어 상품번호, 아니면 None (→ 예전처럼 주인에게 링크 요청)."""
        cfg = self.settings.autolink.naver
        if not cfg.enabled or shop is None or shop.key != "naver" or self.state.dry_run:
            return None
        if auth.status != "ok" and cfg.unverified != "auto":
            return None  # 판매처 확인이 안 된 딜은 주인이 보고 링크를 만든다 (예전 흐름)
        until = from_iso(self.db.kv_get(autolink.KV_STOP))
        if until and until > utcnow() and not ignore_stop:
            return None  # Blog-Auto 가 멈춘 동안(한도·시간 밖·로그인·제한)은 묻지 않고 예전 흐름으로
        if not autolink.job_queries(deal.product.name):
            return None  # 검색어가 안 남는 이름 — Blog-Auto 가 받지 않으니 30분 기다리지 않고 바로 주인에게
        return store_product_no(deal.product.url)

    def _sc_known_missing(self, product_no: str) -> bool:
        cfg = self.settings.autolink.naver
        at = from_iso(self.db.kv_get(f"{autolink.KV_MISS}{product_no}"))
        return bool(at and utcnow() - at < timedelta(days=cfg.not_found_days))

    async def _held_before_autolink(self, item: QueueItem, deal: Deal) -> bool:
        """쇼핑커넥트에 묻기 전 가격 대조 (주인 결정 10/12 보류 규칙): 보류할 딜이면 보류하고 True — 검색·발급 없음.
        수집 때 페이지를 못 읽은(unverified) 딜은 여기서 한 번 더 본다: 품절 확인 때 읽은 페이지(캐시)로, 그래도 모르면
        스토어 크롬으로 (_read_unverified_price — 발행 때 다시 열지 않게 크롬이 읽은 페이지는 잠시 기억). 못 읽으면 예전처럼 진행."""
        p = deal.product
        await self._read_unverified_price(p)
        if (p.extra.get("page_price") or {}).get("status") == "hold":
            await self._hold_item(item, deal, when="before link")
            return True
        return False

    async def _read_unverified_price(self, p: Product) -> PageMeta | None:
        """수집 때 페이지 가격을 확인 못 한(unverified) 딜의 가격만 한 번 더 본다: 품절 확인 때 읽은 페이지(캐시)·일반 요청으로,
        그래도 가격이 없으면 스토어 크롬으로 (크롬이 읽은 페이지는 잠시 기억 — 쇼핑커넥트 전에 읽었으면 발행 때 다시 안 엶).
        크롬으로 읽은 페이지를 돌려준다 (deal_photo 가 그 사진을 씀 — 크롬을 다시 열지 않게, 주인 10/25). 못 읽으면 예전처럼 진행. 쇼핑커넥트에 묻기 전(_held_before_autolink)과
        주인 사진이 없는 네이버 딜의 발행 직전(deal_photo)에 부른다. 주인이 휴대폰으로 확인하고 링크를 붙인 딜은 읽지 않는다."""
        state = p.extra.get("page_price")
        if not isinstance(state, dict) or state.get("status") != "unverified" or self._owner_checked(p):
            return None
        meta: PageMeta | None = None
        browser: PageMeta | None = None
        if self._can_check_page(p):
            try:
                meta = await self.enricher.fetch(p.url)
            except Exception:  # noqa: BLE001
                meta = None
        if (meta is None or meta.prices is None) and p.shop not in self.settings.deal.enrich.exclude_shops:
            try:
                meta = browser = await self.store_browser.page_meta(p.url)
            except Exception:  # noqa: BLE001
                meta = None
        self._recheck_page_price(p, meta)
        return browser  # 크롬으로 읽은 페이지 — deal_photo 가 그 사진을 쓴다 (크롬을 다시 열지 않게)

    def _request_autolink(self, item: QueueItem, deal: Deal, product_no: str) -> None:
        """Blog-Auto 에 쇼핑커넥트 링크를 부탁한다. 글마다 한 번 (job 을 kv 에 먼저 남기고 받은편지함에 한 줄)."""
        p = deal.product
        job = f"{item.id}-{time.time_ns()}"
        self.db.kv_set(f"{autolink.KV_JOB}{item.id}", json.dumps({"job": job, "no": product_no, "at": to_iso(utcnow())}))
        self.db.update_queue_item(item.id, status=autolink.STATUS, error="auto link requested", deal=deal)
        self.relay_to_blog({"connect_link": {
            "id": item.id, "job": job, "product_no": product_no, "store": store_name(p.url), "name": p.name[:120],
            "queries": autolink.job_queries(p.name), "url": p.url, "at": time.time(),
        }})
        log.info("queue #%d → 쇼핑커넥트 자동 링크 부탁 (상품번호 %s)", item.id, product_no)

    def _cancel_autolink(self, queue_id: int) -> None:
        """기다리던 자동 링크를 거둔다: 늦게 온 답은 무시되고, Blog-Auto 는 아직 시작 안 한 일을 버린다."""
        raw = self.db.kv_take(f"{autolink.KV_JOB}{queue_id}")
        if raw:
            marker = json.loads(raw) or {}
            kind = "toss_cancel" if marker.get("shop") == "toss" else "connect_cancel"
            self.relay_to_blog({kind: {"id": queue_id, "job": marker.get("job"), "at": time.time()}})

    # ------------------------------------------------- 토스쇼핑 쉐어링크 자동 링크 (주인 10/23, Blog-Auto 가 브라우저 일)
    def _toss_autolink_key(self, deal: Deal, shop: Any, *, ignore_stop: bool = False) -> tuple[str, str] | None:
        """자동 쉐어링크를 시도할 토스 딜이면 상품 주소의 (t|i, 번호), 아니면 None (→ 예전처럼 주인에게 링크 요청)."""
        cfg = self.settings.autolink.toss
        if not cfg.enabled or shop is None or shop.key != "toss" or self.state.dry_run:
            return None
        until = from_iso(self.db.kv_get(autolink.KV_TOSS_STOP))
        if until and until > utcnow() and not ignore_stop:
            return None  # Blog-Auto 가 멈춘 동안(로그인·한도·시간 밖)은 묻지 않고 예전 흐름으로
        return autolink.toss_key(deal.product.url)

    def _request_toss_autolink(self, item: QueueItem, deal: Deal) -> None:
        """Blog-Auto 에 토스 쉐어링크를 부탁한다. 글마다 한 번 (job 을 kv 에 먼저 남기고 받은편지함에 한 줄)."""
        p = deal.product
        job = f"{item.id}-{time.time_ns()}"
        self.db.kv_set(f"{autolink.KV_JOB}{item.id}",
                       json.dumps({"job": job, "shop": "toss", "url": p.url, "at": to_iso(utcnow())}))
        self.db.update_queue_item(item.id, status=autolink.STATUS, error="auto link requested", deal=deal)
        self.relay_to_blog({"toss_link": {"id": item.id, "job": job, "url": p.url, "name": p.name[:120], "at": time.time()}})
        log.info("queue #%d → 토스 쉐어링크 자동 링크 부탁 (%s)", item.id, p.url)

    async def _apply_toss_result(self, row: dict[str, Any]) -> int:
        try:
            qid = int(row.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        raw = self.db.kv_get(f"{autolink.KV_JOB}{qid}")
        marker = json.loads(raw) if raw else None
        mine = isinstance(marker, dict) and marker.get("shop") == "toss" and bool(row.get("job")) and marker.get("job") == row.get("job")
        at = row.get("at")
        if mine or (isinstance(at, (int, float)) and time.time() - at <= 3600):
            self._note_toss_stop(row)  # 멈춤(로그인·제한·한도·시간 밖)은 늦게 온 답이어도 따르고 알린다
        if not mine or not isinstance(marker, dict):
            log.info("toss autolink: 반영하지 않은 답 #%s (%s)", qid, "ok" if row.get("ok") else row.get("reason"))
            return 0
        self.db.kv_take(f"{autolink.KV_JOB}{qid}")  # 먼저 지워 둔다 → 같은 답이 두 번 반영되지 않음
        item = self.db.get_queue_item(qid)
        if item is None or item.status != autolink.STATUS:
            return 0
        url = item.deal.product.url
        if str(marker.get("url") or "") != url or str(row.get("url") or "") != url:
            await self._autolink_fallback(item, "toss url mismatch")
            return 1
        if row.get("ok"):
            link = str(row.get("link") or "").strip()
            why = autolink.verify_toss_link(link, url, row)
            if why is None:
                why = self.attach_auto_link(qid, link)
            if why is None:
                log.info("toss autolink #%d: 쉐어링크 붙임 %s", qid, link)
                return 1
            log.warning("toss autolink #%d: 링크를 붙이지 않음 (%s)", qid, why)
            await self._autolink_fallback(item, f"toss link check: {why}")
            return 1
        reason = str(row.get("reason") or "error")
        if reason in ("unavailable", "not_found"):
            # 토스가 판매 중이 아니라고 함 (품절·판매 종료·없는 상품) — 주인에게 묻지 않고 내린다
            self.db.update_queue_item(qid, status="skipped", error=f"toss: {reason} (auto link)")
            self.db.log_event("INFO", "autolink", f"#{qid} 토스 {reason} → 건너뜀")
            return 1
        if self._defer_from_row(item, row, "toss"):
            return 1  # 시간 밖·로그인·한도 — 주인에게 묻지 않고 다시 될 때까지
        await self._autolink_fallback(item, f"toss: {reason}")
        return 1

    def _note_toss_stop(self, row: dict[str, Any]) -> None:
        """Blog-Auto 가 토스 일을 멈춘다고 하면(로그인·제한·한도·시간 밖) 그때까지는 묻지 않는다. 로그인·제한만 주인에게 하루 한 번."""
        retry_at = row.get("retry_at")
        if isinstance(retry_at, (int, float)) and retry_at > time.time():
            until = datetime.fromtimestamp(float(retry_at), tz=UTC)
            old = from_iso(self.db.kv_get(autolink.KV_TOSS_STOP))
            if old is None or until > old:
                self.db.kv_set(autolink.KV_TOSS_STOP, to_iso(until))
        reason = str(row.get("reason") or "")
        if reason in autolink.TOSS_ALERT_REASONS:
            day = local_now(self.settings.app.timezone).strftime("%Y-%m-%d")
            key = f"autolink_toss_alert:{reason}"
            if self.db.kv_get(key) != day:
                self.db.kv_set(key, day)
                self.db.log_event("WARNING", "autolink", f"toss stopped: {reason}")
                log.warning("토스 쉐어링크 자동 링크 멈춤: %s", reason)
                text = (f"토스 쉐어링크 자동 링크 멈춤: {autolink.TOSS_ALERT_REASONS[reason]}. 그동안 들어온 토스 딜은 묻지 않고 기다렸다가 다시 되면 자동으로 링크를 만들어요. "
                        f"{autolink.LOGIN_RESUME_HINT if reason == 'login' else autolink.TOSS_RESUME_HINT}")
                if self.notifier.silent_policy:
                    if not self.notifier.owner_line({"kind": "autolink_stop", "key": f"autolink_toss:{reason}", "urgent": True,
                                                     "text": kakao_fit(f"⛔[멈춤] {text}"), "short": "토스 자동 링크 멈춤",
                                                     "ttl_h": 24}):
                        log.error("토스 멈춤 카톡 줄을 남기지 못함: %s", reason)
                else:
                    self.owner_notice("autolink_stop", f"⛔ {text}")

    def _post_raw_link(self, item: QueueItem, deal: Deal, why: str, *, reason: str = "sc_not_found") -> None:
        """쇼핑커넥트에 없는 네이버 상품: 내 링크 없이 원본 주소로, 제휴 고지 없이 올린다 (주인 결정 10/11). 주인에게 묻지 않음.
        reason (extra['raw_link'] — 고지 도우미·블로그 내보내기가 보는 '제휴 링크 아님' 표시): sc_not_found 자동 링크가 못 찾음 ·
        owner_no_sc 주인이 '쇼핑 커넥트에 없음 → 링크 없이 올리기' · sc_store 주인이 그렇게 표시한 판매처의 새 딜 (주인 10/18)."""
        deal.affiliate_url = deal.product.url
        deal.product.extra["raw_link"] = reason
        self.db.update_queue_item(item.id, status="pending", error=None, deal=deal, reset_created=True)
        self.db.log_event("INFO", "autolink", f"#{item.id} {why} → 원본 주소로")
        log.info("queue #%d %s → 원본 주소로 (고지 없이)", item.id, why)

    async def autolink_tick(self) -> int:
        """Blog-Auto 의 답을 반영하고, 너무 오래 답이 없는 글은 주인 요청으로 넘긴다. 처리한 글 수."""
        rows, pos = self.autolink_results.read()
        n = 0
        for row in rows:
            if row.get("resume") is True:  # Blog-Auto 에서 주인이 멈춤을 풀었음 (python -m blogauto shopconnect resume)
                self.db.kv_set(autolink.KV_STOP, "")
                self.db.kv_delete_prefix("autolink_alert:")  # 다시 멈추면 또 알림
                self.notifier.resolve(*(f"autolink:{r}" for r in autolink.ALERT_REASONS))  # 아직 안 간 멈춤 카톡은 보내지 않게
                log.info("쇼핑커넥트 자동 링크 다시 시작")
                continue
            try:
                n += await self._apply_autolink_result(row)
            except Exception as e:  # noqa: BLE001 — 한 줄 때문에 나머지가 막히지 않게 (그 글은 시간이 지나면 주인 요청으로)
                log.warning("autolink result failed (%s): %s", row.get("id"), e)
        if rows:
            self.autolink_results.commit(pos)
        elif pos != int(self.db.kv_get(autolink.KV_POS) or 0):
            self.autolink_results.commit(pos)  # 파일이 새로 만들어짐
        rows, pos = self.toss_results.read()
        for row in rows:
            if row.get("resume") is True:  # Blog-Auto 에서 주인이 멈춤을 풀었음 (python -m blogauto tosslink resume)
                self.db.kv_set(autolink.KV_TOSS_STOP, "")
                self.db.kv_delete_prefix("autolink_toss_alert:")
                self.notifier.resolve(*(f"autolink_toss:{r}" for r in autolink.TOSS_ALERT_REASONS))
                log.info("토스 쉐어링크 자동 링크 다시 시작")
                continue
            try:
                n += await self._apply_toss_result(row)
            except Exception as e:  # noqa: BLE001 — 한 줄 때문에 나머지가 막히지 않게 (그 글은 시간이 지나면 주인 요청으로)
                log.warning("toss autolink result failed (%s): %s", row.get("id"), e)
        if rows or pos != int(self.db.kv_get(autolink.KV_TOSS_POS) or 0):
            self.toss_results.commit(pos)
        return n + await self._autolink_timeouts() + self._autolink_backlog()

    BACKLOG_PER_TICK = 3

    @staticmethod
    def _naver_short(deal: Deal) -> bool:
        """상품 번호가 없는 네이버 주소로 들어온 딜 — 건너뛴다 (주인 10/23). naver.me 단축 주소(대개 글쓴이의 쇼핑커넥트 제휴 링크 —
        열지 않음), 쇼핑라이브 방송·쇼핑스토리 글·쇼핑윈도처럼 스마트스토어 상품 번호가 없는 주소 (자동 링크를 못 거쳐 주인 링크만 기다리다 버려졌음)."""
        if deal.product.shop != "naver":
            return False
        host = urlparse(deal.product.url or "").netloc.lower()
        return host == "naver.me" or host.endswith(".naver.me") or not store_product_no(deal.product.url)

    def _skip_short(self, item: QueueItem) -> None:
        self.db.update_queue_item(item.id, status="skipped", error="naver short link / no product number (owner 10/23: skip)")
        self.notifier.resolve(f"link:{item.id}")
        self.db.log_event("INFO", "autolink", f"#{item.id} 상품 번호 없는 네이버 주소 딜 → 건너뜀")
        log.info("queue #%d skipped — naver short link", item.id)

    def _autolink_backlog(self) -> int:
        """주인 링크를 기다리는 딜 중 자동 링크(쇼핑커넥트·토스 쉐어링크)를 거칠 수 있는 것을 자동으로 보낸다 (주인 10/23 '밀린 링크
        자동으로 싹 처리'). 딜마다 한 번만 (kv autolink:tried:<번호>) — 자동이 안 되면 예전처럼 주인 링크 요청으로 돌아가고 또 보내지 않는다.
        한 번에 BACKLOG_PER_TICK 개씩 (Blog-Auto 하루 한도·간격은 그쪽이 지킨다)."""
        if self.state.dry_run:
            return 0
        n = 0
        for item in self.db.awaiting_items(50):
            if n >= self.BACKLOG_PER_TICK:
                break
            key = f"{autolink.KV_TRIED}{item.id}"
            if self.db.kv_get(key) and not self.db.kv_get(f"autolink:timeout_retry:{item.id}") and self._timed_out_once(item.id):
                # 예전 판은 시간 초과 한 번에 바로 주인에게 물었다 (주인 10/25 '이런 거는 왜 아직 떠') — 그런 딜도 한 번 더 자동으로
                self.db.kv_set(f"autolink:timeout_retry:{item.id}", to_iso(utcnow()))
                self.db.kv_take(key)
            if self.db.kv_get(key):
                continue
            deal = item.deal
            if self._naver_short(deal):  # 밀려 있던 네이버 단축 주소 딜도 건너뜀 (주인 10/23)
                self._skip_short(item)
                continue
            shop = self.registry.get(deal.product.shop)
            cached = deal.product.extra.get("auth")
            auth = (AuthResult(str(cached.get("status") or "ok"), str(cached.get("reason") or ""), cached.get("seller"))
                    if isinstance(cached, dict) else AuthResult("unknown", ""))
            if self._toss_autolink_key(deal, shop):
                self.db.kv_set(key, to_iso(utcnow()))
                self.notifier.resolve(f"link:{item.id}")  # 자동으로 넘어감 — 안 되면 다시 열린다
                self._request_toss_autolink(item, deal)
                n += 1
                continue
            no = self._autolink_product_no(deal, shop, auth)
            if no and not self._sc_known_missing(no):
                self.db.kv_set(key, to_iso(utcnow()))
                self.notifier.resolve(f"link:{item.id}")
                self._request_autolink(item, deal, no)
                n += 1
        if n:
            log.info("밀린 링크 대기 딜 %d개 → 자동 링크로 보냄", n)
        return n

    async def _apply_autolink_result(self, row: dict[str, Any]) -> int:
        try:
            qid = int(row.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0
        raw = self.db.kv_get(f"{autolink.KV_JOB}{qid}")
        marker = json.loads(raw) if raw else None
        mine = isinstance(marker, dict) and bool(row.get("job")) and marker.get("job") == row.get("job")
        at = row.get("at")
        if mine or (isinstance(at, (int, float)) and time.time() - at <= 3600):
            # 멈춤(로그인·제한·한도·시간 밖)은 늦게 온 답(그 글은 이미 주인 요청으로 넘어감)이어도 따르고 알린다
            self._note_autolink_stop(row)
        if not mine or not isinstance(marker, dict):
            # 이미 반영했거나(같은 줄을 다시 읽음) 취소·시간 초과로 넘어간 글의 늦은 답
            log.info("autolink: 반영하지 않은 답 #%s (%s)", qid, "ok" if row.get("ok") else row.get("reason"))
            return 0
        self.db.kv_take(f"{autolink.KV_JOB}{qid}")  # 먼저 지워 둔다 → 같은 답이 두 번 반영되지 않음
        item = self.db.get_queue_item(qid)
        if item is None or item.status != autolink.STATUS:
            return 0
        no = str(marker.get("no") or "")
        if (row.get("product_no") and str(row.get("product_no")) != no) or store_product_no(item.deal.product.url) != no:
            await self._autolink_fallback(item, "product_no mismatch")
            return 1
        if row.get("ok"):
            link = str(row.get("link") or "").strip()
            why = await autolink.verify_link(self.http, link, no, deal_url=item.deal.product.url)
            if why is None:
                why = self.attach_auto_link(qid, link)
            if why is None:
                log.info("autolink #%d 상품 %s: 링크 붙임 (%s, 수수료 %s)", qid, no,
                         "기존 링크" if row.get("existing") else "새로 발급", row.get("commission") or "-")
                return 1
            log.warning("autolink #%d 상품 %s: 링크를 붙이지 않음 (%s)", qid, no, why)
            await self._autolink_fallback(item, f"link check: {why}")
            return 1
        reason = str(row.get("reason") or "error")
        if reason == "not_found":
            self.db.kv_set(f"{autolink.KV_MISS}{no}", to_iso(utcnow()))
            self._post_raw_link(item, item.deal, "쇼핑커넥트에 없음")
            return 1
        if self._defer_from_row(item, row, "naver", no):
            return 1  # 시간 밖·로그인·한도 — 주인에게 묻지 않고 다시 될 때까지
        await self._autolink_fallback(item, reason)
        return 1

    def _note_autolink_stop(self, row: dict[str, Any]) -> None:
        """Blog-Auto 가 멈춘다고 하면(한도·시간 밖·로그인·제한) 그때까지는 묻지 않는다. 로그인·제한만 주인에게 한 번 알림."""
        retry_at = row.get("retry_at")
        if isinstance(retry_at, (int, float)) and retry_at > time.time():
            until = datetime.fromtimestamp(float(retry_at), tz=UTC)
            old = from_iso(self.db.kv_get(autolink.KV_STOP))
            if old is None or until > old:
                self.db.kv_set(autolink.KV_STOP, to_iso(until))
        reason = str(row.get("reason") or "")
        if reason in autolink.ALERT_REASONS:
            day = local_now(self.settings.app.timezone).strftime("%Y-%m-%d")
            key = f"autolink_alert:{reason}"
            if self.db.kv_get(key) != day:
                self.db.kv_set(key, day)
                self.db.log_event("WARNING", "autolink", f"stopped: {reason}")
                log.warning("쇼핑커넥트 자동 링크 멈춤: %s", reason)
                text = (f"쇼핑커넥트 자동 링크 멈춤: {autolink.ALERT_REASONS[reason]}. 그동안 들어온 네이버 딜은 묻지 않고 기다렸다가 다시 되면 자동으로 링크를 만들어요. "
                        f"{autolink.LOGIN_RESUME_HINT if reason == 'login' else autolink.RESUME_HINT}")
                if self.notifier.silent_policy:  # 알림 정책(10/11): 카톡 할 일 한 통 (v2 키 — 다시 시작하면 해결)
                    if not self.notifier.owner_line({"kind": "autolink_stop", "key": f"autolink:{reason}", "urgent": True,
                                                     "text": kakao_fit(f"⛔[멈춤] {text}"), "short": "쇼핑커넥트 자동 링크 멈춤",
                                                     "ttl_h": 24}):
                        log.error("쇼핑커넥트 멈춤 카톡 줄을 남기지 못함: %s", reason)
                else:
                    self.owner_notice("autolink_stop", f"⛔ {text}")

    def _stop_until(self, shop: str) -> datetime | None:
        """Blog-Auto 가 그 일(naver 쇼핑커넥트·toss 쉐어링크)을 멈춘 시각까지 — 멈추지 않았으면 None."""
        until = from_iso(self.db.kv_get(autolink.KV_TOSS_STOP if shop == "toss" else autolink.KV_STOP))
        return until if until and until > utcnow() else None

    def _defer_autolink(self, item: QueueItem, deal: Deal, *, shop: str, until: datetime, no: str = "") -> None:
        """Blog-Auto 가 지금 못 함 (시간 밖·로그인·한도·멈춤 — 주인 10/23 '묻지 말고 기다렸다가'): 주인에게 링크를 묻지 않고
        다시 될 때(until, 또는 주인이 다시 로그인해 resume)까지 자동 링크 대기로 둔다. 깨우는 것은 _autolink_timeouts."""
        marker = {"deferred": True, "shop": shop, "until": to_iso(until), "at": to_iso(utcnow()), "no": no, "url": deal.product.url}
        self.db.kv_set(f"{autolink.KV_JOB}{item.id}", json.dumps(marker))
        self.db.update_queue_item(item.id, status=autolink.STATUS, error="auto link waiting (Blog-Auto paused)", deal=deal)
        self.notifier.resolve(f"link:{item.id}")
        log.info("queue #%d → 자동 링크 기다림 (%s, %s 까지)", item.id, shop, to_iso(until))

    def _defer_from_row(self, item: QueueItem, row: dict[str, Any], shop: str, no: str = "") -> bool:
        """답이 '지금 못 함'(WAIT_REASONS)이면 기다리게 하고 True."""
        if str(row.get("reason") or "") not in autolink.WAIT_REASONS:
            return False
        retry_at = row.get("retry_at")
        until = (datetime.fromtimestamp(float(retry_at), tz=UTC) if isinstance(retry_at, (int, float)) and retry_at > time.time()
                 else self._stop_until(shop) or utcnow() + timedelta(hours=1))
        self._defer_autolink(item, item.deal, shop=shop, until=until, no=no)
        return True

    WAKE_INFLIGHT_MAX = 3  # 기다리던 글을 깨울 때 Blog-Auto 에 한꺼번에 맡겨 두는 수 (하나씩 1~2분 — 30분 안에 답이 오게)

    async def _autolink_timeouts(self) -> int:
        now = utcnow()
        n = 0
        items = self.db.awaiting_items(50, statuses=(autolink.STATUS,))
        markers = {}
        for item in items:
            raw = self.db.kv_get(f"{autolink.KV_JOB}{item.id}")
            try:
                markers[item.id] = json.loads(raw) if raw else {}
            except ValueError:
                markers[item.id] = {}
        inflight = sum(1 for m in markers.values() if isinstance(m, dict) and m and not m.get("deferred"))
        for item in items:
            marker = markers.get(item.id) or {}
            if isinstance(marker, dict) and marker.get("deferred"):  # Blog-Auto 가 다시 될 때까지 기다리는 글
                shop = str(marker.get("shop") or "naver")
                until = from_iso(marker.get("until"))
                if until is not None and until > now and self._stop_until(shop) is not None:
                    continue  # 아직 멈춤 (주인이 다시 로그인해 resume 하면 멈춤이 풀려 바로 깨움)
                if self._stop_until(shop) is not None:  # 멈춤이 늘었음 — 그 시각까지 더 기다림
                    self._defer_autolink(item, item.deal, shop=shop, until=self._stop_until(shop) or now, no=str(marker.get("no") or ""))
                    continue
                if inflight >= self.WAKE_INFLIGHT_MAX:
                    continue  # 앞서 깨운 글의 답을 받은 뒤에 (한꺼번에 맡기면 30분 안에 다 못 함)
                if shop == "toss":
                    self._request_toss_autolink(item, item.deal)
                else:
                    self._request_autolink(item, item.deal, str(marker.get("no") or ""))
                inflight += 1
                n += 1
                continue
            cfg = self.settings.autolink.toss if isinstance(marker, dict) and marker.get("shop") == "toss" else self.settings.autolink.naver
            at = from_iso(marker.get("at")) if isinstance(marker, dict) else None
            if at is not None and at > now - timedelta(minutes=cfg.wait_minutes):
                continue
            tries = int(marker.get("tries") or 0) if isinstance(marker, dict) else 0
            self._cancel_autolink(item.id)
            if tries < self.AUTOLINK_TIMEOUT_RETRIES and isinstance(marker, dict):
                # 시간 초과는 블로그 프로그램이 잠깐 바빴던 것일 수 있다 (주인 10/25 — 원격 로그인 중이라 켈로그 #114 를 바로 물어봄):
                # 주인에게 묻기 전에 한 번 더 맡긴다
                if marker.get("shop") == "toss":
                    self._request_toss_autolink(item, item.deal)
                else:
                    self._request_autolink(item, item.deal, str(marker.get("no") or ""))
                self._mark_autolink_tries(item.id, tries + 1)
                self.db.kv_set(f"autolink:timeout_retry:{item.id}", to_iso(utcnow()))  # 밀린 링크 보내기가 또 다시 보내지 않게
                self.db.log_event("INFO", "autolink", f"#{item.id} timeout → 한 번 더 자동으로")
                n += 1
                continue
            await self._autolink_fallback(item, "timeout")
            n += 1
        return n

    AUTOLINK_TIMEOUT_RETRIES = 1

    def _timed_out_once(self, queue_id: int) -> bool:
        """이 딜의 자동 링크가 시간 초과로 주인 요청이 됐었나 (기록: '#번호 timeout → 링크 요청')."""
        row = self.db._one("SELECT 1 FROM events WHERE kind='autolink' AND message = ? LIMIT 1", (f"#{queue_id} timeout → 링크 요청",))
        return row is not None

    def _mark_autolink_tries(self, queue_id: int, tries: int) -> None:
        raw = self.db.kv_get(f"{autolink.KV_JOB}{queue_id}")
        try:
            marker = json.loads(raw) if raw else {}
        except ValueError:
            marker = {}
        if isinstance(marker, dict) and marker:
            self.db.kv_set(f"{autolink.KV_JOB}{queue_id}", json.dumps({**marker, "tries": tries}))

    async def _autolink_fallback(self, item: QueueItem, why: str) -> None:
        """자동 링크가 안 됨 (오류·시간 초과·로그인·한도…): 예전처럼 주인에게 링크 요청."""
        self.db.kv_set(f"{autolink.KV_TRIED}{item.id}", to_iso(utcnow()))  # 밀린 링크 자동 보내기가 다시 보내지 않게
        self.db.log_event("INFO", "autolink", f"#{item.id} {why} → 링크 요청")
        cached = item.deal.product.extra.get("auth")
        auth = (AuthResult(str(cached.get("status") or "ok"), str(cached.get("reason") or ""), cached.get("seller"))
                if isinstance(cached, dict) else AuthResult("ok", ""))
        shop = self.registry.get(item.deal.product.shop)
        await self._request_manual_link(item, item.deal, shop, auth, f"autolink: {why}")

    def _needs_deeplink(self, item: QueueItem) -> bool:
        """쿠팡 딥링크 API 호출이 필요한 글인가 (쿠팡 딜인데 아직 제휴 링크가 없음)."""
        p = item.deal.product
        return p.deal_kind != "info" and p.shop == "coupang" and not item.deal.affiliate_url and p.source != "manual"

    def _expire_queue(self, now: Any) -> int:
        cfg = self.settings.publish
        expired = self.db.expire_queue(
            now - timedelta(hours=cfg.queue_ttl_hours),
            now,
            awaiting_older_than=now - timedelta(hours=cfg.manual_link_ttl_hours),
            review_older_than=now - timedelta(hours=cfg.review_ttl_hours),  # 확인(/ok) 요청은 링크 요청(24시간)과 따로 12시간
        )
        if expired:
            log.info("expired %d stale queue items", expired)
        return expired

    async def process_queue_once(self) -> bool:
        """대기열에서 1건 발행 시도. 무언가 처리했으면 True."""
        cfg = self.settings.publish
        now = utcnow()
        expired = self._expire_queue(now)
        if expired or not self._pending_notice_ready:
            self._pending_notice_ready = True
            await self.refresh_pending_notice()
        await self.autolink_tick()

        if self.state.paused or not cfg.enabled:
            return False
        decision = self.rate_limiter.check(now)
        if not decision.allowed:
            return False
        item = self.db.next_pending()
        if item is None:
            return False
        if self._needs_deeplink(item) and not self.budget.available("deeplink"):
            # 쿠팡 딥링크 예산이 이번 시간에 다 찼다: 쿠팡 딜은 기다리게 두고 다른 몰의 글을 먼저 올린다
            item = next((c for c in self.db.pending_items(50) if not self._needs_deeplink(c)), None)
            if item is None:
                log.debug("coupang deeplink budget exhausted — queue waits for the next hour")
                return False

        deal = item.deal
        pid = deal.product.product_id
        if self._posted_recently(pid, now):
            self.db.update_queue_item(item.id, status="skipped", error="already posted")
            return True
        if deal.product.deal_kind == "info":
            return await self._publish_info(item, deal)

        # 정품·공식 판매처 확인: 병행수입 등 표시는 버리고, 오픈마켓인데 확인이 안 되면 관리자에게 묻는다
        auth = await self._authenticity(item, deal)
        auth_shop = self.registry.get(deal.product.shop)
        if auth.status == "reject":
            self.db.update_queue_item(item.id, status="skipped", error=f"not genuine: {auth.reason}")
            self.db.log_event("INFO", "authenticity", f"{pid} 정품이 아닐 수 있어 건너뜀 ({auth.reason}): {deal.product.name[:50]}")
            log.info("queue #%d skipped — authenticity: %s", item.id, auth.reason)
            return True
        if auth.status == "unknown" and self.evaluator.is_food(deal.product):
            # 식품은 '정품' 개념이 없으니 공식 판매처 확인이 안 돼도 통과 (병행수입 같은 거부 낱말은 위에서 이미 걸렀다)
            auth = replace(auth, status="ok", reason="식품 (판매처 확인 생략)")
            cached = deal.product.extra.get("auth")
            if isinstance(cached, dict):
                deal.product.extra["auth"] = {**cached, "status": "ok", "reason": auth.reason}
        if (
            auth.status == "unknown"
            and not deal.product.extra.get("auth_approved")
            # 링크를 직접 만드는 몰(토스·네이버)은 어차피 관리자가 앱을 여니, 따로 묻거나 버리지 않고 링크 요청에 확인 문구를 붙인다
            and not (auth_shop is not None and auth_shop.link_mode == "manual")
        ):
            action = self.settings.deal.authenticity.unverified_action
            if action == "skip":  # 확인 안 되면 묻지 않고 제외
                self.db.update_queue_item(item.id, status="skipped", error=f"unverified seller: {auth.reason}")
                self.db.log_event("INFO", "authenticity", f"{pid} 공식 판매처 확인 안 돼 건너뜀: {deal.product.name[:50]}")
                return True
            if action == "review":
                return await self._request_deal_review(item, deal, auth)

        so = self.settings.deal.sold_out
        state, err = await self._ensure_link(item)
        if state == "manual":
            if so.enabled and so.check_page_before_link_request and await self._page_sold_out(deal.product):
                # 링크를 만들어 달라고 하기 전에 품절이면 조용히 내린다 (헛수고 방지)
                self.db.update_queue_item(item.id, status="skipped", error="sold out (page, before link request)")
                self.db.log_event("INFO", "soldout", f"{pid} skipped before link request")
                log.info("queue #%d sold out before link request — skipped", item.id)
                return True
            shop = self.registry.get(err or "")
            if self._sc_store_known(deal, shop, auth):
                # 주인이 '쇼핑 커넥트에 없음'으로 표시한 판매처: 묻지 않고 링크 없이 (주인 10/18). 가격이 안 맞는 딜은 보류
                if await self._held_before_autolink(item, deal):
                    return True
                self._post_raw_link(item, deal, "쇼핑 커넥트 없는 판매처 (주인 표시)", reason="sc_store")
                return True
            toss = self._toss_autolink_key(deal, shop, ignore_stop=True)
            if toss:
                # 토스: 주인에게 묻기 전에 Blog-Auto 가 쉐어링크를 자동으로 (주인 10/23). 안 되면 아래와 같은 링크 요청으로 돌아온다
                if (until := self._stop_until("toss")) is not None:
                    self._defer_autolink(item, deal, shop="toss", until=until)  # 시간 밖·로그인·한도 — 묻지 않고 기다림
                    return True
                self._request_toss_autolink(item, deal)
                return True
            product_no = self._autolink_product_no(deal, shop, auth, ignore_stop=True)
            if product_no and (until := self._stop_until("naver")) is not None and not self._sc_known_missing(product_no):
                if await self._held_before_autolink(item, deal):
                    return True
                self._defer_autolink(item, deal, shop="naver", until=until, no=product_no)
                return True
            if product_no:
                # 네이버: 주인에게 묻기 전에 쇼핑커넥트에서 자동으로 (주인 결정 10/11). 안 되면 아래와 같은 링크 요청으로 돌아온다
                if await self._held_before_autolink(item, deal):
                    return True  # 가격이 안 맞아 보류할 딜은 쇼핑커넥트에서 찾지도 발급하지도 않는다
                if self._sc_known_missing(product_no):
                    self._post_raw_link(item, deal, "쇼핑커넥트에 없던 상품 (기억)")
                else:
                    self._request_autolink(item, deal, product_no)
                return True
            if self._naver_short(deal):
                self._skip_short(item)
                return True
            return await self._request_manual_link(item, deal, shop, auth, err)
        if state == "skip":
            self.db.update_queue_item(item.id, status="skipped", error=err)
            return True
        if state == "wait":
            log.info("queue #%d waits — %s", item.id, err)
            return False
        if state == "fail":
            await self._handle_publish_failure(item, err or "link conversion failed")
            return True

        if so.enabled and so.check_page_before_publish and await self._page_sold_out(deal.product):
            self.db.update_queue_item(item.id, status="skipped", error="sold out (page, before publish)")
            self.db.log_event("INFO", "soldout", f"{pid} skipped before publish")
            log.info("queue #%d sold out before publish — skipped", item.id)
            return True

        if "comment" not in deal.product.extra:
            await self.add_commentary(deal)
        silent = in_time_window(local_now(self.settings.app.timezone), cfg.quiet_hours)
        # 사진은 연습 모드·사진 끔이어도 확인한다 (블로그 내보내기·미리보기용, 보내는 건 publisher 가 send_photo 일 때만)
        photo = await self.deal_photo(deal)
        if (deal.product.extra.get("page_price") or {}).get("status") == "hold" and not self._owner_checked(deal.product):
            await self._hold_item(item, deal)  # 발행 직전에 연 상품 페이지 가격이 글 가격과 다름 → 올리지 않음
            return True
        result = await self.publisher.publish(deal, silent=silent, photo=photo)
        if result.ok:
            self.db.record_post(
                deal,
                channel_id=str(self.publisher.channel_id) if self.publisher.channel_id is not None else None,
                message_id=result.message_id,
                dry_run=result.dry_run,
                now=utcnow(),
            )
            self.db.update_queue_item(item.id, status="published", deal=deal)
            self.db.log_event("INFO", "publish", f"{pid} {deal.product.name[:60]} {deal.product.price}")
            log.info("published [%s/%s] %s (%s)", deal.product.source, deal.product.shop, deal.product.name[:50], "dry-run" if result.dry_run else result.message_id)
            if result.dry_run:
                # 연습 모드: 채널에 올라갔을 글 전체를 관리자 챗으로 보여준다. 스레드/복붙 문구는 실제 발행 때만
                await self.notifier.notify_published(deal, result, preview=self.publisher.render(deal))
                self.export_published(deal, photo, preview=True)  # 블로그 미리보기(/hotdeal)용 — 실제 블로그 발행엔 안 쓰임
            else:
                await self.notifier.notify_published(deal, result)
                row = self.export_published(await self.channel_deal(deal, "blog"), photo)  # 블로그 글의 쿠팡 링크는 blog 채널 아이디로
                # 사진 기다림 기록이 먼저 — 인스타 시간표(ig_scheduler)가 기다리는 동안 사진 없는 딜 글을 미룬다
                await self._ask_late_photo(item.id, deal, photo, row)
                await self._publish_side_channels(deal, photo=photo, deal_no=item.id)
        else:
            await self._handle_publish_failure(item, result.error or "unknown error", deal=deal, maybe_posted=result.maybe_posted)
        return True

    EXPORT_KEEP_DAYS = 14  # media/export 의 사진은 블로그가 가져간 뒤 이만큼 지나면 지운다

    def export_published(self, deal: Deal, photo: bytes | None = None, *, preview: bool = False) -> dict[str, Any] | None:
        """채널에 실제로 올린 딜을 published_deals.jsonl 에 한 줄씩 남긴다 (블로그 자동 발행·카카오 전송이 읽어 감). 남긴 줄을 돌려준다.
        사진은 deal_photo 가 확인한 상품 사진(600px 이상 JPEG)만 media/export 에 저장해 경로를 적는다.

        Blog-Auto 와의 약속 (사진 칸, 주인 결정 10/15 — Blog-Auto 는 같은 product_id 의 가장 나중 줄을 쓴다):
        - photo_kind: 'verified'(봇이 확인한 상품 사진) | 'owner'(주인이 보낸 사진 — 링크와 같이, 또는 발행 뒤 4시간 안에 답장)
          | 'text_card'(사진 없음 — text_card_file 의 글자 카드) | null(사진도 글자 카드도 없음)
        - photo_file: 상품 사진 경로 (사진이 없으면 ''). 글자 카드는 여기에 넣지 않는다 (상품 사진이 아님)
        - photo_wait_until: 사진이 없을 때 posted_at + publish.late_photo_hours(4시간) — 그때까지 주인 사진을 기다려도 됨
          (그 안에 사진이 오면 같은 product_id·posted_at 으로 사진이 바뀐 줄이 하나 더 붙음, photo_updated_at 이 있음). 사진이 있으면 null
        - text_card_file: 사진이 없는 딜이면 봇이 그린 글자 카드(1080×1350 JPEG, media/export) 경로, 사진이 있으면 ''
        photo_src: 받은 주소 ('owner' = 주인 사진)."""
        try:
            p = deal.product
            out_dir = self.settings.data_dir / "media" / "export"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', p.product_id)}_{int(time.time())}"
            photo_file = ""
            if photo:
                photo_file = str(out_dir / f"{stem}.jpg")
                Path(photo_file).write_bytes(photo)
            text_card_file = "" if photo else self._export_text_card(deal, out_dir / f"{stem}_card.jpg")
            self._prune_exports(out_dir)
            posted_at = self.clock()
            row = self._export_row(deal, photo_file=photo_file, text_card_file=text_card_file, posted_at=posted_at,
                                   wait=not photo and not preview and self.late_photo.enabled() and p.deal_kind != "info")
            self._append_export(row, preview=preview)
            return row
        except Exception as e:  # noqa: BLE001 — 내보내기 실패가 발행을 막지 않게
            log.warning("export_published failed: %s", e)
            return None

    def _export_row(self, deal: Deal, *, photo_file: str, text_card_file: str, posted_at: datetime, wait: bool,
                    kind: str | None = None) -> dict[str, Any]:
        p = deal.product
        shop = self.registry.get(p.shop)
        link = deal.affiliate_url or p.url
        ctx = self.renderer
        tier, _ = ctx.deal_tier(deal)
        v = deal.verdict
        # 채널 글 첫 줄과 같은 근거·같은 숫자 (배송비를 반영한 평가기 값, 믿을 만한 평균만)
        ref_price, ref_pct, is_market = ctx.reference(deal)
        ref_label = (f"{market_name(v.market_source)} 최저가" if is_market else "평소 가격") if ref_price else ""
        if kind is None:
            kind = ("owner" if p.extra.get("owner_photo") else "verified") if photo_file else ("text_card" if text_card_file else None)
        return {
            "product_id": p.product_id, "posted_at": posted_at.isoformat(timespec="seconds"),
            "name": clean_name(p.name), "raw_name": p.name, "headline": p.headline, "price": p.price,
            "ref_price": ref_price, "ref_label": ref_label,
            "ref_pct": round(ref_pct) if ref_price and ref_pct else None,
            "tier": tier, "discount_rate": v.discount_rate, "shipping": p.shipping, "rating": p.rating,
            "review_count": p.review_count, "category": p.category, "shop": p.shop, "shop_name": shop.name if shop else p.shop,
            "disclosure": deal_disclosure(shop, p.extra) or "", "link": link, "product_url": p.url,
            # Blog-Auto 와의 약속 (주인 10/18): affiliate=False 면 link 가 제휴 링크가 아닌 원본 상품 주소 (쇼핑 커넥트에 없는 네이버 딜 등)
            # — 수수료가 없으니 이 딜 몫의 대가성 문구·[광고] 를 붙이지 않는다. no_affiliate: 까닭 (sc_not_found·owner_no_sc·sc_store)
            "affiliate": bool(deal_disclosure(shop, p.extra)),
            "no_affiliate": str(p.extra.get("raw_link") or "") if not deal_disclosure(shop, p.extra) else "",
            "image_url": p.image_url, "photo_file": photo_file, "photo_kind": kind,
            "photo_src": ("owner" if p.extra.get("owner_photo") else p.image_url) if photo_file else None,
            "photo_wait_until": (posted_at + timedelta(hours=self.settings.publish.late_photo_hours)).isoformat(timespec="seconds")
            if wait else None,
            "text_card_file": text_card_file,
            "kakao_text": ctx.render_deal(deal, link, shop=shop, template="deal_kakao.j2", autoescape=False),
            **{k: v for k, v in ctx.deal_facts(deal).items() if k != "ref_price"},
        }

    def _append_export(self, row: dict[str, Any], *, preview: bool = False) -> None:
        name = "preview_deals.jsonl" if preview else "published_deals.jsonl"
        with (self.settings.data_dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _export_text_card(self, deal: Deal, path: Path) -> str:
        """사진 없는 딜의 글자 카드 (인스타 글자 카드와 같은 그림 — 상품명·가격 같은 사실만). 못 그리면 ''.
        바닥 안내는 채널 중립(NEUTRAL_CTA): 블로그 글엔 구매 링크가 따로 있어 인스타용 '구매 링크는 프로필 링크에서' 는 틀린 말."""
        if self.card is None:
            return ""
        try:
            shop = self.registry.get(deal.product.shop)
            tier, _ = self.renderer.deal_tier(deal)
            info = None
            if self.card.layout != "current":
                from dealbot.publisher.insta_style import card_info

                info = card_info(deal, self.renderer, shop=shop)
            path.write_bytes(self.card.render(deal, None, shop_name=shop.name if shop else None, tier=tier, info=info,
                                              cta=NEUTRAL_CTA))
            return str(path)
        except Exception as e:  # noqa: BLE001
            log.warning("text card for the blog export failed: %s", e)
            return ""

    def export_photo_update(self, deal: Deal, photo: bytes, *, posted_at: datetime) -> dict[str, Any] | None:
        """발행 뒤 주인이 보낸 사진: 같은 product_id·posted_at 으로 사진이 바뀐 줄을 하나 더 쓴다 (photo_kind 'owner').
        Blog-Auto 는 같은 상품의 가장 나중 줄을 쓰고, 품절 기록과 견주는 posted_at 은 처음 발행 시각 그대로."""
        try:
            out_dir = self.settings.data_dir / "media" / "export"
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', deal.product.product_id)}_{int(time.time())}_owner"
            photo_file = str(out_dir / f"{stem}.jpg")
            Path(photo_file).write_bytes(photo)
            row = self._export_row(deal, photo_file=photo_file, text_card_file="", posted_at=posted_at, wait=False, kind="owner")
            row["photo_updated_at"] = utcnow().isoformat(timespec="seconds")
            self._append_export(row)
            return row
        except Exception as e:  # noqa: BLE001
            log.warning("export photo update failed: %s", e)
            return None

    async def _ask_late_photo(self, queue_id: int, deal: Deal, photo: bytes | None, row: dict[str, Any] | None) -> None:
        """확인된 상품 사진 없이 올라간 딜: 4시간 안에 주인 사진을 받는다 (관리자 챗 📷 + 할 일)."""
        if photo is not None or row is None or not row.get("photo_wait_until"):
            return
        try:
            posted = from_iso(str(row.get("posted_at"))) or utcnow()
            await self.late_photo.ask(queue_id, deal, posted)
        except Exception as e:  # noqa: BLE001 — 사진 부탁이 실패해도 발행은 그대로
            log.warning("late photo ask failed for #%d: %s", queue_id, e)

    def save_late_photo(self, item: QueueItem, jpeg: bytes) -> bool:
        """발행 뒤 받은 주인 사진을 그 딜의 상품 사진으로 (owner_photos 에 저장, 대기열 딜에 적음 — 인스타가 나중에 쓰면 이 사진)."""
        folder = self.settings.data_dir / self.OWNER_PHOTO_DIR
        name = f"q{item.id}_{time.time_ns()}.jpg"
        try:
            from PIL import Image

            with Image.open(io.BytesIO(jpeg)) as im:
                w, h = im.size
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(jpeg)
        except OSError as e:
            log.warning("late owner photo not saved for #%d: %s", item.id, e)
            return False
        p = item.deal.product
        p.extra["owner_photo"] = {"file": name, "at": to_iso(utcnow()), "w": w, "h": h, "late": True}
        self.db.update_queue_item(item.id, status=item.status, error=item.last_error, deal=item.deal)
        self.db.kv_set(f"{self.OWNER_PHOTO_KV}{p.product_id}", json.dumps(p.extra["owner_photo"], ensure_ascii=False))
        try:
            self.ig.attach_photo(item.id, jpeg)  # 인스타 대기열(ig_queue)은 사진 사본을 따로 들고 있음 — 거기에도
        except Exception as e:  # noqa: BLE001 — 인스타 쪽 문제로 사진 받기가 실패하지 않게
            log.warning("late owner photo not attached to the instagram queue for #%d: %s", item.id, e)
        return True

    def late_photo_waiting(self, queue_id: int) -> bool:
        """이 딜이 지금 주인 사진을 기다리는 중인가 ('/photo N' 을 딜 사진으로 받을지 — 아니면 블로그 명령으로 넘김)."""
        return self.late_photo.waiting(queue_id) is not None

    async def attach_late_photo(self, queue_id: int, photo: PhotoSource) -> str:
        return await self.late_photo.attach(queue_id, photo)

    def _prune_exports(self, out_dir: Path) -> None:
        cutoff = time.time() - self.EXPORT_KEEP_DAYS * 86400
        for f in out_dir.glob("*.jpg"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    async def _fetch_image(self, url: str | None, *, referer: str | None = None) -> bytes | None:
        """사진 원본을 받아 온다. 이미지인지는 clean_image 가 직접 열어 본다 (octet-stream 으로 주는 CDN 도 있음). 실패하면 None.
        referer: 상품 페이지 주소 (외부에서 가져가는 걸 막는 CDN 은 사진 주소 자신을 Referer 로 주면 거절함)."""
        if not url:
            return None
        try:
            resp = await self.http.get(url, follow_redirects=True, timeout=15, headers={"Referer": referer or url})
            ctype = resp.headers.get("content-type", "").lower()
            if resp.status_code != 200 or ctype.startswith(("text/", "application/json")) or len(resp.content) > 10_000_000:
                log.info("photo fetch: HTTP %s %s for %s", resp.status_code, ctype or "-", url[:100])
                return None
            return resp.content
        except Exception as e:  # noqa: BLE001
            log.info("photo fetch failed %s: %s", url[:100], e)
            return None

    # ------------------------------------------------- 주인이 링크와 같이 보낸 네이버 딜 상품 사진 (주인 결정 10/15)
    OWNER_PHOTO_DIR = "owner_photos"  # data_dir 아래. 공개 폴더(media)가 아님 — 발행할 때 media/export·media/owner_… 로 복사됨
    OWNER_PHOTO_KEEP_DAYS = 14
    OWNER_PHOTO_KV = "myphoto:"  # 상품마다 마지막으로 받은 주인 사진 (mylink 처럼 — 같은 상품이 또 나오면 그 사진도)
    OWNER_PUBLIC_PREFIX = "owner_"  # media/ 아래 공개 사본 이름 (발행할 때)

    async def _check_owner_photo(self, photo: PhotoSource) -> tuple[bytes | None, str]:
        """링크와 같이 온 사진을 받아(함수면) 검사한다: (저장할 JPEG, "") 또는 (None, 주인에게 보일 이유).
        큰 사진(최대 4,000만 화소)을 푸는 일은 따로 돌린다 — 그동안 텔레그램 받기·발행이 멈추지 않게 (검토 지적)."""
        if callable(photo):
            try:
                data, why = await photo()
            except Exception as e:  # noqa: BLE001 — 텔레그램 오류·시간 초과: 링크도 안 붙이고 다시 보내 달라고
                log.warning("owner photo download failed: %s", type(e).__name__)
                return None, PHOTO_NOT_RECEIVED
            if data is None:
                return None, why or PHOTO_NOT_RECEIVED
        else:
            data = photo
        # 휴대폰 화면 캡처면 상품 사진 부분을 알아서 잘라 본다 (주인 10/18 — 그래도 안 되면 그때 이유)
        return await asyncio.to_thread(owner_photo, data, min_side=self.settings.publish.photo_min_side, crop=True)

    def _take_owner_photo(self, item: QueueItem, jpeg: bytes | None, *, sent: bool = False) -> tuple[str | None, str]:
        """검사를 마친 사진(jpeg)을 그 딜의 상품 사진으로 저장 (네이버 상품 딜만). 링크를 붙이기 바로 전에 부른다 (await 없음).
        (답장에 붙일 사진 상태, 못 쓰는 이유). 상태: 'ok'(저장함) · 'kept'(사진 없이 다시 보냄 — 전에 받은 사진 그대로) ·
        'none'(네이버 딜인데 사진 없음) · 'unused'(사진이 왔지만 네이버 상품 딜이 아님 — 받지 않음) · None(사진과 상관없는 딜).
        이유가 있으면 링크도 붙이지 않는다. sent: 주인이 사진을 같이 보냈는지."""
        p = item.deal.product
        if not wants_owner_photo(p.shop, p):
            return ("unused" if sent else None), ""
        if jpeg is None:
            return ("kept" if p.extra.get("owner_photo") else "none"), ""
        folder = self.settings.data_dir / self.OWNER_PHOTO_DIR
        name = f"q{item.id}_{time.time_ns()}.jpg"
        try:
            from PIL import Image

            with Image.open(io.BytesIO(jpeg)) as im:
                w, h = im.size
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(jpeg)
        except OSError as e:
            log.warning("owner photo not saved for #%d: %s", item.id, e)
            return None, "사진을 저장하지 못했어요 (서버 디스크) — 잠시 뒤 다시 보내 주세요"
        old = p.extra.get("owner_photo")
        info = {"file": name, "at": to_iso(utcnow()), "w": w, "h": h}
        p.extra["owner_photo"] = info
        # 링크(→ pending)보다 먼저 사진을 적는다: 발행 루프가 링크만 붙은 글을 사진 없이 집어 가지 않게 (사이에 await 없음)
        self.db.update_queue_item(item.id, status=item.status, error=item.last_error, deal=item.deal)
        # 같은 상품이 또 나와 그때 만든 링크(mylink)를 다시 쓰면 이 사진도 같이 (_reuse_owner_photo, 남아 있는 동안만)
        self.db.kv_set(f"{self.OWNER_PHOTO_KV}{p.product_id}", json.dumps(info, ensure_ascii=False))
        if isinstance(old, dict) and old.get("file") and old.get("file") != name and not old.get("reused"):
            with contextlib.suppress(OSError):
                (folder / Path(str(old["file"])).name).unlink()  # 사진을 바꿔 다시 보냄: 예전 사진은 지움
        log.info("owner photo saved for #%d (%dx%d)", item.id, w, h)
        return "ok", ""

    def _reuse_owner_photo(self, p: Product) -> None:
        """예전에 주인이 만든 링크(mylink)를 다시 쓰는 같은 상품: 그때 링크와 같이 보낸 상품 사진도 쓴다 (검토 제안 — 사진 파일이
        남아 있는 동안만, 14일). 이번 딜을 주인이 본 건 아니라 가격·옵션 확인으로는 치지 않는다 (owner_link 아님 → 예전 대조 그대로)."""
        if p.extra.get("owner_photo") or not wants_owner_photo(p.shop, p):
            return
        try:
            info = json.loads(self.db.kv_get(f"{self.OWNER_PHOTO_KV}{p.product_id}") or "null")
        except ValueError:
            return
        if not isinstance(info, dict) or not info.get("file"):
            return
        if not (self.settings.data_dir / self.OWNER_PHOTO_DIR / Path(str(info["file"])).name).is_file():
            return  # 정리된 사진 (14일 지남)
        p.extra["owner_photo"] = {**info, "reused": True}
        log.info("reusing the owner's photo for %s with the saved link", p.product_id)

    def _owner_photo(self, p: Product) -> bytes | None:
        """주인이 링크와 같이 보낸 이 딜의 상품 사진 (저장할 때 검사했지만 한 번 더 같은 규칙으로 — owner_photo: 줄인 뒤에도
        짧은 변 600 을 지키는 규칙이라 clean_image 로 다시 보면 길쭉한 사진이 떨어짐). 없거나 못 읽으면 None."""
        info = p.extra.get("owner_photo")
        if not isinstance(info, dict) or not info.get("file"):
            return None
        path = self.settings.data_dir / self.OWNER_PHOTO_DIR / Path(str(info["file"])).name  # 이름만 (경로를 믿지 않음)
        try:
            data = path.read_bytes()
        except OSError:
            log.warning("owner photo missing for %s: %s", p.product_id, path.name)
            return None
        jpeg, why = owner_photo(data, min_side=self.settings.publish.photo_min_side)
        if jpeg is None:
            log.warning("owner photo unusable for %s: %s", p.product_id, why)
        return jpeg

    def _public_owner_photo(self, photo: bytes) -> str | None:
        """주인 사진의 공개 사본 주소 (/media/owner_….jpg). 캡션이 1024자를 넘어 텔레그램 글로 보낼 때 그 사진을 큰 미리보기로 띄우고
        (주소가 없으면 미리보기를 꺼서 채널에 사진이 빠졌다 — 검토 지적), 스레드도 같은 확인된 사진을 쓴다.
        발행할 때만 만든다 (받을 때는 공개 폴더 밖 owner_photos 에만). 공개 도메인이 없거나 못 쓰면 None."""
        base = self.settings.secrets.public_base_url
        if not base:
            return None
        folder = self.settings.data_dir / "media"
        name = f"{self.OWNER_PUBLIC_PREFIX}{hashlib.sha256(photo).hexdigest()[:24]}.jpg"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not (folder / name).exists():
                (folder / name).write_bytes(photo)
        except OSError as e:
            log.warning("owner photo public copy failed: %s", e)
            return None
        return f"{base}/media/{name}"

    def _prune_owner_photos(self) -> int:
        cutoff = time.time() - self.OWNER_PHOTO_KEEP_DAYS * 86400
        n = 0
        folders = ((self.settings.data_dir / self.OWNER_PHOTO_DIR, "*.jpg"),
                   (self.settings.data_dir / "media", f"{self.OWNER_PUBLIC_PREFIX}*.jpg"))
        for folder, pattern in folders:
            for f in folder.glob(pattern) if folder.is_dir() else []:
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        n += 1
                except OSError:
                    pass
        return n

    async def deal_photo(self, deal: Deal) -> bytes | None:
        """올릴 상품 사진: 디코딩되고 짧은 변 photo_min_side(600px) 이상인 JPEG (EXIF 방향 반영). 없으면 None → 사진 없이 올린다.
        - 주인이 링크와 같이 보낸 사진이 있으면 그것 (네이버 딜, 주인 결정 10/15 ① — 페이지·크롬을 열지 않음).
        - 주인 사진이 없으면 모든 몰(네이버 포함 — 주인 10/24 '상품 링크에 들어가면 사진이 무조건 잘 나와 있을 텐데, 직접 넣어'):
          딜의 상품 사진(쿠팡·네이버 CDN 은 큰 주소부터) → 상품 페이지 사진(JSON-LD → og:image, 최종 주소가 상품 페이지일 때만).
          네이버 딜은 수집 때 가격을 확인 못 했으면 가격도 한 번 본다 (_read_unverified_price).
        사진을 찾으러 검색하지 않는다 (네이버 쇼핑 검색 API 는 2026-07-31 종료). 게시판 목록·글 사진(뽐뿌 등)·로고·기본 배너는
        상품 사진으로 쓰지 않는다. 글자 카드도 상품 사진이 아니라 여기서 만들지 않는다 (카드는 인스타만).
        쓴 사진 주소를 p.image_url 에 남겨 스레드가 확인된 같은 사진을 쓰게 하고(주인 사진은 공개 사본 주소), 못 찾았으면 비운다."""
        cc = self.settings.publish
        p = deal.product
        if p.shop == "naver":
            # 가격 확인 (크롬으로 읽었으면 그 페이지를 아래 사진에도 — 주인 10/25). 주인 사진보다 먼저 — 예전 링크·사진을 다시 쓰는 딜은 주인이 가격을 본 게 아니라서
            # 발행 전 가격 확인이 이것뿐이다 (주인이 이번에 확인한 딜은 _read_unverified_price 가 아무것도 안 함)
            browser_meta = await self._read_unverified_price(p)
        else:
            browser_meta = None
        owned = self._owner_photo(p)
        if owned is not None:
            p.image_url = self._public_owner_photo(owned)  # 공개 도메인이 없으면 None (텔레그램은 바이트로, 스레드는 글만)
            return owned
        referer = p.url if p.url and p.url.startswith("http") else None
        tried: set[str] = set()

        async def _try(url: str | None) -> bytes | None:
            if not url or url in tried or is_board_thumb(url) or is_generic_image(url):
                return None
            tried.add(url)
            return clean_image(await self._fetch_image(url, referer=referer), min_side=cc.photo_min_side)

        def _sizes(url: str | None) -> list[str | None]:
            # 쿠팡·네이버 CDN 축소본이면 큰 주소부터, 그다음 원래 주소
            return [u for u in (coupang_image_url(url), naver_image_url(url)) if u != url] + [url]

        photo: bytes | None = None
        src: str | None = None
        for url in _sizes(p.image_url):
            photo = await _try(url)
            if photo is not None:
                src = url
                break
        if photo is None and p.url and not is_board_thumb(p.url) and p.shop not in self.settings.deal.enrich.exclude_shops:
            try:
                meta = await self.enricher.fetch(p.url)
            except Exception:  # noqa: BLE001
                meta = None
            self._recheck_page_price(p, meta)
            for url in [u for page_url in (page_images(meta) if meta else []) for u in _sizes(page_url)]:
                photo = await _try(url)
                if photo is not None:
                    src = url
                    break
            if photo is None and is_naver_store(p.url):
                # 일반 요청이 막힌(429) 네이버 스토어 — 가격 확인에 쓰는 그 크롬(위장 없는 빈 창 · 여유 메모리·간격·막히면 쉼은 store_browser)으로
                # 상품 사진도 (주인 10/24 '직접 넣어' → 10/25 #118 '사진 필요'가 주인에게 감 · 주인 10/25 '응'). 가격 때문에 읽은 페이지면 다시 안 엶
                bmeta = browser_meta
                if bmeta is None:
                    try:
                        bmeta = await self.store_browser.page_meta(p.url)
                    except Exception:  # noqa: BLE001
                        bmeta = None
                for url in [u for page_url in (page_images(bmeta) if bmeta else []) for u in _sizes(page_url)]:
                    photo = await _try(url)
                    if photo is not None:
                        src = url
                        break
        if photo is None and p.image_url:
            log.info("no usable product photo — posting without photo: %s", p.image_url[:100])
        p.image_url = src
        return photo

    async def make_card(self, deal: Deal, photo: bytes | None, *, deal_no: int | None = None, text_card: bool = False,
                        cta: str | None = None) -> str | None:
        """딜 카드 이미지를 만들어 공개 URL 을 돌려준다. 카드가 꺼져 있거나 공개 도메인이 없으면 None.
        photo: deal_photo 가 확인한 상품 사진 바이트 (텔레그램 글에 쓴 그 사진). 여기서 사진을 다시 받지 않는다 —
        예전엔 image_url 을 날것으로 다시 받아 쿠팡 로고가 카드에 크게 들어가거나(/instatest), 외부 반출을 막는 스토어 사진이 빠졌다.
        사진이 없으면 instagram.no_photo 가 text_card(기본, 주인 결정 10/15)이거나 네이버 딜일 때 글자 카드, 아니면(skip) None.
        text_card=True: 사진이 없으면 언제나 글자 카드 (스레드 — 확인된 사진이 없는 딜도 글만 올리지 않게, 주인 결정 10/15).
        cta: 바닥 안내 (스레드는 NEUTRAL_CTA — 본문에 구매 링크가 있어 '프로필 링크' 안내는 인스타에서만). None 이면 인스타용."""
        if self.card is None:
            return None
        base = self.settings.secrets.public_base_url
        if not base:
            log.debug("no public domain — card image cannot be served")
            return None
        if photo is None and not (text_card or self._ig_text_card(deal)):
            return None
        shop = self.registry.get(deal.product.shop)
        tier, _ = self.renderer.deal_tier(deal)  # 배지는 글 첫 줄과 같은 등급으로
        info = None
        if self.card.layout != "current":
            from dealbot.publisher.insta_style import card_info

            info = card_info(deal, self.renderer, shop=shop, deal_no=deal_no)
        try:
            path = await asyncio.to_thread(
                self.card.save, deal, photo, shop_name=shop.name if shop else None, tier=tier, info=info, cta=cta,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("card render failed: %s", e)
            return None
        try:
            self.card.prune(self.settings.card.keep_days)
        except Exception:  # noqa: BLE001
            pass
        return f"{base}/media/{path.name}"

    def _ig_text_card(self, deal: Deal | None) -> bool:
        """확인된 상품 사진이 없는 딜을 글자 카드(상품명·가격 같은 사실만, 브랜드 글꼴 — 로고·퍼 온 사진 없음)로 올리나:
        no_photo 가 text_card 이거나, 네이버 딜 (주인 결정 10/15 — 네이버 딜은 주인이 사진을 같이 보내지 않으면 사진이 없다)."""
        if self.settings.instagram.no_photo == "text_card":
            return True
        return deal is not None and deal.product.shop == "naver"

    def _ig_skip_reason(self, photo: bytes | None, *, deal: Deal | None = None) -> str | None:
        """인스타에 안 올리는 이유 (사진 규칙). 확인된 상품 사진이 없고 글자 카드로 올릴 딜도 아니면(no_photo: skip) 올리지 않는다
        — 인스타는 사진이 꼭 있어야 하고, 로고·기본 이미지를 크게 띄운 카드는 계정 인상을 해친다."""
        if photo is None and not self._ig_text_card(deal):
            return "확인된 상품 사진 없음"
        return None

    async def channel_deal(self, deal: Deal, channel: str) -> Deal:
        """그 채널에 올릴 딜 — 쿠팡 딜이면 채널 아이디(links.coupang_sub_ids[channel])로 만든 링크를 단 사본 (주인 10/23 — 쿠팡 실적을
        채널별로). 쿠팡이 아니거나, 그 채널 아이디가 없거나, 딥링크가 안 되면 그대로 (기본 링크). 만든 링크는 상품 extra 에 기억."""
        sub = (self.settings.links.coupang_sub_ids or {}).get(channel)
        if not sub or deal.product.shop != "coupang" or not deal.affiliate_url or self.state.dry_run:
            return deal
        made = deal.product.extra.setdefault("sub_links", {}) if isinstance(deal.product.extra, dict) else {}
        link = made.get(channel) if isinstance(made, dict) else None
        if not link:
            provider = self.links.providers.get("coupang")
            if provider is None:
                return deal
            try:
                link = await provider.convert(deal.product.url, sub_id=sub)
            except Exception as e:  # noqa: BLE001 — 채널 링크가 안 돼도 기본 링크로 올린다
                log.info("coupang %s link (%s) failed — default link: %s", channel, sub, e)
                return deal
            made[channel] = link
        return replace(deal, affiliate_url=link)

    async def _publish_side_channels(self, deal: Deal, *, photo: bytes | None = None, deal_no: int | None = None) -> None:
        """텔레그램 채널 발행 후: 스레드 대기열 + 인스타 대기열에 넣기 + 복붙 문구를 관리자 챗으로.
        스레드와 인스타는 대기열·리듬이 따로다 (주인 결정 10/15):
        - 스레드: threads_jobs 대기열 (threads_flow) — 07~24시·45분 간격·하루 딜 10개, 리듬이 허락하면 바로.
          주인 10/18: 할인 괜찮은 딜(publisher/good_deal)은 먼저, 15~30분 간격·하루 딜 20개까지.
        - 인스타: ig_queue 대기열 (InstagramScheduler) — 바로 올리지 않고 인스타 시간표(instagram_tick)가 올린다. 주인 10/18:
          딜 하나짜리 글은 할인 괜찮은 딜만, 하루 10개까지 점수 순으로 07~23시·30~50분 간격으로, 올리기 직전 품절·가격을 다시 보고.
          보통 딜은 점심 릴스·저녁 TOP5 에만 (화금 단가표도 같은 시간표).
        photo: 텔레그램 글에 쓴 확인된 상품 사진(deal_photo) — 인스타 카드도 이 사진만 쓴다 (사본을 대기열에 남김). deal_no: 대기열 번호."""
        queue_id = deal_no  # 대기열 번호 그대로
        want_ig = (
            self.settings.instagram.enabled
            and (self.instagram.configured or self.instagram.dry_run)
            and (deal.product.deal_kind != "info" or self.settings.instagram.info_posts)
        )
        want_threads = self.settings.threads.enabled and (self.threads.configured or self.threads.dry_run)
        ig_skip = self._ig_skip_reason(photo, deal=deal) if want_ig else None
        if ig_skip:
            # 사진 없는 딜은 인스타에 안 올림 (no_photo: skip 일 때만 — 실패가 아니라 규칙이라 관리자 알림 없이 기록만)
            want_ig = False
            self.db.log_event("INFO", "instagram", f"{deal.product.product_id} skipped: {ig_skip}")
            self.db.sns_log_add("instagram", "skipped", product_id=deal.product.product_id, reason=ig_skip)
            log.info("instagram skipped (%s): %s", ig_skip, deal.product.name[:40])
        if want_threads:
            # 스레드 대기열에 넣고 리듬(07~24시·45분 간격·하루 딜 10개)이 허락하면 바로 (주인 결정 10/15).
            # 사진: deal_photo 가 확인한 상품 사진, 없으면 봇이 만든 4:5 글자 카드 (글만 올리지 않음)
            try:
                job = self.threads_flow.enqueue_deal(await self.channel_deal(deal, "threads"), queue_id=queue_id,
                                                     has_photo=photo is not None)
                if job is not None and not self.settings.threads.rhythm.enabled:
                    await self.threads_flow.post_now(job)
                elif job is not None:
                    await self.threads_flow.tick()
            except Exception as e:  # noqa: BLE001 — 스레드 쪽 문제로 인스타·복붙 문구가 빠지지 않게
                log.warning("threads queue failed: %s", e)
                self.db.log_event("WARNING", "threads", f"{deal.product.product_id}: {type(e).__name__}: {e}")
        if want_ig:
            try:
                self.ig.enqueue(await self.channel_deal(deal, "instagram"), photo=photo, queue_id=queue_id)
            except Exception as e:  # noqa: BLE001 — 대기열에 못 넣어도 채널 글·복붙 문구는 그대로
                log.warning("instagram queue failed: %s", e)
                self.db.log_event("WARNING", "instagram", f"{deal.product.product_id}: queue failed: {e}")
        for block in self.copy_blocks.build(deal):
            await self.notifier.send(block.as_telegram_html(), silent=True)

    async def instagram_tick(self) -> str | None:
        """인스타 시간표 한 번 보기 (scheduler.instagram_loop 가 1분마다): 차례가 된 글 하나를 올리거나 건너뛴다."""
        return await self.ig.tick()

    async def _sns_failed(self, sns: str, cmd: str, tg_text: str, error: str) -> None:
        """스레드·인스타 발행 실패: 연결(토큰·권한)이 끊긴 것이면 주인 카톡 (다시 연결해야 풀림), 아니면 텔레그램만 (소리 없이)."""
        if _AUTH_RE.search(error):
            # 승인 로그인 화면이 맴돌 때는 토큰으로 연결 (/threadstoken·/instatoken, 주인 10/12)
            token_cmd = cmd.replace("auth", "token")
            await self.notifier.alert(
                "sns_auth", tg_text, kakao_fit(f"⚠️[할 일] {sns} 연결이 끊겼어요", f"→ 텔레그램 핫딜봇 /{token_cmd} 토큰 (또는 /{cmd})"),
                key=f"sns:{cmd}", short=f"{sns} 다시 연결", ttl_h=24,
            )
        else:
            if self.router is not None:  # 주인 10/18: 연결이 끊긴 게 아닌 실패는 정리의 '소식' (이어지면 sns_zero 가 따로)
                hour = local_now(self.settings.app.timezone).strftime("%m%d%H")
                self.notifier.owner_line({"kind": "sns_failed", "key": f"sns_fail:{cmd}:{hour}", "urgent": False,
                                          "text": f"{sns} 발행 실패", "short": f"{sns} 발행 실패", "ttl_h": 24})
            await self.notifier.send(tg_text)

    async def threads_auth_url(self) -> str:
        if not self.settings.secrets.has_threads_app:
            return "THREADS_APP_ID / THREADS_APP_SECRET 이 설정되어 있지 않습니다."
        token = self.threads.stored_token()
        lead = ""
        if token is not None:
            try:
                me = await self.threads.client.me(token.access_token)
                # 이미 연결돼 있어도 링크는 준다 — 새 권한(통계·답글)을 받으려면 다시 승인해야 함 (주인 10/15: '이미 연결'만 와서 못 함)
                if all(self.threads.scopes.status(s) == GRANTED for s in (THREADS_INSIGHTS, THREADS_READ_REPLIES)):
                    # 지금 토큰이 새 권한까지 이미 갖고 있으면 다시 승인할 일이 없다 (주인 10/16: 재승인이 'API access blocked' 로만 끝남)
                    return (f"✅ 스레드는 이미 연결되어 있고(@{me.get('username')}) 통계·답글 권한도 다 받아져 있어요. "
                            "다시 승인할 필요 없어요.")
                lead = (f"이미 연결되어 있어요: @{me.get('username')}. 통계·답글 권한을 받으려면 아래 링크로 한 번 더 승인해 주세요 "
                        "(승인해도 지금 연결은 끊기지 않아요).\n\n")
            except ThreadsError:
                pass
        sec = self.settings.secrets
        app_id = sec.threads_app_id or ""
        redirect = sec.threads_redirect_uri
        state = pysecrets.token_urlsafe(12)
        self.db.kv_set(KV_OAUTH_STATE, state)
        self.db.kv_set(KV_OAUTH_STATE_AT, to_iso(utcnow()))
        url = authorize_url(app_id, redirect, state=state)
        basic_url = authorize_url(app_id, redirect, state=state, scopes=THREADS_BASIC_SCOPES)
        register = (
            f"1) 메타 앱에 이 콜백 주소가 등록돼 있어야 합니다: <code>{redirect}</code>\n"
            "   developers.facebook.com → 앱 → 사용 사례 → Threads API → 설정 → <b>리디렉션 콜백 URL</b>. "
            "입력한 뒤 아래에 뜨는 제안을 눌러야 등록되고, <b>설치 제거·삭제 콜백 URL</b>도 같은 주소로 채워야 저장됩니다. "
            "메타는 localhost 주소를 받지 않습니다.\n"
        )
        check = (
            f"\n확인: 이 링크의 client_id <code>{app_id}</code> 는 앱 설정 → 기본 설정 아래쪽의 <b>Threads 앱 ID</b>와 같아야 합니다 "
            "(같은 페이지 맨 위 '앱 ID'는 메타 앱용이라 다른 번호입니다). "
            "'차단된 URL / 리디렉션 URI가 화이트리스트에 없습니다' 가 뜨면 ① 이 번호가 Threads 앱 ID가 아니거나 ② 1)의 주소가 글자 그대로 등록되지 않은 경우입니다.\n"
            "통계·답글 읽기 권한(threads_manage_insights, threads_read_replies)도 요청해요 — 'Invalid Scopes' 가 뜨면 "
            "앱 → 사용 사례 → Threads API → 권한에서 두 권한을 먼저 추가해 주세요 (docs/SETUP_KEYS.md 7-7).\n"
            "지금 바로 권한을 추가하기 어려우면 이 링크(올리기·답글 달기만 — 통계·답글 모으기는 꺼진 채로 연결)로 연결하세요:\n"
            f"{basic_url}"
        )
        if sec.threads_callback_served:
            return (
                lead + "<b>스레드 연결 (자동)</b>\n"
                + register
                + "2) 아래 링크를 <b>길게 눌러 복사</b>한 뒤 브라우저 주소창에 붙여넣어 열고 승인을 누르세요. 그냥 누르면 스레드 앱이 열려 승인 화면이 안 뜹니다.\n"
                f"{url}\n"
                "승인하면 봇이 바로 연결하고 결과를 이 챗에 보냅니다. 코드를 복사할 필요가 없습니다.\n"
                + check
            )
        return (
            lead + "<b>스레드 연결 (수동)</b>\n"
            + register
            + "   Railway 에서 서비스 도메인을 만들면(Settings → Networking → Generate Domain, 포트 8080) 봇이 콜백을 직접 받아 승인만으로 연결됩니다. docs/SETUP_KEYS.md 7-1 참고.\n"
            "2) 아래 링크를 <b>길게 눌러 복사</b>한 뒤 브라우저 주소창에 붙여넣어 열고 승인을 누르세요.\n"
            f"{url}\n"
            "3) 승인 후 이동한 주소창에서 <code>code=</code> 뒤의 값을 복사해 <code>/threadscode 붙여넣기</code> 로 보내주세요. "
            "(주소가 열리지 않아도 됩니다. <code>error_code=</code> 가 보이면 오류 페이지라 코드가 아닙니다.)\n"
            + check
        )

    def _oauth_state_valid(self, state: str | None) -> bool:
        expected = self.db.kv_get(KV_OAUTH_STATE)
        if not state or not expected or state != expected:
            return False
        issued = from_iso(self.db.kv_get(KV_OAUTH_STATE_AT))
        return issued is not None and utcnow() - issued <= timedelta(hours=1)

    async def _threads_callback(self, query: dict[str, str]) -> tuple[int, str]:
        """스레드 OAuth 리디렉션을 봇이 직접 받는다: 승인 → code 교환 → 결과를 관리자 챗으로."""
        err = query.get("error") or query.get("error_reason")
        if err:
            desc = query.get("error_description") or err
            await self._notify_quiet(f"❌ 스레드 승인이 거부되었습니다: {html.escape(desc)}")
            return 400, page("스레드 연결 실패", html.escape(desc))
        code = query.get("code")
        if not code:
            return 400, page("스레드 연결", "code 가 없습니다. 텔레그램에서 /threadsauth 링크를 다시 열어 주세요.")
        if not self._oauth_state_valid(query.get("state")):
            return 400, page(
                "스레드 연결 실패",
                "요청 확인값(state)이 맞지 않거나 오래되었습니다. 텔레그램에서 /threadsauth 로 새 링크를 받아 다시 시도해 주세요.",
            )
        self.db.kv_set(KV_OAUTH_STATE, "")  # 한 번 쓴 state 는 버린다
        msg = await self.threads_submit_code(code)
        await self._notify_quiet(msg)
        ok = msg.startswith("✅")
        return 200 if ok else 400, page(
            "스레드 연결 완료" if ok else "스레드 연결 실패",
            html.escape(msg) + "<br>이 창을 닫고 텔레그램 관리자 챗을 확인하세요.",
        )

    async def _notify_quiet(self, text: str) -> None:
        # 주인 10/18: 연결 결과는 대시보드 '연결' 에 (다음에 화면을 열 때 한 번 '스레드 연결됐어요'). 글자는 남기지 않고 결과만
        plain = re.sub(r"<[^>]+>", "", text or "")
        ok = plain.lstrip().startswith("✅")
        who = "인스타" if "인스타" in plain else "스레드" if "스레드" in plain else "SNS"
        self.db.kv_set("dash:oauth_note", json.dumps({"at": time.time(), "ok": ok,
                                                     "text": f"{who} 연결됐어요" if ok else f"{who} 연결이 안 됐어요 — 다시 연결해 주세요"},
                                                    ensure_ascii=False))
        try:
            await self.notifier.send(text, silent=True)
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", e)

    # ----------------------------------------------------------- instagram
    async def threads_auth_link(self) -> str | None:
        """대시보드 '다시 연결' (스레드): 승인 화면 주소 하나 (state 를 새로 만들어 둠 — 콜백이 끝냄). 앱 설정이 없으면 None.
        주소는 302 Location 으로만 주고 어디에도 남기지 않는다."""
        sec = self.settings.secrets
        if not sec.has_threads_app:
            return None
        state = pysecrets.token_urlsafe(12)
        self.db.kv_set(KV_OAUTH_STATE, state)
        self.db.kv_set(KV_OAUTH_STATE_AT, to_iso(utcnow()))
        return authorize_url(sec.threads_app_id or "", sec.threads_redirect_uri, state=state)

    async def instagram_auth_link(self) -> str | None:
        """대시보드 '다시 연결' (인스타): 승인 화면 주소 하나. 앱 설정이 없으면 None."""
        from dealbot.publisher.instagram import authorize_url as ig_authorize_url

        sec = self.settings.secrets
        if not sec.has_instagram_app:
            return None
        state = pysecrets.token_urlsafe(12)
        self.db.kv_set(KV_IG_OAUTH_STATE, state)
        self.db.kv_set(KV_IG_OAUTH_STATE_AT, to_iso(utcnow()))
        return ig_authorize_url(sec.instagram_app_id or "", sec.instagram_redirect_uri, state=state)

    async def instagram_auth_url(self) -> str:
        sec = self.settings.secrets
        if not sec.has_instagram_app:
            return "INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET 이 설정되어 있지 않습니다. (메타 앱 → 인스타그램 → 비즈니스 로그인 설정의 '인스타그램 앱 ID/시크릿')"
        token = self.instagram.stored_token()
        lead = ""
        if token is not None:
            try:
                me = await self.instagram.client.me(token.access_token)
                # 이미 연결돼 있어도 링크는 준다 — 새 권한(통계)을 받으려면 다시 승인해야 함 (주인 10/15)
                if self.instagram.scopes.status(IG_INSIGHTS) == GRANTED:
                    return (f"✅ 인스타그램은 이미 연결되어 있고(@{me.get('username')}) 통계 권한도 받아져 있어요. "
                            "다시 승인할 필요 없어요.")
                lead = (f"이미 연결되어 있어요: @{me.get('username')}. 통계 권한을 받으려면 아래 링크로 한 번 더 승인해 주세요 "
                        "(승인해도 지금 연결은 끊기지 않아요).\n\n")
            except InstagramError:
                pass
        from dealbot.publisher.instagram import BASIC_SCOPES as IG_BASIC_SCOPES
        from dealbot.publisher.instagram import authorize_url as ig_authorize_url

        app_id = sec.instagram_app_id or ""
        redirect = sec.instagram_redirect_uri
        state = pysecrets.token_urlsafe(12)
        self.db.kv_set(KV_IG_OAUTH_STATE, state)
        self.db.kv_set(KV_IG_OAUTH_STATE_AT, to_iso(utcnow()))
        url = ig_authorize_url(app_id, redirect, state=state)
        basic_url = ig_authorize_url(app_id, redirect, state=state, scopes=IG_BASIC_SCOPES)
        register = (
            f"1) 메타 앱에 이 주소가 등록돼 있어야 합니다: <code>{redirect}</code>\n"
            "   developers.facebook.com → 앱 → 인스타그램 → API 설정(Instagram 로그인) → 3. 비즈니스 로그인 설정 → <b>OAuth 리디렉션 URI</b>. "
            "입력 후 제안을 눌러 확정하고 저장. 메타는 localhost 를 받지 않습니다.\n"
        )
        check = (
            f"\n확인: 이 링크의 client_id <code>{app_id}</code> 는 같은 화면의 <b>인스타그램 앱 ID</b>와 같아야 합니다 "
            "(앱 설정 → 기본 설정 맨 위의 '앱 ID'와는 다른 번호). 인스타 계정은 프로페셔널(크리에이터/비즈니스)이어야 하고, "
            "앱이 개발 모드면 앱 역할 → Instagram 테스터에 내 계정을 넣고 인스타 앱 → 설정 → 웹사이트 권한 → 앱 및 웹사이트 → 테스터 초대에서 수락해야 합니다.\n"
            "통계 권한(instagram_business_manage_insights)도 요청해요 — 'Invalid Scopes' 가 뜨면 앱 → 사용 사례 → "
            "Instagram 권한에서 먼저 추가해 주세요 (docs/SETUP_KEYS.md 7-7).\n"
            "지금 바로 권한을 추가하기 어려우면 이 링크(올리기만 — 통계는 꺼진 채로 연결)로 연결하세요:\n"
            f"{basic_url}"
        )
        if sec.instagram_callback_served:
            return (
                lead + "<b>인스타그램 연결 (자동)</b>\n"
                + register
                + "2) 아래 링크를 <b>길게 눌러 복사</b>한 뒤 브라우저 주소창에 붙여넣어 열고 승인을 누르세요.\n"
                f"{url}\n"
                "승인하면 봇이 바로 연결하고 결과를 이 챗에 보냅니다.\n"
                + check
            )
        return (
            lead + "<b>인스타그램 연결 (수동)</b>\n"
            + register
            + "2) 아래 링크를 <b>길게 눌러 복사</b>한 뒤 브라우저 주소창에 붙여넣어 열고 승인을 누르세요.\n"
            f"{url}\n"
            "3) 이동한 주소창에서 <code>code=</code> 뒤의 값을 복사해 <code>/instacode 붙여넣기</code> 로 보내주세요.\n"
            + check
        )

    def _ig_oauth_state_valid(self, state: str | None) -> bool:
        expected = self.db.kv_get(KV_IG_OAUTH_STATE)
        if not state or not expected or state != expected:
            return False
        issued = from_iso(self.db.kv_get(KV_IG_OAUTH_STATE_AT))
        return issued is not None and utcnow() - issued <= timedelta(hours=1)

    async def _instagram_callback(self, query: dict[str, str]) -> tuple[int, str]:
        err = query.get("error") or query.get("error_reason")
        if err:
            desc = query.get("error_description") or err
            await self._notify_quiet(f"❌ 인스타그램 승인이 거부되었습니다: {html.escape(desc)}")
            return 400, page("인스타그램 연결 실패", html.escape(desc))
        code = query.get("code")
        if not code:
            return 400, page("인스타그램 연결", "code 가 없습니다. 텔레그램에서 /instaauth 링크를 다시 열어 주세요.")
        if not self._ig_oauth_state_valid(query.get("state")):
            return 400, page(
                "인스타그램 연결 실패",
                "요청 확인값(state)이 맞지 않거나 오래되었습니다. 텔레그램에서 /instaauth 로 새 링크를 받아 다시 시도해 주세요.",
            )
        self.db.kv_set(KV_IG_OAUTH_STATE, "")
        msg = await self.instagram_submit_code(code)
        await self._notify_quiet(msg)
        ok = msg.startswith("✅")
        return 200 if ok else 400, page(
            "인스타그램 연결 완료" if ok else "인스타그램 연결 실패",
            html.escape(msg) + "<br>이 창을 닫고 텔레그램 관리자 챗을 확인하세요.",
        )

    async def instagram_submit_code(self, code: str) -> str:
        if not self.settings.secrets.has_instagram_app:
            return "INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET 이 필요합니다."
        try:
            token = await self.instagram.client.exchange_code(code, self.settings.secrets.instagram_redirect_uri)
            me = await self.instagram.client.me(token.access_token)
            username = str(me.get("username") or "")
            self.instagram.save_token(token, username)
            self.notifier.resolve("sns:instaauth", "sns_fail:instaauth:")  # 다시 연결됨 — 끊김·실패 할 일은 닫는다
            expires = token.expires_at.date().isoformat() if token.expires_at else "-"
            self.db.log_event("INFO", "instagram", f"authorized as {username}")
            return f"✅ 인스타그램 연결 완료: @{username} (토큰 만료 {expires}, 자동 갱신됨)"
        except InstagramError as e:
            self.db.log_event("WARNING", "instagram", f"authorize failed: {e}")
            log.warning("instagram authorize failed: %s", e)
            msg = f"❌ 실패: {e}\n code 는 한 번만 쓸 수 있으니 /instaauth 로 다시 받아 주세요."
            if _auth_blocked(e):
                msg += _AUTH_BLOCKED_HINT.format(cmd="/instatoken", where="인스타그램 → API 설정 → 2. 액세스 토큰 생성")
            return msg

    async def instagram_submit_token(self, token: str) -> str:
        """/instatoken: 메타 개발자 화면에서 만든 토큰으로 연결 (승인 링크 로그인이 돌기만 할 때)."""
        try:
            tok = await self.instagram.client.from_user_token(token)
            me = await self.instagram.client.me(tok.access_token)
        except InstagramError as e:
            return f"❌ 연결하지 못했어요: {html.escape(str(e))}\n토큰을 다시 만들어 보내 주세요 (테스터로 수락한 프로페셔널 계정으로 만든 토큰이어야 해요)."
        username = str(me.get("username") or "")
        self.instagram.save_token(tok, username)
        self.notifier.resolve("sns:instaauth", "sns_fail:instaauth:")  # 다시 연결됨 — 끊김·실패 할 일은 닫는다
        self.db.log_event("INFO", "instagram", f"authorized by user token as {username}")
        expires = tok.expires_at.date().isoformat() if tok.expires_at else "-"
        return f"✅ 인스타그램 연결 완료: @{html.escape(username)} (토큰 만료 {expires}, 자동 갱신됨). 이제 /instatest 로 시험해 보세요."

    async def instagram_test(self, queue_id: int | None = None) -> str:
        """인스타그램에 실제로 올려 본다. 번호 없으면 샘플 딜, 있으면 채널에 올렸던 그 글."""
        from dealbot.cli import sample_deal

        if not self.settings.instagram.enabled:
            return "인스타그램 자동 게시가 꺼져 있습니다 (config instagram.enabled)."
        if self.instagram.stored_token() is None:
            return "인스타그램이 연결되어 있지 않습니다. /instaauth 먼저 해 주세요."
        if queue_id is None:
            deal, label = sample_deal(), "샘플 딜"
        else:
            item = self.db.get_queue_item(queue_id)
            if item is None:
                return f"#{queue_id} 글이 없습니다."
            if item.status != "published":
                return f"#{queue_id} 는 채널에 올라간 글이 아닙니다 (상태 {item.status})."
            deal, label = item.deal, f"#{queue_id} {item.deal.product.name[:30]}"
        deal_no = self._page_deal_no(queue_id)  # 실제 발행과 같이: 핫딜 페이지에 있는 글만 '#번호'
        # 실제 발행과 같은 규칙: 텔레그램 글처럼 확인된 상품 사진만 (샘플의 쿠팡 로고는 사진으로 안 씀)
        photo = await self.deal_photo(deal)
        caption = self.instagram.render(deal, deal_no=deal_no)
        preview = f"📸 <b>인스타그램에는 이렇게 올라갑니다 ({html.escape(label)})</b>\n<pre>{html.escape(caption)}</pre>"
        skip = self._ig_skip_reason(photo, deal=deal)
        if skip:
            return preview + (
                f"\n\n⏭ 이 딜은 인스타에 올리지 않습니다: {skip} (로고·기본 이미지·작은 썸네일은 카드에 안 씀). 실제 딜도 같은 규칙입니다."
                + ("\n사진이 있는 실제 딜로 시험하려면 /instatest 번호" if queue_id is None else "")
            )
        card_url = await self.make_card(deal, photo, deal_no=deal_no)
        preview += f"\n사진(카드): {card_url}" if card_url else "\n⚠️ 카드 이미지를 만들 수 없습니다 (공개 도메인이 없거나 card.enabled 가 꺼짐) — 인스타는 사진이 필수라 올릴 수 없습니다."
        if self.instagram.dry_run:
            return preview + "\n\n연습 모드라 실제로 올리지는 않았습니다."
        result = await self.instagram.publish(deal, card_url, deal_no=deal_no)
        if not result.ok:
            return preview + f"\n\n❌ 올리지 못했습니다: <code>{html.escape(result.error or '')}</code>"
        link = await self._instagram_permalink(result.message_id)
        lines = [preview, "", "✅ 인스타그램에 올렸습니다" + (f": {link}" if link else "")]
        if queue_id is None:
            lines.append("샘플 글이니 확인한 뒤 인스타그램 앱에서 지워 주세요.")
        return "\n".join(lines)

    # ----------------------------------------------------------- 핫딜 페이지 (인스타 프로필 링크용)
    def _page_items(self, now: Any | None = None) -> list[QueueItem]:
        """핫딜 페이지(/)에 보이는 딜. 정보 글·링크 없는 글은 먼저 뺀다 (자리를 차지하지 않게).
        인스타 후보 양식(A/B/C)은 카드·본문에 '#번호' 를 적으므로 기간 안의 딜을 모두 번호 순(큰 번호 = 최근)으로 — 점수로 자르면
        인스타 글의 번호가 페이지에 없을 수 있었다. 예전 양식(current)은 그대로 점수 순 위에서 page_max_items 개."""
        cfg = self.settings.web
        now = now or utcnow()
        # 끝 시각을 조금 뒤로: 방금(같은 초에) 올라간 글도 들어가게
        items = [
            it
            for it in self.db.published_items_between(
                now - timedelta(hours=cfg.page_hours), now + timedelta(minutes=1), include_dry_run=self.state.dry_run
            )
            if it.deal.product.deal_kind != "info" and (it.deal.affiliate_url or it.deal.product.url)
        ]
        if self.settings.instagram.style != "current":
            return sorted(items, key=lambda x: x.id, reverse=True)
        return sorted(items, key=lambda x: x.score, reverse=True)[: cfg.page_max_items]

    def _page_deal_no(self, item_id: int | None) -> int | None:
        """인스타 카드·본문에 적을 핫딜 페이지 번호: 그 글이 지금 페이지에 있을 때만 (페이지에서 찾을 수 없는 번호는 적지 않음)."""
        if item_id is None:
            return None
        return item_id if any(it.id == item_id for it in self._page_items()) else None

    def _past_items(self, now: Any | None = None) -> list[QueueItem]:
        """기간(page_hours) 안에 딜이 없을 때 보여 줄 최근 web.past_days 일의 딜 (새 것부터, page_max_items 개까지)."""
        cfg = self.settings.web
        now = now or utcnow()
        items = [
            it
            for it in self.db.published_items_between(
                now - timedelta(days=cfg.past_days), now + timedelta(minutes=1), include_dry_run=self.state.dry_run
            )
            if it.deal.product.deal_kind != "info" and (it.deal.affiliate_url or it.deal.product.url)
        ]
        return sorted(items, key=lambda x: x.id, reverse=True)[: cfg.page_max_items]

    async def _deals_page(self, query: dict[str, str]) -> tuple[int, str]:
        """'/' 핫딜 페이지 (인스타 프로필 링크용). 맨 위 광고 표시 한 줄 + 텔레그램 채널 버튼(초대 링크 deals_page 가 있으면 그것) +
        인스타·스레드 링크 (주인 결정 10/15). 기간 안에 딜이 없으면 최근 7일 딜을 '지난 딜 · 가격이 바뀌었을 수 있어요' 로."""
        cfg = self.settings.web
        rows = []
        disclosures: list[str] = list(self.settings.blog_digest.always_disclosures)
        items = self._page_items()
        past = False
        plain_links = False
        if not items:
            items = self._past_items()
            past = bool(items)
        for it in items:
            p = it.deal.product
            shop = self.registry.get(p.shop)
            disc = deal_disclosure(shop, p.extra)
            if disc and disc not in disclosures:
                disclosures.append(disc)
            plain_links = plain_links or not disc
            rows.append(
                {
                    "id": it.id,
                    "name": p.name,
                    "price": p.price if p.has_price else None,
                    "original_price": p.original_price if p.original_price and p.price and p.original_price > p.price else None,
                    "discount_rate": p.effective_discount_rate(),
                    "shop": shop.name if shop else p.shop,
                    "rocket": bool(p.is_rocket),
                    "free_shipping": bool(p.is_free_shipping),
                    "plain": not disc,  # 제휴 링크가 아닌 원본 주소 (수수료 없음) — 페이지에 '제휴 아님'
                }
            )
        programs = list(dict.fromkeys(p for d in disclosures if (p := disclosure_program(d))))
        channels = self.settings.channels.as_dict()
        html_text = self.renderer.render(
            "deals_page.j2",
            title=cfg.site_title,
            items=rows,
            hours=cfg.page_hours,
            disclosures=disclosures,
            channels=channels,
            # 인스타 후보 양식(A/B/C)은 카드·본문에 '#번호' 를 적으므로 페이지에도 번호를 보여 찾기 쉽게
            show_numbers=self.settings.instagram.style != "current",
            past=past,
            past_days=cfg.past_days,
            ad_top=(("광고 · 이 페이지의 구매 링크는 제휴 아님 표시가 없으면 제휴 링크라, 구매하면 " if plain_links
                     else "광고 · 이 페이지의 구매 링크는 제휴 링크라, 구매하면 ")
                    + ("·".join(programs) if programs else "판매처") + " 수수료를 받아요"),
            # 텔레그램 버튼: /invitelinks 로 만든 deals_page 초대 링크 (가입 경로를 셈), 없으면 채널 주소
            telegram_join=self.db.kv_get(f"{invites.KV_PREFIX}deals_page") or channels.get("telegram_url") or "",
            picks=self._pick_links(),
        )
        return 200, html_text

    def _pick_links(self, limit: int = 3) -> list[dict[str, str]]:
        """인스타에 올린 데런픽 글 (주인 10/25 — '인스타에 내 블로그 링크도'): 인스타 캡션 주소는 안 눌려서 프로필 링크 페이지 맨 위에
        최근 올린 글 limit 개 [{title, url}] (blog_share 가 올린 글만 — 네이버 블로그 주소만)."""
        from dealbot.growth import blog_share

        folder = blog_share.share_dir(self.settings.dashboard.blogauto_metrics_path)
        rows = []
        for p in folder.glob("*.json") if folder.is_dir() else []:
            rec = blog_share.load(p)
            done = str(self.db.kv_get(blog_share.done_key(rec)) or "") if rec else ""
            url = str((rec or {}).get("url") or "")
            if rec and done.startswith("posted") and url.startswith(("https://blog.naver.com/", "https://m.blog.naver.com/")):
                rows.append((p.stat().st_mtime, {"title": str(rec.get("title") or "데런픽 새 글")[:60], "url": url}))
        return [r for _, r in sorted(rows, key=lambda x: -x[0])[:limit]]

    async def _deal_redirect(self, query: dict[str, str]) -> tuple[int, str]:
        raw = (query.get("path") or "").strip("/")
        if not raw.isdigit():
            return 404, page("없는 딜", "주소가 올바르지 않습니다.")
        item = self.db.get_queue_item(int(raw))
        if item is None or item.status != "published":
            return 404, page("없는 딜", "이 딜은 더 이상 없습니다. 최신 딜은 첫 페이지에서 보세요.")
        target = item.deal.affiliate_url or item.deal.product.url
        if not target:
            return 404, page("없는 딜", "구매 링크가 없습니다.")
        return 302, target

    async def threads_submit_code(self, code: str) -> str:
        if not self.settings.secrets.has_threads_app:
            return "THREADS_APP_ID / THREADS_APP_SECRET 이 필요합니다."
        try:
            token = await self.threads.client.exchange_code(code, self.settings.secrets.threads_redirect_uri)
            self.threads.save_token(token)
            self.notifier.resolve("sns:threadsauth", "sns_fail:threadsauth:")  # 다시 연결됨 — 끊김·실패 할 일은 닫는다
            me = await self.threads.client.me(token.access_token)
            self.db.kv_set(KV_USERNAME, str(me.get("username") or ""))
            expires = token.expires_at.date().isoformat() if token.expires_at else "-"
            self.db.log_event("INFO", "threads", f"authorized as {me.get('username')}")
            return f"✅ 스레드 연결 완료: @{me.get('username')} (토큰 만료 {expires}, 자동 갱신됨)"
        except ThreadsError as e:
            self.db.log_event("WARNING", "threads", f"authorize failed: {e}")
            log.warning("threads authorize failed: %s", e)
            msg = f"❌ 실패: {e}\n code 는 한 번만 쓸 수 있으니 /threadsauth 로 다시 받아 주세요."
            if _auth_blocked(e):
                msg += _AUTH_BLOCKED_HINT.format(cmd="/threadstoken", where="사용 사례 → Threads API → 설정 → 사용자 토큰 생성기")
            return msg

    async def threads_submit_token(self, token: str) -> str:
        """/threadstoken: 메타 개발자 화면에서 만든 사용자 토큰으로 연결 (승인 링크 로그인이 돌기만 할 때)."""
        try:
            tok = await self.threads.client.from_user_token(token)
            me = await self.threads.client.me(tok.access_token)
        except ThreadsError as e:
            return f"❌ 연결하지 못했어요: {html.escape(str(e))}\n토큰을 다시 만들어 보내 주세요 (테스터로 수락한 계정으로 만든 토큰이어야 해요)."
        self.threads.save_token(tok)
        self.notifier.resolve("sns:threadsauth", "sns_fail:threadsauth:")  # 다시 연결됨 — 끊김·실패 할 일은 닫는다
        self.db.kv_set(KV_USERNAME, str(me.get("username") or ""))
        self.db.log_event("INFO", "threads", f"authorized by user token as {me.get('username')}")
        expires = tok.expires_at.date().isoformat() if tok.expires_at else "-"
        return f"✅ 스레드 연결 완료: @{html.escape(str(me.get('username') or ''))} (토큰 만료 {expires}, 자동 갱신됨). 이제 /threadstest 로 시험해 보세요."

    # ----------------------------------------------------------- 채널 성장 (주인 결정 10/15)
    async def _on_chat_member(self, update: Update, _ctx: Any) -> None:
        """채널 가입: 어느 초대 링크로 들어왔는지 링크 이름별로 센다 (공개 주소로 들어오면 '출처 모름'). 사람은 남기지 않는다."""
        try:
            name = invites.record_join(self.db, update, self.publisher.channel_id, self.settings.app.timezone, self.clock())
            if name:
                log.info("channel join via %s", name)
        except Exception as e:  # noqa: BLE001
            log.warning("chat member update not counted: %s", e)

    async def invite_links(self) -> str:
        """/invitelinks: 이름 붙은 채널 초대 링크 (ig_bio·threads_bio·deals_page·demiyum·qr) 를 만들거나 보여 준다.
        새로 만든 링크도 바로 Blog-Auto 에 넘긴다 (channel_links.json — 핫딜 블로그 서명의 demiyum 링크)."""
        text = await invites.create_links(self)
        self.export_channel_links()
        return text

    def export_channel_links(self) -> Path | None:
        """기억한 초대 링크를 데이터 폴더(published_deals.jsonl 옆)의 channel_links.json 에 (켜질 때·/invitelinks 뒤)."""
        try:
            return invites.export_links(self.db, self.settings.data_dir, self.settings.channels.telegram_url, self.clock())
        except Exception as e:  # noqa: BLE001 — 넘기기 실패가 봇을 막지 않게
            log.warning("channel links export failed: %s", type(e).__name__)
            return None

    def channel_guide_text(self) -> str:
        """채널 맨 위에 고정할 짧은 안내 (인스타·스레드·블로그 링크). 구매 링크가 없어 광고 표시는 없고, 채널 글의 제휴 링크 안내만."""
        ch = self.settings.channels
        base = self.settings.secrets.public_base_url
        rows = [
            ("인스타그램", ch.instagram_url),
            ("스레드", ch.threads_url),
            ("블로그", ch.blog_url),
            ("한눈에 보는 최근 딜", f"{base}/" if base and self.settings.web.public_page else ""),
            ("카카오 오픈채팅", ch.kakao_openchat_url),
        ]
        links = [f"· {name} 👉 <a href=\"{html.escape(url)}\">{html.escape(url.split('://', 1)[-1].rstrip('/'))}</a>"
                 for name, url in rows if url]
        return "\n".join([
            f"📌 <b>{html.escape(ch.telegram_name)} 채널 안내</b>",
            "핫딜은 이 텔레그램 채널에 가장 먼저, 모두 올라와요.",
            *links,
            "",
            "<i>채널 글의 구매 링크는 제휴 링크라 구매하면 판매처에서 수수료를 받아요 (광고). 가격·재고는 올린 때 기준이에요.</i>",
        ])

    async def channel_guide(self) -> str:
        """/channelguide (주인이 보낼 때만): 채널에 안내 글을 올리고 고정한다 (봇이 관리자 — 고정 권한이 있어야 함)."""
        if self.bot is None or self.publisher.channel_id is None or self.publisher.dry_run:
            return "텔레그램 채널이 연결되어 있지 않거나 연습 모드라 올리지 않았어요."
        text = self.channel_guide_text()
        result = await self.publisher.publish_raw(text, preview_disabled=True, silent=True)
        if not result.ok or result.message_id is None:
            return f"❌ 채널 안내를 올리지 못했어요: <code>{html.escape(result.error or '')}</code>"
        try:
            await self.bot.pin_chat_message(chat_id=self.publisher.channel_id, message_id=result.message_id,
                                            disable_notification=True)
        except Exception as e:  # noqa: BLE001 — 고정 권한이 없으면 글은 남기고 켜는 법을 알림
            log.warning("channel guide pin failed: %s", type(e).__name__)
            return ("📌 채널 안내를 올렸지만 고정하지 못했어요. 채널 정보 → 관리자 → 핫딜봇 → '메시지 고정' 권한을 켠 뒤 "
                    "채널에서 그 글을 길게 눌러 고정해 주세요.")
        self.db.kv_set("channel_guide_message_id", str(result.message_id))
        self.db.log_event("INFO", "channel_guide", f"posted and pinned ({result.message_id})")
        return "📌 채널 안내를 올리고 맨 위에 고정했어요."

    async def sns_tick(self) -> list[str]:
        """1분마다 (scheduler.sns_loop): 스레드 답글 모으기(하루 두 번)·투표·투표 결과·일요일 TOP5·SNS 0개 알림·통계 권한 할 일.
        하나가 실패해도 나머지는 돈다. 한 일 이름 목록."""
        now = self.clock()
        tz = self.settings.app.timezone
        done: list[str] = []
        tc = self.settings.threads

        async def run(name: str, job: Callable[[], Any]) -> None:
            try:
                if await job():
                    done.append(name)
            except Exception as e:  # noqa: BLE001
                log.warning("sns job %s failed: %s", name, e)
                self.db.log_event("WARNING", "sns", f"{name}: {type(e).__name__}: {e}")

        for i, win in enumerate(tc.replies.windows):
            key = f"threads_replies_{i}"
            if tc.enabled and tc.replies.enabled and slot_due(self.db, key, win, now, tz):
                mark_slot(self.db, key, now, tz)  # 실패해도 같은 시간대에 몇 번씩 돌지 않게 먼저
                await run("threads_replies", lambda i=i: self.threads_flow.collect_replies(now, slot=str(i)))
        await run("threads_poll", lambda: self.threads_flow.maybe_poll(now))
        if self._hourly("poll_results", now):
            await run("poll_results", lambda: self.threads_flow.refresh_poll_results(now))
        await run("weekly_top", lambda: weekly.weekly_top(self, now))
        if self._hourly("sns_zero", now):
            await run("sns_zero", lambda: growth_alerts.sns_zero_check(self, now))
        if slot_due(self.db, "sns_scope", "10:00-10:59", now, tz):
            mark_slot(self.db, "sns_scope", now, tz)
            await run("sns_scope", lambda: growth_alerts.scope_todo(self, now))
        return done

    def _hourly(self, name: str, now: datetime) -> bool:
        """한 시간에 한 번 (kv 표식)."""
        key = f"hourly:{name}"
        last = from_iso(self.db.kv_get(key) or None)
        if last is not None and now - last < timedelta(hours=1):
            return False
        self.db.kv_set(key, to_iso(now))
        return True

    async def domain_todo(self) -> None:
        """봇 시작 때: 내 도메인의 DNS A 레코드가 이 서버를 안 가리키면 주인 할 일 (deploy/lightsail/https.sh --domain)."""
        try:
            await growth_alerts.domain_check(self, alert=True)
        except Exception as e:  # noqa: BLE001
            log.warning("domain check failed: %s", e)

    def owner_notice(self, kind: str, text: str) -> None:
        """주인 카톡 '나에게 보내기' 요청을 Blog-Auto 받은편지함에 (카톡 토큰은 Blog-Auto 가 가지고 있어 거기서 보낸다)."""
        self.relay_to_blog({"owner_notice": {"kind": kind, "text": text, "at": time.time()}})

    def relay_nl(self, message: dict, why: str) -> None:
        """말 명령: 원래 메시지를 'nl' 표시와 함께 Blog-Auto 받은편지함에 (Blog-Auto 의 해석기가 처리하고 답함)."""
        self.nl.relay_nl(message, why)

    def relay_to_blog(self, update: dict) -> bool:
        """블로그(demiyum) 명령·버튼 → Blog-Auto 가 읽는 받은편지함 파일에 한 줄 (Blog-Auto 가 처리하고 이 봇으로 답함).
        남겼으면 True (주인 할 일 카톡을 못 남겼으면 텔레그램으로 소리 내서 알리려고)."""
        try:
            path = self.settings.data_dir / "blogauto_inbox.jsonl"
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(update, ensure_ascii=False, default=str) + "\n")
            os.chmod(path, 0o600)
        except OSError as e:
            log.warning("blog relay failed: %s", e)
            return False
        return True

    async def blog_digest(self, *, preview: bool = False) -> str:
        """하루치 발행 딜을 블로그 글 한 편(복붙용)으로 만들어 관리자 챗에 보낸다. preview 면 기준 시각을 옮기지 않는다."""
        cfg = self.settings.blog_digest
        tz = self.settings.app.timezone
        now = utcnow()
        last = from_iso(self.db.kv_get("blog_digest_last_at"))
        start = max(last or (now - timedelta(hours=24)), now - timedelta(hours=48))
        items = self.db.published_items_between(start, now, include_dry_run=self.state.dry_run)
        span = f"{fmt_local(start, tz)} ~ {fmt_local(now, tz)}"
        if len(items) < max(cfg.min_items, 1):
            return f"📝 블로그 정리: 그동안({span}) 채널에 올라간 딜이 없어 만들 글이 없습니다."
        digest = self.digest.build(items, when=local_now(tz))
        if not digest.blocks:
            return f"📝 블로그 정리: 글에 넣을 딜이 없습니다 ({span})."
        # 왜 이만큼인지 같이 알려 준다: 채널에 실제로 올라간 글만 들어가고, 링크·확인을 기다리는 글은 처리해야 다음 정리에 들어간다
        hot = [it for it in items if it.deal.product.deal_kind != "info"]
        infos_ok = [
            it for it in items
            if it.deal.product.deal_kind == "info"
            and (it.deal.product.extra.get("info_body") or (it.deal.product.extra.get("info_draft") or {}).get("text"))
        ]
        infos_dropped = len(items) - len(hot) - len(infos_ok)
        picked = len(hot) + min(len(infos_ok), cfg.max_info_items if cfg.max_info_items > 0 else len(infos_ok))
        if cfg.max_items > 0:
            picked = min(picked, cfg.max_items)
        counts = self.db.queue_counts()
        why = [f"채널에 실제로 올라간 딜 {len(items)}건 (핫딜 {len(hot)} · 정리된 이벤트 {len(infos_ok)}) 중 점수 높은 {picked}건"]
        if infos_dropped:
            why.append(f"본문 없는 옛 정보 글 {infos_dropped}건은 뺐습니다")
        if counts.get("awaiting_link", 0):
            why.append(f"내 링크를 기다리는 글 {counts['awaiting_link']}건은 링크를 붙이면 다음 정리에 들어갑니다")
        if counts.get("awaiting_approval", 0):
            why.append(f"확인을 기다리는 글 {counts['awaiting_approval']}건은 /ok 하면 다음 정리에 들어갑니다")
        if picked < 3:
            why.append("아직 적으니 21:30 정리에는 더 모일 수 있습니다")
        titles = "\n".join(f"{i}. {html.escape(t)}" for i, t in enumerate(digest.titles, 1))
        head = (
            f"📝 <b>{'미리 보기 — ' if preview else ''}오늘의 핫딜 블로그 글</b> ({html.escape(span)})\n"
            f"어떤 글이 들어갔나: {html.escape(' · '.join(why))}\n\n"
            f"제목 후보 (하나 골라 제목 칸에):\n{titles}\n\n"
            "본문은 아래 회색 상자를 길게 눌러 복사한 뒤 블로그 글쓰기에 붙여넣으세요. "
            "채널 안내·면책·서명까지 다 들어 있어 사진만 넣으면 됩니다. 태그는 마지막 상자에 있습니다."
        )
        await self.notifier.send(head, silent=True)
        for block in digest.blocks:
            await self.notifier.send(block.as_telegram_html(), silent=True)
        if not preview:
            self.db.kv_set("blog_digest_last_at", to_iso(now))
            self.db.log_event("INFO", "blog_digest", f"{len(items)} deals ({span})")
        return f"📝 블로그 글 문구를 보냈습니다 ({span} 사이 {len(items)}건 중 {picked}건)."

    async def send_copy_blocks(self, queue_id: int | None = None) -> str:
        """최근 발행 딜(또는 대기열 번호)의 복붙 문구를 다시 보낸다."""
        deal: Deal | None = None
        if queue_id is not None:
            item = self.db.get_queue_item(queue_id)
            deal = item.deal if item else None
        else:
            item = self.db.last_published_item()
            deal = item.deal if item else None
        if deal is None:
            return "복사할 딜을 찾지 못했습니다. /queue 에서 번호를 확인해 <code>/copy 번호</code> 로 보내주세요."
        blocks = self.copy_blocks.build(deal)
        if not blocks:
            return "복붙 대상이 설정되어 있지 않습니다 (config.yaml 의 copy.targets)."
        for block in blocks:
            await self.notifier.send(block.as_telegram_html(), silent=True)
        return f"📋 {', '.join(b.name for b in blocks)} 문구를 보냈습니다."

    async def _handle_publish_failure(
        self, item: QueueItem, error: str, deal: Deal | None = None, *, maybe_posted: bool = False
    ) -> None:
        """maybe_posted: 보낸 뒤 텔레그램 답만 못 받음(시간 초과) — 이미 올라갔을 수 있어 자동으로 다시 보내지 않고
        주인에게 채널 확인을 부탁한다 (없으면 /link 로 다시). 다른 실패는 max_publish_attempts 까지 다시 시도."""
        if maybe_posted:
            error = f"{MAYBE_POSTED}: {error}"
        attempts = item.attempts + 1
        final = maybe_posted or attempts >= self.settings.publish.max_publish_attempts
        self.db.update_queue_item(
            item.id, status="failed" if final else "pending", error=error, increment_attempts=True, deal=deal
        )
        self.db.log_event("ERROR" if final else "WARNING", "publish", f"{item.product_id}: {error}")
        self.state.set_error(f"[publish] {error}")
        log.warning("publish failed (%d/%d) %s: %s", attempts, self.settings.publish.max_publish_attempts, item.product_id, error)
        if maybe_posted:
            await self.notifier.notify_publish_unsure(item.id, deal or item.deal, error)
        else:
            await self.notifier.notify_publish_failed(item.deal, error, final=final)
        if final:
            await self.notifier.publish_failed_final(error)  # 3번 이어지면 '발행 막힘' 급한 카톡

    async def drain_queue(self, max_items: int | None = None) -> int:
        n = 0
        while max_items is None or n < max_items:
            if not await self.process_queue_once():
                break
            n += 1
        return n

    async def run_once(self, collector_names: list[str] | None = None, *, publish: bool = True) -> list[dict[str, Any]]:
        results = []
        for c in self.collectors:
            if collector_names and c.name not in collector_names:
                continue
            results.append(await self.run_collector(c))
        if publish:
            published = await self.drain_queue()
            log.info("queue drained: %d item(s) processed", published)
        return results

    async def daily_summary(self) -> str:
        now = utcnow()
        summary = self.db.summary(now - timedelta(days=1), now)
        text = self.reporter.summary_text(summary)
        await self.notifier.notify_daily_summary(text)
        self.db.kv_set("last_daily_summary_at", to_iso(now))
        return text

    async def owner_summary(self) -> str:
        """저녁 요약(카톡, Blog-Auto 가 21~22시에 문장으로 보냄)에 넣을 딜봇 하루 정리 — 표가 아니라 문장 (주인 결정 10/11)."""
        now = utcnow()
        s = self.db.summary(now - timedelta(days=1), now)
        posted = "연습으로 미리 본 딜" if self.state.dry_run else "채널에 올린 딜"
        day = f"오늘 딜봇은 {posted}이 {s.published}개예요" + (f" (끝내 못 올린 것 {s.publish_failed}개)" if s.publish_failed else "") + "."
        try:  # 숫자만이 아니라 무엇을 올렸는지 (주인 결정 10/11: 이름으로)
            items = self.db.published_items_between(now - timedelta(days=1), now + timedelta(seconds=1),  # 초 단위라 방금 것도
                                                    include_dry_run=self.state.dry_run)
        except Exception:  # noqa: BLE001 — 이름을 못 읽어도 요약은 보낸다
            items = []
        names = list(dict.fromkeys(truncate(it.deal.product.name, 16) for it in items))
        if names and s.published:
            shown = "·".join(f"「{n}」" for n in names[:2])
            day = day[:-1] + f" — {shown}{' 등' if len(names) > 2 else ''}."
        if s.run_errors:
            day += f" 게시판 확인 중 오류가 {s.run_errors}번 있었지만 봇은 계속 돌았어요."
        if self.state.paused:
            day += " 지금은 발행이 멈춰 있어요 (/resume)."
        try:  # 특가로 봤는데 안 올린 딜과 그 이유 (주인 결정 10/13: 쿠팡 딜이 왜 안 올라오는지 보이게). 알림은 따로 안 보냄
            skipped = self.skipped_deal_counts(now - timedelta(days=1), now + timedelta(seconds=1))
        except Exception:  # noqa: BLE001 — 이유를 못 세도 요약은 보낸다
            log.exception("skipped deal counts failed")
            skipped = {}
        # 링크 요청 자리가 차서 빠진 딜은 '안 올린 딜' 문장이 아니라 아래 링크 자리 문장에서 따로 (자리가 모자라 잘리지 않게 — 주인 결정 10/15)
        replaced, full = skipped.pop(LINK_REPLACED_LABEL, 0), skipped.pop(LINK_FULL_LABEL, 0)
        day += _skip_sentence(skipped, room=DAY_LINE_MAX - len(day))  # 맨 끝에, 남는 자리만큼만 (오류·/resume 이 잘리지 않게)
        self.notifier.summary("day", day, replace=True)
        waiting = []
        links = self.db.awaiting_items(max(50, self.settings.publish.max_awaiting_links))
        if links:
            ids = " ".join(f"#{it.id}" for it in links[:5]) + (" …" if len(links) > 5 else "")
            waiting.append(f"핫딜봇에서 내 링크를 기다리는 딜이 {len(links)}개 있어요 ({ids}).")
        if replaced or full:
            cap = self.settings.publish.max_awaiting_links
            parts = ([f"더 좋은 딜로 바꾼 게 {replaced}개"] if replaced else []) + ([f"못 묻고 건너뛴 게 {full}개"] if full else [])
            waiting.append(f"오늘 링크 요청 자리({cap}개)가 차서 {', '.join(parts)}예요.")
        asks = self.db.awaiting_items(50, statuses=("awaiting_approval",))
        if asks:
            waiting.append(f"확인(/ok)을 기다리는 딜이 {len(asks)}개 있어요.")
        self.notifier.summary("waiting", " ".join(waiting) or "", replace=True)
        return day + (" " + " ".join(waiting) if waiting else "")

    def heartbeat(self) -> None:
        self.db.kv_set("heartbeat", to_iso(utcnow()))

    async def send_heartbeat(self) -> str:
        """관리자 챗이 조용할 때 보내는 짧은 '정상 가동 중' 상태."""
        text = self.reporter.heartbeat_text(self.settings.monitoring.heartbeat_minutes or 30)
        await self.notifier.send(text)
        return text

    def maintenance(self) -> None:
        pruned = self.db.prune(
            price_history_days=self.settings.app.prune_price_history_days,
            events_days=self.settings.app.prune_events_days,
        )
        pruned["admin_msgs"] = self.db.prune_admin_msgs(REGISTRY_DAYS)
        pruned["nl_pending"] = self.nl.prune_pending()
        pruned["owner_photos"] = self._prune_owner_photos()
        pruned["sns"] = self.db.prune_sns(utcnow() - timedelta(days=self.settings.app.prune_events_days))
        pruned["late_photo"] = self.late_photo.prune()
        if self.router is not None:  # 주인 알림·푸시 기록 (14일) · 스레드 답글 (7일)
            now = time.time()
            pruned["owner_notices"] = self.router.store.prune(now - 14 * 86400)
            pruned["threads_replies"] = self.router.store.prune_replies(now)
        with contextlib.suppress(Exception):
            self.db._conn.execute("DELETE FROM dash_audit WHERE ts < ?", (time.time() - 90 * 86400,))
        log.info("maintenance: pruned %s", pruned)

    def __repr__(self) -> str:
        return f"<DealBot v{__version__} collectors={self.collector_names()} dry_run={self.state.dry_run}>"
