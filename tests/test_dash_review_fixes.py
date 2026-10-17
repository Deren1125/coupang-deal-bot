"""대시보드 앱 검토 뒤 고친 것 (주인 10/18 — 휴대폰에서 다 처리): 자동 발행 모드 확인 창 · '다 했어요'는 Blog-Auto 가 닫는 머리만 ·
정리본 파일 모양 · 같은 글 두 번째 사진 묶음 · 맡아 간 파일을 만료로 보지 않음 · 그 줄만 조용히 · 한글 키 · 해결 뒤 다시 열림 ·
블로그 프로그램 멈춤 ⛔ · 같은 글 알림 둘은 한 통 · 망가진 구독 · 카톡 예비는 한 통으로 · 정리에 같은 말 두 번 X ·
PNG ICC · 테스트 알림 한도 · 로그인 실패 기록 · 화면 글 (상태 한 줄·마감·SNS 이름·카톡 안내·통계 판단 불가·버전)."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageCms

from dash_helpers import (
    blogauto_status,
    fixture,
    get_json,
    post_json,
    push_settings,
    queued,
    session_headers,
    subscription,
)
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.bridge.status import BlogAutoStatus
from dealbot.config import Settings
from dealbot.dashboard.uploads import Uploads
from dealbot.notify import ingest
from dealbot.notify.digest import Digests, system_sentences, waiting_sentences
from test_dash_api_actions import act, confirmed, photo_upload
from test_notify_router import Env


def write_export(path: Path, *, modes: dict[str, str] | None = None, **changes: object) -> None:
    data = fixture("export_v2.json")
    data["written_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    data["todo"]["generated_at"] = data["written_at"]
    if modes:
        blogs = data.setdefault("status", {}).setdefault("blogs", {})
        for b, m in modes.items():
            blogs.setdefault(b, {})["mode"] = m
    data.update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


@pytest.fixture
def env(tmp_path: Path):  # type: ignore[no-untyped-def]
    e = Env(tmp_path)
    yield e
    e.db.close()


def new_files(bot: DealBot) -> list[dict]:
    r = bot.actions_out.root()
    return [spool.read(r / "new" / f"{n}.json") for n in spool.names(r, "new")]  # type: ignore[misc]


# ---------------------------------------------------------------- 자동 발행 모드면 확인 창 (주인 결정 3)
async def test_may_publish_actions_ask_first_when_the_blog_publishes_directly(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", modes={"hotdeal": "publish", "issue": "draft"})
    blogauto_status(settings)
    h = session_headers(bot)
    status, body = await act(bot, "blog.hotdeal_now", {}, h)
    assert status == 409 and body["status"] == "confirm" and "바로 네이버에 공개" in body["body"], body
    status, body = await act(bot, "blog.hotdeal_now", {}, h, ctoken=body["ctoken"])
    assert status == 202
    [f] = new_files(bot)
    assert f["action"] == "hotdeal_now" and f["confirmed"] is True  # Blog-Auto 도 confirmed 를 보고 받는다
    status, body = await act(bot, "blog.retry", {"blog": "hotdeal", "pid": 5})
    assert status == 409 and "다시 올릴까요" in body["title"]
    # 초안 모드 블로그는 그대로 한 번에 (확인 창 없음 · confirmed 도 False)
    status, body = await act(bot, "blog.retry", {"blog": "issue", "pid": 3})
    assert status == 202
    f = next(x for x in new_files(bot) if x["id"] == body["id"])
    assert f["confirmed"] is False
    # 화면 값의 확인 표시도 같은 기준
    _s, st, _h = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    hot = next(a for a in st["blogs"]["hotdeal"]["actions"] if a["action"] == "blog.hotdeal_now")
    assert hot["confirm"] is True
    pick = next(a for a in st["blogs"]["issue"]["actions"] if a["action"] == "blog.issue")
    assert pick["confirm"] is False


async def test_fix_keeps_its_confirm_and_warns_when_the_blog_publishes_directly(bot: DealBot, settings: Settings,
                                                                               tmp_path: Path) -> None:
    """말로 고치기(Blog-Auto /fix 도 may_publish): 임시저장 모드여도 원래대로 ★, 자동 발행 모드면 바로 공개된다고 알린다."""
    write_export(tmp_path / "blogauto.json", modes={"issue": "publish", "finance": "draft"})
    blogauto_status(settings)
    h = session_headers(bot)
    status, body = await act(bot, "blog.fix", {"blog": "finance", "pid": 4, "text": "제목 차분하게"}, h)
    assert status == 409 and body["status"] == "confirm" and body["ok"] == "고치기" and "공개" not in body["body"]
    status, body = await act(bot, "blog.fix", {"blog": "issue", "pid": 3, "text": "제목 차분하게"}, h)
    assert status == 409 and "바로 네이버에 공개" in body["body"] and body["ok"] == "고치고 발행", body
    status, body = await act(bot, "blog.fix", {"blog": "issue", "pid": 3, "text": "제목 차분하게"}, h, ctoken=body["ctoken"])
    assert status == 202
    [f] = new_files(bot)
    assert f["action"] == "fix" and f["confirmed"] is True


async def test_availability_is_checked_before_the_confirm_sheet(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings, age=3600)
    status, body = await act(bot, "blog.publish", {"blog": "issue", "pid": 3})
    assert status == 503 and body["code"] == "blogauto_down"


# ---------------------------------------------------------------- 다 했어요
async def test_dismiss_only_heads_blogauto_closes_and_replies_key_works(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    _s, body = await act(bot, "todo.dismiss", {"iid": "blog-issue-waiting", "key": "waiting:issue"})
    assert body["code"] == "not_dismissible" and new_files(bot) == []  # Blog-Auto 가 거절할 키는 화면에서 빼지 않는다
    bot.router.open({"src": "blogauto", "key": "waiting:issue", "kind": "waiting", "iid": "blog-issue-waiting",  # type: ignore[union-attr]
                     "title": "Pick 기다리는 글 3편", "at": time.time()})
    bot.router.open({"src": "blogauto", "key": "buddy:issue", "kind": "buddy", "iid": "blog-issue-buddy",  # type: ignore[union-attr]
                     "title": "서로이웃 신청 2개", "at": time.time()})
    _s, st, _h = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    rows = {it["iid"]: it for it in st["todo"]}
    assert not any(a["action"] == "todo.dismiss" for a in rows["blog-issue-waiting"]["more"])
    assert any(a["action"] == "todo.dismiss" for a in rows["blog-issue-buddy"]["more"])
    _s, body = await act(bot, "todo.dismiss", {"iid": "blog-issue-buddy", "key": "buddy:issue"})
    assert body["code"] == "done"
    # 스레드 답글 시트의 '다 했어요' (키가 ':' 로 끝남)
    _s, body = await act(bot, "todo.dismiss", {"iid": "sns-replies", "key": "threads_replies:"})
    assert body["code"] == "done"


# ---------------------------------------------------------------- 정리본 · 사진 두 번째 묶음 · 한도
async def test_ledger_spool_args_match_the_blogauto_contract(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    h = session_headers(bot)
    path = bot.dashboard.api.uploads.tmp_path()  # type: ignore[union-attr]
    path.write_bytes(("# 정리본\n" + "가" * 80).encode("utf-8"))
    caller = bot.dashboard.api.caller(h)[0]  # type: ignore[union-attr]
    uid = bot.dashboard.api.uploads.process(path, kind="ledger", ctype="text/markdown", session=caller.session)["upload_id"]  # type: ignore[union-attr]
    status, body = await confirmed(bot, "blog.ledger", {"uploads": [uid]}, h)
    assert status == 202
    [f] = new_files(bot)
    assert set(f["args"]) == {"upload"} and f["args"]["upload"].endswith(f"{uid}.md")  # B x_ledger: a.get("upload")
    assert f["args"]["upload"].startswith(str(settings.data_dir / "dash_uploads" / "files"))


async def test_second_photo_batch_on_a_busy_post_is_not_silently_merged(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json", modes={"issue": "draft"})
    blogauto_status(settings)
    h = session_headers(bot)
    a, b = photo_upload(bot, h), photo_upload(bot, h, (1300, 900))
    s1, first = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": [a]}, h)
    assert s1 == 202
    s2, same = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": [a]}, h)
    assert s2 == 202 and same["id"] == first["id"]  # 같은 사진 다시 누름 → 같은 일
    s3, other = await act(bot, "blog.photos", {"blog": "issue", "pid": 46, "uploads": [b]}, h)
    assert s3 == 409 and other["code"] == "busy" and "끝나면 다시" in other["text"]
    assert len(new_files(bot)) == 1


async def test_credit_and_channel_url_limits_match_blogauto(bot: DealBot, settings: Settings) -> None:
    blogauto_status(settings)
    h = session_headers(bot)
    uid = photo_upload(bot, h)
    status, body = await act(bot, "blog.photos", {"blog": "issue", "pid": 1, "uploads": [uid], "credit": "가" * 61}, h)
    assert status == 400 and body["code"] == "bad_args"
    url = "https://blog.naver.com/derenpick/" + "1" * 300
    status, body = await act(bot, "blog.channel", {"blog": "issue", "pid": 1, "url": url}, h)
    assert status == 400


async def test_row_is_written_before_the_file_and_a_failed_write_is_an_error(bot: DealBot, settings: Settings,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    blogauto_status(settings)

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(spool, "write", boom)
    status, body = await act(bot, "blog.noanswer", {"blog": "finance", "pid": 2})
    assert status == 503 and body["code"] == "failed"
    [row] = [dict(r) for r in bot.db._q("SELECT * FROM dash_actions WHERE action = 'blog.noanswer'")]
    assert row["status"] == "error"


async def test_push_test_through_the_action_endpoint_shares_the_test_limit(bot: DealBot) -> None:
    h = session_headers(bot)
    codes = [(await act(bot, "push.test", {}, h))[0] for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


# ---------------------------------------------------------------- 맡아 간 파일
async def test_sweep_does_not_expire_a_file_blogauto_just_claimed(bot: DealBot, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    blogauto_status(settings)
    _s, a = await act(bot, "blog.noanswer", {"blog": "finance", "pid": 2})
    out = bot.actions_out
    r = out.root()
    real_read = spool.read

    def read_after_claim(path: Path) -> dict | None:
        if path.parent.name == "new":
            spool.claim(r, path.stem)  # 목록을 본 뒤 Blog-Auto 가 맡아 감
        return real_read(path)

    monkeypatch.setattr(spool, "read", read_after_claim)
    out.clock = lambda: time.time() + 700
    assert out.sweep()["expired"] == 0
    assert out.store.get(a["id"])["status"] == "queued"  # type: ignore[index]
    monkeypatch.setattr(spool, "read", real_read)
    # 이미 'expired' 로 적힌 줄이어도 Blog-Auto 결과가 오면 그 결과가 맞다 (맡아서 한 일)
    out.store.update(a["id"], status="expired", result={"code": "expired", "text": ""}, now=time.time())
    spool.write(r, a["id"], {"v": 1, "id": a["id"], "status": "ok", "code": "done", "text": "답 없이 진행했어요",
                             "started_at": time.time(), "updated_at": time.time(), "done_at": time.time()}, sub="results")
    out.ingest()
    assert out.store.get(a["id"])["status"] == "ok"  # type: ignore[index]


# ---------------------------------------------------------------- 그 줄만 조용히
async def test_dashboard_action_quiets_only_its_own_item(bot: DealBot) -> None:
    review = queued(bot, status="awaiting_approval", product_id="toss:9")

    def fake_approve(qid: int, text: str | None = None) -> str:
        bot.notifier.owner_line({"kind": "photo_need", "key": f"photo:{qid}", "text": "🔔[할 일] 사진", "short": "사진", "ttl_h": 4})
        bot.notifier.owner_line({"kind": "sns_auth", "key": "sns:instaauth", "text": "⛔[멈춤] 인스타 연결 끊김", "short": "인스타 끊김"})
        bot.db.update_queue_item(qid, status="pending")
        return "✅"

    bot.approve_item = fake_approve  # type: ignore[method-assign]
    await act(bot, "deal.ok", {"qid": review.id})
    assert bot.router.store.get("dealbot", f"photo:{review.id}").quiet  # type: ignore[union-attr]
    other = bot.router.store.get("dealbot", "sns:instaauth")  # type: ignore[union-attr]
    assert other is not None and not other.quiet and other.urgent  # 다른 항목(연결 끊김)은 푸시된다


# ---------------------------------------------------------------- 알림 받기 (Blog-Auto 파일)
def put(tmp: Path, name: str, obj: dict) -> None:
    spool.write(spool.root(tmp, spool.OWNER_NOTIFY), name, obj)


def test_real_blogauto_keys_with_korean_spaces_and_plus_are_accepted(env: Env) -> None:
    base = fixture("notice_open.json")
    for i, (key, kind, iid) in enumerate((("hot:26fw+안효섭+준지:공항", "hot", "post-issue-46"),
                                          ("err:main:스캔:TimeoutError", "job_error", "sys-blogauto"),
                                          ("hot:장원영 공항", "hot", "blog-issue"))):
        put(env.tmp, spool.new_id("n"), dict(base, id=None, key=key, kind=kind, iid=iid, title=f"알림 {i}", at=env.clock()))
    for p in (spool.root(env.tmp, spool.OWNER_NOTIFY) / "new").iterdir():  # id 는 파일 이름과 같아야 함
        data = json.loads(p.read_text(encoding="utf-8"))
        data["id"] = p.stem
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    got = ingest.ingest(env.tmp, env.router)
    assert got["open"] == 3 and got["bad"] == 0
    keys = {n.key for n in env.router.store.open_rows(env.clock())}
    assert {"hot:26fw+안효섭+준지:공항", "err:main:스캔:TimeoutError", "hot:장원영 공항"} <= keys
    assert not ingest.KEY_RE.match("a\nb") and not ingest.KEY_RE.match("x\n") and not ingest.KEY_RE.match(" x")
    assert ingest.clean_keys(["hot:장원영:", "bad\x00"]) == ["hot:장원영:"]


def test_resolve_then_reopen_in_one_batch_keeps_the_reopened_task(env: Env) -> None:
    base = fixture("notice_open.json")
    t0 = env.clock()
    env.router.open({**{k: base[k] for k in ("key", "kind", "iid", "title")}, "src": "blogauto", "at": t0 - 60})
    r1, o2 = spool.new_id("n"), spool.new_id("n")
    put(env.tmp, r1, {"v": 1, "id": r1, "at": t0 - 5, "src": "blogauto", "op": "resolve", "keys": [base["key"]], "prefix": False})
    put(env.tmp, o2, dict(base, id=o2, at=t0 - 3, title="Pick #31 다시 발행 대기"))
    env.clock.tick(10)  # 딜봇이 둘을 한 번에 읽는 때 (재시작·2초 틈)
    got = ingest.ingest(env.tmp, env.router)
    assert got["resolve"] == 1 and got["open"] == 1 and got["stale"] == 0
    row = env.router.store.get("blogauto", base["key"])
    assert row is not None and row.resolved_at is None and row.title == "Pick #31 다시 발행 대기"


# ---------------------------------------------------------------- 블로그 프로그램 멈춤
def test_blogauto_heartbeat_gone_opens_one_urgent_stop_and_closes_when_back(env: Env) -> None:
    now = env.clock()
    assert env.router.watch_blogauto(BlogAutoStatus(), now) is None  # 다리를 본 적이 없으면 (예전 판·처음) 울리지 않음
    assert env.router.watch_blogauto(BlogAutoStatus(at=now - 30, bridge=1), now) is None
    env.clock.tick(25 * 60)
    gone = BlogAutoStatus(at=now - 30, bridge=1)
    assert env.router.watch_blogauto(gone, env.clock()) == "open"
    row = env.router.store.get("dealbot", "blogauto_down")
    assert row is not None and row.urgent and row.sev == "stop" and row.iid == "sys-blogauto" and "멈췄" in row.title
    assert env.router.watch_blogauto(gone, env.clock()) is None  # 한 번만
    assert env.router.watch_blogauto(BlogAutoStatus(at=env.clock(), bridge=1), env.clock()) == "resolve"
    assert env.router.store.get("dealbot", "blogauto_down").resolved_at is not None  # type: ignore[union-attr]


# ---------------------------------------------------------------- 푸시 묶기 · 구독 · 카톡 예비
async def test_two_notices_on_one_post_are_one_item_push_with_the_hot_title(env: Env) -> None:
    env.device()
    env.open("hot:장원영:공항", "hot", src="blogauto", iid="post-issue-46", sev="hot", title="🔥 장원영 공항패션 지금 뜸")
    env.open("photo:issue:46", "pick_photos", src="blogauto", iid="post-issue-46", title="Pick #46 사진 필요")
    env.clock.tick(21)
    await env.router.tick()
    [p] = env.pushes()
    assert p["type"] == "item" and p["tag"] == "post-issue-46" and p["url"] == "/dashboard/go/post-issue-46"
    assert p["title"].startswith("🔥")


async def test_a_broken_subscription_key_does_not_stop_other_devices(env: Env) -> None:
    good = env.device()
    from dealbot.notify import webpush

    bad_ep = "https://fcm.googleapis.com/fcm/send/broken-1"
    bad = env.router.store.add_sub(endpoint=bad_ep, endpoint_hash=webpush.endpoint_hash(bad_ep), p256dh="BAAA", auth="AAAA",
                                   label="Galaxy", session="s2", now=env.clock() - 10)
    env.open("link:7", iid="deal-7")
    env.clock.tick(21)
    out = await env.router.tick()
    assert out["pushed"] == 1 and len(env.pushes()) == 1
    assert env.router.store.sub(bad) is None and env.router.store.sub(good) is not None


async def test_subscribe_refuses_a_key_that_is_not_a_p256_point(bot: DealBot) -> None:
    body, _ua = subscription()
    body["keys"]["p256dh"] = "BP" + "A" * 85  # 65바이트·0x04 로 시작하지만 곡선 위의 점이 아님
    status, got, _h = await post_json(bot, "/dashboard/api/push/subscribe", body, session_headers(bot))
    assert status == 400 and got["code"] == "bad_args"


async def test_kakao_backup_bundles_several_items_into_one_memo(env: Env) -> None:
    for i in (1, 2, 3):
        env.open(f"link:{i}", iid=f"deal-{i}", short=f"상품 {i}")
    env.clock.tick(21)
    out = await env.router.tick()  # 기기가 없음 → 카톡 예비
    [k] = env.kakao_files()
    assert out["kakao"] == 1 and k["kind"] == "urgent" and "할 일 3개" in k["text"] and len(k["text"]) <= 200
    assert k["link"].endswith("/dashboard/go/todo")
    assert all(env.router.store.get("dealbot", f"link:{i}").kakao_at for i in (1, 2, 3))  # type: ignore[union-attr]
    await env.router.tick()
    assert len(env.kakao_files()) == 1


# ---------------------------------------------------------------- 정리
def test_digest_says_a_waiting_post_once(env: Env) -> None:
    todo = [{"kind": "photos", "title": "안효섭 준지 26FW", "iid": "post-issue-46"},
            {"kind": "publish", "title": "토스 이벤트", "iid": "post-finance-12"}]

    class Export:
        v2 = {"digest": {"waiting": ["Pick 「안효섭 준지 26FW」 1편이 사진을 기다려요.",
                                     "아카이브 「토스 이벤트」이 발행 버튼을 기다려요.",
                                     "카카오톡 연결이 3일 뒤 끊겨요 — /kakao 로 다시 연결해 주세요."]}}

    d = Digests(env.db, env.router, tz="Asia/Seoul", export=lambda: Export(), todo=lambda: todo)
    day = datetime.fromtimestamp(env.clock(), ZoneInfo("Asia/Seoul")).date()
    got = d.build(day, "pm", env.clock())
    wait = next(s for s in got["sections"] if s["label"] == "기다리는 일")["sentences"]
    assert sum("안효섭" in s for s in wait) == 1 and sum("토스 이벤트" in s for s in wait) == 1
    assert any("발행을 기다리는 글" in s for s in wait) and any("사진을 기다리는 글" in s for s in wait)
    assert any("카카오톡 연결" in s and "/kakao" not in s for s in wait)
    assert waiting_sentences([{"kind": "answers", "title": "질문 글"}]) == ["답을 기다리는 글 1개(질문 글)가 있어요."]
    assert system_sentences(["핫딜 「x」은 업로드가 실패한 채로 있어요 (/retry 번호)."]) == []


def test_digest_news_are_natural_sentences_merged_by_kind(env: Env) -> None:
    for i in range(3):
        env.open(f"collector:x{i}", "collector_stuck", iid="sys-dealbot", short="⛔[멈춤] 뽐뿌 수집 멈춤", title="뽐뿌 수집 멈춤")
    env.open("threads_replies:1", "threads_replies", iid="sns-replies", short="스레드 답글 2개", title="스레드 답글 2개")
    d = Digests(env.db, env.router, tz="Asia/Seoul")
    day = datetime.fromtimestamp(env.clock(), ZoneInfo("Asia/Seoul")).date()
    news = next(s for s in d.build(day, "am", env.clock() + 1)["sections"] if s["label"] == "소식")["sentences"]
    assert news == ["뽐뿌 수집 멈춤 같은 소식이 3번 있었어요.", "스레드 답글 2개 소식이 있었어요."]
    assert not any("알림이 있었어요" in s for s in news)


# ---------------------------------------------------------------- 사진 · 기록
def test_png_upload_loses_its_icc_profile() -> None:
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 20, 30)).save(buf, "PNG", icc_profile=icc)
    out, ext, *_rest = Uploads._photo(buf.getvalue())
    assert ext == "png" and "icc_profile" not in Image.open(io.BytesIO(out)).info
    p = Image.new("P", (800, 600))
    buf = io.BytesIO()
    p.save(buf, "PNG", transparency=0, icc_profile=icc)
    out, *_rest = Uploads._photo(buf.getvalue())
    info = Image.open(io.BytesIO(out)).info
    assert "icc_profile" not in info and "transparency" in info  # 투명색은 그대로


async def test_failed_logins_and_oauth_starts_show_in_recent_actions(bot: DealBot) -> None:
    from test_dashboard_webapp import post_login

    res = await post_login(bot.web, password="wrong-guess")
    assert res[0] == 200
    bot.threads_auth_link = lambda: _coro("")  # type: ignore[method-assign]
    await bot.web.dispatch("GET", "/dashboard/connect/threads", session_headers(bot))
    lines = [x["text"] for x in bot.dash_state.audit_lines(20)]  # type: ignore[union-attr]
    assert any("로그인 실패" in t for t in lines), lines
    assert any("스레드" in t and "다시 연결 시작" in t for t in lines), lines
    assert not any("wrong-guess" in t for t in lines)


async def _coro(v: str) -> str:
    return v


# ---------------------------------------------------------------- 화면 글
async def test_screen_text_rules(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    data = fixture("export_v2.json")
    deadline = (datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(hours=2)).replace(second=0, microsecond=0)
    data["posts"]["finance"][0]["deadline"] = deadline.isoformat()
    write_export(tmp_path / "blogauto.json", posts=data["posts"])
    blogauto_status(settings, kakao_state="down")
    bot.router.open({"src": "dealbot", "key": "publish_broken", "kind": "publish_broken", "iid": "sys-dealbot", "sev": "stop",  # type: ignore[union-attr]
                     "title": "⛔ 딜봇 채널 발행 막힘", "body": "채널에 글을 못 올리고 있어요 → 대시보드 연결에서 확인", "at": time.time()})
    _s, st, _h = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    rows = {it["iid"]: it for it in st["todo"]}
    sys_row = rows["sys-dealbot"]
    assert sys_row["status"] == "채널에 글을 못 올리고 있어요"
    assert sys_row["primary"]["input"] == "goto" and sys_row["primary"]["goto"] == "#/i/sys-dealbot"
    ask = rows["post-finance-12"]
    assert ask["expires_at"] and ask["status"].startswith("질문 1개 · ") and ask["status"].endswith(f"{deadline:%H:%M}까지")
    labels = [a["label"] for a in st["sns"]["actions"]]
    assert len(labels) == len(set(labels))  # 같은 이름 두 번 X
    kc = next(a for a in st["connect"]["kakao"]["actions"] if a["action"] == "blog.kakao_connect")
    assert kc["placeholder"] == "카카오에서 돌아온 주소" and "붙여넣어" in kc["hint"]
    bd = st["connect"]["bots"]["dealbot"]
    assert "version" in bd and "updated" in bd and "·" not in bd["updated"]


def test_stats_unknown_goal_is_said_once_in_haeyo() -> None:
    from datetime import date

    from dealbot.dashboard.model import CardView, GoalRow, explain
    from dealbot.dashboard.render import card_html

    rows = [GoalRow(label="구독자 수", value=None, goal=1000, unit="명")]
    v = CardView(key="telegram", title="텔레그램", type="telegram", kind="channel", icon="", program="채널 목표", rows=rows)
    v.note = explain(v.program, rows)
    html = card_html(v, token="", today=date(2026, 10, 18), tz="Asia/Seoul", spark_days=14, app=True)
    assert "판단 불가" not in html and "확인할 수 없어" not in html and "아직 읽은 값이 없어요" in html
    old = card_html(v, token="t", today=date(2026, 10, 18), tz="Asia/Seoul", spark_days=14)
    assert "판단 불가" in old  # 예전 한 장짜리 화면은 그대로
