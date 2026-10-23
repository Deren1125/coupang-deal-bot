"""서버 점검 (주인: 터미널 명령을 붙여넣지 않아도 되게) — 연결 탭 '서버 점검' 단추의 딜봇 쪽 몫.

· 읽기만, 정해진 점검만. 주인이 넣는 값은 없다 (단추 하나) → 명령에 끼어들 자리가 없다. systemctl·journalctl·git 은 인자를
  코드에 박은 목록으로, shell 없이, 시간 제한을 두고 부른다.
· 블로그 프로그램(Blog-Auto)이 하는 점검(네이버 로그인·스레드 API·메모리·서비스·글 수·코드 버전)은 블로그 일 server_check 로 맡기고,
  이 모듈은 딜봇 쪽(스레드·인스타 연결과 최근 실패, 딜봇 서비스·오류 기록·코드 버전)을 만들어 한 시트로 합친다.
· 글은 모두 서버가 만든다 — 줄마다 ✅/⚠️/❌ + 짧은 설명 + 고치는 법. 토큰은 있는지·며칠 남았는지만 (값은 어디에도 없다).
· 2분에 한 번 (COOLDOWN_S)."""
from __future__ import annotations

import logging
import re
import subprocess
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dealbot.dashboard.model import safe_text
from dealbot.utils.timeutil import fmt_local, from_iso, utcnow

log = logging.getLogger(__name__)

OK, WARN, BAD = "✅", "⚠️", "❌"
UNKNOWN = "확인 못 함"
COOLDOWN_S = 120
CMD_TIMEOUT_S = 5
JOURNAL_TIMEOUT_S = 8
SERVICE = "dealbot"
EVENT_SHOW = 5
EVENT_MSG_MAX = 120
REPO_ROOT = Path(__file__).resolve().parents[3]
SNS = (("threads", "스레드"), ("instagram", "인스타그램"))

# 흔한 오류 → 짧은 한국어 이유 (위에서부터 처음 맞는 것)
REASONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rate.?limit|too many|\b429\b|\(#(?:4|17|32|613)\)|code[ =:]*(?:4|17|32|613)\b|limit reached|quota", re.I),
     "요청이 너무 많았어요 — 잠시 뒤 자동 재시도"),
    (re.compile(r"oauth|access.?token|token.*(?:expire|invalid|revoked)|session.*(?:expire|invalid)|invalid.*token|\(#(?:10|190|200|102)\)|"
                r"code[ =:]*(?:10|102|190|200)\b|permission|not authorized|unauthori[sz]ed|\b40[13]\b|authorize failed|scope", re.I),
     "로그인·권한이 풀렸어요 — 다시 연결 필요"),
    (re.compile(r"\(#24\)|does not exist|not available|container|media id|media.*not ready", re.I),
     "메타가 사진을 아직 처리하지 못했어요 — 자동으로 다시 해봐요"),
    (re.compile(r"time.?out|timed out|connection|temporar|unavailable|\b5\d\d\b|reset by peer|dns|ssl", re.I),
     "네트워크나 메타 쪽 일시 오류 — 자동 재시도"),
    (re.compile(r"not posted|queue failed", re.I), "올리지 못했어요 — 글 상태를 확인"),
)


def reason_of(message: str) -> str:
    for rx, why in REASONS:
        if rx.search(message or ""):
            return why
    return ""


def line(mark: str, name: str, what: str, fix: str = "") -> str:
    return f"{mark} {name} — {what}" + (f" → {fix}" if fix else "")


# ── 고정 명령 ────────────────────────────────────────────


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None


def unit_state(unit: str) -> str | None:
    r = _run(["systemctl", "is-active", unit], CMD_TIMEOUT_S)
    out = (r.stdout or "").strip().splitlines() if r is not None else []
    return out[0].strip() if out else None


def journal_errors(unit: str) -> int | None:
    r = _run(["journalctl", "-u", unit, "--since", "24 hours ago", "-p", "err", "--no-pager", "-q", "-o", "cat"], JOURNAL_TIMEOUT_S)
    if r is None or r.returncode != 0:
        return None
    err = (r.stderr or "").lower()
    if "not permitted" in err or "permission" in err or "no journal files" in err:
        return None
    return sum(1 for ln in (r.stdout or "").splitlines() if ln.strip() and not ln.startswith("-- "))


def code_version(repo: Path = REPO_ROOT) -> str | None:
    r = _run(["git", "-C", str(repo), "log", "-1", "--format=%h %cd", "--date=format-local:%m/%d %H:%M"], CMD_TIMEOUT_S)
    if r is None or r.returncode != 0:
        return None
    return (r.stdout or "").strip() or None


def meminfo(path: str = "/proc/meminfo") -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                k, _, v = ln.partition(":")
                num = v.strip().split(" ")[0]
                if num.isdigit():
                    out[k.strip()] = int(num) // 1024
    except OSError:
        return {}
    return out


# ── 스레드·인스타 ─────────────────────────────────────────


def _kv_names(name: str) -> dict[str, str]:
    if name == "threads":
        from dealbot.publisher import threads as m
    else:
        from dealbot.publisher import instagram as m
    return {"token": m.KV_TOKEN, "expires": m.KV_TOKEN_EXPIRES, "saved": m.KV_TOKEN_SAVED_AT, "user": m.KV_USERNAME}


def sns_connection(bot: Any, name: str, label: str, now: Any = None) -> str:
    """토큰이 있나 · 계정 · 만료일(남은 날) · 다시 연결한 때. 토큰 값은 읽지도 화면에 쓰지도 않는다 (있는지만)."""
    now = now or utcnow()
    tz = bot.settings.app.timezone
    cfg = getattr(bot.settings, name, None)
    title = f"{label} 연결"
    if cfg is not None and not getattr(cfg, "enabled", True):
        return line(OK, title, "꺼져 있어요 (설정)")
    k = _kv_names(name)
    if not bot.db.kv_get(k["token"]):
        return line(BAD, title, "토큰이 없어요", "연결 탭 '인스타 · 스레드'에서 다시 연결")
    user = bot.db.kv_get(k["user"])
    exp = from_iso(bot.db.kv_get(k["expires"]) or None)
    saved = from_iso(bot.db.kv_get(k["saved"]) or None)
    bits = [f"@{safe_text(user, 40)}" if user else "계정 이름 없음"]
    mark, fix = OK, ""
    if exp is None:
        bits.append("만료일 모름")
        mark = WARN
    else:
        days = (exp - now).days
        bits.append(f"만료 {fmt_local(exp, tz, '%m-%d')} (D-{days})" if days >= 0 else f"만료 {fmt_local(exp, tz, '%m-%d')} (지남)")
        if days < 0 or days <= 3:
            mark, fix = BAD, "다시 연결"
        elif days <= 10:
            mark, fix = WARN, "곧 만료 — 다시 연결"
    bits.append(f"연결한 때 {fmt_local(saved, tz)}" if saved else "연결한 때 기록 없음")
    return line(mark, title, " · ".join(bits), fix)


def sns_events(bot: Any, now: Any = None) -> list[str]:
    """스레드·인스타 WARNING/ERROR 최근 5건 (시간 · 짧은 이유 · 원문 120자). 다시 연결하기 전의 실패는 뺀다."""
    now = now or utcnow()
    tz = bot.settings.app.timezone
    labels = dict(SNS)
    saved = {}
    for name, _ in SNS:
        try:
            saved[name] = from_iso(bot.db.kv_get(_kv_names(name)["saved"]) or None)
        except Exception:  # noqa: BLE001
            saved[name] = None
    got = []
    for ev in bot.db.recent_events_of(tuple(labels), limit=40):
        ts = from_iso(str(ev.get("ts") or ""))
        s = saved.get(ev.get("kind"))
        if ts is not None and s is not None and ts < s:
            continue
        got.append((ev, ts))
        if len(got) >= EVENT_SHOW:
            break
    if not got:
        return [line(OK, "스레드·인스타 최근 실패", "없어요")]
    out = []
    for ev, ts in got:
        msg = safe_text(ev.get("message") or "", EVENT_MSG_MAX)
        why = reason_of(msg)
        mark = BAD if str(ev.get("level")).upper() == "ERROR" else WARN
        name = f"{labels.get(ev.get('kind'), ev.get('kind'))} {fmt_local(ts, tz) if ts else ''}".strip()
        out.append(f"{mark} {name} — {why + ' · ' if why else ''}{msg}")
    return out


# ── 딜봇 서비스·기록·버전 ───────────────────────────────────


def service_line(state: Callable[[str], str | None] = unit_state) -> str:
    st = state(SERVICE)
    name = f"서비스 {SERVICE}"
    if st is None:
        return line(WARN, name, UNKNOWN)
    if st == "active":
        return line(OK, name, "켜져 있어요")
    return line(BAD, name, f"꺼져 있어요 ({st})", f"서버에서 sudo systemctl restart {SERVICE}")


def journal_line(count: Callable[[str], int | None] = journal_errors) -> str:
    n = count(SERVICE)
    name = f"오류 기록 {SERVICE} (24시간)"
    if n is None:
        return line(WARN, name, UNKNOWN, "서버 계정에 journal 읽기 권한이 없을 수 있어요")
    if n == 0:
        return line(OK, name, "없어요")
    fix = f"서버에서 journalctl -u {SERVICE} -p err 로 내용 확인"
    return line(WARN if n < 20 else BAD, name, f"{n}건" if n < 20 else f"{n}건 — 많아요", fix)


def version_line(ver: str | None = None, running: str | None = None) -> str:
    ver = code_version() if ver is None else ver
    name = "코드 버전 딜봇"
    if not ver:
        return line(WARN, name, UNKNOWN)
    if running and running not in ("?", "") and not ver.startswith(running):
        return line(WARN, name, f"받은 코드 {ver} · 돌고 있는 코드 {running}", "새 코드를 받았지만 재시작 전이에요 — 서비스 재시작")
    return line(OK, name, ver)


def memory_line(info: dict[str, int] | None = None) -> str:
    """블로그 프로그램이 응답하지 않을 때만 (같은 서버라 딜봇이 대신 본다)."""
    info = meminfo() if info is None else info
    avail = info.get("MemAvailable")
    if avail is None:
        return line(WARN, "메모리", UNKNOWN)
    st, sf = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    what = f"여유 {avail}MB" + (f" · 스왑 {st - sf}/{st}MB 씀" if st else " · 스왑 없음")
    if avail < 150:
        return line(BAD, "메모리", what, "서버 재시작을 생각해 보세요")
    return line(WARN if avail < 300 or (st and (st - sf) * 2 > st) else OK, "메모리", what)


def local_lines(bot: Any, *, with_memory: bool = False) -> list[str]:
    """딜봇 쪽 점검 전체 — 한 칸이 터져도 줄 하나만 ⚠️ 로 바꾸고 계속."""
    from dealbot.monitoring.admin import CODE_SHA

    now = utcnow()
    parts: list[Callable[[], list[str]]] = [lambda n=n, lb=lb: [sns_connection(bot, n, lb, now)] for n, lb in SNS]
    parts += [lambda: sns_events(bot, now), lambda: [service_line()], lambda: [journal_line()],
              lambda: [version_line(running=CODE_SHA)]]
    if with_memory:
        parts.insert(0, lambda: [memory_line()])
    out: list[str] = []
    for fn in parts:
        try:
            out.extend(fn())
        except Exception as e:  # noqa: BLE001
            log.warning("server check part failed: %s", type(e).__name__)
            out.append(line(WARN, "점검 한 칸", f"{UNKNOWN} ({type(e).__name__})"))
    return [safe_text(x, 400) for x in out]


# ── 합치기 · 쿨다운 ──────────────────────────────────────


def blog_part(status: str, code: str, text: str) -> list[str]:
    """Blog-Auto 결과 → 줄들. 못 받았으면 그 이유 한 줄 (딜봇 쪽 줄은 그대로 보인다)."""
    if status == "ok" and text.strip():
        return [ln for ln in text.splitlines() if ln.strip()]
    if status == "expired":
        why = "블로그 프로그램이 일을 받지 않았어요"
    elif status == "unknown":
        why = "블로그 프로그램 소식이 끊겼어요"
    else:
        why = safe_text(text, 100) or "점검하지 못했어요"
    return [line(WARN if code == "busy" else BAD, "블로그 프로그램 점검", why, "" if code == "busy" else "서버에서 sudo systemctl restart blogauto")]


def compose(blog_lines: list[str], local: list[str]) -> str:
    return "\n".join([*blog_lines, *local])


def down_part(reason: str) -> list[str]:
    return [line(BAD, "블로그 프로그램", reason, "서버에서 sudo systemctl status blogauto 로 확인 · 필요하면 restart")]


class Cooldown:
    """서버 점검 2분에 한 번 (모든 기기 합쳐서)."""

    def __init__(self, clock: Callable[[], float] = time.time, seconds: int = COOLDOWN_S) -> None:
        self.clock, self.seconds, self.last = clock, seconds, 0.0

    def left(self) -> int:
        return max(0, int(self.last + self.seconds - self.clock() + 0.999))

    def hit(self) -> int:
        """0 이면 지금 해도 됨(시각을 적는다), 아니면 남은 초."""
        left = self.left()
        if not left:
            self.last = self.clock()
        return left

    @staticmethod
    def text(left: int) -> str:
        return f"방금 점검했어요 — {left}초 뒤에 다시 눌러 주세요"


class LocalCache:
    """일 id 마다 딜봇 쪽 줄을 한 번만 만든다 (결과를 여러 번 읽어도 명령을 다시 부르지 않게)."""

    def __init__(self, keep: int = 8) -> None:
        self.keep, self.rows = keep, OrderedDict()

    def get(self, aid: str, make: Callable[[], list[str]]) -> list[str]:
        if aid not in self.rows:
            self.rows[aid] = make()
            while len(self.rows) > self.keep:
                self.rows.popitem(last=False)
        return self.rows[aid]


CACHE = LocalCache()


def finish(bot: Any, st: dict[str, Any]) -> dict[str, Any]:
    """끝난 blog.server_check → 시트에 보일 모양 (늘 ok — 블로그 쪽이 실패해도 딜봇 쪽 줄은 보이게)."""
    local = CACHE.get(st["id"], lambda: local_lines(bot, with_memory=st["status"] != "ok"))
    text = compose(blog_part(st["status"], st["code"], st["text"]), local)
    return {**st, "status": "ok", "code": "done", "text": text}


__all__ = ["Cooldown", "compose", "finish", "local_lines", "reason_of"]
