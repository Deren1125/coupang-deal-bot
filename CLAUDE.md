# coupang-deal-bot — AI 작업 지침

이 저장소에서 일하는 AI(오류 점검·수정 담당 포함)는 먼저 Blog-Auto 저장소의 `docs/ai_ops_playbook.md`
(github.com/deren1125/blog-auto) 를 읽는다. 두 프로그램은 같은 서버에서 돌고 주인 원칙·금지 사항을 함께 쓴다.

- 배포: `main` 에 push → 서버가 자동 업데이트.
- 테스트: `PYTHONPATH=src .venv/bin/python -m pytest -q -p no:cacheprovider`.
  원래부터 실패하는 3개(test_deploy_lightsail selftest 템플릿, test_toss_more 두 개) 외 실패가 있으면 push 하지 않는다.
- `threads/`, `threads.js`, `threads_proxy.py`, `affiliate.py`, `shop_search.py` 는 건드리지 않는다.
- 비밀(.env·토큰·쿠키)은 출력·커밋 금지. 고친 일은 `docs/devlog.md` 와 playbook '사건 기록'에 한 줄.
