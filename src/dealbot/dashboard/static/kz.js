/* 키자드 (주인 11/12 '키자드 도구를 대시보드 화면으로'): Blog-Auto tools/keyzard_indexer 를 /dashboard/keyzard/api/… 로 (대시보드 로그인 그대로).
   블로그 탭 각 블로그 맨 위 '키자드' 칸 · SNS › 룩북 아래 '룩북 수집'·'코디 레시피'·'코디 트렌드'.
   키자드 등록은 폰에서 직접 (주인 11/13·11/16 — Cloudflare 사람 확인이 서버 브라우저에서 안 넘어감, 우회 금지): 서버 등록·서버 로그인 단추 없음,
   주소 복사 → keyzard.cc/nb 열기만. 수집은 사람이 단추를 누를 때만 — 이 파일은 스스로 POST 하지 않는다. 글은 textContent 로만.
   logins.js 처럼 셸이 defer 로 받고, app.js 가 #main 을 다시 그리면 같은 칸을 다시 붙인다. 예전 화면은 '예전 화면' 링크. */
'use strict';
(() => {
const D = document, W = window, P = '/dashboard/keyzard/', OLD = P;
const CSS = '.kz .row{flex-wrap:wrap;row-gap:6px}.kz .row .grow{flex:1 1 60%}.kz .row .s{overflow-wrap:anywhere}.kz .sm[disabled],.kz .pb[disabled]{opacity:.45}' +
  '.kz .msg{margin:8px 0 0;font-size:14px;white-space:pre-line}.kz .msg.bad{color:var(--red)}.kz .top{margin:0 0 4px;min-height:36px}.kz .top b{flex:1;font-size:15px}' +
  '.kz .two{display:grid;grid-template-columns:1fr 1fr;gap:12px}.kz ol{margin:4px 0 0;padding-left:20px;font-size:14px;line-height:1.7}.kz .pb{margin-top:10px}';
// 대시보드 블로그 ↔ 도구 블로그(b1~b3): 도구가 알려 주는 블로그 주소(blog_home = RSS 주소에서)의 네이버 아이디로 — Blog-Auto config/<블로그>.yaml blog_id
const NID = {derenlog: 'finance', derenpick: 'issue', demiyum: 'hotdeal'}, NAME = {finance: '드라마', issue: '데런픽', hotdeal: '라커룸'};
const OTHER = /^(pics|search|trial|ptrial|sched)$/, SNS = ['threads', 'x', 'lookbook', 'shorts'];
const KZ = 'https://keyzard.cc/nb', PHONE = '키자드는 폰에서 직접 등록해요 — 주소 복사 → keyzard.cc/nb';
// 홈 화면 앱(아이패드 standalone)이면 사파리로 (routine.js sf 처럼 x-safari-https)
const sf = u => navigator.standalone === true ? 'x-safari-' + u : u;
const ST = {d: null, at: 0, err: '', busy: false}, LK = {d: null, at: 0, err: ''}, NV = {d: null, at: 0, err: ''}, RC = {d: null, at: 0, err: '', all: false};
const MSG = {};  // 칸마다 마지막 결과 한 줄 {text, bad}
let WORK = '', tt = null;

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
const old = () => el('a', {class: 'lnk mute', href: OLD, target: '_blank', rel: 'noopener'}, '예전 화면');
const when = s => typeof s === 'string' && s.length >= 16 ? s.slice(5, 16).replace('T', ' ') : (s || '-');
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text}));
  t.hidden = false;
  clearTimeout(tt);
  tt = setTimeout(() => { t.hidden = true; }, 4000);
}

// 부르기 — 던지지 않는다. {ok, d, msg} (msg = 짧은 한국어)
async function kz(path, body, method) {
  let r;
  try {
    r = await fetch(P + 'api/' + path, {method: method || (body === undefined ? 'GET' : 'POST'), credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  } catch (e) { return {ok: false, net: true, msg: '키자드 도구에 닿지 못했어요 — 잠시 뒤 다시'}; }
  let d = null;
  try { d = await r.json(); } catch (e) { d = null; }
  if (r.ok && d) return {ok: true, d};
  if (r.status === 401) return {ok: false, msg: '다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요'};
  const t = d && typeof d.detail === 'string' ? d.detail.slice(0, 140) : '';
  return {ok: false, msg: t || (r.status >= 500 || r.status === 404 ? '키자드 도구가 응답하지 않아요 — 잠시 뒤 다시' : `하지 못했어요 (${r.status})`)};
}
async function get(o, path, max) {
  if (o.busy || (o.at && Date.now() - o.at < max)) return;
  o.busy = true;
  const r = await kz(path);
  o.busy = false; o.at = Date.now();
  if (r.ok) { o.d = r.d; o.err = ''; } else o.err = r.msg;
  draw();
}
const status = async (force) => {
  if (force) ST.at = 0;
  await get(ST, 'status', 30000);
};
async function copy(t, m) {
  try { await navigator.clipboard.writeText(t); toast(m); return; } catch (e) { /* 아래로 */ }
  const a = el('textarea', {readonly: true, style: 'position:fixed;top:0;left:0;opacity:0'});
  a.value = t; D.body.append(a); a.select();
  let ok = false;
  try { ok = D.execCommand('copy'); } catch (e) { ok = false; }
  a.remove();
  toast(ok ? m : '복사하지 못했어요 — 주소를 꾹 눌러 복사해 주세요');
}

// 단추 — 사람이 누를 때만. 한 번에 하나 (WORK = 지금 하는 일 글)
async function act(slot, work, path, body, done, ask) {
  if (WORK) return toast('다른 키자드 일을 하는 중이에요 — 끝나고 다시');
  if (ask && !W.confirm(ask)) return;
  WORK = work; MSG[slot] = {text: work}; draw();
  let r;
  try { r = await kz(path, body || {}); } catch (e) { r = {ok: false, msg: '하지 못했어요'}; }
  WORK = '';
  let m;
  try { m = r.ok ? done(r.d) : {text: r.net ? '결과를 받지 못했어요 — 서버에서는 계속할 수 있어요. 잠시 뒤 다시 보기' : r.msg, bad: true}; } catch (e) { m = {text: '결과를 읽지 못했어요', bad: true}; }
  MSG[slot] = m;
  toast(m.text.split('\n')[0]);
  ST.at = LK.at = NV.at = 0;
  draw(); status(); look();
}

// -- 어느 화면인지 (app.js 와 같은 규칙: 블로그 고른 것·SNS 칸은 localStorage)
const LSG = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
const parts = () => (location.hash || '#/todo').replace(/^#\/?/, '').split('/');
function blogKey() {
  const p = parts();
  if (p[0] !== 'blog' || OTHER.test(p[1] || '')) return null;
  return NAME[p[1]] ? p[1] : (NAME[LSG('blog')] ? LSG('blog') : 'finance');
}
function snsLook() {
  const p = parts();
  return p[0] === 'sns' && (p[1] === 'lookbook' || (!SNS.includes(p[1]) && LSG('sns') === 'lookbook'));
}
function keyOf(b) {
  const m = /blog\.naver\.com\/([\w-]+)/i.exec(String(b.blog_home || ''));
  if (m && NID[m[1].toLowerCase()]) return NID[m[1].toLowerCase()];
  const n = String(b.name || '');
  return n.includes('드라마') || n.includes('아카이브') ? 'finance' : n.includes('데런픽') || /pick/i.test(n) ? 'issue' : n.includes('라커룸') || n.includes('핫딜') ? 'hotdeal' : null;
}
const blogOf = key => ((ST.d && ST.d.blogs) || []).find(b => keyOf(b) === key) || null;
const msgEl = slot => MSG[slot] ? el('p', {class: 'msg' + (MSG[slot].bad ? ' bad' : '')}, MSG[slot].text) : null;
const wait = (o, what) => o.err ? el('p', {class: 'msg bad'}, o.err) : !o.d ? el('p', {class: 'note'}, what || '불러오는 중…') : null;

// ---------- 블로그 탭 '키자드' 칸 — 폰에서 직접 등록 (주소 복사 → keyzard.cc 열기)
function blogBox(key) {
  const head = el('div', {class: 'top'}, el('b', {}, '키자드'), btn('lnk mute', '다시 보기', () => status(true)));
  if (!ST.d) return [head, wait(ST)];
  const b = blogOf(key);
  if (!b) return [head, el('p', {class: 'note'}, '이 블로그의 키자드 설정을 찾지 못했어요')];
  const w = Array.isArray(b.waiting) ? b.waiting : [], links = w.map(x => x.link).filter(Boolean);
  const out = [head, el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, `등록 대기 ${w.length}개 · 계정당 하루 ${Number(b.daily_limit) || 5}개`),
    el('span', {class: 's'}, PHONE)),
    el('a', {class: 'sm ac', href: sf(KZ), target: '_blank', rel: 'noopener'}, '키자드 열기'))];
  if (links.length > 1) out.push(btn('pb soft', `주소 ${links.length}개 전부 복사`, () => copy(links.join('\n'), `${links.length}개 복사했어요 — 키자드에 붙여넣기 하세요`)));
  if (w.length) {
    const box = el('div', {style: 'margin-top:8px'});
    box.append(...w.slice(0, 20).map(x => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, x.title || '제목 없음'),
      el('span', {class: 's'}, x.link || '')),
      x.link ? btn('sm', '주소 복사', () => copy(x.link, '주소를 복사했어요 — 키자드에 붙여넣기 하세요')) : null)));
    out.push(box);
  } else out.push(el('p', {class: 'note'}, '등록 대기 글이 없어요'));
  return out;
}

// ---------- SNS › 룩북 아래: 룩북 수집 · 코디 레시피 · 코디 트렌드
function look() { if (snsLook()) { get(LK, 'look', 60000); get(NV, 'naver/result', 60000); get(RC, 'look/recipes', 300000); } }
function rank(title, xs) {
  const ar = c => c === 'new' ? ' NEW' : c > 0 ? ` ▲${c}` : c < 0 ? ` ▼${-c}` : '';
  return el('div', {}, el('p', {class: 'h3', style: 'margin:8px 0 0'}, title),
    el('ol', {}, (Array.isArray(xs) ? xs : []).slice(0, 10).map(x => el('li', {text: `${x.word} · 글 ${x.posts}개${ar(x.change)}`}))));
}
function lookBox() {
  const out = [el('p', {class: 'h3'}, '룩북 수집')];
  const L = LK.d, src = (L && L.sources) || [];
  out.push(el('div', {class: 'box'}, wait(LK), L && L.status && L.status.running ? el('p', {class: 'note'}, '수집하는 중…') : null,
    L && L.status && L.status.message ? el('p', {class: 'msg bad'}, String(L.status.message).slice(0, 140)) : null,
    src.map(x => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, x.name || x.id),
      el('span', {class: 's'}, `마지막 ${when(x.collected_at || x.last_run)} · 룩북 ${x.looks || 0} · 상품 ${x.products || 0} · ${x.every_hours || 24}시간마다`),
      x.paused ? el('span', {class: 's bad'}, '멈춤: ' + String(x.paused).slice(0, 80)) : null),
      btn('sm', '지금 수집', () => act('look', '수집하는 중… 몇 분 걸려요', 'look/run/' + x.id, {}, d => ({text: d.ok ? '수집했어요: ' + d.ok : d.skipped || d.error || '끝났어요', bad: !d.ok})),
        {disabled: !!WORK}))),
    L && !src.length ? el('p', {class: 'note'}, '수집 목록이 비었어요 — 예전 화면에서 추가해요') : null, msgEl('look')));
  out.push(el('p', {class: 'h3'}, '코디 레시피'));
  const R = (RC.d && RC.d.recipes) || [], show = RC.all ? R : R.slice(0, 6);
  out.push(el('div', {class: 'box'}, wait(RC), RC.d && !R.length ? el('p', {class: 'note'}, '아직 레시피가 없어요 — 룩북 수집이 한 번 돌아야 해요') : null,
    show.map(r => el('div', {class: 'row'}, el('div', {class: 'grow'}, el('span', {class: 't'}, r.title || r.label || '코디'),
      el('span', {class: 's'}, (Array.isArray(r.items) ? r.items : []).map(i => [i.role, i.color, i.name].filter(Boolean).join(' ')).join(' · '))),
      el('span', {class: 'cp ' + (r.kind === 'lookbook' ? 'ok' : 'info')}, r.kind === 'lookbook' ? '룩북' : '인기 조합'),
      r.popularity ? el('span', {class: 'cp info'}, '♥ ' + Number(r.popularity).toLocaleString()) : null)),
    R.length > 6 ? btn('fold', RC.all ? '6개만' : `${R.length}개 모두 보기`, () => { RC.all = !RC.all; draw(); }, {'aria-expanded': RC.all ? 'true' : 'false'}) : null));
  out.push(el('p', {class: 'h3'}, '코디 트렌드'));
  const N = NV.d, x = N && N.result;
  out.push(el('div', {class: 'box'}, wait(NV), N && !N.has_keys ? el('p', {class: 'note'}, '네이버 검색 API 키를 넣으면 시작해요 (예전 화면 › 공통 설정)') : null,
    N && N.state && N.state.last_error ? el('p', {class: 'msg bad'}, String(N.state.last_error).slice(0, 140)) : null,
    x ? [el('p', {class: 'note', style: 'margin:0'}, `마지막 ${when(x.collected_at)} · 글 ${x.post_count || 0}개 · ${N.every_hours || 12}시간마다` + (N.state && N.state.running ? ' · 모으는 중…' : '')),
      el('div', {class: 'two'}, rank('많이 나온 아이템', x.items), rank('많이 나온 스타일', x.styles))] : N && N.has_keys ? el('p', {class: 'note'}, '아직 모은 결과가 없어요') : null,
    N && N.has_keys ? btn('pb soft', '지금 모으기', () => act('nv', '모으는 중…', 'naver/run', {}, d => ({text: d.ok || d.skipped || d.error || '끝났어요', bad: !d.ok})), {disabled: !!WORK}) : null,
    msgEl('nv')));
  return out;
}

// ---------- 붙이기
const BOX = {};
function sec(id, label) {
  if (!BOX[id]) BOX[id] = el('section', {id, class: 'kz', 'aria-label': label});
  return BOX[id];
}
function fill(s, fn) {
  try { s.replaceChildren(...fn().flat(9).filter(x => x != null && x !== false)); } catch (e) { s.replaceChildren(el('p', {class: 'note'}, '키자드 칸을 그리지 못했어요 — 다시 보기를 눌러 주세요'), old()); }
}
function draw() {
  const k = blogKey();
  if (k && BOX.kzb) { BOX.kzb.className = 'kz box'; fill(BOX.kzb, () => blogBox(k)); }
  if (snsLook() && BOX.kzk) fill(BOX.kzk, lookBox);
}
let KEY = null;
function mount() {
  try {
    const m = D.getElementById('main');
    if (!m) return;
    if (!D.getElementById('kz-css')) D.head.append(el('style', {id: 'kz-css', text: CSS}));
    const k = blogKey(), lk = snsLook();
    for (const [id, want] of [['kzb', k], ['kzk', lk]]) if (!want && BOX[id] && BOX[id].parentNode) BOX[id].remove();
    if (k) {
      const s = sec('kzb', '키자드'), seg = m.querySelector(':scope>.seg');
      if (k !== KEY) { KEY = k; fill(s, () => blogBox(k)); s.className = 'kz box'; }
      if (seg ? seg.nextSibling !== s : m.firstChild !== s) seg ? seg.after(s) : m.prepend(s);
      status();
    }
    if (lk) {
      const s = sec('kzk', '룩북 수집 · 코디');
      if (!s.firstChild) fill(s, lookBox);
      if (m.lastChild !== s) m.append(s);
      look();
    }
  } catch (e) { /* 칸 하나가 탭을 깨지 않게 */ }
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', mount);
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible') { ST.at = LK.at = NV.at = 0; mount(); } });
  mount();
}
W.SBKz = {mount, draw};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
