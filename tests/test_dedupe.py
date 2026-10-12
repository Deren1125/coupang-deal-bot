"""같은 상품이 다른 글·다른 게시판으로 다시 올라오는 것을 막는다."""

from __future__ import annotations

import pytest

from dealbot.app import DealBot
from dealbot.collectors import BaseCollector, register
from dealbot.config import CollectorConfig, Settings
from dealbot.dedupe import find_duplicate, normalize_name, similarity
from dealbot.models import Product


def test_similarity_ignores_tags_prices_and_shipping_words() -> None:
    assert normalize_name("[쿠팡] 스탠리 텀블러 1.18L (29,900원/무료)") == "스탠리 텀블러 1.18l ( / )"
    assert similarity("[쿠팡] 스탠리 텀블러 1.18L 29,900원 무료배송", "스탠리 텀블러 1.18L") >= 0.8
    assert similarity("스탠리 텀블러 1.18L", "스탠리 퀜처 텀블러 1.18L") >= 0.8
    # 숫자(모델·용량·수량)가 다르면 다른 상품
    assert similarity("갤럭시 S26 자급제", "갤럭시 S25 자급제") == 0.0
    assert similarity("올챌린지 화장지 30롤", "올챌린지 화장지 24롤") == 0.0
    assert similarity("롯데온 상품 0", "롯데온 상품 1") == 0.0
    assert similarity("다이슨 에어랩", "다이슨 슈퍼소닉 드라이어") < 0.8


def test_variant_words_make_a_different_product() -> None:
    """제로·프로·무라벨·맛처럼 한쪽 이름에만 있는 변형 낱말이면 같은 상품으로 보지 않는다 (띄어쓰기·영문 표기 차이는 같은 것)."""
    assert similarity("[쿠팡] 코카콜라 350ml 24캔 (15,900원/무료)", "[쿠팡] 코카콜라 제로 350ml 24캔 (15,900원/무료)") == 0.0
    assert similarity("삼성 갤럭시 버즈3", "삼성 갤럭시 버즈3 프로") == 0.0
    assert similarity("아이폰 16 프로 256GB", "아이폰 16 프로 맥스 256GB") == 0.0
    assert similarity("갤럭시 S24+ 256GB", "갤럭시 S24 256GB") == 0.0
    assert similarity("신라면 블랙 컵 105g 12개", "신라면 컵 105g 12개") == 0.0
    assert similarity("삼다수 무라벨 2L 12병", "삼다수 2L 12병") == 0.0
    assert similarity("펩시 제로슈거 라임 355ml 24캔", "펩시 제로슈거 망고 355ml 24캔") == 0.0
    # 같은 상품의 표기 차이는 그대로 같은 것
    assert similarity("[쿠팡] 코카콜라 제로 350ml 24캔", "코카콜라제로 350ml 24캔") >= 0.8
    assert similarity("펩시 제로슈거 라임 355ml 24캔", "펩시 제로 슈거 라임 355ml 24캔") >= 0.8
    assert similarity("아이폰 16 프로 256GB", "아이폰 16 Pro 256GB") >= 0.8
    assert similarity("아이나비 블랙박스 QXD8000", "아이나비 QXD8000") >= 0.8
    assert similarity("락토핏 프로바이오틱스 60포", "락토핏 프로바이오틱스 유산균 60포") >= 0.8
    # 변형 글자가 다른 낱말 속에 있으면 변형이 아니다
    assert similarity("한우 프라임 등심 1kg", "한우 등심 1kg") >= 0.8
    assert similarity("홈플러스 신라면 120g 40개", "신라면 120g 40개") >= 0.8
    assert similarity("홈플러스 시그니처 우유 2.3L", "시그니처 우유 2.3L") >= 0.8


def test_color_only_matters_when_both_names_name_one() -> None:
    """한쪽만 색을 적은 같은 딜은 같은 것. 두 이름의 색이 서로 다르면 다른 상품. 묶음 식품의 '블랙'은 제품 라인."""
    assert similarity("[쿠팡] 에어팟 맥스 블랙", "[쿠팡] 에어팟 맥스") >= 0.8
    assert similarity("소니 WH-1000XM5 블랙", "소니 WH-1000XM5") >= 0.8
    assert similarity("스탠리 퀜처 887ml 블랙", "스탠리 퀜처 887ml") >= 0.8
    assert similarity("나이키 에어포스 1 블랙 270", "나이키 에어포스 1 270") >= 0.8
    assert similarity("블루투스 이어폰 화이트", "블루투스 이어폰") >= 0.8  # 블루투스는 색이 아님
    assert similarity("나이키 에어포스 1 블랙 270", "나이키 에어포스 1 화이트 270") == 0.0
    assert similarity("소니 WH-1000XM5 블랙", "소니 WH-1000XM5 실버") == 0.0
    assert similarity("삼성 비스포크 냉장고 화이트", "삼성 비스포크 냉장고 그레이") == 0.0
    assert similarity("농심 신라면 블랙 134g 32봉", "농심 신라면 134g 32봉") == 0.0
    # 브랜드 이름 속 색 글자는 색이 아니다 (레드미·블루독·그린핑거)
    assert similarity("샤오미 레드미 노트 13 블랙", "샤오미 레드미 노트 13 실버") == 0.0
    assert similarity("블루독 패딩 점퍼 네이비 110", "블루독 패딩 점퍼 베이지 110") == 0.0
    assert similarity("그린핑거 유아 로션 핑크 320ml", "그린핑거 유아 로션 화이트 320ml") == 0.0
    assert similarity("샤오미 레드미 노트 13 블랙", "샤오미 레드미 노트 13") >= 0.8
    assert similarity("샤오미 레드미 13C 블랙", "샤오미 레드미 13C 블루") == 0.0
    # 브랜드가 아닌 '레드 미드'·'레드 미니' 의 레드는 색이다
    assert similarity("Nike Air Force 레드 미드", "Nike Air Force 블랙 미드") == 0.0
    assert similarity("나이키 덩크 레드 미니", "나이키 덩크 블랙 미니") == 0.0


def test_count_unit_spelling_is_the_same_number() -> None:
    """24개·24병·24캔·24개입, '2Lx24개' 는 같은 수량. 묶음(팩·롤)이나 다른 수는 그대로 다른 상품."""
    assert similarity("풀무원 샘물 2L 24개", "풀무원 샘물 2L 24병") >= 0.8
    assert similarity("코카콜라 제로 355ml 24캔", "코카콜라 제로 355ml 24개") >= 0.8
    assert similarity("농심 신라면 120g 40개입", "농심 신라면 120g 40봉") >= 0.8
    assert similarity("풀무원 샘물 2L 24개", "풀무원 샘물 2Lx24개") >= 0.8
    assert similarity("햇반 210g 24개", "햇반 210gX24개") >= 0.8
    assert similarity("햇반 210g 24개", "햇반 210g 12개") == 0.0
    assert similarity("햇반 210g 24개", "햇반 130g 24개") == 0.0
    assert similarity("풀무원 샘물 2L 24팩", "풀무원 샘물 2L 24개") == 0.0


def test_find_duplicate_allows_clearly_cheaper_deal() -> None:
    recent = [{"product_id": "coupang:1", "name": "스탠리 텀블러 1.18L", "price": 29900, "posted_at": "2026-09-11T10:00:00+00:00"}]
    dup = find_duplicate("[네이버] 스탠리 텀블러 1.18L", 29900, recent)
    assert dup and dup["product_id"] == "coupang:1" and dup["similarity"] >= 0.8
    assert find_duplicate("스탠리 텀블러 1.18L", 25000, recent) is None  # 16% 싸짐 → 새 딜
    assert find_duplicate("스탠리 텀블러 1.18L", 25000, recent, allow_cheaper=False)  # 대기 중인 글과 겹치면 가격과 무관하게 중복
    assert find_duplicate("다이슨 에어랩", 25000, recent) is None


@register("fake_dedupe")
class FakeCollector(BaseCollector):
    products: list[Product] = []

    async def collect(self) -> list[Product]:
        return list(FakeCollector.products)


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = [CollectorConfig(name="fake", type="fake_dedupe", interval_minutes=1)]
    settings.publish.min_interval_seconds = 0
    settings.publish.max_per_hour = 20
    FakeCollector.products = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _deal(pid: str, shop: str, name: str, price: int, url: str) -> Product:
    return Product(source="fake", product_id=pid, shop=shop, name=name, price=price, url=url, recommend_count=9, discount_rate=50)


async def test_same_product_from_another_board_is_not_queued_twice(bot: DealBot) -> None:
    FakeCollector.products = [
        _deal("coupang:1", "coupang", "[쿠팡] 스탠리 텀블러 1.18L", 29900, "https://www.coupang.com/vp/products/1"),
        _deal("coupang:2", "coupang", "스탠리 텀블러 1.18L 무료배송", 29900, "https://www.coupang.com/vp/products/2"),  # 다른 주소, 같은 상품
        _deal("coupang:3", "coupang", "스탠리 텀블러 0.7L", 24900, "https://www.coupang.com/vp/products/3"),  # 용량이 다르면 다른 상품
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"pending": 2}
    names = sorted(c["name"] for c in bot.db.open_queue_names())
    assert names == ["[쿠팡] 스탠리 텀블러 1.18L", "스탠리 텀블러 0.7L"]
    events = [e for e in bot.db.recent_events(10) if e["kind"] == "dedupe"]
    assert events and "대기 중인 #1" in events[0]["message"]

    # 올린 뒤에는 같은 이름이 다시 들어오지 않지만, 확실히 싸진 딜은 들어온다
    for _ in range(2):
        assert await bot.process_queue_once()
    assert bot.db.queue_counts() == {"published": 2}
    FakeCollector.products = [
        _deal("coupang:4", "coupang", "스탠리 텀블러 1.18L", 29900, "https://www.coupang.com/vp/products/4"),
        _deal("coupang:5", "coupang", "스탠리 텀블러 1.18L", 24900, "https://www.coupang.com/vp/products/5"),
    ]
    await bot.run_collector(bot.collectors[0])
    assert bot.db.queue_counts() == {"published": 2, "pending": 1}
    assert bot.db.open_queue_names()[0]["product_id"] == "coupang:5"
