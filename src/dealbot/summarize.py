"""정보 글 요약: 게시판 글 본문을 채널 양식(짧은 안내문)으로 다시 쓴다.

Claude API(anthropic SDK)를 쓴다. ANTHROPIC_API_KEY 가 없으면 꺼진 상태로 두고, 그때는 앱이 관리자 승인으로 넘긴다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import anthropic

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
SKIP_TOKEN = "SKIP"

SYSTEM_PROMPT = """당신은 한국 핫딜·혜택 정보 텔레그램 채널 '오늘의 핫딜'의 편집자입니다.
커뮤니티 게시판(뽐뿌·루리웹 등)에 올라온 글을 구독자가 3초 만에 이해할 수 있는 짧은 안내문으로 다시 씁니다.

규칙:
- 글에 적힌 사실만 씁니다. 없는 내용·추측·과장은 절대 넣지 않습니다. 숫자(가격, 할인율, 적립액, 날짜, 횟수)는 원문 그대로.
- 게시판 메뉴, 작성자의 인사말·잡담·사과·캡처 설명, 댓글, "양해 부탁" 같은 개인적 표현은 모두 뺍니다.
- 첫 줄: 무엇을 얼마나/얼마에 받을 수 있는지 한 문장 (30자 안팎).
- 그 아래 '· ' 로 시작하는 짧은 줄 2~5개: 기간 / 조건·대상 / 혜택 내용 / 받는 방법 / 주의사항 중 원문에 있는 것만.
  상품이 여럿이면 대표 2~3개만 "상품명 가격(할인율)" 꼴로.
- 어투: 담백한 안내체. 문장 끝은 명사형이나 "~해요/~돼요". 느낌표·이모지·광고성 표현("놓치지 마세요", "강추") 금지.
- URL 은 쓰지 않습니다 (링크는 봇이 따로 붙입니다). 제목을 다시 쓰지 않습니다. 인용 부호·마크다운·코드 블록 없이 평문만.
- 전체 {max_chars}자 이내.
- 정리할 내용이 거의 없으면 첫 줄만 씁니다.
- 광고·스팸이거나 혜택·상품 정보가 아니라서 채널에 올릴 가치가 없으면 정확히 SKIP 이라고만 답합니다.

답의 맨 위에는 안내문보다 먼저 아래 두 줄을 씁니다 (숫자만, 없으면 0). 그 다음 빈 줄, 그 아래에 안내문:
할인율: 이 글의 혜택 중 가장 큰 할인율(%). 정가 대비 할인율. 무료 증정은 받는 것의 가치가 5,000원 이상일 때만 100, 아니면 0
할인액: 이 글의 혜택 중 가장 큰 할인·적립·캐시백·증정 가치(원). "1만원 이상 결제" 같은 조건 금액이 아니라 실제로 받는 금액"""

_FENCE = re.compile(r"^```[a-zA-Z]*\s*$")
_RATE_LINE = re.compile(r"^\s*할인율\s*[:：]\s*([\d.]+)\s*%?\s*$", re.M)
_AMOUNT_LINE = re.compile(r"^\s*할인액\s*[:：]\s*([\d,]+)\s*원?\s*$", re.M)
# 요약기가 없을 때 본문 숫자로 대략 보는 용도: "최대 60%", "60% 할인/세일", "4,800원 할인", "6만원 적립"
_BENEFIT = r"(?:할인|세일|적립|캐시백|쿠폰|페이백|환급|증정|지급|off)"
_RATE_LEAD = re.compile(r"(?:최대|최고|전\s*품목|전\s*상품|up\s*to)\s*(\d{1,3})\s*%", re.I)
_RATE_TRAIL = re.compile(r"(\d{1,3})\s*%\s*[^\n\d%]{0,4}?" + _BENEFIT, re.I)
_AMOUNT = re.compile(r"(\d[\d,]*)\s*(만|천)?\s*원\s*(?:을|를|이|가|의)?\s*(?:상당\s*)?" + _BENEFIT)
# "적립 58원", "캐시백 5,000원" 처럼 혜택 말이 앞에 오는 꼴 (판매가와 안 헷갈리는 적립성 낱말만)
_AMOUNT_AFTER = re.compile(r"(?:적립|캐시백|페이백|환급)\s*(?:합계|금액|최대)?\s*(\d[\d,]*)\s*(만|천)?\s*원")
_URL_IN_LINE = re.compile(r"https?://\S+")
_BULLET = re.compile(r"^\s*(?:[-*•▪◦]|\d+[.)])\s+")


@dataclass(slots=True)
class Summary:
    text: str | None = None  # 만들어진 본문 (없으면 None)
    skip: bool = False  # 요약기가 "올릴 가치 없음"이라고 판단
    error: str | None = None  # 못 만든 이유 (관리자에게 보여 줄 짧은 설명)
    discount_rate: int | None = None  # 글에서 가장 큰 할인율(%). 요약기가 읽어 준 값, 모르면 None
    discount_amount: int | None = None  # 글에서 가장 큰 할인·적립 금액(원)


def split_benefit_header(answer: str) -> tuple[int | None, int | None, str]:
    """모델 답 맨 위의 '할인율: N' '할인액: N' 줄을 떼어 낸다."""
    rate = amount = None
    m = _RATE_LINE.search(answer)
    if m:
        try:
            rate = int(float(m.group(1)))
        except ValueError:
            rate = None
        answer = answer[: m.start()] + answer[m.end() :]
    m = _AMOUNT_LINE.search(answer)
    if m:
        try:
            amount = int(m.group(1).replace(",", ""))
        except ValueError:
            amount = None
        answer = answer[: m.start()] + answer[m.end() :]
    return rate, amount, answer.strip()


def estimate_discount(text: str) -> tuple[int | None, int | None]:
    """요약기 없이 본문·제목 숫자로 가장 큰 할인율(%)과 할인 금액(원)을 대략 읽는다. 없으면 None."""
    rates = [int(m.group(1)) for m in _RATE_LEAD.finditer(text)] + [int(m.group(1)) for m in _RATE_TRAIL.finditer(text)]
    rates = [r for r in rates if 0 < r <= 100]
    amounts: list[int] = []
    for pattern in (_AMOUNT, _AMOUNT_AFTER):
        for m in pattern.finditer(text):
            try:
                n = int(m.group(1).replace(",", ""))
            except ValueError:
                continue
            unit = m.group(2)
            n *= 10000 if unit == "만" else 1000 if unit == "천" else 1
            amounts.append(n)
    return (max(rates) if rates else None), (max(amounts) if amounts else None)


def clean_summary(raw: str, *, title: str = "", max_chars: int = 500) -> str:
    """모델 답을 채널에 낼 수 있게 다듬는다: 코드 블록·URL·마크다운 글머리 제거, 제목 반복 제거, 길이 맞춤."""
    out: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or _FENCE.match(s):
            continue
        s = _BULLET.sub("· ", s)
        if _URL_IN_LINE.search(s):
            s = _URL_IN_LINE.sub("", s).rstrip(" :-·")
        s = s.strip("`*_\"“”")
        if not s or s == "·":
            continue
        if title and s.replace(" ", "") == title.replace(" ", ""):
            continue
        out.append(s)
    text = "\n".join(dict.fromkeys(out))
    if len(text) > max_chars:
        cut = text[:max_chars]
        if "\n" in cut[max_chars // 2 :]:
            cut = cut[: cut.rfind("\n")]
        text = cut.rstrip() + "…"
    return text


class InfoSummarizer:
    """게시판 글 → 채널 안내문. 키가 없으면 configured=False 로 조용히 꺼져 있다."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        max_chars: int = 500,
        timeout: float = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_chars = max_chars
        self.timeout = timeout
        self.last_error: str | None = None
        self.disabled_reason: str | None = None  # 키·모델 오류처럼 다시 해도 안 되는 것은 이 프로세스 동안 끈다
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def available(self) -> bool:
        return self.configured and self.disabled_reason is None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self.api_key, timeout=self.timeout, max_retries=2)
        return self._client

    @staticmethod
    def _user_message(*, title: str, source: str, text: str, links: list[str]) -> str:
        parts = [f"[게시판] {source}", f"[제목] {title}", "[본문]", text.strip() or "(본문 없음)"]
        if links:
            parts.append("[본문에 있는 링크] (참고용, 답에는 쓰지 말 것)")
            parts += [f"- {u}" for u in links[:3]]
        return "\n".join(parts)

    async def summarize(self, *, title: str, source: str, text: str, links: list[str] | None = None) -> Summary:
        if not self.configured:
            return Summary(error="요약 기능이 꺼져 있음 (ANTHROPIC_API_KEY 없음)")
        if self.disabled_reason:
            return Summary(error=self.disabled_reason)
        if len(text.strip()) < 10:
            return Summary(error="본문이 거의 없음")
        try:
            response = await self._get_client().messages.create(
                model=self.model,
                max_tokens=4000,  # 생각 토큰까지 포함한 상한. 답 자체는 몇백 자
                system=SYSTEM_PROMPT.format(max_chars=self.max_chars),
                messages=[{"role": "user", "content": self._user_message(title=title, source=source, text=text, links=links or [])}],
                # thinking 은 지정하지 않는다: Claude Opus 5 는 기본이 adaptive 라 같고, 다른 모델을 넣어도 400 이 안 난다
            )
        except anthropic.AuthenticationError:
            self.disabled_reason = "ANTHROPIC_API_KEY 가 올바르지 않음"
            log.error("summarizer: authentication failed — check ANTHROPIC_API_KEY")
            return Summary(error=self.disabled_reason)
        except anthropic.PermissionDeniedError as e:
            self.disabled_reason = f"API 키 권한 부족 ({e.message})"
            log.error("summarizer: permission denied: %s", e.message)
            return Summary(error=self.disabled_reason)
        except anthropic.NotFoundError:
            self.disabled_reason = f"모델 이름이 잘못됨 ({self.model})"
            log.error("summarizer: model not found: %s", self.model)
            return Summary(error=self.disabled_reason)
        except anthropic.RateLimitError:
            self.last_error = "API 요청 한도 초과 (잠시 뒤 다시 됨)"
            log.warning("summarizer: rate limited")
            return Summary(error=self.last_error)
        except anthropic.APIStatusError as e:
            self.last_error = f"API 오류 {e.status_code}"
            log.warning("summarizer: API error %s: %s", e.status_code, e.message)
            return Summary(error=self.last_error)
        except anthropic.APIConnectionError:
            self.last_error = "API 연결 실패 (네트워크)"
            log.warning("summarizer: connection error")
            return Summary(error=self.last_error)
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            log.warning("summarizer failed: %s", self.last_error)
            return Summary(error=self.last_error)

        answer = "".join(getattr(block, "text", "") for block in response.content if getattr(block, "type", "") == "text").strip()
        if getattr(response, "stop_reason", None) == "max_tokens":
            self.last_error = "답이 길어서 잘림"
            return Summary(error=self.last_error)
        rate, amount, answer = split_benefit_header(answer)
        if answer.strip("` .\n").upper() == SKIP_TOKEN:
            return Summary(skip=True, discount_rate=rate, discount_amount=amount)
        cleaned = clean_summary(answer, title=title, max_chars=self.max_chars)
        if len(cleaned) < 15 or any(w in cleaned for w in ("죄송", "요약할 수 없", "정리할 수 없", "제공되지 않")):
            self.last_error = "쓸 만한 요약이 안 나옴"
            log.info("summarizer: unusable answer for %r: %r", title[:40], answer[:120])
            return Summary(error=self.last_error)
        self.last_error = None
        return Summary(text=cleaned, discount_rate=rate, discount_amount=amount)

    def describe(self) -> dict[str, Any]:
        return {"configured": self.configured, "model": self.model, "disabled_reason": self.disabled_reason, "last_error": self.last_error}
