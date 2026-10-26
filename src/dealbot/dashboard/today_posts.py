"""'오늘 올린 글' (주인 10/23 — "각 블로그 별로 어떤 글을, 어떤 채널에 어떤 글을 올렸는지 싹 취합"): 오늘 한 일 시트 맨 위 칸.

build(bot, v2, start, end, tz) → {"count", "groups": [{"key","label","rows": [{"title","when","link"?: {"label","url"}}]}], "summary": [str]}
- summary: 맨 위 요약 — 딜 가게별 수 · 링크 종류별 수(쿠팡 파트너스·토스 쉐어링크·쇼핑커넥트·원본) · 블로그별 글 · 인스타·스레드
- 블로그 셋: Blog-Auto 기록 파일 recent[] (title·url·at) · 텔레그램: posts(dry_run=0)+products · 인스타: ig_posts(permalink)
- 스레드: 딜봇 threads_jobs(posted) + 계정 전체 글(기록 파일 threads.posts) 을 주소·글 id 로 겹치지 않게 합침
- 글자는 이미 걸러진 글(safe_text)만, 주소는 https 만 (블로그는 네이버 글 주소만). 빈 칸은 숨김. 새로 받는 것 없음 (DB·기록 파일만)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dealbot.dashboard.model import https_url, safe_text
from dealbot.dashboard.state import _name
from dealbot.dashboard.today import naver_url
from dealbot.utils.timeutil import from_iso, to_iso

BLOG_LABELS = (("finance", "아카이브 (derenlog)"), ("issue", "Pick (derenpick)"), ("hotdeal", "데미얌의 쇼핑일기 (demiyum)"))
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


def shop_of(product_id: Any) -> str:
    """'toss:123' → 'toss' (상품 키 앞머리 = 가게). 앞머리가 없으면 ''."""
    head, sep, _ = str(product_id or "").partition(":")
    return head if sep else ""


def link_kind(url: Any) -> str:
    """채널 글에 붙은 링크 종류: coupang(쿠팡 파트너스)·toss(토스 쉐어링크)·naver(쇼핑커넥트)·raw(제휴 아님 — 원본 주소)."""
    host = urlparse(str(url or "")).netloc.lower()
    if host.endswith(("link.coupang.com", "coupa.ng")):
        return "coupang"
    if host.endswith("toss.im"):
        return "toss"
    if host.endswith(("naver.me", "brandconnect.naver.com")):
        return "naver"
    return "raw"


def _label(bot: Any, shop: str) -> str:
    try:
        return str(bot.notifier.shop_label(shop) or shop)
    except Exception:  # noqa: BLE001
        return shop


def _deals(bot: Any, start: float, end: float, tz: ZoneInfo) -> list[Any]:
    s, e = to_iso(datetime.fromtimestamp(start, tz)), to_iso(datetime.fromtimestamp(end, tz))
    return bot.db._q("SELECT p.product_id, p.message_id, p.posted_at, p.affiliate_url, pr.name FROM posts p "
                     "LEFT JOIN products pr ON pr.product_id = p.product_id "
                     "WHERE p.dry_run = 0 AND p.posted_at >= ? AND p.posted_at < ? ORDER BY p.posted_at DESC LIMIT ?", (s, e, LIMIT))


def telegram(bot: Any, start: float, end: float, tz: ZoneInfo) -> list[dict[str, Any]]:
    base = getattr(bot.notifier, "channel_post_base", None)
    out = []
    for r in _deals(bot, start, end, tz):
        url = https_url(f"{base}/{r['message_id']}") if base and r["message_id"] else None
        shop = shop_of(r["product_id"])
        title = (f"[{_label(bot, shop)}] " if shop else "") + (_name(r["name"] or "") or "딜")  # 어느 가게 딜인지 (주인 10/23)
        out.append(_row(from_iso(r["posted_at"]).timestamp(), title, tz, url, "채널 글"))
    return out


LINK_LABELS = (("coupang", "쿠팡 파트너스"), ("toss", "토스 쉐어링크"), ("naver", "쇼핑커넥트"), ("raw", "원본 주소(제휴 아님)"))


def summary(bot: Any, groups: dict[str, list[dict[str, Any]]], start: float, end: float, tz: ZoneInfo) -> list[str]:
    """오늘 한 일 시트 맨 위 요약 줄 (주인 10/23 — '쿠팡 링크 몇 개, 토스 링크 몇 개 … 요약해서 다 확인'). 0 인 칸은 뺀다."""
    out: list[str] = []
    try:
        deals = _deals(bot, start, end, tz)
    except Exception:  # noqa: BLE001
        deals = []
    if deals:
        shops: dict[str, int] = {}
        for r in deals:
            k = shop_of(r["product_id"]) or "etc"
            shops[k] = shops.get(k, 0) + 1
        out.append(f"딜 {len(deals)}개 — " + " · ".join(f"{_label(bot, k) if k != 'etc' else '기타'} {n}"
                                                       for k, n in sorted(shops.items(), key=lambda kv: -kv[1])))
        kinds: dict[str, int] = {}
        for r in deals:
            kinds[link_kind(r["affiliate_url"])] = kinds.get(link_kind(r["affiliate_url"]), 0) + 1
        out.append("링크 — " + " · ".join(f"{lb} {kinds[k]}" for k, lb in LINK_LABELS if kinds.get(k)))
    blog = [f"{label.split(' (')[0]} {len(groups[k])}" for k, label in BLOG_LABELS if groups.get(k)]
    if blog:
        out.append("블로그 글 — " + " · ".join(blog))
    sns = [f"{lb} {len(groups[k])}" for k, lb in (("instagram", "인스타"), ("threads", "스레드")) if groups.get(k)]
    if sns:
        out.append("SNS — " + " · ".join(sns))
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
        if r.get("kind") == "pick":  # 데런픽 글 가공 (growth/blog_share)
            pick = next((i for i in r.get("items") or [] if isinstance(i, dict) and i.get("pick_pid")), {})
            kind, title = "데런픽 · ", str(pick.get("title") or "")
        if r.get("kind") == "look":  # 데런픽 룩 계정 (growth/look_share)
            look = next((i for i in r.get("items") or [] if isinstance(i, dict) and i.get("look_pid")), {})
            kind, title = "데런픽 룩 · ", str(look.get("title") or "")
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
    return {"count": sum(len(g["rows"]) for g in out), "groups": out,
            "summary": summary(bot, {k: rs for k, _, rs in groups}, start, end, z)}
