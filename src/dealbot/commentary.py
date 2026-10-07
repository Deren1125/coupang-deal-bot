"""딜 한줄평: 서버에 로그인된 Claude Code CLI(`claude -p`)로 상품마다 짧은 소개 한 줄을 쓴다.

- 상품명·분류·가격 근거로 '누구에게·어디에 좋은지'만 쓴다. 숫자·성분·효능·점유율·후기처럼 확인 안 된 사실은 금지.
- 실패·시간 초과·규칙 위반이면 None (글은 한줄평 없이 올라감). 같은 상품은 DB 에 저장해 다시 묻지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from dealbot.models import Deal
from dealbot.utils.text import clean_name

log = logging.getLogger(__name__)

SYSTEM = (
    "너는 핫딜 채널 '오늘의 핫딜'을 운영하는 사람이다. 친구한테 '이거 괜찮더라' 하고 알려주듯 상품 한마디를 쓴다.\n"
    "- 상품명과 분류에 적힌 정보만 근거로, 어떤 사람이 어떤 상황에서 쓰기 좋은지 1~2문장(60자 이내).\n"
    "- 말투는 자연스러운 해요체 (~좋아요, ~편해요, ~하기 딱이에요). 매번 같은 끝맺음 반복 금지.\n"
    "- '~분께 추천드립니다', '~에 안성맞춤입니다', '~를 경험해 보세요' 같은 광고·AI 말투 금지.\n"
    "- 숫자(가격·할인율·용량·개수·순위·평점), 성분, 효능, 성능 수치, 시장 점유율, 제조사 관계, 후기 내용은 절대 쓰지 않는다.\n"
    "- 과장 표현(역대급, 미쳤다, 무조건, 강추, 놓치지 마세요)·느낌표·따옴표·이모지 금지.\n"
    "- 출력은 그 문장만. 쓸 말이 없으면 SKIP 만 출력."
)
BANNED = re.compile(r"\d|역대급|미쳤|무조건|강추|놓치|추천드립니다|안성맞춤|경험해 보세요|!|https?:|[★☆※\"“”]")


def find_claude() -> str | None:
    for c in (shutil.which("claude"), str(Path.home() / ".local/bin/claude"), str(Path.home() / ".claude/local/claude"),
              "/usr/local/bin/claude", "/usr/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def check_comment(text: str | None) -> str | None:
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("'\"")
    if not t or t.upper() == "SKIP" or len(t) > 80 or BANNED.search(t):
        return None
    return t


class Commentator:
    def __init__(self, *, enabled: bool = True, model: str = "sonnet", timeout: float = 90, db=None) -> None:
        self.enabled = enabled
        self.model = model
        self.timeout = timeout
        self.db = db
        self.bin = find_claude() if enabled else None

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.bin)

    def _key(self, deal: Deal) -> str:
        return "comment2:" + ":".join(deal.product.product_id.split(":")[:2])  # 말투를 바꾸면 숫자를 올려 예전 한줄평을 다시 쓰게 함

    async def comment(self, deal: Deal) -> str | None:
        if not self.available:
            return None
        if self.db is not None:
            cached = self.db.kv_get(self._key(deal))
            if cached:
                return cached
        p = deal.product
        user = f"상품명: {clean_name(p.name)}\n분류: {p.category or '-'}\n쇼핑몰: {p.shop}"
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin, "-p", "--output-format", "json", "--model", self.model, "--system-prompt", SYSTEM, "--tools", "",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=os.environ | {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
            )
            out, _ = await asyncio.wait_for(proc.communicate(user.encode()), timeout=self.timeout)
        except (TimeoutError, OSError) as e:
            log.warning("commentary failed: %s", e)
            try:
                proc.kill()  # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                pass
            return None
        try:
            payload = json.loads(out.decode() or "{}")
        except ValueError:
            return None
        if payload.get("is_error"):
            log.warning("commentary error: %s", str(payload.get("result"))[:200])
            return None
        text = check_comment(payload.get("result"))
        if text and self.db is not None:
            self.db.kv_set(self._key(deal), text)
        return text
