"""네이버 딜: 주인이 링크와 같이 보낸 사진 = 그 딜의 확인된 상품 사진 (주인 결정 10/15, '너말대로 가자').

네이버 쇼핑 검색 Open API 는 2026-07-31 종료(대신할 API 없음), 스토어 페이지는 이 서버에 429 → 네이버 딜은 사진이 거의 없었다.
① 주인이 휴대폰으로 가격·옵션을 보고 링크를 만들 때 상품 사진도 같이 보낸다 → 채널·블로그·인스타 상품 사진.
  그 확인이 가격 확인이라, 주인이 링크를 붙인 네이버 딜은 페이지 대조로 보류하지 않는다 (test_page_price.py 에도).
  링크 요청(텔레그램·카톡)에 '가격·옵션 확인 후 사진도 같이 보내 주세요 — 제목 가격 N원'.
② 주인 사진이 없는 네이버 딜: 채널엔 글만(미리보기 끔), 인스타는 상품명·가격 글자 카드 (설정은 skip 그대로 — 쿠팡 등은 그대로 안 올림).
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
    assert "채널엔 글만, 인스타는 글자 카드" in tg
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
        (_jpeg(400, 300), "사진이 너무 작아요 (400×300)"),
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
    assert "사진 없이 받았어요 — 상품 사진을 못 찾으면 채널엔 글만, 인스타는 글자 카드로 올라가요" in reply


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
    assert deal.product.image_url is None  # 주소 없는 사진 (스레드는 글만)
    with Image.open(io.BytesIO(photo)) as im:
        r, g, b = im.convert("RGB").getpixel((500, 500))
        assert abs(r - 120) < 20 and abs(g - 80) < 20 and abs(b - 60) < 20
    # 블로그 내보내기: Blog-Auto 는 photo_kind 'product' 만 상품 사진으로 씀
    bot.export_published(deal, photo)
    row = json.loads((settings.data_dir / "published_deals.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["photo_kind"] == "product" and row["photo_src"] == "owner" and Path(row["photo_file"]).exists()
    assert Path(row["photo_file"]).parent == settings.data_dir / "media" / "export"
    # 사진 파일이 사라졌으면(정리됨) 예전 순서대로 찾는다 — 스토어 페이지·크롬 (뽐뿌 썸네일은 여전히 안 씀)
    for f in _owner_files(settings):
        f.unlink()
    opened.clear()
    deal.product.image_url = BOARD_THUMB
    assert await bot.deal_photo(deal) is None and opened == [STORE_URL, STORE_URL] and BOARD_THUMB not in opened


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
    """주인 사진은 주소가 없다(image_url None). 캡션이 길어 글로 보내게 되면 미리보기를 꺼야 한다 — 안 끄면 텔레그램이 본문 첫
    링크(스토어)의 검사 안 한 사진을 붙인다. 짧으면 그 사진을 그대로 보낸다."""
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


async def test_naver_deal_without_photo_gets_a_text_card(bot) -> None:  # type: ignore[no-untyped-def]
    assert bot.settings.instagram.no_photo == "skip"  # 설정은 그대로 (쿠팡 등은 지금처럼 안 올림)
    spy = _Spy(bot)
    bot._fetch_image = lambda *a, **k: (_ for _ in ()).throw(AssertionError("사진을 받으면 안 됨"))
    d = Deal(_product(), DealVerdict(is_deal=True), affiliate_url=LINK)
    photo = await bot.deal_photo(d)  # 스토어 막힘(conftest) · 크롬 못 엶 · 뽐뿌 썸네일은 안 씀
    assert photo is None and d.product.image_url is None
    await bot._publish_side_channels(d, photo=photo, deal_no=7)
    assert spy.saved == [None] and len(spy.posted) == 1 and (spy.posted[0] or "").startswith("https://bot.example.com/media/")
    assert not any("skipped" in m for m in _ig_events(bot))
    card = bot.settings.data_dir / "media" / (spy.posted[0] or "").rsplit("/", 1)[-1]
    with Image.open(card) as im:
        assert im.size == (1080, 1350)
    # 쿠팡 딜은 그대로 '확인된 상품 사진 없음' 으로 안 올림
    c = Deal(Product(source="goldbox", product_id="coupang:9", shop="coupang", name="텀블러", price=29900,
                     url="https://www.coupang.com/vp/products/9"), DealVerdict(is_deal=True))
    await bot._publish_side_channels(c, photo=None, deal_no=8)
    assert len(spy.saved) == 1 and any("coupang:9 skipped: 확인된 상품 사진 없음" in m for m in _ig_events(bot))
    assert bot._ig_skip_reason(None, deal=d) is None and bot._ig_skip_reason(None, deal=c) == "확인된 상품 사진 없음"


async def test_naver_owner_photo_becomes_the_instagram_card_photo(bot) -> None:  # type: ignore[no-untyped-def]
    qid = _waiting(bot)
    await bot.attach_link(qid, LINK, photo=_jpeg(1000, 1000, (120, 80, 60)))
    deal = bot.db.get_queue_item(qid).deal
    spy = _Spy(bot)
    photo = await bot.deal_photo(deal)
    assert photo is not None
    await bot._publish_side_channels(deal, photo=photo, deal_no=9)
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
