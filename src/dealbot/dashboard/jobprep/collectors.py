"""③ 공고 출처별 읽기 — 모두 로그인 없는 공개 주소. 예절: 호스트마다 fetch_gap_s 간격·동시 1개 · User-Agent 는 설정 그대로 · 쿠키 없음 ·
ETag/If-Modified-Since · 응답 캐시 · 큰 JSON 은 상한(fetch_max_bytes)까지만 받고 스레드에서 파싱 · HTML 은 robots.txt 가 허용한 길만 ·
실패 3번이면 그 출처 24시간 쉼 · 403/사람 확인 화면은 다시 부르지 않고 쉼 (우회·프록시 없음).

parse_* 는 출처 JSON → 같은 꼴 줄 {src, token, src_id, title, loc, url, text, deadline}.
"""

from __future__ import annotations

import asyncio
import hashlib
import html as htmlmod
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "workable": "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{token}/postings",
}
GROWTH_URLS = {
    "defillama": "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyRevenue",
    "coingecko": "https://api.coingecko.com/api/v3/exchanges?per_page=50",
    "yc": "https://yc-oss.github.io/api/companies/all.json",
}
FAIL_MAX = 3
OFF_S = 24 * 3600
CACHE_S = 6 * 3600
TEXT_MAX = 6000
TIMEOUT = 30.0
BLOCK_STATUS = (401, 403, 429, 451)


@dataclass
class Resp:
    status: int
    body: bytes = b""
    cached: bool = False

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmlmod.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


# ── 파서 ──
def parse_greenhouse(data: Any, token: str) -> list[dict[str, Any]]:
    out = []
    for j in (data or {}).get("jobs") or []:
        if not isinstance(j, dict) or j.get("id") is None:
            continue
        out.append({"src": "greenhouse", "token": token, "src_id": str(j["id"]), "title": str(j.get("title") or "")[:200],
                    "loc": str((j.get("location") or {}).get("name") or "")[:120], "url": str(j.get("absolute_url") or ""),
                    "text": strip_html(htmlmod.unescape(str(j.get("content") or "")))[:TEXT_MAX], "deadline": None})
    return out


def parse_lever(data: Any, token: str) -> list[dict[str, Any]]:
    out = []
    for j in data if isinstance(data, list) else []:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        cat = j.get("categories") or {}
        text = "\n".join(str(x or "") for x in (j.get("descriptionPlain"), j.get("additionalPlain")))
        for li in j.get("lists") or []:
            if isinstance(li, dict):
                text += "\n" + str(li.get("text") or "") + "\n" + strip_html(str(li.get("content") or ""))
        out.append({"src": "lever", "token": token, "src_id": str(j["id"]), "title": str(j.get("text") or "")[:200],
                    "loc": str(cat.get("location") or "")[:120], "url": str(j.get("hostedUrl") or ""), "text": text[:TEXT_MAX],
                    "deadline": None, "commitment": str(cat.get("commitment") or "")})
    return out


def parse_ashby(data: Any, token: str) -> list[dict[str, Any]]:
    out = []
    for j in (data or {}).get("jobs") or []:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        loc = str(j.get("location") or "")
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = (loc + " · Remote").strip(" ·")
        out.append({"src": "ashby", "token": token, "src_id": str(j["id"]), "title": str(j.get("title") or "")[:200], "loc": loc[:120],
                    "url": str(j.get("jobUrl") or ""), "text": str(j.get("descriptionPlain") or strip_html(str(j.get("descriptionHtml") or "")))[:TEXT_MAX],
                    "deadline": None, "commitment": str(j.get("employmentType") or "")})
    return out


def parse_workable(data: Any, token: str) -> list[dict[str, Any]]:
    out = []
    for j in (data or {}).get("jobs") or []:
        if not isinstance(j, dict) or not (j.get("shortcode") or j.get("id")):
            continue
        loc = ", ".join(x for x in (str(j.get("city") or ""), str(j.get("country") or "")) if x)
        if j.get("telecommuting"):
            loc = (loc + " · Remote").strip(" ·")
        out.append({"src": "workable", "token": token, "src_id": str(j.get("shortcode") or j.get("id")), "title": str(j.get("title") or "")[:200],
                    "loc": loc[:120], "url": str(j.get("url") or j.get("shortlink") or ""),
                    "text": strip_html(str(j.get("description") or ""))[:TEXT_MAX], "deadline": None})
    return out


def parse_smartrecruiters(data: Any, token: str) -> list[dict[str, Any]]:
    out = []
    for j in (data or {}).get("content") or []:
        if not isinstance(j, dict) or not j.get("id"):
            continue
        loc = j.get("location") or {}
        locs = ", ".join(x for x in (str(loc.get("city") or ""), str(loc.get("country") or "").upper()) if x)
        if loc.get("remote"):
            locs = (locs + " · Remote").strip(" ·")
        out.append({"src": "smartrecruiters", "token": token, "src_id": str(j["id"]), "title": str(j.get("name") or "")[:200], "loc": locs[:120],
                    "url": f"https://jobs.smartrecruiters.com/{token}/{j['id']}", "text": "", "deadline": None})
    return out


PARSERS: dict[str, Callable[[Any, str], list[dict[str, Any]]]] = {
    "greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby, "workable": parse_workable,
    "smartrecruiters": parse_smartrecruiters}


def parse_html_list(html: str, base: str, link_re: str, token: str) -> list[dict[str, Any]]:
    """회사 채용 페이지 HTML 에서 목록 링크 (link_re 에 맞는 href) → 줄. 글은 링크 글자만 (본문은 Gemini 단계에서 안 씀)."""
    out, seen = [], set()
    for m in re.finditer(r'(?is)<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html or ""):
        href, label = m.group(1), strip_html(m.group(2))
        if not re.search(link_re, href) or not label:
            continue
        url = urljoin(base, htmlmod.unescape(href))
        if url in seen:
            continue
        seen.add(url)
        out.append({"src": "html", "token": token, "src_id": hashlib.sha256(url.encode()).hexdigest()[:16], "title": label[:200], "loc": "",
                    "url": url, "text": "", "deadline": None})
    return out


def page_text_hash(html: str) -> tuple[str, list[str]]:
    """지켜보는 회사: 태그·숫자·날짜를 뺀 본문 글 해시 + 줄 목록 (바뀐 줄 보여 주기용)."""
    text = strip_html(html)
    lines = [re.sub(r"\d+", "", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) >= 2]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest(), lines


def parse_defillama(data: Any, top: int = 40) -> list[dict[str, Any]]:
    rows = []
    for p in (data or {}).get("protocols") or []:
        if not isinstance(p, dict):
            continue
        v = p.get("total30d")
        if isinstance(v, (int, float)) and v > 0:
            rows.append({"name": str(p.get("parentProtocol") and p.get("displayName") or p.get("name") or "")[:80], "rev30d": float(v),
                         "cat": str(p.get("category") or "")})
    rows.sort(key=lambda r: -r["rev30d"])
    seen, out = set(), []
    for r in rows:
        k = r["name"].lower()
        if k and k not in seen:
            seen.add(k)
            out.append(r)
        if len(out) >= top:
            break
    return out


def parse_coingecko(data: Any, top: int = 20) -> list[dict[str, Any]]:
    out = []
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("name"):
            out.append({"name": str(e["name"])[:80], "btc24h": float(e.get("trade_volume_24h_btc") or 0), "country": str(e.get("country") or "")})
    out.sort(key=lambda r: -r["btc24h"])
    return out[:top]


def parse_yc(data: Any, *, min_batch_year: int = 2023, top: int = 60) -> list[dict[str, Any]]:
    out = []
    for c in data if isinstance(data, list) else []:
        if not isinstance(c, dict) or not c.get("isHiring"):
            continue
        batch = str(c.get("batch") or "")
        m = re.search(r"(20\d\d)", batch)
        year = int(m.group(1)) if m else 0
        if not m:
            m2 = re.search(r"[SWFX](\d\d)$", batch)
            year = 2000 + int(m2.group(1)) if m2 else 0
        regions = " ".join(str(x) for x in (c.get("regions") or [])) + " " + str(c.get("all_locations") or "")
        if year < min_batch_year or not re.search(r"(?i)korea|seoul|remote", regions):
            continue
        out.append({"name": str(c.get("name") or "")[:80], "batch": batch, "url": str(c.get("website") or ""), "one_liner": str(c.get("one_liner") or "")[:160]})
    return out[:top]


# ── 읽는 쪽 ──
class Fetcher:
    """호스트마다 간격·동시 1개 · 캐시(ETag) · 크기 상한 · robots.txt · 출처별 실패 셈 (kv 'src_state')."""

    def __init__(self, store: Any, cfg: Callable[[], Any], http: Any, ua: str, *, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.store, self.cfg, self.http, self.ua, self.clock, self.sleep = store, cfg, http, ua, clock, sleep
        self.lock = asyncio.Lock()
        self.last: dict[str, float] = {}

    # 출처 쉼
    def state(self) -> dict[str, Any]:
        return self.store.kv_get("src_state", {}) or {}

    def is_off(self, src: str) -> bool:
        s = self.state().get(src) or {}
        return float(s.get("until") or 0) > self.clock()

    def note(self, src: str, ok: bool, why: str = "") -> None:
        st = self.state()
        s = st.get(src) or {"n": 0}
        if ok:
            s = {"n": 0, "ok_at": self.clock(), "why": ""}
        else:
            s["n"] = int(s.get("n") or 0) + 1
            s["why"] = why[:80]
            s["fail_at"] = self.clock()
            if s["n"] >= FAIL_MAX or why.startswith("blocked"):
                s["until"] = self.clock() + OFF_S
                s["n"] = 0
        st[src] = s
        self.store.kv_set("src_state", st)

    def _cache_paths(self, url: str) -> tuple[str, str]:
        h = hashlib.sha256(url.encode()).hexdigest()[:32]
        return f"{h}.json", f"{h}.body"

    async def robots_ok(self, url: str) -> bool:
        sp = urlsplit(url)
        key = f"robots:{sp.netloc}"
        hit = self.store.kv_get(key)
        now = self.clock()
        if not hit or now - float(hit.get("at") or 0) > 86400:
            r = await self.get(f"{sp.scheme}://{sp.netloc}/robots.txt", use_cache=False, max_bytes=512 * 1024)
            hit = {"at": now, "status": r.status, "text": r.text()[:200_000] if r.status == 200 else ""}
            self.store.kv_set(key, hit)
        if hit["status"] in (401, 403) or hit["status"] == 0:
            return False  # 못 읽으면 안 감
        if hit["status"] != 200:
            return True  # 404 등: robots.txt 없음 = 막지 않음
        rp = RobotFileParser()
        rp.parse(hit["text"].splitlines())
        return rp.can_fetch(self.ua, url)

    async def get(self, url: str, *, use_cache: bool = True, max_bytes: int | None = None, accept: str = "application/json") -> Resp:
        if not url.startswith("https://"):
            return Resp(0)
        limit = int(max_bytes or self.cfg().fetch_max_bytes)
        meta_name, body_name = self._cache_paths(url)
        meta = None
        if use_cache:
            raw = self.store.read("cache", meta_name)
            if raw:
                try:
                    meta = json.loads(raw)
                except ValueError:
                    meta = None
            if meta and self.clock() - float(meta.get("at") or 0) < CACHE_S:
                body = self.store.read("cache", body_name)
                if body is not None:
                    return Resp(200, body, True)
        headers = {"User-Agent": self.ua, "Accept": accept}
        if meta and meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta and meta.get("lm"):
            headers["If-Modified-Since"] = meta["lm"]
        host = urlsplit(url).netloc
        async with self.lock:
            wait = float(self.cfg().fetch_gap_s) - (self.clock() - self.last.get(host, -1e9))
            if wait > 0:
                await self.sleep(wait)
            try:
                status, body, rh = await self._stream(url, headers, limit)
            finally:
                self.last[host] = self.clock()
        if status == 304 and meta:
            body2 = self.store.read("cache", body_name)
            if body2 is not None:
                meta["at"] = self.clock()
                self.store.write("cache", meta_name, json.dumps(meta).encode())
                return Resp(200, body2, False)  # 304 = 방금 서버가 '그대로'라고 답함 → 새로 읽은 것으로 침 (miss 를 셈)
        if status == 200 and use_cache:
            self.store.write("cache", body_name, body)
            self.store.write("cache", meta_name, json.dumps({"at": self.clock(), "etag": rh.get("etag", ""), "lm": rh.get("last-modified", ""),
                                                             "url_h": hashlib.sha256(url.encode()).hexdigest()[:8]}).encode())
        return Resp(status, body)

    async def _stream(self, url: str, headers: dict[str, str], limit: int) -> tuple[int, bytes, dict[str, str]]:
        """(status, body, 머리글). 상한을 넘으면 status -1 · 연결 실패는 0. 넘겨주기는 따라가지 않음 (주소 밖으로 안 감)."""
        try:
            async with self.http.stream("GET", url, headers=headers, timeout=TIMEOUT, follow_redirects=False) as r:
                rh = {k.lower(): v for k, v in r.headers.items()}
                if r.status_code != 200:
                    return r.status_code, b"", rh
                cl = rh.get("content-length", "")
                if cl.isdigit() and int(cl) > limit:
                    return -1, b"", rh
                buf = bytearray()
                async for chunk in r.aiter_bytes():
                    buf += chunk
                    if len(buf) > limit:
                        return -1, b"", rh
                return 200, bytes(buf), rh
        except Exception as e:  # noqa: BLE001
            log.info("jobprep fetch %s: %s", urlsplit(url).netloc, type(e).__name__)
            return 0, b"", {}


def why_of(status: int) -> str:
    if status in BLOCK_STATUS:
        return f"blocked {status}"
    if status == -1:
        return "too_big"
    if status == 0:
        return "연결 실패"
    return f"HTTP {status}"


def _parse_rows(body: bytes, ats: str, token: str, filt: Callable[[dict[str, Any]], tuple[bool, str]] | None) -> list[dict[str, Any]]:
    rows = PARSERS[ats](json.loads(body), token)
    if filt is not None:
        for r in rows:
            r["_pass"], r["_why"] = filt(r)
    return rows


async def read_board(f: Fetcher, ats: str, token: str, filt: Callable[[dict[str, Any]], tuple[bool, str]] | None = None
                     ) -> tuple[bool, list[dict[str, Any]], str, bool]:
    """ATS 보드 하나 → (성공?, 줄들, 까닭, 새로 읽음?). 파싱과 규칙 거르기(filt)는 같은 스레드에서 (큰 JSON 이 이벤트 루프를 막지 않게).
    성공해야만 닫힘(miss)을 세고, 캐시(TTL) 저장본이면 새로 읽음=False (miss 를 안 셈 · 304 는 새로 읽은 것)."""
    src = f"{ats}:{token}"
    if ats not in URLS or f.is_off(src):
        return False, [], "쉼", False
    r = await f.get(URLS[ats].format(token=token))
    if r.status != 200:
        f.note(src, False, why_of(r.status))
        return False, [], why_of(r.status), False
    try:
        rows = await asyncio.to_thread(_parse_rows, r.body, ats, token, filt)
    except (ValueError, TypeError, AttributeError) as e:
        f.note(src, False, "parse")
        return False, [], f"읽기 실패 {type(e).__name__}", False
    f.note(src, True)
    return True, rows, "", not r.cached


async def read_page(f: Fetcher, url: str, src: str) -> tuple[int, str]:
    """HTML 한 쪽 (robots.txt 허용일 때만). (status, html) — 막히면 쉼."""
    if f.is_off(src):
        return -2, ""
    if not await f.robots_ok(url):
        f.note(src, False, "blocked robots")
        return -3, ""
    r = await f.get(url, accept="text/html,application/xhtml+xml;q=0.9", max_bytes=3 * 1024 * 1024)
    if r.status != 200:
        if r.status not in (404, 410):
            f.note(src, False, why_of(r.status))
        return r.status, ""
    f.note(src, True)
    return 200, r.text()
