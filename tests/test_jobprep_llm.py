"""취준 AI (주인 11/23 — Claude API 만) — 빠른/깊은 모델 나눔 · 캐시 · JSON 꺼내기·한 번 더 · 그림 넘김 · 상한(하루 $2·달 $30) ·
공용 GEMINI_API_KEY 안 씀 · 무료 취준 키일 때만 Gemini · CLI 로 안 넘어감 · 키 글이 로그·답에 없음."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from dealbot import api_credit
from dealbot.dashboard.jobprep import llm as llmmod
from jobprep_helpers import FakeResp, make_jp

KEY = "sekrit-JOBPREP-key-9f8e"
SHARED = "shared-GEMINI-key-1a2b"
SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}


class FakeClaude:
    """claude_ask 대신 — 받은 것을 모으고, 공용 장부에 한 줄 (sonnet 값으로 usd_per_call)."""

    def __init__(self, answers: list[str] | None = None, usd_per_call: float = 0.3, fail: bool = False) -> None:
        self.answers = list(answers or [])
        self.calls: list[dict] = []
        self.usd, self.fail = usd_per_call, fail

    async def __call__(self, task: str, model: str, system: str, user: str, **kw: object) -> str:
        self.calls.append({"task": task, "model": model, "system": system, "user": user, **kw})
        if self.fail:
            raise api_credit.ApiError("boom")
        api_credit.record(task, "sonnet", {"input_tokens": int(self.usd / 3.0 * 1e6), "output_tokens": 0})
        assert task.startswith("jobprep.")
        return self.answers.pop(0) if self.answers else "답"


@pytest.fixture
def jp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.delenv("JOBPREP_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", SHARED)  # 공용 유료 키가 있어도 안 씀
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    j = make_jp(tmp_path, monkeypatch)
    j.fake_claude = FakeClaude()
    j.llm.claude_ask = j.fake_claude
    return j


async def test_everything_goes_to_claude_with_cheap_and_strong_models(jp, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.INFO)
    jp.fake_claude.answers = ['{"a": 1}', "개념 설명"]
    assert jp.llm.provider() == "claude"
    a = await jp.llm.ask("jobs.extract", "fast", "sys", "user prompt", schema=SCHEMA)
    b = await jp.llm.ask("jobs.extract", "fast", "sys", "user prompt", schema=SCHEMA)  # 캐시
    c = await jp.llm.ask("ctci.ladder:1.1:v1", "good", "sys", "ladder")
    assert a == b == {"a": 1} and c == "개념 설명"
    calls = jp.fake_claude.calls
    assert [x["model"] for x in calls] == ["haiku", "sonnet"]  # 별칭 → api_credit.resolve 가 실제 id
    assert calls[0]["task"] == "jobprep.jobs.extract" and calls[1]["task"] == "jobprep.ctci.ladder"
    assert '"required": ["a"]' in calls[0]["system"] and "JSON" in calls[0]["system"]  # 스키마는 시스템 글에
    assert jp.fake_http.calls == []  # Gemini(공용 유료 키) 안 부름
    assert SHARED not in caplog.text and "user prompt" not in caplog.text
    assert api_credit.totals(prov="gemini")["month"] == 0
    assert jp.llm.kind_spent_today("ctci.ladder:9.9") > 0 and jp.llm.view()["provider"] == "claude"


async def test_json_retry_and_vision(jp) -> None:  # type: ignore[no-untyped-def]
    jp.fake_claude.answers = ["설명만 함", '앞말 {"a": 2} 뒷말']
    assert await jp.llm.ask("x", "fast", "s", "u", schema=SCHEMA) == {"a": 2}
    jp.fake_claude.answers = ["no", "still no"]
    with pytest.raises(llmmod.LlmError):
        await jp.llm.ask("y", "fast", "s", "u", schema=SCHEMA)
    jp.fake_claude.answers = ['{"a": 3}']
    await jp.llm.ask("acad.parse", "good", "s", "u", schema=SCHEMA, images=[(b"\xff\xd8jpg", "image/jpeg")])
    last = jp.fake_claude.calls[-1]
    assert last["model"] == "sonnet" and last["images"] == [(b"\xff\xd8jpg", "image/jpeg")]


async def test_claude_caps_day_and_month(jp) -> None:  # type: ignore[no-untyped-def]
    assert jp.llm.claude_daily_cap() == 2.0 and jp.conf.claude_monthly_usd == 30.0  # 첫 주 규칙 없음
    jp.fake_claude.usd = 0.9
    await jp.llm.ask("a", "good", "s", "1", cache=False)
    await jp.llm.ask("a", "good", "s", "2", cache=False)
    await jp.llm.ask("a", "good", "s", "3", cache=False)  # $1.8 < $2
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.ask("a", "good", "s", "4", cache=False)  # $2.7 ≥ $2 → 안 부름
    assert e.value.status == 429 and e.value.code == "cap" and len(jp.fake_claude.calls) == 3
    jp.fake_clock.t += 86400
    jp.store.x("INSERT INTO usage (day, kind, n, usd) VALUES ('" + jp.llm.month() + "-01', 'claude', 1, 29.9)")
    with pytest.raises(llmmod.CapError):
        await jp.llm.ask("a", "good", "s", "5", cache=False)
    assert len(jp.fake_claude.calls) == 3


async def test_chat_is_claude(jp) -> None:  # type: ignore[no-untyped-def]
    assert await jp.llm.claude("chat", "s", "u") == "답"
    assert jp.fake_claude.calls[0]["model"] == "sonnet" and jp.fake_claude.calls[0]["max_tokens"] == 4000


async def test_free_gemini_key_only_when_set(jp, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("JOBPREP_GEMINI_API_KEY", KEY)
    jp.conf.gemini_calls_per_day = {"fast": 1, "good": 1}
    jp.fake_http.gemini = [{"a": 1}]
    assert jp.llm.provider() == "gemini"
    assert await jp.llm.ask("t", "fast", "s", "u", schema=SCHEMA) == {"a": 1}
    post = [c for c in jp.fake_http.calls if c[0] == "POST"][0]
    assert post[2]["x-goog-api-key"] == KEY and KEY not in post[1] and KEY not in caplog.text
    assert post[3]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert api_credit.totals(prov="gemini")["month"] == 0 and jp.fake_claude.calls == []
    with pytest.raises(llmmod.CapError):
        await jp.llm.ask("t", "fast", "s", "u2", schema=SCHEMA)
    jp.fake_http.gemini = [FakeResp(403, {"error": "denied"})]
    with pytest.raises(llmmod.LlmError):
        await jp.llm.ask("t", "good", "s", "u3")


async def test_claude_never_falls_back_to_cli(jp) -> None:  # type: ignore[no-untyped-def]
    jp.llm.claude_ask = fake = FakeClaude(fail=True)
    with pytest.raises(llmmod.LlmError):
        await jp.llm.ask("chat", "fast", "s", "u")
    assert len(fake.calls) == 1
    src = Path(llmmod.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in src and "cli_env" not in src and "find_claude" not in src and "SHARED_GEMINI" not in src
    assert 'environ.get("GEMINI_API_KEY"' not in src


async def test_without_key_is_refused_without_call(jp, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(llmmod.CapError) as e:
        await jp.llm.ask("chat", "fast", "s", "u")
    assert e.value.code == "no_key" and jp.fake_claude.calls == []


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
