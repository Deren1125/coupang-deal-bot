/* 스레드 글·예약·댓글·통계 (주인 10/21) — threads.js 의 세 칸(compose · alerts · stats)에 붙는 그리기. 값은 state.thpost (thpost.py).
   누르는 것은 blog.threads_post ★ · blog.threads_cancel · blog.threads_reply ★ 뿐 — app.js exec 가 확인 창까지 그대로 한다.
   threads.js 가 SBThreads 를 새로 만들어 덮어쓰므로, 먼저 불러와도 나중에 불러와도 칸이 붙도록 W.SBThreads 에 setter 를 건다. 라이브러리 없음. */
'use strict';
(() => {
const D = document, W = window, S0 = {};
const CSS = '.tp{display:grid;gap:8px}.tp textarea{font:inherit;font-size:16px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--card);' +
  'color:var(--text);min-height:96px;resize:vertical;width:100%;box-sizing:border-box}.tp .r{display:flex;gap:8px;align-items:center;flex-wrap:wrap}' +
  '.tp .r>*{min-width:0}.tp input[type=time]{font:inherit;font-size:16px;padding:9px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text)}' +
  '.tp .seg{width:auto;min-width:120px}.tp .n{margin-left:auto;font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums}.tp .n.x{color:var(--red)}' +
  '.tl2 p{margin:0;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px;overflow-wrap:anywhere}.tl2 p:last-child{border:0}' +
  '.tl2 i{display:block;font-style:normal;color:var(--sub);font-size:12px}.tl2 a{color:var(--text)}.tl2 .pb{width:auto;min-height:40px;padding:0 14px;margin-top:6px}' +
  '.tp .tq,.tt{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:6px}.tt div{background:var(--grey);border-radius:10px;padding:8px 10px}' +
  '.tt b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.tt span{font-size:12px;color:var(--sub)}' +
  '.bar{display:grid;grid-template-columns:64px 1fr 44px;gap:8px;align-items:center;font-size:13px;padding:3px 0}.bar u{display:block;height:10px;border-radius:5px;background:var(--text);opacity:.55;text-decoration:none}' +
  '.bar b{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}';
const css = () => { if (D.getElementById('tp-css')) return; const s = D.createElement('style'); s.id = 'tp-css'; s.textContent = CSS; D.head.append(s); };
const note = (h, t) => h.el('p', {class: 'note', style: 'margin-top:0'}, t);
const nf = n => (n == null ? 0 : n).toLocaleString('ko-KR');
const sub = s => s ? Object.entries(s).length && ` · 조회 ${nf(s.views)} · 좋아요 ${nf(s.likes)} · 답글 ${nf(s.replies)}` : '';

function compose(t, h) {
  css();
  const {el, btn} = h, out = [el('p', {style: 'margin:0'}, t.status), t.cap_text ? note(h, t.cap_text) : null];
  if (!t.connected) return out.concat(note(h, t.how));
  const ta = el('textarea', {maxlength: t.limit, rows: 4, placeholder: '스레드에 올릴 글', 'aria-label': '글'}), cnt = el('span', {class: 'n'}, `0/${t.limit}`);
  ta.oninput = () => { cnt.textContent = `${ta.value.length}/${t.limit}`; cnt.className = 'n' + (ta.value.length > t.limit ? ' x' : ''); };
  let file = null;
  const pic = el('span', {class: 'note', style: 'margin:0'}), fi = el('input', {type: 'file', accept: 'image/*', hidden: true});
  fi.onchange = () => { file = fi.files[0] || null; pic.textContent = file ? file.name : ''; };
  const tm = el('input', {type: 'time', value: '21:00', 'aria-label': '예약 시각'});
  let day = 'today';
  const dayB = [['today', '오늘'], ['tomorrow', '내일']].map(([k, l]) => btn('', l, () => { day = k; dayB.forEach((b, i) => b.setAttribute('aria-pressed', i === (k === 'today' ? 0 : 1))); },
    {'aria-pressed': k === day ? 'true' : 'false'}));
  const send = async (e, extra) => {
    const text = ta.value.trim(), b = e.currentTarget;
    if (!text) return h.toast('글을 써 주세요');
    if (text.length > t.limit) return h.toast(`${t.limit}자까지예요`);
    if (!t.compose || !h.exec) return h.toast('지금은 올릴 수 없어요');
    const a = Object.assign({text}, extra);
    if (file) {
      b.disabled = true;
      try { const ids = await h.uploadAll([file], 'photo', () => 0); if (ids[0]) a.image = ids[0]; } catch (x) { b.disabled = false; return h.toast(x.message || '사진을 올리지 못했어요'); }
      b.disabled = false;
    }
    h.exec(t.compose, a, {btn: b});
  };
  out.push(el('div', {class: 'tp'}, ta, el('div', {class: 'r'}, btn('pb soft', '사진', () => fi.click(), {style: 'width:auto;padding:0 14px;min-height:44px'}), fi, pic, cnt),
    btn('pb', t.compose ? t.compose.label || '바로 올리기' : '바로 올리기', e => send(e, {}), {disabled: !t.compose}),
    el('div', {class: 'r'}, tm, el('div', {class: 'seg', role: 'group', 'aria-label': '날'}, dayB),
      btn('pb soft', '예약', e => tm.value ? send(e, {at: tm.value, day}) : h.toast('시각을 골라 주세요'), {style: 'width:auto;flex:1;min-width:80px', disabled: !t.compose}))));
  if (t.queue.length) out.push(el('p', {class: 'h3'}, '예약 · 대기'), el('div', {class: 'tl2'}, t.queue.map(q => el('p', {}, el('i', {}, `${q.when} · ${q.status_text}`), q.text,
    q.image ? ' 🖼' : '', q.permalink ? el('a', {href: q.permalink, target: '_blank', rel: 'noopener'}, ' ↗') : null,
    q.cancel ? btn('pb soft', q.cancel.label || '취소', e => h.exec(q.cancel, {}, {btn: e.currentTarget})) : null))));
  if (t.posts.length) out.push(el('p', {class: 'h3'}, '최근 올린 글'), el('div', {class: 'tl2'}, t.posts.map(p => el('p', {}, el('i', {}, p.when + sub(p.stats)), p.text,
    p.permalink ? el('a', {href: p.permalink, target: '_blank', rel: 'noopener'}, ' ↗ 열기') : null))));
  return out;
}

function alerts(t, h) {
  css();
  const {el, btn} = h, r = t.replies;
  if (!t.connected) return [note(h, '스레드가 연결되면 새 답글이 여기 떠요')];
  if (!r.items.length) return [note(h, '새 답글이 없어요')];
  return [el('p', {style: 'margin:0 0 4px'}, `새 답글 ${r.new}개`), el('div', {class: 'tl2'}, r.items.map(x => {
    const ta = el('textarea', {maxlength: t.limit, rows: 2, placeholder: '답글', 'aria-label': `${x.username} 님께 답글`});
    return el('p', {}, el('i', {}, `내 글: ${x.post_text}`), el('b', {}, '@' + x.username), ' ' + x.text, el('i', {}, x.when), el('span', {class: 'tp'}, ta,
      btn('pb soft', x.reply ? x.reply.label || '답글 달기' : '답글 달기', e => ta.value.trim() && x.reply && h.exec ? h.exec(x.reply, {text: ta.value.trim()}, {btn: e.currentTarget}) : h.toast('답글을 써 주세요'),
        {disabled: !x.reply})));
  }))];
}

function stats(t, h) {
  css();
  const {el} = h, s = t.stats;
  if (!s) return [note(h, '통계는 3시간마다 모아요 — 아직 없어요')];
  const mx = Math.max(1, ...s.days.map(d => d.views));
  return [el('div', {class: 'tt'}, s.totals.map(x => el('div', {}, el('b', {}, nf(x.n)), el('span', {}, x.label))), s.followers != null ? el('div', {}, el('b', {}, nf(s.followers)), el('span', {}, '팔로워')) : null),
    note(h, `${s.range}${s.when ? ' · ' + s.when + ' 기준' : ''}`),
    el('div', {style: 'margin-top:6px'}, s.days.map(d => el('div', {class: 'bar'}, el('span', {}, d.label), el('span', {}, el('u', {style: `width:${Math.round(d.views / mx * 100)}%`})), el('b', {}, nf(d.views)))))];
}

const mine = {compose, alerts, stats};
// threads.js 는 W.SBThreads = {view, slots} 로 덮어쓴다 — 어느 쪽이 먼저여도 칸이 붙게 setter 로 받아서 합친다
const merge = o => { if (o && typeof o === 'object') { o.slots = o.slots || {}; for (const k in mine) if (!o.slots[k]) o.slots[k] = mine[k]; } return o; };
let cur = merge(W.SBThreads || {slots: {}});
try { Object.defineProperty(W, 'SBThreads', {configurable: true, get: () => cur, set: v => { cur = merge(v); }}); } catch (e) { W.SBThreads = cur; }
})();
