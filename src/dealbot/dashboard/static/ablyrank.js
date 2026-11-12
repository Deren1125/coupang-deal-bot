/* 'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 서버는 에이블리를 열지 않음 · 10/28 원칙 그대로). 주인이 아이패드 사파리·갤럭시 크롬에서
   에이블리 랭킹 화면(a-bly.com · m.a-bly.com …)을 연 채 누르면, 보이는 순위 카드(순위·상품명·쇼핑몰·가격·할인·리뷰 수·별점·찜, 상품 주소·사진 주소)와
   코디 카드(코디 제목·좋아요·든 상품), 주소·제목·고른 탭, 보이는 글을 대시보드 /dashboard/api/ably_rank_capture 로 보낸다.
   쿠키는 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만. 바로 보내지 못하면 이 창에서 폼으로 보낼지 묻는다.
   딜봇 ablyrank.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — statscap.js 와 같은 규칙:
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). 카드가 아직 안 떴으면 0.5초마다 최대 8초 기다린다.
   11/12 코디 랭킹(mobile.a-bly.com/content/style/home 랭킹 탭 — 링크·가격 없는 사진 카드)이 0개로 와서: 사진 왼쪽 위 순위 딱지('1 –'·'3 9↑')에서
   카드까지 올라가 순위·변동·사진·닉네임·해시태그·바로입기·카드 링크를 코디(looks)로, 몇 화면 내려 더 모은 뒤 제자리로. 그림자 DOM·같은 출처 iframe 도 본다.
   카드 링크가 같은 출처면 그 화면을 이 브라우저의 숨은 iframe 으로 열어(최대 40개 · 90초 — 주인 11/12 "10개로 제한 안해도 돼") 가로 상품 줄을 읽는다 — 서버는 그대로 에이블리를 열지 않음.
   11/12 두 번째: 그래도 0개(글은 위 띠만)로 와서 dbg(화면 모양 2KB)를 늘 함께 보내고, 사진 6장 넘게 뜰 때까지 기다린다. 알림은 '코디 N · 상품 M'.
   11/12 세 번째(dbg): mobile.a-bly.com 은 껍데기고 화면은 다른 출처 m.a-bly.com iframe 안 → 그 주소로 옮길지 묻고 거기서 한 번 더.
   m.a-bly.com 코디 화면에서는 위 탭(PICK·추천·랭킹·데일리)을 차례로 눌러 탭마다 카드를 모은다 (각 코디에 tab).
   11/12 네 번째('코디 100 · 상품 0' — 카드에 링크가 없고 누르면 앱 안에서 상세가 뜸): 코디 안 상품을 세 길로 — ① 화면 안 데이터(Next·React 캐시·카드 props)에
   코디별 상품 묶음이 있으면 그것 ② 같은 출처 링크면 숨은 iframe ③ 링크가 없으면 카드를 눌러 상세(요즘코디)를 띄우고 가로 상품 줄(이름·할인·가격·사진·상품 번호)을 읽고
   뒤로 (5초 안에 안 돌아오면 코디 주소로 다시 · 탭 다시 누름 · 스크롤 제자리). 상세는 탭 합쳐 40개(랭킹 1~20위 먼저) · 처음부터 150초 · 띠 '상세 7/40 · 상품 52'.
   상세를 여는 사이 화면이 통째로 넘어가면(pagehide) 모은 것을 그때까지라도 보낸다 (keepalive). */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', W = window, D = document;
if (W.__abcap) { alert('이미 모으는 중이에요'); return; }
if (!/(^|\.)a-bly\.com$/.test(location.hostname)) { alert('에이블리(a-bly.com)에서 눌러 주세요'); return; }
W.__abcap = 1;
// 진행 띠 (주인 11/12 '눌렀는지 모르겠어요'): 위에 크게 · 그림자 DOM 안이라 읽는 글·카드에 안 섞임 (data-abcap 은 건너뜀)
let ov = null, ot = null, bad = 0;
const say = m => { try { if (!ov) { ov = D.createElement('div'); ov.setAttribute('data-abcap', '1'); ov.style.cssText = 'position:fixed;inset:0 0 auto;z-index:2147483647;background:#e8344e;color:#fff;padding:14px;font:bold 18px sans-serif;text-align:center'; ot = ov.attachShadow ? ov.attachShadow({mode: 'open'}) : ov; D.documentElement.appendChild(ov); } ot.textContent = m; } catch (e) {} };
const bye = () => { if (ov) ov.remove(); ov = null; };
say('카드 찾는 중 (0초)');
try {
const SEL = '[aria-selected=true],[aria-current=page],[aria-checked=true],.on,.active,.selected';
const GOODS = /\/goods\/(\d{2,14})/;
const LOOK = /\/(coordi|codi|look|style)s?\/([\w-]{1,40})/i;
const LOAD = /불러오는|로딩\s*중|^loading/i;
const RANK = /^(\d{1,3})(위)?$/;
const PCT = /(\d{1,2})%/;
const PRICE = /\d{1,3}(,\d{3})+(?=원|\s|$)|\d{4,8}(?=원)/g;
const REV = /(리뷰|후기|review)\s?\(?([\d,.]+[만천kK]?\+?)\)?/i;
const STAR = /(★|⭐|별점)\s?([0-5](\.\d)?)/;
const LIKE = /(찜|좋아요|♥|하트)\s?([\d,.]+[만천kK]?\+?)/;
const BADGE = /^(무료배송|오늘출발|광고|new|best|hot|ad|\d*%?쿠폰)$/i;
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/[\t\x20]+/g, ' ');
const lines = n => tx(n).split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(l => l && !(l.length < 40 && LOAD.test(l)));
const abs = h => { try { return new URL(h, location.href).href; } catch (e) { return ''; } };
const idOf = (h, rx) => { const m = rx.exec(h || ''); return m ? m[m.length - 1] : ''; };
const href = a => abs(a.getAttribute('href') || a.href || '');
const pic = c => {
  for (const i of [...c.querySelectorAll('img')].slice(0, 6)) {
    const s = abs(i.currentSrc || i.getAttribute('src') || i.getAttribute('data-src') || i.getAttribute('data-original') || '');
    if (/^https?:/.test(s)) return s.slice(0, 600);
  }
  return '';
};
// 카드 글 → 순위·할인·가격·리뷰·별점·찜 · 남은 글 줄 = 쇼핑몰·상품명
const parse = (c, url) => {
  const o = {url: url, img: pic(c)}, rest = [], prices = [];
  for (const l0 of lines(c).slice(0, 30)) {
    let l = l0, m;
    if (o.rank == null && (m = RANK.exec(l))) { o.rank = +m[1]; continue; }
    if ((m = REV.exec(l))) { o.rev = m[2]; l = l.replace(m[0], ' '); }
    if ((m = STAR.exec(l))) { o.star = +m[2]; l = l.replace(m[0], ' '); }
    if ((m = LIKE.exec(l))) { o.like = m[2]; l = l.replace(m[0], ' '); }
    if (o.star == null && /^[0-5]\.\d$/.test(l.trim())) { o.star = +l.trim(); continue; }
    if ((m = PCT.exec(l))) { if (o.disc == null) o.disc = +m[1]; l = l.replace(m[0], ' '); }
    l = l.replace(PRICE, v => { prices.push(+v.replace(/\D/g, '')); return ' '; }).replace(/원/g, ' ').replace(/\s+/g, ' ').trim();
    if (l.length < 2 || BADGE.test(l) || !/[가-힣A-Za-z]/.test(l)) continue;
    rest.push(l.slice(0, 200));
  }
  if (prices.length) { o.price = Math.min(...prices); if (Math.max(...prices) > o.price) o.orig = Math.max(...prices); }
  // 글 줄이 둘 넘으면 첫 줄 = 쇼핑몰 · 가장 긴 줄 = 상품명
  if (rest.length > 1) o.shop = rest.shift();
  if (rest.length) o.name = rest.reduce((a, b) => (b.length > a.length ? b : a));
  return o;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const css = e => { try { return (e.ownerDocument.defaultView || W).getComputedStyle(e); } catch (x) { return {}; } };
const up = e => e.parentElement || (e.parentNode && e.parentNode.host) || null;
const own = e => [...e.childNodes].filter(n => n.nodeType == 3).map(n => n.data).join('').trim();
// 그림자 DOM·같은 출처 iframe 안도 본다 (11/12 아이패드 코디 화면: 카드 0 · 글은 위 띠만 왔음)
const roots = (d0) => {
  const o = [d0 || D];
  for (let i = 0; i < o.length && o.length < 60; i++) {
    for (const e of o[i].querySelectorAll('*')) {
      if (e.hasAttribute('data-abcap')) continue;
      if (e.shadowRoot) o.push(e.shadowRoot);
      if (e.tagName == 'IFRAME') { try { if (e.contentDocument && e.contentDocument.body) o.push(e.contentDocument); } catch (x) {} }
    }
  }
  return o;
};
const Q = s => roots().flatMap(r => [...r.querySelectorAll(s)]);
const big = s => (s || '').split(',').map(x => x.trim().split(/\s+/)).filter(x => x[0]).sort((a, b) => (parseFloat(b[1]) || 0) - (parseFloat(a[1]) || 0)).map(x => x[0])[0] || '';
// 카드 안 사진들 (img 의 가장 큰 srcset·src · 배경 사진) + 화면 위치
const ims = c => {
  const o = [];
  for (const e of [c, ...c.querySelectorAll('*')]) {
    let s = '';
    if (e.tagName == 'IMG') s = big(e.getAttribute('srcset')) || e.currentSrc || e.getAttribute('src') || e.getAttribute('data-src') || '';
    else { const m = /url\((.+?)\)/.exec(css(e).backgroundImage || ''); if (m) s = m[1].replace(/[\x22\x27]/g, ''); }
    s = s ? abs(s) : '';
    const r = e.getBoundingClientRect();
    if (/^https?:/.test(s) && r.width >= 60 && r.height >= 60) o.push({e: e, s: s.slice(0, 600), r: r});
  }
  return o.sort((a, b) => b.r.width * b.r.height - a.r.width * a.r.height);
};
// 순위 딱지: 숫자만 든 요소('1' · 딱지 안 첫 숫자 — 뒤 숫자는 변동)를 '1 –'·'3 9↑'·'12 NEW' 까지 넓히고, 사진 왼쪽 위(사진 위 절반·바로 위)에 있는 것만
const BG = /^\d{1,3}(–|-|—|NEW|N|신규|[↑↓▲▼]?\d{0,3}[↑↓▲▼]?)$/i;
const badges = () => {
  const o = [];
  for (const e of Q('*')) {
    if (o.length > 200 || !/^\d{1,3}$/.test(own(e))) continue;
    let b = e;
    for (let k = 0; k < 3 && up(b); k++) { const t = (up(b).textContent || '').replace(/\s+/g, ''); if (t.length > 10 || !BG.test(t)) break; b = up(b); }
    const r = b.getBoundingClientRect(), cx = r.left + r.width * 0.5, cy = r.top + r.height * 0.5;
    if (!r.width || [b, ...b.querySelectorAll('*')].find(z => /^\d{1,3}$/.test(own(z))) != e) continue;
    let c = b, ok = false;
    for (let k = 0; k < 6 && up(c) && !ok; k++) { c = up(c); ok = ims(c).some(i => cx >= i.r.left - 8 && cx <= i.r.left + i.r.width * 0.6 && cy >= i.r.top - 48 && cy <= i.r.top + i.r.height * 0.5); }
    if (ok) o.push({b: b, e: e});
  }
  return o;
};
const hint = b => {
  const h = b.outerHTML;
  if (/(^|[^a-z])up|Up|UP|상승/.test(h)) return '↑';
  if (/(^|[^a-z])down|Down|DOWN|하락/.test(h)) return '↓';
  return '';
};
// 코디 카드 = 딱지(또는 사진)에서 다른 카드가 섞이기 직전 조상 · 가격·상품 링크가 든 카드는 상품 랭킹이라 뺌 · 탭마다·내릴 때마다 모음 (LK)
const LK = new Map(), T0 = Date.now();
let first = null, cur = '', ct = '', cc = '';
// 처음부터 150초 안 (탭·칩·상세 합쳐 · 칩은 60초까지)
const late = (n) => Date.now() - T0 > (n || 150000);
const hide = (o, k, v) => Object.defineProperty(o, k, {value: v, writable: true});
// 카드·상품의 data-*id·sno·no 숫자 (코디 번호·상품 번호)
const dat = c => (/\sdata-[\w-]*(id|sno|no)=.(\d{2,14})\W/i.exec(c.outerHTML.slice(0, 4000)) || [])[2] || '';
// 탭마다 코디 수 (주인 11/12 '랭킹 0' — 칩이 100개 한도를 먼저 채움): 랭킹 40 · 다른 탭 15 · 칩마다 8
const cap = () => (cc ? 8 : cur == '랭킹' ? 40 : 15);
const inTab = () => [...LK.values()].filter(l => l.k == cur).length;
const mk = (c, im, n, ch, bl) => {
  const t = tx(c);
  if (/\d{1,3}(,\d{3})+원?|\d{4,8}원/.test(t) || c.querySelector('a[href*="/goods/"]') || im.r.left < -5 || im.r.left >= (im.e.ownerDocument.defaultView || W).innerWidth) return;
  const ls = lines(c).filter(l => bl.indexOf(l) < 0 && l[0] != '#' && /[가-힣A-Za-z]/.test(l) && !/^바로\s?입기$/.test(l) && !BG.test(l.replace(/\s+/g, '')) && !LIKE.test(l));
  const a = im.e.closest('a[href]') || (c.closest && c.closest('a[href]')) || c.querySelector('a[href]'), u = a ? href(a) : '', k = cur + '|' + n + '|' + im.s;
  first = first || im.e;
  if (LK.has(k) || inTab() >= cap()) return;
  const l = {k: cur, tab: ct, chip: cc, rank: n || inTab() + 1, chg: ch.slice(0, 8), img: im.s, nick: (ls[0] || '').slice(0, 60), title: (ls[1] || '').slice(0, 200), tags: (t.match(/#[^\s#]{1,30}/g) || []).slice(0, 10), quick: /바로\s?입기/.test(t), url: /^https?:/.test(u) ? u : '', id: idOf(u, LOOK) || dat(c), items: []};
  hide(l, 'e', im.e);
  hide(l, 'y', box(im.e).scrollTop);
  LK.set(k, l);
};
const coordi = () => {
  const bs = badges(), all = bs.map(x => x.b);
  for (const x of bs) {
    let c = x.b;
    for (let k = 0; k < 10; k++) { const p = up(c); if (!p || /^(BODY|HTML)$/.test(p.tagName) || tx(p).length > 600 || all.some(o => o != x.b && p.contains(o))) break; c = p; }
    const im = ims(c)[0], n = own(x.e);
    if (!im) continue;
    let ch = (x.b.textContent || '').replace(/\s+/g, '').replace(n, '');
    if (/^\d+$/.test(ch)) ch += hint(x.b);
    mk(c, im, +n, ch, lines(x.b));
  }
  return bs.length;
};
// 순위 딱지 없는 탭(PICK·추천·데일리): 큰 사진(80px 넘게 · 배경 사진 div + 투명 img 는 하나로) 하나만 든 가장 큰 조상 = 카드 · 순위는 화면 차례
const pcards = () => {
  for (const i of Q('img,[style*=background]')) {
    const im = ims(i)[0];
    if (!im || im.e != i || im.r.width < 80 || im.r.height < 80) continue;
    let c = i;
    for (let k = 0; k < 8; k++) { const p = up(c); if (!p || /^(BODY|HTML)$/.test(p.tagName) || tx(p).length > 400 || new Set(ims(p).filter(z => z.r.width >= 80).map(z => z.s)).size > 1) break; c = p; }
    mk(c, im, 0, '', []);
  }
};
const grab = () => { if (!coordi()) pcards(); };
const box = e => {
  for (let p = e; p; p = up(p)) { if (p.nodeType == 1 && p.scrollHeight - p.clientHeight > 100 && /auto|scroll/.test(css(p).overflowY || '')) return p; }
  return (e.ownerDocument || D).scrollingElement || D.documentElement;
};
const busy = () => tx(D.body).split('\n').some(l => l.trim().length < 40 && LOAD.test(l.trim()));
// 11/12 실제 dbg: mobile.a-bly.com 은 껍데기, 화면은 다른 출처 m.a-bly.com iframe 안 → 그 화면으로 옮겨 한 번 더 (통계 보내기와 같은 두 단계)
const xf = [...D.querySelectorAll('iframe')].map(f => abs(f.getAttribute('src') || '')).find(u => /^https:\/\/([\w-]+\.)*a-bly\.com\//.test(u) && u.indexOf(location.origin + '/') != 0);
if (xf && Q('img').length < 6) {
  bye();
  if (confirm('코디 화면으로 옮길까요? 옮긴 뒤 한 번 더 눌러 주세요')) location.href = xf;
  return;
}
// 사진이 6장 넘게 뜨고, 상품 링크나 코디 카드 2개가 보일 때까지 (최대 8초 · 그림자 DOM·iframe 안 포함)
const gl = () => Q('a[href*="/goods/"]').length;
for (let i = 0; i < 16 && (busy() || (!gl() && (Q('img').length <= 6 || (coordi(), LK.size < 2)))); i++) { say('카드 찾는 중 (' + Math.round(i * 0.5) + '초)'); await sleep(500); }
// 카드가 보이면 몇 화면 내려 더 모은 뒤 제자리로
const roll = async m => {
  if (!first) return;
  const bx = box(first), y0 = bx.scrollTop;
  for (let i = 1; i <= m && inTab() < cap(); i++) {
    const was = bx.scrollTop;
    say(sum() + ' · 아래로 내리는 중 ' + i + '/' + m);
    bx.scrollTop = y0 + bx.clientHeight * 0.9 * i;
    await sleep(700);
    grab();
    if (bx.scrollTop == was) break;
  }
  bx.scrollTop = y0;
};
// 코디 화면 위 탭(팔로잉·찜은 내 것이라 뺌)을 차례로 눌러 탭마다 · 랭킹 먼저 (11/12 실제: PICK 26 · 추천 74 · 랭킹 0) · 추천은 칩(니트·코트…) 8개까지 · 끝나면 랭킹 탭으로
const TABS = ['랭킹', '데일리', 'PICK', '추천', '오늘 날씨'];
const tabEl = t => Q('*').find(e => own(e) == t && e.getBoundingClientRect().width);
const press = t => { const e = tabEl(t); if (e) (e.closest('button,a,[role=tab]') || e).click(); return !!e; };
const sum = () => { const m = {}; for (const l of LK.values()) m[l.k] = (m[l.k] || 0) + 1; return Object.keys(m).map(k => (k || '카드') + ' ' + m[k] + '개').join(' · '); };
const visit = async (t, m) => {
  first = null;
  if (!press(t)) return 0;
  let last = -1;
  for (let i = 0; i < 12; i++) { say(sum() + ' · ' + cur + ' 찾는 중'); await sleep(500); grab(); if (inTab() && inTab() == last) break; last = inTab(); }
  await roll(m);
  return 1;
};
const tb = TABS.filter(tabEl).length >= 2;
if (tb) {
  LK.clear();
  for (const t of TABS) {
    cur = ct = t;
    cc = '';
    if (!(await visit(t, t == '랭킹' ? 10 : 2)) || t != '추천' || late(60000)) continue;
    const row = Q('*').find(e => e.children.length >= 4 && e.getBoundingClientRect().width && [...e.children].every(c => { const x = tx(c).trim(); return x && x.length <= 8 && x[0] != '#' && TABS.indexOf(x) < 0; }));
    for (const c of row ? [...row.children].map(c => tx(c).trim()).slice(0, 8) : []) {
      cc = c;
      cur = t + '-' + c;
      await visit(c, 1);
    }
  }
} else {
  grab();
  await roll(6);
}
// 코디 안 상품 (앱 화면이라 HTML 에는 상품이 없음 · 서버는 그대로 에이블리를 열지 않음)
// 가로 상품 줄: 보이는 사진 + 가까운 가격 · 상품 주소는 링크, 없으면 data-* 상품 번호
const prods = (d, mx, rk) => {
  const o = [], seen = new Set();
  for (const r of roots(d)) {
    for (const i of r.querySelectorAll('img')) {
      if (o.length >= mx) break;
      if (!i.getBoundingClientRect().width) continue;
      let c = i, ok = false;
      for (let k = 0; k < 5 && up(c) && !ok; k++) { const p = up(c); if (/^(BODY|HTML)$/.test(p.tagName) || p.querySelectorAll('img').length > 2 || tx(p).length > 300) break; c = p; ok = /\d{1,3}(,\d{3})+/.test(tx(c)); }
      if (!ok || seen.has(c)) continue;
      seen.add(c);
      const a = (c.closest && c.closest('a[href]')) || c.querySelector('a[href]'), it = parse(c, a ? href(a) : ''), g = dat(c);
      if (!rk) delete it.rank;
      if (!idOf(it.url, GOODS) && g) it.url = location.origin + '/goods/' + g;
      hide(it, 'e', c);
      if (it.price) o.push(it);
    }
  }
  return o;
};
// ① 화면 안 데이터 (window·script JSON·Next 조각·카드 React props): 가격·이름 든 객체 묶음(상품)을 가진 객체 = 코디 → 코디 번호나 사진 파일 이름으로 맞춤
const keys = o => { try { return Object.keys(o); } catch (x) { return []; } };
const isG = o => !!o && typeof o == 'object' && keys(o).some(k => /price/i.test(k) && +o[k] >= 100) && keys(o).some(k => /name|title/i.test(k) && typeof o[k] == 'string');
const gd = o => {
  const it = {}, kk = keys(o), f = rx => kk.filter(k => rx.test(k) && o[k]).map(k => o[k]), p = f(/price/i).map(Number).filter(x => x >= 100), im = f(/image|img|thumb/i)[0], id = f(/^(goods_?)?(sno|no|id)$/i).find(x => /^\d{2,14}$/.test(x));
  if (p.length) it.price = Math.min(...p);
  it.name = String(o.name || o.goods_name || o.goodsName || f(/name|title/i)[0]).slice(0, 200);
  const d = +f(/discount_?(rate|percent)|^rate/i)[0];
  if (d >= 1 && d < 100) it.disc = Math.round(d);
  if (typeof im == 'string') it.img = abs(im).slice(0, 600);
  it.url = id ? location.origin + '/goods/' + id : '';
  return it;
};
const ST = [], SN = new Set(), SK = /^_|^(return|child|sibling|alternate|stateNode)$/;
const scan = o0 => {
  const q = [[o0, 0]];
  while (q.length && SN.size < 60000) {
    const [o, d] = q.pop();
    if (!o || typeof o != 'object' || SN.has(o) || d > 16 || o.nodeType || o.window == o) continue;
    SN.add(o);
    if (o instanceof Map || o instanceof Set) { q.push([[...o.values()], d + 1]); continue; }
    for (const k of keys(o)) {
      let v = null;
      try { v = SK.test(k) ? null : o[k]; } catch (x) {}
      if (Array.isArray(v) && isG(v[0])) ST.push({o: o, g: v.filter(isG).slice(0, 12).map(gd)});
      else if (v && typeof v == 'object') q.push([v, d + 1]);
    }
  }
};
// 코디 객체의 글자 (상품 묶음 빼고 · 낱말만 — 코디 번호·사진 파일 이름 맞추기)
const sv = o => ' ' + keys(o).map(k => { const v = o[k]; try { return Array.isArray(v) && isG(v[0]) ? '' : v && typeof v == 'object' ? JSON.stringify(v).slice(0, 4000) : v; } catch (x) { return ''; } }).join(' ').replace(/\W+/g, ' ') + ' ';
let nC = 0;
try {
  for (const k of keys(W)) { if (/^__|state|store|cache|query/i.test(k)) { try { scan(W[k]); } catch (x) {} } }
  for (const e of Q('script')) { if (/json/.test(e.type) || e.id) { try { scan(JSON.parse(e.textContent)); } catch (x) {} } }
  for (const l of (W.__next_f || []).map(x => x[1] || '').join('').split('\n')) { try { scan(JSON.parse(l.slice(l.indexOf(':') + 1))); } catch (x) {} }
  for (const l of LK.values()) { const e = l.e, k = keys(e).find(k => /^__react(Fiber|InternalInstance)/.test(k)); for (let f = k && e[k], i = 0; f && i < 20; f = f.return, i++) scan(f.memoizedProps); }
  ST.forEach(s => { s.t = sv(s.o); });
  ST.sort((a, b) => a.t.length - b.t.length);
  for (const l of LK.values()) {
    const n = (l.img || '').split('?')[0].split('/').pop().replace(/\W+/g, ' ').trim(), m = ST.find(s => (l.id && s.t.indexOf(' ' + l.id + ' ') >= 0) || (n.length > 4 && s.t.indexOf(' ' + n + ' ') >= 0));
    if (m) { l.items = m.g.map(x => ({...x})); nC++; }
  }
} catch (x) {}
// ② 같은 출처 코디 화면(요즘코디)을 이 브라우저의 숨은 iframe 으로 열어 상품 줄을 읽음 · 3개씩
const one = lk => new Promise(res => {
  const f = D.createElement('iframe');
  let n = 0, last = -1;
  f.setAttribute('data-abcap', '1');
  f.setAttribute('sandbox', 'allow-scripts allow-same-origin');
  f.style.cssText = 'position:fixed;left:-4000px;top:0;width:390px;height:844px;border:0';
  f.src = lk.url;
  const iv = setInterval(() => {
    let p = [];
    try { p = prods(f.contentDocument, 12); } catch (x) {}
    if ((p.length && p.length == last) || ++n > 16 || late()) { clearInterval(iv); lk.items = p; f.remove(); res(); }
    last = p.length;
  }, 500);
  D.body.appendChild(f);
});
// 상세 순서: 랭킹 1~20위 먼저, 그다음 다른 탭 차례, 랭킹 21위~ 는 끝 · 데이터에서 찾은 코디는 빼고 · 탭 합쳐 40개
const ks = [...new Set([...LK.values()].map(l => l.k))];
const pri = l => (l.tab != '랭킹' ? 1 : l.rank <= 20 ? 0 : 2);
const tq = [...LK.values()].filter(l => !l.items.length).sort((a, b) => pri(a) - pri(b) || ks.indexOf(a.k) - ks.indexOf(b.k) || a.rank - b.rank).slice(0, 40);
const nq = tq.length, qa = tq.filter(l => l.url && new URL(l.url).origin === location.origin);
let nd = 0, np = [...LK.values()].reduce((a, l) => a + l.items.length, 0), nA = 0, nB = 0, nu = 0, nf = 0, nb = 0, pc = 12, at = cur, du = '';
const bar = () => say('상세 ' + nd + '/' + nq + ' · 상품 ' + np);
const work = async () => { while (qa.length && !late()) { const l = qa.shift(); await one(l); if (l.items.length) { nd++; nA++; np += l.items.length; bar(); } } };
if (nq) bar();
await Promise.all([work(), work(), work()]);
// ③ 링크 없는 카드 (11/12 실제): 사진을 눌러(click → 안 되면 pointer·mouse 차례) 상세가 뜨면(주소 바뀜·가격 줄) 읽고 뒤로 · 3번 연달아 안 열리면 그만
const seeI = f => Q('img,[style*=background]').find(x => { const m = ims(x)[0]; return m && m.e == x && f(m.s); });
const IMG = new Set([...LK.values()].map(l => l.img));
const find = async l => {
  for (let i = 0; i < 8; i++) {
    if (l.e && l.e.isConnected && ims(l.e)[0]) return l.e;
    l.e = seeI(s => s == l.img);
    if (l.e) return l.e;
    const a = seeI(s => IMG.has(s));
    if (a) box(a).scrollTop = l.y + (i % 2) * 400;
    await sleep(500);
  }
  return null;
};
// 누르기 — 진짜 링크로 화면이 통째로 넘어가는 것은 막음 (앱 안 이동만)
const hit = (e, m) => {
  const v = e.ownerDocument.defaultView, r = e.getBoundingClientRect(), o = {bubbles: true, cancelable: true, composed: true, view: v, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, pointerId: 1, pointerType: 'mouse', isPrimary: true};
  const g = x => { if (!x.defaultPrevented && x.composedPath().some(n => n.tagName == 'A' && n.getAttribute('href'))) x.preventDefault(); };
  v.addEventListener('click', g);
  for (const t of m ? ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'] : ['click']) e.dispatchEvent(new (t[0] == 'p' && v.PointerEvent || v.MouseEvent)(t, o));
  v.removeEventListener('click', g);
};
const wait = async (f, n) => { for (let i = 0; i < n && !f(); i++) await sleep(300); return f(); };
const tap = async l => {
  if (at != l.k) { cur = l.k; press(l.tab); await sleep(900); if (l.chip) { press(l.chip); await sleep(900); } at = l.k; }
  const e = await find(l);
  if (!e) return;
  const v = e.ownerDocument.defaultView, y = box(e).scrollTop, u0 = v.location.href, p0 = prods(D, 12).length, moved = () => v.location.href != u0;
  let p = [], n = -1;
  hit(e, 0);
  for (let i = 0; i < 20 && !late(); i++) {
    await sleep(300);
    if (i == 4 && !moved() && n <= p0) hit(e, 1);
    p = prods(D, 12);
    if (p.length > p0 && p.length == n) break;
    n = p.length;
  }
  if (p.length <= p0) p = [];
  nB++;
  nf = p.length ? 0 : nf + 1;
  if (moved()) { nu++; du = du || v.location.pathname; }
  // 상품도 눌러 주소 (눌러서 바로 /goods/ 로 바뀔 때만 · 합쳐 12번)
  for (let j = 0; j < p.length && pc > 0 && !late(135000); j++) {
    if (idOf(p[j].url, GOODS)) continue;
    const u1 = v.location.href;
    pc--;
    hit(p[j].e, 0);
    await wait(() => v.location.href != u1, 7);
    const g = idOf(v.location.href, GOODS);
    if (g) p[j].url = v.location.origin + '/goods/' + g; else pc = 0;
    if (v.location.href != u1) v.history.back();
    await wait(() => v.location.href == u1 && prods(D, 12).length >= p.length, 10);
    const p2 = prods(D, 12);
    for (let i = j + 1; i < p.length && p2.length == p.length; i++) p[i].e = p2[i].e;
  }
  l.items = p;
  np += p.length;
  nd++;
  bar();
  const home = () => prods(D, 12).length <= p0 && !!seeI(s => IMG.has(s));
  if (moved()) v.history.back();
  else if (p.length) { const c = Q('[aria-label]').find(x => /닫기|뒤로|close|back/i.test(x.getAttribute('aria-label'))); if (c) c.click(); }
  const ok = await wait(home, 17);
  // 5초 안에 안 돌아오면 코디 주소로 다시 (앱 안 이동) · 탭 다시 누름
  if (!ok && moved()) { v.history.pushState(null, '', u0); v.dispatchEvent(new v.PopStateEvent('popstate', {state: null})); nb++; await wait(home, 10); }
  if (!ok) at = '';
  const b = seeI(s => IMG.has(s));
  if (b) box(b).scrollTop = y;
};
const post = (F, ka) => fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', keepalive: ka, body: new URLSearchParams(F)});
// 보낼 것 묶기 (lim: j 글자 · eh: 화면이 넘어갈 때 — 폼 글자로 60KB 안)
const fin = (lim, eh) => {
const looks = [...LK.values()].sort((a, b) => ks.indexOf(a.k) - ks.indexOf(b.k) || a.rank - b.rank).slice(0, 100).map(l => { const o = {...l}; delete o.k; return o; });
// 상품 랭킹 화면 (코디가 없을 때): 사진 + 가까운 가격 카드 (코디 상품 줄 읽기와 같음 · 순위도)
const items = looks.length ? [] : prods(D, 100, 1);
// 고른 탭: 코디 화면은 끝에 랭킹 탭을 누름 · 그 밖은 aria-selected·.on 같은 표시
const sel = tb ? ['랭킹'] : [];
for (const n of D.querySelectorAll(SEL)) { const s = lines(n)[0] || ''; if (s && s.length < 40 && sel.length < 12 && sel.indexOf(s) < 0 && n.getClientRects().length) sel.push(s); }
const out = [], rs = roots();
let last = '';
for (const r of rs) {
  for (const e of r == D ? [D.body] : r.body ? [r.body] : [...r.children]) {
    for (const l of lines(e)) { if (l != last) { out.push(l); last = l; } }
  }
}
const upd = out.find(l => /업데이트|기준/.test(l) && /\d/.test(l) && l.length < 30) || '';
// 화면 모양 (2KB 안 · Blog-Auto 상태 확인이 찍음 — 0개일 때 진짜 구조를 보려고) · det = 상세 읽기 (데이터 찾은 코디/상품 묶음 · iframe · 누름 · 주소 바뀜 · 다시 옮김 · 상품 눌러 주소)
const all = Q('*');
const bt = (D.body && D.body.innerText) || '';
const dbg = ['ready ' + D.readyState, 'els ' + all.length, 'img ' + Q('img').length, 'a ' + Q('a').length, 'iframe ' + Q('iframe').length,
  'roots ' + rs.length, 'badges ' + badges().length, 'cards ' + LK.size + ' urls ' + [...LK.values()].filter(l => l.url).length + ' ids ' + [...LK.values()].filter(l => l.id).length,
  'det ' + [nC, ST.length, nA, nB, nu, nb, 12 - pc, np, eh ? 'H' : ''].join('/') + ' ' + du.slice(0, 40),
  'ifr ' + Q('iframe').slice(0, 10).map(f => (f.getAttribute('src') || '-').slice(0, 60)).join(' '), 'cls ' + [...new Set(all.map(e => (e.getAttribute('class') || '').split(' ')[0].slice(0, 24)))].slice(0, 16).join(' '), 'text ' + bt.length + ' ' + bt.slice(0, 300).replace(/\s+/g, ' ')].join(' | ').slice(0, 1900);
const F = {k: K, u: location.href, t: D.title || '', s: sel.join('\n'), j: '', x: eh ? '' : out.join('\n').slice(0, 15000)};
const pack = () => { F.j = JSON.stringify({items: items, looks: looks, upd: upd, dbg: dbg}); return eh ? new URLSearchParams(F).toString().length : F.j.length; };
while (pack() > lim && (items.length || looks.length)) { if (items.length) items.pop(); else looks.pop(); }
return {F: F, n: looks.length, g: items.length + looks.reduce((a, l) => a + l.items.length, 0)};
};
// 상세를 여는 사이 화면이 통째로 넘어가면 그때까지라도 (keepalive)
const ph = () => { try { post(fin(60000, 1).F, true); } catch (x) {} };
W.addEventListener('pagehide', ph);
for (const l of tq) { if (late() || nf >= 3) break; if (!l.items.length) await tap(l); }
W.removeEventListener('pagehide', ph);
if (tb) press('랭킹');
const R = fin(95000, 0), F = R.F, ng = R.g, looks = {length: R.n};
let res = null;
say('보내는 중…');
try {
  const r = await post(F, false);
  res = await r.json();
} catch (e) { res = null; }
bye();
if (res && res.ok) { alert('Ably 랭킹 보냄 ✓ (코디 ' + looks.length + ' · 상품 ' + ng + ')' + (ng || looks.length ? '' : ' · 메인에게 알려 주세요')); return; }
if (res) { alert(res.text || '받지 않았어요'); return; }
if (!confirm('못 보냈어요. 이 창에서 보낼까요?')) return;
const fm = D.createElement('form');
fm.method = 'POST'; fm.action = U; fm.acceptCharset = 'UTF-8';
F.via = 'form';
for (const k in F) { const i = D.createElement('input'); i.type = 'hidden'; i.name = k; i.value = F[k]; fm.appendChild(i); }
D.body.appendChild(fm);
fm.submit();
} catch (e) {
  bad = 1;
  say('보내지 못했어요 (' + ((e && e.message) || e) + ')');
  setTimeout(bye, 5000);
} finally { W.__abcap = 0; if (!bad) bye(); }
})();
