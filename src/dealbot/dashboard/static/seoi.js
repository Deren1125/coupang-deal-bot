/* '이웃 관리' 책갈피 (주인 11/14 18:5x~19:0x — 서이추·이웃 들르기(공감·댓글)를 책갈피 하나로 · 서버는 네이버에 로그인하지 않음).
   주인이 이미 로그인한 자기 브라우저(아이패드 사파리 등)의 네이버 블로그 화면에서 누르면 화면을 보고 한 단계만 한다:
     ① 블로그 화면 (글 화면이라도 주소에 #bmmsg= 가 있으면 여기)  → '이웃추가' 단추 한 번
     ② 이웃추가 화면 (BuddyAdd… · 서로이웃 고르는 칸이 보이는 화면)  → '서로이웃' 고르고 멘트 칸 채움 (그룹은 그대로)
     ③ 이웃 글 화면                                              → 공감 한 번(이미 눌렀으면 그대로) + 댓글 초안을 댓글 칸에
   확인·등록·신청·다음 단추는 절대 누르지 않는다 — press() 가 그런 글자·submit 단추를 거절하고, 누르기(.click())는 press() 한 곳뿐.
   사람 속도 지키기(주인 11/14 '너무 빠르면 봇으로 볼까 걱정'): 누르기 전에 대시보드(@@URL@@ · 열쇠 @@KEY@@ · 쿠키 없음)에 물어
   간격(서이추 60~120초 · 들르기 45~90초)·하루·한 시간 상한을 넘으면 아무것도 하지 않고 알림만 (나중에 저절로 하지 않음).
   대시보드에 못 닿으면 아무것도 하지 않는다 (하나만 빼고: ①에서 방금(10분 안) 허락받은 그 블로그의 ②는 그 허락으로 이어 함). 글 화면은 연 지 20초(가운데까지 내려 봤으면 8초)가 지나야 초안을 부탁한다.
   속이는 장치(가짜 마우스·키 입력, 브라우저 정보 바꾸기)는 없다 — 사람이 한 번 누를 때 한 단계, 마지막 단추는 주인이.
   어느 블로그로 하는지: 화면의 로그인 아이디(@@BLOGS@@ = [네이버 아이디, 블로그, 이름]) → 주소의 bmb → 이 탭에서 고른 것 → 고르기 창.
   멘트: 주소 #bmmsg= (대시보드 서이추 줄이 붙임) → 대시보드가 오늘 할 일 줄에서 찾아 준 것(msg) → ①에서 localStorage 'sbnb' 에 15분 맡긴 것
   → 없으면 ②에서 물어봄. 그 블로그가 다른 블로그 할 일 줄이면 대시보드가 거절(code row) — 고른 블로그는 잊고 다음에 다시 물음.
   seoi.py 가 kkumi.minify 로 줄여 javascript: 주소로 만든다 — 줄마다 문장이 끝나게 (// 주석은 줄 맨 앞에만 · 정규식 안에 공백·따옴표 없음). */
(async function () {
const U = '@@URL@@', K = '@@KEY@@', BL = JSON.parse('@@BLOGS@@'), W = window, D = document, L = location, SB = '이웃 관리: ', MINE = ' — 마지막 단추는 직접 눌러 주세요';
if (W.__sbnb) return;
if (!/(^|\.)blog\.naver\.com$/.test(L.hostname)) { alert(SB + '네이버 블로그 화면에서 눌러 주세요'); return; }
W.__sbnb = 1;
let ov = null;
try {
const NO = /확인|등록|신청|보내기|완료|다음|취소|삭제|차단|submit/i;
const FULL = /하루에|초과|비정상|보호조치|일시적으로/;
const HAVE = /이미\s*(서로)?이웃|신청\s*중|이미\s*신청/;
const sl = t => new Promise(r => setTimeout(r, t));
const tx = n => (n && (n.innerText || n.textContent)) || '';
const sq = s => (s || '').replace(/\s+/g, ' ').trim();
const wins = () => {
  const o = [{w: W, d: D, l: L}];
  for (const f of Array.from(D.querySelectorAll('iframe,frame'))) { try { const w = f.contentWindow; if (w.document.body) o.push({w: w, d: w.document, l: w.location}); } catch (e) {} }
  return o;
};
const qa = s => wins().flatMap(x => Array.from(x.d.querySelectorAll(s)));
const vis = n => {
  try { const s = (n.ownerDocument.defaultView || W).getComputedStyle(n); if (s.display === 'none' || s.visibility === 'hidden') return false; } catch (e) {}
  try { return !n.getClientRects || n.getClientRects().length > 0; } catch (e) { return true; }
};
// 누르기는 여기 한 곳뿐 — 확인·등록·신청 같은 마지막 단추와 submit 단추는 누르지 않는다
const press = n => {
  const t = sq(tx(n) + ' ' + (n.value || '') + ' ' + (n.getAttribute('aria-label') || ''));
  if ((n.getAttribute('type') || '').toLowerCase() === 'submit' || NO.test(t)) return false;
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
const choose = () => new Promise(res => {
  const o = card('어느 블로그 계정으로 하고 있나요?');
  const add = (t, v) => {
    const b = D.createElement('button');
    b.type = 'button';
    b.textContent = t;
    b.style.cssText = 'display:block;width:100%;margin:8px 0 0;padding:12px;border:0;border-radius:8px;font:inherit;background:#fff;color:#111';
    b.onclick = () => { o.remove(); res(v); };
    o.appendChild(b);
  };
  for (const x of BL) add(x[2], x[1]);
  add('그만두기', '');
});
// 어느 블로그로 하는지. 로그인 아이디가 우리 블로그면 그것(sure) · 아니면 줄에 적힌 블로그 → 이 탭에서 고른 것 → 고르기
const who = async hint => {
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
  const c = new AbortController(), t = setTimeout(() => c.abort(), 90000);
  try { const r = await fetch(U, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams(F), signal: c.signal}); return await r.json(); } catch (e) { return null; } finally { clearTimeout(t); }
};
const DOWN = '대시보드에 닿지 않아 아무것도 하지 않았어요 — 잠시 뒤 다시';
// 거절 알림 (다른 블로그 할 일 줄이면 이 탭에서 고른 블로그를 잊음)
const no = g => { if (g.code === 'row') { try { W.sessionStorage.removeItem('sbnb_b'); } catch (e) {} } alert(SB + g.text); };
const H = new URLSearchParams(L.hash.slice(1)), P = where(), FM = /BuddyAdd/i.test(L.pathname);
// 서로이웃 고르는 칸: 그 칸의 이름표(label · 바로 옆 글)가 '서로이웃'으로 시작하는 것 → 없으면 이웃추가 화면에서만 아이디·값으로
const lab = r => { try { return sq(tx((r.id && r.ownerDocument.querySelector('label[for=' + JSON.stringify(r.id) + ']')) || r.closest('label') || r.nextElementSibling)); } catch (e) { return ''; } };
const radios = () => {
  const a = qa('input[type=radio]'), t = a.filter(r => /^서로\s*이웃/.test(lab(r)));
  return t.length || !FM ? t : a.filter(r => /both|each|mutual/i.test(r.id + ' ' + r.value));
};
// ---------------------------------------------------------------- ② 이웃추가 화면
const form = async () => {
  const st = lget(), tg = P.id || st.t || '', body = sq(tx(D.body)), rs = radios(), ta0 = qa('textarea').filter(vis)[0];
  // 고르는 칸이 input 이 아닌 화면: '서로이웃' 글자 단추 하나 (이웃추가 화면에서만)
  const alt = rs.length ? [] : qa('label,a,button,[role=radio]').filter(n => vis(n) && sq(tx(n)).replace(/\s/g, '') === '서로이웃');
  if (!rs.length && alt.length !== 1 && !ta0) {
    if (FULL.test(body)) { await ask({a: 'stop', b: st.b || H.get('bmb') || '', w: 'add'}); alert(SB + '네이버가 한도·제한 안내를 보여요 — 오늘 서이추는 그만해요'); return; }
    if (HAVE.test(body)) { alert(SB + '이미 이웃이거나 신청 중이에요 — 아무것도 하지 않았어요'); return; }
    alert(SB + '서로이웃 고르는 칸을 못 찾았어요 (고르는 칸 ' + qa('input[type=radio]').length + '개 · 글 칸 0개) — 직접 해 주세요');
    return;
  }
  const r0 = rs[0];
  if (r0 && r0.disabled) { alert(SB + '서로이웃을 받지 않는 블로그예요 — 아무것도 하지 않았어요'); return; }
  const w = await who((st.t === tg && st.b) || H.get('bmb'));
  if (!w.b) return;
  let g = await ask({a: 'add', b: w.b, t: tg});
  // 신청 화면에서만 대시보드에 못 닿을 때: ①에서 방금 허락받은 같은 블로그면 그 허락으로 이어 함 (새로 세는 일이 아님)
  if (!g && st.g && st.t === tg && st.b === w.b && Date.now() - st.at < 6e5) g = {ok: 1, text: '대시보드에 닿지 않아 앞 단계 허락으로 이어 했어요'};
  if (!g) { alert(SB + DOWN); return; }
  if (!g.ok) { no(g); return; }
  let msg = H.get('bmmsg') || g.msg || (st.t === tg && st.m) || '';
  if (!msg) msg = prompt('서로이웃 멘트를 적어 주세요 (비워 두면 멘트 칸은 그대로 둬요)', '') || '';
  const did = [];
  if (!r0 && alt.length === 1) did.push(press(alt[0]) ? '서로이웃을 눌렀어요 (골라졌는지 화면에서 봐 주세요)' : '서로이웃은 직접 골라 주세요');
  else if (!r0) did.push('서로이웃 고르는 칸은 이 화면에 없어요 (앞 화면에서 골랐는지 봐 주세요)');
  else if (r0.checked) did.push('서로이웃 이미 골라져 있음');
  else { press(r0); await sl(700); did.push(r0.checked ? '서로이웃 고름 ✓' : '서로이웃을 눌렀는데 안 골라졌어요 — 직접 골라 주세요'); }
  const ta = qa('textarea').filter(vis)[0];
  if (!msg) did.push('멘트 없음 — 칸은 그대로');
  else if (!ta) did.push('멘트 칸을 못 찾았어요 — 다음 화면에서 책갈피를 한 번 더 (멘트: ' + msg + ')');
  else { const got = put(ta, msg); did.push('멘트 넣음 ✓ ' + got.length + '자' + (got.length < msg.length ? ' (칸이 짧아 ' + msg.length + '자에서 줄임)' : '')); }
  lset({t: tg, b: w.b, m: msg, at: st.t === tg && st.g ? st.at : Date.now(), g: st.t === tg ? st.g : 0});
  alert(SB + nm(w.b) + ' · ' + did.join(' · ') + ' · 그룹은 그대로' + MINE + ' (' + g.text + ')');
};
// ---------------------------------------------------------------- ① 블로그 화면 → 이웃추가
const blogpage = async () => {
  if (!P.id) { alert(SB + '블로그 화면에서 눌러 주세요 (블로그 아이디를 못 읽었어요)'); return; }
  if (ours(P.id)) { alert(SB + '우리 블로그예요 — 아무것도 하지 않았어요'); return; }
  const hb = H.get('bmb'), w = await who(hb);
  if (!w.b) return;
  if (w.sure && hb && hb !== w.b) { alert(SB + '이 줄은 ' + nm(hb) + ' 할 일인데 지금 ' + nm(w.b) + ' 계정이에요 — 계정을 바꾼 뒤 다시 눌러 주세요'); return; }
  const ok = n => vis(n) && /이웃\s*추가/.test(tx(n));
  let bs = qa('[data-click-area="ebc.add"],a._add_buddy,a.btn_addbuddy,a._buddy_popup_btn,#btn_add_nb,a.btn_add_nb').filter(ok);
  const known = bs.length;
  if (!known) bs = qa('a,button').filter(n => ok(n) && sq(tx(n)).replace(/[+\s]/g, '') === '이웃추가' && !n.closest('[class*=ecommend],template'));
  if (!bs.length) {
    const st = qa('a,button').filter(vis).map(n => sq(tx(n))).filter(t => t.length < 12 && /이웃/.test(t));
    alert(SB + '이웃추가 단추를 못 찾았어요' + (st.length ? ' — 보이는 단추: ' + st.slice(0, 4).join(' / ') + ' (이미 이웃일 수 있어요)' : '') + ' · 아무것도 하지 않았어요');
    return;
  }
  const b0 = bs[0];
  if (bs.length > 1 && !known) { alert(SB + '이웃추가 단추가 ' + bs.length + '개라 누르지 않았어요 — 직접 눌러 주세요'); return; }
  const g = await ask({a: 'add', b: w.b, t: P.id});
  if (!g) { alert(SB + DOWN); return; }
  if (!g.ok) { no(g); return; }
  lset({t: P.id, b: w.b, m: H.get('bmmsg') || g.msg || '', at: Date.now(), g: 1});
  alert(SB + nm(w.b) + ' · 이웃추가를 눌러요 — 신청 화면이 뜨면 책갈피를 한 번 더 눌러 주세요 (' + g.text + ')');
  if (!press(b0)) alert(SB + '이웃추가 단추를 누르지 못했어요 — 직접 눌러 주세요');
};
// ---------------------------------------------------------------- ③ 이웃 글 → 공감 + 댓글 초안
const visit = async () => {
  if (ours(P.id)) { alert(SB + '우리 블로그 글이에요 — 아무것도 하지 않았어요'); return; }
  if (out()) { alert(SB + '네이버에 로그인돼 있지 않아요 — 로그인한 뒤 다시'); return; }
  const tn = qa('.se-title-text,.pcol1 .itemSubjectBoldfont,h3.se_textarea,.tit_h3').filter(vis)[0];
  const bn = qa('.se-main-container,#postViewArea,.post_ct,#viewTypeSelector').filter(vis)[0];
  const title = sq(tx(tn)).slice(0, 200) || sq(D.title).slice(0, 200), text = sq(tx(bn)).slice(0, 3000);
  if (!bn || text.length < 30) { alert(SB + '글 본문을 못 읽었어요 (제목 ' + (tn ? '있음' : '없음') + ' · 본문 ' + text.length + '자) — 글이 다 뜬 뒤 다시'); return; }
  const rd = Math.floor((W.performance && W.performance.now ? W.performance.now() : 99000) / 1000);
  let sc = 0;
  try { const f = bn.ownerDocument.defaultView, fe = f !== W && f.frameElement, r = bn.getBoundingClientRect(); sc = (W.scrollY > 0 || f.scrollY > 0) && (fe ? fe.getBoundingClientRect().top : 0) + r.top + r.height / 2 <= W.innerHeight ? 1 : 0; } catch (e) {}
  if (rd < (sc ? 8 : 20)) { alert(SB + '글을 조금 더 읽고 눌러 주세요 — ' + ((sc ? 8 : 20) - rd) + '초 뒤에 다시 (가운데까지 내려 봤으면 8초, 아니면 20초)'); return; }
  const w = await who(H.get('bmb'));
  if (!w.b) return;
  ov = card('댓글 초안을 쓰는 중이에요 — 30초쯤 걸려요 · 화면은 그대로 두세요');
  const g = await ask({a: 'visit', b: w.b, u: 'https://blog.naver.com/' + P.id + '/' + P.no, t: title, x: text, rd: rd, sc: sc});
  ov.remove();
  ov = null;
  if (!g) { alert(SB + DOWN); return; }
  if (!g.ok) { alert(SB + g.text); return; }
  const did = [];
  // 공감: 글 끝 단추 하나 (떠 있는 단추는 다른 게 없을 때만) · 이미 눌렸으면 그대로 · 여러 개면 누르지 않음
  const on = n => n.getAttribute('aria-pressed') === 'true' || /(^|\s)on(\s|$)/.test(n.className);
  const lk = qa('a.u_likeit_button._face').filter(vis), fx = n => !!n.closest('[class*=Interact__is_floating],.floating_bottom'), end = lk.filter(n => !fx(n)), ls = end.length ? end : lk;
  if (g.like === false) did.push('공감은 누르지 않았어요');
  else if (!ls.length) did.push('공감 단추를 못 찾았어요');
  else if (ls.some(on)) did.push('공감은 이미 눌러져 있어요');
  else if (ls.length > 1) did.push('공감 단추가 ' + ls.length + '개라 누르지 않았어요');
  else {
    press(ls[0]);
    for (let i = 0; i < 8 && !on(ls[0]); i++) await sl(400);
    did.push(on(ls[0]) ? '공감 ✓' : '공감을 눌렀는데 표시가 안 바뀌었어요 — 화면을 봐 주세요');
  }
  // 댓글: 칸이 없으면 댓글 단추로 열고 기다림 · 칸에 글이 있으면 건드리지 않음 · 등록은 주인이
  const BOX = '#naverComment__write_textarea,.u_cbox_write_wrap [contenteditable=true],.u_cbox_write_wrap textarea';
  const cm = g.comment || '';
  if (!cm) did.push(g.note || '댓글 초안은 없어요');
  else {
    let bx = qa(BOX).filter(vis)[0];
    if (!bx) {
      const cb = qa('[class*=Interact__comment_btn],a.btn_comment').filter(vis), ce = cb.filter(n => !fx(n)), c0 = (ce.length ? ce : cb).pop();
      if (c0 && press(c0)) for (let i = 0; i < 15 && !bx; i++) { await sl(400); bx = qa(BOX).filter(vis)[0]; }
    }
    if (qa('.u_cbox_type_logged_out').filter(vis).length) did.push('댓글 칸이 로그인을 바라요');
    else if (qa('li.u_cbox_comment[data-info*="mine:true"]').length) did.push('이미 내 댓글이 있는 글이에요 — 초안은 넣지 않았어요');
    else if (!bx) { did.push('댓글 칸을 못 찾았어요 — 초안을 복사해 쓰세요'); prompt('댓글 초안 (복사해서 쓰세요)', cm); }
    else if (sq(bx.getAttribute('contenteditable') === 'true' ? bx.textContent : bx.value)) { did.push('댓글 칸에 쓰던 글이 있어 그대로 뒀어요'); prompt('댓글 초안 (복사해서 쓰세요)', cm); }
    else { put(bx, cm); did.push("댓글 초안 넣음 ✓ — 내 말로 고친 뒤 '등록'은 직접"); }
  }
  alert(SB + nm(w.b) + ' · ' + did.join(' · ') + ' (' + g.text + ')');
};
if (FM || radios().length) await form();
else if (P.no && !H.get('bmmsg')) await visit();
else await blogpage();
} catch (e) {
alert(SB + '오류로 멈췄어요 — ' + String((e && e.message) || e).slice(0, 120));
} finally {
if (ov) ov.remove();
W.__sbnb = 0;
}
})();
