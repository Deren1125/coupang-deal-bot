"""아침·저녁 정리 (주인 10/18: 급하지 않은 소식은 하루 두 번 묶어서). 딜봇이 만들고 웹 푸시 한 통 + 대시보드 시트.

- 시각 (Asia/Seoul): 아침 07:41~08:19, 저녁 21:03~21:52 사이에서 날마다 한 번 뽑아 kv(digest_plan:YYYY-MM-DD)에 둔다
  (재시작해도 다시 뽑지 않고 두 번 보내지 않음 — 보낸 표시 digest_sent:<날>:<am|pm>). 정각·30분은 피한다.
  봇이 꺼져 있다 켜졌으면 그 시각 + 2시간 안일 때만 보낸다
- 아침 = 기다리는 일 + 밤사이 딜봇 소식 + 소식 (블로그 하루 정리는 전날 저녁에 이미 했으니 뺌)
- 저녁 = 기다리는 일 + 오늘 딜봇 + 블로그마다 하루 정리 + 소식 — 같은 말을 두 번 하지 않게
- 문장으로 (숫자만 늘어놓지 않음). 아무 것도 없으면 푸시는 보내지 않는다 (조용히)
- 저녁에는 카톡(예비) 한 통도 (200자 안, 대시보드 주소와 함께 — 버튼이 안 열려도 글만 보고 알 수 있게)
"""

from __future__ import annotations

import json
import logging
import random
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.bridge.kakao_out import fit
from dealbot.notify import iid as iidmod
from dealbot.notify.router import NotifyRouter
from dealbot.utils.text import truncate

log = logging.getLogger(__name__)

WINDOWS = {"am": (7 * 60 + 41, 8 * 60 + 19), "pm": (21 * 60 + 3, 21 * 60 + 52)}
LATE_S = 2 * 3600
TITLES = {"am": "☀️ 아침 정리", "pm": "🌙 저녁 정리"}
BLOG_LABELS = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜 블로그"}
NEWS_MAX = 8
# 기다리는 일 이름 (종류 → 문장 앞부분)
WAIT_LABELS = (
    (("link_request",), "링크를 기다리는 딜"),
    (("review_auth", "review_info"), "확인을 기다리는 딜"),
    (("publish_unsure",), "채널에 올라갔는지 확인할 딜"),
    (("photo_need",), "사진을 기다리는 딜"),
    (("draft_ready", "publish"), "발행을 기다리는 글"),  # 글 줄의 kind 는 할 일 이름 (state.NEED_ORDER) 이기도 하다
    (("ask", "answers"), "답을 기다리는 글"),
    (("pick_photos", "hot", "photos"), "사진을 기다리는 글"),
    (("upload_fail", "retry"), "업로드가 실패한 글"),
)
SEARCH_KIND = "search_reg"  # 할 일 '구글 검색 등록 N개' (dashboard/state.py — n = 아직 등록 안 한 글 수)
# Blog-Auto 기록 파일 digest.waiting 중 글 이야기 (할 일 목록이 이미 같은 글을 말함 — 두 번 말하지 않게 뺀다)
POST_SENTENCE = re.compile(r"(기다려요|실패한 채로)")
MARKS = re.compile(r"^(?:[⛔🔥ℹ🔔✅❌⚠📌\ufe0f]|\[[^\]]{0,8}\]|\s)+")  # 앞머리 표시 (⛔[멈춤] · ℹ️ …)
CMD_TAIL = re.compile(r"\s*[—-]\s*/[a-z_]+\s*로\s*")  # '— /kakao 로 다시 연결해 주세요' → 대시보드 말로


def plan_minutes(rng: random.Random | Any) -> dict[str, str]:
    out = {}
    for slot, (lo, hi) in WINDOWS.items():
        choices = [m for m in range(lo, hi + 1) if m % 30 != 0]
        m = rng.choice(choices)
        out[slot] = f"{m // 60:02d}:{m % 60:02d}"
    return out


def _names(titles: list[str]) -> str:
    shown = [truncate(" ".join(t.split()), 14) for t in titles[:2]]
    return ", ".join(shown) + (" 등" if len(titles) > 2 else "")


def waiting_sentences(items: list[dict[str, Any]]) -> list[str]:
    """할 일 목록 → '링크를 기다리는 딜 2개(무선청소기, 텀블러)가 있어요.'
    구글 검색 등록 줄 (주인 10/18 22:20)은 한 줄로 따로: '구글 검색 등록할 글 N개가 있어요.' (맨 뒤 — 급하지 않음)"""
    search = sum(int(it.get("n") or 0) for it in items if it.get("kind") == SEARCH_KIND)
    left = [it for it in items if it.get("kind") != SEARCH_KIND]
    out = []
    for kinds, label in WAIT_LABELS:
        mine = [it for it in left if it.get("kind") in kinds]
        if not mine:
            continue
        left = [it for it in left if it not in mine]
        out.append(f"{label} {len(mine)}개({_names([str(it.get('title') or '') for it in mine])})가 있어요.")
    if left:
        out.append(f"그 밖의 할 일 {len(left)}개({_names([str(it.get('title') or '') for it in left])})가 있어요.")
    if search > 0:
        out.append(f"구글 검색 등록할 글 {search}개가 있어요.")
    return out


def system_sentences(lines: Any) -> list[str]:
    """Blog-Auto digest.waiting → 글 이야기는 빼고 (로그인 풀림·자동 발행 대기열·카톡 연결 만료처럼 할 일 목록에 없는 것만),
    텔레그램 명령은 대시보드 말로."""
    out = []
    for s in lines if isinstance(lines, list) else []:
        if not isinstance(s, str) or not s.strip() or POST_SENTENCE.search(s):
            continue
        s = CMD_TAIL.sub(" — 대시보드 '연결'에서 ", s.strip())
        if re.search(r"/[a-z]", s):  # 다른 명령이 남았으면 그 문장은 뺀다 (대시보드에는 명령을 보이지 않음)
            continue
        out.append(s)
    return out[:6]


def news_sentence(text: str, n: int = 1) -> str:
    """알림 한 줄 → 문장 ('스레드 답글 3개' → '스레드 답글 3개 소식이 있었어요.'). 같은 종류가 여러 번이면 한 문장으로."""
    t = MARKS.sub("", text.strip()).strip().rstrip(".")
    if n > 1:
        return f"{t} 같은 소식이 {n}번 있었어요."
    return f"{t}." if t.endswith(("요", "다")) else f"{t} 소식이 있었어요."


def dealbot_sentences(entries: list[dict[str, Any]]) -> list[str]:
    """딜봇 요약 줄 (summary cat 들) → 문장. 'day' 가 있으면 그 문장이 오늘 정리 (owner_summary)."""
    day = [e["text"] for e in entries if e.get("cat") == "day" and e.get("text")]
    out: list[str] = []
    if day:
        out.append(day[-1])
    else:
        pub = list(dict.fromkeys(e["text"] for e in entries if e.get("cat") == "published" and e.get("text")))
        if pub:
            shown = "·".join(f"「{truncate(n, 16)}」" for n in pub[:2])
            out.append(f"주인이 링크를 넣은 딜 {len(pub)}개를 채널에 올렸어요 ({shown}{' 등' if len(pub) > 2 else ''}).")
    held = list(dict.fromkeys(e["text"] for e in entries if e.get("cat") == "held" and e.get("text")))
    if held:
        out.append(f"가격이 안 맞아 보류한 딜이 {len(held)}개 있어요 (「{truncate(held[0], 16)}」{' 등' if len(held) > 1 else ''}).")
    sold = list(dict.fromkeys(e["text"] for e in entries if e.get("cat") == "soldout" and e.get("text")))
    if sold:
        out.append(f"품절돼서 내린 딜이 {len(sold)}개예요 (「{truncate(sold[0], 16)}」{' 등' if len(sold) > 1 else ''}).")
    done = list(dict.fromkeys(e["text"] for e in entries if e.get("cat") == "link_done" and e.get("text")))
    if done and not day:
        out.append(f"링크를 넣은 딜이 {len(done)}개예요.")
    return out


class Digests:
    def __init__(self, db: Any, router: NotifyRouter, *, tz: str, export: Callable[[], Any] | None = None,
                 todo: Callable[[], list[dict[str, Any]]] | None = None, rng: random.Random | None = None) -> None:
        self.db = db
        self.router = router
        self.tz = tz
        self.export = export
        self.todo = todo
        self.rng = rng or random.SystemRandom()

    def _local(self, ts: float) -> datetime:
        return datetime.fromtimestamp(ts, ZoneInfo(self.tz))

    def plan(self, day: date) -> dict[str, str]:
        kv = f"digest_plan:{day.isoformat()}"
        try:
            got = json.loads(self.db.kv_get(kv) or "null")
        except ValueError:
            got = None
        if isinstance(got, dict) and all(isinstance(got.get(s), str) for s in WINDOWS):
            return got
        got = plan_minutes(self.rng)
        self.db.kv_set(kv, json.dumps(got))
        return got

    def slot_time(self, day: date, slot: str) -> datetime:
        hh, mm = (int(x) for x in self.plan(day)[slot].split(":"))
        return datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(self.tz))

    def due(self, now: float) -> tuple[date, str] | None:
        """지금 보낼 정리 (날, am|pm) — 이미 보냈거나 2시간 넘게 늦었으면 None (늦은 것은 건너뜀 표시)."""
        today = self._local(now).date()
        for day in (today - timedelta(days=1), today):
            for slot in WINDOWS:
                sent = f"digest_sent:{day.isoformat()}:{slot}"
                if self.db.kv_get(sent):
                    continue
                at = self.slot_time(day, slot).timestamp()
                if now < at:
                    continue
                if now - at > LATE_S:
                    self.db.kv_set(sent, "skipped")  # 너무 늦음 — 건너뛴다 (다음 정리가 이어서)
                    continue
                return day, slot
        return None

    def _since(self, now: float) -> float:
        last = self.db.kv_get("digest_last_at")
        try:
            return max(float(last), now - 36 * 3600) if last else now - 12 * 3600
        except ValueError:
            return now - 12 * 3600

    def build(self, day: date, slot: str, now: float) -> dict[str, Any]:
        since = self._since(now)
        sections: list[dict[str, Any]] = []
        export = self.export() if self.export is not None else None
        v2 = getattr(export, "v2", None) or {}
        dig = v2.get("digest") if isinstance(v2.get("digest"), dict) else {}
        # 1) 기다리는 일
        wait = waiting_sentences(self.todo() if self.todo is not None else [])
        wait += system_sentences(dig.get("waiting", []))
        if wait:
            sections.append({"label": "기다리는 일", "sentences": wait})
        # 2) 딜봇 (아침은 밤사이만)
        entries: list[dict[str, Any]] = []
        for d in (day - timedelta(days=1), day):
            entries += [e for e in self.router.summaries(d.isoformat()) if float(e.get("at") or 0) >= since]
        if slot == "am":
            entries = [e for e in entries if e.get("cat") != "day"]
        deal = dealbot_sentences(entries)
        if deal:
            sections.append({"label": "딜봇", "sentences": deal})
        # 3) 블로그마다 (저녁만)
        if slot == "pm" and isinstance(dig.get("blogs"), dict):
            for blog, label in BLOG_LABELS.items():
                lines = [s for s in dig["blogs"].get(blog, []) if isinstance(s, str) and s.strip()][:6]
                if lines:
                    sections.append({"label": label, "sentences": lines})
        # 4) 소식 (급하지 않은 알림)
        groups: dict[str, list[str]] = {}
        for n in self.router.store.since(since, now + 1, urgent=False):
            text = (n.short or n.title).strip()
            if text:
                groups.setdefault(n.kind or text, []).append(text)
        if groups:
            sections.append({"label": "소식", "sentences": [news_sentence(ts[0], len(ts)) for ts in list(groups.values())[:NEWS_MAX]]})
        did = iidmod.digest_iid(day, slot)
        lines = [s for sec in sections for s in sec["sentences"]]
        return {"id": did, "title": TITLES[slot], "at": now, "sections": sections, "lines": lines}

    def kakao_memo(self, day: date, d: dict[str, Any], todo_n: int) -> str:
        head = f"🌙 {day.month}/{day.day} 정리 — " + (f"할 일 {todo_n}개 남았어요." if todo_n else "남은 할 일은 없어요.")
        body = " ".join(d["lines"][:2])
        return fit(f"{head} {body} 자세한 건 대시보드에서 볼 수 있어요.".replace("  ", " "))

    async def tick(self, now: float | None = None) -> str | None:
        """1분마다: 차례가 된 정리를 만들어 보낸다. 보낸 정리 id (없으면 None)."""
        now = self.router.clock() if now is None else now
        due = self.due(now)
        if due is None:
            return None
        day, slot = due
        self.db.kv_set(f"digest_sent:{day.isoformat()}:{slot}", "sending")  # 먼저 표시 (만들다 죽어도 두 번 보내지 않게)
        d = self.build(day, slot, now)
        self.router.store.save_digest(d["id"], at=now, title=d["title"], lines=d["lines"], sections=d["sections"])
        self.db.kv_set("digest_last_at", str(now))
        if d["lines"]:
            body = ""
            for s in d["lines"][:3]:
                if len(body) + len(s) + 1 > 120:
                    break
                body = f"{body} {s}".strip()
            sent = await self.router.push_digest(d["id"], d["title"], body or truncate(d["lines"][0], 120))
            self.router.store.mark_digest(d["id"], pushed=sent > 0)
        if slot == "pm":
            todo_n = len(self.todo()) if self.todo is not None else 0
            ok = self.router.kakao.send("daily", key=f"digest:{day:%Y%m%d}", text=self.kakao_memo(day, d, todo_n),
                                        link=self.router.link(d["id"]))
            self.router.store.mark_digest(d["id"], kakao=ok)
        self.db.kv_set(f"digest_sent:{day.isoformat()}:{slot}", "sent")
        log.info("digest %s: %d문장", d["id"], len(d["lines"]))
        return d["id"]
