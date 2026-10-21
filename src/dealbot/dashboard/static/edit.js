/* Sns-board 사진 고치기 (주인 10/19·10/20): 사진 자리 한 번에 고치기 + 편집기 (자르기·비율·돌리기·밝기·사람만 남기기) +
   대시보드 사진을 누르면 바로 편집 → 저장. 처음 쓸 때만 app.js 가 불러온다.
   바꾼 것은 이 화면에만 두고 '수정 완료'에서 한 번에 보낸다 (blog.photos_batch — 사진 올리기 → 일 하나). 라이브러리 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.eb{display:flex;gap:10px;align-items:center;padding:8px 4px;border-top:1px solid var(--line);border-radius:8px}' +
  '.eb.chg{background:var(--or-soft)}.eb.drag{opacity:.4}.eb img,.eb .e0{width:64px;height:64px;border-radius:8px;object-fit:cover;flex:none;background:var(--grey)}' +
  '.eb .e0{border:1.5px dashed var(--fline);background:none}.eb .et{flex:1;min-width:0}.eb .et p{margin:0;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
  '.ea{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}.ef{position:sticky;bottom:0;background:var(--card);padding:10px 0 4px;display:flex;gap:8px;align-items:center}' +
  '.ef .pb{flex:1 1 auto}.ecv{width:100%;display:block;touch-action:none;border-radius:8px;background:#000}.er{width:100%;min-height:44px}';
let H = null;
function style() { if (!D.getElementById('edcss')) D.head.append(Object.assign(D.createElement('style'), {id: 'edcss', textContent: CSS})); }
// put 하나: 편집한 사진 = edited · '사람만 남기기' = focus · 이 글 자리 N 사진을 고친 것 = retouch N (출처를 이어받음)
const op = (slot, c) => Object.assign({op: 'put', slot, upload: null, credit: '', edited: !!c.edited}, c.f.focus ? {focus: true} : {}, c.re ? {retouch: c.re} : {});
async function load(u) {
  try { const r = await fetch(u, {credentials: 'same-origin'}); if (r.ok) return await r.blob(); } catch (x) { /* 아래 */ }
  H.toast('사진을 불러오지 못했어요'); return null;
}

// ------------------------------------------------------------ 사진 자리 한 번에 고치기
// 자리마다 지금 든 것: {o: 원래 자리 번호, src} (원래 사진) · {f: 파일, src, edited} (새 사진) · null (빈 자리)
function batch(h) {
  H = h; style();
  const {el, btn} = h, rows = h.slots.map(s => ({slot: s.slot, label: s.label || '', full: s.full || s.thumb, cur: s.thumb ? {o: s.slot, src: s.thumb} : null, was: !!s.thumb}));
  const urls = [], list = el('div'), foot = el('div', {class: 'ef'});
  const same = r => r.cur ? r.cur.o === r.slot : !r.was;
  const changed = () => rows.filter(r => !same(r)).length;
  let busy = false, dragAt = null;
  const box = el('div', {}, el('p', {class: 'note'}, '바꾼 것은 여기에만 두었다가 수정 완료를 누르면 한 번에 블로그에 넣어요.'), list, foot);
  h.sheet('사진 한 번에 고치기', box, () => urls.forEach(u => URL.revokeObjectURL(u)));
  const swap = (i, j) => { const t = rows[i].cur; rows[i].cur = rows[j].cur; rows[j].cur = t; draw(); };
  const put = (r, f, edited) => { const src = URL.createObjectURL(f); urls.push(src); r.cur = {f, src, edited}; draw(); };
  const choose = r => h.pick('image/*', false, async fs => {
    if (!fs.length) return;
    const out = await editor(fs[0]);
    put(r, out || fs[0], !!out);
  });
  const edit = async r => { const c = r.cur, f = c.f || await load(rows.find(x => x.slot === c.o).full); if (!f) return;
    const out = await editor(f); if (out) { put(r, out, true); r.cur.re = c.f ? c.re : c.o; } };
  function draw() {
    list.replaceChildren(...rows.map((r, i) => {
      const c = r.cur, row = el('div', {class: 'eb' + (same(r) ? '' : ' chg'), draggable: 'true'},
        c ? el('img', {src: c.src, alt: `자리 ${r.slot}`}) : el('span', {class: 'e0'}),
        el('div', {class: 'et'}, el('p', {}, el('b', {}, `자리 ${r.slot}`), r.label ? ' · ' + r.label : ''),
          el('p', {class: 'note'}, !c ? '빈 자리' : c.f ? (c.edited ? '새 사진 · 편집함' : '새 사진') : c.o !== r.slot ? `자리 ${c.o}에서 옮김` : '지금 사진'),
          el('div', {class: 'ea'}, c ? btn('sm', '✕ 빼기', () => { r.cur = null; draw(); }) : null,
            btn('sm', c ? '교체' : '사진 추가', () => choose(r)), c ? btn('sm', '편집', () => edit(r)) : null,
            btn('sm', '↑', () => swap(i, i - 1), {disabled: i === 0 || null, 'aria-label': '위 자리로'}),
            btn('sm', '↓', () => swap(i, i + 1), {disabled: i === rows.length - 1 || null, 'aria-label': '아래 자리로'}))));
      row.addEventListener('dragstart', e => { dragAt = i; row.classList.add('drag'); e.dataTransfer.effectAllowed = 'move'; });
      row.addEventListener('dragend', () => row.classList.remove('drag'));
      row.addEventListener('dragover', e => e.preventDefault());
      row.addEventListener('drop', e => { e.preventDefault(); if (dragAt != null && dragAt !== i) swap(dragAt, i); dragAt = null; });
      return row;
    }));
    const n = changed();
    foot.replaceChildren(el('span', {class: 'cp ' + (n ? 'hot' : 'info')}, n ? `바꿀 것 ${n}개` : '바꾼 것 없음'),
      btn('lnk mute', '취소', () => h.back()), btn('pb', '수정 완료', e => send(e.currentTarget), {disabled: !n || busy || null}));
  }
  // 지금 모양 → ops: 옮기기(맞바꾸기) 먼저 · 새 사진 · 빼기 (Blog-Auto photo_batch 가 같은 순서로 한다)
  function ops() {
    const at = {}, out = [], files = [];
    for (const r of rows) at[r.slot] = r.was ? r.slot : null;
    for (const r of rows) {
      if (!r.cur || r.cur.f) continue;
      const p = rows.find(x => at[x.slot] === r.cur.o).slot;
      if (p !== r.slot) { out.push({op: 'move', from: p, to: r.slot}); const t = at[p]; at[p] = at[r.slot]; at[r.slot] = t; }
    }
    for (const r of rows) if (r.cur && r.cur.f) { out.push(op(r.slot, r.cur)); files.push(r.cur.f); }
    for (const r of rows) if (!r.cur && at[r.slot] != null) out.push({op: 'remove', slot: r.slot});
    return {out, files};
  }
  async function send(b) {
    const {out, files} = ops();
    busy = true; b.disabled = true;
    try {
      const puts = out.filter(o => o.op === 'put');
      if (files.length) {
        b.textContent = `사진 올리는 중 0/${files.length}`;
        const ids = await h.uploadAll(files, 'photo', (i, x, done) => { if (done) b.textContent = `사진 올리는 중 ${i + 1}/${files.length}`; });
        puts.forEach((o, i) => { o.upload = ids[i]; });
      }
      const r = await h.exec({action: 'blog.photos_batch', args: h.args}, {ops: out}, {iid: h.iid});
      if (r) { h.back(); return; }
    } catch (x) { h.toast(x.message || '하지 못했어요 — 잠시 뒤 다시'); }
    busy = false; draw();
  }
  draw();
}

// ------------------------------------------------------------ 편집기: 자르기(손잡이·비율) · 90° 돌리기 · 밝기 · 사람만 남기기 → JPEG (긴 변 2000px, 0.9)
// 비율 -1 = 원본 비율 (돌린 뒤 사진 모양). 사람만 남기기 = 저장할 때 서버가 주인공 둘레만 (Blog-Auto media.focus_subject · focus)
const RATIOS = [['자유', 0], ['원본', -1], ['1:1', 1], ['4:5', 0.8], ['16:9', 16 / 9]];
async function bitmap(f) {
  try { return await createImageBitmap(f, {imageOrientation: 'from-image'}); } catch (x) { /* 아래 */ }
  const u = URL.createObjectURL(f), im = new Image();
  try { im.src = u; await im.decode(); return im; } finally { URL.revokeObjectURL(u); }
}
function editor(file, o = {}) {
  const {el, btn} = H;
  style();
  return new Promise(async done => {
    let src;
    try { src = await bitmap(file); } catch (x) { H.toast('이 사진은 열 수 없어요 — 다른 사진으로'); done(null); return; }
    let rot = 0, ratio = 0, light = 100, c = null, drag = null, out = null, focus = false;
    const cv = el('canvas', {class: 'ecv'}), g = cv.getContext('2d');
    const dims = () => rot % 2 ? [src.height, src.width] : [src.width, src.height];
    const rt = () => ratio < 0 ? dims()[0] / dims()[1] : ratio;
    const fb = btn('sm', '사람만 남기기', e => { focus = !focus; e.currentTarget.setAttribute('aria-pressed', String(focus)); fn.hidden = !focus; }, {'aria-pressed': 'false'});
    const fn = el('p', {class: 'note', hidden: true}, '저장하면 서버가 사람(주인공) 둘레만 남겨요 — 확실할 때만 자르고, 애매하면 그대로');
    const rb = btn('sm', '↻ 90° 돌리기', () => { rot = (rot + 1) % 4; fit(); });
    const chips = RATIOS.map(([t, v]) => btn('sm', t, () => { ratio = v; fit(); }, {'aria-pressed': String(v === ratio)}));
    const sl = el('input', {type: 'range', min: '50', max: '150', value: '100', class: 'er', 'aria-label': '밝기'});
    sl.addEventListener('input', () => { light = +sl.value; paint(); });
    const box = el('div', {}, cv, el('div', {class: 'ea'}, ...chips, rb, fb), fn, el('p', {class: 'note'}, '밝기'), sl,
      el('div', {class: 'ef'}, o.other ? btn('lnk mute', '교체·비우기', () => { H.back(); setTimeout(o.other, 60); }) : null,
        btn('lnk mute', o.save ? '취소' : '그대로 쓰기', () => H.back()), btn('pb', o.save || '편집 완료', async e => {
          e.currentTarget.disabled = true; out = await jpeg(); if (out) out.focus = focus; H.back();
        })));
    H.sheet('사진 편집', box, () => { if (src.close) src.close(); done(out); });
    function fit() {  // 비율에 맞는 가장 큰 칸 (가운데)
      const [w, h] = dims();
      let cw = w, ch = h;
      const q = rt();
      if (q) { if (w / h > q) cw = h * q; else ch = w / q; }
      c = {x: (w - cw) / 2, y: (h - ch) / 2, w: cw, h: ch};
      chips.forEach((b, i) => b.setAttribute('aria-pressed', String(RATIOS[i][1] === ratio)));
      const k = Math.min(1, 1600 / Math.max(w, h));
      cv.width = Math.round(w * k); cv.height = Math.round(h * k);
      paint();
    }
    function drawRot(ctx) {
      const [w, h] = dims();
      if (rot === 1) { ctx.translate(w, 0); ctx.rotate(Math.PI / 2); } else if (rot === 2) { ctx.translate(w, h); ctx.rotate(Math.PI); } else if (rot === 3) { ctx.translate(0, h); ctx.rotate(-Math.PI / 2); }
      ctx.drawImage(src, 0, 0);
    }
    function paint() {
      const k = cv.width / dims()[0];
      g.setTransform(k, 0, 0, k, 0, 0); drawRot(g); g.setTransform(1, 0, 0, 1, 0, 0);
      cv.style.filter = light === 100 ? '' : `brightness(${light}%)`;
      const x = c.x * k, y = c.y * k, w = c.w * k, h = c.h * k, r = Math.max(6, cv.width / 60);
      g.fillStyle = 'rgba(0,0,0,.5)';
      g.fillRect(0, 0, cv.width, y); g.fillRect(0, y + h, cv.width, cv.height - y - h); g.fillRect(0, y, x, h); g.fillRect(x + w, y, cv.width - x - w, h);
      g.strokeStyle = '#fff'; g.lineWidth = Math.max(2, cv.width / 300); g.strokeRect(x, y, w, h);
      g.fillStyle = '#fff';
      for (const [px, py] of [[x, y], [x + w, y], [x, y + h], [x + w, y + h]]) g.fillRect(px - r, py - r, 2 * r, 2 * r);
    }
    const at = e => { const b = cv.getBoundingClientRect(), k = dims()[0] / b.width; return [(e.clientX - b.left) * k, (e.clientY - b.top) * k, 26 * k]; };
    cv.addEventListener('pointerdown', e => {
      const [px, py, hit] = at(e), cs = [[c.x, c.y], [c.x + c.w, c.y], [c.x, c.y + c.h], [c.x + c.w, c.y + c.h]];
      const i = cs.findIndex(([x, y]) => Math.abs(px - x) < hit && Math.abs(py - y) < hit);
      if (i >= 0) drag = {ax: cs[3 - i][0], ay: cs[3 - i][1]};
      else if (px > c.x && px < c.x + c.w && py > c.y && py < c.y + c.h) drag = {dx: px - c.x, dy: py - c.y};
      else return;
      cv.setPointerCapture(e.pointerId); e.preventDefault();
    });
    cv.addEventListener('pointermove', e => {
      if (!drag) return;
      const [px, py] = at(e), [w, h] = dims(), min = Math.max(w, h) / 20;
      if (drag.dx != null) {
        c.x = Math.min(Math.max(0, px - drag.dx), w - c.w); c.y = Math.min(Math.max(0, py - drag.dy), h - c.h);
      } else {
        const sx = px < drag.ax ? -1 : 1, sy = py < drag.ay ? -1 : 1;
        let cw = Math.min(Math.abs(px - drag.ax), sx < 0 ? drag.ax : w - drag.ax), ch = Math.min(Math.abs(py - drag.ay), sy < 0 ? drag.ay : h - drag.ay);
        const q = rt();
        if (q) { if (cw / q > ch) cw = ch * q; else ch = cw / q; }
        if (cw < min || ch < min) return;
        c = {x: sx < 0 ? drag.ax - cw : drag.ax, y: sy < 0 ? drag.ay - ch : drag.ay, w: cw, h: ch};
      }
      paint();
    });
    const up = () => { drag = null; };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    async function jpeg() {
      const s = Math.min(1, 2000 / Math.max(c.w, c.h)), o = D.createElement('canvas'), x = o.getContext('2d');
      o.width = Math.max(1, Math.round(c.w * s)); o.height = Math.max(1, Math.round(c.h * s));
      x.setTransform(s, 0, 0, s, -c.x * s, -c.y * s); drawRot(x);
      if (light !== 100) {  // 밝기 (canvas filter 를 못 쓰는 브라우저도 같게 — 픽셀마다)
        const im = x.getImageData(0, 0, o.width, o.height), d = im.data, k = light / 100;
        for (let i = 0; i < d.length; i += 4) { d[i] *= k; d[i + 1] *= k; d[i + 2] *= k; }
        x.putImageData(im, 0, 0);
      }
      return new Promise(r => o.toBlob(b => r(b), 'image/jpeg', 0.9));
    }
    fit();
  });
}
// ------------------------------------------------------------ 사진 한 장 바로 (10/20): 대시보드 사진을 누르면 → 편집기 → 저장
// = photos_batch put {edited, focus?, retouch: 그 자리} (일 하나 · 봇은 다시 자르지 않음)
async function photo(h, s, other) {
  H = h; style();
  const f = await load(s.full || s.thumb);
  const out = f && await editor(f, {save: '저장', other});
  if (!out) return;
  try {
    const ids = await h.uploadAll([out], 'photo', () => {});
    await h.exec({action: 'blog.photos_batch', args: h.args}, {ops: [Object.assign(op(s.slot, {f: out, edited: true, re: s.slot}), {upload: ids[0]})]}, {iid: h.iid});
  } catch (x) { h.toast(x.message || '하지 못했어요 — 잠시 뒤 다시'); }
}
// ------------------------------------------------------------ 발행 글 고치기 (10/21): 사진마다 빼기·교체 + '이 문장 빼기' → blog.post_edit (★ 확인)
function fix(h, r) {
  H = h; style();
  const {el, btn} = h, ph = r.edit.photos.map(p => ({p, c: null})), urls = [], list = el('div');
  const t = el('textarea', {class: 'fl', rows: 2, maxlength: 500, placeholder: '뺄 문장을 글에 있는 그대로 붙여 넣기'});
  h.sheet('발행 글 고치기', el('div', {}, el('p', {class: 'note'}, '네이버에 공개된 글을 바로 고쳐요 · 카테고리·태그는 그대로'), list,
    el('p', {class: 'h3'}, '이 문장 빼기'), t, el('div', {class: 'ef'}, btn('lnk mute', '취소', () => h.back()), btn('pb', '고치기', e => send(e.currentTarget)))),
    () => urls.forEach(u => URL.revokeObjectURL(u)));
  const draw = () => list.replaceChildren(...ph.map(x => el('div', {class: 'eb' + (x.c ? ' chg' : '')},
    x.c && x.c.f ? el('img', {src: x.c.src, alt: ''}) : x.p.thumb ? el('img', {src: x.p.thumb, alt: x.p.label}) : el('span', {class: 'e0'}),
    el('div', {class: 'et'}, el('p', {}, el('b', {}, x.p.label)), el('p', {class: 'note'}, !x.c ? '지금 사진' : x.c.f ? '새 사진으로 교체' : '뺄 사진'),
      el('div', {class: 'ea'}, btn('sm', x.c && !x.c.f ? '되돌리기' : '빼기', () => { x.c = x.c && !x.c.f ? null : {}; draw(); }),
        btn('sm', '교체', () => h.pick('image/*', false, fs => { if (fs.length) { const src = URL.createObjectURL(fs[0]); urls.push(src); x.c = {f: fs[0], src}; draw(); } })))))));
  async function send(b) {
    const ops = ph.filter(x => x.c).map(x => x.c.f ? {op: 'replace_photo', index: x.p.n, f: x.c.f} : {op: 'remove_photo', index: x.p.n});
    if (t.value.trim()) ops.push({op: 'remove_text', text: t.value.trim()});
    if (!ops.length) { h.toast('뺄 사진이나 문장을 골라 주세요'); return; }
    b.disabled = true;
    try {
      const fs = ops.filter(o => o.f);
      if (fs.length) (await h.uploadAll(fs.map(o => o.f), 'photo', () => {})).forEach((id, i) => { fs[i].file = id; delete fs[i].f; });
      if (await h.exec(r.edit.act, {ops}, {iid: h.iid})) { h.back(); return; }
    } catch (x) { h.toast(x.message || '하지 못했어요 — 잠시 뒤 다시'); }
    b.disabled = false;
  }
  draw();
}
// ------------------------------------------------------------ 사진 테스트 (주인 10/21): 시험 글 사진마다 ✅/❌·이유 · 글 전체 · 테스트 끝
// 피드백은 blog.photo_feedback (배운 것 ×2) · 사진을 누르면 평소 편집기 (편집도 배운 것) · 맨 위 '배운 원칙'
const RK = {margin_left_over: '여백 남음', ui_remnant: 'UI 남음', subject_cut: '얼굴/몸 잘림', too_wide: '너무 넓음', too_tight: '너무 좁음',
  wrong_person: '다른 사람', low_quality_blur: '흐림', duplicate: '중복', text_overlay: '글자 많음', broadcast_frame: '방송 캡처',
  bad_aspect: '비율 이상', dark: '어두움', other: '기타'};
const PK = {placement: '사진 배치', spread: '사진 분포', count: '사진 개수', fit: '글과 안 맞음', other: '기타'};
const QK = Object.assign({border: '테두리', ui: '화면 띠', lowres: '해상도', text: '글자', broadcast: '방송 캡처', missing: '파일 없음'}, RK);
const TS = {};  // 고른 이유·메모 (다시 그려도 남게) — 키 '<pid>-<slot|all>'
function trial(h) {
  H = h; style();
  const {el, btn} = h, t = h.S, box = h.box;
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
    out.push(el('p', {class: 'h3'}, p.title), el('p', {class: 'note'}, `피드백 ${p.feedback_done}개 · 사진을 누르면 편집`));
    p.slots.forEach((s, i) => {
      const k = p.pid + '-' + s.slot, open = TS[k] && TS[k].o;
      out.push(el('div', {class: 'box'}, el('p', {}, el('b', {}, `자리 ${s.slot}`), ' ' + s.desc + (s.credit ? ` · ${s.credit}` : '')),
        s.final || s.full ? el('div', {class: 'ck', onclick: () => h.edPhoto(p, p.edit[i])}, el('img', {src: s.final || s.full, alt: s.desc})) : el('p', {class: 'note'}, '빈 자리'),
        s.qa.issues.length ? el('div', {class: 'ea'}, s.qa.issues.map(q => el('span', {class: 'cp stop'}, QK[q] || q))) : null,
        s.final || s.full ? el('div', {class: 'two'}, btn('pb soft', '✅ 괜찮아', e => go(p, s, 'ok', {r: [], n: ''}, e.currentTarget, k)),
          btn('pb soft', '❌ 별로', () => { TS[k] = Object.assign(TS[k] || {r: [], n: ''}, {o: !open}); h.render(); })) : null,
        open ? fb(p, k, s) : null));
    });
    const k = p.pid + '-all';
    out.push(el('p', {class: 'h3'}, '글 전체 (배치·분포·개수)'), fb(p, k, null),
      el('div', {class: 'two'}, btn('pb soft', '✅ 괜찮아', e => go(p, null, 'ok', {r: [], n: ''}, e.currentTarget, k)),
        btn('pb soft', p.done.label, e => h.exec(p.done, {}, {btn: e.currentTarget, iid: p.iid}))));
  }
  box.replaceChildren(...out);
}
W.SBEdit = {batch, editor, photo, trial, fix};
})();
