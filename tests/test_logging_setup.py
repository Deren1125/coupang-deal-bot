"""로그 설정: 봇 토큰이 든 텔레그램 요청 주소가 로그(화면·파일)에 남지 않는다.

httpx 는 기본으로 모든 요청을 INFO 로 남기는데(HTTP Request: POST https://api.telegram.org/bot<토큰>/...),
setup_logging 이 httpx·httpcore·telegram 로그를 WARNING 으로 올려 막는다. 가짜 전송(MockTransport)이라 인터넷은 안 탄다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from dealbot.logging_setup import setup_logging

TOKEN = "123456789:AAFake-test-token_0123456789abcdef"
NOISY = ("httpx", "httpcore", "telegram", "apscheduler")


@pytest.fixture
def restore_logging():  # type: ignore[no-untyped-def]
    """setup_logging 은 전역 로그 설정을 바꾼다 → 다른 테스트에 남지 않게 되돌린다."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    levels = {n: logging.getLogger(n).level for n in NOISY}
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    for h in handlers:
        if h not in root.handlers:
            root.addHandler(h)
    root.setLevel(level)
    for n, lv in levels.items():
        logging.getLogger(n).setLevel(lv)


@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_bot_token_urls_are_not_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str], restore_logging: None, level: str) -> None:
    setup_logging(level, tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))) as client:
        client.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
    logging.getLogger("telegram.ext.ExtBot").info("Calling Bot API endpoint for bot%s", TOKEN)
    logging.getLogger("dealbot.app").info("평소 로그는 그대로 남음")
    for h in logging.getLogger().handlers:
        h.flush()
    out = capsys.readouterr().out + (tmp_path / "dealbot.log").read_text(encoding="utf-8")
    assert "평소 로그는 그대로 남음" in out
    assert TOKEN not in out and "api.telegram.org" not in out
