"""⑧ 영어 — 통화는 주인 폰의 Gemini 앱 Live. 대시보드는 오늘의 상대·상황 프롬프트(복사 한 번) + 통화 뒤 정리 + 리얼클래스 표현 칸.

주 1번 Gemini good 한 번으로 7일치 상대·상황 · 하루 프롬프트는 **코드가 조립** (AI 없음): 고정 규칙 + 그날 상대 + 어제 자주 틀린 것 3개 + 오늘 표현.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep.store import JpError, clip

KST = ZoneInfo("Asia/Seoul")
RULES = (
    "You are my English conversation partner on a phone call. Act like a real person, not a teacher: keep turns short, ask follow-up "
    "questions, and share your own stories and opinions too.\n"
    "When I make a mistake, do not stop to correct me. Instead, naturally say the correct version back inside your reply (recast).\n"
    "If I say \"just a moment\", \"wait a second\" or \"let me think\", say only \"Sure, take your time.\" and then stay silent and wait.\n"
    "If I say \"feedback\", give me exactly 3 corrections from what I said so far (my sentence -> a natural sentence -> why).\n"
    "We will talk for 20 to 30 minutes."
)
SITUATIONS = ("일상", "여행", "학교", "일", "면접", "토론", "이야기", "유머")
WEEK_SCHEMA = {"type": "object", "properties": {"days": {"type": "array", "items": {"type": "object", "properties": {
    "name": {"type": "string"}, "personality": {"type": "string"}, "accent": {"type": "string", "enum": ["US", "UK", "AU", "SG", "IN"]},
    "situation": {"type": "string"}, "kind": {"type": "string", "enum": list(SITUATIONS)}, "opener": {"type": "string"}},
    "required": ["name", "personality", "accent", "situation", "kind", "opener"]}}}, "required": ["days"]}
AFTER_SCHEMA = {"type": "object", "properties": {
    "fixes": {"type": "array", "items": {"type": "object", "properties": {"mine": {"type": "string"}, "better": {"type": "string"},
                                                                          "why": {"type": "string"}, "kind": {"type": "string"}},
                                         "required": ["mine", "better", "why", "kind"]}},
    "new_expr": {"type": "array", "items": {"type": "string"}}, "frequent": {"type": "array", "items": {"type": "string"}},
    "expr_used": {"type": "boolean"}}, "required": ["fixes", "new_expr", "frequent", "expr_used"]}
ACCENTS = {"US": "American", "UK": "British", "AU": "Australian", "SG": "Singaporean", "IN": "Indian"}


def day_of(ts: float, back: int = 0) -> str:
    return (datetime.fromtimestamp(ts, KST).date() - timedelta(days=back)).isoformat()


async def make_week(jp: Any) -> int:
    t = datetime.fromtimestamp(jp.clock(), KST).date()
    start = t - timedelta(days=t.weekday())
    if jp.store.one("SELECT day FROM eng_week WHERE week=?", (start.isoformat(),)):
        return 0
    out = await jp.llm.gemini(f"eng.week:{start}", "good", "You design realistic English conversation partners for a Korean university student.",
                              "Make 7 days. Each: name, personality, accent(US|UK|AU|SG|IN), situation (one concrete scene, 1-2 sentences), "
                              f"kind (one of {', '.join(SITUATIONS)} — mix them evenly; 면접 at most 2 times), opener (their first line).",
                              schema=WEEK_SCHEMA)
    n = 0
    for i, d in enumerate((out.get("days") or [])[:7]):
        day = (start + timedelta(days=i)).isoformat()
        jp.store.x("INSERT OR REPLACE INTO eng_week (day, body, week) VALUES (?,?,?)", (day, json.dumps(d, ensure_ascii=False), start.isoformat()))
        n += 1
    return n


def frequent_mistakes(jp: Any, day: str, n: int = 3) -> list[str]:
    rows = jp.store.q("SELECT f.mine, f.better FROM eng_fix f JOIN eng_sessions s ON s.id=f.session_id WHERE s.day=? ORDER BY f.id LIMIT ?", (day, n))
    return [f"{r['mine']} -> {r['better']}" for r in rows]


def build_prompt(persona: dict[str, Any] | None, mistakes: list[str], expr: str) -> str:
    parts = [RULES]
    if persona:
        parts.append(f"\nYour character: {persona.get('name')}, {persona.get('personality')}, with a {ACCENTS.get(persona.get('accent'), 'American')} accent.\n"
                     f"Situation: {persona.get('situation')}\nStart the call with: \"{persona.get('opener')}\"")
    if mistakes:
        parts.append("\nI often made these mistakes yesterday — create natural chances for me to use the correct forms:\n- " + "\n- ".join(mistakes))
    if expr:
        parts.append(f"\nToday's expression I am practicing: \"{expr}\". Give me one natural chance to use it.")
    return "\n".join(parts)


def today_view(jp: Any) -> dict[str, Any]:
    day = day_of(jp.clock())
    r = jp.store.one("SELECT body FROM eng_week WHERE day=?", (day,))
    persona = json.loads(r["body"]) if r else None
    ex = jp.store.one("SELECT text FROM eng_expr WHERE day=?", (day,))
    expr = ex["text"] if ex else ""
    mistakes = frequent_mistakes(jp, day_of(jp.clock(), 1))
    sess = jp.store.q("SELECT id, text_len, at FROM eng_sessions WHERE day=?", (day,))
    return {"day": day, "persona": persona, "expr": expr, "mistakes": mistakes, "prompt": build_prompt(persona, mistakes, expr), "sessions": sess}


def set_expr(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    text = clip(body.get("text"), 300)
    jp.store.x("INSERT INTO eng_expr (day, text) VALUES (?,?) ON CONFLICT(day) DO UPDATE SET text=excluded.text", (day_of(jp.clock()), text))
    return today_view(jp)


async def after(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    text = clip(body.get("text"), 30000)
    if len(text) < 40:
        raise JpError(400, "short", "대화 글을 붙여 넣어 주세요")
    day = day_of(jp.clock())
    ex = jp.store.one("SELECT text FROM eng_expr WHERE day=?", (day,))
    out = await jp.llm.gemini("eng.after", "good", "You are an English tutor for a Korean student. Only judge the student's lines (not the AI partner).",
                              f"Transcript:\n{text}\n\nToday's expression: {ex['text'] if ex else '(none)'}\n"
                              "Return: fixes (student's sentence -> natural native sentence -> why in Korean, kind=grammar|word|phrase|pron), "
                              "new_expr (useful expressions from the call), frequent (patterns I repeat wrong), expr_used.", schema=AFTER_SCHEMA)
    sid = jp.store.x("INSERT INTO eng_sessions (day, text_len, result, at) VALUES (?,?,?,?)", (day, len(text), json.dumps(out, ensure_ascii=False), jp.clock()))
    for f in (out.get("fixes") or [])[:30]:
        jp.store.x("INSERT INTO eng_fix (session_id, mine, better, why, kind, at) VALUES (?,?,?,?,?,?)",
                   (sid, clip(f.get("mine"), 400), clip(f.get("better"), 400), clip(f.get("why"), 300), clip(f.get("kind"), 20), jp.clock()))
    if out.get("expr_used") and ex:
        jp.store.x("UPDATE eng_expr SET used=1 WHERE day=?", (day,))
    return {"session_id": sid, **out}


def cards(jp: Any) -> dict[str, Any]:
    return {"fixes": jp.store.q("SELECT f.*, s.day FROM eng_fix f JOIN eng_sessions s ON s.id=f.session_id ORDER BY f.id DESC LIMIT 60"),
            "exprs": jp.store.q("SELECT * FROM eng_expr ORDER BY day DESC LIMIT 30")}
