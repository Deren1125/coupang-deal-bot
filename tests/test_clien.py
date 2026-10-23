"""클리앙 알뜰구매 수집기: 저장해 둔 목록·본문 HTML 로 파싱, robots(쿼리 없음), 품절 건너뛰기, 토스 단서, 0건 DOM 요약."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dealbot.collectors import CollectorContext, build_collector
from dealbot.collectors.clien import (
    DEFAULT_SELECTORS,
    _count,
    is_allowed_url,
    parse_detail_links,
    parse_list,
    post_path,
)
from dealbot.config import CollectorConfig, Settings
from dealbot.storage.db import Database

FIX = Path(__file__).parent / "fixtures" / "clien"
HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}


def _list_html() -> str:
    return (FIX / "jirum_list.html").read_text(encoding="utf-8")


def _row(post_id: int, title: str, likes: int = 5, *, cls: str = "", label: str = "", href_query: str = "?od=T31&po=0&category=0&groupCd=") -> str:
    return f"""<div class="list_item symph_row jirum {cls}" data-comment-count="4" data-role="list-row">
 <div class="list_symph view_symph"><em class="none">{likes}</em></div>
 <div class="list_title"><span class="list_subject" title="{title}">
  <a data-role="list-title-text" href="/service/board/jirum/{post_id}{href_query}">{title}</a>
  <a class="list_reply reply_symph" href="/service/board/jirum/{post_id}{href_query}#comment-point"><span class="rSymph05">4</span></a></span>
  <div class="keyword">{label}</div></div>
 <div class="list_hit"><span class="hit">1.2 k</span></div>
 <div class="list_time"><span class="time popover">10-23<span class="timestamp">2026-10-23 10:00:00</span></span></div>
</div>"""


def _page(*rows: str) -> str:
    return "<html><body><div class='board_main'>" + "".join(rows) + "</div></body></html>"


def test_parse_list_rows_from_saved_sample() -> None:
    items = parse_list(_list_html(), DEFAULT_SELECTORS)
    by_id = {i["external_id"]: i for i in items}
    assert len(items) == 10 and len(by_id) == 10  # 공지·홍보 줄은 빠지고, 같은 글은 한 번만
    w = by_id["19281702"]
    assert w["title"] == "[W컨셉] 로지텍코리아 KEYS-TO-GO 2 (71,732원)"
    assert (w["shop_tag"], w["price"]) == ("W컨셉", 71732)
    assert (w["recommend"], w["comments"], w["views"]) == (0, 2, 647)
    assert w["time"] == "2026-10-23 16:12:27" and not w["sold_out"]
    g = by_id["19281063"]
    assert (g["recommend"], g["comments"], g["views"]) == (6, 3, 5763)
    assert by_id["19281018"]["views"] == 12100  # '12.1 k'
    assert by_id["19276810"]["recommend"] is None  # 광고 줄에는 공감 칸이 비어 있음
    boss = by_id["19280103"]  # '끌올 [네이버] …' 머리말 앞의 끌올은 무시하고 읽는다
    assert (boss["shop_tag"], boss["price"], boss["comments"]) == ("네이버", 104000, 41)


def test_post_urls_have_no_query_string() -> None:
    raw = _list_html()
    assert "?od=" in raw  # 저장한 목록 링크에는 쿼리가 붙어 있다
    for i in parse_list(raw, DEFAULT_SELECTORS):
        assert "?" not in i["post_url"] and "#" not in i["post_url"]
        assert i["post_url"] == f"https://www.clien.net/service/board/jirum/{i['external_id']}"
    assert post_path("/service/board/jirum/19281063?od=T31&po=0&category=0&groupCd=#comment-point") == (
        "19281063", "https://www.clien.net/service/board/jirum/19281063")
    assert post_path("/service/board/rule/10963221") is None and post_path("/service/board/jirum?category=1") is None
    assert is_allowed_url("https://www.clien.net/service/board/jirum")
    assert is_allowed_url("https://www.clien.net/service/board/jirum/19281063")
    assert not is_allowed_url("https://www.clien.net/service/board/jirum?od=T31")
    assert not is_allowed_url("https://www.clien.net/service/board/jirum/19281063?od=T31")
    assert not is_allowed_url("https://www.clien.net/service/board/sold/1")
    assert not is_allowed_url("https://www.clien.net/service/search?q=a")


def test_sold_out_detected_by_class_label_and_title() -> None:
    page = _page(
        _row(1, "[쿠팡] 정상 상품 (10,000원)"),
        _row(2, "[쿠팡] 클래스로 표시 (10,000원)", cls="sold_out"),
        _row(3, "[쿠팡] 라벨로 표시 (10,000원)", label='<span class="icon_info">품절</span>'),
        _row(4, "[쿠팡] 라벨 종료 (10,000원)", label='<span class="icon_info">종료</span>'),
        _row(5, "[쿠팡] (품절) 제목에 표시 (10,000원)"),
        _row(6, "[쿠팡] 종료 임박 이벤트 (10,000원)"),  # 아직 진행 중
    )
    flags = {i["external_id"]: i["sold_out"] for i in parse_list(page, DEFAULT_SELECTORS)}
    assert flags == {"1": False, "2": True, "3": True, "4": True, "5": True, "6": False}
    sample = parse_list(_list_html(), DEFAULT_SELECTORS)
    assert sum(i["sold_out"] for i in sample) == 3


def test_count_parser() -> None:
    assert [_count(x) for x in ("6", "1,234", "13.1 k", "4.6 M", "", None, "댓글")] == [6, 1234, 13100, 4600000, None, None, None]


def test_detail_links_skip_clien_and_pick_shop_link() -> None:
    urls = parse_detail_links((FIX / "jirum_post_19276995.html").read_text(encoding="utf-8"), None)
    assert urls == ["https://www.costco.co.kr/ElectronicsComputers/Computer-Accessories/KeyboardsComputer-Mice/Logitech-Wireless-Mouse-MX-MASTER-3S-Graphite/p/694590"]


def _ctx(settings: Settings, db: Database, handler) -> CollectorContext:  # noqa: ANN001
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())


def _site(list_html: str, calls: list[httpx.Request]):  # noqa: ANN202
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/service/board/jirum":
            return httpx.Response(200, content=list_html.encode(), headers=HTML_HEADERS)
        post = FIX / "jirum_post_19276995.html"
        if req.url.path.startswith("/service/board/jirum/"):
            return httpx.Response(200, content=post.read_bytes(), headers=HTML_HEADERS)
        return httpx.Response(404)

    return handler


async def test_collect_only_bare_urls_and_skips_sold_out(settings: Settings, db: Database) -> None:
    calls: list[httpx.Request] = []
    ctx = _ctx(settings, db, _site(_page(
        _row(100, "[쿠팡] 로켓 텀블러 (12,900원/무료)", 7),
        _row(101, "[쿠팡] 품절된 텀블러 (9,900원)", 30, cls="sold_out"),
        _row(102, "[쿠팡] 종료 표시 텀블러 (9,900원)", 30, label='<span class="icon_info">종료</span>'),
        _row(103, "[듣보잡몰] 모르는 몰 (5,000원)", 9),
    ), calls))
    cfg = CollectorConfig(name="clien_jirum", type="clien", options={"request_delay_seconds": 0})
    products = await build_collector(cfg, ctx).collect()
    assert [p.external_id for p in products] == ["100"]
    p = products[0]
    assert p.source == "clien_jirum" and p.shop == "coupang" and p.price == 12900
    assert (p.recommend_count, p.comment_count, p.view_count) == (7, 4, 1200)
    assert p.extra["post_url"] == "https://www.clien.net/service/board/jirum/100"
    # 요청: 목록 맨 주소 1번 + 새 글 본문 1번(경로만). 쿼리·페이지 넘기기·fragment 없음, 품절·모르는 몰 글은 열지 않음
    assert [str(r.url) for r in calls] == [
        "https://www.clien.net/service/board/jirum", "https://www.clien.net/service/board/jirum/100"]
    assert all(not r.url.query and not r.url.fragment for r in calls)
    assert all("Sec-Fetch-Mode" not in r.headers and "Upgrade-Insecure-Requests" not in r.headers for r in calls)


async def test_collect_toss_hint_and_detail_budget(settings: Settings, db: Database) -> None:
    calls: list[httpx.Request] = []
    ctx = _ctx(settings, db, _site(_page(
        _row(200, "토스쇼핑 애슐리 크리스피 핫도그 (14,890원)", 8),   # 머리말이 없어도 토스 단서
        _row(201, "[모르는머리말] 토스 쉐어링크 라면 (9,900원)", 8),  # 모르는 머리말 + 토스 단서
        _row(202, "[쿠팡] 세 번째 글 (10,000원)", 8),
    ), calls))
    cfg = CollectorConfig(name="clien_jirum", type="clien", options={"request_delay_seconds": 0, "max_detail_fetch_per_run": 2})
    products = await build_collector(cfg, ctx).collect()
    assert [(p.external_id, p.shop) for p in products] == [("200", "toss"), ("201", "toss")]
    assert sum(1 for r in calls if r.url.path.startswith("/service/board/jirum/")) == 2  # 본문은 최대 2개
    # 두 번째 실행: 본 글은 다시 열지 않음 (추천 1 이상이라 저장된 링크로 다시 올라옴), 남은 글 1개만 새로 엶
    calls.clear()
    again = await build_collector(cfg, ctx).collect()
    assert {p.external_id for p in again} == {"200", "201", "202"}
    assert [r.url.path for r in calls] == ["/service/board/jirum", "/service/board/jirum/202"]


async def test_collect_no_rows_logs_dom_summary(settings: Settings, db: Database) -> None:
    html = "<html><body><div class='board_new'><div class='row_new'>a</div><div class='row_new'>b</div></div></body></html>"
    ctx = _ctx(settings, db, _site(html, []))
    cfg = CollectorConfig(name="clien_jirum", type="clien", options={"request_delay_seconds": 0})
    with pytest.raises(RuntimeError, match="셀렉터 조정 필요"):
        await build_collector(cfg, ctx).collect()
    err = db.last_error()
    assert err and err["kind"] == "collector:clien_jirum" and "no rows matched" in err["message"] and "div.row_new×2" in err["message"]


async def test_collect_selectors_configurable(settings: Settings, db: Database) -> None:
    html = '<ul><li class="r"><a class="t" href="/service/board/jirum/300?od=T31">[쿠팡] 셀렉터 상품 (10,000원)</a><b class="v">9</b></li></ul>'
    ctx = _ctx(settings, db, _site(html, []))
    sel = {"row": "li.r", "title": "a.t", "likes": "b.v"}
    cfg = CollectorConfig(name="clien_jirum", type="clien", options={"request_delay_seconds": 0, "selectors": sel})
    products = await build_collector(cfg, ctx).collect()
    assert [(p.external_id, p.recommend_count) for p in products] == [("300", 9)]


async def test_refuses_urls_robots_disallows(settings: Settings, db: Database) -> None:
    calls: list[httpx.Request] = []
    ctx = _ctx(settings, db, _site("", calls))
    collector = build_collector(CollectorConfig(name="c", type="clien", options={}), ctx)
    for bad in ("https://www.clien.net/service/board/jirum?od=T31", "https://www.clien.net/service/board/jirum/1?od=T31",
                "https://www.clien.net/service/search?q=x"):
        with pytest.raises(ValueError):
            await collector._get(bad)  # noqa: SLF001
    assert calls == []


def test_config_registers_clien_collector(settings: Settings) -> None:
    cfg = next(c for c in settings.collectors if c.name == "clien_jirum")
    assert cfg.type == "clien" and not cfg.enabled and cfg.interval_minutes == 15 and cfg.label == "클리앙 알뜰구매"
    # 꺼 둠: 클리앙이 서버 IP 를 막음 (주인 10/23) — 풀리면 enabled 와 함께 이 줄도 되돌린다
    assert cfg.options["shops"] == [] and cfg.options["request_delay_seconds"] == 3 and cfg.options["max_detail_fetch_per_run"] == 8
    rule = settings.deal.per_source["clien_jirum"]
    assert rule.community_min_recommend is None and rule.coupon_min_recommend is None and rule.discount_alone is None  # 전체 판정 기준 그대로
