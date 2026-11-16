"""통계 탭 '블로그 글 만들기' 한 줄 (주인 11/16 — "글 발행하는 데 쓰는 클로드 사용량이 주간 한도의 몇 % 정도인지").
Blog-Auto 기록 파일의 ai_cost 칸(ai_cost.summary)을 읽기만 — 칸이 없는 예전 파일이면 줄을 숨긴다. 네트워크 없이."""

from __future__ import annotations

from datetime import date

from dealbot.dashboard import render
from dealbot.dashboard.blogauto import ai_cost_line, parse_ai_cost, parse_export

AC = {"days": 7, "posts_7d": 21, "usd_7d": 30.5, "post_usd_7d": 25.2, "per_post_usd": 1.2, "pct_7d": 14.5, "post_pct_7d": 12.0,
      "basis": "근거 …"}


def test_line_with_everything() -> None:
    assert ai_cost_line(parse_ai_cost(AC)) == "블로그 글 만들기: 지난 7일 약 21편 · 글 1편당 ≈ $1.20 · 주간 한도의 약 12% 이하(추정)"


def test_line_without_pct_says_measuring() -> None:
    line = ai_cost_line(parse_ai_cost({**AC, "post_pct_7d": None, "pct_7d": None}))
    assert line == "블로그 글 만들기: 지난 7일 약 21편 · 글 1편당 ≈ $1.20 · 주간 한도 % 는 재는 중"


def test_missing_or_empty_hides_line() -> None:
    assert parse_ai_cost(None) is None and parse_ai_cost("x") is None and ai_cost_line(None) is None
    assert parse_ai_cost({"posts_7d": 3, "usd_7d": 0, "post_usd_7d": 0}) is None  # 아직 쓴 게 없음


def test_bad_numbers_are_dropped_not_guessed() -> None:
    ac = parse_ai_cost({**AC, "per_post_usd": "1.2", "post_pct_7d": True, "posts_7d": -1})
    assert ac is not None and ac["per_post_usd"] is None and ac["post_pct_7d"] is None and ac["posts_7d"] is None
    assert ai_cost_line(ac) == "블로그 글 만들기: 7일 ≈ $25.20 · 주간 한도 % 는 재는 중"


def test_export_carries_ai_cost_and_old_file_has_none() -> None:
    base = {"version": 1, "written_at": "2026-11-16T10:00:00+09:00", "blogs": {}}
    assert parse_export(base, "Asia/Seoul").ai_cost is None
    assert parse_export({**base, "ai_cost": AC}, "Asia/Seoul").ai_cost["posts_7d"] == 21


def test_stats_fragment_note_is_escaped_and_optional() -> None:
    kw = {"views": [], "today": date(2026, 11, 16), "tz": "Asia/Seoul", "spark_days": 30}
    assert "ai-cost" not in render.stats_fragment(**kw).split("</style>")[1]
    html = render.stats_fragment(**kw, note="블로그 글 만들기: <b>")
    assert '<p class="posted ai-cost">블로그 글 만들기: &lt;b&gt;</p>' in html
