"""⑥ 학습 자료함 — 링크·메모·파일 이름 + 꼬리표. 다른 탭의 '자료함에 넣기'도 여기로. AI 는 '요약' 단추만 (Gemini fast)."""

from __future__ import annotations

import time
from typing import Any

from dealbot.dashboard.jobprep.store import JpError, clip

KINDS = ("link", "note", "file", "card")


def view(jp: Any, query: dict[str, str]) -> dict[str, Any]:
    tag = clip(query.get("tag"), 40)
    rows = jp.store.q("SELECT * FROM library ORDER BY at DESC LIMIT 300")
    tags: dict[str, int] = {}
    out = []
    for r in rows:
        ts = [t for t in (r["tags"] or "").split(",") if t]
        for t in ts:
            tags[t] = tags.get(t, 0) + 1
        if tag and tag not in ts:
            continue
        out.append({"id": r["id"], "kind": r["kind"], "title": r["title"], "url": r["url"], "body": r["body"], "tags": ts,
                    "src_tab": r["src_tab"], "at": r["at"]})
    return {"items": out, "tags": sorted(tags.items(), key=lambda x: -x[1])}


def add(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    kind = body.get("kind") if body.get("kind") in KINDS else "note"
    title = clip(body.get("title"), 200)
    url = clip(body.get("url"), 600)
    text = clip(body.get("body"), 8000)
    if url and not url.startswith(("https://", "http://")):
        raise JpError(400, "bad_url", "주소는 http(s):// 로 시작해야 해요")
    if not (title or url or text):
        raise JpError(400, "empty", "넣을 내용이 없어요")
    tags = ",".join(t.strip()[:30] for t in (body.get("tags") or []) if isinstance(t, str) and t.strip())[:300]
    i = jp.store.x("INSERT INTO library (kind, title, url, body, tags, src_tab, at) VALUES (?,?,?,?,?,?,?)",
                   (kind, title or url[:80] or text[:40], url, text, tags, clip(body.get("src_tab"), 20), time.time()))
    return {"id": i}


def delete(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    i = body.get("id")
    if not isinstance(i, int):
        raise JpError(400, "bad", "번호가 없어요")
    jp.store.x("DELETE FROM library WHERE id=?", (i,))
    return {"ok": True}


async def summarize(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    i = body.get("id")
    r = jp.store.one("SELECT * FROM library WHERE id=?", (i,)) if isinstance(i, int) else None
    if r is None or not (r["body"] or "").strip():
        raise JpError(404, "gone", "요약할 글이 없어요")
    text = await jp.llm.gemini("lib.summary", "fast", "주어진 글을 한국어 3줄로 요약한다. 없는 내용은 쓰지 않는다.", r["body"][:6000])
    return {"summary": text}
