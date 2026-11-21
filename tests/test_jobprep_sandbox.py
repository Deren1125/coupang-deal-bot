"""취준 코드 돌리기 — 방식 B (rlimit · 시험 환경). 진짜 하위 프로세스 (네트워크 없음)."""

from __future__ import annotations

import asyncio

import pytest

from dealbot.dashboard.jobprep import sandbox as sb


@pytest.fixture
def box() -> sb.Sandbox:
    s = sb.Sandbox("rlimit")
    assert s.mode() == "rlimit" and not s.strong()
    return s


T = {"fn": "f", "cases": [{"args": [2], "expected": 4}, {"args": [3], "expected": 6}], "compare": "exact"}


async def test_right_and_wrong(box: sb.Sandbox) -> None:
    r = await box.run("def f(x):\n    return x * 2\n", T)
    assert (r["passed"], r["total"]) == (2, 2)
    r = await box.run("def f(x):\n    return x + 2\n", T)
    assert r["passed"] == 1 and r["cases"][1]["got"] == "5"
    r = await box.run("def g(x):\n    return 1\n", T)
    assert "함수 f" in r["error"]


async def test_infinite_loop_times_out(box: sb.Sandbox) -> None:
    r = await box.run("def f(x):\n    while True:\n        pass\n", {**T, "cases": T["cases"][:1]})
    assert r["passed"] == 0 and "시간 초과" in r["cases"][0]["err"]


async def test_memory_blowup(box: sb.Sandbox) -> None:
    r = await box.run("def f(x):\n    a = [0] * (10 ** 9)\n    return 1\n", {**T, "cases": T["cases"][:1]})
    assert r["passed"] == 0 and r["cases"][0]["err"] == "메모리 초과"


@pytest.mark.parametrize("code", [
    "import socket\ndef f(x):\n    return 1\n",
    "def f(x):\n    import subprocess\n    return 1\n",
    "def f(x):\n    return open('/etc/passwd').read()\n",
    "def f(x):\n    import os\n    os.system('true')\n    return 1\n",
    "def f(x):\n    open('/tmp/../home/x', 'w')\n    return 1\n",
    "def f(x):\n    import ctypes\n    return 1\n",
])
async def test_dangerous_things_blocked(box: sb.Sandbox, code: str) -> None:
    r = await box.run(code, {**T, "cases": [{"args": [1], "expected": 1}]})
    assert r["passed"] == 0
    assert "PermissionError" in (r["error"] + "".join(c["err"] for c in r["cases"]))


async def test_env_is_empty(box: sb.Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")
    code = "import os\ndef f(x):\n    return [k for k in os.environ if k not in ('LC_CTYPE',)]\n"  # LC_CTYPE 는 파이썬이 스스로 넣는 것 (PEP 538)
    r = await box.run(code, {"fn": "f", "cases": [{"args": [0], "expected": []}]})
    assert r["passed"] == 1, r


async def test_output_capped(box: sb.Sandbox) -> None:
    r = await box.run("print('a' * 100000)\ndef f(x):\n    return x\n", {"fn": "f", "cases": [{"args": [1], "expected": 1}]})
    assert r["passed"] == 1 and len(r["stdout"]) <= 2048
    with pytest.raises(sb.SandboxError):
        await box.run("#" * (sb.CODE_MAX + 1), T)


async def test_codecs_and_compare_modes(box: sb.Sandbox) -> None:
    ll = "def f(h):\n    prev = None\n    while h:\n        h.next, prev, h = prev, h, h.next\n    return prev\n"
    r = await box.run(ll, {"fn": "f", "codec": "linked_list", "cases": [{"args": [[1, 2, 3]], "expected": [3, 2, 1]}, {"args": [[]], "expected": []}]})
    assert r["passed"] == 2
    tree = "def f(t):\n    if t is None:\n        return None\n    t.left, t.right = f(t.right), f(t.left)\n    return t\n"
    r = await box.run(tree, {"fn": "f", "codec": "tree", "cases": [{"args": [[1, 2, 3, None, 4]], "expected": [1, 3, 2, None, None, 4]}]})
    assert r["passed"] == 1
    r = await box.run("def f(x):\n    return [3, 1, 2]\n", {"fn": "f", "compare": "unordered", "cases": [{"args": [0], "expected": [1, 2, 3]}]})
    assert r["passed"] == 1
    r = await box.run("def f(x):\n    return 0.1 + 0.2\n", {"fn": "f", "compare": "float", "cases": [{"args": [0], "expected": 0.3}]})
    assert r["passed"] == 1


def test_systemd_command_is_fixed_text() -> None:
    joined = " ".join(sb.SYSTEMD_ARGS)
    for p in ("DynamicUser=yes", "PrivateNetwork=yes", "ProtectHome=yes", "MemoryMax=256M", "RuntimeMaxSec=12", "NoNewPrivileges=yes"):
        assert p in joined
    assert sb.SYSTEMD_ARGS[:2] == ["sudo", "-n"] and "--uid" not in joined  # 사용자 코드는 동적 사용자로만


async def test_user_code_cannot_forge_results(box: sb.Sandbox) -> None:
    """기대값은 하위 프로세스로 안 넘어가고, 맞고 틀림은 부모가 정한다 — nonce 를 훔쳐 가짜 줄을 써도(지금·atexit) 통과 안 됨."""
    forge = (
        "import sys, json, atexit\n"
        "def f(x):\n"
        "    fr = sys._getframe(1)\n"
        "    while fr is not None and 'D' not in fr.f_globals: fr = fr.f_back\n"
        "    D = fr.f_globals['D']\n"
        "    assert 'expected' not in json.dumps(D['tests'])\n"
        "    fake = D['nonce'] + json.dumps({'cases': [{'ok': True, 'val': '999', 'err': ''}] * 2, 'stdout': '', 'error': ''})\n"
        "    sys.__stdout__.write(fake + '\\n'); sys.__stdout__.flush()\n"
        "    atexit.register(lambda: (sys.__stdout__.write(fake + '\\n'), sys.__stdout__.flush()))\n"
        "    return -1\n")
    r = await box.run(forge, T)
    assert r["passed"] == 0 and r["total"] == 2


def test_fails_closed_without_systemd(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(sb, "systemd_ok", lambda: calls.append(1) or False)
    for want in ("systemd", "auto"):
        s = sb.Sandbox(want)
        assert s.mode() == "unavailable" and not s.strong()
        assert s.mode() == "unavailable"
        with pytest.raises(sb.SandboxError) as e:
            asyncio.run(s.run("def f(x):\n    return x\n", T))
        assert e.value.status == 503 and e.value.code == "no_sandbox"
        n = len(calls)
        s._probed -= sb.PROBE_RETRY_S + 1  # 몇 분 뒤 다시 확인
        monkeypatch.setattr(sb, "systemd_ok", lambda: calls.append(1) or True)
        assert s.mode() == "systemd" and len(calls) == n + 1
        monkeypatch.setattr(sb, "systemd_ok", lambda: calls.append(1) or False)


def test_default_config_is_systemd() -> None:
    from dealbot.config import JobprepConfig

    assert JobprepConfig().sandbox_mode == "systemd"
