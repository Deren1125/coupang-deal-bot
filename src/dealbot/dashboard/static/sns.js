/* SNS 탭 (주인 10/26): 스레드·인스타 = threads.js · X = x.js + xpost.js · 룩북 릴스 = lookbook.js. 화면 파일은 그대로, 담을 상자만.
   X 가 쓰는 모양(.tq·.tb·.tf·.tg·.tl)은 threads.js 에만 있어 X 를 먼저 열어도 보이게 .snx 안에 둔다. */
'use strict';
(() => {
const D = document, W = window;
const CSS = '.snb>.top:first-child .lnk{display:none}.snb>.top:first-child{min-height:0;margin:0 0 10px}'
  + '.snb>.top:first-child h2{font-size:14px;font-weight:600;color:var(--sub)}.snx>.top:first-child{display:none}.sng{margin-top:4px}'
  + '.snb label.tg,.snx label.tg{border-width:0 0 1px;border-radius:0;padding:0}.snb label.tg:last-child,.snx label.tg:last-child{border:0}.snb .tc>.pb{margin:0;padding:0 8px}'
  + '.snx .tq{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:6px;margin:0 0 6px}.snx .tq div{background:var(--grey);border-radius:10px;padding:8px 10px}'
  + '.snx .tq b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.snx .tq span{font-size:12px;color:var(--sub)}'
  + '.snx .tf{display:grid;gap:8px}.snx .tf label{display:grid;gap:4px;font-size:13px;color:var(--sub)}'
  + '.snx .tf input{font:inherit;font-size:16px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--text);min-width:0}'
  + '.snx .tc{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:8px}'
  + '.snx .tg{display:flex;justify-content:space-between;align-items:center;min-height:44px;border-bottom:1px solid var(--line)}'
  + '.snx .tl p{margin:0;padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}.snx .tl p:last-child{border:0}.snx .tl i{display:block;font-style:normal;color:var(--sub)}'
  + '.snx .tb{flex:none;height:34px;padding:0 14px;border:0;border-radius:999px;font:inherit;font-size:14px;font-weight:600;background:var(--grey);color:var(--text)}.snx .tb:active{opacity:.6}'
  // 칸 이름이 두 줄로 꺾이지 않게: 할 일 숫자는 모서리 동그라미
  + '#snt button{position:relative;white-space:nowrap;font-size:14px}#snt .cp{position:absolute;top:-5px;right:-1px;margin:0;padding:0 6px;line-height:18px;font-size:11px}';
const BOX = {};
let RF = 0, LAST = null;
function css() {
  if (D.getElementById('sn-css')) return;
  const s = D.createElement('style'); s.id = 'sn-css'; s.textContent = CSS; D.head.append(s);
}
// threads·x 는 열 때·새로고침·(스레드) 글 칸 값이 바뀔 때만 다시 붙임 (고치던 X 댓글이 남게) · 못 불러왔으면 다음에 다시 · xpost·lookbook 은 매번
function box(h, id, L, key) {
  const o = BOX[id] || (BOX[id] = {b: h.el('div', {class: id}), k: null});
  if (key == null || o.k !== key) {
    o.k = key;
    Promise.resolve(h.mount(L, o.b)).then(ok => { if (ok === false && o.k === key) o.k = null; });
  }
  return o.b;
}
const grp = (h, t, first) => h.el('p', {class: 'grp' + (first ? ' sng' : '')}, t);
function view(b, k, h) {
  css();
  LAST = [b, k, h];
  const L = h.L, at = h.NAV + '.' + RF;
  if (k === 'x') {
    // 할 일 'X 숫자 넣기'(#/blog/x)로 왔으면 숫자 칸이 있는 '직접 올리는 글'이 위
    const x = [grp(h, '자동 반응 · 승인함', !h.j), box(h, 'snx', L.x[0], at)], p = [grp(h, '직접 올리는 글', h.j), box(h, 'snp', L.x[1])];
    b.replaceChildren(...(h.j ? p.concat(x) : x.concat(p)));
  } else b.replaceChildren(k === 'lookbook' ? box(h, 'snl', L.lookbook) : box(h, 'snb', L.threads, at + JSON.stringify((h.S || {}).thpost || 0)));
}
// 새로고침(단추·끌어서·돌아올 때, app.js refresh) = 보는 칸을 서버에서 다시
const again = () => { RF++; if (LAST) view(...LAST); };
W.SBSns = {view, again};
})();
