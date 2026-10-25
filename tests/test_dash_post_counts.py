"""통계 탭 카드마다 '올린 글 오늘 N · 7일 N · 30일 N' (주인 10/25 '통계탭에서, 각 블로그별 올린 글 수, 각 채널별 올린글수 도' ·
'그냥 각 sns 탭에 추가만 해줘' · '너가 말한것도 다 각 탭에 쪼개서 넣어줘'). 네트워크 없이.

- 텔레그램: 봇 DB posts · 인스타: 봇 DB ig_posts ('올라갔을 수도' 줄 빼고) · 스레드: 봇 threads_jobs + Blog-Auto 스레드 줄 + 스레드 자동 (더함)
- 블로그 셋: Blog-Auto 기록 파일 post_counts.blogs (계약 export_v2.json — Blog-Auto 와 같은 파일)
- 오픈채팅: 보낸 기록이 없어 '— 기록 없음' · X: X 화면 초안함 밑 한 줄 ('올렸어요' 누른 것)
- 칸이 없는 예전 Blog-Auto 기록 파일이면 그 카드만 '—' (짐작한 숫자 없음)
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import render, xview
from dealbot.dashboard.blogauto import _post_counts, parse_v2
from dealbot.dashboard.model import CardView, PostCount, post_count
from dealbot.utils.timeutil import to_iso
from test_dash_state import bot, state, write_export  # noqa: F401 — 픽스처

TZ = ZoneInfo("Asia/Seoul")


def test_post_count_periods_skip_future_old_and_bad_rows() -> None:
    today = date(2026, 10, 25)
    got = post_count({"2026-10-25": 2, "2026-10-19": 1, "2026-10-18": 4, "2026-09-26": 1, "2026-09-25": 9,
                      "2026-10-26": 5, "bad": 3, "2026-10-24": -1, "2026-10-23": True, "2026-10-22": "2"}, today)
    assert (got.today, got.d7, got.d30, got.why) == (2, 3, 8, "")
    dated = post_count([date(2026, 10, 25), datetime(2026, 10, 25, 1, 0), date(2026, 10, 1)], today, sub="s")
    assert (dated.today, dated.d7, dated.d30, dated.sub) == (2, 2, 3, "s")
    assert post_count(None, today) == PostCount()


def test_parser_reads_the_contract_and_tolerates_a_missing_section() -> None:
    want = fixture("export_v2.json")["post_counts"]
    got = _post_counts(want)
    assert got == {"blogs": want["blogs"], "channels": want["channels"]}
    assert parse_v2(fixture("export_v2.json"))["post_counts"] == got
    data = fixture("export_v2.json")
    del data["post_counts"]
    assert parse_v2(data)["post_counts"] is None  # 예전 Blog-Auto
    assert _post_counts(None) is None and _post_counts([]) is None
    odd = _post_counts({"blogs": {"finance": {"2026-10-25": 2, "x": 1, "2026-10-24": 0, "2026-10-23": 10**6}, "issue": "?"},
                        "channels": {"threads_auto": None}})
    assert odd["blogs"] == {"finance": {"2026-10-25": 2}, "issue": None, "hotdeal": None}
    assert odd["channels"] == {"threads": None, "threads_auto": None, "x": None}


def _card(v: CardView) -> str:
    return render.stats_fragment(views=[v], today=date(2026, 10, 25), tz="Asia/Seoul", spark_days=30)


def test_card_line_numbers_dash_and_none() -> None:
    v = CardView(key="telegram", title="텔레그램 채널", type="telegram", kind="api", icon="",
                 posted=PostCount(today=2, d7=5, d30=1234, sub="30일 · 딜봇 3"))
    html = _card(v)
    assert re.search(r'<p class="posted">올린 글 오늘 <b class="num">2</b> · 7일 <b class="num">5</b> · '
                     r'30일 <b class="num">1,234</b><span class="psub">30일 · 딜봇 3</span></p>', html)
    assert ".stx .posted{" in html
    v.posted = PostCount(why="기록 없음 · <손으로>")
    html = _card(v)
    assert '<p class="posted">올린 글 <span class="none">— 기록 없음 · &lt;손으로&gt;</span></p>' in html and "오늘 <b" not in html
    v.posted = None
    assert 'class="posted"' not in _card(v)
    # 예전 웹 페이지(app=False) 카드에는 넣지 않음 — 통계 탭에만
    v.posted = PostCount(today=1)
    assert 'class="posted"' not in render.card_html(v, token="t", today=date(2026, 10, 25), tz="Asia/Seoul", spark_days=30)


def _kst(day_offset: int, hour: int = 0, minute: int = 1) -> datetime:
    d = datetime.now(TZ).date() + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def _fill_db(bot: DealBot) -> None:  # noqa: F811
    c = bot.db._conn
    with bot.db._lock:
        for at in (_kst(0), _kst(0, 0, 2), _kst(-3, 9), _kst(-40, 9)):  # 텔레그램 채널 글 (40일 전 것은 30일 밖)
            c.execute("INSERT INTO posts (product_id, source, price, dry_run, posted_at) VALUES ('p', 's', 1000, 0, ?)", (to_iso(at),))
        for media, at in (("m1", _kst(0)), (None, _kst(0)), ("m2", _kst(-10, 9))):  # None = 올리다 끊겨 '올라갔을 수도' — 안 셈
            c.execute("INSERT INTO ig_posts (kind, media_id, items, dry_run, posted_at) VALUES ('single', ?, '[]', 0, ?)",
                      (media, to_iso(at)))
        c.execute("INSERT INTO threads_jobs (kind, product_id, payload, status, post_id, dry_run, created_at, updated_at, posted_at) "
                  "VALUES ('deal', 'p', '{}', 'posted', '17', 0, ?, ?, ?)", (to_iso(_kst(0)),) * 3)
        c.execute("INSERT INTO threads_jobs (kind, product_id, payload, status, dry_run, created_at, updated_at) "
                  "VALUES ('deal', 'q', '{}', 'waiting', 0, ?, ?)", (to_iso(_kst(0)),) * 2)


def _cards(html: str) -> dict[str, str]:
    return dict(re.findall(r'<article class="card" id="card-([a-z_]+)">(.*?)</article>', html, flags=re.S))


async def test_each_stats_card_shows_its_own_post_counts(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    day = lambda n: _kst(n).date().isoformat()  # noqa: E731
    write_export(tmp_path / "blogauto.json", post_counts={
        "days": 30, "blogs": {"finance": {day(0): 2, day(-10): 1}, "issue": {}, "hotdeal": {day(-6): 3}},
        "channels": {"threads": {day(0): 2}, "threads_auto": None, "x": {day(0): 1, day(-8): 2}}})
    blogauto_status(settings)
    _fill_db(bot)
    res = await bot.web.dispatch("GET", "/dashboard/api/stats.html", session_headers(bot))
    cards = _cards(res[1])

    def line(key: str) -> str:
        m = re.search(r'<p class="posted">(.*?)</p>', cards[key])
        assert m, key
        return re.sub(r"<[^>]+>", "", m.group(1))

    assert line("telegram") == "올린 글 오늘 2 · 7일 3 · 30일 3"
    assert line("instagram") == "올린 글 오늘 1 · 7일 1 · 30일 2"
    assert line("threads") == "올린 글 오늘 3 · 7일 3 · 30일 330일 · 딜봇 1 · 블로그 2 · 스레드 자동 기록 없음"
    assert line("blog_finance") == "올린 글 오늘 2 · 7일 2 · 30일 3"
    assert line("blog_issue") == "올린 글 오늘 0 · 7일 0 · 30일 0"
    assert line("blog_hotdeal") == "올린 글 오늘 0 · 7일 3 · 30일 3"
    assert line("kakao").startswith("올린 글 — 기록 없음")
    assert 'class="posted"' not in cards["coupang"]  # 글을 올리는 곳이 아님
    x = (await state(bot))["x"]
    assert x["posted_line"] == "X에 올린 글 (올렸어요 누른 것) 오늘 1 · 7일 1 · 30일 3"

    # 예전 Blog-Auto (post_counts 칸 없음): 블로그 카드·X 는 '—', 스레드는 딜봇 몫만 (못 읽은 쪽은 '기록 없음')
    data = json.loads((tmp_path / "blogauto.json").read_text(encoding="utf-8"))
    del data["post_counts"]
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    cards = _cards((await bot.web.dispatch("GET", "/dashboard/api/stats.html", session_headers(bot)))[1])
    assert line("blog_finance") == "올린 글 — 기록 없음 · 블로그 프로그램이 아직 안 보내요"
    assert line("threads") == "올린 글 오늘 1 · 7일 1 · 30일 130일 · 딜봇 1 · 블로그 기록 없음 · 스레드 자동 기록 없음"
    assert line("telegram") == "올린 글 오늘 2 · 7일 3 · 30일 3"
    assert (await state(bot))["x"]["posted_line"].endswith("— 기록 없음 · 블로그 프로그램이 아직 안 보내요")


def test_x_posted_line_counts_kst_days() -> None:
    now = datetime(2026, 10, 25, 0, 30, tzinfo=TZ).timestamp()  # 한국 0시 반 — UTC 로는 아직 24일
    pc = {"channels": {"x": {"2026-10-25": 1, "2026-10-24": 2}}}
    assert xview.posted_line(pc, TZ, now) == "X에 올린 글 (올렸어요 누른 것) 오늘 1 · 7일 3 · 30일 3"
    assert "기록 없음" in xview.posted_line(None, TZ, now) and "기록 없음" in xview.posted_line({"channels": {"x": None}}, TZ, now)
