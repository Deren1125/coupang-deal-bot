"""X 어그로 후보 (주인 11/17 — dashboard/xaggro.py · static/xaggro.js). 바깥 요청 없음 — 사이트·크롬·AI 는 가짜.

1) 속도 점수: 올린 때(목록 날짜 칸) → 시간당 댓글·추천·조회 · 못 읽으면 처음 본 뒤 늘어난 만큼 · 뜨는 주제 가산
2) 빼기: 정치·성인·비방(xcap) + 미성년자·범죄 피해·지목 고발 · AI 가 빼면 버림 · 본문을 베낀 줄은 초안에서 뺌
3) 카드: 캡처(가림) + 훅·요약·'출처: 사이트' 초안 + '왜 뜰까' · 점수 큰 순 · 하루 per_day · 한 번에 per_run
4) 막힌 출처(403)는 7일 쉼 · robots 가 막으면 안 씀 · 메모리 부족·xcap 이 도는 중이면 미룸 · 크롬은 한 번에 하나
5) [✓ 올렸어요] → 오늘 X 체크리스트 + xstudio posted (자동 게시 없음) · 화면 파일은 안전
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from typing import Any

import pytest

from dealbot.config import XAggroConfig, XCapConfig, XTodayConfig
from dealbot.dashboard import shell
from dealbot.dashboard import xaggro as xa
from dealbot.dashboard import xcap as xc
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from test_dash_xcap import STORY, nosleep, png, view_html
from test_dash_xtoday import NODE, STATIC, FakeDb, FakeNews, FakeRouter, at, needs_node

DAY = "2026-11-17"
NOW = at("13:21", DAY)
BB = xc.SOURCES["bobaedream"]


def row(no: int, title: str, when: str, comments: int, rec: int, views: int) -> str:
    return (f'<tr><td><a class="bsubject" href="/view?code=best&No={no}&vdate=">{title}</a><strong class="totreply">{comments}</strong></td>'
            f'<td class="author02"><span class="author">닉네임{no}</span></td><td class="date">{when}</td><td class="recomm">{rec}</td><td class="count">{views:,}</td></tr>')


def listing(*rows: str) -> str:
    return "<table><tbody>" + "".join(rows) + "</tbody></table>"


# 1001 = 2시간 전 · 빠름 / 1002 = 어제 글 · 쌓인 숫자는 가장 큼(느림) / 1003 = 1시간 전 · 뜨는 주제 / 1004 = 정치 / 1005 = 미성년자 / 1006 = 지목 고발
LIST = listing(row(1001, "친구랑 동업하면 안 되는 이유", "11:21", 120, 88, 20000),
               row(1002, "어제부터 쌓인 글", "11/16", 400, 300, 90000),
               row(1003, "붕어빵 가격 근황", "12:21", 40, 30, 3000),
               row(1004, "국회 의원 또 막말", "13:00", 300, 99, 9000),
               row(1005, "요즘 중학생 용돈 수준", "13:00", 300, 99, 9000),
               row(1006, "이 가게 먹튀 당했습니다 상호 공개", "13:00", 300, 99, 9000))
DRAFT = {"ok": True, "why": "", "hook": "친구랑 동업을 하면 안 되는 이유", "body": "처음엔 다 좋다고 시작함\n돈 얘기 나오는 순간부터 달라짐", "angle": "누구나 한 번쯤 들어 본 돈 문제"}


class Get:
    def __init__(self, pages: dict[str, tuple[int, str]] | None = None, robots: tuple[int, str] = (200, "User-agent: *\nAllow: /\n")) -> None:
        self.pages, self.robots, self.calls = pages or {}, robots, []

    async def __call__(self, url: str) -> tuple[int, str]:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return self.robots
        if url == BB.list_url:
            return self.pages.get("list", (200, LIST))
        if "ppomppu" in url:
            return self.pages.get("ppomppu", (403, ""))  # 서버에서 뽐뿌는 403
        m = re.search(r"No=(\d+)", url)
        return self.pages.get(m.group(1) if m else url, (200, view_html("제목", STORY)))


class Ask:
    def __init__(self, out: dict[str, Any] | None = None) -> None:
        self.out, self.calls = out or DRAFT, []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.out


class Desk:
    def __init__(self, tmp_path: Any, *, get: Get | None = None, ask: Ask | None = None, shot: bytes | None = None, trends: list[tuple[int, str]] | None = None,
                 **cfg: Any) -> None:
        self.now, self.hold, self.shots = [NOW], [""], []
        self.db, self.get, self.ask = FakeDb(), get or Get(), ask or Ask()
        self.studio = xs.Studio(self.db, ask=self.ask, clock=lambda: self.now[0])
        self.today = xt.XToday(self.db, self.studio, cfg=lambda: XTodayConfig(goal_posts=False), router=FakeRouter, news=FakeNews(None), clock=lambda: self.now[0])
        self.studio.on_posted = self.today.mark_posted

        async def shoot(url: str, src: xc.Source) -> bytes | None:
            self.shots.append(url)
            return shot

        self.cap = xc.XCap(self.db, self.studio, self.today, cfg=lambda: XCapConfig(sources=["bobaedream"]), get=self.get, shoot=shoot, img_dir=tmp_path / "xcap",
                           ua="dealbot/1.0", clock=lambda: self.now[0], sleep=nosleep)
        conf = XAggroConfig(**{"sources": ["bobaedream"], **cfg})
        self.d = xa.XAggro(self.db, self.studio, self.today, self.cap, cfg=lambda: conf, img_dir=tmp_path / "xaggro", trends=lambda now: trends or [],
                           hold=lambda: self.hold[0], clock=lambda: self.now[0], sleep=nosleep)


# ---------------------------------------------------------------- 1) 속도 점수
def test_post_time_is_read_from_the_list_date_cell() -> None:
    assert xa.post_ts("11:21", NOW) == at("11:21", DAY) and xa.post_ts("11:21:30", NOW) == at("11:21", DAY)
    assert xa.post_ts("23:50", NOW) == at("23:50", "2026-11-16")  # 아직 안 온 시각 = 어제
    assert xa.post_ts("11/16", NOW) == at("12:00", "2026-11-16") and xa.post_ts("11/17", NOW) is None  # 오늘인데 시각이 없으면 모름
    assert xa.post_ts("2026-11-17 09:05:00", NOW) == at("09:05", DAY) and xa.post_ts("26.11.16 21:00:01", NOW) == at("21:00", "2026-11-16")
    assert xa.post_ts("", NOW) is None and xa.post_ts("공지", NOW) is None and xa.post_ts("99:99", NOW) is None and xa.post_ts("2027-01-01 10:00", NOW) is None
    assert xa.when_map(BB, LIST, NOW)["https://www.bobaedream.co.kr/view?code=best&No=1001&vdate="] == at("11:21", DAY)


def test_velocity_is_per_hour_since_posting_or_since_first_seen() -> None:
    c = {"comments": 120, "rec": 88, "views": 20000}
    v = xa.velocity(c, at("11:21", DAY), None, NOW)
    assert v == {"h": 2.0, "by": "post", "c": 60.0, "r": 44.0, "v": 10000.0} and xa.score(v) == 60 * 3 + 44 * 2 + 100
    assert xa.velocity(c, NOW - 60, None, NOW)["h"] == 0.5  # 방금 글이 숫자로 튀지 않게
    assert xa.velocity(c, None, None, NOW) is None and xa.velocity(c, None, [NOW - 300, 0, 0, 0], NOW) is None  # 처음 봤거나 본 지 5분
    seen = xa.velocity(c, None, [NOW - 7200, 100, 80, 10000], NOW)
    assert seen == {"h": 2.0, "by": "seen", "c": 10.0, "r": 4.0, "v": 5000.0}
    assert xa.boost_of(1) == 0.5 and xa.boost_of(10) == pytest.approx(0.05) and xa.boost_of(11) == 0 and xa.score(v, 0.5) == 552.0
    assert xa.trend_hit("붕어빵 가격 근황", [(2, "날씨"), (3, "#붕어빵")]) == (3, "#붕어빵") and xa.trend_hit("다른 글", [(1, "붕어빵")]) is None
    why = xa.reason({"vel": v, "trend": [3, "붕어빵"]})
    assert why == "올린 지 2시간 · 1시간에 댓글 60 · 추천 44 · 조회 10,000 · 지금 뜨는 주제 3위 '붕어빵'"
    assert xa.reason({"vel": seen}).startswith("최근 2시간 동안")


async def test_candidates_rank_by_velocity_not_by_totals_and_drop_risky_titles(tmp_path: Any) -> None:
    k = Desk(tmp_path, trends=[(1, "붕어빵")])
    ss, seen = k.cap.src_state(NOW), {}
    got = await k.d.candidates(ss, seen, NOW)
    # 1002 (어제 12시 = 25시간 전)은 max_age_h 24 로 빠지고 · 정치·미성년자·지목 고발 제목은 열지도 않음
    assert [re.search(r"No=(\d+)", c["url"]).group(1) for _s, c in got] == ["1001", "1003"]
    assert got[0][1]["score"] == 368.0 and got[1][1]["score"] == round((40 * 3 + 30 * 2 + 30) * 1.5, 1) and got[1][1]["trend"] == [1, "붕어빵"]
    assert len(seen) == 6 and k.get.calls == ["https://www.bobaedream.co.kr/robots.txt", BB.list_url]  # 목록 1번만
    k = Desk(tmp_path, max_age_h=48)
    got = await k.d.candidates(k.cap.src_state(NOW), {}, NOW)
    assert [re.search(r"No=(\d+)", c["url"]).group(1) for _s, c in got] == ["1001", "1003", "1002"]  # 쌓인 숫자는 1002 가 가장 크지만 속도는 꼴찌


async def test_unknown_post_time_waits_for_the_next_look(tmp_path: Any) -> None:
    nodate = listing(row(1001, "친구랑 동업하면 안 되는 이유", "", 120, 88, 20000))
    k = Desk(tmp_path, get=Get({"list": (200, nodate)}))
    assert await k.d.run() == [] and k.ask.calls == [] and "속도를 잴 글" in k.d.view()["err"]
    k.get.pages["list"] = (200, listing(row(1001, "친구랑 동업하면 안 되는 이유", "", 240, 188, 60000)))
    k.now[0] = NOW + 4 * 3600
    made = await k.d.run()
    assert len(made) == 1 and made[0]["why"].startswith("최근 4시간 동안 · 1시간에 댓글 30 · 추천 25 · 조회 10,000")


# ---------------------------------------------------------------- 2) 빼기
def test_risk_rules_cover_minors_victims_and_named_accusations() -> None:
    for bad in ("요즘 중학생 용돈 수준", "미성년자 알바 썰", "폭행 피해자입니다", "이 가게 먹튀 당했습니다", "사장 실명 깝니다", "직장 동료 신상 박제함",
                "국회 의원 또 막말", "블랙박스 제보합니다 번호판 그대로", "사기 당한 후기"):
        assert xa.risk_why(bad), bad
    assert xa.risk_why("친구랑 동업하면 안 되는 이유", STORY) == "" and xa.risk_why("붕어빵 가격 근황") == ""
    assert "미성년자" in xa.SYSTEM and "명예훼손" in xa.SYSTEM and "개인정보" in xa.SYSTEM and "피해자" in xa.SYSTEM and "성인" in xa.SYSTEM


def test_draft_is_hook_summary_and_source_line_without_copied_sentences() -> None:
    src = "제목\n" + STORY
    text = xa.compose_text(DRAFT["hook"], DRAFT["body"], "보배드림", src)
    assert text == "친구랑 동업을 하면 안 되는 이유\n\n처음엔 다 좋다고 시작함\n돈 얘기 나오는 순간부터 달라짐\n\n출처: 보배드림"
    copied = xa.compose_text("이거 보고 한참 생각함", "고등학교 친구랑 가게를 같이 차렸는데 처음엔 좋았음.\n결국 남는 건 없었다는 얘기", "보배드림", src)
    assert copied == "이거 보고 한참 생각함\n\n결국 남는 건 없었다는 얘기\n\n출처: 보배드림"  # 본문을 그대로 옮긴 줄은 뺌
    assert xa.copied("돈 얘기 나오니까 서로 말이 달라지더라", STORY) and not xa.copied("돈 앞에서는 친구도 없음", STORY)
    assert xa.compose_text("", "요약", "보배드림", src) == "" and xa.compose_text("@누구 이거 봐", "", "보배드림", src) == ""
    assert xa.compose_text("이거 봐 https://a.example", "", "보배드림", src) == "" and xa.compose_text("#동업 썰", "", "보배드림", src) == ""
    assert xa.compose_text("가해자 신상 나옴", "", "보배드림", src) == ""
    long = xa.compose_text("훅 한 줄", "\n".join("가나다라마바사아자차카타파하" * 6 for _ in range(3)), "보배드림", src)
    assert xs.x_len(long) <= xs.X_LIMIT and long.startswith("훅 한 줄") and long.endswith("출처: 보배드림")
    assert xa.compose_text("훅", "출처: 다른 곳\n요약 한 줄", "뽐뿌", src) == "훅\n\n요약 한 줄\n\n출처: 뽐뿌"


async def test_body_rules_and_ai_veto_drop_the_candidate(tmp_path: Any) -> None:
    k = Desk(tmp_path, get=Get({"1001": (200, view_html("t", "우리 반 초등학생 아이 이야기인데 " + STORY)), "1003": (200, view_html("t", "짧음"))}))
    assert await k.d.run() == [] and k.ask.calls == [] and k.shots == []  # 본문에 미성년자 · 본문 없음 → AI 도 캡처도 안 함
    k = Desk(tmp_path, ask=Ask({"ok": False, "why": "일반인 사연", "hook": "", "body": "", "angle": ""}), shot=png(800))
    assert await k.d.run() == [] and len(k.ask.calls) == 2 and k.shots == [] and k.d.view()["items"] == []  # AI 가 빼면 캡처도 안 함
    assert len(k.cap.src_state(NOW)["used"]) == 2  # 버린 글은 다시 안 봄 (xcap 과 같은 목록)


# ---------------------------------------------------------------- 3) 카드
async def test_card_has_blurred_capture_draft_reason_and_sorts_by_score(tmp_path: Any) -> None:
    async def shoot(url: str, src: xc.Source) -> Any:
        return png(1000), [[10, 10, 80, 20, "nick"], [10, 60, 120, 20, "phone"], [10, 90, 90, 20, "ip"]], 1.0

    k = Desk(tmp_path, trends=[(1, "붕어빵")])
    k.cap.shoot = shoot
    made = await k.d.run()
    assert [i["title"] for i in made] == ["제목", "제목"] and ["No=1001" in made[0]["url"], "No=1003" in made[1]["url"]] == [True, True]
    system, user = k.ask.calls[0]
    assert "ok=false" in system and "[사이트] 보배드림" in user and "고등학교 친구랑" in user and "1시간에 댓글 60" in user
    v = k.d.view()
    assert v["made"] == 2 and v["per_day"] == 6 and [i["score"] for i in v["items"]] == [368.0, 315.0] and v["pool"] == 2
    it = v["items"][0]
    assert it["text"].startswith("친구랑 동업을 하면 안 되는 이유\n\n") and it["text"].endswith("\n\n출처: 보배드림") and it["blur"] == 3
    assert it["why"].startswith("올린 지 2시간 · 1시간에 댓글 60") and it["angle"] == DRAFT["angle"] and it["done"] is False
    assert "지금 뜨는 주제 1위 '붕어빵'" in v["items"][1]["why"]
    assert it["imgs"] == [f"/dashboard/api/xaggro/img/{it['id']}-1.jpg"] and k.d.img(f"{it['id']}-1.jpg")[:2] == b"\xff\xd8"
    assert k.d.img("../x.jpg") is None and k.d.img(f"{it['id']}-3.jpg") is None and k.d.img("c123456789-1.jpg") is None
    assert k.studio._left("draft", xs.DRAFT_PER_DAY) == xs.DRAFT_PER_DAY - 2
    assert k.today.plan(NOW)["items"] == []  # 올렸다고 누르기 전에는 체크리스트에 안 들어감
    assert (await k.d.run()) == []  # 같은 글은 다시 안 씀
    status, v2 = k.d.skip({"id": it["id"]})
    assert status == 200 and [i["id"] for i in v2["items"]] == [made[1]["id"]]
    with pytest.raises(xs.StudioError):
        k.d.skip({"id": "a000000000"})


async def test_no_browser_falls_back_to_a_text_card_with_scrubbed_contacts(tmp_path: Any) -> None:
    k = Desk(tmp_path, shot=None, per_run=1)
    made = await k.d.run()
    assert len(made) == 1 and made[0]["n"] == 1 and made[0]["blur"] == 1  # STORY 의 전화번호 한 곳
    from dealbot.dashboard import xblur

    assert xblur.scrub("ip 123.45.67.89 / 211.36.*.* 에서 씀 (버전 1.2)") == "ip ●●●●●●●● / ●●●●●●●● 에서 씀 (버전 1.2)"
    assert "x{1,3}" in xc.PII_JS  # 크롬 캡처에서도 본문에 적힌 IP 는 ● → 모자이크


async def test_per_run_and_per_day_caps_and_min_score(tmp_path: Any) -> None:
    k = Desk(tmp_path, per_run=1, per_day=1)
    assert len(await k.d.run()) == 1 and len(k.ask.calls) == 1
    assert await k.d.run() == [] and len(k.ask.calls) == 1 and k.d.due(NOW) == "" and k.d.view()["next"] == ""
    with pytest.raises(xs.StudioError) as e:
        k.d.make()
    assert e.value.code == "full"
    k = Desk(tmp_path, min_score=350)
    assert len(await k.d.run()) == 1 and "No=1003" not in " ".join(k.get.calls)  # 315점은 열지도 않음
    assert XAggroConfig().per_day == 6 and XAggroConfig().per_run == 2 and XAggroConfig().min_free_mb == 400
    with pytest.raises(ValueError):
        XAggroConfig(times=["9:20"])
    with pytest.raises(ValueError):
        XAggroConfig(per_run=9)


# ---------------------------------------------------------------- 4) 막힘 · 메모리
async def test_blocked_or_disallowed_sources_rest_without_retry(tmp_path: Any) -> None:
    k = Desk(tmp_path, sources=["ppomppu", "bobaedream"], per_run=1)
    assert len(await k.d.run()) == 1  # 뽐뿌 403 → 쉬고 보배드림으로
    assert k.cap.src_state(NOW)["off"]["ppomppu"] == pytest.approx(NOW + xc.OFF_S) and k.d.view()["off"] == ["뽐뿌"]
    n = sum("ppomppu" in u for u in k.get.calls)
    await k.d.run()
    assert sum("ppomppu" in u for u in k.get.calls) == n  # 7일 동안 다시 부르지 않음
    k = Desk(tmp_path, get=Get(robots=(200, "User-agent: *\nDisallow: /\n")))
    assert await k.d.run() == [] and k.get.calls == ["https://www.bobaedream.co.kr/robots.txt"] and k.ask.calls == []
    k = Desk(tmp_path, get=Get({"list": (200, "<html>Just a moment... cf-chl</html>")}))
    await k.d.run()
    assert k.cap.src_state(NOW)["off"]["bobaedream"] == pytest.approx(NOW + xc.OFF_S)  # 사람 확인 화면 = 막힘 (우회 안 함)


async def test_tick_waits_on_memory_pressure_and_while_xcap_runs(tmp_path: Any) -> None:
    k = Desk(tmp_path, per_run=1, times=["09:20", "13:20", "17:20"])
    k.now[0] = at("09:00", DAY)
    assert k.d.due(k.now[0]) == "" and k.d.tick() is False
    k.now[0] = NOW
    k.hold[0] = "서버 메모리가 모자라 쉬는 중이에요"
    assert k.d.tick() is False and k.d.plan(NOW)["runs"] == {} and k.d.view()["hold"]  # 그 시각은 남겨 둠
    with pytest.raises(xs.StudioError) as e:
        k.d.make()
    assert e.value.code == "mem"
    k.hold[0] = ""
    k.cap.bg = asyncio.ensure_future(asyncio.sleep(0.01))
    assert k.d.tick() is False  # xcap 이 도는 중
    await k.cap.bg
    assert k.d.tick() is True and k.d.tick() is False  # 도는 중
    await k.d.bg
    assert k.d.view()["made"] == 1 and sorted(k.d.plan(NOW)["runs"]) == ["09:20", "13:20"] and k.d.due(NOW) == ""  # 지나간 시각은 한꺼번에
    assert k.d.view()["next"] == "17:20"
    # 캡처 바로 앞에서 메모리가 모자라면 남은 후보는 안 연다
    k = Desk(tmp_path)
    k.hold[0] = "쉼"
    assert await k.d.run() == [] and k.d.view()["err"] == "쉼" and not any("No=" in u for u in k.get.calls)


def test_mem_hold_reads_the_watchdog_flag_and_free_memory(tmp_path: Any) -> None:
    flag = tmp_path / "mem_pressure"
    assert xa.mem_hold(str(flag), 0) == "" and xa.mem_hold("", 0) == ""
    flag.write_text("1")
    assert "메모리" in xa.mem_hold(str(flag), 0)
    os.utime(flag, (1, 1))  # 오래된 표시 = 감시꾼이 멈춘 것 → 무시
    assert xa.mem_hold(str(flag), 0) == ""
    assert "250MB" in xa.mem_hold("", 400, free_mb=lambda: 250) and xa.mem_hold("", 400, free_mb=lambda: 900) == "" and xa.mem_hold("", 400, free_mb=lambda: None) == ""


async def test_register_serializes_chrome_between_xcap_and_xaggro(tmp_path: Any) -> None:
    live, peak, routes = [0], [0], {}

    async def shoot(url: str, src: xc.Source) -> bytes | None:
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        await asyncio.sleep(0.01)
        live[0] -= 1
        return png(400)

    class Web:
        def route(self, path: str, fn: Any, **kw: Any) -> None:
            routes[path] = fn

        route_prefix = route_json = route

    k = Desk(tmp_path)
    k.cap.shoot = shoot
    settings = type("S", (), {"dashboard": type("D", (), {"x_aggro": XAggroConfig(mem_pressure_path="", min_free_mb=0)})(), "data_dir": str(tmp_path)})()
    trend = type("T", (), {"state": lambda self, now: {"at": now, "rows": [{"kw": "붕어빵", "rank": 1}, {"kw": "숨김", "rank": 2, "hidden": "정치"}, {"kw": "해외", "rank": 0}]}})()
    api = type("A", (), {"bot": type("B", (), {"settings": settings})(), "dash": type("H", (), {"db": k.db})()})()
    desk = xa.register(Web(), api, k.studio, k.today, k.cap, trend)
    assert set(routes) == {xa.STATE_PATH, xa.IMG_PREFIX, xa.MAKE_PATH, xa.SKIP_PATH, xa.POSTED_PATH}
    assert desk.trends(NOW) == [(1, "붕어빵")] and desk.hold() == ""
    await asyncio.gather(*(k.cap.shoot("u", BB) for _ in range(3)))
    assert peak[0] == 1


# ---------------------------------------------------------------- 5) 올렸어요 · 화면
async def test_posted_feeds_the_x_checklist_and_can_be_undone(tmp_path: Any) -> None:
    k = Desk(tmp_path, per_run=1, shot=png(600))
    (item,) = await k.d.run()
    status, v = k.d.posted({"id": item["id"], "op": "add"})
    assert status == 200 and v["items"][0]["done"] is True and v["items"][0]["undo_s"] == 600
    tid = xt.item_id(f"xaggro:{item['id']}")
    rows = k.today.plan(NOW)["items"]
    assert [(r["id"], r["kind"], r["keyword"]) for r in rows] == [(tid, "mine", "어그로 후보 · 보배드림")] and rows[0]["res"]["drafts"][0]["text"] == item["text"]
    assert "사진 1장" in rows[0]["res"]["drafts"][0]["angle"]
    assert [r["topic"] for r in k.studio.posted_rows()] == [tid] and next(i for i in k.today.view()["items"] if i["id"] == tid)["done"] is True
    k.d.posted({"id": item["id"], "op": "add"})
    assert len(k.studio.posted_rows()) == 1  # 한 번만 셈
    _s, v = k.d.posted({"id": item["id"], "op": "undo"})
    assert v["items"][0]["done"] is False and k.studio.posted_rows() == []
    with pytest.raises(xs.StudioError) as e:
        k.d.posted({"id": "a000000000"})
    assert e.value.code == "gone"


def test_module_never_posts_or_logs_in_to_x_and_keeps_the_writer_model_rule() -> None:
    src = (STATIC.parent / "xaggro.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for banned in ("x.com", "twitter.com", "api.twitter", "cookie", "login(", "password", "httpx", "playwright", "intent/"):
        assert banned not in code.lower(), banned
    assert "self.studio.ask(SYSTEM" in code and "JsonAsker" not in code and "anthropic" not in code.lower()  # 글은 Studio.ask 한 길로만 (Opus 까지 · Fable 금지)
    fable = "-".join(["claude", "fable", "5", "2"])
    assert not xs.model_ok(fable)
    with pytest.raises(ValueError):
        xs.assert_writer_model(fable)


def test_xaggro_js_is_local_safe_and_loaded_by_shell() -> None:
    js = (STATIC / "xaggro.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "xaggro.js")], capture_output=True).returncode == 0
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "innerHTML" not in code and "eval(" not in code and ".click()" not in code and re.findall(r"https?://[^\s'\"`)]+", code) == []
    assert "intent" not in code and "x.com" not in code  # X 로 넘기는 링크도 없음 — 복사해서 직접
    for label in ("어그로 후보", "글 복사", "사진 저장", "왜 뜰까", "✓ 올렸어요", "올리기 전에 확인", "안 써요", "지금 고르기", "가림은 어림"):
        assert label in code, label
    assert f'<script defer src="{shell.asset_url("xaggro.js")}"></script>' in shell.page({})


@needs_node
async def test_xaggro_js_card_sits_below_the_capture_card_and_ticks_posted(tmp_path: Any) -> None:
    from test_dash_xtrend import SHIM

    k = Desk(tmp_path, shot=png(1000), per_run=1)
    await k.d.run()
    tail = r"""
const calls = [];
global.fetch = async (u, o) => { if (o && o.method === 'POST') { calls.push([u, o.headers['X-Dash-Token'], JSON.parse(o.body)]); state.items[0].done = true; state.items[0].undo_s = 600; } return {status: 200, json: async () => state}; };
const clip = [];
Object.defineProperty(global, 'navigator', {value: {clipboard: {writeText: async t => { clip.push(t); }}}, configurable: true});
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };
const text = n => n.textContent;
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach(c => all(c, f, out)); return out; };
(async () => {
  const xrc = mk('article'); xrc.attrs.id = 'xrc'; xtc.after(xrc);
  const xcc = mk('article'); xcc.attrs.id = 'xcc'; xrc.after(xcc);
  eval(fs.readFileSync(src, 'utf8'));
  await tick();
  const c = byId(main, 'xag');
  const out = {order: main.children.map(x => x.attrs.id || x.tagName), text: text(c),
    imgs: all(c, n => n.tagName === 'IMG').map(n => n.attrs.src), saves: all(c, n => n.tagName === 'A' && 'download' in n.attrs).map(n => n.attrs.href)};
  all(c, n => n.tagName === 'BUTTON' && text(n) === '글 복사')[0].listeners.click();
  const box = all(c, n => n.tagName === 'INPUT')[0];
  box.checked = true;
  await box.listeners.change({target: box});
  await tick();
  out.clip = clip; out.calls = calls; out.today = today; out.after = text(byId(main, 'xag')); out.toast = text(toast);
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
    shim = SHIM.split("const clip = [];")[0] + tail
    v = k.d.view()
    res = subprocess.run([NODE, "-e", shim, str(STATIC / "xaggro.js"), "x", json.dumps(v, ensure_ascii=False), "#/sns/x"], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["order"] == ["snt", "xsc", "xtc", "xrc", "xcc", "xag", "DIV"]
    for want in ("어그로 후보 · 오늘 1/6", "왜 뜰까 · 올린 지 2시간", "출처: 보배드림", "올리기 전에 확인", "가린 곳 0곳", "✓ 올렸어요", "사진 저장"):
        assert want in out["text"], want
    assert out["imgs"] == out["saves"] and out["imgs"][0].startswith("/dashboard/api/xaggro/img/a")
    assert out["clip"] == [v["items"][0]["text"]]
    assert out["calls"] == [["/dashboard/api/xaggro/posted", "tok", {"id": v["items"][0]["id"], "op": "add"}]] and out["today"] == ["load"]
    assert "오늘 X 체크리스트에 넣었어요" in out["toast"] and (out["text"].count("안 써요"), out["after"].count("안 써요")) == (2, 1)  # 올린 카드는 [안 써요] 단추가 빠짐 (확인 글의 한 번만 남음)
