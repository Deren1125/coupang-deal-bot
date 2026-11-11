/* '통계 보내기' 책갈피 (주인 11/11 — 서버는 네이버 계정에 접속하지 않음). 주인이 아이패드 사파리에서 이미 로그인한 네이버 '내 통계'
   화면(일간 현황·조회수 순위·유입 분석·검색어·방문 분석·평균 사용 시간 …)을 연 채 누르면, 보이는 글(열 수 있는 iframe 포함 · 머리·메뉴 줄 뺌)과
   표(줄마다 칸)와 주소·제목·고른 탭을 대시보드 /dashboard/api/stats_capture 로 보낸다. 쿠키는 보내지 않고 책갈피에 든 열쇠(@@KEY@@)로만.
   바로 보내지 못하면(화면이 막음) 이 창에서 폼으로 보낼지 묻는다. 딜봇 statscap.py 가 주석 줄·들여쓰기를 빼고 javascript: 주소로 만든다 —
   줄마다 문장이 끝나게 쓴다 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). */
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
const tx = n => ((n && (n.innerText || n.textContent)) || '').replace(/[\t ]+/g, ' ');
const out = [], rows = [], sel = [], ids = [], urls = [];
let rn = 0;
for (const d of docs) {
  const b = d.body;
  if (!b) continue;
  try { urls.push(d.location.href); } catch (e) {}
  const root = d.querySelector(ROOT) || b, skip = new Set();
  for (const n of b.querySelectorAll(NAV)) { if (!n.contains(root)) tx(n).split('\n').forEach(l => skip.add(l.trim())); }
  let last = '';
  for (let l of tx(root).split('\n')) {
    l = l.replace(/\s+/g, ' ').trim();
    if (l && l !== last && !skip.has(l)) { out.push(l); last = l; }
  }
  for (const n of d.querySelectorAll(SEL)) { const s = tx(n).replace(/\s+/g, ' ').trim(); if (s && s.length < 40 && sel.length < 12 && sel.indexOf(s) < 0) sel.push(s); }
  for (const t of d.querySelectorAll('table')) {
    if (rn >= 300) break;
    const tb = [];
    for (const r of t.rows) {
      if (rn >= 300) break;
      const c = [...r.cells].slice(0, 12).map(x => tx(x).replace(/\s+/g, ' ').trim().slice(0, 200));
      if (c.some(Boolean)) { tb.push(c); rn++; }
    }
    if (tb.length) rows.push(tb);
  }
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
