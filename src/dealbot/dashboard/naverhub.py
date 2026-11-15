"""네이버 검색 Open API — 네이버 클라우드 'NAVER API HUB' 먼저, 막히면 예전 개발자센터 주소로 (주인 11/15).

주인의 NAVER_CLIENT_ID/SECRET 은 개발자센터 키가 아니라 API HUB 키다 (2026-07 부터 검색 API 새 키는 HUB 에서만 발급).
예전 주소(openapi.naver.com + X-Naver 헤더)로만 부르면 HUB 키는 401 'Not exist client id'. Blog-Auto blogauto/naver/openapi.py 와 같은 길:
  HUB  https://naverapihub.apigw.ntruss.com/search/v1/{blog|news|webkr}  헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY
  예전 https://openapi.naver.com/v1/search/{type}.json                 헤더 X-Naver-Client-Id / X-Naver-Client-Secret
  HUB 가 401/403 (또는 webkr 의 404 — HUB 에 웹문서 검색이 없음) 이면 예전 주소로 한 번 더.
답 모양은 둘 다 같다 (items·total·lastBuildDate). 키 값은 로그·응답·kv 어디에도 남기지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

HUB = "https://naverapihub.apigw.ntruss.com"
OLD = "https://openapi.naver.com"
ROUTES = {"blog": ("/search/v1/blog", "/v1/search/blog.json"), "news": ("/search/v1/news", "/v1/search/news.json"),
          "webkr": ("/search/v1/webkr", "/v1/search/webkr.json")}
HUB_MAYBE_MISSING = ("webkr",)  # HUB 에 없을 수 있는 종류 — 404 면 예전 주소로 (Blog-Auto status: webkr HUB 404 → 예전 401)


def url(typ: str, hub: bool) -> str:
    return (HUB + ROUTES[typ][0]) if hub else (OLD + ROUTES[typ][1])


def headers(cid: str, csec: str, hub: bool) -> dict[str, str]:
    if hub:
        return {"X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": csec}
    return {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}


def hub_refused(typ: str, status: int) -> bool:
    """HUB 답이 '이 키·이 종류는 HUB 로 안 됨' 인가 → 예전 주소로 한 번 더."""
    return status in (401, 403) or (status == 404 and typ in HUB_MAYBE_MISSING)


async def get(http: Any, typ: str, cid: str, csec: str, *, params: dict[str, Any], timeout: float,
              hub: bool = True) -> tuple[Any, bool, int | None]:
    """(답, HUB 로 받았나, HUB 답 코드 | None=HUB 안 부름). hub=False 면 예전 주소만.
    연결 실패·시간 초과(httpx.HTTPError)는 그대로 올려 보낸다 (부르는 쪽이 '응답 없음' 처리 — 두 번 기다리지 않게)."""
    hub_status: int | None = None
    if hub:
        r = await http.get(url(typ, True), params=params, headers=headers(cid, csec, True), timeout=timeout)
        hub_status = r.status_code
        if not hub_refused(typ, hub_status):
            return r, True, hub_status
        log.info("naver search %s: HUB %s — 예전 주소로", typ, hub_status)
    r = await http.get(url(typ, False), params=params, headers=headers(cid, csec, False), timeout=timeout)
    return r, False, hub_status
