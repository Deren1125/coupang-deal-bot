"""구글 검색 등록 체크리스트 (주인 10/18 22:20 — "매일 블로그마다 새로 발행된 글 링크를 모아서 체크리스트로").

네이버 블로그 글을 구글에 알릴 길은 키자드(keyzard.cc/nb)에 글 주소를 넣는 것뿐인데, 서버에서 자동으로 넣으면 Cloudflare 사람 확인에
막힌다 → 주인이 직접: 줄마다 '복사하기' → 붙여넣기 → '등록함'. 다음(Daum)은 블로그를 한 번 등록해 두면 RSS 로 알아서.

- 글은 Blog-Auto 기록 파일 v2 recent (블로그마다 최근 발행 10편) — 읽을 때마다 딜봇 kv(dash_search_reg)에 모아 둔다
  (10편보다 많이 발행한 날도 빠지지 않게). 14일 지난 글은 버린다. Blog-Auto 는 바꾸지 않는다 (딜봇 대시보드만의 표시)
- 키는 PC 주소 (https://blog.naver.com/…) — 키자드에 넣는 주소와 같게
- Blog-Auto 가 그 글의 구글 등록을 마쳤다고 하면 (recent[].google_done — 자동 등록을 켠 경우) 저절로 '등록함'
- 표시는 확인 창 없이 바로 (다시 누르면 되돌림)
- 하루 5개 (주인 10/25 — "키자드는 블로그 아이디마다 하루 5개만 · 5개 넘게 올린 블로그는 가장 중요한·핫한 글 5개만 띄워 · 맨날 놓쳐"):
  블로그마다 오늘 '등록함' 누른 수를 빼고 남은 몫만큼, 최근 FRESH_DAYS 일 안의 아직 안 한 글 중 점수(Blog-Auto recent[].score —
  인기 예상) 높은 순 → 새 글 순으로 골라 보여 준다. 고르지 않은 글은 숨긴다 (센 수에서도 빠짐). 할 일 줄은 맨 위 쪽.
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
PER_DAY = 5  # 키자드: 블로그 아이디마다 하루 등록 수
FRESH_DAYS = 3  # 이 날 수 안에 발행한 글만 (오래된 글은 구글 등록 효과가 적다)


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
                if isinstance(r.get("score"), int | float) and not isinstance(r.get("score"), bool):
                    row["score"] = float(r["score"])
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
                if done:
                    row["done_at"] = self.now()
                else:
                    row.pop("done_at", None)
                n += 1
        if n:
            self._save(rows)
        return n

    def view(self, v2: dict[str, Any] | None) -> dict[str, Any]:
        """화면 값: 블로그마다 묶은 글 목록 blogs (화면은 이것) · 날짜마다 days (새 날 먼저, 새 글 먼저) + 아직 등록 안 한 글 수. 블로그마다 오늘 남은 몫(PER_DAY − 오늘 등록함)만큼
        최근 FRESH_DAYS 일 글 중 점수 높은 순으로 골라 보이고, 오늘 등록함 누른 글은 그대로 보인다 (되돌릴 수 있게)."""
        rows = self.sync(v2)
        tz = ZoneInfo(self.tz)
        now = self.now()
        today = datetime.fromtimestamp(now, tz).date()
        t0 = datetime.combine(today, datetime.min.time(), tz).timestamp()
        fresh = t0 - (FRESH_DAYS - 1) * 86400
        shown: set[str] = set()
        per_blog: dict[str, int] = {}
        for blog in CHIPS:
            mine = {u: r for u, r in rows.items() if r.get("blog") == blog}
            done_today = [u for u, r in mine.items() if (r.get("done") is True and float(r.get("done_at") or 0) >= t0)
                          or (r.get("auto") is True and float(r.get("at") or 0) >= t0)]  # 자동 등록(Blog-Auto)된 오늘 글도 몫에 셈
            shown.update(done_today)
            left = max(0, PER_DAY - len(done_today))
            cands = sorted(((u, r) for u, r in mine.items() if not (r.get("done") is True or r.get("auto") is True)
                            and float(r.get("at") or 0) >= fresh), key=lambda kv: (-float(kv[1].get("score") or 0), -kv[1]["at"]))
            pick = [u for u, _ in cands[:left]]
            shown.update(pick)
            if pick:
                per_blog[CHIPS[blog]] = len(pick)
        days: dict[date, list[dict[str, Any]]] = {}
        by_blog: dict[str, list[dict[str, Any]]] = {}
        for url, r in sorted(rows.items(), key=lambda kv: (-kv[1]["at"], kv[0])):
            if url not in shown:
                continue
            d = datetime.fromtimestamp(r["at"], tz).date()
            auto = r.get("auto") is True
            x = {"url": url, "blog": r.get("blog") or "", "chip": CHIPS.get(r.get("blog") or "", ""), "title": r.get("title") or url,
                 "at": r["at"], "day": day_label(d, today), "score": r.get("score"), "done": auto or r.get("done") is True, "auto": auto}
            days.setdefault(d, []).append(x)
            by_blog.setdefault(x["blog"], []).append(x)
        groups = [{"day": d.isoformat(), "label": day_label(d, today), "items": xs,
                   "left": sum(not x["done"] for x in xs)} for d, xs in sorted(days.items(), reverse=True)]
        # 블로그마다 한 묶음 (주인 10/25 '같은 블로그끼리 묶어줘' — 키자드는 블로그 아이디마다 넣으니까): 아카이브·Pick·핫딜 순,
        # 묶음 안은 안 한 글 먼저 → 점수 높은 순 → 새 글 순
        blogs = [{"blog": b, "label": CHIPS[b], "left": sum(not x["done"] for x in xs),
                  "items": sorted(xs, key=lambda x: (x["done"], -float(x.get("score") or 0), -x["at"]))}
                 for b in CHIPS if (xs := by_blog.get(b))]
        return {"keyzard": KEYZARD, "left": sum(g["left"] for g in groups), "days": groups, "blogs": blogs,
                "per_blog": per_blog, "per_day": PER_DAY}
