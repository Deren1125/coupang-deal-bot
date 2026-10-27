"""'오늘 한 일' (주인 10/18 — "오늘 내가 발행하도록 허가한 글 모음은 어디서 봐? 사진·링크 첨부한 거라거나"):
GET /dashboard/api/today — 칸별(링크·사진·발행·답·버림·기타) · 새것 먼저 · 제목은 대기열·기록 파일에서 · 결과 칩 ·
링크는 정해진 곳(네이버 글 주소·채널 글·상품 페이지)에서만 · 비밀값·args·내부 번호 없음 · 로그인 필요 · state 는 개수만."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, get_json, push_settings, queued, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import today as todaymod
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import TEXTS
from test_dash_api_actions import act
from test_dash_state import write_export

PICK_URL = "https://m.blog.naver.com/derenpick/224000000001"  # 기록 파일 recent.issue pid 40


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    b.notifier.channel_post_base = "https://t.me/hot_deal_and_info"
    yield b
    b.db.close()


def put(bot: DealBot, action: str, target: str = "", args: dict | None = None, *, status: str = "ok",
        result: dict | None = None, ago: float = 0.0) -> str:
    ts = time.time() - ago
    aid = spool.new_id("a", ts)
    bot.actions_out.store.insert(aid=aid, ts=ts, blog=None, action=action, target=target, args=args or {}, status=status,
                                 cid=None, session="s1", result=result)
    return aid


def audit(bot: DealBot, action: str, target: str, code: str, ago: float = 0.0) -> None:
    bot.dashboard.api.audit(session8="s1", client8="c1", action=action, target=target, args={}, code=code, action_id=None)  # type: ignore[union-attr]
    if ago:
        with bot.db._lock:
            bot.db._conn.execute("UPDATE dash_audit SET ts = ts - ? WHERE id = (SELECT MAX(id) FROM dash_audit)", (ago,))


async def today(bot: DealBot, q: str = "") -> dict:
    status, body, headers = await get_json(bot, "/dashboard/api/today" + q, session_headers(bot))
    assert status == 200 and headers["Cache-Control"] == "no-store", body
    return body


def rows_of(body: dict) -> dict[str, list[dict]]:
    return {g["label"]: g["rows"] for g in body["groups"]}


# ---------------------------------------------------------------- 칸 나누기
def test_group_of_every_kind() -> None:
    g = todaymod.group_of
    assert g("deal.link", {}) == "link"
    assert g("deal.late_photo", {}) == g("blog.photos", {}) == "photo"
    assert g("blog.publish", {}) == g("blog.schedule", {}) == g("deal.ok", {}) == g("deal.repost", {}) == "publish"
    assert g("blog.answer", {}) == g("blog.fix", {}) == g("blog.oedit_keep", {}) == "answer"
    assert g("blog.drop", {}) == g("deal.skip", {}) == g("blog.noshare", {}) == "drop"
    assert g("deal.nolink", {"name": "post"}) == "publish" and g("deal.nolink", {"name": "skip"}) == "drop"
    assert g("todo.dismiss", {}) == g("bot.pause", {}) == g("blog.write", {}) == "other"
    for quiet in ("blog.info", "push.test", "stats.refresh", "sns.token", "sns.code", "blog.kakao_connect", "upload", "login",
                  "blog.dismiss"):
        assert g(quiet, {}) is None, quiet


async def test_groups_in_order_newest_first_and_quiet_actions_left_out(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    d1, d2 = queued(bot, name="다이슨 V12 무선청소기"), queued(bot, product_id="toss:2", name="삼다수 2L 12병")
    put(bot, "deal.link", f"deal-{d1.id}", {"qid": d1.id, "url": "https://link.coupang.com/a/x"}, result={"code": "linked"}, ago=50)
    put(bot, "deal.link", f"deal-{d2.id}", {"qid": d2.id, "url": "https://link.coupang.com/a/y"}, result={"code": "linked"}, ago=10)
    put(bot, "blog.photos", "post-issue-46", {"blog": "issue", "pid": 46, "uploads": ["a.jpg", "b.jpg"]}, status="running", ago=40)
    put(bot, "blog.publish", "post-issue-40", {"blog": "issue", "pid": 40}, result={"code": "scheduled", "text": "오늘 15:40 발행 예정이에요"}, ago=30)
    put(bot, "blog.publish", "post-issue-48", {"blog": "issue", "pid": 48}, result={"code": "published", "text": "발행했어요"}, ago=31)
    put(bot, "blog.answer", "post-finance-12", {"blog": "finance", "pid": 12, "answers": {"1": "어제"}}, ago=25)
    put(bot, "deal.skip", f"deal-{d2.id}", {"qid": d2.id}, result={"code": "skipped"}, ago=20)
    put(bot, "todo.dismiss", "sns-replies", {"iid": "sns-replies", "key": "threads_replies:"}, result={"code": "done", "text": "다 했어요"}, ago=15)
    put(bot, "blog.dismiss", "sns-replies", {"key": "x"}, status="queued", ago=15)  # todo.dismiss 가 맡긴 것 — 두 번 세지 않음
    for quiet in ("push.test", "blog.info", "stats.refresh"):
        put(bot, quiet, "", {}, ago=5)
    body = await today(bot)
    assert [g["label"] for g in body["groups"]] == ["링크 넣음", "사진 올림", "발행·예약", "답함·고침", "버림·안 올림", "기타"]
    assert body["count"] == 8 and sum(len(g["rows"]) for g in body["groups"]) == 8
    r = rows_of(body)
    assert [x["title"] for x in r["링크 넣음"]] == ["삼다수 2L 12병", "다이슨 V12 무선청소기"]  # 새것 먼저
    assert r["사진 올림"][0]["what"] == "사진 2장 올리기" and r["사진 올림"][0]["blog"] == "Pick"
    assert r["기타"][0]["title"] == "다 했어요" and "note" not in r["기타"][0]
    assert r["발행·예약"][0]["note"] == "오늘 15:40 발행 예정이에요"  # 발행 칸만 결과 글 (예약 시각 같은 것)
    assert "note" not in r["발행·예약"][1]  # '발행 · 발행했어요' 처럼 겹치는 글은 뺌
    assert all(len(x["when"]) == 5 and x["when"][2] == ":" for g in body["groups"] for x in g["rows"])
    st = (await get_json(bot, "/dashboard/api/state", session_headers(bot)))[1]
    assert st["counts"]["done_today"] == 8


async def test_yesterday_is_separate_and_empty_today_is_empty(bot: DealBot) -> None:
    start = todaymod.day_start(time.time(), bot.settings.app.timezone)
    put(bot, "bot.pause", "", {}, result={"code": "paused"}, ago=time.time() - start + 3600)
    body = await today(bot)
    assert body["count"] == 0 and body["groups"] == [] and body["back"] == 0
    y = await today(bot, "?day=yesterday")
    assert y["count"] == 1 and y["back"] == 1 and rows_of(y)["기타"][0]["title"] == "딜봇 멈춤"
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "한 일이 아직 없어요" in js and "'/dashboard/api/today'" in js and "?day=yesterday" in js


# ---------------------------------------------------------------- 제목
async def test_titles_come_from_queue_and_export_with_safe_fallbacks(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    d = queued(bot, name="필립스 전동칫솔 소닉케어")
    put(bot, "deal.ok", f"deal-{d.id}", {"qid": d.id}, result={"code": "approved"}, ago=60)
    put(bot, "deal.ok", "deal-999999", {"qid": 999999}, result={"code": "approved"}, ago=59)  # 대기열에 없음 → 일 이름
    put(bot, "blog.fix", "post-issue-46", {"blog": "issue", "pid": 46, "text": "둘째 문단 짧게"}, ago=50)  # posts[] 제목
    put(bot, "blog.publish", "post-hotdeal-41", {"blog": "hotdeal", "pid": 41}, ago=40)  # recent[] 제목
    put(bot, "blog.drop", "post-finance-77", {"blog": "finance", "pid": 77}, ago=35)  # 기록에 없음
    put(bot, "blog.write", "", {"text": "주인이 적은 글감 원문"}, result={"code": "saved", "post": {"blog": "issue", "pid": 47}}, ago=30)
    put(bot, "blog.schedule", "", {"blog": "issue", "pids": [46, 47, 48], "at": [1.0, 2.0, 3.0]}, ago=20)
    put(bot, "blog.visit_done", "visit-issue-12", {"id": "visit-issue-12"}, ago=10)
    r = rows_of(await today(bot))
    assert [x["title"] for x in r["발행·예약"]] == ["안효섭 준지 26FW 공항패션 외 2편", "SUNGOD 헤어 드라이기 29,900원", "올리기",
                                                  "필립스 전동칫솔 소닉케어"]
    assert r["답함·고침"][0]["title"] == "안효섭 준지 26FW 공항패션" and r["답함·고침"][0]["blog"] == "Pick"
    assert r["버림·안 올림"][0]["title"] == "버리기" and r["버림·안 올림"][0]["blog"] == "아카이브"
    assert [x["title"] for x in r["기타"]] == ["가을 트렌치코트 코디 세 가지", "차은우 가을 니트 코디"]


# ---------------------------------------------------------------- 결과 칩
async def test_result_chips_ok_running_failed_with_short_reason(bot: DealBot) -> None:
    put(bot, "blog.publish", "post-issue-46", {"blog": "issue", "pid": 46}, result={"code": "published"}, ago=60)
    put(bot, "blog.publish", "post-issue-47", {"blog": "issue", "pid": 47}, status="queued", ago=50)
    put(bot, "blog.publish", "post-issue-48", {"blog": "issue", "pid": 48}, status="unknown", ago=45)
    put(bot, "blog.retry", "post-hotdeal-7", {"blog": "hotdeal", "pid": 7}, status="error",
        result={"code": "login_needed", "text": "네이버 로그인이 필요해요 — 연결에서 확인해 주세요 아주 길게 이어지는 설명 문장 끝"}, ago=40)
    put(bot, "blog.drop", "post-issue-52", {"blog": "issue", "pid": 52}, status="expired", result={"code": "expired", "text": ""}, ago=30)
    audit(bot, "blog.publish", "post-issue-31", "blogauto_down", ago=20)  # 맡기기 전에 막힘 (dash_actions 없음)
    audit(bot, "blog.photos", "post-issue-46", "queued", ago=19)  # 맡긴 줄의 짝 (action_id 가 없어도 성공 코드는 빼고)
    r = rows_of(await today(bot))
    pub = r["발행·예약"]
    assert [(x["res"], x["chip"]) for x in pub] == [("bad", "실패"), ("bad", "실패"), ("run", "확인 필요"), ("run", "진행 중"),
                                                   ("ok", "완료")]
    assert pub[0]["why"] == TEXTS["blogauto_down"]
    assert pub[1]["why"] == "네이버 로그인이 필요해요"  # 앞 마디만
    assert r["버림·안 올림"][0]["why"] == "시간이 지나 취소됐어요"
    assert "why" not in pub[4] and "사진 올림" not in r


async def test_real_dashboard_link_shows_up_done_with_the_photo_flag(bot: DealBot) -> None:
    from test_dash_api_actions import photo_upload

    item = queued(bot, name="락앤락 밀폐용기 20종", url="https://smartstore.naver.com/x/products/333")
    h = session_headers(bot)
    up = photo_upload(bot, h)
    status, body = await act(bot, "deal.link", {"qid": item.id, "url": "https://link.coupang.com/a/abc", "upload_id": up}, h)
    assert status == 200, body
    [x] = rows_of(await today(bot))["링크 넣음"]
    assert x["title"] == "락앤락 밀폐용기 20종" and x["what"] == "링크 넣기 · 사진 같이"
    assert (x["res"], x["chip"]) == (("ok", "완료") if body["status"] == "ok" else ("bad", "실패"))
    assert x["link"] == {"label": "상품", "url": "https://smartstore.naver.com/x/products/333"}


# ---------------------------------------------------------------- 링크 (정해진 곳에서만)
async def test_links_only_from_export_channel_or_product_page(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    recent = {"issue": [{"pid": 40, "title": "제니 공항패션", "url": PICK_URL, "at": "2026-10-18T09:10:00"},
                        {"pid": 44, "title": "이상한 주소 글", "url": "https://evil.example/p/1", "at": "2026-10-18T09:12:00"}],
              "finance": [], "hotdeal": []}
    write_export(tmp_path / "blogauto.json", recent=recent)
    blogauto_status(settings)
    posted = queued(bot, status="published", product_id="toss:10", name="에어팟 프로 2세대")
    bot.db.record_post(posted.deal, channel_id="@hot_deal_and_info", message_id=1201, dry_run=False)
    linked = queued(bot, product_id="toss:11", name="코렐 접시", url="https://service.toss.im/shopping-share?p=11")
    tg = queued(bot, product_id="toss:12", name="텔레그램 주소 딜", url="https://t.me/somewhere/1")
    put(bot, "blog.publish", "post-issue-40", {"blog": "issue", "pid": 40}, result={"code": "published"}, ago=60)
    put(bot, "blog.publish", "post-issue-44", {"blog": "issue", "pid": 44}, result={"code": "published"}, ago=55)
    put(bot, "blog.publish", "post-issue-40", {"blog": "issue", "pid": 40}, status="queued", ago=50)  # 아직 → 링크 없음
    put(bot, "deal.ok", f"deal-{posted.id}", {"qid": posted.id}, result={"code": "approved"}, ago=40)
    put(bot, "deal.posted", f"deal-{linked.id}", {"qid": linked.id, "url": "https://evil.example/x"}, result={"code": "posted"}, ago=35)
    put(bot, "deal.link", f"deal-{linked.id}", {"qid": linked.id, "url": "https://link.coupang.com/a/z"}, result={"code": "linked"}, ago=30)
    put(bot, "deal.link", f"deal-{tg.id}", {"qid": tg.id, "url": "https://link.coupang.com/a/w"}, result={"code": "linked"}, ago=25)
    put(bot, "deal.skip", f"deal-{posted.id}", {"qid": posted.id}, result={"code": "skipped"}, ago=20)
    body = await today(bot)
    r = rows_of(body)
    pub = {(x["title"], x["res"]): x.get("link") for x in r["발행·예약"]}
    assert pub[("제니 공항패션", "ok")] == {"label": "글", "url": PICK_URL}
    assert pub[("제니 공항패션", "run")] is None and pub.get(("이상한 주소 글", "ok")) is None
    assert pub[("에어팟 프로 2세대", "ok")] == {"label": "채널 글", "url": "https://t.me/hot_deal_and_info/1201"}
    assert pub[("코렐 접시", "ok")] is None  # 발행 칸엔 상품 페이지를 달지 않음 · args 의 주소는 쓰지 않음
    links = {x["title"]: x.get("link") for x in r["링크 넣음"]}
    assert links == {"코렐 접시": {"label": "상품", "url": "https://service.toss.im/shopping-share?p=11"}, "텔레그램 주소 딜": None}
    assert "link" not in r["버림·안 올림"][0]
    raw = json.dumps(body, ensure_ascii=False)
    assert "evil.example" not in raw and "link.coupang.com" not in raw


async def test_dry_run_channel_posts_are_not_linked(bot: DealBot) -> None:
    it = queued(bot, status="published", product_id="toss:20", name="연습 딜")
    bot.db.record_post(it.deal, channel_id="@hot_deal_and_info", message_id=77, dry_run=True)
    put(bot, "deal.ok", f"deal-{it.id}", {"qid": it.id}, result={"code": "approved"})
    assert "link" not in rows_of(await today(bot))["발행·예약"][0]


# ---------------------------------------------------------------- 비밀값·내부 번호 없음
async def test_no_secrets_args_or_internal_ids(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    d = queued(bot, name="비밀 없는 딜")
    ids = [put(bot, "sns.token", "", {"sns": "threads", "token": "IGQVJ-very-secret-token"}),
           put(bot, "blog.kakao_connect", "conn-kakao", {"url": "https://localhost/?code=secretcode123"}),
           put(bot, "deal.ok_text", f"deal-{d.id}", {"qid": d.id, "text": "주인이 고친 본문 원문"}, result={"code": "approved"}),
           put(bot, "deal.link", f"deal-{d.id}", {"qid": d.id, "url": "https://link.coupang.com/a/q", "upload_id": "f" * 32},
               result={"code": "linked", "text": "링크를 넣었어요"}),
           put(bot, "blog.answer", "post-finance-12", {"blog": "finance", "pid": 12, "answers": {"1": "주인 답 원문"}})]
    body = await today(bot)
    raw = json.dumps(body, ensure_ascii=False)
    assert body["count"] == 3
    for bad in ("IGQVJ", "secretcode", "고친 본문 원문", "주인 답 원문", "f" * 32, "link.coupang", "deal-", "post-finance", "qid",
                "pid", f"#{d.id}", *ids):
        assert bad not in raw, bad
    keys = {k for g in body["groups"] for x in g["rows"] for k in x}
    assert keys <= {"title", "at", "when", "what", "res", "chip", "why", "note", "blog", "link"}


# ---------------------------------------------------------------- 로그인 · 앱
async def test_today_needs_login_and_is_rate_limited(bot: DealBot) -> None:
    res = await bot.web.dispatch("GET", "/dashboard/api/today", {})
    assert res[0] == 401 and json.loads(res[1])["code"] == "login"
    res = await bot.web.dispatch("GET", "/dashboard/api/today", {"cookie": "dash_session=not-a-real-session"})
    assert res[0] == 401
    h = session_headers(bot)
    codes = [(await get_json(bot, "/dashboard/api/today", h))[0] for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


def test_app_has_the_todo_row_and_the_connect_entry_points_to_the_same_sheet() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "오늘 한 일 ${" in js and "fold('오늘 한 일', todaySheet)" in js and js.count("doneRow()") == 1  # 할 일 탭 '더 보기' 안 (10/27)
    assert "fold('최근 한 일'" not in js  # 연결 탭의 예전 줄 → 같은 시트 (접속 기록은 시트 아래에)
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert int(sw.split("'sb-v", 1)[1].split("'", 1)[0]) >= 8
