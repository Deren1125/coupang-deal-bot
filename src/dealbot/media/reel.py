"""점심 TOP3 릴스 영상 만들기 — 카드 그림(Pillow) 몇 장을 ffmpeg 로 이어 붙인 10~15초 세로 영상 (주인 결정 10/15).

- 1080×1920, H.264(yuv420p, 30fps, 2초마다 키프레임) + 소리 없는 AAC 트랙 (인스타 릴스 영상 규격 — 소리 칸이 없는 영상을
  거절하는 경우를 피하려고 무음 트랙을 넣는다), faststart (메타가 받아 가며 바로 읽게).
- 장면은 잘라 잇기(컷). 장면마다 길이를 정한다: 첫 장(가격이 내려간 폭) 짧게, 딜 장 3~4초, 마지막 장 2초.
- ffmpeg 는 서버 PATH 에서 찾는다. 없으면 None → 그날 릴스는 건너뛰고 설치 방법을 할 일로 한 번 알린다 (ig_scheduler).
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

FPS = 30
MIN_SECONDS = 10.0
MAX_SECONDS = 15.0
HOOK_SECONDS = 1.5  # 첫 장: 가격이 내려간 폭 (첫 1초 안에 보이게)
END_SECONDS = 2.0
ENCODE_TIMEOUT = 180


def find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def durations(n_deals: int) -> list[float]:
    """[첫 장, 딜 장 …, 마지막 장] 길이 (초). 딜 장은 3~4초, 전체는 10~15초 안."""
    if n_deals <= 0:
        return [HOOK_SECONDS, END_SECONDS]
    per = (MAX_SECONDS - HOOK_SECONDS - END_SECONDS) / n_deals
    per = max(3.0, min(4.0, per))
    total = HOOK_SECONDS + END_SECONDS + per * n_deals
    if total < MIN_SECONDS:  # 딜이 2개뿐이면 딜 장을 늘려 10초를 채움
        per = (MIN_SECONDS - HOOK_SECONDS - END_SECONDS) / n_deals
    elif total > MAX_SECONDS:  # 딜이 4개면 3초보다 조금 짧게 (15초 안)
        per = (MAX_SECONDS - HOOK_SECONDS - END_SECONDS) / n_deals
    per = math.floor(per * 100) / 100  # 반올림하면 15초를 살짝 넘을 수 있음
    return [HOOK_SECONDS, *([per] * n_deals), END_SECONDS]


def command(ffmpeg: str, frames: Sequence[tuple[Path, float]], out: Path, *, fps: int = FPS) -> list[str]:
    """ffmpeg 명령 (장면마다 그림 한 장을 그 길이만큼 + 무음 AAC). 장면은 잘라 잇는다."""
    total = sum(d for _, d in frames)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for path, seconds in frames:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.2f}", "-i", str(path)]
    cmd += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    n = len(frames)
    chain = "".join(f"[{i}:v]scale=1080:1920,setsar=1,fps={fps},format=yuv420p[v{i}];" for i in range(n))
    chain += "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd += [
        "-filter_complex", chain, "-map", "[v]", "-map", f"{n}:a",
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-g", str(fps * 2), "-keyint_min", str(fps * 2), "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-shortest", "-movflags", "+faststart", str(out),
    ]
    return cmd


def encode(ffmpeg: str, frames: Sequence[tuple[Path, float]], out: Path, *, timeout: float = ENCODE_TIMEOUT) -> Path:
    """그림 장면들을 MP4 로. 실패하면 RuntimeError (ffmpeg 오류 끝부분)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".part.mp4")
    cmd = command(ffmpeg, frames, tmp)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 실행 실패: {e}") from e
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg 실패 (code {r.returncode}): {(r.stderr or '').strip()[-300:]}")
    tmp.replace(out)
    log.info("reel encoded: %s (%.1fs, %d scenes)", out.name, sum(d for _, d in frames), len(frames))
    return out
