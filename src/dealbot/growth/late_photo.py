"""확인된 상품 사진 없이 올라간 딜의 사진 나중에 받기 (핫딜 블로그·인스타용, 주인 결정 10/15 — 딜봇 쪽).

채널에 사진 없이 올라간 딜이면 관리자 챗에 소리 없이 한 통 '📷 사진 필요 #N …' + 주인 할 일 한 줄.
4시간(publish.late_photo_hours) 안에 그 메시지에 사진을 답장하거나 사진 설명 칸에 '/photo N' 을 쓰면
링크와 같이 보내는 네이버 딜 사진과 같은 검사(형식·EXIF 방향·크기·짧은 변 600px)를 거쳐 그 딜의 상품 사진이 되고,
Blog-Auto 가 읽는 published_deals.jsonl 에 사진이 바뀐 줄을 하나 더 쓴다 (같은 product_id·posted_at, photo_kind 'owner').
채널 글은 이미 올라갔으니 고치지 않는다.
인스타: 기다리는 동안 인스타 시간표는 사진 없는 딜 글을 미루고(ig_scheduler — 사진 있는 딜이 먼저), 사진이 오면 그 사진으로 올린다.
이미 글자 카드로 올라간 뒤면 답장에 그렇게 적는다.
받는 창은 photo_wait_until 보다 CLOSE_EARLY 만큼 일찍 닫는다 — Blog-Auto 는 그 시각이 지나면 글자 카드로 쓰므로
사진 받기(텔레그램 다운로드·검사·내보내기)가 그 뒤에 끝나 '블로그에 써요' 라고 해 놓고 못 쓰는 일이 없게.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.monitoring.admin import kakao_fit, product_links
from dealbot.utils.text import clean_name, truncate
from dealbot.utils.timeutil import from_iso, to_iso

log = logging.getLogger(__name__)

KV = "late_photo:"  # late_photo:{딜 번호} → {"until", "posted_at", "product_id"}
CLOSE_EARLY = timedelta(minutes=2)  # 받는 창을 이만큼 일찍 닫음 (Blog-Auto 가 until 에 글자 카드로 넘어가기 전에 다 받게)
MSG_KIND = "photo_need"
ASK = "📷 사진 필요 #{n} {name} — 4시간 안에 이 메시지에 사진을 답장하면 블로그·인스타에 그 사진을 써요"


def ask_text(queue_id: int, name: str, hours: float) -> str:
    """관리자 챗 한 통 (텔레그램 HTML — 상품명은 이스케이프)."""
    text = ASK.format(n=queue_id, name=html.escape(truncate(clean_name(name), 40), quote=False))
    return text if hours == 4 else text.replace("4시간", f"{hours:g}시간")


class LatePhoto:
    def __init__(self, bot: Any) -> None:
        self.bot = bot

    @property
    def hours(self) -> float:
        return float(self.bot.settings.publish.late_photo_hours)

    def enabled(self) -> bool:
        return bool(self.bot.settings.publish.late_photo)

    def wait_until(self, posted_at: datetime) -> datetime:
        return posted_at + timedelta(hours=self.hours)

    async def ask(self, queue_id: int, deal: Any, posted_at: datetime) -> bool:
        """사진 없이 올라간 딜: 관리자 챗 한 통(소리 없이, 답장 자리) + 주인 할 일. 보냈으면 True."""
        bot = self.bot
        if not self.enabled() or deal.product.deal_kind == "info":
            return False
        until = self.wait_until(posted_at)
        bot.db.kv_set(f"{KV}{queue_id}", json.dumps(
            {"until": to_iso(until), "posted_at": to_iso(posted_at), "product_id": deal.product.product_id}))
        text = ask_text(queue_id, deal.product.name, self.hours)
        mid = await bot.notifier.send_with_id(text, silent=True, kind=MSG_KIND, queue_id=queue_id)
        hhmm = until.astimezone(ZoneInfo(bot.settings.app.timezone)).strftime("%H:%M")
        await bot.notifier.alert(
            "photo_need", None,
            kakao_fit(f"🔔[할 일] 사진 필요 #{queue_id} {truncate(clean_name(deal.product.name), 50)}",
                      f"→ {hhmm} 까지 텔레그램 핫딜봇 📷 메시지에 사진 답장 (없으면 블로그는 글자 카드)"),
            key=f"photo:{queue_id}", short=f"딜 사진 #{queue_id}", group="photo_need", ttl_h=self.hours,
            links=product_links(deal.product),  # 상품 페이지 — 알림에서 바로 열어 사진을 받게 (주인 10/18)
        )
        bot.db.log_event("INFO", "late_photo", f"#{queue_id} 사진 요청 (until {to_iso(until)})")
        return mid is not None

    def waiting(self, queue_id: int, now: datetime | None = None) -> dict[str, Any] | None:
        """이 딜이 지금 사진을 받는 중이면 그 기록, 아니면 None (시간이 지났거나 받은 뒤). 창은 until 보다 CLOSE_EARLY 일찍 닫힘."""
        info = self._info(queue_id)
        if info is None:
            return None
        until = from_iso(info.get("until"))
        now = now or self.bot.clock()
        return info if until is not None and now <= until - CLOSE_EARLY else None

    def until(self, queue_id: int) -> datetime | None:
        """사진을 기다리는 딜이면 기다림이 끝나는 시각 (photo_wait_until), 아니면 None (묻지 않았거나 이미 받음).
        인스타 시간표가 이때까지 이 딜의 사진 없는 글을 미룬다."""
        info = self._info(queue_id)
        return from_iso(info.get("until")) if info else None

    def prune(self, now: datetime | None = None) -> int:
        """시간이 지났거나 받은 사진 기다림 기록을 지운다 (유지보수, 하루 몇 개씩 쌓임)."""
        now = now or self.bot.clock()

        def keep(_key: str, value: str | None) -> bool:
            try:
                info = json.loads(value or "null")
            except ValueError:
                return False
            until = from_iso(info.get("until")) if isinstance(info, dict) else None
            return until is not None and now <= until + timedelta(days=1)

        return int(self.bot.db.kv_delete_prefix(KV, keep=keep))

    def _info(self, queue_id: int) -> dict[str, Any] | None:
        try:
            info = json.loads(self.bot.db.kv_get(f"{KV}{queue_id}") or "null")
        except ValueError:
            return None
        return info if isinstance(info, dict) else None

    async def attach(self, queue_id: int, photo: Any) -> str:
        """주인이 보낸 사진을 그 딜의 상품 사진으로 (검사 → 저장 → 내보내기 줄). 주인에게 보일 답 (HTML)."""
        bot = self.bot
        info = self._info(queue_id)
        if info is None:
            return f"#{queue_id} 번은 사진을 기다리는 딜이 아니에요 (📷 사진 필요 메시지가 온 딜만 받아요)."
        now = bot.clock()
        until = from_iso(info.get("until"))
        late = (f"⌛ #{queue_id} 사진은 {self.hours:g}시간이 지나 받지 않았어요 — 블로그는 이미 글자 카드로 정리했을 수 있어요.")
        if until is None or now > until - CLOSE_EARLY:
            return late
        item = bot.db.get_queue_item(queue_id)
        if item is None or item.status != "published":
            return f"#{queue_id} 번은 채널에 올라간 딜이 아니라 사진을 붙이지 않았어요."
        jpeg, why = await bot._check_owner_photo(photo)
        if jpeg is None:
            return (f"⚠️ #{queue_id} 사진을 쓸 수 없어요: {html.escape(why, quote=False)}\n다른 사진을 이 메시지(📷 사진 필요)에 답장으로 다시 보내 주세요 "
                    "(JPG·PNG·WEBP, 10MB 이하, 짧은 변 600px 이상).")
        if bot.clock() > until:  # 사진을 받는(내려받기·검사) 사이에 기다림이 끝남 — 블로그가 이미 글자 카드로 넘어갔을 수 있음
            return late
        saved = bot.save_late_photo(item, jpeg)
        if not saved:
            return "사진을 저장하지 못했어요 (서버 디스크) — 잠시 뒤 다시 보내 주세요"
        posted_at = from_iso(info.get("posted_at")) or now
        bot.export_photo_update(item.deal, jpeg, posted_at=posted_at)
        bot.db.kv_set(f"{KV}{queue_id}", "")
        bot.notifier.resolve(f"photo:{queue_id}")
        bot.db.log_event("INFO", "late_photo", f"#{queue_id} 주인 사진 받음")
        return f"✅ #{queue_id} 사진을 받았어요 — {self._where(queue_id)} (채널 글은 그대로)"

    def _where(self, queue_id: int) -> str:
        """사진을 어디에 쓰는지 — 인스타 딜 글이 아직 기다리는지, 이미 (글자 카드로) 올라갔는지에 따라."""
        try:
            statuses = {str(r.get("status")) for r in self.bot.db.ig_items_for(queue_id)}
        except Exception:  # noqa: BLE001 — 인스타 대기열을 못 읽어도 답은 보냄
            statuses = set()
        if "queued" in statuses:
            return "블로그·인스타에 이 사진을 써요"
        if statuses & {"posted", "posting"}:
            return "블로그엔 이 사진을 써요 — 인스타 딜 글은 이미 글자 카드로 올라갔어요"
        return "블로그에 이 사진을 써요"
