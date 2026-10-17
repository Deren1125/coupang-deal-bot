"""딜봇 → Blog-Auto 카톡 부탁 (kakao_out/new/<k_…>.json). 카톡 토큰은 Blog-Auto 에만 있어 거기서 보낸다 (카톡은 예비 — 주인 10/18).

    {"v":1,"id":"k_…","at":…,"op":"send","kind":"urgent|daily|test","key":"link:123",
     "text":"🔔 링크 필요 #123 …\n대시보드 '할 일'에서 링크를 넣어 주세요","link":"https://…/dashboard/go/deal-123","button":"대시보드 열기"}
    {"v":1,"id":"k_…","at":…,"op":"resolve","keys":["link:123"]}

text 는 200자 안 (카톡 한 통) — 문장 끝에서 자르고 '…'. 버튼 주소가 아직 카카오 앱에 등록되지 않았어도 글만 보고 알 수 있게 쓴다.
Blog-Auto 가 아직 다리(bridge ≥ 1)를 모르는 예전 판이면 kakao_out 을 읽지 않으니, 오늘처럼 받은편지함(blogauto_inbox.jsonl)에
owner_notice v2 한 줄을 남긴다 (나눠서 업데이트되는 사이에 급한 일이 사라지지 않게).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dealbot.bridge import spool

log = logging.getLogger(__name__)

TEXT_MAX = 200
BUTTON = "대시보드 열기"
KINDS = ("urgent", "daily", "test")
_SENTENCE_END = re.compile(r"[.!?。…]|요(?=\s)|다(?=\s)|\n")


def fit(text: str, limit: int = TEXT_MAX) -> str:
    """200자 안으로: 넘치면 마지막 문장 끝(또는 줄바꿈)에서 자르고 '…'."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    room = text[: limit - 1]
    cut = max((m.end() for m in _SENTENCE_END.finditer(room)), default=0)
    if cut < limit // 2:
        cut = len(room)
    return room[:cut].rstrip() + "…"


class KakaoOut:
    def __init__(self, data_dir: Path | str, *, bridge_ok: Callable[[], bool], inbox: Callable[[dict[str, Any]], bool] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.data_dir = Path(data_dir)
        self.bridge_ok = bridge_ok
        self.inbox = inbox
        self.clock = clock

    def _root(self) -> Path:
        return spool.root(self.data_dir, spool.KAKAO_OUT)

    def send(self, kind: str, *, key: str, text: str, link: str, short: str = "", ttl_h: float = 24) -> bool:
        """카톡 한 통 부탁. 남겼으면 True."""
        if kind not in KINDS:
            raise ValueError("kakao kind")
        text = fit(text)
        now = self.clock()
        try:
            if self.bridge_ok():
                kid = spool.new_id("k", now)
                spool.write(self._root(), kid, {"v": 1, "id": kid, "at": now, "op": "send", "kind": kind, "key": key,
                                                "text": text, "link": link, "button": BUTTON})
                return True
            if self.inbox is None or kind == "daily":  # 예전 Blog-Auto 는 저녁 요약을 스스로 보낸다
                return False
            note = {"v": 2, "at": now, "kind": "dash_" + kind, "key": key, "text": text, "short": (short or text)[:48],
                    "urgent": True, "link": link, "ttl_h": ttl_h, "group": ""}
            return self.inbox({"owner_notice": note}) is not False
        except (OSError, ValueError) as e:
            log.warning("kakao_out write failed: %s", type(e).__name__)
            return False

    def resolve(self, keys: list[str]) -> bool:
        keys = [k for k in keys if k]
        if not keys:
            return False
        now = self.clock()
        try:
            if self.bridge_ok():
                kid = spool.new_id("k", now)
                spool.write(self._root(), kid, {"v": 1, "id": kid, "at": now, "op": "resolve", "keys": keys})
                return True
            if self.inbox is None:
                return False
            return self.inbox({"owner_notice": {"v": 2, "at": now, "resolve": keys}}) is not False
        except (OSError, ValueError) as e:
            log.warning("kakao_out resolve failed: %s", type(e).__name__)
            return False
