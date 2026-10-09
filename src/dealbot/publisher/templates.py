"""Jinja2 템플릿 렌더링 (텔레그램 HTML parse_mode 기준, 자동 이스케이프)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from dealbot.models import Deal, Product
from dealbot.shops import Shop
from dealbot.utils.text import clean_name, format_won, unit_price


def _pct(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


# ---- 배송 문구: 게시판 줄임말('무배', '네멤무배')과 금액만 적힌 배송비('3,000원')를 읽는 사람 말로
_SHIP_FEE_ONLY = re.compile(r"(?:배송비)?(\d[\d,]*)원?")
_SHIP_FREE_ONLY = re.compile(r"무배|무료|무료배송|free|freeshipping", re.I)


def shipping_text(raw: str | None) -> str | None:
    """'무배' → '무료배송', '네멤무배' → '네이버 멤버십 무료배송', '3,000원' → '배송비 3,000원'.
    조건부('2,500(3만↑무료)')처럼 줄여 말할 수 없는 건 원문을 살리되, 금액으로 시작하면 '배송비'를 붙인다."""
    s = " ".join(str(raw or "").split()).strip(" /")
    if not s:
        return None
    compact = s.replace(" ", "")
    m = _SHIP_FEE_ONLY.fullmatch(compact)
    if m:
        fee = int(m.group(1).replace(",", ""))
        return "무료배송" if fee == 0 else f"배송비 {fee:,}원"
    free = re.search(r"무배|무료", compact) is not None
    if free and re.search(r"네멤|멤버십", compact):
        return "네이버 멤버십 무료배송"
    if free and "와우" in compact:
        return "와우 회원 무료배송"
    if _SHIP_FREE_ONLY.fullmatch(compact):
        return "무료배송"
    s = s.replace("무배", "무료배송")
    return f"배송비 {s}" if s[0].isdigit() else s


# ---- 스레드 문장에 넣는 짧은 이름 (AI 가 못 지었을 때)
_BRACKET = re.compile(r"\[[^\[\]]*\]|【[^【】]*】|\([^()]*\)")
_PROMO_IN_BRACKET = re.compile(r"타임딜|한정|무배|무료배송|네멤|쿠폰|카드|광고|오늘만|특가|단독|핫딜|최저가|적립")
_MODEL_CODE = re.compile(r"(?<![\w-])(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9-]{8,}(?![\w-])")  # VS28C973DRG
_QTY = re.compile(r"\d[\d,.]*\s*(?:개입|개|입|병|캔|봉|롤|팩|구|정|매|포|박스|세트|켤레|kg|g|ml|mL|L|l)(?![가-힣A-Za-z])")
_QTY_ONLY = re.compile(rf"(?:{_QTY.pattern}|[\sx×*])+")  # '12병', '1.05kg', '500ml x 3개' 처럼 수량만 있는 칸
_PART_SEP = re.compile(r"\s+([+/])\s+")


def short_name(name: str | None, limit: int = 26) -> str:
    """스레드 '근데 {이름} {가격}' 에 넣을 이름: 게시판·홍보 괄호, ' + ' 구성품 나열을 빼고 낱말 단위로 줄인다.
    용량·수량 괄호('(2개)')와 '[샘플]' 표시는 남긴다. ' / 12병', ' + 1.05kg' 처럼 뒤 칸이 수량이면 빼지 않고 붙인다
    (묶음 가격이 한 개 값처럼 읽히지 않게). 모델 코드는 그래도 길 때만 빼고, 줄이다 수량이 잘리면 끝에 다시 붙인다."""
    base = clean_name(name)

    def _bracket(m: re.Match[str]) -> str:
        seg = m.group(0)
        inner = seg[1:-1]
        if "샘플" in inner:  # 테스트 글이 진짜 딜로 보이지 않게 남김
            return seg
        return " " if _PROMO_IN_BRACKET.search(inner) or not re.search(r"\d", inner) else seg

    s = _BRACKET.sub(_bracket, base)
    parts = _PART_SEP.split(s)  # [본품, 구분자, 칸, 구분자, 칸, ...]
    s = parts[0]
    for sep, part in zip(parts[1::2], parts[2::2], strict=False):
        if not _QTY.search(part):
            continue  # 숫자 없는 구성품('물걸레 브러시')·사이즈·연식은 뺀다
        # '2L / 12병' → '2L 12병' (수량만 있는 칸), '1.05kg + 1.05kg' · '본품 500ml + 리필 500ml' 은 구분자째
        s += f" {part}" if sep == "/" and _QTY_ONLY.fullmatch(part) else f" {sep} {part}"
    s = " ".join(re.sub(r"\s*,\s*", " ", s).split())  # '로우슈거, 24개' → '로우슈거 24개' (문장 안 쉼표는 어색함)
    if len(s) > limit:  # 모델 코드('VS28C973DRG')는 길 때만 뺀다 — 짧은 이름에선 'WH-1000XM5' 가 곧 상품 이름
        s = " ".join(_MODEL_CODE.sub(" ", s).split())
    if len(s) > limit:
        out = ""
        for w in s.split():
            if len(out) + len(w) + (1 if out else 0) > limit:
                break
            out = f"{out} {w}".strip()
        qty = _QTY.findall(s[len(out):])
        s = f"{out} {qty[-1]}" if out and qty else (out or s[:limit])
    return s or base


def price_band(ref: int, price: int = 0) -> int:
    """'평소엔 N원대'의 N: 앞 두 자리만 남기고 내린다 (41,233 → 41,000 · 8,750 → 8,700 · 1,234,567 → 1,200,000).
    내려서 '원대'가 맞는 말로 남게 하되, 오늘 가격보다 낮아지거나 같아지면 한 자리씩 더 살린다 (그래도 안 되면 그대로)."""
    digits = len(str(abs(ref)))
    for keep in (2, 3):
        step = 10 ** max(0, digits - keep)
        band = ref // step * step
        if band > price:
            return band
    return ref


# 스레드 첫 줄·마무리가 같은 말을 되풀이하면 어색함 ('그냥 넘기기엔 아까운 가격' + '가격은 수시로 바뀌니까…')
_SHARED_STEMS = ("필요", "쟁여", "가격", "장바구니", "찾던", "품절", "링크", "후기")
# 큰 가전·전자기기 낱말. 상품명에 이게 있어도 소모품·부속('식기세척기 세제', '드럼세탁기용', '정수기 필터',
# '아이패드 케이스')이면 그 물건이 아니므로 가전·디지털 첫 줄을 쓰지 않는다
_APPLIANCE_WORDS = ("세탁기", "건조기", "워시타워", "청소기", "공기청정기", "가습기", "제습기", "커피머신", "에스프레소머신", "에어프라이어",
                    "전자레인지", "냉장고", "냉동고", "밥솥", "선풍기", "서큘레이터", "식기세척기", "정수기", "전기포트", "드라이기",
                    "전동칫솔", "면도기")
_DIGITAL_WORDS = ("모니터", "노트북", "태블릿", "아이패드", "이어폰", "헤드폰", "헤드셋", "키보드", "마우스", "스마트워치", "갤럭시", "아이폰",
                  "SSD", "외장하드")
_DURABLE = re.compile("|".join(sorted(_APPLIANCE_WORDS + _DIGITAL_WORDS, key=len, reverse=True)))
# '건조기 시트', '세탁기 거름망', '밥솥 내솥', '커피머신 세척 태블릿', '노트북 파우치', '다이슨 호환 배터리'도 부속·소모품
# ('세척'은 '식기세척기' 안의 '세척'을 빼고 본다)
_ACCESSORY = re.compile(
    r"세제|세척(?!기)|클리너|필터|호일|봉투|살균제|탈취제|거치대|케이스|커버|필름|(?<!아이)패드|리필|카트리지|정리함|부품|소모품"
    r"|시트|거름망|내솥|용기|파우치|호환|교체|부속|먼지통|배터리"
    rf"|(?:{_DURABLE.pattern})\s*(?:용|전용)(?![가-힣])"
)
# 낱말이 들어 있지만 다른 물건 ('휴지통'≠휴지, '콜라겐'≠콜라)
_NOT_THE_WORD = ("휴지통", "마우스피스", "콜라겐")
# 가격 없는 쿠폰·이벤트 글에 안 맞는 말 (AI 첫 줄에 있으면 쿠폰·이벤트용 문구로 바꿈)
_PRICE_TALK = re.compile(r"가격|싸|쌈|\d\s*원|쟁여|장바구니")
# 상품명(게시판 제목)에 붙은 할인 자랑('30% 저렴하게', '평소보다 20% 싸게', '쿠팡보다 10%')은 스레드 이름 줄에서 뺀다.
# 주인이 뺀 'N% 쌈' 꼴이고(TH-02, 발행 직전 검사에 걸려 글이 안 올라감), 비교는 바로 아랫줄 금액이 한다.
# '드림카카오 72%'·'과즙 100%'·'10% 할인쿠폰' 같은 상품 정보는 둔다
_PCT_CLAIM = re.compile(
    r"(?:(?:평소|쿠팡|평균)\s*(?:가\s*)?(?:보다|대비)\s*)?\d+(?:\.\d+)?\s*%\s*(?:나|더|정도|가량|쯤)?\s*"
    r"(?:싸\S*|쌈|저렴\S*|빠짐|빠졌\S*|낮음|내려\S*)"
    r"|(?:평소|쿠팡|평균)\s*(?:가\s*)?(?:보다|대비)\s*\d+(?:\.\d+)?\s*%\S*"
)


def _drop_pct_claims(name: str) -> str:
    return " ".join(_PCT_CLAIM.sub(" ", name).split()) or name


class TemplateRenderer:
    def __init__(self, templates_dir: Path, tz: str = "Asia/Seoul", channels: dict[str, str] | None = None) -> None:
        self.templates_dir = Path(templates_dir)
        self.tz = ZoneInfo(tz)
        # 템플릿에서 channels.telegram_url 처럼 씀. 없는 키는 빈 문자열
        self.channels = {"telegram_url": "", "kakao_openchat_url": "", "threads_url": "", **(channels or {})}
        # 텔레그램(HTML 서식)용: 특수문자 이스케이프
        self.env = self._build_env(autoescape=True)
        # 카카오·스레드·블로그(평문)용: 이스케이프하면 &lt; 같은 문자가 그대로 복사되므로 끔
        self.env_plain = self._build_env(autoescape=False)

    def _build_env(self, *, autoescape: bool) -> Environment:
        env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(default=True, default_for_string=True) if autoescape else False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["won"] = format_won
        env.filters["pct"] = _pct
        env.filters["local"] = self._local
        env.filters["clean"] = clean_name
        return env

    def _local(self, dt: datetime | str | None, fmt: str = "%m/%d %H:%M") -> str:
        if dt is None:
            return "-"
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(self.tz).strftime(fmt)

    # 강조 단계 기준: 평소 가격(30일 평균·쿠팡 시중가) 대비 이만큼 싸면 must(꼭 사야) / top(역대급)
    emphasis: tuple[float, float] = (50.0, 70.0)
    # 최근 평균 대비를 근거로 쓰려면 지금 가격이 기록 최저가 +이 % 안이어야 함 (평가기 near_low_pct 와 같은 뜻).
    # 평균보다 싸도 며칠 전 훨씬 싼 값이 있었으면 '평소보다 N%↓'·'초특가'는 거짓에 가까움
    near_low_pct: float | None = 5.0
    # 'N일 중 제일 쌈' 은 기록이 이만큼 쌓이고 관측이 이만큼 있을 때만 (3일·관측 1건으로 '최저'라 하지 않음)
    low_min_days: int = 7
    low_min_samples: int = 3

    def avg_trusted(self, deal: Deal) -> bool:
        """최근 평균가 대비 수치를 글에 써도 되나: 평균이 있고, 지금 가격이 기록 최저가 근처일 때만."""
        p, v = deal.product, deal.verdict
        if v.below_avg_pct is None or not v.avg_price:
            return False
        if self.near_low_pct is None or not v.low_price or not p.price:
            return True
        return p.price <= v.low_price * (1 + self.near_low_pct / 100)

    def reference(self, deal: Deal) -> tuple[int | None, float | None, bool]:
        """글에 내세우는 '평소 가격' 근거: (기준가, 그보다 몇 % 싼지, 쿠팡 시중가인지).
        쿠팡 시중가 대조가 먼저(배송비를 반영한 평가기 값), 없으면 믿을 만한 최근 평균. 10% 미만 차이는 내세우지 않음."""
        p, v = deal.product, deal.verdict
        if v.market_price and (v.below_market_pct or 0) >= 10:
            # 평가기 값은 배송비까지 더해 소수 첫째 자리로 반올림한 것 → 배송비가 붙으면 그쪽(더 작은 값)을 쓴다
            exact = (1 - p.price / v.market_price) * 100 if p.price else 0.0
            return v.market_price, min(exact, float(v.below_market_pct or 0) + 0.05), True
        if v.avg_price and p.price and self.avg_trusted(deal) and (v.below_avg_pct or 0) >= 10:
            ref = int(v.avg_price)
            return ref, (1 - p.price / ref) * 100, False
        return None, None, False

    def deal_tier(self, deal: Deal) -> tuple[str, float]:
        """'normal' | 'must' | 'top' 과 그 근거 비율. 첫 줄에 보이는 근거(reference)와 같은 숫자로 정해서
        배지와 '쿠팡보다 N%↓' 가 어긋나지 않게 한다. 표시 할인율은 부풀려지기 쉬워 안 쓴다."""
        _, ref_pct, _ = self.reference(deal)
        pct = ref_pct or 0.0
        must, top = self.emphasis
        if top > 0 and pct >= top:
            return "top", pct
        if must > 0 and pct >= must:
            return "must", pct
        return "normal", pct

    def low_label(self, deal: Deal, days: int) -> str | None:
        """'N일 최저가(갱신)' 라벨. 기록이 짧거나 관측이 적으면, 또는 값이 내내 같았으면(오르내린 적 없음) 붙이지 않는다."""
        p, v = deal.product, deal.verdict
        if not (v.low_price and p.price and days >= self.low_min_days and v.sample_count >= self.low_min_samples):
            return None
        if p.price < v.low_price:
            return f"{days}일 최저가 갱신"
        # 최저가와 같은 값: 평균이 지금보다 3% 이상 높아야 실제로 비쌌던 때가 있었던 것
        if p.price == v.low_price and v.avg_price and p.price <= v.avg_price * 0.97:
            return f"{days}일 최저가"
        return None

    def deal_facts(self, deal: Deal, *, recent: Sequence[str] = ()) -> dict[str, Any]:
        """글에 쓰는 사실들 (모두 데이터에서 계산한 것만). 텔레그램·블로그 내보내기가 같이 쓴다.
        recent: 최근 스레드 글에 쓴 첫 줄·마무리 (같은 문구를 다시 안 쓰게)."""
        p, v = deal.product, deal.verdict
        days = int(min(v.history_days or 0, 30))
        labels: list[str] = []
        tier, _ = self.deal_tier(deal)
        if tier == "top":
            labels.append("역대급")
        elif tier == "must":
            labels.append("강력 추천")
        low = self.low_label(deal, days)
        if low:
            labels.append(low)
        src = (p.source or "").lower()
        if "goldbox" in src:
            labels.append("골드박스 특가")  # 골드박스 목록 순서는 판매 순위가 아니라 순위는 쓰지 않음
        elif "category_best" in src or "best" in src:
            cat = (p.category or "카테고리").split(">")[-1].strip()
            labels.append(f"{cat} 베스트 {p.rank}위" if p.rank else f"{cat} 베스트")
        # 배송: 한 칸에 한 가지 말로 (정보줄 칸 구분 ' · ' 와 안 겹치게). 게시판 원문은 읽는 말로 바꿈
        if p.is_rocket:
            ship = "무료 로켓배송" if p.is_free_shipping else "로켓배송"
        else:
            ship = shipping_text(p.shipping) or ("무료배송" if p.is_free_shipping else "")
        ref_price, ref_pct, is_market = self.reference(deal)
        pct = round(ref_pct) if ref_pct else None
        # 표시(정가) 할인율은 근거로 안 씀. 평소 가격 근거가 없을 때만 정보줄에 보조로
        list_pct = round(v.discount_rate) if not pct and v.discount_rate and v.discount_rate >= 10 else None
        # 채널 글(정보 항목형) 칸: 할인분류 · 가격비교 · 판매처 · 순위형성 · 방장 한줄평
        kind_map = {"역대급": "초특가", "강력 추천": "강추"}
        kind_labels = [kind_map.get(x, x) for x in labels if "베스트" not in x]
        rank_label = next((x for x in labels if "베스트" in x and "위" in x), None)
        if ref_price and pct:
            compare = f"쿠팡 최저가 {ref_price:,}원" if is_market else f"평소 {price_band(ref_price, p.price):,}원대"
        else:
            compare = None
        auth = (p.extra or {}).get("auth") or {}
        genuine = "공식 판매처 확인" if auth.get("status") == "ok" and str(auth.get("reason") or "").startswith("공식") else None
        return {
            "kind_labels": kind_labels,
            "rank_label": rank_label,
            "compare": compare,
            "genuine": genuine,
            "one_liner": self.one_liner(p),
            "labels": labels,
            "ship": ship,
            "cat": (p.category or "").split(">")[-1].strip(),
            "unit": unit_price(clean_name(p.name), p.price),
            "low_price": v.low_price if days >= 3 else None,
            "avg_price": int(v.avg_price) if v.avg_price and days >= 3 and self.avg_trusted(deal) else None,
            "history_days": days,
            "sale_pct": pct,
            "list_pct": list_pct,
            "comment": (p.extra or {}).get("comment"),
            "rank": p.rank,
            "ref_price": ref_price,
            # 블로그 내보내기 줄에도 첫 줄 근거와 같은 기준 (믿을 수 없는 평균이면 None)
            "ref_pct": pct,
            "ref_label": ("쿠팡 최저가" if is_market else "평소 가격") if ref_price else "",
            **self.evidence(deal, days=days, ref_price=ref_price, pct=pct, labels=labels, tier=tier,
                            is_market=is_market, recent=recent),
        }

    # 스레드 첫 줄 (AI 가 못 썼을 때). (문장, 반전): 반전=True 는 생활 속 상황·불편이라 다음 줄을 '근데 {이름} {가격}' 으로
    # 뒤집고, False 는 그냥 하는 말이라 '근데' 없이 '{이름} {가격}'. 가격·경험·약속을 지어내는 말, 훈수('~게 맞음', '~게 답임',
    # '손해임'), 주인이 뺀 말('필요했던 사람만 보면 됨', '찾던 사람 있을 것 같아서…')은 넣지 않는다 (commentary.THREAD_BANNED).
    # 그룹: (상품명에 이 낱말이 있으면, 쿠팡 분류 칸에 이 말이 있으면, 첫 줄들). 앞 그룹이 먼저.
    # 상품명은 헷갈리지 않는 낱말만 본다 ('청소기'≠'청소', '커피머신'≠'커피', '바디필로우'≠'바디워시', '토너'는 프린터 토너도 있어 분류로만).
    # 뷰티를 먹거리보다 먼저 본다 ('우유 바디워시'는 뷰티).
    THREAD_HOOK_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...], tuple[tuple[str, bool], ...]], ...] = (
        (("휴지", "화장지", "두루마리", "각티슈", "미용티슈"), ("화장지", "휴지"),
         (("휴지 떨어진 거 꼭 샤워 끝나고 알게 됨", True),)),
        (("양말",), (),
         (("양말은 왜 항상 한 짝씩 사라짐", True),)),
        (("충전기", "충전 케이블", "고속충전", "보조배터리", "멀티탭"), (),
         (("충전기는 왜 항상 하나 모자람", True),)),
        (_APPLIANCE_WORDS,
         ("가전", "세탁기", "청소기", "냉장고", "주방가전", "계절가전"),
         (("바꿀 때 된 거 알면서 계속 미루게 됨", True), ("고장 나기 전엔 이상하게 안 바꾸게 됨", True),
          ("가전은 한 번 사면 몇 년 씀", False))),
        (_DIGITAL_WORDS,
         ("디지털", "컴퓨터", "노트북", "휴대폰", "모니터", "음향", "게임", "카메라"),
         (("바꿀 때 된 거 알면서 계속 미루게 됨", True), ("쓰던 게 느려지면 그때부터 계속 눈에 밟힘", True))),
        (("샴푸", "린스", "트리트먼트", "바디워시", "바디로션", "핸드크림", "선크림", "클렌징", "세럼", "마스크팩", "립밤", "향수"),
         ("뷰티", "화장품", "스킨", "헤어", "바디", "향수", "클렌징"),
         (("화장대 정리하다 보면 꼭 이게 없음", True), ("다 쓴 공병 쌓여 있는 사람 손", False))),
        (("생수", "탄산수", "콜라", "사이다", "주스", "이온음료", "캔커피", "아메리카노", "콜드브루", "두유", "커피믹스", "믹스커피"),
         ("생수", "음료", "탄산", "주스"),
         (("마실 거는 꼭 귀찮은 날 떨어짐", True), ("음료는 박스로 쟁여두면 편함", False))),
        (("라면", "햇반", "즉석밥", "만두", "교자", "닭가슴살", "김치", "과자", "초콜릿", "견과", "우유", "요거트", "시리얼", "참치",
          "스팸", "소시지", "계란", "특란", "삼겹살", "한우"),
         ("식품", "과자", "라면", "간편", "축산", "수산", "과일", "채소", "쌀", "냉동식품", "커피"),
         (("야식 생각날 때 꼭 냉장고가 비어 있음", True), ("장보러 가기 귀찮은 날 이거면 됨", False),
          ("배달비 아까워서 쟁여두는 거 있음?", False))),
        (("세제", "섬유유연제", "물티슈", "키친타올", "락스", "칫솔", "치약", "쓰레기봉투", "지퍼백"),
         ("생활", "세제", "화장지", "휴지", "물티슈", "욕실", "세탁", "청소"),
         (("생필품은 꼭 다 쓰고 나서야 생각남", True), ("어차피 계속 쓰는 거라 싸게 나오면 눈에 들어옴", False))),
        (("프라이팬", "냄비", "도마", "칼세트", "밀폐용기", "수저", "식기", "텀블러", "보온병", "탈수기", "그릇", "호일"),
         ("주방", "식기", "조리", "냄비", "프라이팬"),
         (("쓰려고 하면 꼭 설거지통에 들어가 있음", True), ("설거지 거리 하나 줄이면 그게 행복임", False),
          ("주방템은 한 번 사면 몇 년 감", False))),
        (("티셔츠", "속옷", "운동화", "슬리퍼", "레깅스", "패딩", "백팩"),
         ("패션", "의류", "신발", "가방", "속옷", "잡화"),
         (("입으려고 보면 꼭 하나씩 해져 있음", True), ("기본템은 매년 다시 사게 됨", False))),
    )
    # 일반 문구는 10개 이상 (최근 10건 안에 같은 첫 줄을 다시 안 쓰려면). 질문형은 몇 개만 (매번 질문이면 그것도 광고 문법)
    THREAD_HOOKS: tuple[tuple[str, bool], ...] = (
        ("오늘 핫딜 중에 이거 하나만 건지면 됨", False), ("이거 집에 하나씩 있는 거 맞지?", False),
        ("꼭 필요할 때 사면 비싸더라", False), ("가격 보고 두 번 봤음", False),
        ("미뤄둔 거 하나쯤 있지?", False), ("이런 건 알아두면 언젠가 씀", False),
        ("쟁여둘 사람은 지금이 타이밍임", False), ("그냥 넘기기엔 좀 아까운 가격", False),
        ("장바구니에 넣어만 두고 안 산 거 있지?", False), ("이 정도면 한 번 볼 만함", False),
        ("생각난 김에 하나 들여놓기 좋은 날", False),
    )
    THREAD_CLOSES = (
        "다들 이런 거 어디서 사?", "더 싼 데 알면 알려줘", "이거 써본 사람 후기 좀", "링크 가격 다르면 이미 끝난 거임",
        "가격은 수시로 바뀌니까 링크에서 한 번 더 확인", "다들 이거 몇 개씩 쟁여둠?",
    )
    # 가격 없는 쿠폰·이벤트: 가격·싸다·쟁여 같은 말 없이. '아는 사람만 챙김' 같은 '~사람만' 문은 쓰지 않는다 (TH-02)
    THREAD_HOOKS_NOPRICE: tuple[tuple[str, bool], ...] = (
        ("이런 건 꼭 끝나고 나서 알게 됨", False), ("모르고 지나치기 아까운 거", False), ("조건만 맞으면 챙길 만함", False),
    )
    THREAD_CLOSES_NOPRICE = ("조건은 링크에서 한 번 더 확인", "비슷한 거 알면 알려줘", "기간 있는 거라 미리 봐두면 편함")

    def _group(self, p: Product) -> tuple[Any, ...] | None:
        """상품명의 확실한 낱말 → 쿠팡 분류(구체적인 칸부터) 순으로 맞는 상품 그룹 (THREAD_HOOK_GROUPS 의 한 칸). 모르면 None.
        소모품·부속('식기세척기 세제', '드럼세탁기용', '정수기 필터', '아이폰 케이스')은 이름 속 가전·전자기기 낱말을 안 본다."""
        name = clean_name(p.name)
        for compound in _NOT_THE_WORD:
            name = name.replace(compound, " ")
        if _ACCESSORY.search(name):
            name = _DURABLE.sub(" ", name)
        for group in self.THREAD_HOOK_GROUPS:
            if any(w in name for w in group[0]):
                return group
        for seg in reversed([s.strip() for s in (p.category or "").split(">") if s.strip()]):
            for group in self.THREAD_HOOK_GROUPS:
                if any(w in seg for w in group[1]):
                    return group
        return None

    def _hook_pool(self, p: Product) -> tuple[tuple[str, bool], ...]:
        """그 상품 그룹의 스레드 첫 줄들. 모르면 일반 문구."""
        group = self._group(p)
        return group[2] if group else self.THREAD_HOOKS

    # 채널 글 '방장 한줄평' (AI 한마디가 없을 때): 그룹 첫 낱말 → 해요체 한 줄. 가격·경험·약속은 지어내지 않는다
    TG_ONE_LINERS = {
        "휴지": "휴지는 떨어지기 전에 미리 쟁여두면 편해요",
        "양말": "양말은 늘 모자라서 넉넉히 있으면 좋아요",
        "충전기": "충전기는 집·회사·가방에 하나씩 두면 편해요",
        "세탁기": "오래 쓰는 가전이라 가격 내려왔을 때 보기 좋아요",
        "모니터": "오래 쓰는 기기라 가격 내려왔을 때 보기 좋아요",
        "샴푸": "매일 쓰는 거라 떨어지기 전에 챙겨두면 좋아요",
        "생수": "음료는 박스로 쟁여두면 든든해요",
        "라면": "출출할 때 꺼내 먹게 쟁여두기 좋아요",
        "세제": "생필품은 다 쓰기 전에 미리 사두면 편해요",
        "프라이팬": "주방템은 한 번 사면 오래 써요",
        "티셔츠": "기본템이라 하나 더 있어도 잘 입게 돼요",
    }
    TG_ONE_LINER_DEFAULT = "필요했던 거라면 링크에서 가격 한번 확인해 보세요"
    TG_ONE_LINER_NOPRICE = "조건이 맞으면 기간 안에 챙겨 보세요"

    def one_liner(self, p: Product) -> str:
        """방장 한줄평: AI 한마디(commentary)가 있으면 그것, 없으면 상품 그룹별 고정 한 줄."""
        comment = ((p.extra or {}).get("comment") or "").strip()
        if comment:
            return comment
        if not p.has_price:
            return self.TG_ONE_LINER_NOPRICE
        group = self._group(p)
        return self.TG_ONE_LINERS.get(group[0][0], self.TG_ONE_LINER_DEFAULT) if group else self.TG_ONE_LINER_DEFAULT

    def _is_durable(self, pool: tuple[tuple[str, bool], ...]) -> bool:
        """가전·전자기기 첫 줄 그룹인지. 몇 년 쓰는 물건이라 '몇 개씩 쟁여둠?' 같은 말이 안 맞는다."""
        return any(hooks is pool for words, _, hooks in self.THREAD_HOOK_GROUPS if words in (_APPLIANCE_WORDS, _DIGITAL_WORDS))

    @staticmethod
    def _rotate(items: Sequence[Any], h: int, recent: list[str]) -> list[Any]:
        """h 로 정한 자리부터 한 바퀴 돌며 최근에 안 쓴 것만."""
        order = [items[(h + i) % len(items)] for i in range(len(items))] if items else []
        return [x for x in order if (x[0] if isinstance(x, tuple) else x) not in recent]

    def thread_lines(self, p: Product, *, recent: Sequence[str] = ()) -> dict[str, Any]:
        """스레드 첫 줄 · '근데' 반전 여부 · 마무리. 상품마다 정해진 순서로 고르되 recent(최근 글에 쓴 문구)는 피한다."""
        recent = list(recent)
        h = sum(map(ord, p.product_id))
        ai = (p.extra or {}).get("thread_hook")
        if p.has_price:
            pool, generic, closes = self._hook_pool(p), self.THREAD_HOOKS, self.THREAD_CLOSES
            if self._is_durable(pool):  # 냉장고·노트북을 '쟁여'두지는 않음
                generic = tuple(x for x in generic if "쟁여" not in x[0])
                closes = tuple(c for c in closes if "쟁여" not in c)
        else:
            pool = generic = self.THREAD_HOOKS_NOPRICE
            closes = self.THREAD_CLOSES_NOPRICE
            if ai and _PRICE_TALK.search(ai):
                ai = None
        if ai:
            hook, turn = ai, True  # AI 첫 줄은 상황·공감 문장으로 쓰게 시킨다 (commentary)
        else:
            fresh = self._rotate(pool, h, recent) or self._rotate(generic, h, recent)
            if fresh:
                hook, turn = fresh[0]
            else:  # 다 최근에 썼으면 가장 오래전에 쓴 것
                hook, turn = min((*pool, *generic), key=lambda x: recent.index(x[0]))
        close = None
        if h % 2:  # 마무리는 두 번에 한 번꼴 (매번 질문으로 끝나면 그것도 광고 문법)
            options = [c for c in closes if not any(s in hook and s in c for s in _SHARED_STEMS)]
            if hook.rstrip().endswith("?"):  # 질문은 글 하나에 하나까지 (스타일 가이드 4-2: 질문 1줄은 선택)
                options = [c for c in options if not c.rstrip().endswith("?")]
            fresh_close = self._rotate(options, h // 7, recent)
            if fresh_close:
                close = fresh_close[0]
            elif options:
                close = min(options, key=lambda c: recent.index(c) if c in recent else -1)
        return {"thread_hook": hook, "thread_turn": turn, "thread_close": close}

    def evidence(
        self, deal: Deal, *, days: int, ref_price: int | None, pct: int | None, labels: list[str], tier: str,
        is_market: bool = False, recent: Sequence[str] = (),
    ) -> dict[str, Any]:
        """가격 근거를 사람 말로: 첫 줄용 짧은 근거, 문장형 근거, 스레드용 근거. 숫자는 데이터에서만.
        우선순위: 평소 가격(쿠팡 시중가·최근 평균) 대비 > 기록 최저가 > 단가. 표시(정가) 할인율은 근거로 안 씀.
        스레드 근거(evidence_casual)는 '평소보다 N% 쌈' 대신 비교한 금액을 그대로 보여 준다 (주인 지시 TH-02, 스타일 가이드 6장
        '같은 상품 쿠팡은 21,900원' / '평소 15,200원대'). 바로 윗줄이 '{이름} {가격}' 이라 금액만 놓아도 차이가 보인다."""
        p = deal.product
        unit = unit_price(clean_name(p.name), p.price)
        each = f"{unit.split(' (')[0]} 꼴" if unit else None
        low = next((x for x in labels if "최저가" in x), None)
        if ref_price and pct:
            where = "쿠팡" if is_market else "평소"
            short = f"{where}보다 {pct}%↓"
            # 평균은 '원대'(대략 구간)로 말하니 끝자리를 내린다 ('41,233원대' → '41,000원대'). 쿠팡 최저가는 정확한 금액 그대로
            if is_market:
                casual, sentence = f"쿠팡 최저가는 {ref_price:,}원", f"쿠팡 최저가 {ref_price:,}원보다 {pct}% 싸요."
            else:
                band = price_band(ref_price, p.price)
                casual, sentence = f"평소엔 {band:,}원대", f"평소 {band:,}원대인데 오늘 {p.price:,}원이에요."
        elif low:
            short, casual = f"{days}일 중 제일 쌈", f"최근 {days}일 중 최저가"
            sentence = f"최근 {days}일 중 제일 싸요."
        elif each:
            short, casual, sentence = each, each, f"{each}이에요."
        else:
            short = casual = sentence = None
        if p.has_price:
            badge = {"top": "🔥 초특가", "must": "👍 강추"}.get(tier, "☑️")
            link_label = "구매하러 가기"
        else:  # 쿠폰·이벤트: 첫 줄(알림 미리보기)이 '☑️' 하나만 남지 않게 종류를 말해 줌
            coupon = p.deal_kind == "coupon" or "쿠폰" in f"{p.name} {p.headline or ''}"
            word = "쿠폰" if coupon else "이벤트"
            badge = ("🎟" if coupon else "🎁") + ("" if word in p.name else f" {word} ·")
            link_label = "쿠폰 받으러 가기" if coupon else "이벤트 보러 가기"
        return {
            "badge": badge,
            "link_label": link_label,
            "evidence_short": short,
            "evidence": sentence,
            "evidence_casual": casual,
            "unit_each": each if each and each != short else None,
            **self.thread_lines(p, recent=recent),
            "thread_take": (p.extra or {}).get("thread_take"),
            "short_name": _drop_pct_claims((p.extra or {}).get("short_name") or short_name(p.name)),
        }

    def render(self, name: str, *, autoescape: bool = True, **ctx: Any) -> str:
        env = self.env if autoescape else self.env_plain
        template = env.get_template(name)
        ctx.setdefault("now", datetime.now(self.tz))
        ctx.setdefault("channels", self.channels)
        return template.render(**ctx).strip()

    def render_deal(
        self,
        deal: Deal,
        link: str,
        *,
        shop: Shop | None = None,
        template: str = "deal_post.j2",
        autoescape: bool = True,
        recent: Sequence[str] = (),
        single: bool = False,
    ) -> str:
        """recent: 최근 스레드 글에 쓴 문구(피해서 고름). single: 예전 템플릿 호환용 (스레드 링크는 이제 늘 본문에)."""
        p = deal.product
        shop_ctx = {
            "key": shop.key if shop else p.shop,
            "name": shop.name if shop else p.shop,
            "disclosure": shop.disclosure if shop else None,
            "link_mode": shop.link_mode if shop else "raw",
        }
        tier, tier_pct = self.deal_tier(deal)
        # 평소 가격 근거 (숫자로 확인된 것만): 쿠팡 시중가 대조가 있으면 그것, 없으면 믿을 만한 최근 평균
        v = deal.verdict
        ref_price, ref_pct, is_market = self.reference(deal)
        ref_label = ("쿠팡 최저가" if is_market else "평소 가격") if ref_price else ""
        avg_ok = self.avg_trusted(deal)
        return self.render(
            template,
            autoescape=autoescape,
            product=p,
            shop=shop_ctx,
            verdict=deal.verdict,
            tier=tier,
            tier_pct=tier_pct,
            link=link,
            discount_rate=deal.verdict.discount_rate if deal.verdict.discount_rate is not None else p.effective_discount_rate(),
            # 최근 최저가보다 꽤 비싸면 '30일 평균 대비 N% 저렴'을 쓰지 않음 (avg_trusted)
            avg_price=v.avg_price if avg_ok else None,
            below_avg_pct=v.below_avg_pct if avg_ok else None,
            market_price=v.market_price,
            market_source=v.market_source,
            below_market_pct=v.below_market_pct,
            detected_at=deal.detected_at,
            pname=clean_name(p.name),
            ref_price=ref_price,
            ref_label=ref_label,
            ref_pct=round(ref_pct) if ref_price and ref_pct else None,
            single=single,
            facts=self.deal_facts(deal, recent=recent),
        )
