/* X 글쓰기 · 답글 도구 (주인 11/14 20:2x '글 쓰는 툴, 댓글 다는 툴 … 완성된 퀄리티 높은 툴'). 셸이 defer 로 받음 (app.js 는 예산 끝) — xstudio.py.
   SNS 탭 X 칸 맨 위에 '오늘 할 X 일' 카드 · 화면은 #/sns/xs (SNS 탭 안 — 다른 칸은 숨김) · 글쓰기 / 답글 / 계정 / 도우미.
   서버는 초안만 (Opus 까지) — '내 한마디'를 직접 써야 'X에서 올리기'가 켜지고, 올리기·답글은 X 글쓰기 화면에서 내가 누른다.
   바로 가기: #/x/reply?u=<X 글 주소> · #/x/write?u=<주소>&q=<글> (공유 시트 단축어 · 'X 도우미' 책갈피 static/xhelper.js) → 바로 초안.
   링크 앞부분은 서버 값(state.links) — 이 파일에 바깥 주소·innerHTML 없음. 고치던 글은 이 기기에만 (localStorage xs1 · 36시간). */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xstudio/';
const CSS = '.xs .fl{margin:0 0 8px;font-size:16px}.xs textarea.fl{line-height:1.5;resize:vertical}.xs textarea.short{min-height:56px}' +
  '.xs .chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}.xs .tg{min-height:40px}' +
  '.xs .kpi,.xsc .kpi{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 10px}' +
  '.xs .kpi div,.xsc .kpi div{background:var(--tile);border:1px solid var(--line);border-radius:10px;padding:8px 10px;min-width:0}' +
  '.xs .kpi span,.xsc .kpi span{display:block;font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
  '.xs .kpi b,.xsc .kpi b{display:block;font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}' +
  '.xs .w{margin:0 0 6px;padding:8px 10px;border-radius:8px;background:var(--red-soft);color:var(--red);font-size:13px;line-height:1.45}' +
  '.xs .sf{margin:0 0 4px;font-size:12px;color:var(--sub);line-height:1.45}.xs .an{margin:0 0 8px;font-size:13px;color:var(--sub)}' +
  '.xs .n{font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums;margin-left:auto}.xs .n.x{color:var(--or);font-weight:600}' +
  '.xs .rw{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:6px 0 0}' +
  '.xs .rw .pb{flex:1 1 130px;width:auto;margin:0;min-height:46px;text-decoration:none;padding:0 10px}.xs a.pb:not([href]){opacity:.45}' +
  '.xs .xk{display:flex;align-items:center;gap:8px;min-height:44px;font-size:14px}.xs .xk[hidden]{display:none}.xs .xk input{width:20px;height:20px;flex:none}' +
  '.xs .xh{display:block;width:100%;text-align:left;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);' +
  'padding:10px 12px;margin:0 0 6px;font:inherit;font-size:14px}.xs .xh[aria-pressed=true]{border-color:var(--text);background:var(--ac-soft)}' +
  '.xs .xh b{display:block;font-size:15px}.xs .xh span{color:var(--sub)}.xs .po{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;' +
  'background:var(--tile);border-radius:10px;padding:10px;margin:0 0 10px}.xs .po b{display:block;font-size:13px;color:var(--sub);margin:0 0 4px}.xs .po[hidden]{display:none}.xs .itm>.seg{margin:0 0 8px}' +
  '.xs ol{margin:0 0 10px;padding-left:20px;font-size:14px;line-height:1.55}.xs .key{font-family:ui-monospace,Menlo,monospace;font-size:12px;' +
  'word-break:break-all;user-select:all;background:var(--tile);border-radius:8px;padding:8px;margin:0 0 8px}' +
  '.xs .xt{display:flex;align-items:center;gap:8px;min-height:48px;border-top:1px solid var(--line)}.xs .xt:first-child{border:0}' +
  '.xs .xt .g{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.xs .xt i{font-style:normal;color:var(--sub);font-size:13px}' +
  '.xs .xl{font-size:12px;color:var(--sub);text-decoration:underline;background:none;border:0;padding:6px 0;min-height:32px}' +
  '.xsc .ln{display:flex;justify-content:space-between;gap:8px;font-size:14px;padding:3px 0}.xsc .ln span:first-child{flex:none}.xsc .ln span:last-child{color:var(--sub);text-align:right}' +
  '.xsc .rw{display:flex;gap:6px;margin-top:10px}.xsc .rw .pb{margin:0;min-height:44px}.xsc .tl{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}' +
  '.xsc .tl a{font-size:13px;color:var(--ac-ink);text-decoration:none;border:1px solid var(--line);border-radius:14px;padding:4px 10px}' +
  'main[data-xs]>:not(#xss){display:none!important}';
const TABS = [['write', '글쓰기'], ['reply', '답글'], ['list', '계정'], ['help', '도우미']];
const KINDS = [['hot', '화제'], ['paste', '기사·글'], ['topic', '주제 한 줄']];
const ONE = [[0, 4351], [8192, 8205], [8208, 8223], [8242, 8247]];
const SID = /^(?:https?:\/\/)?(?:(?:www|mobile|m)\.)?(?:x|twitter)\.com\/(?:i\/(?:web\/)?|(?:#!\/)?\w{1,15}\/)status(?:es)?\/(\d{5,20})(?:[/?#].*)?$/i;
const NUM = /\d[\d,]*(?:\.\d+)?/g;
const BAD = /https?:\/\/|www\.|[#@]/i;

let ST = null, at = 0, loading = false, scr = null, card = null, tt = null, tick = null, okAt = 0, dirty = false, DL = null, HP = null;
const busy = {};
const V = {tab: 'write', kind: 'hot', hot: '', url: '', text: '', topic: '', fmt: 'auto', long: true, res: null, ed: {}, el: {}, lg: {}, mine: {}, chk: {}, done: {},
  r: {url: '', text: '', tone: 'agree', res: null, ed: {}, meta: null}, job: {}};
try {
  const o = JSON.parse(W.localStorage.getItem('xs1') || 'null');
  if (o && typeof o === 'object' && Date.now() - o.t < 36 * 3600e3) { Object.assign(V, o); V.r = Object.assign({url: '', text: '', tone: 'agree', res: null, ed: {}, meta: null}, o.r); }
} catch (e) { /* 저장본 없음 */ }
function keep() { try { V.t = Date.now(); W.localStorage.setItem('xs1', JSON.stringify(V)); } catch (e) { /* 저장 못 해도 그대로 씀 */ } }

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
const nf = n => (n == null ? '–' : Number(n).toLocaleString('ko-KR'));
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
function copy(text, what) {
  const c = W.navigator && W.navigator.clipboard;
  (c && c.writeText ? c.writeText(String(text)) : Promise.reject(new Error('x')))
    .then(() => toast(`${what || ''} 복사했어요`.trim()), () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
}
async function call(path, body) {
  try {
    const r = await fetch(API + path, body ? {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)} : {credentials: 'same-origin', cache: 'no-store'});
    let d = {};
    try { d = await r.json(); } catch (e) { /* 빈 몸 */ }
    if (r.status === 403 && d.code === 'csrf') d.text = '화면이 오래됐어요 — 새로고침한 뒤 다시 눌러 주세요';
    if (r.status === 401) d.text = '로그인이 풀렸어요 — 새로고침해 주세요';
    return {s: r.status, d};
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요 (쓰던 초안은 이어서 받아요)'}}; }
}

// -- 글자 세기·검사 (xstudio.py 와 같은 셈)
function xlen(t) {
  let n = 0;
  const u = ((ST || {}).limits || {}).url_len || 23;
  t = String(t || '').replace(/https?:\/\/\S+/g, () => { n += u; return ''; });
  for (const c of t) { const o = c.codePointAt(0); n += ONE.some(([a, b]) => o >= a && o <= b) ? 1 : 2; }
  return n;
}
// X 셈으로 처음 280 이 끝나는 자리 (타임라인에 보이는 부분)
function cut(t, lim) {
  let n = 0, i = 0;
  for (const c of t) { const o = c.codePointAt(0); n += ONE.some(([a, b]) => o >= a && o <= b) ? 1 : 2; if (n > lim) return i; i += c.length; }
  return t.length;
}
const norm = s => { const [w, f] = s.replace(/,/g, '').split('.'), g = (f || '').replace(/0+$/, ''), h = w.replace(/^0+/, '') || '0'; return g ? h + '.' + g : h; };
const numsOf = t => [...new Set((String(t || '').match(NUM) || []).map(norm))];
const missing = (t, src) => numsOf(t).filter(n => n.replace('.', '').length >= 2 && !src.includes(n));
const firstUrl = s => { const m = /https?:\/\/[^\s"'<>]+/.exec(String(s || '')); return m ? m[0] : String(s || '').trim(); };
const sid = u => { const s = String(u || '').trim(); return /^\d{10,20}$/.test(s) ? s : (SID.exec(firstUrl(s)) || [])[1] || ''; };
// 빨간 경고 (주인이 확인해야 올림) — 고칠 때마다 다시 봄
function warns(body, R) {
  const L = ST.limits, w = [], m = missing(body, R.src_nums || []);
  if (m.length) w.push(`원문에 없는 숫자: ${m.slice(0, 6).join(', ')} — 원문 숫자로 고치거나 직접 확인해 주세요`);
  if (R.confirmed === false && !(ST.hedge || []).some(h => body.includes(h))) w.push("확인 안 된 이야기인데 '의혹·보도·~라고 함' 같은 말이 없어요");
  const soft = [], first = body.split('\n')[0].trim();
  if (BAD.test(body)) soft.push('본문에 링크·#·@ 는 빼 주세요 (출처는 첫 답글로)');
  if (first.length > L.first) soft.push(`첫 줄 ${first.length}자 — ${L.first}자 안이 눈에 잘 들어와요`);
  return {w, soft};
}
const rowsOf = t => Math.max(5, Math.min(18, String(t).split('\n').reduce((a, l) => a + 1 + Math.floor(l.length / 21), 1)));
const waitLeft = () => Math.max(0, Math.ceil((okAt - Date.now()) / 1000));
const wtxt = s => (s >= 3600 ? `${Math.floor(s / 3600)}시간 ${Math.floor(s % 3600 / 60)}분` : s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`);
function setPace(p) { if (!p) return; ST.replies = p; okAt = Date.now() + p.wait_s * 1000; }
function rid(kind, key) {
  const j = V.job[kind];
  if (j && j.key === key && Date.now() - j.t < 300000) return j.rid;  // 끊겨서 다시 누르면 같은 일을 이어 받음
  const id = 'r_' + Array.from(W.crypto.getRandomValues(new Uint8Array(12)), x => (x % 36).toString(36)).join('');
  V.job[kind] = {key, rid: id, t: Date.now()};
  return id;
}

// -- 서버
async function load() {
  if (loading) return;
  loading = true;
  const r = await call('state');
  loading = false;
  at = Date.now();
  if (r.s === 200) { ST = r.d; okAt = Date.now() + ST.replies.wait_s * 1000; if (!ST.hot.some(h => h.id === V.hot)) V.hot = (ST.hot[0] || {}).id || ''; if (V.kind === 'hot' && !ST.hot.length) V.kind = 'paste'; }
  else if (!ST && r.d.text) toast(r.d.text);
  draw();
  if (ST && DL) runDeep();
}
async function makePost() {
  if (busy.post || !ST) return;
  const b = {kind: V.kind, format: V.fmt, long: V.long !== false};
  if (V.kind === 'hot') b.hot_id = V.hot; else if (V.kind === 'topic') b.topic = V.topic.trim(); else { b.url = V.url.trim(); b.text = V.text; }
  b.rid = rid('post', JSON.stringify(b));
  busy.post = true; keep(); draw(true);
  const r = await call('draft', b);
  busy.post = false;
  if (r.s === 200) { V.res = r.d; V.res.fmt = V.fmt; V.ed = {}; V.el = {}; V.lg = {}; V.mine = {}; V.chk = {}; V.done = {}; delete V.job.post; if (ST.left) ST.left.draft = r.d.left; }
  else toast(r.d.text || '초안을 쓰지 못했어요 — 잠시 뒤 다시');
  keep(); draw(true);
}
async function makeReply() {
  if (busy.reply || !ST) return;
  const R = V.r, b = {url: firstUrl(R.url), text: R.text, tone: R.tone};
  if (!sid(b.url)) return toast('X 글 주소를 넣어 주세요 (x.com/아이디/status/숫자)');
  b.rid = rid('reply', JSON.stringify(b));
  busy.reply = true; keep(); draw(true);
  const r = await call('reply', b);
  busy.reply = false;
  if (r.s === 200) { R.res = r.d; R.ed = {}; delete V.job.reply; if (!R.text.trim() && r.d.post) R.text = r.d.post.text; }
  else toast(r.d.text || '답글 초안을 쓰지 못했어요 — 잠시 뒤 다시');
  keep(); draw(true);
}
// 바로 가기 주소로 온 값 (공유 시트 단축어 · X 도우미 책갈피)
async function runDeep() {
  const d = DL;
  DL = null;
  if (d.a === 'reply') {
    setTab('reply');
    V.r = {url: firstUrl(d.u), text: d.t, tone: V.r.tone, res: null, ed: {}, meta: d.h || d.c ? {h: d.h, v: d.v === '1', c: d.c} : null};
    return makeReply();
  }
  if (d.a === 'write') {
    setTab('write'); V.res = null;
    if (d.hot && ST.hot.some(h => h.id === d.hot)) { V.kind = 'hot'; V.hot = d.hot; }
    else { V.kind = 'paste'; V.url = firstUrl(d.u); V.text = d.t; }
    V.fmt = ST.formats.some(f => f[0] === d.f) ? d.f : sid(V.url) && V.kind === 'paste' ? 'quote' : V.fmt;
    if (V.kind === 'hot' || V.url || V.text.trim().length >= 30) return makePost();
    return draw(true);
  }
  setTab(d.a === 'target' ? 'list' : 'write');
  const r = await call('handoff', d);
  if (r.d.progress) ST.progress = r.d.progress;
  if (r.d.targets) ST.targets = r.d.targets;
  toast(r.d.text || (r.s === 200 ? '받았어요' : '받지 못했어요'));
  keep(); draw(true);
}

// -- 그리기
const sub = () => { const x = (ST || {}).model; return x ? `초안 ${x} · 마지막 한마디와 올리기는 내가` : ''; };
function progLine() {
  const p = ST && ST.progress;
  if (!p) return null;
  const e = [p.eta_verified && p.eta_verified !== '달성' ? `인증 팔로워 ${p.eta_verified}` : '', p.eta_impr && p.eta_impr !== '달성' ? `노출 ${p.eta_impr}` : ''].filter(Boolean);
  return [`인증 팔로워 ${nf(p.verified)}/${nf(p.verified_goal)} · 90일 노출 ${nf(p.impr)}/${nf(p.impr_goal)}`,
    `${p.at} 기록${p.auto ? ' (X 도우미)' : ''}${e.length ? ' · 지금 속도면 ' + e.join(' · ') : ''}`];
}
function drawCard() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, 'X 글쓰기·답글 도구 불러오는 중…'));
  const p = ST.posts, r = ST.replies, g = ST.progress, ts = ST.targets.slice(0, 6), w = waitLeft();
  card.replaceChildren(...[
    el('div', {class: 'ih'}, el('b', {class: 'ti'}, '오늘 할 X 일'), el('a', {class: 'lnk', href: '#/sns/xs'}, '열기 ›')),
    el('div', {class: 'ln'}, el('span', {}, `글 ${p.today}/${p.target.join('~')}개`), el('span', {}, p.today >= p.target[1] ? '오늘 몫 끝' : p.next ? `다음 추천 ${p.next}` : `추천 ${p.times.join(' · ')}`)),
    el('div', {class: 'ln'}, el('span', {}, `답글 ${r.today}/${ST.reply_goal}개`), el('span', {}, r.why === 'day' ? '오늘 몫 끝' : w ? `${wtxt(w)} 쉬는 중` : `한 시간 ${r.hour}/${r.hour_cap}`)),
    g ? el('div', {class: 'ln'}, el('span', {}, `인증 팔로워 ${nf(g.verified)}/${nf(g.verified_goal)}`), el('span', {}, `90일 노출 ${nf(g.impr)}/${nf(g.impr_goal)}`)) : null,
    ts.length ? el('div', {class: 'tl'}, ts.map(t => el('a', Object.assign({href: ST.links.profile + t.h}, NW), '@' + t.h))) : null,
    el('div', {class: 'rw'}, btn('pb', '글쓰기', () => go('write')), btn('pb soft', '답글 달기', () => go('reply')))].filter(Boolean));
}
function go(t) { V.tab = t; keep(); location.hash = '#/sns/xs/' + t; }
function setTab(t) {
  V.tab = t;
  if (onScr() && W.history && W.history.replaceState) W.history.replaceState(null, '', '#/sns/xs/' + t);  // app.js 가 다시 그려도 이 칸 그대로
}
const seg = (xs, cur, f) => el('div', {class: 'seg', role: 'group'}, xs.map(([k, l]) => btn('', l, () => f(k), {'aria-pressed': cur === k ? 'true' : 'false'})));
const chips = (xs, cur, f) => el('div', {class: 'chips'}, xs.map(([k, l]) => btn('tg', l, () => { f(k); keep(); draw(true); }, {'aria-pressed': cur === k ? 'true' : 'false'})));
function field(tag, attr, get, set, after) {
  const e = el(tag, Object.assign({class: 'fl'}, attr));
  e.value = get() || '';
  e.addEventListener('input', () => { set(e.value); keep(); if (after) after(); });
  return e;
}

// 초안 하나: 고치는 칸 + 내 한마디 + 글자 수 + 경고 + X에서 올리기
function draftCard(d, i) {
  const R = V.res, L = ST.limits, LK = ST.links, q = (R.source || {}).quote_url || '', lg = !!(d.long && V.lg[i]);
  const n = el('span', {class: 'n'}), box = el('div'), why = el('p', {class: 'sf'}), pv = el('div', {class: 'po'});
  const post = el('a', Object.assign({class: 'pb'}, NW), q ? 'X에서 인용으로 올리기' : 'X에서 올리기');
  const sch = el('a', Object.assign({class: 'xl'}, NW), 'x.com 웹에서 예약하기');
  const ck = el('input', {type: 'checkbox'}), ckl = el('label', {class: 'xk'}, ck, '숫자·사실을 원문에서 직접 확인했어요');
  ck.checked = !!V.chk[i];
  const full = () => { const b = body.value.trim(), m = mine.value.trim(); return m ? b + '\n\n' + m : b; };
  const up = () => {
    const b = body.value.trim(), m = mine.value.trim(), t = full(), c = xlen(t), {w, soft} = warns(b, R);
    const need = t.includes(ST.need), over = c > L.x, head = t.slice(0, cut(t, L.x)), used = numsOf(b).filter(x => x.replace('.', '').length >= 2 && (R.src_nums || []).includes(x));
    body.setAttribute('rows', rowsOf(body.value));
    n.textContent = over ? `긴 글 ${t.length.toLocaleString('ko-KR')}자 · X 셈 ${c}` : `${c}/${L.x}`;
    n.className = 'n' + (over && !lg ? ' x' : '');
    if (over && used.length && !used.some(x => numsOf(head).includes(x))) soft.push(`핵심 숫자가 처음 ${L.x} 안에 없어요 — 위로 올려 주세요`);
    if (over && !lg) soft.push(`${L.x} 을 넘어 '긴 글'로 올라가요 — 짧게 올리려면 줄여 주세요`);
    box.replaceChildren(...w.map(x => el('p', {class: 'w'}, x)), ...(need ? [el('p', {class: 'w'}, `'${ST.need}' 자리를 직접 확인해서 채워 주세요`)] : []),
      ...soft.map(x => el('p', {class: 'sf'}, x)));
    pv.hidden = !over;  // 타임라인에는 처음 280 만 — 훅과 핵심 숫자가 이 안에 있어야 함
    if (over) pv.replaceChildren(el('b', {}, `타임라인에 보이는 부분 (처음 ${L.x})`), head.trimEnd() + ' … 더 보기');
    ckl.hidden = !w.length;
    const no = !b ? '본문이 비었어요' : m.replace(/\s/g, '').length < L.mine_min ? `'내 한마디'를 직접 써야 올릴 수 있어요 (${L.mine_min}자 넘게)`
      : need ? `'${ST.need}' 자리를 먼저 채워 주세요` : w.length && !ck.checked ? '빨간 경고를 확인하고 체크해 주세요' : '';
    why.textContent = no;
    for (const a of [post, sch]) {
      if (no) a.removeAttribute('href'); else a.setAttribute('href', LK.post + encodeURIComponent(t) + (q ? '&url=' + encodeURIComponent(q) : ''));
      a.setAttribute('aria-disabled', no ? 'true' : 'false');
    }
    post.dataset.no = no;
  };
  const E = lg ? V.el : V.ed;
  const body = field('textarea', {rows: 7, maxlength: L.long_max, 'aria-label': `초안 ${i + 1}`}, () => (E[i] != null ? E[i] : lg ? d.long : d.text), v => { E[i] = v; }, up);
  const mine = field('textarea', {class: 'fl short', rows: 2, 'aria-label': `내 한마디 ${i + 1}`, placeholder: '내 한마디 — 내 생각 한 줄 (직접 써야 올릴 수 있어요)'},
    () => V.mine[i], v => { V.mine[i] = v; }, up);
  ck.addEventListener('change', () => { V.chk[i] = ck.checked; keep(); up(); });
  const stop = e => { if (!e.currentTarget.getAttribute('href')) { e.preventDefault(); toast(post.dataset.no || '아직 올릴 수 없어요'); } };
  post.addEventListener('click', stop);
  sch.addEventListener('click', e => { stop(e); if (e.currentTarget.getAttribute('href')) toast("사파리에서 열리면 달력 단추로 예약 — X 앱이 열리면 '글 복사' 후 x.com 웹에 붙여넣기"); });
  const done = V.done[i];
  const mark = async e => {
    const t = full(), b = e.currentTarget;
    if (!t) return;
    b.disabled = true;
    const r = await call('posted', {op: done ? 'undo' : 'add', text: t, format: R.fmt || 'auto', mine: !!mine.value.trim()});
    if (r.s === 200) { if (done) delete V.done[i]; else V.done[i] = r.d.id; ST.posts = r.d.posts; toast(done ? '기록을 지웠어요' : '올린 글로 기록했어요'); keep(); draw(true); }
    else { b.disabled = false; toast(r.d.text || '기록하지 못했어요'); }
  };
  up();
  return el('section', {class: 'itm', 'data-d': i},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, d.format || `초안 ${i + 1}`), done ? el('span', {class: 'cp ok'}, '올림 ✓') : null, n),
    d.angle ? el('p', {class: 'an'}, `관점 · ${d.angle}`) : null,
    d.long ? seg([['s', '짧게'], ['l', `길게 ${d.long.length.toLocaleString('ko-KR')}자`]], lg ? 'l' : 's', k => { V.lg[i] = k === 'l'; keep(); draw(true); }) : null, box, body, pv,
    (d.hints || []).length ? el('p', {class: 'sf'}, '내 한마디 방향 · ' + d.hints.join(' / ')) : null, mine, ckl, why,
    el('div', {class: 'rw'}, post, btn('pb soft', '글 복사', () => copy(full(), '글'))),
    el('div', {class: 'rw'}, R.first_reply ? btn('pb soft', '첫 답글 복사', () => copy(R.first_reply, '첫 답글(출처)')) : null,
      q ? el('a', Object.assign({class: 'pb soft', href: q}, NW), '원글 열기') : null, btn('pb soft', done ? '올렸어요 취소' : '올렸어요', mark)),
    sch);
}
function writeTab() {
  const p = ST.posts, L = ST.limits, g = progLine(), out = [];
  out.push(el('section', {class: 'box'},
    el('div', {class: 'kpi'}, el('div', {}, el('span', {}, '오늘 올린 글'), el('b', {}, `${p.today}/${p.target.join('~')}`)),
      el('div', {}, el('span', {}, '최근 7일'), el('b', {}, String(p.d7))), el('div', {}, el('span', {}, '다음 추천'), el('b', {}, p.next || '내일'))),
    el('p', {class: 'note'}, `추천 시간 ${p.times.join(' · ')} · 하루 ${p.target.join('~')}개`),
    g ? el('p', {class: 'note'}, g.join(' · ')) : el('p', {class: 'note'}, "수익 조건 숫자는 '도우미'의 X 도우미로 한 번에 기록돼요")));
  out.push(el('p', {class: 'grp'}, '재료'), seg(KINDS, V.kind, k => { V.kind = k; keep(); draw(true); }));
  if (V.kind === 'hot') {
    out.push(ST.hot.length ? ST.hot.map(h => btn('xh', [el('b', {}, h.keyword || '화제'), el('span', {}, h.title + (h.confirmed ? '' : ' · 확인 안 된 이야기'))],
      () => { V.hot = h.id; keep(); draw(true); }, {'aria-pressed': V.hot === h.id ? 'true' : 'false'}))
      : el('p', {class: 'note'}, "지금 화제 글감이 없어요 — '기사·글'이나 '주제 한 줄'로 써 보세요"));
  } else if (V.kind === 'paste') {
    out.push(field('input', {type: 'url', inputmode: 'url', placeholder: '기사·X 글 주소 (첫 답글·인용에 써요)', 'aria-label': '주소'}, () => V.url, v => { V.url = v; }),
      field('textarea', {rows: 5, maxlength: L.text, placeholder: '기사·글 내용 붙여넣기 (비우면 서버가 주소를 읽어 봐요)', 'aria-label': '원문'}, () => V.text, v => { V.text = v; }));
  } else {
    out.push(field('input', {type: 'text', maxlength: L.topic, placeholder: '주제 한 줄 (숫자·사실은 내가 채워요)', 'aria-label': '주제'}, () => V.topic, v => { V.topic = v; }));
  }
  out.push(el('p', {class: 'grp'}, '형식'), chips(ST.formats, V.fmt, k => { V.fmt = k; }),
    el('p', {class: 'grp'}, '길이'), chips([['l', '짧게 + 길게'], ['s', '짧게만']], V.long !== false ? 'l' : 's', k => { V.long = k === 'l'; }),
    btn('pb', busy.post ? '쓰는 중… 30초~1분 걸려요' : V.res ? '초안 다시 만들기' : '초안 3개 만들기', makePost, {disabled: busy.post || null}),
    el('p', {class: 'note'}, `${sub()} · 오늘 ${ST.left.draft}번 남음`));
  const R = V.res;
  if (R && Array.isArray(R.drafts)) {
    const s = R.source || {};
    out.push(el('p', {class: 'grp'}, `초안 ${R.drafts.length}개`),
      el('p', {class: 'note', style: 'margin:0 0 10px'}, [s.title ? `재료 · ${s.title}` : '', s.fetched ? '서버가 주소에서 읽음' : '', R.confirmed === false ? '확인 안 된 이야기 — 공식 발표 뒤에 올리는 게 안전해요' : ''].filter(Boolean).join(' · ')),
      R.drafts.map(draftCard));
  }
  return out;
}

// 답글 초안 하나: 고치는 칸 + 글자 수 + X에서 답글 달기 (쉬는 시간 지킴)
function replyCard(d, i) {
  const R = V.r, id = R.res.sid, n = el('span', {class: 'n'}), a = el('a', Object.assign({class: 'pb'}, NW), 'X에서 답글 달기');
  const up = () => {
    const t = ta.value.trim(), [lo, hi] = ST.limits.reply;
    n.textContent = `${t.length}자`; n.className = 'n' + (t.length < lo || t.length > hi ? ' x' : '');
    if (t) a.setAttribute('href', ST.links.reply + id + '&text=' + encodeURIComponent(t)); else a.removeAttribute('href');
  };
  const ta = field('textarea', {class: 'fl short', rows: 2, 'aria-label': `답글 ${i + 1}`}, () => (R.ed[i] != null ? R.ed[i] : d.text), v => { R.ed[i] = v; }, up);
  a.addEventListener('click', e => {
    const w = waitLeft(), no = !a.getAttribute('href') ? '답글이 비었어요' : w > 0 ? `${wtxt(w)} 뒤에 달 수 있어요 — 답글 사이 1~2분 쉬기`
      : ST.replies.sids.includes(id) ? '이 글에는 오늘 이미 답글을 달았어요' : ST.replies.wait_s ? ST.replies.text : '';
    if (no) { e.preventDefault(); return toast(no); }
    okAt = Date.now() + ST.replies.gap[0] * 1000;  // 서버 답이 오기 전에도 바로 쉼
    ST.replies.sids.push(id);
    call('reply_go', {op: 'go', sid: id}).then(r => { setPace(r.d.replies); if (r.s !== 200 && r.d.text) toast(r.d.text); draw(); });
    setTimeout(draw, 300);  // 바로 다시 그리면 누른 링크가 화면에서 빠져 X 가 안 열림
  });
  up();
  return el('section', {class: 'itm', 'data-r': i}, (d.warn || []).map(x => el('p', {class: 'w'}, x)), ta,
    el('div', {class: 'rw'}, a, btn('pb soft', '복사', () => copy(ta.value.trim(), '답글')), n));
}
function paceBox() {
  const r = ST.replies, w = waitLeft();
  return el('section', {class: 'box', id: 'xsp'},
    el('div', {class: 'kpi'}, el('div', {}, el('span', {}, '오늘 답글'), el('b', {}, `${r.today}/${r.day_cap}`)),
      el('div', {}, el('span', {}, '최근 1시간'), el('b', {}, `${r.hour}/${r.hour_cap}`)), el('div', {}, el('span', {}, '다음 답글'), el('b', {}, w ? wtxt(w) : '지금'))),
    el('p', {class: 'note'}, `답글 사이 ${r.gap[0] / 60}~${r.gap[1] / 60}분 쉬기 · 하루 목표 ${ST.reply_goal}개 · 초안은 참고만, 내 말로 고쳐서`),
    r.undo ? btn('xl', '방금 건 안 달았어요 (기록 지우기)', async () => { const x = await call('reply_go', {op: 'undo'}); setPace(x.d.replies); draw(true); }) : null);
}
function replyTab() {
  const R = V.r, L = ST.limits, m = R.meta, out = [paceBox()];
  out.push(el('p', {class: 'grp'}, '답글 달 글'),
    field('input', {type: 'url', inputmode: 'url', placeholder: 'X 글 주소 붙여넣기 (x.com/아이디/status/숫자)', 'aria-label': 'X 글 주소'}, () => R.url, v => { R.url = v; }),
    field('textarea', {class: 'fl short', rows: 3, maxlength: L.post_text, placeholder: '글 내용 (비우면 서버가 읽어 봐요 — 못 읽으면 붙여넣기)', 'aria-label': '원글'}, () => R.text, v => { R.text = v; }),
    m ? el('p', {class: 'note', style: 'margin:0 0 10px'}, [`@${m.h}`, m.v ? '인증 계정' : '', m.c ? `답글·재게시·마음·조회 ${m.c.split(',').map(x => x || '-').join(' · ')}` : ''].filter(Boolean).join(' · ')) : null,
    el('p', {class: 'grp'}, '말투'), chips(ST.tones, R.tone, k => { R.tone = k; }),
    btn('pb', busy.reply ? '쓰는 중… 20~40초 걸려요' : R.res ? '답글 초안 다시 만들기' : '답글 초안 3개 만들기', makeReply, {disabled: busy.reply || null}),
    el('p', {class: 'note'}, `${sub()} · 오늘 ${ST.left.reply}번 남음`));
  if (R.res && Array.isArray(R.res.replies)) {
    const p = R.res.post || {};
    out.push(el('p', {class: 'grp'}, `답글 초안 ${R.res.replies.length}개`),
      el('div', {class: 'po'}, el('b', {}, (p.author ? `@${p.author} · ` : '') + (p.fetched ? '서버가 읽은 글' : '붙여넣은 글')), p.text || ''),
      R.res.replies.map(replyCard),
      R.url && sid(R.url) ? btn('pb soft', '이 글로 인용 글 쓰기', () => { setTab('write'); V.kind = 'paste'; V.url = firstUrl(R.url); V.text = R.text || p.text || ''; V.fmt = 'quote'; V.res = null; keep(); draw(true); makePost(); }) : null);
  }
  return out;
}
function listTab() {
  const LK = ST.links, h = el('input', {class: 'fl', type: 'text', maxlength: 60, placeholder: 'X 아이디 또는 프로필 주소', 'aria-label': 'X 아이디'});
  const memo = el('input', {class: 'fl', type: 'text', maxlength: 40, placeholder: '메모 (분야 등 — 안 써도 됨)', 'aria-label': '메모'});
  const send = async b => { const r = await call('targets', b); if (r.s === 200) { ST.targets = r.d.targets; draw(true); } else toast(r.d.text || '저장하지 못했어요'); };
  return [el('p', {class: 'grp'}, `자주 보는 계정 ${ST.targets.length}`),
    ST.targets.length ? el('section', {class: 'box'}, ST.targets.map(t => el('div', {class: 'xt'}, el('span', {class: 'g'}, `@${t.h} `, t.memo ? el('i', {}, t.memo) : null),
      el('a', Object.assign({class: 'sm ac', href: LK.profile + t.h}, NW), '프로필'), btn('sm', '빼기', () => send({op: 'del', handle: t.h})))))
      : el('p', {class: 'note'}, '답글을 자주 달 큰 계정을 넣어 두면 프로필을 바로 열 수 있어요'),
    el('p', {class: 'note', style: 'margin:0 0 12px'}, "프로필에서 글을 고른 뒤 공유 → 'X 답글 초안'(도우미)으로 보내면 초안이 바로 나와요. 이 도구는 그 계정 글을 긁지 않아요."),
    h, memo, btn('pb soft', '계정 넣기', () => { if (h.value.trim()) send({op: 'add', handle: h.value.trim(), memo: memo.value}); })];
}
function helpTab() {
  const base = location.origin + '/dashboard/x#/x/', ru = base + 'reply?u=', wu = base + 'write?u=';
  const cp = (u, l) => el('div', {}, el('div', {class: 'key'}, u), btn('sm', `${l} 복사`, () => copy(u, l)));
  const hp = async () => { const r = await fetch('/dashboard/api/xhelper', {credentials: 'same-origin', cache: 'no-store'}).then(x => (x.status === 200 ? x.json() : null), () => null);
    if (r) HP = r; else toast('불러오지 못했어요 — 잠시 뒤 다시'); draw(true); };
  return [el('p', {class: 'grp'}, 'X 앱에서 공유로 바로 초안 (아이폰·아이패드)'),
    el('section', {class: 'box'}, el('ol', {}, ["'단축어' 앱 → 새 단축어 → 이름 'X 답글 초안'", "아래 ⓘ → '공유 시트에서 보기' 켜기 → 받는 종류는 'URL'만",
      "동작 'URL 인코딩' 추가 (입력 = 단축어 입력)", "동작 'URL' 추가 → 아래 답글 주소를 붙여넣고, 맨 끝에 변수 'URL 인코딩된 텍스트' 넣기", "동작 'URL 열기' 추가 → 완료",
      "쓰는 법: X 앱에서 글의 공유 단추 → 'X 답글 초안' → 대시보드에 답글 초안 3개"].map(x => el('li', {text: x}))),
      cp(ru, '답글 주소'), el('p', {class: 'note'}, "글쓰기용은 같은 단축어를 하나 더 ('X 글 초안') — 주소만 아래 것으로. 기사 앱·사파리에서 공유해도 돼요."), cp(wu, '글쓰기 주소'),
      el('p', {class: 'note'}, '단축어에는 비밀번호·키가 없어요 — 사파리가 대시보드에 로그인돼 있으면 열려요.')),
    el('p', {class: 'grp'}, '갤럭시 (안드로이드)'),
    el('section', {class: 'box'}, el('p', {class: 'note', style: 'margin:0'}, "크롬에서 Sns-board 를 '앱 설치(홈 화면에 추가)' 해 두면 X 앱 공유 목록에 'Sns-board' 가 나와요 — 누르면 바로 초안. " +
      "(설치한 지 하루쯤 지나야 목록에 보여요. 안 보이면 'HTTP Shortcuts' 같은 앱에서 위 주소 뒤에 공유한 글을 붙여 브라우저로 여는 바로가기를 만들어요.)")),
    el('p', {class: 'grp'}, 'X 도우미 책갈피 (사파리 x.com)'),
    el('section', {class: 'box'}, el('p', {class: 'note', style: 'margin:0 0 8px'}, '글 화면 → 답글 초안 · 수익 조건 화면 → 인증 팔로워·노출 숫자 저절로 기록 · 프로필 → 자주 보는 계정에 넣기. X 에서는 아무것도 누르지 않아요.'),
      HP ? [el('ol', {}, HP.steps.map(x => el('li', {text: x}))), btn('sm ac', '코드 복사', () => copy(HP.code, '책갈피 코드'))] : btn('pb soft', '설치 방법 보기', hp)),
    el('p', {class: 'grp'}, '하루치 한 번에 — X 예약 게시'),
    el('section', {class: 'box'}, el('p', {class: 'note', style: 'margin:0 0 8px'}, "x.com 웹 글쓰기 창의 달력 단추로 예약할 수 있어요 (X 앱에는 없음). 초안 카드의 'x.com 웹에서 예약하기'를 사파리에서 열거나, " +
      "'글 복사' 후 아래 웹 글쓰기에 붙여넣고 12:00 · 18:30 · 21:30 으로 예약해요. 예약해도 '내 한마디'는 꼭 직접 써요."),
      el('a', Object.assign({class: 'sm', href: ST.links.compose}, NW), 'x.com 웹 글쓰기 열기')),
    el('p', {class: 'note'}, sub())];
}
function drawScr() {
  if (!scr) return;
  const top = el('div', {class: 'top'}, el('a', {class: 'lnk', href: '#/sns/x'}, '‹ X(트위터)'), el('span', {class: 'sub'}, ''), btn('lnk', '다시 보기', load));
  if (!ST) return scr.replaceChildren(top, el('p', {class: 'boot'}, '불러오는 중…'));
  const tabs = seg(TABS, V.tab, k => { setTab(k); keep(); draw(true); });
  scr.replaceChildren(...[top, tabs, V.tab === 'reply' ? replyTab() : V.tab === 'list' ? listTab() : V.tab === 'help' ? helpTab() : writeTab()].flat(9).filter(Boolean));
  clearInterval(tick);
  tick = V.tab === 'reply' && waitLeft() ? setInterval(() => { const b = D.getElementById('xsp'); if (!scr.parentNode || !b) return clearInterval(tick); if (!inField()) b.replaceWith(paceBox()); if (!waitLeft()) clearInterval(tick); }, 1000) : null;
}
const inField = () => { const a = D.activeElement; return !!(scr && a && scr.contains(a) && /^(INPUT|TEXTAREA)$/.test(a.tagName)); };
function draw(force) {
  drawCard();
  if (!force && inField()) { dirty = true; return; }  // 글을 고치는 중이면 다음에
  dirty = false;
  drawScr();
}

// -- 붙이기 (app.js 가 #main 을 다시 그리면 다시 붙음 — ssalmuk.js 틀)
const onScr = () => /^#\/?sns\/xs(\/\w*)?\/?$/.test(location.hash);
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true';
}
function title() {
  const t = D.getElementById('ttl');
  if (onScr() && t && t.textContent !== 'X 글쓰기·답글') t.textContent = 'X 글쓰기·답글';
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xs-css')) D.head.append(el('style', {id: 'xs-css', text: CSS}));
  if (onScr()) {
    const t = (/xs\/(\w+)/.exec(location.hash) || [])[1];
    if (t && TABS.some(x => x[0] === t) && V.tab !== t) { V.tab = t; scr && drawScr(); }
    if (!scr) { scr = el('section', {id: 'xss', class: 'xs', 'aria-label': 'X 글쓰기·답글'}); drawScr(); }
    if (m.firstChild !== scr) m.prepend(scr);
    if (!m.hasAttribute('data-xs')) m.setAttribute('data-xs', '');
    title();
  } else {
    if (m.hasAttribute('data-xs')) m.removeAttribute('data-xs');
    if (scr && scr.parentNode) scr.remove();
  }
  if (!onScr() && onX()) {
    if (!card) { card = el('article', {id: 'xsc', class: 'itm xsc', 'aria-label': '오늘 할 X 일'}); drawCard(); }
    const s = D.getElementById('snt');
    if (s.nextSibling !== card) s.after(card);
  } else if (card && card.parentNode) card.remove();
  if ((onScr() || onX()) && (!ST || Date.now() - at > 60000)) load();
}
// 바로 가기 주소: #/x/reply?u=… · #/x/write?u=…&q=… · #/x/stats?k=… · #/x/target?k=… → 값을 받아 두고 #/sns/xs/… 로
function deep() {
  const m = /^#\/?x\/(reply|write|stats|target)(?:\?(.*))?$/.exec(location.hash);
  if (!m) return;
  const q = new URLSearchParams(m[2] || ''), g = (k, n) => (q.get(k) || '').slice(0, n);
  DL = {a: m[1], k: g('k', 80), u: g('u', 600), t: g('t', 1500) || g('q', 6000), h: g('h', 15), v: g('v', 1), c: g('c', 60), f: g('f', 12), hot: g('hot', 40)};
  for (const k of ['vf', 'im', 'fo', 'ai', 'ae']) if (/^\d{1,13}$/.test(q.get(k) || '')) DL[k] = Number(q.get(k));
  try { W.opener = null; } catch (e) { /* 연 창을 건드리지 않음 */ }
  location.replace('#/sns/xs/' + (m[1] === 'reply' ? 'reply' : m[1] === 'target' ? 'list' : 'write'));
}
function start() {
  deep();
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  const t = D.getElementById('ttl');
  if (t) new MutationObserver(title).observe(t, {childList: true, characterData: true, subtree: true});
  W.addEventListener('hashchange', () => { deep(); mount(); if (onScr()) { W.scrollTo(0, 0); if (DL && ST) runDeep(); } });
  D.addEventListener('focusout', () => setTimeout(() => { if (dirty) draw(); }, 80));
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (onScr() || onX())) load(); });
  mount();
}
W.SBXStudio = {load, mount, xlen, sid, missing, state: () => ST, view: V};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
