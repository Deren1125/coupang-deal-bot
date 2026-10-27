/* 오늘 할 일 (주인 10/27 — 손일 하루 5번). 할 일 탭 맨 위 칸(급한 알림만 그 위) · GET /dashboard/api/routine (routine.py).
   #main 을 다시 그리면 같은 칸을 다시 붙임 (logins.js 틀). 체크는 단추 (app.js 는 입력 칸 커서 동안 그리기를 미룸). */
'use strict';
(() => {
const D = document, W = window;
const CSS = '#rtp .box{padding:4px 16px 6px;margin-bottom:20px}#rtp .sl{display:flex;align-items:center;gap:10px;width:100%;min-height:56px;padding:12px 0;' +
  'border:0;border-top:1px solid var(--line);background:none;text-align:left;color:var(--text)}#rtp .box>.sl:first-child{border-top:0}' +
  '#rtp .sl b{flex:1;min-width:0;font-size:15px;line-height:1.4}#rtp .sl span{font-size:13px;color:var(--sub);font-weight:500}' +
  '#rtp .sl.cur span{color:var(--ac-ink);font-weight:600}#rtp .it{display:flex;gap:12px;align-items:flex-start;padding:14px 0}' +
  '#rtp .it+.it{background:linear-gradient(var(--line),var(--line)) 56px 0/100% 1px no-repeat}' +
  '#rtp .cb{flex:none;width:44px;height:44px;padding:0;border:2px solid var(--fline);border-radius:12px;background:var(--field);color:var(--gr);font-size:20px}' +
  '#rtp .cb[aria-checked=true]{background:var(--gr-soft);border-color:var(--gr)}#rtp .it .grow{flex:1;min-width:0;padding-top:10px}' +
  '#rtp .t{display:block;font-size:15px;font-weight:600;line-height:1.45;overflow-wrap:anywhere}#rtp .dn .t{text-decoration:line-through;color:var(--sub)}' +
  '#rtp .s{display:block;margin-top:3px;font-size:13px;line-height:1.5;color:var(--sub);overflow-wrap:anywhere}#rtp .bt{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}' +
  '#rtp a.sm{display:inline-flex;align-items:center;text-decoration:none;color:var(--text)}' +
  '#rtp .bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:0 0 14px}#rtp .bar i{display:block;height:100%;background:var(--gr)}';
let box = null, data = null, at = 0, timer = null, busy = false, tt = null, open = {}, cur = '', sig = '';

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
const on = () => /^(#\/?(todo\/?)?)?$/.test(location.hash);  // #/todo · 주소 없음 (딜만 보기 #/todo/deal 은 아님)
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text}));
  t.hidden = false;
  clearTimeout(tt);
  tt = setTimeout(() => { t.hidden = true; }, 3000);
}
function csrf() {
  try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; }
}
const rid = () => 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(12)), b => (b % 36).toString(36)).join('');

const ids = () => data && data.slots ? data.slots.flatMap(s => s.items.map(i => i.id)) : [];
async function load() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch('/dashboard/api/routine', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) { data = await r.json(); at = Date.now(); }
  } catch (e) { /* 다음에 다시 */ }
  busy = false;
  const c = data && (data.slots || []).find(s => s.current);
  if (c && c.key !== cur) { cur = c.key; open = {[cur]: true}; }
  draw();
  const k = ids().join();
  if (k !== sig && W.dispatchEvent) { sig = k; W.dispatchEvent(new Event('sb-routine')); }  // app.js 가 겹친 할 일을 빼고 다시 그림
  clearTimeout(timer);
  if (on() && D.visibilityState === 'visible') timer = setTimeout(load, 300000);
}

function recount() {
  data.done = 0;
  for (const s of data.slots) {
    s.done = s.items.filter(i => i.done).length;
    s.min_left = s.items.reduce((n, i) => n + (i.done ? 0 : i.min), 0);
    data.done += s.done;
  }
}

async function check(it) {
  const v = !it.done;
  it.done = v; recount(); draw();
  let r = null, d = {};
  try {
    r = await fetch('/dashboard/api/action', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()},
      body: JSON.stringify({action: 'routine.done', args: {id: it.id, done: v}, cid: rid(), ctoken: null})});
    d = await r.json().catch(() => ({}));
  } catch (x) { r = null; }
  if (r && r.status === 200 && d.status === 'ok') return;
  it.done = !v; recount(); draw();
  if (!r) toast('인터넷 연결을 확인해 주세요');
  else if (r.status === 401) toast('다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요');
  else if (r.status === 403 && d.code === 'csrf') toast('화면이 오래됐어요 — 새로고침해 주세요');
  else toast(d.text || '체크하지 못했어요 — 잠시 뒤 다시');
}

function copy(text) {
  const c = W.navigator && W.navigator.clipboard;
  (c && c.writeText ? c.writeText(text) : Promise.reject(new Error('x')))
    .then(() => toast('복사했어요'), () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
}

function item(it) {
  const btns = it.done ? [] : [it.copy.map(c => el('button', {class: 'sm', type: 'button', onclick: () => copy(c.text)}, c.label)),
    it.links.map(l => el('a', {class: 'sm', href: l.url, target: '_blank', rel: 'noopener noreferrer'}, l.label))].flat();
  return el('div', {class: 'it' + (it.done ? ' dn' : ''), 'data-id': it.id},
    el('button', {class: 'cb', type: 'button', role: 'checkbox', 'aria-checked': it.done ? 'true' : 'false', 'aria-label': it.title,
      onclick: () => check(it)}, it.done ? '✓' : ''),
    el('div', {class: 'grow'}, el('span', {class: 't'}, it.title),
      el('span', {class: 's'}, [it.tag, `${it.min}분`, it.note].filter(Boolean).join(' · ')),
      btns.length ? el('div', {class: 'bt'}, btns) : null));
}

function slot(s) {
  const all = s.total && s.done === s.total;
  const tail = !s.total ? '없음' : `${s.done}/${s.total}` + (all ? ' · 끝' : ` · 약 ${s.min_left}분`) + (s.current ? ' · 지금' : '');
  return [el('button', {class: 'sl' + (s.current ? ' cur' : ''), type: 'button', 'aria-expanded': open[s.key] ? 'true' : 'false',
    onclick: () => { open[s.key] = !open[s.key]; draw(); }}, el('b', {}, `${open[s.key] ? '▾' : '▸'} ${s.label} ${s.at}`), el('span', {}, tail)),
    open[s.key] ? s.items.map(item) : null];
}

function draw() {
  if (!box) return;
  const ok = !!(data && data.slots && data.total);
  box.hidden = !ok;
  if (!ok) { box.replaceChildren(); return; }
  const bar = el('i');
  bar.style.width = Math.round(100 * data.done / data.total) + '%';
  box.replaceChildren(...[
    el('div', {class: 'top', style: 'margin:4px 0 6px'}, el('h2', {}, `오늘 할 일 · ${data.done}/${data.total}`),
      el('button', {class: 'lnk', type: 'button', onclick: load}, '다시 보기')),
    el('div', {class: 'bar'}, bar),
    data.today ? null : el('p', {class: 'note'}, `${data.day} 목록이에요 — 오늘 목록이 아직 안 왔어요`),
    el('div', {class: 'box'}, data.slots.map(slot)),
    data.overflow ? el('p', {class: 'note', style: 'margin:-6px 0 12px'}, `오늘은 여기까지 — 나머지 ${data.overflow}개는 다음에`) : null].filter(Boolean));
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!on()) { clearTimeout(timer); return; }
  if (!box) {
    if (!D.getElementById('rt-css')) D.head.append(el('style', {id: 'rt-css', text: CSS}));
    box = el('section', {id: 'rtp', 'aria-label': '오늘 할 일'});
    box.hidden = true;
    draw();
  }
  const f = m.firstChild, u = f && f !== box && f.className === 'bn bad' ? f : null;  // 급한 알림(app.js) 바로 아래
  if ((u ? u.nextSibling : f) !== box) u ? u.after(box) : m.prepend(box);
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
W.SBRoutine = {load, mount, ids};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
