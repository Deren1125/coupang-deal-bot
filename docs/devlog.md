# 오늘 바뀐 것 (devlog)

딜봇 대시보드 › 할 일 페이지 맨 아래에 접힌 '오늘 바뀐 것 · N개' 칸이 있다. 눌러 열면 시각 · 프로그램(블로그/딜봇) · 한 줄 설명이 나온다.

## 커밋에 Owner-Note 달기

커밋 메시지 본문 끝(Co-Authored-By 줄 위)에 한 줄을 더한다.

    Owner-Note: 할 일 페이지에 '오늘 바뀐 것' 칸을 넣었어요

- 주인이 읽는 쉬운 한국어 한 줄 (80자 안). 파일 이름·영어 용어·메일 주소·해시는 넣지 않는다.
- 없으면 제목 앞말로 '대시보드 손봤어요' 처럼 자동으로 만든다. `docs:` / `Test:` / Merge 커밋은 Owner-Note 가 없으면 빠진다.
- 같은 문장은 한 줄로 묶인다.

## 어떻게 만들어지나

- Blog-Auto `blogauto/devlog.py` 가 서버의 두 git 사본(Blog-Auto 자기 폴더 · 딜봇 = `DEALBOT_ENV` 의 폴더)에서 최근 24시간(또는 오늘 0시부터 — 더 긴 쪽) 커밋을 읽어 기록 파일 `export_v2` 의 `devlog` 칸에 넣는다 (10분 캐시, git 5초 제한).
- 딜봇 `dashboard/state.py` 가 그 칸을 `state["devlog"]` 로 만들어 할 일 페이지가 그린다.
- 모양: `devlog = {day, items: [{at "HH:MM", repo "블로그"|"딜봇", text, sha, count?}], n}` — 새 것부터 최대 15. 계약 예시는 `tests/fixtures/dash_contract/export_v2.json` (두 저장소가 같은 파일).

## 딜봇 쪽

- `dashboard/blogauto.py` `_devlog` 가 기록 파일 칸을 걸러 읽고(시각 HH:MM · 프로그램 블로그|딜봇만), `state.py` `devlog()` 가 `{title: "오늘 바뀐 것 · N개", rows: [{at, repo, text}]}` 로 만든다 (비면 None).
- 화면은 app.js `devRow` — 할 일 페이지 맨 아래, 기존 '오늘 한 일' 과 같은 접는 줄(`foldBox`) + 목록 시트. 새 화면 파일 없음.

## 직전 AI 점검 칸 (주인 10/25)

- 같은 할 일 페이지 맨 아래, '오늘 바뀐 것' 밑에 접힌 '직전 AI 점검 · 시각 · 결과' 칸. 눌러 열면 첫 줄 결과(✅ 새 오류 없음 · 🔧 고침 · ⛔ 막힘 · 🙋 주인 할 일)와 본 것 한 줄씩.
- 매시간 오류 점검 AI 가 Blog-Auto 의 `ops-check` 가지에 `last.json` 하나만 덮어 올린다 (직전 차례만 남음 — `docs/ai_ops_playbook.md` 3장 8번).
- Blog-Auto `blogauto/opscheck.py` 가 10분마다 그 가지를 받아 기록 파일 `opscheck` 칸에 넣고, 딜봇 `dashboard/state.py` 가 `state["opscheck"]` 로 만든다.
- 모양: `opscheck = {at "MM/DD HH:MM", result ok|fixed|blocked|owner, summary, lines: [한 줄]}`. 가지가 없거나 git 이 안 되면 칸이 안 보인다.
