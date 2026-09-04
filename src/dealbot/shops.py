"""쇼핑몰 레지스트리.

각 쇼핑몰에 대해
  - 커뮤니티 글 제목의 [쇼핑몰] 태그를 인식할 별칭(aliases)
  - URL 을 인식할 도메인(domains)
  - 링크 처리 방식(link_mode): api(자동 변환) / manual(내가 앱에서 링크를 만들어 넘겨줌) / raw(원본 링크 그대로) / skip
  - 게시물 하단 고지 문구(disclosure)
를 정의한다. config.yaml 의 shops: 로 덮어쓰거나 추가할 수 있다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from dealbot.utils.urls import extract_product_id as extract_coupang_id

LINK_MODES = ("api", "manual", "raw", "skip")


@dataclass(slots=True)
class Shop:
    key: str
    name: str
    aliases: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    link_mode: str = "raw"  # api | manual | raw | skip
    provider: str | None = None  # api 모드일 때 사용할 링크 변환기: coupang | linkprice
    disclosure: str | None = None
    enabled: bool = True
    manual_hint: str | None = None  # manual 모드일 때 관리자에게 보여줄 안내
    manual_fallback: bool = False  # api 모드에서 자동 변환이 실패하면 수동(관리자 링크)으로 넘길지
    requires_provider: bool = False  # provider 가 설정돼 있을 때만 켜짐 (API 로 링크가 자동 발급되는 경우에만 수집)
    disabled_reason: str | None = None  # 자동으로 꺼졌을 때의 이유 (상태 표시용)

    def matches_url(self, url: str) -> bool:
        try:
            host = urlparse(url).netloc.lower()
        except ValueError:
            return False
        host = host.split(":")[0]
        return any(host == d or host.endswith("." + d) for d in self.domains)


def _d(text: str) -> str:
    return f"이 포스팅은 {text} 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."


DEFAULT_SHOPS: list[Shop] = [
    Shop(
        key="coupang",
        name="쿠팡",
        aliases=["쿠팡", "coupang", "로켓배송", "로켓프레시", "쿠팡이츠"],
        domains=["coupang.com"],
        link_mode="api",
        provider="coupang",
        disclosure=_d("쿠팡 파트너스"),
        manual_hint="파트너스 사이트(partners.coupang.com) → 링크 생성 → 상품 URL 붙여넣기 → 단축 URL 복사",
        manual_fallback=True,  # API 키가 없거나(최종 승인 전) 변환 실패 시 관리자에게 링크 요청
    ),
    Shop(
        key="toss",
        name="토스쇼핑",
        aliases=["토스", "토스쇼핑", "toss"],
        domains=["toss.im"],
        link_mode="manual",
        disclosure=_d("토스쇼핑 쉐어링크"),
        manual_hint="토스 앱 → 상품 페이지 → 공유 → '쉐어링크 공유하기' 로 만든 링크",
    ),
    Shop(
        key="naver",
        name="네이버",
        aliases=["네이버", "네이버쇼핑", "네이버플러스", "스마트스토어", "브랜드스토어", "naver", "n플러스"],
        domains=["naver.com", "naver.me"],
        link_mode="manual",
        provider="naver_connect",  # browser.enabled + link_mode: api 로 바꾸면 브라우저 자동화 시도
        disclosure=_d("네이버 쇼핑커넥트"),
        manual_hint="쇼핑커넥트(connect.naver.com) 에서 상품 URL 로 링크 생성",
        manual_fallback=True,
    ),
    # ---- 앱에서만 링크를 만들 수 있고(API 없음) 특가 빈도가 낮은 몰: 기본 꺼짐. config.yaml 에서 enabled: true 로 켤 수 있음
    Shop(
        key="oliveyoung",
        name="올리브영",
        aliases=["올리브영", "올영"],
        domains=["oliveyoung.co.kr"],
        link_mode="manual",
        disclosure=_d("올리브영 쇼핑 큐레이터"),
        manual_hint="올리브영 앱 → 상품 → 공유 → 큐레이터 링크",
        enabled=False,
    ),
    Shop(
        key="kurly",
        name="컬리",
        aliases=["컬리", "마켓컬리", "뷰티컬리", "kurly"],
        domains=["kurly.com"],
        link_mode="manual",
        disclosure=_d("컬리 큐레이터"),
        manual_hint="컬리 앱 → 마이컬리 → 컬리 큐레이터 → 상품 링크 생성",
        enabled=False,
    ),
    Shop(
        key="musinsa",
        name="무신사",
        aliases=["무신사", "musinsa", "29cm"],
        domains=["musinsa.com", "29cm.co.kr"],
        link_mode="manual",
        disclosure=_d("무신사 큐레이터"),
        manual_hint="무신사 앱 → 상품 → 공유 → 큐레이터 링크",
        enabled=False,
    ),
    # ---- 링크프라이스 API 로 링크가 자동 발급되는 몰: LINKPRICE_AFFILIATE_ID 가 있을 때만 켜짐 (requires_provider)
    Shop(key="11st", name="11번가", aliases=["11번가", "십일번가", "11st"], domains=["11st.co.kr"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    Shop(key="gmarket", name="G마켓", aliases=["지마켓", "g마켓", "gmarket"], domains=["gmarket.co.kr"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    Shop(key="auction", name="옥션", aliases=["옥션", "auction"], domains=["auction.co.kr"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    Shop(key="ssg", name="SSG", aliases=["ssg", "쓱", "이마트몰", "신세계몰"], domains=["ssg.com"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    Shop(key="lotteon", name="롯데온", aliases=["롯데온", "롯데ON", "lotteon"], domains=["lotteon.com"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    Shop(key="aliexpress", name="알리익스프레스", aliases=["알리", "알리익스프레스", "aliexpress", "ali"], domains=["aliexpress.com", "aliexpress.us"], link_mode="api", provider="linkprice", disclosure=_d("알리익스프레스 제휴마케팅"), requires_provider=True),
    Shop(key="ohouse", name="오늘의집", aliases=["오늘의집", "오하우스"], domains=["ohou.se"], link_mode="api", provider="linkprice", disclosure=_d("링크프라이스 제휴마케팅"), requires_provider=True),
    # ---- 수익이 없는 몰: 기본 꺼짐 (정보 공유용으로만 쓰려면 enabled: true)
    Shop(key="temu", name="테무", aliases=["테무", "temu"], domains=["temu.com"], link_mode="raw", disclosure=None, enabled=False),
    Shop(key="daiso", name="다이소몰", aliases=["다이소", "다이소몰"], domains=["daisomall.co.kr"], link_mode="raw", enabled=False),
]

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


class ShopRegistry:
    def __init__(self, shops: list[Shop] | None = None) -> None:
        # 항상 복사본을 보관한다: apply_providers 등이 DEFAULT_SHOPS(전역)를 바꾸면 안 되므로
        self._shops: dict[str, Shop] = {}
        for s in shops if shops is not None else DEFAULT_SHOPS:
            self._shops[s.key] = dataclasses.replace(s, aliases=list(s.aliases), domains=list(s.domains))

    # ------------------------------------------------------------ lookup
    def get(self, key: str) -> Shop | None:
        return self._shops.get(key)

    def all(self) -> list[Shop]:
        return list(self._shops.values())

    def enabled(self) -> list[Shop]:
        return [s for s in self._shops.values() if s.enabled]

    def apply_providers(self, available: Iterable[str]) -> list[str]:
        """requires_provider 인 몰은 링크 변환기(provider)가 실제로 설정돼 있을 때만 켠다. 꺼진 key 목록을 돌려준다."""
        avail = set(available)
        disabled: list[str] = []
        for s in self._shops.values():
            if not s.requires_provider or not s.enabled:
                continue
            if s.provider not in avail:
                s.enabled = False
                s.disabled_reason = f"{s.provider or 'provider'} 미설정 — API 링크 발급이 가능해지면 자동으로 켜짐"
                disabled.append(s.key)
        return disabled

    def keys(self) -> list[str]:
        return list(self._shops)

    def by_alias(self, text: str) -> Shop | None:
        """'[쿠팡]', '토스쇼핑', 'G마켓' 같은 텍스트에서 쇼핑몰을 찾는다 (긴 별칭 우선)."""
        t = text.strip().strip("[]").lower()
        if not t:
            return None
        best: tuple[int, Shop] | None = None
        for s in self._shops.values():
            for a in s.aliases:
                a_l = a.lower()
                if t == a_l or a_l in t:
                    if best is None or len(a_l) > best[0]:
                        best = (len(a_l), s)
        return best[1] if best else None

    def by_url(self, url: str) -> Shop | None:
        for s in self._shops.values():
            if s.matches_url(url):
                return s
        return None

    # ------------------------------------------------------------ keys
    @staticmethod
    def product_key(shop_key: str, url: str) -> str:
        """가격 이력/중복 판단에 쓰는 쇼핑몰 범위의 안정 키."""
        p = urlparse(url)
        path = p.path or ""
        if shop_key == "coupang":
            pid = extract_coupang_id(url)
            if pid:
                return f"coupang:{pid}"
        elif shop_key == "toss":
            m = re.search(r"/_m/([A-Za-z0-9_-]+)", path)
            if m:
                return f"toss:{m.group(1)}"
        elif shop_key == "naver":
            m = re.search(r"/products/(\d+)", path)
            if m:
                return f"naver:{m.group(1)}"
            nid = parse_qs(p.query).get("nvMid", [None])[0]
            if nid:
                return f"naver:{nid}"
        elif shop_key == "11st":
            m = re.search(r"/products/(?:[a-z]+/)?(\d+)", path)
            if m:
                return f"11st:{m.group(1)}"
        elif shop_key in ("gmarket", "auction"):
            for k in ("goodscode", "itemno"):
                v = parse_qs(p.query).get(k, [None])[0]
                if v:
                    return f"{shop_key}:{v}"
        elif shop_key == "aliexpress":
            m = re.search(r"/item/(\d+)", path)
            if m:
                return f"aliexpress:{m.group(1)}"
        elif shop_key == "ssg":
            v = parse_qs(p.query).get("itemId", [None])[0]
            if v:
                return f"ssg:{v}"
        digest = hashlib.sha1(url.split("#")[0].encode("utf-8")).hexdigest()[:12]
        return f"{shop_key}:url:{digest}"


def find_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,)") for m in _URL_RE.finditer(text or "")]
