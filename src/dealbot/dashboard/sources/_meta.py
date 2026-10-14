"""인스타그램·스레드(메타 Graph API) 공용: 권한 오류 가리기, 인사이트 응답 읽기."""

from __future__ import annotations

from typing import Any


def is_token_problem(e: BaseException) -> bool:
    """토큰 만료·잘못된 토큰 (code 190)."""
    msg = str(e).lower()
    return getattr(e, "code", None) == 190 or ("access token" in msg and ("expired" in msg or "invalid" in msg))


def is_permission(e: BaseException) -> bool:
    """앱·토큰에 그 권한(scope)이 없음 (code 10, 200~299, 3) — 다시 시도해도 같다."""
    code = getattr(e, "code", None)
    msg = str(e).lower()
    if code in (3, 10) or (isinstance(code, int) and 200 <= code <= 299):
        return True
    return "permission" in msg or "scope" in msg or "not authorized" in msg


def insight_value(body: Any, name: str) -> float | None:
    """{"data": [{"name": "views", "total_value": {"value": 12}} | {"values": [{"value": 3}, …]}]} 에서 합계."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        total = item.get("total_value")
        if isinstance(total, dict) and isinstance(total.get("value"), int | float) and not isinstance(total.get("value"), bool):
            return float(total["value"])
        values = item.get("values")
        if isinstance(values, list):
            nums = [float(v["value"]) for v in values
                    if isinstance(v, dict) and isinstance(v.get("value"), int | float) and not isinstance(v.get("value"), bool)]
            return sum(nums) if nums else None
    return None
