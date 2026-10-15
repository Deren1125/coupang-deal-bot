"""블로그 카드의 자기 통계 (Blog-Auto 기록 파일의 own_stats — 주인 결정 10/15: 자기 블로그 세 개만, 하루 한 번 날마다 다른 시각).

딜봇은 파일만 읽는다 (네트워크 없음). 이웃 수는 직접 입력과 같은 지표라 '그날 자동으로 읽은 뒤에 넣은 주인 값' 만 주인 값이 이긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from dealbot.config import DashboardCardConfig, DashboardConfig
from dealbot.dashboard import model
from dealbot.dashboard.blogauto import (
    BlogAutoReader,
    OwnDay,
    OwnPrev,
    parse_export,
    parse_own_stats,
)
from dealbot.dashboard.service import Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.dashboard.sources.naver_blog import STATS_NOTE, missed_text
from dealbot.storage.db import Database

NBSP = "\u00a0"
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 한국시간 10/15 15:00
READ = "2026-10-15T14:23:05+09:00"
READ_UTC = datetime(2026, 10, 15, 5, 23, 5, tzinfo=UTC)
TODAY, YESTERDAY = "2026-10-15", "2026-10-14"
BLOGS = {"blog_finance": ("finance", "derenlog"), "blog_issue": ("issue", "derenpick"), "blog_hotdeal": ("hotdeal", "demiyum")}


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def own(**over: Any) -> dict[str, Any]:
    """참고: 서버에서 읽어 본 10/15 값 (derenlog)."""
    base: dict[str, Any] = {
        "day": TODAY, "read_at": READ, "neighbors": 183, "visitors_today": 2, "visitors_total": 2751, "views_today": 5,
        "likes_today": 5, "comments_today": 2, "neighbor_change_today": 0, "buddy_requests_pending": 2,
        "yesterday": {"day": YESTERDAY, "views": 16, "likes": 3, "comments": 1, "neighbor_change": 1},
        "prev": {"day": YESTERDAY, "neighbors": 182, "visitors_total": 2735},
    }
    return {**base, **over}


def write_export(tmp_path: Path, own_stats: Any = None, *, written_at: str = "2026-10-15T14:30:00+09:00", drop: bool = False) -> None:
    data: dict[str, Any] = {"version": 1, "written_at": written_at, "blogs": {
        b: {"posts_published_today": 1, "posts_published_total": 23, "drafts_saved_today": 2, "neighbor_requests_today": 10,
            "comments_today": 3, "replies_today": 2} for b, _ in BLOGS.values()}}
    if not drop:
        data["own_stats"] = own_stats
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def make_dash(db: Database, tmp_path: Path, *, clock: Clock | None = None, titles: dict[str, str] | None = None) -> Dashboard:
    clock = clock or Clock()
    cards = [DashboardCardConfig(key=k, type="naver_blog", title=(titles or {}).get(k, f"블로그 {b}"),
                                 goals={"posts": 50}, options={"blog": b, "blog_id": i}) for k, (b, i) in BLOGS.items()]
    cfg = DashboardConfig(cards=cards, blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    ctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=clock,
                        blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=clock))
    return Dashboard(cfg, ctx, user="owner", password="plum-river-cactus-42")


def card_of(dash: Dashboard, key: str) -> model.CardView:
    return next(v for v in dash.views() if v.key == key)


def card_html(page: str, key: str) -> str:
    return page.split(f'id="card-{key}"', 1)[1].split("</article>", 1)[0]


def tiles(v: model.CardView) -> list[tuple[str, float | None, str]]:
    return [(s.label, s.value, s.sub) for s in v.stats]


# ---------------------------------------------------------------- 파일 읽기
def test_own_stats_are_parsed_per_blog_and_bad_cells_are_dropped() -> None:
    got = parse_own_stats({
        "finance": own(),
        "issue": own(neighbors=None, visitors_today="21", visitors_total="2,751", views_today=5.0, likes_today=True,
                     comments_today=-1, neighbor_change_today=-2, buddy_requests_pending=1.5, yesterday=None, prev=None),
        "hotdeal": {"read_at": "2026-10-15T09:00:00+09:00", "neighbors": 82, "views_today": 10 ** 12, "blog_id": "demiyum",
                    "yesterday": {"day": TODAY, "views": 1}, "prev": {"day": "2026-10-16", "neighbors": 80}},
        "empty": {"day": TODAY, "read_at": READ, "neighbors": None, "yesterday": None},  # 숫자가 하나도 없음 → 없는 것과 같음
        "noday": {"neighbors": 3},  # 언제 읽었는지 모름
        "badday": {"day": "2026/10/15", "neighbors": 3},
        "list": [1, 2],
        "": own(),
    }, "Asia/Seoul")
    assert set(got) == {"finance", "issue", "hotdeal", "demiyum"}
    f = got["finance"]
    assert (f.day, f.read_at, f.neighbors, f.visitors_today, f.visitors_total, f.views_today, f.likes_today, f.comments_today,
            f.neighbor_change_today, f.buddy_requests_pending) == (date(2026, 10, 15), READ_UTC, 183, 2, 2751, 5, 5, 2, 0, 2)
    assert f.yesterday == OwnDay(date(2026, 10, 14), 16, 3, 1, 1)
    assert f.prev == OwnPrev(date(2026, 10, 14), 182, 2735)
    i = got["issue"]  # 숫자 글자('2,751')·12.0 은 받고, 참·거짓·음수·소수는 그 칸만 버림. 이웃증감은 음수도 받음
    assert (i.neighbors, i.visitors_today, i.visitors_total, i.views_today, i.likes_today, i.comments_today,
            i.neighbor_change_today, i.buddy_requests_pending, i.yesterday, i.prev) == (None, 21, 2751, 5, None, None, -2, None, None, None)
    h = got["hotdeal"]  # day 가 없으면 read_at 의 날, 말이 안 되는 큰 수·앞날(어제·앞 값이 읽은 날보다 뒤)은 버림
    assert h is got["demiyum"] and h.day == date(2026, 10, 15) and h.views_today is None and h.yesterday is None and h.prev is None
    # 날짜와 시각이 어긋나면 시각은 버리고 날짜만 믿음
    odd = parse_own_stats({"finance": own(read_at="2026-10-14T23:59:00+09:00")}, "Asia/Seoul")["finance"]
    assert odd.day == date(2026, 10, 15) and odd.read_at is None
    for bad in (None, [], "x", 3):
        assert parse_own_stats(bad, "Asia/Seoul") == {}


def test_old_export_without_own_stats_still_reads_and_stale_export_keeps_them(tmp_path: Path) -> None:
    old = parse_export({"written_at": "2026-10-15T14:00:00+09:00", "blogs": {"finance": {"posts_published_total": 3}}}, "Asia/Seoul")
    assert old.ok and old.own_stats == {} and old.own("finance") is None and old.blog("finance") == {"posts_total": 3.0}
    new = parse_export({"written_at": "2026-10-15T14:00:00+09:00", "blogs": {}, "own_stats": {"Finance": own()}}, "Asia/Seoul")
    assert new.own("finance", "derenlog") is not None and new.own(None, "FINANCE") is not None
    # 활동 기록 파일이 오래돼도(STALE) 자기 통계는 함께 넘긴다 (자기 통계는 read_at 으로 따로 봄)
    write_export(tmp_path, {"finance": own()}, written_at="2026-10-15T09:00:00+09:00")
    ex = BlogAutoReader(tmp_path / "blogauto.json", tz="Asia/Seoul", stale_hours=3, now=Clock()).read()
    assert ex.status == model.STALE and ex.own("finance") is not None


# ---------------------------------------------------------------- 카드
def test_blog_card_shows_auto_neighbors_visitors_and_views(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own()})
    dash = make_dash(db, tmp_path)
    v = card_of(dash, "blog_finance")
    assert tiles(v) == [
        ("이웃 수", 183, "받은 서로이웃 신청 2"),
        ("방문자 · 오늘", 2, "전체 2,751명"),
        ("조회수 · 오늘", 5, "14:23 기준 · 공감 5 · 댓글 2"),  # 하루 중간에 읽은 값이라 몇 시 기준인지
        ("조회수 · 어제", 16, "공감 3 · 댓글 1 · 이웃 +1"),
    ]
    assert (v.stats[0].delta, v.stats[0].delta_label) == (1, "어제 대비")
    assert not any(s.stale for s in v.stats)
    assert v.stats_note == "자동 · 10. 15. 14:23 읽음" and v.stats_warn == ""
    assert v.summary["audience_delta"] == 1.0
    # 날짜별로 쌓임: 이웃 수(오늘·어제 — 직접 입력과 같은 지표), 전체 방문자, 전날 하루 조회수·공감·댓글 (오늘 중간 값은 안 쌓음)
    assert db.metric_series("blog_finance", "neighbors", "2026-10-01", TODAY) == [(YESTERDAY, 182.0), (TODAY, 183.0)]
    assert db.metric_latest("blog_finance", "neighbors")["origin"] == "auto"
    assert db.metric_series("blog_finance", "own_visitors_total", "2026-10-01", TODAY) == [(YESTERDAY, 2735.0), (TODAY, 2751.0)]
    assert [db.metric_latest("blog_finance", m)["value"] for m in ("own_views", "own_likes", "own_comments")] == [16, 3, 1]
    assert db.metric_latest("blog_finance", "own_views")["day"] == YESTERDAY
    assert db.metric_latest("blog_finance", "views_today") is None
    # 화면: 숫자 칸 네 개, 통계 열기 링크 그대로, 회색 직접 입력 안내 대신 '자동 · … 읽음'
    page = dash.page_html()
    card = card_html(page, "blog_finance")
    stats = card.split('<div class="stats">', 1)[1].split('<div class="links">', 1)[0]
    assert stats.count('<div class="stat">') == 4 and 'class="stat stale"' not in stats
    assert '<p class="label">이웃 수</p><p class="value"><span class="num">183</span><span class="unit">명</span>' \
           '<span class="delta up">+1<small>어제 대비</small></span></p><p class="sub">받은 서로이웃 신청 2</p>' in stats
    # 작은 줄은 좁은 칸에서 '댓글 / 2' 로 갈라지지 않게 항목마다 한 덩어리
    assert ('<p class="label">조회수 · 오늘</p><p class="value"><span class="num">5</span><span class="unit">회</span></p>'
            f'<p class="sub"><span class="it">14:23 기준{NBSP}·</span> <span class="it">공감 5{NBSP}·</span> '
            '<span class="it">댓글 2</span></p>') in stats
    assert 'href="https://admin.blog.naver.com/derenlog/stat/today"' in card and 'href="https://m.blog.naver.com/derenlog"' in card
    assert '<p class="stats-note">자동 · 10. 15. 14:23 읽음</p>' in card and STATS_NOTE not in card
    assert card.index('<div class="stats">') < card.index('<div class="links">') < card.index('class="stats-note"')
    # 블로그 활동 줄(봇이 한 일)은 그대로
    assert v.activity == ["오늘 발행 1 · 임시저장 2 · 서로이웃 신청 10 · 댓글 3 · 답글 2"]
    # own_stats 에 없는 블로그 카드는 예전처럼 직접 입력 안내 + 숫자 칸 두 개
    iss = card_of(dash, "blog_issue")
    assert [s.label for s in iss.stats] == ["이웃 수", "어제 방문자"] and iss.stats_note.startswith(STATS_NOTE)
    assert "own_stats" not in iss.detail and card_html(page, "blog_issue").count('<div class="stat">') == 2


def test_delta_text_names_the_compared_day_and_old_reads_are_labelled_by_day(db: Database, tmp_path: Path) -> None:
    # 바로 앞에 읽은 값이 어제가 아니면 그 날짜 대비
    write_export(tmp_path, {"finance": own(prev={"day": "2026-10-12", "neighbors": 180, "visitors_total": 2700},
                                           neighbor_change_today=1),
                            "issue": own(neighbors=21, prev={"day": "2026-10-14", "neighbors": 23}),
                            "hotdeal": own(neighbors=82, prev={"day": "2026-10-14", "neighbors": 82})})
    dash = make_dash(db, tmp_path)
    fin, iss, hot = (card_of(dash, k) for k in BLOGS)
    assert (fin.stats[0].delta, fin.stats[0].delta_label) == (3, "10. 12. 대비")
    assert (iss.stats[0].delta, iss.stats[0].delta_label) == (-2, "어제 대비")
    assert (hot.stats[0].delta, hot.stats[0].delta_label) == (0, "어제 대비")
    page = dash.page_html()
    assert '<span class="delta up">+3<small>10. 12. 대비</small></span>' in card_html(page, "blog_finance")
    assert '<span class="delta down">-2<small>어제 대비</small></span>' in card_html(page, "blog_issue")
    assert '<span class="delta flat">0<small>어제 대비</small></span>' in card_html(page, "blog_hotdeal")
    # 맨 위 '오늘 늘어난 팔로워' 에 블로그 이웃 증감도: 어제 값이 있으면 어제 대비(-2, 0),
    # 없으면(+3 은 10. 12. 대비라 빠짐) 통계 '일간 현황' 의 오늘 이웃증감수(+1)
    assert (iss.summary["audience_delta"], hot.summary["audience_delta"], fin.summary["audience_delta"]) == (-2.0, 0.0, 1.0)
    assert dash.summary(dash.views())[0] == ("오늘 늘어난 팔로워", "-1", "구독자·팔로워·이웃, 어제와 비교한 카드 3개")

    # 오늘 아직 안 읽은 아침: 어제 읽은 값은 '어제' 로, 그 전날 하루 값은 날짜로
    db2 = Database(tmp_path / "morning.db")
    try:
        write_export(tmp_path, {"finance": own(day=YESTERDAY, read_at="2026-10-14T20:41:00+09:00",
                                               yesterday={"day": "2026-10-13", "views": 9, "likes": None, "comments": 0},
                                               prev={"day": "2026-10-13", "neighbors": 181})})
        v = card_of(make_dash(db2, tmp_path, clock=Clock(datetime(2026, 10, 15, 0, 30, tzinfo=UTC))), "blog_finance")
        assert tiles(v)[1:] == [("방문자 · 어제", 2, "전체 2,751명"), ("조회수 · 어제", 5, "20:41 기준 · 공감 5 · 댓글 2"),
                                (f"조회수 · 10.{NBSP}13.", 9, "댓글 0")]
        assert (v.stats[0].value, v.stats[0].delta, v.stats[0].delta_label, v.stats[0].sub) == (183, 2, "10. 13. 대비",
                                                                                               "받은 서로이웃 신청 2")
        assert v.stats_note == "자동 · 10. 14. 20:41 읽음" and v.stats_warn == "" and not any(s.stale for s in v.stats)
        assert "audience_delta" not in v.summary  # 오늘 값이 아직 없음
    finally:
        db2.close()


def test_owner_entry_after_the_auto_read_wins_otherwise_auto_wins(db: Database, tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 15, 0, 0, tzinfo=UTC))  # 09:00 — 오늘 자동 읽기 전
    dash = make_dash(db, tmp_path, clock=clock)
    assert dash.save_manual("blog_finance", {"neighbors": "180"}) == "saved"
    write_export(tmp_path, {"finance": own()})  # 14:23 에 읽음
    clock.t = NOW
    v = card_of(dash, "blog_finance")
    assert (v.stats[0].value, v.stats[0].sub) == (183, "받은 서로이웃 신청 2")  # 읽기 전에 넣은 값은 자동 값이 덮음
    assert db.metric_latest("blog_finance", "neighbors")["origin"] == "auto"

    clock.t = datetime(2026, 10, 15, 6, 40, tzinfo=UTC)  # 15:40 — 읽은 뒤에 주인이 넣음
    assert dash.save_manual("blog_finance", {"neighbors": "185"}) == "saved"
    for _ in range(2):  # 페이지를 다시 열고 정해진 시각에 다시 읽어도 주인 값을 덮지 않음
        v = card_of(dash, "blog_finance")
        assert (v.stats[0].value, v.stats[0].delta, v.stats[0].delta_label) == (185, 3, "어제 대비")
        assert v.stats[0].sub == "직접 입력 · 받은 서로이웃 신청 2" and not v.stats[0].stale
        assert db.metric_latest("blog_finance", "neighbors")["origin"] == "manual"
    assert v.detail["own_stats"]["neighbors_from"] == "manual" and v.detail["own_stats"]["neighbors"] == 183
    assert v.stats[1].value == 2 and v.stats_note == "자동 · 10. 15. 14:23 읽음"  # 나머지 칸은 자동 값 그대로
    assert v.summary["audience_delta"] == 3.0
    assert (f'<p class="sub"><span class="it">직접 입력{NBSP}·</span> <span class="it">받은 서로이웃 신청 2</span></p>'
            in card_html(dash.page_html(), "blog_finance"))

    # 다음 날 자동으로 읽으면 다시 자동 값 (어제 주인 값과 비교, 어제 칸은 주인 값 그대로)
    write_export(tmp_path, {"finance": own(day="2026-10-16", read_at="2026-10-16T10:05:00+09:00", neighbors=186,
                                           yesterday=None, prev={"day": TODAY, "neighbors": 183})},
                 written_at="2026-10-16T10:30:00+09:00")
    clock.t = datetime(2026, 10, 16, 2, 0, tzinfo=UTC)
    v = card_of(dash, "blog_finance")
    assert (v.stats[0].value, v.stats[0].delta, v.stats[0].delta_label, v.stats[0].sub) == (186, 1, "어제 대비", "받은 서로이웃 신청 2")
    assert db.metric_latest("blog_finance", "neighbors", until_day=TODAY) == {
        "day": TODAY, "value": 185.0, "read_at": "2026-10-15T06:40:00+00:00", "origin": "manual"}


def test_owner_entry_on_a_later_day_than_the_last_read_is_shown_as_hers(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(day=YESTERDAY, read_at="2026-10-14T20:41:00+09:00", yesterday=None,
                                           prev={"day": "2026-10-13", "neighbors": 181})})
    dash = make_dash(db, tmp_path)
    assert dash.save_manual("blog_finance", {"neighbors": "184"}) == "saved"
    v = card_of(dash, "blog_finance")
    assert (v.stats[0].value, v.stats[0].delta, v.stats[0].delta_label, v.stats[0].sub) == (184, 1, "어제 대비",
                                                                                           "직접 입력 · 받은 서로이웃 신청 2")
    # 예전 날짜로 넣은 값이면 날짜도
    db.metric_put("blog_issue", "neighbors", "2026-10-13", 20, origin="manual", now=NOW)
    write_export(tmp_path, {"issue": own(neighbors=None, day="2026-10-12", read_at="2026-10-12T11:00:00+09:00",
                                         yesterday=None, prev=None)})
    w = card_of(make_dash(db, tmp_path), "blog_issue")
    assert (w.stats[0].value, w.stats[0].sub) == (20, "직접 입력 · 10. 13. · 받은 서로이웃 신청 2") and not w.stats[0].stale
    assert w.stats[1].stale  # 자동 값은 오래됨


def test_stale_auto_values_are_greyed_with_a_missed_day_hint(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(day="2026-10-13", read_at="2026-10-13T14:23:05+09:00",
                                           yesterday={"day": "2026-10-12", "views": 7}, prev={"day": "2026-10-12", "neighbors": 182}),
                            "issue": own(day="2026-10-12", read_at="2026-10-12T09:00:00+09:00", yesterday=None, prev=None),
                            "hotdeal": own(day=YESTERDAY, read_at="2026-10-14T01:30:00+09:00", yesterday=None, prev=None)})
    dash = make_dash(db, tmp_path)
    fin, iss, hot = (card_of(dash, k) for k in BLOGS)
    assert all(s.stale for s in fin.stats) and len(fin.stats) == 4
    assert [s.label for s in fin.stats] == ["이웃 수", f"방문자 · 10.{NBSP}13.", f"조회수 · 10.{NBSP}13.", f"조회수 · 10.{NBSP}12."]
    assert (fin.stats_note, fin.stats_warn) == ("자동 · 10. 13. 14:23 읽음", "어제 못 읽음")
    assert (iss.stats_warn, hot.stats_warn) == ("10. 13.부터 못 읽음", "오늘 아직 못 읽음")  # 37.5시간 — 어제는 읽었음
    assert all(s.stale for s in hot.stats) and fin.detail["own_stats"]["stale"] is True
    assert "audience_delta" not in fin.summary  # 오래된 값은 '오늘 늘어난' 에 넣지 않음
    page = dash.page_html()
    card = card_html(page, "blog_finance")
    assert card.count('<div class="stat stale">') == 4
    assert '<p class="stats-note">자동 · 10. 13. 14:23 읽음 · <span class="warn">어제 못 읽음</span></p>' in card
    assert ".stat.stale .value" in page and ".stat .sub .it{display:inline-block;max-width:100%}" in page  # 회색 · 작은 줄 덩어리
    assert missed_text(date(2026, 10, 15), date(2026, 10, 15)) == "오늘 아직 못 읽음"


def test_a_whole_missed_day_is_stale_even_within_36_hours(db: Database, tmp_path: Path) -> None:
    """그제 밤 늦게 읽고 어제를 건너뛰었으면 36시간이 안 됐어도 회색 + '어제 못 읽음' (날마다 다른 시각이라 생기는 틈)."""
    write_export(tmp_path, {"finance": own(day="2026-10-13", read_at="2026-10-13T23:10:00+09:00", yesterday=None,
                                           prev={"day": "2026-10-12", "neighbors": 182})},
                 written_at="2026-10-15T07:30:00+09:00")
    morning = Clock(datetime(2026, 10, 14, 23, 0, tzinfo=UTC))  # 한국시간 10/15 08:00 — 읽은 지 32시간 50분
    v = card_of(make_dash(db, tmp_path, clock=morning), "blog_finance")
    assert all(s.stale for s in v.stats) and (v.stats_note, v.stats_warn) == ("자동 · 10. 13. 23:10 읽음", "어제 못 읽음")
    # 같은 시각 차이라도 어제 읽은 값이면 회색이 아님 (오늘은 아직 안 읽었을 뿐)
    write_export(tmp_path, {"finance": own(day=YESTERDAY, read_at="2026-10-14T00:10:00+09:00", yesterday=None, prev=None)},
                 written_at="2026-10-15T07:30:00+09:00")
    v = card_of(make_dash(db, tmp_path, clock=morning), "blog_finance")
    assert not any(s.stale for s in v.stats) and v.stats_warn == "" and v.stats[1].label == "방문자 · 어제"


def test_read_time_unknown_uses_the_day_only(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(day=YESTERDAY, read_at=None, yesterday=None, prev=None),
                            "issue": own(day="2026-10-13", read_at=None, yesterday=None, prev=None)})
    dash = make_dash(db, tmp_path)
    fin, iss = card_of(dash, "blog_finance"), card_of(dash, "blog_issue")
    assert fin.stats_note == "자동 · 10. 14. 읽음" and fin.stats_warn == "" and not any(s.stale for s in fin.stats)
    assert fin.stats[2].sub == "공감 5 · 댓글 2"  # 몇 시 기준인지 모름
    assert iss.stats_warn == "어제 못 읽음" and all(s.stale for s in iss.stats)
    # 읽은 시각을 모르면 그날 넣은 주인 값이 이김
    db.metric_put("blog_finance", "neighbors", YESTERDAY, 190, origin="manual", now=datetime(2026, 10, 14, 3, 0, tzinfo=UTC))
    assert card_of(dash, "blog_finance").stats[0].value == 190


def test_partial_nulls_show_dashes_and_fall_back_to_the_last_stored_neighbors(db: Database, tmp_path: Path) -> None:
    db.metric_put("blog_finance", "neighbors", YESTERDAY, 182, origin="auto", now=NOW)
    write_export(tmp_path, {"finance": own(neighbors=None, visitors_today=None, visitors_total=None, likes_today=None,
                                           buddy_requests_pending=0, yesterday=None, prev=None),
                            "issue": {"day": TODAY, "read_at": READ, "neighbors": None, "visitors_today": None},
                            "hotdeal": own(views_today=None, comments_today=None, likes_today=None, read_at=None,
                                           yesterday={"day": YESTERDAY, "views": None, "likes": None, "comments": None})})
    dash = make_dash(db, tmp_path)
    fin, iss, hot = (card_of(dash, k) for k in BLOGS)
    assert tiles(fin) == [("이웃 수", 182, "10. 14. 값"), ("방문자 · 오늘", None, ""), ("조회수 · 오늘", 5, "14:23 기준 · 댓글 2")]
    assert fin.stats[0].delta is None and fin.summary["audience_delta"] == 0.0  # 통계의 오늘 이웃증감수
    # 숫자가 하나도 없으면 자기 통계가 없는 것과 같음 (직접 입력 안내)
    assert iss.stats_note.startswith(STATS_NOTE) and len(iss.stats) == 2
    assert tiles(hot)[2:] == [("조회수 · 오늘", None, ""), ("조회수 · 어제", None, "")]
    card = card_html(dash.page_html(), "blog_finance")
    assert '<p class="label">방문자 · 오늘</p><p class="value">-</p>' in card


def test_activity_file_stale_but_own_stats_fresh(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own()}, written_at="2026-10-15T10:00:00+09:00")  # 활동 기록은 5시간 전 (Blog-Auto 멈춤)
    v = card_of(make_dash(db, tmp_path), "blog_finance")
    assert not v.activity and any("5시간 전 것" in h for h in v.hints)
    assert v.stats[0].value == 183 and not any(s.stale for s in v.stats) and v.stats_note == "자동 · 10. 15. 14:23 읽음"
    # 파일 자체가 없으면 자기 통계도 없음 → 직접 입력
    (tmp_path / "blogauto.json").unlink()
    v = card_of(make_dash(db, tmp_path), "blog_finance")
    assert v.stats_note.startswith(STATS_NOTE) and "own_stats" not in v.detail


def test_future_day_is_ignored(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(day="2026-10-16", read_at="2026-10-16T09:00:00+09:00", prev=None, yesterday=None)})
    v = card_of(make_dash(db, tmp_path), "blog_finance")
    assert v.stats_note.startswith(STATS_NOTE) and db.metric_latest("blog_finance", "neighbors") is None


def test_own_stats_render_escaped_and_never_inject_text(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(neighbors="<script>alert(1)</script>", visitors_today="<img src=x onerror=1>",
                                           day=TODAY, read_at="2026-10-15T14:23:05+09:00\"><b>",
                                           yesterday={"day": "<b>", "views": 1}, prev={"day": "<i>", "neighbors": 1})})
    dash = make_dash(db, tmp_path, titles={"blog_finance": "<b>아카이브</b> & \"Deren\""})
    page = dash.page_html()
    card = card_html(page, "blog_finance")
    assert "<script" not in page and "<img" not in page and "<b>" not in page and "onerror" not in page
    assert "&lt;b&gt;아카이브&lt;/b&gt; &amp; &quot;Deren&quot;" in card
    v = card_of(dash, "blog_finance")
    # 이상한 칸·어제 값·앞 값은 버림 (글자로 된 이웃 수는 숫자로 보지 않음 — 쌓인 값도 없음)
    assert v.stats[0].value is None and v.stats[1].value is None and len(v.stats) == 3 and v.stats[0].delta is None
    assert db.metric_latest("blog_finance", "neighbors") is None and v.detail["own_stats"]["prev"] is None
    assert v.detail["own_stats"]["neighbors_from"] is None
    assert v.stats_note == "자동 · 10. 15. 읽음"  # 시각이 이상하면 날짜만


def test_json_output_has_the_auto_values_and_stale_flags(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"finance": own(), "issue": own(day="2026-10-13", read_at="2026-10-13T08:00:00+09:00", yesterday=None)})
    data = json.loads(make_dash(db, tmp_path).json_text())
    fin = next(c for c in data["cards"] if c["key"] == "blog_finance")
    assert [(s["label"], s["value"], s["delta"], s["delta_label"], s["sub"], s["stale"]) for s in fin["stats"]] == [
        ("이웃 수", 183, 1, "어제 대비", "받은 서로이웃 신청 2", False),
        ("방문자 · 오늘", 2, None, None, "전체 2,751명", False),
        ("조회수 · 오늘", 5, None, None, "14:23 기준 · 공감 5 · 댓글 2", False),
        ("조회수 · 어제", 16, None, None, "공감 3 · 댓글 1 · 이웃 +1", False),
    ]
    assert fin["stats_note"] == "자동 · 10. 15. 14:23 읽음" and fin["stats_warn"] is None
    assert fin["detail"]["own_stats"] == {
        "day": TODAY, "read_at": "2026-10-15T14:23:05+09:00", "stale": False, "neighbors_from": "auto", "neighbors": 183,
        "visitors_today": 2, "visitors_total": 2751, "views_today": 5, "likes_today": 5, "comments_today": 2,
        "neighbor_change_today": 0, "buddy_requests_pending": 2,
        "yesterday": {"day": YESTERDAY, "views": 16, "likes": 3, "comments": 1, "neighbor_change": 1},
        "prev": {"day": YESTERDAY, "neighbors": 182, "visitors_total": 2735},
    }
    assert fin["links"][0] == {"label": "네이버 통계 열기", "url": "https://admin.blog.naver.com/derenlog/stat/today"}
    iss = next(c for c in data["cards"] if c["key"] == "blog_issue")
    assert iss["stats_warn"] == "어제 못 읽음" and all(s["stale"] for s in iss["stats"])
    assert iss["detail"]["own_stats"]["stale"] is True and iss["detail"]["own_stats"]["yesterday"] is None
    hot = next(c for c in data["cards"] if c["key"] == "blog_hotdeal")
    assert hot["detail"] is None and hot["stats_note"].startswith(STATS_NOTE) and [s["stale"] for s in hot["stats"]] == [False, False]
    assert "t=" not in json.dumps(data)  # 폼 토큰은 JSON 에 없음


def test_other_cards_still_show_two_tiles(db: Database, tmp_path: Path) -> None:
    """숫자 칸을 네 개까지 그리게 바꿨어도 다른 카드는 두 칸 그대로."""
    write_export(tmp_path, {"finance": own()})
    cfg = DashboardConfig(cards=[DashboardCardConfig(key="kakao", type="kakao_openchat"),
                                 DashboardCardConfig(key="blog_finance", type="naver_blog", options={"blog": "finance"})],
                          blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    ctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=Clock(),
                        blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=Clock()))
    page = Dashboard(cfg, ctx, user="owner", password="plum-river-cactus-42").page_html()
    assert card_html(page, "kakao").count('<div class="stat">') == 2
    assert card_html(page, "blog_finance").count('<div class="stat">') == 4
    assert "<span class=\"warn\">" not in page


def test_footer_mentions_the_daily_auto_read_only_when_own_stats_arrive(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, None, drop=True)  # 예전 Blog-Auto (own_stats 없음)
    dash = make_dash(db, tmp_path)
    assert "네이버 블로그 이웃 수·방문자·게시글 수와 오픈채팅 참여자는 카드에서 직접 넣어요." in dash.page_html()
    write_export(tmp_path, {"issue": own()}, written_at="2026-10-15T14:31:00+09:00")
    page = dash.page_html()
    assert "Blog-Auto 가 블로그마다 하루 한 번(날마다 다른 시각) 읽어 와요" in page
    assert "읽은 뒤에 이웃 수를 직접 넣으면 그날은 넣은 값이 보여요" in page


async def test_own_stats_never_make_the_server_request_naver(db: Database, tmp_path: Path, monkeypatch: Any) -> None:
    """자기 통계는 Blog-Auto 가 읽어 파일로 넘긴 값 — 딜봇은 카드를 그리고·JSON·다시 읽기·정해진 시각 읽기를 해도 요청하지 않는다."""
    import httpx

    calls: list[str] = []

    def no_sync(self: Any, request: httpx.Request, **kw: Any) -> Any:
        calls.append(str(request.url))
        raise AssertionError(f"HTTP 요청 {request.url}")

    async def no_async(self: Any, request: httpx.Request, **kw: Any) -> Any:
        calls.append(str(request.url))
        raise AssertionError(f"HTTP 요청 {request.url}")

    monkeypatch.setattr(httpx.Client, "send", no_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_async)
    write_export(tmp_path, {"finance": own(), "issue": own(neighbors=21), "hotdeal": own(neighbors=82)})
    dash = make_dash(db, tmp_path)
    assert dash.page_html().count('<p class="stats-note">자동 · 10. 15. 14:23 읽음</p>') == 3
    dash.json_text()
    assert await dash.refresh("blog_finance") == "reread"
    assert sorted(await dash.tick()) == ["blog_finance:live", "blog_hotdeal:live", "blog_issue:live"]
    assert calls == []
