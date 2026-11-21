"""취준 테스트가 같이 쓰는 것 — 가짜 http(네트워크 없음) · 가짜 시계 · 임시 data_dir 의 Jobprep."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from dealbot.config import JobprepConfig

T0 = 1_795_000_000.0  # 2026-11-18 (수) 경, KST 기준 시험 시계


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeResp:
    def __init__(self, status: int, body: Any = b"", headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = headers or {}
        self.text = self._body.decode("utf-8", "replace")
        self.content = self._body

    def json(self) -> Any:
        return json.loads(self._body)

    async def aiter_bytes(self) -> Any:
        for i in range(0, len(self._body), 4096):
            yield self._body[i:i + 4096]


class FakeHttp:
    """routes: url → (status, body, headers) 또는 함수(url, headers) → 같은 꼴. 부른 것은 calls 에."""

    def __init__(self) -> None:
        self.routes: dict[str, Any] = {}
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.gemini: list[Any] = []  # Gemini 답 (차례로)

    def _hit(self, url: str, headers: dict[str, str]) -> FakeResp:
        r = self.routes.get(url)
        if callable(r):
            r = r(url, headers)
        if r is None:
            return FakeResp(404, b"")
        status, body, *rest = r
        return FakeResp(status, body, rest[0] if rest else {})

    @contextlib.asynccontextmanager
    async def stream(self, method: str, url: str, headers: dict[str, str] | None = None, **kw: Any) -> Any:
        self.calls.append((method, url, dict(headers or {}), None))
        yield self._hit(url, headers or {})

    async def get(self, url: str, headers: dict[str, str] | None = None, **kw: Any) -> FakeResp:
        self.calls.append(("GET", url, dict(headers or {}), None))
        return self._hit(url, headers or {})

    async def post(self, url: str, json: Any = None, headers: dict[str, str] | None = None, **kw: Any) -> FakeResp:  # noqa: A002
        self.calls.append(("POST", url, dict(headers or {}), json))
        if "generativelanguage.googleapis.com" in url:
            ans = self.gemini.pop(0) if self.gemini else {"ok": True}
            if isinstance(ans, FakeResp):
                return ans
            text = ans if isinstance(ans, str) else __import__("json").dumps(ans, ensure_ascii=False)
            return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}}],
                                  "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200}})
        return self._hit(url, headers or {})


def make_jp(tmp_path: Path, monkeypatch: Any = None, **cfg_kw: Any) -> Any:
    from dealbot.dashboard.jobprep import Jobprep

    if monkeypatch is not None:
        monkeypatch.delenv("JOBPREP_DATA_DIR", raising=False)
    cfg_kw.setdefault("sandbox_mode", "rlimit")
    cfg_kw.setdefault("fetch_gap_s", 0.0)
    c = JobprepConfig(data_dir=str(tmp_path / "jpdata"), **cfg_kw)
    http = FakeHttp()
    clock = Clock()
    jp = Jobprep(lambda: c, http=http, ua="dealbot-test/1", clock=clock)
    jp.fake_http, jp.fake_clock, jp.conf = http, clock, c
    return jp


def write_ctci(jp: Any, rows: list[dict[str, Any]], texts: dict[str, dict[str, Any]] | None = None) -> None:
    d = jp.store.root / "ctci"
    (d / "text").mkdir(parents=True, exist_ok=True)
    (d / "index.json").write_text(json.dumps(rows), encoding="utf-8")
    for qid, t in (texts or {}).items():
        (d / "text" / f"{qid}.json").write_text(json.dumps(t), encoding="utf-8")


def ctci_rows(n_per_ch: dict[int, int]) -> list[dict[str, Any]]:
    from dealbot.dashboard.jobprep import ctci

    out = []
    for ch, n in n_per_ch.items():
        for i in range(1, n + 1):
            out.append({"qid": f"{ch}.{i}", "ch": ch, "ch_title": ctci.CH_TITLES[ch], "title": f"Q{ch}.{i}", "q_part": 1, "q_page": 1,
                        "sol_part": 1, "sol_page": 2, "sol_end": 3, "kind": "code" if ch in ctci.CODE_CH else "explain"})
    return out
