/* X(트위터) 화면 (주인 10/25 — 'X 는 유료 구독만, API 는 안 써'). SNS 탭 'X(트위터)' 칸을 처음 열 때만 app.js 가 불러와 '직접 올리는 글' 아래에 그린다 (meta sb-xpost — 스레드 화면의 X 봇 x.js 와는 다른 파일). 값은 state.x (xview.py).
   데런픽 초안함 맨 위(10/26 · Blog-Auto x_drafts — 복사 · 'X에 올리기' 링크 · 링크 답글 · 올렸어요 · 버리기 · 다시 쓰기) · 진행률(주인이 주마다 넣는 숫자 x.stats) · 수익 계정 2주 계획(/dashboard/api/xplan — 열 때만) · 수익 계정 글감 카드.
   'X에 올리기'는 X 글쓰기 화면을 여는 링크일 뿐 (주소는 state.x.intent) — 자동 게시·자동 좋아요·답글 없음. 라이브러리 없음. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.xpw{display:grid;gap:10px}.xpw .itm{padding:14px}.xpb{height:10px;border-radius:5px;background:var(--grey);overflow:hidden;margin:4px 0 10px}' +
  '.xpb i{display:block;height:100%;background:var(--gr);border-radius:5px}.xpk{display:flex;justify-content:space-between;gap:8px;font-size:14px}' +
  '.xpk b{font-variant-numeric:tabular-nums}.xpf{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:8px;margin-top:8px}' +
  '.xpf label{display:grid;gap:4px;font-size:12px;color:var(--sub)}.xpw input,.xpw textarea{font:inherit;font-size:16px;padding:9px;border:1px solid var(--line);' +
  'border-radius:10px;background:var(--card);color:var(--text);min-width:0;width:100%;box-sizing:border-box}.xpw textarea{min-height:120px;resize:vertical}' +
  '.xpr{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px}.xpr .pb,.xpr a{width:auto;flex:1 1 auto;min-height:44px;padding:0 12px}' +
  '.xpr a{display:inline-flex;align-items:center;justify-content:center;border-radius:10px;background:var(--ac);color:var(--ac-on);text-decoration:none;font-weight:600}' +
  '.xpn{font-size:12px;color:var(--sub);font-variant-numeric:tabular-nums;margin-left:auto}.xpn.x{color:var(--red);font-weight:600}' +
  '.xpd{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}.xpd:first-of-type{border:0;margin:0;padding:0}' +
  '.xpd p{margin:0 0 6px;font-size:13px;color:var(--sub)}.xpd a.t{color:var(--text)}.xpg{opacity:.55;white-space:pre-wrap;font-size:14px;overflow-wrap:anywhere}' +
  '.xpi{background:var(--tile);border-radius:10px;padding:10px;margin-top:8px}.xpi b{display:block}.xpi span{display:block;font-size:14px;margin-top:4px;white-space:pre-wrap;overflow-wrap:anywhere}' +
  '.xpi input{margin-top:8px}.xpr a:not([href]){opacity:.5}' +
  '.xpi i{display:block;font-style:normal;font-size:13px;color:var(--sub);margin-top:6px}' +
  '.xpw details>summary{cursor:pointer;font-weight:600;padding:8px 0}.xpi.ok{opacity:.5}.xpi em{font-style:normal;font-size:12px;color:var(--sub)}';
const ED = {}, LOC = {}, RU = {};  // 고치는 중인 글 (다시 그려도 남게) · 누른 뒤 서버가 알려 줄 때까지 보여 줄 상태 (3분) · 링크 답글에 넣은 X 글 주소
let H = null, B = null, PLAN = null;  // PLAN: 수익 계정 2주 계획 (null 아직 · false 없음 — 열 때 한 번만 /dashboard/api/xplan)

const css = () => { if (D.getElementById('xp-css')) return; const s = D.createElement('style'); s.id = 'xp-css'; s.textContent = CSS; D.head.append(s); };
const nf = n => (n == null ? '–' : Number(n).toLocaleString('ko-KR'));
const row = (...k) => H.el('div', {class: 'xpr'}, ...k), note = (...k) => H.el('p', {class: 'note'}, ...k), NW = {target: '_blank', rel: 'noopener'};  // NW: 새 창 링크
const card = (t, ...k) => H.el('section', {class: 'itm'}, H.el('p', {class: 'h3', style: 'margin-top:0'}, t), ...k);
// X 글자 수 (twitter-text: 한글·이모지 2 · 라틴·숫자·기호 1 · 링크는 길이와 상관없이 url_len)
const ONE = [[0, 4351], [8192, 8205], [8208, 8223], [8242, 8247]];
function xlen(t, u) {
  let n = 0;
  t = String(t || '').replace(/https?:\/\/\S+/g, () => { n += u; return ''; });
  for (const c of t) { const o = c.codePointAt(0); n += ONE.some(([a, b]) => o >= a && o <= b) ? 1 : 2; }
  return n;
}
const loc = pid => { const l = LOC[pid]; return l && Date.now() - l.t < 180000 ? l : null; };
async function copy(t) {
  try { await navigator.clipboard.writeText(t); H.toast('복사했어요'); } catch (e) {
    const x = H.el('textarea', {style: 'position:fixed;opacity:0'}); x.value = t; D.body.append(x); x.select();
    try { D.execCommand('copy'); H.toast('복사했어요'); } catch (y) { H.toast('길게 눌러 복사해 주세요'); }
    x.remove();
  }
}
// 블로그 프로그램에 맡기는 일 (올렸어요·버리기·다시 쓰기) — 결과를 기다리지 않고 화면부터 바꾼다 (다음 새로고침이 진짜 상태)
async function send(a, b, state, msg, text) {
  if (!a || a.disabled) return H.toast('블로그 프로그램 업데이트를 기다리는 중이에요');
  b.disabled = true;
  const cid = 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(10)), x => (x % 36).toString(36)).join('');
  const r = await H.api('/dashboard/api/action', {method: 'POST', body: {action: a.action, args: a.args, cid, ctoken: null}});
  b.disabled = false;
  const d = r.data || {};
  if (r.status === 202 || (r.status === 200 && d.status === 'ok')) { LOC[a.args.pid] = {s: state, t: Date.now(), text}; H.toast(msg); draw(); return; }
  H.toast(d.text || '안 됐어요 — 잠시 뒤 다시 눌러 주세요');
}

function bar(label, n, goal, eta) {
  const {el} = H, p = n == null ? 0 : Math.min(100, Math.round(n / goal * 1000) / 10);
  return [el('div', {class: 'xpk'}, el('span', {}, label), el('b', {}, `${nf(n)} / ${nf(goal)}`)),
    el('div', {class: 'xpb', role: 'progressbar', 'aria-label': label, 'aria-valuenow': p, 'aria-valuemin': 0, 'aria-valuemax': 100}, el('i', {style: `width:${p}%`})),
    eta ? el('p', {class: 'note', style: 'margin:-6px 0 10px'}, `지금 속도면 ${eta === '달성' || eta === '늘지 않음' ? eta : eta + ' 달성'}`) : null];
}

function progress(x) {
  const {el, btn} = H, p = x.progress, f = {};
  const inp = (k, label, v) => el('label', {}, label, f[k] = el('input', {type: 'number', inputmode: 'numeric', min: 0, step: 1, placeholder: v == null ? '' : String(v)}));
  const save = e => {
    const v = {};
    for (const k of ['followers', 'verified', 'impressions_90d']) {
      const s = (f[k].value || f[k].placeholder || '').trim();
      if (!/^\d+$/.test(s)) return H.toast('숫자 셋을 모두 넣어 주세요');
      v[k] = Number(s);
    }
    if (v.verified > v.followers) return H.toast('인증 팔로워가 전체 팔로워보다 많아요');
    H.exec(x.save, v, {btn: e.currentTarget});
  };
  return card('수익 배분 진행률',
    bar('인증 팔로워', p.verified, p.verified_goal, p.eta_verified), bar('90일 인증 노출', p.impr, p.impr_goal, p.eta_impr),
    el('div', {class: 'xpk'}, el('span', {}, '전체 팔로워'), el('b', {}, nf(p.followers))),
    note(p.at ? `${p.at}에 넣은 숫자 · 크리에이터 스튜디오에서 보고 주마다 넣어 주세요` : '크리에이터 스튜디오 숫자를 주마다 넣어 주세요', p.note ? ' · ' + p.note : ''),
    el('div', {class: 'xpf'}, inp('followers', '전체 팔로워', p.followers), inp('verified', '인증 팔로워', p.verified), inp('impressions_90d', '90일 인증 노출', p.impr)),
    btn('pb', '저장', save, {style: 'margin-top:8px'}));
}

// 링크 답글 (주인 10/26 — 블로그 주소는 본문 말고 첫 답글): 복사 · 올린 X 글 주소를 넣으면 답글 쓰기 화면 (state.x.reply_intent)
const sid = u => (/^(?:https?:\/\/)?(?:www\.|mobile\.)?(?:x|twitter)\.com\/(?:i\/web|\w{1,15})\/status\/(\d{1,20})(?:[/?#]|$)/i.exec(String(u || '').trim()) || [])[1] || '';
function reply(x, d) {
  const {el, btn} = H, go = el('a', NW, '링크 답글 달기');
  const u = el('input', {type: 'url', value: RU[d.pid] || '', placeholder: '방금 올린 X 글 주소 붙여넣기', 'aria-label': `#${d.pid} X 글 주소`});
  const up = () => {
    const i = sid(RU[d.pid] = u.value);
    if (i) go.href = x.reply_intent + i + '&text=' + encodeURIComponent(d.reply); else go.removeAttribute('href');  // 주소 없으면 흐리게 (CSS)
  };
  u.oninput = up; up();
  go.onclick = e => { if (!go.href) { e.preventDefault(); H.toast('x.com/아이디/status/숫자 주소를 넣어 주세요'); } };
  return el('div', {class: 'xpi'}, el('em', {}, '첫 답글 (블로그 링크)'), el('span', {}, d.reply), u,
    row(btn('pb soft', '답글 복사', () => copy(d.reply)), go));
}

function draft(x, d) {
  const {el, btn} = H, l = loc(d.pid);
  if (l && l.s === 'redo' && l.text !== d.text) { delete LOC[d.pid]; delete ED[d.pid]; }
  const busy = loc(d.pid) && loc(d.pid).s === 'redo';
  const ta = el('textarea', {'aria-label': `#${d.pid} X 글`, rows: 6}), n = el('span', {class: 'xpn'}), go = el('a', NW, 'X에 올리기');
  ta.value = ED[d.pid] != null ? ED[d.pid] : d.text;
  const up = () => {
    const c = xlen(ta.value, x.url_len);
    n.textContent = `${c}/${x.limit}`; n.className = 'xpn' + (c > x.limit ? ' x' : '');
    go.href = x.intent + encodeURIComponent(ta.value.trim());
  };
  ta.oninput = () => { ED[d.pid] = ta.value; up(); };
  up();
  if (busy) ta.disabled = true;
  return el('div', {class: 'xpd'},
    el('p', {}, `#${d.pid} · `, d.url ? el('a', {class: 't', href: d.url, ...NW}, d.title || '블로그 글') : d.title,
      d.shop ? ' · 제휴 고지 줄은 지우지 마세요' : ''),
    ta, row(n),
    busy ? note('다시 쓰는 중이에요 — 1분쯤 뒤 새 초안이 떠요') : null,
    row(btn('pb soft', '복사', () => copy(ta.value.trim())), go,
      btn('pb', '올렸어요', e => send(d.posted_act, e.currentTarget, 'posted', '올렸어요로 표시했어요'))),
    d.reply ? reply(x, d) : null,
    row(btn('pb soft', '버리기', e => send(d.drop, e.currentTarget, 'dropped', '버렸어요')),
      btn('pb soft', '다시 쓰기', e => send(d.redo, e.currentTarget, 'redo', '다시 쓰는 중이에요 — 1분쯤 걸려요', d.text),
        {disabled: busy})));
}

// 수익 계정 2주 계획 (주인 10/26): 날마다 첫 줄 초안 — 복사 · X에 쓰기(글쓰기 화면만 열림) · 올렸어요 표시는 이 폰에만 (localStorage)
const KEY = p => 'xplan:' + p;
const done = k => { try { return localStorage.getItem(KEY(k)) === '1'; } catch (e) { return false; } };
const mark = (k, on) => { try { on ? localStorage.setItem(KEY(k), '1') : localStorage.removeItem(KEY(k)); } catch (e) { /* 저장 안 됨 */ } };
function plan(x) {
  const {el, btn} = H;
  if (!x.plan_n) return null;
  if (PLAN === null) {
    PLAN = false;
    H.api('/dashboard/api/xplan').then(r => { PLAN = r.status === 200 ? r.data : false; draw(); });
    return el('section', {class: 'itm'}, note('2주 계획을 불러오는 중이에요'));
  }
  if (!PLAN) return null;
  const today = new Date().toLocaleDateString('sv-SE', {timeZone: 'Asia/Seoul'});
  const post = (d, p) => {
    const k = d.date + ' ' + p.time, box = el('div', {class: 'xpi' + (done(k) ? ' ok' : '')});
    box.append(el('em', {}, p.time + (done(k) ? ' · 올렸어요' : '')), el('b', {}, p.head), el('span', {}, p.outline),
      p.attach ? el('i', {}, '첨부: ' + p.attach) : '',
      row(btn('pb soft', '복사', () => copy(p.head)),
        el('a', {href: x.intent + encodeURIComponent(p.head), ...NW}, 'X에 쓰기'),
        btn('pb soft', done(k) ? '올렸어요 취소' : '올렸어요', () => { mark(k, !done(k)); draw(); })));
    return box;
  };
  const days = PLAN.days.map((d, i) => el('details', {open: d.date === today || (today < PLAN.start && !i) || null},
    el('summary', {}, `${d.label} · ${d.posts.length}개${d.date === today ? ' · 오늘' : d.date < today ? ' · 지난 날' : ''}`),
    d.posts.map(p => post(d, p))));
  return card(`수익 계정 ${PLAN.title} (${x.plan_n}개)`,
    PLAN.rules.map(r => el('p', {class: 'note', style: 'margin:2px 0'}, r)), days,
    PLAN.replies.length ? el('details', {}, el('summary', {}, `비슷한 계정에 손으로 다는 댓글 틀 ${PLAN.replies.length}개`),
      note(PLAN.reply_rules),
      PLAN.replies.map(r => el('div', {class: 'xpi'}, el('em', {}, r.for), el('span', {}, r.text),
        row(btn('pb soft', '복사', () => copy(r.text)))))) : null);
}

function draw() {
  if (!H || !B) return;
  css();
  const {el, btn} = H, x = H.S.x;
  if (!x) { B.replaceChildren(note('X 화면을 불러오지 못했어요 — 새로고침해 주세요')); return; }
  const live = x.drafts.filter(d => { const l = loc(d.pid); return !l || l.s === 'redo'; });
  const done = x.posted.concat(x.drafts.filter(d => (loc(d.pid) || {}).s === 'posted'));
  const hd = x.handles || {}, names = [hd.pick ? `데런픽 @${hd.pick}` : '', hd.revenue ? `수익 @${hd.revenue}` : ''].filter(Boolean);
  const hp = el('input', {value: hd.pick || '', placeholder: '데런픽 계정 (@ 없이)', 'aria-label': '데런픽 계정'});
  const hr = el('input', {value: hd.revenue || '', placeholder: '수익 계정 (@ 없이)', 'aria-label': '수익 계정'});
  const st = (H.S.todo || []).some(i => i.iid === 'x-stats');  // 할 일 'X 숫자 넣기'가 있으면 숫자 칸이 맨 위
  B.replaceChildren(el('div', {class: 'xpw'},
    el('p', {class: 'note', style: 'margin:0'}, x.rules, names.length ? ' · ' + names.join(' · ') : ''), st ? progress(x) : null,
    card(`데런픽 초안함 ${live.length ? live.length + '개' : ''}`,
      note('고쳐서 복사하거나 ‘X에 올리기’로 X 앱을 연 뒤, 올렸으면 ‘올렸어요’를 눌러 주세요'),
      x.posted_line ? note(x.posted_line) : null,
      live.length ? live.map(d => draft(x, d)) : note(x.empty),
      done.length ? [el('p', {class: 'h3'}, '올린 글'), done.map(d => el('div', {class: 'xpd'}, el('p', {}, `#${d.pid} · ${d.title}`), el('div', {class: 'xpg'}, d.text),
        d.reply ? el('details', {}, el('summary', {}, '링크 답글'), reply(x, d)) : null))] : null),
    st ? null : progress(x), plan(x),
    card('수익 계정 글감 카드', note(x.idea_note),
      x.ideas.length ? x.ideas.map(c => el('div', {class: 'xpi'}, el('b', {}, c.title), c.facts.map(f => el('span', {}, f)), el('i', {}, c.prompt)))
        : note('이번 주 카드가 아직 없어요')),
    el('details', {class: 'itm'}, el('summary', {}, '계정 이름 넣기'), el('div', {class: 'xpf'}, hp, hr),
      btn('pb soft', '저장', e => H.exec(x.save_handles, {pick: hp.value.trim(), revenue: hr.value.trim()}, {btn: e.currentTarget}), {style: 'margin-top:8px'}))));
}

W.SBXP = {view(b, h) { B = b; H = h; draw(); }, xlen, sid};
})();
