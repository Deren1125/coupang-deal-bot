"""'하루치 한 번에 (발행까지)' 책갈피 (주인 11/23 — 책갈피 한 번에 글 여러 개를 넣고 발행 누르는 것까지).

1) autopub.bookmarklet(): 진행기(autopub.js + kkumi.js 통째, PATCHES · 붙여넣기 칸 뺌)를 주소에 통째로 (원격 코드 없음) · 길이 한도 · node 로 읽힘 · 꾸미기 책갈피는 그대로.
2) autopub.act(): 열쇠 · 그 블로그의 올려도 되는 글만 (점검 통과 · 새 방향과 안 맞는 글·고지 빠진 글 뺌) · 예약 시각(첫 글의 지난 자리만 지금) · 40~120초 ·
   한 판 10편(11/25 — 예전 5) · 발행 전 '해 봄' · 멈춘 글 다시 · 네이버 안내 뒤 24시간 쉼 · 발행 확인이면 할 일 줄 체크.
3) GET /dashboard/api/autopub (로그인 아래) · POST /dashboard/api/autopub_act (열쇠 · CORS 는 네이버 블로그 화면만) · 연결 탭 칸 · 할 일 카드 한 줄.
4) 가짜 글쓰기 화면 (진짜 크롬, playwright 가 있을 때만): 한 번 눌러 글 2편을 채우고 발행까지 (지금 발행 + 예약) · 점검에 걸리면 발행하지 않고 멈춤 → 고치고
   단추를 누르면 이어서 발행 · 카테고리·예약 칸을 못 채우면 멈춤 · 네이버 보호 안내면 바로 멈춤 · 다른 블로그면 거절 · '글마다 한 번'(DIR) 길.
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
from test_dash_kkumi import INNER, PASTED, _playwright_env, _tags_line
from test_dash_kkumi import RUN as KK_RUN
from test_dash_kkumi import code as kkumi_code
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
    assert v["version"] == autopub.version() == 11 and "지금 판 v11" in v["note"]
    assert "#" not in v["code"] and v["code"] == "javascript:" + kkumi.encode_short(js)
    assert "u:'https://dash.example/dashboard/api/autopub_act'" in js and f"k:'{autopub.key(db)}'" in js and "@@" not in js
    assert '["derenpick","issue","데런픽"]' in js and "credentials:'omit'" in js and autopub._join(autopub._zcore(kkumi.minify(autopub._core(), let=True))) in js
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
    assert kkumi.version() == 15 and "throw 0;" in src and len(kkumi.bookmarklet()) < 31000
    # 껍데기: 알림·확인·입력 창 없음 · 마지막 발행 단추는 '해 봄'(hold)을 남긴 뒤 한 곳에서만 누름
    for word in (".click(", "requestSubmit", ".submit(", "alert(", "confirm(", "prompt(", "eval(", "document.cookie"):
        assert word not in shell, word
    assert shell.count("K.press(b)") == 1 and shell.index("r: 'hold'") < shell.index("K.press(b)") and shell.count("const b = fin();") == 1
    # v5 (주인 11/23 14:22): 발행 단추 바로 전 '예약'/'현재'를 다시 읽어 계획과 다르면 누르지 않음 (예약을 못 채운 글이 지금 발행되지 않게)
    g = shell.index("else if (!!it.at !== booked())")
    assert shell.index("await S(300);\n      if (tt() !== sp(it.title))") < g < shell.index("r: 'hold'") < shell.index("K.press(b)")
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
# 아래 시험들은 예약 길을 봄 → Blog-Auto 기록에 '오늘 세 블로그 다 발행함'을 넣어 둠 (v7 첫 글 지금 발행은 test_dash_autopub_v7)
PUBBED = {"finance": 1, "issue": 1, "hotdeal": 1}


def _go(bot: DealBot, now: float, pub: dict[str, Any] | None = PUBBED, **form: str) -> dict[str, Any]:
    return autopub.act(bot.db, {"k": autopub.key(bot.db), **form}, tz=TZ, now=now, rv=routine.view(bot, now=now), rng=Rng(), pub=pub)


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
    # 첫 글 (v5 · 주인 11/23 13:45 '누른 시간 기준으로 나눠'): 누른 때(10:00) + 20~45분 → 10분 자리(10·20·40·50)로 예약 · 맨 위 한 줄에 남은 글 셋의 계획
    q = _go(bot, t, a="next", b="issue")
    assert (q["n"], q["m"], q["left"], q["wait"]) == (1, 3, 3, 0) and q["next"]["id"] == "pub:issue:13" and q["next"]["title"] == "글 바로"
    d13 = datetime.fromisoformat(q["next"]["at"])
    assert datetime(2026, 11, 18, 10, 20) <= d13 <= datetime(2026, 11, 18, 10, 55) and d13.minute in autopub.MINUTES and q["next"]["label"] == f"오늘 {d13:%H:%M}"
    assert kkumi.stamped(q["next"]["html"]) == q["next"]["at"] and q["next"]["disc"] is False and q["next"]["photos"] == 0
    assert q["plan"].startswith(f"오늘 예약 {d13:%H:%M} · ") and q["plan"].count(" · ") == 2
    # 발행 직전 '해 봄' → 그 글은 다시 주지 않음 (확인을 못 해도 두 번 발행하지 않게) · 확인되면 할 일 줄 체크 + 블로그 프로그램에 pasted + 글 번호
    assert _go(bot, t + 5, a="done", b="issue", id="pub:issue:13", r="hold") == {"ok": True, "checked": False, "text": ""}
    assert _go(bot, t + 6, a="next", b="issue")["next"]["id"] == "rsv:issue:12"
    with pytest.raises(autopub.CaptureError) as e:  # 다른 탭·창이 같은 글의 발행을 또 누르려 하면 거절 (두 번 발행 막기)
        _go(bot, t + 7, a="done", b="issue", id="pub:issue:13", r="hold")
    assert e.value.status == 409 and e.value.code == "dup"
    d = _go(bot, t + 10, a="done", b="issue", id="pub:issue:13", r="ok", u="223000000001")
    assert d == {"ok": True, "checked": True, "text": "할 일 줄 체크 ✓", "left": 2} and "pub:issue:13" in routine.done_ids(bot.db, DAY)
    assert [(r["name"], r["args"], r["rid"]) for r in routine.relay_rows(bot.db)] == [("pasted", {"blog": "issue", "pid": 13, "done": True}, "pub:issue:13")]
    assert json.loads(bot.db.kv_get(autopub.DAY_KEY + DAY))["res"][-1]["u"] == "223000000001"
    # 둘째 글: 같은 계획의 다음 자리 (v6 — 40분~4시간 남짓 · 들쭉날쭉 · 분 자리가 앞 글과 다름) · 앞 글 뒤 77초가 될 때까지 쉼
    q = _go(bot, t + 20, a="next", b="issue")
    nx = q["next"]
    assert (q["n"], q["m"], q["wait"]) == (2, 3, 67) and nx["id"] == "rsv:issue:12" and kkumi.stamped(nx["html"]) == nx["at"] != ""
    d12 = datetime.fromisoformat(nx["at"])
    assert timedelta(minutes=40) < d12 - d13 <= timedelta(minutes=330) and d12.minute in autopub.MINUTES and d12.minute != d13.minute and nx["label"] == f"오늘 {d12:%H:%M}"
    assert _go(bot, t + 100, a="next", b="issue")["wait"] == 0
    # 점검에 걸려 멈춘 글: 발행하지 않았으므로 다시 누르면 같은 글부터 · 두 번 멈추면 오늘은 주지 않음 (직접)
    _go(bot, t + 110, a="done", b="issue", id="rsv:issue:12", r="stop", n="사진 0/1 — 빠진 사진을 넣어 주세요")
    assert _go(bot, t + 120, a="next", b="issue")["next"]["id"] == "rsv:issue:12" and "rsv:issue:12" not in routine.done_ids(bot.db, DAY)
    td = autopub.today(bot.db, t + 120, TZ, routine.view(bot, now=t + 120))["by"]["issue"]
    assert td["line"] == "하루치 한 번에: 올린 글 1 · 멈춘 글 1 — 사진 0/1 — 빠진 사진을 넣어 주세요 · 준비된 글 2편 — 글쓰기 화면에서 책갈피 한 번"
    _go(bot, t + 130, a="done", b="issue", id="rsv:issue:12", r="stop", n="사진 0/1")
    q = _go(bot, t + 140, a="next", b="issue")
    assert q["next"]["id"] == "rsv:issue:11" and (q["n"], q["m"]) == (2, 2)
    d11 = datetime.fromisoformat(q["next"]["at"])  # 계획의 셋째 자리 (멈춘 글의 자리와 겹치지 않음)
    assert d11 - d12 > timedelta(minutes=40) and d11.minute in autopub.MINUTES
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


def test_slots_come_from_the_tap_time_and_a_retap_keeps_the_plan(bot: DealBot, tmp_path: Path) -> None:
    # v5 (주인 11/23 13:45 — 예전엔 대시보드 계획 시각(같은 자리 · 지난 자리는 다음 날 아침 08:50)을 써 '다 똑같은 것 같다'): 누른 때 기준
    rows = _rows(DAY)
    rows["items"] = [i for i in rows["items"] if i["id"] in ("rsv:issue:11", "rsv:issue:12")]
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "14:00")
    q = _go(bot, t, a="next", b="issue")
    a12 = q["next"]["at"]
    assert q["next"]["id"] == "rsv:issue:12" and datetime(2026, 11, 18, 14, 20) <= datetime.fromisoformat(a12) <= datetime(2026, 11, 18, 14, 55)
    _go(bot, t + 1, a="done", b="issue", id="rsv:issue:12", r="ok")
    q = _go(bot, t + 2, a="next", b="issue")
    a11 = q["next"]["at"]
    assert q["next"]["id"] == "rsv:issue:11" and a11.startswith("2026-11-18T") and q["wait"] == 76  # 오늘 안 (다음 날 아침으로 밀지 않음)
    assert timedelta(minutes=40) < datetime.fromisoformat(a11) - datetime.fromisoformat(a12) <= timedelta(minutes=330) and q["plan"] == f"오늘 예약 {a11[11:]}"
    # 새 글쓰기 화면에서 다시 눌러도 (그날 기록이 없어도) 남은 글은 같은 시각
    bot.db.kv_set(autopub.DAY_KEY + DAY, "{}")
    q = _go(bot, t + 600, a="next", b="issue")
    assert q["n"] == 1 and q["next"]["id"] == "rsv:issue:11" and q["next"]["at"] == a11


def test_a_post_whose_publish_was_pressed_is_never_served_again_even_after_the_day_record_is_gone(bot: DealBot, tmp_path: Path) -> None:
    rows = _rows(DAY)
    rows["items"] = [i for i in rows["items"] if i["id"] in ("rsv:issue:11", "rsv:issue:12")]
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "10:00")
    assert _go(bot, t, a="next", b="issue")["next"]["id"] == "rsv:issue:12"
    _go(bot, t + 1, a="done", b="issue", id="rsv:issue:12", r="hold")
    _go(bot, t + 2, a="done", b="issue", id="rsv:issue:12", r="stop", n="네이버 안내")
    _go(bot, t + 3, a="done", b="issue", id="rsv:issue:12", r="stop", n="네이버 안내")  # 두 번 멈춰도 '눌러 봄'은 그대로
    assert json.loads(bot.db.kv_get(autopub.DAY_KEY + DAY))["tried"]["rsv:issue:12"] == "hold"
    bot.db.kv_set(autopub.DAY_KEY + DAY, "{}")  # 날이 바뀌어 그날 기록이 없어도 (확인 못 한 글이 다음 날 목록에 남아 있어도)
    assert _go(bot, t + 10, a="next", b="issue")["next"]["id"] == "rsv:issue:11"
    with pytest.raises(autopub.CaptureError) as e:
        _go(bot, t + 11, a="done", b="issue", id="rsv:issue:12", r="hold")
    assert e.value.code == "dup"
    assert _go(bot, t + 12, a="done", b="issue", id="rsv:issue:12", r="ok")["checked"] is True  # 늦게 온 확인은 받음


def test_run_cap_weekends_are_normal_days_and_protection_cooldown(bot: DealBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = {"day": DAY, "slots": SLOTS, "overflow": 0, "items": [
        item(f"pub:issue:{n}", "s1", group="발행", blog="issue", title=f"데런픽 발행 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in range(1, 13)]}
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "10:00")
    ats = []
    m = None
    for n in range(1, 11):  # 11/26: 낮에는 오늘 자리 글만 (블로그 하루 DAY_MAX) — 내일 자리 글은 저녁 8시 뒤 한 번에
        q = _go(bot, t + n * 200, a="next", b="issue")
        if q["next"] is None:
            break
        m = m or q["m"]
        assert q["next"]["id"] == f"pub:issue:{n}" and (q["n"], q["m"]) == (n, m)
        ats.append(q["next"]["at"])
        _go(bot, t + n * 200 + 1, a="done", b="issue", id=f"pub:issue:{n}", r="ok")
    assert len(ats) == m <= autopub.DAY_MAX and ats == sorted(ats) and len(set(ats)) == m
    assert all(a == "" or a.startswith(DAY) for a in ats)  # 오늘 것만 (첫 글은 지금 발행)
    assert q["next"] is None and q["left"] == 0 and q["later"] > 0 and f"내일 글 {q['later']}편은 저녁 8시 뒤 한 번에" in q["text"], q
    # 한 판 상한 (RUN_MAX — 시험에서는 작게)
    monkeypatch.setattr(autopub, "RUN_MAX", 3)
    with_routine(tmp_path, {**rows, "items": [item(f"pub:finance:{n}", "s1", group="발행", blog="finance", title=f"트로트 · {n}", paste=_paste(f"글 {n}"), gate=OK)
                                              for n in range(1, 6)]})
    for n in range(1, 4):
        q = _go(bot, t + n * 200, a="next", b="finance")
        assert q["next"]["id"] == f"pub:finance:{n}" and (q["n"], q["m"]) == (n, 3)
        _go(bot, t + n * 200 + 1, a="done", b="finance", id=f"pub:finance:{n}", r="ok")
    q = _go(bot, t + 900, a="next", b="finance")
    assert q["next"] is None and q["left"] == 2 and q["text"] == "트로트: 한 번에 3편까지만 올려요 (남은 글 2) — 30분쯤 뒤에 다시 눌러 주세요"
    q = _go(bot, t + 801 + autopub.RUN_IDLE_S + 60, a="next", b="finance")  # 한참 뒤 다시 누르면 새 판
    assert q["next"]["id"] == "pub:finance:4" and q["n"] == 1
    monkeypatch.undo()
    with_routine(tmp_path, rows)
    # 네이버 보호·제한 안내 → 그 블로그만 24시간 쉼 (다시 눌러도 주지 않음 · 발행 전 '해 봄'도 거절 → 책갈피는 발행을 누르지 않음) · kv 에 until
    t2 = t + 5000
    d = _go(bot, t2, a="done", b="issue", id="pub:issue:11", r="wall", n="자동입력 방지")
    assert d["checked"] is False and d["until"] == t2 + 86400 and d["text"] == "네이버 보호·제한 안내로 데런픽 하루치 한 번에는 11/19 11:23까지 쉬어요 — 그동안은 직접 올려 주세요"
    cool = json.loads(bot.db.kv_get(autopub.KV_COOL))
    assert cool["issue"]["until"] == t2 + 86400 and cool["issue"]["why"] == "자동입력 방지" and list(cool) == ["issue"]
    for form in ({"a": "next"}, {"a": "done", "id": "pub:issue:11", "r": "hold"}):
        with pytest.raises(autopub.CaptureError) as e:
            _go(bot, t2 + 3600, b="issue", **form)
        assert e.value.status == 429 and e.value.code == "cool" and "11/19 11:23까지" in e.value.text
    assert _go(bot, t2 + 60, a="done", b="issue", r="wall")["until"] == t2 + 86400  # 글 번호 없이 와도 · 이미 쉬는 중이면 늘리지 않음
    assert autopub.today(bot.db, t2 + 60, TZ, routine.view(bot, now=t2 + 60))["by"]["issue"]["line"].endswith("11/19 11:23까지 쉬어요 — 그동안은 직접 올려 주세요")
    assert _go(bot, t2 + 60, a="next", b="finance")["next"] is None  # 다른 블로그는 쉬지 않음 (올릴 글이 없을 뿐)
    # 주인 11/24 — 주말도 평일처럼 (11/21 주말 쉼을 지움): 토·일에도 '주말에도 시작' 없이 바로 · 옛 주말 쉼 장치도 없음
    sat = "2026-11-21"
    assert not hasattr(autopub, "weekend") and not hasattr(autopub, "WEEKEND_PAUSE") and not hasattr(autopub, "WEEKEND_BACK_H")
    with_routine(tmp_path, {**rows, "day": sat})
    ts = at(bot, sat, "10:00")
    q = _go(bot, ts, a="next", b="finance")
    assert "weekend" not in q and q["next"] is None  # (드라마는 올릴 글이 없음 — 주말 막이 없음)
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
    assert status == 200 and k in unquote(v["code"]) and hd["Cache-Control"] == "no-store" and v["version"] == 11
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
    with_routine(tmp_path, _rows(now.date().isoformat()))
    status, v, _ = await get_json(bot, "/dashboard/api/routine", session_headers(bot))
    assert status == 200 and v["autopub"]["issue"] == "하루치 한 번에: 준비된 글 3편 — 글쓰기 화면에서 책갈피 한 번"  # 토·일도 같은 줄
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
  const ui = async () => { for (const f of p.frames()) { try { const t = await f.evaluate(() => { const e = document.querySelector('#apb'); return e ? [e.children[0].textContent, e.children[2].style.display !== 'none' ? e.children[2].textContent : '', [...e.children[1].children].map(x => x.textContent), e.children[3] && e.children[3].style.display !== 'none' ? e.children[3].textContent : ''] : null; }); if (t) return [f, ...t]; } catch (e) {} } return null; };
  for (let i = 0; i < 1300; i++) {
    await p.waitForTimeout(200);
    if (opt.give_up && i * 200 > opt.give_up * 1000) break;
    const u = await ui();
    if (!u && out.reload) { out.reload = 0; out.flag = await p.evaluate(() => localStorage.getItem('apbd')).catch(() => null); await tap(); continue; }
    if (!u) {
      // 글마다 한 번: 발행 뒤 화면이 글 주소로 옮겨감 (진행기 사라짐) → 남은 글이 있으면 주인이 글쓰기 화면에서 한 번 더
      if ((opt.dir || opt.noframe) && !/postwrite/.test(p.url())) { out.moved = (out.moved || 0) + 1; out.last_url = p.url(); if (out.done.filter(d => d.r === 'ok').length < queue.length && out.taps < 4) { await tap(); continue; } break; }
      continue;
    }
    const [f, bar, btn, lines, cp] = u;
    if (cp) out.cp = cp;
    const fresh = out.bars[out.bars.length - 1] !== bar;
    if (fresh) out.bars.push(bar);
    // v4: 주인 손 (Playwright 누름 = 진짜 누름 isTrusted) — 진행기가 '① 제목 칸' · '② 본문' 을 누르라고 하면 그 칸을 한 번 누름
    const ed = () => p.frames().filter(x => x.url().includes('PostWriteForm') && !x.isDetached()).pop();
    // v5: 본문에 든 제목 줄을 주인이 지움 (unbody — 진짜 지우기는 시험에서 못 만들어 본문 글 줄을 비움) → 다시 제목 칸
    if (fresh && /본문에 들어간 제목 줄을 지운 뒤/.test(bar)) { out.unbody_bar = bar; if (opt.unbody) await ed().evaluate(() => { for (const p of document.querySelectorAll('.se-content .se-text-paragraph')) p.innerHTML = '<br>'; }); continue; }
    if (fresh && opt.hands !== false && /① 제목 칸을 한 번 눌러|큰 글씨 칸\)을 눌러/.test(bar)) { out.hands = (out.hands || 0) + 1; if (opt.wrongtap) { await ed().click('.se-content .se-text-paragraph'); if (opt.wrongtap !== 'once') continue; await p.waitForTimeout(1500); } await ed().click('.se-documentTitle .se-text-paragraph'); continue; }
    if (fresh && opt.hands !== false && /본문을 한 번 더 눌러/.test(bar)) { out.hands = (out.hands || 0) + 1; out.again = (out.again || 0) + 1; await ed().click('.se-content .se-text-paragraph'); continue; }
    if (fresh && opt.hands !== false && /② 본문을 한 번 눌러|넓은 칸\)을 눌러/.test(bar)) { out.hands = (out.hands || 0) + 1; out.broken = out.broken || await ed().evaluate(() => window.__se1b ? window.__se1b() : 0); await ed().click('.se-content .se-text-paragraph'); continue; }
    // 제목이 안 들어감: 주인이 「제목 복사」 → 제목 칸을 길게 눌러 붙여넣기 (진짜 붙여넣기는 시험에서 못 만들어 __ownerPaste 표시로 대신)
    if (fresh && /「제목 복사」 → 제목 칸을 길게 눌러 붙여넣기/.test(bar)) { out.hands = (out.hands || 0) + 1; await f.click('#apb button:nth-of-type(2)'); const e = ed(); await e.click('.se-documentTitle .se-text-paragraph'); await e.evaluate(t => { window.__ownerPaste = 1; const dt = new DataTransfer(); dt.setData('text/plain', t); document.activeElement.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt})); window.__ownerPaste = 0; }, queue.find(x => bar.includes(x.title)).title); continue; }
    out.lines = lines;
    if (/화면을 다시 열게요/.test(bar)) out.reload = 1;
    if (/ · v\d+$/.test(bar) || /(?<!① 제목 칸을 한 번 |큰 글씨 칸\)을 |넓은 칸\)을 )눌러 주세요$|아무것도 하지 않았어요$/.test(bar)) { out.end = bar; break; }
    if (btn) {
      out.btn = btn;
      if (opt.fix && !out.fixed) {
        out.fixed = 1;
        out.pub_at_stop = out.pub.length;
        const ed = p.frames().filter(x => x.url().includes('PostWriteForm') && !x.isDetached()).pop();
        if (opt.fix === 'photo') await ed.evaluate(() => { for (const i of document.querySelectorAll('.se-image img')) i.src = 'https://blogfiles.pstatic.net/fixed.jpg'; });
        // 주인: 제목 칸을 손으로 누르고(진짜 누름) 붙여넣기 → 책갈피 한 번 더
        if (opt.fix === 'title') { await ed.click('.se-documentTitle .se-text-paragraph'); await ed.evaluate(t => { const dt = new DataTransfer(); dt.setData('text/plain', t); document.activeElement.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt})); }, queue[0].title); await p.waitForTimeout(1200); out.title_at_fix = await ed.evaluate(() => document.querySelector('.se-documentTitle').textContent); p.evaluate(loader).catch(() => {}); continue; }
        // v5 '포스트잇 글 복사': 주인이 복사 단추 → 빈 포스트잇 안을 누름(진짜 누름) → 붙여넣기(__ownerPaste 표시로 대신) → 초록 단추
        if (opt.fix === 'postit') { out.cp_at_stop = out.cp; out.stop_bar = bar; await f.click('#apb button:nth-of-type(2)'); const qs = await ed.$$('.se-quotation'); for (let i = 0; i < qs.length; i++) { if (await qs[i].evaluate(q => q.textContent.trim())) continue; await (await qs[i].$('.se-text-paragraph')).click(); await ed.evaluate(t => { window.__ownerPaste = 1; const dt = new DataTransfer(); dt.setData('text/plain', t); document.activeElement.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt})); window.__ownerPaste = 0; }, opt.pit[i]); } await f.click('#apb button'); continue; }
        if (opt.fix === 'retap') { p.evaluate(loader).catch(() => {}); await p.waitForTimeout(1500); out.retap_bar = (await ui())[1]; break; }
        await f.click('#apb button');
        continue;
      }
      if (!opt.fix || (out.fixed && fresh)) { out.end = bar; break; }
    }
  }
  for (const x of p.frames()) { try { const v = await x.evaluate(() => window.__se1b ? window.__se1b() : 0); if (v) out.broken = v; } catch (e) {} }
  out.url = p.url();
  out.frames = await p.evaluate(() => document.querySelectorAll('iframe').length).catch(() => -1);
  out.sandbox = await p.evaluate(() => { const f = document.querySelector('iframe'); return f ? f.getAttribute('sandbox') : null; }).catch(() => null);
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


# SE ONE 처럼 (주인 11/23 아이패드 — v1 제목이 본문 첫 줄에 들어감): 제목·본문이 한 편집 칸(contenteditable) 안 · 에디터 커서는 누른 곳(pointer/mouse down)을 따르고,
# 누르지 않고 편집 칸에 포커스만 오면 본문 끝 커서를 되살림 (본문이 포커스를 가져감) · 붙여넣기 신호는 DOM 선택이 아니라 에디터 커서 자리에 들어감.
# steal = 첫 붙여넣기는 무조건 본문으로 (본문에 들어간 제목을 지우고 다른 길로 한 번 더 하는지) · stuck = 거기에 글 넣기 명령도 듣지 않음 (둘 다 안 되면 멈춤).
# v3 (주인 11/23 11:36 아이패드 — v2 도 '제목을 넣지 못했어요'): ipad = 진짜 아이패드처럼 스크립트로 누른 것(isTrusted 아님)은 커서를 옮기지 않음 (꾸미기 10/29) ·
#   편집 칸 포커스는 커서를 본문 끝으로 · 처음 커서는 제목 · 제목에 붙인 글은 0.5초 뒤 span(__se-node) 안에 다시 그림 · 글 넣기 명령으로 바뀐 제목 칸은 0.3초 뒤 에디터가
#   원래대로 되돌림 (에디터 모형에 없는 글) → v2 의 차례(포커스 → 누름 → 붙여넣기 → 글 넣기 명령)로는 본문에 들어갔다 지워지고 제목은 빈 칸 = 주인 화면 그대로.
#   ipadbody = 같은데 처음 커서도 본문 (어느 길로도 제목에 못 감 → 본문만 넣고 발행 전에 멈춤 · '제목 복사' → 주인이 제목 칸을 눌러 붙여넣고 다시 누르면 발행).
SE1 = r"""<script>
(() => {
const STEAL = __STEAL__;
if (STEAL === 2) { const ex = document.execCommand.bind(document); document.execCommand = (c, ...a) => c === 'insertText' ? false : ex(c, ...a); }
wbFocus = 'body';
const C = document.querySelector('.se-content'), TB = document.querySelector('.se-documentTitle'), TP = TB.querySelector('.se-text-paragraph');
const host = document.createElement('div');
host.setAttribute('contenteditable', 'true');
TB.before(host); host.append(TB, C); C.removeAttribute('contenteditable');
const IPAD = STEAL > 2;
let cur = STEAL === 4 ? 'body' : 'title', fwd = 0, n = 0, model = '', tap = 0;
window.__se1 = () => cur;
const where = t => t && t.closest ? (t.closest('.se-documentTitle') ? 'title' : t.closest('.se-content') ? 'body' : null) : null;
for (const ty of ['pointerdown', 'mousedown']) document.addEventListener(ty, e => { const w = where(e.target); if (w && (!IPAD || e.isTrusted)) { cur = w; tap = Date.now(); } }, true);
host.addEventListener('focus', () => { if (!IPAD || Date.now() - tap > 300) cur = 'body'; });
const draw = () => { TP.innerHTML = model ? '' : '<span class="se-placeholder __se_placeholder">제목</span>'; if (model) { const s = document.createElement('span'); s.className = 'se-ff-nanumgothic se-fs32 __se-node'; s.textContent = model; TP.append(s); } };
if (IPAD) { draw(); host.addEventListener('input', () => setTimeout(draw, 300)); }
document.querySelector('.se-canvas-bottom-button').addEventListener('click', () => { cur = 'body'; });
const unph = () => { for (const s of TP.querySelectorAll('.se-placeholder')) s.replaceWith(s.textContent.replace('제목', '')); };
if (!IPAD) host.addEventListener('input', () => { if (TP.textContent !== '제목') unph(); });
window.addEventListener('paste', e => {
  if (fwd) return;
  e.stopImmediatePropagation(); e.preventDefault();
  const dt = e.clipboardData, to = STEAL && STEAL < 3 && !n++ ? 'body' : cur;
  if (to === 'title' && !dt.files.length) { if (IPAD) { model += dt.getData('text/plain'); setTimeout(draw, 500); return; } unph(); TP.textContent += dt.getData('text/plain'); return; }
  let d2 = dt;
  if (!dt.files.length && !dt.getData('text/html')) { d2 = new DataTransfer(); const p = document.createElement('p'); p.textContent = dt.getData('text/plain'); d2.setData('text/html', p.outerHTML); }
  fwd = 1; try { C.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: d2})); } finally { fwd = 0; }
}, true);
})();
</script></body></html>"""


# v4 (주인 11/23 12시 아이패드 — v3 도 제목·본문 둘 다 빈 칸, 배너 '글 안 들어감 · 태그 줄이 없어 · 제목 칸이 비었어요 · 본문이 원고와 달라요 · 사진 0/14 · 글 차례'):
# 진짜 기기에서 드러난 것만으로 맞춘 SE ONE 모형 (real). 근거:
#   · 꾸미기 10/29 아이패드: 스크립트로 커서를 옮기는 6가지 모두 안 됨 · 붙여넣기 신호는 됨 (주인이 누른 본문 끝에) · 툴바·'본문 추가' 단추는 커서를 옮김
#     · 꾸미기는 에디터 글을 스크립트로 지우거나 고치지 않음 (붙여넣기 신호·단추 누름·발행창 칸만) — 그래서 실기기에서 돎
#   · 하루치 v1·v2: 새로 연 글쓰기 창에서 포커스 → 붙여넣기 → 제목이 본문 칸으로 (누른 적 없는 창의 포커스 = 본문)
#   · 하루치 v3: 아무것도 누르기 전 붙여넣기 → 아무 데도 안 들어감 · 본문에 든 제목을 DOM 에서 지운(c.remove·textContent) 뒤로 꾸미기 붙여넣기가 하나도 안 들어감
# 그래서: 커서는 진짜 누름(isTrusted)·'본문 추가'만 옮기고 처음엔 없음(null) · 커서 없이 포커스 → 본문 · 커서 없이 붙여넣기 → 버림 ·
#   스크립트가 편집 칸 안 DOM 을 직접 지우거나 바꾸면 모형과 화면이 어긋나 그 뒤 붙여넣기를 모두 버림(broken) · 글 넣기 명령·되돌리기는 안 먹음.
#   realnt = 제목 칸이 스크립트 붙여넣기를 안 받는 경우 (실기기에서 아직 모름 — 주인이 「제목 복사」로 직접 붙여넣는 길을 시험).
SE1R = r"""<script>
(() => {
const NT = __NT__;
wbFocus = 'body';
const C = document.querySelector('.se-content'), TB = document.querySelector('.se-documentTitle'), TP = TB.querySelector('.se-text-paragraph');
const host = document.createElement('div');
host.setAttribute('contenteditable', 'true');
TB.before(host); host.append(TB, C); C.removeAttribute('contenteditable');
let cur = null, model = '', own = 0, broken = 0, fwd = 0;
// 에디터는 자기 편집 칸의 붙여넣기만 받음 (꾸미기 붙여넣기 칸은 에디터 문서 안이지만 편집 칸 밖 — 실기기에서 그 칸에 붙여 넣어짐)
window.__se1 = () => cur; window.__se1b = () => broken;
const mine = f => { own++; try { return f(); } finally { own--; } };
const inH = n => n && n.isConnected && host.contains(n);
const bad = n => { if (!own && inH(n)) broken = 1; };
const er = Element.prototype.remove; Element.prototype.remove = function () { bad(this); return er.apply(this, arguments); };
const rc = Node.prototype.removeChild; Node.prototype.removeChild = function (c) { bad(c); return rc.apply(this, arguments); };
for (const [P, k] of [[Node.prototype, 'textContent'], [Element.prototype, 'innerHTML']]) {
  const d = Object.getOwnPropertyDescriptor(P, k);
  Object.defineProperty(P, k, {configurable: true, get: d.get, set(v) { bad(this); return d.set.call(this, v); }});
}
const ex = document.execCommand.bind(document);
document.execCommand = (c, ...a) => /insertText|undo|delete|insertHTML/i.test(c) ? false : ex(c, ...a);
// 에디터 자기 단추(툴바·본문 추가·발행창)가 바꾸는 것은 에디터 몫
window.addEventListener('click', () => { own++; queueMicrotask(() => own--); }, true);
const draw = () => mine(() => { TP.innerHTML = model ? '' : '<span class="se-placeholder __se_placeholder">제목</span>'; if (model) { const s = document.createElement('span'); s.className = 'se-ff-nanumgothic se-fs32 __se-node'; s.textContent = model; TP.append(s); } });
draw();
const where = t => t && t.closest ? (t.closest('.se-documentTitle') ? 'title' : t.closest('.se-content') ? 'body' : null) : null;
for (const ty of ['pointerdown', 'mousedown', 'touchstart']) document.addEventListener(ty, e => { const w = where(e.target); if (w && e.isTrusted) cur = w; }, true);
host.addEventListener('focus', () => { if (!cur) cur = 'body'; });
document.querySelector('.se-canvas-bottom-button').addEventListener('click', () => { cur = 'body'; });
window.addEventListener('paste', e => {
  if (fwd || !host.contains(e.target)) return;
  e.stopImmediatePropagation(); e.preventDefault();
  const dt = e.clipboardData, real = e.isTrusted || window.__ownerPaste;
  if (broken || !cur) return;
  if (cur === 'title' && !dt.files.length) { if (NT && !real) return; model += dt.getData('text/plain'); setTimeout(draw, 500); return; }
  let d2 = dt;
  if (!dt.files.length && !dt.getData('text/html')) { d2 = new DataTransfer(); const p = document.createElement('p'); p.textContent = dt.getData('text/plain'); d2.setData('text/html', p.outerHTML); }
  fwd = 1; try { mine(() => C.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: d2}))); } finally { fwd = 0; }
}, true);
})();
</script></body></html>"""


# v5 (주인 11/23 13시 아이패드 v4 — 제목·본문·발행창(카테고리·태그·예약)은 다 됐는데 '포스트잇 글이 안 들어감'으로 멈춤 · 드라마 글 「굿파트너2 2회」):
# 같은 꾸미기 코드(v10 — 하루치는 kkumi.js 를 통째로 씀)가 꾸미기 책갈피로는 실기기 포스트잇이 되므로 다른 것은 손의 자리뿐.
#   · 꾸미기: 주인이 본문을 누름 → 책갈피 → 붙여넣기 칸(편집 칸 밖)에 포커스·붙여넣기 → 칸이 사라짐 = 주인 손이 편집 칸에서 빠진 뒤 시작
#   · 하루치 v4: 주인이 본문을 누른 채(편집 칸에 손이 남음) 바로 시작
# 꾸미기 v7 (11/16 드라마 표 인용구 — 글 바로 다음 포스트잇은 뒤에 빈 글 줄이 따라와 에디터 커서가 그 줄): 포스트잇 안 칸을 고르고(setSel) 붙여 넣으면 안으로 · 그 고르기는
#   주인 손이 편집 칸에 남아 있는 동안에는 에디터가 받지 않음 (모형 · 진짜 누름은 늘 받음) → v4 는 표 포스트잇 글이 아래 빈 줄로 = '포스트잇 글이 안 들어감' (차례·사진·글은 그대로라 그 한 줄만).
# realpidrop = 주인 11/23 13:40 v4 3/4 (본문을 눌렀는데 첫 조각이 안 들어감 — 빈 본문 안내 글 앞 커서): 본문 첫 붙여넣기 하나를 버림
# realpi = SE1R + 위 규칙 · realpint = 포스트잇이 스크립트 붙여넣기를 아예 안 받음 (끝까지 안 되면 주인이 「포스트잇 글 복사」로 직접 — 그 길 시험).
PI_INNER = (
    ("let inPostit = null, selected = null, align = 'left';\n",
     "let inPostit = null, selected = null, align = 'left';\n"
     "document.addEventListener('pointerdown', e => { if (e.isTrusted && e.target.closest && e.target.closest('.se-content')) inPostit = e.target.closest('.se-quotation'); }, true);\n"),
    ("C.addEventListener('paste', e => {\n  e.preventDefault();",
     "C.addEventListener('paste', e => {\n  e.preventDefault();\n"
     "  if (window.__drop > 0 && !e.clipboardData.files.length) { window.__drop--; return; }\n"
     "  if (!window.__sess) { const a = getSelection().anchorNode, el = a && (a.nodeType === 1 ? a : a.parentElement); inPostit = (el && el.closest('.se-quotation')) || null; }\n"
     "  if (inPostit && window.__pint && !window.__ownerPaste && !e.clipboardData.files.length) return;"),
    ("const c = add(comp('se-quotation se-l-quotation_postit', '<div class=\"qbox\"></div>')); inPostit = c;",
     "const prev = C.lastElementChild; const c = add(comp('se-quotation se-l-quotation_postit', '<div class=\"qbox\"><p class=\"se-text-paragraph\" style=\"min-height:1em\"></p></div>')); inPostit = c;"
     " if (prev && /se-text/.test(prev.className) && prev.textContent.trim()) { add(comp('se-text', '<div class=\"se-section se-section-align-left\"><p class=\"se-text-paragraph\"></p></div>')); inPostit = null; }"),
    ("text: document.querySelector('.se-content').innerText,",
     "text: document.querySelector('.se-content').innerText, postits: [...document.querySelectorAll('.se-quotation')].map(q => q.textContent.trim()), outside: [...document.querySelectorAll('.se-content > .se-component:not(.se-quotation)')].map(c => c.textContent).join(' '),"),
)
PI = r"""<script>
(() => {
window.__pint = __PINT__; window.__drop = __DROP__; window.__sess = 0;
const host = document.querySelector('.se-content').parentElement;
for (const ty of ['pointerdown', 'mousedown', 'touchstart']) document.addEventListener(ty, e => { if (e.isTrusted && host.contains(e.target)) window.__sess = 1; }, true);
host.addEventListener('focusout', () => { window.__sess = 0; });
})();
</script></body></html>"""


def _editor(rsv: str = "full", se1: str = "") -> str:
    h = INNER.replace("</body></html>", RSV.replace("__MODE__", rsv)).replace("</body></html>", EXTRA).replace("</body></html>", PRE_WALL).replace("</body></html>", TITLE_MID)
    if se1 in ("real", "realnt"):
        return h.replace("</body></html>", SE1R.replace("__NT__", "1" if se1 == "realnt" else "0"))
    if se1 in ("realpi", "realpint", "realpidrop"):
        for a, b in PI_INNER:
            assert h.count(a) == 1, a
            h = h.replace(a, b)
        return h.replace("</body></html>", SE1R.replace("__NT__", "0")).replace("</body></html>", PI.replace("__PINT__", "1" if se1 == "realpint" else "0").replace("__DROP__", "1" if se1 == "realpidrop" else "0"))
    return h.replace("</body></html>", SE1.replace("__STEAL__", {"steal": "1", "stuck": "2", "ipad": "3", "ipadbody": "4"}.get(se1, "0"))) if se1 else h


# v8 (주인 11/25 '본문은 가운데인데 제목은 왼쪽'): SE ONE 처럼 도구줄 가운데는 커서가 있는 칸에 — 마지막으로 누른 곳(도구줄 밖)이 제목 칸이면 제목 줄을 가운데로,
# 아니면 원래 단추 (지금 정렬 + 마지막 글 덩어리). window.__tm = {need: n} 이면 n 번째 누름부터 먹음 · {never: 1} 은 안 먹음 · ok 가 서면(주인 손) 먹음 — test_dash_autopub_v8
TITLE_MID = r"""<script>(() => { const T = document.querySelector('.se-documentTitle'), b = document.querySelector("[data-value='center']"), f = b.onclick; let w = '', n = 0;
document.addEventListener('pointerdown', e => { const t = e.target; if (t.closest && !t.closest('#tb')) w = t.closest('.se-documentTitle') ? 't' : t.closest('.se-content') ? 'b' : w; }, true);
b.onclick = e => { if (w !== 't') return f(e); const o = window.__tm || {}; window.__tmN = ++n; if (o.ok || (!o.never && n >= (o.need || 1))) T.querySelector('.se-text-paragraph').style.textAlign = 'center'; }; })();</script></body></html>"""


V4_SRC = Path(__file__).resolve().parent / "fixtures" / "autopub_v4.js"  # 주인 아이패드에서 '포스트잇 글이 안 들어감'으로 멈춘 v4 진행기 (fad77bf) 그대로
V3_SRC = Path(__file__).resolve().parent / "fixtures" / "autopub_v3.js"  # 주인 아이패드에서 실패한 v3 진행기 (0b36e28) 그대로 — 모형이 그 화면을 재현하는지


def _loader(src: Path | None = None) -> str:
    if src is None:
        return unquote(autopub.bookmarklet("https://dash.test/dashboard/api/autopub_act", "KEY")[len("javascript:"):])
    keep = autopub.SRC
    try:
        autopub.SRC = src
        for f in (autopub.runner, autopub._code, autopub.version):
            f.cache_clear()
        return unquote(autopub.bookmarklet("https://dash.test/dashboard/api/autopub_act", "KEY")[len("javascript:"):])
    finally:
        autopub.SRC = keep
        for f in (autopub.runner, autopub._code, autopub.version):
            f.cache_clear()


def _flow(posts: list[dict], *, rsv: str = "full", timeout: int = 300, se1: str = "", src: Path | None = None, **opt: Any) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    loader = _loader(src)
    out = subprocess.run([NODE, "-e", FLOW, _editor(rsv, se1), loader, json.dumps(posts, ensure_ascii=False), json.dumps({"mode": "xhr", **opt})],
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
    assert any("1/2 「첫 글 제목이에요」 지금 발행 — 확인하는 중" in x for x in got["bars"]) and any("2/2 「둘째 글 제목」 예약 " in x and "19:40 발행 — 확인하는 중" in x for x in got["bars"])
    assert any("다음 글 2/2 — 1초 쉬어요" in x for x in got["bars"])
    assert got["end"].startswith("하루치 한 번에 · 데런픽: 지금 올릴 준비된 글이 없어요 · 올린 글 2 · 할 일 체크 2") and got["end"].endswith(" · v11"), got["end"]
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
    assert "네이버 안내(자동입력 방지)로 멈췄어요 — 풀지 않아요. 데런픽 하루치 한 번에는 24시간 쉬어요" in got["end"] and "올린 글 0" in got["end"]
    assert got["lines"] == ["네이버 안내로 멈춤 · 「첫 글 제목이에요」 — 자동입력 방지"]
    # 글쓰기 화면을 열자마자 보호 안내 → 글을 넣지도 발행하지도 않음
    got = _flow(_posts(_tomorrow()), mode="prewall")
    assert got["pub"] == [] and [(d["id"], d["r"], d["n"]) for d in got["done"]] == [("pub:issue:11", "wall", "보호하고 있습니다")], got
    assert got["next"] == [""] and "안내(보호하고 있습니다)로 멈췄어요" in got["end"]


@needs_node
def test_another_blogs_write_page_is_refused() -> None:
    got = _flow(_posts(_tomorrow())[:1], blog="someoneelse")
    assert got["next"] == [] and got["done"] == [] and got["pub"] == [], got  # 대시보드에 묻지도 않음
    assert got["end"] == "하루치 한 번에 · 우리 블로그 계정의 글쓰기 화면이 아니에요 (someoneelse) — 아무것도 하지 않았어요"


def test_v7_has_no_weekend_button() -> None:
    # v7 (주인 11/24 '주말에도 글 쓰고 발행' · b8d8da8 서버는 weekend 를 안 보냄): 책갈피의 '주말에도 시작' 단추·w 신호를 뺌
    src = (STATIC / "autopub.js").read_text(encoding="utf-8")
    assert "주말에도 시작" not in src and "weekend" not in src and " w: wk" not in src


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


# v1 이 제목을 넣던 순서 (제목 줄 누름 → 꾸미기 setSel = 본문 편집 칸 포커스 + 제목에 선택 → 붙여넣기 신호) — SE ONE 같은 화면에서 주인 아이패드처럼 본문 첫 줄에 들어감
V1_TITLE = r"""
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.setContent(process.argv[1]);
  const out = await p.evaluate(() => {
    const t = document.querySelector('.se-documentTitle .se-text-paragraph'), h = document.querySelector('.se-content').closest('[contenteditable="true"]');
    for (const ty of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) t.dispatchEvent(new MouseEvent(ty, {bubbles: true, cancelable: true}));
    h.focus();
    const r = document.createRange(); r.selectNodeContents(t); r.collapse(false); getSelection().removeAllRanges(); getSelection().addRange(r);
    const dt = new DataTransfer(); dt.setData('text/plain', '첫 글 제목이에요');
    (t.closest('[contenteditable="true"]') || h).dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
    return {title: t.textContent, body: document.querySelector('.se-content').textContent, cur: window.__se1()};
  });
  console.log(JSON.stringify(out)); await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


@needs_node
def test_se_one_like_editor_reproduces_the_v1_title_in_body_bug() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", V1_TITLE, _editor(se1="focus")], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["cur"] == "body" and got["body"] == "첫 글 제목이에요" and got["title"] == "제목", got  # 제목 칸은 안내 글 '제목' 그대로


@needs_node
def test_v5_title_that_landed_in_the_body_waits_for_the_owner_to_delete_it_and_carries_on_in_the_same_window() -> None:
    # steal: 첫 붙여넣기는 무조건 본문으로 (주인 11/23 13:58 '제목이 본문 칸에 들어갔어요 — 이 글쓰기 화면은 닫고…') → v5 는 지우지 않고(스크립트로 지우면 에디터가 망가짐 — v3)
    # '본문에 들어간 제목 줄을 지운 뒤 제목 칸을' → 주인이 지우면 같은 화면에서 ① 다시 → 제목 → 본문 → 발행 (창을 닫지 않음)
    got = _flow(_posts(_tomorrow())[:1], se1="steal", unbody=True)
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and got["pub"][0]["title"] == "첫 글 제목이에요", got
    assert "첫 글 제목" not in got["pub"][0]["text"] and "(길게 눌러 선택 → 삭제) 제목 칸을 눌러 주세요" in got["unbody_bar"]
    assert any(x.endswith("제목 칸(위 큰 글씨 칸)을 눌러 주세요") for x in got["bars"]) and got["taps"] == 1
    # 주인이 지우지 않으면 기다리기만 (발행·'해 봄' 0 · 스크립트가 지우지 않음)
    got = _flow(_posts(_tomorrow())[:1], se1="steal", give_up=25, timeout=120)
    assert got["pub"] == [] and got["done"] == [] and "unbody_bar" in got, got


# v2 가 제목을 넣던 차례 (포커스 → 제목 줄 누름 → 붙여넣기 → 안 되면 글 넣기 명령) — 진짜 아이패드 같은 화면(ipad)에서 주인 11/23 11:36 화면처럼 제목은 빈 칸
V2_TITLE = r"""
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.setContent(process.argv[1]);
  const out = await p.evaluate(async () => {
    const t = document.querySelector('.se-documentTitle .se-text-paragraph'), h = t.closest('[contenteditable="true"]'), S = m => new Promise(r => setTimeout(r, m)), o = {};
    h.focus();
    for (const ty of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) t.dispatchEvent(new MouseEvent(ty, {bubbles: true, cancelable: true}));
    const r = document.createRange(); r.selectNodeContents(t); r.collapse(false); getSelection().removeAllRanges(); getSelection().addRange(r);
    const dt = new DataTransfer(); dt.setData('text/plain', '첫 글 제목이에요');
    h.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
    await S(800);
    o.body = document.querySelector('.se-content').textContent;
    const r2 = document.createRange(); r2.selectNodeContents(t); r2.collapse(false); getSelection().removeAllRanges(); getSelection().addRange(r2);
    document.execCommand('insertText', false, '첫 글 제목이에요');
    o.now = t.textContent;
    await S(1000);
    o.title = t.textContent; o.cur = window.__se1();
    return o;
  });
  console.log(JSON.stringify(out)); await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


@needs_node
def test_ipad_like_editor_reproduces_the_v2_failure() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", V2_TITLE, _editor(se1="ipad")], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # 붙여넣기는 본문으로 · 글 넣기 명령은 잠깐 제목 칸에 보였다가(v2 가 바로 읽으면 '됨') 에디터가 지움 → 제목은 안내 글 '제목' 그대로
    assert got["body"] == "첫 글 제목이에요" and "첫 글 제목이에요" in got["now"] and got["title"] == "제목" and got["cur"] == "body", got


# ── 실기기 모형 (real · SE1R) ─────────────────────────────────────────
@needs_node
def test_real_model_reproduces_the_owners_v3_screen_exactly() -> None:
    # 주인 11/23 12시 화면: v3 를 누르자 제목·본문 둘 다 빈 칸 + 배너의 일곱 가지 (사진 0/14 → 이 글은 0/1) · 발행 0
    got = _flow(_posts(_tomorrow())[:1], se1="real", src=V3_SRC)
    assert got["pub"] == [] and [d["r"] for d in got["done"]] == ["stop"], got
    stop = next(x for x in got["bars"] if "멈췄어요" in x)
    for want in ("글 안 들어감: 첫 글 첫 문단.", "태그 줄이 없어 발행창을 못 채웠어요", "제목 칸이 비었어요", "본문이 원고와 달라요 (첫 글 첫 문단.…)",
                 "사진 0/1 — 빠진 사진을 넣어 주세요", "글 차례가 원고와 달라요"):
        assert want in stop, (want, stop)
    assert got["cp"] == "제목 복사" and got["broken"] == 1  # v3 가 본문에 든 제목을 DOM 에서 지워 에디터가 그 뒤 붙여넣기를 안 받음


@needs_node
def test_real_model_kkumi_works_after_the_owners_body_tap_like_on_the_ipad() -> None:
    # 대조: 실기기에서 도는 꾸미기 — 주인이 본문을 한 번 누르고(진짜 누름) 책갈피 → 칸에 붙여넣기 → 모두 됐어요
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    run = KK_RUN.replace("await frame.waitForSelector('.se-content');", "await frame.waitForSelector('.se-content'); await frame.click('.se-content .se-text-paragraph');")
    out = subprocess.run([NODE, "-e", run, _editor(se1="real"), kkumi_code(), PASTED], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["alert"].startswith("모두 됐어요") and "링크 카드 1/1 · 구분선 2/2 · 포스트잇 1/1 · 태그 3/3" in got["alert"], got["alert"]
    assert got["comps"][0].startswith("TEXT [시험 2]") and "POSTIT" in " ".join(got["comps"])


@needs_node
def test_v4_on_the_real_model_two_owner_taps_per_post_then_publishes_by_itself() -> None:
    when = _tomorrow()
    got = _flow(_posts(when), se1="real")
    assert got["dialogs"] == [] and [d["r"] for d in got["done"]] == ["hold", "ok", "hold", "ok"], got
    a, b = got["pub"]
    assert a["title"] == "첫 글 제목이에요" and "첫 글 제목" not in a["text"] and a["text"].lstrip().startswith("첫 글 첫 문단.") and a["imgs"] == 1
    assert a["tags"].split() == ["#아일릿", "#키링"] and a["cat"] == "셀럽 소식" and "수수료를 받을 수" in a["text"]
    assert b["title"] == "둘째 글 제목" and "둘째 글 제목" not in b["text"] and b["book"] is True and (b["h"], b["m"]) == ("19", "40")
    assert got["hands"] == 4 and not got.get("broken") and "cp" not in got  # 글마다 제목 칸 한 번 + 본문 한 번 · 에디터 글을 스크립트로 고친 적 없음
    assert any("1/2 「첫 글 제목이에요」 ① 제목 칸을 한 번 눌러 주세요" == x[len("하루치 한 번에 · "):] for x in got["bars"])
    assert any("② 본문을 한 번 눌러 주세요" in x for x in got["bars"]) and got["end"].endswith(" · v11") and "올린 글 2" in got["end"]


@needs_node
def test_v4_on_the_real_model_tap_once_per_post_mode() -> None:
    got = _flow(_posts(_tomorrow()), se1="real", dir=True)
    assert [d["r"] for d in got["done"]] == ["hold", "ok", "hold", "ok"] and got["taps"] == 2 and got["hands"] == 4 and not got.get("broken"), got
    assert [x["title"] for x in got["pub"]] == ["첫 글 제목이에요", "둘째 글 제목"]


@needs_node
def test_v4_without_the_owners_taps_inserts_nothing_on_the_real_model() -> None:
    # 대조: 진행기가 누르라고 해도 주인이 안 누르면 모형은 아무것도 받지 않음 (기다림 — 시험은 몇 초 보고 끝냄) · 발행 0
    got = _flow(_posts(_tomorrow())[:1], se1="real", hands=False, timeout=120, give_up=40)
    assert got["pub"] == [] and [d["r"] for d in got["done"]] == [] and "hands" not in got, got
    assert got["bars"][-1].endswith("① 제목 칸을 한 번 눌러 주세요")


@needs_node
def test_v4_if_the_title_field_ignores_script_paste_the_owner_pastes_it_from_the_copy_button() -> None:
    # realnt: 제목 칸이 스크립트 붙여넣기를 안 받으면 「제목 복사」 → 주인이 제목 칸에 붙여넣기 → 그대로 이어서 본문 → 발행 (책갈피 다시 누를 필요 없음)
    got = _flow(_posts(_tomorrow())[:1], se1="realnt")
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and got["hands"] == 3 and not got.get("broken"), got
    assert got["pub"][0]["title"] == "첫 글 제목이에요" and got["pub"][0]["text"].lstrip().startswith("첫 글 첫 문단.")
    assert any("제목이 안 들어갔어요 — 「제목 복사」" in x for x in got["bars"])


@needs_node
def test_v4_owner_tapping_the_body_instead_of_the_title_pastes_nothing_and_keeps_waiting() -> None:
    # 제목 칸이 아닌 곳을 누르면 붙여넣지 않고 제목 칸 누름을 계속 기다림 (본문에 제목이 들어갈 일 없음) · 발행 0 · 에디터 글을 건드리지 않음
    got = _flow(_posts(_tomorrow())[:1], se1="real", wrongtap=True, give_up=30, timeout=120)
    assert got["pub"] == [] and got["done"] == [] and got["hands"] == 1 and not got.get("broken"), got
    assert got["bars"][-1].endswith("① 제목 칸을 한 번 눌러 주세요")


@needs_node
def test_real_model_also_reproduces_the_v1_v2_title_in_body_screens() -> None:
    # 하루치 v1·v2 (주인 11/23 '제목이 본문 칸에 들어갔어요'): 누른 적 없는 창에서 포커스 → 붙여넣기 → 본문 · 글 넣기 명령은 안 먹음 → 제목 칸은 빈 칸
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    for js in (V1_TITLE, V2_TITLE):
        out = subprocess.run([NODE, "-e", js, _editor(se1="real")], capture_output=True, text=True, timeout=120, env=env)
        assert out.returncode == 0, out.stderr
        got = json.loads(out.stdout)
        assert got["body"] == "첫 글 제목이에요" and got["title"] == "제목" and got["cur"] == "body", got


# ── v5: 포스트잇 (주인 11/23 13시 아이패드 v4 — '포스트잇 글이 안 들어감') ─────────────────────
PIT_A, PIT_B = "장면 하나 — 법정 앞", "1회 · 4.0%"


def _drama() -> list[dict]:
    """드라마 글 꼴: 글 · 선 · 소제목 포스트잇 · 글 · 표 포스트잇(앞에 선 없이 글 바로 다음) · 글 · 태그 줄 (지금 발행)."""
    return [{"id": "pub:finance:21", "title": "굿파트너2 2회 이야기", "at": "", "label": "지금", "photos": 0, "disc": False, "text": "",
             "html": f"<p>드라마 첫 문단.</p><p>\u2063</p><blockquote><p>{PIT_A}</p></blockquote><p>표 앞 문단이에요.</p>"
                     f"<blockquote><p>{PIT_B}</p><p>2회 · 5.1%</p></blockquote><p>드라마 끝 문단.</p>" + _tags_line(["굿파트너2"], "셀럽 소식")}]


def _flow_issue(posts: list[dict], **kw: Any) -> dict:
    return _flow([{**x, "id": x["id"].replace("finance", "issue")} for x in posts], **kw)


@needs_node
def test_real_postit_model_reproduces_the_owners_v4_stop_exactly() -> None:
    # 주인 화면: '1/4 「굿파트너2 2회, …」 멈췄어요 — 발행하지 않았어요. 아래를 고친 뒤 단추를 눌러 주세요: 포스트잇 글이 안 들어감' (발행창은 다 채움 · 발행 0)
    got = _flow_issue(_drama(), se1="realpi", src=V4_SRC)
    assert got["pub"] == [] and [d["r"] for d in got["done"]] == ["stop"] and not got.get("broken"), got
    stop = next(x for x in got["bars"] if "멈췄어요" in x)
    assert stop.endswith("「굿파트너2 2회 이야기」 멈췄어요 — 발행하지 않았어요. 아래를 고친 뒤 단추를 눌러 주세요: 포스트잇 글이 안 들어감"), stop
    assert got["done"][0]["n"] == "포스트잇 글이 안 들어감" and got["hands"] == 2


@needs_node
def test_real_postit_model_kkumi_still_does_both_postits() -> None:
    # 대조: 같은 모형에서 꾸미기 책갈피 (주인이 본문을 누른 뒤 → 붙여넣기 칸) — 소제목·표 포스트잇 모두 안에
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    # 실기기 차례 그대로: 본문 누름 → 책갈피 → 붙여넣기 칸을 길게 누름(진짜 누름 — 손이 편집 칸 밖으로) → 붙여넣기
    run = KK_RUN.replace("await frame.waitForSelector('.se-content');", "await frame.waitForSelector('.se-content'); await frame.click('.se-content .se-text-paragraph');").replace(
        "await frame.waitForSelector('[aria-label=\"여기에 붙여넣기\"]');", "await frame.click('[aria-label=\"여기에 붙여넣기\"]');")
    assert run.count("await frame.click(") == 2
    html = _drama()[0]["html"]
    out = subprocess.run([NODE, "-e", run, _editor(se1="realpi"), kkumi_code(), html], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["alert"].startswith("모두 됐어요") and "포스트잇 2/2" in got["alert"] and "글 안 → 나옴 → 글 안 → 나옴" in got["alert"], got["alert"]
    pits = [c for c in got["comps"] if c.startswith("POSTIT")]
    assert len(pits) == 2 and PIT_A[:10] in pits[0] and "1회" in pits[1], got["comps"]


@needs_node
def test_v5_on_the_real_postit_model_puts_both_postits_inside_and_publishes() -> None:
    got = _flow_issue(_drama(), se1="realpi")
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and got["hands"] == 2 and not got.get("broken") and "cp" not in got, got
    a = got["pub"][0]
    assert a["title"] == "굿파트너2 2회 이야기" and a["tags"].split() == ["#굿파트너2"] and a["cat"] == "셀럽 소식"
    assert len(a["postits"]) == 2 and PIT_A in a["postits"][0] and "1회 · 4.0%" in a["postits"][1].replace("\xa0", " ") and "2회 · 5.1%" in a["postits"][1]
    assert "1회" not in a["outside"] and PIT_A not in a["outside"] and "표 앞 문단이에요." in a["outside"]  # 포스트잇 밖(본문)에는 없음
    assert got["end"].endswith(" · v11") and "올린 글 1" in got["end"]


@needs_node
def test_v5_postit_that_never_takes_text_names_it_offers_copy_and_publishes_after_owner_paste() -> None:
    # realpint: 포스트잇이 스크립트 붙여넣기를 끝내 안 받음 → 발행하지 않고 어느 포스트잇·어떤 글인지 + 「포스트잇 글 복사」 →
    # 주인이 빈 포스트잇 안을 눌러 붙여넣고 초록 단추 → 화면을 다시 읽어 두 포스트잇 다 들어간 것을 보고 발행
    got = _flow_issue(_drama(), se1="realpint", fix="postit", pit=[PIT_A, PIT_B + "\n2회 · 5.1%"], timeout=420)
    stop = got["stop_bar"]
    assert "발행하지 않았어요" in stop and "포스트잇 글이 안 들어감" not in stop, stop
    assert "포스트잇 1 「장면 하나 — 법정 앞」 글이 안 들어갔어요 — 「포스트잇 글 복사」 → 빈 포스트잇 안을 눌러 붙여넣기" in stop
    assert "포스트잇 2 「1회 · 4.0%」 글이 안 들어갔어요" in stop and got["cp_at_stop"] == "포스트잇 글 복사"
    assert [(d["r"], d["pub"]) for d in got["done"]] == [("stop", 0), ("hold", 0), ("ok", 1)], got["done"]
    a = got["pub"][0]
    assert len(a["postits"]) == 2 and PIT_A in a["postits"][0] and "2회 · 5.1%" in a["postits"][1] and "1회" not in a["outside"]


@needs_node
def test_v5_never_publishes_while_a_postit_is_still_empty_after_the_green_button() -> None:
    # 주인이 고치지 않고 초록 단추만 → 다시 읽어도 포스트잇이 비어 있어 또 멈춤 (발행 0 · v4 는 다시 점검에서 포스트잇 글을 안 봤음)
    got = _flow_issue(_drama(), se1="realpint", fix="nothing", timeout=420)
    assert got["pub"] == [] and [d["r"] for d in got["done"]] == ["stop", "stop"], got


@needs_node
def test_v5_first_body_chunk_that_does_not_land_asks_for_one_more_body_tap_and_carries_on() -> None:
    # 주인 11/23 13:40 v4 3/4: 제목은 들어갔는데 본문은 빈 칸('글 안 들어감: …' 등 배너 여섯) → v5 는 본문이 비었으니 지우지 않고 '본문을 한 번 더' → 이어서 발행
    got = _flow_issue(_drama(), se1="realpidrop")
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and got["again"] == 1 and got["hands"] == 3 and not got.get("broken"), got
    assert any("본문에 글이 안 들어갔어요 — 본문을 한 번 더 눌러 주세요 (아무것도 지우지 않았어요)" in x for x in got["bars"])
    a = got["pub"][0]
    assert a["text"].lstrip().startswith("드라마 첫 문단.") and a["text"].count("드라마 첫 문단.") == 1 and len(a["postits"]) == 2 and "1회" not in a["outside"]
    # 대조: v4 는 같은 화면에서 첫 조각이 안 들어가자 그 글을 멈춤 (주인 13:40 배너 그대로 — 글 안 들어감 · 태그 줄이 없어 · 본문이 원고와 달라요 · 글 차례) · 발행 0
    got = _flow_issue(_drama(), se1="realpidrop", src=V4_SRC)
    assert got["pub"] == [] and [d["r"] for d in got["done"]] == ["stop"], got
    for want in ("글 안 들어감: 드라마 첫 문단.", "태그 줄이 없어 발행창을 못 채웠어요", "본문이 원고와 달라요 (드라마 첫 문단.…)", "글 차례가 원고와 달라요"):
        assert want in got["done"][0]["n"], (want, got["done"][0]["n"])


def test_retapping_after_a_stopped_post_resumes_the_unpublished_ones_and_never_the_published_one(bot: DealBot, tmp_path: Path) -> None:
    # 주인 11/23 13:40: 2/4 올림(ok) · 3/4 못 넣음(fail) → 새 글쓰기 화면에서 책갈피를 다시 누르면 3/4 부터 (2/4 는 다시 주지 않음)
    with_routine(tmp_path, _rows(DAY))
    t = at(bot, DAY, "10:00")
    first = _go(bot, t, a="next", b="issue")["next"]["id"]
    _go(bot, t + 1, a="done", b="issue", id=first, r="hold")
    _go(bot, t + 2, a="done", b="issue", id=first, r="ok")
    second = _go(bot, t + 200, a="next", b="issue")["next"]["id"]
    assert second != first
    _go(bot, t + 201, a="done", b="issue", id=second, r="fail", n="글 안 들어감: 첫 문단")
    for k in range(2):  # 몇 번을 다시 눌러도 못 넣은 글부터 · 올린 글은 안 줌
        q = _go(bot, t + 400 + k * 300, a="next", b="issue")
        assert q["next"]["id"] == second and first not in [q["next"]["id"]]
    with pytest.raises(autopub.CaptureError):  # 올린 글을 또 발행하려는 '해 봄'은 막음
        _go(bot, t + 1000, a="done", b="issue", id=first, r="hold")


@needs_node
def test_v5_owner_tapping_the_body_first_at_step_one_then_the_title_still_goes_on_in_the_same_window() -> None:
    # 주인 11/23 13:58: ① 에서 본문을 먼저 누름 → 붙이지 않고 그대로 기다림 → 제목 칸을 누르면 그 자리에 제목 → 본문 → 발행 (창을 닫지 않음 · 에디터 글을 건드리지 않음)
    got = _flow_issue(_drama(), se1="realpi", wrongtap="once")
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got.get("broken") and got["taps"] == 1, got
    a = got["pub"][0]
    assert a["title"] == "굿파트너2 2회 이야기" and "굿파트너2 2회 이야기" not in a["text"] and len(a["postits"]) == 2


@needs_node
def test_v5_reserve_fields_that_render_late_still_get_the_next_day_date() -> None:
    # 주인 11/23 14:00 '예약 11/24 08:50 못 채움 (날짜 칸 못 찾음)' (발행창은 '현재'): '예약'을 고른 뒤 날짜·시·분 칸이 1.2초 · 달력이 0.9초 늦게 떠도 채움 (꾸미기 v9 차례 그대로)
    when = _tomorrow()
    got = _flow(_posts(when)[1:], rsv="late")
    assert [d["r"] for d in got["done"]] == ["hold", "ok"], got
    b = got["pub"][0]
    assert b["book"] is True and (b["h"], b["m"]) == ("19", "40") and b["date"] == f"{when.year}. {when.month:02d}. {when.day:02d}"
    # 그래도 날짜를 못 채우면 (달력이 안 뜸) 발행하지 않고 직접 고르라고 함
    got = _flow(_posts(when)[1:], rsv="nocal")
    st = [d for d in got["done"] if d["r"]]  # v9: 날짜를 못 고르면 달력 모양(a=dg · r 없음)도 한 번 옴
    assert got["pub"] == [] and [d["r"] for d in st] == ["stop"] and "그 날짜를 고른 뒤 초록 단추" in st[0]["n"], got
    assert [d["n"][:17] for d in got["done"] if not d["r"]] == [when.strftime("@%Y-%m-%dT%H:%M")], got


def _unz(s: str) -> str:
    """autopub.js Z 와 같은 풀기 (시험용)."""
    import re as _re

    def f(m: Any) -> str:
        a, b, c, d = m.groups()
        if a:
            return chr(44032 + (ord(a) - 65) * 1296 + int(b, 36))
        return chr(int(c or d, 16)) if (c or d) else " "
    return _re.sub(r"([A-I])(..)|J(....)|\.(..)|_", f, s)


def test_kkumi_core_korean_literals_are_packed_and_unpack_to_the_same_code() -> None:
    # v5: 꾸미기 코드의 한글 글을 Z('…') 로 줄임 — 풀면 줄이기 전 코드와 한 글자도 같고, 선택자 속 '…'] 는 건드리지 않음
    import re as _re

    raw = kkumi.minify(autopub._core())
    z = autopub._zcore(raw)
    assert len(kkumi.encode_short(z)) < len(kkumi.encode_short(raw)) - 1000 and z.count("Z('") > 40
    assert "returnZ(" not in z and "return Z('" in z and not _re.search(r"[\w$]Z\('", z)  # 이름에 붙지 않음
    back = _re.sub(r"(return )?Z\('([^']*)'\)", lambda m: ("return" if m.group(1) else "") + "'" + _unz(m.group(2)) + "'", z)
    assert back == raw
    assert "input[placeholder*='링크']" in z and "button[aria-label*='카테고리']" in z


def test_run_cap_counts_only_confirmed_publishes(bot: DealBot, tmp_path: Path) -> None:
    # 주인 11/23 14:35 라커룸 '한 번에 5편까지만 올려요 (남은 글 3) · 올린 글 0' — 실제로 올라간 건 3편: 멈춘·못 넣은·확인 못 한 글은 상한에 안 셈
    rows = {"day": DAY, "slots": SLOTS, "overflow": 0, "items": [
        item(f"pub:issue:{n}", "s1", group="발행", blog="issue", title=f"데런픽 발행 · {n}", paste=_paste(f"글 {n}"), gate=OK) for n in range(1, 10)]}
    with_routine(tmp_path, rows)
    t = at(bot, DAY, "10:00")
    for k, r in enumerate(("ok", "stop", "fail", "ok", "unsure", "stop", "ok")):
        q = _go(bot, t + k * 200, a="next", b="issue")
        assert q["next"] is not None, (k, q)
        if r != "stop" or k == 1:
            _go(bot, t + k * 200 + 1, a="done", b="issue", id=q["next"]["id"], r="hold" if r in ("ok", "unsure") else r)
        if r in ("ok", "unsure"):
            _go(bot, t + k * 200 + 2, a="done", b="issue", id=q["next"]["id"], r=r)
        elif r == "stop" and k != 1:
            _go(bot, t + k * 200 + 1, a="done", b="issue", id=q["next"]["id"], r="stop")
    c = json.loads(bot.db.kv_get(autopub.DAY_KEY + DAY))
    assert c["run"]["issue"]["n"] == 3  # 확인된 발행 3편만
    q = _go(bot, t + 1600, a="next", b="issue")
    assert q["next"] is not None and q["n"] == 4 and "5편까지만" not in q["text"]
