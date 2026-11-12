/* '통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음). 주인이 이미 로그인한 네이버 '내 통계' 화면에서 누르면
   보이는 글(열 수 있는 iframe 포함 · 머리·메뉴 줄 뺌)·표(줄마다 칸)·차트 글·주소·제목·고른 탭을 /dashboard/api/stats_capture 로 보낸다.
   쿠키는 대시보드에 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만. 딜봇 statscap.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 —
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음).
   11/11 덮개('저장 중 잠시만 기다려')가 보이는 동안 기다림.
   11/12 15:24 주인 아이패드: 관리 화면(admin.blog.naver.com/<id>/stat/…) 18개 화면이 모두 겉 글뿐 — 숫자는 다른 출처 iframe
   blog.stat.naver.com/blog/user/<…>?blogId= 안. 그래서 두 단계:
   1단계 관리 화면: 메뉴 화면(AP)을 숨은 같은 출처 iframe 에 하나씩 열어 안쪽 iframe 의 src 속성(다른 출처라도 읽힘)만 모음 →
     목록을 '주소 목록' 하나로 보내고(주소 배우기) 묻고 첫 통계 화면으로 옮김 (목록 = #sc=base64 {b: blogId, l: [[이름, 경로]]} ·
     proxyReferer·isIE8·blogId 뺌). 메뉴가 없는 화면에서 숫자가 다른 출처 iframe 에만 있으면 그 하나를 같은 방식으로.
   2단계 통계 앱(blog.stat.naver.com): 목록 = #sc= → 이 출처 localStorage 'stcap' → 앱 메뉴(/user/ 링크) → 유입분석 하나.
     목록을 localStorage 에 두니 다음부턴 통계 화면을 바로 열고 한 번. 숨은 iframe 에 하나씩 열고(최대 150초) 숫자가 뜰 때까지 기다려
     읽고 화면마다 하나씩 보냄(n = 메뉴 이름). '더보기' 3번까지, 안쪽 탭 4개까지, 일간 현황·유입분석은 '이전'(전날)도.
   차트: svg 글·숫자 든 data-*·aria · billboard 값 · 값 없는 'day-N' 줄은 뺌 · 창 전역(빈 iframe 창에 없는 이름)에서 숫자 7~62개 배열·
   날짜 키 객체와 script[type*=json] → j · 화면이 읽어 온 같은 출처 JSON(performance 기록)은 주인 로그인으로 다시 받아 j 에.
   11/12 16:0x 실사용 고침: '불러오고 있습니다'가 사라지고 숫자(날짜·'0건' 말고)가 두 번 같을 때 읽음 · 못 읽은 첫 화면은 안 보냄(다른 게
   하나도 없을 때만) · '일간' 단추가 있으면 먼저 누름 · day-N 은 aria·title 에서도 뺌 · j 는 날짜·번호 키 숫자 5개 넘는 것만, 분석·태그 전역 뺌.
   (20KB 안에 들게 '이 창에서 폼으로 보내기'와 메뉴 없는 관리 화면의 짐작 목록을 뺌 — 두 출처 모두 fetch 가 됨.) */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', W = window, D = document, L = location, SB = '통계 보내기: ', OK = '통계 보냄 ✓ ', AG = ' 열린 화면에서 책갈피를 한 번 더 눌러 주세요';
if (W.__stcap) { alert(SB + '보내는 중'); return; }
if (!/(^|\.)naver\.com$/.test(L.hostname)) { alert(SB + '네이버 내 통계에서 눌러 주세요'); return; }
W.__stcap = 1;
let ov = null;
try {
const NAV = 'header,nav,footer,[role=navigation],[role=banner],[role=contentinfo],#gnb,#lnb,.gnb,.lnb,#header,#footer';
const ROOT = 'main,[role=main],#content,#container,.content';
const SEL = '[aria-selected=true],[aria-current=page],.on,.active,.selected';
const CELL = '[role=cell],[role=gridcell],[role=columnheader],[role=rowheader]';
const GRID = '[role=grid],[role=table],[role=treegrid]';
const LOAD = /저장\s*중|잠시만\s*기다려|불러오고\s*있습니다/;
const STAT = /^https:\/\/([a-z0-9-]+\.)*(naver\.com(:\d+)?\/.*(stat|referer|rank|inflow|visit|today)|stat\.naver\.com\/)/i;
const sl = t => new Promise(r => setTimeout(r, t));
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/day-\d+/g, '').replace(/[\t\x20]+/g, ' ');
const sq = s => (s || '').replace(/\s+/g, ' ').trim();
const ld = l => l.length < 40 && LOAD.test(l);
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
const lines = n => tx(n).split('\n').map(sq).filter(l => l && !ld(l));
const cl = x => { const s = sq(tx(x)).slice(0, 200); return ld(s) ? null : s; };
const real = c => c.indexOf(null) < 0 && c.some(s => s && s !== '-');
const nz = n => (n.textContent || '').replace(/\s/g, '').length;
const busy = d => d.body && tx(d.body).split('\n').some(l => ld(l.trim()));
// 차트 값 (billboard.js bb.instance — 같은 출처 창이면 읽힘 · 다른 차트는 아래 창 전역 찾기)
const dt = v => v && v.getDate ? (v.getMonth() + 1) + '.' + v.getDate() : v;
const cv = w => {
  const o = [];
  try { for (const c of w.bb.instance) for (const s of c.data()) o.push('차트값 ' + s.id + ': ' + s.values.map(v => dt(v.x) + '=' + v.value).join(' ')); } catch (e) {}
  return o;
};
// 창 전역에서 숫자 7~62개 배열·날짜 키 객체 찾기 (빈 iframe 창에 있는 이름은 뺌 · 3겹까지)
let bk = null;
const wv = w => {
  if (!bk) { bk = new Set(); try { const f = D.createElement('iframe'); D.body.appendChild(f); bk = new Set(Object.keys(f.contentWindow)); f.remove(); } catch (e) {} }
  const o = [];
  const sc = (v, p, k) => {
    if (o.length > 5 || k > 3 || !v || typeof v !== 'object' || v.nodeType || v.window === v) return;
    const ks = Object.keys(v).slice(0, 80);
    const ok = Array.isArray(v) ? v.every(x => typeof x === 'number' || (x && typeof x === 'object' && Object.values(x).some(y => typeof y === 'number') && /20\d\d\D?\d\d\D?\d\d/.test(JSON.stringify(x)))) : ks.every(q => /^\d+$|20\d\d\D?\d\d\D?\d\d|^\d\d?\.\d\d?$/.test(q) && typeof v[q] === 'number');
    if (ok && ks.length > 4 && ks.length < 63) { o.push(p + '\n' + JSON.stringify(v).slice(0, 4000)); return; }
    for (const q of ks.slice(0, 40)) { try { sc(v[q], p + '.' + q, k + 1); } catch (e) {} }
  };
  for (const q of Object.keys(w)) { if (!bk.has(q) && !/^(_?ga|gtag|gNTM|NTM|nlog|lcs|wcs|dataLayer|google|_gaq|__ntm|ntm)/i.test(q)) try { sc(w[q], q, 0); } catch (e) {} }
  return o;
};
// 한 화면 읽기 (T = 문서 · w = 덮개가 보이면 null)
const cap = (T, w) => {
  const docs = [T], fs = [];
  const walk = d => {
    for (const f of d.querySelectorAll('iframe,frame')) {
      let x = null;
      try { x = f.contentDocument; } catch (e) {}
      const s = f.src || '';
      if (/^https?:/.test(s) && fs.indexOf(s) < 0 && fs.length < 12) fs.push(s);
      if (x && x.body && docs.indexOf(x) < 0 && docs.length < 12) { docs.push(x); walk(x); }
    }
  };
  try { walk(T); } catch (e) {}
  if (w && docs.some(busy)) return null;
  const out = [], rows = [], sel = [], ids = [], urls = [], js = [], wj = [];
  let rn = 0;
  const add = (l, skip) => { l = sq(l.replace(/day-\d+/g, '')); if (l && !skip.has(l) && out.indexOf(l) < 0 && out.length < 3000) out.push(l); };
  for (const d of docs) {
    const b = d.body;
    if (!b) continue;
    try { urls.push(d.location.href); } catch (e) {}
    let root = b, rl = lines(b);
    for (const n of d.querySelectorAll(ROOT)) {
      if (!vis(n)) continue;
      const l = lines(n), z = l.join('\n').length;
      if (z >= 200 || z > rl.join('\n').length) { root = n; rl = l; break; }
    }
    const skip = new Set();
    for (const n of b.querySelectorAll(NAV)) { if (!n.contains(root)) lines(n).forEach(l => skip.add(l)); }
// 링크 칸(링크 2개 넘음): 링크·단추 글과 같은 숫자 없는 줄을 뺌 · 링크 4개 넘고 글 대부분이 짧은 링크(표·차트 없음)면 숫자 없는 줄 모두 뺌
    let nc = 0;
    for (const n of d.querySelectorAll('ul,ol,dl,aside,section,div')) {
      if (nc++ > 600) break;
      if (n.contains(root)) continue;
      const a = [...n.querySelectorAll('a,button')];
      if (a.length < 2) continue;
      const at = new Set(a.map(x => sq(x.textContent)));
      const al = a.reduce((s, x) => s + nz(x), 0);
      const nv = a.length >= 4 && !n.querySelector('table,[role=row],svg,canvas') && al >= nz(n) * 0.6 && al <= a.length * 20;
      lines(n).forEach(l => { if (!/\d/.test(l) && (nv || at.has(l))) skip.add(l); });
    }
    let last = '';
    for (const l of rl) { if (l !== last && !skip.has(l)) { out.push(l); last = l; } }
// 열린 shadow root 글 (3겹까지)
    const sw = (r, k) => { for (const e of r.querySelectorAll('*')) { if (e.shadowRoot && k < 3) { for (const c of e.shadowRoot.children) lines(c).forEach(l => add(l, skip)); sw(e.shadowRoot, k + 1); } } };
    try { sw(d, 0); } catch (e) {}
// 차트 글 (svg text·title·desc · 숫자 든 data-*)
    for (const g of d.querySelectorAll('svg')) {
      const q = g.querySelectorAll('text,title,desc');
      let s = (q.length ? [...q] : [g]).map(t => (t.textContent || '').trim()).filter(x => x).join(' ');
      for (const e of [g, ...g.querySelectorAll('*')].slice(0, 400)) { for (const t of e.attributes || []) { if (/^data-/.test(t.name) && /\d/.test(t.value)) s += ' ' + t.name.slice(5) + '=' + t.value; } }
      s = sq(s.replace(/day-\d+/g, '')).slice(0, 3000);
      if (/\d/.test(s)) add('차트: ' + s, skip);
    }
    try { cv(d.defaultView).forEach(l => add(l.slice(0, 3000), skip)); } catch (e) {}
    for (const n of d.querySelectorAll('[aria-label],[title]')) {
      for (const k of ['aria-label', 'title']) { const v = sq(n.getAttribute && n.getAttribute(k)); if (v.length < 300 && /\d/.test(v)) add(v, skip); }
    }
    for (const n of d.querySelectorAll(SEL)) { const s = cl(n); if (s && s.length < 40 && sel.length < 12 && sel.indexOf(s) < 0 && vis(n)) sel.push(s); }
    for (const t of d.querySelectorAll('table')) {
      if (rn >= 300) break;
      if (!vis(t)) continue;
      const tb = [];
      for (const r of t.rows) {
        if (rn >= 300) break;
        const c = [...r.cells].slice(0, 12).map(cl);
        if (real(c)) { tb.push(c); rn++; }
      }
      if (tb.length) rows.push(tb);
    }
// div 로 만든 표 (role=row 안 role=cell·gridcell·columnheader) — table 안 줄은 위에서 읽음
    const gm = new Map();
    for (const r of d.querySelectorAll('[role=row]')) {
      if (rn >= 300) break;
      if ((r.closest && r.closest('table')) || !vis(r)) continue;
      const c = [...r.querySelectorAll(CELL)].slice(0, 12).map(cl);
      if (!real(c)) continue;
      const g = (r.closest && r.closest(GRID)) || d;
      if (!gm.has(g)) gm.set(g, []);
      gm.get(g).push(c);
      rn++;
    }
    for (const tb of gm.values()) rows.push(tb);
// 화면이 읽어 온 같은 출처 fetch·XHR 주소 (나중에 JSON 이면 다시 받음)
    try { for (const e of d.defaultView.performance.getEntriesByType('resource')) { if (/^(fetch|xmlhttprequest)$/.test(e.initiatorType) && e.name.indexOf(L.origin + '/') === 0 && js.indexOf(e.name) < 0 && js.length < 6) js.push(e.name); } } catch (e) {}
// 화면이 들고 있는 날짜별 숫자 (창 전역 · script json)
    try { wv(d.defaultView).forEach(v => wj.push(v)); } catch (e) {}
    for (const t of d.querySelectorAll('script[type*=json]')) { const v = t.textContent || ''; if (/\d/.test(v) && wj.length < 8) wj.push('script\n' + v.slice(0, 4000)); }
  }
  for (const u of urls.concat(fs)) { try { const q = new URL(u).searchParams.get('blogId'); if (q && ids.indexOf(q) < 0) ids.unshift(q); } catch (e) {} }
  const x = out.join('\n').slice(0, 40000);
  return {js, wj, ok: rows.length > 0 || out.filter(l => /\d/.test(l.replace(/20\d\d\.\d+\.\d+\.?|(^|\D)0건/g, ''))).length > 2,
    F: {k: K, u: urls[0] || L.href, t: T.title || '', f: [...new Set(urls.slice(1).concat(fs))].slice(0, 12).join('\n'), b: ids.join(','), s: sel.join('\n'), x, r: JSON.stringify(rows).slice(0, 60000)}};
};
// 같은 출처 JSON 다시 받기 (주인 로그인 · 네이버 화면 안에서만)
const jget = async a => {
  const o = [];
  for (const u of a) { try { const r = await fetch(u, {credentials: 'include'}); if (/json/.test(r.headers.get('content-type') || '')) o.push(u + '\n' + (await r.text()).slice(0, 6000)); } catch (e) {} }
  return o;
};
const send = async F => {
  try {
    const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)});
    return await r.json();
  } catch (e) { return null; }
};
const say = m => { try { if (!ov) { ov = D.createElement('div'); ov.style.cssText = 'position:fixed;top:0;z-index:2147483647;background:#222;color:#fff;padding:8px;font:15px sans-serif'; D.body.appendChild(ov); } ov.textContent = m; } catch (e) {} };
// 모아 보낼 화면 (관리 화면 메뉴 주소 끝 · 먼저 쓴 것부터) · 통계 앱 주소에서 뺄 것
const AP = 'referer,today,visit_pv,uv,visit,frequency,retention,averageDuration,hour_cv,demo,device,relationDist,relationDelta,relationDemo,rank_pv,rank_like,rank_comment,average_upperGroup'.split(',');
const NO = /동영상|재생|국가|다운로드/;
const lk = e => { const h = (e.getAttribute && e.getAttribute('href')) || ''; return h && h[0] !== '#' && !/^javascript:/i.test(h); };
const qid = u => { try { return new URL(u, L.href).searchParams.get('blogId') || ''; } catch (e) { return ''; } };
const sp = x => { for (const k of ['blogId', 'proxyReferer', 'isIE8']) x.searchParams.delete(k); return x.pathname + x.search; };
const fu = (u, b) => L.origin + u + (b ? (u.indexOf('?') < 0 ? '?' : '&') + 'blogId=' + b : '');
const t0 = Date.now(), left = () => Date.now() - t0 < 150000;
const mk = () => { const f = D.createElement('iframe'); f.style.cssText = 'position:fixed;left:-5000px;top:0;width:1200px;height:1000px;border:0'; D.body.appendChild(f); return f; };
const open = (f, h) => new Promise(r => { f.onload = r; f.src = h; setTimeout(r, 12000); });
const dj = async c => c.wj.concat(await jget(c.js)).join('\n\n');
// 숨은 iframe 에 하나씩 열어 숫자가 뜰 때까지 기다려 읽고 화면마다 하나씩 보냄 → [보낸 수, 시간 넘음, 연 수]
const run = async tabs => {
  const seen = new Set(), fr = mk();
  let n = 0, tried = 0, cut = 0;
  const put = async (c, nm) => {
    if (!c || seen.has(c.F.u + c.F.x) || n >= 60) return;
    seen.add(c.F.u + c.F.x);
    c.F.n = nm;
    c.F.j = await dj(c);
    const r = await send(c.F);
    if (r && r.ok) n++;
  };
// 누르고 글이 바뀌고 '불러오고 있습니다' 가 사라지고 숫자가 두 번 같을 때까지 (6초까지)
  const press = async (T, e, p) => {
    try { e.click(); } catch (x) {}
    let c = null;
    for (let j = 0, v = ''; j < 16 && left(); j++) { await sl(400); c = cap(T, 1); if (c && c.ok && c.F.x !== p && c.F.x === v) return c; v = c ? c.F.x : ''; }
    return null;
  };
// 누를 것 (메뉴·진짜 링크 빼고 · 안쪽 같은 출처 iframe 도)
  const btns = T => [T, ...[...T.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (e) { return null; } })].filter(d => d).flatMap(d => [...d.querySelectorAll('a,button,[role=tab]')]).filter(e => !lk(e) && !(e.closest && e.closest(NAV)) && vis(e));
  for (const [nm, h] of tabs) {
    if (!left()) { cut = 1; break; }
    tried++;
    say(tried + '/' + tabs.length + ' ' + nm + '…');
    await open(fr, h);
    let c = null, T = null, v = '', m0 = n;
    for (let j = 0; j < 30 && left(); j++) {
      T = fr.contentDocument;
      c = T && T.body ? cap(T, 1) : null;
      if (c && c.ok && c.F.x === v) break;
      v = c ? c.F.x : '';
      await sl(500);
    }
    if (!T || !T.body) continue;
    c = c || cap(T, 0);
// 일간·주간 단추가 있으면 일간 먼저 (이미 골랐으면 그대로)
    const dy = btns(T).find(e => sq(e.textContent) === '일간' && !(e.matches(SEL) || e.parentNode.matches(SEL)));
    if (dy) c = (await press(T, dy, c.F.x)) || c;
// '더보기' 3번까지 펼침
    for (let k = 0; k < 3 && left(); k++) {
      const m = btns(T).find(e => /^더보기/.test(sq(e.textContent)));
      if (!m) break;
      c = (await press(T, m, c.F.x)) || cap(T, 0);
    }
    if (c.ok) await put(c, nm);
// 안쪽 탭 (li 안 링크·단추 · role=tab — 숫자·동영상·다운로드·주간·월간·로그아웃·삭제·설정 등 빼고 4개까지)
    const sub = [], sn = [];
    for (const e of btns(T)) {
      const s = sq(e.textContent), li = e.closest && e.closest('li');
      if (sub.length < 4 && s.length > 1 && s.length < 16 && !NO.test(s) && !/\d|일간|주간|월간|더보기|이전|다음|로그|삭제|설정/.test(s) && sn.indexOf(s) < 0 && (e.getAttribute('role') === 'tab' || (li && li.parentNode.children.length < 9))) { sub.push(e); sn.push(s); }
    }
    for (let k = 0; k < sub.length && left(); k++) await put(await press(T, sub[k], c.F.x), nm + ' · ' + sn[k]);
// 일간 현황·유입분석은 전날도 (날짜 옆 '이전' 단추)
    if (/유입|일간|referer|today/.test(nm) && left()) {
      const p = btns(T).find(e => /이전|prev/i.test(e.textContent + (e.getAttribute('aria-label') || '') + (e.getAttribute('title') || '') + e.className) && !/페이지|page/i.test(e.className + e.textContent));
      if (p) await put(await press(T, p, cap(T, 0).F.x), nm + ' · 전날');
    }
// 다 못 읽었으면 마지막 화면이라도
    if (n === m0) await put(cap(T, 0), nm);
  }
  fr.remove();
  return [n, cut, tried];
};
const done = (r, len, tip) => { alert(r[0] ? OK + r[0] + '개 화면' + (r[1] ? ' (시간 넘음 · ' + (len - r[2]) + '개 남음)' : '') + tip : SB + '숫자를 못 읽었어요'); };
// 2단계 — 통계 앱(blog.stat.naver.com): 주소 #sc= → 이 출처에 저장해 둔 목록 → 앱 메뉴 → 아는 것
let P = null, first = 0, fb = 0;
const hm = /[#&]sc=([^&]+)/.exec(L.hash || '');
try { if (hm) { P = JSON.parse(decodeURIComponent(escape(atob(hm[1])))); first = 1; } } catch (e) {}
try { P = P || JSON.parse(W.localStorage.getItem('stcap')); } catch (e) {}
if (P || /(^|\.)stat\.naver\.com$/.test(L.hostname)) {
  let l = (P && P.l) || [];
  if (!l.length) {
    for (const a of D.querySelectorAll('a[href]')) {
      const n = sq(a.textContent), u = a.origin === L.origin && /\/user\//.test(a.pathname) && sp(new URL(a.href));
      if (u && n && n.length < 20 && !NO.test(n) && !l.some(e => e[1] === u)) l.push([n, u]);
    }
    if (l.length < 2) { fb = 1; l = [['유입분석', '/blog/user/referer/total']]; }
  }
  P = {b: qid(L.href) || (P && P.b) || '', l: l.slice(0, 30)};
  try { if (!fb) W.localStorage.setItem('stcap', JSON.stringify(P)); } catch (e) {}
  done(await run(P.l.map(e => [e[0], fu(e[1], P.b)])), P.l.length, first ? '\n다음엔 이 화면을 열고 한 번' : '');
  return;
}
// 1단계 — 관리 화면(admin.blog.naver.com): 메뉴 화면마다 안쪽 통계 iframe 의 src 속성(다른 출처라도 읽힘)을 모아 목록
let tabs = [];
for (const a of D.querySelectorAll('a[href]')) {
  const m = a.origin === L.origin && lk(a) && /\/stat\/(\w+)$/.exec(a.pathname);
  if (m && AP.indexOf(m[1]) >= 0 && !tabs.some(t => t[2] === m[1])) tabs.push([sq(a.textContent) || m[1], a.href, m[1]]);
}
const pm = /^\/([\w-]+)\/stat\//.exec(L.pathname);
tabs.sort((a, b) => AP.indexOf(a[2]) - AP.indexOf(b[2]));
const sx = T => {
  for (const f of T.querySelectorAll('iframe,frame')) {
    let x = null, y = null;
    try { x = new URL(f.getAttribute('src') || '', T.baseURI); } catch (e) {}
    if (x && x.origin !== L.origin && STAT.test(x.href)) return x;
    try { y = f.contentDocument && sx(f.contentDocument); } catch (e) {}
    if (y) return y;
  }
  return null;
};
// 찾은 목록을 한 번 보내고(주소 배우기) 첫 통계 화면으로 옮김 (iframe 이 쓰던 주소 그대로 · 목록은 #sc= 에)
const hop = async (l, o, b, fs) => {
  await send({k: K, u: L.href, t: D.title, n: '주소 목록', b, f: fs.join('\n'), x: l.map(e => e[0] + ' ' + o + e[1]).join('\n')});
  if (ov) { ov.remove(); ov = null; }
  if (confirm(l.length + '개 찾음 ✓\n통계 화면으로 옮겨서 모을까요?' + AG)) L.href = fs[0].split('#')[0] + '#sc=' + btoa(unescape(encodeURIComponent(JSON.stringify({b, l}))));
};
if (tabs.length > 1) {
  const fr = mk(), l = [], fs = [];
  let o = '', b = '', i = 0;
  for (const [nm, h] of tabs) {
    if (!left()) break;
    say((++i) + '/' + tabs.length + ' ' + nm + '…');
    await open(fr, h);
    let x = null;
    for (let j = 0; j < 25 && !x && left(); j++) { try { x = sx(fr.contentDocument); } catch (e) {} if (!x) await sl(200); }
    if (!x || (o && x.origin !== o)) continue;
    o = x.origin;
    b = b || qid(x.href) || (pm && pm[1]) || '';
    fs.push(x.href);
    const u = sp(x);
    if (!l.some(e => e[1] === u)) l.push([nm, u]);
  }
  fr.remove();
  if (l.length) { await hop(l, o, b, fs); return; }
  if (ov) { ov.remove(); ov = null; }
}
let c = null;
for (let i = 0; i < 10 && !(c = cap(D, 1)); i++) await sl(500);
c = c || cap(D, 0);
const F = c.F, x = !c.ok && sx(D);
F.j = await dj(c);
const res = await send(F);
// 숫자가 다른 출처 iframe 안에만 있으면 그 화면으로 옮길지 물음
if (x) { const b = qid(x.href), h = x.href; await hop([[D.title || '통계', sp(x)]], x.origin, b, [h]); return; }
if (res && res.ok) { alert(OK + '(' + res.label + ')'); return; }
alert(SB + ((res && res.text) || '못 보냄'));
} catch (e) {
  alert(SB + '못 보냄 (' + ((e && e.message) || e) + ')');
} finally { W.__stcap = 0; if (ov) ov.remove(); }
})();
