"""인스타 글마다 숫자 (주인 10/26 — '데미얌의 쇼핑일기' 2주 시험: 어떤 글 모양이 더 나은지 비교할 숫자가 없었음).

한 시간에 한 번, 올린 지 하루~8일 된 진짜 글(ig_posts — 딜 계정과 데런픽 룩 둘 다)의 인사이트를 읽어 그 줄에 남긴다
(도달·저장·공유·조회·좋아요·댓글, 릴스는 평균 시청 시간까지). 같은 글은 하루에 한 번만 다시 읽고, 한 번에 MAX_CALLS 개까지.
인사이트 권한이 없다고 한 번 안 토큰으로는 다시 부르지 않는다 (publisher.scopes — 대시보드 조회수와 같은 기억).
대시보드 통계 탭의 인스타 카드에 '최근 글 숫자' 목록으로 보인다 (dashboard/sources/instagram.py).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from dealbot.dashboard.sources._meta import insight_value, is_permission, is_token_problem
from dealbot.publisher.scopes import IG_INSIGHTS

log = logging.getLogger(__name__)

MIN_AGE = timedelta(hours=24)  # 숫자가 어느 정도 쌓인 뒤부터
MAX_AGE = timedelta(days=8)  # 2주 시험은 글이 올라간 첫 주 숫자로 비교
REREAD = timedelta(hours=24)  # 같은 글은 하루에 한 번만 다시 읽음
MAX_CALLS = 8  # 한 번 돌 때 부르는 글 수 (나머지는 다음 시간)
BASE = ("reach", "saved", "shares", "likes", "comments", "views")
REEL_EXTRA = ("ig_reels_avg_watch_time",)
SAFE = ("reach", "saved", "shares", "likes", "comments")  # 'views' 를 모르는 오래된 글·버전용


def metrics_for(kind: str) -> tuple[str, ...]:
    return BASE + REEL_EXTRA if kind == "reel" else BASE


def parse(body: Any, names: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name in names:
        if (v := insight_value(body, name)) is not None:
            out[name] = v
    return out


async def read_one(pub: Any, token: Any, row: dict[str, Any]) -> dict[str, float] | None:
    """글 하나. 지표 이름 때문에 거절되면 안전한 묶음으로 한 번 더. 권한·토큰 문제는 그대로 올린다."""
    names = metrics_for(str(row.get("kind") or ""))
    try:
        return parse(await pub.client.media_insights(token, str(row["media_id"]), ",".join(names)), names)
    except Exception as e:  # noqa: BLE001
        if is_permission(e) or is_token_problem(e):
            raise
        log.info("ig insights %s: %s — 기본 지표로 다시", row.get("media_id"), e)
    return parse(await pub.client.media_insights(token, str(row["media_id"]), ",".join(SAFE)), SAFE)


async def collect(bot: Any, now: datetime) -> bool:
    """한 번 돌기. 하나라도 읽었으면 True."""
    rows = bot.db.ig_insight_targets(now - MAX_AGE, now - MIN_AGE)
    rows = [r for r in rows if r.get("insights_at") is None or now - r["insights_at"] >= REREAD][:MAX_CALLS]
    if not rows:
        return False
    done = 0
    tokens: dict[str, Any] = {}
    for row in rows:
        account = "look" if row.get("kind") == "look" else "main"
        pub = bot.ig_publisher(account)
        scopes = getattr(pub, "scopes", None)
        if scopes is not None and not scopes.usable(IG_INSIGHTS):
            continue
        if account not in tokens:
            tokens[account] = await pub.ensure_fresh()
        token = tokens[account]
        if token is None:
            continue
        try:
            values = await read_one(pub, token, row)
        except Exception as e:  # noqa: BLE001
            if is_permission(e) and not is_token_problem(e) and scopes is not None:
                scopes.mark(IG_INSIGHTS, False)
            log.warning("ig insights %s (%s) 못 읽음: %s", row.get("media_id"), account, e)
            tokens[account] = None  # 이 계정은 이번 차례 그만
            continue
        if values is None:
            continue
        bot.db.ig_set_insights(int(row["id"]), values, now)
        done += 1
    return done > 0
