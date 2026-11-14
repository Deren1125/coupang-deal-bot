"""이웃 글 들르기 ↔ 오늘 할 일 줄 (주인 11/14 '이웃 글 들르기 공감·댓글도 책갈피로 — 세팅').

공감·댓글 책갈피는 이미 하나 있다 — '이웃 관리' (static/seoi.js · seoi.py, 같은 날 먼저 올라감): 이웃 글 화면에서 누르면 공감 한 번 + 댓글 초안,
등록은 주인이. 여기서 책갈피를 하나 더 만들면 주인 책갈피가 둘이 되고 사람 속도 상한(seoi.gate)을 비켜 가므로 만들지 않는다.
이 파일은 그 책갈피와 '오늘 할 일'의 이웃 글 들르기 줄(Blog-Auto routine 'nb-visit:<블로그>:<이웃 아이디>:<글 번호>')을 잇는 것만 한다:

  view(db, items, day)  할 일 탭이 읽는 칸 (routine.view()["nb"]) — 블로그마다 남은 줄 수 · 다음에 열 글(주소 끝에 #bmb=<블로그> — 책갈피가
                        어느 블로그 계정인지 앎) · 오늘 책갈피로 체크한 수. routine.js 가 '이웃 글 들르기 시작 (n)' 단추로 그 글을 새 탭에 연다
                        (누를 때마다 한 편 — 저절로 넘어가지 않음).
  tick(bot, blog, url)  '이웃 관리' 책갈피가 그 글에서 공감·초안을 받아 가면 (api.seoi_act 가 seoi.act 성공 뒤에 부름) 그 줄을 체크한다 —
                        routine.done 과 같은 길 (routine.set_done · 봇 DB kv 만). 지키는 것:
                          · 오늘 목록의 nb-visit 줄만 (목록이 어제 것이거나 그 글이 줄에 없으면 아무것도 안 함)
                          · 그 블로그 줄만 (다른 블로그 계정으로 들른 글은 체크하지 않음)
                          · 블로그마다 하루 DAY_CAP(5 — Blog-Auto routine.NB_VISIT 와 같게) 번까지
                          · 이미 체크된 줄은 다시 세지 않음
                        실패해도 책갈피 응답은 그대로 (체크만 안 됨 — 주인이 줄을 직접 체크하면 됨).

여기에는 AI 가 없다 (댓글 초안은 seoi.py 가 Sonnet 까지만 · Blog-Auto 줄의 '댓글 초안'은 그대로). 네이버를 열지 않고, 남의 글 주소는 kv 에 줄 id 로만 남는다.
kv: "nbvisit:day:<목록 날짜>" = {블로그: [체크한 줄 id]} (routine.set_done 처럼 그날·전날 것만 남김).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

log = logging.getLogger(__name__)

DAY_KEY = "nbvisit:day:"
DAY_CAP = 5  # 블로그마다 하루 (Blog-Auto routine.NB_VISIT)
BLOGS = ("issue", "finance", "hotdeal")
ROW_RE = re.compile(r"^nb-visit:(finance|issue|hotdeal):([a-z0-9_-]{1,40}):(\d{1,20})$")
_HOSTS = ("blog.naver.com", "m.blog.naver.com")
_PATH_RE = re.compile(r"^/([A-Za-z0-9_-]{3,30})/(\d{6,20})/?$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{3,30}$")
_NO_RE = re.compile(r"^\d{6,20}$")


def post_of(url: Any) -> tuple[str, str] | None:
    """네이버 블로그 글 주소 → (블로그 아이디 소문자, 글 번호). https · blog.naver.com / m.blog.naver.com 만, 그 밖은 None."""
    if not isinstance(url, str) or len(url) > 500:
        return None
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() not in _HOSTS or u.port is not None or u.username is not None:
        return None
    m = _PATH_RE.match(u.path)
    if m:
        return m.group(1).lower(), m.group(2)
    q = parse_qs(u.query)
    nid, no = (q.get("blogId") or [""])[0], (q.get("logNo") or [""])[0]
    return (nid.lower(), no) if _ID_RE.match(nid) and _NO_RE.match(no) else None


def rows(items: Any, blog: str | None = None) -> list[dict[str, Any]]:
    """오늘 할 일 줄에서 이웃 글 들르기 줄만 [{id, blog, nid, no, url, title, done}] (blog 를 주면 그 블로그 것만)."""
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        m = ROW_RE.match(it.get("id")) if isinstance(it, dict) and isinstance(it.get("id"), str) else None
        if not m or (blog is not None and m.group(1) != blog):
            continue
        url = next((str(k.get("url") or "") for k in it.get("links") or [] if isinstance(k, dict)), "")
        out.append({"id": it["id"], "blog": m.group(1), "nid": m.group(2), "no": m.group(3), "url": url if post_of(url) else "",
                    "title": re.sub(r"^공감·댓글 · ", "", str(it.get("title") or "")), "done": it.get("done") is True})
    return out


def find(items: Any, blog: str, url: Any) -> str | None:
    """그 글의 줄 id — 줄 id 의 (이웃 아이디, 글 번호) 또는 줄의 '글 열기' 주소가 그 글일 때. 없으면 None."""
    post = post_of(url)
    if post is None:
        return None
    for r in rows(items, blog):
        if (r["nid"], r["no"]) == post or (r["url"] and post_of(r["url"]) == post):
            return r["id"]
    return None


def _ticked(db: Any, day: str) -> dict[str, list[str]]:
    try:
        v = json.loads(db.kv_get(DAY_KEY + day) or "{}")
    except (TypeError, ValueError):
        v = {}
    return {b: [x for x in v.get(b) or [] if isinstance(x, str) and ROW_RE.match(x)] for b in BLOGS} if isinstance(v, dict) else {b: [] for b in BLOGS}


def tick(bot: Any, blog: str, url: Any, now: float) -> dict[str, Any]:
    """책갈피가 들른 글의 줄을 체크. 돌려줌 {ok, id?, n?, why?} — why: off(오늘 목록 없음) · row(줄에 없는 글) · cap(하루 5번) · had(이미 체크)."""
    from dealbot.dashboard import routine

    if blog not in BLOGS:
        return {"ok": False, "why": "row"}
    r = routine.blog_routine(bot)
    if not r or r.get("day") != routine.today(bot, now):
        return {"ok": False, "why": "off"}
    rid = find(r.get("items"), blog, url)
    if rid is None:
        return {"ok": False, "why": "row"}
    day = r["day"]
    if rid in routine.done_ids(bot.db, day):
        return {"ok": False, "why": "had", "id": rid}
    got = _ticked(bot.db, day)
    if len(got[blog]) >= DAY_CAP:
        return {"ok": False, "why": "cap", "id": rid}
    routine.set_done(bot.db, day, rid, True)  # routine.done 과 같은 길
    got[blog] = [*[x for x in got[blog] if x != rid], rid]
    bot.db.kv_set(DAY_KEY + day, json.dumps(got, ensure_ascii=False, separators=(",", ":")))
    try:
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        bot.db.kv_delete_prefix(DAY_KEY, keep=lambda k, _v: k in (DAY_KEY + day, DAY_KEY + prev))
    except ValueError:
        pass
    log.info("nbvisit: 줄 체크 blog=%s n=%d/%d", blog, len(got[blog]), DAY_CAP)
    return {"ok": True, "id": rid, "n": len(got[blog])}


def view(db: Any, items: Any, day: str) -> dict[str, Any] | None:
    """할 일 탭 칸 {cap, by: {블로그: {left, n, next: {id, url, title} | None}}} — 이웃 글 들르기 줄이 하나도 없으면 None.
    next = 아직 안 한 첫 줄 (주소가 네이버 블로그 글일 때만 · 끝에 #bmb=<블로그>)."""
    rs = rows(items)
    if not rs:
        return None
    got = _ticked(db, day)
    by: dict[str, Any] = {}
    for b in BLOGS:
        left = [r for r in rs if r["blog"] == b and not r["done"]]
        if not any(r["blog"] == b for r in rs):
            continue
        nxt = next((r for r in left if r["url"]), None)
        by[b] = {"left": len(left), "n": len(got[b]),
                 "next": {"id": nxt["id"], "url": nxt["url"].split("#", 1)[0] + "#bmb=" + b, "title": nxt["title"]} if nxt else None}
    return {"cap": DAY_CAP, "by": by}
