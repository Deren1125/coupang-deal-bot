"""딜 한줄평: 서버에 로그인된 Claude Code CLI(`claude -p`)로 상품마다 짧은 소개 한 줄을 쓴다.

- 상품명·분류·가격 근거로 '누구에게·어디에 좋은지'만 쓴다. 숫자·성분·효능·점유율·후기처럼 확인 안 된 사실은 금지.
- 글의 다른 줄(첫 줄 가격·근거, ⚖️ 단가, 🚚 배송)에 이미 나가는 숫자·배송은 한줄평에서 되풀이하지 않는다.
- 실패·시간 초과·규칙 위반이면 None (글은 한줄평 없이 올라감). 같은 상품·같은 정보면 DB 에 저장해 다시 묻지 않는다
  (네 칸이 다 규칙을 통과한 답만 저장하고, 꺼낼 때도 지금 규칙·정보로 다시 검사한다).
- CLI 가 연달아 실패하면 잠깐 쉬고(발행이 매번 시간 초과를 기다리지 않게) 관리자에게 한 번 알린다.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from dealbot.models import Deal
from dealbot.utils.text import clean_name

log = logging.getLogger(__name__)

SYSTEM = (
    "너는 핫딜 채널 '오늘의 핫딜'을 직접 운영하는 사람이다. 단톡방에서 친구한테 말하듯 상품 한마디를 쓴다.\n"
    "공식: [판단 하나: 누구에게 / 어디에 / 아쉬운 점 / 팁] + 필요하면 상품명에 있는 구성·용량 하나. 1~2문장, 70자 이내.\n"
    "- 가격과 가격 근거(평소 가격·최저가·할인율), 단가(개당·병당 등), 배송(무배·로켓배송), 별점은 글의 다른 줄에 따로 나가니 "
    "한줄평에서 되풀이하지 않는다.\n"
    "- 숫자는 상품명이나 '확인된 정보'에 있는 것만 단위까지 그대로 쓸 수 있다. 다른 숫자(용량 환산, 기간, 개수)나 "
    "'한 달은 버텨요', '두 박스면', '반값' 같은 말은 만들지 않는다.\n"
    "- 상품명·분류·확인된 정보에 없는 성분, 효능, 맛, 품질, 후기, 점유율, 제조사 이야기는 쓰지 않는다.\n"
    "- 직접 사거나 써 본 적이 없으니 '써보니', '먹어보니', '저도 샀어요' 같은 경험담 금지. 대신 '구성 보니', '이 가격이면'처럼 말한다.\n"
    "- '쟁여두기'처럼 어느 상품에나 붙는 말은 되도록 피하고, 이 상품이라서 할 수 있는 말을 쓴다.\n"
    "- 말투는 해요체. 끝맺음을 ~요/~네요/~예요/~거든요/~죠 중에서 자연스럽게. 느낌표·따옴표·이모지 금지.\n"
    "- 금지 표현: 추천드립니다, 안성맞춤, 경험해 보세요, 찾고 계셨다면, 잘 맞는 제품, 활용하기 좋아요, 실용적이고 편리한, "
    "합리적인 가격, 가성비 좋은, 만족도가 높은, 도움이 될 수 있습니다, 평소보다 확실히 싸게, 역대급, 미쳤다, 무조건, 강추, "
    "강력 추천, 인생템, 놓치지 마세요, 서두르세요.\n"
    "- 쓸 말이 없으면 comment 를 SKIP 으로."
    "\n\n스레드(Threads)용 글도 같이 쓴다. 말투는 반말 음슴체(~임, ~함, ~듯, ~음)로 고정. ~요·~니다로 끝내지 않는다.\n"
    "- thread_hook: 상품명 없이 시작하는 첫 줄. 이 상품이 필요해지는 생활 속 순간·공감 (예: '휴지 떨어진 거 꼭 샤워 끝나고 알게 됨'). "
    "30자 이내, 숫자 금지, 지어낸 경험('샀음', '써봤는데') 금지, 질문형은 가끔만.\n"
    "- thread_take: 판단 한 줄 (누구한테 좋은지 / 아쉬운 점 / 팁). 40자 이내 반말 음슴체. 숫자는 상품명·확인된 정보에 있는 것만.\n"
    "- short_name: 상품명을 사람이 부르는 짧은 이름으로 (브랜드 + 핵심 품목 + 용량·수량, 20자 이내). 상품명에 있는 낱말을 그대로 쓰고 "
    "없는 말은 넣지 않는다. 여러 개 묶음이면 개수(예: 24개, 40병)는 꼭 넣고, 제로·무라벨·로우슈거처럼 종류를 가르는 말은 빼지 않는다."
)
SCHEMA = {
    "type": "object",
    "properties": {"comment": {"type": "string"}, "thread_hook": {"type": "string"},
                   "thread_take": {"type": "string"}, "short_name": {"type": "string"}},
    "required": ["comment", "thread_hook", "thread_take", "short_name"],
}
FIELDS: tuple[str, ...] = ("comment", "thread_hook", "thread_take", "short_name")
# '강추위'·'놓치는 사람' 같은 평범한 말은 걸리지 않게 홍보 표현만 콕 집는다
BANNED = re.compile(
    r"역대급|미쳤|무조건|강추(?!위)|강력 ?추천|인생템|놓치지 ?마|놓치지 ?말|놓치면|놓치기 전에|서두르|추천드립니다|안성맞춤|"
    r"경험해 보세요|찾고 계셨다면|잘 맞는 제품|활용하기 좋|실용적이고|합리적인 가격|가성비 좋은|만족도가 높|도움이 될 수|"
    r"확실히 싸|써보니|먹어보니|저도 샀|!|https?:|[★☆※\"“”]"
)
# 스레드 글(반말 음슴체)에 섞이면 안 되는 존댓말 끝. '필요'·'중요'·'주요' 같은 낱말의 '요'는 빼고 본다
POLITE = re.compile(r"(?<![필중주수소])요(?=$|[\s.,~?!…])|니다|세요|십시오|죠(?=$|[\s.,~?!…])")

# 숫자 + 단위. 영문 단위·%는 띄어 써도 같은 단위('350 ml'), 한글 단위는 붙여 쓴 것만('24개', '29일')
_NUM = re.compile(
    r"(\d[\d,.]*)(?:\s*(kg|ml|g|l|cm|mm|%)(?![a-wyz])"
    r"|(개월|개입|봉지|박스|세트|시간|주일|인분|페트|[개병캔봉팩펫매롤입일주달년분원]))?",
    re.I,
)
_UNIT_SAME = {"개입": "개", "입": "개", "ea": "개", "봉지": "봉", "주일": "주", "페트": "병", "펫": "병", "달": "개월"}
# 숫자 없이 한글로 지어내는 개수·기간 ('두 박스면', '한 달은 버텨요'). '한 병씩'·'한 박스' 같은 평범한 말은 둔다
_WORD_QTY = re.compile(
    r"(?<![가-힣])(?:두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇|수십|수백)\s?(?:개월|개|병|캔|박스|상자|봉지|봉|팩|롤|묶음|세트|달|주|해|년)"
    r"|(?<![가-힣])한\s?(?:달|주|해)(?=$|[\s,.~은는이가도을를에치간만씩동])|반\s?년"
)
_HALF = re.compile(r"반값|반\s?가격|절반|반의 반|반액")


def find_claude() -> str | None:
    for c in (shutil.which("claude"), str(Path.home() / ".local/bin/claude"), str(Path.home() / ".claude/local/claude"),
              "/usr/local/bin/claude", "/usr/bin/claude"):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _num_tokens(text: str) -> set[tuple[str, str]]:
    """'개당 1,038원 / 500ml' → {('1038', '원'), ('500', 'ml')}. 쉼표는 떼고, 같은 뜻의 단위는 하나로."""
    out: set[tuple[str, str]] = set()
    for m in _NUM.finditer(text or ""):
        unit = (m.group(2) or m.group(3) or "").lower()
        out.add((m.group(1).rstrip(".,").replace(",", ""), _UNIT_SAME.get(unit, unit)))
    return out


def _max_pct(facts: str) -> float:
    return max((float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", facts or "")), default=0.0)


def check_comment(text: str | None, facts: str = "", name: str = "") -> str | None:
    """규칙 검사: 금지 표현, 길이, 숫자는 '확인된 정보'나 상품명에 있던 숫자만 단위까지 같게 (지어낸 숫자 차단).
    '두 박스', '한 달' 같은 한글 수량·기간, 실제로 50% 이상 싸지 않은데 '반값'도 막는다."""
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("'\"")
    if not t or t.upper() == "SKIP" or len(t) > 90 or BANNED.search(t) or _WORD_QTY.search(t):
        return None
    if _HALF.search(t) and _max_pct(facts) < 50:
        return None
    allowed = _num_tokens(f"{facts} / {clean_name(name)}" if name else facts)
    numbers = {n for n, _ in allowed}
    for n, unit in _num_tokens(t):
        if (n, unit) not in allowed and (unit or n not in numbers):
            return None
    return t


def check_take(text: str | None, facts: str = "", name: str = "") -> str | None:
    """스레드 판단 한 줄: 한줄평 규칙 + 45자 이내 + 반말 음슴체만 (해요체가 섞이면 스레드 글 말투가 두 개가 됨)."""
    t = check_comment(text, facts, name)
    if not t or len(t) > 45 or POLITE.search(t):
        return None
    return t


def check_hook(text: str | None) -> str | None:
    t = re.sub(r"\s+", " ", (text or "")).strip().strip("'\"")
    if (not t or len(t) > 32 or re.search(r"\d", t) or BANNED.search(t) or POLITE.search(t)
            or re.search(r"샀음|샀는데|써봤|먹어봤", t)):
        return None
    return t


_SN_SPLIT = re.compile(r"[\s,()/+·\[\]{}<>|_~]+")
_SN_GLUE = re.compile(r"(\d)\s+(?=(?:kg|ml|g|l|개|병|캔|봉|팩|롤|매|입|박스|세트)(?![a-wyz]))", re.I)
_SN_TIMES = re.compile(r"(\d(?:kg|ml|g|l|개입|개|병|캔|봉|팩|롤|매|입)?)\s*[x×*]\s*(?=\d)", re.I)
_SN_COUNT = re.compile(r"(?<![\d.])(\d{1,4})(개입|개|병|캔|봉지|봉|팩|펫|페트|롤|매|입|포|박스|세트|ea)(?![가-힣a-wyz])", re.I)


def _sn_words(s: str) -> list[str]:
    """'코카콜라 350 ml x 24캔' → ['코카콜라', '350ml', '24캔'] (소문자, 숫자-단위 붙이고, 곱하기 x 는 낱말 구분)."""
    s = _SN_TIMES.sub(r"\1 ", _SN_GLUE.sub(r"\1", s.lower()))
    return [w for w in (x.strip(".-:") for x in _SN_SPLIT.split(s)) if w]


def _sn_counts(words: Iterable[str]) -> set[tuple[int, str]]:
    out: set[tuple[int, str]] = set()
    for w in words:
        for m in _SN_COUNT.finditer(w):
            unit = m.group(2).lower()
            out.add((int(m.group(1)), _UNIT_SAME.get(unit, unit)))
    return out


def check_short_name(text: str | None, name: str) -> str | None:
    """짧은 이름: 낱말마다 원래 상품명의 낱말(또는 붙여 쓴 이웃 낱말)과 같아야 (지어낸 말, '무라벨'→'라벨' 같은 조각 차단).
    묶음 상품이면 개수(24개·40병)가 있어야 — 스레드 '근데 {이름} {가격}임' 이 한 개 값처럼 읽히지 않게."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t or len(t) > 22:
        return None
    words = _sn_words(clean_name(name))
    counts = _sn_counts(words)
    allowed = set(words) | {f"{n}{u}" for n, u in counts}  # '24개입' 은 '24개' 로 줄여 써도 됨
    allowed |= {"".join(words[i:j]) for i in range(len(words)) for j in range(i + 2, min(i + 3, len(words)) + 1)}
    mine = _sn_words(t)
    if not mine or any(w not in allowed or (len(w) < 2 and not w.isdigit()) for w in mine):
        return None
    packs = {c for c in counts if c[0] > 1}
    if packs and not packs & _sn_counts(mine):
        return None
    return t


_SHIP = re.compile(r"무배|무료\s?(?:로켓\s?)?배송|로켓\s?배송|배송비\s?(?:무료|없)")
_AMOUNT = re.compile(r"\d[\d,.]*\s*(?:원|%)")
# 문장 앞머리에서만 걷어낸다: '개당 1,038원이라 ', '1,038원이면 ', '무배라 ', '무료배송이라 '
_LEAD = re.compile(
    r"^(?:[가-힣]{1,3}당\s*)?\d[\d,]*\s*원(?:\s*꼴)?(?:이라서|이라|이고|이면|이니까|이니|인데|에|짜리라)?,?\s+"
    r"|^(?:무배|무료\s?(?:로켓\s?)?배송|로켓\s?배송)(?:이라서|이라|라서|라|이고|고|에|으로|까지|인데|이니)?,?\s+"
)
_SENTENCE = re.compile(r"(?<=[.?~…])\s+")


def _amounts(s: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m) for m in _AMOUNT.findall(s or "")}


def drop_repeats(text: str | None, shown: Iterable[str | None]) -> str | None:
    """한줄평이 글의 다른 줄(첫 줄 가격·근거, ⚖️ 단가, 🚚 배송)에 이미 나간 금액·%·배송을 되풀이하면 그 부분을 뺀다.
    문장 앞머리('개당 1,038원이라 ', '무배라 ')만 걷어내고, 그래도 겹치거나 판단이 안 남는 문장은 통째로 뺀다. 다 빠지면 None."""
    if not text:
        return None
    shown = [s for s in shown if s]
    amounts = set().union(*map(_amounts, shown)) if shown else set()
    ship = any(_SHIP.search(s) for s in shown)

    def repeated(s: str) -> bool:
        return bool(_amounts(s) & amounts) or bool(ship and _SHIP.search(s))

    keep = []
    for sentence in _SENTENCE.split(text.strip()):
        cur = sentence
        for _ in range(3):
            m = _LEAD.match(cur) if repeated(cur) else None
            if not m:
                break
            cur = cur[m.end():]
        if repeated(cur) or (cur != sentence and (len(cur) < 8 or " " not in cur)):
            continue
        keep.append(cur)
    return " ".join(keep) or None


def validate(data: dict[str, Any], facts: str, shown: Iterable[str | None], name: str) -> dict[str, str]:
    """AI 답(또는 저장해 둔 답)을 지금 정보·규칙으로 검사. 통과한 칸만 남긴다."""
    def s(k: str) -> str | None:
        v = data.get(k)
        return v if isinstance(v, str) else None

    got = {
        "comment": drop_repeats(check_comment(s("comment"), facts, name), shown),
        "thread_hook": check_hook(s("thread_hook")),
        "thread_take": check_take(s("thread_take"), facts, name),
        "short_name": check_short_name(s("short_name"), name),
    }
    return {k: v for k, v in got.items() if v}


BREAK_AFTER = 3  # 이만큼 연달아 실패하면
BREAK_SECONDS = 15 * 60  # 이 동안은 CLI 를 안 부르고 한줄평 없이 올린다
_PROMPT_TAG = hashlib.sha1((SYSTEM + json.dumps(SCHEMA)).encode()).hexdigest()[:8]  # 지시문을 바꾸면 저장된 답을 다시 안 씀


class Commentator:
    def __init__(self, *, enabled: bool = True, model: str = "sonnet", timeout: float = 90, db=None) -> None:
        self.enabled = enabled
        self.model = model
        self.timeout = timeout
        self.db = db
        self.bin = find_claude() if enabled else None
        if enabled and self.bin is None:
            log.warning("AI 한줄평이 켜져 있는데 claude CLI 를 찾지 못했습니다 — 한줄평 없이 올립니다 (PATH=%s)",
                        os.environ.get("PATH", ""))
        self.fail_streak = 0
        self.last_error: str | None = None
        self._skip_until = 0.0
        self._alerted = False
        self._alert: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.bin)

    def _key(self, deal: Deal, facts: str = "", shown: Iterable[str | None] = ()) -> str:
        """상품 ID 전체(옵션 vendorItemId·URL 해시까지) + 이름·가격·넘긴 정보·지시문이 모두 같을 때만 같은 키."""
        p = deal.product
        raw = "\x1f".join([_PROMPT_TAG, p.product_id, clean_name(p.name), str(p.price), facts, *[s for s in shown if s]])
        return "comment6:" + hashlib.sha1(raw.encode()).hexdigest()[:24]

    def take_alert(self) -> str | None:
        """연달아 실패해서 쉬기 시작했을 때 관리자에게 보낼 말 (실패가 이어지는 동안 한 번만)."""
        alert, self._alert = self._alert, None
        return alert

    def _fail(self, reason: str) -> None:
        self.fail_streak += 1
        self.last_error = reason
        log.warning("commentary failed (%d번 연속): %s", self.fail_streak, reason)
        if self.fail_streak >= BREAK_AFTER:
            self._skip_until = time.monotonic() + BREAK_SECONDS
            if not self._alerted:
                self._alerted = True
                self._alert = (f"AI 한줄평을 {self.fail_streak}번 연속 못 받았어요. {BREAK_SECONDS // 60}분 동안은 한줄평 없이 올리고 "
                               f"그다음에 다시 시도할게요. 서버의 claude 로그인 상태를 확인해 주세요.\n마지막 오류: {reason}")
        return None

    def _ok(self) -> None:
        self.fail_streak, self._alerted, self._alert = 0, False, None

    async def comment(self, deal: Deal, facts: str = "", shown: Iterable[str | None] = ()) -> str | None:
        return (await self.write(deal, facts, shown=shown)).get("comment")

    async def write(self, deal: Deal, facts: str = "", shown: Iterable[str | None] = ()) -> dict[str, str]:
        """한 번 호출로 한줄평(해요체) + 스레드 첫 줄·판단(반말) + 짧은 이름. 규칙에 어긋난 항목은 빠진다.
        facts: 데이터로 확인된 정보 (예: '평소 15,200원대인데 오늘 12,400원이에요. / 봉당 716원 꼴 / 로켓배송').
        shown: 글의 다른 줄에 이미 찍히는 것 (첫 줄 가격·근거, ⚖️ 단가, 🚚 배송) — 한줄평이 되풀이하지 않게."""
        if not self.available:
            return {}
        p = deal.product
        shown = tuple(s for s in shown if s)
        key = self._key(deal, facts, shown)
        cached: dict[str, str] = {}
        if self.db is not None:
            try:
                raw = json.loads(self.db.kv_get(key) or "{}")
            except ValueError:
                raw = {}
            cached = validate(raw, facts, shown, p.name) if isinstance(raw, dict) else {}
            if len(cached) == len(FIELDS):
                return cached
        if time.monotonic() < self._skip_until:
            log.debug("commentary skipped (쉬는 중, %d번 연속 실패)", self.fail_streak)
            return cached
        user = (f"상품명: {clean_name(p.name)}\n분류: {p.category or '-'}\n쇼핑몰: {p.shop}\n"
                f"확인된 정보: {facts or '없음'}")
        if shown:
            user += f"\n글의 다른 줄에 이미 나가는 것 (한줄평에 다시 쓰지 않기): {' / '.join(shown)}"
        try:
            data = await self._ask(user)
        except Exception as e:  # noqa: BLE001 — 한줄평 때문에 발행이 멈추면 안 됨
            data = self._fail(f"{type(e).__name__}: {e}")
        if data is None:
            return cached
        got = validate(data, facts, shown, p.name)
        if len(got) == len(FIELDS) and self.db is not None:  # 일부만 통과한 답은 저장 안 함 → 다음 글에서 다시 시도
            self.db.kv_set(key, json.dumps(got, ensure_ascii=False))
        return got

    async def _ask(self, user: str) -> dict[str, Any] | None:
        """CLI 를 불러 정해진 칸(dict)을 돌려준다. 실패(실행 안 됨·시간 초과·오류 응답)는 None, 칸 없이 말로만 답하면 {}."""
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin, "-p", "--output-format", "json", "--model", self.model, "--system-prompt", SYSTEM, "--tools", "",
                "--json-schema", json.dumps(SCHEMA, ensure_ascii=False),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=os.environ | {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
            )
            out, err = await asyncio.wait_for(proc.communicate(user.encode()), timeout=self.timeout)
        except TimeoutError:
            await self._kill(proc)
            return self._fail(f"TimeoutError ({self.timeout:g}초 안에 답이 없음)")
        except OSError as e:
            return self._fail(f"{type(e).__name__}: {e}")
        finally:
            if proc is not None and proc.returncode is None:  # 취소될 때도 CLI 가 남지 않게
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        text = out.decode(errors="replace").strip()
        why = err.decode(errors="replace").strip()[:300]
        if not text:
            return self._fail(f"exit {proc.returncode}, 출력 없음: {why or '-'}")
        try:
            payload = json.loads(text)
        except ValueError:
            return self._fail(f"exit {proc.returncode}, JSON 아님: {(why or text)[:300]}")
        if isinstance(payload, list):  # verbose 설정이면 메시지 목록으로 나옴 → 마지막 result 만 본다
            payload = next((m for m in reversed(payload) if isinstance(m, dict) and m.get("type") == "result"), None)
        if not isinstance(payload, dict):
            return self._fail(f"exit {proc.returncode}, 모르는 출력 모양: {text[:200]}")
        if payload.get("is_error"):
            return self._fail(f"오류 응답: {str(payload.get('result'))[:200]}")
        self._ok()
        data = payload.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(payload.get("result") or "")
            except (ValueError, TypeError):
                data = None
        if not isinstance(data, dict) or not any(k in data for k in FIELDS):
            # 말로만 답함 (거절·설명 등): 그 글을 한줄평으로 올리지 않는다
            log.warning("commentary: 정해진 칸 없이 답해서 안 씀: %s", str(payload.get("result"))[:200])
            return {}
        return data

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5)
