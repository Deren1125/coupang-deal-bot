/* SNS 탭 (주인 10/26 — '스레드·인스타·트위터·룩북 릴스는 별개의 SNS 라 블로그 탭에 있으면 안 돼'). app.js 가 SNS 탭을 처음 열 때만 불러온다 (meta sb-sns).
   칸: 스레드·인스타 = threads.js(+thpost.js 세 칸) · X(트위터) = x.js 자동 반응 + xpost.js 내 글 · 룩북 릴스 = lookbook.js.
   각 화면 파일은 고치지 않는다: 담을 상자만 만들고, 옮기며 어색해진 머리 줄('블로그로'·'X →'·'스레드·인스타로')만 CSS 로 숨긴다.
   + 스레드 화면의 깨짐 두 가지 (app.css .tg 알약 테두리가 켜고 끄기 줄에 붙음 · '지금 한 번' 두 줄로 꺾임) 도 이 상자 안에서만 고친다. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.snb>.top:first-child .lnk{display:none}.snb>.top:first-child{min-height:0;margin:0 0 10px}'
  + '.snb>.top:first-child h2{font-size:14px;font-weight:600;color:var(--sub)}.snx>.top:first-child{display:none}.sng{margin-top:4px}'
  + '.snb label.tg{border-width:0 0 1px;border-radius:0;padding:0}.snb label.tg:last-child{border:0}.snb .tc>.pb{margin:0;padding:0 8px}';
const BOX = {};
let RF = 0, LAST = null;
function css() {
  if (D.getElementById('sn-css')) return;
  const s = D.createElement('style'); s.id = 'sn-css'; s.textContent = CSS; D.head.append(s);
  // 위 '새로고침' = 보고 있는 칸을 서버에서 다시 받기 (화면이 그대로여도)
  const rf = D.getElementById('rf');
  if (rf) rf.addEventListener('click', () => { RF++; if (LAST && /^#\/sns/.test(location.hash)) view(...LAST); });
}
// 서버에서 스스로 읽는 화면(threads·x)은 열 때 · 새로고침 · (스레드는) 글 칸 값이 바뀔 때만 다시 붙인다 —
// 20초 새로고침마다 다시 붙이면 X 승인함에서 고치던 댓글이 사라짐. S 로 그리는 화면(xpost·lookbook)은 매번 새 S 로 다시 그린다.
function box(h, id, L, key) {
  const o = BOX[id] || (BOX[id] = {b: h.el('div', {class: id}), k: null});
  if (key == null || o.k !== key) { o.k = key; h.mount(L, o.b); }
  return o.b;
}
const grp = (h, t, first) => h.el('p', {class: 'grp' + (first ? ' sng' : '')}, t);
function view(b, k, h) {
  css();
  LAST = [b, k, h];
  const L = h.L, at = h.NAV + '.' + RF;
  b.replaceChildren(...(k === 'x' ? [grp(h, '자동 반응 · 승인함', 1), box(h, 'snx', L.x[0], at), grp(h, '직접 올리는 글'), box(h, 'snp', L.x[1])]
    : k === 'lookbook' ? [box(h, 'snl', L.lookbook)] : [box(h, 'snb', L.threads, at + JSON.stringify((h.S || {}).thpost || 0))]));
}
W.SBSns = {view};
})();
