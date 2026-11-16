/* 오늘 X 글 · 큰 사건 배너 (주인 11/15 '오늘 x 글 보내는게 아니라 대시보드에 띄워줘 … 큰 사건은 바로 알림'). 셸이 defer 로 받음 (xstudio.js 다음) — xtoday.py.
   SNS 탭 X 칸에 '오늘 X 글' 카드 (시각별 · '오늘 할 X 일' 카드 아래) · 화면은 #/sns/xt (큰 사건 알림을 누르면 #/sns/xt/<번호>).
   서버가 미리 써 둔 초안을 보여 주기만 한다 — '열기'는 그 초안을 X 글쓰기 화면(xstudio.js #/sns/xs/write)에 넣고, 거기서 '내 한마디'를 직접 써야
   'X에서 올리기'가 켜진다. 자동으로 올리는 것은 없다. 주소만 보고 AI 를 부르지 않는다 (초안 만들기·보충은 내가 누를 때만).
   카드마다 복사 단추·'올렸어요' 체크 (주인 11/15): 체크 = xstudio posted(tid) 그대로 · 10분 안 취소 · 표시는 서버 기록(done).
   체크리스트 (주인 11/16 'X 는 책갈피 말고 체크리스트 느낌으로만'): 카드 맨 위 '오늘 X n/10 올림' · 글마다 [복사]·[✓ 올렸어요] 한 줄 (올린 글은
   취소 10분이 지나면 카드에서 빠짐 · 다 하면 한 줄) · '오늘 반응 할 것' 줄 = [열기](서버가 준 X 링크 — 내가 누름)·[✓ 했어요] (자동 클릭 없음).
   #/sns/xt/off = 예전 X 도우미 책갈피로 온 것 → '이제 안 써요' 한 줄 (xstudio.js·xengage.js 가 보냄).
   이 파일에 바깥 주소·innerHTML 없음 (출처 기사 링크는 서버 값). 고치던 '내 한마디'는 이 기기에만 (localStorage xt1 · 그날 것만). */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xtoday/';
const CSS = '.xtb{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 8px;padding:8px 10px;border-radius:10px;background:var(--or-soft);color:var(--or);font-size:14px;line-height:1.45}' +
  '.xtb span{flex:1 1 180px;min-width:0;overflow-wrap:anywhere}.xtb .sm{flex:none}' +
  '.xt .src{margin:0 0 10px;font-size:13px;line-height:1.5}.xt .src a{display:block;color:var(--ac-ink);overflow-wrap:anywhere;padding:3px 0}' +
  '.xt .kw{display:block;font-size:16px;margin:6px 0}.xt .ih{flex-wrap:wrap}.xtc .ln{min-height:36px;align-items:center;cursor:pointer}' +
  '.xtc .ln span:nth-child(2){flex:1;min-width:0;color:var(--text);text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
  '.xd{opacity:.55}.xd .po,.xd .kw,.xtc .xd span:nth-child(2){text-decoration:line-through}' +
  '.xtp{margin:4px 0 8px;font-size:15px}.xtp i{display:block;height:6px;border-radius:3px;background:var(--line);margin-top:6px;overflow:hidden}' +
  '.xtp i b{display:block;height:100%;background:var(--ok,#2a8)}.xtc .ln .sm{flex:none}.xtc .ln a.sm{text-decoration:none}' +
  '.xck{display:inline-flex;align-items:center;gap:6px;min-height:36px;padding:0 6px;font-size:14px;cursor:pointer}.xck input{width:22px;height:22px;margin:0}' +
  'main[data-xt]>:not(#xts){display:none!important}';
const NW = {target: '_blank', rel: 'noopener'}, KEYS = ['ed', 'el', 'lg', 'mine', 'chk', 'done'];
let ST = null, at = 0, loading = false, card = null, scr = null, tt = null, focus = '';
const busy = {};
let ED = {d: '', e: {}};
try { const o = JSON.parse(W.localStorage.getItem('xt1') || 'null'); if (o && typeof o === 'object' && o.e && typeof o.e === 'object') ED = o; } catch (e) { /* 저장본 없음 */ }
function keep() { try { W.localStorage.setItem('xt1', JSON.stringify(ED)); } catch (e) { /* 저장 못 해도 그대로 씀 */ } }

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
const btn = (cls, label, f, x) => el('button', Object.assign({class: cls, type: 'button', onclick: f}, x), label);
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text}));
  t.hidden = false;
  clearTimeout(tt);
  tt = setTimeout(() => { t.hidden = true; }, 3500);
}
function csrf() {
  try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; }
}
function old(text) {  // 클립보드 API 가 막힐 때 (사파리) — 숨은 칸 골라 복사
  try {
    const t = el('textarea', {readonly: true, style: 'position:fixed;top:0;left:0;opacity:0;font-size:16px'});
    t.value = text; D.body.append(t); t.focus(); t.select(); t.setSelectionRange(0, text.length);
    const ok = D.execCommand('copy'); t.remove();
    return !!ok;
  } catch (e) { return false; }
}
function copy(text, what, hint) {  // 탭 안에서 writeText 먼저
  text = String(text || '');
  const ok = () => toast(hint ? '복사했어요 — X 에서 내 한마디를 한 줄 붙여 주세요' : `${what || ''} 복사했어요`.trim());
  const bad = () => (old(text) ? ok() : toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
  const c = W.navigator && W.navigator.clipboard;
  if (c && c.writeText) c.writeText(text).then(ok, bad); else bad();
}
async function call(path, body) {
  try {
    const r = await fetch((path[0] === '/' ? '' : API) + path, body ? {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)} : {credentials: 'same-origin', cache: 'no-store'});
    let d = {};
    try { d = await r.json(); } catch (e) { /* 빈 몸 */ }
    if (r.status === 403 && d.code === 'csrf') d.text = '화면이 오래됐어요 — 새로고침한 뒤 다시 눌러 주세요';
    if (r.status === 401) d.text = '로그인이 풀렸어요 — 새로고침해 주세요';
    return {s: r.status, d};
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요'}}; }
}
const take = d => { if (d && d.v && Array.isArray(d.items)) { ST = d; at = Date.now(); for (const i of d.items) i.until = at + (i.undo_s || 0) * 1000; if (ED.d !== ST.day) { ED = {d: ST.day, e: {}}; keep(); } } };
async function load() {
  if (loading) return;
  loading = true;
  const r = await call('state');
  loading = false;
  at = Date.now();
  if (r.s === 200) take(r.d); else if (!ST && r.d.text) toast(r.d.text);
  draw();
}

// -- X 글쓰기 화면(xstudio.js)으로 넘기기: 초안 묶음을 그대로 넣고, 카드마다 고치던 글·'내 한마디'는 따로 기억
const studio = () => (W.SBXStudio && W.SBXStudio.view ? W.SBXStudio : null);
function sync() {
  const X = studio(), V = X && X.view, id = V && V.res && V.res.tid;
  if (!id || !ST || !ST.items.some(i => i.id === id)) return;
  ED.e[id] = {};
  for (const k of KEYS) ED.e[id][k] = V[k] || {};
  keep();
}
function open(it) {
  const X = studio();
  if (!X) return toast('글쓰기 화면을 불러오는 중이에요 — 잠시 뒤 다시 눌러 주세요');
  sync();
  const V = X.view, e = ED.e[it.id] || {};
  V.res = Object.assign({}, it.res, {tid: it.id, fmt: 'auto'});
  for (const k of KEYS) V[k] = e[k] || {};
  V.tab = 'write';
  if (X.keep) X.keep();  // 넘긴 초안을 그 화면의 저장본에도 (화면이 다시 떠도 그대로)
  location.hash = '#/sns/xs/write';
  X.mount();
  X.load();
}
async function make(ev) {
  if (busy.make) return;
  busy.make = ev.id; draw();
  const r = await call('make', {id: ev.id});
  busy.make = '';
  take(r.d);
  const it = r.s === 200 && ST.items.find(i => i.id === r.d.made);
  if (it) return open(it);
  toast(r.d.text || '초안을 쓰지 못했어요 — 잠시 뒤 다시');
  draw();
}
async function skip(id) {
  const r = await call('skip', {id});
  take(r.d);
  toast(r.s === 200 ? '오늘은 안 쓰기로 했어요' : r.d.text || '다시 눌러 주세요');
  draw();
}
async function fill() {
  if (busy.fill) return;
  busy.fill = true; draw();
  const r = await call('make', {op: 'fill'});
  busy.fill = false;
  take(r.d);
  toast(r.s === 200 ? "만들기 시작했어요 — 몇 분 뒤 '다시 보기'를 눌러 주세요" : r.d.text || '보충하지 못했어요');
  draw();
}
// -- 복사 · '올렸어요' 체크
function texts(it) {  // (고친 글 또는 초안) + '내 한마디'
  sync();
  const e = ED.e[it.id] || {}, d = (((it.res || {}).drafts) || [])[0] || {}, m = String((e.mine || {})[0] || '').trim();
  const pick = (v, f) => String(v != null ? v : f || '').trim(), add = b => (b && m ? b + '\n\n' + m : b);
  return {s: add(pick((e.ed || {})[0], it.preview)), l: d.long ? add(pick((e.el || {})[0], d.long)) : '', m};
}
const copyBtns = it => { const t = texts(it);
  return [btn('pb soft', '복사', () => { const x = texts(it); copy(x.s, '글', !x.m); }), t.l ? btn('pb soft', '긴 글 복사', () => { const x = texts(it); copy(x.l, '긴 글', !x.m); }) : null,
    it.first_reply ? btn('pb soft', '첫 답글 복사', () => copy(it.first_reply, '첫 답글(출처)')) : null]; };
async function check(it, ev) {
  const b = ev.currentTarget, undo = !!it.done;
  if (busy.chk) { b.checked = undo; return; }
  if (undo && Date.now() > (it.until || 0)) { b.checked = true; return toast('올린 지 10분이 지나 취소할 수 없어요'); }
  const t = texts(it);
  busy.chk = it.id; b.disabled = true;
  const r = await call('/dashboard/api/xstudio/posted', {op: undo ? 'undo' : 'add', text: t.s || it.keyword || '글', format: 'auto', mine: !!t.m, tid: it.id});
  busy.chk = '';
  if (r.s === 200) { it.done = !undo; it.until = undo ? 0 : Date.now() + 600000; toast(undo ? '기록을 지웠어요' : '올렸어요 — 기록했어요'); draw(); load(); }
  else { b.disabled = false; b.checked = undo; toast(r.d.text || '기록하지 못했어요 — 다시 눌러 주세요'); }
}
const cp1 = it => btn('sm', '복사', e => { e.stopPropagation(); const x = texts(it); copy(x.s, '글', !x.m); });
const box = (it, label) => el('label', {class: 'xck', onclick: e => e.stopPropagation()},
  el('input', {type: 'checkbox', checked: !!it.done, 'aria-label': `${it.keyword || '글'} 올렸어요`, onchange: e => check(it, e)}), label);
const itemOf = ev => ST.items.find(i => i.id === ev.item);
const goBig = ev => { const it = itemOf(ev); if (it) open(it); else location.hash = '#/sns/xt/' + ev.id; };
const state = it => (it.done ? '올림 ✓' : it.nums_ok ? '준비됨' : '숫자 확인');
const links = xs => (xs || []).filter(s => /^https?:\/\//.test(s.link || '')).map(s => el('a', Object.assign({href: s.link}, NW), s.title || s.link));

// '오늘 반응 할 것' [✓ 했어요] — 그날 체크만 서버에 (링크는 내가 눌러 X 에서 직접 · 자동 클릭 없음)
async function react(r, ev) {
  const b = ev.currentTarget, on = !r.done;
  if (busy.rx) { b.checked = r.done; return; }
  busy.rx = r.key; b.disabled = true;
  const x = await call('react', {key: r.key, done: on});
  busy.rx = '';
  if (x.s === 200) { take(x.d); toast(on ? '반응했어요 — 체크했어요' : '체크를 풀었어요'); } else { b.disabled = false; b.checked = r.done; toast(x.d.text || '다시 눌러 주세요'); }
  draw();
}
const rbox = r => el('label', {class: 'xck'}, el('input', {type: 'checkbox', checked: !!r.done, 'aria-label': `${r.label} 했어요`, onchange: e => react(r, e)}), '✓ 했어요');
const rline = r => el('div', {class: 'ln' + (r.done ? ' xd' : '')}, el('a', Object.assign({class: 'sm', href: r.url}, NW), '열기'), el('span', {}, r.label), rbox(r));
function prog() {  // 오늘 X n/10 올림
  const tot = Math.max(ST.slots.length, ST.items.length), dn = ST.items.filter(i => i.done).length;
  return el('div', {class: 'xtp'}, el('b', {}, `오늘 X ${dn}/${tot} 올림`), el('i', {}, el('b', {style: `width:${tot ? Math.min(100, Math.round(dn * 100 / tot)) : 0}%`})));
}
const OFF = 'X 도우미 책갈피는 이제 안 써요 — 대시보드 X 체크리스트를 쓰세요';

// -- 그리기
function banner(ev) {
  return el('div', {class: 'xtb'}, el('span', {}, `🔥 큰 사건 · ${ev.title}`), btn('sm', itemOf(ev) ? '초안 열기' : '초안 보기', () => goBig(ev)));
}
function drawCard() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, '오늘 X 글 불러오는 중…'));
  const t = Date.now(), tot = Math.max(ST.slots.length, ST.items.length), dn = ST.items.filter(i => i.done).length;
  const left = ST.items.filter(i => !i.done || t < (i.until || 0)), rx = ST.react || [], rl = rx.filter(r => !r.done);
  const line = it => el('div', {class: 'ln' + (it.done ? ' xd' : ''), onclick: () => open(it)}, el('span', {}, it.slot || '바로'),
    el('span', {}, it.keyword || '글'), it.done ? null : cp1(it), box(it, '✓ 올렸어요'));
  const wait = ST.slots.filter(x => !ST.items.some(i => i.slot === x)).length;
  card.replaceChildren(...[
    el('div', {class: 'ih'}, el('b', {class: 'ti'}, '오늘 X 글'), el('a', {class: 'lnk', href: '#/sns/xt'}, '열기 ›')),
    prog(), ST.big.map(banner), left.map(line),
    tot && dn >= tot ? el('p', {class: 'note'}, '오늘 X 다 올렸어요 ✓') : null,
    rx.length ? [el('p', {class: 'grp'}, `오늘 반응 할 것 ${rx.length - rl.length}/${rx.length}`), rl.map(rline),
      rl.length ? null : el('p', {class: 'note'}, '오늘 반응 다 했어요 ✓')] : null,
    ST.err ? el('p', {class: 'note'}, ST.err) : null,
    el('p', {class: 'note'}, !ST.on ? '오늘 X 글 만들기가 꺼져 있어요' : (wait ? `${wait}개는 아직 초안 전${ST.next ? ` — ${ST.next} 에 ${ST.ran ? '보충해요' : '만들어요'}` : ''} · ` : '') +
      "복사해서 X 에 올린 뒤 ✓ · ♥·리포스트·답글은 X 에서 내가 직접")].flat(9).filter(Boolean));
}
function bigCard(ev) {
  const it = itemOf(ev), mk = busy.make === ev.id;
  return el('section', {class: 'itm' + (focus === ev.id ? ' hl' : ''), 'data-b': ev.id},
    el('div', {class: 'ih'}, el('span', {class: 'cp hot'}, '큰 사건'), el('span', {class: 'cp info'}, `${ev.at} 알림`)),
    el('b', {class: 'kw'}, ev.title),
    el('p', {class: 'sf'}, [[ev.lead, ev.shown].filter(Boolean).join(' '), `매체 ${ev.outlets}곳`, `점수 ${ev.score}`].filter(Boolean).join(' · ')),
    el('div', {class: 'src'}, links(ev.sources)),
    el('div', {class: 'rw'}, it ? btn('pb', '초안 열기 — 내 한마디 쓰고 올리기', () => open(it))
      : btn('pb', mk ? '쓰는 중… 30초~1분 걸려요' : '초안 만들기', () => make(ev), {disabled: !!busy.make || null}),
    btn('pb soft', '안 써요', () => skip(ev.id))));
}
function itemCard(it) {
  return el('section', {class: 'itm' + (focus && (focus === it.id || focus === it.eid) ? ' hl' : '') + (it.done ? ' xd' : ''), 'data-t': it.id},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, it.slot || '바로'), el('span', {class: 'cp' + (it.kind === 'big' ? ' hot' : '')}, it.kind === 'big' ? '큰 사건' : it.kind === 'goal' ? '팔로워 목표' : '화제'),
      el('span', {class: 'cp ' + (it.nums_ok ? 'ok' : 'stop')}, it.nums_ok ? '숫자 검사 통과' : '숫자 확인 필요'),
      it.confirmed ? null : el('span', {class: 'cp hot'}, '확인 안 된 이야기'), it.done ? el('span', {class: 'cp ok'}, '올림 ✓') : null),
    el('b', {class: 'kw'}, it.keyword || '오늘 글'),
    it.angle ? el('p', {class: 'an'}, `관점 · ${it.angle}`) : null,
    (it.warn || []).map(x => el('p', {class: 'w'}, x)),
    el('div', {class: 'po'}, it.preview),
    (it.sources || []).length ? el('div', {class: 'src'}, el('span', {}, '출처 기사'), links(it.sources)) : null,
    el('div', {class: 'rw'}, btn('pb', '열기 — 내 한마디 쓰고 올리기', () => open(it))),
    el('div', {class: 'rw'}, copyBtns(it)),
    el('div', {class: 'rw'}, box(it, '✓ 올렸어요'), btn('pb soft', '안 써요', () => skip(it.id))));
}
function drawScr() {
  if (!scr) return;
  const top = el('div', {class: 'top'}, el('a', {class: 'lnk', href: '#/sns/x'}, '‹ X(트위터)'), el('span', {class: 'sub'}, ''), btn('lnk', '다시 보기', load));
  if (!ST) return scr.replaceChildren(top, el('p', {class: 'boot'}, '불러오는 중…'));
  const empty = ST.slots.filter(t => !ST.items.some(i => i.slot === t)), a = ST.ai, b = ST.bigcfg;
  const rx = ST.react || [];
  scr.replaceChildren(...[top, focus === 'off' ? el('p', {class: 'w'}, OFF) : null, prog(),
    el('p', {class: 'note', style: 'margin:0 0 12px'}, `그날 올릴 글을 서버가 미리 써 둬요 (초안 ${ST.model}) · '내 한마디'와 올리기는 내가 · 자동으로 올리지 않아요`),
    ST.note ? el('p', {class: 'w'}, ST.note) : null,
    ST.big.length ? [el('p', {class: 'grp'}, `🔥 큰 사건 ${ST.big.length}`), ST.big.map(bigCard)] : null,
    el('p', {class: 'grp'}, `오늘 글 ${ST.items.length}개`),
    ST.items.length ? ST.items.map(itemCard) : el('p', {class: 'note'}, !ST.on ? '오늘 X 글 만들기가 꺼져 있어요' : ST.ran ? '오늘은 쓸 만한 글감이 아직 없어요' : `아직 만들기 전이에요${ST.next ? ` — ${ST.next} 에 만들어요` : ''}`),
    ST.err ? el('p', {class: 'w'}, ST.err) : null,
    ST.on && empty.length ? el('p', {class: 'note', style: 'margin:0 0 10px'}, `${empty.join(' · ')} 글은 아직 없어요${ST.next ? ` — ${ST.next} 에 보충해요` : ''}`) : null,
    ST.on && ST.free.length ? btn('pb soft', busy.fill ? '시작하는 중…' : '지금 보충하기', fill, {disabled: busy.fill || null}) : null,
    rx.length ? [el('p', {class: 'grp'}, `오늘 반응 할 것 ${rx.filter(r => r.done).length}/${rx.length}`), el('section', {class: 'box xtc'}, rx.map(rline)),
      el('p', {class: 'note', style: 'margin:0 0 10px'}, '자주 보는 계정은 X 글쓰기·답글 화면의 계정 칸에서 넣어요 · 누르기는 X 에서 내가')] : null,
    el('p', {class: 'note'}, `서버가 스스로 쓴 초안 ${a.used}/${a.cap}번 · 오늘 초안 ${a.left}번 남음` + (b.on ? ` · 큰 사건 알림은 점수 ${b.min_score} 넘으면 하루 ${b.per_day}개까지` : ' · 큰 사건 알림 꺼짐')),
    ST.log.length ? [el('p', {class: 'grp'}, '방금 본 뉴스 (점수)'), el('section', {class: 'box'}, ST.log.slice().reverse().map(x =>
      el('p', {class: 'sf'}, `${x.at} · ${[x.lead, x.shown].filter(Boolean).join(' ') || '-'} · ${x.score}점${x.why ? ` · ${x.why}` : ' · 알림'}`)))] : null,
  ].flat(9).filter(Boolean));
}
const inField = () => { const x = D.activeElement; return !!(scr && x && scr.contains(x) && /^(INPUT|TEXTAREA)$/.test(x.tagName)); };
function draw() { sync(); drawCard(); if (!inField()) drawScr(); }

// -- 붙이기 (app.js 가 #main 을 다시 그리면 다시 붙음 — xstudio.js 와 같은 틀)
const onScr = () => /^#\/?sns\/xt(\/[0-9a-z]*)?\/?$/.test(location.hash);
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true';
}
function title() {
  const t = D.getElementById('ttl');
  if (onScr() && t && t.textContent !== '오늘 X 글') t.textContent = '오늘 X 글';
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xt-css')) D.head.append(el('style', {id: 'xt-css', text: CSS}));
  if (onScr()) {
    const f = (/xt\/([0-9a-z]+)/.exec(location.hash) || [])[1] || '';
    if (!scr) { focus = f; scr = el('section', {id: 'xts', class: 'xs xt', 'aria-label': '오늘 X 글'}); drawScr(); }
    else if (f !== focus) { focus = f; drawScr(); }
    if (m.firstChild !== scr) m.prepend(scr);
    if (!m.hasAttribute('data-xt')) m.setAttribute('data-xt', '');
    title();
  } else {
    if (m.hasAttribute('data-xt')) m.removeAttribute('data-xt');
    if (scr && scr.parentNode) scr.remove();
  }
  if (!onScr() && onX() && !/^#\/?sns\/xs/.test(location.hash)) {
    if (!card) { card = el('article', {id: 'xtc', class: 'itm xsc xtc', 'aria-label': '오늘 X 글'}); drawCard(); }
    const x = D.getElementById('xsc'), a = x && x.parentNode === m ? x : D.getElementById('snt');  // '오늘 할 X 일' 카드 아래 (그 카드가 늘 칸 고르기 바로 밑)
    if (a.nextSibling !== card) a.after(card);
  } else if (card && card.parentNode) card.remove();
  if ((onScr() || onX()) && (!ST || Date.now() - at > 120000)) load();
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  const t = D.getElementById('ttl');
  if (t) new MutationObserver(title).observe(t, {childList: true, characterData: true, subtree: true});
  W.addEventListener('hashchange', () => { mount(); if (onScr()) W.scrollTo(0, 0); });
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (onScr() || onX())) load(); });
  mount();
}
W.SBXToday = {load, mount, open, state: () => ST};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
