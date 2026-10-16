"""채널 성장 (주인 결정 10/15): 텔레그램 둘째 줄 광고 표시, 가격 기록 라벨, 스레드 리듬·태그·카드·품절 업데이트·답글 초안·투표,
일요일 TOP5, 통계 권한, 핫딜 페이지 지난 딜, 채널 안내, 초대 링크 가입 경로, SNS 0개 알림, 늦게 받는 딜 사진, 내 도메인 DNS.

네트워크 없음: 스레드·텔레그램은 가짜 (httpx.MockTransport · 가짜 봇)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image

from dealbot.cli import sample_deal
from dealbot.config import Settings, ThreadsConfig
from dealbot.growth import alerts, invites, weekly
from dealbot.growth.slots import slot_due, slot_time
from dealbot.growth.threads_flow import poll_summary, topic_tag
from dealbot.models import Deal, DealVerdict, Product, PublishResult
from dealbot.publisher import instagram as ig_mod
from dealbot.publisher import threads as th_mod
from dealbot.publisher.scopes import IG_INSIGHTS, THREADS_INSIGHTS, THREADS_READ_REPLIES, ScopeState
from dealbot.publisher.templates import TemplateRenderer
from dealbot.publisher.threads import (
    KV_TOKEN,
    KV_USER_ID,
    ThreadsClient,
    ThreadsToken,
    thread_problems,
)
from dealbot.shops import ShopRegistry, ad_line
from dealbot.storage.db import Database

REG = ShopRegistry()
KST_NOON = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)  # 목요일 12:00 KST


def _jpeg(w: int = 900, h: int = 900) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(out, "JPEG")
    return out.getvalue()


def _deal(pid: str = "coupang:1", *, shop: str = "coupang", name: str = "스탠리 텀블러 1.18L", price: int = 29900,
          category: str | None = None, score: float = 10.0, image_url: str | None = None, **extra: Any) -> Deal:
    p = Product(source="s", product_id=pid, shop=shop, name=name, price=price, url=f"https://www.coupang.com/vp/products/{pid}",
                category=category, image_url=image_url, extra=dict(extra))
    return Deal(p, DealVerdict(is_deal=True, score=score), affiliate_url=f"https://link.coupang.com/a/{pid.split(':')[-1]}",
                detected_at=KST_NOON)


# ================================================================ 1. 텔레그램: 첫 줄 그대로, 둘째 줄 짧은 광고 표시
@pytest.mark.parametrize(("shop", "line"), [
    ("coupang", "광고 · 쿠팡 파트너스 수수료"),
    ("naver", "광고 · 네이버 쇼핑 커넥트 수수료"),
    ("toss", "광고 · 토스쇼핑 쉐어링크 수수료"),
])
def test_telegram_second_line_is_a_short_ad_and_full_disclosure_stays_last(repo_root: Path, shop: str, line: str) -> None:
    r = TemplateRenderer(repo_root / "templates")
    d = _deal(f"{shop}:1", shop=shop)
    text = r.render_deal(d, "https://l", shop=REG.get(shop))
    lines = text.splitlines()
    assert lines[0].startswith("☑️ <b>29,900원</b>")  # 첫 줄(알림 미리보기)은 가격 그대로
    assert lines[1] == line and ad_line(REG.get(shop)) == line  # 몰마다 늘 같은 말
    assert text.endswith(f"<i>{REG.get(shop).disclosure}</i>")  # 전체 고지는 끝 그대로
    coupon = Deal(Product(source="s", product_id=f"{shop}:c", shop=shop, name="5천원 할인쿠폰", price=0, url="u", deal_kind="coupon"),
                  DealVerdict(is_deal=True))
    assert r.render_deal(coupon, "https://l", shop=REG.get(shop)).splitlines()[1] == line  # 가격 없는 글도 둘째 줄


def test_no_commission_means_no_ad_line(repo_root: Path) -> None:
    """쇼핑커넥트에 없어 원본 주소로 올리는 네이버 딜(수수료 없음)·고지 없는 몰은 광고 표시도 없다 (지어내지 않음)."""
    r = TemplateRenderer(repo_root / "templates")
    raw = _deal("naver:2", shop="naver", raw_link=True)
    text = r.render_deal(raw, "https://smartstore.naver.com/x/products/2", shop=REG.get("naver"))
    assert "광고" not in text and "수수료" not in text
    th = r.render_deal(raw, "https://x", shop=REG.get("naver"), template="deal_threads.j2", autoescape=False)
    assert "[광고" not in th
    assert ad_line(REG.get("temu")) is None


def test_info_post_has_the_slot_but_no_ad_without_commission(repo_root: Path) -> None:
    """정보 글: 수수료 링크가 없어 광고 표시가 없다 (남의 제휴 링크는 빼고 올림). 자리는 딜 글과 같은 둘째 줄."""
    from dealbot.infopost import InfoPostBuilder, PostBody

    r = TemplateRenderer(repo_root / "templates")
    info = InfoPostBuilder(httpx.AsyncClient(), r)
    d = Deal(Product(source="ppomppu", product_id="info:1", shop="coupang", name="카드 결제일 할인 이벤트", price=0, url="https://b",
                     deal_kind="info", extra={"post_url": "https://board/1"}), DealVerdict(is_deal=True))
    body = PostBody(text="· 내용", images=[], links=[])
    tg = info.render("info_post.j2", d, body, autoescape=True)
    assert "광고" not in tg and "수수료" not in tg
    with_ad = r.render("info_post.j2", **{**info.context(d, body), "ad_line": "광고 · 쿠팡 파트너스 수수료"})
    assert with_ad.splitlines()[1] == "광고 · 쿠팡 파트너스 수수료"


# ================================================================ 2. 가격 기록 라벨
def test_price_record_label_everywhere(repo_root: Path) -> None:
    r = TemplateRenderer(repo_root / "templates")
    v = DealVerdict(is_deal=True, low_price=13000, avg_price=14000, history_days=14.2, sample_count=5)
    d = _deal(price=12360, name="갈아만든배 340ml 24개")
    d.verdict = v
    label = "우리가 본 최근 14일 중 가장 낮음 (10/15 12시 기준)"
    assert r.low_label(d, 14) == label
    facts = r.deal_facts(d)
    assert facts["low_label"] == label and facts["evidence_short"] == label and facts["evidence_casual"] == label
    for tpl, esc in (("deal_post.j2", True), ("deal_threads.j2", False), ("deal_kakao.j2", False), ("deal_blog.j2", False)):
        text = r.render_deal(d, "https://l", shop=REG.get("coupang"), template=tpl, autoescape=esc)
        assert "역대 최저" not in text and "일 최저가" not in text and "제일 쌈" not in text, tpl
    assert label in r.render_deal(d, "https://l", shop=REG.get("coupang"))
    # 관측이 low_min_samples 보다 적으면 라벨 없음
    d.verdict = DealVerdict(is_deal=True, low_price=13000, avg_price=14000, history_days=14.2, sample_count=2)
    assert r.low_label(d, 14) is None and "가장 낮음" not in r.render_deal(d, "https://l", shop=REG.get("coupang"))


# ================================================================ 3. 스레드: 첫 줄 광고 표시 · 검사 · 태그 · 투표 인자
def test_threads_first_line_ad_and_prepublish_check(db: Database, repo_root: Path) -> None:
    pub = th_mod.ThreadsPublisher(ThreadsClient(httpx.AsyncClient()), db, TemplateRenderer(repo_root / "templates"))
    d = sample_deal()
    post = pub.render(d)
    assert post.splitlines()[0] == "[광고·쿠팡 파트너스 수수료 받음]" and not pub.problems(d, post)
    assert post.endswith(f"{REG.get('coupang').disclosure}\n👉 https://link.coupang.com/a/sample")  # 전체 고지는 링크 바로 위
    moved = "\n".join(post.splitlines()[1:]) + "\n" + post.splitlines()[0]
    assert "첫 줄에 광고 표시가 없음" in thread_problems(moved, disclosure=REG.get("coupang").disclosure)


@pytest.mark.parametrize(("category", "name", "tag"), [
    ("주방용품>냄비", "냄비 세트", "집밥"),
    ("식품>라면", "진라면 40개", "집밥"),
    ("생활용품>세제", "식기세척기 세제 1kg", "살림"),
    ("스포츠/레저>요가", "요가매트", "헬스"),
    ("패션>신발", "운동화", "패션"),
    (None, "크리넥스 화장지 30롤", "살림"),
    (None, "무선 이어폰", "핫딜"),
])
def test_topic_tag_by_category(category: str | None, name: str, tag: str) -> None:
    assert topic_tag(ThreadsConfig(), _deal(name=name, category=category).product) == tag


def test_topic_tag_config_is_validated() -> None:
    with pytest.raises(ValueError):
        ThreadsConfig(topic_tag_default="핫딜.")
    with pytest.raises(ValueError):
        ThreadsConfig(topic_tags={"살림&생활": ["생활"]})


async def test_client_sends_topic_tag_and_poll_attachment() -> None:
    seen: list[dict[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(dict(req.url.params))
        if req.url.path.endswith("/threads"):
            return httpx.Response(200, json={"id": "C1"})
        if req.url.path.endswith("/threads_publish"):
            return httpx.Response(200, json={"id": "77"})
        return httpx.Response(200, json={"status": "FINISHED"})

    c = ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0.01)
    await c.post(ThreadsToken("T", "1"), "글", topic_tag="집밥")
    assert seen[0]["topic_tag"] == "집밥" and "poll_attachment" not in seen[0]
    seen.clear()
    await c.post(ThreadsToken("T", "1"), "글", topic_tag="살림.생활")  # 규칙에 안 맞는 태그는 빼고 올림
    assert "topic_tag" not in seen[0]
    seen.clear()
    await c.post(ThreadsToken("T", "1"), "다음 주 뭐부터 찾아볼까?", topic_tag="살림", poll=["주방·식품", "생활용품·세제", "가전·디지털"])
    assert json.loads(seen[0]["poll_attachment"]) == {"option_a": "주방·식품", "option_b": "생활용품·세제", "option_c": "가전·디지털"}
    assert seen[0]["media_type"] == "TEXT" and seen[0]["topic_tag"] == "살림"
    with pytest.raises(th_mod.ThreadsError):
        await c.post(ThreadsToken("T", "1"), "x", poll=["하나"])


def test_new_scopes_are_requested() -> None:
    assert {"threads_manage_insights", "threads_read_replies", "threads_manage_replies"} <= set(th_mod.SCOPES.split(","))
    assert "instagram_business_manage_insights" in ig_mod.SCOPES.split(",")
    assert "threads_read_replies" in th_mod.authorize_url("A", "https://x/cb")


# ================================================================ 4. 스레드 대기열 리듬 (실제 DealBot + 가짜 스레드 API)
class FakeThreads:
    """가짜 스레드 API: 만든 글·답글·투표를 기록."""

    def __init__(self) -> None:
        self.creates: list[dict[str, str]] = []
        self.replies: dict[str, list[dict[str, Any]]] = {}
        self.reply_status = 200
        self.poll: dict[str, Any] = {}
        self.n = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/threads") and req.method == "POST":
            self.creates.append(dict(req.url.params))
            return httpx.Response(200, json={"id": f"C{len(self.creates)}"})
        if path.endswith("/threads_publish"):
            self.n += 1
            return httpx.Response(200, json={"id": str(900 + self.n)})
        if path.endswith("/replies"):
            if self.reply_status != 200:
                return httpx.Response(self.reply_status, json={"error": {"message": "Application does not have permission", "code": 10}})
            post = path.split("/")[-2]
            return httpx.Response(200, json={"data": self.replies.get(post, [])})
        if "poll_attachment" in str(req.url.params.get("fields", "")):
            return httpx.Response(200, json={"poll_attachment": self.poll})
        return httpx.Response(200, json={"status": "FINISHED"})

    def posts(self) -> list[dict[str, str]]:
        return [c for c in self.creates if "reply_to_id" not in c]


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    settings.secrets.public_domain = "bot.example.com"
    settings.copy_cfg.enabled = False
    settings.instagram.enabled = False
    b = DealBot(settings)
    b.fake = FakeThreads()  # type: ignore[attr-defined]
    b.threads.client = ThreadsClient(httpx.AsyncClient(transport=httpx.MockTransport(b.fake)), app_id="A", app_secret="S",
                                     retry_backoff=0.01)
    b.threads.dry_run = False
    b.db.kv_set(KV_TOKEN, "T")
    b.db.kv_set(KV_USER_ID, "42")
    b.db.kv_set(th_mod.KV_TOKEN_EXPIRES, (KST_NOON + timedelta(days=50)).isoformat())
    b.clock = lambda: KST_NOON  # type: ignore[method-assign]
    sent: list[tuple[str, bool]] = []

    async def send_with_id(text: str, *, silent: bool = False, loud: bool = False, kind: str = "", queue_id: int | None = None) -> int:
        sent.append((text, silent))
        b.db.record_admin_msg(len(sent), kind, queue_id)
        return len(sent)

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    alerts_: list[dict[str, Any]] = []

    async def alert(kind: str, tg_text: str | None, kakao_text: str, **kw: Any) -> None:
        alerts_.append({"kind": kind, "tg": tg_text, "kakao": kakao_text, **kw})

    b.notifier.alert = alert  # type: ignore[method-assign]
    b.alerts = alerts_  # type: ignore[attr-defined]
    resolved: list[str] = []
    b.notifier.resolve = lambda *keys: resolved.extend(keys)  # type: ignore[method-assign]
    b.resolved = resolved  # type: ignore[attr-defined]

    async def not_sold_out(p: Product) -> bool:
        return False

    b._page_sold_out = not_sold_out  # type: ignore[method-assign]
    yield b
    b.db.close()


def _at(bot, when: datetime) -> None:  # type: ignore[no-untyped-def]
    bot.clock = lambda: when


async def test_threads_posts_only_inside_hours_and_with_a_gap(bot) -> None:  # type: ignore[no-untyped-def]
    night = datetime(2026, 10, 14, 18, 30, tzinfo=UTC)  # 03:30 KST
    _at(bot, night)
    await bot._publish_side_channels(_deal("coupang:1"), photo=_jpeg(), deal_no=11)
    assert bot.fake.posts() == [] and len(bot.db.threads_waiting()) == 1  # 밤에는 기다림
    _at(bot, datetime(2026, 10, 14, 22, 0, tzinfo=UTC))  # 07:00 KST
    assert await bot.threads_flow.tick()
    assert len(bot.fake.posts()) == 1 and not bot.db.threads_waiting()
    await bot._publish_side_channels(_deal("coupang:2"), photo=_jpeg(), deal_no=12)
    _at(bot, datetime(2026, 10, 14, 22, 30, tzinfo=UTC))  # 30분 뒤: 아직
    assert not await bot.threads_flow.tick() and len(bot.fake.posts()) == 1
    _at(bot, datetime(2026, 10, 14, 22, 46, tzinfo=UTC))  # 46분 뒤
    assert await bot.threads_flow.tick() and len(bot.fake.posts()) == 2
    assert [r["outcome"] for r in bot.db.sns_log_since("threads", night)] == ["posted", "posted"]


async def test_threads_daily_deal_cap_and_expiry(bot) -> None:  # type: ignore[no-untyped-def]
    bot.settings.threads.rhythm.min_gap_minutes = 0
    t = datetime(2026, 10, 14, 23, 0, tzinfo=UTC)  # 08:00 KST
    for i in range(11):
        _at(bot, t + timedelta(minutes=i))
        await bot._publish_side_channels(_deal(f"coupang:{i}"), photo=_jpeg(), deal_no=i + 1)
    assert len(bot.fake.posts()) == 10 and len(bot.db.threads_waiting()) == 1  # 하루 딜 10개까지
    info = Deal(Product(source="ppomppu", product_id="info:9", shop="coupang", name="이벤트", price=0, url="u", deal_kind="info"),
                DealVerdict(is_deal=True, score=3))
    bot.threads_flow.enqueue_info(info, "📢 이벤트\n\n원문 👉 https://board/9")
    assert await bot.threads_flow.tick()  # 정보 글은 딜 개수와 따로
    assert bot.fake.posts()[-1]["media_type"] == "TEXT" and "image_url" not in bot.fake.posts()[-1]  # 게시판 사진 없이
    _at(bot, t + timedelta(hours=9))  # 8시간 넘게 기다린 딜은 내림
    await bot.threads_flow.tick()
    assert not bot.db.threads_waiting()


async def test_threads_card_when_no_verified_photo_and_tag(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:5", category="생활용품>화장지", name="크리넥스 30롤")
    await bot._publish_side_channels(d, photo=None, deal_no=5)
    post = bot.fake.posts()[-1]
    assert post["media_type"] == "IMAGE" and post["image_url"].startswith("https://bot.example.com/media/")  # 글자 카드
    assert post["topic_tag"] == "살림" and post["text"].startswith("[광고·쿠팡 파트너스 수수료 받음]\n")
    with_photo = _deal("coupang:6", image_url="https://img.example/p.jpg")
    _at(bot, KST_NOON + timedelta(hours=1))
    await bot._publish_side_channels(with_photo, photo=_jpeg(), deal_no=6)
    assert bot.fake.posts()[-1]["image_url"] == "https://img.example/p.jpg" and bot.fake.posts()[-1]["topic_tag"] == "핫딜"


async def test_delayed_threads_post_rechecks_sold_out(bot) -> None:  # type: ignore[no-untyped-def]
    _at(bot, datetime(2026, 10, 14, 18, 0, tzinfo=UTC))  # 03:00 KST: 기다림
    await bot._publish_side_channels(_deal("coupang:7"), photo=_jpeg(), deal_no=7)

    async def sold(p: Product) -> bool:
        return True

    bot._page_sold_out = sold
    _at(bot, datetime(2026, 10, 14, 22, 5, tzinfo=UTC))
    assert await bot.threads_flow.tick()
    assert bot.fake.posts() == [] and bot.db.sns_log_since("threads", KST_NOON - timedelta(days=1))[0]["outcome"] == "skipped"


async def test_sold_out_reply_only_under_our_post_once(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:8")
    await bot._publish_side_channels(d, photo=_jpeg(), deal_no=8)
    post_id = bot.db.threads_posted_since(KST_NOON - timedelta(hours=1))[0].post_id
    _at(bot, KST_NOON + timedelta(minutes=95))  # 13:35 KST
    await bot._on_sold_out(d.product, via="게시판에 품절/종료 표시")
    await bot._on_sold_out(d.product, via="게시판에 품절/종료 표시")  # 게시판이 같은 품절 글을 또 돌려줘도
    replies = [c for c in bot.fake.creates if c.get("reply_to_id")]
    assert len(replies) == 1 and replies[0]["reply_to_id"] == post_id
    assert replies[0]["text"] == "업데이트) 13:35 기준 품절됐어" and "topic_tag" not in replies[0]
    bot.settings.threads.soldout_reply = False
    d2 = _deal("coupang:9")
    await bot._publish_side_channels(d2, photo=_jpeg(), deal_no=9)
    await bot._on_sold_out(d2.product, via="x")
    assert len([c for c in bot.fake.creates if c.get("reply_to_id")]) == 1


# ================================================================ 5. 답글 초안
async def test_reply_drafts_go_to_the_admin_chat_never_to_threads(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:10", name="곰곰 특란 30구", price=7000)
    d.product.is_rocket, d.product.is_free_shipping = True, True
    await bot._publish_side_channels(d, photo=_jpeg(), deal_no=10)
    post_id = bot.db.threads_posted_since(KST_NOON - timedelta(hours=1))[0].post_id
    bot.db.kv_set(th_mod.KV_USERNAME, "oneul_hot_deal")
    bot.fake.replies[post_id] = [
        {"id": "r1", "text": "이거 유통기한 언제까지예요?", "username": "kim"},
        {"id": "r2", "text": "업데이트) 13:00 기준 품절됐어", "username": "oneul_hot_deal"},  # 내 답글
    ]
    creates = len(bot.fake.creates)
    assert await bot.threads_flow.collect_replies(KST_NOON + timedelta(hours=1)) == 1
    msg, silent = bot.sent[-1]
    assert silent and msg.startswith("💬 <b>스레드 답글 #10</b>") and "원글: 곰곰 특란 30구 7,000원" in msg
    assert "답글 @kim: 이거 유통기한 언제까지예요?" in msg and "답변 초안 1: 올린 10/15 12:00 기준 7,000원이었어" in msg
    assert "답변 초안 2: 옵션·구성은 링크 상품 페이지에 나온 그대로" in msg  # 모르는 사양(유통기한)은 지어내지 않음
    assert "봇은 답글을 달지 않아요" in msg and len(bot.fake.creates) == creates  # 스레드엔 아무것도 안 올림
    assert bot.alerts[-1]["group"] == "threads_replies" and "스레드 답글 1개" in bot.alerts[-1]["kakao"]
    assert await bot.threads_flow.collect_replies(KST_NOON + timedelta(hours=2)) == 0  # 같은 답글은 다시 안 보냄
    bot.fake.replies[post_id].append({"id": "r3", "text": "와 싸다", "username": "lee"})
    await bot.threads_flow.collect_replies(KST_NOON + timedelta(hours=3))
    assert "답변 초안 2: 배송은 무료 로켓배송로 나와 있었어" in bot.sent[-1][0]  # 질문이 아니면 저장된 사실 한 줄


async def test_reply_reading_stops_quietly_without_the_scope(bot) -> None:  # type: ignore[no-untyped-def]
    await bot._publish_side_channels(_deal("coupang:11"), photo=_jpeg(), deal_no=11)
    bot.fake.reply_status = 403
    assert await bot.threads_flow.collect_replies(KST_NOON) == 0
    assert bot.threads.scopes.status(THREADS_READ_REPLIES) == "missing" and bot.sent == []
    calls = len(bot.fake.creates)
    assert await bot.threads_flow.collect_replies(KST_NOON) == 0 and len(bot.fake.creates) == calls  # 다시 두드리지 않음
    missing = await alerts.scope_todo(bot, KST_NOON, probe=False)
    assert missing == [f"스레드 {THREADS_READ_REPLIES}"]
    assert bot.alerts[-1]["key"] == "sns_scope" and "인스타·스레드 통계 권한 다시 연결 (/instaauth, /threadsauth)" in bot.alerts[-1]["kakao"]
    assert await alerts.scope_todo(bot, KST_NOON + timedelta(hours=2), probe=False) and len(bot.alerts) == 1  # 하루 한 번
    bot.db.kv_set(KV_TOKEN, "T2")  # 다시 연결 → 권한 모름 → 할 일 해제
    assert await alerts.scope_todo(bot, KST_NOON + timedelta(hours=3), probe=False) == [] and "sns_scope" in bot.resolved


def test_scope_state_resets_when_the_token_changes(db: Database) -> None:
    tok = {"v": "A"}
    st = ScopeState(db, "threads", lambda: tok["v"])
    assert st.status(THREADS_INSIGHTS) == "unknown" and st.usable(THREADS_INSIGHTS)
    st.mark(THREADS_INSIGHTS, False)
    st.mark(IG_INSIGHTS, True)
    assert not st.usable(THREADS_INSIGHTS) and st.granted(IG_INSIGHTS) and st.missing([THREADS_INSIGHTS]) == [THREADS_INSIGHTS]
    tok["v"] = "B"
    assert st.status(THREADS_INSIGHTS) == "unknown" and st.usable(THREADS_INSIGHTS)
    tok["v"] = None
    assert not st.usable(THREADS_INSIGHTS)
    assert "A" not in (db.kv_get("threads_scopes") or "")  # 토큰은 저장하지 않음 (지문만)


# ================================================================ 6. 투표 + 일요일 TOP5
async def test_poll_on_a_day_without_deal_posts(bot) -> None:  # type: ignore[no-untyped-def]
    morning = datetime(2026, 10, 15, 5, 0, tzinfo=UTC)  # 14:00 KST
    assert not await bot.threads_flow.maybe_poll(morning)
    afternoon = datetime(2026, 10, 15, 6, 30, tzinfo=UTC)  # 15:30 KST
    assert await bot.threads_flow.maybe_poll(afternoon)
    post = bot.fake.posts()[-1]
    assert post["text"] == "다음 주 뭐부터 찾아볼까?" and post["topic_tag"] == "살림" and "http" not in post["text"]
    assert len(json.loads(post["poll_attachment"])) == 4
    assert not await bot.threads_flow.maybe_poll(afternoon + timedelta(hours=1))  # 하루 한 번
    polls = bot.db.polls_since(morning)
    assert polls and polls[0]["question"] == "다음 주 뭐부터 찾아볼까?" and len(polls[0]["options"]) == 4


async def test_no_poll_when_a_deal_was_posted_today(bot) -> None:  # type: ignore[no-untyped-def]
    await bot._publish_side_channels(_deal("coupang:12"), photo=_jpeg(), deal_no=12)
    assert not await bot.threads_flow.maybe_poll(datetime(2026, 10, 15, 7, 0, tzinfo=UTC))


def test_poll_summary_reads_fraction_or_percent() -> None:
    raw = {"option_a": "주방·식품", "option_b": "반려동물", "option_a_votes_percentage": 0.25, "option_b_votes_percentage": 0.75,
           "total_votes": 8}
    assert poll_summary(["주방·식품", "반려동물"], raw) == {
        "options": [{"label": "반려동물", "pct": 75}, {"label": "주방·식품", "pct": 25}], "total": 8}
    assert poll_summary(["a", "b"], {"option_a_votes_percentage": 40, "option_b_votes_percentage": 60})["options"][0]["pct"] == 60


async def test_weekly_top5_and_poll_results_on_sunday(bot) -> None:  # type: ignore[no-untyped-def]
    sunday = datetime(2026, 10, 18, 11, 59, tzinfo=UTC)  # 일요일 20:59 KST (시간대 끝 — 고른 분이 지났음)
    for i, (shop, price, score) in enumerate((("coupang", 9900, 50), ("naver", 15900, 40), ("coupang", 4900, 30))):
        d = _deal(f"{shop}:w{i}", shop=shop, price=price, score=score, name=f"상품 {i}")
        assert bot.db.enqueue(d, score=score, now=sunday - timedelta(days=2))
        qid = max(it.id for it in bot.db.pending_items(10))
        bot.db.update_queue_item(qid, status="published", deal=d)
        bot.db.record_post(d, channel_id="-1001", message_id=i + 1, now=sunday - timedelta(days=2))
    bot.db.poll_add("777", "다음 주 뭐부터 찾아볼까?", ["주방·식품", "반려동물"], now=sunday - timedelta(days=3))
    bot.fake.poll = {"option_a_votes_percentage": 0.25, "option_b_votes_percentage": 0.75, "total_votes": 12}
    posted: list[str] = []

    async def publish_raw(text: str, **kw: Any) -> PublishResult:
        posted.append(text)
        return PublishResult(ok=True, message_id=5)

    bot.publisher.publish_raw = publish_raw
    assert await weekly.weekly_top(bot, sunday - timedelta(days=1)) is None  # 토요일엔 안 올림
    text = await weekly.weekly_top(bot, sunday)
    assert text and posted == [text]
    lines = text.splitlines()
    assert lines[0] == "📊 <b>이번 주 TOP3 + 투표 결과</b>"
    assert lines[1] == "광고 · 쿠팡 파트너스·네이버 쇼핑 커넥트 수수료"  # 둘째 줄 짧은 광고 표시
    assert "1. <b>상품 0</b> — 9,900원 (10/16 올린 가격)" in text and text.index("상품 0") < text.index("상품 1") < text.index("상품 2")
    assert "가격·재고는 올린 때 기준이라 지금은 바뀌었을 수 있어요." in text
    assert "참여 12명" in text and "1위 반려동물 75%" in text and "2위 주방·식품 25%" in text
    assert text.rstrip().endswith("</i>") and "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로" in text
    assert await weekly.weekly_top(bot, sunday) is None  # 같은 날 두 번 안 올림


async def test_weekly_skips_when_there_is_nothing(bot) -> None:  # type: ignore[no-untyped-def]
    sent: list[str] = []

    async def publish_raw(text: str, **kw: Any) -> PublishResult:
        sent.append(text)
        return PublishResult(ok=True)

    bot.publisher.publish_raw = publish_raw
    assert await weekly.weekly_top(bot, datetime(2026, 10, 18, 11, 59, tzinfo=UTC)) is None and sent == []


def test_daily_slot_is_a_random_minute_inside_the_window(db: Database) -> None:
    day = datetime(2026, 10, 15).date()
    t = slot_time("threads_replies_0", day, "12:30-12:59", "Asia/Seoul")
    local = t.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul"))
    assert local.hour == 12 and 30 <= local.minute <= 59
    assert slot_time("threads_replies_0", day, "12:30-12:59", "Asia/Seoul") == t  # 재시작해도 같은 분
    assert not slot_due(db, "k", "12:30-12:59", datetime(2026, 10, 15, 3, 29, tzinfo=UTC), "Asia/Seoul")  # 12:29
    assert slot_due(db, "k", "12:30-12:59", datetime(2026, 10, 15, 3, 59, tzinfo=UTC), "Asia/Seoul")  # 12:59
    assert not slot_due(db, "k", "12:30-12:59", datetime(2026, 10, 15, 4, 5, tzinfo=UTC), "Asia/Seoul")  # 지나면 건너뜀


async def test_sns_tick_runs_reply_collection_twice_a_day(bot) -> None:  # type: ignore[no-untyped-def]
    ran: list[str] = []

    async def collect(now: datetime | None = None, *, slot: str = "") -> int:
        ran.append(slot)
        return 0

    bot.threads_flow.collect_replies = collect
    for minute in range(0, 60):
        _at(bot, datetime(2026, 10, 15, 3, minute, tzinfo=UTC))  # 12:00~12:59 KST
        await bot.sns_tick()
    for minute in range(0, 60):
        _at(bot, datetime(2026, 10, 15, 11, minute, tzinfo=UTC))  # 20:00~20:59 KST
        await bot.sns_tick()
    assert ran == ["0", "1"]


# ================================================================ 7. 핫딜 페이지 · 채널 안내 · 초대 링크
def _publish(bot, pid: str, when: datetime, name: str = "상품") -> int:  # type: ignore[no-untyped-def]
    d = _deal(pid, name=name)
    assert bot.db.enqueue(d, score=10, now=when)
    qid = max(it.id for it in bot.db.pending_items(10))
    bot.db.update_queue_item(qid, status="published", deal=d, now=when)
    bot.db.record_post(d, channel_id="-1001", message_id=qid, now=when)
    return qid


async def test_deals_page_shows_past_deals_with_a_note_ad_line_and_channel_links(bot) -> None:  # type: ignore[no-untyped-def]
    bot.state.dry_run = False
    _publish(bot, "coupang:p1", datetime.now(UTC) - timedelta(days=4), name="지난 주 텀블러")
    bot.db.kv_set("invite_link:deals_page", "https://t.me/+DEALSPAGE")
    status, html_text = await bot._deals_page({})
    assert status == 200 and "지난 주 텀블러" in html_text
    assert "지난 딜 · 가격이 바뀌었을 수 있어요" in html_text and "최근 7일 동안 올라온 딜" in html_text
    assert html_text.index('class="ad"') < html_text.index("<h1>")  # 맨 위 광고 표시
    assert "광고 · 이 페이지의 구매 링크는 제휴 링크라, 구매하면 쿠팡 파트너스 수수료를 받아요" in html_text
    assert 'href="https://t.me/+DEALSPAGE"' in html_text  # 텔레그램 버튼 = deals_page 초대 링크
    assert "https://www.instagram.com/oneul_hot_deal" in html_text and "https://www.threads.net/@oneul_hot_deal" in html_text
    _publish(bot, "coupang:p2", datetime.now(UTC) - timedelta(hours=1), name="오늘 딜")
    _, now_page = await bot._deals_page({})
    assert "오늘 딜" in now_page and "지난 딜 ·" not in now_page and "지난 주 텀블러" not in now_page


class FakeTgBot:
    def __init__(self, *, refuse: bool = False, pin_fails: bool = False) -> None:
        self.refuse = refuse
        self.pin_fails = pin_fails
        self.links: list[str] = []
        self.pins: list[int] = []

    async def create_chat_invite_link(self, chat_id: Any, name: str) -> SimpleNamespace:
        if self.refuse:
            raise RuntimeError("Not enough rights to manage chat invite link")
        self.links.append(name)
        return SimpleNamespace(invite_link=f"https://t.me/+{name.upper()}", name=name)

    async def pin_chat_message(self, chat_id: Any, message_id: int, disable_notification: bool = False) -> None:
        if self.pin_fails:
            raise RuntimeError("not enough rights to pin a message")
        self.pins.append(message_id)


async def test_invite_links_are_created_once_and_explained(bot) -> None:  # type: ignore[no-untyped-def]
    tg = FakeTgBot()
    bot.bot = tg
    bot.publisher.channel_id = -1001
    text = await bot.invite_links()
    assert tg.links == list(invites.INVITE_NAMES) and "https://t.me/+IG_BIO" in text and "출처 모름" in text
    assert await bot.invite_links() and tg.links == list(invites.INVITE_NAMES)  # 두 번째는 기억한 것
    bot.db.kv_set("invite_link:qr", "")
    refused = FakeTgBot(refuse=True)
    bot.bot = refused
    msg = await bot.invite_links()
    assert "링크로 초대" in msg and "/invitelinks" in msg and "https://t.me/+IG_BIO" in msg


async def test_invite_links_are_handed_to_blogauto_as_channel_links_json(bot) -> None:  # type: ignore[no-untyped-def]
    """/invitelinks 뒤 데이터 폴더(published_deals.jsonl 옆)의 channel_links.json 에 이름별 링크 (주인 10/16 — 블로그 서명용)."""
    bot.bot = FakeTgBot()
    bot.publisher.channel_id = -1001
    bot.db.kv_set("invite_link:other_place", "https://t.me/+OTHERPLACE1")  # INVITE_NAMES 밖: 안 넘김
    await bot.invite_links()
    path = bot.settings.data_dir / invites.EXPORT_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"v", "telegram_public", "invites", "updated_at"}
    assert data["invites"] == {n: f"https://t.me/+{n.upper()}" for n in invites.INVITE_NAMES}
    assert data["telegram_public"] == bot.settings.channels.telegram_url
    assert datetime.fromisoformat(data["updated_at"]).tzinfo is not None
    assert oct(path.stat().st_mode & 0o777) == "0o600"  # 초대 링크는 주인만
    assert not [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]  # 임시 파일 안 남김


def test_channel_links_export_keeps_only_telegram_links(bot) -> None:  # type: ignore[no-untyped-def]
    bot.db.kv_set("invite_link:demiyum", "https://t.me/+DemiyumFake123")
    bot.db.kv_set("invite_link:qr", "http://t.me/+NOT_HTTPS")
    bot.db.kv_set("invite_link:ig_bio", "https://evil.example/+x")
    bot.db.kv_set("invite_link:threads_bio", "https://t.me/+bad link")
    path = bot.export_channel_links()  # 켜질 때 (run_forever) 부르는 것
    assert path == bot.settings.data_dir / "channel_links.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["invites"] == {"demiyum": "https://t.me/+DemiyumFake123"}
    assert invites.export_links(bot.db, bot.settings.data_dir, "javascript:alert(1)") is not None
    assert json.loads(path.read_text(encoding="utf-8"))["telegram_public"] == ""  # 텔레그램 주소가 아니면 비움


def test_channel_links_export_is_atomic(bot, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """바꿔치기에 실패하면 예전 파일이 그대로 남고 (반쯤 쓴 파일 없음) 봇은 계속 돈다."""
    bot.db.kv_set("invite_link:demiyum", "https://t.me/+OldLink12345")
    path = bot.export_channel_links()
    before = path.read_text(encoding="utf-8")
    bot.db.kv_set("invite_link:demiyum", "https://t.me/+NewLink12345")

    def boom(*a: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(invites.os, "replace", boom)
    assert bot.export_channel_links() is None
    assert path.read_text(encoding="utf-8") == before
    assert not [p.name for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def _member_update(chat_id: int, old: str, new: str, link: Any = None) -> SimpleNamespace:
    return SimpleNamespace(chat_member=SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, username=None), old_chat_member=SimpleNamespace(status=old),
        new_chat_member=SimpleNamespace(status=new), invite_link=link))


async def test_joins_are_counted_per_invite_link(bot, db: Database) -> None:  # type: ignore[no-untyped-def]
    bot.publisher.channel_id = -1001
    bot.db.kv_set("invite_link:threads_bio", "https://t.me/+TH")
    for u in (
        _member_update(-1001, "left", "member", SimpleNamespace(name="ig_bio", invite_link="https://t.me/+IG")),
        _member_update(-1001, "left", "member", SimpleNamespace(name="", invite_link="https://t.me/+TH")),  # 이름 없이 주소로
        _member_update(-1001, "left", "member"),  # 공개 주소(@채널)로
        _member_update(-1001, "member", "left"),  # 나감: 안 셈
        _member_update(-999, "left", "member"),  # 다른 채팅: 안 셈
    ):
        await bot._on_chat_member(u, None)
    today = bot.clock().astimezone(__import__("zoneinfo").ZoneInfo("Asia/Seoul")).date()
    assert bot.db.invite_joins_since(today.isoformat()) == {"ig_bio": 1, "threads_bio": 1, "unknown": 1}
    total, parts = invites.joins_summary(bot.db, today)
    assert total == 3 and "인스타 프로필 1" in parts and "출처 모름 1" in parts


async def test_channel_guide_posts_and_pins_only_on_command(bot) -> None:  # type: ignore[no-untyped-def]
    tg = FakeTgBot()
    bot.bot = tg
    bot.publisher.channel_id = -1001
    bot.publisher.dry_run = False
    posted: list[str] = []

    async def publish_raw(text: str, **kw: Any) -> PublishResult:
        posted.append(text)
        return PublishResult(ok=True, message_id=321)

    bot.publisher.publish_raw = publish_raw
    msg = await bot.channel_guide()
    assert "고정했어요" in msg and tg.pins == [321]
    text = posted[0]
    for url in ("https://www.instagram.com/oneul_hot_deal", "https://www.threads.net/@oneul_hot_deal", "https://blog.naver.com/demiyum",
                "https://bot.example.com/"):
        assert url in text, url
    assert "제휴 링크" in text and "(광고)" in text
    bot.bot = FakeTgBot(pin_fails=True)
    assert "고정하지 못했어요" in await bot.channel_guide()


# ================================================================ 8. 인스타·스레드 24시간 0개 알림
async def test_sns_zero_alert_with_reasons(bot) -> None:  # type: ignore[no-untyped-def]
    bot.settings.instagram.enabled = True
    bot.instagram.dry_run = True
    start = KST_NOON - timedelta(hours=30)
    assert not await alerts.sns_zero_check(bot, start)  # 기록 시작 표시만
    bot.state.dry_run = False
    _publish(bot, "coupang:z1", KST_NOON - timedelta(hours=2))
    bot.db.sns_log_add("instagram", "skipped", product_id="coupang:z1", reason="확인된 상품 사진 없음", now=KST_NOON - timedelta(hours=2))
    bot.db.sns_log_add("threads", "posted", product_id="coupang:z1", now=KST_NOON - timedelta(hours=2))
    assert await alerts.sns_zero_check(bot, KST_NOON)
    a = bot.alerts[-1]
    assert a["key"] == "sns_zero" and "인스타 0개 (확인된 사진 없음 1)" in a["kakao"] and "스레드 0개" not in a["kakao"]
    assert not await alerts.sns_zero_check(bot, KST_NOON + timedelta(hours=1))  # 하루 한 번


# ================================================================ 9. 사진 나중에 받기 + 블로그 내보내기 약속
async def test_late_photo_contract(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:lp", name="데켓 주물팬 28cm")
    qid = _publish(bot, "coupang:lp", KST_NOON, name="데켓 주물팬 28cm")
    row = bot.export_published(d, None)
    assert row["photo_kind"] == "text_card" and row["photo_file"] == "" and Path(row["text_card_file"]).is_file()
    posted = datetime.fromisoformat(row["posted_at"])
    assert datetime.fromisoformat(row["photo_wait_until"]) == posted + timedelta(hours=4)
    await bot._ask_late_photo(qid, d, None, row)
    text, silent = bot.sent[-1]
    assert silent and text == f"📷 사진 필요 #{qid} 데켓 주물팬 28cm — 4시간 안에 이 메시지에 사진을 답장하면 블로그·인스타에 그 사진을 써요"
    assert bot.db.admin_msg(len(bot.sent))["kind"] == "photo_need" and bot.alerts[-1]["key"] == f"photo:{qid}"
    assert bot.late_photo_waiting(qid)

    async def load() -> tuple[bytes | None, str]:
        return _jpeg(1000, 800), ""

    _at(bot, posted + timedelta(hours=2))
    reply = await bot.attach_late_photo(qid, load)
    assert reply.startswith(f"✅ #{qid} 사진을 받았어요")
    rows = [json.loads(x) for x in (bot.settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").splitlines()]
    last = rows[-1]
    assert last["product_id"] == "coupang:lp" and last["posted_at"] == row["posted_at"]  # 같은 상품·같은 발행 시각
    assert last["photo_kind"] == "owner" and Path(last["photo_file"]).is_file() and last["photo_wait_until"] is None
    assert last["text_card_file"] == "" and last["photo_updated_at"]
    assert bot.db.get_queue_item(qid).deal.product.extra["owner_photo"]["late"] is True  # 인스타가 나중에 쓰면 이 사진
    assert f"photo:{qid}" in bot.resolved and not bot.late_photo_waiting(qid)
    assert "사진을 기다리는 딜이 아니에요" in await bot.attach_late_photo(qid, load)  # 한 번만


async def test_late_photo_after_the_window_or_bad_photo(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:lp2")
    qid = _publish(bot, "coupang:lp2", KST_NOON)
    row = bot.export_published(d, None)
    await bot._ask_late_photo(qid, d, None, row)

    async def tiny() -> tuple[bytes | None, str]:
        return _jpeg(200, 200), ""

    assert "쓸 수 없어요" in await bot.attach_late_photo(qid, tiny)  # 같은 검사 (짧은 변 600px)
    _at(bot, KST_NOON + timedelta(hours=5))
    assert "4시간이 지나" in await bot.attach_late_photo(qid, tiny)
    with_photo = bot.export_published(_deal("coupang:lp3"), _jpeg())
    assert with_photo["photo_kind"] == "verified" and with_photo["photo_wait_until"] is None and with_photo["text_card_file"] == ""
    await bot._ask_late_photo(99, _deal("coupang:lp3"), _jpeg(), with_photo)
    assert not bot.late_photo_waiting(99)  # 사진이 있으면 묻지 않음


async def test_process_queue_asks_for_a_photo_after_a_photoless_publish(bot) -> None:  # type: ignore[no-untyped-def]
    bot.state.dry_run = False
    bot.publisher.dry_run = False
    bot.settings.publish.min_interval_seconds = 0

    async def fake_publish(deal: Deal, **kw: Any) -> PublishResult:
        return PublishResult(ok=True, message_id=1)

    bot.publisher.publish = fake_publish
    d = _deal("coupang:q1", name="무선 청소기")
    assert bot.db.enqueue(d, score=40)
    assert await bot.process_queue_once()
    assert any(t.startswith("📷 사진 필요 #") for t, _ in bot.sent)
    row = json.loads((bot.settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["photo_kind"] == "text_card" and row["photo_wait_until"]


# ================================================================ 10. 내 도메인 DNS
async def test_domain_check_alerts_when_dns_does_not_point_here(bot) -> None:  # type: ignore[no-untyped-def]
    sec = bot.settings.secrets
    sec.public_domain = "deals.example.com"
    sec.public_domain_aliases = ["3-35-1-2.sslip.io"]

    async def here(host: str) -> list[str]:
        return ["3.35.1.2"]

    async def elsewhere(host: str) -> list[str]:
        return ["9.9.9.9"]

    assert await alerts.domain_check(bot, here) == (True, "내 도메인 deals.example.com → 이 서버 (3.35.1.2)")
    ok, line = await alerts.domain_check(bot, elsewhere, alert=False)
    assert ok is None and "9.9.9.9" in line and bot.alerts == []
    await alerts.domain_check(bot, elsewhere)
    assert bot.alerts[-1]["key"] == "domain_dns" and "3.35.1.2" in bot.alerts[-1]["kakao"]
    sec.public_domain = "3-35-1-2.sslip.io"
    assert await alerts.domain_check(bot, elsewhere) is None  # sslip.io 는 확인할 것 없음


def test_domain_aliases_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.config import load_settings

    monkeypatch.setenv("DEALBOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_DOMAIN", "deals.example.com")
    monkeypatch.setenv("PUBLIC_DOMAIN_ALIASES", "3-35-1-2.sslip.io, old.example.org")
    monkeypatch.setenv("THREADS_REDIRECT_URI", "https://3-35-1-2.sslip.io/threads/callback")
    s = load_settings(Path(__file__).resolve().parent.parent / "config.yaml", load_env=False)
    assert s.secrets.public_domain_aliases == ["3-35-1-2.sslip.io", "old.example.org"]
    assert s.secrets.threads_callback_served  # 예전 sslip.io 주소로 등록한 리디렉션도 봇이 직접 받음
    assert s.secrets.public_base_url == "https://deals.example.com"  # 카드·핫딜 페이지 주소는 내 도메인
    assert s.secrets.instagram_redirect_uri == "https://deals.example.com/instagram/callback"
    monkeypatch.setenv("PUBLIC_DOMAIN_ALIASES", "")
    assert not load_settings(Path(__file__).resolve().parent.parent / "config.yaml", load_env=False).secrets.threads_callback_served


async def test_failed_threads_post_backs_off_before_the_next(bot) -> None:  # type: ignore[no-untyped-def]
    """스레드가 막혔을 때 밀린 글이 한꺼번에 실패(알림 폭주)하지 않게 실패 뒤 10분 쉰다."""
    sent: list[str] = []

    async def send(text: str, *, silent: bool = False) -> bool:
        sent.append(text)
        return True

    bot.notifier.send = send
    bot.settings.threads.rhythm.min_gap_minutes = 0
    _at(bot, datetime(2026, 10, 14, 18, 0, tzinfo=UTC))  # 03:00 KST: 둘 다 기다림
    for i in (1, 2):
        await bot._publish_side_channels(_deal(f"coupang:f{i}"), photo=_jpeg(), deal_no=i)

    async def down(deal: Deal, **_kw: Any) -> PublishResult:
        return PublishResult(ok=False, error="threads api 500: upstream")

    bot.threads.publish = down
    morning = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)
    assert await bot.threads_flow.tick(morning)
    assert not await bot.threads_flow.tick(morning + timedelta(minutes=1))  # 쉼
    assert len([t for t in sent if "스레드 발행 실패" in t]) == 1 and len(bot.db.threads_waiting()) == 1
    assert await bot.threads_flow.tick(morning + timedelta(minutes=11))


async def test_rhythm_off_posts_right_after_telegram(bot) -> None:  # type: ignore[no-untyped-def]
    bot.settings.threads.rhythm.enabled = False
    _at(bot, datetime(2026, 10, 14, 18, 0, tzinfo=UTC))  # 03:00 KST 여도 바로 (예전 동작)
    await bot._publish_side_channels(_deal("coupang:r1"), photo=_jpeg(), deal_no=1)
    assert len(bot.fake.posts()) == 1 and not bot.db.threads_waiting()


async def test_late_photo_records_are_pruned(bot) -> None:  # type: ignore[no-untyped-def]
    d = _deal("coupang:pr")
    qid = _publish(bot, "coupang:pr", KST_NOON)
    await bot._ask_late_photo(qid, d, None, bot.export_published(d, None))
    assert bot.late_photo.prune(KST_NOON + timedelta(hours=5)) == 0  # 지난 지 하루 안이면 둠
    assert bot.late_photo.prune(KST_NOON + timedelta(days=2)) == 1 and not bot.late_photo_waiting(qid)


# ================================================================ 11. 검토 뒤 고침
def _capture_specs(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from dealbot.media import card_slides

    specs: list[Any] = []
    render = card_slides.render

    def spy(card: Any, spec: Any, layout: str | None = None) -> Any:
        specs.append(spec)
        return render(card, spec, layout)

    monkeypatch.setattr(card_slides, "render", spy)
    return specs


async def test_blog_export_and_threads_cards_use_a_channel_neutral_footer(bot, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """블로그로 내보내는 글자 카드·스레드 글자 카드에 인스타 전용 '구매 링크는 프로필 링크에서' 가 그려지지 않는다
    (블로그·스레드엔 구매 링크가 글 안에 있다). 고지 띠는 그대로. 인스타 카드는 예전처럼 프로필 링크 안내."""
    from dealbot.media.card import NEUTRAL_CTA

    bot.card.layout = "B"
    specs = _capture_specs(monkeypatch)
    d = _deal("coupang:ft", name="데켓 주물팬 28cm")
    row = bot.export_published(d, None)
    assert row["text_card_file"] and len(specs) == 1
    blog = specs[0]
    assert blog.footer == NEUTRAL_CTA and blog.ad.startswith("광고 · ")
    assert not any("프로필" in t for t in blog.texts())
    await bot._publish_side_channels(_deal("coupang:th", name="크리넥스 30롤"), photo=None, deal_no=5)  # 스레드 글자 카드
    assert bot.fake.posts()[-1]["media_type"] == "IMAGE"
    th = specs[-1]
    assert th.footer == NEUTRAL_CTA and not any("프로필" in t for t in th.texts()) and th.ad.startswith("광고 · ")
    # 스레드 글자 카드 위 띠엔 고지만 — 핫딜 페이지 번호 '#5' 는 안 그림 (스레드 독자는 본문의 링크로 감)
    assert th.tag == "" and not any(t.startswith("#") for t in th.texts())
    await bot.make_card(_deal("coupang:th2"), None, deal_no=6, text_card=True, cta=NEUTRAL_CTA)  # 번호를 넘겨도 채널 중립 카드엔 없음
    assert specs[-1].tag == "" and specs[-1].ad.startswith("광고 · ")
    await bot.make_card(_deal("coupang:ig"), None, deal_no=7)  # 인스타 카드
    assert specs[-1].footer == "구매 링크: 프로필 링크 → 핫딜 페이지 #7" and specs[-1].tag == "#7"
    paths = {p.name for p in (bot.settings.data_dir / "media").glob("*.jpg")}
    assert len(paths) == 3  # 스레드·인스타 카드는 파일도 따로 (같은 상품이어도 덮어쓰지 않음)


def test_current_layout_card_takes_the_neutral_footer(tmp_path: Path) -> None:
    from dealbot.media.card import NEUTRAL_CTA, DealCard

    card = DealCard(tmp_path)
    d = _deal("coupang:cur")
    insta = card.render(d, None)
    neutral = card.render(d, None, cta=NEUTRAL_CTA)
    assert insta != neutral  # 바닥 안내가 다름 (current 양식도)


async def test_threads_job_cut_off_while_posting_is_not_posted_twice(bot) -> None:  # type: ignore[no-untyped-def]
    """스레드 글도 API 를 부르기 전에 'posting' 으로 — 올리는 사이에 꺼졌다 켜지면 다시 올리지 않고 실패('올라갔을 수도')로 정리."""
    from dealbot.growth.threads_flow import ThreadsFlow

    _at(bot, datetime(2026, 10, 14, 18, 0, tzinfo=UTC))  # 03:00 KST: 기다림
    await bot._publish_side_channels(_deal("coupang:c1"), photo=_jpeg(), deal_no=1)
    job = bot.db.threads_waiting()[0]
    assert bot.db.threads_claim(job.id)  # 올리던 중 (재시작 전 프로세스)
    assert not bot.db.threads_claim(job.id)  # 두 번 맡지 않음
    assert not bot.db.threads_waiting()
    await bot._publish_side_channels(_deal("coupang:c1"), photo=_jpeg(), deal_no=1)
    assert not bot.db.threads_waiting()  # 올리는 중인 상품은 다시 줄 서지 않음
    bot.threads_flow = ThreadsFlow(bot)  # 재시작
    morning = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)
    assert not await bot.threads_flow.tick(morning)
    assert bot.fake.posts() == [] and bot.db.threads_job(job.id).status == "failed"
    assert "maybe posted" in (bot.db.threads_job(job.id).reason or "")
    logged = bot.db.sns_log_since("threads", morning - timedelta(days=1))
    assert [(r["outcome"], r["product_id"]) for r in logged] == [("failed", "coupang:c1")]


async def test_threads_tick_does_not_fail_a_post_this_process_is_making(bot) -> None:  # type: ignore[no-untyped-def]
    _at(bot, datetime(2026, 10, 14, 18, 0, tzinfo=UTC))
    await bot._publish_side_channels(_deal("coupang:c2"), photo=_jpeg(), deal_no=2)
    job = bot.db.threads_waiting()[0]
    seen: list[str] = []
    publish = bot.threads.publish

    async def slow(deal: Deal, **kw: Any) -> PublishResult:
        seen.append(bot.db.threads_job(job.id).status)
        assert bot.db.threads_fail_stale_posting(bot.threads_flow._posting) == []  # 다른 tick 이 이 사이에 돌아도
        return await publish(deal, **kw)

    bot.threads.publish = slow
    _at(bot, datetime(2026, 10, 14, 22, 0, tzinfo=UTC))
    assert await bot.threads_flow.tick()
    assert seen == ["posting"] and bot.db.threads_job(job.id).status == "posted" and len(bot.fake.posts()) == 1


async def test_weekly_top_leaves_out_deals_marked_sold_out(bot) -> None:  # type: ignore[no-untyped-def]
    """일요일 텔레그램 TOP5 도 채널에 품절로 표시한 딜에 '구매하러 가기' 를 달지 않는다 (인스타 TOP5 와 같은 규칙)."""
    sunday = datetime(2026, 10, 18, 11, 59, tzinfo=UTC)
    for i, pid in enumerate(("coupang:s0", "coupang:s1", "coupang:s2")):
        d = _deal(pid, score=50 - i, name=f"상품 {i}")
        assert bot.db.enqueue(d, score=50 - i, now=sunday - timedelta(days=2))
        qid = max(it.id for it in bot.db.pending_items(10))
        bot.db.update_queue_item(qid, status="published", deal=d)
        bot.db.record_post(d, channel_id="-1001", message_id=i + 1, now=sunday - timedelta(days=2))
    bot.db.kv_set("soldout_marked:coupang:s0", (sunday - timedelta(days=1)).isoformat())  # 채널 글에 품절 표시
    bot.mark_sold_out_seen("coupang:s2", sunday - timedelta(hours=3))  # 스레드가 올리기 직전에 품절을 봄
    ctx = weekly.weekly_context(bot, sunday)
    assert ctx is not None and [x["name"] for x in ctx["deals"]] == ["상품 1"]


async def test_late_photo_window_closes_a_little_early(bot) -> None:  # type: ignore[no-untyped-def]
    """Blog-Auto 는 photo_wait_until 이 지나면 글자 카드로 넘어간다 — 사진 받기(내려받기·검사·내보내기)가 그 뒤에 끝나
    '블로그에 써요' 라고 해 놓고 못 쓰는 일이 없게 받는 창을 조금 일찍 닫고, 받는 사이에 시간이 지나면 받지 않는다."""
    from dealbot.growth.late_photo import CLOSE_EARLY

    d = _deal("coupang:edge")
    qid = _publish(bot, "coupang:edge", KST_NOON)
    row = bot.export_published(d, None)
    await bot._ask_late_photo(qid, d, None, row)
    until = datetime.fromisoformat(row["photo_wait_until"])
    _at(bot, until - CLOSE_EARLY - timedelta(seconds=1))
    assert bot.late_photo_waiting(qid)
    _at(bot, until - timedelta(seconds=30))
    assert not bot.late_photo_waiting(qid)

    async def load() -> tuple[bytes | None, str]:
        return _jpeg(1000, 800), ""

    assert "시간이 지나" in await bot.attach_late_photo(qid, load)
    times = iter([until - CLOSE_EARLY - timedelta(seconds=5), until + timedelta(seconds=1)])
    bot.clock = lambda: next(times, until + timedelta(seconds=1))  # 받는(검사) 사이에 기다림이 끝남

    rows_before = (bot.settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").count("\n")
    assert "시간이 지나" in await bot.attach_late_photo(qid, load)
    assert (bot.settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").count("\n") == rows_before


async def test_auth_links_offer_a_basic_scope_fallback(bot) -> None:  # type: ignore[no-untyped-def]
    """새 권한(통계·답글 읽기)을 메타 앱에 아직 안 넣어 'Invalid Scopes' 가 떠도 다시 연결할 수 있게 예전 권한 링크도 같이 준다."""
    from urllib.parse import parse_qs, urlsplit

    sec = bot.settings.secrets
    sec.threads_app_id, sec.threads_app_secret = "TAPP", "TS"
    sec.instagram_app_id, sec.instagram_app_secret = "IAPP", "IS"
    bot.db.kv_set(KV_TOKEN, "")
    for msg, full, basic in ((await bot.threads_auth_url(), th_mod.SCOPES, th_mod.BASIC_SCOPES),
                             (await bot.instagram_auth_url(), ig_mod.SCOPES, ig_mod.BASIC_SCOPES)):
        links = [ln for ln in msg.splitlines() if ln.startswith("https://") and "oauth/authorize" in ln]
        assert len(links) == 2, msg
        scopes = [parse_qs(urlsplit(link).query)["scope"][0] for link in links]
        assert scopes == [full, basic]
        assert set(basic.split(",")) < set(full.split(","))
        assert "insights" not in basic and "read_replies" not in basic


async def test_auth_links_are_given_even_when_already_connected(bot) -> None:  # type: ignore[no-untyped-def]
    """주인 10/15: 이미 연결된 상태에서 /instaauth·/threadsauth 를 보내면 '이미 연결'만 와서 새 권한을 받을 수 없었다 —
    연결돼 있어도 다시 승인할 링크를 같이 준다."""
    from types import SimpleNamespace

    sec = bot.settings.secrets
    sec.threads_app_id, sec.threads_app_secret = "TAPP", "TS"
    sec.instagram_app_id, sec.instagram_app_secret = "IAPP", "IS"

    async def me(_token: str) -> dict:
        return {"username": "oneul_hot_deal"}

    for svc in (bot.threads, bot.instagram):
        svc.stored_token = lambda: SimpleNamespace(access_token="tok")  # type: ignore[method-assign]
        svc.client.me = me  # type: ignore[method-assign]
    for msg in (await bot.threads_auth_url(), await bot.instagram_auth_url()):
        assert msg.startswith("이미 연결되어 있어요: @oneul_hot_deal") and "한 번 더 승인" in msg
        assert sum(1 for ln in msg.splitlines() if ln.startswith("https://") and "oauth/authorize" in ln) == 2


async def test_auth_url_says_nothing_to_do_when_new_scopes_are_already_granted(bot) -> None:  # type: ignore[no-untyped-def]
    """주인 10/16: 스레드 토큰은 이미 통계·답글 권한이 있었는데 재승인을 시켜 'API access blocked' 만 받았다 —
    지금 토큰이 새 권한까지 갖고 있으면 링크 대신 '다시 승인할 필요 없음'만 알려 준다."""
    from types import SimpleNamespace

    from dealbot.publisher import instagram as ig_mod
    from dealbot.publisher import threads as th_mod
    from dealbot.publisher.scopes import IG_INSIGHTS, THREADS_INSIGHTS, THREADS_READ_REPLIES

    sec = bot.settings.secrets
    sec.threads_app_id, sec.threads_app_secret = "TAPP", "TS"
    sec.instagram_app_id, sec.instagram_app_secret = "IAPP", "IS"

    async def me(_token: str) -> dict:
        return {"username": "oneul_hot_deal"}

    for svc, kv in ((bot.threads, th_mod.KV_TOKEN), (bot.instagram, ig_mod.KV_TOKEN)):
        bot.db.kv_set(kv, "tok")
        svc.stored_token = lambda: SimpleNamespace(access_token="tok")  # type: ignore[method-assign]
        svc.client.me = me  # type: ignore[method-assign]
    bot.threads.scopes.mark(THREADS_INSIGHTS, True)
    msg = await bot.threads_auth_url()
    assert "한 번 더 승인" in msg, "한 권한만 있으면 아직 링크를 준다"
    bot.threads.scopes.mark(THREADS_READ_REPLIES, True)
    bot.instagram.scopes.mark(IG_INSIGHTS, True)
    for msg in (await bot.threads_auth_url(), await bot.instagram_auth_url()):
        assert msg.startswith("✅") and "다시 승인할 필요 없어요" in msg and "oauth/authorize" not in msg


async def test_blocked_code_exchange_keeps_old_token_and_points_to_token_generator(bot) -> None:  # type: ignore[no-untyped-def]
    """메타가 code 교환을 'API access blocked'(code 200) 로 막으면: 지금 토큰은 그대로, 실패는 기록, 토큰 생성기 안내."""
    import httpx

    from dealbot.publisher import threads as th_mod

    sec = bot.settings.secrets
    sec.threads_app_id, sec.threads_app_secret = "TAPP", "TS"
    bot.db.kv_set(th_mod.KV_TOKEN, "OLD")

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "API access blocked.", "type": "OAuthException", "code": 200}})

    bot.threads.client = th_mod.ThreadsClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), app_id="TAPP", app_secret="TS", max_retries=1, retry_backoff=0
    )
    msg = await bot.threads_submit_code("CODE")
    assert msg.startswith("❌") and "(oauth_code)" in msg
    assert "/threadstoken" in msg and "사용자 토큰 생성기" in msg
    assert bot.db.kv_get(th_mod.KV_TOKEN) == "OLD"
