/* Sns-board 일정 화면 (주인 10/21 — "발행 예약한 글들 … 세 블로그 통합으로 볼 수 있는 페이지"): 아카이브 · Pick · 핫딜의 앞으로 일정을 시간순으로.
   state.sched (서버 schedview.py) = 맨 위 한 줄 + 블로그 칩 + 날짜 칸(오늘 · 내일 · 날짜(요일)) → 줄: 시각 · 블로그 · 제목 · 상태 · 사진 · 시간 바꾸기 / 바로 발행 / 빼기.
   누르는 것은 글 줄에 이미 있는 행동 그대로 (확인 창도 app.js exec 가 그대로). 멈춘 글(시간 없음)은 마지막 칸. 처음 열 때만 app.js 가 불러온다. 라이브러리 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.sf{margin:0 0 4px}.sf button{font-size:14px;padding:0 2px}.sf .n{color:var(--muted);font-weight:500;margin-left:4px}' +
  '.sr{display:grid;grid-template-columns:54px minmax(0,1fr) 48px;gap:2px 10px;align-items:start}.sr .tm{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums;line-height:24px}' +
  '.sr .tm.z{font-size:14px;color:var(--red)}.sr .sx{min-width:0}.sr .ln{display:flex;align-items:center;gap:6px;min-width:0}.sr .ln .st{margin:0;flex:1}' +
  '.sr .ti{width:100%;min-width:0;border:0;background:none;padding:2px 0;text-align:left;font:inherit;font-weight:600;font-size:15px;color:var(--text);' +
  'overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;word-break:keep-all}' +
  '.sr .ph{width:48px;height:48px;border-radius:8px;object-fit:cover;background:var(--grey)}.sr .va{grid-column:1/-1;margin-top:6px}' +
  '.sr.go{opacity:.55}.dh{display:flex;align-items:baseline;gap:8px}.dh span{font-size:12px;font-weight:500;color:var(--muted)}';
let F = 'all';  // 고른 블로그 칩 (다시 그려도 남게)

function css() {
  if (D.getElementById('sc-css')) return;
  const s = D.createElement('style'); s.id = 'sc-css'; s.textContent = CSS; D.head.append(s);
}
function row(r, h) {
  const {el, btn} = h, busy = h.pend(r.iid), b = (a, label, cls) => a ? btn('sm' + (cls || ''), label, e => h.act(r, a, e.currentTarget)) : null;
  return el('article', {class: 'itm sr' + (busy ? ' go' : '')},
    el('span', {class: 'tm' + (r.time ? '' : ' z')}, r.time || '멈춤'),
    el('div', {class: 'sx'}, el('div', {class: 'ln'}, el('span', {class: 'cp ' + (r.state === 'blocked' ? 'stop' : 'info')}, r.chip),
      el('p', {class: 'st'}, busy ? '하는 중이에요' : r.status)), btn('ti', r.title, () => h.open(r.iid))),
    r.thumb ? el('img', {class: 'ph', src: r.thumb, alt: '', loading: 'lazy'}) : el('span'),
    busy ? null : el('div', {class: 'va'}, b(r.move, '시간 바꾸기'), b(r.now, '바로 발행'), b(r.remove, '빼기')));
}
function view(s, h) {
  css();
  const {el, btn} = h;
  if (!s) return [el('p', {class: 'empty'}, '일정을 아직 못 받았어요')];
  if (F !== 'all' && !s.blogs.some(b => b.key === F)) F = 'all';
  const chips = [{key: 'all', label: '전체', n: s.blogs.reduce((a, b) => a + b.n, 0)}, ...s.blogs];
  const out = [el('div', {class: 'top'}, el('h2', {}, '일정'), el('a', {class: 'lnk', href: '#/blog'}, '블로그로')),
    el('p', {class: 'note', style: 'margin:0 0 10px'}, s.text),
    el('div', {class: 'seg sf', role: 'group', 'aria-label': '블로그'}, chips.map(c => btn('', [c.label, el('span', {class: 'n'}, c.n)],
      () => { F = c.key; h.render(); }, {'aria-pressed': c.key === F ? 'true' : 'false'})))];
  const days = s.days.map(d => ({d, xs: d.items.filter(r => F === 'all' || r.blog === F)})).filter(x => x.xs.length);
  if (!days.length) return out.concat(el('p', {class: 'empty'}, F === 'all' ? '앞으로 예약된 글이 없어요' : '이 블로그는 앞으로 예약된 글이 없어요'),
    el('p', {class: 'note', style: 'text-align:center'}, '블로그 탭 ‘기다리는 글’에서 간격 두고 발행을 누르면 여기에 시간순으로 보여요'));
  for (const {d, xs} of days) out.push(el('p', {class: 'h3 dh'}, d.label, el('span', {}, `${xs.length}편`)), ...xs.map(r => row(r, h)));
  return out;
}
W.SBSched = {view};
})();
