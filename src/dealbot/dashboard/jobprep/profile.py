"""① 내 소개 — 프로필 (적합도·비교·대화의 기준). 첫 값은 SEED (주인이 준 내용 그대로) · 고치면 ver +1 → 열린 공고 적합도를 천천히 다시."""

from __future__ import annotations

import json
import time
from typing import Any

from dealbot.dashboard.jobprep.store import JpError, clip

FIELDS = ("school", "major", "student_no", "military", "return_plan", "graduation", "credits", "gpa", "liked_courses", "interests",
          "wants", "english", "experience", "certs", "avoid", "note")
SEED: dict[str, str] = {
    "school": "연세대학교",
    "major": "전기전자공학부 (건설환경공학과에서 전과)",
    "student_no": "",
    "military": "공군 복무 중 · 전역 2027-07-13 · 말년 휴가 2027-06-01쯤",
    "return_plan": "복학 3-2 (2027 가을) → 4-1 → 4-2",
    "graduation": "2029-02 졸업 예정",
    "credits": "79",
    "gpa": "3.83 / 4.3",
    "liked_courses": "",
    "interests": "IB·금융 · 크립토(거래소·마켓메이커) · AI·테크 성장 기업 · 컨설팅 · 퀀트",
    "wants": "전환형 인턴 · 높은 보수",
    "english": "",
    "experience": "",
    "certs": "",
    "avoid": "회로 설계",
    "note": "",
}
LABELS = {"school": "학교", "major": "학과", "student_no": "학번", "military": "군", "return_plan": "복학", "graduation": "졸업",
          "credits": "이수 학점", "gpa": "평점", "liked_courses": "좋아한 과목", "interests": "관심 분야", "wants": "바라는 것",
          "english": "영어", "experience": "경험", "certs": "자격증", "avoid": "피하고 싶은 것", "note": "메모"}


def get(store: Any) -> tuple[dict[str, str], int]:
    r = store.one("SELECT body, ver FROM profile WHERE id=1")
    if r is None:
        return dict(SEED), 1
    try:
        body = {**SEED, **json.loads(r["body"])}
    except ValueError:
        body = dict(SEED)
    return body, int(r["ver"] or 1)


def save(store: Any, body: dict[str, Any]) -> dict[str, Any]:
    cur, ver = get(store)
    new = {k: clip(body.get(k, cur.get(k, "")), 600) for k in FIELDS}
    if new == cur:
        return {"ver": ver, "changed": False}
    store.x("INSERT INTO profile (id, body, ver, at) VALUES (1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET body=excluded.body, ver=excluded.ver, at=excluded.at",
            (json.dumps(new, ensure_ascii=False), ver + 1, time.time()))
    return {"ver": ver + 1, "changed": True}


def summary(p: dict[str, str], limit: int = 400) -> str:
    """적합도·비교·대화에 넣는 짧은 스펙 (400자)."""
    order = ("school", "major", "gpa", "credits", "graduation", "military", "interests", "wants", "english", "experience", "certs", "avoid")
    parts = [f"{LABELS[k]}: {p[k]}" for k in order if p.get(k)]
    out = " · ".join(parts)
    return out[:limit]


def view(jp: Any) -> dict[str, Any]:
    p, ver = get(jp.store)
    return {"fields": [{"k": k, "label": LABELS[k], "v": p.get(k, "")} for k in FIELDS], "ver": ver, "summary": summary(p),
            "intro": jp.store.kv_get(f"intro:{ver}")}


def post(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise JpError(400, "bad", "고칠 칸이 없어요")
    out = save(jp.store, fields)
    return {**view(jp), **out}


INTRO_SCHEMA = {"type": "object", "properties": {"one_liner_ko": {"type": "string"}, "one_liner_en": {"type": "string"},
                                                  "pitch_ko": {"type": "string"}, "pitch_en": {"type": "string"}},
                "required": ["one_liner_ko", "one_liner_en", "pitch_ko", "pitch_en"]}


async def intro(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    p, ver = get(jp.store)
    out = await jp.llm.ask("profile.intro", "good", "너는 취업 준비생의 자기소개를 돕는다. 주어진 사실만 쓰고 지어내지 않는다.",
                              "프로필:\n" + summary(p, 1200) + "\n\n한 줄 소개(한/영)와 1분 자기소개(한 300~400자 / 영 120~150 words)를 써 줘.",
                              schema=INTRO_SCHEMA)
    jp.store.kv_set(f"intro:{ver}", out)
    return {"intro": out}
