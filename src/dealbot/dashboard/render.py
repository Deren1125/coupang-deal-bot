"""대시보드 HTML (참고 화면을 닮게): 밝은 회색 바탕, 흰 둥근 카드 3열, 상태 칩, 숫자 칸 2개, 보라색 계단 그래프, 목표 막대.

스크립트는 없다. 화면에 들어가는 값은 모두 html.escape 를 거친다 (아이콘 SVG 만 코드에 박아 둔 것 그대로).
토큰·비밀값·환경변수 값은 여기로 오지 않는다 (CardView 에 없음).
"""

from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    CHIP_LABELS,
    CardView,
    GoalRow,
    Stat,
    fmt_num,
    fmt_signed,
    fmt_time,
)

PURPLE = "#5b4fd6"

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--line:#e6e8eb;--text:#191f28;--sub:#6b7684;--muted:#8b95a1;--purple:#5b4fd6;
--green:#1a8a4a;--track:#eceef1}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;
word-break:keep-all;overflow-wrap:anywhere;
font-family:Pretendard,"Pretendard Variable",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:20px 16px 40px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px 16px;flex-wrap:wrap;margin:0 0 14px}
.top h1{font-size:20px;line-height:1.3;margin:0;font-weight:700;letter-spacing:-.01em}
.top .updated{color:var(--sub);font-size:13px}
.notice{border-radius:10px;padding:10px 14px;margin:0 0 14px;font-size:13px;background:#eef0ff;color:#3f35a8;border:1px solid #dcdffc}
.notice.bad{background:#fff4e5;color:#b45309;border-color:#fde7c7}
.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 16px}
.sum{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.sum .label{color:var(--sub);font-size:12px;margin:0 0 2px}
.sum .value{font-size:19px;font-weight:700;margin:0;overflow-wrap:anywhere}
.sum .sub{color:var(--muted);font-size:12px;margin:2px 0 0}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
@media (max-width:1099px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:699px){.grid{grid-template-columns:minmax(0,1fr)}.wrap{padding:16px 16px 32px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 16px 18px;min-width:0}
.head{display:flex;align-items:center;justify-content:space-between;gap:8px}
.name{display:flex;align-items:center;gap:8px;min-width:0}
.name h2{font-size:15px;margin:0;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.icon{display:inline-flex;flex:none;width:20px;height:20px}
.actions{display:flex;align-items:center;gap:6px;flex:none}
.chip{font-size:12px;padding:3px 8px;border-radius:6px;font-weight:600;white-space:nowrap}
.chip.unmet{background:#fff4e5;color:#b45309}
.chip.unknown{background:#f1f2f4;color:#4e5968}
.chip.met{background:#e8f5ee;color:var(--green)}
.btn{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text);border:1px solid #d1d6db;border-radius:6px;
padding:4px 9px;text-decoration:none;background:#fff;white-space:nowrap}
.btn:hover{background:#f9fafb}
.read{color:var(--muted);font-size:12px;margin:8px 0 12px}
.stats{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.stat{border:1px solid var(--line);border-radius:8px;padding:10px 12px;min-width:0}
.stat .label{color:var(--sub);font-size:12px;margin:0 0 4px;overflow-wrap:anywhere}
.stat .value{font-size:20px;font-weight:700;margin:0;line-height:1.3;overflow-wrap:anywhere}
.stat .value.missing{font-size:15px;color:var(--muted);font-weight:600}
.stat .unit{font-size:12px;font-weight:600;color:var(--sub);margin-left:2px}
.stat .sub{font-size:12px;color:var(--muted);margin:2px 0 0}
.delta{font-size:12px;font-weight:700;margin-left:6px;white-space:nowrap}
.delta small{font-weight:500;color:var(--muted);margin-left:2px;font-size:11px}
.delta.up{color:var(--green)}.delta.down{color:#d93025}.delta.flat{color:var(--muted)}
.spark{display:block;width:100%;max-width:240px;height:46px;margin:14px auto 4px}
h3{font-size:13px;color:#4e5968;margin:14px 0 8px;font-weight:600}
.row{margin:0 0 10px}
.row-top{display:flex;justify-content:space-between;gap:8px;font-size:13px}
.row-top .rl{display:inline-flex;align-items:center;gap:4px;min-width:0}
.row-top .rv{color:#4e5968;white-space:nowrap;font-variant-numeric:tabular-nums}
.bar{height:6px;background:var(--track);border-radius:3px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--purple);border-radius:3px}
.bar.met i{background:var(--green)}
.activity{font-size:12px;color:#4e5968;background:#f9fafb;border-radius:8px;padding:8px 10px;margin:10px 0 0}
.note{color:var(--sub);font-size:12.5px;margin:10px 0 0;line-height:1.6}
.hint{color:#b45309;font-size:12px;margin:8px 0 0;overflow-wrap:anywhere}
form.manual{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-end;margin:12px 0 0;border-top:1px dashed var(--line);padding-top:12px}
form.manual label{display:flex;flex-direction:column;font-size:11px;color:var(--sub);gap:2px;flex:1 1 84px;min-width:0}
form.manual label.date{flex:1 1 118px}
form.manual input{font:inherit;font-size:13px;padding:6px 8px;border:1px solid #d1d6db;border-radius:6px;width:100%;min-width:0;
background:#fff;color:var(--text)}
form.manual button{font:inherit;font-size:13px;font-weight:600;padding:6px 14px;border:0;border-radius:6px;background:var(--purple);
color:#fff;cursor:pointer}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:24px;color:var(--sub)}
.foot{color:var(--muted);font-size:12px;margin:18px 0 0}
"""


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


# ---------------------------------------------------------------- 계단 그래프
def step_points(series: list[tuple[str, float]], *, days: int, today: date) -> list[tuple[int, float]]:
    """(날짜, 값) → (기간 안 칸 번호, 값). 기간 밖 값은 뺀다."""
    start = today - timedelta(days=days - 1)
    out = []
    for d, v in series:
        i = (date.fromisoformat(d) - start).days
        if 0 <= i < days:
            out.append((i, float(v)))
    return sorted(out)


def step_path(points: list[tuple[int, float]], *, days: int, width: float = 240, height: float = 46, pad: float = 3) -> str:
    """값이 바뀌는 날까지 수평 → 수직으로 꺾는 계단 선 (SVG path d). 점이 하나면 오른쪽 끝까지 평평하게."""
    if not points:
        return ""
    vals = [v for _, v in points]
    lo, hi = min(vals), max(vals)

    def x(i: int) -> float:
        return pad + i * (width - 2 * pad) / max(1, days - 1)

    def y(v: float) -> float:
        if hi == lo:
            return height / 2
        return pad + (hi - v) / (hi - lo) * (height - 2 * pad)

    i0, v0 = points[0]
    if len(points) == 1:  # 값이 하나뿐이면 그 값에서 오른쪽 끝까지 (오늘 값이면 짧은 선)
        start = min(x(i0), x(days - 1) - 24)
        return f"M{start:.1f} {y(v0):.1f} H{x(days - 1):.1f}"
    d = f"M{x(i0):.1f} {y(v0):.1f}"
    for i, v in points[1:]:
        d += f" H{x(i):.1f} V{y(v):.1f}"
    return d


def sparkline(series: list[tuple[str, float]], *, days: int, today: date, label: str = "") -> str:
    pts = step_points(series, days=days, today=today)
    name = e(f"최근 {days}일 {label} 추이".replace("  ", " "))
    if not pts:
        return (f'<svg class="spark" viewBox="0 0 240 46" role="img" aria-label="{name} (기록 없음)">'
                '<path d="M3 23 H237" stroke="#e6e8eb" stroke-width="2" stroke-dasharray="3 4" fill="none"/></svg>')
    return (f'<svg class="spark" viewBox="0 0 240 46" role="img" aria-label="{name}">'
            f'<path d="{step_path(pts, days=days)}" stroke="{PURPLE}" stroke-width="2" fill="none" '
            'stroke-linejoin="round" stroke-linecap="round"/></svg>')


# ---------------------------------------------------------------- 조각
def _stat(s: Stat) -> str:
    if s.value is None:
        cls = " missing" if s.missing != "-" else ""
        value = f'<p class="value{cls}">{e(s.missing)}</p>'
    else:
        unit = f'<span class="unit">{e(s.unit)}</span>' if s.unit else ""
        delta = ""
        if s.delta is not None:
            kind = "up" if s.delta > 0 else "down" if s.delta < 0 else "flat"
            small = f"<small>{e(s.delta_label)}</small>" if s.delta_label else ""
            delta = f'<span class="delta {kind}">{e(fmt_signed(s.delta))}{small}</span>'
        value = f'<p class="value">{e(fmt_num(s.value))}{unit}{delta}</p>'
    sub = f'<p class="sub">{e(s.sub)}</p>' if s.sub else ""
    return f'<div class="stat"><p class="label">{e(s.label)}</p>{value}{sub}</div>'


def _row(r: GoalRow) -> str:
    met = r.met
    check = icons.CHECK if met else ""
    goal = f"최대 {fmt_num(r.goal)}" if r.lower_is_better else fmt_num(r.goal)
    unit = f" {r.unit}" if r.unit else ""
    pct = 0.0 if r.value is None else max(r.ratio * 100, 1.5 if r.value else 0.0)
    return (
        f'<div class="row"><div class="row-top"><span class="rl">{check}{e(r.label)}</span>'
        f'<span class="rv">{e(fmt_num(r.value))} / {e(goal)}{e(unit)}</span></div>'
        f'<div class="bar{" met" if met else ""}"><i style="width:{pct:.1f}%"></i></div></div>'
    )


def _form(v: CardView, token: str, today: date, min_day: date | None = None) -> str:
    if not v.manual:
        return ""
    fields = []
    for f in v.manual:
        cur = v.manual_values.get(f.metric)
        ph = fmt_num(cur) if cur is not None else "숫자"
        fields.append(
            f'<label>{e(f.label)}<input name="{e(f.metric)}" inputmode="numeric" pattern="[0-9,]*" maxlength="13" '
            f'autocomplete="off" placeholder="{e(ph)}"></label>'
        )
    return (
        '<form class="manual" method="get" action="/dashboard/input">'
        f'<input type="hidden" name="src" value="{e(v.key)}"><input type="hidden" name="t" value="{e(token)}">'
        + "".join(fields)
        + f'<label class="date">날짜<input type="date" name="date" value="{e(today.isoformat())}" max="{e(today.isoformat())}"'
        + (f' min="{e(min_day.isoformat())}"' if min_day else "") + "></label>"
        '<button type="submit">저장</button></form>'
    )


def card_html(v: CardView, *, token: str, today: date, tz: str, spark_days: int, min_day: date | None = None) -> str:
    status = v.status
    chip = f'<span class="chip {e(status)}">{e(CHIP_LABELS[status])}</span>' if status else ""
    btn = ""
    if v.refreshable:
        href = "/dashboard/refresh?" + urlencode({"src": v.key, "t": token})
        btn = f'<a class="btn" href="{e(href)}">{icons.REFRESH}다시 읽기</a>'
    read = (f"{v.read_label} {fmt_time(v.read_at.astimezone(ZoneInfo(tz)))}" if v.read_at else "아직 읽은 값이 없어요")
    parts = [
        f'<article class="card" id="card-{e(v.key)}">',
        f'<div class="head"><div class="name"><span class="icon">{v.icon}</span><h2>{e(v.title)}</h2></div>'
        f'<div class="actions">{chip}{btn}</div></div>',
        f'<p class="read">{e(read)}</p>',
        '<div class="stats">' + "".join(_stat(s) for s in v.stats[:2]) + "</div>",
        sparkline(v.spark, days=spark_days, today=today, label=v.spark_label),
    ]
    if v.rows:
        parts.append(f"<h3>{e(v.program or '목표')}</h3>")
        parts.extend(_row(r) for r in v.rows)
    parts.extend(f'<p class="activity">{e(line)}</p>' for line in v.activity)
    if v.note:
        parts.append(f'<p class="note">{e(v.note)}</p>')
    parts.extend(f'<p class="hint">{e(h)}</p>' for h in v.hints)
    parts.append(_form(v, token, today, min_day))
    parts.append("</article>")
    return "".join(parts)


def render_page(
    *,
    title: str,
    views: list[CardView],
    summary: list[tuple[str, str, str]],
    updated_at: datetime | None,
    token: str,
    today: date,
    tz: str,
    spark_days: int,
    notice: tuple[str, str] | None = None,
    footer: str = "",
    min_day: date | None = None,
) -> str:
    updated = f"마지막 업데이트 {fmt_time(updated_at.astimezone(ZoneInfo(tz)))}" if updated_at else "아직 읽은 값이 없어요"
    head = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
    )
    body = [f'<header class="top"><h1>{e(title)}</h1><span class="updated">{e(updated)}</span></header>']
    if notice:
        text, kind = notice
        body.append(f'<div class="notice{" bad" if kind == "bad" else ""}" role="status">{e(text)}</div>')
    if summary:
        body.append('<section class="summary">' + "".join(
            f'<div class="sum"><p class="label">{e(label)}</p><p class="value">{e(value)}</p>'
            + (f'<p class="sub">{e(sub)}</p>' if sub else "") + "</div>"
            for label, value, sub in summary
        ) + "</section>")
    if views:
        body.append('<main class="grid">' + "".join(
            card_html(v, token=token, today=today, tz=tz, spark_days=spark_days, min_day=min_day) for v in views) + "</main>")
    else:
        body.append('<p class="empty">보여 줄 카드가 없어요 (config.yaml 의 dashboard.cards).</p>')
    if footer:
        body.append(f'<p class="foot">{e(footer)}</p>')
    return head + "".join(body) + "</div></body></html>"


# ---------------------------------------------------------------- JSON
def view_dict(v: CardView, tz: str) -> dict[str, Any]:
    return {
        "key": v.key,
        "title": v.title,
        "type": v.type,
        "kind": v.kind,
        "status": v.status or None,
        "status_label": CHIP_LABELS.get(v.status) if v.status else None,
        "read_at": v.read_at.astimezone(ZoneInfo(tz)).isoformat() if v.read_at else None,
        "stats": [
            {"label": s.label, "value": s.value, "unit": s.unit, "delta": s.delta, "delta_label": s.delta_label or None,
             "missing": s.missing if s.value is None else None, "sub": s.sub or None}
            for s in v.stats
        ],
        "spark": {"label": v.spark_label, "points": [{"day": d, "value": val} for d, val in v.spark]},
        "program": {
            "title": v.program,
            "rows": [{"label": r.label, "value": r.value, "goal": r.goal, "unit": r.unit, "met": r.met,
                      "lower_is_better": r.lower_is_better} for r in v.rows],
        },
        "activity": v.activity,
        "note": v.note or None,
        "hints": v.hints,
        "manual": [{"metric": f.metric, "label": f.label, "unit": f.unit, "day_offset": f.day_offset,
                    "latest": v.manual_values.get(f.metric)} for f in v.manual],
    }


def render_json(*, title: str, views: list[CardView], generated_at: datetime, tz: str,
                summary: list[tuple[str, str, str]]) -> str:
    return json.dumps(
        {
            "title": title,
            "generated_at": generated_at.astimezone(ZoneInfo(tz)).isoformat(),
            "summary": [{"label": a, "value": b, "sub": c or None} for a, b, c in summary],
            "cards": [view_dict(v, tz) for v in views],
        },
        ensure_ascii=False,
    )
