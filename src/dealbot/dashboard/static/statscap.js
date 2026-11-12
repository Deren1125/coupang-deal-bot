/* '통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음). 주인이 이미 로그인한 네이버 '내 통계' 화면에서 누르면
   보이는 글(열 수 있는 iframe 포함 · 머리·메뉴 줄 뺌)·표(줄마다 칸)·차트 글·주소·제목·고른 탭을 /dashboard/api/stats_capture 로 보낸다.
   쿠키는 대시보드에 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만. 딜봇 statscap.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 —
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음).
   11/11 덮개('저장 중 잠시만 기다려')가 보이는 동안 기다림 · 11/12 다른 출처 iframe 주소를 f 에, 숫자가 그 안에만 있으면 따로 열지 물음.
   11/12 주인 '한 번 누르면 다 모아서': 왼쪽 메뉴(같은 출처 링크)에서 필요한 화면을 골라 숨은 iframe 에 하나씩 열고(최대 90초 · 22화면)
   숫자가 뜰 때까지 기다려 읽고 화면마다 하나씩 보냄(n = 메뉴 이름). 화면 안 '더보기'를 3번까지 펼치고, 안쪽 탭(조회수/방문횟수 …)을
   눌러 하나씩, 일간 현황·유입분석은 '이전'(전날)도. 차트는 svg 글·숫자 든 data-*·aria 와 billboard·Highcharts 차트 값.
   화면이 읽어 온 같은 출처 JSON(performance 기록)은 주인 로그인으로 다시 받아 j 에. 메뉴가 없으면 예전처럼 이 화면 하나만. */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', W = window, D = document, L = location;
if (W.__stcap) { alert('통계 보내기: 보내는 중이에요'); return; }
if (!/(^|\.)naver\.com$/.test(L.hostname)) { alert('통계 보내기: 네이버 블로그 내 통계 화면에서 눌러 주세요'); return; }
W.__stcap = 1;
let ov = null;
try {
const NAV = 'header,nav,footer,[role=navigation],[role=banner],[role=contentinfo],#gnb,#lnb,.gnb,.lnb,#header,#footer';
const ROOT = 'main,[role=main],#content,#container,.content';
const SEL = '[aria-selected=true],[aria-current=page],[aria-current=true],.on,.active,.selected,.is_selected';
const CELL = '[role=cell],[role=gridcell],[role=columnheader],[role=rowheader]';
const GRID = '[role=grid],[role=table],[role=treegrid]';
const LOAD = /저장\s*중|잠시만\s*기다려/;
const STAT = /^https:\/\/([a-z0-9-]+\.)*naver\.com(:\d+)?\/.*(stat|referer|rank|inflow|visit|today)/i;
const sl = t => new Promise(r => setTimeout(r, t));
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/[\t\x20]+/g, ' ');
const sq = s => (s || '').replace(/\s+/g, ' ').trim();
const ld = l => l.length < 40 && LOAD.test(l);
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
const lines = n => tx(n).split('\n').map(sq).filter(l => l && !ld(l));
const cl = x => { const s = sq(tx(x)).slice(0, 200); return ld(s) ? null : s; };
const real = c => c.indexOf(null) < 0 && c.some(s => s && s !== '-');
const nz = n => (n.textContent || '').replace(/\s/g, '').length;
const busy = d => d.body && tx(d.body).split('\n').some(l => ld(l.trim()));
// 차트 값 (billboard.js bb.instance · Highcharts.charts — 같은 출처 창이면 읽힘)
const dt = v => v && v.getDate ? (v.getMonth() + 1) + '.' + v.getDate() : v;
const cv = w => {
  const o = [];
  try { for (const c of w.bb.instance) for (const s of c.data()) o.push('차트값 ' + s.id + ': ' + s.values.map(v => dt(v.x) + '=' + v.value).join(' ')); } catch (e) {}
  try { for (const c of w.Highcharts.charts) if (c) for (const s of c.series) o.push('차트값 ' + s.name + ': ' + s.data.map(p => (p.category || dt(new Date(p.x))) + '=' + p.y).join(' ')); } catch (e) {}
  return o;
};
// 한 화면 읽기 (T = 문서 · w = 덮개가 보이면 null)
const cap = (T, w) => {
  const docs = [T], fs = [], xo = [];
  const walk = d => {
    for (const f of d.querySelectorAll('iframe,frame')) {
      let x = null;
      try { x = f.contentDocument; } catch (e) {}
      const s = f.src || '';
      if (/^https?:/.test(s) && fs.indexOf(s) < 0 && fs.length < 12) fs.push(s);
      if (!x && /^https?:/.test(s) && xo.indexOf(s) < 0) xo.push(s);
      if (x && x.body && docs.indexOf(x) < 0 && docs.length < 12) { docs.push(x); walk(x); }
    }
  };
  try { walk(T); } catch (e) {}
  if (w && docs.some(busy)) return null;
  const out = [], rows = [], sel = [], ids = [], urls = [], js = [];
  let rn = 0;
  const add = (l, skip) => { if (l && !skip.has(l) && out.indexOf(l) < 0 && out.length < 3000) out.push(l); };
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
// 차트 글 (svg text·title·desc · 숫자 든 data-* · canvas 만 있으면 그렇다고 적음)
    for (const g of d.querySelectorAll('svg,canvas')) {
      const q = g.querySelectorAll('text,title,desc');
      let s = (q.length ? [...q] : [g]).map(t => (t.textContent || '').trim()).filter(x => x).join(' ');
      for (const e of [g, ...g.querySelectorAll('[data-value],[data-x],[data-y],[data-date],[data-count]')].slice(0, 400)) { for (const t of e.attributes || []) { if (/^data-/.test(t.name) && /\d/.test(t.value)) s += ' ' + t.name.slice(5) + '=' + t.value; } }
      s = sq(s).slice(0, 3000);
      if (/\d/.test(s)) add('차트: ' + s, skip);
      else if (g.tagName === 'CANVAS' && vis(g)) add('차트: canvas (글 없음)', skip);
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
    for (const a of d.querySelectorAll('a[href*="blog.naver.com/"]')) {
      const m = /blog\.naver\.com\/([A-Za-z0-9_-]{3,30})/.exec(a.href);
      if (m && ids.length < 10 && ids.indexOf(m[1]) < 0) ids.push(m[1]);
    }
// 화면이 읽어 온 같은 출처 fetch·XHR 주소 (나중에 JSON 이면 다시 받음)
    try { for (const e of d.defaultView.performance.getEntriesByType('resource')) { if (/^(fetch|xmlhttprequest)$/.test(e.initiatorType) && e.name.indexOf(L.origin + '/') === 0 && js.indexOf(e.name) < 0 && js.length < 4) js.push(e.name); } } catch (e) {}
  }
  for (const u of urls.concat(fs)) { try { const q = new URL(u).searchParams.get('blogId'); if (q && ids.indexOf(q) < 0) ids.unshift(q); } catch (e) {} }
  const x = out.join('\n').slice(0, 40000);
  return {js, ok: rows.length > 0 || out.filter(l => /\d/.test(l)).length > 2, sf: (!rows.length || !/\d/.test(x)) && xo.find(s => STAT.test(s)),
    F: {k: K, u: urls[0] || L.href, t: T.title || '', f: [...new Set(urls.slice(1).concat(fs))].slice(0, 12).join('\n'), b: ids.join(','), s: sel.join('\n'), x, r: JSON.stringify(rows).slice(0, 60000)}};
};
// 같은 출처 JSON 다시 받기 (주인 로그인 · 네이버 화면 안에서만)
const jget = async a => {
  const o = [];
  for (const u of a) { try { const r = await fetch(u, {credentials: 'include'}); if (/json/.test(r.headers.get('content-type') || '')) o.push(u + '\n' + (await r.text()).slice(0, 6000)); } catch (e) {} }
  return o.join('\n\n');
};
const send = async F => {
  try {
    const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)});
    return await r.json();
  } catch (e) { return null; }
};
const say = m => { try { if (!ov) { ov = D.createElement('div'); ov.style.cssText = 'position:fixed;top:8px;left:8px;z-index:2147483647;background:#222;color:#fff;padding:8px 12px;border-radius:8px;font:15px sans-serif'; D.body.appendChild(ov); } ov.textContent = m; } catch (e) {} };
const go = (m, sf) => { if (confirm(m + '\n통계 화면만 따로 열까요? 열린 화면에서 책갈피를 한 번 더 눌러 주세요')) L.href = sf; };
// 모아 보낼 화면 (메뉴 이름 · 공백 뺌 · 먼저 쓴 것부터)
const WANT = '유입분석,일간현황,유입검색어,검색어,조회수순위,시간대분석,조회수,순방문자수,방문횟수,평균방문횟수,재방문율,평균사용시간,성별·연령별분포,기기별분포,이웃방문현황,이웃증감수,이웃증감분석,공감수순위,댓글수순위,블로그평균데이터'.split(',');
const YD = ['유입분석', '일간현황'];
const nk = s => sq(s).replace(/\s/g, '');
const lk = e => { const h = (e.getAttribute && e.getAttribute('href')) || ''; return h && h[0] !== '#' && !/^javascript:/i.test(h); };
let tabs = [];
for (const a of D.querySelectorAll('a[href]')) {
  const n = sq(a.textContent), h = a.href || '';
  if (WANT.indexOf(nk(n)) >= 0 && h.indexOf(L.origin + '/') === 0 && h.indexOf('#') < 0 && !tabs.some(t => t[0] === n || t[1] === h)) tabs.push([n, h]);
}
const pm = /^\/[\w-]+\/stat\//.exec(L.pathname);
if (tabs.length < 2 && pm && /조회수순위/.test(nk(tx(D.body)))) tabs = [['일간 현황', 'today'], ['유입분석', 'referer'], ['조회수 순위', 'rank_pv']].map(t => [t[0], L.origin + pm[0] + t[1]]);
tabs.sort((a, b) => WANT.indexOf(nk(a[0])) - WANT.indexOf(nk(b[0])));
tabs = tabs.slice(0, 22);
if (tabs.length > 1) {
  const t0 = Date.now(), left = () => Date.now() - t0 < 90000, xs = [], seen = new Set();
  let n = 0, tried = 0, cut = 0;
  const fr = D.createElement('iframe');
  fr.style.cssText = 'position:fixed;left:-5000px;top:0;width:1200px;height:1000px;border:0';
  D.body.appendChild(fr);
  const doc = () => { try { return fr.contentDocument; } catch (e) { return null; } };
  const put = async (c, nm) => {
    if (!c || seen.has(c.F.u + c.F.x) || n >= 40) return;
    seen.add(c.F.u + c.F.x);
    c.F.n = nm;
    c.F.j = await jget(c.js);
    const r = await send(c.F);
    if (r && r.ok) n++;
  };
// 누르고 글이 바뀌고 숫자가 뜰 때까지 (4초까지)
  const press = async (T, e, p) => {
    try { e.click(); } catch (x) {}
    let c = null;
    for (let j = 0; j < 10 && left(); j++) { await sl(400); c = cap(T, 0); if (c.F.x !== p && c.ok) return c; }
    return null;
  };
// 누를 것 (메뉴·진짜 링크 빼고 · 안쪽 같은 출처 iframe 도)
  const btns = T => [T, ...[...T.querySelectorAll('iframe')].map(f => { try { return f.contentDocument; } catch (e) { return null; } })].filter(d => d).flatMap(d => [...d.querySelectorAll('a,button,[role=tab]')]).filter(e => !lk(e) && !(e.closest && e.closest(NAV)) && vis(e));
  for (const [nm, h] of tabs) {
    if (!left()) { cut = 1; break; }
    tried++;
    say(tried + '/' + tabs.length + ' ' + nm + '…');
    await new Promise(r => { fr.onload = r; fr.src = h; setTimeout(r, 12000); });
    let c = null, T = null;
    for (let j = 0; j < 14 && left(); j++) {
      T = doc();
      c = T && T.body ? cap(T, 0) : null;
      if (c && (c.ok || (c.sf && j > 3))) break;
      await sl(500);
    }
    if (!c) continue;
    if (c.sf && !c.ok) { xs.push(c.sf); await put(c, nm); continue; }
// '더보기' 3번까지 펼침
    for (let k = 0; k < 3 && left(); k++) {
      const m = btns(T).find(e => /^더보기/.test(sq(e.textContent)));
      if (!m) break;
      c = (await press(T, m, c.F.x)) || cap(T, 0);
    }
    await put(c, nm);
// 안쪽 탭 (li 안 링크·단추 · role=tab — 숫자·동영상·다운로드·주간·월간·로그아웃·삭제·설정 등 빼고 4개까지)
    const sub = [], sn = [];
    for (const e of btns(T)) {
      const s = sq(e.textContent), li = e.closest && e.closest('li');
      if (sub.length < 4 && s.length > 1 && s.length < 16 && !/\d|동영상|다운로드|주간|월간|더보기|이전|다음|로그|삭제|설정|탈퇴|저장|공유|닫기/.test(s) && sn.indexOf(s) < 0 && (e.getAttribute('role') === 'tab' || (li && li.parentNode.children.length < 9))) { sub.push(e); sn.push(s); }
    }
    for (let k = 0; k < sub.length && left(); k++) { say(tried + '/' + tabs.length + ' ' + nm + ' · ' + sn[k] + '…'); await put(await press(T, sub[k], c.F.x), nm + ' · ' + sn[k]); }
// 일간 현황·유입분석은 전날도 (날짜 옆 '이전' 단추)
    if (YD.indexOf(nk(nm)) >= 0 && left()) {
      const p = btns(T).find(e => /이전|prev/i.test(e.textContent + (e.getAttribute('aria-label') || '') + (e.getAttribute('title') || '') + e.className) && !/페이지|page/i.test(e.className + e.textContent));
      if (p) { say(tried + '/' + tabs.length + ' ' + nm + ' · 전날…'); await put(await press(T, p, cap(T, 0).F.x), nm + ' · 전날'); }
    }
  }
  fr.remove();
  if (xs.length && xs.length === tried) { ov.remove(); ov = null; go('통계 보냄 ✓ ' + n + '개 화면 — 숫자는 안쪽 화면에 있어요.', xs[0]); return; }
  if (n) { say('통계 보냄 ✓ ' + n + '개 화면'); alert('통계 보냄 ✓ ' + n + '개 화면' + (cut ? ' (90초 넘어 ' + (tabs.length - tried) + '개 못 봄)' : '')); return; }
  if (ov) { ov.remove(); ov = null; }
}
let c = null;
for (let i = 0; i < 10 && !(c = cap(D, 1)); i++) await sl(500);
c = c || cap(D, 0);
const F = c.F, sf = c.sf;
F.j = await jget(c.js);
const res = await send(F);
if (sf) { go(res && res.ok ? '통계 보냄 ✓ (' + res.label + ') — 숫자는 안쪽 화면에 있어요.' : '통계 보내기: 이 화면에는 숫자가 없어요.', sf); return; }
if (res && res.ok) { alert('통계 보냄 ✓ (' + res.label + ')'); return; }
if (res) { alert('통계 보내기: ' + (res.text || '받지 않았어요')); return; }
if (!confirm('통계 보내기: 이 화면에서 바로 보내지 못했어요. 이 창에서 보낼까요? (보낸 뒤 뒤로 가기로 돌아와요)')) return;
const fm = D.createElement('form');
fm.method = 'POST'; fm.action = U; fm.acceptCharset = 'UTF-8';
F.via = 'form';
for (const k in F) { const i = D.createElement('input'); i.type = 'hidden'; i.name = k; i.value = F[k]; fm.appendChild(i); }
D.body.appendChild(fm);
fm.submit();
} catch (e) {
  alert('통계 보내기: 보내지 못했어요 (' + ((e && e.message) || e) + ')');
} finally { W.__stcap = 0; if (ov) ov.remove(); }
})();
