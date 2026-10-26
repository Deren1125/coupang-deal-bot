/* Sns-board X(트위터) 화면 (주인 10/25 — '대시보드 형식 보고 그에 맞게 X 화면').
   스레드·인스타 화면 위 'X →' 를 처음 누를 때만 threads.js 가 불러온다 (주소는 meta sb-x).
   데이터: 스레드 자동화 서비스(Blog-Auto threads/ xbot.py)의 /api/state 'x' 칸 — /dashboard/api/threads/state 를 같이 쓴다.
   바꾸기: /dashboard/api/threads/act 의 x_* 일 (threads_proxy.py OPS). 라이브러리 없음, 글자는 모두 textContent 로.
   계정 둘 (주인 10/25): state 'x' · 'x2'. 위 단추로 고른 계정만 그리고, 바꾸기에 acct 를 붙인다. */
'use strict';
(() => {
const D = document, W = window, P = '/dashboard/api/threads';
const CSS = '.xi{padding:10px 0;border-bottom:1px solid var(--line)}.xi:last-child{border:0}.xi a{font-weight:600;color:var(--text);text-decoration:none}' +
  '.xi q{display:block;quotes:none;font-size:14px;color:var(--sub);white-space:pre-wrap;margin:4px 0 6px;overflow-wrap:anywhere}' +
  '.xc{display:flex;gap:6px;margin:6px 0}.xc .tb{flex:1}' +
  '.tb.on{background:var(--gr-soft);color:var(--gr)}.x2{display:grid;grid-template-columns:1fr 1fr;gap:8px}.x2 .pb+.pb{margin-top:0}' +
  '.tf label.tg{display:flex;gap:8px}.tf .tg input{width:24px;height:24px;flex:none;padding:0}' +
  '.xi .say{font-size:15px;margin:0 0 6px;overflow-wrap:anywhere}a.tb{display:flex;align-items:center;justify-content:center;text-decoration:none}';
const N = {like: '♥ 좋아요', repost: '↻ 리포스트', reply: '💬 댓글'};
const A = {like: '♥ 좋아요', reply: '💬 댓글', quote: '❝ 인용'};  // 자동 숫자 칸 (리포스트는 2~3일에 1번이라 뺌)
const R = {done: '완료', already: '이미 함', no_button: '단추 못 찾음', no_menu: '메뉴 안 뜸', no_box: '글 칸 못 찾음', no_send: '보내기 안 눌림', unconfirmed: '확인 안 됨'};
let T = null, box = null, H = null, back = null, cur = 'x';
const E = {};  // 승인함 글마다 고친 댓글·고른 동작 (다시 그려도 남게)

function css() {
  if (D.getElementById('x-css')) return;
  const s = D.createElement('style'); s.id = 'x-css'; s.textContent = CSS; D.head.append(s);
}
async function load() {
  const r = await H.api(P + '/state');
  T = r.status === 200 ? r.data : {off: (r.data && r.data.text) || '스레드 서비스에 연결하지 못했어요'};
  draw();
}
async function act(op, args, why) {
  const r = await H.api(P + '/act', {method: 'POST', body: {op, args: Object.assign({acct: cur}, args)}});
  if (r.status !== 200) { H.toast((r.data && r.data.text) || '안 됐어요'); return null; }
  if (why) H.toast(why);
  await load();
  return r.data;
}
const card = (title, ...kids) => H.el('section', {class: 'itm'}, H.el('p', {class: 'h3', style: 'margin-top:0'}, title), ...kids);
const inp = (id, label, v, x) => H.el('label', {}, label, H.el('input', Object.assign({id, value: v == null ? '' : v}, x)));
const num = (id, label, v) => inp(id, label, v, {type: 'number', inputmode: 'numeric'});
const val = id => (D.getElementById(id) || {}).value || '';
const list = (xs, empty) => H.el('div', {class: 'tl'}, ...(xs.length ? xs : [H.el('p', {class: 'note'}, empty)]));
const res = r => !r ? '' : r.error ? r.error : Object.entries(r).map(([k, v]) => `${N[k] || A[k] || k} ${R[v] || v}`).join(' · ');

// 첫 화면은 간략하게 (주인 10/25 '대시보드는 무조건 간략해야 해'): 알림 한 줄 · 오늘 숫자 · 켜기/끄기. 기록·설정은 접어 둔다
function head(X) {
  const {el, btn} = H, s = X.settings, d = X.did_today || {};
  const top = el('div', {class: 'top'}, el('h2', {}, T.x2 ? 'X' : 'X' + (X.me ? ' @' + X.me : '')), btn('lnk', '스레드·인스타로', back));
  const pick = T.x2 ? el('div', {class: 'seg', role: 'group', 'aria-label': '계정', style: 'margin-bottom:10px'}, [['x', T.x], ['x2', T.x2]].map(([k, S], i) =>
    btn('', (S.me || S.settings.handle ? '@' + (S.me || S.settings.handle) : `계정 ${i + 1}`) + ((!S.settings.phone && S.login_needed) || S.paused_until ? ' ⚠' : ''), () => { cur = k; draw(); },
      {'aria-pressed': cur === k ? 'true' : 'false'}))) : null;
  const ph = !!s.phone;
  const w = !ph && X.login_needed ? ['X 로그인이 필요해요 — 채팅에서 ‘X 원격 로그인 열어 줘’라고 해 주세요', 1]
    : X.paused_until ? [`X 가 잠시 막아서 ${X.paused_until}까지 쉬어요`, 1]
    : !s.on ? ['꺼져 있어요'] : ph && !(s.sources || []).length ? ['설정에 글을 읽어 올 계정을 넣어 주세요']
    : !ph && !s.keywords.length ? ['설정에 찾을 키워드를 넣어 주세요'] : null;
  const c = X.caps_today || s.caps;
  const tiles = (s.auto || ph) ? Object.keys(A).map(k => el('div', {}, el('b', {}, `${d[k] || 0}/${c[k]}`), el('span', {}, A[k])))
    : [el('div', {}, el('b', {}, `${X.sent_today}/${s.daily_max}`), el('span', {}, '보낸 글'))];
  const v = X.visits_today || [0, 0];
  const till = (s.auto_until || '').slice(5, 16).replace('T', ' ');
  const info = ph ? `폰에서 누르기 · 카드 ${(X.waiting || []).length}장${X.next_visit ? ' · 다음 준비 ' + X.next_visit : ''}`
    : till ? `서버 자동 · ${till}까지 · 접속 ${v[0]}/${v[1]}${X.next_visit ? ' · 다음 ' + X.next_visit : ''}`
    : s.auto ? `자동 · 접속 ${v[0]}/${v[1]}${X.next_visit ? ' · 다음 ' + X.next_visit : ''}` : '누른 글만 보내요';
  return [top, pick, w ? el('p', {class: 'bn' + (w[1] ? ' bad' : '')}, el('span', {}, w[0])) : null,
    card('오늘', el('div', {class: 'tq'}, tiles), el('p', {class: 'note'}, info),
      el('div', {class: ph ? '' : 'x2', style: 'margin-top:10px'}, btn('pb soft', s.on ? '끄기' : '켜기', () => act('x_settings', {on: !s.on}, s.on ? '껐어요' : '켰어요')),
        ph ? null : btn('pb soft', '폰 방식으로', () => act('x_settings', {trial_days: 0}, '폰 방식으로 바꿨어요'))))];  // 서버 자동 켜기 버튼은 주인 10/26 19:30 취소
}
// 승인함 (승인 방식) — 댓글 고치기 · 동작 고르기 · 보내기/건너뛰기
function inbox(X) {
  const {el, btn} = H, ws = X.waiting || [];
  for (const k of Object.keys(E)) if (!ws.some(x => x.id === k)) delete E[k];
  const rows = ws.map(x => {
    const e = E[x.id] = E[x.id] || {draft: x.draft, actions: Object.assign({}, x.actions)};
    const ta = el('textarea', {class: 'fl short', maxlength: '280', 'aria-label': '댓글', oninput: ev => { e.draft = ev.target.value; }}, e.draft);
    return el('div', {class: 'xi'}, el('a', {href: x.url, target: '_blank', rel: 'noopener noreferrer'}, '@' + x.author),
      el('q', {}, x.text.slice(0, 200)), ta,
      el('div', {class: 'xc'}, Object.keys(N).map(k => btn('tb' + (e.actions[k] ? ' on' : ''), N[k], ev => {
        e.actions[k] = !e.actions[k]; ev.currentTarget.classList.toggle('on', e.actions[k]);
      }))),
      el('div', {class: 'x2'}, btn('pb', '보내기', () => act('x_approve', {id: x.id, draft: e.draft, actions: e.actions}, '보낼게요')),
        btn('pb soft', '건너뛰기', () => act('x_skip', {id: x.id}))));
  });
  return card(`승인함 ${ws.length}`, ...(rows.length ? rows : [el('p', {class: 'note', style: 'margin-top:0'}, '기다리는 글이 없어요. 서버가 찾으면 여기에 떠요')]),
    rows.length > 1 ? btn('pb soft', `${rows.length}개 전부 보내기`, async () => {
      if (!confirm(`${rows.length}개를 고른 대로 전부 보낼까요?`)) return;
      for (const x of ws) { const e = E[x.id]; if (Object.values(e.actions).some(Boolean)) await act('x_approve', {id: x.id, draft: e.draft, actions: e.actions}); }
    }) : null);
}
// 폰에서 누르기 (주인 10/26): 서버는 X 에 안 들어감 — 버튼이 폰 X 앱을 연다 (댓글·인용은 초안이 들어간 글쓰기 창, 글자는 복사도 해 둠)
function phoneCards(X) {
  const {el, btn} = H, ws = X.waiting || [];
  const lnk = (label, href, copy) => el('a', {class: 'tb', href, target: '_blank', rel: 'noopener noreferrer',
    onclick: () => { if (copy && navigator.clipboard) navigator.clipboard.writeText(copy).catch(() => {}); }}, label);
  const rows = ws.map(x => {
    const L = x.links || {open: x.url};
    return el('div', {class: 'xi'}, el('a', {href: L.open, target: '_blank', rel: 'noopener noreferrer'}, '@' + x.author),
      el('span', {class: 'note'}, ` ♥ ${x.likes}`), el('q', {}, x.text.slice(0, 120)),
      x.draft ? el('p', {class: 'say'}, '💬 ' + x.draft) : null, x.quote ? el('p', {class: 'say'}, '❝ ' + x.quote) : null,
      el('div', {class: 'xc'}, lnk('♥ 글 열기', L.open), L.reply ? lnk('💬 댓글', L.reply, x.draft) : null, L.quote ? lnk('❝ 인용', L.quote, x.quote) : null),
      el('div', {class: 'x2'}, btn('pb', '✓ 했어요', () => act('x_done', {id: x.id})), btn('pb soft', '건너뛰기', () => act('x_skip', {id: x.id}))));
  });
  return card(`오늘 할 것 ${ws.length}`, el('p', {class: 'note', style: 'margin-top:0'}, '버튼을 누르면 X 앱에서 열려요. 앱에서 누른 뒤 ‘했어요’.'),
    ...(rows.length ? rows : [el('p', {class: 'note'}, '지금은 없어요. 하루 3~4번 새로 골라 둬요')]));
}
const OPEN = {};  // 접은 칸을 열어 둔 채로 다시 그리기
const fold = (key, title, ...kids) => H.el('details', {class: 'itm', open: !!OPEN[key], ontoggle: e => { OPEN[key] = e.target.open; }},
  H.el('summary', {class: 'h3', style: 'margin:0'}, title), ...kids);
function log(X) {
  const {el} = H;
  const rows = (X.done || []).slice(0, 10).map(x => el('p', {}, el('i', {}, `${(x.done_at || '').slice(5, 16).replace('T', ' ')}${x.status === 'sent' ? '' : ' · 실패 ' + res(x.result)}`),
    el('a', {href: x.url, target: '_blank', rel: 'noopener noreferrer'}, '@' + x.author), x.result && x.result.reply === 'done' && x.draft ? el('i', {}, '💬 ' + x.draft) : null));
  const al = (X.log || []).slice(0, 5).map(a => el('p', {}, el('i', {}, a.at), a.text));
  return fold('log', '기록', list(rows.concat(al), '아직 없어요'));
}
function settings(X) {
  const {el, btn} = H, s = X.settings, hc = X.hard_caps || {};
  let auto = s.auto;
  return fold('set', '설정', el('div', {class: 'tf', style: 'margin-top:8px'},
    el('div', {class: 'tc'}, Object.keys(A).map(k => num('xc-' + k, `${A[k]} 하루 (최대 ${hc[k]})`, s.caps[k]))),
    s.phone ? inp('x-src', '글을 읽어 올 계정 (쉼표로, @ 없이)', (s.sources || []).join(', ')) : inp('x-kw', '찾을 키워드 (쉼표로)', s.keywords.join(', ')),
    s.phone ? null : el('label', {class: 'tg'}, el('span', {}, '승인 없이 자동으로 보내기'), el('input', {type: 'checkbox', checked: !!s.auto, onchange: e => { auto = e.target.checked; }})),
    el('p', {class: 'note', style: 'margin:0'}, s.phone ? '서버는 X 에 들어가지 않아요. 하루 3~4번 공개 글을 읽어 카드로 둬요. 0이면 그 카드는 안 만들어요.'
      : '하루 3~4번만 접속해 나눠서 해요. 주말은 20% 더, 리포스트는 2~3일에 1번. 0이면 그 일은 안 해요.'),
    btn('pb', '저장', () => {
      if (auto && !s.auto && !confirm('승인 없이 서버가 바로 보내요. X 약관상 계정 제한 위험이 있어요. 켤까요?')) return;
      const caps = {}; for (const k of Object.keys(A)) caps[k] = +val('xc-' + k);
      act('x_settings', s.phone ? {caps, sources: val('x-src')} : {caps, keywords: val('x-kw'), auto}, '저장했어요');
    })));
}
function draw() {
  if (!box || !T) return;
  const {el} = H;
  if (T.off || !T.x) { box.replaceChildren(el('p', {class: 'empty'}, T.off || 'X 기능이 아직 서버에 없어요'), H.btn('pb soft', '다시 불러오기', load)); return; }
  const X = T[cur] || T.x, typing = D.activeElement && box.contains(D.activeElement) && /^(TEXTAREA|INPUT)$/.test(D.activeElement.tagName);
  if (typing) return;  // 댓글·설정을 고치는 중에는 다시 그리지 않음
  box.replaceChildren(...[...head(X), X.settings.phone ? phoneCards(X) : X.settings.auto ? null : inbox(X), log(X), settings(X)].filter(Boolean));
}
function view(b, h, goBack) {
  css(); box = b; H = h; back = goBack;
  b.replaceChildren(h.el('p', {class: 'boot'}, '불러오는 중…'));
  load();
}
W.SBX = {view};
})();
