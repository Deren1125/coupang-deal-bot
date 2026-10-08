"""정보 글 요약기: 모델 답 다듬기와 API 오류 처리 (실제 API·CLI 는 부르지 않는다)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import dealbot.commentary as commentary
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


def test_clean_summary_normalizes_board_markers_and_inline_bold() -> None:
    """모델이 원문의 ▶ / > 표시나 굵은 글씨(**)를 따라 써도 채널에는 '· ' 글머리 평문만 나간다."""
    raw = "> 쿠폰 할인 최대 7%\n▶ Galaxy Tab S12 11인치 1,099,000원\n**기간**: 10/7~10/20\n- 카드 즉시할인 10%\n→ 결제 후 자동 적립"
    assert clean_summary(raw) == (
        "· 쿠폰 할인 최대 7%\n· Galaxy Tab S12 11인치 1,099,000원\n기간: 10/7~10/20\n· 카드 즉시할인 10%\n· 결제 후 자동 적립"
    )
    # 숫자로 시작하는 줄은 건드리지 않는다 (글머리로 잘못 읽으면 숫자가 바뀜)
    assert clean_summary("1.5배 적립\n10.5% 할인\n-10% 쿠폰\n3.1절 이벤트") == "1.5배 적립\n10.5% 할인\n-10% 쿠폰\n3.1절 이벤트"
    assert clean_summary("1. 첫째 조건\n2) 둘째 조건") == "· 첫째 조건\n· 둘째 조건"


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


def test_estimate_discount_does_not_read_prices_as_benefits() -> None:
    """판매가 뒤 줄의 '쿠폰/할인' 이나 '쿠폰 적용가' 를 혜택 금액으로 읽으면 7% 쿠폰 글이 5,000원 기준을 넘어 버린다."""
    assert estimate_discount("▶ Galaxy Tab S12 11인치 1,099,000원\n쿠폰 할인 최대 7%") == (7, None)
    assert estimate_discount("Galaxy Tab S12 1,599,000원 쿠폰 적용시") == (None, None)
    assert estimate_discount("상품 29,900원\n할인 쿠폰 받기") == (None, None)
    assert estimate_discount("29,900원 쿠폰적용가") == (None, None)
    assert estimate_discount("정가 39,000원\n할인가 19,000원") == (None, None)
    assert estimate_discount("정가 39,000원 할인가 19,000원") == (None, None)
    assert estimate_discount("판매가 129,000원\n쿠폰 적용가 119,000원") == (None, None)
    assert estimate_discount("결제 금액 20%\n할인 쿠폰은 별도") == (None, None)  # 다음 줄의 '할인' 을 끌어오지 않음
    assert estimate_discount("캐시백\n5,000원 상당 굿즈") == (None, None)  # 다른 줄의 금액을 혜택으로 붙이지 않음
    # 진짜 혜택 금액은 그대로 읽는다
    assert estimate_discount("5,000원 쿠폰 증정") == (None, 5000)
    assert estimate_discount("3,000원 할인쿠폰 지급") == (None, 3000)
    assert estimate_discount("5,000원 할인 쿠폰") == (None, 5000)
    assert estimate_discount("최대 6만원 적립") == (None, 60000)


# ---- 서버 claude CLI 로 요약 (API 키가 없을 때) ----


def _fake_claude(d: Path, monkeypatch: pytest.MonkeyPatch, payload: dict | str, *, sleep: float = 0) -> Path:
    """claude 대신 쓰는 가짜 실행 파일: 받은 인자·입력을 d 에 남기고 정해 둔 출력을 낸다."""
    d.mkdir(parents=True, exist_ok=True)
    out = d / "out.json"
    out.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    script = d / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{d}/args.txt"\n'
        f'cat > "{d}/stdin.txt"\n'
        f'echo x >> "{d}/calls.txt"\n'
        + (f"exec sleep {sleep}\n" if sleep else "")  # 시간 초과 흉내: 답 없이 버틴다 (exec 라 kill 이 바로 먹힘)
        + f'cat "{out}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(commentary, "find_claude", lambda: str(script))
    return script


def _calls(d: Path) -> int:
    f = d / "calls.txt"
    return len(f.read_text().splitlines()) if f.exists() else 0


async def test_summarize_falls_back_to_claude_cli_without_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """키가 없어도 서버에 로그인된 claude CLI 가 있으면 그걸로 요약한다 (한줄평과 같은 CLI)."""
    answer = "할인율: 60\n할인액: 0\n\n라코스테 최대 60% 세일\n- 기간: 9/11~9/14\n▶ 대상: LF몰 회원"
    _fake_claude(tmp_path, monkeypatch, {"type": "result", "is_error": False, "result": answer})
    s = InfoSummarizer(None, max_chars=300)
    assert s.configured and s.available and s.backend == "cli" and s.describe()["backend"] == "cli"
    r = await s.summarize(title="[LF몰] 라코스테 최대 60%세일", source="뽐뿌", text="본문 " * 20, links=["https://www.lfmall.co.kr/e/1"])
    assert r.error is None and r.discount_rate == 60 and r.discount_amount == 0
    assert r.text == "라코스테 최대 60% 세일\n· 기간: 9/11~9/14\n· 대상: LF몰 회원"
    args = (tmp_path / "args.txt").read_text().splitlines()
    assert args[:3] == ["-p", "--output-format", "json"] and "--tools" in args and "--system-prompt" in args
    assert args[args.index("--tools") + 1] == "" and "300자 이내" in "\n".join(args)
    assert "--json-schema" not in args and "--model" not in args  # 답은 머리줄 붙은 평문, 모델은 CLI 기본값
    stdin = (tmp_path / "stdin.txt").read_text()
    assert "[제목] [LF몰] 라코스테 최대 60%세일" in stdin and "[게시판] 뽐뿌" in stdin

    # 모델을 정해 주면 --model 로 넘긴다. SKIP 도 API 와 똑같이 처리
    d = tmp_path / "skip"
    _fake_claude(d, monkeypatch, {"is_error": False, "result": "할인율: 0\n할인액: 0\n\nSKIP"})
    s = InfoSummarizer(None, cli_model="my-cli-model")
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.skip and r.discount_rate == 0
    args = (d / "args.txt").read_text().splitlines()
    assert args[args.index("--model") + 1] == "my-cli-model"


async def test_summarize_cli_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 로그인이 풀림 → 이 프로세스 동안 다시 부르지 않는다
    d = tmp_path / "auth"
    _fake_claude(d, monkeypatch, {"is_error": True, "result": "Invalid API key · Please run /login"})
    s = InfoSummarizer(None)
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.text is None and "로그인" in (r.error or "") and not s.available
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).error == r.error and _calls(d) == 1

    # 일시적 오류 (과부하 등) → 다음 글에서 다시 시도
    _fake_claude(tmp_path / "busy", monkeypatch, {"is_error": True, "result": "API Error: 529 overloaded"})
    s = InfoSummarizer(None)
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.text is None and r.error and s.available and s.last_error == r.error

    # 출력이 비었거나 깨짐, 답이 비어 있음 → 예외 없이 오류로
    for i, bad in enumerate(["", "not json", "[1, 2]", json.dumps({"is_error": False, "result": ""}), json.dumps({"is_error": False})]):
        _fake_claude(tmp_path / f"bad{i}", monkeypatch, bad)
        s = InfoSummarizer(None)
        r = await s.summarize(title="t", source="s", text="본문 " * 10)
        assert r.text is None and r.error and s.available, bad

    # 시간 초과 → 프로세스를 끝내고 오류로 (다음 글에서 다시 시도)
    _fake_claude(tmp_path / "slow", monkeypatch, {"is_error": False, "result": "늦은 답"}, sleep=5)
    s = InfoSummarizer(None, cli_timeout=0.5)
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert r.text is None and "시간" in (r.error or "") and s.available


async def test_summarize_backend_choice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_claude(tmp_path, monkeypatch, {"is_error": False, "result": "쓰이면 안 되는 답"})
    # API 키가 있으면 API 가 먼저 (CLI 는 부르지 않음)
    s, fm = _summarizer("12,000원 이상 결제하면 4,800원 할인")
    assert s.backend == "api"
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).text and fm.calls and _calls(tmp_path) == 0
    # CLI 를 쓰지 말라고 하면 (요약기를 설정에서 끈 경우) 꺼진 상태
    off = InfoSummarizer(None, use_cli=False)
    assert not off.configured and off.backend is None
    assert "ANTHROPIC_API_KEY" in ((await off.summarize(title="t", source="s", text="본문 " * 10)).error or "") and _calls(tmp_path) == 0
