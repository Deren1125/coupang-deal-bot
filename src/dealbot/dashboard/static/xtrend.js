/* 지금 뜨는 주제 · 내 주제로 글 쓰기 (주인 11/16 'X 에서 뜨는 주제 랭킹을 보고 글 주제를 고르게 · 내가 주제·내용을 넣으면 그 글을 써 주게'). 셸이 defer 로 받음 — xtrend.py.
   SNS 탭 X 칸에 '지금 뜨는 주제' 카드 (오늘 X 글 카드 아래 · 위 5줄) · 화면은 #/sns/xr (순위 전체 + 정렬 + 내 주제로 글 쓰기 · #/sns/xr/w 는 글 쓰기 칸으로).
   순위는 서버가 공개 출처에서 읽어 둔 값 (15분 지났으면 이 화면이 '새로 보기'를 한 번 부름 — 서버가 10분에 한 번만 받음). X 에 로그인·자동 게시·자동 클릭 없음:
   대표 글·'X에서 보기'는 서버가 준 링크를 내가 누를 때만 열린다. [이걸로 글 쓰기] = 주제 칸에 넣기만 (AI 는 [초안 만들기]를 누를 때만 · 한 번 = 초안 묶음 하나).
   초안은 [복사] 해서 X 에 내가 올리고 [✓ 올렸어요] → 오늘 X 체크리스트에 '내 주제' 줄로 (10분 안 취소).
   사진으로 글 쓰기 칸은 xphoto.js 가 그려 넣는다 (W.SBXPhoto.form — 초안은 같은 '내 초안' 목록에 사진·가린 곳 수와 같이).
   이 파일에 바깥 주소·innerHTML 없음. 쓰던 주제·메모·고친 초안은 이 기기에만 (localStorage xr1). */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xtrend/';
const CSS = '.xr .r{display:flex;align-items:center;gap:8px;min-height:40px;font-size:14px}.xr .r>b{flex:none;min-width:20px;text-align:right;font-variant-numeric:tabular-nums}' +
  '.xr .r .k{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}.xr .r .m,.xr .mv{flex:none;font-size:12px;color:var(--sub)}' +
  '.xr .mv.up{color:var(--red)}.xr .mv.dn{color:var(--ac-ink)}.xr .sm{flex:none}.xr .kw{display:block;font-size:16px;margin:6px 0;overflow-wrap:anywhere}' +
  '.xr .p{display:block;font-size:13px;line-height:1.5;color:var(--ac-ink);text-decoration:none;padding:4px 0;overflow-wrap:anywhere}.xr .p i{font-style:normal;color:var(--sub)}' +
  '.xr .hd{opacity:.6;font-size:13px;margin:0 0 4px}.xr .opt{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 8px}.xr .opt .seg{flex:1 1 200px}' +
  '.xr textarea.dr{min-height:150px}.xr .cap{display:block;width:100%;height:auto;border-radius:8px;margin:6px 0;border:1px solid var(--line,#ddd)}.xr .dn1{opacity:.55}.xr label.ck{display:inline-flex;align-items:center;gap:6px;min-height:44px;font-size:14px}' +
  '.xr label.ck input{width:22px;height:22px;margin:0}main[data-xr]>:not(#xrs){display:none!important}';
const NW = {target: '_blank', rel: 'noopener'}, CARD_ROWS = 5;
const SORTS = [['rank', '인기순'], ['views', '조회순'], ['replies', '댓글 많은 순'], ['move', '급상승']];
const ONE = [[0, 4351], [8192, 8205], [8208, 8223], [8242, 8247]];
let ST = null, at = 0, loading = false, card = null, scr = null, tt = null, poll = null, polls = 0, asked = 0, sort = 'rank', showHidden = false, showAbroad = false, jump = false, err = null, made = '', pend = null;
const busy = {};
let F = {topic: '', memo: '', tone: 'eum', length: 'normal', tags: false, trend: '', ed: {}};
try { const o = JSON.parse(W.localStorage.getItem('xr1') || 'null'); if (o && typeof o === 'object') F = Object.assign(F, o, {ed: o.ed && typeof o.ed === 'object' ? o.ed : {}}); } catch (e) { /* 저장본 없음 */ }
function keep() { try { W.localStorage.setItem('xr1', JSON.stringify(F)); } catch (e) { /* 저장 못 해도 그대로 씀 */ } }

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v; else if (n === 'value') e.value = v;
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
function copy(text) {
  text = String(text || '');
  const ok = () => toast('복사했어요 — X 에 붙여넣어 올린 뒤 ✓ 를 눌러 주세요');
  const bad = () => (old(text) ? ok() : toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
  const c = W.navigator && W.navigator.clipboard;
  if (c && c.writeText) c.writeText(text).then(ok, bad); else bad();
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
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요'}}; }
}
const take = d => { if (d && d.v && Array.isArray(d.rows)) { ST = d; at = Date.now(); for (const s of d.mine || []) for (const x of s.drafts) x.until = at + (x.undo_s || 0) * 1000; settle(); } };
function settle() {  // 연결이 끊긴 뒤(폰 화면 꺼짐·느린 서버) 서버가 계속 쓴 초안을 기다림 (주인 11/18 '눌렀는데 아무것도 안 나옴')
  if (!pend || !ST) return;
  const s = (ST.mine || []).find(x => !pend.ids.has(x.id));
  if (s) { pend = null; err = null; made = s.id; toast('초안을 만들었어요 — 바로 아래 「내 초안」에 있어요'); return; }
  if (ST.run) return;  // 아직 쓰는 중 — 4초마다 다시 봄
  if (ST.fail && !ST.fail.photo && JSON.stringify(ST.fail) !== pend.fail) { pend = null; err = null; return; }  // 서버가 남긴 이번 실패 이유를 보여 줌
  lost();
}
function lost() {  // 서버도 안 쓰고 있고 초안·실패 기록도 없음 = 부탁이 서버에 안 닿음
  pend = null;
  err = {text: '초안이 오지 않았어요 — [다시 해 보기]를 눌러 주세요', topic: F.topic.trim(), code: 'lost'};
}
function watch() {  // 서버가 뒤에서 새로 보는 동안만 4초마다 (30번까지)
  clearTimeout(poll);
  if (pend && (Date.now() > pend.until || polls >= 45)) { lost(); drawScr(true); }
  if (ST && (ST.busy || (ST.run && !busy.mk) || pend) && polls < 45) poll = setTimeout(() => { polls++; load(); }, 4000);
}
async function load() {
  if (loading) return;
  loading = true;
  const r = await call('state');
  loading = false;
  at = Date.now();
  if (r.s === 200) take(r.d); else if (!ST && r.d.text) toast(r.d.text);
  // 15분 넘은 순위면 한 번 새로 보기 (이 화면에서 10분에 한 번만 부름 — 서버도 같은 사이로 막음)
  if (ST && ST.on && ST.stale && !ST.busy && !ST.wait_s && Date.now() - asked > 600000) { asked = Date.now(); refresh(true); }
  draw(); watch();
  if (made && !busy.mk) jumpMade();
}
function jumpMade() { const m = D.getElementById('xrm-' + made); made = ''; if (m && m.scrollIntoView) m.scrollIntoView({block: 'start'}); }
async function refresh(quiet) {
  if (busy.rf) return;
  busy.rf = true; draw();
  const r = await call('refresh', {});
  busy.rf = false; polls = 0;
  take(r.d);
  if (quiet !== true) toast(r.s === 200 ? '새로 보는 중이에요 — 1분쯤 걸려요' : r.d.text || '새로 보지 못했어요');
  draw(); watch();
}

// -- 숫자·순위
const num = n => (n >= 1e8 ? (n / 1e8).toFixed(1).replace(/\.0$/, '') + '억' : n >= 1e4 ? (n / 1e4).toFixed(n >= 1e5 ? 0 : 1).replace(/\.0$/, '') + '만' : String(n || 0));
function xlen(t) {  // X 글자 수 (서버 x_len 과 같은 셈 — 한글 2 · 영문 1)
  let n = 0;
  for (const c of String(t || '')) { const o = c.codePointAt(0); n += ONE.some(r => o >= r[0] && o <= r[1]) ? 1 : 2; }
  return n;
}
function mv(r) {
  if (r.move === 'new') return el('span', {class: 'mv up'}, '새로');
  if (typeof r.move !== 'number' || !r.move) return el('span', {class: 'mv'}, r.move === 0 ? '-' : '');
  return el('span', {class: 'mv ' + (r.move > 0 ? 'up' : 'dn')}, (r.move > 0 ? '▲' : '▼') + Math.abs(r.move));
}
const meta = r => [r.n ? `조회 ${num(r.views)} · 답글 ${num(r.replies)}` : '', r.traffic ? `검색 ${num(r.traffic)}+` : ''].filter(Boolean).join(' · ') || (r.src.includes('hot') ? '뉴스 화제' : 'X 글 적음');
function sorted() {
  const rows = (ST.rows || []).slice(), mvn = r => (r.move === 'new' ? 99 : typeof r.move === 'number' ? r.move : -99);
  if (sort === 'views') rows.sort((a, b) => b.views - a.views || a.rank - b.rank);
  else if (sort === 'replies') rows.sort((a, b) => b.replies - a.replies || a.rank - b.rank);
  else if (sort === 'move') rows.sort((a, b) => mvn(b) - mvn(a) || a.rank - b.rank);
  else rows.sort((a, b) => a.rank - b.rank);
  return rows;
}
function pick(r) {  // [이걸로 글 쓰기] — 주제 칸에 넣기만 (AI 는 부르지 않음)
  F.topic = r.kw; F.trend = r.id; keep();
  jump = true;
  if (onScr()) { drawScr(true); toForm(); } else location.hash = '#/sns/xr/w';
}
function toForm() {
  jump = false;
  const f = scr && D.getElementById('xrf');
  if (f && f.scrollIntoView) f.scrollIntoView({block: 'start'});
}

// -- 초안
async function make() {
  if (busy.mk) return;
  const ids = new Set(((ST && ST.mine) || []).map(s => s.id)), prevFail = JSON.stringify((ST && ST.fail) || null);
  pend = null; err = null;
  const lim = (ST && ST.lim) || {topic: [2, 80], memo: 600}, topic = F.topic.trim();
  if (topic.length < lim.topic[0] || topic.length > lim.topic[1]) return toast(`주제는 ${lim.topic[0]}~${lim.topic[1]}자로 적어 주세요`);
  if (F.memo.length > lim.memo) return toast(`하고 싶은 말은 ${lim.memo}자까지예요`);
  busy.mk = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); drawScr(true);
  const r = await call('draft', {rid: busy.mk, topic, memo: F.memo, tone: F.tone, length: F.length, tags: !!F.tags, n: 3,
    trend: F.trend && ST && ST.rows.some(x => x.id === F.trend && x.kw === topic) ? F.trend : ''});
  busy.mk = '';
  take(r.d);
  // 주인 11/17 '초안이 안 뜸 — 어디서 보는지 모름': 실패는 칸 안에 남기고(다시 단추), 만든 초안은 아래 '내 초안'으로 내려가 보여 줌
  // 서버가 이유를 준 실패(code 있음)는 그대로 · 연결이 끊긴 것(0·프록시 오류)은 서버가 아직 쓰는 중일 수 있어 상태를 다시 보며 기다림
  const cut = r.s !== 200 && !r.d.code && r.s !== 401 && r.s !== 403;
  err = r.s === 200 ? null : cut ? {text: '연결이 잠깐 끊겼어요 — 서버가 계속 쓰는 중인지 보고 있어요', topic, code: 'net'}
    : {text: r.d.text || '초안을 쓰지 못했어요 — 잠시 뒤 다시', topic, code: r.d.code || ''};
  made = r.s === 200 ? r.d.made || '' : '';
  if (!cut) toast(r.s === 200 ? '초안을 만들었어요 — 바로 아래 「내 초안」에 있어요' : err.text);
  drawScr(true);
  if (made) jumpMade();
  if (cut) { pend = {ids, fail: prevFail, until: Date.now() + 240000}; polls = 0; ST && (ST.run = ST.run || {topic, at: ''}); load(); }
}
const txt = (s, i, d) => { const v = F.ed[s.id + ':' + i]; return String(v != null ? v : d.text); };
async function posted(s, i, d, ev) {
  const b = ev.currentTarget, undo = !!d.done;
  if (busy.pk) { b.checked = undo; return; }
  if (undo && Date.now() > (d.until || 0)) { b.checked = true; return toast('올린 지 10분이 지나 취소할 수 없어요'); }
  busy.pk = true; b.disabled = true;
  const r = await call('posted', {set: s.id, i, text: txt(s, i, d), op: undo ? 'undo' : 'add'});
  busy.pk = false;
  if (r.s === 200) { take(r.d); toast(undo ? '기록을 지웠어요' : '올렸어요 — 오늘 X 체크리스트에 넣었어요'); if (W.SBXToday) W.SBXToday.load(); }
  else { b.disabled = false; b.checked = undo; toast(r.d.text || '기록하지 못했어요 — 다시 눌러 주세요'); }
  drawScr(true);
}
function draftCard(s, i, d) {
  const lim = ST.lim.x, cnt = el('span', {class: 'n'}), show = t => { const n = xlen(t); cnt.textContent = s.long ? `${t.length}자` : `${n}/${lim}`; cnt.className = 'n' + (!s.long && n > lim ? ' x' : ''); };
  const ta = el('textarea', {class: 'fl dr', 'aria-label': `초안 ${i + 1}`, value: txt(s, i, d), oninput: e => { F.ed[s.id + ':' + i] = e.target.value; keep(); show(e.target.value); }});
  show(ta.value);
  return el('section', {class: 'itm' + (d.done ? ' dn1' : '')},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, `초안 ${i + 1}`), d.done ? el('span', {class: 'cp ok'}, '올림 ✓') : null, cnt),
    d.angle ? el('p', {class: 'an'}, `관점 · ${d.angle}`) : null, (d.warn || []).map(x => el('p', {class: 'w'}, x)), ta,
    el('div', {class: 'rw'}, btn('pb soft', '복사', () => copy(ta.value)),
      el('label', {class: 'ck'}, el('input', {type: 'checkbox', checked: !!d.done, 'aria-label': `초안 ${i + 1} 올렸어요`, onchange: e => posted(s, i, d, e)}), '✓ 올렸어요')));
}
const seg = (name, opts) => el('div', {class: 'seg', role: 'group'}, opts.map(([k, lab]) => btn('', lab, () => { F[name] = k; keep(); drawScr(true); }, {'aria-pressed': String(F[name] === k)})));
function form() {
  const lim = ST.lim;
  return el('section', {class: 'itm', id: 'xrf'},
    el('input', {class: 'fl', type: 'text', maxlength: lim.topic[1], placeholder: '주제 (예: 수능 연기 논란)', 'aria-label': '주제', value: F.topic,
      oninput: e => { F.topic = e.target.value; if (F.trend) F.trend = ''; keep(); }}),
    el('textarea', {class: 'fl short', maxlength: lim.memo, placeholder: '내가 하고 싶은 말·내용 (사실·숫자는 여기 적은 것만 써요)', 'aria-label': '하고 싶은 말', value: F.memo,
      oninput: e => { F.memo = e.target.value; keep(); }}),
    el('div', {class: 'opt'}, seg('tone', ST.tones)), el('div', {class: 'opt'}, seg('length', ST.lengths),
      el('label', {class: 'ck'}, el('input', {type: 'checkbox', checked: !!F.tags, onchange: e => { F.tags = e.target.checked; keep(); }}), '해시태그')),
    btn('pb', busy.mk || ST.run || pend ? '쓰는 중… 30초~1분 걸려요' : '초안 만들기', make, {disabled: !!busy.mk || !!ST.run || !!pend || ST.left <= 0 || null}),
    failBox(),
    busy.mk || ST.run || pend ? el('p', {class: 'note', role: 'status'}, `⏳ 「${(ST.run && ST.run.topic) || F.topic.trim()}」 초안을 쓰는 중이에요${ST.run && ST.run.at ? ` (${ST.run.at} 시작)` : ''}` +
      ' — 화면을 닫아도 서버는 계속 써요 · 다 되면 바로 아래 「내 초안」에 떠요') : null,
    ST.left <= 0 && !ST.run ? el('p', {class: 'w'}, '오늘 초안 횟수를 다 썼어요 — 내일 다시 쓸 수 있어요') : null,
    el('p', {class: 'note'}, `오늘 초안 ${ST.left}번 남음 · 초안 ${ST.model} · 내가 적은 주제·메모는 바깥 검색에 쓰지 않아요`));
}
function failBox() {  // 마지막 실패 (이 기기 것 또는 서버가 기억한 것 — 새로 고쳐도 남음, 그 뒤 초안이 생기면 사라짐)
  const f = err || (ST.fail && !ST.fail.photo ? ST.fail : null);  // 사진으로 쓴 것의 실패는 그 칸(xphoto.js)에
  if (!f || busy.mk || (f.code === 'net' && (pend || ST.run))) return null;  // 끊긴 뒤 서버가 아직 쓰는 중이면 '쓰는 중'만
  const wait = f.wait_s ? ` (${Math.ceil(f.wait_s / 60)}분 남음)` : '';
  return el('section', {class: 'box', role: 'alert', id: 'xrfail'}, el('p', {class: 'w'}, `✗ ${f.at ? f.at + ' · ' : ''}「${f.topic || F.topic}」 초안을 못 썼어요 — ${f.text}${wait}`),
    el('div', {class: 'rw'}, btn('pb soft', '다시 해 보기', () => { if (f.topic && !F.topic.trim()) F.topic = f.topic; err = null; make(); }, {disabled: f.code === 'ai_limit' && f.wait_s > 0 || null}),
      btn('lnk', '닫기', () => { err = null; if (ST.fail) ST.fail = null; drawScr(true); })));
}

// -- 그리기
const ago = () => (!ST.at ? '아직 안 봤어요' : `${ST.at} 기준`) + (ST.busy || busy.rf ? ' · 새로 보는 중…' : '');
function drawCard() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, '지금 뜨는 주제 불러오는 중…'));
  const rows = (ST.rows || []).slice().sort((a, b) => a.rank - b.rank).slice(0, CARD_ROWS);
  card.replaceChildren(...[
    el('div', {class: 'ih'}, el('b', {class: 'ti'}, '지금 뜨는 주제'), el('a', {class: 'lnk', href: '#/sns/xr'}, '열기 ›')),
    rows.map(r => el('div', {class: 'r'}, el('b', {}, r.rank), mv(r), el('span', {class: 'k'}, r.kw), el('span', {class: 'm'}, r.n ? `조회 ${num(r.views)}` : r.traffic ? `검색 ${num(r.traffic)}+` : ''),
      btn('sm', '글 쓰기', () => pick(r), {'aria-label': `${r.kw} 이걸로 글 쓰기`}))),
    !ST.on ? el('p', {class: 'note'}, '지금 뜨는 주제가 꺼져 있어요') : rows.length ? null : el('p', {class: 'note'}, ST.busy || busy.rf ? '처음 보는 중이에요 — 1분쯤 걸려요' : '아직 볼 주제가 없어요'),
    el('div', {class: 'rw'}, btn('pb soft', '내 주제로 글 쓰기', () => { jump = true; location.hash = '#/sns/xr/w'; }),
      (ST.mine || []).length ? el('a', {class: 'lnk', href: '#/sns/xr/w'}, `내 초안 ${ST.mine.length}묶음 ›`) : null),
    ST.fail ? el('p', {class: 'w'}, `✗ 초안 못 씀 — ${ST.fail.text}`) : ST.run ? el('p', {class: 'note'}, `「${ST.run.topic}」 초안 쓰는 중…`) : null,
    el('p', {class: 'note'}, `${ago()} · 주제를 골라 초안을 받고, 올리기는 X 에서 내가`)].flat(9).filter(Boolean));
}
function rowCard(r) {
  return el('section', {class: 'itm'},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, `${r.rank}위`), mv(r), r.since ? el('span', {class: 'cp'}, `${r.since}부터`) : null),
    el('b', {class: 'kw'}, r.kw), r.orig ? el('p', {class: 'hd'}, `해외 화제 「${r.orig}」 — 한국 기사 제목으로 보여요`) : null,
    el('p', {class: 'sf'}, [r.n ? `X 반응 큰 글 ${r.n}개 (24시간) · 조회 ${num(r.views)} · 답글 ${num(r.replies)} · ♥ ${num(r.likes)}` : r.seen ? 'X 반응 큰 글 없음' : 'X 글 수는 못 봤어요',
      r.traffic ? `구글 검색 ${num(r.traffic)}+` : ''].filter(Boolean).join(' · ')),
    (r.posts || []).map(p => el('a', Object.assign({class: 'p', href: p.url}, NW), el('i', {}, `@${p.author} · 조회 ${num(p.views)} · 답글 ${num(p.replies)} — `), p.text)),
    (r.news || []).map(n => el('a', Object.assign({class: 'p', href: n.link}, NW), el('i', {}, `${n.source || '기사'} — `), n.title)),
    el('div', {class: 'rw'}, btn('pb', '이걸로 글 쓰기', () => pick(r)), el('a', Object.assign({class: 'pb soft', href: r.x}, NW), 'X에서 보기')));
}
function drawScr(force) {
  if (!scr) return;
  const x = D.activeElement;
  if (force !== true && x && scr.contains(x) && /^(INPUT|TEXTAREA)$/.test(x.tagName)) return;  // 쓰는 중에는 다시 그리지 않음
  const top = el('div', {class: 'top'}, el('a', {class: 'lnk', href: '#/sns/x'}, '‹ X(트위터)'), el('span', {class: 'sub'}, ST ? ago() : ''),
    btn('lnk', '새로 보기', refresh, {disabled: !!(busy.rf || (ST && (ST.busy || !ST.on))) || null}));
  if (!ST) return scr.replaceChildren(top, el('p', {class: 'boot'}, '불러오는 중…'));
  const rows = sorted(), hid = ST.hidden || [], mine = ST.mine || [];
  scr.replaceChildren(...[top,
    ST.note ? el('p', {class: 'w'}, ST.note) : null,
    el('p', {class: 'grp'}, `지금 뜨는 주제 ${rows.length}`),
    rows.length ? el('div', {class: 'seg', role: 'group', style: 'margin:0 0 10px'}, SORTS.map(([k, lab]) => btn('', lab, () => { sort = k; drawScr(true); }, {'aria-pressed': String(sort === k)}))) : null,
    rows.length ? rows.map(rowCard) : el('p', {class: 'note'}, !ST.on ? '지금 뜨는 주제가 꺼져 있어요' : ST.busy || busy.rf ? '처음 보는 중이에요 — 1분쯤 걸려요' : "아직 볼 주제가 없어요 — '새로 보기'를 눌러 주세요"),
    hid.length ? [btn('xl', `숨긴 주제 ${hid.length}개 (정치·사고·혐오·성인) ${showHidden ? '접기' : '보기'}`, () => { showHidden = !showHidden; drawScr(true); }),
      showHidden ? el('section', {class: 'box'}, hid.map(h => el('p', {class: 'hd'}, `${h.kw} · ${h.why} — 숨김`))) : null] : null,
    el('p', {class: 'note', style: 'margin:0 0 10px'}, '조회·답글 수는 주제마다 좋아요 20 넘는 X 글 20개까지 더한 값이에요 (X 전체 글 수 아님) · 오른 정도는 약 1시간 전 순위와 견줘요'),
    abroad(),
    el('p', {class: 'grp'}, '내 주제로 글 쓰기'),
    el('p', {class: 'note'}, '① 주제 한 줄 ② 하고 싶은 말(방향·내용) 적고 [초안 만들기] → 30초~1분 뒤 바로 아래 「내 초안」에 초안 3개가 떠요'),
    form(),
    // 주인 11/18: 초안이 '사진으로 글 쓰기' 칸 밑에 있어 안 보였음 → 글 쓰기 칸 바로 아래로
    el('p', {class: 'grp', id: 'xrmine'}, `내 초안 · 오늘 ${mine.length}묶음`),
    mine.length ? null : el('p', {class: 'note'}, '아직 오늘 만든 초안이 없어요 — [초안 만들기]로 만든 초안은 여기 남아요 (새로 고쳐도 · 오늘 것)'),
    mine.map(s => [el('p', {class: 'sub', id: 'xrm-' + s.id}, `${s.at} · ${s.topic}`), pics(s), s.drafts.map((d, i) => draftCard(s, i, d))]),
    W.SBXPhoto ? W.SBXPhoto.form() : null,
  ].flat(9).filter(Boolean));
  if (jump) toForm();
}
function pics(s) {  // 사진으로 쓴 묶음 (xphoto.py) — 모자이크본만 서버에 있음 · [사진 저장] 해서 초안과 같이 올림
  const im = s.imgs || [];
  if (!im.length) return null;
  return el('section', {class: 'itm'}, im.map((src, i) => el('img', {class: 'cap', src, alt: `사진 ${i + 1} (가린 뒤)`, loading: 'lazy'})),
    el('div', {class: 'rw'}, im.map((src, i) => el('a', {class: 'pb soft', href: src, download: `x-${s.id}-${i + 1}.jpg`}, im.length > 1 ? `사진 ${i + 1} 저장` : '사진 저장'))),
    el('p', {class: 'an'}, `가린 곳 ${s.blur}곳${s.faces ? ' (얼굴 포함)' : ''} · 원본은 서버에서 지웠어요 — 올리기 전에 닉네임·아이디가 남았는지 한 번 봐 주세요`));
}
function abroad() {  // 외국어 주제 · 한국 기사 없음 (주인 11/17 — 순위에서 빼고 접어 둠, 기본 숨김)
  const ab = ST.abroad || [];
  if (!ab.length) return null;
  return [btn('xl', `해외 화제 ${ab.length}개 (한국 기사 없음) ${showAbroad ? '접기' : '보기'}`, () => { showAbroad = !showAbroad; drawScr(true); }),
    showAbroad ? el('section', {class: 'box'}, ab.map(a => el('a', Object.assign({class: 'p', href: a.x}, NW), el('i', {}, a.views ? `조회 ${num(a.views)} — ` : a.traffic ? `검색 ${num(a.traffic)}+ — ` : ''), a.kw))) : null];
}
function draw() { drawCard(); drawScr(); }

// -- 붙이기 (app.js 가 #main 을 다시 그리면 다시 붙음 — xtoday.js 와 같은 틀)
const onScr = () => /^#\/?sns\/xr(\/w)?\/?$/.test(location.hash);
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true' && !/^#\/?sns\/x[set]/.test(location.hash);
}
function title() {
  const t = D.getElementById('ttl');
  if (onScr() && t && t.textContent !== '지금 뜨는 주제') t.textContent = '지금 뜨는 주제';
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xr-css')) D.head.append(el('style', {id: 'xr-css', text: CSS}));
  if (onScr()) {
    if (/\/w\/?$/.test(location.hash)) jump = true;
    if (!scr) { scr = el('section', {id: 'xrs', class: 'xs xr', 'aria-label': '지금 뜨는 주제'}); drawScr(true); }
    if (m.firstChild !== scr) { m.prepend(scr); if (jump) toForm(); }
    if (!m.hasAttribute('data-xr')) m.setAttribute('data-xr', '');
    title();
  } else {
    if (m.hasAttribute('data-xr')) m.removeAttribute('data-xr');
    if (scr && scr.parentNode) scr.remove();
  }
  if (!onScr() && onX()) {
    if (!card) { card = el('article', {id: 'xrc', class: 'itm xsc xr', 'aria-label': '지금 뜨는 주제'}); drawCard(); }
    const a = D.getElementById('xec') || D.getElementById('xtc') || D.getElementById('xsc') || D.getElementById('snt');  // 반응 카드 → 오늘 X 글 카드 → 오늘 할 X 일 카드 아래
    if (a && a.nextSibling !== card) a.after(card);
  } else if (card && card.parentNode) card.remove();
  if ((onScr() || onX()) && (!ST || Date.now() - at > 120000)) load();
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  const t = D.getElementById('ttl');
  if (t) new MutationObserver(title).observe(t, {childList: true, characterData: true, subtree: true});
  W.addEventListener('hashchange', () => { mount(); if (onScr() && !jump) W.scrollTo(0, 0); });
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (onScr() || onX())) load(); });
  mount();
}
W.SBXTrend = {load, mount, pick, state: () => ST, form: () => F, take, redraw: () => drawScr(true), busy: () => !!busy.mk, seg};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
