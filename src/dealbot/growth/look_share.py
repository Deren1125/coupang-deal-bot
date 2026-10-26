"""두 번째 인스타 '데런픽 룩' @derenpick.look (주인 10/26).

- 연결: 같은 메타 앱에 인스타 테스터로 넣은 뒤 대시보드 연결 탭 '데런픽 룩 연결' (publisher/instagram 의 같은 승인 흐름 —
  state 앞 'look.' 으로 계정 구분, 토큰은 instagram_look_* kv). 연결 전에는 대기열이 기다리기만 하고, 대시보드 할 일에
  '데런픽 룩 인스타 연결 필요' 한 줄 (알림·카톡 없음 — 상태로만 보이고 연결하면 저절로 빠짐).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

LOOK_IID = "sns-look"
TODO_TITLE = "데런픽 룩 인스타 연결 필요"


def look_wanted(bot: Any) -> bool:
    """데런픽 룩 계정을 쓰는 설정인가 (인스타·룩 켜짐 + 메타 앱 키 있음)."""
    s = bot.settings
    return bool(s.instagram.enabled and s.instagram.look.enabled and s.secrets.has_instagram_app)


def look_connected(bot: Any) -> bool:
    pub = getattr(bot, "instagram_look", None)
    return pub is not None and pub.stored_token() is not None


def todo_item(bot: Any, now: float, goto: Callable[..., dict[str, Any]], *, waiting: int = 0) -> dict[str, Any] | None:
    """할 일 한 줄 — 룩 계정을 쓰는데 아직 연결이 없을 때만. waiting: 올릴 차례를 기다리는 패션 글 수 (있으면 상태 줄에)."""
    if not look_wanted(bot) or look_connected(bot):
        return None
    handle = bot.settings.instagram.look.username.lstrip("@")
    status = f"패션 글 {waiting}편이 기다려요 · " if waiting else ""
    status += f"@{handle} 를 메타 앱 인스타 테스터로 넣고 → 연결 탭 '데런픽 룩 연결'"
    return {"iid": LOOK_IID, "src": "dealbot", "blog": "", "kind": "sns_look", "sev": "todo", "urgent": False,
            "title": TODO_TITLE, "status": status, "since": None, "expires_at": None,
            "primary": goto("연결 탭 열기", "#/connect"), "more": [], "detail": {"lines": []}}


def waiting_count(bot: Any) -> int:
    """올릴 차례를 기다리는 데런픽 룩 글 수 (대기열이 아직 없으면 0)."""
    return 0
