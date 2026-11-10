/* 결재함 (주인 11/10 '대시보드에 결재함을 새로 만들어서 실시간으로 직접 보내게'). 값은 GET /dashboard/api/inbox (inbox.py — owner-inbox 가지,
   1분마다 받음). 할 일 탭 맨 위에 '결재함 N개' 줄 → 누르면 화면 전체 결재함. 카드: 한 번(once 문구 복사+했어요) → 결정(decide 선택지·답 칸) → 소식(info 읽음).
   '한 번에 보내기' = 대시보드 일 inbox.send 하나 (서버가 answers/<ms>.json 을 바로 올림). app.js 는 예산 끝이라 셸이 defer 로 받고 스스로 붙는다.
   글은 textContent 로만 · 바깥 주소 없음 · 쓰던 답은 이 기기에만 잠깐 (localStorage). */
'use strict';
(() => {
const D = document, W = window;
const CSS = '#ibr{display:flex;align-items:center;gap:10px;width:100%;min-height:52px;margin:0 0 12px;padding:8px 14px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--text);text-align:left}' +
  '.rt>#ibr+.sl{border-top:0}.rt>#ibr{margin-top:8px}#ibr b{font-size:16px}#ibr span{flex:1;min-width:0;font-size:13px;font-weight:400;color:var(--sub)}#ibr i{font-style:normal;color:var(--ac);font-weight:700}' +
  '#ibx{position:fixed;inset:0;z-index:50;overflow-y:auto;background:var(--bg);color:var(--text);padding:calc(10px + var(--top)) 14px 96px;overscroll-behavior:contain}' +
  '#ibx .ixh{display:flex;align-items:center;gap:8px;margin:0 0 10px}#ibx .ixh h2{flex:1;margin:0;font-size:19px}' +
  '#ibx .ib{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 14px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);font-size:15px;font-weight:600}' +
  '#ibx .ib[aria-pressed=true]{background:var(--ac);border-color:var(--ac);color:var(--ac-on)}#ibx .ib[disabled]{opacity:.45}' +
  '#ibx .cd{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0 0 12px}#ibx .cd.gone{opacity:.55}' +
  '#ibx .ixt{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 6px;font-size:12px;color:var(--sub)}#ibx .ixt span{padding:2px 8px;border-radius:999px;background:var(--grey)}' +
  '#ibx .ixt .rk{background:var(--red-soft);color:var(--red)}#ibx h3{margin:0 0 6px;font-size:16px;line-height:1.4}' +
  '#ibx p{margin:0 0 8px;font-size:15px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}#ibx .rc{color:var(--sub);font-size:14px}' +
  '#ibx details{margin:0 0 8px;font-size:14px}#ibx summary{min-height:36px;display:flex;align-items:center;color:var(--sub);cursor:pointer}' +
  '#ibx ul{margin:4px 0 0;padding-left:18px;line-height:1.55}#ibx li{margin:0 0 4px;overflow-wrap:anywhere;white-space:pre-wrap}' +
  '#ibx .op{display:grid;gap:6px;margin:0 0 8px}#ibx .op .ib{justify-content:flex-start;text-align:left;white-space:normal;padding:8px 12px;line-height:1.4}' +
  '#ibx textarea{display:block;width:100%;box-sizing:border-box;min-height:64px;padding:10px;border:1px solid var(--fline,var(--line));border-radius:10px;background:var(--field,var(--card));font-size:16px;resize:vertical}' +
  '#ibx .ixc{margin:0 0 8px;padding:10px;border-radius:10px;background:var(--grey);font-size:14px;white-space:pre-wrap;overflow-wrap:anywhere}' +
  '#ibx .rw{display:flex;flex-wrap:wrap;gap:8px}#ibx .to{font-size:12px;color:var(--sub);margin:6px 0 0}' +
  '#ibx .ixg{margin:18px 0 8px;font-size:14px;font-weight:700;color:var(--sub)}#ibx .nt{font-size:13px;color:var(--muted);margin:0 0 10px}' +
  '#ibx .sg{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:0 0 8px}#ibx .bl{margin:0;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}' +
  '#ibx .bl:last-child{border:0}#ibx .bl i{font-style:normal;color:var(--sub);margin-right:6px}' +
  '#ibx .sb{position:fixed;left:0;right:0;bottom:0;z-index:51;padding:10px 14px calc(10px + env(safe-area-inset-bottom,0px));background:var(--bg);border-top:1px solid var(--line)}' +
  '#ibx .sb .ib{width:100%;min-height:50px;background:var(--ac);border-color:var(--ac);color:var(--ac-on);font-size:16px}';
const KEY = 'sb-inbox-draft';
let RT = null, data = null, at = 0, busy = false, row = null, pane = null, tt = null, tab = null, timer = null, sending = false;
let draft = {}, done = {}, gone = {};  // draft[id] = {choice, text} · done[id] = true · gone[id] = 보낸 시각 (중계가 'sent' 로 바꿀 때까지 흐리게)
try { const s = JSON.parse(localStorage.getItem(KEY) || '{}'); draft = s.draft || {}; done = s.done || {}; } catch (e) { /* 저장 안 됨 — 그대로 */ }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify({draft, done})); } catch (e) { /* 그대로 */ } };

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
const btn = (label, on, x) => el('button', Object.assign({class: 'ib', type: 'button', onclick: on}, x || {}), label);
const onTodo = () => /^(#\/?(todo\/?)?)?$/.test(location.hash);
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
const live = () => ((data && data.open) || []).filter(x => !gone[x.id]);

async function load() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch('/dashboard/api/inbox', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) {
      data = await r.json(); at = Date.now();
      const ids = new Set((data.open || []).map(x => x.id));
      for (const k in gone) if (!ids.has(k) || Date.now() - gone[k] > 600000) delete gone[k];  // 중계가 받아 'sent' 로 바꾸면 여기서 빠짐 (10분 뒤엔 다시 보임)
    } else if (r.status === 401 && pane) toast('다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요');
  } catch (e) { /* 다음에 다시 */ }
  busy = false;
  place();
  if (pane) draw();
}

// 할 일 탭 맨 위 줄: '오늘 할 일' 칸(routine.js #rtp) 안 맨 위 · 그 칸이 없거나 숨었으면 그 칸 바로 아래(없으면 #main 맨 위)
function place() {
  const m = D.getElementById('main');
  if (!m) return;
  const n = live().length;
  if (!onTodo() || !n) { if (row && row.parentNode) row.remove(); return; }
  if (!row) row = el('button', {id: 'ibr', type: 'button', onclick: open});
  row.replaceChildren(el('b', {}, '결재함'), el('i', {}, `${n}개`), el('span', {}, '주인님 몫 — 골라서 한 번에 보내기'));
  const rt = D.getElementById('rtp');
  if (rt && m.contains(rt) && !rt.hidden) { if (rt.firstChild !== row) rt.prepend(row); }
  else if (rt && m.contains(rt)) { if (rt.nextSibling !== row) rt.after(row); }
  else if (m.firstChild !== row) m.prepend(row);
}

function open() {
  if (!D.getElementById('ib-css')) D.head.append(el('style', {id: 'ib-css', text: CSS}));
  if (!pane) { pane = el('section', {id: 'ibx', role: 'dialog', 'aria-modal': 'true', 'aria-label': '결재함'}); D.body.append(pane); }
  draw();
  pane.scrollTop = 0;
  load();
  clearInterval(timer);
  timer = setInterval(() => { if (D.visibilityState === 'visible') load(); }, 30000);
}
function close() {
  if (pane) pane.remove();
  pane = null;
  clearInterval(timer);
  place();
}

function card(x) {
  const d = draft[x.id] || {}, sent = !!gone[x.id];
  const set = (k, v) => { draft[x.id] = Object.assign({}, draft[x.id], {[k]: v}); save(); };
  const kids = [el('div', {class: 'ixt'}, el('span', {}, x.glabel), x.risk !== '일반' ? el('span', {class: 'rk'}, x.risk) : null,
    el('span', {}, {once: '한 번 할 일', decide: '정해 주세요', info: '알려 드림'}[x.kind])), el('h3', {}, x.title || x.id)];
  if (x.question) kids.push(el('p', {}, x.question));
  if (x.recommend) kids.push(el('p', {class: 'rc'}, '추천: ' + x.recommend));
  if (x.background.length) kids.push(el('details', {}, el('summary', {}, `배경 자료 ${x.background.length}개`), el('ul', {}, x.background.map(b => el('li', {}, b)))));
  if (sent) kids.push(el('p', {class: 'rc'}, '보냈어요 — 중계 세션이 전하는 중'));
  else if (x.kind === 'decide') {
    if (x.options.length) kids.push(el('div', {class: 'op'}, x.options.map((o, i) => btn(`${i + 1}. ${o}`, () => { set('choice', d.choice === i + 1 ? null : i + 1); draw(); },
      {'aria-pressed': d.choice === i + 1 ? 'true' : 'false'}))));
    const ta = el('textarea', {rows: '2', maxlength: '4000', placeholder: x.options.length ? '덧붙일 말 (안 써도 돼요)' : '답을 적어 주세요', 'aria-label': `${x.title} 답`});
    ta.value = d.text || '';
    ta.addEventListener('input', () => { set('text', ta.value); bar(); });
    kids.push(ta);
  } else {
    if (x.kind === 'once' && x.copy) kids.push(el('div', {class: 'ixc'}, x.copy));
    kids.push(el('div', {class: 'rw'}, x.kind === 'once' && x.copy ? btn('문구 복사', () => copy(x.copy)) : null,
      btn(x.kind === 'once' ? '했어요' : '읽음', () => { done[x.id] = !done[x.id]; if (!done[x.id]) delete done[x.id]; save(); draw(); },
        {'aria-pressed': done[x.id] ? 'true' : 'false'})));
  }
  if (x.targets.length) kids.push(el('p', {class: 'to'}, '→ ' + x.targets.join(' · ')));
  return el('div', {class: 'cd' + (sent ? ' gone' : '')}, kids);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast('복사했어요'); return; } catch (e) { /* 아래로 */ }
  const ta = el('textarea', {style: 'position:fixed;top:-1000px;opacity:0'});
  ta.value = text; D.body.append(ta); ta.select();
  let ok = false;
  try { ok = D.execCommand('copy'); } catch (e) { ok = false; }
  ta.remove();
  toast(ok ? '복사했어요' : '복사하지 못했어요 — 글을 길게 눌러 복사해 주세요');
}

function picked() {
  const ids = new Set(live().map(x => x.id)), ans = [], dn = [];
  for (const x of live()) {
    const d = draft[x.id] || {};
    if (x.kind === 'decide' && (d.choice || (d.text || '').trim())) ans.push({id: x.id, choice: d.choice || null, text: (d.text || '').trim()});
  }
  for (const k in done) if (ids.has(k)) dn.push(k);
  return {answers: ans, done: dn};
}
function bar() {
  const b = pane && pane.querySelector('.sb .ib');
  if (!b) return;
  const p = picked(), n = p.answers.length + p.done.length;
  b.disabled = !n || sending;
  b.textContent = sending ? '보내는 중…' : n ? `한 번에 보내기 (${n})` : '고르거나 적으면 보낼 수 있어요';
}

async function send() {
  const p = picked();
  if (!p.answers.length && !p.done.length) return;
  sending = true; bar();
  let r = null, d = {};
  try {
    r = await fetch('/dashboard/api/action', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()},
      body: JSON.stringify({action: 'inbox.send', args: p, cid: rid(), ctoken: null})});
    d = await r.json().catch(() => ({}));
  } catch (x) { r = null; }
  sending = false;
  if (!r) toast('인터넷 연결을 확인해 주세요 — 쓴 답은 그대로 있어요');
  else if (r.status === 401) toast('다시 로그인해 주세요 — 쓴 답은 그대로 있어요');
  else if (r.status === 403 && d.code === 'csrf') toast('화면이 오래됐어요 — 새로고침해 주세요 (쓴 답은 그대로)');
  else if (d.status === 'ok') {
    const now = Date.now();
    for (const a of p.answers) { gone[a.id] = now; delete draft[a.id]; }
    for (const k of p.done) { gone[k] = now; delete done[k]; }
    save();
    toast(d.text || '보냈어요');
  } else toast(d.text || '보내지 못했어요 — 잠시 뒤 다시 (쓴 답은 그대로)');
  draw();
  load();
}

function digest() {
  const g = (data && data.digest) || {tabs: [], server: []}, tabs = g.tabs || [];
  if (!tabs.length && !(g.server || []).length) return [];
  if (!tab || !tabs.some(t => t.key === tab)) tab = tabs.length ? tabs[0].key : null;
  const cur = tabs.find(t => t.key === tab);
  return [el('p', {class: 'ixg'}, '밤사이 소식'),
    tabs.length ? el('div', {class: 'sg', role: 'tablist'}, tabs.map(t => btn(t.label, () => { tab = t.key; draw(); },
      {role: 'tab', 'aria-pressed': t.key === tab ? 'true' : 'false', 'aria-selected': t.key === tab ? 'true' : 'false'}))) : null,
    cur ? el('div', {class: 'cd'}, cur.period ? el('p', {class: 'nt'}, cur.period) : null,
      cur.bullets.length ? cur.bullets.map(b => el('p', {class: 'bl'}, el('i', {}, [b.t, b.who].filter(Boolean).join(' · ')), b.text)) : el('p', {class: 'nt'}, '소식 없음')) : null,
    (g.server || []).length ? el('div', {class: 'cd'}, el('p', {class: 'nt'}, '서버 상태'), g.server.map(s => el('p', {class: 'bl'}, s))) : null];
}

function draw() {
  if (!pane) return;
  const top = el('div', {class: 'ixh'}, el('h2', {}, (data && data.title) || '결재함'), btn('새로', load), btn('닫기', close));
  const kids = [top];
  if (!data) kids.push(el('p', {class: 'nt'}, '불러오는 중…'));
  else {
    if (data.err) kids.push(el('p', {class: 'nt'}, data.err));
    if (data.pending) kids.push(el('p', {class: 'nt'}, `아직 못 보낸 답 ${data.pending}묶음 — 1분마다 다시 보내요`));
    const xs = data.open || [];
    if (!xs.length) kids.push(el('p', {class: 'nt'}, '결재할 것이 없어요'));
    let last = null;
    for (const x of xs) {
      if (x.kind !== last) { kids.push(el('p', {class: 'ixg'}, {once: '한 번 할 일', decide: '정해 주세요', info: '알려 드림'}[x.kind])); last = x.kind; }
      kids.push(card(x));
    }
    if ((data.sent || []).length) kids.push(el('details', {}, el('summary', {}, `보낸 것 ${data.sent.length}개`),
      el('ul', {}, data.sent.map(x => el('li', {}, x.title + (x.sent_note ? ` — ${x.sent_note}` : ''))))));
    kids.push(...digest().filter(Boolean));
  }
  kids.push(el('div', {class: 'sb'}, btn('', send)));
  const y = pane.scrollTop;
  pane.replaceChildren(...kids);
  pane.scrollTop = y;
  bar();
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!onTodo()) { if (row && row.parentNode) row.remove(); return; }
  // '오늘 할 일' 칸(routine.js)은 칸 안을 통째로 다시 그림 → 줄이 빠지면 다시 붙이게 그 칸도 지켜봄 (칸이 새로 생기면 새로)
  const rt = D.getElementById('rtp');
  if (rt && rt !== RT) { RT = rt; new MutationObserver(place).observe(rt, {childList: true, attributes: true, attributeFilter: ['hidden']}); }
  place();
  if (!data || Date.now() - at > 60000) load();
}

function start() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('ib-css')) D.head.append(el('style', {id: 'ib-css', text: CSS}));
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', () => { if (pane && !onTodo()) close(); mount(); });
  D.addEventListener('keydown', e => { if (e.key === 'Escape' && pane) close(); });
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (pane || onTodo())) load(); });
  const rf = D.getElementById('rf');
  if (rf) rf.addEventListener('click', () => { if (onTodo() && !busy) setTimeout(load, 300); });
  mount();
}
W.SBInbox = {load, open, close};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
