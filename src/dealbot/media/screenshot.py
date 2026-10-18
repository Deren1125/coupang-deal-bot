"""주인이 보낸 딜 상품 사진이 휴대폰 화면 캡처일 때 다듬기 (주인 10/18: "알아서 사진 잘라서 넣는 기능 들어 있는 거지?").

네이버 딜(#80 폴햄 …)은 주인이 상품 페이지를 휴대폰으로 캡처해 올리는 일이 많다. 예전에는 길쭉한 캡처를 '상품 부분만 잘라서'
라며 받지 않았는데, 이제는 먼저 다듬고 그다음 원래 검사(짧은 변·비율)를 한다:
  ① 위 상태바(시각·배터리)·아래 탐색 막대(제스처 막대·버튼 줄·앱 아래 탭)는 또렷하게 보일 때만 걷는다 (내용은 자르지 않음)
  ② 가장 큰 '사진 덩어리'(배경이 아닌 칸이 화면 폭 절반 넘게 쭉 이어진 줄들 — 글자·메뉴는 띄엄띄엄)를 확신하면 그 부분만
  ③ 못 찾았거나 애매하면 가장자리 흰색·검정 여백만 걷는다
진짜 사진(가장자리까지 그림이 꽉 참)·가로가 긴 그림은 그대로. 길쭉한 그림(세로/가로 ≥ 1.6)만 본다.
Blog-Auto blogauto/media.py 의 crop_screenshot·phone_ui_bounds·_photo_box·trim_margins 를 옮긴 것 (파일이 아니라 그림으로) —
고칠 때는 두 곳을 같이. Pillow 만 쓴다.
"""

from __future__ import annotations

import io
import logging
from collections import Counter

from PIL import Image

log = logging.getLogger(__name__)

SCREEN_ASPECT = 1.6  # 이보다 길쭉하면 휴대폰 화면 캡처로 보고 다듬어 본다


def _photo_box(img: Image.Image, force: bool = False) -> tuple[int, int, int, int] | None:
    """화면 캡처에서 가장 큰 '사진 덩어리' 영역 (원본 좌표). 못 찾으면 None.
    배경색은 화면 가장자리에서 가장 많은 색으로 정한다 (흰 화면·회색 화면·다크 모드 모두)."""
    w, h = img.size
    scale = 240 / w if w > 240 else 1.0
    small = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    sw, sh = small.size
    px = small.load()
    edge = [px[x, y] for y in range(sh) for x in (0, 1, sw - 2, sw - 1)]
    edge = [tuple(c // 8 for c in e) for e in edge]
    bg = tuple(v * 8 + 4 for v in max(set(edge), key=edge.count))

    def ink(x, y):
        r, g, b = px[x, y]
        return abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 60

    # 줄마다 '배경이 아닌 칸'이 이어진 가장 긴 구간의 비율 (글자는 띄엄띄엄, 사진은 쭉 이어짐)
    def run_frac(y):
        best = cur = 0
        for x in range(sw):
            cur = cur + 1 if ink(x, y) else 0
            best = max(best, cur)
        return best / sw

    rows = [run_frac(y) for y in range(sh)]
    blocks, start, gap = [], None, 0
    for y, f in enumerate(rows + [0.0]):
        if f > 0.45:
            start = y if start is None else start
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max(2, sh // 80) or y == sh:
                blocks.append((start, y - gap))
                start, gap = None, 0
    # 사진 속 밝은 부분(흰 벽 등) 때문에 끊긴 덩어리는 다시 잇는다: 사이에 글자 줄이 하나도 없고 너무 멀지 않으면
    merged: list[tuple[int, int]] = []
    for b in blocks:
        if merged:
            a0, a1 = merged[-1]
            between = range(a1 + 1, b[0])
            texty = any(any(ink(x, y) for x in range(sw)) for y in between)
            if not texty and b[0] - a1 <= sh * 0.25:
                merged[-1] = (a0, b[1])
                continue
        merged.append(b)
    blocks = [b for b in merged if b[1] - b[0] >= sh * 0.12]
    if not blocks:
        return None
    top, bottom = max(blocks, key=lambda b: b[1] - b[0])
    # 진짜 '캡처 화면'인지 확인: 사진 덩어리 바깥(위·아래)이 대부분 배경색이어야 한다 (글자·메뉴만 띄엄띄엄).
    # 바깥에도 그림이 꽉 차 있으면 캡처가 아니라 그냥 사진 → 자르지 않는다 (사진 속 일부만 잘리는 사고 방지)
    # 화면 배경은 앱이 그린 단색이라 거의 똑같은 색이고, 사진 속 벽·하늘은 결·그라데이션이 있어 색이 조금씩 다르다
    outside = [y for y in range(sh) if y < top or y > bottom]
    if outside and not force:
        filled = sum(ink(x, y) for y in outside for x in range(sw)) / (len(outside) * sw)
        # 줄인 그림은 결이 뭉개지므로 원본 화소로 본다 (바깥 영역을 고르게 뽑아서)
        opx, ys = img.load(), outside[:: max(1, len(outside) // 60)]
        ref = Counter(opx[int(x / scale), int(y / scale)] for y in ys for x in range(0, sw, 3)).most_common(1)[0][0]
        flat_bg = sum(sum(abs(c - b) for c, b in zip(opx[min(w - 1, int(x / scale)), min(h - 1, int(y / scale))], ref, strict=False)) <= 9
                      for y in ys for x in range(sw)) / (len(ys) * sw)
        if filled > 0.3 or flat_bg < 0.55:
            return None
    cols = [sum(ink(x, y) for y in range(top, bottom + 1)) / (bottom - top + 1) for x in range(sw)]
    xs = [x for x, f in enumerate(cols) if f > 0.4]
    if not xs:
        return None
    box = (int(xs[0] / scale), int(top / scale), min(w, int((xs[-1] + 1) / scale)), min(h, int((bottom + 1) / scale)))
    if (box[2] - box[0]) * (box[3] - box[1]) > 0.92 * w * h:
        return None  # 거의 화면 전체 = 이미 사진만 있는 이미지
    return box


# ---------------------------------------------------------------- 휴대폰 화면 테두리(상태바·아래 탐색 막대)
# 주인이 보낸 화면 캡처에서 휴대폰 UI 만 걷어낸다. 또렷하게 보일 때만 자르고, 애매하면 그대로 둔다 (내용은 절대 안 자름).


def _dominant(img: Image.Image) -> tuple[int, int, int]:
    """가장 많은 색 (비슷한 색은 한 묶음으로)."""
    counts: Counter = Counter()
    for n, c in img.getcolors(img.width * img.height) or []:
        counts[tuple(v // 8 for v in c[:3])] += n
    q = counts.most_common(1)[0][0] if counts else (31, 31, 31)
    return tuple(v * 8 + 4 for v in q)


def _ink(img: Image.Image, bg) -> Image.Image:
    """바탕색과 또렷하게 다른 칸(글자·아이콘)만 255 인 흑백 그림 (세 색 중 가장 큰 차이가 40 넘음)."""
    from PIL import ImageChops

    r, g, b = ImageChops.difference(img, Image.new("RGB", img.size, bg)).split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > 40 else 0)


def _profile(mask: Image.Image) -> tuple[list[int], list[int]]:
    """(줄마다 글자 칸 수, 세로 칸마다 글자 칸 수)."""
    w, h = mask.size
    data = mask.tobytes()
    return [w - data[y * w:(y + 1) * w].count(0) for y in range(h)], [h - data[x::w].count(0) for x in range(w)]


def _groups(cols: list[int], gap: float, off: int = 0) -> list[list[int]]:
    """글자 칸이 있는 세로줄을 덩어리로 묶는다 (빈 줄이 gap 칸 미만이면 한 덩어리). [[시작, 끝], …], off 만큼 밀어서."""
    groups: list[list[int]] = []
    for x, c in enumerate(cols):
        if not c:
            continue
        if groups and x + off - groups[-1][1] <= gap:
            groups[-1][1] = x + off
        else:
            groups.append([x + off, x + off])
    return groups


def _longest_run(cols: list[int]) -> int:
    """빈 줄 없이 이어진 글자 칸 세로줄의 가장 긴 길이 (로고·글자 덩어리는 길고, 시각 숫자는 짧다)."""
    best = run = 0
    for c in cols:
        run = run + 1 if c else 0
        best = max(best, run)
    return best


def _status_bar(work: Image.Image) -> int:
    """위 상태바의 아래 경계 — 그 아래 빈 줄 가운데. 상태바 모양이 또렷하지 않으면 0 (자르지 않음).
    상태바 모양: 화면 맨 위(높이 2.4% 안)에서 시작하는 얇은 띠(높이 2.2% 이하), 가운데는 비고,
    왼쪽은 화면 왼쪽 끝 가까이의 좁은 시각 한 덩어리(폭 15% 이하, 끊김 없는 덩어리 12% 이하 — 로고·글자 줄은 아님),
    오른쪽은 서로 떨어진 작은 아이콘 2개 이상이 화면 오른쪽 끝 가까이(88% 넘어)까지.
    사이트 머리줄(로고 + 검색·메뉴)이나 '신청기간 … 10.1~10.16' 같은 첫 내용 줄은 이 모양이 아니라서 남긴다."""
    W, H = work.size
    top = work.crop((0, 0, W, max(4, int(H * 0.1))))
    mask = _ink(top, _dominant(top.crop((0, 0, W, max(2, H // 300)))))
    rows, _ = _profile(mask)
    n = len(rows)
    y0 = next((y for y in range(n) if rows[y]), None)
    if y0 is None or y0 == 0 or y0 > H * 0.024:
        return 0
    y1, holes, y = y0, 0, y0 + 1
    while y < n and holes <= 2:
        y1, holes = (y, 0) if rows[y] else (y1, holes + 1)
        y += 1
    if not (H * 0.005 <= y1 - y0 + 1 <= H * 0.022) or max(rows[y0:y1 + 1]) > 0.45 * W:
        return 0
    _, cols = _profile(mask.crop((0, y0, W, y1 + 1)))
    mid = cols[int(W * 0.42):int(W * 0.58)]
    if sum(1 for c in mid if c) > 0.1 * len(mid):
        return 0
    left = _groups(cols[:int(W * 0.42)], W * 0.015)  # 시각: 숫자 사이 좁은 틈은 한 덩어리로
    if not left:
        return 0
    a, b = left[0]
    if a > W * 0.16 or b - a + 1 > W * 0.15 or _longest_run(cols[a:b + 1]) > W * 0.12:
        return 0
    start = int(W * 0.58)
    right = _groups(cols[start:], W * 0.01, start)  # 아이콘: 서로 떨어진 작은 덩어리
    if not (2 <= len(right) <= 10 and right[-1][1] >= W * 0.88 and right[-1][1] - right[0][0] + 1 <= W * 0.4
            and all(q - p + 1 <= W * 0.12 for p, q in right)):
        return 0
    gap, y = 0, y1 + 1
    while y < n and not rows[y]:
        gap, y = gap + 1, y + 1
    if gap < max(2, H * 0.004):
        return 0  # 바로 아래에 내용이 붙어 있으면 경계가 애매 → 자르지 않음
    return y1 + 1 + gap // 2


def _bottom_bars(work: Image.Image, bars: bool = True) -> int:
    """아래 탐색 막대를 뺀 아래 경계 (자를 게 없으면 높이 그대로).
    ① 제스처 막대·아이폰 홈 표시줄: 맨 아래 가운데의 짧은 가로 막대 하나 + 위로 빈 줄
    ② (bars=True 일 때) 버튼·탭·브라우저 도구 줄: 한 가지 색의 구분선이나 바탕색 바뀜으로 위 내용과 또렷이 나뉘고,
       아이콘 3~6개가 고르게 놓인 줄. 경계가 사진·배너처럼 울퉁불퉁하면 내용으로 보고 자르지 않는다"""
    W, H = work.size
    base = int(H * 0.85)
    reg = work.crop((0, base, W, H))
    n, edge = reg.height, max(2, H // 400)
    bottom = n
    mask = _ink(reg, _dominant(reg.crop((0, n - edge, W, n))))
    rows, _ = _profile(mask)
    yb1 = next((y for y in range(n - 1, max(-1, n - 1 - int(H * 0.035)), -1) if rows[y]), None)
    if yb1 is not None:
        yb0 = yb1
        while yb0 > 0 and rows[yb0 - 1] and yb1 - yb0 + 1 < H * 0.012:
            yb0 -= 1
        gap, y = 0, yb0 - 1
        while y >= 0 and not rows[y]:
            gap, y = gap + 1, y - 1
        _, cols = _profile(mask.crop((0, yb0, W, yb1 + 1)))
        xs = [x for x, c in enumerate(cols) if c]
        span = xs[-1] - xs[0] + 1 if xs else 0
        if (yb1 - yb0 + 1 <= H * 0.012 and gap >= max(2, H * 0.004) and xs and xs[0] >= W * 0.25
                and xs[-1] <= W * 0.75 and W * 0.08 <= span <= W * 0.5 and len(xs) >= 0.85 * span):
            bottom = yb0 - gap // 2
    if not bars:
        return base + bottom
    sub = reg.crop((0, 0, W, bottom))
    m = sub.height
    bar_bg = _dominant(sub.crop((0, m - edge, W, m)))
    srows, _ = _profile(_ink(sub, bar_bg))

    def color(y):
        return sub.crop((0, y, W, y + 1)).resize((1, 1), Image.BOX).getpixel((0, 0))

    def flat(y):
        return all(hi - lo <= 24 for lo, hi in sub.crop((0, y, W, y + 1)).getextrema())

    def near(a, b, d=12):
        return max(abs(x - z) for x, z in zip(a, b, strict=False)) <= d

    y, line = m - 1, None
    while y >= max(0, m - int(H * 0.11)):
        if flat(y) and not near(color(y), bar_bg):
            line = y  # 구분선 또는 다른 바탕(위 내용)이 시작되는 줄
            break
        if srows[y] > 0.5 * W:
            return base + bottom  # 막대 바로 위가 사진·배너 → 경계가 애매하니 자르지 않음
        y -= 1
    if line is None or m - line - 1 < H * 0.025:
        return base + bottom
    t, ref = 1, color(line)
    while line - t >= 0 and t <= max(3, H * 0.003) and flat(line - t) and near(color(line - t), ref):
        t += 1
    cut = line - t + 1 if flat(line) and t <= max(3, H * 0.003) else line + 1  # 얇은 구분선이면 선까지 걷음
    _, cols = _profile(_ink(sub.crop((0, line + 1, W, m)), bar_bg))
    groups: list[list[int]] = []
    for x, c in enumerate(cols):
        if not c:
            continue
        if groups and x - groups[-1][1] <= W * 0.03:
            groups[-1][1] = x
        else:
            groups.append([x, x])
    centers = [(a + b) / 2 for a, b in groups]
    steps = [b - a for a, b in zip(centers, centers[1:], strict=False)]
    if (3 <= len(groups) <= 6 and groups[0][0] < W * 0.35 and groups[-1][1] > W * 0.65
            and all(b - a + 1 <= W * 0.2 for a, b in groups) and max(steps) <= 1.8 * min(steps)):
        bottom = cut
    return base + bottom


def phone_ui_bounds(img: Image.Image) -> tuple[int, int]:
    """휴대폰 화면 캡처에서 남길 (위, 아래) 경계. 세로로 긴 휴대폰 화면(세로/가로 ≥ 1.6)만 보고, 아니면 (0, 높이)."""
    w, h = img.size
    if h < 1.6 * w or w < 200:
        return 0, h
    W = min(w, 360)
    work = img.resize((W, h), Image.NEAREST) if W != w else img  # 가로만 줄임 (얇은 구분선이 사라지지 않게)
    top = _status_bar(work)
    top = top if top <= h * 0.08 else 0
    bottom = _bottom_bars(work, bars=top > 0)  # 아이콘 줄은 상태바까지 보이는 '휴대폰 화면 그대로'일 때만
    return top, (bottom if bottom >= h * 0.8 else h)


def trim_margins(img: Image.Image, tolerance: int = 22, pad: int = 6) -> Image.Image:
    """가장자리 여백(흰색·밝은 회색·검정 계열의 한 가지 색 줄)을 위·아래·왼쪽·오른쪽 각각 걷는다.
    색이 있는 배경(스튜디오 배경지·배너 바탕)은 사진의 일부라 남긴다. 거의 안 잘리거나 너무 작아지면 그대로."""
    w, h = img.size

    def flat(box: tuple[int, int, int, int]) -> bool:
        ext = img.crop(box).getextrema()
        if not all(hi - lo <= tolerance for lo, hi in ext):
            return False
        lo_all, hi_all = min(lo for lo, _ in ext), max(hi for _, hi in ext)
        return lo_all >= 225 or hi_all <= 30

    top, bottom, left, right = 0, h, 0, w
    while top < bottom - 1 and flat((0, top, w, top + 1)):
        top += 1
    while bottom > top + 1 and flat((0, bottom - 1, w, bottom)):
        bottom -= 1
    while left < right - 1 and flat((left, top, left + 1, bottom)):
        left += 1
    while right > left + 1 and flat((right - 1, top, right, bottom)):
        right -= 1
    x0, t = max(0, left - pad), max(0, top - pad)
    r, b = min(w, right + pad), min(h, bottom + pad)
    if (r - x0) < 160 or (b - t) < 120 or (r - x0) * (b - t) > 0.97 * w * h:
        return img
    return img.crop((x0, t, r, b))


def tidy(img: Image.Image) -> tuple[Image.Image, str]:
    """휴대폰 화면 캡처면 다듬은 그림과 한 일 ('photo' 사진 부분만 · 'bars' 상태바·막대·여백만 · '' 그대로).
    img 는 RGB (EXIF 방향을 이미 반영한 것)."""
    w, h = img.size
    if h < SCREEN_ASPECT * w or w < 200:
        return img, ""
    top, bottom = phone_ui_bounds(img)
    out = img.crop((0, top, w, bottom)) if top or bottom < h else img
    try:
        box = _photo_box(out)
    except Exception as e:  # noqa: BLE001 — 애매하면 자르지 않는다
        log.info("screenshot photo box failed: %s", type(e).__name__)
        box = None
    if box:
        return out.crop(box), "photo"
    trimmed = trim_margins(out)
    return trimmed, "bars" if trimmed is not img else ""


def tidy_bytes(data: bytes) -> tuple[bytes, str]:
    """사진 바이트 → (다듬은 PNG 바이트 또는 그대로, 한 일). 열지 못하면 그대로 (뒤 검사가 이유를 말함)."""
    try:
        from PIL import ImageOps

        with Image.open(io.BytesIO(data)) as src:
            src.load()
            img = ImageOps.exif_transpose(src)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
    except Exception as e:  # noqa: BLE001
        log.debug("screenshot tidy: not an image (%s)", type(e).__name__)
        return data, ""
    out, what = tidy(img)
    if not what:
        return data, ""
    buf = io.BytesIO()
    out.save(buf, "PNG")
    log.info("owner photo looks like a phone screenshot: %s %sx%s → %sx%s", what, img.width, img.height, out.width, out.height)
    return buf.getvalue(), what
