"""일요일 텔레그램 'TOP5 + 투표 결과' (주인 결정 10/15, 2단계).

일요일 20:00~21:00 의 아무 분에 한 번 (publish.weekly_top.window): 지난 7일 채널에 실제로 올린 상품 딜 중 점수 높은 5개와
그 주의 스레드 투표 결과. 둘 다 없으면 올리지 않는다. 구매 링크가 있으면 둘째 줄 짧은 광고 표시 + 끝 전체 고지 (딜 글과 같은 규칙).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.growth.slots import mark_slot, slot_due
from dealbot.shops import deal_disclosure, disclosure_program
from dealbot.utils.text import clean_name, truncate
from dealbot.utils.timeutil import from_iso, in_time_window

log = logging.getLogger(__name__)

SLOT = "weekly_top"


def weekly_context(bot: Any, now: datetime) -> dict[str, Any] | None:
    """글에 넣을 것. 보여 줄 딜도 투표 결과도 없으면 None."""
    wc = bot.settings.publish.weekly_top
    tz = bot.settings.app.timezone
    since = now - timedelta(days=7)
    items = [
        it for it in bot.db.published_items_between(since, now + timedelta(seconds=1), include_dry_run=bot.state.dry_run)
        if it.deal.product.deal_kind != "info" and (it.deal.affiliate_url or it.deal.product.url)
    ]
    best: dict[str, Any] = {}
    for it in sorted(items, key=lambda x: x.score, reverse=True):  # 같은 상품은 한 번 (점수 높은 쪽)
        best.setdefault(it.product_id, it)
    top = list(best.values())[: wc.top_n]
    deals, programs, disclosures = [], [], []
    for it in top:
        p = it.deal.product
        shop = bot.registry.get(p.shop)
        disc = deal_disclosure(shop, p.extra)
        if disc:
            prog = disclosure_program(disc) or (shop.name if shop else "제휴")
            if prog not in programs:
                programs.append(prog)
            if disc not in disclosures:
                disclosures.append(disc)
        post = bot.db.latest_post(p.product_id) or {}
        at = from_iso(post.get("posted_at")) or it.created_at
        deals.append({
            "name": truncate(clean_name(p.name), 60),
            "price": p.price if p.has_price else None,
            "posted": at.astimezone(ZoneInfo(tz)).strftime("%m/%d"),
            "link": it.deal.affiliate_url or p.url,
            "link_label": "구매하러 가기" if p.has_price else "보러 가기",
        })
    poll = None
    for row in bot.db.polls_since(since):
        res = row.get("results") or {}
        if res.get("total") and res.get("options"):
            poll = {"question": row["question"], "total": int(res["total"]), "options": res["options"], "post_id": row["post_id"]}
            break
    if not deals and poll is None:
        return None
    return {
        "deals": deals,
        "poll": poll,
        "ad_line": f"광고 · {'·'.join(programs)} 수수료" if programs else None,
        "disclosures": disclosures,
    }


async def weekly_top(bot: Any, now: datetime | None = None, *, force: bool = False) -> str | None:
    """때가 되면(일요일 20시대 아무 분) 채널에 올린다. 올렸으면 글, 아니면 None. force: 시각·요일 확인 없이 (시험용)."""
    wc = bot.settings.publish.weekly_top
    tz = bot.settings.app.timezone
    now = now or bot.clock()
    if not wc.enabled and not force:
        return None
    if not force:
        if now.astimezone(ZoneInfo(tz)).weekday() != wc.weekday or not slot_due(bot.db, SLOT, wc.window, now, tz):
            return None
    try:
        await bot.threads_flow.refresh_poll_results(now, include_running=True)
    except Exception as e:  # noqa: BLE001 — 투표 결과를 못 읽어도 TOP5 는 올린다
        log.info("weekly: poll results not refreshed: %s", e)
    ctx = weekly_context(bot, now)
    mark_slot(bot.db, SLOT, now, tz)
    if ctx is None:
        log.info("weekly top: nothing to show this week")
        return None
    text = bot.renderer.render("weekly_top.j2", **ctx)
    silent = in_time_window(now.astimezone(ZoneInfo(tz)), bot.settings.publish.quiet_hours)
    result = await bot.publisher.publish_raw(text, preview_disabled=True, silent=silent)
    if not result.ok:
        log.warning("weekly top post failed: %s", result.error)
        bot.db.log_event("WARNING", "weekly", f"TOP5 글 실패: {result.error}")
        return None
    if ctx["poll"]:
        bot.db.poll_mark_reported(str(ctx["poll"]["post_id"]), now)
    bot.db.log_event("INFO", "weekly", f"TOP{len(ctx['deals'])} + 투표 {'있음' if ctx['poll'] else '없음'}"
                     + (" (dry-run)" if result.dry_run else ""))
    return text
