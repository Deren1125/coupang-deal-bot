"""파이프라인 오케스트레이터.

수집기 → 가격 이력 저장 → 특가 판정 → 대기열 → (속도 제한/중복 확인) → 링크 변환(자동/수동) → 채널 발행 → 관리자 알림
"""

from __future__ import annotations

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
from dealbot.coupang.client import CoupangClient
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
from dealbot.monitoring.state import BotState, CollectorStatus
from dealbot.pricing.evaluator import DealEvaluator
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer
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
        if settings.secrets.has_coupang:
            self.coupang = CoupangClient(
                settings.secrets.coupang_access_key or "",
                settings.secrets.coupang_secret_key or "",
                http=self.http,
                sub_id=settings.secrets.coupang_sub_id,
                max_retries=settings.http.max_retries,
                retry_backoff=settings.http.retry_backoff_seconds,
            )
            providers["coupang"] = CoupangDeeplinkProvider(self.coupang, self.http, settings.links)
        else:
            log.warning("COUPANG_ACCESS_KEY/SECRET_KEY not set — coupang collectors & deeplink disabled")
        if settings.secrets.has_linkprice:
            providers["linkprice"] = LinkPriceProvider(
                settings.secrets.linkprice_affiliate_id or "",
                self.http,
                retries=settings.http.max_retries,
                backoff=settings.http.retry_backoff_seconds,
            )
        self.links = LinkRouter(self.registry, settings.links, settings.publish, providers=providers)

        self.evaluator = DealEvaluator(settings.deal)
        self.renderer = TemplateRenderer(settings.templates_dir, settings.app.timezone)
        self.rate_limiter = RateLimiter(self.db, settings.publish)

        # ---- 텔레그램
        self.application: Application | None = None  # type: ignore[type-arg]
        self.bot: Bot | None = None
        if settings.secrets.has_telegram:
            self.application = Application.builder().token(settings.secrets.telegram_bot_token or "").build()
            self.bot = self.application.bot
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
        self.notifier = AdminNotifier(
            self.bot, settings.secrets.telegram_admin_chat_id, settings.monitoring, self.renderer, settings.app.timezone
        )
        self.reporter = StatusReporter(settings, self.db, self.state, self.rate_limiter, self.renderer, self.registry, self.links)

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
        await self.application.initialize()
        await self.application.start()
        if polling and self.settings.secrets.telegram_admin_chat_id and self.application.updater:
            await self.application.updater.start_polling(allowed_updates=[Update.MESSAGE], drop_pending_updates=True)

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
            for p in products:
                if not self._shop_allowed(p):
                    continue
                stats = self.db.price_stats(p.product_id, cfg.deal.history_days, now)
                verdict = self.evaluator.evaluate(p, stats)
                if p.has_price:
                    self.db.record_observation(p, now)
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
            result.update(status="error", error=msg)
            self.db.finish_run(run_id, status="error", error=msg)
            self.db.log_event("ERROR", f"collector:{name}", msg + "\n" + traceback.format_exc()[-1500:])
            self.state.set_error(f"[{name}] {msg}")
            log.exception("collector '%s' failed", name)
            await self.notifier.notify_error(f"collector:{name}", msg)
        finally:
            status.running = False
        return result

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
            await self.notifier.notify_published(deal, result)
        else:
            await self._handle_publish_failure(item, result.error or "unknown error", deal=deal)
        return True

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

    def maintenance(self) -> None:
        pruned = self.db.prune(
            price_history_days=self.settings.app.prune_price_history_days,
            events_days=self.settings.app.prune_events_days,
        )
        log.info("maintenance: pruned %s", pruned)

    def __repr__(self) -> str:
        return f"<DealBot v{__version__} collectors={self.collector_names()} dry_run={self.state.dry_run}>"
