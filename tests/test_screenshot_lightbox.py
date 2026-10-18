"""주인 딜 사진이 쇼핑몰 '사진 보기' 화면(라이트박스) 캡처일 때 가운데 상품 사진만 남기기 (주인 10/18 21:10, 블로그 #81 폴햄 긴팔티).

주인이 보낸 캡처: 위·아래 검은 띠 + 어둡게 눌린 상품 사진(‹ › 화살표) + 아래 미리보기 사진 5칸. 예전에는 띠 일부만 걷히고
검은 띠·미리보기 줄이 블로그 사진에 그대로 들어갔다. 주인 21:45 사진 원칙: 주인공(인물·상품)만, 둘레는 조금(8~12%) 남기고,
확실하지 않으면 덜 자른다 — 넘김 화살표도 걷는다(양옆을 똑같이 잘라서, 너무 많이 잘리면 배경으로 메워서).
- fixtures/owner_lightbox_81.jpg: 발행된 #81 글을 주인이 캡처한 화면에서 잘라낸 그림 (828×1424, 사진은 y 276~1246 · x 8~828)
- 흰 띠·검은 띠·미리보기 줄이 가까운 화면·정사각 사진도 가운데 사진만
- 진짜 상품 사진(흰 배경·검은 배경 위 상품, 밤하늘처럼 위가 거의 검은 사진)은 라이트박스로 보지 않음
- 흰 배경 상품 사진은 둘레를 조금 남기고, 복잡한 사진·밤 사진은 그대로. 셔츠 위 흰 로고·꽉 찬 네모는 화살표가 아님
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


def _lightbox(bg=(0, 0, 0), *, thumbs: bool = True, gap: int = 130, light: bool = False, arrow_x: int = 60,
              shirt: bool = False, w: int = 1080, h: int = 2340, top: int = 420,
              ph: int = 1350) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """사진 보기 화면 캡처: 상태바·닫기 X·'1 / 5' + 어둡게 눌린 사진(어두운 배경지 + 가운데 사람, 흰 ‹ ›) + 미리보기 5칸.
    shirt=True: 사람이 사진 폭을 거의 채우고(5~95%), 왼쪽 끝 고른 검은 셔츠 위에 흰 ‹ 모양 로고 (넘김 화살표 아님)."""
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
            elif shirt and x < w * 0.4 and top + ph * 0.4 <= y < top + ph * 0.6:
                v = 22  # 고른 검은 셔츠
            elif abs(x - w / 2) < w * (0.45 if shirt else 0.22) and y > top + ph * 0.15:
                v = rnd.randint(40, 130)
            else:
                v = rnd.randint(12, 40)  # 띠 색(검정)에 가까운 어두운 배경지
            d.rectangle([x, y, x + 9, y + 9], fill=(v, max(0, v - 5), max(0, v - 10)))
    cy = top + ph // 2
    a = arrow_x
    d.line([(a + 30, cy - 40), (a, cy), (a + 30, cy + 40)], fill=(235, 235, 235), width=5)
    if not shirt:
        d.line([(w - a - 30, cy - 40), (w - a, cy), (w - a - 30, cy + 40)], fill=(235, 235, 235), width=5)
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


def _black_rows(img: Image.Image, ys) -> int:
    return sum(1 for y in ys if max(hi for _, hi in img.crop((0, y, img.width, y + 1)).getextrema()) <= 12)


def _bright_edge_pixels(img: Image.Image) -> int:
    """왼쪽·오른쪽 15%, 높이 35~65% 안의 밝은 칸 수 (흰 넘김 화살표가 남았나)."""
    g = img.convert("L")
    w, h = g.size
    n = 0
    for box in ((0, int(h * 0.35), int(w * 0.15), int(h * 0.65)), (w - int(w * 0.15), int(h * 0.35), w, int(h * 0.65))):
        n += sum(g.crop(box).point(lambda v: 255 if v > 110 else 0).histogram()[255:])
    return n


def test_owner_lightbox_capture_81_keeps_only_the_person() -> None:
    src = Image.open(FIXTURE).convert("RGB")
    assert src.size == (828, 1424)
    assert _bright_edge_pixels(src.crop((8, 276, 828, 1246))) > 100  # 원본 사진에는 흰 ‹ 가 있음
    out, what = screenshot.tidy(src)
    assert what == "photo"
    # 검은 띠·회색 테두리·‹ 화살표를 걷고 사람(가로 309~639)을 가운데 둔 640×970 안팎 (짧은 변 600 아래로는 안 줄임)
    assert 600 <= out.width <= 700 and 940 <= out.height <= 1000, out.size
    assert _black_rows(out, range(0, 8)) == 0 and _black_rows(out, range(out.height - 8, out.height)) == 0
    assert _bright_edge_pixels(out) == 0
    jpeg, why = imagecheck.owner_photo(FIXTURE.read_bytes(), crop=True)  # 대시보드·텔레그램으로 받은 그대로
    assert why == "" and jpeg is not None
    with Image.open(io.BytesIO(jpeg)) as im:
        assert min(im.size) >= 600 and 1.3 <= im.height / im.width <= 1.6


@pytest.mark.parametrize("kw", [
    {}, {"bg": (10, 10, 10)}, {"bg": (255, 255, 255), "light": True}, {"bg": (255, 255, 255)},
    {"thumbs": False}, {"gap": 30}, {"ph": 1080}, {"w": 828, "h": 1424, "top": 150, "ph": 1000, "thumbs": False},
])
def test_lightbox_captures_lose_bands_thumbnails_and_arrows(kw) -> None:
    img, want = _lightbox(**kw)
    pw, ph = want[2] - want[0], want[3] - want[1]
    for src in (img, _jpeg(img)):
        box = screenshot._lightbox_box(src)
        assert box is not None and all(abs(a - b) <= 6 for a, b in zip(box, want, strict=True)), (kw, box, want)
        out, what = screenshot.tidy(src)
        assert what == "photo" and out.height <= ph + 2, (kw, out.size)  # 띠·미리보기 줄 없음
        if not kw.get("light"):  # 밝은 사진 위 흰 화살표는 또렷하지 않아 그대로 (덜 자름)
            assert out.width <= pw - 2 * 90 and screenshot._arrow_glyphs(out)[0] == [], (kw, out.size)
        assert out.width >= min(600, pw) * 0.98  # 사람(가로 44%)은 둘레를 남기고 그대로


def test_arrows_are_cropped_symmetrically_or_painted_over() -> None:
    img, box = _lightbox(thumbs=False)
    photo = img.crop(box)
    glyphs, subject = screenshot._arrow_glyphs(photo)
    assert len(glyphs) == 2 and subject is not None and subject[0] > glyphs[0][2] and subject[1] < glyphs[1][0]
    out = screenshot.strip_arrows(photo)
    assert out.height == photo.height and (photo.width - out.width) % 2 == 0 and out.width >= 0.7 * photo.width
    assert out.tobytes() == photo.crop(((photo.width - out.width) // 2, 0, (photo.width + out.width) // 2,
                                        photo.height)).tobytes()  # 가운데 그대로 (양옆을 똑같이)
    # 화살표가 안쪽(15% 넘어)까지 들어와 자르면 폭이 70% 아래가 되면 → 크기는 그대로, 화살표 자리만 둘레 배경으로 메움
    img, box = _lightbox(thumbs=False, arrow_x=135)
    photo = img.crop(box)
    assert len(screenshot._arrow_glyphs(photo)[0]) == 2
    out = screenshot.strip_arrows(photo)
    assert out.size == photo.size and screenshot._arrow_glyphs(out)[0] == []
    patch = out.crop((130, 600, 175, 760)).convert("L")
    assert patch.getextrema()[1] < 90  # 흰 획(235)이 남지 않음


def test_white_shapes_that_are_not_arrows_stay() -> None:
    # 셔츠 위 흰 ‹ 모양 로고 (주인공 세로줄 안) → 그대로
    img, box = _lightbox(thumbs=False, shirt=True)
    photo = img.crop(box)
    assert screenshot._arrow_glyphs(photo)[0] == [] and screenshot.strip_arrows(photo) is photo
    # 가장자리 가운데쯤의 꽉 찬 흰 네모(상품·로고) → 그대로
    img, box = _lightbox(thumbs=False, arrow_x=-200)  # 화살표는 그림 밖
    photo = img.crop(box)
    ImageDraw.Draw(photo).rectangle([40, 630, 100, 720], fill=(240, 240, 240))
    assert screenshot._arrow_glyphs(photo)[0] == [] and screenshot.strip_arrows(photo) is photo


def test_subject_principle_modest_crop_and_busy_photos_unchanged() -> None:
    """사진 원칙: 주인공 둘레만 조금 남기고 (꽉 끼게 자르지 않음), 확실하지 않으면 그대로."""
    prod = _product_on((255, 255, 255), w=1400, h=1400)  # 상품은 가로 22~78% · 세로 20~80% (784×840)
    out, what = screenshot.tidy(prod)
    assert what == "subject" and out.size != prod.size
    assert 784 * 1.12 <= out.width <= 784 * 1.3 and 840 * 1.12 <= out.height <= 840 * 1.3, out.size  # 둘레 8~12% 씩 안팎
    x0, y0, x1, y1 = screenshot.subject_box(prod)
    assert x0 <= 308 - 40 and x1 >= 1092 + 40 and y0 <= 280 - 40 and y1 >= 1120 + 40  # 상품은 안 잘림
    # 길쭉한 흰 배경 상품 사진(화면 캡처 아님): 예전 사진 덩어리 찾기는 상품 위아래를 잘랐다 → 상품 전체 + 둘레
    tall = _product_on((255, 255, 255))  # 900×1600, 상품 504×960 (y 320~1280)
    out, what = screenshot.tidy(tall)
    assert what == "subject" and out.width >= 504 * 1.12 and out.height >= 960 * 1.12, out.size
    assert screenshot._cuts_through(tall, screenshot._photo_box(tall))
    rnd = random.Random(1)
    busy = Image.new("RGB", (1000, 800))
    d = ImageDraw.Draw(busy)
    for _ in range(300):
        x, y = rnd.randint(0, 1000), rnd.randint(0, 800)
        d.rectangle([x, y, x + rnd.randint(10, 120), y + rnd.randint(10, 120)],
                    fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    assert screenshot.tidy(busy) == (busy, "")  # 꽉 찬 복잡한 사진 → 그대로
    night = _night()
    assert screenshot.subject_box(night) is None and screenshot.focus_subject(night) is night
    small = _product_on((255, 255, 255), w=700, h=700)
    x0, y0, x1, y1 = screenshot.subject_box(small)
    assert x1 - x0 >= 600 and y1 - y0 >= 600  # 짧은 변 600 아래로는 안 줄임 (딜봇 주인 사진 검사)
    tiny = _product_on((255, 255, 255), w=620, h=620)
    assert screenshot.subject_box(tiny) is None  # 잘라도 거의 그대로 → 안 자름


def test_real_product_photos_are_not_taken_for_a_lightbox() -> None:
    photos = [_product_on((255, 255, 255)), _product_on((0, 0, 0)), _product_on((22, 22, 22)), _night()]
    for p in photos + [_jpeg(p) for p in photos]:
        assert screenshot._lightbox_box(p) is None
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
    monkeypatch.setattr(screenshot, "strip_arrows", boom)
    monkeypatch.setattr(screenshot, "subject_box", boom)
    img, _ = _lightbox()
    out, what = screenshot.tidy(img)
    assert what in ("photo", "bars") and out.size != img.size
