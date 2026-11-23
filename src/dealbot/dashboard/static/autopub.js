/* '하루치 한 번에 (발행까지)' 진행기 v1 (주인 11/23 — "책갈피 누르면 한 번에 글 여러 개를 넣고, 발행 누르는 것까지"). autopub.py 가 줄여 책갈피 주소에 통째로 넣는다
   (대시보드에서 코드를 받지 않음 — 데이터만 · 화면에서 바로 받지 않는 파일). 서버는 네이버를 열지도 로그인하지도 않는다 — 모두 주인이 로그인한 자기 사파리에서.
   한 번 누르면: 그 블로그(로그인한 아이디 — 우리 블로그가 아니면 거절)의 준비된 글을 차례로 받아 → 새 글쓰기 화면 → 제목 → 본문·꾸미기·사진·태그·카테고리·예약 시각
   (아래 CORE = 꾸미기 책갈피 kkumi.js 그대로) → 스스로 점검 → 깨끗하면 '해 봄'을 먼저 남기고 마지막 발행 단추를 누름 → 발행 확인(저장 요청 답 · 글 주소 · 끝 알림)
   → 대시보드에 글 번호와 함께 알림(할 일 줄 ✓) → 40~120초 쉬고 다음 글 (한 번에 5편까지 — 간격·상한·주말·쉼은 대시보드가 정함).
   점검에 하나라도 걸리면 (사진 빠짐 · 제목/본문 다름 · 포스트잇 · 카테고리 · 예약 시각 · 제휴 고지 · 태그) 그 글은 발행하지 않고 멈춘다 — 무엇이 틀렸는지 적고,
   주인이 고친 뒤 [고쳤어요] 단추(또는 책갈피 한 번 더)를 누르면 발행창·글을 다시 점검해 이어 간다.
   이동에도 이어가기: 진행기는 누른 화면에 남고 글쓰기는 같은 출처 iframe(sandbox — 맨 위 창 이동만 막음)으로 글마다 새로 연다 ('글쓰기 한 번에' write.js 와 같은 길).
   iframe 에서 글쓰기가 안 뜨면 이 브라우저에 '글마다 한 번'(DIR)이라고 적고 화면을 다시 연다 — 그 뒤로는 누른 화면의 글쓰기에 바로 넣고 발행하고,
   발행되면 '다음 글 N/M — 책갈피 한 번 더' (새 글쓰기 화면은 스스로 엶).
   네이버가 자동입력 방지·보호조치·이용 제한·한도 안내를 보이면 풀지 않고 그 자리에서 멈춘다 → 대시보드가 그 블로그를 24시간 쉬게 함.
   알림·확인·입력 창은 쓰지 않는다 (사파리에서 멈춤 — 모두 화면의 한 줄과 단추 하나로). */
async CFG => {
const V = 1;
const W = window, D0 = document, S = m => new Promise(r => setTimeout(r, m));
// 한글 글은 autopub.py 가 Z('…') 로 줄여 넣는다 (seoi.pack — 한 번에 책갈피 one.js 와 같은 Z · 껍데기만 · 정규식 안에 따옴표 없음 · 한글 글 안에 역슬래시 없음)
const Z = s => s.replace(/([A-I])(..)|J(....)|\.(..)|_/g, (m, a, b, c, d) => a ? String.fromCharCode(44032 + (a.charCodeAt(0) - 65) * 1296 + parseInt(b, 36)) : c || d ? String.fromCharCode(parseInt(c || d, 16)) : ' ');
const CORE = '@@KKUMI@@';
const PUB = /blog\.naver\.com\/(?:PostView\.naver\?.*logNo=|[\w-]+\/|[\w-]+\?.*logNo=)(\d{6,})|[?&]logNo=(\d{6,})/;
const WRITE = /postwrite|PostWriteForm|GoBlogWrite|Redirect=Write/i;
const WALL = ['자동입력 방지', '자동 입력 방지', '보안문자', '로봇이 아닙니다', 'captcha', '비정상적인', '보호조치', '보호 조치', '보호하고 있습니다', '이용이 제한', '이용 제한', '일시적으로 제한', '발행 한도', '등록 한도', '한도를 초과', '더 이상 등록할 수 없'];
const OKAY = /(발행|예약|등록)(이|을)?\s*(완료|되었|됐)/;
const DISC = /수수료를\s*(받|제공|지급)/;
const SB = 'allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-modals allow-downloads';
const DOWN = '대시보드에 닿지 못했어요 — 잠시 뒤 책갈피를 다시 눌러 주세요';
const TAG = '하루치 한 번에 · ';
const sp = t => (t || '').replace(/\s+/g, ' ').trim();
let fr = null, K = null, sig = {}, sbOn = 1, DIR = 0;
try { DIR = W.localStorage.getItem('apbd') === '1'; } catch (e) {}
// 창과 그 안쪽 창들의 문서 (같은 출처만 — 못 읽는 창은 건너뜀)
const docs = (w, o = []) => {
  try { const d = w.document; if (d && d.body) { o.push(d); for (const f of d.querySelectorAll('iframe,frame')) docs(f.contentWindow, o); } } catch (e) {}
  return o;
};
const ed = ds => ds.find(d => d.querySelector('.se-content')) || null;
const seen = e => e.offsetParent !== null;
const root = () => docs(fr ? fr.contentWindow : W);
const ask = async F => {
  try { return await (await fetch(CFG.u, {method: 'POST', mode: 'cors', credentials: 'omit', keepalive: true, body: new URLSearchParams({k: CFG.k, v: V, ...F})})).json(); } catch (e) { return null; }
};
// ── 누른 화면 읽기: 글쓰기 화면 · 블로그 (주소의 블로그 아이디 = 로그인한 내 블로그 — 글쓰기는 내 블로그만 열림)
const first = docs(W), E0 = ed(first);
const ui = D0.createElement('div'), bar = D0.createElement('div'), list = D0.createElement('div'), btn = D0.createElement('button');
ui.id = 'apb';
bar.style.cssText = 'padding:10px 14px;background:#111;color:#fff;font:600 15px/1.4 -apple-system,sans-serif';
list.style.cssText = 'max-height:30vh;overflow:auto;padding:0 14px;background:#f4f5f7;color:#111;font:14px/1.6 -apple-system,sans-serif';
btn.type = 'button';
btn.style.cssText = 'display:none;width:100%;min-height:48px;border:0;background:#03a94f;color:#fff;font:600 16px -apple-system,sans-serif';
ui.append(bar, list, btn);
const say = t => { bar.textContent = TAG + t; };
const line = t => { const p = D0.createElement('p'); p.style.margin = '8px 0'; p.textContent = t; list.append(p); };
// 단추 하나 (고쳤어요 · 주말에도 시작) — 책갈피를 한 번 더 눌러도 같은 뜻 (W.__apb)
const ask1 = t => { btn.textContent = t; btn.style.display = t ? 'block' : 'none'; sig.go = 0; };
btn.onclick = W.__apb = () => { sig.go = 1; };
const put = fixed => {
  const d = first.find(x => x.body.tagName !== 'FRAMESET') || D0;
  if (fixed) ui.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;box-shadow:0 -2px 12px rgba(0,0,0,.3)';
  (d.body || d.documentElement).appendChild(ui);
};
if (!E0) { put(1); say('네이버 블로그 글쓰기 화면에서 눌러 주세요'); setTimeout(() => ui.remove(), 9000); return; }
const ids = [];
for (const d of first) {
  try {
    const u = new URL(d.location.href), m = /^\/([A-Za-z0-9_-]{3,30})(?:\/postwrite|\/?$)/i.exec(u.pathname);
    for (const x of [u.searchParams.get('blogId'), m && m[1], d.defaultView.blogId, d.defaultView.userId]) if (x && typeof x === 'string') ids.push(x.toLowerCase());
  } catch (e) {}
}
// 우리 블로그 하나로만 읽혀야 함 (둘 이상이면 어느 블로그 글을 넣을지 모름 — 멈춤)
const mines = CFG.bl.filter(x => ids.includes(x[0])), mine = mines.length === 1 ? mines[0] : null;
if (!mine) { put(1); say(mines.length ? '어느 블로그 계정인지 헷갈려요 — 그 블로그로 로그인한 글쓰기 화면에서 눌러 주세요' : '우리 블로그 계정의 글쓰기 화면이 아니에요' + (ids[0] ? ' (' + ids[0] + ')' : '') + ' — 아무것도 하지 않았어요'); setTimeout(() => ui.remove(), 9000); return; }
const BLOG = mine[1], URL_ = E0.location.href.split('#')[0];
// ── 화면: 이어가기(iframe) = 누른 화면을 비우고 위에 한 줄 + 아래 글쓰기 창 · 글마다 한 번(DIR) = 글쓰기 화면 아래쪽에 한 줄
const box = D0.createElement('div');
if (DIR) put(1);
else {
  const nb = D0.createElement('body');
  nb.style.cssText = 'margin:0;background:#fff';
  box.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;display:flex;flex-direction:column;background:#fff;z-index:2147483647';
  ui.style.flex = 'none';
  box.append(ui);
  nb.append(box);
  if (D0.body) D0.documentElement.replaceChild(nb, D0.body); else D0.documentElement.appendChild(nb);
}
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
    const ds = root(), e = ed(ds);
    if (e) { await S(1500); return e; }
    if (ds.some(d => /nidlogin|nid\.naver\.com/.test(d.documentElement.innerHTML.slice(0, 4000)))) return 'login';
    if (wall(ds)) return null;
  }
  return null;
};
// 화면의 글 (본문·제목에 쓴 글과 우리 한 줄은 빼고) — 소문자
const outer = d => {
  let t = '';
  try {
    t = d.body.innerText || '';
    for (const c of d.querySelectorAll('.se-content,.se-documentTitle,#apb')) t = t.replace(c.innerText || '', '');
  } catch (e) {}
  return t.slice(0, 8000).toLowerCase();
};
// 자동입력 방지·보호조치·이용 제한·한도 안내인가 → 그 낱말 | ''
const wall = ds => {
  for (const d of ds) {
    const t = outer(d), h = WALL.find(w => t.includes(w));
    if (h) return h;
    try { if ([...d.querySelectorAll('iframe[src*="captcha" i],img[src*="captcha" i],[id*="captcha" i],[class*="captcha" i]')].some(seen)) return 'captcha'; } catch (e) {}
  }
  return '';
};
// 네이버 안내 창 (제목 없음 등)의 글 | ''
const popup = ds => {
  for (const d of ds) for (const e of d.querySelectorAll('.se-popup-alert,.se-popup-alert-confirm,[class*="layer_alert"],[role="alertdialog"]')) if (seen(e)) return sp(e.innerText) || '안내 창';
  return '';
};
// ── 지켜보기: 글쓰기 창의 저장 요청 답(글 번호) · 막힌 창 이동
const good = (u, t) => {
  if (/auto|temp|draft|upload|photo|image|oglink|tag|token|session|stat|log\b/i.test(u.split('?')[0])) return;
  if (!(/Rabbit(Write|Update)|PostWrite(?!Form)|\/publish/i.test(u) ? !/\x22isSuccess\x22\s*:\s*false|\x22error/i.test(t) : /\x22isSuccess\x22\s*:\s*true/.test(t) && /logNo|redirectUrl/.test(t))) return;
  const m = /logNo\D{1,6}(\d{6,})|blog\.naver\.com\/[\w-]+\/(\d{6,})/.exec(t);
  sig.net = 1;
  sig.u = m ? m[1] || m[2] : '';
  // 발행 뒤 화면이 곧 옮겨질 수 있어 (글마다 한 번 — 진행기가 사라짐) 그 자리에서 바로 알림
  if (sig.fin) sig.fin();
};
const arm = d => {
  const w = d.defaultView;
  if (!w || w.__apArm) return;
  w.__apArm = 1;
  try {
    const X = w.XMLHttpRequest.prototype, o = X.open, s = X.send;
    X.open = function (m, u) { this.__ap = [String(m), String(u)]; return o.apply(this, arguments); };
    X.send = function () {
      const x = this;
      if (x.__ap && /post/i.test(x.__ap[0])) x.addEventListener('load', () => { try { if (x.status === 200) good(x.__ap[1], typeof x.response === 'string' ? x.response : JSON.stringify(x.response)); } catch (e) {} });
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
};
const ttl = () => K.D.querySelector('.se-documentTitle .se-text-paragraph,.se-section-documentTitle .se-text-paragraph');
// ── 글 넣기 전 (CORE 가 부름 — k = 꾸미기의 도구들): 이어 쓰기 창 닫기 → 제목 → 커서를 본문으로 (v1 '글쓰기 한 번에'의 제목이 본문에 들어가던 일 — 넣은 뒤 제목 칸에 있는지 확인)
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
  const head = sp(it.title).slice(0, 12), has = () => { const t = ttl(); return !!t && sp(k.real(t)).includes(head); };
  const inBody = () => k.body_().some(p => sp(p.textContent).includes(head));
  const t = ttl();
  if (!t) { K.why = '제목 칸을 못 찾았어요'; return false; }
  if (k.real(t) || k.comps().some(c => k.real(c))) { K.why = '글쓰기 화면에 쓰던 글이 있어요 — 빈 글쓰기 화면에서 눌러 주세요'; return false; }
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
// ── 점검 (하나라도 있으면 발행하지 않음) — lite: 화면에서 다시 읽는 것 (고친 뒤에도 같은 점검) · chk: 꾸미기가 넣으며 적은 것 + lite · again: 발행창을 다시 채워 보고 + lite
const booked = () => { const r = K.any(['label,[role=radio]'], e => e.innerText.replace(/\s/g, '') === '예약'); return !!r && (r.control ? r.control.checked : r.getAttribute('aria-checked') === 'true'); };
const lite = (it, R) => {
  const P = [], T = ttl(), bt = sp(K.body_().map(p => p.textContent).join(' ')), src = new DOMParser().parseFromString(it.html, 'text/html').body;
  const ls = [...src.querySelectorAll('p')].map(p => sp(p.textContent.replace(/[⁣⁤​-‍﻿]/g, ''))).filter(t => t && !/^https?:/.test(t));
  const im = [...K.D.querySelectorAll('.se-component.se-image img')].filter(i => !/\/media\/blog_/.test(i.src)).length, need = Math.max(it.photos, R.pics[1]);
  if (!T || sp(K.real(T)) !== sp(it.title)) P.push('제목이 원고와 달라요');
  else if (!sp(src.textContent).includes(sp(it.title)) && bt.includes(sp(it.title))) P.push('제목이 본문에 들어갔어요');
  for (const l of [ls[0], ls[ls.length - 1]]) if (l && !K.count(l.slice(0, 20))) P.push('본문이 원고와 달라요 (' + l.slice(0, 12) + '…)');
  if (im < need) P.push('사진 ' + im + '/' + need + ' — 빠진 사진을 넣어 주세요');
  if (K.quotes().length < R.quotes[1]) P.push('포스트잇 ' + K.quotes().length + '/' + R.quotes[1]);
  if (K.cards() < R.cards[1]) P.push('링크 카드 ' + K.cards() + '/' + R.cards[1]);
  if (K.shape() !== R.want) P.push('글 차례가 원고와 달라요');
  if (!!it.disc !== DISC.test(bt)) P.push(it.disc ? '제휴 고지 줄이 빠졌어요' : '원고에 없는 제휴 고지 줄이 있어요');
  return P;
};
const when = (it, R, w) => it.at ? (!R.book ? '예약 시각이 글에 없어요' : w ? '예약 ' + (R.at || '') + ' 못 채움 (' + w + ')' : '') : booked() ? '발행창이 예약으로 되어 있어요 (지금 발행할 글)' : '';
const chk = (it, R) => {
  const P = [...new Set(R.bad)], w = when(it, R, R.why);
  if (!R.tagList) P.push('태그 줄이 없어 발행창을 못 채웠어요');
  else if (R.tags[0] < R.tags[1]) P.push('태그 ' + R.tags[0] + '/' + R.tags[1]);
  if (R.tagList && w) P.push(w);
  return [...P, ...lite(it, R)];
};
const again = async (it, R) => {
  const P = [];
  say('「' + it.title + '」 다시 점검하는 중');
  try {
    if (!R.tagList) P.push('태그 줄이 없어 발행창을 못 채웠어요');
    else {
      const [n, why] = await K.tagsIn(R.tagList), c = R.cat ? await K.catIn(R.cat) : '', w = when(it, R, it.at && R.book ? await K.bookIn(R.book) : '');
      if (n < R.tagList.length || why) P.push('태그 ' + n + '/' + R.tagList.length);
      if (c) P.push('카테고리 ' + c);
      if (w) P.push(w);
    }
    return [...P, ...lite(it, R)];
  } catch (e) { return ['다시 점검하지 못했어요 (' + e + ')']; }
};
// 마지막 발행 단추 (발행 설정창 안 — 창을 여는 위쪽 '발행' 단추가 아님)
const fin = () => K.any(["button[data-testid='seOnePublishBtn']", "button[class*='confirm_btn']"]) || K.any(['button'], e => !/publish_btn/.test(e.className) && !!e.closest("[class*='layer']") && /^(예약\s*)?발행$/.test(sp(e.innerText)));
// ── 글 하나 → {r: ok|unsure|wall|fail|login|dir, n: 한마디, u: 글 번호}
const one = async (it, at) => {
  const name = at + ' 「' + it.title + '」 ';
  sig = {};
  K = null;
  let E = DIR ? ed(root()) : null;
  if (!DIR) {
    say(name + '글쓰기 화면을 여는 중');
    E = await open(sbOn);
    if (!E && sbOn && !wall(root())) { sbOn = 0; E = await open(0); }
  }
  if (E === 'login') return {r: 'login'};
  const w0 = wall(root());
  if (w0) return {r: 'wall', n: w0};
  if (!E) return {r: DIR ? 'fail' : 'dir', n: '글쓰기 화면이 안 열려요'};
  for (const d of root()) arm(d);
  say(name + '넣는 중 — 1~2분 걸려요, 화면을 건드리지 마세요');
  const msgs = [];
  let R = null;
  try { await CORE(DIR ? D0 : fr.contentDocument, t => { msgs.push(String(t)); }, () => false, {h: it.html, t: it.text}, k => { R = k.R; return pre(it)(k); }); } catch (e) { msgs.push('오류: ' + e); }
  if (!R || !R.finished) return {r: wall(root()) ? 'wall' : 'fail', n: wall(root()) || (K && K.why) || sp(msgs[msgs.length - 1] || '글을 넣지 못했어요').slice(0, 70)};
  let P = chk(it, R), held = 0, t0 = 0, gone = 0;
  sig.fin = () => sig.sent = sig.sent || ask({a: 'done', b: BLOG, id: it.id, r: 'ok', u: sig.u || ''});
  for (;;) {
    if (!P.length) {
      // 발행을 누르기 전에 대시보드에 '해 봄'을 먼저 남김 — 발행 뒤 화면이 닫혀도 같은 글을 또 주지 않게 (두 번 발행 막기)
      const h0 = wall(root());
      if (h0) return {r: 'wall', n: h0};
      if (!held) { const h = await ask({a: 'done', b: BLOG, id: it.id, r: 'hold'}); if (!h || !h.ok) return {r: 'fail', n: (h && h.text) || '대시보드에 닿지 못했어요 (발행 전)'}; held = 1; }
      const b = fin();
      if (!b) P = ['마지막 발행 단추를 못 찾았어요'];
      else { say(name + (it.at ? '예약 ' + (R.at || '') : '지금') + ' 발행을 눌렀어요 — 확인하는 중'); t0 = Date.now(); K.press(b); }
    }
    if (P.length) {
      t0 = 0;
      await ask({a: 'done', b: BLOG, id: it.id, r: 'stop', n: P.join(' / ').slice(0, 300)});
      say(name + '멈췄어요 — 발행하지 않았어요. 아래를 고친 뒤 단추를 눌러 주세요: ' + P.join(' · '));
      ask1('고쳤어요 — 다시 점검하고 발행');
    }
    for (;;) {
      await S(500);
      const ds = root();
      for (const d of ds) arm(d);
      const m = ds.map(d => !WRITE.test(d.location.href) && PUB.exec(d.location.href)).find(Boolean), h = wall(ds);
      gone = ed(ds) ? 0 : gone + 1;
      if (sig.net || m || (t0 && (sig.err >= t0 || ds.some(d => OKAY.test(outer(d))) || (gone > 3 && ds.length && ds.every(d => d.readyState === 'complete'))))) { ask1(''); return {r: 'ok', u: sig.u || (m && (m[1] || m[2])) || ''}; }
      if (h) return {r: 'wall', n: h};
      if (sig.go) { ask1(''); P = await again(it, R); break; }
      if (!t0) { if (gone > 8) return {r: 'fail', n: '글쓰기 화면이 닫혔어요'}; continue; }
      const age = Date.now() - t0, pt = popup(ds);
      if (age > 4000 && !gone && pt) { P = ['네이버 안내: ' + pt.slice(0, 80)]; break; }
      if (age > 30000) return {r: 'unsure'};
    }
  }
};
// ── 차례로
const MARK = {ok: '✓ 올렸어요', unsure: '? 발행을 눌렀지만 확인 못 함 — 블로그에서 확인하고 할 일은 직접 체크', wall: '네이버 안내로 멈춤', fail: '못 넣음'};
let end = '', wk = '', okn = 0, chkn = 0, last = null;
for (;;) {
  const q = await ask({a: 'next', b: BLOG, w: wk});
  if (!q) { end = DOWN; break; }
  if (q.weekend && !wk) { say(q.text); ask1('주말에도 시작'); while (!sig.go) await S(300); ask1(''); wk = '1'; continue; }
  if (!q.ok || !q.next) { end = q.text || DOWN; break; }
  for (let s = q.wait; s > 0; s--) { say('다음 글 ' + q.n + '/' + q.m + ' — ' + s + '초 쉬어요'); await S(1000); }
  const it = q.next, res = await one(it, q.n + '/' + q.m);
  if (res.r === 'dir') {
    try { W.localStorage.setItem('apbd', '1'); } catch (e) {}
    say('이 브라우저에서는 스스로 이어가기가 안 돼요 — 화면을 다시 열게요. 열리면 책갈피를 한 번 더 (이제 글마다 한 번씩 눌러요)');
    await S(4000);
    W.location.reload();
    return;
  }
  if (res.r === 'login') { end = '네이버 로그인이 풀렸어요 — 로그인한 뒤 글쓰기 화면에서 다시 눌러 주세요'; break; }
  const d = await (sig.sent || ask({a: 'done', b: BLOG, id: it.id, r: res.r, n: res.n || '', u: res.u || ''}));
  if (d && d.checked) chkn++;
  line(MARK[res.r] + ' · 「' + it.title + '」' + (res.u ? ' · 글 번호 ' + res.u : '') + (res.n ? ' — ' + res.n : '') + (d && d.checked ? ' · 할 일 줄 체크 ✓' : res.r === 'ok' ? ' · 할 일 줄은 직접 체크' : ''));
  if (res.r === 'wall') { end = '네이버가 보호·제한 안내(' + res.n + ')를 보여 바로 멈췄어요 — 풀지 않아요. ' + ((d && d.text) || '이 블로그 자동 발행은 24시간 쉬어요'); break; }
  if (res.r === 'unsure') { end = '발행 확인을 못 해 멈췄어요 — 블로그에서 그 글이 올라갔는지 봐 주세요 (같은 글을 다시 넣지 않아요)'; break; }
  if (res.r === 'fail') { end = '글을 넣지 못해 멈췄어요 (' + (res.n || '') + ') — 발행된 것은 없어요'; break; }
  okn++;
  last = d;
  if (!d) { end = DOWN; break; }
  if (DIR) {
    // 글마다 한 번: 발행 뒤 네이버가 화면을 옮기면 진행기는 사라짐 — 남은 글이 있으면 새 글쓰기 화면을 스스로 열고 한 번 더 누르게
    if (!d.left) break;
    say('✓ 올렸어요 · 다음 글 ' + (q.n + 1) + '/' + q.m + ' — 글쓰기 화면이 열리면 책갈피 한 번 더');
    await S(5000);
    W.location.reload();
    return;
  }
}
if (fr && end.indexOf('멈췄어요') < 0) fr.remove();
say((end ? end + ' · ' : '끝났어요 · ') + '올린 글 ' + okn + ' · 할 일 체크 ' + chkn + (last && last.text ? ' · ' + last.text : '') + ' · v' + V);
}
