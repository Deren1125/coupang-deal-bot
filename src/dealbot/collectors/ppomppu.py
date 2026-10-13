"""뽐뿌 핫딜 게시판 크롤러 (모든 쇼핑몰).

- 목록에서 제목 "[쇼핑몰] 상품명 (가격/배송)" 을 파싱하고, 등록된 쇼핑몰(config.shops) 글만 고른다.
- 새 글은 상세 페이지를 열어 쇼핑몰 링크를 찾는다. 이미 본 글은 다시 열지 않지만,
  추천/댓글/조회수가 관심도 게이트 기준을 넘어서면 저장된 링크로 다시 판정 대상에 올린다
  (중복 발행은 dedup 이 막고, 가격 기록은 observation_min_gap_hours 간격으로만 남긴다).
- 사이트 구조가 바뀌면 options.*_selector 만 고치면 된다.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from dealbot.collectors.base import BaseCollector
from dealbot.collectors.registry import register
from dealbot.models import Product
from dealbot.shops import Shop, ShopRegistry, find_urls
from dealbot.utils.retry import retry_async
from dealbot.utils.text import parse_price
from dealbot.utils.urls import canonical_product_url, is_short_affiliate_link, unwrap_redirect

BASE_URL = "https://www.ppomppu.co.kr"
LIST_URL = BASE_URL + "/zboard/zboard.php"
MOBILE_BASE_URL = "https://m.ppomppu.co.kr"
MOBILE_LIST_URL = MOBILE_BASE_URL + "/new/bbs_list.php"

# 뽐뿌는 평범한 크롤러 요청을 403 으로 막는다. 실제 브라우저가 보내는 헤더를 갖춰야 통과한다.
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# "[쿠팡] 상품명 (12,900원/무료)" 형태. 끝 괄호 안의 괄호 한 겹("3,000원(3만↑무료)", "무료(와우)")과
# 괄호 뒤의 짧은 꼬리말("와우", "카드할인")도 받는다 — 꼬리말은 가격 괄호 뒤일 때만 이름에서 뺀다.
# 이름은 공백 아닌 글자로 끝나고 그 뒤 공백은 되돌려 주지 않는다(\S\s*+) — 긴 공백 줄 뒤에 다른 글자가 오면
# 공백마다 다시 훑어 세제곱으로 느려졌다 (공백 1,000칸에 약 2초)
_TITLE_RE = re.compile(
    r"^\s*\[(?P<shop>[^\]]+)\]\s*(?P<name>.*?\S)\s*+"
    r"(?:\((?P<meta>(?:[^()]|\([^()]*\))*)\)\s*+(?P<note>[^()]{0,15}?))?\s*+$"
)
# 괄호 속 금액 후보: "12,900원", "1.5만원", "3만5천원", "1만2000원", "12900"
_AMOUNT_RE = re.compile(
    r"(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?P<man>\s*만(?:\s*\d+\s*천|\d{1,4}(?![\d,]))?|\s*천)?(?P<won>\s*원)?"
)
_WON_RE = re.compile(r"\d\s*(?:[만천]\s*)?원")
# '12,900' 처럼 세 자리마다 쉼표를 찍고 단위가 붙지 않은 수는 연식·모델 번호가 아니라 값 ('10,000피스' 는 아니다)
_GROUPED_NUM = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d가-힣A-Za-z])")
# 쉼표 찍은 수 뒤에 띄어 쓴 말. 혜택 말('12,900 쿠폰적용')이나 값에 붙는 말('와우', '카드', '최저가')이 아니면 그 수는
# 개수·크기다 ('1,000 매', '1,500 피스', '3,840 x 2,160')
_WORD_AFTER = re.compile(r"\s++(?!와우|카드|최저|특가|역대|최종|실구매|실결제)[가-힣A-Za-z]")
_BEFORE_SLASH = re.compile(r"(?:[^/()]|\([^()]*\))*+")  # 가격 칸: 괄호 밖 첫 '/' 앞까지
# 단위 없는 숫자 바로 뒤에 붙은 개수·용량 단위 — 가격이 아니다 ("100매 9,900원", "10,000mAh", "500ml 24개").
# 곱하기 x 가 붙어 있어도 단위다 ("500mlx24개")
_QTY_AFTER = re.compile(
    r"(?:개|팩|매|입|캔|병|봉|롤|박스|세트|장(?!바구니)|켤레|족|포(?!함)|정(?![가상])|알|캡슐|마리|인분|종|개월|일(?![시반])|년|시간|명|겹|벌|권|곽|갑|송이|포기|근|키로|리터|그램|미리)"
    r"|\s*+(?:ml|l|g|kg|mg|cc|mah|wh|gb|tb|cm|mm|m|w|v|hz|p|ea|box|set|pcs|%|인치)(?![a-wyz])",
    re.IGNORECASE,
)
# 혜택 말. 앞에 짧은 꾸밈말이 두 개까지 붙어도 된다 ('장바구니쿠폰', '즉시할인', 'T멤버십 할인', '네이버페이 적립',
# '스마일캐시'). 꾸밈말 없는 core 말만 붙은 금액은 혜택 금액이다 ('10,000원 할인', '5,000원 쿠폰')
_BENEFIT_WORD = r"(?P<core>할인(?![전율률])|쿠폰|적립|캐시백|페이백|환급|증정)|캐시|포인트|마일리지"
_BENEFIT_PHRASE = rf"(?P<mod>(?:[가-힣A-Za-z]{{1,6}}\s?){{0,2}}?)(?:{_BENEFIT_WORD})"
_BENEFIT_AFTER = re.compile(rf"\s*+{_BENEFIT_PHRASE}")
_CONDITION_AFTER = re.compile(r"\s*+(?:이상|미만|초과)")  # '5만원 이상' 은 조건 금액
# 혜택 말 뒤에 그 금액이 판매가라는 말: 할인가·쿠폰가·쿠폰(할인)가·할인된 가격, 쿠폰적용·쿠폰 미적용, 적립포함·증정품 포함,
# 쿠폰 사용시·적립금 사용시, 쿠폰 후. 이런 금액도 뒤에 판매가가 이어지면 쿠폰·적립 금액일 수 있다 (_later_price)
_PRICE_WORDING = re.compile(r"[금품된]?\s*(?:할인\s*)?(?:(?P<unapplied>미적용)|후|가(?!능)|적용|포함|사용)")
# 혜택 말이 금액 앞에 오는 꼴 ('쿠폰 3,000원 적용시 19,900원', '적립금 2,000원 사용시', '쿠폰할인 3,000원, 19,900원')
_BENEFIT_BEFORE = re.compile(r"(?:할인|쿠폰|적립금?|캐시백?|페이백|포인트|마일리지)\s*$")
# 앞 금액과 뒤 금액 사이에 와도 되는 말: 앞 금액에 붙은 혜택 말('쿠폰', '장바구니쿠폰') 다음에 판매가로 이어 주는 말
# ('적용시', '사용 후', '포함', '받고', '하면', '으로', '할인된 가격', '최종', '실구매가', ':', '=')이나 쉼표·'와우' 같은 말만.
# 모르는 말('일반', '판매가', '정가', '첫구매 적립금', '아닐 때')이 끼면 뒤 금액을 판매가로 보지 않는다
_TIGHT_LINK = (
    r"적용|사용|포함|후|시|하면|하여|해서|으로|받고|받아서?|받으면|다운(?:로드)?|된|가격|금액|가|총|최종가?"
    r"|실구매가?|실결제가?|실질|결제(?:금액|가)?|최저가?|기준|개당|당|[:=]"
)
_LOOSE_LINK = re.compile(r"[,+]|와우|회원")  # 다른 조건을 붙이는 말 — 뒤 금액이 더 작으면 따로 붙은 값이다
# 앞 금액을 조건으로 받거나('쿠폰 적용 시', '할인 후', '적용하면', '받으면', '할인된') 뒤 금액이 치르는 값이라는 말
# ('최종', '실구매가'). 이런 말 없이 '쿠폰가'·'쿠폰적용'으로만 이어지면 앞 금액도 판매가로 적힌 것이다
_IF_LINK = re.compile(r"시|후|하[면여]|해서|으로|받|된|최종|실구매|실결제")
# '쿠폰가'·'쿠폰적용가'·'카드할인가' 의 '가'. 공백은 되돌려 주지 않는다(\s*+) — 두 \s* 가 같은 공백 줄을 나눠 가지면
# '가' 가 없을 때 공백 길이의 제곱으로 느려졌다
_PRICE_GA = re.compile(r"\s*+(?:(?:적용|사용|할인)\s*+)?가(?!격)")
_COUNT_PLUS = re.compile(r"\s*\+\s*(?=\d)")  # '1+1'·'2+1' 의 '+' 는 금액을 나누는 '+' 가 아니다
_GAP_PIECE = re.compile(rf"\s*+(?P<lead>{_BENEFIT_PHRASE}[금품]?)?(?P<rest>(?:\s+|{_TIGHT_LINK}|{_LOOSE_LINK.pattern})*+)")
# 뒤 금액 바로 뒤에 와도 되는 말. 이 밖의 말이 붙으면 판매가가 아니다: 배송비('2,500원 배송'), 쿠폰 없는 값·정가
# ('19,900원 일반', '299,000원 출고가', '69,000원 소비자가'), 적립·절약액('15,000원 캐쉬백', '1만원 저렴', '16,000원 DC'),
# 몇 개 값('10,950원 1개'), 조건('3,000원 (3만원 이상 무료)'). 끝이거나 '~'·'부터'·'배송비 포함'·'무료배송'·'(개당 1,295원)',
# '최종', 다른 금액으로 이어지는 '+'·','·'일반'('+ 2,000원 적립', ', 2개 이상 구매시', '일반 13,900원')만 된다
_TAIL_OK = re.compile(
    r"\s*+(?:$|[~/]|부터|\(?\s*(?:배송비|택배비)\s*포함|무료\s*배송|무배|(?:최종|실구매|실결제)가?\s*$"
    r"|\(\s*\S{0,6}?당\s*\d|(?:[,+]|일반)\s*\d)"
)
# 앞 금액이 쿠폰 금액이 뻔할 때('5,000원 쿠폰 적용시 29,900원 와우', '10,000원 할인 25,000원 (최저가)') 더 큰 뒤 금액에
# 붙으면 안 되는 말: 정가·쿠폰 없는 값('정가', '출고가', '일반', '미적용'), 적립·절약액('캐쉬백', '적립', '저렴', 'DC'),
# 배송비('배송비 별도', '택배'), 몇 개 값('1개', '(2개)'). 이 밖의 꼬리말('와우', '역대가', '선착순', '!')은 판매가에 붙는다
_TAIL_BLOCK = re.compile(
    r"\s*+\(?\s*(?:배송|택배|별도|정가|원가|정상|출고|소비자|시중|판매가|일반|미적용|권장|출시|기존|캐쉬|캐시|리워드|적립"
    r"|상품권|페이|머니|지급|선물|상당|사은품|증정|돌려|보상|반환|저렴|이득|차이|싸게|아낌|다운|인하|낮|(?i:dc)|디씨|세이브|\d)"
)
# 단위당 값과 판매가 사이에 와도 되는 말: 띄어쓰기, '총'·'합계'·'최종' ('개당 647원, 총 25,900원')
_UNIT_GAP = re.compile(r"\s*+(?:,?\s*(?:총|합계|최종가?)\s*)?")
# 배송비로 흔한 값 — 싼 상품 값보다 작은 뒤 금액이 이 값이면 배송비일 수 있다 ('3,900원 쿠폰적용 2,500원')
_SHIPPING_FEES = frozenset((2000, 2500, 3000, 3500, 4000, 5000, 6000))
# 묶음 개수: '2개', '3팩', '2+1'(3개). '개당'·'1개입' 처럼 단위당이거나 묶음 안 낱개 수는 아니다
_BUNDLE_COUNT = re.compile(r"(\d{1,4})\s*(?:\+\s*(\d{1,4})(?!\d)|(?:개|팩|세트|박스|봉|병|캔|통)(?![당입]))")
_UNIT_WORDS = r"g|kg|ml|l|개|팩|봉|매|롤|입|캔|병|구|ea"
# 단위당 값: '개당 9,900원', '100g당 1,790원', '1,790원 꼴', '1,790원/100g'
_UNIT_AFTER = re.compile(rf"\s*+(?:꼴|당|/\s*(?:\d+(?:\.\d+)?\s*)?(?:{_UNIT_WORDS})(?![a-z]))", re.IGNORECASE)
# '/100g/무료' 처럼 슬래시 사이에 단위만 있으면 배송비 칸이 아니라 앞 가격의 단위
_UNIT_SEGMENT = re.compile(rf"^\s*(?:\d+(?:\.\d+)?\s*)?(?:{_UNIT_WORDS})\s*당?\s*$", re.IGNORECASE)
_APPROX_AFTER = re.compile(r"대(?![가-힣])(?:\s*[초중후]반)?")  # '1만원대', '2만원대 초반' 은 어림값
_ARROW_RE = re.compile(r"→|->|⇒|➡|▶")  # "29,900원→19,900원" 은 화살표 뒤가 지금 가격
_COUPON_WORDS = ("쿠폰", "할인코드", "적립", "캐시백")
_DATE_META = re.compile(r"^\s*~?\s*\d{1,2}\s*/\s*\d{1,2}")  # "(~9/13)", "(9/12~9/14)" 같은 기간 표기
_NAME_PRICE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,})\s*원")
_NOT_A_PRICE_WORDS = ("쿠폰", "할인", "적립", "캐시백", "페이백", "증정", "환급")
_EVENT_WORDS = ("이벤트", "응모", "추첨", "무료체험", "증정")
_SKIP_HOSTS = ("ppomppu.co.kr",)


def parse_title(title: str) -> dict[str, Any]:
    """제목 → shop_tag / name / price / shipping."""
    text = title.strip()
    m = _TITLE_RE.match(text)
    if not m:
        return {"shop_tag": None, "name": text, "price": None, "shipping": None}
    meta = (m.group("meta") or "").strip()
    name = m.group("name").strip()
    shop_tag = m.group("shop").strip()
    head = _BEFORE_SLASH.match(meta).end()  # 안쪽 괄호 속 '/' 는 칸 나눔이 아니다 ('12,900원(1/2개)/무료')
    price_text, shipping = meta[:head], meta[head + 1 :]
    unit, slash, rest = shipping.partition("/")
    if slash and _UNIT_SEGMENT.match(unit):
        price_text, shipping = f"{price_text}/{unit}", rest
    price = _meta_price(price_text)
    if price is None and _SHIPPING_WORDS.search(shipping):
        # 배송비 칸까지 적힌 상품 딜("12,900원 할인/무료")이면 혜택 말이 붙은 금액도 판매가로 본다
        price = _meta_price(price_text, benefit_ok=True)
    if (
        (m.group("note") or "(" in meta)
        and not _SHIPPING_WORDS.search(shipping)
        and (price is None or not (_WON_RE.search(price_text) or _grouped_price(price_text, price)))
    ):
        # 가격 괄호가 아닌데 뒤에 말이 더 있거나 괄호가 겹친 제목("에어팟 프로 2 (USB-C) 화이트", "맥북 에어 (2024) 실버")은
        # 통째로 이름. '원'도 쉼표 찍은 값('12,900')도 배송비 칸도 없는 숫자 괄호는 연식·용량·모델 번호·개수·크기다
        whole = text[m.start("name"):].strip()
        return {"shop_tag": shop_tag, "name": whole, "price": _price_in_name(whole), "shipping": None}
    if meta and (meta.startswith("~") or _DATE_META.match(meta)):
        # "(~9/13)" 은 기간이지 가격이 아니다 — 이름에 남기고 가격은 이름에서 찾는다
        return {"shop_tag": shop_tag, "name": f"{name} ({meta})", "price": _price_in_name(name), "shipping": None}
    if price is None:
        price = _price_in_name(name)
    return {"shop_tag": shop_tag, "name": name, "price": price, "shipping": shipping.strip() or None}


_SHIPPING_WORDS = re.compile(r"무료|무배|배송|착불|\d\s*원")


def _grouped_price(text: str, price: int) -> bool:
    """판매가 price 가 '12,900' 처럼 쉼표 찍은 단위 없는 수로 적혔는지. 그 수 뒤에 띄어 쓴 말이 붙었으면 혜택 말이나
    값에 붙는 말일 때만 ('12,900 쿠폰적용') — '1,000 매'·'1,500 피스'·'3,840 x 2,160' 은 개수·크기다."""
    digits = str(price)  # 수를 int 로 바꾸지 않는다 (아주 긴 수는 int 변환이 막힌다)
    return any(
        g.group().replace(",", "") == digits
        and (not _WORD_AFTER.match(text, g.end()) or _BENEFIT_AFTER.match(text, g.end()))
        for g in _GROUPED_NUM.finditer(text)
    )


def _meta_price(text: str, *, benefit_ok: bool = False) -> int | None:
    """괄호 속 가격 칸("12,900원", "2개 12,900원", "29,900원→19,900원")의 판매가. 없으면 None.

    예전(맨 앞 숫자)처럼 첫 금액이 판매가다. 다만 개수·용량("2개", "500ml", "10,000mAh")과 100 미만의 수는 건너뛰고,
    단위당 값 바로 뒤에 더 큰 금액만 있으면("개당 647원 25,900원") 그 금액으로 넘어가며, 첫 금액에 혜택 말이
    붙었으면('5,000원 쿠폰 사용시', '19,900원 쿠폰가', '쿠폰 3,000원 적용시', '10,000원 할인') _later_price 가 뒤 금액으로
    바꿀 수 있다. 혜택 금액만 있으면 판매가가
    없다(benefit_ok 면 혜택 말이 붙은 첫 금액도 판매가). 화살표가 있으면 그 뒤에서 먼저 찾는다.
    금액마다 바로 앞뒤 글만 보고 뒤로 한 번만 훑으니(되부르기 없음) 글 길이에 비례해 끝난다."""
    if not text:
        return None
    parts = _ARROW_RE.split(text)
    for chunk in ([parts[-1], text] if len(parts) > 1 else [text]):
        price = _chunk_price(chunk, benefit_ok)
        if price is not None:
            return price
    return None


def _chunk_price(chunk: str, benefit_ok: bool) -> int | None:
    """화살표로 나눈 한 토막의 판매가 (_meta_price)."""
    amounts = [(a, _amount_value(chunk, a)) for a in _AMOUNT_RE.finditer(chunk)]
    prev_end = 0
    for i, (a, value) in enumerate(amounts):
        before, prev_end = chunk[prev_end : a.start()], a.end()
        if value is None:
            continue
        if benefit_ok:
            return value
        if before.rstrip().endswith("당"):
            # '개당 647원 25,900원' — 단위당 값 바로 뒤의 더 큰 금액이 판매가. 다른 말·배송비·혜택 금액이 이어지면
            # ('개당 990원, 배송비 3,000원', '개당 990원 + 2,500원', '개당 990원, 1,000원 쿠폰', '개당 990원 1만원 쿠폰
            # 적용시') 단위당 값을 둔다
            if _after_unit(chunk, amounts, i, value):
                continue
            return value
        kind = _benefit_kind(chunk, a, before)
        if kind in ("plain", "unapplied"):
            return value
        later = _later_price(chunk, amounts, i, value, kind)
        if later is not None:
            return later
        if kind != "benefit":
            return value
        # 혜택·조건 금액('10,000원 할인', '5만원 이상')을 판매가로 이을 말이 없다. 남은 금액도 다 혜택·조건 금액이면
        # 판매가가 없고, 아니면 예전처럼 첫 금액을 둔다 ('12,900원 할인 2,500원')
        rest = [b for b, v in amounts[i + 1 :] if v is not None]
        return value if any(_benefit_kind(chunk, b) != "benefit" for b in rest) else None
    return None


def _after_unit(chunk: str, amounts: list[tuple[re.Match[str], int | None]], i: int, unit: int) -> bool:
    """단위당 값 amounts[i] 다음 금액을 판매가로 이어 읽을지. 사이에 띄어쓰기·'총'(_UNIT_GAP)과 개수·용량만 있고, 그
    금액이 단위당 값보다 크고 배송비로 흔한 값이 아니어야 한다. 혜택 말 없는 금액은 뒤에 붙은 말도 판매가다워야(_TAIL_OK)
    하고, 혜택 말이 붙은 금액은 '25,900원 쿠폰가'·'38,700원 쿠폰 적용시' 처럼 쿠폰·적립 금액답지 않은 값일 때만 판매가다
    ('1만원 쿠폰 적용시'·'1,500원 적립 포함'·'1,000원 추가할인' 은 쿠폰·적립 금액). 혜택·조건 금액('1,000원 쿠폰',
    '3만원 이상')과 쿠폰 없는 값('1,500원 쿠폰 미적용')은 판매가가 아니다."""
    pos = amounts[i][0].end()
    for b, y in amounts[i + 1 :]:
        if not _UNIT_GAP.fullmatch(chunk, pos, b.start()):
            return False
        if y is None:
            qty = _QTY_AFTER.match(chunk, b.end())
            pos = qty.end() if qty else b.end()
            continue
        kind = _benefit_kind(chunk, b)
        if y <= unit or y in _SHIPPING_FEES or kind in ("benefit", "unapplied"):
            return False
        if kind != "plain":  # 'said'·'loose' — 혜택 말이 붙은 금액
            return not _coupon_like(y)
        return bool(_TAIL_OK.match(chunk, b.end()))
    return False


def _amount_value(chunk: str, a: re.Match[str]) -> int | None:
    """금액 후보 a 의 값. 개수·용량('2개', '500ml', '1만개')이거나 100 미만이면 None (날짜·개수 등)."""
    if not a.group("won") and _QTY_AFTER.match(chunk, a.end()):
        return None
    num, man = a.group("num").replace(",", ""), (a.group("man") or "").strip()
    if len(num) > 12:  # 가격일 리 없는 긴 숫자
        return None
    if man == "천":
        value: int | None = round(float(num) * 1000)  # '5천원' (parse_price 는 5 로 읽는다)
    elif man:
        value = parse_price(a.group(0))  # '1.5만원', '3만5천원', '1만2000원'
    else:
        value = int(num.partition(".")[0])  # '12,900원', '12900' ('12.900원' 은 parse_price 처럼 12)
    return value if value is not None and value >= 100 else None


def _benefit_kind(chunk: str, a: re.Match[str], before: str = "") -> str:
    """금액 a 앞뒤 글로 본 금액의 성격.
    'benefit' — 혜택·조건 금액 ('10,000원 할인', '5,000원 쿠폰', '5만원 이상'). 판매가가 아니다
    'said' — 혜택 말 뒤에 판매가라는 말이 붙음 ('19,900원 쿠폰가', '5,000원 쿠폰 사용시')
    'loose' — 꾸밈말 붙은 혜택 말만 붙음 ('189,000원 카드할인', '5만원 카드할인 시')
    'before' — 혜택 말이 금액 앞에 옴 ('쿠폰 3,000원 적용시', '적립금 2,000원 사용 후')
    'unapplied' — 쿠폰 없는 값 ('19,900원 쿠폰 미적용')
    'plain' — 그 밖의 금액"""
    b = _BENEFIT_AFTER.match(chunk, a.end())
    if b:
        said = _PRICE_WORDING.match(chunk, b.end())
        if said:
            return "unapplied" if said.group("unapplied") else "said"
        return "benefit" if b.group("core") and not b.group("mod") else "loose"
    if _CONDITION_AFTER.match(chunk, a.end()):
        return "benefit"
    if _BENEFIT_BEFORE.search(before):
        return "before"
    return "plain"


def _coupon_like(value: int) -> bool:
    """쿠폰·적립 금액다운 둥근 값: 1,000·5,000·2만·10만처럼 유효숫자가 한 자리이거나 1,500·2.5만처럼 5로 끝나는 두 자리."""
    digits = value
    while digits % 10 == 0:
        digits //= 10
    return value % 500 == 0 and (digits < 10 or (digits < 100 and digits % 10 == 5))


def _later_price(
    chunk: str, amounts: list[tuple[re.Match[str], int | None]], i: int, x: int, x_kind: str
) -> int | None:
    """혜택 말이 붙은 금액 amounts[i] (값 x, 성격 x_kind) 대신 판매가로 볼 뒤 금액 y. 없으면 None (x 가 판매가).

    뒤로 한 번 훑으며 개수·용량('2개', '1+1'), 어림값('1만원대'), 혜택 금액('2,000원 추가적립')은 건너뛰고 처음 만나는
    금액을 y 로 본다(혜택 말이 붙은 y 가 판매가가 못 되면 그것도 건너뛴다). 쿠폰 없는 값('15,900원 미적용')을 만나면
    그만둔다. y 는 이럴 때만 판매가다.
    - 금액 사이의 말이 모두 _GAP_PIECE 로 읽힌다. 모르는 말('일반', '판매가', '첫구매 적립금')이 끼면 x 를 둔다
    - y 바로 뒤의 말도 판매가답다 (_TAIL_OK). '캐쉬백'·'저렴'·'출고가'·'(3만원 이상 무료)' 같은 말이 붙으면 x 를 둔다.
      다만 x 가 쿠폰 금액이 뻔하고('5,000원 쿠폰 적용시', '10,000원 할인' 의 둥근 값) y 가 더 크면 정가·적립·배송 말
      (_TAIL_BLOCK)만 아니면 된다 ('5,000원 쿠폰 적용시 24,900원 와우', '1만원 쿠폰 적용시 49,900원, 무료배송')
    - y 가 x 보다 크면 x 가 쿠폰·적립 금액다운 둥근 값이고, 개수 뒤의 y 가 x 의 배수(묶음 값)가 아니다. x 가 조건 말
      없이 '쿠폰가'·'쿠폰적용'으로 적혔으면(판매가로 적힘) '+' 뒤의 y('1,000원 쿠폰적용 + 2,500원'), '쿠폰가'·배송비
      값일 때 쉼표 뒤의 y('1,000원 쿠폰적용, 3,500원 배송비포함'), x 의 N 개 묶음 값쯤 되는 y('20,000원 쿠폰가, 2개
      38,000원')도 판매가가 아니다
    - y 가 x 보다 작으면 쿠폰으로 깎인 값이다: 혜택 말 다음에 '사용시'·'후'·'가' 같은 이어 주는 말이 있고, 쉼표·'와우'·
      개수 같은 다른 조건이 끼지 않았고, y 에 따로 혜택 말이 붙지 않았으며, x 의 절반보다 크고, 배송비로 흔한 값
      ('3,900원 쿠폰적용 2,500원')이 아니다. 혜택 말만 사이에 있으면('19,900원 카드할인 9,000원') 그 말이 y 에 붙은
      것일 수 있어 x 를 둔다
    - 단위당 값('개당', '꼴', '/100g')은 x 가 쿠폰 금액일 때만 ('1만원 할인 후 개당 9,900원'), 그리고 x 의 절반보다 클
      때만. '10,000원 쿠폰가 개당 5,500원' 처럼 x 가 판매가로 적혔고 단위당 값이 더 작으면 x 를 둔다"""
    pos = amounts[i][0].end()
    first = _GAP_PIECE.match(chunk, pos)
    ga = bool(first and _PRICE_GA.match(first.group("rest")))
    loose = counted = plus = comma = cond = False
    n = 0  # 사이에 적힌 마지막 묶음 개수
    for b, y in amounts[i + 1 :]:
        piece = _GAP_PIECE.fullmatch(chunk, pos, b.start())
        if not piece:
            return None
        rest = piece.group("rest")
        loose = loose or bool(_LOOSE_LINK.search(rest))
        plus, comma = plus or "+" in rest, comma or "," in rest
        cond = cond or bool(_IF_LINK.search(rest))
        if y is None:  # 개수·용량은 단위까지 건너뛴다 ('1+1' 의 '+' 까지)
            qty = _QTY_AFTER.match(chunk, b.end()) or _COUNT_PLUS.match(chunk, b.end())
            pos, loose, counted = (qty.end() if qty else b.end()), True, True
            bundle = _BUNDLE_COUNT.match(chunk, b.start())
            n = int(bundle.group(1)) + int(bundle.group(2) or 0) if bundle else n
            continue
        approx = _APPROX_AFTER.match(chunk, b.end())
        if approx:
            pos, loose = approx.end(), True
            continue
        kind = _benefit_kind(chunk, b)
        if kind == "unapplied":
            return None
        if kind in ("benefit", "loose"):
            pos = b.end()
            continue
        unit_after = _UNIT_AFTER.match(chunk, b.end())
        if kind != "said" and not unit_after and not _TAIL_OK.match(chunk, b.end()):
            # 앞 금액이 쿠폰 금액이 뻔하면('쿠폰 적용시'·'할인 후'·'10,000원 할인' 의 둥근 값) 더 큰 y 의 꼬리말은
            # 정가·적립·배송 말(_TAIL_BLOCK)만 아니면 된다 ('5,000원 쿠폰 적용시 24,900원 와우')
            coupon_first = (cond or x_kind in ("benefit", "before")) and y > x and _coupon_like(x)
            if not coupon_first or _TAIL_BLOCK.match(chunk, b.end()):
                return None
        x_as_price = x_kind == "said" and not cond  # '쿠폰가'·'쿠폰적용' — '쿠폰 적용 시'·'할인 후' 면 x 는 쿠폰 금액
        if unit_after or piece.group(0).rstrip().endswith("당"):
            price = y if _coupon_like(x) and y * 2 > x and not (x_as_price and y < x) else None
        elif y > x:
            # 개수 뒤의 x 배수나, x 가 판매가로 적혔을 때 x 의 N 개 묶음 값쯤(0.7·N·x ~ N·x) 되는 y 는 묶음 값이다
            bundle = counted and (y % x == 0 or (x_as_price and n >= 2 and 7 * n * x <= 10 * y <= 10 * n * x))
            apart = x_as_price and (plus or (comma and (ga or y in _SHIPPING_FEES)))  # 따로 붙은 배송비·다른 값
            price = y if _coupon_like(x) and not bundle and not apart else None
        elif loose or kind == "said" or not rest.strip():
            price = None
        else:
            price = y if y * 2 > x and y not in _SHIPPING_FEES else None
        if price is not None or kind != "said":
            return price
        pos = b.end()  # 판매가가 못 되는 '1만원 쿠폰 적용 시' 같은 금액은 혜택 금액처럼 넘어간다
    return None


def _price_in_name(name: str) -> int | None:
    """괄호에 가격이 없을 때 "9,900원 샴푸" 처럼 이름 앞쪽에 적힌 가격. 쿠폰·할인 금액과 헷갈리는 이름이면 안 쓴다."""
    if any(w in name for w in _NOT_A_PRICE_WORDS):
        return None
    m = _NAME_PRICE.search(name)
    if not m:
        return None
    try:
        value = int(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return value if value >= 100 else None


def guess_kind(name: str, price: int | None) -> str:
    low = name.lower()
    if any(w in low for w in _EVENT_WORDS):
        return "event"
    if price is None and any(w in low for w in _COUPON_WORDS):
        return "coupon"
    if price is None:
        return "event"
    return "hotdeal"


def _first_int(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"-?\d[\d,]*", text)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def decode_html(resp: httpx.Response) -> str:
    """뽐뿌는 EUC-KR 이 섞여 있어 헤더 charset 이 없으면 euc-kr → utf-8 순으로 시도."""
    ctype = resp.headers.get("content-type", "").lower()
    if "charset=" in ctype:
        try:
            return resp.text
        except (UnicodeDecodeError, LookupError):
            pass
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            return resp.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return resp.content.decode("utf-8", errors="replace")


def parse_list_page(
    html: str,
    *,
    row_selector: str,
    title_selector: str,
    thumb_selector: str,
    rec_selector: str = "td.baseList-rec",
    views_selector: str = "td.baseList-views",
    comments_selector: str = "span.baseList-c",
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict[str, Any]] = []
    for row in soup.select(row_selector):
        a = row.select_one(title_selector)
        if a is None:
            continue
        href = a.get("href") or ""
        if "view.php" not in href:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        no = parse_qs(urlparse(href).query).get("no", [None])[0]
        if not no:
            continue
        img = row.select_one(thumb_selector)
        thumb = None
        if img is not None:
            src = img.get("src") or img.get("data-src")
            if src:
                thumb = src if src.startswith("http") else ("https:" + src if src.startswith("//") else urljoin(BASE_URL, src))
        rec_el = row.select_one(rec_selector) if rec_selector else None
        views_el = row.select_one(views_selector) if views_selector else None
        comments_el = row.select_one(comments_selector) if comments_selector else None
        items.append(
            {
                "external_id": str(no),
                "title": title,
                "post_url": urljoin(LIST_URL, href),
                "thumb": thumb,
                "recommend": _first_int(rec_el.get_text(" ", strip=True) if rec_el else None),
                "views": _first_int(views_el.get_text(" ", strip=True) if views_el else None),
                "comments": _first_int(comments_el.get_text(" ", strip=True) if comments_el else None),
                **parse_title(title),
            }
        )
    return items


def find_shop_urls(html: str, shop: Shop | None) -> list[str]:
    """글 상세 HTML 에서 링크 후보를 우선순위대로: 해당 쇼핑몰 도메인 > 그 외 외부 링크."""
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = unwrap_redirect(a["href"].strip())
        if href.startswith("http"):
            candidates.append(href)
    candidates.extend(unwrap_redirect(u) for u in find_urls(html))

    def external(u: str) -> bool:
        host = urlparse(u).netloc.lower()
        return bool(host) and not any(host.endswith(h) for h in _SKIP_HOSTS)

    uniq = [u for u in dict.fromkeys(candidates) if external(u)]
    if shop is None:
        return uniq
    matched = [u for u in uniq if shop.matches_url(u)]
    # 쿠팡은 상품 페이지 > 단축링크 순
    if shop.key == "coupang":
        matched.sort(key=lambda u: 0 if canonical_product_url(u) else (1 if is_short_affiliate_link(u) else 2))
    return matched


@register("ppomppu")
class PpomppuCollector(BaseCollector):
    requires_coupang = False
    _warmed = False

    @property
    def registry(self) -> ShopRegistry:
        reg = getattr(self.ctx, "shops", None)
        return reg if reg is not None else self.ctx.settings.shop_registry()

    async def _warm_up(self, base_url: str) -> None:
        """목록을 읽기 전에 홈을 한 번 방문해 쿠키를 받는다 (봇 차단 회피)."""
        if self._warmed:
            return
        self._warmed = True
        try:
            await self.ctx.http.get(base_url + "/", headers=BROWSER_HEADERS, follow_redirects=True, timeout=15)
        except Exception as e:  # noqa: BLE001 — 실패해도 본 요청은 시도한다
            self.log.debug("warm-up failed for %s: %s", base_url, e)

    async def _get(self, url: str, params: dict[str, Any] | None = None, *, referer: str | None = None) -> str:
        http_cfg = self.ctx.settings.http
        headers = {**BROWSER_HEADERS}
        if referer:
            headers["Referer"] = referer

        async def _do() -> str:
            resp = await self.ctx.http.get(url, params=params, headers=headers, follow_redirects=True)
            resp.raise_for_status()
            return decode_html(resp)

        return await retry_async(
            _do, attempts=http_cfg.max_retries, backoff=http_cfg.retry_backoff_seconds, label=f"GET {url}"
        )

    async def _resolve_deal_url(self, url: str, shop: Shop | None) -> str:
        """쿠팡이면 정규화(타인 단축링크는 원본으로), 그 외는 그대로."""
        if shop is not None and shop.key == "coupang":
            canon = canonical_product_url(url)
            if canon:
                return canon
            if is_short_affiliate_link(url) and self.ctx.settings.links.resolve_short_links:
                try:
                    resp = await self.ctx.http.get(url, follow_redirects=True)
                    canon = canonical_product_url(str(resp.url))
                    if canon:
                        return canon
                except httpx.HTTPError as e:
                    self.log.debug("short link resolve failed %s: %s", url, e)
        return url

    def _build_product(self, item: dict[str, Any], shop: Shop, deal_url: str) -> Product:
        name = item["name"]
        price = item.get("price")
        kind = guess_kind(name, price)
        return Product(
            source=self.name,
            product_id=ShopRegistry.product_key(shop.key, deal_url),
            shop=shop.key,
            deal_kind=kind,
            name=name,
            price=int(price or 0),
            url=deal_url,
            image_url=item.get("thumb"),
            shipping=item.get("shipping"),
            is_free_shipping=("무료" in (item.get("shipping") or "")) or None,
            external_id=item["external_id"],
            recommend_count=item.get("recommend"),
            view_count=item.get("views"),
            comment_count=item.get("comments"),
            extra={"post_url": item["post_url"], "shop_tag": item.get("shop_tag"), "title": item["title"]},
        )

    async def collect(self) -> list[Product]:
        board = self.opt("board_id", "ppomppu")
        pages = int(self.opt("pages", 1))
        delay = float(self.opt("request_delay_seconds", 2))
        max_detail = int(self.opt("max_detail_fetch_per_run", 15))
        row_sel = self.opt("list_row_selector", "tr.baseList")
        title_sel = self.opt("title_selector", "a.baseList-title")
        thumb_sel = self.opt("thumb_selector", "img")
        rec_sel = self.opt("rec_selector", "td.baseList-rec")
        views_sel = self.opt("views_selector", "td.baseList-views")
        comments_sel = self.opt("comments_selector", "span.baseList-c")
        only_shops = {s.lower() for s in self.opt("shops", [])}  # 비우면 등록된 모든 쇼핑몰
        unknown_policy = self.opt("unknown_shop", "skip")  # skip | raw
        interest = self.ctx.settings.deal.interest
        registry = self.registry

        rule = self.ctx.settings.deal.per_source.get(self.name)
        min_rec = interest.min_recommend if rule is None or rule.interest_min_recommend is None else rule.interest_min_recommend
        min_com = interest.min_comments if rule is None or rule.interest_min_comments is None else rule.interest_min_comments
        min_views = interest.min_views if rule is None or rule.interest_min_views is None else rule.interest_min_views

        def passes_gate(it: dict[str, Any]) -> bool:
            rec, com, views = it.get("recommend") or 0, it.get("comments") or 0, it.get("views") or 0
            return (
                (min_rec > 0 and rec >= min_rec)
                or (min_com > 0 and com >= min_com)
                or (min_views > 0 and views >= min_views)
            )

        list_url = self.opt("list_url", LIST_URL)
        base_url = self.opt("base_url", BASE_URL)
        self._warmed = False
        await self._warm_up(base_url)

        listed: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            if page > 1:
                await asyncio.sleep(delay)
            try:
                html = await self._get(list_url, params={"id": board, "page": page}, referer=base_url + "/")
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 403 or not self.opt("mobile_fallback", True):
                    raise
                # 데스크톱이 막히면 모바일 주소로 한 번 더 (구조가 달라 셀렉터는 options 로 조정)
                self.log.warning("ppomppu desktop 403 — 모바일 주소로 재시도합니다")
                self._warmed = False
                await self._warm_up(MOBILE_BASE_URL)
                html = await self._get(
                    self.opt("mobile_list_url", MOBILE_LIST_URL),
                    params={"id": board, "page": page},
                    referer=MOBILE_BASE_URL + "/",
                )
            items = parse_list_page(
                html, row_selector=row_sel, title_selector=title_sel, thumb_selector=thumb_sel,
                rec_selector=rec_sel, views_selector=views_sel, comments_selector=comments_sel,
            )
            if not items:
                self.log.warning("ppomppu list page %d: no rows matched '%s' — site layout may have changed", page, row_sel)
            listed.extend(items)

        products: list[Product] = []
        fetched = 0
        for item in listed:
            tag = item.get("shop_tag")
            shop = registry.by_alias(tag) if tag else None
            if shop is None and unknown_policy != "raw":
                if not self.ctx.db.is_seen(self.name, item["external_id"]):
                    self.ctx.db.mark_seen(self.name, item["external_id"], None, title=item["title"], recommend=item.get("recommend"), views=item.get("views"), comments=item.get("comments"))
                else:
                    self.ctx.db.touch_seen(self.name, item["external_id"], recommend=item.get("recommend"), views=item.get("views"), comments=item.get("comments"))
                continue
            if shop is not None and (not shop.enabled or shop.link_mode == "skip"):
                continue
            if only_shops and (shop is None or shop.key not in only_shops):
                continue
            if shop is None:
                shop = Shop(key="unknown", name=tag or "기타", link_mode="raw")

            ext = item["external_id"]
            if self.ctx.db.is_seen(self.name, ext):
                # 이미 본 글: 추천/조회수 갱신(통계용). 추천 수가 올라왔으면 저장된 링크로 다시 판정 대상에
                self.ctx.db.touch_seen(self.name, ext, recommend=item.get("recommend"), views=item.get("views"), comments=item.get("comments"))
                if passes_gate(item):
                    seen = self.ctx.db.seen_item(self.name, ext)
                    if seen and seen[0] and seen[1]:
                        p = self._build_product(item, shop, seen[1])
                        p.product_id = seen[0]
                        products.append(p)
                continue

            if fetched >= max_detail:
                break
            if fetched > 0 and delay > 0:
                await asyncio.sleep(delay)
            fetched += 1
            try:
                detail_html = await self._get(item["post_url"], referer=list_url)
            except Exception as e:  # noqa: BLE001
                self.log.warning("detail fetch failed %s: %s", item["post_url"], e)
                continue

            urls = find_shop_urls(detail_html, None if shop.key == "unknown" else shop)
            deal_url = await self._resolve_deal_url(urls[0], shop) if urls else None
            if not deal_url:
                # 링크가 없으면 뽐뿌 글 자체를 링크로 (쿠폰/이벤트 안내글 등)
                deal_url = item["post_url"]
            product = self._build_product(item, shop, deal_url)
            self.ctx.db.mark_seen(
                self.name, ext, product.product_id, url=deal_url,
                title=item["title"], recommend=item.get("recommend"), views=item.get("views"), comments=item.get("comments"),
            )
            products.append(product)

        self.log.info("ppomppu(%s): %d listed, %d detail fetched, %d products", board, len(listed), fetched, len(products))
        return products
