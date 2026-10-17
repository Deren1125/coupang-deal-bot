"""설정 로딩.

- 동작 설정은 config.yaml (비밀값 없음)
- 비밀값(API 키/토큰)은 환경변수 또는 .env
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

from dealbot import __version__
from dealbot.shops import DEFAULT_SHOPS, LINK_MODES, Shop, ShopRegistry


class AppConfig(BaseModel):
    timezone: str = "Asia/Seoul"
    log_level: str = "INFO"
    scheduler_tick_seconds: int = 30
    prune_price_history_days: int = 180
    prune_events_days: int = 30


class HttpConfig(BaseModel):
    timeout_seconds: float = 20
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    # 봇이라고 밝힌다: 브라우저 UA 로 꾸미지 않는다 (운영 원칙 — UA·지문 위장 없음)
    user_agent: str = f"dealbot/{__version__}"


class CollectorConfig(BaseModel):
    name: str
    type: str
    label: str | None = None  # 관리자 챗에 보여줄 이름 (없으면 내장 표시명 → name)
    enabled: bool = True
    interval_minutes: int = 60
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("interval_minutes")
    @classmethod
    def _positive_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval_minutes must be >= 1")
        return v


class ShopConfig(BaseModel):
    """config.yaml 의 shops[] 항목. key 만 있으면 기본값을 덮어쓴다."""

    key: str
    name: str | None = None
    aliases: list[str] | None = None
    domains: list[str] | None = None
    link_mode: str | None = None
    provider: str | None = None
    disclosure: str | None = None
    enabled: bool | None = None
    manual_hint: str | None = None
    manual_fallback: bool | None = None
    requires_provider: bool | None = None  # provider(링크프라이스 등)가 설정돼 있을 때만 켜짐

    @field_validator("link_mode")
    @classmethod
    def _mode(cls, v: str | None) -> str | None:
        if v is not None and v not in LINK_MODES:
            raise ValueError(f"link_mode must be one of {LINK_MODES}")
        return v


class CoupangConfig(BaseModel):
    """파트너스 API 호출 예산 (시간당). 0 이면 무제한."""

    max_calls_per_hour: int = 10
    deeplink_reserve: int = 3  # 발행용 딥링크 호출을 위해 남겨 둘 몫
    deeplink_max_per_hour: int = 7  # 딥링크가 시간당 예산을 다 쓰지 못하게 (골드박스·시중가 조회 몫을 남김). 넘치는 쿠팡 딜은 기다림


class MarketCheckConfig(BaseModel):
    """(d) 시중가 대조: 다른 몰의 딜을 쿠팡 검색 API 로 찾은 같은 상품 가격과 비교."""

    enabled: bool = True
    min_below_market_pct: float = 20  # 쿠팡가보다 N% 이상 싸면 특가
    strict: bool = True  # 대조 결과가 있으면 N% 미만은 무조건 탈락 (false: 0~N% 사이는 (b)/(c) 보조 규칙으로 판정)
    veto_if_not_cheaper: bool = True  # (strict 가 아닐 때) 쿠팡가보다 싸지 않으면 (a)/(c) 로 잡혀도 탈락
    require_for_discount_rule: bool = True  # 대조가 가능한 환경이면 (a) 표시 할인율만으로는 통과 못 함
    max_checks_per_hour: int = 3  # 쿠팡 검색 API 호출 예산 (전체 예산 안에서)
    cache_hours: int = 24
    min_token_match: float = 0.8  # 상품명 토큰 일치 비율
    require_first_token: bool = True  # 상품명 첫 낱말(대개 브랜드)이 후보 이름에 있어야 같은 상품으로 봄


class NaverReferenceConfig(BaseModel):
    """쿠팡 딜(골드박스·카테고리 베스트)의 가격 근거: 네이버 쇼핑 검색(공식 Open API) 최저가와 비교 (주인 결정 10/13).
    골드박스 응답엔 정가·할인율이 없어 표시 할인율 규칙으로는 통과할 수 없었다. 같은 상품이 확실할 때만 근거로 쓰고,
    헷갈리면(수량·규격이 다름, 중고·렌탈·해외직구·옵션 목록·부속품, 판매글이 적음) 근거 없음으로 둔다 (할인을 지어내지 않음).
    NAVER_CLIENT_ID/NAVER_CLIENT_SECRET(developers.naver.com 검색 API 키)이 없거나 401 이면 이 비교만 꺼짐 (로그 한 번).
    네이버 쇼핑 검색 Open API 는 2026-07-31 종료 (대신할 API 없음) — 운영 config.yaml 은 꺼 둠."""

    enabled: bool = True
    sources: list[str] = Field(default_factory=lambda: ["goldbox", "category_best"])  # 이 수집기의 쿠팡 상품만 (비우면 모든 쿠팡 상품)
    min_below_pct: float = 12  # 네이버 최저가보다 N% 이상 싸면 특가
    strict: bool = False  # True 면 0~N% 사이도 탈락 (False: 가격 이력 규칙으로 넘어감)
    veto_if_not_cheaper: bool = True  # 네이버 최저가보다 싸지 않으면 다른 규칙으로 잡혀도 탈락
    # 같은 상품을 다시 찾지 않는 시간 (못 찾음 포함). 찾은 값은 price_cache_hours 가 지나면 다시 찾되, 그때 한도·오류로 못 찾으면
    # 이 시간 안의 지난 값으로 '네이버가 더 쌈' 탈락만 지킨다 ('N% 싸요' 근거로는 안 씀 — 지난 금액이니까)
    cache_hours: float = 24
    price_cache_hours: float = 6  # 찾은 최저가는 이만큼만 근거로 (가격이 자주 바뀜 — 글에 '네이버 최저가보다 N%' 를 쓰니까)
    # 한도: 카테고리 베스트 한 판(3개 x 30 = 90개)도 고르게 보게 (리뷰 10/13: 15회로는 첫 카테고리만 봄).
    # 골드박스 2시간마다 ~28개 + 카테고리 3시간마다 45회 → 시간당 평균 30회쯤, 하루 700회쯤 (네이버 허용 25,000회)
    max_lookups_per_run: int = Field(45, ge=0)  # 수집 한 판에 검색 API 호출 수
    max_lookups_per_hour: int = Field(90, ge=0)  # 한 시간에 (하루 25,000회 허용이지만 점잖게)
    run_seconds: float = Field(60.0, ge=0)  # 수집 한 판에 네이버 조회에 쓰는 시간 상한 (5분 수집 루프를 오래 막지 않게)
    timeout_seconds: float = Field(8.0, gt=0)  # 호출 한 번의 시간 제한
    min_interval_seconds: float = Field(0.5, ge=0)  # 호출 사이 간격
    min_token_match: float = 0.8  # 쿠팡 상품명 낱말이 네이버 상품명에 있는 비율 (수량·용량은 반드시 같아야 함)
    require_first_token: bool = True  # 첫 낱말(대개 브랜드)이 있어야 같은 상품
    # 가격비교(카탈로그) 상품 없이 일반 판매글만 맞았을 때: 서로 다른 판매처가 이만큼은 돼야
    # 탈락(이만큼이 쿠팡 값 이하)·근거(낮은 가격순 확인과 함께)로 씀. 판매글 한 곳의 값으로는 둘 다 안 함
    min_listings: int = Field(2, ge=1)
    # 일반 판매글 값(lprice)은 배송비를 뺀 값이라, 탈락 판단 때만 이만큼 더해 본다 (쿠팡은 대개 로켓·무료배송).
    # 예: 진라면 11,500원 + 배송비 3,500원 판매글은 쿠팡 11,900원 로켓배송보다 싸다고 보지 않음
    general_shipping_allowance: int = Field(3000, ge=0)
    # 가격비교 상품 없이 판매글만 맞았는데 쿠팡이 기준 넘게 싸 보이면, 낮은 가격순으로 한 번 더 찾아 정말 최저가인지 확인
    # (관련도순 40개 밖에 더 싼 같은 상품이 있을 수 있음). 끄거나 확인을 못 하면 판매글만으로는 'N% 싸요' 근거로 안 씀
    confirm_lowest: bool = True
    # 같은 상품 판매글이 쿠팡 값의 이 배보다 싸면 낱개·옵션·부속 같은 다른 상품일 수 있어 근거도 탈락도 없음 (확실하지 않음)
    min_price_ratio: float = 0.5
    # 쿠팡이 네이버 최저가보다 이만큼 넘게 싸면 다른 상품(묶음 수 차이 등)일 가능성이 커서 근거로 안 씀
    max_below_pct: float = 70


class InterestConfig(BaseModel):
    """관심도 게이트: 모든 상품을 대조할 수 없으니 '사람들이 관심 있어 보이는' 딜만 판정 대상으로.
    아래 신호 중 하나라도 넘으면 통과. 값이 없는 신호는 무시."""

    enabled: bool = True
    min_recommend: int = 1  # 커뮤니티 추천
    min_comments: int = 3  # 커뮤니티 댓글
    min_views: int = 500  # 커뮤니티 조회수
    max_rank: int = 30  # API 목록(골드박스/카테고리 베스트) 순위
    always_pass_sources: list[str] = Field(default_factory=lambda: ["manual", "adpick"])


class EnrichConfig(BaseModel):
    """상품 페이지(OG/JSON-LD)에서 이미지·가격·별점·리뷰 수를 읽어 빈 칸을 채운다."""

    enabled: bool = True
    shops: list[str] = Field(default_factory=lambda: ["toss", "naver", "oliveyoung", "kurly", "musinsa"])  # "*" = 모든 몰
    exclude_shops: list[str] = Field(default_factory=lambda: ["coupang"])  # 페이지를 못 읽는 몰 (쿠팡은 API 사진을 씀)
    max_per_run: int = 10
    # 네이버 스마트스토어·브랜드스토어는 몰아서 읽으면 429 를 준다: 요청 사이 간격, 429·5xx 재시도(Retry-After 우선, 없으면 지수 백오프+지터)
    store_min_interval_seconds: float = 3.0
    store_retry_attempts: int = 3
    store_retry_backoff_seconds: float = 2.0
    store_max_wait_seconds: float = 30.0  # Retry-After 가 이보다 길면 기다리지 않고 건너뜀 (그 시각까지 스토어에 안 감)
    # 재시도까지 다 막힌 판이 이만큼 연속이면 스토어 전체를 쉼 (breaker_seconds 부터 두 배씩, 최대 breaker_max_seconds. 0 = 안 쉼)
    store_breaker_after: int = 2
    store_breaker_seconds: float = 600.0
    store_breaker_max_seconds: float = 7200.0
    store_cache_seconds: float = 300.0  # 읽은 스토어 페이지를 이만큼 기억 (수집·정품 확인·발행 사진이 같은 페이지를 다시 안 읽게)
    # 일반 요청이 막힌 네이버 스토어 페이지를 실제 크롬(로그인 없는 빈 창)으로 한 번 열어 가격을 읽음 (10/10 엔 사진용, 10/15 부터
    # 네이버 딜 사진은 주인이 보낸 사진만이라 크롬은 가격 확인용 — 수집 때 대조·쇼핑커넥트 전·발행 직전)
    store_browser: bool = False
    store_browser_min_interval_seconds: float = 60.0  # 열기 사이 간격
    store_browser_min_free_mb: int = 500  # 서버 여유 메모리가 이보다 적으면 열지 않음 (블로그 크롬이 떠 있을 때 등)
    store_browser_timeout_seconds: float = 45.0


class AuthenticityConfig(BaseModel):
    """정품·공식 판매처 확인. 오픈마켓 딜은 공식 표시가 있어야 자동으로 올리고, 병행수입 표시가 있으면 올리지 않는다."""

    enabled: bool = True
    # 제목·게시글·상품 페이지 어디에든 이 낱말이 보이면 올리지 않음
    reject_keywords: list[str] = Field(
        default_factory=lambda: [
            "병행수입", "병행 수입", "병행", "구매대행", "해외구매대행", "리퍼", "리퍼비시", "리퍼상품", "리퍼제품", "리퍼폰",
            "중고", "중고품", "중고상품", "벌크", "벌크포장", "전시상품", "전시품", "반품상품", "박스훼손", "짝퉁", "이미테이션",
            "레플리카", "스크래치",
        ]
    )
    # 누구나 팔 수 있는 몰: 제목·게시글·판매자명에 공식 표시가 있어야 자동으로 올림
    open_markets: list[str] = Field(default_factory=lambda: ["gmarket", "auction", "11st", "lotteon", "ssg", "aliexpress", "temu", "ohouse", "naver", "toss"])
    official_markers: list[str] = Field(
        default_factory=lambda: ["공식", "브랜드관", "브랜드 스토어", "브랜드스토어", "본사", "직영", "official store", "official"]
    )
    # 오픈마켓인데 공식 표시를 못 찾았을 때: review = 관리자 확인(/ok) 후 올림 / skip = 올리지 않음 / allow = 그냥 올림
    unverified_action: Literal["review", "skip", "allow"] = "review"
    # 후기가 이만큼 쌓인 상품은 공식 표시가 없어도 통과 (오래 팔린 정상 판매처로 봄). 0 이면 안 씀
    trusted_reviews: int = 100
    # 쿠팡은 판매자를 봇이 못 가리므로 후기 양으로: 후기 수를 알았는데 이보다 적으면 올리지 않음 (0 이면 안 씀, 모르면 통과)
    coupang_min_reviews: int = 30


class FoodRuleConfig(BaseModel):
    """식품류는 평소 가격(쿠팡 시중가·30일 평균) 대비 이만큼은 싸야 특가로 본다. 그 외 상품은 기존 기준 그대로."""

    enabled: bool = True
    min_below_reference_pct: float = 50
    # 상품 분류(쿠팡 API categoryName, 루리웹 말머리)에 이 말이 있으면 식품
    categories: list[str] = Field(
        default_factory=lambda: ["식품", "음식", "먹거리", "간식", "음료", "신선", "가공식품", "건강식품", "건강기능식품", "냉동", "축산", "수산", "과일", "채소", "쌀", "베이커리", "카페"]
    )
    # 상품명에 이 말이 있으면 식품
    keywords: list[str] = Field(
        default_factory=lambda: [
            "즙", "과자", "라면", "커피", "원두", "음료", "우유", "두유", "잡곡", "현미", "쌀", "고기", "닭가슴살", "닭", "돼지", "삼겹", "소고기", "한우",
            "김치", "만두", "빵", "떡", "과일", "사과", "귤", "포도", "견과", "아몬드", "호두", "참치", "스팸", "햄", "소시지", "계란", "달걀",
            "생수", "탄산", "콜라", "사이다", "주스", "요거트", "치즈", "버터", "초콜릿", "초코", "사탕", "젤리", "미역", "국물", "곰탕", "삼계탕",
            "찌개", "카레", "소스", "간장", "고추장", "된장", "식용유", "올리브유", "시리얼", "그래놀라", "프로틴", "단백질", "비타민", "홍삼",
            "오메가", "유산균", "영양제", "육포", "어묵", "핫도그", "피자", "치킨", "밀키트", "식품", "간식", "도시락", "컵밥", "볶음밥", "누룽지",
            "떡볶이", "라떼", "녹차", "티백", "홍차", "보리차", "꿀", "잼", "시럽", "육수", "다시", "김자반", "조미김", "돌김", "새우", "오징어", "연어",
            "갈아만든", "식혜", "수정과", "에이드", "탄산수", "생과일", "냉동", "즉석", "레토르트", "국밥", "죽", "면", "파스타", "스파게티",
            # 신선 채소·과일 (매일 값이 오르내려 '평소보다 싸다'가 의미 없는 경우가 많음)
            "애호박", "호박", "고추", "청양", "오이", "양파", "대파", "쪽파", "마늘", "감자", "고구마", "당근", "배추", "양배추", "상추", "양상추",
            "깻잎", "시금치", "청경채", "브로콜리", "버섯", "토마토", "방울토마토", "멜론", "수박", "참외", "바나나", "딸기", "블루베리", "키위",
            "오렌지", "레몬", "자몽", "샤인머스캣", "복숭아", "자두", "로켓프레시", "국산", "국내산",
        ]
    )
    # "340ml 24개", "500g 3팩" 처럼 식품·음료 묶음 규격 (아래 non_food_keywords 가 있으면 제외)
    unit_patterns: list[str] = Field(
        default_factory=lambda: [
            r"\d+(?:\.\d+)?\s*(?:ml|l|리터)\s*[x×*]?\s*\d+\s*(?:개|입|병|캔|팩|봉)",
            r"\d+(?:\.\d+)?\s*(?:g|kg|그램)\s*[x×*]?\s*\d+\s*(?:개|입|팩|봉|봉지)",
        ]
    )
    non_food_keywords: list[str] = Field(
        default_factory=lambda: [
            "샴푸", "린스", "트리트먼트", "세제", "화장지", "물티슈", "로션", "크림", "클렌징", "세정", "섬유유연제", "치약", "바디워시",
            "핸드워시", "향수", "스킨", "토너", "에센스", "선크림", "마스크팩", "세럼", "샤워", "주방세제", "표백", "락스", "탈취", "방향제",
        ]
    )


class QualityConfig(BaseModel):
    """후기가 거의 없는 상품은 특가로 올려도 잘 안 팔린다. 상품 페이지에서 후기 수를 읽었을 때만 적용 (못 읽으면 통과)."""

    enabled: bool = True
    min_review_count: int = 10
    shops: list[str] = Field(default_factory=list)  # 비우면 전체 쇼핑몰


class SoldOutConfig(BaseModel):
    """품절/종료된 딜 정리.
    - 게시판을 다시 읽을 때 제목에 품절 표시가 붙은 글 → 기다리는 글에서 내리고, 링크 요청 메시지를 고치고, 채널 글에 품절 표시
    - 토스/네이버처럼 상품 페이지를 읽을 수 있는 몰 → 링크 요청 전·발행 전에 재고 확인, 링크를 기다리는 동안 N분마다 재확인"""

    enabled: bool = True
    words: list[str] = Field(default_factory=list)  # 비우면 내장 목록(품절/매진/완판/재고소진/판매종료 + 종료/마감)
    check_page_before_link_request: bool = True
    check_page_before_publish: bool = True
    recheck_awaiting_minutes: int = 5
    mark_published_hours: int = 24  # 올린 지 N시간 안의 채널 글만 품절 표시를 붙임


class PageCheckConfig(BaseModel):
    """상품 페이지 가격 대조 (주인 결정 10/12, 큐 #22). 수집 때 읽는 상품 페이지의 가격·옵션을 제목 가격과 맞춰 본다.
    - 제목 가격 ±tolerance_pct 안의 페이지 가격(판매가·할인가·쿠폰가·정가·옵션가·배송비 포함가·2~4개 묶음가 중 아무거나)이
      있으면 그 값으로 진행 (시중가 대조도 그 값으로). 다른 옵션에 맞으면 옵션 이름을 남김
    - 맞는 값의 옵션 수량이 제목과 다르거나(24개 ↔ 12개) 맞는 값이 없으면 보류(held): 링크 요청·발행 없이 관리자 챗에 짧은 메모
    - 페이지를 못 읽었거나 가격이 없으면(토스 웹 페이지는 늘 가격 없음) 지금처럼 진행하고 로그만 (상품마다 하루 한 번)
    - 일반 요청이 막힌 네이버 상품 페이지는 사진용 크롬으로 (browser, 한도·쉬는 시간은 아래 browser_*, page_reader 참고)"""

    enabled: bool = True
    tolerance_pct: float = 10
    # 제목에 카드·쿠폰·적립·멤버십 같은 말이 있으면 글 가격이 페이지에 안 보이는 할인 뒤 값일 수 있어,
    # 글 가격이 페이지 가격보다 이만큼(%)까지 싼 것도 맞은 것으로 본다 (한쪽만. 0 = 안 씀)
    discount_tolerance_pct: float = 25
    shops: list[str] = Field(default_factory=lambda: ["toss", "naver"])
    # 일반 요청이 막힌(429·403·빈 페이지) 네이버 상품 페이지(스마트스토어·브랜드스토어·쇼핑 상품 페이지)를 사진용 크롬
    # (enrich.store_browser 가 켜져 있어야 함)으로 읽어 대조. 꺼져 있으면 예전처럼 '확인 못 함'
    browser: bool = False
    browser_per_run: int = Field(1, ge=0)  # 수집 한 판에 크롬으로 읽는 페이지 수 (크롬이 바쁘거나 열기 간격 안이면 기다리지 않고 다음 판에)
    browser_per_hour: int = Field(12, ge=0)  # 한 시간에 (모든 수집기 합)
    browser_timeout_seconds: float = Field(40.0, gt=0)  # 한 번 읽기(크롬 띄우기~닫기) 시간 제한
    # 크롬도 막히면(429·403·캡차·빈 페이지) 크롬 읽기를 쉬는 시간 (사진용도 같이). 또 막히면 두 배씩, 최대 max.
    # 크롬으로 열었는데 못 읽은 상품(시간 초과·로그인 등 다른 페이지로 넘어감)은 그 상품만 browser_cooldown_minutes 뒤에 다시,
    # 또 못 읽으면 두 배씩 (최대 6시간). 크롬도 막힌 상품은 처음부터 두 배 — 쉬는 시간이 끝나면 다른 상품부터 읽게
    browser_cooldown_minutes: float = Field(30.0, ge=0)
    browser_cooldown_max_minutes: float = Field(60.0, ge=0)
    cache_minutes: float = Field(120.0, ge=0)  # 읽은 페이지 가격·옵션을 상품마다 기억 (그동안 일반 요청·크롬 다시 안 함. 0 = 안 씀)
    unverified_log_hours: float = Field(24.0, ge=0)  # '확인 못 함' 로그는 상품마다 이 시간에 한 번


class SourceRule(BaseModel):
    """수집원별 기준 재정의. 사이트마다 추천 수의 의미가 달라서 필요하다.
    (예: 루리웹 핫딜 게시판은 추천이 후하고, 뽐뿌는 짜다)"""

    community_min_recommend: int | None = None
    coupon_min_recommend: int | None = None
    interest_min_recommend: int | None = None
    interest_min_comments: int | None = None
    interest_min_views: int | None = None
    discount_alone: bool | None = None  # (a) 표시 할인율 단독 통과 허용 (None 이면 전체 설정)
    min_discount_rate: float | None = None  # (a) 기준 재정의


class DealConfig(BaseModel):
    enrich: EnrichConfig = Field(default_factory=EnrichConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    authenticity: AuthenticityConfig = Field(default_factory=AuthenticityConfig)
    food: FoodRuleConfig = Field(default_factory=FoodRuleConfig)
    # 원본 주소가 쇼핑몰이 아니라 게시판 글이면(본문에서 상품 링크를 못 찾은 이벤트 글 등) 링크 요청 없이 건너뜀
    require_shop_url: bool = True
    sold_out: SoldOutConfig = Field(default_factory=SoldOutConfig)
    page_check: PageCheckConfig = Field(default_factory=PageCheckConfig)
    per_source: dict[str, SourceRule] = Field(default_factory=dict)
    interest: InterestConfig = Field(default_factory=InterestConfig)
    market: MarketCheckConfig = Field(default_factory=MarketCheckConfig)
    naver_reference: NaverReferenceConfig = Field(default_factory=NaverReferenceConfig)
    min_discount_rate: float = 50
    history_days: int = 30
    min_below_average_pct: float = 15
    min_history_samples: int = 3
    min_history_days: float = 0  # (b) 가격 이력이 최소 며칠에 걸쳐 쌓였을 때만 (하루치 기록으로 '평소 가격'을 단정하지 않게)
    near_low_pct: float | None = None  # (b) 최근 최저가 대비 이 % 이내(또는 더 쌈)일 때만 — 이력 최저보다 비싸면 특가 아님
    # (b') 기록 최저가 이하(최근 history_days 일 중 제일 쌈)이면서 평균보다 이만큼 싸면 특가 (기록 조건은 (b)와 같음). None = 안 씀
    new_low_below_avg_pct: float | None = None
    observation_min_gap_hours: float = 6  # 같은 상품 가격 기록은 이 간격으로만 (재판정 때 중복 기록 방지)
    min_price: int = 1000
    # (c) 추천 수만으로는 통과 못 하게: 가격 근거(평균 대비 support_below_avg_pct% 이상 또는 표시 할인율 (a))가 같이 있어야 함
    recommend_needs_support: bool = False
    support_below_avg_pct: float = 5
    # 추천이 이만큼 몰리면(커뮤니티 검증) 가격 근거 없이도 인정 (0 이면 끔)
    community_strong_recommend: int = 0
    # (a) 표시 할인율만으로 통과 허용 여부 (쇼핑몰 정가는 부풀려진 경우가 많아 끄는 걸 권장)
    discount_alone: bool = True
    exclude_keywords: list[str] = Field(default_factory=list)
    # (c) 커뮤니티 추천 수가 이 값 이상이면 가격 조건과 무관하게 특가로 인정 (0 이면 비활성)
    community_min_recommend: int = 5
    # 가격이 없는 글(쿠폰/이벤트/공지)은 노이즈가 많아 기준을 따로 높게 둔다.
    # 커뮤니티에서 추천이 몰리는 글은 대개 '공짜 이벤트'라 상품 특가와 성격이 다르다.
    coupon_min_recommend: int = 20
    # 쿠폰/이벤트(가격 없음) 글을 다룰지. 다루면 규칙 (c) 로만 판정
    accept_coupons_and_events: bool = True


class CommentaryConfig(BaseModel):
    """딜 한줄평 (서버에 로그인된 Claude Code CLI 사용). 없거나 실패하면 한줄평 없이 올림."""

    enabled: bool = True
    # CLI 모델 이름은 저장소에 두지 않는다: 서버 .env 의 DEALBOT_MODEL_COMMENTARY. 없으면 --model 없이 (CLI 기본 모델)
    model: str | None = None
    timeout_seconds: float = 90


def _window(v: str) -> str:
    """'HH:MM-HH:MM' (끝은 24:00 까지). 틀리면 ValueError."""
    start, sep, end = str(v).partition("-")
    if not sep:
        raise ValueError("시간대는 'HH:MM-HH:MM'")
    for i, t in enumerate((start, end)):
        hh, _, mm = t.strip().partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(mm) < 60 and (0 <= int(hh) < 24 or (i == 1 and t.strip() == "24:00"))):
            raise ValueError(f"시간대 '{v}' 는 'HH:MM-HH:MM'")
    return f"{start.strip()}-{end.strip()}"


class WeeklyTopConfig(BaseModel):
    """일요일 텔레그램 'TOP5 + 투표 결과' 글 (주인 결정 10/15, 2단계): 지난 7일 채널 딜 중 점수 높은 5개 + 스레드 투표 결과.
    window 안의 아무 분에 한 번 (보여 줄 것이 있을 때만)."""

    enabled: bool = True
    weekday: int = Field(6, ge=0, le=6)  # 0=월 … 6=일
    window: str = "20:00-20:59"
    top_n: int = Field(5, ge=1, le=10)

    @field_validator("window")
    @classmethod
    def _win(cls, v: str) -> str:
        return _window(v)


class PublishConfig(BaseModel):
    enabled: bool = True
    dry_run: bool = False
    max_per_hour: int = 6
    max_per_day: int = 40
    min_interval_seconds: int = 180
    dedup_days: int = 7
    # 이름이 거의 같은 상품(다른 게시판·주소 변형)을 같은 딜로 보는 기준 (0~1). 전보다 dedupe_cheaper_pct% 이상 싸졌으면 새 딜로 인정
    dedupe_similarity: float = 0.8
    dedupe_cheaper_pct: float = 10
    # 글의 강조 단계: 평소 가격(30일 평균·쿠팡 시중가) 대비 must_pct% 이상 싸면 "꼭 담으세요", top_pct% 이상이면 "역대급"
    emphasis_must_pct: float = 50
    emphasis_top_pct: float = 70
    queue_ttl_hours: int = 6
    manual_link_ttl_hours: int = 24  # 내 링크 입력을 기다리는 항목의 유효 시간 (주인 결정 10/15: 12 → 24)
    # 정보 글·정품 확인(/ok)과 쇼핑커넥트 자동 링크를 기다리는 항목의 유효 시간. 링크 요청만 24시간으로 늘렸으니 이것은 예전 그대로
    # (하루 지난 정보 글이 /ok 로 올라가거나 확인 요청이 자리를 오래 잡지 않게)
    review_ttl_hours: int = 12
    max_publish_attempts: int = 3
    publisher_tick_seconds: int = 20
    send_photo: bool = True
    photo_min_side: int = 600  # 사진의 짧은 변이 이보다 작으면(목록 썸네일·저해상도) 상품 페이지 사진을 보고, 그래도 없으면 사진 없이
    # 이 시간대(현지 시각)에는 채널 글을 무음으로 올린다 (구독자 알림 없이). 예: "00:00-07:00". 비우면 항상 알림
    quiet_hours: str | None = None
    # 내 링크를 기다리는 글이 이 개수 이상 쌓이면, 새 링크 요청은 보내지 않고 건너뛴다 (관리자 챗 폭주 방지). 0 = 제한 없음
    # (주인 결정 10/15: 6 → 15). 자리가 다 찼을 때 새 딜의 점수가 기다리는 글 중 가장 낮은 점수보다 swap_min_score_gain 이상
    # 높으면 그 글을 내리고 새 딜을 묻는다 (점수 = 대기열 점수, 판정의 DealVerdict.score). null 이면 바꾸지 않고 예전처럼 건너뜀
    max_awaiting_links: int = 15
    swap_min_score_gain: float | None = 5
    # 정보 글·정품 확인(/ok)을 기다리는 글의 자리 (링크 요청 자리와 따로). 링크 자리를 15로 늘려도 확인 요청은 6개까지만
    # (관리자 챗 폭주 방지). 링크 자리와 합쳐 세면 교체로 링크 15칸이 늘 차 있어 확인 요청을 하나도 못 보냄. 0 = 제한 없음
    max_awaiting_reviews: int = 6
    allow_raw_links: bool = True  # 제휴 변환이 불가능한 쇼핑몰은 원본 링크로라도 발행
    templates_dir: Path = Path("templates")
    template: str = "deal_post.j2"
    commentary: CommentaryConfig = Field(default_factory=CommentaryConfig)
    # 확인된 상품 사진 없이 올라간 딜 (주인 결정 10/15): 관리자 챗에 '📷 사진 필요' 를 보내고 이 시간 안에 사진을 답장하면
    # 그 사진을 블로그·인스타 사진으로 (Blog-Auto 는 published_deals.jsonl 의 photo_wait_until 까지 기다렸다가 없으면 글자 카드로)
    late_photo: bool = True
    late_photo_hours: float = Field(4.0, gt=0, le=24)
    weekly_top: WeeklyTopConfig = Field(default_factory=WeeklyTopConfig)


class ThreadsRhythmConfig(BaseModel):
    """스레드 글 리듬 (주인 결정 10/15): 07:00~24:00 에만, 글 사이 45분 이상, 딜 글은 하루 10개까지.
    텔레그램에 올린 딜은 스레드 대기열(DB)에 넣고, 리듬이 허락할 때 점수 높은 것부터 올린다. 기다린 글은 올리기 직전에 품절을 다시 본다."""

    enabled: bool = True  # false 면 예전처럼 텔레그램 발행 직후 바로 (대기열 없이)
    hours: str = "07:00-24:00"  # 이 시간대(app.timezone)에만 올림
    min_gap_minutes: float = Field(45, ge=0)
    max_deal_posts_per_day: int = Field(10, ge=0)  # 0 = 제한 없음
    queue_ttl_hours: float = Field(8, gt=0)  # 이보다 오래 기다린 글은 내림 (값·재고가 바뀌었을 수 있음)
    recheck_after_minutes: float = Field(10, ge=0)  # 이만큼 넘게 기다린 딜은 올리기 직전에 품절 다시 확인

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: str) -> str:
        return _window(v)


class ThreadsRepliesConfig(BaseModel):
    """내 스레드 글에 달린 답글 → 관리자 챗에 '원글 요약 + 답글 + 답변 초안 2개' (주인 결정 10/15: 하루 두 번 모아서, 자동 답글 없음).
    threads_read_replies 권한이 있는 토큰일 때만 (없으면 조용히 건너뜀)."""

    enabled: bool = True
    windows: list[str] = Field(default_factory=lambda: ["12:30-12:59", "20:30-20:59"])  # 날마다 이 안의 아무 분에 한 번씩
    lookback_hours: float = Field(48, gt=0)  # 이 시간 안에 올린 내 글의 답글만
    max_posts: int = Field(20, ge=1)  # 한 번에 답글을 읽는 내 글 수 (API 호출 수)
    max_messages: int = Field(15, ge=1)  # 한 번에 관리자 챗으로 보내는 답글 수 (넘치면 다음 번에)

    @field_validator("windows")
    @classmethod
    def _windows(cls, v: list[str]) -> list[str]:
        return [_window(x) for x in v]


class ThreadsPollConfig(BaseModel):
    """딜 글이 없는 날의 스레드 투표 (주인 결정 10/15, 2단계): after 시각까지 스레드 딜 글이 하나도 없으면 링크 없는 투표 글 하나
    (공식 poll_attachment, 하루 한 번까지). 결과는 일요일 텔레그램 'TOP5 + 투표 결과' 에 (publish.weekly_top)."""

    enabled: bool = True
    after: str = "15:00"
    question: str = "다음 주 뭐부터 찾아볼까?"
    topic_tag: str = "살림"
    # 우리가 찾는 분류 (수집기 카테고리) — 날마다 돌려 가며 4개 (선택지는 25자까지)
    options: list[str] = Field(default_factory=lambda: ["주방·식품", "생활용품·세제", "가전·디지털", "뷰티·헬스",
                                                         "반려동물", "출산·유아동", "홈인테리어"])

    @field_validator("after")
    @classmethod
    def _after(cls, v: str) -> str:
        return _window(f"{v}-24:00").split("-")[0]


# 스레드 주제 태그 (topic_tag) — 분류(쿠팡 카테고리 칸)·상품명에 이 말이 있으면 그 태그 (앞에 적은 것이 먼저). 없으면 topic_tag_default
DEFAULT_TOPIC_TAGS: dict[str, list[str]] = {
    "집밥": ["주방", "식품", "조리", "냄비", "프라이팬", "식기", "라면", "햇반", "즉석밥", "간식", "과자", "음료", "생수", "커피",
            "우유", "만두", "고기", "닭가슴살", "김치", "쌀", "과일", "채소", "축산", "수산", "냉동식품"],
    "살림": ["생활", "세제", "세탁", "섬유유연제", "화장지", "휴지", "물티슈", "키친타올", "청소", "욕실", "수납", "정리", "쓰레기봉투",
            "지퍼백", "홈인테리어"],
    "헬스": ["운동", "스포츠", "헬스", "피트니스", "요가", "레저", "프로틴", "단백질", "아령", "덤벨"],
    "패션": ["패션", "의류", "신발", "운동화", "가방", "잡화", "속옷", "양말", "티셔츠", "패딩", "레깅스"],
}


class ThreadsConfig(BaseModel):
    """스레드 자동 발행. 인증은 /threadsauth 로 한 번만."""

    enabled: bool = True
    template: str = "deal_threads.j2"  # 한 글: 링크는 제휴 고지 아래 본문에 (주인 지시로 '링크는 댓글에' 안 씀)
    reply_template: str | None = None  # 선택: 링크 없는 답글(채널 안내). deal_threads_reply.j2
    send_photo: bool = True
    refresh_before_days: int = 7
    topic_tag_default: str = "핫딜"
    topic_tags: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_TOPIC_TAGS.items()})
    rhythm: ThreadsRhythmConfig = Field(default_factory=ThreadsRhythmConfig)
    # 품절이 확인된 딜: 내 스레드 글 아래에만 '업데이트) HH:MM 기준 품절됐어' 사실 답글 하나 (글마다 한 번, 남의 글에는 안 담)
    soldout_reply: bool = True
    replies: ThreadsRepliesConfig = Field(default_factory=ThreadsRepliesConfig)
    poll: ThreadsPollConfig = Field(default_factory=ThreadsPollConfig)

    @field_validator("topic_tag_default")
    @classmethod
    def _default_tag(cls, v: str) -> str:
        return _topic_tag(v)

    @field_validator("topic_tags")
    @classmethod
    def _tags(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for tag in v:
            _topic_tag(tag)
        return v


def _topic_tag(v: str) -> str:
    """스레드 주제 태그 규칙: 1~50자, 마침표(.)·& 없음."""
    t = str(v or "").strip().lstrip("#")
    if not t or len(t) > 50 or "." in t or "&" in t:
        raise ValueError(f"스레드 주제 태그 '{v}' 는 1~50자, '.'·'&' 없이")
    return t


def _window(v: str) -> str:
    """'HH:MM-HH:MM' (자정을 넘지 않는 구간) 확인."""
    try:
        start, end = (s.strip() for s in str(v).split("-"))
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except ValueError as e:
        raise ValueError(f"시간 구간 형식이 잘못됐습니다: {v!r} (예: 20:00-21:00)") from e
    if not (0 <= sh < 24 and 0 <= eh <= 24 and 0 <= sm < 60 and 0 <= em < 60) or sh * 60 + sm >= eh * 60 + em:
        raise ValueError(f"시간 구간은 같은 날 안에서 시작이 끝보다 빨라야 합니다: {v!r} (예: 20:00-21:00)")
    return f"{sh:02d}:{sm:02d}-{eh:02d}:{em:02d}"


class InstagramCarouselConfig(BaseModel):
    """저녁 '오늘 가격 내려간 딜 TOP5' 캐러셀 (하루 1개). 그날 텔레그램에 올린 딜 중 비교 근거가 있고 아직 품절이 아닌 것만."""

    enabled: bool = True
    window: str = "20:00-21:00"  # 이 구간 안의 아무 분에 (날마다 무작위)
    max_deals: int = Field(5, ge=1, le=8)  # 딜 장 수 (표지·마지막 장 빼고. 인스타 캐러셀은 10장까지)
    min_deals: int = Field(2, ge=1)  # 이보다 적으면 그날은 건너뜀

    @field_validator("window")
    @classmethod
    def _win(cls, v: str) -> str:
        return _window(v)


class InstagramReelsConfig(BaseModel):
    """점심 'TOP3' 릴스 (하루 1개, 주인 결정 10/15). 그날 지금까지 텔레그램에 올린 딜 중 위에서 3개를 10~15초 영상으로.
    서버에 ffmpeg 가 있어야 한다 (없으면 그날은 건너뛰고 설치 방법을 할 일로 한 번 알림)."""

    enabled: bool = True
    window: str = "12:00-13:00"
    max_deals: int = Field(3, ge=1, le=4)
    min_deals: int = Field(2, ge=1)
    processing_timeout_seconds: float = Field(300, gt=0)  # 인스타가 영상을 처리하는 동안 기다리는 최대 시간 (넘으면 조용히 포기)

    @field_validator("window")
    @classmethod
    def _win(cls, v: str) -> str:
        return _window(v)


class InstagramUnitConfig(BaseModel):
    """'이번 주 생필품 1개당 가격' 캐러셀 (화·금, 2단계). 휴지·물티슈·생수·세제·기저귀 중 봇이 가격 기록을 충분히 가진 딜만."""

    enabled: bool = True
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(default_factory=lambda: ["tue", "fri"])
    window: str = "19:00-20:00"  # TOP5 캐러셀과 같은 시간대(한 시간 안)에 겹치지 않게 고른다
    lookback_days: int = Field(7, ge=1)  # 이번 주 = 최근 이만큼 텔레그램에 올린 딜
    baseline_days: int = Field(30, ge=1)  # 단위 장마다 '기준선' = 최근 이만큼 올린 그 단위 딜의 1개당 가격 가운데 값 (3건 이상일 때만)
    min_slides: int = Field(2, ge=1)  # 분류 장이 이보다 적으면 그날은 건너뜀

    @field_validator("window")
    @classmethod
    def _win(cls, v: str) -> str:
        return _window(v)


class InstagramConfig(BaseModel):
    """인스타그램 자동 게시 (Instagram API with Instagram Login). 인증은 /instaauth 로 한 번만.
    사진 없는 글은 못 올리므로 봇이 만든 카드 이미지를 올린다. 본문 링크는 눌리지 않아 프로필 링크(핫딜 페이지)로 유도."""

    enabled: bool = True
    template: str = "deal_instagram.j2"
    refresh_before_days: int = 7
    info_posts: bool = False  # 정보 글(이벤트류)도 올릴지. 기본은 상품 딜만
    # 카드·본문 양식. current = 처음 카드 + template. A/B/C 는 후보 양식:
    # A 사진 크게 + 큰 가격, B 가격 정보 카드(표), C 사진 꽉 채움 + 가격 스티커 (본문은 deal_instagram_a/b/c.j2),
    # P 종이 카드 (주인 결정 10/16 '1+2 섞은 안' — 종이 결·테이프·포스트잇 '#번호'·Bodoni 가격 + 빨간 펜 밑줄, media/card_paper.py;
    # 본문은 B 와 같은 deal_instagram_b.j2). TOP5·단가표 캐러셀도 같은 그림. 되돌리려면 B.
    style: Literal["current", "A", "B", "C", "P"] = "current"
    # 확인된 상품 사진(deal_photo — 텔레그램에 올린 그 사진)이 없을 때: text_card = 사진 없는 글자 카드로 올림 (기본, 주인 결정 10/15),
    # skip = 인스타엔 안 올리고 기록만. 로고·기본 이미지·게시판 썸네일은 어느 쪽이든 카드에 안 넣는다
    no_photo: Literal["skip", "text_card"] = "text_card"
    # ---- 인스타 전용 대기열 (주인 결정 10/15): 텔레그램에 올린 딜을 바로 올리지 않고 줄 세워 하루 몇 개만 골라 올린다
    daily_singles: int = Field(3, ge=0)  # 딜 하나짜리 글 하루 최대 (점수 높은 순). 캐러셀·릴스·단가표는 따로 셈
    hours: str = "07:00-23:00"  # 인스타에 올리는 시간 (app.timezone)
    min_gap_minutes: int = Field(120, ge=0)  # 인스타 글 사이 간격. 딜 하나짜리 글은 캐러셀·릴스·단가표와도 이만큼 띄운다
    queue_expire_hours: float = Field(12, gt=0)  # 대기열에서 이보다 오래 기다린 딜은 안 올림 (가격이 바뀌었을 가능성)
    carousel: InstagramCarouselConfig = Field(default_factory=lambda: InstagramCarouselConfig())
    reels: InstagramReelsConfig = Field(default_factory=lambda: InstagramReelsConfig())
    unit_carousel: InstagramUnitConfig = Field(default_factory=lambda: InstagramUnitConfig())

    @field_validator("style", mode="before")
    @classmethod
    def _style_upper(cls, v: Any) -> Any:
        return v.upper() if isinstance(v, str) and v.lower() in ("a", "b", "c", "p") else v

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: str) -> str:
        return _window(v)


class CardConfig(BaseModel):
    """딜 카드 이미지(1080×1350 JPEG). 인스타그램만 — 사진은 텔레그램 글과 같은 확인된 상품 사진(deal_photo)만."""

    enabled: bool = True
    font_path: str | None = None  # 비우면 서버 글꼴(나눔고딕 등)을 찾음
    font_heavy_path: str | None = None  # 큰 가격용 아주 굵은 글꼴 (비우면 나눔고딕 ExtraBold → 없으면 font_path)
    font_regular_path: str | None = None  # 작은 안내 글자용 보통 굵기 (비우면 나눔고딕 → 없으면 font_path)
    footer: str = "오늘의 핫딜"
    footer_note: str = "구매 링크는 프로필 링크에서"
    keep_days: int = 7  # 만든 카드 파일 보관 기간


class WebConfig(BaseModel):
    """봇의 작은 웹 서버(공개 도메인). OAuth 콜백·카드 이미지·핫딜 페이지."""

    public_page: bool = True  # "/" 에 최근 채널 딜 목록(구매 링크 포함) 페이지를 연다 — 인스타 프로필 링크용
    site_title: str = "오늘의 핫딜"
    page_hours: int = 48  # 페이지에 보여줄 기간
    past_days: int = Field(7, ge=1, le=30)  # 기간 안에 딜이 없으면 최근 N일 딜을 '지난 딜'로 보여 줌 (주인 결정 10/15)
    page_max_items: int = 30  # 예전 인스타 양식(current)만: 점수 순 위에서 이만큼. 번호 양식(A/B/C)은 기간 안의 딜을 모두 (인스타 '#번호' 를 찾게)


class CopyTarget(BaseModel):
    """API 가 없어 복붙해야 하는 곳 (카카오 오픈채팅, 네이버 블로그)."""

    key: str
    name: str
    template: str
    enabled: bool = True


class CopyConfig(BaseModel):
    """발행 후 관리자 챗으로 '복사해서 붙여넣을 문구'를 보낸다."""

    enabled: bool = True
    targets: list[CopyTarget] = Field(
        default_factory=lambda: [
            CopyTarget(key="kakao", name="카카오 오픈채팅", template="deal_kakao.j2"),
            CopyTarget(key="blog", name="네이버 블로그", template="deal_blog.j2"),
        ]
    )


class LinksConfig(BaseModel):
    always_deeplink: bool = False
    resolve_short_links: bool = True
    # 링크프라이스 링크 생성이 실패한 몰(대개 아직 승인 안 된 머천트)은 이 시간 동안 건너뛴다. 0 = 매번 시도
    provider_error_cooldown_hours: float = 6


class NaverAutoLinkConfig(BaseModel):
    """네이버 쇼핑커넥트 링크 자동 만들기 (주인 결정 10/11). 브라우저 일은 Blog-Auto 가 아카이브 계정 프로필로 하고,
    딜봇은 받은편지함으로 묻고 connect_results.jsonl 의 답을 읽는다. 끄면(false) 예전처럼 주인에게 링크 요청."""

    enabled: bool = False
    # Blog-Auto 의 답을 이만큼 기다리고, 없으면 예전처럼 주인에게 링크 요청 (Blog-Auto 는 20분 넘은 일은 시작하지 않음)
    wait_minutes: float = 30
    # 쇼핑커넥트에 없던 상품번호는 이 기간 다시 찾지 않고 원본 주소로 (참여 상품은 매달 1일에 바뀜)
    not_found_days: float = 7
    # 정품·공식 판매처 확인이 안 된 딜: manual = 예전처럼 주인에게 링크 요청(주인이 판매처를 보고 만듦), auto = 그래도 자동으로
    unverified: Literal["manual", "auto"] = "manual"


class AutoLinkConfig(BaseModel):
    naver: NaverAutoLinkConfig = Field(default_factory=NaverAutoLinkConfig)


class InfoSummarizerConfig(BaseModel):
    """정보 글 본문을 채널 양식에 맞게 짧게 다시 쓴다. ANTHROPIC_API_KEY 와 API 모델 이름이 둘 다 있으면 API 를 먼저 쓰고,
    아니면 서버에 로그인된 claude CLI(한줄평과 같은 것)로. enabled: false 면 둘 다 안 씀."""

    enabled: bool = True
    # API 모델 이름은 저장소에 두지 않는다: 서버 .env 의 DEALBOT_MODEL_SUMMARIZER. 없으면 API 는 건너뛰고 CLI 로
    model: str | None = None
    raw_chars: int = 4000  # 요약기에 넘기는 원문 최대 길이
    timeout_seconds: float = 60


class InfoPostsConfig(BaseModel):
    """정보 글: 상품 링크가 없는 게시판 글(이벤트·공지)을 본문·사진만 정리해 올린다. 수익 링크 없음."""

    enabled: bool = True
    min_recommend: int = 5  # 커뮤니티 추천 수가 이 값 이상인 글만
    max_per_day: int = 10
    max_chars: int = 500  # 본문 정리 길이
    send_photo: bool = True  # 원문 사진이 있으면 봇이 받아서 같이 올림
    template: str = "info_post.j2"  # 텔레그램
    kakao_template: str = "info_kakao.j2"  # 관리자 챗으로 보내는 복붙 문구
    threads_template: str = "info_threads.j2"
    # auto: 요약이 만들어지면 바로 올리고, 요약을 못 만들거나 본문 위치가 불확실하면 관리자 확인(/ok) 후 올림
    # always: 정보 글은 항상 관리자 확인 후 올림 / never: 확인 없이 정리한 원문을 그대로 올림 (비추천)
    review: Literal["auto", "always", "never"] = "auto"
    # 이벤트성 글(브랜드 세일 등)은 혜택이 이만큼은 돼야 올린다. 둘 중 하나만 넘으면 통과, 0 이면 그 기준은 안 봄
    min_discount_rate: int = 40  # %
    min_discount_amount: int = 5000  # 원 (할인·적립·캐시백 금액. 결제 조건 금액이 아님)
    summarizer: InfoSummarizerConfig = Field(default_factory=InfoSummarizerConfig)


class BlogDigestConfig(BaseModel):
    """하루치 발행 딜을 모아 밤에 블로그 글 한 편(복붙용)으로 관리자 챗에 보낸다. /blog 로 바로 만들어 볼 수도 있다."""

    enabled: bool = True
    time: str = "21:30"  # 매일 이 시각(app.timezone)에 보냄
    template: str = "blog_daily.j2"
    max_items: int = 10  # 글 하나에 넣는 최대 딜 수 (점수 높은 순)
    min_items: int = 1  # 이보다 적으면 안 보냄
    include_info: bool = True  # 정보 글(이벤트)도 넣음 (정리된 본문이 있는 것만)
    max_info_items: int = 3
    blog_name: str = "Deren의 아카이브"  # 도입부 "OO를 운영하고 있는 OO입니다"
    author: str = "Deren"
    # 채널 안내·서명에 핫딜 방 다음으로 덧붙일 줄 (예: "- 텔레그램 · Deren의 쌀농사방\nhttps://t.me/Derens_tip"). 비우면 핫딜 방만
    extra_channel_lines: list[str] = Field(default_factory=list)
    # 글마다 반드시 넣는 고지 문구 (쿠팡 파트너스 규정: 쿠팡 딜이 없는 날도 넣는다)
    always_disclosures: list[str] = Field(
        default_factory=lambda: ["이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."]
    )

    @field_validator("time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, mm = v.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("blog_digest.time must be HH:MM")
        return v


_CARD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class DashboardCardConfig(BaseModel):
    """대시보드 카드 하나 = 데이터 소스 하나. 화면에는 config 에 적은 순서대로 나온다.
    type 은 src/dealbot/dashboard/sources/ 의 모듈이 정한 이름 (telegram, instagram, threads, coupang_partners, dealbot,
    naver_blog, kakao_openchat). key 는 주소(?src=)·DB 에 쓰는 이름이라 카드마다 달라야 한다."""

    key: str
    type: str
    title: str | None = None  # 없으면 모듈의 기본 이름
    enabled: bool = True
    goals: dict[str, float] = Field(default_factory=dict)  # 목표 줄의 목표값 (모듈마다 이름이 다름, docs/DASHBOARD.md)
    options: dict[str, Any] = Field(default_factory=dict)  # 모듈별 설정 (블로그 이름·개설일 등)

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        if not _CARD_KEY_RE.fullmatch(v):
            raise ValueError("dashboard card key 는 영문 소문자로 시작하는 영문 소문자·숫자·_ (32자까지)")
        return v


class DashboardConfig(BaseModel):
    """채널 대시보드 (주인 요청 10/14): /dashboard 에 채널별 구독자·이웃·조회수와 목표를 한 화면에.
    페이지는 DASHBOARD_USER / DASHBOARD_PASSWORD 가 모두 있을 때만 열린다 (없으면 404)."""

    enabled: bool = True  # false 면 숫자 모으기도 페이지도 끔
    title: str = "채널 대시보드"
    # API 로 읽는 카드(텔레그램·인스타·스레드·쿠팡)를 읽는 시각 (app.timezone). 재시작해도 같은 시각을 두 번 읽지 않음
    collect_times: list[str] = Field(default_factory=lambda: ["09:00", "13:00", "18:00", "22:00"])
    export_interval_minutes: int = Field(60, ge=5)  # Blog-Auto 기록 파일·봇 DB 로 채우는 카드를 다시 읽는 간격
    refresh_min_minutes: int = Field(10, ge=1)  # '다시 읽기' 버튼은 카드마다 이 간격에 한 번
    fetch_timeout_seconds: float = Field(45.0, gt=0)  # 카드 하나 읽기에 쓰는 시간 상한 (딜 루프와 따로 돌지만 오래 붙잡지 않게)
    spark_days: int = Field(28, ge=7, le=90)  # 작은 추이 그래프의 기간
    manual_max_days_back: int = Field(60, ge=1, le=400)  # 직접 입력 날짜는 오늘부터 이만큼 전까지
    blogauto_metrics_path: str = "/home/ubuntu/Blog-Auto/var/metrics/blogauto.json"  # Blog-Auto 가 한 시간마다 쓰는 활동 기록
    blogauto_stale_hours: float = Field(3.0, gt=0)  # 이보다 오래된 기록은 '판단 불가'
    cards: list[DashboardCardConfig] = Field(default_factory=list)
    # 대시보드 앱(Sns-board) 주소 — 푸시·카톡의 바로가기 링크(/dashboard/go/<항목>), 같은 사이트 확인(Origin), VAPID sub 에 씀
    public_url: str = "https://52-79-136-64.sslip.io"
    actions_enabled: bool = True  # false 면 대시보드는 보기만 (누르는 일은 모두 텔레그램으로)

    @field_validator("public_url")
    @classmethod
    def _public_url(cls, v: str) -> str:
        v = (v or "").strip().rstrip("/")
        if not re.fullmatch(r"https://[a-z0-9.-]+(:[0-9]{1,5})?", v):
            raise ValueError("dashboard.public_url 은 https://호스트 (경로 없이)")
        return v

    @field_validator("collect_times")
    @classmethod
    def _times(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            hh, _, mm = str(t).partition(":")
            if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
                raise ValueError("dashboard.collect_times 는 HH:MM 목록")
            out.append(f"{int(hh):02d}:{int(mm):02d}")
        return sorted(set(out))

    @field_validator("cards")
    @classmethod
    def _unique(cls, v: list[DashboardCardConfig]) -> list[DashboardCardConfig]:
        keys = [c.key for c in v]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"dashboard card key 가 겹침: {dupes}")
        return v


class ChannelsConfig(BaseModel):
    """내 채널 주소. 비워 두면 템플릿에서 해당 줄이 빠진다."""

    telegram_url: str = ""  # 예: https://t.me/oneul_hotdeal
    kakao_openchat_url: str = ""  # 예: https://open.kakao.com/o/xxxx
    threads_url: str = ""  # 예: https://www.threads.net/@oneul_hot_deal
    instagram_url: str = ""  # 예: https://www.instagram.com/oneul_hot_deal (핫딜 페이지·채널 안내 글의 링크)
    blog_url: str = ""  # 예: https://blog.naver.com/demiyum (채널 안내 글의 블로그 링크)
    telegram_name: str = "오늘의 핫딜"  # 블로그 글 채널 안내에 쓰는 이름
    kakao_openchat_name: str = "오늘의 핫딜 오픈채팅"

    def as_dict(self) -> dict[str, str]:
        return {
            "telegram_url": self.telegram_url,
            "kakao_openchat_url": self.kakao_openchat_url,
            "threads_url": self.threads_url,
            "instagram_url": self.instagram_url,
            "blog_url": self.blog_url,
            "telegram_name": self.telegram_name,
            "kakao_openchat_name": self.kakao_openchat_name,
        }


class PushConfig(BaseModel):
    """휴대폰 푸시 (텔레그램과 별개). provider: auto | ntfy | pushover | none"""

    provider: str = "auto"
    events: list[str] = Field(default_factory=lambda: ["manual_link", "info_review"])  # manual_link | info_review | publish_failed | error | daily_summary | startup
    ntfy_url: str = "https://ntfy.sh"


class NaverConnectConfig(BaseModel):
    """네이버 쇼핑커넥트 브라우저 자동화. 셀렉터는 실제 화면을 보고 맞춰야 함 (/shot 으로 확인)."""

    login_url: str = "https://nid.naver.com/nidlogin.login"
    create_url: str = "https://connect.naver.com/"
    login_cookie: str = "NID_AUT"
    timeout_seconds: int = 30
    selectors: dict[str, str] = Field(
        default_factory=lambda: {
            "qr_tab": "text=QR코드",
            "url_input": "input[type='url'], input[placeholder*='URL'], input[placeholder*='주소'], input[type='text']",
            "create_button": "text=링크 발급",
            "result": "input[readonly], textarea[readonly], a[href*='naver.me'], a[href*='connect']",
        }
    )


class BrowserConfig(BaseModel):
    enabled: bool = False
    headless: bool = True
    profile_dir: str = "browser-profile"  # data_dir 아래
    executable_path: str | None = None  # 크로미움 실행 파일 (비우면 자동 탐색, 환경변수 DEALBOT_CHROMIUM_PATH 도 가능)
    naver_connect: NaverConnectConfig = Field(default_factory=NaverConnectConfig)


class NLRouterConfig(BaseModel):
    """관리자 챗 '말 명령' (주인 요청 10/11): 명령어 대신 그냥 말로 보내면 AI 가 기존 명령으로 바꿔 실행한다.
    false 면 예전 그대로 (일반 메시지는 링크 답장·직접 발행만). 모델은 비워 두면 정보 글 요약기(API)·한줄평(CLI)과 같은 것."""

    enabled: bool = True
    model: str | None = None  # API 모델 (None → info_posts.summarizer.model = DEALBOT_MODEL_SUMMARIZER)
    cli_model: str | None = None  # 서버 claude CLI 모델 (None → publish.commentary.model = DEALBOT_MODEL_COMMENTARY)
    timeout_seconds: float = 30
    confidence: float = 0.75  # 이보다 확신이 낮으면 실행하지 않고 물어봄
    rate_10min: int = 20  # 10분에 이만큼 넘게 오면 AI 를 부르지 않고 쉬어 감
    rate_day: int = 200


class MonitoringConfig(BaseModel):
    push: PushConfig = Field(default_factory=PushConfig)
    nl_router: NLRouterConfig = Field(default_factory=NLRouterConfig)
    notify_on_publish: bool = True
    notify_on_failure: bool = True
    # 재시도 중인 실패(1·2번째)도 알릴지. False 면 최종 포기(❌)만 알림
    notify_failure_attempts: bool = False
    notify_on_error: bool = True
    notify_on_manual_link: bool = True
    # 링크 요청·링크 완료를 블로그 프로그램(Blog-Auto)이 카카오톡 '나에게 보내기'로도 보내게 받은편지함에 남긴다 (주인 요청 10/10)
    kakao_relay: bool = True
    error_alert_cooldown_minutes: int = 30
    daily_summary: bool = True  # 매일 daily_summary_time 에 하루 요약을 보낼지 (/status 로 언제든 볼 수 있음)
    daily_summary_time: str = "21:00"
    quiet_notices: bool = False  # true: 시작/미리보기/발행 알림을 무음으로 (false: 모든 알림이 소리·진동과 함께 옴)
    # 주인 결정 10/11: 관리자 챗 알림은 소리 없이. 주인이 해야 할 일(링크·확인·고장)은 Blog-Auto 가 카톡으로 보내고,
    # Blog-Auto·카톡이 안 될 때만 텔레그램으로 소리 내서. 명령에 대한 답장은 그대로 (소리와 함께). false 면 예전 그대로
    telegram_silent: bool = True
    owner_summary_time: str = "20:50"  # 저녁 요약(카톡, Blog-Auto 가 21~22시에 보냄)에 넣을 딜봇 하루 정리를 넘기는 시각
    # 텔레그램엔 딜을 올렸는데 인스타·스레드가 24시간 동안 0개면 주인 할 일 한 통 (안 올린 이유 요약과 함께, 하루 한 번까지)
    sns_zero_alert: bool = True
    # 주인 10/18: 관리자 챗 알림은 보내지 않는다 (할 일은 대시보드 앱 푸시 → 안 되면 카톡). 명령 답장은 그대로.
    # notify.router 가 legacy 면 이 값과 상관없이 예전 그대로
    telegram_owner_notices: bool = False
    # 마지막 수단: 푸시 기기가 하나도 없고 카톡도 끊겼을 때만 텔레그램으로 소리 내서 (기본 끔)
    telegram_last_resort: bool = False
    heartbeat_minutes: int = 0  # 관리자 챗이 N분 동안 조용하면 "정상 가동 중" 짧은 상태를 보냄 (0 = 끔). 특가 알림이 있으면 그걸로 대신함

    @field_validator("daily_summary_time", "owner_summary_time")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        hh, mm = v.split(":")
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("daily_summary_time must be HH:MM")
        return v


class NotifyConfig(BaseModel):
    """주인 알림 길 (주인 10/18): push = 대시보드 앱 웹 푸시가 먼저(카톡은 예비, 텔레그램 관리자 챗은 조용히),
    legacy = 예전 그대로 (Blog-Auto 카톡 + 텔레그램). 되돌릴 때만 legacy."""

    router: str = "push"
    push_quiet_hours: str = ""  # 예: "00:00-07:00" — 그 시간엔 ⛔ 말고는 끝날 때까지 기다림 (기본 없음)

    @field_validator("router")
    @classmethod
    def _router(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in ("push", "legacy"):
            raise ValueError("notify.router 는 push 또는 legacy")
        return v

    @field_validator("push_quiet_hours")
    @classmethod
    def _quiet(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]-[0-2][0-9]:[0-5][0-9]", v):
            raise ValueError("notify.push_quiet_hours 는 HH:MM-HH:MM 또는 빈칸")
        return v


class Secrets(BaseModel):
    """환경변수에서만 읽는 값들."""

    coupang_access_key: str | None = None
    coupang_secret_key: str | None = None
    coupang_sub_id: str | None = None
    linkprice_affiliate_id: str | None = None
    adpick_affid: str | None = None
    telegram_bot_token: str | None = None
    telegram_channel_id: str | None = None
    telegram_admin_chat_id: int | None = None
    threads_app_id: str | None = None
    threads_app_secret: str | None = None
    threads_redirect_uri: str = "https://localhost/callback"
    instagram_app_id: str | None = None
    instagram_app_secret: str | None = None
    instagram_redirect_uri: str = "https://localhost/instagram/callback"
    public_domain: str | None = None  # Railway 가 넣어 주는 RAILWAY_PUBLIC_DOMAIN (예: xxx.up.railway.app)
    # 같은 봇을 가리키는 다른 공개 주소 (PUBLIC_DOMAIN_ALIASES, 쉼표로). 내 도메인으로 옮긴 뒤에도 Caddy 가 예전 sslip.io 주소를
    # 같이 받아서 메타에 등록해 둔 예전 리디렉션 주소가 계속 된다 (deploy/lightsail/https.sh --domain)
    public_domain_aliases: list[str] = Field(default_factory=list)
    web_port: int = 8080  # PORT — 스레드 OAuth 콜백·/health 를 받는 작은 HTTP 서버
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    pushover_user_key: str | None = None
    pushover_app_token: str | None = None
    anthropic_api_key: str | None = None  # 정보 글 요약 (Claude API)
    naver_client_id: str | None = None  # 네이버 쇼핑 검색 API (쿠팡 딜 네이버 최저가 비교를 켤 때만, 선택 — API 는 2026-07-31 종료)
    naver_client_secret: str | None = None
    dashboard_user: str | None = None  # DASHBOARD_USER / DASHBOARD_PASSWORD — /dashboard 기본 인증 (둘 다 있어야 페이지가 열림)
    dashboard_password: str | None = None

    @property
    def has_dashboard_auth(self) -> bool:
        return bool(self.dashboard_user and self.dashboard_password)

    @property
    def has_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def has_naver_search(self) -> bool:
        return bool(self.naver_client_id and self.naver_client_secret)

    @property
    def has_threads_app(self) -> bool:
        return bool(self.threads_app_id and self.threads_app_secret)

    @property
    def has_instagram_app(self) -> bool:
        return bool(self.instagram_app_id and self.instagram_app_secret)

    @property
    def instagram_callback_path(self) -> str:
        return urlsplit(self.instagram_redirect_uri).path or "/instagram/callback"

    @property
    def served_hosts(self) -> set[str]:
        """이 봇이 받는 공개 주소들 (PUBLIC_DOMAIN + 별칭)."""
        hosts = {(self.public_domain or "").strip().strip("/").lower()} | {a.strip().strip("/").lower() for a in self.public_domain_aliases}
        return {h for h in hosts if h}

    @property
    def instagram_callback_served(self) -> bool:
        host = (urlsplit(self.instagram_redirect_uri).hostname or "").lower()
        return bool(host) and host in self.served_hosts

    @property
    def public_base_url(self) -> str | None:
        """공개 도메인이 있으면 https://도메인 (카드 이미지·핫딜 페이지 주소의 앞부분)."""
        return f"https://{self.public_domain.strip().strip('/')}" if self.public_domain else None

    @property
    def threads_callback_path(self) -> str:
        """봇이 직접 받는 스레드 OAuth 콜백 경로 (리디렉션 주소의 경로 부분)."""
        return urlsplit(self.threads_redirect_uri).path or "/threads/callback"

    @property
    def threads_callback_served(self) -> bool:
        """리디렉션 주소가 이 봇의 공개 도메인(또는 별칭)을 가리키면 승인만 눌러도 자동으로 연결된다."""
        host = (urlsplit(self.threads_redirect_uri).hostname or "").lower()
        return bool(host) and host in self.served_hosts

    @property
    def has_ntfy(self) -> bool:
        return bool(self.ntfy_topic)

    @property
    def has_pushover(self) -> bool:
        return bool(self.pushover_user_key and self.pushover_app_token)

    @property
    def has_coupang(self) -> bool:
        return bool(self.coupang_access_key and self.coupang_secret_key)

    @property
    def has_linkprice(self) -> bool:
        return bool(self.linkprice_affiliate_id)

    @property
    def has_adpick(self) -> bool:
        return bool(self.adpick_affid)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_bot_token)

    @property
    def has_channel(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_channel_id)

    @property
    def has_admin(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_admin_chat_id)


class Settings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    app: AppConfig = Field(default_factory=AppConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    coupang: CoupangConfig = Field(default_factory=CoupangConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    collectors: list[CollectorConfig] = Field(default_factory=list)
    shops: list[ShopConfig] = Field(default_factory=list)
    deal: DealConfig = Field(default_factory=DealConfig)
    publish: PublishConfig = Field(default_factory=PublishConfig)
    threads: ThreadsConfig = Field(default_factory=ThreadsConfig)
    instagram: InstagramConfig = Field(default_factory=InstagramConfig)
    card: CardConfig = Field(default_factory=CardConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    # yaml 키는 copy 지만 BaseModel.copy 와 겹쳐 속성명은 copy_cfg
    copy_cfg: CopyConfig = Field(default_factory=CopyConfig, alias="copy")
    links: LinksConfig = Field(default_factory=LinksConfig)
    autolink: AutoLinkConfig = Field(default_factory=AutoLinkConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    info_posts: InfoPostsConfig = Field(default_factory=InfoPostsConfig)
    blog_digest: BlogDigestConfig = Field(default_factory=BlogDigestConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    secrets: Secrets = Field(default_factory=Secrets)

    # 파생 경로
    config_path: Path | None = None
    data_dir: Path = Path("data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dealbot.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def templates_dir(self) -> Path:
        d = self.publish.templates_dir
        if d.is_absolute():
            return d
        base = self.config_path.parent if self.config_path else Path.cwd()
        return (base / d).resolve()

    def shop_registry(self) -> ShopRegistry:
        """기본 쇼핑몰 목록 + config.shops 오버라이드."""
        shops: dict[str, Shop] = {
            s.key: dataclasses.replace(s, aliases=list(s.aliases), domains=list(s.domains)) for s in DEFAULT_SHOPS
        }
        for o in self.shops:
            base = shops.get(o.key) or Shop(key=o.key, name=o.name or o.key)
            for f in ("name", "aliases", "domains", "link_mode", "provider", "disclosure", "enabled", "manual_hint", "manual_fallback", "requires_provider"):
                v = getattr(o, f)
                if v is not None:
                    setattr(base, f, v)
            shops[o.key] = base
        return ShopRegistry(list(shops.values()))

    @field_validator("collectors")
    @classmethod
    def _unique_names(cls, v: list[CollectorConfig]) -> list[CollectorConfig]:
        names = [c.name for c in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate collector names: {sorted(dupes)}")
        return v


# 모델 이름은 저장소(코드·config.yaml)에 두지 않고 서버 .env 에서 받는다
ENV_MODEL_COMMENTARY = "DEALBOT_MODEL_COMMENTARY"  # 서버 claude CLI 모델 (한줄평, CLI 로 도는 요약·말 명령)
ENV_MODEL_SUMMARIZER = "DEALBOT_MODEL_SUMMARIZER"  # Claude API 모델 (정보 글 요약, API 로 도는 말 명령)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip().strip("'\"")
    try:
        return int(value)
    except ValueError:
        # 숫자가 아니면 죽지 말고 경고만 — 어떤 값이 잘못됐는지 로그로 알린다
        logging.getLogger(__name__).error(
            "환경변수 %s 는 숫자여야 합니다 (@userinfobot 이 알려준 숫자). 지금 값: %r — 무시합니다", name, raw
        )
        return None


def _public_domain() -> str | None:
    """공개 도메인: PUBLIC_DOMAIN (Lightsail 등 직접 운영) 또는 Railway 가 넣어 주는 RAILWAY_PUBLIC_DOMAIN."""
    return _env_str("PUBLIC_DOMAIN") or _env_str("RAILWAY_PUBLIC_DOMAIN")


def _default_threads_redirect(public_domain: str | None, path: str = "/threads/callback") -> str:
    """메타는 localhost 리디렉션을 받지 않으므로, 공개 도메인이 있으면 봇이 직접 받는 주소를 기본값으로 쓴다."""
    if public_domain:
        return f"https://{public_domain.strip().strip('/')}{path}"
    return "https://localhost/callback" if path == "/threads/callback" else f"https://localhost{path}"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    # 따옴표째 붙여넣는 실수가 잦아서 벗겨 준다
    return raw.strip().strip("'\"") or None


def load_settings(config_path: str | os.PathLike[str] | None = None, *, load_env: bool = True) -> Settings:
    """config.yaml + 환경변수를 합쳐 Settings 를 만든다."""
    if load_env:
        load_dotenv(override=False)

    path = Path(config_path or os.getenv("DEALBOT_CONFIG") or "config.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    elif config_path is not None:
        raise FileNotFoundError(f"config file not found: {path}")

    secrets = Secrets(
        coupang_access_key=_env_str("COUPANG_ACCESS_KEY"),
        coupang_secret_key=_env_str("COUPANG_SECRET_KEY"),
        coupang_sub_id=_env_str("COUPANG_SUB_ID"),
        linkprice_affiliate_id=_env_str("LINKPRICE_AFFILIATE_ID"),
        adpick_affid=_env_str("ADPICK_AFFID"),
        telegram_bot_token=_env_str("TELEGRAM_BOT_TOKEN"),
        telegram_channel_id=_env_str("TELEGRAM_CHANNEL_ID"),
        telegram_admin_chat_id=_env_int("TELEGRAM_ADMIN_CHAT_ID"),
        threads_app_id=_env_str("THREADS_APP_ID"),
        threads_app_secret=_env_str("THREADS_APP_SECRET"),
        threads_redirect_uri=_env_str("THREADS_REDIRECT_URI") or _default_threads_redirect(_public_domain()),
        instagram_app_id=_env_str("INSTAGRAM_APP_ID"),
        instagram_app_secret=_env_str("INSTAGRAM_APP_SECRET"),
        instagram_redirect_uri=_env_str("INSTAGRAM_REDIRECT_URI")
        or _default_threads_redirect(_public_domain(), "/instagram/callback"),
        public_domain=_public_domain(),
        public_domain_aliases=[a.strip() for a in (_env_str("PUBLIC_DOMAIN_ALIASES") or "").split(",") if a.strip()],
        web_port=_env_int("PORT") or 8080,
        ntfy_topic=_env_str("NTFY_TOPIC"),
        ntfy_token=_env_str("NTFY_TOKEN"),
        pushover_user_key=_env_str("PUSHOVER_USER_KEY"),
        pushover_app_token=_env_str("PUSHOVER_APP_TOKEN"),
        anthropic_api_key=_env_str("ANTHROPIC_API_KEY"),
        naver_client_id=_env_str("NAVER_CLIENT_ID"),
        naver_client_secret=_env_str("NAVER_CLIENT_SECRET"),
        dashboard_user=_env_str("DASHBOARD_USER"),
        dashboard_password=_env_str("DASHBOARD_PASSWORD"),
    )

    settings = Settings(
        **raw,
        secrets=secrets,
        config_path=path.resolve() if path.exists() else None,
        data_dir=Path(os.getenv("DEALBOT_DATA_DIR") or raw.get("data_dir") or "data"),
    )

    # 환경변수 오버라이드
    if os.getenv("LOG_LEVEL"):
        settings.app.log_level = os.environ["LOG_LEVEL"].upper()
    settings.publish.dry_run = _env_bool("DEALBOT_DRY_RUN", settings.publish.dry_run)
    if os.getenv("TZ") and not raw.get("app", {}).get("timezone"):
        settings.app.timezone = os.environ["TZ"]
    settings.publish.commentary.model = _env_str(ENV_MODEL_COMMENTARY) or settings.publish.commentary.model
    settings.info_posts.summarizer.model = _env_str(ENV_MODEL_SUMMARIZER) or settings.info_posts.summarizer.model

    return settings
