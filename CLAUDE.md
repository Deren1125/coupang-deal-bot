# coupang-deal-bot — AI 작업 지침

이 저장소에서 일하는 AI(오류 점검·수정 담당 포함)는 먼저 Blog-Auto 저장소의 `docs/ai_ops_playbook.md`
(github.com/deren1125/blog-auto) 를 읽는다. 두 프로그램은 같은 서버에서 돌고 주인 원칙·금지 사항을 함께 쓴다.

- 배포: `main` 에 push → 서버가 자동 업데이트.
- 테스트: `PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider`.
  원래부터 실패하는 3개(test_deploy_lightsail selftest 템플릿, test_toss_more 두 개) 외 실패가 있으면 push 하지 않는다.
- 메인·운영 AI는 `threads.js`·`threads_proxy.py`(스레드·X 봇 담당)와 `affiliate.py`·`shop_search.py`(에이블리 담당)를 고치지 않는다. Blog-Auto `threads/` 는 메인이 ai.py 문구만 (playbook 4장).
- 비밀(.env·토큰·쿠키)은 출력·커밋 금지. 고친 일은 `docs/devlog.md` 와 playbook '사건 기록'에 한 줄.

## 세션 역할과 소통 (주인 10/26)
- 일을 시작할 때마다 Blog-Auto 공유 보드를 먼저 읽는다: Blog-Auto 클론에서 `git fetch -q --no-write-fetch-head origin +ops-board:refs/remotes/origin/ops-board && git show origin/ops-board:board.md`. 제보 처리 순서·보드 쓰는 법은 `git show origin/main:CLAUDE.md` (Blog-Auto) '세션 역할과 소통'.
- 이 저장소 담당 (파일 단위, 표에 없는 파일 = 메인): 본체·대시보드 공용 = 메인 · static/threads.js = 스레드 · static/x.js·tests/test_dash_threads.py = X 봇 · dashboard/earn.py·static/earn.js·sources/creator_mall.py = 에이블리. 함께 씀(고치기 전 claim 줄): dashboard/threads_proxy.py(스레드·X 봇), dashboard/state.py·dashboard/blogauto.py·autolink.py(메인·에이블리·운영 AI). 오류 수정은 담당이 안 보일 때 운영 AI.
- 딜봇이 스스로 쉬기 시작하면(인스타 백오프·자동 링크 멈춤 등) kv 에 until 을 남긴다 — Blog-Auto status.md holds 칸이 읽는다.
