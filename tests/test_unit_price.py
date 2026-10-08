from __future__ import annotations

import pytest

from dealbot.utils.text import clean_name, unit_price


@pytest.mark.parametrize(
    ("name", "price", "expected"),
    [
        # 단위 있는 개수 + 단위 없는 곱수는 둘 다 곱함 (예전엔 'x 8'을 버려 단가가 몇 배로 나왔음)
        ("오뚜기밥 210g 3개입 x 8", 15000, "개당 625원 (100g당 298원)"),
        ("코카콜라 제로 355ml 24캔 x 2", 30000, "캔당 625원 (100ml당 176원)"),
        ("크리넥스 3겹 30m 30롤 x 2", 28000, "롤당 467원"),
        ("신라면 5개입 x 4", 15000, "개당 750원"),
        # 겉포장(팩·박스)에 안쪽 곱수가 있으면 '팩당'이 아니라 낱개 단가
        ("제주삼다수 2L x 6 x 2팩", 11000, "개당 917원 (100ml당 46원)"),
        ("코카콜라 제로 355ml x 24 x 2박스", 36000, "개당 750원 (100ml당 211원)"),
        ("코카콜라 제로 355ml 2박스 24캔", 36000, "캔당 750원 (100ml당 211원)"),
        # 붙여 쓴 곱하기 기호. 이어 쓴 곱수도 다 곱함 ('2L*6*2'의 2를 버리면 단가가 두 배)
        ("삼다수 2L*6", 11000, "개당 1,833원 (100ml당 92원)"),
        ("코카콜라 355ml×24캔", 18000, "캔당 750원 (100ml당 211원)"),
        ("제주삼다수 2L*6*2", 11000, "개당 917원 (100ml당 46원)"),
        ("코카콜라 355ml*24*2", 36000, "개당 750원 (100ml당 211원)"),
        ("생수 500mlx20x2", 10000, "개당 250원 (100ml당 50원)"),
        ("햇반 210g*3*8", 15000, "개당 625원 (100g당 298원)"),
        # 개수를 풀어 쓴 짝 곱은 같은 개수라 그대로
        ("햇반 210g 24개(3x8)", 15000, "개당 625원 (100g당 298원)"),
        # 곱수가 겉포장 뒤에 오면 겉포장을 센 것 (봉 하나가 1.05kg)
        ("비비고 왕교자 1.05kg 2봉 x 2", 26000, "봉당 6,500원 (100g당 619원)"),
        ("비비고 왕교자 1.05kg x 2봉 x 2", 26000, "봉당 6,500원 (100g당 619원)"),
        ("신라면 5봉 x 4", 15000, "봉당 750원"),
        # 커피믹스 스틱 'T'
        ("맥심 모카골드 12g x 100T", 15000, "개당 150원 (100g당 1,250원)"),
        ("카누 다크 0.9g 100T x 2", 19900, "개당 100원 (100g당 11,056원)"),
        # 원래 되던 것들은 그대로
        ("코카콜라 제로 355ml x 24", 18000, "개당 750원 (100ml당 211원)"),
        ("비비고 왕교자 1.05kg x 2봉", 13900, "봉당 6,950원 (100g당 662원)"),
        ("스파클 생수 무라벨, 500ml, 40병", 5900, "병당 148원 (100ml당 30원)"),
        ("쌀 10kg", 30000, "100g당 300원"),
        ("쌀 20kg", 59000, "100g당 295원"),
    ],
)
def test_unit_price_multiplies_all_counts(name: str, price: int, expected: str) -> None:
    assert unit_price(clean_name(name), price) == expected


@pytest.mark.parametrize(
    "name",
    [
        "RTX 4090 그래픽카드",
        "아이폰 X 256GB",  # 모델명 X 뒤 숫자는 곱수가 아님
        "Xiaomi 14T Pro 512GB",  # 모델명 T 는 스틱 개수가 아님
        "LG 그램 16T90P",
        "극세사 담요 150x200",  # 가로x세로 치수
        "극세사 담요 150 x 200",
        "이불 150x200 2kg",
        # 단위 없는 'NxM'은 치수인지 묶음 개수인지 몰라서 계산 안 함 (곱수 하나만 쓰면 단가가 몇 배로 틀림)
        "오뚜기밥 210g 3x8",
        "삼다수 2L 6x2",
        "삼다수 2L 6x2팩",
        "제주삼다수 500ml 20x2",
        "코카콜라 355ml 24x2",
        "제주삼다수 500ml 20x2 2개",
        "삼다수 6 x 2L",
    ],
)
def test_unit_price_ignores_model_names_and_dimensions(name: str) -> None:
    assert unit_price(clean_name(name), 29900) is None


@pytest.mark.parametrize(
    ("name", "price", "expected"),
    [
        # 가로x세로 치수는 빼고 따로 적힌 개수로 계산 (치수를 개수로 읽으면 80배 싸게 나왔음)
        ("수건 40 x 80 10개", 19900, "개당 1,990원"),
        ("욕실 발매트 40 x 60 2매", 19900, "매당 9,950원"),
        ("규조토 발매트 60 x 40 2개", 19900, "개당 9,950원"),
        ("크린랩 위생백 20x30cm 100매", 19900, "매당 199원"),
    ],
)
def test_unit_price_dimensions_use_the_named_count(name: str, price: int, expected: str) -> None:
    assert unit_price(clean_name(name), price) == expected


@pytest.mark.parametrize(
    "name",
    [
        "[1+1] 동원참치 라이트 150g 10캔",
        "하림 닭가슴살 1kg 1+1",
        "우유 1L 2+1",
        "(2+1) 우유 1L",
        "2+1 코카콜라 355ml 24캔",
        "펩시 제로 라임 355ml 24캔 1+1",
        "(1+1) 비비고 왕교자 1.05kg",
        "동원참치 150g 10캔 (5+5)",
        "동원참치 150g 10캔+2캔",
    ],
)
def test_unit_price_skips_bundle_bonus(name: str) -> None:
    # 덤 묶음은 가격이 묶음 전체 값인지 알 수 없어서 단가를 안 냄
    assert unit_price(clean_name(name), 19900) is None


@pytest.mark.parametrize(
    ("name", "price"),
    [
        ("LG 트롬 드럼세탁기 21kg F21VDSK", 990000),
        ("락앤락 채소탈수기 5L", 12900),
        ("필립스 에어프라이어 6.2L", 129000),
        ("삼성 비스포크 냉장고 870L", 1890000),
        ("스탠리 텀블러 퀜처 1.18L", 29900),
        ("코스트코 대용량 다용도 56L", 25900),  # 한 개 20L 넘으면 용적으로 봄
        # '에'로 시작하는 낱말(에디션·에코)은 조사 '에'가 아님
        ("스타벅스 텀블러 에디션 473ml", 29000),
        ("텀블러 에코 500ml", 19900),
        ("락앤락 트라이탄 물병 에코젠 1L", 9900),
        ("스탠리 텀블러 에코 1.18L", 29900),
        ("스탠리 클래식 보틀 1L", 39000),
    ],
)
def test_unit_price_skips_capacity_of_durables(name: str, price: int) -> None:
    assert unit_price(clean_name(name), price) is None


def test_unit_price_durables_keep_count_but_drop_capacity() -> None:
    assert unit_price("리빙박스 수납함 56L 3개", 25900) == "개당 8,633원"
    assert unit_price("종량제봉투 20L 10매", 5000) == "매당 500원"
    # 분류로도 거름 (이름에 '세탁기'가 없어도)
    assert unit_price("LG 21kg F21VDSK", 990000, category="가전디지털>세탁기") is None
    assert unit_price("스파클 생수 500ml 40병", 5900, category="식품>생수") == "병당 148원 (100ml당 30원)"


def test_unit_price_keeps_food_and_supplies_for_durables() -> None:
    # 가전에 쓰는 음식·소모품은 용량 단가 그대로
    assert unit_price("에어프라이어용 냉동감자 2kg", 9900) == "100g당 495원"
    assert unit_price("오븐구이 김 20g 10봉", 9900) == "봉당 990원 (100g당 4,950원)"
    assert unit_price("세탁조 클리너 450g 3개", 9900) == "개당 3,300원 (100g당 733원)"


@pytest.mark.parametrize(
    ("name", "price", "expected"),
    [
        # 무게로 파는 음식의 포장 (아이스박스·지퍼백·김치통)은 가전·용기가 아님
        ("꽃게 2kg (아이스박스)", 39900, "100g당 1,995원"),
        ("제주 감귤 5kg 아이스박스 포장", 19900, "100g당 398원"),
        ("냉동 블루베리 1kg 지퍼백", 12900, "100g당 1,290원"),
        ("종가집 포기김치 10kg 김치통", 59000, "100g당 590원"),
        # 가전 낱말이 음식 이름 안에 들어간 경우
        ("냉동고등어 1kg", 12900, "100g당 1,290원"),
        ("냉동고구마 2kg", 12900, "100g당 645원"),
        ("그랑데 피자 1kg", 12900, "100g당 1,290원"),
        ("오븐치킨 1kg", 12900, "100g당 1,290원"),
        ("냄비우동 212g 4개", 8000, "개당 2,000원 (100g당 943원)"),
        ("에어프라이어에 돌리는 감자 1kg", 8900, "100g당 890원"),
        ("에어프라이어에서 굽는 감자 1kg", 8900, "100g당 890원"),
        # 병으로 세는 '보틀'은 음료
        ("하이네켄 보틀 330ml 24병", 19900, "병당 829원 (100ml당 251원)"),
    ],
)
def test_unit_price_keeps_food_named_like_durables(name: str, price: int, expected: str) -> None:
    assert unit_price(clean_name(name), price) == expected
