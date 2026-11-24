/* '하루치 한 번에 (발행까지)' 진행기 v6 (v6: 11/24 주인 — 사진은 원고의 90% 넘고 2장까지 빠지면, 태그는 90% 넘거나 1개만 빠지면 발행하고 요약에 적음 (다시 점검도 같게) · 원고보다 사진이 많으면(같은 사진 겹침) 멈춤 · 가운데 블로그(드라마·데런픽)는 원고에서 가운데인 글 줄이 화면에서도 가운데인지 봄 (2줄 넘게 어긋나면 멈추고 '전체 선택 → 가운데 단추') · 꾸미기 v11: 사진이 30초 안에 다 안 올라가도 주소로 한 번 더 넣지 않음 (같은 사진 두 장의 원인) · 태그는 네이버처럼 글자·숫자만 남겨 넣고 비교·한 번 더 넣어 봄 · v5: 11/23 13시 아이패드 v4 — 제목·본문·발행창은 됐는데 '포스트잇 글이 안 들어감': 주인 손이 본문에 남은 채(꾸미기는 붙여넣기 칸으로 손이 빠진 뒤) 붙여넣어 꾸미기 v7 의 포스트잇 칸 고르기가 안 먹음 → 본문을 누른 뒤 꾸미기처럼 편집 칸 밖 칸으로 포커스를 뺐다가 시작 · 포스트잇마다 화면에서 다시 읽어 빠진 글을 적고 「포스트잇 글 복사」 · 본문을 눌렀는데 첫 조각이 안 들어가면(13:40 3/4) 본문이 빈 때만 '한 번 더 눌러 주세요' · 예약 시각은 누른 때 기준 계획을 맨 위에 · v4: 11/23 12시 아이패드 v3 — 제목·본문 둘 다 빈 칸: 스크립트는 SE ONE 커서를 못 옮기고(새 창 포커스 → 본문) v3 는 본문에 든 제목을 DOM 에서 지워 에디터가 붙여넣기를 안 받음 → 글마다 주인이 제목 칸·본문을 한 번씩 누르면(진짜 누름) 그 자리에 붙여넣기 · 에디터 글을 스크립트로 지우지 않음 · v3: 11/23 11:36 아이패드 — v2 도 제목을 못 넣음: 아무것도 누르기 전에 처음 커서(제목)로 붙여넣기 · 늦게 그리는 제목을 기다려 두 번 읽음 · 끝내 안 되면 본문만 넣고 발행 전에 멈춤 + '제목 복사' · v2: 제목이 본문에 들어가던 것 · 주인 11/23 — "책갈피 누르면 한 번에 글 여러 개를 넣고, 발행 누르는 것까지"). autopub.py 가 줄여 책갈피 주소에 통째로 넣는다
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
const V = 6;
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
const TAG = '하루치 한 번에 · ', BK = '발행창이 예약으로 되어 있어요 (지금 발행할 글)', NT = '태그 줄이 없어 발행창을 못 채웠어요';
const sp = t => (t || '').replace(/\s+/g, ' ').trim(), QA = (e, q) => [...e.querySelectorAll(q)], mk = t => D0.createElement(t);
let fr = null, K = null, sig = {}, sbOn = 1, DIR = 0;
try { DIR = W.localStorage.getItem('apbd') === '1'; } catch (e) {}
// 창과 그 안쪽 창들의 문서 (같은 출처만 — 못 읽는 창은 건너뜀)
const docs = (w, o = []) => {
  try { const d = w.document; if (d && d.body) { o.push(d); for (const f of QA(d, 'iframe,frame')) docs(f.contentWindow, o); } } catch (e) {}
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
const ui = mk('div'), bar = mk('div'), list = mk('div'), btn = mk('button'), cp = mk('button');
ui.id = 'apb';
bar.style.cssText = 'padding:10px 14px;background:#111;color:#fff;font:600 15px/1.4 system-ui';
list.style.cssText = 'max-height:30vh;overflow:auto;padding:0 14px;background:#f4f5f7;color:#111;font:14px/1.6 system-ui';
btn.type = 'button';
btn.style.cssText = 'display:none;width:100%;min-height:48px;border:0;background:#03a94f;color:#fff;font:600 16px system-ui';
cp.type = 'button';
cp.style.cssText = btn.style.cssText.replace('#03a94f', '#1b64da');
ui.append(bar, list, btn, cp);
// v5: 맨 위에 그 블로그 예약 계획 '오늘 예약 14:20 · 16:10' (대시보드가 누른 때 기준으로 나눔)
let PL = '';
const say = t => { bar.textContent = TAG + (PL ? PL + ' · ' : '') + t; };
const line = t => { const p = mk('p'); p.style.margin = '8px 0'; p.textContent = t; list.append(p); };
// 단추 하나 (고쳤어요 · 주말에도 시작) — 책갈피를 한 번 더 눌러도 같은 뜻 (W.__apb)
const ask1 = t => { btn.textContent = t; btn.style.display = t ? 'block' : 'none'; sig.go = 0; };
btn.onclick = W.__apb = () => { sig.go = 1; };
// 제목이 안 들어갈 때 '제목 복사' (주인이 제목 칸을 길게 눌러 붙여넣음 — 누른 손으로만 복사됨)
const copy = (t, l) => { cp.textContent = l || '제목 복사'; cp.style.display = t ? 'block' : 'none'; cp.onclick = async () => {
  let ok = 0;
  try { await navigator.clipboard.writeText(t); ok = 1; } catch (e) {}
  cp.textContent = ok ? '복사했어요' : t;
}; };
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
const box = mk('div');
if (DIR) put(1);
else {
  const nb = mk('body');
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
  fr = mk('iframe');
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
    for (const c of QA(d, '.se-content,.se-documentTitle,#apb')) t = t.replace(c.innerText || '', '');
  } catch (e) {}
  return t.slice(0, 8000).toLowerCase();
};
// 자동입력 방지·보호조치·이용 제한·한도 안내인가 → 그 낱말 | ''
const wall = ds => {
  for (const d of ds) {
    const t = outer(d), h = WALL.find(w => t.includes(w));
    if (h) return h;
    try { if (QA(d, 'iframe[src*="captcha" i],img[src*="captcha" i],[id*="captcha" i],[class*="captcha" i]').some(seen)) return 'captcha'; } catch (e) {}
  }
  return '';
};
// 네이버 안내 창 (제목 없음 등)의 글 | ''
const popup = ds => {
  for (const d of ds) for (const e of QA(d, '.se-popup-alert,.se-popup-alert-confirm,[class*="layer_alert"],[role="alertdialog"]')) if (seen(e)) return sp(e.innerText) || '안내 창';
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
// 제목 칸 (v2: .se-title-text · 안내 글 '제목' 으로도 찾음)
const TTL = '.se-documentTitle,.se-section-documentTitle,.se-title-text';
const TP = '.se-text-paragraph', ttl = () => { const d = K.D, t = d.querySelector(TTL.replace(/,/g, ' ' + TP + ',') + ' ' + TP + ',.se-title-text'), h = t || QA(d, '[class*=placeholder]').find(e => sp(e.textContent) === '제목'); return h && (t || h.closest('p') || h.parentElement); };
// v3: 제목 칸의 글 = 제목 칸 안 글 줄을 모두 (SE ONE 은 넣은 글을 span 안에 나중에 다시 그림 — 늘 새로 찾아 읽음)
const tt = () => { const t = ttl(), c = t && (t.closest(TTL) || t), q = c ? QA(c, TP) : []; return t ? sp((q.length ? q : [t]).map(K.real).join(' ')) : ''; };
// 가운데 글인가 (사진 줄 빼고 가운데 글 줄이 반 넘게)
const MID = it => (it.html.match(/<p style="text-align:center">(?!<img)/g) || []).length * 2 > (it.html.match(/<p[ >]/g) || []).length;
const TCP = '제목 칸이 비었어요 — 「제목 복사」 → 제목 칸을 길게 눌러 붙여넣은 뒤 초록 단추 (또는 책갈피 한 번 더)';
// v4 (주인 11/23 12시 아이패드 v3 — 제목·본문 둘 다 빈 칸): 진짜 기기에서 SE ONE 커서는 사람의 진짜 누름으로만 옮겨짐 (꾸미기 10/29 — 꾸미기는 주인이 본문을 누른 뒤 돎).
// 새로 연 글쓰기 창에는 누른 적이 없어 스크립트 포커스는 본문으로 가고, v3 는 본문에 든 제목을 DOM 에서 직접 지워 에디터가 그 뒤 붙여넣기를 받지 않음.
// 그래서 주인이 제목 칸 → 본문을 한 번씩 누를 때(isTrusted)까지 기다렸다가 그 자리에 붙여넣기 신호만 보냄 · 에디터 글을 스크립트로 지우거나 고치지 않음.
const where = e => e && e.closest ? (e.closest(TTL) ? 1 : e.closest('.se-content') ? 2 : 0) : 0;
// 에디터 선택이 있는 곳 (1 제목 · 2 본문 · 0 모름)
const sel = () => { const a = K.D.getSelection().anchorNode; return a ? where(a.nodeType === 1 ? a : a.parentElement) : 0; };
const hand = (w, ok) => new Promise(res => {
  const D = K.D, ev = ['pointerup', 'touchend', 'mouseup'], t0 = Date.now(), f = e => { if (e.isTrusted && where(e.target) === w) end(1); }, end = v => { clearInterval(t); for (const y of ev) D.removeEventListener(y, f, true); res(v); };
  for (const y of ev) D.addEventListener(y, f, true);
  const t = setInterval(() => { if (ok && ok()) end(2); else if (Date.now() - t0 > 6e5) end(0); }, 300);
});
const NOHAND = '10분 동안 누르지 않아 멈췄어요 — 책갈피를 다시 눌러 주세요';
// v5 (11/23 13:40 아이패드 v4 3/4 — 본문을 눌렀는데 첫 조각이 안 들어감 · 커서는 빈 본문 안내 글 앞): 누른 뒤 에디터 선택이 본문 안에 올 때까지(1.5초까지) + 0.6초 기다림
// away: 꾸미기는 주인이 본문을 누른 뒤 붙여넣기 칸(편집 칸 밖)으로 손이 빠졌다가 시작 — 그래야 포스트잇 칸 고르기(꾸미기 v7)가 먹음 (v4 는 손이 본문에 남아 표 포스트잇 글이 밖으로) → 같은 차례로
const settle = async (name, away) => {
  const D = K.D;
  await K.wait(() => sel() === 2, 1500);
  await S(600);
  if (away) {
    const fb = D.createElement('div');
    fb.contentEditable = 'true';
    fb.style.cssText = 'position:fixed;left:0;top:0;width:2px;height:2px;opacity:.01';
    D.body.append(fb);
    fb.focus();
    await S(400);
    fb.remove();
    await S(300);
  }
  say(name + '넣는 중 — 1~2분 걸려요, 화면을 건드리지 마세요');
};
// v5: 첫 조각이 본문에 안 들어갔고 본문이 아직 비었을 때만 (지우지 않음 · 두 번 넣지 않음) 주인에게 본문을 한 번 더 눌러 달라고 해 다시 (2번까지 · 첫 번은 손 빼기 그대로, 둘째는 손 빼기 없이)
const again1 = name => { let n = 0; return async () => {
  if (K.comps().some(c => K.real(c)) || ++n > 2) return false;
  say(name + '본문에 글이 안 들어갔어요 — 본문을 한 번 더 눌러 주세요 (아무것도 지우지 않았어요)');
  if (!await hand(2)) return false;
  await settle(name, n < 2);
  return true;
}; };
// ── 글 넣기 전 (CORE 가 부름 — k = 꾸미기의 도구들): 이어 쓰기 창 닫기 → 주인이 제목 칸을 누름 → 제목 → 주인이 본문을 누름 → (꾸미기가 본문)
const pre = (it, name) => async k => {
  K = k;
  await k.wait(() => k.vis(['.se-popup']), 1500);
  for (let n = 0; n < 3; n++) {
    const pop = k.vis(['.se-popup-alert-confirm', '.se-popup-alert', '.se-popup']);
    if (!pop || !/작성\s?중인\s?글|이어서\s?작성|임시저장된\s?글/.test(pop.innerText)) break;
    const c = QA(pop, 'button').find(b => /cancel/i.test(b.className) || sp(b.innerText) === '취소');
    if (!c || /발행|확인|등록/.test(c.innerText)) break;
    k.press(c);
    await S(900);
  }
  const hp = k.vis(['.se-help-panel-close-button', 'button.se-help-close']);
  if (hp) { k.press(hp); await S(400); }
  const T0 = sp(it.title), W_ = k.W, bs = () => k.body_().map(k.real).filter(Boolean).join(' '), b0 = bs(), body0 = !k.comps().some(c => k.real(c));
  if (!ttl()) { K.why = '제목 칸을 못 찾았어요'; return false; }
  // 다시 누름: 주인이 제목만 넣어 둔 빈 글이면 그대로 · v5: 본문에 제목만 든 화면(제목이 본문으로 감)도 같은 화면에서 이어 감
  if (!(tt() === T0 && body0)) {
    if (tt() || !body0 && b0 !== sp(T0)) { K.why = '글쓰기 화면에 쓰던 글이 있어요 — 빈 글쓰기 화면에서 눌러 주세요'; return false; }
    // v5 (주인 11/23 13:58 — 본문을 눌렀는데 제목이 본문으로 · '창을 닫지 않고 제목 칸을 누르면 이어 가게'): 누른 곳이 제목 칸이고 에디터 선택이 본문이 아닐 때만 붙여넣음 ·
    // 본문에 들어가면 지우지 않고(스크립트로 지우면 에디터가 망가짐 — v3) 주인이 지울 때까지 기다렸다가 ① 부터 다시
    for (let n = 0; n < 5; n++) {
      if (bs()) { say(name + '본문에 들어간 제목 줄을 지운 뒤(길게 눌러 선택 → 삭제) 제목 칸을 눌러 주세요 — 안 되면 이 창을 닫고 새로'); if (!await hand(-1, () => !bs())) { K.why = NOHAND; return false; } await S(800); }
      if (tt()) break;
      say(name + (n ? '제목 칸(위 큰 글씨 칸)을 눌러 주세요' : '① 제목 칸을 한 번 눌러 주세요'));
      const h = await hand(1, tt);
      if (!h) { K.why = NOHAND; return false; }
      if (h === 1) {
        // 손을 뗀 뒤 에디터가 커서를 둘 때까지 기다림 · 선택이 본문이면 붙이지 않고 다시
        await k.wait(sel, 1500);
        await S(500);
        if (sel() === 2) continue;
        const dt = new W_.DataTransfer();
        dt.setData('text/plain', it.title);
        (k.host() || ttl()).dispatchEvent(new W_.ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt}));
        await k.wait(() => tt() || bs(), 3000);
        await S(800);
      }
      if (!bs()) break;
    }
    if (!tt()) {
      copy(it.title);
      say(name + '제목이 안 들어갔어요 — 「제목 복사」 → 제목 칸을 길게 눌러 붙여넣기 (그대로 기다려요)');
      const h2 = await hand(-1, tt);
      copy('');
      if (!h2) { K.why = NOHAND; return false; }
    }
    await S(1000);
    if (tt() !== T0) { K.why = '제목 칸이 원고와 달라요'; return false; }
    // v6: 가운데 글이면 커서가 제목 칸에 있는 지금 도구줄 가운데 단추 (주인이 누른 자리라 먹음)
    if (MID(it)) await k.titleMid();
  }
  // 본문: 꾸미기와 같게 주인이 본문을 한 번 누른 뒤 · v5: 선택이 제목 칸이면 다시 눌러 달라고 함
  for (let n = 0; n < 3; n++) {
    say(name + (n ? '본문(아래 넓은 칸)을 눌러 주세요' : '② 본문을 한 번 눌러 주세요 — 그다음은 손대지 마세요 (1~2분)'));
    if (!await hand(2)) { K.why = NOHAND; return false; }
    await k.wait(sel, 1500);
    if (sel() !== 1) break;
  }
  // v6: 가운데 글이면 주인이 누른 빈 본문 줄에서 도구줄 가운데 단추 — 붙여넣는 글이 가운데를 이어받음 (붙여넣기가 정렬을 잃어도)
  const cb = MID(it) && k.vis(k.MAIN_CENTER);
  if (cb) { k.press(cb); await S(400); }
  await settle(name, 1);
  return true;
};
// ── 점검 (하나라도 있으면 발행하지 않음) — lite: 화면에서 다시 읽는 것 (고친 뒤에도 같은 점검) · chk: 꾸미기가 넣으며 적은 것 + lite · again: 발행창을 다시 채워 보고 + lite
const booked = () => { const r = K.any(['label,[role=radio]'], e => e.innerText.replace(/\s/g, '') === '예약'); return !!r && (r.control ? r.control.checked : r.getAttribute('aria-checked') === 'true'); };
// v5: 포스트잇 글 — 원고 인용구마다 첫 줄이 화면의 포스트잇 안에 있는지 · 밖(본문)에 더 있는지 (빈칸·안 보이는 글자 빼고)
const tz = t => (t || '').replace(/[\s\u200b-\u200d\u2060-\u2064\ufeff]/g, ''), nn = (a, b) => a.split(b).length - 1;
let miss = '', nt = [];
const pits = (src, P) => {
  const bq = QA(src, 'blockquote'), qi = tz(K.quotes().map(q => q.textContent).join('|')), al = tz(K.D.querySelector('.se-content').textContent), st = tz(src.textContent), sq = tz(bq.map(q => q.textContent).join('|'));
  miss = '';
  bq.forEach((q, i) => {
    const l = QA(q, 'p').map(p => sp(p.textContent)).filter(Boolean), x = l[0] || sp(q.textContent), p = tz(x).slice(0, 16), out = nn(al, p) - nn(qi, p) > nn(st, p) - nn(sq, p), h = '포스트잇 ' + (i + 1) + ' 「' + x.slice(0, 14) + '」 ';
    if (!p) return;
    if (!qi.includes(p)) { P.push(h + (out ? '글이 포스트잇 밖(아래 줄)에 들어갔어요 — 그 줄을 지우고 ' : '글이 안 들어갔어요 — ') + '「포스트잇 글 복사」 → 빈 포스트잇 안을 눌러 붙여넣기'); miss = miss || (l.length ? l.join('\n') : x); }
    else if (out) P.push(h + '글이 포스트잇 밖에도 있어요 — 밖의 줄을 지워 주세요');
  });
};
const lite = (it, R) => {
  const P = [], T = tt(), bt = sp(K.body_().map(p => p.textContent).join(' ')), src = new DOMParser().parseFromString(it.html, 'text/html').body;
  const ls = QA(src, 'p').map(p => sp(p.textContent.replace(/[⁣⁤​-‍﻿]/g, ''))).filter(t => t && !/^https?:/.test(t));
  const im = QA(K.D, '.se-component.se-image img').filter(i => !/\/media\/blog_/.test(i.src)).length, need = Math.max(it.photos, R.pics[1]);
  if (T !== sp(it.title)) P.push(T ? '제목이 원고와 달라요' : TCP);
  else if (!sp(src.textContent).includes(sp(it.title)) && bt.includes(sp(it.title))) P.push('제목이 본문에 들어갔어요');
  for (const l of [ls[0], ls[ls.length - 1]]) if (l && !K.count(l.slice(0, 20))) P.push('본문이 원고와 달라요 (' + l.slice(0, 12) + '…)');
  // v6 (주인 11/24): 사진은 원고의 90% 넘고 2장까지 빠지면 발행 (요약에 적음) · 원고보다 많으면(같은 사진 겹침) 멈춤
  if (im < need) { if (need - im > 2 || im < need * .9) P.push('사진 ' + im + '/' + need + ' — 빠진 사진을 넣어 주세요'); else nt.push('사진 ' + (need - im) + '장 빠짐'); }
  if (K.nImg() > need) P.push('사진 ' + (K.nImg() - need) + '장 더 — 겹친 사진을 지워 주세요');
  // v6 (주인 11/24 '드라마·데런픽 가운데 정렬이 제대로 안 됨'): 원고에서 가운데인 글 줄이 화면에서 가운데가 아니면 (포스트잇·빈 줄 빼고) — 1줄은 넘어감
  const off = QA(src, 'p[style*=center]').filter(p => (x => x.length > 3 && !p.closest('blockquote') && K.body_().some(q => tz(q.textContent).startsWith(x) && !K.centered(q)))(tz(p.textContent).slice(0, 12))).length;
  if (MID(it) && !K.centered(ttl())) nt.push('제목 가운데 아님');
  if (off) (off > 1 ? P : nt).push('가운데 아닌 줄 ' + off + (off > 1 ? ' — 본문 「전체 선택」 → 위 가운데 단추' : ''));
  if (K.quotes().length < R.quotes[1]) P.push('포스트잇 ' + K.quotes().length + '/' + R.quotes[1]);
  else pits(src, P);
  if (K.cards() < R.cards[1]) P.push('링크 카드 ' + K.cards() + '/' + R.cards[1]);
  if (K.shape() !== R.want) P.push('글 차례가 원고와 달라요');
  if (!!it.disc !== DISC.test(bt)) P.push(it.disc ? '제휴 고지 줄이 빠졌어요' : '원고에 없는 제휴 고지 줄이 있어요');
  return P;
};
const when = (it, R, w) => it.at ? (!R.book ? '예약 시각이 글에 없어요' : w ? '예약 ' + (R.at || '') + ' 못 채움 (' + w + ')' + (/날짜/.test(w) ? ' — 발행창 예약에서 그 날짜를 직접 고른 뒤 초록 단추 (시·분은 다시 채워요)' : '') : '') : booked() ? BK : '';
// v6 (주인 11/24): 태그는 90% 넘거나 1개만 빠지면 발행 (빠진 태그 이름을 요약에)
const tg = (R, n, P) => { const m = R.tagList.length; if (n < m) (m - n > 1 && n < m * .9 ? P : nt).push('태그 ' + n + '/' + m + ' 빠짐 ' + (R.tagMiss || []).join(',')); };
const chk = (it, R) => {
  nt = [];
  const P = [...new Set(R.bad)].filter(x => x !== '포스트잇 글이 안 들어감'), w = when(it, R, R.why);
  if (!R.tagList) P.push(NT);
  else tg(R, R.tags[0], P);
  if (R.tagList && w) P.push(w);
  return [...P, ...lite(it, R)];
};
const again = async (it, R) => {
  const P = [];
  nt = [];
  say('「' + it.title + '」 다시 점검하는 중');
  try {
    if (!R.tagList) P.push(NT);
    else {
      const [n, why] = await K.tagsIn(R.tagList), c = R.cat ? await K.catIn(R.cat) : '', w = when(it, R, it.at && R.book ? await K.bookIn(R.book) : '');
      tg(R, n, P);
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
  const msgs = [];
  let R = null;
  try { await CORE(DIR ? D0 : fr.contentDocument, t => { msgs.push(String(t)); }, () => false, {h: it.html, t: it.text, re: again1(name)}, k => { R = k.R; return pre(it, name)(k); }); } catch (e) { msgs.push('오류: ' + e); }
  if (!R || !R.finished) return {r: wall(root()) ? 'wall' : 'fail', n: wall(root()) || (K && K.why) || sp(msgs[msgs.length - 1] || '글을 넣지 못했어요').slice(0, 70)};
  let P = chk(it, R), held = 0, t0 = 0, gone = 0;
  sig.fin = () => sig.sent = sig.sent || ask({a: 'done', b: BLOG, id: it.id, r: 'ok', u: sig.u || '', n: nt.join(' · ')});
  for (;;) {
    if (!P.length) {
      // 발행을 누르기 전에 대시보드에 '해 봄'을 먼저 남김 — 발행 뒤 화면이 닫혀도 같은 글을 또 주지 않게 (두 번 발행 막기)
      const h0 = wall(root());
      if (h0) return {r: 'wall', n: h0};
      // v3: 발행 바로 전에 제목 칸을 한 번 더 읽음 — 원고 제목과 다르면 절대 누르지 않음
      await S(300);
      if (tt() !== sp(it.title)) P = [tt() ? '제목이 원고와 달라요' : TCP];
      // v5 (주인 11/23 14:22 — 예약을 못 채운 글이 '현재'로 발행 중): 발행 바로 전에 발행창 '예약'/'현재'를 한 번 더 읽어 계획과 다르면 절대 안 누름
      else if (!!it.at !== booked()) P = [it.at ? '발행창이 현재로 되어 있어요 — 예약 ' + (R.at || '') + ' 을 고른 뒤 초록 단추' : BK];
      else if (!held) { const h = await ask({a: 'done', b: BLOG, id: it.id, r: 'hold'}); if (!h || !h.ok) return {r: 'fail', n: (h && h.text) || '대시보드에 닿지 못했어요 (발행 전)'}; held = 1; }
      const b = fin();
      if (!P.length) if (!b) P = ['마지막 발행 단추를 못 찾았어요'];
      else { say(name + (it.at ? '예약 ' + (R.at || '') : '지금') + ' 발행을 눌렀어요 — 확인하는 중'); t0 = Date.now(); K.press(b); }
    }
    if (P.length) {
      t0 = 0;
      await ask({a: 'done', b: BLOG, id: it.id, r: 'stop', n: P.join(' / ').slice(0, 300)});
      say(name + '멈췄어요 — 발행하지 않았어요. 아래를 고친 뒤 단추를 눌러 주세요: ' + P.join(' · '));
      ask1('고쳤어요 — 다시 점검하고 발행');
      if (tt() !== sp(it.title)) copy(it.title); else copy(miss, '포스트잇 글 복사');
    }
    for (;;) {
      await S(500);
      const ds = root();
      for (const d of ds) arm(d);
      const m = ds.map(d => !WRITE.test(d.location.href) && PUB.exec(d.location.href)).find(Boolean), h = wall(ds);
      gone = ed(ds) ? 0 : gone + 1;
      if (sig.net || m || (t0 && (sig.err >= t0 || ds.some(d => OKAY.test(outer(d))) || (gone > 3 && ds.length && ds.every(d => d.readyState === 'complete'))))) { ask1(''); return {r: 'ok', u: sig.u || (m && (m[1] || m[2])) || '', n: nt.join(' · ')}; }
      if (h) return {r: 'wall', n: h};
      if (sig.go) { ask1(''); copy(''); P = await again(it, R); break; }
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
  PL = q.plan || '';
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
PL = '';
say((end ? end + ' · ' : '끝났어요 · ') + '올린 글 ' + okn + ' · 할 일 체크 ' + chkn + (last && last.text ? ' · ' + last.text : '') + ' · v' + V);
}
