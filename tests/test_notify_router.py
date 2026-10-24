"""주인 알림 길 (주인 10/18 — 웹 푸시가 먼저, 카톡은 예비): 같은 일 한 번 · 20초 묶기 · 한 시간 8번 · quiet · 늦추기·모으기 ·
해결 · src 안에서만 해결 · 오래된 열림 무시 · 재시작 · 실패하면 카톡 · 예전 Blog-Auto 면 받은편지함 줄."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dash_helpers import ORIGIN, Clock, PushServer, decrypt, fixture, subscription
from dealbot.bridge import spool
from dealbot.bridge.kakao_out import KakaoOut
from dealbot.notify import router as routermod
from dealbot.notify.router import NotifyRouter
from dealbot.storage.db import Database


class Env:
    def __init__(self, tmp: Path, *statuses: int, retry_after: str | None = None) -> None:
        self.tmp = tmp
        self.db = Database(tmp / "t.db")
        self.clock = Clock()
        self.bridge = True
        self.inbox: list[dict[str, Any]] = []
        self.kakao = KakaoOut(tmp, bridge_ok=lambda: self.bridge, inbox=lambda x: self.inbox.append(x) or True, clock=self.clock)
        self.server = PushServer(*(statuses or (201,)), retry_after=retry_after)
        self.http = self.server.client()
        self.router = self.make()
        self.ua = None

    def make(self) -> NotifyRouter:
        return NotifyRouter(self.db, data_dir=self.tmp, http=self.http, public_url=ORIGIN, tz="Asia/Seoul", kakao=self.kakao,
                            clock=self.clock)

    def device(self) -> str:
        body, self.ua = subscription()
        from dealbot.notify import webpush

        return self.router.store.add_sub(endpoint=body["endpoint"], endpoint_hash=webpush.endpoint_hash(body["endpoint"]),
                                         p256dh=body["keys"]["p256dh"], auth=body["keys"]["auth"], label="Galaxy", session="s1",
                                         now=self.clock())

    def open(self, key: str, kind: str = "link_request", *, src: str = "dealbot", iid: str | None = None, **kw: Any) -> str:
        n = {"src": src, "key": key, "kind": kind, "iid": iid or f"deal-{abs(hash(key)) % 1000}", "title": f"{key} 제목",
             "body": "대시보드 할 일에서 해 주세요", "short": key[:48], "ttl_h": 24, "at": self.clock()}
        n.update(kw)
        return self.router.open(n)

    def pushes(self) -> list[dict[str, Any]]:
        return [decrypt(r.content, self.ua) for r in self.server.requests]

    def kakao_files(self) -> list[dict[str, Any]]:
        r = spool.root(self.tmp, spool.KAKAO_OUT)
        return [spool.read(r / "new" / f"{n}.json") for n in spool.names(r, "new")]  # type: ignore[misc]


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    e = Env(tmp_path)
    yield e
    e.db.close()


async def test_urgent_is_pushed_once_after_the_20s_window(env: Env) -> None:
    env.device()
    env.open("link:12", iid="deal-12")
    await env.router.tick()
    assert env.server.requests == []  # 20초 동안 더 모은다
    env.clock.tick(21)
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "item" and p["tag"] == "deal-12" and p["url"] == "/dashboard/go/deal-12" and p["urgent"] is True
    assert p["v"] == 1 and p["title"] and "ts" in p and "badge" in p
    env.clock.tick(60)
    env.open("link:12", iid="deal-12")  # 같은 일이 다시 열려도 (해결 전) 한 번만
    await env.router.tick()
    assert len(env.server.requests) == 1


async def test_two_urgent_in_20s_become_one_batch(env: Env) -> None:
    env.device()
    env.open("link:1", iid="deal-1")
    env.clock.tick(5)
    env.open("photo:2", "photo_need", iid="deal-2")
    env.clock.tick(20)
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "batch" and p["title"] == "할 일 2개" and p["tag"] == "todo-batch" and p["url"] == "/dashboard/go/todo"
    assert "링크 넣기 1" in p["body"] and "사진 1" in p["body"]


async def test_at_most_8_pushes_per_hour_then_one_batch(env: Env) -> None:
    env.device()
    for i in range(10):
        env.open(f"link:{i}", iid=f"deal-{i}")
        env.clock.tick(25)
        await env.router.tick()
    assert len(env.server.requests) == 8
    env.clock.tick(3600)
    await env.router.tick()
    assert len(env.server.requests) == 9 and env.pushes()[-1]["type"] == "batch"


async def test_quiet_and_digest_notices_are_never_pushed(env: Env) -> None:
    env.device()
    env.open("link:1", iid="deal-1", quiet=True)
    env.open("collector:x", "collector_stuck", iid="sys-dealbot")
    with env.router.quiet():
        env.open("photo:3", "photo_need", iid="deal-3")
    env.clock.tick(60)
    await env.router.tick()
    assert env.server.requests == []
    assert {n.key for n in env.router.store.open_rows(env.clock())} == {"link:1", "collector:x", "photo:3"}  # 할 일 줄은 그대로


async def test_resolve_cancels_a_pending_push(env: Env) -> None:
    env.device()
    env.open("link:1", iid="deal-1")
    env.router.resolve("dealbot", ["link:1"])
    env.clock.tick(30)
    await env.router.tick()
    assert env.server.requests == []


async def test_no_device_falls_back_to_kakao_once_and_resolve_cancels_it(env: Env) -> None:
    env.open("selfcheck:db", "selfcheck", iid="deal-12")
    env.clock.tick(21)
    await env.router.tick()
    await env.router.tick()
    [k] = env.kakao_files()
    assert k["op"] == "send" and k["kind"] == "urgent" and k["key"] == "selfcheck:db" and k["button"] == "대시보드 열기"
    assert k["link"] == f"{ORIGIN}/dashboard/go/deal-12" and len(k["text"]) <= 200 and k["id"].startswith("k_")
    env.router.resolve("dealbot", ["selfcheck:db"])
    files = env.kakao_files()
    assert files[-1]["op"] == "resolve" and files[-1]["keys"] == ["selfcheck:db"]


async def test_old_blogauto_without_bridge_gets_the_v2_inbox_line(env: Env) -> None:
    env.bridge = False
    env.open("selfcheck:db", "selfcheck", iid="deal-12")
    env.clock.tick(21)
    await env.router.tick()
    assert env.kakao_files() == []
    [line] = env.inbox
    note = line["owner_notice"]
    assert note["v"] == 2 and note["key"] == "selfcheck:db" and note["urgent"] is True and "/dashboard/go/deal-12" in note["link"]


async def test_5xx_retries_30s_then_2min_then_kakao(tmp_path: Path) -> None:
    env = Env(tmp_path, 500)
    env.device()
    env.open("publish_broken", "publish_broken", iid="deal-1")
    env.clock.tick(21)
    await env.router.tick()
    assert len(env.server.requests) == 1 and env.kakao_files() == []
    env.clock.tick(29)
    await env.router.tick()
    assert len(env.server.requests) == 1
    env.clock.tick(2)
    await env.router.tick()
    assert len(env.server.requests) == 2
    env.clock.tick(121)
    await env.router.tick()
    assert len(env.server.requests) == 3
    await env.router.tick()
    [k] = env.kakao_files()
    assert k["key"] == "publish_broken"
    env.db.close()


async def test_410_drops_the_subscription(tmp_path: Path) -> None:
    env = Env(tmp_path, 410)
    sid = env.device()
    env.open("link:1", iid="deal-1")
    env.clock.tick(21)
    await env.router.tick()
    assert env.router.store.sub(sid) is None
    env.db.close()


async def test_429_waits_for_retry_after(tmp_path: Path) -> None:
    env = Env(tmp_path, 429, 201, retry_after="90")
    env.device()
    env.open("link:1", iid="deal-1")
    env.clock.tick(21)
    await env.router.tick()
    env.clock.tick(60)
    await env.router.tick()
    assert len(env.server.requests) == 1
    env.clock.tick(31)
    await env.router.tick()
    assert len(env.server.requests) == 2 and env.router.store.get("dealbot", "link:1").push_ok is True  # type: ignore[union-attr]
    env.db.close()


async def test_delay_holds_the_push_not_the_row_and_resolve_cancels(env: Env) -> None:
    env.device()
    env.open("draft:issue:31", "draft_ready", src="blogauto", iid="post-issue-31", delay_s=300)
    assert [n.key for n in env.router.store.open_rows(env.clock())] == ["draft:issue:31"]  # 줄은 바로
    env.clock.tick(200)
    await env.router.tick()
    assert env.server.requests == []
    env.router.resolve("blogauto", ["draft:issue:31"])
    env.clock.tick(400)
    await env.router.tick()
    assert env.server.requests == []


async def test_group_wait_sends_one_batch_when_n_items(env: Env) -> None:
    env.device()
    for i in (1, 2):
        env.open(f"photo:issue:{i}", "pick_photos", src="blogauto", iid=f"post-issue-{i}", group="pick_photos", wait_s=7200, wait_n=3)
    env.clock.tick(60)
    await env.router.tick()
    assert env.server.requests == []
    env.open("photo:issue:3", "pick_photos", src="blogauto", iid="post-issue-3", group="pick_photos", wait_s=7200, wait_n=3)
    env.clock.tick(21)
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "batch" and p["title"] == "할 일 3개"


async def test_blogauto_prefix_resolve_never_closes_a_dealbot_row(env: Env) -> None:
    env.open("login:claude_cli", "claude_login", iid="sys-dealbot")
    env.open("login:issue", "login", src="blogauto", iid="conn-naver-issue")
    assert env.router.resolve("blogauto", ["login:"]) == 1
    assert [n.key for n in env.router.store.open_rows(env.clock())] == ["login:claude_cli"]


async def test_stale_open_after_resolve_is_ignored(env: Env) -> None:
    env.open("draft:issue:9", "draft_ready", src="blogauto", iid="post-issue-9")
    env.clock.tick(10)
    env.router.resolve("blogauto", ["draft:issue:9"])
    assert env.open("draft:issue:9", "draft_ready", src="blogauto", iid="post-issue-9", at=env.clock() - 5) == "stale"
    assert env.router.store.open_rows(env.clock()) == []


async def test_restart_pushes_pending_rows_once_as_a_batch(env: Env) -> None:
    env.device()
    env.open("link:1", iid="deal-1")
    env.open("link:2", iid="deal-2")
    env.router = env.make()  # 재시작 (같은 DB)
    env.clock.tick(30)
    await env.router.tick()
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "batch"


async def test_failed_rows_fall_back_to_kakao_after_restart(env: Env) -> None:
    env.open("login:claude_cli", "claude_login", iid="deal-1")
    env.router.store.set_push([("dealbot", "login:claude_cli")], pushed_at=env.clock(), push_ok=0)
    env.router = env.make()
    await env.router.tick()
    assert [k["key"] for k in env.kakao_files()] == ["login:claude_cli"]
    await env.router.tick()
    assert len(env.kakao_files()) == 1


async def test_only_truly_urgent_kinds_fall_back_to_kakao(env: Env) -> None:
    """주인 10/19 ("진짜 급한 거 아니면 카톡 안 오는 걸로"): 기기가 없어 푸시가 안 가도 링크·사진·확인 부탁은 카톡 예비로 넘기지 않는다
    (대시보드 할 일에는 그대로). 로그인·연결 끊김·멈춤은 그대로 카톡."""
    env.open("link:12", iid="deal-12")
    env.open("photo:3", "photo_need", iid="deal-3")
    env.open("draft:issue:5", "draft_ready", src="blogauto", iid="post-issue-5")
    env.clock.tick(21)
    await env.router.tick()
    await env.router.tick()
    assert env.kakao_files() == []
    assert {n.key for n in env.router.store.open_rows(env.clock())} >= {"link:12", "photo:3", "draft:issue:5"}
    env.router.resolve("dealbot", ["link:12"])  # 카톡에 넘기지 않았으니 취소 파일도 없음
    assert env.kakao_files() == []
    env.open("login:issue", "login", src="blogauto", iid="sys-blogauto")
    env.clock.tick(21)
    await env.router.tick()
    assert [k["key"] for k in env.kakao_files()] == ["login:issue"]


async def test_kakao_kinds_config_override(tmp_path: Path) -> None:
    env = Env(tmp_path)
    env.router = NotifyRouter(env.db, data_dir=env.tmp, http=env.http, public_url=ORIGIN, tz="Asia/Seoul", kakao=env.kakao,
                              clock=env.clock, kakao_kinds=["link_request"])
    env.open("link:12", iid="deal-12")
    env.open("selfcheck:db", "selfcheck", iid="sys-dealbot")
    env.clock.tick(21)
    await env.router.tick()
    assert [k["key"] for k in env.kakao_files()] == ["link:12"]
    env.db.close()


def test_repeated_digest_errors_are_promoted_once(env: Env) -> None:
    for i in range(3):
        env.open(f"err:main:{i}", "job_error", src="blogauto", iid="sys-blogauto")
    rows = {n.key: n for n in env.router.store.open_rows(env.clock())}
    assert rows["err:main:2"].urgent and not rows["err:main:0"].urgent
    env.open("err:main:3", "job_error", src="blogauto", iid="sys-blogauto")
    assert not env.router.store.get("blogauto", "err:main:3").urgent  # type: ignore[union-attr]


def test_reopen_with_same_text_within_30min_is_not_pushed_again(env: Env) -> None:
    env.open("link:1", iid="deal-1")
    env.router.store.set_push([("dealbot", "link:1")], pushed_at=env.clock(), push_ok=1)
    env.router.resolve("dealbot", ["link:1"])
    env.clock.tick(60)
    assert env.open("link:1", iid="deal-1") == "reopened"
    assert env.router.store.get("dealbot", "link:1").pushed_at is not None  # type: ignore[union-attr]
    env.router.resolve("dealbot", ["link:1"])
    env.clock.tick(60)
    env.open("link:1", iid="deal-1", body="새 글")
    assert env.router.store.get("dealbot", "link:1").pushed_at is None  # type: ignore[union-attr]


def test_urgency_table_matches_the_shared_fixture() -> None:
    assert fixture("urgency.json") == routermod.URGENCY
    assert routermod.urgency("never_heard_of_it") == "DIGEST"


def test_quiet_hours_hold_everything_but_stop(tmp_path: Path) -> None:
    env = Env(tmp_path)
    r = NotifyRouter(env.db, data_dir=tmp_path, http=env.http, public_url=ORIGIN, tz="UTC", kakao=env.kakao, clock=env.clock,
                     quiet_hours="00:00-23:59")
    assert r._in_quiet(env.clock())
    env.db.close()


async def test_push_test_reports_devices_and_kakao_when_none(env: Env) -> None:
    got = await env.router.push_test()
    assert got == {"sent": 0, "failed": 0, "kakao": 1} and env.kakao_files()[0]["kind"] == "test"
    env.device()
    got = await env.router.push_test()
    assert got["sent"] == 1 and env.pushes()[-1]["type"] == "test"
    assert json.loads(json.dumps(got))  # 응답에 주소·키는 없음


# ---------------------------------------------------------------- 새 제휴 수익 알림 (주인 10/24 — notify/earn_watch)
async def test_new_affiliate_sale_pushes_once_and_first_run_only_remembers(env: Env) -> None:
    from types import SimpleNamespace

    from dealbot.dashboard.earn import EarnStore
    from dealbot.notify import earn_watch

    store = EarnStore(env.db)
    day = "2026-10-24"
    export = SimpleNamespace(v2={"earnings": {"shopconnect": {"confirmed": True, "days": [{"day": day, "orders": 0, "commission": 0}]}},
                                 "creator_mall": {"read_at": f"{day}T09:00:00", "malls": [
                                     {"key": "ably", "name": "에이블리", "state": "ok", "windows": [{"label": "오늘", "orders": 1, "commission": 500}]}]}})
    bot = SimpleNamespace(router=env.router, earn_store=store, dash_state=SimpleNamespace(_export=lambda: export), db=env.db,
                          settings=SimpleNamespace(app=SimpleNamespace(timezone="Asia/Seoul")))
    now = 1792800000.0  # 2026-10-24 KST
    env.device()
    assert await earn_watch.check(bot, now) is None and env.server.requests == []  # 처음: 기억만 (지난 주문으로 알리지 않음)
    store.replace_coupang({"days": {(day, "threads"): {"clicks": 9, "orders": 1, "cancels": 0, "amount": 30000, "cancel_amount": 0,
                                                        "commission": 900}}, "orders": []}, day, day, f"{day}T10:00:00")
    export.v2["earnings"]["shopconnect"]["days"][0].update(orders=2, commission=1200)
    got = await earn_watch.check(bot, now + 60)
    assert got and "쿠팡 1건 · 수익 900원" in got and "쇼핑커넥트 2건 · 수익 1,200원" in got
    p = env.pushes()[-1]
    assert p["type"] == "event" and p["title"] == "🎉 제휴 수익이 생겼어요! (3건)" and p["url"] == "/dashboard/#/stats"
    assert await earn_watch.check(bot, now + 120) is None and len(env.pushes()) == 1  # 같은 주문으로 다시 알리지 않음
    assert env.router.store.open_rows(now) == []  # 할 일 줄은 만들지 않음
