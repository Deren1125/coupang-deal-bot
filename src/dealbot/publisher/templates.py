"""Jinja2 템플릿 렌더링 (텔레그램 HTML parse_mode 기준, 자동 이스케이프)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from dealbot.models import Deal
from dealbot.shops import Shop
from dealbot.utils.text import clean_name, format_won, unit_price


def _pct(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


class TemplateRenderer:
    def __init__(self, templates_dir: Path, tz: str = "Asia/Seoul", channels: dict[str, str] | None = None) -> None:
        self.templates_dir = Path(templates_dir)
        self.tz = ZoneInfo(tz)
        # 템플릿에서 channels.telegram_url 처럼 씀. 없는 키는 빈 문자열
        self.channels = {"telegram_url": "", "kakao_openchat_url": "", "threads_url": "", **(channels or {})}
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
        env.filters["clean"] = clean_name
        return env

    def _local(self, dt: datetime | str | None, fmt: str = "%m/%d %H:%M") -> str:
        if dt is None:
            return "-"
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(self.tz).strftime(fmt)

    # 강조 단계 기준: 평소 가격(30일 평균·쿠팡 시중가) 대비 이만큼 싸면 must(꼭 사야) / top(역대급)
    emphasis: tuple[float, float] = (50.0, 70.0)

    def deal_tier(self, deal: Deal) -> tuple[str, float]:
        """'normal' | 'must' | 'top' 과 그 근거 비율. 표시 할인율은 부풀려지기 쉬워 안 쓴다."""
        v = deal.verdict
        pct = max(v.below_avg_pct or 0.0, v.below_market_pct or 0.0)
        must, top = self.emphasis
        if top > 0 and pct >= top:
            return "top", pct
        if must > 0 and pct >= must:
            return "must", pct
        return "normal", pct

    def deal_facts(self, deal: Deal) -> dict[str, Any]:
        """글에 쓰는 사실들 (모두 데이터에서 계산한 것만). 텔레그램·블로그 내보내기가 같이 쓴다."""
        p, v = deal.product, deal.verdict
        days = int(min(v.history_days or 0, 30))
        labels: list[str] = []
        tier, _ = self.deal_tier(deal)
        if tier == "top":
            labels.append("역대급")
        elif tier == "must":
            labels.append("강력 추천")
        if v.low_price and p.price and days >= 3:
            if p.price < v.low_price:
                labels.append(f"{days}일 최저가 갱신")
            elif p.price == v.low_price:
                labels.append(f"{days}일 최저가")
        src = (p.source or "").lower()
        if "goldbox" in src:
            labels.append("골드박스 특가")  # 골드박스 목록 순서는 판매 순위가 아니라 순위는 쓰지 않음
        elif "category_best" in src or "best" in src:
            cat = (p.category or "카테고리").split(">")[-1].strip()
            labels.append(f"{cat} 베스트 {p.rank}위" if p.rank else f"{cat} 베스트")
        ship = []
        if p.is_rocket:
            ship.append("로켓배송")
        if p.is_free_shipping:
            ship.append("무료배송")
        if not ship and p.shipping:
            ship.append(str(p.shipping))
        ref_price = None
        if v.market_price and (v.below_market_pct or 0) >= 10:
            ref_price = v.market_price
        elif v.avg_price and (v.below_avg_pct or 0) >= 10:
            ref_price = int(v.avg_price)
        pct = round((1 - p.price / ref_price) * 100) if ref_price and p.price else None
        if pct is None and v.discount_rate:
            pct = round(v.discount_rate)
        return {
            "labels": labels,
            "ship": " · ".join(ship),
            "cat": (p.category or "").split(">")[-1].strip(),
            "unit": unit_price(clean_name(p.name), p.price),
            "low_price": v.low_price if days >= 3 else None,
            "avg_price": int(v.avg_price) if v.avg_price and days >= 3 else None,
            "history_days": days,
            "sale_pct": pct,
            "comment": (p.extra or {}).get("comment"),
            "rank": p.rank,
            "ref_price": ref_price,
        }

    def render(self, name: str, *, autoescape: bool = True, **ctx: Any) -> str:
        env = self.env if autoescape else self.env_plain
        template = env.get_template(name)
        ctx.setdefault("now", datetime.now(self.tz))
        ctx.setdefault("channels", self.channels)
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
        tier, tier_pct = self.deal_tier(deal)
        # 평소 가격 근거 (숫자로 확인된 것만): 쿠팡 시중가 대조가 있으면 그것, 없으면 최근 평균
        v = deal.verdict
        ref_price, ref_label = None, ""
        if v.market_price and (v.below_market_pct or 0) >= 10:  # 10% 미만 차이는 굳이 내세우지 않음
            ref_price, ref_label = v.market_price, "쿠팡 최저가"
        elif v.avg_price and (v.below_avg_pct or 0) >= 10:
            ref_price, ref_label = int(v.avg_price), "평소 가격"
        return self.render(
            template,
            autoescape=autoescape,
            product=p,
            shop=shop_ctx,
            verdict=deal.verdict,
            tier=tier,
            tier_pct=tier_pct,
            link=link,
            discount_rate=deal.verdict.discount_rate if deal.verdict.discount_rate is not None else p.effective_discount_rate(),
            avg_price=deal.verdict.avg_price,
            below_avg_pct=deal.verdict.below_avg_pct,
            market_price=deal.verdict.market_price,
            market_source=deal.verdict.market_source,
            below_market_pct=deal.verdict.below_market_pct,
            detected_at=deal.detected_at,
            pname=clean_name(p.name),
            ref_price=ref_price,
            ref_label=ref_label,
            ref_pct=round((1 - p.price / ref_price) * 100) if ref_price and p.price else None,
            facts=self.deal_facts(deal),
        )
