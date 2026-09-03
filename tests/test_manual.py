from __future__ import annotations

import pytest

from dealbot.manual import parse_manual_post
from dealbot.shops import ShopRegistry

SAMPLE = """/post
[토스쇼핑 첫 구매 시 3,000원 추가 할인]

상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트
가격: 14,890원
https://toss.im/_m/P4Qr1ope

이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."""


def test_parse_sample() -> None:
    p = parse_manual_post(SAMPLE, ShopRegistry())
    assert p.url == "https://toss.im/_m/P4Qr1ope"
    assert p.name == "애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트"
    assert p.price == 14890
    assert p.shop_key == "toss"
    assert p.headline == "토스쇼핑 첫 구매 시 3,000원 추가 할인"


def test_parse_minimal_and_shop_field() -> None:
    p = parse_manual_post("Bhc 뿌링팝콘, 60g, 12개\n7,900원\nhttps://toss.im/_m/RP3Lprdr", ShopRegistry())
    assert p.name == "Bhc 뿌링팝콘, 60g, 12개" and p.price == 0 and p.shop_key == "toss"
    p2 = parse_manual_post("쇼핑몰: 네이버\n상품명: 물티슈\n가격: 9,900원\nhttps://naver.me/xyz", ShopRegistry())
    assert p2.shop_key == "naver" and p2.price == 9900


def test_parse_errors() -> None:
    with pytest.raises(ValueError, match="링크"):
        parse_manual_post("상품: x\n가격: 1,000원", ShopRegistry())
    with pytest.raises(ValueError, match="상품명"):
        parse_manual_post("https://toss.im/_m/abc", ShopRegistry())
