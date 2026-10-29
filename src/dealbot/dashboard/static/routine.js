/* 오늘 할 일 (주인 10/27 — 손일 하루 5번). 할 일 탭 맨 위 칸(급한 알림만 그 위) · GET /dashboard/api/routine (routine.py).
   #main 을 다시 그리면 같은 칸을 다시 붙임 (logins.js 틀). 체크는 단추 (app.js 는 입력 칸 커서 동안 그리기를 미룸).
   그 아래 에이블리 링크 부탁 (10/28): 폰 앱에서 만든 링크를 붙여 넣어 blog.similar_links {blog, pid, links, rid}. */
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
  '#rtp .bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:0 0 14px}#rtp .bar i{display:block;height:100%;background:var(--gr)}' +
  '#rtp .pb .sm{flex:1 1 30%;min-height:48px;font-size:15px;font-weight:600;justify-content:center}' +
  '#rtp .ab .grow{padding-top:0}#rtp .ab+.ab{background-position:0 0}#rtp .pf .fl{flex:1 1 60%;min-width:0;font-size:16px}' +
  '#rtp .pf .sm{min-height:48px;font-size:15px;font-weight:600;padding:0 18px}#rtp .er{color:var(--red)}' +
  '#rtp .ath{flex:none;width:76px;height:76px;object-fit:cover;border-radius:10px;background:var(--line)}';
let box = null, data = null, at = 0, timer = null, busy = false, tt = null, open = {}, cur = '', sig = '';
const typed = {}, sent = {}, errs = {};  // 붙여 넣던 글 · 넣은 부탁(다음 목록까지 숨김) · 안 된 이유

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
  const a = D.activeElement;
  if (!(a && a.tagName === 'INPUT' && box && box.contains(a))) draw();  // 붙여 넣는 중이면 다음에
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

async function send(body) {
  let r = null, d = {};
  try {
    r = await fetch('/dashboard/api/action', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)});
    d = await r.json().catch(() => ({}));
  } catch (x) { r = null; }
  return [r ? r.status : 0, d];
}
const why = (s, d, f) => !s ? '인터넷 연결을 확인해 주세요' : s === 401 ? '다시 로그인해 주세요 — 새로고침하면 로그인 화면이 떠요'
  : s === 403 && d.code === 'csrf' ? '화면이 오래됐어요 — 새로고침해 주세요' : d.text || f;

async function check(it) {
  const v = !it.done;
  it.done = v; recount(); draw();
  const [s, d] = await send({action: 'routine.done', args: {id: it.id, done: v}, cid: rid(), ctoken: null});
  if (s === 200 && d.status === 'ok') return;
  it.done = !v; recount(); draw();
  toast(why(s, d, '체크하지 못했어요 — 잠시 뒤 다시'));
}

// 에이블리 링크 부탁 (주인 10/28) — 넣으면 줄을 숨기고, 블로그 프로그램 결과가 실패면 이유와 함께 다시 보임
async function put(r, f, b, m) {
  const v = f.value.trim();
  if (!v) { m.textContent = '에이블리 앱에서 만든 링크를 먼저 붙여 넣어 주세요'; return; }
  b.disabled = true; m.textContent = '넣는 중…';
  const body = {action: 'blog.similar_links', args: {blog: 'issue', pid: r.pid, links: [v], rid: r.rid}, cid: rid(), ctoken: null};
  let [s, d] = await send(body);
  if (s === 409 && d.ctoken && W.confirm && W.confirm(d.title || '이대로 넣을까요?')) { body.ctoken = d.ctoken; [s, d] = await send(body); }
  b.disabled = false;
  if (s === 202 || (s === 200 && d.status === 'ok')) {
    sent[r.rid] = 1; delete typed[r.rid]; delete errs[r.rid]; toast('넣었어요'); draw();
    if (d.id) watch(d.id, r.rid, 0);
    return;
  }
  m.textContent = d.status === 'confirm' ? '취소했어요' : why(s, d, '넣지 못했어요 — 잠시 뒤 다시');
}
async function watch(id, k, n) {
  let x = null;
  try {
    const r = await fetch('/dashboard/api/actions?ids=' + id, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
    x = ((await r.json()).actions || [])[0];
  } catch (e) { x = null; }
  if (x && /^(error|rejected|expired)$/.test(x.status)) {
    delete sent[k]; errs[k] = x.text || '넣지 못했어요 — 다시 넣어 주세요'; toast(errs[k]); draw();
  } else if (!(x && x.status === 'ok') && n < 60) setTimeout(() => watch(id, k, n + 1), 3000);
}
const won = n => n ? n.toLocaleString('ko-KR') + '원' : '';
function req(r) {
  const m = el('span', {class: 's' + (errs[r.rid] ? ' er' : ''), role: 'status'}, errs[r.rid] || '');
  const f = el('input', {class: 'fl', type: 'text', placeholder: '여기에 링크 붙여넣기', autocomplete: 'off', autocapitalize: 'off',
    spellcheck: 'false', enterkeyhint: 'done', 'aria-label': `${r.name} 에이블리 링크`, oninput: e => { typed[r.rid] = e.currentTarget.value; }});
  f.value = typed[r.rid] || '';
  const b = el('button', {class: 'sm ac', type: 'button', onclick: () => put(r, f, b, m)}, '넣기');
  // 주인 10/29 '사진 보고 디자인이 비슷해야' — 같은 상품 사진 · 후기 · 만족 · 글 속 옷과 닮음
  const facts = [r.reviews && `후기 ${r.reviews.toLocaleString('ko-KR')}개`, r.satisfaction && `만족 ${r.satisfaction}%`, r.sim && `닮음 ${r.sim}점`].filter(Boolean);
  return el('div', {class: 'it ab', 'data-rid': r.rid},
    r.thumb ? el('img', {class: 'ath', src: '/dashboard/thumb/' + encodeURIComponent(r.thumb), alt: `${r.name} 사진`, loading: 'lazy'}) : null,
    el('div', {class: 'grow'}, el('span', {class: 't'}, r.name),
    el('span', {class: 's'}, [r.piece, won(r.price), r.shop].filter(Boolean).join(' · ')),
    facts.length ? el('span', {class: 's'}, facts.join(' · ')) : null,
    el('span', {class: 's'}, [r.title && `글: ${r.title}`, r.due_text && `발행 ${r.due_text} 전`].filter(Boolean).join(' · ')),
    el('div', {class: 'bt'}, el('a', {class: 'sm', href: r.url, target: '_blank', rel: 'noopener noreferrer'}, '에이블리에서 열기 ↗')),
    el('div', {class: 'bt pf'}, f, b), m));
}
function ably() {
  const rs = data && data.ably && data.ably.rows ? data.ably.rows.filter(r => !sent[r.rid]) : [];
  return rs.length ? [el('div', {class: 'top', style: 'margin:4px 0 6px'}, el('h2', {}, `에이블리 링크 부탁 (${rs.length})`)),
    data.ably.note ? el('p', {class: 'note', style: 'margin:0 0 10px'}, data.ably.note) : null, el('div', {class: 'box'}, rs.map(req))] : [];
}

function copy(text) {
  const c = W.navigator && W.navigator.clipboard;
  (c && c.writeText ? c.writeText(text) : Promise.reject(new Error('x')))
    .then(() => toast('복사했어요'), () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요'));
}

// 본문 복사 (10/27): html+글 한 번에 · 안 되면 화면 밖 칸에 DOM 으로 담아 복사 (html 글을 그대로 넣지 않음 · on*·스크립트 한 번 더 뺌)
function rich(p) {
  const c = W.navigator && W.navigator.clipboard, B = (t, y) => new Blob([t], {type: y});
  if (!c || !c.write || !W.ClipboardItem) return off(p);
  c.write([new W.ClipboardItem({'text/html': B(p.html, 'text/html'), 'text/plain': B(p.text, 'text/plain')})]).then(() => toast('복사했어요'), () => off(p));
}
function off(p) {
  let ok = false;
  try {
    const doc = new DOMParser().parseFromString(p.html, 'text/html'), d = el('div', {contenteditable: 'true',
      style: 'position:fixed;left:-9999px;top:0;width:600px;opacity:0'}), r = D.createRange(), s = W.getSelection();
    doc.querySelectorAll('script,style,iframe,object,embed,link,meta').forEach(x => x.remove());
    doc.querySelectorAll('*').forEach(x => [...x.attributes].forEach(a => /^on/i.test(a.name) && x.removeAttribute(a.name)));
    d.append(...Array.from(doc.body.childNodes, n => D.importNode(n, true)));
    D.body.append(d);
    r.selectNodeContents(d); s.removeAllRanges(); s.addRange(r);
    ok = D.execCommand('copy');
    s.removeAllRanges(); d.remove();
  } catch (e) { ok = false; }
  toast(ok ? '복사했어요' : '복사하지 못했어요 — 잠시 뒤 다시');
}
const big = (label, f) => el('button', {class: 'sm', type: 'button', onclick: f}, label);
function paste(p) {
  return [el('div', {class: 'bt pb'}, big('제목 복사', () => copy(p.title)), big('본문 복사', () => rich(p)),
    p.tag_text ? big('태그 복사', () => copy(p.tag_text)) : null),
  el('span', {class: 's'}, "본문은 글쓰기에 바로 붙이지 말고 — 빈 본문을 한 번 누르고 책갈피 '꾸미기' → 뜨는 칸에 붙여넣기")];
}

function item(it) {
  const btns = it.done ? [] : [it.copy.map(c => el('button', {class: 'sm', type: 'button', onclick: () => copy(c.text)}, c.label)),
    it.links.map(l => el('a', {class: 'sm', href: l.url, target: '_blank', rel: 'noopener noreferrer'}, l.label))].flat();
  return el('div', {class: 'it' + (it.done ? ' dn' : ''), 'data-id': it.id},
    el('button', {class: 'cb', type: 'button', role: 'checkbox', 'aria-checked': it.done ? 'true' : 'false', 'aria-label': it.title,
      onclick: () => check(it)}, it.done ? '✓' : ''),
    el('div', {class: 'grow'}, el('span', {class: 't'}, it.title),
      el('span', {class: 's'}, [it.tag, `${it.min}분`, it.note].filter(Boolean).join(' · ')),
      it.paste && !it.done ? paste(it.paste) : null, btns.length ? el('div', {class: 'bt'}, btns) : null));
}

function slot(s) {
  const all = s.total && s.done === s.total;
  const tail = !s.total ? '없음' : `${s.done}/${s.total}` + (all ? ' · 끝' : ` · 약 ${s.min_left}분`) + (s.current ? ' · 지금' : '');
  return [el('button', {class: 'sl' + (s.current ? ' cur' : ''), type: 'button', 'aria-expanded': open[s.key] ? 'true' : 'false',
    onclick: () => { open[s.key] = !open[s.key]; draw(); }}, el('b', {}, `${open[s.key] ? '▾' : '▸'} ${s.label} ${s.at}`), el('span', {}, tail)),
    open[s.key] ? s.items.map(item) : null];
}

function times() {  // 블로그별 예약 추천 시각 (주인 10/29) — 눌러서 펼침
  const ts = data && data.times || [];
  return ts.length ? el('div', {class: 'box'}, el('button', {class: 'sl', type: 'button', 'aria-expanded': open.tm ? 'true' : 'false',
    onclick: () => { open.tm = !open.tm; draw(); }}, el('b', {}, `${open.tm ? '▾' : '▸'} 블로그별 예약 추천 시각`)),
    open.tm ? ts.map(t => el('div', {class: 'it'}, el('div', {class: 'grow', style: 'padding-top:0'},
      el('span', {class: 't'}, `${t.name} · ${t.times.join(' · ')}`), t.note ? el('span', {class: 's'}, t.note) : null))) : null) : null;
}

function draw() {
  if (!box) return;
  const ok = !!(data && data.slots && data.total), ab = ably();
  box.hidden = !ok && !ab.length;
  if (box.hidden) { box.replaceChildren(); return; }
  const bar = el('i');
  if (ok) bar.style.width = Math.round(100 * data.done / data.total) + '%';
  box.replaceChildren(...[ok ? [
    el('div', {class: 'top', style: 'margin:4px 0 6px'}, el('h2', {}, `오늘 할 일 · ${data.done}/${data.total}`),
      el('button', {class: 'lnk', type: 'button', onclick: load}, '다시 보기')),
    el('div', {class: 'bar'}, bar),
    data.today ? null : el('p', {class: 'note'}, `${data.day} 목록이에요 — 오늘 목록이 아직 안 왔어요`),
    el('div', {class: 'box'}, data.slots.map(slot)),
    times(),
    data.overflow ? el('p', {class: 'note', style: 'margin:-6px 0 12px'}, `오늘은 여기까지 — 나머지 ${data.overflow}개는 다음에`) : null] : [], ab].flat().filter(Boolean));
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
