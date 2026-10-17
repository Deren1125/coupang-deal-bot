"""Blog-Auto → 딜봇 주인 알림 받기 (owner_notify/new/<n_…>.json, 2초마다). 모양 (spec §3.3):

    {"v":1,"id":"n_…","at":…,"src":"blogauto","op":"open","key":"draft:issue:31","kind":"draft_ready","blog":"issue",
     "iid":"post-issue-31","urgent":true,"quiet":false,"sev":"todo","title":"≤40","body":"≤120","short":"≤48",
     "ttl_h":168,"group":"","wait":null|[초, 개수],"delay_s":300}
    {"v":1,"id":"n_…","at":…,"src":"blogauto","op":"resolve","keys":["draft:issue:31"],"prefix":false}

- 파일은 id 순서(=시간 순서)로 적용하고, DB 에 넣은 뒤 지운다. 같은 id 는 한 번만 (7일 기억)
- 걸러서 받는다: 이름·키·iid 모양, 글 길이 (넘으면 자름), 토큰처럼 보이는 글자는 가림. src 는 blogauto 만
  (파일로 딜봇 줄을 닫거나 열 수 없게 — 해결은 같은 src 안에서만)
- 이상한 파일은 expired/ 로 옮기고 기록만
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from dealbot.bridge import spool
from dealbot.dashboard.model import safe_text
from dealbot.notify import iid as iidmod
from dealbot.notify.router import SEVS, NotifyRouter

log = logging.getLogger(__name__)

# 알림 키: Blog-Auto 의 진짜 키에는 한글·빈칸·'+' 가 들어간다 ('hot:26fw+안효섭+준지:', 'err:main:스캔:TimeoutError').
# 제어 글자·줄바꿈만 막고 120자까지 (키는 DB 에 매개변수로만 들어가고 화면에는 보이지 않는다)
KEY_RE = re.compile(r"^[^\s\x00-\x1f\x7f][^\x00-\x1f\x7f]{0,119}\Z")
KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
BLOGS = ("finance", "issue", "hotdeal", "")
TTL_MAX_H = 24 * 30
MAX_PER_TICK = 200


def _text(v: Any, limit: int) -> str:
    if not isinstance(v, str):
        return ""
    t = safe_text(v[:2000], 600)  # 한 줄로 · 토큰처럼 보이는 글자는 가림 (자르기 전에)
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _num(v: Any, lo: float, hi: float) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v) if lo <= v <= hi else None


def clean_open(raw: dict[str, Any]) -> dict[str, Any] | None:
    """열림 알림 하나 → 걸러 둔 모양 (못 쓰면 None)."""
    key, kind, iid, blog = raw.get("key"), raw.get("kind") or "", raw.get("iid"), raw.get("blog") or ""
    if not isinstance(key, str) or not KEY_RE.match(key) or not isinstance(kind, str) or not (kind == "" or KIND_RE.match(kind)):
        return None
    if not isinstance(iid, str) or not iidmod.ok(iid) or blog not in BLOGS:
        return None
    sev = raw.get("sev") if raw.get("sev") in SEVS else "todo"
    title = _text(raw.get("title"), 40) or _text(raw.get("short"), 40)
    if not title:
        return None
    wait = raw.get("wait")
    wait_s = wait_n = None
    if isinstance(wait, list) and len(wait) == 2:
        wait_s, n = _num(wait[0], 1, 7 * 86400), _num(wait[1], 1, 100)
        wait_n = int(n) if n is not None else None
        if wait_s is None or wait_n is None:
            wait_s = wait_n = None
    group = raw.get("group") if isinstance(raw.get("group"), str) and len(raw.get("group")) <= 40 else ""
    return {
        "src": "blogauto", "key": key, "kind": kind, "blog": blog, "iid": iid, "sev": sev,
        "urgent": raw.get("urgent") if isinstance(raw.get("urgent"), bool) else None,
        "quiet": raw.get("quiet") is True, "title": title, "body": _text(raw.get("body"), 120),
        "short": _text(raw.get("short"), 48) or title[:48], "ttl_h": _num(raw.get("ttl_h"), 0.05, TTL_MAX_H) or 24,
        "group": group if re.fullmatch(r"[a-z0-9_:\-]*", group or "") else "", "wait_s": wait_s, "wait_n": wait_n,
        "delay_s": _num(raw.get("delay_s"), 0, 86400) or 0, "at": _num(raw.get("at"), 0, 1e11) or 0,
    }


def clean_keys(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [k for k in raw[:50] if isinstance(k, str) and KEY_RE.match(k)]


def ingest(data_dir: Path | str, router: NotifyRouter, *, limit: int = MAX_PER_TICK) -> dict[str, int]:
    """owner_notify/new 의 파일을 적용. {'open': n, 'resolve': n, 'bad': n, 'dup': n}."""
    r = spool.root(data_dir, spool.OWNER_NOTIFY)
    out = {"open": 0, "resolve": 0, "bad": 0, "dup": 0, "stale": 0}
    now = router.clock()
    for name in spool.names(r, "new")[:limit]:
        path = r / "new" / f"{name}.json"
        data = spool.read(path)
        if data is None or data.get("v") != 1 or data.get("id") != name or not name.startswith("n_"):
            spool.expire(r, path, "bad notice")
            out["bad"] += 1
            continue
        if data.get("src") != "blogauto":
            spool.expire(r, path, "bad src")
            out["bad"] += 1
            continue
        if router.store.seen(name):
            spool.remove(path)
            out["dup"] += 1
            continue
        op = data.get("op")
        if op == "open":
            note = clean_open(data)
            if note is None:
                spool.expire(r, path, "bad open")
                out["bad"] += 1
                continue
            what = router.open(note)
            out["stale" if what == "stale" else "open"] += 1
        elif op == "resolve":
            # ':' 로 끝나는 키만 앞부분 (notify.resolve 와 같은 뜻 — prefix 칸은 그런 키가 있다는 표시일 뿐)
            # 해결 시각은 Blog-Auto 가 쓴 시각 (지금보다 늦으면 지금) — 다시 열림(open.at, Blog-Auto 시각)과 같은 시계로 비교해야
            # 해결 → 다시 열림이 한 번에 들어와도(2초 안·재시작 동안) 다시 열린 일이 'stale' 로 사라지지 않는다
            at = _num(data.get("at"), 0, 1e11)
            router.resolve("blogauto", clean_keys(data.get("keys")), at=min(at, now) if at else None)
            out["resolve"] += 1
        else:
            spool.expire(r, path, "bad op")
            out["bad"] += 1
            continue
        router.store.mark_seen(name, now)
        spool.remove(path)
    return out
