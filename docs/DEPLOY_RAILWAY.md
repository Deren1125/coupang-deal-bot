# Railway 배포

Railway 는 GitHub 저장소를 연결하면 Dockerfile 로 자동 빌드·배포해 주는 PaaS 입니다. 가장 손쉽지만 **무료 티어가 아닙니다** — 가입 시 1회성 크레딧($5 상당 트라이얼)만 제공되고 이후는 Hobby 플랜(월 $5, 사용량 포함)이 필요합니다. 이 봇은 리소스를 거의 안 쓰므로 월 $5 안에서 충분히 돌아가지만, 완전 무료를 원하면 [오라클 클라우드](DEPLOY_ORACLE.md) 를 쓰세요.

저장소에 `railway.json` (Dockerfile 빌더, 재시작 정책 ALWAYS) 이 포함되어 있습니다.

## 아이패드/휴대폰만 있어도 됩니다

Railway 는 브라우저만으로 배포·설정·로그 확인이 다 되므로 컴퓨터가 없어도 운영할 수 있습니다. 터미널이 필요한 단계는 없습니다.
- 코드: GitHub 저장소를 브라우저에서 연결 (수정은 GitHub 웹 편집기나 Claude Code 로)
- 설정: Railway Variables 화면에 키 입력
- 확인: 봇이 켜지면 텔레그램 관리자 챗으로 자기 점검 결과가 오고, `/status` `/test` 로 확인

## 절차

1. https://railway.app 가입 (GitHub 로그인 권장).
2. **New Project → Deploy from GitHub repo** → `deren1125/coupang-deal-bot` 선택 → 브랜치 선택.
3. 서비스가 만들어지면 **Variables** 탭에서 아래를 추가:
   ```
   COUPANG_ACCESS_KEY=...
   COUPANG_SECRET_KEY=...
   COUPANG_SUB_ID=          (선택)
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHANNEL_ID=...
   TELEGRAM_ADMIN_CHAT_ID=...
   TZ=Asia/Seoul
   DEALBOT_DATA_DIR=/data
   ```
4. **Volume 추가 (중요)**: 서비스 → Settings → Volumes → **Add Volume** → Mount path `/data`.
   볼륨이 없으면 재배포 때마다 SQLite 가격 이력이 사라집니다.
5. Deploy. 로그 탭에서 `DealBot started` 가 보이면 정상. 관리자 챗에 시작 알림이 옵니다.
6. 설정(`config.yaml`)이나 템플릿을 바꾸면 git push → 자동 재배포.

## 확인

- Railway 로그: `Deployments → View logs`
- 텔레그램: `/status`

## 참고

- Railway 는 아웃바운드 IP 가 고정되지 않습니다. 쿠팡 API 는 IP 제한이 없지만, 뽐뿌가 특정 IP 대역을 차단하면 수집이 실패할 수 있습니다(관리자 챗에 에러 알림).
- Hobby 플랜에서 sleep 정책이 걸리지 않도록 서비스 설정의 "App Sleeping" 이 꺼져 있는지 확인하세요.
