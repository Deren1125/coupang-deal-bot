"""오늘 X 글 · 큰 사건 바로 알림 (주인 11/15 — "오늘 x 글 … 대시보드에 띄워줘 · 큰 사건은 바로 알림" · dashboard/xtoday.py · static/xtoday.js).

1) 큰 사건 점수 (규칙만): 돈 크기·이름 사전·센 낱말·매체 수·속도 — 젠슨 황 1억 800만 달러는 매체가 모이면 알림, 이름 없는 회사·사망·정치는 아님.
2) 알림: 딜봇 전용 급한 종류 x_big (두 저장소 계약 표 밖 · 카톡 예비 없음) · 누르면 #/sns/xt/<번호> · 같은 사건 한 번 ·
   같은 이름 하루 한 번 · 하루 상한 · 간격 · 조용한 시간엔 미룸 · 키가 막히면 쉼.
3) 오늘 카드: 아침 한 번 + 보충 · 시각별 · 같은 사건 중복 없음 · 자동 초안 상한·주인 몫 남기기 · AI 가 안 되면 그 차례는 끝.
4) 글 쓰는 모델은 Opus 까지 — xstudio 와 같은 길(POST_SYSTEM·assert_writer_model)로만, Fable 이면 AI 를 부르지 않음.
5) '올렸어요'에 카드 번호가 남고 큰 사건 알림이 닫힘 · '안 써요' · 화면 API (로그인·같은 사이트 토큰) · 할 일 줄 단추.
6) xtoday.js (node): 카드·화면 · 출처 링크·숫자 검사 표시 · '열기'는 X 글쓰기 화면에 초안을 넘기기만 (자동 게시·주소만으로 AI 부르기 없음).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
import shutil
import subprocess
from datetime import datetime
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import pytest

from dash_helpers import (
    ORIGIN,
    PushServer,
    decrypt,
    get_json,
    post_json,
    push_settings,
    session_headers,
    subscription,
)
from dealbot.app import DealBot
from dealbot.config import Settings, XTodayConfig
from dealbot.dashboard import naverhub, shell
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from dealbot.notify import router as routermod

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
FABLE = "-".join(["claude", "fable", "5", "2"])  # 판 번호가 붙은 이름은 저장소에 그대로 적지 않는다 (test_no_model_ids_in_the_repo)
OK_TEXT = "이번 일은 숫자보다 순서가 이상함\n발표가 먼저고 설명이 나중임\n\n그래서 다들 헷갈리는 듯"


def at(hm: str, day: str = "2026-11-15") -> float:
    return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=xs.KST).timestamp()


class FakeDb:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v


class FakeAsk:
    """AI 대신: 부를 때마다 글이 다른 초안 하나 (받은 것을 모음). fail 이 있으면 그 오류를 낸다."""

    def __init__(self, text: str = OK_TEXT, *, fail: Exception | None = None, ok: bool = True) -> None:
        self.text, self.fail, self.ok = text, fail, ok
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user, schema))
        if self.fail is not None:
            raise self.fail
        return {"ok": self.ok, "why": "" if self.ok else "광고 글", "confirmed": True,
                "drafts": [{"text": f"{self.text}\n{len(self.calls)}번째 관점", "format": "반전", "angle": "순서가 뒤집힘",
                            "hints": ["내 지갑에 닿는 곳은?", "다음에 볼 것은?"]}]}


class FakeRouter:
    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []
        self.closed: list[str] = []

    def open(self, n: dict[str, Any]) -> str:
        self.opened.append(n)
        return "new"

    def resolve(self, src: str, keys: list[str]) -> int:
        self.closed += keys
        return len(keys)


class FakeNews:
    """뉴스 검색 대신: rows 를 그대로 (부른 낱말을 모음)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, dead: bool = False) -> None:
        self.rows, self.dead, self.asked = rows or [], dead, []

    async def search(self, query: str) -> list[dict[str, Any]] | None:
        self.asked.append(query)
        return None if self.dead else list(self.rows)


def news(title: str, host: str, when: float, desc: str = "") -> dict[str, Any]:
    return {"title": title.replace("젠슨 황", "<b>젠슨 황</b>"), "originallink": f"https://www.{host}/a/{abs(hash(title + host)) % 10**6}",
            "link": "https://n.news.naver.com/x", "description": desc, "pubDate": format_datetime(datetime.fromtimestamp(when, xs.KST))}


def jensen(when: float, n: int = 8) -> list[dict[str, Any]]:
    return [news(f"젠슨 황, 1억 800만 달러 기부 약속{'!' * (i % 2)}", f"press{i}.example", when - i * 60,
                 "젠슨 황 엔비디아 CEO가 모교에 1억 800만 달러를 내기로 했다.") for i in range(n)]


def hot(i: str, keyword: str, title: str, *, score: int = 7, breaking: bool = False, created: str = "2026-11-15T06:30:00+09:00") -> dict[str, Any]:
    return {"id": i, "keyword": keyword, "text": f"{keyword} 이야기\n{title}", "reply": "", "confirmed": True, "breaking": breaking, "score": score,
            "created_at": created, "sources": [{"title": title, "link": f"https://news.example/{i}"}]}


HOTS = [hot("a1", "세무사 시험 논란", "세무사 시험 채점 논란 확산"), hot("a2", "쿠팡 과징금", "쿠팡에 과징금 부과", breaking=True),
        hot("a3", "카카오 먹통", "카카오 서비스 또 먹통", score=9), hot("a4", "테슬라 리콜", "테슬라 대규모 리콜"),
        hot("a5", "넷플릭스 요금", "넷플릭스 요금 인상"), hot("a6", "아이폰 가격", "아이폰 출고가 인상")]


def desk(*, ask: Any = None, hots: list[dict[str, Any]] | None = None, rows: list[dict[str, Any]] | None = None, now: list[float] | None = None,
         **cfg: Any) -> tuple[xt.XToday, FakeAsk, FakeRouter, FakeNews, list[float]]:
    now = now if now is not None else [at("07:45")]
    big = cfg.pop("big", {})
    conf = XTodayConfig(**cfg, big=big)
    db, ask, router, feed = FakeDb(), ask or FakeAsk(), FakeRouter(), FakeNews(rows)
    studio = xs.Studio(db, ask=ask, hot=lambda: list(HOTS if hots is None else hots), clock=lambda: now[0])
    return xt.XToday(db, studio, cfg=lambda: conf, router=lambda: router, news=feed, clock=lambda: now[0]), ask, router, feed, now


# ---------------------------------------------------------------- 1) 큰 사건 점수
def test_money_reads_korean_amounts_and_ignores_counts() -> None:
    assert xt.money("젠슨 황, 1억 800만 달러 기부") == (1.08e8 * 1400, "1억800만달러")
    assert xt.money("엔비디아 시총 이틀 만에 600조 증발") == (6e14, "600조")
    assert xt.money("삼성전자 1조2000억원 자사주 소각")[1] == "1조2000억원" and xt.money("3조 2,000억 원 투자")[0] == 3.2e12
    for not_money in ("관객 3천만 돌파", "가입자 5억 명 돌파", "12만전자 간다", "조회 2억 뷰", "2026년 계획"):
        assert xt.money(not_money) == (0.0, ""), not_money
    assert xt.who("파인애플·메타버스·토스트 가격") == ([], []) and xt.who("젠슨황 엔비디아 CEO") == (["젠슨 황"], ["엔비디아"])
    assert xt.who("일론머스크의 xAI, 오픈AI 에 소송") == (["일론 머스크"], ["오픈AI", "xAI"])


def scored(rows: list[dict[str, Any]], now: float, **kw: Any) -> list[dict[str, Any]]:
    arts = [a for a in (xt.article(x, now) for x in rows) if a]
    return sorted((xt.score(c, now, min_score=kw.get("min_score", 9), min_outlets=kw.get("min_outlets", 3))
                   for c in xt.clusters(arts, now, window_s=7200)), key=lambda c: -c["score"])


def test_jensen_huang_story_alerts_only_when_many_outlets_carry_it() -> None:
    now = at("09:00")
    [c] = scored(jensen(now, 8), now)
    # 돈 2 (1,000억 원대) + 이름 3 (인물) + 센 낱말 1 (기부) + 매체 8곳 3 + 속도 1 = 10
    assert c["ok"] and c["score"] == 10 and c["parts"] == {"m": 2, "w": 3, "s": 1, "o": 3, "v": 1}
    assert c["lead"] == "젠슨 황" and c["shown"] == "1억800만달러" and c["outlets"] == 8 and c["title"].startswith("젠슨 황, 1억 800만 달러")
    assert len(c["arts"]) == xt.EVENT_ARTS and "<b>" not in c["arts"][0]["title"] and c["arts"][0]["link"].startswith("https://www.press")
    [few] = scored(jensen(now, 2), now)
    assert not few["ok"] and few["why"] == "매체 2곳"
    [three] = scored(jensen(now, 3), now)  # 매체 셋이면 아직 점수가 모자람 — 더 퍼지면 알림
    assert not three["ok"] and three["why"] == "점수 7"
    same_outlet = [news("젠슨 황, 1억 800만 달러 기부", "press0.example", now) for _ in range(9)]
    assert not scored(same_outlet, now)[0]["ok"]  # 한 매체가 여러 번 쓴 것은 한 곳


def test_no_alert_for_unknown_names_tragedy_politics_or_old_news() -> None:
    now = at("09:00")
    small = [news("한빛테크, 300억 원 투자 유치", f"p{i}.example", now) for i in range(9)]
    assert scored(small, now) == []  # 사전에 없는 이름 + 10조 아래 → 묶지도 않음
    huge = [news("국민연금, 30조 원 손실 충격", f"p{i}.example", now) for i in range(9)]
    assert scored(huge, now)[0]["ok"]  # 10조 넘는 돈은 이름 없이도
    sad = [news("삼성전자 공장 사고로 2명 사망, 1조 원 피해", f"p{i}.example", now) for i in range(9)]
    pol = [news("대통령, 삼성전자 100조 투자 약속 받아", f"p{i}.example", now) for i in range(9)]
    old = [news("엔비디아 시총 600조 증발", f"p{i}.example", now - 3 * 3600) for i in range(9)]
    assert scored(sad + pol + old, now) == []
    plain = [news("삼성전자, 신제품 공개 행사", f"p{i}.example", now) for i in range(9)]
    assert scored(plain, now) == []  # 큰 숫자도 센 낱말도 없는 기사
    assert scored(jensen(now, 8), now, min_score=11)[0]["why"] == "점수 10"  # 임계값은 설정으로


# ---------------------------------------------------------------- 2) 알림
async def test_big_event_pushes_once_then_respects_lead_day_cap_and_gap() -> None:
    d, _ask, router, feed, now = desk(rows=jensen(at("09:00")), now=[at("09:00")], hots=[], big={"queries": ["젠슨 황", "억 달러"], "predraft": False})
    [ev] = await d.poll()
    assert feed.asked == ["젠슨 황", "억 달러"] and len(router.opened) == 1
    n = router.opened[0]
    assert n["kind"] == "x_big" and n["urgent"] is True and n["sev"] == "hot" and n["src"] == "dealbot"
    assert n["iid"] == f"x-big-{ev['id']}" and n["key"] == f"xbig:{ev['id']}" and re.fullmatch(r"[0-9a-f]{8}", ev["id"])
    assert n["title"] == "🔥 X 큰 사건 — 젠슨 황 1억800만달러" and "매체 8곳" in n["body"] and n["ttl_h"] == xt.NOTICE_TTL_H
    assert await d.poll() == [] and feed.asked == ["젠슨 황", "억 달러"]  # 20분 안에는 뉴스를 다시 보지 않음
    now[0] += 21 * 60
    feed.rows = jensen(now[0]) + [news("젠슨 황, 엔비디아 주식 5,000억 원어치 매각", f"q{i}.example", now[0]) for i in range(9)]
    assert await d.poll() == [] and len(router.opened) == 1  # 같은 사건은 한 번 · 같은 이름도 하루 한 번
    # 다른 이름의 큰 사건: 알림 사이 90분이 안 지났으면 미뤘다가 (held) 간격이 지나면 보냄
    feed.rows = [news("머스크, 테슬라 보상 1조 달러 확정", f"q{i}.example", now[0]) for i in range(9)]
    now[0] += 21 * 60
    assert await d.poll() == [] and [e["state"] for e in d.big()["events"]] == ["new", "held"]
    now[0] += 60 * 60
    feed.rows = []
    [late] = await d.poll()
    assert late["lead"] == "일론 머스크" and len(router.opened) == 2
    v = d.view()
    assert [b["lead"] for b in v["big"]] == ["일론 머스크", "젠슨 황"] and v["big"][1]["sources"][0]["link"].startswith("https://www.press")
    assert v["log"][-1]["lead"] and v["bigcfg"] == {"on": True, "min_score": 9, "per_day": 3}


async def test_big_event_day_cap_quiet_hours_and_dead_key() -> None:
    d, _a, router, feed, now = desk(rows=jensen(at("03:00")), now=[at("03:00")], hots=[],
                                    big={"per_day": 1, "gap_minutes": 0, "quiet_hours": "01:00-07:00", "predraft": False})
    assert await d.poll() == [] and router.opened == [] and d.big()["events"][0]["state"] == "held"  # 조용한 시간엔 미룸
    now[0] = at("07:05")
    feed.rows = [news("머스크, 테슬라 보상 1조 달러 확정", f"q{i}.example", now[0]) for i in range(9)]
    sent = await d.poll()
    assert [e["lead"] for e in sent] == ["젠슨 황"] and len(router.opened) == 1  # 끝나면 점수 순으로 · 하루 1개 상한
    assert [e["state"] for e in d.big()["events"]] == ["new", "held"]
    assert xt.in_quiet("23:00-07:00", at("23:30")) and xt.in_quiet("23:00-07:00", at("06:59")) and not xt.in_quiet("23:00-07:00", at("07:00"))
    assert not xt.in_quiet("", at("03:00")) and not xt.in_quiet("oops", at("03:00"))
    # 키가 막히면 6시간 쉰다 (돌려 가며 다시 부르지 않음) · 화면에 한 줄
    d, _a, router, feed, now = desk(now=[at("09:00")], hots=[])
    feed.dead = True
    assert await d.poll() == [] and len(feed.asked) == 1
    now[0] += 30 * 60
    assert await d.poll() == [] and len(feed.asked) == 1 and "네이버 검색 키" in d.view()["note"]
    now[0] += 6 * 3600
    await d.poll()
    assert len(feed.asked) == 2
    # 하루 API 상한 · 꺼 두면 아예 안 봄
    d, _a, _r, feed, now = desk(now=[at("09:00")], hots=[], big={"api_per_day": 3})
    await d.poll()
    assert len(feed.asked) == 3
    d, _a, _r, feed, now = desk(now=[at("09:00")], hots=[], big={"enabled": False})
    assert await d.poll() == [] and feed.asked == []


def test_x_big_is_local_urgent_outside_the_shared_contract_and_never_kakao() -> None:
    assert "x_big" in routermod.LOCAL_URGENT and routermod.urgency("x_big") == "URGENT"
    assert "x_big" not in routermod.URGENCY and "x_big" not in routermod.KAKAO_KINDS  # 두 저장소 계약 표는 그대로 · 카톡 예비 없음
    table = json.loads((Path(__file__).parent / "fixtures" / "dash_contract" / "urgency.json").read_text(encoding="utf-8"))
    assert "x_big" not in json.dumps(table)


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_push_opens_the_draft_screen_and_falls_back_to_no_kakao(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    d: xt.XToday = bot.dashboard.api.xtoday  # type: ignore[union-attr]
    assert d.studio is bot.dashboard.api.xstudio and isinstance(d.news, xt.NaverNews)  # type: ignore[union-attr]
    server = PushServer(201)
    bot.router.http = server.client()  # type: ignore[union-attr]
    now = [at("09:00")]
    d.clock = d.studio.clock = bot.router.clock = lambda: now[0]  # type: ignore[union-attr]
    # 할 일 목록(state.items)은 벽시계로 열린 알림을 고른다 — 시험 날짜 09:00 이 실제 시각보다 6시간 넘게 지나면 줄이 빠졌음 (11/15 오후부터 실패)
    import dealbot.dashboard.state as statemod
    monkeypatch.setattr(statemod.time, "time", lambda: now[0])
    from datetime import UTC, datetime as _dt
    monkeypatch.setattr(bot.dash_state, "_now", lambda: _dt.fromtimestamp(now[0], UTC))
    d.news = FakeNews(jensen(now[0]))
    d.cfg().big.predraft = False
    # 기기가 없을 때: 카톡 예비로 새지 않는다 (할 일 줄만)
    [ev] = await d.poll()
    now[0] += 30
    out = await bot.router.tick()  # type: ignore[union-attr]
    row = bot.router.store.get("dealbot", f"xbig:{ev['id']}")  # type: ignore[union-attr]
    assert out == {"pushed": 0, "kakao": 0} and row.kakao_at == routermod.NO_KAKAO and row.kind == "x_big" and row.urgent
    # 할 일 줄: '초안 보기' → 그 사건 초안 화면
    item = next(i for i in bot.dash_state.items() if i["iid"] == f"x-big-{ev['id']}")
    assert item["primary"]["goto"] == f"#/sns/xt/{ev['id']}" and item["primary"]["label"] == "초안 보기" and item["sev"] == "hot"
    # 기기가 있으면: 한 통 · 누르면 바로 그 화면
    d.skip({"id": ev["id"]})
    assert bot.router.store.get("dealbot", f"xbig:{ev['id']}").resolved_at is not None  # type: ignore[union-attr]
    body, ua = subscription()
    from dealbot.notify import webpush

    bot.router.store.add_sub(endpoint=body["endpoint"], endpoint_hash=webpush.endpoint_hash(body["endpoint"]),  # type: ignore[union-attr]
                             p256dh=body["keys"]["p256dh"], auth=body["keys"]["auth"], label="Galaxy", session="s1", now=now[0])
    now[0] += 25 * 3600
    d.news = FakeNews([news("머스크, 테슬라 보상 1조 달러 확정", f"q{i}.example", now[0]) for i in range(9)])
    [ev2] = await d.poll()
    now[0] += 30
    assert (await bot.router.tick())["pushed"] == 1  # type: ignore[union-attr]
    [p] = [decrypt(r.content, ua) for r in server.requests]
    assert p["url"] == f"/dashboard/#/sns/xt/{ev2['id']}" and p["title"].startswith("🔥 X 큰 사건 — 일론 머스크") and p["urgent"] is True
    assert ORIGIN  # 같은 사이트 주소 (알림 주소는 경로만)


# ---------------------------------------------------------------- 3) 오늘 카드
async def test_morning_run_makes_cards_by_time_once_and_fills_later() -> None:
    d, ask, _r, _f, now = desk(now=[at("07:39")], big={"enabled": False})
    assert d.due(d.plan(), now[0]) == "" and (await d.run())["made"] == 0 and ask.calls == []  # 아침 시각 전
    now[0] = at("07:45")
    assert d.due(d.plan(), now[0]) == "morning"
    assert (await d.run())["made"] == 5 and len(ask.calls) == 5
    v = d.view()
    assert [i["slot"] for i in v["items"]] == list(xs.POST_TIMES) and v["free"] == [] and v["ai"]["used"] == 5 and v["ran"] is True
    # 막 뜨는 것 → 점수 순 · 카드에는 출처 기사 제목·링크 · 숫자 검사 · 첫 답글(출처)
    first = v["items"][0]
    assert first["keyword"] == "쿠팡 과징금" and v["items"][1]["keyword"] == "카카오 먹통"
    assert first["sources"] == [{"title": "쿠팡에 과징금 부과", "link": "https://news.example/a2"}] and first["nums_ok"] is True and first["warn"] == []
    assert first["first_reply"] == "출처: 쿠팡에 과징금 부과\nhttps://news.example/a2" and first["preview"].startswith("이번 일은")
    assert re.fullmatch(r"t[0-9a-f]{10}", first["id"]) and first["res"]["drafts"][0]["hints"] and first["done"] is False
    assert "내 한마디" not in first["preview"]
    now[0] += 60
    assert d.due(d.plan(), now[0]) == "" and (await d.run())["made"] == 0 and len(ask.calls) == 5  # 아침은 한 번
    # 보충: 빈 시각이 있을 때만, 그 시각 뒤 90분 안에 한 번
    now[0] = at("11:25")
    assert d.due(d.plan(), now[0]) == ""
    d.skip({"id": v["items"][3]["id"]})  # 18:30 카드 '안 써요' → 그 시각이 다시 빔
    assert d.view()["free"] == ["18:30"] and d.due(d.plan(), now[0]) == "fill:11:20"
    assert (await d.run())["made"] == 1 and len(ask.calls) == 6
    assert [i["slot"] for i in d.view()["items"]] == list(xs.POST_TIMES) and d.view()["items"][3]["keyword"] == "아이폰 가격"
    now[0] = at("00:10", "2026-11-16")  # 다음 날: 어제 초안은 사라지고, 어제 쓴 글감은 다시 쓰지 않는다
    assert d.view()["items"] == [] and d.view()["ran"] is False
    now[0] = at("08:00", "2026-11-16")
    assert (await d.run())["made"] == 0 and len(ask.calls) == 6


async def test_late_start_only_makes_cards_for_times_still_ahead() -> None:
    d, ask, _r, _f, now = desk(now=[at("15:50")], big={"enabled": False})
    assert (await d.run())["made"] == 3 and [i["slot"] for i in d.view()["items"]] == ["15:30", "18:30", "21:30"]  # 30분 지난 시각까지
    d, ask, _r, _f, now = desk(now=[at("22:30")], big={"enabled": False})
    assert (await d.run())["made"] == 0 and ask.calls == []
    d, ask, _r, _f, now = desk(now=[at("08:00")], enabled=False)
    assert (await d.run()) == {"big": 0, "made": 0} and d.view()["on"] is False


async def test_restart_in_the_middle_of_a_run_resumes_once_for_the_times_left() -> None:
    class Dies(FakeAsk):
        async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            if len(self.calls) == 2:
                raise asyncio.CancelledError  # 자동 업데이트로 프로그램이 꺼짐
            return await super().__call__(system, user, schema)

    d, ask, _r, _f, now = desk(ask=Dies(), big={"enabled": False})
    with pytest.raises(asyncio.CancelledError):
        await d.run()
    st = d.plan()
    assert len(st["items"]) == 2 and st["doing"] == "morning" and "morning" in st["runs"]
    again = xt.XToday(d.db, d.studio, cfg=d.cfg, clock=d.clock)  # 다시 켜진 프로그램
    d.studio._ask_fn = ask2 = FakeAsk()
    now[0] += 120
    assert again.due(again.plan(), now[0]) == "resume"
    assert (await again.run())["made"] == 3 and [i["slot"] for i in again.view()["items"]] == list(xs.POST_TIMES) and len(ask2.calls) == 3
    assert again.plan()["doing"] == "" and again.due(again.plan(), now[0] + 60) == ""
    # 이어서 하기는 한 번만 — 또 꺼져도 다음 보충 때까지 기다린다
    st = again.plan()
    st["doing"], st["items"] = "fill:11:20", st["items"][:2]
    again._save(xt.KV, st)
    assert again.due(again.plan(), now[0] + 120) == ""


async def test_same_story_is_not_written_twice_and_big_events_come_first() -> None:
    now = [at("07:45")]
    hots = [hot("b1", "젠슨 황 기부", "젠슨 황 1억 800만 달러 기부 약속"), hot("b2", "쿠팡 과징금", "쿠팡에 과징금 부과"),
            hot("b3", "쿠팡 과징금 논란", "쿠팡 과징금 두고 논란"), hot("b4", "옛 화제", "지나간 이야기", created="2026-11-14T08:00:00+09:00")]
    d, ask, router, _f, now = desk(rows=jensen(now[0]), now=now, hots=hots)
    out = await d.run()
    assert out == {"big": 1, "made": 2} and len(ask.calls) == 2  # 큰 사건 초안(미리) 1 + 쿠팡 1 — 같은 이야기 둘·옛 화제는 뺌
    items = d.view()["items"]
    assert [(i["kind"], i["slot"], i["keyword"]) for i in items] == [("big", "", "젠슨 황 1억800만달러"), ("hot", "08:30", "쿠팡 과징금")]
    assert "기사 요약:" in ask.calls[0][1] and "1억 800만 달러를 내기로 했다" in ask.calls[0][1] and "막 뜨는 말: 젠슨 황 1억800만달러" in ask.calls[0][1]
    assert items[0]["first_reply"].startswith("출처: 젠슨 황") and len(items[0]["sources"]) == 3
    assert d.view()["big"][0]["item"] == items[0]["id"]
    assert xt.same_event(xt.sig("젠슨 황 기부", ["젠슨 황 1억 800만 달러 기부 약속"]), xt.sig("젠슨 황", ["젠슨 황, 1억 800만 달러 기부"], "젠슨 황"))
    assert not xt.same_event(xt.sig("삼성전자 실적", ["삼성전자 영업이익 12조"]), xt.sig("삼성전자 파업", ["삼성전자 노조 파업 돌입"]))


async def test_auto_draft_budget_reserve_and_failures_stop_the_run() -> None:
    d, ask, _r, _f, now = desk(big={"enabled": False}, auto_drafts_per_day=2)
    assert (await d.run())["made"] == 2 and len(ask.calls) == 2 and "자동 초안 몫" in d.view()["err"]
    # 주인이 화면에서 누를 몫(reserve)은 남긴다 — xstudio 하루 40번 가운데
    d, ask, _r, _f, now = desk(big={"enabled": False}, reserve=10)
    for _ in range(xs.DRAFT_PER_DAY - 11):
        d.studio._count("draft", xs.DRAFT_PER_DAY)
    assert (await d.run())["made"] == 1 and d.studio._left("draft", xs.DRAFT_PER_DAY) == 10
    # AI 가 안 되면 그 차례는 끝 (돌려 가며 다시 부르지 않음) — 다음 보충 때만 다시
    d, ask, _r, _f, now = desk(ask=FakeAsk(fail=xs.StudioError(503, "ai_login", "서버 AI 로그인이 풀렸어요 — 메인 세션에 알려 주세요")), big={"enabled": False})
    assert (await d.run())["made"] == 0 and len(ask.calls) == 1 and "로그인이 풀렸어요" in d.view()["err"]
    now[0] += 120
    assert (await d.run())["made"] == 0 and len(ask.calls) == 1
    now[0] = at("11:21")
    ask.fail = None
    assert (await d.run())["made"] == 4 and d.view()["err"] == ""
    # 모델이 쓰지 않겠다고 한 글감은 기억해서 다시 고르지 않는다
    d, ask, _r, _f, now = desk(ask=FakeAsk(ok=False), big={"enabled": False}, hots=HOTS[:2])
    assert (await d.run())["made"] == 0 and len(ask.calls) == 2 and len(d.used(now[0])) == 2
    # 원문에 없는 숫자가 든 초안은 카드에 '숫자 확인 필요'
    d, ask, _r, _f, now = desk(ask=FakeAsk("쿠팡 과징금 1,628억 원 맞음\n이게 처음이 아님"), big={"enabled": False}, hots=HOTS[1:2])
    await d.run()
    [card] = d.view()["items"]
    assert card["nums_ok"] is False and "원문에 없는 숫자: 1628" in card["warn"][0]


async def test_owner_taps_do_not_eat_the_auto_budget_and_yesterdays_drafts_are_wiped_at_midnight() -> None:
    # 주인이 화면에서 누른 '초안 만들기'는 서버 자동 몫에 세지 않는다 (xstudio 하루 몫에는 셈)
    d, ask, _r, _f, now = desk(rows=jensen(at("09:00")), now=[at("09:00")], hots=[], big={"predraft": False})
    await d.poll()
    [ev] = d.view()["big"]
    await d.make({"id": ev["id"]})
    v = d.view()
    assert len(ask.calls) == 1 and v["ai"]["used"] == 0 and v["ai"]["left"] == xs.DRAFT_PER_DAY - 1
    # 날이 바뀌면 다음 1분 일에서 어제 초안 글이 kv 에서 사라진다 — 기능이 꺼져 있어도 · 아침 만들기를 기다리지 않고
    for off in (False, True):
        d, ask, _r, _f, now = desk(big={"enabled": False}, hots=HOTS[:2])
        await d.run()
        assert OK_TEXT.split("\n")[0] in d.db.kv[xt.KV] and len(d.view()["items"]) == 2
        if off:
            d.cfg = lambda: XTodayConfig(enabled=False)
        now[0] = at("00:01", "2026-11-16")
        calls = len(ask.calls)
        assert (await d.run())["made"] == 0 and len(ask.calls) == calls
        assert OK_TEXT.split("\n")[0] not in d.db.kv[xt.KV] and json.loads(d.db.kv[xt.KV])["day"] == "2026-11-16" and d.view()["items"] == []


async def test_news_api_stops_the_round_on_a_failed_read_and_the_borrowed_key_has_its_own_cap() -> None:
    class Flaky(FakeNews):
        async def search(self, query: str) -> list[dict[str, Any]] | None:
            self.asked.append(query)
            return None  # 한도·서버 오류·시간 초과 (키는 살아 있음)

    d, _ask, router, _f, now = desk(now=[at("09:00")], hots=[])
    d.news = feed = Flaky()
    assert await d.poll() == [] and len(feed.asked) == 1 and d.big()["api"] == 1 and d.big()["dead_until"] == 0  # 남은 검색어로 더 두드리지 않음
    now[0] += 60
    assert await d.poll() == [] and len(feed.asked) == 1  # 간격은 그대로 (바로 다시 부르지 않음)
    now[0] += 20 * 60
    await d.poll()
    assert len(feed.asked) == 2
    # Blog-Auto 키를 빌려 쓰는 동안은 api_per_day 를 올려도 하루 BLOG_KEY_PER_DAY 까지
    d, _ask, _r, feed, now = desk(now=[at("09:00")], hots=[], big={"api_per_day": 5000})
    feed.src = "blog"  # type: ignore[attr-defined]
    st = d.big()
    st["api"] = xt.BLOG_KEY_PER_DAY - 3
    d._save(xt.KV_BIG, st)
    await d.poll()
    assert len(feed.asked) == 3 and d.big()["api"] == xt.BLOG_KEY_PER_DAY
    now[0] += 20 * 60
    await d.poll()
    assert len(feed.asked) == 3
    feed.src = "dealbot"  # type: ignore[attr-defined]
    now[0] += 20 * 60
    await d.poll()
    assert len(feed.asked) == 3 + 8  # 딜봇 키면 설정 상한까지


async def test_naver_news_uses_keys_only_falls_back_once_and_never_logs_them(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from types import SimpleNamespace

    import httpx

    env = tmp_path / "blog.env"
    env.write_text("NAVER_CLIENT_ID=blogid\nNAVER_CLIENT_SECRET=blogsecret\n", encoding="utf-8")
    seen: list[tuple[str, str, dict[str, Any]]] = []
    status = {"dealid": 401, "blogid": 200}

    class Http:
        async def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float) -> httpx.Response:
            if url.startswith(naverhub.HUB):  # 예전 개발자센터 키 — HUB 는 401 → 예전 주소로 (HUB 먼저는 test_naver_news_hub_first)
                return httpx.Response(401, json={"errorMessage": "Authentication Failed"})
            seen.append((url, headers["X-Naver-Client-Id"], params))
            code = status[headers["X-Naver-Client-Id"]]
            if code == 0:
                raise httpx.ConnectTimeout("x")
            return httpx.Response(code, json={"items": [{"title": "가"}, "x"]} if code == 200 else {"errorCode": "024"})

    bot = SimpleNamespace(http=Http(), settings=SimpleNamespace(secrets=SimpleNamespace(naver_client_id="dealid", naver_client_secret="dealsecret"),
                                                                dashboard=SimpleNamespace(ssalmuk_search_env_file=str(env))))
    n = xt.NaverNews(bot)
    caplog.set_level("DEBUG")
    assert await n.search("엔비디아") == [{"title": "가"}] and n.src == "blog" and n.dead is False
    assert [k for _u, k, _p in seen] == ["dealid", "blogid"] and seen[0][0] == xt.NEWS_URL and seen[0][2] == {"query": "엔비디아", "display": 100, "sort": "date"}
    await n.search("조원")
    assert [k for _u, k, _p in seen][2:] == ["blogid"]  # 통했던 키부터 — 막힌 딜봇 키를 매번 두드리지 않음
    status["blogid"] = 429  # 한도 — 키는 살아 있음: 쉬지 않고 이번 것만 못 읽음 (다른 키로 돌려 쓰지 않음)
    assert await n.search("조원") is None and n.dead is False and [k for _u, k, _p in seen][3:] == ["blogid"]
    status["blogid"] = 0
    assert await n.search("조원") is None and n.dead is False
    status["blogid"] = 403
    assert await n.search("조원") is None and n.dead is True  # 둘 다 막힘 → 부르는 쪽이 6시간 쉼
    env.unlink()
    bot.settings.secrets.naver_client_id = ""
    seen.clear()
    assert await xt.NaverNews(bot).search("조원") is None and seen == []  # 키가 없으면 부르지 않음
    for secret in ("dealsecret", "blogsecret", "dealid", "blogid"):
        assert secret not in caplog.text


async def test_naver_news_falls_back_to_kakao_web_recency_and_keeps_only_outlets(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """11/15 네이버 키 둘 다 401 → 카카오(다음) 웹문서 최신순 (제목·주소·매체·시각만, 블로그·카페·영상은 뺌) ·
    카카오로 된 뒤엔 카카오 먼저, 네이버는 하루 한 번만 · 카카오 키는 딜봇 환경 먼저, 없으면 Blog-Auto .env 한 줄 · 값은 로그에 안 남김."""
    from types import SimpleNamespace

    import httpx

    env = tmp_path / "blog.env"
    env.write_text("NAVER_CLIENT_ID=blogid\nNAVER_CLIENT_SECRET=blogsecret\nKAKAO_REST_API_KEY=kakaofilekey\n", encoding="utf-8")
    seen: list[tuple[str, str, dict[str, Any]]] = []
    kakao_code = [200]
    docs = [{"title": "<b>엔비디아</b> 시총 5조 달러", "contents": "요약은 안 씀", "url": "https://www.hankyung.com/article/1",
             "datetime": "2026-11-15T09:10:00.000+09:00"},
            {"title": "블로그 글", "contents": "", "url": "https://blog.naver.com/x/1", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "카페 글", "contents": "", "url": "https://cafe.daum.net/x/1", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "영상", "contents": "", "url": "https://www.youtube.com/watch?v=1", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "커뮤니티 글 엔비디아 5조 달러", "contents": "", "url": "https://gall.dcinside.com/board/view/?id=x", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "지식iN", "contents": "", "url": "https://kin.naver.com/qna/detail.naver?d1id=1", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "쇼핑", "contents": "", "url": "https://smartstore.naver.com/x/products/1", "datetime": "2026-11-15T09:00:00.000+09:00"},
            {"title": "포털 뉴스", "contents": "", "url": "https://v.daum.net/v/20261115090000000", "datetime": "2026-11-15T09:05:00.000+09:00"},
            {"title": "언제 쓴 글인지 모름 엔비디아 5조 달러", "contents": "", "url": "https://old.example/2019", "datetime": ""},
            {"title": "", "contents": "", "url": "https://news.example/empty", "datetime": "2026-11-15T09:00:00.000+09:00"}]

    class Http:
        async def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float) -> httpx.Response:
            if "dapi.kakao.com" in url:
                seen.append((url, headers["Authorization"], params))
                return httpx.Response(kakao_code[0], json={"meta": {"total_count": 5}, "documents": docs} if kakao_code[0] == 200 else {})
            if url.startswith(naverhub.HUB):
                return httpx.Response(401, json={"errorMessage": "Authentication Failed"})
            seen.append((url, headers["X-Naver-Client-Id"], params))
            return httpx.Response(401, json={"errorMessage": "Not Exist Client ID"})

    now = [1_800_000_000.0]
    bot = SimpleNamespace(http=Http(), settings=SimpleNamespace(secrets=SimpleNamespace(naver_client_id="dealid", naver_client_secret="dealsecret"),
                                                                dashboard=SimpleNamespace(ssalmuk_search_env_file=str(env))))
    n = xt.NaverNews(bot, clock=lambda: now[0])
    caplog.set_level("DEBUG")
    rows = await n.search("엔비디아")
    assert [k for _u, k, _p in seen] == ["dealid", "blogid", "KakaoAK kakaofilekey"] and n.src == "kakao" and n.dead is False
    assert seen[-1][0] == "https://dapi.kakao.com/v2/search/web" and seen[-1][2] == {"query": "엔비디아", "sort": "recency", "size": 50, "page": 1}
    assert rows == [{"title": "엔비디아 시총 5조 달러", "link": "https://www.hankyung.com/article/1", "originallink": "https://www.hankyung.com/article/1",
                     "pubDate": "Sun, 15 Nov 2026 09:10:00 +0900"},
                    {"title": "포털 뉴스", "link": "https://v.daum.net/v/20261115090000000", "originallink": "https://v.daum.net/v/20261115090000000",
                     "pubDate": "Sun, 15 Nov 2026 09:05:00 +0900"},
                    {"title": "", "link": "https://news.example/empty", "originallink": "https://news.example/empty", "pubDate": "Sun, 15 Nov 2026 09:00:00 +0900"}]
    # 커뮤니티·지식iN·쇼핑·시각 모르는 글은 매체로 안 셈 (큰 사건 매체 수·속도 부풀림 방지) · 포털 뉴스·언론사 주소는 그대로
    for host, drop in (("gall.dcinside.com", True), ("www.fmkorea.com", True), ("m.blog.naver.com", True), ("post.naver.com", True),
                       ("n.news.naver.com", False), ("news.nate.com", False), ("www.yna.co.kr", False), ("biz.chosun.com", False), ("zdnet.co.kr", False)):
        assert xt.not_outlet(host) is drop, host
    a = xt.article(rows[0], now[0])
    assert a is not None and a["outlet"] == "hankyung.com" and a["desc"] == "" and a["at"] < now[0]
    assert xt.article(rows[2], now[0]) is None  # 제목 없는 줄은 버림 (예전 그대로)
    # 다음 검색은 카카오만 (네이버를 매번 두드리지 않음) · 하루 지나면 네이버를 다시 봄
    await n.search("조원")
    assert [k for _u, k, _p in seen][3:] == ["KakaoAK kakaofilekey"]
    now[0] += 86401
    await n.search("조원")
    assert [k for _u, k, _p in seen][4:] == ["dealid", "blogid", "KakaoAK kakaofilekey"]
    # 딜봇 환경에 카카오 키가 있으면 그것 먼저
    bot.settings.secrets.kakao_rest_api_key = "kakaoenvkey"
    await n.search("조원")
    assert seen[-1][1] == "KakaoAK kakaoenvkey"
    # 카카오 429 = 한도 — 쉬지 않고 이번만 못 읽음 · 401 이면 셋 다 막힘 → dead (부르는 쪽이 6시간 쉼)
    kakao_code[0] = 429
    assert await n.search("조원") is None and n.dead is False
    kakao_code[0] = 401
    assert await n.search("조원") is None and n.dead is True
    for secret in ("dealsecret", "blogsecret", "dealid", "blogid", "kakaofilekey", "kakaoenvkey"):
        assert secret not in caplog.text


async def test_kakao_news_counts_against_the_borrowed_key_cap() -> None:
    """카카오 키도 이벤트 참여 검색과 같이 쓰는 빌린 키 — api_per_day 를 올려도 하루 BLOG_KEY_PER_DAY 까지."""
    d, _ask, _r, feed, now = desk(now=[at("09:00")], hots=[], big={"api_per_day": 5000})
    feed.src = "kakao"  # type: ignore[attr-defined]
    st = d.big()
    st["api"] = xt.BLOG_KEY_PER_DAY - 2
    d._save(xt.KV_BIG, st)
    await d.poll()
    assert len(feed.asked) == 2 and d.big()["api"] == xt.BLOG_KEY_PER_DAY


# ---------------------------------------------------------------- 4) 글 쓰는 모델
async def test_cards_are_written_through_xstudio_rules_and_never_by_fable(monkeypatch: pytest.MonkeyPatch) -> None:
    d, ask, _r, _f, now = desk(big={"enabled": False}, hots=HOTS[:1])
    await d.run()
    system, user, schema = ask.calls[0]
    assert system == xs.post_system("auto", True) and schema is xs.POST_SCHEMA and "'내 한마디' 줄을 쓰지 않는다" in system and "[속보]" in system
    assert d.view()["model"] == "Opus" and d.studio.model == xs.MODEL_DEFAULT
    # 설정이 Fable 이어도 Studio 는 Opus 로 — 그리고 어떤 이유로든 모델이 바뀌어 있으면 AI 를 부르지 않는다
    monkeypatch.setenv(xs.ENV_MODEL, FABLE)
    assert xs.Studio(FakeDb(), ask=ask).model == "opus"
    for bad in ("fable", FABLE, "mythos", "opus-6", "gpt"):
        d, ask, _r, _f, now = desk(big={"enabled": False}, hots=HOTS[:2])
        d.studio.model = bad
        assert (await d.run())["made"] == 0 and ask.calls == [] and "글 쓰는 모델 규칙" in d.view()["err"], bad
        with pytest.raises(xs.StudioError) as e:
            await d._draft({"key": "hot:a1", "kind": "hot", "hot_id": "a1", "keyword": "", "sig": {}, "sources": []}, d.plan(), "", now[0])
        assert e.value.code == "ai_off" and ask.calls == []
    # 이 파일은 AI 를 직접 부르지 않는다 (Studio.draft_src 하나로만) · 모델 이름도 적지 않는다
    code = (Path(xt.__file__)).read_text(encoding="utf-8")
    assert "JsonAsker" not in code and "llm_json" not in code and "import anthropic" not in code and "cli_model" not in code
    assert code.count("draft_src(") == 1 and "assert_writer_model" in code
    assert not re.search(r"claude-|sonnet|haiku|fable|mythos", re.sub(r'""".*?"""|#[^\n]*', "", code, flags=re.S), re.I)
    for key in ("--model", "subprocess", "x.com/i/api", "api.x.com", "api.twitter.com"):
        assert key not in code, key


# ---------------------------------------------------------------- 5) 주인이 누른 것 · 화면 API
async def test_posted_keeps_the_card_id_and_closes_the_big_event() -> None:
    now = [at("09:00")]
    d, _ask, router, _f, now = desk(rows=jensen(now[0]), now=now, hots=HOTS[:1])
    await d.run()
    big, card = d.view()["items"]
    text = big["preview"] + "\n\n이건 기부가 아니라 투자 같음"
    status, out = d.studio.posted({"op": "add", "text": text, "format": "auto", "mine": True, "tid": big["id"]})
    assert status == 200 and out["posts"]["today"] == 1
    [row] = d.studio.posted_rows()
    assert row["topic"] == big["id"] and row["draft_id"] == xs.sha(text) and "기부" not in json.dumps(row, ensure_ascii=False)  # 글은 해시만
    v = d.view()
    assert v["items"][0]["done"] is True and v["items"][1]["done"] is False and v["big"] == [] and router.closed == [f"xbig:{big['eid']}"]
    d.studio.posted({"op": "undo", "text": text, "tid": big["id"]})
    assert d.view()["items"][0]["done"] is False
    # 이상한 카드 번호는 기록에 안 남음
    d.studio.posted({"op": "add", "text": "다른 글\n\n내 말", "tid": "<script>"})
    assert "topic" not in d.studio.posted_rows()[-1]
    # '안 써요': 카드는 오늘 숨김 · 큰 사건이면 알림도 닫음 · 초안 없는 큰 사건은 화면에서 눌러 만든다
    d.skip({"id": card["id"]})
    assert [i["id"] for i in d.view()["items"]] == [big["id"]]
    with pytest.raises(xs.StudioError):
        d.skip({"id": "../etc"})
    d, ask, router, _f, now = desk(rows=jensen(at("09:00")), now=[at("09:00")], hots=[], big={"predraft": False})
    await d.poll()
    [ev] = d.view()["big"]
    assert ev["item"] == "" and d.view()["items"] == [] and ask.calls == []
    out = await d.make({"id": ev["id"]})
    assert out["made"] == out["items"][0]["id"] and out["big"][0]["item"] == out["made"] and len(ask.calls) == 1
    assert (await d.make({"id": ev["id"]}))["made"] == out["made"] and len(ask.calls) == 1  # 다시 눌러도 한 번
    with pytest.raises(xs.StudioError) as e:
        await d.make({"id": "00000000"})
    assert e.value.status == 404
    d.skip({"id": ev["id"]})
    assert d.view()["big"] == [] and router.closed == [f"xbig:{ev['id']}"]


async def test_card_check_box_adds_once_undoes_within_ten_minutes_and_survives_reload() -> None:
    """주인 11/15 '완료하면 체크 버튼으로 체크' — 카드 체크 = xstudio '올렸어요'(tid) 그대로. 글을 고쳐 해시가 달라도 카드 하나는 한 번."""
    now = [at("09:00")]
    d, _ask, router, _f, now = desk(rows=jensen(now[0]), now=now, hots=HOTS[:1])
    await d.run()
    big, card = d.view()["items"]
    assert card["done"] is False and card["undo_s"] == 0
    status, out = d.studio.posted({"op": "add", "text": card["preview"], "format": "auto", "mine": False, "tid": card["id"]})
    assert status == 200 and out["posts"]["today"] == 1
    # 글쓰기 화면에서 한마디를 붙여 또 '올렸어요' 해도 같은 카드면 한 번만 (오늘 올린 글 수·dash_x_posts 가 맞게)
    assert d.studio.posted({"op": "add", "text": card["preview"] + "\n\n내 말", "tid": card["id"]})[1]["posts"]["today"] == 1
    [row] = d.studio.posted_rows()
    assert row["topic"] == card["id"] and row["edited"] is False and json.loads(d.db.kv_get(xs.KV_POSTS))[0]["topic"] == card["id"]
    now[0] += 120
    v = d.view()  # 새로고침해도 서버 기록으로 체크 유지
    assert [i["done"] for i in v["items"]] == [False, True] and v["items"][1]["undo_s"] == int(xs.UNDO_WITHIN) - 120
    # 다른 글(해시가 달라도) + 카드 번호로 취소 → 그 카드 줄이 지워짐
    status, out = d.studio.posted({"op": "undo", "text": "고친 글\n\n한마디", "tid": card["id"]})
    assert status == 200 and out["posts"]["today"] == 0 and d.view()["items"][1]["done"] is False
    # 10분이 지나면 취소 못 함 (기존 UNDO_WITHIN)
    d.studio.posted({"op": "add", "text": card["preview"], "tid": card["id"]})
    now[0] += xs.UNDO_WITHIN + 1
    assert d.view()["items"][1]["undo_s"] == 0
    with pytest.raises(xs.StudioError) as e:
        d.studio.posted({"op": "undo", "text": card["preview"], "tid": card["id"]})
    assert e.value.status == 409 and "10분" in e.value.text and d.view()["items"][1]["done"] is True
    # 큰 사건 카드 체크 → 알림도 닫힘 (xstudio on_posted 경로 그대로)
    d.studio.posted({"op": "add", "text": big["preview"], "tid": big["id"]})
    assert router.closed == [f"xbig:{big['eid']}"] and d.studio.post_stats()["today"] == 2


async def test_manual_fill_runs_in_the_background_once_per_ten_minutes() -> None:
    d, ask, _r, _f, now = desk(now=[at("10:00")], big={"enabled": False})
    d.plan()
    out = await d.make({"op": "fill"})
    assert out["started"] is True
    await d.fill_task  # type: ignore[misc]
    assert [i["slot"] for i in d.view()["items"]] == ["12:00", "15:30", "18:30", "21:30"] and len(ask.calls) == 4
    with pytest.raises(xs.StudioError) as e:
        await d.make({"op": "fill"})
    assert e.value.code in ("rate", "full")
    assert d.tick() is True and d.tick() is False  # 1분 일: 도는 중이면 건너뜀
    await d.bg  # type: ignore[misc]


async def test_endpoints_need_login_and_same_site_token(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", xt.STATE_PATH, anon))[0] == 401
    h = session_headers(bot)
    status, v, headers = await get_json(bot, xt.STATE_PATH, h)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert v["v"] == 1 and v["on"] is True and v["slots"] == list(xs.POST_TIMES) and v["items"] == [] and v["big"] == [] and v["model"] == "Opus"
    assert v["ai"] == {"used": 0, "cap": 8, "left": xs.DRAFT_PER_DAY}
    for path, body in ((xt.MAKE_PATH, {"id": "0123abcd"}), (xt.SKIP_PATH, {"id": "0123abcd"})):
        assert (await post_json(bot, path, body, session_headers(bot, csrf=False)))[0] == 403, path
        assert (await post_json(bot, path, body, anon))[0] in (401, 403), path
    assert (await post_json(bot, xt.MAKE_PATH, {"id": "0123abcd"}, h))[0] == 404
    assert (await post_json(bot, xt.MAKE_PATH, {"id": "nope"}, h))[0] == 400
    status, out, _ = await post_json(bot, xt.SKIP_PATH, {"id": "0123abcd"}, h)
    assert status == 200 and out["status"] == "ok" and out["items"] == []
    cfg = bot.settings.dashboard.x_today
    assert (cfg.morning, cfg.fills, cfg.per_day, cfg.auto_drafts_per_day) == ("07:40", ["11:20", "17:20"], 5, 8)
    assert (cfg.big.min_score, cfg.big.min_outlets, cfg.big.per_day, cfg.big.gap_minutes, cfg.big.quiet_hours) == (9, 3, 3, 90, "")
    assert len(cfg.big.queries) * (24 * 60 // cfg.big.poll_minutes) <= cfg.big.api_per_day  # 하루 뉴스 API 가 상한 안
    with pytest.raises(ValueError):
        XTodayConfig(morning="25:00")
    with pytest.raises(ValueError):
        XTodayConfig(big={"quiet_hours": "밤"})


# ---------------------------------------------------------------- 6) xtoday.js
def test_xtoday_js_is_local_safe_and_loaded_by_shell() -> None:
    js = (STATIC / "xtoday.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "xtoday.js")], capture_output=True).returncode == 0
    code = re.sub(r"/\*.*?\*/|  //[^\n]*", "", js, flags=re.S)
    assert "innerHTML" not in code and "outerHTML" not in code and "eval(" not in code and "new Function" not in code and "document.write" not in code
    assert re.findall(r"https?://[^\s'\"`)]+", code) == []  # 바깥 주소 없음 — 출처 링크는 서버 값 (http(s) 만 건다)
    assert "/dashboard/api/xtoday/" in code and "X-Dash-Token" in code and "intent" not in code  # 올리기 링크는 xstudio.js 화면에서만
    for label in ("오늘 X 글", "큰 사건", "숫자 검사 통과", "숫자 확인 필요", "출처 기사", "열기 — 내 한마디 쓰고 올리기", "첫 답글 복사", "안 써요",
                  "초안 만들기", "지금 보충하기", "자동으로 올리지 않아요", "긴 글 복사", "올렸어요", "execCommand('copy')", "/dashboard/api/xstudio/posted"):
        assert label in code, label
    raw = (STATIC / "xtoday.js").read_bytes()
    assert len(raw) <= 18 * 1024 and len(gzip.compress(raw, 9)) <= 7 * 1024
    assert shell.XTODAY_JS == "xtoday.js"
    page = shell.page({})
    tag = f'<script defer src="{shell.asset_url("xtoday.js")}"></script>'
    assert tag in page and page.index(tag) > page.index(shell.asset_url("xstudio.js"))  # xstudio.js 다음 (그 화면에 초안을 넘김)
    xsjs = (STATIC / "xstudio.js").read_text(encoding="utf-8")
    assert "tid: R.tid" in xsjs and "view: V" in xsjs  # '올렸어요'에 카드 번호 · 초안을 넘겨받는 자리


SHIM = r"""
const [src, scenario, stateJson] = process.argv.slice(1);
const fs = require('fs');
function mk(tag) {
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
const mk0 = mk; mk = t => Object.assign(mk0(t), {focus() {}, select() {}, setSelectionRange() {}});
const main = mk('main'), ttl = mk('h1'), toast = mk('div'), head = mk('head');
main.attrs.id = 'main'; main.dataset.tab = 'sns'; ttl.attrs.id = 'ttl'; ttl.textContent = 'SNS'; toast.attrs.id = 'toast';
const snt = mk('div'); snt.attrs.id = 'snt';
for (let i = 0; i < 4; i++) { const b = mk('button'); b.attrs['aria-pressed'] = i === 1 ? 'true' : 'false'; snt.append(b); }
const xsc = mk('article'); xsc.attrs.id = 'xsc';
const box = mk('div'); main.append(...(scenario === 'both' ? [snt, box] : [snt, xsc, box]));
const byId = (n, id) => (n.attrs && n.attrs.id === id ? n : n.children.map(c => byId(c, id)).find(Boolean));
global.document = {readyState: 'complete', head, visibilityState: 'visible', activeElement: null, createElement: mk, addEventListener() {},
  getElementById: id => (id === 'boot' ? {textContent: JSON.stringify({me: {csrf: 'tok'}})} : id === 'ttl' ? ttl : id === 'toast' ? toast : byId(main, id) || byId(head, id) || null)};
const store = {};
global.location = {hash: process.argv[4] || '#/sns/x', origin: 'https://dash.example'};
global.window = global; global.addEventListener = () => {}; global.scrollTo = () => {};
global.MutationObserver = class { observe() {} };
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
global.setTimeout = () => 0; global.clearTimeout = () => {}; global.setInterval = () => 0; global.clearInterval = () => {};
const state = JSON.parse(stateJson), xsState = JSON.parse(process.argv[5] || 'null');
const posts = [], studio = {view: {res: null, ed: {}, el: {}, lg: {}, mine: {}, chk: {}, done: {}, tab: 'reply'}, calls: [], keep() { this.calls.push('keep'); }, mount() { this.calls.push('mount'); }, load() { this.calls.push('load'); }};
if (scenario !== 'both') global.SBXStudio = studio;
global.fetch = async (u, o) => {
  if (u.startsWith('/dashboard/api/xstudio/')) {  // 진짜 xstudio.js 와 같이 돌릴 때
    if (!o || o.method !== 'POST') return {status: 200, json: async () => xsState};
    const b = JSON.parse(o.body);
    posts.push(['xs:' + u.slice(23), o.headers['X-Dash-Token'], b]);
    const it = scenario === 'check' && state.items.find(i => i.id === b.tid);  // 서버 기록처럼 (새로고침하면 그대로)
    if (it) { it.done = b.op === 'add'; it.undo_s = it.done ? 600 : 0; }
    return {status: 200, json: async () => ({id: 'h1', posts: xsState.posts})};
  }
  const path = u.replace('/dashboard/api/xtoday/', '');
  if (!o || o.method !== 'POST') return {status: 200, json: async () => state};
  const body = JSON.parse(o.body);
  posts.push([path, o.headers['X-Dash-Token'], body]);
  if (path === 'make' && body.id) return {status: 200, json: async () => Object.assign({}, state, {made: state.items[0].id, big: state.big.map(b => Object.assign({}, b, {item: state.items[0].id}))})};
  if (path === 'skip') return {status: 200, json: async () => Object.assign({}, state, {items: state.items.filter(i => i.id !== body.id), big: state.big.filter(b => b.id !== body.id)})};
  return {status: 200, json: async () => Object.assign({started: true}, state)};
};
const clip = []; let picked = '';
Object.defineProperty(global, 'navigator', {value: {clipboard: {writeText: async t => { clip.push(t); }}}, configurable: true, writable: true});
document.body = mk('body'); document.execCommand = c => { const t = find(document.body, n => n.tagName === 'TEXTAREA'); picked = t ? t.value : ''; return c === 'copy' && !!t; };
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };
const text = n => n.textContent;
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach(c => all(c, f, out)); return out; };
const find = (n, f) => all(n, f)[0];
const byText = (root, tag, t) => find(root, n => n.tagName === tag && text(n) === t);
const click = n => n.listeners.click({currentTarget: n, preventDefault() {}});
(async () => {
  const out = {};
  if (scenario === 'both') eval(fs.readFileSync(src.replace('xtoday.js', 'xstudio.js'), 'utf8'));
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  if (scenario === 'both') {
    out.order = main.children.map(c => c.attrs.id || c.tagName);
    click(all(byId(main, 'xtc'), n => n.className === 'ln')[1]); await tick();
    const scr = main.children[0], d = find(scr, n => n.attrs['data-d'] === '0');
    out.scr = scr.attrs.id; out.hash = location.hash; out.card = text(d);
    const post = byText(d, 'A', 'X에서 올리기'), mine = find(d, n => (n.attrs['aria-label'] || '').startsWith('내 한마디'));
    out.before = [post.getAttribute('href'), post.attrs['aria-disabled']];
    mine.value = '과징금보다 다음 조사가 더 궁금함'; mine.listeners.input();
    out.blocked = post.getAttribute('href');  // 원문에 없는 숫자 경고 → 체크해야 올림
    const ck = find(d, n => n.attrs.type === 'checkbox'); ck.checked = true; ck.listeners.change();
    out.href = post.getAttribute('href');
    await click(byText(d, 'BUTTON', '올렸어요')); await tick();
    out.posts = posts; out.saved = JSON.parse(store.xs1).res.tid;
  }
  if (scenario === 'check') {
    const cards = () => all(main.children[0], n => n.attrs['data-t'] != null), cd = i => cards()[i];
    const inp = i => find(cd(i), n => n.attrs.type === 'checkbox'), flip = async (i, v) => { const x = inp(i); x.checked = v; await x.listeners.change({currentTarget: x}); await tick(); };
    out.start = cards().map(c => [c.className, 'checked' in inp(cards().indexOf(c)).attrs]);
    out.btns = all(cd(1), n => n.tagName === 'BUTTON').map(text);
    await click(byText(cd(1), 'BUTTON', '복사')); await tick(); out.c1 = [clip[clip.length - 1], text(toast)];
    studio.view.res = {tid: state.items[1].id}; studio.view.mine = {0: '이건 내 생각임'};  // 글쓰기 화면에서 쓴 한마디
    await click(byText(cd(1), 'BUTTON', '복사')); await tick(); out.c2 = [clip[clip.length - 1], text(toast)];
    await click(byText(cd(1), 'BUTTON', '첫 답글 복사')); await tick(); out.c3 = clip[clip.length - 1];
    navigator.clipboard.writeText = async () => { throw new Error('NotAllowed'); };  // 사파리가 막으면 숨은 칸으로
    await click(byText(cd(1), 'BUTTON', '복사')); await tick(); out.c4 = [picked, text(toast)];
    await flip(1, true); out.add = posts[posts.length - 1]; out.after = [cd(1).className, 'checked' in inp(1).attrs, text(toast)];
    await flip(1, false); out.undo = posts[posts.length - 1]; out.undone = [cd(1).className, 'checked' in inp(1).attrs];
    const n = posts.length; await flip(0, false); out.late = [posts.length - n, inp(0).checked, text(toast), cd(0).className];
  }
  if (scenario === 'card') {
    out.order = main.children.map(c => c.attrs.id || c.tagName);
    const card = byId(main, 'xtc');
    out.card = text(card); out.lines = all(card, n => n.className === 'ln').map(text);
    out.links = all(card, n => n.tagName === 'A').map(a => a.attrs.href);
    out.fetched = posts.length;
    click(all(card, n => n.className === 'ln')[1]);
    out.hash = location.hash; out.res = studio.view.res; out.tab = studio.view.tab; out.calls = studio.calls; out.posts = posts;
  }
  if (scenario === 'screen') {
    const scr = main.children[0];
    out.scr = scr.attrs.id; out.hidden = 'data-xt' in main.attrs; out.title = ttl.textContent; out.screen = text(scr);
    out.cards = all(scr, n => n.attrs['data-t'] != null).map(c => ({id: c.attrs['data-t'], cls: c.className, chips: all(c, n => n.className.startsWith('cp')).map(text),
      links: all(c, n => n.tagName === 'A').map(a => [a.attrs.href, a.attrs.target, a.attrs.rel, text(a)]), warn: all(c, n => n.className === 'w').map(text)}));
    const b = find(scr, n => n.attrs['data-b'] != null);
    out.big = {cls: b.className, text: text(b), btn: all(b, n => n.tagName === 'BUTTON').map(text)};
    out.before = posts.length;  // 주소만으로는 아무것도 누르지 않음
    studio.view.res = {tid: state.items[1].id}; studio.view.mine = {0: '어제 쓰던 한마디'};
    await click(byText(b, 'BUTTON', '초안 만들기')); await tick();
    out.made = posts[posts.length - 1]; out.hash = location.hash; out.res = studio.view.res; out.mine = studio.view.mine;
    out.saved = JSON.parse(store.xt1).e[state.items[1].id].mine;
    location.hash = '#/sns/xt'; window.SBXToday.mount();
    const c1 = find(main.children[0], n => n.attrs['data-t'] === state.items[1].id);
    click(byText(c1, 'BUTTON', '열기 — 내 한마디 쓰고 올리기'));
    out.back = [studio.view.res.tid, studio.view.mine];
    location.hash = '#/sns/xt'; window.SBXToday.mount();
    await click(byText(find(main.children[0], n => n.attrs['data-t'] === state.items[1].id), 'BUTTON', '안 써요')); await tick();
    out.skip = posts[posts.length - 1]; out.left = all(main.children[0], n => n.attrs['data-t'] != null).length;
    await click(byText(main.children[0], 'BUTTON', '지금 보충하기')); await tick();
    out.fill = [posts[posts.length - 1], text(toast)];
    location.hash = '#/sns/threads'; snt.children[1].attrs['aria-pressed'] = 'false'; window.SBXToday.mount();
    out.gone = ['data-xt' in main.attrs, main.children.map(c => c.attrs.id || c.tagName)];
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


async def view_json() -> tuple[dict[str, Any], str]:
    now = [at("09:00")]
    hots = [hot("c1", "쿠팡 과징금", "쿠팡에 과징금 부과"), hot("c2", "카카오 먹통", "카카오 서비스 또 먹통")]
    d, ask, _r, _f, now = desk(rows=jensen(now[0]), now=now, hots=hots, ask=FakeAsk("쿠팡 과징금 1,628억 원 맞음\n이게 처음이 아님"))
    await d.run()
    v = d.view()
    v["items"][1]["sources"].append({"title": "나쁜 주소", "link": "javascript:alert(1)"})
    v["items"][2]["confirmed"] = False
    return v, json.dumps(v, ensure_ascii=False)


def run_shim(scenario: str, state: str, hash_: str, xs_state: str = "null") -> dict[str, Any]:
    res = subprocess.run([NODE, "-e", SHIM, str(STATIC / "xtoday.js"), scenario, state, hash_, xs_state], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
async def test_js_card_lists_today_by_time_with_big_banner_and_hands_draft_to_studio() -> None:
    v, state = await view_json()
    got = await asyncio.to_thread(run_shim, "card", state, "#/sns/x")
    assert got["order"] == ["snt", "xsc", "xtc", "DIV"]  # '오늘 할 X 일' 카드 바로 아래
    assert got["lines"] == ["바로젠슨 황 1억800만달러숫자 확인", "08:30쿠팡 과징금숫자 확인", "12:00카카오 먹통숫자 확인", "15:30아직 없음", "18:30아직 없음", "21:30아직 없음"]
    assert "오늘 X 글" in got["card"] and "🔥 큰 사건 · 젠슨 황, 1억 800만 달러" in got["card"] and got["links"] == ["#/sns/xt"]
    # 줄을 누르면: 그 카드의 초안 묶음을 X 글쓰기 화면에 넘김 (카드 번호 tid 와 함께) — 서버에 보내는 것 없음
    assert got["hash"] == "#/sns/xs/write" and got["tab"] == "write" and got["calls"] == ["keep", "mount", "load"] and got["posts"] == []
    assert got["res"]["tid"] == v["items"][1]["id"] and got["res"]["drafts"] == v["items"][1]["res"]["drafts"]
    assert got["res"]["first_reply"].startswith("출처: 쿠팡에 과징금 부과") and got["res"]["src_nums"] == v["items"][1]["res"]["src_nums"]


@needs_node
async def test_js_screen_shows_sources_checks_and_only_acts_on_taps() -> None:
    v, state = await view_json()
    eid = v["big"][0]["id"]
    v["big"][0]["item"] = ""  # 초안이 아직 없는 큰 사건
    got = await asyncio.to_thread(run_shim, "screen", json.dumps(v, ensure_ascii=False), f"#/sns/xt/{eid}")
    assert got["scr"] == "xts" and got["hidden"] and got["title"] == "오늘 X 글" and got["before"] == 0
    for t in ("자동으로 올리지 않아요", "초안 Opus", "🔥 큰 사건 1", "오늘 글 3개", "15:30 · 18:30 · 21:30 글은 아직 없어요", "방금 본 뉴스 (점수)", "큰 사건 알림은 점수 9 넘으면 하루 3개까지"):
        assert t in got["screen"], t
    big, c1, c2 = got["cards"]
    assert big["cls"] == "itm hl" and big["chips"][:2] == ["바로", "큰 사건"]  # 알림으로 온 사건의 카드가 눈에 띄게
    assert c1["chips"] == ["08:30", "화제", "숫자 확인 필요"] and "원문에 없는 숫자: 1628" in c1["warn"][0]
    assert c1["links"] == [["https://news.example/c1", "_blank", "noopener", "쿠팡에 과징금 부과"]]  # 출처 기사 · javascript: 주소는 걸지 않음
    assert c2["chips"] == ["12:00", "화제", "숫자 확인 필요", "확인 안 된 이야기"]
    assert got["big"]["cls"] == "itm hl" and got["big"]["btn"] == ["초안 만들기", "안 써요"] and "매체 8곳" in got["big"]["text"]
    assert got["made"] == ["make", "tok", {"id": eid}] and got["hash"] == "#/sns/xs/write" and got["res"]["tid"] == v["items"][0]["id"]
    # 다른 카드를 열 때 쓰던 '내 한마디'는 카드마다 따로 기억했다가 돌려줌
    assert got["mine"] == {} and got["saved"] == {"0": "어제 쓰던 한마디"} and got["back"] == [v["items"][1]["id"], {"0": "어제 쓰던 한마디"}]
    assert got["skip"] == ["skip", "tok", {"id": v["items"][1]["id"]}] and got["left"] == 2
    assert got["fill"][0] == ["make", "tok", {"op": "fill"}] and "만들기 시작했어요" in got["fill"][1]
    assert got["gone"] == [False, ["snt", "xsc", "DIV"]]


@needs_node
async def test_js_with_real_studio_screen_needs_my_line_then_posts_by_hand_with_card_id() -> None:
    """xtoday.js + xstudio.js 를 같이: 카드 줄 → 글쓰기 화면의 초안 카드 → '내 한마디'·숫자 확인 뒤에만 X 글쓰기 링크 → '올렸어요'에 카드 번호."""
    v, state = await view_json()
    studio_view = xs.Studio(FakeDb(), ask=FakeAsk(), clock=lambda: at("09:00")).view()
    got = await asyncio.to_thread(run_shim, "both", state, "#/sns/x", json.dumps(studio_view, ensure_ascii=False))
    assert got["order"] == ["snt", "xsc", "xtc", "DIV"] and got["scr"] == "xss" and got["hash"] == "#/sns/xs/write"
    assert "원문에 없는 숫자: 1628" in got["card"] and "내 한마디 방향" in got["card"] and "첫 답글 복사" in got["card"]
    assert got["before"] == [None, "true"] and got["blocked"] is None  # 자동으로 올라가는 길 없음 — 내 한마디 + 숫자 확인 뒤에만 링크가 켜짐
    body = v["items"][1]["res"]["drafts"][0]["text"]
    assert got["href"] == xs.post_intent(body + "\n\n과징금보다 다음 조사가 더 궁금함")
    [posted] = got["posts"]
    assert posted[0] == "xs:posted" and posted[1] == "tok" and posted[2]["tid"] == v["items"][1]["id"] and posted[2]["op"] == "add" and posted[2]["mine"] is True
    assert got["saved"] == v["items"][1]["id"]


async def test_naver_news_calls_api_hub_first_then_old_address(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """11/15 주인 네이버 키는 API HUB 키 — 뉴스도 HUB(/search/v1/news, X-NCP 헤더) 먼저 · 401/403 이면 같은 키로 예전 주소 한 번."""
    from types import SimpleNamespace

    import httpx

    seen: list[tuple[str, dict[str, str]]] = []
    hub_code = [200]

    class Http:
        async def get(self, url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float) -> httpx.Response:
            seen.append((url, dict(headers)))
            code = hub_code[0] if url.startswith(naverhub.HUB) else 200
            return httpx.Response(code, json={"items": [{"title": "가"}]} if code == 200 else {})

    bot = SimpleNamespace(http=Http(), settings=SimpleNamespace(secrets=SimpleNamespace(naver_client_id="dealid", naver_client_secret="dealsecret"),
                                                                dashboard=SimpleNamespace(ssalmuk_search_env_file=str(tmp_path / "none.env"))))
    n = xt.NaverNews(bot)
    caplog.set_level("DEBUG")
    assert await n.search("엔비디아") == [{"title": "가"}] and (n.src, n.via) == ("dealbot", "hub")
    assert seen == [("https://naverapihub.apigw.ntruss.com/search/v1/news", {"X-NCP-APIGW-API-KEY-ID": "dealid", "X-NCP-APIGW-API-KEY": "dealsecret"})]
    for code in (401, 403):
        hub_code[0] = code
        seen.clear()
        assert await n.search("엔비디아") == [{"title": "가"}] and n.via == "old"
        assert [u for u, _h in seen] == [naverhub.url("news", True), xt.NEWS_URL] and seen[1][1] == {"X-Naver-Client-Id": "dealid", "X-Naver-Client-Secret": "dealsecret"}
    hub_code[0] = 500  # 서버 오류는 예전 주소로 넘어가지 않음 — 이번만 못 읽음
    seen.clear()
    assert await n.search("엔비디아") is None and len(seen) == 1 and n.dead is False
    for secret in ("dealid", "dealsecret"):
        assert secret not in caplog.text
    # 큰 사건 기록엔 어느 키로 통했는지 이름만 (HUB 면 '_hub')
    d, _ask, _r, feed, _now = desk(now=[at("09:00")], hots=[])
    feed.src, feed.via = "blog", "hub"  # type: ignore[attr-defined]
    await d.poll()
    assert d.big()["keysrc"] == "blog_hub"
    feed.src = "kakao"  # type: ignore[attr-defined]
    _now[0] += 3600
    await d.poll()
    assert d.big()["keysrc"] == "kakao"


@needs_node
async def test_js_card_copy_buttons_and_posted_check_box() -> None:
    """주인 11/15 'x 같은 경우에도 복사 버튼 … 완료하면 체크 버튼으로 체크' — 블로그 할 일 줄처럼."""
    v, _ = await view_json()
    v["items"][0].update(done=True, undo_s=0)  # 오래전에 올린 카드 (취소 못 함)
    got = await asyncio.to_thread(run_shim, "check", json.dumps(v, ensure_ascii=False), "#/sns/xt", json.dumps({"posts": {"today": 1}}))
    assert got["start"] == [["itm xd", True], ["itm", False], ["itm", False]]  # 서버 기록 done → 흐리게·줄긋기 + 체크
    it = v["items"][1]
    assert got["btns"][:2] == ["열기 — 내 한마디 쓰고 올리기", "복사"] and "첫 답글 복사" in got["btns"] and "안 써요" in got["btns"]
    assert ("긴 글 복사" in got["btns"]) == bool(it["res"]["drafts"][0].get("long"))
    # 한마디 없이도 복사는 되고, X 에서 한마디를 붙이라고 알려 줌 · 한마디가 있으면 붙여서
    assert got["c1"] == [it["preview"], "복사했어요 — X 에서 내 한마디를 한 줄 붙여 주세요"]
    assert got["c2"] == [it["preview"] + "\n\n이건 내 생각임", "글 복사했어요"] and got["c3"] == it["first_reply"]
    assert got["c4"] == [it["preview"] + "\n\n이건 내 생각임", "글 복사했어요"]  # 클립보드 API 가 막히면 숨은 칸 골라 복사
    # 체크 = xstudio '올렸어요' (카드 번호와) → 흐리게 · 다시 누르면 취소
    assert got["add"][:2] == ["xs:posted", "tok"] and got["add"][2] == {"op": "add", "text": it["preview"] + "\n\n이건 내 생각임", "format": "auto", "mine": True, "tid": it["id"]}
    assert got["after"][:2] == ["itm xd", True] and "기록했어요" in got["after"][2]
    assert got["undo"][2]["op"] == "undo" and got["undo"][2]["tid"] == it["id"] and got["undone"] == ["itm", False]
    # 10분 지난 카드는 서버에 보내지 않고 체크 그대로
    assert got["late"] == [0, True, "올린 지 10분이 지나 취소할 수 없어요", "itm xd"]
