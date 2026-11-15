#!/usr/bin/env python3
"""앱 아이콘 바꾸기 (주인 11/15 — "앱 커버는 오리 그림"): 그림 폴더 하나 → 대시보드 앱 아이콘 파일들 + 테스트의 승인 표.

    python tools/app_icons.py <그림 폴더>        # 예: 퍼프덱스 저장소의 perpfarm/hub_static
    python tools/app_icons.py <그림 폴더> --check  # 바꾸지 않고 무엇이 바뀔지만

폴더에서 찾는 파일 (있는 것 중 가장 큰 것을 밑그림으로): icon-1024.jpg · icon-1024.png · icon-512.png · icon-192.png · icon-180.png
만드는 파일 (src/dealbot/dashboard/static/):
  icon-192.png · icon-512.png   둥근 모서리, 바깥은 투명 (매니페스트 purpose any)
  icon-maskable-512.png         가장자리까지 꽉 찬 네모 (휴대폰이 잘라 씀)
  apple-touch-icon.png          180, 꽉 찬 네모 (아이폰이 스스로 둥글림)
  favicon-64.png                64, 둥근 모서리
icon.svg(브라우저 탭의 벡터 그림)는 건드리지 않는다 — 오리 벡터가 생기면 그 파일만 따로 바꾼다.
끝에 tests/fixtures/dash_icons.json (파일 → sha256) 을 다시 써서, 주소의 ?v= 해시와 '승인한 그림' 테스트가 같이 바뀐다.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dealbot" / "dashboard" / "static"
TABLE = ROOT / "tests" / "fixtures" / "dash_icons.json"
SOURCES = ("icon-1024.jpg", "icon-1024.png", "icon-512.png", "icon-192.png", "icon-180.png")
RADIUS = 0.225  # 둥근 모서리 반지름 / 한 변 (지금 아이콘과 비슷하게)
SS = 4  # 모서리를 매끈하게 — 4배로 그려 줄임


def source(folder: Path) -> Image.Image:
    best: Image.Image | None = None
    for name in SOURCES:
        path = folder / name
        if path.is_file():
            img = Image.open(path)
            img.load()
            if img.width != img.height:
                raise SystemExit(f"⛔ {name}: 정사각형이 아니에요 ({img.width}x{img.height})")
            if best is None or img.width > best.width:
                best = img
    if best is None:
        raise SystemExit(f"⛔ {folder} 에 아이콘 그림이 없어요 ({' · '.join(SOURCES)} 중 하나)")
    if best.width < 512:
        raise SystemExit(f"⛔ 밑그림이 너무 작아요 ({best.width}px) — 512px 이상이 필요해요")
    return best


def square(src: Image.Image, size: int) -> Image.Image:
    """꽉 찬 네모 (투명한 곳은 흰색으로)."""
    rgba = src.convert("RGBA").resize((size, size), Image.LANCZOS)
    out = Image.new("RGB", (size, size), "#ffffff")
    out.paste(rgba, mask=rgba.getchannel("A"))
    return out


def rounded(src: Image.Image, size: int) -> Image.Image:
    base = square(src, size).convert("RGBA")
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * SS - 1, size * SS - 1), radius=int(size * SS * RADIUS), fill=255)
    base.putalpha(mask.resize((size, size), Image.LANCZOS))
    return base


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def build(folder: Path) -> dict[str, bytes]:
    src = source(folder)
    return {
        "icon-192.png": png(rounded(src, 192)),
        "icon-512.png": png(rounded(src, 512)),
        "icon-maskable-512.png": png(square(src, 512)),
        "apple-touch-icon.png": png(square(src, 180)),
        "favicon-64.png": png(rounded(src, 64)),
    }


def apply(files: dict[str, bytes], static: Path = STATIC, table: Path = TABLE, *, write: bool = True) -> list[str]:
    """바뀌는 파일 이름들. write 면 파일과 승인 표를 쓴다."""
    changed = [name for name, data in files.items() if not (static / name).is_file() or (static / name).read_bytes() != data]
    if write:
        for name in changed:
            (static / name).write_bytes(files[name])
        digests = json.loads(table.read_text(encoding="utf-8")) if table.is_file() else {}
        for name in sorted(p.name for p in static.iterdir() if p.name in digests or p.name in files):
            digests[name] = hashlib.sha256((static / name).read_bytes()).hexdigest()
        table.write_text(json.dumps(dict(sorted(digests.items())), indent=1) + "\n", encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    changed = apply(build(Path(args[0]).expanduser()), write="--check" not in argv)
    print(("바뀔 파일: " if "--check" in argv else "✅ 바꾼 파일: ") + (", ".join(changed) or "없음 (이미 같은 그림)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
