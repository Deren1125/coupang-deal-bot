/* 계정 정리 (구글·인스타) (주인 11/15 — '계정별 사용처를 정리해서 대시보드에 간단한 페이지로').
   연결 탭의 접는 칸 — 값은 GET /dashboard/api/accounts (accounts_map.py 의 상수 · 쓰임만, 주소·비밀 없음). 읽기 전용 · 누르는 것은 펼치기뿐.
   바로 가기 #/connect/accounts 로 열면 펼치고 그 칸으로 내려간다. app.js 가 #main 을 다시 그리면 같은 칸을 다시 붙인다 (logins.js 칸 바로 아래).
   글자는 모두 텍스트 노드 · 바깥 주소 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '#acm{margin:12px 0;scroll-margin-top:64px}#acm .sw{overflow-x:auto;-webkit-overflow-scrolling:touch}' +
  '#acm table{width:100%;min-width:560px;border-collapse:collapse;font-size:13px;line-height:1.5}' +
  '#acm th,#acm td{padding:8px 6px;border-top:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}' +
  '#acm th{font-weight:700;color:var(--sub);white-space:nowrap}#acm td:first-child{font-weight:600;min-width:120px}' +
  '#acm tr.on td:last-child{color:var(--ac-ink);font-weight:600}#acm tr.todo td:last-child{color:var(--or);font-weight:600}' +
  '#acm ol{margin:10px 0 0;padding:0 0 0 20px;font-size:14px;line-height:1.6}#acm .note{margin:8px 0 12px}';
let box = null, data = null, busy = false, open = false, failed = false, jumped = '';

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
const on = () => /^#\/connect(\/|$)/.test(location.hash);
const direct = () => /^#\/connect\/accounts(\/|$)/.test(location.hash);
const s = v => (typeof v === 'string' ? v : '');
const list = v => (Array.isArray(v) ? v : []);

async function load() {
  if (busy || data) return;
  busy = true;
  failed = false;
  try {
    const r = await fetch('/dashboard/api/accounts', {credentials: 'same-origin', headers: {Accept: 'application/json'}});
    if (r.status === 200) data = await r.json(); else failed = true;
  } catch (e) { failed = true; }
  busy = false;
  draw();
}

function body() {
  if (!data) return el('p', {class: 'note'}, failed ? '불러오지 못했어요 — 접었다 다시 펼쳐 주세요' : '불러오는 중…');
  const rows = list(data.rows).filter(r => r && typeof r === 'object');
  return [
    el('div', {class: 'sw'}, el('table', {},
      el('thead', {}, el('tr', {}, list(data.cols).slice(0, 4).map(c => el('th', {}, s(c))))),
      el('tbody', {}, rows.map(r => el('tr', {class: /^(on|todo|wait)$/.test(s(r.state)) ? r.state : null},
        el('td', {}, s(r.name)), el('td', {}, s(r.use)), el('td', {}, s(r.who)), el('td', {}, s(r.status))))))),
    el('ol', {}, list(data.rules).map(x => el('li', {}, s(x)))),
    el('p', {class: 'note'}, s(data.source))];
}

function draw() {
  if (!box) return;
  box.replaceChildren(el('div', {class: 'box', style: 'padding:0 14px'},
    el('button', {class: 'fold', type: 'button', 'aria-expanded': open ? 'true' : 'false', onclick: () => { open = !open; if (open) load(); draw(); }},
      '계정 정리 (구글·인스타)'),
    ...(open ? [body()].flat(9) : [])));
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!on()) { jumped = ''; return; }
  if (!box) {
    if (!D.getElementById('acm-css')) D.head.append(el('style', {id: 'acm-css'}, CSS));
    box = el('section', {id: 'acm', 'aria-label': '계정 정리'});
    draw();
  }
  if (box.parentNode !== m) {  // app.js 가 연결 탭을 다시 그리면 칸이 빠짐 → 로그인 관리 칸 바로 아래에 다시 (그 뒤 바뀜은 이 조건으로 멈춤)
    const lg = D.getElementById('lgp');
    if (lg && lg.parentNode === m) lg.after(box); else m.prepend(box);
  }
  if (direct() && jumped !== location.hash) {  // 바로 가기: 펼치고 그 칸으로 (같은 주소에서는 한 번만 — 주인이 접으면 그대로)
    jumped = location.hash;
    if (!open) { open = true; draw(); }
    load();
    setTimeout(() => { if (box.scrollIntoView) box.scrollIntoView({block: 'start'}); }, 60);
  } else if (!direct()) jumped = '';
}

function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', mount);
  mount();
}
W.SBAccounts = {mount};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
