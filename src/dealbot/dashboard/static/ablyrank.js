/* 'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 서버는 에이블리를 열지 않음 · 10/28 원칙 그대로). 주인이 아이패드 사파리·갤럭시 크롬에서
   에이블리 랭킹 화면(a-bly.com · m.a-bly.com …)을 연 채 누르면, 보이는 순위 카드(순위·상품명·쇼핑몰·가격·할인·리뷰 수·별점·찜, 상품 주소·사진 주소)와
   코디 카드(코디 제목·좋아요·든 상품), 주소·제목·고른 탭, 보이는 글을 대시보드 /dashboard/api/ably_rank_capture 로 보낸다.
   쿠키는 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만. 바로 보내지 못하면 이 창에서 폼으로 보낼지 묻는다.
   딜봇 ablyrank.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — statscap.js 와 같은 규칙:
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). 카드가 아직 안 떴으면 0.5초마다 최대 8초 기다린다.
   11/12 코디 랭킹(mobile.a-bly.com/content/style/home 랭킹 탭 — 링크·가격 없는 사진 카드)이 0개로 와서: 사진 왼쪽 위 순위 딱지('1 –'·'3 9↑')에서
   카드까지 올라가 순위·변동·사진·닉네임·해시태그·바로입기·카드 링크를 코디(looks)로, 몇 화면 내려 더 모은 뒤 제자리로. 그림자 DOM·같은 출처 iframe 도 본다.
   카드 링크가 같은 출처면 그 화면을 이 브라우저의 숨은 iframe 으로 열어(최대 40개 · 90초 — 주인 11/12 "10개로 제한 안해도 돼") 가로 상품 줄을 읽는다 — 서버는 그대로 에이블리를 열지 않음.
   11/12 두 번째: 그래도 0개(글은 위 띠만)로 와서 dbg(화면 모양 2KB)를 늘 함께 보내고, 사진 6장 넘게 뜰 때까지 기다린다. 알림은 '코디 N · 상품 M'. */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', W = window, D = document;
if (W.__abcap) { alert('Ably 랭킹 보내기: 보내는 중이에요'); return; }
if (!/(^|\.)a-bly\.com$/.test(location.hostname)) { alert('Ably 랭킹 보내기: 에이블리 랭킹 화면(a-bly.com)에서 눌러 주세요'); return; }
W.__abcap = 1;
try {
const SEL = '[aria-selected=true],[aria-current=page],[aria-current=true],[aria-checked=true],[aria-pressed=true],.on,.active,.selected,.is_selected';
const GOODS = /\/goods\/(\d{2,14})/;
const LOOK = /\/(coordi|codi|cody|look|lookbook|styling|outfit|style)s?\/([\w-]{1,40})/i;
const LOAD = /불러오는\s*중|로딩\s*중|잠시만\s*기다려|^loading/i;
const RANK = /^(\d{1,3})(위)?$/;
const PCT = /(\d{1,2})%/;
const PRICE = /\d{1,3}(,\d{3})+(?=원|\s|$)|\d{4,8}(?=원)/g;
const REV = /(리뷰|후기|review)\s?\(?([\d,.]+[만천kK]?\+?)\)?/i;
const STAR = /(★|⭐|별점)\s?([0-5](\.\d)?)/;
const LIKE = /(찜|좋아요|♥|❤|♡|하트)\s?([\d,.]+[만천kK]?\+?)/;
const BADGE = /^(무료배송|오늘출발|당일발송|빠른배송|new|best|hot|ad|광고|쿠폰|단독|신상|특가|타임세일|\d+%?쿠폰)$/i;
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/[\t\x20]+/g, ' ');
const lines = n => tx(n).split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(l => l && !(l.length < 40 && LOAD.test(l)));
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
const abs = h => { try { return new URL(h, location.href).href; } catch (e) { return ''; } };
const idOf = (h, rx) => { const m = rx.exec(h || ''); return m ? m[m.length - 1] : ''; };
const href = a => abs(a.getAttribute('href') || a.href || '');
const links = (r, rx) => [...r.querySelectorAll('a[href]')].filter(a => idOf(href(a), rx));
const pic = c => {
  for (const i of [...c.querySelectorAll('img')].slice(0, 6)) {
    const s = abs(i.currentSrc || i.getAttribute('src') || i.getAttribute('data-src') || i.getAttribute('data-original') || '');
    if (/^https?:/.test(s)) return s.slice(0, 600);
  }
  return '';
};
// 링크 하나에서 카드까지: 다른 상품(또는 코디) 링크가 섞이기 직전 조상까지 (6단·글 800자·멈춤 요소 안)
const card = (a, rx, stop) => {
  let c = a;
  const me = idOf(href(a), rx);
  for (let i = 0; i < 6; i++) {
    const p = c.parentElement;
    if (!p || p === D.body || p === stop || (stop && !stop.contains(p))) break;
    if (links(p, rx).some(x => idOf(href(x), rx) !== me) || tx(p).length > 800) break;
    c = p;
  }
  return c;
};
// 카드 글 → 순위·할인·가격·리뷰·별점·찜 · 남은 글 줄 = 쇼핑몰·상품명
// ex: 뺄 안쪽 카드 (코디 카드 안 상품 카드 — 코디 제목·좋아요만 읽게)
const parse = (c, url, ex) => {
  const o = {url: url, img: pic(c)}, rest = [], prices = [], out = ex || [], drop = new Set();
  out.forEach(e => lines(e).forEach(l => drop.add(l)));
  for (const l0 of lines(c).filter(l => !drop.has(l)).slice(0, 30)) {
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
  const SHOP = '[class*=market],[class*=Market],[class*=shop],[class*=Shop],[class*=brand],[class*=Brand],[class*=store],[class*=seller]';
  const hint = (s, no) => { const n = [...c.querySelectorAll(s)].find(x => !out.some(e => e.contains(x)) && !(no && x.matches(no))); return n ? lines(n)[0] || '' : ''; };
  const shop = hint(SHOP, '');
  const name = hint('[class*=goodsName],[class*=GoodsName],[class*=productName],[class*=ProductName],[class*=itemName],[class*=name],[class*=Name]', SHOP);
  if (name) o.name = name;
  if (shop && shop !== name) o.shop = shop;
  const left = rest.filter(l => l !== o.name && l !== o.shop);
  if (!o.shop && left.length > 1) o.shop = left.shift();
  if (!o.name && left.length) o.name = left.reduce((a, b) => (b.length > a.length ? b : a));
  return o;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const css = e => { try { return (e.ownerDocument.defaultView || W).getComputedStyle(e); } catch (x) { return {}; } };
const up = e => e.parentElement || (e.parentNode && e.parentNode.host) || null;
const own = e => [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.data).join('').trim();
// 그림자 DOM·같은 출처 iframe 안도 본다 (11/12 아이패드 코디 화면: 카드 0 · 글은 위 띠만 왔음)
const roots = (d0) => {
  const o = [d0 || D];
  for (let i = 0; i < o.length && o.length < 60; i++) {
    for (const e of o[i].querySelectorAll('*')) {
      if (e.shadowRoot) o.push(e.shadowRoot);
      if (e.tagName === 'IFRAME' && !e.hasAttribute('data-abcap')) { try { if (e.contentDocument && e.contentDocument.body) o.push(e.contentDocument); } catch (x) {} }
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
    if (e.tagName === 'IMG') s = big(e.getAttribute('srcset')) || e.currentSrc || e.getAttribute('src') || e.getAttribute('data-src') || '';
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
    if (!r.width || [b, ...b.querySelectorAll('*')].find(z => /^\d{1,3}$/.test(own(z))) !== e) continue;
    let c = b, ok = false;
    for (let k = 0; k < 6 && up(c) && !ok; k++) { c = up(c); ok = ims(c).some(i => cx >= i.r.left - 8 && cx <= i.r.left + i.r.width * 0.6 && cy >= i.r.top - 48 && cy <= i.r.top + i.r.height * 0.5); }
    if (ok) o.push({b: b, e: e});
  }
  return o;
};
const hint = (b, d) => {
  const h = [b, ...b.querySelectorAll('*')].map(e => (e.getAttribute('class') || '') + ' ' + (e.getAttribute('aria-label') || '') + ' ' + (e.getAttribute('alt') || '')).join(' ');
  if (/(^|[^a-z])up|Up|UP|[Rr]ise|[Ii]ncreas|상승/.test(h)) return '↑';
  if (/(^|[^a-z])down|Down|DOWN|[Ff]all|[Dd]ecreas|하락/.test(h)) return '↓';
  const z = [b, ...b.querySelectorAll('*')].find(e => own(e) === d), m = ((z && css(z).color) || '').match(/\d+/g) || [];
  return +m[0] > +m[2] + 60 ? '↑' : +m[2] > +m[0] + 60 ? '↓' : '';
};
// 코디 카드 = 딱지에서 다른 딱지가 섞이기 직전 조상 · 가격·상품 링크가 든 카드는 상품 랭킹이라 뺌 · 내릴 때마다 모음 (LK)
const LK = new Map();
let first = null;
const coordi = () => {
  const bs = badges(), all = bs.map(x => x.b);
  for (const x of bs) {
    let c = x.b;
    for (let k = 0; k < 10; k++) { const p = up(c); if (!p || /^(BODY|HTML)$/.test(p.tagName) || tx(p).length > 600 || all.some(o => o !== x.b && p.contains(o))) break; c = p; }
    const im = ims(c)[0], t = tx(c), n = own(x.e);
    if (!im || /\d{1,3}(,\d{3})+원?|\d{4,8}원/.test(t) || c.querySelector('a[href*="/goods/"]')) continue;
    first = first || x.b;
    let ch = (x.b.textContent || '').replace(/\s+/g, '').replace(n, '');
    if (/^\d+$/.test(ch)) ch += hint(x.b, ch);
    const bl = lines(x.b), ls = lines(c).filter(l => bl.indexOf(l) < 0 && l[0] !== '#' && /[가-힣A-Za-z]/.test(l) && !/^바로\s?입기$/.test(l) && !BG.test(l.replace(/\s+/g, '')) && !LIKE.test(l));
    const a = im.e.closest('a[href]') || (c.closest && c.closest('a[href]')) || c.querySelector('a[href]'), u = a ? href(a) : '';
    const k = n + '|' + im.s;
    if (!LK.has(k)) LK.set(k, {rank: +n, chg: ch.slice(0, 8), img: im.s, nick: (ls[0] || '').slice(0, 60), title: (ls[1] || '').slice(0, 200), tags: (t.match(/#[^\s#]{1,30}/g) || []).slice(0, 10), quick: /바로\s?입기/.test(t) || !!c.querySelector('[alt*=바로입기]'), url: /^https?:/.test(u) ? u : '', items: []});
  }
  return bs.length;
};
const box = e => {
  for (let p = e; p; p = up(p)) { if (p.nodeType === 1 && p.scrollHeight - p.clientHeight > 100 && /auto|scroll/.test(css(p).overflowY || '')) return p; }
  return (e.ownerDocument || D).scrollingElement || D.documentElement;
};
const busy = () => tx(D.body).split('\n').some(l => l.trim().length < 40 && LOAD.test(l.trim()));
// 사진이 6장 넘게 뜨고, 상품 링크나 코디 카드 2개가 보일 때까지 (최대 8초 · 그림자 DOM·iframe 안 포함)
const gl = () => Q('a[href*="/goods/"]').length;
for (let i = 0; i < 16 && (busy() || (!gl() && (Q('img').length <= 6 || (coordi(), LK.size < 2)))); i++) await sleep(500);
coordi();
// 코디 카드가 보이면 몇 화면 내려 20~40개 모은 뒤 제자리로
if (first) {
  const bx = box(first), y0 = bx.scrollTop;
  const go = y => { bx.scrollTop = y; };
  for (let i = 1; i <= 6 && LK.size < 40; i++) {
    const was = bx.scrollTop;
    go(y0 + bx.clientHeight * 0.9 * i);
    await sleep(700);
    coordi();
    if (bx.scrollTop === was) break;
  }
  go(y0);
}
// 코디 안 상품: 같은 출처 코디 화면(요즘코디)을 이 브라우저의 숨은 iframe 으로 열어 가로 상품 줄(사진 + 가까운 가격)을 읽음 · 3개씩 · 최대 40개 · 90초
// (앱 화면이라 HTML 에는 상품이 없음 · 서버는 그대로 에이블리를 열지 않음)
const t0 = Date.now();
const prods = d => {
  const o = [], seen = new Set();
  for (const r of roots(d)) {
    for (const i of r.querySelectorAll('img')) {
      if (o.length >= 12) break;
      let c = i, ok = false;
      for (let k = 0; k < 5 && up(c) && !ok; k++) { const p = up(c); if (/^(BODY|HTML)$/.test(p.tagName) || p.querySelectorAll('img').length > 2 || tx(p).length > 300) break; c = p; ok = /\d{1,3}(,\d{3})+/.test(tx(c)); }
      if (!ok || seen.has(c)) continue;
      seen.add(c);
      const a = (c.closest && c.closest('a[href]')) || c.querySelector('a[href]'), it = parse(c, a ? href(a) : '');
      delete it.rank;
      if (it.price) o.push(it);
    }
  }
  return o;
};
const one = lk => new Promise(res => {
  const f = D.createElement('iframe');
  let n = 0, last = -1;
  f.setAttribute('data-abcap', '1');
  f.setAttribute('sandbox', 'allow-scripts allow-same-origin');
  f.style.cssText = 'position:fixed;left:-4000px;top:0;width:390px;height:844px;border:0';
  f.src = lk.url;
  const iv = setInterval(() => {
    let p = [];
    try { p = prods(f.contentDocument); } catch (x) {}
    if ((p.length && p.length === last) || ++n > 16 || Date.now() - t0 > 88000) { clearInterval(iv); lk.items = p; f.remove(); res(); }
    last = p.length;
  }, 500);
  D.body.appendChild(f);
});
const q = [...LK.values()].filter(l => l.url && new URL(l.url).origin === location.origin).slice(0, 40);
const work = async () => { while (q.length && Date.now() - t0 < 88000) await one(q.shift()); };
await Promise.all([work(), work(), work()]);
const items = [], looks = [], seen = new Set(), lseen = new Set();
looks.push(...[...LK.values()].sort((a, b) => a.rank - b.rank).slice(0, 50));
// 코디 카드 (코디 링크 하나 + 안에 든 상품 링크들) — 순위 딱지 카드가 없을 때만
for (const a of LK.size ? [] : links(D, LOOK)) {
  if (looks.length >= 50) break;
  const id = idOf(href(a), LOOK);
  if (lseen.has(id) || !vis(a)) continue;
  lseen.add(id);
  const c = card(a, LOOK, null), its = [], gc = [];
  for (const g of links(c, GOODS)) {
    const gid = idOf(href(g), GOODS);
    if (seen.has(gid) || its.length >= 12) continue;
    seen.add(gid);
    const e = card(g, GOODS, c), it = parse(e, href(g));
    delete it.rank;
    its.push(it);
    gc.push(e);
  }
  const lk = parse(c, href(a), gc);
  looks.push({rank: lk.rank, title: lk.name || (lines(c).find(l => l.length > 3 && !/^\d/.test(l)) || '').slice(0, 200), shop: lk.shop, like: lk.like, url: lk.url, img: lk.img, items: its});
}
// 상품 카드 (코디 안에 든 상품은 위에서)
for (const a of links(D, GOODS)) {
  if (items.length >= 100) break;
  const gid = idOf(href(a), GOODS);
  if (seen.has(gid) || !vis(a)) continue;
  seen.add(gid);
  items.push(parse(card(a, GOODS, null), href(a)));
}
// 링크 없는 화면(누르면 열리는 카드) — 큰 사진 하나 + 가격이 든 조상
if (!items.length && !looks.length) {
  for (const i of D.querySelectorAll('img')) {
    if (items.length >= 100) break;
    if (!vis(i) || (i.width && i.width < 60)) continue;
    let c = i;
    for (let k = 0; k < 6 && c.parentElement && c.parentElement !== D.body; k++) {
      const p = c.parentElement;
      if (p.querySelectorAll('img').length > 3 || tx(p).length > 800) break;
      c = p;
      if (/\d{1,3}(,\d{3})+/.test(tx(c))) break;
    }
    if (c !== i && /\d{1,3}(,\d{3})+/.test(tx(c))) { const o = parse(c, ''); if (o.name || o.price) items.push(o); }
  }
}
const sel = [];
// 고른 탭: '랭킹' 이 든 짧은 글 줄(탭 줄)에서 글 굵기·색·밑줄이 혼자 다른 칸
for (const e of Q('*')) {
  const k = [...e.children];
  if (sel.length || k.length < 3 || k.length > 12 || !k.some(c => tx(c).trim() === '랭킹') || k.some(c => tx(c).trim().length > 12)) continue;
  const sg = k.map(c => { const d = [c, ...c.querySelectorAll('*')].filter(z => own(z)).pop() || c, a = css(d), b = css(c); return [a.fontWeight, a.color, b.borderBottomWidth, b.borderBottomColor, b.backgroundColor, c.getAttribute('aria-selected')].join('|'); });
  const u = sg.filter(x => sg.indexOf(x) === sg.lastIndexOf(x));
  if (u.length === 1) sel.push(tx(k[sg.indexOf(u[0])]).trim());
}
for (const n of D.querySelectorAll(SEL)) { const s = lines(n)[0] || ''; if (s && s.length < 40 && sel.length < 12 && sel.indexOf(s) < 0 && vis(n)) sel.push(s); }
const heads = [...D.querySelectorAll('h1,h2,h3')].filter(vis).map(n => lines(n)[0] || '').filter(Boolean).slice(0, 8);
const out = [], rs = roots();
let last = '';
for (const r of rs) {
  for (const e of r === D ? [D.body] : r.body ? [r.body] : [...r.children]) {
    for (const l of lines(e)) { if (l !== last) { out.push(l); last = l; } }
  }
}
const upd = out.find(l => /업데이트|기준/.test(l) && /\d/.test(l) && l.length < 30) || '';
// 화면 모양 (2KB 안 · Blog-Auto 상태 확인이 찍음 — 0개일 때 진짜 구조를 보려고)
const all = Q('*'), cnt = {};
for (const r of rs) { const t = r === D ? 'D' : r.host ? 'S' : 'F'; cnt[t] = (cnt[t] || 0) + [...r.querySelectorAll('*')].filter(e => /^\d{1,3}$/.test(own(e))).length + '/' + ((r.textContent || '').match(/↑|↓|NEW/g) || []).length + ' '; }
const cls = [];
for (const e of all) { const c = ((e.getAttribute('class') || '').split(/\s/)[0] || '').split(/__/)[0].slice(0, 24); if (c && cls.length < 20 && cls.indexOf(c) < 0) cls.push(c); }
const scs = all.filter(e => e.scrollHeight > e.clientHeight + 200 && e.clientHeight > 0).slice(0, 8).map(e => e.tagName.toLowerCase() + '.' + ((e.getAttribute('class') || '').split(/\s/)[0] || '').slice(0, 24));
const bt = (D.body && D.body.innerText) || '';
const dbg = ['ready ' + D.readyState, 'els ' + all.length, 'img ' + Q('img').length, 'a ' + Q('a').length, 'iframe ' + Q('iframe').length, 'shadow ' + all.filter(e => e.shadowRoot).length, 'custom ' + all.filter(e => e.tagName.indexOf('-') > 0).length,
  'roots ' + rs.length, 'rank(num/arrow) ' + Object.keys(cnt).map(k => k + cnt[k]).join(''), 'badges ' + badges().length, 'cards ' + LK.size + ' urls ' + [...LK.values()].filter(l => l.url).length,
  'ifr ' + Q('iframe').slice(0, 10).map(f => (f.getAttribute('src') || '-').slice(0, 60)).join(' '), 'cls ' + cls.join(' '), 'scroll ' + scs.join(' '), 'text ' + bt.length + ' ' + bt.slice(0, 300).replace(/\s+/g, ' ')].join(' | ').slice(0, 1900);
const pack = () => JSON.stringify({items: items, looks: looks, upd: upd, dbg: dbg});
let j = pack();
while (j.length > 60000 && (items.length || looks.length)) { if (items.length) items.pop(); else looks.pop(); j = pack(); }
const F = {k: K, u: location.href, t: D.title || '', s: sel.join('\n'), h: heads.join('\n'), j: j, x: out.join('\n').slice(0, 15000)};
let res = null;
try {
  const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)});
  res = await r.json();
} catch (e) { res = null; }
const ng = items.length + looks.reduce((a, l) => a + l.items.length, 0);
if (res && res.ok) { alert('Ably 랭킹 보냄 ✓ (코디 ' + looks.length + ' · 상품 ' + ng + ')' + (ng || looks.length ? '' : ' · dbg 보냄 — 메인에게 알려 주세요')); return; }
if (res) { alert('Ably 랭킹 보내기: ' + (res.text || '받지 않았어요')); return; }
if (!confirm('Ably 랭킹 보내기: 이 화면에서 바로 보내지 못했어요. 이 창에서 보낼까요? (보낸 뒤 뒤로 가기로 돌아와요)')) return;
const fm = D.createElement('form');
fm.method = 'POST'; fm.action = U; fm.acceptCharset = 'UTF-8';
F.via = 'form';
for (const k in F) { const i = D.createElement('input'); i.type = 'hidden'; i.name = k; i.value = F[k]; fm.appendChild(i); }
D.body.appendChild(fm);
fm.submit();
} catch (e) {
  alert('Ably 랭킹 보내기: 보내지 못했어요 (' + ((e && e.message) || e) + ')');
} finally { W.__abcap = 0; }
})();
