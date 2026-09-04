"""파이프라인 오케스트레이터.

수집기 → 가격 이력 저장 → 특가 판정 → 대기열 → (속도 제한/중복 확인) → 링크 변환(자동/수동) → 채널 발행 → 관리자 알림
"""

from __future__ import annotations

import html
import logging
import traceback
from datetime import timedelta
from typing import Any

import httpx
from telegram import Bot, Update
from telegram.ext import Application

from dealbot import __version__
from dealbot.collectors import (
    BaseCollector,
    CollectorContext,
    CollectorUnavailable,
    build_collector,
)
from dealbot.config import Settings
from dealbot.coupang.client import ApiBudget, CoupangClient
from dealbot.enrich import PageEnricher
from dealbot.links import (
    CoupangDeeplinkProvider,
    LinkConversionError,
    LinkPriceProvider,
    LinkRouter,
    ManualLinkRequired,
    ShopSkipped,
)
from dealbot.manual import parse_manual_post
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring.admin import AdminNotifier, StatusReporter, register_admin_handlers
from dealbot.monitoring.push import PushNotifier
from dealbot.monitoring.state import BotState, CollectorStatus
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.pricing.market import CoupangMarketReference, MarketQuote
from dealbot.publisher.copyblocks import CopyBlockBuilder
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer
from dealbot.publisher.threads import (
    ThreadsClient,
    ThreadsError,
    ThreadsPublisher,
    ThreadsToken,
    authorize_url,
)
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database, QueueItem
from dealbot.utils.timeutil import to_iso, utcnow

log = logging.getLogger(__name__)


class DealBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = BotState(dry_run=settings.publish.dry_run)
        self.db = Database(settings.db_path)
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
            settings.coupang.max_calls_per_hour, reserve={"deeplink": settings.coupang.deeplink_reserve}
        )
        self.market: CoupangMarketReference | None = None
        if settings.secrets.has_coupang:
            self.coupang = CoupangClient(
                settings.secrets.coupang_access_key or "",
                settings.secrets.coupang_secret_key or "",
                http=self.http,
                sub_id=settings.secrets.coupang_sub_id,
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
        self.enricher = PageEnricher(self.http, timeout=settings.http.timeout_seconds)
        self.renderer = TemplateRenderer(settings.templates_dir, settings.app.timezone)
        self.rate_limiter = RateLimiter(self.db, settings.publish)

        # ---- 텔레그램
        self.application: Application | None = None  # type: ignore[type-arg]
        self.bot: Bot | None = None
        if settings.secrets.has_telegram:
            try:
                self.application = Application.builder().token(settings.secrets.telegram_bot_token or "").build()
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
            enabled=settings.threads.enabled,
            dry_run=self.state.dry_run,
            refresh_before_days=settings.threads.refresh_before_days,
        )
        self.copy_blocks = CopyBlockBuilder(settings.copy_cfg, self.renderer, self.registry)

        self.notifier = AdminNotifier(
            self.bot,
            settings.secrets.telegram_admin_chat_id,
            settings.monitoring,
            self.renderer,
            settings.app.timezone,
            push=self.push,
        )
        self.reporter = StatusReporter(
            settings, self.db, self.state, self.rate_limiter, self.renderer, self.registry, self.links, budget=self.budget
        )

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

        if self.application is not None and settings.secrets.telegram_admin_chat_id:
            register_admin_handlers(
                self.application,
                admin_chat_id=settings.secrets.telegram_admin_chat_id,
                reporter=self.reporter,
                controller=self,
            )

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
        except Exception as e:  # noqa: BLE001
            log.warning("get_me failed: %s", e)
        if polling and self.settings.secrets.telegram_admin_chat_id and self.application.updater:
            # 재배포 중(봇이 잠깐 꺼진 사이)에 보낸 명령도 켜지면 처리한다. 너무 오래된 것은 admin 쪽 가드가 버림
            await self.application.updater.start_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=False)

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

    async def close(self) -> None:
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
            return f"알 수 없는 수집기: {', '.join(unknown)}\n사용 가능: {', '.join(names)}"
        for t in targets:
            self.state.collectors[t].run_requested = True
        return f"▶️ 실행 예약: {', '.join(targets)} (다음 스케줄러 틱에 실행)"

    async def attach_link(self, queue_id: int, url: str) -> str:
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return f"#{queue_id} 항목이 없습니다."
        if item.status not in ("awaiting_link", "pending", "failed"):
            return f"#{queue_id} 은(는) 현재 '{item.status}' 상태라 링크를 붙일 수 없습니다."
        if not url.startswith("http"):
            return "링크는 http(s):// 로 시작해야 합니다."
        self.db.set_queue_link(queue_id, url.strip())
        self.db.log_event("INFO", "manual_link", f"#{queue_id} {url}")
        return f"🔗 #{queue_id} 링크 저장됨. 곧 발행됩니다."

    def skip_item(self, queue_id: int) -> str:
        item = self.db.get_queue_item(queue_id)
        if item is None:
            return f"#{queue_id} 항목이 없습니다."
        if item.status in ("published",):
            return f"#{queue_id} 은(는) 이미 발행되었습니다."
        self.db.update_queue_item(queue_id, status="skipped", error="skipped by admin")
        return f"⏭ #{queue_id} 건너뜀."

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
        if self.db.posted_within(product.product_id, self.settings.publish.dedup_days, now):
            return f"⚠️ 최근 {self.settings.publish.dedup_days}일 안에 이미 발행한 상품입니다: {product.name[:40]}"
        if not self.db.enqueue(deal, score=1000.0, now=now):
            return "⚠️ 이미 대기열에 있는 상품입니다."
        shop_name = shop.name if shop else post.shop_key
        return (
            f"📝 대기열 맨 앞에 추가 [{shop_name}] {product.name[:50]}"
            + (f" — {product.price:,}원" if product.has_price else "")
            + "\n속도 제한 안에서 바로 발행됩니다."
        )

    async def test_post(self) -> str:
        """샘플 딜을 관리자 챗에 보내 양식 확인."""
        from dealbot.cli import sample_deal

        if self.bot is None or not self.notifier.enabled:
            return "텔레그램 봇 토큰과 관리자 챗 ID 가 필요합니다."
        original = (self.publisher.channel_id, self.publisher.dry_run)
        try:
            self.publisher.channel_id = self.notifier.chat_id
            self.publisher.dry_run = False
            result = await self.publisher.publish(sample_deal())
        finally:
            self.publisher.channel_id, self.publisher.dry_run = original
        return "✅ 샘플 발행 완료 (위 메시지)" if result.ok else f"❌ 실패: {result.error}"

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
        return "⏰ 2분 안에 로그인이 확인되지 않았습니다. /naverlogin 을 다시 보내세요."

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
        if self.copy_blocks.enabled:
            out.append((True, "복붙 문구: " + ", ".join(t.name for t in self.settings.copy_cfg.targets if t.enabled)))
        if self.market is not None:
            out.append((True, f"시중가 대조: 쿠팡 검색 (시간당 {self.settings.deal.market.max_checks_per_hour}회)"))
        else:
            out.append((None, "시중가 대조: 꺼짐 (쿠팡 키 필요)"))
        if self.settings.browser.enabled:
            if self.naver_connect is None:
                out.append((False, "브라우저 자동화: 켜져 있으나 playwright/크로미움 없음"))
            else:
                try:
                    logged = await self.naver_connect.is_logged_in()
                    out.append((True, "네이버 브라우저 로그인 유지 중") if logged else (None, "네이버 브라우저: 로그인 필요 (/naverlogin)"))
                except Exception as e:  # noqa: BLE001
                    out.append((False, f"브라우저: {e}"))
        if self.settings.secrets.has_linkprice:
            out.append((True, "링크프라이스 ID 설정됨"))
        else:
            out.append((None, "링크프라이스: 미설정 (11번가/G마켓/알리 등은 원본 링크)"))
        return out

    # ------------------------------------------------------------- pipeline
    def _shop_allowed(self, product: Product) -> bool:
        shop = self.registry.get(product.shop)
        if shop is None:
            return self.settings.publish.allow_raw_links
        return shop.enabled and shop.link_mode != "skip"

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
            deals = queued = 0
            enriched = 0
            for p in products:
                if not self._shop_allowed(p):
                    continue
                if self._should_enrich(p) and enriched < cfg.deal.enrich.max_per_run:
                    enriched += 1
                    await self._enrich(p)
                stats = self.db.price_stats(p.product_id, cfg.deal.history_days, now)
                verdict = self.evaluator.evaluate(p, stats)
                if p.has_price and self._should_record(p.product_id, now):
                    self.db.record_observation(p, now)
                if self._needs_market_check(p, verdict):
                    quote = await self._market_quote(p)
                    verdict = self.evaluator.evaluate(p, stats, quote, market_available=True)
                if not verdict.is_deal:
                    continue
                deals += 1
                if self.db.posted_within(p.product_id, cfg.publish.dedup_days, now):
                    log.debug("deal %s already posted within %dd — skip", p.product_id, cfg.publish.dedup_days)
                    continue
                deal = Deal(product=p, verdict=verdict, detected_at=now)
                if self.db.enqueue(deal, score=verdict.score, now=now):
                    queued += 1
                    log.info("deal queued [%s/%s] %s %s원 (%s)", name, p.shop, p.name[:40], f"{p.price:,}", ", ".join(verdict.reasons))
            result.update(collected=len(products), deals=deals, queued=queued)
            self.db.finish_run(run_id, status="ok", collected=len(products), deals=deals, queued=queued)
            log.info("collector '%s' done: collected=%d deals=%d queued=%d", name, len(products), deals, queued)
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

    def _should_enrich(self, p: Product) -> bool:
        ec = self.settings.deal.enrich
        if not ec.enabled or p.shop not in ec.shops:
            return False
        if self.db.posted_within(p.product_id, self.settings.publish.dedup_days):
            return False
        return not p.image_url or p.rating is None or not p.has_price

    async def _enrich(self, p: Product) -> list[str]:
        meta = await self.enricher.fetch(p.url)
        if meta is None:
            return []
        filled = PageEnricher.apply(p, meta)
        if filled:
            log.info("enriched %s from page: %s", p.name[:40], ", ".join(filled))
        return filled

    def _should_record(self, product_id: str, now: Any) -> bool:
        gap = self.settings.deal.observation_min_gap_hours
        if gap <= 0:
            return True
        last = self.db.last_observed_at(product_id)
        return last is None or (now - last) >= timedelta(hours=gap)

    def _needs_market_check(self, p: Product, verdict: DealVerdict) -> bool:
        """시중가 대조는 쿠팡 검색 예산을 쓰므로 '후보'에만: 다른 몰 + 가격 있음 + (판정 통과 또는 표시 할인율 후보)."""
        if self.market is None or p.shop == "coupang" or not p.has_price:
            return False
        if self.db.posted_within(p.product_id, self.settings.publish.dedup_days):
            return False
        if "low_interest" in verdict.reasons or any(r.startswith("excluded") for r in verdict.reasons):
            return False
        return True  # 관심도 게이트를 통과한 다른 몰의 딜은 (d) 대조가 기본

    async def _market_quote(self, p: Product) -> MarketQuote | None:
        assert self.market is not None
        cached = self.db.get_market_quote(p.product_id, self.settings.deal.market.cache_hours)
        if cached:
            return MarketQuote(price=int(cached["price"]), source=cached["source"], title=cached.get("title") or "", url=cached.get("url"))
        quote = await self.market.lookup(p)
        if quote is not None:
            self.db.set_market_quote(p.product_id, price=quote.price, source=quote.source, title=quote.title, url=quote.url)
        return quote

    async def _ensure_link(self, item: QueueItem) -> tuple[str, str | None]:
        """returns (state, error): state ∈ ok | manual | skip | fail"""
        deal = item.deal
        if deal.affiliate_url:
            # 관리자가 /link 로 붙였거나 직접 올린 딜: 그대로 사용
            return "ok", None
        try:
            deal.affiliate_url = await self.links.to_affiliate(deal.product)
            return "ok", None
        except ManualLinkRequired as e:
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
            return "fail", str(e)
        except Exception as e:  # noqa: BLE001
            return "fail", f"{type(e).__name__}: {e}"

    def _expire_queue(self, now: Any) -> None:
        cfg = self.settings.publish
        expired = self.db.expire_queue(
            now - timedelta(hours=cfg.queue_ttl_hours),
            now,
            awaiting_older_than=now - timedelta(hours=cfg.manual_link_ttl_hours),
        )
        if expired:
            log.info("expired %d stale queue items", expired)

    async def process_queue_once(self) -> bool:
        """대기열에서 1건 발행 시도. 무언가 처리했으면 True."""
        cfg = self.settings.publish
        now = utcnow()
        self._expire_queue(now)

        if self.state.paused or not cfg.enabled:
            return False
        decision = self.rate_limiter.check(now)
        if not decision.allowed:
            return False
        item = self.db.next_pending()
        if item is None:
            return False

        deal = item.deal
        pid = deal.product.product_id
        if self.db.posted_within(pid, cfg.dedup_days, now):
            self.db.update_queue_item(item.id, status="skipped", error="already posted")
            return True

        state, err = await self._ensure_link(item)
        if state == "manual":
            shop = self.registry.get(err or "")
            self.db.update_queue_item(item.id, status="awaiting_link", error="manual link required", deal=deal)
            if shop is not None:
                await self.notifier.notify_manual_link(item, shop)
            log.info("queue #%d awaiting manual link (%s)", item.id, err)
            return True
        if state == "skip":
            self.db.update_queue_item(item.id, status="skipped", error=err)
            return True
        if state == "fail":
            await self._handle_publish_failure(item, err or "link conversion failed")
            return True

        result = await self.publisher.publish(deal)
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
            else:
                await self.notifier.notify_published(deal, result)
                await self._publish_side_channels(deal)
        else:
            await self._handle_publish_failure(item, result.error or "unknown error", deal=deal)
        return True

    async def _publish_side_channels(self, deal: Deal) -> None:
        """텔레그램 채널 발행 후: 스레드 자동 게시 + 복붙 문구를 관리자 챗으로."""
        if self.settings.threads.enabled and (self.threads.configured or self.threads.dry_run):
            result = await self.threads.publish(deal)
            if result.ok:
                self.db.log_event("INFO", "threads", f"{deal.product.product_id} {'dry-run' if result.dry_run else result.message_id}")
                log.info("threads posted: %s", deal.product.name[:40])
            else:
                self.db.log_event("WARNING", "threads", f"{deal.product.product_id}: {result.error}")
                log.warning("threads post failed: %s", result.error)
                await self.notifier.send(f"⚠️ <b>스레드 발행 실패</b>\n<code>{result.error}</code>")
        for block in self.copy_blocks.build(deal):
            await self.notifier.send(block.as_telegram_html(), silent=True)

    async def threads_auth_url(self) -> str:
        if not self.settings.secrets.has_threads_app:
            return "THREADS_APP_ID / THREADS_APP_SECRET 이 설정되어 있지 않습니다."
        token = self.threads.stored_token()
        if token is not None:
            try:
                me = await self.threads.client.me(token.access_token)
                return f"이미 연결되어 있습니다: @{me.get('username')} (다시 연결하려면 /threadscode 로 새 code 를 넣으세요)"
            except ThreadsError:
                pass
        url = authorize_url(self.settings.secrets.threads_app_id or "", self.settings.secrets.threads_redirect_uri)
        return (
            "1) 아래 링크를 열어 스레드 계정으로 승인하세요.\n"
            f"{url}\n\n"
            "2) 승인 후 이동한 주소창에서 <code>code=</code> 뒤의 값을 복사해\n"
            "<code>/threadscode 붙여넣기</code> 로 보내주세요.\n"
            "(주소가 열리지 않아도 됩니다. 주소창의 code 값만 필요합니다.)"
        )

    async def threads_submit_code(self, code: str) -> str:
        if not self.settings.secrets.has_threads_app:
            return "THREADS_APP_ID / THREADS_APP_SECRET 이 필요합니다."
        try:
            token = await self.threads.client.exchange_code(code, self.settings.secrets.threads_redirect_uri)
            self.threads.save_token(token)
            me = await self.threads.client.me(token.access_token)
            expires = token.expires_at.date().isoformat() if token.expires_at else "-"
            self.db.log_event("INFO", "threads", f"authorized as {me.get('username')}")
            return f"✅ 스레드 연결 완료: @{me.get('username')} (토큰 만료 {expires}, 자동 갱신됨)"
        except ThreadsError as e:
            return f"❌ 실패: {e}\n code 는 한 번만 쓸 수 있으니 /threadsauth 로 다시 받아 주세요."

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

    async def _handle_publish_failure(self, item: QueueItem, error: str, deal: Deal | None = None) -> None:
        attempts = item.attempts + 1
        final = attempts >= self.settings.publish.max_publish_attempts
        self.db.update_queue_item(
            item.id, status="failed" if final else "pending", error=error, increment_attempts=True, deal=deal
        )
        self.db.log_event("ERROR" if final else "WARNING", "publish", f"{item.product_id}: {error}")
        self.state.set_error(f"[publish] {error}")
        log.warning("publish failed (%d/%d) %s: %s", attempts, self.settings.publish.max_publish_attempts, item.product_id, error)
        await self.notifier.notify_publish_failed(item.deal, error, final=final)

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

    def heartbeat(self) -> None:
        self.db.kv_set("heartbeat", to_iso(utcnow()))

    async def send_heartbeat(self) -> str:
        """N시간마다 관리자 챗으로 보내는 짧은 '정상 가동 중' 상태."""
        text = self.reporter.heartbeat_text(self.settings.monitoring.heartbeat_hours or 3)
        await self.notifier.send(text)
        return text

    def maintenance(self) -> None:
        pruned = self.db.prune(
            price_history_days=self.settings.app.prune_price_history_days,
            events_days=self.settings.app.prune_events_days,
        )
        log.info("maintenance: pruned %s", pruned)

    def __repr__(self) -> str:
        return f"<DealBot v{__version__} collectors={self.collector_names()} dry_run={self.state.dry_run}>"
