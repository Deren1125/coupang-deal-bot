"""대시보드에서 누르는 일 (POST /dashboard/api/action) — 무엇을 누를 수 있는지·확인 창·딜봇에서 바로 하는 일 (spec §1.9·§2·§4.5).

본문 {"action":"blog.publish","args":{…},"cid":"c_…","ctoken":null} → 응답:
    200 {"status":"ok|error","code":…,"text":…,"refresh":true}   딜봇에서 바로 한 일 (deal.* · bot.* · sns.* · push.* · todo.* · stats.*)
    202 {"status":"queued","id":"a_…"}                           Blog-Auto 에 맡긴 일 (blog.*) — 화면은 /api/actions?ids= 로 결과를 봄
    409 {"status":"confirm","ctoken":…,"title":…,"body":…,"ok":…,"danger":…}   되돌리기 어려운 일(★)의 확인 창 — ctoken 을 붙여 다시
    400/403/429/503 {"status":"error","code":…,"text":…}

- 확인 창(★) 목록은 CONFIRM 이 하나뿐인 기준. ctoken = HMAC(세션 | 일 | 정리한 args | 시각), 5분 · 한 번만
- 같은 cid(누를 때마다 화면이 만든 난수)는 10분 동안 같은 결과 (두 번 눌러도 한 번)
- 딜봇 일은 텔레그램 명령과 같은 DealBot 함수를 같은 검사로 부른다. 그동안 그 줄(target)에 새로 열린 알림만 quiet (주인이 그 줄을 보는 중)
- 대시보드는 force 를 넘기지 않는다 — Blog-Auto 의 '직접 고친 글' 지킴(A-37)이 그대로 막는다. 덮어쓰기는 oedit_redo(★★) 하나
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dealbot.bridge import spool
from dealbot.bridge.actions_out import ActionsBusy, ActionsOut, ActionStore, redact, status_of
from dealbot.dashboard.model import safe_text
from dealbot.notify import iid as iidmod
from dealbot.utils.text import truncate

log = logging.getLogger(__name__)

BLOGS = ("finance", "issue", "hotdeal")
BLOG_NAMES = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
CTOKEN_S = 300
CID_S = 600
CID_RE = re.compile(r"^c_[A-Za-z0-9_-]{6,40}$")
ACTION_RE = re.compile(r"^[a-z]+\.[a-z_]+$")
ACTION_RATE = 30  # 세션마다 1분에
URL_MAX = 2000
SECRET_MAX = 600
CREDIT_MAX = 60  # Blog-Auto 와 같은 한도 (넘으면 사진을 다 올린 뒤에야 거절되지 않게 여기서 바로)
CHANNEL_URL_MAX = 300

# ★ 확인 창 (spec §4.5 — 하나뿐인 기준). blog.mode 는 발행 모드로 바꿀 때만, push.unsubscribe 는 다른 기기일 때만
CONFIRM = frozenset({
    "deal.skip", "deal.repost", "deal.post", "bot.pause", "sns.test", "sns.disconnect",
    "blog.publish", "blog.drop", "blog.fix", "blog.rewrite", "blog.restyle", "blog.share", "blog.channel", "blog.oedit_redo",
    "blog.mode", "blog.pause", "blog.drafts_cleanup", "blog.ledger", "push.unsubscribe",
})
DANGER = frozenset({"blog.oedit_redo", "blog.drop", "deal.skip"})
# 그 블로그가 자동 발행 모드면 바로 네이버에 공개되는 일 (Blog-Auto nl.CATALOG 의 may_publish 와 같게) — 그때만 ★
MAY_PUBLISH = frozenset({"blog.retry", "blog.fix", "blog.photos", "blog.write", "blog.issue", "blog.pick", "blog.hotdeal_now", "blog.fixpic"})
FIXED_BLOG = {"blog.write": "finance", "blog.pick": "finance", "blog.issue": "issue", "blog.hotdeal_now": "hotdeal",
              "blog.fixpic": "finance"}
# Blog-Auto 에 맡기는 일 (blog.<이름> → Blog-Auto 의 이름)
BLOG_ACTIONS = ("publish", "drop", "retry", "fix", "fixpic", "rewrite", "restyle", "photos", "photo_clear", "answer", "noanswer",
                "oedit_keep", "oedit_redo", "share", "noshare", "channel", "scan", "pick", "write", "issue", "hotdeal_now", "mode",
                "pause", "resume", "drafts_cleanup", "ledger", "login_check", "info", "kakao_auth_url", "kakao_connect", "kakao_test",
                "dismiss")
DEAL_ACTIONS = ("deal.link", "deal.skip", "deal.nolink", "deal.sc_forget", "deal.ok", "deal.ok_text", "deal.posted", "deal.repost", "deal.late_photo", "deal.post",
                "bot.pause", "bot.resume", "bot.run", "sns.token", "sns.code", "sns.test", "sns.invites", "push.test",
                "todo.dismiss", "stats.refresh", "stats.input")
# 다 했어요 (todo.dismiss): 밖에서 끝내는 일만. 링크·확인·사진·답·발행·직접 고친 글 같은 진짜 일은 일을 해야 닫힌다
DISMISS_POST_HEADS = ("dup", "cards", "openchat")
REAL_WORK_HEADS = ("link", "review", "unsure", "photo", "ask", "draft", "oedit", "fail", "chan", "share", "hot")
# Blog-Auto 가 닫아 주는 키 머리 (blogauto/bridge.py DISMISS_HEADS 와 같게) — 이 밖의 Blog-Auto 줄은 '다 했어요'를 보이지 않는다
BLOGAUTO_DISMISS_HEADS = ("dup", "cards", "openchat", "cq", "buddy", "stalled", "scan", "err", "loop", "neighbors", "restyle_once")
REPLIES_KEY = "threads_replies:"


def dismissible(iid: str, key: str, *, dealbot_row: bool) -> bool:
    """'다 했어요'를 보여 줄·받을 줄인가. dealbot_row: 딜봇이 연 알림 줄 (딜봇이 바로 닫음) — 아니면 Blog-Auto 가 닫을 수 있는 머리만."""
    if iid == "sns-replies":
        return True
    head = key.split(":", 1)[0]
    kind_ok = (iid.startswith("post-") and head in DISMISS_POST_HEADS) or iid.startswith(("blog-", "sys-")) or iid == "sns-status"
    if not kind_ok or head in REAL_WORK_HEADS or not key or key.endswith(":"):
        return False
    return dealbot_row or head in BLOGAUTO_DISMISS_HEADS

# 화면 안내 (코드 → 한 줄). 화면은 코드로 고르고, text 는 대신 쓸 글
TEXTS = {
    "done": "했어요",
    "linked": "링크를 넣었어요 — 차례가 되면 채널에 올라가요",
    "linked_twin": "같은 상품이 새 번호로 다시 줄 서 있어 그 딜에 넣었어요",
    "link_not_waiting": "지금은 링크를 기다리는 딜이 아니에요 (이미 넣었거나 내려간 딜)",
    "link_replaced_late": "더 좋은 딜로 바뀐 뒤 시간이 지나 넣지 않았어요",
    "link_twin_review": "같은 상품이 확인을 기다리고 있어요 — 먼저 올리기·안 올리기를 정해 주세요",
    "link_bad": "이 링크는 붙일 수 없어요 — 그 몰 앱에서 만든 상품·제휴 링크를 넣어 주세요",
    "link_failed": "링크를 넣지 못했어요 — 잠시 뒤 다시 눌러 주세요",
    "link_busy": "링크 넣기는 1분에 10번까지예요 — 조금 뒤에 다시",
    "photo_refused": "사진을 쓸 수 없어요 — 다른 사진으로 다시",
    "approved": "올릴 차례에 넣었어요 — 곧 채널에 올라가요",
    "skipped": "안 올리기로 했어요",
    "posted": "채널에 올라간 글로 기록했어요",
    "no_link": "넣어 둔 링크가 없어요 — 링크를 넣어 다시 올려 주세요",
    "queued": "맨 앞에 넣었어요 — 차례가 되면 바로 채널에 올라가요",
    "paused": "딜봇을 멈췄어요",
    "resumed": "딜봇을 다시 시작했어요",
    "run": "곧 확인할게요 (1분 안에 시작)",
    "connected": "연결했어요",
    "not_found": "없는 항목이에요",
    "wrong_state": "지금 상태에서는 할 수 없어요",
    "bad_args": "입력한 값을 다시 확인해 주세요",
    "bad_upload": "올린 파일을 쓸 수 없어요 — 다시 올려 주세요",
    "failed": "하지 못했어요 — 잠시 뒤 다시 눌러 주세요",
    "unsupported": "블로그 프로그램 업데이트를 기다리는 중이에요",
    "blogauto_down": "블로그 프로그램이 응답하지 않아요",
    "not_dismissible": "이 일은 직접 처리해야 닫혀요",
    "busy": "앞의 작업이 아직 하는 중이에요 — 끝나면 다시 눌러 주세요",
    "rate": "너무 자주 눌렀어요 — 조금 뒤에 다시",
    "confirm_expired": "확인 창이 오래됐어요 — 다시 눌러 주세요",
    "off": "지금은 대시보드에서 할 수 없어요",
    "pushed": "테스트 알림을 보냈어요",
    "no_device": "알림 켠 기기가 없어 테스트 카톡을 보냈어요",
}


class ActionError(Exception):
    def __init__(self, code: str, status: int = 400, text: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.text = text or TEXTS.get(code, TEXTS["failed"])


@dataclass(slots=True)
class Caller:
    """누가 눌렀나: session = 세션 해시 앞 8자 (Basic 이면 'basic'), client8 = 접속 주소 해시 앞 8자."""

    session: str
    client8: str = ""


def plain(text: str, limit: int = 120) -> str:
    """DealBot 답(텔레그램 HTML) → 화면 한 줄 (태그·명령 줄 없이, 토큰처럼 보이는 글자는 가림)."""
    s = re.sub(r"<[^>]+>", "", text or "")
    s = s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    first = next((ln.strip() for ln in s.splitlines() if ln.strip()), "")
    return safe_text(first, limit)


# ---------------------------------------------------------------- args 걸러 받기
def _int(v: Any, lo: int = 1, hi: int = 10**9) -> int:
    if isinstance(v, bool):
        raise ActionError("bad_args")
    if isinstance(v, str) and v.strip().lstrip("#").isdigit():
        v = int(v.strip().lstrip("#"))
    if not isinstance(v, int) or not lo <= v <= hi:
        raise ActionError("bad_args")
    return v


def _str(v: Any, limit: int, *, required: bool = True) -> str:
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        raise ActionError("bad_args")
    v = v.strip()
    if (required and not v) or len(v) > limit or "\x00" in v:
        raise ActionError("bad_args")
    return v


def _blog(v: Any) -> str:
    if v not in BLOGS:
        raise ActionError("bad_args")
    return str(v)


def _uploads(v: Any, lo: int, hi: int) -> list[str]:
    if v is None:
        v = []
    if not isinstance(v, list) or not lo <= len(v) <= hi or not all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{32}", x) for x in v):
        raise ActionError("bad_args")
    return list(dict.fromkeys(v))


def _pids(v: Any, *, allow_all: bool = False) -> list[int] | str:
    if allow_all and v == "all":
        return "all"
    if isinstance(v, int | str) and not isinstance(v, bool):
        v = [v]
    if not isinstance(v, list) or not 1 <= len(v) <= 20:
        raise ActionError("bad_args")
    return list(dict.fromkeys(_int(x) for x in v))


def own_blog_url(url: str, blog_id: str | None) -> bool:
    """채널에 올릴 주소: 주인 블로그 글만 (blog.naver.com · m.blog.naver.com, 첫 경로가 그 블로그 id)."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or (parts.hostname or "").lower() not in ("blog.naver.com", "m.blog.naver.com"):
        return False
    first = parts.path.strip("/").split("/", 1)[0].lower()
    return bool(blog_id) and first == str(blog_id).lower()


def clean_args(action: str, args: dict[str, Any], *, blog_ids: dict[str, str] | None = None) -> dict[str, Any]:
    """일마다 받을 수 있는 args 만 (모양이 다르면 ActionError bad_args). 모르는 키는 버린다."""
    a = args if isinstance(args, dict) else {}
    g = a.get
    if action == "deal.link":
        out: dict[str, Any] = {"qid": _int(g("qid")), "url": _str(g("url"), URL_MAX)}
        up = g("upload_id") or ((g("uploads") or [None])[0] if isinstance(g("uploads"), list) else None)
        if up is not None:
            out["upload_id"] = _uploads([up], 1, 1)[0]
        return out
    if action in ("deal.skip", "deal.ok", "deal.repost"):
        return {"qid": _int(g("qid"))}
    if action == "deal.nolink":  # 쇼핑 커넥트에 없음: post = 링크 없이 올리기 · skip = 안 올리기
        if g("name") not in ("post", "skip"):
            raise ActionError("bad_args")
        return {"qid": _int(g("qid")), "name": g("name")}
    if action == "deal.sc_forget":  # 쇼핑 커넥트 없는 판매처 하나 빼기
        return {"name": _str(g("name"), 80)}
    if action == "deal.ok_text":
        return {"qid": _int(g("qid")), "text": _str(g("text"), 4000)}
    if action == "deal.posted":
        return {"qid": _int(g("qid")), "url": _str(g("url"), URL_MAX, required=False)}
    if action == "deal.late_photo":
        ups = g("uploads") if g("uploads") is not None else ([g("upload_id")] if g("upload_id") else None)
        return {"qid": _int(g("qid")), "uploads": _uploads(ups, 1, 1)}
    if action == "deal.post":
        return {"text": _str(g("text"), 4000)}
    if action in ("bot.pause", "bot.resume", "sns.invites", "push.test"):
        return {}
    if action == "bot.run":
        name = g("name")
        return {"name": None if name in (None, "", "all") else _str(name, 40)}
    if action in ("sns.token", "sns.code"):
        sns = g("sns")
        if sns not in ("threads", "instagram"):
            raise ActionError("bad_args")
        return {"sns": sns, ("token" if action == "sns.token" else "code"): _str(g("token" if action == "sns.token" else "code"),
                                                                              SECRET_MAX)}
    if action == "sns.test":
        sns = g("sns")
        if sns not in ("threads", "instagram"):
            raise ActionError("bad_args")
        return {"sns": sns, "qid": _int(g("qid")) if g("qid") not in (None, "") else None}
    if action == "todo.dismiss":
        iid = _str(g("iid"), 64)
        if not iidmod.ok(iid):
            raise ActionError("bad_args")
        return {"iid": iid, "key": _str(g("key"), 120)}
    if action == "stats.refresh":
        return {"src": _str(g("src"), 40)}
    if action == "stats.input":
        values = g("values")
        if not isinstance(values, dict) or len(values) > 10 or not all(isinstance(k, str) and isinstance(v, str | int) for k, v in values.items()):
            raise ActionError("bad_args")
        return {"src": _str(g("src"), 40), "values": {k[:40]: str(v)[:20] for k, v in values.items()},
                "date": _str(g("date"), 10, required=False)}
    if not action.startswith("blog."):
        raise ActionError("bad_args")
    return _clean_blog(action[5:], a, blog_ids or {})


def _clean_blog(name: str, a: dict[str, Any], blog_ids: dict[str, str]) -> dict[str, Any]:
    g = a.get
    if name in ("publish", "retry", "share", "noshare", "photo_clear") or name in ("fix", "photos", "channel"):
        out: dict[str, Any] = {"blog": _blog(g("blog")), "pid": _int(g("pid"))}
        if name == "fix":
            out["text"] = _str(g("text"), 1000)
        if name == "photo_clear":
            out["slot"] = _int(g("slot"), 1, 40)
        if name == "photos":
            out["uploads"] = _uploads(g("uploads"), 1, 20)
            mode = g("mode") or ("slot" if g("slot") else "add")
            if mode not in ("add", "replace", "slot"):
                raise ActionError("bad_args")
            out.update(mode=mode, slot=_int(g("slot"), 1, 40) if mode == "slot" else 0, in_order=g("in_order") is True,
                       crop=g("crop") is True, credit=_str(g("credit"), CREDIT_MAX, required=False))
        if name == "channel":
            url = _str(g("url"), CHANNEL_URL_MAX, required=False)
            if url and not own_blog_url(url, blog_ids.get(out["blog"])):
                raise ActionError("bad_args")
            out["url"] = url
        return out
    if name == "drop":
        return {"blog": _blog(g("blog")), "pids": _pids(g("pids") if g("pids") is not None else g("pid"))}
    if name in ("rewrite", "restyle"):
        return {"blog": "issue", "pids": _pids(g("pids") if g("pids") is not None else g("pid"), allow_all=name == "restyle")}
    if name in ("fixpic", "answer", "noanswer", "oedit_keep", "oedit_redo"):
        out = {"pid": _int(g("pid"))}
        if g("blog") is not None:
            out["blog"] = _blog(g("blog"))
        if name == "answer":
            out["text"] = _str(g("text"), 4000)
        return out
    if name in ("scan", "hotdeal_now", "kakao_auth_url", "kakao_test"):
        return {}
    if name == "pick":
        return {"sid": _str(g("sid"), 40), "n": _int(g("n"), 1, 50)}
    if name == "write":
        return {"text": _str(g("text"), 4000), "uploads": _uploads(g("uploads"), 0, 20)}
    if name == "issue":
        return {"keyword": _str(g("keyword"), 40, required=False)}
    if name == "mode":
        if g("mode") not in ("draft", "publish"):
            raise ActionError("bad_args")
        return {"blog": _blog(g("blog")), "mode": g("mode")}
    if name in ("pause", "resume", "drafts_cleanup", "login_check"):
        return {"blog": _blog(g("blog"))}
    if name == "ledger":
        return {"uploads": _uploads(g("uploads") if g("uploads") is not None else [g("upload_id")], 1, 1)}
    if name == "info":
        if g("what") not in ("trends", "plan", "creator"):
            raise ActionError("bad_args")
        return {"what": g("what")}
    if name == "kakao_connect":
        url = _str(g("url"), URL_MAX)
        if not url.startswith("https://"):
            raise ActionError("bad_args")
        return {"url": url}
    if name == "dismiss":
        return {"key": _str(g("key"), 120)}
    raise ActionError("bad_args")


def publish_blog(action: str, args: dict[str, Any]) -> str | None:
    """MAY_PUBLISH 일이 걸린 블로그 (그 블로그 모드를 보고 ★ 를 정한다). 아니면 None."""
    if action not in MAY_PUBLISH:
        return None
    blog = args.get("blog")
    return str(blog) if blog in BLOGS else FIXED_BLOG.get(action)


def needs_confirm(action: str, args: dict[str, Any], *, this_device: bool = True, publish_mode: bool = False) -> bool:
    """★ 확인 창이 필요한가. publish_mode: 그 일이 걸린 블로그가 자동 발행 모드(또는 모름)라 MAY_PUBLISH 일이 바로 공개되는 때."""
    if action in MAY_PUBLISH and publish_mode:
        return True
    if action not in CONFIRM:  # (blog.fix 처럼 원래 ★ 인 일은 임시저장 모드여도 ★)
        return False
    if action == "blog.mode":
        return args.get("mode") == "publish"
    if action == "push.unsubscribe":
        return not this_device
    return True


def target_of(action: str, args: dict[str, Any]) -> str:
    """그 일이 걸린 항목 (iid) — 같은 일 두 번 막기·기록에."""
    if action.startswith("deal.") and "qid" in args:
        return iidmod.deal_iid(args["qid"])
    blog, pid = args.get("blog"), args.get("pid")
    if isinstance(pid, int) and blog in BLOGS:
        return iidmod.post_iid(blog, pid)
    pids = args.get("pids")
    if isinstance(pids, list) and len(pids) == 1 and blog in BLOGS:
        return iidmod.post_iid(blog, pids[0])
    if action == "todo.dismiss":
        return str(args.get("iid") or "")
    if action in ("blog.mode", "blog.pause", "blog.resume", "blog.drafts_cleanup") and blog in BLOGS:
        return f"blog-{blog}"
    if action == "blog.login_check" and blog in BLOGS:
        return f"conn-naver-{blog}"
    if action.startswith("blog.kakao"):
        return "conn-kakao"
    return ""


# ---------------------------------------------------------------- 확인 창 글 (지금 사실로)
def confirm_text(action: str, args: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    blog = BLOG_NAMES.get(str(args.get("blog") or ""), "")
    pids = args.get("pids")
    pid = args.get("pid") or (pids[0] if isinstance(pids, list) and pids else None)
    num = f" #{pid}" if pid else ""
    qid = args.get("qid")
    edited = bool(facts.get("owner_edited"))
    ed_note = " 직접 고친 글이라 바로 바뀌지 않고, 할 일에서 한 번 더 물어봐요."
    c = {
        "deal.skip": (f"#{qid} 딜을 안 올릴까요?", "채널에 올리지 않아요. 나중에 올리려면 새 딜로 직접 넣어야 해요.", "안 올리기"),
        "deal.repost": (f"#{qid} 딜을 다시 올릴까요?", "채널을 먼저 확인했나요? 이미 올라갔으면 두 번 올라가요.", "다시 올리기"),
        "deal.post": ("새 딜을 올릴까요?", "맨 앞에 넣고 차례가 되면 채널에 바로 올라가요.", "올리기"),
        "bot.pause": ("딜봇을 멈출까요?", "게시판 확인과 채널 올리기를 모두 멈춰요. '다시 시작'을 누르면 이어서 해요.", "멈추기"),
        "sns.test": (f"{'스레드' if args.get('sns') == 'threads' else '인스타'}에 테스트 글을 올릴까요?",
                     "공개 글로 올라가요. 확인한 뒤 앱에서 지워 주세요.", "올리기"),
        "sns.disconnect": ("연결을 끊을까요?", "끊으면 자동으로 올리지 않아요. 다시 연결해야 해요.", "끊기"),
        "blog.publish": (f"{blog}{num} 글을 발행할까요?", "네이버 블로그에 바로 올라가요. 발행 후에는 네이버 앱에서만 지울 수 있어요.", "발행"),
        "blog.drop": (f"{blog}{num} 글을 버릴까요?", "이 글은 올리지 않아요. 봇이 만든 임시저장 글만 지워요."
                      + (" 직접 고치신 임시저장 글은 네이버에 그대로 남아요." if edited else ""), "버리기"),
        "blog.fix": ((f"{blog}{num} 글을 말한 대로 고칠까요?", "고친 뒤 자동 발행 모드라 바로 네이버에 공개돼요."
                      + (ed_note if edited else ""), "고치고 발행") if facts.get("publish") else
                     (f"{blog}{num} 글을 말한 대로 고칠까요?", "지금 글을 고쳐 다시 저장해요." + (ed_note if edited else ""), "고치기")),
        "blog.rewrite": (f"Pick{num} 글을 다시 쓸까요?", "지금 글을 새로 써서 바꿔요." + (ed_note if edited else ""), "다시 쓰기"),
        "blog.restyle": (("Pick 글 전체 스타일을 다시 입힐까요?", "기다리는 Pick 글의 꾸밈을 새로 입혀요. 직접 고친 글은 건너뛰어요.", "스타일 다시")
                         if args.get("pids") == "all" else
                         (f"Pick{num} 글 스타일을 다시 입힐까요?", "글 꾸밈을 새로 입혀요." + (ed_note if edited else ""), "스타일 다시")),
        "blog.share": (f"{blog}{num} 글을 채널·카톡으로 보낼까요?", "텔레그램 채널과 카톡 오픈채팅에 이 글을 알려요.", "보내기"),
        "blog.channel": (f"{blog}{num} 글을 채널에 올릴까요?", "텔레그램 채널에 이 글 소개를 올려요.", "채널에 올리기"),
        "blog.oedit_redo": ("봇 글로 덮어쓸까요?", "직접 고치신 내용이 봇 글로 바뀌어요. 되돌릴 수 없어요.", "덮어쓰기"),
        "blog.mode": (f"{blog} 블로그를 발행 모드로 바꿀까요?", "앞으로 이 블로그 글은 임시저장 없이 바로 발행돼요.", "바꾸기"),
        "blog.pause": (f"{blog} 블로그를 멈출까요?", "새 글 쓰기와 발행을 멈춰요. '다시 시작'으로 이어서 해요.", "멈추기"),
        "blog.drafts_cleanup": (f"{blog} 임시저장 글을 정리할까요?", "봇이 만든 오래된 임시저장 글만 지워요. 직접 쓰신 글은 그대로 둬요.", "정리하기"),
        "blog.ledger": ("정리본을 바꿀까요?", "올린 파일로 아카이브 정리본을 바꿔요. 지금 정리본은 백업해 둬요.", "바꾸기"),
        "push.unsubscribe": (f"{facts.get('device') or '그 기기'} 알림을 끌까요?", "그 기기에는 알림이 더 가지 않아요.", "끄기"),
    }
    pub = "자동 발행 모드라 바로 네이버에 공개돼요."
    blog_p = BLOG_NAMES.get(publish_blog(action, args) or "", blog)
    c.update({
        "blog.retry": (f"{blog}{num} 글을 다시 올릴까요?", pub, "공개로 올리기"),
        "blog.photos": (f"{blog}{num} 글에 사진을 넣을까요?", "사진을 넣고 " + pub, "넣고 발행"),
        "blog.fixpic": (f"{blog_p}{num} 글 사진을 바꿀까요?", "사진을 바꾸고 " + pub, "바꾸고 발행"),
        "blog.write": (f"{blog_p} 새 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.issue": (f"{blog_p} 새 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.pick": (f"이 글감으로 {blog_p} 글을 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
        "blog.hotdeal_now": (f"{blog_p} 글을 지금 한 편 쓸까요?", "다 쓰면 " + pub, "쓰고 발행"),
    })
    title, body, ok = c.get(action, ("이대로 할까요?", "되돌리기 어려운 일이에요.", "하기"))
    return {"title": title, "body": body, "ok": ok, "danger": action in DANGER}


class ConfirmTokens:
    """확인 창 표 (메모리 — 5분, 한 번만). 같은 세션·같은 일·같은 args 에만 맞는다."""

    def __init__(self, key: bytes, *, clock: Callable[[], float] = time.time) -> None:
        self.key = key
        self.clock = clock
        self._used: dict[str, float] = {}

    def _sig(self, session: str, action: str, args: dict[str, Any], ts: int) -> str:
        canon = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        mac = hmac.new(self.key, f"{session}|{action}|{canon}|{ts}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip("=")

    def issue(self, session: str, action: str, args: dict[str, Any]) -> str:
        ts = int(self.clock())
        return f"{ts}.{secrets.token_hex(4)}.{self._sig(session, action, args, ts)}"

    def check(self, token: Any, session: str, action: str, args: dict[str, Any]) -> bool:
        if not isinstance(token, str) or len(token) > 120:
            return False
        ts_raw, _, rest = token.partition(".")
        nonce, _, sig = rest.partition(".")
        if not ts_raw.isdigit() or not nonce or not sig:
            return False
        now = self.clock()
        for t in [t for t, at in self._used.items() if now - at > CTOKEN_S * 2]:
            self._used.pop(t, None)
        ts = int(ts_raw)
        if now - ts > CTOKEN_S or ts - now > 60 or token in self._used:
            return False
        if not hmac.compare_digest(sig.encode(), self._sig(session, action, args, ts).encode()):
            return False
        self._used[token] = now
        return True


class RateWindow:
    def __init__(self, per: int, window: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.per = per
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> bool:
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        if len(q) >= self.per:
            return False
        q.append(now)
        if len(self._hits) > 2048:
            self._hits.pop(next(iter(self._hits)))
        return True


# ---------------------------------------------------------------- 실행
class ActionDesk:
    """딜봇 쪽 일은 바로, 블로그 일은 Blog-Auto 에 맡긴다. bot = app.DealBot (가짜로 바꿔 끼울 수 있게 Any)."""

    def __init__(self, bot: Any, *, outbox: ActionsOut, uploads: Any, key: bytes, audit: Callable[..., None],
                 facts: Callable[[str], dict[str, Any]] | None = None, clock: Callable[[], float] = time.time,
                 test_rate: RateWindow | None = None) -> None:
        self.bot = bot
        self.outbox = outbox
        self.store: ActionStore = outbox.store
        self.uploads = uploads
        self.tokens = ConfirmTokens(key, clock=clock)
        self.audit = audit
        self.facts = facts or (lambda _iid: {})
        self.clock = clock
        self.rate = RateWindow(ACTION_RATE, 60.0)
        self.test_rate = test_rate  # 테스트 알림 한도 (POST /api/push/test 와 같은 셈 — 기기가 없으면 카톡 한 통이라)

    # 바깥에서 부르는 하나
    async def run(self, body: dict[str, Any], caller: Caller) -> tuple[int, dict[str, Any]]:
        action = body.get("action")
        cid = body.get("cid") if isinstance(body.get("cid"), str) and CID_RE.match(body.get("cid") or "") else None
        if not isinstance(action, str) or not ACTION_RE.match(action) or not (
                action in DEAL_ACTIONS or (action.startswith("blog.") and action[5:] in BLOG_ACTIONS)):
            return 400, self._err("bad_args")
        try:
            args = clean_args(action, body.get("args") or {}, blog_ids=self._blog_ids())
        except ActionError as e:
            self._audit(caller, action, "", {}, e.code)
            return e.status, self._err(e.code)
        target = target_of(action, args)
        if cid is not None and (prev := self.store.by_cid(cid, self.clock() - CID_S)) is not None:
            return self._replay(prev)
        if not self.rate.hit(caller.session):
            return 429, self._err("rate")
        if action == "push.test" and self.test_rate is not None and not self.test_rate.hit(caller.session):
            return 429, self._err("rate")
        if not getattr(getattr(self.bot.settings, "dashboard", None), "actions_enabled", True):
            return 403, self._err("off")
        if action.startswith("blog.") and (down := self._blog_down(action[5:], caller, target, args)) is not None:
            return down  # 확인 창을 띄우기 전에 (맡길 곳이 없는데 '발행할까요?'를 묻지 않게)
        this_device = True
        pub = self._publish_mode(action, args)
        need = needs_confirm(action, args, this_device=this_device, publish_mode=pub)
        if need:
            if body.get("ctoken") is None:
                facts = self.facts(target) if target else {}
                if pub:
                    facts = {**facts, "publish": True}
                tok = self.tokens.issue(caller.session, action, args)
                return 409, {"status": "confirm", "ctoken": tok, **confirm_text(action, args, facts)}
            if not self.tokens.check(body.get("ctoken"), caller.session, action, args):
                return 409, {**self._err("confirm_expired"), "status": "confirm_expired"}
        if action.startswith("blog."):
            return self._queue(action, args, target, caller, cid, confirmed=need)
        return await self._deal(action, args, target, caller, cid)

    def _publish_mode(self, action: str, args: dict[str, Any]) -> bool:
        """MAY_PUBLISH 일의 블로그가 자동 발행 모드인가 (모르면 True — 확인 창 한 번이 더 안전).
        Blog-Auto 도 실행할 때 모드를 다시 보고 confirmed 가 없으면 받지 않는다 (누른 뒤 모드가 바뀌어도)."""
        blog = publish_blog(action, args)
        if blog is None:
            return False
        return self.modes().get(blog) != "draft"

    def modes(self) -> dict[str, str]:
        """블로그마다 지금 모드 (Blog-Auto 기록 파일 status.blogs.*.mode)."""
        try:
            st = self.bot.dash_state
            export = st._export() if st is not None else None
            blogs = ((getattr(export, "v2", None) or {}).get("status") or {}).get("blogs") or {}
            return {b: str((blogs.get(b) or {}).get("mode") or "") for b in BLOGS}
        except Exception:  # noqa: BLE001
            return {}

    def _blog_ids(self) -> dict[str, str]:
        try:
            return dict(getattr(self.bot, "blog_ids", lambda: {})())
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _err(code: str, text: str | None = None) -> dict[str, Any]:
        return {"status": "error", "code": code, "text": text or TEXTS.get(code, TEXTS["failed"])}

    def _replay(self, row: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        st = status_of(row)
        if row["action"].startswith("blog."):
            return 202, {"status": "queued", "id": row["id"]}
        return 200, {"status": "ok" if row["status"] == "ok" else "error", "code": st["code"], "text": st["text"],
                     "refresh": True, "id": row["id"]}

    def _audit(self, caller: Caller, action: str, target: str, args: dict[str, Any], code: str, aid: str | None = None) -> None:
        try:
            self.audit(session8=caller.session, client8=caller.client8, action=action, target=target,
                       args=redact(action, args), code=code, action_id=aid)
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard audit failed: %s", type(e).__name__)

    # ---------------------------------------------------------- 블로그 일 (Blog-Auto)
    def _blog_down(self, name: str, caller: Caller, target: str, args: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
        """Blog-Auto 에 맡길 수 없으면 503 (신호 없음·다리 없음·모르는 일)."""
        st = self.outbox.status()
        if not st.bridge_ok(self.clock()):
            self._audit(caller, f"blog.{name}", target, args, "blogauto_down")
            return 503, self._err("blogauto_down")
        if st.actions and not st.supports(name):
            self._audit(caller, f"blog.{name}", target, args, "unsupported")
            return 503, self._err("unsupported")
        return None

    def _queue(self, action: str, args: dict[str, Any], target: str, caller: Caller, cid: str | None, *,
               confirmed: bool = False) -> tuple[int, dict[str, Any]]:
        name = action[5:]
        if (down := self._blog_down(name, caller, target, args)) is not None:
            return down
        spool_args = dict(args)
        ups = args.get("uploads") or []
        if ups:
            kind = "ledger" if name == "ledger" else "photo"
            paths = []
            for uid in ups:
                p = self.uploads.path(uid, kind=kind)
                if p is None:
                    self._audit(caller, action, target, args, "bad_upload")
                    return 400, self._err("bad_upload")
                paths.append(str(p))
            if name == "ledger":  # Blog-Auto x_ledger 는 {"upload": 경로} 하나 (계약 §2.4 ledger: {upload})
                spool_args.pop("uploads", None)
                spool_args["upload"] = paths[0]
            else:
                spool_args["uploads"] = paths
        try:
            aid, existed = self.outbox.submit(action=name, blog=args.get("blog"), args=spool_args, target=target,
                                              confirmed=confirmed, session=caller.session, cid=cid)
        except ActionsBusy:  # 같은 글에 같은 일이 다른 값으로 아직 하는 중 → 올린 사진은 화면에 그대로 (다시 누르면 됨)
            self._audit(caller, action, target, args, "busy")
            return 409, self._err("busy")
        except (OSError, ValueError) as e:
            log.warning("dashboard action spool write failed: %s", type(e).__name__)
            self._audit(caller, action, target, args, "failed")
            return 503, self._err("failed")
        if ups and not existed:
            self.uploads.mark_used(ups, aid)
        self._audit(caller, action, target, args, "queued", aid)
        return 202, {"status": "queued", "id": aid}

    # ---------------------------------------------------------- 딜봇 일 (바로)
    async def _deal(self, action: str, args: dict[str, Any], target: str, caller: Caller, cid: str | None) -> tuple[int, dict[str, Any]]:
        router = getattr(self.bot, "router", None)
        quiet = router.quiet(target) if router is not None else contextlib.nullcontext()  # 그 줄에 열린 알림만 조용히
        try:
            with quiet:
                code, text = await self._do(action, args, caller)
        except ActionError as e:
            code, text = e.code, e.text
        except Exception as e:  # noqa: BLE001 — 한 일이 터져도 대시보드는 그대로
            log.exception("dashboard action %s failed", action)
            code, text = "failed", TEXTS["failed"] + f" ({type(e).__name__})"
        ok = code in OK_CODES
        now = self.clock()
        aid = spool.new_id("a", now)
        result = {"code": code, "text": safe_text(text, 120), "done_at": now}
        self.store.insert(aid=aid, ts=now, blog=None, action=action, target=target, args=args, status="ok" if ok else "error",
                          cid=cid, session=caller.session, result=result)
        self._audit(caller, action, target, args, code, aid)
        return 200, {"status": "ok" if ok else "error", "code": code, "text": result["text"], "refresh": True, "id": aid}

    async def _do(self, action: str, args: dict[str, Any], caller: Caller) -> tuple[str, str]:
        bot = self.bot
        db = bot.db
        if action == "deal.link":
            dash = bot.dashboard
            if dash is None:
                raise ActionError("off")
            if not dash.link_rate_ok():
                raise ActionError("link_busy", 429)
            photo = None
            if args.get("upload_id"):
                photo = self.uploads.deal_jpeg(args["upload_id"])
                if photo is None:
                    raise ActionError("bad_upload")
            code, note = await dash.owner_link(args["qid"], args["url"], photo=photo)
            text = TEXTS.get(code, TEXTS["failed"])
            if code == "linked_twin" and note:
                text = f"같은 상품이 #{note} 번으로 다시 줄 서 있어 #{note} 번으로 받아요"
            if code == "photo_refused" and note:
                text = f"{TEXTS['photo_refused']} ({truncate(note, 60)})"
            return code, text
        if action == "deal.skip":
            item = db.get_queue_item(args["qid"])
            if item is None:
                return "not_found", TEXTS["not_found"]
            reply = bot.skip_item(args["qid"])
            after = db.get_queue_item(args["qid"])
            return ("skipped", TEXTS["skipped"]) if after is not None and after.status == "skipped" else ("wrong_state", plain(reply))
        if action == "deal.nolink":
            code, text = bot.no_connect(args["qid"], post=args["name"] == "post")
            if code in ("nolink", "skipped"):
                try:
                    await bot.refresh_pending_notice()  # 텔레그램 고정 공지('내 링크 기다리는 글')에서 빠지게
                except Exception as e:  # noqa: BLE001 — 공지를 못 고쳐도 한 일은 됐다
                    log.warning("dashboard nolink: pending notice refresh failed: %s", type(e).__name__)
            return code, text or TEXTS.get(code, TEXTS["failed"])
        if action == "deal.sc_forget":
            if not bot.sc_store_forget(args["name"]):
                return "not_found", TEXTS["not_found"]
            return "done", "목록에서 뺐어요 — 다음 딜부터 다시 링크를 물어요"
        if action in ("deal.ok", "deal.ok_text"):
            item = db.get_queue_item(args["qid"])
            if item is None:
                return "not_found", TEXTS["not_found"]
            if item.status != "awaiting_approval":
                return "wrong_state", TEXTS["wrong_state"]
            reply = bot.approve_item(args["qid"], args.get("text"))
            after = db.get_queue_item(args["qid"])
            ok = after is not None and after.status == "pending"
            return ("approved", TEXTS["approved"]) if ok else ("wrong_state", plain(reply))
        if action == "deal.posted":
            if db.get_queue_item(args["qid"]) is None:
                return "not_found", TEXTS["not_found"]
            reply = await bot.mark_posted(args["qid"], args.get("url") or "")
            after = db.get_queue_item(args["qid"])
            return ("posted", TEXTS["posted"]) if after is not None and after.status == "published" else ("wrong_state", plain(reply))
        if action == "deal.repost":
            code, text = await bot.repost_unsure(args["qid"])
            return code, text or TEXTS.get(code, TEXTS["failed"])
        if action == "deal.late_photo":
            if not bot.late_photo_waiting(args["qid"]):
                return "wrong_state", "지금은 사진을 기다리는 딜이 아니에요"
            jpeg = self.uploads.deal_jpeg(args["uploads"][0])
            if jpeg is None:
                raise ActionError("bad_upload")
            reply = await bot.attach_late_photo(args["qid"], jpeg)
            ok = plain(reply).startswith("✅")
            return ("done" if ok else "photo_refused"), plain(reply)
        if action == "deal.post":
            reply = await bot.submit_manual(args["text"])
            return ("queued", plain(reply)) if plain(reply).startswith("📝") else ("bad_args", plain(reply))
        if action == "bot.pause":
            bot.pause()
            return "paused", TEXTS["paused"]
        if action == "bot.resume":
            bot.resume()
            return "resumed", TEXTS["resumed"]
        if action == "bot.run":
            name = args.get("name")
            if name is not None and name not in bot.collector_names():
                return "bad_args", TEXTS["bad_args"]
            bot.request_run(name)
            return "run", TEXTS["run"]
        if action == "sns.token":
            fn = bot.threads_submit_token if args["sns"] == "threads" else bot.instagram_submit_token
            reply = plain(await fn(args["token"]))
            return ("connected" if reply.startswith("✅") else "failed"), reply
        if action == "sns.code":
            fn = bot.threads_submit_code if args["sns"] == "threads" else bot.instagram_submit_code
            reply = plain(await fn(args["code"]))
            return ("connected" if reply.startswith("✅") else "failed"), reply
        if action == "sns.test":
            fn = bot.threads_test if args["sns"] == "threads" else bot.instagram_test
            reply = await fn(args.get("qid"))
            ok = "✅" in reply
            lines = [plain(ln) for ln in reply.splitlines() if ("✅" in ln or "❌" in ln)]
            return ("done" if ok else "failed"), (lines[0] if lines else plain(reply))
        if action == "sns.invites":
            reply = await bot.invite_links()
            return ("done" if "t.me/" in reply else "failed"), "초대 링크를 새로 만들었어요" if "t.me/" in reply else plain(reply)
        if action == "push.test":
            router = getattr(bot, "router", None)
            if router is None:
                raise ActionError("off")
            got = await router.push_test()
            if got["sent"]:
                return "pushed", f"테스트 알림을 {got['sent']}대에 보냈어요"
            if got.get("kakao"):
                return "no_device", TEXTS["no_device"]
            return "failed", "알림을 보내지 못했어요 — 기기에서 알림을 다시 켜 주세요"
        if action == "todo.dismiss":
            return self._dismiss(args, caller)
        if action == "stats.refresh":
            dash = bot.dashboard
            got = await dash.refresh(args["src"])
            return ("done" if got in ("refreshed", "reread") else got), {"refreshed": "다시 읽었어요", "reread": "다시 읽었어요"}.get(
                got, TEXTS.get(got, "읽지 못했어요"))
        if action == "stats.input":
            dash = bot.dashboard
            query = dict(args["values"])
            if args.get("date"):
                query["date"] = args["date"]
            got = dash.save_manual(args["src"], query)
            return ("done" if got == "saved" else got), ("저장했어요" if got == "saved" else TEXTS.get(got, TEXTS["bad_args"]))
        raise ActionError("bad_args")

    def _dismiss(self, args: dict[str, Any], caller: Caller) -> tuple[str, str]:
        """다 했어요: 밖에서 끝낸 일만 (두 번 발행·구매 링크 빠짐·서로이웃·멈춤·스레드 답글·시스템 참고)."""
        iid, key = args["iid"], args["key"]
        bot = self.bot
        router = getattr(bot, "router", None)
        row = router.store.get("dealbot", key) if router is not None and key else None
        dealbot_row = row is not None and row.iid == iid
        if not dismissible(iid, key, dealbot_row=dealbot_row):
            raise ActionError("not_dismissible")
        if iid == "sns-replies":
            if router is not None:
                router.store.replies_done(self.clock())
                router.resolve("dealbot", [REPLIES_KEY])
            return "done", "다 했어요"
        if dealbot_row:
            router.resolve("dealbot", [key])
            return "done", "다 했어요"
        # Blog-Auto 키 (알림 또는 Blog-Auto 가 셈한 줄) → Blog-Auto 가 닫는다. 여기까지 오는 건 Blog-Auto 가 닫아 주는 머리뿐
        # (dismissible — BLOGAUTO_DISMISS_HEADS) 이라 화면 줄은 바로 뺀다
        code, body = self._queue("blog.dismiss", {"key": key}, iid, caller, None)
        if code == 202:
            if router is not None:
                router.resolve("blogauto", [key])  # 화면에서 바로 빠지게 (Blog-Auto 도 곧 같은 키를 닫음)
            return "done", "다 했어요"
        return str(body.get("code") or "failed"), str(body.get("text") or TEXTS["failed"])


OK_CODES = frozenset({"done", "linked", "linked_twin", "nolink", "approved", "skipped", "posted", "queued", "paused", "resumed", "run",
                      "connected", "pushed", "no_device"})
