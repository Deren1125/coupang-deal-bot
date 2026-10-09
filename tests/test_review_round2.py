"""리뷰 2차 후속: 텔레그램 '사진 없이' 미리보기, 스레드 뺀 말의 같은 꼴, 마무리 고르기, '원대' 끝자리,
상품 페이지·사진 주소 판단, https.sh 의 Caddyfile 쓰기.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from dealbot.cli import sample_deal
from dealbot.commentary import SYSTEM, check_take
from dealbot.config import Settings
from dealbot.enrich import PageMeta
from dealbot.media.imagecheck import is_board_thumb, is_generic_image
from dealbot.models import Deal, DealVerdict, Product
from dealbot.publisher.telegram import TelegramPublisher
from dealbot.publisher.templates import TemplateRenderer, price_band
from dealbot.shops import ShopRegistry

REG = ShopRegistry()
LINK = "https://link.coupang.com/a/x"


def _jpeg(w: int, h: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), (180, 40, 40)).save(out, "JPEG")
    return out.getvalue()


# ================================================================ T-09: 텔레그램, 확인된 사진이 없으면 미리보기도 끔
class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_photo(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(("photo", kw))
        return SimpleNamespace(message_id=1)

    async def send_message(self, **kw):  # type: ignore[no-untyped-def]
        self.calls.append(("message", kw))
        return SimpleNamespace(message_id=2)


def _pub(repo_root: Path, *, send_photo: bool = True) -> tuple[TelegramPublisher, _FakeBot]:
    fake = _FakeBot()
    pub = TelegramPublisher(fake, "-100123", TemplateRenderer(repo_root / "templates"), send_photo=send_photo)  # type: ignore[arg-type]
    return pub, fake


async def test_no_verified_photo_turns_the_link_preview_off(repo_root: Path) -> None:
    """deal_photo 가 None(사진 못 씀) → image_url 도 None. 미리보기 옵션을 안 주면 텔레그램이 제휴 링크 쇼핑몰의 og:image
    (로고·공유 배너일 수 있음)로 미리보기를 만들므로 꺼야 한다."""
    pub, fake = _pub(repo_root)
    d = sample_deal()
    d.product.image_url = None  # deal_photo 가 못 찾으면 비움
    assert (await pub.publish(d, photo=None)).ok
    kind, kw = fake.calls[-1]
    assert kind == "message" and kw["link_preview_options"] is not None and kw["link_preview_options"].is_disabled is True
    assert not [c for c in fake.calls if c[0] == "photo"]


async def test_photo_off_still_shows_the_verified_photo_as_preview(repo_root: Path) -> None:
    """send_photo: false 면 확인된 사진을 바이트로 안 보내되, 같은(확인된) 주소로 큰 미리보기를 띄운다."""
    pub, fake = _pub(repo_root, send_photo=False)
    d = sample_deal()
    d.product.image_url = "https://img.example/verified.jpg"
    assert (await pub.publish(d, photo=_jpeg(800, 800))).ok
    kind, kw = fake.calls[-1]
    opts = kw["link_preview_options"]
    assert kind == "message" and opts is not None and not opts.is_disabled and opts.url == "https://img.example/verified.jpg"
    # 사진 켬이면 확인된 사진 바이트를 그대로 보낸다
    pub, fake = _pub(repo_root, send_photo=True)
    photo = _jpeg(800, 800)
    assert (await pub.publish(d, photo=photo)).ok
    assert fake.calls[-1][0] == "photo" and fake.calls[-1][1]["photo"] == photo


# ================================================================ TH-02: 판단 줄은 가격·할인율을 되풀이하지 않음
FACTS = "쿠팡 최저가 35,100원보다 29% 싸요. / 개당 1,246원 꼴"
NAME = "스탠리 퀜처 1.18L"


@pytest.mark.parametrize("text", ["이 가격이면 29% 싸게 사는 셈", "개당 1,246원 꼴이라 부담 없음", "35,100원짜리를 이 값에 삼"])
def test_take_does_not_restate_price_or_percent(text: str) -> None:
    assert check_take(text, FACTS, NAME) is None


def test_plain_takes_still_pass() -> None:
    for ok in ("용량 커서 사무실 책상용으로 괜찮음", "1.18L라 차 컵홀더엔 안 들어감"):
        assert check_take(ok, FACTS, NAME) == ok, ok


def test_prompt_no_longer_pushes_im_endings_and_says_take_skips_the_price() -> None:
    assert "음슴체(~임" not in SYSTEM  # '…임' 끝을 시키면 '…원임' 꼴이 나옴
    assert "'임'을 붙이지 않는다" in SYSTEM and "다시 말하지 않는다" in SYSTEM
    for s in ("개당 1,246원 꼴임", "N% 싸짐", "쿠팡보다 N%", "아는 사람만 챙김", "찾는 사람 있을 것 같아서 올려둠", "링크 댓글에"):
        assert s in SYSTEM, s


def test_pools_drop_the_gatekeeping_and_superlative_lines() -> None:
    hooks = [h for h, _ in (*TemplateRenderer.THREAD_HOOKS, *TemplateRenderer.THREAD_HOOKS_NOPRICE)]
    assert "이런 건 아는 사람만 챙김" not in hooks
    assert not [h for h in hooks if "제일" in h or "사람만" in h], hooks  # '오늘 본 것 중에 제일…'은 하루 여러 번이면 거짓말


@pytest.mark.parametrize(
    ("name", "kept"),
    [("다우니 섬유유연제 평소보다 20% 싸게", "다우니 섬유유연제"), ("하기스 기저귀 쿠팡보다 10% 저렴", "하기스 기저귀"),
     ("다우니 30% 저렴하게 4L", "다우니 4L"), ("드림카카오 72% 86g", "드림카카오 72% 86g")],
)
async def test_name_with_a_percent_claim_does_not_block_the_post(repo_root: Path, name: str, kept: str) -> None:
    """게시판 제목에 붙은 '평소보다 20% 싸게' 같은 말이 이름 줄에 그대로 나가면 뺀 말 검사에 걸려 글이 안 올라간다 → 이름에서 뺌.
    '카카오 72%' 같은 상품 정보는 둔다."""
    from dealbot.publisher.threads import thread_problems

    r = TemplateRenderer(repo_root / "templates")
    shop = REG.get("coupang")
    assert shop is not None
    d = Deal(Product(source="s", product_id="coupang:9", shop="coupang", name=name, price=19900, url="u"),
             DealVerdict(is_deal=True), affiliate_url=LINK)
    th = r.render_deal(d, LINK, shop=shop, template="deal_threads.j2", autoescape=False)
    assert f"{kept} 19,900원" in th, th
    assert thread_problems(f"{th}\n👉 {LINK}", disclosure=shop.disclosure, link=LINK) == []
    # 쿠폰 이름의 할인율은 그대로 (그게 쿠폰 내용)
    c = Deal(Product(source="s", product_id="coupang:c", shop="coupang", name="쿠팡 와우 10% 할인쿠폰", price=0, url="u", deal_kind="coupon"),
             DealVerdict(is_deal=True), affiliate_url=LINK)
    assert "10% 할인쿠폰" in r.render_deal(c, LINK, shop=REG.get("coupang"), template="deal_threads.j2", autoescape=False)


# ================================================================ 마무리: 가전·전자기기에 '쟁여', 질문 두 번
@pytest.fixture
def r(repo_root: Path) -> TemplateRenderer:
    return TemplateRenderer(repo_root / "templates")


@pytest.mark.parametrize("name", ["삼성 비스포크 냉장고", "맥북 에어 노트북", "LG 모니터 27인치", "다이슨 청소기 V12"])
def test_durables_never_get_stockpile_lines(r: TemplateRenderer, name: str) -> None:
    group = next(hooks for words, _, hooks in TemplateRenderer.THREAD_HOOK_GROUPS if any(w in name for w in words))
    for i in range(120):
        p = Product(source="s", product_id=f"coupang:{i}", shop="coupang", name=name, price=990000, url="u")
        # 그룹 문구를 다 최근에 썼으면 일반 문구로 넘어가는데, 거기서도 '쟁여'는 빠져야 함
        for recent in ([], [h for h, _ in group]):
            t = r.thread_lines(p, recent=recent)
            assert "쟁여" not in t["thread_hook"] and "쟁여" not in (t["thread_close"] or ""), (name, t)


def test_one_question_per_post(r: TemplateRenderer) -> None:
    pairs = 0
    for i in range(400):
        p = Product(source="s", product_id=f"coupang:{i}", shop="coupang", name=f"아무 상품 {i}", price=10000, url="u")
        t = r.thread_lines(p, recent=[])
        if t["thread_hook"].endswith("?"):
            pairs += 1
            assert not (t["thread_close"] or "").endswith("?"), t
    assert pairs  # 질문 첫 줄이 실제로 나오는 상황을 확인함
    # AI 첫 줄이 질문이어도 같음
    for i in range(40):
        p = Product(source="s", product_id=f"coupang:q{i}", shop="coupang", name="상품", price=10000, url="u",
                    extra={"thread_hook": "다들 이런 거 어디다 둠?"})
        assert not (r.thread_lines(p)["thread_close"] or "").endswith("?")


# ================================================================ '평소엔 N원대': 끝자리를 내려 대략 구간으로
@pytest.mark.parametrize(
    ("ref", "price", "band"),
    [(41233, 29000, 41000), (1234567, 900000, 1200000), (8750, 5000, 8700), (42000, 29900, 42000), (10900, 10000, 10900)],
)
def test_price_band(ref: int, price: int, band: int) -> None:
    assert price_band(ref, price) == band


def test_average_evidence_is_a_rounded_band_but_market_price_stays_exact(r: TemplateRenderer) -> None:
    d = Deal(Product(source="s", product_id="coupang:1", shop="coupang", name="스탠리 퀜처 1.18L", price=29000, url="u"),
             DealVerdict(is_deal=True, avg_price=41233, below_avg_pct=29.7), affiliate_url=LINK)
    th = r.render_deal(d, LINK, shop=REG.get("coupang"), template="deal_threads.j2", autoescape=False)
    assert "평소엔 41,000원대" in th and "41,233" not in th
    assert r.deal_facts(d)["evidence"] == "평소 41,000원대인데 오늘 29,000원이에요."
    m = Deal(Product(source="s", product_id="coupang:2", shop="coupang", name="스탠리 퀜처 1.18L", price=24900, url="u"),
             DealVerdict(is_deal=True, market_price=35130, below_market_pct=29.1), affiliate_url=LINK)
    assert "쿠팡 최저가는 35,130원" in r.render_deal(m, LINK, shop=REG.get("coupang"), template="deal_threads.j2", autoescape=False)


# ================================================================ 사진 주소: 쇼핑몰 '/thumbnails/' 는 크기로, 낱말은 낱말 단위로
def test_shop_thumbnail_paths_are_not_board_thumbs() -> None:
    assert not is_board_thumb("https://image.msscdn.net/thumbnails/images/goods_img/20240101/123/123_500.jpg")
    assert not is_board_thumb("https://thumbnail7.coupangcdn.com/thumbnails/remote/1000x1000ex/image/a.jpg")
    assert is_board_thumb("https://cdn2.ppomppu.co.kr/zboard/data3/m_thumb_1.jpg")
    assert is_board_thumb("https://i1.ruliweb.com/img/1.jpg")
    assert not is_board_thumb("https://notppomppu.co.kr.example.com/a.jpg")


def test_generic_image_words_match_whole_words_only() -> None:
    assert not is_generic_image("https://shop.example/products/weighted-blanket.jpg")
    assert not is_generic_image("https://cdn.x.com/common/goods/1/main.jpg")
    for u in ("https://cdn.x.com/img/default_banner.jpg", "https://cdn.x.com/defaultImg.png", "https://cdn.x.com/noImage.gif",
              "https://pics.gmarket.co.kr/common/logo_og.png", "https://cdn.x.com/blank.gif"):
        assert is_generic_image(u), u


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


async def test_deal_photo_uses_a_big_shop_image_under_thumbnails(bot) -> None:  # type: ignore[no-untyped-def]
    big = "https://image.msscdn.net/thumbnails/images/goods_img/20240101/123/123_500.jpg"
    small = "https://image.msscdn.net/thumbnails/images/goods_img/20240101/123/123_125.jpg"
    images = {big: _jpeg(1000, 1000), small: _jpeg(125, 125)}

    async def fetch(url: str, **_kw: object) -> bytes | None:
        return images.get(url)

    async def no_page(url: str) -> PageMeta | None:
        return None

    bot._fetch_image = fetch
    bot.enricher.fetch = no_page  # type: ignore[method-assign]
    d = Deal(Product(source="s", product_id="naver:1", shop="naver", name="상품", price=9900, url="https://www.musinsa.com/products/123",
                     image_url=big), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is not None and d.product.image_url == big
    d = Deal(Product(source="s", product_id="naver:2", shop="naver", name="상품", price=9900, url="https://www.musinsa.com/products/124",
                     image_url=small), DealVerdict(is_deal=True))
    assert await bot.deal_photo(d) is None and d.product.image_url is None  # 작은 건 크기 검사(600px)가 거른다


# ================================================================ https.sh: Caddyfile 을 비운 채 끝나지 않음
pytestmark_bash = pytest.mark.skipif(not (shutil.which("git") and shutil.which("bash")), reason="git·bash 필요")
OTHER = "blog.example.com {\n\treverse_proxy 127.0.0.1:3000\n}\n"


def _caddy(tmp_path: Path, caddyfile: bytes, *, validate_ok: bool = True, include: str | None = None):  # type: ignore[no-untyped-def]
    from test_deploy_lightsail import Server

    server = Server(tmp_path)
    sim = server.sim
    sudo = (sim / "bin" / "sudo").read_text(encoding="utf-8")
    (sim / "bin" / "sudo").write_text(sudo.replace("  cp) shift; exec cp \"$@\" ;;", "  cp) shift; exec cp \"$@\" ;;\n  caddy) shift; exec caddy \"$@\" ;;"),
                                      encoding="utf-8")
    (sim / "bin" / "caddy").write_text(
        '#!/usr/bin/env bash\necho "caddy $*" >> "$SIM/calls.log"\n'
        + ("" if validate_ok else '[ "$1" = validate ] && exit 1\n') + "exit 0\n", encoding="utf-8")
    (sim / "etc" / "Caddyfile").write_bytes(caddyfile)
    if include is not None:
        (sim / "etc" / "dealbot.caddy").write_text(include, encoding="utf-8")
    (server.srv / ".env").write_text("TELEGRAM_BOT_TOKEN=fake\n", encoding="utf-8")
    server.env["CADDY_DIR"] = str(sim / "etc")
    server.env["FAKE_IP"] = "3.35.1.2"
    return server, server.run(script="https.sh")


@pytestmark_bash
def test_https_leaves_the_caddyfile_alone_when_it_cannot_be_read(tmp_path: Path) -> None:
    """UTF-8 이 아닌 Caddyfile: 예전엔 'python | sudo tee Caddyfile' 이라 tee 가 먼저 비우고 python 이 실패 → 빈 Caddyfile."""
    before = OTHER.encode() + "# caf\xe9\n".encode("latin-1")
    server, r = _caddy(tmp_path, before)
    assert r.returncode == 1 and "아무것도 바꾸지 않았어요" in r.stdout, r.stdout + r.stderr
    assert (server.sim / "etc" / "Caddyfile").read_bytes() == before
    assert not (server.sim / "etc" / "dealbot.caddy").exists()
    assert "restart caddy" not in server.log("calls.log")


@pytestmark_bash
def test_https_restores_dealbot_caddy_too_when_validation_fails(tmp_path: Path) -> None:
    """옛 Caddyfile 이 이미 dealbot.caddy 를 import 하면, Caddyfile 만 되돌려서는 새 dealbot.caddy 가 그대로 읽힌다."""
    etc = tmp_path / "sim" / "etc"
    before = f"{OTHER}\nimport {etc}/dealbot.caddy\n"
    old_inc = "1-2-3-4.sslip.io {\n\treverse_proxy 127.0.0.1:8080\n}\n"
    server, r = _caddy(tmp_path, before.encode(), validate_ok=False, include=old_inc)
    assert r.returncode == 1 and "되돌렸어요" in r.stdout
    assert (etc / "Caddyfile").read_text(encoding="utf-8") == before
    assert (etc / "dealbot.caddy").read_text(encoding="utf-8") == old_inc
    assert "restart caddy" not in server.log("calls.log")


@pytestmark_bash
def test_https_still_writes_the_import_when_all_is_well(tmp_path: Path) -> None:
    server, r = _caddy(tmp_path, OTHER.encode())
    assert r.returncode == 0, r.stdout + r.stderr
    caddy = (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8")
    assert OTHER.strip() in caddy and caddy.count(f"import {server.sim / 'etc'}/dealbot.caddy") == 1
    assert "3-35-1-2.sslip.io {" in (server.sim / "etc" / "dealbot.caddy").read_text(encoding="utf-8")
