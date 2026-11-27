"""꾸미기 v14 · 하루치 v10 — 실제 네이버 발행창(11/26 진단 autopub:dg) 모양에서 다음 날 예약 날짜 고르기.

진단으로 안 것: 날짜 칸은 읽기 전용 INPUT 'YYYY. MM. DD' · 이름은 CSS 모듈 '<이름>__<해시>' (layer_popup__VVWW8 is_show__LbCeK · layer_publish__Jv1Pu) ·
data-click-area 'tpb.publish' · v13 은 1~31 숫자가 든 큰 묶음(발행창 머리)을 달력으로 봤다.
가짜 발행창: 날짜 칸을 누르면 0.3초 뒤 문서 끝(portal)에 달력이 붙음 · 발행창 안에는 1~31 숫자가 늘 보이는 미끼 줄 ·
  rdp = react-datepicker 모양 (머리 'November 2026' · 날 칸 div role=option aria-label 'Choose Friday, November 27th, 2026' · day--0DD · 다른 달 outside-month)
  kocal = CSS 모듈 단추 aria-label '2026년 11월 27일' data-date · 머리는 '11월' 뿐 · 앞에 지난달 날(표시 없는 이름) 숫자가 같이 보임
  icon = rdp 인데 날짜 칸을 눌러도 안 뜨고 옆 달력 단추(class calendar_btn__ · data-click-area)로 뜸
  mute = rdp 인데 날을 눌러도 안 바뀜 → '현재'로 되돌리고 진단(A 날짜 칸 · C 이름 칸 · B 새로 보인 것)을 남김.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from dealbot.dashboard import autopub, kkumi
from test_dash_kkumi import INNER, NODE, _playwright_env, code
from test_dash_kkumi_when import RSV_RUN, _date, _lab, _pasted

LAYER = r"""<script>
window.rsvLog = {confirm: 0, radio: 0, opened: 0};
document.querySelector('.publish_btn').addEventListener('click', () => {
  const tg = document.getElementById('tg');
  if (!tg || tg.querySelector('.option_time__Kc1d2')) return;
  const MODE = '__MODE__', now = new Date(), w = document.createElement('div'), p2 = n => String(n).padStart(2, '0');
  let sel = [now.getFullYear(), now.getMonth() + 1, now.getDate()], view = [sel[0], sel[1]];
  w.className = 'option_time__Kc1d2';
  w.innerHTML = '<div class="page_nav__Pq7r1">' + [...Array(31)].map((_, i) => '<span class="num__a1b2c">' + (i + 1) + '</span>').join('') + '</div>'
    + '<span class="radio_item__m1"><input type="radio" id="radio_time1" name="radio_time" checked><label for="radio_time1" class="radio_label__mX3">현재</label></span>'
    + '<span class="radio_item__m1"><input type="radio" id="radio_time2" name="radio_time"><label for="radio_time2" class="radio_label__mX3">예약</label></span>'
    + '<div class="time_setting__vB8" style="display:none"><div class="date_area__Hh2k"><div class="input_wrap__z0"><input type="text" class="input_date__Qd7Rt" readonly></div>'
    + (MODE === 'icon' ? '<button type="button" class="calendar_btn__Q1w2e" data-click-area="tpb*i.calendar"><i class="icon__x9"></i></button>' : '') + '</div>'
    + '<select class="hour_option__J4k"></select>시 <select class="minute_option__V2n"></select>분</div>'
    + '<button type="button" class="confirm_btn__WzX1" data-click-area="tpb*i.publish" data-testid="seOnePublishBtn">발행</button>';
  tg.append(w);
  const q = s => w.querySelector(s), inp = q('.input_date__Qd7Rt');
  const show = () => { inp.value = sel[0] + '. ' + p2(sel[1]) + '. ' + p2(sel[2]); };
  const opts = () => {
    q('.hour_option__J4k').innerHTML = [...Array(24)].map((_, i) => '<option value="' + i + '">' + p2(i) + '</option>').join('');
    q('.minute_option__V2n').innerHTML = [0, 10, 20, 30, 40, 50].map(i => '<option value="' + i + '">' + p2(i) + '</option>').join('');
  };
  show(); opts(); q('.hour_option__J4k').value = '9';
  for (const r of w.querySelectorAll('input[type=radio]')) r.addEventListener('change', () => { window.rsvLog.radio++; q('.time_setting__vB8').style.display = q('#radio_time2').checked ? 'block' : 'none'; });
  q('.confirm_btn__WzX1').onclick = () => { window.rsvLog.confirm++; };
  const close = () => { const c = document.getElementById('dp_portal'); if (c) c.remove(); };
  const pick = (y, m, d) => { if (MODE === 'mute') return; sel = [y, m, d]; show(); close(); for (const c of w.querySelectorAll('select')) c.replaceWith(Object.assign(document.createElement('select'), {className: c.className})); opts(); };
  const EN = (y, m, d) => { const t = new Date(y, m - 1, d), s = d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th';
    return 'Choose ' + t.toLocaleDateString('en-US', {weekday: 'long'}) + ', ' + t.toLocaleDateString('en-US', {month: 'long'}) + ' ' + d + s + ', ' + y; };
  const draw = () => {
    let c = document.getElementById('dp_portal');
    if (!c) { c = document.createElement('div'); c.id = 'dp_portal'; document.body.append(c); }
    const [y, m] = view, start = new Date(y, m - 1, 1 - new Date(y, m - 1, 1).getDay()), today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const ko = MODE === 'kocal', name = new Date(y, m - 1, 1).toLocaleDateString('en-US', {month: 'long'});
    let h = ko ? '<div class="calendar_wrap__Ab3"><div class="calendar_head__Cd4"><button type="button" class="nav_prev__Ef5" aria-label="이전 달"></button><strong class="month__Gh6">' + m + '월</strong><button type="button" class="nav_next__Ij7" aria-label="다음 달"></button></div><div class="calendar_body__Kl8">'
      : '<div class="react-datepicker-popper" style="position:absolute;top:40px;left:10px;background:#fff"><div class="react-datepicker"><button type="button" class="react-datepicker__navigation react-datepicker__navigation--previous" aria-label="Previous Month"><span>Previous Month</span></button>'
        + '<button type="button" class="react-datepicker__navigation react-datepicker__navigation--next" aria-label="Next Month"><span>Next Month</span></button><div class="react-datepicker__month-container"><div class="react-datepicker__header"><h2 class="react-datepicker__current-month">' + name + ' ' + y + '</h2></div>'
        + '<div class="react-datepicker__month" role="listbox" aria-label="month  ' + y + '-' + p2(m) + '">';
    for (let r = 0; r < 6; r++) {
      h += ko ? '<div class="week__Mn9">' : '<div class="react-datepicker__week">';
      for (let i = 0; i < 7; i++) {
        const t = new Date(start.getFullYear(), start.getMonth(), start.getDate() + r * 7 + i), Y = t.getFullYear(), M = t.getMonth() + 1, d = t.getDate(), other = M !== m, past = t < today;
        h += ko ? '<button type="button" class="day__h4sh' + (other ? ' dim__k3j' : '') + '" aria-label="' + Y + '년 ' + M + '월 ' + d + '일" data-date="' + Y + '-' + p2(M) + '-' + p2(d) + '" data-y="' + Y + '" data-m="' + M + '"' + (past ? ' disabled' : '') + '><span class="num__p0o">' + d + '</span></button>'
          : '<div class="react-datepicker__day react-datepicker__day--0' + p2(d) + (other ? ' react-datepicker__day--outside-month' : '') + (past ? ' react-datepicker__day--disabled' : '') + '" tabindex="-1" aria-label="' + EN(Y, M, d) + '" role="option" aria-disabled="' + past + '" data-y="' + Y + '" data-m="' + M + '">' + d + '</div>';
      }
      h += '</div>';
    }
    c.innerHTML = h + (ko ? '</div></div>' : '</div></div></div></div>');
    c.querySelector(ko ? '.nav_next__Ij7' : '.react-datepicker__navigation--next').onclick = () => { view = m === 12 ? [y + 1, 1] : [y, m + 1]; draw(); };
    for (const e of c.querySelectorAll(ko ? '.day__h4sh' : '.react-datepicker__day')) e.onclick = () => { if (!e.matches('[disabled],.react-datepicker__day--disabled')) pick(+e.dataset.y, +e.dataset.m, +e.textContent); };
  };
  const open = () => { window.rsvLog.opened++; view = [sel[0], sel[1]]; setTimeout(draw, 300); };
  if (MODE === 'icon') q('.calendar_btn__Q1w2e').onclick = open; else inp.onclick = open;
  window.rsvState = () => ({now: q('#radio_time1').checked, book: q('#radio_time2').checked, date: inp.value,
    h: q('.hour_option__J4k').value, m: q('.minute_option__V2n').value, ...window.rsvLog});
});
</script></body></html>
"""


def _go(mode: str, when: datetime) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RSV_RUN, INNER.replace("</body></html>", LAYER.replace("__MODE__", mode)), code(), _pasted(when)],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "태그 2/2" in got["alert"] and got["tags"].split() == ["#아일릿", "#키링"], got["alert"]
    return got


def _days() -> list[datetime]:
    now = datetime.now()
    tmr = (now + timedelta(days=1)).replace(hour=9, minute=50, second=0, microsecond=0)  # 11/26 밤 하루치 그대로 (내일 09:50)
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=2, hour=21, minute=20, second=0, microsecond=0)  # 다음 달 2일 (다음 단추)
    return [tmr, nxt]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
@pytest.mark.parametrize("mode", ["rdp", "kocal", "icon"])
def test_v14_picks_the_next_day_on_the_real_layer_shape(mode: str) -> None:
    """읽기 전용 날짜 칸 · 나중에 문서 끝에 붙는 달력 · 늘 보이는 숫자 미끼 · 날 이름(aria-label·data-date)으로 그 날을 골라 채움 — 발행 단추 0번."""
    for when in _days():
        got = _go(mode, when)
        r = got["rsv"]
        assert r["book"] is True and r["now"] is False and r["date"] == _date(when), (mode, when, r, got["alert"])
        assert r["h"] == str(when.hour) and r["m"] == str(when.minute) and r["confirm"] == 0 and r["opened"] >= 1, r
        assert f"예약 {_lab(when)} 채움\n" in got["alert"] and "못 채움" not in got["alert"], got["alert"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_v14_failure_reverts_and_sends_the_new_nodes() -> None:
    """날을 눌러도 안 바뀌면 '현재'로 되돌림 · 진단에 날짜 칸(읽기 전용 INPUT) 이름 · B 새로 보인 portal (v15) — 글은 없음."""
    when = _days()[0]
    got = _go("mute", when)
    r = got["rsv"]
    assert r["now"] is True and r["book"] is False and r["confirm"] == 0 and r["h"] == "9", r
    assert f"예약 {_lab(when)} 못 채움 (날짜 칸 못 찾음)" in got["alert"], got["alert"]
    dg = got["report"]["dg"]
    assert dg.startswith(when.strftime("@%Y-%m-%dT%H:%M") + ",true," + _date(datetime.now())), dg
    # v15 (11/27 날짜 고르기가 돼서): 진단은 머리(날짜 칸 이름까지)와 B 새로 보인 것만 — A 세 겹 위 · C 이름 칸 60개는 뺌 (주소를 짧게)
    head, b = dg.split("|B", 1)
    assert "INPUT.input_date__Qd7Rt" in head and "|A" not in dg and "|C" not in dg, head
    assert b.startswith('<div id="dp_portal">') and "Choose" in b and len(b) <= 6000, b
    assert "첫 글이에요" not in dg and "끝 글이에요" not in dg


def test_v14_sizes_and_labels() -> None:
    assert kkumi.version() == 15 and autopub.version() == 11 and len(kkumi.bookmarklet()) < 31000
    assert len(autopub.bookmarklet("https://dash.example.com/dashboard/api/autopub_act", "k" * 43)) <= autopub.LEN_MAX


def test_v10_server_keeps_a_cleaned_diagnostic() -> None:
    """서버는 주소·style·svg d 를 지우고 빈칸을 하나로 · 9,000자까지."""
    h = '<svg d="M2 0h7"></svg><a href="https://x.example/p?q=1" style="color:red">~</a>  <img src="https://i.example/a.png"> https://evil.example/x ' + "x" * 10000
    got = autopub.DG_STRIP[1].sub("~", autopub.DG_STRIP[0].sub("", h))
    assert "https" not in got and "style=" not in got and ' d="' not in got and "href" not in got
