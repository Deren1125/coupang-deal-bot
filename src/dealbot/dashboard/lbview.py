"""룩북 릴스 화면 값 (주인 10/25 — 'AI 룩북 릴스: 실제 지그재그 상품 → 얼굴 없는 AI 모델 사진 → 영상 → 내가 새 인스타 계정에 올림').

state['lookbook'] = 블로그 탭 '룩북 릴스 →' 화면(static/lookbook.js)이 그대로 그리는 값. 새로 받는 것 없음 — Blog-Auto 기록 파일 lookbook 칸만
(blogauto._lookbook 로 걸러 둔 것).
- 편마다: 주제·훅 · 쉬운 말 상태(서버 준비 중 / 사진 만드는 중 / 완성 / 상품과 달라 다시 / 실패 / 올렸어요) · 영상 미리보기와 표지
  (대시보드가 /dashboard/lookbook/<id>/video.mp4 · cover.jpg 로 내보냄 — api.py) · 캡션 복사 · 룩 목록(Look N · 상품 · 가격 · 링크) ·
  올리기 전 확인(인스타 'AI 정보' 켜기 · 유행 음악 고르기 · 캡션 붙여넣기) · 올린 릴스 주소 → 다리 일 blog.lookbook_posted {id, url} ·
  버리기(blog.lookbook_drop) · 다시 만들기(blog.lookbook_redo). 확인 창 없음 (서버에서 밖으로 나가는 것이 없음 — 올리기는 주인 손).
- 하지 않는 것: 인스타그램 열기·읽기 · 자동 게시 · 자동 좋아요·댓글·팔로우. 쇼츠 서버 주소·토큰은 화면에 오지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

WORDS = {"new": "서버 준비 중", "waiting_server": "서버 준비 중", "submitted": "사진 만드는 중", "rendering": "사진 만드는 중",
         "ready": "완성", "failed_match": "상품과 달라 다시", "failed": "실패", "expired": "실패", "posted": "올렸어요"}
TONES = {"ready": "ok", "posted": "done", "failed": "bad", "failed_match": "bad", "expired": "bad"}  # 나머지는 wait (기다리는 중)
REASONS = {
    "no_token": "쇼츠 서버 열쇠(토큰)를 아직 안 넣었어요 — 넣으면 저절로 시작해요",
    "not_found": "쇼츠 서버가 아직 준비 안 됐어요 — 저절로 다시 보내요",
    "unreachable": "쇼츠 서버에 닿지 않아요 — 저절로 다시 보내요",
    "auth": "쇼츠 서버 열쇠(토큰)가 맞지 않아요",
    "bad_reply": "쇼츠 서버 답이 이상해요 — 저절로 다시 해요",
    "looks_left": "사진 속 옷이 상품과 달라 뺐더니 4벌이 안 남았어요",
    "bad_file": "받은 영상·사진 파일이 이상해요",
    "timeout": "하루 안에 다 만들지 못했어요",
    "too_old": "이틀 동안 서버에 못 보내 상품 정보가 낡았어요",
    "owner_redo": "다시 만드는 중이에요",
    "server": "쇼츠 서버가 만들지 못했어요",
}
WARNS = {"video_has_dropped_look": "영상에 상품과 다른 룩이 남아 있어요 — 다시 만들기를 권해요",
         "unscored": "상품과 같은지 채점을 못 했어요 — 영상을 한 번 보고 올려 주세요"}
ABLY = {"queued": "에이블리 챌린지에 낼 차례예요", "put": "에이블리 챌린지에 냈어요", "start": "에이블리 챌린지에 냈어요",
        "skip": "이번 회차엔 이미 다른 글을 냈어요", "none": "지금 열린 에이블리 챌린지가 없어요",
        "login": "에이블리 로그인이 필요해요 — 앱에서 직접 내 주세요", "failed": "에이블리 제출이 안 됐어요 — 앱에서 직접 내 주세요",
        "no_link": "에이블리 링크가 없어 챌린지엔 안 내요"}
MALLS = {"zigzag": "지그재그", "ably": "에이블리"}
CHECKLIST = ("인스타 'AI 정보' 켜기 (고급 설정 → AI 레이블)", "유행 음악 고르기", "캡션 붙여넣기")
RULES = "AI 가상인물 영상이에요 — 올릴 때 인스타 'AI 정보'를 꼭 켜 주세요 · 캡션 첫 줄 #광고 는 지우지 마세요"
DROPPABLE = ("new", "waiting_server", "submitted", "rendering", "ready", "failed", "failed_match", "expired")
REDOABLE = ("ready", "failed", "failed_match")  # Blog-Auto lookbook_jobs.redo_all 이 받는 것 중 주인이 누를 만한 것
MEDIA = "/dashboard/lookbook/"  # api.py blog_lookbook — blog_view 사본, 없으면 Blog-Auto var/lookbook/<id>/ 만
THUMB = "/dashboard/thumb/"


def _reason(code: str) -> str:
    if code.startswith("http_"):
        return "쇼츠 서버가 잠시 안 돼요 — 저절로 다시 보내요"
    return REASONS.get(code, "")


def _when(iso: str | None, tz: ZoneInfo) -> str:
    """'10/26 07:12' (모르면 '')."""
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    d = d.astimezone(tz) if d.tzinfo else d
    return f"{d.month}/{d.day} {d.hour:02d}:{d.minute:02d}"


def _won(n: int) -> str:
    return f"{n:,}원" if n else ""


def _posted_row(x: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """올린 편은 한 줄만 (화면 첫 값을 작게 — 캡션·룩은 안 실음)."""
    return {"id": x["id"], "theme": x["theme"], "status": "posted", "word": WORDS["posted"], "created": _when(x["created_at"], tz),
            "posted_url": x["posted_url"] or "", "ably": ABLY.get(x["ably"] or "", "")}


def _row(x: dict[str, Any], tz: ZoneInfo, act: Callable[..., dict[str, Any]], bridge_ok: bool) -> dict[str, Any]:
    jid, st = x["id"], x["status"]
    off = not bridge_ok or None
    looks = [{"n": lk["n"], "name": lk["name"], "price": _won(lk["price"]), "link": lk["link"], "mall": MALLS.get(lk["mall"], ""),
              "thumb": THUMB + lk["thumb"] if lk["thumb"] else "", "dropped": lk["dropped"]} for lk in x["looks"]]
    note = _reason(x["reason"]) if st != "ready" else ""
    if st == "waiting_server" and not note:
        note = "쇼츠 서버를 기다리는 중이에요"
    args = {"id": jid}
    return {
        "id": jid, "theme": x["theme"], "hook": x["hook"].replace(" / ", "\n"), "status": st, "word": WORDS.get(st, st),
        "tone": TONES.get(st, "wait"), "note": note, "warn": WARNS.get(x["warn"] or "", ""), "created": _when(x["created_at"], tz),
        "video": f"{MEDIA}{jid}/video.mp4" if x["has_video"] else "", "cover": f"{MEDIA}{jid}/cover.jpg" if x["has_cover"] else "",
        "save_name": f"{jid}.mp4", "cover_name": f"{jid}-cover.jpg",
        "caption": x["caption"], "looks": looks,
        **({"posted_act": act("blog.lookbook_posted", "올렸어요", args, input="url", disabled=off)} if st == "ready" else {}),
        **({"drop": act("blog.lookbook_drop", "버리기", args, disabled=off)} if st in DROPPABLE else {}),
        **({"redo": act("blog.lookbook_redo", "다시 만들기", args, disabled=off)} if st in REDOABLE else {}),
    }


def build(lv: dict[str, Any] | None, tz: ZoneInfo, now: float, act: Callable[..., dict[str, Any]], *,
          bridge_ok: bool = True) -> dict[str, Any]:
    """화면 값. lv = blogauto._lookbook 로 걸러 둔 기록 파일 lookbook 칸 (예전 Blog-Auto 면 None → 일감 없음).
    items = 올리기 전 편 (새 것부터 · 버린 편은 뺌), posted = 올린 편. entry = 블로그 탭 입구 단추 글."""
    items, posted = [], []
    for x in (lv or {}).get("items") or []:
        if x["status"] == "dropped":
            continue
        if x["status"] == "posted":
            posted.append(_posted_row(x, tz))
        else:
            items.append(_row(x, tz, act, bridge_ok))
    ready = sum(it["status"] == "ready" for it in items)
    if lv is None:
        empty = "블로그 프로그램이 아직 룩북 릴스를 안 보내요 — 업데이트를 기다리는 중이에요"
    elif not lv["enabled"]:
        empty = "룩북 릴스 만들기가 꺼져 있어요 (블로그 프로그램 설정 lookbook.enabled)"
    else:
        empty = f"아직 만든 릴스가 없어요 — 하루 {lv['per_day'] or 1}편, 아침 6~8시 사이에 만들기 시작해요"
    server = ""
    if lv is not None and lv["server"] == "no_token":
        server = "쇼츠 서버 열쇠(토큰)가 아직 없어요 — 넣기 전엔 '서버 준비 중'에서 멈춰 있어요"
    ig = (lv or {}).get("instagram")
    return {
        "ready": lv is not None, "bridge_ok": bridge_ok, "enabled": bool(lv and lv["enabled"]), "server": server,
        "instagram": f"@{ig}" if ig else "", "items": items, "posted": posted, "ready_n": ready,
        "entry": f"룩북 릴스 · 완성 {ready}편 →" if ready else "룩북 릴스 →",
        "checklist": list(CHECKLIST), "rules": RULES, "empty": empty,
    }


TODO_PREFIX = "lookbook-"


def todo_item(lv: dict[str, Any] | None, tz: ZoneInfo, goto: Callable[..., dict[str, Any]]) -> dict[str, Any] | None:
    """할 일 한 줄 '룩북 릴스 올리기' — 완성(ready)됐는데 아직 안 올린 편이 있을 때 (가장 새 편 이름으로 — 숨겨도 다음 편이 완성되면 다시 뜸).
    올렸어요·버리기를 누르면(또는 다른 상태가 되면) 저절로 빠짐."""
    ready = [x for x in (lv or {}).get("items") or [] if x["status"] == "ready"]
    if not ready:
        return None
    x = ready[0]  # 새 것부터 (Blog-Auto 순서)
    since = None
    if x["created_at"]:
        try:
            d = datetime.fromisoformat(x["created_at"].replace("Z", "+00:00"))
            since = (d if d.tzinfo else d.replace(tzinfo=tz)).timestamp()
        except ValueError:
            since = None
    more = f" · {len(ready)}편" if len(ready) > 1 else ""
    return {"iid": TODO_PREFIX + x["id"], "src": "blog", "blog": "", "kind": "lookbook", "sev": "todo", "urgent": False,
            "title": f"룩북 릴스 올리기{more}",
            "status": f"{x['theme'] or '오늘 릴스'} · 영상 저장 → 인스타 앱에서 유행 음악 · 캡션 붙여넣기 · 'AI 정보' 켜기",
            "since": since, "expires_at": None, "primary": goto("보기", "#/blog/lookbook"), "more": [], "detail": {"lines": []}}
