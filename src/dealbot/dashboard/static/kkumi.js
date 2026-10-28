/* 꾸미기 책갈피 (주인 10/27) — 네이버 블로그 글쓰기 화면(아이패드 사파리)에서 누르면, 주소만 있는 줄마다 링크 카드를 만든다.
   대시보드 '본문 복사'로 붙여 넣은 글은 링크 카드가 빠지므로 (서버 봇이 하던 것처럼 주소 줄은 남기고 그 아래에 카드).
   줄마다 위에서부터: 줄 끝에 커서 → 툴바 링크(oglink) 단추 → 주소 넣기 → 검색 → 미리보기(15초) → 확인 → 카드 수가 느는지(8초).
   이미 카드가 바로 아래 있는 줄은 건너뜀. 그다음 구분선은 짧은 선·가운데로, 인용구는 포스트잇으로 (이미 맞으면 건너뜀).
   끝에 알림 한 번 (종류마다 n/m 한 줄 + 못 한 것과 이유). 바깥 요청 없음 (편집기 화면만 누름).
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
// 구분선·인용구 (주인 10/27 — 붙여 넣은 <hr> 은 긴 선, <blockquote> 는 기본 인용구가 됨). 서버 봇 editor.py 의 선택자·순서 그대로:
// 선을 누름 → LINE_DEFAULT(짧은 선) · 선을 누름 → 정렬 → 가운데 · 인용구를 누름 → QUOTE_OPENERS → QUOTE_POSTIT (이것만 봇에 없던 길)
const LINE_OPENERS = ["button[data-name='horizontal-line'] + button", "button[data-name='horizontal-line-select']", '.se-toolbar-item-horizontal-line button.se-document-toolbar-select-option-button', "[class*='horizontal'] button[class*='select']"];
const LINE_DEFAULT = ["button[data-value='default'][data-name*='horizontal']", "[class*='horizontal'] button[data-value='default']", "button[data-value='horizontal-line-default']", "button[class*='horizontal-line-default']", "button[data-value='default']"];
const ALIGN = ["button[data-name='align-drop-down-with-justify']", "button[data-name='align']"];
const CENTER = ["button[data-value='center']", "button[class*='align-center']"];
const QUOTE_OPENERS = ["button[data-name='quotation'] + button", "button[data-name='quotation-select']", '.se-toolbar-item-quotation button.se-document-toolbar-select-option-button', "[class*='quotation'] button[class*='select']", "[class*='quotation'] button[class*='arrow']"];
const QUOTE_POSTIT = ["button[data-value='quotation_postit']", "button[class*='quotation_postit']", "[data-value*='postit']", "button[class*='postit']"];
const lines = () => [...D.querySelectorAll('.se-component.se-horizontalLine')];
const quotes = () => [...D.querySelectorAll('.se-component.se-quotation')];
const short = e => /(^|\s)se-l-default(\s|$)/.test(e.className);
const mid = e => /align-center/.test((e.querySelector('.se-section') || e).className);
const postit = e => /se-l-quotation_postit/.test(e.className);
// 메뉴 열기(있으면) → 고르기. 고를 단추가 이미 보이면 바로 누름
const choose = async (open, pick) => {
  let c = vis(pick);
  if (!c && open.length) {
    const o = vis(open);
    if (!o) return false;
    press(o);
    c = await wait(() => vis(pick), 2000);
  }
  if (!c) return false;
  press(c);
  await S(300);
  return true;
};
const fix = async (list, i, ok, steps) => {
  for (const [good, open, pick, why] of steps) {
    let e = list()[i];
    if (!e) return '사라짐';
    if (good(e)) continue;
    press(e);
    await S(400);
    const q = e.querySelector('.se-text-paragraph');
    if (q) { caret(q); await S(200); }
    if (!await choose(open, pick)) return why + ' 단추를 못 찾음';
    if (!await wait(() => list()[i] && good(list()[i]), 3000)) return '눌렀지만 ' + why + '(으)로 안 바뀜';
  }
  return ok(list()[i]) ? '' : '확인 실패';
};
const run = async (name, list, ok, steps) => {
  let n = 0, m = 0;
  const all = list();
  for (let i = 0; i < all.length; i++) {
    const e = list()[i];
    if (!e || ok(e)) continue;
    m++;
    const why = await fix(list, i, ok, steps);
    if (why) bad.push(name + ' ' + (i + 1) + '번째 — ' + why); else n++;
  }
  return [n, m];
};
const m = todo().length, bad = [], out = [];
let n = 0, lf = 0;
try {
  for (let k = 0; k < m; k++) {
    const p = todo()[lf];
    if (!p) break;
    const u = txt(p), why = await one(p, u);
    if (why) { lf++; bad.push(u + ' — ' + why); } else n++;
  }
  if (m) out.push('링크 카드 ' + n + '/' + m + '개 만들었어요');
  // 구분선·인용구 고치기는 끔 (10/28 주인 아이패드: 기존 것을 못 고르고 새 짧은 선·빈 포스트잇을 하나씩 더 넣음) — 붙여넣기 모양으로 맞추는 길을 시험 중
  if (window.__kkumiFix || W.__kkumiFix) {
    const [ln, lm] = await run('구분선', lines, e => short(e) && mid(e), [[short, LINE_OPENERS, LINE_DEFAULT, '짧은 선'], [mid, ALIGN, CENTER, '가운데']]);
    if (lm) out.push('구분선 ' + ln + '/' + lm + '개 짧게·가운데');
    const [qn, qm] = await run('인용구', quotes, postit, [[postit, QUOTE_OPENERS, QUOTE_POSTIT, '포스트잇']]);
    if (qm) out.push('인용구 ' + qn + '/' + qm + '개 포스트잇');
  }
} finally { W.__kkumi = 0; }
alert(out.length ? out.join('\n') + (bad.length ? '\n못 한 것:\n' + bad.join('\n') : '') : '고칠 것이 없어요 (링크 카드가 모두 있어요)');
})().catch(e => alert('꾸미기 오류: ' + e));
