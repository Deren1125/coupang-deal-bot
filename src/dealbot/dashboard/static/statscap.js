/* '통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음). 주인이 아이패드 사파리에서 이미 로그인한 네이버 '내 통계'
   화면(일간 현황·조회수 순위·유입 분석·검색어·방문 분석·평균 사용 시간 …)을 연 채 누르면, 보이는 글(열 수 있는 iframe 포함 · 머리·메뉴 줄 뺌)과
   표(줄마다 칸)와 주소·제목·고른 탭을 대시보드 /dashboard/api/stats_capture 로 보낸다. 쿠키는 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만.
   바로 보내지 못하면(화면이 막음) 이 창에서 폼으로 보낼지 묻는다. 딜봇 statscap.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 —
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음).
   11/11 밤 갤럭시 크롬 첫 실사용: 세 화면 모두 '저장 중 잠시만 기다려 주세요.' 덮개 글만 왔다 → 덮개·로딩 글 줄과 안 보이는 요소는 빼고,
   그 글이 보이는 동안 0.5초마다 최대 5초 기다리고, ROOT 글이 200자보다 짧으면 body 전체(메뉴 줄 뺌)를 읽고, div 표([role=row])도 읽는다. */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', W = window, D = document;
if (W.__stcap) { alert('통계 보내기: 보내는 중이에요'); return; }
if (!/(^|\.)naver\.com$/.test(location.hostname)) { alert('통계 보내기: 네이버 블로그 내 통계 화면에서 눌러 주세요'); return; }
W.__stcap = 1;
try {
const docs = [D];
const walk = d => {
  for (const f of d.querySelectorAll('iframe,frame')) {
    let x = null;
    try { x = f.contentDocument; } catch (e) {}
    if (x && x.body && docs.indexOf(x) < 0 && docs.length < 12) { docs.push(x); walk(x); }
  }
};
try { walk(D); } catch (e) {}
const NAV = 'header,nav,footer,[role=navigation],[role=banner],[role=contentinfo],#gnb,#lnb,.gnb,.lnb,#header,#footer';
const ROOT = 'main,[role=main],#content,#container,.content';
const SEL = '[aria-selected=true],[aria-current=page],[aria-current=true],.on,.active,.selected,.is_selected';
const CELL = '[role=cell],[role=gridcell],[role=columnheader],[role=rowheader]';
const GRID = '[role=grid],[role=table],[role=treegrid]';
const LOAD = /저장\s*중|잠시만\s*기다려/;
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/[\t\x20]+/g, ' ');
const ld = l => l.length < 40 && LOAD.test(l);
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
// 줄로 나눠 공백을 줄이고 덮개('저장 중'·'잠시만 기다려') 줄은 뺌
const lines = n => tx(n).split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(l => l && !ld(l));
// 칸 글 (덮개 글이면 null → 그 줄은 아직 안 뜬 줄이라 뺌 · '-' 만 있는 줄도 뺌)
const cl = x => { const s = tx(x).replace(/\s+/g, ' ').trim().slice(0, 200); return ld(s) ? null : s; };
const real = c => c.indexOf(null) < 0 && c.some(s => s && s !== '-');
// 덮개 글이 보이는 동안 0.5초마다 최대 5초 기다림 (늦게 뜨는 통계·iframe)
const busy = () => docs.some(d => d.body && tx(d.body).split('\n').some(l => ld(l.trim())));
for (let i = 0; i < 10 && busy(); i++) await new Promise(r => setTimeout(r, 500));
try { walk(D); } catch (e) {}
const out = [], rows = [], sel = [], ids = [], urls = [];
let rn = 0;
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
  let last = '';
  for (const l of rl) { if (l !== last && !skip.has(l)) { out.push(l); last = l; } }
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
}
for (const u of urls) { try { const q = new URL(u).searchParams.get('blogId'); if (q && ids.indexOf(q) < 0) ids.unshift(q); } catch (e) {} }
const F = {k: K, u: location.href, t: D.title || '', f: urls.slice(1, 8).join('\n'), b: ids.join(','), s: sel.join('\n'),
  x: out.join('\n').slice(0, 40000), r: JSON.stringify(rows).slice(0, 60000)};
let res = null;
try {
  const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)});
  res = await r.json();
} catch (e) { res = null; }
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
} finally { W.__stcap = 0; }
})();
