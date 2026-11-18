/* 꾸미기 책갈피 v9 (v9: 11/18 다음 날 예약 날짜 채움 · v8: 11/17 포스트잇 빠져나오기·안 확인 다시 찾기 · v7: 11/16 글 바로 다음 포스트잇도 안에 · v4: 주인 10/29 아이패드 시험 뒤 — 위에서부터 차례로 쌓기 · v6: 11/14 예약 날짜·시각 채움 — 발행 단추는 누르지 않음). 네이버 블로그 글쓰기(아이패드 사파리)에서 본문을 한 번 누른 뒤 누르면:
   작은 칸이 뜨고 → 주인이 대시보드에서 복사한 본문을 그 칸에 붙여 넣으면 → 글을 위에서부터 조각마다 에디터에 붙여 넣으며
   구분선 자리(\u2063 만 든 빈 줄)에는 짧은 가운데 구분선 · 주소만 있는 줄 다음엔 링크 카드 · 인용구는 포스트잇 · 끝의 태그 줄(\u2064)은 발행 설정창 태그 칸에.
   아이패드 시험(10/29): 스크립트로 커서를 옮기는 방법 6가지 모두 안 됨 (넣은 것은 늘 글 맨 끝) · 스크립트 붙여넣기 신호는 됨 (맨 끝에).
   그래서 커서를 옮기지 않고 맨 끝에 차례로 쌓는다. 바깥 요청 없음. 끝에 알림 한 번 (부품 차례 · 못 한 것). 결과는 window.__kkumiReport 에도.
   딜봇 kkumi.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — 줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). */
(async () => {
const S = m => new Promise(r => setTimeout(r, m));
// 칸 고르기 (v9 · 책갈피 주소를 짧게)
const qa = (e, q) => [...e.querySelectorAll(q)];
const look = d => {
  if (d.querySelector('.se-content')) return d;
  for (const f of qa(d, 'iframe,frame')) {
    try { const x = f.contentDocument, r = x && look(x); if (r) return r; } catch (e) {}
  }
  return null;
};
const D = look(document);
if (!D) { alert('꾸미기: 글쓰기 화면이 아니에요'); return; }
const W = D.defaultView;
if (W.__kkumi) { alert('꾸미기: 이미 도는 중'); return; }
// 판 번호 (11/14 — 끝 알림에 'v6' · 대시보드가 '책갈피가 바뀌었어요'를 알릴 때 같이 보임 · 고칠 때마다 올림)
const V = 9;
const ZL = '\u2063', ZT = '\u2064';
const CARD = '.se-oglink,.se-module-oglink,[class*="se-component-oglink"]';
const cards = () => qa(D, CARD).length;
const raw = p => (p.textContent || '').replace(/[\u200b\ufeff]/g, '');
const txt = p => raw(p).replace(/[\u2063\u2064\u200c\u200d]/g, '').trim();
const isUrl = t => /^https?:\/\/[^\s]+$/i.test(t);
const seen = e => e.offsetParent !== null;
const vis = q => {
  for (const s of q) for (const e of qa(D, s)) if (seen(e)) return e;
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
const nImg = () => qa(D, '.se-component.se-image').length;
const content = () => D.querySelector('.se-content') || D.body;
const host = () => content().closest('[contenteditable="true"]') || content().querySelector('[contenteditable="true"]') || D.querySelector('[contenteditable="true"]');
const TT = '.se-documentTitle,.se-section-documentTitle', inTitle = e => !!e.closest(TT);
const body_ = () => qa(D, '.se-text-paragraph').filter(p => !inTitle(p));
const comps = () => qa(content(), '.se-component').filter(c => !inTitle(c) && !c.parentElement.closest('.se-component'));
const lines = () => qa(D, '.se-component.se-horizontalLine');
const quotes = () => qa(D, '.se-component.se-quotation');
const newOf = (list, before) => list().find(x => !before.includes(x)) || null;
// 빈 본문의 안내 글(글감과 함께 …)은 placeholder 칸이라 글로 세지 않음 (10/29 아이패드)
const real = c => { const k = c.cloneNode(true); qa(k, '[class*="placeholder"]').forEach(x => x.remove()); return txt(k); };
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
// 글자 비교는 빈칸을 하나로 (11/14 라커룸 '글 안 들어감' — 에디터가 꾸민 숫자 앞 빈칸을 &nbsp; 로 바꿔 '타율 0.322' 를 못 찾음)
const sp=t=>(t||'').replace(/\s+/g,' ');
// v8: 포스트잇 안 확인은 빈칸·&nbsp;·안 보이는 글자를 모두 빼고 비교
const tz=t=>(t||'').replace(/[\s\u200b-\u200d\u2060-\u2064]/g,'');
const count = s_ => { const a=sp(content().textContent),s=sp(s_); let n = 0, i = 0; if (!s.trim()) return 0; while ((i = a.indexOf(s, i)) >= 0) { n++; i += s.length; } return n; };
// at: 붙일 칸 (없으면 본문 끝 줄)
const pasteIn = async (html, plain, probe, at) => {
  const ps = body_(), before = count(probe), imgs = nImg();
  setSel(at || ps[ps.length - 1]);
  const dt = new W.DataTransfer();
  dt.setData('text/html', html);
  dt.setData('text/plain', plain);
  host().dispatchEvent(new W.ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
// v8 (11/17 픽 글 '글 안 들어감: 사진' 14번): 사진 사이 빈 줄만 든 조각은 글도 사진도 없어 기다릴 것이 없음 → 붙이고 넘어감 (거짓 알림이었음)
  const ok = probe || /<img/i.test(html) ? await wait(() => probe ? count(probe) > before : nImg() > imgs, 6000) : 1;
  await S(500);
  return !!ok;
};
// 사진은 받아서 파일로 붙여 네이버에 올림 (주인 11/09 — 주소로 붙이면 서버 사진을 불러 써 대표 사진이 안 됨). 안 되면 예전처럼 주소로.
// 받는 곳은 우리 공개 사진(/media/blog_*.jpg)만 · 쿠키 없이
const PIC = /^https:\/\/[\w.-]+\/media\/blog_[a-z]+_\d+_\d+_[0-9a-f]{8}\.jpg$/;
const pic = async u => {
  const n = nImg();
  try {
    if (!PIC.test(u)) throw 0;
    const b = await (await fetch(u, {credentials: 'omit'})).blob(), dt = new W.DataTransfer(), me = u.split('/')[2];
    dt.items.add(new W.File([b], 'p.jpg', {type: 'image/jpeg'}));
    setSel(body_().slice(-1)[0]);
    host().dispatchEvent(new W.ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
    if (await wait(() => { const a = qa(D, '.se-component.se-image img'), i = a[a.length - 1]; return a.length > n && /^https:/.test(i.src) && !i.src.includes(me); }, 30000)) return '';
  } catch (e) {}
  return await pasteIn('<p><img src="' + esc(u).replace(/"/g, '%22') + '"></p>', '', '') ? 'url' : 'x';
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
// 제목 가운데 (주인 11/06 '제목은 원래 가운데였잖아' — 서버가 올릴 땐 맞췄음): 본문이 가운데 글이면 끝에 제목 줄을 누르고 가운데 단추
const titleMid = async () => {
  const t = qa(D, '.se-text-paragraph').find(inTitle);
  if (!t || !txt(t)) return '제목 없음';
  if (centered(t)) return '';
  press(t); setSel(t); await S(400);
  const b = vis(MAIN_CENTER);
  if (!b) return '정렬 단추 없음';
  press(b); await S(500);
  return centered(t) ? '' : '못 함';
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
  put(i, u);
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
  const probe = (b.lines[0] || '').slice(0, 20), P = tz(probe), i = R.quotes[1];
// 11/16 드라마 표 인용구 (앞에 선 없이 글 다음): 본문 끝 줄이 포스트잇 밖(뒤 빈 줄)일 수 있음 → 포스트잇 안 칸을 눌러 거기에 붙임
// 빈칸 비교는 sp (숫자 앞 &nbsp;) · 어디에도 안 들어갔으면 한 번 더 (밖에 들어갔으면 두 번 넣지 않음)
// v8 (11/17 '글 밖' 잘못 봄): 붙인 뒤 에디터가 포스트잇을 새로 그릴 수 있어 새 포스트잇을 다시 찾아 tz 로 비교 · 1.5초까지 기다림
  const Q = () => q.isConnected ? q : newOf(quotes, before) || q, has = () => tz(Q().textContent).includes(P);
  const at = q.querySelector('.se-quote .se-text-paragraph,.se-text-paragraph'), n = count(probe);
  for (let k = 0; k < 2 && !(k && (!P || has() || count(probe) > n)); k++) { if (at) press(at); await pasteIn(b.html, b.lines.join('\n'), probe, at || q); }
  const inside = P && await wait(has, 1500);
  R.q.push('글 ' + (inside ? '안' : '밖'));
// 빠져나오기 (v8 · 11/17 '못 나옴'): 맨 아래 '본문 추가' → 안 되면 포스트잇 다음 글 줄을 누름 → '본문 추가' 한 번 더 · 끝 줄이 포스트잇 다음 글 줄이면 나옴 → 그 줄을 눌러 커서를 둠
  const fol = l => l && Q().compareDocumentPosition(l) & 4 && /se-text\b/.test(l.className);
  let out = null, bt = null;
  for (let k = 0; k < 3 && !out; k++) {
    const nx = k === 1 && comps().find(fol), e = nx ? nx.querySelector('.se-text-paragraph') || nx : bt = vis(BOTTOM);
    if (e) press(e);
    out = await wait(() => { const l = comps().pop(); return fol(l) && l; }, 2500);
  }
  if (out) { const op = out.querySelector('.se-text-paragraph'); if (op) press(op); }
  else R.stuck = [Q(), i];
  R.q.push(out ? '나옴' : '못 나옴' + (bt ? '' : ' (본문 추가 칸 없음)'));
  await S(300);
  return inside ? (out ? '' : i + " '" + probe + "'에서 못 나옴") : '글이 안 들어감';
};
// 바깥 문서·에디터 문서에서 보이는 첫 칸 (qs 차례대로 · f 는 더 거르기)
const any = (qs, f) => { for (const q of qs) for (const d of [document, D]) for (const e of qa(d, q)) if (seen(e) && (!f || f(e))) return e; return null; };
const tagIn = () => any(["input[class*='tag_input'],input[placeholder*='태그']"]);
const pubBtn = () => any(["button[class*='publish_btn']"], e => !e.closest("[class*='layer']"));
const chips = t => {
  let box = tagIn();
  for (let k = 0; box && k < 6 && !(box.innerText || '').includes('#'); k++) box = box.parentElement;
  return !!box && (box.innerText || '').replace(/\s/g, '').includes('#' + t);
};
// 발행창 카테고리 고르기 (서버 업로더 _select_category 와 같은 칸 · 띄어쓰기·'하위 카테고리' 말 무시하고 비교)
const nz = s => (s || '').replace(/하위\s*카테고리|카테고리|[\sㄴ└·>-]/g, '');
const catBtn = () => any(["[class*='option_category'] button", "button[aria-label*='카테고리']", "button[class*='selectbox_button']"]);
const catIn = async c => {
  let b = catBtn();
  if (!b) return '칸 없음';
  if (nz(b.innerText) === nz(c)) return '';
  // 하위 카테고리 항목은 <label><span 숨김>하위 카테고리</span>드라마 패션</label> — 글자가 자식 칸 밖에 있어 '맨 끝 칸만' 보면 못 찾음 (11/10 드라마 패션)
  // → 이름이 맞는 칸 중 가장 안쪽 (문서 순서라 뒤에 오는 자손이 덮음)
  // v9 (11/18 '목록에 없음' 뒤 예약 못 채움): 없으면 연 목록을 닫고(목록 칸 수가 늘었을 때만) 예약으로 넘어감
  const ls = () => { let f = null, n = 0; for (const d of [document, D]) for (const e of qa(d, '[class*=category] *,[role=option]')) if (seen(e) && e !== b && !b.contains(e) && !e.contains(b)) { n++; if (nz(e.innerText) === nz(c)) f = e; } return [f, n]; }, n0 = ls()[1];
  press(b);
  const it = await wait(() => ls()[0], 3000);
  if (!it) { if (ls()[1] > n0) { press(b); await S(600); } return '목록에 없음 (' + c + ')'; }
  it.scrollIntoView && it.scrollIntoView({block: 'nearest'});
  press(it);
  await S(600);
  b = catBtn();
  return b && nz(b.innerText) === nz(c) ? '' : '확인 못 함';
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
    setV(inp, t);
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
// 예약 시각 채움 (v6 · 주인 11/14 '시간 고르는 수고 없게'): 발행 설정창에서 '예약'을 고르고 날짜·시·분을 채운 뒤 멈춘다 — 발행 단추는 주인이 직접.
// 발행창 예약 칸은 로그인 없이 볼 수 없어 글자('현재'·'예약')와 모양(0~23 고르기 칸 · 분 고르기 칸 · 'YYYY. M. D' 날짜 칸 · 달력 표)으로 찾는다.
// 하나라도 못 찾거나 확인이 안 되면 고르기 칸을 되돌리고 '현재'로 돌려놓은 뒤 안내만 (꾸미기 본 일은 그대로). 클릭 사이는 사람처럼 0.4~0.8초.
// try 안에서는 못 찾은 것(null·undefined)을 그대로 써서 나는 오류도 '못 찾음'으로 친다 (책갈피 주소를 짧게).
const p2 = n => (n < 10 ? '0' : '') + n;
const num = o => parseInt(/\d+|$/.exec(o.text + ' ' + o.value)[0], 10);
const ymd = e => { const m = /(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})/.exec(e.value || e.innerText || ''); return m ? m[1] * 372 + m[2] * 31 + m[3] * 1 : 0; };
// 발행을 끝내는 단추는 어떤 일이 있어도 누르지 않는다: 누를 것(또는 그것을 감싼 단추·링크)의 이름에 confirm·publish·submit 이 들었거나
// 종류가 submit(종류를 안 적은 단추 포함)이거나 글자에 발행·확인·등록이 있으면 멈춤
const tap = async e => {
  const b = e.closest('button,a') || e;
  if (/confirm|publish|submit/i.test(b.className + b.getAttribute('data-testid') + b.type) || /발행|확인|등록/.test(b.innerText)) throw 0;
  press(e);
  await S(400 + Math.random() * 400);
};
// 칸 값 넣기: 그 칸 문서의 원래 value 설정 (에디터·발행창이 바뀐 값을 알아채게)
const setV = (e, v) => Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value').set.call(e, v);
const put = (s, v) => {
  setV(s, v);
  for (const t of ['input', 'change']) s.dispatchEvent(new Event(t, {bubbles: true}));
};
const opt = (s, n) => {
  const o = [...s.options].find(x => num(x) === n);
  if (o) put(s, o.value);
  return !!o && num(s.selectedOptions[0]) === n;
};
const atOf = a => a.slice(1).split(/\D/).map(Number);
const atLabel = (m, mm) => m[1] + '/' + m[2] + ' ' + p2(m[3]) + ':' + p2(mm);
// v9: 날짜를 못 바꾸면 끝 알림에 '날짜 칸 못 찾음 — 11/19 07:30 직접 골라요' (예약은 건드리지 않음 — 틀린 날짜를 넣지 않게)
const DT = '날짜 칸 못 찾음';
const bookIn = async at => {
  const m = atOf(at), [y, mo, d, h] = m, want = y * 372 + mo * 31 + d, ym = y * 12 + mo, late = n => new Date(y, mo - 1, d, h, n) - Date.now() < 3e5;
  if (late(m[4])) return '지난 시각';
  const radio = (b, t) => qa(b, 'label,[role=radio]').find(e => seen(e) && e.innerText.replace(/\s/g, '') === t);
  // v9 (11/18 다음 날 예약 '칸 못 찾음'): 날짜를 고르면 발행창이 칸을 새로 그릴 수 있어 예약 칸·고르기 칸은 볼 때마다 새로 찾는다
  const find = () => { let b = tagIn(), r; for (let k = 0; b && !r && k < 12; k++) { b = b.parentElement; r = b && radio(b, '예약'); } return r && [b, r]; };
  const on = e => e.control ? e.control.checked : e.getAttribute('aria-checked') === 'true';
  let [box, r1] = find() || [], old, why = '칸 못 찾음';
  if (!r1) return '예약 칸 없음';
  const was = on(r1);
  // [시 고르기 칸, 분 고르기 칸, 날짜 칸] — '예약'이 골라져 있고 셋 다 보일 때만
  const sels = () => {
    let H, M;
    [box, r1] = find() || [D.body, D.body];
    for (const s of qa(box, 'select')) {
      const n = [...s.options].map(num), x = Math.max(...n);
      if (!seen(s)) continue;
      if (!H && n.length === 24 && x === 23) H = s; else if (H && !M && x > 29 && x < 60) M = s;
    }
    const D_ = qa(box, 'input,button').find(e => seen(e) && ymd(e));
    return H && M && D_ && on(r1) && [H, M, D_];
  };
  try {
    if (!was) await tap(r1);
    let [H, M, D_] = await wait(sels, 2500), s;
    const mm = Math.max(...[...M.options].map(num).filter(n => n <= m[4]));
    // 분을 내린 시각이 이미 지났으면(5분 안) 넣지 않는다
    if (late(mm)) { why = '지난 시각'; throw 0; }
    old = [H, H.value, M, M.value];
    // 날짜: 이미 그 날이면 그대로 · 아니면 달력을 열어 다음 달 단추(두 번까지)·그 날을 누르고 날짜 칸이 그 날로 바뀐 것을 본다
    // v9: 달력은 발행창 밖(문서 맨 끝)에 붙을 수 있음 · 눌러도 안 뜨면 한 번 더 · 날짜 칸을 다시 찾아 확인 — 안 되면 '날짜 칸 못 찾음'
    if (ymd(D_) !== want) {
      why = DT;
      const cal = () => {
        for (const t of qa(box.ownerDocument, 'table,[role=grid]')) {
          // 본문에 넣은 표는 달력이 아님
          if (!seen(t) || t.closest('.se-content') || qa(t, 'td,[role=gridcell]').length < 28) continue;
          for (let c = t, k = 0; k < 4 && (c = c.parentElement); k++) {
            const a = /(\d{4})\D{1,3}(\d{1,2})/.exec(c.innerText.replace(t.innerText, ''));
            if (a) return [t, c, a[1] * 12 + a[2] * 1];
          }
        }
      };
      let c;
      for (let k = 0; !c && k < 2; k++) { D_ = sels()[2]; D_.focus(); await tap(D_); c = await wait(cal, 2500); }
      for (let k = 0; c[2] < ym && k < 2; k++) {
        const was_ = c[2];
        await tap(qa(c[1], 'button,a').find(e => seen(e) && /next|다음/i.test(e.outerHTML)));
        c = await wait(() => { const x = cal(); return x && x[2] > was_ && x; }, 2000);
      }
      // 고를 수 없는 날(지난날·다른 달·막힌 날)은 칸 안 어디에든 other·disabled·outside·unselectable 이 있음
      const ds = qa(c[0], 'td,[role=gridcell]').filter(e => e.innerText.trim() === '' + d && !/other|disabled|outside|unselect/i.test(e.outerHTML));
      if (c[2] !== ym || ds.length !== 1) throw 0;
      await tap(ds[0].querySelector('button,a') || ds[0]);
      if (!await wait(() => (s = sels()) && ymd(s[2]) === want, 2500)) throw 0;
      [H, M] = s;
      why = '칸 못 찾음';
    }
    if (!opt(H, h) || !opt(M, mm)) throw 0;
    await S(300);
    s = sels();
    if (ymd(s[2]) !== want || num(s[0].selectedOptions[0]) !== h || num(s[1].selectedOptions[0]) !== mm) throw 0;
    if (mm !== m[4]) R.notes.push('분 내림');
    R.at = atLabel(m, mm);
    return '';
  } catch (e) {
    // 되돌리기: 고르기 칸 값 → 원래대로 · 우리가 고른 '예약' → '현재' (그러면 날짜는 쓰이지 않음)
    try { if (old) { put(old[0], old[1]); put(old[2], old[3]); } sels(); if (!was && on(r1)) press(radio(box, '현재')); } catch (e2) {}
    return why;
  }
};
const tagsOf = t => {
  const bits = t.slice(1).replace(/[^\u200c\u200d]/g, '');
  const bytes = new Uint8Array(Math.floor(bits.length / 8));
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(bits.slice(i * 8, i * 8 + 8).replace(/\u200c/g, '0').replace(/\u200d/g, '1'), 2);
  // 카테고리는 맨 앞 '\u0001' 앞에 (11/09 — 발행창 카테고리가 늘 지난번 것으로 남음)
  // v6: 카테고리와 태그 사이에 예약 시각 '@2026-11-15T19:00' 이 올 수 있음 (딜봇 kkumi.stamp — 옛 책갈피는 그 칸을 안 읽음)
  try { const a = new TextDecoder('utf-8').decode(bytes).split('\u0001'), t_ = a.pop(); return {cat: a[0] || '', at: a[1] || '', tags: t_.split(/[\s,#]+/).filter(Boolean)}; } catch (e) { return {cat: '', at: '', tags: []}; }
};
// 붙여 넣은 HTML → 조각 [{k: text|rule|card|quote|tags}] (사파리가 감싼 div·span 은 풀어서 문단만)
const BLOCK = /^(P|BLOCKQUOTE|HR|H[1-6]|UL|OL|TABLE|FIGURE|PRE)$/;
const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'})[c]);
// 인용구 줄은 글자만 다시 쓰되 글자 크기·굵게는 살림 (라커룸 소제목 19 굵게 — 주인 11/14 16:4x 벤치마킹 추천안 · Blog-Auto kkumi_v4.js qline 과 같게)
const qline = p => { const h = p.outerHTML, z = (h.match(/font-size:\s*(\d+)px/) || [])[1]; let s_ = esc(p.textContent.trim()); if (p.querySelector('b,strong') || /font-weight:\s*(bold|[6-9]00)/.test(h)) s_ = '<b>' + s_ + '</b>'; return '<p>' + (z ? '<span style="font-size:' + z + 'px">' + s_ + '</span>' : s_) + '</p>'; };
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
    for (const st of qa(doc, 'style')) for (const m of (st.textContent || '').matchAll(/\.([\w-]+)[^{]*\{[^}]*text-align:\s*center/g)) midCls.add(m[1]);
    ls = leaves(doc.body, []);
  }
  else ls = (plain || '').split(/\r?\n/).map(t => ({tagName: 'P', text: t, html: t.trim() ? '<p>' + esc(t) + '</p>' : '<p><br></p>'}));
  for (const n of ls) {
    const t = (n.text || '').replace(/[\u200b\ufeff]/g, '').trim();
    if (n.tagName === 'BLOCKQUOTE') {
      flush();
      const qp = qa(n.el, 'p').filter(p => p.textContent.trim());
      const lines_ = qp.length ? qp.map(p => p.textContent.trim()) : [t];
      out.push({k: 'quote', html: qp.length ? qp.map(qline).join('') : '<p>' + esc(t) + '</p>', lines: lines_});
    } else if (n.tagName === 'HR' || t === ZL) { flush(); out.push({k: 'rule'}); }
    else if (t.startsWith(ZT)) { flush(); out.push({k: 'tags', ...tagsOf(t)}); }
    else if (!t && n.el && (n.el.tagName === 'IMG' || n.el.querySelector('img'))) { flush(); out.push({k: 'img', u: (n.el.tagName === 'IMG' ? n.el : n.el.querySelector('img')).getAttribute('src')}); }
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
  msg.textContent = '꾸미기: 아래 칸에 길게 눌러 붙여넣고 기다려요';
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
// 사진은 글 조각 안에 같이 붙어 들어가 원래 차례(want)에 없음 — 비교에서 빼고 셈 (11/08 '원래: 글'인데 '확인해 주세요'로 뜨던 것)
const shape = () => comps().map(kind).filter(k => k && k !== '사진').filter((k, i, a) => !(k === '글' && a[i - 1] === '글')).join(' ');
const want = ps => ps.map(p => ({text: '글', rule: '선', card: '카드', quote: '포스트잇'})[p.k]).filter(Boolean).filter((k, i, a) => !(k === '글' && a[i - 1] === '글')).join(' ');
// 글자 꾸밈 세기 (주인 11/06 '형광펜·색이 하나도 안 됨'): 받은 글과 에디터에 들어간 글에서 형광펜·글자 색·굵게 수 — 어디서 빠지는지 알림에 보임
const fx = h => [/background-color/gi, /[^-]color\s*:/gi, /<(b|strong)[\s>]|font-weight:\s*(bold|[6-9]00)|class="[^"]*bold/gi].map(x => (h.match(x) || []).length);
const R = {pieces: 0, done: 0, pics: [0, 0], cards: [0, 0], lines: [0, 0], quotes: [0, 0], tags: [0, 0], q: [], bad: [], notes: []};
W.__kkumiReport = R;
try { window.__kkumiReport = R; } catch (e) {}
if (!empty() && !confirm('꾸미기: 글이 있어요. 아래에 이을까요?')) return;
const got = await ask();
if (!got) return;
const ps = pieces(got.h, got.t);
R.pieces = ps.length;
R.want = want(ps);
R.fx = [fx(ps.filter(p => p.k === 'text').map(p => p.html).join('')), [0, 0, 0]];
if (!ps.length) { alert('꾸미기: 빈 글'); return; }
W.__kkumi = 1;
try {
  for (const p of ps) {
    if (p.k === 'text') {
      const probe = (p.lines[p.lines.length - 1] || '').slice(0, 20);
      if (!await pasteIn(p.html, p.lines.join('\n'), probe)) { R.bad.push('글 안 들어감: ' + (probe || '사진')); if (!R.done) break; }
      // v8: 못 나온 포스트잇 안에 다음 글이 들어갔으면 어느 포스트잇·어떤 글인지 알림 (에디터가 옮긴 글을 되돌릴 수 있어 직접 꺼냄)
      if (R.stuck && probe && tz(R.stuck[0].textContent).includes(tz(probe))) R.bad.push('포스트잇 ' + R.stuck[1] + " 안에 든 글 '" + probe + "'");
      R.stuck = 0;
      if (!R.done && probe && qa(D, TT).some(t => (t.textContent || '').includes(probe))) { R.bad.push('제목 칸에 들어감'); break; }
      if (probe && !await align(p.align)) R.notes.push('가운데 못 함: ' + probe);
    } else if (p.k === 'img') {
      R.pics[1]++;
      const why = await pic(p.u);
      if (why === 'x') R.bad.push('사진 안 들어감'); else if (why) R.notes.push('사진 주소로'); else R.pics[0]++;
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
    } else if (p.k === 'tags') {
      R.tags[1] = p.tags.length;
      R.tagList = p.tags;
      R.cat = p.cat;
      if (/^@\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(p.at)) { const a = atOf(R.book = p.at); R.at = atLabel(a, a[4]); R.why = '글 덜 들어감'; }
    }
    R.done++;
  }
  R.shape = shape();
  R.fx[1] = fx(body_().map(p => p.innerHTML).join(''));
  if (ps.some(p => p.align === 'center')) { R.title = await titleMid(); if (R.title) R.notes.push('제목 가운데 ' + R.title + ' — 직접'); }
  if (R.tagList && R.done === ps.length) {
    const [n, why] = await tagsIn(R.tagList);
    R.tags[0] = n;
    if (why) R.bad.push('태그 ' + why);
    if (R.cat) { const w = await catIn(R.cat); if (w) R.bad.push('카테고리 ' + w); else R.catOk = 1; }
    if (R.book) R.why = await bookIn(R.book);
  }
} finally { W.__kkumi = 0; }
const out = [];
const nm = (a, label) => { if (a[1]) out.push(label + ' ' + a[0] + '/' + a[1]); };
nm(R.pics, '사진');
nm(R.cards, '링크 카드');
nm(R.lines, '구분선');
nm(R.quotes, '포스트잇');
nm(R.tags, '태그');
if (R.catOk) out.push('카테고리 ' + R.cat);
const fs = a => `형광펜 ${a[0]}·색 ${a[1]}·굵게 ${a[2]}`;
if (R.fx[0].some(Boolean)) { out.push('꾸밈 ' + fs(R.fx[0]) + ' → ' + fs(R.fx[1])); if (R.fx[0][0] && !R.fx[1][0]) R.notes.push('형광펜 빠짐'); }
else R.notes.push('꾸밈 없음');
R.ok = R.shape === R.want && !R.bad.length;
R.finished = true;
alert((R.ok ? '모두 됐어요' : '확인해 주세요') + '\n' + out.join(' · ') + '\n차례: ' + R.shape + (R.ok ? '' : '\n원래: ' + R.want) + (R.q.length ? '\n포스트잇: ' + R.q.join(' → ') : '') + (R.bad.length ? '\n못 한 것:\n' + [...new Set(R.bad)].map(x => (n => n > 1 ? x + ' ×' + n : x)(R.bad.filter(y => y === x).length)).join('\n') : '') + (R.notes.length ? '\n' + R.notes.join(' · ') : '') + (R.book ? '\n' + (R.why === DT ? DT + ' — ' + R.at + ' 직접 골라요' : '예약 ' + R.at + (R.why ? ' 못 채움 (' + R.why + ') — 직접 골라요' : ' 채움 — 발행은 직접')) : '') + (R.tags[1] ? '\n발행창에서 확인 뒤 발행' : '') + '\nv' + V);
})().catch(e => alert('꾸미기 오류: ' + e));
