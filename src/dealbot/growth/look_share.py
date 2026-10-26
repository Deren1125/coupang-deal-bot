"""두 번째 인스타 '데런픽 룩' @derenpick.look (주인 10/26) — 데런픽 패션 글 캐러셀.

- 연결: 같은 메타 앱에 인스타 테스터로 넣은 뒤 대시보드 연결 탭 '데런픽 룩 연결' (publisher/instagram 의 같은 승인 흐름 —
  state 앞 'look.' 으로 계정 구분, 토큰은 instagram_look_* kv). 연결 전에는 대기열이 기다리기만 하고, 대시보드 할 일에
  '데런픽 룩 인스타 연결 필요' 한 줄 (알림·카톡 없음 — 상태로만 보이고 연결하면 저절로 빠짐).
- 글: Blog-Auto(look_share.py)가 패션 글마다 var/look_share/<글번호>.json + 상품 사진 사본(<글번호>/NN.jpg)을 쓴다:
  {kind: look, pid, title, url, person, caption, deck: {kicker, title[2], sub, items[{name, price, mall, piece, same, image}], …},
   post_after, cancelled}. 셀럽 사진은 들어 있지 않다 (상품 사진만).
- 올리기 (ig_scheduler.tick 이 1분마다 — 딜 계정 글과 따로): 연결돼 있을 때만, 인스타 시간대(instagram.look.hours — 비우면
  instagram.hours) 안에서 하루 per_day(2)편까지, 데런픽 룩 글 사이 min_gap_hours(3)시간 넘게. post_after 가 지난 것부터.
  expire_hours(48) 넘게 기다린 글은 안 올림. 카드는 media/card_look (모든 장 '광고' 칩), 올리기 전 검사(check) — 캡션 첫 줄 고지 ·
  주소 글자 없음 · 해시태그 5개까지 #광고 먼저 · 'N가지' = 상품 카드 수 · 사진은 그 글 폴더의 상품 사진만.
- 기록: kv look_share:ig:<글번호> (posting · posted · failed · skipped: 이유) — 같은 글은 한 번만. 올린 글은 ig_posts(kind 'look') —
  딜 계정 간격·하루 수·통계에는 세지 않는다. 실패하면 데런픽 룩만 30분 쉼 (딜 계정은 그대로) · 기록 kind 'instagram_look'
  (연결 탭 '데런픽 룩 인스타' 줄에 '최근 실패 있음' + 다시 연결 — 푸시 없음).
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dealbot.models import PublishResult
from dealbot.utils.timeutil import from_iso, in_time_window, to_iso

log = logging.getLogger(__name__)

LOOK_IID = "sns-look"
TODO_TITLE = "데런픽 룩 인스타 연결 필요"
KIND = "look"  # ig_posts.kind
KV_DONE = "look_share:ig:"
KV_BACKOFF = "ig_look_backoff_until"
FAIL_BACKOFF = timedelta(minutes=30)
EVENT = "instagram_look"  # log_event kind (publisher/instagram.account_keys('look').platform 과 같음)
DISCLOSURES = ("광고 · 제휴 링크로 수수료를 받아요", "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다")
URL_RX = re.compile(r"https?://|www\.|\b[\w-]+\.(?:com|net|kr|co|me|ly|io)\b|naver", re.I)
TAGS_MAX = 5
CAPTION_MAX = 2200


def look_wanted(bot: Any) -> bool:
    """데런픽 룩 계정을 쓰는 설정인가 (인스타·룩 켜짐 + 메타 앱 키 있음)."""
    s = bot.settings
    return bool(s.instagram.enabled and s.instagram.look.enabled and s.secrets.has_instagram_app)


def look_connected(bot: Any) -> bool:
    pub = getattr(bot, "instagram_look", None)
    return pub is not None and pub.stored_token() is not None


def share_dir(metrics_path: str) -> Path:
    """Blog-Auto var/look_share (활동 기록 파일 var/metrics/blogauto.json 옆)."""
    return Path(metrics_path).parent.parent / "look_share"


def load(path: Path) -> dict[str, Any] | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("pid") and rec.get("kind") == KIND else None


def _when(v: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.astimezone()


def due(folder: Path, db: Any, now: datetime) -> list[dict[str, Any]]:
    """올릴 차례인 글 (post_after 가 지남 · 취소 안 됨 · 아직 안 올림) — 먼저 차례가 된 것부터."""
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("*.json"):
        rec = load(p)
        if rec is None or rec.get("cancelled") or db.kv_get(f"{KV_DONE}{rec['pid']}"):
            continue
        after = _when(rec.get("post_after"))
        if after is None or now < after:
            continue
        rec["_after"] = after
        rec["_dir"] = str(p.with_suffix(""))
        out.append(rec)
    return sorted(out, key=lambda r: r["_after"])


def waiting_count(bot: Any) -> int:
    """올릴 차례를 기다리는 (아직 안 올린) 데런픽 룩 글 수 — 할 일 줄 '패션 글 N편이 기다려요'."""
    try:
        folder = share_dir(bot.settings.dashboard.blogauto_metrics_path)
        if not folder.is_dir():
            return 0
        n = 0
        for p in folder.glob("*.json"):
            rec = load(p)
            if rec is not None and not rec.get("cancelled") and not bot.db.kv_get(f"{KV_DONE}{rec['pid']}"):
                n += 1
        return n
    except Exception:  # noqa: BLE001 — 셀 수 없으면 0 (할 일 줄은 그대로)
        return 0


def todo_item(bot: Any, now: float, goto: Callable[..., dict[str, Any]], *, waiting: int = 0) -> dict[str, Any] | None:
    """할 일 한 줄 — 룩 계정을 쓰는데 아직 연결이 없을 때만. waiting: 올릴 차례를 기다리는 패션 글 수 (있으면 상태 줄에)."""
    if not look_wanted(bot) or look_connected(bot):
        return None
    handle = bot.settings.instagram.look.username.lstrip("@")
    status = f"패션 글 {waiting}편이 기다려요 · " if waiting else ""
    status += f"@{handle} 를 메타 앱 인스타 테스터로 넣고 → 연결 탭 '데런픽 룩 연결'"
    return {"iid": LOOK_IID, "src": "dealbot", "blog": "", "kind": "sns_look", "sev": "todo", "urgent": False,
            "title": TODO_TITLE, "status": status, "since": None, "expires_at": None,
            "primary": goto("연결 탭 열기", "#/connect"), "more": [], "detail": {"lines": []}}


# ---------------------------------------------------------------- 카드·검사
def deck_of(rec: dict[str, Any], fonts: dict[str, str] | None = None) -> Any:
    from dealbot.media.card_look import LookDeck, LookItem

    d = rec.get("deck") or {}
    items = [LookItem(name=str(x.get("name") or "")[:60], price=int(x.get("price") or 0), mall=str(x.get("mall") or "")[:20],
                      image=str(x["image"]) if x.get("image") else None, piece=str(x.get("piece") or "")[:20], same=bool(x.get("same")))
             for x in d.get("items") or [] if isinstance(x, dict) and str(x.get("name") or "").strip()]
    return LookDeck(person=str(d.get("person") or rec.get("person") or "")[:20], kicker=str(d.get("kicker") or "")[:40],
                    title=[str(t)[:20] for t in (d.get("title") or [])][:2], sub=str(d.get("sub") or "")[:40], items=items,
                    end_line=str(d.get("end_line") or "전체 글은 프로필 링크에서")[:40], price_note=str(d.get("price_note") or "")[:40],
                    price_day=str(d.get("price_day") or "")[:8], end_kicker=str(d.get("end_kicker") or "한눈에 정리")[:40],
                    fonts=dict(fonts or {}))


def check(rec: dict[str, Any], deck: Any) -> list[str]:
    """올리기 전 검사 (하나라도 걸리면 안 올림 — 기록만)."""
    probs = []
    cap = str(rec.get("caption") or "")
    lines = cap.split("\n")
    if not cap or lines[0] not in DISCLOSURES:
        probs.append("캡션 첫 줄이 광고 고지가 아님")
    if URL_RX.search(cap):
        probs.append("캡션에 주소 글자")
    tags = re.findall(r"#(\S+)", cap)
    if not tags or tags[0] != "광고" or len(tags) > TAGS_MAX:
        probs.append("해시태그 (#광고 먼저 · 5개까지)")
    if len(cap) > CAPTION_MAX:
        probs.append("캡션이 너무 김")
    n = len(deck.items)
    if not 2 <= n <= 4:
        probs.append(f"상품 카드 {n}장 (2~4장)")
    if f"{n}가지" not in "".join(deck.title):
        probs.append("표지 'N가지' 와 상품 카드 수가 다름")
    folder = Path(str(rec.get("_dir") or ""))
    for it in deck.items:  # 사진은 그 글 폴더의 상품 사진 사본만 (셀럽 사진이 끼어들 길을 막음)
        if it.image and (not folder.name or Path(it.image).resolve().parent != folder.resolve()):
            probs.append("상품 사진이 글 폴더 밖에 있음")
            break
    if sum(1 for it in deck.items if it.image and Path(it.image).exists()) < 2:
        probs.append("상품 사진 2장 미만")
    return probs


# ---------------------------------------------------------------- 올리기
def _cfg(bot: Any) -> Any:
    return bot.settings.instagram.look


def _day_start(local: datetime) -> datetime:
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def tick(sched: Any, now: datetime) -> str | None:
    """1분마다 (ig_scheduler.tick): 올릴 차례면 데런픽 룩 글 하나. 한 일을 짧게 (없으면 None)."""
    bot, db = sched.bot, sched.db
    pub = getattr(bot, "instagram_look", None)
    if pub is None or not look_wanted(bot) or not pub.enabled:
        return None
    if pub.stored_token() is None:
        return None  # 연결 전 — 기다리기만 (연습 모드여도 글을 '올림'으로 써 버리지 않게 · 할 일 줄은 대시보드)
    cfg = _cfg(bot)
    local = now.astimezone(sched.tz)
    if not in_time_window(local, cfg.hours or sched.cfg.hours):
        return None
    backoff = from_iso(db.kv_get(KV_BACKOFF))
    if backoff and now < backoff:
        return None
    posted = db.ig_posts_since(_day_start(local), kind=KIND)
    if len(posted) >= cfg.per_day:
        return None
    last = db.ig_last_post_at(kind=KIND)
    if last is not None and now - last < timedelta(hours=cfg.min_gap_hours):
        return None
    recs = due(share_dir(bot.settings.dashboard.blogauto_metrics_path), db, now)
    if not recs:
        return None
    status = await post_one(sched, recs[0], now)
    log.info("instagram look %s", status)
    return status


async def post_one(sched: Any, rec: dict[str, Any], now: datetime) -> str:
    db, bot = sched.db, sched.bot
    key = f"{KV_DONE}{rec['pid']}"
    what = f"look #{rec['pid']}"
    if now - rec["_after"] > timedelta(hours=_cfg(bot).expire_hours):
        db.kv_set(key, "skipped: too late")
        return f"{what}: skipped (too late)"
    base = bot.settings.secrets.public_base_url
    if not base:
        db.kv_set(key, "skipped: no public url")
        return f"{what}: skipped (no public url)"
    from dealbot.media.card_look import FontMissing, render

    deck = deck_of(rec)
    probs = check(rec, deck)
    if probs:
        db.kv_set(key, f"skipped: {' · '.join(probs)}"[:300])
        db.log_event("INFO", EVENT, f"{what} skipped: {' · '.join(probs)}"[:500])
        return f"{what}: skipped ({' · '.join(probs)})"
    try:
        slides = render(deck)
    except (FontMissing, ValueError, OSError) as e:
        db.kv_set(key, f"skipped: cards {type(e).__name__}")
        return f"{what}: skipped (cards {type(e).__name__})"
    db.kv_set(key, "posting")  # 올리다 끊겨도(재시작) 두 번 올리지 않게
    sched.media_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    try:
        images = []
        for jpg, alt in slides:
            out = sched.media_dir / f"look_{secrets.token_hex(8)}.jpg"
            out.write_bytes(jpg)
            made.append(out)
            images.append((f"{base}/media/{out.name}", alt))
        result = await bot.instagram_look.publish_carousel(images, str(rec.get("caption") or ""))
    except Exception as e:  # noqa: BLE001
        result = PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        for p in made:
            p.unlink(missing_ok=True)
    if result.ok:
        media = "dry-run" if result.dry_run else str(result.message_id or "")
        item = {"look_pid": rec["pid"], "title": rec.get("title"), "url": rec.get("url")}
        row = db.ig_record_post(KIND, media_id=media or None, items=[item], dry_run=result.dry_run, now=now)
        await _keep_permalink(bot, db, row, result)
        db.kv_set(key, f"posted {media} {to_iso(now)}")
        db.log_event("INFO", EVENT, f"{what} posted ({len(slides)} cards)")
        return f"{what}: posted {media}"
    db.kv_set(key, f"failed: {result.error}"[:300])
    db.log_event("WARNING", EVENT, f"{what}: {result.error}"[:500])
    db.kv_set(KV_BACKOFF, to_iso(now + FAIL_BACKOFF))
    return f"{what}: failed {result.error}"


async def _keep_permalink(bot: Any, db: Any, row: int, result: PublishResult) -> None:
    """올린 글 주소 (데런픽 룩 토큰으로 읽음). 못 읽어도 그대로."""
    pub = getattr(bot, "instagram_look", None)
    if result.dry_run or not row or result.message_id is None or pub is None:
        return
    try:
        token = await pub.ensure_fresh()
        if token is None:
            return
        link = await pub.client.permalink(token, str(result.message_id))
        if link:
            db.ig_set_permalink(row, link)
    except Exception as e:  # noqa: BLE001
        log.info("instagram look permalink not kept for post %s: %s", row, e)
