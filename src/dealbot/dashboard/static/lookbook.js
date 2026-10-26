/* 룩북 릴스 화면 (주인 10/25 — 실제 지그재그 상품 → 얼굴 없는 AI 모델 영상 → 주인이 새 인스타 계정에 유행 음악을 붙여 올림).
   SNS 탭 '룩북 릴스' 칸을 처음 열 때만 app.js 가 불러온다 (meta sb-lookbook). 값은 state.lookbook (lbview.py).
   편마다: 주제·훅 · 쉬운 말 상태 · 영상 미리보기(서버가 Range 206 — 아이폰도 재생) · 표지 · 영상·표지 저장 · 캡션·링크 복사 · 룩 목록 ·
   올리기 전 확인 · 올린 릴스 주소 → 올렸어요 · 버리기 · 다시 만들기. 인스타그램을 열거나 자동으로 올리지 않음. 라이브러리 없음.
   사진 시험(10/26): 목록은 열 때만 /dashboard/api/lbtrial. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.lbw{display:grid;gap:10px}.lbw .itm{padding:14px}.lbh{white-space:pre-line;font-weight:700;font-size:17px;margin:2px 0 6px}' +
  '.lbs{display:inline-block;font-size:12px;font-weight:600;border-radius:999px;padding:3px 9px;background:var(--grey);color:var(--text)}' +
  '.lbs.ok{background:var(--gr);color:#fff}.lbs.bad{background:var(--red);color:#fff}.lbs.done{opacity:.7}' +
  '.lbm{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:8px;margin:10px 0}.lbm video,.lbm img{width:100%;border-radius:10px;' +
  'background:#000;aspect-ratio:9/16;object-fit:cover}.lbr{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.lbr .pb{width:auto;flex:1 1 auto;' +
  'min-height:44px;padding:0 12px;margin:0}.lbl{display:flex;gap:8px;align-items:center;padding:8px 0;border-top:1px solid var(--line);font-size:14px}' +
  '.lbl img{width:44px;height:44px;border-radius:8px;object-fit:cover;flex:none}.lbl span{flex:1;min-width:0;overflow-wrap:anywhere}' +
  '.lbl a{flex:none;color:var(--ac);font-weight:600;text-decoration:none}.lbl.x{opacity:.45}.lbc{display:flex;gap:8px;align-items:center;' +
  'min-height:40px;font-size:15px}.lbc input{width:22px;height:22px;flex:none}.lbw input[type=url]{font:inherit;font-size:16px;padding:9px;' +
  'border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);width:100%;box-sizing:border-box;margin-top:8px}' +
  '.lbp{white-space:pre-wrap;font-size:13px;color:var(--sub);background:var(--tile);border-radius:10px;padding:10px;margin-top:8px;' +
  'max-height:9em;overflow:auto;overflow-wrap:anywhere}.lbw details summary{min-height:40px;display:flex;align-items:center}' +
  '.lbtr{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;align-items:start;margin-top:6px}.lbtr b{font-size:13px}' +
  '.lbtr small{display:block;font-weight:400;color:var(--sub)}.lbtc img,.lbte{display:block;width:100%;aspect-ratio:9/16;object-fit:contain;' +
  'border-radius:8px;background:var(--tile)}.lbte{display:flex;align-items:center;justify-content:center;font-size:12px;color:var(--sub)}' +
  '.lbtc span{display:block;font-size:13px;font-weight:600;text-align:center}.lbtn{margin:12px 0 0;font-size:13px}' +
  '.lbtr .pb{width:100%;min-height:40px;padding:0 4px;margin:0;font-size:13px;white-space:normal}';
const LOC = {}, TYPED = {}, CHK = {}, VID = {}, FILE = {}, SURE = {};  // 누른 뒤 상태(3분) · 쓰던 주소 · 확인 칸 · 재생 중인 영상 · 받은 파일 · 버리기 두 번
const REEL = /^https:\/\/(www\.|m\.)?instagram\.com\/([A-Za-z0-9_.]{1,30}\/)?(p|reels?|tv)\/[A-Za-z0-9_-]{5,40}\/?(\?\S*)?$/;
let H = null, B = null, TR = null, TK = '', TOPEN = false;  // TR: null · 0 받는 중 · false 실패 · 목록

const css = () => { if (D.getElementById('lb-css')) return; const s = D.createElement('style'); s.id = 'lb-css'; s.textContent = CSS; D.head.append(s); };
const loc = id => { const l = LOC[id]; return l && Date.now() - l.t < 180000 ? l : null; };
const ls = (k, v) => { try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || 'null'); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { return null; } };
async function copy(t, what) {
  try { await navigator.clipboard.writeText(t); H.toast(what + ' 복사했어요'); } catch (e) {
    const x = H.el('textarea', {style: 'position:fixed;opacity:0'}); x.value = t; D.body.append(x); x.select();
    try { D.execCommand('copy'); H.toast(what + ' 복사했어요'); } catch (y) { H.toast('길게 눌러 복사해 주세요'); }
    x.remove();
  }
}
// 블로그 프로그램에 맡기는 일 (올렸어요·버리기·다시 만들기) — 결과를 기다리지 않고 화면부터 바꾼다 (다음 새로고침이 진짜 상태)
async function send(a, b, state, msg, extra, done) {
  if (!a || a.disabled) return H.toast('블로그 프로그램 업데이트를 기다리는 중이에요');
  b.disabled = true;
  const cid = 'c_' + Array.from(crypto.getRandomValues(new Uint8Array(10)), x => (x % 36).toString(36)).join('');
  const r = await H.api('/dashboard/api/action', {method: 'POST', body: {action: a.action, args: Object.assign({}, a.args, extra), cid, ctoken: null}});
  b.disabled = false;
  const d = r.data || {};
  if (r.status === 202 || (r.status === 200 && d.status === 'ok')) { if (a.args.id) LOC[a.args.id] = {s: state, t: Date.now()}; if (done) done(); H.toast(msg); draw(); return; }
  H.toast(d.text || '안 됐어요 — 잠시 뒤 다시 눌러 주세요');
}
// 폰에 저장: 공유 시트('동영상 저장' → 사진첩)가 되면 그걸로, 아니면 파일로 받기. 큰 영상은 받은 뒤 한 번 더 눌러야 할 수 있음
function down(href, name) { const a = H.el('a', {href, download: name}); D.body.append(a); a.click(); a.remove(); }
async function save(href, name, type, b) {
  const can = f => navigator.canShare && navigator.canShare({files: [f]});
  const share = async f => { try { await navigator.share({files: [f]}); } catch (e) { if (e.name !== 'AbortError') H.toast('준비됐어요 — 한 번 더 눌러 주세요'); } };
  if (FILE[href]) return can(FILE[href]) ? share(FILE[href]) : down(href, name);
  if (!navigator.canShare || !W.File) return down(href, name);
  const t = b.textContent;
  b.disabled = true; b.textContent = '받는 중…';
  try {
    const r = await fetch(href, {credentials: 'same-origin'});
    if (!r.ok) throw new Error('http');
    FILE[href] = new File([await r.blob()], name, {type});
  } catch (e) { b.disabled = false; b.textContent = t; return H.toast('파일을 받지 못했어요 — 잠시 뒤 다시 눌러 주세요'); }
  b.disabled = false; b.textContent = t;
  return can(FILE[href]) ? share(FILE[href]) : down(href, name);
}

function video(it) {
  const k = it.id + it.video;
  if (!VID[k]) VID[k] = H.el('video', {src: it.video, controls: true, playsinline: true, preload: 'metadata', poster: it.cover || null, 'aria-label': `${it.theme} 영상`});
  return VID[k];
}

function looks(it) {
  const {el} = H;
  return it.looks.map(lk => el('div', {class: 'lbl' + (lk.dropped ? ' x' : '')},
    lk.thumb ? el('img', {src: lk.thumb, alt: '', loading: 'lazy'}) : null,
    el('span', {}, `Look ${lk.n} · ${lk.name}`, lk.price ? ` · ${lk.price}` : '', lk.dropped ? ' · 상품과 달라 뺌' : ''),
    lk.link && !lk.dropped ? el('a', {href: lk.link, target: '_blank', rel: 'noopener noreferrer'}, (lk.mall || '상품') + ' ↗') : null));
}

function checks(it, L) {
  const {el} = H, c = CHK[it.id] || (CHK[it.id] = ls('lbchk:' + it.id) || {});
  return [el('p', {class: 'h3'}, '올리기 전 확인'), L.checklist.map((t, i) => el('label', {class: 'lbc'},
    el('input', {type: 'checkbox', checked: !!c[i], onchange: e => { c[i] = e.target.checked; ls('lbchk:' + it.id, c); }}), t))];
}

function posted(it) {
  const {el, btn} = H;
  const inp = el('input', {type: 'url', inputmode: 'url', autocomplete: 'off', placeholder: '올린 릴스 주소 붙여넣기 (instagram.com/reel/…)',
    'aria-label': '올린 릴스 주소', value: TYPED[it.id] || '', oninput: e => { TYPED[it.id] = e.target.value; }});
  const go = e => {
    const u = (inp.value || '').trim();
    if (!REEL.test(u)) return H.toast('인스타 릴스 주소를 넣어 주세요 (instagram.com/reel/…)');
    send(it.posted_act, e.currentTarget, 'posted', '올렸어요로 표시했어요', {url: u});
  };
  return [el('p', {class: 'h3'}, '올린 뒤'), el('p', {class: 'note'}, '인스타 앱에서 릴스 → ⋯ → 링크 복사 → 여기 붙여넣기'), inp,
    el('div', {class: 'lbr'}, btn('pb', '올렸어요', go, {disabled: !it.posted_act || it.posted_act.disabled || null}))];
}

function card(it, L) {
  const {el, btn} = H, l = loc(it.id), busy = l && l.s === 'redo';
  const ready = it.status === 'ready';
  const links = it.looks.filter(lk => lk.link && !lk.dropped).map(lk => `Look ${lk.n} · ${lk.name} · ${lk.link}`).join('\n');  // 프로필 링크 칸에 넣을 것
  const drop = it.drop && btn('pb soft', SURE[it.id] ? '한 번 더 누르면 버려요' : '버리기', e => {
    if (!SURE[it.id]) { SURE[it.id] = 1; setTimeout(() => { delete SURE[it.id]; draw(); }, 3000); return draw(); }
    delete SURE[it.id];
    send(it.drop, e.currentTarget, 'dropped', '이번 릴스는 버렸어요');
  });
  const redo = it.redo && btn('pb soft', '다시 만들기', e => send(it.redo, e.currentTarget, 'redo', '다시 만들어 달라고 했어요 — 몇 분 걸려요'), {disabled: busy || null});
  return el('section', {class: 'itm'},
    el('div', {class: 'top'}, el('span', {class: 'lbs ' + it.tone}, busy ? '다시 만드는 중' : it.word), el('span', {class: 'note'}, [it.theme, it.created].filter(Boolean).join(' · '))),
    el('p', {class: 'lbh'}, it.hook || it.theme),
    it.note ? el('p', {class: 'note'}, it.note) : null,
    it.warn ? el('p', {class: 'note', style: 'color:var(--red)'}, it.warn) : null,
    it.video || it.cover ? el('div', {class: 'lbm'}, it.video ? video(it) : el('div', {}), it.cover ? el('img', {src: it.cover, alt: '표지', loading: 'lazy'}) : null) : null,
    it.video || it.cover ? el('div', {class: 'lbr'},
      it.video ? btn('pb', '영상 저장', e => save(it.video, it.save_name, 'video/mp4', e.currentTarget)) : null,
      it.cover ? btn('pb soft', '표지 저장', e => save(it.cover, it.cover_name, 'image/jpeg', e.currentTarget)) : null) : null,
    it.caption ? [el('div', {class: 'lbr'}, btn('pb soft', '캡션 복사', () => copy(it.caption, '캡션을')),
      links ? btn('pb soft', '링크 복사', () => copy(links, '룩 링크를')) : null),
    el('details', {}, el('summary', {}, '캡션 보기'), el('div', {class: 'lbp'}, it.caption))] : null,
    it.looks.length ? [el('p', {class: 'h3'}, `룩 ${it.looks.filter(x => !x.dropped).length}벌`), looks(it)] : null,
    ready ? [checks(it, L), posted(it)] : null,
    drop || redo ? el('div', {class: 'lbr'}, redo, drop) : null);
}

// 사진 시험 (주인 10/26): 룩마다 상품 사진 | 거울 셀카 | 예전 양식 · 양식 고르기
const SW = {mirror: '거울 셀카', studio: '예전 양식'};
function tload(L) {
  const k = L.trial_sig || (L.trial_n + '/' + L.trial_ready);  // 세트 번호·상태가 바뀌면 다시 받음
  if (!TOPEN || TR === 0 || (TR !== null && TK === k)) return;
  TR = 0; TK = k;
  H.api('/dashboard/api/lbtrial').then(r => { TR = r.status === 200 && Array.isArray(r.data) ? r.data : false; draw(); });
}
function tcell(src, score, alt) {
  const {el} = H;
  return el('div', {class: 'lbtc'}, src ? el('a', {href: src, target: '_blank', rel: 'noopener noreferrer'}, el('img', {src, alt, loading: 'lazy'})) : el('div', {class: 'lbte'}, '사진 없음'),
    score === undefined ? null : el('span', {}, score === null ? '채점 전' : `${score}점`));
}
function tgroup(g, L, cur) {
  const {el, btn} = H, S = {}, ns = [];
  g.sets.forEach(s => { S[s.style] = S[s.style] || s; s.looks.forEach(lk => { if (!ns.includes(lk.n)) ns.push(lk.n); }); });
  const look = (s, n) => s && s.looks.find(x => x.n === n);
  const head = st => el('b', {}, SW[st] + (cur === st ? ' · 지금' : ''), el('small', {}, S[st] ? S[st].word : '없음'));
  // 결과는 app.js exec 가 기다렸다가 알려 줌 (거절되면 그 까닭) · 바뀐 양식은 다음 상태 값으로 (검토 10/26: 접수만 보고 '됐어요' 하지 않게)
  const pick = st => btn('pb soft', cur === st ? '지금 양식' : '이 양식으로 정하기', e => run({action: 'blog.lookbook_style', args: {style: st}}, e.currentTarget, L),
    {disabled: cur === st || !S[st] || null});
  return [el('p', {class: 'note', style: 'margin:14px 0 0'}, [g.theme, g.created].filter(Boolean).join(' · ')),
    el('div', {class: 'lbtr'}, el('b', {}, '상품 사진'), head('mirror'), head('studio')),
    ns.sort((a, b) => a - b).map(n => {
      const m = look(S.mirror, n), t = look(S.studio, n), p = (m && m.product_thumb) || (t && t.product_thumb);
      return [el('p', {class: 'lbtn'}, `Look ${n} · ${(m || t).name}`), el('div', {class: 'lbtr'}, tcell(p, undefined, '상품'),
        tcell(m && m.thumb, m ? m.score : null, '거울 셀카'), tcell(t && t.thumb, t ? t.score : null, '예전 양식'))];
    }),
    el('div', {class: 'lbtr'}, el('span', {}), pick('mirror'), pick('studio'))];
}
const run = (a, b, L) => (L.trial_ok && H.exec ? H.exec(a, {}, {btn: b}) : H.toast('블로그 프로그램 업데이트를 기다리는 중이에요'));
function trial(L) {
  const {el, btn} = H, cur = L.style;
  tload(L);
  return el('section', {class: 'itm'},
    el('div', {class: 'top'}, el('p', {class: 'h3', style: 'margin:0'}, '사진 시험' + (L.trial_n ? ` · ${L.trial_n}세트` : '')),
      btn('pb soft', TOPEN ? '접기' : '열기', () => { TOPEN = !TOPEN; if (TOPEN) TR = null; draw(); }, {style: 'width:auto;margin:0'})),
    el('p', {class: 'note'}, `지금 양식: ${SW[cur] || '거울 셀카'} · 점수 = 사진 속 옷이 상품과 같은 정도 (10점 만점)`),
    TOPEN ? [TR === null || TR === 0 ? el('p', {class: 'note'}, '불러오는 중…') : TR === false ? el('p', {class: 'note'}, '불러오지 못했어요 — 다시 열어 주세요')
      : TR.length ? TR.map(g => tgroup(g, L, cur)) : el('p', {class: 'note'}, '아직 사진 시험이 없어요'),
    el('div', {class: 'lbr'}, btn('pb', L.trial_busy ? '사진 시험 만드는 중…' : '시험 새로 만들기', e => run({action: 'blog.lookbook_trial', args: {}}, e.currentTarget, L),
      {disabled: L.trial_busy || !L.trial_base || null})),
    el('p', {class: 'note'}, L.trial_base ? '최근 릴스 상품으로 두 양식 한 세트씩 · 사진값 약 1,000원 · 쇼핑몰 접속 없음 · 20분쯤 뒤 여기서 보여요'
      : '바탕이 될 릴스(4벌 이상)가 아직 없어요 — 아침에 첫 편이 만들어지면 눌러 주세요')] : null);
}

function draw() {
  if (!H || !B) return;
  css();
  const {el} = H, L = H.S.lookbook;
  if (!L) { B.replaceChildren(el('p', {class: 'note'}, '룩북 릴스 화면을 불러오지 못했어요 — 새로고침해 주세요')); return; }
  const live = L.items.filter(it => { const l = loc(it.id); return !l || l.s === 'redo'; });
  const done = L.posted.concat(L.items.filter(it => (loc(it.id) || {}).s === 'posted'));
  B.replaceChildren(el('div', {class: 'lbw'},
    el('p', {class: 'note', style: 'margin:0'}, L.rules, L.instagram ? ` · 계정 ${L.instagram}` : ''),
    L.server ? el('p', {class: 'note', style: 'margin:0;color:var(--red)'}, L.server) : null,
    L.ready ? trial(L) : null,
    live.length ? live.map(it => card(it, L)) : el('section', {class: 'itm'}, el('p', {class: 'note', style: 'margin:0'}, L.empty)),
    done.length ? el('section', {class: 'itm'}, el('p', {class: 'h3', style: 'margin-top:0'}, '올린 릴스'),
      done.map(it => el('div', {class: 'lbl'}, el('span', {}, `${it.theme} · ${it.created}`, it.ably ? ` · ${it.ably}` : ''),
        it.posted_url ? el('a', {href: it.posted_url, target: '_blank', rel: 'noopener noreferrer'}, '보기 ↗') : null))) : null));
}

W.SBLB = {view(b, h) { B = b; H = h; draw(); }};
})();
