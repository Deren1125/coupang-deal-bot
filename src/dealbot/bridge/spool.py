"""파일 다리 (주인 10/18). Blog-Auto blogauto/spool.py 와 같은 규칙 — 두 저장소의 tests/fixtures/dash_contract 로 맞춘다.

모두 딜봇 데이터 폴더(DEALBOT_DATA_DIR, 보통 ~/dealbot-data) 아래:
    dash_actions/{tmp,new,claimed,results,expired}/   대시보드 → Blog-Auto 할 일 (딜봇이 new/ 에 쓰고, Blog-Auto 가 claimed/ 로 맡고 results/ 에 결과)
    owner_notify/{tmp,new,expired}/                   Blog-Auto → 딜봇: 주인 알림 (딜봇 푸시가 보냄)
    kakao_out/{tmp,new,claimed,expired}/              딜봇 → Blog-Auto: 카톡으로 보내 달라는 것 (카톡 토큰은 Blog-Auto 에만)
쓰기는 tmp/ 에 다 쓰고(fsync) os.replace 로 한 번에 — 읽는 쪽은 반쪽 파일을 보지 않는다. 맡기는 os.rename 하나 (한쪽만 이긴다).
이름은 ^[a-z]_[0-9a-z]{26}$ (앞 글자 + ULID — 이름순 = 시간순), 크기는 64KB 까지 — 어긋나면 expired/ 로 옮기고 기록만.
폴더는 0700, 파일은 0600 (두 프로그램은 같은 사용자로 돈다)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[a-z]_[0-9a-z]{26}$")
MAX_BYTES = 64 * 1024
_B32 = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32 (소문자) — 모두 [0-9a-z]
DASH_ACTIONS, OWNER_NOTIFY, KAKAO_OUT = "dash_actions", "owner_notify", "kakao_out"
_lock = threading.Lock()
_last: tuple[int, int] | None = None
SUBDIRS = {DASH_ACTIONS: ("tmp", "new", "claimed", "results", "expired"),
           OWNER_NOTIFY: ("tmp", "new", "expired"),
           KAKAO_OUT: ("tmp", "new", "claimed", "expired")}


def new_id(prefix: str, now: float | None = None) -> str:
    """'a_' + ULID 26자 (앞 10자 = 밀리초 시각, 뒤 16자 = 무작위) — 이름순 = 시간순."""
    if not re.fullmatch(r"[a-z]", prefix):
        raise ValueError("prefix 는 영문 소문자 한 글자")
    global _last
    ms = int((time.time() if now is None else now) * 1000)
    with _lock:
        if _last is not None and ms <= _last[0]:  # 같은 밀리초(또는 시계가 뒤로) → 앞 이름 + 1 (쓴 순서 = 이름순, ULID monotonic)
            ms, rnd = _last[0], (_last[1] + 1) % (1 << 80)
        else:
            rnd = secrets.randbits(79)  # 다음 +1 이 넘치지 않게 위쪽 한 비트는 비움
        _last = (ms, rnd)
    head = "".join(_B32[(ms >> (5 * i)) & 31] for i in range(9, -1, -1))
    tail = "".join(_B32[(rnd >> (5 * i)) & 31] for i in range(15, -1, -1))
    return f"{prefix}_{head}{tail}"


def root(base: Path | str, name: str) -> Path:
    """한 다리의 폴더 (없으면 만든다 — 먼저 시작한 쪽이). 0700."""
    r = Path(base) / name
    for sub in SUBDIRS.get(name, ("tmp", "new")):
        d = r / sub
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(d, 0o700)
    with contextlib.suppress(OSError):
        os.chmod(r, 0o700)
    return r


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def write(r: Path, name: str, obj: Any, sub: str = "new") -> Path:
    """r/tmp 에 다 쓰고 fsync → r/<sub>/<name>.json 으로 한 번에 바꿔치기 (0600). 64KB 넘으면 쓰지 않는다 (ValueError)."""
    if not NAME_RE.match(name):
        raise ValueError("bad spool name")
    data = dumps(obj).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("spool file too big")
    (r / "tmp").mkdir(parents=True, exist_ok=True)
    (r / sub).mkdir(parents=True, exist_ok=True)
    tmp = r / "tmp" / f"{name}.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        target = r / sub / f"{name}.json"
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return target


def expire(r: Path, path: Path, why: str = "") -> None:
    """못 쓰는 파일은 expired/ 로 (지우지 않고 — 나중에 볼 수 있게). 기록만 (파일 내용은 남기지 않음)."""
    (r / "expired").mkdir(parents=True, exist_ok=True)
    try:
        os.replace(path, r / "expired" / path.name)
        log.warning("다리 파일 %s → expired (%s)", path.name[:40], why)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("다리 파일 %s 옮기기 실패: %s", path.name[:40], e)


def names(r: Path, sub: str = "new") -> list[str]:
    """r/<sub> 의 쓸 수 있는 파일 이름들 (이름순 = 시간순). 이름·크기가 어긋난 파일은 expired/ 로."""
    d = r / sub
    try:
        files = sorted(d.iterdir())
    except FileNotFoundError:
        return []
    out = []
    for p in files:
        if p.name.startswith("."):
            continue
        if p.suffix != ".json" or not NAME_RE.match(p.stem):
            expire(r, p, "bad name")
            continue
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            continue
        if p.is_symlink() or not p.is_file() or st.st_size > MAX_BYTES:
            expire(r, p, "too big" if st.st_size > MAX_BYTES else "not a file")
            continue
        out.append(p.stem)
    return out


def claim(r: Path, name: str, src: str = "new", dst: str = "claimed") -> Path | None:
    """맡기: new/<name>.json → claimed/<name>.json (os.rename 은 한쪽만 이긴다). 진 쪽(ENOENT)은 None."""
    (r / dst).mkdir(parents=True, exist_ok=True)
    target = r / dst / f"{name}.json"
    try:
        os.rename(r / src / f"{name}.json", target)
    except FileNotFoundError:
        return None
    return target


def read(path: Path) -> dict[str, Any] | None:
    """파일 하나 (64KB 넘거나 JSON 객체가 아니면 None)."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)
