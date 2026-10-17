"""대시보드 HTML (참고 화면을 닮게): 밝은 회색 바탕, 흰 둥근 카드 3열, 상태 칩, 숫자 칸 2개, 보라색 계단 그래프, 목표 막대.
맨 위(요약 줄 위)에는 '지금 할 일' 카드 (todo.py — 주인 요청 10/15).

스크립트는 없다. 화면에 들어가는 값은 모두 html.escape 를 거친다 (아이콘 SVG 만 코드에 박아 둔 것 그대로).
토큰·비밀값·환경변수 값은 여기로 오지 않는다 (CardView 에 없음).
카드 하나를 그리다 터져도 그 카드만 '그리지 못함' 으로 바뀌고 페이지는 뜬다.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dealbot.dashboard import icons, webapp
from dealbot.dashboard import todo as todo_mod
from dealbot.dashboard.model import (
    CHIP_LABELS,
    MET,
    UNKNOWN,
    UNMET,
    CardLink,
    CardView,
    GoalRow,
    ListEntry,
    ListGroup,
    Stat,
    explain,
    fmt_num,
    fmt_short_day,
    fmt_signed,
    fmt_time,
    https_url,
    josa,
    safe_error,
)

log = logging.getLogger(__name__)

PURPLE = "#5b4fd6"
MIN_SPAN_DAYS = 7  # 기록이 짧아도 그래프 가로는 최소 7일 폭으로 (첫 값은 왼쪽 끝부터)
NBSP = "\u00a0"

CSS = """
:root{--bg:#f4f5f7;--card:#fff;--line:#e6e8eb;--text:#191f28;--sub:#6b7684;--muted:#8b95a1;--purple:#5b4fd6;
--green:#1a8a4a;--track:#eceef1;--orange:#b45309;--tile:#f8f9fb}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;
word-break:keep-all;overflow-wrap:anywhere;
font-family:Pretendard,"Pretendard Variable",-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif}
.wrap{max-width:1320px;margin:0 auto;padding:20px 16px 40px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px 16px;flex-wrap:wrap;margin:0 0 14px}
.top h1{font-size:20px;line-height:1.3;margin:0;font-weight:700;letter-spacing:-.01em}
.top .meta{display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px;color:var(--sub);font-size:13px}
.top .counts{display:inline-flex;flex-wrap:wrap;gap:4px}
.top .logout{display:inline;margin:0}
.top .logout button{font:inherit;font-size:13px;color:var(--sub);background:none;border:0;padding:6px 2px;min-height:32px;cursor:pointer;
text-decoration:underline;text-underline-offset:2px}
.notice{border-radius:10px;padding:10px 14px;margin:0 0 14px;font-size:13px;background:#eef0ff;color:#3f35a8;border:1px solid #dcdffc}
.notice.bad{background:#fff4e5;color:var(--orange);border-color:#fde7c7}
.card .notice{margin:10px 0 0;padding:8px 12px;border-radius:8px}
.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 16px}
.sum{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0;
box-shadow:0 1px 3px rgba(17,24,39,.05)}
.sum .label{color:var(--sub);font-size:12px;margin:0 0 2px}
.sum .value{font-size:19px;font-weight:700;margin:0;white-space:nowrap}
.sum .sub{color:var(--muted);font-size:12px;margin:2px 0 0}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
@media (max-width:1099px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:699px){.grid{grid-template-columns:minmax(0,1fr)}.wrap{padding:16px 16px 32px}}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 16px 18px;min-width:0;
box-shadow:0 1px 3px rgba(17,24,39,.05)}
.head{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px 8px}
.name{display:flex;align-items:center;gap:8px;min-width:0}
.name h2{font-size:15px;line-height:1.35;margin:0;font-weight:700}
.icon{display:inline-flex;flex:none;width:20px;height:20px}
.actions{display:flex;align-items:center;gap:6px;flex:none;margin-left:auto}
.chip{font-size:12px;padding:3px 8px;border-radius:6px;font-weight:600;white-space:nowrap}
.chip.unmet{background:#fff4e5;color:var(--orange)}
.chip.unknown{background:#f1f2f4;color:#4e5968}
.chip.met{background:#e8f5ee;color:var(--green)}
.btn{display:inline-flex;align-items:center;gap:4px;font-size:13px;color:var(--text);border:1px solid #d1d6db;border-radius:6px;
padding:4px 10px;min-height:28px;text-decoration:none;background:#fff;white-space:nowrap}
.btn:hover{background:#f9fafb}
.read{color:var(--muted);font-size:12px;margin:8px 0 12px}
.stats{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.stat{background:var(--tile);border:1px solid var(--line);border-radius:10px;padding:10px 12px;min-width:0}
.stat .label{color:var(--sub);font-size:12px;margin:0 0 4px}
.stat .value{font-size:20px;font-weight:700;margin:0;line-height:1.3}
.stat .value.missing{font-size:15px;color:var(--muted);font-weight:600}
.num{white-space:nowrap}
.stat .unit{font-size:12px;font-weight:600;color:var(--sub);margin-left:2px}
.stat .sub{font-size:12px;color:var(--muted);margin:2px 0 0}
.stat.stale{background:#fbfbfc}
.stat.stale .value,.stat.stale .unit,.stat.stale .delta,.stat.stale .delta small{color:#adb5bd}
.delta{font-size:12px;font-weight:700;margin-left:6px;white-space:nowrap}
.delta small{font-weight:500;color:var(--muted);margin-left:2px;font-size:11px}
.delta.up{color:var(--green)}.delta.down{color:#d93025}.delta.flat{color:var(--muted)}
.spark{display:block;width:100%;max-width:240px;height:46px;margin:14px auto 4px}
.links{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;margin:10px 0 0}
.ext{color:var(--sub);font-size:12px;text-decoration:none;white-space:nowrap;padding:10px 4px}
.ext:hover{text-decoration:underline}
.ext.primary{flex:1 1 auto;display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:38px;padding:7px 14px;
border-radius:8px;background:#eef0ff;border:1px solid #dcdffc;color:#3f35a8;font-size:13px;font-weight:600}
.ext.primary:hover{background:#e4e7ff;text-decoration:none}
.stats-note{color:var(--muted);font-size:12px;line-height:1.5;margin:8px 0 0}
.stats-note .warn{color:var(--orange);font-weight:600;white-space:nowrap}
h3{font-size:13px;color:#4e5968;margin:14px 0 8px;font-weight:600}
.row{margin:0 0 10px}
.row-top{display:flex;justify-content:space-between;gap:8px;font-size:13px}
.row-top .rl{display:inline-flex;align-items:center;gap:4px;min-width:0}
.row-top .rv{color:#4e5968;white-space:nowrap;font-variant-numeric:tabular-nums}
.bar{height:6px;background:var(--track);border-radius:3px;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--purple);border-radius:3px}
.bar.met i{background:var(--green)}
.bar.over i{background:var(--orange)}
.activity{font-size:12px;color:#4e5968;background:var(--tile);border-radius:8px;padding:8px 10px;margin:10px 0 0}
.activity .it{display:inline-block;max-width:100%}
.stat .sub .it{display:inline-block;max-width:100%}
.note{color:var(--sub);font-size:12.5px;margin:10px 0 0;line-height:1.6}
.hint{color:var(--orange);font-size:12px;margin:8px 0 0;overflow-wrap:anywhere}
details.manual-box{margin:12px 0 0;border-top:1px dashed var(--line);padding-top:10px}
details.manual-box summary{display:flex;align-items:center;flex-wrap:wrap;gap:2px 6px;cursor:pointer;list-style:none;
font-size:13px;font-weight:600;color:var(--purple);min-height:28px}
details.manual-box summary::-webkit-details-marker{display:none}
details.manual-box summary::before{content:"";flex:none;width:6px;height:6px;margin:0 3px 2px 1px;border:solid currentColor;
border-width:0 1.5px 1.5px 0;transform:rotate(-45deg)}
details.manual-box[open] summary::before{transform:rotate(45deg);margin-bottom:4px}
details.manual-box summary .what{font-size:12px;font-weight:400;color:var(--muted)}
form.manual{display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;margin:8px 0 0}
form.manual label{display:flex;flex-direction:column;font-size:11px;color:var(--sub);gap:2px;flex:1 1 84px;min-width:0}
form.manual label.date{flex:1 1 118px}
form.manual input{font:inherit;font-size:13px;padding:6px 8px;border:1px solid #d1d6db;border-radius:6px;width:100%;min-width:0;
background:#fff;color:var(--text)}
form.manual input::placeholder{color:#b0b8c1}
form.manual .last{font-size:11px;line-height:1.35;color:var(--muted)}
form.manual button{font:inherit;font-size:13px;font-weight:600;padding:6px 14px;border:0;border-radius:6px;background:var(--purple);
color:#fff;cursor:pointer;align-self:flex-end;margin-bottom:17px}
form.manual .form-note{flex:1 1 100%;margin:0;font-size:11px;color:var(--muted)}
.lists{margin:6px 0 0}
.lgroup{border-top:1px solid var(--line);padding:10px 0 4px;min-width:0}
.lgroup:first-child{border-top:0;padding-top:2px}
.lg-head{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:2px 8px}
.lg-head h3{margin:0;font-size:13.5px;color:var(--text);font-weight:700;min-width:0}
.lg-sum{font-size:12.5px;color:#4e5968;white-space:nowrap;font-variant-numeric:tabular-nums}
.lg-sum.unknown{font-size:12px;font-weight:600;padding:1px 8px;border-radius:6px;background:#f1f2f4}
.litems{list-style:none;margin:4px 0 0;padding:0}
.lit{padding:7px 0;min-width:0}
.lit+.lit{border-top:1px dashed var(--line)}
.lit-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.lit-title{font-size:13px;line-height:1.45;min-width:0;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
overflow:hidden}
.pill{flex:none;font-size:11.5px;font-weight:700;line-height:1.6;padding:0 8px;border-radius:999px;background:#f1f2f4;color:#4e5968;
white-space:nowrap;font-variant-numeric:tabular-nums}
.pill.hot{background:#eef0ff;color:#3f35a8}
.lit-sub{font-size:12px;color:var(--muted);margin:2px 0 0}
.lit-act{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:8px 0 0;min-width:0}
.lit-act .ext.primary{flex:0 0 auto;min-height:40px}
.lempty{color:var(--sub);font-size:13px;background:var(--tile);border-radius:8px;padding:10px 12px;margin:10px 0 0}
form.linkform{flex:1 1 220px;display:flex;gap:6px;min-width:0;margin:0}
form.linkform input{flex:1 1 auto;width:100%;min-width:0;font:inherit;font-size:16px;min-height:40px;padding:6px 10px;
border:1px solid #d1d6db;border-radius:8px;background:#fff;color:var(--text)}
form.linkform input::placeholder{color:#b0b8c1}
form.linkform button{flex:none;font:inherit;font-size:14px;font-weight:600;min-height:40px;padding:0 14px;border:0;border-radius:8px;
background:var(--purple);color:#fff;cursor:pointer;white-space:nowrap}
.todo{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px 12px;margin:0 0 16px;min-width:0;
box-shadow:0 1px 3px rgba(17,24,39,.05)}
.todo h2{display:flex;align-items:center;gap:8px;font-size:16px;line-height:1.35;margin:0;font-weight:700}
.todo h2 .todo-n{font-size:13px;font-weight:700;line-height:1.6;padding:0 9px;border-radius:999px;background:var(--purple);color:#fff;
font-variant-numeric:tabular-nums}
.todo.done h2{color:var(--green)}
.todo-list{list-style:none;margin:6px 0 0;padding:0;display:grid;grid-template-columns:minmax(0,1fr);column-gap:28px}
.td{padding:10px 0;border-top:1px dashed var(--line);min-width:0}
.todo-list>.td:first-child{border-top:0}
.td-top{display:flex;align-items:flex-start;gap:4px 8px;min-width:0}
.mk{flex:none;font-size:11.5px;font-weight:700;line-height:1.6;padding:0 7px;border-radius:6px;white-space:nowrap;margin-top:1px}
.mk.m-stop{background:#fdecec;color:#c62828}.mk.m-hot{background:#fff1e6;color:#c2410c}.mk.m-warn{background:#fff4e5;color:var(--orange)}
.mk.m-todo{background:#eef0ff;color:#3f35a8}.mk.m-info{background:#f1f2f4;color:#4e5968}
.mk.m-urgent{background:#c62828;color:#fff}
.td-title{font-size:14px;font-weight:700;line-height:1.45;min-width:0;overflow-wrap:anywhere}
.td-how{font-size:13px;color:#4e5968;line-height:1.5;margin:3px 0 0;overflow-wrap:anywhere}
.td-foot{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px 10px;margin:4px 0 0;min-width:0}
.td-meta{font-size:12px;color:var(--muted);min-width:0}
.td-meta .left{color:var(--orange);font-weight:700;white-space:nowrap}
.td-act{flex:none;display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:36px;padding:6px 14px;margin-left:auto;
border-radius:8px;background:#eef0ff;border:1px solid #dcdffc;color:#3f35a8;font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.td-act:hover{background:#e4e7ff}
details.todo-more{border-top:1px dashed var(--line);margin:0}
details.todo-more summary{display:flex;align-items:center;gap:6px;cursor:pointer;list-style:none;min-height:40px;font-size:13px;
font-weight:600;color:var(--purple)}
details.todo-more summary::-webkit-details-marker{display:none}
details.todo-more summary::before{content:"";flex:none;width:6px;height:6px;margin:0 3px 2px 1px;border:solid currentColor;
border-width:0 1.5px 1.5px 0;transform:rotate(45deg)}
details.todo-more[open] summary::before{transform:rotate(-135deg);margin-bottom:-2px}
details.todo-more .todo-list{margin:0}
.todo-hint{color:var(--muted);font-size:12px;margin:8px 0 0}
.card,.todo{scroll-margin-top:12px}
@media (min-width:1100px){.todo-list{grid-template-columns:repeat(2,minmax(0,1fr))}.todo-list>.td:nth-child(2){border-top:0}}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:24px;color:var(--sub)}
.foot{color:var(--muted);font-size:12px;margin:18px 0 0}
.card{container-type:inline-size}
@container (max-width:344px){.btn .btn-text{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
clip-path:inset(50%);white-space:nowrap}.btn{padding:4px 8px}}
@media (max-width:420px){.btn .btn-text{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
clip-path:inset(50%);white-space:nowrap}.btn{padding:4px 8px;min-height:36px;min-width:36px;justify-content:center}}
@media (max-width:399px){.stat .value{font-size:18px}.sum .value{font-size:17px}}
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
    """참고 화면 같은 계단 선 (SVG path d): 값이 그대로인 동안은 수평, 바뀐 날은 전날→그날을 짧게 비스듬히 잇는다.
    첫 값은 왼쪽 끝부터, 마지막 값은 오른쪽 끝(오늘)까지 그대로 이어 그린다 — 값이 하나면 가운데 높이의 평평한 선."""
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

    pen = (round(pad, 1), round(y(points[0][1]), 1))
    d = f"M{pen[0]:.1f} {pen[1]:.1f}"

    def line_to(px: float, py: float) -> None:
        nonlocal d, pen
        pt = (round(px, 1), round(py, 1))
        if pt != pen:
            d += f" L{pt[0]:.1f} {pt[1]:.1f}"
            pen = pt

    prev = points[0][1]
    for i, v in points[1:]:
        if v != prev:
            line_to(x(i - 1), y(prev))  # 전날까지는 앞 값
            line_to(x(i), y(v))  # 그날 새 값
            prev = v
    line_to(x(days - 1), y(prev))
    return d


def spark_window(points: list[tuple[int, float]], *, days: int) -> tuple[list[tuple[int, float]], int]:
    """가로 폭을 기록 길이에 맞춘다: 첫 기록부터 오늘까지(최소 MIN_SPAN_DAYS 일, 최대 days 일).
    배포하고 며칠 안 된 그래프가 오른쪽 끝에 몰려 점·막대처럼 보이지 않게 → (다시 센 칸 번호, 칸 수)."""
    if not points:
        return [], days
    span = min(days, max(MIN_SPAN_DAYS, days - points[0][0]))
    shift = days - span
    return [(i - shift, v) for i, v in points if i >= shift], span


def sparkline(series: list[tuple[str, float]], *, days: int, today: date, label: str = "") -> str:
    pts, span = spark_window(step_points(series, days=days, today=today), days=days)
    name = e(f"최근 {days}일 {label} 추이".replace("  ", " "))
    if not pts:
        return (f'<svg class="spark" viewBox="0 0 240 46" role="img" aria-label="{name} (기록 없음)">'
                '<path d="M3 23 H237" stroke="#e6e8eb" stroke-width="2" stroke-dasharray="3 4" fill="none"/></svg>')
    return (f'<svg class="spark" viewBox="0 0 240 46" role="img" aria-label="{name}">'
            f'<path d="{step_path(pts, days=span)}" stroke="{PURPLE}" stroke-width="2" fill="none" '
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
        # 숫자 가운데서는 줄이 바뀌지 않게 (단위·증감만 다음 줄로 갈 수 있음)
        value = f'<p class="value"><span class="num">{e(fmt_num(s.value))}</span>{unit}{delta}</p>'
    # 작은 줄도 '공감 3 · 댓글 1 · 이웃 +1' 처럼 항목이 여럿이면 항목마다 한 덩어리 ('이웃 / +1' 로 갈라지지 않게)
    sub = f'<p class="sub">{_chunks(s.sub) if " · " in s.sub else e(s.sub)}</p>' if s.sub else ""
    return f'<div class="stat{" stale" if s.stale else ""}"><p class="label">{e(s.label)}</p>{value}{sub}</div>'


def _stats_note(v: CardView) -> str:
    """숫자 칸 밑 회색 안내 + (있으면) 주황 경고 ('자동 · 10. 15. 14:23 읽음 · 어제 못 읽음')."""
    if not v.stats_note and not v.stats_warn:
        return ""
    warn = f'<span class="warn">{e(v.stats_warn)}</span>' if v.stats_warn else ""
    sep = " · " if v.stats_note and warn else ""
    return f'<p class="stats-note">{e(v.stats_note)}{sep}{warn}</p>'


def _row(r: GoalRow) -> str:
    """목표 줄. 막대는 '많을수록 좋음' 이면 채운 만큼 보라(다 채우면 초록), '적을수록 좋음'(최대 N) 이면
    늘 꽉 찬 막대로 충족은 초록·넘침은 주황 — 줄어들수록 막대가 길어지는 착시가 없게."""
    met = r.met
    check = icons.CHECK if met else ""
    goal = f"최대 {fmt_num(r.goal)}" if r.lower_is_better else fmt_num(r.goal)
    unit = f" {r.unit}" if r.unit else ""
    pct = 0.0 if r.value is None else max(r.ratio * 100, 1.5 if r.value else 0.0)
    cls = " met" if met else (" over" if r.lower_is_better and met is False else "")
    return (
        f'<div class="row"><div class="row-top"><span class="rl">{check}{e(r.label)}</span>'
        f'<span class="rv">{e(fmt_num(r.value))} / {e(goal)}{e(unit)}</span></div>'
        f'<div class="bar{cls}"><i style="width:{pct:.1f}%"></i></div></div>'
    )


def _links(links: list[CardLink], title: str) -> str:
    """바깥 링크 줄 (새 탭). 주인 브라우저가 여는 탐색일 뿐 페이지가 읽어 오는 것이 아니라 CSP(default-src 'none')와 상관없고,
    rel=noreferrer 로 대시보드 주소를 넘기지 않는다. https 가 아닌 주소는 그리지 않는다 (소스에서 한 번 더 거른 값)."""
    out = []
    for link in links:
        if not link.url.startswith("https://"):
            continue
        out.append(f'<a class="ext{" primary" if link.primary else ""}" href="{e(link.url)}" target="_blank" '
                   f'rel="noopener noreferrer" aria-label="{e(f"{title} {link.label} (새 탭)")}">'
                   f'{e(link.label)}{NBSP}<span aria-hidden="true">↗</span></a>')
    return f'<div class="links">{"".join(out)}</div>' if out else ""


def _entry(x: ListEntry, token: str) -> str:
    """목록 한 줄: 제목 + 점수 알약, 회색 작은 줄, '상품 열기 ↗'(https 만, 새 탭, 대시보드 주소를 넘기지 않음),
    queue_id 가 있으면 '링크 넣기' 폼 (POST /dashboard/link, 폼 토큰 t·글 번호 q·링크 url).
    링크 칸은 type=text + inputmode=url: 몰 앱의 '공유하기' 글(상품명 + 주소)을 통째로 붙여도 텔레그램 답장처럼 주소만 찾아 붙인다."""
    pill = f'<span class="pill{" hot" if x.hot else ""}">{e(x.pill)}</span>' if x.pill else ""
    out = [f'<li class="lit"><div class="lit-top"><span class="lit-title">{e(x.title)}</span>{pill}</div>']
    if x.sub:
        out.append(f'<p class="lit-sub">{e(x.sub)}</p>')
    act = []
    url = https_url(x.url)
    if url:
        act.append(f'<a class="ext primary" href="{e(url)}" target="_blank" rel="noopener noreferrer" '
                   f'aria-label="{e(f"{x.title} {x.url_label} (새 탭)")}">{e(x.url_label)}{NBSP}<span aria-hidden="true">↗</span></a>')
    if x.queue_id is not None:
        act.append(
            '<form class="linkform" method="post" action="/dashboard/link">'
            f'<input type="hidden" name="t" value="{e(token)}"><input type="hidden" name="q" value="{e(int(x.queue_id))}">'
            '<input type="text" name="url" inputmode="url" required maxlength="1500" autocomplete="off" autocapitalize="off" '
            f'spellcheck="false" placeholder="만든 링크 https://…" aria-label="{e(f"#{x.queue_id} {x.title} 링크")}">'
            '<button type="submit">링크 넣기</button></form>'
        )
    if act:
        out.append(f'<div class="lit-act">{"".join(act)}</div>')
    return "".join(out) + "</li>"


def _group(g: ListGroup, token: str) -> str:
    head = ""
    if g.title or g.summary:
        head = (f'<div class="lg-head"><h3>{e(g.title)}</h3>'
                f'<span class="lg-sum{" unknown" if g.unknown else ""}">{e(g.summary)}</span></div>')
    items = "".join(_entry(x, token) for x in g.entries)
    return f'<section class="lgroup">{head}' + (f'<ol class="litems">{items}</ol>' if items else "") + "</section>"


def _lists(v: CardView, token: str) -> str:
    """그래프 밑 목록 (발행 대기 글 · 링크 기다리는 딜). 줄이 하나도 없으면 empty_text 한 줄."""
    if not v.groups:
        return f'<p class="lempty">{e(v.empty_text)}</p>' if v.empty_text else ""
    return '<div class="lists">' + "".join(_group(g, token) for g in v.groups) + "</div>"


def _chunks(line: str) -> str:
    """' · ' 로 이은 항목들 ('오늘 발행 1 · 임시저장 2 · 댓글 3'). 칸이 좁아 줄이 바뀔 때 '댓글 / 3' 처럼 이름과 숫자가 갈라지거나
    '·' 가 다음 줄 맨 앞에 오지 않게, 항목마다 한 덩어리(넘치게 긴 항목만 안에서 줄바꿈)로 두고 '·' 는 앞 항목 덩어리 안에 넣는다
    (덩어리 바깥에 두면 브라우저가 덩어리와 '·' 사이에서도 줄을 바꾼다)."""
    items = line.split(" · ")
    return " ".join(f'<span class="it">{e(item)}{NBSP + "·" if i < len(items) - 1 else ""}</span>' for i, item in enumerate(items))


def _activity(line: str) -> str:
    """활동 줄 — 항목마다 한 덩어리 (_chunks)."""
    return f'<p class="activity">{_chunks(line)}</p>'


def _form(v: CardView, token: str, today: date, min_day: date | None = None, *, open_: bool = False) -> str:
    """직접 입력 칸. 칸은 비워 두고(지난 값이 들어 있는 것처럼 보이지 않게) 지난 값은 칸 아래 작은 글로.
    참고 화면처럼 카드가 숫자·그래프·목표로 끝나 보이게 '직접 넣기' 를 눌러야 펼쳐진다 (스크립트 없이 details).
    넣은 값이 하나도 없을 때(처음 쓸 때)와 open_(이 카드의 입력이 잘못돼 돌아왔을 때)은 펼쳐 둔다."""
    if not v.manual:
        return ""
    is_open = open_ or all(v.manual_values.get(f.metric) is None for f in v.manual)
    fields = []
    for f in v.manual:
        cur, day = v.manual_values.get(f.metric), v.manual_days.get(f.metric)
        # 칸이 좁으면 날짜 앞에서만 줄이 바뀌게 (날짜 가운데는 붙여 둠)
        when = f" · {fmt_short_day(day).replace(' ', NBSP)}" if day else ""
        last = f"지난 값 {fmt_num(cur)}{when}" if cur is not None else "아직 없음"
        fields.append(
            f'<label>{e(f.label)}<input name="{e(f.metric)}" inputmode="numeric" pattern="[0-9,]*" maxlength="13" '
            f'autocomplete="off" placeholder="숫자"><span class="last">{e(last)}</span></label>'
        )
    note = ""
    if any(f.day_offset for f in v.manual):
        names = "·".join(f.label for f in v.manual if f.day_offset)
        note = f'<p class="form-note">{e(names)}{e(josa(names, "은", "는"))} 입력하는 날의 전날 값으로 저장돼요.</p>'
    what = "·".join(f.label for f in v.manual)
    return (
        f'<details class="manual-box"{" open" if is_open else ""}><summary>직접 넣기<span class="what">{e(what)}</span></summary>'
        '<form class="manual" method="get" action="/dashboard/input">'
        f'<input type="hidden" name="src" value="{e(v.key)}"><input type="hidden" name="t" value="{e(token)}">'
        + "".join(fields)
        + f'<label class="date">입력하는 날<input type="date" name="date" value="{e(today.isoformat())}" max="{e(today.isoformat())}"'
        + (f' min="{e(min_day.isoformat())}"' if min_day else "") + '><span class="last">오늘이 아니면 바꿔 주세요</span></label>'
        '<button type="submit">저장</button>' + note + "</form></details>"
    )


def card_html(v: CardView, *, token: str, today: date, tz: str, spark_days: int, min_day: date | None = None,
              notice: tuple[str, str] | None = None, app: bool = False) -> str:
    """카드 하나. notice 가 있으면(이 카드에서 저장·다시 읽기를 누르고 돌아왔을 때) 머리 바로 밑에.
    app=True 는 앱의 통계 탭 조각: 링크·폼 대신 data-action 단추 (app.js 가 세션마다 다른 토큰으로 JSON 으로 보냄)."""
    status = v.status
    # 앱: 목표를 아직 판단할 수 없으면 '판단 불가' 칩·빈 목표 막대·설명 문단을 빼고 '아직 읽은 값이 없어요' 한 줄만 (같은 말 세 번 X)
    goal_unknown = app and bool(v.rows) and all(r.met is None for r in v.rows)
    if app and status == UNKNOWN:
        status = ""
    chip = f'<span class="chip {e(status)}">{e(CHIP_LABELS[status])}</span>' if status else ""
    btn = ""
    if v.refreshable and app:
        btn = (f'<button type="button" class="btn" data-action="stats.refresh" data-src="{e(v.key)}" '
               f'aria-label="{e(v.title)} 다시 읽기">{icons.REFRESH}<span class="btn-text">다시 읽기</span></button>')
    elif v.refreshable:
        href = "/dashboard/refresh?" + urlencode({"src": v.key, "t": token})
        # 좁은 폰에서는 ↻ 만 (글자는 화면 읽기용으로 남김) — 카드 이름이 잘리지 않게
        btn = (f'<a class="btn" href="{e(href)}" title="다시 읽기" aria-label="{e(v.title)} 다시 읽기">'
               f'{icons.REFRESH}<span class="btn-text">다시 읽기</span></a>')
    read = (f"{v.read_label} {fmt_time(v.read_at.astimezone(ZoneInfo(tz)))}" if v.read_at else "아직 읽은 값이 없어요")
    parts = [
        f'<article class="card" id="card-{e(v.key)}">',
        f'<div class="head"><div class="name"><span class="icon">{v.icon}</span><h2>{e(v.title)}</h2></div>'
        f'<div class="actions">{chip}{btn}</div></div>',
    ]
    if notice:
        text, kind = notice
        parts.append(f'<div class="notice{" bad" if kind == "bad" else ""}" role="status">{e(text)}</div>')
    parts += [
        f'<p class="read">{e(read)}</p>',
        # 숫자 칸은 두 칸씩 줄 (블로그 카드는 자기 통계가 있으면 네 칸)
        '<div class="stats">' + "".join(_stat(s) for s in v.stats[:4]) + "</div>",
        _links(v.links, v.title),
        _stats_note(v),
        sparkline(v.spark, days=spark_days, today=today, label=v.spark_label),
        _lists(v, token),
    ]
    if v.rows and not goal_unknown:
        parts.append(f"<h3>{e(v.program or '목표')}</h3>")
        parts.extend(_row(r) for r in v.rows)
    parts.extend(_activity(line) for line in v.activity)
    note = v.note
    if goal_unknown and note == explain(v.program, v.rows):
        note = ""
    if note:
        parts.append(f'<p class="note">{e(haeyo(note) if app else note)}</p>')
    parts.extend(f'<p class="hint">{e(h)}</p>' for h in v.hints)
    parts.append(_app_input(v, today) if app else _form(v, token, today, min_day, open_=bool(notice and notice[1] == "bad")))
    parts.append("</article>")
    return "".join(parts)


HAEYO = (("남았습니다.", "남았어요."), ("많습니다.", "많아요."), ("채웠습니다.", "채웠어요."),
         ("판단할 수 없습니다.", "판단할 수 없어요."), ("아직 확인할 수 없습니다.", "아직 확인 못 했어요."), ("없습니다.", "없어요."),
         ("있습니다.", "있어요."), ("합니다.", "해요."), ("됩니다.", "돼요."))


def haeyo(text: str) -> str:
    """앱 안내 문장은 해요체로 ('…남았습니다.' → '…남았어요.')."""
    for a, b in HAEYO:
        text = text.replace(a, b)
    return text


def _app_input(v: CardView, today: date) -> str:
    """앱의 '직접 넣기' 단추 (칸은 앱이 시트로 띄움 — 조각에는 폼이 없다). 지난 값은 단추 옆 작은 글로."""
    if not v.manual:
        return ""
    fields = json.dumps([{"metric": f.metric, "label": f.label} for f in v.manual], ensure_ascii=False)
    last = []
    for f in v.manual:
        cur, day = v.manual_values.get(f.metric), v.manual_days.get(f.metric)
        if cur is not None:
            last.append(f"{f.label} {fmt_num(cur)}" + (f" · {fmt_short_day(day)}" if day else ""))
    what = " · ".join(last) if last else "·".join(f.label for f in v.manual)
    return (f'<div class="manual-app"><button type="button" class="mbtn" data-action="stats.input" data-src="{e(v.key)}" '
            f'data-title="{e(v.title)} 직접 넣기" data-today="{e(today.isoformat())}" data-fields="{e(fields)}">직접 넣기</button>'
            f'<span class="what">{e(what)}</span></div>')


# 앱 통계 탭에 넣지 않는 카드: 할 일·딜 탭·연결과 겹치는 것 (한 번만 보이게)
STATS_SKIP = ("dealbot_links", "blog_waiting", "dealbot")
# 앱 안에서만 쓰는 카드 꾸밈 (색은 app.css 의 토큰 — 어두운 모드도 그대로). .stx 아래로만
STATS_CSS = """
.stx{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
@media (min-width:760px){.stx{grid-template-columns:repeat(2,minmax(0,1fr))}}
.stx .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px 16px;min-width:0}
.stx .head{display:flex;align-items:center;justify-content:space-between;gap:6px 8px;min-height:44px}
.stx .name{display:flex;align-items:center;gap:8px;min-width:0}.stx h2{font-size:16px;margin:0}
.stx .icon{display:inline-flex;flex:none}.stx .actions{display:flex;align-items:center;gap:6px;margin-left:auto}
.stx .chip{font-size:12px;padding:2px 8px;border-radius:6px;font-weight:700;white-space:nowrap;background:var(--grey);color:var(--sub)}
.stx .chip.unmet{background:var(--or-soft);color:var(--or)}.stx .chip.met{background:var(--gr-soft);color:var(--gr)}
.stx .btn{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:44px;min-width:44px;border:1px solid var(--fline);
border-radius:10px;background:var(--card);color:var(--text);padding:0 10px;font-size:14px}
.stx .read,.stx .stats-note,.stx .note{color:var(--muted);font-size:13px;margin:4px 0 10px}
.stx .stats{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.stx .stat{background:var(--grey);border-radius:10px;padding:10px 12px;min-width:0}
.stx .stat .label{color:var(--sub);font-size:13px;margin:0 0 2px}.stx .stat .value{font-size:20px;font-weight:700;margin:0;line-height:1.3}
.stx .stat .value.missing{font-size:15px;color:var(--muted)}.stx .unit{font-size:12px;color:var(--sub);margin-left:2px}
.stx .stat .sub{font-size:12px;color:var(--muted);margin:2px 0 0}.stx .num{white-space:nowrap}
.stx .delta{font-size:12px;font-weight:700;margin-left:6px}.stx .delta.up{color:var(--gr)}.stx .delta.down{color:var(--red)}
.stx .delta small{font-weight:500;color:var(--muted);margin-left:2px}.stx .stat.stale .value{color:var(--muted)}
.stx .spark{display:block;width:100%;max-width:260px;height:46px;margin:12px auto 4px}.stx .spark path{stroke:var(--ac-ink)}
.stx .spark path[stroke-dasharray]{stroke:var(--line)}
@media (max-width:420px){.stx .btn-text{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}}
.stx .links{display:flex;flex-wrap:wrap;gap:6px 12px;margin:10px 0 0}
.stx .ext{color:var(--ac-ink);font-size:14px;text-decoration:none;min-height:44px;display:inline-flex;align-items:center}
.stx .ext.primary{flex:1 1 auto;justify-content:center;border-radius:10px;background:var(--ac-soft);font-weight:600;padding:0 14px}
.stx h3{font-size:13px;color:var(--sub);margin:14px 0 8px}
.stx .row{display:block;min-height:0;border:0;padding:0;margin:0 0 10px}
.stx .row-top{display:flex;justify-content:space-between;gap:8px;font-size:14px}.stx .rv{color:var(--sub);white-space:nowrap}
.stx .bar{height:6px;background:var(--grey);border-radius:3px;overflow:hidden;margin-top:5px}
.stx .bar i{display:block;height:100%;background:var(--ac)}.stx .bar.met i{background:var(--gr)}.stx .bar.over i{background:var(--or)}
.stx .activity{font-size:13px;color:var(--sub);background:var(--grey);border-radius:8px;padding:8px 10px;margin:10px 0 0}
.stx .it{display:inline-block;max-width:100%}.stx .hint{color:var(--or);font-size:13px;margin:8px 0 0}
.stx .manual-app{display:flex;align-items:center;flex-wrap:wrap;gap:4px 10px;margin:12px 0 0;border-top:1px dashed var(--line);padding-top:8px}
.stx .mbtn{min-height:44px;border:0;background:none;color:var(--ac-ink);font-size:15px;font-weight:700;padding:0}
.stx .what{font-size:13px;color:var(--muted)}
"""


def stats_fragment(*, views: list[CardView], today: date, tz: str, spark_days: int) -> str:
    """앱의 통계 탭 (GET /dashboard/api/stats.html): 예전 카드 그대로 한 줄씩 (텔레그램·인스타·스레드·블로그 셋·오픈채팅·쿠팡).
    폼·스크립트 없음 — 단추는 data-action. 카드 하나가 깨져도 나머지는 뜬다."""
    cards = []
    for v in views:
        if v.type in STATS_SKIP:
            continue
        try:
            cards.append(card_html(v, token="", today=today, tz=tz, spark_days=spark_days, app=True))
        except Exception as err:  # noqa: BLE001
            log.exception("dashboard stats card %s render failed", v.key)
            cards.append(_broken_card(v, err))
    body = "".join(cards) or '<p class="empty">보여 줄 카드가 없어요</p>'
    return f"<style>{STATS_CSS}</style>{body}"


def _broken_card(v: CardView, err: BaseException) -> str:
    return (f'<article class="card" id="card-{e(v.key)}"><div class="head"><div class="name"><span class="icon">{v.icon}</span>'
            f'<h2>{e(v.title)}</h2></div><div class="actions"><span class="chip unknown">{e(CHIP_LABELS[UNKNOWN])}</span></div></div>'
            f'<p class="hint">{e(f"카드를 그리지 못함 ({safe_error(err)})")}</p></article>')


def _todo_row(r: todo_mod.TodoRow, now: datetime) -> str:
    """할 일 한 줄: 표시 칩 · 제목 · '→ 어떻게' · (블로그 · 몇 시간 전 · N시간 남음) · 버튼.
    버튼은 https 주소(새 탭, 대시보드 주소를 넘기지 않음 — 텔레그램 주소는 todo.link_target 이 이미 뺌) 또는 같은 페이지 카드(#card-…)."""
    emoji, label, cls = todo_mod.CHIPS.get(r.marker, todo_mod.CHIPS[todo_mod.TODO])
    chip = f'<span class="mk {cls}">{e(emoji)}{NBSP}{e(label)}</span>'
    if r.urgent and r.marker != todo_mod.STOP:
        chip += '<span class="mk m-urgent">급함</span>'
    how = "<br>".join(e(line) for line in r.how.split("\n") if line)
    meta = [x for x in (r.blog_label, todo_mod.ago(r.since, now) if r.since else "") if x]
    meta_html = e(" · ".join(meta))
    if r.expires_at is not None:
        sep = " · " if meta_html else ""
        meta_html += f'{sep}<span class="left">{e(todo_mod.left(r.expires_at, now))}</span>'
    act = ""
    url = todo_mod.link_target(r.link)
    if url:
        label = r.link_label or "열기"
        act = (f'<a class="td-act" href="{e(url)}" target="_blank" rel="noopener noreferrer" '
               f'aria-label="{e(f"{r.short} {label} (새 탭)")}">{e(label)}{NBSP}<span aria-hidden="true">↗</span></a>')
    elif r.anchor and r.anchor.startswith("#card-"):
        act = (f'<a class="td-act" href="{e(r.anchor)}">{e(r.anchor_label or "보기")}{NBSP}'
               '<span aria-hidden="true">↓</span></a>')
    foot = f'<div class="td-foot"><span class="td-meta">{meta_html}</span>{act}</div>' if meta_html or act else ""
    return (f'<li class="td"><div class="td-top">{chip}<span class="td-title">{e(r.short)}</span></div>'
            + (f'<p class="td-how">{how}</p>' if how else "") + foot + "</li>")


def todo_html(view: todo_mod.TodoView, *, now: datetime) -> str:
    """맨 위 '지금 할 일 N' 카드. 처음 SHOW 줄만 보이고 나머지는 '더 보기' (스크립트 없이 details). 없으면 '지금 할 일 없음 ✓'."""
    hints = "".join(f'<p class="todo-hint">{e(h)}</p>' for h in view.hints)
    if not view.rows:
        return (f'<section class="todo done" id="todo" aria-label="{e(todo_mod.TITLE)}">'
                f'<h2>{e(todo_mod.EMPTY_TEXT)}</h2>{hints}</section>')
    first, rest = view.rows[: todo_mod.SHOW], view.rows[todo_mod.SHOW:]
    parts = [f'<section class="todo" id="todo" aria-labelledby="todo-h"><h2 id="todo-h">{e(todo_mod.TITLE)}'
             f'<span class="todo-n">{len(view.rows)}</span></h2>',
             '<ol class="todo-list">' + "".join(_todo_row(r, now) for r in first) + "</ol>"]
    if rest:
        parts.append(f'<details class="todo-more"><summary>더 보기 ({len(rest)}개)</summary>'
                     '<ol class="todo-list">' + "".join(_todo_row(r, now) for r in rest) + "</ol></details>")
    parts.append(hints + "</section>")
    return "".join(parts)


def _todo_safe(view: todo_mod.TodoView | None, now: datetime) -> str:
    if view is None:
        return ""
    try:
        return todo_html(view, now=now)
    except Exception as err:  # noqa: BLE001 — 할 일 카드가 깨져도 페이지는 뜬다
        log.exception("dashboard todo render failed")
        return (f'<section class="todo" id="todo"><h2>{e(todo_mod.TITLE)}</h2>'
                f'<p class="hint">{e(f"할 일 목록을 그리지 못함 ({safe_error(err)})")}</p></section>')


def _counts_html(counts: dict[str, int] | None) -> str:
    if not counts:
        return ""
    chips = "".join(f'<span class="chip {k}">{e(CHIP_LABELS[k])} {counts.get(k, 0)}</span>' for k in (MET, UNMET, UNKNOWN))
    return f'<span class="counts" aria-label="카드 상태">{chips}</span>'


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
    notice_src: str | None = None,
    footer: str = "",
    min_day: date | None = None,
    counts: dict[str, int] | None = None,
    todo: todo_mod.TodoView | None = None,
    now: datetime | None = None,
    logout_path: str | None = None,
) -> str:
    """notice_src 가 카드 키면 안내는 그 카드 안에(돌아가면 그 카드로 내려가므로), 아니면 맨 위에.
    todo 가 있으면 요약 줄 위(머리 바로 밑)에 '지금 할 일' 카드 — now 는 '3시간 전'·'14시간 남음' 기준.
    logout_path 가 있으면 머리 오른쪽에 '로그아웃' (그 주소로 폼 토큰과 함께 POST).
    <head> 에는 바탕화면 앱용 링크·메타와 <title>Sns-board</title> (webapp.head_tags) — 화면 맨 위 이름은 title 그대로."""
    updated = f"마지막 업데이트 {fmt_time(updated_at.astimezone(ZoneInfo(tz)))}" if updated_at else "아직 읽은 값이 없어요"
    head = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'>"
        f"{webapp.head_tags()}<style>{CSS}</style></head><body><div class='wrap'>"
    )
    keys = {v.key for v in views}
    in_card = notice is not None and notice_src in keys
    logout = (f'<form class="logout" method="post" action="{e(logout_path)}"><input type="hidden" name="t" value="{e(token)}">'
              '<button type="submit">로그아웃</button></form>') if logout_path else ""
    body = [f'<header class="top"><h1>{e(title)}</h1><div class="meta"><span class="updated">{e(updated)}</span>'
            f'{_counts_html(counts)}{logout}</div></header>']
    if notice and not in_card:
        text, kind = notice
        body.append(f'<div class="notice{" bad" if kind == "bad" else ""}" role="status">{e(text)}</div>')
    if todo is not None:
        body.append(_todo_safe(todo, now or datetime.now(ZoneInfo(tz))))
    if summary:
        body.append('<section class="summary">' + "".join(
            f'<div class="sum"><p class="label">{e(label)}</p><p class="value">{e(value)}</p>'
            + (f'<p class="sub">{e(sub)}</p>' if sub else "") + "</div>"
            for label, value, sub in summary
        ) + "</section>")
    if views:
        cards = []
        for v in views:
            try:
                cards.append(card_html(v, token=token, today=today, tz=tz, spark_days=spark_days, min_day=min_day,
                                       notice=notice if in_card and v.key == notice_src else None))
            except Exception as err:  # noqa: BLE001 — 카드 하나가 깨져도 페이지는 뜬다
                log.exception("dashboard card %s render failed", v.key)
                cards.append(_broken_card(v, err))
        body.append('<main class="grid">' + "".join(cards) + "</main>")
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
             "missing": s.missing if s.value is None else None, "sub": s.sub or None, "stale": s.stale}
            for s in v.stats
        ],
        "spark": {"label": v.spark_label, "points": [{"day": d, "value": val} for d, val in v.spark]},
        "program": {
            "title": v.program,
            "rows": [{"label": r.label, "value": r.value, "goal": r.goal, "unit": r.unit, "met": r.met,
                      "lower_is_better": r.lower_is_better} for r in v.rows],
        },
        "activity": v.activity,
        "links": [{"label": link.label, "url": link.url} for link in v.links if link.url.startswith("https://")],
        "stats_note": v.stats_note or None,
        "stats_warn": v.stats_warn or None,
        "note": v.note or None,
        "hints": v.hints,
        "manual": [{"metric": f.metric, "label": f.label, "unit": f.unit, "day_offset": f.day_offset,
                    "latest": v.manual_values.get(f.metric), "latest_day": v.manual_days.get(f.metric)} for f in v.manual],
        # 목록 카드의 값 (발행 대기 글 · 링크 기다리는 딜). 폼 토큰은 화면의 폼에만 있고 여기에는 없다
        "detail": v.detail or None,
    }


def todo_dict(view: todo_mod.TodoView, now: datetime, tz: str) -> dict[str, Any]:
    """'지금 할 일' (화면과 같은 순서). 주소는 화면에 그리는 것과 같은 것만 (https, 텔레그램 주소 아님) — 폼 토큰은 없다."""
    def iso(dt: datetime | None) -> str | None:
        return dt.astimezone(ZoneInfo(tz)).isoformat() if dt else None

    items = []
    for r in view.rows:
        url = todo_mod.link_target(r.link)
        anchor = r.anchor if r.anchor and r.anchor.startswith("#card-") and not url else None
        items.append({
            "key": r.key, "marker": r.marker, "urgent": r.urgent, "blog": r.blog, "blog_label": r.blog_label or None,
            "kind": r.kind, "short": r.short, "text": r.text, "how": r.how or None, "since": iso(r.since),
            "age": todo_mod.ago(r.since, now) if r.since else None, "expires_at": iso(r.expires_at),
            "left": todo_mod.left(r.expires_at, now) if r.expires_at else None, "link": url,
            "action": (r.link_label or "열기") if url else (r.anchor_label or None) if anchor else None, "anchor": anchor,
            "group": r.group or None, "count": r.count, "source": r.source, "ids": r.ids,
        })
    return {"title": todo_mod.TITLE, "count": len(items), "items": items, "hints": view.hints,
            "blog_generated_at": iso(view.blog_at), "empty_text": todo_mod.EMPTY_TEXT if not items else None}


def render_json(*, title: str, views: list[CardView], generated_at: datetime, tz: str,
                summary: list[tuple[str, str, str]], todo: todo_mod.TodoView | None = None) -> str:
    data: dict[str, Any] = {
        "title": title,
        "generated_at": generated_at.astimezone(ZoneInfo(tz)).isoformat(),
    }
    if todo is not None:
        data["todo"] = todo_dict(todo, generated_at, tz)
    data["summary"] = [{"label": a, "value": b, "sub": c or None} for a, b, c in summary]
    data["cards"] = [view_dict(v, tz) for v in views]
    return json.dumps(data, ensure_ascii=False)
