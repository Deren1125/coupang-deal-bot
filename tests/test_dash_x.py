"""대시보드 X(트위터) 화면 (주인 10/25 — 'X 는 유료 구독만, API 는 안 써'). 네트워크 없이.

1) 진행률: 주인이 주마다 넣는 숫자 셋 x.stats (0 이상 정수 · 인증 ≤ 전체) → 봇 DB kv dash_x_stats (최근 60번) · 인증 팔로워 n/500 ·
   90일 인증 노출 n/500,000 · 지금 속도면 예상 달성일 (마지막 두 기록 직선). 계정 이름 x.handles (비어도 됨).
2) 초안함: Blog-Auto 기록 파일 x 칸 (계약 export_v2.json — Blog-Auto 와 같은 파일) · 칸이 없으면 초안 없음 ·
   올렸어요·버리기·다시 쓰기 = 다리 일 blog.x_posted · x_drop · x_redo {pid} (계약 action_x_posted.json · 확인 창 없음).
3) xpost.js: 처음 열 때만 (meta sb-xpost — 스레드 화면의 X 봇 x.js 와 다른 파일) · 크기 예산 · 바깥 주소 없음 (X 글쓰기 주소는 state.x.intent) · 블로그 탭 'X(트위터) →'.
4) 링크 답글 (주인 10/26 새 기준 — 블로그 주소는 본문 말고 첫 답글): 초안마다 reply (엄격히 거름 · 끝 줄 = 글 주소) · 주인이 넣은 X 글 주소의
   번호로 답글 쓰기 화면 (state.x.reply_intent — 자동 게시 없음) · 스레드 첫 댓글이 두 번 실패한 할 일은 그 글을 여는 단추.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import shell, webapp, xview
from dealbot.dashboard.actions import (
    BLOG_ACTIONS,
    DEAL_ACTIONS,
    X_STATS_KEEP,
    X_STATS_KEY,
    ActionError,
    clean_args,
    needs_confirm,
    target_of,
    x_stats_rows,
)
from dealbot.dashboard.blogauto import _x
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

STATIC = webapp.STATIC_DIR
TZ = ZoneInfo("Asia/Seoul")


# ---------------------------------------------------------------- 다리 일 (Blog-Auto 와 같은 계약)
def test_draft_actions_match_the_contract_fixture() -> None:
    want = fixture("action_x_posted.json")
    assert want["action"] == "x_posted" and want["confirmed"] is False and want["blog"] is None
    for name in ("x_posted", "x_drop", "x_redo"):
        assert name in BLOG_ACTIONS and name in fixture("status_blogauto.json")["actions"]
        out = clean_args(f"blog.{name}", {"pid": want["args"]["pid"], "blog": "issue", "extra": 1})
        assert out == want["args"] and not needs_confirm(f"blog.{name}", out)
        assert target_of(f"blog.{name}", out) == want["target"]
        for bad in ({}, {"pid": 0}, {"pid": True}, {"pid": "abc"}, {"pid": 10**10}):
            with pytest.raises(ActionError):
                clean_args(f"blog.{name}", bad)


async def test_posted_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    want = fixture("action_x_posted.json")
    status, body = await act(bot, "blog.x_posted", {"pid": want["args"]["pid"]}, session_headers(bot))
    assert status == 202, body  # 확인 창 없음
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed", "args")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed", "args")}
    # 예전 Blog-Auto (x_* 를 모름) → 맡기지 않음
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if not a.startswith("x_")])
    status, body = await act(bot, "blog.x_drop", {"pid": 131}, session_headers(bot))
    assert status == 503 and body["code"] == "unsupported"


# ---------------------------------------------------------------- 진행률 숫자 (x.stats · x.handles)
def test_stats_and_handles_args_are_strict() -> None:
    assert "x.stats" in DEAL_ACTIONS and "x.handles" in DEAL_ACTIONS and not needs_confirm("x.stats", {})
    good = {"followers": 1200, "verified": 87, "impressions_90d": 154000}
    assert clean_args("x.stats", {**good, "x": 1}) == good and clean_args("x.stats", {k: str(v) for k, v in good.items()}) == good
    assert target_of("x.stats", good) == "x"
    for bad in ({**good, "verified": -1}, {**good, "followers": True}, {**good, "impressions_90d": 10**13}, {**good, "verified": 1201},
                {"followers": 1, "verified": 0}, {**good, "followers": 1.5}, {**good, "followers": "1,200"}):
        with pytest.raises(ActionError):
            clean_args("x.stats", bad)
    assert clean_args("x.handles", {"pick": "@derenpick", "revenue": ""}) == {"pick": "derenpick", "revenue": ""}
    assert clean_args("x.handles", {}) == {"pick": "", "revenue": ""}
    for bad in ({"pick": "데런픽"}, {"pick": "a b"}, {"revenue": "x" * 16}, {"pick": 3}):
        with pytest.raises(ActionError):
            clean_args("x.handles", bad)


async def test_stats_are_saved_and_shown_with_an_eta(bot: DealBot, settings: Settings, tmp_path: Path, monkeypatch) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    st = await state(bot)
    x = st["x"]
    assert x["progress"]["verified"] is None and x["progress"]["eta_verified"] == "" and x["progress"]["note"]
    # 할 일 'X 숫자 넣기' (주인 10/25 '알림 띄워 주라') — X 를 안 쓰면 없음 · 계정 아이디를 넣고 숫자를 안 넣었으면 뜨고,
    # 넣으면 빠지고, 7일 지나면 다시
    assert "x-stats" not in {it["iid"] for it in st["todo"]}
    status, _ = await act(bot, "x.handles", {"pick": "deren_pick", "revenue": ""}, h)
    assert status == 200
    todo = {it["iid"]: it for it in (await state(bot))["todo"]}
    assert todo["x-stats"]["primary"]["goto"] == "#/blog/x" and "크리에이터 스튜디오" in todo["x-stats"]["status"]
    status, body = await act(bot, "x.stats", {"followers": 1000, "verified": 100, "impressions_90d": 100000}, h)
    assert status == 200 and body["status"] == "ok", body
    assert "x-stats" not in {it["iid"] for it in (await state(bot))["todo"]}
    rows = x_stats_rows(bot.db)
    stale = [{**rows[-1], "at": time.time() - 8 * 86400}]  # 화면은 지금 시각으로 봄 — 8일 전에 넣었으면 다시 할 일
    bot.db.kv_set(X_STATS_KEY, json.dumps(stale))
    assert "x-stats" in {it["iid"] for it in (await state(bot))["todo"]}
    rows[-1]["at"] -= 7 * 86400  # 일주일 전에 넣은 것으로
    bot.db.kv_set(X_STATS_KEY, json.dumps(rows))
    status, body = await act(bot, "x.stats", {"followers": 1100, "verified": 150, "impressions_90d": 150000}, h)
    assert status == 200, body
    p = (await state(bot))["x"]["progress"]
    assert (p["verified"], p["verified_goal"], p["impr"], p["impr_goal"], p["followers"]) == (150, 500, 150000, 500000, 1100)
    assert p["eta_verified"].endswith("쯤") and p["eta_impr"].endswith("쯤") and p["note"] == ""
    status, body = await act(bot, "x.stats", {"followers": 10, "verified": 11, "impressions_90d": 0}, h)
    assert status == 400 and "인증 팔로워" in body["text"]
    bot.db.kv_set(X_STATS_KEY, json.dumps([{"at": 1.0 + i, "followers": 1, "verified": 0, "impressions_90d": 0}
                                           for i in range(X_STATS_KEEP + 3)] + [{"at": 9.0, "followers": -1}]))  # 틀린 줄은 버림
    status, _ = await act(bot, "x.stats", {"followers": 2, "verified": 1, "impressions_90d": 5}, h)
    rows = x_stats_rows(bot.db)
    assert status == 200 and len(rows) == X_STATS_KEEP and rows[-1]["impressions_90d"] == 5  # 최근 60번만
    status, _ = await act(bot, "x.handles", {"pick": "derenpick", "revenue": ""}, h)
    assert status == 200 and (await state(bot))["x"]["handles"] == {"pick": "derenpick", "revenue": ""}


def test_eta_is_linear_from_the_last_two_entries() -> None:
    day = 86400.0
    t0 = 1_790_000_000.0
    rows = [{"at": t0, "verified": 100}, {"at": t0 + 7 * day, "verified": 200}]
    got = xview.eta(rows, "verified", 500, TZ)  # 일주일에 100 → 300 남음 = 21일 뒤
    from datetime import datetime

    want = datetime.fromtimestamp(t0 + 28 * day, TZ)
    assert got.endswith("쯤") and f"{want.month}/{want.day}" in got
    assert xview.eta(rows[:1], "verified", 500, TZ) == ""
    assert xview.eta([*rows, {"at": t0 + 8 * day, "verified": 200}], "verified", 500, TZ) == "늘지 않음"
    assert xview.eta([{"at": t0, "verified": 600}], "verified", 500, TZ) == "달성"
    assert xview.eta([{"at": t0, "verified": 1}, {"at": t0 + day, "verified": 2}], "verified", 500_000, TZ) == "3년 넘게"


# ---------------------------------------------------------------- 초안함 (Blog-Auto x 칸)
# 링크 답글 (주인 10/26 새 기준): 계약 파일 export_v2.json 은 두 저장소가 같아야 하는데 Blog-Auto 쪽에는 아직 x.drafts[].reply 가 없다 →
# 이 시험은 기록 파일에 답글을 넣어서 쓴다 (계약 파일은 그대로 · Blog-Auto x_drafts 가 reply 를 보내기 시작하면 두 계약 파일에 같이 넣기)
X_REPLIES = ["🔗 변우석 새 드라마 첫 촬영 현장 공개\nhttps://blog.naver.com/derenpick/224000000131",
             "링크를 통해 수익의 일부를 지급받을 수 있어요\n블로그 정리글\n장원영 공항패션 니트 브랜드 정보\nhttps://blog.naver.com/derenpick/224000000128"]


def _x_with_replies() -> dict:
    fx = fixture("export_v2.json")["x"]
    fx["drafts"] = [dict(d, reply=d.get("reply", r)) for d, r in zip(fx["drafts"], X_REPLIES, strict=True)]
    return fx


async def test_state_has_drafts_posted_ideas_and_the_intent_link(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    fx = _x_with_replies()
    write_export(tmp_path / "blogauto.json", x=fx)
    blogauto_status(settings)
    x = (await state(bot))["x"]
    assert x["ready"] and x["bridge_ok"] and x["limit"] == 280 and x["url_len"] == 23
    assert x["intent"] == "https://x.com/intent/post?text="  # 무료 글쓰기 화면 링크 (API 아님)
    assert x["reply_intent"] == "https://x.com/intent/tweet?in_reply_to="  # 링크 답글 쓰기 화면 (주인이 넣은 X 글 번호 + &text= 는 화면이)
    assert [d["pid"] for d in x["drafts"]] == [131] and [d["pid"] for d in x["posted"]] == [128]
    d = x["drafts"][0]
    assert d["text"] == fx["drafts"][0]["text"] and d["text"].endswith(d["url"])
    assert d["reply"] == fx["drafts"][0]["reply"] and d["reply"].split("\n")[-1] == d["url"]
    p = x["posted"][0]  # 제휴 글: 답글 첫 줄 고지 · 끝 줄 주소 (올린 글에도 — '올렸어요'를 먼저 눌러도 답글은 달 수 있게)
    assert p["reply"] == fx["drafts"][1]["reply"] and p["reply"].split("\n")[0] == "링크를 통해 수익의 일부를 지급받을 수 있어요"
    assert [d[k]["action"] for k in ("posted_act", "drop", "redo")] == ["blog.x_posted", "blog.x_drop", "blog.x_redo"]
    assert all(d[k]["args"] == {"pid": 131} and not d[k]["confirm"] for k in ("posted_act", "drop", "redo"))
    assert x["posted"][0]["shop"] is True and "posted_act" not in x["posted"][0]
    assert [c["id"] for c in x["ideas"]] == ["week", "visits", "deals"] and x["idea_note"] == "직접 쓰는 글 — 숫자는 실제 값만"
    assert "두 계정에 같은 글 금지" in x["rules"] and "자동 좋아요" in x["rules"]
    assert x["save"]["action"] == "x.stats" and x["handles"] == {"pick": "", "revenue": ""}
    # 예전 Blog-Auto: 다리 일을 모르면 단추는 꺼둠 · 기록 파일에 x 칸이 없으면 초안 없음
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if not a.startswith("x_")])
    assert (await state(bot))["x"]["drafts"][0]["drop"]["disabled"] is True
    write_export(tmp_path / "blogauto.json", x=None)
    x = (await state(bot))["x"]
    assert x["ready"] is False and x["drafts"] == [] and x["posted"] == [] and x["ideas"] == [] and x["empty"]
    data = fixture("export_v2.json")
    del data["x"]
    (tmp_path / "blogauto.json").write_text(json.dumps(dict(data, written_at=json.loads((tmp_path / "blogauto.json").read_text())["written_at"]),
                                                       ensure_ascii=False), encoding="utf-8")
    assert (await state(bot))["x"]["drafts"] == []


def test_export_parsing_tolerates_missing_and_bad_rows() -> None:
    assert _x(None) is None and _x({}) is None and _x({"drafts": "x"}) is None
    fx = copy.deepcopy(fixture("export_v2.json")["x"])
    fx["drafts"] += [{"pid": 1, "status": "weird", "text": "t"}, {"pid": "x", "status": "draft", "text": "t"},
                     {"pid": 2, "status": "draft", "text": ""}, "junk",
                     {"pid": 3, "status": "draft", "text": "글\x00자", "url": "https://evil.example/x", "shop": "yes"}]
    fx["ideas"] = [{"title": ""}, "junk", {"title": "카드", "facts": ["a", 3, None], "prompt": None}]
    got = _x(fx)
    assert [d["pid"] for d in got["drafts"]] == [131, 128, 3]
    bad = got["drafts"][2]
    assert bad["url"] == "" and bad["shop"] is False and "\x00" not in bad["text"]  # 글 주소는 네이버 블로그만
    assert [c["title"] for c in got["ideas"]] == ["카드"] and got["ideas"][0]["facts"] == ["a"] and got["ideas"][0]["prompt"] == ""
    assert _x({"drafts": []}) == {"per_day": 0, "limit": 280, "url_len": 23, "drafts": [], "ideas": [], "plan": None}


def test_reply_is_parsed_strictly() -> None:
    """첫 답글 글자: 글자만 · 줄은 그대로 (줄마다 제어 문자를 빼고 공백 정리 · 빈 줄은 뺌) · 끝 줄은 이 초안의 블로그 주소 · 8줄·500자 안.
    아니면 '' — 새 기준 전 초안(칸 없음)도 '' (화면은 답글 칸을 숨김)."""
    base = fixture("export_v2.json")["x"]["drafts"][0]
    url = base["url"]

    def one(reply: object, **kw: object) -> str:
        return _x({"drafts": [dict(base, reply=reply, **kw)]})["drafts"][0]["reply"]

    assert one(f"🔗 제목\r\n\n  {url}  ") == f"🔗 제목\n{url}"
    assert one(f"블로그\x00 정리글\x1b\n제목   둘\n{url}") == f"블로그 정리글\n제목 둘\n{url}"
    for bad in (None, 3, ["a"], {"t": url}, "", "제목만", f"{url}\n제목", f"제목\n{url}x", "제목\nhttps://blog.naver.com/other/1",
                "\n".join(["줄"] * 8 + [url]), "가" * 480 + "\n" + url, "가" * 1100 + "\n" + url):
        assert one(bad) == "", bad
    assert one(f"제목\n{url}", url="https://evil.example/x") == ""  # 글 주소가 네이버 블로그가 아니면 답글도 없음
    assert _x({"drafts": [{k: v for k, v in base.items() if k != "reply"}]})["drafts"][0]["reply"] == ""


async def test_two_week_plan_is_fetched_only_when_the_screen_opens(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    """주인 10/26 '2주 계획이 X 탭에서 안 보여' — 첫 상태 값엔 글 수만 (104 KB 예산), 계획은 GET /dashboard/api/xplan."""
    from dash_helpers import get_json

    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    x = (await state(bot))["x"]
    fx = fixture("export_v2.json")["x"]["plan"]
    assert x["plan_n"] == 3 and "plan" not in x
    status, plan, _ = await get_json(bot, "/dashboard/api/xplan", session_headers(bot))
    assert status == 200 and [d["date"] for d in plan["days"]] == ["2026-10-27", "2026-10-28"]
    assert plan["days"][0]["posts"][0] == fx["days"][0]["posts"][0] and plan["replies"] == fx["replies"] and plan["rules"] == fx["rules"]
    status, _p, _ = await get_json(bot, "/dashboard/api/xplan", {})
    assert status in (401, 302, 303)  # 로그인 아래
    write_export(tmp_path / "blogauto.json", x=dict(fixture("export_v2.json")["x"], plan=None))
    assert (await state(bot))["x"]["plan_n"] == 0
    status, _p, _ = await get_json(bot, "/dashboard/api/xplan", session_headers(bot))
    assert status == 404


def test_plan_parsing_drops_bad_days_posts_and_replies() -> None:
    fx = copy.deepcopy(fixture("export_v2.json")["x"])
    fx["plan"]["days"] += [{"date": "10/29", "posts": [{"time": "08:00", "head": "a"}]}, "junk",
                           {"date": "2026-10-30", "posts": [{"time": "8시", "head": "a"}, {"time": "09:10", "head": ""}]},
                           {"date": "2026-10-31", "posts": [{"time": "09:10", "head": "글\x00자", "outline": None}]}]
    fx["plan"]["replies"] += ["junk", {"for": "x", "text": ""}]
    got = _x(fx)["plan"]
    assert [d["date"] for d in got["days"]] == ["2026-10-27", "2026-10-28", "2026-10-31"] and got["end"] == "2026-10-31"
    assert got["days"][2]["posts"] == [{"time": "09:10", "head": "글자", "outline": "", "attach": ""}]  # 제어 문자 빠짐
    assert len(got["replies"]) == 2
    assert _x(dict(fx, plan={"days": []}))["plan"] is None and _x(dict(fx, plan="x"))["plan"] is None


# ---------------------------------------------------------------- x.js
def test_xpost_js_lazy_load_budget_and_safety() -> None:
    js = (STATIC / "xpost.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "eval(" not in js and "new Function" not in js and "innerHTML" not in js
    for word in ("인증 팔로워", "90일 인증 노출", "전체 팔로워", "지금 속도면", "X에 올리기", "올렸어요", "버리기", "다시 쓰기", "복사",
                 "x.idea_note", "x.intent + encodeURIComponent(", "rel: 'noopener'", "target: '_blank'",
                 "/dashboard/api/xplan", "X에 쓰기", "댓글 틀", "try { return localStorage",
                 "링크 답글 달기", "답글 복사", "x.reply_intent + i + '&text=' + encodeURIComponent(d.reply)", "d.reply ? reply(x, d) : null"):
        assert word in js, word
    for label in re.findall(r"btn\('[^']*', '([^']*)'", js):
        assert re.search(r"[가-힣]", label), label
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "lazy('meta[name=sb-xpost]', 'xpost', 'SBXP')" in app and "'#/blog/x'" in app and "'X(트위터) →'" in app
    assert "/^(pics|search|trial|ptrial|sched|threads|x|lookbook)$/" in app
    html = shell.page({"me": {}})
    assert re.search(r'<meta name="sb-xpost" content="/dashboard/static/xpost\.js\?v=[0-9a-f]{10}">', html)
    assert html.count("<script src=") == 1  # xpost.js 는 처음 열 때만
    assert "xpost.js" in __import__("dealbot.dashboard.api", fromlist=["x"]).APP_FILES


def test_xpost_js_syntax_and_x_length_count() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 없음")
    assert subprocess.run([node, "--check", str(STATIC / "xpost.js")], capture_output=True).returncode == 0
    code = ("global.document={};global.window={};require(process.argv[1]);const f=window.SBXP.xlen;"
            "console.log(JSON.stringify([f('abc',23),f('가나',23),f('가 https://blog.naver.com/a/1?x=1',23)]))")
    out = subprocess.run([node, "-e", code, str(STATIC / "xpost.js")], capture_output=True, text=True)
    assert json.loads(out.stdout) == [3, 4, 26]  # Blog-Auto x_drafts.x_len 과 같은 셈 (한글 2 · 링크 23)
    # 링크 답글: 주인이 넣은 X 글 주소 → 글 번호 (x.com·twitter.com/아이디/status/숫자만 — 아니면 '' 이고 '링크 답글 달기'는 주소 없이 흐림)
    urls = ["https://x.com/deren_pick/status/1850123456789012345?s=46&t=ab", " twitter.com/a/status/12/ ", "https://mobile.twitter.com/a_b/status/9#m",
            "https://X.com/i/web/status/77", "https://x.com/deren/status/abc", "https://evil.com/x.com/a/status/1", "https://x.com.evil.com/a/status/1",
            "x.com/a/status/", "https://x.com/thisnameistoolong1/status/1", "https://x.com/a/status/1x", "https://x.com/a/likes/1", "", None]
    code = ("global.document={};global.window={};require(process.argv[1]);"
            "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(window.SBXP.sid)))")
    out = subprocess.run([node, "-e", code, str(STATIC / "xpost.js"), json.dumps(urls)], capture_output=True, text=True)
    assert json.loads(out.stdout) == ["1850123456789012345", "12", "9", "77"] + [""] * 9


# ---------------------------------------------------------------- 스레드 첫 댓글(블로그 링크) 실패 할 일 (Blog-Auto threads_api)
async def test_threads_reply_fail_todo_opens_the_threads_post(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    """두 번 실패하면 Blog-Auto 가 notify.queue (키 sns:threads_reply:<pid> · 종류 threads_reply_fail · iid sns-status · links [스레드 글]).
    할 일 줄의 첫 단추 = '스레드 글 열기' (그 글 주소) · 본문에 댓글로 달 글자 · 급하지 않음 (정리에만) · 닫기는 Blog-Auto (주인 댓글이 보이면)
    라 '다 했어요' 없이 '알림 없애기'만."""
    from dealbot.notify import ingest
    from dealbot.notify.router import urgency

    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    h = session_headers(bot)
    bot.router.open({"src": "dealbot", "key": "sns_zero", "kind": "sns_zero", "iid": "sns-status", "title": "SNS 0개", "at": time.time()})  # type: ignore[union-attr]
    it = {i["iid"]: i for i in (await state(bot))["todo"]}["sns-status"]
    assert it["primary"]["action"] == "goto" and it["primary"]["goto"] == "#/connect"  # 링크 없는 SNS 줄은 예전 그대로
    await act(bot, "todo.dismiss", {"iid": "sns-status", "key": "sns_zero"}, h)
    link = "https://www.threads.net/@derenpick/post/DAbc123xyz"
    n = ingest.clean_open(dict(fixture("notice_open_links.json"), key="sns:threads_reply:131", kind="threads_reply_fail", iid="sns-status",
                               urgent=False, title="스레드 Pick #131 블로그 링크 댓글 실패", short="스레드 Pick #131 댓글 실패",
                               body="댓글로 달아 주세요: 🔗 변우석 새 드라마 첫 촬영 현장 공개 / blog.naver.com/derenpick/224000000131",
                               links=[{"label": "스레드 글", "url": link}]))
    assert n is not None and n["iid"] == "sns-status" and n["links"] == [{"label": "스레드 글", "url": link}]
    assert urgency("threads_reply_fail") == "DIGEST"
    bot.router.open(dict(n, at=time.time()))  # type: ignore[union-attr]
    it = {i["iid"]: i for i in (await state(bot))["todo"]}["sns-status"]
    assert (it["kind"], it["primary"]["input"], it["primary"]["url"], it["primary"]["label"]) == ("threads_reply_fail", "open", link, "스레드 글 열기")
    assert "blog.naver.com/derenpick/224000000131" in it["detail"]["lines"][0] and not it["urgent"]
    assert [a["action"] for a in it["more"]] == ["todo.hide"]


async def test_threads_reply_fail_link_survives_a_dealbot_sns_row_in_the_same_card(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    """같은 sns-status 칸에 딜봇 줄(sns_zero — sev todo, 먼저 열림)이 남아 있어도 첫 단추는 '스레드 글 열기'.
    Blog-Auto 는 threads_reply_fail 을 sev 'info'(DIGEST)로 보내서 정렬하면 딜봇 줄이 앞에 온다."""
    from dealbot.notify import ingest

    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    bot.router.open({"src": "dealbot", "key": "sns_zero", "kind": "sns_zero", "iid": "sns-status", "sev": "todo",  # type: ignore[union-attr]
                     "title": "SNS 0개", "body": "오늘 SNS 글이 0개예요", "at": time.time() - 3600})
    link = "https://www.threads.net/@derenpick/post/DAbc123xyz"
    n = ingest.clean_open(dict(fixture("notice_open_links.json"), key="sns:threads_reply:131", kind="threads_reply_fail", iid="sns-status",
                               urgent=False, sev="info", title="스레드 Pick #131 블로그 링크 댓글 실패", short="스레드 Pick #131 댓글 실패",
                               body="댓글로 달아 주세요: 🔗 변우석 새 드라마 첫 촬영 현장 공개 / blog.naver.com/derenpick/224000000131",
                               links=[{"label": "스레드 글", "url": link}]))
    assert n is not None and n["sev"] == "info"
    bot.router.open(dict(n, at=time.time()))  # type: ignore[union-attr]
    it = {i["iid"]: i for i in (await state(bot))["todo"]}["sns-status"]
    assert (it["primary"]["input"], it["primary"]["url"], it["primary"]["label"]) == ("open", link, "스레드 글 열기")
    assert any("blog.naver.com/derenpick/224000000131" in ln for ln in it["detail"]["lines"])  # 붙여 넣을 글자도 같은 칸에
