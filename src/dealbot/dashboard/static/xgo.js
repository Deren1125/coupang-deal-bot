/* X 도구 바로 가기 디딤돌 (xstudio.py GO_PATH /dashboard/x — 로그인 없이 열리는 빈 화면, 데이터 없음).
   # 뒤는 X 도구 주소(#/x/reply|write|stats|target)만 그대로 /dashboard/ 로 넘긴다.
   서버가 '직접 연 것'(Sec-Fetch-Site: none — 공유 시트·단축어·주소창)이라고 본 때만 스크립트 태그에 data-d 를 붙인다 → 그때만
   sessionStorage 에 표시를 남겨 xstudio.js 가 초안을 바로 만든다. 다른 사이트의 링크로 왔으면 표시가 없어 칸만 채워진다
   (세션 쿠키가 SameSite=Lax 라 남의 링크로도 로그인된 대시보드가 열린다 — 남이 초안·주소 읽기를 대신 누르게 하지 못하게). */
'use strict';
(() => {
const h = location.hash, ok = /^#\/x\/(reply|write|stats|target)(\?|$)/.test(h);
try {
  if (ok && document.querySelector('script[data-d]')) sessionStorage.setItem('xsgo', String(Date.now())); else sessionStorage.removeItem('xsgo');
} catch (e) { /* 저장소를 못 쓰면 표시 없이 — 칸만 채워짐 */ }
location.replace('/dashboard/' + (ok ? h : ''));
})();
