"""Jinja2 템플릿 렌더링 (텔레그램 HTML parse_mode 기준, 자동 이스케이프)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from dealbot.models import Deal
from dealbot.shops import Shop
from dealbot.utils.text import format_won


def _pct(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


class TemplateRenderer:
    def __init__(self, templates_dir: Path, tz: str = "Asia/Seoul") -> None:
        self.templates_dir = Path(templates_dir)
        self.tz = ZoneInfo(tz)
        # 텔레그램(HTML 서식)용: 특수문자 이스케이프
        self.env = self._build_env(autoescape=True)
        # 카카오·스레드·블로그(평문)용: 이스케이프하면 &lt; 같은 문자가 그대로 복사되므로 끔
        self.env_plain = self._build_env(autoescape=False)

    def _build_env(self, *, autoescape: bool) -> Environment:
        env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(default=True, default_for_string=True) if autoescape else False,
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        env.filters["won"] = format_won
        env.filters["pct"] = _pct
        env.filters["local"] = self._local
        return env

    def _local(self, dt: datetime | str | None, fmt: str = "%m/%d %H:%M") -> str:
        if dt is None:
            return "-"
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(self.tz).strftime(fmt)

    def render(self, name: str, *, autoescape: bool = True, **ctx: Any) -> str:
        env = self.env if autoescape else self.env_plain
        template = env.get_template(name)
        ctx.setdefault("now", datetime.now(self.tz))
        return template.render(**ctx).strip()

    def render_deal(
        self,
        deal: Deal,
        link: str,
        *,
        shop: Shop | None = None,
        template: str = "deal_post.j2",
        autoescape: bool = True,
    ) -> str:
        p = deal.product
        shop_ctx = {
            "key": shop.key if shop else p.shop,
            "name": shop.name if shop else p.shop,
            "disclosure": shop.disclosure if shop else None,
            "link_mode": shop.link_mode if shop else "raw",
        }
        return self.render(
            template,
            autoescape=autoescape,
            product=p,
            shop=shop_ctx,
            verdict=deal.verdict,
            link=link,
            discount_rate=deal.verdict.discount_rate if deal.verdict.discount_rate is not None else p.effective_discount_rate(),
            avg_price=deal.verdict.avg_price,
            below_avg_pct=deal.verdict.below_avg_pct,
            market_price=deal.verdict.market_price,
            market_source=deal.verdict.market_source,
            below_market_pct=deal.verdict.below_market_pct,
            detected_at=deal.detected_at,
        )
