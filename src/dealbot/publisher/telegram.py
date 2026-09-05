"""텔레그램 채널 발행."""

from __future__ import annotations

import logging

from telegram import Bot, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

from dealbot.models import Deal, PublishResult
from dealbot.publisher.templates import TemplateRenderer
from dealbot.shops import ShopRegistry

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


def normalize_chat_id(chat_id: str | int | None) -> str | int | None:
    if chat_id is None:
        return None
    s = str(chat_id).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    return s


class TelegramPublisher:
    def __init__(
        self,
        bot: Bot | None,
        channel_id: str | int | None,
        renderer: TemplateRenderer,
        *,
        registry: ShopRegistry | None = None,
        template: str = "deal_post.j2",
        send_photo: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.bot = bot
        self.channel_id = normalize_chat_id(channel_id)
        self.renderer = renderer
        self.registry = registry or ShopRegistry()
        self.template = template
        self.send_photo = send_photo
        self.dry_run = dry_run or bot is None or channel_id is None

    def render(self, deal: Deal) -> str:
        link = deal.affiliate_url or deal.product.url
        shop = self.registry.get(deal.product.shop)
        return self.renderer.render_deal(deal, link, shop=shop, template=self.template)

    SOLD_OUT_BANNER = "⛔ <b>품절 또는 종료된 딜입니다</b>\n\n"

    async def mark_sold_out(self, channel_id: str | int, message_id: int, original_text: str) -> bool:
        """이미 올린 채널 글 맨 위에 품절 표시를 붙인다. 사진 글이면 캡션을, 아니면 본문을 고친다."""
        if self.bot is None:
            return False
        text = self.SOLD_OUT_BANNER + original_text
        try:
            await self.bot.edit_message_text(chat_id=channel_id, message_id=message_id, text=text[:MESSAGE_LIMIT], parse_mode=ParseMode.HTML)
            return True
        except BadRequest as e:
            if "no text" not in str(e).lower() and "caption" not in str(e).lower():
                log.warning("mark_sold_out edit_message_text failed: %s", e)
                if "not modified" in str(e).lower():
                    return True
        except TelegramError as e:
            log.warning("mark_sold_out failed: %s", e)
            return False
        try:
            await self.bot.edit_message_caption(chat_id=channel_id, message_id=message_id, caption=text[:CAPTION_LIMIT], parse_mode=ParseMode.HTML)
            return True
        except TelegramError as e:
            log.warning("mark_sold_out edit_message_caption failed: %s", e)
            return False

    async def publish(self, deal: Deal) -> PublishResult:
        text = self.render(deal)
        if self.dry_run:
            log.info("[DRY-RUN] would publish to %s:\n%s", self.channel_id, text)
            return PublishResult(ok=True, dry_run=True)

        assert self.bot is not None and self.channel_id is not None
        image = deal.product.image_url
        try:
            if self.send_photo and image and len(text) <= CAPTION_LIMIT:
                try:
                    msg = await self.bot.send_photo(
                        chat_id=self.channel_id, photo=image, caption=text, parse_mode=ParseMode.HTML
                    )
                    return PublishResult(ok=True, message_id=msg.message_id)
                except BadRequest as e:
                    # 이미지 URL 문제 등 → 텍스트 발행으로 대체
                    log.warning("send_photo failed (%s); falling back to text message", e)

            preview = LinkPreviewOptions(url=image, prefer_large_media=True, show_above_text=True) if image else None
            msg = await self.bot.send_message(
                chat_id=self.channel_id,
                text=text[:MESSAGE_LIMIT],
                parse_mode=ParseMode.HTML,
                link_preview_options=preview,
            )
            return PublishResult(ok=True, message_id=msg.message_id)
        except RetryAfter as e:
            return PublishResult(ok=False, error=f"rate limited by telegram, retry after {e.retry_after}s")
        except TelegramError as e:
            return PublishResult(ok=False, error=f"{type(e).__name__}: {e}")
