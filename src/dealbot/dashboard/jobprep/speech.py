"""⑦ 스피치·인문학.

스피치 카드 (주 3장): 지미 카 공식 채널 공개 영상의 **자막만** (영상 내려받기 없음 — yt-dlp --skip-download --write-auto-subs json3).
Gemini good 이 자막에서 농담 3개 {setup, twist, t_start} 를 고르고, 쉼(초)은 코드가 낱말 시각의 빈틈으로 잰다. 카드에는 짧은 인용 + youtu.be 링크만.
유튜브가 서버를 막으면 그 주는 쉬고 한 줄 ('영상 주소 붙여 넣기' 로 주인이 고른 영상 자막을 시도). 프록시·로그인 없음.
따라 말하기: 녹음 → audio.measure → 말 빠르기·twist 앞 쉼·쉼 수를 원본과 나란히. AI 없음.
인문학: 하루 1주제 (주 1번 7개 묶음 · 3분 읽기 900~1100자) + '30초로 설명' (받아쓴 글이 있으면 Gemini fast 가 한 번 봐 줌).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard.jobprep import audio
from dealbot.dashboard.jobprep.store import JpError, clip

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
VID_RE = re.compile(r"(?:youtu\.be/|v=|/shorts/)([A-Za-z0-9_-]{11})")
JOKES_SCHEMA = {"type": "object", "properties": {"jokes": {"type": "array", "items": {"type": "object", "properties": {
    "setup": {"type": "string"}, "twist": {"type": "string"}, "t_start": {"type": "number"}}, "required": ["setup", "twist", "t_start"]}}},
    "required": ["jokes"]}
HUMAN_SCHEMA = {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "object", "properties": {
    "title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"]}}}, "required": ["topics"]}


def week_key(ts: float) -> str:
    d = datetime.fromtimestamp(ts, KST).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def video_id(url: str) -> str | None:
    m = VID_RE.search(url or "")
    return m.group(1) if m else (url if re.fullmatch(r"[A-Za-z0-9_-]{11}", url or "") else None)


async def _ytdlp(*args: str, timeout: float = 90) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec("nice", "-n", "19", sys.executable, "-m", "yt_dlp", "--no-cookies", "--no-warnings", *args,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        o, e = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, ""
    if proc.returncode and b"No module named yt_dlp" in e:
        return -2, ""
    return proc.returncode or 0, o.decode("utf-8", "replace")


async def recent_videos(url: str) -> list[str]:
    code, out = await _ytdlp("--flat-playlist", "-J", "--playlist-end", "12", url)
    if code != 0:
        raise JpError(503, "yt_blocked" if code != -2 else "no_ytdlp", "유튜브 목록을 읽지 못했어요 (이번 주는 쉼)" if code != -2 else "yt-dlp 가 설치되지 않았어요")
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return [str(e.get("id")) for e in data.get("entries") or [] if isinstance(e, dict) and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(e.get("id") or ""))]


async def subtitle_words(vid: str) -> list[dict[str, Any]]:
    tmp = Path(tempfile.mkdtemp(prefix="jpyt-"))
    try:
        code, _o = await _ytdlp("--skip-download", "--write-auto-subs", "--write-subs", "--sub-langs", "en.*,en", "--sub-format", "json3",
                                "-o", str(tmp / "%(id)s.%(ext)s"), f"https://www.youtube.com/watch?v={vid}")
        files = sorted(tmp.glob("*.json3"))
        if code != 0 or not files:
            raise JpError(503, "yt_blocked", "자막을 받지 못했어요 (유튜브가 막았거나 자막이 없음)")
        return audio.json3_words(json.loads(files[0].read_text(encoding="utf-8")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def transcript(words: list[dict[str, Any]], limit: int = 30000) -> str:
    out, last = [], -10.0
    for w in words:
        if w["t"] - last >= 5:
            out.append(f"\n[{w['t']:.0f}]")
            last = w["t"]
        out.append(w["w"])
    return " ".join(out)[:limit]


async def make_cards(jp: Any, vid: str, *, n: int = 3) -> list[int]:
    words = await subtitle_words(vid)
    if len(words) < 50:
        raise JpError(422, "short", "자막이 너무 짧아요")
    out = await jp.llm.gemini(f"speech.jokes:{vid}", "good", "너는 스탠드업 코미디 코치다. 자막에서 짧고 구조가 분명한 농담만 고른다.",
                              "자막 ([초] 표시):\n" + transcript(words) + f"\n\n농담 {n}개: setup(앞부분 원문 그대로), twist(반전 원문 그대로), t_start(setup 시작 초).",
                              schema=JOKES_SCHEMA)
    ids = []
    wk = week_key(jp.clock())
    for j in (out.get("jokes") or [])[:n]:
        t0 = float(j.get("t_start") or 0)
        first = (str(j.get("twist") or "").split() or [""])[0]
        pause = audio.pause_before(words, first, t0)
        span = audio.word_pauses(words, t0, t0 + 25)
        quote = (clip(j.get("setup"), 200) + " … " + clip(j.get("twist"), 160)).strip()
        ids.append(jp.store.x("INSERT INTO speech_cards (week, video_id, setup, twist, t_start, pause, wps, quote, at) VALUES (?,?,?,?,?,?,?,?,?)",
                              (wk, vid, clip(j.get("setup"), 400), clip(j.get("twist"), 300), t0, pause, span["wps"], quote, jp.clock())))
    return ids


async def weekly(jp: Any) -> dict[str, Any]:
    wk = week_key(jp.clock())
    if jp.store.one("SELECT id FROM speech_cards WHERE week=?", (wk,)):
        return {"skip": "already"}
    try:
        vids = await recent_videos(jp.cfg().speech_channel_url)
        used = {r["video_id"] for r in jp.store.q("SELECT DISTINCT video_id FROM speech_cards")}
        vid = next((v for v in vids if v not in used), None)
        if vid is None:
            return {"skip": "no_video"}
        ids = await make_cards(jp, vid)
        jp.store.kv_set("speech_last", {"at": jp.clock(), "ok": True, "n": len(ids)})
        return {"made": len(ids)}
    except JpError as e:
        jp.store.kv_set("speech_last", {"at": jp.clock(), "ok": False, "why": e.text})
        return {"skip": e.code}


async def from_url(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    vid = video_id(clip(body.get("url"), 200))
    if not vid:
        raise JpError(400, "bad_url", "유튜브 주소를 넣어 주세요")
    ids = await make_cards(jp, vid)
    return {"made": len(ids)}


def view(jp: Any) -> dict[str, Any]:
    cards = jp.store.q("SELECT * FROM speech_cards ORDER BY at DESC LIMIT 9")
    for c in cards:
        c["link"] = f"https://youtu.be/{c['video_id']}?t={int(c['t_start'] or 0)}"
        c["takes"] = [{"id": t["id"], "at": t["at"], "result": json.loads(t["result"] or "{}")}
                      for t in jp.store.q("SELECT * FROM speech_takes WHERE card_id=? ORDER BY at DESC LIMIT 5", (c["id"],))]
    return {"cards": cards, "last": jp.store.kv_get("speech_last", {}), "human": human_today(jp)}


async def take(jp: Any, card_id: int, wav: Path, fname: str) -> dict[str, Any]:
    c = jp.store.one("SELECT * FROM speech_cards WHERE id=?", (card_id,))
    if c is None:
        raise JpError(404, "gone", "그 카드가 없어요")
    words = len((c["setup"] + " " + c["twist"]).split())
    m = await audio.measure(wav, words)
    if m is None:
        raise JpError(422, "audio", "녹음을 읽지 못했어요")
    ref = {"wps": c["wps"], "pauses": []}
    res = {**m, "ref_wps": c["wps"], "ref_pause": c["pause"], "cmp": audio.compare(m, ref),
           "twist_pause_mine": max((p["len"] for p in m["pauses"]), default=0.0)}
    tid = jp.store.x("INSERT INTO speech_takes (card_id, file, at, result) VALUES (?,?,?,?)", (card_id, fname, jp.clock(), json.dumps(res)))
    return {"id": tid, "result": res}


# ── 인문학 ──
async def human_week(jp: Any) -> int:
    t = datetime.fromtimestamp(jp.clock(), KST).date()
    wk = (t - timedelta(days=t.weekday())).isoformat()
    if jp.store.one("SELECT day FROM human_topics WHERE week=?", (wk,)):
        return 0
    seen = [r["title"] for r in jp.store.q("SELECT title FROM human_topics ORDER BY day DESC LIMIT 40")]
    out = await jp.llm.gemini(f"human.week:{wk}", "good", "너는 교양 강의 작가다. 대학생이 3분에 읽을 한국어 글을 쓴다. 사실만, 출처가 분명한 내용만.",
                              "인문학·역사·철학·경제사·과학사 주제 7개. 각 title 과 body(900~1100자, 이야기처럼 · 마지막 줄은 '30초로 말해 보기' 질문 하나). "
                              f"이미 다룬 것 피함: {', '.join(seen)}", schema=HUMAN_SCHEMA, max_tokens=12000)
    n = 0
    for i, tp in enumerate((out.get("topics") or [])[:7]):
        day = (t - timedelta(days=t.weekday()) + timedelta(days=i)).isoformat()
        jp.store.x("INSERT OR REPLACE INTO human_topics (day, title, body, week) VALUES (?,?,?,?)", (day, clip(tp.get("title"), 120), clip(tp.get("body"), 3000), wk))
        n += 1
    return n


def human_today(jp: Any) -> dict[str, Any] | None:
    day = datetime.fromtimestamp(jp.clock(), KST).strftime("%Y-%m-%d")
    r = jp.store.one("SELECT * FROM human_topics WHERE day<=? ORDER BY day DESC LIMIT 1", (day,))
    if r is None:
        return None
    takes = jp.store.q("SELECT id, said, review, at FROM human_takes WHERE day=? ORDER BY at DESC LIMIT 3", (r["day"],))
    return {**r, "takes": takes}


async def human_say(jp: Any, body: dict[str, Any]) -> dict[str, Any]:
    said = clip(body.get("text"), 3000)
    h = human_today(jp)
    if h is None:
        raise JpError(404, "none", "오늘 주제가 아직 없어요")
    review = ""
    if said:
        review = await jp.llm.gemini("human.say", "fast", "너는 말하기 코치다. 한국어로 3줄: 빠진 핵심 · 군말 · 더 짧게 말하는 법.",
                                     f"주제 글:\n{h['body'][:2500]}\n\n내가 30초로 말한 것(받아쓰기):\n{said}")
    else:
        review = "받아쓰기가 없어요 — 스스로 점검: ① 핵심 한 문장 ② 예 하나 ③ 30초 안에 끝났나"
    jp.store.x("INSERT INTO human_takes (day, file, said, review, at) VALUES (?,?,?,?,?)", (h["day"], clip(body.get("file"), 80), said, review, jp.clock()))
    return {"review": review}
