"""이벤트 참여(쌀먹) LLM 대리 호출 — POST /dashboard/api/ssalmuk/llm (주인 11/15 '접근 권한 나눠 받아', dashboard/ssalmuk_llm.py).

실제 Gemini·claude CLI 는 부르지 않는다 (httpx MockTransport · run_cli 갈아 끼움).
1) 인증: X-Ssalmuk-Key 만 (없음·틀림 401 — 아무것도 부르지 않음).
2) Claude: haiku·sonnet 만 (Opus·Fable·Mythos·모르는 이름 400) · CLI 인자 · 실제로 쓴 모델이 Fable 이면 버림 · json · 한 번에 1개.
3) Gemini: 키 없으면 503 no_key · 키는 헤더로만 · 허용 목록 · 몸 그대로 · 오류 502.
4) 한도: 하루 Gemini 40 · Claude 150 (Sonnet 50) · 1분 10 · 몸 32KB · state 응답 llm_proxy.
5) 개인정보: 전화·이메일 꼴 400 pii · 글·키는 로그·kv 에 없음.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from dash_helpers import push_settings
from dealbot import commentary
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import ssalmuk, ssalmuk_llm

LLM = ssalmuk.LLM_PATH
GKEY = "test-gemini-key-zzz"
SECRET_Q = "보라색 코끼리 퀴즈의 정답은"
SECRET_A = "무지개떡이 정답"


@pytest.fixture
def bot(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "no-such.env")
    settings.dashboard.ssalmuk_llm_per_minute = 1000  # 1분 한도는 따로 시험
    settings.secrets.gemini_api_key = None
    for k in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(k, raising=False)
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def desk(bot: DealBot) -> ssalmuk.Ssalmuk:
    return bot.dashboard.api.ssalmuk  # type: ignore[union-attr]


async def call(bot: DealBot, body: Any, key: str | None, raw: bytes | None = None, peer: str = "198.51.100.7") -> tuple[int, Any]:
    data = raw if raw is not None else json.dumps(body, ensure_ascii=False).encode()
    h = {":peer": peer, "content-type": "application/json", "user-agent": "ssalmuk-sync/1", "content-length": str(len(data))}
    if key is not None:
        h["x-ssalmuk-key"] = key
    res = await bot.web.dispatch("POST", LLM, h, data)
    return res[0], json.loads(res[1]) if isinstance(res[1], str) and res[1].startswith("{") else res[1]


def mid(tail: str) -> str:
    """시험용 모델 이름 (저장소에 버전 붙은 모델 ID 를 글자 그대로 두지 않음 — test_no_model_ids_in_the_repo)."""
    return "claude" + "-" + tail


def fake_cli(monkeypatch: pytest.MonkeyPatch, *, result: str = SECRET_A, usage: dict[str, Any] | None = None, is_error: bool = False,
             raises: BaseException | None = None, hold: asyncio.Event | None = None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def run(args: list[str], stdin: bytes, timeout: float, env: dict[str, str]) -> tuple[int, bytes, bytes]:
        calls.append({"args": args, "stdin": stdin.decode(), "timeout": timeout, "env": env})
        if hold is not None:
            await hold.wait()
        if raises is not None:
            raise raises
        model = args[args.index("--model") + 1]
        out = {"type": "result", "is_error": is_error, "result": result,
               "modelUsage": usage if usage is not None else {mid(f"{model}-9-9"): {"outputTokens": 5}}}
        return 0, json.dumps(out).encode(), b""

    monkeypatch.setattr(ssalmuk_llm, "run_cli", run)
    monkeypatch.setattr(commentary, "find_claude", lambda: "/fake/claude")
    return calls


def fake_gemini(bot: DealBot, handler: Any = None, *, key: str | None = GKEY) -> list[Any]:
    seen: list[Any] = []

    def h(req: Any) -> Any:
        seen.append(req)
        if handler is not None:
            return handler(req)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": SECRET_A}]}}], "usageMetadata": {"totalTokenCount": 9}})

    bot.http = httpx.AsyncClient(transport=httpx.MockTransport(h))
    bot.settings.secrets.gemini_api_key = key
    return seen


def cbody(**kw: Any) -> dict[str, Any]:
    return {"provider": "claude", "model": "haiku", "prompt": SECRET_Q, **kw}


def gbody(**kw: Any) -> dict[str, Any]:
    return {"v": 1, "purpose": "quiz", "model": "gemini-2.5-flash", "request": {"contents": [{"role": "user", "parts": [{"text": SECRET_Q}]}]}, **kw}


# ---------------------------------------------------------------- 1) 인증
async def test_llm_needs_key_and_calls_nothing(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, seen = fake_cli(monkeypatch), fake_gemini(bot)
    key = desk(bot).key()
    for k in (None, "", "wrong", key[:-1]):
        for body in (cbody(), gbody()):
            status, out = await call(bot, body, k)
            assert status == 401 and out["code"] == "bad_key", k
    assert calls == [] and seen == [] and desk(bot).llm_view()["claude"]["today"] == 0
    assert LLM in ssalmuk.PUBLIC_PATHS  # 로그인 없이 (키로 직접 확인)


# ---------------------------------------------------------------- 2) Claude
async def test_claude_ok_cli_args_and_shape(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    status, out = await call(bot, cbody(model="sonnet", system="퀴즈 풀이", max_tokens=300), key)
    assert status == 200 and out == {"ok": True, "text": SECRET_A, "model_used": mid("sonnet-9-9")}
    c = calls[0]
    a = c["args"]
    assert a[0] == "/fake/claude" and a[a.index("--model") + 1] == "sonnet" and a[a.index("--system-prompt") + 1] == "퀴즈 풀이"
    assert a[a.index("--tools") + 1] == "" and a[-2:] == ["--tools", ""] and c["stdin"] == SECRET_Q and c["timeout"] <= 60
    assert {"--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"} <= set(a)
    assert SECRET_Q not in " ".join(a)  # 질문은 표준 입력으로만 (옵션으로 읽힐 자리가 없음)
    assert c["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "300"
    # messages 꼴 (여러 줄이면 대화 글로)
    msgs = [{"role": "user", "content": "첫 질문"}, {"role": "assistant", "content": "첫 답"}, {"role": "user", "content": "둘째 질문"}]
    status, _ = await call(bot, {"provider": "claude", "model": "haiku", "messages": msgs}, key)
    assert status == 200 and "첫 질문" in calls[1]["stdin"] and "둘째 질문" in calls[1]["stdin"]
    assert calls[1]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1000"
    assert desk(bot).llm_view()["claude"] == {"today": 2, "cap": 150, "sonnet": 1, "sonnet_cap": 50}


async def test_claude_model_refused(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    for m in ("opus", "fable", "mythos", mid("fable-5"), mid("opus-5-5"), mid("sonnet-5-5"), "gpt-9", "", None, 3, "Sonnet", "haiku "):
        status, out = await call(bot, cbody(model=m), key)
        assert status == 400 and out["code"] == "bad_model", m
    assert calls == [] and desk(bot).llm_view()["claude"]["today"] == 0


async def test_claude_bad_body(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    bads = [cbody(prompt=""), cbody(prompt=None), cbody(max_tokens=2001), cbody(max_tokens=0), cbody(max_tokens="9"), cbody(json="yes"),
            cbody(system=3), cbody(messages=[{"role": "user", "content": "a"}]),  # prompt 와 messages 둘 다
            {"provider": "claude", "model": "haiku", "messages": []}, {"provider": "claude", "model": "haiku", "messages": [{"role": "system", "content": "a"}]},
            {"provider": "claude", "model": "haiku", "messages": [{"role": "assistant", "content": "a"}]}]
    for b in bads:
        status, out = await call(bot, b, key)
        assert status == 400 and out["code"] == "bad_body", b
    for b in ({"provider": "openai", "model": "x"}, {"model": "haiku", "prompt": "a"}):
        status, out = await call(bot, b, key)
        assert status == 400 and out["code"] == "bad_body"
    assert calls == []


async def test_claude_answer_from_fable_is_dropped(bot: DealBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    key = desk(bot).key()
    # Fable·Mythos·Opus · 모르는 이름 · 쓴 모델을 알 수 없음(빈 목록) → 모두 버림
    for used in ({mid("fable-5-2"): {}}, {mid("haiku-9"): {}, mid("mythos-1"): {}}, {mid("opus-5-5"): {}}, {"new-model-1": {}}, {}):
        fake_cli(monkeypatch, usage=used)
        with caplog.at_level(logging.DEBUG):
            status, out = await call(bot, cbody(), key)
        assert status == 503 and out["code"] == "ai_off" and SECRET_A not in json.dumps(out, ensure_ascii=False)
    # 별칭 환경 변수가 Fable 쪽이면 부르지도 않음
    calls = fake_cli(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", mid("fable-5-2"))
    status, out = await call(bot, cbody(), key)
    assert status == 503 and out["code"] == "ai_off" and calls == []
    # haiku·sonnet 별칭이 Opus 쪽이어도 부르지 않음 (opus 별칭이 Opus 인 건 괜찮음 — X 글쓰기가 씀)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", mid("opus-5-5"))
    assert (await call(bot, cbody(), key))[0] == 200
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", mid("opus-5-5"))
    status, out = await call(bot, cbody(), key)
    assert status == 503 and out["code"] == "ai_off" and len(calls) == 1


async def test_claude_low_memory_spawns_nothing_and_counts_nothing(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    monkeypatch.setattr(ssalmuk_llm, "mem_low", lambda: True)
    status, out = await call(bot, cbody(), key)
    assert status == 429 and out["code"] == "busy" and calls == [] and desk(bot).llm_view()["claude"]["today"] == 0
    monkeypatch.setattr(ssalmuk_llm, "mem_low", lambda: False)
    assert (await call(bot, cbody(), key))[0] == 200


async def test_claude_odd_text_is_refused_or_stays_on_stdin(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    for b in (cbody(prompt="짝 없는 \ud800 글자"), cbody(system="널 \x00 글자")):
        status, out = await call(bot, b, key, raw=json.dumps(b).encode())
        assert status == 400 and out["code"] == "bad_body"
    assert calls == []
    # '-' · '/' 로 시작하는 글도 옵션·명령이 아니라 글로 (표준 입력 · --system-prompt 의 값 자리)
    status, _ = await call(bot, cbody(prompt="--model opus", system="--dangerously-skip-permissions"), key)
    a = calls[0]["args"]
    assert status == 200 and calls[0]["stdin"] == "--model opus" and a[a.index("--system-prompt") + 1] == "--dangerously-skip-permissions"
    assert a[a.index("--model") + 1] == "haiku" and a.count("--model") == 1


async def test_run_cli_kills_on_timeout_and_runs_in_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import sys

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(ssalmuk_llm, "workdir", lambda: str(work))
    code, out, _ = await ssalmuk_llm.run_cli([sys.executable, "-c", "import os,sys;print(os.getcwd(), os.getpid()==os.getsid(0), sys.stdin.read())"],
                                             b"hi", 20, dict(os.environ))
    assert code == 0 and out.decode().split() == [str(work), "True", "hi"]
    pidfile = tmp_path / "pid"
    slow = f"import os,time;open({str(pidfile)!r},'w').write(str(os.getpid()));time.sleep(60)"
    with pytest.raises(TimeoutError):
        await ssalmuk_llm.run_cli([sys.executable, "-c", slow], b"", 1.5, dict(os.environ))
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):  # 시간 초과 뒤 프로세스가 남지 않음
        os.kill(pid, 0)


async def test_claude_json_mode(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    key = desk(bot).key()
    calls = fake_cli(monkeypatch, result='```json\n{"answer": "2"}\n```')
    status, out = await call(bot, cbody(json=True), key)
    assert status == 200 and json.loads(out["text"]) == {"answer": "2"}
    assert "JSON" in calls[0]["args"][calls[0]["args"].index("--system-prompt") + 1]
    fake_cli(monkeypatch, result="정답은 2번")
    status, out = await call(bot, cbody(json=True), key)
    assert status == 502 and out["code"] == "bad_json"


async def test_claude_errors(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    key = desk(bot).key()
    fake_cli(monkeypatch, raises=TimeoutError())
    status, out = await call(bot, cbody(), key)
    assert status == 502 and out["code"] == "claude_error"
    fake_cli(monkeypatch, result="rate limited", is_error=True)
    assert (await call(bot, cbody(), key))[1]["code"] == "claude_error"
    monkeypatch.setattr(commentary, "find_claude", lambda: None)
    status, out = await call(bot, cbody(), key)
    assert status == 503 and out["code"] == "no_cli"
    # 로그인이 풀리면 503 cli_login — 그 뒤 10분은 CLI 를 다시 부르지 않음 (로그인 반복 금지)
    calls = fake_cli(monkeypatch, result="Invalid API key · Please run /login", is_error=True)
    status, out = await call(bot, cbody(), key)
    assert status == 503 and out["code"] == "cli_login" and len(calls) == 1
    status, out = await call(bot, cbody(), key)
    assert status == 503 and out["code"] == "cli_login" and len(calls) == 1


async def test_claude_one_at_a_time(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    key = desk(bot).key()
    hold = asyncio.Event()
    calls = fake_cli(monkeypatch, hold=hold)
    monkeypatch.setattr(ssalmuk_llm, "BUSY_WAIT", 0.05)
    first = asyncio.create_task(call(bot, cbody(), key))
    await asyncio.sleep(0.01)
    status, out = await call(bot, cbody(), key)  # 앞 것이 도는 동안
    assert status == 429 and out["code"] == "busy" and len(calls) == 1
    hold.set()
    assert (await first)[0] == 200
    assert (await call(bot, cbody(), key))[0] == 200  # 끝나면 다시 됨
    assert desk(bot).llm_view()["claude"]["today"] == 2  # 못 돈 것은 세지 않음


# ---------------------------------------------------------------- 3) Gemini
async def test_gemini_no_key_is_503(bot: DealBot) -> None:
    seen = fake_gemini(bot, key=None)
    status, out = await call(bot, gbody(), desk(bot).key())
    assert status == 503 and out["code"] == "no_key" and seen == []
    assert desk(bot).llm_view()["gemini"]["today"] == 0


async def test_gemini_ok_key_in_header_only_and_body_as_is(bot: DealBot) -> None:
    seen = fake_gemini(bot)
    key = desk(bot).key()
    body = gbody()
    body["request"]["systemInstruction"] = {"parts": [{"text": "퀴즈"}]}
    body["request"]["generationConfig"] = {"temperature": 0, "maxOutputTokens": 99999}
    status, out = await call(bot, body, key)
    assert status == 200 and out["ok"] is True and out["model"] == "gemini-2.5-flash"
    assert out["response"]["candidates"][0]["content"]["parts"][0]["text"] == SECRET_A
    req = seen[0]
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert req.headers["x-goog-api-key"] == GKEY and GKEY not in str(req.url) and GKEY not in json.dumps(out)
    sent = json.loads(req.content)
    assert sent["contents"] == body["request"]["contents"] and sent["systemInstruction"] == body["request"]["systemInstruction"]
    assert "tools" not in sent and sent["generationConfig"] == {"temperature": 0, "maxOutputTokens": 8192}  # 상한만 손댐
    # provider 를 적어도 같음 · 상한 안이면 그대로
    b2 = gbody(provider="gemini")
    b2["request"]["generationConfig"] = {"maxOutputTokens": 512}
    assert (await call(bot, b2, key))[0] == 200 and json.loads(seen[1].content)["generationConfig"] == {"maxOutputTokens": 512}
    assert desk(bot).llm_view()["gemini"] == {"today": 2, "cap": 40}


async def test_gemini_key_from_blog_env_file(bot: DealBot, tmp_path: Path) -> None:
    seen = fake_gemini(bot, key=None)
    p = tmp_path / "blog.env"
    p.write_text("OTHER_SECRET=nope\nexport GEMINI_API_KEY='blog-gem-key'\nBLOGAUTO_VIDEO_GEMINI_KEY=not-this\n", encoding="utf-8")
    bot.settings.dashboard.ssalmuk_search_env_file = str(p)
    assert (await call(bot, gbody(), desk(bot).key()))[0] == 200
    assert seen[0].headers["x-goog-api-key"] == "blog-gem-key"


async def test_gemini_model_allowlist_and_bad_body(bot: DealBot) -> None:
    seen = fake_gemini(bot)
    key = desk(bot).key()
    for m in ("gemini-3.1-pro-preview", "gemini-2.5-flash:streamGenerateContent", "gemini-2.5-flash/../x", "claude-sonnet", "sonnet", "", None):
        status, out = await call(bot, gbody(model=m), key)
        assert status == 400 and out["code"] == "bad_model", m
    for req in ({}, {"contents": []}, {"contents": "x"}):
        status, out = await call(bot, {"provider": "gemini", "model": "gemini-2.5-flash", "request": req}, key)
        assert status == 400 and out["code"] == "bad_body"
    assert seen == []


async def test_gemini_errors_are_502(bot: DealBot) -> None:
    key = desk(bot).key()
    fake_gemini(bot, lambda req: httpx.Response(429, json={"error": {"message": f"quota for {SECRET_Q}"}}))
    status, out = await call(bot, gbody(), key)
    assert status == 502 and out["code"] == "gemini_error" and out["status"] == 429 and SECRET_Q not in json.dumps(out, ensure_ascii=False)

    def boom(req: Any) -> Any:
        raise httpx.ConnectTimeout("t")

    fake_gemini(bot, boom)
    status, out = await call(bot, gbody(), key)
    assert status == 502 and out == {"code": "gemini_error", "status": 0, "text": "Gemini 가 답하지 않아요"}


# ---------------------------------------------------------------- 4) 한도
async def test_daily_limits_and_state_view(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, seen = fake_cli(monkeypatch), fake_gemini(bot)
    d = desk(bot)
    key = d.key()
    assert d.llm_caps == {"gemini": 40, "claude": 150, "sonnet": 50}  # 설정 기본값
    now = [1_800_000_000.0]
    d.clock = lambda: now[0]
    codes = [(await call(bot, gbody(), key))[0] for _ in range(41)]
    assert codes[:40] == [200] * 40 and codes[40] == 429 and len(seen) == 40
    codes = [(await call(bot, cbody(model="sonnet"), key))[0] for _ in range(51)]
    assert codes[:50] == [200] * 50 and codes[50] == 429
    codes = [(await call(bot, cbody(), key, peer="198.51.100.8"))[0] for _ in range(101)]  # Haiku 는 남은 100 까지 (손님 1분 120번은 따로)
    assert codes[:100] == [200] * 100 and codes[100] == 429 and len(calls) == 150
    status, out = await call(bot, cbody(), key, peer="198.51.100.9")
    assert status == 429 and out["code"] == "limit"
    v = d.view()["llm_proxy"]
    assert v == {"gemini": {"today": 40, "cap": 40}, "claude": {"today": 150, "cap": 150, "sonnet": 50, "sonnet_cap": 50}}
    assert json.loads(bot.db.kv_get(ssalmuk.KV_LLM_DAY)).keys() == {"d", "gemini", "claude", "sonnet"}  # 숫자만
    now[0] += 86400  # 다음 날 (KST)
    assert d.view()["llm_proxy"]["claude"]["today"] == 0
    assert (await call(bot, cbody(model="sonnet"), key, peer="198.51.100.9"))[0] == 200
    assert (await call(bot, gbody(), key, peer="198.51.100.9"))[0] == 200


async def test_per_minute_limit_shared(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.dashboard.ssalmuk_search_env_file = str(tmp_path / "no-such.env")
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    try:
        assert b.settings.dashboard.ssalmuk_llm_per_minute == 10
        fake_cli(monkeypatch)
        fake_gemini(b)
        key = desk(b).key()
        codes = [(await call(b, cbody() if i % 2 else gbody(), key))[0] for i in range(11)]
        assert codes[:10] == [200] * 10 and codes[10] == 429
        assert (await call(b, cbody(model="opus"), key))[0] == 400  # 거절은 한도와 상관없이 그대로
    finally:
        b.db.close()


async def test_body_cap_32kb(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_cli(monkeypatch)
    key = desk(bot).key()
    status, out = await call(bot, cbody(prompt="가" * 12000), key)  # 36KB
    assert status == 413 and out["code"] == "too_big" and calls == []
    assert (await call(bot, cbody(prompt="가" * 9000), key))[0] == 200


# ---------------------------------------------------------------- 5) 개인정보 · 로그
@pytest.mark.parametrize("leak", ["010-1234-5678", "01012345678", "010 1234 5678", "011.123.4567", "+82 10-1234-5678", "+821012345678",
                                  "010 - 1234 - 5678", "010)1234-5678", "+82 (0)10 1234 5678", "０１０－１２３４－５６７８", "010-12\u200b34-5678",
                                  "hong@example.com", "a.b+c@mail.co.kr"])
async def test_pii_is_refused_everywhere_in_body(bot: DealBot, monkeypatch: pytest.MonkeyPatch, leak: str) -> None:
    calls, seen = fake_cli(monkeypatch), fake_gemini(bot)
    key = desk(bot).key()
    g = gbody()
    g["request"]["contents"][0]["parts"].append({"text": f"연락처 {leak}"})
    bodies = [cbody(prompt=f"문의 {leak} 로"), cbody(system=f"이름 홍길동 {leak}"),
              {"provider": "claude", "model": "haiku", "messages": [{"role": "user", "content": f"x {leak}"}]}, g]
    for b in bodies:
        status, out = await call(bot, b, key)
        assert status == 400 and out["code"] == "pii" and leak not in json.dumps(out, ensure_ascii=False)
    assert calls == [] and seen == [] and desk(bot).llm_view()["claude"]["today"] == 0


def test_pii_does_not_trip_on_plain_quiz_text() -> None:
    for ok in ("2026년 11월 15일 20101234 번째 손님", "정답은 3번 (1:23)", "가격 1,010,000원 · 0101 · 제품 010", "@ 기호와 example.com 주소",
               "https://docs.google.com/forms/d/e/1FAIpQLSc0101234/viewform", "점수 +82점 · +820원", "문의 02-123-4567 · 1588-1234",
               "인스타 팔로우 @event.official 후 응모", "20261115010123456789"):
        assert not ssalmuk_llm.has_pii({"a": [ok]}), ok
    assert ssalmuk_llm.has_pii({"a": [{"b": "x@y.kr"}]}) and ssalmuk_llm.has_pii({"010-1111-2222": 1})


async def test_no_prompt_answer_or_key_in_logs_or_kv(bot: DealBot, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    fake_cli(monkeypatch)
    fake_gemini(bot)
    key = desk(bot).key()
    with caplog.at_level(logging.DEBUG):
        assert (await call(bot, cbody(system="비밀 지시문 가나다"), key))[0] == 200
        assert (await call(bot, gbody(), key))[0] == 200
        fake_gemini(bot, lambda req: httpx.Response(500, text=SECRET_Q))
        assert (await call(bot, gbody(), key))[0] == 502
        fake_cli(monkeypatch, result=f"오류 {SECRET_Q}", is_error=True)
        assert (await call(bot, cbody(), key))[0] == 502
        assert (await call(bot, cbody(prompt="전화 010-9999-8888"), key))[0] == 400
    text = caplog.text
    assert "ssalmuk llm: claude model=haiku status=200" in text and "ssalmuk llm: gemini model=gemini-2.5-flash status=200" in text
    for secret in (SECRET_Q, SECRET_A, "비밀 지시문", GKEY, key, "010-9999-8888"):
        assert secret not in text, secret
    rows = bot.db._conn.execute("SELECT key, value FROM kv").fetchall()
    dump = json.dumps([tuple(r) for r in rows], ensure_ascii=False)
    for secret in (SECRET_Q, SECRET_A, "비밀 지시문", GKEY, "010-9999-8888"):
        assert secret not in dump, secret
