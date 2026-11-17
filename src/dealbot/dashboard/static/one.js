/* '한 번에' 책갈피 v1 (주인 11/16 — '책갈피 버튼 합치자 · 켜놓고 놔두면 한 번에' · '내가 누를 버튼이 없어야 한다 · 한 번 누르면 순차적으로 모든 게').
   '통계 보내기'(statscap.js)와 '이웃 관리'(seoi.js)의 코드를 그대로 안에 넣은 껍데기 — 딜봇 one.py 가 아래 두 자리('@@STAT@@'·'@@SEOI@@')에
   두 책갈피의 줄인 코드를 통째로 넣는다 (간격·상한·멈춤 규칙·공감·댓글 코드는 한 글자도 다시 쓰지 않음 · 옛 두 책갈피는 그대로 돎).
   누르는 화면을 보고:
     내 통계 (blog.stat.naver.com · admin.blog.naver.com) → 통계 화면을 모두 모아 보냄 → 끝나면 스스로 이웃새글(m.blog.naver.com)로 옮김
     m.blog.naver.com (이웃새글·내 블로그·아무 블로그 화면)   → 이웃 관리 자동을 바로 시작: 서이추 → 들르기(공감·댓글 등록) → 결과 기록 → 할 일 줄 체크
     PC 블로그 화면 (blog.naver.com)                       → 스스로 모바일 이웃새글로 옮김
   통계 화면과 블로그 화면은 출처(주소 앞부분)가 달라 브라우저가 한 화면에서 둘을 못 읽게 막는다 — 화면을 옮기면 책갈피 코드는 사라지므로
   화면마다 한 번씩 누른다 (책갈피는 하나). 누른 뒤에는 사람이 누를 것이 없다:
     알림 창(alert)·묻는 창(confirm)·적는 창(prompt)을 쓰지 않음 — 안에 든 옛 코드가 부르는 셋은 아래 같은 이름 함수가 받아 아래쪽 칸에 한 줄로 적는다
     (confirm 은 늘 '예' = 통계 화면으로 옮기기 하나뿐 · prompt 는 늘 빈 값).
     이웃 관리 자동의 [시작]·[멈춤] 단추는 숨기고 바로 돈다 · 블로그 고르기 창은 띄우지 않음 (로그인 아이디·내 블로그 주소로 못 알아보면 그 글만 남기고 끝).
   멈추기 = 탭을 닫거나 새로 고침. 다시 누르면 대시보드가 기억하는 차례에서 이어서 (오늘 이 브라우저에서 한 수는 localStorage 'sb1' 에 더해 둠).
   네이버가 한도·제한 안내·자동입력 방지·로그인 화면을 보이면 옛 코드 그대로 바로 멈추고(그날 쉼) 끝 요약에 '폰에서 직접'으로 남긴다 — 돌아가지 않는다.
   대시보드에는 옛 두 길만 쓴다 (통계 /dashboard/api/stats_capture · 이웃 /dashboard/api/seoi_act — 열쇠도 그대로 · 쿠키 없음).
   one.py 가 kkumi.minify 로 줄이고 한글 글은 Z('…') 로 바꾼다 — 줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음 · 한글 글 안에 역슬래시 없음). */
(async function () {
const V1 = 1;
const Z = s => s.replace(/([A-I])(..)|J(....)|\.(..)|_/g, (m, a, b, c, d) => a ? String.fromCharCode(44032 + (a.charCodeAt(0) - 65) * 1296 + parseInt(b, 36)) : c || d ? String.fromCharCode(parseInt(c || d, 16)) : ' ');
const W = window, D = document, L = location, FEED = 'https://m.blog.naver.com/FeedList.naver', TAG = '한 번에: ', NL = String.fromCharCode(10);
if (W.__sb1) return;
W.__sb1 = V1;
const sl = t => new Promise(r => setTimeout(r, t));
const cd = D.createElement('div');
cd.id = 'sb1card';
cd.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;z-index:2147483646;padding:10px 12px;border-radius:12px;background:#0b3d2e;color:#fff;font:14px/1.5 sans-serif;white-space:pre-line';
cd.onclick = () => cd.remove();
const show = t => { cd.textContent = t; if (!cd.parentNode) (D.body || D.documentElement).appendChild(cd); };
// 오늘 이 브라우저에서 한 수 (다시 눌러 이어 해도 더해짐 · 날이 바뀌면 처음부터)
const day = new Date().toDateString();
let n = {d: day, add: 0, visit: 0, skip: 0, bad: [], hand: []};
try { const o = JSON.parse(W.localStorage.getItem('sb1') || '{}'); if (o && o.d === day && o.bad && o.hand) n = o; } catch (e) {}
const keep = () => { try { W.localStorage.setItem('sb1', JSON.stringify(n)); } catch (e) {} };
const sum = () => '서이추 ' + n.add + ' · 들르기 ' + n.visit + ' · 건너뜀 ' + n.skip + ' · 안 된 것 ' + n.bad.length;
// 안에 든 옛 코드가 부르는 곳: item = 한 명·한 편이 끝날 때마다 (r = ok·skip·fail·stop·login) · end = 자동이 끝났을 때 (q = 마지막 차례 답 · t = 마지막 한 줄)
const ONE = {say: [], hop: 0, q: null, line: '', ran: 0};
ONE.item = (c, r, t) => {
  const who = c.id + (c.no ? ' 글' : '');
  if (r === 'ok') n[c.no ? 'visit' : 'add']++;
  else if (r === 'skip') n.skip++;
  else if (r === 'stop' || r === 'login') n.hand.push(who + ' — ' + String(t).slice(0, 70));
  else if (r !== 'srv') n.bad.push(who + ' — ' + String(t).slice(0, 70));
  n.bad = n.bad.slice(-8);
  n.hand = n.hand.slice(-4);
  keep();
  show(TAG + sum() + ' — 하는 중 · 화면을 켜 둔 채 이 탭에 두세요');
};
ONE.end = (q, t) => { ONE.q = q; ONE.line = t; ONE.ran = 1; };
// 알림·묻기·적기 창 대신 (같은 이름 — 안쪽 코드의 alert·confirm·prompt 가 여기로 옴 · 창을 띄우지 않음)
const note = t => { ONE.say.push(String(t)); show(TAG + String(t)); };
const alert = note;
const confirm = t => { ONE.hop = 1; note(t); return true; };
const prompt = t => { note(t); return null; };
try {
// 통계 화면: 통계 앱·관리 화면, 그리고 블로그 주소 안의 통계 화면(모바일 '내 통계' — 11/17: 11/16 통계가 서버에 하나도 없어 점검 ·
// 블로그 주소의 통계 화면이 이웃 관리로 가던 길을 통계로 · 경로에 stat·statistics 가 한 마디로 있거나 제목이 '통계 :' 로 시작)
if (/(^|\.)stat\.naver\.com$/.test(L.hostname)||L.hostname==='admin.blog.naver.com'||(/(^|\.)blog\.naver\.com$/.test(L.hostname)&&(/\/(stats?|statistics?)(\/|$|\.naver)/i.test(L.pathname)||/^통계\s*[:|]/.test(D.title)))) {
  show(TAG + '통계를 모아 보내요 — 이 탭을 켜 두세요');
  '@@STAT@@';
  const r = ONE.say[ONE.say.length - 1] || '통계 화면을 읽지 못했어요';
  if (ONE.hop) { show(TAG + '통계 화면으로 옮겨요 — 열리면 이 책갈피를 한 번 더 (처음 한 번만)'); return; }
// 못 보냈으면 '통계 못 보냄:' 으로 · 이웃새글 끝 요약의 '통계:' 줄은 서버가 실제로 받은 수 (q.stats_line)
  const ok=!r.indexOf('통계 보냄');
  show(TAG+(ok?r:'통계 못 보냄: '+r)+NL+'곧 이웃새글 화면으로 옮겨요 — 열리면 이 책갈피를 한 번 더 누르면 서이추·들르기를 끝까지 해요');
  await sl(5000);
  L.href = FEED + '#sb1=' + encodeURIComponent(r.slice(0, 60));
  return;
}
if (!/(^|\.)blog\.naver\.com$/.test(L.hostname)) {
  const nv = /(^|\.)naver\.com$/.test(L.hostname);
  show(TAG + (nv ? '이웃새글 화면으로 옮겨요 — 열리면 이 책갈피를 한 번 더' : '네이버 이웃새글(m.blog.naver.com)이나 내 통계 화면에서 눌러 주세요'));
  await sl(nv ? 1500 : 6000);
  if (nv) L.href = FEED;
  else cd.remove();
  return;
}
if (L.hostname !== 'm.blog.naver.com') {
  show(TAG + '모바일 이웃새글 화면으로 옮겨요 — 열리면 이 책갈피를 한 번 더');
  await sl(1500);
  L.href = FEED;
  return;
}
const st = (/[#&]sb1=([^&]*)/.exec(L.hash) || [])[1];
show(TAG + '서이추·들르기를 차례로 해요 — 화면을 켜 둔 채 이 탭에 두세요');
'@@SEOI@@';
// 끝 요약 (누를 것 없음 · 칸을 누르면 닫힘)
const q = ONE.q, stop = !ONE.ran || ONE.line.indexOf('다 했어요') < 0, o = ['한 번에 끝' + (stop ? ' — 다 하지는 못했어요' : ' ✓') + ' · 오늘 여기서 ' + sum()];
if (q && q.text) o.push(q.text);
if (ONE.ran && stop && ONE.line) o.push(ONE.line);
if (!ONE.ran && ONE.say.length) o.push(ONE.say[ONE.say.length - 1]);
let s1 = '';
try { s1 = st ? decodeURIComponent(st) : ''; } catch (e) {}
// 통계 줄은 서버가 실제로 받은 수 (11/17 — 책갈피가 보냈다고 여겨도 서버에 없던 일) · 이번 통계 화면에서 본 글은 덧붙임
if (q&&q.stats_line) o.push('통계: '+q.stats_line+(s1?' · 이번: '+s1:''));
else if (s1) o.push('통계: ' + s1);
else if (q && q.stats === true) o.push('통계: 오늘 받았어요 ✓');
else if (q && q.stats === false) o.push('통계: 오늘은 아직이에요 — 내 통계 화면에서 이 책갈피를 한 번');
if (n.bad.length) o.push('안 된 것: ' + n.bad.slice(-5).join(' / '));
if (n.hand.length || (ONE.ran && stop)) o.push('폰에서 직접: ' + (n.hand.length ? n.hand.join(' / ') + ' · ' : '') + '남은 줄은 대시보드 할 일에서');
o.push('결과표는 대시보드 할 일 탭 · 이 칸은 누르면 닫혀요');
show(o.join(NL));
} catch (e) {
  show(TAG + '오류로 멈췄어요 — ' + String((e && e.message) || e).slice(0, 120));
} finally { W.__sb1 = 0; }
})();
