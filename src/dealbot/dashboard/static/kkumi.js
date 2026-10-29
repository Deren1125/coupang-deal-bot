/* 꾸미기 책갈피 v4 (주인 10/29 아이패드 시험 뒤 — 위에서부터 차례로 쌓기). 네이버 블로그 글쓰기(아이패드 사파리)에서 본문을 한 번 누른 뒤 누르면:
   작은 칸이 뜨고 → 주인이 대시보드에서 복사한 본문을 그 칸에 붙여 넣으면 → 글을 위에서부터 조각마다 에디터에 붙여 넣으며
   구분선 자리(\u2063 만 든 빈 줄)에는 짧은 가운데 구분선 · 주소만 있는 줄 다음엔 링크 카드 · 인용구는 포스트잇 · 끝의 태그 줄(\u2064)은 발행 설정창 태그 칸에.
   아이패드 시험(10/29): 스크립트로 커서를 옮기는 방법 6가지 모두 안 됨 (넣은 것은 늘 글 맨 끝) · 스크립트 붙여넣기 신호는 됨 (맨 끝에).
   그래서 커서를 옮기지 않고 맨 끝에 차례로 쌓는다. 바깥 요청 없음. 끝에 알림 한 번 (부품 차례 · 못 한 것). 결과는 window.__kkumiReport 에도.
   딜봇 kkumi.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — 줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). */
(async () => {
const S = m => new Promise(r => setTimeout(r, m));
const look = d => {
  if (d.querySelector('.se-content')) return d;
  for (const f of d.querySelectorAll('iframe,frame')) {
    try { const x = f.contentDocument, r = x && look(x); if (r) return r; } catch (e) {}
  }
  return null;
};
const D = look(document);
if (!D) { alert('꾸미기: 글쓰기 화면이 아니에요'); return; }
const W = D.defaultView;
if (W.__kkumi) { alert('꾸미기: 이미 도는 중'); return; }
const ZL = '\u2063', ZT = '\u2064';
const CARD = '.se-oglink,.se-module-oglink,[class*="se-component-oglink"]';
const cards = () => D.querySelectorAll(CARD).length;
const raw = p => (p.textContent || '').replace(/[\u200b\ufeff]/g, '');
const txt = p => raw(p).replace(/[\u2063\u2064\u200c\u200d]/g, '').trim();
const isUrl = t => /^https?:\/\/[^\s]+$/i.test(t);
const vis = q => {
  for (const s of q) for (const e of D.querySelectorAll(s)) if (e.offsetParent !== null) return e;
  return null;
};
const fire = (e, types, p) => {
  const o = {bubbles: true, cancelable: true, view: W, clientX: p.x, clientY: p.y, button: 0, pointerId: 1, isPrimary: true};
  for (const t of types) {
    const C = t.startsWith('pointer') && W.PointerEvent ? W.PointerEvent : W.MouseEvent;
    e.dispatchEvent(new C(t, o));
  }
};
const mid_ = e => { const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; };
const press = e => fire(e, ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'], mid_(e));
const wait = async (f, ms) => {
  const t = Date.now();
  while (Date.now() - t < ms) { const v = f(); if (v) return v; await S(250); }
  return null;
};
const content = () => D.querySelector('.se-content') || D.body;
const host = () => content().closest('[contenteditable="true"]') || content().querySelector('[contenteditable="true"]') || D.querySelector('[contenteditable="true"]');
const inTitle = e => !!e.closest('.se-documentTitle,.se-section-documentTitle');
const body_ = () => [...D.querySelectorAll('.se-text-paragraph')].filter(p => !inTitle(p));
const comps = () => [...content().querySelectorAll('.se-component')].filter(c => !inTitle(c) && !c.parentElement.closest('.se-component'));
const lines = () => [...D.querySelectorAll('.se-component.se-horizontalLine')];
const quotes = () => [...D.querySelectorAll('.se-component.se-quotation')];
const newOf = (list, before) => list().find(x => !before.includes(x)) || null;
// 빈 본문의 안내 글(글감과 함께 …)은 placeholder 칸이라 글로 세지 않음 (10/29 아이패드)
const real = c => { const k = c.cloneNode(true); k.querySelectorAll('[class*="placeholder"]').forEach(x => x.remove()); return txt(k); };
const empty = () => comps().every(c => /se-text\b/.test(c.className) && !real(c));
// 에디터 붙여넣기 신호 (아이패드 10/29: 맨 끝에 들어감). 선택은 에디터가 쓰지 않지만 시험 때와 같게 끝 줄에 둔 뒤 보냄
const setSel = p => {
  const h = host();
  if (h) h.focus();
  if (!p) return;
  const w = D.createTreeWalker(p, NodeFilter.SHOW_TEXT), r = D.createRange();
  let last = null;
  while (w.nextNode()) last = w.currentNode;
  if (last) { r.setStart(last, last.data.length); r.collapse(true); } else { r.selectNodeContents(p); r.collapse(false); }
  const g = W.getSelection();
  g.removeAllRanges();
  g.addRange(r);
};
const count = s => { const a = content().textContent || ''; let n = 0, i = 0; if (!s) return 0; while ((i = a.indexOf(s, i)) >= 0) { n++; i += s.length; } return n; };
const pasteIn = async (html, plain, probe) => {
  const ps = body_(), before = count(probe), imgs = D.querySelectorAll('.se-component.se-image').length;
  setSel(ps[ps.length - 1]);
  const dt = new W.DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', plain);
  host().dispatchEvent(new W.ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
  const ok = await wait(() => probe ? count(probe) > before : D.querySelectorAll('.se-component.se-image').length > imgs, 6000);
  await S(500);
  return !!ok;
};
const shut = () => {
  const x = vis(['.se-popup-oglink .se-popup-close-button', '.se-popup-oglink button[class*="close"]', '.se-popup-oglink button[class*="cancel"]']);
  if (x) press(x);
};
// 정렬 (주인 10/29 '가운데 정렬해야 하는 글들도 있잖아'): 위 도구줄 정렬 단추는 커서가 있는 글 덩어리(방금 붙여 넣은 조각)에 먹는다.
// 조각을 붙인 뒤 원래 정렬(가운데 줄이 반 넘으면 가운데)과 다르면 그 단추를 한 번 누른다 — 가운데 상태에서 넣은 구분선은 저절로 가운데.
const MAIN_CENTER = ["button[data-value='center']", "button[class*='align-center']"];
const centered = p => !!p && (/align-center/.test(((p.closest('.se-section,.se-component') || p).className) + ' ' + p.className) || W.getComputedStyle(p).textAlign === 'center');
const lastPara = () => { const ps = body_().filter(p => !p.closest('.se-quotation') && txt(p)); return ps[ps.length - 1]; };
// 10/29 아이패드: 붙여넣기는 가운데 정렬을 그대로 살린다 — 가운데가 빠졌을 때만 누르고, 왼쪽으로 바꾸는 일은 하지 않는다
// (사파리가 복사 글을 바꿔 써서 '원래 정렬'을 잘못 읽을 수 있음 — 그때 왼쪽으로 바꾸면 가운데 글이 망가짐)
const align = async want => {
  const p = lastPara();
  if (want !== 'center' || !p || centered(p)) return true;
  const b = vis(MAIN_CENTER);
  if (!b) return false;
  press(b);
  await S(400);
  return centered(lastPara());
};
// 링크 카드: 툴바 링크 단추 → 주소 → 검색 → 미리보기 → 확인 (카드는 맨 끝 = 바로 위 주소 줄 다음)
const card = async u => {
  const before = cards();
  const b = vis(["button[data-name='oglink']", 'button.se-oglink-toolbar-button', "button[class*='oglink']"]);
  if (!b) return '링크 단추 없음';
  press(b);
  const i = await wait(() => vis(['.se-popup-oglink input', "input[class*='oglink']", "input[placeholder*='URL']", "input[placeholder*='url']", "input[placeholder*='링크']"]), 3000);
  if (!i) return '주소 칸 없음';
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
  if (!pv) { shut(); return '미리보기 없음'; }
  const c = vis(['.se-popup-oglink .se-popup-button-confirm', 'button.se-popup-button-confirm']);
  if (!c) { shut(); return '확인 단추 없음'; }
  press(c);
  if (!await wait(() => cards() > before, 8000)) { shut(); return '카드 안 생김'; }
  await S(400);
  return '';
};
const LINE_OPENERS = ["button[data-name='horizontal-line'] + button", "button[data-name='horizontal-line-select']", '.se-toolbar-item-horizontal-line button.se-document-toolbar-select-option-button', "[class*='horizontal'] button[class*='select']"];
const LINE_DEFAULT = ["button[data-value='default'][data-name*='horizontal']", "[class*='horizontal'] button[data-value='default']", "button[data-value='horizontal-line-default']", "button[class*='horizontal-line-default']", "button[data-value='default']"];
const QUOTE_OPENERS = ["button[data-name='quotation'] + button", "button[data-name='quotation-select']", '.se-toolbar-item-quotation button.se-document-toolbar-select-option-button', "[class*='quotation'] button[class*='select']", "[class*='quotation'] button[class*='arrow']"];
const QUOTE_POSTIT = ["button[data-value='quotation_postit']", "button[class*='quotation_postit']", "[data-value*='postit']", "button[class*='postit']"];
const short = e => /(^|\s)se-l-default(\s|$)/.test(e.className);
const mid = e => /align-center/.test((e.querySelector('.se-section') || e).className);
const postit = e => /se-l-quotation_postit/.test(e.className);
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
  await S(400);
  return true;
};
// 구분선: 툴바로 넣기만 한다. 정렬은 그때 쓰던 정렬을 따른다 (가운데 글 = 가운데 선 · 왼쪽 글 = 네이버 기본 왼쪽 선).
// 10/29 아이패드 조사: 선을 넣은 뒤 선만 고르는 작은 도구줄은 스크립트로 잡히지 않고, 위 정렬 단추는 앞 글 덩어리까지 바꿈.
const rule = async () => {
  const before = lines();
  if (!await choose(LINE_OPENERS, LINE_DEFAULT)) return ['', '단추 없음'];
  const e = await wait(() => newOf(lines, before), 3000);
  if (!e) return ['', '안 생김'];
  if (!short(e)) R.notes.push('선 모양 ' + (e.className.match(/se-l-[\w-]+/) || [''])[0]);
  return [e, ''];
};
// 글 맨 아래 '본문 추가' 칸 (10/29 아이패드 조사: button.se-canvas-bottom-button) — 누르면 맨 끝에 새 글 줄 → 포스트잇에서 빠져나옴
const BOTTOM = ['button.se-canvas-bottom-button', "button[class*='canvas-bottom']", "button[class*='edge-area']"];
// 포스트잇: 툴바로 새 포스트잇(맨 끝 · 안에 커서) → 글 붙여넣기 → 맨 아래 '본문 추가'로 포스트잇 다음 새 줄
const quote = async b => {
  const before = quotes();
  if (!await choose(QUOTE_OPENERS, QUOTE_POSTIT)) return '단추 없음';
  const q = await wait(() => newOf(quotes, before), 3000);
  if (!q) return '안 생김';
  await S(300);
  const probe = (b.lines[0] || '').slice(0, 20);
  await pasteIn(b.html, b.lines.join('\n'), probe);
  const inside = probe && q.textContent.includes(probe);
  R.q.push('글 ' + (inside ? '안' : '밖'));
  const bt = vis(BOTTOM);
  if (bt) press(bt);
  const out = await wait(() => { const cs = comps(), l = cs[cs.length - 1]; return l && l !== q && !!(q.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING) && /se-text\b/.test(l.className); }, 2500);
  R.q.push(out ? '나옴' : '못 나옴' + (bt ? '' : ' (본문 추가 칸 없음)'));
  await S(300);
  return inside ? (out ? '' : '포스트잇에서 못 나옴') : '글이 안 들어감';
};
const tagIn = () => {
  for (const d of [document, D]) for (const e of d.querySelectorAll("input[class*='tag_input'],input[placeholder*='태그']")) if (e.offsetParent !== null) return e;
  return null;
};
const pubBtn = () => {
  for (const d of [document, D]) for (const e of d.querySelectorAll("button[class*='publish_btn']")) if (e.offsetParent !== null && !e.closest("[class*='layer']")) return e;
  return null;
};
const chips = t => {
  let box = tagIn();
  for (let k = 0; box && k < 6 && !(box.innerText || '').includes('#'); k++) box = box.parentElement;
  return !!box && (box.innerText || '').replace(/\s/g, '').includes('#' + t);
};
const tagsIn = async tags => {
  let inp = tagIn();
  if (!inp) {
    const b = pubBtn();
    if (!b) return [0, '발행 단추 없음'];
    press(b);
    inp = await wait(tagIn, 4000);
    if (!inp) return [0, '태그 칸 없음'];
  }
  let n = 0;
  for (const t of tags) {
    if (chips(t)) { n++; continue; }
    inp = tagIn() || inp;
    const IW = inp.ownerDocument.defaultView;
    inp.focus();
    Object.getOwnPropertyDescriptor(IW.HTMLInputElement.prototype, 'value').set.call(inp, t);
    inp.dispatchEvent(new IW.Event('input', {bubbles: true}));
    await S(150);
    const o = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true};
    for (const ty of ['keydown', 'keypress', 'keyup']) inp.dispatchEvent(new IW.KeyboardEvent(ty, o));
    if (await wait(() => chips(t) || !(tagIn() || inp).value, 1500)) n++;
    await S(200 + Math.round(Math.random() * 200));
  }
  const left = (tagIn() || inp).value;
  return [n, left ? '남은 글 ' + left : ''];
};
const tagsOf = t => {
  const bits = t.slice(1).replace(/[^\u200c\u200d]/g, '');
  const bytes = new Uint8Array(Math.floor(bits.length / 8));
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8).replace(/\u200c/g, '0').replace(/\u200d/g, '1'), 2);
  try { return new TextDecoder('utf-8').decode(bytes).split(/[\s,#]+/).filter(Boolean); } catch (e) { return []; }
};
// 붙여 넣은 HTML → 조각 [{k: text|rule|card|quote|tags}] (사파리가 감싼 div·span 은 풀어서 문단만)
const BLOCK = /^(P|BLOCKQUOTE|HR|H[1-6]|UL|OL|TABLE|FIGURE|PRE)$/;
const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[c]);
const leaves = (el, out) => {
  for (const n of el.childNodes) {
    if (n.nodeType === 3) { if (n.data.trim()) out.push({tagName: 'P', text: n.data, html: '<p>' + esc(n.data) + '</p>'}); continue; }
    if (n.nodeType !== 1 || /^(META|STYLE|SCRIPT|TITLE|LINK)$/.test(n.tagName)) continue;
    if (BLOCK.test(n.tagName) || !n.querySelector('p,blockquote,hr,h1,h2,h3,h4,h5,h6,ul,ol,table,figure,pre,div')) out.push({tagName: n.tagName, text: n.textContent || '', html: n.outerHTML, el: n});
    else leaves(n, out);
  }
  return out;
};
const pieces = (html, plain) => {
  const out = [];
  let chunk = [];
  const clean = t => t.replace(/[\u2063\u2064\u200c\u200d\u200b\ufeff]/g, '').trim();
  const mid_ = x => /text-align:\s*center/i.test(x.html.slice(0, 200)) || (!!x.el && ((x.el.getAttribute('align') || '').toLowerCase() === 'center' || [...x.el.classList].some(c => midCls.has(c))));
  const flush = () => {
    if (!chunk.length) return;
    const full = chunk.filter(x => clean(x.text));
    out.push({k: 'text', html: chunk.map(x => x.html).join(''), lines: full.map(x => clean(x.text)), align: full.length && full.filter(mid_).length * 2 > full.length ? 'center' : 'left'});
    chunk = [];
  };
  let ls = [];
  const midCls = new Set();
  if (html) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    for (const st of doc.querySelectorAll('style')) for (const m of (st.textContent || '').matchAll(/\.([\w-]+)[^{]*\{[^}]*text-align:\s*center/g)) midCls.add(m[1]);
    ls = leaves(doc.body, []);
  }
  else ls = (plain || '').split(/\r?\n/).map(t => ({tagName: 'P', text: t, html: t.trim() ? '<p>' + esc(t) + '</p>' : '<p><br></p>'}));
  for (const n of ls) {
    const t = (n.text || '').replace(/[\u200b\ufeff]/g, '').trim();
    if (n.tagName === 'BLOCKQUOTE') {
      flush();
      const qs = [...n.el.querySelectorAll('p')].map(p => p.textContent.trim()).filter(Boolean);
      const lines_ = qs.length ? qs : [t];
      out.push({k: 'quote', html: lines_.map(x => '<p>' + esc(x) + '</p>').join(''), lines: lines_});
    } else if (n.tagName === 'HR' || t === ZL) { flush(); out.push({k: 'rule'}); }
    else if (t.startsWith(ZT)) { flush(); out.push({k: 'tags', tags: tagsOf(t)}); }
    else {
      chunk.push(n);
      if (isUrl(t)) { flush(); out.push({k: 'card', u: t}); }
    }
  }
  flush();
  return out;
};
// 붙여넣기 칸 (에디터 문서 안 · 바깥 페이지는 frameset 이라 거기 붙이면 안 보임 — 10/29 아이패드) — 주인이 길게 눌러 붙여넣기 → 그 HTML 을 읽음
const ask = () => new Promise(res => {
  const T = D, wrap = T.createElement('div'), box = T.createElement('div'), no = T.createElement('button'), msg = T.createElement('p');
  wrap.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:16px';
  const card_ = T.createElement('div');
  card_.style.cssText = 'background:#fff;color:#111;border-radius:16px;padding:20px;width:min(520px,100%);font:16px -apple-system,sans-serif;display:grid;gap:12px';
  msg.textContent = '꾸미기: 복사한 본문을 아래 칸에 길게 눌러 붙여넣기. 그다음 화면을 만지지 말고 기다려요.';
  msg.style.margin = '0';
  box.contentEditable = 'true';
  box.style.cssText = 'min-height:120px;border:2px dashed #03a94f;border-radius:12px;padding:12px;outline:none;color:#555';
  box.setAttribute('aria-label', '여기에 붙여넣기');
  no.textContent = '닫기';
  no.style.cssText = 'min-height:44px;border:0;border-radius:10px;background:#eee;font:600 16px -apple-system,sans-serif';
  box.addEventListener('paste', e => {
    e.stopPropagation();
    const dt = e.clipboardData;
    const h = dt ? dt.getData('text/html') : '', t = dt ? dt.getData('text/plain') : '';
    e.preventDefault();
    wrap.remove();
    res({h, t});
  }, true);
  no.addEventListener('click', () => { wrap.remove(); res(null); });
  card_.append(msg, box, no);
  wrap.append(card_);
  T.body.append(wrap);
  setTimeout(() => box.focus(), 50);
});
const kind = c => /se-horizontalLine/.test(c.className) ? '선' : /se-quotation/.test(c.className) ? (postit(c) ? '포스트잇' : '인용구') : c.matches(CARD) || c.querySelector(CARD) ? '카드' : /se-image/.test(c.className) ? '사진' : txt(c) ? '글' : '';
const shape = () => comps().map(kind).filter(Boolean).filter((k, i, a) => !(k === '글' && a[i - 1] === '글')).join(' ');
const want = ps => ps.map(p => ({text: '글', rule: '선', card: '카드', quote: '포스트잇'})[p.k]).filter(Boolean).filter((k, i, a) => !(k === '글' && a[i - 1] === '글')).join(' ');
const R = {pieces: 0, done: 0, cards: [0, 0], lines: [0, 0], quotes: [0, 0], tags: [0, 0], q: [], bad: [], notes: []};
W.__kkumiReport = R;
try { window.__kkumiReport = R; } catch (e) {}
if (!empty() && !confirm('꾸미기: 본문에 이미 글이 있어요. 그 아래에 이어서 넣을까요?')) return;
const got = await ask();
if (!got) return;
const ps = pieces(got.h, got.t);
R.pieces = ps.length;
R.want = want(ps);
if (!ps.length) { alert('꾸미기: 붙여 넣은 글이 비었어요'); return; }
W.__kkumi = 1;
try {
  for (const p of ps) {
    if (p.k === 'text') {
      const probe = (p.lines[p.lines.length - 1] || '').slice(0, 20);
      if (!await pasteIn(p.html, p.lines.join('\n'), probe)) { R.bad.push('글 안 들어감: ' + (probe || '사진')); if (!R.done) break; }
      if (!R.done && probe && [...D.querySelectorAll('.se-documentTitle,.se-section-documentTitle')].some(t => (t.textContent || '').includes(probe))) { R.bad.push('제목 칸에 들어감'); break; }
      if (probe && !await align(p.align)) R.notes.push('가운데 정렬 못 함: ' + probe);
    } else if (p.k === 'rule') {
      R.lines[1]++;
      const [, why] = await rule();
      if (why) R.bad.push('선 ' + why); else R.lines[0]++;
    } else if (p.k === 'card') {
      R.cards[1]++;
      const why = await card(p.u);
      if (why) R.bad.push('카드 ' + why); else R.cards[0]++;
    } else if (p.k === 'quote') {
      R.quotes[1]++;
      const why = await quote(p);
      if (why) R.bad.push('포스트잇 ' + why); else R.quotes[0]++;
    } else if (p.k === 'tags' && p.tags.length) {
      R.tags[1] = p.tags.length;
      R.tagList = p.tags;
    }
    R.done++;
  }
  R.shape = shape();
  if (R.tagList && R.done === ps.length) {
    const [n, why] = await tagsIn(R.tagList);
    R.tags[0] = n;
    if (why) R.bad.push('태그 ' + why);
  }
} finally { W.__kkumi = 0; }
const out = [];
const nm = (a, label) => { if (a[1]) out.push(label + ' ' + a[0] + '/' + a[1]); };
nm(R.cards, '링크 카드');
nm(R.lines, '구분선');
nm(R.quotes, '포스트잇');
nm(R.tags, '태그');
R.ok = R.shape === R.want && !R.bad.length;
R.finished = true;
alert((R.ok ? '모두 됐어요' : '확인해 주세요') + '\n' + out.join(' · ') + '\n차례: ' + R.shape + (R.ok ? '' : '\n원래: ' + R.want) + (R.q.length ? '\n포스트잇: ' + R.q.join(' → ') : '') + (R.bad.length ? '\n못 한 것:\n' + R.bad.join('\n') : '') + (R.notes.length ? '\n' + R.notes.join(' · ') : '') + (R.tags[1] ? '\n발행 설정창에서 확인 뒤 발행' : ''));
})().catch(e => alert('꾸미기 오류: ' + e));
