"""핫딜 블로그 새 글 → '오픈톡방에 보내기' 앱 알림 (주인 10/24 — 카톡은 오픈톡방 전송 API 가 없어 1번 방식: 알림 → 한 번 눌러 보내기).

1분마다 (scheduler.dashboard_loop) Blog-Auto 기록 파일의 핫딜 최근 글(recent.hotdeal)을 본다.
오픈채팅 문구(share)가 있는 새 글이면 앱 알림 한 통 — 누르면 대시보드 #/share/<글번호> 가 열리고
그 글의 '오픈채팅에 보내기' 판이 바로 뜬다 (버튼 → 휴대폰 공유창 → 카톡 → 오픈톡방 여러 개 골라 보내기).
처음 한 번은 지금 글들을 기억만 한다 (지난 글로 알림이 쏟아지지 않게). 기억은 kv openchat_watch:seen (최근 KEEP 개).
할 일 줄을 만들지 않는다 (알림만) — 기기가 없으면 아무것도 보내지 않는다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)

KV_SEEN = "openchat_watch:seen"
KEEP = 200


def fresh(export: Any) -> list[dict[str, Any]]:
    """오픈채팅 문구가 있는 핫딜 최근 글 (글번호 있는 것만)."""
    v2 = (getattr(export, "v2", None) or {}) if export is not None else {}
    rows = (v2.get("recent") or {}).get("hotdeal") or []
    return [r for r in rows if isinstance(r, dict) and r.get("pid") and r.get("share")]


async def check(bot: Any) -> str | None:
    """1분마다. 새 핫딜 글마다 앱 알림 (보낸 줄 요약), 없으면 None."""
    router = getattr(bot, "router", None)
    state = getattr(bot, "dash_state", None)
    if router is None or state is None:
        return None
    rows = fresh(state._export())
    raw = bot.db.kv_get(KV_SEEN)
    try:
        seen = json.loads(raw) if raw else None
    except ValueError:
        seen = None
    pids = [int(r["pid"]) for r in rows]
    if not isinstance(seen, list):  # 처음: 기억만
        bot.db.kv_set(KV_SEEN, json.dumps(pids[:KEEP]))
        return None
    new = [r for r in rows if int(r["pid"]) not in seen]
    if not new:
        return None
    bot.db.kv_set(KV_SEEN, json.dumps(([int(r["pid"]) for r in new] + seen)[:KEEP]))
    out = []
    for r in reversed(new):  # 먼저 발행된 글부터
        title = str(r.get("title") or "")
        sent = await router.push_event("📣 오픈톡방에 보낼 새 핫딜 글", f"{title} — 눌러서 오픈톡방에 보내기",
                                       url=f"/dashboard/#/share/{int(r['pid'])}", tag=f"openchat-{int(r['pid'])}")
        log.info("open chat share alert: hotdeal #%s pushed to %d device(s)", r["pid"], sent)
        out.append(f"#{r['pid']} (알림 {sent}대)")
    return ", ".join(out)
