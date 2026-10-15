"""대시보드 HTML (참고 화면을 닮게): 밝은 회색 바탕, 흰 둥근 카드 3열, 상태 칩, 숫자 칸 2개, 보라색 계단 그래프, 목표 막대.

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

from dealbot.dashboard import icons
from dealbot.dashboard.model import (
    CHIP_LABELS,
    MET,
    UNKNOWN,
    UNMET,
    CardLink,
    CardView,
    GoalRow,
    Stat,
    fmt_num,
    fmt_short_day,
    fmt_signed,
    fmt_time,
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
.delta{font-size:12px;font-weight:700;margin-left:6px;white-space:nowrap}
.delta small{font-weight:500;color:var(--muted);margin-left:2px;font-size:11px}
.delta.up{color:var(--green)}.delta.down{color:#d93025}.delta.flat{color:var(--muted)}
.spark{display:block;width:100%;max-width:240px;height:46px;margin:14px auto 4px}
.links{display:flex;align-items:center;flex-wrap:wrap;gap:6px 12px;margin:10px 0 0}
.ext{color:var(--sub);font-size:12px;text-decoration:none;white-space:nowrap;padding:4px 0}
.ext:hover{text-decoration:underline}
.ext.primary{flex:1 1 auto;display:inline-flex;align-items:center;justify-content:center;gap:4px;min-height:38px;padding:7px 14px;
border-radius:8px;background:#eef0ff;border:1px solid #dcdffc;color:#3f35a8;font-size:13px;font-weight:600}
.ext.primary:hover{background:#e4e7ff;text-decoration:none}
.stats-note{color:var(--muted);font-size:12px;line-height:1.5;margin:8px 0 0}
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
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:24px;color:var(--sub)}
.foot{color:var(--muted);font-size:12px;margin:18px 0 0}
.card{container-type:inline-size}
@container (max-width:344px){.btn .btn-text{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
clip-path:inset(50%);white-space:nowrap}.btn{padding:4px 8px}}
@media (max-width:420px){.btn .btn-text{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);
clip-path:inset(50%);white-space:nowrap}.btn{padding:4px 8px}}
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
    sub = f'<p class="sub">{e(s.sub)}</p>' if s.sub else ""
    return f'<div class="stat"><p class="label">{e(s.label)}</p>{value}{sub}</div>'


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


def _activity(line: str) -> str:
    """활동 줄 ('오늘 발행 1 · 임시저장 2 · 댓글 3'). 칸이 좁아 줄이 바뀔 때 '댓글 / 3' 처럼 이름과 숫자가 갈라지거나
    '·' 가 다음 줄 맨 앞에 오지 않게, 항목마다 한 덩어리(넘치게 긴 항목만 안에서 줄바꿈)로 두고 '·' 는 앞 항목 덩어리 안에 넣는다
    (덩어리 바깥에 두면 브라우저가 덩어리와 '·' 사이에서도 줄을 바꾼다)."""
    items = line.split(" · ")
    spans = [f'<span class="it">{e(item)}{NBSP + "·" if i < len(items) - 1 else ""}</span>' for i, item in enumerate(items)]
    return f'<p class="activity">{" ".join(spans)}</p>'


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
              notice: tuple[str, str] | None = None) -> str:
    """카드 하나. notice 가 있으면(이 카드에서 저장·다시 읽기를 누르고 돌아왔을 때) 머리 바로 밑에."""
    status = v.status
    chip = f'<span class="chip {e(status)}">{e(CHIP_LABELS[status])}</span>' if status else ""
    btn = ""
    if v.refreshable:
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
        '<div class="stats">' + "".join(_stat(s) for s in v.stats[:2]) + "</div>",
        _links(v.links, v.title),
        f'<p class="stats-note">{e(v.stats_note)}</p>' if v.stats_note else "",
        sparkline(v.spark, days=spark_days, today=today, label=v.spark_label),
    ]
    if v.rows:
        parts.append(f"<h3>{e(v.program or '목표')}</h3>")
        parts.extend(_row(r) for r in v.rows)
    parts.extend(_activity(line) for line in v.activity)
    if v.note:
        parts.append(f'<p class="note">{e(v.note)}</p>')
    parts.extend(f'<p class="hint">{e(h)}</p>' for h in v.hints)
    parts.append(_form(v, token, today, min_day, open_=bool(notice and notice[1] == "bad")))
    parts.append("</article>")
    return "".join(parts)


def _broken_card(v: CardView, err: BaseException) -> str:
    return (f'<article class="card" id="card-{e(v.key)}"><div class="head"><div class="name"><span class="icon">{v.icon}</span>'
            f'<h2>{e(v.title)}</h2></div><div class="actions"><span class="chip unknown">{e(CHIP_LABELS[UNKNOWN])}</span></div></div>'
            f'<p class="hint">{e(f"카드를 그리지 못함 ({safe_error(err)})")}</p></article>')


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
) -> str:
    """notice_src 가 카드 키면 안내는 그 카드 안에(돌아가면 그 카드로 내려가므로), 아니면 맨 위에."""
    updated = f"마지막 업데이트 {fmt_time(updated_at.astimezone(ZoneInfo(tz)))}" if updated_at else "아직 읽은 값이 없어요"
    head = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex,nofollow'><meta name='referrer' content='no-referrer'>"
        f"<title>{e(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
    )
    keys = {v.key for v in views}
    in_card = notice is not None and notice_src in keys
    body = [f'<header class="top"><h1>{e(title)}</h1><div class="meta"><span class="updated">{e(updated)}</span>'
            f'{_counts_html(counts)}</div></header>']
    if notice and not in_card:
        text, kind = notice
        body.append(f'<div class="notice{" bad" if kind == "bad" else ""}" role="status">{e(text)}</div>')
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
        "links": [{"label": link.label, "url": link.url} for link in v.links if link.url.startswith("https://")],
        "stats_note": v.stats_note or None,
        "note": v.note or None,
        "hints": v.hints,
        "manual": [{"metric": f.metric, "label": f.label, "unit": f.unit, "day_offset": f.day_offset,
                    "latest": v.manual_values.get(f.metric), "latest_day": v.manual_days.get(f.metric)} for f in v.manual],
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
