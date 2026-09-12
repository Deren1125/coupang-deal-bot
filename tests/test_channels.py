from __future__ import annotations

from pathlib import Path

from dealbot.cli import sample_deal
from dealbot.config import ChannelsConfig
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry

KAKAO = "https://open.kakao.com/o/abc123"
TG = "https://t.me/oneul_hotdeal"


def test_channels_default_empty() -> None:
    c = ChannelsConfig()
    assert c.as_dict() == {"telegram_url": "", "kakao_openchat_url": "", "threads_url": "", "telegram_name": "오늘의 핫딜", "kakao_openchat_name": "오늘의 핫딜 오픈채팅"}


def test_post_footer_only_when_kakao_set(repo_root: Path) -> None:
    shop = ShopRegistry().get("coupang")
    plain = TemplateRenderer(repo_root / "templates").render_deal(sample_deal(), "https://l", shop=shop)
    assert "카톡 오픈채팅" not in plain
    with_kakao = TemplateRenderer(repo_root / "templates", channels={"kakao_openchat_url": KAKAO}).render_deal(sample_deal(), "https://l", shop=shop)
    assert f"💬 카톡 오픈채팅: {KAKAO}" in with_kakao
    assert with_kakao.index("https://l") < with_kakao.index(KAKAO) < with_kakao.index("쿠팡 파트너스")


def test_kakao_copy_points_to_telegram(repo_root: Path) -> None:
    shop = ShopRegistry().get("coupang")
    r = TemplateRenderer(repo_root / "templates", channels={"telegram_url": TG})
    text = r.render_deal(sample_deal(), "https://l", shop=shop, template="deal_kakao.j2", autoescape=False)
    assert f"📲 실시간 전체 딜(텔레그램): {TG}" in text
    r2 = TemplateRenderer(repo_root / "templates")
    assert "텔레그램" not in r2.render_deal(sample_deal(), "https://l", shop=shop, template="deal_kakao.j2", autoescape=False)


def test_threads_reply_link_fallback(repo_root: Path) -> None:
    shop = ShopRegistry().get("coupang")
    kw = dict(shop=shop, template="deal_threads_reply.j2", autoescape=False)
    assert "프로필 링크 👆" in TemplateRenderer(repo_root / "templates").render_deal(sample_deal(), "https://l", **kw)
    only_kakao = TemplateRenderer(repo_root / "templates", channels={"kakao_openchat_url": KAKAO}).render_deal(sample_deal(), "https://l", **kw)
    assert f"실시간 전체 딜 👉 {KAKAO}" in only_kakao
    both = TemplateRenderer(repo_root / "templates", channels={"kakao_openchat_url": KAKAO, "telegram_url": TG}).render_deal(sample_deal(), "https://l", **kw)
    assert TG in both and KAKAO not in both
