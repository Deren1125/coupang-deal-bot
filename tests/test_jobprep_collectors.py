"""취준 공고 출처 — 출처별 픽스처 JSON → 같은 꼴 · 규칙 거르기 · 겹침 합치기 · 크기 상한 · 실패 3번 쉼 · robots · 금지 주소가 코드에 없음."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dealbot.dashboard.jobprep import collectors as co
from dealbot.dashboard.jobprep import jobs
from jobprep_helpers import make_jp

GH = {"jobs": [{"id": 11, "title": "Software Engineering Intern (Summer 2027)", "location": {"name": "Seoul, South Korea"},
                "absolute_url": "https://boards.greenhouse.io/x/jobs/11", "content": "&lt;p&gt;Join us as an &lt;b&gt;intern&lt;/b&gt;&lt;/p&gt;"},
               {"id": 12, "title": "Senior Software Engineer", "location": {"name": "Seoul"}, "absolute_url": "https://b/12", "content": "x"}]}
LEVER = [{"id": "ab-1", "text": "Trading Intern", "categories": {"location": "Singapore", "commitment": "Intern"}, "hostedUrl": "https://jobs.lever.co/w/ab-1",
          "descriptionPlain": "Summer internship", "lists": [{"text": "Req", "content": "<li>Python</li>"}]}]
ASHBY = {"jobs": [{"id": "u-1", "title": "Research Intern", "location": "San Francisco", "isRemote": True, "jobUrl": "https://jobs.ashbyhq.com/o/u-1",
                   "descriptionPlain": "Research internship 2027"}]}
WORKABLE = {"jobs": [{"shortcode": "W1", "title": "Marketing Intern", "city": "Hong Kong", "country": "Hong Kong", "url": "https://apply.workable.com/x/j/W1",
                      "description": "<p>intern</p>"}]}
SMART = {"content": [{"id": "s1", "name": "Finance Intern", "location": {"city": "New York", "country": "us"}}]}


def test_parsers_give_same_shape() -> None:
    rows = (co.parse_greenhouse(GH, "x") + co.parse_lever(LEVER, "w") + co.parse_ashby(ASHBY, "o") + co.parse_workable(WORKABLE, "k")
            + co.parse_smartrecruiters(SMART, "sr"))
    assert len(rows) == 6
    for r in rows:
        assert set(r) >= {"src", "token", "src_id", "title", "loc", "url", "text", "deadline"}
    assert rows[0]["text"] == "Join us as an intern" and rows[0]["src_id"] == "11"
    assert "Python" in rows[2]["text"] and "Remote" in rows[3]["loc"]
    assert rows[5]["url"] == "https://jobs.smartrecruiters.com/sr/s1"


@pytest.mark.parametrize("title,text,cat,loc,ok", [
    ("Software Engineering Intern", "", "ai_growth", "Seoul", True),
    ("Summer Analyst 2027", "", "ib", "Hong Kong", True),
    ("체험형 인턴 (데이터)", "", "kr_exchange", "서울", True),
    ("Senior Software Engineer", "internship program mention", "ai_growth", "Seoul", False),
    ("PhD Research Intern", "", "ai_growth", "Seoul", False),
    ("Research Intern", "PhD required", "ai_growth", "Remote", False),
    ("Analog Design Intern", "", "semi", "Seoul", False),
    ("Process Engineering Intern", "", "semi", "Seoul", True),
    ("Software Engineer", "", "ai_growth", "Seoul", False),
    ("Data Intern", "", "ai_growth", "Berlin, Germany", False),
    ("Data Intern", "", "ai_growth", "", True),
    ("Internal Tools Engineer", "", "ai_growth", "Seoul", False),
])
def test_rule_filter(title: str, text: str, cat: str, loc: str, ok: bool) -> None:
    assert jobs.rule_pass(title, text, cat, loc)[0] is ok


def test_regions() -> None:
    assert jobs.region_of("Seoul, South Korea") == "KR" and jobs.region_of("Remote - US") == "REMOTE"
    assert jobs.region_of("New York, NY") == "US" and jobs.region_of("Singapore") == "SG" and jobs.region_of("London") == "OTHER"


def test_merge_across_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    cid = jp.store.x("INSERT INTO companies (name, norm, cat, region, ats, token, career_url, growth_note, on_) VALUES ('X','x','ai_growth','KR','greenhouse','x','','',1)")
    comp = {"id": cid, "name": "X", "cat": "ai_growth"}
    a = [{"src_id": "1", "title": "Software Intern (2027)", "loc": "Seoul", "url": "https://a/1", "text": "", "deadline": None}]
    b = [{"src_id": "h1", "title": "Software Intern", "loc": "Seoul", "url": "https://b/h1", "text": "", "deadline": None}]
    jobs.apply_board(jp.store, comp, "greenhouse", "x", a, ok=True, now=1.0)
    st = jobs.apply_board(jp.store, comp, "html", "x", b, ok=True, now=2.0)
    assert st["merged"] == 1
    rows = jp.store.q("SELECT * FROM jobs")
    assert len(rows) == 1 and json.loads(rows[0]["alt_urls"]) == ["https://b/h1"] and rows[0]["src"] == "greenhouse"


async def test_fetcher_cap_fail_off_and_etag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    url = co.URLS["greenhouse"].format(token="big")
    jp.fake_http.routes[url] = (200, b"x" * 5000)
    r = await jp.fetcher.get(url, max_bytes=1000)
    assert r.status == -1  # 상한 넘음
    for _ in range(3):
        ok, _rows, why, _fresh = await co.read_board(jp.fetcher, "greenhouse", "missing")
        assert not ok
    assert jp.fetcher.is_off("greenhouse:missing")  # 3번 실패 → 24시간 쉼
    n = len(jp.fake_http.calls)
    ok, _r, why, _f = await co.read_board(jp.fetcher, "greenhouse", "missing")
    assert why == "쉼" and len(jp.fake_http.calls) == n  # 쉬는 동안 부르지 않음
    # 403 은 한 번에 쉼
    u2 = co.URLS["lever"].format(token="blk")
    jp.fake_http.routes[u2] = (403, b"")
    await co.read_board(jp.fetcher, "lever", "blk")
    assert jp.fetcher.is_off("lever:blk")
    # ETag: 캐시가 오래되면 If-None-Match 로 묻고 304 면 저장본
    u3 = co.URLS["greenhouse"].format(token="e")
    jp.fake_http.routes[u3] = (200, json.dumps(GH).encode(), {"etag": '"v1"'})
    ok, rows, _, fresh = await co.read_board(jp.fetcher, "greenhouse", "e", lambda p: (p["title"].startswith("Software"), "x"))
    assert ok and len(rows) == 2 and fresh and [r["_pass"] for r in rows] == [True, False]  # 거르기는 파싱과 같은 스레드에서
    ok, rows, _, fresh = await co.read_board(jp.fetcher, "greenhouse", "e")
    assert ok and not fresh  # TTL 캐시 저장본 → miss 를 세지 않음
    jp.fake_clock.t += co.CACHE_S + 1
    seen = {}

    def answer(u: str, h: dict[str, str]):  # type: ignore[no-untyped-def]
        seen.update(h)
        return (304, b"")

    jp.fake_http.routes[u3] = answer
    ok, rows, _, fresh = await co.read_board(jp.fetcher, "greenhouse", "e")
    assert ok and len(rows) == 2 and seen.get("If-None-Match") == '"v1"' and fresh  # 304 는 새로 읽은 것
    assert all(c[2].get("User-Agent") == "dealbot-test/1" and "Cookie" not in c[2] for c in jp.fake_http.calls)


async def test_robots_disallow_blocks_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    jp = make_jp(tmp_path, monkeypatch)
    jp.fake_http.routes["https://ex.com/robots.txt"] = (200, b"User-agent: *\nDisallow: /careers\n")
    jp.fake_http.routes["https://ex.com/careers"] = (200, b"<html>jobs</html>")
    status, html = await co.read_page(jp.fetcher, "https://ex.com/careers", "watch:ex.com")
    assert status == -3 and html == ""
    assert not any(c[1] == "https://ex.com/careers" for c in jp.fake_http.calls)


def test_no_login_sites_in_code() -> None:
    root = Path(co.__file__).resolve().parent
    text = "".join(p.read_text(encoding="utf-8") for p in root.glob("*.py")).lower()
    text += "".join(p.read_text(encoding="utf-8") for p in (root.parent / "static").glob("jp*.js")).lower()
    import re

    for bad in (r"linkedin", r"teamblind", r"\bblind\.com", r"//(www\.)?x\.com", r"twitter\.com", r"jasoseol"):
        assert not re.search(bad, text), bad


def test_growth_parsers() -> None:
    dl = {"protocols": [{"name": "A", "total30d": 5e6, "category": "Dexs"}, {"name": "B", "total30d": 9e6}, {"name": "C", "total30d": None}]}
    assert [r["name"] for r in co.parse_defillama(dl)] == ["B", "A"]
    yc = [{"name": "K", "isHiring": True, "batch": "Winter 2024", "regions": ["South Korea"], "website": "https://k"},
          {"name": "Old", "isHiring": True, "batch": "W12", "regions": ["Remote"]}, {"name": "N", "isHiring": False, "batch": "S24", "regions": ["Remote"]}]
    assert [r["name"] for r in co.parse_yc(yc)] == ["K"]
    assert co.parse_coingecko([{"name": "Ex", "trade_volume_24h_btc": 10}])[0]["name"] == "Ex"
