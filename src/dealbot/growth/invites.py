"""텔레그램 채널 초대 링크별 가입 경로 (주인 결정 10/15, 2단계).

- /invitelinks (주인이 보낼 때만): 공식 Bot API createChatInviteLink 로 이름 붙은 링크를 만든다
  (ig_bio · threads_bio · deals_page · demiyum · qr). 한 번 만든 링크는 기억해 다시 쓴다. 봇이 채널 관리자이고
  '링크로 초대' 권한이 있어야 한다 — 없으면 켜는 법을 답한다.
- 가입 세기: chat_member 업데이트(allowed_updates 에 CHAT_MEMBER)에서 그 사람이 어느 초대 링크로 들어왔는지만 본다.
  공개 주소(@채널이름)로 들어온 사람은 링크가 없어 '출처 모름'. 날짜·링크 이름·수만 남긴다 (누가 들어왔는지는 안 남김).
- Blog-Auto 에 넘기기 (주인 10/16): 기억한 링크를 데이터 폴더(published_deals.jsonl 옆)의 channel_links.json 에 쓴다
  (켜질 때와 /invitelinks 뒤). 핫딜 블로그(demiyum) 서명이 공개 주소 대신 demiyum 링크를 써서 블로그 독자 가입도 센다.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

INVITE_NAMES = ("ig_bio", "threads_bio", "deals_page", "demiyum", "qr")
UNKNOWN = "unknown"
OTHER = "other"
LABELS = {
    "ig_bio": "인스타 프로필",
    "threads_bio": "스레드 프로필",
    "deals_page": "핫딜 페이지",
    "demiyum": "블로그",
    "qr": "QR",
    UNKNOWN: "출처 모름",
    OTHER: "다른 초대 링크",
}
KV_PREFIX = "invite_link:"
EXPORT_FILE = "channel_links.json"
# 넘기는 주소는 텔레그램 https://t.me/ 주소만 (공개 주소 https://t.me/채널이름 · 초대 링크 https://t.me/+… )
TME_RE = re.compile(r"^https://t\.me/[A-Za-z0-9_+/-]+$")
JOINED = ("member", "administrator", "creator")
LEFT = ("left", "kicked")

GRANT_HELP = (
    "봇이 채널 관리자이고 <b>'링크로 초대'(초대 링크 만들기)</b> 권한이 있어야 해요.\n"
    "텔레그램 채널 → 채널 정보 → 관리자 → 핫딜봇 → '사용자 초대(링크로 초대)' 를 켠 뒤 <code>/invitelinks</code> 를 다시 보내 주세요."
)


def stored_links(db: Any) -> dict[str, str]:
    out = {}
    for name in INVITE_NAMES:
        url = db.kv_get(f"{KV_PREFIX}{name}")
        if url:
            out[name] = url
    return out


def export_links(db: Any, data_dir: Path, public_url: str = "", now: datetime | None = None) -> Path | None:
    """기억한 초대 링크를 Blog-Auto 가 읽는 channel_links.json 에 (임시 파일에 쓴 뒤 바꿔치기 — 읽는 쪽이 반쯤 쓴 파일을 보지 않게).
    모양: {"v": 1, "telegram_public": "https://t.me/채널", "invites": {"demiyum": "https://t.me/+…", …}, "updated_at": "…"}
    INVITE_NAMES 의 이름·https://t.me/ 주소만 넣는다. 쓴 파일 경로, 못 썼으면 None (봇은 그대로 돈다)."""
    links = {name: url for name, url in stored_links(db).items() if TME_RE.match(url.strip())}
    public = (public_url or "").strip()
    row = {"v": 1, "telegram_public": public if TME_RE.match(public) else "", "invites": links,
           "updated_at": (now or datetime.now().astimezone()).isoformat(timespec="seconds")}
    path = Path(data_dir) / EXPORT_FILE
    tmp = path.with_name(f".{EXPORT_FILE}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)  # 초대 링크는 주인만 (Blog-Auto 는 같은 사용자로 돈다)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("channel links export failed: %s", type(e).__name__)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return path


async def create_links(bot: Any) -> str:
    """이름 붙은 초대 링크를 만들거나(처음) 기억한 것을 보여 준다. 주인에게 보낼 답 (HTML)."""
    tg, channel = bot.bot, bot.publisher.channel_id
    if tg is None or channel is None:
        return "텔레그램 봇과 채널(TELEGRAM_CHANNEL_ID)이 연결되어 있어야 초대 링크를 만들 수 있어요."
    links = stored_links(bot.db)
    problem = ""
    for name in INVITE_NAMES:
        if name in links:
            continue
        try:
            link = await tg.create_chat_invite_link(chat_id=channel, name=name)
        except Exception as e:  # noqa: BLE001 — 권한 없음(BadRequest·Forbidden)·네트워크: 켜는 법을 답한다
            log.warning("invite link %s not created: %s", name, type(e).__name__)
            problem = f"⚠️ 초대 링크를 만들지 못했어요 ({html.escape(type(e).__name__)}: {html.escape(str(e)[:120])})\n{GRANT_HELP}"
            break
        url = str(getattr(link, "invite_link", "") or "")
        if not url:
            continue
        bot.db.kv_set(f"{KV_PREFIX}{name}", url)
        links[name] = url
    lines = [f"· {LABELS[n]} ({n}): {html.escape(links[n])}" for n in INVITE_NAMES if n in links]
    head = "🔗 <b>채널 초대 링크</b> — 어디서 들어왔는지 세려고 자리마다 다른 링크예요"
    use = ("인스타·스레드 프로필 소개(바이오)엔 ig_bio·threads_bio, 블로그엔 demiyum, 인쇄물엔 qr 링크를 넣어 주세요. "
           "핫딜 페이지(/)의 텔레그램 버튼은 deals_page 링크를 써요. 가입 수는 대시보드 텔레그램 카드에 나와요 "
           "(공개 주소 @채널로 들어온 사람은 '출처 모름').")
    parts = [head] + (lines or ["(아직 만든 링크가 없어요)"]) + ["", use]
    if problem:
        parts += ["", problem]
    return "\n".join(parts)


def classify_join(update: Any, channel_id: Any, known: dict[str, str]) -> str | None:
    """chat_member 업데이트 → 가입한 링크 이름 (우리 채널에 새로 들어온 경우만). 아니면 None.
    known: {초대 링크 주소: 이름} (이름이 비었거나 다른 링크일 때 주소로 찾음)."""
    cm = getattr(update, "chat_member", None)
    if cm is None:
        return None
    chat = getattr(cm, "chat", None)
    cid = getattr(chat, "id", None)
    uname = getattr(chat, "username", None)
    target = str(channel_id or "")
    if not (str(cid) == target or (uname and target.lstrip("@").lower() == str(uname).lower())):
        return None
    old = getattr(getattr(cm, "old_chat_member", None), "status", None)
    new = getattr(getattr(cm, "new_chat_member", None), "status", None)
    if str(old) not in LEFT or str(new) not in JOINED:
        return None
    link = getattr(cm, "invite_link", None)
    if link is None:
        return UNKNOWN
    name = str(getattr(link, "name", "") or "")
    if name in INVITE_NAMES:
        return name
    return known.get(str(getattr(link, "invite_link", "") or ""), OTHER)


def record_join(db: Any, update: Any, channel_id: Any, tz: str, now: datetime) -> str | None:
    known = {url: name for name, url in stored_links(db).items()}
    name = classify_join(update, channel_id, known)
    if name is None:
        return None
    db.invite_join_add(now.astimezone(ZoneInfo(tz)).date().isoformat(), name)
    return name


def joins_summary(db: Any, today: Any, days: int = 7) -> tuple[int, str]:
    """최근 N일 가입 수와 '인스타 프로필 2 · 출처 모름 3' 줄 (많은 순)."""
    since = (today - timedelta(days=days - 1)).isoformat()
    counts = db.invite_joins_since(since)
    total = sum(counts.values())
    parts = [f"{LABELS.get(k, k)} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if v]
    return total, " · ".join(parts)
