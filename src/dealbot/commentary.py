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
    "너는 핫딜 채널 '오늘의 핫딜'을 직접 운영하는 사람이다. 단톡방에서 친구한테 말하듯 상품 한마디를 쓴다.\n"
    "공식: [확인된 사실이나 숫자 하나] + [판단 하나: 누구에게 / 아쉬운 점 / 팁]. 1~2문장, 70자 이내.\n"
    "- 가격 근거(평소 가격·최저가·할인율)는 글 첫 줄에 따로 나가니 한줄평에서 되풀이하지 않는다. 숫자는 꼭 필요할 때 단가 하나 정도만.\n"
    "- 숫자는 아래 '확인된 정보'에 있는 숫자만 그대로 쓸 수 있다. 다른 숫자(용량 환산, 기간, 효능 수치)는 만들지 않는다.\n"
    "- 상품명·분류·확인된 정보에 없는 성분, 효능, 맛, 품질, 후기, 점유율, 제조사 이야기는 쓰지 않는다.\n"
    "- 직접 사거나 써 본 적이 없으니 '써보니', '먹어보니', '저도 샀어요' 같은 경험담 금지. 대신 '구성 보니', '이 가격이면'처럼 말한다.\n"
    "- 말투는 해요체. 끝맺음을 ~요/~네요/~예요/~거든요/~죠 중에서 자연스럽게. 느낌표·따옴표·이모지 금지.\n"
    "- 금지 표현: 추천드립니다, 안성맞춤, 경험해 보세요, 찾고 계셨다면, 잘 맞는 제품, 활용하기 좋아요, 실용적이고 편리한, "
    "합리적인 가격, 가성비 좋은, 만족도가 높은, 도움이 될 수 있습니다, 역대급, 미쳤다, 무조건, 강력 추천, 인생템, 놓치지 마세요, 서두르세요.\n"
    "- 쓸 말이 없으면 comment 를 SKIP 으로."
    "\n\n스레드(Threads)용 글도 같이 쓴다. 말투는 반말 음슴체(~임, ~함, ~듯, ~음)로 고정.\n"
    "- thread_hook: 상품명 없이 시작하는 첫 줄. 이 상품이 필요해지는 생활 속 순간·공감 (예: '휴지 떨어진 거 꼭 샤워 끝나고 알게 됨'). "
    "30자 이내, 숫자 금지, 지어낸 경험('샀음', '써봤는데') 금지, 질문형은 가끔만.\n"
    "- thread_take: 판단 한 줄 (누구한테 좋은지 / 아쉬운 점 / 팁). 40자 이내 반말. 숫자는 확인된 정보에 있는 것만.\n"
    "- short_name: 상품명을 사람이 부르는 짧은 이름으로 (브랜드 + 핵심 품목 + 용량·수량 1개 정도, 20자 이내). 상품명에 없는 말은 넣지 않는다."
)
SCHEMA = {
    "type": "object",
    "properties": {"comment": {"type": "string"}, "thread_hook": {"type": "string"},
                   "thread_take": {"type": "string"}, "short_name": {"type": "string"}},
    "required": ["comment", "thread_hook", "thread_take", "short_name"],
}
BANNED = re.compile(
    r"역대급|미쳤|무조건|강추|강력 ?추천|인생템|놓치|서두르|추천드립니다|안성맞춤|경험해 보세요|찾고 계셨다면|잘 맞는 제품|"
    r"활용하기 좋|실용적이고|합리적인 가격|가성비 좋은|만족도가 높|도움이 될 수|써보니|먹어보니|저도 샀|!|https?:|[★☆※\"“”]"
)


def find_claude() -> str | None:
    for c in (shutil.which("claude"), str(Path.home() / ".local/bin/claude"), str(Path.home() / ".claude/local/claude"),
              "/usr/local/bin/claude", "/usr/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def check_comment(text: str | None, facts: str = "") -> str | None:
    """규칙 검사: 금지 표현, 길이, 그리고 숫자는 '확인된 정보'에 있던 숫자만 (지어낸 숫자 차단)."""
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("'\"")
    if not t or t.upper() == "SKIP" or len(t) > 90 or BANNED.search(t):
        return None
    known = set(re.findall(r"\d[\d,.]*", facts))
    if any(n.rstrip(".,") not in {k.rstrip(".,") for k in known} for n in re.findall(r"\d[\d,.]*", t)):
        return None
    return t


def check_hook(text: str | None) -> str | None:
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("'\"")
    if not t or len(t) > 32 or re.search(r"\d", t) or BANNED.search(t) or re.search(r"샀음|샀는데|써봤|먹어봤", t):
        return None
    return t


def check_short_name(text: str | None, name: str) -> str | None:
    """짧은 이름: 낱말이 모두 원래 상품명에 있어야 (지어낸 말 차단)."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    plain = clean_name(name).lower()
    if not t or len(t) > 22 or any(w.lower() not in plain for w in t.split()):
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
        return "comment5:" + ":".join(deal.product.product_id.split(":")[:2])  # 말투를 바꾸면 숫자를 올려 예전 것을 다시 쓰게 함

    async def comment(self, deal: Deal, facts: str = "") -> str | None:
        return (await self.write(deal, facts)).get("comment")

    async def write(self, deal: Deal, facts: str = "") -> dict[str, str]:
        """한 번 호출로 한줄평(해요체) + 스레드 첫 줄·판단(반말) + 짧은 이름. 규칙에 어긋난 항목은 빠진다.
        facts: 데이터로 확인된 정보 (예: '평소 15,200원대인데 오늘 12,400원이에요. / 봉당 716원 꼴 / 로켓배송')."""
        if not self.available:
            return {}
        if self.db is not None:
            cached = self.db.kv_get(self._key(deal))
            if cached:
                try:
                    return json.loads(cached)
                except ValueError:
                    pass
        p = deal.product
        user = (f"상품명: {clean_name(p.name)}\n분류: {p.category or '-'}\n쇼핑몰: {p.shop}\n"
                f"확인된 정보: {facts or '없음'}")
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin, "-p", "--output-format", "json", "--model", self.model, "--system-prompt", SYSTEM, "--tools", "",
                "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
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
            return {}
        try:
            payload = json.loads(out.decode() or "{}")
        except ValueError:
            return {}
        if payload.get("is_error"):
            log.warning("commentary error: %s", str(payload.get("result"))[:200])
            return {}
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(payload.get("result") or "{}")
            except ValueError:
                data = {"comment": payload.get("result")}
        got = {
            "comment": check_comment(data.get("comment"), facts),
            "thread_hook": check_hook(data.get("thread_hook")),
            "thread_take": check_comment(data.get("thread_take"), facts),
            "short_name": check_short_name(data.get("short_name"), p.name),
        }
        got = {k: v for k, v in got.items() if v}
        if got and self.db is not None:
            self.db.kv_set(self._key(deal), json.dumps(got, ensure_ascii=False))
        return got
