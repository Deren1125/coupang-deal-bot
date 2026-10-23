"""'오늘 올린 글' (주인 10/23 — "각 블로그 별로 어떤 글을, 어떤 채널에 어떤 글을 올렸는지 싹 취합"): 오늘 한 일 시트 맨 위 칸.

build(bot, v2, start, end, tz) → {"count", "groups": [{"key","label","rows": [{"title","when","link"?: {"label","url"}}]}]}
- 블로그 셋: Blog-Auto 기록 파일 recent[] (title·url·at) · 텔레그램: posts(dry_run=0)+products · 인스타: ig_posts(permalink)
- 스레드: 딜봇 threads_jobs(posted) + 계정 전체 글(기록 파일 threads.posts) 을 주소·글 id 로 겹치지 않게 합침
- 글자는 이미 걸러진 글(safe_text)만, 주소는 https 만 (블로그는 네이버 글 주소만). 빈 칸은 숨김. 새로 받는 것 없음 (DB·기록 파일만)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import https_url, safe_text
from dealbot.dashboard.state import _name
from dealbot.dashboard.today import naver_url
from dealbot.utils.timeutil import from_iso, to_iso

BLOG_LABELS = (("finance", "아카이브 (derenlog)"), ("issue", "Pick (derenpick)"), ("hotdeal", "오늘의 핫딜 (demiyum)"))
TITLE_MAX = 60
LIMIT = 200


def _line(text: Any) -> str:
    for ln in str(text or "").splitlines():
        if ln.strip():
            return safe_text(" ".join(ln.split()), TITLE_MAX)
    return ""


def _ts(v: Any, tz: ZoneInfo) -> float | None:
    """ISO 글자 → 초 (시간대 없으면 설정 시간대로 본다)."""
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()


def _row(ts: float, title: str, tz: ZoneInfo, url: str | None = None, label: str = "글") -> dict[str, Any]:
    out: dict[str, Any] = {"title": title, "when": datetime.fromtimestamp(ts, tz).strftime("%H:%M"), "at": ts}
    if url:
        out["link"] = {"label": label, "url": url}
    return out


def _json(raw: Any) -> dict[str, Any]:
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _names(db: Any, pids: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pid in pids:
        r = db._one("SELECT name FROM products WHERE product_id = ?", (pid,))
        if r and r["name"]:
            out[pid] = _name(r["name"])
    return out


def blogs(v2: dict[str, Any], start: float, end: float, tz: ZoneInfo) -> list[tuple[str, str, list[dict[str, Any]]]]:
    out = []
    for key, label in BLOG_LABELS:
        rows = []
        for r in (v2.get("recent") or {}).get(key) or []:
            ts = _ts(r.get("published_at") or r.get("at"), tz) if isinstance(r, dict) else None
            if ts is not None and start <= ts < end:
                rows.append(_row(ts, safe_text(r.get("title") or "", TITLE_MAX) or "(제목 없음)", tz, naver_url(r.get("url"))))
        out.append((key, label, rows))
    return out


def telegram(bot: Any, start: float, end: float, tz: ZoneInfo) -> list[dict[str, Any]]:
    s, e = to_iso(datetime.fromtimestamp(start, tz)), to_iso(datetime.fromtimestamp(end, tz))
    base = getattr(bot.notifier, "channel_post_base", None)
    rs = bot.db._q("SELECT p.product_id, p.message_id, p.posted_at, pr.name FROM posts p LEFT JOIN products pr ON pr.product_id = p.product_id "
                   "WHERE p.dry_run = 0 AND p.posted_at >= ? AND p.posted_at < ? ORDER BY p.posted_at DESC LIMIT ?", (s, e, LIMIT))
    out = []
    for r in rs:
        url = https_url(f"{base}/{r['message_id']}") if base and r["message_id"] else None
        out.append(_row(from_iso(r["posted_at"]).timestamp(), _name(r["name"] or "") or "딜", tz, url, "채널 글"))
    return out


def instagram(bot: Any, start: float, end: float, tz: ZoneInfo) -> list[dict[str, Any]]:
    out = []
    for r in bot.db.ig_posts_since(datetime.fromtimestamp(start, tz)):
        ts = r["posted_at"].timestamp()
        if r.get("dry_run") or ts >= end:
            continue
        items = [i for i in r.get("items") or [] if isinstance(i, dict) and i.get("product_id")]
        title = _names(bot.db, {str(items[0]["product_id"])}).get(str(items[0]["product_id"]), "") if items else ""
        if len(items) > 1:
            title = f"{title or '딜'} 외 {len(items) - 1}개"
        kind = {"single": "", "reels": "릴스 · ", "top5": "TOP5 · "}.get(str(r.get("kind")), "")
        out.append(_row(ts, kind + (title or "인스타 글"), tz, https_url(r.get("permalink")), "인스타"))
    return sorted(out, key=lambda x: x["at"], reverse=True)


def _job_title(payload: dict[str, Any], fallback: str) -> str:
    deal = payload.get("deal") if isinstance(payload.get("deal"), dict) else {}
    prod = deal.get("product") if isinstance(deal.get("product"), dict) else {}
    return _line(payload.get("text")) or _name(prod.get("name") or "") or _name(payload.get("name") or "") or fallback


def threads(bot: Any, v2: dict[str, Any], start: float, end: float, tz: ZoneInfo) -> list[dict[str, Any]]:
    """딜봇 스레드 글 + 계정 전체 글 — 같은 글(주소 또는 글 id)은 한 줄."""
    items: dict[str, dict[str, Any]] = {}
    ids: dict[str, str] = {}

    def put(key: str, row: dict[str, Any]) -> None:
        items.setdefault(key, row)

    export = [x for x in ((v2.get("threads") or {}).get("posts") or []) if isinstance(x, dict)]
    by_id = {str(x.get("id")): x for x in export}
    by_link = {str(x.get("permalink")): x for x in export if x.get("permalink")}
    jobs = bot.db._q("SELECT post_id, payload, posted_at, permalink FROM threads_jobs WHERE status = 'posted' AND dry_run = 0 "
                     "AND post_id IS NOT NULL AND post_id != '' AND posted_at >= ? AND posted_at < ? ORDER BY posted_at DESC LIMIT ?",
                     (to_iso(datetime.fromtimestamp(start, tz)), to_iso(datetime.fromtimestamp(end, tz)), LIMIT))
    for j in jobs:
        ts = from_iso(j["posted_at"]).timestamp()
        link = https_url(j["permalink"])
        ex = by_id.get(j["post_id"]) or by_link.get(link or "") or {}
        link = link or https_url(ex.get("permalink"))
        title = _line(ex.get("text")) or _job_title(_json(j["payload"]), "스레드 글")
        key = link or f"id:{j['post_id']}"
        ids[j["post_id"]] = key
        put(key, _row(ts, title, tz, link, "스레드"))
    for x in export:
        ts = _ts(x.get("at"), tz)
        link = https_url(x.get("permalink"))
        if ts is None or not (start <= ts < end) or str(x.get("id")) in ids or (link and link in items):
            continue
        put(link or f"id:{x.get('id')}", _row(ts, _line(x.get("text")) or "스레드 글", tz, link, "스레드"))
    return sorted(items.values(), key=lambda x: x["at"], reverse=True)


def build(bot: Any, v2: dict[str, Any], start: float, end: float, tz: str) -> dict[str, Any]:
    z = ZoneInfo(tz)
    groups: list[tuple[str, str, list[dict[str, Any]]]] = blogs(v2, start, end, z)
    for key, label, fn in (("telegram", "텔레그램 채널", lambda: telegram(bot, start, end, z)),
                           ("instagram", "인스타그램", lambda: instagram(bot, start, end, z)),
                           ("threads", "스레드", lambda: threads(bot, v2, start, end, z))):
        try:
            groups.append((key, label, fn()))
        except Exception:  # noqa: BLE001 — 한 채널이 안 읽혀도 나머지는 보여 줌
            groups.append((key, label, []))
    out = [{"key": k, "label": lb, "rows": [{a: b for a, b in r.items() if a != "at"} for r in sorted(rs, key=lambda x: x["at"], reverse=True)]}
           for k, lb, rs in groups if rs]
    return {"count": sum(len(g["rows"]) for g in out), "groups": out}
