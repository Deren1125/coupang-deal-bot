"""제휴 수익 (주인 10/23 — 통계 탭 '제휴 수익'): 쿠팡 리포트 읽기·저장·30일 창 · 직접 넣기 확인·고치기·지우기 · 합계 글 ·
쇼핑커넥트 '확인 준비 중' 자리 · earn.js 크기·안전."""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from dash_helpers import blogauto_status, fixture, post_json, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.coupang.client import ApiBudget, CoupangClient
from dealbot.dashboard import earn, shell, webapp
from dealbot.dashboard.blogauto import _earnings
from dealbot.storage.db import Database
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

REPORTS = json.loads((Path(__file__).parent / "fixtures" / "coupang_reports.json").read_text(encoding="utf-8"))
TZ = "Asia/Seoul"
NOW = datetime(2026, 10, 23, 15, 0, tzinfo=ZoneInfo(TZ)).timestamp()
TODAY = date(2026, 10, 23)
STATIC = webapp.STATIC_DIR
EARN_JS_MAX = 8 * 1024
EARN_JS_GZIP_MAX = 3 * 1024 + 256


class Clock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def api_client(handler, budget: ApiBudget | None = None) -> CoupangClient:  # type: ignore[no-untyped-def]
    return CoupangClient("AK", "SK", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), max_retries=1, retry_backoff=0.01, budget=budget)


def reports_handler(calls: list[httpx.Request], data: dict | None = None, fail: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    data = data if data is not None else REPORTS

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        name = req.url.path.rsplit("/", 1)[-1]
        if fail and name in fail:
            return httpx.Response(fail[name], text="no")
        return httpx.Response(200, json={"rCode": "0", "rMessage": "", "data": data.get(name, [])})

    return handler


def make(db: Database, client: CoupangClient | None, clock: Clock, budget: ApiBudget | None = None) -> tuple[earn.EarnStore, earn.EarnCoupang]:
    store = earn.EarnStore(db)
    return store, earn.EarnCoupang(store, lambda: client, lambda: budget, TZ, clock)


# ---------------------------------------------------------------- 쿠팡 리포트 해석
def test_parse_coupang_reports_days_orders_cancels_and_commission() -> None:
    p = earn.parse_coupang(REPORTS, TODAY)
    d = p["days"]
    assert d[("2026-10-22", "dealbot")] == {"clicks": 40, "orders": 1, "amount": 71800, "cancel_amount": 0, "commission": 2154, "order_commission": 2154}
    assert d[("2026-10-21", "dealbot")]["cancel_amount"] == 9900 and d[("2026-10-21", "dealbot")]["commission"] == 0
    assert d[("2026-10-21", "")]["clicks"] == 5 and d[("2026-10-21", "")]["commission"] == 450
    names = {o["product"]: o for o in p["orders"]}
    assert set(names) == {"무선 청소기 헤드 세트", "청소기 필터", "방수 양말 3족", "주방 저울"}
    assert names["방수 양말 3족"]["cancelled"] == 1 and names["주방 저울"]["cancelled"] == 0
    assert len({o["okey"] for o in p["orders"]}) == 4
    # 수익 리포트가 없는 계정: 주문 줄의 수익 합
    p2 = earn.parse_coupang({k: v for k, v in REPORTS.items() if k != "commission" and not k.startswith("_")}, TODAY)
    assert p2["days"][("2026-10-22", "dealbot")]["commission"] == 2154
    assert earn.parse_coupang({}, TODAY) == {"days": {}, "orders": []}
    # 이상한 값은 0
    odd = earn.parse_coupang({"orders": [{"date": "20261022", "gmv": "Infinity", "commission": "x", "productName": "A"}]}, TODAY)
    assert odd["days"][("2026-10-22", "")]["amount"] == 0


# ---------------------------------------------------------------- 읽기·저장·30일 창
async def test_fetch_stores_thirty_day_window_and_replaces_stale_rows(db: Database) -> None:
    calls: list[httpx.Request] = []
    clock = Clock()
    store, ec = make(db, api_client(reports_handler(calls)), clock)
    db.kv_set(earn.K_NEXT, "0")
    store.replace_coupang({"days": {("2026-09-01", ""): {"clicks": 1, "orders": 0, "amount": 0, "cancel_amount": 0, "commission": 0, "order_commission": 0},
                                    ("2026-10-10", "old"): {"clicks": 9, "orders": 1, "amount": 100, "cancel_amount": 0, "commission": 5, "order_commission": 5}},
                           "orders": []}, "2026-09-01", "2026-10-23", "x")
    assert await ec.tick() == "ok"
    assert [r.url.path.rsplit("/", 1)[-1] for r in calls] == ["clicks", "orders", "cancels", "commission"]
    q = calls[0].url.params
    assert (q["startDate"], q["endDate"]) == ("20260924", "20261023")  # 오늘 포함 30일
    rows = store.coupang_days("2026-09-24", "2026-10-23")
    assert {(r["day"], r["sub_id"]) for r in rows} == {("2026-10-22", "dealbot"), ("2026-10-21", "dealbot"), ("2026-10-21", "")}  # 창 안의 묵은 줄은 지워짐
    assert [r["day"] for r in db._q("SELECT day FROM earn_coupang WHERE day < '2026-09-24'")] == ["2026-09-01"]  # 창 밖은 건드리지 않음
    assert len(store.coupang_orders("2026-09-24", 10)) == 4
    # 3시간 안에는 다시 읽지 않고, 지나면 읽음 (같은 값을 다시 써도 줄이 늘지 않음)
    clock.t += 3 * 3600 - 5
    assert await ec.tick() is None and len(calls) == 4
    clock.t += 10
    assert await ec.tick() == "ok" and len(calls) == 8
    assert len(store.coupang_orders("2026-09-24", 10)) == 4 and db.kv_get(earn.K_ERR) == ""


async def test_fetch_failure_keeps_old_rows_retries_in_an_hour_and_secret_free(db: Database) -> None:
    calls: list[httpx.Request] = []
    clock = Clock()
    store, ec = make(db, api_client(reports_handler(calls)), clock)
    assert await ec.tick() == "ok"
    ec.client_of = lambda: api_client(reports_handler(calls, fail={"orders": 503}))
    clock.t += 3 * 3600 + 1
    n = len(calls)
    assert await ec.tick() == "error" and len(calls) == n + 2  # clicks 다음 orders 에서 멈춤
    assert len(store.coupang_orders("2026-09-24", 10)) == 4  # 묵은 값 그대로
    assert "503" in (db.kv_get(earn.K_ERR) or "") or "Error" in (db.kv_get(earn.K_ERR) or "")
    assert "AK" not in (db.kv_get(earn.K_ERR) or "") and "SK" not in (db.kv_get(earn.K_ERR) or "")
    clock.t += 3000
    assert await ec.tick() is None  # 1시간 전
    clock.t += 700
    ec.client_of = lambda: api_client(reports_handler(calls))
    assert await ec.tick() == "ok"


async def test_fetch_cancels_or_commission_unavailable_is_not_fatal_and_no_client_is_quiet(db: Database) -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        name = req.url.path.rsplit("/", 1)[-1]
        if name in ("cancels", "commission"):
            return httpx.Response(200, json={"rCode": "403", "rMessage": "no permission"})
        return httpx.Response(200, json={"rCode": "0", "data": REPORTS[name]})

    store, ec = make(db, api_client(handler), Clock())
    assert await ec.tick() == "ok"
    assert store.coupang_days("2026-10-22", "2026-10-22")[0]["commission"] == 2154  # 주문 줄에서
    _, none = make(db, None, Clock())
    assert await none.tick() is None


async def test_fetch_respects_budget_and_daily_cap(db: Database) -> None:
    calls: list[httpx.Request] = []
    budget = ApiBudget(10, reserve={"deeplink": 3})
    for _ in range(9):
        budget.record("deeplink")
        budget.record("general")  # 일반 몫(7)을 거의 다 씀
    clock = Clock()
    store, ec = make(db, api_client(reports_handler(calls), budget), clock, budget)
    assert await ec.tick() == "budget" and calls == []
    assert float(db.kv_get(earn.K_NEXT) or 0) == clock.t + 600
    day = TODAY.isoformat()
    db.kv_set(earn.K_NEXT, "0")
    db.kv_set(earn.K_CALLS.format(day=day), "38")
    store2, ec2 = make(db, api_client(reports_handler(calls)), clock)
    assert await ec2.tick() == "daily_cap" and calls == []


# ---------------------------------------------------------------- 직접 넣기
def good(**kw: object) -> dict:
    return {"shop": "토스쇼핑", "day": "2026-10-22", "product": "양말", "amount": "12,900", "commission": "300", "memo": "", **kw}


def test_manual_validation() -> None:
    c = earn.EarnStore.clean_manual
    assert c(good(), TODAY) == {"shop": "토스쇼핑", "day": "2026-10-22", "product": "양말", "amount": 12900, "commission": 300, "memo": ""}
    assert c(good(amount="", commission="500", product=""), TODAY)["amount"] == 0  # 수익만도 됨
    for bad, text in ((dict(shop="쿠팡"), "골라"), (dict(shop=""), "골라"), (dict(day="2026-13-01"), "날짜"), (dict(day="2026-10-24"), "날짜"),
                      (dict(day="2020-01-01"), "날짜"), (dict(amount="abc"), "금액"), (dict(amount="-5"), "금액"), (dict(amount="1.5"), "금액"),
                      (dict(commission="9" * 12), "수익"), (dict(amount="", commission=""), "금액이나"), (dict(amount="0", commission="0"), "금액이나"),
                      (dict(product="가" * 81), "80자"), (dict(memo="가" * 201), "200자")):
        with pytest.raises(earn.EarnError) as e:
            c(good(**bad), TODAY)
        assert text in str(e.value), (bad, str(e.value))
    assert c(good(amount=5000, commission=None), TODAY)["commission"] == 0  # 숫자로 와도 받음


def test_manual_crud(db: Database) -> None:
    store = earn.EarnStore(db)
    a = store.add_manual(store.clean_manual(good(), TODAY), "t1")
    b = store.add_manual(store.clean_manual(good(shop="무신사", day="2026-10-20", product="", amount="50000"), TODAY), "t1")
    assert [r["id"] for r in store.manual()] == [a, b]  # 날짜 새것부터
    assert store.update_manual(b, store.clean_manual(good(shop="29CM", day="2026-10-21", amount="1"), TODAY), "t2")
    assert store.manual(limit=5)[1]["shop"] == "29CM" and store.manual()[1]["updated_at"] == "t2"
    assert not store.update_manual(999, store.clean_manual(good(), TODAY), "t3")
    assert store.delete_manual(a) and not store.delete_manual(a) and [r["id"] for r in store.manual()] == [b]


async def test_manual_actions_through_the_api(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    blogauto_status(settings)
    h = session_headers(bot)

    n = [0]

    async def act(action: str, args: dict) -> tuple[int, dict]:
        n[0] += 1
        s, d, _ = await post_json(bot, "/dashboard/api/action", {"action": action, "args": args, "cid": f"c_test{n[0]:06d}"}, h)
        return s, d

    today = datetime.now(ZoneInfo(bot.settings.app.timezone)).date().isoformat()
    s, d = await act("earn.save", {"shop": "토스쇼핑", "day": today, "product": "양말", "amount": "12900", "commission": "300"})
    assert s == 200 and d["status"] == "ok" and d["text"] == "넣었어요", d
    s, d = await act("earn.save", {"shop": "쿠팡", "day": today, "amount": "100"})
    assert d["status"] != "ok" and "골라" in d["text"]
    s, d = await act("earn.save", {"shop": "에이블리", "day": "2999-01-01", "amount": "100"})
    assert d["status"] != "ok" and "날짜" in d["text"]
    (row,) = bot.earn_store.manual()
    s, d = await act("earn.save", {"id": row["id"], "shop": "에이블리", "day": today, "product": "바지", "amount": "20000", "commission": ""})
    assert d["status"] == "ok" and d["text"] == "고쳤어요" and bot.earn_store.manual()[0]["shop"] == "에이블리"
    s, d = await act("earn.save", {"id": 999, "shop": "에이블리", "day": today, "amount": "5"})
    assert d["status"] != "ok"
    st = await state(bot)
    assert st["earnings"]["manual"]["rows"][0]["id"] == row["id"] and "바지" in st["earnings"]["manual"]["rows"][0]["text"]
    s, d = await act("earn.delete", {"id": row["id"]})
    assert d["status"] == "ok" and d["text"] == "지웠어요" and bot.earn_store.manual() == []
    s, d = await act("earn.delete", {"id": row["id"]})
    assert d["status"] != "ok"
    s, d, _ = await post_json(bot, "/dashboard/api/action", {"action": "earn.save", "args": {"shop": "토스쇼핑"}, "cid": "c_xx12345678"}, h)
    assert s == 400  # 날짜가 없으면 모양부터 틀림
    s, d, _ = await post_json(bot, "/dashboard/api/action", {"action": "earn.save", "args": {"shop": "토스쇼핑", "day": today, "amount": "1"}},
                              session_headers(bot, csrf=False))
    assert s in (401, 403) and bot.earn_store.manual() == []  # 로그인·확인 값 없이는 못 넣음


# ---------------------------------------------------------------- 화면 값
def seeded(db: Database, with_sc: dict | None = None, sub_id_set: bool = True) -> dict:
    store = earn.EarnStore(db)
    for m in store.manual():  # 한 시험에서 여러 번 불러도 같은 모양
        store.delete_manual(m["id"])
    store.replace_coupang(earn.parse_coupang(REPORTS, TODAY), "2026-09-24", "2026-10-23", "t")
    store.add_manual(store.clean_manual(good(day="2026-10-23", amount="10000", commission="400"), TODAY), "t")
    store.add_manual(store.clean_manual(good(shop="무신사", day="2026-10-10", product="", amount="50000", commission="1500", memo="후드"), TODAY), "t")
    return earn.build(store, TZ, NOW, with_sc, coupang_on=True, sub_id_set=sub_id_set)


def test_build_totals_per_source_and_recent(db: Database) -> None:
    e = seeded(db)
    t = {x["label"]: x for x in e["totals"]}
    # 오늘(10/23): 쿠팡 없음(하루 늦게) + 직접 400 · 7일(10/17~): 쿠팡 2154+0+450 + 400 · 30일: + 1500
    assert t["오늘"] == {"label": "오늘", "orders": "1건", "amount": "10,000원", "commission": "400원"}
    assert t["7일"]["commission"] == "3,004원" and t["7일"]["orders"] == "4건"
    assert t["30일"]["commission"] == "4,504원" and t["30일"]["orders"] == "5건"
    assert t["7일"]["amount"] == "96,800원"  # 쿠팡 주문금액 71,800+9,900+15,000 - 취소 9,900 + 직접 10,000
    src = {s["key"]: s for s in e["sources"]}
    assert [s["label"] for s in e["sources"]] == ["쿠팡 (자동)", "쇼핑커넥트", "직접 넣은 것"]
    assert src["coupang"]["state"] == "ok" and "하루 늦게" in src["coupang"]["note"] or src["coupang"]["state"] == "wait"
    assert src["coupang"]["cells"][1]["commission"] == "2,604원"
    assert src["manual"]["cells"][2]["commission"] == "1,900원"
    cp = e["coupang"]
    assert cp["rows"][2]["clicks"] == "70회" and cp["rows"][2]["orders"] == "3건"
    assert [s["sub"] for s in cp["subs"]] == ["dealbot", "(서브아이디 없음)"]  # 수익 큰 순
    assert "같은 서브아이디" in cp["sub_note"]  # 이름 있는 서브아이디가 하나뿐 (나머지는 서브아이디 없는 클릭) → 플랫폼은 못 나눔
    two = {k: ([{**x, "subId": "tg"} if x.get("subId") == "" else x for x in v] if isinstance(v, list) else v) for k, v in REPORTS.items()}
    store2 = earn.EarnStore(Database(db.path.with_name("two.db")))
    store2.replace_coupang(earn.parse_coupang(two, TODAY), "2026-09-24", "2026-10-23", "t")
    assert earn.build(store2, TZ, NOW, None, coupang_on=True, sub_id_set=True)["coupang"]["sub_note"] == ""  # 이름 있는 서브아이디가 둘이면 나눠 보임
    r = e["recent"]
    assert r[0]["src"] == "토스쇼핑" and r[0]["day"] == "10/23" and r[0]["amount"] == "10,000원" and r[0]["id"] is not None
    assert {x["name"] for x in r} >= {"무선 청소기 헤드 세트", "방수 양말 3족", "(상품 이름 없음)"}
    assert next(x for x in r if x["name"] == "방수 양말 3족")["cancelled"] is True
    assert all(x["id"] is None for x in r if x["src"] == "쿠팡")
    assert len(e["manual"]["rows"]) == 2 and e["manual"]["shops"] == list(earn.SHOPS)
    assert "API" not in json.dumps(e, ensure_ascii=False) and "AK" not in json.dumps(e)


def test_single_sub_id_note(db: Database) -> None:
    one = {k: ([{**x, "subId": "dealbot"} for x in v] if isinstance(v, list) else v) for k, v in REPORTS.items()}
    store = earn.EarnStore(db)
    store.replace_coupang(earn.parse_coupang(one, TODAY), "2026-09-24", "2026-10-23", "t")
    e = earn.build(store, TZ, NOW, None, coupang_on=True, sub_id_set=True)
    assert "같은 서브아이디" in e["coupang"]["sub_note"] and "dealbot" not in json.dumps(e["coupang"]["sub_note"])  # 값은 보이지 않음
    assert "dealbot" not in e["coupang"]["sub_note"]
    e2 = earn.build(earn.EarnStore(Database(db.path.with_name("b.db"))), TZ, NOW, None, coupang_on=False, sub_id_set=False)
    assert e2["sources"][0]["state"] == "off" and e2["sources"][0]["note"] == "쿠팡 파트너스 키가 없어요"


def test_shopconnect_placeholder_gate_and_confirmed(db: Database) -> None:
    fx = fixture("export_v2.json")["earnings"]
    assert _earnings(fx)["shopconnect"]["confirmed"] is True
    off = _earnings({"shopconnect": {"confirmed": False, "read_at": None, "days": [], "orders": []}})
    e = seeded(db, off["shopconnect"])
    sc = e["sources"][1]
    assert sc["state"] == "wait" and sc["note"] == "쇼핑커넥트 실적: 확인 준비 중 (서버 점검 후 켜짐)"
    assert sc["cells"][2]["commission"] == "0원" and e["totals"][2]["commission"] == "4,504원"  # 켜지기 전엔 합계에 들어가지 않음
    assert seeded(db, None)["sources"][1]["note"] == earn.SC_NOFIELD  # 예전 Blog-Auto (칸 없음)
    # 꺼져 있는데 줄이 실려 와도 믿지 않음
    junk = _earnings({"shopconnect": {"confirmed": False, "days": [{"day": "2026-10-22", "commission": 9}], "orders": [{"day": "2026-10-22", "name": "x"}]}})
    assert junk["shopconnect"]["days"] == [] and seeded(db, junk["shopconnect"])["totals"][2]["commission"] == "4,504원"
    # 계약 파일의 켜진 예: 7일·30일 합계에 들어가고 최근 구매에 나옴
    on = _earnings(fx)["shopconnect"]
    on["days"] = [{**d, "day": d["day"].replace("2026-10-17", "2026-10-22").replace("2026-10-16", "2026-10-21")} for d in on["days"]]
    on["orders"] = [{**o, "day": o["day"].replace("2026-10-17", "2026-10-22")} for o in on["orders"]]
    e2 = seeded(db, on)
    assert e2["sources"][1]["state"] == "ok" and e2["sources"][1]["cells"][1]["commission"] == "3,225원"
    assert e2["totals"][1]["commission"] == "6,229원"
    assert any(x["src"] == "쇼핑커넥트" and x["name"] == "무선 청소기 헤드 세트" for x in e2["recent"])


def test_contract_reader_drops_bad_rows() -> None:
    fx = fixture("export_v2.json")["earnings"]
    got = _earnings(fx)["shopconnect"]
    assert got["confirmed"] is True and got["read_at"] and len(got["days"]) == 2 and got["orders"][0]["commission"] == 1794
    bad = json.loads(json.dumps(fx))
    bad["shopconnect"]["days"] += [{"day": "10/17", "commission": 1}, "x", {"day": "2026-10-15", "commission": -5, "orders": "3"}]
    bad["shopconnect"]["orders"] += [{"day": "bad", "name": "x"}, {"day": "2026-10-15", "name": ""}]
    got = _earnings(bad)["shopconnect"]
    assert len(got["days"]) == 3 and got["days"][2] == {"day": "2026-10-15", "clicks": 0, "orders": 0, "amount": 0, "commission": 0}
    assert got["orders"][-1]["name"] == "(상품 이름 없음)"
    for v in (None, {}, {"shopconnect": None}, {"shopconnect": {"confirmed": "yes"}}, []):
        assert _earnings(v) is None


async def test_state_has_earnings_and_old_export_still_works(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    e = (await state(bot))["earnings"]
    assert e["sources"][1]["state"] == "ok" and e["totals"][0]["label"] == "오늘" and e["manual"]["rows"] == []
    write_export(tmp_path / "blogauto.json", earnings={"shopconnect": {"confirmed": False, "read_at": None, "days": [], "orders": []}})
    assert (await state(bot))["earnings"]["sources"][1]["note"] == earn.SC_WAIT
    write_export(tmp_path / "blogauto.json", **{"earnings": None})
    assert (await state(bot))["earnings"]["sources"][1]["note"] == earn.SC_NOFIELD


# ---------------------------------------------------------------- earn.js
def test_earn_js_budget_safety_and_lazy_load() -> None:
    js = (STATIC / "earn.js").read_bytes()
    assert len(js) <= EARN_JS_MAX and len(gzip.compress(js, 9)) <= EARN_JS_GZIP_MAX
    text = js.decode()
    assert "http://" not in text and "https://" not in text and "eval(" not in text and "new Function" not in text and "innerHTML" not in text
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "sb-earn" in app and app.count("innerHTML") == 1
    assert "earn.save" in text and "earn.delete" in text
    html = shell.page({"me": {}})
    assert re.search(r'<meta name="sb-earn" content="/dashboard/static/earn\.js\?v=[0-9a-f]{10}">', html)
    assert html.count("<script src=") == 1  # earn.js 는 통계 탭을 처음 열 때만
    assert "earn.js" in __import__("dealbot.dashboard.api", fromlist=["x"]).APP_FILES
    for label in re.findall(r"btn\('[^']*', '([^']*)'", text):
        assert re.search(r"[가-힣]", label), label


def test_earn_js_syntax() -> None:
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    assert subprocess.run([node, "--check", str(STATIC / "earn.js")], capture_output=True).returncode == 0


def test_schedule_loop_calls_earn_tick() -> None:
    src = (Path(__file__).parent.parent / "src" / "dealbot" / "scheduler.py").read_text(encoding="utf-8")
    assert "earn_coupang" in src and asyncio.iscoroutinefunction(earn.EarnCoupang.tick)


# ---------------------------------------------------------------- 에이블리·지그재그·토스쇼핑 자동 (주인 10/23 — 쇼핑몰 크리에이터를 제휴 수익으로)
def test_creator_malls_are_auto_rows_in_the_same_layout(db: Database) -> None:
    from dealbot.dashboard.blogauto import _creator_mall

    cm = _creator_mall(json.loads((Path(__file__).parent / "fixtures" / "dash_contract" / "export_v2.json").read_text(encoding="utf-8"))["creator_mall"])
    store = earn.EarnStore(db)
    for m in store.manual():
        store.delete_manual(m["id"])
    store.replace_coupang(earn.parse_coupang(REPORTS, TODAY), "2026-09-24", "2026-10-23", "t")
    plain = earn.build(store, TZ, NOW, None, coupang_on=True, sub_id_set=True)
    e = earn.build(store, TZ, NOW, None, coupang_on=True, sub_id_set=True, cm_field=cm)
    labels = [s["label"] for s in e["sources"]]
    assert labels == ["쿠팡 (자동)", "쇼핑커넥트", "에이블리 (자동)", "지그재그 (자동)", "토스쇼핑 (자동)", "직접 넣은 것"]
    src = {s["key"]: s for s in e["sources"]}
    toss = src["cm_toss"]
    assert toss["state"] == "ok" and [c["label"] for c in toss["cells"]] == ["오늘", "7일", "30일"]
    assert toss["cells"][1] == {"label": "7일", "orders": "2건", "amount": "33,790원", "commission": "2,703원"}
    assert "방문 오늘 1 · 7일 4 · 30일 4" in toss["note"] and "연결됨" in toss["note"]
    assert src["cm_ably"]["cells"][0]["amount"] == "-"  # 에이블리는 주문 금액을 주지 않는다 — 짐작하지 않음
    zz = src["cm_zigzag"]
    assert zz["state"] == "wait" and "로그인 필요" in zz["note"]  # 로그인이 풀린 몰은 숫자 대신 안내
    # 합계에 더해짐 (로그인이 풀린 지그재그는 빼고) — 30일: 에이블리 600 + 토스 2,703
    p = {t["label"]: t for t in plain["totals"]}
    t = {t["label"]: t for t in e["totals"]}
    def won(s: str) -> int:
        return int(s.replace(",", "").replace("원", ""))
    assert won(t["30일"]["commission"]) - won(p["30일"]["commission"]) == 600 + 2703
    assert any(r["src"] == "토스쇼핑" and r["name"].startswith("교동 육개장") for r in e["recent"])  # 토스가 알려 준 산 상품
    assert e["manual"]["shops"] == ["무신사", "29CM", "기타"]


def test_reward_rules_and_ably_visit_challenge(db: Database) -> None:
    """보상 기준 시트 · 에이블리 방문 수 챌린지 (주인 10/23 — "조회수 기준으로 돈 주는 것 … 보상 확인할 수 있는 탭")."""
    from dealbot.dashboard.blogauto import _creator_mall

    cm = _creator_mall(json.loads((Path(__file__).parent / "fixtures" / "dash_contract" / "export_v2.json").read_text(encoding="utf-8"))["creator_mall"])
    e = earn.build(earn.EarnStore(db), TZ, NOW, None, coupang_on=True, sub_id_set=True, cm_field=cm)
    rw = {r["name"]: r for r in e["rewards"]}
    assert list(rw) == ["에이블리", "지그재그", "토스쇼핑"]
    assert any("8%" in t for t in rw["토스쇼핑"]["rules"]) and rw["토스쇼핑"]["challenge"] is None
    c = rw["에이블리"]["challenge"]
    assert c["text"] == "방문 수 챌린지 2회차 · 12명 · 30명 되면 3,000원 · 10/28까지"
    assert "0원" in c["warn"] and c["earned"] == ""  # 콘텐츠 링크를 안 냈으면 경고
    assert c["tiers"][0] == {"visits": "30명", "reward": "3,000원", "hit": False} and len(c["tiers"]) == 6
    assert c["history"] == ["1회차 (~8/1) · 0명 · 0원 · 콘텐츠를 제출하지 않아 0원"]
    src = {s["key"]: s for s in e["sources"]}
    assert src["cm_ably"]["challenge"] == c and src["cm_toss"]["challenge"] is None
    # 단계를 넘고 콘텐츠를 냈으면 경고 대신 닿은 금액
    raw = json.loads(json.dumps(cm))
    raw["malls"][0]["challenge"].update(visits=60, earned=10000, next={"visits": 100, "reward": 30000}, content=True)
    c2 = earn._challenge(_creator_mall(raw)["malls"][0]["challenge"])
    assert c2["warn"] == "" and c2["earned"].startswith("지금 닿은 단계 10,000원") and [t["hit"] for t in c2["tiers"][:3]] == [True, True, False]


def test_creator_mall_parser_drops_bad_challenge_bits() -> None:
    from dealbot.dashboard.blogauto import _cm_challenge, _cm_rules

    assert _cm_rules("x") == [] and _cm_rules(["a", 3, "", "b"]) == ["a", "b"]
    c = _cm_challenge({"visits": -1, "tiers": [{"visits": 30, "reward": 3000}, {"visits": "x"}], "next": {"visits": 30}, "content": "yes",
                       "history": ["x", {"round": 1, "ended": "2026-08-01"}]})
    assert c["visits"] is None and c["tiers"] == [{"visits": 30, "reward": 3000}] and c["next"] is None and c["content"] is False
    assert len(c["history"]) == 1 and _cm_challenge(None) is None


def test_recent_purchase_shows_link_kind_and_where_it_was_posted(db: Database) -> None:
    """최근 구매 작은 줄 (주인 10/23 — "뭘 파는 링크인지 … 어떤 sns 타고 온 건지"): 토스 링크 상품/다른 상품 · 봇이 올린 곳."""
    from dealbot.dashboard.blogauto import _creator_mall

    cm = _creator_mall(json.loads((Path(__file__).parent / "fixtures" / "dash_contract" / "export_v2.json").read_text(encoding="utf-8"))["creator_mall"])
    db._conn.execute("INSERT INTO products (product_id, name, url, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
          ("toss:1", "교동 육개장 500g 10개", "https://toss.shopping/t/1", "2026-10-18T00:00:00+00:00", "2026-10-18T00:00:00+00:00"))
    db._conn.execute("INSERT INTO posts (product_id, source, price, channel_id, posted_at) VALUES (?, ?, ?, ?, ?)",
          ("toss:1", "ppomppu", 1000, "@ch", "2026-10-18T08:06:52+00:00"))
    db._conn.execute("INSERT INTO sns_log (at, channel, product_id, outcome) VALUES (?, ?, ?, ?)", ("2026-10-18T20:00:00+00:00", "threads", "toss:1", "posted"))
    db._conn.execute("INSERT INTO sns_log (at, channel, product_id, outcome) VALUES (?, ?, ?, ?)", ("2026-10-19T01:00:00+00:00", "instagram", "toss:1", "failed"))
    e = earn.build(earn.EarnStore(db), TZ, NOW, None, coupang_on=True, sub_id_set=True, cm_field=cm)
    subs = {r["name"]: r["sub"] for r in e["recent"] if r["src"] == "토스쇼핑"}
    assert subs["교동 육개장, 500g, 10개"].endswith("링크 상품을 삼 · 올린 곳 텔레그램 10/18 · 스레드 10/19")  # 이름은 띄어쓰기·쉼표 달라도 같음
    assert subs["프로프룻 샤인머스켓, 특품, 2kg, 1박스"].endswith("링크로 들어와 다른 상품을 삼 · 어느 링크였는지는 토스가 알려 주지 않아요")
