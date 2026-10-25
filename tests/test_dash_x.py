"""대시보드 X(트위터) 화면 (주인 10/25 — 'X 는 유료 구독만, API 는 안 써'). 네트워크 없이.

1) 진행률: 주인이 주마다 넣는 숫자 셋 x.stats (0 이상 정수 · 인증 ≤ 전체) → 봇 DB kv dash_x_stats (최근 60번) · 인증 팔로워 n/500 ·
   90일 인증 노출 n/500,000 · 지금 속도면 예상 달성일 (마지막 두 기록 직선). 계정 이름 x.handles (비어도 됨).
2) 초안함: Blog-Auto 기록 파일 x 칸 (계약 export_v2.json — Blog-Auto 와 같은 파일) · 칸이 없으면 초안 없음 ·
   올렸어요·버리기·다시 쓰기 = 다리 일 blog.x_posted · x_drop · x_redo {pid} (계약 action_x_posted.json · 확인 창 없음).
3) xpost.js: 처음 열 때만 (meta sb-xpost — 스레드 화면의 X 봇 x.js 와 다른 파일) · 크기 예산 · 바깥 주소 없음 (X 글쓰기 주소는 state.x.intent) · 블로그 탭 'X(트위터) →'.
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
async def test_state_has_drafts_posted_ideas_and_the_intent_link(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    x = (await state(bot))["x"]
    fx = fixture("export_v2.json")["x"]
    assert x["ready"] and x["bridge_ok"] and x["limit"] == 280 and x["url_len"] == 23
    assert x["intent"] == "https://x.com/intent/post?text="  # 무료 글쓰기 화면 링크 (API 아님)
    assert [d["pid"] for d in x["drafts"]] == [131] and [d["pid"] for d in x["posted"]] == [128]
    d = x["drafts"][0]
    assert d["text"] == fx["drafts"][0]["text"] and d["text"].endswith(d["url"])
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
    assert _x({"drafts": []}) == {"per_day": 0, "limit": 280, "url_len": 23, "drafts": [], "ideas": []}


# ---------------------------------------------------------------- x.js
def test_xpost_js_lazy_load_budget_and_safety() -> None:
    js = (STATIC / "xpost.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "eval(" not in js and "new Function" not in js and "innerHTML" not in js
    for word in ("인증 팔로워", "90일 인증 노출", "전체 팔로워", "지금 속도면", "X에 올리기", "올렸어요", "버리기", "다시 쓰기", "복사",
                 "x.idea_note", "x.intent + encodeURIComponent(", "rel: 'noopener'", "target: '_blank'"):
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
