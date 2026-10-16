"""종이 카드 (instagram.style: P — 주인 결정 10/16 '1+2 섞은 안', 가격 C: Bodoni 숫자 + 빨간 펜 밑줄).

- 크기 1080×1350, 사진 있음·없음·깨짐·아주 작음·아주 넓음·흰 바탕·투명 PNG·한 색뿐인 그림
- 맨 처음 그리는 글 = 고지 ('광고' 칩 + 몰 고지 그대로), spec.texts() 첫 글도 '광고 · …'
- '#번호' 와 '프로필 링크 → 핫딜 페이지' 는 카드에 한 번 (포스트잇). 번호가 없거나 스레드·블로그 카드면 둘 다 없음
- 긴 이름·빠진 줄에도 글이 카드 밖으로 나가지 않음, 가장 작은 글 30px (360px 화면에서 읽힘)
- 같은 딜이면 같은 그림 (난수는 딜에서 만든 씨앗으로만)
- 비교 가격엔 취소선이 없고, 말은 데이터로만 (평소/최저가 메모는 CardInfo 의 비교 근거에서)
"""

from __future__ import annotations

import io
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from dealbot.media import card_paper, card_slides
from dealbot.media.card import NEUTRAL_CTA, CardInfo, DealCard
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher import insta_style
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry

ROOT = Path(__file__).resolve().parent.parent
REG = ShopRegistry()
LONG = "[1+1] 크리넥스 3겹 데코앤소프트 화이트 천연펄프 두루마리 화장지 30m 30롤 x 2팩 (총 60롤) 대용량 생활필수품 초특가 묶음 구성"


@pytest.fixture
def r() -> TemplateRenderer:
    return TemplateRenderer(ROOT / "templates", "Asia/Seoul", {"telegram_url": "https://t.me/x"})


def _img(w: int, h: int, color: Any = (90, 140, 200), mode: str = "RGB", fmt: str = "JPEG", spot: bool = True) -> bytes:
    im = Image.new(mode, (w, h), color)
    if spot:  # 상품처럼 보이는 가운데 덩어리
        ImageDraw.Draw(im).ellipse((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=(200, 60, 40) + ((255,) if mode == "RGBA" else ()))
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


PHOTOS = {
    "photo": _img(1000, 800, (120, 130, 110)),
    "white_cutout": _img(600, 1000, (255, 255, 255)),
    "tiny": _img(120, 120, (240, 160, 30)),
    "wide": _img(1400, 420, (110, 140, 100)),
    "rgba": _img(600, 600, (0, 0, 0, 0), mode="RGBA", fmt="PNG"),
    "blank": _img(600, 600, (252, 252, 252), spot=False),
    "none": None,
    "broken": b"not an image",
}


def _deal(name: str = "크리넥스 3겹 30m 30롤", **kw: Any) -> Deal:
    p = dict(source="goldbox", product_id="coupang:1", shop="coupang", name=name, price=15900, original_price=25800,
             url="https://www.coupang.com/vp/products/1", category="생활용품>화장지>두루마리", is_rocket=True,
             is_free_shipping=True, rating=4.8, review_count=12431)
    p.update(kw)
    return Deal(Product(**p),  # type: ignore[arg-type]
                DealVerdict(is_deal=True, discount_rate=38.4, market_price=22900, below_market_pct=30.6, low_price=16900,
                            history_days=30, sample_count=12),
                affiliate_url="https://link.coupang.com/a/1", detected_at=datetime(2026, 10, 15, 5, 12, tzinfo=UTC))


def _naver() -> Deal:
    return Deal(Product(source="ppomppu", product_id="naver:1", shop="naver", name="데켓 주물 IH 오발쿡플레이트 31cm", price=60020,
                        original_price=89000, url="https://brand.naver.com/daekket/products/1", category="주방용품>프라이팬>그릴팬",
                        shipping="무료배송"), DealVerdict(is_deal=True), affiliate_url="https://naver.me/x")


def _info(r: TemplateRenderer, deal: Deal | None = None, deal_no: int | None = 71) -> CardInfo:
    deal = deal or _deal()
    return insta_style.card_info(deal, r, shop=REG.get(deal.product.shop), deal_no=deal_no)


class Drawn:
    """ImageDraw.text 로 그린 글 (글, 1배 글 크기, 그 그림 안에 다 들어갔나) — 그린 차례대로."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.items: list[tuple[str, float, bool]] = []
        orig = ImageDraw.ImageDraw.text

        def spy(d: ImageDraw.ImageDraw, xy: Any, t: str, *a: Any, **kw: Any) -> None:
            font = kw.get("font")
            size = getattr(font, "size", 0) / card_paper.SS if isinstance(font, ImageFont.FreeTypeFont) else 0
            box = d.textbbox(xy, t, font=font, anchor=kw.get("anchor"))
            w, h = d.im.size
            inside = box[0] >= -2 and box[1] >= -2 and box[2] <= w + 2 and box[3] <= h + 2
            self.items.append((t, size, inside))
            orig(d, xy, t, *a, **kw)

        monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)

    @property
    def texts(self) -> list[str]:
        return [t for t, _, _ in self.items]

    def count(self, part: str) -> int:
        return sum(part in t for t in self.texts)


def _render(info: CardInfo, photo: bytes | None, tmp_path: Path) -> Image.Image:
    raw = DealCard(tmp_path, layout="P").render(_deal(), photo, info=info)
    return Image.open(io.BytesIO(raw))


# ---------------------------------------------------------------- 크기·사진
@pytest.mark.parametrize("kind", list(PHOTOS))
def test_card_is_1080x1350_jpeg_for_any_photo(r: TemplateRenderer, tmp_path: Path, kind: str) -> None:
    for info in (_info(r), _info(r, _deal(LONG)), _info(r, _naver(), deal_no=None)):
        im = _render(info, PHOTOS[kind], tmp_path)
        assert im.format == "JPEG" and im.size == (1080, 1350)


def test_photo_modes_pick_a_fitting_treatment() -> None:
    """흰 바탕 상품 사진 → 색판 위 오린 상품, 아주 작거나 너무 길쭉한 사진 → 색판 위 인화지 (흐리게 늘이거나 잘라 내지 않음)."""
    def mode(kind: str) -> str:
        img = card_paper.open_photo(PHOTOS[kind])
        assert img is not None
        return card_paper.photo_mode(img, card_paper.S(1020), card_paper.S(640))

    assert mode("photo") == "cover"
    assert mode("white_cutout") == "cutout" and mode("rgba") == "cutout"  # 투명 PNG 는 흰 바탕에
    assert mode("tiny") == "framed" and mode("wide") == "framed"
    assert card_paper.open_photo(b"broken") is None and card_paper.open_photo(None) is None


def test_same_deal_same_image_and_different_deals_differ(r: TemplateRenderer) -> None:
    """종이 결·테이프·기울기는 딜에서 만든 씨앗으로만 (예전 시안은 Image.effect_noise 라 그릴 때마다 달랐음)."""
    info = _info(r)
    for kind in ("photo", "white_cutout", "none"):
        spec = card_paper.deal_spec(info, PHOTOS[kind])
        a, b = card_paper.render(spec).tobytes(), card_paper.render(spec).tobytes()
        assert a == b, kind
    other = card_paper.render(card_paper.deal_spec(replace(info, deal_no=72, name="다른 상품"), PHOTOS["photo"])).tobytes()
    assert other != card_paper.render(card_paper.deal_spec(info, PHOTOS["photo"])).tobytes()


# ---------------------------------------------------------------- 고지·번호 안내
@pytest.mark.parametrize("kind", ["photo", "none"])
def test_disclosure_is_the_first_thing_drawn(r: TemplateRenderer, tmp_path: Path, kind: str,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    for deal, ad in ((_deal(), "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다"),
                     (_naver(), "광고 · 네이버 쇼핑 커넥트 활동으로 수수료를 받습니다")):
        info = _info(r, deal)
        spec = card_paper.deal_spec(info, PHOTOS[kind])
        assert spec.texts()[0] == ad == info.ad  # 몰 고지 그대로 (card_info → ad_line)
        drawn = Drawn(monkeypatch)
        card_paper.render(spec)
        assert drawn.texts[:2] == ["광고", ad.removeprefix("광고 · ")]  # 맨 위 '광고' 칩 + 나머지
        monkeypatch.undo()
    generic = replace(_info(r), ad="", disclosure="")
    assert card_paper.deal_spec(generic, None).texts()[0] == "광고 · 제휴 링크로 수수료를 받아요"


@pytest.mark.parametrize("kind", ["photo", "white_cutout", "none"])
def test_number_and_profile_guide_appear_once(r: TemplateRenderer, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = card_paper.deal_spec(_info(r, deal_no=1532), PHOTOS[kind])
    assert spec.tag == "#1532" and spec.footer == "프로필 링크 → 핫딜 페이지"
    drawn = Drawn(monkeypatch)
    card_paper.render(spec)
    assert drawn.count("#1532") == 1 and sum(t.startswith("#") for t in drawn.texts) == 1
    assert drawn.count("프로필 링크") == 1 and drawn.count("핫딜 페이지") == 1  # 포스트잇 두 줄 '프로필 링크 →' / '핫딜 페이지'
    assert "핫딜 번호" in drawn.texts


@pytest.mark.parametrize("cta", [NEUTRAL_CTA, None])
def test_cards_without_a_page_number_have_no_number_or_profile_guide(r: TemplateRenderer, cta: str | None,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """스레드·블로그 카드(cta): 구매 링크는 글 안에 → '#번호'·'프로필' 없이 바닥에 NEUTRAL_CTA.
    번호가 없는 인스타 카드: 핫딜 페이지에서 찾을 수 없으니 '프로필 링크 → 핫딜 페이지' 도 쓰지 않는다."""
    info = replace(_info(r, deal_no=5 if cta else None), cta=cta)
    spec = card_paper.deal_spec(info, None)
    assert spec.tag == "" and spec.footer == NEUTRAL_CTA
    assert not any("프로필" in t for t in spec.texts()) and not any(t.startswith("#") for t in spec.texts())
    drawn = Drawn(monkeypatch)
    card_paper.render(spec)
    assert drawn.count("프로필") == 0 and not any(t.startswith("#") for t in drawn.texts) and NEUTRAL_CTA in drawn.texts


# ---------------------------------------------------------------- 글 배치
@pytest.mark.parametrize("kind", ["photo", "none", "tiny"])
def test_long_names_and_all_text_stay_inside_and_readable(r: TemplateRenderer, kind: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """아주 긴 이름은 두 줄(사진 없으면 네 줄)까지 + '…', 어떤 글도 그림 밖으로 안 나감, 가장 작은 글 30px (360px 에서 10px)."""
    info = replace(_info(r, _deal(LONG, price=1299000), deal_no=12345), low_label="우리가 본 최근 30일 중 가장 낮음 (10/15 14시 기준)")
    drawn = Drawn(monkeypatch)
    img = card_paper.render(card_paper.deal_spec(info, PHOTOS[kind]))
    assert img.size == (1080, 1350)
    assert all(inside for _, _, inside in drawn.items), [t for t, _, ok in drawn.items if not ok]
    assert min(size for _, size, _ in drawn.items if size) >= card_paper.MIN_TEXT
    assert "1,299,000" in drawn.texts and "원" in drawn.texts
    name_lines = [t for t in drawn.texts if len(t) > 6 and t.rstrip("…").strip() in info.name]
    assert 2 <= len(name_lines) <= 4 and name_lines[0] == info.name[:len(name_lines[0])]
    if not name_lines[-1].endswith("…"):  # 다 들어갔으면 이름 전체
        assert " ".join(name_lines) == info.name
    assert "(10/15 14시 기준)" in drawn.texts  # 기록의 기준 시각은 자르지 않고 따로


def test_missing_rows_still_make_a_full_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """비교·기록·단가·배송이 없는 딜: 가격과 판매처만 — 근거 없는 말(평소·최저가·%↓)을 지어내지 않는다."""
    info = CardInfo(name="이름만 있는 상품", price=9900, deal_no=3, ad="광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다")
    spec = card_paper.deal_spec(info, None)
    assert spec.big_note == "" and spec.items == []
    drawn = Drawn(monkeypatch)
    assert card_paper.render(spec).size == (1080, 1350)
    joined = "\n".join(drawn.texts)
    assert "9,900" in joined and not any(w in joined for w in ("평소", "최저가", "%↓", "역대"))
    no_price = card_paper.render(card_paper.deal_spec(replace(info, price=None, shop_name="쿠팡"), PHOTOS["photo"]))
    assert no_price.size == (1080, 1350)


def test_pen_note_and_facts_come_from_the_card_data(r: TemplateRenderer) -> None:
    """빨간 손글씨 메모: 1개당 가격이 먼저 (데이터 unit_parts), 없으면 비교 폭 — 쿠팡 최저가 비교면 '쿠팡 최저가보다', 평소 가격이면 '평소보다'.
    사실 줄은 같은 근거(ref_text·low_label·판매처·배송)."""
    info = _info(r)  # 크리넥스 30롤 — 1롤당 530원, 쿠팡 최저가 22,900원
    spec = card_paper.deal_spec(info, None)
    assert spec.big == "15,900원" and spec.big_note == "1롤당 530원"
    assert spec.items[0] == ("쿠팡 최저가 22,900원", "31%↓", "")
    assert ("우리가 본 최근 30일 중 가장 낮음", "", "10/15 14시 기준") in spec.items
    assert ("쿠팡 · 무료 로켓배송", "", "") in spec.items
    shampoo = _info(r, _deal("도브 샴푸 680ml"))
    assert card_paper.deal_spec(shampoo, None).big_note.startswith("100mL당")
    plain = replace(shampoo, unit=None)
    assert card_paper.deal_spec(plain, None).big_note == "쿠팡 최저가보다 31%↓"
    usual = replace(plain, ref_label="평소 가격", ref_text="평소 22,000원대")
    assert card_paper.deal_spec(usual, None).big_note == "평소보다 31%↓"
    naver = _info(r, _naver())
    assert card_paper.deal_spec(naver, None).items[0] == ("정가 89,000원", "", "33%↓")  # 표시 정가는 약한 근거 — 빨간 글 없이


def test_comparison_percent_is_said_once_and_never_struck(r: TemplateRenderer, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.media import card_variants

    def no_strike(*a: object, **kw: object) -> int:
        raise AssertionError("paper card struck a comparison price")

    monkeypatch.setattr(card_variants, "_strike", no_strike)
    info = replace(_info(r, _deal("도브 샴푸")), unit=None)
    drawn = Drawn(monkeypatch)
    card_paper.render(card_paper.deal_spec(info, PHOTOS["photo"]))
    assert drawn.count("31%↓") == 1  # 메모 '쿠팡 최저가보다 31%↓' 가 있으면 사실 줄의 빨간 덧말은 빠짐
    assert "쿠팡 최저가 22,900원" in drawn.texts


def test_validator_sees_what_the_card_draws(r: TemplateRenderer) -> None:
    info = _info(r)
    spec = card_paper.deal_spec(info, None)
    caption = "광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다\n크리넥스"
    assert not insta_style.validate(caption, slides=[spec], allowed=insta_style.backed_phrases(info))
    assert insta_style.validate(caption, slides=[spec])  # '쿠팡 최저가' 근거를 안 넘기면 걸림
    bad = replace(spec, ad="")
    assert "1번째 장에 광고·수수료 고지가 없음" in insta_style.validate(caption, slides=[bad], allowed=["쿠팡 최저가"])


# ---------------------------------------------------------------- 양식 스위치·캐러셀
def test_style_switch_routes_p_to_the_paper_card_and_keeps_b(r: TemplateRenderer, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.config import InstagramConfig

    assert InstagramConfig(style="p").style == "P"  # type: ignore[arg-type]
    info = _info(r)
    first = card_slides.drawn_first_part("P", info, None)
    assert first.texts()[0].startswith("광고 · ") and getattr(first, "tag", "") == "#71"
    old = card_slides.drawn_first_part("B", info, None)
    assert getattr(old, "footer", "") == "구매 링크: 프로필 링크 → 핫딜 페이지 #71"  # 되돌리기용 B 는 그대로
    called: list[str] = []
    real = card_paper.render
    monkeypatch.setattr(card_paper, "render", lambda spec: called.append(spec.kind) or real(spec))
    DealCard(tmp_path, layout="P").render(_deal(), None, info=info)
    DealCard(tmp_path, layout="B").render(_deal(), None, info=info)
    assert called == ["deal"]


def test_caption_card_hint_matches_the_style(r: TemplateRenderer) -> None:
    info = _info(r)
    deal = _deal()
    p = insta_style.caption_context(deal, info, shop=REG.get("coupang"), style="P")["notes"]
    b = insta_style.caption_context(deal, info, shop=REG.get("coupang"))["notes"]
    assert "1롤당 가격도 카드에 적어 두었어요" in p and "1롤당 가격은 카드 표 둘째 줄에 있어요" in b
    assert insta_style.template_for("P", "x") == "deal_instagram_b.j2"


def test_carousel_slides_render_in_the_paper_style(r: TemplateRenderer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """표지·딜 장·단가표 장·마지막 장 모두 1080×1350, 고지가 맨 처음. 딜 장은 포스트잇 '#번호' 한 번 + 장 번호."""
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from dealbot.publisher.ig_scheduler import InstagramScheduler

    card = DealCard(tmp_path, layout="P")
    local = datetime(2026, 10, 16, 20, 15, tzinfo=ZoneInfo("Asia/Seoul"))
    picks = [insta_style.Pick(deal=d, info=_info(r, d, deal_no=no), deal_no=no, photo=ph)
             for d, no, ph in ((_deal(), 12, PHOTOS["photo"]), (_deal(LONG), 13, None))]
    specs = InstagramScheduler.top5_specs(SimpleNamespace(bot=SimpleNamespace(card=card)), picks, local)  # type: ignore[arg-type]
    assert [s.kind for s in specs] == ["cover", "deal", "deal", "end"]
    assert [s.tag for s in specs[1:3]] == ["#12", "#13"] and [s.page for s in specs[1:3]] == ["2/4", "3/4"]
    rows = [insta_style.UnitRow(title="휴지·물티슈", kind="휴지", label="1롤당", value=v, name=n, price=p, deal_no=no, product_id=pid)
            for v, n, p, no, pid in ((498, "크리넥스 30롤", 14940, 81, "c:1"), (530, "잘풀리는집 30롤", 15900, None, "c:2"))]
    fake = SimpleNamespace(bot=SimpleNamespace(card=card, renderer=SimpleNamespace(low_min_samples=5),
                                               registry=SimpleNamespace(get=lambda k: None)),
                           cfg=SimpleNamespace(unit_carousel=SimpleNamespace(baseline_days=60)))
    unit = InstagramScheduler.unit_specs(fake, {"휴지 1롤당": rows}, [("휴지", "1롤당", 540, 7)], [], local,  # type: ignore[arg-type]
                                         local.replace(day=10))
    for spec in [*specs, *unit]:
        drawn = Drawn(monkeypatch)
        img = card_slides.render(card, spec)
        assert img.size == (1080, 1350), spec.kind
        assert drawn.texts[0] == "광고" and drawn.texts[1] == spec.ad.removeprefix("광고 · "), spec.kind
        assert all(inside for _, _, inside in drawn.items), (spec.kind, [t for t, _, ok in drawn.items if not ok])
        if spec.kind == "deal":
            assert drawn.count("프로필 링크") == 1 and sum(t.startswith("#") for t in drawn.texts) == 1
        monkeypatch.undo()
    assert insta_style.validate("광고 · 쿠팡 파트너스 활동으로 수수료를 받습니다\nTOP", slides=[*specs, *unit],
                                allowed=["쿠팡 최저가"]) == []


def test_reels_keep_their_own_frames_under_the_paper_style(r: TemplateRenderer, tmp_path: Path) -> None:
    """릴스(9:16)는 다시 그리지 않음 — P 양식이어도 예전 릴스 화면 (안전 영역 규칙 그대로)."""
    card = DealCard(tmp_path, layout="P")
    info = _info(r)
    for kind in ("reel_hook", "reel_deal", "reel_end"):
        spec = card_slides.SlideSpec(kind=kind, ad=info.ad, title=info.name, big="15,900원", rows=[("비교", "쿠팡 최저가 22,900원")])
        assert card_slides.render(card, spec).size == (1080, 1920)


def test_fonts_and_licenses_are_bundled() -> None:
    """글꼴은 저장소(패키지 데이터)에 OFL 라이선스와 함께 — 서버 글꼴(나눔)에 기대지 않음."""
    d = card_paper.FONT_DIR
    for name in card_paper.FONT_FILES.values():
        assert (d / name).exists(), name
        assert isinstance(card_paper._font(next(k for k, v in card_paper.FONT_FILES.items() if v == name), 40),
                          ImageFont.FreeTypeFont)
    for lic in ("LICENSE-Pretendard.txt", "LICENSE-BodoniModa.txt", "LICENSE-GamjaFlower.txt"):
        assert "SIL Open Font License" in (d / lic).read_text(encoding="utf-8")
    assert sum(p.stat().st_size for p in d.iterdir()) < 6_000_000
    assert '"dealbot.media" = ["fonts/*"]' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert card_paper.covers("hand", "1롤당 530원") and not card_paper.covers("hand", "똠")  # 부분 글꼴 밖 글자 → Pretendard
    assert card_paper.hand("뷁똠") == "md"
