/* X 반응 연속 모드 '조종석' (주인 11/15 'X 반응을 블로그처럼 한 번에' → 반자동). 셸이 defer 로 받음 (xstudio.js 다음) — xengage.py.
   SNS 탭 X 칸에 '반응 연속 모드' 카드 · 화면은 #/sns/xe. 오늘 반응할 글 목록을 한 글씩: 글 내용 · 답글 초안(미리 받아 둠) ·
   [X에서 이 글 열기] · [답글 쓰기](X 공식 글쓰기 창) · [다음 글]. ♥·리포스트·게시는 X 화면에서 내가 직접 누른다.
   이 파일은 X 의 단추를 누르지 않는다 — 누르기 흉내·키 이벤트·창 열기 함수가 없고, X 로 가는 길은 내가 누르는 링크(a)뿐이다.
   '다음 글'도 내가 누를 때만 (시간이 지났다고 저절로 넘어가지 않음 — 1초마다 도는 것은 남은 시간 글자뿐).
   목록은 'X 도우미' 책갈피가 넘긴 보이는 글(#/x/engage?k=…&l=… — 열쇠가 맞을 때만)과 붙여넣은 주소. 링크 앞부분은 서버 값 — 바깥 주소·innerHTML 없음. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xengage/', RGO = '/dashboard/api/xstudio/reply_go';
const CSS = '.xe .fl{margin:0 0 8px;font-size:16px}.xe textarea.fl{line-height:1.5;resize:vertical;min-height:56px}' +
  '.xe .chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}.xe .tg{min-height:40px}' +
  '.xe .kpi{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 10px}' +
  '.xe .kpi div{background:var(--tile);border:1px solid var(--line);border-radius:10px;padding:8px 10px;min-width:0}' +
  '.xe .kpi span{display:block;font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
  '.xe .kpi b{display:block;font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}' +
  '.xe .w{margin:0 0 6px;padding:8px 10px;border-radius:8px;background:var(--red-soft);color:var(--red);font-size:13px;line-height:1.45}' +
  '.xe .n{font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums;margin-left:auto}.xe .n.x{color:var(--or);font-weight:600}' +
  '.xe .rw{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0 0}' +
  '.xe .rw .pb{flex:1 1 130px;width:auto;margin:0;min-height:46px;text-decoration:none;padding:0 10px}.xe a.pb:not([href]){opacity:.45}' +
  '.xe .po{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;background:var(--tile);border-radius:10px;padding:10px;margin:0 0 10px}' +
  '.xe .po b{display:block;font-size:13px;color:var(--sub);margin:0 0 4px}.xe .hand{font-size:14px;margin:0 0 10px;padding:10px;border:1px dashed var(--line);border-radius:10px;line-height:1.5}' +
  '.xe .xt{display:flex;align-items:center;gap:8px;min-height:44px;border-top:1px solid var(--line);font-size:14px}.xe .xt:first-child{border:0}' +
  '.xe .xt .g{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.xe .tl{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}' +
  '.xe .tl a{font-size:13px;color:var(--ac-ink);text-decoration:none;border:1px solid var(--line);border-radius:14px;padding:6px 10px}' +
  '.xe .xl{font-size:12px;color:var(--sub);text-decoration:underline;background:none;border:0;padding:6px 0;min-height:32px}' +
  '.xec .ln{display:flex;justify-content:space-between;gap:8px;font-size:14px;padding:3px 0}.xec .ln span:last-child{color:var(--sub);text-align:right}' +
  '.xec .rw{display:flex;gap:6px;margin-top:10px}.xec .rw .pb{margin:0;min-height:44px}' +
  'main[data-xe]>:not(#xes){display:none!important}';
const HAND = '♥·리포스트는 X 화면에서 단추를 직접 눌러 주세요 — 이 도구는 X 에서 아무것도 누르지 않아요.';

let ST = null, at = 0, loading = false, scr = null, card = null, tt = null, tick = null, okAt = 0, rOkAt = 0, DL = null, dirty = false;
const busy = {};
const V = {tone: 'auto', dr: {}, ed: {}, job: {}};
try {
  const o = JSON.parse(W.localStorage.getItem('xe1') || 'null');
  if (o && typeof o === 'object' && Date.now() - o.t < 36 * 3600e3) Object.assign(V, o, {dr: o.dr || {}, ed: o.ed || {}, job: o.job || {}});
} catch (e) { /* 저장본 없음 */ }
function keep() { try { V.t = Date.now(); W.localStorage.setItem('xe1', JSON.stringify(V)); } catch (e) { /* 저장 못 해도 그대로 씀 */ } }

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
const NW = {target: '_blank', rel: 'noopener'};
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
function copy(text) {
  const c = W.navigator && W.navigator.clipboard;
  (c && c.writeText ? c.writeText(String(text)) : Promise.reject(new Error('x')))
    .then(() => toast('답글 복사했어요'), () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
}
async function call(url, body) {
  try {
    const r = await fetch(url, body ? {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)} : {credentials: 'same-origin', cache: 'no-store'});
    let d = {};
    try { d = await r.json(); } catch (e) { /* 빈 몸 */ }
    if (r.status === 403 && d.code === 'csrf') d.text = '화면이 오래됐어요 — 새로고침한 뒤 다시 눌러 주세요';
    if (r.status === 401) d.text = '로그인이 풀렸어요 — 새로고침해 주세요';
    return {s: r.status, d};
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요'}}; }
}
const wtxt = s => (s >= 3600 ? `${Math.floor(s / 3600)}시간 ${Math.floor(s % 3600 / 60)}분` : s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`);
const waitLeft = () => Math.max(0, Math.ceil((okAt - Date.now()) / 1000));
const rWaitLeft = () => Math.max(0, Math.ceil((rOkAt - Date.now()) / 1000));
function setPace(p, r) {
  if (p) { ST.pace = p; okAt = Date.now() + p.wait_s * 1000; }
  if (r) { ST.replies = r; rOkAt = Date.now() + r.wait_s * 1000; }
}
function apply(d) {
  if (!d || !Array.isArray(d.queue)) return;
  ST = d;
  setPace(d.pace, d.replies);
  const have = d.queue.map(q => q.sid);
  for (const k of Object.keys(V.dr)) if (!have.includes(k)) { delete V.dr[k]; delete V.ed[k]; delete V.job[k]; }
}
const opened = sid => ST.pace.opened.includes(sid);
// 아직 안 연 글을 지금 열어도 되는지 — 안 되면 까닭 (서버가 한 번 더 봄)
function paceNo(sid) {
  if (opened(sid)) return '';
  const w = waitLeft(), p = ST.pace;
  return w > 0 ? (p.why === 'gap' || !p.why ? `조금 쉬었다가 ${wtxt(w)} 뒤에 열어 주세요 (글 사이 1~2분 쉬기)` : p.text) : '';
}
// 글을 X 에서 연다 = 반응 1 (서버 답이 오기 전에도 바로 쉼)
function markOpen(sid) {
  if (opened(sid)) return;
  ST.pace.opened.push(sid);
  ST.pace.today += 1;
  okAt = Date.now() + ST.pace.gap[0] * 1000;
  call(API + 'open', {sid}).then(r => { setPace(r.d.pace); if (r.s !== 200 && r.d.text) toast(r.d.text); draw(); });
}
function rid(sid) {
  const j = V.job[sid];
  if (j && j.tone === V.tone && Date.now() - j.t < 300000) return j.rid;  // 끊겨서 다시 부르면 같은 일을 이어 받음
  const id = 'r_' + Array.from(W.crypto.getRandomValues(new Uint8Array(12)), x => (x % 36).toString(36)).join('');
  V.job[sid] = {rid: id, tone: V.tone, t: Date.now()};
  return id;
}

// -- 서버
async function load() {
  if (loading) return;
  loading = true;
  const r = await call(API + 'state');
  loading = false;
  at = Date.now();
  if (r.s === 200) apply(r.d); else if (!ST && r.d.text) toast(r.d.text);
  draw();
  if (ST && DL) await runDeep();
  prefetch();
}
// 지금 글과 다음 글의 답글 초안을 미리 (한 번에 하나 — 이 화면을 보고 있을 때만)
function prefetch() {
  if (!ST || !onScr() || busy.dr) return;
  const q = ST.queue.slice(0, 2).find(x => !V.dr[x.sid]);
  if (q) makeDraft(q.sid);
}
async function makeDraft(sid) {
  if (busy.dr) return;
  busy.dr = sid;
  delete V.dr[sid];
  draw();
  const r = await call(API + 'draft', {sid, tone: V.tone, rid: rid(sid)});
  const ok = r.s === 200 && Array.isArray(r.d.replies);
  busy.dr = '';
  delete V.job[sid];
  if (ST.queue.some(q => q.sid === sid)) {
    V.dr[sid] = ok ? {res: r.d} : {err: r.d.text || '답글 초안을 받지 못했어요'};
    delete V.ed[sid];
    if (ok) ST.left = r.d.left;
  }
  keep(); draw();
  if (ok) prefetch();
}
async function next(sid) {
  if (busy.step) return;
  busy.step = true;
  const r = await call(API + 'step', {sid, op: 'next'});
  busy.step = false;
  if (r.s === 200) { apply(r.d); keep(); draw(true); W.scrollTo(0, 0); prefetch(); return; }
  setPace(r.d.pace);
  toast(r.d.text || '넘기지 못했어요 — 다시 눌러 주세요');
  if (r.s === 404) load(); else draw();
}
async function runDeep() {
  const d = DL;
  DL = null;
  const r = await call(API + 'handoff', d);
  apply(r.d);
  toast(r.d.text || (r.s === 200 ? '받았어요' : '받지 못했어요'));
  keep(); draw(true);
}

// -- 그리기
function field(tag, attr, get, set, after) {
  const e = el(tag, Object.assign({class: 'fl'}, attr));
  e.value = get() || '';
  e.addEventListener('input', () => { set(e.value); keep(); if (after) after(); });
  return e;
}
function paceBox() {
  const p = ST.pace, w = waitLeft();
  return el('section', {class: 'box', id: 'xep'},
    el('div', {class: 'kpi'}, el('div', {}, el('span', {}, '오늘 반응한 글'), el('b', {}, `${p.today}/${p.day_cap}`)),
      el('div', {}, el('span', {}, '최근 1시간'), el('b', {}, `${p.hour}/${p.hour_cap}`)),
      el('div', {}, el('span', {}, '다음 글'), el('b', {}, p.why === 'day' ? '내일' : w ? wtxt(w) : '지금'))),
    el('p', {class: 'note'}, `글 사이 ${p.gap[0] / 60}~${p.gap[1] / 60}분 쉬기 · 한 시간 ${p.hour_cap}개 · 하루 ${p.day_cap}개 · 넘기기는 내가 누를 때만`));
}
// 답글 초안 하나: 고치는 칸 + [답글 쓰기] (X 공식 글쓰기 창 — 게시 단추는 거기서 내가)
function replyCard(q, d, i) {
  const E = V.ed[q.sid] || (V.ed[q.sid] = {}), n = el('span', {class: 'n'}), a = el('a', Object.assign({class: 'pb'}, NW), '답글 쓰기');
  const up = () => {
    const t = ta.value.trim(), [lo, hi] = ST.limits.reply;
    n.textContent = `${t.length}자`; n.className = 'n' + (t.length < lo || t.length > hi ? ' x' : '');
    if (t) a.setAttribute('href', ST.links.reply + q.sid + '&text=' + encodeURIComponent(t)); else a.removeAttribute('href');
  };
  const ta = field('textarea', {rows: 2, 'aria-label': `답글 ${i + 1}`}, () => (E[i] != null ? E[i] : d.text), v => { E[i] = v; }, up);
  a.addEventListener('click', e => {
    const w = rWaitLeft(), R = ST.replies;
    const no = !a.getAttribute('href') ? '답글이 비었어요' : R.sids.includes(q.sid) ? '이 글에는 오늘 이미 답글을 달았어요'
      : w > 0 ? `${wtxt(w)} 뒤에 달 수 있어요 — 답글 사이 1~2분 쉬기` : R.wait_s ? R.text : paceNo(q.sid);
    if (no) { e.preventDefault(); return toast(no); }
    rOkAt = Date.now() + R.gap[0] * 1000;
    R.sids.push(q.sid);
    markOpen(q.sid);
    call(RGO, {op: 'go', sid: q.sid}).then(r => { setPace(null, r.d.replies); if (r.s !== 200 && r.d.text) toast(r.d.text); draw(); });
    setTimeout(draw, 300);  // 바로 다시 그리면 누른 링크가 화면에서 빠져 X 가 안 열림
  });
  up();
  return el('section', {class: 'itm', 'data-r': i}, (d.warn || []).map(x => el('p', {class: 'w'}, x)), ta,
    el('div', {class: 'rw'}, a, btn('pb soft', '복사', () => copy(ta.value.trim())), n));
}
function current(q, i, total) {
  const dr = V.dr[q.sid], res = dr && dr.res, post = (res && res.post) || {}, isOpen = opened(q.sid);
  const go = el('a', Object.assign({class: 'pb', href: q.url}, NW), isOpen ? 'X에서 이 글 다시 열기' : 'X에서 이 글 열기');
  go.addEventListener('click', e => {
    const no = paceNo(q.sid);
    if (no) { e.preventDefault(); return toast(no); }
    markOpen(q.sid);
    setTimeout(draw, 300);
  });
  const out = [el('p', {class: 'grp'}, `지금 글 ${i + 1}/${total}` + (q.h ? ` · @${q.h}` : '') + (isOpen ? ' · 열어 봄' : '')),
    res ? el('div', {class: 'po'}, el('b', {}, (post.author ? `@${post.author} · ` : '') + '서버가 읽은 글'), post.text || '') : null,
    el('div', {class: 'rw', style: 'margin:0 0 10px'}, go),
    el('p', {class: 'hand'}, HAND)];
  if (busy.dr === q.sid) out.push(el('p', {class: 'note'}, '답글 초안 쓰는 중… 20~40초 걸려요 (그동안 글을 열어 ♥·리포스트를 해도 돼요)'));
  else if (res) out.push(el('p', {class: 'grp'}, `답글 초안 ${res.replies.length}개 — 참고만, 내 말로 고쳐서`), res.replies.map((d, k) => replyCard(q, d, k)));
  else if (dr) out.push(el('p', {class: 'w'}, `${dr.err} (답글 없이 ♥·리포스트만 해도 돼요)`));
  out.push(el('div', {class: 'chips', style: 'margin-top:10px'}, ST.tones.map(([k, l]) => btn('tg', l, () => { V.tone = k; keep(); draw(true); }, {'aria-pressed': V.tone === k ? 'true' : 'false'}))),
    el('div', {class: 'rw'}, btn('pb soft', res || dr ? '초안 다시 받기' : '답글 초안 받기', () => makeDraft(q.sid), {disabled: !!busy.dr || null}),
      btn('pb', i + 1 < total ? '다음 글' : '다음 글 (마지막)', () => next(q.sid), {disabled: busy.step || null})),
    el('p', {class: 'note'}, `초안 ${ST.model} · 오늘 ${ST.left}번 남음 · 게시는 X 글쓰기 창에서 내가`));
  return out;
}
function addBox() {
  const ta = el('textarea', {class: 'fl', rows: 3, maxlength: ST.limits.add, placeholder: 'X 글 주소 붙여넣기 (여러 개면 줄을 바꿔서)', 'aria-label': 'X 글 주소들'});
  const send = async () => {
    if (!ta.value.trim()) return;
    const r = await call(API + 'add', {text: ta.value});
    apply(r.d);
    toast(r.d.text || (r.s === 200 ? '넣었어요' : '넣지 못했어요'));
    keep(); draw(true); prefetch();
  };
  return [el('p', {class: 'grp'}, `글 모으기 (목록 ${ST.queue.length}/${ST.limits.queue})`),
    el('section', {class: 'box'}, el('p', {class: 'note', style: 'margin:0 0 8px'}, "사파리에서 아래 계정·화제를 열고 'X 도우미' 책갈피를 누르면 그때 보이는 글이 목록에 들어와요 (홈·검색 화면도 됨). " +
      `한 계정 글은 ${ST.limits.per_handle}개까지 · 서버는 X 에 로그인하지 않고 계정 글을 긁지 않아요.`),
      ST.targets.length ? el('div', {class: 'tl'}, ST.targets.map(t => el('a', Object.assign({href: ST.links.profile + t.h}, NW), '@' + t.h)))
        : el('p', {class: 'note', style: 'margin:0 0 8px'}, "자주 보는 계정이 없어요 — 'X 글쓰기·답글'의 계정 칸에서 넣어 두면 여기에 나와요"),
      ST.hot.length ? el('div', {class: 'tl'}, ST.hot.map(h => el('a', Object.assign({href: h.url}, NW), '화제 · ' + h.keyword))) : null),
    ta, btn('pb soft', '목록에 넣기', send)];
}
function drawScr() {
  if (!scr) return;
  const top = el('div', {class: 'top'}, el('a', {class: 'lnk', href: '#/sns/x'}, '‹ X(트위터)'), el('span', {class: 'sub'}, ''), btn('lnk', '다시 보기', load));
  if (!ST) return scr.replaceChildren(top, el('p', {class: 'boot'}, '불러오는 중…'));
  const Q = ST.queue, p = ST.pace, out = [top, paceBox()];
  if (!Q.length) out.push(el('p', {class: 'note'}, '반응할 글 목록이 비었어요 — 아래에서 글을 모아 주세요'), el('p', {class: 'hand'}, HAND));
  else if (p.why === 'day' && !opened(Q[0].sid)) out.push(el('p', {class: 'w'}, p.text));
  else out.push(current(Q[0], 0, Q.length));
  if (Q.length > 1) {
    out.push(el('p', {class: 'grp'}, `다음 차례 ${Q.length - 1}개`),
      el('section', {class: 'box'}, Q.slice(1).map(q => el('div', {class: 'xt'}, el('span', {class: 'g'}, (q.h ? `@${q.h} · ` : '') + '…' + q.sid.slice(-6)),
        btn('sm', '빼기', () => next(q.sid))))),
      btn('xl', '안 연 글 모두 비우기', async () => { const r = await call(API + 'step', {op: 'clear'}); apply(r.d); keep(); draw(true); }));
  }
  out.push(addBox());
  scr.replaceChildren(...out.flat(9).filter(Boolean));
  clearInterval(tick);
  // 1초마다 남은 시간 글자만 다시 그린다 — 다음 글로 넘어가지 않는다
  tick = waitLeft() ? setInterval(() => { const b = D.getElementById('xep'); if (!scr.parentNode || !b) return clearInterval(tick); b.replaceWith(paceBox()); if (!waitLeft()) clearInterval(tick); }, 1000) : null;
}
function drawCard() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, 'X 반응 연속 모드 불러오는 중…'));
  const p = ST.pace, w = waitLeft();
  card.replaceChildren(el('div', {class: 'ih'}, el('b', {class: 'ti'}, '반응 연속 모드'), el('a', {class: 'lnk', href: '#/sns/xe'}, '열기 ›')),
    el('div', {class: 'ln'}, el('span', {}, `반응할 글 ${ST.queue.length}개`), el('span', {}, p.why === 'day' ? '오늘 몫 끝' : w ? `${wtxt(w)} 쉬는 중` : `오늘 ${p.today}/${p.day_cap}`)),
    el('div', {class: 'rw'}, btn('pb', ST.queue.length ? '반응 연속 모드 시작' : '반응할 글 모으기', () => { location.hash = '#/sns/xe'; })));
}
const inField = () => { const a = D.activeElement; return !!(scr && a && scr.contains(a) && /^(INPUT|TEXTAREA)$/.test(a.tagName)); };
function draw(force) {
  drawCard();
  if (!force && inField()) { dirty = true; return; }  // 글을 고치는 중이면 다음에
  dirty = false;
  drawScr();
}

// -- 붙이기 (xstudio.js 와 같은 틀 — app.js 가 #main 을 다시 그리면 다시 붙음)
const onScr = () => /^#\/?sns\/xe\/?$/.test(location.hash);
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true' && !/^#\/?sns\/xs/.test(location.hash);
}
function title() {
  const t = D.getElementById('ttl');
  if (onScr() && t && t.textContent !== 'X 반응 연속 모드') t.textContent = 'X 반응 연속 모드';
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xe-css')) D.head.append(el('style', {id: 'xe-css', text: CSS}));
  if (onScr()) {
    if (!scr) { scr = el('section', {id: 'xes', class: 'xe', 'aria-label': 'X 반응 연속 모드'}); drawScr(); }
    if (m.firstChild !== scr) m.prepend(scr);
    if (!m.hasAttribute('data-xe')) m.setAttribute('data-xe', '');
    title();
  } else {
    if (m.hasAttribute('data-xe')) m.removeAttribute('data-xe');
    if (scr && scr.parentNode) scr.remove();
  }
  if (!onScr() && onX()) {
    if (!card) { card = el('article', {id: 'xec', class: 'itm xec', 'aria-label': '반응 연속 모드'}); drawCard(); }
    // '오늘 X 글' 카드(xtoday.js) 아래 → 없으면 '오늘 할 X 일' 카드 아래 → 칸 고르기 아래. xtoday 카드도 '오늘 할 X 일' 바로 밑에 붙으므로
    // 같은 자리를 다투면 두 파일이 서로 끝없이 옮긴다 (둘 다 #main 을 지켜봄) — 그래서 늘 그 카드 뒤에 선다
    const s = D.getElementById('xtc') || D.getElementById('xsc') || D.getElementById('snt');
    if (s.nextSibling !== card) s.after(card);
  } else if (card && card.parentNode) card.remove();
  if ((onScr() || onX()) && (!ST || Date.now() - at > 60000)) load();
}
// 책갈피가 넘긴 보이는 글: #/x/engage?k=…&l=아이디/글번호,…[&h=&fo=] → 값을 받아 두고 #/sns/xe 로 (열쇠는 서버가 봄)
function deep() {
  const m = /^#\/?x\/engage(?:\?(.*))?$/.exec(location.hash);
  if (!m) return;
  const q = new URLSearchParams(m[1] || ''), g = (k, n) => (q.get(k) || '').slice(0, n);
  DL = {k: g('k', 80), l: g('l', 4000), h: g('h', 15), fo: g('fo', 13)};
  try { W.opener = null; } catch (e) { /* 연 창을 건드리지 않음 */ }
  location.replace('#/sns/xe');
}
function start() {
  deep();
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  const t = D.getElementById('ttl');
  if (t) new MutationObserver(title).observe(t, {childList: true, characterData: true, subtree: true});
  W.addEventListener('hashchange', () => { deep(); mount(); if (onScr()) { W.scrollTo(0, 0); if (ST && DL) runDeep(); else prefetch(); } });
  D.addEventListener('focusout', () => setTimeout(() => { if (dirty) draw(); }, 80));
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (onScr() || onX())) load(); });
  mount();
}
W.SBXEngage = {load, mount, state: () => ST, view: V};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
