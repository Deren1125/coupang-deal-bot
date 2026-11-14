"""대시보드 앱 화면 값 (GET /dashboard/api/state, 주인 10/18): 할 일 한 곳 — 봇 DB 줄이 이김 · 한 가지 일은 한 줄(iid) ·
글은 기록 파일 posts[] 의 needs · 줄마다 누를 것 하나 · 텔레그램 말 없음 · 블로그·연결·정리 값."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, get_json, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.storage.db import MAYBE_POSTED


def write_export(path: Path, **changes: object) -> None:
    data = fixture("export_v2.json")
    data["written_at"] = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    data["todo"]["generated_at"] = data["written_at"]
    data.update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def state(bot: DealBot) -> dict:
    status, body, _ = await get_json(bot, "/dashboard/api/state", session_headers(bot))
    assert status == 200
    return body


async def test_todo_merges_deals_notices_and_export_posts(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    link = queued(bot)
    review = queued(bot, status="awaiting_approval", product_id="toss:2")
    unsure = queued(bot, status="failed", error=f"{MAYBE_POSTED}: timeout", product_id="toss:3")
    # 같은 딜에 알림도 있으면 한 줄 (봇 DB 줄이 이김)
    bot.router.open({"src": "dealbot", "key": f"link:{link.id}", "kind": "link_request", "iid": f"deal-{link.id}",  # type: ignore[union-attr]
                     "title": "핫딜 링크", "at": time.time()})
    # 이미 처리된 딜 알림은 줄이 없다
    bot.router.open({"src": "dealbot", "key": "link:999", "kind": "link_request", "iid": "deal-999", "title": "옛 링크",  # type: ignore[union-attr]
                     "at": time.time()})
    # Blog-Auto 알림 (글) + 시스템 알림
    bot.router.open({"src": "blogauto", "key": "draft:issue:31", "kind": "draft_ready", "iid": "post-issue-31",  # type: ignore[union-attr]
                     "title": "Pick #31 발행 대기", "at": time.time()})
    bot.router.open({"src": "dealbot", "key": "publish_broken", "kind": "publish_broken", "iid": "sys-dealbot", "sev": "stop",  # type: ignore[union-attr]
                     "title": "딜봇 채널 발행 막힘", "at": time.time()})
    body = await state(bot)
    todo = body["todo"]
    iids = [it["iid"] for it in todo]
    assert len(iids) == len(set(iids))  # 한 가지 일은 한 줄
    assert iids[0] == "sys-dealbot"  # ⛔ 먼저
    want = {f"deal-{link.id}", f"deal-{review.id}", f"deal-{unsure.id}", "post-issue-31", "post-issue-46", "post-finance-12"}
    assert want <= set(iids) and "deal-999" not in iids
    by = {it["iid"]: it for it in todo}
    deal = by[f"deal-{link.id}"]
    assert deal["primary"]["action"] == "deal.link" and deal["primary"]["input"] == "url" and deal["primary"]["confirm"] is False
    assert [a["action"] for a in deal["more"]][:1] == ["deal.skip"] and deal["more"][0]["confirm"] is True
    assert by[f"deal-{review.id}"]["primary"]["action"] == "deal.ok"
    assert by[f"deal-{unsure.id}"]["primary"]["action"] == "deal.posted"
    pick = by["post-issue-46"]  # 기록 파일 posts[] 의 needs (알림 없어도)
    assert pick["primary"]["action"] == "blog.photos" and pick["primary"]["multiple"] is True
    assert pick["detail"]["photo_request"]["must"] and pick["chip"] == "Pick"
    ask = by["post-finance-12"]
    assert ask["primary"]["action"] == "blog.answer" and ask["status"] == "질문 1개 · 65점"  # 11/10 할 일 카드에도 점수
    # Pick 은 간격 두고만 발행 (주인 10/18): 큰 버튼은 '간격 두고 발행' (확인 없이 줄 끝으로), '지금 발행' 은 없다
    pub = by["post-issue-31"]
    assert pub["primary"]["action"] == "blog.schedule" and pub["primary"]["label"] == "간격 두고 발행"
    assert pub["primary"]["args"] == {"blog": "issue", "pids": [31]} and pub["primary"]["confirm"] is False
    assert "blog.publish" not in [a["action"] for a in pub["more"]]
    # 예약된 글(#47)은 할 일이 아니다 · 차례에 멈춘 글(#48)은 할 일 (이유와 고칠 일)
    assert "post-issue-47" not in iids
    blocked = by["post-issue-48"]
    assert blocked["kind"] == "sched_blocked" and blocked["status"] == "예약 발행 멈춤 · 사진이 아직 없어요"
    assert blocked["primary"]["action"] == "blog.photos" and "blog.unschedule" in [a["action"] for a in blocked["more"]]
    for it in todo:
        text = json.dumps({k: it[k] for k in ("title", "status", "primary", "more")}, ensure_ascii=False)
        assert "텔레그램" not in text and "/ok" not in text and "/skip" not in text, it["iid"]
        assert it["primary"]["label"]
    assert body["counts"]["todo"] == len(todo)


async def test_primary_follows_the_need_order_and_owner_edit_wins(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    data = fixture("export_v2.json")
    data["posts"]["issue"][0]["needs"] = ["publish", "photos", "owner_edit"]
    data["posts"]["issue"][0]["owner_edited"] = True
    write_export(tmp_path / "blogauto.json", posts=data["posts"])
    blogauto_status(settings)
    body = await state(bot)
    it = next(x for x in body["todo"] if x["iid"] == "post-issue-46")
    assert it["primary"]["action"] == "blog.oedit_keep"
    more = [a["action"] for a in it["more"]]
    assert more[:2] == ["blog.photos", "blog.schedule"] and "blog.oedit_redo" in more and "blog.drop" in more
    redo = next(a for a in it["more"] if a["action"] == "blog.oedit_redo")
    assert redo["danger"] is True and redo["confirm"] is True
    assert it["detail"]["owner_edited"] is True


async def test_unsupported_blog_actions_are_hidden_or_disabled(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=["publish", "dismiss"])
    body = await state(bot)
    pick = next(x for x in body["todo"] if x["iid"] == "post-issue-46")
    assert pick["primary"].get("disabled") is True
    assert all(a["action"] in ("blog.publish", "blog.dismiss") or not a["action"].startswith("blog.") for a in pick["more"])


async def test_blogs_connect_and_deal_sections(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    issue = body["blogs"]["issue"]
    assert issue["label"] == "Deren's Pick" and issue["login"] == "ok" and issue["today"] == 2
    assert issue["recent"][0]["url"].startswith("https://m.blog.naver.com/")
    assert {a["action"] for a in issue["actions"]} >= {"blog.issue", "blog.info", "blog.mode", "blog.pause", "blog.drafts_cleanup"}
    fin = body["blogs"]["finance"]
    assert fin["scan"]["items"][0]["action"]["action"] == "blog.pick"
    assert any(a["action"] == "blog.ledger" and a["input"] == "file" for a in fin["actions"])
    conn = body["connect"]
    assert conn["kakao"]["state"] == "ok" and conn["bots"]["blogauto"]["ok"] and conn["bots"]["blogauto"]["bridge"] == 1
    assert set(conn["naver"]) == {"finance", "issue", "hotdeal"}
    assert body["deal"]["status"]["paused"] is False and isinstance(body["deal"]["queue"], list)
    assert body["banner"]["kind"] == "push_off"


async def test_item_endpoint_has_long_fields_and_404_for_done(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    info = queued(bot, status="awaiting_approval", product_id="toss:9", deal_kind="info")
    item = bot.db.get_queue_item(info.id)
    deal = item.deal  # type: ignore[union-attr]
    deal.product.extra["info_draft"] = {"text": "긴 본문 " * 200}
    bot.db.update_queue_item(info.id, status="awaiting_approval", deal=deal)
    h = session_headers(bot)
    status, it, _ = await get_json(bot, f"/dashboard/api/item/deal-{info.id}", h)
    assert status == 200 and it["kind"] == "review_info" and len(it["detail"]["preview"]) > 500
    body = await state(bot)
    short = next(x for x in body["todo"] if x["iid"] == f"deal-{info.id}")
    assert short["detail"]["preview"] is None  # 화면 첫 값에는 긴 글을 넣지 않음
    status, gone, _ = await get_json(bot, "/dashboard/api/item/deal-424242", h)
    assert status == 404 and gone["text"] == "이미 처리됐어요"
    status, _p, _ = await get_json(bot, "/dashboard/api/item/post-issue-46", h)
    assert status == 200


async def test_state_stays_small_with_30_items(bot: DealBot, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    for i in range(30):
        queued(bot, product_id=f"toss:{100 + i}", name=f"상품 이름이 조금 긴 테스트 상품 {i} " * 2)
    body = await state(bot)
    # 10/23 오늘 바뀐 것·제휴 수익·토스 칸이 더해짐 → 96 KB · 10/25 룩북 릴스 칸(완성 편 캡션·룩 목록·단추 셋 + 할 일 한 줄 ≈ 4.4 KB,
    # 올린 편은 한 줄만) → 104 KB
    assert len(body["todo"]) >= 30 and len(json.dumps(body, ensure_ascii=False).encode()) <= 104 * 1024


async def test_digest_endpoint(bot: DealBot) -> None:
    bot.router.store.save_digest("digest-20261018-pm", at=time.time(), title="🌙 저녁 정리", lines=["a."],  # type: ignore[union-attr]
                                 sections=[{"label": "기다리는 일", "sentences": ["a."]}])
    status, d, _ = await get_json(bot, "/dashboard/api/digest/digest-20261018-pm", session_headers(bot))
    assert status == 200 and d["sections"][0]["label"] == "기다리는 일" and set(d) == {"id", "title", "at", "sections"}
    assert (await state(bot))["digest"]["latest"] == "digest-20261018-pm"


def test_connect_tab_shopconnect_row(bot: DealBot) -> None:
    """주인 10/24 '연결 탭에서 실시간으로 쇼핑커넥트 연결 상태': Blog-Auto 상태 → 한 줄 (멈춤 이유·오늘 수·마지막 성공), 로그인 멈춤이면 원격 로그인 단추."""
    from dealbot.dashboard.blogauto import _shopconnect

    now = 1792846800.0
    raw = {"state": "stopped", "reason": "login", "until": now + 3600, "hours": "08:00-23:00", "queued": 2,
           "today": {"searches": 3, "issues": 1}, "limits": {"searches": 16, "issues": 8}, "last_at": now - 60,
           "last_ok_at": now - 7200, "link": "https://naver.me/secret"}
    sc = _shopconnect(raw)
    assert sc is not None and "link" not in sc and sc["queued"] == 2
    row = bot.dash_state._sc_row(sc, now)
    assert row["bad"] and row["text"].startswith("멈춤 — 쇼핑커넥트 계정(다시 보는 드라마 블로그) 로그인 필요 · ") and "오늘 링크 1/8 · 검색 3/16" in row["text"]
    assert "대기 2" in row["text"] and "마지막 성공" in row["text"]
    assert row["action"]["action"] == "login.remote" and row["action"]["args"] == {"target": "finance"}
    ok = bot.dash_state._sc_row(_shopconnect(dict(raw, state="ok", reason="", until=None, queued=0)), now)
    assert ok["text"].startswith("정상 · ") and not ok["bad"] and "action" not in ok
    assert bot.dash_state._sc_row(_shopconnect({"state": "off"}), now) == {"text": "꺼짐", "bad": False}
    assert _shopconnect({"state": "weird"}) is None and bot.dash_state._sc_row(None, now) is None


# ---------------------------------------------------------------- 가짜·좀비 할 일 (주인 11/14 — Pick #340 '사진 필요'가 본문을 고친 뒤에도 남음)
def _notice(bot: DealBot, key: str, kind: str, iid: str, *, ago_s: float = 0, ttl_h: float = 24, **kw: object) -> None:
    bot.router.store.upsert_open({"src": "blogauto", "key": key, "kind": kind, "iid": iid, "title": f"{kind} {iid}", "ttl_h": ttl_h, **kw},  # type: ignore[union-attr]
                                 time.time() - ago_s)


def _open(bot: DealBot, key: str) -> bool:
    n = bot.router.store.get("blogauto", key)  # type: ignore[union-attr]
    return n is not None and n.resolved_at is None


async def test_export_needs_win_over_old_need_notices(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """기록 파일이 그 글을 알고 (알림보다 나중에 쓰였고) 할 일이 없다고 하면, 예전 '사진·답' 알림만으로는 카드가 안 생기고 그 알림은 닫힌다.
    기록 파일보다 나중에 온 알림은 그대로 (기록 파일이 아직 모르는 새 일)."""
    posts = fixture("export_v2.json")["posts"]
    for p in posts["issue"]:
        if p["pid"] == 47:
            p.pop("schedule", None)  # 예약되지 않은 기다리는 글 (예약된 글은 원래 할 일에 안 나옴)
    write_export(tmp_path / "blogauto.json", posts=posts)  # issue 47: needs=[] (saved) · issue 46: needs=['photos']
    blogauto_status(settings)
    _notice(bot, "pick:issue:47", "pick_photos", "post-issue-47", ago_s=3600)
    _notice(bot, "hot:issue:47", "hot", "post-issue-47", ago_s=1800, sev="hot")
    _notice(bot, "pick:issue:46", "pick_photos", "post-issue-46", ago_s=3600)
    by = {i["iid"]: i for i in (await state(bot))["todo"]}
    assert "post-issue-47" not in by  # 사진 자리가 다 찬 글 — '사진 필요' 카드 없음
    assert not _open(bot, "pick:issue:47") and not _open(bot, "hot:issue:47")  # 그 알림은 닫음 (다시 열리면 다시 봄)
    assert by["post-issue-46"]["detail"]["needs"] == ["photos"] and _open(bot, "pick:issue:46")  # 진짜 사진 필요는 그대로
    # 발행 대기 알림이 같이 있으면 카드는 남되 할 일은 기록 파일 것만 (사진 필요가 붙지 않음)
    _notice(bot, "pickb:issue:47", "pick_photos", "post-issue-47", ago_s=3600)
    _notice(bot, "draft:issue:47", "draft_ready", "post-issue-47", ago_s=3600)
    by = {i["iid"]: i for i in (await state(bot))["todo"]}
    assert "photos" not in by["post-issue-47"]["detail"]["needs"] and "사진" not in by["post-issue-47"]["status"]
    assert _open(bot, "pickb:issue:47")  # 섞여 있으면 닫지 않음
    # 기록 파일이 쓰인 뒤에 온 알림 — 기록 파일이 아직 모름 → 알림대로 '사진 필요'
    bot.router.store.resolve("blogauto", ["pickb:issue:47", "draft:issue:47"], time.time())  # type: ignore[union-attr]
    bot.router.store.upsert_open({"src": "blogauto", "key": "pick2:issue:47", "kind": "pick_photos", "iid": "post-issue-47", "title": "사진 골라 주세요"},  # type: ignore[union-attr]
                                 time.time() + 5)
    bot.dash_state._now = lambda: datetime.now(ZoneInfo("Asia/Seoul")) + timedelta(seconds=10)  # type: ignore[method-assign]
    by = {i["iid"]: i for i in bot.dash_state.items()}
    assert by["post-issue-47"]["detail"]["needs"] == ["photos"] and by["post-issue-47"]["status"].startswith("사진 필요")
    # 기록 파일에 없는 글은 예전처럼 알림에서 (posts 가 잘려 못 실린 글)
    _notice(bot, "pick:issue:900", "pick_photos", "post-issue-900", ago_s=600)
    by = {i["iid"]: i for i in bot.dash_state.items()}
    assert by["post-issue-900"]["detail"]["needs"] == ["photos"]


async def test_zombie_draft_ready_and_reminder_kinds_make_no_card(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    """글이 기록 파일에도 오늘 할 일에도 없는데 24시간 넘게 열려 있는 '발행 대기' 알림은 숨김 (이미 올렸거나 버린 글의 남은 알림) ·
    키자드 등록 도구 알림은 리마인더만 (할 일 줄 없음)."""
    st = fixture("export_v2.json")["status"]
    rt = {"day": datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat(), "slots": [], "items": [
        {"id": "pub:issue:602", "slot": "s1", "group": "발행", "blog": "issue", "title": "오늘 할 일에 있는 글", "copy": [], "links": []}]}
    write_export(tmp_path / "blogauto.json", status={**st, "routine": rt})
    blogauto_status(settings)
    _notice(bot, "draft:issue:600", "draft_ready", "post-issue-600", ago_s=5 * 86400, ttl_h=240)  # 좀비
    _notice(bot, "draft:issue:601", "draft_ready", "post-issue-601", ago_s=2 * 3600, ttl_h=240)   # 방금 것 — 기록 파일이 아직 모를 수 있음
    _notice(bot, "draft:issue:602", "draft_ready", "post-issue-602", ago_s=5 * 86400, ttl_h=240)  # 오래됐어도 오늘 할 일 줄에 있음
    _notice(bot, "ask:issue:603", "ask", "post-issue-603", ago_s=5 * 86400, ttl_h=240)            # 답을 기다리는 일은 숨기지 않음
    _notice(bot, "kzt:1", "keyzard_tool", "sys-blogauto", ago_s=60)
    _notice(bot, "kz:issue", "keyzard_login", "conn-naver-issue", ago_s=60)
    ids = {i["iid"] for i in (await state(bot))["todo"]}
    assert "post-issue-600" not in ids and {"post-issue-601", "post-issue-602", "post-issue-603"} <= ids
    assert "sys-blogauto" not in ids and "conn-naver-issue" not in ids
    assert _open(bot, "draft:issue:600")  # 숨기기만 (알림 줄은 건드리지 않음 — 글이 다시 실리면 다시 보임)
    # 기록 파일을 못 읽으면 (블로그 프로그램이 멈춤·파일 없음) 아무것도 숨기지 않음 — 모르는 것을 없다고 하지 않게
    (tmp_path / "blogauto.json").unlink()
    assert "post-issue-600" in {i["iid"] for i in bot.dash_state.items()}
    from dealbot.dashboard import state as state_mod

    assert state_mod.PUSH_ONLY_KINDS == ("keyzard_login", "keyzard_tool") and state_mod.ZOMBIE_S == 86400
