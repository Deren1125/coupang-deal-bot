"""API 크레딧 (주인 11/21·11/22 — 'Sns-board 의 Claude 호출(분류·경로·글쓰기, 쌀먹 대리 포함)을 API 키 방식으로 · 하루 상한은 지금처럼',
블로그 서버 합계 월 $160 · 넘거나 키가 없거나 틀리면 claude CLI(구독)로)."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest

from dealbot import api_credit, cli_env, commentary
from dealbot.llm_json import JsonAsker
from dealbot.summarize import InfoSummarizer

NOW = datetime(2026, 11, 24, 10, 0)


def mid(tail: str) -> str:
    return "claude" + "-" + tail  # 저장소에 버전 붙은 모델 이름을 글자 그대로 두지 않음


class FakeClient:
    """anthropic.AsyncAnthropic 대역 — models.list 와 messages.create 를 흉내."""

    def __init__(self, text: str = '{"a": 1}', raise_: Exception | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.text, self.raise_ = text, raise_
        self.models = NS(list=self._list)
        self.messages = NS(create=self._create)

    async def _list(self, **kw: Any) -> Any:
        return NS(data=[NS(id=mid("fable-9-9")), NS(id=mid("opus-9-9")), NS(id=mid("sonnet-9-9")), NS(id=mid("haiku-9-9"))])

    async def _create(self, **kw: Any) -> Any:
        self.sent.append(kw)
        if self.raise_:
            raise self.raise_
        return NS(stop_reason="end_turn", model=kw["model"], content=[NS(type="text", text=self.text)],
                  usage=NS(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0,
                           server_tool_use=None))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    import anthropic

    c = FakeClient()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kw: c)
    api_credit._resolved.clear()
    return c


def test_ledger_shared_with_blogauto_and_month_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    assert api_credit.allow("commentary", NOW) == (False, "no_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    assert api_credit.allow("commentary", NOW) == (True, "ok")
    api_credit.record("commentary", mid("sonnet-9-9"), {"input_tokens": 1_000_000}, now=NOW)  # 갈래 값(넉넉히) $3
    # Blog-Auto(blogauto/api_credit.py)가 같은 파일에 적는 줄 — 합계로 본다
    with open(api_credit.ledger_dir() / "2026-11.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-11-03T09:00:00", "src": "blogauto", "job": "photos.pick", "usd": 156.5}) + "\n")
    t = api_credit.totals(NOW)
    assert t["month"] == 159.5 and t["today"] == 3.0
    assert api_credit.monthly_budget() == 160.0 and api_credit.allow("commentary", NOW) == (True, "ok")  # 하루 몫은 안 봄
    api_credit.record("commentary", mid("sonnet-9-9"), {"output_tokens": 100_000}, now=NOW)
    assert api_credit.allow("commentary", NOW) == (False, "month_cap")
    row = json.loads((api_credit.ledger_dir() / "2026-11.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == {"at", "src", "job", "model", "in", "out", "cw", "cr", "ws", "usd"} and row["src"] == "dealbot"


def test_tasks_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    for t in ("commentary", "summarize", "xstudio", "ssalmuk", "seoi"):  # 올라가는 글·쌀먹 Claude 요청 = Claude API
        assert api_credit.provider(t) == "api"
    assert api_credit.provider("nl") == "gemini"  # 분류·경로 = Gemini (주인 11/22)
    monkeypatch.setenv("DEALBOT_API_TASKS", "commentary:cli, xstudio:api")
    assert api_credit.provider("commentary") == "cli" and api_credit.provider("unknown") == "cli"
    # 올라가는 글: Fable·Mythos·Haiku 안 씀 · Opus 까지 그대로
    assert api_credit.api_model("opus") == "opus" and api_credit.api_model(None) == "sonnet"
    for bad in ("haiku", "fable", mid("mythos-9-9")):
        assert api_credit.api_model(bad) == "sonnet"
    assert api_credit.api_model("haiku", posted=False) == "haiku"


def test_cli_never_gets_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_API_CREDIT_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(k, "secret")
    env = cli_env.cli_env({"X": "1"})
    assert env["X"] == "1" and not {"ANTHROPIC_API_KEY", "CLAUDE_API_CREDIT_KEY", "ANTHROPIC_AUTH_TOKEN"} & set(env)


async def test_ask_resolves_alias_without_fable_and_records(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    got = await api_credit.ask("xstudio", "opus", "sys", "u", schema={"type": "object", "properties": {"a": {"type": "integer"}}})
    assert got == {"a": 1} and fake.sent[-1]["model"] == mid("opus-9-9")
    assert fake.sent[-1]["output_config"]["format"]["schema"]["additionalProperties"] is False
    monkeypatch.setenv("DEALBOT_API_MODEL_SONNET", "my-sonnet")
    await api_credit.ask("commentary", "sonnet", "s", "u", schema={"type": "object"})
    assert fake.sent[-1]["model"] == "my-sonnet"
    assert api_credit.totals()["month"] > 0


async def test_commentary_api_first_then_cli_on_error(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    fake.text = json.dumps({"comment": "a", "thread_hook": "b", "thread_take": "c", "short_name": "d"})
    monkeypatch.setattr(commentary, "find_claude", lambda: None)
    c = commentary.Commentator(enabled=True)
    assert c.available  # CLI 가 없어도 API 로
    assert (await c._ask("u"))["comment"] == "a" and fake.sent[-1]["model"] == mid("sonnet-9-9")
    # API 오류 → CLI (여기선 CLI 가 없어 실패로 셈)
    fake.raise_ = RuntimeError("boom")
    assert await c._ask("u") is None and c.fail_streak == 1


async def test_key_error_marks_failed_and_falls_back(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake.raise_ = anthropic.AuthenticationError("bad", response=httpx.Response(401, request=req), body=None)
    a = JsonAsker(None, cli_bin="/fake/claude", task="xstudio")
    seen = []

    async def cli(self: JsonAsker, *args: Any) -> dict[str, Any]:
        seen.append(1)
        return {"cli": True}

    monkeypatch.setattr(JsonAsker, "_ask_cli", cli)
    assert await a.ask("s", "u", {"type": "object"}) == {"cli": True} and a.last_backend == "cli"
    assert api_credit.failed_until() is not None
    assert await a.ask("s", "u", {"type": "object"}) == {"cli": True} and len(fake.sent) == 1  # 쉬는 동안 API 안 부름


async def test_json_asker_api_first_with_images_and_old_paths_unchanged(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    a = JsonAsker(None, cli_bin="/fake/claude", cli_model="opus", task="xstudio", effort="medium")
    assert a.backend == "cli"  # 키 없음 → 예전처럼 CLI
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    got = await a.ask("s", "u", {"type": "object"}, images=[b"\xff\xd8jpeg"])
    assert got == {"a": 1} and a.last_backend == "api" and a.last_cli_models == ["opus"]
    sent = fake.sent[-1]
    assert sent["model"] == mid("opus-9-9") and sent["messages"][0]["content"][0]["type"] == "image"
    assert sent["output_config"]["effort"] == "medium"
    # 일 이름 없는 호출기(task=None)는 예전 그대로 — 키가 있어도 CLI
    assert JsonAsker(None, cli_bin="/fake/claude").task is None


async def test_summarizer_uses_api_then_cli(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    monkeypatch.setattr(commentary, "find_claude", lambda: "/fake/claude")
    s = InfoSummarizer(None, use_cli=True)
    called = []

    async def cli(system: str, user: str) -> tuple[str | None, str | None]:
        called.append(1)
        return "CLI 가 정리한 이벤트 안내 문구입니다 충분히 길게", None

    monkeypatch.setattr(s, "_ask_cli", cli)
    text = "본문 " * 20
    await s.summarize(title="t", source="s", text=text)
    assert called and not fake.sent  # 키 없음 → CLI
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    fake.text = "API 가 정리한 이벤트 안내 문구입니다 충분히 길게 씀"
    out = await s.summarize(title="t", source="s", text=text)
    assert out.text and "API" in out.text and len(called) == 1 and fake.sent[-1]["model"] == mid("sonnet-9-9")


class GResp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.status_code, self._t, self.text = status, text, ""

    def json(self) -> Any:
        return {"candidates": [{"content": {"parts": [{"text": self._t}]}}],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 50}}


class GHttp:
    def __init__(self, answers: list[str]) -> None:
        self.answers, self.urls, self.headers = answers, [], []

    async def post(self, url: str, json: Any = None, headers: Any = None) -> GResp:  # noqa: A002
        self.urls.append(url)
        self.headers.append(headers)
        return GResp(self.answers.pop(0))

    async def aclose(self) -> None:
        return None


async def test_nl_goes_gemini_first_then_claude_api_then_cli(monkeypatch: pytest.MonkeyPatch, fake: FakeClient) -> None:
    """주인 11/22: 말 명령(분류·경로) = Gemini 싼 모델 → (모양이 틀리면 한 번 더) → Claude API → CLI · Gemini 줄은 따로 셈."""
    import httpx

    g = GHttp(['{"cmd": "deals"}'])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: g)
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    schema = {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}
    a = JsonAsker(None, cli_bin="/fake/claude", task="nl", posted=False)
    assert await a.ask("s", "u", schema) == {"cmd": "deals"} and a.last_backend == "gemini"
    assert api_credit.GEMINI_CHEAP in g.urls[-1] and "gk" not in g.urls[-1] and g.headers[-1]["x-goog-api-key"] == "gk"
    assert api_credit.totals(prov=api_credit.GEMINI)["month"] > 0 and api_credit.totals()["month"] == 0
    g.answers[:] = ['{"x": 1}', "글"]  # 두 번 다 틀림 → Claude API
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    fake.text = '{"cmd": "api"}'
    assert await a.ask("s", "u", schema) == {"cmd": "api"} and a.last_backend == "api" and len(g.urls) == 3
    monkeypatch.setenv("GEMINI_CREDIT_MONTHLY_USD", "0.000001")  # Gemini 달 상한 → 바로 Claude
    assert await a.ask("s", "u", schema) == {"cmd": "api"} and len(g.urls) == 3
