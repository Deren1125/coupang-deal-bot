/* 키워드 글쓰기 결과 줄 (주인 11/17 '인기 키워드·막 뜨는 중 눌렀는데 글이 안 써짐 — 왜 안 되는지 안 보임'). 셸이 defer 로 받음 — kwrun.py.
   블로그 탭 '인기 키워드/인기 검색어'(app.js trendBox 줄)와 할 일 탭 '⚡ 막 뜨는 중'(routine.js) 단추 옆에, 오늘 그 키워드로 누른 글쓰기의
   마지막 결과를 한 줄로: 보내는 중·쓰는 중 · ✓ 완료(보러 가기) · ✗ 못 쓴 까닭 (Blog-Auto 가 쓴 글 그대로 — AI 한도면 풀리는 시각).
   새로 고쳐도 남는다 (서버 표). 진행 중인 것이 있으면 5초마다 다시 봄 (30분까지). 이 파일은 읽기만 — 누르기·자동 클릭 없음. innerHTML 없음. */
'use strict';
(() => {
const D = document, W = window, API = '/dashboard/api/kwruns';
const CSS = '.kws{font-size:12px;line-height:1.45;margin:2px 0 6px 28px;color:var(--sub)}.kws.ok{color:var(--ac-ink)}.kws.fail{color:var(--red)}' +
  '.kws a{margin-left:6px;color:inherit;font-weight:600}.bn.rz+.kwl .kws{margin-left:0}';
let RUNS = [], at = 0, loading = false, poll = null, since = 0, raf = 0;

function el(t, a, ...kids) {
  const e = D.createElement(t);
  for (const n in a || {}) { const v = a[n]; if (v == null) continue; if (n === 'class') e.className = v; else if (n === 'text') e.textContent = v; else e.setAttribute(n, v); }
  for (const c of kids) if (c != null) e.append(c.nodeType ? c : String(c));
  return e;
}
async function load() {
  if (loading) return;
  loading = true;
  try {
    const r = await fetch(API, {credentials: 'same-origin', cache: 'no-store'});
    if (r.ok) { const d = await r.json(); if (d && Array.isArray(d.runs)) RUNS = d.runs; }
  } catch (e) { /* 다음에 */ }
  loading = false; at = Date.now();
  paint(); watch();
}
function watch() {
  clearTimeout(poll);
  if (RUNS.some(r => r.state === 'run') && Date.now() - since < 1800000) poll = setTimeout(load, 5000);
}
const ICON = {run: '⏳', ok: '✓', fail: '✗'};
function line(r, kw) {
  const n = el('p', {class: 'kws ' + r.state, role: r.state === 'fail' ? 'alert' : 'status', 'data-k': [r.action, r.blog, kw, r.state, r.text, r.go].join('|')},
    `${ICON[r.state]} ${r.at} 「${kw}」 ${r.text}`);
  if (r.go) n.append(el('a', {href: r.go}, '보러 가기 ›'));
  return n;
}
const find = (action, blog, kw) => RUNS.find(r => r.action === action && r.kw === kw && (action === 'blog.issue' || r.blog === blog));
function put(after, node) {  // after 바로 뒤에 (같은 내용이면 그대로 — MutationObserver 가 다시 불러도 바뀌지 않게)
  const nx = after.nextElementSibling;
  if (nx && nx.classList && nx.classList.contains('kws')) {
    if (!node) nx.remove(); else if (nx.getAttribute('data-k') !== node.getAttribute('data-k')) nx.replaceWith(node);
  } else if (node) after.after(node);
}
function paint() {
  raf = 0;
  const m = D.getElementById('main');
  if (!m) return;
  if (!D.getElementById('kws-css')) D.head.append(el('style', {id: 'kws-css', text: CSS}));
  const hm = /^#\/?blog\/([a-z]+)/.exec(location.hash), blog = hm ? hm[1] : '';
  for (const row of m.querySelectorAll('.row.tr')) {  // 블로그 탭 인기 키워드 줄 (Pick = blog.issue · 드라마·라커룸 = blog.channel_topic)
    const t = row.querySelector('.tw .t'), kw = t && t.textContent.trim();
    if (!kw || !blog) continue;
    const r = find(blog === 'issue' ? 'blog.issue' : 'blog.channel_topic', blog, kw);
    put(row, r ? line(r, kw) : null);
  }
  for (const box of m.querySelectorAll('.bn.rz')) {  // 할 일 탭 ⚡ 막 뜨는 중 (Pick 글 쓰기 = blog.issue)
    const kws = [...box.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean);
    const rs = kws.map(k => [k, find('blog.issue', 'issue', k)]).filter(x => x[1]);
    let wrap = box.nextElementSibling && box.nextElementSibling.classList.contains('kwl') ? box.nextElementSibling : null;
    const key = rs.map(([k, r]) => [k, r.state, r.text, r.at].join('|')).join('/');
    if (!rs.length) { if (wrap) wrap.remove(); continue; }
    if (wrap && wrap.getAttribute('data-k') === key) continue;
    const w = el('div', {class: 'kwl', 'data-k': key});
    for (const [k, r] of rs) w.append(line(r, k));
    if (wrap) wrap.replaceWith(w); else box.after(w);
  }
}
const soon = () => { if (!raf) raf = (W.requestAnimationFrame || setTimeout)(paint); };
function onTap(e) {  // 키워드 단추를 누르면 결과를 조금 뒤 다시 봄 (누르기는 app.js·routine.js 가 한다)
  const b = e.target && e.target.closest && e.target.closest('.row.tr .tw, .bn.rz button');
  if (!b) return;
  since = Date.now();
  setTimeout(load, 1500); setTimeout(load, 6000);
}
const here = () => /^#\/?(blog|todo)/.test(location.hash) || location.hash === '' || location.hash === '#/';
function start() {
  const m = D.getElementById('main');
  if (!m) return;
  new MutationObserver(soon).observe(m, {childList: true, subtree: true});
  D.addEventListener('click', onTap, true);
  W.addEventListener('hashchange', () => { if (here() && Date.now() - at > 30000) load(); else soon(); });
  D.addEventListener('visibilitychange', () => { if (D.visibilityState === 'visible' && here()) load(); });
  since = Date.now();
  if (here()) load();
}
W.SBKwRun = {load, paint, runs: () => RUNS};
if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', start); else start();
})();
