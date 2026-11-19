/* '통계 보내기' 세 화면 (11/18 — 홈판 분석 세션: 11/16~ 받은 통계가 시간대·기기·이웃뿐이고 모두 '블로그 모름' →
   조회수 순위(일간)·유입분석(일간)·검색 유입을 어느 블로그 것인지 붙여서). '한 번에'(one.js)가 통계 자리('@@STAT@@')에 이 코드를 넣는다
   (옛 '통계 보내기' 책갈피 statscap.js 는 그대로 — 따로 쓸 때).
   blog.stat.naver.com 에서만 (주인 로그인 그대로 · 같은 출처 · 화면이 스스로 부르는 통계 JSON 주소만 · 쿠키는 대시보드로 안 보냄):
     ① 어느 블로그: 화면이 부르는 /api/blog/user-info 의 blogId (없으면 주소의 blogId=) → 우리 세 블로그 표(@@BLOGS@@ = seoi.BLOGS — statscap.BLOG_IDS).
        못 알아보면 하나도 보내지 않고 마지막 줄에 그렇게 적음 ('블로그 모름'으로 보내지 않음).
     ② 조회수 순위·유입분석: 화면이 부르는 주소(/api/blog/rank/cvContentPc · /api/blog/user/referer/total — 11/12·11/13 받은 기록) 를
        일간(timeDimension=DATE)으로 어제·오늘 하나씩 받아 날짜마다 하나씩 보냄 (n = 화면 이름 · d = 날짜).
     ③ 검색 유입: 주소를 모르므로 유입분석 화면을 숨은 창에 열고 '검색 유입' 탭(없으면 '통합검색' 유입 경로 줄)을 한 번 눌러
        화면이 새로 부르는 JSON 주소를 배워 어제·오늘로 받음. 탭이 없거나 새 주소가 없으면 '못 읽음'으로 적음 (돌아가는 길 없음).
   요청마다 0.8~2초 쉼 · 한 번에 10번 남짓. 다른 블로그·다른 주소는 열지 않음. 서버는 JSON 을 표 줄로 바꿔 둔다 (statscap.save).
   줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음 · 한글 글 안에 역슬래시 없음). */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', BL = @@BLOGS@@, W = window, D = document, L = location, O = L.origin;
if (W.__stday) return;
W.__stday = 1;
let fr = null, ov = null;
try {
const V = ['조회수 순위', '유입분석', '검색 유입'], g = {}, m = {};
const sl = t => new Promise(r => setTimeout(r, t));
const nap = () => sl(800 + Math.random() * 1200);
const p2 = n => String(n).padStart(2, '0');
const ymd = t => { const d = new Date(t); return d.getFullYear() + '-' + p2(d.getMonth() + 1) + '-' + p2(d.getDate()); };
const DS = [ymd(Date.now() - 864e5), ymd(Date.now())];
const md = s => Number(s.slice(5, 7)) + '/' + Number(s.slice(8, 10));
const say = t => { try { if (!ov) { ov = D.createElement('div'); ov.style.cssText = 'position:fixed;top:0;z-index:2147483647;background:#222;color:#fff;padding:8px;font:15px sans-serif'; D.body.appendChild(ov); } ov.textContent = t; } catch (e) {} };
const add = (o, v, s) => { (o[v] = o[v] || []).push(s); };
const line = o => V.filter(v => o[v]).map(v => v + ' ' + o[v].join('·')).join(', ');
// 같은 출처 JSON (주인 로그인 그대로) → 글 · JSON 이 아니면 ''
const gj = async u => { try { const r = await fetch(u, {credentials: 'include'}); const t = await r.text(); JSON.parse(t); return r.ok ? t : ''; } catch (e) { return ''; } };
const good = t => { try { const o = JSON.parse(t); return o.statusCode === 200 && !!o.result; } catch (e) { return false; } };
const send = async F => { try { const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F)}); return await r.json(); } catch (e) { return null; } };
if (!/(^|\.)stat\.naver\.com$/.test(L.hostname)) {
  if (confirm('통계 화면(blog.stat.naver.com)으로 옮겨요')) L.href = 'https://blog.stat.naver.com/m/blog/user/referer/total';
  return;
}
// ① 어느 블로그
say('통계: 어느 블로그인지 보는 중…');
let id = '';
try { id = JSON.parse(await gj(O + '/api/blog/user-info')).blogId || ''; } catch (e) {}
id = String(id || new URLSearchParams(L.search).get('blogId') || '').toLowerCase();
const bl = BL.find(x => x[0] === id);
if (!bl) { alert('어느 블로그인지 못 알아봐서 안 보냈어요' + (id ? ' (' + id + ')' : '') + ' — ' + BL.map(x => x[2]).join('·') + ' 계정으로 로그인한 통계 화면에서 눌러 주세요'); return; }
const put = async (v, d, u, j) => {
  const r = await send({k: K, u: u, t: D.title || '', n: v, b: id, d: d, x: v + ' ' + d, j: j.slice(0, 120000)});
  add(r && r.ok ? g : m, v, md(d));
};
// ② 조회수 순위·유입분석 (일간 · 어제·오늘)
for (const [v, p] of [[V[0], '/api/blog/rank/cvContentPc'], [V[1], '/api/blog/user/referer/total']]) {
  for (const d of DS) {
    say(bl[2] + ' ' + v + ' ' + md(d) + '…');
    const u = O + p + '?timeDimension=DATE&startDate=' + d + '&exclude=', t = await gj(u);
    if (good(t)) await put(v, d, u, u + '\n' + t);
    else add(m, v, md(d));
    await nap();
  }
}
// ③ 검색 유입 (유입분석 화면의 탭을 한 번 눌러 화면이 부르는 주소를 배움)
say(bl[2] + ' ' + V[2] + '…');
const pe = () => { try { return fr.contentWindow.performance.getEntriesByType('resource').map(e => e.name).filter(n => n.indexOf(O + '/api/') === 0 && n.indexOf('user-info') < 0); } catch (e) { return []; } };
fr = D.createElement('iframe');
fr.style.cssText = 'position:fixed;left:-5000px;top:0;width:1000px;height:900px;border:0';
fr.sandbox = 'allow-scripts allow-same-origin';
D.body.appendChild(fr);
fr.src = O + (/^\/m\//.test(L.pathname) ? '/m' : '') + '/blog/user/referer/total';
let a = [], hit = null, nu = [];
for (let i = 0; i < 40 && !hit; i++) {
  await sl(500);
  a = pe();
  const T = a.some(n => /referer/.test(n)) && fr.contentDocument, es = T ? [...T.querySelectorAll('a,button,[role=tab],li,td,span,div')] : [];
  const tc = e => (e.textContent || '').replace(/\s+/g, '');
// 누를 것: 그 글이 든 가장 안쪽 칸 (안에 링크·단추가 있으면 그것을)
  const pk = (rx, z) => es.find(e => rx.test(tc(e)) && tc(e).length < z && !e.querySelector('a,button,[role=tab]'));
  hit = pk(/^검색유입/, 12) || pk(/^네이버통합검색/, 30) || null;
}
if (hit) {
  hit.click();
  for (let i = 0; i < 16 && !nu.length; i++) { await sl(500); nu = pe().filter(n => a.indexOf(n) < 0 && /referer|search|keyword/i.test(n)).slice(0, 2); }
}
fr.remove();
fr = null;
if (!nu.length) add(m, V[2], hit ? '새 주소 없음' : '탭 없음');
for (const d of nu.length ? DS : []) {
  const j = [];
  for (const n of nu) {
    const x = new URL(n);
    x.searchParams.delete('_');
    x.searchParams.set('timeDimension', 'DATE');
    x.searchParams.set('startDate', d);
    const t = await gj(x.href);
    if (good(t)) j.push(x.href + '\n' + t);
    await nap();
  }
  if (j.length) await put(V[2], d, j[0].split('\n')[0], j.join('\n\n'));
  else add(m, V[2], md(d));
}
const n = Object.values(g).reduce((s, x) => s + x.length, 0);
alert((n ? '통계 보냄 ✓ ' + bl[2] + ' ' + n + '개 — ' + line(g) : '통계를 못 읽었어요 (' + bl[2] + ')') + (line(m) ? ' · 못 읽음: ' + line(m) : ''));
} catch (e) {
  alert('통계 못 보냄 (' + ((e && e.message) || e) + ')');
} finally { W.__stday = 0; if (fr) fr.remove(); if (ov) ov.remove(); }
})();
