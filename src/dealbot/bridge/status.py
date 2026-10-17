"""두 프로그램의 생존 신호 파일 (데이터 폴더). 두 저장소가 따로 자동 업데이트되니, 상대가 새 판인지 여기로 본다.

- blogauto_status.json (Blog-Auto, 60초마다): {"at", "kakao_ok", "kakao_checked", "outbox", "bridge": 1, "actions": [...],
  "kakao": {"state", "relink", "expires_at"}, "route": "push|legacy", "version"} — bridge·actions 가 없으면 예전 판
- dashboard_status.json (딜봇, 60초마다): {"at", "v": 1, "push_router": 1, "push_devices", "push_ok_at", "actions_v": 1}
  — Blog-Auto 는 이게 15분 안이고 push_router ≥ 1 일 때만 알림을 딜봇에 넘긴다 (아니면 예전처럼 카톡)
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BLOGAUTO_FILE = "blogauto_status.json"
DASHBOARD_FILE = "dashboard_status.json"
FRESH_S = 15 * 60
MAX_BYTES = 64 * 1024
ACTIONS_V = 1
PUSH_ROUTER_V = 1


@dataclass(slots=True)
class BlogAutoStatus:
    at: float | None = None
    kakao_ok: bool | None = None
    bridge: int = 0
    actions: list[str] = field(default_factory=list)
    kakao_state: str = "unknown"  # ok | down | expiring | unknown
    kakao_relink: bool = False
    kakao_expires_at: float | None = None
    route: str = ""
    version: str = ""

    def fresh(self, now: float | None = None) -> bool:
        return self.at is not None and (time.time() if now is None else now) - self.at <= FRESH_S

    def bridge_ok(self, now: float | None = None) -> bool:
        """대시보드 일을 맡길 수 있나: 15분 안 신호 + bridge ≥ 1."""
        return self.fresh(now) and self.bridge >= 1

    def supports(self, action: str) -> bool:
        return action in self.actions


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def read_blogauto(data_dir: Path | str) -> BlogAutoStatus:
    path = Path(data_dir) / BLOGAUTO_FILE
    try:
        if path.stat().st_size > MAX_BYTES:
            return BlogAutoStatus()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BlogAutoStatus()
    if not isinstance(data, dict):
        return BlogAutoStatus()
    kakao = data.get("kakao") if isinstance(data.get("kakao"), dict) else {}
    state = kakao.get("state") if kakao.get("state") in ("ok", "down", "expiring", "unknown") else None
    kakao_ok = data.get("kakao_ok") if isinstance(data.get("kakao_ok"), bool) else None
    if state is None:
        state = "unknown" if kakao_ok is None else ("ok" if kakao_ok else "down")
    actions = data.get("actions")
    bridge = data.get("bridge")
    return BlogAutoStatus(
        at=_num(data.get("at")), kakao_ok=kakao_ok,
        bridge=int(bridge) if isinstance(bridge, int) and not isinstance(bridge, bool) and 0 <= bridge < 1000 else 0,
        actions=[a for a in actions if isinstance(a, str) and len(a) <= 40][:100] if isinstance(actions, list) else [],
        kakao_state=state, kakao_relink=kakao.get("relink") is True, kakao_expires_at=_num(kakao.get("expires_at")),
        route=str(data.get("route") or "")[:10] if data.get("route") in ("push", "legacy") else "",
        version=str(data.get("version") or "")[:20] if isinstance(data.get("version"), str) else "",
    )


def write_dashboard(data_dir: Path | str, *, push_devices: int, push_ok_at: float | None, now: float | None = None) -> None:
    """딜봇 생존 신호 (임시 파일 → os.replace, 0600)."""
    base = Path(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    obj = {"at": time.time() if now is None else now, "v": 1, "push_router": PUSH_ROUTER_V, "push_devices": int(push_devices),
           "push_ok_at": push_ok_at, "actions_v": ACTIONS_V}
    tmp = base / f".{DASHBOARD_FILE}.{os.getpid()}.{secrets.token_hex(3)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, base / DASHBOARD_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
