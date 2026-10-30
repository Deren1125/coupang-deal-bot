"""로그인 없이 읽은 블로그 숫자 (주인 10/30 — Blog-Auto public_stats): 블로그 통계 카드 밑에 공감·댓글 많은 글 · 검색 순위 · 오르는 키워드."""

from __future__ import annotations

from dealbot.dashboard.blogauto import _public_stats
from dealbot.dashboard.sources.naver_blog import NaverBlogSource

RAW = {"finance": {"at": "2026-10-30T11:40:00", "visitors_today": 7,
                   "posts": [{"title": "아이폰 듀오 출시", "likes": 7, "comments": 1}, {"title": "로또", "likes": 0, "comments": None},
                             {"title": "  ", "likes": 3}, {"title": "음수", "likes": -1, "comments": True}],
                   "ranks": [{"title": "키움 이벤트", "q": "키움 이벤트", "rank": 4}, {"title": "옛 글", "q": "옛날", "rank": None}],
                   "trends": [{"keyword": "니트 가디건", "growth": 0.62}, {"keyword": "떨어짐", "growth": -0.2},
                              {"keyword": "이상함", "growth": float("nan")}, {"keyword": "참거짓", "growth": True}]},
       "stranger": {"posts": []}, "issue": "not a dict"}


def test_parser_keeps_only_clean_rows_per_blog() -> None:
    got = _public_stats(RAW)
    assert list(got) == ["finance"]
    f = got["finance"]
    assert [p["title"] for p in f["posts"]] == ["아이폰 듀오 출시", "로또", "음수"] and f["posts"][2] == {"title": "음수", "likes": None, "comments": None}
    assert f["ranks"][1]["rank"] is None and [t["keyword"] for t in f["trends"]] == ["니트 가디건", "떨어짐"]
    assert _public_stats(None) == {} and _public_stats([1]) == {}


def test_blog_card_lists_liked_posts_ranks_and_rising_keywords() -> None:
    groups = NaverBlogSource.public_groups(_public_stats(RAW)["finance"])
    assert [g.title for g in groups] == ["공감·댓글 많은 글", "검색 순위", "오르는 키워드"]
    assert [(x.title, x.pill, x.sub) for x in groups[0].entries] == [("아이폰 듀오 출시", "공감 7", "댓글 1")]  # 0·0 인 글은 뺌
    assert [(x.pill, x.hot) for x in groups[1].entries] == [("4위", True), ("100위 밖", False)]
    assert [(x.title, x.pill, x.hot) for x in groups[2].entries] == [("니트 가디건", "+62%", True)]  # 떨어지는 키워드는 뺌
    assert NaverBlogSource.public_groups(None) == [] and NaverBlogSource.public_groups({"posts": [], "ranks": [], "trends": []}) == []
