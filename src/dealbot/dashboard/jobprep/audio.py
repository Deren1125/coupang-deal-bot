"""⑦⑧ 공용 소리 — ffmpeg 로 16kHz 모노 wav · 쉼 재기 (shorts-research sound/senior/meas.py 의 방식을 옮겨 적음 — 다른 저장소를 import 하지 않음).

무음 = silencedetect noise=-38dB d=0.08 · 앞뒤 무음은 뺌 · 안쪽 무음 중 0.25초 넘는 것 = 쉼 · 말하는 시간 = 길이 − 무음.
원본(자막): 낱말 시각의 빈틈(≥0.25초)으로 같은 꼴.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

PAUSE_MIN = 0.25
EDGE = 0.05
MAX_S = 20


def parse_silence(stderr: str) -> tuple[list[tuple[float, float | None]], float | None]:
    st = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr or "")]
    en = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", stderr or "")]
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", stderr or "")
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    return list(zip(st, en + [None] * (len(st) - len(en)), strict=False)), dur


def measure_from(sil: list[tuple[float, float | None]], dur: float, words: int | None = None) -> dict[str, Any]:
    ss = [(max(0.0, a), b if b is not None else dur) for a, b in sil]
    lead = ss[0][1] if ss and ss[0][0] < EDGE else 0.0
    tail = dur - ss[-1][0] if ss and ss[-1][1] >= dur - EDGE else 0.0
    silent = sum(b - a for a, b in ss)
    speech = max(0.0, dur - silent)
    inner = [(a, b - a) for a, b in ss if a >= EDGE and b < dur - EDGE]
    pauses = [{"at": round(a - lead, 2), "len": round(n, 2)} for a, n in inner if n >= PAUSE_MIN]
    out: dict[str, Any] = {"dur": round(dur, 2), "speech_s": round(speech, 2), "talk_span": round(max(0.0, dur - lead - tail), 2), "pauses": pauses}
    if words:
        out["wps"] = round(words / speech, 2) if speech > 0 else 0.0
    return out


async def _run(*args: str, timeout: float = 60) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("nice", "-n", "19", *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _o, e = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, ""
    return proc.returncode or 0, e.decode("utf-8", "replace")


async def to_wav(src: Path, dst: Path, *, max_s: int = MAX_S) -> bool:
    code, _e = await _run("ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", str(src), "-t", str(max_s), "-ac", "1", "-ar", "16000",
                          "-sample_fmt", "s16", str(dst))
    return code == 0 and dst.exists() and dst.stat().st_size > 1000


async def measure(wav: Path, words: int | None = None) -> dict[str, Any] | None:
    code, e = await _run("ffmpeg", "-nostdin", "-hide_banner", "-i", str(wav), "-af", "silencedetect=noise=-38dB:d=0.08", "-f", "null", "-")
    if code != 0:
        return None
    sil, dur = parse_silence(e)
    if not dur:
        return None
    return measure_from(sil, dur, words)


def json3_words(data: Any) -> list[dict[str, Any]]:
    """유튜브 자막 json3 → [{w, t(초)}] (낱말별 시각 — tStartMs + tOffsetMs)."""
    out = []
    for ev in (data or {}).get("events") or []:
        if not isinstance(ev, dict):
            continue
        t0 = float(ev.get("tStartMs") or 0)
        for s in ev.get("segs") or []:
            w = str((s or {}).get("utf8") or "").strip()
            if w and w != "\n":
                out.append({"w": w, "t": round((t0 + float(s.get("tOffsetMs") or 0)) / 1000, 3)})
    return out


def word_pauses(words: list[dict[str, Any]], start: float, end: float) -> dict[str, Any]:
    """자막 낱말 시각으로 [start, end] 구간의 말 빠르기·쉼 (낱말 하나 ≈ 다음 낱말까지, 빈틈 ≥ 0.25초 = 쉼 — 낱말 길이를 0.35초로 잡음)."""
    ws = [w for w in words if start <= w["t"] <= end]
    pauses = []
    for a, b in zip(ws, ws[1:], strict=False):
        gap = b["t"] - a["t"] - 0.35
        if gap >= PAUSE_MIN:
            pauses.append({"at": round(a["t"] - start, 2), "len": round(gap, 2), "before": b["w"]})
    span = (ws[-1]["t"] - ws[0]["t"] + 0.35) if ws else 0.0
    speech = max(0.01, span - sum(p["len"] for p in pauses))
    return {"dur": round(span, 2), "speech_s": round(speech, 2), "pauses": pauses, "wps": round(len(ws) / speech, 2) if ws else 0.0, "words": len(ws)}


def pause_before(words: list[dict[str, Any]], twist_first: str, start: float) -> float:
    """twist 첫 낱말 앞 빈틈 (초)."""
    tw = re.sub(r"[^a-z']", "", (twist_first or "").lower())
    prev = None
    for w in words:
        if w["t"] < start:
            prev = w
            continue
        if prev is not None and re.sub(r"[^a-z']", "", w["w"].lower()) == tw:
            return round(max(0.0, w["t"] - prev["t"] - 0.35), 2)
        prev = w
    return 0.0


def compare(mine: dict[str, Any], ref: dict[str, Any], twist_pause_ref: float | None = None, twist_pause_mine: float | None = None) -> dict[str, Any]:
    r = {"wps_ratio": round((mine.get("wps") or 0) / ref["wps"], 2) if ref.get("wps") else None,
         "pauses_mine": len(mine.get("pauses") or []), "pauses_ref": len(ref.get("pauses") or [])}
    if twist_pause_ref is not None and twist_pause_mine is not None:
        r["twist_pause_diff"] = round(twist_pause_mine - twist_pause_ref, 2)
    return r
