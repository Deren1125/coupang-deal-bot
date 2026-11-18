/* '이웃 관리' 책갈피 v2 (주인 11/14 서이추·이웃 들르기를 책갈피 하나로 · 11/15 '켜 두면 알아서' 자동 · 서버는 네이버에 로그인하지 않음).
   주인이 이미 로그인한 자기 브라우저(아이패드 사파리 등)의 네이버 블로그 화면에서 누른다. 화면을 보고:
   [한 단계 — 누를 때마다 하나 · 마지막 단추는 주인이]
     ① 남의 블로그 화면 (글 화면이라도 주소에 #bmmsg= 가 있으면 여기)  → '이웃추가' 단추 한 번
     ② 이웃추가 화면 (BuddyAdd… · 서로이웃 고르는 칸이 보이는 화면)   → '서로이웃' 고르고 멘트 칸 채움 (그룹은 그대로)
     ③ 남의 글 화면                                              → 공감 한 번(이미 눌렀으면 그대로) + 댓글 초안을 댓글 칸에
        (댓글 단추가 본문 없는 댓글 화면으로 옮겨 가면 거기서 한 번 더 → 방금 받은 그 초안만 넣음)
     확인·등록·신청·다음 단추는 누르지 않는다 — press(n) 이 그런 글자·submit 단추를 거절 (누르기 .click() 은 press 한 곳뿐).
   [자동 — 주인 11/15 '한 번 켜 놓으면 이웃 가서 서이추·공감·댓글까지, 대신 시간 간격은 있게' · 보호조치 위험이 올라간다는 설명을 듣고 고름]
     ④ 내 블로그·이웃새글처럼 남의 블로그가 아닌 m.blog.naver.com 화면 → 떠 있는 칸 [시작][멈춤]. 시작하면 이 화면 안의 작은 창(iframe · 같은 m.blog 주소)에
        대시보드가 정해 준 차례(a=queue — 오늘 할 일의 서로이웃 줄·이웃 글 들르기 줄 · 그 블로그 것만)대로 한 명·한 편씩:
          서이추 = 이웃추가 화면 → 서로이웃 + 멘트 → '확인' → '신청 완료' 글 확인
          들르기 = 글을 열어 읽는 시간(20~60초 · 끝까지 천천히 내림) → 공감 → 댓글 초안(대시보드 a=visit) → '등록' → 내 댓글이 생겼는지 확인
        마지막 단추는 press(n, 1) 로만 (자동에서만 · 이웃추가 화면의 a.btn_ok '확인' · 댓글 칸의 .u_cbox_btn_upload '등록' 두 곳).
        다음까지 기다리는 시간은 대시보드가 정한다 (서이추·들르기 25~45초 · 둘을 합쳐 한 시간 40 · 하루 상한) — 책갈피는 그 초만큼 세고 다시 묻는다.
        바로 멈춤: 네이버가 한도·제한·보호조치·잠시 후 다시 같은 글을 보이거나, 로그인 화면이 나오거나, 두 번 잇달아 안 되면 — 대시보드에 a=stop
        (v3 11/18: 한도·제한 안내·자동입력 방지 = w=add|visit → 그 블로그 그 종류 24시간 · 로그인·두 번 실패 = w=all 그날).
        자동입력 방지 칸([class*=captcha] — 보이고 크기가 있는 것)이 뜨면 어디서든 멈춤. 대시보드가 '방금 한 대상'(dup — 다른 탭·다시 시작)이라고 하면 그 대상은 건너뜀
        (같은 글에 댓글 두 번 막기 — 댓글 칸에서도 목록이 뜨기를 2.5초 기다려 내 댓글·같은 글이 있으면 넣지 않음).
        이미 이웃·신청 중·서로이웃을 받지 않는 블로그는 건너뜀 (세지 않음). 탭이 뒤로 가면 느려질 뿐 돌아오면 이어서 · 새로고침하면 책갈피를 다시.
        네이버 알림 창(alert·confirm)은 그 작은 창에서만 글을 받아 적는다 (confirm 은 늘 '아니오') — 한도 안내를 읽으려는 것.
   사람 속도 지키기(주인 11/14 '너무 빠르면 봇으로 볼까 걱정'): 누르기 전에 대시보드(@@URL@@ · 열쇠 @@KEY@@ · 쿠키 없음)에 물어
   간격·하루·한 시간 상한을 넘으면 아무것도 하지 않는다. 대시보드에 못 닿으면 아무것도 하지 않는다
   (하나만 빼고: ①에서 방금(10분 안) 허락받은 그 블로그의 ②는 그 허락으로 이어 함). 글 화면은 연 지 20초(가운데까지 내려 봤으면 8초)가 지나야 초안을 부탁한다.
   속이는 장치(가짜 마우스·키 입력, 브라우저 정보 바꾸기)는 없다.
   공감 (주인 11/15 화면 두 번 — '눌렀는데 표시가 안 바뀌었어요'): 실제 m.blog 글 아래는 .blog_mobile_like_area 안에
   .my_reaction(빈 하트 ♡ = a.u_likeit_button._face · 아이콘 ._longpress) + a.other_reaction(분홍 하트·숫자 = 남들 반응 목록 — 누르지 않음).
   하트를 톡 누르면 네이버(reaction.basic.js _runFaceDirectReaction)는 고르는 칸(._faceLayer · 모바일은 CSS 로 늘 숨김)의 첫 단추 '공감'을
   눌러 준다 — 스크립트로 하트(a._face)를 누르면 칸만 열리고 공감은 안 됨. 그래서 그 묶음 칸 안의 a._button[data-type=like] 를 바로 누른다
   (칸이 없는 화면만 그 단추). 누르기 전: 공감 묶음 어디든(남들 반응 묶음·떠 있는 것까지) on·aria-pressed·aria-selected 가 있으면 절대 안 누름
   (누르면 풀림 · 이 글 묶음만 = data-cid 블로그아이디_글번호) · 네이버가 내 반응을 아직 안 그렸으면(data-sid 묶음에 data-loaded=1 없음) 안 누름. 누른 뒤 표시가 바뀔 때까지 5초쯤 다시 본다.
   어느 블로그로 하는지: 화면의 로그인 아이디(@@BLOGS@@ = [네이버 아이디, 블로그, 이름]) → 주소의 bmb → 이 탭에서 고른 것 → 고르기 창.
   멘트: 주소 #bmmsg= (대시보드 서이추 줄이 붙임) → 대시보드가 오늘 할 일 줄에서 찾아 준 것(msg) → ①에서 localStorage 'sbnb' 에 15분 맡긴 것
   → 없으면 ②에서 물어봄. 그 블로그가 다른 블로그 할 일 줄이면 대시보드가 거절(code row) — 고른 블로그는 잊고 다음에 다시 물음.
   seoi.py 가 kkumi.minify 로 줄이고 한글이 든 글은 Z('…') 로 짧게 바꿔(pack — 책갈피 길이) javascript: 주소로 만든다
   — 줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음 · 글 안에 역슬래시 없음). */
(async function () {
const V = 3;
// 한글 글 풀기 (seoi.py pack 이 '…' → Z('…') 로 바꿈)
const Z = s => s.replace(/([A-I])(..)|J(....)|\.(..)|_/g, (m, a, b, c, d) => a ? String.fromCharCode(44032 + (a.charCodeAt(0) - 65) * 1296 + parseInt(b, 36)) : c || d ? String.fromCharCode(parseInt(c || d, 16)) : ' ');
const U = '@@URL@@', K = '@@KEY@@', BL = JSON.parse('@@BLOGS@@'), W = window, D = document, L = location, SB = '이웃 관리: ', MINE = ' — 마지막 단추는 직접 눌러 주세요';
if (W.__sbnb) return;
if (!/(^|\.)blog\.naver\.com$/.test(L.hostname)) { alert(SB + '네이버 블로그 화면에서 눌러 주세요'); return; }
W.__sbnb = 1;
// au = 자동으로 도는 중 (알림은 칸에 · 화면은 작은 창 fr 안만 봄) · fx = 자동의 블로그 · cur = 지금 사람·글 · res = 그 결과 · dl = 작은 창이 띄우려던 알림 글
// lr·cr = 그 글의 공감·댓글 결과 낱말 (a=done 의 l·c — 대시보드 '오늘 자동 결과' 표 · seoi.py LIKES·CMTS · '-' = 거기까지 안 감)
let ov = null, au = 0, fr = null, fx = '', cur = {}, res = '', said = '', gc = '', dl = [], t0 = 0, lr = '-', cr = '-';
try {
const NO = /확인|등록|신청|보내기|완료|다음|취소|삭제|차단|submit/i;
const FULL = /하루|초과|비정상|보호조치|일시적|한도|제한|잠시후|스팸|차단|정지|자동입력|연속|도배|나중에|운영/;
const HAVE = /상대방|이미(서로)?이웃|이미추가|신청중|진행중|이미신청/, SENT = /신청(이|을)?(완료|하였|했|되었)/;
const fc = t => (t.replace(/\s/g, '').match(RegExp(FULL.source, 'g')) || []).length;
const LY = '#_alertLayer,.lyr_popup', BODY = '.se-main-container,#postViewArea,.post_ct,#viewTypeSelector';
// 네이버가 보인 글: full = 한도·제한 안내 (그날 쉼) · have = 이미 이웃·신청 중·받지 않음 (건너뜀)
const kind = t => { t = t.replace(/\s/g, ''); return !/상대방/.test(t) && FULL.test(t) ? 'full' : HAVE.test(t) ? 'have' : ''; };
// 알림: 한 단계는 알림 창 · 자동은 칸에 (r = 자동의 결과)
const A = (t, r) => { said = t; if (r) res = r; if (!au) alert(SB + t); };
const sl = t => new Promise(r => setTimeout(r, t));
const tx = n => (n && (n.innerText || n.textContent)) || '';
const sq = s => (s || '').replace(/\s+/g, ' ').trim();
// 보는 창들: 이 화면 + 안쪽 창 (자동은 작은 창과 그 안쪽만) — 못 읽는 창(다른 사이트로 넘어감)은 빠짐
const wins = () => {
  const o = [], go = (w, k) => {
    try { if (k && !w.document.body) return; o.push({w: w, d: w.document, l: w.location}); if (k < 3) for (const f of Array.from(w.document.querySelectorAll('iframe,frame'))) go(f.contentWindow, k + 1); } catch (e) {}
  };
  go(au ? fr.contentWindow : W, au);
  return o;
};
const page = () => sq(wins().map(x => tx(x.d.body)).join(' '));
const qa = s => wins().flatMap(x => Array.from(x.d.querySelectorAll(s)));
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
// 누르기는 여기 한 곳뿐 — 확인·등록·신청 같은 마지막 단추와 submit 단추는 누르지 않는다
// (type 을 안 적은 button 도 폼 안에 있으면 submit 이라 거절 — 폼 밖의 그런 단추는 그냥 단추)
// f = 자동이 마지막 단추(이웃추가 화면의 확인 · 댓글 등록)를 누를 때만 1
const press = (n, f) => {
  const t = sq(tx(n) + ' ' + (n.value || '') + ' ' + (n.getAttribute('aria-label') || '')), ty = String(n.getAttribute('type') || (n.form && n.type) || '').toLowerCase();
  if (!(f && au) && (ty === 'submit' || ty === 'image' || NO.test(t))) return false;
  n.click();
  return true;
};
const fire = (n, t) => { try { n.dispatchEvent(new Event(t, {bubbles: true})); } catch (e) {} };
// 칸에 글 넣기: 글 넣기 명령(insertText) → 안 되면 값을 바로 넣고 input·change 를 알림. 돌려줌 = 넣은 글
const put = (n, t) => {
  const d = n.ownerDocument || D, ce = n.getAttribute('contenteditable') === 'true', mx = parseInt(n.getAttribute('maxlength') || '0', 10);
  if (mx > 0) t = t.slice(0, mx);
  const now = () => (ce ? n.textContent : n.value) || '';
  const set = v => {
    if (ce) { n.textContent = v; return; }
    try { Object.getOwnPropertyDescriptor(d.defaultView.HTMLTextAreaElement.prototype, 'value').set.call(n, v); } catch (e) { n.value = v; }
  };
  try { n.focus(); } catch (e) {}
  try { if (ce) { const r = d.createRange(); r.selectNodeContents(n); const s = d.defaultView.getSelection(); s.removeAllRanges(); s.addRange(r); } else n.select(); } catch (e) {}
  let ok = false;
  try { ok = d.execCommand('insertText', false, t); } catch (e) {}
  if (!ok || now() !== t) set(t);
  fire(n, 'input');
  fire(n, 'change');
  return t;
};
// 지금 화면의 블로그 아이디·글 번호 (PC 는 안쪽 창 주소까지)
const where = () => {
  const o = {id: '', no: ''};
  for (const x of wins()) {
    let u = null;
    try { u = new URL(x.l.href); } catch (e) { continue; }
    const m = /^\/([A-Za-z0-9_-]{3,30})(?:\/(\d{6,}))?\/?$/.exec(u.pathname), id = u.searchParams.get('blogId') || (m && m[1]) || '', no = u.searchParams.get('logNo') || (m && m[2]) || '';
    if (id && !o.id) o.id = id.toLowerCase();
    if (id && no && !o.no) { o.id = id.toLowerCase(); o.no = no; }
  }
  return o;
};
// 로그인한 네이버 아이디 (글 화면 전역 userId · 블로그 첫 화면은 화면 데이터)
let off = 0;
const me = () => {
  let v = '';
  off = 0;
  for (const x of wins()) {
    try { v = v || x.w.userNaverId || x.w.userId || ''; if (x.w.bLoginUser === false) off = 1; } catch (e) {}
    try { for (const q of x.w.__REACT_QUERY_STATE__.queries) { const s = q.state.data, r = s && (s.result || s); if (r && r.loggedIn && r.userId) v = v || r.userId; } } catch (e) {}
  }
  return String(v).toLowerCase();
};
const out = () => !me() && !!off;
const nm = b => (BL.find(x => x[1] === b) || [])[2] || b;
const ours = id => BL.some(x => x[0] === id);
const card = t => {
  const o = D.createElement('div');
  o.style.cssText = 'position:fixed;left:8px;right:8px;top:8px;z-index:2147483647;padding:14px;border-radius:12px;background:#111;color:#fff;font:16px/1.5 sans-serif;text-align:center';
  o.textContent = t;
  (D.body || D.documentElement).appendChild(o);
  return o;
};
const btn = (o, t, f) => {
  const b = D.createElement('button');
  b.type = 'button';
  b.textContent = t;
  b.style.cssText = 'display:block;width:100%;margin:8px 0 0;padding:12px;border:0;border-radius:8px;font:inherit;background:#fff;color:#111';
  b.onclick = f;
  o.appendChild(b);
  return b;
};
const choose = () => new Promise(done => {
  const o = card('어느 블로그 계정으로 하고 있나요?'), add = (t, v) => btn(o, t, () => { o.remove(); done(v); });
  for (const x of BL) add(x[2], x[1]);
  add('그만두기', '');
});
// 어느 블로그로 하는지. 로그인 아이디가 우리 블로그면 그것(sure) · 아니면 줄에 적힌 블로그 → 이 탭에서 고른 것 → 고르기
const who = async hint => {
  if (fx) return {b: fx, sure: 1};
  const hit = BL.find(x => x[0] === me());
  if (hit) return {b: hit[1], sure: 1};
  if (hint && BL.some(x => x[1] === hint)) return {b: hint};
  let b = '';
  try { b = W.sessionStorage.getItem('sbnb_b') || ''; } catch (e) {}
  if (!BL.some(x => x[1] === b)) b = await choose();
  try { if (b) W.sessionStorage.setItem('sbnb_b', b); } catch (e) {}
  return {b: b};
};
const lget = () => { try { const v = JSON.parse(W.localStorage.getItem('sbnb') || '{}'); return v && Date.now() - v.at < 9e5 ? v : {}; } catch (e) { return {}; } };
const lset = v => { try { W.localStorage.setItem('sbnb', JSON.stringify(v)); } catch (e) {} };
// 대시보드에 묻기 (쿠키 없이 열쇠로) — 못 닿으면 null
const ask = async F => {
  F.k = K;
  F.v = V;
  if (au) F.au = 1;
  gc = '';
  const c = new AbortController(), t = setTimeout(() => c.abort(), 90000);
  try { const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F), signal: c.signal}), j = await r.json(); gc = (j && !j.ok && j.code) || ''; return j; } catch (e) { return null; } finally { clearTimeout(t); }
};
const DOWN = '대시보드에 닿지 않아 아무것도 하지 않았어요 — 잠시 뒤 다시';
// 거절 알림 (다른 블로그 할 일 줄이면 이 탭에서 고른 블로그를 잊음)
const no = g => { if (g.code === 'row') { try { W.sessionStorage.removeItem('sbnb_b'); } catch (e) {} } A(g.text, 'srv'); };
let H, P, FM;
const look = () => { H = new URLSearchParams(au ? '' : L.hash.slice(1)); P = where(); FM = /BuddyAdd/i.test((au ? fr.contentWindow.location : L).pathname); };
look();
// 서로이웃 고르는 칸: 그 칸의 이름표(label · 바로 옆 글)가 '서로이웃'으로 시작하는 것 → 없으면 이웃추가 화면에서만 아이디·값으로
const lab = r => { try { return sq(tx((r.id && r.ownerDocument.querySelector('label[for=' + JSON.stringify(r.id) + ']')) || r.closest('label') || r.nextElementSibling)); } catch (e) { return ''; } };
const radios = () => {
  const a = qa('input[type=radio]'), t = a.filter(r => /^서로\s*이웃/.test(lab(r)));
  return t.length || !FM ? t : a.filter(r => /both|each|mutual/i.test(r.id + ' ' + r.value));
};
// ---------------------------------------------------------------- ② 이웃추가 화면
const form = async () => {
  const st = au ? {} : lget(), tg = (au && cur.id) || P.id || st.t || '', rs = radios(), ta0 = qa('textarea').filter(vis)[0];
  // 네이버 알림 창(이미 추가한 이웃 · 한도 안내 …)이 떠 있으면 그 글이 먼저 — 그 아래 칸은 건드리지 않음
  const ly = sq(qa(LY).filter(vis).map(tx).join(' '));
  // 고르는 칸이 input 이 아닌 화면: '서로이웃' 글자 단추 하나 (이웃추가 화면에서만)
  const alt = rs.length ? [] : qa('label,a,button,[role=radio]').filter(n => vis(n) && sq(tx(n)).replace(/\s/g, '') === '서로이웃');
  if (ly || (!rs.length && alt.length !== 1 && !ta0)) {
    const k = kind(ly || page());
    if (k === 'full') { await ask({a: 'stop', b: fx || st.b || H.get('bmb') || '', w: 'add'}); A('네이버가 한도·제한 안내를 보여요 — 오늘 서이추는 그만해요', 'stop'); return; }
    if (k === 'have') { A('이미 이웃이거나 신청 중이에요 — 아무것도 하지 않았어요', 'skip'); return; }
    A(ly ? '네이버 알림이 떠 있어요 (' + ly.slice(0, 60) + ') — 아무것도 하지 않았어요' : '서로이웃 고르는 칸을 못 찾았어요 (고르는 칸 ' + qa('input[type=radio]').length + '개 · 글 칸 0개) — 직접 해 주세요');
    return;
  }
  const r0 = rs[0];
  if (r0 && r0.disabled) { A('서로이웃을 받지 않는 블로그예요 — 아무것도 하지 않았어요', 'skip'); return; }
  const w = await who((st.t === tg && st.b) || H.get('bmb'));
  if (!w.b) return;
  let g = await ask({a: 'add', b: w.b, t: tg});
  // 신청 화면에서만 대시보드에 못 닿을 때: ①에서 방금 허락받은 같은 블로그면 그 허락으로 이어 함 (새로 세는 일이 아님)
  if (!g && st.g && st.t === tg && st.b === w.b && Date.now() - st.at < 6e5) g = {ok: 1, text: '대시보드에 닿지 않아 앞 단계 허락으로 이어 했어요'};
  if (!g) { A(DOWN, 'down'); return; }
  if (!g.ok) { no(g); return; }
  let msg = H.get('bmmsg') || g.msg || (st.t === tg && st.m) || '';
  if (!msg && !au) msg = prompt('서로이웃 멘트를 적어 주세요 (비워 두면 멘트 칸은 그대로 둬요)', '') || '';
  const did = [];
  if (!r0 && alt.length === 1) did.push(press(alt[0]) ? '서로이웃을 눌렀어요 (골라졌는지 화면에서 봐 주세요)' : '서로이웃은 직접 골라 주세요');
  else if (!r0) did.push('서로이웃 고르는 칸은 이 화면에 없어요 (앞 화면에서 골랐는지 봐 주세요)');
  else if (r0.checked) did.push('서로이웃 이미 골라져 있음');
  else { press(r0); await sl(700); did.push(r0.checked ? '서로이웃 고름 ✓' : '서로이웃을 눌렀는데 안 골라졌어요 — 직접 골라 주세요'); }
  const ta = qa('textarea').filter(vis)[0];
  if (!msg) did.push('멘트 없음 — 칸은 그대로');
  else if (!ta) did.push('멘트 칸을 못 찾았어요 — 다음 화면에서 책갈피를 한 번 더 (멘트: ' + msg + ')');
  else { const got = put(ta, msg); did.push('멘트 넣음 ✓ ' + got.length + '자' + (got.length < msg.length ? ' (칸이 짧아 ' + msg.length + '자에서 줄임)' : '')); }
  if (au) {
    // 자동: 서로이웃이 골라졌고 멘트 칸이 보일 때만 '확인'(a.btn_ok 하나) → 네이버가 보이는 글을 읽음
    const ok = qa('a.btn_ok').filter(n => vis(n) && sq(tx(n)) === '확인');
    if ((r0 ? !r0.checked : alt.length) || !ta || ok.length !== 1) { A('신청 화면이 예상과 달라 누르지 않았어요 (' + did.join(' · ') + ' · 확인 단추 ' + ok.length + '개)'); return; }
    await sl(1500);
// 11/18 — 누르기 전 화면의 한도·제한 낱말 수 (누른 뒤 화면 본문에 새로 생기면 안내 창이 아니어도 한도 안내로 봄)
    const l0 = fc(page());
    press(ok[0], 1);
    for (let i = 0; i < 24; i++) {
      await sl(500);
      if (!wins().length) { res = 'login'; return; }
      const p = page(), t = sq(qa(LY).filter(vis).map(tx).concat(dl).join(' ')) || (fc(p) > l0 && !SENT.test(p.replace(/\s/g, '')) ? p : ''), k = kind(t);
      // 신청 완료 글은 화면에든 안내 창·알림에든 — 한도·이미 이웃 글이 먼저
      if (!k && SENT.test((p + t).replace(/\s/g, ''))) { A(tg + ' 서로이웃 신청 ✓ (' + g.text + ')', 'ok'); return; }
      if (t) { A(k === 'full' ? '네이버가 한도·제한 안내를 보여요 (' + t.slice(0, 60) + ')' : k === 'have' ? '이미 이웃이거나 신청 중이에요' : '네이버 알림: ' + t.slice(0, 60), k === 'full' ? 'stop' : k === 'have' ? 'skip' : 'fail'); return; }
    }
    A('확인을 눌렀는데 신청 완료 글을 못 봤어요', 'unsure');
    return;
  }
  lset({t: tg, b: w.b, m: msg, at: st.t === tg && st.g ? st.at : Date.now(), g: st.t === tg ? st.g : 0});
  A(nm(w.b) + ' · ' + did.join(' · ') + ' · 그룹은 그대로' + MINE + ' (' + g.text + ')');
};
// ---------------------------------------------------------------- ① 블로그 화면 → 이웃추가
const blogpage = async () => {
  const hb = H.get('bmb'), w = await who(hb);
  if (!w.b) return;
  if (w.sure && hb && hb !== w.b) { A('이 줄은 ' + nm(hb) + ' 할 일인데 지금 ' + nm(w.b) + ' 계정이에요 — 계정을 바꾼 뒤 다시 눌러 주세요'); return; }
  const ok = n => vis(n) && /이웃\s*추가/.test(tx(n));
  let bs = qa('[data-click-area="ebc.add"],a._add_buddy,a.btn_addbuddy,a._buddy_popup_btn,#btn_add_nb,a.btn_add_nb').filter(ok);
  const known = bs.length;
  if (!known) bs = qa('a,button').filter(n => ok(n) && sq(tx(n)).replace(/[+\s]/g, '') === '이웃추가' && !n.closest('[class*=ecommend],template'));
  if (!bs.length) {
    const st = qa('a,button').filter(vis).map(n => sq(tx(n))).filter(t => t.length < 12 && /이웃/.test(t));
    A('이웃추가 단추를 못 찾았어요' + (st.length ? ' — 보이는 단추: ' + st.slice(0, 4).join(' / ') + ' (이미 이웃일 수 있어요)' : '') + ' · 아무것도 하지 않았어요');
    return;
  }
  const b0 = bs[0];
  if (bs.length > 1 && !known) { A('이웃추가 단추가 ' + bs.length + '개라 누르지 않았어요 — 직접 눌러 주세요'); return; }
  const g = await ask({a: 'add', b: w.b, t: P.id});
  if (!g) { A(DOWN); return; }
  if (!g.ok) { no(g); return; }
  lset({t: P.id, b: w.b, m: H.get('bmmsg') || g.msg || '', at: Date.now(), g: 1});
  A(nm(w.b) + ' · 이웃추가를 눌러요 — 신청 화면이 뜨면 책갈피를 한 번 더 눌러 주세요 (' + g.text + ')');
  if (!press(b0)) A('이웃추가 단추를 누르지 못했어요 — 직접 눌러 주세요');
};
// ---------------------------------------------------------------- ③ 이웃 글 → 공감 + 댓글 초안
// 공감 묶음(._reactionModule)의 단추 어디든 눌린 표시(on · aria-pressed · aria-selected)가 있으면 1 · 안 눌린 표시만 있으면 0 · 표시가 없거나 아직 안 그려졌으면 -1
// LK = 내 공감 단추 (a 만 — 남들 반응 묶음의 하트는 span) · 눌린 표시는 모든 묶음에서 봄
const LK = 'a.u_likeit_button._face,a.u_likeit_list_btn._button';
const lmod = n => n.closest('._reactionModule,.u_likeit_list_module,.u_likeit_module') || n;
const lon = n => {
  const m = lmod(n), a = [n].concat(Array.from(m.querySelectorAll('a'))), f = (x, v) => x.getAttribute('aria-pressed') === v || x.getAttribute('aria-selected') === v;
  return a.some(x => f(x, 'true') || /(^|\s)on(\s|$)/.test(x.className)) ? 1 : m.getAttribute('data-sid') && m.getAttribute('data-loaded') !== '1' ? -1 : a.some(x => f(x, 'false') || /(^|\s)off(\s|$)/.test(x.className)) ? 0 : -1;
};
// 이 글 것만 (묶음의 data-cid = 블로그아이디_글번호 · 글 아래 다른 글 묶음이 섞여도 그 표시로 판단하지 않게)
// 글번호로 맞춤 — 주소의 블로그 아이디가 별칭이면(naver_diary → naverofficial) 앞쪽이 달라서 · 글번호가 다르면 남의 것 = 안 봄·안 누름
const mine = n => { const c = lmod(n).getAttribute('data-cid'); return !c || c.endsWith('_' + P.no); };
const liked = () => qa(LK + ',._reactionModule').some(n => mine(n) && lon(n) > 0);
// 자동입력 방지 칸 (공감·댓글·이웃추가 어디든 — 보이고 크기가 있는 것만)
const caps = () => qa('[class*=captcha]').filter(n => vis(n) && n.offsetHeight > 9);
const visit = async () => {
  if (ours(P.id)) { A('우리 블로그 글이에요 — 아무것도 하지 않았어요'); return; }
  if (out()) { A('네이버에 로그인돼 있지 않아요 — 로그인한 뒤 다시', 'login'); return; }
  const tn = qa('.se-title-text,.pcol1 .itemSubjectBoldfont,h3.se_textarea,.tit_h3').filter(vis)[0];
  const bn = qa(BODY).filter(vis)[0];
  const title = sq(tx(tn)).slice(0, 200) || sq(D.title).slice(0, 200), text = sq(tx(bn)).slice(0, 3000);
  // 본문 없이 댓글 칸만 있는 화면 (댓글 단추가 댓글 화면으로 옮겨 간 뒤): 방금(10분 안) 이 글에서 받은 초안을 이어서 넣음 — 새로 세지도 쓰지도 않음
  const BOX = '#naverComment__write_textarea,.u_cbox_write_wrap [contenteditable=true],.u_cbox_write_wrap textarea';
  const cont = (!bn || text.length < 30) && qa(BOX).filter(vis).length > 0;
  if (!cont && (!bn || text.length < 30)) { A('글 본문을 못 읽었어요 (제목 ' + (tn ? '있음' : '없음') + ' · 본문 ' + text.length + '자) — 글이 다 뜬 뒤 다시'); return; }
  // 읽은 시간: 자동은 작은 창에 글이 뜬 뒤 실제로 흐른 시간 (그동안 끝까지 내려 봄)
  const rd = Math.floor((au ? Date.now() - t0 : W.performance && W.performance.now ? W.performance.now() : 99000) / 1000);
  let sc = au;
  try { const f = bn.ownerDocument.defaultView, fe = f !== W && f.frameElement, r = bn.getBoundingClientRect(); if (!au) sc = (W.scrollY > 0 || f.scrollY > 0) && (fe ? fe.getBoundingClientRect().top : 0) + r.top + r.height / 2 <= W.innerHeight ? 1 : 0; } catch (e) {}
  if (!cont && rd < (sc ? 8 : 20)) { A('글을 조금 더 읽고 눌러 주세요 — ' + ((sc ? 8 : 20) - rd) + '초 뒤에 다시 (가운데까지 내려 봤으면 8초, 아니면 20초)'); return; }
  const w = await who(H.get('bmb'));
  if (!w.b) return;
  if (!cont && !au) ov = card('댓글 초안을 쓰는 중이에요 — 30초쯤 걸려요 · 화면은 그대로 두세요');
  const g = await ask({a: 'visit', b: w.b, u: 'https://blog.naver.com/' + P.id + '/' + P.no, t: title, x: cont ? '' : text, rd: rd, sc: sc});
  if (ov && !au) { ov.remove(); ov = null; }
  if (!g) { A(DOWN, 'down'); return; }
  if (!g.ok) { A(cont && g.code === 'empty' ? '이 화면에서는 글 본문을 못 읽었어요 (제목 ' + (tn ? '있음' : '없음') + ' · 본문 ' + text.length + '자) — 글 화면에서 책갈피를 눌러 주세요' : g.text, 'srv'); return; }
  const did = [];
  let bad = 0;
  // 공감: 글 끝 단추 하나 (떠 있는 단추는 다른 게 없을 때만) · 여러 개면 누르지 않음
  // 누르기 전에 글의 공감 단추 모두(가려진 것까지)에서 눌린 표시를 먼저 봄 — 이미 눌렸으면 절대 누르지 않음 (누르면 풀림)
  const all = qa(LK).filter(n => mine(n) && !n.closest('a.other_reaction')), lk = all.filter(vis), fl = n => !!n.closest('[class*=Interact__is_floating],.floating_bottom'), end = lk.filter(n => !fl(n)), ls = end.length ? end : lk;
  if (g.like === false) { did.push('공감은 누르지 않았어요'); lr = 'no'; }
  else if (liked()) { did.push('공감은 이미 눌러져 있어요'); lr = 'had'; }
  else if (cont) did.push('공감은 글 화면에서');
  else if (!ls.length) { did.push(all.length ? '공감 단추가 가려져 있어요 — 댓글 창을 닫고 글 화면에서 다시' : '공감 단추를 못 찾았어요'); bad = 1; lr = 'miss'; }
  else if (ls.length > 1) { did.push('공감 단추가 ' + ls.length + '개라 누르지 않았어요'); bad = 1; lr = 'miss'; }
  else if (lon(ls[0]) < 0) { did.push('공감이 눌렸는지 알 수 없어 누르지 않았어요 — 직접 눌러 주세요'); bad = 1; lr = 'miss'; }
  else {
    // 그 묶음 칸 안의 '공감'을 바로 누름 (하트를 톡 누를 때 네이버가 누르는 그 단추 · 칸이 숨어 있어도) · 칸이 없으면 그 단추가 곧 공감 · 한 번만
    press(lmod(ls[0]).querySelector('._faceLayer a._button[data-type=like]') || ls[0]);
    // 표시가 늦게 바뀌는 화면이 있어 5초쯤 다시 봄
    for (let i = 0; i < 12 && !liked(); i++) await sl(400);
    if (liked()) { did.push('공감 ✓'); lr = 'ok'; }
    else { did.push('공감을 눌렀는데 표시가 안 바뀌었어요 — 화면을 봐 주세요'); bad = 1; lr = 'fail'; }
  }
  // 댓글: 칸이 없으면 댓글 단추로 열고 기다림 · 칸에 글이 있으면 건드리지 않음 · 등록은 주인이 (자동만 등록까지)
  const cm = g.comment || '';
  if (!cm) { did.push(g.note || '댓글 초안은 없어요'); cr = 'none'; }
  else {
    let bx = qa(BOX).filter(vis)[0];
    if (!bx) {
      const cb = qa('[class*=Interact__comment_btn],[class*=comment_btn__],a.btn_comment').filter(vis), ce = cb.filter(n => !fl(n)), c0 = (ce.length ? ce : cb).pop();
      if (c0 && press(c0)) for (let i = 0; i < 15 && !bx; i++) { await sl(400); bx = qa(BOX).filter(vis)[0]; }
    }
    // 자동: 댓글 목록이 뜰 시간을 준 뒤 내 댓글·같은 글이 이미 있는지 봄 (같은 글에 두 번 달지 않게)
    const has = () => qa('.u_cbox_comment').some(n => sq(tx(n)).includes(sq(cm).slice(0, 12)));
    if (au) await sl(2500);
    if (qa('.u_cbox_type_logged_out').filter(vis).length) { did.push('댓글 칸이 로그인을 바라요'); bad = 1; cr = 'login'; }
    else if (qa('li.u_cbox_comment[data-info*="mine:true"]').length || (au && has())) { did.push('이미 내 댓글이 있는 글이에요 — 초안은 넣지 않았어요'); cr = 'had'; }
    else if (!bx) { did.push('댓글 칸을 못 찾았어요 — 초안을 복사해 쓰세요'); bad = 1; cr = 'nobox'; if (!au) prompt('댓글 초안 (복사해서 쓰세요)', cm); }
    else if (sq(bx.getAttribute('contenteditable') === 'true' ? bx.textContent : bx.value)) { did.push('댓글 칸에 쓰던 글이 있어 그대로 뒀어요'); cr = 'kept'; if (!au) prompt('댓글 초안 (복사해서 쓰세요)', cm); }
    else if (!au) { put(bx, cm); did.push("댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접"); }
    else {
      // 자동: 등록(.u_cbox_btn_upload 하나)까지 누르고, 댓글 목록에 그 글이 실제로 생겼는지 봄
      put(bx, cm);
      await sl(1500);
      const up = qa('.u_cbox_btn_upload').filter(vis);
      if (up.length !== 1) { did.push('등록 단추가 ' + up.length + '개라 누르지 않았어요'); bad = 1; cr = 'nobtn'; }
      else {
        press(up[0], 1);
        for (let i = 0; i < 16 && !has() && !dl.length; i++) await sl(500);
        if (has()) { did.push('댓글 등록 ✓'); cr = 'ok'; }
        else { did.push('등록을 눌렀는데 내 댓글이 안 보여요'); bad = 1; cr = 'fail'; }
      }
    }
  }
  // 자동: 네이버가 띄우려던 알림·댓글 칸 안내·공감 자동입력 방지 창에 한도·제한 글이 있으면 멈춤
  const cap = caps(), nt = au ? sq(dl.concat(cap.concat(qa('[class*=_likePopupModule]').filter(vis)).map(tx)).join(' ')) : '';
  if (nt) did.push('네이버 알림: ' + nt.slice(0, 60));
  A(nm(w.b) + ' · ' + did.join(' · ') + ' (' + g.text + ')', !wins().length ? 'login' : nt && (cap.length || kind(nt) === 'full') ? 'stop' : bad || nt ? 'fail' : 'ok');
};
// ---------------------------------------------------------------- ④ 자동 (주인 11/15 — 켜 두면 서이추·들르기를 차례로 · 간격은 대시보드가)
const auto = async () => {
  if (L.hostname !== 'm.blog.naver.com') { A('서이추·들르기 자동은 m.blog.naver.com 화면(이웃새글이나 내 블로그)에서 눌러 주세요 — 남의 블로그 화면에서는 한 단계씩이에요'); return; }
  const w = await who((BL.find(x => x[0] === P.id) || [])[1]);
  if (!w.b) return;
  let q = await ask({a: 'queue', b: w.b});
  if (!q) { A(DOWN); return; }
  if (!q.ok) { A(q.text); return; }
  fx = w.b;
  let go = 0, busy = 0, bad = 0, n = 0, end = null;
  const pn = ov = card(q.text), ln = D.createElement('div');
  ln.style.cssText = 'margin-top:6px;font-size:14px;color:#ccc';
  pn.appendChild(ln);
  fr = D.createElement('iframe');
  fr.style.cssText = 'display:none;width:100%;height:45vh;margin-top:8px;border:0;border-radius:8px;background:#fff';
  const say = t => { ln.textContent = t; };
  const rest = async (s, t) => { for (let i = Math.ceil(s); i > 0 && go; i--) { say(t + ' — ' + i + '초 뒤 · 화면을 켜 둔 채 이 탭에 두세요'); await sl(1000); } };
  // 작은 창에 그 화면을 열고 다 뜰 때까지 기다림 · 그 창이 띄우려는 알림 글은 받아 적음 (0 = 못 읽는 화면 — 로그인으로 넘어감)
  const load = async u => {
    let ok = 0;
    fr.onload = () => { ok = 1; };
    fr.src = u;
    for (let i = 0; i < 40 && !ok; i++) await sl(500);
    await sl(1500);
    t0 = Date.now();
    for (const x of wins()) { try { x.w.alert = t => { dl.push(String(t)); }; x.w.confirm = t => { dl.push(String(t)); return false; }; } catch (e) {} }
    return wins().length;
  };
  // 글 읽기: 본문 길이에 따라 20~60초 · 그동안 끝까지 천천히 내림
  const read = async () => {
    for (let i = 0; i < 20 && !qa(BODY).filter(vis).length; i++) await sl(500);
    const f = fr.contentWindow, s = Math.min(60, Math.max(20, Math.round(sq(tx(qa(BODY)[0])).length / 50)));
    for (let i = 1; i <= s && go; i++) { say('글을 읽는 중 — ' + (s - i) + '초'); try { f.scrollTo(0, (f.document.documentElement.scrollHeight - f.innerHeight) * i / s); } catch (e) {} await sl(1000); }
  };
  const run = async () => {
    go = au = 1;
    if (busy) return;
    busy = 1;
    b1.textContent = '이어하기';
    fr.style.display = 'block';
    try {
      while (go) {
        q = await ask({a: 'queue', b: fx});
        if (!q) { say(DOWN); break; }
        pn.firstChild.textContent = q.text;
        cur = q.next;
        if (!q.ok || !cur) { say(q.ok ? '오늘 할 것을 다 했어요 ✓' : '멈췄어요'); break; }
        await rest(q.wait, '다음: ' + cur.id);
        if (!go) break;
        res = 'fail';
        lr = cr = '-';
        said = '';
        dl = [];
        say(cur.id + (cur.no ? ' 글을 여는 중' : ' 이웃추가 화면을 여는 중'));
        if (!(await load(cur.no ? '/' + cur.id + '/' + cur.no : '/BuddyAddForm.naver?blogId=' + cur.id))) res = 'login';
        else if (cur.no) { await read(); if (!go) break; look(); await visit(); }
        else { look(); for (let i = 0; i < 20 && !(radios().length || qa('textarea,' + LY).filter(vis).length); i++) await sl(500); await form(); }
        if (res === 'down') { say(DOWN); break; }
        if (res !== 'stop' && caps().length) { res = 'stop'; said = (said ? said + ' · ' : '') + '자동입력 방지 칸이 떴어요'; }
        if (res === 'srv' && /gap|hour|dup/.test(gc)) continue;
        if (res === 'login' && !said) said = '로그인 화면이 나왔거나 화면을 읽지 못했어요 — 네이버에 로그인한 뒤 책갈피를 다시 눌러 주세요';
        if (res !== 'srv') await ask({a: 'done', b: fx, w: cur.no ? 'visit' : 'add', t: cur.id, n: cur.no || '', r: res, l: lr, c: cr});
        say(said);
        bad = /ok|skip/.test(res) ? 0 : bad + 1;
        // 바로 멈춤: 한도·제한 안내 · 로그인 화면 · 두 번 잇달아 안 됨 → 대시보드에 알려 그날 쉼 (첫 화면부터 로그인이면 쉼 없이 멈춤만)
        if (res === 'srv' || /stop|login/.test(res) || bad > 1) {
// 11/18 — 한도·제한 안내(stop)는 그 종류만 24시간 (대시보드가 쉬는 글을 돌려줌) · 로그인·두 번 실패는 그날 모두
          if (res !== 'srv' && (res !== 'login' || n)) { const g = await ask({a: 'stop', b: fx, w: res === 'stop' ? cur.no ? 'visit' : 'add' : 'all'}); said += ' · ' + (g ? g.text : ''); }
          say('멈췄어요 — ' + said);
          b1.remove();
          break;
        }
        n++;
      }
    } catch (e) { say('오류로 멈췄어요 — ' + String((e && e.message) || e).slice(0, 120)); }
    // 멈춤을 눌러 끝났으면 (하던 한 명·한 편은 마치고) 이어하기를 기다림
    if (!go) say((said ? said + ' · ' : '') + '멈췄어요 — 이어하기를 누르면 이어서 해요');
    go = busy = 0;
  };
  const b1 = btn(pn, '시작', run);
  btn(pn, '멈춤', () => { if (go) { go = 0; say('멈추는 중이에요 — 하던 것만 마치고 멈춰요'); } else end(); });
  pn.appendChild(fr);
  say('시작을 누르면 차례로 해요 · 멈춘 뒤 멈춤을 한 번 더 누르면 닫혀요');
  await new Promise(r => { end = r; });
};
if (FM || radios().length) await form();
else if (!P.id || ours(P.id)) await auto();
else if (P.no && !H.get('bmmsg')) await visit();
else await blogpage();
} catch (e) {
alert(SB + '오류로 멈췄어요 — ' + String((e && e.message) || e).slice(0, 120));
} finally {
if (ov) ov.remove();
W.__sbnb = 0;
}
})();
