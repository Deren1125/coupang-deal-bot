"""'오늘 한 일' (주인 10/18 — "오늘 내가 발행하도록 허가한 글 모음은 어디서 봐? 사진·링크 첨부한 거라거나").

GET /dashboard/api/today[?day=yesterday] → {"day","count","groups":[{"key","label","rows":[Row]}]} — 시트를 열 때만 (state 는 개수만).
  Row = {"title","at","when","what","res": ok|run|bad,"chip","why"?,"note"?,"blog"?,"link"?: {"label","url"}}

- 오늘(설정 시간대 0시부터) 대시보드에서 주인이 한 일: dash_actions (딜봇에서 바로 한 일 + Blog-Auto 에 맡긴 일) +
  맡기기 전에 막힌 일 (dash_audit 의 action_id 없는 실패 줄 — 블로그 프로그램 응답 없음·하는 중 같은 것)
- 제목은 대기열(딜 이름)·기록 파일(글 제목)에서, 없으면 일 이름. 글자는 이미 저장된 안전한 글(safe_text)만 —
  args 원문·토큰·코드·내부 번호(qid·pid·action id·upload id)는 내보내지 않는다 (args 는 분류·개수에만 씀)
- 링크는 정해진 곳에서만: 발행한 글 = 기록 파일 posts[].naver_url · recent[].url (네이버 블로그 주소만),
  딜 = 채널 글 (설정의 channel_post_base + 올린 메시지 — 딜 탭 '채널 글'과 같게, 연습 모드 제외) 또는 그 딜의 상품 페이지 (link_target — https · 텔레그램 아님)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dealbot.dashboard.actions import BLOG_NAMES, TEXTS
from dealbot.dashboard.model import https_url, safe_text
from dealbot.dashboard.todo import link_target
from dealbot.notify import iid as iidmod

GROUPS = (("link", "링크 넣음"), ("photo", "사진 올림"), ("publish", "발행·예약"), ("answer", "답함·고침"),
          ("drop", "버림·안 올림"), ("other", "기타"))
GROUP_OF = {
    "deal.link": "link",
    "deal.late_photo": "photo", "blog.photos": "photo", "blog.photo_clear": "photo",
    "blog.publish": "publish", "blog.schedule": "publish", "blog.unschedule": "publish", "blog.unschedule_all": "publish",
    "blog.schedule_move": "publish",
    "blog.retry": "publish", "blog.channel": "publish", "blog.share": "publish", "deal.ok": "publish", "deal.ok_text": "publish",
    "deal.repost": "publish", "deal.posted": "publish", "deal.post": "publish",
    "blog.answer": "answer", "blog.noanswer": "answer", "blog.fix": "answer", "blog.fixpic": "answer", "blog.rewrite": "answer",
    "blog.restyle": "answer", "blog.oedit_keep": "answer", "blog.oedit_redo": "answer",
    "blog.drop": "drop", "deal.skip": "drop", "blog.noshare": "drop",
}
# 보기만 하거나 연결·시험인 일 — 한 일 목록에 넣지 않음
SKIP = frozenset({"blog.info", "stats.refresh", "push.test", "push.subscribe", "push.unsubscribe", "blog.login_check",
                  "blog.kakao_auth_url", "blog.kakao_connect", "blog.kakao_test", "sns.token", "sns.code", "upload", "login",
                  "logout", "state", "connect", "blog.dismiss"})  # blog.dismiss: todo.dismiss 가 맡긴 것 (두 번 세지 않게)
LABELS = {
    "deal.link": "링크 넣기", "deal.late_photo": "사진 올리기", "blog.photos": "사진 올리기", "blog.photo_clear": "사진 자리 비우기",
    "blog.publish": "발행", "blog.schedule": "예약", "blog.unschedule": "예약 취소", "blog.unschedule_all": "예약 모두 취소",
    "blog.schedule_move": "예약 시간 바꾸기",
    "blog.retry": "다시 올리기", "blog.channel": "채널에 올리기", "blog.share": "보내기", "deal.ok": "올리기",
    "deal.ok_text": "고쳐서 올리기", "deal.repost": "다시 올리기", "deal.posted": "채널에 있음", "deal.post": "새 딜 올리기",
    "blog.answer": "답하기", "blog.noanswer": "답 없이 진행", "blog.fix": "말로 고치기", "blog.fixpic": "사진도 바꾸기",
    "blog.rewrite": "다시 쓰기", "blog.restyle": "스타일 다시", "blog.oedit_keep": "그대로 두기", "blog.oedit_redo": "봇 글로 덮어쓰기",
    "blog.drop": "버리기", "deal.skip": "안 올리기", "blog.noshare": "보내지 않기", "deal.nolink": "링크 없이",
    "deal.sc_forget": "판매처 빼기", "bot.pause": "딜봇 멈춤", "bot.resume": "딜봇 다시 시작", "bot.run": "지금 수집",
    "sns.test": "테스트 글", "sns.invites": "초대 링크 만들기", "todo.dismiss": "다 했어요", "stats.input": "통계 직접 넣기",
    "blog.scan": "글감 찾기", "blog.pick": "글감으로 쓰기", "blog.write": "새 글 쓰기", "blog.issue": "키워드로 쓰기",
    "blog.hotdeal_now": "한 편 쓰기", "blog.mode": "모드 바꾸기", "blog.pause": "블로그 멈춤", "blog.resume": "블로그 다시 시작",
    "blog.drafts_cleanup": "임시저장 정리", "blog.ledger": "정리본 바꾸기", "blog.dismiss": "다 했어요", "blog.visit_done": "이웃 글 들름",
    "search.check": "검색 등록",
}
CHIP = {"ok": "완료", "run": "진행 중", "bad": "실패"}
OPEN = ("queued", "running")
BAD = ("error", "rejected", "expired")
WHY_MAX = 40
NOTE_MAX = 60
TITLE_MAX = 40
NAVER_BLOG = ("blog.naver.com", "m.blog.naver.com")
LIMIT = 300


def group_of(action: str, args: dict[str, Any]) -> str | None:
    """그 일이 들어갈 칸 (None = 목록에 넣지 않음)."""
    if action in SKIP or not action:
        return None
    if action == "deal.nolink":
        return "publish" if args.get("name") == "post" else "drop"
    return GROUP_OF.get(action, "other")


def day_start(now: float, tz: str, back: int = 0) -> float:
    d = datetime.fromtimestamp(now, ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=back)
    return d.timestamp()


def naver_url(url: Any) -> str | None:
    u = link_target(url)
    return u if u is not None and (urlsplit(u).hostname or "").lower() in NAVER_BLOG else None


def _json(raw: Any) -> dict[str, Any]:
    try:
        v = json.loads(raw or "null")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _short(text: Any, limit: int) -> str:
    return safe_text(" ".join(str(text or "").split()), limit) if text else ""


def raw_rows(db: Any, start: float, end: float) -> list[dict[str, Any]]:
    """그날 한 일 줄 (분류 전): dash_actions + 맡기기 전에 막힌 dash_audit 줄."""
    out = []
    for r in db._q("SELECT ts, action, target, args, status, result FROM dash_actions WHERE ts >= ? AND ts < ? "
                   "ORDER BY ts DESC LIMIT ?", (start, end, LIMIT)):
        out.append({"ts": r["ts"], "action": r["action"], "target": r["target"] or "", "args": _json(r["args"]),
                    "status": r["status"], "result": _json(r["result"])})
    for r in db._q("SELECT ts, action, target, args_redacted, result_code FROM dash_audit WHERE ts >= ? AND ts < ? "
                   "AND action_id IS NULL AND result_code NOT IN ('queued', 'done', 'redirect') ORDER BY ts DESC LIMIT ?",
                   (start, end, LIMIT)):
        out.append({"ts": r["ts"], "action": r["action"], "target": r["target"] or "", "args": _json(r["args_redacted"]),
                    "status": "error", "result": {"code": r["result_code"] or "failed", "text": ""}})
    return [r for r in out if group_of(r["action"], r["args"]) is not None]


def count(bot: Any, *, now: float) -> int:
    tz = bot.settings.app.timezone
    return len(raw_rows(bot.db, day_start(now, tz), now + 60))


class TodayView:
    """bot = app.DealBot. 바깥 요청 없음 — 봇 DB·기록 파일만."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def _export_v2(self) -> dict[str, Any]:
        st = getattr(self.bot, "dash_state", None)
        try:
            export = st._export() if st is not None else None
        except Exception:  # noqa: BLE001
            export = None
        return getattr(export, "v2", None) or {}

    # ---------------------------------------------------------- 제목·링크
    def _post(self, v2: dict[str, Any], blog: str, pid: Any) -> tuple[str, str | None]:
        """(제목, 네이버 글 주소) — 기록 파일 posts[] 다음 recent[]."""
        title, url = "", None
        for p in (v2.get("posts") or {}).get(blog) or []:
            if isinstance(p, dict) and p.get("pid") == pid:
                title, url = str(p.get("title") or ""), naver_url(p.get("naver_url"))
                break
        for r in (v2.get("recent") or {}).get(blog) or []:
            if isinstance(r, dict) and r.get("pid") == pid:
                title = title or str(r.get("title") or "")
                url = url or naver_url(r.get("url"))
                break
        return _short(title, TITLE_MAX), url

    def _deal(self, qid: int, group: str) -> tuple[str, dict[str, str] | None]:
        """(딜 이름, 링크) — 채널 글이 있으면 그것, 없으면 링크·사진 칸만 상품 페이지."""
        from dealbot.dashboard.state import _name

        it = self.bot.db.get_queue_item(qid)
        if it is None:
            return "", None
        p = it.deal.product
        if group == "drop":
            return _name(p.name), None
        post = self.bot.db.latest_post(p.product_id) or {}
        base = getattr(self.bot.notifier, "channel_post_base", None)
        chan = https_url(f"{base}/{post['message_id']}") if base and post.get("message_id") and not post.get("dry_run") else None
        if chan:
            return _name(p.name), {"label": "채널 글", "url": chan}
        prod = link_target(p.url) if group in ("link", "photo") else None
        return _name(p.name), ({"label": "상품", "url": prod} if prod else None)

    def _visit_title(self, v2: dict[str, Any], vid: str) -> str:
        for v in v2.get("visits") or []:
            if isinstance(v, dict) and v.get("id") == vid:
                return _short(v.get("title"), TITLE_MAX)
        return ""

    # ---------------------------------------------------------- 한 줄
    def row(self, r: dict[str, Any], v2: dict[str, Any], tz: str) -> dict[str, Any]:
        action, target, args, res = r["action"], r["target"], r["args"], r["result"]
        group = group_of(action, args) or "other"
        status = r["status"]
        kind = "ok" if status == "ok" else "run" if status in OPEN else "bad" if status in BAD else "run"
        what = LABELS.get(action, "대시보드 일")
        out: dict[str, Any] = {"title": "", "at": r["ts"], "when": datetime.fromtimestamp(r["ts"], ZoneInfo(tz)).strftime("%H:%M"),
                               "what": what, "res": kind, "chip": "확인 필요" if status == "unknown" else CHIP[kind]}
        if action == "deal.link" and args.get("upload_id"):
            out["what"] = "링크 넣기 · 사진 같이"
        ups = args.get("uploads")
        if action == "blog.photos" and isinstance(ups, list) and ups:
            out["what"] = f"사진 {len(ups)}장 올리기"
        title, link = "", None
        if (qid := iidmod.deal_of(target)) is not None:
            title, link = self._deal(qid, group)
        else:
            post = iidmod.post_of(target)
            rp = res.get("post") if isinstance(res.get("post"), dict) else {}
            if post is None and rp.get("blog") in BLOG_NAMES and isinstance(rp.get("pid"), int):
                post = (rp["blog"], rp["pid"])  # 새 글 쓰기처럼 결과에서 글이 정해진 일
            pids = args.get("pids") if isinstance(args.get("pids"), list) else []
            if post is None and args.get("blog") in BLOG_NAMES and pids and isinstance(pids[0], int):
                post = (args["blog"], pids[0])
            if post is not None:
                out["blog"] = BLOG_NAMES[post[0]]
                title, url = self._post(v2, *post)
                if len(pids) > 1:
                    title = f"{title or '글'} 외 {len(pids) - 1}편"
                if url and kind == "ok" and action in ("blog.publish", "blog.channel", "blog.share", "blog.retry"):
                    link = {"label": "글", "url": url}
            elif target.startswith("blog-") and target.split("-")[1] in BLOG_NAMES:
                out["blog"] = BLOG_NAMES[target.split("-")[1]]
            elif action == "blog.visit_done":
                title = self._visit_title(v2, target)
        out["title"] = title or what
        text = _short(str(res.get("text") or "").split(" — ")[0], NOTE_MAX)  # 앞 마디만 ('… — 다시 눌러 주세요' 같은 꼬리는 뺌)
        if kind == "bad":
            code = str(res.get("code") or "")
            why = text or ("시간이 지나 취소됐어요" if status == "expired" or code == "expired" else
                           TEXTS.get(code, TEXTS["failed"]).split(" — ")[0])
            out["why"] = _short(why, WHY_MAX)
        elif text and group == "publish" and text != "하는 중" and not text.startswith(out["what"]):  # 예약 시각처럼 결과에만 있는 것 (다른 칸은 '완료' 로 충분)
            out["note"] = text
        if link:
            out["link"] = link
        return out

    def view(self, *, now: float, back: int = 0) -> dict[str, Any]:
        tz = self.bot.settings.app.timezone
        start = day_start(now, tz, back)
        end = day_start(now, tz, back - 1) if back else now + 60
        v2 = self._export_v2()
        groups: dict[str, list[dict[str, Any]]] = {k: [] for k, _ in GROUPS}
        rows = sorted(raw_rows(self.bot.db, start, end), key=lambda x: x["ts"], reverse=True)
        for r in rows:
            groups[group_of(r["action"], r["args"]) or "other"].append(self.row(r, v2, tz))
        d = datetime.fromtimestamp(start, ZoneInfo(tz))
        return {"day": f"{d.month}월 {d.day}일", "back": back, "count": len(rows),
                "groups": [{"key": k, "label": label, "rows": groups[k]} for k, label in GROUPS if groups[k]]}
