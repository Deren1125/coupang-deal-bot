from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from dealbot.collectors import CollectorContext, build_collector
from dealbot.collectors.ppomppu import (
    decode_html,
    find_shop_urls,
    guess_kind,
    parse_list_page,
    parse_title,
)
from dealbot.config import CollectorConfig, Settings
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database
from dealbot.utils.text import parse_price


def test_parse_title_date_range_is_not_a_price() -> None:
    t = parse_title("[토스] 9,900원 샴푸 + 선크림 900원, 같이 담으면 무료배송 (~9/13)")
    assert t["price"] == 9900 and t["shipping"] is None and t["name"].endswith("(~9/13)")  # 예전엔 9원/배송 '13' 으로 읽었다
    assert parse_title("[네이버] 5,000원 할인 쿠폰 (9/12~9/14)")["price"] is None  # 할인 금액은 가격이 아니다
    assert parse_title("[쿠팡] 세제 3개 (3/무료)")["price"] is None  # 개수처럼 가격일 리 없는 수


def test_parse_title() -> None:
    t = parse_title("[쿠팡] 스탠리 텀블러 1.18L (29,900원/무료)")
    assert t == {"shop_tag": "쿠팡", "name": "스탠리 텀블러 1.18L", "price": 29900, "shipping": "무료"}
    t2 = parse_title("[쿠팡] 로켓와우 10% 쿠폰 (무료)")
    assert t2["price"] is None and t2["shipping"] is None
    t3 = parse_title("제목만 있음")
    assert t3["shop_tag"] is None and t3["name"] == "제목만 있음"
    t4 = parse_title("[쿠팡(로켓)] 상품 (1.5만원/3,000원)")
    assert t4["shop_tag"] == "쿠팡(로켓)" and t4["price"] == 15000 and t4["shipping"] == "3,000원"
    assert guess_kind("로켓와우 10% 쿠폰", None) == "coupon"
    assert guess_kind("무료체험 이벤트", None) == "event"
    assert guess_kind("상품", 1000) == "hotdeal"


def test_parse_title_price_skips_quantities_and_benefit_amounts() -> None:
    """괄호 속 맨 앞 숫자가 아니라 판매가: 개수·용량·옛 가격(화살표 앞)·할인 금액은 가격이 아니다."""
    assert parse_title("[쿠팡] 상품 (29,900원→19,900원/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (정가 29,900원 -> 19,900원 / 무료)")["price"] == 19900
    assert parse_title("[쿠팡] 앤커 보조배터리 (10,000mAh 19,900원/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 비비고 왕교자 (2개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 비비고 왕교자 (1+1 12,900원/무료)")["price"] == 12900
    assert parse_title("[네이버] 하림 닭가슴살 (2팩 25,800원/무료)")["price"] == 25800
    assert parse_title("[쿠팡] 코카콜라 제로 (500ml 24개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 크리넥스 물티슈 (100매 9,900원/무료)")["price"] == 9900
    assert parse_title("[쿠팡] 삼다수 (2Lx24개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 코카콜라 제로 (500mlx24개 12,900원/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 삼성 메모리 (128GBx2 59,000원/무료)")["price"] == 59000
    assert parse_title("[쿠팡] 상품 (1만원 할인 후 19,900원/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (12,900원 쿠폰 후 9,900원/무료)")["price"] == 9900
    t = parse_title("[쿠팡] 로켓프레시 첫구매 쿠폰 (10,000원 할인)")
    assert t["price"] is None and guess_kind(t["name"], t["price"]) == "coupon"  # 쿠폰 금액을 상품 가격으로 올리지 않음
    assert parse_title("[쿠팡] 로켓프레시 (5,000원 쿠폰)")["price"] is None
    assert parse_title("[쿠팡] 상품 (5만원 이상 구매시 1만원 할인)")["price"] is None
    # 지금껏 읽던 모양은 그대로
    assert parse_title("[쿠팡] 상품 (12900/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (12900x2/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (1만2000원/무료)")["price"] == 12000
    assert parse_title("[쿠팡] 상품 (3만5천원/무료)")["price"] == 35000
    assert parse_title("[쿠팡] 상품 (19,900원 쿠폰가/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (19,900원 쿠폰적용/무료)")["price"] == 19900
    assert parse_title("[쿠팡] 상품 (현대카드 7,700원/무료)")["price"] == 7700
    assert parse_title("[쿠팡] 상품 (12900 + 배송비 3,000원)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (19,900원 + 3,000원 적립/무료)")["price"] == 19900
    # 혜택 말 뒤에 그 금액이 판매가라는 말이 이어지면 가격 (배송비 칸이 있든 없든)
    for said in ("쿠폰할인가", "쿠폰 할인가", "쿠폰 미적용", "쿠폰미적용", "적립포함", "적립 포함", "증정품 포함",
                 "쿠폰 사용시", "쿠폰사용가", "적립금 사용시", "캐시백 포함", "할인된 가격", "쿠폰 후", "할인 후"):
        for meta in (f"12,900원 {said}/무료", f"12,900원 {said}"):
            t = parse_title(f"[쿠팡] 상품 ({meta})")
            assert t["price"] == 12900 and guess_kind(t["name"], t["price"]) == "hotdeal", meta
    # 배송비 칸까지 적힌 상품 딜은 혜택 말만 붙은 금액도 가격 (예전처럼). 배송비 칸이 없으면 쿠폰·이벤트
    assert parse_title("[쿠팡] 상품 (12,900원 할인/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (12,900원 쿠폰 할인/무료)")["price"] == 12900
    assert parse_title("[쿠팡] 상품 (12,900원 할인)")["price"] is None


def test_parse_title_coupon_amount_before_the_price() -> None:
    """'쿠폰 사용시'·'적립 포함' 같은 말 바로 뒤에 판매가가 오면 앞 금액은 쿠폰·적립 금액이다."""
    for meta, price in (
        ("5,000원 쿠폰 사용시 19,900원/무료", 19900),
        ("2만원 쿠폰 사용시 39,900원/무료", 39900),
        ("1만원 적립 포함 12,900원/무료", 12900),
        ("5,000원 쿠폰 포함 19,900원/무료", 19900),
        ("1만원 쿠폰할인가 19,900원/무료", 19900),
        ("19,900원 쿠폰 사용시 14,900원/무료", 14900),
        ("19,900원 → 5,000원 쿠폰 사용시 14,900원/무료", 14900),
        ("3,000원 쿠폰 적용시 12,900원/무료", 12900),
        ("5,000원 쿠폰 사용시 실구매가 14,900원/무료", 14900),
        ("5,000원 쿠폰 사용시 19,900원", 19900),
        ("1만원 할인 후 개당 9,900원/무료", 9900),
        # 판매가라는 말과 판매가 사이에 다른 말이 끼어도 ('후', '총', '최저가', '와우', '하면', '으로', '2개', '가격')
        ("5,000원 쿠폰 적용 후 14,900원/무료", 14900),
        ("3,000원 적립금 사용 후 9,900원/무료", 9900),
        ("5,000원 쿠폰 사용 시 총 24,900원/무료", 24900),
        ("4,000원 쿠폰 적용시 최저가 17,900원/무료", 17900),
        ("10만원 쿠폰 적용하면 1,290,000원/무료", 1290000),
        ("5,000원 쿠폰 적용으로 24,900원/무료", 24900),
        ("5,000원 쿠폰 적용시 와우 14,900원/무료", 14900),
        ("3,000원 쿠폰 적용 2개 23,800원/무료", 23800),
        ("1만원 쿠폰 적용 후 가격 89,000원/무료", 89000),
        ("3,000원 할인된 가격 15,900원/무료", 15900),
        ("5천원 쿠폰 적용시 25,900원/무료", 25900),
        ("5,000원 쿠폰 적용 시 개당 1만원대 29,900원/무료", 29900),
        # 혜택 말 앞에 꾸밈말이 붙거나('장바구니쿠폰', '즉시할인', '네이버페이 적립') 혜택 말이 금액 앞에 와도
        ("1만원 장바구니쿠폰 적용시 25,900원/무료", 25900),
        ("2,000원 스마일클럽쿠폰 사용시 23,900원/무료", 23900),
        ("2만원 즉시할인 적용 279,000원/무료", 279000),
        ("5만원 카드할인 시 599,000원/무료", 599000),
        ("5만원 T멤버십 할인적용 449,000원/무료", 449000),
        ("5,000원 스마일캐시 적립 포함 39,900원/무료", 39900),
        ("2,000원 네이버페이 적립 포함 19,900원/무료", 19900),
        ("쿠폰 3,000원 적용시 19,900원/무료", 19900),
    ):
        t = parse_title(f"[쿠팡] 상품 ({meta})")
        assert t["price"] == price and guess_kind(t["name"], t["price"]) == "hotdeal", meta
    # 뒤에 오는 것이 개수·용량·개당 값·혜택 금액·배송비·쿠폰 없는 값이면 앞 금액이 그대로 판매가 (배송비 칸이 있든 없든)
    for meta in ("12,900원 쿠폰적용 2개/무료", "12,900원 쿠폰적용 2개", "12,900원 쿠폰적용 1+1", "12,900원 쿠폰 후 2개",
                 "12,900원 쿠폰적용 500ml 24개", "12,900원 쿠폰 사용시 1개당 6,450원/무료",
                 "12,900원 쿠폰적용 3,000원 적립/무료", "12,900원 쿠폰적용, 배송비 3,000원",
                 "12,900원 쿠폰 사용시 2,500원 배송", "12,900원 쿠폰 미적용 시 17,900원/무료",
                 "12,900원 쿠폰적용, 1만개 한정/무료", "12,900원 쿠폰 후 개당 6,450원/무료"):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == 12900, meta
    assert parse_title("[쿠팡] 상품 (2개 25,800원 쿠폰적용 개당 12,900원/무료)")["price"] == 25800
    # 꾸밈말 붙은 혜택 말만 있고 다른 금액이 없으면 예전처럼 그 금액이 판매가
    for meta in ("189,000원 카드할인", "19,900원 카드할인시", "12,900원 와우할인가", "9,900원 첫구매 할인/무료"):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == parse_price(meta.split()[0]), meta


def test_parse_title_later_reward_shipping_or_unit_amount_is_not_the_price() -> None:
    """판매가 뒤의 적립·추가할인 금액, 배송비, 단위당 값, 쿠폰 없는 값, 정가는 판매가를 밀어내지 않는다."""
    for meta in (
        # 적립·추가할인 금액 (꾸밈말이 붙어도)
        "17,900원 쿠폰적용시 2,000원 추가적립/무료", "17,900원 쿠폰 적용, 2,000원 추가할인 가능/무료",
        "17,900원 쿠폰적용 3,000원 중복할인/무료", "17,900원 쿠폰적용 2,000원 즉시할인/무료",
        "17,900원 쿠폰적용시 3,000원 네이버페이 적립/무료", "17,900원 쿠폰가 1,000원 리뷰 적립/무료",
        "17,900원 쿠폰 포함 2,000원 포인트 적립/무료", "17,900원 쿠폰적용 2,000원 스마일캐시/무료",
        "17,900원 쿠폰가 3,000원 추가할인", "17,900원 쿠폰적용 3,000원 더 할인/무료",
        "17,900원 쿠폰 사용 시 2,000원 추가 적립/무료", "17,900원 쿠폰적용, 추가할인 8,000원/무료",
        "17,900원 카드 추가할인 9,000원/무료",
        # 배송비·단위당 값·쿠폰 없는 값 — 판매가로 보기엔 너무 작거나 그렇다고 적힌 값
        "17,900원 쿠폰 적용 2,500원", "17,900원 쿠폰적용 3,000원 (3만원 이상 무료)",
        "17,900원 쿠폰 적용 시 1,790원/100g/무료", "17,900원 쿠폰가 1,790원 꼴/무료",
        "17,900원 쿠폰적용, 19,900원 일반/무료", "17,900원 쿠폰적용 시 9,900원 2개 구매시/무료",
        "17,900원 쿠폰 후, 배송비 3,000원", "17,900원 할인 후 가격, 정가 49,900원/무료",
    ):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == 17900, meta
    for meta in ("12,900원 쿠폰적용, 15,900원 미적용/무료", "12,900원 쿠폰적용, 15,900원 쿠폰미적용/무료",
                 "12,900원 쿠폰적용, 15,900원/무료"):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == 12900, meta
    assert parse_title("[쿠팡] 상품 (9,900원 쿠폰적용 3,000원)")["price"] == 9900
    t = parse_title("[쿠팡] 상품 (17,900원 쿠폰 적용 시 1,790원/100g/무료)")
    assert t["shipping"] == "무료"  # 슬래시 사이 '100g' 은 배송비 칸이 아니라 단위
    # 뒤 금액이 쿠폰으로 더 깎인 값이면 그 값 (혜택 금액을 건너뛰어도)
    for meta, price in (("17,900원 쿠폰가 3,000원 추가 할인시 14,900원", 14900),
                        ("12,900원 쿠폰 후 9,900원/무료", 9900), ("3,000원 쿠폰 적용시 17,900원 + 2,000원 적립/무료", 17900),
                        ("1,000원 쿠폰 적용시 12,900원 일반 13,900원/무료", 12900),
                        ("5,000원 쿠폰 사용시 19,900원 배송비 포함", 19900)):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == price, meta
    # 와우·카드 회원값처럼 쉼표 뒤에 따로 붙은 더 낮은 값은 다른 조건의 값이라 예전(첫 금액)처럼 둔다
    for meta in ("17,900원 쿠폰적용, 와우 16,900원/무료", "17,900원 쿠폰적용 와우 16,900원/무료",
                 "17,900원 쿠폰가, 16,900원 와우쿠폰가/무료"):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == 17900, meta


def test_parse_title_later_amount_needs_linking_words() -> None:
    """앞 금액에 혜택 말이 붙어 있어도 뒤 금액은 '사용시'·'후'·'적용가'·'최종' 같은 이어 주는 말만 사이에 있을 때 판매가다.
    '일반'·'판매가'·'정가'·'첫구매 적립금' 같은 다른 말이 끼거나, 뒤 금액에 사은품·할인액이라는 말이 붙거나, 쿠폰 금액답지
    않은 앞 금액보다 크기만 하면 예전(첫 금액)처럼 둔다."""
    for meta, price in (
        ("99,000원 쿠폰가, 일반 109,000원/무료", 99000),
        ("129,000원 쿠폰적용, 판매가 149,000원/무료", 129000),
        ("299,000원 쿠폰적용가, 할인전 349,000원/무료", 299000),
        ("39,900원 쿠폰가 + 19,000원 상당 사은품/무료", 39900),
        ("14,900원 쿠폰적용, 첫구매 적립금 6,000원/무료", 14900),
        ("19,900원 쿠폰가, 첫구매 포인트 1만원/무료", 19900),
        ("19,900원 쿠폰적용 시, 적립금 8,000원/무료", 19900),
        ("249,000원 카드할인가, 판매가 289,000원/무료", 249000),
        ("19,900원 쿠폰적용, 할인금액 8,000원/무료", 19900),
        ("15,900원 쿠폰적용가, 쿠폰할인액 7,000원/무료", 15900),
        ("9,900원 쿠폰가 4,000원↓/무료", 9900),
        ("9,900원 쿠폰가, -4,000원/무료", 9900),
        ("19,900원 쿠폰적용가, 10,000원 OFF/무료", 19900),
        ("19,900원 쿠폰가, 최대 적립액 8,000원/무료", 19900),
        ("24,900원 쿠폰적용가, 1만원 상당 스타벅스 텀블러 증정/무료", 24900),
        ("29,900원 쿠폰가, 리워드 12,000원/무료", 29900),
        ("399,000원 카드할인가, 정상판매가 499,000원/무료", 399000),
        ("69,000원 쿠폰적용, 기존가 89,000원/무료", 69000),
        ("69,000원 쿠폰적용시, 와우 아닐 때 72,000원/무료", 69000),
        ("1,290,000원 카드할인가, 출고가 1,590,000원/무료", 1290000),
        ("13,000원 쿠폰적용, 2개 26,000원/무료", 13000),
        ("10,000원 쿠폰적용, 2개 20,000원/무료", 10000),  # 개수 뒤의 배수는 묶음 값
        ("89,000원 쿠폰적용 99,000원/무료", 89000),  # 89,000원은 쿠폰 금액답지 않다
        ("6,900원 쿠폰적용 3,000원/무료", 6900),  # 절반도 안 되는 값은 배송비일 수 있다
        ("19,900원 카드할인 9,000원/무료", 19900),  # 혜택 말만 사이에 있으면 그 말이 뒤 금액(할인액)에 붙은 것일 수 있다
        ("19,900원 쿠폰적용시 9,900원 절약/무료", 19900),
        ("12,900원 할인 2,500원", 12900),  # 혜택 금액 뒤에 이어지지 않는 금액이 있으면 예전처럼 첫 금액
    ):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] == price, meta


def test_parse_title_benefit_word_before_the_amount_and_chains() -> None:
    """혜택 말이 금액 앞에 오거나('쿠폰 5,000원 다운 후', '적립금 3,000원 사용 후') 혜택 금액이 여럿 이어져도 판매가를 찾는다."""
    for meta, price in (
        ("적립금 3,000원 사용 후 24,900원/무료", 24900),
        ("3,000원 적립금 사용 후 9,900원/무료", 9900),
        ("쿠폰 5,000원 다운 후 14,900원/무료", 14900),
        ("11pay 포인트 5,000원 사용시 34,900원/무료", 34900),
        ("캐시 2,000원 사용시 21,900원/무료", 21900),
        ("와우할인 2,000원 받아 9,900원/무료", 9900),
        ("할인쿠폰 2,000원, 21,900원/무료", 21900),
        ("쿠폰할인 3,000원, 19,900원/무료", 19900),
        ("2만원 즉시할인 + 1만원 쿠폰 적용 시 169,000원/무료", 169000),
        ("5천원 쿠폰적용 시 2만원대 초반 21,900원/무료", 21900),
        ("개당 647원 25,900원/무료", 25900),
    ):
        t = parse_title(f"[쿠팡] 상품 ({meta})")
        assert t["price"] == price and guess_kind(t["name"], t["price"]) == "hotdeal", meta
    # 혜택·조건 금액뿐이면 판매가가 없다
    for meta in ("1만원 할인 쿠폰 적용", "2만원 이상 구매시 1만원 할인", "1,000원 적립", "12,900원 이상 무료배송"):
        assert parse_title(f"[쿠팡] 상품 ({meta})")["price"] is None, meta


def test_parse_title_repeated_wording_stays_fast() -> None:
    """같은 말이 서른 번 되풀이된 제목도 곧바로 읽는다 (재귀가 있던 판은 2^n 으로 느려져 스무 번에 몇 초씩 걸렸다).
    금액마다 앞뒤 글만 보고 뒤로 한 번 훑으니 글 길이에 비례한다."""
    units = ("1,000원 쿠폰 사용시 ", "쿠폰 사용시 ", "1원 쿠폰 후 ", "1원쿠폰후", "2개 쿠폰 후 ", "19,900원 쿠폰가 ",
             "1,000원 할인쿠폰 ", "쿠폰 1,000원 적용 ", "1만원대 ", "개당 1,000원 ", "1+", "(1)", "1,000원→")
    for unit in units:
        for title in (f"[쿠팡] 상품 ({unit * 30}19,900원/무료)", f"[쿠팡] 상품 (1,000원 쿠폰 {unit * 30}19,900원)"):
            started = time.perf_counter()
            parse_title(title)
            assert time.perf_counter() - started < 0.05, unit


def test_parse_title_nested_shipping_and_trailing_note() -> None:
    """끝 괄호 안 괄호("3,000원(3만↑무료)")와 괄호 뒤 꼬리말("와우", "카드할인")이 이름·가격·배송을 망치지 않는다."""
    t = parse_title("[네이버] 동원 리챔 오리지널 200g 10개 (25,900원/3,000원(3만↑무료))")
    assert t == {"shop_tag": "네이버", "name": "동원 리챔 오리지널 200g 10개", "price": 25900, "shipping": "3,000원(3만↑무료)"}
    t = parse_title("[G마켓] 오뚜기 진라면 매운맛 120g 40개 (19,800원/무료(스마일클럽))")
    assert t["name"] == "오뚜기 진라면 매운맛 120g 40개" and t["shipping"] == "무료(스마일클럽)"
    t = parse_title("[쿠팡] 삼다수 2L 24병 (15,840원/무료) 와우")
    assert t == {"shop_tag": "쿠팡", "name": "삼다수 2L 24병", "price": 15840, "shipping": "무료"}
    t = parse_title("[11번가] 갤럭시 버즈3 프로 (189,000원/무료) 카드할인")
    assert t["name"] == "갤럭시 버즈3 프로" and t["price"] == 189000 and guess_kind(t["name"], t["price"]) == "hotdeal"
    assert parse_title("[네이버] 굽네 닭가슴살 30팩 (29,900원/무료) 적립포함")["price"] == 29900
    assert parse_title("[쿠팡] 상품 (19,900원(쿠폰적용)/무료)")["price"] == 19900
    # 가격 괄호가 아니면 예전처럼 통째로 이름. 꼬리말 앞 괄호에 '원'도 배송비 칸도 없는 숫자만 있으면 연식·용량·모델 번호다
    assert parse_title("[쿠팡] 에어팟 프로 2 (USB-C) 화이트")["name"] == "에어팟 프로 2 (USB-C) 화이트"
    for title in ("[쿠팡] 맥북 에어 M3 (2024) 실버", "[쿠팡] 아이폰 16 (256) 블랙", "[쿠팡] 레고 테크닉 (42115) 람보르기니"):
        t = parse_title(title)
        assert t["price"] is None and t["name"] == title.removeprefix("[쿠팡] "), title
    assert parse_title("[쿠팡] 상품 (12,900원) 와우") == {"shop_tag": "쿠팡", "name": "상품", "price": 12900, "shipping": None}
    assert parse_title("[쿠팡] 상품 (옵션) 블랙 (12,900원/무료)")["name"] == "상품 (옵션) 블랙"
    t = parse_title("[토스] 9,900원 샴푸 (~9/13) 선착순")
    assert t["name"] == "9,900원 샴푸 (~9/13) 선착순" and t["price"] == 9900 and t["shipping"] is None


def test_parse_list_page(fixtures_dir: Path) -> None:
    html = (fixtures_dir / "ppomppu_list.html").read_text(encoding="utf-8")
    items = parse_list_page(html, row_selector="tr.baseList", title_selector="a.baseList-title", thumb_selector="img")
    assert [i["external_id"] for i in items] == [str(n) for n in range(600001, 600008)]
    first = items[0]
    assert first["shop_tag"] == "쿠팡" and first["price"] == 29900
    assert first["recommend"] == 10 and first["views"] == 5000 and first["comments"] == 12
    assert items[1]["comments"] is None
    assert first["thumb"] == "https://cdn2.ppomppu.co.kr/zboard/data3/2026/0903/m_thumb_600001.jpg"
    assert first["post_url"].startswith("https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu")


def test_find_shop_urls(fixtures_dir: Path) -> None:
    reg = ShopRegistry()
    html = (fixtures_dir / "ppomppu_view_600001.html").read_text(encoding="utf-8")
    urls = find_shop_urls(html, reg.get("coupang"))
    assert urls[0].startswith("https://www.coupang.com/vp/products/7381234")
    assert all("ppomppu.co.kr" not in u for u in urls)
    html4 = (fixtures_dir / "ppomppu_view_600004.html").read_text(encoding="utf-8")
    assert find_shop_urls(html4, reg.get("coupang")) == ["https://link.coupang.com/a/OTHERS"]
    html5 = (fixtures_dir / "ppomppu_view_600005.html").read_text(encoding="utf-8")
    assert find_shop_urls(html5, reg.get("toss")) == ["https://toss.im/_m/ABC123"]
    assert find_shop_urls(html5, reg.get("coupang")) == []
    assert find_shop_urls(html5, None) == ["https://toss.im/_m/ABC123"]


def test_decode_html_euc_kr() -> None:
    body = "안녕".encode("euc-kr")
    resp = httpx.Response(200, content=body, headers={"content-type": "text/html"})
    assert decode_html(resp) == "안녕"
    resp2 = httpx.Response(200, content="안녕".encode(), headers={"content-type": "text/html; charset=utf-8"})
    assert decode_html(resp2) == "안녕"


def _transport(fixtures_dir: Path, calls: list[str]) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/zboard/zboard.php":
            return httpx.Response(200, content=(fixtures_dir / "ppomppu_list.html").read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        if req.url.path == "/zboard/view.php":
            f = fixtures_dir / f"ppomppu_view_{req.url.params['no']}.html"
            if f.exists():
                return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
            return httpx.Response(404)
        if req.url.host == "link.coupang.com":
            return httpx.Response(302, headers={"location": "https://www.coupang.com/vp/products/999?itemId=1"})
        if req.url.host == "www.coupang.com":
            return httpx.Response(200, text="product page")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_collect_multi_shop(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []
    http = httpx.AsyncClient(transport=_transport(fixtures_dir, calls))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="ppomppu", type="ppomppu", options={"request_delay_seconds": 0})
    collector = build_collector(cfg, ctx)

    products = await collector.collect()
    by_ext = {p.external_id: p for p in products}
    assert set(by_ext) == {"600001", "600002", "600003", "600004", "600005", "600006"}  # 듣보잡몰은 skip

    c = by_ext["600001"]
    assert c.shop == "coupang" and c.product_id == "coupang:7381234:22" and c.price == 29900 and c.recommend_count == 10 and c.comment_count == 12
    assert c.url == "https://www.coupang.com/vp/products/7381234?itemId=11&vendorItemId=22" and c.affiliate_url is None
    assert by_ext["600004"].product_id == "coupang:999"  # 타인 단축링크 → 원본 상품으로
    g = by_ext["600002"]
    assert g.shop == "gmarket" and g.product_id == "gmarket:3344556"
    coupon = by_ext["600003"]
    assert coupon.deal_kind == "coupon" and coupon.price == 0 and coupon.recommend_count == 7
    t = by_ext["600005"]
    assert t.shop == "toss" and t.product_id == "toss:ABC123" and t.price == 14890 and t.url == "https://toss.im/_m/ABC123"
    n = by_ext["600006"]
    assert n.shop == "naver" and n.product_id == "naver:123456"
    assert not any("no=600007" in u for u in calls)

    # 두 번째 실행: 상세 요청 없이, 관심도 게이트(추천≥1/댓글≥3/조회≥500)를 넘는 글만 저장된 링크로 다시 올라옴
    calls.clear()
    again = await collector.collect()
    assert all("view.php" not in u for u in calls)
    assert {p.external_id for p in again} == {"600001", "600002", "600003", "600005", "600006"}  # 600004 는 추천0·조회100
    assert {"coupang:7381234:22", by_ext["600003"].product_id, "toss:ABC123"} <= {p.product_id for p in again}
    assert all(p.url for p in again)


async def test_collect_shop_filter_and_unknown_raw(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    calls: list[str] = []
    http = httpx.AsyncClient(transport=_transport(fixtures_dir, calls))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp", type="ppomppu", options={"request_delay_seconds": 0, "shops": ["toss"], "unknown_shop": "raw"})
    products = await build_collector(cfg, ctx).collect()
    assert [p.shop for p in products] == ["toss"]
    cfg2 = CollectorConfig(name="pp2", type="ppomppu", options={"request_delay_seconds": 0, "unknown_shop": "raw"})
    products2 = await build_collector(cfg2, ctx).collect()
    unknown = [p for p in products2 if p.external_id == "600007"]
    assert unknown and unknown[0].shop == "unknown" and unknown[0].url == "https://unknown-mall.example/item/1"


def test_registry_plugin_path() -> None:
    from dealbot.collectors import available_types, resolve_type
    from dealbot.collectors.ppomppu import PpomppuCollector

    assert {"coupang_goldbox", "coupang_category_best", "ppomppu", "adpick_hotdeal"} <= set(available_types())
    assert resolve_type("dealbot.collectors.ppomppu:PpomppuCollector") is PpomppuCollector


async def test_403_falls_back_to_mobile(settings: Settings, db: Database, fixtures_dir: Path) -> None:
    """데스크톱이 403 이면 모바일 주소로 재시도하고, 브라우저 헤더와 워밍업 요청이 들어간다."""
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.url.path))
        assert req.headers.get("upgrade-insecure-requests") == "1"
        assert "ko-KR" in req.headers.get("accept-language", "")
        if req.url.host == "www.ppomppu.co.kr" and req.url.path == "/zboard/zboard.php":
            return httpx.Response(403, text="blocked")
        if req.url.path in ("/", "/new/bbs_list.php", "/zboard/zboard.php"):
            return httpx.Response(200, content=(fixtures_dir / "ppomppu_list.html").read_bytes(),
                                  headers={"content-type": "text/html; charset=utf-8"})
        f = fixtures_dir / f"ppomppu_view_{req.url.params.get('no', '')}.html"
        if f.exists():
            return httpx.Response(200, content=f.read_bytes(), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp403", type="ppomppu", options={"request_delay_seconds": 0, "max_detail_fetch_per_run": 1})
    products = await build_collector(cfg, ctx).collect()

    hosts_paths = [f"{h}{p}" for h, p in seen]
    assert "www.ppomppu.co.kr/" in hosts_paths          # 워밍업
    assert "m.ppomppu.co.kr/new/bbs_list.php" in hosts_paths  # 모바일 폴백
    assert products  # 모바일에서 목록을 읽어 상품이 나온다


async def test_403_without_fallback_raises(settings: Settings, db: Database) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html></html>") if req.url.path == "/" else httpx.Response(403, text="no")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ctx = CollectorContext(settings=settings, http=http, db=db, coupang=None, shops=settings.shop_registry())
    cfg = CollectorConfig(name="pp", type="ppomppu", options={"request_delay_seconds": 0, "mobile_fallback": False})
    with pytest.raises(httpx.HTTPStatusError, match="403"):
        await build_collector(cfg, ctx).collect()
