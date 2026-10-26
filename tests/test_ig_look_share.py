"""데런픽 룩 인스타 대기열 (주인 10/26 — growth/look_share): 연결 전엔 기다림 · 하루 2편 · 3시간 간격 · 시간대 안 ·
모든 장 '광고' 칩 카드 · 캡션 검사 · 딜 계정 간격·통계와 따로. 인스타·메타에는 나가지 않는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from dealbot.growth import look_share
from dealbot.media import card_look
from dealbot.publisher import instagram as ig_mod
from dealbot.publisher.instagram import InstagramClient, InstagramToken
from test_ig_schedule import DAY, _carousel_handler, _plan, at, bot, clock, live, tick  # noqa: F401 — 같은 봇·시계 준비

CAPTION = ("광고 · 제휴 링크로 수수료를 받아요\n\n카리나 공항룩, 비슷하게 입는 법\n\n글 속 블랙 레더 재킷,\n분위기 비슷한 걸로 골랐고\n"
           "카리나 착용 제품은 아니에요\n\n🔗 전체 글은 프로필 링크에서\n사진: 에이블리·지그재그 상품 이미지\n\n"
           "#광고 #카리나 #카리나패션 #공항패션 #데런픽")


def _look(bot: Any, tmp: Path, pid: int = 31, *, after: datetime | None = None, n: int = 3, caption: str = CAPTION,
          title: list[str] | None = None, image_dir: Path | None = None) -> Path:
    var = tmp / "Blog-Auto" / "var"
    (var / "metrics").mkdir(parents=True, exist_ok=True)
    bot.settings.dashboard.blogauto_metrics_path = str(var / "metrics" / "blogauto.json")
    d = var / "look_share" / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for i in range(1, n + 1):
        p = (image_dir or d) / f"{i:02d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (700, 900), (40 * i, 60, 90)).save(p, "JPEG")
        items.append({"name": f"크롭 레더 자켓 {i}", "price": 39000 + i, "mall": "에이블리", "piece": "블랙 레더 재킷", "same": False,
                      "image": str(p)})
    rec = {"v": 1, "kind": "look", "pid": pid, "title": "카리나 공항 패션", "url": "https://blog.naver.com/derenpick/9",
           "person": "카리나", "caption": caption, "post_after": (after or at(10)).isoformat(), "cancelled": False,
           "deck": {"person": "카리나", "kicker": "공항 패션 · 10월 13일 글", "title": title or ["카리나 공항 패션,", f"비슷한 옷 {n}가지"],
                    "sub": "카리나 착용 제품 아님 · 비슷한 상품이에요", "end_kicker": "카리나 공항 패션 · 한눈에",
                    "end_line": "전체 글은 프로필 링크에서", "price_note": "가격은 10/13 기준이에요", "price_day": "10/13", "items": items}}
    path = var / "look_share" / f"{pid}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return path


def _look_live(bot: Any, monkeypatch: Any, published_id: str = "7700") -> list[httpx.Request]:
    """데런픽 룩 계정을 가짜 API 로 연결 (계정 번호 LUID). 딜 계정 요청은 따로 기록."""
    seen: list[httpx.Request] = []
    count = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/v25.0/LUID/media":
            count["n"] += 1
            return httpx.Response(200, json={"id": f"L{count['n']}"})
        if req.url.path == "/v25.0/LUID/media_publish":
            return httpx.Response(200, json={"id": published_id})
        if req.url.path.endswith(f"/{published_id}"):
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/LOOK/"})
        return httpx.Response(200, json={"status_code": "FINISHED"})

    monkeypatch.setattr(ig_mod, "CONTAINER_POLL_SECONDS", 0.01)
    monkeypatch.setattr(ig_mod, "PUBLISH_ATTEMPT_DELAYS", (0.0,))
    bot.settings.secrets.instagram_app_id = "IGAPP"
    bot.settings.secrets.instagram_app_secret = "IGSECRET"
    bot.state.dry_run = False
    pub = bot.instagram_look
    pub.dry_run = False
    pub.save_token(InstagramToken("LT", "LUID", expires_at=datetime(2099, 1, 1, tzinfo=UTC)), "derenpick.look")
    pub.client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), retry_backoff=0.01)
    return seen


async def test_waits_until_the_look_account_is_connected(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _plan(bot, {})
    _look(bot, tmp_path)
    bot.settings.secrets.instagram_app_id = "IGAPP"
    bot.settings.secrets.instagram_app_secret = "IGSECRET"
    bot.instagram_look.dry_run = False
    assert await tick(bot, clock, at(11)) is None  # 연결 전 — 기다리기만
    assert bot.db.kv_get("look_share:ig:31") is None
    assert look_share.waiting_count(bot) == 1
    row = look_share.todo_item(bot, 0, lambda label, route: {"goto": route}, waiting=look_share.waiting_count(bot))
    assert row is not None and "패션 글 1편이 기다려요" in row["status"]


async def test_posts_a_carousel_with_ad_chip_cards_to_the_look_account_only(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    deal_seen = live(bot, _carousel_handler("9100"), monkeypatch)
    seen = _look_live(bot, monkeypatch)
    _plan(bot, {})
    _look(bot, tmp_path)
    assert await tick(bot, clock, at(9, 30)) is None  # 차례 전
    assert await tick(bot, clock, at(10, 1)) == "look #31: posted 7700"
    creates = [r for r in seen if r.url.path == "/v25.0/LUID/media"]
    items, parent = creates[:-1], creates[-1]
    assert len(items) == 5  # 표지 + 상품 3 + 끝 카드
    assert all(r.url.params["image_url"].startswith("https://bot.example.com/media/look_") for r in items)
    assert all(r.url.params.get("alt_text") for r in items)
    assert parent.url.params["media_type"] == "CAROUSEL" and parent.url.params["caption"] == CAPTION
    assert not [r for r in deal_seen if "/media" in r.url.path]  # 딜 계정으로는 아무것도 안 감
    assert not list((bot.settings.data_dir / "media").glob("look_*"))  # 올린 뒤 공개 사본은 지움
    rows = bot.db.ig_posts_since(at(0))
    assert [r["kind"] for r in rows] == ["look"] and rows[0]["permalink"] == "https://www.instagram.com/p/LOOK/"
    assert bot.db.ig_last_post_at(exclude=("look",)) is None  # 딜 계정 간격에는 안 셈
    srcs = {x.key: x for x in bot.dashboard.sources}  # type: ignore[union-attr]
    clock["now"] = at(12)
    assert srcs["instagram"].counts(rows[0]) is False and srcs["instagram_look"].counts(rows[0]) is True
    assert await tick(bot, clock, at(14)) is None  # 같은 글은 한 번


async def test_two_a_day_three_hours_apart_inside_the_hours(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _look_live(bot, monkeypatch)
    _plan(bot, {})
    for pid in (41, 42, 43):
        _look(bot, tmp_path, pid, after=at(6))
    bot.settings.instagram.hours = "07:00-23:00"
    assert await tick(bot, clock, at(6, 30)) is None  # 시간대 전
    assert (await tick(bot, clock, at(7, 1)) or "").startswith("look #41: posted")
    assert await tick(bot, clock, at(9, 59)) is None  # 3시간 안
    assert (await tick(bot, clock, at(10, 2)) or "").startswith("look #42: posted")
    assert await tick(bot, clock, at(14)) is None  # 하루 2편
    next_day = DAY + timedelta(days=1)
    assert (await tick(bot, clock, at(8, day=next_day)) or "").startswith("look #43: posted")  # 다음 날 다시 (기다린 글)


async def test_bad_records_are_skipped_not_posted(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    seen = _look_live(bot, monkeypatch)
    _plan(bot, {})
    _look(bot, tmp_path, 51, caption=CAPTION.replace("광고 · 제휴 링크로 수수료를 받아요\n\n", ""))
    assert "skipped" in (await tick(bot, clock, at(10, 1)) or "")
    _look(bot, tmp_path, 52, caption=CAPTION + "\nblog.naver.com/derenpick")
    assert "주소" in (await tick(bot, clock, at(10, 2)) or "")
    _look(bot, tmp_path, 53, title=["카리나 공항 패션,", "비슷한 옷 5가지"])
    assert "N가지" in (await tick(bot, clock, at(10, 3)) or "")
    _look(bot, tmp_path, 54, image_dir=tmp_path / "elsewhere")  # 글 폴더 밖 사진 (셀럽 사진이 끼어들 길)
    assert "폴더 밖" in (await tick(bot, clock, at(10, 4)) or "")
    assert not [r for r in seen if r.url.path.endswith("/media")]


async def test_failure_rests_only_the_look_account(bot, clock, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _look_live(bot, monkeypatch)
    _plan(bot, {})

    def broken(req: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid OAuth access token", "code": 190}})

    bot.instagram_look.client = InstagramClient(httpx.AsyncClient(transport=httpx.MockTransport(broken)), retry_backoff=0.01)
    _look(bot, tmp_path, 61)
    assert "failed" in (await tick(bot, clock, at(10, 1)) or "")
    assert bot.db.kv_get(look_share.KV_BACKOFF) and not bot.db.kv_get("ig_backoff_until")
    sns = bot.dash_state.sns()  # type: ignore[union-attr]
    assert "최근 실패" in sns["instagram_look"]["text"] and "최근 실패" not in sns["instagram"]["text"]


def test_cards_are_1080x1350_with_the_ad_chip_on_every_slide(tmp_path) -> None:
    rec = json.loads(_look_rec(tmp_path))
    deck = look_share.deck_of(rec)
    slides = card_look.render(deck)
    assert len(slides) == 5
    for jpg, alt in slides:
        im = Image.open(__import__("io").BytesIO(jpg))
        assert im.size == (1080, 1350) and alt
        # '광고' 칩: 왼쪽 위 칩 자리(80~120, 70~120) 가 둘레 바탕과 다른 색 · 오른쪽 위 210×110 은 비움
        chip = im.crop((82, 72, 100, 118)).convert("L").getextrema()
        assert chip[1] - chip[0] > 60 or chip[0] < 40
        corner = im.crop((1080 - 210, 0, 1080, 110)).convert("L").getextrema()
        assert corner[1] - corner[0] < 12
    assert "광고" in deck.texts() and "카리나 착용 제품 아님" in deck.texts()


def _look_rec(tmp: Path) -> str:
    class _B:  # 기록만 만드는 가짜 봇
        class settings:  # noqa: N801
            class dashboard:  # noqa: N801
                blogauto_metrics_path = ""

    b: Any = _B()
    return _look(b, tmp).read_text(encoding="utf-8")
