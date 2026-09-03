"""Jinja2 템플릿 렌더링 (텔레그램 HTML parse_mode 기준, 자동 이스케이프)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from dealbot.models import Deal
from dealbot.utils.text import format_won


def _pct(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


class TemplateRenderer:
    def __init__(self, templates_dir: Path, tz: str = "Asia/Seoul") -> None:
        self.templates_dir = Path(templates_dir)
        self.tz = ZoneInfo(tz)
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=select_autoescape(default=True, default_for_string=True),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.filters["won"] = format_won
        self.env.filters["pct"] = _pct
        self.env.filters["local"] = self._local

    def _local(self, dt: datetime | str | None, fmt: str = "%m/%d %H:%M") -> str:
        if dt is None:
            return "-"
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(self.tz).strftime(fmt)

    def render(self, name: str, **ctx: Any) -> str:
        template = self.env.get_template(name)
        ctx.setdefault("now", datetime.now(self.tz))
        return template.render(**ctx).strip()

    def render_deal(self, deal: Deal, link: str, template: str = "deal_post.j2") -> str:
        p = deal.product
        return self.render(
            template,
            product=p,
            verdict=deal.verdict,
            link=link,
            discount_rate=deal.verdict.discount_rate if deal.verdict.discount_rate is not None else p.effective_discount_rate(),
            avg_price=deal.verdict.avg_price,
            below_avg_pct=deal.verdict.below_avg_pct,
            detected_at=deal.detected_at,
        )
