"""데런픽 룩 카드 새 디자인 A·B·C (media/look_styles, 주인 10/26 '카드가 건성건성' → 시안 셋, instagram.look.card_style).

세 갈래 모두 같은 규칙을 지키는지 본다: 모든 장 '광고' 표시 · 글은 80px 안 · 오른쪽 위 210×110 비움 · 셀럽 사진 길 없음
(글 폴더 밖 사진이면 안 그림) · 상품 장마다 가격·판매처·기준 날짜 · '착용 제품 아님' · 주소 글자 없음 · 끝 장 '프로필 링크'.
기본값(current)은 처음 카드 그대로.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from dealbot.config import InstagramLookConfig
from dealbot.growth import look_share
from dealbot.media import card_look, look_styles
from dealbot.media.card_look import LookDeck, LookItem, won
from test_ig_look_share import _look, _look_live
from test_ig_schedule import _plan, at, bot, clock, tick  # noqa: F401 — 같은 봇·시계 준비

STYLES = ("A", "B", "C")
LONG = "[당일출고] 여리여리 루즈핏 브이넥 케이블 꽈배기 울 혼방 롱 슬리브 니트 가디건 아이보리"


def _studio(path: Path, color: tuple[int, int, int], shape: str) -> str:
    """흰 바탕 상품 사진 (누끼가 되는 사진)."""
    im = Image.new("RGB", (800, 1000), (255, 255, 255))
    d = ImageDraw.Draw(im)
    if shape == "jacket":
        d.polygon([(200, 180), (600, 180), (700, 820), (100, 820)], fill=color)
    else:
        d.rounded_rectangle((220, 300, 580, 760), radius=120, fill=color)
    im.save(path, "JPEG", quality=92)
    return str(path)


def _busy(path: Path) -> str:
    """바탕이 복잡한 연출 사진 (줄무늬 + 얼룩)."""
    im = Image.new("RGB", (700, 1050), (120, 100, 80))
    d = ImageDraw.Draw(im)
    for i in range(0, 1050, 30):
        d.rectangle((0, i, 700, i + 14), fill=(60 + i % 120, 90, 140 - i % 60))
    d.ellipse((150, 300, 550, 800), fill=(40, 40, 44))
    im.save(path, "JPEG", quality=92)
    return str(path)


@pytest.fixture(scope="module")
def deck_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("look_deck")


@pytest.fixture(scope="module")
def deck(deck_dir: Path) -> LookDeck:
    items = [
        LookItem(name=LONG, price=89000, mall="에이블리", image=_studio(deck_dir / "01.jpg", (20, 20, 22), "jacket"), piece="블랙 레더 재킷"),
        LookItem(name="연청 와이드 데님 팬츠", price=42900, mall="지그재그", image=_busy(deck_dir / "02.jpg"), piece="연청 와이드 데님"),
        LookItem(name="베이직 울 그레이 니트 비니", price=19800, mall="무신사", image=_studio(deck_dir / "03.jpg", (90, 90, 96), "hat"),
                 piece="그레이 니트 비니"),
    ]
    return LookDeck(person="카리나", kicker="공항 패션 · 10월 26일 글", title=["카리나 공항 패션,", "비슷한 옷 3가지"],
                    sub="카리나 착용 제품 아님 · 비슷한 상품이에요", items=items, end_line="전체 글은 프로필 링크에서",
                    price_note="가격은 10/26 기준이에요", price_day="10/26", end_kicker="카리나 공항 패션 · 한눈에")


@pytest.fixture(scope="module")
def drawn(deck: LookDeck) -> dict[str, list[look_styles.Sheet]]:
    return {s: look_styles.sheets(deck, s) for s in STYLES}


def test_card_style_config_defaults_to_the_current_cards() -> None:
    assert InstagramLookConfig().card_style == "current"
    assert InstagramLookConfig(card_style="B").card_style == "B"
    with pytest.raises(ValidationError):
        InstagramLookConfig(card_style="Z")  # type: ignore[arg-type]


def test_default_render_is_unchanged(deck: LookDeck) -> None:
    a = card_look.render(deck)
    b = card_look.render(deck, "current")
    assert [x for x, _ in a] == [x for x, _ in b]


@pytest.mark.parametrize("style", STYLES)
def test_every_direction_keeps_the_hard_rules(style: str, deck: LookDeck, drawn: dict[str, list[look_styles.Sheet]]) -> None:
    sheets = drawn[style]
    assert len(sheets) == len(deck.items) + 2  # 표지 · 상품 장 · 끝 장
    arranged = look_styles.arrange(deck, style)
    for i, sh in enumerate(sheets):
        im = sh.final()
        assert im.size == (1080, 1350)
        # 오른쪽 위 210×110 (앱 장 번호 자리) 는 고르게 비어 있음
        corner = im.crop((1080 - 210, 0, 1080, 110)).convert("L").getextrema()
        assert corner[1] - corner[0] < 12, (style, i)
        texts = [d.text for d in sh.drawn]
        # '광고' 표시: 모든 장 왼쪽 위, 바탕과 또렷이 다름
        ads = [d for d in sh.drawn if d.text == card_look.AD]
        assert len(ads) == 1 and ads[0].box[0] < 160 and ads[0].box[1] < 140, (style, i)
        x0, y0, x1, y1 = (int(v) for v in ads[0].box)
        chip = im.crop((x0 - 8, y0 - 6, x1 + 8, y1 + 6)).convert("L").getextrema()
        assert chip[1] - chip[0] > 120
        assert card_look.BRAND in texts
        for d in sh.drawn:  # 글은 80px 안 · 오른쪽 위 비움 · 덱의 글(과 숫자)만 · 주소 글자 없음
            bx0, by0, bx1, by1 = d.box
            assert bx0 >= 79.5 and by0 >= 79.5 and bx1 <= 1000.5 and by1 <= 1270.5, (style, i, d.text, d.box)
            assert not (bx1 > 1080 - 210 and by0 < 110), (style, i, d.text)
            assert look_styles.allowed_text(deck, style, d.text), (style, i, d.text)
            assert not look_share.URL_RX.search(d.text), d.text
            assert "…" not in d.text, (style, i, d.text)  # 긴 상품 이름도 줄여서 두 줄 안 — '…' 없이
        # 그린 사진은 상품 사진뿐
        assert {p for p, _ in sh.photos} <= {it.image for it in deck.items}
    for n, it in enumerate(arranged.items, 1):  # 상품 장: 가격 · 판매처 · 기준 날짜 · 착용 제품 아님
        texts = [d.text for d in sheets[n].drawn]
        joined = " ".join(texts)
        num = won(it.price).removesuffix("원")
        assert num in texts and it.mall in joined and "10/26 기준" in joined, (style, n)
        assert "카리나 착용 제품 아님" in joined
    end = " ".join(d.text for d in sheets[-1].drawn)
    assert "프로필 링크" in end and "가격은 10/26 기준이에요" in end


@pytest.mark.parametrize("style", STYLES)
def test_photos_outside_the_post_folder_are_refused(style: str, deck: LookDeck, deck_dir: Path, tmp_path: Path) -> None:
    out = card_look.render(deck, style, photo_root=deck_dir)
    assert len(out) == 5 and all(alt for _, alt in out)
    stray = _studio(tmp_path / "celeb.jpg", (200, 180, 160), "hat")  # 글 폴더 밖 사진 (셀럽 사진이 끼어들 길)
    bad = LookDeck(**{**{f: getattr(deck, f) for f in LookDeck.__slots__}, "items": [*deck.items[:2], LookItem("x", 1000, "몰", stray)]})
    with pytest.raises(ValueError):
        card_look.render(bad, style, photo_root=deck_dir)
    assert not hasattr(deck, "person_image") and not hasattr(deck.items[0], "photo_of_person")  # 사람 사진 칸 자체가 없음


def test_same_deck_same_pictures(deck: LookDeck) -> None:
    a = card_look.render(deck, "C")
    b = card_look.render(deck, "C")
    assert [x for x, _ in a] == [x for x, _ in b]


def test_outfit_board_numbers_items_head_to_toe(deck: LookDeck) -> None:
    order = [it.piece for it in look_styles.arrange(deck, "B").items]
    assert order == ["그레이 니트 비니", "블랙 레더 재킷", "연청 와이드 데님"]
    assert look_styles.arrange(deck, "A") is deck
    alts = [alt for _, alt in card_look.render(deck, "B")]
    assert "베이직 울 그레이 니트 비니" in alts[1]  # 대체 텍스트도 같은 순서


def test_items_without_photo_get_a_type_plate_not_an_empty_box(deck: LookDeck) -> None:
    items = [*deck.items[:2], LookItem(name="와이드 슬랙스", price=0, mall="쇼핑커넥트", image=None, piece="슬랙스")]
    d2 = LookDeck(**{**{f: getattr(deck, f) for f in LookDeck.__slots__}, "items": items})
    for style in STYLES:
        sheets = look_styles.sheets(d2, style)
        page = sheets[3]
        assert any(d.text == "슬랙스" for d in page.drawn), style  # 빈 칸 대신 아이템 이름을 크게
        assert not page.photos


def test_photo_handling_studio_vs_busy(deck_dir: Path) -> None:
    from dealbot.media import look_photo as lp

    studio = lp.load(str(deck_dir / "01.jpg"))
    busy = lp.load(str(deck_dir / "02.jpg"))
    assert studio is not None and studio.clean and studio.cut  # 흰 바탕 → 오려서 색판 위에
    assert busy is not None and not busy.clean and not busy.cut  # 복잡한 바탕 → 오리지 않음
    assert lp.plate(studio, (400, 300), fill=(234, 230, 223)).size == (400, 300)
    assert lp.plate(busy, (900, 300)).size == (900, 300)  # 아주 길쭉한 칸: 자르지 않고 흐린 바탕 위에
    assert lp.load(str(deck_dir / "nope.jpg")) is None


async def test_post_uses_the_configured_card_style(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    seen: list[tuple[str, Any]] = []
    real = card_look.render

    def spy(deck: LookDeck, style: str = "current", photo_root: Any = None) -> list[tuple[bytes, str]]:
        seen.append((style, photo_root))
        return real(deck, style, photo_root)

    monkeypatch.setattr(card_look, "render", spy)
    _look_live(bot, monkeypatch)
    _plan(bot, {})
    bot.settings.instagram.look.card_style = "B"
    path = _look(bot, tmp_path)
    assert (await tick(bot, clock, at(10, 1)) or "").startswith("look #31: posted")
    assert seen and seen[0][0] == "B" and Path(seen[0][1]) == path.with_suffix("")


def test_texts_for_a_style_cover_its_fixed_words(deck: LookDeck) -> None:
    assert "Get the Look" in deck.texts("A") and "Get the Look" not in deck.texts()
    assert "모두 합쳐" in deck.texts("B")
    assert all(not re.search(r"https?://|www\.", t) for s in STYLES for t in deck.texts(s))
