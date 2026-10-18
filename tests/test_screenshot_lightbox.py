"""주인 딜 사진이 쇼핑몰 '사진 보기' 화면(라이트박스) 캡처일 때 가운데 상품 사진만 남기기 (주인 10/18 21:10, 블로그 #81 폴햄 긴팔티).

주인이 보낸 캡처: 위·아래 검은 띠 + 어둡게 눌린 상품 사진(‹ › 화살표) + 아래 미리보기 사진 5칸. 예전에는 띠 일부만 걷히고
검은 띠·미리보기 줄이 블로그 사진에 그대로 들어갔다.
- fixtures/owner_lightbox_81.jpg: 발행된 #81 글을 주인이 캡처한 화면에서 잘라낸 그림 (828×1424, 사진은 y 276~1246 · x 8~828)
- 흰 띠·검은 띠·미리보기 줄이 가까운 화면·정사각 사진도 가운데 사진만
- 진짜 상품 사진(흰 배경·검은 배경 위 상품, 밤하늘처럼 위가 거의 검은 사진)은 예전과 똑같이 (라이트박스로 보지 않음)
Blog-Auto tests/test_lightbox_crop.py 와 같은 그림·같은 기대값 (media.py 와 screenshot.py 는 같이 고친다).
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dealbot.media import imagecheck, screenshot

FIXTURE = Path(__file__).parent / "fixtures" / "owner_lightbox_81.jpg"


def _jpeg(img: Image.Image) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def _lightbox(bg=(0, 0, 0), *, thumbs: bool = True, gap: int = 130, light: bool = False,
              w: int = 1080, h: int = 2340, top: int = 420, ph: int = 1350) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """사진 보기 화면 캡처: 상태바·닫기 X·'1 / 5' + 어둡게 눌린 사진(어두운 배경지 + 가운데 사람, 흰 ‹ ›) + 미리보기 5칸."""
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    fg = (240, 240, 240) if sum(bg) < 384 else (20, 20, 20)
    d.text((40, 30), "9:41", fill=fg)
    d.text((w - 140, 30), "LTE 87%", fill=fg)
    d.line([(w - 90, 150), (w - 50, 190)], fill=fg, width=4)
    d.line([(w - 90, 190), (w - 50, 150)], fill=fg, width=4)
    d.text((w // 2 - 15, 165), "1 / 5", fill=fg)
    rnd = random.Random(3)
    for y in range(top, top + ph, 10):
        for x in range(0, w, 10):
            if light:
                v = rnd.randint(150, 230)
            elif abs(x - w / 2) < w * 0.22 and y > top + ph * 0.15:
                v = rnd.randint(40, 130)
            else:
                v = rnd.randint(12, 40)  # 띠 색(검정)에 가까운 어두운 배경지
            d.rectangle([x, y, x + 9, y + 9], fill=(v, max(0, v - 5), max(0, v - 10)))
    cy = top + ph // 2
    d.line([(90, cy - 40), (60, cy), (90, cy + 40)], fill=(235, 235, 235), width=5)
    d.line([(w - 90, cy - 40), (w - 60, cy), (w - 90, cy + 40)], fill=(235, 235, 235), width=5)
    if thumbs:
        ty, side = top + ph + gap, 170
        for i in range(5):
            x = 30 + i * 210
            for yy in range(ty, ty + side, 10):
                for xx in range(x, x + side, 10):
                    v = rnd.randint(30, 160)
                    d.rectangle([xx, yy, xx + 9, yy + 9], fill=(v, v - 10, v - 20))
            if i == 0:
                d.rectangle([x - 4, ty - 4, x + side + 4, ty + side + 4], outline=(255, 255, 255), width=3)
    return img, (0, top, w, top + ph)


def _product_on(bg, w: int = 900, h: int = 1600) -> Image.Image:
    """한 가지 색 배경 위 가운데 상품 (가장자리까지 배경) — 라이트박스가 아님."""
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    rnd = random.Random(5)
    for y in range(int(h * 0.2), int(h * 0.8), 10):
        for x in range(int(w * 0.22), int(w * 0.78), 10):
            if ((x - w / 2) / (w * 0.28)) ** 2 + ((y - h / 2) / (h * 0.3)) ** 2 < 1:
                v = rnd.randint(60, 200)
                d.rectangle([x, y, x + 9, y + 9], fill=(v, v - 20, v - 40))
    return img


def _night(w: int = 900, h: int = 1600) -> Image.Image:
    """위 45% 가 거의 검은 밤하늘(결이 있음), 아래가 곧은 수평선 아래 풍경인 진짜 사진."""
    sky = Image.effect_noise((w, int(h * 0.45)), 5).point(lambda v: max(0, v - 120)).convert("RGB")
    ground = Image.effect_noise((w, h - sky.height), 25).point(lambda v: v // 2 + 10).convert("RGB")
    img = Image.new("RGB", (w, h))
    img.paste(sky, (0, 0))
    img.paste(ground, (0, sky.height))
    return img


def _old_tidy(img: Image.Image, monkeypatch: pytest.MonkeyPatch):
    with monkeypatch.context() as m:
        m.setattr(screenshot, "_lightbox_box", lambda _img: None)
        return screenshot.tidy(img)


def _black_rows(img: Image.Image, ys) -> int:
    return sum(1 for y in ys if max(hi for _, hi in img.crop((0, y, img.width, y + 1)).getextrema()) <= 12)


def test_owner_lightbox_capture_81_keeps_only_the_product_photo() -> None:
    src = Image.open(FIXTURE).convert("RGB")
    assert src.size == (828, 1424)
    out, what = screenshot.tidy(src)
    assert what == "photo"
    assert 800 <= out.width <= 828 and 940 <= out.height <= 1000, out.size  # 사진 820×970 (검은 띠·회색 테두리 없음)
    assert _black_rows(out, range(0, 8)) == 0 and _black_rows(out, range(out.height - 8, out.height)) == 0
    jpeg, why = imagecheck.owner_photo(FIXTURE.read_bytes(), crop=True)  # 대시보드·텔레그램으로 받은 그대로
    assert why == "" and jpeg is not None
    with Image.open(io.BytesIO(jpeg)) as im:
        assert 1.1 <= im.height / im.width <= 1.25  # 길쭉한 캡처가 아니라 상품 사진 비율


@pytest.mark.parametrize("kw", [
    {}, {"bg": (10, 10, 10)}, {"bg": (255, 255, 255), "light": True}, {"bg": (255, 255, 255)},
    {"thumbs": False}, {"gap": 30}, {"ph": 1080}, {"w": 828, "h": 1424, "top": 150, "ph": 1000, "thumbs": False},
])
def test_lightbox_captures_lose_bands_and_thumbnail_strip(kw) -> None:
    img, want = _lightbox(**kw)
    for src in (img, _jpeg(img)):
        box = screenshot._lightbox_box(src)
        assert box is not None and all(abs(a - b) <= 6 for a, b in zip(box, want, strict=True)), (kw, box, want)
        out, what = screenshot.tidy(src)
        assert what == "photo" and abs(out.height - (want[3] - want[1])) <= 12, (kw, out.size)


def test_real_product_photos_are_not_taken_for_a_lightbox(monkeypatch: pytest.MonkeyPatch) -> None:
    photos = [_product_on((255, 255, 255)), _product_on((0, 0, 0)), _product_on((22, 22, 22)), _night()]
    photos += [_jpeg(p) for p in photos]
    for p in photos:
        assert screenshot._lightbox_box(p) is None
        new, old = screenshot.tidy(p), _old_tidy(p, monkeypatch)
        assert new[1] == old[1] and new[0].size == old[0].size  # 예전과 똑같이
    # 가장자리까지 꽉 찬 사진 · 정사각 사진은 여전히 그대로
    square = Image.new("RGB", (1000, 1000), (120, 90, 60))
    assert screenshot._lightbox_box(square) is None and screenshot.tidy(square) == (square, "")
    # 같은 사진을 두 장 이어 붙인 그림(가운데 검은 띠)은 한 장만 남기지 않는다
    one, _ = _lightbox(thumbs=False, top=0, ph=1100, h=1100)
    pair = Image.new("RGB", (1080, 2340), (0, 0, 0))
    pair.paste(one, (0, 0))
    pair.paste(one, (0, 1240))
    assert screenshot._lightbox_box(pair) is None


def test_lightbox_finder_failure_falls_back_to_the_old_photo_box(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_img):
        raise ValueError("x")

    monkeypatch.setattr(screenshot, "_lightbox_box", boom)
    img, _ = _lightbox()
    out, what = screenshot.tidy(img)
    assert what in ("photo", "bars") and out.size != img.size
