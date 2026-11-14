/* 이벤트 참여 (쌀먹 — 돈좀요 이벤트 자동 응모, 주인 11/14 'Sns-board 에 합치자'). 셸이 defer 로 받음 (app.js 는 예산 끝).
   할 일 탭(#/todo) 맨 위 묶음에 카드 한 장 · 화면은 #/todo/ev (할 일 탭 안 — app.js 칸은 숨김).
   GET /dashboard/api/ssalmuk/state · POST …/act {op: run|done|profile, args} · 연결 칸: GET …/key · POST …/rotate (ssalmuk.py).
   쌀먹 서버가 1분마다 명령을 가져가 처리하고 상태를 다시 보낸다 — 20분 넘게 안 오면 '연결 끊김'. 바깥 주소·innerHTML 없음. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/ssalmuk/';
const CSS = '.ev .kpi{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:0 0 12px}' +
  '.ev .kpi div{background:var(--tile);border:1px solid var(--line);border-radius:10px;padding:10px;min-width:0}' +
  '.ev .kpi span{display:block;font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
  '.ev .kpi b{display:block;font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.ev .kpi b.urg{color:var(--red)}' +
  '.ev .kpi .z{opacity:.5}.ev .kpi .z b{color:var(--muted)}.evc .row{padding:4px 0}.evc .row .t{white-space:normal}' +
  '.ev .opts,.ev .chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}.ev .chips{margin:0 0 10px}' +
  '.ev .ti,.evc .ti{display:block;min-height:0;font-size:16px;font-weight:650;line-height:1.4;overflow-wrap:anywhere}' +
  '.ev .itm .st{white-space:normal}.ev .itm.done .ti{text-decoration:line-through;color:var(--sub)}' +
  '.ev .cpy{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:start;margin:0 0 12px}.ev .cpy .dr{margin:0;font-size:14px}' +
  '.ev .dl{margin-left:0;justify-content:flex-start}.ev .dl .done{margin-left:auto}.ev ol{margin:0 0 10px;padding-left:20px;font-size:14px}' +
  '.ev .key{font-family:ui-monospace,Menlo,monospace;font-size:13px;word-break:break-all;user-select:all}' +
  '.ev textarea.fl{min-height:96px;resize:vertical;line-height:1.5}.ev .fl{margin:0 0 10px;font-size:16px}' +
  '.ev .ck{display:flex;align-items:center;gap:10px;min-height:48px;font-size:15px}.ev .ck input{width:20px;height:20px}' +
  '.evc .st{white-space:normal}.evc .chips{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 0}' +
  'main[data-ev]>:not(#evs):not(#tdh){display:none!important}';
const GROUPS = [['today', '오늘 마감'], ['assist', '폼 + SNS'], ['check', '확인 필요'], ['sns', 'SNS 직접 참여']];
const TONE = /^(ok|hot|stop|info)$/;
const SRC = {ok: ['ok', '정상'], blocked: ['stop', '막힘'], skip: ['info', '건너뜀'], error: ['stop', '오류']};  // 수집 출처 상태 (11/14)
const OPL = {run: '지금 확인하기', done: '완료 표시', profile: '내 정보 저장'};
// 내 정보 칸 (점 키 그대로 보냄 — 명세 '내 정보')
const FIELDS = [['기본', [['name', '이름'], ['phone', '휴대폰 번호', 'tel'], ['email', '이메일', 'email'], ['birth', '생년월일 (1995-03-15)'], ['gender', '성별', ['', '여', '남']]]],
  ['주소 (배송 경품용)', [['zipcode', '우편번호'], ['address', '주소'], ['addressDetail', '상세 주소']]],
  ['SNS', [['sns.nickname', '닉네임'], ['sns.instagram', '인스타그램 아이디'], ['sns.instagramUrl', '인스타그램 프로필 주소', 'url'], ['sns.youtube', '유튜브 닉네임'],
    ['sns.blog', '블로그 주소', 'url'], ['shareUrl', '공유 인증용 게시물 주소', 'url']]],
  ['설문 답', [['demographics.age', '연령대 (30대)'], ['demographics.region', '지역 (서울)'], ['demographics.job', '직업 (회사원)'], ['surveyMode', '만족도 질문', ['', 'positive', 'neutral']]]]];
const OPT = {'': '선택 안 함', positive: '긍정으로 (매우 만족)', neutral: '보통으로'};
const AREAS = [['commentTemplates', '기대평·댓글 문구 (한 줄에 하나)'], ['quizAnswers', '퀴즈 정답 (질문 낱말 = 정답)'], ['customAnswers', '그 밖의 칸 (칸 이름 낱말 = 넣을 값)']];

let data = null, at = 0, busy = false, timer = null, card = null, scr = null, tt = null, kinfo = null, kbusy = false;
let seg = 'todo';
const open = {}, steps = {}, draft = {}, mine = new Set(), seen = new Set();

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
const https = u => typeof u === 'string' && /^https:\/\/[^\s]+$/.test(u);
const onTodo = () => /^(#\/?(todo\/?)?)?$/.test(location.hash);
const onScr = () => /^#\/?todo\/ev\/?$/.test(location.hash);
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
function copy(text, what) {
  const c = W.navigator && W.navigator.clipboard;
  (c && c.writeText ? c.writeText(String(text)) : Promise.reject(new Error('x')))
    .then(() => toast(`${what || ''} 복사했어요`.trim()), () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
}
const kst = s => {
  const d = s ? new Date(s) : null;
  if (!d || isNaN(d)) return '-';
  return d.toLocaleString('ko-KR', {timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false});
};
const md = s => { const m = /(\d{1,2})월\s*(\d{1,2})일/.exec(s || ''); return m ? `${m[1]}/${m[2]}` : (s || '-'); };
const st = () => (data && data.state) || null;
// 11/14 '자동화 되는 애들만': 새 숫자(todayApplied·weekApplied)가 오면 자동 응모 중심 화면 · 없으면 예전 화면 그대로
const isNew = () => !!st() && typeof ((st().counts || {}).todayApplied) === 'number';
const kday = t => { const d = t ? new Date(t) : null; return d && !isNaN(d) ? d.toLocaleDateString('en-CA', {timeZone: 'Asia/Seoul'}) : ''; };
const applied = () => ((st() || {}).applied || []).filter(h => h && typeof h === 'object');
const todayApplied = () => applied().filter(h => kday(h.at) === kday(Date.now()));
const skipped = () => { const s = st() || {}, n = s.notAutomated != null ? s.notAutomated : (s.counts || {}).notAutomated; return Number(n) || 0; };
// 11/14 자동 안 되는 이유별 개수 (counts.skipReasons = {라벨: 건수}) — 많은 순 · 라벨은 글자로만
const skipWhy = () => { const r = (st() || {}).counts && st().counts.skipReasons; return r && typeof r === 'object' ? Object.entries(r).map(([k, v]) => [String(k).slice(0, 30), Number(v) || 0]).filter(x => x[0] && x[1] > 0).sort((a, b) => b[1] - a[1]) : []; };
const skipEx = k => { const s = st() || {}, e = s.skipExamples || (s.counts || {}).skipExamples, a = e && typeof e === 'object' ? e[k] : null; return Array.isArray(a) ? a.filter(x => typeof x === 'string' && x).slice(0, 3).map(x => x.slice(0, 40)) : []; };
function skipNote() {
  const n = skipped(), why = skipWhy(), base = `자동 안 되는 이벤트 ${n}건은 건너뜀`;
  if (!why.length) return el('p', {class: 'note'}, base);
  const o = !!open.skip;
  return el('div', {class: 'note'}, btn('note', `${base}${o ? '' : why.slice(0, 2).map(x => ` · ${x[0]} ${x[1]}`).join('')} ${o ? '▴' : '▾'}`,
    () => { open.skip = !o; draw(); }, {'aria-expanded': o ? 'true' : 'false', style: 'background:none;border:0;padding:0;font:inherit;color:inherit;text-align:left;cursor:pointer'}),
    o ? el('ul', {class: 'note', style: 'margin:4px 0 0;padding-left:18px'}, why.map(x => el('li', {}, `${x[0]} · ${x[1]}건`, skipEx(x[0]).length ? el('div', {style: 'font-size:12px;opacity:.7'}, skipEx(x[0]).join(' · ')) : null))) : null);
}
const openLabel = u => /^https:\/\/([a-z0-9-]+\.)*donzomyo\.kr\//.test(u) ? '돈좀요에서 열기' : '이벤트 열기';
const hm = t => { const d = t ? new Date(t) : null; return d && !isNaN(d) ? d.toLocaleTimeString('ko-KR', {timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false}) : ''; };
// 11/14 키마다 마지막 네이버 답 (401 원인 찾기) — '딜봇 키 401 · 블로그 키 파일 없음'
const keyDiag = k => {
  if (!k || typeof k !== 'object') return '';
  const one = (name, v) => v === 'none' ? `${name} 키 없음` : v === 'no_file' ? `${name} 키 파일 못 읽음` : v === '-' || v == null ? '' : `${name} 키 ${String(v).slice(0, 12)}`;
  return [one('딜봇', k.dealbot), one('블로그', k.blog)].filter(Boolean).join(' · ');
};
// 11/14 네이버 검색 상태 한 줄 (state.search + 응답 search_proxy) — 글자로만, 없으면 null
const searchLine = () => {
  let q = (st() || {}).search; const p = data && data.search_proxy, n = v => Number.isFinite(Number(v)) ? Number(v) : null;
  q && typeof q === 'object' || (q = null);
  const ks = p && p.keysrc, cnt = p && n(p.today_count) ? n(p.today_count) : q ? n(q.count) : null;
  if (!q && !ks && !(p && n(p.today_count))) return null;
  const d = q && q.lastAt ? new Date(q.lastAt) : null, hm = d && !isNaN(d) ? d.toLocaleTimeString('ko-KR', {timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', hour12: false}) : '';
  const err = q && q.lastError ? String(q.lastError).slice(0, 40) : '';
  return ['네이버 검색', cnt != null ? `오늘 ${cnt}회${q && n(q.ok) != null ? ` (성공 ${n(q.ok)} · 실패 ${n(q.fail) || 0})` : ''}` : '',
    ks ? `키: ${ks === 'blog' ? '블로그 서버' : ks === 'dealbot' ? '딜봇' : String(ks).slice(0, 10)}` : '',
    q && q.lastStatus ? `마지막 ${String(q.lastStatus).slice(0, 10)}${hm ? ' ' + hm : ''}` : '', err ? `오류: ${err}` : '', keyDiag(p && p.keys)].filter(Boolean).join(' · ');
};
const todos = () => ((st() || {}).todo || []).filter(t => t && typeof t === 'object');
const cnt = () => Object.assign({auto: 0, left: 0, today: 0, applied: 0}, (st() || {}).counts || {});
function recount() {
  const s = st();
  if (!s) return;
  const left = todos().filter(t => !t.done);
  s.counts = Object.assign({}, s.counts, {left: left.length, today: left.filter(t => t.today).length});
}

// -- 서버
async function load() {
  if (busy) return;
  busy = true;
  try {
    const r = await fetch(API + 'state', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) { data = await r.json(); at = Date.now(); }
  } catch (e) { /* 다음에 다시 */ }
  busy = false;
  for (const x of (data && data.results) || []) {
    if (seen.has(x.id)) continue;
    seen.add(x.id);
    if (mine.delete(x.id)) toast(x.result === 'ok' ? `${OPL[x.op] || '명령'} — 이벤트 참여 서버가 처리했어요` : `${OPL[x.op] || '명령'} 안 됨: ${x.result}`);
  }
  draw();
  clearTimeout(timer);
  if (D.visibilityState === 'visible' && (onScr() || onTodo())) timer = setTimeout(load, onScr() ? (data && data.pending ? 5000 : 30000) : 300000);
}
async function post(path, body) {
  let r = null, d = {};
  try {
    r = await fetch(API + path, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)});
    d = await r.json().catch(() => ({}));
  } catch (x) { r = null; }
  return [r ? r.status : 0, d];
}
const why = (s, d, f) => !s ? '인터넷 연결을 확인해 주세요' : s === 401 ? '다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요'
  : s === 403 && d.code === 'csrf' ? '화면이 오래됐어요 — 새로고침해 주세요' : d.text || f;
async function act(op, args) {
  const [s, d] = await post('act', {op, args});
  if (s === 200 && d.ok) { if (d.id) mine.add(d.id); setTimeout(load, 800); return true; }
  toast(why(s, d, '보내지 못했어요 — 잠시 뒤 다시'));
  return false;
}
async function run(b) {
  b.disabled = true;
  if (await act('run', {})) toast('이벤트 참여 서버에 보냈어요 — 1분 안에 확인을 시작해요');
  else b.disabled = false;
}
async function done(t) {
  const v = !t.done;
  t.done = v; recount(); draw();
  if (await act('done', {url: t.url, done: v})) return;
  t.done = !v; recount(); draw();
}
async function keyLoad() {
  if (kbusy) return;
  kbusy = true;
  try {
    const r = await fetch(API + 'key', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    if (r.status === 200) kinfo = await r.json();
  } catch (e) { /* 다시 열면 */ }
  kbusy = false;
  draw();
}
async function rotate() {
  if (W.confirm && !W.confirm('새 키를 만들면 지금 키는 바로 안 받아요. 이벤트 참여 예비 대시보드에 새 키를 다시 붙여 넣어야 해요. 만들까요?')) return;
  const [s, d] = await post('rotate', {});
  if (s === 200 && d.key) { kinfo = d; toast('새 키를 만들었어요 — 복사해서 붙여 넣어 주세요'); draw(); } else toast(why(s, d, '만들지 못했어요 — 잠시 뒤 다시'));
}

// -- 할 일 탭 카드 (맨 위 묶음)
function drawCard() {
  if (!card) return;
  if (isNew()) return drawCard2();
  const s = st(), c = cnt(), today = todos().filter(t => t.today && !t.done);
  const chips = !s ? [el('span', {class: 'cp info'}, '연결 전')] : [data.stale ? el('span', {class: 'cp stop'}, '연결 끊김') : null,
    el('span', {class: 'cp info'}, `남은 할 일 ${c.left}`), c.today ? el('span', {class: 'cp hot'}, `오늘 마감 ${c.today}`) : null,
    s.running ? el('span', {class: 'cp ok'}, '확인 중') : null];
  card.replaceChildren(...[el('div', {class: 'ih'}, el('span', {class: 'ti'}, '이벤트 참여'), el('a', {class: 'lnk', href: '#/todo/ev'}, '열기 ›')),
    el('div', {class: 'chips'}, chips),
    !s ? el('p', {class: 'st'}, '연결 키를 이벤트 참여 예비 대시보드에 붙여 넣으면 여기 보여요') : null,
    data && data.stale && s ? el('p', {class: 'st warn'}, `이벤트 참여 서버 연결 끊김 · 마지막 받음 ${kst(data.receivedAt)}`) : null,
    today.slice(0, 3).map(t => el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, `오늘 마감 · ${t.title || ''}`)),
      https(t.url) ? el('a', {class: 'sm', href: t.url, target: '_blank', rel: 'noopener noreferrer'}, '돈좀요에서 열기') : null))].flat(9).filter(Boolean));
}

function drawCard2() {
  const s = st(), c = cnt(), ta = todayApplied(), chk = todos().filter(t => !t.done);
  const ext = (u, l) => https(u) ? el('a', {class: 'sm', href: u, target: '_blank', rel: 'noopener noreferrer'}, l) : null;
  card.replaceChildren(...[el('div', {class: 'ih'}, el('span', {class: 'ti'}, '이벤트 참여'), el('a', {class: 'lnk', href: '#/todo/ev'}, '열기 ›')),
    data.stale || s.running ? el('div', {class: 'chips'}, data.stale ? el('span', {class: 'cp stop'}, '연결 끊김') : null, s.running ? el('span', {class: 'cp ok'}, '확인 중') : null) : null,
    el('p', {class: 'st'}, `오늘 자동 응모 ${c.todayApplied} · 이번 주 ${c.weekApplied != null ? c.weekApplied : '-'} · 확인 필요 ${c.left}`),
    data.stale ? el('p', {class: 'st warn'}, `이벤트 참여 서버 연결 끊김 · 마지막 받음 ${kst(data.receivedAt)}`) : null,
    ta.slice(0, 3).map(h => el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, h.title || ''),
      el('span', {class: 's'}, [hm(h.at), h.source].filter(Boolean).join(' · '))), el('span', {class: 'cp ok'}, h.status === 'duplicate' ? '이미 응모' : '응모 완료'))),
    chk.slice(0, 3).map(t => el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, `확인 필요 · ${t.title || ''}`),
      t.detail ? el('span', {class: 's'}, t.detail) : null), ext(t.url, openLabel(t.url))))].flat(9).filter(Boolean));
}

// -- 이벤트 참여 화면 (#/todo/ev)
function arow(h) {
  const u = https(h.url);
  return el(u ? 'a' : 'div', {class: 'row', href: u ? h.url : null, target: u ? '_blank' : null, rel: u ? 'noopener noreferrer' : null,
    style: 'text-decoration:none;color:inherit'}, el('span', {class: 'grow'}, el('span', {class: 't'}, h.title || ''),
    el('span', {class: 's'}, [kst(h.at), h.source, h.detail].filter(Boolean).join(' · '))), el('span', {class: 'cp ok'}, h.status === 'duplicate' ? '이미 응모' : '응모 완료'));
}
function item(t) {
  const links = (t.links || []).filter(l => l && https(l.href)).slice(0, 3);
  const chip = t.chip && t.chip.text ? el('span', {class: 'cp ' + (TONE.test(t.chip.tone) ? t.chip.tone : 'info')}, t.chip.text) : null;
  const ss = (t.steps || []).filter(Boolean), o = !!steps[t.id];
  return el('article', {class: 'itm' + (t.done ? ' done' : ''), 'data-id': t.id || ''},
    el('div', {class: 'ih'}, el('h3', {class: 'ti'}, t.title || '(제목 없음)'), chip),
    el('p', {class: 'st'}, [t.prize, `마감 ${md(t.deadline)}`, typeof t.source === 'string' && t.source].filter(Boolean).join(' · ')),
    (t.actions || []).length ? el('div', {class: 'chips'}, t.actions.map(a => el('span', {class: 'cp'}, a))) : null,
    t.detail ? el('p', {class: 'st warn'}, t.detail) : null,
    ss.length ? [btn('fold', `참여 방법 ${ss.length}단계`, () => { steps[t.id] = !o; draw(); }, {'aria-expanded': o ? 'true' : 'false'}),
      o ? el('ol', {}, ss.map(x => el('li', {}, x))) : null] : null,
    t.comment ? el('div', {class: 'cpy'}, el('div', {class: 'dr'}, t.comment), btn('sm', '복사', () => copy(t.comment, '댓글 초안'))) : null,
    el('div', {class: 'dl'}, links.map(l => el('a', {class: 'sm', href: l.href, target: '_blank', rel: 'noopener noreferrer'}, String(l.label || '열기').slice(0, 14))),
      https(t.url) ? el('a', {class: 'sm', href: t.url, target: '_blank', rel: 'noopener noreferrer'}, openLabel(t.url)) : null,  // 링크가 비어도 늘 (RSS 만 읽음 — 11/14)
      btn('sm done', t.done ? '완료함' : '완료', () => done(t), {'aria-pressed': t.done ? 'true' : 'false'})));
}
function fold(k, label, body, onOpen) {
  const o = !!open[k];
  return el('div', {class: 'box'}, btn('fold', label, () => { open[k] = !o; if (!o && onOpen) onOpen(); draw(); }, {'aria-expanded': o ? 'true' : 'false'}),
    o ? body() : null);
}
const getp = (o, k) => k.split('.').reduce((a, x) => (a && typeof a === 'object' ? a[x] : undefined), o);
const kvLines = o => Object.entries(o && typeof o === 'object' ? o : {}).filter(([k]) => !k.startsWith('예시')).map(([k, v]) => `${k} = ${v}`).join('\n');
const parseKv = s => Object.fromEntries(String(s || '').split(/\r?\n/).map(l => l.split('=')).filter(a => a.length >= 2 && a[0].trim()).map(([k, ...v]) => [k.trim(), v.join('=').trim()]));
function pval(p, k) {
  const v = p[k] !== undefined ? p[k] : getp(p, k);
  if (k === 'commentTemplates') return Array.isArray(v) ? v.join('\n') : String(v || '');
  if (k === 'quizAnswers' || k === 'customAnswers') return kvLines(v);
  if (k === 'agreeOptional') return !!v;
  return v == null ? '' : String(v);
}
const copies = p => [['이름', p.name], ['전화번호', p.phone], ['숫자만', String(p.phone || '').replace(/\D/g, '')], ['이메일', p.email]].filter(([, v]) => v);
function profileBox() {
  const p = (st() || {}).profile || {}, cur = k => (k in draft ? draft[k] : pval(p, k)), set = k => e => { draft[k] = e.currentTarget.type === 'checkbox' ? e.currentTarget.checked : e.currentTarget.value; };
  const field = ([k, label, kind]) => [el('label', {class: 'lab', for: 'ev_' + k}, label), Array.isArray(kind)
    ? (s => { kind.forEach(o => s.append(el('option', {value: o, selected: cur(k) === o}, OPT[o] || o || '선택 안 함'))); return s; })(el('select', {class: 'fl', id: 'ev_' + k, onchange: set(k)}))
    : (i => { i.value = cur(k); return i; })(el('input', {class: 'fl', id: 'ev_' + k, type: kind === 'email' ? 'email' : kind === 'tel' ? 'tel' : kind === 'url' ? 'url' : 'text',
      autocomplete: 'off', oninput: set(k)}))];
  const areas = AREAS.map(([k, label]) => [el('label', {class: 'lab', for: 'ev_' + k}, label), (a => { a.value = cur(k); return a; })(el('textarea', {class: 'fl', id: 'ev_' + k, oninput: set(k)}))]);
  const cs = copies(p);
  return [!(st() || {}).profileSet ? el('p', {class: 'note'}, '아직 내 정보가 없어요 — 채워서 저장하면 이벤트 참여 서버에 들어가요') : null,
    cs.length ? el('div', {class: 'opts'}, cs.map(([k, v]) => btn('tg', `${k} 복사`, () => copy(v, k)))) : null,
    FIELDS.map(([g, fs]) => [el('p', {class: 'h3'}, g), fs.map(field)]),
    el('p', {class: 'h3'}, '응모 방식'),
    el('label', {class: 'ck'}, (c => { c.checked = !!cur('agreeOptional'); return c; })(el('input', {type: 'checkbox', onchange: set('agreeOptional')})), '선택(마케팅) 동의도 체크'),
    areas, btn('pb', '내 정보 저장', saveProfile), el('p', {class: 'note'}, '다음 확인부터 적용돼요 (이벤트 참여 서버가 1분 안에 가져가요)')];
}
async function saveProfile() {
  const p = (st() || {}).profile || {}, out = {};
  for (const k of Object.keys(draft)) {
    if (draft[k] === pval(p, k)) continue;
    out[k] = k === 'quizAnswers' || k === 'customAnswers' ? parseKv(draft[k]) : draft[k];
  }
  if (!Object.keys(out).length) { toast('바뀐 게 없어요'); return; }
  if (await act('profile', {profile: out})) {
    const s = st();
    if (s) { s.profile = Object.assign({}, s.profile); for (const k in out) s.profile[k] = k === 'commentTemplates' ? String(out[k]).split('\n').filter(Boolean) : out[k]; }
    for (const k in draft) delete draft[k];
    toast('저장했어요 — 이벤트 참여 서버에 보냈어요');
    draw();
  }
}
function connBox() {
  const k = kinfo;
  return [el('p', {class: 'st'}, `마지막 받은 시각 ${data && data.receivedAt ? kst(data.receivedAt) : '아직 없음'}`),
    !k ? el('p', {class: 'note'}, '불러오는 중…') : [
      el('p', {class: 'lab'}, 'Sns-board 주소'), el('div', {class: 'cpy'}, el('div', {class: 'dr key'}, k.url), btn('sm', '복사', () => copy(k.url, '주소'))),
      el('p', {class: 'lab'}, '연결 키'), el('div', {class: 'cpy'}, el('div', {class: 'dr key'}, k.key), btn('sm ac', '복사', () => copy(k.key, '연결 키'))),
      el('p', {class: 'note'}, `이벤트 참여 예비 대시보드 → 내 정보 → Sns-board 연결 에 주소와 키를 붙여 넣고 '연결 저장'.${k.at ? ` 키 만든 때 ${kst(k.at)}` : ''}`),
      btn('pb soft', '새 키 만들기', rotate)]];
}
function drawScr() {
  if (!scr) return;
  const s = st(), c = cnt(), ts = todos(), left = ts.filter(t => !t.done), dn = ts.filter(t => t.done), p = (s || {}).profile || {};
  const pend = !!(data && data.pending), running = !!(s && s.running);
  const cps = copies(p), nw = isNew();
  const out = [el('div', {class: 'top'}, el('a', {class: 'lnk', href: '#/todo'}, '‹ 할 일'), el('span', {class: 'sub'}, s && s.nextRun ? `다음 ${s.nextRun}` : ''),
    btn('lnk', '다시 보기', load))];
  if (!s) out.push(el('div', {class: 'bn'}, el('span', {}, "아직 연결 전이에요 — 아래 '연결'에서 주소와 키를 복사해 이벤트 참여 예비 대시보드 '내 정보 → Sns-board 연결'에 붙여 넣어 주세요")));
  else if (data.stale) out.push(el('div', {class: 'bn bad'}, el('span', {}, `이벤트 참여 서버 연결 끊김 — 20분 넘게 소식이 없어요 (마지막 받음 ${kst(data.receivedAt)})`)));
  if (s) {
    const lr = s.lastRun || {};
    out.push(el('section', {class: 'box'},
      nw ? el('div', {class: 'kpi'}, el('div', {}, el('span', {}, '오늘 자동 응모'), el('b', {}, String(c.todayApplied))),
        el('div', {}, el('span', {}, '이번 주'), el('b', {}, String(c.weekApplied != null ? c.weekApplied : '-'))),
        el('div', {}, el('span', {}, '확인 필요'), el('b', {class: c.left ? 'urg' : null}, String(c.left))))
        : el('div', {class: 'kpi'}, el('div', {class: c.auto ? null : 'z'}, el('span', {}, '자동 응모'), el('b', {}, String(c.auto))), el('div', {}, el('span', {}, '남은 할 일'), el('b', {}, String(c.left))),
        el('div', {}, el('span', {}, '오늘 마감'), el('b', {class: c.today ? 'urg' : null}, String(c.today)))),
      btn('pb', running ? `확인 중 · ${s.running.label || ''}` : pend ? '보낸 명령 처리 기다리는 중…' : '지금 확인하기', e => run(e.currentTarget), {disabled: running || null}),
      el('p', {class: 'note'}, [`마지막 확인 ${kst(s.checkedAt || lr.finished)}`, lr.finished && lr.ok === false ? '실패' : '',
        (s.runTimes || []).length ? `매일 ${s.runTimes.join(', ')} 자동` : '', pend ? `명령 ${data.pending}개 기다리는 중` : ''].filter(Boolean).join(' · ')),
      nw && skipped() ? skipNote() : null));
    if (nw) {
      const ta = todayApplied();
      out.push(el('p', {class: 'grp'}, `오늘 응모한 이벤트 ${ta.length}`), ta.length ? el('div', {class: 'box'}, ta.map(arow)) : el('p', {class: 'empty'}, '오늘은 아직 자동 응모한 이벤트가 없어요'),
        el('p', {class: 'grp'}, `확인 필요 ${left.length}`), left.length ? left.map(item) : el('p', {class: 'empty'}, '확인할 것 없어요'),
        dn.length ? [el('p', {class: 'grp'}, `완료 표시 ${dn.length}`), dn.map(item)] : null);
    } else {
    if (cps.length) out.push(el('div', {class: 'opts'}, cps.map(([k, v]) => btn('tg', `${k} 복사`, () => copy(v, k)))));
    out.push(el('div', {class: 'seg', role: 'group'}, [['todo', `남은 할 일 ${left.length}`], ['done', `완료 ${dn.length}`]].map(([k, l]) =>
      btn('', l, () => { seg = k; draw(); }, {'aria-pressed': seg === k ? 'true' : 'false'}))));
    if (seg === 'done') out.push(dn.length ? dn.map(item) : el('p', {class: 'empty'}, '완료한 일이 없어요'));
    else {
      const gs = GROUPS.map(([g, l]) => [l, left.filter(t => (GROUPS.some(x => x[0] === t.group) ? t.group : 'sns') === g)]).filter(([, xs]) => xs.length);
      out.push(gs.length ? gs.map(([l, xs]) => [el('p', {class: 'grp'}, `${l} ${xs.length}`), xs.map(item)]) : el('p', {class: 'empty'}, '남은 할 일이 없어요'));
    }
    }
    out.push(el('p', {class: 'grp'}, '더 보기'), fold('applied', `자동 응모 내역 ${applied().length}`, () => applied().length ? el('div', {}, applied().map(arow))
      : el('p', {class: 'note'}, '아직 자동 응모한 이벤트가 없어요')),
      (Array.isArray(s.sources) && s.sources.length) || searchLine() ? fold('src', `수집 출처 ${(s.sources || []).length}`, () => [searchLine() && el('p', {class: 'note', style: 'font-size:12px;opacity:.7'}, searchLine()), ...(s.sources || []).filter(x => x && typeof x === 'object').map(x =>
        el('div', {class: 'row'}, el('span', {class: 'grow'}, el('span', {class: 't'}, x.name || x.id || ''),
          el('span', {class: 's'}, [x.items != null ? `${x.items}개` : '', x.why, x.until ? `${kst(x.until)}까지` : '', x.at ? `확인 ${kst(x.at)}` : ''].filter(Boolean).join(' · '))),
          el('span', {class: 'cp ' + (SRC[x.status] || SRC.error)[0]}, (SRC[x.status] || ['', x.status || '?'])[1])))]) : null,
      fold('profile', '내 정보', profileBox));
  }
  out.push(fold('conn', '연결', connBox, keyLoad));
  if (!s && !open.conn && !open._auto) { open.conn = open._auto = true; keyLoad(); }
  scr.replaceChildren(...out.flat(9).filter(Boolean));
}
function draw() {
  drawCard();
  if (W.dispatchEvent) W.dispatchEvent(new Event('sb-hub'));  // 할 일 종류 목록의 '이벤트' 수
  const a = D.activeElement;
  if (scr && a && scr.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return;  // 입력 중이면 다음에
  drawScr();
}

// -- 붙이기 (app.js 가 #main 을 다시 그리면 다시 붙음 — routine.js 틀)
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('ev-css')) D.head.append(el('style', {id: 'ev-css', text: CSS}));
  if (onScr()) {
    if (!scr) { scr = el('section', {id: 'evs', class: 'ev', 'aria-label': '이벤트 참여'}); drawScr(); }
    const h = m.firstChild && m.firstChild.id === 'tdh' ? m.firstChild : null;  // 할 일 종류 목록(todohub.js) 바로 아래
    if ((h ? h.nextSibling : m.firstChild) !== scr) h ? h.after(scr) : m.prepend(scr);
    if (!m.hasAttribute('data-ev')) m.setAttribute('data-ev', '');
    title();
  } else {
    if (m.hasAttribute('data-ev')) m.removeAttribute('data-ev');
    if (scr && scr.parentNode) scr.remove();
  }
  if (onTodo()) {
    if (!card) { card = el('article', {id: 'evc', class: 'itm evc', 'aria-label': '이벤트 참여'}); drawCard(); }
    if (m.children[m.children.length - 1] !== card) m.append(card);  // 할 일 맨 아래 (주인 11/14)
  } else if (card && card.parentNode) card.remove();
  if ((onScr() || onTodo() || /^#\/?todo\/\w+\/?$/.test(location.hash)) && (!data || Date.now() - at > 30000)) load();  // 할 일 종류 화면도 — '이벤트' 수
}
function title() {
  const t = D.getElementById('ttl');
  if (onScr() && t && t.textContent !== '이벤트 참여') t.textContent = '이벤트 참여';
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  const t = D.getElementById('ttl');
  if (t) new MutationObserver(title).observe(t, {childList: true, characterData: true, subtree: true});
  W.addEventListener('hashchange', () => { mount(); if (onScr()) { W.scrollTo(0, 0); load(); } });
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && (onScr() || onTodo())) load(); });
  mount();
}
// 할 일 종류 목록(todohub.js)의 '이벤트' 칸: 확인 필요 수 · 오늘 마감·연결 끊김이면 급함
W.SBEvent = {load, mount, n: () => st() ? {n: cnt().left, u: (isNew() ? 0 : cnt().today) + (data.stale ? 1 : 0)} : null};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
