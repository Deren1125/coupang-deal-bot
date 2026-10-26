"""인스타그램 대기열·시간표 (주인 결정 10/15).

텔레그램 채널에 올린 딜은 바로 인스타에 올리지 않고 인스타 대기열(DB ig_queue — 재시작해도 남음)에 넣는다. 1분마다 tick():
- 시간: 07:00~23:00 (instagram.hours) 에만. 한 번 볼 때 많아야 글 하나.
- 딜 하나짜리 글 (주인 10/18): '할인 괜찮은 딜'(publisher/good_deal — top·must 또는 할인율 30% 이상)만, 가벼운 안전 한도로 —
  하루 10개까지(daily_singles), 점수 높은 순, 인스타 글 사이 30~50분 무작위(min_gap_minutes~min_gap_max_minutes), 캐러셀·릴스·단가표가
  잡힌 시각 앞 30분(slot_guard_minutes)은 비워 둔다. 보통 딜은 줄은 서되(사진 사본 — 릴스·TOP5 가 씀) 'bundle' 로 두고 단일 글로는 안 올린다. 올리기 직전에 품절·가격 오름을 다시 확인하고(채널 품절 기록 → 그 뒤 수집기가 본 가격 → 상품 페이지),
  품절이거나 값이 올랐으면 건너뛴다(사유 기록). 12시간(queue_expire_hours) 넘게 기다린 딜은 안 올린다.
  사진 없이 줄 선 딜은 주인 사진을 기다리는 동안(growth/late_photo — 발행 뒤 4시간) 미루고 사진 있는 딜을 먼저 올린다
  (사진이 오면 그 사진으로, 안 오면 그 뒤 글자 카드로). 같은 상품이 더 싸게 다시 올라오면 기다리던 줄을 새 딜·번호로 바꾼다.
  카드에 그리는 글도 올리기 전에 검사한다 (첫 부분 고지 — B 카드의 위 띠).
- 점심 릴스(12~13시 아무 분): 그날 텔레그램에 올린 딜 중 점수 위 3개 → 10~15초 세로 영상 (서버 ffmpeg).
- 저녁 캐러셀(20~21시 아무 분): '오늘 가격 내려간 딜 TOP5' — 비교 근거(쿠팡 최저가·평소 가격·우리 기록 최저)가 있는 딜만.
- 화·금 단가표(19~20시 아무 분, TOP5 와 min_gap_minutes 와 한 시간 중 큰 값 넘게 — 안 되면 적어도 한 시간 떨어지게.
  되는 분 중에서 바로 고른다): '이번 주 생필품 1개당 가격' — 봇이 기록한 가격만. 장(목록) 하나엔 한 단위만('휴지 1롤당'·'물티슈 1매당'은
  따로), 그 단위의 기준선은 우리가 올린 딜이 3건 이상일 때만 그 장에.
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
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.media import card_paper, card_slides, reel
from dealbot.media.card import CardInfo
from dealbot.media.card_slides import GENERIC_AD, SlideSpec, ad_of, deal_rows
from dealbot.models import Deal, PublishResult
from dealbot.monitoring.admin import kakao_fit
from dealbot.page_price import check_page_price
from dealbot.publisher import insta_style
from dealbot.publisher.good_deal import good_deal_reason
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
QUEUE_LOOK = 50  # 한 번 볼 때 살펴보는 대기열 줄 (사진 기다리느라 미룬 줄은 건너뛰고 그 뒤를 봄)
HOLD_MARGIN = timedelta(minutes=30)  # 사진을 기다리다 대기열 시간(12시간)이 끝나지 않게 이만큼 앞에서 기다림을 멈춤
PHOTO_KEEP_SECONDS = 36 * 3600  # 상품 사진 사본: 그날 캐러셀·릴스가 쓰고 나면 지움
REEL_KEEP_SECONDS = 2 * 86400
UNIT_LISTING = 4  # 단가표 한 장(한 단위)에 넣는 딜 수
OWN_DROP_DAYS = 30  # 릴스 취소선의 '바로 앞 가격'은 우리 가격 기록에서 이 안의 것만
OWN_DROP_MIN_PCT = 10  # 바로 앞 가격보다 이만큼 이상 내렸을 때만 '전 → 지금' (비교 근거와 같은 10% 기준)
SCHEDULED_GAP_MINUTES = 60  # 단가표와 TOP5 캐러셀은 적어도 이만큼 떨어지게 (되면 min_gap_minutes 만큼)
BUNDLE_ONLY = "bundle"  # ig_queue 상태: 보통 딜 (단일 글 없이 릴스·TOP5 사진 사본만 — 주인 10/18)
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


def paper_fit(card: object, spec: SlideSpec, alt: Callable[[list[tuple[str, str, str]]], str]) -> SlideSpec:
    """종이 카드(P) 표지·단가표 장: 자리가 모자라 그려지지 않는 줄은 spec 에서도 뺀다 — 대체 텍스트·검사가 그림과 같은 줄을 보게."""
    if getattr(card, "layout", "") != card_slides.PAPER:
        return spec
    k = card_paper.items_that_fit(spec)
    if k >= len(spec.items):
        return spec
    items = spec.items[:k]
    return replace(spec, items=items, alt=alt(items))


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
        good = self._good(deal)
        if not good and queue_id is not None and self.db.ig_items_for(queue_id):
            item_id = None  # 같은 채널 글은 한 번만 (보통 딜은 사진 사본만 있으면 됨)
        else:
            item_id = self.db.ig_enqueue(deal, queue_id=queue_id, score=deal.verdict.score, photo_file=photo_file, now=now)
        if item_id is None:
            if photo_file:
                Path(photo_file).unlink(missing_ok=True)
            log.info("instagram queue: %s is already waiting", pid)
            return None
        if not good:
            # 주인 10/18: 보통 딜은 딜 하나짜리 글로 안 올림 — 릴스·TOP5 만 (사진 사본은 그쪽이 씀). 대기열 시간이 지나 '버림'으로 남지 않게 따로 둔다
            self.db.ig_update(item_id, status=BUNDLE_ONLY, reason="not a good-discount deal (reel/TOP5 only)", now=now)
            log.info("instagram: %s kept for reel/TOP5 only (not a good-discount deal)", pid)
            return item_id
        self.db.log_event("INFO", "instagram", f"{pid} queued for instagram (#{item_id}, score {deal.verdict.score:.0f})")
        log.info("instagram queued #%d: %s", item_id, deal.product.name[:40])
        return item_id

    def attach_photo(self, queue_id: int, photo: bytes, now: datetime | None = None) -> int:
        """발행 뒤 받은 주인 사진 (growth/late_photo): 사진 없이 줄 선 이 딜의 인스타 행에 사본을 붙인다 —
        단일 글·TOP5·릴스가 글자 카드 대신 이 사진을 쓴다. 붙인 행 수 (없으면 사본도 지움)."""
        now = now or utcnow()
        try:
            self.photo_dir.mkdir(parents=True, exist_ok=True)
            path = self.photo_dir / (hashlib.sha1(f"late|{queue_id}|{now.isoformat()}".encode()).hexdigest()[:16] + ".jpg")
            path.write_bytes(photo)
        except OSError as e:
            log.warning("instagram queue: late photo copy failed (%s)", e)
            return 0
        n = self.db.ig_set_photo(queue_id, str(path), now=now)
        if not n:
            path.unlink(missing_ok=True)
        else:
            self.db.log_event("INFO", "instagram", f"#{queue_id} late owner photo attached to the instagram queue")
        return n

    def _expire(self, now: datetime) -> None:
        cutoff = now - timedelta(hours=self.cfg.queue_expire_hours)
        for row in self.db.ig_expire(cutoff, now=now):
            self.db.log_event("INFO", "instagram", f"{row['product_id']} not posted: {row['reason']}")
            self.db.sns_log_add("instagram", row["status"], product_id=row["product_id"], reason=row["reason"], now=now)
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
            plan["unit"] = self._pick_apart(uc.window, plan.get("top5"))
        self.db.kv_set(key, json.dumps(plan))
        log.info("instagram plan %s: %s", local.date().isoformat(), plan)
        return plan

    def _pick(self, spec: str) -> str:
        start, end = _window(spec)
        m = self.rng.randrange(start, end)
        return f"{m // 60:02d}:{m % 60:02d}"

    def _pick_apart(self, spec: str, other: str | None) -> str:
        """구간 안에서 other(TOP5 시각)와 인스타 글 간격(min_gap_minutes, 2시간) 넘게 떨어진 분을 바로 고른다.
        구간이 붙어 있어 그런 분이 없으면 적어도 SCHEDULED_GAP_MINUTES(1시간), 그것도 없으면 가장 먼 분 (같은 시간대에 캐러셀 두 개가 나가지 않게)."""
        start, end = _window(spec)
        if other is None:
            return self._pick(spec)
        o = _minutes(other)
        minutes = range(start, end)
        for gap in (max(self.cfg.min_gap_minutes, SCHEDULED_GAP_MINUTES), SCHEDULED_GAP_MINUTES):
            ok = [m for m in minutes if abs(m - o) >= gap]
            if ok:
                m = self.rng.choice(ok)
                break
        else:
            m = max(minutes, key=lambda x: abs(x - o))
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
        done = await self._pick_share(now, local, plan)
        if done:
            return done
        return await self._single(now, local, plan)

    async def _pick_share(self, now: datetime, local: datetime, plan: dict[str, str]) -> str | None:
        """데런픽 패션 아닌 글 → 인스타 (주인 10/23 — growth/blog_share). 딜 하나짜리 글과 같은 간격·묶음 글 앞 30분 비움."""
        from dealbot.growth import blog_share

        recs = blog_share.due(blog_share.share_dir(self.bot.settings.dashboard.blogauto_metrics_path), self.db, now)
        if not recs:
            return None
        last = self.db.ig_last_post_at()
        if last is not None and now - last < self.gap_after(last):
            return None
        guard = timedelta(minutes=self.cfg.slot_guard_minutes)
        for kind, slot in plan.items():
            if not self._done(kind, local) and _at(local, _minutes(slot)) - local < guard:
                return None
        status = await blog_share.post_one(self, recs[0], now)
        log.info("instagram %s", status)
        return status

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
            await self._crashed(kind, f"{type(e).__name__}: {e}", now)
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
        last = self.db.ig_last_post_at()
        if last is not None and now - last < self.gap_after(last):
            return None
        guard = timedelta(minutes=cfg.slot_guard_minutes)
        for kind, slot in plan.items():
            if self._done(kind, local):
                continue  # 이미 올렸거나 건너뛴 묶음 글 (올리다 끊긴 것도 — 같은 날 다시 안 올림)
            if _at(local, _minutes(slot)) - local < guard:
                return None  # 곧 캐러셀·릴스·단가표 차례 — 그 앞 30분은 비워 둔다
        tries = 0
        for item in self.db.ig_queued(QUEUE_LOOK):
            if not self._good(item["deal"]):  # 이 규칙 전에 줄 선 보통 딜 (주인 10/18) — 릴스·TOP5 에만
                self.db.ig_update(item["id"], status=BUNDLE_ONLY, reason="not a good-discount deal (reel/TOP5 only)", now=now)
                continue
            if self._photo_hold(item, now):
                continue  # 주인 사진을 기다리는 사진 없는 딜 — 사진 있는 딜이 먼저
            if tries >= RECHECK_TRIES:
                break
            tries += 1
            deal: Deal = item["deal"]
            why = await self.recheck(deal, since=item["created_at"])
            if why:
                self._mark(item, "skipped", reason=why, now=now)
                self.db.log_event("INFO", "instagram", f"{deal.product.product_id} not posted: {why}")
                log.info("instagram queue #%d skipped — %s: %s", item["id"], why, deal.product.name[:40])
                continue
            return await self._post_single(item, now)
        return None

    def _good(self, deal: Deal) -> bool:
        """할인 괜찮은 딜인가 (publisher/good_deal — 주인 10/18). 아니면 딜 하나짜리 글로 안 올림."""
        return good_deal_reason(deal, getattr(self.bot, "renderer", None), self.bot.settings.publish.good_deal_min_discount_pct) is not None

    def gap_after(self, last: datetime) -> timedelta:
        """인스타 글 사이 간격 (주인 10/18: 30~50분 무작위). 직전 글 시각으로 정해서 1분마다 볼 때 같은 값 (재시작해도)."""
        lo = self.cfg.min_gap_minutes
        hi = max(lo, self.cfg.min_gap_max_minutes)
        return timedelta(minutes=random.Random(f"ig_gap|{last.isoformat()}").randint(lo, hi))

    def _photo_hold(self, item: dict[str, Any], now: datetime) -> bool:
        """사진 없이 줄 선 딜이고 주인 사진을 기다리는 중이면(growth/late_photo, 발행 뒤 4시간) True — 그동안 딜 글을 미룬다.
        사진이 오면 photo_file 이 붙어 바로 올릴 수 있고, 안 오면 기다림이 끝난 뒤 글자 카드로. 대기열 시간 안에서만 기다린다."""
        if item.get("photo_file") or item.get("queue_id") is None:
            return False
        late = getattr(self.bot, "late_photo", None)
        if late is None:
            return False
        until = late.until(int(item["queue_id"]))
        if until is None:
            return False
        created = item.get("created_at") or now
        last = created + timedelta(hours=self.cfg.queue_expire_hours) - HOLD_MARGIN
        return now < min(until, last)

    # ------------------------------------------------------------ 올리기 직전 확인
    def _known_sold_out(self, pid: str) -> bool:
        """채널 글이 품절로 기록된 상품 (app.known_sold_out — 품절 표시·블로그 품절 줄·스레드가 본 품절)."""
        return bool(self.bot.known_sold_out(pid))

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
        deal_no = bot._page_deal_no(item["queue_id"])  # 카드·본문의 '#번호' 는 링크 페이지에 있는 글만
        photo = _read(item.get("photo_file"))
        # 본문과 카드에 그리는 글을 같이 검사 (TOP5·릴스·단가표처럼) — 카드 첫 부분(위 띠)에 고지가 있어야 함
        slide = self.single_slide(deal, photo, deal_no=deal_no)
        problems = bot.instagram.check(deal, deal_no=deal_no, slides=[slide] if slide is not None else ())
        if problems:
            why = f"{CHECK_FAILED}: {'; '.join(problems)}"
            self._mark(item, "skipped", reason=why[:300], now=now)
            self.db.log_event("WARNING", "instagram", f"{pid} not posted: {why}"[:500])
            return f"single skipped: {pid}"
        self.db.ig_update(item["id"], status="posting", now=now)
        card_url = await bot.make_card(deal, photo, deal_no=deal_no)
        if card_url is None and not bot.instagram.dry_run:
            # 공개 도메인·카드 설정이 없음 (자기 점검이 알림) — 딜마다 실패 알림을 보내지 않고 기록만
            why = "카드 이미지를 못 만듦 (공개 도메인·card 설정)"
            self._mark(item, "skipped", reason=why, now=now)
            self.db.log_event("WARNING", "instagram", f"{pid} not posted: {why}")
            return f"single skipped: {pid}"
        try:
            result = await bot.instagram.publish(deal, card_url, deal_no=deal_no)
        except Exception as e:  # noqa: BLE001 — 재시도 뒤에도 남은 5xx·429·시간 초과
            result = PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
        if result.ok:
            media = "dry-run" if result.dry_run else str(result.message_id or "")
            self._mark(item, "posted", media_id=media or None, now=now)
            row = self.db.ig_record_post("single", media_id=media or None, items=[{"product_id": pid, "queue_id": item["queue_id"]}],
                                         dry_run=result.dry_run, now=now)
            await self._keep_permalink(row, result)
            self.db.log_event("INFO", "instagram", f"{pid} {media}")
            log.info("instagram posted: %s", deal.product.name[:40])
            return f"single: {pid}"
        self._mark(item, "failed", reason=(result.error or "")[:300], now=now)
        await self._failed(result, now, what=pid)
        return f"single failed: {pid}"

    async def _keep_permalink(self, row: int, result: PublishResult) -> None:
        """올린 글의 주소를 남긴다 (대시보드 딜 줄 '인스타 ↗'). 못 읽어도 발행은 그대로 — 그 줄에 버튼만 없음."""
        lookup = getattr(self.bot, "_instagram_permalink", None)
        if result.dry_run or not row or result.message_id is None or lookup is None:
            return
        try:
            link = await lookup(result.message_id)
            if link:
                self.db.ig_set_permalink(row, link)
        except Exception as e:  # noqa: BLE001
            log.info("instagram permalink not kept for post %s: %s", row, e)

    def single_slide(self, deal: Deal, photo: bytes | None, *, deal_no: int | None) -> Any | None:
        """딜 하나짜리 글의 카드에 그리는 글 (검사용). 카드가 없으면 None (그땐 카드를 못 만들어 어차피 안 올림)."""
        bot = self.bot
        card = getattr(bot, "card", None)
        if card is None:
            return None
        shop = bot.registry.get(deal.product.shop)
        info = insta_style.card_info(deal, bot.renderer, shop=shop, deal_no=deal_no)
        return card_slides.drawn_first_part(card.layout, info, photo)

    def _mark(self, item: dict[str, Any], status: str, *, reason: str | None = None, media_id: str | None = None,
              now: datetime) -> None:
        """대기열 딜의 결과를 적는다 + SNS 발행 기록(sns_log — 하루 0개 알림이 이유를 셈)."""
        self.db.ig_update(item["id"], status=status, reason=reason, media_id=media_id, now=now)
        self.db.sns_log_add("instagram", status, product_id=item["deal"].product.product_id, reason=reason, now=now)

    async def _crashed(self, kind: str, error: str, now: datetime) -> None:
        """묶음 글(릴스·TOP5·단가표)이 발행 결과 밖에서 실패 (영상 만들기 ffmpeg 오류 등): 하루 0개 알림이 이유를 셀 수 있게
        sns_log 에 남기고 주인에게 소리 없이 한 통 (그날 그 글은 끝 — 같은 날 다시 안 올림, 다음 날 다시)."""
        self.db.sns_log_add("instagram", "failed", reason=f"{kind}: {error}"[:300], now=now)
        try:
            await self.bot._sns_failed(
                "인스타그램", "instaauth",
                f"⚠️ <b>인스타 {LABELS.get(kind, kind)} 못 올림</b> (오늘은 건너뜀, 내일 다시)\n<code>{html.escape(error[:300])}</code>", error,
            )
        except Exception as e:  # noqa: BLE001 — 알림 실패가 시간표를 멈추지 않게
            log.warning("instagram %s failure notice failed: %s", kind, e)

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
            items = [{"product_id": pk.deal.product.product_id, "queue_id": pk.queue_id} if isinstance(pk, Pick)
                     else {"product_id": pk.product_id, "deal_no": pk.deal_no} for pk in picks]  # 단가표 줄은 대기열 번호가 아니라 페이지 번호
            row = self.db.ig_record_post(kind, media_id=media or None, items=items, dry_run=result.dry_run, now=now)
            await self._keep_permalink(row, result)
            self.db.sns_log_add("instagram", "posted", reason=kind, now=now)  # 묶음 글 (TOP5·릴스·단가표)
            return f"posted {media} ({len(picks)} deals)"
        if result.error == REEL_TIMEOUT:
            return "failed: timeout — 인스타가 영상 처리를 끝내지 않아 포기 (다음 날 다시)"
        self.db.sns_log_add("instagram", "failed", reason=f"{kind}: {result.error}", now=now)
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
            info = replace(self.own_before(it.deal, info), ad_free=False)  # 묶은 글은 글 전체가 광고 → 모든 장에 고지
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

    def own_before(self, deal: Deal, info: CardInfo) -> CardInfo:
        """이 상품의 바로 앞 가격 (우리 가격 기록 — 딜을 본 시각 바로 앞 관측)이 지금보다 OWN_DROP_MIN_PCT 이상 비쌌으면
        info.own_was·own_was_label('우리가 본 10/08 가격')을 채운다. 릴스 첫 장 취소선 '전 → 지금' 은 이것만 쓴다
        (다른 판매처 최저가·표시 정가는 이 상품의 종전 가격이 아님 — 표시광고법)."""
        p = deal.product
        at = deal.detected_at
        if not (info.price and p.product_id and at):
            return info
        got = self.db.price_before(p.product_id, at, at - timedelta(days=OWN_DROP_DAYS))
        if got is None:
            return info
        before, seen = got
        if before <= info.price or (1 - info.price / before) * 100 < OWN_DROP_MIN_PCT:
            return info
        return replace(info, own_was=before, own_was_label=f"우리가 본 {seen.astimezone(self.tz):%m/%d} 가격")

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
            note="가격·재고는 올린 시점 기준이에요", footer="구매 링크: 프로필 링크 → 링크 페이지에서 번호로",
            alt=f"{title} 표지 — " + ", ".join(f"{a} {b}" for a, b, _ in rows),
        )
        cover = paper_fit(self.bot.card, cover, lambda got: f"{title} 표지 — " + ", ".join(f"{a} {b}" for a, b, _ in got))
        if getattr(self.bot.card, "layout", "") == card_slides.PAPER:
            # 종이 카드: 딜 장은 딜 하나짜리 카드와 같은 그림 — 포스트잇에 '#번호' + 구매 안내 한 번, 장 번호는 위 오른쪽
            deals = [card_paper.deal_spec(pk.info, pk.photo, page=f"{i + 2}/{n}") for i, pk in enumerate(picks)]
        else:
            deals = [
                card_slides.deal_spec(pk.info, pk.photo, tag=f"{i + 2}/{n}", head=f"{title} · {i + 1}") for i, pk in enumerate(picks)
            ]
        end = SlideSpec(
            kind="end", ad=GENERIC_AD, tag=f"{n}/{n}", title="실시간 전체 딜은\n텔레그램에 먼저", big="프로필 링크",
            note="가격·재고는 올린 시점 기준이에요. 구매 링크도 프로필 링크의 링크 페이지에 있어요.", footer=card_slides.BRAND,
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
        # 첫 1초: 가격이 내려간 폭. 취소선 '19,900원 → 15,900원' 은 19,900원이 이 상품의 바로 앞 가격(우리 가격 기록)일 때만,
        # 근거 '우리가 본 10/08 가격' 과 함께. 시중가 비교만 있으면 지금 가격을 크게 + 따로 한 줄 '네이버 최저가 19,900원보다 4,000원 ↓'
        # (다른 판매처의 최저가를 취소선으로 그리면 이 상품의 종전 가격처럼 읽힘 — 표시광고법)
        lead = next((pk for pk in picks if pk.info.own_was and pk.info.own_was_label), None)
        lead = lead or next((pk for pk in picks if card_slides.compare_line(pk.info)), picks[0])
        info = lead.info
        was = was_note = note = ""
        if info.own_was and info.own_was_label and info.price:
            was, was_note = f"{info.own_was:,}원", info.own_was_label
            note = f"{info.own_was - info.price:,}원 내려갔어요 ({round((1 - info.price / info.own_was) * 100)}%↓)"
        else:
            note = card_slides.compare_line(info)
        hook = SlideSpec(
            kind="reel_hook", ad=GENERIC_AD, head=head, tag=f"{local:%m/%d}", title=info.name,
            big=f"{info.price:,}원" if info.price else "", was=was, was_note=was_note, big_note=note,
            note=f"오늘 텔레그램에 올린 딜 중 {k}개 · 가격은 올린 시점 기준",
        )
        frames = [hook]
        for i, pk in enumerate(picks, 1):
            rows = [r for r in deal_rows(pk.info) if r[0] not in ("가격", "배송", "평점", "판매")]
            rows.append(("구매", f"프로필 링크 → 링크 페이지 #{pk.deal_no}" if pk.deal_no else "프로필 링크 → 링크 페이지"))
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
        """표지 + 단위 목록마다 한 장 (week: 목록 이름 '휴지 1롤당' → 줄 — 한 장엔 한 단위만). 그 단위의 기준선(우리가 올린 딜
        3건 이상의 가운데 값)이 있으면 그 장 아래에 한 줄. 단위가 다른 값을 한 목록에 세우지 않는다."""
        uc = self.cfg.unit_carousel
        n = 1 + len(week)
        need = self.bot.renderer.low_min_samples
        base = {insta_style.unit_list_name(kind, label): (value, cnt) for kind, label, value, cnt in baselines}
        period = f"{since:%m/%d}~{local:%m/%d}"
        # 링크 페이지(최근 page_hours)에서 내려간 지난 딜은 번호가 없다 — '번호로 찾아 주세요' 라고만 하면 못 찾는 딜이 섞임
        old = any(r.deal_no is None for rows in week.values() for r in rows)
        footer = insta_style.UNIT_FIND_OLD if old else insta_style.UNIT_FIND
        cover = SlideSpec(
            kind="cover", ad=GENERIC_AD, head=insta_style.date_kr(local), tag=f"1/{n}", title="이번 주 생필품 1개당 가격",
            big_note=f"{period} 텔레그램에 올린 딜 · 우리가 기록한 가격으로 계산했어요",
            items=[(title, f"{len(rows)}개", f"{rows[0].title} · {rows[0].label} 가격 낮은 순") for title, rows in week.items()],
            note="가격·재고는 올린 시점 기준이에요", footer=footer,
            alt="이번 주 생필품 1개당 가격 표지 — " + ", ".join(week),
        )
        specs = [paper_fit(self.bot.card, cover, lambda got: "이번 주 생필품 1개당 가격 표지 — " + ", ".join(a for a, _, _ in got))]
        by_id = {d.product.product_id: d for d in deals}
        for i, (title, rows) in enumerate(week.items(), 2):
            ad = insta_style.ad_line(
                deal_disclosure(self.bot.registry.get(by_id[r.product_id].product.shop), by_id[r.product_id].product.extra)
                for r in rows if r.product_id in by_id
            )
            label = rows[0].label
            items = [(f"#{r.deal_no} {r.name}" if r.deal_no else f"{r.name} (지난 딜)", f"{r.value:,}원",
                      f"판매가 {r.price:,}원") for r in rows]  # 단위는 장 제목에 (한 장엔 한 단위)
            line: list[tuple[str, str]] = []
            if title in base:  # 그 단위 딜이 3건 이상일 때만 (insta_style.baseline)
                value, cnt = base[title]
                line = [("사도 되는 기준선", f"{label} {value:,}원"),
                        ("근거", f"최근 {uc.baseline_days}일 우리가 올린 {rows[0].kind} 딜 {cnt}건의 가운데 값 · 이보다 낮으면 그 절반보다 싸요")]
            def list_alt(got: list[tuple[str, str, str]], title: str = title, line: list[tuple[str, str]] = line) -> str:
                return f"{title} 가격 — " + ", ".join(f"{a} {b}" for a, b, _ in got) + (f" · 사도 되는 기준선 {line[0][1]}" if line else "")

            specs.append(paper_fit(self.bot.card, SlideSpec(
                kind="list", ad=ad, head=f"이번 주 생필품 · {rows[0].title}", tag=f"{i}/{n}", title=f"{title} 가격",
                big_note=f"{label} 가격 낮은 순 · 우리가 기록한 가격", items=items, rows=line,
                note=f"가격 기록이 {need}번 이상인 상품만 · {period} 텔레그램에 올린 딜",
                footer=footer, alt=list_alt(items),
            ), list_alt))
        return specs

    async def _post_unit(self, now: datetime, local: datetime) -> str:
        uc = self.cfg.unit_carousel
        week_rows = self.unit_rows(now, uc.lookback_days, skip_sold_out=True)
        # 목록(장) 하나엔 한 단위만: '휴지 1롤당'·'물티슈 1매당'·'액체 세제 100mL당'… 은 따로 세운다 (다른 단위끼리 순위를 매기지 않음)
        week = insta_style.unit_lists(week_rows, UNIT_LISTING)
        categories = {rows[0].title for rows in week.values()}
        if len(categories) < uc.min_slides:
            return f"skipped: {len(categories)} category slide(s) with our price data (need {uc.min_slides})"
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
