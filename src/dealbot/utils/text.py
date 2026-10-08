from __future__ import annotations

import re

_PRICE_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)")
_MAN_RE = re.compile(r"(\d+(?:\.\d+)?)만")


def parse_price(text: str | None) -> int | None:
    """'12,900원', '12900', '1.2만', '3만5천원' 등에서 원 단위 정수 추출. 실패 시 None."""
    if not text:
        return None
    t = text.replace(" ", "")
    m_man = _MAN_RE.search(t)
    m_num = _PRICE_RE.search(t)
    if m_man and (not m_num or m_num.start() >= m_man.start()):
        value = int(float(m_man.group(1)) * 10000)
        rest = t[m_man.end() :]
        m_cheon = re.match(r"(\d)천", rest)
        m_tail = re.match(r"(\d{1,4})(?!\d)", rest)
        if m_cheon:
            value += int(m_cheon.group(1)) * 1000
        elif m_tail:
            value += int(m_tail.group(1))
        return value
    if not m_num:
        return None
    try:
        return int(m_num.group(1).replace(",", ""))
    except ValueError:
        return None


def format_won(value: int | float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}원"


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


_DECOR = re.compile(r"[★☆♥♡◆◇■□●○※♪♬✔✅🔥🚨⭐]+")
_HYPE = re.compile(r"(초특가|역대급|핫딜|특가|대박|강추|최저가|단독)(?=[\s:!\]\)]|$)")
_TAG = re.compile(r"^\s*[\[(【](?:무배|무료배송|무료|타임딜|오늘만|한정|광고|카드|쿠폰)[^\])】]*[\])】]\s*")


def clean_name(name: str | None) -> str:
    """게시판 제목 꾸밈 걷어내기: ★·♥ 같은 기호, '초특가'·'역대급' 같은 홍보 낱말, 앞머리의 [무배]·(타임딜) 태그."""
    if not name:
        return ""
    s = _DECOR.sub(" ", name)
    for _ in range(3):
        s = _TAG.sub("", s)
    s = _HYPE.sub("", s)
    s = re.sub(r"\s*→\s*", "→", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" -·:,")
    return s or name.strip()


# 용량·개수 뒤에 붙은 x 는 곱하기 기호라 허용 ('2Lx6', '24캔x2')
_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)(?![a-wyz])", re.I)
_COUNT = re.compile(r"(?:x\s*)?(\d{1,4})\s*(개입|개|입|팩|봉지|봉|캔|병|펫|페트|pet|롤|매|포|구|정|박스|박|세트|ea)(?![가-힣a-wyz])", re.I)
# 단위 없는 곱수 'x 8'. 영문·숫자 바로 뒤의 x ('RTX 4090', '150x200')나 뒤에 영문 단위가 붙는 수 ('X 256GB', 'x 30cm')는 곱수가 아님
_TIMES = re.compile(r"(?<![a-z\d])x\s*(\d{1,4})(?![\d.,]*\s*[a-wyz])", re.I)
# 1+1·2+1·'10캔+2캔' 같은 덤 표기. 가격이 묶음 전체 값인지 알 수 없어서 계산하지 않음
_BUNDLE = re.compile(r"\d\s*[가-힣a-z]{0,3}\s*\+\s*\d", re.I)
# 겉포장 낱말 (작을수록 안쪽). 다른 곱수와 같이 나오면 '팩당'·'박스당'이 아니라 낱개로 나눈 값이라 '개당'으로 씀
_OUTER = {"봉": 1, "봉지": 1, "팩": 2, "박스": 3, "박": 3, "세트": 3}
# 이름의 kg·L 이 내용물이 아니라 크기·용적인 물건 (가전·용기·가방·봉투). '100ml당'이 말이 안 되니 용량 단가는 빼고 개수 단가만 남김.
# 'X용'·'X전용'·'X에'·'X구이' 는 그 물건에 쓰는 소모품·음식이라 제외 ('에어프라이어용 감자 2kg', '오븐구이 김')
_DURABLE = re.compile(
    r"(?:세탁기|건조기|워시타워|트롬|그랑데|비스포크|냉장고|냉동고|청소기|식기세척기|에어프라이어|전자레인지|오븐|밥솥|"
    r"전기포트|커피포트|주전자|커피머신|정수기|공기청정기|제습기|가습기|블렌더|믹서기|탈수기|"
    r"텀블러|보온병|보냉병|물병|물통|워터보틀|머그|냄비|프라이팬|"
    r"수납|정리함|보관함|리빙박스|아이스박스|쿨러|밀폐용기|보관용기|반찬통|김치통|쓰레기통|휴지통|"
    r"봉투|지퍼백|위생백|가방|배낭|캐리어)(?!\s*(?:용|전용|구이|에))"
)
_DURABLE_CAT = re.compile(r"가전|디지털|가구|인테리어|주방용품|수납")
_CONSUMABLE = re.compile(r"세정제|클리너|세제|리필|살균제|탈취제")


def unit_price(name: str | None, price: int | None, category: str | None = None) -> str | None:
    """상품명의 용량·개수로 단가를 계산한다. 예) '왕교자 1.05kg x 2봉' 13,900원 → '100g당 662원'.
    확실히 읽히는 경우만 (용량 1개 + 개수 최대 2개). 애매하면 None.
    category(쿠팡 분류)를 주면 가전·주방용품처럼 용량이 내용물이 아닌 물건을 더 잘 거른다."""
    if not name or not price:
        return None
    text = name.replace("×", "x").replace("*", "x")
    if _BUNDLE.search(text):
        return None
    sizes = _SIZE.findall(text)
    body = _SIZE.sub(" ", text)
    named = _COUNT.findall(body)
    # 모르는 단위가 붙은 숫자가 남아 있으면 (예: '20입수', '3박스입') 개수를 잘못 읽을 수 있으니 계산하지 않음
    rest = _COUNT.sub(" ", body)
    if re.search(r"\d\s*(?!겹|단|종|인치|년|세|구|중|차|호|겹|분|시간|도)[가-힣]", rest):
        return None
    # '3개입 x 8', '24캔 x 2', '2L x 6 x 2팩' 처럼 단위 있는 개수와 단위 없는 곱수가 같이 나오면 둘 다 곱함
    times = [int(n) for n in _TIMES.findall(rest)]
    counts = [int(n) for n, _ in named] + times
    if len(counts) > 2 or len(sizes) > 1:
        return None
    count = 1
    for c in counts:
        count *= c
    if not 1 <= count <= 1000:
        return None
    unit_word = min((w for _, w in named), key=lambda w: _OUTER.get(w, 0), default="개")
    if times and unit_word in _OUTER:
        unit_word = "개"
    word = {"개입": "개", "입": "개", "ea": "개", "pet": "병", "펫": "병", "페트": "병", "박": "박스"}.get(unit_word.lower(), unit_word)
    each = f"{word}당 {round(price / count):,}원" if count >= 2 else None
    durable = bool(_DURABLE.search(text) or (category and _DURABLE_CAT.search(category))) and not _CONSUMABLE.search(text)
    per = None
    if sizes and not durable:
        qty = float(sizes[0][0].replace(",", "."))
        unit = sizes[0][1].lower()
        base = "g" if unit in ("g", "kg") else "ml"
        one = qty * (1000 if unit in ("kg", "l") else 1)
        total = one * count
        # 한 개에 20L 넘는 건 내용물보다 용적(리빙박스·쿨러·냉장고)인 경우가 대부분
        if total > 100 and not (base == "ml" and one >= 20000):
            per = f"100{base}당 {round(price / total * 100):,}원"
    if each and per:
        return f"{each} ({per})"
    return each or per
