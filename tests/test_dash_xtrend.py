"""지금 뜨는 주제 · 내 주제로 글 쓰기 (주인 11/16 — dashboard/xtrend.py · static/xtrend.js). 바깥 요청 없음 — 출처는 가짜 답.

1) 읽기: 구글 트렌드 RSS·FxTwitter 검색 답을 조심해서 읽음 (선언 있는 XML·남의 답글·오래된 글·모양 다른 값은 버림).
2) 새로 보기: 출처마다 한 번씩 · 순위 = X 글 조회수 합 · 대표 글 · 숨김(정치·사고·혐오·성인 — 검색도 안 함) · 오른 정도 ·
   한 번·하루 상한 · 막히면 그 출처만 6시간 끔 (다시 부르지 않음) · 10분에 한 번.
3) 내 주제로 글 쓰기: xstudio 와 같은 길 (하루 몫·Opus 까지·AI 한 번) · 주인 주제는 바깥에 안 보냄 · 규칙에 걸린 초안은 버림 · 숫자 경고.
4) ✓ 올렸어요 → 오늘 X 체크리스트(xtoday)에 '내 주제' 줄 + 올린 글 기록 · 10분 안 취소.
5) 화면 API (로그인·같은 사이트 토큰) · xtrend.js (node): 카드·정렬·숨김·글 쓰기 칸·복사·체크.
"""

from __future__ import annotations

import gzip
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings, XTodayConfig, XTrendConfig
from dealbot.dashboard import shell
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from dealbot.dashboard import xtrend as xr
from test_dash_xtoday import FABLE, NODE, STATIC, FakeDb, FakeNews, FakeRouter, at, needs_node

NOW = at("14:00", "2026-11-16")
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>
<item><title>수능 연기</title><ht:approx_traffic>20,000+</ht:approx_traffic><pubDate>Mon, 16 Nov 2026 12:10:00 +0900</pubDate>
<ht:news_item><ht:news_item_title>교육부 &quot;수능 연기 없다&quot;</ht:news_item_title><ht:news_item_url>https://news.example/a1</ht:news_item_url>
<ht:news_item_source>가나일보</ht:news_item_source></ht:news_item>
<ht:news_item><ht:news_item_title>나쁜 주소</ht:news_item_title><ht:news_item_url>javascript:alert(1)</ht:news_item_url></ht:news_item></item>
<item><title>용인반도체클러스터</title><ht:approx_traffic>100+</ht:approx_traffic><pubDate>Mon, 16 Nov 2026 13:10:00 +0900</pubDate></item>
<item><title>국회 탄핵 표결</title><ht:approx_traffic>5만+</ht:approx_traffic></item>
<item><title>"</title></item>
</channel></rss>"""


def post(i: int, author: str, text: str, *, views: int = 1000, replies: int = 1, likes: int = 10, age_h: float = 2.0, **more: Any) -> dict[str, Any]:
    return {"type": "status", "id": str(10**9 + i), "url": "x", "text": text, "author": {"screen_name": author}, "views": views, "replies": replies,
            "likes": likes, "reposts": 0, "created_timestamp": NOW - age_h * 3600, "lang": "ko", **more}


def fx(*posts: dict[str, Any], cursor: str = "") -> str:
    return json.dumps({"code": 200, "results": list(posts), "cursor": {"top": "t", "bottom": cursor}}, ensure_ascii=False)


SAMPLE = fx(post(1, "aaa", "#엔하이픈 무대 미쳤음", views=50000), post(2, "bbb", "오늘 #엔하이픈 컴백", views=30000),
            post(3, "ccc", "치즈냥이 귀여움", views=900000), post(4, "ddd", "치즈냥이는 멍청하다는 편견", views=5), post(5, "eee", "우리집 치즈냥이", views=5))


class FakeGet:
    """출처 대신: 주소·검색어마다 정해 둔 답. 받은 요청을 모은다."""

    def __init__(self, **over: Any) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.rss: tuple[int, str] = over.get("rss", (200, RSS))
        self.sample: tuple[int, str] = over.get("sample", (200, SAMPLE))
        self.topic: dict[str, tuple[int, str]] = over.get("topic", {})
        self.fx_status: int = over.get("fx_status", 200)

    async def __call__(self, url: str, params: dict[str, str]) -> tuple[int, str]:
        self.calls.append((url, dict(params)))
        if url == xr.GOOGLE_RSS:
            return self.rss
        assert url == xr.FX_SEARCH
        if self.fx_status != 200:
            return self.fx_status, ""
        if params["q"] == xr.SAMPLE_QUERY:
            return self.sample
        return next((v for k, v in self.topic.items() if k in params["q"]), (200, fx()))

    def fx_calls(self) -> list[dict[str, str]]:
        return [p for u, p in self.calls if u == xr.FX_SEARCH]


class Ask:
    def __init__(self, drafts: list[Any] | None = None, *, ok: bool = True) -> None:
        self.drafts = drafts if drafts is not None else [{"text": "수능 연기 얘기 나오는 게 이상함\n준비한 사람만 손해임", "angle": "손해 보는 쪽"},
                                                         {"text": "연기보다 대책이 먼저임\n순서가 뒤집혔음", "angle": "순서"}]
        self.ok = ok
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        return {"ok": self.ok, "why": "" if self.ok else "광고 글", "drafts": self.drafts}


async def nosleep(_s: float) -> None:
    return None


def desk(*, get: FakeGet | None = None, ask: Ask | None = None, hots: list[dict[str, Any]] | None = None, model: str | None = None,
         **cfg: Any) -> tuple[xr.XTrend, FakeGet, Ask, list[float], xt.XToday]:
    now = [NOW]
    db, get, ask = FakeDb(), get or FakeGet(), ask or Ask()
    studio = xs.Studio(db, ask=ask, hot=lambda: list(hots or []), clock=lambda: now[0], model=model)
    tconf, conf = XTodayConfig(goal_posts=False), XTrendConfig(**cfg)
    today = xt.XToday(db, studio, cfg=lambda: tconf, router=FakeRouter, news=FakeNews(None), clock=lambda: now[0])
    return xr.XTrend(db, studio, today, cfg=lambda: conf, get=get, clock=lambda: now[0], sleep=nosleep), get, ask, now, today


# ---------------------------------------------------------------- 1) 읽기
def test_google_rss_is_read_carefully() -> None:
    rows = xr.google_rows(RSS, NOW)
    assert rows is not None and [(r["kw"], r["traffic"]) for r in rows] == [("수능 연기", 20000), ("용인반도체클러스터", 100), ("국회 탄핵 표결", 50000)]
    assert rows[0]["news"] == [{"title": '교육부 "수능 연기 없다"', "link": "https://news.example/a1", "source": "가나일보"}]  # javascript: 주소는 버림
    assert rows[0]["since"] == at("12:10", "2026-11-16") and rows[2]["since"] == 0.0
    assert xr.google_rows('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "b">]><rss><channel/></rss>', NOW) is None  # 선언·엔티티는 풀지 않음
    assert xr.google_rows("<html></html>", NOW) is None and xr.google_rows("not xml", NOW) is None
    assert [xr.traffic(v) for v in ("2,000+", "20K+", "2만+", "1M+", "", None, "많음")] == [2000, 20000, 20000, 1000000, 0, 0, 0]


def test_fx_answer_keeps_only_recent_korean_original_posts() -> None:
    data = json.loads(fx(post(1, "aaa", "수능 연기 반대", views=7, replies=3), post(2, "bbb", "old", age_h=30), post(3, "ccc", "답글", replying_to={"x": 1}),
                         post(4, "ddd", "리포스트", reposted_by={"x": 1}), post(5, "bad name!", "글"), post(6, "eee", "english only", lang="en"),
                         post(7, "fff", "성인", possibly_sensitive=True), {"type": "tombstone"}, "x"))
    got = xr.fx_posts(data, NOW)
    assert got is not None and [(p["author"], p["sensitive"]) for p in got] == [("aaa", False), ("fff", True)]
    assert got[0]["url"] == "https://x.com/aaa/status/1000000001" and (got[0]["views"], got[0]["replies"]) == (7, 3)  # 주소는 서버가 만든다
    assert xr.fx_posts({"code": 404, "results": []}, NOW) is None and xr.fx_posts([], NOW) is None
    posts = xr.fx_posts(json.loads(SAMPLE), NOW) or []
    assert xr.native_topics(posts) == ["치즈냥이", "#엔하이픈"]  # 낱말은 3명 · 해시태그는 2명 넘게 같이 쓴 것 (조회수 합 큰 순)
    assert [xr.hidden_why(t) for t in ("국회 탄핵 표결", "아파트 화재 사망", "한남 조롱", "야동 사이트", "수능 연기")] == ["정치", "사고", "혐오", "성인", ""]
    assert xr.clean_kw('수능" OR from:x (연기)') == "수능 OR from x 연기" and xr.clean_kw("!") == "" and xr.row_id("#엔하이픈") == xr.row_id("엔하이픈")


# ---------------------------------------------------------------- 2) 새로 보기
async def test_refresh_ranks_by_views_with_top_posts_and_hides_politics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xr, "FX_PER_REFRESH", 8)  # 순위 셈만 보는 시험 — 한 번에 주제를 다 보게 (상한은 아래 시험)
    topic = {"수능 연기": (200, fx(post(11, "kim", "수능 연기 반대함", views=300000, replies=40), post(12, "lee", "수능 연기 찬성", views=100000, replies=900),
                                    post(13, "rude", "수능 연기 씨발", views=999999, replies=1))),
             "치즈냥이": (200, fx(post(3, "ccc", "치즈냥이 귀여움", views=900000)))}
    d, get, _a, now, _t = desk(get=FakeGet(topic=topic), hots=[{"id": "h1", "keyword": "쿠팡 과징금", "text": "요약",
                                                                "sources": [{"title": "쿠팡에 과징금", "link": "https://news.example/c"}]}])
    assert d.view()["stale"] is True and d.view()["rows"] == []
    assert await d.refresh() == 6
    v = d.view()
    top = v["rows"][0]
    assert (top["kw"], top["views"], top["replies"], top["n"], top["traffic"], top["since"], top["src"]) == ("수능 연기", 1399999, 941, 3, 20000, "12:10", ["google"])
    assert [p["author"] for p in top["posts"]] == ["kim", "lee"]  # 욕이 든 글은 대표 글로 안 보임 (수에는 셈)
    assert top["posts"][0]["url"] == "https://x.com/kim/status/1000000011" and top["x"] == "https://x.com/search?q=%EC%88%98%EB%8A%A5%20%EC%97%B0%EA%B8%B0"
    assert top["news"] == [{"title": '교육부 "수능 연기 없다"', "link": "https://news.example/a1", "source": "가나일보"}] and top["move"] is None
    assert [r["kw"] for r in v["rows"]] == ["수능 연기", "치즈냥이", "#엔하이픈", "용인반도체클러스터", "쿠팡 과징금"]  # 조회수 합 → 검색량
    assert v["rows"][1]["src"] == ["x"] and v["rows"][4]["src"] == ["hot"] and v["rows"][4]["news"][0]["link"] == "https://news.example/c"
    assert v["hidden"] == [{"kw": "국회 탄핵 표결", "why": "정치"}] and v["stale"] is False and v["note"] == ""
    qs = [p["q"] for p in get.fx_calls()]
    assert qs[0] == xr.SAMPLE_QUERY and len(qs) == 1 + 5 and not any("탄핵" in q for q in qs)  # 숨긴 주제는 검색도 안 함
    assert '"수능 연기" lang:ko since:2026-11-15 min_faves:20 -filter:replies' in qs and "#엔하이픈 lang:ko since:2026-11-15 min_faves:20 -filter:replies" in qs
    assert all(p["count"] == "20" for p in get.fx_calls()) and [u for u, _p in get.calls].count(xr.GOOGLE_RSS) == 1
    assert (d.state(now[0])["fx"], d.state(now[0])["google"]) == (6, 1)
    # 한 시간 뒤: 순위가 바뀌면 오른 칸 수 · 처음 보이면 new
    now[0] += 3600
    get.topic["용인반도체클러스터"] = (200, fx(post(21, "sk", "용인반도체클러스터 착공", views=5_000_000, replies=10, age_h=1)))
    get.rss = (200, RSS.replace("<item><title>\"</title></item>", "<item><title>새 검색어</title></item>"))
    await d.refresh()
    moves = {r["kw"]: (r["rank"], r["move"]) for r in d.view()["rows"]}
    assert moves["용인반도체클러스터"] == (1, 3) and moves["수능 연기"] == (2, -1) and moves["새 검색어"][1] == "new"
    st = d.state(now[0])
    assert (st["fx"], st["google"]) == (len(get.fx_calls()), 2) and len(st["hist"]) == 2


async def test_refresh_stays_inside_caps_and_a_blocked_source_rests_six_hours() -> None:
    many = "".join(f"<item><title>검색어{i}</title></item>" for i in range(30))
    d, get, _a, now, _t = desk(get=FakeGet(rss=(200, RSS.replace("</channel>", many + "</channel>"))),
                               hots=[{"id": f"h{i}", "keyword": f"화제{i}", "text": "t"} for i in range(30)])
    await d.refresh()
    v = d.view()
    assert len(v["rows"]) + len(v["hidden"]) == xr.TOPIC_MAX and len(get.fx_calls()) == xr.FX_PER_REFRESH  # 구글 8 + X 낱말 + 화제 · 표본 1 + 주제 4
    assert [r["seen"] for r in v["rows"]].count(True) == xr.FX_PER_REFRESH - 1
    rounds = 1 + -(-13 // (xr.FX_PER_REFRESH - 1))  # 다음 번에는 아직 못 본 주제부터 — 몇 번이면 다 봄 (앞서 본 수는 그대로)
    for _ in range(rounds - 1):
        now[0] += xr.CACHE_S
        await d.refresh()
    qs = [p["q"] for p in get.fx_calls()]
    assert len(qs) == rounds * xr.FX_PER_REFRESH and len(set(qs)) == 1 + 13 and all(r["seen"] for r in d.view()["rows"])
    # 하루 몫
    d, get, _a, now, _t = desk(fx_per_day=3)
    await d.refresh()
    assert len(get.fx_calls()) == 3 and "오늘 몫을 다 썼어요" in d.view()["note"]
    rows = d.view()["rows"]
    assert rows and [r["seen"] for r in rows].count(False) >= 1
    now[0] += 3600
    await d.refresh()
    assert len(get.fx_calls()) == 3 and d.view()["rows"]  # 몫이 없으면 더 부르지 않고, 구글 검색어만으로
    # 403 → 그 출처만 바로 끔 (다시 부르지 않음) · 6시간 뒤 다시
    d, get, _a, now, _t = desk(get=FakeGet(fx_status=403))
    await d.refresh()
    assert len(get.fx_calls()) == 1 and "못 읽어서 쉬고 있어요 (20:00 까지)" in d.view()["note"]
    assert [r["kw"] for r in d.view()["rows"]] == ["수능 연기", "용인반도체클러스터"]  # 구글 검색어는 그대로 (검색량 순)
    now[0] += 3600
    await d.refresh()
    assert len(get.fx_calls()) == 1
    now[0] += xr.OFF_S
    get.fx_status = 200
    await d.refresh()
    assert len(get.fx_calls()) > 1 and d.view()["note"] == ""
    # 그 밖에 못 읽은 때(404·500 — 몰아 부르면 404 를 준다)는 그 차례를 끝내고 20분 쉼
    d, get, _a, now, _t = desk(get=FakeGet(fx_status=404), google=False,
                               hots=[{"id": f"h{i}", "keyword": f"화제{i}", "text": "t"} for i in range(xr.FX_PER_REFRESH - 3)])
    await d.refresh()
    assert len(get.fx_calls()) == 1 and d.state(now[0])["off"]["fx"] == now[0] + xr.SOFT_OFF_S and [u for u, _p in get.calls if u == xr.GOOGLE_RSS] == []
    assert len(d.view()["rows"]) == xr.FX_PER_REFRESH - 3 and not any(r["seen"] for r in d.view()["rows"])
    now[0] += xr.SOFT_OFF_S + 1
    get.fx_status = 200
    await d.refresh()
    assert len(get.fx_calls()) == 1 + xr.FX_PER_REFRESH and all(r["seen"] for r in d.view()["rows"])
    d, get, _a, now, _t = desk()
    await d.refresh()
    before = d.view()["rows"]
    now[0] += 3600
    get.fx_status, get.rss = 429, (403, "")
    await d.refresh()
    assert [r["kw"] for r in d.view()["rows"]] == [r["kw"] for r in before] and d.view()["age_s"] == 3600
    off = desk(enabled=False)[0]
    with pytest.raises(xs.StudioError) as e:
        off.start()
    assert e.value.code == "off"


async def test_404_or_429_twice_in_a_row_rests_six_hours_and_links_are_https_only() -> None:
    d, get, _a, now, _t = desk(get=FakeGet(fx_status=429), google=False)
    await d.refresh()
    assert len(get.fx_calls()) == 1 and d.state(now[0])["off"]["fx"] == now[0] + xr.SOFT_OFF_S  # 한 번은 20분
    now[0] += xr.SOFT_OFF_S + 1
    get.fx_status = 404
    await d.refresh()
    assert len(get.fx_calls()) == 2 and d.state(now[0])["off"]["fx"] == now[0] + xr.OFF_S  # 연속 두 번이면 6시간
    now[0] += xr.OFF_S + 1
    get.fx_status = 200
    await d.refresh()
    assert d.state(now[0])["streak"]["fx"] == 0  # 읽히면 처음부터
    get.fx_status = 404
    now[0] += xr.CACHE_S
    await d.refresh()
    assert d.state(now[0])["off"]["fx"] == now[0] + xr.SOFT_OFF_S
    assert xr.https_link("http://news.example/a") == "" and xr.https_link("javascript:alert(1)") == ""
    assert xr.https_link("https://news.example/a") == "https://news.example/a"
    rows = xr.google_rows(RSS.replace("https://news.example/a1", "http://news.example/a1"), NOW) or []
    assert rows and rows[0]["news"] == []


async def test_refresh_button_runs_in_the_background_once_per_ten_minutes() -> None:
    d, get, _a, now, _t = desk()
    status, out = d.start()
    assert status == 200 and out["started"] is True and out["busy"] is True
    assert d.start()[1]["busy"] is True  # 도는 중에 또 눌러도 한 번만
    await d.task  # type: ignore[misc]
    calls = len(get.calls)
    assert calls > 0 and d.view()["busy"] is False and d.view()["rows"]
    with pytest.raises(xs.StudioError) as e:
        d.start()
    assert (e.value.status, e.value.code) == (429, "rate") and len(get.calls) == calls
    now[0] += xr.REFRESH_MIN_S + 1
    d.start()
    await d.task  # type: ignore[misc]
    assert len(get.calls) > calls


# ---------------------------------------------------------------- 3) 내 주제로 글 쓰기
async def test_my_topic_draft_goes_the_xstudio_way_and_never_leaves_the_server() -> None:
    d, get, ask, now, _t = desk()
    out = await d.draft({"topic": "수능 연기 논란", "memo": "준비한 사람만 손해 본다고 생각함. 응시자 52만 명.", "tone": "polite", "length": "short", "tags": False})
    assert out["status"] == "ok" and len(ask.calls) == 1 and get.calls == []  # AI 한 번 · 주인 주제는 바깥 검색에 안 씀
    system, user, schema = ask.calls[0]
    assert "[재료] 에 있는 것만" in system and "존댓말" in system and "1~2줄" in system and "해시태그(#) 금지" in system and schema is xr.MINE_SCHEMA
    assert "[주제] 수능 연기 논란" in user and "응시자 52만 명" in user and "[참고]" not in user
    s = out["mine"][0]
    assert s["id"] == out["made"] and s["topic"] == "수능 연기 논란" and [x["angle"] for x in s["drafts"]] == ["손해 보는 쪽", "순서"]
    assert out["left"] == xs.DRAFT_PER_DAY - 1 and out["model"] == "Opus" and all(x["done"] is False for x in s["drafts"])
    # 순위에서 고른 주제면 대표 글·기사 제목이 참고로 · 숨긴 주제는 쓰지 않음
    await d.refresh()
    row = next(r for r in d.view()["rows"] if r["kw"] == "수능 연기")
    await d.draft({"topic": row["kw"], "memo": "", "trend": row["id"], "tags": True, "n": 1})
    system, user, _s = ask.calls[1]
    assert "[참고]" in user and "기사 제목: 교육부" in user and "(비어 있음)" in user and "해시태그 1~2개" in system and "초안 1개" in system
    hid = next(r for r in d.state(now[0])["rows"] if r.get("hidden"))
    for body, code in (({"topic": hid["kw"], "trend": hid["id"]}, "declined"), ({"topic": "아파트 화재 사망자"}, "declined"), ({"topic": "가"}, "bad_args"),
                       ({"topic": "주제", "memo": "가" * (xr.MEMO_MAX + 1)}, "too_long"), ({"topic": "야동 추천"}, "declined")):
        with pytest.raises(xs.StudioError) as e:
            await d.draft(body)
        assert e.value.code == code, body
    assert len(ask.calls) == 2 and d.view()["left"] == xs.DRAFT_PER_DAY - 2  # 거절은 AI 도 하루 몫도 안 씀
    assert len(d.view()["mine"]) == 2 and d.view()["mine"][0]["topic"] == row["kw"]  # 새 묶음이 위
    now[0] += 86400
    assert d.view()["mine"] == []  # 초안은 그날만


async def test_my_topic_drafts_are_linted_and_number_checked() -> None:
    bad = [{"text": "링크 있음 https://a.example", "angle": "a"}, {"text": "#태그 있음", "angle": "b"}, {"text": "@누구 봐라", "angle": "c"},
           {"text": "좋아요 눌러 주세요", "angle": "d"}, {"text": "가" * 150, "angle": "e"}, {"text": "유족 얘기", "angle": "f"}, "x",
           {"text": "응시자 52만 명인데 연기하면 60만 명이 흔들림", "angle": "숫자"}, {"text": "여긴 [확인 필요] 임", "angle": "빈칸"},
           {"text": "응시자 52만 명인데 연기하면 60만 명이 흔들림", "angle": "같은 글"}]
    d, _g, ask, _n, _t = desk(ask=Ask(bad[5:]))
    ds = (await d.draft({"topic": "수능 연기", "memo": "응시자 52만 명"}))["mine"][0]["drafts"]
    assert [x["angle"] for x in ds] == ["숫자", "빈칸"] and "재료에 없는 숫자: 60" in ds[0]["warn"][0] and "[확인 필요]" in ds[1]["warn"][0]
    d, _g, ask, _n, _t = desk(ask=Ask(bad[:5]))
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "수능 연기"})
    assert e.value.code == "ai_failed" and len(ask.calls) == 1  # 다시 묻지 않음 (AI 한 번 = 묶음 하나)
    # 해시태그를 켜면 3개까지 · 길게는 280 을 넘어도 됨
    d, _g, ask, _n, _t = desk(ask=Ask([{"text": "수능 얘기\n#수능 #연기", "angle": "t"}, {"text": "#a #b #c #d", "angle": "많음"}]))
    assert [x["angle"] for x in (await d.draft({"topic": "수능 연기", "tags": True}))["mine"][0]["drafts"]] == ["t"]
    d, _g, ask, _n, _t = desk(ask=Ask([{"text": "가" * 400, "angle": "긴 글"}, {"text": "가" * (xr.LONG_HI + 1), "angle": "너무 김"}]))
    s = (await d.draft({"topic": "수능 연기", "length": "long"}))["mine"][0]
    assert s["long"] is True and [x["angle"] for x in s["drafts"]] == ["긴 글"]
    d, _g, ask, _n, _t = desk(ask=Ask(ok=False))
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "수능 연기"})
    assert e.value.code == "declined" and "광고 글" in e.value.text


async def test_my_topic_shares_the_daily_draft_budget_and_is_never_written_by_fable(monkeypatch: pytest.MonkeyPatch) -> None:
    d, _g, ask, _n, _t = desk()
    d.studio._save(xs.KV_DAY, {"day": xs._day(NOW), "draft": xs.DRAFT_PER_DAY})
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "수능 연기"})
    assert (e.value.status, e.value.code) == (429, "day") and ask.calls == []
    assert desk(model=FABLE)[0].studio.model == "opus"  # 설정 이름이 Fable 이면 Opus 로
    d, _g, ask, _n, _t = desk()
    d.studio.model = FABLE  # 설정이 뒤에서 바뀌어도
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"topic": "수능 연기"})
    assert e.value.code == "ai_off" and ask.calls == [] and d.view()["left"] == xs.DRAFT_PER_DAY
    for bad in ("fable", "mythos"):
        assert bad not in xr.MINE_SYSTEM.lower()


# ---------------------------------------------------------------- 4) ✓ 올렸어요
async def test_posted_puts_the_draft_on_todays_checklist_and_undoes_within_ten_minutes() -> None:
    d, _g, _a, now, today = desk()
    out = await d.draft({"topic": "수능 연기 논란", "memo": "준비한 사람만 손해"})
    sid = out["made"]
    status, v = d.posted({"set": sid, "i": 1, "text": "내가 고친 글임"})
    assert status == 200 and [x["done"] for x in v["mine"][0]["drafts"]] == [False, True] and v["mine"][0]["drafts"][1]["undo_s"] == 600
    tv = today.view()
    item = tv["items"][0]
    assert (item["kind"], item["keyword"], item["slot"], item["done"], item["preview"]) == ("mine", "수능 연기 논란", "", True, "내가 고친 글임")
    assert d.studio.post_stats()["today"] == 1 and d.studio.posted_rows()[0]["topic"] == item["id"] and xs.TID_RE.match(item["id"])
    assert today._free_slots(today.plan(), now[0]) == [t for t in xs.post_times("2026-11-16") if t >= "13:30"]  # 시각 없는 줄 — 그날 열 시각은 그대로
    d.posted({"set": sid, "i": 1})  # 또 눌러도 한 번
    assert d.studio.post_stats()["today"] == 1 and len(today.view()["items"]) == 1
    now[0] += 300
    assert d.posted({"set": sid, "i": 1, "op": "undo"})[1]["mine"][0]["drafts"][1]["done"] is False and d.studio.post_stats()["today"] == 0
    d.posted({"set": sid, "i": 0})
    now[0] += 601
    with pytest.raises(xs.StudioError) as e:
        d.posted({"set": sid, "i": 0, "op": "undo"})
    assert e.value.code == "late"
    for body in ({"set": sid, "i": 5}, {"set": "m000000000", "i": 0}, {"set": sid, "i": True}, {"set": sid}, {"set": sid, "i": 0, "text": "가" * 9000}):
        with pytest.raises(xs.StudioError):
            d.posted(body)


# ---------------------------------------------------------------- 5) 화면 API · xtrend.js
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_endpoints_need_login_and_same_site_token(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", xr.STATE_PATH, anon))[0] == 401
    h = session_headers(bot)
    status, v, headers = await get_json(bot, xr.STATE_PATH, h)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert v["v"] == 1 and v["on"] is True and v["rows"] == [] and v["hidden"] == [] and v["mine"] == [] and v["stale"] is True and v["model"] == "Opus"
    assert v["left"] == xs.DRAFT_PER_DAY and v["tones"][0] == ["eum", "음슴체"] and [k for k, _l in v["lengths"]] == ["short", "normal", "long"]
    for path, body in ((xr.REFRESH_PATH, {}), (xr.DRAFT_PATH, {"topic": "수능 연기"}), (xr.POSTED_PATH, {"set": "m000000000", "i": 0})):
        assert (await post_json(bot, path, body, session_headers(bot, csrf=False)))[0] == 403, path
        assert (await post_json(bot, path, body, anon))[0] in (401, 403), path
    desk_: xr.XTrend = bot.dashboard.api.xtrend  # type: ignore[union-attr]
    get, ask = FakeGet(), Ask()
    desk_.get, desk_.sleep = get, nosleep
    bot.dashboard.api.xstudio._ask_fn = ask  # type: ignore[union-attr]
    status, out, _ = await post_json(bot, xr.REFRESH_PATH, {}, h)
    assert status == 200 and out["started"] is True
    await desk_.task  # type: ignore[misc]
    assert (await get_json(bot, xr.STATE_PATH, h))[1]["rows"] and (await post_json(bot, xr.REFRESH_PATH, {}, h))[0] == 429
    assert all(set(p) <= {"q", "feed", "count", "cursor", "geo"} for _u, p in get.calls)
    status, out, _ = await post_json(bot, xr.DRAFT_PATH, {"rid": "r_abc123", "topic": "수능 연기", "memo": "손해"}, h)
    assert status == 200 and len(out["mine"][0]["drafts"]) == 2
    assert (await post_json(bot, xr.DRAFT_PATH, {"rid": "r_abc123", "topic": "수능 연기", "memo": "손해"}, h))[0] == 200 and len(ask.calls) == 1  # 같은 rid — AI 한 번
    assert (await post_json(bot, xr.DRAFT_PATH, {"topic": "가"}, h))[0] == 400
    assert (await post_json(bot, xr.DRAFT_PATH, {"topic": "수능", "memo": "가" * 9000}, h))[0] in (400, 413)
    status, out, _ = await post_json(bot, xr.POSTED_PATH, {"set": out["made"], "i": 0}, h)
    assert status == 200 and out["mine"][0]["drafts"][0]["done"] is True
    tv = (await get_json(bot, xt.STATE_PATH, h))[1]
    assert [(i["kind"], i["done"]) for i in tv["items"]] == [("mine", True)]
    assert (await post_json(bot, xr.POSTED_PATH, {"set": "m000000000", "i": 0}, h))[0] == 404
    assert (await bot.web.dispatch("GET", "/dashboard/static/xtrend.js", anon))[0] == 200
    cfg = bot.settings.dashboard.x_trend
    assert (cfg.enabled, cfg.fx, cfg.google, cfg.fx_per_day, cfg.google_per_day) == (True, True, True, 200, 100)
    assert xr.FX_PER_REFRESH * (86400 // xr.CACHE_S) >= cfg.fx_per_day >= xr.FX_PER_REFRESH * 24  # 하루 상한이 한 시간에 한 번은 넘고 15분마다 보다는 적게


def test_xtrend_js_is_local_safe_and_loaded_by_shell() -> None:
    js = (STATIC / "xtrend.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "xtrend.js")], capture_output=True).returncode == 0
    code = re.sub(r"/\*.*?\*/|  //[^\n]*", "", js, flags=re.S)
    assert "innerHTML" not in code and "outerHTML" not in code and "eval(" not in code and "new Function" not in code and "document.write" not in code
    assert re.findall(r"https?://[^\s'\"`)]+", code) == []  # 바깥 주소 없음 — X 글·기사·검색 링크는 서버 값
    assert "/dashboard/api/xtrend/" in code and "X-Dash-Token" in code and "intent" not in code and ".click()" not in code  # 자동 게시·자동 클릭 없음
    for label in ("지금 뜨는 주제", "이걸로 글 쓰기", "내 주제로 글 쓰기", "초안 만들기", "댓글 많은 순", "조회순", "급상승", "숨긴 주제", "✓ 올렸어요", "복사", "X에서 보기", "새로 보기"):
        assert label in code, label
    raw = (STATIC / "xtrend.js").read_bytes()
    assert len(raw) <= 24 * 1024 and len(gzip.compress(raw, 9)) <= 8 * 1024
    page = shell.page({})
    tag = f'<script defer src="{shell.asset_url("xtrend.js")}"></script>'
    assert shell.XTREND_JS == "xtrend.js" and tag in page and page.index(tag) > page.index(shell.asset_url("xengage.js"))
    assert len((STATIC / "app.js").read_bytes()) <= 96000  # app.js 는 그대로 (새 화면은 따로)


SHIM = r"""
const [src, scenario, stateJson] = process.argv.slice(1);
const fs = require('fs');
function mk0(tag) {
  const n = {nodeType: 1, tagName: tag.toUpperCase(), children: [], attrs: {}, style: {}, dataset: {}, listeners: {}, parentNode: null, hidden: false, disabled: false,
    className: '', value: '', _text: '',
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }, hasAttribute(k) { return k in this.attrs; },
    removeAttribute(k) { delete this.attrs[k]; }, addEventListener(t, f) { this.listeners[t] = f; },
    append(...xs) { for (const x of xs) { const c = typeof x === 'string' ? Object.assign(mk('#text'), {_text: x}) : x; if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); } },
    prepend(x) { if (x.parentNode) x.remove(); x.parentNode = this; this.children.unshift(x); },
    after(x) { if (x.parentNode) x.remove(); const p = this.parentNode; x.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, x); },
    remove() { const p = this.parentNode; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; } },
    replaceChildren(...xs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this.append(...xs); },
    contains(x) { while (x) { if (x === this) return true; x = x.parentNode; } return false; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; },
    set textContent(v) { this._text = String(v); this.children = []; }, get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }};
  return n;
}
const mk = t => Object.assign(mk0(t), {focus() {}, select() {}, setSelectionRange() {}, scrollIntoView() { scrolled.push(this.attrs.id); }});
const scrolled = [];
const main = mk('main'), ttl = mk('h1'), toast = mk('div'), head = mk('head');
main.attrs.id = 'main'; main.dataset.tab = 'sns'; ttl.attrs.id = 'ttl'; ttl.textContent = 'SNS'; toast.attrs.id = 'toast';
const snt = mk('div'); snt.attrs.id = 'snt';
for (let i = 0; i < 4; i++) { const b = mk('button'); b.attrs['aria-pressed'] = i === 1 ? 'true' : 'false'; snt.append(b); }
const xsc = mk('article'); xsc.attrs.id = 'xsc'; const xtc = mk('article'); xtc.attrs.id = 'xtc';
main.append(snt, xsc, xtc, mk('div'));
const byId = (n, id) => (n.attrs && n.attrs.id === id ? n : n.children.map(c => byId(c, id)).find(Boolean));
global.document = {readyState: 'complete', head, visibilityState: 'visible', activeElement: null, createElement: mk, addEventListener() {},
  getElementById: id => (id === 'boot' ? {textContent: JSON.stringify({me: {csrf: 'tok'}})} : id === 'ttl' ? ttl : id === 'toast' ? toast : byId(main, id) || byId(head, id) || null)};
const store = {};
global.location = {hash: process.argv[4] || '#/sns/x'};
global.window = global; global.addEventListener = () => {}; global.scrollTo = () => {};
global.MutationObserver = class { observe() {} };
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
global.setTimeout = () => 0; global.clearTimeout = () => {};
const state = JSON.parse(stateJson), posts = [], today = [];
global.SBXToday = {load() { today.push('load'); }};
global.fetch = async (u, o) => {
  const path = u.replace('/dashboard/api/xtrend/', '');
  if (!o || o.method !== 'POST') return {status: 200, json: async () => state};
  const body = JSON.parse(o.body);
  posts.push([path, o.headers['X-Dash-Token'], body]);
  if (path === 'refresh') { state.stale = false; state.busy = true; }
  if (path === 'draft') { state.mine = [{id: 'm123456789', at: '14:01', topic: body.topic, long: false, drafts: [{text: '초안 글 하나', angle: '관점 하나', warn: ['재료에 없는 숫자: 60'], done: false, undo_s: 0}]}]; state.left -= 1; }
  if (path === 'posted') { const d = state.mine[0].drafts[body.i]; d.done = body.op === 'add'; d.undo_s = d.done ? 600 : 0; }
  return {status: 200, json: async () => Object.assign({started: true}, state)};
};
const clip = [];
Object.defineProperty(global, 'navigator', {value: {clipboard: {writeText: async t => { clip.push(t); }}}, configurable: true, writable: true});
document.body = mk('body'); document.execCommand = () => false;
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };
const text = n => n.textContent;
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach(c => all(c, f, out)); return out; };
const find = (n, f) => all(n, f)[0];
const byText = (root, tag, t) => find(root, n => n.tagName === tag && text(n) === t);
const click = n => n.listeners.click({currentTarget: n, target: n, preventDefault() {}, stopPropagation() {}});
(async () => {
  const out = {};
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  if (scenario === 'card') {
    out.order = main.children.map(c => c.attrs.id || c.tagName);
    const card = byId(main, 'xrc');
    out.rows = all(card, n => n.className === 'r').map(text); out.card = text(card);
    out.auto = posts.map(p => p[0]);  // 15분 넘은 순위 → 새로 보기 한 번
    window.SBXTrend.load(); await tick(); out.again = posts.length;
    out.links = all(card, n => n.tagName === 'A').map(a => a.attrs.href);
    click(all(byId(main, 'xrc'), n => n.tagName === 'BUTTON' && text(n) === '글 쓰기')[1]);
    out.hash = location.hash; out.form = [window.SBXTrend.form().topic, window.SBXTrend.form().trend]; out.posted = posts.length;
  }
  if (scenario === 'screen') {
    const scr = () => main.children[0];
    out.scr = scr().attrs.id; out.hidden = 'data-xr' in main.attrs; out.title = ttl.textContent;
    const kws = () => all(scr(), n => n.className === 'kw').map(text);
    out.rank = kws();
    click(byText(scr(), 'BUTTON', '댓글 많은 순')); out.replies = kws();
    click(byText(scr(), 'BUTTON', '조회순')); out.views = kws();
    click(byText(scr(), 'BUTTON', '급상승')); out.move = kws();
    click(byText(scr(), 'BUTTON', '인기순'));
    const c0 = all(scr(), n => n.tagName === 'SECTION' && n.className === 'itm')[0];
    out.first = text(c0); out.links = all(c0, n => n.tagName === 'A').map(a => [a.attrs.href, a.attrs.target, a.attrs.rel]);
    out.hid0 = text(scr()).includes('국회 탄핵 표결');
    click(find(scr(), n => n.tagName === 'BUTTON' && text(n).startsWith('숨긴 주제')));
    out.hid1 = all(scr(), n => n.className === 'hd').map(text);
    out.before = posts.length;  // 열기만 해서는 아무것도 보내지 않음 (순위가 새것일 때)
    click(byText(all(scr(), n => n.tagName === 'SECTION' && n.className === 'itm')[1], 'BUTTON', '이걸로 글 쓰기'));
    const f = () => byId(main, 'xrf'), topic = () => find(f(), n => n.attrs['aria-label'] === '주제'), memo = () => find(f(), n => n.attrs['aria-label'] === '하고 싶은 말');
    out.picked = [topic().value, scrolled.includes('xrf'), posts.length - out.before];
    memo().value = '내 생각은 이럼'; memo().listeners.input({target: memo()});
    click(byText(f(), 'BUTTON', '존댓말')); click(byText(f(), 'BUTTON', '짧게'));
    const tg = find(f(), n => n.attrs.type === 'checkbox'); tg.checked = true; tg.listeners.change({target: tg});
    await click(byText(f(), 'BUTTON', '초안 만들기')); await tick();
    out.draft = posts[posts.length - 1]; out.toast = text(toast);
    const dc = () => find(scr(), n => n.className.startsWith('itm') && all(n, x => x.className === 'fl dr').length > 0), ta = () => find(dc(), n => n.tagName === 'TEXTAREA');
    out.dcard = text(dc()); out.dval = ta().value; out.warn = all(dc(), n => n.className === 'w').map(text);
    const t = ta(); t.value = '내가 고친 초안'; t.listeners.input({target: t});
    await click(byText(dc(), 'BUTTON', '복사')); await tick(); out.copied = clip[clip.length - 1];
    let ck = find(dc(), n => n.attrs.type === 'checkbox'); ck.checked = true; await ck.listeners.change({currentTarget: ck}); await tick();
    out.add = posts[posts.length - 1]; out.after = [dc().className, text(toast), today.length];
    ck = find(dc(), n => n.attrs.type === 'checkbox'); ck.checked = false; await ck.listeners.change({currentTarget: ck}); await tick();
    out.undo = posts[posts.length - 1]; out.saved = JSON.parse(store.xr1).memo;
    topic().value = '내가 직접 쓴 주제'; topic().listeners.input({target: topic()});
    await click(byText(f(), 'BUTTON', '초안 만들기')); await tick(); out.own = posts[posts.length - 1][2];
    location.hash = '#/sns/threads'; snt.children[1].attrs['aria-pressed'] = 'false'; window.SBXTrend.mount();
    out.gone = ['data-xr' in main.attrs, main.children.map(c => c.attrs.id || c.tagName)];
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


async def view_json(*, stale: bool) -> tuple[dict[str, Any], str]:
    topic = {"수능 연기": (200, fx(post(11, "kim", "수능 연기 반대함", views=300000, replies=40), post(12, "lee", "수능 연기 찬성", views=100000, replies=20))),
             "용인반도체클러스터": (200, fx(post(21, "sk", "용인반도체클러스터 착공", views=5_000_000, replies=500)))}
    d, _g, _a, now, _t = desk(get=FakeGet(topic=topic, sample=(200, fx())))
    await d.refresh()
    now[0] += 3600
    d.get.topic["용인반도체클러스터"] = (200, fx(post(21, "sk", "용인반도체클러스터 착공", views=1000, replies=500)))  # type: ignore[attr-defined]
    await d.refresh()
    if stale:
        now[0] += xr.CACHE_S
    v = d.view()
    return v, json.dumps(v, ensure_ascii=False)


def run_shim(scenario: str, state: str, hash_: str) -> dict[str, Any]:
    res = subprocess.run([NODE, "-e", SHIM, str(STATIC / "xtrend.js"), scenario, state, hash_], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
async def test_js_card_shows_top_rows_under_the_today_card_and_refreshes_once_when_stale() -> None:
    v, state = await view_json(stale=True)
    out = run_shim("card", state, "#/sns/x")
    assert out["order"] == ["snt", "xsc", "xtc", "xrc", "DIV"]  # 오늘 X 글 카드 아래
    assert out["rows"] == ["1▲1수능 연기조회 40만글 쓰기", "2▼1용인반도체클러스터조회 1000글 쓰기"]
    assert "지금 뜨는 주제" in out["card"] and "내 주제로 글 쓰기" in out["card"] and "올리기는 X 에서 내가" in out["card"]
    assert out["auto"] == ["refresh"] and out["again"] == 1 and out["links"] == ["#/sns/xr"]  # 새로 보기는 한 번만 · 카드에 바깥 링크 없음
    assert out["hash"] == "#/sns/xr/w" and out["form"] == ["용인반도체클러스터", v["rows"][1]["id"]] and out["posted"] == 1  # 고르기만 — AI 안 부름


@needs_node
async def test_js_screen_sorts_hides_writes_copies_and_ticks_only_on_taps() -> None:
    v, state = await view_json(stale=False)
    out = run_shim("screen", state, "#/sns/xr")
    assert out["scr"] == "xrs" and out["hidden"] is True and out["title"] == "지금 뜨는 주제"
    assert out["rank"] == ["수능 연기", "용인반도체클러스터"] and out["views"] == out["rank"]
    assert out["replies"] == ["용인반도체클러스터", "수능 연기"] and out["move"] == ["수능 연기", "용인반도체클러스터"]
    assert "1위" in out["first"] and "X 반응 큰 글 2개 (24시간) · 조회 40만 · 답글 60" in out["first"] and "구글 검색 2만+" in out["first"]
    assert "@kim · 조회 30만 · 답글 40 — 수능 연기 반대함" in out["first"] and "가나일보 — " in out["first"]
    assert out["links"] == [["https://x.com/kim/status/1000000011", "_blank", "noopener"], ["https://x.com/lee/status/1000000012", "_blank", "noopener"],
                            ["https://news.example/a1", "_blank", "noopener"], [v["rows"][0]["x"], "_blank", "noopener"]]
    assert out["hid0"] is False and out["hid1"] == ["국회 탄핵 표결 · 정치 — 숨김"]
    assert out["before"] == 0 and out["picked"] == ["용인반도체클러스터", True, 0]
    path, tok, body = out["draft"]
    assert (path, tok) == ("draft", "tok") and re.fullmatch(r"r[a-z0-9]{6,39}", body.pop("rid"))
    assert body == {"topic": "용인반도체클러스터", "memo": "내 생각은 이럼", "tone": "polite", "length": "short", "tags": True, "n": 3, "trend": v["rows"][1]["id"]}
    assert "초안을 만들었어요" in out["toast"] and "관점 · 관점 하나" in out["dcard"] and out["dval"] == "초안 글 하나" and out["warn"] == ["재료에 없는 숫자: 60"]
    assert out["copied"] == "내가 고친 초안"
    assert out["add"] == ["posted", "tok", {"set": "m123456789", "i": 0, "text": "내가 고친 초안", "op": "add"}]
    assert out["after"] == ["itm dn1", "올렸어요 — 오늘 X 체크리스트에 넣었어요", 1]
    assert out["undo"][2]["op"] == "undo" and out["saved"] == "내 생각은 이럼"
    assert out["own"]["topic"] == "내가 직접 쓴 주제" and out["own"]["trend"] == ""  # 내가 고친 주제에는 순위 참고를 붙이지 않음
    assert out["gone"] == [False, ["snt", "xsc", "xtc", "DIV"]]
