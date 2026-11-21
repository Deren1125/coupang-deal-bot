"""⑤ 코드 돌리기 (격리). 사용자 글은 표준 입력 JSON 으로만 (명령줄에 안 들어감) · 답은 표준 출력 JSON 한 줄.

방식 A (sandbox_mode auto 이고 `sudo -n systemd-run --version` 이 되면): systemd-run 임시 단위 — DynamicUser(다른 사용자)·
PrivateNetwork(네트워크 없음)·ProtectHome(/home 안 보임 → .env 못 읽음)·ProtectSystem=strict·PrivateTmp·NoNewPrivileges·메모리 256MB·
CPU 60%·nice 19·작업 16개·12초. 특권 사용자로 사용자 코드를 돌리지 않는다 (DynamicUser).
방식 B (A 가 안 될 때): python3 -I -S -B 하위 프로세스 · env={} · 새 임시 폴더 · setsid·nice 19·RLIMIT(AS 256MB·CPU 5초·FSIZE 1MB·
NPROC·NOFILE 32) · runner 의 audit hook 이 socket·subprocess·os.system·ctypes·폴더 밖 open 을 막음. **완전한 격리가 아님** (화면에 '격리: 약함').
공통: 경우마다 2초(signal.alarm) · 전체 10초 넘으면 프로세스 묶음째 죽임 · 동시에 1개 · 코드 50KB · 출력 64KB.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import shutil
import signal
import tempfile
import time
from typing import Any

log = logging.getLogger(__name__)

CODE_MAX = 50 * 1024
OUT_MAX = 64 * 1024
TOTAL_S = 10.0
CASE_S = 2
PY = "/usr/bin/python3"
BOOT = "import sys,json;D=json.loads(sys.stdin.read());exec(compile(D['runner'],'<runner>','exec'),{'D':D,'__name__':'__runner__'})"
SYSTEMD_ARGS = ["sudo", "-n", "systemd-run", "--quiet", "--wait", "--pipe", "--collect", "-p", "DynamicUser=yes", "-p", "PrivateNetwork=yes",
                "-p", "ProtectHome=yes", "-p", "ProtectSystem=strict", "-p", "PrivateTmp=yes", "-p", "NoNewPrivileges=yes", "-p", "MemoryMax=256M",
                "-p", "MemorySwapMax=0", "-p", "CPUQuota=60%", "-p", "Nice=19", "-p", "TasksMax=16", "-p", "RuntimeMaxSec=12",
                PY, "-I", "-S", "-B", "-c", BOOT]

RUNNER = r'''
import sys, json, signal, io, os, math, collections, heapq, itertools, functools, bisect, re, string, random, typing, dataclasses, copy, operator
NONCE = D["nonce"]
OUT = sys.stdout
TMP = os.path.realpath(D.get("tmp") or "/tmp")
os.chdir(TMP)
LIBS = tuple(os.path.realpath(p) for p in sys.path if p) + (os.path.realpath(sys.base_prefix),)
BLOCK_MODS = {"socket", "_socket", "ssl", "subprocess", "_posixsubprocess", "multiprocessing", "ctypes", "_ctypes", "pty", "fcntl", "resource",
              "urllib.request", "http.client", "ftplib", "smtplib", "asyncio", "threading", "_thread", "signal", "shutil", "pathlib"}
def _hook(ev, args):
    if ev == "import" and args and args[0] in BLOCK_MODS:
        raise PermissionError("blocked import " + str(args[0]))
    if ev.startswith(("socket.", "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.kill", "os.killpg",
                      "ctypes.", "os.chdir", "os.chmod", "os.remove", "os.rename", "os.rmdir", "shutil.", "os.symlink", "os.link", "os.putenv")):
        raise PermissionError("blocked " + ev)
    if ev == "open" and args:
        p = args[0]
        if isinstance(p, int):
            return
        rp = os.path.realpath(os.fsdecode(p))
        mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
        if rp == TMP or rp.startswith(TMP + os.sep):
            return
        if not any(c in mode for c in "wax+") and any(rp.startswith(l + os.sep) for l in LIBS):
            return
        raise PermissionError("blocked open")
class ListNode:
    def __init__(self, val=0, next=None):
        self.val = val; self.next = next
    def __repr__(self):
        return "ListNode(" + repr(self.val) + ")"
class TreeNode:
    def __init__(self, val=0, left=None, right=None):
        self.val = val; self.left = left; self.right = right
    def __repr__(self):
        return "TreeNode(" + repr(self.val) + ")"
def to_list_node(xs):
    head = cur = None
    for x in xs or []:
        n = ListNode(x)
        if head is None: head = cur = n
        else: cur.next = n; cur = n
    return head
def from_list_node(n):
    out = []
    while n is not None and len(out) < 10000:
        out.append(n.val); n = n.next
    return out
def to_tree(xs):
    if not xs or xs[0] is None: return None
    root = TreeNode(xs[0]); q = collections.deque([root]); i = 1
    while q and i < len(xs):
        node = q.popleft()
        if i < len(xs) and xs[i] is not None:
            node.left = TreeNode(xs[i]); q.append(node.left)
        i += 1
        if i < len(xs) and xs[i] is not None:
            node.right = TreeNode(xs[i]); q.append(node.right)
        i += 1
    return root
def from_tree(root):
    out = []; q = collections.deque([root])
    while q and len(out) < 10000:
        n = q.popleft()
        if n is None: out.append(None); continue
        out.append(n.val); q.append(n.left); q.append(n.right)
    while out and out[-1] is None: out.pop()
    return out
def enc(codec, v):
    if codec == "linked_list" and isinstance(v, list): return to_list_node(v)
    if codec == "tree" and isinstance(v, list): return to_tree(v)
    return v
def dec(codec, v):
    if codec == "linked_list" and (v is None or isinstance(v, ListNode)): return from_list_node(v)
    if codec == "tree" and (v is None or isinstance(v, TreeNode)): return from_tree(v)
    if isinstance(v, tuple): return list(v)
    if isinstance(v, set): return sorted(v, key=repr)
    return v
def same(mode, got, exp):
    if mode == "unordered" and isinstance(got, list) and isinstance(exp, list):
        k = lambda x: json.dumps(x, sort_keys=True, default=repr)
        return sorted(map(k, got)) == sorted(map(k, exp))
    if mode == "float":
        try: return abs(float(got) - float(exp)) <= 1e-6 * max(1.0, abs(float(exp)))
        except Exception: return False
    if mode == "any_of" and isinstance(exp, list):
        return any(got == e for e in exp)
    return got == exp
class Timeout(Exception):
    pass
def _alarm(sig, frm):
    raise Timeout()
signal.signal(signal.SIGALRM, _alarm)
buf = io.StringIO()
class Cap(io.TextIOBase):
    def write(self, s):
        if buf.tell() < 2048: buf.write(s[:2048 - buf.tell()])
        return len(s)
T = D["tests"]
res = {"cases": [], "stdout": "", "error": ""}
sys.addaudithook(_hook)
sys.stdout = Cap(); sys.stderr = Cap()
g = {"__name__": "__main__", "ListNode": ListNode, "TreeNode": TreeNode, "__builtins__": __builtins__}
try:
    signal.alarm(%(case_s)d)
    exec(compile(D["code"], "<내 코드>", "exec"), g)
    signal.alarm(0)
    fn = g.get(T["fn"])
    if not callable(fn):
        res["error"] = "함수 " + T["fn"] + " 를 찾지 못했어요"
    else:
        codec = T.get("codec") or "none"; mode = T.get("compare") or "exact"
        for c in T["cases"]:
            args = [enc(codec, copy.deepcopy(a)) for a in (c.get("args") or [])]
            t0 = __import__("time").perf_counter(); ok = False; got = None; err = ""
            try:
                signal.alarm(%(case_s)d)
                got = dec(codec, fn(*args))
                signal.alarm(0)
                ok = same(mode, got, c.get("expected"))
            except Timeout:
                err = "시간 초과 (%(case_s)d초)"
            except MemoryError:
                signal.alarm(0); err = "메모리 초과"
            except RecursionError:
                signal.alarm(0); err = "재귀가 너무 깊어요"
            except BaseException as e:
                signal.alarm(0); err = type(e).__name__ + ": " + str(e)[:160]
            ms = int((__import__("time").perf_counter() - t0) * 1000)
            try: gs = json.dumps(got, default=repr)[:200]
            except Exception: gs = repr(got)[:200]
            res["cases"].append({"ok": ok, "got": gs, "ms": ms, "err": err})
except Timeout:
    res["error"] = "시간 초과"
except BaseException as e:
    signal.alarm(0)
    res["error"] = type(e).__name__ + ": " + str(e)[:300]
res["stdout"] = buf.getvalue()[:2048]
OUT.write(NONCE + json.dumps(res, ensure_ascii=False) + "\n"); OUT.flush()
''' % {"case_s": CASE_S}


class SandboxError(Exception):
    def __init__(self, status: int, code: str, text: str) -> None:
        super().__init__(code)
        self.status, self.code, self.text = status, code, text


def systemd_ok() -> bool:
    if not shutil.which("sudo") or not shutil.which("systemd-run"):
        return False
    try:
        import subprocess

        r = subprocess.run(["sudo", "-n", "systemd-run", "--version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _limits() -> None:  # 방식 B 하위 프로세스에서 (exec 전)
    import resource

    os.setsid()
    with contextlib.suppress(OSError):
        os.nice(19)
    for lim, v in ((resource.RLIMIT_AS, 256 * 1024 * 1024), (resource.RLIMIT_CPU, 5), (resource.RLIMIT_FSIZE, 1024 * 1024),
                   (resource.RLIMIT_NOFILE, 32), (resource.RLIMIT_CORE, 0)):
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(lim, (v, v))
    with contextlib.suppress(ValueError, OSError, AttributeError):
        n = 64
        resource.setrlimit(resource.RLIMIT_NPROC, (n, n))


class Sandbox:
    def __init__(self, mode: str = "auto", tmp_root: str | None = None) -> None:
        self.want = mode
        self._mode: str | None = None
        self.tmp_root = tmp_root
        self.lock = asyncio.Lock()

    def mode(self) -> str:
        if self._mode is None:
            if self.want == "off":
                self._mode = "off"
            elif self.want == "rlimit":
                self._mode = "rlimit"
            elif self.want in ("auto", "systemd"):
                self._mode = "systemd" if systemd_ok() else ("off" if self.want == "systemd" else "rlimit")
            else:
                self._mode = "rlimit"
        return self._mode

    def strong(self) -> bool:
        return self.mode() == "systemd"

    async def run(self, code: str, tests: dict[str, Any]) -> dict[str, Any]:
        """{cases:[{ok, got, ms, err}], stdout, error, passed, total, mode}."""
        if len(code.encode("utf-8")) > CODE_MAX:
            raise SandboxError(413, "too_big", "코드가 너무 길어요 (50KB)")
        mode = self.mode()
        if mode == "off":
            raise SandboxError(503, "off", "코드 돌리기가 꺼져 있어요")
        async with self.lock:
            return await self._run(mode, code, tests)

    async def _run(self, mode: str, code: str, tests: dict[str, Any]) -> dict[str, Any]:
        nonce = "@@" + secrets.token_hex(8) + "@@"
        tmp = tempfile.mkdtemp(prefix="jpsb-", dir=self.tmp_root if mode == "rlimit" else None)
        os.chmod(tmp, 0o700)
        payload = {"runner": RUNNER, "code": code, "tests": tests, "nonce": nonce, "tmp": "/tmp" if mode == "systemd" else tmp}
        data = json.dumps(payload).encode()
        t0 = time.monotonic()
        try:
            if mode == "systemd":
                proc = await asyncio.create_subprocess_exec(*SYSTEMD_ARGS, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            else:
                proc = await asyncio.create_subprocess_exec(PY, "-I", "-S", "-B", "-c", BOOT, stdin=asyncio.subprocess.PIPE,
                                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env={}, cwd=tmp,
                                                            preexec_fn=_limits)
            try:
                out, _ = await asyncio.wait_for(self._talk(proc, data), timeout=TOTAL_S)
            except asyncio.TimeoutError:
                self._kill(proc)
                await proc.wait()
                return {"cases": [], "stdout": "", "error": f"전체 시간 초과 ({TOTAL_S:g}초)", "passed": 0, "total": len(tests.get("cases") or []),
                        "mode": mode}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        text = out[:OUT_MAX].decode("utf-8", "replace")
        line = next((ln for ln in reversed(text.splitlines()) if ln.startswith(nonce)), "")
        try:
            res = json.loads(line[len(nonce):]) if line else None
        except ValueError:
            res = None
        if not isinstance(res, dict):
            res = {"cases": [], "stdout": "", "error": "코드가 끝나지 않았어요 (메모리·시간 상한일 수 있어요)"}
        res["passed"] = sum(1 for c in res.get("cases") or [] if c.get("ok"))
        res["total"] = len(tests.get("cases") or [])
        res["mode"] = mode
        res["ms"] = int((time.monotonic() - t0) * 1000)
        return res

    @staticmethod
    async def _talk(proc: Any, data: bytes) -> tuple[bytes, bytes]:
        out = bytearray()
        proc.stdin.write(data)
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await proc.stdin.drain()
        proc.stdin.close()
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            if len(out) < OUT_MAX:
                out += chunk[:OUT_MAX - len(out)]
        await proc.wait()
        return bytes(out), b""

    @staticmethod
    def _kill(proc: Any) -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
