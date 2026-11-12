/* 'Ably 랭킹 보내기' 책갈피 (주인 11/12 — 서버는 에이블리를 열지 않음 · 10/28 원칙 그대로). 주인이 아이패드 사파리·갤럭시 크롬에서
   에이블리 랭킹 화면(a-bly.com · m.a-bly.com …)을 연 채 누르면, 보이는 순위 카드(순위·상품명·쇼핑몰·가격·할인·리뷰 수·별점·찜, 상품 주소·사진 주소)와
   코디 카드(코디 제목·좋아요·든 상품), 주소·제목·고른 탭, 보이는 글을 대시보드 /dashboard/api/ably_rank_capture 로 보낸다.
   쿠키는 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만. 바로 보내지 못하면 이 창에서 폼으로 보낼지 묻는다.
   딜봇 ablyrank.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 — statscap.js 와 같은 규칙:
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). 카드가 아직 안 떴으면 0.5초마다 최대 5초 기다린다. */
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
const found = () => D.querySelector('a[href*="/goods/"]') || [...D.querySelectorAll('a[href]')].some(a => LOOK.test(href(a)));
const busy = () => tx(D.body).split('\n').some(l => l.trim().length < 40 && LOAD.test(l.trim()));
for (let i = 0; i < 10 && (busy() || !found()); i++) await new Promise(r => setTimeout(r, 500));
const items = [], looks = [], seen = new Set(), lseen = new Set();
// 코디 카드 (코디 링크 하나 + 안에 든 상품 링크들)
for (const a of links(D, LOOK)) {
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
for (const n of D.querySelectorAll(SEL)) { const s = lines(n)[0] || ''; if (s && s.length < 40 && sel.length < 12 && sel.indexOf(s) < 0 && vis(n)) sel.push(s); }
const heads = [...D.querySelectorAll('h1,h2,h3')].filter(vis).map(n => lines(n)[0] || '').filter(Boolean).slice(0, 8);
const out = [];
let last = '';
for (const l of lines(D.body)) { if (l !== last) { out.push(l); last = l; } }
let j = JSON.stringify({items: items, looks: looks});
while (j.length > 60000 && (items.length || looks.length)) { if (items.length) items.pop(); else looks.pop(); j = JSON.stringify({items: items, looks: looks}); }
const F = {k: K, u: location.href, t: D.title || '', s: sel.join('\n'), h: heads.join('\n'), j: j, x: out.join('\n').slice(0, 15000)};
let res = null;
try {
  const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)});
  res = await r.json();
} catch (e) { res = null; }
if (res && res.ok) { alert('Ably 랭킹 보냄 ✓ (' + res.label + ')'); return; }
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
