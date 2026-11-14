/* 할 일 종류 (주인 11/14 '할 일 첫 화면은 종류별 목록만 — 누르면 그 종류 할 일만'). 셸이 defer 로 받음 (app.js 는 예산 끝).
   #/todo = 종류 목록 (폰: 세로 줄 · 600px 넘는 화면: 블로그 탭처럼 가로 단추) · #/todo/<종류> = 그 종류 할 일만 + '‹ 할 일'.
   칸은 app.js 가 routine.js part() 로 종류마다 하나씩 그림 (data-k) — 여기선 고른 종류만 보이게 #main 에 data-hub 를 달 뿐 (다시 그리지 않음).
   수는 SBRoutine.cnt (남은 일·급함) · 이벤트는 SBEvent.n() (#/todo/ev 화면은 ssalmuk.js). 마지막에 본 종류는 localStorage.
   종류 목록·종류 화면 맨 위 알림 줄 (주인 11/14 '오류는 주인이 보기 전에 · 멈춘 걸 몰라서 몇 시간') — SBRoutine.meta():
     블로그 프로그램 소식이 30분 넘게 없거나 오늘 목록이 아니면 한 줄 · 자동 점검 한 줄 · 꾸미기 책갈피 새 판(블로그 셋·목록) ·
     못 전한 체크 수 · 멈춤·핫 시스템 알림(sys-*)은 목록에서도 보이게 (누르면 기타). 줄 순서는 늘 CATS 그대로 (없는 종류는 흐리게만). */
'use strict';
(() => {
const D = document, W = window;
// [주소, 이름, part() 칸 이름] — 블로그 셋 → 딜 → SNS → 숏폼 → 기타 → 이벤트 (주인 11/14 '블로그가 맨 위 · 이벤트 참여는 맨 아래' — 늘 이 순서)
const CATS = [['issue', '데런픽', 'issue'], ['finance', '드라마', 'finance'], ['hotdeal', '라커룸', 'hotdeal'], ['deals', '딜', 'deals'],
  ['sns', 'SNS', 'sns'], ['shorts', '숏폼', 'shorts'], ['etc', '기타', ''], ['ev', '이벤트', null]];
const BLOG3 = ['issue', 'finance', 'hotdeal'], STALE_MIN = 30;
const KEYS = CATS.map(c => c[0]);
const DOT = {issue: '#F2B400', finance: '#3B82F6', hotdeal: '#EF4444', deals: '#F97316', sns: '#10B981', shorts: '#A855F7', ev: '#0EA5E9', etc: '#94A3B8'};
const only = CATS.filter(c => c[2]).map(c => `main[data-hub="${c[0]}"]>:not(#tdh):not(.bad):not([data-k="${c[2]}"])`);
const CSS = only.concat('main[data-hub=hub]>:not(#tdh):not(.bad)', 'main[data-hub=etc]>[data-k]:not([data-k=""])', 'main[data-hub=etc]>#evc',
  'main[data-hub] .bk .bh', 'main[data-hub] #evs>.top>a.lnk:first-child', '#tdh .sg2').join(',') + '{display:none!important}' +
  '#tdh{margin:0 0 12px}#tdh .tl{padding:0 14px}#tdh .row2{display:flex;align-items:center;gap:12px;width:100%;min-height:60px;padding:10px 0;' +
  'border:0;border-top:1px solid var(--line);background:none;text-align:left;color:var(--text);font:inherit}#tdh .row2:first-child{border-top:0}' +
  '#tdh .dt{flex:none;width:12px;height:12px;border-radius:50%}#tdh .nm{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}' +
  '#tdh .nm b{font-size:17px;line-height:1.3}#tdh .nm span{font-size:13px;color:var(--sub)}#tdh .z{opacity:.45}' +
  '#tdh .rd{display:inline-block;width:8px;height:8px;margin-left:6px;border-radius:50%;background:var(--red);vertical-align:3px}' +
  '#tdh .rc{font-style:normal;font-size:12px;font-weight:600;color:var(--ac-ink);margin-left:6px}#tdh .cv2{flex:none;font-size:20px;color:var(--muted)}' +
  '#tdh .row2 .cp{margin:0}#tdh .bk2{display:flex;align-items:center;gap:8px;min-height:44px;margin:0 0 8px}#tdh .bk2 b{font-size:17px}' +
  '#tdh .bk2 .lnk{padding-left:0}#tdh .bk2 span.s{margin-left:auto;font-size:13px;color:var(--sub)}#tdh .em{margin:8px 0 0}main[data-hub] .bk{padding-top:14px}' +
  '#tdh .hn{display:block;margin:0 0 8px;padding:10px 12px;border-radius:12px;font-size:14px;line-height:1.45;text-decoration:none;color:var(--text);background:var(--line);text-align:left;border:0;width:100%;font-family:inherit}' +
  '#tdh .hn.w{background:var(--red-soft);color:var(--red)}#tdh .hn.g{background:none;padding:0 2px;color:var(--sub);font-size:13px}' +
  '@media (min-width:600px){#tdh .tl{display:none}#tdh .sg2{display:flex!important;overflow-x:auto;gap:3px;scrollbar-width:none}' +
  '#tdh .sg2 button{flex:1 0 auto;padding:0 6px;white-space:nowrap}#tdh .sg2 .cp{margin-left:3px}#tdh .sg2 .rd{vertical-align:2px}}';
let box = null, from = '', tt = null;

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v;
    else if (n.startsWith('on')) e.addEventListener(n.slice(2), v); else e.setAttribute(n, v === true ? '' : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const LS = {g() { try { return W.localStorage.getItem('todo-cat') || ''; } catch (e) { return ''; } },
  s(v) { try { W.localStorage.setItem('todo-cat', v); } catch (e) { /* 개인 창 — 이번만 */ } }};
// 지금 화면: 'hub' · 종류 · null(다른 탭 · 딜만 보기 #/todo/deal · 시간대별)
function page() {
  const h = location.hash, m = /^#\/?todo\/([^/]*)\/?$/.exec(h);
  if (!(W.SBRoutine && W.SBRoutine.by && W.SBRoutine.by())) return null;
  if (m && m[1] === 'deal') return null;
  if (m && KEYS.includes(m[1])) return m[1];
  return m || /^(#\/?(todo\/?)?)?$/.test(h) ? 'hub' : null;
}
// 종류마다 [남은 수, 급한 수, 한 줄]
function count(c) {
  if (c[0] === 'ev') {
    const e = W.SBEvent && W.SBEvent.n && W.SBEvent.n();
    return e ? [e.n, e.u, e.n ? `확인 필요 ${e.n}` : '할 일 없음'] : [0, 0, '연결 전'];
  }
  const r = (W.SBRoutine && W.SBRoutine.cnt) || {}, x = r[c[2]] || {n: 0, m: 0, u: 0}, n = x.n + (c[0] === 'etc' ? r.ab || 0 : 0);
  return [n, x.u, (n ? `남은 일 ${n}` + (x.m ? ` · 약 ${x.m}분` : '') : x.w ? `만드는 중 ${x.w}` : '할 일 없음') + (x.v ? ` · 들르기 ${x.v}` : '')];  // v = 오늘 이웃 들르기 (책갈피 '이웃 관리' — routine.js)
}
// 맨 위 알림 줄 (p = 'hub' 또는 종류)
function notes(p) {
  const r = W.SBRoutine, t = r && r.meta && r.meta(), out = [], m = D.getElementById('main');
  if (p === 'hub' && m) for (const a of m.querySelectorAll('[data-k] .itm[data-iid^="sys-"]')) {  // 멈춤·핫 시스템 알림 — 기타 칸에만 있던 것
    const c = a.querySelector('.ih .cp.stop,.ih .cp.hot'), x = a.querySelector('.ih .ti'), s = a.querySelector('.st');
    if (c && x) out.push(el('button', {class: 'hn w sys', type: 'button', 'data-iid': a.getAttribute('data-iid'), onclick: () => go('etc')},
      [c.textContent, x.textContent, s && s.textContent].filter(Boolean).join(' · ') + ' ›'));
  }
  if (!t) return out;
  const a = t.age_min;
  if (a > STALE_MIN) out.push(el('p', {class: 'hn w stl'}, `블로그 프로그램 소식이 ${a < 120 ? a + '분' : Math.floor(a / 60) + '시간'}째 없어요 — 목록이 오래됐을 수 있어요`));
  else if (!t.today) out.push(el('p', {class: 'hn w stl'}, t.day ? '오늘 목록이 아직 안 왔어요 — 보이는 건 지난 목록이에요' : '블로그 프로그램 목록이 아직 안 왔어요'));
  if (t.relay) out.push(el('p', {class: 'hn rl'}, `체크 ${t.relay}개는 블로그 프로그램이 깨어나면 전해요`));
  if (t.kkumi_new && (p === 'hub' || BLOG3.includes(p))) out.push(el('a', {class: 'hn kn', href: '#/connect'}, '꾸미기 책갈피가 바뀌었어요 — 다시 설치 (1분) ›'));
  if (t.gate && (p === 'hub' || BLOG3.includes(p))) out.push(el('p', {class: 'hn g gt'}, t.gate.line));
  return out;
}
function go(k) {
  LS.s(k);
  if (page() === 'hub') { from = k; location.hash = '#/todo/' + k; } else { from = from && k; location.replace('#/todo/' + k); }  // 종류끼리는 바꿔 끼움 — 뒤로 가기는 늘 목록으로
}
function back() {
  if (from && from === page()) { from = ''; W.history.back(); } else location.hash = '#/todo';  // 목록에서 왔으면 뒤로 (밀어서 뒤로와 같게)
}
const badge = (n, u) => n ? el('span', {class: 'cp ' + (u ? 'stop' : 'hot'), 'aria-label': `할 일 ${n}개` + (u ? ` · 급한 것 ${u}개` : '')}, n) : null;

function draw() {
  if (!box) return;
  const p = page();
  if (!p) return;
  const xs = CATS.map(c => [c, ...count(c)]), last = LS.g(), sg = el('div', {class: 'seg sg2', role: 'group', 'aria-label': '할 일 종류'},
    xs.map(([c, n, u]) => el('button', {class: n ? '' : 'z', type: 'button', 'aria-pressed': c[0] === p ? 'true' : 'false', onclick: () => go(c[0])},
      c[1], u ? el('i', {class: 'rd', 'aria-hidden': 'true'}) : null, badge(n, u))));
  if (p === 'hub') {
    const row = ([c, n, u, s]) => el('button', {class: 'row2' + (n ? '' : ' z'), type: 'button', 'data-cat': c[0], onclick: () => go(c[0])},
      el('span', {class: 'dt', style: `background:${DOT[c[0]]}`}),
      el('span', {class: 'nm'}, el('b', {}, c[1], u ? el('i', {class: 'rd', 'aria-label': '급한 것 있음'}) : null, c[0] === last ? el('em', {class: 'rc'}, '최근') : null),
        el('span', {}, s)), badge(n, u), el('span', {class: 'cv2', 'aria-hidden': 'true'}, '›'));
    // 폰: 세로 줄 (늘 CATS 순서 — 블로그 셋이 맨 위 · 없는 종류는 흐리게만, 자리는 그대로 · 주인 11/14) · 넓은 화면: 가로 단추
    box.replaceChildren(...notes(p), el('div', {class: 'box tl'}, xs.map(row)), sg,
      el('p', {class: 'note em sg2'}, xs.some(x => x[1]) ? '종류를 누르면 그 할 일만 보여요' : '오늘 할 일 없어요 ✓'));
    return;
  }
  const c = CATS.find(x => x[0] === p), [n, u, t] = count(c), m = D.getElementById('main');
  const empty = c[2] != null && c[0] !== 'etc' && m && ![...m.children].some(x => x.getAttribute && x.getAttribute('data-k') === c[2]);
  box.replaceChildren(el('div', {class: 'bk2'}, el('button', {class: 'lnk', type: 'button', onclick: back}, '‹ 할 일'), el('b', {}, c[1]),
    badge(n, u), c[0] === 'ev' ? null : el('span', {class: 's'}, t)), sg, ...notes(p), empty ? el('p', {class: 'empty em'}, '할 일 없어요 ✓') : '');
}

// #main 을 app.js 가 다시 그리면 맨 위에 다시 붙임 (routine.js·ssalmuk.js 는 이 칸 다음에 붙음)
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  const p = page();
  if (location.hash && /^#\/?todo\/[^/]+\/?$/.test(location.hash) && p === 'hub') {  // 모르는 종류 → 목록
    W.history.replaceState(null, '', '#/todo');
  }
  if (!p) {
    if (m.hasAttribute('data-hub')) m.removeAttribute('data-hub');
    if (box && box.parentNode) box.remove();
    return;
  }
  if (!D.getElementById('th-css')) D.head.append(el('style', {id: 'th-css', text: CSS}));
  if (!box) box = el('section', {id: 'tdh', 'aria-label': '할 일 종류'});
  if (m.getAttribute('data-hub') !== p) m.setAttribute('data-hub', p);
  if (m.firstChild !== box) m.prepend(box);
  clearTimeout(tt);
  tt = setTimeout(draw, 0);  // 같은 틱에 여러 번 불려도 한 번만 그림 (routine·ssalmuk 칸이 다 붙은 뒤)
  draw();
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', mount);
  W.addEventListener('sb-hub', () => box && box.parentNode && draw());
  mount();
}
W.SBHub = {page, mount, draw, CATS};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
