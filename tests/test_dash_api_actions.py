"""대시보드 앱 '누르는 일' (POST /dashboard/api/action, 주인 10/18): 딜봇 일은 텔레그램 명령과 같은 DealBot 함수로 바로,
블로그 일은 Blog-Auto 에 파일로. 확인 창(★) · cid 한 번 · 같은 일 두 번 막기 · 결과 읽기 · 다 했어요 · 광고 표시는 늘 맨 앞."""

from __future__ import annotations

import io
import json
import time

import pytest
from PIL import Image

from dash_helpers import blogauto_status, post_json, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.storage.db import MAYBE_POSTED


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def act(bot: DealBot, action: str, args: dict, h: dict | None = None, **extra: object) -> tuple[int, dict]:
    h = h or session_headers(bot)
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": action, "args": args, **extra}, h)
    return status, body


async def confirmed(bot: DealBot, action: str, args: dict, h: dict | None = None) -> tuple[int, dict]:
    h = h or session_headers(bot)
    status, body = await act(bot, action, args, h)
    assert status == 409 and body["status"] == "confirm" and body["title"] and body["ok"], body
    return await act(bot, action, args, h, ctoken=body["ctoken"])


def photo_upload(bot: DealBot, h: dict, size: tuple[int, int] = (1200, 900)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "JPEG")
    path = bot.dashboard.api.uploads.tmp_path()  # type: ignore[union-attr]
    path.write_bytes(buf.getvalue())
    caller = bot.dashboard.api.caller(h)[0]  # type: ignore[union-attr]
    return bot.dashboard.api.uploads.process(path, kind="photo", ctype="image/jpeg", session=caller.session)["upload_id"]  # type: ignore[union-attr]


# ---------------------------------------------------------------- 딜봇 일
async def test_link_calls_the_same_attach_link_and_records_an_audit_row(bot: DealBot) -> None:
    item = queued(bot)
    status, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://link.coupang.com/a/abc"})
    assert status == 200 and body["status"] == "ok" and body["code"] == "linked" and body["refresh"] is True
    after = bot.db.get_queue_item(item.id)
    assert after.status == "pending" and after.deal.affiliate_url == "https://link.coupang.com/a/abc"  # type: ignore[union-attr]
    row = bot.db._one("SELECT * FROM dash_audit WHERE action = 'deal.link'")
    assert row is not None and row["result_code"] == "linked" and row["target"] == f"deal-{item.id}"
    status, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://t.me/oneul_hotdeal/1"})
    assert body["code"] == "link_not_waiting"


async def test_link_refuses_telegram_urls_like_the_reply(bot: DealBot) -> None:
    item = queued(bot)
    status, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://t.me/oneul_hotdeal/123"})
    assert status == 200 and body["status"] == "error" and body["code"] == "link_bad"
    assert bot.db.get_queue_item(item.id).status == "awaiting_link"  # type: ignore[union-attr]


async def test_link_with_a_photo_passes_jpeg_bytes(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    item = queued(bot, shop="naver", product_id="naver:1", url="https://smartstore.naver.com/x/products/1")
    seen: dict = {}

    async def fake_attach(queue_id: int, url: str, *, photo: object = None) -> str:
        seen.update(qid=queue_id, url=url, photo=photo)
        bot.db.set_queue_link(queue_id, url)
        return "✅ ok"

    monkeypatch.setattr(bot, "attach_link", fake_attach)
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    status, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://naver.me/abc", "upload_id": uid}, h)
    assert body["code"] == "linked" and seen["qid"] == item.id
    assert isinstance(seen["photo"], bytes) and seen["photo"][:2] == b"\xff\xd8" and len(seen["photo"]) <= 10 * 1024 * 1024


async def test_photo_refused_keeps_the_link_unattached(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    item = queued(bot, shop="naver", product_id="naver:2", url="https://smartstore.naver.com/x/products/2")

    async def refuse(queue_id: int, url: str, *, photo: object = None) -> str:
        from dealbot.monitoring.admin import photo_refused

        return photo_refused(queue_id, "짧은 변이 600px 보다 작아요")

    monkeypatch.setattr(bot, "attach_link", refuse)
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    _s, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://naver.me/abc", "upload_id": uid}, h)
    assert body["code"] == "photo_refused" and "600px" in body["text"]
    assert bot.db.get_queue_item(item.id).status == "awaiting_link"  # type: ignore[union-attr]


async def test_skip_needs_a_confirm_and_the_ctoken_is_single_use_bound_and_expiring(bot: DealBot) -> None:
    item = queued(bot)
    h = session_headers(bot)
    status, body = await act(bot, "deal.skip", {"qid": item.id}, h)
    assert status == 409 and body["danger"] is True and bot.db.get_queue_item(item.id).status == "awaiting_link"  # type: ignore[union-attr]
    tok = body["ctoken"]
    other = queued(bot, product_id="toss:2")
    status, bad = await act(bot, "deal.skip", {"qid": other.id}, h, ctoken=tok)  # 다른 args
    assert status == 409 and bad["status"] == "confirm_expired"
    status, body2 = await act(bot, "deal.skip", {"qid": item.id}, session_headers(bot), ctoken=tok)  # 다른 세션
    assert status == 409 and body2["status"] == "confirm_expired"
    status, ok = await act(bot, "deal.skip", {"qid": item.id}, h, ctoken=tok)
    assert status == 200 and ok["code"] == "skipped" and bot.db.get_queue_item(item.id).status == "skipped"  # type: ignore[union-attr]
    status, again = await act(bot, "deal.skip", {"qid": item.id}, h, ctoken=tok)  # 한 번만
    assert status == 409 and again["status"] == "confirm_expired"
    desk = bot.dashboard.api.desk  # type: ignore[union-attr]
    old = desk.tokens.issue(desk.tokens and "x", "deal.skip", {"qid": 1})
    desk.tokens.clock = lambda: time.time() + 301
    assert not desk.tokens.check(old, "x", "deal.skip", {"qid": 1})


async def test_ok_ok_text_posted_post_pause_resume_run(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    review = queued(bot, status="awaiting_approval", product_id="toss:3")
    _s, body = await act(bot, "deal.ok", {"qid": review.id})
    assert body["code"] == "approved" and bot.db.get_queue_item(review.id).status == "pending"  # type: ignore[union-attr]
    _s, body = await act(bot, "deal.ok", {"qid": review.id})
    assert body["code"] == "wrong_state"
    calls: list = []

    async def fake_posted(qid: int, link: str = "") -> str:
        calls.append(("posted", qid, link))
        bot.db.update_queue_item(qid, status="published")
        return "✅"

    monkeypatch.setattr(bot, "mark_posted", fake_posted)
    unsure = queued(bot, status="failed", error=f"{MAYBE_POSTED}: timeout", product_id="toss:4")
    _s, body = await act(bot, "deal.posted", {"qid": unsure.id, "url": "https://t.me/oneul_hotdeal/9"})
    assert body["code"] == "posted" and calls == [("posted", unsure.id, "https://t.me/oneul_hotdeal/9")]
    _s, body = await confirmed(bot, "bot.pause", {})
    assert body["code"] == "paused" and bot.state.paused
    _s, body = await act(bot, "bot.resume", {})
    assert body["code"] == "resumed" and not bot.state.paused
    _s, body = await act(bot, "bot.run", {"name": "nope"})
    assert body["code"] == "bad_args"


async def test_repost_needs_a_confirm_and_reuses_the_stored_link(bot: DealBot) -> None:
    unsure = queued(bot, status="failed", error=f"{MAYBE_POSTED}: timeout", product_id="toss:5")
    deal = unsure.deal
    deal.affiliate_url = "https://link.coupang.com/a/re"
    bot.db.update_queue_item(unsure.id, status="failed", error=f"{MAYBE_POSTED}: timeout", deal=deal)
    status, body = await act(bot, "deal.repost", {"qid": unsure.id})
    assert status == 409 and "두 번" in body["body"]
    _s, body = await confirmed(bot, "deal.repost", {"qid": unsure.id})
    assert body["code"] == "done" and bot.db.get_queue_item(unsure.id).status == "pending"  # type: ignore[union-attr]
    plain = queued(bot, status="failed", error=f"{MAYBE_POSTED}: x", product_id="toss:6")
    _s, body = await confirmed(bot, "deal.repost", {"qid": plain.id})
    assert body["code"] == "no_link"


async def test_cid_replays_the_same_result(bot: DealBot) -> None:
    item = queued(bot)
    h = session_headers(bot)
    _s, first = await act(bot, "deal.link", {"qid": item.id, "url": "https://link.coupang.com/a/1"}, h, cid="c_tap123456")
    _s, second = await act(bot, "deal.link", {"qid": item.id, "url": "https://link.coupang.com/a/1"}, h, cid="c_tap123456")
    assert first["id"] == second["id"] and second["code"] == "linked"
    assert bot.db._one("SELECT COUNT(*) AS n FROM dash_actions")["n"] == 1


async def test_bad_args_and_unknown_actions(bot: DealBot) -> None:
    for action, args in (("deal.link", {"qid": "x", "url": "https://a"}), ("deal.link", {"qid": 1}), ("nope.x", {}),
                         ("blog.publish", {"blog": "evil", "pid": 1}), ("blog.photos", {"blog": "issue", "pid": 1, "uploads": ["../x"]}),
                         ("blog.channel", {"blog": "issue", "pid": 1, "url": "https://blog.naver.com/someoneelse/1"})):
        status, body = await act(bot, action, args)
        assert status == 400 and body["code"] == "bad_args", (action, args)


async def test_compliance_disclosure_first_for_dashboard_ok_text_and_post(bot: DealBot) -> None:
    """대시보드로 고쳐 올린 정보 글·직접 올린 딜도 채널 글은 광고 표시가 맨 앞 (발행기 테스트와 같은 확인)."""
    _s, body = await confirmed(bot, "deal.post", {"text": "https://link.coupang.com/a/xyz\n테스트 상품 무선청소기\n12,900원"})
    assert body["code"] == "queued", body
    item = bot.db.pending_items(10)[0]
    text = bot.publisher.render(item.deal)
    first = next(ln for ln in text.splitlines() if ln.strip())
    shop = bot.registry.get(item.deal.product.shop)
    from dealbot.shops import deal_disclosure

    disclosure = deal_disclosure(shop, item.deal.product.extra)
    assert disclosure and disclosure.split()[0] in first


# ---------------------------------------------------------------- 블로그 일 (Blog-Auto)
async def test_blog_publish_writes_a_dash_action_and_results_come_back(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    h = session_headers(bot)
    status, body = await act(bot, "blog.publish", {"blog": "issue", "pid": 31}, h)
    assert status == 409 and "발행" in body["title"]
    status, body = await act(bot, "blog.publish", {"blog": "issue", "pid": 31}, h, ctoken=body["ctoken"], cid="c_pub000001")
    assert status == 202 and body["status"] == "queued" and body["id"].startswith("a_")
    aid = body["id"]
    r = spool.root(settings.data_dir, spool.DASH_ACTIONS)
    f = spool.read(r / "new" / f"{aid}.json")
    assert f["action"] == "publish" and f["blog"] == "issue" and f["args"] == {"blog": "issue", "pid": 31}  # type: ignore[index]
    assert f["target"] == "post-issue-31" and f["confirmed"] is True and f["origin"]["cid"] == "c_pub000001"  # type: ignore[index]
    # 다른 기기에서 같은 글 발행을 또 눌러도 같은 id
    h2 = session_headers(bot)
    status, b2 = await act(bot, "blog.publish", {"blog": "issue", "pid": 31}, h2)
    status, b2 = await act(bot, "blog.publish", {"blog": "issue", "pid": 31}, h2, ctoken=b2["ctoken"])
    assert b2["id"] == aid and len(spool.names(r, "new")) == 1
    # Blog-Auto 가 맡아 결과를 씀 → /api/actions 로 보임
    spool.claim(r, aid)
    spool.write(r, aid, {"v": 1, "id": aid, "status": "ok", "code": "published", "text": "발행했어요",
                         "post": {"blog": "issue", "pid": 31, "status": "published"}, "started_at": time.time(),
                         "updated_at": time.time(), "done_at": time.time()}, sub="results")
    res = await bot.web.dispatch("GET", f"/dashboard/api/actions?ids={aid}", h)
    got = json.loads(res[1])["actions"][0]
    assert got["status"] == "ok" and got["code"] == "published" and got["target"] == "post-issue-31"
    assert spool.names(r, "results") == []


async def test_blog_actions_503_when_blogauto_is_stale_or_has_no_bridge(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings, age=3600)
    status, body = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    assert status == 503 and body["code"] == "blogauto_down"
    blogauto_status(settings, bridge=0)
    status, body = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    assert status == 503
    blogauto_status(settings, actions=["publish"])
    status, body = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    assert status == 503 and body["code"] == "unsupported"


async def test_photos_map_upload_ids_to_paths_inside_the_upload_dir(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    status, body = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": [uid], "slot": 3}, h)
    assert status == 202
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    [path] = f["args"]["uploads"]  # type: ignore[index]
    assert path.startswith(str(settings.data_dir / "dash_uploads" / "files")) and path.endswith(f"{uid}.jpg")
    assert f["args"]["mode"] == "slot" and f["args"]["slot"] == 3  # type: ignore[index]
    status, body = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": ["0" * 32]}, h)
    assert status == 400 and body["code"] == "bad_upload"


async def test_secret_args_never_reach_the_db_or_the_audit(bot: DealBot, settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    blogauto_status(settings)
    secret_url = "https://localhost/kakao?code=FAKE_KAKAO_CODE_123"
    status, body = await act(bot, "blog.kakao_connect", {"url": secret_url})
    assert status == 202
    calls: list = []

    async def fake_token(token: str) -> str:
        calls.append(token)
        return "✅ 연결했어요"

    bot.threads_submit_token = fake_token  # type: ignore[method-assign]
    _s, b = await act(bot, "sns.token", {"sns": "threads", "token": "FAKE_THREADS_TOKEN_XYZ"})
    assert b["code"] == "connected" and calls == ["FAKE_THREADS_TOKEN_XYZ"] and "FAKE_THREADS" not in json.dumps(b)
    dump = json.dumps([dict(r) for r in bot.db._q("SELECT * FROM dash_actions")] +
                      [dict(r) for r in bot.db._q("SELECT * FROM dash_audit")], ensure_ascii=False)
    for secret in ("FAKE_KAKAO_CODE_123", "FAKE_THREADS_TOKEN_XYZ"):
        assert secret not in dump and secret not in caplog.text
    assert "[비밀]" in dump


async def test_unclaimed_actions_expire_and_secret_files_are_deleted(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    _s, a = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    _s, k = await act(bot, "blog.kakao_connect", {"url": "https://localhost/k?code=SECRET"})
    out = bot.actions_out
    out.clock = lambda: time.time() + 700
    got = out.sweep()
    assert got["expired"] == 2
    r = out.root()
    assert [p.name for p in (r / "expired").iterdir()] == [f"{a['id']}.json"]  # 비밀이 든 파일은 옮기지 않고 지움
    assert out.store.get(a["id"])["status"] == "expired"  # type: ignore[index]


async def test_running_without_news_for_20min_becomes_unknown(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    _s, a = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    r = bot.actions_out.root()
    spool.claim(r, a["id"])
    t0 = time.time()
    spool.write(r, a["id"], {"v": 1, "id": a["id"], "status": "running", "code": "", "text": "올리는 중", "started_at": t0,
                             "updated_at": t0, "done_at": None}, sub="results")
    bot.actions_out.ingest()
    bot.actions_out.clock = lambda: t0 + 21 * 60
    assert bot.actions_out.sweep()["unknown"] == 1
    bot.actions_out.ingest()
    assert bot.actions_out.store.get(a["id"])["status"] == "unknown"  # type: ignore[index]


async def test_dismiss_only_for_things_fixed_outside(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    for iid, key in (("deal-12", "link:12"), ("post-issue-3", "ask:3"), ("post-issue-3", "draft:issue:3"),
                     ("post-issue-3", "oedit:3:x"), ("deal-4", "review:4"), ("blog-issue-buddy", "photo:issue:3")):
        _s, body = await act(bot, "todo.dismiss", {"iid": iid, "key": key})
        assert body["code"] == "not_dismissible", (iid, key)
    bot.router.open({"src": "dealbot", "key": "sns_zero", "kind": "sns_zero", "iid": "sns-status", "title": "SNS 0개", "at": time.time()})  # type: ignore[union-attr]
    _s, body = await act(bot, "todo.dismiss", {"iid": "sns-status", "key": "sns_zero"})
    assert body["code"] == "done" and bot.router.store.get("dealbot", "sns_zero").resolved_at  # type: ignore[union-attr]
    _s, body = await act(bot, "todo.dismiss", {"iid": "post-issue-7", "key": "dup:7"})
    assert body["code"] == "done"
    files = [spool.read(bot.actions_out.root() / "new" / f"{n}.json") for n in spool.names(bot.actions_out.root(), "new")]
    assert any(f["action"] == "dismiss" and f["args"] == {"key": "dup:7"} for f in files)  # type: ignore[index]


async def test_dashboard_results_are_quiet_no_push(bot: DealBot) -> None:
    """대시보드에서 주인이 한 일 때문에 새로 열린 알림은 quiet (화면을 보고 있으니 푸시 안 함)."""
    review = queued(bot, status="awaiting_approval", product_id="toss:9")

    def fake_approve(qid: int, text: str | None = None) -> str:
        bot.notifier.owner_line({"kind": "photo_need", "key": f"photo:{qid}", "text": "🔔[할 일] 사진", "short": "사진", "ttl_h": 4})
        bot.db.update_queue_item(qid, status="pending")
        return "✅"

    bot.approve_item = fake_approve  # type: ignore[method-assign]
    await act(bot, "deal.ok", {"qid": review.id})
    row = bot.router.store.get("dealbot", f"photo:{review.id}")  # type: ignore[union-attr]
    assert row is not None and row.quiet
