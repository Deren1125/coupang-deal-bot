"""개인정보 모자이크 (주인 11/17 — "원글 작성자·댓글 작성자를 특정할 수 있는 부분은 블러/모자이크").

그림 위의 상자(닉네임·아이디·프로필 사진·등급 아이콘·IP·전화번호·이메일·@아이디·실명·일반인 얼굴)를 픽셀 모자이크로 덮는다.
상자를 어디서 받는지는 부르는 쪽 몫 — 이 파일은 바깥 요청·AI 호출이 없다:
  · 커뮤 캡처(xcap)   크롬이 알려 준 DOM 요소 자리 (WHO_JS — 글쓴이·댓글 닉네임·프로필·등급·IP 요소 + 전화번호·이메일·@아이디 글자 자리)
  · 주인 업로드(xphoto) ① 서버에 tesseract(kor) 가 있으면 글자 자리(ocr_boxes) ② 글 쓰는 AI 가 사진을 보며 같이 준 상자(ai_boxes) — 둘을 합친다

모자이크는 되돌릴 수 없게: 상자를 조금 넓혀(PAD) 큰 칸(상자 짧은 변의 1/3 · 얼굴은 1/6)으로 줄였다 늘리고, 그 전에 흐림을 한 번 먹인다.
한계 (숨기지 않는다): AI 가 준 좌표는 어림값이라 상자가 조금 빗나갈 수 있고(그래서 넓힘), 못 본 닉네임은 그대로 남는다.
tesseract 는 '시각·날짜가 붙은 짧은 줄의 앞 낱말' 같은 모양으로만 닉네임을 짐작한다. → 카드에 '가린 곳 n곳'을 보여 주고 올리기 전 눈으로 확인.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
from typing import Any

from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

Box = tuple[int, int, int, int]  # (왼쪽, 위, 오른쪽, 아래) 픽셀
WHAT = ("nick", "id", "profile", "icon", "ip", "phone", "email", "name", "face")
PAD = 0.18  # 상자를 이만큼(짧은 변 기준) + PAD_PX 넓혀서 가린다
PAD_PX = 4
BOX_MAX = 60  # 그림 한 장에 받는 상자 수
AREA_MAX = 0.6  # 그림의 이만큼을 넘는 상자는 잘못 온 것으로 보고 버림 (통째로 가려지지 않게)
OCR_TIMEOUT = 25.0
OCR_MIN_CONF = 35.0

PHONE_RE = re.compile(r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}|0\d{1,2}[-.]\d{3,4}[-.]\d{4}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
AT_RE = re.compile(r"@[A-Za-z0-9_.]{2,30}")
IP_RE = re.compile(r"\(?\b\d{1,3}\.\d{1,3}(?:\.(?:\d{1,3}|\*{1,3}|x{1,3})){0,2}\)?")
TIME_RE = re.compile(r"\d{1,2}:\d{2}|\d{2,4}[./-]\d{1,2}[./-]\d{1,2}|\d+\s*(?:초|분|시간|일|주|달|개월)\s*전|방금|어제")
LABEL_RE = re.compile(r"^(?:글쓴이|작성자|닉네임|아이디|ID|id|작성|by)[:：]?$")
NOT_NICK_RE = re.compile(r"^(?:[\d.,:/|·\-()\[\]]+|답글|댓글|신고|추천|비추|공감|조회|좋아요|수정|삭제|\d+\s*(?:초|분|시간|일)|전|방금|어제|오전|오후)$")
TEXT_PII = (PHONE_RE, EMAIL_RE, AT_RE)

# 캡처 화면에서 가릴 요소 자리 (크롬 안에서 돈다) — sel = 찍는 칸, who = 그 사이트의 글쓴이·닉네임·프로필 선택자.
# 찍는 칸과 겹치는 요소만 · 찍는 칸 기준 CSS 픽셀 [x, y, w, h, 종류]. 글자 안 전화번호·이메일·@아이디는 Range 자리로.
WHO_JS = r"""([sel, who]) => {
  const root = document.querySelector(sel); if (!root) return [];
  const rb = root.getBoundingClientRect(), out = [];
  const add = (r, what) => { const x = Math.max(r.left, rb.left), y = Math.max(r.top, rb.top), x2 = Math.min(r.right, rb.right), y2 = Math.min(r.bottom, rb.bottom);
    if (x2 - x >= 3 && y2 - y >= 3 && out.length < 60) out.push([x - rb.left, y - rb.top, x2 - x, y2 - y, what]); };
  const generic = '[class*="nick" i],[class*="writer" i],[class*="author" i],[class*="userid" i],[class*="user_id" i],[class*="profile" i] img,' +
    'img[class*="profile" i],img[class*="avatar" i],img[class*="level" i],[class*="level" i] img,[class*="ip_"],[class~="ip"]';
  let list = [];
  try { list = [...document.querySelectorAll([who, generic].filter(Boolean).join(','))]; } catch (e) { try { list = [...document.querySelectorAll(generic)]; } catch (e2) { list = []; } }
  for (const e of list) {
    if (e === root || e.contains(root)) continue;
    const r = e.getBoundingClientRect();
    if (r.width * r.height > rb.width * rb.height * 0.25) continue;
    add(r, e.tagName === 'IMG' ? 'profile' : 'nick');
  }
  const pat = [[/01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}/g, 'phone'], [/[\w.+-]+@[\w-]+\.[\w.-]+/g, 'email'], [/@[A-Za-z0-9_]{2,15}/g, 'id']];
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) for (const [p, what] of pat) { p.lastIndex = 0;
    for (let m = p.exec(n.nodeValue); m; m = p.exec(n.nodeValue)) { const g = document.createRange(); g.setStart(n, m.index); g.setEnd(n, m.index + m[0].length);
      for (const r of g.getClientRects()) add(r, what); } }
  return out;
}"""


def scrub(text: str) -> str:
    """글자 안 전화번호·이메일·@아이디를 ● 로 (글자 그림 카드용)."""
    for p in TEXT_PII:
        text = p.sub(lambda m: "●" * min(len(m.group(0)), 8), text)
    return text


def clip_boxes(boxes: list[Any], size: tuple[int, int], *, scale: float = 1.0, faces: bool = True) -> list[tuple[Box, str]]:
    """[x, y, w, h, 종류] (또는 {"x","y","w","h","what"}) 목록 → 그림 안으로 자르고 넓힌 (상자, 종류). 말이 안 되는 상자는 버린다."""
    W, H = size
    out: list[tuple[Box, str]] = []
    for b in boxes[: BOX_MAX * 3]:
        try:
            if isinstance(b, dict):
                x, y, w, h, what = float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"]), str(b.get("what") or "nick")
            else:
                x, y, w, h, what = float(b[0]), float(b[1]), float(b[2]), float(b[3]), str(b[4] if len(b) > 4 else "nick")
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not all(v == v and abs(v) < 1e7 for v in (x, y, w, h)) or w <= 0 or h <= 0:
            continue
        what = what if what in WHAT else "nick"
        if what == "face" and not faces:
            continue
        x, y, w, h = x * scale, y * scale, w * scale, h * scale
        pad = min(w, h) * (PAD * 2 if what == "face" else PAD) + PAD_PX
        box = (max(0, int(x - pad)), max(0, int(y - pad)), min(W, int(x + w + pad + 0.999)), min(H, int(y + h + pad + 0.999)))
        if box[2] - box[0] < 3 or box[3] - box[1] < 3 or (box[2] - box[0]) * (box[3] - box[1]) > W * H * AREA_MAX:
            continue
        if box not in [o[0] for o in out]:
            out.append((box, what))
        if len(out) >= BOX_MAX:
            break
    return out


def mosaic(im: Image.Image, boxes: list[tuple[Box, str]]) -> Image.Image:
    """상자마다 픽셀 모자이크 (원본은 그대로 두고 새 그림). 얼굴은 더 굵게."""
    out = im.convert("RGB").copy()
    for (x1, y1, x2, y2), what in boxes:
        part = out.crop((x1, y1, x2, y2))
        w, h = part.size
        cell = max(8, round(min(w, h) / (2.5 if what != "face" else 1)), round(max(w, h) / (9999 if what != "face" else 6)))
        small = part.filter(ImageFilter.GaussianBlur(max(2, cell // 3))).resize((max(1, round(w / cell)), max(1, round(h / cell))), Image.BOX)
        out.paste(small.resize((w, h), Image.NEAREST), (x1, y1))
    return out


def blur_bytes(raw: bytes, boxes: list[Any], *, scale: float = 1.0, faces: bool = True, fmt: str = "PNG") -> tuple[bytes, int]:
    """그림 바이트 + 상자 → (모자이크한 그림 바이트, 가린 곳 수)."""
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    use = clip_boxes(boxes, im.size, scale=scale, faces=faces)
    buf = io.BytesIO()
    mosaic(im, use).save(buf, fmt, **({"quality": 90} if fmt == "JPEG" else {}))
    return buf.getvalue(), len(use)


# ---------------------------------------------------------------- tesseract (있을 때만)
_OCR: dict[str, Any] = {}


def ocr_ready() -> bool:
    """서버에 tesseract 와 한국어 데이터가 있나 (한 번만 본다)."""
    if "ok" not in _OCR:
        exe = shutil.which("tesseract")
        ok = False
        if exe:
            try:
                r = subprocess.run([exe, "--list-langs"], capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
                ok = "kor" in (r.stdout + r.stderr).split()
            except (OSError, subprocess.SubprocessError):
                ok = False
        _OCR["ok"], _OCR["exe"] = ok, exe
    return bool(_OCR["ok"])


def run_tesseract(png: bytes) -> str:
    """그림 → tesseract TSV 글 (못 하면 '')."""
    if not ocr_ready():
        return ""
    try:
        r = subprocess.run([_OCR["exe"], "stdin", "stdout", "-l", "kor+eng", "--psm", "11", "tsv"], input=png, capture_output=True,  # noqa: S603
                           timeout=OCR_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("xblur ocr: %s", type(e).__name__)
        return ""
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def tsv_lines(tsv: str) -> list[list[dict[str, Any]]]:
    """tesseract TSV → 줄마다 낱말 [{text, x, y, w, h}] (왼쪽부터)."""
    rows = [ln.split("\t") for ln in tsv.splitlines()]
    if not rows or "text" not in rows[0]:
        return []
    head = {k: i for i, k in enumerate(rows[0])}
    lines: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for r in rows[1:]:
        try:
            if len(r) < len(head) or r[head["level"]] != "5" or float(r[head["conf"]]) < OCR_MIN_CONF:
                continue
            text = r[head["text"]].strip()
            if not text:
                continue
            word = {"text": text, "x": int(r[head["left"]]), "y": int(r[head["top"]]), "w": int(r[head["width"]]), "h": int(r[head["height"]])}
        except (KeyError, ValueError):
            continue
        lines.setdefault((r[head["page_num"]], r[head["block_num"]], r[head["par_num"]], r[head["line_num"]]), []).append(word)
    return [sorted(ws, key=lambda w: w["x"]) for ws in lines.values()]


def ocr_boxes(lines: list[list[dict[str, Any]]]) -> list[list[Any]]:
    """글자 줄에서 가릴 자리 [x, y, w, h, 종류]: 전화번호·이메일·@아이디·IP 낱말 + '글쓴이·작성자' 다음 낱말 +
    시각·날짜가 붙은 짧은 줄(댓글 머리줄)의 앞쪽 낱말(닉네임으로 짐작)."""
    out: list[list[Any]] = []
    for ws in lines:
        hit: dict[int, str] = {}
        for i, w in enumerate(ws):
            t = w["text"]
            if EMAIL_RE.search(t):
                hit[i] = "email"
            elif AT_RE.search(t):
                hit[i] = "id"
            elif IP_RE.fullmatch(t) and t.count(".") >= 1 and not re.fullmatch(r"\d{1,2}\.\d{1,2}", t):
                hit[i] = "ip"
            elif LABEL_RE.match(t) and i + 1 < len(ws):
                hit[i + 1] = "nick"
        joined = " ".join(w["text"] for w in ws)
        m = PHONE_RE.search(joined)
        if m:  # 번호가 낱말 여럿으로 갈렸을 수 있다 — 숫자가 든 낱말을 다 가림
            for i, w in enumerate(ws):
                if re.search(r"\d{3,}", w["text"]):
                    hit[i] = "phone"
        if len(ws) <= 8 and TIME_RE.search(joined):  # 댓글·글 머리줄: '닉네임 … 11.17 12:34' / '닉네임 3분 전'
            for i, w in enumerate(ws):
                if TIME_RE.search(w["text"]) or NOT_NICK_RE.match(w["text"]):
                    break
                hit.setdefault(i, "nick")
                if i >= 2:
                    break
        out += [[ws[i]["x"], ws[i]["y"], ws[i]["w"], ws[i]["h"], what] for i, what in sorted(hit.items())]
    return out
