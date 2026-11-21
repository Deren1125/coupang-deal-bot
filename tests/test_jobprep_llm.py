"""취준 AI — 캐시 · Gemini 하루 횟수·달 몫 · Claude 상한(하루·달·첫 주) · 공용 장부 'jobprep.' 줄 · CLI 로 안 넘어감 · 키 글이 로그·답에 없음."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dealbot import api_credit
from dealbot.dashboard.jobprep import llm as llmmod
from jobprep_helpers import FakeResp, make_jp

KEY = "sekrit-JOBPREP-key-9f8e"
SHARED = "shared-GEMINI-key-1a2b"


@pytest.fixture
def jp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("JOBPREP_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return make_jp(tmp_path, monkeypatch)


async def test_cache_second_call_does_not_hit(jp, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", KEY)
    jp.fake_http.gemini = [{"a": 1}]
    caplog.set_level(logging.INFO)
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    a = await jp.llm.gemini("t.x", "fast", "sys", "user prompt", schema=schema)
    b = await jp.llm.gemini("t.x", "fast", "sys", "user prompt", schema=schema)
    assert a == b == {"a": 1}
    posts = [c for c in jp.fake_http.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["x-goog-api-key"] == KEY and KEY not in posts[0][1]  # 키는 머리글에만 (주소에 없음)
    assert KEY not in caplog.text and "user prompt" not in caplog.text and KEY not in str(a)
    assert jp.llm.gemini_spent()["fast"] == 1
    # 키가 다른 모델·판 번호면 다시 부름
    jp.fake_http.gemini = [{"a": 2}]
    assert (await jp.llm.gemini("t.x", "fast", "sys", "user prompt", schema=schema, ver=2)) == {"a": 2}


async def test_own_key_not_in_shared_ledger_shared_key_is(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", KEY)
    await jp.llm.gemini("t.own", "fast", "s", "u", cache=False)
    assert api_credit.totals(prov="gemini")["month"] == 0
    monkeypatch.delenv("JOBPREP_GEMINI_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", SHARED)
    await jp.llm.gemini("t.shared", "fast", "s", "u", cache=False)
    text = (api_credit.ledger_dir()).glob("*.jsonl")
    rows = "".join(p.read_text() for p in text)
    assert '"job": "jobprep.t.shared"' in rows and '"prov": "gemini"' in rows
    assert jp.fake_http.calls[-1][2]["x-goog-api-key"] == SHARED


async def test_gemini_daily_cap_and_shared_month_cap(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GEMINI_API_KEY", SHARED)
    jp.conf.gemini_calls_per_day = {"fast": 2, "good": 1}
    await jp.llm.gemini("a", "fast", "s", "1", cache=False)
    await jp.llm.gemini("a", "fast", "s", "2", cache=False)
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.gemini("a", "fast", "s", "3", cache=False)
    assert e.value.code == "cap" and e.value.status == 429
    # 달 추정액 상한 (공용 키일 때만)
    jp.store.x("INSERT INTO usage (day, kind, n, usd) VALUES (?, 'gemini:good', 0, 3.5)", (jp.llm.day(),))
    with pytest.raises(llmmod.CapError):
        await jp.llm.gemini("b", "good", "s", "x", cache=False)
    n = len(jp.fake_http.calls)
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", KEY)  # 취준 키면 달 몫은 안 봄 (하루 횟수만)
    await jp.llm.gemini("b", "good", "s", "x", cache=False)
    assert len(jp.fake_http.calls) == n + 1


async def test_no_key_and_key_error(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.gemini("a", "fast", "s", "u")
    assert e.value.code == "no_key" and jp.fake_http.calls == []
    monkeypatch.setenv("GEMINI_API_KEY", SHARED)
    jp.fake_http.gemini = [FakeResp(403, {"error": "denied"})]
    with pytest.raises(llmmod.LlmError):
        await jp.llm.gemini("a", "fast", "s", "u")
    assert api_credit.failed_until(prov="gemini") is not None


class FakeClaude:
    def __init__(self, usd_per_call: float = 0.3, fail: bool = False) -> None:
        self.calls = 0
        self.usd = usd_per_call
        self.fail = fail

    async def __call__(self, task: str, model: str, system: str, user: str, **kw: object) -> str:
        self.calls += 1
        if self.fail:
            raise api_credit.ApiError("boom")
        # 진짜 ask 처럼 공용 장부에 한 줄 (sonnet 값 $3/$15 — in 100k 토큰 = $0.3)
        api_credit.record(task, "sonnet", {"input_tokens": int(self.usd / 3.0 * 1e6), "output_tokens": 0})
        assert task.startswith("jobprep.")
        return "답"


async def test_claude_caps_first_week_day_month(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    fake = FakeClaude(0.3)
    jp.llm.claude_ask = fake
    assert jp.llm.claude_daily_cap() == 0.5  # 처음 7일
    assert await jp.llm.claude("chat", "s", "u") == "답"
    assert await jp.llm.claude("chat", "s", "u") == "답"  # $0.3 < $0.5
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.claude("chat", "s", "u")  # $0.6 ≥ $0.5 → 안 부름
    assert e.value.status == 429 and e.value.code == "cap" and fake.calls == 2
    assert jp.llm.claude_spent()["today"] == pytest.approx(0.6, abs=0.01)
    # 7일 뒤: 하루 $1
    jp.fake_clock.t += 8 * 86400
    assert jp.llm.claude_daily_cap() == 1.0
    await jp.llm.claude("chat", "s", "u")
    # 달 상한 $10
    jp.store.x("INSERT INTO usage (day, kind, n, usd) VALUES ('" + jp.llm.month() + "-01', 'claude', 1, 9.9)")
    with pytest.raises(llmmod.CapError):
        await jp.llm.claude("chat", "s", "u")
    assert fake.calls == 3


async def test_claude_never_falls_back_to_cli(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    fake = FakeClaude(fail=True)
    jp.llm.claude_ask = fake
    with pytest.raises(llmmod.LlmError):
        await jp.llm.claude("chat", "s", "u")
    assert fake.calls == 1
    src = Path(llmmod.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in src and "cli_env" not in src and "find_claude" not in src


async def test_claude_without_key_is_refused_without_call(jp) -> None:  # type: ignore[no-untyped-def]
    fake = FakeClaude()
    jp.llm.claude_ask = fake
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.claude("chat", "s", "u")
    assert e.value.code == "no_key" and fake.calls == 0


class _Block:
    type = "text"

    def __init__(self, t: str) -> None:
        self.text = t


class _Msgs:
    def __init__(self, stop: str) -> None:
        self.stop, self.kw = stop, {}

    async def create(self, **kw):  # type: ignore[no-untyped-def]
        self.kw = kw
        return type("R", (), {"model": "test-sonnet-x", "stop_reason": self.stop, "content": [_Block("긴 답 …")],
                              "usage": {"input_tokens": 1000, "output_tokens": 4000}})()


async def test_claude_call_returns_truncated_text_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEALBOT_API_MODEL_SONNET", "test-sonnet-x")
    client = type("C", (), {"messages": _Msgs("max_tokens")})()
    out = await llmmod.claude_call("jobprep.chat", "sonnet", "s", "u", client=client)
    assert out.startswith("긴 답") and "(잘림" in out and client.messages.kw["max_tokens"] == llmmod.CLAUDE_MAX_TOKENS == 4000
    assert api_credit.totals()["month"] > 0  # 값은 장부에
    client = type("C", (), {"messages": _Msgs("refusal")})()
    with pytest.raises(api_credit.ApiError):
        await llmmod.claude_call("jobprep.chat", "sonnet", "s", "u", client=client)


async def test_gemini_sets_thinking_budget(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", KEY)
    jp.fake_http.gemini = [{"a": 1}, "글"]
    await jp.llm.gemini("t", "good", "s", "u", schema={"type": "object", "properties": {}, "required": []}, cache=False)
    await jp.llm.gemini("t", "good", "s", "u2", cache=False, thinking=1024)
    bodies = [c[3] for c in jp.fake_http.calls if c[0] == "POST"]
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert bodies[1]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1024}
