"""주인 11/18 신고 — X '내 주제로 글 쓰기'(#/sns/xr/w) 눌러도 초안이 안 나옴.

1) .env 의 CLAUDE_CODE_OAUTH_TOKEN 이 11/17 23:52 끊김(OAuth token revoked) → claude CLI 가 모두 인증 오류.
   cli_env: 인증 오류면 그 변수만 빼고 한 번 더 (서버 claude 로그인) · 되면 24시간 토큰 없이 · 둘 다 안 되면 '로그인 풀림'.
2) 화면: 연결이 끊겨도(폰 화면 꺼짐·느린 서버) '쓰는 중'을 보이며 서버 상태를 다시 보고, 초안이 생기면 바로 보여 줌.
   초안 목록(내 초안)은 글 쓰기 칸 바로 아래 (사진으로 글 쓰기 칸 위).
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

from dealbot import cli_env, llm_json
from dealbot.llm_json import LLMError

OK = json.dumps({"type": "result", "is_error": False, "result": "", "structured_output": {"a": 1}, "modelUsage": {"opus": {}}}).encode()
REVOKED = json.dumps({"type": "result", "is_error": True, "result": "Failed to authenticate. API Error: 401 OAuth token revoked"}).encode()


class Proc:
    def __init__(self, out: bytes, rc: int) -> None:
        self.out, self.returncode, self._rc = out, None, rc

    async def communicate(self, _inp: bytes) -> tuple[bytes, bytes]:
        self.returncode = self._rc
        return self.out, b""

    async def wait(self) -> int:
        return self._rc

    def kill(self) -> None:
        pass


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    monkeypatch.setattr(cli_env, "_drop", {"until": 0.0, "warned": ""})
    monkeypatch.setenv(cli_env.ENV_TOKEN, "dummy-not-a-real-token")
    calls: list[dict[str, str]] = []
    outs: list[tuple[bytes, int]] = []

    async def spawn(*a, **k):  # noqa: ANN002, ANN003
        calls.append(k["env"])
        out, rc = outs.pop(0)
        return Proc(out, rc)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return calls, outs


async def test_revoked_env_token_retries_with_server_login_and_remembers(spawned) -> None:  # noqa: ANN001
    calls, outs = spawned
    outs += [(REVOKED, 1), (OK, 0), (OK, 0)]
    asker = llm_json.JsonAsker(None, cli_bin="/bin/true", cli_model="opus")
    assert await asker.ask("s", "u", {"type": "object"}) == {"a": 1}
    assert cli_env.ENV_TOKEN in calls[0] and cli_env.ENV_TOKEN not in calls[1]
    assert cli_env.dropped()
    assert await asker.ask("s", "u", {"type": "object"}) == {"a": 1}  # 24시간 동안은 처음부터 토큰 없이 (한 번만 부름)
    assert len(calls) == 3 and cli_env.ENV_TOKEN not in calls[2]


async def test_both_fail_is_login_error_and_no_retry_without_token(spawned, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    calls, outs = spawned
    outs += [(REVOKED, 1), (REVOKED, 1)]
    with pytest.raises(LLMError, match="로그인"):
        await llm_json.JsonAsker(None, cli_bin="/bin/true", cli_model="opus").ask("s", "u", {"type": "object"})
    assert len(calls) == 2 and not cli_env.dropped()
    monkeypatch.delenv(cli_env.ENV_TOKEN)
    outs += [(REVOKED, 1)]
    with pytest.raises(LLMError, match="로그인"):
        await llm_json.JsonAsker(None, cli_bin="/bin/true", cli_model="opus").ask("s", "u", {"type": "object"})
    assert len(calls) == 3  # 넘긴 토큰이 없으면 다시 부르지 않음


async def test_auth_error_as_plain_text_is_login_not_unreadable(spawned) -> None:  # noqa: ANN001
    calls, outs = spawned
    outs += [(b"Invalid API key \xc2\xb7 Please run /login", 1), (b"Invalid API key", 1)]
    with pytest.raises(LLMError, match="로그인"):
        await llm_json.JsonAsker(None, cli_bin="/bin/true", cli_model="opus").ask("s", "u", {"type": "object"})
    assert len(calls) == 2


def test_auth_failed_ignores_successful_runs() -> None:
    assert cli_env.auth_failed((1, REVOKED, b""))
    assert not cli_env.auth_failed((0, OK, b""))
    assert not cli_env.auth_failed((1, json.dumps({"type": "result", "is_error": True, "result": "overloaded"}).encode(), b""))


async def test_other_cli_callers_use_the_fallback(spawned) -> None:  # noqa: ANN001
    from dealbot import summarize
    from dealbot.commentary import Commentator

    calls, outs = spawned
    outs += [(REVOKED, 1), (json.dumps({"type": "result", "is_error": False, "result": "요약"}).encode(), 0)]
    s = summarize.InfoSummarizer.__new__(summarize.InfoSummarizer)
    s.cli_bin, s.cli_model, s.cli_timeout, s.last_error, s._cli_down_until = "/bin/true", "sonnet", 10, None, 0.0
    text, why = await s._ask_cli("sys", "user")
    assert (text, why) == ("요약", None) and cli_env.ENV_TOKEN not in calls[1]
    c = Commentator.__new__(Commentator)
    c.bin, c.model, c.timeout = "/bin/true", "sonnet", 10
    outs += [(json.dumps({"type": "result", "is_error": False, "structured_output": {"comment": "x"}}).encode(), 0)]
    c._ok = lambda: None  # type: ignore[method-assign]
    assert await c._ask("u") == {"comment": "x"} and cli_env.ENV_TOKEN not in calls[2]  # 이미 기억 → 토큰 없이


# ---------------------------------------------------------------- 화면 (node)
from test_dash_xtoday import NODE, STATIC, needs_node  # noqa: E402
from test_dash_xtrend import SHIM, desk  # noqa: E402

LOST_TAIL = r"""
(async () => {
  const out = {};
  let phase = 'idle';
  const mine0 = state.mine;
  global.fetch = async (u, o) => {
    if (o && o.method === 'POST') { posts.push(JSON.parse(o.body)); phase = 'cut'; throw new Error('network'); }  // 폰 화면 꺼짐 — 서버는 계속 씀
    if (phase === 'idle') return {status: 200, json: async () => Object.assign({}, state)};
    if (phase === 'cut') return {status: 200, json: async () => Object.assign({}, state, {run: {topic: '수능 연기 논란', at: '08:10'}})};
    return {status: 200, json: async () => Object.assign({}, state, {run: null, mine: [{id: 'm999999999', at: '08:11', topic: '수능 연기 논란', long: false,
      drafts: [{text: '새로 온 초안', angle: '', warn: [], done: false, undo_s: 0}]}].concat(mine0 || [])})};
  };
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  const scr = () => main.children[0], f = () => byId(main, 'xrf'), topic = () => find(f(), n => n.attrs['aria-label'] === '주제');
  const t = topic(); t.value = '수능 연기 논란'; t.listeners.input({target: t});
  const grp = all(scr(), n => n.className === 'grp' || n.attrs.id === 'xrph').map(n => n.attrs.id || text(n));
  out.order = grp;
  await click(byText(f(), 'BUTTON', '초안 만들기')); await tick();
  out.during = text(f()); out.fail = !!byId(main, 'xrfail');
  phase = 'done';
  window.SBXTrend.load(); await tick();
  out.after = text(scr()) + all(scr(), n => n.tagName === 'TEXTAREA').map(n => n.value).join('|'); out.toast = text(toast); out.fail2 = !!byId(main, 'xrfail'); out.scrolled = scrolled.slice(-1)[0];
  out.btn = !!byText(f(), 'BUTTON', '초안 만들기');
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_node
def test_js_keeps_waiting_after_a_dropped_connection_and_shows_the_draft() -> None:
    d, _g, _a, _n, _t = desk()
    v = d.view()
    shim = SHIM.split("(async () => {")[0] + LOST_TAIL
    res = subprocess.run([NODE, "-e", shim, str(STATIC / "xtrend.js"), "x", json.dumps(v, ensure_ascii=False), "#/sns/xr/w"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["order"].index("xrmine") == out["order"].index("내 주제로 글 쓰기") + 1  # 내 초안 = 글 쓰기 칸 바로 아래
    assert "쓰는 중" in out["during"] and "화면을 닫아도 서버는 계속 써요" in out["during"] and out["fail"] is False
    assert "새로 온 초안" in out["after"] and "바로 아래 「내 초안」" in out["toast"] and out["fail2"] is False
    assert out["scrolled"] == "xrm-m999999999" and out["btn"] is True


LOST2_TAIL = r"""
(async () => {
  const out = {};
  global.fetch = async (u, o) => {
    if (o && o.method === 'POST') throw new Error('network');
    return {status: 200, json: async () => Object.assign({}, state, {run: null})};  // 서버에 안 닿음 — 쓰는 중도 초안도 없음
  };
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  const f = () => byId(main, 'xrf'), t = find(f(), n => n.attrs['aria-label'] === '주제');
  t.value = '내 주제 하나'; t.listeners.input({target: t});
  await click(byText(f(), 'BUTTON', '초안 만들기')); await tick();
  out.alert = text(byId(main, 'xrfail') || mk('x'));
  out.retry = !!find(byId(main, 'xrfail') || mk('x'), n => n.tagName === 'BUTTON' && text(n) === '다시 해 보기');
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@needs_node
def test_js_says_retry_when_the_request_never_reached_the_server() -> None:
    d, _g, _a, _n, _t = desk()
    shim = SHIM.split("(async () => {")[0] + LOST2_TAIL
    res = subprocess.run([NODE, "-e", shim, str(STATIC / "xtrend.js"), "x", json.dumps(d.view(), ensure_ascii=False), "#/sns/xr/w"],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert "초안이 오지 않았어요" in out["alert"] and out["retry"] is True
