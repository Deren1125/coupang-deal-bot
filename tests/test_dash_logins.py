"""대시보드 '로그인 관리' (주인 10/26 — "지금 있는 모든 로그인이 필요한 사이트들은 다 원격으로 탭 열어서 로그인 기능 넣어 주라.
대시보드에. 너가 알림 주면 대시보드에서 바로 처리할 수 있게").

1) 기록 파일 status.logins (Blog-Auto remote_login.states) 를 엄격히 거름 · 할 일 줄의 login 칸 · login_keep 키.
2) 원격 로그인 대상에 인스타 검색 계정 (instagram) — 키 → 대상 표는 Blog-Auto 와 같음 · 알림·할 일 줄마다 첫 단추가 원격 로그인.
3) 딜봇 네이버 QR 로그인 (login.qr): 텔레그램 /naverlogin 과 같은 길 · QR 화면은 대시보드 로그인 아래 2분 동안만 · 두 번 눌러도 한 번 ·
   QR 화면은 로그인을 기다리는 동안 열어 둠 (닫으면 앱에서 승인한 로그인을 못 받음).
4) GET /dashboard/api/logins (연결 탭 칸 — 첫 상태 값 밖) · logins.js (셸이 defer · 크기 예산은 test_dash_shell · 바깥 주소·innerHTML 없음).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from dash_helpers import (
    blogauto_status,
    fixture,
    get_json,
    post_json,
    push_settings,
    session_headers,
)
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import logins, remote_login
from dealbot.dashboard.blogauto import _logins, parse_export
from dealbot.notify import iid as iidmod
from test_dash_state import write_export

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
PNG = b"\x89PNG\r\n\x1a\n" + b"qr" * 40


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


class FakeNaver:
    """딜봇 네이버 쿠키·QR 화면 흉내 (사이트를 열지 않음)."""

    def __init__(self, logged: bool = False) -> None:
        self.logged = logged
        self.opened = 0
        self.release = asyncio.Event()
        self.on_state = None

    async def is_logged_in(self) -> bool:
        return self.logged

    async def start_qr_login(self) -> bytes:
        self.opened += 1
        return PNG

    async def wait_login(self, timeout_seconds: int = 120, poll_seconds: float = 3.0) -> bool:
        await self.release.wait()
        return self.logged


def with_naver(bot: DealBot, fake: FakeNaver) -> FakeNaver:
    bot.settings.browser.enabled = True
    bot.naver_connect = fake
    return fake


async def act(bot: DealBot, action: str, args: dict) -> dict:
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": action, "args": args}, session_headers(bot))
    assert status == 200, body
    return body


def rows_of(view: dict) -> dict[str, dict]:
    return {r["id"]: r for r in view["rows"]}


# ---------------------------------------------------------------- 1) 기록 파일
def test_fixture_logins_parse_with_targets_and_labels_matching_the_remote_targets() -> None:
    e = parse_export(fixture("export_v2.json"), "Asia/Seoul")
    got = e.v2["status"]["logins"]
    assert [r["id"] for r in got] == ["finance", "issue", "hotdeal", "toss", "ably", "zigzag", "instagram", "threads_api"]
    assert {r["id"]: r["label"] for r in got if r["target"]} == remote_login.TARGETS  # 이름이 두 저장소에서 같다
    assert all(r["target"] == r["id"] for r in got if r["target"])
    th = got[-1]
    assert th["target"] is None and th["where"] and th["state"] == "ok"
    ably = next(r for r in got if r["id"] == "ably")
    assert ably["state"] == "resting" and ably["until"] == "2026-10-19T07:30:00+09:00" and ably["checked_at"].endswith("+09:00")


def test_logins_are_filtered_row_by_row() -> None:
    base = {"id": "issue", "label": "Pick 블로그", "state": "needed", "note": "로그인 풀림", "checked_at": "2026-10-18T08:40:00+09:00",
            "since": None, "until": None, "target": "issue", "where": ""}
    assert _logins(None) is None and _logins({"rows": []}) is None  # 예전 Blog-Auto — 칸 없음
    got = _logins([
        base,
        dict(base, label="두 번째 같은 id"),  # 같은 id 는 첫 줄만
        dict(base, id="Bad-ID"), dict(base, id="x" * 30), "줄 아님", dict(base, id="zz", label=""),  # 이상한 줄만 빠짐
        dict(base, id="toss", label="토스", state="weird", target="rm -rf", checked_at="2026-10-18T08:40:00",  # 시간대 없음
             since="어제", until=12345, note="가" * 200, where=["x"]),
        dict(base, id="ably", label="에이블리", target=None, until="2026-13-40T99:00:00+09:00"),
    ])
    assert [r["id"] for r in got] == ["issue", "toss", "ably"]
    t = got[1]
    assert t["state"] == "unknown" and t["target"] is None and t["checked_at"] is None and t["since"] is None and t["until"] is None
    assert len(t["note"]) <= 80 and t["note"].endswith("…") and t["where"] == ""
    assert got[2]["target"] is None and got[2]["until"] is None
    assert len(_logins([dict(base, id=f"r{i}") for i in range(30)])) == 12


def test_todo_rows_keep_only_known_login_targets() -> None:
    data = fixture("export_v2.json")
    item = {"key": "own_stats_block:hotdeal", "marker": "⚠️[할 일]", "blog": "hotdeal", "kind": "own_stats_blocked", "short": "핫딜 로그인 확인",
            "text": "⚠️[할 일] 핫딜 내 통계를 읽다가 네이버 로그인 화면이 나와 멈췄어요\n→ 네이버 로그인 확인 — 대시보드 할 일에서 원격 로그인",
            "since": "2026-10-18T09:00:00+09:00", "urgent": False, "link": "https://m.blog.naver.com/demiyum", "group": "", "count": 1}
    data["todo"]["items"] = [dict(item, login="hotdeal"), dict(item, key="mall_login:x", login="nope"), dict(item, key="x:1", login=3)]
    items = parse_export(data, "Asia/Seoul").todo.items
    assert [t.login for t in items] == ["hotdeal", None, None]


def test_login_keys_map_to_the_same_rows_and_targets_as_blog_auto() -> None:
    assert iidmod.iid_for("login_keep:issue", "", src="blogauto") == "conn-naver-issue"
    assert iidmod.iid_for("mall_login:instagram", "", src="blogauto") == "conn-mall-instagram"
    table = {"login:issue": "issue", "login_down:finance": "finance", "login_keep:hotdeal": "hotdeal", "mall_login:toss": "toss",
             "mall_login:ably_cf": "ably", "mall_login:zigzag": "zigzag", "mall_login:instagram": "instagram",
             "own_stats_block:issue": None, "kz:issue": None, "ownstats:issue:x": None, "mall_login:../x": None,
             "mall_login:finance": None, "login:claude_cli": None, "login:issue:x": None, "": None}
    assert {k: remote_login.target_of(k) for k in table} == table
    assert remote_login.TARGETS["instagram"] == "인스타 검색 계정" and list(remote_login.TARGETS)[-1] == "instagram"


# ---------------------------------------------------------------- 2) 할 일 줄 — 첫 단추가 원격 로그인
def test_login_notices_get_the_remote_login_button_first(bot: DealBot) -> None:
    t0 = time.time()
    for key, title in (("login:issue", "Pick 네이버 로그인 필요"), ("kz:hotdeal", "핫딜 키자드 확인"), ("login_keep:finance", "로그인 상태 유지"),
                       ("mall_login:instagram", "인스타 검색 계정 로그인")):
        bot.router.open({"src": "blogauto", "key": key, "kind": key.split(":")[0], "iid": iidmod.iid_for(key, "", src="blogauto"),  # type: ignore[union-attr]
                         "title": title, "at": t0})
    bot.router.open({"src": "dealbot", "key": "autolink:login", "kind": "autolink_stop", "iid": "sys-dealbot",  # type: ignore[union-attr]
                     "title": "쇼핑커넥트 자동 링크 멈춤", "at": t0})
    rows = {it["iid"]: it for it in bot.dash_state.items()}
    issue = rows["conn-naver-issue"]
    assert issue["primary"]["action"] == "login.remote" and issue["primary"]["args"] == {"target": "issue"}
    assert any(a["action"] == "blog.login_check" for a in issue["more"])
    assert rows["conn-naver-finance"]["primary"]["args"] == {"target": "finance"}  # login_keep 도 같은 줄
    kz = rows["conn-naver-hotdeal"]  # 키자드는 원격 화면으로 풀 수 없어 그대로 (확인이 먼저)
    assert kz["primary"]["action"] == "blog.login_check" and any(a["action"] == "login.remote" for a in kz["more"])
    ig = rows["conn-mall-instagram"]
    assert ig["primary"]["action"] == "login.remote" and ig["primary"]["args"] == {"target": "instagram"}
    sysrow = rows["sys-dealbot"]
    assert sysrow["primary"]["action"] == "login.remote" and sysrow["primary"]["args"] == {"target": "finance"}
    assert any(a.get("goto") == "#/i/sys-dealbot" for a in sysrow["more"])


def test_export_todo_login_rows_open_the_remote_screen(settings: Settings, tmp_path: Path) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    base = {"marker": "⚠️[할 일]", "urgent": False, "group": "", "count": 1, "since": now}
    data = {"generated_at": now, "items": [
        dict(base, key="own_stats_block:hotdeal", blog="hotdeal", kind="own_stats_blocked", short="핫딜 네이버 로그인 확인",
             text="⚠️[할 일] 핫딜 내 통계를 읽다가 네이버 로그인 화면이 나와 멈췄어요\n→ 대시보드 할 일에서 원격 로그인",
             link="https://m.blog.naver.com/demiyum", login="hotdeal"),
        dict(base, key="mall_login:instagram", blog="", kind="mall_login", short="인스타 검색 계정 로그인",
             text="⚠️[할 일] 인스타 검색 계정 로그인이 풀렸어요\n→ 대시보드 할 일에서 원격 로그인", link=""),
        dict(base, key="own_stats_block:issue", blog="issue", kind="own_stats_blocked", short="Pick 보안 확인",
             text="⚠️[할 일] Pick 내 통계 보안 확인", link="https://m.blog.naver.com/derenpick")]}  # 보안 확인(login 칸 없음)은 그대로
    write_export(tmp_path / "blogauto.json", todo=data)
    blogauto_status(settings)
    b = DealBot(push_settings(settings))
    try:
        rows = {it["iid"]: it for it in b.dash_state.items()}
        hd = rows["conn-naver-hotdeal"]
        assert hd["primary"]["action"] == "login.remote" and hd["primary"]["args"] == {"target": "hotdeal"}
        assert any(a.get("url") == "https://m.blog.naver.com/demiyum" for a in hd["more"])  # 네이버에서 보기는 더보기로
        assert rows["conn-mall-instagram"]["primary"]["args"] == {"target": "instagram"}
        assert rows["conn-naver-issue"]["primary"]["input"] == "open"
    finally:
        b.db.close()


async def test_remote_login_for_instagram_relays_and_the_same_screen_is_not_opened_twice(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    assert (await act(bot, "login.remote", {"target": "instagram"}))["code"] == "done"
    again = await act(bot, "login.remote", {"target": "instagram"})
    assert again["code"] == "done" and "이미" in again["text"]
    inbox = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    sent = [u["login_remote"] for u in inbox if "login_remote" in u]
    assert len(sent) == 1 and sent[0]["target"] == "instagram"  # 로그인 화면을 거듭 띄우지 않음 (주인 10/26)
    row = next(i for i in bot.dash_state.items() if i["iid"] == "sys-vnc")
    assert row["title"] == "원격 로그인 화면 — 인스타 검색 계정"


# ---------------------------------------------------------------- 3) 딜봇 네이버 QR
async def test_qr_login_is_off_when_the_deal_bot_browser_is_off(bot: DealBot) -> None:
    got = await act(bot, "login.qr", {})
    assert got["status"] == "error" and got["code"] == "off" and "꺼져" in got["text"]
    v = logins.view(bot)
    assert rows_of(v)["dealbot_naver"]["state"] == "off" and rows_of(v)["dealbot_naver"]["button"] is None
    assert not [i for i in bot.dash_state.items() if i["iid"] in ("sys-naverqr", "conn-dealbot-naver")]


async def test_qr_login_shows_the_qr_once_and_clears_after_login(bot: DealBot, settings: Settings) -> None:
    fake = with_naver(bot, FakeNaver())
    h = session_headers(bot)
    got = await act(bot, "login.qr", {})
    assert got["status"] == "ok" and "QR" in got["text"] and fake.opened == 1
    again = await act(bot, "login.qr", {})  # 떠 있는 동안 다시 눌러도 새로 열지 않음
    assert again["status"] == "ok" and "이미" in again["text"] and fake.opened == 1
    status, body, headers = await get_json(bot, "/dashboard/api/naver_qr.png", h)
    assert status == 200 and body == PNG and headers["Cache-Control"] == "no-store"
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/naver_qr.png", anon))[0] == 401  # 대시보드 로그인 아래
    status, view, headers = await get_json(bot, "/dashboard/api/logins", h)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert view["qr"]["img"].startswith("/dashboard/api/naver_qr.png?t=") and 0 < view["qr"]["left_s"] <= logins.QR_S
    nv = rows_of(view)["dealbot_naver"]
    assert nv["state"] == "needed" and "QR" in nv["text"] and nv["button"] is None
    qrow = next(i for i in bot.dash_state.items() if i["iid"] == "sys-naverqr")
    assert qrow["primary"]["input"] == "open" and qrow["primary"]["url"] == view["qr"]["img"]
    fake.logged = True
    fake.release.set()
    await asyncio.gather(*list(logins._TASKS))
    assert (await get_json(bot, "/dashboard/api/naver_qr.png", h))[0] == 404  # 끝나면 QR 화면은 지움
    assert not logins.qr_path(bot).exists()
    v = logins.view(bot)
    assert v["qr"] is None and rows_of(v)["dealbot_naver"]["state"] == "ok"
    assert not [i for i in bot.dash_state.items() if i["iid"] in ("sys-naverqr", "conn-dealbot-naver")]
    busy = await act(bot, "login.qr", {})  # 방금 끝났으면 바로 다시 열지 않음
    assert busy["code"] == "busy" and fake.opened == 1


async def test_qr_login_when_already_logged_in_opens_nothing(bot: DealBot) -> None:
    fake = with_naver(bot, FakeNaver(logged=True))
    got = await act(bot, "login.qr", {})
    assert got["status"] == "ok" and "이미" in got["text"] and fake.opened == 0
    assert logins.naver_state(bot.db)["ok"] is True and logins.qr_png(bot) is None


def test_deal_bot_naver_needs_login_row_has_the_qr_button(bot: DealBot) -> None:
    with_naver(bot, FakeNaver())
    logins.note_naver(bot.db, False)
    row = next(i for i in bot.dash_state.items() if i["iid"] == "conn-dealbot-naver")
    assert row["primary"]["action"] == "login.qr" and row["sev"] == "stop"
    nv = rows_of(logins.view(bot))["dealbot_naver"]
    assert nv["state"] == "needed" and nv["bad"] and nv["button"]["action"] == "login.qr"


def test_naver_state_is_noted_only_when_it_changes_or_every_ten_minutes(bot: DealBot) -> None:
    logins.note_naver(bot.db, True, now=1000.0)
    logins.note_naver(bot.db, True, now=1100.0)
    assert logins.naver_state(bot.db)["at"] == 1000.0
    logins.note_naver(bot.db, False, now=1101.0)
    assert logins.naver_state(bot.db) == {"ok": False, "at": 1101.0}
    logins.note_naver(bot.db, False, now=1101.0 + logins.NAVER_NOTE_S)
    assert logins.naver_state(bot.db)["at"] == 1101.0 + logins.NAVER_NOTE_S


def test_app_wires_the_naver_state_hook_without_opening_anything(settings: Settings, tmp_path: Path) -> None:
    settings.browser.enabled = True
    b = DealBot(push_settings(settings))
    try:
        assert b.naver_connect is not None and b.browser is not None and not b.browser.started  # 크롬은 아직 안 띄움
        b.naver_connect.on_state(False)
        assert logins.naver_state(b.db)["ok"] is False
    finally:
        b.db.close()


class _Loc:
    first = None

    def __init__(self) -> None:
        self.first = self

    async def click(self, timeout: int = 0) -> None:
        return None


class _Page:
    def __init__(self) -> None:
        self.closed = False
        self.urls: list[str] = []

    async def goto(self, url: str, **kw: object) -> None:
        self.urls.append(url)

    def locator(self, sel: str) -> _Loc:
        return _Loc()

    async def wait_for_timeout(self, ms: int) -> None:
        return None

    async def screenshot(self, **kw: object) -> bytes:
        return PNG

    async def close(self) -> None:
        self.closed = True


class _Session:
    gate_acct = "dealbot-test"

    def __init__(self) -> None:
        self.pages: list[_Page] = []
        self.cookie = False

    async def run_keep(self, fn, *a, **kw):  # type: ignore[no-untyped-def]
        page = _Page()
        self.pages.append(page)
        return await fn(page, *a, **kw), page

    async def cookies(self, name: str | None = None, domain_contains: str | None = None) -> list[dict]:
        return [{"name": name}] if self.cookie else []


async def test_qr_page_stays_open_until_the_login_wait_ends(tmp_path: Path) -> None:
    """예전엔 QR 을 찍은 뒤 바로 화면을 닫아 앱에서 승인한 로그인을 받을 화면이 없었다 — 기다리는 동안 열어 두고 끝나면 닫는다."""
    from dealbot import site_gate
    from dealbot.browser.naver_connect import NaverConnectProvider
    from dealbot.config import NaverConnectConfig

    site_gate.configure(tmp_path / "site_gate.db")
    try:
        sess = _Session()
        prov = NaverConnectProvider(sess, NaverConnectConfig())  # type: ignore[arg-type]
        seen: list[bool] = []
        prov.on_state = seen.append
        assert await prov.start_qr_login() == PNG
        page = sess.pages[0]
        assert not page.closed and page.urls == [NaverConnectConfig().login_url]
        assert not await prov.wait_login(timeout_seconds=0, poll_seconds=0.01)
        assert page.closed and seen == [False]  # 시간이 지나도 닫음
        await prov.start_qr_login()
        sess.cookie = True
        assert await prov.wait_login(timeout_seconds=1, poll_seconds=0.01)
        assert sess.pages[1].closed and seen[-1] is True
    finally:
        site_gate.configure(None)


async def test_an_older_qr_wait_does_not_close_a_newer_qr_page(tmp_path: Path) -> None:
    """텔레그램 /naverlogin 을 연 뒤 대시보드 QR 로그인을 누르면 새 화면이 옛 화면을 닫고 이어받는다 — 옛 기다림이 끝나면서
    새 화면까지 닫으면 주인이 찍은 QR 을 받을 화면이 없다."""
    from dealbot import site_gate
    from dealbot.browser.naver_connect import NaverConnectProvider
    from dealbot.config import NaverConnectConfig

    site_gate.configure(tmp_path / "site_gate.db")
    try:
        sess = _Session()
        prov = NaverConnectProvider(sess, NaverConnectConfig())  # type: ignore[arg-type]
        await prov.start_qr_login()
        first = asyncio.create_task(prov.wait_login(timeout_seconds=0.2, poll_seconds=0.01))
        await asyncio.sleep(0.05)
        await prov.start_qr_login()  # 다른 쪽에서 새 QR
        assert sess.pages[0].closed and not sess.pages[1].closed
        assert not await first
        assert not sess.pages[1].closed  # 옛 기다림은 새 화면을 두고 끝남
        assert not await prov.wait_login(timeout_seconds=0, poll_seconds=0.01)
        assert sess.pages[1].closed
    finally:
        site_gate.configure(None)

# ---------------------------------------------------------------- 4) 칸 · 화면 파일
async def test_logins_view_lists_every_login_with_buttons_only_for_remote_rows(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    status, view, _ = await get_json(bot, "/dashboard/api/logins", h)
    assert status == 200
    assert [r["id"] for r in view["rows"]] == ["finance", "issue", "hotdeal", "toss", "ably", "zigzag", "instagram", "dealbot_naver",
                                               "threads_api", "threads_auto", "telegram"]  # 딜봇 API 연결 · 카톡은 연결 탭 제 칸에만
    r = rows_of(view)
    for t in remote_login.TARGETS:
        assert r[t]["button"] == {"action": "login.remote", "args": {"target": t}, "label": "원격 로그인"}, t
    assert r["issue"]["bad"] and r["issue"]["text"].startswith("로그인 필요") and "확인 10/18 08:40" in r["issue"]["text"]
    assert r["ably"]["state"] == "resting" and "쉬는 중" in r["ably"]["text"] and "10/19 07:30까지" in r["ably"]["text"]
    for ro in ("threads_api", "threads_auto", "telegram"):
        assert r[ro]["button"] is None and r[ro]["where"], ro
    assert "스레드·X 봇" in r["threads_auto"]["where"] and r["threads_auto"]["state"] is None
    assert view["need"] == 3 and view["qr"] is None and view["vnc"] is None  # Pick · 지그재그 · 인스타 검색 계정
    for row in view["rows"]:
        assert "http" not in json.dumps(row.get("button") or {}), row  # 바깥 주소 단추 없음
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/logins", anon))[0] == 401
    body = (await get_json(bot, "/dashboard/api/state", h))[1]
    assert "logins" not in body and "dealbot_naver" not in json.dumps(body, ensure_ascii=False)  # 첫 상태 값 예산 밖


def test_logins_view_without_the_new_field_still_shows_every_remote_target(bot: DealBot, tmp_path: Path) -> None:
    data = fixture("export_v2.json")
    del data["status"]["logins"]
    write_export(tmp_path / "blogauto.json", status=data["status"])
    r = rows_of(logins.view(bot))
    for t in remote_login.TARGETS:
        assert r[t]["state"] == "unknown" and r[t]["button"]["action"] == "login.remote", t
    assert "threads_api" not in r


def test_stale_blog_auto_record_shows_logins_as_unknown_but_keeps_the_buttons(bot: DealBot, tmp_path: Path) -> None:
    """Blog-Auto 가 멈춰 기록 파일이 오래되면 그때의 '로그인됨' 을 지금 상태처럼 보이지 않는다 — '모름 · N시간 전 것' (단추는 그대로)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    write_export(tmp_path / "blogauto.json")
    data = json.loads((tmp_path / "blogauto.json").read_text(encoding="utf-8"))
    data["written_at"] = (datetime.now(ZoneInfo("Asia/Seoul")) - timedelta(hours=9)).isoformat(timespec="seconds")
    (tmp_path / "blogauto.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    export = bot.dash_state._export()
    assert export is not None and export.status != "ok" and export.v2.get("status", {}).get("logins")
    r = rows_of(logins.view(bot))
    for t in remote_login.TARGETS:
        assert r[t]["state"] == "unknown" and "9시간 전 것" in r[t]["text"] and not r[t]["bad"], t
        assert r[t]["button"]["action"] == "login.remote", t
    assert "(그때 로그인 필요)" in r["issue"]["text"] and r["threads_api"]["state"] == "unknown"
    write_export(tmp_path / "blogauto.json")  # 새 기록이면 그대로
    assert rows_of(logins.view(bot))["issue"]["state"] == "needed"

def test_open_remote_screen_turns_its_row_into_an_open_link(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    s = remote_login.start(bot.db, lambda msg: None, "zigzag")
    v = logins.view(bot)
    r = rows_of(v)
    assert r["zigzag"]["button"]["url"] == remote_login.url(s) and r["zigzag"]["button"]["label"] == "원격 화면 열기"
    assert r["issue"]["button"]["disabled"] is True  # 서버 화면은 한 번에 하나
    assert v["vnc"]["target"] == "zigzag" and v["vnc"]["label"] == "지그재그" and v["vnc"]["url"].startswith("/dashboard/vnc/vnc.html?")


async def test_a_screen_that_never_came_up_does_not_lock_the_remote_login(bot: DealBot, settings: Settings, tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 화면을 못 띄웠으면 (받은편지함을 늦게 읽음 · 6080 이 바쁨 · 스크립트 실패) GRACE_S 뒤엔 다시 누를 수 있다 —
    25분 동안 '이미 열려 있어요'·죽은 링크·흐린 단추로 막지 않는다. 다른 대상은 화면이 떠 있거나 띄우는 중이면 막는다 (비밀번호가 바뀌지 않게)."""
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    assert not remote_login.listening()  # 이 시험 환경엔 서버 화면이 없음 (= 못 뜬 화면)
    assert (await act(bot, "login.remote", {"target": "ably"}))["code"] == "done"
    other = await act(bot, "login.remote", {"target": "finance"})  # 띄우는 중 — 다른 대상은 기다림
    assert other["code"] == "busy" and "에이블리" in other["text"]
    assert remote_login.live(remote_login.session(bot.db))
    assert any(i["iid"] == "sys-vnc" for i in bot.dash_state.items())
    s = json.loads(bot.db.kv_get(remote_login.KV))
    s["at"] -= remote_login.GRACE_S + 1  # 한 번도 안 뜬 채 시간이 지남
    bot.db.kv_set(remote_login.KV, json.dumps(s))
    assert not remote_login.live(remote_login.session(bot.db))
    assert not any(i["iid"] == "sys-vnc" for i in bot.dash_state.items())  # 죽은 링크 줄을 두지 않음
    v = logins.view(bot)
    r = rows_of(v)
    assert v["vnc"] is None and r["ably"]["button"]["action"] == "login.remote" and "disabled" not in r["issue"]["button"]
    again = await act(bot, "login.remote", {"target": "ably"})
    assert again["code"] == "done" and "이미" not in again["text"]
    inbox = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [u["login_remote"]["target"] for u in inbox if "login_remote" in u] == ["ably", "ably"]  # finance 는 안 보냄
    # 화면이 뜬 뒤(seen)엔 시간이 지나도 계속 막음
    monkeypatch.setattr(remote_login.refresh, "__defaults__", (None, lambda: True))  # 서버 화면이 떠 있음
    assert remote_login.refresh(bot.db)["seen"] is True
    s = json.loads(bot.db.kv_get(remote_login.KV))
    s["at"] -= remote_login.GRACE_S + 1
    bot.db.kv_set(remote_login.KV, json.dumps(s))
    assert (await act(bot, "login.remote", {"target": "instagram"}))["code"] == "busy"
    assert rows_of(logins.view(bot))["issue"]["button"]["disabled"] is True

def test_logins_js_is_local_safe_and_korean() -> None:
    js = (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js
    assert "/dashboard/api/logins" in js and "X-Dash-Token" in js and "MutationObserver" in js
    for word in ("로그인 관리", "원격 화면 열기", "QR 크게 보기", "다시 보기"):
        assert word in js, word
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "logins.js")], capture_output=True).returncode == 0


# 작은 가짜 DOM (jsdom 없이): app.js 가 #main 을 다시 그려도 칸이 맨 위에 다시 붙는지 · 단추가 X-Dash-Token 으로 login.remote 를 보내는지 ·
# QR 화면 · 다른 탭에선 붙지 않는지
DOM_SHIM = r"""
const calls = [], out = {};
let observer = null;
class T { constructor(s) { this.data = s; this.parentNode = null; } get nodeType() { return 3; } get textContent() { return this.data; } }
class E {
  constructor(t) { this.tagName = t.toUpperCase(); this.children = []; this.attrs = {}; this.ls = {}; this.parentNode = null; this.className = ''; this._t = ''; this.disabled = false; this.hidden = false; }
  get nodeType() { return 1; }
  get firstChild() { return this.children[0] || null; }
  set textContent(v) { this._t = String(v); this.children = []; }
  get textContent() { return this._t + this.children.map(c => c.textContent).join(''); }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'disabled') this.disabled = true; }
  addEventListener(n, f) { (this.ls[n] = this.ls[n] || []).push(f); }
  _add(c, front) { if (!c.nodeType) c = new T(String(c)); if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(x => x !== c); c.parentNode = this; front ? this.children.unshift(c) : this.children.push(c); }
  append(...cs) { cs.forEach(c => this._add(c, false)); this._n(); }
  prepend(...cs) { cs.reverse().forEach(c => this._add(c, true)); this._n(); }
  replaceChildren(...cs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; cs.forEach(c => this._add(c, false)); this._n(); }
  remove() { if (this.parentNode) { this.parentNode.children = this.parentNode.children.filter(x => x !== this); this.parentNode = null; } }
  _n() { if (observer && observer.t === this) queueMicrotask(observer.cb); }
  all(f, acc = []) { for (const c of this.children) if (c.nodeType === 1) { if (f(c)) acc.push(c); c.all(f, acc); } return acc; }
  querySelector(s) { return this.all(c => s === '.spin' && c.className === 'spin')[0] || null; }
  click() { (this.ls.click || []).forEach(f => f({currentTarget: this})); }
}
const ids = {}, mk = (t, id) => { const e = new E(t); ids[id] = e; return e; };
const main = mk('main', 'main'), boot = mk('script', 'boot'), toast = mk('div', 'toast'), rf = mk('button', 'rf');
boot.textContent = JSON.stringify({me: {csrf: 'tok123'}});
let rfClicks = 0; rf.addEventListener('click', () => { rfClicks++; });
main.append(new E('p'));
global.location = {hash: '#/connect'};
global.window = {addEventListener() {}};
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe(t) { this.t = t; observer = this; } };
global.document = {readyState: 'complete', visibilityState: 'visible', head: new E('head'), getElementById: i => ids[i] || null,
  createElement: t => new E(t), addEventListener() {}, contains: x => { for (let n = x; n; n = n.parentNode) if (n === main) return true; return false; }};
const VIEW = JSON.parse(process.argv[2]);
global.fetch = async (url, opt) => { calls.push({url, opt: opt || {}}); return url === '/dashboard/api/logins'
  ? {status: 200, json: async () => VIEW} : {status: 200, json: async () => ({status: 'ok', code: 'done', text: '서버에 로그인 화면을 띄우는 중이에요'})}; };
const tick = () => new Promise(r => setTimeout(r, 5));
(async () => {
  require(process.argv[1]);
  await tick(); await tick();
  const box = () => main.children.find(c => c.attrs.id === 'lgp');
  out.first = main.firstChild.attrs.id;
  out.rows = box().all(c => c.className.startsWith('row')).map(r => r.attrs['data-id']);
  out.img = (box().all(c => c.tagName === 'IMG')[0] || {attrs: {}}).attrs.src || null;
  main.replaceChildren(new E('div'), new E('div'));  // app.js 가 연결 탭을 다시 그림
  await tick();
  out.after = main.firstChild.attrs.id;
  out.same = box() !== undefined && out.rows.length === box().all(c => c.className.startsWith('row')).length;
  const b = box().all(c => c.tagName === 'BUTTON' && c.textContent === '원격 로그인')[0];
  b.click();
  await tick(); await tick();
  const post = calls.find(c => c.url === '/dashboard/api/action');
  out.post = {token: post.opt.headers['X-Dash-Token'], body: JSON.parse(post.opt.body)};
  out.rf = rfClicks; out.toast = toast.textContent;
  out.disabled = box().all(c => c.tagName === 'BUTTON' && c.disabled).length;
  location.hash = '#/todo';
  main.replaceChildren(new E('div'));
  await tick();
  out.todo = main.children.some(c => c.attrs.id === 'lgp');
  console.log(JSON.stringify(out));
  process.exit(0);  // QR 이 떠 있으면 4초마다 다시 읽는 타이머가 남아 있음
})();
"""


def test_logins_js_mounts_on_the_connect_tab_and_sends_the_remote_login(bot: DealBot, tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    write_export(tmp_path / "blogauto.json")
    remote_login.start(bot.db, lambda msg: None, "ably")  # 열린 원격 화면 하나 → 다른 줄 단추는 흐림
    view = logins.view(bot)
    view["qr"] = {"img": "/dashboard/api/naver_qr.png?t=1", "left_s": 90, "how": logins.QR_HOW}
    bot.db.kv_set(remote_login.KV, json.dumps(None))
    view_free = logins.view(bot)  # 열린 화면 없을 때 — 단추 누르기용
    view_free["qr"] = view["qr"]
    out = subprocess.run([node, "-e", DOM_SHIM, str(STATIC / "logins.js"), json.dumps(view_free, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["first"] == "lgp" and got["after"] == "lgp" and got["same"]  # 다시 그려도 같은 칸이 맨 위
    assert got["rows"] == [r["id"] for r in view_free["rows"]] and got["img"] == "/dashboard/api/naver_qr.png?t=1"
    assert got["post"] == {"token": "tok123", "body": {"action": "login.remote", "args": {"target": "finance"},
                                                       "cid": got["post"]["body"]["cid"], "ctoken": None}}
    assert got["post"]["body"]["cid"].startswith("c_") and got["rf"] == 1 and "로그인 화면" in got["toast"]
    assert got["disabled"] == 0 and got["todo"] is False  # 할 일 탭에는 붙지 않음
    out = subprocess.run([node, "-e", DOM_SHIM.replace("const b = box()", "out.dis = box().all(c => c.tagName === 'BUTTON' && c.disabled).length; const b = box()"),
                          str(STATIC / "logins.js"), json.dumps(view, ensure_ascii=False)], capture_output=True, text=True, timeout=30)
    assert json.loads(out.stdout)["dis"] == len(remote_login.TARGETS) - 1  # 원격 화면이 열려 있으면 나머지 원격 로그인 단추는 흐림
