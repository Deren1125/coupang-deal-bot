"""네이버 딜: 주인이 링크와 같이 보낸 사진 = 그 딜의 확인된 상품 사진 (주인 결정 10/15, '너말대로 가자').

네이버 쇼핑 검색 Open API 는 2026-07-31 종료(대신할 API 없음), 스토어 페이지는 이 서버에 429 → 네이버 딜은 사진이 거의 없었다.
① 주인이 휴대폰으로 가격·옵션을 보고 링크를 만들 때 상품 사진도 같이 보낸다 → 채널·블로그·인스타 상품 사진.
  그 확인이 가격 확인이라, 주인이 링크를 붙인 네이버 딜은 페이지 대조로 보류하지 않는다 (test_page_price.py 에도).
  링크 요청(텔레그램·카톡)에 '가격·옵션 확인 후 사진도 같이 보내 주세요 — 제목 가격 N원'.
② 주인 사진이 없는 네이버 딜: 채널엔 글만(미리보기 끔), 인스타는 상품명·가격 글자 카드 (설정은 skip 그대로 — 쿠팡 등은 그대로 안 올림).
  사진을 찾지 않는다 — 딜에 적힌 사진·스토어 페이지·크롬 사진 모두 안 씀 (검토 지적으로 '스토어 사진은 시도' 를 뺐음).
③ 뽐뿌 글 사진은 쓰지 않는다. ④ 쇼핑 검색 사진 찾기는 지움 (test_naver_store_photo.py).
테스트는 가짜만 쓴다 (네트워크·텔레그램·크롬 없음).
"""

from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from dealbot.config import Settings
from dealbot.media.imagecheck import clean_image, owner_photo
from dealbot.models import Deal, DealVerdict, Product, PublishResult
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer

STORE_URL = "https://brand.naver.com/daekket/products/13008954661"
BOARD_THUMB = "https://cdn4.ppomppu.co.kr/zboard/data/_thumb/ppomppu/1/small_739081.jpg"
LINK = "https://naver.me/xMINE"
ASK = "📸 가격·옵션 확인 후 사진도 같이 보내 주세요 — 제목 가격 60,020원"


def _jpeg(w: int, h: int, color: tuple[int, int, int] = (40, 120, 200), *, exif: bool = False, fmt: str = "JPEG") -> bytes:
    img = Image.new("RGB", (w, h), color)
    out = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"  # Make
        ex[0x0112] = 1  # Orientation
        ex[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0), 3: "E", 4: (126.0, 58.0, 0.0)}  # GPS
        img.save(out, fmt, exif=ex.tobytes())
    else:
        img.save(out, fmt)
    return out.getvalue()


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    settings.secrets.public_domain = "bot.example.com"
    settings.threads.enabled = False
    settings.copy_cfg.enabled = False
    b = DealBot(settings)
    yield b
    b.db.close()


def _product(**kw: object) -> Product:
    base: dict = dict(source="ppomppu", product_id="naver:13008954661", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm",
                      price=60020, url=STORE_URL, image_url=BOARD_THUMB, recommend_count=9)
    base.update(kw)
    return Product(**base)


def _waiting(bot, **kw: object) -> int:  # type: ignore[no-untyped-def]
    """링크 요청 중인 글 하나."""
    assert bot.db.enqueue(Deal(_product(**kw), DealVerdict(is_deal=True)), score=1)
    qid = bot.db.pending_items()[0].id
    bot.db.update_queue_item(qid, status="awaiting_link", error="manual link required")
    return qid


def _owner_files(settings: Settings) -> list[Path]:
    d = settings.data_dir / "owner_photos"
    return sorted(d.glob("*.jpg")) if d.exists() else []


def _inbox(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r["owner_notice"] for r in rows if "owner_notice" in r]


def _healthy(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "blogauto_status.json").write_text(
        json.dumps({"at": int(time.time()), "kakao_ok": True, "kakao_checked": 0, "outbox": 0}), encoding="utf-8")


# ---------------------------------------------------------------- 1. 링크 요청: 사진도 같이 (제목 가격)
async def test_link_request_asks_for_the_photo_with_the_title_price(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    sent: list[str] = []

    async def fake_send(text: str, **_kw: object) -> int:
        sent.append(text)
        return len(sent)

    bot.notifier.send_with_id = fake_send
    # 페이지 가격으로 바꿔 둔 딜도 부탁은 글 제목 가격으로 (app._title_price 와 같은 값)
    qid = _waiting(bot, name="데켓 주물 IH 오발쿡플레이트 31cm " + "아주 긴 상품명 " * 20, price=61500,
                   extra={"title_price": 60020})
    await bot.notifier.notify_manual_link(bot.db.get_queue_item(qid), bot.registry.get("naver"))
    tg = sent[-1]
    assert tg.startswith(f"🔗 <b>내 링크가 필요합니다 #{qid}</b> [네이버]")  # 말 명령이 알아보는 첫머리는 그대로
    assert ASK in tg and f"<code>/link {qid} https://...</code>" in tg and "이 메시지에 답장" in tg
    assert "채널엔 글만, 인스타는 글자 카드" in tg and "못 찾으면" not in tg  # 사진을 찾지 않으니 '못 찾으면' 이 아님
    assert "화면 캡처는 상품 부분만 잘라서" in tg
    note = _inbox(settings)[-1]
    text = note["text"]
    # 카톡: 긴 상품명은 잘려도 사진 부탁은 둘째 줄이라 남는다 (200자 한 통, 주소 없음)
    assert len(text) <= 200 and ASK.removeprefix("📸 ") in text and "http" not in text
    assert text.endswith(f"→ 텔레그램 핫딜봇 #{qid} 에 답장") and text.startswith(f"🔔[할 일] 링크 필요 #{qid} [네이버]")

    # 가격 없는 딜: 가격 부분만 뺌
    q2 = _waiting(bot, product_id="naver:2", name="데켓 쿠폰 이벤트", price=0, url="https://brand.naver.com/daekket/products/2")
    await bot.notifier.notify_manual_link(bot.db.get_queue_item(q2), bot.registry.get("naver"))
    assert "📸 가격·옵션 확인 후 사진도 같이 보내 주세요\n" in sent[-1] and "제목 가격" not in sent[-1]


async def test_toss_link_request_is_unchanged(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    _healthy(settings)
    sent: list[str] = []

    async def fake_send(text: str, **_kw: object) -> int:
        sent.append(text)
        return 1

    bot.notifier.send_with_id = fake_send
    qid = _waiting(bot, product_id="toss:1", shop="toss", name="풀무원 샘물 2L 24개", price=6410, url="https://toss.im/_m/abc")
    await bot.notifier.notify_manual_link(bot.db.get_queue_item(qid), bot.registry.get("toss"))
    assert "사진" not in sent[-1] and "사진" not in _inbox(settings)[-1]["text"]


async def test_old_v1_relay_carries_the_ask_within_200(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    bot.notifier.cfg.telegram_silent = False
    sent: list[str] = []

    async def fake_send(text: str, **_kw: object) -> int:
        sent.append(text)
        return 1

    bot.notifier.send_with_id = fake_send
    for name in ("데켓 주물 IH 오발쿡플레이트 31cm", "데켓 주물 IH 오발쿡플레이트 31cm " + "아주 긴 상품명 " * 20):
        qid = _waiting(bot, product_id=f"naver:{len(name)}", name=name)
        await bot.notifier.notify_manual_link(bot.db.get_queue_item(qid), bot.registry.get("naver"))
        text = [n for n in _inbox(settings) if n.get("kind") == "link_request"][-1]["text"]
        assert len(text) <= 200 and ASK.removeprefix("📸 ") in text and f"#{qid}" in text, text


# ---------------------------------------------------------------- 2. 링크 + 사진 붙이기
async def test_link_with_photo_stores_a_clean_private_photo(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot)
    raw = _jpeg(3000, 2400, exif=True)  # 휴대폰 사진: 크고 위치 정보가 있음
    assert Image.open(io.BytesIO(raw)).getexif().get(0x8825)
    reply = await bot.attach_link(qid, LINK, photo=raw)
    assert reply.startswith(f"✅ <b>상품 링크 완료 #{qid}</b>") and "📸 상품 사진도 받았어요 (채널·블로그·인스타에 씀)" in reply
    assert "한 번 더 알려" not in reply
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.affiliate_url == LINK and item.deal.product.extra["owner_link"] is True
    info = item.deal.product.extra["owner_photo"]
    (f,) = _owner_files(settings)
    assert info["file"] == f.name and (info["w"], info["h"]) == (1280, 1024) and info["at"]
    assert f.parent == settings.data_dir / "owner_photos"  # 공개 폴더(/media/)가 아님
    assert not list((settings.data_dir / "media").glob("**/*.jpg")) if (settings.data_dir / "media").exists() else True
    with Image.open(f) as im:
        assert im.format == "JPEG" and max(im.size) <= 1280 and not im.getexif()  # EXIF(위치·기기) 없음
    # 사진 없이 링크만 다시 보내면 전에 받은 사진 그대로
    reply = await bot.attach_link(qid, LINK)
    assert "전에 받은 상품 사진을 그대로 써요" in reply and bot.db.get_queue_item(qid).deal.product.extra["owner_photo"] == info
    # 사진을 바꿔 다시 보내면 새 사진으로 (예전 파일은 지움)
    await bot.attach_link(qid, LINK, photo=_jpeg(800, 800, (200, 30, 30)))
    (g,) = _owner_files(settings)
    assert g.name != f.name and bot.db.get_queue_item(qid).deal.product.extra["owner_photo"]["file"] == g.name


@pytest.mark.parametrize(
    ("data", "why"),
    [
        (b"x" * 10_000_001, "10MB"),
        (b"<html>not a photo</html>", "JPG·PNG·WEBP"),
        (_jpeg(800, 800, fmt="GIF"), "HEIC·GIF"),
        (_jpeg(400, 300), "사진이 너무 작아요 (400×300) — 짧은 변이 600px 이상이어야 해요"),
        # 텔레그램이 줄인 휴대폰 화면 캡처 (1179×2556 → 590×1280): '작다'만이 아니라 자르거나 파일로 보내는 법까지
        (_jpeg(590, 1280), "사진이 너무 작아요 (590×1280) — 짧은 변이 600px 이상이어야 해요 (화면 캡처면 상품 부분만 잘라서, 또는 파일로 보내 주세요)"),
        (_jpeg(700, 1800), "사진이 너무 길쭉해요 (700×1800)"),
    ],
)
async def test_unusable_photo_is_refused_without_attaching(bot, settings: Settings, data: bytes, why: str) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot)
    reply = await bot.attach_link(qid, LINK, photo=data)
    assert reply.startswith(f"⚠️ #{qid} 사진을 쓸 수 없어 링크도 아직 안 붙였어요") and why in reply, reply
    assert f"<code>/link {qid} 링크</code>" in reply and "사진 없이 올리려면 링크만" in reply
    item = bot.db.get_queue_item(qid)
    assert item.status == "awaiting_link" and item.deal.affiliate_url is None and "owner_photo" not in item.deal.product.extra
    assert _owner_files(settings) == [] and bot.db.kv_get("mylink:naver:13008954661") is None


def test_owner_photo_check_guards_huge_pixel_counts() -> None:
    big = io.BytesIO()
    Image.new("1", (9000, 9000)).save(big, "PNG")  # 작은 파일이지만 8,100만 화소
    jpeg, why = owner_photo(big.getvalue())
    assert jpeg is None and "해상도가 너무 커요" in why
    jpeg, why = owner_photo(_jpeg(1200, 900, fmt="PNG"))
    assert jpeg is not None and why == "" and Image.open(io.BytesIO(jpeg)).format == "JPEG"


async def test_photo_with_a_non_naver_link_is_not_used(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot, product_id="toss:1", shop="toss", name="풀무원 샘물 2L 24개", price=6410, url="https://toss.im/_m/abc")
    reply = await bot.attach_link(qid, "https://toss.im/_m/MINE", photo=_jpeg(900, 900))
    assert "상품 링크 완료" in reply and "사진은 네이버 딜에만 써요 — 링크만 붙였어요" in reply
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and "owner_photo" not in item.deal.product.extra and _owner_files(settings) == []
    # 사진 없이 붙인 토스 링크는 예전 답장 그대로 (사진 말 없음)
    q2 = _waiting(bot, product_id="toss:2", shop="toss", name="스파클 생수", price=5900, url="https://toss.im/_m/def")
    assert "사진" not in await bot.attach_link(q2, "https://toss.im/_m/MINE2")


async def test_naver_link_without_photo_says_text_only(bot) -> None:  # type: ignore[no-untyped-def]
    reply = await bot.attach_link(_waiting(bot), LINK)
    assert "사진 없이 받았어요 — 채널엔 글만, 인스타는 상품명·가격 글자 카드로 올라가요" in reply and "못 찾으면" not in reply


# ---------------------------------------------------------------- 3. 확인된 상품 사진으로 쓰기
async def test_owner_photo_is_the_verified_photo_and_no_page_is_opened(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot)
    await bot.attach_link(qid, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    deal = bot.db.get_queue_item(qid).deal
    opened: list[str] = []

    async def page_meta(url: str) -> None:
        opened.append(url)

    async def fetch(url: str, **_kw: object) -> bytes:
        opened.append(url)
        return _jpeg(1000, 1000)

    bot.store_browser.page_meta = page_meta
    bot.enricher.fetch = page_meta
    bot._fetch_image = fetch
    photo = await bot.deal_photo(deal)
    assert photo is not None and opened == []  # 스토어 페이지·크롬·게시판 사진 모두 안 엶
    # 주소: 발행할 때 만든 공개 사본 (캡션이 길어 글로 보낼 때 미리보기·스레드가 쓰는 같은 사진)
    url = deal.product.image_url or ""
    assert url.startswith("https://bot.example.com/media/owner_") and url.endswith(".jpg")
    assert (settings.data_dir / "media" / url.rsplit("/", 1)[-1]).read_bytes() == photo
    with Image.open(io.BytesIO(photo)) as im:
        r, g, b = im.convert("RGB").getpixel((500, 500))
        assert abs(r - 120) < 20 and abs(g - 80) < 20 and abs(b - 60) < 20
    # 블로그 내보내기 (약속 10/15): 주인이 보낸 사진은 photo_kind 'owner' (봇이 확인한 사진은 'verified'), 기다릴 일 없음
    bot.export_published(deal, photo)
    row = json.loads((settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["photo_kind"] == "owner" and row["photo_src"] == "owner" and Path(row["photo_file"]).exists()
    assert row["photo_wait_until"] is None and row["text_card_file"] == ""
    assert Path(row["photo_file"]).parent == settings.data_dir / "media" / "export"
    # 사진 파일이 사라졌으면(정리됨) 사진 없는 네이버 딜과 같다 — 찾지 않는다: 스토어 페이지·크롬·뽐뿌 썸네일 모두 안 엶 (검토 지적)
    for f in _owner_files(settings):
        f.unlink()
    opened.clear()
    deal.product.image_url = BOARD_THUMB
    assert await bot.deal_photo(deal) is None and opened == [] and deal.product.image_url is None


async def test_owner_photo_deal_publishes_with_that_photo_everywhere(bot) -> None:  # type: ignore[no-untyped-def]
    """발행 흐름: 주인 사진 = 텔레그램 사진 = 인스타 카드 사진 (같은 바이트). 페이지 대조가 '보류'여도 주인 확인이라 올린다."""
    qid = _waiting(bot)
    await bot.attach_link(qid, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    item = bot.db.get_queue_item(qid)
    item.deal.product.extra["page_price"] = {"status": "hold", "title_price": 60020, "reason": "price"}  # 옛 판정이 남아 있어도
    bot.db.update_queue_item(qid, status="pending", deal=item.deal)
    got: dict[str, object] = {}

    async def fake_publish(deal: Deal, **kw: object) -> PublishResult:
        got["telegram_photo"] = kw.get("photo")
        return PublishResult(ok=True, message_id=1)

    async def side(deal: Deal, *, photo: bytes | None = None, deal_no: int | None = None) -> None:
        got["side_photo"] = photo

    bot.publisher.publish = fake_publish
    bot._publish_side_channels = side
    bot.settings.publish.min_interval_seconds = 0
    bot.state.dry_run = False
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "published"
    assert got["telegram_photo"] is not None and got["side_photo"] is got["telegram_photo"]


class _FakeTg:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_photo(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(("photo", kw))
        return SimpleNamespace(message_id=1)

    async def send_message(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(("message", kw))
        return SimpleNamespace(message_id=2)


async def test_telegram_photo_without_address_never_gets_an_auto_preview(repo_root: Path) -> None:
    """공개 도메인이 없으면 주인 사진은 주소가 없다(image_url None). 캡션이 길어 글로 보내게 되면 미리보기를 꺼야 한다 — 안 끄면
    텔레그램이 본문 첫 링크(스토어)의 검사 안 한 사진을 붙인다. 짧으면 그 사진을 그대로 보낸다 (주소가 있으면 아래 테스트)."""
    from dealbot.cli import sample_deal

    fake = _FakeTg()
    pub = TelegramPublisher(fake, "-100123", TemplateRenderer(repo_root / "templates"))  # type: ignore[arg-type]
    d = sample_deal()
    d.product.image_url = None
    photo = clean_image(_jpeg(900, 900))
    assert (await pub.publish(d, photo=photo)).ok
    assert fake.calls[-1][0] == "photo" and fake.calls[-1][1]["photo"] == photo
    d.product.name = "아주 긴 상품명 " * 120  # 캡션 1024자 넘음 → 글로
    assert (await pub.publish(d, photo=photo)).ok
    kind, kw = fake.calls[-1]
    assert kind == "message" and kw["link_preview_options"].is_disabled is True


# ---------------------------------------------------------------- 4. 사진 없는 네이버 딜: 채널엔 글만, 인스타는 글자 카드
class _Spy:
    def __init__(self, bot) -> None:  # type: ignore[no-untyped-def]
        self.saved: list[bytes | None] = []
        self.posted: list[str | None] = []
        save, publish = bot.card.save, bot.instagram.publish

        def spy_save(deal: Deal, photo: bytes | None = None, **kw: object) -> Path:
            self.saved.append(photo)
            return save(deal, photo, **kw)  # type: ignore[arg-type]

        async def spy_publish(deal: Deal, image_url: str | None, **kw: object) -> PublishResult:
            self.posted.append(image_url)
            return await publish(deal, image_url, **kw)  # type: ignore[arg-type]

        bot.card.save = spy_save
        bot.instagram.publish = spy_publish


def _ig_events(bot) -> list[str]:  # type: ignore[no-untyped-def]
    return [e["message"] for e in bot.db.recent_events(20) if e["kind"] == "instagram"]


async def test_naver_deal_without_photo_gets_a_text_card(bot, ig_post_now) -> None:  # type: ignore[no-untyped-def]
    """주인 결정 10/15 로 기본(no_photo)이 text_card 가 됐다 (예전엔 skip 이 기본이라 쿠팡 등은 안 올림). 네이버 딜은 skip 설정이어도
    글자 카드 — 여기선 skip 으로 두고 본다. 인스타는 대기열 → 시간표로 올라간다 (ig_post_now)."""
    assert bot.settings.instagram.no_photo == "text_card"
    bot.settings.instagram.no_photo = "skip"
    spy = _Spy(bot)
    bot._fetch_image = lambda *a, **k: (_ for _ in ()).throw(AssertionError("사진을 받으면 안 됨"))
    d = Deal(_product(), DealVerdict(is_deal=True), affiliate_url=LINK)
    photo = await bot.deal_photo(d)  # 스토어 막힘(conftest) · 크롬 못 엶 · 뽐뿌 썸네일은 안 씀
    assert photo is None and d.product.image_url is None
    await bot._publish_side_channels(d, photo=photo, deal_no=7)
    await ig_post_now(bot)
    assert spy.saved == [None] and len(spy.posted) == 1 and (spy.posted[0] or "").startswith("https://bot.example.com/media/")
    assert not any("skipped" in m for m in _ig_events(bot))
    card = bot.settings.data_dir / "media" / (spy.posted[0] or "").rsplit("/", 1)[-1]
    with Image.open(card) as im:
        assert im.size == (1080, 1350)
    # 쿠팡 딜은 그대로 '확인된 상품 사진 없음' 으로 안 올림
    c = Deal(Product(source="goldbox", product_id="coupang:9", shop="coupang", name="텀블러", price=29900,
                     url="https://www.coupang.com/vp/products/9"), DealVerdict(is_deal=True))
    await bot._publish_side_channels(c, photo=None, deal_no=8)
    await ig_post_now(bot)
    assert len(spy.saved) == 1 and any("coupang:9 skipped: 확인된 상품 사진 없음" in m for m in _ig_events(bot))
    assert bot._ig_skip_reason(None, deal=d) is None and bot._ig_skip_reason(None, deal=c) == "확인된 상품 사진 없음"


async def test_naver_owner_photo_becomes_the_instagram_card_photo(bot, ig_post_now) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot)
    await bot.attach_link(qid, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    deal = bot.db.get_queue_item(qid).deal
    spy = _Spy(bot)
    photo = await bot.deal_photo(deal)
    assert photo is not None
    await bot._publish_side_channels(deal, photo=photo, deal_no=9)
    await ig_post_now(bot)  # 대기열에 남긴 사진 사본으로 카드
    assert spy.saved == [photo] and len(spy.posted) == 1
    card = bot.settings.data_dir / "media" / (spy.posted[0] or "").rsplit("/", 1)[-1]
    with Image.open(card) as im:  # 카드 사진 칸이 주인 사진 색 (글자 카드가 아님)
        assert im.size == (1080, 1350)
        colors = {px for _n, px in im.convert("RGB").getcolors(1_500_000) or []}
        assert any(abs(r - 120) < 25 and abs(g - 80) < 25 and abs(b - 60) < 25 for r, g, b in colors)


# ---------------------------------------------------------------- 5. 자기 점검 · 정리
async def test_self_check_says_naver_search_ended(bot) -> None:  # type: ignore[no-untyped-def]
    assert bot.naver_ref is None  # 운영 설정: 쿠팡 딜 네이버 최저가 비교는 꺼 둠
    lines = [text for _ok, text in await bot.self_check()]
    line = next(t for t in lines if t.startswith("네이버 쇼핑 검색"))
    assert line.startswith("네이버 쇼핑 검색: 2026-07-31 종료 — 대신할 API 없음, 키 조치 필요 없음")
    assert not any("네이버 최저가 비교" in t for t in lines)


def test_maintenance_prunes_old_owner_photos(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    d = settings.data_dir / "owner_photos"
    d.mkdir(parents=True)
    old, new = d / "q1_1.jpg", d / "q2_2.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    past = time.time() - 15 * 86400
    os.utime(old, (past, past))
    bot.maintenance()
    assert not old.exists() and new.exists()


# ---------------------------------------------------------------- 6. 검토 반영 (10/15 둘째 판)
async def test_naver_deal_without_owner_photo_posts_text_only_and_never_looks_for_a_photo(bot, ig_post_now) -> None:  # type: ignore[no-untyped-def]
    """검토 지적 (②를 글자 그대로): 주인이 사진 없이 링크만 보낸 네이버 딜은 사진을 찾지 않는다 — 딜에 적힌 큰 스토어 사진,
    일반 요청으로 읽힌 스토어 페이지, 크롬(최대 45초) 모두 쓰지 않는다. 예전엔 크롬이 열리면 채널에 스토어 사진이 붙고 인스타도
    그 사진 카드였다. 이제 채널엔 글만(사진 바이트·주소 없음), 인스타는 글자 카드."""
    from dealbot.enrich import page_meta_at

    big = "https://shop-phinf.pstatic.net/20260123_260/1769_JPEG/1032.jpeg"
    store_html = f'<html><head><title>데켓 주물팬</title><meta property="og:image" content="{big}"></head></html>'
    opened: list[str] = []
    fetched: list[str] = []

    async def page(url: str) -> object:  # 일반 요청: 읽힘 (품절·정품 확인도 이걸 쓴다 — 그 페이지에 큰 사진이 있음)
        return page_meta_at(store_html, STORE_URL)

    async def browser(url: str) -> object:
        opened.append(url)
        return page_meta_at(store_html, STORE_URL)

    async def fetch(url: str, **_kw: object) -> bytes:
        fetched.append(url)
        return _jpeg(1000, 1000, (120, 80, 60))

    bot.enricher.fetch = page
    bot.store_browser.page_meta = browser
    bot._fetch_image = fetch
    qid = _waiting(bot, image_url=big)
    reply = await bot.attach_link(qid, LINK)  # 사진 없이
    assert "사진 없이 받았어요 — 채널엔 글만" in reply
    got: dict[str, object] = {}

    async def fake_publish(deal: Deal, **kw: object) -> PublishResult:
        got.update(photo=kw.get("photo"), image=deal.product.image_url)
        return PublishResult(ok=True, message_id=1)

    spy = _Spy(bot)
    bot.publisher.publish = fake_publish
    bot.settings.publish.min_interval_seconds = 0
    bot.state.dry_run = False
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "published"
    assert got == {"photo": None, "image": None} and opened == [] and fetched == []
    # 바뀜 (IG-1): 사진 없이 올라간 딜은 주인 사진을 기다리는 동안(📷 사진 필요, 4시간) 인스타 딜 글을 미룬다
    assert bot.late_photo_waiting(qid)
    assert await ig_post_now(bot) is None and spy.saved == [] and spy.posted == []
    info = json.loads(bot.db.kv_get(f"late_photo:{qid}"))
    bot.db.kv_set(f"late_photo:{qid}", json.dumps({**info, "until": "2000-01-01T00:00:00+00:00"}))  # 사진 없이 기다림이 끝남
    await ig_post_now(bot)
    assert spy.saved == [None] and len(spy.posted) == 1  # 인스타: 글자 카드 (대기열 → 시간표)
    # 텔레그램: 사진도 주소도 없으면 미리보기를 끈다 (스토어 og:image 가 미리보기로 붙지 않게)
    from dealbot.cli import sample_deal

    fake = _FakeTg()
    pub = TelegramPublisher(fake, "-100123", bot.renderer)  # type: ignore[arg-type]
    d = sample_deal()
    d.product.image_url = None
    assert (await pub.publish(d, photo=None)).ok
    assert fake.calls[-1][0] == "message" and fake.calls[-1][1]["link_preview_options"].is_disabled is True


def test_phone_jpeg_with_a_multi_picture_block_is_accepted() -> None:
    """검토 지적: 요즘 휴대폰 JPG (안드로이드 Ultra HDR·아이폰 HDR — JPEG 안에 MPF 묶음)는 Pillow 가 'MPO' 로 읽는다.
    예전엔 'JPG·PNG·WEBP 사진만 받아요' 로 JPG 를 거절했다 (다시 보내도 같음). 첫 장만 보통 JPEG 로 다시 저장."""
    main = Image.new("RGB", (1600, 1200), (40, 120, 200))
    gain = Image.new("RGB", (400, 300), (0, 0, 0))  # HDR 게인 맵 같은 둘째 장
    out = io.BytesIO()
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (37.0, 33.0, 0.0)}  # 위치
    main.save(out, "MPO", save_all=True, append_images=[gain], exif=ex.tobytes())
    raw = out.getvalue()
    with Image.open(io.BytesIO(raw)) as im:
        assert im.format == "MPO" and im.n_frames == 2
    jpeg, why = owner_photo(raw)
    assert jpeg is not None and why == ""
    with Image.open(io.BytesIO(jpeg)) as im:
        assert im.format == "JPEG" and getattr(im, "n_frames", 1) == 1 and im.size == (1280, 960) and not im.getexif()


async def test_phone_screenshot_sent_as_a_file_survives_to_publish(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """검토 지적의 짝: 텔레그램이 줄이지 않게 파일로 보낸 휴대폰 화면 캡처(1179×2556, 비율 2.17)는 받는다. 예전엔 긴 변 1280 으로 줄여
    590×1280 으로 저장해, 발행 때 600px 검사에서 떨어져 사진 없이 올라갔다 — 이제 줄여도 짧은 변 600 을 지킨다."""
    qid = _waiting(bot)
    reply = await bot.attach_link(qid, LINK, photo=_jpeg(1179, 2556))
    assert "📸 상품 사진도 받았어요" in reply
    info = bot.db.get_queue_item(qid).deal.product.extra["owner_photo"]
    assert min(info["w"], info["h"]) >= 600 and max(info["w"], info["h"]) <= 1310
    photo = await bot.deal_photo(bot.db.get_queue_item(qid).deal)
    assert photo is not None
    with Image.open(io.BytesIO(photo)) as im:
        assert min(im.size) >= 600
    # 보통 사진은 그대로 긴 변 1280
    jpeg, _ = owner_photo(_jpeg(4000, 3000))
    assert jpeg is not None and Image.open(io.BytesIO(jpeg)).size == (1280, 960)


def test_owner_photo_check_turns_pillow_bomb_warnings_into_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """검토 지적: 화소 폭탄 경고(DecompressionBombWarning)·오류 갈래가 시험되지 않았다 (9000×9000 은 Pillow 경고 기준 아래).
    기준을 낮춰 두 갈래를 모두 지난다 — 경고(기준 초과)도, 오류(기준 두 배 초과)도 '해상도가 너무 커요' 로 거절."""
    data = _jpeg(800, 800)  # 64만 화소
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 500_000)  # 64만 > 50만 → 경고 (두 배 100만 아래)
    jpeg, why = owner_photo(data)
    assert jpeg is None and why == "사진 해상도가 너무 커요"
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200_000)  # 64만 > 40만 → 오류
    jpeg, why = owner_photo(data)
    assert jpeg is None and why == "사진 해상도가 너무 커요"


async def test_photo_is_fetched_only_when_the_naver_link_can_be_attached(bot) -> None:  # type: ignore[no-untyped-def]
    """검토 지적: 사진부터 받아 검사하지 않는다 — 없는 번호·채널 글 주소(❓)·네이버가 아닌 딜이면 사진을 받지 않고 링크 쪽 답만.
    (HEIC·10MB 사진이 토스 링크를 막던 일, '/link 999' 에도 사진을 받던 일)"""
    asked: list[int] = []

    def loader(data: bytes | None, why: str = ""):  # type: ignore[no-untyped-def]
        async def load() -> tuple[bytes | None, str]:
            asked.append(1)
            return data, why
        return load

    heic = loader(None, "사진으로 열리지 않아요 — HEIC")
    assert "#999 번 글이 없습니다" in await bot.attach_link(999, LINK, photo=heic)
    toss = _waiting(bot, product_id="toss:1", shop="toss", name="풀무원 샘물 2L 24개", price=6410, url="https://toss.im/_m/abc")
    reply = await bot.attach_link(toss, "https://toss.im/_m/MINE", photo=heic)
    assert "상품 링크 완료" in reply and "사진은 네이버 딜에만 써요" in reply and bot.db.get_queue_item(toss).status == "pending"
    assert "링크는 http" in await bot.attach_link(_waiting(bot, product_id="naver:5"), "naver.me/x", photo=heic)
    assert asked == []
    qid = _waiting(bot, product_id="naver:6")
    assert "📸 상품 사진도 받았어요" in await bot.attach_link(qid, LINK, photo=loader(_jpeg(900, 900)))
    assert asked == [1]


async def test_item_changed_while_the_photo_was_loading_is_checked_again(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """사진을 받고 푸는 사이(await) 주인이 다른 답으로 그 글을 건너뛰었으면 링크도 사진도 붙이지 않는다 (다시 읽어 같은 기준)."""
    qid = _waiting(bot)

    async def load() -> tuple[bytes | None, str]:
        bot.skip_item(qid)  # 받는 사이에 건너뜀
        return _jpeg(900, 900), ""

    reply = await bot.attach_link(qid, LINK, photo=load)
    assert "'skipped' 상태라 링크를 붙일 수 없습니다" in reply
    item = bot.db.get_queue_item(qid)
    assert item.status == "skipped" and item.deal.affiliate_url is None and "owner_photo" not in item.deal.product.extra
    assert _owner_files(settings) == []


async def test_big_photo_is_decoded_off_the_event_loop(bot, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """검토 지적: 4,000만 화소까지 푸는 일이 이벤트 루프에서 돌면 그동안 텔레그램 받기·발행이 멈춘다 → 따로 돌린다."""
    import threading

    import dealbot.app as app_mod

    where: list[bool] = []
    real = app_mod.owner_photo

    def spy(data: bytes | None, **kw: object) -> tuple[bytes | None, str]:
        where.append(threading.current_thread() is threading.main_thread())
        return real(data, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(app_mod, "owner_photo", spy)
    assert "📸 상품 사진도 받았어요" in await bot.attach_link(_waiting(bot), LINK, photo=_jpeg(900, 900))
    assert where == [False]


async def test_repeat_deal_with_the_saved_link_reuses_the_owner_photo(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    """검토 제안: 같은 상품이 또 나와 그때 만든 링크(mylink)를 다시 쓰면, 그때 같이 보낸 사진도 다시 쓴다 (파일이 남아 있는 동안).
    주인이 이번 딜을 본 건 아니라 가격 확인으로는 치지 않는다 (owner_link 없음)."""
    first = _waiting(bot)
    await bot.attach_link(first, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    bot.db.update_queue_item(first, status="published")
    assert bot.db.enqueue(Deal(_product(), DealVerdict(is_deal=True)), score=1)
    item = next(i for i in bot.db.pending_items() if i.id != first)
    assert await bot._ensure_link(item) == ("ok", None) and item.deal.affiliate_url == LINK
    p = item.deal.product
    assert p.extra["owner_photo"]["reused"] is True and not p.extra.get("owner_link") and not bot._owner_checked(p)
    photo = await bot.deal_photo(item.deal)
    assert photo is not None
    with Image.open(io.BytesIO(photo)) as im:
        r, g, b = im.convert("RGB").getpixel((500, 500))
        assert abs(r - 120) < 20 and abs(g - 80) < 20 and abs(b - 60) < 20
    # 사진이 정리(14일)됐으면 다시 쓰지 않음 → 글만
    bot.db.update_queue_item(item.id, status="published")
    for f in _owner_files(settings):
        f.unlink()
    assert bot.db.enqueue(Deal(_product(product_id="naver:13008954661", name="데켓 주물팬 다시"), DealVerdict(is_deal=True)), score=1)
    again = max(bot.db.pending_items(), key=lambda i: i.id)
    assert await bot._ensure_link(again) == ("ok", None) and "owner_photo" not in again.deal.product.extra


async def test_long_caption_keeps_the_owner_photo_as_the_preview(bot, settings: Settings, repo_root: Path) -> None:  # type: ignore[no-untyped-def]
    """검토 지적: 글이 길어(캡션 1024자 초과) 텔레그램 글로 보낼 때 주인 사진은 주소가 없어 미리보기가 꺼지고 채널에 사진이 빠졌다.
    이제 발행할 때 공개 사본(/media/owner_…)을 만들어 그 주소로 큰 미리보기를 띄운다 (스레드도 같은 사진). 오래된 사본은 정리."""
    qid = _waiting(bot)
    await bot.attach_link(qid, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    deal = bot.db.get_queue_item(qid).deal
    photo = await bot.deal_photo(deal)
    url = deal.product.image_url or ""
    assert photo is not None and url.startswith("https://bot.example.com/media/owner_")
    fake = _FakeTg()
    pub = TelegramPublisher(fake, "-100123", TemplateRenderer(repo_root / "templates"))  # type: ignore[arg-type]
    deal.product.name = "아주 긴 상품명 " * 120  # 캡션 1024자 넘음 → 글로
    assert (await pub.publish(deal, photo=photo)).ok
    kind, kw = fake.calls[-1]
    assert kind == "message" and kw["link_preview_options"].url == url and kw["link_preview_options"].is_disabled is not True
    # 공개 도메인이 없으면 예전처럼 주소 없음 → 미리보기 끔 (위 test_telegram_photo_without_address_never_gets_an_auto_preview)
    bot.settings.secrets.public_domain = None
    assert bot.settings.secrets.public_base_url is None
    assert await bot.deal_photo(deal) is not None and deal.product.image_url is None
    # 공개 사본도 14일 뒤 정리
    copy = settings.data_dir / "media" / url.rsplit("/", 1)[-1]
    past = time.time() - 15 * 86400
    os.utime(copy, (past, past))
    bot.maintenance()
    assert not copy.exists()
