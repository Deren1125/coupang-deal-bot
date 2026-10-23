"""쇼핑몰 크리에이터 (주인 10/23 — "내 링크 타고 들어와서 구매한 내역 … 조회수 … 예상 리워드도 표시해주고 각 사이트별로,
그리고 총합도"): Blog-Auto 기록 파일의 "creator_mall" 만 본다 (Blog-Auto creator_stats — 에이블리·지그재그 크리에이터 화면을
하루 한 번 읽은 이번 달 숫자). 딜봇은 쇼핑몰에 접속하지 않는다.

위쪽 숫자: 이번 달 방문 · 구매 · 예상 리워드 (읽은 쇼핑몰 합계). 아래 목록: 쇼핑몰마다 한 줄 —
'방문 N · 구매 N · 지난달 N원' + 오른쪽 알약 '예상 N원', 로그인이 풀린 쇼핑몰은 '다시 로그인' (Blog-Auto deploy/affiliate-login.sh),
예상 리워드는 정산 금액이 아니다 (아직 확정 안 된 것까지).
"""

from __future__ import annotations

from typing import Any

from dealbot.dashboard import icons
from dealbot.dashboard.model import OK, PARTIAL, UNAVAILABLE, CardView, FetchResult, ListEntry, ListGroup, Stat, fmt_num
from dealbot.dashboard.sources.base import CardData, Source, register

UNKNOWN_TEXT = "판단 불가"
CONN_TEXT = {"ok": "연결됨", "expired": "로그인 필요", "failed": "확인 실패", "unknown": "확인 전"}
STATE_TEXT = {"login": "다시 로그인", "failed": "읽기 실패", "pending": "승인 대기", "none": "아직 안 읽음"}
NO_PART = "no_creator_mall"


def _won(v: int | None) -> str:
    return f"{fmt_num(v)}원" if v is not None else "-"


@register
class CreatorMallSource(Source):
    type = "creator_mall"
    kind = "export"
    default_title = "쇼핑몰 크리에이터"
    icon = icons.LINK

    def live(self) -> FetchResult:
        reader = self.ctx.blogauto
        if reader is None:
            return FetchResult(UNAVAILABLE, note="Blog-Auto 활동 기록 경로가 설정되지 않음")
        export = reader.read()
        if not export.ok:
            return FetchResult(export.status, note=export.note, read_at=export.written_at)
        part = (export.v2 or {}).get("creator_mall")
        if not part:
            return FetchResult(UNAVAILABLE, read_at=export.written_at, detail=NO_PART)
        tot = part["total"]
        values = {f"cm_{k}": float(tot[k]) for k in ("visits", "orders", "expected") if tot.get(k) is not None}
        ok = [m for m in part["malls"] if m["state"] == "ok"]
        active = [m for m in part["malls"] if m["state"] != "pending"]
        status = OK if active and len(ok) == len(active) else PARTIAL if ok else UNAVAILABLE
        day = self.ctx.local_day(export.written_at).isoformat() if export.written_at else None
        return FetchResult(status, days={day: values} if values and day else {}, read_at=export.written_at, detail=part)

    def card(self, data: CardData) -> CardView:
        live = data.live
        part: dict[str, Any] | None = live.detail if live is not None and isinstance(live.detail, dict) else None
        if part is None:
            note = ("Blog-Auto 가 아직 쇼핑몰 통계를 보내지 않아요 — Blog-Auto 를 새 버전으로 올리면 보여요."
                    if live is not None and live.detail == NO_PART else "")
            stats = [Stat("방문 · 이번 달", None, "회", missing=UNKNOWN_TEXT), Stat("구매 · 이번 달", None, "건", missing=UNKNOWN_TEXT),
                     Stat("예상 리워드 · 이번 달", None, money=True, missing=UNKNOWN_TEXT)]
            return self.view(data, stats=stats, note=note, read_at=live.read_at if live is not None else None)
        tot = part["total"]
        conn_rows = []  # 쇼핑몰별 연결 (주인 10/23 — "실시간 쇼핑몰별 연결 내역"): 4시간쯤마다 확인한 로그인 상태
        for m in part["malls"]:
            if m["state"] == "pending":
                continue
            since = f" · {m['conn_since']}부터" if m.get("conn_since") and m.get("conn") != "ok" else ""
            conn_rows.append(ListEntry(m["name"], pill=CONN_TEXT.get(m.get("conn") or "unknown", "확인 전"),
                                       hot=m.get("conn") == "ok",
                                       sub=(f"마지막 확인 {m['checked_at']}" if m.get("checked_at") else "아직 확인 전") + since))
        entries = []
        for m in part["malls"]:
            if m["state"] == "ok":
                sub = f"방문 {fmt_num(m['visits']) if m['visits'] is not None else '-'} · 구매 {fmt_num(m['orders']) if m['orders'] is not None else '-'}"
                if m.get("order_amount"):
                    sub += f" · 주문 {_won(m['order_amount'])}"
                if m.get("last_month") is not None:
                    sub += f" · 지난달 {_won(m['last_month'])}"
                entries.append(ListEntry(m["name"], pill=f"예상 {_won(m['expected'])}", hot=bool(m.get("expected")), sub=sub))
            else:
                sub = m.get("text") or ""
                if m["state"] == "login" and m.get("expected") is not None:  # 로그인이 풀려도 지난번 숫자는 남아 있다
                    sub = (sub + f" · 지난번 예상 {_won(m['expected'])}").strip(" ·")
                entries.append(ListEntry(m["name"], pill=STATE_TEXT.get(m["state"], m["state"]), sub=sub))
        logins = [m["name"] for m in part["malls"] if m["state"] == "login" or m.get("conn") == "expired"]
        hints = [f"{', '.join(logins)} 로그인이 풀렸어요 — 원격 로그인 화면(Blog-Auto deploy/affiliate-login.sh)으로 다시 로그인해 주세요"] if logins else []
        read = part.get("read_at")
        stats = [
            Stat("방문 · 이번 달", tot.get("visits"), "회", missing=UNKNOWN_TEXT, sub="내 링크로 들어온 수"),
            Stat("구매 · 이번 달", tot.get("orders"), "건", missing=UNKNOWN_TEXT),
            Stat("예상 리워드 · 이번 달", tot.get("expected"), money=True, missing=UNKNOWN_TEXT,
                 sub=f"지난달 {_won(tot.get('last_month'))}" if tot.get("last_month") is not None else "정산 전 금액 포함"),
        ]
        note = f"쇼핑몰 화면을 읽은 시각 {read} · 하루 한 번" if read else "쇼핑몰 화면을 아직 읽지 않았어요 (하루 한 번 무작위 시각)"
        groups = [ListGroup("쇼핑몰별", "", entries), ListGroup("연결", "", conn_rows)]
        return self.view(data, stats=stats, groups=groups, note=note, hints=hints,
                         spark=data.series("cm_expected"), spark_label="예상 리워드",
                         read_at=live.read_at if live is not None else None, detail=part)
