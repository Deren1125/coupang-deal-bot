"""채널·스레드 문구 규칙 회귀 테스트 (2026-10 템플릿 감사: 등급·최저가·배송·쿠폰·스레드 첫 줄/마무리/짧은 이름/고지)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dealbot.cli import sample_deal
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.templates import TemplateRenderer, shipping_text, short_name
from dealbot.publisher.threads import (
    KV_RECENT_LINES,
    KV_TOKEN,
    KV_USER_ID,
    TEXT_LIMIT,
    ThreadsClient,
    ThreadsPublisher,
    fit_text,
)
from dealbot.shops import ShopRegistry
from dealbot.storage.db import Database

REG = ShopRegistry()
COUPANG = REG.get("coupang")
LINK = "https://link.coupang.com/a/x"


@pytest.fixture
def r(repo_root: Path) -> TemplateRenderer:
    return TemplateRenderer(repo_root / "templates")


def _deal(verdict: DealVerdict | None = None, **kw) -> Deal:  # type: ignore[no-untyped-def]
    base = dict(source="s", product_id="coupang:1", shop="coupang", name="상품", price=10000, url="u")
    base.update(kw)
    return Deal(Product(**base), verdict or DealVerdict(is_deal=True), affiliate_url=LINK)


def _tg(r: TemplateRenderer, d: Deal) -> str:
    return r.render_deal(d, LINK, shop=REG.get(d.product.shop))


def _th(r: TemplateRenderer, d: Deal, **kw) -> str:  # type: ignore[no-untyped-def]
    return r.render_deal(d, LINK, shop=REG.get(d.product.shop), template="deal_threads.j2", autoescape=False, **kw)


# ---------------------------------------------------------------- 등급 (#4)
def test_tier_ignores_average_when_price_is_above_recent_low(r: TemplateRenderer) -> None:
    """평균보다 72% 싸도 20일 안에 30,000원이 있었으면 50,000원은 '초특가·역대급'이 아님. 배지는 보이는 근거(쿠팡 25%)로."""
    v = DealVerdict(is_deal=True, avg_price=180000, below_avg_pct=72.2, low_price=30000, history_days=20, sample_count=10,
                    market_price=67000, below_market_pct=25.4)
    d = _deal(v, price=50000)
    assert r.deal_tier(d)[0] == "normal"
    assert "역대급" not in r.deal_facts(d)["labels"] and "강력 추천" not in r.deal_facts(d)["labels"]
    assert _tg(r, d).startswith("☑️ <b>50,000원</b> · 쿠팡보다 25%↓")
    # 쿠팡 대조가 없으면 그 평균은 아예 근거로 안 씀 (블로그 복붙 문구의 '30일 평균가 대비'도)
    no_market = _deal(DealVerdict(is_deal=True, avg_price=180000, below_avg_pct=72.2, low_price=30000, history_days=20,
                                  sample_count=10), price=50000)
    assert "평소보다" not in _tg(r, no_market)
    blog = r.render_deal(no_market, LINK, shop=COUPANG, template="deal_blog.j2", autoescape=False)
    assert "평균가" not in blog
    # 블로그 내보내기 줄(deal_facts)도 같은 기준
    assert r.deal_facts(no_market)["ref_pct"] is None and r.deal_facts(no_market)["avg_price"] is None
    assert (r.deal_facts(d)["ref_pct"], r.deal_facts(d)["ref_label"]) == (25, "쿠팡 최저가")
    # 지금 가격이 최근 최저가 근처면 같은 평균도 믿고 씀
    near_low = _deal(DealVerdict(is_deal=True, avg_price=180000, below_avg_pct=72.2, low_price=49000, history_days=20,
                                 sample_count=10), price=50000)
    assert r.deal_tier(near_low)[0] == "top" and _tg(r, near_low).startswith("🔥 초특가 <b>50,000원</b> · 평소보다 72%↓")


def test_market_pct_never_exceeds_evaluator_value_with_shipping(r: TemplateRenderer) -> None:
    """평가기 값(배송비 포함)이 가격만으로 잰 값보다 작으면 그쪽을 씀 — 배송비가 붙은 딜을 부풀리지 않게."""
    d = _deal(DealVerdict(is_deal=True, market_price=10000, below_market_pct=30.0), price=6000)  # 가격만이면 40%
    assert _tg(r, d).startswith("☑️ <b>6,000원</b> · 쿠팡보다 30%↓")


# ---------------------------------------------------------------- 기록 최저가 (#5, #14)
@pytest.mark.parametrize(
    ("verdict", "label"),
    [
        # 25일 내내 같은 값: 내려간 적이 없으니 '최저' 아님
        (dict(low_price=12900, avg_price=12900, below_avg_pct=0.0, history_days=25, sample_count=10), None),
        # 직전 관측 1건뿐
        (dict(low_price=13900, history_days=3.5, sample_count=1), None),
        # 기록이 짧음 (7일 미만)
        (dict(low_price=13900, avg_price=14000, history_days=5, sample_count=6), None),
        # 진짜로 기록보다 내려감
        (dict(low_price=13900, avg_price=14000, history_days=12, sample_count=6), "12일 최저가 갱신"),
        # 오르내리다 최저가와 같은 값
        (dict(low_price=12900, avg_price=14000, history_days=12, sample_count=6), "12일 최저가"),
    ],
)
def test_low_label_needs_real_history(r: TemplateRenderer, verdict: dict, label: str | None) -> None:
    d = _deal(DealVerdict(is_deal=True, discount_rate=55, **verdict), source="goldbox", price=12900)
    labels = r.deal_facts(d)["labels"]
    lows = [x for x in labels if "최저가" in x]
    assert lows == ([label] if label else [])
    if label is None:
        assert "제일 쌈" not in _tg(r, d)


def test_reference_gap_beats_short_low_label(r: TemplateRenderer) -> None:
    """쿠팡보다 72% 싼데 '3일 중 제일 쌈'이 그걸 가리면 안 됨."""
    v = DealVerdict(is_deal=True, market_price=36000, below_market_pct=72.5, low_price=9900, history_days=3.2, sample_count=4)
    d = _deal(v, source="goldbox", price=9900)
    tg = _tg(r, d)
    assert tg.startswith("🔥 초특가 <b>9,900원</b> · 쿠팡보다 72%↓") and "3일" not in tg
    th = _th(r, d)
    assert "쿠팡 최저가는 36,000원" in th and "3일" not in th


# ---------------------------------------------------------------- 배송 (#9)
@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("무배", "무료배송"), ("무료", "무료배송"), ("무료배송", "무료배송"), ("네멤무배", "네이버 멤버십 무료배송"),
        ("와우무배", "와우 회원 무료배송"), ("3,000원", "배송비 3,000원"), ("3000", "배송비 3,000원"), ("0원", "무료배송"),
        ("2,500(3만↑무료)", "배송비 2,500(3만↑무료)"), ("", None), (None, None),
    ],
)
def test_shipping_text(raw: str | None, text: str | None) -> None:
    assert shipping_text(raw) == text


def test_post_shows_readable_shipping(r: TemplateRenderer) -> None:
    board = _deal(shop="naver", name="곰곰 특란 30구", price=7000, shipping="네멤무배")
    assert r.deal_facts(board)["ship"] == "네이버 멤버십 무료배송" and "배송관련 : 네이버 멤버십 무료배송" in _tg(r, board)
    fee = _deal(shop="naver", name="곰곰 특란 30구", price=7000, shipping="3,000원")
    assert "배송관련 : 배송비 3,000원" in _tg(r, fee)
    rocket = _deal(name="상품", price=7000, is_rocket=True, is_free_shipping=True, rating=4.9, review_count=52011)
    assert "배송관련 : 무료 로켓배송\n평점점수 : 4.9\n리뷰숫자 : 52,011" in _tg(r, rocket)  # 정보 항목형: 한 줄에 한 칸


# ---------------------------------------------------------------- 표시 할인율 (#21)
def test_list_discount_is_not_the_headline(r: TemplateRenderer) -> None:
    d = _deal(DealVerdict(is_deal=True, discount_rate=62), source="goldbox", name="필립스 전동칫솔", price=15900)
    tg = _tg(r, d)
    assert tg.splitlines()[0] == "☑️ <b>15,900원</b>"
    assert "정가대비 : 62%↓" in tg  # 보조 정보로만
    facts = r.deal_facts(d)
    assert facts["evidence_short"] is None and facts["evidence_casual"] is None and facts["sale_pct"] is None
    assert "정가" not in _th(r, d)


# ---------------------------------------------------------------- 쿠폰·이벤트 (#16)
def test_coupon_and_event_posts(r: TemplateRenderer) -> None:
    coupon = _deal(name="쿠팡 와우 생필품 5천원 할인쿠폰 (~10/9)", price=0, deal_kind="coupon")
    tg = _tg(r, coupon)
    assert tg.splitlines()[0] == "🎟 <b>쿠팡 와우 생필품 5천원 할인쿠폰 (~10/9)</b>"
    assert "쿠폰 받으러 가기" in tg and "구매하러" not in tg and tg.count("할인쿠폰") == 1
    event = _deal(name="스타벅스 e-프리퀀시 응모", price=0, deal_kind="event")
    tg = _tg(r, event)
    assert tg.splitlines()[0] == "🎁 이벤트 · <b>스타벅스 e-프리퀀시 응모</b>" and "이벤트 보러 가기" in tg
    # 수동 등록은 늘 event 로 들어오지만 이름에 쿠폰이 있으면 쿠폰
    manual = _deal(name="배민 1만원 쿠폰", price=0, deal_kind="event")
    assert _tg(r, manual).startswith("🎟 <b>배민 1만원 쿠폰</b>")


def test_coupon_threads_has_no_price_talk(r: TemplateRenderer) -> None:
    for i in range(30):
        d = _deal(product_id=f"coupang:c{i}", name="쿠팡 와우 생필품 5천원 할인쿠폰", price=0, deal_kind="coupon")
        lines = r.thread_lines(d.product)
        for text in (lines["thread_hook"], lines["thread_close"] or ""):
            assert not any(w in text for w in ("가격", "싸", "쌈", "쟁여", "장바구니")), text
    ai = _deal(name="쿠팡 할인쿠폰", price=0, deal_kind="coupon", extra={"thread_hook": "이 가격 다시 없음"})
    assert r.thread_lines(ai.product)["thread_hook"] in [h for h, _ in r.THREAD_HOOKS_NOPRICE]


# ---------------------------------------------------------------- 스레드 첫 줄 분류 (#12)
def _group(word: str) -> set[str]:
    for words, _, hooks in TemplateRenderer.THREAD_HOOK_GROUPS:
        if word in words:
            return {h for h, _ in hooks}
    raise AssertionError(word)


GENERIC = {h for h, _ in TemplateRenderer.THREAD_HOOKS}


@pytest.mark.parametrize(
    ("category", "name", "pool"),
    [
        ("가전디지털", "LG 트롬 드럼세탁기 21kg", _group("세탁기")),
        ("가전디지털", "다이슨 V12 무선 청소기", _group("세탁기")),
        ("가전디지털", "삼성 비스포크 제트 무선청소기", _group("세탁기")),
        ("생활가전", "위닉스 공기청정기", _group("세탁기")),
        ("가전디지털", "드롱기 전자동 커피머신", _group("세탁기")),
        ("가전디지털", "LG 27인치 모니터", _group("모니터")),
        ("", "코지 바디필로우", GENERIC),
        ("주방용품", "락앤락 채소탈수기", _group("프라이팬")),
        ("", "스파클 생수 무라벨, 500ml, 40병", _group("생수")),
        ("", "충전식 손난로", GENERIC),
        ("생활용품>화장지", "크리넥스 3겹 30m 30롤", _group("화장지")),
        ("", "스텐 휴지통 20L", GENERIC),
    ],
)
def test_category_hooks_fit_the_item(r: TemplateRenderer, category: str, name: str, pool: set[str]) -> None:
    for pid in ("coupang:1", "coupang:12", "coupang:123"):
        hook = r.thread_lines(Product(source="s", product_id=pid, shop="coupang", name=name, price=10000, url="u",
                                      category=category))["thread_hook"]
        assert hook in pool, (name, hook)


# ---------------------------------------------------------------- '근데' 반전 (#18)
def test_turn_only_after_situation_hooks(r: TemplateRenderer) -> None:
    situation = {h for g in TemplateRenderer.THREAD_HOOK_GROUPS for h, turn in g[2] if turn}
    seen_statement = seen_situation = False
    for i in range(40):
        for name in ("할리스 바닐라 딜라이트 로우슈거, 24개", "크리넥스 화장지 30롤"):
            d = _deal(product_id=f"toss:{i}", shop="toss", name=name, price=24900)
            lines = _th(r, d).split("\n")
            hook, price_line = lines[0], lines[2]
            if hook in situation:
                seen_situation = True
                assert price_line.startswith("근데 "), lines
            else:
                seen_statement = True
                assert not price_line.startswith("근데"), lines
    assert seen_statement and seen_situation
    ai = _deal(name="상품 24개", extra={"thread_hook": "라면 떨어지면 꼭 밤에 알게 됨"})
    assert _th(r, ai).split("\n")[2].startswith("근데 ")  # AI 첫 줄은 상황 문장으로 쓰게 함


# ---------------------------------------------------------------- 짧은 이름 (#15)
@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("삼성전자 비스포크 AI 제트 400W 무선청소기 VS28C973DRG 새틴 그레이지 청정스테이션 포함 + 물걸레 브러시 + 침구 브러시 "
         "(타임딜 한정)", "삼성전자 비스포크 AI 제트 400W 무선청소기"),
        ("할리스 바닐라 딜라이트 로우슈거, 24개", "할리스 바닐라 딜라이트 로우슈거 24개"),
        ("스파클 생수 무라벨, 500ml, 40병", "스파클 생수 무라벨 500ml 40병"),
        ("삼성 청소기 (타임딜 한정)", "삼성 청소기"),
        ("[네이버] 곰곰 특란 30구 (네멤무배)", "곰곰 특란 30구"),
        ("비비고 왕교자 1.05kg x 2봉 (2개)", "비비고 왕교자 1.05kg x 2봉 (2개)"),  # 수량 괄호는 남김
        ("농심 신라면 블랙 사발면 컵라면 매운맛 큰사발 오리지널 101g 16개", "농심 신라면 블랙 사발면 컵라면 매운맛 큰사발 16개"),  # 잘린 수량은 다시
        ("[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L", "[샘플] 스탠리 텀블러 퀜처 H2.0 1.18L"),  # 테스트 표시는 남김
    ],
)
def test_fallback_short_name(name: str, short: str) -> None:
    assert short_name(name) == short


def test_threads_uses_short_name_when_ai_missing(r: TemplateRenderer) -> None:
    long = ("삼성전자 비스포크 AI 제트 400W 무선청소기 VS28C973DRG 새틴 그레이지 청정스테이션 포함 + 물걸레 브러시 + 침구 브러시 "
            "+ 연장관 풀세트 2024년형 정품 국내 AS (타임딜 한정)")
    th = _th(r, _deal(name=long, price=699000))
    assert "삼성전자 비스포크 AI 제트 400W 무선청소기 699,000원\n" in th
    assert "VS28C973DRG" not in th and "타임딜" not in th and "+" not in th
    ai = _deal(name=long, price=699000, extra={"short_name": "비스포크 제트 청소기"})
    assert "비스포크 제트 청소기 699,000원\n" in _th(r, ai)


# ---------------------------------------------------------------- 마무리 겹침·반복 (#17)
def test_hook_and_close_never_repeat_a_stem(r: TemplateRenderer) -> None:
    for i in range(200):
        lines = r.thread_lines(Product(source="s", product_id=f"toss:{i}", shop="toss", name="상품", price=1000, url="u"))
        if lines["thread_close"]:
            assert "필요" not in lines["thread_hook"] or "필요" not in lines["thread_close"], lines


def test_recent_lines_are_skipped(r: TemplateRenderer) -> None:
    p = Product(source="s", product_id="toss:abc", shop="toss", name="상품", price=1000, url="u")
    first = r.thread_lines(p)["thread_hook"]
    second = r.thread_lines(p, recent=[first])["thread_hook"]
    assert second != first and second in GENERIC
    # 다 최근에 썼으면 가장 오래전에 쓴 것
    everything = [h for h, _ in r.THREAD_HOOKS]
    assert r.thread_lines(p, recent=everything)["thread_hook"] == everything[0]


def _client(handler) -> ThreadsClient:  # type: ignore[no-untyped-def]
    return ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="A", app_secret="S", retry_backoff=0.01)


def _ok(req: httpx.Request) -> httpx.Response:
    if req.url.path.endswith("/threads_publish"):
        return httpx.Response(200, json={"id": "100"})
    if req.url.path.endswith("/threads"):
        return httpx.Response(200, json={"id": "C1"})
    return httpx.Response(200, json={"status": "FINISHED"})


async def test_threads_rotation_over_ten_posts(db: Database, repo_root: Path) -> None:
    """같은 첫 줄·마무리를 최근 10건 안에 다시 안 씀. 미리보기와 실제 글이 같고, 연습 모드는 기록을 안 남김."""
    renderer = TemplateRenderer(repo_root / "templates")
    posted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/threads") and "reply_to_id" not in req.url.params:
            posted.append(req.url.params["text"])
        return _ok(req)

    pub = ThreadsPublisher(_client(handler), db, renderer)
    db.kv_set(KV_TOKEN, "T")
    db.kv_set(KV_USER_ID, "999")
    deals = [_deal(product_id=f"toss:{i}", shop="toss", name=f"상품 {i}", price=9900) for i in range(10)]
    for d in deals:
        preview = pub.render(d)
        assert (await pub.publish(d)).ok
        assert posted[-1] == preview
    hooks = [t.split("\n")[0] for t in posted]
    assert len(set(hooks)) == 10, hooks
    closes = [t.split("\n\n")[-2] for t in posted if t.split("\n\n")[-2] in TemplateRenderer.THREAD_CLOSES]
    assert len(closes) == len(set(closes)), closes
    saved = db.kv_get(KV_RECENT_LINES) or ""
    assert hooks[-1] in saved

    dry = ThreadsPublisher(_client(handler), db, renderer, dry_run=True)
    await dry.publish(_deal(product_id="toss:dry", shop="toss", name="상품", price=9900))
    assert db.kv_get(KV_RECENT_LINES) == saved


# ---------------------------------------------------------------- 고지 + 링크 (#11, TH-02)
@pytest.mark.parametrize("shop_key", ["coupang", "toss", "naver"])
def test_reply_carries_disclosure(repo_root: Path, db: Database, shop_key: str) -> None:
    """링크는 답글이 아니라 본문에: 공식 고지 문구 바로 아래 링크. 기본은 답글 없음."""
    shop = REG.get(shop_key)
    assert shop is not None and shop.disclosure
    pub = ThreadsPublisher(_client(_ok), db, TemplateRenderer(repo_root / "templates"))
    d = _deal(shop=shop_key, product_id=f"{shop_key}:1", name="상품 24개", price=9900)
    assert pub.render_reply(d) is None
    post = pub.render(d)
    assert post.endswith(f"{shop.disclosure}\n👉 {LINK}") and len(post) <= TEXT_LIMIT and not pub.problems(d, post)


# ---------------------------------------------------------------- 한 글 모드 (#22)
def test_single_post_mode_puts_link_in_post(repo_root: Path, db: Database) -> None:
    pub = ThreadsPublisher(_client(_ok), db, TemplateRenderer(repo_root / "templates"), reply_template=None)
    post = pub.render(sample_deal())
    assert post.endswith("👉 https://link.coupang.com/a/sample") and "댓글에" not in post and len(post) <= TEXT_LIMIT
    assert pub.render_reply(sample_deal()) is None
    # 본문이 길어도 고지와 링크는 안 잘림
    long = sample_deal()
    long.product.extra["thread_take"] = "가" * 480
    post = pub.render(long)
    assert len(post) <= TEXT_LIMIT and post.endswith(f"{COUPANG.disclosure}\n👉 https://link.coupang.com/a/sample")


def test_fit_text_keeps_disclosure() -> None:
    body = "첫 줄\n\n" + "\n".join(["긴 줄" * 30] * 5) + "\n\n이 포스팅은 고지입니다.\n링크는 댓글에 👇"
    out = fit_text(body, 200, keep="이 포스팅은 고지입니다.")
    assert len(out) <= 200 and out.startswith("첫 줄") and out.endswith("이 포스팅은 고지입니다.\n링크는 댓글에 👇")
    assert fit_text("짧음", 200, keep="x") == "짧음"


# ---------------------------------------------------------------- 채널 글 A 정보 항목형 (주인 선택 2026-10)
def test_channel_post_is_info_field_layout(r: TemplateRenderer) -> None:
    d = _deal(DealVerdict(is_deal=True, score=60, below_market_pct=25, market_price=39900, market_source="coupang"),
              name="스탠리 퀜처 텀블러 1.18L", price=29900, category="주방용품>텀블러", is_rocket=True, is_free_shipping=True,
              rating=4.7, review_count=1312)
    d.product.extra["auth"] = {"status": "ok", "reason": "공식 판매처 표시 '공식'"}
    tg = _tg(r, d)
    lines = tg.splitlines()
    assert lines[0].startswith("☑️ <b>29,900원</b>") or lines[0].split(" <b>")[0] in ("🔥 초특가", "👍 강추")  # 1줄 = 가격 (알림 미리보기)
    assert lines[1] == "<b>스탠리 퀜처 텀블러 1.18L</b>"
    for row in ("카테고리 : 텀블러", "배송관련 : 무료 로켓배송", "판매처 : 공식 판매처 확인", "평점점수 : 4.7", "리뷰숫자 : 1,312",
                "방장 한줄평 : 주방템은 한 번 사면 오래 써요"):
        assert row in lines, row
    assert tg.index("방장 한줄평") < tg.index("👉") < tg.index("<i>이 포스팅은")  # 링크 → 제휴 고지는 끝
    assert len(tg) < 1024  # 사진 캡션 한도


def test_channel_post_one_liner_prefers_ai_comment_and_skips_unknown_rows(r: TemplateRenderer) -> None:
    d = _deal(name="이름 모를 상품", price=9900)
    d.product.extra["comment"] = "출근길 가방에 쏙 들어가는 크기예요"
    tg = _tg(r, d)
    assert "방장 한줄평 : 출근길 가방에 쏙 들어가는 크기예요" in tg
    assert "판매처 :" not in tg and "평점점수" not in tg and "리뷰숫자" not in tg and "순위형성" not in tg  # 모르는 칸은 빼고 지어내지 않음
    assert r.one_liner(_deal(name="필립스 전동칫솔", price=15900).product).startswith("오래 쓰는 가전")
    assert not r.one_liner(_deal(name="필립스 전동칫솔 리필모 4입", price=15900).product).startswith("오래 쓰는")  # 소모품은 가전 아님
