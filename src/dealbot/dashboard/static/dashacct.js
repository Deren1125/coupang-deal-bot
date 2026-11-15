/* 대시보드 로그인 (주인 11/16 — '로그인한 다음에 아이디·비번 볼 수 있는 기능, 거기서 수정도').
   연결 탭의 접는 칸 (바로 가기 #/connect/login). 값은 dashacct.py:
   - 펼치면 GET /dashboard/api/dashacct (아이디만). 비밀번호는 ●●● — '보기'를 누르면 POST …/reveal 로 받아 10초 보였다 다시 가림 (변수에만).
   - '바꾸기': 새 아이디(비우면 그대로)·새 비밀번호 두 번 → 규칙 확인 → 새 값을 한 번 더 보여 주고 '이대로 저장' 을 눌러야 POST …/save.
   글자는 모두 텍스트 노드 · 바깥 주소 없음. #main 을 다시 그리면 다시 붙인다. 화면을 떠나면 보인 값·확인 화면을 가림. */
'use strict';
(() => {
const D = document, W = window;
const API = '/dashboard/api/dashacct', DOTS = '●●●●●●●●', PW_MIN = 12, PW_MAX = 128;
const PW_RE = /^[A-Za-z0-9._-]+$/, USER_RE = /^[A-Za-z0-9._@-]{1,64}$/;
const CSS = '#dac{margin:12px 0;scroll-margin-top:64px}#dac .row{flex-wrap:wrap;row-gap:6px}#dac .v{font-weight:600;overflow-wrap:anywhere;user-select:all}' +
  '#dac label{display:block;font-size:13px;font-weight:600;color:var(--sub);margin:10px 0 0}' +
  '#dac input{display:block;width:100%;margin:4px 0 0;font-size:16px;font-weight:400;padding:10px 12px;min-height:44px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text)}' +
  '#dac .bt{display:flex;gap:8px;margin:12px 0}#dac .chk{background:var(--tile);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:10px 0 0}' +
  '#dac .bad{color:var(--red)}#dac .note{margin:8px 0 12px}';
let box = null, data = null, busy = false, open = false, failed = false, jumped = '';
let mode = 'view', shown = '', left = 0, timer = 0, msg = '', msgBad = false;
let nu = '', np = '', np2 = '';

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
const direct = () => /^#\/connect\/login(\/|$)/.test(location.hash);
const s = v => (typeof v === 'string' ? v : '');
const btn = (cls, label, f, off) => el('button', {class: cls, type: 'button', disabled: !!off, onclick: f}, label);
function csrf() {
  try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; }
}
function say(text, bad) { msg = text; msgBad = !!bad; draw(); }

async function load() {
  if (busy || data) return;
  busy = true;
  failed = false;
  try {
    const r = await fetch(API, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) data = await r.json(); else failed = true;
  } catch (e) { failed = true; }
  busy = false;
  draw();
}

async function post(path, body) {
  if (busy) return null;
  busy = true;
  draw();
  let r = null, d = {};
  try {
    r = await fetch(API + path, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)});
    d = await r.json();
  } catch (e) { /* 아래에서 안내 */ }
  busy = false;
  if (r && r.status === 200) return d;
  say(!r ? '인터넷 연결을 확인해 주세요' : r.status === 401 ? '다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요'
    : r.status === 403 && d.code === 'csrf' ? '화면이 오래됐어요 — 새로고침해 주세요' : s(d.text) || '하지 못했어요 — 잠시 뒤 다시', true);
  return null;
}

function hide() {
  clearInterval(timer);
  timer = 0;
  shown = '';
  left = 0;
}
async function reveal() {
  msg = '';
  const d = await post('/reveal', {});
  if (!d) return;
  hide();
  if (data) data.user = s(d.user);
  shown = s(d.password);
  left = Math.min(Math.max(1, Number(d.seconds) || 10), 30);
  timer = setInterval(() => { left -= 1; if (left <= 0) hide(); draw(); }, 1000);
  draw();
}

function problem() {
  if (nu && !USER_RE.test(nu)) return '아이디는 영문·숫자·-_.@ 만, 64자까지 써 주세요';
  if (np.length < PW_MIN) return `비밀번호는 ${PW_MIN}자 이상으로 써 주세요`;
  if (np.length > PW_MAX) return `비밀번호는 ${PW_MAX}자까지 써 주세요`;
  if (!PW_RE.test(np)) return '비밀번호는 영문·숫자·-_. 만 써 주세요';
  if (np !== np2) return '새 비밀번호 두 칸이 서로 달라요';
  return '';
}
function edit() { hide(); nu = np = np2 = ''; msg = ''; mode = 'edit'; draw(); }
function cancel() { nu = np = np2 = ''; msg = ''; mode = 'view'; draw(); }
function next() {
  const p = problem();
  if (p) return say(p, true);
  msg = '';
  mode = 'confirm';
  draw();
}
async function save() {
  if (mode !== 'confirm' || problem()) return;
  const d = await post('/save', {user: nu, password: np, password2: np2});
  if (!d) return;
  if (data) { data.user = s(d.user); data.custom = true; }
  nu = np = np2 = '';
  mode = 'view';
  say(s(d.text) || '바꿨어요');
}

const field = (label, type, value, set, auto) => el('label', {}, label, el('input', {type, value, autocomplete: auto, autocapitalize: 'none', autocorrect: 'off',
  spellcheck: 'false', maxlength: type === 'text' ? 64 : PW_MAX, oninput: e => { set(e.currentTarget.value); }}));

function body() {
  if (!data) return [el('p', {class: 'note'}, failed ? '불러오지 못했어요 — 접었다 다시 펼쳐 주세요' : '불러오는 중…')];
  const note = msg ? el('p', {class: 'note' + (msgBad ? ' bad' : ''), role: 'status'}, msg) : null;
  if (mode === 'edit') return [
    field('새 아이디 (비우면 그대로)', 'text', nu, v => { nu = v.trim(); }, 'username'),
    field('새 비밀번호', 'password', np, v => { np = v; }, 'new-password'),
    field('새 비밀번호 한 번 더', 'password', np2, v => { np2 = v; }, 'new-password'),
    el('p', {class: 'note'}, `비밀번호는 ${PW_MIN}자 이상, 영문·숫자·-_. 만 (예: 단어 서너 개를 - 로 잇기)`), note,
    el('div', {class: 'bt'}, btn('sm ac', '다음 — 새 값 확인', next), btn('sm', '취소', cancel))];
  if (mode === 'confirm') return [
    el('div', {class: 'chk'}, el('p', {class: 'q'}, '이 값으로 바꿀까요? 다음 로그인부터 이 값을 써요.'),
      el('p', {}, '아이디: ', el('span', {class: 'v'}, nu || s(data.user))), el('p', {}, '비밀번호: ', el('span', {class: 'v'}, np))),
    el('p', {class: 'note'}, '이 기기는 그대로 로그인돼 있고, 다른 기기는 새 비밀번호로 다시 로그인해요. 저장 전에 적어 두세요.'), note,
    el('div', {class: 'bt'}, btn('sm ac', '이대로 저장', save, busy), btn('sm', '고치기', () => { msg = ''; mode = 'edit'; draw(); }, busy))];
  return [
    el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, '아이디'), el('span', {class: 's v'}, s(data.user)))),
    el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, '비밀번호'),
      el('span', {class: 's v'}, shown || DOTS), shown ? el('span', {class: 's'}, `${left}초 뒤 다시 가려요`) : null),
      shown ? btn('sm', '가리기', () => { hide(); draw(); }) : btn('sm', '보기', reveal, busy)),
    note,
    el('div', {class: 'bt'}, btn('sm ac', '바꾸기', edit)),
    el('p', {class: 'note'}, data.custom ? '이 화면에서 바꾼 값을 쓰고 있어요.' : '서버에 처음 정해 둔 값을 쓰고 있어요.'),
    el('p', {class: 'note'}, '잊으면 서버에서 bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD 로 예전과 다른 값을 정하고 sudo systemctl restart dealbot 하면 그 값(아이디는 .env 의 DASHBOARD_USER)으로 들어가요.')];
}

function draw() {
  if (!box) return;
  box.replaceChildren(el('div', {class: 'box', style: 'padding:0 14px'},
    el('button', {class: 'fold', type: 'button', 'aria-expanded': open ? 'true' : 'false', onclick: () => {
      open = !open;
      if (open) load(); else { hide(); cancel(); }
      draw();
    }}, '대시보드 로그인 (아이디·비밀번호)'),
    ...(open ? body() : [])));
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!on()) { jumped = ''; if (shown) { hide(); draw(); } return; }
  if (!box) {
    if (!D.getElementById('dac-css')) D.head.append(el('style', {id: 'dac-css'}, CSS));
    box = el('section', {id: 'dac', 'aria-label': '대시보드 로그인'});
    draw();
  }
  if (box.parentNode !== m) {  // 연결 탭을 다시 그리면 빠짐 → 계정 정리(없으면 로그인 관리) 칸 아래에
    const up = D.getElementById('acm') || D.getElementById('lgp');
    if (up && up.parentNode === m) up.after(box); else m.prepend(box);
  }
  if (direct() && jumped !== location.hash) {
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
  D.addEventListener('visibilitychange', () => { if (D.visibilityState !== 'visible' && (shown || mode === 'confirm')) { hide(); if (mode === 'confirm') mode = 'edit'; draw(); } });
  mount();
}
W.SBDashAcct = {mount};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
