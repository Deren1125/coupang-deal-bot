"""주인 알림 한 곳 (주인 10/18): 대시보드 앱(Sns-board) 웹 푸시가 먼저, 카톡은 예비, 텔레그램 관리자 챗은 조용히.

- router.py  : 급한 일은 바로(묶기·한 시간 8번까지), 나머지는 아침·저녁 정리 — 같은 일은 한 번
- webpush.py · vapid.py : 표준 웹 푸시 (RFC 8291 aes128gcm + RFC 8292 VAPID) — 바깥 라이브러리 없이 cryptography + httpx
- digest.py  : 아침·저녁 정리 (날마다 다른 시각)
- ingest.py  : Blog-Auto 가 owner_notify/ 에 남긴 알림 받기
- store.py   : 표 (owner_notices · push_subs · owner_digests · threads_replies …)
- iid.py     : 알림 키 → 대시보드 항목 이름 (deal-12 · post-issue-31 …)
"""
