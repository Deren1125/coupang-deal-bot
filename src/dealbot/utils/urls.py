"""쿠팡 URL 파싱/정규화."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

COUPANG_HOSTS = {"www.coupang.com", "coupang.com", "m.coupang.com", "link.coupang.com"}
_PRODUCT_PATH_RE = re.compile(r"/vp/products/(\d+)")
_KEEP_QUERY = ("itemId", "vendorItemId")


def is_coupang_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return host in COUPANG_HOSTS or host.endswith(".coupang.com")


def is_short_affiliate_link(url: str) -> bool:
    """link.coupang.com/a/xxxx 형태 (누군가의 파트너스 단축 링크)."""
    p = urlparse(url)
    return p.netloc.lower() == "link.coupang.com" and p.path.startswith("/a/")


def is_affiliate_link(url: str) -> bool:
    p = urlparse(url)
    if p.netloc.lower() == "link.coupang.com":
        return True
    return "lptag=" in (p.query or "")


def extract_product_id(url: str) -> str | None:
    """상품 URL에서 productId 추출. 못 찾으면 None."""
    try:
        p = urlparse(url)
    except ValueError:
        return None
    m = _PRODUCT_PATH_RE.search(p.path or "")
    if m:
        return m.group(1)
    qs = parse_qs(p.query or "")
    for key in ("pageKey", "productId"):
        vals = qs.get(key)
        if vals and vals[0].isdigit():
            return vals[0]
    return None


def canonical_product_url(url: str) -> str | None:
    """www.coupang.com/vp/products/{id}?itemId=..&vendorItemId=.. 로 정규화 (추적 파라미터 제거)."""
    pid = extract_product_id(url)
    if not pid:
        return None
    p = urlparse(url)
    qs = parse_qs(p.query or "")
    keep = {k: qs[k][0] for k in _KEEP_QUERY if k in qs and qs[k]}
    query = urlencode(keep)
    return urlunparse(("https", "www.coupang.com", f"/vp/products/{pid}", "", query, ""))


def url_fingerprint(url: str) -> str:
    return "url:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
