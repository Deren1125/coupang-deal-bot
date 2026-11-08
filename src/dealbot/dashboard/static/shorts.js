/* 올리기 할 일 (주인 11/08 — 롱폼·쇼츠·룩북 릴스·설명글을 한곳에, to do list 처럼). SNS 탭 '올리기' 칸을 처음 열 때만 불러옴.
   값은 GET /dashboard/api/uploads (uploadplan.py — 쇼츠 서버 일정을 딜봇이 10분마다 읽어 둔 것). 체크는 봇 DB 에만 (uploads.done).
   한 줄 = 시각 · 플랫폼 · 무엇 + [파일 받기] [제목 복사] [설명글+태그 복사] + 올렸어요 체크. 오늘·내일·이번 주·나중 · 지난 것은 접힘. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.upw{display:grid;gap:10px}.upw .itm{padding:12px 14px}.upg{margin:4px 0 0;font-size:14px;font-weight:700;color:var(--sub)}' +
  '.upr{padding:10px 0;border-top:1px solid var(--line)}.upr:first-of-type{border-top:0}.upr.d{opacity:.45}' +
  '.uph{display:flex;gap:8px;align-items:center;font-size:14px}.uph b{font-variant-numeric:tabular-nums}' +
  '.upc{font-size:12px;font-weight:600;border-radius:999px;padding:2px 8px;background:var(--grey)}' +
  '.upt{margin:4px 0 0;font-size:15px;font-weight:600;overflow-wrap:anywhere}.upn{margin:2px 0 0;font-size:13px;color:var(--sub)}' +
  '.upb{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.upb .pb,.upb a.pb{width:auto;flex:1 1 auto;min-height:44px;padding:0 12px;margin:0;' +
  'display:inline-flex;align-items:center;justify-content:center;text-decoration:none}' +
  '.upk{display:flex;gap:8px;align-items:center;min-height:44px;margin-top:4px;font-size:15px}.upk input{width:24px;height:24px;flex:none}' +
  '.upr.g{opacity:.5}.upq{display:inline-block;margin-top:6px;font-size:13px;font-weight:700;color:var(--red)}.upn{white-space:pre-line}' +
  '.upw details summary{min-height:44px;display:flex;align-items:center;font-weight:600}';
let H = null, B = null, V = null, BUSY = false;

const css = () => { if (D.getElementById('up-css')) return; const s = D.createElement('style'); s.id = 'up-css'; s.textContent = CSS; D.head.append(s); };
async function copy(t, what) {
  try { await navigator.clipboard.writeText(t); H.toast(what + ' 복사했어요'); } catch (e) {
    const x = H.el('textarea', {style: 'position:fixed;opacity:0'}); x.value = t; D.body.append(x); x.select();
    try { D.execCommand('copy'); H.toast(what + ' 복사했어요'); } catch (y) { H.toast('길게 눌러 복사해 주세요'); }
    x.remove();
  }
}
const descTags = x => [x.desc, x.tags.length ? x.tags.map(t => '#' + t).join(' ') : ''].filter(Boolean).join('\n\n');

async function check(x, box) {
  const v = !x.done;
  x.done = v; draw();
  const cid = 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(10)), n => (n % 36).toString(36)).join('');
  const r = await H.api('/dashboard/api/action', {method: 'POST', body: {action: 'uploads.done', args: {id: x.id, done: v}, cid, ctoken: null}});
  if (r.status === 200 && (r.data || {}).status === 'ok') return;
  x.done = !v; draw();
  H.toast((r.data || {}).text || '체크하지 못했어요 — 잠시 뒤 다시');
}

function row(x) {
  const {el, btn} = H;
  if (x.lb) {
    return el('div', {class: 'upr' + (x.done ? ' d' : '')},
      el('div', {class: 'uph'}, el('b', {}, x.hm || x.when_text), el('span', {class: 'upc'}, x.plat || '인스타'), el('span', {}, x.what || '룩북 릴스')),
      el('p', {class: 'upt'}, x.title || '룩북 릴스'),
      el('div', {class: 'upb'}, el('a', {class: 'pb', href: '#/sns/lookbook'}, '룩북 릴스 칸에서 올리기')));
  }
  if (x.gate === false) {
    return el('div', {class: 'upr g'},
      el('div', {class: 'uph'}, el('b', {}, x.hm || x.when_text), x.plat ? el('span', {class: 'upc'}, x.plat) : null, el('span', {}, x.what)),
      x.title ? el('p', {class: 'upt'}, x.title) : null,
      el('span', {class: 'upq'}, '검수 대기 · 아직 올리지 마세요'),
      x.note ? el('p', {class: 'upn'}, x.note) : null);
  }
  const box = el('input', {type: 'checkbox'});
  box.checked = !!x.done;
  box.onchange = () => check(x, box);
  return el('div', {class: 'upr' + (x.done ? ' d' : '')},
    el('div', {class: 'uph'}, el('b', {}, x.hm || x.when_text), x.plat ? el('span', {class: 'upc'}, x.plat) : null, el('span', {}, x.what)),
    x.title ? el('p', {class: 'upt'}, x.title) : null,
    x.note ? el('p', {class: 'upn'}, x.note) : null,
    el('div', {class: 'upb'},
      x.file_url ? el('a', {class: 'pb', href: x.file_url, target: '_blank', rel: 'noopener noreferrer', download: ''}, '파일 받기') : null,
      x.title ? btn('pb', '제목 복사', () => copy(x.title, '제목')) : null,
      x.desc || x.tags.length ? btn('pb', '설명글+태그 복사', () => copy(descTags(x), '설명글·태그')) : null),
    el('label', {class: 'upk'}, box, x.done ? '올렸어요 ✓' : '올렸으면 체크'));
}

function group(g) {
  const {el} = H, left = g.items.filter(x => !x.done && !x.lb && x.gate !== false).length;
  const body = [el('p', {class: 'upg'}, g.label, left ? ` · 남은 ${left}` : g.items.some(x => x.gate === false) ? ' · 검수 대기' : ' ✓'), g.items.map(row)];
  if (g.key === 'past') return el('section', {class: 'itm'}, el('details', {}, el('summary', {}, `지난 것 ${g.items.length}개`), g.items.map(row)));
  return el('section', {class: 'itm'}, body);
}

function draw() {
  if (!H || !B) return;
  css();
  const {el} = H;
  if (V === null) { B.replaceChildren(el('p', {class: 'note'}, '불러오는 중…')); return; }
  if (V === false) { B.replaceChildren(el('p', {class: 'note'}, '올리기 할 일을 불러오지 못했어요 — 새로고침해 주세요')); return; }
  B.replaceChildren(el('div', {class: 'upw'},
    el('p', {class: 'note', style: 'margin:0'}, '쇼츠 총괄이 정한 일정이에요 · 파일 받기 → 앱에서 올리기 → 체크'),
    V.err ? el('p', {class: 'note', style: 'margin:0;color:var(--red)'}, V.err) : null,
    V.groups.length ? V.groups.map(group)
      : el('section', {class: 'itm'}, el('p', {class: 'note', style: 'margin:0'}, '아직 올릴 일정이 없어요 — 쇼츠 서버에 일정이 생기면 여기 떠요'))));
}

async function load() {
  if (BUSY) return;
  BUSY = true;
  const r = await H.api('/dashboard/api/uploads');
  BUSY = false;
  V = r.status === 200 && r.data && Array.isArray(r.data.groups) ? r.data : false;
  draw();
}

W.SBShorts = {view(b, h) { B = b; H = h; if (V === null) draw(); load(); }};
})();
