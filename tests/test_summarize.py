"""정보 글 요약기: 모델 답 다듬기와 API 오류 처리 (실제 API 는 부르지 않는다)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx

from dealbot.summarize import InfoSummarizer, clean_summary, estimate_discount


class FakeMessages:
    def __init__(self, answer: str | Exception, stop: str = "end_turn") -> None:
        self.answer = answer
        self.stop = stop
        self.calls: list[dict] = []

    async def create(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(kw)
        if isinstance(self.answer, Exception):
            raise self.answer
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking="…"), SimpleNamespace(type="text", text=self.answer)],
            stop_reason=self.stop,
        )


def _summarizer(answer: str | Exception, stop: str = "end_turn", max_chars: int = 300) -> tuple[InfoSummarizer, FakeMessages]:
    s = InfoSummarizer("sk-test", model="claude-opus-5", max_chars=max_chars)
    fm = FakeMessages(answer, stop)
    s._client = SimpleNamespace(messages=fm)  # type: ignore[assignment]
    return s, fm


def test_clean_summary_strips_markdown_urls_and_title() -> None:
    raw = "```\n페이코 이벤트\n**12,000원 이상 결제 시 4,800원 할인**\n- 기간: 9/12~9/14 https://event.payco.com/1\n* 조건: 페이코 앱 결제\n- https://only.url/x\n```"
    assert clean_summary(raw, title="페이코 이벤트") == "12,000원 이상 결제 시 4,800원 할인\n· 기간: 9/12~9/14\n· 조건: 페이코 앱 결제"
    long = "\n".join(f"· 줄 {i} 내용입니다" for i in range(60))
    cut = clean_summary(long, max_chars=100)
    assert len(cut) <= 101 and cut.endswith("…") and "\n" in cut


async def test_summarize_happy_path() -> None:
    s, fm = _summarizer("12,000원 이상 결제하면 4,800원 할인\n- 기간: 9월 12일~14일")
    r = await s.summarize(title="[페이코] 결제 이벤트", source="루리웹", text="본문 " * 20, links=["https://event.payco.com/1"])
    assert r.text == "12,000원 이상 결제하면 4,800원 할인\n· 기간: 9월 12일~14일" and not r.skip and r.error is None
    call = fm.calls[0]
    assert call["model"] == "claude-opus-5" and "300자 이내" in call["system"]
    user = call["messages"][0]["content"]
    assert "[제목] [페이코] 결제 이벤트" in user and "[게시판] 루리웹" in user and "https://event.payco.com/1" in user


async def test_summarize_skip_and_unusable_answers() -> None:
    s, _ = _summarizer("SKIP")
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).skip
    s, _ = _summarizer("죄송하지만 요약할 수 없습니다.")
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.text is None and r.error
    s, _ = _summarizer("절반만 나온 답이 여기까지 왔습니다", stop="max_tokens")
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).error == "답이 길어서 잘림"
    s, fm = _summarizer("무엇이든")
    assert (await s.summarize(title="t", source="s", text="짧음")).error == "본문이 거의 없음" and not fm.calls


async def test_summarize_errors() -> None:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    s, _ = _summarizer(anthropic.APIConnectionError(request=req))
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.text is None and "연결" in (r.error or "") and s.available  # 일시적 오류: 다음 글에서 다시 시도
    s, fm = _summarizer(anthropic.AuthenticationError("bad key", response=httpx.Response(401, request=req), body=None))
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert "ANTHROPIC_API_KEY" in (r.error or "") and not s.available
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).error == r.error and len(fm.calls) == 1  # 다시 부르지 않음
    off = InfoSummarizer(None)
    assert not off.configured and "ANTHROPIC_API_KEY" in ((await off.summarize(title="t", source="s", text="본문 " * 10)).error or "")


async def test_summarize_reads_benefit_header() -> None:
    s, _ = _summarizer("할인율: 60\n할인액: 269,400원\n\n라코스테 최대 60% 세일\n- 기간: 9/11~9/14")
    r = await s.summarize(title="[LF몰] 라코스테 최대 60%세일", source="뽐뿌", text="본문 " * 20)
    assert r.discount_rate == 60 and r.discount_amount == 269400
    assert r.text == "라코스테 최대 60% 세일\n· 기간: 9/11~9/14"
    s, _ = _summarizer("할인율: 0\n할인액: 0\n\nSKIP")
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.skip and r.discount_rate == 0


def test_estimate_discount_from_text() -> None:
    assert estimate_discount("12,000원 이상 결제 시 4,800원 할인") == (None, 4800)  # 조건 금액은 혜택이 아님
    assert estimate_discount("[LF몰] 라코스테 최대 60%세일 추천상품들") == (60, None)
    assert estimate_discount("전 품목 30% 할인 + 최대 6만원 적립") == (30, 60000)
    assert estimate_discount("더미식 교자 (20,900원/무료) 쿠폰 적용가") == (None, None)  # 판매가는 혜택 금액이 아니다
    assert estimate_discount("5천원 쿠폰 지급, 100% 정품") == (None, 5000)
    assert estimate_discount("클릭적립 합계 58원\n라이브 예고 적립 3원") == (None, 58)
    assert estimate_discount("9월 12일 새 매장 오픈 안내") == (None, None)
