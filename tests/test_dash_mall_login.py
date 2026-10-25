"""쇼핑몰 로그인·사람 확인 할 일 (주인 10/25 '할 일 목록에 에이블리 새로 로그인하는 게 안 떠').

Blog-Auto 가 남기는 mall_login:<몰>(creator_stats) · mall_login:ably_cf(에이블리 사람 확인) 알림이 모르는 키라 'sys-blogauto' 한 줄에
다른 블로그 시스템 알림과 묶여 첫 줄 제목만 보였다 → 몰마다 따로 한 줄(conn-mall-<몰>) + 원격 화면 버튼 (확인 통과는 주인 몫)."""

from __future__ import annotations

import time

from dealbot.app import DealBot
from dealbot.notify import iid as iidmod
from test_dash_api_actions import bot  # noqa: F401 — 픽스처


def test_mall_login_keys_get_their_own_row() -> None:
    assert iidmod.iid_for("mall_login:ably_cf", "", src="blogauto") == "conn-mall-ably"
    assert iidmod.iid_for("mall_login:zigzag", "", src="blogauto") == "conn-mall-zigzag"
    assert iidmod.iid_for("mall_login:toss", "", src="blogauto") == "conn-mall-toss"
    assert iidmod.ok("conn-mall-ably")
    assert iidmod.iid_for("mall_login:../x", "", src="blogauto") == "sys-blogauto"  # 이상한 이름은 예전처럼


def test_ably_check_row_is_separate_and_opens_the_remote_screen(bot: DealBot) -> None:  # noqa: F811
    t0 = time.time()
    bot.router.open({"src": "blogauto", "key": "err:loop", "kind": "job_error", "iid": "sys-blogauto",  # type: ignore[union-attr]
                     "title": "블로그 프로그램 오류", "at": t0 - 60})
    bot.router.open({"src": "blogauto", "key": "mall_login:ably_cf", "kind": "mall_login",  # type: ignore[union-attr]
                     "iid": iidmod.iid_for("mall_login:ably_cf", "", src="blogauto"), "title": "에이블리 사람 확인",
                     "body": "에이블리가 '사람인지 확인하십시오' 화면을 띄워요", "at": t0})
    rows = {it["iid"]: it for it in bot.dash_state.items()}
    row = rows["conn-mall-ably"]
    assert row["title"] == "에이블리 사람 확인"
    assert row["primary"]["action"] == "login.remote" and row["primary"]["args"] == {"target": "ably"}
    assert rows["sys-blogauto"]["title"] == "블로그 프로그램 오류"  # 다른 시스템 알림은 그대로 따로


def test_ably_todo_line_from_the_blog_file_also_opens_the_remote_screen(settings, tmp_path) -> None:
    """알림이 딜봇에 넘어오지 않고 기록 파일 todo 에만 있어도 같은 한 줄 + 원격 화면 버튼."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from dash_helpers import blogauto_status, push_settings
    from test_dash_state import write_export

    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")
    data = {"generated_at": now, "items": [
        {"key": "mall_login:ably_cf", "marker": "⚠️[할 일]", "blog": "", "kind": "mall_login", "short": "에이블리 사람 확인",
         "text": "⚠️[할 일] 에이블리가 '사람인지 확인하십시오' 화면을 띄워요", "since": now,
         "urgent": False, "link": "", "group": "", "count": 1}]}
    write_export(tmp_path / "blogauto.json", todo=data)
    blogauto_status(settings)
    b = DealBot(push_settings(settings))
    try:
        row = next(it for it in b.dash_state.items() if it["iid"] == "conn-mall-ably")
        assert row["title"] == "에이블리 사람 확인" and row["primary"]["action"] == "login.remote"
    finally:
        b.db.close()
