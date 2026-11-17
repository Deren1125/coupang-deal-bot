/* X 커뮤 캡처 글 (주인 11/17 — 공개 커뮤니티에서 반응 터진 글 본문 캡처 1~2장 + 내 말투 한 줄). 셸이 defer 로 받음 — xcap.py.
   SNS 탭 X 칸 '지금 뜨는 주제' 카드 아래 카드 하나: 오늘 카드마다 캡처 그림 · [사진 n 저장] · [한 줄 복사] · [원글] · [안 써요].
   올리기는 내가 X 앱에서 (서버는 X 에 안 올림) — ✓ 는 '오늘 X 글' 체크리스트의 '커뮤 캡처' 줄에서. 이 파일에 바깥 주소·innerHTML 없음. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xcap/';
const CSS = '.xcp .cap{display:block;width:100%;height:auto;border-radius:8px;margin:6px 0;border:1px solid var(--line,#ddd)}' +
  '.xcp .ln1{font-size:15px;font-weight:600;margin:6px 0;overflow-wrap:anywhere}.xcp .an{font-size:12px;color:var(--sub);margin:0 0 4px}';
const NW = {target: '_blank', rel: 'noopener noreferrer'};
let ST = null, at = 0, loading = false, card = null, tt = null, poll = null, polls = 0, busy = false;

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v; else if (n.startsWith('on')) e.addEventListener(n.slice(2), v);
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
  const ok = () => toast('복사했어요 — 사진과 같이 X 에 올린 뒤 체크리스트에서 ✓');
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
  toast(r.s === 200 ? '고르는 중이에요 — 1~3분 걸려요' : r.d.text || '만들지 못했어요');
  draw(); watch();
}
async function skip(it) {
  const r = await call('skip', {id: it.id});
  take(r.d); if (r.s !== 200) toast(r.d.text || '다시 눌러 주세요');
  draw();
}
const n = v => (v >= 1e4 ? (v / 1e4).toFixed(1).replace(/\.0$/, '') + '만' : String(v || 0));
function item(it) {
  return el('section', {class: 'itm'},
    el('div', {class: 'ih'}, el('span', {class: 'cp info'}, it.site), el('span', {class: 'cp'}, it.at),
      el('span', {class: 'n'}, `댓글 ${n(it.react.comments)} · 추천 ${n(it.react.rec)}`)),
    el('p', {class: 'ln1'}, it.line), it.angle ? el('p', {class: 'an'}, `왜 터졌나 · ${it.angle}`) : null,
    it.imgs.map((src, i) => el('img', {class: 'cap', src, alt: `캡처 ${i + 1} — ${it.title}`, loading: 'lazy'})),
    it.blur != null ? el('p', {class: 'an'}, `가린 곳 ${it.blur}곳 (닉네임·프로필·전화번호·이메일·@아이디 모자이크) — 올리기 전에 남은 닉네임이 없는지 한 번 봐 주세요`) : null,
    el('div', {class: 'rw'}, it.imgs.map((src, i) => el('a', {class: 'pb soft', href: src, download: `${it.site}-${i + 1}.jpg`}, `사진 ${i + 1} 저장`)),
      btn('pb soft', '한 줄 복사', () => copy(it.line)), it.url ? el('a', Object.assign({class: 'lnk', href: it.url}, NW), '원글 ›') : null,
      btn('lnk', '안 써요', () => skip(it))));
}
function draw() {
  if (!card) return;
  if (!ST) return card.replaceChildren(el('p', {class: 'note'}, '커뮤 캡처 글 불러오는 중…'));
  const its = ST.items || [];
  card.replaceChildren(...[
    el('div', {class: 'ih'}, el('b', {class: 'ti'}, `커뮤 캡처 글 · 오늘 ${ST.made}/${ST.per_day}`),
      btn('lnk', busy || ST.busy ? '고르는 중…' : '하나 더 만들기', make, {disabled: !!(busy || ST.busy || !ST.on) || null})),
    its.length ? its.map(item) : el('p', {class: 'note'}, !ST.on ? '꺼져 있어요' : ST.busy ? '반응 큰 글을 고르는 중이에요 — 1~3분' : ST.next ? `${ST.next} 쯤 첫 카드가 생겨요` : '오늘은 아직 없어요'),
    ST.err ? el('p', {class: 'w'}, ST.err) : null,
    ST.off.length ? el('p', {class: 'note'}, `${ST.off.join('·')} 은 막혀서 쉬는 중 (7일 · 우회 안 함)`) : null,
    el('p', {class: 'note'}, `출처: ${ST.sources.join('·')} (로그인 없는 공개 글 · 글쓴이 칸은 안 찍고 닉네임·프로필은 모자이크) · 사진 저장 → X 앱에서 직접 올리고 ✓ 는 오늘 X 체크리스트에서`),
  ].flat(9).filter(Boolean));
}
function onX() {
  const m = D.getElementById('main'), s = D.getElementById('snt'), b = s && s.children[1];
  return !!m && (m.dataset || {}).tab === 'sns' && !!b && b.getAttribute('aria-pressed') === 'true' && !/^#\/?sns\/x[sertc]/.test(location.hash);
}
function mount() {
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('xcp-css')) D.head.append(el('style', {id: 'xcp-css', text: CSS}));
  if (onX()) {
    if (!card) { card = el('article', {id: 'xcc', class: 'itm xsc xcp', 'aria-label': '커뮤 캡처 글'}); draw(); }
    const a = D.getElementById('xrc') || D.getElementById('xec') || D.getElementById('xtc') || D.getElementById('snt');  // 지금 뜨는 주제 카드 아래
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
W.SBXCap = {load, mount, state: () => ST};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
