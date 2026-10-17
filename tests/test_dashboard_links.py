"""대시보드 '발행 대기 글'(Blog-Auto 기록 파일의 waiting)과 '링크 기다리는 딜'(+ POST /dashboard/link 링크 넣기) — 주인 요청 10/15.

네트워크 없음: Blog-Auto 기록 파일은 tmp 에 쓰고, 텔레그램은 연결하지 않은 봇(연습 모드)으로.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

from dealbot.app import DealBot
from dealbot.config import DashboardCardConfig, DashboardConfig, Settings
from dealbot.dashboard.blogauto import BlogAutoReader, parse_export
from dealbot.dashboard.service import LINK_BODY_MAX, LINK_RATE, SECURITY_HEADERS, Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.models import Deal, DealVerdict, Product
from dealbot.shops import find_urls
from dealbot.storage.db import LINK_WAITING, Database, QueueItem
from dealbot.web import WebServer

NOW = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)  # 한국시간 18:05
USER, PASSWORD = "owner", "plum-river-cactus-42"
FORM = {"content-type": "application/x-www-form-urlencoded"}
BLOG_CARDS = [
    {"key": "blog_finance", "type": "naver_blog", "title": "Deren의 아카이브", "options": {"blog": "finance"}},
    {"key": "blog_issue", "type": "naver_blog", "title": "Deren's Pick", "options": {"blog": "issue"}},
    {"key": "blog_hotdeal", "type": "naver_blog", "title": "오늘의 핫딜 블로그", "options": {"blog": "hotdeal"}},
]


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


AUTH = {"authorization": basic(USER, PASSWORD)}


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class Desk:
    """봇(app.DealBot) 대신: 거르기는 진짜 DealBot._link_problem, 붙이기는 DB 에 직접 (호출을 센다).
    붙일 글은 그 번호의 글 그대로 (자리를 내준 글을 여는 길은 진짜 DealBot 으로 보는 테스트에서)."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.calls: list[tuple[int, str]] = []
        self.refreshed = 0

    def owner_link_target(self, queue_id: int) -> QueueItem | None:
        return self.db.get_queue_item(queue_id)

    def owner_link_problem(self, queue_id: int, url: str) -> str | None:
        return DealBot._link_problem(self.db.get_queue_item(queue_id), queue_id, url, link_ttl=timedelta(hours=24))

    async def attach_link(self, queue_id: int, url: str) -> str:
        self.calls.append((queue_id, url))
        self.db.set_queue_link(queue_id, url)
        return "ok"

    async def refresh_pending_notice(self) -> None:
        self.refreshed += 1


def make_dash(db: Database, tmp_path: Path, cards: list[dict[str, Any]] | None = None, *, clock: Clock | None = None,
              links: Any = None, **ctx: Any) -> Dashboard:
    clock = clock or Clock()
    card_list = cards if cards is not None else [{"key": "links", "type": "dealbot_links", "title": "링크 기다리는 딜"},
                                                 {"key": "blog_waiting", "type": "blog_waiting", "title": "발행 대기 글"},
                                                 *BLOG_CARDS]
    cfg = DashboardConfig(cards=[DashboardCardConfig(**c) for c in card_list], blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    sctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=clock,
                         blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=clock), **ctx)
    return Dashboard(cfg, sctx, user=USER, password=PASSWORD, links=links)


def card_html(page: str, key: str) -> str:
    return page.split(f'id="card-{key}"', 1)[1].split("</article>", 1)[0]


def add_item(db: Database, n: int, *, status: str = "awaiting_link", name: str | None = None, url: str | None = None,
             score: float = 12.5, at: datetime = NOW, price: int = 19900) -> int:
    pid = f"naver:{n}"
    p = Product(source="ppomppu", product_id=pid, shop="naver", name=name or f"상품 {n}", price=price,
                url=url or f"https://smartstore.naver.com/s/products/{n}")
    assert db.enqueue(Deal(p, DealVerdict(is_deal=True, score=score)), score=score, now=at)
    qid = db.items_for_product(pid, ("pending",))[0].id
    if status != "pending":
        db.update_queue_item(qid, status=status, error="manual link required" if status == "awaiting_link" else None, now=at)
    return qid


def write_export(tmp_path: Path, waiting: Any = None, *, written_at: str = "2026-10-14T18:00:00+09:00", drop: bool = False) -> None:
    data: dict[str, Any] = {"version": 1, "written_at": written_at,
                            "blogs": {"finance": {"posts_published_today": 1, "posts_published_total": 23, "drafts_saved_today": 2}}}
    if not drop:
        data["waiting"] = waiting
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


WAITING = {"threshold": 70, "blogs": {
    "finance": {"count": 4, "popular": 1, "top": [
        {"title": "ISA 만기 후 연금계좌로 옮기는 법", "score": 64, "saved_at": "2026-10-14T09:12:00+09:00", "category": "MONEY"},
        {"title": "연말정산 미리보기", "score": 82, "saved_at": "2026-10-13T09:12:00+09:00", "category": "MONEY"},
        {"title": "같은 점수 중 더 새 글", "score": 41, "saved_at": "2026-10-14T10:00:00+09:00", "category": None},
        {"title": "같은 점수 중 옛 글", "score": 41, "saved_at": "2026-10-12T10:00:00+09:00", "category": "TECH"},
    ]},
    "issue": {"count": 0, "popular": 0, "top": []},
}}


# ---------------------------------------------------------------- Blog-Auto 기록 파일의 waiting
def test_export_without_waiting_still_reads_and_with_it_keeps_the_old_keys() -> None:
    old = {"version": 1, "written_at": "2026-10-14T18:00:00+09:00", "blogs": {"finance": {"posts_published_today": 1}}}
    a = parse_export(old, "Asia/Seoul")
    assert a.ok and a.blogs["finance"] == {"posts_today": 1.0} and a.waiting is None  # 지금 배포된 Blog-Auto 파일 그대로
    b = parse_export({**old, "waiting": WAITING}, "Asia/Seoul")
    assert b.ok and b.blogs == a.blogs and b.written_at == a.written_at  # 더한 키는 기존 값에 영향 없음
    assert b.waiting is not None and b.waiting.threshold == 70 and set(b.waiting.blogs) == {"finance", "issue"}
    fin = b.waiting.blogs["finance"]
    assert (fin.count, fin.popular) == (4, 1)
    # 점수 높은 순, 같으면 최근 저장 순
    assert [p.title for p in fin.top] == ["연말정산 미리보기", "ISA 만기 후 연금계좌로 옮기는 법", "같은 점수 중 더 새 글", "같은 점수 중 옛 글"]
    assert fin.top[0].category == "MONEY" and fin.top[0].saved_at == datetime(2026, 10, 13, 0, 12, tzinfo=UTC)
    assert b.waiting.blogs["issue"].count == 0 and b.waiting.blogs["issue"].top == []


def test_waiting_is_parsed_defensively_blog_by_blog() -> None:
    long = "가" * 80
    w = parse_export({"written_at": "2026-10-14T18:00:00+09:00", "blogs": {}, "waiting": {"threshold": "70", "blogs": {
        "Finance": {"count": 2, "popular": 9, "top": [
            {"title": f"  {long}\n", "score": 88, "saved_at": "nope", "category": 3},
            {"title": "", "score": 50}, "x", {"score": 99},
            {"title": "둘째", "score": 75.0, "saved_at": None, "category": "LIFE"},
            {"title": "셋째는 count 를 넘어서 빠짐", "score": 10}]},
        "pick": {"count": 3, "top": [{"title": "점수가 이상함", "score": 101}, {"title": "점수 없음"}]},
        "issue": {"count": -1, "popular": 0},
        "hotdeal": {"count": True},
        "nothing": "x",
    }}}, "Asia/Seoul").waiting
    assert w is not None and w.threshold is None  # 숫자 글자는 받지 않음 (카드는 기본 70)
    assert set(w.blogs) == {"finance", "pick"}  # 세지 못한 블로그(이상한 count)는 빠짐 → 카드 그 줄은 '판단 불가'
    fin = w.blogs["finance"]
    assert fin.popular == 2  # count 보다 클 수 없음
    assert [p.title for p in fin.top] == ["가" * 59 + "…", "둘째"] and len(fin.top[0].title) == 60  # 한 줄, 60자
    assert fin.top[0].score == 88 and fin.top[0].saved_at is None and fin.top[0].category is None
    assert fin.top[1].score == 75 and fin.top[1].category == "LIFE"
    pick = w.blogs["pick"]
    assert pick.popular is None and [(p.title, p.score) for p in pick.top] == [("점수가 이상함", None), ("점수 없음", None)]
    for bad in (None, [], "x", {"blogs": []}):
        assert parse_export({"written_at": "2026-10-14T18:00:00+09:00", "waiting": bad}, "Asia/Seoul").waiting is None


# ---------------------------------------------------------------- '발행 대기 글' 카드
def test_waiting_card_shows_each_blog_with_top_titles_and_unknown_rows(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, WAITING)
    dash = make_dash(db, tmp_path)
    v = next(x for x in dash.views() if x.key == "blog_waiting")
    # 줄 이름은 블로그 카드 제목 그대로, 순서도 config 순서
    assert [(g.title, g.summary, g.unknown) for g in v.groups] == [
        ("Deren의 아카이브", "대기 4개 · 인기 예상 1개", False),
        ("Deren's Pick", "대기 글 없음", False),
        ("오늘의 핫딜 블로그", "판단 불가", True),
    ]
    top = v.groups[0].entries
    assert [(x.title, x.pill, x.hot) for x in top] == [  # 3개만, 기준(70) 이상은 알약 강조
        ("연말정산 미리보기", "82점", True), ("ISA 만기 후 연금계좌로 옮기는 법", "64점", False), ("같은 점수 중 더 새 글", "41점", False)]
    assert [(s.label, s.value) for s in v.stats] == [("대기 글", 4.0), ("인기 예상", 1.0)]
    assert v.stats[1].sub == "70점 이상" and v.status == ""  # 목표가 없는 카드 — 일부를 몰라도 오류 칩이 아님
    page = dash.page_html()
    card = card_html(page, "blog_waiting")
    assert "읽은 시각 10. 14. 18:00" in card  # 다른 Blog-Auto 카드처럼 파일의 written_at
    assert '<span class="lg-sum unknown">판단 불가</span>' in card and '<span class="pill hot">82점</span>' in card
    assert '<p class="hint">' not in card and "블로그 1개는 Blog-Auto 가 대기 글을 세지 못했어요" in card  # 회색 안내 한 줄
    detail = json.loads(dash.json_text())["cards"][1]["detail"]
    assert detail["threshold"] == 70 and [b["status"] for b in detail["blogs"]] == ["ok", "ok", "unknown"]
    assert detail["blogs"][0]["count"] == 4 and len(detail["blogs"][0]["top"]) == 4  # JSON 에는 받은 5개까지 모두
    assert detail["blogs"][0]["top"][0] == {"title": "연말정산 미리보기", "score": 82, "saved_at": "2026-10-13T09:12:00+09:00",
                                            "category": "MONEY"}
    # 숫자는 metrics 에도 (그래프용)
    assert db.metric_latest("blog_waiting", "waiting_total")["value"] == 4
    assert db.metric_latest("blog_waiting", "waiting_finance")["value"] == 4


def test_waiting_card_without_key_or_file_says_unknown_not_an_error(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path)
    # 파일 없음: 블로그 카드들과 같은 안내
    v = next(x for x in dash.views() if x.key == "blog_waiting")
    assert [g.summary for g in v.groups] == ["판단 불가"] * 3 and v.status == "unknown"
    assert any("기록 파일이 아직 없음" in h for h in v.hints)
    # 지금 배포된 Blog-Auto (waiting 없음): 줄마다 '판단 불가' + 회색 안내, 오류 띠 없음
    write_export(tmp_path, drop=True)
    page = dash.page_html()
    card = card_html(page, "blog_waiting")
    assert card.count('<span class="lg-sum unknown">판단 불가</span>') == 3 and '<p class="hint">' not in card
    assert "Blog-Auto 가 아직 발행 대기 글 수를 보내지 않아요" in card and "읽은 시각 10. 14. 18:00" in card
    assert "Deren의 아카이브" in card_html(page, "blog_finance")  # 나머지 카드는 그대로
    # 오래된 기록: 다른 Blog-Auto 카드처럼 판단 불가 + 몇 시간 전 것인지
    write_export(tmp_path, WAITING, written_at="2026-10-14T12:00:00+09:00")
    v = next(x for x in dash.views() if x.key == "blog_waiting")
    assert [g.summary for g in v.groups] == ["판단 불가"] * 3 and any("6시간 전 것" in h for h in v.hints)
    # 블로그 카드가 없으면 기본 세 줄
    dash2 = make_dash(db, tmp_path, [{"key": "w", "type": "blog_waiting"}])
    write_export(tmp_path, WAITING)
    assert [g.title for g in next(x for x in dash2.views()).groups] == ["아카이브", "픽", "핫딜"]
    # options.blogs 로 줄을 정할 수 있고, 비었거나 이상하면 기본으로
    dash3 = make_dash(db, tmp_path, [{"key": "w", "type": "blog_waiting", "options": {"blogs": [{"blog": "Issue", "title": "픽"}]}}])
    assert [(g.title, g.summary) for g in next(x for x in dash3.views()).groups] == [("픽", "대기 글 없음")]
    dash4 = make_dash(db, tmp_path, [{"key": "w", "type": "blog_waiting", "options": {"blogs": [{"title": "x"}]}}])
    assert [g.title for g in next(x for x in dash4.views()).groups] == ["아카이브", "픽", "핫딜"]


def test_waiting_titles_are_escaped(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, {"threshold": 70, "blogs": {"finance": {"count": 1, "popular": 1, "top": [
        {"title": "<img src=x onerror=alert(1)>", "score": 90, "category": "<b>"}]}}})
    dash = make_dash(db, tmp_path, [{"key": "w", "type": "blog_waiting", "title": "<script>x</script>"},
                                    {"key": "blog_finance", "type": "naver_blog", "title": '"><svg onload=1>', "options": {"blog": "finance"}}])
    page = dash.page_html()
    for raw in ("<img src=x", "<script>x", '"><svg onload'):
        assert raw not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page and "&quot;&gt;&lt;svg onload=1&gt;" in page


# ---------------------------------------------------------------- '링크 기다리는 딜' 카드
def test_links_card_lists_exactly_the_link_waiting_set_newest_first(db: Database, tmp_path: Path) -> None:
    waiting = [add_item(db, n, at=NOW - timedelta(hours=20 - n)) for n in range(12)]
    others = {status: add_item(db, 100 + i, status=status) for i, status in enumerate(
        ("pending", "awaiting_approval", "awaiting_autolink", "failed", "published", "skipped", "expired"))}
    assert LINK_WAITING == ("awaiting_link",)
    same_set = {it.id for it in db.awaiting_items(100)}  # 링크 요청 카톡·텔레그램 /pending·저녁 요약이 쓰는 묶음
    assert same_set == set(waiting) and db.awaiting_count() == 12 == db.queue_counts()["awaiting_link"]
    dash = make_dash(db, tmp_path, links=Desk(db))
    v = next(x for x in dash.views() if x.key == "links")
    shown = [x.queue_id for x in v.groups[0].entries]
    assert shown == list(reversed(waiting))[:10]  # 새것부터 10개
    assert not set(shown) & set(others.values())
    assert v.detail["total"] == 12 and v.detail["shown"] == 10 and [r["queue_id"] for r in v.detail["items"]] == shown
    assert [(s.label, s.value, s.unit) for s in v.stats] == [("기다리는 딜", 12.0, "개"), ("가장 오래 기다림", 20.0, "시간")]
    first = v.groups[0].entries[0]
    assert first.pill == "점수 12.5" and first.sub == "#12 · naver · 19,900원 · 10. 14. 09:05부터 9시간째"
    page = dash.page_html()
    card = card_html(page, "links")
    assert card.count('<form class="linkform" method="post" action="/dashboard/link">') == 10
    assert card.count('<button type="submit">링크 넣기</button>') == 10
    assert "나머지 2개는 텔레그램 /pending 에서" in card and "새것부터 10개만 보여요" in card
    # 상태가 바뀌면 바로 빠짐 (페이지를 열 때마다 봇 DB)
    db.update_queue_item(waiting[-1], status="skipped")
    v = next(x for x in dash.views() if x.key == "links")
    assert v.detail["total"] == 11 and waiting[-1] not in [x.queue_id for x in v.groups[0].entries]
    # 하나도 없으면 한 줄
    for qid in waiting:
        db.update_queue_item(qid, status="skipped")
    card = card_html(dash.page_html(), "links")
    assert '<p class="lempty">지금 링크를 기다리는 딜이 없어요.</p>' in card and "linkform" not in card


@pytest.mark.parametrize(("url", "shown"), [
    ("https://smartstore.naver.com/s/products/1?a=1&b=2", "https://smartstore.naver.com/s/products/1?a=1&amp;b=2"),
    ("http://smartstore.naver.com/s/products/1", None),
    ("javascript:alert(1)", None),
    ('https://x.example/"onmouseover="alert(1)', None),
    ("https://user@evil.example/", None),
    ("https:///nohost", None),
    ("//evil.example/x", None),
])
def test_product_button_is_https_only_and_escaped(db: Database, tmp_path: Path, url: str, shown: str | None) -> None:
    add_item(db, 1, url=url, name='<script>alert("x")</script> 상품')
    dash = make_dash(db, tmp_path, [{"key": "links", "type": "dealbot_links"}], links=Desk(db))
    card = card_html(dash.page_html(), "links")
    assert "<script>" not in card and "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; 상품" in card
    if shown is None:
        assert "상품 열기" not in card and "linkform" in card  # 링크 칸은 그대로 (상품 페이지만 못 엶)
        assert next(x for x in dash.views()).detail["items"][0]["url"] is None
    else:
        assert (f'<a class="ext primary" href="{shown}" target="_blank" rel="noopener noreferrer" '
                'aria-label="&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; 상품 상품 열기 (새 탭)">상품 열기') in card


# ---------------------------------------------------------------- POST /dashboard/link
async def test_link_post_needs_the_form_token_and_a_numeric_id(db: Database, tmp_path: Path) -> None:
    desk = Desk(db)
    qid = add_item(db, 1)
    dash = make_dash(db, tmp_path, links=desk)
    url = "https://naver.me/abc"
    for form in ({"q": str(qid), "url": url}, {"t": "", "q": str(qid), "url": url}, {"t": dash.form_token + "x", "q": str(qid), "url": url}):
        res = await dash._link(form)
        assert res[0] == 403 and res[3] == SECURITY_HEADERS
    for q in ("", "abc", "1.5", "-1", "1234567890", "<b>"):
        assert (await dash._link({"t": dash.form_token, "q": q, "url": url}))[0] == 400
    assert desk.calls == [] and db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    # 토큰은 /dashboard/input 과 같은 kv 토큰
    assert db.kv_get("dash:form_token") == dash.form_token


async def test_link_post_attaches_once_and_is_idempotent(db: Database, tmp_path: Path) -> None:
    desk = Desk(db)
    qid = add_item(db, 1)
    pending = add_item(db, 2, status="pending")  # attach_link 기준으로는 붙일 수 있지만 링크를 기다리는 글이 아님
    dash = make_dash(db, tmp_path, links=desk)
    tok = dash.form_token
    share = "[네이버쇼핑] 데켓 주물 플레이트\nhttps://naver.me/xAbC12 지금 확인"
    res = await dash._link({"t": tok, "q": f"#{qid}", "url": share})
    assert res == (303, f"/dashboard?msg=linked&src=links&q={qid}#card-links")
    assert desk.calls == [(qid, find_urls(share)[0])] == [(qid, "https://naver.me/xAbC12")] and desk.refreshed == 1
    item = db.get_queue_item(qid)
    assert item is not None and item.status == "pending" and item.deal.affiliate_url == "https://naver.me/xAbC12"
    # 두 번 눌러도: 이제 기다리는 글이 아니라 아무것도 안 함
    assert await dash._link({"t": tok, "q": str(qid), "url": "https://naver.me/other"}) == (
        303, f"/dashboard?msg=link_not_waiting&src=links&q={qid}#card-links")
    assert await dash._link({"t": tok, "q": str(pending), "url": "https://naver.me/x"}) == (
        303, f"/dashboard?msg=link_not_waiting&src=links&q={pending}#card-links")
    assert await dash._link({"t": tok, "q": "999", "url": "https://naver.me/x"}) == (
        303, "/dashboard?msg=link_not_waiting&src=links&q=999#card-links")
    assert desk.calls == [(qid, "https://naver.me/xAbC12")]
    # 봇이 없으면(대시보드만) 아무것도 부르지 않음
    lone = make_dash(db, tmp_path)
    assert (await lone._link({"t": tok, "q": str(add_item(db, 3)), "url": "https://naver.me/y"}))[1].startswith("/dashboard?msg=link_off")


async def test_link_post_rate_limit_per_process(db: Database, tmp_path: Path) -> None:
    clock = Clock()
    desk = Desk(db)
    dash = make_dash(db, tmp_path, links=desk, clock=clock)
    ids = [add_item(db, n) for n in range(LINK_RATE + 2)]
    for qid in ids[:LINK_RATE - 1]:
        assert (await dash._link({"t": dash.form_token, "q": str(qid), "url": f"https://naver.me/{qid}"}))[1].startswith("/dashboard?msg=linked")
    assert (await dash._link({"t": "wrong", "q": "1", "url": "x"}))[0] == 403  # 틀린 토큰도 한도에 셈 (맞혀 보기 막기)
    res = await dash._link({"t": dash.form_token, "q": str(ids[-2]), "url": "https://naver.me/late"})
    assert res == (303, "/dashboard?msg=link_busy&src=links#card-links")
    assert len(desk.calls) == LINK_RATE - 1 and db.get_queue_item(ids[-2]).status == "awaiting_link"  # type: ignore[union-attr]
    clock.t += timedelta(seconds=61)
    assert (await dash._link({"t": dash.form_token, "q": str(ids[-2]), "url": "https://naver.me/late"}))[1].startswith(
        "/dashboard?msg=linked")
    _, body, _, _ = await dash._page({"msg": "link_busy", "src": "links"})
    assert f"1분에 {LINK_RATE}번까지" in card_html(body, "links")


async def test_link_post_uses_the_same_guard_as_the_telegram_reply(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """URL 은 텔레그램 답장과 같은 길: find_urls 의 첫 주소 → DealBot._link_problem (attach_link 와 같은 기준) → attach_link 한 번."""
    settings.collectors = []
    settings.secrets.dashboard_user, settings.secrets.dashboard_password = USER, PASSWORD
    bot = DealBot(settings)
    try:
        assert bot.dashboard is not None and bot.dashboard.links is bot
        qid = add_item(bot.db, 1)
        calls: list[tuple[int, str]] = []
        real = bot.attach_link

        async def spy(queue_id: int, url: str) -> str:
            calls.append((queue_id, url))
            return await real(queue_id, url)

        monkeypatch.setattr(bot, "attach_link", spy)
        tok = bot.dashboard.form_token

        async def post(text: str) -> Any:
            body = urlencode({"t": tok, "q": str(qid), "url": text}).encode()
            return await bot.web.dispatch("POST", "/dashboard/link", {**AUTH, **FORM}, body)

        for bad in ("https://t.me/oneul_hotdeal/123", "답장 https://telegram.me/x", "javascript:alert(1)", "ftp://x.example/a",
                    "링크 없음", "www.naver.com/abc"):
            assert (await post(bad))[:2] == (303, f"/dashboard?msg=link_bad&src=links&q={qid}#card-links"), bad
            # 텔레그램 답장 길(find_urls → attach_link)에서도 같은 주소는 같은 이유로 막힘
            for u in find_urls(bad)[:1]:
                assert bot.owner_link_problem(qid, u) is not None and "상품 링크 완료" not in await real(qid, u)
        # 링크 칸이 너무 길면 (텔레그램보다 엄격하게) 아무것도 부르지 않음
        assert (await post("x" * 2001 + " https://naver.me/a"))[1].startswith("/dashboard?msg=link_bad")
        assert calls == [] and bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
        # 거르는 함수가 정말 같은 것인지: _link_problem 이 막으면 좋은 링크도 막힘
        monkeypatch.setattr(DealBot, "_link_problem", classmethod(lambda cls, item, q, url, **_kw: "막힘"))
        assert (await post("https://naver.me/good"))[1].startswith("/dashboard?msg=link_bad")
        assert calls == []
        monkeypatch.undo()
        monkeypatch.setattr(bot, "attach_link", spy)
        res = await post("https://naver.me/good")
        assert res[:2] == (303, f"/dashboard?msg=linked&src=links&q={qid}#card-links")
        assert calls == [(qid, "https://naver.me/good")]
        item = bot.db.get_queue_item(qid)
        assert item is not None and item.status == "pending" and item.deal.affiliate_url == "https://naver.me/good"
        assert bot.db.kv_get("mylink:naver:1") == "https://naver.me/good"  # 텔레그램 답장처럼 '내 링크' 로 기억
        assert (await post("https://naver.me/again"))[1].startswith("/dashboard?msg=link_not_waiting") and len(calls) == 1
    finally:
        await bot.close()


async def test_link_post_over_http_checks_method_type_size_and_origin(db: Database, tmp_path: Path) -> None:
    desk = Desk(db)
    qid = add_item(db, 1, name="<b>굵은</b> 상품")
    dash = make_dash(db, tmp_path, links=desk)
    srv = WebServer(0, host="127.0.0.1")
    assert dash.register(srv)
    await srv.start()
    form = {"t": dash.form_token, "q": str(qid), "url": "https://naver.me/abc"}
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{srv.port}") as c:
            r = await c.post("/dashboard/link", data=form)
            assert r.status_code == 401  # 기본 인증이 먼저
            r = await c.get("/dashboard/link", auth=(USER, PASSWORD))
            assert r.status_code == 405 and r.headers["allow"] == "POST"
            r = await c.post("/dashboard", data=form, auth=(USER, PASSWORD))
            assert r.status_code == 405  # 다른 대시보드 경로는 POST 를 받지 않음
            r = await c.post("/dashboard/link", json=form, auth=(USER, PASSWORD))
            assert r.status_code == 415
            r = await c.post("/dashboard/link", data={**form, "url": "x" * (LINK_BODY_MAX + 10)}, auth=(USER, PASSWORD))
            assert r.status_code == 413
            r = await c.post("/dashboard/link", data=form, auth=(USER, PASSWORD), headers={"sec-fetch-site": "cross-site"})
            assert r.status_code == 403
            r = await c.post("/dashboard/link?t=" + dash.form_token, data={"q": str(qid), "url": "https://naver.me/abc"},
                             auth=(USER, PASSWORD))
            assert r.status_code == 403  # 토큰은 본문에서만 (주소의 쿼리는 안 봄)
            assert desk.calls == []
            r = await c.post("/dashboard/link", data=form, auth=(USER, PASSWORD), headers={"sec-fetch-site": "same-origin"})
            assert r.status_code == 303 and r.headers["location"] == f"/dashboard?msg=linked&src=links&q={qid}#card-links"
            assert desk.calls == [(qid, "https://naver.me/abc")]
            r = await c.get(r.headers["location"], auth=(USER, PASSWORD), follow_redirects=True)  # /dashboard → /dashboard/
            assert r.status_code == 200
            csp = r.headers["content-security-policy"]
            assert "default-src 'none'" in csp and "form-action 'self'" in csp  # 폼은 같은 서버로만
            card = card_html(r.text, "links")
            assert f'<div class="notice" role="status">#{qid} 링크를 넣었어요 — 올릴 차례가 되면 채널에 올라가요.</div>' in card
            assert "<b>굵은</b>" not in r.text and "linkform" not in card  # 붙인 글은 목록에서 빠짐
    finally:
        await srv.stop()


async def test_link_notices_never_reflect_the_input(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, links=Desk(db))
    _, body, _, _ = await dash._page({"msg": "linked", "src": "links", "q": "<script>alert(1)</script>"})
    card = card_html(body, "links")
    assert "<script>alert" not in body and '<div class="notice" role="status">링크를 넣었어요' in card
    _, body, _, _ = await dash._page({"msg": "link_bad", "src": "links", "q": "12"})
    assert "#12 이 링크는 붙일 수 없어요" in card_html(body, "links") and "notice bad" in card_html(body, "links")
    _, body, _, _ = await dash._page({"msg": "<img src=x>", "src": "links", "q": "1"})
    assert "<img src=x>" not in body and 'class="notice' not in body


async def test_json_has_both_cards_but_never_the_form_token(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, WAITING)
    qid = add_item(db, 1, name="토큰 없는 JSON")
    dash = make_dash(db, tmp_path, links=Desk(db))
    tok = dash.form_token
    assert f'name="t" value="{tok}"' in dash.page_html()  # 화면의 폼에만
    _, text, ctype, _ = await dash._data({})
    assert ctype.startswith("application/json") and tok not in text
    cards = {c["key"]: c for c in json.loads(text)["cards"]}
    assert cards["links"]["detail"]["total"] == 1 and cards["links"]["detail"]["items"][0]["queue_id"] == qid
    assert cards["links"]["detail"]["items"][0]["name"] == "토큰 없는 JSON"
    assert cards["blog_waiting"]["detail"]["blogs"][0]["popular"] == 1


# ---------------------------------------------------------------- 웹 서버 POST 규칙
async def test_web_server_post_rules() -> None:
    srv = WebServer(0)
    seen: list[dict[str, str]] = []

    async def handler(form: dict[str, str]) -> Any:
        seen.append(form)
        return 303, "/done"

    srv.route_post("/f", handler, max_body=64)
    assert await srv.dispatch("POST", "/f?x=1", FORM, b"a=1&b=%ED%95%9C") == (303, "/done")
    assert seen == [{"a": "1", "b": "한"}]  # 주소의 쿼리는 넘기지 않음
    assert (await srv.dispatch("POST", "/f", FORM, None))[0] == 411  # 길이를 몰라 읽지 않음
    assert (await srv.dispatch("POST", "/f", {**FORM, "content-length": "999"}, None))[0] == 413
    assert (await srv.dispatch("POST", "/f", FORM, b"a" * 65))[0] == 413
    assert (await srv.dispatch("POST", "/f", FORM, b"\xff\xfe"))[0] == 400
    assert (await srv.dispatch("POST", "/f", {"content-type": "text/plain"}, b"a=1"))[0] == 415
    assert (await srv.dispatch("POST", "/f", {**FORM, "sec-fetch-site": "cross-site"}, b"a=1"))[0] == 403
    assert (await srv.dispatch("GET", "/f"))[0] == 405 and (await srv.dispatch("PUT", "/f"))[0] == 405
    assert (await srv.dispatch("POST", "/other", FORM, b"a=1"))[0] == 405
    assert len(seen) == 1  # 거른 요청은 핸들러까지 가지 않음
    big = WebServer(0)
    big.route_post("/f", handler, max_body=4096)
    assert (await big.dispatch("POST", "/f", FORM, "&".join(f"k{i}=1" for i in range(25)).encode()))[0] == 400  # 칸이 너무 많음
