/* Sns-board 앱 (주인 10/18): 할 일 · 딜 · 블로그 · 통계 · 연결. 라이브러리 없음, 첫 화면은 boot JSON. */
'use strict';
(() => {
const D = document, W = window, N = navigator;
const $ = id => D.getElementById(id);
const LS = {g(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
  s(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* 개인 창 */ } }};
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
const btn = (cls, label, on, x) => el('button', Object.assign({class: cls, type: 'button', onclick: on}, x), label);
const ext = (cls, label, url) => el('a', {class: cls, href: url, target: '_blank', rel: 'noopener noreferrer'}, label, ' ↗');
const rnd = () => Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join('');

const TABS = {todo: '할 일', deal: '딜', blog: '블로그', stats: '통계', connect: '연결'};
const BLOGS = [['finance', '아카이브'], ['issue', 'Pick'], ['hotdeal', '핫딜']];
const SEV = {stop: ['⛔', '멈춤'], hot: ['🔥', '핫'], info: ['ℹ️', '참고'], todo: ['', '할 일']};
const MSG = {published: '발행했어요', saved: '저장했어요', done: '했어요', not_found: '이미 없는 항목이에요',
  wrong_state: '지금 상태에서는 할 수 없어요', owner_edited: '직접 고친 글이라 멈췄어요 — 할 일에서 정해 주세요',
  busy: '다른 일을 하는 중이에요 — 잠시 뒤 다시', expired: '시간이 지나 취소됐어요 — 다시 눌러 주세요',
  bad_args: '입력한 값을 다시 확인해 주세요', bad_upload: '올린 파일을 쓸 수 없어요', failed: '하지 못했어요 — 잠시 뒤 다시',
  login_needed: '네이버 로그인이 필요해요 — 연결에서 확인해 주세요', unsupported: '블로그 프로그램 업데이트를 기다리는 중이에요',
  blogauto_down: '블로그 프로그램이 응답하지 않아요', rate: '너무 자주 눌렀어요 — 조금 뒤에 다시', net: '인터넷 연결을 확인해 주세요',
  unknown: '확인이 필요해요 — 잠시 뒤 상태를 봐 주세요', confirm_expired: '확인 창이 오래됐어요 — 다시 눌러 주세요'};
const SCH = /^blog\.(un)?schedule/, PIX = /^blog\.pics_/;
const SECRET_KEY = {'sns.token': 'token', 'sns.code': 'code', 'blog.kakao_connect': 'url'};

let S = null, OLD = false, R = {tab: 'todo', sub: ''}, loading = null, ALL = false, DIRTY = false, NEXT = null;
const VAL = {}, P = {}, OPEN = {}, POLL = {};
let REG = null, SUB = null, RESYNC = false, KURL = null, KSTEP = false, STATS = null, DENY_ONCE = false;
const IOS = /iP(hone|ad|od)/.test(N.userAgent) || (N.platform === 'MacIntel' && N.maxTouchPoints > 1);
const STANDALONE = N.standalone === true || matchMedia('(display-mode: standalone)').matches;
const PUSH = 'serviceWorker' in N && 'PushManager' in W && 'Notification' in W;

// ------------------------------------------------------------ 시간
const now = () => Date.now() / 1000;
function ago(t) {
  if (!t) return '';
  const s = Math.max(0, now() - t);
  return s < 60 ? '방금' : s < 3600 ? `${Math.floor(s / 60)}분 전` : s < 86400 ? `${Math.floor(s / 3600)}시간 전` : `${Math.floor(s / 86400)}일 전`;
}
const hhmm = t => t ? new Date(t * 1000).toLocaleTimeString('ko-KR', {hour: '2-digit', minute: '2-digit', hour12: false}) : '';

// ------------------------------------------------------------ 서버
async function api(path, opt = {}, again) {
  const h = Object.assign({}, opt.headers);
  if (opt.method === 'POST') { h['X-Dash-Token'] = S && S.me ? S.me.csrf : ''; h['Content-Type'] = 'application/json'; }
  let r;
  try {
    r = await fetch(path, {method: opt.method || 'GET', headers: h, credentials: 'same-origin', cache: 'no-store',
      body: opt.body != null ? JSON.stringify(opt.body) : undefined});
  } catch (e) { return {status: 0, data: {code: 'net'}}; }
  let data = {};
  if ((r.headers.get('content-type') || '').includes('json')) try { data = await r.json(); } catch (e) { /* 빈 몸 */ }
  if (!again && r.status === 401 && await login()) return api(path, opt, 1);
  if (!again && r.status === 403 && data.code === 'csrf') { await load(); return api(path, opt, 1); }
  return {status: r.status, data};
}

function load() {
  if (loading) return loading;
  $('rf').classList.add('busy');
  loading = (async () => {
    let r = null;
    try {
      r = await fetch('/dashboard/api/state', {credentials: 'same-origin', cache: 'no-store',
        headers: S && S.etag ? {'If-None-Match': `"${S.etag}"`} : {}});
    } catch (e) { r = null; }
    loading = null;
    $('rf').classList.remove('busy');
    if (r && r.status === 401) { if (await login()) return load(); return; }
    if (!r || (!r.ok && r.status !== 304)) { OLD = true; head(); return; }
    if (r.status === 304) { OLD = false; head(); after(); return; }
    const d = await r.json().catch(() => null);
    if (!d) return;
    OLD = r.headers.get('x-sw-cache') === '1';
    const same = S && S.etag && d.etag === S.etag;
    S = d;
    if (!same) render(); else head();
    after();
  })();
  return loading;
}

function after() {
  if (!S) return;
  const open = new Set(S.todo.map(i => i.iid));
  if (REG && REG.getNotifications) {
    REG.getNotifications().then(ns => ns.forEach(n => {
      if (n.tag === 'digest' || n.tag === 'test') return;
      if (n.tag === 'todo-batch' ? !S.todo.length : !open.has(n.tag)) n.close();
    })).catch(() => {});
  }
  try { S.counts.todo ? N.setAppBadge && N.setAppBadge(S.counts.todo) : N.clearAppBadge && N.clearAppBadge(); } catch (e) { /* 지원 안 함 */ }
  for (const p of S.pending || []) if (!POLL[p.id] && (p.status === 'queued' || p.status === 'running')) POLL[p.id] = {iid: p.target, action: p.action, t: Date.now()};
  if (Object.keys(POLL).length) poll();
  if (SUB && !thisOn() && !RESYNC && S.me.push_key) { RESYNC = true; sendSub().then(r => r.status === 200 && load()); }
  const n = S.connect && S.connect.notice;
  if (n && n.text) { toast(n.text); S.connect.notice = null; }
}

// ------------------------------------------------------------ 화면 틀
function head() {
  const t = R.tab === 'todo' && S ? (S.todo.length ? `할 일 ${S.todo.length}개` : '할 일') : TABS[R.tab];
  $('ttl').textContent = t;
  D.title = `${TABS[R.tab]} · Sns-board`;
  const o = $('old');
  o.hidden = !(OLD && S && R.tab === 'todo');
  if (!o.hidden) o.textContent = `저장된 정보 · ${ago(S.at)}`;
  const b = $('bdg'), n = S ? S.counts.todo : 0;
  b.hidden = !n; b.textContent = n > 99 ? '99+' : n; b.classList.toggle('urg', !!S && S.todo.some(i => i.sev === 'stop'));
  D.querySelectorAll('.tab').forEach(a => a.dataset.tab === R.tab ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'));
}

function render() {
  if (!S) return;
  const a = D.activeElement, m = $('main');
  if (a && m.contains(a) && a.matches('input,textarea') && !SH.length) { DIRTY = true; head(); return; }
  DIRTY = false;
  const v = {todo: vTodo, deal: vDeal, blog: vBlog, stats: vStats, connect: vConnect}[R.tab]();
  m.replaceChildren(...v);
  head();
  if (NEXT) {
    const it = $('i-' + NEXT), f = it && it.querySelector('input[data-url]'); NEXT = null;
    if (f) { it.scrollIntoView({block: 'center', behavior: 'smooth'}); f.focus({preventScroll: true}); }
  }
}
D.addEventListener('focusout', () => setTimeout(() => DIRTY && render(), 50));

function route() {
  const p = (location.hash || '#/todo').replace(/^#\/?/, '').split('/');
  if (p[0] === 'i' && p[1]) { history.replaceState(null, '', '#/todo'); R = {tab: 'todo', sub: ''}; render(); openItem(decodeURIComponent(p[1])); return; }
  R = {tab: TABS[p[0]] ? p[0] : 'todo', sub: p[1] || ''};
  if (R.tab === 'blog' && !/^(pics|search)$/.test(R.sub)) { if (!BLOGS.some(b => b[0] === R.sub)) R.sub = LS.g('blog') || 'finance'; LS.s('blog', R.sub); }
  while (SH.length) closeTop();
  render();
  scrollTo(0, 0);
  if (R.tab === 'stats') loadStats();
}
function go(u) {
  const m = /\/go\/([a-z0-9-]+)/.exec(u || '');
  if (m) { if (m[1] === 'todo') location.hash = '#/todo'; else openItem(m[1]); return; }
  const h = (u || '').split('#')[1];
  if (h) location.hash = '#' + h;
}

// ------------------------------------------------------------ 시트 (모달 대신 아래에서 올라오는 판)
const SH = [];
function sheet(title, body, onclose) {
  const sh = el('div', {class: 'sh', role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1'},
    el('div', {class: 'gp'}), el('div', {class: 'shh'}, el('h2', {text: title}), btn('lnk mute', '닫기', back)),
    el('div', {class: 'shb'}, body));
  const bd = el('div', {class: 'bd', onclick: e => e.target === bd && back()}, sh);
  let y0 = null;
  sh.addEventListener('touchstart', e => { y0 = e.target.closest('.shb') && sh.querySelector('.shb').scrollTop > 0 ? null : e.touches[0].clientY; }, {passive: true});
  sh.addEventListener('touchmove', e => { if (y0 != null) { const dy = e.touches[0].clientY - y0; sh.style.transform = dy > 0 ? `translateY(${dy}px)` : ''; } }, {passive: true});
  sh.addEventListener('touchend', e => { if (y0 == null) return; const dy = e.changedTouches[0].clientY - y0; sh.style.transform = ''; y0 = null; if (dy > 90) back(); });
  $('sheets').append(bd);
  SH.push({bd, onclose, from: D.activeElement});
  history.pushState({sh: SH.length}, '');
  D.body.style.overflow = 'hidden'; D.body.classList.add('shon');
  sh.focus({preventScroll: true});
  return sh.querySelector('.shb');
}
function back() { if (SH.length) history.back(); }
function closeTop() {
  const s = SH.pop();
  s.bd.remove();
  if (!SH.length) { D.body.style.overflow = ''; D.body.classList.remove('shon'); }
  if (s.onclose) s.onclose();
  if (s.from && s.from.focus && D.contains(s.from)) s.from.focus({preventScroll: true});
}
W.addEventListener('popstate', e => { const n = (e.state && e.state.sh) || 0; while (SH.length > n) closeTop(); });
D.addEventListener('keydown', e => {
  if (!SH.length) return;
  if (e.key === 'Escape') { back(); return; }
  if (e.key !== 'Tab') return;
  const box = SH[SH.length - 1].bd, f = [...box.querySelectorAll('button,a[href],input,textarea,select')].filter(x => !x.disabled);
  if (!f.length) return;
  const i = f.indexOf(D.activeElement);
  if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
});
function confirmSheet(d) {
  return new Promise(res => {
    let ok = false;
    sheet(d.title || '이대로 할까요?', el('div', {class: 'cf'}, el('p', {text: d.body || ''}),
      btn('pb' + (d.danger ? ' red' : ''), d.ok || '확인', () => { ok = true; back(); }), btn('pb ghost', '취소', back)), () => res(ok));
  });
}

// 간격 두고 발행 확인: 제목·시각 + '첫 글은 지금'
function planSheet(d, body) {
  return new Promise(res => {
    let ok = false, cur = d;
    const box = el('div', {class: 'cf'});
    const draw = () => box.replaceChildren(el('p', {text: cur.body || ''}),
      el('ul', {class: 'pl'}, cur.plan.map(x => el('li', {}, el('span', {}, x.title), el('b', {}, x.when)))),
      cur.toggle ? btn('tg', cur.toggle.label, flip, {'aria-pressed': cur.toggle.on ? 'true' : 'false'}) : null,
      btn('pb', cur.ok || '예약하기', () => { ok = true; back(); }), btn('pb ghost', '취소', back));
    async function flip(e) {
      e.currentTarget.disabled = true;
      const args = Object.assign({}, body.args, {first_now: !cur.toggle.on}); delete args.at;
      const r = await api('/dashboard/api/action', {method: 'POST', body: {action: body.action, args, cid: 'c_' + rnd(), ctoken: null}});
      if (r.status === 409 && r.data.plan) cur = r.data; else toast(r.data.text || MSG.failed);
      draw();
    }
    draw();
    sheet(cur.title, box, () => res(ok && cur));
  });
}

// ------------------------------------------------------------ 알림 줄 (토스트)
let toastT = null;
function toast(text, act, fn) {
  const t = $('toast');
  t.replaceChildren(el('span', {text}), ...(act ? [btn('', act, () => { t.hidden = true; fn(); })] : []));
  t.hidden = false;
  clearTimeout(toastT);
  toastT = setTimeout(() => { t.hidden = true; }, act ? 8000 : 3000);
}

// ------------------------------------------------------------ 로그인 시트 (하던 일은 그대로)
let LOGIN = null;
function login() {
  if (LOGIN) return LOGIN;
  LOGIN = new Promise(res => {
    let t = '', ok = false;
    fetch('/dashboard/login', {headers: {Accept: 'application/json'}, credentials: 'same-origin'}).then(r => r.json()).then(d => { t = d.t; }).catch(() => {});
    const u = el('input', {class: 'fl', name: 'user', autocomplete: 'username', placeholder: '아이디', autocapitalize: 'off'});
    const pw = el('input', {class: 'fl', type: 'password', name: 'password', autocomplete: 'current-password', placeholder: '비밀번호'});
    const msg = el('p', {class: 'note'}), go = el('button', {class: 'pb', type: 'submit'}, '로그인');
    const f = el('form', {onsubmit: async e => {
      e.preventDefault(); go.disabled = true;
      let r = null;
      try {
        r = await fetch('/dashboard/login', {method: 'POST', credentials: 'same-origin', redirect: 'manual', headers: {Accept: 'application/json'},
          body: new URLSearchParams({t, user: u.value, password: pw.value})});
      } catch (x) { r = null; }
      go.disabled = false;
      if (r && (r.type === 'opaqueredirect' || r.status === 303)) { ok = true; back(); return; }
      const d = r ? await r.json().catch(() => ({})) : {code: 'net'};
      if (d.t) t = d.t;
      msg.textContent = d.text || MSG.net;
    }}, el('p', {text: '다시 로그인하면 하던 일을 이어서 해요.'}), u, pw, msg, go);
    sheet('다시 로그인', f, () => { LOGIN = null; res(ok); });
    u.focus();
  });
  return LOGIN;
}

// ------------------------------------------------------------ 누르기 → 서버
const pk = (iid, a) => `${iid}|${a.action}|${a.label}`;
async function exec(a, extra, ctx = {}) {
  const body = {action: a.action, args: Object.assign({}, a.args, extra), cid: 'c_' + rnd(), ctoken: null};
  const b = ctx.btn;
  if (b) { b.disabled = true; b.prepend(el('span', {class: 'spin'})); }
  let r = await api('/dashboard/api/action', {method: 'POST', body});
  if (r.status === 409 && r.data.status === 'confirm') {
    if (b) { b.disabled = false; b.querySelector('.spin') && b.querySelector('.spin').remove(); }
    const c = r.data.plan ? await planSheet(r.data, body) : await confirmSheet(r.data) && r.data;
    if (!c) return null;
    body.ctoken = c.ctoken;
    if (c.args) body.args = c.args;  // 보여 준 시각 그대로
    if (b) { b.disabled = true; b.prepend(el('span', {class: 'spin'})); }
    r = await api('/dashboard/api/action', {method: 'POST', body});
  }
  if (b && D.contains(b)) { b.disabled = false; b.querySelector('.spin') && b.querySelector('.spin').remove(); }
  const d = r.data || {};
  if (r.status === 202) {
    POLL[d.id] = {iid: ctx.iid || '', action: a.action, args: body.args, t: Date.now()};
    if (ctx.key) { delete P[ctx.key]; delete OPEN[ctx.iid]; }
    toast(SCH.test(a.action) ? '예약하는 중이에요' : a.action === 'blog.visit_done' ? '했어요로 표시했어요' : /feedback|learn_/.test(a.action) ? '받았어요 — 곧 반영돼요' : PIX.test(a.action) ? (/fetch/.test(a.action) ? '사진 찾는 중이에요 — 다 되면 알려 드릴게요' : '지우는 중이에요') : a.action.startsWith('blog.') ? '받았어요 — 블로그에 넣는 데 1~2분 걸려요. 끝나면 알려 드릴게요' : '요청했어요 — 끝나면 알려 드릴게요');
    render(); poll();
    return d;
  }
  if (r.status === 200 && d.status === 'ok') {
    if (ctx.key) { delete P[ctx.key]; delete OPEN[ctx.iid]; }
    if (ctx.keys) ctx.keys.forEach(k => delete VAL[k]);
    if (a.action === 'deal.link') {  // 여러 개 한 번에: 다음 링크 줄을 열어 둔다
      const ls = S.todo.filter(i => i.primary && i.primary.action === 'deal.link'), at = ls.findIndex(i => i.iid === ctx.iid);
      const nx = ls[at + 1] || ls.find(i => i.iid !== ctx.iid);
      if (nx) { OPEN[nx.iid] = pk(nx.iid, nx.primary); NEXT = nx.iid; }
    }
    toast(d.text || MSG.done);
    if (ctx.close) back();
    load();
    return d;
  }
  toast(d.text || MSG[d.code] || MSG.failed);
  return null;
}

let pollT = null;
async function poll() {
  clearTimeout(pollT);
  const ids = Object.keys(POLL);
  if (!ids.length) return;
  const r = await api('/dashboard/api/actions?ids=' + ids.slice(0, 20).join(','));
  let changed = false, moved = false;
  for (const s of (r.data && r.data.actions) || []) {
    const p = POLL[s.id];
    if (!p) continue;
    if (s.status === 'queued' || s.status === 'running') { if (p.status !== s.status) { p.status = s.status; moved = true; } continue; }
    delete POLL[s.id]; changed = true;
    finish(s, p);
  }
  if (changed) load(); else if (moved) render();
  const left = Object.keys(POLL);
  if (left.length) pollT = setTimeout(poll, left.some(i => Date.now() - POLL[i].t < 120000) ? 2000 : 10000);
}
function finish(s, p) {
  const ok = s.status === 'ok';
  if (s.action === 'blog.kakao_auth_url' && ok && s.url) { KURL = s.url; toast('카카오 화면을 열어 주세요'); return; }
  if (s.action === 'blog.info' && ok) { infoSheet(s.text); return; }
  if (s.action === 'blog.visit_done') { if (ok) return; GONE.delete(p.iid); }
  let text = ok ? (s.text && !/\/[a-z]/.test(s.text) ? s.text : MSG[s.code] || MSG.done) : (!/move/.test(s.action) && MSG[s.code]) || s.text || MSG.failed;
  if (s.status === 'unknown') text = MSG.unknown;
  if (s.status === 'expired') text = MSG.expired;
  const a = p.args || {};
  if (ok && s.action === 'blog.fix' && a.blog === 'finance') {
    toast(text, '사진도 바꾸기', () => exec({action: 'blog.fixpic', args: {pid: a.pid, blog: 'finance'}}, {}, {iid: s.target}));
  } else if (ok && s.action === 'blog.pics_fetch') toast(text, '보기', () => s.target ? openItem(s.target) : go('#/blog/pics'));
  else toast(text);
}
function pendingOf(iid) {
  if (!iid) return null;
  const mine = Object.values(POLL).find(p => p.iid === iid && !PIX.test(p.action));
  if (mine) return mine;
  return (S.pending || []).find(p => p.target === iid && !PIX.test(p.action) && (p.status === 'queued' || p.status === 'running'));
}
const fetching = iid => Object.values(POLL).some(p => p.iid === iid && /pics_fetch/.test(p.action)) ||
  (S.pending || []).some(p => (p.target || 'pics') === iid && /pics_fetch/.test(p.action) && /queued|running/.test(p.status));

// ------------------------------------------------------------ 올리기 (사진 · 정리본)
async function prep(f) {
  const ok = /^image\/(jpeg|png|webp)$/.test(f.type);
  if (ok && f.size <= 4e6) return f;
  try {
    const b = await createImageBitmap(f, {imageOrientation: 'from-image'});
    const k = Math.min(1, 2560 / Math.max(b.width, b.height)), c = D.createElement('canvas');
    c.width = Math.round(b.width * k); c.height = Math.round(b.height * k);
    c.getContext('2d').drawImage(b, 0, 0, c.width, c.height);
    if (b.close) b.close();
    const out = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.92));
    if (out) return out;
  } catch (e) { /* 아래 */ }
  if (ok) return f;
  throw new Error('이 사진은 열 수 없어요 — 다른 사진으로');
}
function xhr(blob, kind, onp) {
  return new Promise(res => {
    const x = new XMLHttpRequest();
    x.open('POST', '/dashboard/api/upload');
    x.setRequestHeader('X-Dash-Token', S.me.csrf);
    x.setRequestHeader('X-Upload-Kind', kind);
    x.setRequestHeader('Content-Type', blob.type || 'application/octet-stream');
    x.timeout = 300000;
    x.upload.onprogress = e => e.lengthComputable && onp(e.loaded / e.total);
    x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch (e) { /* */ } res({status: x.status, data: d}); };
    x.onerror = x.ontimeout = () => res({status: 0, data: {}});
    x.send(blob);
  });
}
async function uploadAll(files, kind, onp) {
  const ids = [];
  for (let i = 0; i < files.length; i++) {
    let f = files[i];
    if (kind === 'ledger') f = new Blob([f], {type: /\.md$/i.test(f.name) ? 'text/markdown' : 'text/plain'});
    else f = await prep(f);
    for (let tries = 0; ; ) {
      const r = await xhr(f, kind, x => onp(i, x));
      if (r.status === 200 && r.data.upload_id) { ids.push(r.data.upload_id); onp(i, 1, true); break; }
      if (r.status === 401) { if (!await login()) throw new Error('로그인 후 다시 올려 주세요'); continue; }
      if (r.status === 403 && r.data.code === 'csrf') { await load(); continue; }
      if ([400, 413, 415].includes(r.status)) { onp(i, 0, false); throw new Error(r.data.text || MSG.bad_upload); }
      if (++tries > 2) { onp(i, 0, false); throw new Error(r.status ? MSG.failed : MSG.net); }
    }
  }
  return ids;
}
const prog = figs => (i, x, done) => {  // 올리는 칸 아래 막대 · 끝나면 초록 / 실패 빨강
  const fg = figs[i]; if (!fg) return;
  fg.querySelector('i').style.width = Math.round(x * 100) + '%';
  if (done === true) fg.classList.add('ok'); else if (done === false) fg.classList.add('bad');
};
let PICK = 0;
function pick(accept, multiple, done) {
  const f = el('input', {type: 'file', accept, multiple: multiple || null, hidden: true});
  PICK = Date.now();
  f.addEventListener('change', () => { PICK = 0; done([...f.files]); f.remove(); });
  D.body.append(f);
  f.click();
}

// ------------------------------------------------------------ 줄 하나 (칩 · 제목 · 상태 한 줄 · 누를 것 하나)
function chip(it) {
  if (it.cue) return el('span', {class: 'cp ' + (it.cue === '급해요' ? 'stop' : 'hot')}, it.cue);
  const [mk, lab] = SEV[it.sev] || SEV.todo;
  if (!mk && !it.chip) return null;
  const text = it.sev === 'todo' || !mk ? (it.chip || lab) : `${mk} ${it.chip || lab}`;
  return el('span', {class: 'cp ' + (it.sev || 'todo')}, text);
}
function item(it, plain) {
  const more = (it.more || []).filter(a => !a.disabled);
  const warn = it.expires_at && it.expires_at - now() < 3 * 3600;
  return el('article', {class: 'itm', id: 'i-' + it.iid, 'data-iid': it.iid},
    el('div', {class: 'ih'}, plain && it.sev === 'todo' ? null : chip(it), btn('ti', title(it), () => openItem(it.iid, it)),
      more.length ? btn('lnk mute', '더보기', () => moreSheet(it, more)) : null),
    it.status ? el('p', {class: 'st' + (warn ? ' warn' : '')}, it.status) : null,
    it.primary && it.primary.input === 'pics' && !pendingOf(it.iid) ? btn('pks', picks(it).slice(0, 5).map(x => el('img', {src: x.thumb, alt: ''})), () => openItem(it.iid, it), {'aria-label': '추천 사진 보기'}) : null,
    primary(it, it.primary));
}

const title = it => { const c = it.chip && `[${it.chip}]`; return c && it.title.startsWith(c) ? it.title.slice(c.length).trim() : it.title; };
function primary(it, a) {
  if (!a) return null;
  const pend = pendingOf(it.iid);
  if (pend) return el('div', {}, btn('pb', [el('span', {class: 'spin'}), pend.status === 'running' ? ' 하는 중' : ' 요청함'], null, {disabled: true}),
    it.src === 'blog' && !SCH.test(pend.action || '') ? el('p', {class: 'note'}, '블로그에 넣는 중이에요 · 1~2분') : null);
  if (a.disabled) return el('div', {}, btn('pb', a.label, null, {disabled: true}), el('p', {class: 'note'}, a.note || ''));
  if (a.input === 'open') return ext('pb soft', a.label, a.url);
  if (a.input === 'goto') return btn('pb soft', a.label, () => go('#' + a.goto.replace(/^#/, '')));
  const key = pk(it.iid, a);
  if (OPEN[it.iid] === key) return panel(it, a, key, {});
  let b = btn('pb', a.label, e => act(it, a, e.currentTarget, true));
  const sl = igSlots(it, a), L = a.input === 'photos' ? it.links || [] : [];
  // 사진 부탁: 사진 받을 곳 (첫 링크) — 큰 버튼 옆 테두리 버튼, 링크가 더 있으면 시트에서 모두
  if (L.length) b = el('div', {class: 'two'}, b, ext('pb soft', it.src === 'deal' || it.blog === 'hotdeal' ? '상품 열기' : '사진 찾을 곳', L[0].url));
  const t = [sl.length ? btn('lnk', '인스타 링크 넣기', () => {
    igAdd(P[key] = {files: [], opt: {}}, sl);
    if (SH.length) inputSheet(it, a, key); else { OPEN[it.iid] = key; render(); }
  }) : null, L.length > 1 && !SH.length ? btn('lnk', `링크 ${L.length}개 모두`, () => openItem(it.iid, it)) : null].filter(Boolean);
  return t.length ? el('div', {}, b, el('div', {class: 'tools sub'}, ...t)) : b;
}
// 인스타 링크 (Pick 사진 자리 — 주소 글자만 보고 Blog-Auto 가 링크 카드로): 빈 자리(설명 한 줄), 자리 정보가 없으면 1..N
const IGRE = /^https?:\/\/(www\.|m\.)?instagram\.com\/([\w.]+\/)?(p|reels?)\/[\w-]{5,40}([/?#]|$)/i;
function igSlots(it, a) {
  if (a.input !== 'photos' || !a.options || (a.args || {}).blog !== 'issue') return [];
  const d = it.detail || {};
  return d.slots && d.slots.length ? d.slots.filter(x => !x.thumb) : Array.from({length: ((d.photo_request || {}).must || []).length || 6}, (_, i) => ({slot: i + 1}));
}
function igAdd(st, sl) {
  const rows = st.ig = st.ig || [], u = rows.map(r => r.slot);
  rows.push({slot: (sl.find(x => !u.includes(x.slot)) || sl[0]).slot, url: ''});
}

// 누를 것: 입력이 없으면 바로, 있으면 그 자리에(inline) 또는 시트로
function act(it, a, b, inline) {
  if (a.input === 'open') { W.open(a.url, '_blank', 'noopener'); return; }
  if (a.input === 'goto') { go('#' + a.goto.replace(/^#/, '')); return; }
  if (a.input === 'none' || !a.input) { exec(a, {}, {btn: b, iid: it.iid}); return; }
  if (a.input === 'pics') { exec(a, picks(it, 1), {btn: b, iid: it.iid}); return; }
  if (a.input === 'choice') { choiceSheet(a); return; }
  if (a.input === 'search') { searchSheet(a); return; }
  if (a.input === 'when') { whenSheet(a); return; }
  const key = pk(it.iid, a);
  if (a.input === 'photos' || a.input === 'file') {
    pick(a.accept || 'image/*', a.multiple, fs => {
      if (!fs.length) return;
      P[key] = {files: fs.slice(0, a.multiple ? 20 : 1), opt: {}};
      if (inline) { OPEN[it.iid] = key; render(); } else inputSheet(it, a, key);
    });
    return;
  }
  if (!inline) { inputSheet(it, a, key); return; }
  if (a.input === 'url') for (const k in OPEN) if (OPEN[k].includes('|deal.link|')) delete OPEN[k];
  OPEN[it.iid] = key; render();
  const f = D.querySelector(`[data-k="${CSS.escape(key)}"]`); if (f) f.focus();
  if (a.input === 'url' && !IOS && !VAL[key] && N.clipboard && N.clipboard.readText) {
    N.clipboard.readText().then(t => { t = (t || '').trim(); if (/^https?:\/\/\S+$/.test(t) && f && !f.value) { f.value = t; VAL[key] = t; } }).catch(() => {});
  }
}
function inputSheet(it, a, key) {
  const box = el('div');
  const ctx = {sheet: true};
  sheet(a.label, box, () => { if (!ctx.done) delete P[key]; });
  box.append(panel(it, a, key, ctx));
  const f = box.querySelector('input,textarea');
  if (f && a.input !== 'photos') f.focus();
}

function field(key, a, x) {
  const multi = (a.input === 'text' && !a.optional) || a.input === 'answers' || (a.placeholder || '').includes('\n');
  const f = el(multi && a.input !== 'url' ? 'textarea' : 'input', Object.assign({class: 'fl', 'data-k': key, placeholder: a.placeholder || '',
    autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', oninput: e => { VAL[key] = e.target.value; }}, x));
  f.value = VAL[key] || '';
  return f;
}

function panel(it, a, key, ctx) {
  const st = P[key] || (P[key] = {files: [], opt: {}});
  const box = el('div', {class: 'ip'});
  const kids = [], keys = [], qs = a.input === 'answers' && (it.detail && it.detail.questions) || [], qf = st.q = st.q || {};
  const cancel = ctx.sheet || ctx.fixed ? null : btn('lnk mute', '접기', () => { delete P[key]; delete OPEN[it.iid]; render(); });
  let f = null;
  if (a.input === 'url') {
    f = field(key, a, {type: 'text', inputmode: 'url', 'data-url': a.optional ? null : '1', enterkeyhint: 'go',
      onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); go1(); } }});
    keys.push(key);
    const tools = el('div', {class: 'tools'},
      a.url ? ext('lnk', '상품 열기', a.url) : null, paste(f, key),
      a.photo_optional ? btn('lnk', st.files.length ? '사진 바꾸기' : '사진 추가 (선택)', () => pick(a.accept || 'image/*', false, fs => { if (fs.length) { st.files = fs.slice(0, 1); redraw(); } })) : null);
    kids.push(tools, f);
  } else if (qs.length) {
    // 답마다 사진 첨부 (10/18: 공식 페이지 캡처 같은 근거 — Blog-Auto 가 읽고 고친다, 글에 넣지는 않음). ✕ 는 기호 — 읽는 글자는 aria-label
    for (const q of qs) {
      const k = `${key}|${q.n}`, fs = qf[q.n] || [];
      keys.push(k);
      kids.push(el('label', {class: 'lab'}, `${q.n}. ${q.q}`), field(k, {input: 'text', placeholder: '답'}, {rows: 2, class: 'fl short'}),
        fs.length ? el('div', {class: 'pv', 'data-q': q.n}, fs.map((x, i) => el('figure', {}, el('img', {src: x.u || (x.u = URL.createObjectURL(x.f)), alt: ''}), el('i'),
          btn('x', ['✕'], () => { URL.revokeObjectURL(x.u); fs.splice(i, 1); redraw(); }, {'aria-label': '사진 빼기'})))) : null,
        el('div', {class: 'tools'}, btn('lnk', fs.length ? `사진 ${fs.length}장 · 더 첨부` : '사진 첨부', () => pick('image/*', true, more => {
          if (more.length) { qf[q.n] = fs.concat(more.map(f => ({f}))).slice(0, 10); redraw(); }
        }))));
    }
  } else if (a.input === 'text' || a.input === 'answers') {
    if (a.product) { keys.push(key + '|p'); kids.push(field(key + '|p', {placeholder: '제품 이름 (예: 샤오미 무선청소기)'}, {maxlength: 60})); }
    f = field(key, a, {rows: 4}); keys.push(key); kids.push(f);
    if (a.photos_optional) kids.push(btn('lnk', st.files.length ? `사진 ${st.files.length}장 · 다시 고르기` : '사진 추가 (선택)', () => pick('image/*', true, fs => { st.files = fs.slice(0, 20); redraw(); })));
  } else if (a.input === 'secret') {
    f = field(key, a, {type: 'password', autocomplete: 'off'}); keys.push(key);
    kids.push(a.hint ? el('p', {class: 'note'}, a.hint) : null, el('div', {class: 'tools'}, paste(f, key)), f);
  } else if (a.input === 'file') {
    kids.push(el('p', {}, st.files.length ? st.files[0].name : '파일을 골라 주세요'), btn('lnk', '다시 고르기', () => pick(a.accept, false, fs => { if (fs.length) { st.files = fs; redraw(); } })));
  }
  if (st.files.length && a.input !== 'file') {
    kids.push(el('div', {class: 'pv'}, st.files.map((x, i) => el('figure', {'data-i': i}, el('img', {src: st.url && st.url[i] || (st.url = st.url || [], st.url[i] = URL.createObjectURL(x)), alt: ''}), el('i')))));
  }
  if (a.input === 'photos') {
    const sl = igSlots(it, a), rows = st.ig || [];
    kids.push(el('div', {class: 'tools'}, btn('lnk', st.files.length ? '다시 고르기' : '사진 고르기', () => pick(a.accept || 'image/*', a.multiple, fs => { if (fs.length) { st.files = fs.slice(0, a.multiple ? 20 : 1); st.url = null; redraw(); } })),
      a.options && !st.more && st.files.length ? btn('lnk', '옵션', () => { st.more = 1; redraw(); }) : null,
      rows.length < sl.length ? btn('lnk', rows.length ? '링크 더 넣기' : '인스타 링크 넣기', () => { igAdd(st, sl); redraw(); }) : null));
    rows.forEach((r, i) => {
      const u = el('input', {class: 'fl', type: 'url', inputmode: 'url', placeholder: 'instagram.com/p/… 게시물 주소', autocomplete: 'off', oninput: e => { r.url = e.target.value; }});
      u.value = r.url;
      kids.push(el('div', {class: 'ig'}, el('select', {class: 'fl', 'aria-label': '사진 자리', onchange: e => { r.slot = +e.target.value; }},
        sl.map(x => el('option', {value: x.slot, selected: x.slot === r.slot}, `자리 ${x.slot}` + (x.label && x.label !== '자리 ' + x.slot ? ' · ' + x.label : '')))),
      btn('lnk mute', '빼기', () => { rows.splice(i, 1); redraw(); }), u, btn('lnk', '붙여넣기', async () => {
        try { const t = ((await N.clipboard.readText()) || '').trim(); if (!t) throw 0; u.value = r.url = t; } catch (e) { u.focus(); toast('길게 눌러 붙여넣기'); }
      })));
    });
    if (a.options && st.more) {
      const T = {replace: '전부 바꿔 넣기', in_order: '순서대로', crop: '자르기', credit: '출처 적기'};
      kids.push(el('div', {class: 'opts'}, a.options.map(o => btn('tg', T[o] || o, () => { st.opt[o] = !st.opt[o]; redraw(); }, {'aria-pressed': st.opt[o] ? 'true' : 'false'}))));
      if (st.opt.credit) { const k = key + '|credit'; keys.push(k); kids.push(field(k, {placeholder: '사진 출처 (예: 소속사 제공)'}, {maxlength: 60})); }
    }
  }
  const n = st.files.length;
  const k = (st.ig || []).length;
  const label = a.input !== 'photos' ? a.label : k ? `${n ? n + '장 · ' : ''}링크 ${k}개 넣기` : n ? `${n}장 올리기` : a.label;
  const send = btn('pb', label, () => go1());
  kids.push(send);
  if (a.note) kids.push(el('p', {class: 'note'}, a.note));
  if (cancel) kids.push(el('div', {class: 'foot'}, cancel));
  box.append(...kids.filter(Boolean));
  function redraw() { if (ctx.sheet && box.isConnected) box.replaceWith(panel(it, a, key, ctx)); else { if (!ctx.sheet) OPEN[it.iid] = key; render(); } }
  async function go1() {
    const extra = {};
    if (a.input === 'url') {
      const v = (f.value || '').trim();
      if (!v && !a.optional) { f.focus(); toast('링크를 넣어 주세요'); return; }
      extra.url = v;
    } else if (qs.length) {
      const vals = keys.map(k => (VAL[k] || '').trim()), ph = qs.map(q => qf[q.n] || []);
      if (!vals.some(Boolean) && !ph.some(x => x.length)) { toast('답을 적어 주세요'); return; }
      extra.text = vals.map((v, i) => `${i + 1}) ${v || (ph[i].length ? '(사진 참고)' : '-')}`).join('\n');
      if (ph.some(x => x.length)) {
        const per = qs.map((q, i) => ({n: q.n, text: vals[i], uploads: []}));
        send.disabled = true; send.textContent = '올리는 중…';
        try {
          for (let i = 0; i < qs.length; i++) if (ph[i].length) per[i].uploads = await uploadAll(ph[i].map(x => x.f), 'photo', prog(box.querySelectorAll(`.pv[data-q="${qs[i].n}"] figure`)));
        } catch (e) { send.disabled = false; send.textContent = label; toast(e.message || MSG.bad_upload); return; }
        send.disabled = false; send.textContent = label;
        extra.answers = per;
      }
    } else if (f && (a.input === 'text' || a.input === 'answers' || a.input === 'secret')) {
      const v = (f.value || '').trim();
      if (!v && !a.optional) { f.focus(); toast('내용을 적어 주세요'); return; }
      if (a.product && !(extra.product = (VAL[key + '|p'] || '').trim())) { toast('제품 이름을 적어 주세요'); return; }
      if (a.input === 'secret') extra[SECRET_KEY[a.action] || 'token'] = v;
      else if (a.action === 'blog.issue') extra.keyword = v; else extra.text = v;
    }
    const links = (st.ig || []).map(r => ({slot: r.slot, url: r.url.trim()}));
    if (links.some(l => !IGRE.test(l.url))) { toast('인스타 게시물 주소(…/p/·…/reel/)만 넣을 수 있어요'); return; }
    if (new Set(links.map(l => l.slot)).size < k) { toast('같은 자리를 두 번 골랐어요'); return; }
    if (k) extra.links = links;
    else if ((a.input === 'photos' || a.input === 'file') && !n) { act(it, a, null, !ctx.sheet); return; }
    let ids = [];
    if (n) {
      send.disabled = true; send.textContent = '올리는 중…';
      try {
        ids = await uploadAll(st.files, a.input === 'file' ? 'ledger' : 'photo', prog(box.querySelectorAll('.pv figure')));
      } catch (e) { send.disabled = false; send.textContent = label; toast(e.message || MSG.bad_upload); return; }
      send.disabled = false; send.textContent = label;
    }
    if (a.action === 'deal.link') { if (ids[0]) extra.upload_id = ids[0]; } else if (ids.length) extra.uploads = ids;
    if (a.input === 'photos' && a.options) {
      if (st.opt.replace) extra.mode = 'replace';
      if (st.opt.in_order) extra.in_order = true;
      if (st.opt.crop) extra.crop = true;
      if (st.opt.credit) extra.credit = (VAL[key + '|credit'] || '').trim();
    }
    const d = await exec(a, extra, {btn: send, iid: it.iid, key: ctx.sheet ? null : key, keys});
    if (d) { keys.forEach(k => delete VAL[k]); delete P[key]; if (ctx.sheet) { ctx.done = true; back(); } }
  }
  return box;
}

function paste(f, key) {
  return btn('lnk', '붙여넣기', async () => {
    try { const t = await N.clipboard.readText(); if (t) { f.value = t; VAL[key] = t; } else throw 0; } catch (e) { f.focus(); toast('길게 눌러 붙여넣기'); }
  });
}

// ------------------------------------------------------------ 링크에서 사진 고르기 (10/18): AI 고른 것 미리 · 누르면 넣기·빼기 · 꾹 = 크게
const PS = {};
function sel(it) {
  const p = it.detail.pics, o = PS[it.iid];
  if (o && o.at === p.at) return o.m;
  return (PS[it.iid] = {at: p.at, m: new Map(p.items.filter(x => x.slot != null).map(x => [x.id, x.slot]))}).m;
}
function picks(it, ids) {
  const m = sel(it), xs = it.detail.pics.items.filter(x => m.has(x.id)).map(x => ({id: x.id, slot: m.get(x.id), thumb: x.thumb}))
    .sort((a, b) => !a.slot - !b.slot || a.slot - b.slot);
  return ids ? {pics: xs.map(x => ({id: x.id, slot: x.slot}))} : xs;
}
function hold(b, tap, long) {
  let t = 0, held = 0;
  const stop = () => clearTimeout(t);
  b.addEventListener('pointerdown', () => { held = 0; stop(); t = setTimeout(() => { held = 1; long(); }, 550); });
  for (const n of ['pointerup', 'pointerleave', 'pointercancel']) b.addEventListener(n, stop);
  b.addEventListener('contextmenu', e => { e.preventDefault(); if (!held) { stop(); held = 1; long(); } });
  b.addEventListener('click', () => { if (held) held = 0; else tap(); });
}
function tile(x, badge, tap) {
  const b = btn('pt' + (badge != null ? ' on' : ''), [el('img', {src: x.thumb, alt: x.why || '', draggable: 'false'}), badge != null ? el('b', {}, badge) : null], null,
    tap ? {'aria-pressed': badge != null ? 'true' : 'false'} : null);
  hold(b, tap || (() => big(x)), () => big(x));
  return b;
}
function big(x) {
  sheet(x.why || '사진', el('div', {class: 'big'}, el('img', {src: x.full, alt: x.why || ''}),
    el('p', {class: 'note'}, ['꾹 눌러 저장 · 복사', x.host && '출처 ' + x.host].filter(Boolean).join(' · ')), ext('mr', '새 창에서 열기', x.full)));
}
function picsBox(it) {
  const p = (it.detail || {}).pics, n = p && p.items.length;
  if (!p) return [];
  const ok = p.state === 'ready' || p.state === 'inserted';
  const out = [el('p', {class: 'h3'}, p.state === 'ready' && n ? `추천 사진 ${n}장` : p.state === 'inserted' ? '기사 사진 자동으로 넣음' : '링크에서 사진')];
  if (p.state === 'working' || fetching(it.iid)) return out.concat(el('p', {class: 'note'}, '사진 찾는 중…'));
  if (p.state === 'failed') out.push(el('p', {class: 'st warn'}, p.text || '사진을 찾지 못했어요'));
  if (p.state === 'inserted') out.push(el('p', {class: 'note'}, (p.text || '사진을 자동으로 넣었어요') + (n ? ` · 남은 후보 ${n}장` : '')));
  if (ok && n) {
    const m = sel(it), box = el('div');
    const draw = () => box.replaceChildren(el('p', {class: 'note'}, '누르면 넣기·빼기 · 꾹 누르면 크게'),
      el('div', {class: 'pk'}, p.items.map(x => tile(x, m.has(x.id) ? m.get(x.id) || '✓' : null, () => { if (!m.delete(x.id)) m.set(x.id, 0); draw(); }))),
      btn('pb', m.size ? `이대로 넣기 · ${m.size}장` : '넣을 사진을 눌러 주세요', async e => {
        if (await exec(p.put, picks(it, 1), {btn: e.currentTarget, iid: it.iid}) && SH.length) back();
      }, {disabled: !m.size}));
    draw(); out.push(box);
  }
  const x = (a, c) => a && btn(c, a.label, e => exec(a, {}, {btn: e.currentTarget, iid: it.iid}));
  out.push(n ? el('div', {class: 'tools'}, x(p.fetch, 'lnk'), x(p.clear, 'lnk mute')) : x(p.fetch, 'pb ghost'));
  return out;
}
// 블로그 탭 '링크에서 사진 뽑기' (웹은 한 번에 저장 불가 → 크게 열어 꾹)
function vPics() {
  const t = S.pics, out = [el('div', {class: 'top'}, el('h2', {}, '링크에서 사진 뽑기'), el('a', {class: 'lnk', href: '#/blog/' + (LS.g('blog') || 'finance')}, '블로그로'))];
  if (!t) return out.concat(el('p', {class: 'empty'}, MSG.unsupported));
  const a = t.fetch, k = 'pics|url', w = 'pics|want';
  const u = field(k, a, {type: 'text', inputmode: 'url', enterkeyhint: 'next'}), want = field(w, {placeholder: '원하는 사진 (선택) 예: 제품 정면'}, {maxlength: 100, enterkeyhint: 'go'});
  out.push(el('div', {class: 'box'}, el('div', {class: 'tools'}, paste(u, k)), u, want, btn('pb', a.label, async e => {
    const v = u.value.trim();
    if (!/^https?:\/\/\S+$/i.test(v)) { u.focus(); toast('링크를 넣어 주세요'); return; }
    if (await exec(a, {url: v, want: want.value.trim()}, {btn: e.currentTarget, iid: 'pics'})) { delete VAL[k]; delete VAL[w]; render(); }
  })));
  const busy = fetching('pics') && !t.jobs.some(j => j.state === 'working');
  if (busy) out.push(el('p', {class: 'note'}, '사진 찾는 중…'));
  if (!t.jobs.length && !busy) out.push(el('p', {class: 'empty'}, '아직 뽑은 사진이 없어요'));
  for (const j of t.jobs) {
    out.push(el('div', {class: 'box'}, el('div', {class: 'ih'}, el('b', {class: 'ti'}, j.host || '링크'), btn('lnk mute', j.clear.label, e => exec(j.clear, {}, {btn: e.currentTarget, iid: 'pics'}))),
      el('p', {class: 'st' + (j.state === 'failed' ? ' warn' : '')}, [j.want, j.state === 'working' ? '사진 찾는 중…' : j.text, ago(j.at)].filter(Boolean).join(' · ')),
      j.items.length ? [el('div', {class: 'pk'}, j.items.map(x => tile(x))), el('p', {class: 'note'}, '사진을 누르면 크게 · 꾹 눌러 저장')] : null));
  }
  return out;
}

// 구글 검색 등록 (주인 10/18 22:20 · 10/19 '그냥 복사하기 버튼으로'): 주소 복사만, 등록함은 확인 없이 (다시 누르면 되돌림)
const CHK = {}, isOn = x => x.auto || (x.url in CHK ? CHK[x.url] : x.done);
async function mark(urls, on) {
  urls.forEach(u => { CHK[u] = on; }); render();
  const ok = await exec({action: 'search.check', args: {urls, done: on}});
  await loading;  // 서버 값이 오면 화면 표시는 지운다 (다른 기기에서 바꾼 것도 보이게)
  urls.forEach(u => delete CHK[u]); render();
  if (ok) toast(on ? '등록함으로 표시했어요' : '되돌렸어요', '되돌리기', () => mark(urls, !on));
}
function vSearch() {
  const s = S.search, out = [el('div', {class: 'top'}, el('h2', {}, '구글 검색 등록'), el('a', {class: 'lnk', href: '#/blog/' + (LS.g('blog') || 'finance')}, '블로그로'))];
  if (!s || !s.days.length) return out.concat(el('p', {class: 'empty'}, '새로 발행한 글이 아직 없어요'));
  out.push(el('p', {class: 'note'}, '주소를 키자드에 붙여넣고 등록함을 눌러 주세요 · 다음은 할 일 없어요'));
  for (const g of s.days) {
    const left = g.items.filter(x => !isOn(x)).map(x => x.url);
    out.push(el('div', {class: 'top hs'}, el('p', {class: 'h3'}, `${g.label} · ${left.length ? left.length + '개 남음' : '다 했어요'}`),
      left.length ? btn('lnk', '전부 복사', () => copy(left.join('\n'), `${left.length}개 복사했어요 — 키자드에 붙여넣기 하세요`)) : null,
      left.length > 1 ? btn('lnk mute', '모두 등록함', () => mark(left, true)) : null),
    el('div', {class: 'box'}, g.items.map(x => {
      const on = isOn(x);
      return el('div', {class: 'vi' + (on ? ' ok' : '')}, el('span', {class: 'vn'}, x.chip), el('span', {class: 'vt'}, x.title),
        el('div', {class: 'va k'}, btn('sm' + (on ? '' : ' ac'), '복사하기', () => copy(x.url, '주소를 복사했어요 — 키자드에 붙여넣기 하세요')),
          x.auto ? el('span', {class: 'cp ok'}, '자동 등록됨') : btn('sm', on ? '등록함 ✓' : '등록함', () => mark([x.url], !on), {'aria-pressed': on ? 'true' : 'false'})));
    })));
  }
  return out;
}

// ------------------------------------------------------------ 시트들: 더보기 · 자세히 · 고르기 · 찾기 · 정리
function moreSheet(it, more) {
  const box = el('div');
  sheet(it.title || '더보기', box);
  box.append(...more.map(a => a.input === 'open' ? ext('mr', a.label, a.url)
    : btn('mr' + (a.danger ? ' red' : ''), a.label, () => { history.back(); setTimeout(() => act(it, a, null, false), 60); })));
}
function choiceSheet(a) {
  const box = el('div');
  sheet(a.title || a.label, box);
  box.append(a.note ? el('p', {class: 'note'}, a.note) : '', ...(a.choices || []).map(c => btn(c.primary ? 'pb' : 'mr', c.label, () => { back(); exec(a, {name: c.value}); })));
}
function whenSheet(a) {  // 10/19: 예약 시간 바꾸기 — 시각 + 오늘/내일
  const f = el('input', {class: 'fl', type: 'time', value: a.value}), K = ['today', 'tomorrow'];
  let day = a.day;
  const tg = K.map((k, i) => btn('tg', i ? '내일' : '오늘', () => { day = k; tg.forEach((b, j) => b.setAttribute('aria-pressed', '' + (j === i))); }, {'aria-pressed': '' + (day === k)}));
  sheet(a.label, el('div', {}, el('div', {class: 'row'}, ...tg), f, btn('pb', '바꾸기', () => { if (f.value) { back(); exec(a, {at: f.value, day}); } })));
}
async function searchSheet(a) {
  const box = el('div'), out = el('div');
  sheet(a.label, box);
  const show = async q => {
    out.replaceChildren(el('p', {class: 'note'}, '찾는 중…'));
    const r = await api(a.url + (q != null ? '?q=' + encodeURIComponent(q) : '?n=10'));
    const xs = (r.data && r.data.items) || [];
    out.replaceChildren(...(xs.length ? xs.map(x => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, x.title),
      el('span', {class: 's'}, [x.where, x.recommend != null ? `추천 ${x.recommend}` : '', ago(x.at)].filter(Boolean).join(' · '))),
      x.url ? ext('sm', '열기', x.url) : null)) : [el('p', {class: 'empty'}, r.status === 200 ? '찾은 글이 없어요' : (r.data.text || MSG.failed))]));
  };
  if (a.action === 'deal.find') {
    const f = el('input', {class: 'fl', type: 'search', placeholder: '찾을 낱말', maxlength: 40, enterkeyhint: 'search'});
    box.append(el('form', {onsubmit: e => { e.preventDefault(); if (f.value.trim()) show(f.value.trim()); }}, f, el('button', {class: 'pb', type: 'submit'}, '찾기')), out);
    f.focus();
  } else { box.append(out); show(null); }
}
function infoSheet(text) {
  sheet('보기', el('div', {}, (text || '').split('\n').filter(Boolean).map(t => el('p', {text: t}))));
}
async function openItem(iid, it) {
  if (/^digest-/.test(iid)) { digestSheet(iid); return; }
  if (iid === 'search') { go('#/blog/search'); return; }
  if (iid === 'visits') { const v = vis(((S.todo.find(x => x.iid === iid) || {}).detail || {}).visits); v.length ? visitSheet(v, 1) : toast('들를 이웃 글이 없어요'); return; }
  it = it || S && (S.todo.find(x => x.iid === iid) || Object.values(S.blogs || {}).flatMap(b => b.waiting || []).find(x => x.iid === iid));
  const box = el('div');
  const long = iid === 'sns-replies' || (it && it.kind === 'review_info') || (it && it.detail && (it.detail.questions || []).length);
  if (!it) {
    const r = await api('/dashboard/api/item/' + encodeURIComponent(iid));
    if (r.status !== 200) { toast(r.data.text || '이미 처리됐어요'); return; }
    it = r.data;
  } else if (long) {
    api('/dashboard/api/item/' + encodeURIComponent(iid)).then(r => { if (r.status === 200 && D.contains(box)) box.replaceChildren(...detail(r.data)); });
  }
  sheet(it.title, box);
  box.append(...detail(it));
  const card = $('i-' + iid);
  if (card) { card.classList.remove('hl'); void card.offsetWidth; card.classList.add('hl'); }
}
function detail(it) {
  const d = it.detail || {}, out = [];
  const more = (it.more || []).filter(a => !a.disabled);
  out.push(el('div', {class: 'ih'}, chip(it), d.owner_edited ? el('span', {class: 'cp info'}, '직접 고친 글') : null),
    it.status && !(d.lines || []).some(l => l.startsWith(it.status)) ? el('p', {class: 'st'}, it.status) : null);
  if (d.how) out.push(el('p', {text: d.how}));
  for (const [k, l] of [['search'], ['short', '짧게']]) if (d[k]) out.push(el('div', {class: 'kv'}, l ? el('span', {class: 'note'}, l) : null, el('code', {text: d[k]}), btn('sm', '복사', () => copy(d[k]))));
  if (d.note) out.push(el('p', {class: 'note'}, d.note));
  if (d.reason) out.push(el('p', {text: d.reason}));
  if (d.replaced_by) out.push(el('p', {text: `${d.replaced_by} 딜로 바뀌었어요`}));
  if ((d.lines || []).length) out.push(el('ul', {class: 'ls'}, d.lines.map(t => el('li', {text: t}))));
  const pr = d.photo_request;
  if (pr && (pr.must || []).length) out.push(el('p', {class: 'h3'}, '필요한 사진'), el('ul', {class: 'ls'}, pr.must.map(t => el('li', {text: t}))));
  if (pr && (pr.links || []).length) out.push(el('div', {class: 'tools'}, pr.links.map(l => ext('lnk', l.label, l.url))));
  out.push(...picsBox(it));
  // 기사에서 찾은 인스타 게시물 · 넣기 = 인스타 링크 칸과 같은 blog.photos links
  if ((d.ig || []).length) out.push(el('p', {class: 'h3'}, '넣으면 좋을 인스타 게시물'), ...d.ig.map(g => el('div', {class: 'box'}, el('p', {text: g.text}),
    el('div', {class: 'tools'}, g.open && ext('lnk', '인스타 열기', g.open), g.src && ext('lnk mute', '기사', g.src), g.put && btn('lnk', g.put.label, e => exec(g.put, {}, {btn: e.currentTarget, iid: it.iid}))))));
  if ((d.slots || []).length) {
    out.push(el('p', {class: 'h3'}, '사진 자리'), el('div', {class: 'slots'}, d.slots.map(s => btn('slot' + (s.thumb ? '' : ' empty'),
      s.thumb ? [el('img', {src: s.thumb, alt: `자리 ${s.slot}`}), el('b', {}, s.slot)] : `자리 ${s.slot}: ${s.label || '빈 자리'}`,
      () => s.thumb ? slotView(it, s) : slotSheet(it, s)))),
      ED(it) ? btn('mr', '사진 한 번에 고치기 · 빼기·교체·옮기기·편집', () => edBatch(it)) : null,
      d.slots.some(s => s.thumb) ? el('p', {class: 'note'}, '사진을 누르면 올릴 원본 · 편집·교체·비우기') : null);
  }
  if ((d.questions || []).length) out.push(el('p', {class: 'h3'}, '질문'), ...d.questions.map(q => el('p', {class: 'q'}, `${q.n}. ${q.q}`)));
  if (d.preview) out.push(el('p', {class: 'h3'}, '올릴 글'), el('div', {class: 'dr'}, d.preview));
  if (d.photo_count) out.push(el('p', {class: 'note'}, `사진 ${d.photo_count}장`));
  for (const r of d.replies || []) {
    out.push(el('div', {class: 'box'}, el('p', {class: 'note'}, [r.who, ago(r.at)].filter(Boolean).join(' · ')), r.post ? el('p', {class: 'note'}, r.post) : null,
      el('p', {class: 'q'}, r.reply), ...(r.drafts || []).map(t => el('div', {class: 'dr'}, t, el('div', {}, btn('lnk', '복사', () => copy(t))))),
      r.open ? ext('lnk', '스레드에서 열기', r.open) : null));
  }
  if (d.replies && !d.replies.length) out.push(el('p', {class: 'empty'}, '답글 없어요'));
  if (!(it.primary && (it.primary.goto === '#/i/' + it.iid || it.primary.input === 'pics'))) out.push(el('div', {style: 'margin:12px 0'}, primary(it, it.primary)));
  for (const o of d.open || []) out.push(ext('mr', o.label, o.url));
  out.push(...more.map(a => a.input === 'open' ? ext('mr', a.label, a.url)
    : btn('mr' + (a.danger ? ' red' : ''), a.label, () => act(it, a, null, false))));
  return out;
}
const slotT = s => `자리 ${s.slot}${s.label ? ': ' + s.label : ''}`;
function slotActs(it, s, c, l1, l2) {
  const base = it.primary && it.primary.action === 'blog.photos' ? it.primary : (it.more || []).find(a => a.action === 'blog.photos');
  const args = {blog: it.blog, pid: Number(it.iid.split('-')[2])};
  const a = Object.assign({}, base || {action: 'blog.photos', args, input: 'photos', accept: 'image/*'}, {label: '이 자리 사진 바꾸기', multiple: false, options: null});
  a.args = Object.assign({}, a.args, {mode: 'slot', slot: s.slot});
  return [btn(c, l1, () => act(it, a, null, false)),
    s.thumb ? btn(c + ' red', l2, e => exec({action: 'blog.photo_clear', args: Object.assign({}, args, {slot: s.slot})}, {}, {btn: e.currentTarget, iid: it.iid})) : null];
}
function slotSheet(it, s) { sheet(slotT(s), el('div', {}, slotActs(it, s, 'mr', '이 자리 사진 바꾸기', '이 자리 비우기'))); }
// 사진을 누르면 먼저 올릴 그대로 (10/20): final(올릴 파일, 없으면 full) · 체크무늬 위 원래 비율(흰 여백이 보이게) · 픽셀 크기
function slotView(it, s) {
  const px = el('p', {class: 'note'}), im = el('img', {src: s.final || s.full || s.thumb, alt: slotT(s), onload: () => { px.textContent = `${im.naturalWidth} × ${im.naturalHeight}px`; }});
  sheet(slotT(s), el('div', {}, el('div', {class: 'ck'}, im), px,
    el('div', {class: 'two'}, ED(it) ? btn('pb soft', '편집', () => edPhoto(it, s)) : null, slotActs(it, s, 'pb soft', '교체', '비우기'))));
}
// 사진 한 번에 고치기·편집기 (주인 10/19): edit.js 는 처음 누를 때만 받는다 (app.js 를 가볍게) — 주소는 껍데기의 meta(?v=)
let EDJ = null;
const edLoad = () => EDJ = EDJ || new Promise((ok, bad) => {
  const m = D.querySelector('meta[name=sb-edit]'), s = el('script', {src: m ? m.content : '/dashboard/static/edit.js'});
  s.onload = () => W.SBEdit ? ok(W.SBEdit) : bad(); s.onerror = () => { EDJ = null; s.remove(); bad(); };
  D.head.append(s);
});
const ED = it => /^(finance|issue)$/.test(it.blog) && !pendingOf(it.iid);
async function edGo(it, fn) {
  let E;
  try { E = await edLoad(); } catch (e) { toast(MSG.net); return; }
  fn(E, {el, btn, sheet, back, pick, uploadAll, exec, toast, slots: it.detail.slots, iid: it.iid,
    args: {blog: it.blog, pid: Number(it.iid.split('-')[2])}});
}
const edBatch = it => edGo(it, (E, h) => E.batch(h));
// 사진을 누르면 바로 편집기 (10/20): 자르기·비율·돌리기·사람만 남기기 → 저장 (교체·비우기는 편집기 아래 링크)
const edPhoto = (it, s) => edGo(it, (E, h) => E.photo(h, s, () => slotSheet(it, s)));
// 발행 글 고치기 (10/21): 최근 글 → 사진 빼기·교체 · 문장 빼기 (edit.js fix)
const edFix = (k, r) => edGo({blog: k, iid: `post-${k}-${r.pid}`, detail: {}}, (E, h) => E.fix(h, r));
async function digestSheet(id) {
  const r = await api('/dashboard/api/digest/' + encodeURIComponent(id));
  if (r.status !== 200) { toast('정리가 아직 없어요'); return; }
  sheet(r.data.title || '오늘 정리', el('div', {}, (r.data.sections || []).map(s => [el('p', {class: 'h3'}, s.label), ...(s.sentences || []).map(t => el('p', {text: t}))])));
}
async function copy(t, m = '복사했어요', a, f) {
  try { await N.clipboard.writeText(t); toast(m, a, f); } catch (e) {
    const x = el('textarea', {style: 'position:fixed;opacity:0'}); x.value = t; D.body.append(x); x.select();
    try { D.execCommand('copy'); toast(m, a, f); } catch (y) { toast('길게 눌러 복사해 주세요'); }
    x.remove();
  }
}

// ------------------------------------------------------------ 탭: 할 일
function banner() {
  const b = S.banner;
  if (b && b.kind === 'nowhere') return el('div', {class: 'bn bad'}, el('span', {}, b.text), el('a', {class: 'pb soft', href: '#/connect'}, '연결'));
  if (thisOn() || Number(LS.g('plater') || 0) > Date.now()) return null;
  const later = btn('lnk', '나중에', () => { LS.s('plater', Date.now() + 30 * 864e5); render(); });
  if (IOS && !STANDALONE) return el('div', {class: 'bn'}, el('span', {}, '알림을 받으려면: 공유 → 홈 화면에 추가 후 열어 주세요'), later);
  if (!PUSH || !S.me.push_key) return null;
  if (Notification.permission === 'denied') {
    if (LS.g('pdeny') && !DENY_ONCE) return null;
    DENY_ONCE = true; LS.s('pdeny', '1');
    return el('div', {class: 'bn'}, el('span', {}, '이 기기는 알림이 막혀 있어요'), el('a', {class: 'pb soft', href: '#/connect'}, '켜는 방법'), later);
  }
  return el('div', {class: 'bn'}, el('span', {}, '알림을 켜면 할 일이 바로 와요'), btn('pb', '알림 켜기', pushOn), later);
}
function vTodo() {
  const all = S.todo.flatMap(i => i.kind !== 'visits' ? [i] : (n => n ? [Object.assign({}, i, {title: `오늘 들를 이웃 글 ${n}개`})] : [])(vis(i.detail.visits).length)), out = [];
  let items = R.sub === 'deal' ? all.filter(i => i.src === 'deal') : all, k = 0, rest = 0;
  if (R.sub !== 'deal' && !ALL) items = items.filter(i => i.kind !== 'link_request' || ++k <= 3 || !++rest);
  const b = banner(); if (b) out.push(b);
  if (R.sub === 'deal') out.push(el('div', {class: 'top'}, el('span', {class: 'sub'}, `딜만 보는 중 · ${items.length}개`), el('a', {class: 'lnk', href: '#/todo'}, '전체 보기')));
  if (!items.length) out.push(doneRow(), el('p', {class: 'empty'}, '할 일 없어요'));
  const n = ALL ? items.length : 10;
  const shown = items.slice(0, n), last = shown.map(i => i.kind).lastIndexOf('link_request');
  const more = rest ? el('a', {class: 'bn', href: '#/todo/deal'}, `링크 기다리는 딜 ${rest}개 더 →`) : null;
  const gs = new Set(shown.map(i => i.group)).size > 1;
  let g = null;
  shown.forEach((x, i) => { if (gs && x.group !== g) out.push(el('p', {class: 'grp'}, g = x.group)); out.push(item(x)); if (more && i === last) out.push(more); });
  if (more && last < 0) out.push(more);
  if (items.length > n) out.push(btn('pb ghost', `더 보기 (${items.length - n})`, () => { ALL = true; render(); }));
  if (items.length) out.push(doneRow());
  if (S.digest && S.digest.latest) out.push(el('p', {class: 'foot'}, btn('lnk', '오늘 정리 보기', () => digestSheet(S.digest.latest))));
  return out;
}

// ------------------------------------------------------------ 탭: 딜
function vDeal() {
  const d = S.deal, st = d.status, n = S.todo.filter(i => i.src === 'deal').length, seg = LS.g('dseg') || 'today';
  const pseudo = {iid: 'deal-tab', title: '딜'};
  const out = [el('div', {class: 'top'}, el('span', {class: 'cp ' + (st.paused ? 'stop' : 'ok')}, st.paused ? '멈춤' : st.dry_run ? '연습' : '정상'),
    el('span', {class: 'sub'}, `오늘 ${st.today}개 올림`), (d.actions || []).length ? btn('lnk', '더보기', () => moreSheet(pseudo, d.actions)) : null)];
  if (n) out.push(el('a', {class: 'bn', href: '#/todo/deal'}, `할 일에 딜 ${n}개 →`));
  if (d.nolink) out.push(el('p', {class: 'note'}, btn('lnk mute', d.nolink.label, () => choiceSheet(d.nolink))));
  out.push(el('div', {class: 'seg', role: 'group'}, [['today', '오늘 올림'], ['queue', '대기열']].map(([k, l]) =>
    btn('', l, () => { LS.s('dseg', k); render(); }, {'aria-pressed': seg === k ? 'true' : 'false'}))));
  const rows = seg === 'queue' ? d.queue : d.today;
  if (!rows.length) out.push(el('p', {class: 'empty'}, seg === 'queue' ? '기다리는 딜 없어요' : '오늘 올린 딜이 아직 없어요'));
  else out.push(el('div', {class: 'box'}, rows.map(r => el('div', {class: 'row'},
    el('div', {class: 'grow'}, el('span', {class: 't'}, r.title), el('span', {class: 's'}, [[r.shop && `[${r.shop}]`, r.price].filter(Boolean).join(' '), seg === 'today' ? hhmm(r.at) : ''].filter(Boolean).join(' · '))),
    r.photo_wait ? btn('sm', `사진 기다림 · ${hhmm(r.photo_wait.until)}까지`, () => openItem(r.photo_wait.iid))
      : seg === 'today' ? dealLinks(r) : null))));
  return out;
}

// 같은 딜이 간 곳 (채널·핫딜 블로그·인스타·스레드) — 아는 주소만, 휴대폰에선 넘치면 둘째 줄로
const DEAL_LINKS = [['url', '채널'], ['blog_url', '블로그'], ['ig_url', '인스타'], ['th_url', '스레드']];
function dealLinks(r) {
  const a = DEAL_LINKS.filter(([k]) => r[k]).map(([k, l]) => ext('sm', l, r[k]));
  return a.length ? el('span', {class: 'dl'}, a) : null;
}

// ------------------------------------------------------------ 탭: 블로그
const IDEA = ['blog.scan', 'blog.issue', 'blog.hotdeal_now'];
// 블로그 이름 옆 할 일 수 (10/20): '할 일' 탭과 같은 줄을 그 블로그만 — 하는 중·끝난 줄은 빼서 바로 줄어든다 (0 이면 숨김)
const todoN = k => S.todo.reduce((n, i) => n + (i.kind === 'visits' ? +vis(i.detail.visits).some(x => x.blog === k) : +(i.blog === k && !pendingOf(i.iid))), 0);
// Pick 인기 검색어 (10/20 — Blog-Auto: 구글 트렌드 · Pick 후보 · 뉴스 낱말): 순위 ▲▼ · 이미 쓴 글 · 패션/연예 · 누르면 그 키워드로 Pick 쓰기
const TSRC = {advisor: '어드바이저', google: '구글', pick: 'Pick', news: '뉴스', early: '급상승'}, TTAG = {fashion: '패션', ent: '연예'};
function trendBox(t) {
  const all = OPEN.trends, mv = x => x.new ? el('i', {class: 'up'}, 'NEW') : x.change ? el('i', {class: x.change > 0 ? 'up' : 'dn'}, (x.change > 0 ? '▲' : '▼') + Math.abs(x.change)) : el('i', {}, '–');
  return [el('div', {class: 'top hs'}, el('p', {class: 'h3'}, '인기 검색어'), el('span', {class: 'note'}, ago(t.at))),
    el('div', {class: 'box'}, t.items.slice(0, all ? 20 : 10).map(x => el('div', {class: 'row tr'}, el('b', {}, x.rank), mv(x),
      btn('grow tw', [el('span', {class: 't'}, x.keyword), el('span', {class: 's'}, [x.sources.map(k => TSRC[k]).join('·'), TTAG[x.tag]].filter(Boolean).join(' · '))],
        e => trendGo(t, x, e), {disabled: !t.write || Object.values(POLL).some(p => p.args.keyword === x.keyword && p.action === t.write.action) || null}), x.badge ? el('span', {class: 'cp hot'}, x.badge) : null, x.written ? el('span', {class: 'cp ok'}, '이미 쓴 글 있음') : null)),
    t.items.length > 10 ? btn('fold', all ? '10위까지만' : `20위까지 보기`, () => { OPEN.trends = !all; render(); }) : null)];
}
// 누르면 바로 쓰기 (10/20 — 확인 창 없이) · 쓰는 동안 그 줄은 못 누름 (POLL — 두 번 눌러 두 편 쓰지 않게)
async function trendGo(t, x, e) {
  if (await exec(t.write, {keyword: x.keyword}, {btn: e.currentTarget})) toast(`「${x.keyword}」 글 쓰기 시작했어요`);
}
function vBlog() {
  const key = R.sub, b = S.blogs[key];
  const out = [el('div', {class: 'seg', role: 'group'}, BLOGS.map(([k, l]) => { const n = todoN(k);
    return btn('', [l, n ? el('span', {class: 'cp hot', 'aria-label': `할 일 ${n}개`}, n) : null], () => { location.hash = '#/blog/' + k; }, {'aria-pressed': k === key ? 'true' : 'false'}); }))];
  if (key === 'pics') return out.concat(vPics());
  if (key === 'search') return out.concat(vSearch());
  if (!b) return out;
  const acts = b.actions || [], pseudo = {iid: 'blog-' + key, title: b.label};
  const idea = acts.filter(a => IDEA.includes(a.action) || (a.action === 'blog.info' && a.args.what === 'trends'));
  const write = acts.find(a => /^blog\.(write|post_notes)$/.test(a.action)), seed = acts.find(a => a.action === 'blog.topic_seed');
  const more = acts.filter(a => !idea.includes(a) && a !== write && a !== seed);
  out.push(el('div', {class: 'top'}, el('span', {class: 'sub'}, `${b.mode === 'publish' ? '발행 모드' : '초안 모드'} · 오늘 ${b.today}편${b.paused ? ' · 멈춤' : ''}`),
    b.login === 'needed' ? el('a', {class: 'lnk bad', href: '#/connect'}, '로그인 필요') : null, more.length ? btn('lnk', '더보기', () => moreSheet(pseudo, more)) : null));
  if (S.pics) out.push(el('a', {class: 'bn', href: '#/blog/pics'}, '링크에서 사진 뽑기 →'));
  const sr = S.search;
  if (sr && sr.days.length) out.push(el('a', {class: 'bn', href: '#/blog/search'}, sr.left ? `구글 검색 등록 ${sr.left}개 →` : '구글 검색 등록 →'));
  out.push(el('p', {class: 'h3'}, '기다리는 글'));
  const sc = b.schedule, want = sc ? 'blog.schedule' : 'blog.publish';
  const pubs = b.waiting.filter(x => x.primary && x.primary.action === want && !x.primary.disabled && x.kind !== 'sched_blocked' && !pendingOf(x.iid));
  if (pubs.length > 1) {
    out.push(sc ? btn('pb soft', `대기 ${pubs.length}편 간격 두고 발행`, e => exec({action: want, args: {blog: key, pids: pubs.map(x => x.primary.args.pids[0])}},
      {}, {btn: e.currentTarget}), {style: 'margin-bottom:12px'}) : btn('pb soft', `발행 대기 ${pubs.length}편 한 번에`, () => pubAll(pubs), {style: 'margin-bottom:12px'}));
  }
  out.push(...(b.waiting.length ? b.waiting.map(x => item(x, true)) : [el('p', {class: 'empty'}, '기다리는 글 없어요')]));
  if (sc && sc.count) {
    out.push(el('div', {class: 'top hs'}, el('p', {class: 'h3'}, `예약된 글 ${sc.count}편`),
      sc.cancel_all ? btn('lnk mute', sc.cancel_all.label, e => exec(sc.cancel_all, {}, {btn: e.currentTarget})) : null));
    out.push(...sc.items.map(x => item(x, true)));
  }
  if (b.trends && b.trends.items.length) out.push(...trendBox(b.trends));
  if (b.learn) out.push(...learnBox(b.learn));
  if (idea.length || (b.scan && b.scan.items.length)) {
    out.push(el('p', {class: 'h3'}, '글감'));
    const box = el('div', {class: 'box'});
    if (b.scan) box.append(...b.scan.items.map(x => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, x.title), x.why ? el('span', {class: 's'}, x.why) : null),
      x.done ? el('span', {class: 'cp info'}, '씀') : btn('sm', '이걸로 쓰기', e => exec(x.action, {}, {btn: e.currentTarget})))));
    else if (key === 'finance') box.append(el('p', {class: 'note'}, '오늘 글감이 아직 없어요'));
    for (const a of idea) {
      if (a.action === 'blog.issue') box.append(panel(pseudo, a, pk(pseudo.iid, a), {fixed: true}));
      else box.append(el('div', {style: 'margin-top:8px'}, btn(a.action === 'blog.info' ? 'pb ghost' : 'pb soft', a.label, e => exec(a, {}, {btn: e.currentTarget}))));
    }
    out.push(box);
  }
  if (write) {
    const w = {iid: 'blog-' + key + '-write', title: write.label};
    out.push(el('div', {class: 'box', style: 'margin-top:12px'}, OPEN[w.iid] ? panel(w, write, pk(w.iid, write), {}) : btn('pb soft', write.label, () => { OPEN[w.iid] = pk(w.iid, write); render(); })));
  }
  // 소재 넣기 (10/21): 카페 인기글 주제·아이디어 → 다음 글감의 첫 후보 (글 칸 + 보내기)
  if (seed) { const s = {iid: 'blog-' + key + '-seed', title: seed.label}; out.push(el('p', {class: 'h3'}, '소재 넣기'), el('div', {class: 'box'}, panel(s, seed, pk(s.iid, seed), {fixed: true}))); }
  if (b.recent.length) {
    const open = OPEN['recent-' + key];
    out.push(el('div', {class: 'box', style: 'margin-top:12px'}, btn('fold', `최근 글 ${b.recent.length}개`, () => { OPEN['recent-' + key] = !open; render(); }, {'aria-expanded': open ? 'true' : 'false'}),
      open ? b.recent.map(r => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, r.title), el('span', {class: 's'}, ago(r.at))),
        ...fbBtns(r), r.url ? ext('sm', '네이버', r.url) : null, r.edit ? btn('sm', '고치기', () => edFix(key, r)) : null,
        r.google || r.daum || r.share ? btn('lnk mute', '더보기', () => submitSheet(r)) : null)) : null));
  }
  const vs = vis(b.visits);
  if (b.neighbors || vs.length) {
    out.push(el('p', {class: 'h3'}, '이웃'), b.neighbors ? el('p', {class: 'note nb'}, b.neighbors.split(' · ').map((t, i) => [i ? ' · ' : '', el('span', {}, t)])) : null);
    if (vs.length) out.push(el('div', {class: 'box'}, el('p', {class: 'vh'}, `오늘 들를 이웃 글 ${vs.length}개`), vs.slice(0, 3).map(x => visitRow(x)),
      vs.length > 3 ? btn('pb ghost', `모두 보기 (${vs.length})`, () => visitSheet(vs)) : null));
  }
  return out;
}

// 배운 것으로 나아지기 (10/20 — Blog-Auto learn.py): 이번 주 개선안 ✅ 적용 / ❌ 빼기 · 적용 중인 규칙 지우기 · 최근 글 👍/👎
const LA = {style: '문체', title: '제목', photo: '사진', tags: '태그', topic: '글감', schedule: '발행 시간'};
const lgo = a => e => exec(a, {}, {btn: e.currentTarget});
function learnBox(l) {
  const ps = l.proposals.filter(p => p.state === 'open'), rs = l.rules;
  if (!ps.length && !rs.length && !l.lessons_week) return [];
  return [el('div', {class: 'top hs'}, el('p', {class: 'h3'}, '이번 주 개선안'), el('span', {class: 'note'}, `배운 것 ${l.lessons_week}개`)),
    el('div', {class: 'box'}, ps.length ? ps.map(p => el('div', {class: 'vi'}, el('span', {class: 'vn'}, LA[p.area] + (p.evidence ? ` · 근거 ${p.evidence}개` : '')),
      el('span', {class: 'vt'}, p.rule_text), p.examples.length ? el('span', {class: 's'}, '예: ' + p.examples.join(' · ')) : null,
      p.effect ? el('span', {class: 's'}, '→ ' + p.effect) : null,
      p.apply ? el('div', {class: 'va'}, btn('sm ac', p.apply.label, lgo(p.apply)), btn('sm', p.reject.label, lgo(p.reject))) : null))
      : el('p', {class: 'note'}, '이번 주 새 개선안은 없어요'),
    rs.length ? [el('p', {class: 'vh'}, `적용 중인 규칙 ${rs.length}개`), ...rs.map(r => el('div', {class: 'row'}, el('div', {class: 'grow'},
      el('span', {class: 't'}, r.rule_text), el('span', {class: 's'}, LA[r.area] + (r.by === 'auto' ? ' · 자동 적용' : ''))),
      r.remove ? btn('lnk mute', r.remove.label, lgo(r.remove)) : null))] : null,
    l.auto_apply_days ? el('p', {class: 'note'}, `답이 없으면 ${l.auto_apply_days}일 뒤 저절로 적용돼요`) : null)];
}
const fbBtns = r => r.fb ? [['up', '👍'], ['down', '👎']].map(([v, t]) => btn('sm', t, () => r.fb.vote === v ? exec(r.fb.act, {vote: 'none'}) : fbSheet(r, v),
  {'aria-pressed': r.fb.vote === v ? 'true' : 'false', 'aria-label': v === 'up' ? '좋았어요' : '아쉬워요'})) : [];
function fbSheet(r, v) {
  const f = el('input', {class: 'fl', maxlength: 80, placeholder: '한 줄 이유 (비워도 돼요)', autocomplete: 'off'});
  sheet(v === 'up' ? '👍 좋았던 점' : '👎 아쉬운 점', el('div', {class: 'cf'}, f, btn('pb', '보내기', () => { back(); exec(r.fb.act, {vote: v, reason: f.value.trim()}); })));
  f.focus();
}

// 오늘 들를 이웃 글: 남의 블로그는 누를 때만 열고, 댓글은 주인이 직접 (했어요 → visit_done)
const GONE = new Set(), vis = xs => (xs || []).filter(x => !GONE.has(x.id) && !pendingOf(x.id));
function visitRow(x, all) {
  const r = el('div', {class: 'vi'}, el('span', {class: 'vn'}, all ? `${x.name} · ${(BLOGS.find(b => b[0] === x.blog) || [])[1]}` : x.name),
    el('span', {class: 'vt'}, x.title), x.comment ? el('div', {class: 'dr'}, x.comment) : null,
    el('div', {class: 'va'}, x.comment ? Object.assign(ext('sm ac', '복사하고 열기', x.url), {onclick: () => { copy(x.comment, '복사했어요 — 글에서 붙여넣기 하세요'); }}) : ext('sm', '열기', x.url),
      btn('sm', '했어요', async e => {
        if (!await exec({action: 'blog.visit_done', args: {id: x.id}}, {}, {btn: e.currentTarget, iid: x.id})) return;
        GONE.add(x.id);
        const box = r.parentNode, sh = r.closest('.sh'); r.remove();
        const n = box ? box.querySelectorAll('.vi').length : 0;
        if (sh) { sh.querySelector('h2').textContent = `오늘 들를 이웃 글 ${n}개`; if (!n) box.prepend(el('p', {class: 'empty'}, '다 들렀어요')); }
        render();
      })));
  return r;
}
function visitSheet(xs, all) {
  sheet(`오늘 들를 이웃 글 ${xs.length}개`, el('div', {}, xs.map(x => visitRow(x, all)), el('p', {class: 'note'}, '글을 읽고 초안을 고쳐서 직접 달아 주세요')));
}

// 검색 등록: 구글은 주소 복사만 (주인 10/19)
function submitSheet(r) {
  const box = el('div');
  sheet(r.title, box);
  box.append(r.google ? btn('mr', '주소 복사하기', () => {
    N.clipboard.writeText(r.google.copy).then(() => toast('글 주소를 복사했어요 — 붙여넣고 등록하세요'), () => toast('길게 눌러 복사해 주세요: ' + r.google.copy));
  }) : null, r.daum ? ext('mr', '다음 확인', r.daum) : null, r.share ? btn('mr', '오픈채팅 문구 복사', () => copy(r.share, '복사했어요 — 오픈채팅에 붙여넣기 하세요')) : null,
  r.daum ? el('p', {class: 'note'}, '다음은 블로그를 한 번 등록해 두면 새 글이 알아서 들어가요') : null);
}

// 한 번에 발행: 확인 창은 제목 목록으로 한 번, 그다음 글마다 발행 요청
async function pubAll(xs) {
  if (!await confirmSheet({title: `${xs.length}편을 발행할까요?`, ok: '모두 발행',
    body: xs.map(x => '· ' + title(x)).join('\n') + '\n네이버 블로그에 바로 올라가요.'})) return;
  for (const x of xs) {
    const a = x.primary, body = {action: a.action, args: a.args, cid: 'c_' + rnd(), ctoken: null};
    let r = await api('/dashboard/api/action', {method: 'POST', body});
    if (r.status === 409 && r.data.ctoken) { body.ctoken = r.data.ctoken; r = await api('/dashboard/api/action', {method: 'POST', body}); }
    if (r.status === 202) POLL[r.data.id] = {iid: x.iid, action: a.action, args: a.args, t: Date.now()};
    else toast(r.data.text || MSG[r.data.code] || MSG.failed);
  }
  render(); poll();
}

// ------------------------------------------------------------ 탭: 통계 (서버가 그린 카드 조각 — 폼·스크립트 없음)
function vStats() {
  const box = el('div', {class: 'stx', onclick: statsClick});
  if (STATS) box.innerHTML = STATS; else box.append(el('p', {class: 'boot'}, '불러오는 중…'));
  return [box];
}
async function loadStats() {
  let r = null;
  try { r = await fetch('/dashboard/api/stats.html', {credentials: 'same-origin', cache: 'no-store'}); } catch (e) { r = null; }
  if (r && r.status === 401) { if (await login()) loadStats(); return; }
  if (r && r.ok) { STATS = await r.text(); if (R.tab === 'stats') render(); } else if (!STATS) toast(MSG.net);
}
function statsClick(e) {
  const b = e.target.closest('[data-action]');
  if (!b) return;
  const src = b.dataset.src;
  if (b.dataset.action === 'stats.refresh') { exec({action: 'stats.refresh', args: {src}}, {}, {btn: b}).then(d => d && loadStats()); return; }
  let fields = [];
  try { fields = JSON.parse(b.dataset.fields || '[]'); } catch (x) { fields = []; }
  const ins = fields.map(f => el('input', {class: 'fl', inputmode: 'numeric', pattern: '[0-9,]*', placeholder: f.label, 'data-m': f.metric, 'aria-label': f.label}));
  const day = el('input', {class: 'fl', type: 'date', value: b.dataset.today || '', max: b.dataset.today || null, 'aria-label': '입력하는 날'});
  const box = el('div');
  sheet(b.dataset.title || '직접 넣기', box);
  box.append(...ins.map((x, i) => [el('label', {class: 'lab'}, fields[i].label), x]), el('label', {class: 'lab'}, '입력하는 날'), day,
    btn('pb', '저장', async ev => {
      const values = {};
      ins.forEach(x => { if (x.value.trim()) values[x.dataset.m] = x.value.replace(/[^0-9]/g, ''); });
      if (!Object.keys(values).length) { toast('숫자를 넣어 주세요'); return; }
      const d = await exec({action: 'stats.input', args: {src}}, {values, date: day.value}, {btn: ev.currentTarget});
      if (d) { back(); loadStats(); }
    }));
}

// ------------------------------------------------------------ 탭: 연결 (가끔 쓰는 설정)
function thisOn() { return !!(S && S.connect && S.connect.push.devices.some(d => d.this)); }
function denyHint() {
  return IOS ? '알림이 막혀 있어요 — 설정 › 알림 › Sns-board 에서 허용해 주세요'
    : /Android/.test(N.userAgent) ? '알림이 막혀 있어요 — 휴대폰 설정 › 애플리케이션 › Sns-board(또는 브라우저) › 알림에서 허용해 주세요'
      : '알림이 막혀 있어요 — 주소창 왼쪽 자물쇠 › 알림 › 허용';
}
function b64(s) {
  const p = (s + '='.repeat((4 - s.length % 4) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  return Uint8Array.from(atob(p), c => c.charCodeAt(0));
}
function sendSub() { const j = SUB.toJSON(); return api('/dashboard/api/push/subscribe', {method: 'POST', body: {endpoint: j.endpoint, keys: j.keys}}); }
async function pushOn() {
  if (!PUSH || !S.me.push_key) { toast('이 브라우저는 알림을 받을 수 없어요'); return; }
  const perm = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
  if (perm !== 'granted') { toast(denyHint()); render(); return; }
  try {
    REG = REG || await N.serviceWorker.ready;
    SUB = await REG.pushManager.getSubscription() || await REG.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64(S.me.push_key)});
  } catch (e) { toast('알림을 켜지 못했어요 — 다시 눌러 주세요'); return; }
  const r = await sendSub();
  toast(r.status === 200 ? '알림을 켰어요' : r.data.text || '알림을 켜지 못했어요');
  load();
}
async function pushOff(dev) {
  if (SUB) { const j = SUB.toJSON(); try { await SUB.unsubscribe(); } catch (e) { /* */ } SUB = null; await api('/dashboard/api/push/unsubscribe', {method: 'POST', body: {endpoint: j.endpoint}}); }
  if (dev) await api('/dashboard/api/push/unsubscribe', {method: 'POST', body: {id: dev.id}});
  toast('이 기기 알림을 껐어요'); load();
}
async function devOff(dev) {
  const body = {id: dev.id};
  let r = await api('/dashboard/api/push/unsubscribe', {method: 'POST', body});
  if (r.status === 409 && r.data.status === 'confirm') {
    if (!await confirmSheet(r.data)) return;
    body.ctoken = r.data.ctoken;
    r = await api('/dashboard/api/push/unsubscribe', {method: 'POST', body});
  }
  toast(r.status === 200 ? `${dev.label} 알림을 껐어요` : r.data.text || MSG.failed); load();
}
const sec = (title, moreActs, ...kids) => [el('div', {class: 'top', style: 'margin:20px 0 6px'}, el('h2', {}, title),
  moreActs && moreActs.length ? btn('lnk', '더보기', () => moreSheet({iid: 'conn', title}, moreActs)) : null), el('div', {class: 'box'}, ...kids)];
const row = (t, s, right, bad) => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, t), s ? el('span', {class: 's' + (bad ? ' bad' : '')}, s) : null), right);
const fold = (label, fn) => btn('fold', label, fn);
function listSheet(title, rows) { sheet(title, el('div', {}, rows.length ? rows : el('p', {class: 'empty'}, '없어요'))); }
// 오늘 한 일 (주인 10/18): 링크·사진·발행… 칸마다 새것 먼저 · 결과 칩 · 글·채널·상품 링크 (서버가 정한 주소만)
const RC = {ok: 'ok', run: 'info', bad: 'stop'};
const doneRow = () => el('div', {class: 'box', style: 'padding:0 14px;margin-top:12px'}, fold(`오늘 한 일 ${(S.counts || {}).done_today || 0}개`, todaySheet));
function todaySheet() {
  const box = sheet('오늘 한 일', el('p', {class: 'empty'}, '불러오는 중…')), h = box.parentNode.querySelector('h2');
  const fill = async b => {
    const r = await api('/dashboard/api/today' + (b ? '?day=yesterday' : '')), d = r.data || {}, w = b ? '어제' : '오늘';
    h.textContent = `${w} 한 일${d.count ? ' ' + d.count + '개' : ''}`;
    box.replaceChildren(...[r.status !== 200 ? el('p', {class: 'empty'}, d.text || MSG.net) : d.groups.length ? d.groups.map(g => [el('p', {class: 'grp'}, g.label),
      el('div', {class: 'box'}, g.rows.map(x => row(x.title, [el('span', {class: 'cp ' + RC[x.res]}, x.chip), ' ', [x.when, x.blog, x.title !== x.what && x.what, x.why || x.note].filter(Boolean).join(' · ')],
        x.link && ext('sm', x.link.label, x.link.url), x.res === 'bad')))]) : el('p', {class: 'empty'}, `${w} 한 일이 아직 없어요`),
    el('p', {class: 'foot'}, btn('lnk mute', b ? '오늘 보기' : '어제 보기', () => fill(!b)), S.connect && S.connect.audit.length ? btn('lnk mute', '접속 기록', () => listSheet('접속 기록', S.connect.audit.map(x => el('p', {text: x.text})))) : null)].flat(9));
  };
  fill(0);
}
function vConnect() {
  const c = S.connect, out = [], push = c.push, me = push.devices.find(d => d.this), others = push.devices.filter(d => !d.this);
  let s = me ? '켜짐' : '꺼짐', right = null;
  if (!S.me.push_key || !push.key_ok) s = '알림 키가 없어요 — 딜봇 점검이 필요해요';
  else if (IOS && !STANDALONE) s = '홈 화면에 추가한 앱에서 켤 수 있어요';
  else if (!PUSH) s = '이 브라우저는 알림을 받을 수 없어요';
  else if (!me && Notification.permission === 'denied') s = denyHint();
  else right = me ? btn('sm', '끄기', () => pushOff(me)) : btn('sm ac', '알림 켜기', pushOn);
  out.push(...sec('알림', null, row('이 기기', s, right),
    push.devices.length ? row('테스트 알림', '알림이 오는지 확인해요', btn('sm', '보내기', e => exec({action: 'push.test', args: {}}, {}, {btn: e.currentTarget}))) : null,
    others.map(d => row(d.label, d.last_ok ? `마지막 성공 ${ago(d.last_ok)}` : '아직 받은 적 없어요', btn('sm', '끄기', () => devOff(d)), !d.ok))));
  const k = c.kakao, KS = {ok: '연결됨', down: '끊김', expiring: `곧 만료${k.expires_in_days != null ? ` (D-${k.expires_in_days})` : ''}`, unknown: '확인 중'};
  const kAuth = (k.actions || []).find(a => a.action === 'blog.kakao_auth_url'), kConn = (k.actions || []).find(a => a.action === 'blog.kakao_connect');
  const kKid = {iid: 'conn-kakao', title: '카톡'};
  out.push(...sec('카톡 (예비)', (k.actions || []).filter(a => a !== kAuth && a !== kConn), row('카톡', KS[k.state] || KS.unknown,
    kAuth ? (KURL ? el('a', {class: 'sm ac', href: KURL, target: '_blank', rel: 'noopener noreferrer', onclick: () => { KSTEP = true; setTimeout(render, 300); }}, '카카오 열기 ↗')
      : btn('sm ac', kAuth.label, e => { KSTEP = true; exec(kAuth, {}, {btn: e.currentTarget, iid: 'conn-kakao'}); })) : null, k.state === 'down'),
  kConn && KSTEP ? panel(kKid, kConn, pk(kKid.iid, kConn), {fixed: true}) : null));
  const sns = S.sns, conn = (name, x) => row(name, x.text, x.fix ? el('a', {class: 'sm ac', href: x.connect_url}, '다시 연결') : null, x.state === 'broken');
  out.push(...sec('인스타 · 스레드', sns.actions, conn('인스타그램', sns.instagram), conn('스레드', sns.threads),
    sns.invites.length ? fold(`초대 링크 ${sns.invites.length}개`, () => listSheet('초대 링크', sns.invites.map(v => el('div', {class: 'box'},
      el('p', {class: 'q'}, v.label), v.where ? el('p', {class: 'note'}, v.where) : null, el('div', {class: 'kv'}, el('code', {text: v.url}), btn('sm', '복사', () => copy(v.url))))))) : null));
  const nv = c.naver, need = BLOGS.some(([b]) => nv[b] && nv[b].login === 'needed');
  out.push(...sec('네이버 로그인', null, BLOGS.map(([b, l]) => {
    const x = nv[b] || {};
    const t = {ok: '정상', needed: '필요', unknown: '모름'}[x.login] || '모름';
    return row(l, `${t}${x.checked_at ? ` · 확인 ${hhmm(x.checked_at)}` : ''}`, x.action ? btn('sm', '지금 확인', e => exec(x.action, {}, {btn: e.currentTarget, iid: 'conn-naver-' + b})) : null, x.login === 'needed');
  }), need ? el('p', {class: 'note'}, 'PC에서 다시 로그인해야 해요 — 끝나면 지금 확인') : null));
  const bd = c.bots.dealbot, ba = c.bots.blogauto, cols = bd.collectors || [];
  out.push(...sec('봇 상태', null, row('딜봇', `${bd.paused ? '멈춤' : '정상'}${bd.updated ? ` · ${bd.updated} 업데이트` : ''}`, null, bd.paused),
    row('블로그 프로그램', ba.ok ? `정상 · ${ago(ba.seen_at)} 신호` : `응답 없음${ba.seen_at ? ' · ' + ago(ba.seen_at) : ''}`, null, !ba.ok),
    cols.length ? fold(`수집기 ${cols.length}개`, () => listSheet('수집기', cols.map(x => row(x.label, `${x.enabled ? (x.ok ? '정상' : '오류') : '꺼짐'} · ${x.text}`, null, !x.ok)))) : null));
  out.push(el('div', {class: 'box', style: 'margin-top:20px'},
    c.errors.length ? fold(`오류 ${c.errors.length}개`, () => listSheet('오류', c.errors.map(x => row(x.where || '오류', `${hhmm(x.at)} · ${x.text}`)))) : row('오류', '없어요'),
    el('div', {class: 'row', style: 'padding:0'}, fold('오늘 한 일', todaySheet))));
  out.push(el('form', {method: 'post', action: '/dashboard/logout', class: 'foot', onsubmit: () => {
    try { N.serviceWorker && N.serviceWorker.controller && N.serviceWorker.controller.postMessage({type: 'logout'}); } catch (e) { /* */ }
    try { localStorage.clear(); } catch (e) { /* */ }
  }}, el('input', {type: 'hidden', name: 't', value: S.me.logout_t || ''}), el('button', {class: 'lnk mute', type: 'submit'}, '로그아웃')));
  return out;
}

// ------------------------------------------------------------ 시작
async function sw() {
  if (!('serviceWorker' in N)) return;
  try {
    REG = await N.serviceWorker.register('/dashboard/sw.js', {scope: '/dashboard/'});
    if (REG.waiting) REG.waiting.postMessage({type: 'skip'});
    if (PUSH) SUB = await REG.pushManager.getSubscription();
  } catch (e) { REG = null; }
  N.serviceWorker.addEventListener('message', e => e.data && e.data.go && go(e.data.go));
  after();
}
function ptr() {
  const bar = $('ptr');
  let y0 = null;
  D.addEventListener('touchstart', e => { y0 = scrollY <= 0 && !SH.length ? e.touches[0].clientY : null; }, {passive: true});
  D.addEventListener('touchmove', e => { if (y0 == null) return; const on = e.touches[0].clientY - y0 > 80; bar.classList.toggle('on', on); bar.textContent = on ? '놓으면 새로고침' : ''; }, {passive: true});
  D.addEventListener('touchend', () => {
    if (y0 != null && bar.classList.contains('on')) { bar.textContent = '새로고침 중…'; refresh().then(() => bar.classList.remove('on')); }
    y0 = null;
  });
}
function refresh() { if (R.tab === 'stats') loadStats(); return load(); }
function start() {
  try { S = JSON.parse($('boot').textContent); } catch (e) { S = null; }
  $('rf').addEventListener('click', refresh);
  W.addEventListener('hashchange', route);
  D.addEventListener('visibilitychange', () => D.visibilityState === 'visible' && Date.now() - PICK > 120000 && load());
  setInterval(() => { if (D.visibilityState === 'visible' && (R.tab === 'todo' || Object.keys(POLL).length)) load(); }, 60000);
  ptr();
  route();
  if (!S) load(); else setTimeout(load, 400);
  sw();
}
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
