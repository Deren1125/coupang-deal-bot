"""'하루치 한 번에 (발행까지)' 책갈피 (주인 11/23 — 책갈피 한 번에 글 여러 개를 넣고 발행 누르는 것까지).

1) autopub.bookmarklet(): 진행기(autopub.js + kkumi.js 통째, PATCHES · 붙여넣기 칸 뺌)를 주소에 통째로 (원격 코드 없음) · 길이 한도 · node 로 읽힘 · 꾸미기 책갈피는 그대로.
2) autopub.act(): 열쇠 · 그 블로그의 올려도 되는 글만 (점검 통과 · 새 방향과 안 맞는 글·고지 빠진 글 뺌) · 예약 시각(첫 글의 지난 자리만 지금) · 40~120초 ·
   한 판 5편 · 주말 쉼 · 발행 전 '해 봄' · 멈춘 글 다시 · 네이버 안내 뒤 24시간 쉼 · 발행 확인이면 할 일 줄 체크.
3) GET /dashboard/api/autopub (로그인 아래) · POST /dashboard/api/autopub_act (열쇠 · CORS 는 네이버 블로그 화면만) · 연결 탭 칸 · 할 일 카드 한 줄.
4) 가짜 글쓰기 화면 (진짜 크롬, playwright 가 있을 때만): 한 번 눌러 글 2편을 채우고 발행까지 (지금 발행 + 예약) · 점검에 걸리면 발행하지 않고 멈춤 → 고치고
   단추를 누르면 이어서 발행 · 카테고리·예약 칸을 못 채우면 멈춤 · 네이버 보호 안내면 바로 멈춤 · 다른 블로그면 거절 · 주말 단추 · '글마다 한 번'(DIR) 길.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import get_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import actions, autopub, kkumi, logins, routine
from test_dash_kkumi import INNER, _playwright_env, _tags_line
from test_dash_kkumi_when import RSV
from test_dash_routine import SLOTS, at, item, with_routine
from test_dash_statscap import FakeDb
from test_dash_writebm import EXTRA

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
TZ = ZoneInfo("Asia/Seoul")
DAY = "2026-11-18"  # 수요일
OK = {"ok": True, "codes": []}


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.public_domain = "bot.example.com"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture(autouse=True)
def _no_live_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.dashboard import threads_proxy

    def off(*a: object) -> None:
        raise ConnectionError("test")
    monkeypatch.setattr(threads_proxy, "_call", off)
    routine._TH_CACHE.clear()


def _paste(title: str, body: str = "<p>본문이에요.</p>") -> dict:
    return {"title": title, "html": body + _tags_line(["태그"], "셀럽 소식"), "tags": ["태그"], "cards": 0, "photos": 0}


def _when(t: str) -> list[dict]:
    return [{"label": "예약 시각", "text": t}]


def _rows(day: str) -> dict:
    return {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("rsv:issue:11", "s4", group="예약", blog="issue", title="데런픽 예약 · 저녁", copy=_when("19:30"), paste=_paste("글 저녁"), gate=OK),
        item("rsv:issue:12", "s1", group="예약", blog="issue", title="데런픽 예약 · 지난 자리", copy=_when("09:00"), paste=_paste("글 아침"), gate=OK),
        item("pub:issue:13", "s1", group="발행", blog="issue", title="데런픽 발행 · 바로", paste=_paste("글 바로"), gate=OK),
        item("rsv:issue:14", "s2", group="예약", blog="issue", title="데런픽 예약 · 점검에 걸림", paste=_paste("글 걸림"),
             gate={"ok": False, "codes": ["dup_photo"]}),
        item("rsv:issue:15", "s2", group="예약", blog="issue", title="데런픽 예약 · 점검 안 됨", paste=_paste("글 점검 없음")),
        item("rsv:issue:16", "s2", group="예약", blog="issue", title="데런픽 예약 · 옛 글", note="새 방향과 안 맞음 — 올리지 않음",
             paste=_paste("글 옛 방향"), gate=OK),
        item("rsv:issue:17", "s2", group="예약", blog="issue", title="데런픽 예약 · 고지 없음",
             paste=_paste("글 고지 없음", "<p>이거 좋아요.</p><p>https://link.coupang.com/a/abc</p>"), gate=OK),
        item("rsv:issue:19", "s2", group="예약", blog="issue", title="데런픽 예약 · 만드는 중", paste={"title": "글 만드는 중", "stale": True, "why": "building"}, gate=OK),
        item("pub:finance:18", "s1", group="발행", blog="finance", title="드라마 발행", paste=_paste("글 드라마"), gate=OK),
        item("nb:issue:a", "s1", blog="issue"),
    ]}


class Rng:
    def randint(self, a: int, b: int) -> int:
        assert (a, b) == (40, 120)
        return 77


# ---------------------------------------------------------------- 1) 책갈피·진행기
def test_bookmarklet_carries_the_whole_runner_and_fetches_no_code() -> None:
    db = FakeDb()
    v = autopub.view(db, "https://dash.example")
    js = unquote(v["code"][len("javascript:"):])
    assert v["code"].startswith("javascript:") and v["len"] == len(v["code"]) <= autopub.LEN_MAX == 50000 and v["steps"] == list(autopub.STEPS)
    assert v["version"] == autopub.version() == 1 and "지금 판 v1" in v["note"]
    assert "#" not in v["code"] and v["code"] == "javascript:" + kkumi.encode_short(js)
    assert "u:'https://dash.example/dashboard/api/autopub_act'" in js and f"k:'{autopub.key(db)}'" in js and "@@" not in js
    assert '["derenpick","issue","데런픽"]' in js and "credentials:'omit'" in js and kkumi.minify(autopub._core()) in js
    # 원격 코드 실행 없음: 받은 글을 코드로 돌리는 길이 없고, 대시보드에 코드를 달라고 하지 않음 · 쿠키를 읽지 않음
    for word in ("new Function", "Function(", "eval(", "a:'code'", "import(", "createElement('script')", "prompt(", "cookie"):
        assert word not in js, word
    # 알림·확인 창: 꾸미기 코드의 alert·confirm 은 진행기가 넘긴 함수 (매개변수 이름이 가림) — 진짜 창을 부르는 곳은 없음
    assert js.count("async(document,alert,confirm,GOT,PRE)=>{") == 1 and "window.alert" not in js and "W.alert" not in js
    with pytest.raises(ValueError):
        autopub.bookmarklet("http://dash.example/x", "k")
    assert autopub.key(db) == autopub.key(db) and autopub.key(FakeDb()) != autopub.key(db) and not autopub.key_ok(db, "") and autopub.key_ok(db, autopub.key(db))
    # 열쇠는 '글쓰기 한 번에'·이웃 관리와 따로 (하나가 새도 다른 길은 그대로)
    from dealbot.dashboard import writebm

    assert autopub.key(db) != writebm.key(db)


def test_runner_reuses_kkumi_whole_and_kkumi_itself_is_untouched() -> None:
    r, src = autopub.runner(), kkumi.SRC.read_text(encoding="utf-8")
    shell = (STATIC / "autopub.js").read_text(encoding="utf-8")
    assert r.startswith("async CFG => {") and "@@KKUMI@@" not in r and shell.count(autopub.SLOT) == 1
    assert "const CORE = async (document, alert, confirm, GOT, PRE) => {" in r and "const got = GOT;" in r and "await ask();" not in r
    # 꾸미기 코드에서 바꾸는 것은 PATCHES 네 줄 + 붙여넣기 칸 빼기뿐 — 그 줄이 사라지면 바로 알림 (kkumi.js 를 고칠 때 같이)
    for old, _new in autopub.PATCHES:
        assert src.count(old) == 1, old
    assert all(src.count(c) == 1 for c in autopub.CUT) and "aria-label', '여기에 붙여넣기'" not in r
    for tool in autopub.TOOLS.split(", "):  # 껍데기가 쓰는 꾸미기 도구가 kkumi.js 에 그 이름으로 있음
        assert f"const {tool} = " in src or f"const {tool}=" in src or tool in ("D", "W", "R", "S"), tool
    # 꾸미기 책갈피는 혼자서도 그대로 (발행을 끝내는 단추를 누르지 않는 막이 포함 · 판 번호 그대로)
    assert kkumi.version() == 10 and "throw 0;" in src and len(kkumi.bookmarklet()) < 30000
    # 껍데기: 알림·확인·입력 창 없음 · 마지막 발행 단추는 '해 봄'(hold)을 남긴 뒤 한 곳에서만 누름
    for word in (".click(", "requestSubmit", ".submit(", "alert(", "confirm(", "prompt(", "eval(", "document.cookie"):
        assert word not in shell, word
    assert shell.count("K.press(b)") == 1 and shell.index("r: 'hold'") < shell.index("K.press(b)") and shell.count("const b = fin();") == 1
    assert "keepalive: true" in shell and "credentials: 'omit'" in shell and "sandbox" in shell
    assert autopub.ROW_RE.pattern == actions.PASTE_ROW_RE.pattern
    assert autopub.DISC_RE.pattern == r"수수료를\s*(받|제공|지급)" and "const DISC = /수수료를\\s*(받|제공|지급)/;" in shell


@needs_node
def test_runner_and_bookmarklet_parse(tmp_path: Path) -> None:
    f = tmp_path / "r.js"
    f.write_text("(" + autopub.runner() + ")", encoding="utf-8")
    g = tmp_path / "l.js"
    g.write_text(unquote(autopub.view(FakeDb(), "https://dash.example")["code"][len("javascript:"):]), encoding="utf-8")
    for p in (f, g):
        out = subprocess.run([NODE, "--check", str(p)], capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr


# ---------------------------------------------------------------- 2) act
def _go(bot: DealBot, now: float, **form: str) -> dict[str, Any]:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), **form}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng())


def test_act_serves_only_fit_posts_in_slot_order_with_their_reserve_times(bot: DealBot, tmp_path: Path) -> None:
    with_routine(tmp_path, _rows(DAY))
    t = at(bot, DAY, "10:00")
    with pytest.raises(autopub.CaptureError) as e:
        autopub.act(bot.db, {"k": "wrong", "a": "next", "b": "issue"}, tz=TZ)
    assert e.value.status == 403 and e.value.code == "key"
    for bad in ({"a": "code", "b": "issue"}, {"a": "next", "b": "nope"}):
        with pytest.raises(autopub.CaptureError):
            _go(bot, t, **bad)
    # 올려도 되는 글은 셋뿐: 점검에 걸린 글·점검 안 된 글·새 방향과 안 맞는 글·고지 없는 제휴 글·만드는 중인 글·다른 블로그는 없음
    rv = routine.view(bot, now=t)
    assert [i["id"] for i in autopub.ready(rv, "issue")] == ["pub:issue:13", "rsv:issue:12", "rsv:issue:11"]
    why = {i["id"]: autopub.unfit(i) for s in rv["slots"] for i in s["items"] if i["blog"] == "issue" and i["group"] != "이웃"}
    assert why["rsv:issue:14"] and why["rsv:issue:15"].startswith("올리기 전 자동 점검") and why["rsv:issue:16"].startswith("대시보드에 올리지 않는")
    assert why["rsv:issue:17"].startswith("제휴 링크가 있는데") and why["rsv:issue:19"] and not why["rsv:issue:11"]
    # 첫 글: 바로 발행 줄 → 지금 발행 (예약 시각 칸 없음)
    q = _go(bot, t, a="next", b="issue")
    assert (q["n"], q["m"], q["left"], q["wait"]) == (1, 3, 3, 0) and q["next"]["id"] == "pub:issue:13" and q["next"]["title"] == "글 바로"
    assert q["next"]["at"] == "" and q["next"]["label"] == "지금" and kkumi.stamped(q["next"]["html"]) == "" and q["next"]["disc"] is False and q["next"]["photos"] == 0
    # 발행 직전 '해 봄' → 그 글은 다시 주지 않음 (확인을 못 해도 두 번 발행하지 않게) · 확인되면 할 일 줄 체크 + 블로그 프로그램에 pasted + 글 번호
    assert _go(bot, t + 5, a="done", b="issue", id="pub:issue:13", r="hold") == {"ok": True, "checked": False, "text": ""}
    assert _go(bot, t + 6, a="next", b="issue")["next"]["id"] == "rsv:issue:12"
    d = _go(bot, t + 10, a="done", b="issue", id="pub:issue:13", r="ok", u="223000000001")
    assert d == {"ok": True, "checked": True, "text": "할 일 줄 체크 ✓", "left": 2} and "pub:issue:13" in routine.done_ids(bot.db, DAY)
    assert [(r["name"], r["args"], r["rid"]) for r in routine.relay_rows(bot.db)] == [("pasted", {"blog": "issue", "pid": 13, "done": True}, "pub:issue:13")]
    assert json.loads(bot.db.kv_get(autopub.DAY_KEY + DAY))["res"][-1]["u"] == "223000000001"
    # 둘째 글: 자리가 지난 예약 글 — 첫 글이 아니므로 지금이 아니라 규칙으로 고른 다음 자리 (정각·30분 아님) · 앞 글 뒤 77초가 될 때까지 쉼
    q = _go(bot, t + 20, a="next", b="issue")
    nx = q["next"]
    assert (q["n"], q["m"], q["wait"]) == (2, 3, 67) and nx["id"] == "rsv:issue:12" and kkumi.stamped(nx["html"]) == nx["at"] != ""
    d12 = datetime.fromisoformat(nx["at"])
    assert d12 > datetime(2026, 11, 18, 10, 20) and d12.minute % 30 >= 10 and nx["label"] == f"11/18 {d12:%H:%M}"
    assert _go(bot, t + 100, a="next", b="issue")["wait"] == 0
    # 점검에 걸려 멈춘 글: 발행하지 않았으므로 다시 누르면 같은 글부터 · 두 번 멈추면 오늘은 주지 않음 (직접)
    _go(bot, t + 110, a="done", b="issue", id="rsv:issue:12", r="stop", n="사진 0/1 — 빠진 사진을 넣어 주세요")
    assert _go(bot, t + 120, a="next", b="issue")["next"]["id"] == "rsv:issue:12" and "rsv:issue:12" not in routine.done_ids(bot.db, DAY)
    td = autopub.today(bot.db, t + 120, TZ, routine.view(bot, now=t + 120))["by"]["issue"]
    assert td["line"] == "하루치 한 번에: 올린 글 1 · 멈춘 글 1 — 사진 0/1 — 빠진 사진을 넣어 주세요 · 준비된 글 2편 — 글쓰기 화면에서 책갈피 한 번"
    _go(bot, t + 130, a="done", b="issue", id="rsv:issue:12", r="stop", n="사진 0/1")
    q = _go(bot, t + 140, a="next", b="issue")
    assert q["next"]["id"] == "rsv:issue:11" and (q["n"], q["m"]) == (2, 2)
    d11 = datetime.fromisoformat(q["next"]["at"])  # 살아 있는 계획 시각 19:30 을 글마다 정해진 만큼 흔든 값 (정각·30분 아님)
    assert abs((d11 - datetime(2026, 11, 18, 19, 30)).total_seconds()) / 60 <= 45 and d11.minute % 30 >= 10
    # 확인 못 한 글: 체크하지 않고 다시 주지 않음 · 모르는 줄·다른 블로그 줄·모르는 결과는 거절
    assert _go(bot, t + 150, a="done", b="issue", id="rsv:issue:11", r="unsure")["checked"] is False
    q = _go(bot, t + 160, a="next", b="issue")
    assert q["next"] is None and q["text"] == "데런픽: 지금 올릴 준비된 글이 없어요"
    for bad in ({"id": "pub:finance:18", "r": "ok"}, {"id": "rsv:issue:11", "r": "yes"}, {"id": "nb:issue:a", "r": "ok"}, {"id": "", "r": "ok"}):
        with pytest.raises(autopub.CaptureError):
            _go(bot, t + 170, a="done", b="issue", **bad)
    # 점검에 걸린 줄은 열쇠가 있어도 체크되지 않음 · 이미 체크된 줄에 확인이 또 와도 다시 전하지 않음
    with pytest.raises(autopub.CaptureError):
        _go(bot, t + 180, a="done", b="issue", id="rsv:issue:14", r="ok")
    assert "rsv:issue:14" not in routine.done_ids(bot.db, DAY)
    assert _go(bot, t + 190, a="done", b="issue", id="pub:issue:13", r="ok")["checked"] is False and len(routine.relay_rows(bot.db)) == 1
    note = autopub.today(bot.db, t + 200, TZ)["note"]
    assert note == "오늘 하루치 한 번에: 데런픽 1 · 확인 필요 1 · 멈춘 글 1" and autopub.view(bot.db, "https://dash.example", now=t + 200, tz=TZ)["note"].endswith(note)


def test_first_post_with_a_past_slot_goes_now_and_a_live_slot_stays_reserved(bot: DealBot, tmp_path: Path) -> None:
    rows = _rows(DAY)
    rows["items"] = [i for i in rows["items"] if i["id"] in ("rsv:issue:11", "rsv:issue:12")]
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "10:00")
    q = _go(bot, t, a="next", b="issue")  # 첫 글의 자리(09:00)가 이미 지남 → 첫 글만 지금
    assert q["next"]["id"] == "rsv:issue:12" and q["next"]["at"] == "" and kkumi.stamped(q["next"]["html"]) == ""
    _go(bot, t + 1, a="done", b="issue", id="rsv:issue:12", r="ok")
    q = _go(bot, t + 2, a="next", b="issue")
    assert q["next"]["id"] == "rsv:issue:11" and q["next"]["at"].startswith("2026-11-18T19:") and q["wait"] == 76
    # 첫 글이어도 자리가 살아 있으면 그 시각으로 예약 (지금 올리지 않음)
    rows["items"] = [i for i in rows["items"] if i["id"] == "rsv:issue:11"]
    with_routine(tmp_path, rows)
    bot.db.kv_set(autopub.DAY_KEY + DAY, "{}")
    q = _go(bot, t, a="next", b="issue")
    assert q["n"] == 1 and q["next"]["id"] == "rsv:issue:11" and q["next"]["at"].startswith("2026-11-18T19:")


def test_run_cap_weekend_pause_and_protection_cooldown(bot: DealBot, tmp_path: Path) -> None:
    rows = {"day": DAY, "slots": SLOTS, "overflow": 0, "items": [
        item(f"pub:issue:{n}", "s1", group="발행", blog="issue", title=f"데런픽 발행 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in range(1, 8)]}
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "10:00")
    for n in range(1, 6):  # 한 판에 5편까지
        q = _go(bot, t + n * 200, a="next", b="issue")
        assert q["next"]["id"] == f"pub:issue:{n}" and (q["n"], q["m"]) == (n, 5)
        assert (q["next"]["at"] == "") is (n == 1)  # 지금 발행은 첫 글만 — 나머지는 다음 자리로 예약
        _go(bot, t + n * 200 + 1, a="done", b="issue", id=f"pub:issue:{n}", r="ok")
    q = _go(bot, t + 1300, a="next", b="issue")
    assert q["next"] is None and q["left"] == 2 and q["text"] == "데런픽: 한 번에 5편까지만 올려요 (남은 글 2) — 30분쯤 뒤에 다시 눌러 주세요"
    q = _go(bot, t + 1001 + autopub.RUN_IDLE_S + 60, a="next", b="issue")  # 한참 뒤 다시 누르면 새 판
    assert q["next"]["id"] == "pub:issue:6" and (q["n"], q["m"]) == (1, 2)
    # 네이버 보호·제한 안내 → 그 블로그만 24시간 쉼 (다시 눌러도 주지 않음 · 발행 전 '해 봄'도 거절 → 책갈피는 발행을 누르지 않음) · kv 에 until
    t2 = t + 5000
    d = _go(bot, t2, a="done", b="issue", id="pub:issue:6", r="wall", n="자동입력 방지")
    assert d["checked"] is False and d["until"] == t2 + 86400 and d["text"] == "네이버 보호·제한 안내로 데런픽 하루치 한 번에는 11/19 11:23까지 쉬어요 — 그동안은 직접 올려 주세요"
    cool = json.loads(bot.db.kv_get(autopub.KV_COOL))
    assert cool["issue"]["until"] == t2 + 86400 and cool["issue"]["why"] == "자동입력 방지" and list(cool) == ["issue"]
    for form in ({"a": "next"}, {"a": "done", "id": "pub:issue:6", "r": "hold"}):
        with pytest.raises(autopub.CaptureError) as e:
            _go(bot, t2 + 3600, b="issue", **form)
        assert e.value.status == 429 and e.value.code == "cool" and "11/19 11:23까지" in e.value.text
    assert _go(bot, t2 + 60, a="done", b="issue", r="wall")["until"] == t2 + 86400  # 글 번호 없이 와도 · 이미 쉬는 중이면 늘리지 않음
    assert autopub.today(bot.db, t2 + 60, TZ, routine.view(bot, now=t2 + 60))["by"]["issue"]["line"].endswith("11/19 11:23까지 쉬어요 — 그동안은 직접 올려 주세요")
    assert _go(bot, t2 + 60, a="next", b="finance")["next"] is None  # 다른 블로그는 쉬지 않음 (올릴 글이 없을 뿐)
    # 주말 (토 00:00 ~ 일 21:00): 주인이 '주말에도 시작'을 눌렀을 때만 (w=1) · 일요일 21시부터는 평소대로 (월 아침 글)
    sat, sun = "2026-11-21", "2026-11-22"
    assert [autopub.weekend(at(bot, d_, hm), TZ) for d_, hm in ((DAY, "10:00"), (sat, "00:10"), (sun, "20:59"), (sun, "21:00"), ("2026-11-23", "07:00"))] \
        == [False, True, True, False, False]
    with_routine(tmp_path, {**rows, "day": sat})
    ts = at(bot, sat, "10:00")
    q = _go(bot, ts, a="next", b="finance")
    assert q["next"] is None and q["weekend"] is True and "주말에도 시작" in q["text"]
    q = _go(bot, ts, a="next", b="finance", w="1")
    assert "weekend" not in q and q["next"] is None  # (드라마는 올릴 글이 없음 — 주말 막이는 지나감)
    # 오늘 목록이 아니면 아무것도 주지 않음
    with_routine(tmp_path, {**rows, "day": "2026-11-17"})
    q = _go(bot, at(bot, "2026-11-19", "12:00"), a="next", b="finance")
    assert q["next"] is None and q["text"].startswith("오늘 할 일 목록이 아직 없어요")


# ---------------------------------------------------------------- 3) 대시보드
async def _post(bot: DealBot, body: dict[str, str], **headers: str):  # type: ignore[no-untyped-def]
    raw = urlencode(body).encode()
    h = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.9", "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
         "content-length": str(len(raw)), "sec-fetch-site": "cross-site", **headers}
    return await bot.web.dispatch("POST", autopub.PATH, h, raw)


async def test_api_code_needs_login_and_act_takes_key_only(bot: DealBot, tmp_path: Path) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/autopub", anon))[0] == 401
    status, v, hd = await get_json(bot, "/dashboard/api/autopub", session_headers(bot))
    k = autopub.key(bot.db)
    assert status == 200 and k in unquote(v["code"]) and hd["Cache-Control"] == "no-store" and v["version"] == 1
    res = await _post(bot, {"k": k, "a": "code", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 400 and json.loads(res[1])["code"] == "bad" and res[3]["Access-Control-Allow-Origin"] == "https://blog.naver.com"
    res = await _post(bot, {"k": k, "a": "next", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["next"] is None and res[3]["Cache-Control"] == "no-store"
    res = await _post(bot, {"k": "wrong", "a": "next", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 403 and json.loads(res[1])["code"] == "key"
    for o in ("https://evil.example", "https://cafe.naver.com", "https://blog.naver.com.evil.example", "http://blog.naver.com", "null"):
        res = await _post(bot, {"k": k, "a": "next", "b": "issue"}, origin=o)  # 네이버 블로그 화면이 아니면 답을 못 읽음
        assert "Access-Control-Allow-Origin" not in res[3] and res[3]["Vary"] == "Origin", o
    assert (await bot.web.dispatch("GET", autopub.PATH, anon))[0] == 405
    assert (await _post(bot, {"k": k, "a": "next", "b": "issue", "x": "가" * 3000}, origin="https://blog.naver.com"))[0] == 413
    # 설정으로 끄면 코드 받기·이미 깐 책갈피 둘 다 403 (맞는 열쇠라도 목록을 주지 않음) · 연결 탭 칸도 숨김
    assert logins.view(bot)["autopub"] is True
    bot.settings.dashboard.auto_pub = False
    status, v, _ = await get_json(bot, "/dashboard/api/autopub", session_headers(bot))
    assert status == 403 and v["code"] == "off" and logins.view(bot)["autopub"] is False
    res = await _post(bot, {"k": k, "a": "next", "b": "issue"}, origin="https://blog.naver.com")
    assert res[0] == 403 and json.loads(res[1]) == {"ok": False, "code": "off", "text": autopub.OFF_TEXT}


async def test_connect_tab_entry_and_todo_card_progress_line(bot: DealBot, tmp_path: Path) -> None:
    js = (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "{api: 'autopub', a: 1, t: '하루치 한 번에 (발행까지) 버튼 설치 — 글 여러 개 넣고 예약 발행까지'}" in js.split("const BM = [", 1)[1].split("];", 1)[0]
    assert "b.d.version ? ` (v${b.d.version})` : ''" in js and "data.autopub !== false" in js
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "data.autopub[k]" in rt and "'data-id': 'autopub:' + k" in rt
    # 할 일 카드 한 줄: 준비된 글 수 → 올린 글 수 (GET /dashboard/api/routine 의 autopub — 블로그마다)
    now = datetime.now(TZ)
    wk = autopub.weekend(now.timestamp(), TZ)
    with_routine(tmp_path, _rows(now.date().isoformat()))
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    tail = " (주말 — '주말에도 시작'을 눌러야 해요)" if wk else ""
    assert status == 200 and v["autopub"]["issue"] == "하루치 한 번에: 준비된 글 3편 — 글쓰기 화면에서 책갈피 한 번" + tail
    assert v["autopub"]["finance"].startswith("하루치 한 번에: 준비된 글 1편") and "hotdeal" not in v["autopub"]
    k = autopub.key(bot.db)
    res = await _post(bot, {"k": k, "a": "done", "b": "issue", "id": "pub:issue:13", "r": "ok", "u": "223000000009"}, origin="https://blog.naver.com")
    assert res[0] == 200 and json.loads(res[1])["checked"] is True
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert v["autopub"]["issue"].startswith("하루치 한 번에: 올린 글 1 · 준비된 글 2편")
    assert next(i for s in v["slots"] for i in s["items"] if i["id"] == "pub:issue:13")["done"] is True  # ✓ 올렸어요
    bot.settings.dashboard.auto_pub = False
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert "autopub" not in v


# ---------------------------------------------------------------- 4) 가짜 글쓰기 화면
# test_dash_writebm 의 가짜 네이버 글쓰기 (바깥 frameset + 안쪽 에디터 · 발행창 예약 칸 · 마지막 발행 단추를 누르면 MODE 대로:
#   xhr = 저장 요청(/RabbitWrite.naver) 뒤 맨 위 창을 글 주소로 옮기려 함 · wall = 자동입력 방지 창이 뜸) + prewall = 열자마자 보호조치 안내.
PRE_WALL = r"""<script>if (/mode=prewall/.test(location.search)) { const w = document.createElement('div'); w.textContent = '회원님의 아이디를 보호하고 있습니다'; document.body.append(w); }</script></body></html>"""
FLOW = r"""
const {chromium} = require('playwright');
(async () => {
  const [editor, loader, posts, opt_] = process.argv.slice(1), opt = JSON.parse(opt_);
  const b = await chromium.launch();
  const ctx = await b.newContext({viewport: {width: 1100, height: 900}});
  const p = await ctx.newPage();
  const out = {dialogs: [], done: [], pub: [], next: [], errors: [], taps: 0, fixed: 0, bars: []};
  const queue = JSON.parse(posts), closed = r => ['ok', 'unsure', 'wall', 'fail'].includes(r);
  p.on('dialog', async d => { out.dialogs.push(d.message()); await d.dismiss(); });
  p.on('pageerror', e => out.errors.push(String(e)));
  const cors = {'Access-Control-Allow-Origin': 'https://blog.naver.com'}, json = (r, v) => r.fulfill({headers: cors, contentType: 'application/json', body: JSON.stringify(v)});
  await ctx.route('**/*', async r => {
    const q = r.request(), u = new URL(q.url());
    if (u.host === 'dash.test' && u.pathname === '/dashboard/api/autopub_act') {
      const f = new URLSearchParams(q.postData() || ''), left = () => queue.filter(x => !out.done.some(d => d.id === x.id && closed(d.r)));
      if (f.get('k') !== 'KEY' || f.get('b') !== 'issue') return r.fulfill({status: 403, headers: cors, contentType: 'application/json', body: '{"ok":false,"text":"열쇠"}'});
      if (f.get('a') === 'next') {
        out.next.push(f.get('w') || '');
        if (opt.weekend && f.get('w') !== '1') return json(r, {ok: true, next: null, weekend: true, text: '주말은 쉬는 날이에요'});
        const n = out.done.filter(d => d.r === 'ok').length, l = left();
        return json(r, {ok: true, next: l[0] || null, left: l.length, n: n + 1, m: n + l.length, wait: n ? 1 : 0, text: '데런픽: 지금 올릴 준비된 글이 없어요'});
      }
      out.done.push({id: f.get('id'), r: f.get('r'), n: f.get('n'), u: f.get('u'), pub: out.pub.length});
      return json(r, {ok: true, checked: f.get('r') === 'ok', left: left().length, text: f.get('r') === 'wall' ? '데런픽 하루치 한 번에는 24시간 쉬어요' : ''});
    }
    if (u.host === 'dash.test' && u.pathname.startsWith('/media/')) return opt.nophoto ? r.abort() : r.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, contentType: 'image/jpeg', body: Buffer.from([255, 216, 255, 217])});
    if (u.host !== 'blog.naver.com') return r.fulfill({status: 404, body: ''});
    const m = /^\/(\w+)\/postwrite$/.exec(u.pathname);
    if (m) return r.fulfill({contentType: 'text/html', body: '<html><head><title>w</title></head><frameset rows="*"><frame id="mainFrame" src="/PostWriteForm.naver?blogId=' + m[1] + '&mode=' + opt.mode + '"></frameset></html>'});
    // noframe: 글쓰기가 iframe 안에서는 안 뜨는 브라우저 (frameset 의 frame 으로는 뜸)
    if (u.pathname === '/PostWriteForm.naver' && opt.noframe && await (await q.frame().frameElement()).evaluate(e => e.tagName === 'IFRAME')) { out.iframes = (out.iframes || 0) + 1; return r.fulfill({contentType: 'text/html; charset=utf-8', body: '<html><body><p>빈 화면</p></body></html>'}); }
    if (u.pathname === '/PostWriteForm.naver') return r.fulfill({contentType: 'text/html; charset=utf-8', body: editor});
    if (u.pathname === '/RabbitWrite.naver') { out.pub.push(JSON.parse(q.postData())); return r.fulfill({contentType: 'application/json', body: JSON.stringify({isSuccess: true, result: {redirectUrl: 'https://blog.naver.com/derenpick/22300000000' + out.pub.length}})}); }
    if (u.pathname === '/pressed') { out.pub.push(JSON.parse(q.postData())); return r.fulfill({body: 'ok'}); }
    return r.fulfill({contentType: 'text/html; charset=utf-8', body: '<html><body><p>글 보기 화면</p></body></html>'});
  });
  const write = 'https://blog.naver.com/' + (opt.blog || 'derenpick') + '/postwrite';
  const tap = async () => { out.taps++; await p.goto(write); await p.frames()[1].waitForSelector('.se-content'); if (opt.dir) await p.evaluate(() => localStorage.setItem('apbd', '1')); p.evaluate(loader).catch(() => {}); };
  await tap();
  // 우리 한 줄 (#apb) 은 이어가기 길에서는 맨 위 문서에, '글마다 한 번' 길에서는 글쓰기 문서에 있음
  const ui = async () => { for (const f of p.frames()) { try { const t = await f.evaluate(() => { const e = document.querySelector('#apb'); return e ? [e.children[0].textContent, e.children[2].style.display !== 'none' ? e.children[2].textContent : '', [...e.children[1].children].map(x => x.textContent)] : null; }); if (t) return [f, ...t]; } catch (e) {} } return null; };
  for (let i = 0; i < 1300; i++) {
    await p.waitForTimeout(200);
    const u = await ui();
    if (!u && out.reload) { out.reload = 0; out.flag = await p.evaluate(() => localStorage.getItem('apbd')).catch(() => null); await tap(); continue; }
    if (!u) {
      // 글마다 한 번: 발행 뒤 화면이 글 주소로 옮겨감 (진행기 사라짐) → 남은 글이 있으면 주인이 글쓰기 화면에서 한 번 더
      if ((opt.dir || opt.noframe) && !/postwrite/.test(p.url())) { out.moved = (out.moved || 0) + 1; out.last_url = p.url(); if (out.done.filter(d => d.r === 'ok').length < queue.length && out.taps < 4) { await tap(); continue; } break; }
      continue;
    }
    const [f, bar, btn, lines] = u;
    if (out.bars[out.bars.length - 1] !== bar) out.bars.push(bar);
    out.lines = lines;
    if (/화면을 다시 열게요/.test(bar)) out.reload = 1;
    if (/ · v\d+$/.test(bar) || /눌러 주세요$|아무것도 하지 않았어요$/.test(bar)) { out.end = bar; break; }
    if (btn) {
      out.btn = btn;
      if (/주말/.test(btn)) { await f.click('#apb button'); continue; }
      if (opt.fix && !out.fixed) {
        out.fixed = 1;
        out.pub_at_stop = out.pub.length;
        const ed = p.frames().filter(x => x.url().includes('PostWriteForm') && !x.isDetached()).pop();
        if (opt.fix === 'photo') await ed.evaluate(() => { for (const i of document.querySelectorAll('.se-image img')) i.src = 'https://blogfiles.pstatic.net/fixed.jpg'; });
        if (opt.fix === 'retap') { p.evaluate(loader).catch(() => {}); await p.waitForTimeout(1500); out.retap_bar = (await ui())[1]; break; }
        await f.click('#apb button');
        continue;
      }
      if (!opt.fix) { out.end = bar; break; }
    }
  }
  out.url = p.url();
  out.frames = await p.evaluate(() => document.querySelectorAll('iframe').length).catch(() => -1);
  out.sandbox = await p.evaluate(() => { const f = document.querySelector('iframe'); return f ? f.getAttribute('sandbox') : null; }).catch(() => null);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def _editor(rsv: str = "full") -> str:
    return INNER.replace("</body></html>", RSV.replace("__MODE__", rsv)).replace("</body></html>", EXTRA).replace("</body></html>", PRE_WALL)


def _flow(posts: list[dict], *, rsv: str = "full", timeout: int = 300, **opt: Any) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    loader = unquote(autopub.bookmarklet("https://dash.test/dashboard/api/autopub_act", "KEY")[len("javascript:"):])
    out = subprocess.run([NODE, "-e", FLOW, _editor(rsv), loader, json.dumps(posts, ensure_ascii=False), json.dumps({"mode": "xhr", **opt})],
                         capture_output=True, text=True, timeout=timeout, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _posts(when: datetime, cat: str = "셀럽 소식") -> list[dict]:
    """autopub.act 의 next 와 같은 모양: 첫 글 = 지금 발행 (시각 칸 없음 · 사진 하나 · 제휴 고지) · 둘째 글 = 예약."""
    line = _tags_line(["아일릿", "키링"], cat)
    disc = "<p>이 포스팅은 크리에이터 활동의 일환으로, 링크를 통해 구매하시면 일정액의 수수료를 받을 수 있습니다.</p>"
    at_ = when.strftime("%Y-%m-%dT%H:%M")
    return [
        {"id": "pub:issue:11", "title": "첫 글 제목이에요", "at": "", "label": "지금", "photos": 1, "disc": True, "text": "",
         "html": '<p>첫 글 첫 문단.</p><p><img src="https://dash.test/media/blog_issue_11_1_abcdef12.jpg"></p><blockquote><p>포스트잇 한 줄</p></blockquote>'
                 "<p>첫 글 끝 문단.</p>" + disc + line},
        {"id": "rsv:issue:12", "title": "둘째 글 제목", "at": at_, "label": "내일", "photos": 0, "disc": False, "text": "",
         "html": "<p>둘째 글 첫 문단.</p><p>둘째 글 끝 문단.</p>" + kkumi.stamp(line, at_)},
    ]


def _tomorrow() -> datetime:
    return (datetime.now() + timedelta(days=1)).replace(hour=19, minute=47, second=0, microsecond=0)


@needs_node
def test_one_tap_fills_checks_and_publishes_two_posts() -> None:
    when = _tomorrow()
    got = _flow(_posts(when))
    assert got["dialogs"] == [], got  # 알림·확인 창 0개
    # 가짜 네이버 화면이 맨 위 창을 옮기려다 막힌 것뿐 (글마다 한 번) — 진행기 오류는 없음
    assert len(got["errors"]) == 2 and all(e.startswith("SecurityError") and "navigate" in e for e in got["errors"]), got["errors"]
    # 글마다: 발행 전 '해 봄'(그때까지 발행 0·1건) → 발행 확인 + 글 번호
    assert [(d["id"], d["r"], d["u"], d["pub"]) for d in got["done"]] == [
        ("pub:issue:11", "hold", None, 0), ("pub:issue:11", "ok", "223000000001", 1),
        ("rsv:issue:12", "hold", None, 1), ("rsv:issue:12", "ok", "223000000002", 2)], got["done"]
    a, b = got["pub"]
    assert a["trusted"] is False and b["trusted"] is False  # 마지막 발행 단추를 책갈피가 누름 (주인이 누르지 않음)
    assert a["title"] == "첫 글 제목이에요" and "첫 글 첫 문단." in a["text"] and "첫 글 끝 문단." in a["text"] and "첫 글 제목" not in a["text"]
    assert a["imgs"] == 1 and a["tags"].split() == ["#아일릿", "#키링"] and a["cat"] == "셀럽 소식" and a["book"] is False and "수수료를 받을 수" in a["text"]
    assert b["title"] == "둘째 글 제목" and "둘째 글 끝 문단." in b["text"] and "첫 글" not in b["text"]  # 글마다 새 글쓰기 화면
    # 예약: 그 날 19시 · 분은 고르기 칸(10분 단위)에서 47 보다 크지 않은 가장 큰 값 40 (정각·30분이 아님)
    assert b["book"] is True and (b["h"], b["m"]) == ("19", "40") and b["date"] == f"{when.year}. {when.month:02d}. {when.day:02d}"
    assert any("1/2 「첫 글 제목이에요」 지금 발행을 눌렀어요" in x for x in got["bars"]) and any("2/2 「둘째 글 제목」 예약 " in x and "19:40 발행을 눌렀어요" in x for x in got["bars"])
    assert any("다음 글 2/2 — 1초 쉬어요" in x for x in got["bars"])
    assert got["end"].startswith("하루치 한 번에 · 데런픽: 지금 올릴 준비된 글이 없어요 · 올린 글 2 · 할 일 체크 2") and got["end"].endswith(" · v1"), got["end"]
    assert len(got["lines"]) == 2 and all(x.startswith("✓ 올렸어요") and "할 일 줄 체크 ✓" in x for x in got["lines"]) and "글 번호 223000000001" in got["lines"][0]
    # 네이버 화면이 맨 위 창을 글 주소로 옮기려 했지만 진행기는 누른 화면에 그대로 (sandbox) · 끝나면 글쓰기 창을 치움
    assert got["url"] == "https://blog.naver.com/derenpick/postwrite" and got["frames"] == 0 and got["next"] == ["", "", ""] and got["taps"] == 1


@needs_node
def test_a_missing_photo_stops_before_publish_and_resumes_after_the_owner_fixes_it() -> None:
    got = _flow(_posts(_tomorrow())[:1], nophoto=True, fix="photo")
    assert got["dialogs"] == [] and got["pub_at_stop"] == 0, got  # 멈춘 동안 발행된 것은 없음
    assert got["btn"] == "고쳤어요 — 다시 점검하고 발행"
    stop = next(x for x in got["bars"] if "멈췄어요" in x)
    assert "1/1 「첫 글 제목이에요」 멈췄어요 — 발행하지 않았어요" in stop and "사진 0/1 — 빠진 사진을 넣어 주세요" in stop
    # 멈춤을 대시보드에 알림 (걸린 것) → 주인이 사진을 넣고 단추 → 발행창·글을 다시 점검 → '해 봄' → 발행 → 확인
    assert [(d["id"], d["r"], d["pub"]) for d in got["done"]] == [("pub:issue:11", "stop", 0), ("pub:issue:11", "hold", 0), ("pub:issue:11", "ok", 1)], got["done"]
    assert "사진 0/1" in got["done"][0]["n"] and len(got["pub"]) == 1 and got["pub"][0]["cat"] == "셀럽 소식" and got["pub"][0]["tags"].split() == ["#아일릿", "#키링"]
    assert "올린 글 1" in got["end"]


@needs_node
def test_tapping_the_bookmark_again_while_stopped_resumes_the_same_runner() -> None:
    got = _flow(_posts(_tomorrow())[:1], nophoto=True, fix="retap")
    # 고치지 않고 책갈피만 한 번 더 → 새 진행기가 뜨지 않고 같은 글을 다시 점검 → 여전히 사진이 없어 또 멈춤 (발행 0)
    assert got["pub"] == [] and got["dialogs"] == [] and [d["r"] for d in got["done"]] == ["stop", "stop"], got
    assert "멈췄어요" in got["retap_bar"] and "사진 0/1" in got["retap_bar"]


@needs_node
def test_category_not_found_or_reserve_time_not_set_never_publishes() -> None:
    when = _tomorrow()
    got = _flow(_posts(when, cat="없는 카테고리"))
    assert got["pub"] == [] and [(d["id"], d["r"]) for d in got["done"]] == [("pub:issue:11", "stop")] and got["dialogs"] == [], got
    assert "카테고리 목록에 없음 (없는 카테고리)" in got["end"] and "발행하지 않았어요" in got["end"] and got["next"] == [""]  # 다음 글로 넘어가지 않음
    # 예약 칸(시·분 고르기)을 못 찾는 발행창: 예약 글은 발행하지 않음 (지금 올려 버리지 않음)
    got = _flow(_posts(when)[1:], rsv="nosel")
    assert got["pub"] == [] and [(d["id"], d["r"]) for d in got["done"]] == [("rsv:issue:12", "stop")], got
    assert "못 채움" in got["end"] and "발행하지 않았어요" in got["end"]


@needs_node
def test_protection_notice_stops_at_once_and_reports_for_the_cooldown() -> None:
    # 발행을 누른 뒤 자동입력 방지 창 → 풀지 않고 바로 멈춤 · 대시보드에 wall (→ 24시간 쉼) · 둘째 글은 열지도 않음
    got = _flow(_posts(_tomorrow()), mode="wall")
    assert got["dialogs"] == [] and [(d["id"], d["r"], d["n"]) for d in got["done"]] == [("pub:issue:11", "hold", None), ("pub:issue:11", "wall", "자동입력 방지")], got
    assert got["next"] == [""] and len(got["pub"]) == 1
    assert "네이버가 보호·제한 안내(자동입력 방지)를 보여 바로 멈췄어요 — 풀지 않아요. 데런픽 하루치 한 번에는 24시간 쉬어요" in got["end"] and "올린 글 0" in got["end"]
    assert got["lines"] == ["네이버 안내로 멈춤 · 「첫 글 제목이에요」 — 자동입력 방지"]
    # 글쓰기 화면을 열자마자 보호 안내 → 글을 넣지도 발행하지도 않음
    got = _flow(_posts(_tomorrow()), mode="prewall")
    assert got["pub"] == [] and [(d["id"], d["r"], d["n"]) for d in got["done"]] == [("pub:issue:11", "wall", "보호하고 있습니다")], got
    assert got["next"] == [""] and "바로 멈췄어요" in got["end"]


@needs_node
def test_another_blogs_write_page_is_refused_and_weekend_needs_the_owners_tap() -> None:
    got = _flow(_posts(_tomorrow())[:1], blog="someoneelse")
    assert got["next"] == [] and got["done"] == [] and got["pub"] == [], got  # 대시보드에 묻지도 않음
    assert got["end"] == "하루치 한 번에 · 우리 블로그 계정의 글쓰기 화면이 아니에요 (someoneelse) — 아무것도 하지 않았어요"
    got = _flow(_posts(_tomorrow())[:1], weekend=True)
    assert got["btn"] == "주말에도 시작" and got["next"][:2] == ["", "1"] and any(x == "하루치 한 번에 · 주말은 쉬는 날이에요" for x in got["bars"]), got
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and len(got["pub"]) == 1


@needs_node
def test_tap_once_per_post_mode_publishes_in_the_tapped_page_and_asks_for_one_more_tap() -> None:
    when = _tomorrow()
    got = _flow(_posts(when), dir=True)
    assert got["dialogs"] == [] and got["errors"] == [], got
    # 글마다 한 번: 누른 화면의 글쓰기에 바로 넣고 발행 → 네이버가 화면을 글 주소로 옮기기 전에 확인을 보냄 → 글쓰기 화면에서 한 번 더 → 둘째 글
    assert [(d["id"], d["r"], d["u"]) for d in got["done"]] == [
        ("pub:issue:11", "hold", None), ("pub:issue:11", "ok", "223000000001"), ("rsv:issue:12", "hold", None), ("rsv:issue:12", "ok", "223000000002")], got["done"]
    assert got["taps"] == 2 and got["moved"] == 2 and got["last_url"] == "https://blog.naver.com/derenpick/223000000002"
    a, b = got["pub"]
    assert a["title"] == "첫 글 제목이에요" and a["book"] is False and a["imgs"] == 1 and b["title"] == "둘째 글 제목" and b["book"] is True and (b["h"], b["m"]) == ("19", "40")
    assert got["sandbox"] is None  # iframe 을 쓰지 않음


@needs_node
def test_falls_back_to_tap_once_per_post_when_the_editor_will_not_load_in_a_frame() -> None:
    got = _flow(_posts(_tomorrow())[:1], noframe=True, timeout=420)
    # iframe 으로 두 번(sandbox · sandbox 없이) 열어 봐도 글쓰기가 안 뜸 → 아무것도 넣지 않고 이 브라우저에 '글마다 한 번'을 적고 화면을 다시 엶 → 한 번 더 누르면 그 화면에서 바로
    assert got["iframes"] == 2 and got["flag"] == "1" and got["taps"] == 2 and got["dialogs"] == [], got
    assert any("스스로 이어가기가 안 돼요 — 화면을 다시 열게요. 열리면 책갈피를 한 번 더" in x for x in got["bars"])
    assert [(d["id"], d["r"], d["u"]) for d in got["done"]] == [("pub:issue:11", "hold", None), ("pub:issue:11", "ok", "223000000001")] and len(got["pub"]) == 1
    assert got["last_url"] == "https://blog.naver.com/derenpick/223000000001"
