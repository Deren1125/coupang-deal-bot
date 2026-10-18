"""스레드 리듬·주제 태그·품절 업데이트 답글·답글 초안·투표 (주인 결정 10/15).

- 리듬: 텔레그램에 올린 딜(·정보 글)은 스레드 대기열(threads_jobs)에 넣고, 07:00~24:00 에만 · 글 사이 45분 이상 ·
  딜 글은 하루 10개까지, 점수 높은 것부터 올린다. 오래(8시간) 기다린 글은 내리고, 10분 넘게 기다린 딜은 올리기 직전에 품절을 다시 본다.
- 사진: 확인된 상품 사진이 있으면 그 사진, 없으면 봇이 만든 4:5 글자 카드 (글만 올리지 않음). 정보 글은 게시판 사진을 쓰지 않는다.
- 주제 태그: 분류별 (config threads.topic_tags — 주방·식품 '집밥', 생활용품 '살림', 운동 '헬스', 패션 '패션', 기본 '핫딜').
- 품절: 내 스레드 글 아래에만 '업데이트) HH:MM 기준 품절됐어' 답글 하나 (글마다 한 번). 남의 글·남의 답글에는 아무것도 안 단다.
- 답글: 하루 두 번(12:30·20:30대) 내 최근 글의 새 답글을 읽어 관리자 챗에 '원글 요약 + 답글 + 답변 초안 2개'
  (초안은 봇이 저장한 딜 사실만 — 사양을 지어내지 않음) + 주인 할 일 한 줄. 봇이 답글을 달지는 않는다.
- 투표: 15시까지 스레드 딜 글이 없는 날 링크 없는 투표 글 하나 (공식 poll_attachment, 하루 한 번까지, 태그 '살림').
"""

from __future__ import annotations

import html
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.config import ThreadsConfig
from dealbot.media.card import NEUTRAL_CTA
from dealbot.models import Deal, Product, PublishResult
from dealbot.monitoring.admin import kakao_fit
from dealbot.publisher.scopes import THREADS_READ_REPLIES
from dealbot.publisher.threads import KV_USERNAME, POLL_OPTION_KEYS
from dealbot.storage.sns_store import ThreadsJob
from dealbot.utils.text import clean_name, truncate
from dealbot.utils.timeutil import from_iso, in_time_window

log = logging.getLogger(__name__)

KIND_DEAL, KIND_INFO, KIND_POLL = "deal", "info", "poll"
SOLDOUT_REPLY = "업데이트) {hhmm} 기준 품절됐어"
REPLY_LOOKBACK_SOLDOUT = timedelta(hours=48)  # 이보다 오래된 내 글에는 품절 답글을 달지 않음
FAIL_BACKOFF = timedelta(minutes=10)  # 올리다 실패하면 이만큼 쉬고 다음 글 (스레드가 막혔을 때 밀린 글이 한꺼번에 실패하지 않게)
NO_AUTO_REPLY = "(봇은 답글을 달지 않아요 — 고르거나 고쳐서 스레드 앱에서 직접 달아 주세요)"


def topic_tag(cfg: ThreadsConfig, p: Product) -> str:
    """분류(쿠팡 카테고리 칸) → 상품명 순으로 config 의 낱말을 찾아 주제 태그. 못 찾으면 기본('핫딜')."""
    cat = " ".join(s.strip() for s in (p.category or "").split(">") if s.strip())
    name = clean_name(p.name)
    for hay in (cat, name):
        if not hay:
            continue
        for tag, words in cfg.topic_tags.items():
            if any(w and w in hay for w in words):
                return tag
    return cfg.topic_tag_default


def day_start_utc(now: datetime, tz: str) -> datetime:
    local = now.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(ZoneInfo("UTC"))


class ThreadsFlow:
    """DealBot 에 붙어 스레드 대기열을 돌린다 (bot: DealBot — db·settings·threads·notifier·make_card·_page_sold_out 를 씀)."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._failed_at: datetime | None = None
        self._posting: set[int] = set()  # 이 프로세스가 지금 올리는 글 (그 밖의 'posting' 은 재시작으로 끊긴 글)

    @property
    def cfg(self) -> ThreadsConfig:
        return self.bot.settings.threads

    @property
    def tz(self) -> str:
        return self.bot.settings.app.timezone

    def active(self) -> bool:
        th = self.bot.threads
        return bool(self.cfg.enabled and (th.configured or th.dry_run))

    # ------------------------------------------------------------ 넣기
    def enqueue_deal(self, deal: Deal, *, queue_id: int | None, has_photo: bool) -> int | None:
        """채널에 올린 딜을 스레드 대기열에. has_photo: 텔레그램 글에 확인된 상품 사진이 있었는지 (없으면 글자 카드로 올림)."""
        payload = {"deal": deal.to_dict(), "has_photo": has_photo}
        return self.bot.db.threads_enqueue(KIND_DEAL, deal.product.product_id, payload, queue_id=queue_id,
                                           score=float(deal.verdict.score or 0.0), now=self.bot.clock())

    def enqueue_info(self, deal: Deal, text: str, *, queue_id: int | None = None) -> int | None:
        """정보 글 (게시판 사진 없이 요약 + 원문 링크)."""
        payload = {"text": text, "topic_tag": topic_tag(self.cfg, deal.product), "name": deal.product.name}
        return self.bot.db.threads_enqueue(KIND_INFO, deal.product.product_id, payload, queue_id=queue_id,
                                           score=float(deal.verdict.score or 0.0), now=self.bot.clock())

    # ------------------------------------------------------------ 리듬
    def rhythm_block(self, now: datetime) -> str | None:
        """지금 올리면 안 되는 이유 (리듬). 괜찮으면 None."""
        rc = self.cfg.rhythm
        if not rc.enabled:
            return None
        if not in_time_window(now.astimezone(ZoneInfo(self.tz)), rc.hours):
            return f"시간대 밖 ({rc.hours})"
        last = self.bot.db.threads_last_posted_at()
        if last is not None and now - last < timedelta(minutes=rc.min_gap_minutes):
            return f"글 사이 {rc.min_gap_minutes:g}분"
        return None

    def deal_cap_reached(self, now: datetime) -> bool:
        rc = self.cfg.rhythm
        if not rc.enabled or not rc.max_deal_posts_per_day:
            return False
        return self.bot.db.threads_posted_count(day_start_utc(now, self.tz), kind=KIND_DEAL) >= rc.max_deal_posts_per_day

    def next_job(self, now: datetime) -> ThreadsJob | None:
        cap = self.deal_cap_reached(now)
        for job in self.bot.db.threads_waiting(50):
            if job.kind == KIND_DEAL and cap:
                continue
            return job
        return None

    async def tick(self, now: datetime | None = None) -> bool:
        """리듬이 허락하면 대기열에서 글 하나를 올린다. 무언가 올렸거나 내렸으면 True."""
        bot = self.bot
        if not self.active() or bot.state.paused:
            return False
        now = now or bot.clock()
        ttl = timedelta(hours=self.cfg.rhythm.queue_ttl_hours)
        for pid in bot.db.threads_fail_stale_posting(self._posting, now):  # 올리다 끊긴 글 (재시작) — 두 번 올리지 않음
            bot.db.sns_log_add("threads", "failed", product_id=pid, reason="maybe posted (stopped while posting)", now=now)
            log.warning("threads job for %s was cut off while posting — not retried (it may be on threads already)", pid)
        for pid in bot.db.threads_expire(now - ttl, now):  # 하루 0개 알림의 이유에 남김
            bot.db.sns_log_add("threads", "skipped", product_id=pid, reason="waited too long", now=now)
            log.info("threads queue: dropped %s (waited over %gh)", pid, self.cfg.rhythm.queue_ttl_hours)
        if self.rhythm_block(now):
            return False
        if self._failed_at is not None and now - self._failed_at < FAIL_BACKOFF:
            return False
        job = self.next_job(now)
        if job is None:
            return False
        await self.post_job(job, now)
        return True

    async def post_now(self, job_id: int) -> bool:
        """리듬 없이 바로 (threads.rhythm.enabled: false — 예전처럼 텔레그램 발행 직후)."""
        job = self.bot.db.threads_job(job_id)
        if job is None or job.status != "waiting":
            return False
        await self.post_job(job, self.bot.clock())
        return True

    # ------------------------------------------------------------ 올리기
    async def post_job(self, job: ThreadsJob, now: datetime) -> PublishResult:
        bot = self.bot
        # API 를 부르기 전에 'posting' 으로 — 올리는 사이에 꺼지면 다음 tick 이 실패로 정리하고 다시 올리지 않는다 (인스타 대기열과 같은 방식)
        if not bot.db.threads_claim(job.id, now):
            return PublishResult(ok=False, error="skipped")  # 이미 다른 쪽이 올리는 중이거나 내려진 글
        self._posting.add(job.id)
        try:
            return await self._post_claimed(job, now)
        finally:
            self._posting.discard(job.id)

    async def _post_claimed(self, job: ThreadsJob, now: datetime) -> PublishResult:
        bot = self.bot
        try:
            if job.kind == KIND_DEAL:
                result = await self._post_deal(job, now)
            elif job.kind == KIND_POLL:
                result = await self._post_poll(job, now)
            else:
                result = await bot.threads.publish_text(str(job.payload.get("text") or ""), None,
                                                        topic_tag=job.payload.get("topic_tag"))
        except Exception as e:  # noqa: BLE001 — 재시도 뒤에도 남은 5xx·429·시간 초과: 실패로 기록하고 다음 글로
            result = PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
        if result is None:  # 올리기 직전에 내림 (품절)
            return PublishResult(ok=False, error="skipped")
        pid = job.product_id
        if result.ok:
            self._failed_at = None
            post_id = str(result.message_id) if result.message_id is not None else None
            bot.db.threads_update(job.id, status="posted", post_id=post_id, dry_run=result.dry_run, now=now)
            bot.db.sns_log_add("threads", "posted", product_id=pid, now=now)
            bot.db.log_event("INFO", "threads", f"{pid} {'dry-run' if result.dry_run else post_id}")
            log.info("threads posted (%s): %s", job.kind, pid)
            if job.kind == KIND_DEAL and post_id and not result.dry_run:
                await self._keep_permalink(job.id, result.message_id)
            if result.error:  # 올라가긴 했지만 사진이 빠졌거나 (선택) 답글이 실패한 경우
                bot.db.log_event("WARNING", "threads", f"{pid}: {result.error}")
                if result.error.startswith("답글"):
                    if getattr(bot, "router", None) is not None:  # 주인 10/18: 정리의 '소식' 한 줄 (관리자 챗 대신)
                        bot.notifier.owner_line({"kind": "sns_failed", "key": f"threads_reply_fail:{pid}"[:120], "urgent": False,
                                                 "text": "스레드 글은 올라갔지만 답글이 실패했어요", "short": "스레드 답글 실패",
                                                 "ttl_h": 24})
                    await bot.notifier.send(f"⚠️ <b>스레드 글은 올라갔지만 답글이 실패했습니다</b> (링크는 본문에 있음)\n"
                                            f"<code>{html.escape(result.error)}</code>")
            return result
        error = result.error or "unknown error"
        blocked = error.startswith("스레드 글 규칙 위반")
        if not blocked:
            self._failed_at = now
        bot.db.threads_update(job.id, status="skipped" if blocked else "failed", reason=error[:300], now=now)
        bot.db.sns_log_add("threads", "skipped" if blocked else "failed", product_id=pid, reason=error, now=now)
        bot.db.log_event("WARNING", "threads", f"{pid}: {error}")
        log.warning("threads post failed: %s", error)
        await bot._sns_failed("스레드", "threadsauth", f"⚠️ <b>스레드 발행 실패</b>\n<code>{html.escape(error)}</code>", error)
        return result

    async def _keep_permalink(self, job_id: int, message_id: int | None) -> None:
        """올린 딜 글의 주소를 남긴다 (대시보드 딜 줄 '스레드 ↗'). 못 읽어도 발행은 그대로 — 그 줄에 버튼만 없음."""
        lookup = getattr(self.bot, "_threads_permalink", None)
        if lookup is None:
            return
        try:
            link = await lookup(message_id)
            if link:
                self.bot.db.threads_set_permalink(job_id, link)
        except Exception as e:  # noqa: BLE001
            log.info("threads permalink not kept for job %s: %s", job_id, e)

    async def _post_deal(self, job: ThreadsJob, now: datetime) -> PublishResult | None:
        bot = self.bot
        deal = Deal.from_dict(job.payload["deal"])
        p = deal.product
        sold = from_iso(bot.db.kv_get(f"soldout_at:{p.product_id}") or None)
        if sold is not None and sold >= job.created_at:  # 넣은 뒤에 품절 확인 (예전 품절 뒤 다시 올라온 딜은 그대로)
            return self._skip(job, now, "sold out")
        waited = now - job.created_at
        if waited >= timedelta(minutes=self.cfg.rhythm.recheck_after_minutes) and await bot._page_sold_out(p):
            bot.mark_sold_out_seen(p.product_id, now)
            return self._skip(job, now, "sold out (page, before threads)")
        card_url = None
        if not job.payload.get("has_photo") or not p.image_url:
            # 확인된 상품 사진이 없는 딜: 글만 올리지 않고 봇이 만든 4:5 글자 카드 (상품명·가격 같은 사실만, 주인 결정 10/15)
            # 바닥 안내는 채널 중립 — 스레드 본문에 구매 링크가 있어 인스타용 '프로필 링크 → 핫딜 페이지' 는 맞지 않음.
            # 같은 까닭으로 위 띠에 핫딜 페이지 번호 '#N' 도 안 그린다 (번호를 안 넘김). 고지 띠는 그대로
            card_url = await bot.make_card(deal, None, text_card=True, cta=NEUTRAL_CTA)
        return await bot.threads.publish(deal, fallback_image_url=card_url, topic_tag=topic_tag(self.cfg, p))

    async def _post_poll(self, job: ThreadsJob, now: datetime) -> PublishResult:
        bot = self.bot
        question = str(job.payload.get("question") or "")
        options = [str(o) for o in (job.payload.get("options") or [])]
        result = await bot.threads.publish_text(question, topic_tag=job.payload.get("topic_tag"), poll=options)
        if result.ok and result.message_id is not None and not result.dry_run:
            bot.db.poll_add(str(result.message_id), question, options, now=now)
        return result

    def _skip(self, job: ThreadsJob, now: datetime, why: str) -> None:
        self.bot.db.threads_update(job.id, status="skipped", reason=why, now=now)
        self.bot.db.sns_log_add("threads", "skipped", product_id=job.product_id, reason=why, now=now)
        log.info("threads job #%d skipped: %s", job.id, why)
        return None

    # ------------------------------------------------------------ 품절 업데이트
    async def soldout_update(self, product_id: str, now: datetime | None = None) -> int:
        """품절 확인: 기다리는 스레드 글은 내리고, 이미 올린 내 글 아래에 사실 답글 하나 (글마다 한 번, config soldout_reply)."""
        bot = self.bot
        now = now or bot.clock()
        bot.db.threads_skip_product(product_id, "sold out", now)
        if not self.cfg.soldout_reply or not self.active():
            return 0
        n = 0
        hhmm = now.astimezone(ZoneInfo(self.tz)).strftime("%H:%M")
        for job in bot.db.threads_posts_for_product(product_id, now - REPLY_LOOKBACK_SOLDOUT):
            if not job.post_id or not bot.db.threads_mark_soldout_reply(job.id, now):
                continue  # 이미 달았음 (실패했어도 다시 두드리지 않음 — 글마다 한 번)
            result = await bot.threads.publish_reply(job.post_id, SOLDOUT_REPLY.format(hhmm=hhmm))
            if result.ok:
                n += 1
                bot.db.log_event("INFO", "threads", f"{product_id} 품절 업데이트 답글 ({job.post_id})")
            else:
                log.warning("threads sold-out reply failed for %s: %s", job.post_id, result.error)
        return n

    # ------------------------------------------------------------ 답글 → 관리자 챗 초안
    def drafts(self, job: ThreadsJob, reply_text: str) -> tuple[str, str]:
        """답변 초안 2개 — 봇이 저장한 사실(올린 시각·가격·비교 금액·배송·단가)만 쓴다. 모르는 건 '링크에서 확인'으로."""
        when = (job.posted_at or job.created_at).astimezone(ZoneInfo(self.tz)).strftime("%m/%d %H:%M")
        if job.kind == KIND_POLL:
            return ("투표 고마워! 다른 분류도 계속 찾아볼게", "의견 고마워, 많이 고른 것부터 더 챙겨 볼게")
        if job.kind == KIND_INFO:
            return (f"올린 {when} 기준 원문 내용이야. 기간·대상 조건은 원문 링크에서 한 번 더 확인해 줘",
                    "정리한 건 원문 기준이라 그 뒤에 바뀌었을 수 있어 — 원문 링크가 제일 정확해")
        deal = Deal.from_dict(job.payload["deal"])
        p = deal.product
        facts = self.bot.renderer.deal_facts(deal)
        first = (f"올린 {when} 기준 {p.price:,}원이었어. 지금 가격·재고는 링크에서 한 번 더 확인해 줘" if p.has_price
                 else f"올린 {when} 기준 정보야. 조건은 링크에서 한 번 더 확인해 줘")
        bits: list[str] = []
        if facts.get("compare"):
            bits.append(f"비교한 값은 {facts['compare']}였어")
        if facts.get("unit_each"):
            bits.append(f"{facts['unit_each']}이고")
        if facts.get("ship"):
            bits.append(f"배송은 {facts['ship']}로 나와 있었어")
        asks = any(w in reply_text for w in ("?", "몇", "언제", "어디", "뭐", "무슨", "사이즈", "용량", "구성", "옵션"))
        if bits and not asks:
            second = " · ".join(bits[:2])
        else:
            second = "옵션·구성은 링크 상품 페이지에 나온 그대로라, 거기서 보는 게 제일 정확해"
        return first, second

    def reply_message(self, job: ThreadsJob, reply: dict[str, Any]) -> str:
        """관리자 챗 한 통: 원글 요약 + 답글 + 답변 초안 2개."""
        when = (job.posted_at or job.created_at).astimezone(ZoneInfo(self.tz)).strftime("%m/%d %H:%M")
        if job.kind == KIND_DEAL:
            p = Deal.from_dict(job.payload["deal"]).product
            price = f" {p.price:,}원" if p.has_price else ""
            head = f"{truncate(clean_name(p.name), 50)}{price}"
        elif job.kind == KIND_POLL:
            head = f"투표: {job.payload.get('question') or ''}"
        else:
            head = truncate(str(job.payload.get("name") or "정보 글"), 50)
        text = str(reply.get("text") or "").strip()
        who = str(reply.get("username") or "").strip()
        d1, d2 = self.drafts(job, text)
        num = f" #{job.queue_id}" if job.queue_id else ""
        return (
            f"💬 <b>스레드 답글{num}</b> (원글 {when})\n"
            f"원글: {html.escape(head)}\n"
            f"답글{(' @' + html.escape(who)) if who else ''}: {html.escape(truncate(text, 300))}\n\n"
            f"답변 초안 1: {html.escape(d1)}\n"
            f"답변 초안 2: {html.escape(d2)}\n"
            f"{NO_AUTO_REPLY}"
        )

    def store_reply(self, router: Any, job: ThreadsJob, reply: dict[str, Any], now: datetime) -> bool:
        """대시보드 '스레드 답글' 시트에 한 줄 (원글 요약·답글·답변 초안 2개·스레드에서 열기). 봇은 답글을 달지 않는다."""
        if job.kind == KIND_DEAL:
            p = Deal.from_dict(job.payload["deal"]).product
            head = f"{truncate(clean_name(p.name), 50)}" + (f" {p.price:,}원" if p.has_price else "")
        elif job.kind == KIND_POLL:
            head = f"투표: {job.payload.get('question') or ''}"
        else:
            head = truncate(str(job.payload.get("name") or "정보 글"), 50)
        text = str(reply.get("text") or "").strip()
        d1, d2 = self.drafts(job, text)
        link = reply.get("permalink") if isinstance(reply.get("permalink"), str) else None
        return router.store.add_reply(reply_id=str(reply.get("id")), post_id=str(job.post_id), post_excerpt=head,
                                      reply_text=truncate(text, 300), username=str(reply.get("username") or "")[:40],
                                      drafts=[d1, d2], open_url=link if link and link.startswith("https://") else None,
                                      now=now.timestamp())

    async def collect_replies(self, now: datetime | None = None, *, slot: str = "") -> int:
        """내 최근 글의 새 답글 → 관리자 챗 (소리 없이 한 통씩) + 주인 할 일 한 줄. 권한(threads_read_replies)이 없으면 조용히 0."""
        bot = self.bot
        rc = self.cfg.replies
        if not rc.enabled or not self.active() or bot.threads.dry_run:
            return 0
        now = now or bot.clock()
        if not bot.threads.scopes.usable(THREADS_READ_REPLIES):
            return 0
        me = (bot.db.kv_get(KV_USERNAME) or "").lower()
        fresh: list[tuple[ThreadsJob, dict[str, Any]]] = []
        for job in bot.db.threads_posted_since(now - timedelta(hours=rc.lookback_hours))[: rc.max_posts]:
            try:
                rows = await bot.threads.read_replies(str(job.post_id))
            except Exception as e:  # noqa: BLE001 — 한 글을 못 읽어도 다음 글
                log.info("threads replies for %s not read: %s", job.post_id, e)
                continue
            if rows is None:  # 권한 없음 (기억함) — 이번엔 그만
                return 0
            for r in rows:
                rid = str(r.get("id") or "")
                if not rid or bot.db.reply_seen(rid):
                    continue
                if r.get("is_reply_owned_by_me") or (me and str(r.get("username") or "").lower() == me):
                    bot.db.mark_reply_seen(rid, str(job.post_id), now)  # 내 답글 (품절 업데이트 등)
                    continue
                fresh.append((job, r))
        sent = 0
        router = getattr(bot, "router", None)
        for job, r in fresh[: rc.max_messages]:
            if router is not None:  # 주인 10/18: 관리자 챗 대신 대시보드 '스레드 답글' (답글마다 한 줄 + 초안 2개, 7일)
                self.store_reply(router, job, r, now)
                bot.db.mark_reply_seen(str(r.get("id")), str(job.post_id), now)
                sent += 1
            elif await bot.notifier.send(self.reply_message(job, r), silent=True):
                bot.db.mark_reply_seen(str(r.get("id")), str(job.post_id), now)
                sent += 1
        if sent:
            local = now.astimezone(ZoneInfo(self.tz))
            await bot.notifier.alert(
                "threads_replies", None,
                kakao_fit(f"🔔[할 일] 스레드 답글 {sent}개 — 답변 초안 2개씩 보냈어요",
                          "→ 텔레그램 핫딜봇의 💬 스레드 답글 메시지 (봇은 답글을 달지 않아요)"),
                key=f"threads_replies:{local:%Y%m%d}{slot}", short=f"스레드 답글 {sent}개", group="threads_replies", ttl_h=12,
            )
            log.info("threads replies: %d new sent to the admin chat", sent)
        return sent

    # ------------------------------------------------------------ 투표
    def poll_options(self, now: datetime) -> list[str]:
        pool = [o.strip() for o in self.cfg.poll.options if o and o.strip()]
        if len(pool) <= 4:
            return pool
        k = now.astimezone(ZoneInfo(self.tz)).toordinal() % len(pool)
        return [pool[(k + i) % len(pool)] for i in range(4)]

    async def maybe_poll(self, now: datetime | None = None) -> bool:
        """오늘 after(15:00)까지 스레드 딜 글이 없고 기다리는 딜도 없으면 링크 없는 투표 글 하나 (하루 한 번까지)."""
        bot = self.bot
        pc = self.cfg.poll
        if not pc.enabled or not self.active():
            return False
        now = now or bot.clock()
        local = now.astimezone(ZoneInfo(self.tz))
        hh, _, mm = pc.after.partition(":")
        if local.hour * 60 + local.minute < int(hh) * 60 + int(mm):
            return False
        day = local.date().isoformat()
        if bot.db.kv_get("threads_poll_day") == day:
            return False
        if bot.db.threads_posted_count(day_start_utc(now, self.tz), kind=KIND_DEAL):
            return False  # 오늘은 딜 글이 있었음
        if any(j.kind == KIND_DEAL for j in bot.db.threads_waiting(50)):
            return False  # 곧 올라갈 딜이 있음
        options = self.poll_options(now)
        if len(options) < 2:
            return False
        payload = {"question": pc.question, "options": options, "topic_tag": pc.topic_tag}
        bot.db.kv_set("threads_poll_day", day)
        if bot.db.threads_enqueue(KIND_POLL, f"poll:{day}", payload, score=-1.0, now=now) is None:
            return False
        log.info("threads poll queued for %s: %s", day, options)
        await self.tick(now)
        return True

    async def refresh_poll_results(self, now: datetime | None = None, *, include_running: bool = False) -> int:
        """끝난(24시간 지난) 투표 결과를 읽어 둔다. include_running: 아직 진행 중인 것도 지금 값으로."""
        bot = self.bot
        if not self.cfg.enabled or bot.threads.dry_run:
            return 0
        now = now or bot.clock()
        token = None
        n = 0
        for poll in bot.db.polls_since(now - timedelta(days=8)):
            posted = from_iso(poll["posted_at"]) or now
            done = now - posted >= timedelta(hours=24)
            read_at = from_iso(poll.get("results_at"))
            if read_at is not None and read_at - posted >= timedelta(hours=24):
                continue  # 끝난 뒤에 읽은 값이 있음
            if not done and not include_running:
                continue
            token = token or await bot.threads.ensure_fresh()
            if token is None:
                return n
            try:
                raw = await bot.threads.client.poll_results(token, str(poll["post_id"]))
            except Exception as e:  # noqa: BLE001
                log.info("threads poll results not read (%s): %s", poll["post_id"], e)
                continue
            bot.db.poll_set_results(str(poll["post_id"]), poll_summary(poll.get("options") or [], raw), now=now)
            n += 1
        return n


def poll_summary(options: list[str], raw: dict[str, Any]) -> dict[str, Any]:
    """API 결과 → {'options': [{'label', 'pct'}], 'total': N}. 비율은 API 값(0~1 비율 또는 0~100)을 % 로."""
    vals: list[tuple[str, float]] = []
    for key, label in zip(POLL_OPTION_KEYS, options, strict=False):
        try:
            pct = float(raw.get(f"{key}_votes_percentage") or 0)
        except (TypeError, ValueError):
            pct = 0.0
        vals.append((str(raw.get(key) or label), max(0.0, pct)))
    fraction = 0 < sum(v for _, v in vals) <= 1.01  # 0~1 비율로 온 값
    rows = [{"label": label, "pct": round(v * 100 if fraction else v)} for label, v in vals]
    try:
        total = int(raw.get("total_votes") or 0)
    except (TypeError, ValueError):
        total = 0
    return {"options": sorted(rows, key=lambda r: -r["pct"]), "total": total}


def skip_reasons(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(str(r.get("reason") or r.get("outcome") or "?") for r in rows if r.get("outcome") != "posted")
