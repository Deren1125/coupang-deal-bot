/* X 어그로 후보 (주인 11/17 — 막 반응이 붙는 커뮤니티 글을 캡처(가림) + X 에 올릴 초안으로). 셸이 defer 로 받음 — xaggro.py.
   SNS 탭 X 칸 '커뮤 캡처 글' 카드 아래 카드 하나: 후보마다 캡처 그림 · [사진 n 저장] · 초안 · [글 복사] · '왜 뜰까' · [✓ 올렸어요] · [원글] · [안 써요].
   올리기는 내가 X 앱에서 (서버는 X 에 안 올림 · X 로그인 없음) — ✓ 는 오늘 X 체크리스트에 한 줄로 (10분 안 취소).
   가림은 어림이라 카드마다 '올리기 전에 확인' 을 늘 보여 준다. 이 파일에 바깥 주소·innerHTML 없음. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xaggro/';
const CSS = '.xag .cap{display:block;width:100%;height:auto;border-radius:8px;margin:6px 0;border:1px solid var(--line,#ddd)}' +
  '.xag .dr{font-size:15px;margin:6px 0;white-space:pre-wrap;overflow-wrap:anywhere;padding:8px;border-radius:8px;border:1px solid var(--line,#ddd)}' +
  '.xag .an{font-size:12px;color:var(--sub);margin:0 0 4px}.xag .chk{font-size:12px;margin:6px 0;padding:6px 8px;border-radius:8px;border:1px dashed var(--line,#ddd)}' +
  '.xag .dn{opacity:.6}.xag .ck{display:inline-flex;align-items:center;gap:4px;font-size:13px}';
const NW = {target: '_blank', rel: 'noopener noreferrer'};
const CHECK = '올리기 전에 확인 — 가림은 어림이에요. ① 사진에 닉네임·프로필·IP·전화번호·이메일·얼굴·차 번호가 남아 있지 않은지 ② 일반인 사연·특정 가게나 사람을 지목한 글이 아닌지 ③ 글 내용이 사실과 다르게 줄여지지 않았는지 보고 올려 주세요. 걸리면 [안 써요].';
let ST = null, at = 0, loading = false, card = null, tt = null, poll = null, polls = 0, busy = false, chk = '';

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v; else if (n === 'checked') e.checked = !!v;
    else if (n.startsWith('on')) e.addEventListener(n.slice(2), v);
    else e.setAttribute(n, v === true ? '' : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const btn = (cls, label, f, x) => el('button', Object.assign({class: cls, type: 'button', onclick: f}, x), label);
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text})); t.hidden = false;
  clearTimeout(tt); tt = setTimeout(() => { t.hidden = true; }, 3500);
}
function csrf() { try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; } }
async function call(path, body) {
  try {
    const r = await fetch(API + path, body ? {method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()}, body: JSON.stringify(body)} : {credentials: 'same-origin', cache: 'no-store'});
    let d = {};
    try { d = await r.json(); } catch (e) { /* 빈 몸 */ }
    if (r.status === 403 && d.code === 'csrf') d.text = '화면이 오래됐어요 — 새로고침한 뒤 다시 눌러 주세요';
    return {s: r.status, d};
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요'}}; }
}
function copy(text) {
  const ok = () => toast('글을 복사했어요 — 사진과 같이 X 에 올린 뒤 ✓ 올렸어요');
  const c = W.navigator && W.navigator.clipboard;
  if (c && c.writeText) c.writeText(text).then(ok, () => toast('복사하지 못했어요 — 글을 길게 눌러 복사해 주세요')); else toast('글을 길게 눌러 복사해 주세요');
}
const take = d => { if (d && d.v && Array.isArray(d.items)) { ST = d; at = Date.now(); } };
async function load() {
  if (loading) return;
  loading = true;
  const r = await call('state');
  loading = false; at = Date.now();
  if (r.s === 200) take(r.d);
  draw(); watch();
}
function watch() { clearTimeout(poll); if (ST && ST.busy && polls < 40) poll = setTimeout(() => { polls++; load(); }, 6000); }
async function make() {
  if (busy) return;
  busy = true; draw();
  const r = await call('make', {});
  busy = false; polls = 0;
  take(r.d);
  toast(r.s === 200 ? '고르는 중이에요 — 2~5분 걸려요' : r.d.text || '고르지 못했어요');
  draw(); watch();
}
async function skip(it) {
  const r = await call('skip', {id: it.id});
  take(r.d); if (r.s !== 200) toast(r.d.text || '다시 눌러 주세요');
  draw();
}
async function posted(it, ev) {
  const b = ev.target, undo = !b.checked;
  if (chk) { b.checked = undo; return; }
  if (undo && !it.undo_s) { b.checked = true; return toast('올린 지 10분이 지나 취소할 수 없어요'); }
  chk = it.id; b.disabled = true;
  const r = await call('posted', {id: it.id, op: undo ? 'undo' : 'add'});
  chk = '';
  if (r.s === 200) { take(r.d); toast(undo ? '기록을 지웠어요' : '올렸어요 — 오늘 X 체크리스트에 넣었어요'); if (W.SBXToday && W.SBXToday.load) W.SBXToday.load(); }
  else { b.checked = undo; toast(r.d.text || '기록하지 못했어요 — 다시 눌러 주세요'); }
  draw();
}
const n = v => (v >= 1e4 ? (v / 1e4).toFixed(1).replace(/\.0$/, '') + '만' : String(v || 0));
function item(it) {
  return el('section', {class: 'itm' + (it.done ? ' dn' : '')},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, it.site), el('span', {class: 'cp'}, it.at),
      it.news ? el('span', {class: 'cp hot'}, '기사') : el('span', {class: 'cp hot'}, `속도 ${n(Math.round(it.score))}점`),
      it.news ? null : el('span', {class: 'n'}, `댓글 ${n(it.react.comments)} · 추천 ${n(it.react.rec)} · 조회 ${n(it.react.views)}`)),
    el('p', {class: 'an'}, `왜 뜰까 · ${[it.why, it.angle].filter(Boolean).join(' — ')}`),
    it.imgs.map((src, i) => el('img', {class: 'cap', src, alt: `캡처 ${i + 1} — ${it.title}`, loading: 'lazy'})),
    it.news ? el('p', {class: 'an'}, '그림은 초안 글로 만든 글자 그림 (기사·판 글은 찍지 않음) · 출처는 그 기사') : el('p', {class: 'an'}, `가린 곳 ${it.blur}곳 (닉네임·프로필·IP·전화번호·이메일 모자이크)`),
    el('div', {class: 'rw'}, it.imgs.map((src, i) => el('a', {class: 'pb soft', href: src, download: `${it.site}-${it.id}-${i + 1}.jpg`}, it.imgs.length > 1 ? `사진 ${i + 1} 저장` : '사진 저장'))),
    el('p', {class: 'dr'}, it.text),
    el('p', {class: 'chk'}, CHECK),
    el('div', {class: 'rw'}, btn('pb soft', '글 복사', () => copy(it.text)),
      el('label', {class: 'ck'}, el('input', {type: 'checkbox', checked: !!it.done, disabled: chk === it.id || null, 'aria-label': `${it.title} 올렸어요`, onchange: e => posted(it, e)}), '✓ 올렸어요'),
      it.url ? el('a', Object.assign({class: 'lnk', href: it.url}, NW), it.news ? '기사 ›' : '원글 ›') : null,
      it.done ? null : btn('lnk', '안 써요', () => skip(it))));
}
function draw() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, '어그로 후보 불러오는 중…'));
  const its = ST.items || [];
  card.replaceChildren(...[
    el('div', {class: 'ih'}, el('b', {class: 'ti'}, `어그로 후보 · 오늘 ${ST.made}/${ST.per_day}`),
      btn('lnk', busy || ST.busy ? '고르는 중…' : '지금 고르기', make, {disabled: !!(busy || ST.busy || !ST.on || ST.hold || ST.made >= ST.per_day) || null})),
    el('p', {class: 'note'}, '막 반응이 붙는 글(시간당 댓글·추천·조회)을 골라 캡처 + 초안으로 — 사진 저장 → 글 복사 → X 앱에서 직접 올리고 ✓'),
    its.length ? its.map(item) : el('p', {class: 'note'}, !ST.on ? '꺼져 있어요' : ST.busy ? '빨리 뜨는 글을 고르는 중이에요 — 2~5분' : ST.next ? `${ST.next} 쯤 고르기 시작해요` : '오늘은 아직 없어요'),
    ST.hold ? el('p', {class: 'w'}, ST.hold) : null,
    ST.err ? el('p', {class: 'w'}, ST.err) : null,
    ST.off.length ? el('p', {class: 'note'}, `${ST.off.join('·')} 은 막혀서 쉬는 중 (7일 · 우회 안 함)`) : null,
    el('p', {class: 'note'}, `출처: ${ST.sources.join('·')} (로그인 없는 공개 글 · 본문 칸만 찍고 닉네임·프로필은 모자이크) · 서버는 X 에 올리지 않아요`),
    el('p', {class: 'note'}, '네이트판은 robots.txt 가 막아서 판 글을 열지 않아요 — 폰에서 본 판 글은 캡처해서 ',
      el('a', {class: 'lnk', href: '#/sns/xr/w'}, '사진으로 글 쓰기'), ' 에 올리면 가림 + 초안 (판 화제 기사는 위 카드로)'),
  ].flat(9).filter(Boolean));
}
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true' && !/^#\/?sns\/x[sertc]/.test(location.hash);
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xag-css')) D.head.append(el('style', {id: 'xag-css', text: CSS}));
  if (onX()) {
    if (!card) { card = el('article', {id: 'xag', class: 'itm xsc xag', 'aria-label': '어그로 후보'}); draw(); }
    // 커뮤 캡처 글 카드(xcc) 아래 — xcap.js 가 같은 닻(xrc…) 바로 뒤에 제 카드를 다시 끼우므로, 그 카드가 있으면 그 뒤에 붙어야 서로 밀어내지 않는다
    const a = D.getElementById('xcc') || D.getElementById('xrc') || D.getElementById('xec') || D.getElementById('xtc') || D.getElementById('snt');
    if (a && a.nextSibling !== card) a.after(card);
    if (!ST || Date.now() - at > 120000) load();
  } else if (card && card.parentNode) card.remove();
}
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(mount).observe(m, {childList: true});
  W.addEventListener('hashchange', mount);
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && onX()) load(); });
  mount();
}
W.SBXAggro = {load, mount, state: () => ST};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
