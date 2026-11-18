/* 사진으로 X 글 쓰기 (주인 11/17 '사진(캡처·참고 사진)을 올리면 그걸 보고 X 글을 써 줘 · 작성자를 알아볼 수 있는 곳은 모자이크'). 셸이 defer 로 받음 — xphoto.py.
   '직접 쓰기' 화면(#/sns/xr/w)의 '내 주제로 글 쓰기' 아래에 칸 하나 (xtrend.js 가 W.SBXPhoto.form() 을 그려 넣음 · X 칸 '직접 쓰기' 카드의 [사진으로 글 쓰기] 로 바로 옴):
   [사진 넣기 (1~4장)] 단추 → 고른 사진이 작은 그림으로 (주인 11/18 '흰 격자 그림이 뭔지 모르겠음' — 빈 그림 칸 대신 단추와 실제 사진, 못 그리면 파일 이름) → (선택) 한 줄 메모 · 얼굴도 가리기 → [사진으로 초안 만들기]. 말투·길이는 위 칸에서 고른 것을 같이 씀.
   사진은 기존 업로드 길(POST /dashboard/api/upload)로 올리고, 서버가 사진을 보고 초안 + 가릴 자리를 정해 모자이크본만 남김 (원본은 지움).
   초안·사진·실패 줄은 xtrend.js 의 '내 초안' 목록에 그대로 뜬다 — [복사]·[사진 저장]·[✓ 올렸어요]. 올리기는 내가 X 앱에서.
   이 파일에 바깥 주소·innerHTML 없음. 고른 사진 미리 보기는 이 기기 안 blob 주소. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/xphoto/';
const TYPES = ['image/jpeg', 'image/png', 'image/webp'], MAX = 20 * 1024 * 1024;
let PS = null, at = 0, loading = false, pics = [], memo = '', faces = false, busy = '', err = null, tt = null;

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) {
    const v = a[n];
    if (v == null || v === false) continue;
    if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v; else if (n === 'value') e.value = v;
    else if (n.startsWith('on')) e.addEventListener(n.slice(2), v); else e.setAttribute(n, v === true ? '' : v);
  }
  for (const c of kids.flat(9)) if (c != null && c !== false) e.append(c.nodeType ? c : String(c));
  return e;
}
const btn = (cls, label, f, x) => el('button', Object.assign({class: cls, type: 'button', onclick: f}, x), label);
function toast(text) {
  const t = D.getElementById('toast');
  if (!t) return;
  t.replaceChildren(el('span', {text})); t.hidden = false;
  clearTimeout(tt); tt = setTimeout(() => { t.hidden = true; }, 3500);
}
function csrf() { try { return JSON.parse(D.getElementById('boot').textContent).me.csrf || ''; } catch (e) { return ''; } }
const T = () => W.SBXTrend;
const redraw = () => { if (T()) T().redraw(); };
async function req(path, opt) {
  try {
    const r = await fetch(path, Object.assign({credentials: 'same-origin', cache: 'no-store'}, opt));
    let d = {};
    try { d = await r.json(); } catch (e) { /* 빈 몸 */ }
    if (r.status === 403 && d.code === 'csrf') d.text = '화면이 오래됐어요 — 새로고침한 뒤 다시 눌러 주세요';
    if (r.status === 401) d.text = '로그인이 풀렸어요 — 새로고침해 주세요';
    return {s: r.status, d};
  } catch (e) { return {s: 0, d: {text: '연결이 끊겼어요 — 다시 눌러 주세요'}}; }
}
async function load() {
  if (loading) return;
  loading = true;
  const r = await req(API + 'state');
  loading = false; at = Date.now();
  if (r.s === 200 && r.d.v) { PS = r.d; redraw(); }
}
function drop(p) { try { URL.revokeObjectURL(p.url); } catch (e) { /* */ } }
function pick(ev) {
  const max = (PS && PS.lim.photos) || 4;
  for (const f of Array.from(ev.target.files || [])) {
    if (pics.length >= max) { toast(`사진은 ${max}장까지예요`); break; }
    if (!TYPES.includes(f.type)) { toast('JPG·PNG·WEBP 사진만 받아요'); continue; }
    if (f.size > MAX) { toast('사진이 너무 커요 (20MB 까지)'); continue; }
    pics.push({file: f, url: URL.createObjectURL(f), id: ''});
  }
  ev.target.value = ''; err = null;
  redraw();
}
async function make() {
  if (busy || !pics.length || !T()) return;
  const F = T().form();
  busy = 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8); err = null; redraw();
  let bad = '';
  for (const p of pics) {  // 한 장씩 기존 업로드 길로 (형식·크기 검사 · EXIF 지움은 서버가)
    if (p.id) continue;
    const r = await req('/dashboard/api/upload', {method: 'POST', headers: {'Content-Type': p.file.type, 'X-Upload-Kind': 'photo', 'X-Dash-Token': csrf()}, body: p.file});
    if (r.s === 200 && r.d.upload_id) p.id = r.d.upload_id; else { bad = r.d.text || '사진을 올리지 못했어요 — 다시 눌러 주세요'; break; }
  }
  let r = null;
  if (!bad) {
    r = await req(API + 'draft', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Dash-Token': csrf()},
      body: JSON.stringify({rid: busy, photos: pics.map(p => p.id), memo, tone: F.tone, length: F.length, faces, n: 3})});
    if (r.s !== 200) bad = r.d.text || '초안을 쓰지 못했어요 — 잠시 뒤 다시';
  }
  busy = '';
  if (r && r.d && r.d.v) T().take(r.d);
  if (bad) {
    err = {text: bad, code: (r && r.d.code) || ''};
    if (r && r.s === 404) for (const p of pics) p.id = '';  // 서버에 사진이 없음 — 다음에 다시 올림
    toast(bad); redraw();
    return;
  }
  pics.forEach(drop); pics = []; memo = ''; at = 0;
  toast('초안을 만들었어요 — 아래 「내 초안」에 사진과 같이 있어요');
  redraw(); load();
  const m = r.d.made && D.getElementById('xrm-' + r.d.made);
  if (m && m.scrollIntoView) m.scrollIntoView({block: 'start'});
}
function failBox(ST) {
  const f = err || (ST.fail && ST.fail.photo ? ST.fail : null);
  if (!f || busy) return null;
  const wait = f.wait_s ? ` (${Math.ceil(f.wait_s / 60)}분 남음)` : '';
  return el('section', {class: 'box', role: 'alert'}, el('p', {class: 'w'}, `✗ ${f.at ? f.at + ' · ' : ''}사진 초안을 못 썼어요 — ${f.text}${wait}`),
    el('div', {class: 'rw'}, pics.length ? btn('pb soft', '다시 해 보기', make, {disabled: f.code === 'ai_limit' && f.wait_s > 0 || null}) : null,
      btn('lnk', '닫기', () => { err = null; if (ST.fail && ST.fail.photo) ST.fail = null; redraw(); })));
}
function form() {  // xtrend.js drawScr 가 부른다 — 그릴 때마다 새 칸 (값은 이 파일 변수에)
  const ST = T() && T().state();
  if (!ST) return null;
  if (!PS || Date.now() - at > 120000) load();
  if (PS && !PS.on) return null;
  const max = (PS && PS.lim.photos) || 4, lim = (PS && PS.lim.memo) || 200, run = !!busy || !!ST.run || T().busy();
  const inp = el('input', {type: 'file', accept: TYPES.join(','), multiple: true, onchange: pick, 'aria-label': '사진 넣기', style: 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none'});
  const TH = 'width:88px;height:88px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:var(--tile);border:1px solid var(--line);font-size:12px;color:var(--sub);overflow:hidden;text-align:center;overflow-wrap:anywhere';
  return [el('p', {class: 'grp'}, '사진으로 글 쓰기'),
    el('section', {class: 'itm', id: 'xpf'},
      el('p', {class: 'note'}, `캡처나 사진을 올리면 그걸 보고 초안을 써요 (${max}장까지 · 말투·길이는 위에서 고른 대로). 닉네임·아이디·프로필은 모자이크해서 돌려드려요.`),
      busy || pics.length >= max ? null : el('label', {class: 'pb' + (pics.length ? ' soft' : ''), style: 'cursor:pointer;position:relative;margin:0 0 10px'},
        pics.length ? `사진 더 넣기 (${pics.length}/${max}장)` : `사진 넣기 (1~${max}장)`, inp),
      pics.length ? [el('p', {class: 'note'}, `넣은 사진 ${pics.length}장 — ✕ 를 누르면 빠져요`),
        el('div', {style: 'display:flex;flex-wrap:wrap;gap:12px;margin:0 0 12px'}, pics.map((p, i) => el('span', {style: 'position:relative;display:inline-block'},
          // 못 그리는 사진(기기가 못 여는 형식)은 빈 칸 대신 파일 이름을 보여 줌
          el('img', {src: p.url, alt: `넣은 사진 ${i + 1}`, style: TH + ';object-fit:cover', onerror: e => e.target.replaceWith(el('span', {style: TH}, p.file.name || `사진 ${i + 1}`))}),
          btn('sm', '✕', () => { drop(p); pics = pics.filter(x => x !== p); redraw(); }, {'aria-label': `사진 ${i + 1} 빼기`, disabled: !!busy || null,
            style: 'position:absolute;top:-10px;right:-10px;min-width:36px;min-height:36px;padding:0;border-radius:18px'}))))] : null,
      el('input', {class: 'fl', type: 'text', maxlength: lim, placeholder: '한 줄 메모 (선택 — 하고 싶은 말)', 'aria-label': '사진 메모', value: memo, oninput: e => { memo = e.target.value; }}),
      el('label', {class: 'ck'}, el('input', {type: 'checkbox', checked: faces, onchange: e => { faces = e.target.checked; }}), '얼굴도 가리기 (일반인 얼굴 — 연예인·공식 사진은 그대로)'),
      btn(pics.length ? 'pb' : 'pb soft', busy ? '사진 보고 쓰는 중… 1~2분 걸려요' : pics.length ? `사진 ${pics.length}장으로 초안 만들기` : '사진으로 초안 만들기', make, {disabled: run || !pics.length || ST.left <= 0 || (PS && PS.left <= 0) || null}),
      failBox(ST),
      el('p', {class: 'note'}, `오늘 사진 ${PS ? PS.left : '-'}장 남음 · 초안 횟수는 위와 같이 셈 · 원본은 초안이 나오면 바로 지우고 모자이크본만 남겨요${PS && PS.ocr ? '' : ' · 가릴 곳은 AI 가 찾아요 (놓칠 수 있으니 확인)'}`))];
}
W.SBXPhoto = {form, load, state: () => PS};
if (W.SBXTrend) W.SBXTrend.redraw();
})();
