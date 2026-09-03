from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dealbot.utils.text import format_won, parse_price, truncate
from dealbot.utils.timeutil import humanize_delta, next_daily_time
from dealbot.utils.urls import (
    canonical_product_url,
    extract_product_id,
    is_affiliate_link,
    is_coupang_url,
    is_short_affiliate_link,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("12,900원", 12900),
        ("12900", 12900),
        ("1,234,000원/무료", 1234000),
        ("1.2만원", 12000),
        ("3만5천원", 35000),
        ("2만", 20000),
        ("무료", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text: str | None, expected: int | None) -> None:
    assert parse_price(text) == expected


def test_format_won_and_truncate() -> None:
    assert format_won(12900) == "12,900원"
    assert format_won(None) == "-"
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 4) == "abc"


@pytest.mark.parametrize(
    "url,pid",
    [
        ("https://www.coupang.com/vp/products/7381234?itemId=1&vendorItemId=2", "7381234"),
        ("https://m.coupang.com/vm/products/555", None),
        ("https://link.coupang.com/re/AFFSDP?lptag=AF123&pageKey=99887&itemId=1", "99887"),
        ("https://link.coupang.com/a/abcd", None),
        ("not a url", None),
    ],
)
def test_extract_product_id(url: str, pid: str | None) -> None:
    assert extract_product_id(url) == pid


def test_canonical_url_strips_tracking() -> None:
    u = "https://www.coupang.com/vp/products/7381234?itemId=11&vendorItemId=22&src=abc&lptag=x"
    assert canonical_product_url(u) == "https://www.coupang.com/vp/products/7381234?itemId=11&vendorItemId=22"
    assert canonical_product_url("https://link.coupang.com/a/abcd") is None


def test_link_classification() -> None:
    assert is_coupang_url("https://www.coupang.com/vp/products/1")
    assert is_coupang_url("https://link.coupang.com/a/x")
    assert not is_coupang_url("https://example.com/coupang.com")
    assert is_short_affiliate_link("https://link.coupang.com/a/x")
    assert not is_short_affiliate_link("https://www.coupang.com/vp/products/1")
    assert is_affiliate_link("https://link.coupang.com/re/AFFSDP?lptag=AF1")
    assert not is_affiliate_link("https://www.coupang.com/vp/products/1")


def test_time_helpers() -> None:
    assert humanize_delta(timedelta(seconds=30)) == "30초"
    assert humanize_delta(timedelta(minutes=5)) == "5분"
    assert humanize_delta(timedelta(hours=2, minutes=3)) == "2시간 3분"
    assert humanize_delta(timedelta(days=1, hours=1)) == "1일 1시간"
    now = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
    assert next_daily_time(now, "21:00") == datetime(2026, 1, 2, 21, 0, tzinfo=UTC)
    assert next_daily_time(now, "23:30") == datetime(2026, 1, 1, 23, 30, tzinfo=UTC)
