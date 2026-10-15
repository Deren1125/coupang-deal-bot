"""대시보드 맨 위 '지금 할 일' (주인 요청 10/15: "그거 기반으로 대시보드에 다 넣어줘 지금 해야할 일 목록 느낌으로").

Blog-Auto 기록 파일의 "todo" + 봇 DB 의 지금 상태 (링크 기다리는 딜 한 줄 · 확인 기다리는 글 · 채널에 올라갔는지 모르는 글).
네트워크 없음: 기록 파일은 tmp 에 쓰고, DB 는 테스트 DB.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dealbot.config import DashboardCardConfig, DashboardConfig
from dealbot.dashboard import render
from dealbot.dashboard import todo as todo_mod
from dealbot.dashboard.blogauto import BlogAutoReader, parse_export, parse_todo
from dealbot.dashboard.service import Dashboard
from dealbot.dashboard.sources import SourceContext
from dealbot.dashboard.todo import TodoRow
from dealbot.models import Deal, DealVerdict, Product
from dealbot.storage.db import Database

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)  # 한국시간 10/15 15:00
WRITTEN = "2026-10-15T14:30:05+09:00"
CARDS = [
    {"key": "links", "type": "dealbot_links", "title": "링크 기다리는 딜"},
    {"key": "blog_waiting", "type": "blog_waiting", "title": "발행 대기 글"},
    {"key": "blog_finance", "type": "naver_blog", "title": "Deren의 아카이브", "options": {"blog": "finance", "blog_id": "derenlog"}},
    {"key": "blog_issue", "type": "naver_blog", "title": "Deren's Pick", "options": {"blog": "issue", "blog_id": "derenpick"}},
    {"key": "dealbot", "type": "dealbot", "title": "핫딜 봇 운영"},
]


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def make_dash(db: Database, tmp_path: Path, cards: list[dict[str, Any]] | None = None, *, ttl: float | None = 12,
              clock: Clock | None = None) -> Dashboard:
    clock = clock or Clock()
    cfg = DashboardConfig(cards=[DashboardCardConfig(**c) for c in (CARDS if cards is None else cards)],
                          blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    ctx = SourceContext(db=db, tz="Asia/Seoul", config=cfg, now=clock, link_ttl_hours=ttl,
                        blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=3, now=clock),
                        shop_label={"naver": "네이버", "toss": "토스"}.get)
    return Dashboard(cfg, ctx, user="owner", password="plum-river-cactus-42")


def add_item(db: Database, n: int, *, status: str = "awaiting_link", hours: float = 1.0, name: str | None = None,
             shop: str = "naver", price: int = 19900, url: str | None = None, info: bool = False, error: str | None = None,
             updated_hours: float | None = None) -> int:
    pid = f"{shop}:{n}"
    p = Product(source="ppomppu", product_id=pid, shop=shop, name=name or f"상품 {n}", price=price,
                url=url or f"https://smartstore.naver.com/s/products/{n}")
    if info:
        p.deal_kind = "info"
    at = NOW - timedelta(hours=hours)
    assert db.enqueue(Deal(p, DealVerdict(is_deal=True, score=12)), score=12, now=at)
    qid = db.items_for_product(pid, ("pending",))[0].id
    if status != "pending":
        upd = NOW - timedelta(hours=updated_hours) if updated_hours is not None else at
        db.update_queue_item(qid, status=status, error=error, now=upd)
    return qid


def item(key: str, short: str, text: str, *, marker: str = "🔔[할 일]", since: str = "2026-10-15T12:00:00+09:00",
         **kw: Any) -> dict[str, Any]:
    return {"key": key, "marker": marker, "blog": kw.pop("blog", "finance"), "kind": kw.pop("kind", "x"), "short": short,
            "text": text, "since": since, "urgent": kw.pop("urgent", False), "link": kw.pop("link", ""),
            "group": kw.pop("group", ""), "count": kw.pop("count", 1), **kw}


def write_export(tmp_path: Path, items: list[dict[str, Any]] | None, *, written_at: str = WRITTEN,
                 generated_at: str | None = WRITTEN) -> None:
    data: dict[str, Any] = {"version": 1, "written_at": written_at, "blogs": {"finance": {"posts_published_today": 1}}}
    if items is not None:
        data["todo"] = {"generated_at": generated_at, "items": items}
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def todo_section(page: str) -> str:
    return page.split('id="todo"', 1)[1].split("</section>", 1)[0]


def rows_of(dash: Dashboard) -> dict[str, TodoRow]:
    return {r.key: r for r in dash.todo().rows}


# ---------------------------------------------------------------- 파일의 "todo" 읽기
def test_todo_export_is_parsed_item_by_item() -> None:
    token = "A" * 45
    raw = {"generated_at": WRITTEN, "items": [
        item("draft_ready:issue:31", "#31 발행 대기",
             "🔔[할 일] [Deren's Pick] #31 발행 대기 「가을 코트」\n→ 텔레그램 Deren's Pick 봇에서 🚀 발행\n둘째 줄\n셋째 줄은 버림",
             blog="Issue", kind="draft_ready", count=1, link="https://m.blog.naver.com/derenpick"),
        "문자열 항목",
        {"key": "empty"},  # 글도 요약도 없음 → 버림
        item("own:issue", "로그인 확인", "⚠[할 일] 통계 읽기 막힘\n→ 로그인 확인", marker="모르는 표시"),  # VS16 빠진 표시 → 글 첫머리로
        item("health", "새 글 없음", "ℹ 아카이브 7일째 새 글 없음 — 글감이 모두 걸러짐", marker="ℹ"),
        item("tok", "긴 요약 " + "가" * 60, f"🔔[할 일] 토큰 {token} 이 들어감\n→ access_token=abc123 지우기",
             link="http://admin.blog.naver.com/x", count="3", urgent="yes", since="2026-10-15T09:00:00"),
        {"marker": "⛔[멈춤]", "short": "키 없는 줄", "text": "⛔[멈춤] 키 없는 줄\n→ 확인", "link": "javascript:alert(1)", "count": 0},
    ]}
    t = parse_todo(raw, "Asia/Seoul")
    assert t is not None and t.generated_at == datetime(2026, 10, 15, 5, 30, 5, tzinfo=UTC)
    by = {it.key: it for it in t.items}
    assert list(by) == ["draft_ready:issue:31", "own:issue", "health", "tok", "todo:키 없는 줄"]
    a = by["draft_ready:issue:31"]
    assert (a.marker, a.blog, a.kind, a.short) == ("🔔[할 일]", "issue", "draft_ready", "#31 발행 대기")
    assert a.what == "[Deren's Pick] #31 발행 대기 「가을 코트」"  # 표시는 칩으로 따로
    assert a.how == "→ 텔레그램 Deren's Pick 봇에서 🚀 발행\n둘째 줄"  # '→ 어떻게' 는 두 줄까지
    assert a.link == "https://m.blog.naver.com/derenpick"
    assert by["own:issue"].marker == "⚠️[할 일]" and by["own:issue"].what == "통계 읽기 막힘"
    assert by["health"].marker == "ℹ️" and by["health"].how == ""
    tok = by["tok"]
    assert token not in tok.what and "***" in tok.what and "abc123" not in tok.how  # 토큰 모양은 가림
    assert len(tok.short) <= 40 and tok.short.endswith("…")
    assert tok.link is None and tok.count == 1 and tok.urgent is False  # http 주소·글자 숫자·참이 아닌 값은 안 믿음
    assert tok.since == datetime(2026, 10, 15, 0, 0, tzinfo=UTC)  # 시간대가 없으면 한국시간
    keyless = by["todo:키 없는 줄"]
    assert keyless.marker == "⛔[멈춤]" and keyless.link is None and keyless.count == 1
    # 모양이 다르면 None ('블로그 쪽 할 일을 못 읽음'), 다른 키는 그대로
    for bad in (None, [], {"items": "x"}, {"generated_at": WRITTEN}):
        assert parse_todo(bad, "Asia/Seoul") is None
    old = {"version": 1, "written_at": WRITTEN, "blogs": {"finance": {"posts_published_today": 1}}}
    a, b = parse_export(old, "Asia/Seoul"), parse_export({**old, "todo": raw}, "Asia/Seoul")
    assert a.todo is None and b.todo is not None and a.blogs == b.blogs and a.written_at == b.written_at


# ---------------------------------------------------------------- 합치기 (봇 DB 가 이김)
def test_live_db_overrides_exported_deal_items_and_drops_closed_ones(db: Database, tmp_path: Path) -> None:
    l1 = add_item(db, 1, hours=10.5, name="곰곰 대란 30구")
    l2 = add_item(db, 2, hours=4)
    l3 = add_item(db, 3, hours=0.6)
    rv = add_item(db, 4, status="awaiting_approval", hours=3, name="나이키 에어포스1 공식몰")
    us = add_item(db, 6, status="failed", hours=5, error="maybe posted: Timed out", name="다이슨 V15")
    gone_link = add_item(db, 7, status="skipped", hours=2, error="skipped by admin")
    gone_review = add_item(db, 8, status="published", hours=2)
    not_unsure = add_item(db, 9, status="failed", hours=2, error="Bad Request: chat not found")
    write_export(tmp_path, [
        item(f"link:{l1}", f"핫딜 링크 #{l1}", f"🔔[할 일] 링크 필요 #{l1}\n→ 텔레그램 핫딜봇 #{l1} 에 답장", blog="dealbot",
             group="link_request"),
        item(f"link:{gone_link}", f"핫딜 링크 #{gone_link}", "🔔[할 일] 링크 필요\n→ 답장", blog="dealbot", group="link_request"),
        item("deal:link_request:123", "링크 필요 2건", "🔔[할 일] 링크 필요 2건\n→ 답장", group="link_request"),
        item(f"review:{rv}", "옛 글", "🔔[할 일] 파일에 있던 옛 글\n→ 옛 안내", blog="dealbot"),
        item(f"review:{gone_review}", "정품 확인", "🔔[할 일] 정품 확인\n→ /ok", blog="dealbot"),
        item("review:999", "없는 글", "🔔[할 일] 없는 글\n→ /ok", blog="dealbot"),
        item(f"unsure:{us}", "채널 확인", "🔔[할 일] 채널 확인 옛 글\n→ /posted", blog="dealbot"),
        item(f"unsure:{not_unsure}", "채널 확인", "🔔[할 일] 채널 확인\n→ /posted", blog="dealbot"),
        item("publish_broken", "딜봇 채널 발행 막힘", "⛔[멈춤] 딜봇이 채널에 못 올리고 있어요 (3건 연속 실패)\n→ 텔레그램 핫딜봇 /errors",
             marker="⛔[멈춤]", blog="dealbot", urgent=True),
        item("linkprice:toss", "링크프라이스 토스 승인", "🔔[할 일] 링크프라이스 토스 승인 확인\n→ 링크프라이스 사이트", blog="dealbot"),
        item("sns:instatoken", "인스타 다시 연결", "⚠️[할 일] 인스타그램 연결이 끊겼어요\n→ 텔레그램 핫딜봇 /instatoken",
             marker="⚠️[할 일]", blog="dealbot"),
        item("waiting:finance", "아카이브 발행 대기 4개", "🔔[할 일] 대기 4개\n→ 아카이브 봇", kind="waiting", count=4),
        item("waiting:finance", "아카이브 발행 대기 4개", "⚠️[할 일] 대기 4개 (급함)\n→ 아카이브 봇", marker="⚠️[할 일]", kind="waiting"),
    ])
    dash = make_dash(db, tmp_path)
    rows = rows_of(dash)
    assert set(rows) == {"link_request", f"review:{rv}", f"unsure:{us}", "publish_broken", "linkprice:toss",
                         "sns:instatoken", "waiting:finance"}
    links = rows["link_request"]
    assert (links.source, links.count, links.ids, links.short) == ("dealbot", 3, [l1, l2, l3], "핫딜 링크 3건")
    assert links.what == f"링크 필요 3건 #{l1} #{l2} #{l3}" and links.anchor == "#card-links" and links.anchor_label == "링크 넣기"
    assert links.since == NOW - timedelta(hours=10.5)
    assert links.expires_at == NOW + timedelta(hours=1.5)  # 가장 오래된 것 + 12시간 (expire_queue 와 같은 기준)
    review = rows[f"review:{rv}"]
    assert review.source == "dealbot" and review.what == f"정품 확인 #{rv} 나이키 에어포스1 공식몰"
    assert review.how == f"→ 텔레그램 핫딜봇에서 /ok {rv} 또는 /skip {rv}" and review.link_label == "상품 열기"
    unsure = rows[f"unsure:{us}"]
    assert unsure.source == "dealbot" and unsure.how == f"→ 채널에 있으면 텔레그램 핫딜봇 /posted {us} · 없으면 /link {us}"
    assert unsure.since == NOW - timedelta(hours=5)
    assert rows["waiting:finance"].marker == "⚠️[할 일]"  # 같은 키가 두 번이면 더 급한 것 하나
    assert rows["waiting:finance"].anchor == "#card-blog_waiting"  # 주소가 없는 대기 글 줄 → '발행 대기 글' 카드로
    assert rows["publish_broken"].source == "blogauto" and rows["linkprice:toss"].blog_label == "핫딜 봇"
    # 화면에는 '지금 할 일 7' — 처리한 글(#7 #8 #9 #999)은 없다
    page = dash.page_html()
    sec = todo_section(page)
    assert '<span class="todo-n">7</span>' in sec
    for gone in (gone_link, gone_review, not_unsure, 999):
        assert f"#{gone}" not in sec
    assert "옛 안내" not in sec and "파일에 있던 옛 글" not in sec


def test_link_row_for_one_deal_names_it_and_without_links_card_points_to_telegram(db: Database, tmp_path: Path) -> None:
    q = add_item(db, 1, hours=2, name="토스 단독  에어팟 프로", shop="toss", price=219000)
    write_export(tmp_path, [])
    row = rows_of(make_dash(db, tmp_path))["link_request"]
    assert row.short == f"핫딜 링크 #{q}" and row.what == f"링크 필요 #{q} [토스] 토스 단독 에어팟 프로 — 219,000원"
    no_card = make_dash(db, tmp_path, cards=[c for c in CARDS if c["type"] != "dealbot_links"])
    row = rows_of(no_card)["link_request"]
    assert row.anchor is None and row.how == "→ 텔레그램 핫딜봇 📌고정 목록에 한 번에 답장"
    assert "링크 넣기" not in todo_section(no_card.page_html())


def test_review_rows_say_ok_or_skip_and_only_auth_reviews_link_the_product(db: Database, tmp_path: Path) -> None:
    auth = add_item(db, 1, status="awaiting_approval", hours=1, url="https://brand.example/p/1")
    plain = add_item(db, 2, status="awaiting_approval", hours=2, url="http://brand.example/p/2")
    info = add_item(db, 3, status="awaiting_approval", hours=3, info=True, name="11월 카드 무이자 할부 정리", price=0)
    write_export(tmp_path, [])
    rows = rows_of(make_dash(db, tmp_path))
    assert rows[f"review:{auth}"].link == "https://brand.example/p/1" and rows[f"review:{auth}"].kind == "review_auth"
    assert rows[f"review:{plain}"].link is None  # https 가 아니면 버튼 없음
    r = rows[f"review:{info}"]
    assert (r.kind, r.short, r.link) == ("review_info", f"정보 글 확인 #{info}", None)
    assert r.what == f"정보 글 확인 #{info} 11월 카드 무이자 할부 정리 — 이대로 올릴까요?"
    assert r.how == f"→ 텔레그램 핫딜봇에서 /ok {info} · /skip {info}"
    assert all(rows[k].expires_at == rows[k].since + timedelta(hours=12) for k in rows)  # 확인도 같은 시간에 끝남


def test_publish_unsure_rows_only_for_the_last_week(db: Database, tmp_path: Path) -> None:
    fresh = add_item(db, 1, status="failed", hours=30, updated_hours=26, error="maybe posted: Timed out")
    old = add_item(db, 2, status="failed", hours=200, updated_hours=24 * 8, error="maybe posted: Timed out")
    write_export(tmp_path, [])
    rows = rows_of(make_dash(db, tmp_path))
    assert set(rows) == {f"unsure:{fresh}"} and old != fresh
    assert rows[f"unsure:{fresh}"].since == NOW - timedelta(hours=26)  # 시간 초과가 난 때부터


# ---------------------------------------------------------------- 순서
def test_order_stop_then_urgent_then_expiring_then_todo_then_info() -> None:
    def r(key: str, marker: str = todo_mod.TODO, hours: float = 1, **kw: Any) -> TodoRow:
        return TodoRow(key=key, marker=marker, short=key, since=NOW - timedelta(hours=hours), **kw)

    rows = [
        r("info", todo_mod.INFO, hours=100),
        r("todo_new", hours=1),
        r("todo_old", hours=30),
        r("review_9h", expires_at=NOW + timedelta(hours=9)),
        r("links_1h", expires_at=NOW + timedelta(hours=1)),
        r("warn", todo_mod.WARN, hours=2),
        r("urgent_todo", urgent=True, hours=5),
        r("hot", todo_mod.HOT, hours=1),
        r("stop_new", todo_mod.STOP, hours=1),
        r("stop_old", todo_mod.STOP, hours=9),
        TodoRow(key="no_time", marker=todo_mod.TODO, short="no_time"),
    ]
    got = [x.key for x in todo_mod.merge(rows, None)]
    assert got == ["stop_old", "stop_new", "hot", "warn", "urgent_todo", "links_1h", "review_9h", "todo_old", "todo_new",
                   "no_time", "info"]


def test_expiry_and_age_text(db: Database, tmp_path: Path) -> None:
    assert todo_mod.left(NOW + timedelta(hours=14, minutes=59), NOW) == "14시간 남음"
    assert todo_mod.left(NOW + timedelta(minutes=40, seconds=30), NOW) == "40분 남음"
    assert todo_mod.left(NOW + timedelta(hours=60), NOW) == "2일 남음"
    assert todo_mod.left(NOW - timedelta(minutes=5), NOW) == "곧 끝남"
    assert todo_mod.ago(NOW - timedelta(seconds=20), NOW) == "방금"
    assert todo_mod.ago(NOW + timedelta(minutes=3), NOW) == "방금"  # 시계가 어긋난 앞날
    assert todo_mod.ago(NOW - timedelta(minutes=5), NOW) == "5분 전"
    assert todo_mod.ago(NOW - timedelta(hours=3, minutes=59), NOW) == "3시간 전"
    assert todo_mod.ago(NOW - timedelta(days=2, hours=5), NOW) == "2일 전"
    add_item(db, 1, hours=11 + 2 / 3)  # 12시간 중 11시간 40분 기다림
    write_export(tmp_path, [])
    sec = todo_section(make_dash(db, tmp_path).page_html())
    assert '<span class="left">20분 남음</span>' in sec and "11시간 전" in sec
    # 끝나는 시간을 모르면(설정을 못 받음) 남은 시간 없이
    sec = todo_section(make_dash(db, tmp_path, ttl=None).page_html())
    assert "남음" not in sec and "11시간 전" in sec


# ---------------------------------------------------------------- 화면
def test_escaping_and_only_https_non_telegram_links(db: Database, tmp_path: Path) -> None:
    bad_links = ["javascript:alert(1)", "http://example.com/x", "https://t.me/hot_deal_bot", "https://telegram.me/x",
                 'https://evil.example/"onmouseover=alert(1)', "https://user@evil.example/", "//evil.example/x", "data:text/html,x"]
    items = [item(f"bad:{i}", f"나쁜 링크 {i}", "🔔[할 일] 나쁜 링크\n→ 눌러 보기", link=u) for i, u in enumerate(bad_links)]
    items.append(item("xss", "<script>alert(1)</script>", '🔔[할 일] <b>굵게</b>\n→ "><img src=x onerror=alert(1)>',
                      blog="<i>blog</i>"))
    items.append(item("buddy:finance", "아카이브 서로이웃 신청 2개", "🔔[할 일] 받은 서로이웃 신청 2개\n→ 이웃 관리에서 수락·거절",
                      link="https://admin.blog.naver.com/derenlog/buddy/relation", count=2))
    write_export(tmp_path, items)
    dash = make_dash(db, tmp_path)
    page = dash.page_html()
    sec = page.split('id="todo"', 1)[1].split('<section class="summary">', 1)[0]
    assert "<script>alert" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in sec
    assert "<img src=x" not in page and "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in sec
    hrefs = re.findall(r'href="([^"]*)"', sec)
    assert hrefs == ["https://admin.blog.naver.com/derenlog/buddy/relation"]  # 나쁜 주소는 버튼 없이 글만
    a = re.search(r'<a class="td-act"[^>]*>', sec)
    assert a and 'target="_blank"' in a.group(0) and 'rel="noopener noreferrer"' in a.group(0)
    assert "신청 보기" in sec and "t.me" not in sec and "telegram.me" not in sec
    data = json.loads(dash.json_text())["todo"]
    assert [x["link"] for x in data["items"] if x["link"]] == ["https://admin.blog.naver.com/derenlog/buddy/relation"]
    assert all(x["anchor"] is None or x["anchor"].startswith("#card-") for x in data["items"])
    assert "t.me" not in json.dumps(data)


def test_empty_state(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, [])
    dash = make_dash(db, tmp_path)
    page = dash.page_html()
    sec = todo_section(page)
    assert "지금 할 일 없음 ✓" in sec and "todo-hint" not in sec and "더 보기" not in sec and "todo-n" not in sec
    data = json.loads(dash.json_text())["todo"]
    assert (data["count"], data["items"], data["hints"], data["empty_text"]) == (0, [], [], "지금 할 일 없음 ✓")


def test_missing_old_or_stale_export_keeps_live_items_with_a_hint(db: Database, tmp_path: Path) -> None:
    add_item(db, 1, hours=2)
    dash = make_dash(db, tmp_path)  # 파일 없음
    view = dash.todo()
    assert [r.key for r in view.rows] == ["link_request"]
    assert view.hints == ["블로그 쪽 할 일을 못 읽음 — Blog-Auto 기록 파일이 없거나 읽지 못했어요"]
    sec = todo_section(dash.page_html())
    assert '<p class="todo-hint">블로그 쪽 할 일을 못 읽음' in sec and "핫딜 링크 #" in sec
    write_export(tmp_path, None)  # 예전 Blog-Auto (todo 없음)
    view = dash.todo()
    assert [r.key for r in view.rows] == ["link_request"] and "아직 할 일 목록을 보내지 않아요" in view.hints[0]
    # 5시간 전 파일 — 보인 그대로 + 몇 시간 전 것인지
    write_export(tmp_path, [item("waiting:finance", "아카이브 발행 대기 4개", "🔔[할 일] 대기 4개\n→ 아카이브 봇")],
                 written_at="2026-10-15T10:00:00+09:00", generated_at="2026-10-15T10:00:00+09:00")
    view = dash.todo()
    assert {r.key for r in view.rows} == {"link_request", "waiting:finance"}
    assert view.hints == ["블로그 쪽 할 일은 5시간 전 것이에요 — Blog-Auto 가 멈췄는지 확인해 주세요"]


def test_more_rows_fold_under_details_and_the_title_counts_them(db: Database, tmp_path: Path) -> None:
    write_export(tmp_path, [item(f"k{i}", f"할 일 {i}", f"🔔[할 일] 할 일 {i}\n→ 하기", since=f"2026-10-15T0{i}:00:00+09:00")
                            for i in range(8)])
    page = make_dash(db, tmp_path).page_html()
    sec = page.split('id="todo"', 1)[1].split('<section class="summary">', 1)[0]
    assert '지금 할 일<span class="todo-n">8</span>' in sec
    head, more = sec.split('<details class="todo-more">', 1)
    assert head.count('<li class="td">') == 6 and more.count('<li class="td">') == 2
    assert "<summary>더 보기 (2개)</summary>" in more
    assert [int(x) for x in re.findall(r"할 일 (\d)</span>", sec)] == list(range(8))  # 오래 기다린 것부터
    assert "<script" not in page  # '더 보기' 는 스크립트 없이 (CSP default-src 'none')


def test_todo_card_sits_above_the_summary_and_json_has_the_list(db: Database, tmp_path: Path) -> None:
    add_item(db, 1, hours=10.5)
    write_export(tmp_path, [item("waiting:issue", "Pick 발행 대기 7개", "🔔[할 일] [Deren's Pick] 발행 대기 7개\n→ Pick 봇에서 발행",
                                 blog="issue", kind="waiting", count=7)])
    dash = make_dash(db, tmp_path)
    page = dash.page_html()
    assert page.index('<header class="top">') < page.index('id="todo"') < page.index('<section class="summary">') \
        < page.index('<main class="grid">')
    data = json.loads(dash.json_text())
    assert list(data)[:3] == ["title", "generated_at", "todo"]
    todo = data["todo"]
    assert todo["count"] == 2 and todo["blog_generated_at"] == WRITTEN
    first, second = todo["items"]
    assert first["key"] == "link_request" and first["left"] == "1시간 남음" and first["age"] == "10시간 전"
    assert first["anchor"] == "#card-links" and first["action"] == "링크 넣기" and first["source"] == "dealbot"
    assert first["expires_at"] == "2026-10-15T16:30:00+09:00" and first["text"].startswith("🔔[할 일] 링크 필요 #")
    assert second["key"] == "waiting:issue" and second["blog_label"] == "Deren's Pick" and second["count"] == 7
    assert second["anchor"] == "#card-blog_waiting" and second["text"] == "🔔[할 일] [Deren's Pick] 발행 대기 7개\n→ Pick 봇에서 발행"
    assert "t=" not in json.dumps(todo)  # 폼 토큰 없음


def test_layout_fits_a_390px_phone(db: Database, tmp_path: Path) -> None:
    """390px (좌우 16px 여백 → 카드 358px): 줄이 넘치지 않게 (실제 크롬 390px 확인은 미리보기에서 — scrollWidth 390)."""
    css = render.CSS
    rules: dict[str, str] = {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        rules.setdefault(m.group(1).strip(), m.group(2))  # 기본 규칙 (@media 안의 같은 이름은 뒤에 따로 확인)
    assert "min-width:0" in rules[".todo"] and "min-width:0" in rules[".td"] and "min-width:0" in rules[".td-title"]
    assert "overflow-wrap:anywhere" in rules[".td-title"] and "overflow-wrap:anywhere" in rules[".td-how"]
    assert "flex-wrap:wrap" in rules[".td-foot"] and "min-width:0" in rules[".td-top"]
    assert "grid-template-columns:minmax(0,1fr)" in rules[".todo-list"]  # 폰은 한 줄 (두 칸은 1100px 이상에서만)
    assert "@media (min-width:1100px){.todo-list{grid-template-columns:repeat(2,minmax(0,1fr))}" in css
    assert "min-height:36px" in rules[".td-act"] and "min-height:40px" in rules["details.todo-more summary"]  # 누르기 좋은 크기
    todo_rules = {k: v for k, v in rules.items() if re.match(r"(\.todo|\.td|\.mk|details\.todo-more)", k)}
    for sel, body in todo_rules.items():
        for w in re.findall(r"(?:^|;)(?:min-)?width:(\d+)px", body):
            assert int(w) <= 358, (sel, w)
    # 표시 칩의 class 가 카드(.todo) 규칙에 걸리지 않게 (걸리면 칩에 카드 여백·테두리가 붙어 줄이 벌어짐)
    for _, _, cls in todo_mod.CHIPS.values():
        assert f".{cls}" not in rules and not any(sel.split(",")[0].strip() == f".{cls}" for sel in rules)
        assert cls.startswith("m-")
    add_item(db, 1, hours=2)
    write_export(tmp_path, [item("a", "가" * 30, "🔔[할 일] " + "나" * 120 + "\n→ " + "https://example.com/" + "x" * 100)])
    sec = todo_section(make_dash(db, tmp_path).page_html())
    assert 'class="mk m-todo"' in sec and 'class="mk todo"' not in sec


def test_crash_in_the_todo_list_does_not_break_the_page(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_export(tmp_path, [item("a", "할 일", "🔔[할 일] 할 일\n→ 하기")])
    dash = make_dash(db, tmp_path)

    def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("터짐")

    monkeypatch.setattr(todo_mod, "build", boom)
    page = dash.page_html()
    assert "할 일 목록을 만들지 못함 (RuntimeError: 터짐)" in todo_section(page) and 'id="card-links"' in page
    assert json.loads(dash.json_text())["todo"]["count"] == 0
    monkeypatch.undo()
    monkeypatch.setattr(render, "todo_html", boom)
    page = dash.page_html()
    assert "할 일 목록을 그리지 못함 (RuntimeError: 터짐)" in page and 'id="card-links"' in page


def test_db_read_failure_keeps_blogauto_items_as_sent(db: Database, tmp_path: Path) -> None:
    class BrokenDb:
        def __getattr__(self, name: str) -> Any:
            def fail(*a: Any, **k: Any) -> Any:
                raise RuntimeError("db locked")
            return fail

    export = parse_export({"written_at": WRITTEN, "blogs": {}, "todo": {"generated_at": WRITTEN, "items": [
        item("link:5", "핫딜 링크 #5", "🔔[할 일] 링크 필요 #5\n→ 텔레그램 핫딜봇 #5 에 답장", blog="dealbot", group="link_request"),
        item("review:6", "정품 확인 #6", "🔔[할 일] 정품 확인 #6\n→ /ok 6", blog="dealbot"),
    ]}}, "Asia/Seoul")
    view = todo_mod.build(export, BrokenDb(), NOW, ttl_hours=12, links_anchor="#card-links")
    assert [r.key for r in view.rows] == ["link:5", "review:6"] and view.hints == ["딜봇 쪽 할 일을 못 읽음"]


def test_no_network_during_page_and_json(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_sync(self: Any, *a: Any, **k: Any) -> Any:
        raise AssertionError("HTTP 요청이 있으면 안 됨")

    async def no_async(self: Any, *a: Any, **k: Any) -> Any:
        raise AssertionError("HTTP 요청이 있으면 안 됨")

    monkeypatch.setattr(httpx.Client, "send", no_sync)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_async)
    add_item(db, 1, hours=2)
    add_item(db, 2, status="awaiting_approval", hours=1)
    write_export(tmp_path, [item("buddy:finance", "신청 2개", "🔔[할 일] 신청 2개\n→ 수락",
                                 link="https://admin.blog.naver.com/derenlog/buddy/relation")])
    dash = make_dash(db, tmp_path)
    assert '<span class="todo-n">3</span>' in dash.page_html()
    assert json.loads(dash.json_text())["todo"]["count"] == 3
