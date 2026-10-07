"""Jinja2 템플릿 렌더링 (텔레그램 HTML parse_mode 기준, 자동 이스케이프)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from dealbot.models import Deal, Product
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
            **self.evidence(deal, days=days, ref_price=ref_price, pct=pct, labels=labels, tier=tier),
        }

    # 스레드 첫 줄 (AI 가 못 썼을 때): 분류별 생활 속 순간 → 없으면 일반. 상품마다 정해진 하나라 같은 문장이 연달아 안 나옴
    THREAD_HOOKS_BY_CAT = (
        (("식품", "과자", "음료", "생수", "라면", "커피", "간편", "냉동", "축산", "수산", "과일", "채소"),
         ("야식 생각날 때 꼭 냉장고가 비어 있음", "장보러 가기 귀찮은 날 이거면 됨", "배달비 아까워서 쟁여두는 거 있음?")),
        (("생활", "세제", "화장지", "휴지", "물티슈", "청소", "욕실", "세탁"),
         ("휴지 떨어진 거 꼭 샤워 끝나고 알게 됨", "생필품은 쌀 때 사는 게 이기는 거임", "어차피 쓰는 거면 쌀 때 사두는 게 답임")),
        (("주방", "식기", "조리", "냄비", "프라이팬", "보관"),
         ("설거지 거리 하나 줄이면 그게 행복임", "주방템은 한 번 사면 몇 년 감")),
        (("뷰티", "화장품", "스킨", "헤어", "바디", "향수", "클렌징"),
         ("다 쓴 공병 쌓여 있는 사람 손", "화장대 정리하다 보면 꼭 이게 없음")),
        (("가전", "디지털", "컴퓨터", "휴대폰", "충전", "이어폰", "모니터", "노트북"),
         ("충전기는 왜 항상 하나 모자람", "전자기기는 할인할 때 사는 거임")),
        (("패션", "의류", "신발", "가방", "양말", "속옷"),
         ("양말은 왜 항상 한 짝씩 사라짐", "기본템은 매년 다시 사게 됨")),
    )
    THREAD_HOOKS = (
        "오늘 핫딜 중에 이거 하나만 건지면 됨", "쟁여둘 사람은 지금이 타이밍임", "이거 집에 하나씩 있는 거 맞지?",
        "이 가격 다시 보기 쉽지 않을 듯", "장바구니에 넣어둔 사람 지금 보셈", "필요했던 사람만 보면 됨",
    )
    THREAD_CLOSES = ("다들 이런 거 어디서 사?", "더 싼 데 알면 알려줘", "품절되면 댓글에 표시해둘게", "필요한 사람만. 안 쓸 거면 싸도 손해임")

    def _thread_hook(self, p: Product, h: int) -> str:
        if (p.extra or {}).get("thread_hook"):
            return p.extra["thread_hook"]
        text = f"{p.category or ''} {p.name}"
        for words, hooks in self.THREAD_HOOKS_BY_CAT:
            if any(w in text for w in words):
                return hooks[h % len(hooks)]
        return self.THREAD_HOOKS[h % len(self.THREAD_HOOKS)]

    def evidence(self, deal: Deal, *, days: int, ref_price: int | None, pct: int | None, labels: list[str], tier: str) -> dict[str, Any]:
        """가격 근거를 사람 말로: 첫 줄용 짧은 근거, 문장형 근거, 스레드용 반말 근거. 숫자는 데이터에서만."""
        p, v = deal.product, deal.verdict
        unit = unit_price(clean_name(p.name), p.price)
        each = f"{unit.split(' (')[0]} 꼴" if unit else None
        low = next((x for x in labels if "최저가" in x), None)
        is_market = bool(v.market_price and ref_price == v.market_price)
        if low:
            short, casual = f"{days}일 중 제일 쌈", f"최근 {days}일 중 제일 쌈"
            sentence = f"최근 {days}일 중 제일 싸요."
        elif ref_price and pct:
            where = "쿠팡" if is_market else "평소"
            short, casual = f"{where}보다 {pct}%↓", f"{where}보다 {pct}% 쌈"
            sentence = (f"쿠팡 최저가 {ref_price:,}원보다 {pct}% 싸요." if is_market
                        else f"평소 {ref_price:,}원대인데 오늘 {p.price:,}원이에요.")
        elif each:
            short, casual, sentence = each, each, f"{each}이에요."
        elif pct:
            short, casual, sentence = f"정가 대비 {pct}%↓", f"정가 대비 {pct}% 빠짐", f"정가 대비 {pct}% 내려왔어요."
        else:
            short = casual = sentence = None
        h = sum(map(ord, p.product_id))
        return {
            "badge": {"top": "🔥 초특가", "must": "👍 강추"}.get(tier, "☑️"),
            "evidence_short": short,
            "evidence": sentence,
            "evidence_casual": casual,
            "unit_each": each if each and each != short else None,
            "thread_hook": self._thread_hook(p, h),
            "thread_take": (p.extra or {}).get("thread_take"),
            # 마무리 질문은 두 번에 한 번꼴 (매번 질문으로 끝나면 그것도 광고 문법)
            "thread_close": self.THREAD_CLOSES[(h // 7) % len(self.THREAD_CLOSES)] if h % 2 else None,
            "short_name": (p.extra or {}).get("short_name") or clean_name(p.name),
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
