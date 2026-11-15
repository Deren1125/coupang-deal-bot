/* 오늘 자동 결과 (주인 11/15 14:1x — '책갈피 자동 한 바퀴 뒤 좋아요가 잘 눌렸는지·댓글이 잘 달렸는지 대시보드에서 검수').
   할 일 탭 블로그 칸의 '이웃 들르기' 줄 아래 접는 칸 — routine.js part() 가 SBSeoiRes.fold(블로그, res, sf) 를 부른다 (routine.js 는 예산 끝).
   res = routine.view().seoi.by[블로그].res (seoi.results — {n, bad, rows: [{who, what, like, cmt, res, bad, url, hm}]} · 문제 줄이 먼저 옴).
   서버를 부르지 않는다 · 글자는 모두 텍스트 노드 · '글 열기'는 m.blog.naver.com 주소만 (홈 화면 앱이면 routine.js 의 sf 가 사파리로). */
'use strict';
(() => {
const D = document, W = window, open = {}, only = {};
const CSS = '.sr{margin:0 0 10px}.sr .sf{display:flex;align-items:center;gap:8px;width:100%;min-height:44px;padding:8px 0;border:0;background:none;' +
  'text-align:left;color:var(--text);font-size:14px;font-weight:600}.sr .sf b{flex:1;min-width:0}.sr .sf em{font-style:normal;color:var(--red)}' +
  '.sr .sf span{font-size:13px;color:var(--ac-ink)}.sr .sw{overflow-x:auto;-webkit-overflow-scrolling:touch}' +
  '.sr table{width:100%;min-width:520px;border-collapse:collapse;font-size:13px;line-height:1.45}' +
  '.sr th,.sr td{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top}.sr th{font-weight:700;color:var(--sub);white-space:nowrap}' +
  '.sr td.c,.sr th.c{text-align:center;white-space:nowrap}.sr td.w{overflow-wrap:anywhere}.sr tr.bd td.r{color:var(--red);font-weight:600}' +
  '.sr td i{display:block;font-style:normal;font-size:12px;color:var(--sub)}.sr a{color:var(--ac-ink);white-space:nowrap}' +
  '.sr .so{margin:2px 0 8px;min-height:36px;padding:0 12px;font-size:13px}.sr .so[aria-pressed=true]{color:var(--ac-ink);font-weight:700}';
const URL_OK = /^https:\/\/m\.blog\.naver\.com\/[a-z0-9_-]{3,30}(\/\d{6,20})?$/;

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n.startsWith('on')) e.addEventListener(n.slice(2), v); else e.setAttribute(n, v === true ? '' : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const redraw = () => W.dispatchEvent(new Event('sb-routine'));
const s = v => (typeof v === 'string' ? v : '');

function line(r, sf) {
  const u = URL_OK.test(s(r.url)) ? r.url : '';
  return el('tr', {class: r.bad ? 'bd' : null},
    el('td', {class: 'w'}, s(r.who), r.hm ? el('i', {}, s(r.hm)) : null), el('td', {}, s(r.what)),
    el('td', {class: 'c'}, s(r.like)), el('td', {class: 'c'}, s(r.cmt)), el('td', {class: 'w r'}, s(r.res)),
    el('td', {}, u ? el('a', {href: sf ? sf(u) : u, target: '_blank', rel: 'noopener noreferrer'}, r.what === '들르기' ? '글 열기 ↗' : '블로그 ↗') : '–'));
}

// 블로그 칸에 넣을 접는 칸 (오늘 결과가 없으면 null)
function fold(k, res, sf) {
  const all = res && Array.isArray(res.rows) ? res.rows.filter(r => r && typeof r === 'object') : [];
  if (!all.length) return null;
  if (!D.getElementById('sr-css')) D.head.append(el('style', {id: 'sr-css'}, CSS));
  const bad = all.filter(r => r.bad), o = !!open[k], f = !!only[k] && bad.length > 0, rows = f ? bad : all;
  return el('div', {class: 'sr', 'data-k': k},
    el('button', {class: 'sf', type: 'button', 'aria-expanded': o ? 'true' : 'false', onclick: () => { open[k] = !o; redraw(); }},
      el('b', {}, `오늘 자동 결과 ${all.length}`, bad.length ? el('em', {}, ` · 문제 ${bad.length}`) : ' · 모두 정상'), el('span', {}, o ? '접기' : '열기')),
    !o ? null : [
      bad.length ? el('button', {class: 'sm so', type: 'button', 'aria-pressed': f ? 'true' : 'false', onclick: () => { only[k] = !f; redraw(); }},
        f ? `문제만 보는 중 (${bad.length}) — 모두 보기` : `문제만 보기 (${bad.length})`) : null,
      el('div', {class: 'sw'}, el('table', {},
        el('thead', {}, el('tr', {}, el('th', {}, '이웃'), el('th', {}, '할 일'), el('th', {class: 'c'}, '공감'), el('th', {class: 'c'}, '댓글'), el('th', {}, '결과'), el('th', {}, '열기'))),
        el('tbody', {}, rows.map(r => line(r, sf)))))]);
}
W.SBSeoiRes = {fold};
})();
