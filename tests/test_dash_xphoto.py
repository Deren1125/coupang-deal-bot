"""사진으로 X 글 쓰기 · 개인정보 모자이크 · 초안에 붙일 사진 (주인 11/17 — dashboard/xphoto.py · xblur.py · static/xphoto.js).
바깥 요청 없음 — AI·tesseract·크롬·기사 사이트는 가짜.

1) 모자이크: 가짜 캡처의 닉네임 자리 픽셀이 바뀌고 그 밖은 그대로 · 말이 안 되는 상자는 버림 · 얼굴 상자는 켰을 때만
2) tesseract 글자 줄 → 닉네임·전화번호·이메일·@아이디·IP 자리
3) 사진으로 초안: 기존 업로드 길 → AI(사진 같이) → 모자이크본만 남고 원본은 지움 → '내 초안' 목록·체크리스트 · 실패 줄 · 하루 상한 · Fable 금지
4) 커뮤 캡처(xcap): 크롬이 알려 준 요소 자리를 모자이크 · 카드에 가린 곳 수
5) 오늘 X 글 카드의 기사 대표 사진: robots 확인 · og:image 한 장 · 출처
6) 화면 파일은 안전
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings, XPhotoConfig
from dealbot.dashboard import shell, xblur
from dealbot.dashboard import xcap as xc
from dealbot.dashboard import xphoto as xp
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtoday as xt
from dealbot.dashboard import xtrend as xr
from test_dash_uploads_security import upload
from test_dash_xtoday import FABLE, NODE, STATIC, FakeDb, needs_node

NICK = (40, 30, 200, 40)  # 가짜 캡처의 닉네임 자리 (x, y, w, h)
BODY = (40, 200, 700, 300)


def capture(size: tuple[int, int] = (800, 1200)) -> Image.Image:
    """흰 바탕 + 닉네임 자리(검은 줄무늬 — 글자 대신) + 본문 자리(다른 무늬)."""
    im = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(im)
    for x in range(NICK[0], NICK[0] + NICK[2], 8):
        d.rectangle((x, NICK[1], x + 3, NICK[1] + NICK[3]), fill=(0, 0, 0))
    for y in range(BODY[1], BODY[1] + BODY[3], 20):
        d.rectangle((BODY[0], y, BODY[0] + BODY[2], y + 6), fill=(30, 30, 30))
    return im


def jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def region(im: Image.Image, box: tuple[int, int, int, int]) -> bytes:
    x, y, w, h = box
    return im.convert("RGB").crop((x, y, x + w, y + h)).tobytes()


def stripes_left(im: Image.Image, box: tuple[int, int, int, int]) -> int:
    """자리 안 가운데 줄에서 '검정↔흰색' 바뀜 수 (줄무늬가 남았나)."""
    x, y, w, h = box
    row = [im.convert("L").getpixel((i, y + h // 2)) for i in range(x, x + w)]
    return sum(1 for a, b in zip(row, row[1:], strict=False) if (a < 80) != (b < 80))


# ---------------------------------------------------------------- 1) 모자이크
def test_mosaic_changes_only_the_boxed_pixels() -> None:
    im = capture()
    assert stripes_left(im, NICK) > 30
    boxes = xblur.clip_boxes([[*NICK, "nick"]], im.size)
    out = xblur.mosaic(im, boxes)
    assert out.size == im.size and region(out, NICK) != region(im, NICK)
    assert stripes_left(out, NICK) <= 2  # 줄무늬(글자)가 뭉개짐 — 읽을 수 없음
    assert region(out, BODY) == region(im, BODY)  # 본문은 그대로
    assert region(im, NICK) == region(capture(), NICK)  # 원본 그림은 안 건드림
    data, n = xblur.blur_bytes(jpeg(im), [{"x": NICK[0] / 2, "y": NICK[1] / 2, "w": NICK[2] / 2, "h": NICK[3] / 2, "what": "id"}], scale=2.0)
    assert n == 1 and stripes_left(Image.open(io.BytesIO(data)), NICK) <= 2  # 배율(크롬 2배 · AI 에 보낸 작은 그림) 반영


def test_bad_boxes_are_dropped_and_faces_only_when_asked() -> None:
    size = (800, 1200)
    bad = [[0, 0, 800, 1200, "nick"], [10, 10, -5, 5, "nick"], ["a", 1, 2, 3], {"x": 1}, [float("nan"), 0, 5, 5, "nick"], [5000, 5000, 10, 10, "nick"], None]
    assert xblur.clip_boxes(bad, size) == []  # 그림 통째·음수·글자·그림 밖
    face = [[100, 100, 120, 120, "face"]]
    assert xblur.clip_boxes(face, size, faces=False) == [] and len(xblur.clip_boxes(face, size, faces=True)) == 1
    (box, what), = xblur.clip_boxes([[100, 100, 50, 20, "모름"]], size)
    assert what == "nick" and box[0] < 100 and box[1] < 100 and box[2] > 150 and box[3] > 120  # 넉넉히 넓힘
    assert len(xblur.clip_boxes([[i * 10, 0, 8, 8, "nick"] for i in range(200)], (4000, 100))) == xblur.BOX_MAX
    assert xblur.scrub("연락 010-1234-5678 메일 a.b@ex.com 트위터 @some_id 끝") == "연락 ●●●●●●●● 메일 ●●●●●●●● 트위터 ●●●●●●●● 끝"


# ---------------------------------------------------------------- 2) tesseract 줄
def tsv(*lines: list[tuple[str, int, int, int, int]]) -> str:
    rows = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for ln, words in enumerate(lines, 1):
        rows += [f"5\t1\t1\t1\t{ln}\t{i}\t{x}\t{y}\t{w}\t{h}\t91.5\t{t}" for i, (t, x, y, w, h) in enumerate(words, 1)]
    return "\n".join(rows)


def test_ocr_lines_find_nicknames_and_contact_text() -> None:
    text = tsv([("행복한고양이", 40, 30, 120, 28), ("2026.11.17", 300, 30, 110, 28), ("12:34", 420, 30, 60, 28)],
               [("친구랑", 40, 200, 70, 28), ("동업하면", 120, 200, 90, 28), ("안", 220, 200, 20, 28), ("되는", 250, 200, 40, 28), ("이유를", 300, 200, 60, 28),
                ("알게", 370, 200, 40, 28), ("됐다", 420, 200, 40, 28), ("정말로", 470, 200, 60, 28), ("그렇다", 540, 200, 60, 28)],
               [("문의", 40, 300, 40, 28), ("010-1234-5678", 90, 300, 150, 28), ("또는", 250, 300, 40, 28), ("me@ex.com", 300, 300, 100, 28)],
               [("글쓴이", 40, 400, 60, 28), ("달리는곰", 110, 400, 80, 28), ("(121.168)", 200, 400, 90, 28)],
               [("댓글러", 40, 500, 60, 28), ("3분", 110, 500, 30, 28), ("전", 150, 500, 20, 28), ("@who_am_i", 200, 500, 90, 28)])
    got = {(b[0], b[1]): b[4] for b in xblur.ocr_boxes(xblur.tsv_lines(text))}
    assert got[(40, 30)] == "nick" and (300, 30) not in got  # 시각이 붙은 머리줄의 앞 낱말 = 닉네임 · 날짜는 그대로
    assert not any(y == 200 for _x, y in got)  # 본문 줄은 안 가림
    assert got[(90, 300)] == "phone" and got[(300, 300)] == "email"
    assert got[(110, 400)] == "nick" and got[(200, 400)] == "ip"
    assert got[(40, 500)] == "nick" and got[(200, 500)] == "id"
    assert xblur.tsv_lines("") == [] and xblur.tsv_lines("garbage\nrows") == [] and xblur.ocr_boxes([]) == []


# ---------------------------------------------------------------- 3) 사진으로 초안
class Vision:
    """AI 대신: 받은 사진·글을 모으고 정해 둔 답을 준다. 좌표는 '보낸 그림' 크기 기준."""

    def __init__(self, *, boxes: list[dict[str, Any]] | None = None, ok: bool = True, kind: str = "capture", fail: Exception | None = None,
                 drafts: list[dict[str, str]] | None = None) -> None:
        self.boxes, self.ok, self.kind, self.fail = boxes, ok, kind, fail
        self.drafts = drafts if drafts is not None else [{"text": "친구랑 동업하면 안 되는 이유\n돈 얘기 나오면 달라짐", "angle": "돈 얘기"},
                                                         {"text": "이거 보고 한참 생각함\n3년 우정이 1년 만에 끝남", "angle": "시간"}]
        self.calls: list[tuple[str, str, list[bytes]]] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any], *, images: list[bytes] | None = None) -> dict[str, Any]:
        self.calls.append((system, user, list(images or [])))
        if self.fail is not None:
            raise self.fail
        return {"ok": self.ok, "why": "" if self.ok else "특정인 비방", "kind": self.kind, "subject": "동업 후기 글", "read": "3년 우정 1년 만에 끝",
                "boxes": self.boxes if self.boxes is not None else [], "drafts": self.drafts}


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    b.dashboard.api.xphoto.ocr = None  # type: ignore[union-attr]
    yield b
    b.db.close()


def scaled(box: tuple[int, int, int, int], k: float, what: str = "nick", img: int = 1) -> dict[str, Any]:
    return {"img": img, "x": box[0] * k, "y": box[1] * k, "w": box[2] * k, "h": box[3] * k, "what": what}


async def test_photo_draft_blurs_keeps_only_the_blurred_copy_and_joins_my_drafts(bot: DealBot) -> None:
    api = bot.dashboard.api  # type: ignore[union-attr]
    h = session_headers(bot)
    big = capture((1600, 2400)).resize((2000, 3000))  # 보낼 때 줄어드는 큰 캡처 (긴 변 3000 → 1568)
    k = xp.SEND_SIDE / 3000
    nick = tuple(round(v * 1.25) for v in NICK)  # 큰 그림에서의 닉네임 자리
    ask = Vision(boxes=[scaled(nick, k), scaled((500, 500, 300, 300), k, "face"), {"img": 2, "x": 10, "y": 10, "w": 50, "h": 20, "what": "id"}])
    api.xstudio._ask_fn = ask
    status, up1 = await upload(bot, jpeg(big), h)
    status2, up2 = await upload(bot, jpeg(capture()), h)
    assert status == status2 == 200
    orig = api.uploads.path(up1["upload_id"])
    assert orig is not None and orig.is_file()
    status, out, _ = await post_json(bot, xp.DRAFT_PATH, {"rid": "r_photo1", "photos": [up1["upload_id"], up2["upload_id"]], "memo": "동업은 신중하게", "tone": "eum"}, h)
    assert status == 200, out
    system, user, images = ask.calls[0]
    assert len(images) == 2 and max(Image.open(io.BytesIO(images[0])).size) == xp.SEND_SIDE  # 줄인 JPEG 로 보냄
    assert "사진 1: 1045x1568" in user and "사진 2: 800x1200" in user and "동업은 신중하게" in user
    assert "face 상자를 주지 않는다" in system and "capture" in system and "모자이크" in system
    s = out["mine"][0]
    assert s["id"] == out["made"] and s["topic"] == "사진 캡처 · 동업 후기 글" and len(s["drafts"]) == 2
    assert s["imgs"] == [f"{xp.IMG_PREFIX}{s['id']}-1.jpg", f"{xp.IMG_PREFIX}{s['id']}-2.jpg"]
    assert s["blur"] == 2 and s["pkind"] == "capture" and s["faces"] is False  # 얼굴 상자는 안 씀 (기본 꺼짐)
    # 모자이크본: 닉네임 자리는 뭉개지고 본문은 남음 · 원본 업로드는 지워짐 (파일·썸네일·표)
    res = await bot.web.dispatch("GET", s["imgs"][0], h)
    assert res[0] == 200 and res[2] == "image/jpeg"
    done = Image.open(io.BytesIO(res[1]))
    kk = done.width / 2000
    nick_d = tuple(round(v * kk) for v in nick)
    assert stripes_left(done, nick_d) <= 2 and stripes_left(capture((1600, 2400)).resize(done.size), nick_d) > 20
    assert api.uploads.path(up1["upload_id"]) is None and api.uploads.path(up2["upload_id"]) is None and not orig.exists()
    assert api.uploads.thumb_path(up1["upload_id"]) is None
    assert (await bot.web.dispatch("GET", s["imgs"][0], {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}))[0] == 401  # 로그인 아래
    assert (await bot.web.dispatch("GET", xp.IMG_PREFIX + "../../etc/passwd", h))[0] == 404
    # 같은 rid 로 다시 = AI 한 번 · 지운 사진으로 다시 = 404
    assert (await post_json(bot, xp.DRAFT_PATH, {"rid": "r_photo1", "photos": [up1["upload_id"], up2["upload_id"]]}, h))[0] == 200 and len(ask.calls) == 1
    status, out2, _ = await post_json(bot, xp.DRAFT_PATH, {"rid": "r_photo2", "photos": [up1["upload_id"]]}, h)
    assert status == 404 and out2["code"] == "gone" and len(ask.calls) == 1
    # 하루 몫: 초안 1번 · 사진 2장
    v = (await get_json(bot, xr.STATE_PATH, h))[1]
    assert v["left"] == xs.DRAFT_PER_DAY - 1
    pv = (await get_json(bot, xp.STATE_PATH, h))[1]
    assert pv["on"] is True and pv["left"] == XPhotoConfig().photos_per_day - 2 and pv["lim"]["photos"] == 4 and pv["ocr"] is False
    # [✓ 올렸어요] → 오늘 X 체크리스트에 '사진 n장과 같이'
    status, out3, _ = await post_json(bot, xr.POSTED_PATH, {"set": s["id"], "i": 0}, h)
    assert status == 200 and out3["mine"][0]["drafts"][0]["done"] is True
    item = (await get_json(bot, xt.STATE_PATH, h))[1]["items"][0]
    assert item["kind"] == "mine" and item["done"] is True and "사진 2장과 같이" in item["angle"]


async def test_faces_are_blurred_only_when_ticked_and_ocr_boxes_are_merged(bot: DealBot) -> None:
    api = bot.dashboard.api  # type: ignore[union-attr]
    h = session_headers(bot)
    face = (300, 600, 200, 200)
    im = capture()
    ImageDraw.Draw(im).ellipse((face[0], face[1], face[0] + face[2], face[1] + face[3]), fill=(200, 150, 120), outline=(0, 0, 0), width=6)
    ask = Vision(boxes=[scaled(face, 1.0, "face")], kind="photo")
    api.xstudio._ask_fn = ask
    api.xphoto.ocr = lambda _png: tsv([("행복한고양이", NICK[0], NICK[1], NICK[2], NICK[3]), ("3분", 300, 30, 30, 28), ("전", 340, 30, 20, 28)])
    _s, up = await upload(bot, jpeg(im), h)
    status, out, _ = await post_json(bot, xp.DRAFT_PATH, {"photos": [up["upload_id"]], "faces": True, "length": "short"}, h)
    assert status == 200 and "모두 face 상자로" in ask.calls[0][0]
    s = out["mine"][0]
    assert s["blur"] == 2 and s["faces"] is True and s["topic"].startswith("사진 사진")
    done = Image.open(io.BytesIO((await bot.web.dispatch("GET", s["imgs"][0], h))[1]))
    assert region(done, face) != region(im, face) and stripes_left(done, NICK) <= 2  # 얼굴(AI) + 닉네임(글자 자리) 둘 다
    assert max(abs(a - b) for a, b in zip(region(done, (40, 900, 100, 100)), region(im, (40, 900, 100, 100)), strict=True)) < 12  # 그 밖은 JPEG 차이뿐


async def test_failures_show_in_the_same_place_and_limits_hold(bot: DealBot, monkeypatch: pytest.MonkeyPatch) -> None:
    api = bot.dashboard.api  # type: ignore[union-attr]
    h = session_headers(bot)
    _s, up = await upload(bot, jpeg(capture()), h)
    uid, session, desk = up["upload_id"], api.caller(h)[0].session, api.xphoto

    async def go(body: dict[str, Any], who: str = "") -> tuple[int, str, str]:  # 분당 제한을 건너 책상을 바로 부름
        try:
            await desk.draft(body, who or session)
        except xs.StudioError as e:
            return e.status, e.code, e.text
        return 200, "", ""

    # 입력 검사 — AI 를 부르지 않음
    ask = Vision()
    api.xstudio._ask_fn = ask
    for body in ({"photos": []}, {"photos": [uid] * 2}, {"photos": [uid, "x", "y", "z", "w"]}, {"photos": "abc"}, {"photos": [uid], "memo": "가" * 201}):
        assert (await go(body))[0] == 400, body
    assert (await go({"photos": ["0" * 32]}))[0] == 404 and (await go({"photos": [uid]}, "other000"))[0] == 404  # 없는 사진 · 다른 세션이 올린 사진
    assert (await post_json(bot, xp.DRAFT_PATH, {"photos": [uid]}, session_headers(bot, csrf=False)))[0] == 403
    assert (await go({"photos": [uid], "memo": "야동 얘기"}))[0] == 422 and ask.calls == []
    # AI 가 거절 → 실패 줄이 '내 주제' 와 같은 자리에 (photo 표시) · 원본은 남아 다시 해 볼 수 있음
    api.xstudio._ask_fn = Vision(ok=False)
    status, out, _ = await post_json(bot, xp.DRAFT_PATH, {"photos": [uid]}, h)
    assert status == 422 and out["code"] == "declined" and "특정인 비방" in out["text"]
    v = (await get_json(bot, xr.STATE_PATH, h))[1]
    assert v["fail"]["photo"] is True and v["fail"]["code"] == "declined" and v["run"] is None and v["mine"] == []
    assert api.uploads.path(uid) is not None
    # AI 가 아예 못 답함 → 몫을 돌려줌
    left, pleft = v["left"], desk.view()["left"]
    api.xstudio._ask_fn = Vision(fail=xs.StudioError(502, "ai_failed", "AI 가 답하지 못했어요"))
    assert (await go({"photos": [uid]}))[0] == 502
    assert (await get_json(bot, xr.STATE_PATH, h))[1]["left"] == left and desk.view()["left"] == pleft
    # 규칙에 걸린 초안(@·링크)만 오면 초안 없음 · 원본은 그대로
    api.xstudio._ask_fn = Vision(drafts=[{"text": "@someone 이 쓴 글 https://a.example", "angle": "x"}])
    assert (await go({"photos": [uid]}))[:2] == (502, "ai_failed") and api.uploads.path(uid) is not None
    # 하루 사진 상한
    bot.settings.dashboard.x_photo.photos_per_day = 2
    api.xstudio._ask_fn = ask
    status, _code, text = await go({"photos": [uid]})
    assert status == 429 and "사진" in text and ask.calls == []
    bot.settings.dashboard.x_photo.photos_per_day = 24
    # 글 쓰는 모델이 Fable 이면 AI 를 부르지 않음
    monkeypatch.setattr(api.xstudio, "model", FABLE)
    assert (await go({"photos": [uid]}))[:2] == (503, "ai_off") and ask.calls == []
    monkeypatch.undo()
    bot.settings.dashboard.x_photo.enabled = False
    assert (await go({"photos": [uid]}))[0] == 409


def test_vision_goes_through_the_same_model_gate() -> None:
    sys_ = xp.photo_system(2, 3, "eum", "normal", False)
    assert "Fable" not in sys_ and "닉네임" in sys_ and "1~2줄" in sys_ and "{" not in sys_.replace('{"ok"', "").split("JSON 만")[0]
    from dealbot.llm_json import _stream_rows, image_blocks

    blk = image_blocks([b"\xff\xd8abc"])
    assert blk[0]["type"] == "image" and blk[0]["source"]["media_type"] == "image/jpeg" and blk[0]["source"]["data"] == "/9hhYmM="
    rows = _stream_rows('{"type":"system"}\nnot json\n{"type":"result","structured_output":{"ok":true}}\n')
    assert rows is not None and rows[-1]["type"] == "result" and _stream_rows("x") is None


# ---------------------------------------------------------------- 4) 커뮤 캡처
async def test_xcap_blurs_the_spots_chrome_reports(tmp_path: Any) -> None:
    from test_dash_xcap import Ask, FakeRouter, Get, nosleep  # noqa: F401
    from test_dash_xcap import NOW as CAP_NOW
    from test_dash_xtoday import FakeNews

    from dealbot.config import XCapConfig, XTodayConfig

    db = FakeDb()
    studio = xs.Studio(db, ask=Ask(), clock=lambda: CAP_NOW)
    today = xt.XToday(db, studio, cfg=lambda: XTodayConfig(goal_posts=False), router=FakeRouter, news=FakeNews(None), clock=lambda: CAP_NOW)
    shot = capture((1520, 2000))  # 크롬 2배 캡처

    async def shoot(url: str, src: xc.Source) -> xc.Shot:
        buf = io.BytesIO()
        shot.save(buf, "PNG")
        return buf.getvalue(), [[NICK[0] / 2, NICK[1] / 2, NICK[2] / 2, NICK[3] / 2, "nick"], [9999, 0, 10, 10, "nick"], "bad"], 2.0

    d = xc.XCap(db, studio, today, cfg=lambda: XCapConfig(sources=["bobaedream"]), get=Get(), shoot=shoot, img_dir=tmp_path, clock=lambda: CAP_NOW, sleep=nosleep)
    item = await d.make_one(CAP_NOW)
    assert item is not None and item["blur"] == 1
    v = d.view()
    assert v["items"][0]["blur"] == 1
    out = Image.open(io.BytesIO(d.img(f"{item['id']}-1.jpg") or b""))
    k = xc.IMG_W / 1520
    nick = (round(NICK[0] * k), round(NICK[1] * k) + 64, round(NICK[2] * k), round(NICK[3] * k))  # 출처 띠(56) + 틈(8) 아래
    assert stripes_left(out, nick) <= 2
    assert all(SRC.who for SRC in xc.SOURCES.values()) and "getBoundingClientRect" in xblur.WHO_JS and "●" in xc.DOT_JS


# ---------------------------------------------------------------- 5) 기사 대표 사진
class Fetch:
    def __init__(self, pages: dict[str, tuple[int, str, bytes]]) -> None:
        self.pages, self.calls = pages, []  # type: ignore[var-annotated]

    async def _get_within(self, url: str, accept: str, *, check_host: bool = True, cap: int = 0) -> tuple[int, str, bytes]:
        self.calls.append(url)
        return self.pages.get(url, (404, "", b""))


def news_desk(tmp_path: Any, pages: dict[str, tuple[int, str, bytes]], **cfg: Any) -> tuple[xp.XPhoto, Fetch]:
    f = Fetch(pages)
    conf = XPhotoConfig(**{"news": True, **cfg})  # 기본은 꺼짐 (저작권) — 받는 길 시험이라 켬
    return xp.XPhoto(FakeDb(), None, None, None, cfg=lambda: conf, img_dir=tmp_path, fetcher=f, ua="dealbot", ocr=lambda _b: "", clock=lambda: 1_800_000_000.0), f  # type: ignore[arg-type]


async def test_news_photo_takes_one_og_image_with_credit_and_respects_robots(tmp_path: Any) -> None:
    art = "https://news.example-daily.co.kr/a/1"
    html = b'<html><head><meta property="og:image" content="/img/top.jpg"></head></html>'
    pic = jpeg(Image.new("RGB", (1200, 800), (10, 90, 160)))
    pages = {"https://news.example-daily.co.kr/robots.txt": (200, "text/plain", b"User-agent: *\nDisallow: /private/\n"),
             art: (200, "text/html; charset=utf-8", html), "https://news.example-daily.co.kr/img/top.jpg": (200, "image/jpeg", pic)}
    d, f = news_desk(tmp_path, pages)
    got = await d.news_photo(art)
    assert got is not None and re.fullmatch(r"n[0-9a-f]{12}\.jpg", got["name"]) and got["credit"] == "example-daily.co.kr"
    assert Image.open(io.BytesIO(d.img(got["name"]) or b"")).size == (1200, 800) and xt.PHOTO_NAME_RE.match(got["name"])
    assert f.calls == ["https://news.example-daily.co.kr/robots.txt", art, "https://news.example-daily.co.kr/img/top.jpg"]
    assert await d.news_photo(art) == got and len(f.calls) == 3  # 같은 기사는 다시 안 받음
    # robots 가 막은 주소 · http 주소 · 그림이 아닌 답 · 하루 상한 · 끔
    assert await d.news_photo("https://news.example-daily.co.kr/private/2") is None and len(f.calls) == 3
    assert await d.news_photo("http://news.example-daily.co.kr/a/3") is None
    d2, f2 = news_desk(tmp_path / "b", {**pages, "https://news.example-daily.co.kr/img/top.jpg": (200, "text/html", b"<html>")})
    assert await d2.news_photo(art) is None
    d3, f3 = news_desk(tmp_path / "c", {art: (200, "text/html", html)})  # robots.txt 를 못 읽음(막힘) → 안 받음
    pages3 = dict(f3.pages)
    pages3["https://news.example-daily.co.kr/robots.txt"] = (403, "", b"")
    f3.pages = pages3
    assert await d3.news_photo(art) is None and art not in f3.calls
    d4, f4 = news_desk(tmp_path / "d", pages, news_per_day=0)
    assert await d4.news_photo(art) is None and f4.calls == []
    d5, f5 = news_desk(tmp_path / "e", pages, news=False)
    assert XPhotoConfig().news is False  # 기본은 꺼짐 (보도 사진 저작권 — 주인이 켤 수 있음)
    assert await d5.news_photo(art) is None and f5.calls == []
    assert xp.og_image('<meta property="og:image" content="http://a.example/x.jpg"><meta name="twitter:image" content="https://a.example/y.jpg">', art) == "https://a.example/y.jpg"
    assert xp.og_image('<meta property="og:image" content="javascript:alert(1)">', art) == ""


# ---------------------------------------------------------------- 6) 화면 파일
def test_xphoto_js_is_local_safe_and_loaded_by_shell() -> None:
    js = (STATIC / "xphoto.js").read_text(encoding="utf-8")
    if NODE:
        for name in ("xphoto.js", "xtrend.js", "xtoday.js", "xcap.js"):
            assert subprocess.run([NODE, "--check", str(STATIC / name)], capture_output=True).returncode == 0, name
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    assert "innerHTML" not in code and "eval(" not in code and ".click()" not in code and re.findall(r"https?://[^\s'\"`)]+", code) == []
    assert "/dashboard/api/upload" in code and "X-Upload-Kind" in code and "X-Dash-Token" in code and "얼굴도 가리기" in code
    assert "faces = false" in code  # 얼굴 가리기 기본 꺼짐
    page = shell.page({})
    assert f'<script defer src="{shell.asset_url("xphoto.js")}"></script>' in page
    assert page.index(shell.asset_url("xtrend.js")) < page.index(shell.asset_url("xphoto.js"))
    tr = (STATIC / "xtrend.js").read_text(encoding="utf-8")
    assert "W.SBXPhoto.form()" in tr and "가린 곳" in tr and "사진 저장" in tr
    assert "가린 곳" in (STATIC / "xcap.js").read_text(encoding="utf-8") and "출처: ${it.photo.credit}" in (STATIC / "xtoday.js").read_text(encoding="utf-8")
    assert needs_node is not None
