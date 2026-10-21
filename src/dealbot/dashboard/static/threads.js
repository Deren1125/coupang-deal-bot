/* Sns-board 스레드 화면 (주인 10/21 — "sns-board 에서 스레드 페이지를 아예 만들면 되겠네"). 블로그 탭 '스레드 →' 를 처음 열 때만 app.js 가 불러온다.
   위: 스레드 자동화 서비스(Blog-Auto threads/ — 맞팔·좋아요·AI 댓글·답글·AI 글) 상태와 설정 — /dashboard/api/threads/* (threads_proxy.py).
   아래 세 칸은 개발 세션 자리 (주인이 직접 쓰는 글·예약 · 댓글 알림 · 통계 — sns-board state 의 thpost 칸):
   SBThreads.slots.compose / alerts / stats = (thpost, h) => 노드 배열 을 붙이면 그 칸에 그린다 (없으면 '준비 중').
   라이브러리 없음. 비밀값(스레드 토큰·비밀번호)은 넣고 바로 지우고 화면에 다시 보이지 않는다. */
'use strict';
(() => {
const D = document, W = window, P = '/dashboard/api/threads';
const CSS = '.tq{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:6px;margin:0 0 6px}' +
  '.tq div{background:var(--grey);border-radius:10px;padding:8px 10px}.tq b{display:block;font-size:20px;font-variant-numeric:tabular-nums}' +
  '.tq span{font-size:12px;color:var(--sub)}.tf{display:grid;gap:8px}.tf label{display:grid;gap:4px;font-size:13px;color:var(--sub)}' +
  '.tf input{font:inherit;font-size:16px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);min-width:0}' +
  '.tc{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:8px}.tg{display:flex;justify-content:space-between;align-items:center;' +
  'min-height:44px;border-bottom:1px solid var(--line)}.tg:last-child{border:0}.tg input{width:24px;height:24px}' +
  '.tl p{margin:0;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}.tl p:last-child{border:0}.tl i{display:block;font-style:normal;color:var(--sub)}' +
  '.ts{max-width:100%;border-radius:10px;border:1px solid var(--line);margin-top:8px}';
const MODE = [['off', '꺼짐'], ['dry', '시험'], ['live', '실제']];
const LOGIN = {busy: '블로그 봇이 크롬을 쓰는 중이에요 — 1~2분 뒤 다시 눌러 주세요', ok: '로그인됐어요', code: '인증 코드를 넣어 주세요 (문자·메일·인증 앱)', no: '로그인 안 된 상태예요',
  check: '화면 확인이 필요해요 — 아래 사진을 보고, 인스타그램 앱에 “본인 맞나요?” 알림이 왔으면 승인한 뒤 ‘로그인 확인’을 눌러 주세요'};
const slots = {};
let T = null, box = null, H = null, code = false;

function css() {
  if (D.getElementById('th-css')) return;
  const s = D.createElement('style'); s.id = 'th-css'; s.textContent = CSS; D.head.append(s);
}
async function load() {
  const r = await H.api(P + '/state');
  T = r.status === 200 ? r.data : {off: (r.data && r.data.text) || '스레드 서비스에 연결하지 못했어요'};
  draw();
}
async function act(op, args, why) {
  const r = await H.api(P + '/act', {method: 'POST', body: {op, args: args || {}}});
  if (r.status !== 200) { H.toast((r.data && r.data.text) || '안 됐어요'); return null; }
  if (why) H.toast(why);
  await load();
  return r.data;
}
const card = (title, ...kids) => H.el('section', {class: 'itm'}, H.el('p', {class: 'h3', style: 'margin-top:0'}, title), ...kids);
const inp = (id, label, v, x) => H.el('label', {}, label, H.el('input', Object.assign({id, value: v == null ? '' : v}, x)));
const val = id => (D.getElementById(id) || {}).value || '';

function head() {
  const {el, btn} = H, s = T.settings, K = T.kind_names || {};
  const top = [el('div', {class: 'top'}, el('h2', {}, '스레드 @' + T.username), el('a', {class: 'lnk', href: '#/blog'}, '블로그로'))];
  const warn = [];
  if (T.login_needed) warn.push('스레드 로그인이 풀렸어요 — 아래 ‘스레드 로그인’에서 다시 로그인해 주세요');
  if (T.paused_until) warn.push(`${T.paused_until}까지 쉬는 중이에요`);
  if (T.low_mem) warn.push(`서버 메모리가 모자라 잠깐 쉬어요 (남은 ${T.low_mem.free_mb}MB) — 블로그 봇이 먼저예요`);
  if (s.mode === 'dry') warn.push('시험 모드 — 화면만 열어 보고 아무것도 누르거나 올리지 않아요');
  const last = T.last_cycle ? `마지막 ${T.last_cycle.at}` : '아직 돌지 않았어요';
  return top.concat(warn.map(w => el('p', {class: 'bn' + (/풀렸|쉬는/.test(w) ? ' bad' : '')}, el('span', {}, w))),
    card('오늘', el('div', {class: 'tq'}, Object.entries(T.today).map(([k, v]) => el('div', {}, el('b', {}, `${v.done}/${v.cap}`), el('span', {}, K[k] || k)))),
      el('p', {class: 'note'}, `${T.busy ? '지금 하는 중 · ' : ''}${last}${T.next_run ? ' · 다음 ' + T.next_run : ''} · ${s.hours[0]}~${s.hours[1]}시`),
      el('div', {class: 'seg', role: 'group', 'aria-label': '모드', style: 'margin-top:10px'}, MODE.map(([m, l]) => btn('', l, () => {
        if (m === 'live' && !confirm('실제로 누르고 올리기 시작할까요?')) return;
        act('settings', {mode: m}, l + ' 모드로 바꿨어요');
      }, {'aria-pressed': s.mode === m ? 'true' : 'false'}))),
      el('div', {class: 'tc', style: 'margin-top:8px'}, btn('pb soft', '지금 한 번', () => act('run', {}, '곧 한 번 돌아요')),
        T.paused_until ? btn('pb soft', '다시 시작', () => act('resume', {}, '다시 시작해요')) : btn('pb soft', '오늘 멈추기', () => act('pause', {}, '내일 아침까지 쉬어요')))));
}
function jobs() {
  const {el} = H, s = T.settings;
  return card('할 일 켜고 끄기', ...Object.entries(T.job_names).map(([k, n]) => el('label', {class: 'tg'}, el('span', {}, n),
    el('input', {type: 'checkbox', checked: !!s.jobs[k], onchange: e => act('settings', {jobs: {[k]: e.target.checked}})}))));
}
function settings() {
  const {el, btn} = H, s = T.settings, K = T.kind_names || {};
  const caps = Object.keys(T.hard_caps).map(k => inp('thc-' + k, `${K[k] || k} (최대 ${T.hard_caps[k]})`, s.caps[k], {type: 'number', inputmode: 'numeric'}));
  return card('하루 상한 · 키워드 · AI', el('div', {class: 'tf'}, el('div', {class: 'tc'}, ...caps),
    inp('th-kw', '관심 키워드 (쉼표로)', s.keywords.join(', ')), inp('th-tone', 'AI 말투', s.tone), inp('th-top', 'AI 글 주제 (쉼표로)', s.topics.join(', ')),
    el('div', {class: 'tc'}, inp('th-h0', '활동 시작(시)', s.hours[0], {type: 'number'}), inp('th-h1', '활동 끝(시)', s.hours[1], {type: 'number'}),
      inp('th-p0', 'AI 글 시작(시)', s.post_hours[0], {type: 'number'}), inp('th-p1', 'AI 글 끝(시)', s.post_hours[1], {type: 'number'}),
      inp('th-mf', '팔로워 이보다 많으면 건너뜀', s.max_followers, {type: 'number'})),
    btn('pb', '저장', () => {
      const c = {}; for (const k of Object.keys(T.hard_caps)) c[k] = +val('thc-' + k);
      act('settings', {caps: c, keywords: val('th-kw'), tone: val('th-tone'), topics: val('th-top'), hours: [+val('th-h0'), +val('th-h1')],
        post_hours: [+val('th-p0'), +val('th-p1')], max_followers: +val('th-mf')}, '저장했어요');
    })));
}
function login() {
  const {el, btn} = H, pw = el('input', {type: 'password', placeholder: '인스타그램·스레드 비밀번호', autocomplete: 'off'});
  const cd = el('input', {inputmode: 'numeric', placeholder: '인증 코드 6자리', autocomplete: 'one-time-code'});
  const say = el('p', {class: 'note'}), go = async (op, args) => {
    say.textContent = '확인 중… (최대 2분)';
    const d = await act(op, args); pw.value = ''; cd.value = '';
    if (d) { code = d.result === 'code'; say.textContent = LOGIN[d.result] || d.result; draw(); }
  };
  const tok = el('input', {type: 'password', placeholder: 'Meta 에서 받은 스레드 장기 토큰', autocomplete: 'off'});
  return [card('스레드 로그인', el('div', {class: 'tf'}, pw, el('div', {class: 'tc'}, btn('pb', '로그인', () => pw.value && go('tlogin', {password: pw.value})),
      btn('pb soft', '로그인 확인', () => go('tcheck', {}))), code ? cd : null, code ? btn('pb', '코드 보내기', () => cd.value && go('tcode', {code: cd.value})) : null),
    say, el('p', {class: 'note'}, '비밀번호는 저장하지 않아요. 로그인 상태만 서버 브라우저에 남아요.'),
    T.shot ? el('img', {class: 'ts', src: P + '/shot.png?' + Date.now(), alt: '서버 브라우저 화면'}) : null),
  card('스레드 API 토큰', el('p', {class: 'note', style: 'margin-top:0'}, T.has_api ? '연결됨 — AI 글·답글을 쓸 수 있어요' : '아직 없음 — AI 글·답글은 토큰이 있어야 돌아요'),
    el('div', {class: 'tf', style: 'margin-top:8px'}, tok, btn('pb', '토큰 저장', async () => {
      const v = tok.value.trim(); tok.value = '';
      if (v) { const d = await act('token', {token: v}); if (d) H.toast('연결됐어요: @' + d.username); }
    })), el('p', {class: 'note'}, '채팅에 붙여 넣지 말고 여기에 넣어 주세요. 서버에만 저장되고 다시 보이지 않아요.'))];
}
function logs() {
  const {el} = H, K = T.kind_names || {};
  const al = (T.alerts || []).map(a => el('p', {}, el('i', {}, a.at), a.text));
  const ac = (T.actions || []).map(a => el('p', {}, el('i', {}, `${a.at.slice(5, 16).replace('T', ' ')} · ${K[a.kind] || a.kind}${a.dry ? ' (시험)' : ''}`),
    '@' + a.target, a.note ? el('i', {}, a.note) : null));
  return [card('알림', el('div', {class: 'tl'}, ...(al.length ? al : [el('p', {class: 'note'}, '없어요')]))),
    card('최근 기록', el('div', {class: 'tl'}, ...(ac.length ? ac : [el('p', {class: 'note'}, '없어요')])))];
}
// 개발 세션 자리 — thpost 칸이 오면 slots 에 붙인 그리기로
function slot(key, title) {
  const tp = H.S && H.S.thpost, f = slots[key];
  return card(title, ...(tp && f ? f(tp, H) : [H.el('p', {class: 'note', style: 'margin-top:0'}, '준비 중이에요')]));
}
function draw() {
  if (!box || !T) return;
  const {el} = H;
  if (T.off) { box.replaceChildren(el('p', {class: 'empty'}, T.off), H.btn('pb soft', '다시 불러오기', load)); return; }
  box.replaceChildren(...head(), slot('compose', '내가 쓰는 글 (바로 올리기·예약)'), slot('alerts', '댓글 알림'), slot('stats', '통계'),
    jobs(), settings(), ...login(), ...logs());
}
function view(b, h) {
  css(); box = b; H = h;
  b.replaceChildren(h.el('p', {class: 'boot'}, '불러오는 중…'));
  load();
}
W.SBThreads = {view, slots};
})();
