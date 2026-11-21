"""아래 대화창 (모든 탭) — 그 탭에 보이는 것을 짧게 묶어(≤ CTX_TOKENS) + 그 탭·맥락 대화 최근 12개 → Gemini(기본) 또는 Claude(단추).
대화는 탭·맥락별로 DB 에. 프롬프트·답 글은 로그에 안 남김."""

from __future__ import annotations

import json
from typing import Any

from dealbot.dashboard.jobprep import ctci, english, profile
from dealbot.dashboard.jobprep.store import JpError, clip

TABS = ("today", "me", "acad", "jobs", "cases", "code", "lib", "speech", "eng", "set")
CTX_TOKENS = 6000
HISTORY = 12
SYSTEM = ("너는 취업 준비를 돕는 코치다 (지원자는 한국 대학생 · 군 복무 중). 한국어로 짧고 분명하게 답한다. 모르는 것은 모른다고 한다. "
          "아래 '지금 화면' 은 지원자가 보고 있는 것이다.")


def tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 2) // 3


def cut(text: str, limit_tokens: int) -> str:
    if tokens(text) <= limit_tokens:
        return text
    b = text.encode("utf-8")[: limit_tokens * 3]
    return b.decode("utf-8", "ignore")


def context(jp: Any, tab: str, ctx: str) -> str:
    prof, _ = profile.get(jp.store)
    parts = ["[내 스펙] " + profile.summary(prof)]
    if tab == "jobs":
        rows = jp.store.q("SELECT j.title, j.loc, j.fit, j.fit_why, j.deadline, c.name company FROM jobs j LEFT JOIN companies c ON c.id=j.company_id "
                          "WHERE j.state='open' AND j.auto_out=0 ORDER BY COALESCE(j.fit,0) DESC LIMIT 20")
        parts.append("[보이는 공고] " + " / ".join(f"{r['company']}·{r['title']}·{r['loc']}·★{r['fit'] or '-'}·마감 {r['deadline'] or '-'}" for r in rows))
    elif tab == "code" and ctx:
        q = next((r for r in ctci.load_index(jp) if r["qid"] == ctx), None)
        text = ctci.load_text(jp, ctx)
        last = jp.store.one("SELECT code, passed, total, fail_case FROM attempts WHERE qid=? ORDER BY at DESC LIMIT 1", (ctx,))
        parts.append(f"[문제 {ctx} {q['title'] if q else ''}] {(text.get('question') or '')[:1500]}")
        if last:
            parts.append(f"[내 코드 · 마지막 채점 {last['passed']}/{last['total']}] {last['code'][:3000]}\n실패: {last['fail_case'] or '-'}")
    elif tab == "eng":
        t = english.today_view(jp)
        fixes = jp.store.q("SELECT mine, better FROM eng_fix ORDER BY id DESC LIMIT 8")
        parts.append("[오늘 통화 상대] " + json.dumps(t.get("persona") or {}, ensure_ascii=False)[:600])
        parts.append("[최근 교정] " + " / ".join(f"{f['mine']} → {f['better']}" for f in fixes))
    elif tab == "acad":
        cs = jp.store.q("SELECT name, credits, grade, area FROM acad_courses LIMIT 80")
        parts.append("[들은 과목] " + ", ".join(f"{c['name']}({c['credits']:g},{c['grade']},{c['area']})" for c in cs))
    elif tab == "cases":
        cs = jp.store.q("SELECT company, role, school, gpa, exp FROM cases ORDER BY at DESC LIMIT 12")
        parts.append("[합격 사례] " + " / ".join(f"{c['company']}·{c['role']}·{c['school']}·{c['gpa']}·{c['exp'][:60]}" for c in cs))
    elif tab == "speech":
        h = jp.store.one("SELECT title FROM human_topics ORDER BY day DESC LIMIT 1")
        parts.append("[오늘 인문학] " + (h["title"] if h else "-"))
    return cut("\n".join(parts), CTX_TOKENS)


def history(jp: Any, tab: str, ctx: str, n: int = HISTORY) -> list[dict[str, Any]]:
    rows = jp.store.q("SELECT role, model, text, at FROM chat WHERE tab=? AND ctx=? ORDER BY at DESC, id DESC LIMIT ?", (tab, ctx, n))
    return list(reversed(rows))


def view(jp: Any, query: dict[str, str]) -> dict[str, Any]:
    tab, ctx = clip(query.get("tab"), 10), clip(query.get("ctx"), 40)
    return {"messages": history(jp, tab, ctx, 30), "spend": jp.llm.view()}


async def send(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    tab, ctx, text = clip(body.get("tab"), 10), clip(body.get("ctx"), 40), clip(body.get("text"), 4000)
    model = "claude" if body.get("model") == "claude" else "gemini"
    if tab not in TABS:
        raise JpError(400, "bad", "탭을 알 수 없어요")
    if not text:
        raise JpError(400, "empty", "물을 말을 넣어 주세요")
    hist = history(jp, tab, ctx)
    convo = "\n".join(f"{'나' if m['role'] == 'user' else '코치'}: {m['text'][:1200]}" for m in hist)
    user = f"[지금 화면]\n{context(jp, tab, ctx)}\n\n[지난 대화]\n{convo}\n\n나: {text}"
    if model == "claude":
        reply = await jp.llm.claude("chat", SYSTEM, user)
    else:
        reply = await jp.llm.gemini("chat", "fast", SYSTEM, user, cache=False, max_tokens=2048)
    now = jp.clock()
    jp.store.x("INSERT INTO chat (tab, ctx, role, model, text, at) VALUES (?,?,?,?,?,?)", (tab, ctx, "user", model, text, now))
    jp.store.x("INSERT INTO chat (tab, ctx, role, model, text, at) VALUES (?,?,?,?,?,?)", (tab, ctx, "assistant", model, reply, now + 0.001))
    return {"reply": reply, "model": model, "spend": jp.llm.view()}
