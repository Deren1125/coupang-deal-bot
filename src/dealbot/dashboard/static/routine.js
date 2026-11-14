/* 오늘 할 일 (주인 10/27 — 손일 하루 5번). 할 일 탭 맨 위 칸(급한 알림만 그 위) · GET /dashboard/api/routine (routine.py).
   #main 을 다시 그리면 같은 칸을 다시 붙임 (logins.js 틀). 체크는 단추 (app.js 는 입력 칸 커서 동안 그리기를 미룸).
   그 아래 에이블리 링크 부탁 (10/28): 폰 앱에서 만든 링크를 붙여 넣어 blog.similar_links {blog, pid, links, rid}. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.rt .box,.box.rt{padding:4px 16px 6px;margin-bottom:20px}.rt .sl{display:flex;align-items:center;gap:10px;width:100%;min-height:56px;padding:12px 0;' +
  'border:0;border-top:1px solid var(--line);background:none;text-align:left;color:var(--text)}.rt .box>.sl:first-child,.box.rt>.sl:first-child{border-top:0}' +
  '.rt .sl b{flex:1;min-width:0;font-size:15px;line-height:1.4}.rt .sl span{font-size:13px;color:var(--sub);font-weight:500}' +
  '.rt .sl.cur span{color:var(--ac-ink);font-weight:600}.rt .it{display:flex;gap:12px;align-items:flex-start;padding:14px 0}' +
  '.rt .it+.it{background:linear-gradient(var(--line),var(--line)) 56px 0/100% 1px no-repeat}' +
  '.rt .cb{flex:none;width:44px;height:44px;padding:0;border:2px solid var(--fline);border-radius:12px;background:var(--field);color:var(--gr);font-size:20px}' +
  '.rt .cb[aria-checked=true]{background:var(--gr-soft);border-color:var(--gr)}.rt .it .grow{flex:1;min-width:0;padding-top:10px}' +
  '.rt .t{display:block;font-size:15px;font-weight:600;line-height:1.45;overflow-wrap:anywhere}.rt .dn .t{text-decoration:line-through;color:var(--sub)}.rt .sc{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;font-size:12px;font-weight:600;line-height:20px;font-style:normal;color:var(--sub);background:var(--line);white-space:nowrap}.rt .sc.h{color:#E8590C;background:rgba(232,89,12,.14)}.rt .sc.ok{color:var(--gr);background:var(--gr-soft)}.rt .pp .sm[disabled]{opacity:.55}' +
  '.rt .s{display:block;margin-top:3px;font-size:13px;line-height:1.5;color:var(--sub);overflow-wrap:anywhere}.rt .bt{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}' +
  '.rt a.sm{display:inline-flex;align-items:center;text-decoration:none;color:var(--text)}' +
  '.rt .bar{height:6px;border-radius:3px;background:var(--line);overflow:hidden;margin:0 0 14px}.rt .bar i{display:block;height:100%;background:var(--gr)}' +
  '.rt .pp .sm{flex:1 1 30%;min-height:48px;font-size:15px;font-weight:600;justify-content:center}' +
  '.rt .ab .grow{padding-top:0}.rt .ab+.ab{background-position:0 0}.rt .pf .fl{flex:1 1 60%;min-width:0;font-size:16px}' +
  '.rt .pf .sm{min-height:48px;font-size:15px;font-weight:600;padding:0 18px}.rt .er{color:var(--red)}' +
  '.bk{padding:0 16px 4px}.bk .bh{display:flex;align-items:center;gap:12px;width:100%;min-height:64px;padding:14px 0 10px;border:0;background:none;text-align:left;color:var(--text)}' +
  '.bk .dt{flex:none;width:12px;height:12px;border-radius:50%}.bk .bn2{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}' +
  '.bk .bn2 b{font-size:17px;line-height:1.3}.bk .bn2 span{font-size:13px;color:var(--sub)}.bk .cv{flex:none;font-size:14px;font-weight:600;color:var(--ac-ink)}' +
  '.bk .bar{margin:0 0 12px}.rt .sg{display:flex;justify-content:space-between;margin:14px 0 2px;font-size:13px;font-weight:700;color:var(--sub)}' +
  '.rt .sg i{font-style:normal;font-weight:600}.rt .mi{padding:10px 0;gap:10px}.rt .mi .grow{padding-top:2px}.rt .mi .t{font-size:14px}' +
  '.rt .mi .bt{margin-top:8px;gap:6px}.rt .mi .sm{min-height:36px;padding:0 12px;font-size:13px}' +
  '.bk .hu{margin-left:8px;padding:2px 8px;border-radius:999px;background:var(--red);color:#fff;font-size:12px;font-style:normal;font-weight:700;vertical-align:2px}' +
  '.bk .itm{margin:8px 0}.rt .ath{flex:none;width:76px;height:76px;object-fit:cover;border-radius:10px;background:var(--line)}';
let box = null, data = null, at = 0, timer = null, busy = false, tt = null, open = {}, cur = '', sig = '';
const bo = {};
let up = null;  // 숏폼·릴스·롱폼 올리기 (uploadplan.py · 주인 11/09 '릴스·쇼츠·룩북도 다 할 일 탭에')  // 블로그별 칸 펼침 (주인 11/09 '한 블로그 로그인하면 그 블로그 일 몰아서')
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
const on = () => /^(#\/?(todo(\/\w*)?\/?)?)?$/.test(location.hash) && !/\/(deal|ev)\/?$/.test(location.hash);  // #/todo · #/todo/<종류> (딜만 보기 #/todo/deal · 이벤트 #/todo/ev 는 아님)
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
    if (by()) { const u = await fetch('/dashboard/api/uploads', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}}); if (u.status === 200) up = await u.json(); }
  } catch (e) { /* 다음에 다시 */ }
  busy = false;
  const c = data && (data.slots || []).find(s => s.current);
  if (c && c.key !== cur) { cur = c.key; open = {[cur]: true, tm: open.tm, rs: open.rs}; }
  const a = D.activeElement;
  if (!(a && a.tagName === 'INPUT' && box && box.contains(a))) draw();  // 붙여 넣는 중이면 다음에
  const k = ids().join();
  if (k !== sig && W.dispatchEvent) { sig = k; W.dispatchEvent(new Event('sb-routine')); }  // app.js 가 겹친 할 일을 빼고 다시 그림
  if (W.SBHub && W.SBHub.draw) W.SBHub.draw();  // 종류 목록의 소식·점검 줄 (todohub.js — 새 신호를 만들지 않고 바로 다시 그림)
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
  it.done = v; recount(); draw(); bump();
  const [s, d] = await send({action: 'routine.done', args: {id: it.id, done: v}, cid: rid(), ctoken: null});
  if (s === 200 && d.status === 'ok') { if (/^(pub|rsv):/.test(it.id) || /깨어나면/.test(d.text)) toast(d.text); return; }
  it.done = !v; recount(); draw(); bump();
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
  cnt.ab = rs.length;
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
// 아직 못 쓰는 묶음 (주인 11/14 '만드는 중인 옛 묶음을 복사하게 두지 않기'): 새 원고 만드는 중·못 만듦(paste.stale) · 자동 점검에 걸림(gate.ok 거짓)
const nr = it => !!it.paste && (!!it.paste.stale || !!(it.gate && it.gate.ok === false));
const OLD = 600000;  // 받은 지 10분 넘은 목록으로는 복사하지 않음 — 그새 묶음이 바뀌었을 수 있어 먼저 새로 받음
async function body(p) {
  if (Date.now() - at <= OLD) return rich(p);
  await load();
  toast(Date.now() - at <= OLD ? '목록을 새로 받았어요 — 한 번 더 눌러 주세요' : '목록을 새로 받지 못했어요 — 인터넷 연결을 확인해 주세요');
}
function paste(it) {
  const p = it.paste, g = it.gate;
  // 못 쓰는 묶음은 누를 수 없는 단추 하나 (본문 복사 없음) · 제목 복사는 그대로
  if (nr(it)) return [el('div', {class: 'bt pp'}, big('제목 복사', () => copy(p.title)), el('button', {class: 'sm', type: 'button', disabled: true},
    p.stale && p.why !== 'failed' ? '새 원고 만드는 중 — 잠시 뒤' : '고치는 중 · ' + (p.stale ? p.reason : g.text))),
  el('span', {class: 's'}, '다 되면 본문 복사 단추가 다시 떠요 — 그때까지 이 글은 올리지 않아도 돼요')];
  // 태그는 '꾸미기'가 발행 설정창에 넣어 줘서 따로 복사 단추 없음 (주인 11/06 '태그 복사 안 해도 들어가는데')
  return [el('div', {class: 'bt pp'}, big('제목 복사', () => copy(p.title)), big('본문 복사', () => body(p))),
  el('span', {class: 's'}, "본문은 글쓰기에 바로 붙이지 말고 — 빈 본문을 한 번 누르고 책갈피 '꾸미기' → 뜨는 칸에 붙여넣기 (태그도 저절로)")];
}

function item(it) {
  if (by() && it.group === '이웃') return mini(it);
  // Pick '사진 N장 필요' 카드는 이 붙여넣기 줄에 한 마디로 (주인 11/09 대시보드 줄이기 ④) — 누르면 그 글 화면에서 사진 올리기
  const m = /^(?:pub|rsv):(\w+):(\d+)$/.exec(it.id), ph = m && (W.SBPh || {})[`post-${m[1]}-${m[2]}`], pk = ph && ph.kind === 'photos' ? ph : null;
  const btns = it.done ? [] : [pk ? el('button', {class: 'sm', type: 'button', onclick: () => { location.hash = '#/i/' + pk.iid; }}, '사진 올리기') : [],
    it.copy.map(c => el('button', {class: 'sm', type: 'button', onclick: () => copy(c.text)}, c.label)),
    it.links.map(l => el('a', {class: 'sm', href: l.url, target: '_blank', rel: 'noopener noreferrer'}, l.label))].flat();
  return el('div', {class: 'it' + (it.done ? ' dn' : ''), 'data-id': it.id},
    el('button', {class: 'cb', type: 'button', role: 'checkbox', 'aria-checked': it.done ? 'true' : 'false', 'aria-label': it.title,
      onclick: () => check(it)}, it.done ? '✓' : ''),
    el('div', {class: 'grow'}, el('span', {class: 't'}, it.title, it.sc ? el('i', {class: 'sc' + (it.hot ? ' h' : '')}, it.sc) : null,
      it.paste && it.gate && it.gate.ok && !nr(it) ? el('i', {class: 'sc ok'}, '점검 통과') : null),
      el('span', {class: 's'}, [by() ? it.group : it.tag, `${it.min}분`, it.note, pk && pk.status.split(' · ')[0]].filter(Boolean).join(' · ')),
      it.paste && !it.done ? paste(it) : null, btns.length ? el('div', {class: 'bt'}, btns) : null));
}

// 이웃 줄은 짧게 (11/09) · 서로이웃은 이유·멘트도 (11/14) · 여는 주소 뒤에 멘트·블로그(#bmmsg=…&bmb=…) — 책갈피 '이웃 관리'가 읽음
const nbUrl = (it, u, msg) => /^https:\/\/(m\.)?blog\.naver\.com\/[\w-]+(\/\d+)?$/.test(u) ? u + '#' + (msg ? 'bmmsg=' + encodeURIComponent(msg) + '&' : '') + 'bmb=' + it.blog : u;
function mini(it) {
  const t = it.title.replace(/^(서로이웃 신청|공감·댓글) · /, ''), nb = it.id.startsWith('nb-add:'),
    why = nb && it.note, msg = nb && (it.copy.find(c => c.label == '멘트') || {}).text;
  return el('div', {class: 'it mi' + (it.done ? ' dn' : ''), 'data-id': it.id},
    el('button', {class: 'cb', type: 'button', role: 'checkbox', 'aria-checked': it.done ? 'true' : 'false', 'aria-label': it.title,
      onclick: () => check(it)}, it.done ? '✓' : ''),
    el('div', {class: 'grow'}, el('span', {class: 't'}, t), why ? el('span', {class: 's'}, why) : null,
      msg && !it.done ? el('span', {class: 's'}, '멘트 · ' + msg) : null, it.done ? null : el('div', {class: 'bt'},
      it.links.map(l => el('a', {class: 'sm', href: nbUrl(it, l.url, msg), target: '_blank', rel: 'noopener noreferrer'}, l.label + ' ↗')),
      it.copy.map(c => el('button', {class: 'sm', type: 'button', onclick: () => copy(c.text)}, c.label + ' 복사')))));
}

function slot(s) {
  const all = s.total && s.done === s.total;
  const tail = !s.total ? '없음' : `${s.done}/${s.total}` + (all ? ' · 끝' : ` · 약 ${s.min_left}분`) + (s.current ? ' · 지금' : '');
  return [el('button', {class: 'sl' + (s.current ? ' cur' : ''), type: 'button', 'aria-expanded': open[s.key] ? 'true' : 'false',
    onclick: () => { open[s.key] = !open[s.key]; draw(); }}, el('b', {}, `${open[s.key] ? '▾' : '▸'} ${s.label} ${s.at}`), el('span', {}, tail)),
    open[s.key] ? s.items.filter(i => !(i.paste && !i.done)).map(item) : null];
}
// 붙여넣기 대기 (주인 10/30) — 시간대 칸과 상관없이 맨 위에 모두 (안 한 것만 · 체크하면 제 칸으로) · 꾸미기 설치는 연결 탭
function pasteBox() {
  const ps = data && data.slots ? data.slots.flatMap(s => s.items.filter(i => i.paste && !i.done)).sort((a, b) => nr(a) - nr(b)) : [], mk = ps.filter(nr).length;
  return ps.length ? [el('div', {class: 'top', style: 'margin:4px 0 6px'}, el('h2', {}, `붙여넣기 대기 (준비 ${ps.length - mk} · 만드는 중 ${mk})`),
    el('a', {class: 'lnk', href: '#/connect'}, '꾸미기 설치')), el('div', {class: 'box'}, ps.map(item))] : [];
}

// ⚡ 막 뜨는 중 (주인 10/30) — 키워드마다 단추: 누르면 그 키워드로 Pick 글 쓰기 (blog.issue · 11/09 '눌러도 연결이 안 돼' — 예전엔 화면만 옮김)
function rising() {
  const xs = data && data.rising || [];
  return xs.length ? el('div', {class: 'bn rz', style: 'display:flex;flex-wrap:wrap;gap:6px;align-items:center'}, '⚡ 막 뜨는 중 — 누르면 Pick 글 쓰기', xs.map(k => el('button', {class: 'sm', type: 'button', onclick: e => write(k, e)}, k))) : null;
}
async function write(k, e) {
  e.currentTarget.disabled = true;
  const [s, d] = await send({action: 'blog.issue', args: {keyword: k}, cid: rid(), ctoken: null});
  toast(s === 202 || s === 200 ? `'${k}' Pick 글 쓰기 시작 — 몇 분 뒤 발행 대기에 떠요` : why(s, d, '시작하지 못했어요 — 잠시 뒤 다시'));
}
// 블로그별 (주인 11/09) — 할 일 탭(app.js)이 블로그마다 part(블로그, 이름, 그 블로그 할 일 카드) 를 붙임: 손일 체크(붙여넣기·발행·예약·이웃) + 카드
const by = () => { try { return W.localStorage.getItem('rt-by') !== 'slot'; } catch (e) { return true; } };
function setBy(v) { try { W.localStorage.setItem('rt-by', v); } catch (e) { /* 이번만 */ } draw(); bump(); }
const bump = () => by() && W.dispatchEvent && W.dispatchEvent(new Event('sb-routine'));
const ORD = {'발행': 0, '예약': 1, '이웃': 2, 'SNS': 3};
const mine = k => data && data.slots ? data.slots.flatMap(s => s.items).filter(i => (i.blog || (i.group === 'SNS' ? 'sns' : '')) === k)
  .sort((a, b) => a.done - b.done || nr(a) - nr(b) || (b.sco + 1 || 0) - (a.sco + 1 || 0) || !!b.paste - !!a.paste || ORD[a.group] - ORD[b.group]) : [];
// 블로그 칸 (주인 11/09 '직관적으로') — 색 점·남은 일·막대 · 펼치면 올리기 / 이웃 글 들르기 / 서로이웃 신청 / 확인할 것 순서로 작은 제목
const DOT = {issue: '#F2B400', finance: '#3B82F6', hotdeal: '#EF4444', deals: '#F97316', sns: '#10B981', shorts: '#A855F7', '': '#94A3B8'};
const SEC = [['올리기', i => i.group === '발행' || i.group === '예약'], ['이웃 글 들르기 (공감·댓글)', i => i.id.startsWith('nb-visit')],
  ['서로이웃 신청', i => i.id.startsWith('nb-add')], ['그 밖', i => true]];
const sh = (t, n) => el('p', {class: 'sg'}, t, n ? el('i', {}, n) : null);
// 칸 안 (주인 11/09 '중요도 순'): 급한 것 → 올리기 → 확인할 것 → 이웃 글 들르기 → 서로이웃 신청 → 그 밖
// 영상 올리기 줄: 밀린 것·오늘·내일 (룩북 칸과 같은 편은 그 카드로) · 체크 = uploads.done
const UG = {past: '밀린 영상', today: '오늘 올릴 영상', tomorrow: '내일 올릴 영상'};
const urows = () => up && up.groups ? up.groups.filter(g => UG[g.key]).flatMap(g => g.items.filter(x => !x.lb && !(g.key === 'past' && x.done)).map(x => Object.assign(x, {g: g.key}))) : [];
async function udone(x) {
  const v = !x.done;
  x.done = v; bump();
  const [st, d] = await send({action: 'uploads.done', args: {id: x.id, done: v}, cid: rid(), ctoken: null});
  if (!(st === 200 && d.status === 'ok')) { x.done = !v; bump(); toast(why(st, d, '체크하지 못했어요 — 잠시 뒤 다시')); }
}
function vid(x) {
  const tg = (x.tags || []).map(t => '#' + t).join(' '), d = [x.desc, tg].filter(Boolean).join('\n\n');
  return el('div', {class: 'it mi' + (x.done ? ' dn' : ''), 'data-id': 'up:' + x.id},
    x.gate ? el('button', {class: 'cb', type: 'button', role: 'checkbox', 'aria-checked': x.done ? 'true' : 'false', 'aria-label': x.title || x.what, onclick: () => udone(x)}, x.done ? '✓' : '') : el('span', {class: 'cb', style: 'opacity:.35'}),
    el('div', {class: 'grow'}, el('span', {class: 't'}, [x.g === 'past' ? x.when_text : x.hm, x.plat, x.what].filter(Boolean).join(' · ')),
      el('span', {class: 's'}, x.gate ? [x.title, x.note].filter(Boolean).join(' · ') : '검수 대기 · 아직 올리지 마세요'),
      x.done ? null : el('div', {class: 'bt'}, x.file_url ? el('a', {class: 'sm', href: x.file_url, target: '_blank', rel: 'noopener noreferrer'}, x.gate ? '파일 받기 ↗' : '영상 보기 ↗') : null,
        x.gate && x.title ? el('button', {class: 'sm', type: 'button', onclick: () => copy(x.title)}, '제목 복사') : null,
        x.gate && d ? el('button', {class: 'sm', type: 'button', onclick: () => copy(d)}, '설명·태그 복사') : null)));
}
// 유튜브 받기 계정 (11/14 쇼츠 총괄) — 숏폼 칸 안 작은 카드. 로그인 풀리면 급함 1 · '다시 로그인' 은 주인이 누를 때만 새 탭
const ytc = () => up && up.yt && up.yt.kind ? up.yt : null;
function ytcard(y) {
  const ok = y.kind === 'ok', lg = y.kind === 'login', hm = y.hm ? ` · ${y.hm}` : '';
  return el('div', {class: 'it mi', 'data-id': 'yt-alive'}, el('span', {class: 'cb', style: 'opacity:.35'}),
    el('div', {class: 'grow'}, el('span', {class: 't'}, '유튜브 받기 계정'),
      el('span', {class: 's' + (lg ? ' er' : '')}, (ok ? '✅ 정상' : lg ? '❗다시 로그인 필요' : '⚠ 서버 문제 — 총괄이 보고 있어요' + (y.why ? ` (${y.why})` : '')) + hm),
      lg && /^https:\/\//.test(y.login_url || '') ? el('div', {class: 'bt'}, el('a', {class: 'sm', href: y.login_url, target: '_blank', rel: 'noopener noreferrer'}, '다시 로그인')) : null));
}
// 이웃 글 들르기 시작 (주인 11/14 '공감·댓글도 책갈피로'): 아직 안 한 첫 글을 새 탭에 — 거기서 책갈피 '이웃 관리'(공감 + 댓글 초안 · 등록은 직접).
// 누를 때마다 한 편 · 책갈피가 들른 글의 줄은 대시보드가 체크 (nbvisit.py) → 돌아오면 다음 글
function nbv(k) {
  const b = data && data.nb && data.nb.by[k];
  return b && b.left ? [b.next ? el('div', {class: 'bt nbv'}, el('a', {class: 'sm ac', href: b.next.url, target: '_blank', rel: 'noopener noreferrer'}, `이웃 글 들르기 시작 (${b.left}) ↗`)) : null,
    el('span', {class: 's'}, "열린 글을 읽고(20초쯤) 책갈피 '이웃 관리' → 공감 + 댓글 초안 · 등록은 직접 · 줄은 저절로 체크")] : null;
}
const TOP = new Set(['issue', 'finance', 'hotdeal']);  // 블로그 셋 — 할 일 맨 위 (주인 11/14)
// 종류마다 남은 일·급한 수 (todohub.js 의 종류 목록이 읽음 — 주인 11/14 '할 일 첫 화면은 종류별 목록만')
const cnt = {};
// 이웃 들르기 한 줄 (주인 11/14) — 책갈피 '이웃 관리'로 한 오늘 수 (routine.view seoi · 체크 없음 · 남은 일에 안 셈)
const seoiRow = (k, sv, v) => el('div', {class: 'it mi', 'data-id': 'seoi:' + k}, el('span', {class: 'cb', style: 'opacity:.35'}),
  el('div', {class: 'grow'}, el('span', {class: 't'}, sv.line), el('span', {class: 's'}, v.how), el('div', {class: 'bt'},
    v.feed ? el('a', {class: 'sm', href: v.feed, target: '_blank', rel: 'noopener noreferrer'}, '이웃새글 열기 ↗') : null, el('a', {class: 'sm', href: '#/connect'}, '책갈피 설치'))));
function part(k, label, extra, hot) {
  hot = hot || [];
  const ur = k === 'shorts' ? urows() : [], ul = ur.filter(x => x.gate && !x.done), yt = k === 'shorts' && by() ? ytc() : null, yl = yt && yt.kind === 'login' ? 1 : 0;
  // 만드는 중인 글은 남은 일로 세지 않음 (주인이 지금 할 수 없는 일 — 11/14) · w = 그 수
  const xs = by() ? mine(k) : [], left = xs.filter(i => !i.done && !nr(i)), w = xs.filter(i => !i.done && nr(i)).length, n = left.length + extra.length + hot.length + ul.length + yl, m = left.reduce((a, i) => a + i.min, 0) + ul.length * 3;
  const hn = hot.length + yl + ur.filter(x => x.g === 'past' && x.gate && !x.done).length, rz = k === 'issue' && by() ? rising() : null;
  cnt[k] = {n, m, u: hn, w};
  const sv = data && data.seoi && data.seoi.by && data.seoi.by[k];
  if (sv) cnt[k].v = sv.visit;
  if (!xs.length && !extra.length && !hot.length && !ur.length && !yt && !rz) return [];
  const o = W.SBHub || (bo[k] == null ? k === 'issue' : bo[k]), bar = el('i'), all = xs.length + extra.length + hot.length + ur.filter(x => x.gate).length + yl || 1, body = [];
  bar.style.width = Math.round(100 * Math.max(0, all - n - w) / all) + '%';
  if (o) {
    const used = new Set(), sec = (t, f, pre) => { const g = xs.filter(i => !used.has(i) && f(i)); g.forEach(i => used.add(i)); const d = g.filter(i => !i.done), q = d.filter(nr).length;
      if (g.length) body.push(sh(t, q ? `준비 ${d.length - q} · 만드는 중 ${q}` : d.length || '끝'), pre ? pre() : null, g.map(item)); };
    if (rz) body.push(rz);  // ⚡ 막 뜨는 중은 데런픽 칸 안에 (11/14)
    if (hot.length) body.push(sh('급한 것', hot.length), hot);
    if (yt) body.push(ytcard(yt));
    Object.keys(UG).forEach(g => { const r = ur.filter(x => x.g === g); if (r.length) body.push(sh(UG[g], r.filter(x => x.gate && !x.done).length || '끝'), r.map(vid)); });
    sec(...SEC[0]);
    if (extra.length) body.push(sh('확인할 것', extra.length), extra);
    SEC.slice(1).forEach((x, i) => sec(x[0], x[1], i ? null : () => nbv(k)));
    if (sv) body.push(sh('이웃 들르기 (책갈피)', sv.visit), seoiRow(k, sv, data.seoi));
  }
  return [el('div', {class: 'box rt bk' + (o ? ' on' : '') + (TOP.has(k) ? ' tpb' : ''), 'data-k': k}, el('button', {class: 'bh', type: 'button', 'aria-expanded': o ? 'true' : 'false',
    onclick: () => { bo[k] = !o; W.dispatchEvent(new Event('sb-routine')); }}, el('span', {class: 'dt', style: `background:${DOT[k]}`}),
    el('span', {class: 'bn2'}, el('b', {}, label, hn ? el('em', {class: 'hu'}, `급함 ${hn}`) : null), el('span', {}, n ? `남은 일 ${n}` + (m ? ` · 약 ${m}분` : '') : w ? `만드는 중 ${w}` : '오늘 끝 ✓')),
    el('span', {class: 'cv'}, o ? '접기' : '열기')), el('div', {class: 'bar'}, bar), body)];
}

function draw() {
  if (!box) return;
  const ok = !!(data && data.slots && data.total), ab = ably(), top = by() ? null : rising(), st = data && data.stats && el('p', {class: 'note'}, '📊 ' + data.stats);
  box.hidden = !ok && !ab.length && !top && !st;
  if (box.hidden) { box.replaceChildren(); return; }
  const bar = el('i');
  if (ok) bar.style.width = Math.round(100 * data.done / data.total) + '%';
  box.replaceChildren(...[top, st, ok ? [...(by() ? [] : pasteBox()),
    el('div', {class: 'top', style: 'margin:4px 0 6px'}, el('h2', {}, by() ? `오늘 손일 ${data.done}/${data.total} 끝냄` : `오늘 할 일 · ${data.done}/${data.total}`),
      el('button', {class: 'lnk', type: 'button', onclick: () => setBy(by() ? 'slot' : 'blog')}, by() ? '시간대별로' : '블로그별로'),
      el('button', {class: 'lnk', type: 'button', onclick: load}, '다시 보기')),
    el('div', {class: 'bar'}, bar),
    data.today ? null : el('p', {class: 'note'}, `${data.day} 목록이에요 — 오늘 목록이 아직 안 왔어요`),
    by() ? null : el('div', {class: 'box'}, data.slots.map(slot)),
    data.overflow ? el('p', {class: 'note', style: 'margin:-6px 0 12px'}, `오늘은 여기까지 — 나머지 ${data.overflow}개는 다음에`) : null] : [], ab].flat().filter(Boolean));
}

function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!on()) { clearTimeout(timer); return; }
  if (!box) {
    if (!D.getElementById('rt-css')) D.head.append(el('style', {id: 'rt-css', text: CSS}));
    box = el('section', {id: 'rtp', class: 'rt', 'aria-label': '오늘 할 일'});
    box.hidden = true;
    draw();
  }
  const h = m.firstChild && m.firstChild.id === 'tdh' ? m.firstChild : null, f = h ? h.nextSibling : m.firstChild,  // 종류 목록(todohub.js) 다음
    u = f && f !== box && f.className === 'bn bad' ? f : h;  // 급한 알림(app.js) 바로 아래
  const tp = [...m.children].filter(c => / tpb/.test(c.className)), a = tp.length ? tp[tp.length - 1] : u;  // 블로그 셋이 맨 위 (11/14) — 오늘 손일 칸은 그 아래
  if (a ? a.nextSibling !== box : f !== box) a ? a.after(box) : m.prepend(box);
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
// todohub.js 가 읽는 값 (11/14): 목록 날짜·오늘 것인지·기록이 쓰인 지 몇 분·자동 점검 한 줄·꾸미기 새 판·못 전한 체크 수 · 받기 전이면 null
const meta = () => data ? {day: data.day, today: !!data.today, age_min: data.age_min, gate: data.gate || null, kkumi_new: !!data.kkumi_new, relay: data.relay || 0, at} : null;
W.SBRoutine = {load, mount, ids, by, part, cnt, meta};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
