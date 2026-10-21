"""대시보드 주인 10/20 (3) — Pick 패션 글 '비슷한 아이템 링크 넣기'. 네트워크 없이.

에이블리 크리에이터 링크는 앱에서만 만들어져 주인이 붙여 넣는다: 패션 글(Blog-Auto posts[].similar 칸이 있는 글 — 임시저장·예약)에
'비슷한 아이템 링크 넣기' → 글 칸에 한 줄에 하나 '이름 | 링크' (이름은 선택) → Blog-Auto 다리 일 similar_links {blog, pid, links, notes?}
(계약 tests/fixtures/dash_contract/action_similar_links.json — Blog-Auto 와 같은 파일). 확인 창 없음 (임시저장만 다시) · sw sb-v23."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import BLOG_ACTIONS, ActionError, clean_args, similar_lines
from dealbot.dashboard.blogauto import parse_v2
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export

TEXT = "베이지 트렌치코트 | https://m.a-bly.com/goods/12345678\n\n  https://applink.a-bly.com/aBcD1234  \n"


def test_clean_args_match_the_contract_fixture() -> None:
    want = fixture("action_similar_links.json")
    assert want["action"] == "similar_links" and want["target"] == "post-issue-41" and want["confirmed"] is False
    assert "similar_links" in BLOG_ACTIONS and "similar_links" in fixture("status_blogauto.json")["actions"]
    a = want["args"]
    out = clean_args("blog.similar_links", {"blog": "issue", "pid": a["pid"], "text": TEXT})  # 화면 글 칸
    assert out == a and list(out) == list(a)
    assert clean_args("blog.similar_links", dict(a)) == a  # links·notes 로 보내도 같은 모양
    plain = clean_args("blog.similar_links", {"pid": 41, "text": "https://m.a-bly.com/goods/1\nhttps://m.a-bly.com/goods/1"})
    assert plain == {"blog": "issue", "pid": 41, "links": ["https://m.a-bly.com/goods/1"]}  # 이름이 없으면 notes 없음 · 같은 주소는 한 번
    assert similar_lines("크림 니트 https://m.a-bly.com/goods/2") == [("https://m.a-bly.com/goods/2", "크림 니트")]
    assert similar_lines("https://m.a-bly.com/goods/3 | 블랙 로퍼") == [("https://m.a-bly.com/goods/3", "블랙 로퍼")]
    for bad in ({"blog": "hotdeal", "pid": 41, "text": TEXT}, {"pid": 41, "text": ""}, {"pid": 41, "text": "이름만 있어요"},
                {"pid": 41, "text": "http://m.a-bly.com/goods/1"}, {"pid": 41, "text": "\n".join(f"https://a.com/{i}" for i in range(7))},
                {"text": TEXT}, {"pid": True, "text": TEXT}, {"pid": 41, "links": "https://a.com/1"},
                {"pid": 41, "links": ["https://a.com/1"], "notes": ["a", "b"]}, {"pid": 41, "links": ["javascript:alert(1)"]},
                {"pid": 41, "links": ["https://a.com/" + "x" * 500]}, {"pid": 41, "text": "가" * 4001}):
        with pytest.raises(ActionError):
            clean_args("blog.similar_links", bad)


async def test_links_reach_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")  # Pick 모드를 아는 때 — 간격 두고만 발행이라 바로 공개되지 않음 (확인 창 없음)
    blogauto_status(settings)
    want = fixture("action_similar_links.json")
    h = session_headers(bot)
    status, body = await act(bot, "blog.similar_links", {"blog": "issue", "pid": 41, "text": TEXT}, h)
    assert status == 202, body  # 확인 창 없음 (임시저장만 다시)
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    assert f["args"] == want["args"] and list(f["args"]) == list(want["args"])


def test_similar_field_is_optional_and_checked() -> None:
    data = fixture("export_v2.json")
    rows = {r["pid"]: r for r in parse_v2(data)["posts"]["issue"]}
    assert rows[48]["similar"] == {"n": 3, "ably": 1} and "similar" not in rows[46]
    for bad in ({"n": -1, "ably": 0}, {"n": True, "ably": 0}, "3", {"n": 3}, {"n": 99, "ably": 0}):
        data["posts"]["issue"][2]["similar"] = bad
        assert "similar" not in parse_v2(data)["posts"]["issue"][2]


def pick_posts() -> dict:
    """계약 export 의 Pick 글: 46 임시저장(패션) · 47 예약(패션) · 48 막힌 예약(패션) · 49 연예 글(similar 칸 없음)."""
    posts = copy.deepcopy(fixture("export_v2.json")["posts"])
    rows = posts["issue"]
    rows[0]["similar"] = {"n": 0, "ably": 0}
    rows[1]["similar"] = {"n": 2, "ably": 0}
    ent = dict(copy.deepcopy(rows[0]), pid=49, title="연예 이슈 글", schedule=None, pics=None)
    ent.pop("similar")
    rows.append(ent)
    return posts


def _rows(body: dict) -> dict:
    iss = body["blogs"]["issue"]
    return {x["iid"]: x for x in iss["waiting"] + ((iss.get("schedule") or {}).get("items") or [])}


async def test_button_on_pick_fashion_posts_only(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json", posts=pick_posts())
    blogauto_status(settings)
    rows = _rows(await state(bot))
    for pid in (46, 47, 48):  # 임시저장 · 예약 · 막힌 예약
        got = [a for a in rows[f"post-issue-{pid}"]["more"] if a["action"] == "blog.similar_links"]
        assert len(got) == 1 and got[0]["label"] == "비슷한 아이템 링크 넣기" and got[0]["args"] == {"blog": "issue", "pid": pid}
        assert got[0]["input"] == "text" and not got[0]["confirm"] and "이름 | 링크" in got[0]["placeholder"]
        assert "\n" in got[0]["placeholder"]  # 여러 줄 글 칸 (app.js field: placeholder 에 줄바꿈 → textarea)
    assert not any(a["action"] == "blog.similar_links" for a in rows["post-issue-49"]["more"])  # 연예 글
    body = await state(bot)
    for blog in ("hotdeal", "finance"):
        assert not any(a["action"] == "blog.similar_links" for x in body["blogs"][blog]["waiting"] for a in x["more"])
    # 예전 Blog-Auto (similar_links 를 모름) — 단추 없음
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "similar_links"])
    rows = _rows(await state(bot))
    assert not any(a["action"] == "blog.similar_links" for x in rows.values() for a in x["more"])


def test_sw_bumped_and_textarea_path_in_app_js() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "const V = 'sb-v27'" in sw  # 10/21 merge: 사진 테스트 + 제품 테스트 (sb-v27)
    assert "(a.placeholder || '').includes('\\n')" in js and "else extra.text = v" in js  # 글 칸 그대로 text 로 → 서버가 줄을 나눔
