"""인스타그램 대기열·시간표 (주인 결정 10/15).

텔레그램 채널에 올린 딜은 바로 인스타에 올리지 않고 인스타 대기열(DB ig_queue — 재시작해도 남음)에 넣는다. 1분마다 tick():
- 시간: 07:00~23:00 (instagram.hours) 에만. 한 번 볼 때 많아야 글 하나.
- 딜 하나짜리 글: 하루 3개까지(daily_singles), 점수 높은 순, 인스타 글 사이 2시간 이상(min_gap_minutes) — 캐러셀·릴스·단가표가
  잡힌 시각 앞 2시간도 비워 둔다. 올리기 직전에 품절·가격 오름을 다시 확인하고(채널 품절 기록 → 그 뒤 수집기가 본 가격 → 상품 페이지),
  품절이거나 값이 올랐으면 건너뛴다(사유 기록). 12시간(queue_expire_hours) 넘게 기다린 딜은 안 올린다.
- 점심 릴스(12~13시 아무 분): 그날 텔레그램에 올린 딜 중 점수 위 3개 → 10~15초 세로 영상 (서버 ffmpeg).
- 저녁 캐러셀(20~21시 아무 분): '오늘 가격 내려간 딜 TOP5' — 비교 근거(쿠팡 최저가·평소 가격·우리 기록 최저)가 있는 딜만.
- 화·금 단가표(19~20시 아무 분, TOP5 와 한 시간 넘게 떨어지게): '이번 주 생필품 1개당 가격' — 봇이 기록한 가격만.
릴스·캐러셀·단가표는 날마다 한 번 (kv ig_done:종류:날짜). 딜이 모자라면 그날은 건너뛴다. 하루 시각표는 kv ig_plan:날짜 (재시작해도 같은 시각).
올리기 전엔 insta_style.validate 로 본문 첫 줄 고지·모든 장의 고지·근거 없는 '최저가'·'역대'를 검사한다 (실패하면 안 올리고 기록).
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import random
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.media import card_slides, reel
from dealbot.media.card_slides import GENERIC_AD, SlideSpec, ad_of, deal_rows
from dealbot.models import Deal, PublishResult
from dealbot.monitoring.admin import kakao_fit
from dealbot.page_price import check_page_price
from dealbot.publisher import insta_style
from dealbot.publisher.insta_style import Pick, UnitRow
from dealbot.publisher.instagram import CHECK_FAILED, REEL_TIMEOUT
from dealbot.shops import deal_disclosure
from dealbot.utils.text import clean_name, unit_price
from dealbot.utils.timeutil import from_iso, in_time_window, to_iso, utcnow

log = logging.getLogger(__name__)

KINDS = ("reel", "unit", "top5")  # 날마다 정해진 시간대에 한 번씩 올리는 글
LABELS = {"single": "딜 하나", "reel": "점심 릴스", "top5": "TOP5 캐러셀", "unit": "생필품 단가표"}
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
FAIL_BACKOFF = timedelta(minutes=30)  # 올리다 실패하면 이만큼 쉼 (연결이 끊겼을 때 같은 알림이 줄줄이 가지 않게)
RECHECK_TRIES = 3  # 한 번 볼 때 딜 하나짜리 글 후보를 이만큼까지 다시 확인
PHOTO_KEEP_SECONDS = 36 * 3600  # 상품 사진 사본: 그날 캐러셀·릴스가 쓰고 나면 지움
REEL_KEEP_SECONDS = 2 * 86400
UNIT_LISTING = 4  # 단가표 한 장에 넣는 딜 수
SCHEDULED_GAP_MINUTES = 60  # 단가표와 TOP5 캐러셀은 같은 시간(한 시간 안)에 올리지 않는다
KV_BACKOFF = "ig_backoff_until"
KV_FFMPEG = "ig_ffmpeg_alerted"
KV_KEEP_DAYS = 7


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _window(spec: str) -> tuple[int, int]:
    start, end = spec.split("-")
    return _minutes(start), _minutes(end)


def _at(local: datetime, minutes: int) -> datetime:
    """그날(local 의 날짜) 자정 + minutes."""
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + timedelta(minutes=minutes)


def _read(path: str | None) -> bytes | None:
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


class InstagramScheduler:
    """인스타 대기열과 하루 시간표. bot 은 DealBot (설정·DB·게시기·카드·품절 확인을 그대로 씀)."""

    def __init__(self, bot: Any, *, rng: random.Random | None = None) -> None:
        self.bot = bot
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ 기본
    @property
    def cfg(self) -> Any:
        return self.bot.settings.instagram

    @property
    def db(self) -> Any:
        return self.bot.db

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.bot.settings.app.timezone)

    @property
    def active(self) -> bool:
        ig = self.bot.instagram
        return bool(self.cfg.enabled and (ig.configured or ig.dry_run))

    @property
    def photo_dir(self) -> Path:
        return Path(self.bot.settings.data_dir) / "ig_photos"

    @property
    def media_dir(self) -> Path:
        return Path(self.bot.settings.data_dir) / "media"

    # ------------------------------------------------------------ 대기열
    def enqueue(self, deal: Deal, *, photo: bytes | None, queue_id: int | None, now: datetime | None = None) -> int | None:
        """채널에 올린 딜을 인스타 대기열에 넣는다. photo: 텔레그램 글에 쓴 확인된 상품 사진 (사본을 남겨 나중에 카드로)."""
        now = now or utcnow()
        pid = deal.product.product_id
        photo_file: str | None = None
        if photo:
            try:
                self.photo_dir.mkdir(parents=True, exist_ok=True)
                path = self.photo_dir / (hashlib.sha1(f"{pid}|{now.isoformat()}".encode()).hexdigest()[:16] + ".jpg")
                path.write_bytes(photo)
                photo_file = str(path)
            except OSError as e:
                log.warning("instagram queue: photo copy failed (%s) — text card later", e)
        item_id = self.db.ig_enqueue(deal, queue_id=queue_id, score=deal.verdict.score, photo_file=photo_file, now=now)
        if item_id is None:
            if photo_file:
                Path(photo_file).unlink(missing_ok=True)
            log.info("instagram queue: %s is already waiting", pid)
            return None
        self.db.log_event("INFO", "instagram", f"{pid} queued for instagram (#{item_id}, score {deal.verdict.score:.0f})")
        log.info("instagram queued #%d: %s", item_id, deal.product.name[:40])
        return item_id

    def _expire(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=self.cfg.queue_expire_hours)
        for row in self.db.ig_expire(cutoff, now=now):
            self.db.log_event("INFO", "instagram", f"{row['product_id']} not posted: {row['reason']}")
            log.info("instagram queue #%d %s: %s", row["id"], row["status"], row["reason"])
        self._prune_files()
        day = (now.astimezone(self.tz) - timedelta(days=KV_KEEP_DAYS)).date().isoformat()
        for prefix in ("ig_plan:", "ig_done:"):
            self.db.kv_delete_prefix(prefix, keep=lambda k, _v: k.rsplit(":", 1)[-1] >= day)

    def _prune_files(self) -> None:
        cut = time.time()
        for folder, pattern, keep in ((self.photo_dir, "*.jpg", PHOTO_KEEP_SECONDS), (self.media_dir, "reel_*.mp4", REEL_KEEP_SECONDS)):
            if not folder.is_dir():
                continue
            for f in folder.glob(pattern):
                try:
                    if cut - f.stat().st_mtime > keep:
                        f.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------ 시간표
    def plan(self, local: datetime) -> dict[str, str]:
        """그날 릴스·TOP5·단가표 시각 (구간 안 무작위 분, 'HH:MM'). 처음 볼 때 정해 kv 에 남긴다 (재시작해도 같은 시각)."""
        key = f"ig_plan:{local.date().isoformat()}"
        raw = self.db.kv_get(key)
        if raw:
            try:
                got = json.loads(raw)
                if isinstance(got, dict):
                    return {str(k): str(v) for k, v in got.items()}
            except ValueError:
                pass
        cfg = self.cfg
        plan: dict[str, str] = {}
        if cfg.reels.enabled:
            plan["reel"] = self._pick(cfg.reels.window)
        if cfg.carousel.enabled:
            plan["top5"] = self._pick(cfg.carousel.window)
        uc = cfg.unit_carousel
        if uc.enabled and DAY_NAMES[local.weekday()] in uc.days:
            slot = self._pick(uc.window)
            for _ in range(60):  # TOP5 와 한 시간 넘게 떨어지게 (같은 시간대에 캐러셀 두 개가 나가지 않게)
                if "top5" not in plan or abs(_minutes(slot) - _minutes(plan["top5"])) >= SCHEDULED_GAP_MINUTES:
                    break
                slot = self._pick(uc.window)
            plan["unit"] = slot
        self.db.kv_set(key, json.dumps(plan))
        log.info("instagram plan %s: %s", local.date().isoformat(), plan)
        return plan

    def _pick(self, spec: str) -> str:
        start, end = _window(spec)
        m = self.rng.randrange(start, end)
        return f"{m // 60:02d}:{m % 60:02d}"

    def _window_of(self, kind: str) -> str:
        cfg = self.cfg
        return {"reel": cfg.reels.window, "top5": cfg.carousel.window, "unit": cfg.unit_carousel.window}[kind]

    def _done(self, kind: str, local: datetime) -> str | None:
        return self.db.kv_get(f"ig_done:{kind}:{local.date().isoformat()}")

    # ------------------------------------------------------------ 한 번 보기
    async def tick(self, now: datetime | None = None) -> str | None:
        """올릴 차례인 글을 하나 올린다 (또는 건너뜀을 기록한다). 한 일을 짧게 돌려준다 (없으면 None)."""
        if not self.active:
            return None
        now = now or utcnow()
        local = now.astimezone(self.tz)
        self._expire(now)
        if not in_time_window(local, self.cfg.hours):
            return None
        backoff = from_iso(self.db.kv_get(KV_BACKOFF))
        if backoff and now < backoff:
            return None
        plan = self.plan(local)
        for kind in KINDS:
            done = await self._scheduled(kind, plan, now, local)
            if done:
                return done
        return await self._single(now, local, plan)

    async def _scheduled(self, kind: str, plan: dict[str, str], now: datetime, local: datetime) -> str | None:
        slot = plan.get(kind)
        if not slot or self._done(kind, local):
            return None
        if local < _at(local, _minutes(slot)):
            return None
        key = f"ig_done:{kind}:{local.date().isoformat()}"
        if local >= _at(local, _window(self._window_of(kind))[1]):
            self.db.kv_set(key, "skipped: window passed")  # 봇이 꺼져 있다가 구간이 지난 뒤 켜짐
            log.info("instagram %s skipped today: the window passed", kind)
            return None
        self.db.kv_set(key, "posting")  # 올리다 끊겨도(재시작) 같은 날 두 번 올리지 않게
        posters: dict[str, Callable[[datetime, datetime], Awaitable[str]]] = {
            "reel": self._post_reel, "top5": self._post_top5, "unit": self._post_unit,
        }
        try:
            status = await posters[kind](now, local)
        except Exception as e:  # noqa: BLE001 — 한 종류가 실패해도 다른 인스타 글·발행 루프는 계속
            log.exception("instagram %s failed", kind)
            status = f"failed: {type(e).__name__}: {e}"
        self.db.kv_set(key, status[:300])
        level = "WARNING" if status.startswith("failed") else "INFO"
        self.db.log_event(level, "instagram", f"{kind} {local.date().isoformat()}: {status}"[:500])
        log.info("instagram %s: %s", kind, status)
        return f"{kind}: {status}"

    async def _single(self, now: datetime, local: datetime, plan: dict[str, str]) -> str | None:
        cfg = self.cfg
        if cfg.daily_singles <= 0:
            return None
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        if len(self.db.ig_posts_since(day_start, kind="single")) >= cfg.daily_singles:
            return None
        gap = timedelta(minutes=cfg.min_gap_minutes)
        last = self.db.ig_last_post_at()
        if last is not None and now - last < gap:
            return None
        for kind, slot in plan.items():
            if self._done(kind, local):
                continue  # 이미 올렸거나 건너뛴 묶음 글 (올리다 끊긴 것도 — 같은 날 다시 안 올림)
            if _at(local, _minutes(slot)) - local < gap:
                return None  # 곧 캐러셀·릴스·단가표 차례 — 그 앞 2시간은 비워 둔다
        tries = 0
        for item in self.db.ig_queued(10):
            if tries >= RECHECK_TRIES:
                break
            tries += 1
            deal: Deal = item["deal"]
            why = await self.recheck(deal, since=item["created_at"])
            if why:
                self.db.ig_update(item["id"], status="skipped", reason=why, now=now)
                self.db.log_event("INFO", "instagram", f"{deal.product.product_id} not posted: {why}")
                log.info("instagram queue #%d skipped — %s: %s", item["id"], why, deal.product.name[:40])
                continue
            return await self._post_single(item, now)
        return None

    # ------------------------------------------------------------ 올리기 직전 확인
    def _known_sold_out(self, pid: str) -> bool:
        """채널 글이 품절로 기록된 상품 (품절 표시를 붙였거나 블로그에 품절 줄을 남김 — app._on_sold_out)."""
        post = self.db.latest_post(pid)
        posted = str(post["posted_at"]) if post else ""
        marked = self.db.kv_get(f"soldout_marked:{pid}")
        if marked and (not posted or marked >= posted):
            return True
        return bool(posted) and self.db.kv_get(f"soldout_exported:{pid}") == posted

    async def recheck(self, deal: Deal, *, since: datetime | None) -> str | None:
        """올리기 직전 확인. 품절이거나 값이 올랐으면 그 사유, 아니면 None. 못 읽은 페이지로는 막지 않는다 (채널 글과 같은 규칙).
        1) 채널 글 품절 기록  2) 채널에 올린 뒤 수집기가 본 가격  3) 상품 페이지 (재고 표시 → 가격, 페이지를 읽는 몰만)."""
        bot = self.bot
        p = deal.product
        pid = p.product_id
        if self._known_sold_out(pid):
            return "sold out (channel record)"
        price = p.price if p.has_price else None
        tolerance = bot.settings.deal.page_check.tolerance_pct
        if price and since is not None:
            seen = self.db.price_observed_since(pid, since)
            if seen and seen > price * (1 + tolerance / 100):
                return f"price up {price:,}→{seen:,} (collected)"
        if not bot._can_check_page(p):
            return None
        try:
            meta = await bot.enricher.fetch(p.url)
        except Exception as e:  # noqa: BLE001
            log.info("instagram recheck: page read failed for %s: %s", pid, e)
            return None
        if meta is None:
            return None
        if meta.available is False:
            try:
                await bot._on_sold_out(p, via="인스타 올리기 전 상품 페이지")  # 채널 글 품절 표시·블로그 품절 줄도 (기존 품절 처리)
            except Exception as e:  # noqa: BLE001
                log.warning("sold-out handling after the instagram recheck failed: %s", e)
            return "sold out (page)"
        if price and meta.prices is not None:
            check = check_page_price(p.name, price, meta.prices, page_title=meta.title, **bot._page_tolerance(p))
            if check.status == "hold" and check.reason == "price" and check.page_price and check.page_price > price:
                return f"price up {price:,}→{check.page_price:,} (page)"
        return None

    # ------------------------------------------------------------ 딜 하나짜리 글
    async def _post_single(self, item: dict[str, Any], now: datetime) -> str:
        bot = self.bot
        deal: Deal = item["deal"]
        pid = deal.product.product_id
        deal_no = bot._page_deal_no(item["queue_id"])  # 카드·본문의 '#번호' 는 핫딜 페이지에 있는 글만
        problems = bot.instagram.check(deal, deal_no=deal_no)
        if problems:
            why = f"{CHECK_FAILED}: {'; '.join(problems)}"
            self.db.ig_update(item["id"], status="skipped", reason=why[:300], now=now)
            self.db.log_event("WARNING", "instagram", f"{pid} not posted: {why}"[:500])
            return f"single skipped: {pid}"
        self.db.ig_update(item["id"], status="posting", now=now)
        card_url = await bot.make_card(deal, _read(item.get("photo_file")), deal_no=deal_no)
        if card_url is None and not bot.instagram.dry_run:
            # 공개 도메인·카드 설정이 없음 (자기 점검이 알림) — 딜마다 실패 알림을 보내지 않고 기록만
            why = "카드 이미지를 못 만듦 (공개 도메인·card 설정)"
            self.db.ig_update(item["id"], status="skipped", reason=why, now=now)
            self.db.log_event("WARNING", "instagram", f"{pid} not posted: {why}")
            return f"single skipped: {pid}"
        try:
            result = await bot.instagram.publish(deal, card_url, deal_no=deal_no)
        except Exception as e:  # noqa: BLE001 — 재시도 뒤에도 남은 5xx·429·시간 초과
            result = PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
        if result.ok:
            media = "dry-run" if result.dry_run else str(result.message_id or "")
            self.db.ig_update(item["id"], status="posted", media_id=media or None, now=now)
            self.db.ig_record_post("single", media_id=media or None, items=[{"product_id": pid, "queue_id": item["queue_id"]}],
                                   dry_run=result.dry_run, now=now)
            self.db.log_event("INFO", "instagram", f"{pid} {media}")
            log.info("instagram posted: %s", deal.product.name[:40])
            return f"single: {pid}"
        self.db.ig_update(item["id"], status="failed", reason=(result.error or "")[:300], now=now)
        await self._failed(result, now, what=pid)
        return f"single failed: {pid}"

    async def _failed(self, result: PublishResult, now: datetime, *, what: str) -> None:
        self.db.log_event("WARNING", "instagram", f"{what}: {result.error}"[:500])
        log.warning("instagram post failed (%s): %s", what, result.error)
        if (result.error or "").startswith(CHECK_FAILED):
            return
        self.db.kv_set(KV_BACKOFF, to_iso(now + FAIL_BACKOFF))
        await self.bot._sns_failed(
            "인스타그램", "instaauth", f"⚠️ <b>인스타그램 발행 실패</b>\n<code>{html.escape(result.error or '')}</code>", result.error or "",
        )

    async def _finish(self, kind: str, result: PublishResult, picks: list[Any], now: datetime) -> str:
        if result.ok:
            media = "dry-run" if result.dry_run else str(result.message_id or "")
            items = [{"product_id": pk.deal.product.product_id if isinstance(pk, Pick) else pk.product_id,
                      "queue_id": pk.queue_id if isinstance(pk, Pick) else pk.deal_no} for pk in picks]
            self.db.ig_record_post(kind, media_id=media or None, items=items, dry_run=result.dry_run, now=now)
            return f"posted {media} ({len(picks)} deals)"
        if result.error == REEL_TIMEOUT:
            return "failed: timeout — 인스타가 영상 처리를 끝내지 않아 포기 (다음 날 다시)"
        await self._failed(result, now, what=kind)
        return f"failed: {result.error}"

    # ------------------------------------------------------------ 그날 딜 고르기 (캐러셀·릴스)
    def _photo_for(self, queue_id: int) -> bytes | None:
        for row in self.db.ig_items_for(queue_id):
            data = _read(row.get("photo_file"))
            if data:
                return data
        return None

    def _published(self, since: datetime, now: datetime) -> list[Any]:
        """그 뒤로 채널에 올라간 상품 딜 (같은 상품은 가장 최근 글 하나, 정보 글 제외)."""
        items = self.db.published_items_between(since, now + timedelta(minutes=1), include_dry_run=self.bot.state.dry_run)
        latest: dict[str, Any] = {}
        for it in items:
            p = it.deal.product
            if p.deal_kind == "info" or not p.has_price:
                continue
            if p.product_id not in latest or it.id > latest[p.product_id].id:
                latest[p.product_id] = it
        return list(latest.values())

    async def _picks(self, now: datetime, local: datetime, *, limit: int, need_drop: bool) -> tuple[list[Pick], list[str]]:
        """오늘(0시부터) 채널에 올린 딜 중 limit 개. need_drop(TOP5): 비교 근거가 있는 딜만, 내려간 폭 순 / 아니면(릴스) 점수 순.
        고를 때마다 올리기 직전 확인(recheck) — 품절이거나 값이 오른 딜은 빼고 다음 딜로."""
        bot = self.bot
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        cands: list[tuple[Any, Any]] = []
        for it in self._published(day_start, now):
            shop = bot.registry.get(it.deal.product.shop)
            info = insta_style.card_info(it.deal, bot.renderer, shop=shop, deal_no=bot._page_deal_no(it.id))
            if need_drop and not ((info.ref_price and info.ref_pct) or info.low_label):
                continue
            cands.append((it, info))
        if need_drop:
            cands.sort(key=lambda c: (c[1].ref_pct or 0, 1 if c[1].low_label else 0, c[0].score), reverse=True)
        else:
            cands.sort(key=lambda c: (c[0].score, c[1].ref_pct or 0), reverse=True)
        picks: list[Pick] = []
        dropped: list[str] = []
        for it, info in cands:
            if len(picks) >= limit:
                break
            pid = it.deal.product.product_id
            post = self.db.latest_post(pid)
            why = await self.recheck(it.deal, since=from_iso(post["posted_at"]) if post else it.created_at)
            if why:
                dropped.append(f"{pid} {why}")
                log.info("instagram bundle: %s left out — %s", pid, why)
                continue
            picks.append(Pick(deal=it.deal, info=info, deal_no=info.deal_no, photo=self._photo_for(it.id), queue_id=it.id))
        return picks, dropped

    def _save_slides(self, specs: list[SlideSpec], stamp: str) -> list[str] | None:
        """장들을 JPEG 로 그려 공개 폴더(media)에 두고 공개 주소를 돌려준다. 공개 도메인·카드가 없으면 None."""
        base = self.bot.settings.secrets.public_base_url
        card = self.bot.card
        if not base or card is None:
            return None
        self.media_dir.mkdir(parents=True, exist_ok=True)
        urls: list[str] = []
        for i, spec in enumerate(specs, 1):
            name = f"ig_{stamp}_{i:02d}.jpg"
            (self.media_dir / name).write_bytes(card_slides.to_jpeg(card_slides.render(card, spec)))
            urls.append(f"{base}/media/{name}")
        return urls

    @staticmethod
    def _stamp(kind: str, local: datetime, now: datetime) -> str:
        return f"{kind}_{local:%Y%m%d}_{int(now.timestamp()) % 100000:05d}"

    # ------------------------------------------------------------ 저녁 TOP5 캐러셀
    def top5_specs(self, picks: list[Pick], local: datetime) -> list[SlideSpec]:
        k = len(picks)
        n = k + 2
        title = f"오늘 가격 내려간 딜 TOP{k}"
        rows = []
        for pk in picks:
            info = pk.info
            sub = f"{info.ref_text} ({info.ref_pct}%↓)" if info.ref_text and info.ref_pct else (info.low_label or "")
            rows.append((pk.label, f"{info.price:,}원" if info.price else "", sub))
        cover = SlideSpec(
            kind="cover", ad=GENERIC_AD, head=insta_style.date_kr(local), tag=f"1/{n}", title=title,
            big_note="우리가 기록한 가격·비교 근거가 있는 딜만 골랐어요", items=rows,
            note="가격·재고는 올린 시점 기준이에요", footer="구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로",
            alt=f"{title} 표지 — " + ", ".join(f"{a} {b}" for a, b, _ in rows),
        )
        deals = [
            card_slides.deal_spec(pk.info, pk.photo, tag=f"{i + 2}/{n}", head=f"{title} · {i + 1}") for i, pk in enumerate(picks)
        ]
        end = SlideSpec(
            kind="end", ad=GENERIC_AD, tag=f"{n}/{n}", title="실시간 전체 딜은\n텔레그램에 먼저", big="프로필 링크",
            note="가격·재고는 올린 시점 기준이에요. 구매 링크도 프로필 링크의 핫딜 페이지에 있어요.", footer=card_slides.BRAND,
            alt="실시간 전체 딜은 텔레그램에 먼저 올라와요. 프로필 링크에서 볼 수 있어요.",
        )
        return [cover, *deals, end]

    async def _post_top5(self, now: datetime, local: datetime) -> str:
        cc = self.cfg.carousel
        picks, dropped = await self._picks(now, local, limit=cc.max_deals, need_drop=True)
        if len(picks) < cc.min_deals:
            return f"skipped: {len(picks)} deal(s) with price data today (need {cc.min_deals})" + (
                f", left out: {'; '.join(dropped)}" if dropped else "")
        specs = self.top5_specs(picks, local)
        caption = insta_style.top5_caption(picks, self.bot.registry, local)
        problems = insta_style.validate(caption, slides=specs, allowed=[a for pk in picks for a in insta_style.backed_phrases(pk.info)])
        if problems:
            return f"skipped: {CHECK_FAILED}: {'; '.join(problems)}"
        urls = await asyncio.to_thread(self._save_slides, specs, self._stamp("top5", local, now))
        if urls is None:
            return "skipped: 공개 도메인·카드가 없어 사진을 못 올림"
        result = await self.bot.instagram.publish_carousel(list(zip(urls, [s.alt for s in specs], strict=True)), caption)
        return await self._finish("top5", result, picks, now)

    # ------------------------------------------------------------ 점심 릴스
    def reel_specs(self, picks: list[Pick], local: datetime) -> list[SlideSpec]:
        k = len(picks)
        head = f"점심 TOP{k}"
        # 첫 1초: 가격이 내려간 폭 — 데이터 근거(시중가·평소 가격)가 있는 딜이 있을 때만 '19,800원 → 15,900원'
        lead = next((pk for pk in picks if pk.info.ref_price and pk.info.ref_pct and pk.info.ref_text), picks[0])
        info = lead.info
        was = note = ""
        if info.ref_price and info.ref_pct and info.ref_text:
            was = info.ref_text.rpartition(" ")[2]
            note = f"{info.ref_label} 대비 {info.ref_pct}%↓"
        hook = SlideSpec(
            kind="reel_hook", ad=GENERIC_AD, head=head, tag=f"{local:%m/%d}", title=info.name,
            big=f"{info.price:,}원" if info.price else "", was=was, big_note=note,
            note=f"오늘 텔레그램에 올린 딜 중 {k}개 · 가격은 올린 시점 기준",
        )
        frames = [hook]
        for i, pk in enumerate(picks, 1):
            rows = [r for r in deal_rows(pk.info) if r[0] not in ("가격", "배송", "평점", "판매")]
            rows.append(("구매", f"프로필 링크 → 핫딜 페이지 #{pk.deal_no}" if pk.deal_no else "프로필 링크 → 핫딜 페이지"))
            frames.append(SlideSpec(
                kind="reel_deal", ad=ad_of(pk.info), head=head, tag=f"{i}/{k}", title=pk.info.name,
                big=f"{pk.info.price:,}원" if pk.info.price else "", rows=rows, photo=pk.photo,
            ))
        frames.append(SlideSpec(kind="reel_end", ad=GENERIC_AD, title="실시간 전체 딜은\n텔레그램", big="프로필 링크",
                                note="가격·재고는 올린 시점 기준이에요"))
        return frames

    def _encode_reel(self, ffmpeg: str, specs: list[SlideSpec], video: Path, cover: Path) -> None:
        card = self.bot.card
        lengths = reel.durations(len(specs) - 2)
        with tempfile.TemporaryDirectory(prefix="reel_", dir=Path(self.bot.settings.data_dir)) as tmp:
            frames: list[tuple[Path, float]] = []
            for i, (spec, seconds) in enumerate(zip(specs, lengths, strict=True)):
                img = card_slides.render(card, spec)
                path = Path(tmp) / f"f{i:02d}.png"
                img.save(path, "PNG")
                frames.append((path, seconds))
                if i == 0:
                    cover.write_bytes(card_slides.to_jpeg(img))  # 표지 = 첫 장 (가격이 내려간 폭)
            reel.encode(ffmpeg, frames, video)

    async def _ffmpeg_missing(self) -> None:
        """ffmpeg 가 없어 릴스를 못 만듦: 주인 할 일로 한 번만 알린다 (설치하면 다음 날부터 저절로)."""
        if self.db.kv_get(KV_FFMPEG):
            return
        self.db.kv_set(KV_FFMPEG, to_iso(utcnow()))
        await self.bot.notifier.alert(
            "ig_ffmpeg",
            "⚠️ <b>인스타 점심 릴스를 못 만들었어요</b> — 서버에 영상 도구 ffmpeg 가 없어요.\n"
            "서버에서 <code>sudo apt-get install -y ffmpeg</code> 한 줄이면 돼요 "
            "(또는 <code>bash deploy/lightsail/setup.sh</code> 다시 실행). 설치 전까지 릴스만 건너뛰고 다른 인스타 글은 그대로 올라가요.",
            kakao_fit("⚠️[할 일] 인스타 릴스용 ffmpeg 설치", "→ 서버에서 sudo apt-get install -y ffmpeg"),
            key="ig_ffmpeg", short="릴스용 ffmpeg 설치",
        )

    async def _post_reel(self, now: datetime, local: datetime) -> str:
        rc = self.cfg.reels
        picks, dropped = await self._picks(now, local, limit=rc.max_deals, need_drop=False)
        if len(picks) < rc.min_deals:
            return f"skipped: {len(picks)} deal(s) today (need {rc.min_deals})" + (f", left out: {'; '.join(dropped)}" if dropped else "")
        ffmpeg = reel.find_ffmpeg()
        if not ffmpeg:
            await self._ffmpeg_missing()
            return "skipped: ffmpeg not installed"
        self.db.kv_set(KV_FFMPEG, "")  # 설치됨 — 나중에 다시 없어지면 또 알리게
        base = self.bot.settings.secrets.public_base_url
        if not base or self.bot.card is None:
            return "skipped: 공개 도메인·카드가 없어 영상을 못 올림"
        specs = self.reel_specs(picks, local)
        caption = insta_style.reel_caption(picks, self.bot.registry, local)
        problems = insta_style.validate(caption, slides=specs, allowed=[a for pk in picks for a in insta_style.backed_phrases(pk.info)])
        if problems:
            return f"skipped: {CHECK_FAILED}: {'; '.join(problems)}"
        stamp = self._stamp("reel", local, now)
        video, cover = self.media_dir / f"{stamp}.mp4", self.media_dir / f"{stamp}_cover.jpg"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._encode_reel, ffmpeg, specs, video, cover)
        result = await self.bot.instagram.publish_reel(
            f"{base}/media/{video.name}", caption, cover_url=f"{base}/media/{cover.name}", timeout=rc.processing_timeout_seconds,
        )
        return await self._finish("reel", result, picks, now)

    # ------------------------------------------------------------ 화·금 생필품 1개당 가격
    def unit_rows(self, now: datetime, days: int, *, skip_sold_out: bool) -> list[UnitRow]:
        """최근 days 일 채널에 올린 딜 중 생필품(휴지·물티슈·생수·세제·기저귀)이고, 우리 가격 기록이 low_min_samples 번 이상이며,
        이름으로 1개당 가격을 셀 수 있는 것."""
        bot = self.bot
        need = bot.renderer.low_min_samples
        rows: list[UnitRow] = []
        for it in self._published(now - timedelta(days=days), now):
            p = it.deal.product
            if skip_sold_out and self._known_sold_out(p.product_id):
                continue
            name = clean_name(p.name)
            got = insta_style.unit_kind(name, unit_price(name, p.price, p.category))
            if got is None:
                continue
            if self.db.price_stats(p.product_id, 30, now=now).count < need:
                continue
            title, kind, value = got
            rows.append(UnitRow(title=title, kind=kind.name, label=kind.label, value=value,
                                name=insta_style.ig_name(p.extra.get("short_name") or name)[:28], price=p.price,
                                deal_no=bot._page_deal_no(it.id), posted=it.created_at, product_id=p.product_id))
        return rows

    def unit_specs(self, week: dict[str, list[UnitRow]], baselines: list[tuple[str, str, int, int]], deals: list[Deal],
                   local: datetime, since: datetime) -> list[SlideSpec]:
        uc = self.cfg.unit_carousel
        n = 1 + len(week) + (1 if baselines else 0)
        need = self.bot.renderer.low_min_samples
        period = f"{since:%m/%d}~{local:%m/%d}"
        cover = SlideSpec(
            kind="cover", ad=GENERIC_AD, head=insta_style.date_kr(local), tag=f"1/{n}", title="이번 주 생필품 1개당 가격",
            big_note=f"{period} 텔레그램에 올린 딜 · 우리가 기록한 가격으로 계산했어요",
            items=[(title, f"{len(rows)}개", " · ".join(dict.fromkeys(f"{r.kind} {r.label}" for r in rows))) for title, rows in week.items()],
            note="가격·재고는 올린 시점 기준이에요", footer="구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로",
            alt="이번 주 생필품 1개당 가격 표지 — " + ", ".join(week),
        )
        specs = [cover]
        by_id = {d.product.product_id: d for d in deals}
        for i, (title, rows) in enumerate(week.items(), 2):
            ad = insta_style.ad_line(
                deal_disclosure(self.bot.registry.get(by_id[r.product_id].product.shop), by_id[r.product_id].product.extra)
                for r in rows if r.product_id in by_id
            )
            items = [(f"#{r.deal_no} {r.name}" if r.deal_no else r.name, f"{r.value:,}원", f"{r.kind} {r.label} · 판매가 {r.price:,}원")
                     for r in rows]
            specs.append(SlideSpec(
                kind="list", ad=ad, head="이번 주 생필품 1개당 가격", tag=f"{i}/{n}", title=title,
                big_note="1개당 가격 낮은 순 · 우리가 기록한 가격", items=items,
                note=f"가격 기록이 {need}번 이상인 상품만 · {period} 텔레그램에 올린 딜",
                footer="구매 링크: 프로필 링크 → 핫딜 페이지에서 번호로",
                alt=f"{title} 1개당 가격 — " + ", ".join(f"{a} {b}" for a, b, _ in items),
            ))
        if baselines:
            items = [(f"{kind} {label}", f"{value:,}원", f"최근 {uc.baseline_days}일 우리가 올린 딜 {cnt}건의 가운데 값")
                     for kind, label, value, cnt in baselines]
            specs.append(SlideSpec(
                kind="list", ad=GENERIC_AD, head="우리 가격 기록으로 본", tag=f"{n}/{n}", title="이 가격 밑이면 사도 되는 기준선",
                big_note=f"1개당 가격이 이보다 낮으면 우리가 최근 {uc.baseline_days}일 동안 올린 딜의 절반보다 싸요",
                items=items, note=f"{local:%m/%d %H}시 기준 · 우리가 기록한 가격만 썼어요", footer=card_slides.BRAND,
                alt="생필품 1개당 가격 기준선 — " + ", ".join(f"{a} {b}" for a, b, _ in items),
            ))
        return specs

    async def _post_unit(self, now: datetime, local: datetime) -> str:
        uc = self.cfg.unit_carousel
        week_rows = self.unit_rows(now, uc.lookback_days, skip_sold_out=True)
        order = [title for title, _ in insta_style.UNIT_GROUPS]
        week: dict[str, list[UnitRow]] = {}
        for title in order:
            rows = sorted((r for r in week_rows if r.title == title), key=lambda r: (r.kind, r.value))
            if rows:
                week[title] = rows[:UNIT_LISTING]
        if len(week) < uc.min_slides:
            return f"skipped: {len(week)} category slide(s) with our price data (need {uc.min_slides})"
        values: dict[tuple[str, str], list[int]] = {}
        for r in self.unit_rows(now, uc.baseline_days, skip_sold_out=False):
            values.setdefault((r.kind, r.label), []).append(r.value)
        baselines = []
        for (kind, label), vals in values.items():
            mid = insta_style.baseline(vals)
            if mid is not None:
                baselines.append((kind, label, mid, len(vals)))
        listed = {r.product_id for rows in week.values() for r in rows}
        deals = [it.deal for it in self._published(now - timedelta(days=uc.lookback_days), now) if it.deal.product.product_id in listed]
        since = (now - timedelta(days=uc.lookback_days)).astimezone(self.tz)
        specs = self.unit_specs(week, baselines, deals, local, since)
        caption = insta_style.unit_caption(week, baselines, deals, self.bot.registry, since=since, until=local)
        problems = insta_style.validate(caption, slides=specs)
        if problems:
            return f"skipped: {CHECK_FAILED}: {'; '.join(problems)}"
        urls = await asyncio.to_thread(self._save_slides, specs, self._stamp("unit", local, now))
        if urls is None:
            return "skipped: 공개 도메인·카드가 없어 사진을 못 올림"
        result = await self.bot.instagram.publish_carousel(list(zip(urls, [s.alt for s in specs], strict=True)), caption)
        return await self._finish("unit", result, [r for rows in week.values() for r in rows], now)
