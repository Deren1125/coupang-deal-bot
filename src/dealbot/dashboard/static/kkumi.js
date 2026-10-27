/* 꾸미기 책갈피 (주인 10/27) — 네이버 블로그 글쓰기 화면(아이패드 사파리)에서 누르면, 주소만 있는 줄마다 링크 카드를 만든다.
   대시보드 '본문 복사'로 붙여 넣은 글은 링크 카드가 빠지므로 (서버 봇이 하던 것처럼 주소 줄은 남기고 그 아래에 카드).
   줄마다 위에서부터: 줄 끝에 커서 → 툴바 링크(oglink) 단추 → 주소 넣기 → 검색 → 미리보기(15초) → 확인 → 카드 수가 느는지(8초).
   이미 카드가 바로 아래 있는 줄은 건너뜀. 끝에 알림 한 번. 바깥 요청 없음 (편집기 화면만 누름).
   kkumi.py 가 이 파일에서 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — 줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만). */
(async () => {
const S = m => new Promise(r => setTimeout(r, m));
const look = d => {
  if (d.querySelector('.se-content')) return d;
  for (const f of d.querySelectorAll('iframe')) {
    try { const x = f.contentDocument, r = x && look(x); if (r) return r; } catch (e) {}
  }
  return null;
};
const D = look(document);
if (!D) { alert('글쓰기 화면을 못 찾았어요 — 네이버 블로그 글쓰기 화면에서 눌러 주세요'); return; }
const W = D.defaultView;
if (W.__kkumi) { alert('꾸미기가 이미 돌고 있어요 — 끝날 때까지 기다려 주세요'); return; }
W.__kkumi = 1;
const CARD = '.se-oglink,.se-module-oglink,[class*="se-component-oglink"]';
const cards = () => D.querySelectorAll(CARD).length;
const txt = p => (p.textContent || '').replace(/[\u200b\ufeff]/g, '').trim();
const isUrl = t => /^https?:\/\/[^\s]+$/i.test(t);
const vis = q => {
  for (const s of q) for (const e of D.querySelectorAll(s)) if (e.offsetParent !== null) return e;
  return null;
};
const press = e => {
  const r = e.getBoundingClientRect(), o = {bubbles: true, cancelable: true, view: W, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0};
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    const C = t.startsWith('pointer') && W.PointerEvent ? W.PointerEvent : W.MouseEvent;
    e.dispatchEvent(new C(t, Object.assign({pointerId: 1, isPrimary: true}, o)));
  }
};
const wait = async (f, ms) => {
  const t = Date.now();
  while (Date.now() - t < ms) { const v = f(); if (v) return v; await S(250); }
  return null;
};
// 주소만 있는 줄 중 바로 아래(빈 줄은 건너뜀)가 카드가 아닌 것 — 위에서부터
const todo = () => {
  const all = [...D.querySelectorAll('.se-text-paragraph,' + CARD)], out = [];
  all.forEach((p, i) => {
    if (!p.matches('.se-text-paragraph') || !isUrl(txt(p))) return;
    let j = i + 1;
    while (j < all.length && all[j].matches('.se-text-paragraph') && !txt(all[j])) j++;
    if (!(j < all.length && !all[j].matches('.se-text-paragraph'))) out.push(p);
  });
  return out;
};
const caret = p => {
  const w = D.createTreeWalker(p, NodeFilter.SHOW_TEXT), r = D.createRange();
  let last = null;
  while (w.nextNode()) last = w.currentNode;
  if (last) { r.setStart(last, last.data.length); r.collapse(true); } else { r.selectNodeContents(p); r.collapse(false); }
  const g = W.getSelection();
  g.removeAllRanges();
  g.addRange(r);
  D.dispatchEvent(new Event('selectionchange'));
};
const shut = () => {
  const x = vis(['.se-popup-oglink .se-popup-close-button', '.se-popup-oglink button[class*="close"]', '.se-popup-oglink button[class*="cancel"]']);
  if (x) press(x);
};
const one = async (p, u) => {
  const before = cards();
  caret(p);
  await S(300);
  const b = vis(["button[data-name='oglink']", 'button.se-oglink-toolbar-button', "button[class*='oglink']"]);
  if (!b) return '링크 단추를 못 찾음';
  press(b);
  const i = await wait(() => vis(['.se-popup-oglink input', "input[class*='oglink']", "input[placeholder*='URL']", "input[placeholder*='url']", "input[placeholder*='링크']"]), 3000);
  if (!i) return '주소 칸이 안 뜸';
  i.focus();
  Object.getOwnPropertyDescriptor(W.HTMLInputElement.prototype, 'value').set.call(i, u);
  i.dispatchEvent(new Event('input', {bubbles: true}));
  i.dispatchEvent(new Event('change', {bubbles: true}));
  await S(200);
  const s = vis(['button.se-popup-oglink-button', ".se-popup-oglink button[class*='search']", "button[class*='oglink'][class*='search']"]);
  if (s) press(s); else i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, bubbles: true}));
  await S(600);
  const pv = await wait(() => {
    const pop = D.querySelector('.se-popup-oglink');
    if (!pop) return null;
    const busy = pop.querySelector('[class*="loading"],[class*="spinner"],[class*="progress"]');
    const prev = pop.querySelector('[class*="preview"],[class*="oglink-info"],img,[class*="thumbnail"]');
    return (!busy || busy.offsetParent === null) && prev ? prev : null;
  }, 15000);
  if (!pv) { shut(); return '미리보기가 15초 안에 안 뜸'; }
  const c = vis(['.se-popup-oglink .se-popup-button-confirm', 'button.se-popup-button-confirm']);
  if (!c) { shut(); return '확인 단추를 못 찾음'; }
  press(c);
  if (!await wait(() => cards() > before, 8000)) { shut(); return '확인을 눌렀지만 카드가 안 생김'; }
  await S(400);
  return '';
};
const m = todo().length, bad = [];
let n = 0;
try {
  for (let k = 0; k < m; k++) {
    const p = todo()[bad.length];
    if (!p) break;
    const u = txt(p), why = await one(p, u);
    if (why) bad.push(u + ' — ' + why); else n++;
  }
} finally { W.__kkumi = 0; }
alert(m ? '링크 카드 ' + n + '/' + m + '개 만들었어요' + (bad.length ? '\n못 만든 것:\n' + bad.join('\n') : '') : '만들 링크 카드가 없어요 (주소만 있는 줄이 없거나 이미 카드가 있어요)');
})().catch(e => alert('꾸미기 오류: ' + e));
