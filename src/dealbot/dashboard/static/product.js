/* Sns-board 제품 테스트 (주인 10/21 — Blog-Auto product_trial.py): Pick 패션 글 '비슷한 아이템' 후보를 주인이 검수.
   글마다 착장 아이템 → 후보 카드(사진·이름·판매처·가격/날짜·열기) ✅ 괜찮아 / ❌ 별로(이유 칩 + 한 줄) · 글 전체 한 줄 · 테스트 끝.
   맨 위 '배운 기준' (Blog-Auto 가 판정으로 만든 기준). 처음 열 때만 app.js 가 불러온다. 보내기 = blog.product_feedback. 라이브러리 없음.
   사진 테스트 화면(photo — 주인 10/21): 글 제목 + 사진 한눈에 줄 → 자리마다 'N번째 사진 · 위치' · 소제목 · 앞 문장 "…" · 큰 사진 · 고른 이유 ·
   ✅/❌ (이 화면도 처음 열 때만 받는 이 파일에 둔다 — edit.js 크기 한도). 보내기 = blog.photo_feedback. */
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
    el('div', {class: 'pg'}, el('span', {class: 'vn'}, pc.desc ? `글 속 '${pc.desc}' 대신` : pc.part || ''), el('span', {class: 'vt'}, x.name),
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
      el('p', {class: 'note'}, '글 속 옷: ' + p.pieces.map(c => `${c.part} ${c.desc}`).join(' · ')), ...p.items.map(x => card(t, p, x)));
    const f = line('p' + p.pid, '고르는 방식 한 줄 (예: 같은 브랜드 공식몰 우선)');
    out.push(el('div', {class: 'box', style: 'margin-top:8px'}, f, el('div', {class: 'two', style: 'margin-top:8px'},
      btn('pb soft', '한 줄 보내기', e => whole(p, 'note', f, e)), btn('pb', '테스트 끝', e => whole(p, 'done', f, e)))));
  }
  return out;
}
// ------------------------------------------------------------ 사진 테스트 (주인 10/21)
const RK = {margin_left_over: '여백 남음', ui_remnant: 'UI 남음', subject_cut: '얼굴/몸 잘림', too_wide: '너무 넓음', too_tight: '너무 좁음',
  wrong_person: '다른 사람', low_quality_blur: '흐림', duplicate: '중복', text_overlay: '글자 많음', broadcast_frame: '방송 캡처',
  bad_aspect: '비율 이상', dark: '어두움', other: '기타'};
const PK = {placement: '사진 배치', spread: '사진 분포', count: '사진 개수', fit: '글과 안 맞음', other: '기타'};
const QK = Object.assign({border: '테두리', ui: '화면 띠', lowres: '해상도', text: '글자', broadcast: '방송 캡처', missing: '파일 없음'}, RK);
const TS = {};  // 고른 이유·메모 (다시 그려도 남게) — 키 '<pid>-<slot|all>'
function photo(t, h) {
  H = h; css();
  const {el, btn} = h;
  const fb = (p, k, s) => {  // 이유 칩 + 메모 + 보내기
    const st = TS[k] = TS[k] || {r: [], n: ''}, keys = s ? RK : PK;
    const note = el('input', {class: 'fl', placeholder: '한 줄 메모 (선택)', value: st.n, oninput: e => { st.n = e.target.value; }});
    return el('div', {class: 'box'}, el('div', {class: 'ea'}, Object.keys(keys).map(r => btn('cp' + (st.r.includes(r) ? ' hot' : ' info'), keys[r], () => {
      st.r = st.r.includes(r) ? st.r.filter(x => x !== r) : st.r.concat(r); h.render(); }, {'aria-pressed': st.r.includes(r) ? 'true' : 'false'}))), note,
    btn('pb', '보내기', e => go(p, s, 'bad', st, e.currentTarget, k)));
  };
  async function go(p, s, v, st, b, k) {
    if (v === 'bad' && !st.r.length && !st.n.trim()) { h.toast('이유를 하나 골라 주세요'); return; }
    const x = Object.assign({verdict: v, reasons: v === 'bad' ? st.r : []}, s ? {slot: s.slot} : {}, st.n.trim() && v === 'bad' ? {note: st.n.trim()} : {});
    if (await h.exec(p.fb, x, {btn: b, iid: p.iid})) { delete TS[k]; h.render(); }
  }
  const out = [];
  if (t.rules.length) out.push(el('p', {class: 'h3'}, '배운 원칙'), el('div', {class: 'box'}, t.rules.map(r => el('p', {class: 'note'}, '· ' + r.text))));
  for (const p of t.posts) {
    out.push(el('p', {class: 'h3', style: 'margin-top:20px;font-size:15px'}, p.title), el('p', {class: 'note'}, `사진 ${p.slots.filter(s => s.final || s.full).length}/${p.slots.length}장 · 피드백 ${p.feedback_done}개 · 사진을 누르면 편집`),
      el('div', {class: 'sv', 'aria-label': '글 순서대로 사진'}, p.slots.map(s => s.final || s.full ? el('img', {src: s.final || s.full, alt: `${s.slot}번째 사진`}) : el('i', {title: `${s.slot}번째 빈 자리`}))));
    p.slots.forEach((s, i) => {
      const k = p.pid + '-' + s.slot, open = TS[k] && TS[k].o, pic = s.final || s.full;
      out.push(el('div', {class: 'box', style: 'margin-top:8px'}, el('p', {}, el('b', {}, `${i + 1}번째 사진${s.where ? ' · ' + s.where : ''}`), s.section ? ' · ' + s.section : ''),
        s.ctx ? el('p', {class: 'cx note'}, `“${s.ctx}”`) : null,
        pic ? el('div', {class: 'ck', onclick: () => h.edPhoto(p, p.edit[i])}, el('img', {src: pic, alt: s.desc})) : el('p', {class: 'note'}, '빈 자리'),
        el('p', {class: 'note'}, [s.why ? '고른 이유: ' + s.why : '', s.desc ? '자리 설명: ' + s.desc : '', s.credit ? '출처 ' + s.credit : ''].filter(Boolean).join(' · ')),
        s.qa.issues.length ? el('div', {class: 'ea'}, s.qa.issues.map(q => el('span', {class: 'cp stop'}, QK[q] || q))) : null,
        pic ? el('div', {class: 'two'}, btn('pb soft', '✅ 괜찮아', e => go(p, s, 'ok', {r: [], n: ''}, e.currentTarget, k)),
          btn('pb soft', '❌ 별로', () => { TS[k] = Object.assign(TS[k] || {r: [], n: ''}, {o: !open}); h.render(); })) : null,
        open ? fb(p, k, s) : null));
    });
    const k = p.pid + '-all';
    out.push(el('p', {class: 'h3'}, '글 전체 (배치·분포·개수)'), fb(p, k, null),
      el('div', {class: 'two'}, btn('pb soft', '✅ 괜찮아', e => go(p, null, 'ok', {r: [], n: ''}, e.currentTarget, k)),
        btn('pb soft', p.done.label, e => h.exec(p.done, {}, {btn: e.currentTarget, iid: p.iid}))));
  }
  return out;
}
W.SBProduct = {view, photo};
})();
