"""드라마·라커룸 탭 '인기 키워드' (주인 11/14 10:1x "드라마랑 라커룸은 왜 인기 키워드가 안 떠"):
계약 export_v2.json channel_trends {finance, hotdeal} → 새 채널이 켜진 블로그 화면에 Pick 과 같은 카드 · 누르면 다리 일
channel_topic {blog, keyword} (Blog-Auto 가 다음 빈 예약 자리에 한 편 — 붙여넣기 글이라 확인 없음)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dash_helpers import blogauto_status, fixture, push_settings
from dealbot.app import DealBot
from dealbot.bridge import spool
from dealbot.config import Settings
from dealbot.dashboard import webapp
from dealbot.dashboard.actions import ActionError, clean_args, needs_confirm, target_of
from dealbot.dashboard.blogauto import _channel_trends, parse_v2
from test_dash_api_actions import act
from test_dash_state import state, write_export


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _export(tmp_path: Path, channel: bool = True, **changes: object) -> None:
    data = fixture("export_v2.json")
    base = {k: v for k, v in data["status"]["blogs"]["issue"].items() if k != "name"}
    if channel:
        data["status"]["blogs"]["hotdeal"] = {**base, "name": "오늘의 라커룸", "short": "라커룸", "channel": "sports", "blog_off": True}
        data["status"]["blogs"]["finance"] = {**base, "name": "다시 보는 드라마", "short": "드라마", "channel": "drama"}
    write_export(tmp_path / "blogauto.json", status=data["status"], **changes)


def test_parse_keeps_contract_and_drops_bad_rows() -> None:
    fx = fixture("export_v2.json")["channel_trends"]
    got = parse_v2(fixture("export_v2.json"))["channel_trends"]
    assert set(got) == {"finance", "hotdeal"} and got["finance"]["kind"] == "drama"
    assert got["finance"]["items"][0] == fx["finance"]["items"][0]
    assert [x["keyword"] for x in got["hotdeal"]["items"]] == [x["keyword"] for x in fx["hotdeal"]["items"]]
    bad = _channel_trends({"issue": {"items": [{"keyword": "x", "rank": 1}]}, "finance": {"at": "x", "kind": "politics", "items": [
        {"keyword": "a" * 80, "rank": 2, "sources": ["onair", "instagram"], "change": True, "tag": "가" * 40},
        {"keyword": "", "rank": 1}, "junk", {"keyword": "정년이", "rank": 1, "written": "yes"}]}})
    assert set(bad) == {"finance"} and bad["finance"]["kind"] == ""
    rows = bad["finance"]["items"]
    assert rows[0]["keyword"] == "정년이" and len(rows[1]["keyword"]) == 40 and rows[0]["written"] is False
    assert rows[1]["sources"] == ["onair"] and rows[1]["change"] is None and len(rows[1]["tag"]) <= 20
    assert _channel_trends(None) == {} and parse_v2({})["channel_trends"] == {}


async def test_drama_and_lockerroom_tabs_show_board_and_tap_sends_channel_topic(bot: DealBot, settings: Settings,
                                                                                tmp_path: Path) -> None:
    _export(tmp_path)
    blogauto_status(settings)
    body = await state(bot)
    fin, hot = body["blogs"]["finance"]["trends"], body["blogs"]["hotdeal"]["trends"]
    assert fin["title"] == "인기 키워드" and fin["kind"] == "drama" and fin["items"][0]["keyword"] == "정년이"
    assert fin["items"][0]["written"] is True and hot["items"][0]["keyword"] == "김현수"
    assert fin["src"]["roster"] == "계약·이적" and set(fin["src"]) >= {"topic", "google", "onair", "news"}
    assert fin["write"]["action"] == "blog.channel_topic" and fin["write"]["args"] == {"blog": "finance"}
    assert fin["write"]["label"] == "이 키워드로 드라마 쓰기" and hot["write"]["label"] == "이 키워드로 라커룸 쓰기"
    assert body["blogs"]["issue"]["trends"]["write"]["action"] == "blog.issue"  # Pick 은 그대로
    status, res = await act(bot, hot["write"]["action"], dict(hot["write"]["args"], keyword=hot["items"][1]["keyword"]))
    assert status == 202, res  # 붙여넣기 글이라 확인 없이 바로
    f = spool.read(spool.root(settings.data_dir, spool.DASH_ACTIONS) / "new" / f"{res['id']}.json")
    assert f["action"] == "channel_topic" and f["args"] == {"blog": "hotdeal", "keyword": "LG 트윈스"}
    assert f["action"] == fixture("action_channel_topic.json")["action"]


async def test_no_board_without_channel_or_old_blogauto(bot: DealBot, settings: Settings, tmp_path: Path) -> None:
    _export(tmp_path, channel=False)
    blogauto_status(settings)
    assert "trends" not in (await state(bot))["blogs"]["finance"]  # 새 채널이 꺼진 블로그
    _export(tmp_path, channel_trends=None)
    assert (await state(bot))["blogs"]["finance"]["trends"] is None  # 예전 Blog-Auto — 카드 없음
    _export(tmp_path)
    blogauto_status(settings, actions=["publish", "dismiss"])
    tr = (await state(bot))["blogs"]["hotdeal"]["trends"]
    assert tr["items"] and tr["write"] is None  # 일을 모르는 Blog-Auto — 보기만


def test_args_target_and_no_confirm() -> None:
    a = clean_args("blog.channel_topic", {"blog": "finance", "keyword": "  나는   솔로 ", "x": 1})
    assert a == {"blog": "finance", "keyword": "나는 솔로"}
    for bad in ({"blog": "issue", "keyword": "x"}, {"blog": "finance", "keyword": ""}, {"blog": "finance", "keyword": "가" * 41}):
        with pytest.raises(ActionError):
            clean_args("blog.channel_topic", bad)
    assert target_of("blog.channel_topic", a) != target_of("blog.channel_topic", {"blog": "finance", "keyword": "정년이"})
    assert target_of("blog.channel_topic", {"blog": "finance", "keyword": "정년이"}) == fixture("action_channel_topic.json")["target"]
    assert not needs_confirm("blog.channel_topic", a)


def test_app_js_reuses_the_board_with_channel_labels() -> None:
    js = (webapp.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    for word in ("t.title || '인기 검색어'", "(t.src || TSRC)[k]", "TTAG[x.tag] || x.tag",
                 "if (b.trends && b.trends.items.length) out.push(...trendBox(b.trends))"):
        assert word in js, word
