"""네이버 쇼핑커넥트 링크 자동 만들기 — 딜봇 쪽 (주인 결정 10/11: 계정 제재 위험을 알고도 자동화).

브라우저 일은 Blog-Auto 가 한다. 아카이브 계정(finance 프로필)의 크롬은 Blog-Auto 가 쥐고 있고, 두 프로그램이 같은
프로필을 같이 열면 안 되기 때문이다. 딜봇은 묻고 Blog-Auto 가 답한다:
  딜봇 → Blog-Auto  blogauto_inbox.jsonl 에 {"connect_link": {...}} 한 줄 (주인 카톡 알림과 같은 받은편지함)
  Blog-Auto → 딜봇  connect_results.jsonl 에 한 줄 {"id", "job", "ok", "link" 또는 "reason", ...}
답은 같은 줄을 두 번 읽어도 한 번만 반영된다: 글마다 kv autolink:{id} 에 남긴 job 이 맞을 때만, 반영하면 지운다.
붙이기 전에 naver.me 링크가 같은 상품번호로 넘어가는지 한 번 더 본다 (리다이렉트는 따라가지 않음 — 내 링크를 누르지 않게).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from dealbot.naver_shop import search_queries

log = logging.getLogger(__name__)

STATUS = "awaiting_autolink"  # 대기열 상태: Blog-Auto 의 답을 기다림 (주인 '내 링크 대기' 목록·한도에는 안 셈)
RESULTS_FILE = "connect_results.jsonl"
KV_JOB = "autolink:"  # autolink:{queue_id} → {"job", "no", "at"}
KV_POS = "autolink_results_pos"
KV_STOP = "autolink_stop_until"  # Blog-Auto 가 멈춘(한도·시간 밖·로그인·제한) 동안은 묻지 않고 바로 주인에게
KV_MISS = "sc_miss:"  # sc_miss:{상품번호} → 쇼핑커넥트에 없던 시각
NAVER_ME = re.compile(r"^https://naver\.me/[A-Za-z0-9]+$")
_PRODUCTS = re.compile(r"/products/(\d+)")
# 주인에게 알리는 멈춤 (로그인·제한 안내). 한도·시간 밖은 조용히 예전 흐름으로
ALERT_REASONS = {
    "login": "아카이브 계정 로그인이 필요해요 (bash deploy/login.sh finance)",
    "blocked": "쇼핑커넥트에 제한·보호조치 안내가 떠서 오늘은 멈췄어요. 브랜드커넥트 화면을 확인해 주세요",
}
# 다시 켜는 법 (Blog-Auto 서버에서). 안 하면 다음 날 저절로 다시 시도
RESUME_HINT = "확인 뒤 python -m blogauto shopconnect resume 하면 바로 다시 해요"

# 토스쇼핑 쉐어링크 (주인 10/23): 같은 방식 — 딜봇 → {"toss_link": {...}} · Blog-Auto → toss_results.jsonl (blogauto/tosslink.py)
TOSS_RESULTS_FILE = "toss_results.jsonl"
KV_TOSS_POS = "autolink_toss_results_pos"
KV_TOSS_STOP = "autolink_toss_stop_until"
TOSS_SHORT = re.compile(r"^https://(?:toss\.shopping|toss\.im)/_m/[A-Za-z0-9_-]+$")
_TOSS_PATH = re.compile(r"^/(t|i)/(\d+)/?$")
TOSS_ALERT_REASONS = {
    "login": "토스 쉐어링크 로그인이 풀렸어요 (Blog-Auto 서버에서 bash deploy/affiliate-login.sh 토스)",
    "blocked": "토스가 쉐어링크 요청을 잠시 막아 오늘은 멈췄어요",
}
TOSS_RESUME_HINT = "로그인한 뒤 python -m blogauto tosslink resume 하면 바로 다시 해요"


def toss_key(url: str | None) -> tuple[str, str] | None:
    """토스 상품 주소 → ('t', tacaId) · ('i', tacaItemId). toss.shopping/t/·/i/ 주소만 (남의 쉐어링크 _m·앱 화면 주소는 None
    — Blog-Auto 가 남의 링크를 열지 않으므로 예전처럼 주인에게)."""
    u = urlparse(str(url or "").strip())
    if u.scheme != "https" or (u.hostname or "").lower() not in ("toss.shopping", "www.toss.shopping"):
        return None
    m = _TOSS_PATH.match(u.path or "")
    return (m.group(1), m.group(2)) if m else None


def verify_toss_link(link: str, deal_url: str, row: dict[str, Any]) -> str | None:
    """Blog-Auto 가 만든 쉐어링크가 붙여도 되는 모양인지. 맞으면 None, 아니면 이유 (링크는 열지 않는다 — 내 링크를 누르지 않게).
    상품은 Blog-Auto 가 발급 응답의 originUrl(toss.shopping/t/{tacaId})로 이미 맞춰 봤고, 여기서는 그 tacaId 가 딜 주소와 같은지만."""
    link = (link or "").strip()
    if not TOSS_SHORT.match(link):
        return "not_toss_short"
    if link == (deal_url or "").strip():
        return "same_as_deal_url"
    key = toss_key(deal_url)
    if key is None:
        return "deal_url"
    kind, n = key
    if kind == "t" and str(row.get("taca_id") or "") != n:
        return "mismatch"
    if kind == "i" and str(row.get("item_id") or "") != n:
        return "mismatch"
    return None


def job_queries(name: str) -> list[str]:
    """쇼핑커넥트 '상품 찾기' 검색어: 쇼핑몰 꼬리표·괄호(가격·배송)를 걷은 상품명을 낱말 단위로 40자 안에서, 그다음 앞 네 낱말."""
    out: list[str] = []
    for q in search_queries(name):
        while len(q) > 40 and " " in q:
            q = q.rsplit(" ", 1)[0]
        q = q[:40].strip()
        if q and q not in out:
            out.append(q)
    return out


def redirect_product_no(location: str | None) -> str | None:
    """naver.me 가 넘겨주는 주소의 상품번호. brandconnect.naver.com/affiliates/{id}?channelProductNo=번호,
    또는 바로 스토어(smartstore·brand.naver.com/…/products/번호)로 가면 그 번호. 다른 곳이면 None."""
    if not location:
        return None
    u = urlparse(location)
    host = u.netloc.lower().split(":")[0]
    if host == "brandconnect.naver.com" and re.match(r"^/affiliates/\d+/?$", u.path or ""):
        nos = parse_qs(u.query).get("channelProductNo") or []
        return nos[0] if len(nos) == 1 and nos[0].isdigit() else None
    if host.endswith("smartstore.naver.com") or host.endswith("brand.naver.com"):
        m = _PRODUCTS.search(u.path or "")
        return m.group(1) if m else None
    return None


async def verify_link(http: httpx.AsyncClient, link: str, product_no: str, *, deal_url: str = "") -> str | None:
    """쇼핑커넥트 링크가 이 상품의 것인지. 맞으면 None, 아니면 이유. naver.me 한 번만 묻고 넘어가지 않는다."""
    link = (link or "").strip()
    if not NAVER_ME.match(link):
        return "not_naver_me"
    if link == (deal_url or "").strip():
        return "same_as_deal_url"
    try:
        resp = await http.head(link, follow_redirects=False, timeout=10)
        if resp.status_code in (400, 404, 405, 501):  # HEAD 를 안 받는 곳이면 GET (그래도 따라가지 않음)
            resp = await http.get(link, follow_redirects=False, timeout=10)
    except httpx.HTTPError as e:
        return f"http_error:{type(e).__name__}"
    if resp.status_code not in (301, 302, 303, 307, 308):
        return f"status_{resp.status_code}"
    got = redirect_product_no(urljoin(link, resp.headers.get("location", "")))
    if got != product_no:
        return "mismatch"
    return None


class ResultReader:
    """Blog-Auto 가 쓰는 connect_results.jsonl 을 이어 읽는다. 읽은 자리는 반영한 뒤에 저장 (도중에 꺼져도 다시 읽어 반영)."""

    def __init__(self, path: Path, db: Any, pos_key: str = KV_POS) -> None:
        self.path = path
        self.db = db
        self.pos_key = pos_key

    def read(self, max_bytes: int = 256 * 1024) -> tuple[list[dict[str, Any]], int]:
        pos = int(self.db.kv_get(self.pos_key) or 0)
        try:
            size = self.path.stat().st_size
        except OSError:
            return [], pos
        if size < pos:
            pos = 0  # 파일이 새로 만들어졌으면 처음부터 (이미 반영한 줄은 job 이 지워져 있어 무시됨)
        if size == pos:
            return [], pos
        with self.path.open("rb") as f:
            f.seek(pos)
            data = f.read(max_bytes)
        end = data.rfind(b"\n") + 1  # 다 쓰인 줄까지만
        if end <= 0:
            return [], pos
        rows: list[dict[str, Any]] = []
        for line in data[:end].decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows, pos + end

    def commit(self, pos: int) -> None:
        self.db.kv_set(self.pos_key, str(pos))
