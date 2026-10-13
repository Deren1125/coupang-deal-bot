"""주인 알림 (10/10 → 10/11 결정): 주인이 해야 할 일은 카카오톡 '나에게 보내기', 관리자 챗은 소리 없이.

카톡 토큰은 Blog-Auto 가 가지고 있어서, 딜봇은 Blog-Auto 받은편지함(blogauto_inbox.jsonl)에 {"owner_notice": {"v": 2, …}} 한 줄을
남기고 Blog-Auto 데몬이 카톡으로 보낸다 (같은 키는 한 번 · 모아서 · 주인이 처리하면 resolve 줄로 취소).
Blog-Auto 생존 신호(blogauto_status.json)가 오래됐거나 카톡이 안 되면 텔레그램으로 소리 내서 대신 알린다 (같은 일은 6시간에 한 번).
'상품 링크 완료'·'채널에 올렸어요' 카톡은 없애고 저녁 요약에만 (주인 결정 10/11).
"""

from __future__ import annotations

import json
import time

import pytest

from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product, PublishResult


@pytest.fixture
def bot(settings: Settings) -> DealBot:
    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


@pytest.fixture
def sent(bot: DealBot) -> list[tuple[str, bool]]:
    """관리자 챗에 보낸 글과 소리를 냈는지."""
    out: list[tuple[str, bool]] = []

    async def fake_send(text: str, *, silent: bool = False, loud: bool = False, **_kw: object) -> int:
        out.append((text, loud))
        return len(out)

    bot.notifier.send_with_id = fake_send  # type: ignore[method-assign]
    return out


def _inbox(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r["owner_notice"] for r in rows if "owner_notice" in r]


def _healthy(settings: Settings, ok: bool = True, age: float = 0) -> None:
    """Blog-Auto 생존 신호 (카톡이 되는지)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "blogauto_status.json").write_text(
        json.dumps({"at": int(time.time() - age), "kakao_ok": ok, "kakao_checked": 0, "outbox": 0}), encoding="utf-8")


def _product(**kw: object) -> Product:
    base: dict = dict(source="ppomppu", product_id="naver:13008954661", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm",
                      price=60020, url="https://brand.naver.com/daekket/products/13008954661")
    base.update(kw)
    return Product(**base)


def _queued(bot: DealBot, **kw: object):  # type: ignore[no-untyped-def]
    assert bot.db.enqueue(Deal(_product(**kw), DealVerdict(is_deal=True)), score=1)
    return bot.db.pending_items()[0]


async def test_link_request_goes_to_kakao_and_telegram_is_silent(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    item = _queued(bot, name="데켓 주물 IH 오발쿡플레이트 31cm " + "아주 긴 상품명 " * 20)
    await bot.notifier.notify_manual_link(item, bot.registry.get("naver"))
    assert len(sent) == 1 and sent[0][1] is False and f"#{item.id}" in sent[0][0]  # 답장할 자리는 텔레그램 (소리 없이)
    note = _inbox(settings)[-1]
    assert note["v"] == 2 and note["kind"] == "link_request" and note["key"] == f"link:{item.id}"
    assert note["group"] == "link_request" and note["short"] == f"핫딜 링크 #{item.id}" and note["urgent"] is False
    text = note["text"]
    assert len(text) <= 200 and text.startswith(f"🔔[할 일] 링크 필요 #{item.id} [") and "데켓 주물" in text
    assert text.endswith(f"→ 텔레그램 핫딜봇 #{item.id} 에 답장") and "http" not in text  # 주소 줄 없이 한 통


async def test_kakao_down_means_loud_telegram_once_per_key(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    item = _queued(bot)
    shop = bot.registry.get("naver")
    for state in ("missing", "stale", "kakao_off"):
        if state == "stale":
            _healthy(settings, age=20 * 60)
        elif state == "kakao_off":
            _healthy(settings, ok=False)
        bot.notifier._loud_sent.clear()
        await bot.notifier.notify_manual_link(item, shop)
        text, loud = sent[-1]
        assert loud and text.startswith("🔗") and text.endswith("(카톡 알림이 안 돼서 텔레그램으로 알려요)"), state
    await bot.notifier.notify_manual_link(item, shop)  # 같은 일은 6시간에 한 번만 소리
    assert sent[-1][1] is False
    assert len([n for n in _inbox(settings) if n.get("key") == f"link:{item.id}"]) == 4  # 카톡 줄은 그대로 (Blog-Auto 가 한 번만)


async def test_link_done_is_summary_only_and_cancels_the_kakao(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    item = _queued(bot)
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual")
    msg = await bot.attach_link(item.id, "https://naver.me/abc")
    assert "상품 링크 완료" in msg and "데켓 주물" in msg and "한 번 더 알려" not in msg
    notes = _inbox(settings)
    assert notes[-2]["resolve"] == [f"link:{item.id}"]
    assert notes[-1]["kind"] == "summary" and notes[-1]["cat"] == "link_done" and "데켓 주물" in notes[-1]["text"]
    assert not any(n.get("kind") == "link_done" for n in notes)  # '상품 링크 완료' 카톡은 없음 (주인 결정 10/11)
    assert bot.db.get_queue_item(item.id).deal.product.extra.get("owner_link") is True


async def test_owner_linked_publish_goes_to_summary_not_telegram(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    bot.notifier.cfg.notify_on_publish = False
    await bot.notifier.notify_published(Deal(_product(), DealVerdict(is_deal=True)), PublishResult(ok=True, message_id=1, dry_run=False))
    mine = Deal(_product(extra={"owner_link": True}), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(mine, PublishResult(ok=True, message_id=2, dry_run=False))
    assert sent == []  # '채널에 올렸어요' 는 텔레그램·카톡 없이
    notes = _inbox(settings)
    assert len(notes) == 1 and notes[0]["kind"] == "summary" and notes[0]["cat"] == "published" and "데켓 주물" in notes[0]["text"]


async def test_skip_resolves_pending_kakao(bot: DealBot, settings: Settings) -> None:
    _healthy(settings)
    item = _queued(bot)
    bot.skip_item(item.id)
    assert _inbox(settings)[-1]["resolve"] == [f"link:{item.id}", f"review:{item.id}"]


async def test_summary_and_resolve_lines_wait_for_a_blogauto_that_reads_them(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    """두 저장소는 따로 자동 업데이트된다: 딜봇이 먼저 바뀌고 예전 Blog-Auto(생존 신호 파일을 안 씀)가 돌면, 예전 판은 글이 든
    owner_notice 를 모두 바로 카톡으로 보내서 요약 줄이 상품 이름 한 줄짜리 카톡이 된다. 그 사이에는 요약·해결 줄을 남기지
    않는다 — 할 일 알림은 글 전체가 한 통이라 예전 판에도 그대로 남긴다 (텔레그램은 소리 내서)."""
    from dealbot.page_price import PageCheck

    item = _queued(bot)
    await bot.notifier.notify_price_hold(22, _product(price=9900), PageCheck(status="hold", title_price=9900, reason="price"))
    await bot.notifier.notify_manual_link(item, bot.registry.get("naver"))
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual")
    assert "상품 링크 완료" in await bot.attach_link(item.id, "https://naver.me/abc")
    await bot.notifier.notify_published(Deal(_product(extra={"owner_link": True}), DealVerdict(is_deal=True)),
                                        PublishResult(ok=True, message_id=2, dry_run=False))
    await bot.owner_summary()
    bot.skip_item(item.id)
    notes = _inbox(settings)
    assert [n["kind"] for n in notes] == ["link_request"] and notes[0]["text"].startswith("🔔[할 일] 링크 필요")
    assert not [n for n in notes if "resolve" in n or n.get("kind") == "summary"]
    assert [loud for text, loud in sent if text.startswith("🔗")] == [True]  # 카톡이 될지 몰라 텔레그램은 소리 내서

    _healthy(settings)  # 새 Blog-Auto 가 돌기 시작함 → 요약·해결 줄을 남긴다
    await bot.notifier.notify_price_hold(23, _product(price=9900), PageCheck(status="hold", title_price=9900, reason="price"))
    bot.notifier.resolve("link:99")
    tail = _inbox(settings)[len(notes):]
    assert [(n.get("cat"), n.get("resolve")) for n in tail] == [("held", None), (None, ["link:99"])]


async def test_reviews_are_kakao_alerts(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    item = _queued(bot)
    await bot.notifier.notify_deal_review(item, "네이버", "공식 판매처 확인 안 됨")
    await bot.notifier.notify_info_review(item, "본문", reason="요약 실패")
    a, b = _inbox(settings)[-2:]
    assert a["kind"] == "review_auth" and a["key"] == f"review:{item.id}" and f"/ok {item.id}" in a["text"]
    assert b["kind"] == "review_info" and b["text"].endswith(f"/ok {item.id} · /skip {item.id}")
    assert sent[-1][0].startswith("📝")  # 정보 글 확인은 첫 글자(📝)로 답장 대상을 알아본다 — 그대로


async def test_three_final_publish_failures_are_urgent(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    await bot.notifier.publish_failed_final("TimedOut")
    assert not [n for n in _inbox(settings) if n.get("kind") == "publish_broken"]  # 한 번은 요약에만
    await bot.notifier.publish_failed_final("TimedOut")
    await bot.notifier.publish_failed_final("TimedOut")
    note = [n for n in _inbox(settings) if n.get("kind") == "publish_broken"][-1]
    assert note["urgent"] is True and note["key"] == "publish_broken" and "3건 연속" in note["text"]
    await bot.notifier.notify_published(Deal(_product(), DealVerdict(is_deal=True)), PublishResult(ok=True, message_id=9, dry_run=False))
    assert bot.notifier._final_fails == []  # 한 번 올라가면 처음부터


async def test_unsure_publish_is_a_kakao_todo(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    """보낸 뒤 텔레그램 답만 시간 초과(올라갔는지 모름): 다시 보내지 않고 7일 동안 줄 세우지 않으니 주인이 채널을 확인해야 한다 —
    링크 요청처럼 카톡 할 일 한 통 (소리 없는 텔레그램 한 줄만으로는 묻혔다). 채널에 있으면 /posted, 없으면 /link."""
    _healthy(settings)
    item = _queued(bot)
    await bot._handle_publish_failure(item, "TimedOut: Timed out", maybe_posted=True)
    assert bot.db.get_queue_item(item.id).last_error == "maybe posted: TimedOut: Timed out"
    note = [n for n in _inbox(settings) if n.get("kind") == "publish_unsure"][-1]
    assert note["v"] == 2 and note["key"] == f"unsure:{item.id}" and note["short"] == f"채널 확인 #{item.id}"
    assert note["text"].startswith(f"🔔[할 일] 채널 확인 #{item.id} 데켓 주물") and len(note["text"]) <= 200
    assert f"/posted {item.id}" in note["text"] and f"/link {item.id}" in note["text"]
    text, loud = sent[-1]
    assert text.startswith(f"❓ <b>채널에 올라갔는지 모릅니다 #{item.id}") and not loud  # 답할 자리는 텔레그램 (소리 없이)
    assert f"/posted {item.id}" in text and f"/link {item.id} https://brand.naver.com/daekket" in text
    bot.skip_item(item.id)  # 주인이 처리함 → 아직 안 보낸 카톡은 취소
    assert _inbox(settings)[-1]["resolve"] == [f"link:{item.id}", f"review:{item.id}", f"unsure:{item.id}"]
    other = _queued(bot, product_id="naver:1", url="https://brand.naver.com/daekket/products/1")
    await bot._handle_publish_failure(other, "TimedOut: Timed out", maybe_posted=True)
    await bot.attach_link(other.id, "https://naver.me/again")  # 채널에 없어서 다시 올림
    assert {"resolve": [f"unsure:{other.id}"]}.items() <= _inbox(settings)[-3].items()


async def test_unsure_publish_is_loud_when_kakao_is_down(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    item = _queued(bot)
    await bot._handle_publish_failure(item, "TimedOut: Timed out", maybe_posted=True)
    text, loud = sent[0]
    assert text.startswith("❓") and loud and text.endswith("(카톡 알림이 안 돼서 텔레그램으로 알려요)")


async def test_collector_failing_for_two_hours_is_an_alert(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from dealbot.utils.timeutil import utcnow

    _healthy(settings)
    await bot.notifier.notify_error("collector:ppomppu", "HTTP 503")
    assert not [n for n in _inbox(settings) if n.get("kind") == "collector_stuck"]
    bot.notifier._err_since["collector:ppomppu"] = utcnow() - timedelta(hours=2, minutes=1)
    await bot.notifier.notify_error("collector:ppomppu", "HTTP 503")
    note = [n for n in _inbox(settings) if n.get("kind") == "collector_stuck"][-1]
    assert note["key"] == "collector:ppomppu" and "뽐뿌" in note["text"] and "2시간째" in note["text"]
    bot.notifier.error_cleared("collector:ppomppu")
    assert "collector:ppomppu" not in bot.notifier._err_since
    for _ in range(3):  # 발행 작업 오류가 30분 안에 3번 → 급함
        await bot.notifier.notify_error("publisher", "RuntimeError: boom")
    assert [n for n in _inbox(settings) if n.get("kind") == "publisher_stuck"][-1]["urgent"] is True


async def test_owner_summary_lines_are_sentences(bot: DealBot, settings: Settings) -> None:
    _healthy(settings)
    item = _queued(bot)
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual")
    text = await bot.owner_summary()
    day, waiting = [n for n in _inbox(settings) if n.get("kind") == "summary"][-2:]
    assert day["cat"] == "day" and day["replace"] and day["text"].startswith("오늘 딜봇은") and day["text"].endswith(".")
    assert waiting["cat"] == "waiting" and f"#{item.id}" in waiting["text"] and "내 링크를 기다리는 딜이 1개" in waiting["text"]
    assert "|" not in text
    bot.db.update_queue_item(item.id, status="skipped")
    await bot.owner_summary()
    assert _inbox(settings)[-1] == {**_inbox(settings)[-1], "cat": "waiting", "text": "", "replace": True}  # 기다리는 일이 없어지면 비움


async def test_owner_summary_names_what_was_posted(bot: DealBot, settings: Settings) -> None:
    """숫자만이 아니라 무엇을 올렸는지 이름으로 (주인 결정 10/11)."""
    _healthy(settings)
    item = _queued(bot)
    bot.db.update_queue_item(item.id, status="published")
    bot.db.record_post(item.deal, channel_id="@c", message_id=1, dry_run=bot.state.dry_run)
    await bot.owner_summary()
    day = [n for n in _inbox(settings) if n.get("cat") == "day"][-1]["text"]
    assert day.startswith("오늘 딜봇은") and "1개예요 — 「데켓 주물 IH" in day and day.endswith(".")


async def test_new_selfcheck_failure_alerts_once(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    from dealbot.scheduler import selfcheck_alerts

    _healthy(settings)
    lines = ["✅ 텔레그램: 연결됨", "❌ 쿠팡 파트너스: 키 오류"]
    await selfcheck_alerts(bot, lines)
    await selfcheck_alerts(bot, lines)  # 다시 켜져도 같은 ❌ 는 다시 안 알림
    notes = [n for n in _inbox(settings) if n.get("kind") == "selfcheck"]
    assert len(notes) == 1 and "쿠팡 파트너스" in notes[0]["text"] and notes[0]["key"] == "selfcheck:쿠팡 파트너스"


# ── telegram_silent 를 끄면 예전(10/10) 그대로 ─────────────


async def test_old_v1_relay_when_policy_is_off(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    bot.notifier.cfg.telegram_silent = False
    bot.notifier.channel_post_base = "https://t.me/oneul_hotdeal"
    item = _queued(bot)
    await bot.notifier.notify_manual_link(item, bot.registry.get("naver"))
    notes = _inbox(settings)
    assert notes and notes[-1]["kind"] == "link_request" and "v" not in notes[-1]
    assert f"#{item.id}" in notes[-1]["text"] and "brand.naver.com/daekket" in notes[-1]["text"] and len(notes[-1]["text"]) <= 200
    bot.db.update_queue_item(item.id, status="awaiting_link", error="manual")
    msg = await bot.attach_link(item.id, "https://naver.me/abc")
    assert "상품 링크 완료" in msg  # 텔레그램 답장은 그대로
    bot.notifier.cfg.notify_on_publish = False
    mine = Deal(_product(extra={"owner_link": True}), DealVerdict(is_deal=True))
    await bot.notifier.notify_published(mine, PublishResult(ok=True, message_id=77, dry_run=False))
    assert "링크를 붙여 주신 상품을 채널에 올렸어요" in sent[-1][0]
    # '상품 링크 완료'·'채널에 올렸어요' 카톡은 정책을 꺼도 없음 (주인 결정 10/11) — 링크 요청 카톡만 남는다
    assert [n["kind"] for n in _inbox(settings)] == ["link_request"]


async def test_relay_can_be_turned_off(bot: DealBot, settings: Settings) -> None:
    bot.notifier.cfg.kakao_relay = False
    bot.notifier._relay("link_request", "x")
    bot.notifier.summary("day", "x")
    assert not _inbox(settings)


async def test_alert_is_loud_when_the_kakao_line_cannot_be_written(bot: DealBot, settings: Settings, sent) -> None:  # type: ignore[no-untyped-def]
    """Blog-Auto 는 살아 있어도 받은편지함에 못 썼거나(파일 오류) 카톡 넘기기를 꺼 두었으면 할 일은 텔레그램으로 소리 내서."""
    _healthy(settings)
    item = _queued(bot)
    bot.notifier.owner_inbox = lambda note: False  # 받은편지함 쓰기 실패 (relay_to_blog 가 False)
    await bot.notifier.notify_manual_link(item, bot.registry.get("naver"))
    assert sent[-1][1] is True and sent[-1][0].endswith("(카톡 알림이 안 돼서 텔레그램으로 알려요)")
    bot.notifier.owner_inbox = bot.relay_to_blog
    bot.notifier.cfg.kakao_relay = False
    await bot.notifier.notify_deal_review(item, "네이버", "공식 판매처 확인 안 됨")
    assert sent[-1][1] is True and not _inbox(settings)


def test_relay_to_blog_reports_a_failed_write(bot: DealBot, settings: Settings) -> None:
    assert bot.relay_to_blog({"owner_notice": {"v": 2, "resolve": ["link:1"]}}) is True
    bot.settings.data_dir = settings.data_dir / "missing" / "dir"
    assert bot.relay_to_blog({"owner_notice": {"v": 2, "resolve": ["link:1"]}}) is False


async def test_price_hold_is_routine_silent_telegram_and_summary_not_kakao(bot: DealBot, settings: Settings) -> None:
    """상품 페이지 가격이 안 맞아 보류한 딜 (10/12): 주인이 할 일이 아니라 소식 — 텔레그램은 소리 없이, 저녁 요약에만."""
    from dealbot.page_price import PageCheck

    _healthy(settings)
    calls: list[dict] = []

    async def fake_send(text: str, **kw: object) -> int:
        calls.append({"text": text, **kw})
        return 1

    bot.notifier.send_with_id = fake_send  # type: ignore[method-assign]
    check = PageCheck(status="hold", title_price=9900, page_price=19900, page_label="2개입", reason="price")
    await bot.notifier.notify_price_hold(22, _product(price=9900), check)
    assert len(calls) == 1 and calls[0]["text"].startswith("⏸ #22 보류") and not calls[0].get("loud")
    notes = _inbox(settings)
    assert [(n["kind"], n["cat"]) for n in notes] == [("summary", "held")] and "데켓 주물" in notes[0]["text"]
    assert not [n for n in notes if n.get("key")]  # 할 일 카톡(키 있는 알림)은 없음


async def test_price_hold_telegram_has_no_sound_under_the_policy(bot: DealBot) -> None:
    from dealbot.page_price import PageCheck

    class FakeBot:
        def __init__(self) -> None:
            self.kw: list[dict] = []

        async def send_message(self, **kw: object):  # type: ignore[no-untyped-def]
            self.kw.append(kw)

            class M:
                message_id = 5
            return M()

    fake = FakeBot()
    bot.notifier.bot = fake  # type: ignore[assignment]
    bot.notifier.chat_id = "1"
    await bot.notifier.notify_price_hold(22, _product(price=9900), PageCheck(status="hold", title_price=9900, reason="price"))
    assert fake.kw and all(k.get("disable_notification") is True for k in fake.kw)


def test_kakao_fit_trims_only_the_first_line() -> None:
    from dealbot.monitoring.admin import kakao_fit

    out = kakao_fit("🔔[할 일] 링크 필요 #1 " + "가" * 300, "→ 텔레그램 핫딜봇 #1 에 답장")
    assert len(out) == 200 and out.endswith("…\n→ 텔레그램 핫딜봇 #1 에 답장")


def test_long_store_url_keeps_kakao_text_in_one_message() -> None:
    from dealbot.monitoring.admin import _short_url

    url = "https://brand.naver.com/daekket/products/13008954661?" + "nl-query=%EB%8D%B0%EC%BC%93&" * 6
    assert _short_url(url) == "https://brand.naver.com/daekket/products/13008954661"
    assert _short_url("https://link.coupang.com/a/abc") == "https://link.coupang.com/a/abc"


def test_channel_post_base_only_for_public_channels() -> None:
    from dealbot.monitoring.admin import channel_post_base

    assert channel_post_base("@oneul_hotdeal") == "https://t.me/oneul_hotdeal"
    assert channel_post_base("-1001234567890", "https://t.me/oneul_hotdeal") == "https://t.me/oneul_hotdeal"
    assert channel_post_base("-1001234567890", "https://t.me/+AbCdEf") is None
    assert channel_post_base(None) is None
