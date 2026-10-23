/* 화면 밝기 단추 (주인 10/24 "대시보드 안에 다크모드 단추 넣어줘") — 자동(기기 따라) → 밝게 → 어둡게.
   app.js 는 크기 한도(88KB)라 따로 둔 작은 파일. <head> 에서 defer 없이 먼저 돌아 첫 화면부터 고른 색이라 깜빡이지 않는다.
   고른 값은 이 기기의 localStorage 'sb-theme' (''·'light'·'dark'). app.css 는 '기기가 밝게 고르면' 어두운 색을 끄는 한 줄(:not([data-theme=light]))만. */
(function () {
  const D = document, R = D.documentElement, K = 'sb-theme';
  const BG = {light: '#f6f7f9', dark: '#111318'}, NEXT = {'': 'light', light: 'dark', dark: ''};
  const NAME = {'': '자동', light: '밝게', dark: '어둡게'};
  const get = () => { try { return localStorage.getItem(K) || ''; } catch (e) { return ''; } };
  const set = v => { try { v ? localStorage.setItem(K, v) : localStorage.removeItem(K); } catch (e) { /* 개인 창 */ } };
  // 어둡게의 색은 app.css 의 '기기가 어두울 때' 묶음을 그대로 옮겨 쓴다 (색을 두 곳에 적지 않게 — app.css 는 크기 한도가 꽉 참)
  function css() {
    if (D.getElementById('thm-css')) return true;
    let dark = '';
    try {
      for (const sh of D.styleSheets) for (const r of sh.cssRules || [])
        if (r.media && /prefers-color-scheme:\s*dark/.test(r.media.mediaText) && r.cssRules[0]) dark = r.cssRules[0].style.cssText;
    } catch (e) { /* 아직 못 읽음 */ }
    if (!dark) return false;
    const s = D.createElement('style'); s.id = 'thm-css';
    s.textContent = ':root[data-theme=dark]{color-scheme:dark;' + dark + '}:root[data-theme=light]{color-scheme:light}' +
      '.thm{border:1px solid var(--line);background:var(--card);color:var(--ac-ink);font:inherit;font-size:13px;font-weight:600;height:30px;' +
      'padding:0 12px;border-radius:999px;white-space:nowrap}';
    D.head.append(s);
    return true;
  }
  function apply(v) {
    if (v) R.setAttribute('data-theme', v); else R.removeAttribute('data-theme');
    for (const m of D.querySelectorAll('meta[name="theme-color"]')) {  // 휴대폰 맨 위 막대 색도 같이
      if (!m.dataset.auto) m.dataset.auto = m.content;
      m.content = v ? BG[v] : m.dataset.auto;
    }
    const b = D.getElementById('thm');
    if (b) { b.textContent = '◐ ' + NAME[v]; b.setAttribute('aria-label', '화면 밝기: ' + NAME[v] + ' (누르면 바꿔요)'); }
  }
  css();
  apply(get());
  addEventListener('load', css);
  D.addEventListener('DOMContentLoaded', () => {
    css();
    const rf = D.getElementById('rf');
    if (!rf || D.getElementById('thm')) return;
    const b = D.createElement('button');
    b.type = 'button'; b.id = 'thm'; b.className = 'thm';
    b.addEventListener('click', () => { const v = NEXT[get()] ?? ''; set(v); apply(v); });
    rf.before(b);
    apply(get());
  });
})();
