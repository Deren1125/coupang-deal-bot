/* 독자 댓글 모으기 (주인 11/27 '댓글 모아 글 방향 보완' — 서버는 네이버에 접속하지 않음 · 자동 답글·공감 없음).
   '한 번에'(one.js)가 모바일 블로그 화면(m.blog.naver.com)에서 이웃 관리 자리('@@SEOI@@') 바로 앞 '@@CMT@@' 자리에 이 코드를 넣는다.
   하루 한 번 (이 브라우저 localStorage 'sbc1' = 보낸 날 · 세 블로그 다 보냈을 때만 적음):
     ① 우리 세 블로그(@@BLOGS@@)마다 공개 글 목록 /api/blogs/<id>/post-list (같은 출처 · 서버 comments.py 가 쓰던 주소) 최근 30편 중 댓글 있는 글 15편까지
     ② 글마다 공개 댓글창(apis.naver.com cbox — 블로그 화면이 스스로 부르는 JSONP 주소 · script 한 개) 첫 쪽 → 지난 7일 댓글 (블로그마다 100개까지)
     ③ 블로그마다 한 번 POST /dashboard/api/comments_capture (열쇠는 통계 것 · 쿠키 없음)
   ⓪ (11/27 v6) 같은 POST 에 블로그 AI 브리핑 인용 수 qn(정수)·qt(읽은 글 그대로)·qg(못 찾으면 '인용'이 든 칸 진단) —
      블로그 정보 JSON /api/blogs/<id> 의 cit·quot·brief 칸 → 없으면 그 블로그 모바일 홈(/<id>, 같은 출처)을 숨은 칸에 열어 프로필 글
   보내는 칸: 댓글 번호·글 번호·글 제목·시각·글·답글 단계·주인 답글 여부만 — 댓글 쓴 사람의 아이디·별명·프로필은 보내지 않음.
   못 읽으면 단계·답 꼴(칸 이름)만 짧게 g 로 (화면 글 없음). 다른 블로그·다른 주소는 열지 않음 · 요청마다 0.6~1.5초 쉼.
   줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음 · 한글 글 안에 역슬래시 없음). */
(async function () {
const U = '@@CURL@@', K = '@@SKEY@@', BL = @@BLOGS@@, W = window, D = document;
if (W.__sbc1) return;
W.__sbc1 = 1;
let ov = null;
try {
const td = new Date().toDateString();
let ls = '';
try { ls = W.localStorage.getItem('sbc1') || ''; } catch (e) {}
if (ls === td) { W.__sbcl = '오늘 보냈어요'; return; }
const sl = t => new Promise(r => setTimeout(r, t));
const nap = () => sl(600 + Math.random() * 900);
const say = t => { try { if (!ov) { ov = D.createElement('div'); ov.style.cssText = 'position:fixed;top:0;z-index:2147483647;background:#222;color:#fff;padding:8px;font:15px sans-serif'; D.body.appendChild(ov); } ov.textContent = t; } catch (e) {} };
const since = Date.now() - 7 * 864e5;
const ks = o => o && typeof o === 'object' ? Object.keys(o).slice(0, 10).join(',') : String(typeof o);
const gj = async u => { let s = -1; try { const r = await fetch(u, {credentials: 'include'}); s = r.status; return {s: s, j: JSON.parse(await r.text())}; } catch (e) { return {s: s, j: null}; } };
// 공개 댓글창 JSONP (화면이 스스로 부르는 꼴) — 다 읽었는데 부르지 않았거나 12초 안에 답이 없으면 null
const jp = u => new Promise(res => { const cb = '__sbcj' + Math.floor(Math.random() * 1e9), s = D.createElement('script'); let dn = 0; const end = v => { if (dn) return; dn = 1; try { delete W[cb]; } catch (e) {} s.remove(); res(v); }; W[cb] = end; s.onload = s.onerror = () => end(null); setTimeout(() => end(null), 12000); s.src = u + '&_callback=' + cb; (D.head || D.documentElement).appendChild(s); });
const send = async F => { try { const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)}); return await r.json(); } catch (e) { return null; } };
// AI 브리핑 인용 수 (11/27 v6 — 블로그 프로필 '7.2천 인용' · '1.2만 인용' · '인용 345'): 글 → {n, t} · 못 찾으면 null
const qre = /(\d[\d,.]*)\s?([천만])?\s?인용|인용\s?(\d[\d,.]*)\s?([천만])?/;
const qnum = s => { const m = qre.exec(String(s || '').replace(/\s+/g, ' ')); if (!m) return null; const v = parseFloat((m[1] || m[3]).replace(/,/g, '')), u = m[2] || m[4]; return v >= 0 ? {n: Math.round(v * (u === '만' ? 1e4 : u ? 1e3 : 1)), t: m[0].slice(0, 40)} : null; };
const qown = d => [...d.querySelectorAll('body *')].filter(e => [...e.childNodes].some(x => x.nodeType === 3 && x.nodeValue.indexOf('인용') >= 0)).slice(0, 4).map(e => e.tagName.toLowerCase() + '.' + String(e.className || '').slice(0, 30) + ':' + String(e.textContent || '').replace(/\s+/g, ' ').slice(0, 40) + (e.parentElement ? '<' + String(e.parentElement.textContent || '').replace(/\s+/g, ' ').slice(0, 50) : ''));
// ① 블로그 정보 JSON (모바일 블로그 화면이 부르는 같은 출처 주소) 안 인용 칸 · ② 없으면 그 블로그 모바일 홈을 숨은 칸에 열어 프로필 글 (같은 출처 · 15초까지)
const cite = async nid => {
  const g = [], bi = await gj('/api/blogs/' + nid), walk = (o, p, d) => { if (!o || typeof o !== 'object' || d > 4) return null; for (const k of Object.keys(o).slice(0, 80)) { const v = o[k]; if (/cit|quot|brief/i.test(k) && (typeof v === 'number' || typeof v === 'string') && String(v).length < 40) { const r = typeof v === 'number' ? {n: Math.round(v), t: p + k + ':' + v} : qnum(v) || (/^\d[\d,]*$/.test(v) ? {n: +v.replace(/,/g, ''), t: p + k + ':' + v} : null); if (r) return r; } const r = walk(v, p + k + '.', d + 1); if (r) return r; } return null; };
  const j = walk(bi.j, '', 0);
  if (j) return {n: j.n, t: j.t, g: ''};
  g.push('info ' + bi.s + ' ' + ks(bi.j && (bi.j.result || bi.j)));
  const f = D.createElement('iframe');
  f.style.cssText = 'position:fixed;left:-3000px;top:0;width:390px;height:900px;opacity:0;border:0';
  f.src = '/' + nid;
  D.body.appendChild(f);
  let r = null, fd = null;
  for (let i = 0; i < 30 && !r; i++) { await sl(500); try { fd = f.contentDocument; r = fd && fd.body && qnum(String(fd.body.innerText || '').slice(0, 4000)); } catch (e) { fd = null; } }
  try { if (!r) g.push(fd && fd.body ? 'home ' + (qown(fd).join(' | ') || 'no ' + String(fd.body.innerText || '').replace(/\s+/g, ' ').length) : 'home noframe'); } catch (e) { g.push('home err'); }
  f.remove();
  return {n: r ? r.n : '', t: r ? r.t : '', g: g.join(' / ').slice(0, 400)};
};
const out = [];
let all = 1;
for (const [nid, , name] of BL) {
  say('댓글: ' + name + ' 읽는 중…');
  const dg = [], cs = [];
  let q = {n: '', t: '', g: 'skip'};
  try { q = await cite(nid); } catch (e) { q.g = 'cite ' + String((e && e.message) || e).slice(0, 60); }
  const pl = await gj('/api/blogs/' + nid + '/post-list?categoryNo=0&itemCount=30&page=1');
  const its = pl.j && pl.j.result && pl.j.result.items;
  if (!Array.isArray(its)) dg.push('list ' + pl.s + ' ' + ks(pl.j && (pl.j.result || pl.j)));
  for (const p of Array.isArray(its) ? its.filter(p => p && p.commentCnt > 0 && p.logNo && p.blogNo).slice(0, 15) : []) {
    if (cs.length >= 100) break;
    await nap();
    const r = await jp('https://apis.naver.com/commentBox/cbox/web_naver_list_jsonp.json?ticket=blog&templateId=default&pool=blogid&lang=ko&country=KR&objectId=' + p.blogNo + '_201_' + p.logNo + '&groupId=' + p.blogNo + '&pageSize=50&indexSize=10&listType=OBJECT&pageType=default&page=1&initialize=true&useAltSort=true&replyPageSize=10');
    const cl = r && r.result && r.result.commentList;
    if (!Array.isArray(cl)) { dg.push('cbox ' + ks(r && (r.result || r))); continue; }
    const own = c => !!(c.mine || c.manager || String(c.profileUserId || '').toLowerCase() === nid);
    const ans = new Set(cl.filter(c => c.replyLevel > 1 && own(c)).map(c => String(c.parentCommentNo)));
    for (const c of cl) {
      const at = Date.parse(String(c.regTime || '').replace(/([+-]\d\d)(\d\d)$/, '$1:$2'));
      if (own(c) || c.deleted || c.secret || c.blind || !c.contents || !(at >= since) || cs.length >= 100) continue;
      cs.push({no: String(c.commentNo), p: String(p.logNo), t: String(p.titleWithInspectMessage || p.title || '').slice(0, 100), at: String(c.regTime), x: String(c.contents).slice(0, 600), lv: c.replyLevel > 1 ? 2 : 1, r: ans.has(String(c.commentNo)) ? 1 : 0});
    }
  }
  const r = await send({k: K, u: location.href.slice(0, 200), b: nid, c: JSON.stringify(cs), g: dg.slice(0, 6).join(' / ').slice(0, 600), qn: String(q.n), qt: q.t, qg: q.g});
  if (!(r && r.ok)) all = 0;
  out.push(name + ' ' + (r && r.ok ? cs.length + '개' + (dg.length ? '(일부 못 읽음)' : '') + (q.t ? ' · 인용 ' + q.n : ' · 인용 못 찾음') : '못 보냄'));
}
if (all) { try { W.localStorage.setItem('sbc1', td); } catch (e) {} }
W.__sbcl = out.join(' · ');
} catch (e) {
  W.__sbcl = '못 보냄 (' + String((e && e.message) || e).slice(0, 60) + ')';
} finally { W.__sbc1 = 0; if (ov) ov.remove(); }
})();
