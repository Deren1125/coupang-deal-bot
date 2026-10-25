"""대시보드 항목 이름(iid) — 알림 키 → 대시보드의 '그 일' 하나 (Blog-Auto blogauto/iid.py 와 같은 표,
tests/fixtures/dash_contract/iid_map.json 이 두 저장소에서 같아야 한다).

한 가지 일(딜 하나·글 하나·연결 하나)에 걸린 알림은 모두 같은 iid — 대시보드는 그 일을 한 줄로만 보여 준다 (D9).
    딜 link:N·review:N·unsure:N·photo:N            → deal-N
    publish_broken·publisher_stuck·selfcheck:…·autolink:…·collector:…·linkprice:…·login:claude_cli·domain_dns·ig_ffmpeg
                                                   → sys-dealbot
    sns:threadsauth · sns:instaauth                → sns-threads · sns-instagram
    sns_zero · sns_scope                           → sns-status
    threads_replies:…                              → sns-replies
    글 draft:B:N·fail:B:N·photo:B:N·sched:B:N     → post-B-N
       ask:N·chan:N·share:N·oedit:N:…·dup:N·cards:N·openchat:N → post-<그 글의 블로그>-N (블로그는 알림을 낸 쪽이 줌)
    hot:…                                          → post-issue-N (글 번호를 알 때), 아니면 blog-issue
    login:B·kz:B·ownstats:B:…·login_down:B·own_stats_block:B → conn-naver-B
    kakao:…                                        → conn-kakao
    scan:…                                         → blog-finance-scan
    cq:B:…                                         → blog-B-comments
    buddy:B · stalled:B · waiting:B                → blog-B-buddy · blog-B-stall · blog-B-waiting
    neighbors:protect:B:…                          → blog-B-buddy (서로이웃 보호조치 문구 — 주인 10/18)
    err:…·loop:…·neighbors:config·album:…          → sys-blogauto
    정리                                            → digest-YYYYMMDD-am|pm      묶음 알림 → todo (할 일 화면)
모르는 키는 그 프로그램의 시스템 줄 (sys-dealbot · sys-blogauto — 잃지 않게)."""

from __future__ import annotations

import re
from datetime import date, datetime

IID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
BLOGS = ("finance", "issue", "hotdeal")
DEAL_HEADS = ("link", "review", "unsure")
SYS_DEALBOT_EXACT = ("publish_broken", "publisher_stuck", "login:claude_cli", "domain_dns", "ig_ffmpeg")
SYS_DEALBOT_HEADS = ("selfcheck", "autolink", "collector", "linkprice", "update")  # update: auto-update.sh 업데이트 막힘
SYS_BLOGAUTO_HEADS = ("err", "loop", "neighbors", "album")
POST_HEADS = ("ask", "chan", "share", "oedit", "dup", "cards", "openchat")  # 블로그 칸 없이 글 번호만 있는 글 키
TODO_IID = "todo"  # 묶음 푸시 → 할 일 화면
_DEAL_IID = re.compile(r"^deal-([0-9]{1,9})$")
_POST_IID = re.compile(r"^post-(finance|issue|hotdeal)-([0-9]{1,9})$")
_DIGEST_IID = re.compile(r"^digest-([0-9]{8})-(am|pm)$")


def iid_for(key: str, blog: str = "", pid: int | None = None, *, src: str = "dealbot") -> str:
    """알림 키 → iid. blog·pid: 알림을 낸 쪽이 아는 글의 블로그·번호 (키에 없을 때). src: 모르는 키를 둘 시스템 줄."""
    key = str(key or "")
    parts = key.split(":")
    head = parts[0]
    if key in SYS_DEALBOT_EXACT or head in SYS_DEALBOT_HEADS:
        return "sys-dealbot"
    if head == "neighbors" and len(parts) >= 3 and parts[1] == "protect" and parts[2] in BLOGS:
        return f"blog-{parts[2]}-buddy"
    if head in SYS_BLOGAUTO_HEADS:
        return "sys-blogauto"
    if head == "sns" and len(parts) > 1:
        return {"threadsauth": "sns-threads", "instaauth": "sns-instagram"}.get(parts[1], "sns-status")
    if head in ("sns_zero", "sns_scope"):
        return "sns-status"
    if head == "threads_replies":
        return "sns-replies"
    if head in DEAL_HEADS and len(parts) == 2 and parts[1].isdigit():
        return f"deal-{parts[1]}"
    if head in ("draft", "fail", "photo", "sched") and len(parts) == 3 and parts[1] in BLOGS and parts[2].isdigit():
        return f"post-{parts[1]}-{parts[2]}"
    if head == "photo" and len(parts) == 2 and parts[1].isdigit():
        return f"deal-{parts[1]}"
    if head in POST_HEADS and len(parts) >= 2 and parts[1].isdigit():
        return f"post-{blog}-{int(parts[1])}" if blog in BLOGS else "sys-blogauto"
    if head == "hot":
        return f"post-issue-{int(pid)}" if pid else "blog-issue"
    if head in ("login", "kz", "ownstats", "login_down", "own_stats_block") and len(parts) >= 2 and parts[1] in BLOGS:
        return f"conn-naver-{parts[1]}"
    if head == "kakao":
        return "conn-kakao"
    if head == "mall_login" and len(parts) == 2 and re.fullmatch(r"[a-z0-9]{1,20}", mall := parts[1].split("_", 1)[0]):
        return f"conn-mall-{mall}"  # 쇼핑몰 로그인·사람 확인 (mall_login:ably_cf → 에이블리) — 몰마다 한 줄 (주인 10/25)
    if head == "scan":
        return "blog-finance-scan"
    if head == "cq" and len(parts) >= 2 and parts[1] in BLOGS:
        return f"blog-{parts[1]}-comments"
    if head in ("buddy", "stalled", "waiting") and len(parts) == 2 and parts[1] in BLOGS:
        return f"blog-{parts[1]}-" + {"buddy": "buddy", "stalled": "stall", "waiting": "waiting"}[head]
    return "sys-dealbot" if src == "dealbot" else "sys-blogauto"


def post_iid(blog: str, pid: int) -> str:
    return f"post-{blog}-{int(pid)}"


def deal_iid(queue_id: int) -> str:
    return f"deal-{int(queue_id)}"


def digest_iid(day: date | datetime, slot: str) -> str:
    return f"digest-{day:%Y%m%d}-{'am' if slot == 'am' else 'pm'}"


def ok(iid: str) -> bool:
    return bool(IID_RE.match(iid or ""))


def deal_of(iid: str) -> int | None:
    m = _DEAL_IID.match(iid or "")
    return int(m.group(1)) if m else None


def post_of(iid: str) -> tuple[str, int] | None:
    m = _POST_IID.match(iid or "")
    return (m.group(1), int(m.group(2))) if m else None


def digest_of(iid: str) -> tuple[str, str] | None:
    """'digest-20261018-am' → ('20261018', 'am')."""
    m = _DIGEST_IID.match(iid or "")
    return (m.group(1), m.group(2)) if m else None
