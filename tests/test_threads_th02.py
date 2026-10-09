"""스레드 양식 새로 짜기 (주인 지시 TH-02, 2026-10-07) + 템플릿 리뷰 후속 (C3 #4·#9·#12·#15) + 트레비 단가 회귀.

주인이 뺀 말: '…원임', '평소보다 N% 쌈', '필요했던 사람만 보면 됨', '찾던 사람 있을 것 같아서 남겨둠',
고지 뒤 '링크는 댓글에 👇'. 렌더러가 만들지 않고(코드), AI 문구 검사·발행 직전 검사가 막고(검사), 여기서 확인한다(테스트).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import httpx
import pytest
from PIL import Image

from dealbot.cli import sample_deal
from dealbot.commentary import SYSTEM, THREAD_BANNED, check_hook, check_take
from dealbot.media.card import DealCard
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.templates import TemplateRenderer, short_name
from dealbot.publisher.threads import (
    KV_TOKEN,
    KV_USER_ID,
    TEXT_LIMIT,
    ThreadsClient,
    ThreadsPublisher,
    thread_problems,
)
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database
from dealbot.utils.text import clean_name, unit_price

REG = ShopRegistry()
LINK = "https://link.coupang.com/a/x"
# 주인이 스레드에서 뺀 말 (정확한 문구 + 같은 꼴)
BANNED_EXACT = ("필요했던 사람만 보면 됨", "찾던 사람 있을 것 같아서 남겨둠", "링크는 댓글에")
BANNED_FORMS = (re.compile(r"\d\s*원임(?![가-힣])"), re.compile(r"평소보다\s*\d+\s*%\s*쌈"), re.compile(r"\d+\s*%\s*쌈(?![가-힣])"))


def _assert_clean(text: str) -> None:
    for s in BANNED_EXACT:
        assert s not in text, (s, text)
    for rx in BANNED_FORMS:
        assert not rx.search(text), (rx.pattern, text)
    assert not text.rstrip().endswith("👇"), text


def _client(handler) -> ThreadsClient:  # type: ignore[no-untyped-def]
    return ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="A", app_secret="S", retry_backoff=0.01)


def _ok(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/threads_publish"):
        return httpx.Response(200, json={"id": "100"})
    if req.url.path.endswith("/threads"):
        return httpx.Response(200, json={"id": "C1"})
    return httpx.Response(200, json={"status": "FINISHED"})


@pytest.fixture
def r(repo_root: Path) -> TemplateRenderer:
    return TemplateRenderer(repo_root / "templates", channels={"telegram_url": "https://t.me/x"})


VERDICTS = [
    DealVerdict(is_deal=True),  # 근거 없음
    DealVerdict(is_deal=True, market_price=35100, below_market_pct=29.0),  # 쿠팡 시중가
    DealVerdict(is_deal=True, avg_price=42000, below_avg_pct=29.0),  # 믿을 만한 평균 (최저가 정보 없음)
    DealVerdict(is_deal=True, low_price=30000, avg_price=33000, history_days=20, sample_count=9),  # 기록 최저
    DealVerdict(is_deal=True, market_price=120000, below_market_pct=75.0),  # top
]
NAMES = ["[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L", "크리넥스 3겹 30m 30롤", "할리스 바닐라 딜라이트 로우슈거, 24개",
         "LG 트롬 드럼세탁기 21kg", "피니시 식기세척기 세제 올인원 100개", "제주삼다수 2L / 12병", "코지 바디필로우"]


# ---------------------------------------------------------------- TH-02: 렌더 결과에 뺀 말이 없음
@pytest.mark.parametrize("shop_key", ["coupang", "toss", "naver"])
async def test_threads_posts_never_use_the_banned_lines(r: TemplateRenderer, db: Database, shop_key: str) -> None:
    pub = ThreadsPublisher(_client(_ok), db, r)
    shop = REG.get(shop_key)
    assert shop is not None and shop.disclosure
    seen_hooks: set[str] = set()
    for i in range(60):
        v = VERDICTS[i % len(VERDICTS)]
        name = NAMES[i % len(NAMES)]
        p = Product(source="s", product_id=f"{shop_key}:{i}", shop=shop_key, name=name, price=29900, url="u")
        d = Deal(p, v, affiliate_url=LINK)
        post = pub.render(d)
        seen_hooks.add(post.split("\n")[0])
        _assert_clean(post)
        assert len(post) <= TEXT_LIMIT
        # 공식 고지 문구는 그대로 본문 안에, 링크는 그 바로 아래 (댓글로 넘기지 않음)
        assert post.endswith(f"{shop.disclosure}\n👉 {LINK}"), post
        assert pub.render_reply(d) is None and not pub.problems(d, post)
        # 가격 줄은 '…원' 으로 끝남 ('임' 없음)
        price_line = next(x for x in post.split("\n") if "29,900원" in x)
        assert price_line.endswith("29,900원")
    assert len(seen_hooks) >= 8  # 첫 줄이 한두 문장만 돌지 않음
    # 쿠폰·이벤트(가격 없음)도 같은 규칙
    coupon = Deal(Product(source="s", product_id="coupang:c", shop="coupang", name="쿠팡 와우 5천원 할인쿠폰", price=0, url="u",
                          deal_kind="coupon"), DealVerdict(is_deal=True), affiliate_url=LINK)
    _assert_clean(pub.render(coupon))


def test_threads_sample_matches_the_new_layout(r: TemplateRenderer, db: Database) -> None:
    """주인이 받았던 샘플(스탠리 텀블러)이 새 양식으로: 상황 첫 줄 → '근데 이름 가격' → 비교 금액 → 고지 → 링크."""
    pub = ThreadsPublisher(_client(_ok), db, r)
    d = sample_deal()
    d.product.extra.update(thread_hook="텀블러는 꼭 출근길에 두고 나옴", short_name="스탠리 퀜처 1.18L",
                           thread_take="용량 커서 사무실 책상용으로 괜찮음")
    post = pub.render(d)
    lines = post.split("\n")
    assert lines[0] == "텀블러는 꼭 출근길에 두고 나옴" and lines[1] == ""
    assert lines[2] == "근데 스탠리 퀜처 1.18L 29,900원" and lines[3] == "평소엔 42,000원대"
    assert "용량 커서 사무실 책상용으로 괜찮음" in post
    assert post.endswith("이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.\n👉 https://link.coupang.com/a/sample")
    _assert_clean(post)


def test_market_evidence_names_the_amount(r: TemplateRenderer) -> None:
    d = Deal(Product(source="s", product_id="toss:1", shop="toss", name="크리넥스 3겹 30m 30롤", price=14900, url="u"),
             DealVerdict(is_deal=True, market_price=21900, below_market_pct=32.0), affiliate_url=LINK)
    th = r.render_deal(d, LINK, shop=REG.get("toss"), template="deal_threads.j2", autoescape=False)
    assert "크리넥스 3겹 30m 30롤 14,900원\n쿠팡 최저가는 21,900원" in th
    # 텔레그램 첫 줄은 그대로 (채널 양식은 이번에 안 바꿈)
    assert r.render_deal(d, LINK, shop=REG.get("toss")).startswith("☑️ <b>14,900원</b> · 쿠팡보다 32%↓")


def test_every_pool_line_passes_the_same_checks() -> None:
    """고정 문구(첫 줄·마무리)도 AI 문구와 같은 검사를 통과해야 한다 — 뺀 말·훈수·존댓말·숫자 없음."""
    hooks = [h for g in TemplateRenderer.THREAD_HOOK_GROUPS for h, _ in g[2]]
    hooks += [h for h, _ in TemplateRenderer.THREAD_HOOKS] + [h for h, _ in TemplateRenderer.THREAD_HOOKS_NOPRICE]
    for h in hooks:
        assert check_hook(h) == h, h
    for c in (*TemplateRenderer.THREAD_CLOSES, *TemplateRenderer.THREAD_CLOSES_NOPRICE):
        assert not THREAD_BANNED.search(c), c
        assert not re.search(r"니다|세요|(?<![필중주수소])요$", c), c
    # 훈수로 읽히던 문구는 빠짐 (A-23)
    everything = " / ".join(hooks + list(TemplateRenderer.THREAD_CLOSES))
    for s in ("할인할 때 사는 거임", "이기는 거임", "게 답임", "게 맞음", "싸도 손해임", "큰 가전은"):
        assert s not in everything, s
    assert len(TemplateRenderer.THREAD_HOOKS) >= 10  # 최근 10건 안에 같은 첫 줄을 안 쓰려면


@pytest.mark.parametrize(
    "text",
    ["필요했던 사람만 보면 됨", "찾던 사람 있을 것 같아서 남겨둠", "29,900원임", "평소보다 29% 쌈", "링크는 댓글에 달아둠",
     "살 거면 할인할 때 사는 게 맞음", "안 쓸 거면 싸도 손해임",
     # 같은 꼴 (리뷰 2차): 정확한 문구만 막으면 이런 말이 AI 문구 검사·발행 직전 검사를 그대로 통과했음
     "쿠팡보다 29% 저렴함", "평소보다 29% 싸짐", "29%나 쌈", "29% 더 쌈", "개당 1,246원 꼴임", "찾는 사람 있을 것 같아서 올려둠",
     "링크 댓글에 남김", "필요했던 사람은 보면 됨", "이런 건 아는 사람만 챙김"],
)
def test_ai_lines_with_banned_words_are_dropped(text: str) -> None:
    facts = "평소 42,000원대인데 오늘 29,900원이에요. / 29% / 쿠팡 최저가 35,100원보다 29% 싸요. / 개당 1,246원 꼴"
    assert THREAD_BANNED.search(text), text  # 숫자 검사와 상관없이 뺀 말 검사가 직접 잡는다 (발행 직전 검사도 같은 정규식)
    assert check_take(text, facts, "스탠리 텀블러 29,900원") is None
    assert check_hook(text) is None
    assert thread_problems(f"첫 줄\n\n{text}")


def test_ai_prompt_lists_the_banned_lines() -> None:
    for s in ("29,900원임", "N% 쌈", "필요했던 사람만", "찾던 사람 있을 것 같아서", "링크는 댓글에"):
        assert s in SYSTEM, s


async def test_publisher_blocks_a_post_that_slips_through(r: TemplateRenderer, db: Database) -> None:
    """검사를 거치지 않고 들어온 문구(저장된 옛 답 등)가 있으면 올리지 않고 이유를 돌려준다."""
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        return _ok(req)

    pub = ThreadsPublisher(_client(handler), db, r)
    db.kv_set(KV_TOKEN, "T")
    db.kv_set(KV_USER_ID, "999")
    d = sample_deal()
    d.product.extra["thread_take"] = "찾던 사람 있을 것 같아서 남겨둠"
    res = await pub.publish(d)
    assert not res.ok and "규칙 위반" in (res.error or "") and "찾던 사람" in (res.error or "") and calls == []
    assert thread_problems("본문\n고지", disclosure="고지 문구", link=LINK) == ["제휴 고지 문구가 빠짐", "링크가 빠짐"]
    ok = sample_deal()
    assert (await pub.publish(ok)).ok and calls


# ---------------------------------------------------------------- C3 #12: 소모품·부속은 가전 첫 줄이 아님
def _pool(r: TemplateRenderer, name: str, category: str = "") -> set[str]:
    return {h for h, _ in r._hook_pool(Product(source="s", product_id="coupang:1", shop="coupang", name=name, price=1000, url="u",
                                                category=category))}


def _group_of(word: str) -> set[str]:
    for words, _, hooks in TemplateRenderer.THREAD_HOOK_GROUPS:
        if word in words:
            return {h for h, _ in hooks}
    raise AssertionError(word)


APPLIANCE = _group_of("세탁기")
DIGITAL = _group_of("모니터")
LIVING = _group_of("세제")
KITCHEN = _group_of("프라이팬")
BEAUTY = _group_of("바디워시")
DRINK = _group_of("생수")
GENERIC = {h for h, _ in TemplateRenderer.THREAD_HOOKS}


@pytest.mark.parametrize(
    ("name", "category", "want"),
    [
        ("피니시 식기세척기 세제 올인원 100개", "", LIVING),
        ("퍼실 드럼세탁기용 액체세제 4L 2개", "", LIVING),
        ("세탁조 클리너 세탁기 청소", "", GENERIC),
        ("세탁기 통세척제", "", GENERIC),
        ("에어프라이어 종이호일 100매", "", KITCHEN),
        ("에어프라이어 전용 종이호일", "주방용품", KITCHEN),
        ("청소기 먼지봉투", "", GENERIC),
        ("브리타 정수기 필터 6개", "", GENERIC),
        ("가습기 살균제", "", GENERIC),
        ("냉장고 탈취제", "", GENERIC),
        ("아이폰 15 실리콘 케이스", "", GENERIC),
        ("HP 정품 토너 CF217A", "", GENERIC),
        ("해피바스 우유 바디워시", "뷰티>바디", BEAUTY),
        ("에버콜라겐 타임 112정", "", GENERIC),
        ("코카콜라 제로 355ml 24캔", "", DRINK),
        ("딤채 김치냉장고 스탠드형", "", APPLIANCE),
        ("LG 트롬 드럼세탁기 21kg", "", APPLIANCE),
        ("애플 아이패드 프로 11", "", DIGITAL),
        ("LG 27인치 모니터", "가전디지털", DIGITAL),
        # 부품·소모품 (리뷰 2차): '가전은 한 번 사면 몇 년 씀'이 160매 건조기 시트에 붙으면 틀린 말
        ("건조기 시트 160매", "", GENERIC),
        ("세탁기 거름망", "", GENERIC),
        ("밥솥 내솥", "", GENERIC),
        ("에어프라이어 실리콘 용기", "", GENERIC),
        ("커피머신 세척 태블릿", "", GENERIC),
        ("노트북 파우치 15인치", "", GENERIC),
        ("다이슨 호환 배터리", "", GENERIC),
        ("LG 디오스 식기세척기 12인용", "", APPLIANCE),  # '세척'은 부속으로 보되 '식기세척기' 자체는 가전
    ],
)
def test_consumables_do_not_get_appliance_hooks(r: TemplateRenderer, name: str, category: str, want: set[str]) -> None:
    assert _pool(r, name, category) == want, name


def test_appliance_hooks_say_appliance_not_big_appliance() -> None:
    assert "가전은 한 번 사면 몇 년 씀" in APPLIANCE and not any("큰 가전" in h for h in APPLIANCE)


# ---------------------------------------------------------------- C3 #15: 짧은 이름이 묶음 수량을 잃지 않음
@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("비비고 왕교자 1.05kg + 1.05kg", "비비고 왕교자 1.05kg + 1.05kg"),
        ("제주삼다수 2L / 12병", "제주삼다수 2L 12병"),
        ("코카콜라 제로 355ml / 24캔", "코카콜라 제로 355ml 24캔"),
        ("햇반 210g 24개 + 12개", "햇반 210g 24개 + 12개"),
        ("본품 500ml + 리필 500ml x 3개", "본품 500ml + 리필 500ml x 3개"),
        ("소니 WH-1000XM5 노이즈캔슬링 헤드폰", "소니 WH-1000XM5 노이즈캔슬링 헤드폰"),  # 짧으면 모델명이 곧 이름
        ("다이슨 V12 무선 청소기 + 물걸레 브러시 + 침구 브러시", "다이슨 V12 무선 청소기"),  # 구성품 이름은 뺌
    ],
)
def test_short_name_keeps_pack_quantity(name: str, short: str) -> None:
    assert short_name(name) == short


def test_threads_price_line_keeps_the_bundle(r: TemplateRenderer) -> None:
    d = Deal(Product(source="s", product_id="coupang:2", shop="coupang", name="제주삼다수 2L / 12병", price=12900, url="u"),
             DealVerdict(is_deal=True), affiliate_url=LINK)
    th = r.render_deal(d, LINK, shop=REG.get("coupang"), template="deal_threads.j2", autoescape=False)
    assert "제주삼다수 2L 12병 12,900원" in th


# ---------------------------------------------------------------- C3 #4: 카드 배지도 글과 같은 등급
def test_card_badge_follows_the_post_tier(r: TemplateRenderer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """평균보다 72% 싸도 20일 안에 30,000원이 있었으면 글은 '☑️ 쿠팡보다 25%↓' — 카드도 '역대급'이 아니라 '핫딜'."""
    v = DealVerdict(is_deal=True, avg_price=180000, below_avg_pct=72.2, low_price=30000, history_days=20, sample_count=10,
                    market_price=67000, below_market_pct=25.4)
    d = Deal(Product(source="s", product_id="coupang:1", shop="coupang", name="상품", price=50000, url="u"), v, affiliate_url=LINK)
    card = DealCard(tmp_path / "media")
    badges: list[str] = []
    monkeypatch.setattr(card, "_badge", lambda draw, text, *a, **k: badges.append(text))
    tier, _ = r.deal_tier(d)
    assert tier == "normal"
    raw = card.render(d, None, tier=tier)
    assert Image.open(io.BytesIO(raw)).format == "JPEG"
    card.render(d, None)  # 등급을 모르면 근거 없는 말을 안 붙임
    top = Deal(Product(source="s", product_id="coupang:2", shop="coupang", name="상품", price=9900, url="u"),
               DealVerdict(is_deal=True, market_price=36000, below_market_pct=72.5), affiliate_url=LINK)
    card.render(top, None, tier=r.deal_tier(top)[0])
    assert badges == ["핫딜", "핫딜", "초특가"] and not any("역대급" in b for b in badges)


# ---------------------------------------------------------------- C3 #9: 카톡·인스타·블로그도 읽는 배송 말
@pytest.mark.parametrize("template", ["deal_kakao.j2", "deal_instagram.j2", "deal_blog.j2"])
def test_other_templates_show_readable_shipping(r: TemplateRenderer, template: str) -> None:
    d = Deal(Product(source="s", product_id="naver:1", shop="naver", name="곰곰 특란 30구", price=6900, url="u", shipping="네멤무배"),
             DealVerdict(is_deal=True), affiliate_url=LINK)
    text = r.render_deal(d, LINK, shop=REG.get("naver"), template=template, autoescape=False)
    assert "네이버 멤버십 무료배송" in text and "네멤무배" not in text and "배송 배송" not in text


# ---------------------------------------------------------------- 트레비 단가 (서버 시험 글의 '100ml당 2,543원' 사실 오류)
def test_trevi_unit_price_is_per_100ml_of_the_whole_pack(r: TemplateRenderer) -> None:
    """350ml x 20병 8,900원 = 7,000ml → 100ml 당 약 127원 (병당 445원). '100ml당 2,543원'(병 하나 기준 오계산)은 절대 안 됨."""
    name = "트레비 플레인 350ml 20펫"
    unit = unit_price(clean_name(name), 8900)
    assert unit == "병당 445원 (100ml당 127원)"
    per_100 = int(re.search(r"100ml당 ([\d,]+)원", unit or "").group(1).replace(",", ""))  # type: ignore[union-attr]
    assert abs(per_100 - 8900 / 7000 * 100) < 1
    d = Deal(Product(source="s", product_id="coupang:t", shop="coupang", name=name, price=8900, url="u"),
             DealVerdict(is_deal=True), affiliate_url=LINK)
    shop = REG.get("coupang")
    rendered = [r.render_deal(d, LINK, shop=shop), str(r.deal_facts(d))]
    rendered += [r.render_deal(d, LINK, shop=shop, template=t, autoescape=False)
                 for t in ("deal_threads.j2", "deal_kakao.j2", "deal_instagram.j2", "deal_blog.j2")]
    for text in rendered:
        assert "2,543" not in text and "2543" not in text, text
    assert "병당 445원 꼴" in rendered[0]
