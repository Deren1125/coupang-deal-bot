/* Sns-board 제품 테스트 (주인 10/21 — Blog-Auto product_trial.py): Pick 패션 글 '비슷한 아이템' 후보를 주인이 검수.
   글마다 착장 아이템 → 후보 카드(사진·이름·판매처·가격/날짜·열기) ✅ 괜찮아 / ❌ 별로(이유 칩 + 한 줄) · 글 전체 한 줄 · 테스트 끝.
   맨 위 '배운 기준' (Blog-Auto 가 판정으로 만든 기준). 처음 열 때만 app.js 가 불러온다. 보내기 = blog.product_feedback. 라이브러리 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.pc{display:flex;gap:10px;align-items:flex-start}.pc img,.pc .p0{width:88px;height:88px;flex:none;border-radius:8px;object-fit:contain;background:#fff}' +
  '.pc .p0{background:var(--grey)}.pc .pg{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}.pr{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}' +
  '.pr .cp{min-height:36px;padding:0 10px;border:1px solid var(--line);border-radius:18px;background:none;font:inherit;font-size:13px}.pr .cp.on{background:var(--or-soft);border-color:currentColor}';
const V = {}, RS = {}, NT = {}, OP = {};  // 이 화면에서 누른 판정 · 고른 이유 · 적던 한 줄 · 이유 칩 펼침
let H = null;
const won = n => n ? n.toLocaleString('ko-KR') + '원' : '';
const day = s => /^\d{4}-\d\d-\d\d$/.test(s || '') ? ` (${+s.slice(5, 7)}/${+s.slice(8)} 기준)` : '';

function css() {
  if (D.getElementById('pt-css')) return;
  const s = D.createElement('style'); s.id = 'pt-css'; s.textContent = CSS; D.head.append(s);
}
function line(k, ph) {
  return H.el('input', {class: 'fl', maxlength: 200, placeholder: ph, value: NT[k] || '', autocomplete: 'off', oninput: e => { NT[k] = e.target.value; }});
}
async function send(x, v, e) {
  const extra = {verdict: v}, n = (NT[x.id] || '').trim(), r = [...(RS[x.id] || [])];
  if (v === 'bad' && r.length) extra.reasons = r;
  if (n) extra.note = n;
  if (await H.exec(x.act, extra, {btn: e.currentTarget})) { V[x.id] = v; OP[x.id] = false; delete NT[x.id]; delete RS[x.id]; H.render(); }
}
async function whole(p, v, f, e) {
  const n = f.value.trim();
  if (v === 'note' && !n) { f.focus(); H.toast('한 줄을 적어 주세요'); return; }
  const extra = {verdict: v};
  if (n) extra.note = n;
  if (await H.exec(p.act, extra, {btn: e.currentTarget})) { delete NT['p' + p.pid]; H.render(); }
}
function bad(t, x) {
  const {el, btn} = H, r = RS[x.id] = RS[x.id] || new Set();
  return el('div', {}, el('div', {class: 'pr', role: 'group', 'aria-label': '별로인 이유'}, t.reasons.map(w => btn('cp' + (r.has(w) ? ' on' : ''), w, e => {
    r.has(w) ? r.delete(w) : r.add(w); e.currentTarget.classList.toggle('on', r.has(w)); e.currentTarget.setAttribute('aria-pressed', r.has(w) ? 'true' : 'false');
  }, {'aria-pressed': r.has(w) ? 'true' : 'false'}))), line(x.id, '한 줄 (선택) 예: 셀럽 코트보다 너무 반짝여요'),
  el('div', {style: 'margin-top:8px'}, btn('pb', '별로 보내기', e => send(x, 'bad', e))));
}
function card(t, p, x) {
  const {el, btn, ext} = H, v = V[x.id] || x.verdict, pc = p.pieces[x.piece] || {};
  return el('div', {class: 'box', style: 'margin-top:8px'}, el('div', {class: 'pc'}, x.img ? el('img', {src: x.img, alt: x.name, loading: 'lazy'}) : el('span', {class: 'p0'}),
    el('div', {class: 'pg'}, el('span', {class: 'vn'}, [pc.part, pc.desc].filter(Boolean).join(' · ')), el('span', {class: 'vt'}, x.name),
      el('span', {class: 's'}, [x.shop, won(x.price) + day(x.at)].filter(Boolean).join(' · ')),
      v ? el('span', {class: 's'}, v === 'ok' ? '✅ 괜찮아로 보냄' : '❌ 별로로 보냄') : null)),
    el('div', {class: 'va'}, ext('sm', '열기', x.link), btn('sm' + (v === 'ok' ? ' ac' : ''), '✅ 괜찮아', e => send(x, 'ok', e)),
      btn('sm' + (v === 'bad' || OP[x.id] ? ' ac' : ''), '❌ 별로', () => { OP[x.id] = !OP[x.id]; H.render(); }, {'aria-expanded': OP[x.id] ? 'true' : 'false'})),
    OP[x.id] ? bad(t, x) : null);
}
function view(t, h) {
  H = h; css();
  const {el, btn} = h;
  if (!t || !t.posts.length) return [el('p', {class: 'empty'}, '테스트 중인 글이 없어요')];
  const out = [el('div', {class: 'top hs'}, el('p', {class: 'h3'}, '배운 기준'), el('span', {class: 'note'}, `괜찮아 ${t.ok}/${t.goal}`)),
    el('div', {class: 'box'}, t.learned.length ? t.learned.map(s => el('p', {class: 'note', text: '· ' + s})) : el('p', {class: 'note'}, '아직 배운 기준이 없어요'))];
  for (const p of t.posts) {
    out.push(el('p', {class: 'h3', style: 'margin-top:20px'}, p.title),
      el('p', {class: 'note'}, p.pieces.map(c => `${c.part} · ${c.desc}`).join('  /  ')), ...p.items.map(x => card(t, p, x)));
    const f = line('p' + p.pid, '고르는 방식 한 줄 (예: 같은 브랜드 공식몰 우선)');
    out.push(el('div', {class: 'box', style: 'margin-top:8px'}, f, el('div', {class: 'two', style: 'margin-top:8px'},
      btn('pb soft', '한 줄 보내기', e => whole(p, 'note', f, e)), btn('pb', '테스트 끝', e => whole(p, 'done', f, e)))));
  }
  return out;
}
W.SBProduct = {view};
})();
