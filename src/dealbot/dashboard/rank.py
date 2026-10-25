"""'할 일' 순서 (주인 10/18 — "할일 33개라고 나오는데 이거를 중요한, 핫할것 같은 순으로 나열해주면 좋갯어").

줄마다 점수 하나(rank)를 매겨 큰 것부터. 같은 입력이면 늘 같은 순서 (마지막은 iid 로 가름).
  1) 고장난 것 (⛔ 멈춤: 발행 막힘·연결 끊김·로그인 필요·블로그 프로그램 멈춤)
  2) 돈·시간이 걸린 딜 (링크·사진 부탁): 딜이 얼마나 핫한지 + 끝나는 시간이 가까울수록 크게 (3시간 안이면 확 앞으로).
     끝난 딜은 맨 아래로
  3) 주인을 기다리는 글 (사진·질문 — 마감이 가까운 것 먼저, 인기 글감 먼저) · 업로드 실패 · 예약 차례에 멈춘 글
     (간격 두고 발행으로 예약된 글은 할 일이 아니라 목록에 없다 — state.py)
  4) 발행 대기 초안 (인기 예상·점수 높은 것 먼저)
  5) ℹ️ 참고는 맨 뒤
같으면 끝나는 시간이 이른 것, 그다음 새 것.

화면에는 숫자를 보이지 않는다 — 맨 위 몇 줄에만 작은 '급해요'·'인기' 칩(cue)과 묶음 이름(group: 지금 꼭 · 오늘 안에 · 여유 있을 때).
"""

from __future__ import annotations

from typing import Any

DEAL_KINDS = ("link_request", "link_replaced", "review_auth", "review_info", "photo_need")
BROKEN_IIDS = ("sys-", "conn-", "sns-threads", "sns-instagram")
CUE_TOP = 5  # 칩은 맨 위 몇 줄에만
CUE_URGENT_H = 3.0
CUE_HOT = 60.0
GROUPS = ((650.0, "지금 꼭"), (300.0, "오늘 안에"), (float("-inf"), "여유 있을 때"))


def deal_hot(score: float | None, *, price: int = 0, low_price: int | None = None, discount: float | None = None,
             recommend: int | None = None) -> float:
    """딜이 얼마나 핫한지 0~100: 봇 점수(평균보다 싼 % · 추천 수 · 할인율 절반) + 기록 최저가 + 큰 할인 + 추천 많음."""
    s = max(0.0, float(score or 0))
    hot = min(60.0, s * 1.5)
    if low_price and price and price <= low_price:
        hot += 20
    if discount is not None and discount >= 40:
        hot += 10
    if recommend is not None and recommend >= 20:
        hot += 10
    return min(100.0, hot)


def post_hot(post: dict[str, Any] | None) -> float:
    """글감이 얼마나 핫한지 0~100 (기록 파일 posts[] 의 score · popular)."""
    p = post or {}
    try:
        s = float(p.get("score") or 0)
    except (TypeError, ValueError):
        s = 0.0
    return min(100.0, max(0.0, s) * 0.6 + (30 if p.get("popular") else 0))


def _boost(hours_left: float) -> float:
    if hours_left < 1:
        return 300
    if hours_left < 3:
        return 220
    if hours_left < 6:
        return 120
    if hours_left < 12:
        return 50
    if hours_left < 24:
        return 15
    return 0


HOT_TOP = 10  # 인기 검색어 상위 몇 위까지 '핫 글' (Blog-Auto hotfirst.HOT_TOP)
HOT_BASE = 2000.0  # 핫 글 줄은 맨 위, 순위 순 (주인 10/20)


def hot_rank(it: dict[str, Any]) -> int | None:
    """핫 글 줄의 인기 순위 (안쪽 칸 _hot_rank — Blog-Auto posts[].hot_rank). 아니면 None."""
    r = it.get("_hot_rank")
    return r if isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= HOT_TOP else None


def rank(it: dict[str, Any], now: float) -> float:
    if (hr := hot_rank(it)) is not None:  # 주인 10/20: 인기 상위 10 키워드 글은 다른 할 일보다 위
        return HOT_BASE - hr
    sev = it.get("sev") or "todo"
    kind = it.get("kind") or ""
    hot = float(it.get("_hot") or 0)
    exp = it.get("expires_at")
    left = (exp - now) / 3600 if exp is not None else None
    if sev == "stop":
        return 1000.0
    if sev == "info":
        return 50.0
    if kind == "search_reg":
        return 950.0  # 키자드 등록 (주인 10/25 '맨날 놓쳐') — 멈춤 바로 아래, 다른 할 일보다 위
    if str(it.get("iid", "")).startswith(BROKEN_IIDS):
        return 900.0  # 연결·시스템 줄 (네이버 로그인 필요 등) — ⛔ 표시가 없어도 고장 쪽
    if it.get("src") == "deal" and kind in DEAL_KINDS:
        if left is not None and left <= 0:
            return 10.0  # 끝난 딜 — 맨 아래 (곧 목록에서 빠짐)
        base = 450.0 if kind == "link_replaced" else 500.0
        return base + hot + (_boost(left) if left is not None else 0)
    if kind == "publish_unsure":
        return 560.0
    if sev == "hot":
        return 600.0 + hot
    if it.get("src") == "blog" and str(it.get("iid", "")).startswith("post-"):
        if left is not None and left <= 0 and kind in ("answers", "photos"):
            return 280.0  # 마감이 지난 질문·사진 — 아직 할 수는 있어서 발행 대기 바로 위
        if kind in ("answers", "photos"):
            return 400.0 + hot * 0.5 + (_boost(left) if left is not None else 0)
        if kind in ("retry", "sched_blocked"):
            return 480.0  # 업로드 실패 · 간격 두고 발행 차례에 멈춘 글 — 막힌 글은 같은 자리
        if kind == "owner_edit":
            return 380.0
        if kind == "publish":
            return 250.0 + hot * 0.7 + (_boost(left) if left is not None else 0)
        if kind in ("share", "channel"):
            return 300.0
        return 260.0
    return 300.0 + (100.0 if it.get("urgent") else 0.0)


def order(items: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """점수 큰 것부터 정렬하고 group·cue 를 붙인다 (안쪽 칸 _hot 은 지움). 새 목록을 돌려준다."""
    scored = [(rank(it, now), it) for it in items]

    def key(x: tuple[float, dict[str, Any]]) -> tuple[Any, ...]:
        r, it = x
        exp = it.get("expires_at")
        return (-r, exp if exp is not None else float("inf"), -(it.get("since") or 0), str(it.get("iid", "")))

    scored.sort(key=key)
    out = []
    for i, (r, it) in enumerate(scored):
        hot = float(it.pop("_hot", 0) or 0)
        hr = hot_rank(it)
        it.pop("_hot_rank", None)
        it["group"] = next(label for floor, label in GROUPS if r >= floor)
        it["cue"] = None
        if hr is not None:
            it["cue"] = f"🔥 인기 {hr}위"
        elif i < CUE_TOP and it.get("sev") not in ("stop", "info"):
            exp = it.get("expires_at")
            if exp is not None and 0 < exp - now < CUE_URGENT_H * 3600:
                it["cue"] = "급해요"
            elif hot >= CUE_HOT:
                it["cue"] = "인기"
        out.append(it)
    return out


def public(it: dict[str, Any]) -> dict[str, Any]:
    """정렬을 거치지 않는 줄 (자세히 시트·블로그 탭의 기다리는 글)에서 안쪽 칸을 뺀다."""
    it.pop("_hot", None)
    it.pop("_hot_rank", None)
    return it
