"""쇼핑몰 크리에이터 (주인 10/23 — Blog-Auto creator_stats → 기록 파일 "creator_mall") 읽기: 이상한 줄은 빠짐.
화면은 통계 탭 '제휴 수익' 의 자동 줄 (tests/test_dash_earn.py — 따로 있던 카드는 10/23 그 칸으로 옮김)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dealbot.dashboard.blogauto import _creator_mall, parse_export

FIX = Path(__file__).parent / "fixtures" / "dash_contract" / "export_v2.json"


def fixture() -> dict[str, Any]:
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_fixture_parses_and_bad_rows_are_dropped() -> None:
    e = parse_export(fixture(), "Asia/Seoul")
    cm = e.v2["creator_mall"]
    assert [m["key"] for m in cm["malls"]] == ["ably", "zigzag", "toss"]
    assert [(m["conn"], m["checked_at"]) for m in cm["malls"]] == [("ok", "2026-10-23 18:40"), ("expired", "2026-10-23 18:41"),
                                                                    ("ok", "2026-10-23 18:42")]
    assert cm["total"] == {"visits": 21, "orders": 3, "expected": 3903, "last_month": 10000}
    assert cm["malls"][0]["items"] is None and cm["malls"][2]["items"][0] == {
        "name": "교동 육개장, 500g, 10개", "qty": 1, "refunded": 0, "amount": 24800, "reward": 1984, "via": "내 링크 상품"}
    bad = _creator_mall({"malls": [{"name": "에이블리", "state": "ok", "visits": -3, "orders": 1.5, "expected": "999"},
                                   {"name": "", "state": "ok"}, {"name": "x", "state": "hacked"}, "junk"]})
    assert bad is not None and len(bad["malls"]) == 1 and bad["malls"][0]["conn"] == "unknown"
    assert bad["malls"][0]["visits"] is None and bad["malls"][0]["orders"] is None and bad["malls"][0]["expected"] is None
    assert _creator_mall(None) is None and _creator_mall({"malls": "x"}) is None
    items = _creator_mall({"malls": [{"name": "토스쇼핑", "state": "ok", "items": [
        {"name": "육개장", "qty": 1, "amount": -5, "reward": "9", "via": "내 링크 상품"}, {"name": ""}, "junk"] + [{"name": "x"}] * 30}]})
    got = items["malls"][0]["items"]  # 이름 없는 줄·이상한 숫자는 빠지거나 None, 최대 20줄
    assert got[0] == {"name": "육개장", "qty": 1, "refunded": None, "amount": None, "reward": None, "via": "내 링크 상품"}
    assert len(got) == 18
    w = parse_export(fixture(), "Asia/Seoul").v2["creator_mall"]["malls"][2]["windows"]  # 제휴 수익 양식 (오늘·7일·30일)
    assert [x["label"] for x in w] == ["오늘", "7일", "30일"] and w[1] == {"label": "7일", "visits": 4, "orders": 2, "amount": 33790,
                                                                        "commission": 2703}
