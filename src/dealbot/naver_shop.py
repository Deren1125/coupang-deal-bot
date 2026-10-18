"""네이버 쇼핑 검색 API(공식 Open API, openapi.naver.com/v1/search/shop.json)와 네이버 스토어 주소 도구.

네이버 쇼핑 검색 Open API 는 2026-07-31 에 끝났다 (대신할 API 없음 — 이 서버 키는 401, 네이버 클라우드 API HUB 에는 쇼핑 경로가 없음).
그래서 스토어 딜의 큰 사진 찾기(예전 NaverShopSearch.find_image)는 지웠다 (주인 결정 10/15): 네이버 딜 사진은 주인이 링크와 같이
보낸 사진이다. 남은 것:
1. 스토어 주소 도구 (store_product_no·store_name·search_queries) — 쇼핑커넥트 자동 링크(autolink)가 쓴다
2. 쿠팡 딜(골드박스·카테고리 베스트)의 가격 근거: 같은 상품의 네이버 최저가 (NaverPriceReference, 주인 결정 10/13)
   같은 수량·규격, 낱말 대부분 일치(양쪽 갈래 말·모델 번호까지), 중고·렌탈·해외직구·옵션 목록·부속품이 아닌 새 상품만 본다.
   'N% 싸요' 근거는 최저가가 확인될 때만(가격비교 상품, 또는 낮은 가격순 검색에도 같은 상품), 탈락은 가격비교 상품이나
   판매처 여럿이 더 쌀 때만 (판매글 한 곳의 싼 값으로는 안 함). 헷갈리면 근거 없음 (할인을 지어내지 않음).
   설정(deal.naver_reference.enabled)으로만 켜지고 운영은 꺼 둠 — 꺼져 있으면 검색 API 를 한 번도 부르지 않는다.
NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 이 없으면 건너뛴다 (로그 한 번). 키 값은 로그에 남기지 않는다.
켜 둔 상태에서 401·404 등이 오면 첫 호출 뒤로 다시 시작할 때까지 끈다.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from dealbot.enrich import is_naver_store
from dealbot.models import Product
from dealbot.pricing.market import (
    NAVER_SOURCE,
    MarketQuote,
    model_numbers,
    parse_quantity,
    same_quantity,
    tokens,
)
from dealbot.utils.text import clean_name

if TYPE_CHECKING:
    from dealbot.config import NaverReferenceConfig

log = logging.getLogger(__name__)

SEARCH_URL = "https://openapi.naver.com/v1/search/shop.json"
_PRODUCT_NO = re.compile(r"/products/(\d+)")
# 키가 틀렸거나 API 를 더 쓸 수 없음 (401·403 키/권한, 404·410 종료) → 다시 시작할 때까지 안 부름
_DEAD = (401, 403, 404, 410)


class _Unavailable(Exception):
    pass


def store_product_no(url: str | None) -> str | None:
    """스마트스토어·브랜드스토어 상품 주소의 상품번호 ('smartstore.naver.com/가게/products/123' → '123')."""
    if not url or not is_naver_store(url):
        return None
    m = _PRODUCT_NO.search(urlparse(url).path or "")
    return m.group(1) if m else None


def store_name(url: str | None) -> str | None:
    """스토어 주소의 가게 이름 칸 ('/가게/products/1' → '가게'). 'main'(가게를 안 밝힌 주소)이면 None."""
    if not url or not is_naver_store(url):
        return None
    parts = [x for x in (urlparse(url).path or "").split("/") if x]
    if len(parts) < 2 or parts[1] != "products" or parts[0].lower() == "main":
        return None
    return parts[0].lower()


def _norm(text: object) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(text or "").lower())


def search_queries(name: str) -> list[str]:
    """검색어: 꾸밈·괄호(가격·배송·기간)를 걷어낸 상품명, 못 찾으면 앞 네 낱말."""
    s = clean_name(name)
    s = re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", s)
    words = re.sub(r"[^\w.+%/-]+", " ", s).split()
    out: list[str] = []
    for q in (" ".join(words)[:100].strip(), " ".join(words[:4])):
        if q and q not in out:
            out.append(q)
    return out


# ---------------------------------------------------------------- 짧은 검색어 (주인 10/18)
# 쇼핑 커넥트 '상품 찾기'는 판매처가 붙인 상품명으로 찾는다 → 게시판 제목 그대로(긴 이름)는 0건이 잦다.
# 그래서 브랜드 + 핵심 낱말 2~4개만 ('폴햄 여성 에코베로 라운드 긴팔티' → '폴햄 에코베로 긴팔티'). 규칙만 (AI 안 부름).
# 괄호 앞머리에 쓰는 쇼핑몰 이름 (브랜드가 아님)
_SHOP_TAGS = frozenset({
    "네이버", "네이버쇼핑", "네이버플러스", "스마트스토어", "브랜드스토어", "n플러스", "naver", "쇼핑라이브", "네이버페이",
    "쿠팡", "지마켓", "g마켓", "옥션", "11번가", "롯데온", "ssg", "쓱", "토스", "토스쇼핑", "카카오", "카카오톡", "톡딜",
})
# 뺄 낱말: 성별·나이, 색, 크기, 계절, 홍보·배송·옵션 말, 모양을 꾸미는 말 (핵심 상품 낱말은 남는다)
_SHORT_DROP = frozenset("""
여성 남성 여자 남자 여성용 남성용 남녀 남녀공용 공용 유니섹스 아동 아동용 키즈 주니어 성인 성인용 여아 남아 유아 우먼 맨즈
블랙 화이트 네이비 그레이 차콜 베이지 아이보리 브라운 카키 레드 블루 핑크 그린 옐로우 퍼플 오렌지 민트 크림 실버 골드 와인
버건디 라벤더 멜란지 오트밀 연청 중청 흑청 검정 흰색 투명 black white navy gray grey beige ivory brown khaki red blue pink green
s m l xl xxl xxxl 2xl 3xl 4xl free 프리 프리사이즈 빅사이즈 소형 중형 대형 특대 사이즈
봄 여름 가을 겨울 간절기 사계절 봄가을 ss fw 신학기
무료배송 무배 당일발송 당일출고 빠른배송 로켓배송 특가 초특가 최저가 정품 정식수입 공식 공식몰 공식판매 본사 직영 신상 신상품 신제품
인기 베스트 best 추천 단독 한정 할인 세일 sale 핫딜 행사 증정 사은품 이벤트 쿠폰 택1 택일 옵션 옵션선택 선택 모음 모음전 균일가
외 등 무료 국내 국내산 정가 런칭 기획 기획전 대용량 초대용량 1위 브랜드 신규 new 고급 프리미엄 데일리 기본 베이직 단품 세트 종
라운드 브이넥 오버핏 루즈핏 세미오버핏 슬림핏 레귤러핏 와이드 박시 크롭 롱 숏 미디 맥시
""".split())
_SHORT_PATTERNS = (
    re.compile(r"^\d+(?:[.,]\d+)?[a-z가-힣%]{0,4}$", re.I),  # 수·용량·개수·연도 ('31cm', '2개', '500ml', '2026')
    re.compile(r"^[x×*]\d+", re.I),  # 곱수 ('x2')
    re.compile(r"\d\s*[가-힣a-z]{0,3}\+\d", re.I),  # 덤 ('1+1', '10캔+2캔')
    re.compile(r"^(?=[a-z0-9-]*\d)(?=[a-z0-9-]*[a-z])[a-z0-9-]{5,}$", re.I),  # 모델 번호 ('PHE3TR3501', 'SM-R630')
    re.compile(r"^[가-힣]{1,3}색$"),  # '검정색'·'회색'
    re.compile(r"^[가-힣]{2,}(?:한|운|는)$"),  # 꾸미는 말 ('따뜻한'·'부드러운'·'가벼운')
)


def _short_word_ok(w: str) -> bool:
    if len(w) < 2 or w.lower() in _SHORT_DROP:
        return False
    return not any(p.search(w) for p in _SHORT_PATTERNS)


def short_query(name: str, category: str | None = None) -> str:
    """쇼핑 커넥트 짧은 검색어: 브랜드(첫 낱말 또는 앞머리 [괄호] 안 브랜드) + 가운데 낱말 최대 2개 + 핵심 상품 낱말(마지막 낱말).
    크기·색·개수·옵션·홍보 말·모델 번호는 뺀다. 한 낱말만 남으면 분류 끝 칸(category)을 핵심 낱말로, 그래도 모자라면 ''."""
    s = clean_name(name or "")
    brand = ""
    m = re.match(r"^\s*[\[(【]([^\])】]{1,20})[\])】]", s)  # '[폴햄] 여성 …' 처럼 앞머리 괄호 안이 브랜드일 때
    if m:
        inner = m.group(1).strip()
        if inner.lower().replace(" ", "") not in _SHOP_TAGS and _short_word_ok(inner) and re.search(r"[가-힣a-zA-Z]", inner):
            brand = inner
    s = re.sub(r"\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】", " ", s)
    words: list[str] = []
    for w in re.split(r"[^\w+-]+", s):
        w = w.strip("-")
        if w and _short_word_ok(w) and w.lower() not in {x.lower() for x in words}:
            words.append(w)
    if brand:
        words = [brand] + [w for w in words if w.lower() != brand.lower()]
    if len(words) == 1 and category:
        leaf = re.split(r"[>/]", category)[-1].replace(" ", "")
        if _short_word_ok(leaf) and leaf.lower() != words[0].lower():
            words.append(leaf)
    if len(words) < 2:
        return ""
    if len(words) > 4:
        words = [words[0], *words[1:3], words[-1]]
    return " ".join(words)[:40].strip()


class NaverShopSearch:
    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str | None,
        client_secret: str | None,
        *,
        timeout: float = 20,
        display: int = 40,
    ) -> None:
        self.http = http
        self._id = client_id or None
        self._secret = client_secret or None
        self.timeout = timeout
        self.display = max(1, min(100, display))
        self.disabled_reason: str | None = None
        self.verified = False  # 검색이 한 번이라도 200 으로 됐나 (키가 맞는지는 첫 호출 때야 앎 — 자기 점검 표시용)

    @property
    def configured(self) -> bool:
        return bool(self._id and self._secret)

    async def _search(
        self, query: str, *, exclude: str | None = None, timeout: float | None = None, sort: str = "sim"
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query, "display": self.display, "sort": sort}
        if exclude:
            params["exclude"] = exclude
        resp = await self.http.get(
            SEARCH_URL,
            params=params,
            headers={"X-Naver-Client-Id": self._id or "", "X-Naver-Client-Secret": self._secret or ""},
            timeout=self.timeout if timeout is None else timeout,
        )
        if resp.status_code in _DEAD:
            if self.disabled_reason is None:  # 한 판에 여러 번 맞아도 경고는 한 번
                log.warning(
                    "naver shop search: HTTP %s — 키가 틀렸거나 API 를 쓸 수 없음 (네이버 쇼핑 검색 Open API 는 2026-07-31 종료, "
                    "대신할 API 없음). 다시 시작할 때까지 쿠팡 딜 네이버 최저가 비교를 끔",
                    resp.status_code,
                )
            self.disabled_reason = f"HTTP {resp.status_code}"
            raise _Unavailable
        resp.raise_for_status()
        self.verified = True
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


# ---------------------------------------------------------------- 2. 쿠팡 딜의 네이버 최저가 (가격 근거)
# 검색 결과 productType: 1 = 가격비교(카탈로그) 상품 (lprice = 여러 판매처 중 최저가), 2·3 = 새 상품 일반 판매글.
# 4~6 중고, 7~9 단종, 10~12 판매예정 → 비교에 안 씀
_CATALOG_TYPE = "1"
_NEW_TYPES = frozenset({"1", "2", "3"})
# 검색에서 아예 빼는 것 (API exclude): 중고 · 렌탈 · 해외직구/구매대행
PRICE_EXCLUDE = "used:rental:cbshop"
# 네이버 상품명에 있는데 쿠팡 상품명엔 없으면 같은 상품으로 안 본다 (그 값이 '네이버 최저가'로 잡히면 비교가 틀어짐)
_NOT_SAME_WORDS = (
    # 중고·리퍼·전시·하자·개봉품
    "중고", "리퍼", "refurb", "전시", "반품", "s급", "a급", "b급", "스크래치", "박스훼손", "하자", "단순개봉", "개봉",
    # 렌탈·약정·정기배송·구독 (월 값·회차 값이 상품값으로 잡힘)
    "렌탈", "렌털", "대여", "약정", "정기배송", "구독",
    # 해외직구·구매대행·병행수입·해외판
    "해외", "직구", "구매대행", "병행", "관부가세", "직수입", "글로벌", "global",
    # 옵션 목록 (가장 싼 옵션 값이 최저가로 잡힘)
    "택1", "택일", "골라", "모음", "옵션", "균일가", "선택",
    # 부속품·소모품 (본품 이름이 다 들어 있어도 훨씬 쌈)
    "케이스", "커버", "필름", "거치대", "호환", "리필", "교체용", "부품", "액세서리", "악세사리", "악세서리", "스트랩", "파우치",
    "필터", "배터리", "충전기", "어댑터", "케이블", "칫솔모", "브러시", "헤드",
)
# 위 낱말이 들어 있지만 그 뜻이 아닌 말 (검사 전에 지움): 미개봉 새 상품, 헤드폰·헤드셋, 브러시리스 모터
_NOT_SAME_OK = re.compile(r"미개봉|헤드폰|헤드셋|브러시리스")
# 같은 이름 줄기의 다른 상품(윗급·아랫급·맛·색·기획 묶음·사은품)을 가르는 말: 한쪽 상품명에만 있으면 다른 상품
# (버즈3 ≠ 버즈3 프로, 신라면 ≠ 신라면블랙, 바나나맛우유 ≠ 바나나맛우유 라이트, 햇반 24개 ≠ 햇반 24개 스팸 증정).
# 붙여 쓴 말('신라면블랙', '기획세트', '더블팩')도 보되, '프로바이오틱스'·'라이트닝'·'맥스웰' 처럼 앞에 오는 말은 뒤에 붙을 때만 셈
_VARIANT_ANY = ("기획", "세트", "증정", "사은품", "더블", "트윈", "듀오", "제로", "울트라", "플러스")
_VARIANT_END = ("프로", "맥스", "라이트", "미니", "블랙", "pro", "plus", "max", "ultra", "lite", "mini")
# 쿠팡 수량 규칙(pricing.market)이 못 읽는 묶음 단위 ('60정 3통', '30 롤', '2box'): 양쪽 숫자가 같아야 같은 상품 (1은 안 셈)
_PACK_RE = re.compile(r"(\d+)\s*(통|롤|묶음|켤레|box|set)(?![a-z])")
# 괄호 안 개수 ('(2박스)', '(스팸 1개 증정)'): 바깥 개수와 다르면 묶음·사은품 — 같은 상품으로 단정하지 않음
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_COUNT_WORD_RE = re.compile(r"\d\s*(?:개입|개(?!월)|입|팩|세트|박스|병|캔|봉지|봉|포|정|매|ea(?![a-z]))|x\s*\d")
_TAG_RE = re.compile(r"<[^>]+>")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")  # '1,000ml' → '1000ml'
_HAS_DIGIT = re.compile(r"\d")
_HAS_NON_DIGIT = re.compile(r"\D")
_PAUSE_ON_429 = 600.0  # 검색 API 가 429 를 주면 이만큼 쉼 (초)


def item_title(item: dict[str, Any]) -> str:
    """검색 결과 상품명 ('<b>샴푸</b> 1000ml &amp; 린스' → '샴푸 1000ml & 린스')."""
    return " ".join(html.unescape(_TAG_RE.sub("", str(item.get("title") or ""))).split())


def _won(v: Any) -> int | None:
    try:
        n = int(str(v or "").replace(",", "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _category(item: dict[str, Any]) -> str | None:
    cats = [str(item.get(f"category{i}") or "").strip() for i in range(1, 5)]
    return ">".join(c for c in cats if c) or None


def _spaced(text: str) -> str:
    """낱말 경계를 남긴 소문자 ('갤럭시 버즈 3 실버' → '갤럭시 버즈3 실버'): 글자 뒤 띄어 쓴 숫자는 붙이고, 천 단위 쉼표만 지운다."""
    s = re.sub(r"[^0-9a-z가-힣]+", " ", _THOUSANDS_RE.sub("", text.lower()))
    return re.sub(r"(?<=[a-z가-힣]) (?=\d)", "", s).strip()


def _variants(text: str) -> frozenset[str]:
    out: set[str] = set()
    for w in tokens(text):
        out.update(v for v in _VARIANT_ANY if v in w)
        out.update(v for v in _VARIANT_END if w.endswith(v))
    return frozenset(out)


def _packs(text: str) -> list[tuple[str, str]]:
    return sorted((n, u) for n, u in _PACK_RE.findall(text.lower()) if n.lstrip("0") != "1")


def _paren_count_differs(title: str, name: str) -> bool:
    """네이버 상품명의 괄호 안에 개수가 있는데 상품명 전체의 낱개 수와 다른가 ('24캔 (2박스)', '24개 (스팸 1개 증정)').
    '(총 48개)'·'(6병x2)' 는 풀어 쓴 것, 쿠팡 상품명에도 있는 괄호('1kg(5~7입)')는 같은 설명이라 안 봄."""
    total = parse_quantity(title).count
    mine = _norm(name)
    for m in _PAREN_RE.finditer(title.lower()):
        inner = m.group(1).strip()
        if inner.startswith("총") or not _COUNT_WORD_RE.search(inner) or (_norm(inner) and _norm(inner) in mine):
            continue
        if parse_quantity(inner).count != total:
            return True
    return False


def same_item(name: str, title: str, cfg: NaverReferenceConfig) -> bool:
    """쿠팡 상품명(name)과 네이버 상품명(title)이 같은 상품인가 (보수적으로, 양쪽에서).
    - 수량·용량·묶음이 같아야 함 (pricing.market.same_quantity — 2팩·1+1·'+ 증정' 묶음은 다른 상품). 수량 규칙이 못 읽는
      묶음 단위('3통', '30롤')와 괄호 안 개수('(2박스)')도 같아야 함
    - 쿠팡 상품명 낱말의 min_token_match 이상이 네이버 상품명에 있어야 함 (띄어쓰기는 무시: '대추 방울토마토' = '대추방울토마토')
    - 첫 낱말(대개 브랜드)은 있어야 하고, 숫자가 섞인 낱말(버즈3·10세대·128gb)은 통째로 있어야 함 ('버즈3' 은 '버즈3프로' 에 없음).
      띄어 쓴 세대·모델 번호('에어팟 4')도 같은 낱말 뒤에 있어야 함 ('에어팟 2세대' 는 다른 상품)
    - 프로·라이트·블랙·기획세트·증정 같은 갈래 말은 양쪽이 같아야 함 (한쪽에만 있으면 다른 상품)
    - 쿠팡 상품명에 없는 중고·렌탈·해외직구·옵션 목록·부속품 말이 네이버 상품명에 있으면 다른 상품"""
    lowered, mine = _NOT_SAME_OK.sub(" ", title.lower()), _NOT_SAME_OK.sub(" ", name.lower())
    if any(w in lowered and w not in mine for w in _NOT_SAME_WORDS):
        return False
    if not same_quantity(name, title) or _packs(name) != _packs(title):
        return False
    if _paren_count_differs(title, name):
        return False
    words = tokens(name)
    if not words:
        return False
    flat = _norm(title)
    hit = sum(1 for w in words if _norm(w) in flat)
    if hit / len(words) < cfg.min_token_match:
        return False
    if cfg.require_first_token and _norm(words[0]) not in flat:
        return False
    if _variants(name) != _variants(title):
        return False
    spaced = _spaced(title)
    models = [w for w in words if _HAS_DIGIT.search(w) and _HAS_NON_DIGIT.search(w)]
    if not all(re.search(rf"{re.escape(w)}(?![0-9a-z가-힣])", spaced) for w in models):
        return False
    return all(re.search(rf"{re.escape(m)}(?!\d)", spaced) for m in model_numbers(name))


@dataclass(slots=True)
class NaverPrices:
    """검색 결과에서 읽은, 쿠팡 상품과 확실히 같은 새 상품들의 네이버 값. 쿠팡 값과 상관없는 사실만 담는다 —
    그대로 기억해 두고 판마다 지금의 쿠팡 값과 맞춰 본다 (naver_quote). 쿠팡이 네이버에 낸 판매글은 뺀다."""

    lowest: int  # 같은 상품 전부(가격비교 상품·일반 판매글) 중 가장 싼 값 — '네이버 최저가보다 N% 싸요' 의 기준 (부풀리지 않게)
    title: str
    url: str | None = None
    category: str | None = None  # 네이버 분류 (식품 판정용 — 기억한 값에도 같이 남김)
    # 탈락 기준 값: 가격비교 상품의 최저가, 또는 서로 다른 판매처 min_listings 곳이 (값 + 배송비 여유) 이 값 이하.
    # 판매글 한 곳의 싼 값(싼 옵션·누락된 부속품·개봉품일 수 있음)만으로는 탈락시키지 않는다
    floor: int | None = None
    floor_title: str | None = None
    # lowest 가 정말 네이버 최저가인가: 가격비교 상품이 맞았거나, 낮은 가격순 검색에도 같은 상품이 나왔음.
    # 관련도순 40개만 본 일반 판매글의 최솟값은 최저가가 아닐 수 있어 'N% 싸요' 근거로 안 씀
    confirmed: bool = False
    catalog: bool = False
    sellers: int = 0  # 같은 상품 일반 판매글의 서로 다른 판매처 수

    def to_meta(self) -> str:
        return json.dumps({"category": self.category, "floor": self.floor, "floor_title": self.floor_title,
                           "confirmed": self.confirmed, "catalog": self.catalog, "sellers": self.sellers}, ensure_ascii=False)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> NaverPrices | None:
        """기억해 둔 값 (market_quotes 한 줄). 예전 꼴이라 탈락·확인 정보가 없으면 None (다시 찾음)."""
        try:
            meta = json.loads(row.get("meta") or "")
            price = int(row["price"])
        except (ValueError, TypeError, KeyError):
            return None
        if not isinstance(meta, dict) or price <= 0:
            return None
        floor = meta.get("floor")
        return cls(lowest=price, title=str(row.get("title") or ""), url=row.get("url"), category=meta.get("category"),
                   floor=int(floor) if isinstance(floor, int) and floor > 0 else None, floor_title=meta.get("floor_title"),
                   confirmed=bool(meta.get("confirmed")), catalog=bool(meta.get("catalog")), sellers=int(meta.get("sellers") or 0))


def naver_prices(name: str, items: list[dict[str, Any]], cfg: NaverReferenceConfig,
                 asc_items: list[dict[str, Any]] | None = None) -> NaverPrices | None:
    """검색 결과(관련도순 items, 있으면 낮은 가격순 asc_items 도)에서 쿠팡 상품(name)과 확실히 같은 새 상품들의 값. 없으면 None."""
    catalog: list[tuple[int, str, str | None, str | None]] = []
    general: dict[str, tuple[int, str, str | None, str | None]] = {}
    in_asc = False
    for page, its in ((False, items), (True, asc_items or [])):
        for it in its:
            if str(it.get("productType") or "").strip() not in _NEW_TYPES:
                continue  # 중고·단종·판매예정
            if "쿠팡" in str(it.get("mallName") or "") or "coupang" in str(it.get("mallName") or "").lower():
                continue  # 쿠팡이 네이버에 낸 같은 판매글은 '다른 곳 값'이 아님
            lp, title = _won(it.get("lprice")), item_title(it)
            if lp is None or not title or not same_item(name, title, cfg):
                continue
            in_asc = in_asc or page
            row = (lp, title, str(it.get("link") or "") or None, _category(it))
            if str(it.get("productType")).strip() == _CATALOG_TYPE:
                catalog.append(row)
            else:
                seller = _norm(it.get("mallName")) or str(it.get("link") or title)
                if seller not in general or lp < general[seller][0]:
                    general[seller] = row
    found = catalog + list(general.values())
    if not found:
        return None
    low = min(found, key=lambda r: r[0])
    floors: list[tuple[int, str]] = []
    if catalog:
        c = min(catalog, key=lambda r: r[0])
        floors.append((c[0], c[1]))
    by_price = sorted(general.values(), key=lambda r: r[0])
    if len(by_price) >= cfg.min_listings:
        r = by_price[cfg.min_listings - 1]
        floors.append((r[0] + cfg.general_shipping_allowance, r[1]))
    floor = min(floors) if floors else None
    cat = next((r[3] for r in (*catalog, low, *by_price) if r[3]), None)
    confirmed = bool(catalog) or (in_asc and len(general) >= cfg.min_listings)
    return NaverPrices(lowest=low[0], title=low[1], url=low[2], category=cat, floor=floor[0] if floor else None,
                       floor_title=floor[1] if floor else None, confirmed=confirmed, catalog=bool(catalog), sellers=len(general))


def naver_quote(ref: NaverPrices, price: int, cfg: NaverReferenceConfig) -> tuple[MarketQuote | None, str]:
    """기억한(또는 방금 찾은) 네이버 값과 지금 쿠팡 값(price)으로 평가기에 넘길 근거. (근거, 이유). 확실하지 않으면 (None, 이유).
    - 같은 상품 판매글이 쿠팡 값의 min_price_ratio 배보다 싸면: 낱개·옵션·다른 상품일 수 있어 근거도 탈락도 없음
    - 탈락 기준 값(floor)이 쿠팡 값 이하: 그 값으로 (평가기가 '네이버보다 싸지 않음'으로 탈락)
    - 탈락 기준엔 못 미치는 판매글(한 곳뿐이거나 배송비를 더하면 비쌈)만 쿠팡 값 이하: 확실하지 않음 (가격 이력으로 판정)
    - 모두 쿠팡보다 비쌈: 최저가가 확인된 것(confirmed)만 'N% 싸요' 근거로 — 너무 큰 차이(max_below_pct)는 다른 상품일 수 있어 안 씀"""
    if ref.lowest < price * cfg.min_price_ratio:
        return None, f"쿠팡 값의 {cfg.min_price_ratio:g}배도 안 되는 같은 상품 판매글 있음 — 확실하지 않음"
    if ref.floor is not None and ref.floor <= price:
        q = MarketQuote(price=ref.floor, source=NAVER_SOURCE, title=ref.floor_title or ref.title, category=ref.category)
        return q, "네이버가 더 쌈" + (" (가격비교 상품)" if ref.catalog else f" (판매처 {cfg.min_listings}곳↑)")
    if ref.lowest <= price:
        return None, f"더 싼 판매글({ref.lowest:,}원)이 있지만 탈락 기준엔 못 미침 — 확실하지 않음"
    if not ref.confirmed:
        return None, f"가격비교 상품 없이 판매글 {ref.sellers}곳 — 최저가로 단정 안 함"
    if (1 - price / ref.lowest) * 100 > cfg.max_below_pct:
        return None, f"네이버 {ref.lowest:,}원보다 {cfg.max_below_pct:g}% 넘게 쌈 — 다른 상품일 수 있음"
    q = MarketQuote(price=ref.lowest, source=NAVER_SOURCE, title=ref.title, url=ref.url, category=ref.category)
    return q, "가격비교 상품" if ref.catalog else "낮은 가격순 확인"


def naver_lowest(name: str, price: int, items: list[dict[str, Any]], cfg: NaverReferenceConfig,
                 asc_items: list[dict[str, Any]] | None = None) -> tuple[MarketQuote | None, str]:
    """검색 결과와 쿠팡 값으로 바로 근거 (naver_prices + naver_quote)."""
    ref = naver_prices(name, items, cfg, asc_items)
    if ref is None:
        return None, "같은 상품 없음"
    return naver_quote(ref, price, cfg)


class LookupRun:
    """수집 한 판의 네이버 조회 한도: 호출 수와 쓴 시간 (5분 수집 루프를 오래 막지 않게). 판이 끝나면 버린다."""

    def __init__(self, max_calls: int, seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_calls = max(0, max_calls)
        self.seconds = max(0.0, seconds)
        self._clock = clock
        self.started = clock()
        self.calls = 0
        self.matched = 0  # 같은 상품의 네이버 값을 찾음
        self.no_match = 0  # 검색했는데 확실한 같은 상품이 없음
        self.skipped = 0  # 한도·시간·쉬는 중이라 이번 판엔 못 찾아봄 (다음 판에)
        self.failed = 0  # 일시 오류 (기억하지 않음)
        self.kept_veto = 0  # 다시 못 찾아서 지난 값의 '네이버가 더 쌈' 탈락을 그대로 씀

    def time_left(self) -> float:
        return self.seconds - (self._clock() - self.started)

    def allows(self) -> bool:
        return self.calls < self.max_calls and self.time_left() > 0

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in (("calls", self.calls), ("matched", self.matched), ("no_match", self.no_match),
                                          ("skipped", self.skipped), ("failed", self.failed), ("kept_veto", self.kept_veto)) if v]
        return " ".join(parts)


class NaverPriceReference:
    """쿠팡 상품의 네이버 쇼핑 값 (가격 근거). 검색은 NaverShopSearch (키가 없거나 401 이면 꺼짐).
    한 판(LookupRun)·한 시간 호출 수를 넘지 않고, 호출 사이엔 간격을 둔다. 429 면 잠깐 쉰다.
    일시 오류(429·5xx·시간 초과)와 한도로 못 찾은 것은 '없음'으로 기억하지 않는다 (last_no_match 가 False — 부르는 쪽이 다음 판에 다시).
    가격비교 상품 없이 일반 판매글만 맞았는데 쿠팡이 기준 넘게 싸 보이면, 낮은 가격순으로 한 번 더 찾아 최저가를 확인한다
    (못 하면 그 값은 'N% 싸요' 근거로 안 쓰고 last_complete 가 False — 부르는 쪽이 기억하지 않고 다음 판에 다시)."""

    source = NAVER_SOURCE

    def __init__(
        self,
        search: NaverShopSearch,
        cfg: NaverReferenceConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.search = search
        self.cfg = cfg
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._next_at = 0.0
        self._pause_until = 0.0
        self._told_missing = False
        self.last_no_match = False  # 마지막 lookup 이 실제로 검색했는데 확실한 같은 상품이 없었나
        self.last_complete = True  # 마지막 lookup 결과를 그대로 기억해도 되나 (최저가 확인 검색을 못 했으면 False)

    @property
    def usable(self) -> bool:
        """키가 있고 401 등으로 꺼지지 않았나. 키가 없으면 그 사실을 한 번만 로그 (값은 안 남김)."""
        if not self.search.configured:
            if not self._told_missing:
                self._told_missing = True
                log.info("NAVER_CLIENT_ID/NAVER_CLIENT_SECRET not set — 쿠팡 딜의 네이버 최저가 비교는 꺼짐 (가격 이력으로만 판정)")
            return False
        return self.search.disabled_reason is None

    def new_run(self) -> LookupRun:
        return LookupRun(self.cfg.max_lookups_per_run, self.cfg.run_seconds, self._clock)

    def hour_available(self) -> bool:
        now = self._clock()
        while self._calls and now - self._calls[0] > 3600:
            self._calls.popleft()
        return len(self._calls) < self.cfg.max_lookups_per_hour

    def calls_last_hour(self) -> int:
        self.hour_available()
        return len(self._calls)

    async def _turn(self, run: LookupRun) -> bool:
        """이번 호출을 해도 되나: 한 판·한 시간 한도, 429 쉬는 중, 호출 간격(판의 남은 시간 안에서만 기다림)."""
        now = self._clock()
        if now < self._pause_until or not run.allows() or not self.hour_available():
            return False
        wait = self._next_at - now
        if wait > 0:
            if wait >= run.time_left():
                return False
            await self._sleep(wait)
        now = self._clock()
        run.calls += 1
        self._calls.append(now)
        self._next_at = now + self.cfg.min_interval_seconds
        return True

    async def _fetch(self, product: Product, run: LookupRun, query: str, sort: str) -> list[dict[str, Any]] | None:
        """검색 한 번. 한도·쉬는 중이면 run.skipped, 일시 오류면 run.failed 를 올리고 None (꺼짐은 _Unavailable 그대로)."""
        if not await self._turn(run):
            run.skipped += 1
            log.debug("naver ref skipped (run/hour limit or pause): %s", product.name[:40])
            return None
        timeout = self.cfg.timeout_seconds
        try:
            return await asyncio.wait_for(
                self.search._search(query, exclude=PRICE_EXCLUDE, timeout=timeout, sort=sort), timeout + 2)
        except httpx.HTTPStatusError as e:
            run.failed += 1
            if e.response.status_code == 429:
                self._pause_until = self._clock() + _PAUSE_ON_429
                log.info("naver ref: HTTP 429 — 네이버 최저가 비교를 %d분 쉼", int(_PAUSE_ON_429 // 60))
            else:
                log.info("naver ref failed for %s: HTTP %s", product.name[:40], e.response.status_code)
        except (httpx.HTTPError, ValueError, TimeoutError) as e:
            run.failed += 1
            log.info("naver ref failed for %s: %s", product.name[:40], type(e).__name__)
        return None

    async def lookup(self, product: Product, run: LookupRun) -> NaverPrices | None:
        """같은 상품의 네이버 값 (확실할 때만). 검색어는 꾸밈을 뺀 상품명, 결과가 하나도 없을 때만 앞 네 낱말로 한 번 더.
        판정(근거·탈락)은 부르는 쪽이 naver_quote 로 지금 쿠팡 값과 맞춰 본다."""
        self.last_no_match = False
        self.last_complete = True
        if not product.has_price or not self.usable:
            return None
        items: list[dict[str, Any]] = []
        used = ""
        for query in search_queries(product.name)[:2]:
            try:
                got = await self._fetch(product, run, query, "sim")
            except _Unavailable:
                return None  # 키가 틀렸거나 API 가 닫힘: _search 가 한 번 경고하고 껐다
            if got is None:
                return None
            items, used = got, query
            if items:
                break
        ref = naver_prices(product.name, items, self.cfg)
        if ref is None:
            run.no_match += 1
            self.last_no_match = True
            log.info("naver ref: no confident match for '%s' — 같은 상품 없음 (%d results)", product.name[:40], len(items))
            return None
        if self._needs_confirm(ref, product.price):
            # 일반 판매글만 맞았고 쿠팡이 기준 넘게 싸 보임: 관련도순 40개 밖에 더 싼 같은 상품이 있는지 낮은 가격순으로 확인
            try:
                asc = await self._fetch(product, run, used, "asc")
            except _Unavailable:
                asc = None
            if asc is None:
                self.last_complete = False
            else:
                ref = naver_prices(product.name, items, self.cfg, asc_items=asc) or ref
        run.matched += 1
        log.info("naver ref for '%s' %s원 → 네이버 같은 상품 최저 %s원 (%s, 판매처 %d곳%s)%s · 탈락 기준 %s",
                 product.name[:40], f"{product.price:,}", f"{ref.lowest:,}", "가격비교 상품" if ref.catalog else "판매글만",
                 ref.sellers, ", 최저가 확인" if ref.confirmed else "", "" if self.last_complete else " (최저가 확인 못 함 — 기억 안 함)",
                 f"{ref.floor:,}원" if ref.floor else "없음")
        return ref

    def _needs_confirm(self, ref: NaverPrices, price: int) -> bool:
        """낮은 가격순 확인 검색이 판정을 바꿀 수 있나: 확인 안 된 판매글 값이고, 그대로라면 'N% 싸요' 근거가 될 만큼 쌈."""
        if ref.confirmed or not self.cfg.confirm_lowest or ref.sellers < self.cfg.min_listings or ref.lowest <= price:
            return False
        return (1 - price / ref.lowest) * 100 >= self.cfg.min_below_pct
