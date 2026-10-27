/* 로그인 관리 (주인 10/26 — "로그인이 필요한 사이트들은 다 원격으로 탭 열어서 로그인 … 너가 알림 주면 대시보드에서 바로 처리").
   연결 탭 맨 위 칸. 값은 GET /dashboard/api/logins (연결 탭을 열 때만 — 첫 상태 값 예산 밖, logins.py). 누르는 것은 login.remote {target} ·
   login.qr 뿐 (확인 창 없음 — 로그인은 주인이 원격 화면·네이버 앱에서 직접). app.js 는 예산 끝이라 이 칸을 넣지 않는다: 셸이 defer 로 받고,
   app.js 가 #main 을 다시 그릴 때마다 같은 칸(상태 유지)을 맨 위에 다시 붙인다. 라이브러리 없음 · 글은 textContent 로만 · 바깥 주소 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '#lgp .row .s{white-space:normal;overflow-wrap:anywhere}#lgp .w{display:block;font-size:12px;color:var(--sub);margin-top:2px}' +
  '#lgp .sm[disabled]{opacity:.45}#lgp .qr{display:grid;gap:8px;justify-items:center;padding:10px 0;border-top:1px solid var(--line)}' +
  '#lgp .qr img{width:100%;max-width:520px;height:auto;border-radius:8px;border:1px solid var(--line);background:#fff}' +
  '#lgp .qr p{margin:0;font-size:13px;text-align:center}#lgp .vn{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}' +
  '#lgp .vn span{flex:1;min-width:0;font-size:14px}#lgp .ro .t{color:var(--sub)}';
let box = null, data = null, at = 0, timer = null, busy = false, tt = null;

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
const on = () => /^#\/connect(\/|$)/.test(location.hash);
const link = (cls, label, url) => el('a', {class: cls, href: url, target: '_blank', rel: 'noopener noreferrer'}, label);
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text}));
  t.hidden = false;
  clearTimeout(tt);
  tt = setTimeout(() => { t.hidden = true; }, 5000);
}
function csrf() {
  try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; }
}
const rid = () => 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join('');

async function load() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch('/dashboard/api/logins', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) { data = await r.json(); at = Date.now(); } else if (r.status === 401) toast('다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요');
  } catch (e) { /* 다음에 다시 */ }
  busy = false;
  draw();
  clearTimeout(timer);
  // QR·원격 화면이 떠 있는 동안만 자주 (로그인되면 저절로 바뀐 줄을 보이게) — 그 밖에는 탭을 다시 열 때만
  if (on() && data && (data.qr || data.vnc) && D.visibilityState === 'visible') timer = setTimeout(load, 4000);
}

async function press(b, e) {
  const btn = e.currentTarget;
  btn.disabled = true;
  btn.prepend(el('span', {class: 'spin'}));
  let r = null, d = {};
  try {
    r = await fetch('/dashboard/api/action', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()},
      body: JSON.stringify({action: b.action, args: b.args || {}, cid: rid(), ctoken: null})});
    d = await r.json().catch(() => ({}));
  } catch (x) { r = null; }
  if (D.contains(btn)) { btn.disabled = false; const s = btn.querySelector('.spin'); if (s) s.remove(); }
  if (!r) toast('인터넷 연결을 확인해 주세요');
  else if (r.status === 401) toast('다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요');
  else if (r.status === 403 && d.code === 'csrf') toast('화면이 오래됐어요 — 새로고침해 주세요');
  else toast(d.text || (d.status === 'ok' ? '했어요' : '하지 못했어요 — 잠시 뒤 다시'));
  const rf = D.getElementById('rf');
  if (r && r.status === 200 && rf) rf.click(); else load();  // 새로고침: 할 일 맨 위 '원격 화면 열기'·'QR 보기' 줄과 이 칸을 같이
}

function right(b, hot) {
  if (!b) return null;
  if (b.url) return link('sm ac', b.label, b.url);
  return el('button', {class: 'sm' + (hot && !b.disabled ? ' ac' : ''), type: 'button', disabled: !!b.disabled,
    title: b.disabled ? '다른 원격 화면이 열려 있어요 — 닫힌 뒤에' : null, onclick: e => press(b, e)}, b.label);
}

function row(r) {
  return el('div', {class: 'row' + (r.button ? '' : ' ro'), 'data-id': r.id},
    el('div', {class: 'grow'}, el('span', {class: 't'}, r.label), el('span', {class: 's' + (r.bad ? ' bad' : '')}, r.text),
      r.where ? el('span', {class: 'w'}, r.where) : null),
    right(r.button, r.bad));
}

function draw() {
  if (!box) return;
  const rows = (data && data.rows) || [], q = data && data.qr, v = data && data.vnc;
  const head = el('div', {class: 'top', style: 'margin:20px 0 6px'},
    el('h2', {}, data && data.need ? `로그인 관리 · 필요 ${data.need}` : '로그인 관리'),
    el('button', {class: 'lnk', type: 'button', onclick: load}, '다시 보기'));
  const top = [];
  if (v) top.push(el('div', {class: 'vn'}, el('span', {}, `원격 화면 — ${v.label} (${v.left_min}분 남음)`), link('sm ac', '원격 화면 열기', v.url)));
  if (q) {
    top.push(el('div', {class: 'qr'}, el('img', {src: q.img, alt: '네이버 QR 로그인 화면'}),
      el('p', {text: `${q.how} · ${q.left_s}초 남음`}), link('lnk', 'QR 크게 보기', q.img)));
  }
  const nv = data && data.visits;  // 네이버 접속 (주인 10/27 — 블로그마다 오늘·24시간·상한)
  const visits = nv ? [el('div', {class: 'top', style: 'margin:20px 0 6px'}, el('h2', {}, '네이버 접속')),
    el('div', {class: 'box'}, nv.rows.map(r => el('div', {class: 'row ro'}, el('div', {class: 'grow'}, el('span', {class: 't'}, r.name),
      el('span', {class: 's' + (r.bad ? ' bad' : '')}, r.text), r.jobs ? el('span', {class: 'w'}, r.jobs) : null)))),
    el('p', {class: 'note'}, `${nv.total} · 적응 기간 = 로그인이 돌아온 뒤 3일, 상한을 낮추고 밀린 글을 천천히`)] : [];
  box.replaceChildren(head, el('div', {class: 'box'}, data ? null : el('p', {class: 'note'}, '불러오는 중…'), top, rows.map(row)),
    el('p', {class: 'note'}, '로그인은 주인이 직접 해요 — 봇은 비밀번호를 넣거나 확인 화면을 대신 누르지 않고, 정해진 때 로그인 화면을 열지 않아요'),
    visits);
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!on()) { clearTimeout(timer); return; }
  if (!box) {
    if (!D.getElementById('lg-css')) D.head.append(el('style', {id: 'lg-css', text: CSS}));
    box = el('section', {id: 'lgp', 'aria-label': '로그인 관리'});
    draw();
  }
  if (m.firstChild !== box) m.prepend(box);  // app.js 가 연결 탭을 다시 그리면 칸이 빠짐 → 같은 칸을 다시 (그 뒤 바뀜은 이 조건으로 멈춤)
  if (!data || Date.now() - at > 30000) load();
}

function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', mount);
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && on()) load(); });
  const rf = D.getElementById('rf');
  if (rf) rf.addEventListener('click', () => { if (on() && !busy) setTimeout(load, 300); });
  mount();
}
W.SBLogins = {load, mount};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
