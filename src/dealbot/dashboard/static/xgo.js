/* X 도구 바로 가기 디딤돌 (xstudio.py GO_PATH /dashboard/x — 로그인 없이 열리는 빈 화면, 데이터 없음).
   x.com 책갈피·공유 시트처럼 다른 곳에서 대시보드를 열면 세션 쿠키(SameSite=Strict)가 첫 요청에 안 실려 로그인 화면이 뜬다 →
   여기서 같은 사이트 안 이동으로 한 번 더 넘기면 쿠키가 실린다. # 뒤는 X 도구 주소(#/x/reply|write|stats|target)만 그대로 넘긴다. */
'use strict';
(() => {
const h = location.hash;
location.replace('/dashboard/' + (/^#\/x\/(reply|write|stats|target)(\?|$)/.test(h) ? h : ''));
})();
