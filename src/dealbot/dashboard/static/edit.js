/* Sns-board 사진 고치기 (주인 10/19): 사진 자리 한 번에 고치기 + 사진 편집기 (자르기·돌리기·밝기). 처음 쓸 때만 app.js 가 불러온다.
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

// ------------------------------------------------------------ 사진 자리 한 번에 고치기
// 자리마다 지금 든 것: {o: 원래 자리 번호, src} (원래 사진) · {f: 파일, src, edited} (새 사진) · null (빈 자리)
function batch(h) {
  H = h; style();
  const {el, btn} = h, rows = h.slots.map(s => ({slot: s.slot, label: s.label || '', cur: s.thumb ? {o: s.slot, src: s.thumb} : null, was: !!s.thumb}));
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
  const edit = async r => { const out = await editor(r.cur.f); if (out) put(r, out, true); };
  function draw() {
    list.replaceChildren(...rows.map((r, i) => {
      const c = r.cur, row = el('div', {class: 'eb' + (same(r) ? '' : ' chg'), draggable: 'true'},
        c ? el('img', {src: c.src, alt: `자리 ${r.slot}`}) : el('span', {class: 'e0'}),
        el('div', {class: 'et'}, el('p', {}, el('b', {}, `자리 ${r.slot}`), r.label ? ' · ' + r.label : ''),
          el('p', {class: 'note'}, !c ? '빈 자리' : c.f ? (c.edited ? '새 사진 · 편집함' : '새 사진') : c.o !== r.slot ? `자리 ${c.o}에서 옮김` : '지금 사진'),
          el('div', {class: 'ea'}, c ? btn('sm', '✕ 빼기', () => { r.cur = null; draw(); }) : null,
            btn('sm', c ? '교체' : '사진 추가', () => choose(r)), c && c.f ? btn('sm', '편집', () => edit(r)) : null,
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
    for (const r of rows) if (r.cur && r.cur.f) { out.push({op: 'put', slot: r.slot, upload: null, credit: '', edited: !!r.cur.edited}); files.push(r.cur.f); }
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

// ------------------------------------------------------------ 편집기: 자르기(손잡이·비율) · 90° 돌리기 · 밝기 → JPEG (긴 변 2000px, 0.9)
const RATIOS = [['자유', 0], ['1:1', 1], ['4:5', 0.8], ['3:4', 0.75]];
async function bitmap(f) {
  try { return await createImageBitmap(f, {imageOrientation: 'from-image'}); } catch (x) { /* 아래 */ }
  const u = URL.createObjectURL(f), im = new Image();
  try { im.src = u; await im.decode(); return im; } finally { URL.revokeObjectURL(u); }
}
function editor(file) {
  const {el, btn} = H;
  style();
  return new Promise(async done => {
    let src;
    try { src = await bitmap(file); } catch (x) { H.toast('이 사진은 열 수 없어요 — 다른 사진으로'); done(null); return; }
    let rot = 0, ratio = 0, light = 100, c = null, drag = null, out = null;
    const cv = el('canvas', {class: 'ecv'}), g = cv.getContext('2d');
    const dims = () => rot % 2 ? [src.height, src.width] : [src.width, src.height];
    const rb = btn('sm', '↻ 90° 돌리기', () => { rot = (rot + 1) % 4; fit(); });
    const chips = RATIOS.map(([t, v]) => btn('sm', t, () => { ratio = v; fit(); }, {'aria-pressed': String(v === ratio)}));
    const sl = el('input', {type: 'range', min: '50', max: '150', value: '100', class: 'er', 'aria-label': '밝기'});
    sl.addEventListener('input', () => { light = +sl.value; paint(); });
    const box = el('div', {}, cv, el('div', {class: 'ea'}, ...chips, rb), el('p', {class: 'note'}, '밝기'), sl,
      el('div', {class: 'ef'}, btn('lnk mute', '그대로 쓰기', () => H.back()), btn('pb', '편집 완료', async e => {
        e.currentTarget.disabled = true; out = await jpeg(); H.back();
      })));
    H.sheet('사진 편집', box, () => { if (src.close) src.close(); done(out); });
    function fit() {  // 비율에 맞는 가장 큰 칸 (가운데)
      const [w, h] = dims();
      let cw = w, ch = h;
      if (ratio) { if (w / h > ratio) cw = h * ratio; else ch = w / ratio; }
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
        if (ratio) { if (cw / ratio > ch) cw = ch * ratio; else ch = cw / ratio; }
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
W.SBEdit = {batch, editor};
})();
