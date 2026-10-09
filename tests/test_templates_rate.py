from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from dealbot.cli import sample_deal
from dealbot.config import PublishConfig
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.rate_limiter import RateLimiter
from dealbot.publisher.telegram import normalize_chat_id
from dealbot.publisher.templates import TemplateRenderer
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow


def test_render_deal_post(repo_root: Path) -> None:
    from dealbot.shops import ShopRegistry

    r = TemplateRenderer(repo_root / "templates")
    text = r.render_deal(sample_deal(), "https://link.coupang.com/a/sample", shop=ShopRegistry().get("coupang"))
    lines = text.splitlines()
    assert lines[0] == "☑️ <b>29,900원</b> · 평소보다 29%↓"  # 1줄 = 알림 미리보기: 등급 + 가격 + 근거
    assert lines[1] == "<b>[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L</b>" and "[샘플 · 오늘의 특가]" in lines
    assert "평소보다 확실히" not in text  # 숫자 없는 상투 문구는 쓰지 않음
    assert '\n👉 <a href="https://link.coupang.com/a/sample">구매하러 가기</a>\n' in text  # 긴 주소 대신 글자 링크
    assert text.endswith("<i>이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.</i>")
    assert len(text) < 1024  # 사진 캡션 한도


def test_render_escapes_html(repo_root: Path) -> None:
    r = TemplateRenderer(repo_root / "templates")
    p = Product(source="s", product_id="x:1", shop="temu", name="<script>alert(1)</script> & 상품", price=1000, url="u")
    d = Deal(product=p, verdict=DealVerdict(is_deal=True), affiliate_url="https://l")
    text = r.render_deal(d, "https://l")
    assert "&lt;script&gt;" in text and "&amp;" in text
    assert "할인" not in text and "평균가" not in text and "포스팅" not in text  # 고지 문구 없는 몰
    coupon = Product(source="s", product_id="coupang:c", shop="coupang", name="10% 쿠폰", price=0, url="u", deal_kind="coupon")
    t2 = r.render_deal(Deal(product=coupon, verdict=DealVerdict(is_deal=True)), "https://l")
    assert "정가" not in t2 and "10% 쿠폰" in t2


def test_status_and_summary_templates_render(repo_root: Path, db: Database) -> None:
    r = TemplateRenderer(repo_root / "templates")
    ctx = {
        "version": "0.1.0",
        "uptime": "1시간",
        "paused": False,
        "dry_run": True,
        "publish_enabled": True,
        "has_coupang": False,
        "has_channel": False,
        "collectors": [
            {"name": "a", "type": "t", "enabled": True, "available": True, "unavailable_reason": None, "running": False,
             "interval_minutes": 5, "last_status": "ok", "last_run_ago": "1분 전", "collected": 3, "deals": 1, "queued": 1,
             "error": None, "next_in": "4분"},
            {"name": "b", "type": "t", "enabled": False},
            {"name": "c", "type": "t", "enabled": True, "available": False, "unavailable_reason": "no key"},
        ],
        "shops": [{"key": "coupang", "name": "쿠팡", "enabled": True, "mode": "자동 변환(coupang)"}, {"key": "temu", "name": "테무", "enabled": False, "mode": "꺼짐"}],
        "rate": {"posts_hour": 1, "posts_day": 2, "max_hour": 6, "max_day": 40, "last_post_at": utcnow()},
        "queue": {"pending": 2},
        "products": 10,
        "price_points": 100,
        "db_mb": 0.1,
        "last_error": "boom <x>",
        "last_error_at": "09/03 10:00",
        "tz": "Asia/Seoul",
    }
    text = r.render("status.j2", **ctx)
    assert "연습 모드" in text and "쿠팡 API 키 없음" in text and "b — 꺼짐" in text and "no key" in text
    assert "• 쿠팡: 자동 변환(coupang)" in text and "테무" not in text
    assert "boom &lt;x&gt;" in text

    s = db.summary(utcnow() - timedelta(days=1))
    out = r.render("daily_summary.j2", s=s, tz="Asia/Seoul")
    assert "오늘 하루 정리" in out and "에러 없음" in out


def test_rate_limiter(db: Database) -> None:
    cfg = PublishConfig(max_per_hour=2, max_per_day=3, min_interval_seconds=60)
    rl = RateLimiter(db, cfg)
    now = utcnow()
    assert rl.check(now).allowed

    db.record_post(sample_deal(), channel_id=None, message_id=None, now=now - timedelta(seconds=30))
    d = rl.check(now)
    assert not d.allowed and d.reason == "min_interval" and d.retry_after == timedelta(seconds=30)

    db.record_post(sample_deal(), channel_id=None, message_id=None, now=now - timedelta(minutes=10))
    assert rl.check(now).reason == "min_interval"
    assert rl.check(now + timedelta(minutes=2)).reason == "hourly_limit"

    later = now + timedelta(hours=2)
    db.record_post(sample_deal(), channel_id=None, message_id=None, now=later - timedelta(minutes=5))
    assert rl.check(later).reason == "daily_limit"
    assert rl.check(later + timedelta(days=1)).allowed
    snap = rl.snapshot(later)
    assert snap["posts_hour"] == 1 and snap["posts_day"] == 3 and snap["max_hour"] == 2


def test_normalize_chat_id() -> None:
    assert normalize_chat_id("-1001234") == -1001234
    assert normalize_chat_id("42") == 42
    assert normalize_chat_id("@chan") == "@chan"
    assert normalize_chat_id(None) is None


def test_emphasis_tiers(repo_root: Path) -> None:
    from dealbot.shops import ShopRegistry

    r = TemplateRenderer(repo_root / "templates")
    shop = ShopRegistry().get("coupang")

    def deal(**v):  # type: ignore[no-untyped-def]
        p = Product(source="s", product_id="coupang:9", shop="coupang", name="갈아만든배 340ml 24개", price=12360, url="u")
        return Deal(product=p, verdict=DealVerdict(is_deal=True, **v), affiliate_url="https://l")

    must = r.render_deal(deal(below_avg_pct=55.0, avg_price=27000), "https://l", shop=shop)
    assert must.startswith("👍 강추 <b>12,360원</b> · 평소보다 54%↓") and "단위가격 : 개당 515원 꼴" in must
    top = r.render_deal(deal(below_market_pct=72.5, market_price=45000), "https://l", shop=shop)
    assert top.startswith("🔥 초특가 <b>12,360원</b> · 쿠팡보다 73%↓")
    plain = r.render_deal(deal(below_avg_pct=6.0, avg_price=13207), "https://l", shop=shop)
    assert plain.startswith("☑️ <b>12,360원</b> · 개당 515원 꼴") and "평소" not in plain  # 6% 는 근거로 안 씀
    # 평소 가격 대비가 기록 최저가보다 먼저 (스타일 가이드 우선순위), 최저가는 평소 가격 근거가 없을 때
    both = deal(below_avg_pct=20.0, avg_price=15450, low_price=13000, history_days=12.4, sample_count=6)
    assert r.render_deal(both, "https://l", shop=shop).startswith("☑️ <b>12,360원</b> · 평소보다 20%↓")
    low = r.render_deal(deal(below_avg_pct=8.4, avg_price=13500, low_price=13000, history_days=12.4, sample_count=6), "https://l", shop=shop)
    assert low.startswith("☑️ <b>12,360원</b> · 12일 중 제일 쌈")
    th = r.render_deal(deal(below_avg_pct=20.0, avg_price=15450), "https://l", shop=shop, template="deal_threads.j2", autoescape=False)
    assert "갈아만든배 340ml 24개 12,360원\n평소엔 15,000원대" in th and "쿠팡 파트너스" in th  # 평균 15,450 → '원대'는 끝자리 내림
    assert "원임" not in th and "% 쌈" not in th and "댓글" not in th  # 주인이 뺀 말 (TH-02)
    kakao = r.render_deal(deal(below_avg_pct=55.0, avg_price=27000), "https://l", shop=shop, template="deal_kakao.j2", autoescape=False)
    assert kakao.startswith("🔥 갈아만든배") and "👉 " in kakao and "평균가" not in kakao and "✱" not in kakao


def test_unit_price() -> None:
    from dealbot.utils.text import unit_price

    assert unit_price("비비고 왕교자 1.05kg x 2봉", 13900) == "봉당 6,950원 (100g당 662원)"
    assert unit_price("햇반 210g, 36개", 30900) == "개당 858원 (100g당 409원)"
    assert unit_price("크리넥스 3겹 30m 30롤", 14900) == "롤당 497원"
    assert unit_price("삼성 25W 고속충전기", 15900) is None and unit_price("LG 27인치 모니터", 199000) is None


def test_comment_rules() -> None:
    from dealbot.commentary import check_comment

    assert check_comment("자취생 냉동실 상비용으로 좋습니다.") == "자취생 냉동실 상비용으로 좋습니다."
    assert check_comment("단백질 20g 함유로 좋습니다") is None  # 확인된 정보에 없는 숫자 금지
    assert check_comment("봉당 716원 꼴이면 편의점 갈 일이 없어요.", "봉당 716원 꼴") == "봉당 716원 꼴이면 편의점 갈 일이 없어요."
    assert check_comment("써보니 좋아요.") is None  # 지어낸 경험
    assert check_comment("역대급 가격이에요!") is None and check_comment("SKIP") is None


def test_clean_name_strips_board_decorations() -> None:
    from dealbot.utils.text import clean_name

    assert clean_name("초특가★슈페리어→골져스 파셜★UP 더블룸") == "슈페리어→골져스 파셜 UP 더블룸"
    assert clean_name("[무배] ★역대급★ 스파클 생수 2L 24개") == "스파클 생수 2L 24개"
    assert clean_name("갈아만든배 340ml 24개") == "갈아만든배 340ml 24개"


def test_naver_search_help() -> None:
    from dealbot.monitoring.admin import naver_search_help

    t = naver_search_help("[무배] 순살족발 300g + 증정", "https://m.smartstore.naver.com/mggtable/products/1", "naver")
    assert "<code>순살족발 300g + 증정</code>" in t and "<code>mggtable</code>" in t
    assert naver_search_help("x", "https://toss.im/a", "toss") == ""


def test_unit_price_bottles_and_unknown_units() -> None:
    from dealbot.utils.text import unit_price

    assert unit_price("트레비 플레인 350ml 20펫", 8900) == "병당 445원 (100ml당 127원)"
    assert unit_price("탄산수 350ml 20입수", 8900) is None  # 모르는 단위면 계산 안 함


def test_threads_uses_ai_lines_and_category_hooks(repo_root: Path) -> None:
    from dealbot.commentary import check_hook, check_short_name
    from dealbot.shops import ShopRegistry

    r = TemplateRenderer(repo_root / "templates")
    shop = ShopRegistry().get("coupang")
    p = Product(source="s", product_id="coupang:7", shop="coupang", name="크리넥스 3겹 데코앤소프트 30m 30롤", price=14900,
                url="u", category="생활용품>화장지",
                extra={"thread_take": "롤당 497원 꼴이면 휴지는 이때 사는 거임", "short_name": "크리넥스 30롤"})
    d = Deal(product=p, verdict=DealVerdict(is_deal=True, avg_price=21800, below_avg_pct=31.7), affiliate_url="https://l")
    t = r.render_deal(d, "https://l", shop=shop, template="deal_threads.j2", autoescape=False)
    lines = t.split("\n")
    assert lines[0] == "휴지 떨어진 거 꼭 샤워 끝나고 알게 됨"  # 분류 '화장지' → 휴지 첫 줄 (상황 문장이라 '근데'로 뒤집음)
    assert "근데 크리넥스 30롤 14,900원\n평소엔 21,000원대" in t and "롤당 497원 꼴이면" in t
    assert check_hook("휴지 떨어진 거 꼭 샤워 끝나고 알게 됨") and check_hook("어제 3개 샀음") is None
    assert check_short_name("크리넥스 30롤", "크리넥스 3겹 데코앤소프트 30m 30롤") == "크리넥스 30롤"
    assert check_short_name("크리넥스 프리미엄", "크리넥스 3겹 30롤") is None  # 상품명에 없는 말
