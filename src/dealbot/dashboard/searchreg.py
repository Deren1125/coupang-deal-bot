"""구글 검색 등록 체크리스트 (주인 10/18 22:20 — "매일 블로그마다 새로 발행된 글 링크를 모아서 체크리스트로").

네이버 블로그 글을 구글에 알릴 길은 키자드(keyzard.cc/nb)에 글 주소를 넣는 것뿐인데, 서버에서 자동으로 넣으면 Cloudflare 사람 확인에
막힌다 → 주인이 직접: 줄마다 '복사하기' → 붙여넣기 → '등록함'. 다음(Daum)은 블로그를 한 번 등록해 두면 RSS 로 알아서.

- 글은 Blog-Auto 기록 파일 v2 recent (블로그마다 최근 발행 10편) — 읽을 때마다 딜봇 kv(dash_search_reg)에 모아 둔다
  (10편보다 많이 발행한 날도 빠지지 않게). 14일 지난 글은 버린다. Blog-Auto 는 바꾸지 않는다 (딜봇 대시보드만의 표시)
- 키는 PC 주소 (https://blog.naver.com/…) — 키자드에 넣는 주소와 같게
- Blog-Auto 가 그 글의 구글 등록을 마쳤다고 하면 (recent[].google_done — 자동 등록을 켠 경우) 저절로 '등록함'
- 표시는 확인 창 없이 바로 (다시 누르면 되돌림)
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

KV = "dash_search_reg"
KEEP_DAYS = 14
MAX_ROWS = 600  # 블로그 셋 × 하루 몇 편 × 14일 — 넘으면 오래된 것부터 버림
KEYZARD = "https://keyzard.cc/nb"
CHIPS = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
WEEK = "월화수목금토일"


def pc_url(url: str) -> str:
    return url.replace("https://m.blog.naver.com/", "https://blog.naver.com/")


def _ts(v: Any, tz: str) -> float | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))).timestamp()


def day_label(d: date, today: date) -> str:
    if d == today:
        return "오늘"
    if d == today - timedelta(days=1):
        return "어제"
    return f"{d.month}/{d.day} ({WEEK[d.weekday()]})"


class SearchReg:
    """db = 딜봇 Database (kv). now = 지금 (초)."""

    def __init__(self, db: Any, *, tz: str, now: Callable[[], float]) -> None:
        self.db = db
        self.tz = tz
        self.now = now

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            got = json.loads(self.db.kv_get(KV) or "{}")
        except ValueError:
            return {}
        return {k: v for k, v in got.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(got, dict) else {}

    def _save(self, rows: dict[str, dict[str, Any]]) -> None:
        self.db.kv_set(KV, json.dumps(rows, ensure_ascii=False, sort_keys=True))

    def _start(self) -> float:
        """남길 첫날 0시 (오늘 포함 14일)."""
        d = datetime.fromtimestamp(self.now(), ZoneInfo(self.tz)).replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - timedelta(days=KEEP_DAYS - 1)).timestamp()

    def sync(self, v2: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """기록 파일 recent → 모아 둔 표에 더하고 (제목·자동 등록은 새 값으로) 14일 지난 것은 버린다. 바뀌었을 때만 쓴다."""
        rows = self._load()
        before = json.dumps(rows, sort_keys=True)
        now = self.now()
        recent = (v2 or {}).get("recent") or {}
        for blog in CHIPS:
            for r in recent.get(blog) or []:
                url = r.get("url") if isinstance(r, dict) else None
                if not isinstance(url, str) or not url:
                    continue
                key = pc_url(url)
                at = _ts(r.get("published_at") or r.get("at"), self.tz)
                row = rows.setdefault(key, {"blog": blog, "title": "", "at": at or now, "done": False})
                row["blog"] = blog
                row["title"] = str(r.get("title") or row.get("title") or "")[:80]
                if at is not None:
                    row["at"] = at
                if r.get("google_done") is True:
                    row["auto"] = True
        start = self._start()
        rows = {k: v for k, v in rows.items() if isinstance(v.get("at"), int | float) and v["at"] >= start}
        if len(rows) > MAX_ROWS:
            rows = dict(sorted(rows.items(), key=lambda kv: -kv[1]["at"])[:MAX_ROWS])
        if json.dumps(rows, sort_keys=True) != before:
            try:
                self._save(rows)
            except Exception as e:  # noqa: BLE001 — 못 써도 화면은 보인다 (다음에 다시)
                log.warning("search checklist save failed: %s", type(e).__name__)
        return rows

    def check(self, urls: list[str], done: bool) -> int:
        """'등록함' 표시 (done=False 면 되돌림). 표에 있는 글만 — 바꾼 글 수."""
        rows = self._load()
        n = 0
        for u in urls:
            row = rows.get(pc_url(u))
            if row is not None:
                row["done"] = done
                n += 1
        if n:
            self._save(rows)
        return n

    def view(self, v2: dict[str, Any] | None) -> dict[str, Any]:
        """화면 값: 날짜마다 (새 날 먼저) 글 목록 (새 글 먼저) + 아직 등록 안 한 글 수."""
        rows = self.sync(v2)
        tz = ZoneInfo(self.tz)
        today = datetime.fromtimestamp(self.now(), tz).date()
        days: dict[date, list[dict[str, Any]]] = {}
        for url, r in sorted(rows.items(), key=lambda kv: (-kv[1]["at"], kv[0])):
            d = datetime.fromtimestamp(r["at"], tz).date()
            auto = r.get("auto") is True
            days.setdefault(d, []).append({"url": url, "blog": r.get("blog") or "", "chip": CHIPS.get(r.get("blog") or "", ""),
                                           "title": r.get("title") or url, "at": r["at"], "done": auto or r.get("done") is True,
                                           "auto": auto})
        groups = [{"day": d.isoformat(), "label": day_label(d, today), "items": xs,
                   "left": sum(not x["done"] for x in xs)} for d, xs in sorted(days.items(), reverse=True)]
        return {"keyzard": KEYZARD, "left": sum(g["left"] for g in groups), "days": groups}
