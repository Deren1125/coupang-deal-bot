/* 로그인 관리 (주인 10/26). 연결 탭 맨 위 칸 · 값은 GET /dashboard/api/logins (logins.py). 누르는 것은 login.remote
   {target, force?} · login.remote_cancel (열린 원격 화면 취소 — 10/29) · login.qr 뿐. app.js 가 #main 을 다시 그리면 같은 칸을 다시 붙인다. 주인 몫이 없으면 한 줄로 접음 (10/27).
   글은 textContent 로만 · 바깥 주소 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '#lgp .row{flex-wrap:wrap;row-gap:8px}#lgp .grow{flex-basis:60%}#lgp .row .s{white-space:normal;overflow-wrap:anywhere}#lgp .w{display:block;font-size:12px;color:var(--sub);margin-top:2px}' +
  '#lgp .sm[disabled]{opacity:.45}#lgp .qr{display:grid;gap:8px;justify-items:center;padding:10px 0;border-top:1px solid var(--line)}' +
  '#lgp .qr img{width:100%;max-width:520px;height:auto;border-radius:8px;border:1px solid var(--line);background:#fff}' +
  '#lgp .qr p{margin:0;font-size:13px;text-align:center}#lgp .vn{display:flex;gap:10px;align-items:center;padding:8px 0;border-top:1px solid var(--line)}' +
  '#lgp .vn span{flex:1;min-width:0;font-size:14px}#lgp .ro .t{color:var(--sub)}#lgp .kk ol{margin:0;padding:0 0 10px 20px;font-size:14px;line-height:1.6}' +
  '#lgp .kk .ac{width:100%;min-height:48px;margin-bottom:12px;justify-content:center}';
let box = null, data = null, at = 0, timer = null, busy = false, tt = null, open = false, kk = null, kd = null, ko = false;

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
      [r.where, r.last].map(x => x ? el('span', {class: 'w'}, x) : null)),
    right(r.button, r.bad));
}

// '꾸미기 버튼 설치' (10/27 — 붙여 넣은 글의 링크 카드를 만드는 사파리 책갈피 · 코드는 펼칠 때 /dashboard/api/kkumi 로)
function kkDraw() {
  const n = W.navigator, c = t => (n && n.clipboard ? n.clipboard.writeText(t) : Promise.reject(t))
    .then(() => toast('복사했어요 — 책갈피 주소 칸에 붙여 넣어요'), () => toast('복사하지 못했어요 — 다시 눌러 주세요'));
  kk.replaceChildren(el('button', {class: 'fold kf', type: 'button', 'aria-expanded': ko ? 'true' : 'false', onclick: () => {
    ko = !ko; if (ko && !kd) kkLoad(); kkDraw(); }}, '꾸미기 버튼 설치 (링크 카드)'), ...(!ko ? [] : kd ? [el('ol', {}, kd.steps.map(x => el('li', {text: x}))),
    el('button', {class: 'sm ac', type: 'button', onclick: () => c(kd.code)}, '코드 복사')] : [el('p', {class: 'note'}, '불러오는 중…')]));
}
async function kkLoad() {
  try { const r = await fetch('/dashboard/api/kkumi', {credentials: 'same-origin', headers: {Accept: 'application/json'}}); if (r.status === 200) kd = await r.json(); } catch (e) { /* 다시 펼치면 */ }
  if (!kd) { ko = false; toast('불러오지 못했어요 — 잠시 뒤 다시'); }
  kkDraw();
}

function draw() {
  if (!box) return;
  if (!kk) { kk = el('div', {class: 'box kk', style: 'padding:0 14px'}); kkDraw(); }
  const rows = (data && data.rows) || [], q = data && data.qr, v = data && data.vnc;
  // 주인 몫이 없으면 한 줄 (누르면 펼침) · 필요하면 늘 펼침
  const calm = data && !data.need && !q && !v, f = calm && el('div', {class: 'box', style: 'padding:0 14px'}, el('button', {class: 'fold', type: 'button',
    'aria-expanded': open ? 'true' : 'false', onclick: () => { open = !open; draw(); }}, rows.every(r => r.state !== 'resting' && r.state !== 'unknown') ? '로그인 모두 정상' : '로그인 · 지금 할 것 없음'));
  if (calm && !open) { box.replaceChildren(f, kk); return; }
  const head = f || el('div', {class: 'top', style: 'margin:20px 0 6px'}, el('h2', {}, data && data.need ? `로그인 관리 · 필요 ${data.need}` : '로그인 관리'),
    el('button', {class: 'lnk', type: 'button', onclick: load}, '다시 보기'));
  const top = [];
  if (v) top.push(el('div', {class: 'vn'}, el('span', {}, `원격 화면 — ${v.label} (${v.left_min}분 남음)`), link('sm ac', '원격 화면 열기', v.url),
    v.cancel ? el('button', {class: 'sm', type: 'button', onclick: e => press({action: 'login.remote_cancel'}, e)}, '취소') : null));
  if (q) {
    top.push(el('div', {class: 'qr'}, el('img', {src: q.img, alt: '네이버 QR 로그인 화면'}),
      el('p', {text: `${q.how} · ${q.left_s}초 남음`}), link('lnk', 'QR 크게 보기', q.img)));
  }
  const nv = data && data.visits;  // 네이버 접속 (10/27)
  const visits = nv ? [el('div', {class: 'top', style: 'margin:20px 0 6px'}, el('h2', {}, '네이버 접속')),
    el('div', {class: 'box'}, nv.rows.map(r => el('div', {class: 'row ro'}, el('div', {class: 'grow'}, el('span', {class: 't'}, r.name),
      el('span', {class: 's' + (r.bad ? ' bad' : '')}, r.text), r.jobs ? el('span', {class: 'w'}, r.jobs) : null)))),
    el('p', {class: 'note'}, `${nv.total} · 적응 기간 = 다시 로그인한 뒤 3일`)] : [];
  box.replaceChildren(head, el('div', {class: 'box'}, data ? null : el('p', {class: 'note'}, '불러오는 중…'), top, rows.map(row)),
    el('p', {class: 'note'}, '로그인은 주인이 직접 해요 — 봇은 비밀번호·확인 화면을 대신하지 않아요'),
    ...visits, kk);  // visits 는 배열 — 펼쳐 넣음 (replaceChildren 은 배열을 펴지 않음)
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
