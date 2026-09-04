"""API 가 없는 플랫폼(카카오 오픈채팅, 네이버 블로그)용 '복붙 문구' 생성.

발행에 성공하면 관리자 챗으로 각 플랫폼 양식의 완성된 글을 보낸다.
텔레그램에서 <pre> 블록으로 보내면 길게 눌러 전체 복사가 쉬워진다.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from dealbot.config import CopyConfig
from dealbot.models import Deal
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CopyBlock:
    key: str
    name: str
    text: str

    def as_telegram_html(self) -> str:
        return f"📋 <b>{html.escape(self.name)}</b> 복사용\n<pre>{html.escape(self.text)}</pre>"


class CopyBlockBuilder:
    def __init__(self, cfg: CopyConfig, renderer: TemplateRenderer, registry: ShopRegistry | None = None) -> None:
        self.cfg = cfg
        self.renderer = renderer
        self.registry = registry or ShopRegistry()

    @property
    def enabled(self) -> bool:
        return self.cfg.enabled and any(t.enabled for t in self.cfg.targets)

    def build(self, deal: Deal, only: str | None = None) -> list[CopyBlock]:
        if not self.cfg.enabled:
            return []
        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        blocks: list[CopyBlock] = []
        for target in self.cfg.targets:
            if not target.enabled or (only and target.key != only):
                continue
            try:
                text = self.renderer.render_deal(deal, link, shop=shop, template=target.template, autoescape=False)
            except Exception as e:  # noqa: BLE001
                log.warning("copy block '%s' render failed: %s", target.key, e)
                continue
            blocks.append(CopyBlock(key=target.key, name=target.name, text=text))
        return blocks
