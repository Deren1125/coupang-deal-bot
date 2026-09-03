"""쿠팡파트너스 Open API HMAC 서명.

공식 샘플과 동일한 규칙:
  message   = signed_date + METHOD + path + query   (query 는 '?' 없이)
  signature = HMAC-SHA256(secret_key, message).hexdigest()
  header    = "CEA algorithm=HmacSHA256, access-key=..., signed-date=..., signature=..."
signed_date 는 UTC, 형식 yyMMdd'T'HHmmss'Z'
"""

from __future__ import annotations

import hashlib
import hmac
import time


def signed_date(now: float | None = None) -> str:
    t = time.gmtime(now if now is not None else time.time())
    return time.strftime("%y%m%d", t) + "T" + time.strftime("%H%M%S", t) + "Z"


def build_authorization(
    method: str,
    path: str,
    query: str,
    access_key: str,
    secret_key: str,
    *,
    now: float | None = None,
) -> str:
    date = signed_date(now)
    message = date + method.upper() + path + query
    signature = hmac.new(secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"CEA algorithm=HmacSHA256, access-key={access_key}, signed-date={date}, signature={signature}"
