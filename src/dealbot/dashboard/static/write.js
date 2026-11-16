/* '글쓰기 한 번에' 진행기 (주인 11/16). 책갈피(writebm.py LOADER)가 대시보드에서 받아 네이버 블로그 글쓰기 화면에서 돌린다 — 화면에서 바로 받지 않는 파일.
   한 번 누르면: 그 블로그(로그인된 아이디)의 오늘 붙여넣기 대기 글을 하나씩 받아 → 새 글쓰기 화면을 열고 → 제목 → 본문·꾸미기·사진·태그·카테고리·예약 시각
   (아래 CORE = 꾸미기 책갈피 kkumi.js 그대로) → 멈춤 → 주인이 '발행'을 누르면 → 대시보드에 알리고(할 일 줄 체크) 30~60초 쉰 뒤 다음 글. 끝나면 요약.
   이동에도 이어가기: 진행기는 누른 화면에 남는다. 그 화면의 내용을 비우고(바깥이 frameset 이어도 보이게 body 를 새로 끼움) 글쓰기 화면을 같은 출처 iframe 으로
   글마다 새로 연다 (네이버도 글쓰기를 안쪽 창에 넣어 씀 · 11/16 응답 머리에 X-Frame-Options·CSP 없음). iframe 은 sandbox(맨 위 창 이동만 못 함) —
   네이버 화면이 발행 뒤·로그인 풀림 때 top.location 을 바꿔 진행기가 사라지는 것을 막는다. sandbox 에서 글쓰기가 안 뜨면 sandbox 없이 한 번 더.
   발행 알아보기 (누르지 않고 지켜보기만): 글쓰기 창의 저장 요청 답(성공) · 창 주소가 글 주소로 바뀜 · 주인이 발행을 누른 뒤 창 이동이 막힘/글쓰기 화면이 사라짐.
   발행을 눌렀는데 25초 안에 못 알아보면 '확인 필요'(체크하지 않음)로 넘어간다.
   하지 않는 것: 발행·예약을 끝내는 단추 누르기 (CORE 의 tap 막이 · 여기서 누르는 것은 취소·닫기·'본문 추가'·발행 설정 열기·임시저장뿐) ·
   자동입력 방지·사람 확인 풀기 (뜨면 임시저장 → '폰에서 직접' → 다음 글) · 알림·확인·입력 창 (사파리에서 멈춤 — 모두 화면 위 한 줄로). */
async CFG => {
const V = 1;
const W = window, D0 = document, S = m => new Promise(r => setTimeout(r, m));
const CORE = '@@KKUMI@@';
const PUB = /blog\.naver\.com\/(?:PostView\.naver\?.*logNo=|[\w-]+\/|[\w-]+\?.*logNo=)(\d{6,})|[?&]logNo=(\d{6,})/;
const WRITE = /postwrite|PostWriteForm|Redirect=Write/i;
const WALL = ['자동입력 방지', '자동 입력 방지', '보안문자', '로봇이 아닙니다', 'captcha', '비정상적인 접근'];
const SB = 'allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads';
const DOWN = '대시보드에 닿지 못했어요 — 잠시 뒤 책갈피를 다시 눌러 주세요';
const sp = t => (t || '').replace(/\s+/g, ' ').trim();
let fr = null, bar = null, list = null, K = null, sig = {}, sbOn = 1;
// 창과 그 안쪽 창들의 문서 (같은 출처만 — 못 읽는 창은 건너뜀)
const docs = (w, o = []) => {
  try { const d = w.document; if (d && d.body) { o.push(d); for (const f of d.querySelectorAll('iframe,frame')) docs(f.contentWindow, o); } } catch (e) {}
  return o;
};
const ed = ds => ds.find(d => d.querySelector('.se-content')) || null;
const seen = e => e.offsetParent !== null;
const say = t => { if (bar) bar.textContent = '글쓰기 한 번에 · ' + t; };
const ask = async F => {
  try { return await (await fetch(CFG.u, {method: 'POST', mode: 'cors', credentials: 'omit', body: new URLSearchParams({k: CFG.k, v: V, ...F})})).json(); } catch (e) { return null; }
};
// ── 누른 화면 읽기: 글쓰기 화면 주소 · 블로그 (주소의 블로그 아이디 = 로그인한 내 블로그 — 글쓰기는 내 블로그만 열림)
const first = docs(W), E0 = ed(first);
if (!E0) { alert_('네이버 블로그 글쓰기 화면에서 눌러 주세요'); return; }
function alert_(t) {
  const o = D0.createElement('div');
  o.style.cssText = 'position:fixed;left:8px;right:8px;top:8px;z-index:2147483647;padding:14px;border-radius:12px;background:#111;color:#fff;font:16px/1.5 sans-serif;text-align:center';
  o.textContent = '글쓰기 한 번에 · ' + t;
  // 바깥이 frameset 이면 거기 붙인 것은 안 보임 — 보이는 첫 문서에
  const d = first.find(x => x.body.tagName !== 'FRAMESET') || D0;
  (d.body || d.documentElement).appendChild(o);
  setTimeout(() => o.remove(), 9000);
}
const ids = [];
for (const d of first) {
  try {
    const u = new URL(d.location.href), m = /^\/([A-Za-z0-9_-]{3,30})(?:\/postwrite|\/?$)/i.exec(u.pathname);
    for (const x of [u.searchParams.get('blogId'), m && m[1], d.defaultView.blogId, d.defaultView.userId]) if (x && typeof x === 'string') ids.push(x.toLowerCase());
  } catch (e) {}
}
const mine = CFG.bl.find(x => ids.includes(x[0]));
if (!mine) { alert_('우리 블로그 계정의 글쓰기 화면이 아니에요' + (ids[0] ? ' (' + ids[0] + ')' : '')); return; }
const BLOG = mine[1], URL_ = E0.location.href.split('#')[0];
// ── 화면 차지: 위 한 줄(지금 하는 일) + 아래 글쓰기 창
const nb = D0.createElement('body'), box = D0.createElement('div');
nb.style.cssText = 'margin:0;background:#fff';
box.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;display:flex;flex-direction:column;background:#fff;z-index:2147483647';
bar = D0.createElement('div');
bar.style.cssText = 'flex:none;padding:10px 14px;background:#111;color:#fff;font:600 15px/1.4 -apple-system,sans-serif';
list = D0.createElement('div');
list.style.cssText = 'flex:none;max-height:40%;overflow:auto;padding:0 14px;background:#f4f5f7;color:#111;font:14px/1.6 -apple-system,sans-serif';
box.append(bar, list);
nb.append(box);
if (D0.body) D0.documentElement.replaceChild(nb, D0.body); else D0.documentElement.appendChild(nb);
const line = t => { const p = D0.createElement('p'); p.style.margin = '8px 0'; p.textContent = t; list.append(p); };
// ── 글쓰기 창 열기 (글마다 새로) → 글쓰기 문서 | 'login' | null
const open = async sb => {
  if (fr) fr.remove();
  fr = D0.createElement('iframe');
  if (sb) fr.setAttribute('sandbox', SB);
  fr.style.cssText = 'flex:1;width:100%;border:0;background:#fff';
  fr.src = URL_;
  box.append(fr);
  for (let i = 0; i < 60; i++) {
    await S(500);
    const ds = docs(fr.contentWindow), e = ed(ds);
    if (e) { await S(1500); return e; }
    if (ds.some(d => /nidlogin|nid\.naver\.com/.test(d.documentElement.innerHTML.slice(0, 4000)))) return 'login';
    if (wall(ds)) return null;
  }
  return null;
};
// 자동입력 방지·사람 확인 화면인가 (본문에 쓴 글은 빼고 봄) → 그 낱말 | ''
const wall = ds => {
  for (const d of ds) {
    try {
      const c = d.querySelector('.se-content');
      let t = d.body.innerText || '';
      if (c) t = t.replace(c.innerText || '', '');
      t = t.slice(0, 6000).toLowerCase();
      const h = WALL.find(w => t.includes(w));
      if (h) return h;
      if ([...d.querySelectorAll('iframe[src*="captcha" i],img[src*="captcha" i],[id*="captcha" i],[class*="captcha" i]')].some(seen)) return 'captcha';
    } catch (e) {}
  }
  return '';
};
// 네이버 안내 창 (제목 없음 등)이 떠 있나
const popup = ds => ds.some(d => [...d.querySelectorAll('.se-popup-alert,.se-popup-alert-confirm,[class*="layer_alert"],[role="alertdialog"]')].some(seen));
// ── 지켜보기: 글쓰기 창의 저장 요청 답 · 막힌 창 이동 · 주인이 누른 발행 (우리는 누르지 않음)
const good = (u, t) => {
  if (/auto|temp|draft|upload|photo|image|oglink|tag|token|session|stat|log\b/i.test(u.split('?')[0])) return;
  if (/Rabbit(Write|Update)|PostWrite(?!Form)|\/publish/i.test(u) ? !/"isSuccess"\s*:\s*false|"error/i.test(t) : /"isSuccess"\s*:\s*true/.test(t) && /logNo|redirectUrl/.test(t)) sig.net = 1;
};
const arm = d => {
  const w = d.defaultView;
  if (!w || w.__wbArm) return;
  w.__wbArm = 1;
  try {
    const X = w.XMLHttpRequest.prototype, o = X.open, s = X.send;
    X.open = function (m, u) { this.__wb = [String(m), String(u)]; return o.apply(this, arguments); };
    X.send = function () {
      const x = this;
      if (x.__wb && /post/i.test(x.__wb[0])) x.addEventListener('load', () => { try { if (x.status === 200) good(x.__wb[1], typeof x.response === 'string' ? x.response : JSON.stringify(x.response)); } catch (e) {} });
      return s.apply(this, arguments);
    };
    const f = w.fetch;
    if (f) w.fetch = function (a, b) {
      const p = f.apply(this, arguments);
      try { if (/post/i.test((b && b.method) || (a && a.method) || '')) p.then(r => r.status === 200 && r.clone().text().then(t => good(String((a && a.url) || a), t))).catch(() => {}); } catch (e) {}
      return p;
    };
  } catch (e) {}
  const er = e => { if (/secur|insecure|navigat|sandbox/i.test(String(e.message || e.reason || ''))) sig.err = Date.now(); };
  w.addEventListener('error', er);
  w.addEventListener('unhandledrejection', er);
  // 주인이 손으로 누른 것만 (isTrusted) — 발행 설정창의 마지막 발행 단추
  d.addEventListener('click', e => {
    const b = e.isTrusted && e.target && e.target.closest && e.target.closest('button,a');
    if (!b) return;
    const t = sp(b.innerText), opener = /publish_btn/.test(b.className) && !b.closest("[class*='layer']");
    if (!opener && (/confirm_btn/.test(b.className) || b.getAttribute('data-testid') === 'seOnePublishBtn' || /^(발행|예약)/.test(t))) sig.click = Date.now();
  }, true);
};
// ── 글 넣기 전 (CORE 가 부름 — k = 꾸미기의 도구들): 이어 쓰기 창 닫기 → 제목 → 커서를 본문으로
const pre = it => async k => {
  K = k;
  await k.wait(() => k.vis(['.se-popup']), 1500);
  for (let n = 0; n < 3; n++) {
    const pop = k.vis(['.se-popup-alert-confirm', '.se-popup-alert', '.se-popup']);
    if (!pop || !/작성 중인 글|이어서 작성|임시저장된 글/.test(pop.innerText)) break;
    const c = [...pop.querySelectorAll('button')].find(b => /cancel/i.test(b.className) || sp(b.innerText) === '취소');
    if (!c || /발행|확인|등록/.test(c.innerText)) break;
    k.press(c);
    await S(900);
  }
  const hp = k.vis(['.se-help-panel-close-button', 'button.se-help-close']);
  if (hp) { k.press(hp); await S(400); }
  const T = () => k.D.querySelector('.se-documentTitle .se-text-paragraph,.se-section-documentTitle .se-text-paragraph');
  const head = sp(it.title).slice(0, 12), has = () => { const t = T(); return !!t && sp(k.real(t)).includes(head); };
  const inBody = () => k.body_().some(p => sp(p.textContent).includes(head));
  const t = T();
  if (!t) { K.why = '제목 칸을 못 찾았어요'; return false; }
  if (k.real(t) || k.comps().some(c => k.real(c))) { K.why = '글쓰기 화면에 쓰던 글이 있어요'; return false; }
  // 제목: 제목 줄을 누르고 글자만 붙여넣기 신호 → 안 되면 글 넣기 명령
  k.press(t);
  k.setSel(t);
  await S(300);
  const dt = new k.W.DataTransfer();
  dt.setData('text/plain', it.title);
  (t.closest('[contenteditable="true"]') || k.host() || k.D.body).dispatchEvent(new k.W.ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
  if (!await k.wait(() => has() || inBody(), 2500)) {
    k.setSel(t);
    try { k.D.execCommand('insertText', false, it.title); } catch (e) {}
    await k.wait(() => has() || inBody(), 1500);
  }
  if (!has()) { K.why = inBody() ? '제목이 본문 칸에 들어갔어요' : '제목을 넣지 못했어요'; return false; }
  // 본문으로: 본문 줄을 누르고, 맨 아래 '본문 추가'(아이패드에서 스크립트로 커서가 옮겨지는 단 하나의 길 — 꾸미기 10/29)도 누름
  const n0 = k.comps().length, last = k.body_().slice(-1)[0], bt = k.vis(k.BOTTOM);
  if (last) { k.press(last); k.setSel(last); await S(300); }
  if (bt) { k.press(bt); await k.wait(() => k.comps().length > n0, 1500); }
  await S(300);
  return true;
};
// 임시저장 (자동입력 방지가 떴을 때만) — CORE 의 tap 으로 (발행·확인·등록 단추면 스스로 멈춤)
const save = async () => {
  try {
    const b = [...K.D.querySelectorAll('button')].find(x => seen(x) && /^(저장|임시저장)$/.test(sp((x.innerText || '').split('\n')[0])));
    if (!b) return 0;
    await K.tap(b);
    await S(2500);
    return 1;
  } catch (e) { return 0; }
};
// ── 글 하나 → {r: ok|unsure|phone|fail|login, p: 사진은 직접, n: 한마디}
const one = async (it, at) => {
  sig = {};
  K = null;
  say(at + ' 「' + it.title + '」 글쓰기 화면을 여는 중');
  let E = await open(sbOn);
  if (!E && sbOn && !wall(docs(fr.contentWindow))) { sbOn = 0; E = await open(0); }
  if (E === 'login') return {r: 'login'};
  if (!E) return wall(docs(fr.contentWindow)) ? {r: 'phone', n: '글쓰기 화면에 자동입력 방지'} : {r: 'fail', n: '글쓰기 화면이 안 열려요'};
  for (const d of docs(fr.contentWindow)) arm(d);
  say(at + ' 「' + it.title + '」 넣는 중 — 1~2분 걸려요, 화면을 건드리지 마세요');
  const msgs = [];
  let R = null;
  try { await CORE(fr.contentDocument, t => { msgs.push(String(t)); }, () => false, {h: it.html, t: it.text}, k => { R = k.R; return pre(it)(k); }); } catch (e) { msgs.push('오류: ' + e); }
  if (!R || !R.finished) return {r: 'fail', n: (K && K.why) || sp(msgs[msgs.length - 1] || '글을 넣지 못했어요').slice(0, 70)};
  const hard = R.bad.find(b => /^글 안 들어감|^제목 칸/.test(b));
  if (hard) return {r: 'fail', n: hard.slice(0, 70)};
  const photo = R.pics[1] > R.pics[0], tips = [];
  if (photo) tips.push('사진은 직접 넣어 주세요 (' + R.pics[0] + '/' + R.pics[1] + ')');
  if (it.group === '예약' && (!R.book || R.why)) tips.push('예약 시각은 직접 골라요' + (it.when ? ' (' + it.when + ')' : ''));
  else if (R.book) tips.push('예약 ' + R.at + ' 채움');
  const etc = R.bad.filter(b => !/^사진/.test(b));
  if (etc.length) tips.push('확인: ' + etc.join(', '));
  // 발행 설정창이 안 열려 있으면 열어 둔다 (꾸미기가 태그를 넣을 때 여는 그 단추 — 마지막 발행 단추가 아님)
  if (!K.tagIn()) { const b = K.pubBtn(); if (b) { K.press(b); await K.wait(K.tagIn, 4000); } }
  const ready = at + ' 「' + it.title + '」 준비 끝 — ' + (it.group === '예약' ? "'발행'만 눌러 주세요 (예약)" : "'발행'만 눌러 주세요") + (tips.length ? ' · ' + tips.join(' · ') : '');
  say(ready);
  const note = tips.join(' · ').slice(0, 70);
  let gone = 0;
  for (;;) {
    await S(500);
    const ds = docs(fr.contentWindow);
    for (const d of ds) arm(d);
    if (sig.net || ds.some(d => PUB.test(d.location.href) && !WRITE.test(d.location.href))) return {r: 'ok', p: photo, n: note};
    if (wall(ds)) { const s = await save(); return {r: 'phone', p: photo, n: '자동입력 방지 — ' + (s ? '임시저장함' : '임시저장은 자동 저장에 맡김')}; }
    const here = !!ed(ds);
    gone = here ? 0 : gone + 1;
    if (!sig.click) { if (gone > 8) return {r: 'fail', n: '글쓰기 화면이 닫혔어요'}; continue; }
    if (sig.err >= sig.click || (gone > 3 && ds.length && ds.every(d => d.readyState === 'complete'))) return {r: 'ok', p: photo, n: note};
    const age = Date.now() - sig.click;
    if (age > 4000 && here && popup(ds)) { sig.click = 0; say(ready + ' — 네이버 안내를 보고 다시 발행을 눌러 주세요'); continue; }
    if (age > 25000) {
      if (here && K.tagIn()) { sig.click = 0; continue; }
      return {r: 'unsure', p: photo, n: note};
    }
  }
};
// ── 차례로
const MARK = {ok: '✓ 발행·예약', unsure: '? 발행을 눌렀지만 확인 못 함 — 블로그에서 확인하고 할 일은 직접 체크', phone: '폰에서 직접', fail: '못 넣음'};
const sum = {ok: 0, unsure: 0, phone: 0, fail: 0, photo: 0, chk: 0};
let n = 0, end = '';
for (;;) {
  const q = await ask({a: 'next', b: BLOG});
  if (!q || !q.ok) { end = (q && q.text) || DOWN; break; }
  if (!q.next) { end = n ? '' : q.text; break; }
  for (let s = q.wait; s > 0; s--) { say('다음 글까지 ' + s + '초 쉬어요 (남은 글 ' + q.left + ')'); await S(1000); }
  n++;
  const it = q.next, res = await one(it, n + '/' + (n - 1 + q.left));
  if (res.r === 'login') { end = '네이버 로그인이 풀렸어요 — 로그인한 뒤 글쓰기 화면에서 다시 눌러 주세요'; break; }
  const d = await ask({a: 'done', b: BLOG, id: it.id, r: res.r, p: res.p ? 1 : 0, n: res.n || ''});
  sum[res.r]++;
  if (res.p) sum.photo++;
  if (d && d.checked) sum.chk++;
  line(MARK[res.r] + ' · 「' + it.title + '」' + (res.n ? ' — ' + res.n : '') + (d && d.checked ? ' · 할 일 줄 체크 ✓' : res.r === 'ok' ? ' · 할 일 줄은 직접 체크' : ''));
  // 못 넣은 글은 화면을 그대로 두고 멈춘다 (같은 까닭으로 다음 글도 안 될 것 — 발행된 것은 없음)
  if (res.r === 'fail') { end = '글을 넣지 못해 멈췄어요 (' + (res.n || '') + ') — 발행된 것은 없어요. 이 글은 예전처럼 꾸미기 책갈피로 올려 주세요'; break; }
  if (!d) { end = DOWN; break; }
}
if (fr && !sum.fail) fr.remove();
list.style.maxHeight = sum.fail ? '40%' : 'none';
say((end ? end + ' · ' : '끝났어요 · ') + '발행·예약 ' + sum.ok + (sum.unsure ? ' · 확인 필요 ' + sum.unsure : '') + (sum.phone ? ' · 폰에서 직접 ' + sum.phone : '')
  + (sum.photo ? ' · 사진은 직접 ' + sum.photo : '') + ' · 할 일 체크 ' + sum.chk + ' · v' + V);
}
