"""채널 대시보드: 카드 목록·소스(가짜 API)·직접 입력·증감/그래프 계산·다시 읽기 한도·정해진 시각 읽기·DB 이전."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import dealbot.scheduler as sched
from dealbot.config import DashboardCardConfig, DashboardConfig, load_settings
from dealbot.coupang.client import ApiBudget, CoupangClient
from dealbot.dashboard import model
from dealbot.dashboard.blogauto import BlogAutoReader, parse_export
from dealbot.dashboard.model import GoalRow, chip_status, explain, safe_text
from dealbot.dashboard.render import step_path, step_points
from dealbot.dashboard.service import Dashboard
from dealbot.dashboard.sources import (
    SOURCE_TYPES,
    CardData,
    Source,
    SourceContext,
    load_all,
    register,
)
from dealbot.publisher import instagram as ig
from dealbot.publisher import threads as th
from dealbot.publisher.instagram import InstagramClient, InstagramPublisher
from dealbot.publisher.threads import ThreadsClient, ThreadsPublisher
from dealbot.storage.db import Database

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 14, 9, 5, tzinfo=UTC)  # 한국시간 18:05
TODAY = "2026-10-14"
YESTERDAY = "2026-10-13"


class Clock:
    def __init__(self, t: datetime = NOW) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class FakeTelegramBot:
    def __init__(self, count: int = 1234, error: Exception | None = None) -> None:
        self.count = count
        self.error = error
        self.calls: list[Any] = []

    async def get_chat_member_count(self, chat_id: Any) -> int:
        self.calls.append(chat_id)
        if self.error is not None:
            raise self.error
        return self.count


def cards(*items: dict[str, Any]) -> list[DashboardCardConfig]:
    return [DashboardCardConfig(**c) for c in items]


def make_dash(db: Database, tmp_path: Path, *, card_list: list[DashboardCardConfig] | None = None, clock: Clock | None = None,
              user: str | None = "owner", password: str | None = "plum-river-cactus-42", **ctx: Any) -> Dashboard:
    clock = clock or Clock()
    cfg = DashboardConfig(cards=card_list if card_list is not None else load_settings(ROOT / "config.yaml", load_env=False).dashboard.cards,
                          blogauto_metrics_path=str(tmp_path / "blogauto.json"))
    sctx = SourceContext(
        db=db, tz="Asia/Seoul", config=cfg, now=clock,
        blogauto=BlogAutoReader(cfg.blogauto_metrics_path, tz="Asia/Seoul", stale_hours=cfg.blogauto_stale_hours, now=clock),
        **ctx,
    )
    return Dashboard(cfg, sctx, user=user, password=password)


def card_of(dash: Dashboard, key: str) -> model.CardView:
    return next(v for v in dash.views() if v.key == key)


# ---------------------------------------------------------------- 카드 목록
def test_config_cards_are_listed_in_order_with_every_v1_source(db: Database, tmp_path: Path) -> None:
    s = load_settings(ROOT / "config.yaml", load_env=False)
    keys = [c.key for c in s.dashboard.cards]
    assert keys == ["telegram", "instagram", "threads", "blog_finance", "blog_issue", "blog_hotdeal", "kakao", "coupang", "dealbot"]
    assert {"telegram", "instagram", "threads", "coupang_partners", "dealbot", "naver_blog", "kakao_openchat"} <= set(load_all())
    dash = make_dash(db, tmp_path)
    assert [x.key for x in dash.sources] == keys and not dash.problems
    assert [v.key for v in dash.views()] == keys
    assert s.dashboard.blogauto_metrics_path == "/home/ubuntu/Blog-Auto/var/metrics/blogauto.json"
    assert all(c.options.get("open_date", "x") is None for c in s.dashboard.cards if c.type == "naver_blog")  # 주인이 채울 칸


def test_disabled_and_unknown_cards(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, card_list=cards(
        {"key": "kakao", "type": "kakao_openchat"},
        {"key": "off", "type": "telegram", "enabled": False},
        {"key": "tiktok", "type": "tiktok"},
        {"key": "dealbot", "type": "dealbot"},
    ))
    assert [s.key for s in dash.sources] == ["kakao", "dealbot"]
    lines = dash.self_check()
    # 대시보드는 덤이라 ⚠️(None) — ❌ 면 '딜봇 멈춤' 카톡이 감
    assert lines[0][0] is None and "모르는 종류 'tiktok'" in lines[0][1]
    assert lines[-1] == (True, "채널 대시보드 /dashboard (카드 2개, 비밀번호 보호)")


def test_new_sns_is_one_module_plus_one_config_line(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """새 SNS = @register 한 클래스 + config 한 줄 (다른 곳은 안 고침)."""
    calls: list[str] = []

    class TikTok(Source):
        type = "test_tiktok"
        kind = "api"
        default_title = "틱톡"

        async def fetch(self) -> model.FetchResult:
            calls.append("fetch")
            return model.FetchResult(model.OK, values={"followers": 2})

        def card(self, data: CardData) -> model.CardView:
            return self.view(data, stats=[self.stat_with_delta(data, "followers", "팔로워")],
                             rows=[GoalRow("팔로워 수", data.latest_value("followers"), self.goal("followers") or 10000, "명")])

    monkeypatch.setitem(SOURCE_TYPES, "test_tiktok", TikTok)
    register(TikTok)
    dash = make_dash(db, tmp_path, card_list=cards({"key": "tiktok", "type": "test_tiktok", "goals": {"followers": 10000}},
                                                   {"key": "kakao", "type": "kakao_openchat"}))
    assert [s.key for s in dash.sources] == ["tiktok", "kakao"]
    asyncio.run(dash.tick())
    v = card_of(dash, "tiktok")
    assert calls == ["fetch"] and v.title == "틱톡" and v.rows[0].value == 2 and v.status == model.UNMET


# ---------------------------------------------------------------- DB
def test_metrics_table_migrates_onto_an_existing_production_db(tmp_path: Path) -> None:
    path = tmp_path / "dealbot.db"
    old = Database(path)
    old.kv_set("instagram_user_id", "178")
    old.close()
    con = sqlite3.connect(path)  # 대시보드 이전의 DB: metrics 표가 없다
    con.execute("DROP TABLE metrics")
    con.execute("INSERT INTO posts (product_id, source, price, dry_run, posted_at) VALUES ('p1', 's', 1000, 0, '2026-10-14T01:00:00+00:00')")
    con.commit()
    assert "metrics" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()

    db = Database(path)
    try:
        cols = [r["name"] for r in db._q("PRAGMA table_info(metrics)")]
        assert cols == ["source", "metric", "day", "value", "read_at", "origin"]
        assert db.kv_get("instagram_user_id") == "178" and db._one("SELECT COUNT(*) AS c FROM posts")["c"] == 1
        db.metric_put("telegram", "subscribers", TODAY, 10)
    finally:
        db.close()
    db = Database(path)  # 다시 열어도 그대로
    try:
        assert db.metric_latest("telegram", "subscribers")["value"] == 10
    finally:
        db.close()


def test_one_value_per_source_metric_day_latest_wins(db: Database) -> None:
    db.metric_put("blog", "neighbors", TODAY, 10, origin="manual", now=NOW)
    db.metric_put("blog", "neighbors", TODAY, 12, origin="manual", now=NOW + timedelta(minutes=5))
    db.metric_put("blog", "neighbors", YESTERDAY, 9, origin="auto", now=NOW)
    assert db.metric_series("blog", "neighbors", "2026-10-01", TODAY) == [(YESTERDAY, 9.0), (TODAY, 12.0)]
    latest = db.metric_latest("blog", "neighbors")
    assert latest["value"] == 12 and latest["origin"] == "manual"
    assert db.metric_latest("blog", "neighbors", until_day=YESTERDAY)["value"] == 9
    assert db.metric_last_read("blog", origin="manual") == NOW + timedelta(minutes=5)
    assert db.metric_last_read("nothing") is None


# ---------------------------------------------------------------- 계산
def test_delta_and_sparkline_math(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, card_list=cards({"key": "kakao", "type": "kakao_openchat"}))
    ctx = dash.ctx
    for day, v in (("2026-10-01", 40), ("2026-10-12", 50), (YESTERDAY, 52), (TODAY, 57)):
        db.metric_put("kakao", "members", day, v)
    data = CardData(ctx, "kakao")
    assert data.delta("members") == (5.0, "오늘")
    assert data.today_delta("members") == 5.0
    assert data.window_avg("members", 3) == pytest.approx((50 + 52 + 57) / 3)
    assert data.month_sum("members") == 40 + 50 + 52 + 57
    db.metric_put("kakao", "members", YESTERDAY, 52)
    # 어제 값이 없으면: 증감은 그 앞 값과 (날짜 표시), '오늘 늘어난' 합계에는 안 넣음
    con = db._conn
    con.execute("DELETE FROM metrics WHERE day = ?", (YESTERDAY,))
    data = CardData(ctx, "kakao")
    assert data.delta("members") == (7.0, "10. 12. 대비")
    assert data.today_delta("members") is None
    con.execute("DELETE FROM metrics WHERE day = '2026-10-12'")
    assert CardData(ctx, "kakao").delta("members") is None  # 13일이나 떨어진 값과는 비교 안 함

    pts = step_points([("2026-09-01", 1), ("2026-10-13", 2), (TODAY, 5)], days=28, today=date(2026, 10, 14))
    assert pts == [(26, 2.0), (27, 5.0)]  # 기간 밖(9/1)은 뺌
    # 첫 값은 왼쪽 끝부터, 바뀐 날은 전날→그날을 비스듬히 (참고 화면처럼)
    assert step_path(pts, days=28, width=240, height=46, pad=3) == "M3.0 43.0 L228.3 43.0 L237.0 3.0"
    assert step_path([(0, 3.0)], days=28) == "M3.0 23.0 L237.0 23.0"  # 값이 하나면 가운데 높이로 끝까지 평평하게
    assert step_path([(27, 3.0)], days=28) == "M3.0 23.0 L237.0 23.0"  # 오늘 값 하나도 짧은 토막이 아니라 가로 전체
    assert step_path([], days=28) == ""
    flat = step_path([(0, 4.0), (5, 4.0)], days=28)
    assert flat == "M3.0 23.0 L237.0 23.0"
    steps = step_path([(0, 1.0), (3, 1.0), (4, 2.0), (10, 3.0)], days=11, width=106, height=23, pad=3)
    assert steps == "M3.0 20.0 L33.0 20.0 L43.0 11.5 L93.0 11.5 L103.0 3.0"  # 값이 그대로면 수평, 바뀐 날만 비스듬히


def test_chip_rules_and_explanation() -> None:
    met = GoalRow("구독자 수", 1200, 1000, "명")
    unmet = GoalRow("구독자 수", 7, 1000, "명")
    unknown = GoalRow("방문자 수", None, 100, "명")
    assert chip_status([met]) == model.MET
    assert chip_status([unmet, unknown]) == model.UNMET  # 아는 줄이 못 미치면 미충족 (참고 화면 틱톡처럼)
    assert chip_status([met, unknown]) == model.UNKNOWN
    assert chip_status([], model.ERROR) == model.UNKNOWN and chip_status([], model.OK) == model.NO_CHIP
    assert explain("YouTube", [unmet]) == "구독자 수가 7명으로 목표 1,000명까지 993명 남았습니다."
    assert "확인할 수 없어 판단할 수 없습니다" in explain("네이버 애드포스트", [unknown])
    assert explain(None, [met]) == "목표를 모두 채웠습니다."
    low = GoalRow("링크 기다리는 딜", 5, 3, "개", lower_is_better=True)
    assert low.met is False and "기준(3개 이하)보다 많습니다" in explain(None, [low])
    assert GoalRow("x", 2, 3, lower_is_better=True).met is True
    assert safe_text("bad access_token=EAAG12345 and 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw x") == \
        "bad access_token=*** and *** x"


# ---------------------------------------------------------------- 텔레그램
async def test_telegram_subscribers_and_channel_posts(db: Database, tmp_path: Path) -> None:
    from dealbot.cli import sample_deal

    tg = FakeTelegramBot(1234)
    dash = make_dash(db, tmp_path, card_list=cards({"key": "telegram", "type": "telegram", "goals": {"subscribers": 1000}}),
                     telegram_bot=lambda: tg, channel_id="@hot_deal_and_info")
    db.metric_put("telegram", "subscribers", YESTERDAY, 1200)
    deal = sample_deal()
    db.record_post(deal, channel_id="@c", message_id=1, now=NOW - timedelta(hours=1))
    db.record_post(deal, channel_id="@c", message_id=2, now=NOW - timedelta(days=3))
    db.record_post(deal, channel_id="@c", message_id=3, dry_run=True, now=NOW)  # 연습은 안 셈
    assert await dash.refresh("telegram") == "refreshed"
    assert tg.calls == ["@hot_deal_and_info"]
    v = card_of(dash, "telegram")
    assert v.stats[0].value == 1234 and v.stats[0].delta == 34 and v.stats[0].delta_label == "오늘"
    assert v.stats[1].value == 1 and v.stats[1].sub == "최근 7일 2개"
    assert v.status == model.MET and v.rows[0].met and v.summary == {"audience_delta": 34.0}
    assert v.read_at == NOW


async def test_telegram_error_and_missing_bot_never_crash(db: Database, tmp_path: Path) -> None:
    tg = FakeTelegramBot(error=RuntimeError("Timed out https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/x"))
    dash = make_dash(db, tmp_path, card_list=cards({"key": "telegram", "type": "telegram"}),
                     telegram_bot=lambda: tg, channel_id="@c")
    assert await dash.refresh("telegram") == "failed"
    v = card_of(dash, "telegram")
    assert v.status == model.UNKNOWN and v.stats[0].value is None
    assert any("구독자 수를 못 읽음" in h for h in v.hints)
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in json.dumps(dash.status("telegram"))  # 토큰 모양은 가림
    dash2 = make_dash(db, tmp_path, card_list=cards({"key": "tg2", "type": "telegram"}))
    res = await dash2.collect_one(dash2.sources[0])
    assert res.status == model.UNAVAILABLE and "연결되지 않아" in (res.note or "")


# ---------------------------------------------------------------- 인스타그램
def _ig(db: Database, handler: Any) -> InstagramPublisher:
    db.kv_set(ig.KV_TOKEN, "IG-SECRET-TOKEN")
    db.kv_set(ig.KV_USER_ID, "17841")
    db.kv_set(ig.KV_TOKEN_EXPIRES, (NOW + timedelta(days=50)).isoformat())
    client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="A", app_secret="S",
                             max_retries=2, retry_backoff=0.01)
    return InstagramPublisher(client, db, None, enabled=True)  # type: ignore[arg-type]


async def test_instagram_followers_and_views(db: Database, tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "graph.instagram.com"  # 공식 Graph API 만
        seen.append(req.url.path)
        if req.url.path.endswith("/me"):
            assert "followers_count" in req.url.params["fields"]
            return httpx.Response(200, json={"user_id": "17841", "username": "oneul_hot_deal", "followers_count": 321, "media_count": 45})
        if req.url.path.endswith("/17841/insights"):
            assert req.url.params["metric"] == "views,reach" and req.url.params["metric_type"] == "total_value"
            return httpx.Response(200, json={"data": [{"name": "views", "total_value": {"value": 6458}},
                                                      {"name": "reach", "total_value": {"value": 900}}]})
        return httpx.Response(404, json={})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "instagram", "type": "instagram", "goals": {"followers": 1000, "posts": 40}}),
                     instagram=_ig(db, handler))
    assert await dash.refresh("instagram") == "refreshed"
    v = card_of(dash, "instagram")
    assert [s.value for s in v.stats] == [321, 6458] and v.stats[1].sub == "도달 900명"
    assert [(r.label, r.value, r.met) for r in v.rows] == [("팔로워 수", 321, False), ("게시물 수", 45, True)]
    assert v.status == model.UNMET and not v.hints
    assert seen == ["/v25.0/me", "/v25.0/17841/insights"]


async def test_instagram_without_insights_permission_shows_permission_needed(db: Database, tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"followers_count": 12, "media_count": 3})
        return httpx.Response(400, json={"error": {"message": "(#10) Application does not have permission for this action", "code": 10}})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "instagram", "type": "instagram"}), instagram=_ig(db, handler))
    assert await dash.refresh("instagram") == "refreshed"  # 팔로워는 읽음
    v = card_of(dash, "instagram")
    assert v.stats[0].value == 12 and v.stats[1].value is None and v.stats[1].missing == "권한 필요"
    assert any("instagram_business_manage_insights" in h for h in v.hints)
    assert dash.status("instagram")["status"] == model.PARTIAL


async def test_instagram_token_problem_and_network_error(db: Database, tmp_path: Path) -> None:
    def expired(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Error validating access token: Session has expired", "code": 190}})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "instagram", "type": "instagram"}), instagram=_ig(db, expired))
    assert await dash.refresh("instagram") == "failed"
    v = card_of(dash, "instagram")
    assert v.status == model.UNKNOWN and any("/instatoken" in h for h in v.hints)

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    dash = make_dash(db, tmp_path, card_list=cards({"key": "ig2", "type": "instagram"}), instagram=_ig(db, down))
    res = await dash.collect_one(dash.sources[0])
    assert res.status == model.ERROR and "ConnectError" in (res.note or "")
    db.kv_set(ig.KV_TOKEN, "")
    res = await dash.collect_one(dash.sources[0])
    assert res.status == model.UNAVAILABLE and "/instatoken" in (res.note or "")


# ---------------------------------------------------------------- 스레드
def _th(db: Database, handler: Any) -> ThreadsPublisher:
    db.kv_set(th.KV_TOKEN, "TH-SECRET-TOKEN")
    db.kv_set(th.KV_USER_ID, "42")
    db.kv_set(th.KV_TOKEN_EXPIRES, (NOW + timedelta(days=50)).isoformat())
    client = ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="A", app_secret="S",
                           max_retries=2, retry_backoff=0.01)
    return ThreadsPublisher(client, db, None, enabled=True)  # type: ignore[arg-type]


async def test_threads_insights_happy_permission_and_network(db: Database, tmp_path: Path) -> None:
    asked: list[dict[str, str]] = []

    def ok(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "graph.threads.net" and req.url.path == "/v1.0/42/threads_insights"
        asked.append({k: v for k, v in req.url.params.items() if k != "access_token"})
        if req.url.params["metric"] == "followers_count":
            return httpx.Response(200, json={"data": [{"name": "followers_count", "total_value": {"value": 77}}]})
        return httpx.Response(200, json={"data": [
            {"name": "views", "period": "day", "values": [{"value": 10}, {"value": 15}, {"value": 5}]},
        ]})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "threads", "type": "threads", "goals": {"followers": 50}}),
                     threads=_th(db, ok))
    assert await dash.refresh("threads") == "refreshed"
    v = card_of(dash, "threads")
    assert [s.value for s in v.stats] == [77, 30] and v.status == model.MET
    assert v.stats[1].label == "프로필 조회 · 최근 28일"  # 사용자 단위 views 는 프로필 조회수 (글 조회수가 아님)
    assert asked[0] == {"metric": "followers_count"}  # 팔로워는 기간 없이
    assert asked[1]["metric"] == "views" and int(asked[1]["until"]) - int(asked[1]["since"]) == 28 * 86400

    def views_down(req: httpx.Request) -> httpx.Response:
        if req.url.params["metric"] == "followers_count":
            return httpx.Response(200, json={"data": [{"name": "followers_count", "total_value": {"value": 80}}]})
        return httpx.Response(400, json={"error": {"message": "Invalid parameter", "code": 100}})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "th1", "type": "threads"}), threads=_th(db, views_down))
    assert await dash.refresh("th1") == "refreshed"  # 팔로워만이라도
    v = card_of(dash, "th1")
    assert v.stats[0].value == 80 and v.stats[1].value is None and any("프로필 조회를 못 읽음" in h for h in v.hints)

    def no_scope(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Application does not have permission for this action", "code": 10}})

    dash = make_dash(db, tmp_path, card_list=cards({"key": "th2", "type": "threads"}), threads=_th(db, no_scope))
    assert await dash.refresh("th2") == "failed"
    v = card_of(dash, "th2")
    assert v.status == model.UNKNOWN and v.stats[0].missing == "권한 필요"
    assert any("threads_manage_insights" in h for h in v.hints)

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    dash = make_dash(db, tmp_path, card_list=cards({"key": "th3", "type": "threads"}), threads=_th(db, down))
    res = await dash.collect_one(dash.sources[0])
    assert res.status == model.ERROR and "ReadTimeout" in (res.note or "")


# ---------------------------------------------------------------- 쿠팡 파트너스
def _coupang(handler: Any, budget: ApiBudget | None = None) -> CoupangClient:
    return CoupangClient("AK", "SK", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=1,
                         retry_backoff=0.01, budget=budget)


async def test_coupang_reports_respect_daily_cap_and_deeplink_reserve(db: Database, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        calls.append(name)
        assert req.url.path.startswith("/v2/providers/affiliate_open_api/apis/openapi/v1/reports/")
        assert req.headers["Authorization"].startswith("CEA algorithm=HmacSHA256")
        assert req.url.params["startDate"] == "20261001" and req.url.params["endDate"] == "20261013"  # 어제까지
        rows = {
            "clicks": [{"date": "20261013", "click": 30}, {"date": "20261014", "click": 5}, {"date": "20261013", "click": 2}],
            "orders": [{"date": "20261013", "orderId": "A", "commission": 300}, {"date": "20261013", "orderId": "A"},
                       {"date": "20261014", "orderId": "B"}],
            "commission": [{"date": "20261013", "commission": 1500}, {"date": "20261009", "commission": "2,000"}],
        }[name]
        return httpx.Response(200, json={"rCode": "0", "rMessage": "", "data": rows})

    budget = ApiBudget(10, reserve={"deeplink": 3}, caps={"deeplink": 7})
    dash = make_dash(db, tmp_path, card_list=cards({"key": "coupang", "type": "coupang_partners",
                                                    "goals": {"commission_month": 10000},
                                                    "options": {"max_calls_per_day": 6, "min_interval_hours": 3}}),
                     coupang=_coupang(handler, budget), budget=budget)
    assert await dash.refresh("coupang") == "refreshed"
    assert calls == ["clicks", "orders", "commission"]
    assert budget.usage() == {"report": 3} and budget.available("deeplink")  # 딥링크 예약 몫은 그대로
    v = card_of(dash, "coupang")
    # 하루치는 다음 날 모이므로 어제까지만: 오늘 행(클릭 5·주문 B)은 응답에 있어도 쓰지 않고, 오늘을 0 으로 채우지도 않음
    assert v.stats[0].value == 32 and v.stats[0].sub == "어제까지"
    assert v.stats[1].value == 3500 and v.stats[1].sub == "주문 1건 · 어제까지"
    assert v.rows[0].label == "이번 달 수익 (어제까지)" and v.rows[0].value == 3500 and v.status == model.UNMET
    assert v.activity == ["어제 클릭 32회 · 주문 1건 · 수익 1,500원"]
    assert db.metric_series("coupang", "clicks", "2026-10-12", TODAY) == [("2026-10-12", 0.0), (YESTERDAY, 32.0)]
    assert v.summary == {"revenue_7d": 3500.0, "orders_7d": 1.0}
    assert v.spark[-1] == (YESTERDAY, 3500.0) and v.spark_label == "수익 7일 합계"  # 하루 수익 대신 7일 합계

    dash.ctx.now = Clock(NOW + timedelta(hours=2))  # 간격(3시간) 안: 조용히 건너뜀
    assert await dash.refresh("coupang") == "skipped" and len(calls) == 3
    assert not card_of(dash, "coupang").hints and dash.status("coupang")["status"] == model.OK
    dash.ctx.now = Clock(NOW + timedelta(hours=4))  # 두 번째 읽기: 오늘 6회 → 끝
    assert await dash.refresh("coupang") == "refreshed" and len(calls) == 6
    dash.ctx.now = Clock(NOW + timedelta(hours=5, minutes=30))  # 같은 날 23:35
    assert await dash.refresh("coupang") == "skipped" and len(calls) == 6  # 하루 한도
    v = card_of(dash, "coupang")
    assert v.stats[1].value == 3500 and any("한도(6회)" in h for h in v.hints)  # 지난 값은 그대로

    # 시간당 예산의 일반 몫이 모자라면 부르지 않는다 (딥링크 몫을 건드리지 않게)
    tight = ApiBudget(10, reserve={"deeplink": 3})
    for _ in range(5):
        tight.record("goldbox")
    dash2 = make_dash(db, tmp_path, card_list=cards({"key": "cp2", "type": "coupang_partners"}),
                      coupang=_coupang(handler, tight), budget=tight)
    res = await dash2.collect_one(dash2.sources[0])
    assert res.status == model.SKIPPED and len(calls) == 6


async def test_coupang_report_endpoint_unavailable_or_down(db: Database, tmp_path: Path) -> None:
    calls: list[str] = []

    def missing(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return httpx.Response(404, text="Not Found")

    dash = make_dash(db, tmp_path, card_list=cards({"key": "coupang", "type": "coupang_partners"}), coupang=_coupang(missing))
    assert await dash.refresh("coupang") == "failed"
    assert len(calls) == 1  # 리포트 API 가 없으면 나머지는 부르지 않음
    v = card_of(dash, "coupang")
    assert v.status == model.UNKNOWN and any("쿠팡 실적 API 를 못 읽음" in h for h in v.hints)

    def down(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    dash = make_dash(db, tmp_path, card_list=cards({"key": "cp3", "type": "coupang_partners"}), coupang=_coupang(down))
    res = await dash.collect_one(dash.sources[0])
    assert res.status == model.ERROR
    dash = make_dash(db, tmp_path, card_list=cards({"key": "cp4", "type": "coupang_partners"}))
    assert (await dash.collect_one(dash.sources[0])).status == model.UNAVAILABLE


# ---------------------------------------------------------------- 봇 운영
async def test_dealbot_operations_card(db: Database, tmp_path: Path) -> None:
    from dataclasses import replace

    from dealbot.cli import sample_deal

    dash = make_dash(db, tmp_path, card_list=cards({"key": "dealbot", "type": "dealbot",
                                                    "goals": {"deals_per_day": 2, "links_waiting_max": 0}}))
    base = sample_deal()
    for i, (status, kind) in enumerate([("published", "hotdeal"), ("published", "info"), ("awaiting_link", "hotdeal"),
                                        ("pending", "hotdeal")]):
        deal = replace(base, product=replace(base.product, product_id=f"p{i}", deal_kind=kind))
        assert db.enqueue(deal, score=10, now=NOW - timedelta(minutes=30))
        db.update_queue_item(i + 1, status=status, deal=deal, now=NOW - timedelta(minutes=20))
        if status == "published":
            db.record_post(deal, channel_id="@c", message_id=i, now=NOW - timedelta(minutes=20))
    v = card_of(dash, "dealbot")
    assert v.stats[0].value == 1  # 정보 글은 '딜' 에 안 셈
    assert v.stats[1].value == 1
    assert [(r.label, r.value, r.met) for r in v.rows] == [("오늘 올린 딜", 1, False), ("링크 기다리는 딜", 1, False)]
    assert v.activity == ["대기열: 발행 대기 1 · 내 링크 기다림 1 · 확인(/ok) 기다림 0 · 자동 링크 중 0"]
    # 그래프는 하루 딜 수의 '최근 7일 합계' — 다시 센 29일(딜 기록을 지우는 30일 안) 가운데 7일이 다 찬 날부터
    assert v.spark[-1] == (TODAY, 1.0) and len(v.spark) == 23 and v.spark_label == "올린 딜 7일 합계"
    assert v.rows[1].lower_is_better and v.rows[1].ratio == 1.0  # 넘친 줄은 꽉 찬 주황 막대 (render)


# ---------------------------------------------------------------- 네이버 블로그 (직접 입력 + Blog-Auto 기록)
def _write_export(tmp_path: Path, written_at: str, blogs: Any) -> None:
    (tmp_path / "blogauto.json").write_text(json.dumps({"written_at": written_at, "blogs": blogs}), encoding="utf-8")


def test_blog_card_reads_blogauto_export_and_manual_entries(db: Database, tmp_path: Path) -> None:
    _write_export(tmp_path, "2026-10-14T18:00:00+09:00", {
        "finance": {"posts_published_today": 1, "posts_published_total": 55, "drafts_saved_today": 2,
                    "neighbor_requests_today": 10, "neighbor_requests_7d": 55, "comments_today": 3, "replies_today": 2},
        "issue": {"posts": {"published_today": 0, "published_total": 7}, "neighbors": {"requests_today": 4}},
    })
    dash = make_dash(db, tmp_path, card_list=cards(
        {"key": "blog_finance", "type": "naver_blog", "title": "Deren의 아카이브",
         "goals": {"open_days": 90, "posts": 50, "avg_visitors": 100}, "options": {"blog": "finance", "open_date": "2026-07-01"}},
        {"key": "blog_issue", "type": "naver_blog", "options": {"blog": "issue"}},
    ))
    for day, v in (("2026-10-08", 120), ("2026-10-10", 90), (YESTERDAY, 120)):
        db.metric_put("blog_finance", "visitors", day, v, origin="manual")
    db.metric_put("blog_finance", "neighbors", YESTERDAY, 120, origin="manual")
    db.metric_put("blog_finance", "neighbors", TODAY, 127, origin="manual")
    v = card_of(dash, "blog_finance")
    assert v.stats[0].value == 127 and v.stats[0].delta == 7 and v.stats[0].delta_label == "오늘"
    assert v.stats[1].label == "방문자 · 10.\u00a013." and v.stats[1].value == 120  # 짧게, 날짜 가운데서 줄이 안 바뀌게
    assert [(r.label, r.value, r.met) for r in v.rows] == [("개설 기간", 105, True), ("게시글 수", 55, True), ("일평균 방문자", 110, True)]
    assert v.status == model.MET and v.program == "네이버 애드포스트"
    assert v.activity == ["오늘 발행 1 · 임시저장 2 · 서로이웃 신청 10 (7일 55) · 댓글 3 · 답글 2"]
    assert v.summary == {"audience_delta": 7.0, "posts_today": 1.0, "visitors_yesterday": 120.0}
    assert db.metric_latest("blog_finance", "posts_total")["value"] == 55  # 기록 파일 값도 날짜별로 쌓임
    w = card_of(dash, "blog_issue")  # 중첩 모양도 읽힘
    assert w.rows[1].value == 7 and "서로이웃 신청 4" in w.activity[0]
    assert w.status == model.UNMET  # 게시글 7/50 (개설일·방문자는 모름)


def test_blog_card_when_export_missing_stale_or_broken(db: Database, tmp_path: Path) -> None:
    card = cards({"key": "blog", "type": "naver_blog", "options": {"blog": "finance"}})
    dash = make_dash(db, tmp_path, card_list=card)
    v = card_of(dash, "blog")
    assert v.status == model.UNKNOWN and not v.activity
    assert any("기록 파일이 아직 없음" in h for h in v.hints)
    assert "판단할 수 없습니다" in v.note

    _write_export(tmp_path, "2026-10-14T10:00:00+09:00", {"finance": {"posts_published_total": 80}})  # 8시간 전
    dash = make_dash(db, tmp_path, card_list=card)
    v = card_of(dash, "blog")
    assert v.status == model.UNKNOWN and v.rows[1].value is None
    assert any("8시간 전 것" in h for h in v.hints)

    (tmp_path / "blogauto.json").write_text("{not json", encoding="utf-8")
    dash = make_dash(db, tmp_path, card_list=card)
    assert any("읽지 못함" in h for h in card_of(dash, "blog").hints)

    _write_export(tmp_path, "2026-10-14T18:00:00+09:00", {"hotdeal": {"posts_published_total": 1}})
    dash = make_dash(db, tmp_path, card_list=card)
    assert any("이 블로그가 없음" in h for h in card_of(dash, "blog").hints)


def test_blogauto_export_parsing_is_tolerant() -> None:
    ex = parse_export({"generated_at": "2026-10-14T09:00:00Z", "blogs": [
        {"name": "finance", "blog_id": "derenlog", "published_today": "2", "total_posts": 30, "comments_today": True,
         "neighbor_requests_sent_7d": -1},
    ]}, "Asia/Seoul")
    assert ex.ok and ex.written_at == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    assert ex.blog("derenlog") == {"posts_today": 2.0, "posts_total": 30.0}  # 참/거짓·음수는 숫자로 안 봄
    naive = parse_export({"written_at": "2026-10-14T18:00:00", "blogs": {}}, "Asia/Seoul")
    assert naive.written_at is not None and naive.written_at.utcoffset() == timedelta(hours=9)
    assert parse_export({"blogs": {}}, "Asia/Seoul").status == model.UNAVAILABLE
    assert parse_export([1, 2], "Asia/Seoul").status == model.UNAVAILABLE


# ---------------------------------------------------------------- 직접 입력
def test_manual_input_validation(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, card_list=cards(
        {"key": "blog", "type": "naver_blog", "options": {"blog": "finance"}},
        {"key": "kakao", "type": "kakao_openchat"},
        {"key": "telegram", "type": "telegram"},
    ))
    save = dash.save_manual
    for bad in ("12a", "-5", "1.5", "1e3", "１２", "1,23", "12,3456", "0x10", "1" * 14, "99999999999"):
        assert save("kakao", {"members": bad}) == "bad_number", bad
    assert save("kakao", {"members": "50", "date": "2026-10-15"}) == "bad_date"  # 내일
    assert save("kakao", {"members": "50", "date": "2026-08-01"}) == "bad_date"  # 60일보다 전
    assert save("kakao", {"members": "50", "date": "2026-13-01"}) == "bad_date"
    assert save("kakao", {"members": "50", "date": "14/10/2026"}) == "bad_date"
    assert save("kakao", {"members": " "}) == "empty"
    assert save("nope", {"members": "1"}) == "unknown"
    assert save("telegram", {"subscribers": "1"}) == "not_manual"  # API 로 읽는 값은 손으로 못 바꿈
    assert db.metric_latest("kakao", "members") is None
    # 하나라도 틀리면 아무것도 저장 안 함
    assert save("blog", {"neighbors": "100", "visitors": "abc"}) == "bad_number"
    assert db.metric_latest("blog", "neighbors") is None

    assert save("kakao", {"members": "1,234"}) == "saved"
    assert db.metric_latest("kakao", "members") == {"day": TODAY, "value": 1234.0, "read_at": NOW.isoformat(), "origin": "manual"}
    assert save("blog", {"neighbors": "130", "visitors": "45", "date": "2026-10-12", "subscribers": "999"}) == "saved"
    assert db.metric_series("blog", "neighbors", "2026-10-01", TODAY) == [("2026-10-12", 130.0)]
    assert db.metric_series("blog", "visitors", "2026-10-01", TODAY) == [("2026-10-11", 45.0)]  # '어제 방문자' 는 전날로
    assert db.metric_latest("blog", "subscribers") is None  # 카드에 없는 이름은 무시
    assert save("blog", {"visitors": "40"}) == "saved"
    assert db.metric_latest("blog", "visitors")["day"] == YESTERDAY


# ---------------------------------------------------------------- 다시 읽기·정해진 시각
async def test_refresh_is_rate_limited_per_card(db: Database, tmp_path: Path) -> None:
    tg = FakeTelegramBot(10)
    clock = Clock()
    dash = make_dash(db, tmp_path, clock=clock, card_list=cards(
        {"key": "telegram", "type": "telegram"}, {"key": "blog", "type": "naver_blog"}, {"key": "kakao", "type": "kakao_openchat"}),
        telegram_bot=lambda: tg, channel_id="@c")
    assert await dash.refresh("telegram") == "refreshed"
    assert await dash.refresh("telegram") == "too_soon"
    clock.t += timedelta(minutes=9)
    assert await dash.refresh("telegram") == "too_soon"
    assert len(tg.calls) == 1
    clock.t += timedelta(minutes=2)
    assert await dash.refresh("telegram") == "refreshed" and len(tg.calls) == 2
    assert await dash.refresh("blog") == "reread"  # 로컬 파일은 한도 없이 다시 읽음
    assert await dash.refresh("nope") == "unknown"
    # 같은 카드를 동시에 눌러도 한 번만 읽는다
    clock.t += timedelta(minutes=11)
    results = await asyncio.gather(dash.refresh("telegram"), dash.refresh("telegram"))
    assert sorted(results) == ["refreshed", "too_soon"] and len(tg.calls) == 3


async def test_tick_reads_api_cards_once_per_collect_time(db: Database, tmp_path: Path) -> None:
    tg = FakeTelegramBot(10)
    clock = Clock(datetime(2026, 10, 14, 9, 5, tzinfo=UTC))  # 18:05 → 18:00 차례
    dash = make_dash(db, tmp_path, clock=clock, card_list=cards({"key": "telegram", "type": "telegram"},
                                                                {"key": "dealbot", "type": "dealbot"}),
                     telegram_bot=lambda: tg, channel_id="@c")
    assert dash.current_slot(datetime(2026, 10, 14, 8, 59)) == "2026-10-13 22:00"
    assert dash.current_slot(datetime(2026, 10, 14, 9, 0)) == "2026-10-14 09:00"
    assert await dash.tick() == ["telegram", "telegram:live", "dealbot:live"]
    assert await dash.tick() == []  # 같은 차례, 로컬 기록도 아직 한 시간 안
    clock.t += timedelta(minutes=61)
    assert await dash.tick() == ["telegram:live", "dealbot:live"]
    clock.t = datetime(2026, 10, 14, 13, 1, tzinfo=UTC)  # 22:01
    assert "telegram" in await dash.tick()
    clock.t = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)  # 다음 날 08:00 — 아직 어제 22시 차례
    assert "telegram" not in await dash.tick()
    assert len(tg.calls) == 2
    # 재시작해도(새 Dashboard) 같은 차례는 다시 안 읽음
    dash2 = make_dash(db, tmp_path, clock=clock, card_list=cards({"key": "telegram", "type": "telegram"}),
                      telegram_bot=lambda: tg, channel_id="@c")
    assert "telegram" not in await dash2.tick() and len(tg.calls) == 2


async def test_dashboard_loop_is_a_noop_without_a_dashboard() -> None:
    stop = asyncio.Event()
    await asyncio.wait_for(sched.dashboard_loop(SimpleNamespace(), stop), 1)  # type: ignore[arg-type]
    await asyncio.wait_for(sched.dashboard_loop(SimpleNamespace(dashboard=SimpleNamespace(enabled=False)), stop), 1)  # type: ignore[arg-type]


async def test_one_broken_source_does_not_break_the_page(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard.sources.kakao import KakaoOpenChatSource

    def boom(self: Any, data: CardData) -> Any:
        raise ValueError("bad card")

    monkeypatch.setattr(KakaoOpenChatSource, "card", boom)
    dash = make_dash(db, tmp_path, card_list=cards({"key": "kakao", "type": "kakao_openchat"}, {"key": "dealbot", "type": "dealbot"}))
    html_text = dash.page_html()
    assert "카드를 그리지 못함" in html_text and "핫딜 봇 운영" in html_text


# ---------------------------------------------------------------- 쿠팡: 실패하면 더 두드리지 않기
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_coupang_throttled_or_down_makes_exactly_one_http_call(db: Database, tmp_path: Path, status: int) -> None:
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(req.url.path)
        return httpx.Response(status, text="Too Many Requests" if status == 429 else "oops")

    budget = ApiBudget(10, reserve={"deeplink": 3})
    client = CoupangClient("AK", "SK", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=3,
                           retry_backoff=0.01, budget=budget)  # 발행용 기본 재시도(3번)가 있어도 리포트는 한 번만
    dash = make_dash(db, tmp_path, card_list=cards({"key": "coupang", "type": "coupang_partners"}), coupang=client, budget=budget)
    res = await dash.collect_one(dash.sources[0])
    assert res.status == model.ERROR and "다음 시각에 다시" in (res.note or "")
    assert len(hits) == 1 and budget.used() == 1  # 실제 호출 수 = 예산에 센 수
    assert db.kv_get(f"dash:coupang_calls:coupang:{TODAY}") == "1"  # 하루 한도에도 1번
    assert card_of(dash, "coupang").status == model.UNKNOWN


async def test_coupang_later_report_failing_stops_the_rest(db: Database, tmp_path: Path) -> None:
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        name = req.url.path.rsplit("/", 1)[-1]
        hits.append(name)
        if name == "clicks":
            return httpx.Response(200, json={"rCode": "0", "data": [{"date": "20261013", "click": 4}]})
        return httpx.Response(429, text="slow down")

    dash = make_dash(db, tmp_path, card_list=cards({"key": "coupang", "type": "coupang_partners"}), coupang=_coupang(handler))
    res = await dash.collect_one(dash.sources[0])
    assert hits == ["clicks", "orders"]  # 429 를 받으면 수익 리포트는 부르지 않음
    assert res.status == model.PARTIAL and db.metric_latest("coupang", "clicks")["value"] == 4


def test_coupang_report_client_is_single_attempt_by_default() -> None:
    hits: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(502)

    client = CoupangClient("AK", "SK", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=3, retry_backoff=0.01)
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(client.report("clicks", "20261001", "20261013"))
    assert len(hits) == 1
    with pytest.raises(Exception):  # noqa: B017
        asyncio.run(client.report("clicks", "20261001", "20261013", attempts=2))
    assert len(hits) == 3


# ---------------------------------------------------------------- 무한·NaN 값이 페이지를 깨지 않게
async def test_non_finite_values_are_dropped_and_never_break_the_page(db: Database, tmp_path: Path) -> None:
    from dealbot.dashboard.sources._meta import insight_value
    from dealbot.dashboard.sources.coupang import parse_report

    per_day, _ = parse_report("commission", [{"date": "20261013", "commission": "Infinity"},
                                             {"date": "20261013", "commission": "1e400"},
                                             {"date": "20261013", "commission": "NaN"},
                                             {"date": "20261013", "commission": 100}], date(2026, 10, 14))
    assert per_day == {YESTERDAY: 100.0}
    body = json.loads('{"data": [{"name": "views", "total_value": {"value": 1e400}}, '
                      '{"name": "reach", "values": [{"value": NaN}, {"value": 7}]}]}')
    assert insight_value(body, "views") is None and insight_value(body, "reach") == 7.0
    assert model.fmt_num(float("inf")) == "-" and model.fmt_signed(float("nan")) == "-"

    dash = make_dash(db, tmp_path, card_list=cards({"key": "kakao", "type": "kakao_openchat"},
                                                   {"key": "coupang", "type": "coupang_partners"}))
    # 숫자가 아닌 값은 그 줄만 버리고 나머지는 저장 (배치째 잃지 않음)
    stored = dash._store(dash.sources[1], model.FetchResult(model.OK, values={"commission": float("nan"), "clicks": 3.0},
                                                            days={YESTERDAY: {"orders": float("inf"), "clicks": 2.0}}))
    assert stored == 2 and db.metric_latest("coupang", "orders") is None and db.metric_latest("coupang", "clicks")["value"] == 3
    # 예전 코드가 넣어 둔 무한 값이 DB 에 있어도 페이지는 뜬다 (그 값만 '-')
    db.metric_put("coupang", "commission", YESTERDAY, float("inf"))
    db.metric_put("kakao", "members", TODAY, float("inf"), origin="manual")
    page_text = dash.page_html()
    assert "쿠팡 파트너스" in page_text and "카카오 오픈채팅" in page_text


def test_one_card_failing_to_render_only_breaks_that_card(db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import render

    real = render._stat

    def flaky(s: model.Stat) -> str:
        if s.label == "참여자":
            raise OverflowError("cannot convert float infinity to integer")
        return real(s)

    monkeypatch.setattr(render, "_stat", flaky)
    dash = make_dash(db, tmp_path, card_list=cards({"key": "kakao", "type": "kakao_openchat"}, {"key": "dealbot", "type": "dealbot"}))
    page_text = dash.page_html()
    kakao = page_text.split('id="card-kakao"', 1)[1].split("</article>", 1)[0]
    assert "카드를 그리지 못함 (OverflowError" in kakao and "판단 불가" in kakao
    assert "핫딜 봇 운영" in page_text and 'id="card-dealbot"' in page_text


# ---------------------------------------------------------------- 블로그: 자정 직후·게시글 수 직접 입력
def test_blog_activity_after_midnight_is_labelled_yesterday_and_not_counted_today(db: Database, tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 10, 14, 15, 40, tzinfo=UTC))  # 10/15 00:40 (한국시간)
    _write_export(tmp_path, "2026-10-14T23:58:00+09:00", {"finance": {
        "posts_published_today": 3, "posts_published_total": 60, "drafts_saved_today": 1, "neighbor_requests_today": 20,
        "neighbor_requests_7d": 90, "comments_today": 4, "replies_today": 2}})
    dash = make_dash(db, tmp_path, clock=clock, card_list=cards(
        {"key": "blog", "type": "naver_blog", "goals": {"posts": 50}, "options": {"blog": "finance"}}))
    v = card_of(dash, "blog")
    assert v.activity == ["어제 발행 3 · 임시저장 1 · 서로이웃 신청 20 (7일 90) · 댓글 4 · 답글 2"]
    assert "posts_today" not in v.summary  # 맨 위 '오늘 올린 글' 에 어제 글을 더하지 않음
    posts = next(r for r in v.rows if r.label == "게시글 수")
    assert posts.value == 60 and posts.met  # 발행 합계는 날짜와 상관없어 그대로 씀
    assert dash.summary([v])[1][1] == "-"


def _posts_row(v: model.CardView) -> GoalRow:
    return next(r for r in v.rows if r.label == "게시글 수")


def test_blog_post_count_uses_owner_entry_plus_later_bot_posts(db: Database, tmp_path: Path) -> None:
    clock = Clock()
    card = cards({"key": "blog", "type": "naver_blog", "goals": {"posts": 50}, "options": {"blog": "finance"}})
    _write_export(tmp_path, "2026-10-14T18:00:00+09:00", {"finance": {"posts_published_total": 23}})
    dash = make_dash(db, tmp_path, clock=clock, card_list=card)
    v = card_of(dash, "blog")
    assert _posts_row(v).value == 23 and v.status == model.UNMET  # 넣은 적이 없으면 Blog-Auto 발행 합계 (손으로 쓴 글은 모름)
    assert [f.metric for f in v.manual] == ["neighbors", "visitors", "posts"]
    # 주인이 블로그에 보이는 글 수(손으로 쓴 예전 글까지)를 넣음
    assert dash.save_manual("blog", {"posts": "63"}) == "saved"
    v = card_of(dash, "blog")
    assert _posts_row(v).value == 63 and _posts_row(v).met and v.manual_values["posts"] == 63 and v.manual_days["posts"] == TODAY
    # 다음 날 봇이 2개 더 발행 → 63 + 2
    clock.t += timedelta(days=1)
    _write_export(tmp_path, "2026-10-15T18:00:00+09:00", {"finance": {"posts_published_total": 25}})
    assert _posts_row(card_of(dash, "blog")).value == 65
    # 넣은 수가 Blog-Auto 합계보다 작으면(잘못 넣음) 합계를 씀
    assert dash.save_manual("blog", {"posts": "10"}) == "saved"
    assert _posts_row(card_of(dash, "blog")).value == 25


def test_manual_form_is_empty_with_last_value_caption_and_clear_date_label(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, card_list=cards({"key": "blog", "type": "naver_blog", "options": {"blog": "finance"}}))
    db.metric_put("blog", "neighbors", YESTERDAY, 257, origin="manual")
    page_text = dash.page_html()
    form = page_text.split('<form class="manual"', 1)[1].split("</form>", 1)[0]
    assert 'placeholder="257"' not in form and form.count('placeholder="숫자"') == 3  # 칸은 비어 보이게
    assert "지난 값 257 · 10.\u00a013." in form and "아직 없음" in form
    assert "입력하는 날<input" in form and "어제 방문자는 입력하는 날의 전날 값으로 저장돼요." in form


# ---------------------------------------------------------------- 봇 운영: 지운 날의 기록은 0 으로 덮지 않음
def test_dealbot_does_not_overwrite_history_older_than_the_queue_keeps(db: Database, tmp_path: Path) -> None:
    db.metric_put("dealbot", "deals", "2026-09-20", 9)  # 24일 전에 저장해 둔 값 (그 딜 기록은 이미 지워짐)
    db.metric_put("dealbot", "deals", "2026-10-12", 4)
    dash = make_dash(db, tmp_path, card_list=cards({"key": "dealbot", "type": "dealbot"}), history_days=10)
    card_of(dash, "dealbot")
    assert db.metric_series("dealbot", "deals", "2026-09-20", "2026-09-20") == [("2026-09-20", 9.0)]
    assert db.metric_latest("dealbot", "deals", until_day="2026-10-12")["value"] == 0  # 기록이 남아 있는 9일 안은 다시 셈
    assert db.metric_series("dealbot", "deals", "2026-10-01", "2026-10-05") == []  # 9일보다 전은 건드리지 않음


# ---------------------------------------------------------------- 그래프: 짧은 기록·날마다 오르내리는 값
def test_sparkline_fills_the_width_for_short_histories() -> None:
    from dealbot.dashboard.render import spark_window, sparkline

    today = date(2026, 10, 14)
    one = sparkline([(TODAY, 412)], days=28, today=today)
    assert 'd="M3.0 23.0 L237.0 23.0"' in one  # 첫날: 짧은 토막이 아니라 가운데 높이의 가로 전체 선
    two = sparkline([(YESTERDAY, 35), (TODAY, 41)], days=28, today=today)
    assert 'd="M3.0 43.0 L198.0 43.0 L237.0 3.0"' in two  # 이틀: 7일 폭에 그려 오른쪽 끝의 세로 막대가 아니게
    assert spark_window([(26, 1.0), (27, 2.0)], days=28) == ([(5, 1.0), (6, 2.0)], 7)
    assert spark_window([(3, 1.0), (27, 2.0)], days=28) == ([(0, 1.0), (24, 2.0)], 25)  # 기록이 길면 첫 기록부터
    assert spark_window([(0, 1.0)], days=28) == ([(0, 1.0)], 28)
    assert 'stroke-dasharray="3 4"' in sparkline([], days=28, today=today)  # 기록 없음은 그대로 점선


def test_rolling_sum_and_average(db: Database, tmp_path: Path) -> None:
    dash = make_dash(db, tmp_path, card_list=cards({"key": "kakao", "type": "kakao_openchat"}))
    for i in range(10):  # 10/5~10/14 매일 1~10
        db.metric_put("kakao", "x", (date(2026, 10, 5) + timedelta(days=i)).isoformat(), i + 1)
    data = CardData(dash.ctx, "kakao")
    sums = data.rolling("x", 7)
    assert sums[0] == ("2026-10-11", 28.0) and sums[-1] == (TODAY, 4 + 5 + 6 + 7 + 8 + 9 + 10)  # 7일이 다 찬 날부터
    assert len(sums) == 4
    db.metric_put("kakao", "v", "2026-10-10", 100)
    db.metric_put("kakao", "v", "2026-10-12", 50)
    avgs = CardData(dash.ctx, "kakao").rolling("v", 7, how="avg")
    assert avgs[0] == ("2026-10-10", 100.0) and avgs[-1] == (TODAY, 75.0)  # 평균은 넣은 값만으로


# ---------------------------------------------------------------- 화면 다듬기 (참고 화면)
def test_page_design_details(db: Database, tmp_path: Path) -> None:
    from dealbot.dashboard.render import CSS

    dash = make_dash(db, tmp_path, card_list=cards(
        {"key": "dealbot", "type": "dealbot", "goals": {"deals_per_day": 10, "links_waiting_max": 0}},
        {"key": "coupang", "type": "coupang_partners"},
        {"key": "blog_finance", "type": "naver_blog", "title": "Deren의 아카이브", "options": {"blog": "finance"}},
    ))
    db.metric_put("coupang", "commission", YESTERDAY, 12345678)
    db.metric_put("coupang", "orders", YESTERDAY, 3)
    db.metric_put("coupang", "clicks", YESTERDAY, 1234567)
    page_text = dash.page_html(("저장했어요.", "ok"), notice_src="blog_finance")
    # 안내는 누른 카드 안에, 맨 위에는 없음
    blog = page_text.split('id="card-blog_finance"', 1)[1].split("</article>", 1)[0]
    assert '<div class="notice" role="status">저장했어요.</div>' in blog and page_text.count("저장했어요.") == 1
    # 카드 이름은 잘리지 않고(줄바꿈 허용), 좁은 폰에서는 다시 읽기 글자를 숨기고 ↻ 만
    assert ".head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap" in CSS
    assert "text-overflow:ellipsis" not in CSS and "white-space:nowrap;overflow:hidden" not in CSS
    assert "@media (max-width:420px){.btn .btn-text{position:absolute" in CSS
    assert "@container (max-width:344px){.btn .btn-text{position:absolute" in CSS  # 2열(태블릿)의 좁은 카드도
    # 숫자 칸은 옅은 회색 바탕·둥근 모서리, 카드는 옅은 그림자 (참고 화면)
    assert ".stat{background:var(--tile);border:1px solid var(--line);border-radius:10px" in CSS and "--tile:#f8f9fb" in CSS
    assert ".card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 16px 18px;min-width:0;\nbox-shadow" in CSS
    # 큰 숫자는 숫자 가운데서 줄이 바뀌지 않음
    assert '<span class="num">12,345,678</span><span class="unit">원</span>' in page_text and ".num{white-space:nowrap}" in CSS
    # 칩 개수는 맨 위 줄에, 쿠팡 요약 칸에는 주문 수
    top = page_text.split('<header class="top">', 1)[1].split("</header>", 1)[0]
    assert '<span class="chip met">조건 충족 1</span><span class="chip unmet">조건 미충족 1</span>' in top
    assert '<span class="chip unknown">판단 불가 1</span>' in top
    assert dash.summary(dash.views())[3] == ("쿠팡 수익 · 최근 7일", "12,345,678원", "주문 3건 · 어제까지")
    # '최대 N' 줄이 넘치면 꽉 찬 주황 막대 (줄어들수록 길어지는 보라 막대가 아님)
    dealbot = page_text  # 기다리는 딜 0개 ≤ 최대 0 → 충족(꽉 찬 초록).split('id="card-dealbot"', 1)[1].split("</article>", 1)[0]
    assert '<div class="bar met"><i style="width:100.0%"></i></div>' in dealbot
    from dealbot.dashboard.render import _row

    assert _row(GoalRow("링크 기다리는 딜", 5, 3, "개", lower_is_better=True)).endswith(
        '<div class="bar over"><i style="width:100.0%"></i></div></div>')
    assert ".bar.over i{background:var(--orange)}" in CSS
