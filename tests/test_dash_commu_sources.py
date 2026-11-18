"""커뮤 출처 늘림 (주인 11/18 — 디시 켜고 갈등·혐오 거름 · 허용된 곳만 더 · 네이트판은 규칙 안에서). 바깥 요청 없음.

1) 디시 실시간 베스트: 실제 화면에서 저장한 목록·글 HTML(tests/fixtures/dcinside_*.html · 닉네임은 바꿔 둠)로 줄·숫자·날짜·본문을 읽음 ·
   공지·설문 줄 안 읽음 · 바뀌는 주소 꼬리(&_dcbest=1&page=1) 뗌 · robots.txt(저장본)가 목록·글 주소를 허용
2) 디시는 strict: 성별·세대·지역 갈등 · 혐오 · 정치·말머리 · 성적 · 피해·아이 · 고발·논란 낱말이면 뺌 · AI 에 디시 규칙 + sure=true 일 때만
3) 네이트판: robots.txt(저장본)는 프로그램에게 모든 주소 금지 → 판 주소는 한 번도 열지 않음 ·
   뉴스 검색으로 판 화제를 다룬 언론사 기사만 카드로 (출처 = 기사) · 하루 검색 수 · 판·커뮤·블로그 주소는 뺌
4) 기본 설정: x_cap·x_aggro 출처에 디시 · 확인해서 안 넣은 곳(더쿠·에펨·오유·엠팍·웃대·82cook·네이트판)은 출처 표에 없음
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

from dealbot.config import XAggroConfig, XCapConfig, XTodayConfig
from dealbot.dashboard import xaggro as xa
from dealbot.dashboard import xcap as xc
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from test_dash_xcap import nosleep, png
from test_dash_xtoday import FakeDb, FakeNews, FakeRouter, at

FIX = Path(__file__).parent / "fixtures"
DC = xc.SOURCES["dcinside"]
DC_LIST = (FIX / "dcinside_list.html").read_text(encoding="utf-8")
DC_VIEW = (FIX / "dcinside_view_479630.html").read_text(encoding="utf-8")
NOW = at("10:00", "2026-11-18")
UA = "dealbot/1.0"
SURE = {"ok": True, "sure": True, "why": "", "line": "기자 일 하면서 슈퍼맨인 거 안 들키는 법", "hook": "슈퍼맨이 안 들키는 진짜 이유",
        "body": "안경 하나로 다 속는다는 설정\n자세랑 목소리까지 바꾼다는 게 포인트", "angle": "다들 한 번쯤 해 본 의문"}


class Get:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        if url == "https://gall.dcinside.com/robots.txt":
            return 200, (FIX / "dcinside_robots.txt").read_text(encoding="utf-8")
        if url == DC.list_url:
            return 200, DC_LIST
        if url.startswith("https://gall.dcinside.com/board/view/"):
            return 200, DC_VIEW
        return 404, ""


class Ask:
    def __init__(self, out: dict[str, Any]) -> None:
        self.out, self.calls = out, []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        return self.out


def make(tmp_path: Any, out: dict[str, Any], *, news: Any = None, sources: list[str] | None = None, **cfg: Any) -> tuple[xc.XCap, xa.XAggro, Get, Ask]:
    db, get, ask = FakeDb(), Get(), Ask(out)
    studio = xs.Studio(db, ask=ask, clock=lambda: NOW)
    today = xt.XToday(db, studio, cfg=lambda: XTodayConfig(goal_posts=False), router=FakeRouter, news=FakeNews(None), clock=lambda: NOW)

    async def shoot(url: str, src: xc.Source) -> bytes | None:
        return png(1000)

    srcs = ["dcinside"] if sources is None else sources
    cap = xc.XCap(db, studio, today, cfg=lambda: XCapConfig(sources=srcs), get=get, shoot=shoot, img_dir=tmp_path / "xcap", ua=UA,
                  clock=lambda: NOW, sleep=nosleep)
    conf = XAggroConfig(**{"sources": srcs, "min_score": 1, **cfg})
    ag = xa.XAggro(db, studio, today, cap, cfg=lambda: conf, img_dir=tmp_path / "xaggro", news=news, clock=lambda: NOW, sleep=nosleep)
    return cap, ag, get, ask


# ---------------------------------------------------------------- 1) 디시 화면 모양
def test_dcinside_list_and_view_are_read_from_saved_pages() -> None:
    rows = xc.list_rows(DC, DC_LIST)
    assert len(rows) == 24  # 26 줄 중 설문·공지 2 줄은 안 읽음
    assert not any("이용 안내" in r["title"] or "SNS로 소통" in r["title"] for r in rows)
    assert all(re.fullmatch(r"https://gall\.dcinside\.com/board/view/\?id=dcbest&no=\d+", r["url"]) for r in rows)  # 꼬리 뗌
    first = rows[0]
    assert first["url"].endswith("no=479702") and first["title"] == "[유갤] 자우림 김윤아 망한연애 썰"
    assert (first["comments"], first["rec"], first["views"]) == (15, 5, 875)
    when = xa.when_map(DC, DC_LIST, NOW)
    assert when[first["url"]] == at("09:00", "2026-11-18")  # title="2026-11-18 09:00:03" → 분까지 (꼬리 뗀 주소로)
    title, body = xc.view_text(DC, DC_VIEW)
    assert title == "신문 기자 클라크 켄트가 슈퍼맨임을 들키지 않는 방법.jpg"
    assert body.startswith("설정상 슈퍼맨은") and "닉네임" not in body and len(body) >= xc.BODY_MIN
    assert {"img", "video"} <= set(DC.hide) and DC.strict and DC.canon  # 본문 그림·영상은 찍지 않음 (AI 가 못 본 그림)


def test_saved_robots_allow_dcbest_and_forbid_every_nate_pann_path() -> None:
    dc = RobotFileParser()
    dc.parse((FIX / "dcinside_robots.txt").read_text(encoding="utf-8").splitlines())
    assert dc.can_fetch(UA, DC.list_url) and dc.can_fetch(UA, "https://gall.dcinside.com/board/view/?id=dcbest&no=479630")
    pann = RobotFileParser()
    pann.parse((FIX / "pann_robots.txt").read_text(encoding="utf-8").splitlines())
    for path in ("/", "/talk/ranking", "/talk/ranking/d", "/talk/123456789", "/rss", "/talk/rss", "/main"):
        assert not pann.can_fetch(UA, "https://pann.nate.com" + path), path  # 'User-agent: * Disallow: /'


# ---------------------------------------------------------------- 2) 디시는 더 세게
def test_strict_rules_drop_conflict_hate_politics_sex_victims_and_accusations() -> None:
    for t in ("[계갤] 여자랑 절대 야구장 안간다는 남자.jpg", "[주갤] 남친이 헤어지자네", "급식충들 요즘 왜 이럼", "전라도 사람 특징",
              "[싱갤] 싱글벙글 대한민국 역사상 가장 접전이었던 선거", "[국갤] 금융사 해킹 3.9일", "[이갤] 전한길 입장 발표",
              "비키니 몸매 레전드", "4살 딸 통학버스 방치", "[세갤] 20등 논란 당사자입니다", "삼전직원 글 (현재는 글삭튀)", "박지윤 최동석 2심 뒤집어"):
        assert xc.bad_why(t, strict=True), t
        assert xa.risk_why(t, strict=True), t
    for t in ("[싱갤] 신문 기자 클라크 켄트가 슈퍼맨임을 들키지 않는 방법.jpg", "[닌갤] 포켓몬 애니가 게임기획이랑 어긋나게 만든 장면", "맛도리 CCD 보고가~"):
        assert xc.bad_why(t, strict=True) == "", t
    assert xc.bad_why("[주갤] 남친이 헤어지자네") == ""  # 다른 출처(보배·뽐뿌)는 예전 규칙 그대로


async def test_dcinside_ai_gets_the_strict_prompt_and_needs_sure(tmp_path: Any) -> None:
    cap, _ag, get, ask = make(tmp_path, {**SURE, "sure": False})
    assert await cap.make_one() is None  # sure=false → 버림 (애매하면 뺀다)
    system, _user, schema = ask.calls[0]
    assert xc.STRICT_NOTE in system and "sure" in schema["required"]
    assert get.calls[0] == "https://gall.dcinside.com/robots.txt" and not any("nate.com" in u for u in get.calls)
    cap, _ag, _g, ask = make(tmp_path, {k: v for k, v in SURE.items() if k != "sure"})
    assert await cap.make_one() is None  # sure 가 없어도 버림
    cap, _ag, _g, ask = make(tmp_path, SURE)
    item = await cap.make_one()
    assert item is not None and item["site"] == "디시인사이드" and re.search(r"no=\d+$", item["url"])
    titles = [u for _s, u, _sc in ask.calls]
    assert not any(re.search(r"남친|여자|선거|논란", u.split("\n")[2]) for u in titles)  # 규칙에 걸린 제목은 AI 까지 가지 않음


async def test_xaggro_dcinside_uses_strict_prompt_too(tmp_path: Any) -> None:
    _cap, ag, _g, ask = make(tmp_path, {**SURE, "sure": False}, per_run=1)
    assert await ag.run() == [] and ask.calls and xc.STRICT_NOTE in ask.calls[0][0]
    _cap, ag, _g, ask = make(tmp_path, SURE, per_run=1)
    made = await ag.run()
    assert len(made) == 1 and made[0]["site"] == "디시인사이드" and made[0]["text"].endswith("출처: 디시인사이드")
    assert "sure" in ask.calls[0][2]["required"]


# ---------------------------------------------------------------- 3) 네이트판 — 판은 열지 않고 기사만
def news_row(title: str, desc: str, link: str, hm: str = "08:30") -> dict[str, Any]:
    return {"title": title, "description": desc, "originallink": link, "link": link, "pubDate": f"Wed, 18 Nov 2026 {hm}:00 +0900"}


PANN_ROWS = [
    news_row("“택배 기사님께 쓴 쪽지 하나” 네이트판 화제", "온라인 커뮤니티 네이트판에 올라온 글이 누리꾼 사이에서 화제다. 글쓴이는 매일 오는 택배 기사에게 감사 쪽지를 붙였더니",
             "https://www.example-news.co.kr/article/1"),
    news_row("네이트판 글", "판 원문", "https://pann.nate.com/talk/123456789"),  # 판 주소 — 안 실음
    news_row("네이트판 화제 블로그 정리", "네이트판 화제 글 모음", "https://blog.naver.com/someone/1"),  # 블로그 — 기사 아님
    news_row("오늘 날씨 맑음", "전국 맑음", "https://www.example-news.co.kr/article/2"),  # 판 이야기 아님
    news_row("네이트판 '시댁 갑질 폭로' 글에 공분", "네이트판에 올라온 시어머니 갑질 폭로 글", "https://www.example-news.co.kr/article/3"),  # 고발 — 규칙
    news_row("“택배 기사님께 쓴 쪽지 하나” 네이트판 화제", "같은 이야기 다른 매체", "https://other-news.com/a/9", "08:20"),  # 같은 이야기
    news_row("네이트판 오래된 화제", "네이트판", "https://www.example-news.co.kr/article/4", "00:00"),
]
PANN_DRAFT = {"ok": True, "why": "", "hook": "택배 기사님한테 쪽지 붙였다가 생긴 일", "body": "매일 오시는 분께 고맙다고 남긴 쪽지\n답장이 와서 다들 훈훈해함",
              "angle": "작은 친절 이야기"}


async def test_pann_cards_come_from_news_articles_and_never_open_pann(tmp_path: Any) -> None:
    news = FakeNews(PANN_ROWS)
    _cap, ag, get, ask = make(tmp_path, PANN_DRAFT, news=news, sources=[], max_age_h=6)
    made = await ag.run()
    assert len(made) == 1
    it = made[0]
    assert it["url"] == "https://www.example-news.co.kr/article/1" and it["news"] is True and it["src"] == "pann_news"
    assert it["site"] == f"example-news.co.kr ({xa.PANN_SITE})" and it["text"].endswith(f"출처: example-news.co.kr ({xa.PANN_SITE})")
    assert "네이트판 화제 기사" in it["why"] and it["n"] == 1 and ag.img(f"{it['id']}-1.jpg")[:2] == b"\xff\xd8"
    assert get.calls == [] and news.asked == XAggroConfig().pann_queries  # 판·기사 페이지는 열지 않음 (검색 API 만)
    system, user, _schema = ask.calls[0]
    assert xa.PANN_NOTE in system and "택배" in user and len(ask.calls) == 1
    v = ag.view()
    assert v["items"][0]["news"] is True and xa.PANN_SITE in v["sources"]
    again = await ag.run()  # 같은 기사도, 다른 매체의 같은 이야기도 다시 안 씀
    assert again == [] and len(news.asked) == 6


async def test_pann_search_respects_the_daily_cap_and_can_be_turned_off(tmp_path: Any) -> None:
    news = FakeNews(PANN_ROWS)
    _cap, ag, _g, _a = make(tmp_path, PANN_DRAFT, news=news, sources=[], pann_per_day=2, max_age_h=6)
    await ag.run()
    await ag.run()
    assert len(news.asked) == 2
    news = FakeNews(PANN_ROWS)
    _cap, ag, _g, ask = make(tmp_path, PANN_DRAFT, news=news, sources=[], pann_news=False)
    assert await ag.run() == [] and news.asked == [] and ask.calls == [] and xa.PANN_SITE not in ag.view()["sources"]
    dead = FakeNews(None, dead=True)
    _cap, ag, _g, ask = make(tmp_path, PANN_DRAFT, news=dead, sources=[])
    assert await ag.run() == [] and len(dead.asked) == 1 and ask.calls == []  # 키 없음 — 그 차례는 그만


def test_pann_article_keeps_only_outlet_articles_about_pann() -> None:
    got = [xa.pann_article(x, NOW, 6) for x in PANN_ROWS]
    assert [g["link"] if g else None for g in got] == ["https://www.example-news.co.kr/article/1", None, None, None, None, "https://other-news.com/a/9", None]


# ---------------------------------------------------------------- 4) 기본 설정 · 안 넣은 곳
def test_defaults_turn_dcinside_on_and_unchecked_sites_are_absent() -> None:
    assert "dcinside" in XCapConfig().sources and "dcinside" in XAggroConfig().sources and XAggroConfig().pann_news
    assert set(xc.SOURCES) == {"bobaedream", "ppomppu", "dcinside"}
    assert all(not s.strict for k, s in xc.SOURCES.items() if k != "dcinside")
    root = Path(__file__).resolve().parent.parent
    cfg = (root / "config.yaml").read_text(encoding="utf-8")
    assert cfg.count("sources: [bobaedream, ppomppu, dcinside]") == 2
    js = (root / "src/dealbot/dashboard/static/xaggro.js").read_text(encoding="utf-8")
    assert "#/sns/xr/w" in js and "사진으로 글 쓰기" in js  # 폰에서 본 판 글 = 사진으로 글 쓰기


async def test_pann_takes_one_slot_and_community_fills_the_rest(tmp_path: Any) -> None:
    class Both(Ask):
        async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            self.calls.append((system, user, schema))
            return PANN_DRAFT if xa.PANN_NOTE in system else SURE

    news = FakeNews(PANN_ROWS)
    _cap, ag, get, _a = make(tmp_path, {}, news=news, max_age_h=6)
    ag.studio._ask_fn = Both({})
    made = await ag.run()
    assert [m["src"] for m in made] == ["pann_news", "dcinside"]
    assert not any("nate.com" in u for u in get.calls)
