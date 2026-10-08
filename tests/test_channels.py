from __future__ import annotations

import re
from pathlib import Path

from dealbot.cli import sample_deal
from dealbot.config import ChannelsConfig
from dealbot.models import Deal, DealVerdict, Product
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
    assert "카톡 오픈채팅" not in with_kakao  # 오픈채팅 안내는 글마다 넣지 않음 (고정 공지로)
    assert with_kakao.index("https://l") < with_kakao.index("쿠팡 파트너스")  # 제휴 고지는 메시지 끝 (상위 채널 관례)


def test_kakao_copy_has_no_channel_ad_or_tier_filler(repo_root: Path) -> None:
    """카톡 복붙 문구도 채널 링크를 글마다 붙이지 않고, '역대급 가격입니다' 같은 확인 안 되는 등급 문구를 쓰지 않는다."""
    shop = ShopRegistry().get("coupang")
    r = TemplateRenderer(repo_root / "templates", channels={"telegram_url": TG, "kakao_openchat_url": KAKAO})
    text = r.render_deal(sample_deal(), "https://l", shop=shop, template="deal_kakao.j2", autoescape=False)
    assert "텔레그램" not in text and TG not in text and KAKAO not in text and "📲" not in text
    assert "👉 https://l" in text and text.rstrip().endswith("수수료를 제공받습니다.")  # 제휴 고지는 그대로 끝에
    for v in ({"below_avg_pct": 55.0, "avg_price": 27000}, {"below_market_pct": 72.5, "market_price": 45000}):  # must / top 등급
        p = Product(source="s", product_id="coupang:9", shop="coupang", name="갈아만든배 340ml 24개", price=12360, url="u")
        tiered = r.render_deal(Deal(product=p, verdict=DealVerdict(is_deal=True, **v)), "https://l", shop=shop, template="deal_kakao.j2", autoescape=False)
        assert tiered.startswith(("🔥 ", "🚨 ")) and not re.search(r"역대급|다시 보기 어렵|절반입니다|꼭 담으세요", tiered), tiered


def test_threads_reply_link_fallback(repo_root: Path) -> None:
    shop = ShopRegistry().get("coupang")
    kw = dict(shop=shop, template="deal_threads_reply.j2", autoescape=False)
    assert "실시간 딜은 프로필 링크 👆" in TemplateRenderer(repo_root / "templates").render_deal(sample_deal(), "https://l", **kw)
    only_kakao = TemplateRenderer(repo_root / "templates", channels={"kakao_openchat_url": KAKAO}).render_deal(sample_deal(), "https://l", **kw)
    assert f"실시간으로 올라오는 곳 👉 {KAKAO}" in only_kakao
    both = TemplateRenderer(repo_root / "templates", channels={"kakao_openchat_url": KAKAO, "telegram_url": TG}).render_deal(sample_deal(), "https://l", **kw)
    assert TG in both and KAKAO not in both
