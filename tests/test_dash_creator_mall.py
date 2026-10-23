"""쇼핑몰 크리에이터 카드 (주인 10/23 — Blog-Auto creator_stats → 기록 파일 "creator_mall"): 읽기(이상한 줄은 빠짐) ·
통계 탭 카드(합계 숫자 · 쇼핑몰별 줄 · 로그인 풀림 안내) · 예전 Blog-Auto(칸 없음)는 오류가 아니라 안내."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dealbot.dashboard.blogauto import _creator_mall, parse_export
from dealbot.storage.db import Database
from tests.test_dashboard_links import card_html, make_dash

FIX = Path(__file__).parent / "fixtures" / "dash_contract" / "export_v2.json"
CARD = [{"key": "creator_mall", "type": "creator_mall", "title": "쇼핑몰 크리에이터"}]


def fixture() -> dict[str, Any]:
    return json.loads(FIX.read_text(encoding="utf-8"))


def write(tmp_path: Path, part: Any, *, written_at: str = "2026-10-14T18:00:00+09:00") -> None:
    data = fixture()
    data["written_at"] = written_at
    if part is None:
        data.pop("creator_mall", None)
    else:
        data["creator_mall"] = part
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_fixture_parses_and_bad_rows_are_dropped() -> None:
    e = parse_export(fixture(), "Asia/Seoul")
    cm = e.v2["creator_mall"]
    assert [m["key"] for m in cm["malls"]] == ["ably", "zigzag"]
    assert [(m["conn"], m["checked_at"]) for m in cm["malls"]] == [("ok", "2026-10-23 18:40"), ("expired", "2026-10-23 18:41")]
    assert cm["total"] == {"visits": 17, "orders": 1, "expected": 1200, "last_month": 10000}
    bad = _creator_mall({"malls": [{"name": "에이블리", "state": "ok", "visits": -3, "orders": 1.5, "expected": "999"},
                                   {"name": "", "state": "ok"}, {"name": "x", "state": "hacked"}, "junk"]})
    assert bad is not None and len(bad["malls"]) == 1 and bad["malls"][0]["conn"] == "unknown"
    assert bad["malls"][0]["visits"] is None and bad["malls"][0]["orders"] is None and bad["malls"][0]["expected"] is None
    assert _creator_mall(None) is None and _creator_mall({"malls": "x"}) is None


def test_card_shows_totals_rows_and_login_hint(db: Database, tmp_path: Path) -> None:
    write(tmp_path, fixture()["creator_mall"])
    dash = make_dash(db, tmp_path, CARD)
    v = next(x for x in dash.views() if x.key == "creator_mall")
    assert [(s.label, s.value) for s in v.stats] == [("방문 · 이번 달", 17), ("구매 · 이번 달", 1), ("예상 리워드 · 이번 달", 1200)]
    rows = v.groups[0].entries
    assert [(r.title, r.pill) for r in rows] == [("에이블리", "예상 1,200원"), ("지그재그", "다시 로그인")]
    conn = v.groups[1].entries  # 쇼핑몰별 연결 (주인 10/23)
    assert [(r.title, r.pill, r.hot) for r in conn] == [("에이블리", "연결됨", True), ("지그재그", "로그인 필요", False)]
    assert conn[0].sub == "마지막 확인 2026-10-23 18:40" and conn[1].sub == "마지막 확인 2026-10-23 18:41 · 2026-10-23 14:05부터"
    assert "방문 12 · 구매 1 · 지난달 10,000원" == rows[0].sub
    assert "지난번 예상 0원" in rows[1].sub
    card = card_html(dash.page_html(), "creator_mall")
    assert "지그재그 로그인이 풀렸어요" in card and "쇼핑몰 화면을 읽은 시각 2026-10-23 14:05" in card
    assert db.metric_latest("creator_mall", "cm_expected")["value"] == 1200


def test_old_blogauto_without_the_part_is_a_note_not_an_error(db: Database, tmp_path: Path) -> None:
    write(tmp_path, None)
    dash = make_dash(db, tmp_path, CARD)
    v = next(x for x in dash.views() if x.key == "creator_mall")
    assert all(s.value is None for s in v.stats)
    assert "Blog-Auto 가 아직 쇼핑몰 통계를 보내지 않아요" in card_html(dash.page_html(), "creator_mall")
