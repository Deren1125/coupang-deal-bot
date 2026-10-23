"""데런픽 글 → 인스타 (주인 10/23 — '인스타·스레드에 쇼핑 글만 올리면 안 되니까, 데런픽 패션 아닌 글을 하루 한 편 가공해서').

Blog-Auto(sns_share.py)가 글마다 var/sns_share/<글번호>.json 을 쓴다:
  {pid, title, url, post_after, ig_caption, photos: [블로그 글에 넣은 사진 파일], credits, cancelled}
인스타 대기열(ig_scheduler.tick)이 1분마다 여기를 본다 — post_after 가 지났고 취소되지 않았고 아직 안 올린 글을
인스타 시간대 안에서, 인스타 글 사이 간격(gap_after)을 지켜 캐러셀(사진 2장 이상) 또는 사진 한 장 글로 올린다.
사진은 4:5~1.91:1 안으로 맞춰(세로가 너무 길면 위쪽을 남기고 자름) 공개 폴더(media)에 두고 올린 뒤 지운다.
스레드는 Blog-Auto 가 자기 스레드 예약 줄로 올린다 (여기서는 인스타만). 스레드 예약을 취소하면 cancelled 가 되어 인스타도 안 올림.
올린 기록: kv blog_share:ig:<글번호> (posted · failed · skipped: 이유) — 같은 글은 한 번만 (실패해도 다시 올리지 않음).
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from dealbot.models import PublishResult
from dealbot.utils.timeutil import to_iso

log = logging.getLogger(__name__)

KV_DONE = "blog_share:ig:"
MAX_AGE = timedelta(hours=30)  # post_after 뒤 이만큼 지나도 못 올렸으면 (지난 소식) 건너뜀
RATIO_MIN, RATIO_MAX = 0.8, 1.91  # 인스타 사진 가로:세로 허용 범위
MAX_SIDE = 1440


def share_dir(metrics_path: str) -> Path:
    """Blog-Auto var/sns_share (활동 기록 파일 var/metrics/blogauto.json 옆)."""
    return Path(metrics_path).parent.parent / "sns_share"


def load(path: Path) -> dict[str, Any] | None:
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("pid") else None


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
        out.append(rec)
    return sorted(out, key=lambda r: r["_after"])


def _when(v: Any) -> datetime | None:
    """시간대가 붙은 시각은 그대로, 없으면 서버 시각(Blog-Auto 가 적는 방식)으로 읽는다."""
    try:
        dt = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else dt.astimezone()


def fit_jpeg(src: Path) -> bytes:
    """인스타 비율(4:5~1.91:1)로 맞춘 JPEG. 너무 세로로 길면 얼굴이 남게 위쪽을 남기고, 너무 가로로 길면 가운데를 남긴다."""
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        r = w / h
        if r < RATIO_MIN:
            nh = int(w / RATIO_MIN)
            top = int((h - nh) * 0.15)
            im = im.crop((0, top, w, top + nh))
        elif r > RATIO_MAX:
            nw = int(h * RATIO_MAX)
            left = (w - nw) // 2
            im = im.crop((left, 0, left + nw, h))
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=90)
        return buf.getvalue()


async def publish(instagram: Any, urls: list[str], caption: str, alt: str) -> PublishResult:
    """사진 2장 이상은 캐러셀, 1장은 사진 글."""
    if len(urls) >= 2:
        return await instagram.publish_carousel([(u, alt) for u in urls], caption)
    if not instagram.enabled:
        return PublishResult(ok=False, error="instagram disabled")
    if instagram.dry_run:
        log.info("[DRY-RUN] would post a Pick share to instagram:\n%s", caption)
        return PublishResult(ok=True, dry_run=True)
    token = await instagram.ensure_fresh()
    if token is None:
        return PublishResult(ok=False, error="instagram not authorized (/instaauth)")
    try:
        media_id = await instagram.client.post_image(token, urls[0], caption, alt_text=alt)
    except Exception as e:  # noqa: BLE001 — InstagramError 와 연결 오류
        return PublishResult(ok=False, error=str(e))
    return PublishResult(ok=True, message_id=int(media_id) if str(media_id).isdigit() else None)


async def post_one(sched: Any, rec: dict[str, Any], now: datetime) -> str:
    """글 하나를 인스타에 (sched: InstagramScheduler). 한 일을 짧게 돌려준다."""
    db, bot = sched.db, sched.bot
    key = f"{KV_DONE}{rec['pid']}"
    if now - rec["_after"] > MAX_AGE:
        db.kv_set(key, "skipped: too late")
        return f"pick #{rec['pid']}: skipped (too late)"
    files = [Path(p) for p in rec.get("photos") or [] if Path(p).exists()]
    base = bot.settings.secrets.public_base_url
    if not files or not base:
        db.kv_set(key, "skipped: no photo" if not files else "skipped: no public url")
        return f"pick #{rec['pid']}: skipped ({'no photo' if not files else 'no public url'})"
    db.kv_set(key, "posting")  # 올리다 끊겨도(재시작) 두 번 올리지 않게
    sched.media_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    try:
        for f in files[:10]:
            out = sched.media_dir / f"pick_{secrets.token_hex(8)}.jpg"
            out.write_bytes(fit_jpeg(f))
            made.append(out)
        urls = [f"{base}/media/{p.name}" for p in made]
        alt = str(rec.get("title") or "")[:200]
        result = await publish(bot.instagram, urls, str(rec.get("ig_caption") or ""), alt)
    except Exception as e:  # noqa: BLE001
        result = PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
    finally:
        for p in made:
            p.unlink(missing_ok=True)
    if result.ok:
        media = "dry-run" if result.dry_run else str(result.message_id or "")
        item = {"pick_pid": rec["pid"], "title": rec.get("title"), "url": rec.get("url")}
        row = db.ig_record_post("pick", media_id=media or None, items=[item], dry_run=result.dry_run, now=now)
        await sched._keep_permalink(row, result)
        db.sns_log_add("instagram", "posted", reason="pick", now=now)
        db.kv_set(key, f"posted {media} {to_iso(now)}")
        db.log_event("INFO", "instagram", f"pick #{rec['pid']} posted ({len(made)} photos)")
        return f"pick #{rec['pid']}: posted {media}"
    db.kv_set(key, f"failed: {result.error}"[:300])
    db.sns_log_add("instagram", "failed", reason=f"pick: {result.error}", now=now)
    await sched._failed(result, now, what=f"pick #{rec['pid']}")
    return f"pick #{rec['pid']}: failed {result.error}"
