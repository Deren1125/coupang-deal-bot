"""대시보드 주인 10/21 — Pick 비슷한 아이템 '제품 테스트'. 네트워크 없이.

Blog-Auto 기록 칸 product_trial (계약 export_v2.json — Blog-Auto 와 같은 파일) 에 테스트 중인 글이 있을 때만 블로그 › Pick 에
'제품 테스트 N편 →' → 화면(처음 열 때만 받는 product.js): 맨 위 '배운 기준' · 글마다 착장 아이템 → 후보 카드(사진·이름·판매처·
가격/날짜·열기) ✅ 괜찮아 / ❌ 별로(이유 칩 + 한 줄) · 글 전체 한 줄 · 테스트 끝 → Blog-Auto 다리 일 product_feedback
(계약 action_product_feedback.json). 확인 창 없음 (글 본문은 바뀌지 않음) · sw sb-v26."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import shell, webapp
from dealbot.dashboard.actions import BLOG_ACTIONS, ActionError, clean_args, needs_confirm
from dealbot.dashboard.blogauto import PT_REASONS, parse_v2
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export


def test_clean_args_match_the_contract_fixture() -> None:
    want = fixture("action_product_feedback.json")
    assert want["action"] == "product_feedback" and want["target"] == "post-issue-41" and want["confirmed"] is False
    assert "product_feedback" in BLOG_ACTIONS and "product_feedback" in fixture("status_blogauto.json")["actions"]
    a = want["args"]
    out = clean_args("blog.product_feedback", dict(a))
    assert out == a and list(out) == list(a)
    assert not needs_confirm("blog.product_feedback", a)
    assert clean_args("blog.product_feedback", {"pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "ok", "reasons": []}) == \
        {"blog": "issue", "pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "ok"}
    assert clean_args("blog.product_feedback", {"pid": 41, "verdict": "done", "note": "  같은 브랜드   공식몰 우선 "}) == \
        {"blog": "issue", "pid": 41, "verdict": "done", "note": "같은 브랜드 공식몰 우선"}
    assert clean_args("blog.product_feedback", {"pid": 41, "verdict": "note", "note": "x"})["verdict"] == "note"
    for bad in ({"pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "done"}, {"pid": 41, "verdict": "ok"},
                {"pid": 41, "item_id": "pt-xyz", "verdict": "ok"}, {"pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "bad", "reasons": ["몰라"]},
                {"pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "ok", "reasons": ["색 다름"]},
                {"pid": 41, "item_id": "pt-5e6f7a8b", "verdict": "bad", "reasons": "색 다름"},
                {"pid": 41, "verdict": "done", "note": "가" * 201}, {"item_id": "pt-5e6f7a8b", "verdict": "ok"}, {"pid": True, "verdict": "done"}):
        with pytest.raises(ActionError):
            clean_args("blog.product_feedback", bad)
    assert PT_REASONS == tuple(fixture("export_v2.json")["product_trial"]["reasons"])  # Blog-Auto 와 같은 이유 목록


async def test_feedback_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    want = fixture("action_product_feedback.json")
    status, body = await act(bot, "blog.product_feedback", dict(want["args"]), session_headers(bot))
    assert status == 202, body  # 확인 창 없음
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    assert f["args"] == want["args"] and list(f["args"]) == list(want["args"])


def test_product_trial_field_is_optional_and_checked() -> None:
    data = fixture("export_v2.json")
    pt = parse_v2(data)["product_trial"]
    assert pt["ok"] == 3 and pt["goal"] == 20 and pt["learned"][0].startswith("가격") and list(pt["reasons"]) == list(PT_REASONS)
    post = pt["posts"][0]
    assert post["pid"] == 41 and [p["part"] for p in post["pieces"]] == ["아우터", "신발"]
    a, b = post["items"]
    assert a["img"] == "pt-41-1a2b3c4d-9f8e7d6c.jpg" and "verdict" not in a and b["img"] is None and b["verdict"] == "bad"
    assert set(a) == {"id", "piece", "name", "shop", "price", "at", "img", "link", "query"}
    del data["product_trial"]
    assert parse_v2(data)["product_trial"] is None  # 예전 Blog-Auto
    data = fixture("export_v2.json")
    items = data["product_trial"]["posts"][0]["items"]
    items[0]["img"] = "../../etc/passwd"
    items[0]["piece"] = 9
    items[1]["link"] = "javascript:alert(1)"
    items.append({"id": "nope", "name": "x", "link": "https://a.com/1"})
    got = parse_v2(data)["product_trial"]["posts"][0]["items"]
    assert len(got) == 1 and got[0]["img"] is None and got[0]["piece"] == 0
    data["product_trial"]["posts"][0]["items"] = []
    assert parse_v2(data)["product_trial"]["posts"] == []  # 후보가 없는 글은 빠짐


async def test_page_only_when_posts_and_supported(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    pt = (await state(bot))["ptrial"]
    assert pt and pt["ok"] == 3 and pt["goal"] == 20 and pt["learned"] and len(pt["reasons"]) == 10
    post = pt["posts"][0]
    assert post["act"]["action"] == "blog.product_feedback" and post["act"]["args"] == {"blog": "issue", "pid": 41}
    x = post["items"][0]
    assert x["img"] == "/dashboard/thumb/pt-41-1a2b3c4d-9f8e7d6c.jpg" and x["link"].startswith("https://")
    assert x["act"]["args"] == {"blog": "issue", "pid": 41, "item_id": "pt-1a2b3c4d"} and not x["act"]["confirm"]
    # 테스트 중인 글이 없으면 · 예전 Blog-Auto(product_feedback 을 모름)면 숨김
    empty = copy.deepcopy(fixture("export_v2.json")["product_trial"])
    empty["posts"] = []
    write_export(tmp_path / "blogauto.json", product_trial=empty)
    assert (await state(bot))["ptrial"] is None
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "product_feedback"])
    assert (await state(bot))["ptrial"] is None


async def test_shell_loads_product_js_lazily(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    res = await bot.web.dispatch("GET", "/dashboard/static/product.js", {})
    assert res[0] == 200 and res[2].startswith("text/javascript")
    html = shell.page({"v": 1})
    assert '<meta name="sb-product" content="/dashboard/static/product.js?v=' in html
    assert html.count("<script src=") == 1  # product.js 는 처음 열 때만


def test_app_js_page_and_product_js_flow() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    pj = (webapp.STATIC_DIR / "product.js").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "const V = 'sb-v30'" in sw  # 10/21 merge post-edit (sb-v30)
    assert "/^(pics|search|trial|ptrial|sched|threads|x)$/" in js and "key === 'ptrial'" in js and "meta[name=sb-product]" in js
    assert "key === 'issue' && S.ptrial" in js and "제품 테스트 ${S.ptrial.posts.length}편" in js
    for word in ("배운 기준", "✅ 괜찮아", "❌ 별로", "열기", "테스트 끝", "한 줄 보내기", "별로 보내기", "W.SBProduct = {view, photo}",
                 "extra.reasons", "verdict: v", "'done'", "'note'"):
        assert word in pj, word
    assert "innerHTML" not in pj and "https://" not in pj and "eval(" not in pj


def test_trial_post_read_view_keeps_text_and_only_https_links() -> None:
    """'글 보기' (주인 10/23 — 어느 글인지 보며 검수): 본문 여러 줄 그대로 · https 주소만 · 둘 다 없으면 없음."""
    from dealbot.dashboard.blogauto import _read

    assert _read({"url": "https://blog.naver.com/derenpick/1", "text": "첫 줄\n\n📷 사진 1"}) == {
        "url": "https://blog.naver.com/derenpick/1", "text": "첫 줄\n\n📷 사진 1"}
    assert _read({"url": "javascript:alert(1)", "text": "본문"}) == {"url": "", "text": "본문"}
    assert _read({"url": "", "text": ""}) is None and _read(None) is None
