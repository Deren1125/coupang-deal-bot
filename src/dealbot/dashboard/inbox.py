"""결재함 (주인 11/10 14:2x — "대시보드에 결재함을 새로 만들어서 실시간으로 직접 보내게").

주인 대리 중계 세션이 Blog-Auto 저장소의 owner-inbox 가지(배포가 일어나지 않는 가지)에 주인 몫 카드를 올리고,
주인이 대시보드 '결재함'에서 답을 고르면 딜봇 서버가 그 가지에 답 파일 하나를 바로 올린다. 형식은 그 가지 README.md.

- 받기: 서버의 Blog-Auto 저장소(~/Blog-Auto — 이미 server-status 를 올리는 저장소 · 같은 ~/.git-credentials)에 이 가지만 담는
  worktree 를 하나 두고(딜봇 데이터 폴더 owner-inbox/ · 가지에 이름을 붙이지 않은 detached) 1분마다 fetch → 원격 그대로
  (scheduler.dashboard_loop → tick). 못 올린 답 커밋이 남아 있으면 그때 rebase 해서 다시 올린다.
- 읽기: inbox/items.json · inbox/digest.json 만 (각 1MB 까지 · 모르는 필드 버림 · 토큰 모양 글자는 가림). 화면엔 세션 id 를 보내지 않는다.
- 보내기 (대시보드 일 inbox.send): answers/<ms>.json {at, answers:[{id, choice, choice_text, text}], done:[id]} 를 쓰자마자
  commit + push origin owner-inbox. 실패하면 fetch·rebase 해서 3번까지 · 강제 push 없음. 그래도 안 되면 커밋은 남겨 두고
  1분마다 다시 (화면엔 '아직 못 보냄'). 빈 답(선택도 글도 없음)은 넣지 않고, 다 비면 보내지 않는다.
- 저장소 위치: 환경 변수 DEALBOT_OWNER_INBOX_REPO, 없으면 dashboard.blogauto_metrics_path(…/Blog-Auto/var/metrics/blogauto.json)의 Blog-Auto.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dealbot.dashboard.model import safe_text

log = logging.getLogger(__name__)

BRANCH = "owner-inbox"
REMOTE_REF = f"refs/remotes/origin/{BRANCH}"
REPO_ENV = "DEALBOT_OWNER_INBOX_REPO"
EVERY = 60  # 1분
GIT_TIMEOUT = 60
PUSH_TRIES = 3
GIT_NAME, GIT_EMAIL = "dealbot-dashboard", "noreply@localhost"
FILE_MAX = 1024 * 1024
ITEMS_MAX, BG_MAX, OPT_MAX, TARGETS_MAX, SENT_SHOW = 100, 20, 12, 8, 20
ANSWERS_MAX, TEXT_MAX = 60, 4000
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
GROUPS = {"perp": "퍼프덱스", "blog": "블로그", "shorts": "숏폼", "common": "공통"}
KINDS = ("once", "decide", "info")  # 화면 순서 (README)
RISKS = ("돈", "계정", "공개", "일반")
DIGEST_KEYS = ("perp", "blog", "shorts")

Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str] | None"]


def _run(args: list[str], timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess[str] | None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"}
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout, check=False, shell=False, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _ok(r: subprocess.CompletedProcess[str] | None) -> bool:
    return r is not None and r.returncode == 0


def _why(r: subprocess.CompletedProcess[str] | None, what: str) -> str:
    if r is None:
        return f"{what} 시간 초과"
    err = (r.stderr or "").lower()
    if "403" in err or "denied" in err or "authentication" in err or "could not read username" in err:
        return f"{what} 권한 없음 (서버 GitHub 토큰 확인)"
    return f"{what} 실패"


# ── 글 거르기 ──────────────────────────────────────────────


def _line(v: Any, n: int) -> str:
    """한 줄 (토큰 모양 가림 · 보이지 않는 글자 뺌)."""
    if not isinstance(v, str):
        return ""
    return "".join(c for c in safe_text(v, n) if c.isprintable()).strip()


def _text(v: Any, n: int) -> str:
    """여러 줄 글 — 줄마다 _line."""
    if not isinstance(v, str):
        return ""
    return "\n".join(_line(x, n) for x in v.splitlines()[:40]).strip()[:n]


def _ms(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def clean_items(raw: Any) -> dict[str, Any]:
    """items.json → 화면 값 (open 카드 · sent '보낸 것'). closed 는 뺀다. 세션 id 는 보내지 않는다."""
    rows = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("no items")
    open_, sent, seen = [], [], set()
    for r in rows[:ITEMS_MAX]:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not ID_RE.match(r["id"]) or r["id"] in seen:
            continue
        seen.add(r["id"])
        st = r.get("status")
        if st not in ("open", "sent"):
            continue
        kind = r.get("kind") if r.get("kind") in KINDS else "info"
        x = {"id": r["id"], "group": r.get("group") if r.get("group") in GROUPS else "common", "kind": kind,
             "risk": r.get("risk") if r.get("risk") in RISKS else "일반",
             "title": _line(r.get("title"), 120), "question": _text(r.get("question"), 1000), "recommend": _text(r.get("recommend"), 600),
             "background": [s for s in (_text(b, 600) for b in (r.get("background") or [])[:BG_MAX] if isinstance(r.get("background"), list)) if s],
             "options": [s for s in (_line(o, 200) for o in (r.get("options") or [])[:OPT_MAX] if isinstance(r.get("options"), list)) if s],
             "copy": _text(r.get("copy"), 2000) if kind == "once" else "",
             "targets": [n for n in (_line(t.get("name"), 30) for t in (r.get("targets") or [])[:TARGETS_MAX]
                                     if isinstance(t, dict)) if n] if isinstance(r.get("targets"), list) else [],
             "created_at": _ms(r.get("created_at")), "sent_at": _ms(r.get("sent_at")), "sent_note": _line(r.get("sent_note"), 200)}
        x["glabel"] = GROUPS[x["group"]]
        (open_ if st == "open" else sent).append(x)
    open_.sort(key=lambda x: (KINDS.index(x["kind"]), x["created_at"] or 0))
    sent.sort(key=lambda x: -(x["sent_at"] or 0))
    return {"title": _line(raw.get("title"), 60), "updated_at": _ms(raw.get("updated_at")), "open": open_, "sent": sent[:SENT_SHOW]}


def clean_digest(raw: Any) -> dict[str, Any]:
    d = raw.get("digest") if isinstance(raw, dict) else None
    tabs = []
    for k in DIGEST_KEYS:
        v = d.get(k) if isinstance(d, dict) else None
        if not isinstance(v, dict):
            continue
        bullets = [{"t": _line(b.get("t"), 12), "who": _line(b.get("who"), 30), "text": _text(b.get("text"), 600)}
                   for b in (v.get("bullets") or [])[:40] if isinstance(b, dict)] if isinstance(v.get("bullets"), list) else []
        tabs.append({"key": k, "label": GROUPS[k], "period": _line(v.get("period"), 60), "bullets": [b for b in bullets if b["text"]]})
    srv = raw.get("server") if isinstance(raw, dict) else None
    lines = [s for s in (_line(x, 300) for x in (srv.get("lines") or [])[:30]) if s] if isinstance(srv, dict) and isinstance(srv.get("lines"), list) else []
    return {"tabs": tabs, "server": lines}


def clean_send(args: dict[str, Any]) -> dict[str, Any]:
    """대시보드 일 inbox.send 의 args → {answers, done}. 틀리면 ValueError (actions.clean_args 가 bad_args 로)."""
    ans, done = args.get("answers", []), args.get("done", [])
    if not isinstance(ans, list) or not isinstance(done, list) or len(ans) > ANSWERS_MAX or len(done) > ANSWERS_MAX:
        raise ValueError("bad")
    out, seen = [], set()
    for a in ans:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str) or not ID_RE.match(a["id"]) or a["id"] in seen:
            raise ValueError("bad")
        ch, tx = a.get("choice"), a.get("text", "")
        if ch is not None and (not isinstance(ch, int) or isinstance(ch, bool) or not 1 <= ch <= OPT_MAX):
            raise ValueError("bad")
        if not isinstance(tx, str) or len(tx) > TEXT_MAX or "\x00" in tx:
            raise ValueError("bad")
        tx = _text(tx, TEXT_MAX)
        if ch is None and not tx:
            continue  # 답 칸이 빈 카드는 넣지 않음 (README)
        seen.add(a["id"])
        out.append({"id": a["id"], "choice": ch, "text": tx})
    dn = []
    for i in done:
        if not isinstance(i, str) or not ID_RE.match(i):
            raise ValueError("bad")
        if i not in dn:
            dn.append(i)
    if not out and not dn:
        raise ValueError("empty")
    return {"answers": out, "done": dn}


def _read_json(p: Path) -> Any:
    if not p.is_file() or p.stat().st_size > FILE_MAX:
        raise ValueError("missing")
    return json.loads(p.read_text(encoding="utf-8"))


# ── git (worktree 하나 · 잠금 하나) ─────────────────────────


class Inbox:
    def __init__(self, repo: Path, wt: Path, run: Runner | None = None, clock: Callable[[], float] = time.time) -> None:
        self.repo, self.wt = Path(repo), Path(wt)
        self.run = run or _run
        self.clock = clock
        self.lock = threading.Lock()
        self.state: dict[str, Any] = {"state": "wait", "why": "", "at": None, "tried": 0.0, "pending": 0}

    def _g(self, *args: str, cwd: Path | None = None, timeout: float = GIT_TIMEOUT) -> subprocess.CompletedProcess[str] | None:
        return self.run(["-C", str(cwd or self.wt), *args], timeout)

    def _ensure(self) -> str:
        if (self.wt / ".git").exists():
            return ""
        if not (self.repo / ".git").exists():
            return "Blog-Auto 저장소를 못 찾음"
        r = self._g("fetch", "-q", "origin", f"+refs/heads/{BRANCH}:{REMOTE_REF}", cwd=self.repo)
        if not _ok(r):
            return _why(r, "결재함 가지 받기")
        self._g("worktree", "prune", cwd=self.repo, timeout=15)
        self.wt.parent.mkdir(parents=True, exist_ok=True)
        r = self._g("worktree", "add", "-q", "--detach", str(self.wt), REMOTE_REF, cwd=self.repo)
        return "" if _ok(r) else "결재함 폴더 만들기 실패"

    def _ahead(self) -> int:
        r = self._g("rev-list", "--count", f"{REMOTE_REF}..HEAD", timeout=10)
        try:
            return int((r.stdout or "0").strip()) if _ok(r) else 0
        except ValueError:
            return 0

    def _fetch(self) -> subprocess.CompletedProcess[str] | None:
        return self._g("fetch", "-q", "origin", f"+refs/heads/{BRANCH}:{REMOTE_REF}")

    def _push(self) -> str:
        """올리기 — 실패하면 fetch·rebase 뒤 다시 (PUSH_TRIES 번까지 · 강제 없음). '' = 됨."""
        why = ""
        for _ in range(PUSH_TRIES):
            r = self._g("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
            if _ok(r):
                return ""
            why = _why(r, "올리기")
            if not _ok(self._fetch()):
                continue
            if not _ok(self._g("rebase", "-q", REMOTE_REF)):
                self._g("rebase", "--abort", timeout=15)
                return "답 파일 합치기 실패"
        return why or "올리기 실패"

    def _mark(self, state: str, why: str = "") -> None:
        self.state.update({"state": state, "why": why, "pending": self._ahead() if (self.wt / ".git").exists() else 0})
        if state == "ok":
            self.state["at"] = self.clock()

    def sync(self) -> str:
        """1분마다: 원격 그대로 받기 (못 올린 답이 있으면 rebase 해서 다시 올림). 한 일 한 마디."""
        with self.lock:
            self.state["tried"] = self.clock()
            if (why := self._ensure()):
                self._mark("down", why)
                return why
            if not _ok(r := self._fetch()):
                self._mark("down", _why(r, "받기"))
                return self.state["why"]
            if self._ahead():
                if not _ok(self._g("rebase", "-q", REMOTE_REF)):
                    self._g("rebase", "--abort", timeout=15)
                    self._mark("down", "답 파일 합치기 실패")
                    return self.state["why"]
                if (why := self._push()):
                    self._mark("down", why)
                    return why
            elif not _ok(self._g("reset", "-q", "--hard", REMOTE_REF)):
                self._mark("down", "결재함 맞추기 실패")
                return self.state["why"]
            self._mark("ok")
            return "ok"

    def read(self) -> dict[str, Any]:
        items, digest, bad = {"title": "", "updated_at": None, "open": [], "sent": []}, {"tabs": [], "server": []}, []
        try:
            items = clean_items(_read_json(self.wt / "inbox" / "items.json"))
        except (ValueError, OSError, TypeError):
            bad.append("items")
        try:
            digest = clean_digest(_read_json(self.wt / "inbox" / "digest.json"))
        except (ValueError, OSError, TypeError):
            bad.append("digest")
        return {**items, "digest": digest, "bad": bad}

    def send(self, answers: list[dict[str, Any]], done: list[str]) -> tuple[str, str]:
        """('done'|'queued'|'failed', 주인에게 보일 한 줄). answers 는 clean_send 를 거친 것."""
        with self.lock:
            if (why := self._ensure()):
                return "failed", f"보내지 못했어요 — {why}"
            opts = {x["id"]: x["options"] for x in self.read()["open"]}
            now = int(self.clock() * 1000)
            ans_dir = self.wt / "answers"
            ans_dir.mkdir(exist_ok=True)
            while (ans_dir / f"{now}.json").exists():
                now += 1
            body = {"at": now, "answers": [{"id": a["id"], "choice": a["choice"],
                                            "choice_text": (opts.get(a["id"]) or [])[a["choice"] - 1] if a["choice"] and a["choice"] <= len(opts.get(a["id"]) or []) else None,
                                            "text": a["text"]} for a in answers], "done": list(done)}
            name = f"answers/{now}.json"
            try:
                (self.wt / name).write_text(json.dumps(body, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            except OSError:
                return "failed", "보내지 못했어요 — 답 파일 쓰기 실패"
            if not _ok(self._g("add", "--", name)):
                return "failed", "보내지 못했어요 — 답 파일 담기 실패"
            r = self._g("-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}", "-c", "commit.gpgsign=false",
                        "commit", "-q", "--no-verify", "-m", f"owner-inbox: answers {now} ({len(answers)} 답 · {len(done)} 했어요)")
            if not _ok(r):
                return "failed", "보내지 못했어요 — 답 파일 커밋 실패"
            why = self._push()
            self.state["pending"] = self._ahead()
            if why:
                log.warning("결재함 답 올리기 못 함: %s", why)
                return "queued", f"답은 저장했는데 아직 못 보냈어요 ({why}) — 1분마다 다시 보내요"
            n = len(answers) + len(done)
            return "done", f"{n}개 보냈어요 — 중계 세션이 바로 나눠 전해요"


def repo_of(bot: Any) -> Path:
    env = (os.environ.get(REPO_ENV) or "").strip()
    if env:
        return Path(env)
    return Path(bot.settings.dashboard.blogauto_metrics_path).resolve().parents[2]


def of(bot: Any) -> Inbox:
    ib = getattr(bot, "_owner_inbox", None)
    if ib is None:
        ib = Inbox(repo_of(bot), Path(bot.settings.data_dir) / "owner-inbox")
        bot._owner_inbox = ib
    return ib


async def tick(bot: Any, now: float | None = None) -> str | None:
    """1분마다 불림 (scheduler.dashboard_loop). EVERY 가 안 지났으면 바로 돌아옴."""
    import asyncio

    ib = of(bot)
    now = now or ib.clock()
    if now - float(ib.state.get("tried") or 0) < EVERY - 5:
        return None
    return await asyncio.to_thread(ib.sync)


def view(bot: Any) -> dict[str, Any]:
    """GET /dashboard/api/inbox 몸."""
    ib = of(bot)
    v = ib.read()
    st = ib.state
    err = None
    if st["state"] == "down":
        err = f"결재함을 새로 못 받았어요 ({st['why']})" + (" — 지난 목록이에요" if st.get("at") else "")
    elif st["state"] == "wait":
        err = "결재함을 받는 중이에요 (1분마다)"
    return {**v, "state": st["state"], "err": err, "pending": st.get("pending") or 0, "count": len(v["open"])}


def count(bot: Any) -> int:
    try:
        return len(of(bot).read()["open"])
    except Exception:  # noqa: BLE001
        return 0
