"""대시보드 주인 10/21 — '소재 넣기' · 인기 검색어 '급상승' 칩. 네트워크 없이.

1) 소재 넣기: 블로그 탭마다 글 칸 + 보내기 → Blog-Auto 다리 일 topic_seed {blog, text} (계약 tests/fixtures/dash_contract/
   action_topic_seed.json — Blog-Auto 와 같은 파일). 카페 인기글 주제·아이디어를 주인이 붙여 넣는다 — Blog-Auto 는 카페를 열지 않고
   주소는 지운다. 확인 창 없음 (다음 글감의 첫 후보로 적어 두기만) · 예전 Blog-Auto 면 칸 없음.
2) 인기 검색어 카드: 출처 early = '급상승' · 선택 칸 badge '⚡ 막 뜨는 중' 칩 · sw sb-v24."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, session_headers
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import BLOG_ACTIONS, SEED_MAX, ActionError, clean_args, target_of
from dealbot.dashboard.blogauto import TREND_SOURCES, _trends
from test_dash_api_actions import act
from test_dash_notes import bot  # noqa: F401 — 픽스처
from test_dash_state import state, write_export


def test_clean_args_match_the_contract_fixture() -> None:
    want = fixture("action_topic_seed.json")
    assert want["action"] == "topic_seed" and want["target"] == "blog-issue" and want["confirmed"] is False
    assert "topic_seed" in BLOG_ACTIONS and "topic_seed" in fixture("status_blogauto.json")["actions"]
    a = want["args"]
    out = clean_args("blog.topic_seed", {"blog": "issue", "text": f"  {a['text']} "})
    assert out == a and list(out) == list(a)
    assert target_of("blog.topic_seed", out) == "blog-issue"
    for bad in ({"blog": "nope", "text": "x"}, {"blog": "issue", "text": ""}, {"blog": "issue"},
                {"blog": "finance", "text": "가" * (SEED_MAX + 1)}, {"text": "소재"}):
        with pytest.raises(ActionError):
            clean_args("blog.topic_seed", bad)


async def test_seed_reaches_the_spool_like_the_contract_fixture(bot: DealBot, settings: Settings, tmp_path: Path) -> None:  # noqa: F811
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    want = fixture("action_topic_seed.json")
    status, body = await act(bot, "blog.topic_seed", {"blog": "issue", "text": want["args"]["text"]}, session_headers(bot))
    assert status == 202, body  # 확인 창 없음
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{body['id']}.json")
    assert set(f) == set(want)
    assert {k: f[k] for k in ("v", "action", "blog", "target", "confirmed")} == \
        {k: want[k] for k in ("v", "action", "blog", "target", "confirmed")}
    assert f["args"] == want["args"] and list(f["args"]) == list(want["args"])


async def test_seed_box_on_each_blog_view_and_hidden_for_old_blogauto(bot: DealBot, settings: Settings,  # noqa: F811
                                                                      tmp_path: Path) -> None:
    write_export(tmp_path / "blogauto.json")
    blogauto_status(settings)
    body = await state(bot)
    for blog in ("finance", "issue", "hotdeal"):
        got = [a for a in body["blogs"][blog]["actions"] if a["action"] == "blog.topic_seed"]
        assert len(got) == 1 and got[0]["label"] == "소재 넣기" and got[0]["args"] == {"blog": blog}
        assert got[0]["input"] == "text" and not got[0]["confirm"] and "뉴스·공식 자료" in got[0]["placeholder"]
    blogauto_status(settings, actions=[a for a in fixture("status_blogauto.json")["actions"] if a != "topic_seed"])
    body = await state(bot)
    assert not any(a["action"] == "blog.topic_seed" for b in ("finance", "issue", "hotdeal") for a in body["blogs"][b]["actions"])


def test_trend_early_source_and_badge_chip() -> None:
    assert "early" in TREND_SOURCES
    got = _trends({"at": "2026-10-21T14:00:00+09:00", "items": [
        {"keyword": "흑백요리사2", "rank": 1, "sources": ["early", "news"], "badge": "⚡ 막 뜨는 중"},
        {"keyword": "육영수", "rank": 2, "sources": ["google"], "badge": 3},
        {"keyword": "긴 표시", "rank": 3, "sources": ["early"], "badge": "가" * 50}]})
    a, b, c = got["items"]
    assert a["sources"] == ["news", "early"] and a["badge"] == "⚡ 막 뜨는 중"
    assert "badge" not in b and len(c["badge"]) <= 20  # 선택 칸 — 글자가 아니면 없음 · 한 줄 20자
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "early: '급상승'" in js and "x.badge ? el('span', {class: 'cp hot'}, x.badge)" in js
    # 블로그 탭 소재 넣기 칸: 늘 펼친 글 칸(fixed) + 보내기 · 더보기 시트에는 넣지 않음
    assert "a.action === 'blog.topic_seed'" in js and "a !== seed" in js and "'소재 넣기'" in js
    assert "panel(s, seed, pk(s.iid, seed), {fixed: true})" in js
    sw = (webapp.STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    assert "const V = 'sb-v28'" in sw  # 10/21 merge post-edit: 발행 글 고치기 + 사진 테스트 + 제품 테스트 (sb-v28)
